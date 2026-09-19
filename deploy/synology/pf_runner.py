"""One controlled process runner for the control release (PF-A1.2).

Every child process the control release starts (Git, Docker/Compose, SQL tools
through the Compose CLI, host diagnostics) goes through ``ProcessRunner.run``:

* the executable is a *registered* host tool (``bootstrap/tools.conf``), resolved
  through a trusted link chain and protected ancestors; PATH, ``PF_*`` and other
  inherited variables are never consulted;
* the child environment is built from an allowlist (``host_environment``); the
  process environment of the control release is not inherited;
* arguments are argv arrays (no shell strings), the working directory is explicit;
* output is captured with a byte bound and redacted before it can reach a log,
  a diagnostic or an exception message;
* every run has an explicit timeout; on timeout or interruption the whole process
  group is terminated and, for effects the caller declared, an *unresolved effect*
  record is persisted so a later operation can observe before retrying.

Python standard library only, Python 3.9 language baseline. Loaded as a sibling
module of the pinned control release (see pf-admin.py ``_load_sibling_module``).
"""
import dataclasses
import datetime as dt
import errno
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import uuid


def _load_sibling_module(name):
    path = Path(__file__).resolve().parent / (name + ".py")
    if name in sys.modules and getattr(sys.modules[name], "__file__", None) == str(path):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load control module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pf_bootstrap = _load_sibling_module("pf_bootstrap")
TRUSTED_UID = pf_bootstrap.TRUSTED_UID
TOOL_IDS = pf_bootstrap.TOOL_IDS

# Fixed trusted PATH for children (same list as the installed launcher). It exists for
# helpers a registered tool may start itself; the control release never resolves its own
# executables on it.
TRUSTED_PATH = (
    "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:"
    "/var/packages/ContainerManager/target/usr/bin:/var/packages/Docker/target/usr/bin:"
    "/var/packages/Git/target/bin"
)
# Variables the child environment may never receive from a caller-supplied extension.
RESERVED_ENV_PREFIXES = ("PATH", "HOME", "PF_", "PYTHON", "LD_", "DOCKER_", "COMPOSE_", "GIT_", "LANG", "LC_",
                         "TERM", "TMPDIR", "SSH_", "XDG_", "SUDO_")
DEFAULT_TIMEOUT = 600.0
DEFAULT_CAPTURE_LIMIT = 4 * 1024 * 1024
STREAM_LIMIT = 64 * 1024 * 1024
TERMINATE_GRACE_SECONDS = 3.0
REDACTED = "[REDACTED]"
MAX_LINK_HOPS = 16


class RunnerError(RuntimeError):
    """Refusal or failure of the runner boundary. The message is already redacted."""


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ------------------------------------------------------------------- redaction


class Redactor:
    """Replaces known secret values (and their URL-encoded form) with a marker."""

    def __init__(self, secrets=()):
        self._tokens = []
        for value in secrets:
            self.add(value)

    def add(self, value):
        if not isinstance(value, str) or len(value) < 4:
            # Values shorter than four characters cannot be redacted without destroying
            # the surrounding text; the configuration validators refuse such secrets.
            return
        for token in (value, urllib.parse.quote(value, safe="")):
            if token not in self._tokens:
                self._tokens.append(token)
        self._tokens.sort(key=len, reverse=True)

    @property
    def longest(self):
        return max((len(token) for token in self._tokens), default=0)

    def text(self, value):
        for token in self._tokens:
            value = value.replace(token, REDACTED)
        return value

    def data(self, value):
        for token in self._tokens:
            value = value.replace(token.encode("utf-8"), REDACTED.encode("utf-8"))
        return value


# ------------------------------------------------------ trusted executables


def _replacement_resistant(info):
    return pf_bootstrap.replacement_resistant_ancestor(info)


def resolve_trusted_executable(path):
    """Resolve a registered executable through a trusted link chain.

    Every directory component must be owned by the trusted uid and be replacement
    resistant; a symbolic-link component is followed only when the link itself is
    owned by the trusted uid (a system-managed layout such as DSM's ``/var/packages``
    is therefore accepted, an editor-owned or editor-replaceable link is not). The
    final entry must be a regular file owned by the trusted uid, not writable by
    others and executable. Returns the resolved absolute path. Never executes it.
    """
    text = str(path)
    reason = pf_bootstrap.canonical_path_error(text)
    if reason is not None:
        raise RunnerError(f"tool-path-noncanonical: {text!r}: {reason}")
    pending = list(Path(text).parts[1:])
    current = Path("/")
    hops = 0
    while pending:
        part = pending.pop(0)
        if part == ".":
            continue
        if part == "..":
            # Only link targets can introduce '..'; it steps back to a directory that was
            # already walked and validated. Escaping above the root is refused.
            if current == Path("/"):
                raise RunnerError(f"tool-link-chain: {text!r}: link target escapes the filesystem root")
            current = current.parent
            continue
        candidate = current / part
        try:
            info = os.lstat(str(candidate))
        except OSError as exc:
            raise RunnerError(f"tool-missing: {candidate}: {exc.strerror or exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            hops += 1
            if hops > MAX_LINK_HOPS:
                raise RunnerError(f"tool-link-chain: {candidate}: too many symbolic links")
            if info.st_uid != TRUSTED_UID:
                raise RunnerError(f"tool-untrusted-link: {candidate}: link owner uid {info.st_uid} is not trusted")
            target = os.readlink(str(candidate))
            if target.startswith("/"):
                current = Path("/")
                pending = list(Path(target).parts[1:]) + pending
            else:
                pending = list(Path(target).parts) + pending
            continue
        if stat.S_ISDIR(info.st_mode):
            if not _replacement_resistant(info):
                raise RunnerError(
                    f"tool-ancestor-replaceable: {candidate}: owner uid {info.st_uid} mode "
                    f"{oct(stat.S_IMODE(info.st_mode))}; an editor could replace entries"
                )
            current = candidate
            continue
        if pending:
            raise RunnerError(f"tool-path-component: {candidate}: not a directory")
        if not stat.S_ISREG(info.st_mode):
            raise RunnerError(f"tool-not-regular: {candidate}: not a regular file")
        if info.st_uid != TRUSTED_UID:
            raise RunnerError(f"tool-untrusted-owner: {candidate}: owner uid {info.st_uid}; trusted owner is uid {TRUSTED_UID}")
        if pf_bootstrap.writable_by_others(info.st_mode):
            raise RunnerError(f"tool-writable: {candidate}: mode {oct(stat.S_IMODE(info.st_mode))} is group/world writable")
        if not info.st_mode & stat.S_IXUSR:
            raise RunnerError(f"tool-not-executable: {candidate}")
        return str(candidate)
    raise RunnerError(f"tool-path-invalid: {text!r}")


# -------------------------------------------------------------- environment


def host_environment(*, home, docker_config, docker_host, git_config_global="/dev/null", term="dumb"):
    """The complete child environment. Nothing inherited; every key is explicit."""
    return {
        "PATH": TRUSTED_PATH,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": term,
        "DOCKER_CONFIG": str(docker_config),
        "DOCKER_HOST": str(docker_host),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(git_config_global),
    }


def extend_environment(base, extra, *, allowed_keys):
    """Add caller values (app configuration for Compose interpolation) to a host environment.

    ``extra`` may only contain keys from ``allowed_keys``; reserved host variables can
    never be overridden by application data.
    """
    result = dict(base)
    for key, value in extra.items():
        if key not in allowed_keys or key in base or any(key.startswith(prefix) for prefix in RESERVED_ENV_PREFIXES):
            raise RunnerError(f"env-key-refused: {key!r} is not an allowed child environment key")
        if not isinstance(value, str) or "\x00" in value:
            raise RunnerError(f"env-value-refused: {key!r} must be a string without NUL")
        result[key] = value
    return result


# ----------------------------------------------------------------- records


@dataclasses.dataclass(frozen=True)
class ProcessSpec:
    """Typed executable ID, argument vector, explicit cwd/env, limits and output policy."""

    tool: str
    argv: tuple
    cwd: str
    env: dict
    timeout: float = DEFAULT_TIMEOUT
    stdin: object = None            # None -> /dev/null; bytes -> written; str/Path -> file opened read-only;
                                    # an open file object is used as is
    stdout: object = None           # None -> capture; str/Path or open file object -> written there;
                                    # "stream" -> redacted live output
    capture_limit: int = DEFAULT_CAPTURE_LIMIT
    effect: object = None           # mapping describing an external effect to record when unresolved
    label: str = ""


@dataclasses.dataclass(frozen=True)
class ProcessResult:
    tool: str
    executable: str
    argv: tuple                     # redacted rendering
    returncode: object              # int, or None when the process was terminated by the runner
    stdout: str                     # redacted, bounded (empty when written to a file or streamed)
    stderr: str                     # redacted, bounded
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    interrupted: bool
    duration: float
    effect_id: object = None

    @property
    def ok(self):
        return self.returncode == 0 and not self.timed_out and not self.interrupted


class _Capture:
    """Bounded capture of one pipe; counts everything, keeps at most ``limit`` bytes."""

    def __init__(self, limit):
        self.limit = limit
        self.chunks = []
        self.kept = 0
        self.total = 0

    def feed(self, data):
        self.total += len(data)
        if self.kept < self.limit:
            room = self.limit - self.kept
            self.chunks.append(data[:room])
            self.kept += min(len(data), room)

    @property
    def truncated(self):
        return self.total > self.kept

    def data(self, hold=0):
        """Kept bytes; when truncated, the last ``hold`` bytes are dropped so that a secret cut
        by the limit cannot survive as an unredactable prefix."""
        data = b"".join(self.chunks)
        if self.truncated and hold:
            data = data[:-hold]
        return data


class _Stream:
    """Line-buffered redacted forwarding to a text stream (passthrough/logs)."""

    def __init__(self, redactor, sink, limit):
        self.redactor = redactor
        self.sink = sink
        self.limit = limit
        self.total = 0
        self.pending = b""
        self.hold = max(redactor.longest, 1)

    def feed(self, data):
        self.total += len(data)
        self.pending += data
        cut = self.pending.rfind(b"\n")
        if cut < 0:
            # No complete line yet. Redact the whole buffer first (every complete secret is
            # replaced), then keep a tail long enough to hold a secret whose end has not
            # arrived: such a partial can only be a suffix of the buffer shorter than the
            # longest token, so it lies entirely inside the retained tail.
            if len(self.pending) > 64 * 1024:
                redacted = self.redactor.data(self.pending)
                self._emit(redacted[:-self.hold])
                self.pending = redacted[-self.hold:]
            return
        self._emit(self.pending[:cut + 1])
        self.pending = self.pending[cut + 1:]

    def close(self):
        if self.pending:
            self._emit(self.pending)
            self.pending = b""

    def _emit(self, data):
        self.sink.write(self.redactor.data(data).decode("utf-8", "replace"))
        self.sink.flush()


# ------------------------------------------------------------------ runner


class ProcessRunner:
    """Runs registered host tools under one boundary. Construction performs no I/O."""

    def __init__(self, tools, *, home, docker_config, docker_host, redactor=None, effects_path=None,
                 stream_sink=None):
        unknown = sorted(set(tools) - set(TOOL_IDS))
        if unknown:
            raise RunnerError("tools-unknown: " + ", ".join(unknown))
        self.tools = {key: str(value) for key, value in tools.items()}
        self.home = Path(home)
        self.docker_config = Path(docker_config)
        self.docker_host = str(docker_host)
        self.redactor = redactor if redactor is not None else Redactor()
        self.effects_path = Path(effects_path) if effects_path is not None else None
        self.stream_sink = stream_sink
        self._resolved = {}
        self.history = []

    # -- executables -------------------------------------------------------

    def registered(self, tool):
        if tool not in TOOL_IDS:
            raise RunnerError(f"tool-unknown: {tool!r} is not a typed executable id")
        path = self.tools.get(tool)
        if path is None:
            raise RunnerError(f"tool-unregistered: {tool!r} is not registered in bootstrap/tools.conf; "
                              "the control release does not search PATH")
        return path

    def executable(self, tool):
        """Registered and validated executable for ``tool`` (validated once per process)."""
        if tool not in self._resolved:
            self._resolved[tool] = resolve_trusted_executable(self.registered(tool))
        return self._resolved[tool]

    def describe(self):
        """Read-only description of every registered tool and its trust status."""
        rows = []
        for tool in TOOL_IDS:
            path = self.tools.get(tool)
            if path is None:
                rows.append((tool, "unregistered", ""))
                continue
            try:
                rows.append((tool, "ok", resolve_trusted_executable(path)))
            except RunnerError as exc:
                rows.append((tool, "refused", str(exc)))
        return rows

    def environment(self, extra=None, *, allowed_keys=()):
        base = host_environment(home=self.home, docker_config=self.docker_config, docker_host=self.docker_host)
        if not extra:
            return base
        return extend_environment(base, extra, allowed_keys=allowed_keys)

    # -- execution ---------------------------------------------------------

    def run(self, spec):
        executable = self.executable(spec.tool)
        if not isinstance(spec.argv, (tuple, list)) or not all(isinstance(item, str) for item in spec.argv):
            raise RunnerError("argv-invalid: arguments must be an array of strings")
        if any("\x00" in item for item in spec.argv):
            raise RunnerError("argv-invalid: NUL in an argument")
        if not isinstance(spec.env, dict) or "PATH" not in spec.env:
            raise RunnerError("env-invalid: the child environment must be built by host_environment()")
        if spec.timeout is None or spec.timeout <= 0:
            raise RunnerError("timeout-invalid: every run needs an explicit positive timeout")
        cwd = str(spec.cwd)
        if not os.path.isdir(cwd):
            raise RunnerError(f"cwd-invalid: {cwd} is not a directory")
        rendered = tuple(self.redactor.text(item) for item in spec.argv)

        stdin_handle = None
        stdin_arg = subprocess.DEVNULL
        input_bytes = None
        stdout_handle = None
        stdout_arg = subprocess.PIPE
        try:
            if isinstance(spec.stdin, (bytes, bytearray)):
                stdin_arg = subprocess.PIPE
                input_bytes = bytes(spec.stdin)
            elif hasattr(spec.stdin, "fileno"):
                stdin_arg = spec.stdin          # caller-owned open file (a dump being restored)
            elif spec.stdin is not None:
                stdin_handle = open(str(spec.stdin), "rb")
                stdin_arg = stdin_handle
            if hasattr(spec.stdout, "fileno"):
                stdout_arg = spec.stdout        # caller-owned open file (a dump being written)
            elif isinstance(spec.stdout, (str, Path)) and spec.stdout != "stream":
                stdout_handle = open(str(spec.stdout), "wb")
                stdout_arg = stdout_handle
        except OSError as exc:
            self._close(stdin_handle, stdout_handle)
            raise RunnerError(f"io-failed: {spec.tool}: cannot open input/output file: {exc.strerror or exc}") from exc
        stream = None
        if spec.stdout == "stream":
            stream = _Stream(self.redactor, self.stream_sink or sys.stdout, STREAM_LIMIT)

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                [executable, *spec.argv], executable=executable, cwd=cwd, env=dict(spec.env),
                stdin=stdin_arg, stdout=stdout_arg, stderr=subprocess.PIPE,
                start_new_session=True, close_fds=True,
            )
        except OSError as exc:
            self._close(stdin_handle, stdout_handle)
            raise RunnerError(f"exec-failed: {spec.tool} ({executable}): {exc.strerror or exc}") from exc
        out = _Capture(spec.capture_limit)
        err = _Capture(spec.capture_limit)
        timed_out = False
        interrupted = False
        try:
            timed_out = self._pump(process, spec, out, err, stream, input_bytes, started)
        except BaseException:
            interrupted = True
            self._terminate(process)
            raise
        finally:
            self._close(stdin_handle, stdout_handle)
            if stream is not None:
                stream.close()
            duration = time.monotonic() - started
            effect_id = None
            if (timed_out or interrupted) and spec.effect is not None:
                effect_id = self._record_effect(spec, rendered, executable, process,
                                                "timeout" if timed_out else "interrupted")
            hold = self.redactor.longest
            result = ProcessResult(
                tool=spec.tool, executable=executable, argv=rendered,
                returncode=None if timed_out else process.returncode,
                stdout=self.redactor.data(out.data(hold)).decode("utf-8", "replace"),
                stderr=self.redactor.data(err.data(hold)).decode("utf-8", "replace"),
                stdout_truncated=out.truncated or (stream is not None and stream.total > STREAM_LIMIT),
                stderr_truncated=err.truncated, timed_out=timed_out, interrupted=interrupted,
                duration=duration, effect_id=effect_id,
            )
            self.history.append(result)
        return result

    def _pump(self, process, spec, out, err, stream, input_bytes, started):
        """Feed stdin and drain both pipes in one select loop with a byte bound and the deadline.

        Reads and the stdin write share the loop, so a child that fills its output pipe
        before consuming its input cannot deadlock the runner. Returns timed_out.
        """
        deadline = started + float(spec.timeout)
        selector = selectors.DefaultSelector()
        active = 0
        for pipe, sink in ((process.stdout, out), (process.stderr, err)):
            if pipe is not None:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, sink)
                active += 1
        pending_input = memoryview(input_bytes or b"")
        stdin_pipe = process.stdin
        if stdin_pipe is not None:
            os.set_blocking(stdin_pipe.fileno(), False)
            selector.register(stdin_pipe, selectors.EVENT_WRITE, "stdin")
            active += 1
        timed_out = False
        try:
            while active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                if stream is not None and stream.total > STREAM_LIMIT:
                    timed_out = True  # output bound reached: treated like a deadline
                    break
                for key, _ in selector.select(timeout=min(remaining, 1.0)):
                    if key.data == "stdin":
                        try:
                            written = os.write(key.fileobj.fileno(), pending_input[:65536])
                            pending_input = pending_input[written:]
                        except BlockingIOError:
                            continue
                        except (BrokenPipeError, OSError):
                            pending_input = memoryview(b"")
                        if not pending_input:
                            selector.unregister(key.fileobj)
                            active -= 1
                            key.fileobj.close()
                        continue
                    try:
                        data = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                        active -= 1
                        continue
                    if stream is not None and key.data is out:
                        stream.feed(data)
                    else:
                        key.data.feed(data)
            if timed_out:
                self._terminate(process)
                return True
            remaining = deadline - time.monotonic()
            try:
                process.wait(timeout=max(remaining, 0.0))
            except subprocess.TimeoutExpired:
                self._terminate(process)
                return True
            return False
        finally:
            selector.close()
            for pipe in (process.stdout, process.stderr, process.stdin):
                if pipe is not None and not pipe.closed:
                    try:
                        pipe.close()
                    except OSError:
                        pass

    def _terminate(self, process):
        """SIGTERM then SIGKILL the whole process group; wait for the leader."""
        if process.poll() is not None and not self._group_alive(process.pid):
            return
        for sig, grace in ((signal.SIGTERM, TERMINATE_GRACE_SECONDS), (signal.SIGKILL, TERMINATE_GRACE_SECONDS)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise
            end = time.monotonic() + grace
            while time.monotonic() < end:
                if process.poll() is not None and not self._group_alive(process.pid):
                    return
                time.sleep(0.05)
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _group_alive(pgid):
        """True while any non-zombie process still belongs to the group (``/proc`` scan on Linux)."""
        members = group_members(pgid)
        if members is not None:
            return bool(members)
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _close(*handles):
        for handle in handles:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass

    # -- unresolved effects ---------------------------------------------------

    def _redacted(self, value):
        """The effect descriptor with every string passed through the redactor (defence in depth:
        descriptors are built from verbs and identifiers, never from application values)."""
        if isinstance(value, str):
            return self.redactor.text(value)
        if isinstance(value, dict):
            return {str(key): self._redacted(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redacted(item) for item in value]
        return value

    def _record_effect(self, spec, rendered, executable, process, outcome):
        """Persist what was started and how it ended, before any later operation retries."""
        if self.effects_path is None:
            return None
        record = {
            "id": uuid.uuid4().hex,
            "recorded_at": utc_now(),
            "outcome": outcome,
            "tool": spec.tool,
            "executable": executable,
            "argv": list(rendered),
            "cwd": str(spec.cwd),
            "pid": process.pid,
            "process_group": process.pid,
            "returncode": process.returncode,
            "effect": self._redacted(spec.effect),
            "label": spec.label,
        }
        existing = load_unresolved_effects(self.effects_path)
        existing.append(record)
        data = json.dumps(existing, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        temporary = self.effects_path.with_name(self.effects_path.name + ".tmp-" + uuid.uuid4().hex[:8])
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self.effects_path))
        except BaseException:
            try:
                os.unlink(str(temporary))
            except OSError:
                pass
            raise
        directory = os.open(str(self.effects_path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return record["id"]


def group_members(pgid):
    """PIDs of live (non-zombie) processes in process group ``pgid``; None when /proc is unavailable."""
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    members = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                text = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        # "pid (comm) state ppid pgrp ..." — comm may contain spaces/parentheses.
        tail = text.rsplit(")", 1)[-1].split()
        if len(tail) < 3:
            continue
        state, _, pgrp = tail[0], tail[1], tail[2]
        if pgrp == str(pgid) and state != "Z":
            members.append(int(name))
    return members


def load_unresolved_effects(path):
    """Strictly parsed list of unresolved effect records; missing file -> empty list."""
    path = Path(path)
    try:
        data = pf_bootstrap.read_bytes_nofollow(path)
    except FileNotFoundError:
        return []
    value = pf_bootstrap.parse_strict_json(data, label=str(path), error=RunnerError)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RunnerError(f"{path}: unresolved effects must be a JSON array of objects")
    return value


def failure_detail(result, limit=5000):
    """Redacted, bounded stderr tail for an error message (secrets were removed at capture)."""
    detail = result.stderr[-limit:].strip()
    if result.stderr_truncated:
        detail = "[stderr truncated] " + detail
    if result.timed_out:
        detail = f"[timed out after {result.duration:.0f}s; process group terminated] " + detail
    return detail
