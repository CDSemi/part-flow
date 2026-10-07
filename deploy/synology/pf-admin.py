#!/usr/bin/env python3
"""Conservative lifecycle commands for the supplied PartFlow NAS staging stack.

Python standard library only. No application or database business rules live here.
Local configuration and this controller are never replaced by downloaded source.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import errno
import grp
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import sys
import tarfile
import tempfile
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import uuid


def _load_sibling_module(name):
    """Import a module from this file's own directory by absolute path.

    The launcher already selected this control release from the protected
    bootstrap; importing a sibling by explicit path keeps that choice and does
    not consult sys.path, PYTHONPATH or the working directory.
    """
    path = Path(__file__).resolve().parent / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load control module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pf_instance = _load_sibling_module("pf_instance")
pf_runner = _load_sibling_module("pf_runner")
pf_config = _load_sibling_module("pf_config")
pf_source = _load_sibling_module("pf_source")
pf_docker = _load_sibling_module("pf_docker")
# PF-A2.1: the installer (pf install ...) and the install gate/binding re-check of Controller.lock.
pf_install = _load_sibling_module("pf_install")
pf_bootstrap = pf_instance.pf_bootstrap
RUNNING_RELEASE = Path(__file__).resolve().parent

VERSION = "2.5.0"
CHECKPOINT = "PF-A2.2"
PAGE_SIZE = 10
# Explicit per-call limits for the controlled runner (PF-A1.2). A5 tunes budgets; the
# security floor (every child has a deadline and a bounded, redacted capture) is here.
TIMEOUT_DIAGNOSTIC = 120.0
TIMEOUT_COMPOSE = 900.0
TIMEOUT_BUILD = 3600.0
TIMEOUT_DATA = 3600.0
TIMEOUT_GIT_FETCH = 1800.0
# PF-A1.4: `pf logs` bounds. `-f` ends after this deadline or the runner's 64 MiB stream cap.
TIMEOUT_LOGS_FOLLOW = 3600.0
LOGS_DEFAULT_TAIL = 200
LOGS_MAX_TAIL = 10000
# PF-A1.3: the daemon identity probe (`docker info`), run once per process before any other Docker child.
TIMEOUT_DAEMON_PROBE = 30.0
# Container listings taken when a container vanishes between `ps -a` and inspect (inventory churn).
INVENTORY_ATTEMPTS = 3
# Per-call Compose value overrides: only the temporary databases of the update rehearsal and of
# reset-db (fullmatch). Any other key or value is refused before rendering (OD-A13-13).
COMPOSE_VALUE_OVERRIDES = {"POSTGRES_DB": r"pf_(migrate|clean)_[0-9a-f]{20}"}
GITHUB_HTTPS = "https://github.com/"
# The editable admin-config keys and defaults; owned by pf_config since PF-A2.1 (validate_admin_config).
DEFAULTS = pf_config.ADMIN_CONFIG_DEFAULTS
# Runtime control/configuration lives outside the writable repository in v2.5. These names are
# ignored as *untracked* workspace artifacts only; a verified commit that tracks one of them is
# refused by the store export, so nothing deployed is ever outside the manifest (A12-R02).
SOURCE_EXCLUDES = pf_source.DEFAULT_EXCLUDES
AUTO_REVIEW_PATHS = (
    ".env.example", "compose.yaml", "backend/Dockerfile", "frontend/Dockerfile",
    "backend/.dockerignore", "frontend/.dockerignore", ".github/workflows/ci.yml",
)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BACKUP_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}-[0-9a-f]{6}\Z")
RECOVERY_RE = re.compile(r"purge-\d{8}T\d{6}Z-[0-9a-f]{12}-[0-9a-f]{6}\Z")
# The protected state files create_purge_recovery() copies into a bundle; the only names a bundle's
# state_files may list for a restore into protected state (PF-A1.4).
RESTORABLE_STATE_FILES = ("deployed.json", "last-reset.json", "observed-tags.json")
# Compose project names (pf-config.json grammar); --project is validated by it before any selection.
PROJECT_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")
# `pf logs --since/--until`: a relative duration or an RFC 3339 date/time; nothing else reaches Compose.
SINCE_RE = re.compile(r"(?:\d{1,6}[smh]|\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:\d{2})?)?)\Z")
PS_STATUSES = ("paused", "restarting", "removing", "running", "dead", "created", "exited")
OPERATION_ID_RE = re.compile(r"\d{8}T\d{6}Z-[a-z0-9-]+-[0-9a-f]{8}\Z")
IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*:[a-zA-Z0-9_.-]+\Z")
REQUIRED_NAS_ENV_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "SITE_TIMEZONE",
    "PARTFLOW_BIND_IP",
    "PARTFLOW_HTTP_PORT",
    "PARTFLOW_ALLOWED_HOST",
)
# The A1 SITE_TIMEZONE grammar; one expression, owned by pf_config since PF-A2.2 (host zone data checks).
TIMEZONE_RE = pf_config.TIMEZONE_RE
HOST_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")



class Failure(RuntimeError):
    pass


class Deferred(Failure):
    """A scheduled update needs human review; exit 20, never silently succeed."""


class PlanChanged(Failure):
    """The binding inventory differs from the preliminary plan (PF-A1.3). Raised inside the bundle
    creation after the checkpoint exists, so purge reopens that exact application before re-raising.
    ``checkpoint`` is None when reopening is unsafe (a new blocker Compose could adopt or recreate)."""

    def __init__(self, message, checkpoint):
        super().__init__(message)
        self.checkpoint = checkpoint


class DaemonFailure(Failure):
    """The bound Docker daemon is unreachable, invalid, rootless or drifted (PF-A1.3).

    Cached for the life of the process: every later Docker/Compose child raises it again
    without starting a process.
    """

    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def log(message):
    print(message, flush=True)


def utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def real_directory(path):
    """True only for an existing directory that is not a symbolic link (lstat; nothing is followed)."""
    try:
        return stat.S_ISDIR(os.lstat(str(path)).st_mode)
    except OSError:
        return False


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def migration_files(root):
    base = Path(root) / "backend"
    files = [base / "alembic.ini"]
    folder = base / "alembic"
    if not folder.is_dir() or not files[0].is_file():
        raise Failure("Missing backend/alembic or backend/alembic.ini.")
    files.extend(p for p in folder.rglob("*") if p.is_file()
                 and "__pycache__" not in p.parts and p.suffix != ".pyc")
    return {str(p.relative_to(base)): digest(p) for p in sorted(files)}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def quote_identifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value):
        raise Failure("Database/role names must use 1-63 ASCII letters, digits or underscores.")
    return '"' + value + '"'


def database_swap_sql(current, prepared, retained):
    # Run on the maintenance database, not on any database being renamed.
    # Both renames commit together; the retained copy refuses new connections.
    return (
        "BEGIN; SET LOCAL lock_timeout = '10s'; "
        f"ALTER DATABASE {quote_identifier(current)} ALLOW_CONNECTIONS false; "
        f"ALTER DATABASE {quote_identifier(current)} RENAME TO {quote_identifier(retained)}; "
        f"ALTER DATABASE {quote_identifier(prepared)} RENAME TO {quote_identifier(current)}; "
        "COMMIT;"
    )


# ------------------------------------------------------------ effect descriptors (PF-A1.2)
# Every child process that can change external state carries an effect descriptor, so a
# timeout or interruption is journaled in the operation's unresolved-effects.json (A12-R01).
# Read-only invocations carry none. The classification is fail-closed: an invocation the
# tables below do not recognise as read-only is recorded as a mutation. Descriptors name the
# tool, verb and targets needed to reconcile; they never carry application values.
DOCKER_READ_ONLY = frozenset({
    ("ps",), ("inspect",), ("version",), ("compose", "version"), ("info",),
    ("image", "inspect"), ("image", "ls"), ("volume", "ls"), ("network", "ls"),
    ("container", "inspect"), ("volume", "inspect"), ("network", "inspect"),
    ("image", "save"),  # writes a host file inside the operation's own folder; the daemon is unchanged
})
DOCKER_GROUPS = frozenset({"image", "volume", "network", "compose", "container"})
COMPOSE_EXEC_READ_ONLY_PROGRAMS = frozenset({"pg_dump", "pg_dumpall", "wget"})
GIT_READ_ONLY = frozenset({"--version", "rev-parse", "ls-tree", "cat-file", "merge-base"})
SQL_READ_ONLY_KEYWORDS = frozenset({"SELECT", "SHOW"})
EFFECT_TARGET_LIMIT = 8
EFFECT_TARGET_WIDTH = 120


def effect_targets(words):
    """Bounded, option-free rendering of the arguments that identify what an invocation acts on."""
    return [str(word)[:EFFECT_TARGET_WIDTH] for word in words if not str(word).startswith("-")][:EFFECT_TARGET_LIMIT]


def docker_effect(arguments):
    """Effect descriptor for one Docker CLI invocation, None for the read-only forms."""
    words = [str(word) for word in arguments]
    if tuple(words[:1]) in DOCKER_READ_ONLY or tuple(words[:2]) in DOCKER_READ_ONLY:
        return None
    width = 2 if words[:1] and words[0] in DOCKER_GROUPS else 1
    return {"kind": "docker", "verb": " ".join(words[:width]) or "?", "targets": effect_targets(words[width:])}


def compose_effect(project, arguments):
    """Effect descriptor for one Compose invocation of ``project``, None for the read-only verbs.

    ``exec`` is classified by the program run inside the service: dump/health programs are
    read-only, everything else (createdb, dropdb, pg_restore, psql, unknown) is a mutation
    unless the caller passes an explicit descriptor (``sql()`` knows its statement).
    """
    words = [str(word) for word in arguments]
    verb = words[0] if words else "?"
    if verb in COMPOSE_READ_ONLY_VERBS or verb == "config":
        return None
    if verb == "exec":
        positional = [word for word in words[1:] if not word.startswith("-")]
        service, program = (positional + ["?", "?"])[:2]
        if program in COMPOSE_EXEC_READ_ONLY_PROGRAMS or (program == "pg_restore" and "--list" in words):
            return None
        return {"kind": "compose-exec", "verb": program, "project": project, "service": service,
                "targets": effect_targets(positional[2:])}
    return {"kind": "compose", "verb": verb, "project": project, "targets": effect_targets(words[1:])}


def git_effect(arguments):
    """Effect descriptor for one protected-store Git invocation, None for object/ref queries."""
    words = [str(word) for word in arguments]
    for index, word in enumerate(words):
        if not word.startswith("-"):
            verb, rest = word, words[index + 1:]
            break
    else:
        verb, rest = (words[0] if words else "?"), []
    if verb in GIT_READ_ONLY:
        return None
    return {"kind": "source-store", "verb": verb, "targets": effect_targets(rest)}


# Global Compose options the effect cross-check consumes (as option/value pairs) before the verb.
COMPOSE_GLOBAL_PAIRS = ("--project-directory", "--env-file", "-p", "-f")
READ_ONLY_PROBE_TOOLS = ("ip", "hostname")


def read_only_sql(words):
    """``exec ... psql ... -c <statement>`` whose first keyword is a read-only one and with no ``-f``.

    The first-keyword policy is ``sql()``'s: it classifies journaling, not database authority.
    """
    if not words or words[0] != "exec" or "-f" in words or "-c" not in words:
        return False
    positional = [word for word in words[1:] if not word.startswith("-")]
    if positional[1:2] != ["psql"]:
        return False
    index = words.index("-c")
    statement = words[index + 1] if index + 1 < len(words) else ""
    return bool(statement.strip()) and statement.lstrip().split(None, 1)[0].upper() in SQL_READ_ONLY_KEYWORDS


def unclassified_mutation(tool, arguments):
    """``None`` when a direct Git/Docker/Compose argv is read-only by the classifiers, else its verb.

    ``Controller.command`` refuses ``effect=None`` on anything this does not prove read-only
    (PF-A1.3, VERDICT A12r2-F03): a mutating child must carry an explicit effect descriptor.
    """
    words = [str(word) for word in arguments]
    if tool in READ_ONLY_PROBE_TOOLS:
        return None
    if tool == "git":
        effect = git_effect(words)
        return None if effect is None else "git " + effect["verb"]
    if tool == "docker" and words[:1] != ["compose"]:
        effect = docker_effect(words)
        return None if effect is None else "docker " + effect["verb"]
    if tool not in ("docker", "docker_compose"):
        return tool
    rest = words[1:] if tool == "docker" else words
    project = "?"
    while rest and rest[0].startswith("-"):
        if rest[0] not in COMPOSE_GLOBAL_PAIRS or len(rest) < 2:
            return "compose " + rest[0]
        if rest[0] == "-p":
            project = rest[1]
        rest = rest[2:]
    if compose_effect(project, rest) is None or read_only_sql(rest):
        return None
    return "compose " + (rest[0] if rest else "?")


def read_app_env(path, *, require_all=True):
    """Strict data parsing of an application ``.env`` (pf_config grammar). Never evaluates anything."""
    path = Path(path)
    try:
        data = pf_instance.read_bytes_nofollow(path)
    except OSError as exc:
        raise Failure(f"Cannot read {path}: {exc.strerror or exc}") from exc
    try:
        return pf_config.parse_app_env(data, label=str(path), require_all=require_all)
    except pf_config.ConfigError as exc:
        raise Failure(str(exc)) from exc


def validate_timezone_name(value):
    if not TIMEZONE_RE.fullmatch(value):
        raise Failure("SITE_TIMEZONE must be UTC or an IANA-style zone such as America/Los_Angeles.")
    return value


def validate_access_mode(value):
    if value not in ("1", "2"):
        raise Failure("Choose 1 or 2.")
    return value


def validate_ipv4(value, *, allow_loopback=True):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise Failure("PARTFLOW_BIND_IP must be a valid IPv4 address.") from exc
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise Failure("PARTFLOW_BIND_IP must be a usable IPv4 address.")
    if not allow_loopback and address.is_loopback:
        raise Failure("Direct LAN mode requires the NAS LAN IPv4 address, not 127.0.0.1.")
    return str(address)


def validate_http_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise Failure("PARTFLOW_HTTP_PORT must be an integer.") from exc
    if port < 1024 or port > 65535:
        raise Failure("PARTFLOW_HTTP_PORT must be between 1024 and 65535 for this staging package.")
    return str(port)


def validate_allowed_host(value):
    value = value.rstrip(".")
    if value == "localhost":
        return value
    if len(value) > 253 or not value:
        raise Failure("PARTFLOW_ALLOWED_HOST must be one exact hostname.")
    labels = value.split(".")
    if len(labels) < 2 or any(not HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise Failure("PARTFLOW_ALLOWED_HOST must be one exact hostname such as partflow.example.com.")
    return value.lower()


def prompt_yes_no(label, default=True):
    if unattended():
        raise Failure("Initial deployment configuration requires an interactive terminal.")
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        answer = input(label + suffix + ": ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        log("Enter y or n.")


def copy_tree_entry(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination, symlinks=False)
    elif source.is_file() and not source.is_symlink():
        shutil.copy2(source, destination)
    else:
        raise Failure(f"Unsupported local deployment path: {source}")


def create_source_archive(root, destination):
    root = Path(root)
    def select(info):
        relative = Path(info.name)
        if any(part in SOURCE_EXCLUDES for part in relative.parts):
            return None
        if not (info.isfile() or info.isdir()):
            raise Failure(f"Source backup refuses links/special files: {info.name}")
        return info
    with tarfile.open(destination, "w:gz") as archive:
        for item in sorted(root.iterdir()):
            if item.name not in SOURCE_EXCLUDES:
                archive.add(item, arcname=item.name, filter=select)


def extract_source(archive_path, destination):
    destination = Path(destination).resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if (target != destination and destination not in target.parents
                    or Path(member.name).is_absolute()
                    or not (member.isfile() or member.isdir())):
                raise Failure("Unsafe source archive member; extraction refused.")
        # Members were explicitly constrained to regular files/directories above.
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o777 & ~0o022)


def schema_gate(current_files, target_files, live_heads, target_heads, allow=False):
    if not live_heads or len(target_heads) != 1:
        raise Failure("Expected an initialized database and one target Alembic head.")
    # Existing migrations cannot be deleted or rewritten, even with approval.
    for name, checksum in current_files.items():
        if name.startswith("alembic/versions/") and target_files.get(name) != checksum:
            raise Failure(f"Existing migration changed/removed: {name}; manual recovery review required.")
    changed = current_files != target_files or sorted(live_heads) != sorted(target_heads)
    if changed and not allow:
        raise Deferred("Migration contract/schema differs. Use a reviewed manual update with --allow-migrations.")
    return changed


def select_release(releases, channel):
    choices = [r for r in releases if not r.get("draft") and r.get("published_at")
               and (channel == "prerelease" or not r.get("prerelease"))]
    return max(choices, key=lambda r: (r["published_at"], r["id"]), default=None)


def page_items(items, page):
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    if page < 1 or page > pages:
        raise Failure(f"Page must be between 1 and {pages}.")
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], pages, start


def unattended():
    """True when no operator terminal is attached: stdin absent, closed, detached or not a TTY (PF-A1.4).

    A scheduled task, a script and ``ssh`` without ``-t`` are unattended. The predicate fails closed and
    is shared by the pre-lock unattended gate and every prompt.
    """
    stream = sys.stdin
    if stream is None:
        return True
    try:
        return not stream.isatty()
    except (ValueError, OSError, AttributeError):  # closed or detached stream
        return True


def input_line(prompt):
    """One operator answer (the pf install prompts); EOF and Ctrl-C propagate to the caller."""
    return input(prompt)


def confirm(phrase, warning):
    log(warning)
    if unattended():
        raise Failure("This operation requires an interactive terminal; no --yes bypass exists.")
    answer = input(f"Type exactly '{phrase}': ").strip()
    if answer != phrase:
        raise Failure("Confirmation did not match; nothing was changed.")


# ------------------------------------------------------------ config wizards (PF-A2.2)
# `pf config admin` / `pf config app`: ask only missing or uncertain inputs, show a summary, confirm [y/N], then
# write one editable file atomically without clobbering an editor's change (compare-and-swap, section 3.7).


class ConfigCancelled(Failure):
    """config-cancelled before the summary: ``q``, end of input or Ctrl-C at a question."""

    def __init__(self, stage):
        super().__init__(stage)
        self.stage = stage


@contextlib.contextmanager
def cancelled_before_summary(path):
    """config-cancelled copy for a cancel at a question (before the summary): nothing was created or changed."""
    try:
        yield
    except ConfigCancelled as exc:
        raise Failure(f"config-cancelled: Cancelled at {exc.stage}; {path} was not created or changed.") from exc


class OptionRefused(Failure):
    """A refused option combination; exit 2 like an argparse usage error, before any registry read."""


# Own temporary names of one wizard write (section 3.7); reserved in the configuration directory.
EDITABLE_TEMP_RE = re.compile(r"(?:\.pf-config\.json|\.env)\.pf-config-[0-9a-f]{8}\Z")
GROUP_LIST_LIMIT = 30
BACKUP_GROUP_CONSEQUENCE = ("Members of the backup read group can read database contents and any credentials "
                            "included in a backup or recovery bundle.")
ADMIN_NOT_ASKED = ("branch", "ci_workflow", "release_channel", "auto_update", "health_timeout_seconds",
                   "minimum_free_mb")
MAINTENANCE_DATABASES = ("postgres", "template0", "template1")


@dataclasses.dataclass(frozen=True, repr=False)
class EditableTarget:
    """Read-only observation of one editable file (section 3.7 step 0). Holds the bytes it read; never logged."""

    path: Path
    present: bool
    data: object
    identity: object
    uid: object
    gid: object
    mode: object
    nlink: int
    removable_leftovers: tuple

    def __repr__(self):
        return f"EditableTarget({self.path}, present={self.present})"


def editable_temp_name(path, op8):
    name = Path(path).name
    return ("" if name.startswith(".") else ".") + name + ".pf-config-" + op8


def _config_file_unsafe(path, detail):
    path = Path(path)
    return Failure(f"config-file-unsafe: {path} is not a single-link regular file owned as expected, or {path.parent} "
                   f"holds an unexplained temporary file ({detail}); nothing was changed or removed.")


def _config_changed(path, detail, *, written=False):
    tail = "the observed file is left as it is" if written else "nothing was written"
    return Failure(f"config-changed: {path} changed while the wizard was running ({detail}); {tail}. Run the command "
                   "again to review the current file.")


def inspect_editable_target(path, *, keys=None):
    """Section 3.7 step 0 (read-only): the target's bytes, identity and attributes, and the classified own-pattern
    leftovers. A symlink, non-regular file, unexplained hard link, ACL-bearing file or unexplained leftover is
    refused here, before any question and again before any write."""
    path = Path(path)
    data = identity = uid = gid = mode = None
    nlink = 0
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        fd = None
    except OSError as exc:
        raise _config_file_unsafe(path, f"{path.name}: " + ("symbolic link" if exc.errno == errno.ELOOP
                                                             else str(exc.strerror or exc))) from exc
    if fd is not None:
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _config_file_unsafe(path, f"{path.name}: not a regular file")
            chunks, total = [], 0
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > pf_install.SOURCE_FILE_LIMIT:
                    raise _config_file_unsafe(path, f"{path.name}: more than {pf_install.SOURCE_FILE_LIMIT} bytes")
                chunks.append(block)
            data = b"".join(chunks)
            identity = (info.st_dev, info.st_ino)
            uid, gid, mode, nlink = info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink
        finally:
            os.close(fd)
        acl = pf_bootstrap.inspect_posix_acl(path)
        if acl.kind != "none":
            raise Failure(f"config-file-acl: {path} carries ACL entries ({acl.kind}); replacing it would drop them, so "
                          f"nothing was changed. Apply these changes by hand: "
                          f"{', '.join(keys) if keys else 'the settings you intended to change'}. ACL-preserving "
                          "writes belong to PF-A2.3.")
    temp_prefix = editable_temp_name(path, "")
    try:
        names = sorted(os.listdir(str(path.parent)))
    except OSError as exc:
        raise _config_file_unsafe(path, f"{path.parent} cannot be listed: {exc.strerror or exc}") from exc
    removable, linked = [], 0
    for name in names:
        if not (name.startswith(temp_prefix) and EDITABLE_TEMP_RE.fullmatch(name)):
            continue
        try:
            item = os.lstat(str(path.parent / name))
        except FileNotFoundError:
            continue
        regular = stat.S_ISREG(item.st_mode)
        if regular and identity is not None and (item.st_dev, item.st_ino) == identity:
            linked += 1                     # L1: interrupted create link (the target inode itself)
        elif regular and item.st_nlink == 1 and item.st_uid == pf_instance.TRUSTED_UID:
            pass                            # L2: crash before ownership
        elif regular and item.st_nlink == 1 and identity is not None \
                and (item.st_uid, item.st_gid, stat.S_IMODE(item.st_mode)) == (uid, gid, mode):
            pass                            # L3: crash after ownership in replace mode
        else:
            raise _config_file_unsafe(path, f"{name}: a reserved 'pf config' temporary name that is not a leftover "
                                            "of an interrupted run")
        removable.append(name)
    if fd is not None and not (nlink == 1 or (nlink == 2 and linked == 1)):
        raise _config_file_unsafe(path, f"{path.name}: {nlink} hard links")
    return EditableTarget(path, fd is not None, data, identity, uid, gid, mode, nlink, tuple(removable))


def write_editable_file(path, data, *, expected, expected_identity, create_gid, op8, keys=None):
    """Section 3.7 steps 1-5: refuse unless the target still equals the reviewed observation, remove classified
    leftovers, write a private temp, take the target's ownership and mode (or root:<create_gid> 0660 for a new file),
    compare-and-swap, publish by rename (replace) or link (create, never clobbers), fsync and re-read."""
    path = Path(path)
    current = inspect_editable_target(path, keys=keys)
    if (current.data if current.present else None) != expected or current.identity != expected_identity:
        raise _config_changed(path, "it differs from the file the wizard reviewed")
    if current.removable_leftovers:
        for name in current.removable_leftovers:
            os.unlink(str(path.parent / name))
        pf_instance._fsync_directory(path.parent)
        log("config-temp-removed: Removed leftover temporary file(s) of an interrupted 'pf config' run: "
            + ", ".join(current.removable_leftovers) + ".")
    temp = path.parent / editable_temp_name(path, op8)
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    published = False
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
            if current.present:
                os.fchown(fd, current.uid, current.gid)
                os.fchmod(fd, current.mode)
            else:
                os.fchown(fd, pf_instance.TRUSTED_UID, create_gid)
                os.fchmod(fd, 0o660)
        finally:
            os.close(fd)
        check = inspect_editable_target(path, keys=keys)
        if current.present:
            if not check.present or check.data != expected or check.identity != expected_identity or check.nlink != 1:
                raise _config_changed(path, "another writer changed it before the swap")
            os.replace(str(temp), str(path))
            published = True
        else:
            if check.present:
                raise _config_changed(path, "another writer created it before the swap")
            try:
                os.link(str(temp), str(path))
            except FileExistsError as exc:
                raise _config_changed(path, "another writer created it before the swap") from exc
            published = True
            os.unlink(str(temp))
    except BaseException:
        if not published:
            try:
                os.unlink(str(temp))
            except OSError:
                pass
        raise
    pf_instance._fsync_directory(path.parent)
    try:
        observed = pf_install.read_regular_file(path)
    except OSError as exc:
        raise _config_changed(path, f"after the write it cannot be read: {exc.strerror or exc}", written=True) from exc
    if observed != data:
        raise _config_changed(path, "after the write it holds other bytes", written=True)


def write_reviewed(path, data, target, *, create_gid, op8, keys, secret=False):
    """write_editable_file against the reviewed observation ``target``. An interrupt is reported from an observation
    of the target (old or new bytes), never assumed."""
    try:
        write_editable_file(path, data, expected=target.data if target.present else None,
                            expected_identity=target.identity, create_gid=create_gid, op8=op8, keys=keys)
    except KeyboardInterrupt as exc:
        try:
            observed = pf_install.read_regular_file(path)
        except OSError:
            observed = None
        if observed == data:
            raise Failure(f"config-interrupted: {path} was written before the interrupt and holds the new content; "
                          "run the command again to review it.") from exc
        raise Failure(f"config-cancelled: Cancelled; {path} was not created or changed"
                      + (" and the generated password was discarded" if secret else "") + ".") from exc


def remove_editable_leftovers(target):
    """Nothing to change: still remove the classified leftovers of an interrupted run (re-inspected first)."""
    if not target.removable_leftovers:
        return
    current = inspect_editable_target(target.path)
    if current.data != target.data or current.identity != target.identity:
        raise _config_changed(target.path, "it differs from the file the wizard reviewed")
    for name in current.removable_leftovers:
        os.unlink(str(target.path.parent / name))
    pf_instance._fsync_directory(target.path.parent)
    log("config-temp-removed: Removed leftover temporary file(s) of an interrupted 'pf config' run: "
        + ", ".join(current.removable_leftovers) + ".")


def host_groups():
    """Read-only group detection (section 3.3): ``users`` first when it exists, then every group with gid >= 1000
    except 65534, sorted by name, as (name, gid). Nothing is created."""
    groups = {}
    for entry in grp.getgrall():
        groups.setdefault(entry.gr_name, entry.gr_gid)
    result = [("users", groups["users"])] if "users" in groups else []
    result += sorted((name, gid) for name, gid in groups.items()
                     if name != "users" and gid >= 1000 and gid != 65534)
    return result


def group_exists(name):
    if not isinstance(name, str):
        return False
    try:
        grp.getgrnam(name)
    except (KeyError, ValueError):
        return False
    return True


def ask_answer(stage, question, *, default=None, validate=None, show_default=True):
    """One wizard answer. Enter takes ``default``; ``q``, end of input and Ctrl-C cancel (config-cancelled)."""
    suffix = f" [{default}]" if default is not None and show_default else ""
    while True:
        try:
            answer = input(f"{question}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise ConfigCancelled(stage) from exc
        if answer == "q":
            raise ConfigCancelled(stage)
        if not answer:
            if default is None:
                log("A value is required.")
                continue
            answer = str(default)
        if validate is None:
            return answer
        try:
            return validate(answer)
        except Failure as exc:
            log("Invalid value: " + str(exc))


GROUP_LABELS = {
    "workspace_write_group": "Group that can read and edit the workspace and configuration",
    "backup_read_group": "Group that can view and copy backups and recovery bundles",
}


def ask_group(key, *, default, location):
    """Ask one real group from the detected list (or any existing name); groups are never created."""
    candidates = host_groups()
    shown = candidates[:GROUP_LIST_LIMIT]
    log(GROUP_LABELS[key])
    log("  Location: " + location)
    log("  Groups on this host:")
    for number, (name, gid) in enumerate(shown, 1):
        log(f"    {number}. {name} (gid {gid})")
    if not shown:
        log("    (none detected; type a name)")
    if len(candidates) > len(shown):
        log(f"    … and {len(candidates) - len(shown)} more; type a name")
    number = next((str(index) for index, (name, _) in enumerate(shown, 1) if name == default), None)

    def validate(answer):
        if answer.isdigit() and 1 <= int(answer) <= len(shown):
            return shown[int(answer) - 1][0]
        if pf_instance._anchored_fullmatch(pf_config.ADMIN_CONFIG_SCHEMA["properties"][key]["pattern"], answer) \
                and group_exists(answer):
            return answer
        raise Failure(f"{answer!r} is not a listed number or an existing group on this host; groups are never "
                      "created. Choose a number or type an existing group name.")

    return ask_answer(key, f"Choose a number or type a group name [{number or 'none'}]", default=number,
                      validate=validate, show_default=False)


def confirm_write(path, *, secret=False):
    """[y/N] confirmation of one editable write (OD-A22-17); anything but y/yes cancels."""
    try:
        answer = input(f"Write {path}? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        raise Failure(f"config-cancelled: Cancelled; {path} was not created or changed"
                      + (" and the generated password was discarded" if secret else "") + ".")


def display_value(value):
    return value if isinstance(value, str) else json.dumps(value)


def load_admin_example(path, pf_command):
    """The installed immutable example as schema 2 settings, else admin-example-invalid."""
    try:
        parsed = pf_config.parse_admin_config(pf_install.read_regular_file(path), label=str(path))
        problem = parsed.problems[0] if parsed.problems else None
    except OSError as exc:
        parsed, problem = None, str(exc.strerror or exc)
    if problem is not None or parsed.schema_version != pf_config.ADMIN_CONFIG_SCHEMA_VERSION:
        raise Failure(f"admin-example-invalid: The installed example {path} is not a valid schema 2 admin configuration "
                      f"({problem or 'legacy schema 1'}); nothing was changed. Run '{pf_command} doctor' and reinstall "
                      "the control release.")
    return dict(parsed.values)


def refuse_admin_config(path, parsed, *, rerun, prefix, release_id):
    """admin-config-invalid / admin-config-version-unsupported copy for a refused current file (nothing asked).
    ``rerun`` is the full command that re-checks this file (pre-registration: with --configuration/--project)."""
    if parsed.code == "admin-config-version-unsupported":
        raise Failure(f"admin-config-version-unsupported: {path} declares schema_version "
                      f"{json.dumps(parsed.declared_version)}; this control ({release_id}) reads schema 2 and the legacy "
                      "form without schema_version. Nothing was changed. If a newer control wrote this file, select "
                      f"that control again ('{prefix} install control --release <id>') or restore the previous file.")
    more = f" (+{len(parsed.problems) - 1} more)" if len(parsed.problems) > 1 else ""
    raise Failure(f"admin-config-invalid: {path}: {parsed.problems[0]}{more}. Nothing was changed; fix the file by "
                  f"hand, then run '{rerun}' again.")


def admin_group_questions(values, *, mode, locations):
    """Ask the missing or uncertain groups (section 3.3) in order; returns {key: answer} and updates ``values``.
    Create mode asks both (the example's group preselected when it exists); otherwise only a group that does not
    exist on this host is asked, with no default (a broader group is never picked silently)."""
    asked = {}
    for key in ("workspace_write_group", "backup_read_group"):
        exists = group_exists(values[key])
        if mode != "create" and exists:
            continue
        if key == "backup_read_group":
            log(BACKUP_GROUP_CONSEQUENCE)
        answer = ask_group(key, default=values[key] if mode == "create" and exists else None,
                           location=locations[key])
        asked[key] = answer
        values[key] = answer
    return asked


def admin_summary(path, *, mode, before, document, asked, instance_line, environment_line, app_hint):
    """Section 4.6 admin summary (no confirmation)."""
    title = {"create": "create", "migrate": "migrate schema 1 -> 2", "complete": "complete"}[mode]
    log(f"Admin configuration {path} ({title})")
    log(instance_line)
    log({"create": "  schema_version: 2 (from example)", "migrate": "  schema_version: (none) -> 2",
         "complete": "  schema_version: 2 (kept)"}[mode])
    for key in pf_config.ADMIN_CONFIG_KEYS:
        value = display_value(document[key])
        if key in asked and before is not None:
            old = display_value(before.values[key])
            log(f"  {key}: {old} -> {value} (asked: {old} does not exist on this host)")
        elif key in asked:
            log(f"  {key}: {value} (asked)")
        elif before is None:
            log(f"  {key}: {value} (from example)")
        elif key in before.implicit:
            log(f"  {key}: {value} (implicit legacy default, now explicit)")
        else:
            log(f"  {key}: {value} (kept)")
    log("Not asked here (edit the file by hand to change): " + ", ".join(ADMIN_NOT_ASKED) + ".")
    log(environment_line)
    if before is not None and "backup_read_group" in asked:
        log("backup_read_group change takes effect at the next backup or purge without a separate approval "
            "(revision-bound group approval: PF-A2.3).")
    log(app_hint)


def app_value_problem(kind, value):
    """``check`` of plan_app_config: None when ``value`` is valid as written (canonical), else the reason."""
    try:
        if kind == "identifier":
            quote_identifier(value)
        elif kind == "database":
            quote_identifier(value)
            if value in MAINTENANCE_DATABASES:
                return "a PostgreSQL maintenance/template database cannot hold the application"
        elif kind == "timezone":
            validate_timezone_name(value)
        elif kind in ("access", "port", "host"):
            canonical = {"access": validate_ipv4, "port": validate_http_port, "host": validate_allowed_host}[kind](value)
            if canonical != value:
                return f"not written in its canonical form ({canonical})"
        else:
            return f"no rule for kind {kind!r}"
    except Failure as exc:
        return str(exc)
    return None


def app_canonical_value(kind, value):
    """The canonical spelling of a valid but non-canonical value (e.g. 05173 -> 5173), else None."""
    validator = {"access": validate_ipv4, "port": validate_http_port, "host": validate_allowed_host}.get(kind)
    if validator is None:
        return None
    try:
        canonical = validator(value)
    except Failure:
        return None
    return canonical if canonical != value else None


# Explicit routes into a pending journal (LIFECYCLE.md section 1, step 2). A
# command absent from this table is refused while a journal exists. Each
# handler still validates the exact journal state it accepts; the table only
# replaces the former blanket ``allow_pending`` bypass.
PENDING_ROUTES = {
    "resume": (
        lambda journal: journal.get("phase") in ("paused", "backup-ready"),
        "resume the unchanged deployment after a pre-change failure",
    ),
    "rollback": (
        lambda journal: journal.get("operation") in ("update", "rollback", "reset-db"),
        "roll back to a verified checkpoint (use --restore-db when data/schema may have changed)",
    ),
    "abort-deploy": (
        lambda journal: journal.get("operation") == "deploy",
        "remove the incomplete first deployment before frontend access opened",
    ),
    "purge": (
        lambda journal: journal.get("operation") == "purge" and journal.get("phase") == "deleting",
        "resume the recorded purge deletion plan with the already verified recovery bundle",
    ),
}
# Journal keys shown by diagnostics. Anything else is reported by name only.
JOURNAL_PUBLIC_KEYS = (
    "operation", "phase", "started", "recovery", "active_checkpoint", "checkpoint", "selected",
    "database", "migration_required", "delete_backups", "reset_admin_config",
)


class Controller:
    """Lifecycle controller bound to one immutable, protected InstanceContext.

    Construction resolves paths from the registered context only. It never
    creates directories, changes ownership or modes, writes configuration or
    migrates state; those happen in explicit, locked operations.
    """

    def __init__(self, context, *, validation=None, running_release=None):
        if not isinstance(context, pf_instance.InstanceContext):
            raise Failure("Controller requires a resolved InstanceContext; legacy path arguments are not accepted.")
        self.context = context
        self.validation = validation
        self.running_release = running_release
        self.root = context.paths.workspace
        self.control_dir = context.control.path
        self.admin_dir = self.control_dir  # Compatibility name used by a few helpers.
        self.config_dir = context.paths.configuration
        self.state = context.state_dir
        self.backups_root = context.paths.backups
        self.revisions_root = self.backups_root / "revisions"
        self.backups_dir = self.revisions_root / context.compose_project
        self.recovery_root = context.paths.recovery / context.compose_project
        self.pending = self.state / "pending.json"
        self.override = self.state / "active-images.yaml"
        self.cli = None
        self._config = None
        self.config_schema_version = None
        self._backup_gid = None
        self._workspace_gid = None
        # PF-A1.2: one runner, one frozen configuration per locked operation, one source store.
        self.redactor = pf_runner.Redactor()
        self._runner = None
        self.frozen = None
        self.operation_id = None
        self.operation_dir = None
        self._snapshots = 0
        self.remote_override = None          # tests: local approved remote instead of GitHub
        self.source_protocols = ("https",)   # tests: ("file",) for a local remote
        # PF-A1.3 Docker scope: process-wide daemon verification (an observation or the cached
        # DaemonFailure), whether any effect-carrying child has started, and per-operation state
        # (topology check, approved Compose input keys, render sequence, image tags created).
        self._daemon = None
        self._daemon_recorded_op = None
        self.effects_started = False
        self.compose_version = None
        self._inventory_active = False
        self._topology_checked = False
        self._operation_command = None
        self._approved_envelopes = {}
        self._envelope_sequence = 0
        self.created_image_refs = []

    def ensure_config(self):
        """Load the runtime configuration once (read-only); return the cached values."""
        if self._config is None:
            self._config = self.load_app_config()
        return self._config

    @property
    def config(self):
        return self.ensure_config()

    @config.setter
    def config(self, value):
        self._config = value

    @property
    def backup_gid(self):
        self.ensure_config()
        return self._backup_gid

    @property
    def workspace_gid(self):
        self.ensure_config()
        return self._workspace_gid

    def load_app_config(self):
        """Read-only strict load of config/pf-config.json against the installed app schema.

        Missing or invalid configuration is a diagnostic failure; the file is
        never created from the template or rewritten here.
        """
        path = self.config_dir / "pf-config.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except OSError as exc:
            raise Failure(
                f"Runtime configuration is missing or unreadable: {path} ({exc.strerror}). "
                f"The controller does not create it; create it with '{self.pf_command()} config admin'."
            ) from exc
        # PF-A2.1: the parse and shape rules are shared with the installer; the first problem is raised with the
        # unchanged message. PF-A2.2: both schemas are read as they are (schema 1 with its frozen implicit values);
        # loading never migrates (INV-06).
        parsed = pf_config.parse_admin_config(data, label=str(path))
        if parsed.problems:
            raise Failure(parsed.problems[0])
        config = dict(parsed.values)
        self.config_schema_version = parsed.schema_version
        # The protected registration is authoritative for identity and environment.
        if config["project"] != self.context.compose_project:
            raise Failure(
                f"pf-config.json project {config['project']!r} disagrees with the registered compose_project "
                f"{self.context.compose_project!r}; the protected registration is authoritative."
            )
        if config["environment"] != self.context.approved_environment:
            raise Failure(
                f"pf-config.json environment {config['environment']!r} disagrees with the approved environment "
                f"{self.context.approved_environment!r}; editable configuration cannot change policy."
            )
        try:
            self._backup_gid = grp.getgrnam(config["backup_read_group"]).gr_gid
            self._workspace_gid = grp.getgrnam(config["workspace_write_group"]).gr_gid
        except KeyError as exc:
            raise Failure(
                "Configured DSM group does not exist. Check backup_read_group and workspace_write_group in "
                + f"{path}; choose an existing group with '{self.pf_command()} config admin'."
            ) from exc
        return config

    def pf_command(self):
        """The operator command prefix of this instance in copy: the launcher rule of pf_install (read-only)."""
        return pf_install.launcher_prefix(self.context.installation_root) + " --instance " + self.context.slug

    # ------------------------------------------------------------ runner (PF-A1.2)

    def registered_tools(self):
        """Read-only: the host executables registered in ``<root>/bootstrap/tools.conf``."""
        path = self.context.installation_root / pf_instance.BOOTSTRAP_DIR / pf_bootstrap.TOOLS_CONF_NAME
        try:
            return pf_bootstrap.parse_tools_conf(pf_instance.read_bytes_nofollow(path), label=str(path),
                                                 error=Failure)
        except (OSError, UnicodeDecodeError) as exc:
            raise Failure(f"Registered tools cannot be read: {path}: {exc}") from exc

    @property
    def runner(self):
        """The single process boundary. Built lazily and read-only; it validates each tool on first use."""
        if self._runner is None:
            context = self.context
            try:
                self._runner = pf_runner.ProcessRunner(
                    self.registered_tools(), home=context.home_dir, docker_config=context.docker_config_dir,
                    docker_host=context.daemon.endpoint, redactor=self.redactor,
                )
            except pf_runner.RunnerError as exc:
                raise Failure(str(exc)) from exc
        return self._runner

    def command(self, argv, *, effect, cwd=None, output=None, input_file=None, env=None, timeout=None,
                stream=False):
        """Every child process of the control release. ``argv[0]`` is a typed executable id.

        ``effect`` is required (PF-A1.3): a descriptor for a child that can change external
        state, or ``None``, which is accepted only for an argv the classifiers prove read-only.
        ``env`` may only carry allowlisted application values (Compose interpolation);
        the host environment is built by the runner from scratch. Output is bounded and
        redacted; every call has a deadline; timeouts terminate the process group and,
        when ``effect`` describes an external effect, record it as unresolved. No process
        starts before, in order: the argv/tool checks, the effect cross-check, the locked
        operation check, the child environment allowlist, the daemon binding (every Docker
        and Compose child) and the lazy topology ownership check (PF-A1.3).
        """
        if not argv:
            raise Failure("Empty command.")
        tool, arguments = str(argv[0]), [str(item) for item in argv[1:]]
        if tool not in pf_bootstrap.TOOL_IDS:
            raise Failure(f"{tool!r} is not a registered executable id; the control release never searches PATH.")
        if effect is None:
            verb = unclassified_mutation(tool, arguments)
            if verb is not None:
                raise Failure(f"unclassified-mutation: {verb}: a mutating child must carry an explicit effect "
                              "descriptor; nothing was started.")
        if effect is not None and self.operation_dir is None:
            # A child that can change external state must have a journal to record an
            # unresolved effect in; only a locked operation provides one.
            raise Failure(f"{tool} {' '.join(arguments[:3])}: a mutating child process requires a locked "
                          "operation (no unresolved-effect journal outside one).")
        if timeout is None:
            timeout = TIMEOUT_DATA if tool == "docker" and arguments[:2] in (["image", "save"], ["image", "load"]) \
                else TIMEOUT_DIAGNOSTIC
        try:
            child_env = self.runner.environment(env or None, allowed_keys=pf_config.CHILD_KEYS)
        except pf_runner.RunnerError as exc:
            raise Failure(str(exc)) from exc
        if tool in ("docker", "docker_compose"):
            if (tool, *arguments) != pf_docker.DAEMON_PROBE_ARGV:
                self.verify_daemon()
            if effect is not None and self.operation_dir is not None and not self._topology_checked \
                    and not self._inventory_active:
                self.require_topology_owned(self._operation_command)
        try:
            spec = pf_runner.ProcessSpec(
                tool=tool, argv=tuple(arguments), cwd=str(cwd or self.context.installation_root), env=child_env,
                timeout=float(timeout), stdin=input_file,
                stdout="stream" if stream else output, effect=effect, label=tool,
            )
            if effect is not None:
                self.effects_started = True
            result = self.runner.run(spec)
        except pf_runner.RunnerError as exc:
            raise Failure(str(exc)) from exc
        if not result.ok:
            status = "timed out" if result.timed_out else f"exit {result.returncode}"
            raise Failure(f"{tool} failed ({status}).\n{pf_runner.failure_detail(result)}")
        return result.stdout.strip()

    def docker(self, *args, **kwargs):
        if "effect" not in kwargs:
            kwargs["effect"] = docker_effect(args)
        return self.command(["docker", *args], **kwargs)

    def compose_cli(self):
        """The Compose entry point: the registered Docker CLI plugin, else a registered standalone binary."""
        if self.cli is None:
            try:
                self.compose_version = self.docker("compose", "version").strip()
                self.cli = ["docker", "compose"]
            except DaemonFailure:
                raise
            except Failure as exc:
                if "docker_compose" not in self.registered_tools():
                    raise Failure("Docker Compose is unavailable: " + str(exc).splitlines()[0]) from exc
                self.compose_version = self.command(["docker_compose", "version"], effect=None).strip()
                self.cli = ["docker_compose"]
        return list(self.cli)

    # ------------------------------------------------------------ Docker scope (PF-A1.3)

    def write_private_json(self, name, value):
        """A private (0600) audit/authority file in the current operation directory (no-op outside one)."""
        if self.operation_dir is None:
            return None
        path = self.operation_dir / name
        pf_config._write_private(path, pf_docker.normalize_json(value) + b"\n", 0o600)
        return path

    def daemon_failure(self, code, detail):
        """Operator copy for a daemon refusal (no values; engine IDs are identities, not secrets)."""
        context = self.context
        endpoint = context.daemon.endpoint
        if code == "daemon-unreachable":
            text = (f"Docker daemon at {endpoint} did not answer ({detail}); no further Docker or Compose step "
                    "was attempted.")
        elif code == "daemon-info-invalid":
            text = f"Docker daemon at {endpoint} returned an unusable identity ({detail}); every Docker step is refused."
        elif code == "daemon-rootless":
            text = (f"The daemon at {endpoint} runs in rootless mode; only a local rootful daemon is supported. "
                    "Nothing was changed.")
        else:
            head = (f"Docker daemon drift: instance {context.slug} is bound to engine {context.daemon.engine_id} at "
                    f"{endpoint}, but the endpoint answers as engine {detail}.")
            if self.effects_started:
                journal = self.read_journal() or {}
                operation = journal.get("operation") or self._operation_command or "the operation"
                text = (head + f" Every further Docker/Compose step is refused. {operation} stopped in phase "
                        f"{journal.get('phase') or 'unknown'}; review it with 'pf status --instance {context.slug}'. "
                        "Re-binding a daemon is an explicit installation transaction (PF-A2).")
            else:
                text = head + (" Every Docker/Compose step is refused and nothing was changed. Re-binding a daemon is "
                               "an explicit installation transaction (PF-A2).")
        return DaemonFailure(code, f"{code}: {text}", detail)

    def verify_daemon(self, *, refresh=False):
        """Observe the bound daemon (engine ID, rootless) once per process; refuse drift before any mutation.

        The result, success or failure, is cached; a failure is raised again by every later
        Docker/Compose child without starting a process. ``refresh`` re-observes (immediately
        before a deletion plan executes, which also covers a resumed purge or abort).
        """
        if self._daemon is not None and not refresh:
            if isinstance(self._daemon, Failure):
                raise self._daemon
            self._record_daemon(self._daemon)
            return self._daemon
        self._daemon = None
        endpoint = self.context.daemon.endpoint
        socket_path = endpoint[len(pf_instance.UNIX_SCHEME):] if endpoint.startswith(pf_instance.UNIX_SCHEME) \
            else endpoint
        try:
            if not os.path.lexists(socket_path):
                raise self.daemon_failure("daemon-unreachable", "socket absent")
            try:
                text = self.command(["docker", "info", "--format", "{{json .}}"], effect=None,
                                    timeout=TIMEOUT_DAEMON_PROBE)
            except DaemonFailure:
                raise
            except Failure as exc:
                lines = [line.strip() for line in str(exc).splitlines()[1:] if line.strip()]
                detail = lines[0] if lines else str(exc).splitlines()[0]
                raise self.daemon_failure("daemon-unreachable", detail[:200]) from exc
            try:
                observation = pf_docker.parse_daemon_info(text, endpoint=endpoint)
            except pf_docker.DockerScopeError as exc:
                detail = "server error" if exc.code == "daemon-unreachable" else \
                    (exc.findings[0].message if exc.findings else "info")
                raise self.daemon_failure(exc.code, detail) from exc
            try:
                pf_docker.check_daemon(observation, self.context.daemon)
            except pf_docker.DockerScopeError as exc:
                raise self.daemon_failure(exc.code, observation.engine_id) from exc
        except Failure as exc:
            self._daemon = exc
            raise
        self._daemon = observation
        self._record_daemon(observation)
        return observation

    def _record_daemon(self, observation):
        if self.operation_dir is None or self._daemon_recorded_op == self.operation_id:
            return
        self.write_private_json("daemon.json", {
            "schema_version": 1, "observed_at": utc(), "endpoint": observation.endpoint,
            "registered_engine_id": self.context.daemon.engine_id, "engine_id": observation.engine_id,
            "server_version": observation.server_version, "operating_system": observation.operating_system,
            "rootless": observation.rootless, "result": "verified",
        })
        self._daemon_recorded_op = self.operation_id

    def describe_daemon(self):
        observation = self.verify_daemon()
        return (f"verified | engine {observation.engine_id} | endpoint {observation.endpoint} | server "
                f"{observation.server_version or 'unknown'} | local rootful")

    def compose_env_file(self):
        """The env-file Compose reads: the frozen snapshot inside an operation, else the diagnostics file."""
        if self.frozen is not None:
            return self.frozen.env_file
        return self.context.diagnostic_env_path

    def compose_prefix(self, cli, root, env_file, override):
        command = list(cli) + [
            "--project-directory", str(root),
            "--env-file", str(env_file),
            "-p", self.context.compose_project,
            "-f", str(self.control_dir / "compose.nas.yaml"),
        ]
        if override is not None:
            command += ["-f", str(override)]
        return command

    def envelope_failure(self, exc):
        findings = exc.findings or (pf_docker.Finding(exc.code, "$", "refused"),)
        lines = [f"  - {finding.code} at {finding.path}: {finding.message}" for finding in findings]
        return Failure(f"{exc.code}: Compose envelope refused for {self.context.compose_project} "
                       f"({len(findings)} finding(s)); nothing was built, created or started:\n" + "\n".join(lines))

    def render_compose(self, root, override, child):
        """``compose ... config --format json`` with exactly the inputs of the verb; literal bytes, never logged.

        Inside an operation the runner writes ``compose-<n>.json`` (0600) directly; outside one
        (doctor) an unlinked private temporary file is used. Returns (model, file name or None).
        """
        cli = self.compose_cli()
        argv = self.compose_prefix(cli, root, self.compose_env_file(), override) + ["config", "--format", "json"]
        name = None
        if self.operation_dir is not None:
            self._envelope_sequence += 1
            name = f"compose-{self._envelope_sequence}.json"
            fd = os.open(str(self.operation_dir / name),
                         os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            handle = os.fdopen(fd, "w+b")
        else:
            handle = tempfile.TemporaryFile(dir=str(self.context.operations_dir))

        def failed(reason):
            return pf_docker.DockerScopeError("envelope-render-failed", [pf_docker.Finding(
                "envelope-render-failed", "$", reason)])

        with handle:
            try:
                # Classifier-computed (read-only `config`); the runtime cross-check re-verifies the argv.
                self.command(argv, env=child, output=handle, timeout=TIMEOUT_DIAGNOSTIC,
                             effect=compose_effect(self.context.compose_project, argv[-3:]))
            except DaemonFailure:
                raise
            except Failure as exc:
                raise failed(str(exc).splitlines()[0] + "; Compose v1 cannot render `config --format json` and "
                             "is unsupported") from exc
            handle.flush()
            handle.seek(0)
            data = handle.read(pf_docker.RENDER_LIMIT + 1)
        if len(data) > pf_docker.RENDER_LIMIT:
            raise failed("the resolved model exceeds 4 MiB")
        if not data.strip():
            raise failed("empty output")
        try:
            model = pf_instance.parse_strict_json(data, label="compose config")
        except pf_instance.ContextError as exc:
            raise failed("duplicate key" if "Duplicate" in str(exc) else "invalid JSON") from exc
        return model, name

    def require_envelope(self, root, override, child, *, value_overrides=()):
        """Render, validate and record the Compose model of exactly these inputs before a mutating verb.

        The input key covers the installed file, the protected override, the project directory,
        the frozen env-file, the instance ID and every effective child value (overrides
        included); a key approved earlier in this operation is not rendered again.
        """
        root = Path(root)
        compose_file = self.control_dir / "compose.nas.yaml"
        try:
            compose_bytes = pf_instance.read_bytes_nofollow(compose_file)
            override_bytes = pf_instance.read_bytes_nofollow(override) if override is not None else None
        except OSError as exc:
            raise Failure(f"envelope-render-failed: Compose inputs unreadable: {exc.strerror or exc}") from exc
        key = (
            pf_instance.sha256_bytes(compose_bytes),
            pf_instance.sha256_bytes(override_bytes) if override_bytes is not None else None,
            str(root), self.frozen.env_sha256 if self.frozen is not None else None, self.context.instance_id,
            pf_instance.sha256_bytes(pf_instance.normalize_json(child)),
        )
        if key in self._approved_envelopes:
            return self._approved_envelopes[key]
        project = self.context.compose_project
        record = {
            "sequence": None, "compose_version": None,
            "inputs": {"compose_file": str(compose_file), "compose_file_sha256": key[0],
                       "override": str(override) if override is not None else None, "override_sha256": key[1],
                       "project_directory": str(root), "repo_root": child.get("PARTFLOW_REPO_ROOT"),
                       "frozen_env_sha256": key[3], "instance_id": key[4], "effective_values_sha256": key[5],
                       "value_overrides": sorted(value_overrides)},
            "resolved_file": None, "resolved_sha256": None, "escape_mode": None, "result": "refused",
        }
        try:
            images = pf_docker.parse_image_override(override_bytes, project=project) \
                if override_bytes is not None else None
            expectation = pf_docker.ComposeExpectation(
                project=project, instance_id=self.context.instance_id, repo_root=child["PARTFLOW_REPO_ROOT"],
                values=types.MappingProxyType({name: child[name] for name in pf_config.APP_KEYS}),
                database_url=child["PARTFLOW_DATABASE_URL"], images=images)
            model, name = self.render_compose(root, override, child)
            record.update(sequence=self._envelope_sequence if name else None, resolved_file=name,
                          compose_version=self.compose_version,
                          resolved_sha256=pf_instance.sha256_bytes(pf_instance.normalize_json(model)))
            if pf_instance.read_bytes_nofollow(compose_file) != compose_bytes or (
                    override is not None and pf_instance.read_bytes_nofollow(override) != override_bytes):
                raise pf_docker.DockerScopeError("envelope-render-failed", [pf_docker.Finding(
                    "envelope-render-failed", "$", "Compose inputs changed during the render")])
            result = pf_docker.validate_envelope(model, expectation)
        except pf_docker.DockerScopeError as exc:
            record["code"] = exc.code
            self._append_envelope_record(record)
            raise self.envelope_failure(exc) from exc
        record.update(escape_mode=result.escape_mode, result="approved")
        self._append_envelope_record(record)
        self._approved_envelopes[key] = result
        return result

    def _append_envelope_record(self, record):
        if self.operation_dir is None:
            return
        path = self.operation_dir / "compose-envelope.json"
        document = {"schema_version": 1, "renders": []}
        if os.path.lexists(str(path)):
            document = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(path), label=str(path))
        document["renders"].append(record)
        self.write_private_json("compose-envelope.json", document)

    def describe_envelope(self):
        """Doctor: render and validate the current inputs read-only (literal values; no file remains)."""
        values, _ = self.compose_inputs()
        override = self.override if self.override.exists() else None
        child = pf_config.child_values(values, workspace=self.root, instance_id=self.context.instance_id)
        result = self.require_envelope(self.root, override, child)
        version = (self.compose_version or "unknown").split()[-1]  # "Docker Compose version v2.x" -> "v2.x"
        return (f"ok | compose {version} | services {', '.join(pf_docker.SERVICES)} | "
                f"dollar-escape {result.escape_mode} | values compared")

    def docker_inventory(self):
        """Exact, read-only inventory of this instance's resources on the bound daemon (ARCH section 8)."""
        if self._inventory_active:
            raise Failure("Internal error: nested Docker inventory.")
        self._inventory_active = True
        try:
            return self._observe_inventory()
        except pf_docker.DockerScopeError as exc:
            raise Failure(f"{exc.code}: Docker inventory output is not understood: "
                          + "; ".join(finding.render() for finding in exc.findings)) from exc
        finally:
            self._inventory_active = False

    @staticmethod
    def _ls_rows(text):
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            row = pf_docker.strict_json(line, code="inventory-invalid", path="ls")
            if not isinstance(row, dict):
                raise pf_docker.DockerScopeError("inventory-invalid", [pf_docker.Finding(
                    "inventory-invalid", "ls", "row is not an object")])
            rows.append(row)
        return rows

    def _selected_names(self, kind, topology, label_filter):
        """Exact topology names present on the daemon plus every name carrying this instance's label."""
        extra = ["--no-trunc"] if kind == "network" else []
        present = {row.get("Name") for row in self._ls_rows(self.docker(kind, "ls", *extra, "--format", "{{json .}}"))}
        labelled = {row.get("Name") for row in self._ls_rows(
            self.docker(kind, "ls", *extra, "--filter", label_filter, "--format", "{{json .}}"))}
        names = (present & set(topology)) | labelled
        if not all(isinstance(name, str) and name for name in names):
            raise pf_docker.DockerScopeError("inventory-invalid", [pf_docker.Finding(
                "inventory-invalid", kind, "ls row without a name")])
        return sorted(names)

    def _observe_containers(self, batch):
        """Every container (`ps -a`, then batched inspect). A container removed between the two calls
        (another application's short-lived container) makes inspect fail with "No such container";
        the whole listing is then taken again, a bounded number of times, and never partially used."""
        vanished = None
        for _ in range(INVENTORY_ATTEMPTS):
            ids = [line.strip() for line in self.docker("ps", "-a", "--no-trunc", "--format", "{{.ID}}").splitlines()
                   if line.strip()]
            containers = []
            try:
                for start in range(0, len(ids), batch):
                    containers += pf_docker.parse_field_lines(self.docker(
                        "container", "inspect", "--format", pf_docker.CONTAINER_FIELDS, *ids[start:start + batch]),
                        kind="container")
            except DaemonFailure:
                raise
            except Failure as exc:
                if "No such container" not in str(exc):
                    raise
                vanished = exc
                continue
            return containers
        raise Failure(
            f"inventory-unstable: Docker inventory could not be completed: containers disappeared between "
            f"'docker ps -a' and 'docker container inspect' on {INVENTORY_ATTEMPTS} consecutive attempts (another "
            "application is creating and removing containers). This step stopped before any further Docker change; "
            "retry the command when the host is quieter (an interrupted purge or abort-deploy resumes its frozen "
            "plan).") from vanished

    def _observe_inventory(self):
        project, instance_id = self.context.compose_project, self.context.instance_id
        label_filter = f"label={pf_docker.INSTANCE_LABEL}={instance_id}"
        batch = 50
        containers = self._observe_containers(batch)
        names = pf_docker.topology_names(project)
        observed = {}
        for kind, fields in (("volume", pf_docker.VOLUME_FIELDS), ("network", pf_docker.NETWORK_FIELDS)):
            selected = self._selected_names(kind, names[kind].values(), label_filter)
            observed[kind] = []
            for start in range(0, len(selected), batch):
                observed[kind] += pf_docker.parse_field_lines(self.docker(
                    kind, "inspect", "--format", fields, *selected[start:start + batch]), kind=kind)
        references = set()
        for row in self._ls_rows(self.docker("image", "ls", "--no-trunc", "--filter", label_filter,
                                             "--format", "{{json .}}")):
            repository, tag = row.get("Repository"), row.get("Tag")
            if isinstance(repository, str) and isinstance(tag, str) and "<none>" not in (repository, tag):
                references.add(repository + ":" + tag)
        references = sorted(references)
        images = []
        for start in range(0, len(references), batch):
            chunk = references[start:start + batch]
            inspected = pf_docker.parse_field_lines(self.docker(
                "image", "inspect", "--format", pf_docker.IMAGE_FIELDS, *chunk), kind="image")
            if len(inspected) != len(chunk):
                raise pf_docker.DockerScopeError("inventory-invalid", [pf_docker.Finding(
                    "inventory-invalid", "image", "inspect returned a different number of images")])
            for reference, record in zip(chunk, inspected):
                images.append({"reference": reference, "id": record["id"], "labels": record["labels"]})
        return pf_docker.classify_inventory(project=project, instance_id=instance_id, containers=containers,
                                            volumes=observed["volume"], networks=observed["network"], images=images)

    @staticmethod
    def resource_name(item):
        if item.kind == "container":
            return item.identity.get("name") or item.key[:12]
        return item.key

    def retained_lines(self, items):
        return [f"  retained: {item.reason if item.kind == 'image' else item.cls} {item.kind} {self.resource_name(item)}"
                for item in items]

    def describe_inventory(self):
        inventory = self.docker_inventory()
        summary = inventory.summary()
        lines = [f"containers {summary['containers']}, networks {summary['networks']}, volumes {summary['volumes']}, "
                 f"image tags {summary['image_tags']} | excluded {summary['excluded']} | blocked {summary['blocked']}"]
        lines += [f"  blocked: {item.cls} {item.kind} {self.resource_name(item)}" for item in inventory.blockers]
        lines += self.retained_lines(inventory.excluded)
        return "\n".join(lines)

    def require_topology_owned(self, command):
        """Ownership preflight: refuse any blocker before Compose could adopt or recreate it (OD-A13-03)."""
        inventory = self.docker_inventory()
        self.write_private_json("inventory-preflight.json", dict(inventory.record(), command=str(command)))
        if inventory.blockers:
            lines = [f"  - {item.cls}: {item.kind} {self.resource_name(item)}" for item in inventory.blockers]
            raise Failure(
                f"resource-not-owned: {command} refused before any change: {len(inventory.blockers)} Compose "
                f"topology resource(s) exist but are not owned by instance {self.context.slug}; Compose could adopt "
                "or recreate them:\n" + "\n".join(lines)
                + "\nLegacy or foreign resources are never adopted automatically (adoption is PF-A2).")
        self._topology_checked = True
        return inventory

    def require_empty_target(self, command):
        """deploy / exact restore-instance: no owned or blocking container, volume or network may exist."""
        inventory = self.docker_inventory()
        self.write_private_json("inventory-preflight.json", dict(inventory.record(), command=str(command)))
        present = [item for item in inventory.blockers + inventory.owned
                   if item.kind in ("container", "volume", "network")]
        if present:
            item = present[0]
            raise Failure(f"resource-target-not-empty: {command} requires an empty target: {item.kind} "
                          f"{self.resource_name(item)} ({item.cls}) already exists for project "
                          f"{self.context.compose_project}. Nothing was changed.")
        self._topology_checked = True
        return inventory

    def plan_for(self, kind, inventory, *, command, recovery_id=None, covered_image_refs=None):
        """A deletion plan of ``kind`` for this operation; blockers refuse before any confirmation."""
        observation = self.verify_daemon()
        try:
            plan = pf_docker.plan_deletion(inventory, kind=kind, operation_id=self.operation_id,
                                           daemon=observation, recovery_id=recovery_id,
                                           covered_image_refs=covered_image_refs)
        except pf_docker.DockerScopeError as exc:
            if exc.code != "resource-blocked":
                raise Failure(str(exc)) from exc
            lines = [f"  - {item.cls}: {item.kind} {self.resource_name(item)}: {item.reason}"
                     for item in inventory.blockers]
            raise Failure(
                f"resource-blocked: {command} refused before any confirmation: {len(inventory.blockers)} resource(s) "
                f"on daemon {observation.engine_id} cannot be proven to belong to instance {self.context.slug}:\n"
                + "\n".join(lines) + f"\nNothing was stopped or deleted. Review them with 'pf status --instance "
                f"{self.context.slug}'. Legacy or foreign resources are never adopted automatically (adoption is "
                "PF-A2).") from exc
        plan["slug"] = self.context.slug
        self._topology_checked = True
        return plan

    def log_plan(self, plan, *, title):
        log(title)
        for item in plan["candidates"]:
            name = (item["identity"].get("name") or item["key"]) if item["kind"] == "container" else item["key"]
            users = f" (users: {len(item['users'])})" if "users" in item else ""
            log(f"  delete {item['kind']} {name}{users}")
        if plan.get("pending_images"):
            log("  image tags pending recovery-bundle coverage: " + ", ".join(plan["pending_images"]))
        for entry in plan["exclusions"]:
            if entry["kind"] == "image" and entry["reason"] == "not-covered":
                log(f"  not covered by the recovery bundle: retained image {entry['key']}")
            else:
                log(f"  retained: {entry['reason'] if entry['kind'] == 'image' else entry['class']} {entry['kind']} "
                    f"{entry['key']}")
        for path in plan["bind_paths"]:
            log(f"  retained bind path (never deleted): {path}")

    def write_deletion_plan(self, plan):
        """Persist the binding plan durably (O_EXCL|O_NOFOLLOW temp, fsync, rename, directory fsync)."""
        expected = {"purge": "bound", "abort-deploy": "none"}.get(plan.get("kind"))
        if self.operation_dir is None or expected is None or plan.get("image_coverage") != expected \
                or plan.get("operation_id") != self.operation_id:
            raise Failure("plan-invalid: only a binding plan of this locked operation can be frozen; nothing was "
                          "deleted.")
        data = pf_docker.plan_bytes(plan)
        path = self.operation_dir / "deletion-plan.json"
        pf_config._write_private(path, data, 0o600)
        return {"operation_id": self.operation_id, "path": str(path), "sha256": pf_instance.sha256_bytes(data)}

    def durable_phase(self, phase, **fields):
        """``phase()`` plus an fsync of the state directory (deletion-plan journal writes)."""
        self.phase(phase, **fields)
        directory = os.open(str(self.state), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def load_deletion_plan(self, journal, *, kind):
        """The frozen plan the journal references: exact path, regular file, hash, instance, kind, operation."""
        def invalid(detail):
            return Failure("plan-invalid: The frozen deletion plan for this journal is missing, not the expected "
                           "regular file, of the wrong kind or operation, or does not match its recorded hash; "
                           "nothing was deleted.\n  detail: " + detail)

        reference = journal.get("deletion_plan")
        if not isinstance(reference, dict) or set(reference) != {"operation_id", "path", "sha256"} \
                or not all(isinstance(value, str) for value in reference.values()):
            raise invalid("journal reference is malformed")
        operation_id = reference["operation_id"]
        if not OPERATION_ID_RE.fullmatch(operation_id):
            raise invalid("operation id is malformed")
        expected = self.context.operations_dir / operation_id / "deletion-plan.json"
        if reference["path"] != str(expected):
            raise invalid("path is not the operation's deletion-plan.json")
        try:
            fd = os.open(str(expected), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise invalid(f"cannot open: {exc.strerror or exc}") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise invalid("not a regular file")
            chunks = []
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                chunks.append(block)
        finally:
            os.close(fd)
        try:
            return pf_docker.load_plan(b"".join(chunks), expected_sha256=reference["sha256"],
                                       instance_id=self.context.instance_id, kind=kind, operation_id=operation_id)
        except pf_docker.DockerScopeError as exc:
            raise invalid("; ".join(finding.message for finding in exc.findings)) from exc

    def plan_drift(self, kind, key, reason, deleted):
        removed = sum(1 for entry in deleted if entry.get("outcome") == "removed")
        return Failure(f"plan-drift: Planned {kind} {key} changed after the plan was frozen ({reason}); deletion "
                       f"stopped. Already removed: {removed}. The journal keeps the frozen plan; inspect with "
                       f"'pf status --instance {self.context.slug}'.")

    def require_plan_engine(self, plan, observation, deleted):
        """The verified daemon must be the engine the frozen plan was built on."""
        if plan["daemon"]["engine_id"] != observation.engine_id:
            raise self.plan_drift("daemon", plan["daemon"]["engine_id"],
                                  "the endpoint answers as engine " + observation.engine_id, deleted)

    def verify_resume_daemon(self, plan, journal):
        """Resume routes: the bound daemon and the plan's engine are verified before the RESUME prompt,
        so a drifted, rootless or unreachable daemon refuses with its daemon-* copy and no confirmation."""
        self.require_plan_engine(plan, self.verify_daemon(), list(journal.get("deleted") or []))

    def execute_deletion_plan(self, plan):
        """Execute exactly the frozen plan: re-observe every item and its users before its effect.

        Never prunes, never ``compose down``, never forces an image removal. The journal's
        ``deleted`` list is persisted durably after each item, so a resume continues the same
        closed plan and never adds a resource.
        """
        journal = load_json(self.pending)
        deleted = list(journal.get("deleted") or [])
        phase = journal.get("phase")
        try:
            observation = self.verify_daemon(refresh=True)
        except DaemonFailure as exc:
            if exc.code != "daemon-drift":
                raise
            raise self.plan_drift("daemon", plan["daemon"]["engine_id"],
                                  "the endpoint now answers as engine " + exc.detail, deleted) from exc
        self.require_plan_engine(plan, observation, deleted)
        done = {(entry.get("kind"), entry.get("key")) for entry in deleted}

        def prove(inventory, items):
            if inventory.blockers:
                item = inventory.blockers[0]
                raise self.plan_drift(item.kind, self.resource_name(item), item.cls, deleted)
            owned = {(entry.kind, entry.key) for entry in inventory.owned}
            for item in items:
                by_id, by_name = inventory.index(item["kind"])
                outcome = pf_docker.compare_identity(item, by_id, by_name)
                if isinstance(outcome, tuple):
                    raise self.plan_drift(item["kind"], item["key"], outcome[1], deleted)
                if outcome == "identical" and (item["kind"], item["key"]) not in owned:
                    # Still present but no longer owned, e.g. an image tag a foreign container
                    # started to use after the freeze (excluded foreign-in-use, not a blocker).
                    observed = next((entry for entry in inventory.excluded
                                     if (entry.kind, entry.key) == (item["kind"], item["key"])), None)
                    detail = f"now {observed.cls}: {observed.reason}" if observed is not None else "no longer owned"
                    raise self.plan_drift(item["kind"], item["key"], detail, deleted)
                if item["kind"] in ("volume", "network"):
                    violations = pf_docker.users_violations(plan, item, inventory.users_of(item["kind"], item["key"]))
                    if violations:
                        raise self.plan_drift(item["kind"], item["key"], violations[0].message, deleted)
            return inventory

        # Pre-effect proof of this run (first execution and resume alike): no effect before it passes.
        # Every later observation is taken immediately after the previous effect, so each item and
        # its users are reinspected (fresh `ps -a` + inspect) right before its own effect.
        inventory = prove(self.docker_inventory(), plan["candidates"])
        for item in plan["candidates"]:
            if (item["kind"], item["key"]) in done:
                continue
            prove(inventory, [item])
            by_id, by_name = inventory.index(item["kind"])
            if pf_docker.compare_identity(item, by_id, by_name) == "absent":
                deleted.append({"kind": item["kind"], "key": item["key"], "outcome": "already-absent"})
            else:
                identity = item["identity"]
                if item["kind"] == "container":
                    self.docker("rm", "-f", identity["id"])
                elif item["kind"] == "network":
                    self.docker("network", "rm", identity["id"])
                elif item["kind"] == "volume":
                    self.docker("volume", "rm", identity["name"])
                else:
                    self.docker("image", "rm", identity["reference"])
                inventory = self.docker_inventory()
                by_id, by_name = inventory.index(item["kind"])
                if pf_docker.compare_identity(item, by_id, by_name) != "absent":
                    raise Failure(f"plan-effect-unconfirmed: {item['kind']} {item['key']} is still present after "
                                  "removal; deletion stopped and the journal keeps the plan.")
                deleted.append({"kind": item["kind"], "key": item["key"], "outcome": "removed"})
            self.durable_phase(phase, deleted=deleted)
        return deleted

    # --------------------------------------------- application configuration (PF-A1.2)

    def check_app_values(self, values):
        """Validate accepted values without changing them (canonical form is required as written)."""
        missing = [key for key in REQUIRED_NAS_ENV_KEYS if not values.get(key)]
        if missing:
            raise Failure("Missing required NAS environment values: " + ", ".join(missing))
        quote_identifier(values["POSTGRES_USER"])
        quote_identifier(values["POSTGRES_DB"])
        if values["POSTGRES_DB"] in ("postgres", "template0", "template1"):
            raise Failure("The application cannot use a PostgreSQL maintenance/template database.")
        if len(values["POSTGRES_PASSWORD"]) < 4:
            raise Failure("POSTGRES_PASSWORD is too short to be handled safely (minimum 4 characters).")
        validate_timezone_name(values["SITE_TIMEZONE"])
        for key, validator in (("PARTFLOW_BIND_IP", validate_ipv4), ("PARTFLOW_HTTP_PORT", validate_http_port),
                               ("PARTFLOW_ALLOWED_HOST", validate_allowed_host)):
            if validator(values[key]) != values[key]:
                raise Failure(f"{key} must be written in its canonical form ({validator(values[key])!r}); "
                              "editable configuration is not rewritten by the controller.")
        problems = pf_config.unsupported_values(values)
        if problems:
            raise Failure("migration-issue: config/.env holds values that cannot be frozen literally: "
                          + "; ".join(f"{key}: {issue}" for key, issue in sorted(problems.items()))
                          + ". Nothing was changed or regenerated; fix the file explicitly.")
        return values

    def load_app_env(self):
        """Strict, read-only load of the editable ``config/.env`` proposal; secrets go to the redactor."""
        path = self.config_dir / ".env"
        if not path.is_file():
            raise Failure("Missing config/.env. Run deploy to create it or restore the host configuration.")
        values = self.check_app_values(read_app_env(path))
        for key in pf_config.SECRET_KEYS:
            self.redactor.add(values[key])
        return values

    def env(self):
        """Application values for this process: the frozen snapshot inside an operation, else the proposal."""
        if self.frozen is not None:
            return dict(self.frozen.values)
        if self.operation_dir is not None:
            raise Failure("This operation has no frozen application configuration; config/.env was absent "
                          "when the operation started and has not been created by the operation itself.")
        if not (self.control_dir / "compose.nas.yaml").is_file():
            raise Failure("Missing installed control/compose.nas.yaml.")
        return self.load_app_env()

    def freeze_app_config(self, *, explicit=False):
        """Render the current ``config/.env`` into the operation's private immutable snapshot.

        Implicitly (from ``lock``) it freezes once; a later implicit call only verifies that
        the editable file still equals the frozen source. ``explicit=True`` is used by the
        operations that create or restore ``.env`` themselves.
        """
        if self.operation_dir is None:
            raise Failure("Application configuration can only be frozen inside a locked operation.")
        path = self.config_dir / ".env"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except OSError as exc:
            raise Failure(f"Cannot read {path}: {exc.strerror or exc}") from exc
        if self.frozen is not None and not explicit:
            if pf_instance.sha256_bytes(data) != self.frozen.source_sha256:
                raise Failure("config/.env changed after this operation froze it; the operation stops and "
                              "must be restarted to approve the new values.")
            return self.frozen
        try:
            values = self.check_app_values(pf_config.parse_app_env(data, label=str(path)))
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        for key in pf_config.SECRET_KEYS:
            self.redactor.add(values[key])
        self._snapshots += 1
        directory = self.operation_dir if self._snapshots == 1 else self.operation_dir / f"refreeze-{self._snapshots}"
        if directory != self.operation_dir:
            os.mkdir(directory, 0o700)
            os.chmod(directory, 0o700)
        try:
            self.frozen = pf_config.freeze_app_config(values, source_bytes=data, operation_id=self.operation_id,
                                                      operation_dir=directory)
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        return self.frozen

    def compose_inputs(self, overrides=None):
        """(values, env_file) one Compose invocation consumes: frozen inside an operation, proposal otherwise."""
        if self.frozen is not None:
            try:
                pf_config.verify_frozen(self.frozen)
            except pf_config.ConfigError as exc:
                raise Failure(str(exc)) from exc
            values, env_file = dict(self.frozen.values), self.frozen.env_file
        else:
            if self.operation_dir is not None:
                raise Failure("This operation has no frozen application configuration; refusing to read the "
                              "editable config/.env mid-operation.")
            # Read-only diagnostics interpolate the current proposal; Compose itself reads no
            # editable file: the registration-created empty env-file replaces <project-directory>/.env.
            values, env_file = self.load_app_env(), self.context.diagnostic_env_path
            if not env_file.is_file():
                raise Failure(f"Registered diagnostics env-file is missing: {env_file}; it is created only by registration.")
        for key, value in (overrides or {}).items():
            pattern = COMPOSE_VALUE_OVERRIDES.get(key)
            if pattern is None or not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise Failure(f"compose-override-refused: Compose value override {key} is not an approved per-call "
                              "override (or its value is not an approved temporary database name); nothing was "
                              "started.")
            values[key] = value
        return values, env_file

    def begin_operation(self, command, *, freeze=True):
        """Inside the instance lock: private operation directory, effect log and frozen configuration.

        PF-A2.2: ``freeze=False`` (the ``config`` route) skips the snapshot: a wizard runs on a .env that may not
        parse completely yet, and it never starts a child that would consume one."""
        if self.operation_dir is not None:
            raise Failure("An operation is already active in this process.")
        name = re.sub(r"[^a-z0-9]+", "-", str(command).lower()).strip("-") or "operation"
        operation_id = f"{utc()}-{name}-{uuid.uuid4().hex[:8]}"
        directory = self.context.operations_dir / operation_id
        os.mkdir(directory, 0o700)
        os.chmod(directory, 0o700)
        self.operation_id, self.operation_dir, self._snapshots, self.frozen = operation_id, directory, 0, None
        self.reset_operation_scope(str(command))
        self.runner.effects_path = directory / "unresolved-effects.json"
        context = self.context
        write_json(directory / "operation.json", {
            "schema_version": 1, "operation_id": operation_id, "command": str(command), "started": utc(),
            "instance_id": context.instance_id, "slug": context.slug, "compose_project": context.compose_project,
            "record_sha256": context.record_sha256, "control_release": context.control.release_id,
            "control_sha256": context.control.sha256, "profile_sha256": context.profile.sha256,
            "policy_revision": context.approved_policy.revision, "policy_sha256": context.approved_policy.sha256,
        })
        if freeze and (self.config_dir / ".env").is_file():
            self.freeze_app_config()

    def reset_operation_scope(self, command=None):
        """Per-operation Docker-scope state (PF-A1.3); the daemon verification is per process."""
        self._operation_command = command
        self._topology_checked = False
        self._approved_envelopes = {}
        self._envelope_sequence = 0
        self.created_image_refs = []

    def end_operation(self):
        self.frozen = None
        self.operation_id = None
        self.operation_dir = None
        self._snapshots = 0
        self.reset_operation_scope()
        if self._runner is not None:
            self._runner.effects_path = None

    def unresolved_effects(self):
        """Every recorded unresolved effect of earlier operations (protected private state, read-only)."""
        records = []
        try:
            names = sorted(os.listdir(self.context.operations_dir))
        except OSError:
            return records
        for name in names:
            path = self.context.operations_dir / name / "unresolved-effects.json"
            try:
                for item in pf_runner.load_unresolved_effects(path):
                    records.append(dict(item, operation_id=name))
            except pf_runner.RunnerError as exc:
                records.append({"operation_id": name, "outcome": "unreadable", "tool": "?", "argv": [],
                                "recorded_at": "?", "error": str(exc)})
        return records

    def log_effects(self):
        records = self.unresolved_effects()
        if not records:
            return
        log(f"UNRESOLVED EFFECTS recorded by earlier operations: {len(records)} (observe before retrying):")
        for item in records[-10:]:
            effect = item.get("effect")
            if isinstance(effect, dict):
                # The descriptor names what to reconcile (kind, verb, targets); argv is the detail.
                summary = " ".join(str(part) for part in (effect.get("kind"), effect.get("verb"),
                                                          effect.get("service"), effect.get("database"),
                                                          *(effect.get("targets") or []),
                                                          effect.get("statement")) if part)
            else:
                summary = " ".join(item.get("argv", []))[:120]
            log(f"  {item.get('recorded_at')} {item.get('operation_id')}: {item.get('tool')} "
                f"{summary} -> {item.get('outcome')}")

    def read_journal(self):
        """Return the pending journal, None, or an unreadable-journal marker. Never creates or repairs it."""
        try:
            data = pf_instance.read_bytes_nofollow(self.pending)
        except FileNotFoundError:
            return None
        except OSError as exc:
            return {"operation": "<unreadable>", "phase": "<unreadable>", "error": str(exc)}
        try:
            journal = pf_instance.parse_strict_json(data, label=str(self.pending))
        except pf_instance.ContextError as exc:
            return {"operation": "<unreadable>", "phase": "<unreadable>", "error": str(exc)}
        if not isinstance(journal, dict):
            return {"operation": "<unreadable>", "phase": "<unreadable>", "error": "journal is not a JSON object"}
        return journal

    def legal_routes(self, journal):
        routes = []
        for command, (predicate, description) in PENDING_ROUTES.items():
            try:
                accepted = bool(predicate(journal))
            except (TypeError, AttributeError):
                accepted = False
            if accepted:
                routes.append(f"pf {command} --instance {self.context.slug}: {description}")
        return routes

    def check_pending_route(self, journal, command):
        route = PENDING_ROUTES.get(command)
        if route is not None and route[0](journal):
            return
        routes = self.legal_routes(journal)
        raise Failure(
            "A previous operation is incomplete (operation="
            + str(journal.get("operation")) + ", phase=" + str(journal.get("phase")) + "). "
            + ("Supported next actions: " + "; ".join(routes) + "." if routes else
               "No automatic route is supported for this journal; review it with status.")
            + " Automation and other mutations remain blocked."
        )

    def log_context(self):
        context = self.context
        log(
            f"Instance: {context.slug} ({context.instance_id}) | project: {context.compose_project}"
            f" | environment: {context.approved_environment} | state: {context.state}"
        )
        log(
            f"Installation root: {context.installation_root} | control release: {context.control.release_id}"
            f" | profile: {context.profile.id}@{context.profile.version} | policy revision: {context.approved_policy.revision}"
        )
        log(f"Workspace: {self.root}")
        log(f"Configuration: {self.config_dir}")

    def log_journal(self, journal, *, trusted=True):
        if journal is None:
            log("No incomplete managed operation.")
            return
        if trusted:
            log("INCOMPLETE OPERATION (protected journal, read before live checks):")
        else:
            log("INCOMPLETE OPERATION (journal read from UNVERIFIED private state; see trust findings; "
                "shown for recovery orientation only, not as protected truth):")
        for key in JOURNAL_PUBLIC_KEYS:
            if key in journal:
                log(f"  {key}: {journal[key]}")
        if "error" in journal:
            log("  error: " + str(journal["error"]))
        hidden = sorted(set(journal) - set(JOURNAL_PUBLIC_KEYS) - {"error"})
        if hidden:
            log("  (private fields not shown: " + ", ".join(hidden) + ")")
        routes = self.legal_routes(journal)
        if routes:
            log("  Next supported action: " + "; ".join(routes))
        else:
            log("  Next supported action: none automatic; review with status, backups and recoveries.")

    def ensure_validation(self):
        if self.validation is None:
            self.validation = pf_instance.validate_context(self.context, running_release=self.running_release)
        return self.validation

    def log_validation(self):
        validation = self.ensure_validation()
        if validation.mutation_allowed:
            log("Protected context: trusted bootstrap, control release, registration and locks verified; mutation allowed")
        else:
            log("Protected context: mutation REFUSED")
        for finding in validation.findings:
            log("  " + finding.render())
        log("ACL state on protected paths: " + validation.acl_state)

    def log_trust_summary(self):
        validation = self.ensure_validation()
        if validation.mutation_allowed:
            log("Protected context: trusted (mutation allowed)")
        else:
            log("Protected context: REFUSED (" + ", ".join(validation.refused_codes()) + "); run doctor for details")
        return validation

    def refuse_live_checks(self, reason):
        """Diagnostics stop before any Git/Docker/Compose command when authority or configuration is refused."""
        log("Live checks skipped: " + reason + ". No Git, Docker or Compose command was issued and nothing was changed.")
        raise Failure("Diagnostics are offline-only: " + reason)

    def require_trusted_context(self, *, load_config=True):
        """Refuse privileged mutation unless the protected context and app configuration validated cleanly.

        Runs before any lock, journal write, transport call or filesystem effect. PF-A2.2: ``load_config=False``
        (the ``config`` route only) skips the final configuration load so the wizard can reach an absent, refused
        or mismatched pf-config.json; the protected-context refusal always runs.
        """
        validation = self.ensure_validation()
        if not validation.mutation_allowed:
            raise Failure(
                "Protected context validation refused mutation:\n" + "\n".join(validation.blocking_messages())
            )
        # A rejected editable configuration blocks the mutation here, not after effects started.
        if load_config:
            self.ensure_config()

    def policy_permits(self, operation_class):
        """Whether the approved protected policy permits ``operation_class`` without an operator (PF-A1.4).

        The policy bytes, hash, schema, revision and environment are re-verified on every call. The A1
        policy schema (``schema_version``, ``revision``, ``environment``) grants no unattended operation
        class, so the answer is always False here; the PF-A4.3 policy mechanism replaces this method.
        """
        try:
            pf_instance.load_policy(self.context)
        except (pf_instance.ContextError, OSError) as exc:
            raise Failure(str(exc)) from exc
        return False

    def publish_backup_permissions(self, root):
        """Make backup artifacts read-only to the configured trusted DSM group.

        The controller runs with a restrictive umask so state and temporary files
        stay private. Backups are the exception: the configured DSM read group needs to inspect
        and copy them over SMB, but must not gain write access to recovery
        artifacts. Directories are 0750 and regular files are 0640.
        """
        root = Path(root)
        if not root.exists():
            return
        if root.is_symlink() or not root.is_dir():
            raise Failure(f"Backup path is not a safe directory: {root}")
        self.refuse_multiply_linked(root)

        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            os.chown(current_path, -1, self.backup_gid)
            os.chmod(current_path, 0o750)

            for name in directories:
                path = current_path / name
                if path.is_symlink():
                    raise Failure(f"Backup tree contains a symbolic link: {path}")

            for name in files:
                path = current_path / name
                if path.is_symlink() or not path.is_file():
                    raise Failure(f"Backup tree contains an unsupported file type: {path}")
                os.chown(path, -1, self.backup_gid)
                os.chmod(path, 0o640)

    def refuse_multiply_linked(self, root):
        """Pre-scan before any mode/owner change: a hard-linked file would change an inode outside the tree."""
        for current, _, files in os.walk(root, followlinks=False):
            for name in files:
                path = Path(current) / name
                info = os.lstat(path)
                if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                    raise Failure(
                        f"Permission change refused: {path} has {info.st_nlink} hard links, so another name "
                        "outside the managed tree would change. Nothing was modified."
                    )

    def publish_config_permissions(self):
        """Allow the configured workspace group to manage host configuration."""
        self.config_dir.mkdir(mode=0o2770, parents=True, exist_ok=True)
        self.refuse_multiply_linked(self.config_dir)
        os.chown(self.config_dir, -1, self.workspace_gid)
        os.chmod(self.config_dir, 0o2770)
        for path in self.config_dir.iterdir():
            if path.is_symlink():
                raise Failure(f"Configuration directory contains a symbolic link: {path}")
            if path.is_file():
                os.chown(path, -1, self.workspace_gid)
                os.chmod(path, 0o660)

    def publish_workspace_permissions(self):
        """Make the repository fully writable to the configured DSM workspace group.

        The executable control plane is outside this tree. Regular source files
        become 0660 (0770 when already executable), directories become 2770 so
        newly created SMB files inherit the shared group.
        """
        if not self.root.is_dir():
            raise Failure("Repository root does not exist: " + str(self.root))
        self.refuse_multiply_linked(self.root)
        for current, directories, files in os.walk(self.root, followlinks=False):
            current_path = Path(current)
            if current_path.is_symlink():
                continue
            os.chown(current_path, -1, self.workspace_gid)
            os.chmod(current_path, 0o2770)
            for name in directories:
                path = current_path / name
                if path.is_symlink():
                    continue
                os.chown(path, -1, self.workspace_gid)
                os.chmod(path, 0o2770)
            for name in files:
                path = current_path / name
                if path.is_symlink() or not path.is_file():
                    continue
                executable = bool(path.stat().st_mode & 0o111)
                os.chown(path, -1, self.workspace_gid)
                os.chmod(path, 0o770 if executable else 0o660)
        # PF-A1.2: privileged Git never runs against the writable checkout, so the former
        # ``git config core.sharedRepository group`` call is gone; ``.git`` metadata is
        # editor data and is only chmod'ed like every other workspace file above.

    def validate_deploy_env(self, values, *, require_strong_password=False):
        missing = [key for key in REQUIRED_NAS_ENV_KEYS if not values.get(key)]
        if missing:
            raise Failure("Missing required NAS environment values: " + ", ".join(missing))
        quote_identifier(values["POSTGRES_USER"])
        quote_identifier(values["POSTGRES_DB"])
        if values["POSTGRES_DB"] in ("postgres", "template0", "template1"):
            raise Failure("The application cannot use a PostgreSQL maintenance/template database.")
        if require_strong_password and len(values["POSTGRES_PASSWORD"]) < 32:
            raise Failure("Existing POSTGRES_PASSWORD is too short for a new deployment; move .env aside and let deploy generate one.")
        validate_timezone_name(values["SITE_TIMEZONE"])
        validate_ipv4(values["PARTFLOW_BIND_IP"])
        values["PARTFLOW_HTTP_PORT"] = validate_http_port(values["PARTFLOW_HTTP_PORT"])
        values["PARTFLOW_ALLOWED_HOST"] = validate_allowed_host(values["PARTFLOW_ALLOWED_HOST"])
        return values

    def detect_lan_ipv4(self):
        addresses = []
        commands = (["ip", "-4", "-o", "addr", "show", "scope", "global"], ["hostname", "-I"])
        for command in commands:
            try:
                output = self.command(command, effect=None)
            except Failure:
                continue
            candidates = re.findall(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])", output)
            for value in candidates:
                try:
                    address = validate_ipv4(value, allow_loopback=False)
                except Failure:
                    continue
                if address not in addresses:
                    addresses.append(address)
            if addresses:
                break
        return addresses

    def choose_lan_ipv4(self):
        addresses = self.detect_lan_ipv4()
        if addresses:
            log("Detected NAS LAN IPv4 addresses:")
            for number, address in enumerate(addresses, 1):
                log(f"  {number}. {address}")
        default = "1" if len(addresses) == 1 else None

        def validate(answer):
            if answer.isdigit() and addresses and 1 <= int(answer) <= len(addresses):
                return addresses[int(answer) - 1]
            return validate_ipv4(answer, allow_loopback=False)

        # PF-A2.2: a wizard answer (q, end of input and Ctrl-C cancel with config-cancelled).
        return self.ask("PARTFLOW_BIND_IP", "NAS LAN IPv4 (enter an address or detected number)", default=default,
                        validate=validate)

    def environment_summary(self, values):
        if values["PARTFLOW_BIND_IP"] == "127.0.0.1":
            access = "DSM Reverse Proxy / localhost listener"
            endpoint = f"http://127.0.0.1:{values['PARTFLOW_HTTP_PORT']} (proxy hostname: {values['PARTFLOW_ALLOWED_HOST']})"
        else:
            access = "Direct LAN"
            endpoint = f"http://{values['PARTFLOW_BIND_IP']}:{values['PARTFLOW_HTTP_PORT']}"
        return (
            f"Database: {values['POSTGRES_DB']} | user: {values['POSTGRES_USER']}\n"
            f"Factory timezone: {values['SITE_TIMEZONE']}\n"
            f"Access: {access}\n"
            f"Endpoint: {endpoint}"
        )

    def prepare_new_env(self):
        env_path = self.config_dir / ".env"

        if env_path.exists():
            # An existing file is accepted only as written (strict parse, literal values) or
            # refused with an explicit issue; its password is never displayed, changed or regenerated.
            # Inside the operation the frozen snapshot is the only source of these values.
            current = dict(self.frozen.values) if self.frozen is not None else self.load_app_env()
            values = self.validate_deploy_env(current, require_strong_password=True)
            log("Existing .env found; POSTGRES_PASSWORD will not be displayed or changed.")
            log(self.environment_summary(values))
            if not prompt_yes_no("Reuse this existing .env for the new deployment", default=True):
                raise Failure("Existing .env was left unchanged. Move or edit it explicitly, then rerun deploy.")
            os.chown(env_path, -1, self.workspace_gid)
            os.chmod(env_path, 0o660)
            self.freeze_app_config()
            return values

        # PF-A2.2: the missing-.env branch is the app-variable wizard (the record's profile declaration).
        values = self.app_wizard(inside_deploy=True)
        log("Created " + str(env_path) + " with group-write access for " + self.config["workspace_write_group"] + ". It remains outside the repository.")
        # The operation consumes the file it just wrote, frozen once, never the editable copy later.
        self.freeze_app_config(explicit=True)
        return values

    # ------------------------------------------------------------ config wizards (PF-A2.2)

    def configure(self, args):
        """`pf config admin|app` (registered mode), inside the instance lock; never a Git, Docker or Compose child."""
        if args.config_verb == "admin":
            self.admin_wizard()
            return
        # The workspace group of a created .env comes from a valid admin configuration (the A1 load).
        try:
            self.ensure_config()
        except Failure as exc:
            raise Failure(f"admin-config-required: {str(exc).splitlines()[0]} Run '{self.pf_command()} config admin' "
                          "first; nothing was changed.") from exc
        self.app_wizard()

    def ask(self, stage, question, *, default=None, validate=None, choices=None):
        """One wizard answer: Enter takes ``default``; ``q``, end of input and Ctrl-C cancel (config-cancelled)."""
        if choices is not None:
            allowed = tuple(choices)

            def validate_choice(answer, inner=validate):
                if answer not in allowed:
                    raise Failure("Choose one of " + ", ".join(allowed) + ".")
                return inner(answer) if inner is not None else answer

            return ask_answer(stage, question, default=default, validate=validate_choice)
        return ask_answer(stage, question, default=default, validate=validate)

    def refuse_admin_mismatch(self, path, values):
        context = self.context
        if values["project"] != context.compose_project:
            raise Failure(f"admin-config-mismatch: {path} names project {values['project']!r}, but {context.slug} is "
                          f"registered with project {context.compose_project!r}; the registration is authoritative and "
                          "nothing was changed.")
        if values["environment"] != context.approved_environment:
            environment = context.approved_environment
            raise Failure(f"admin-config-mismatch: {path} names environment {values['environment']!r}; the approved "
                          f"policy of {context.slug} is {environment} revision {context.approved_policy.revision}. An "
                          "editable label never changes the approved policy, and nothing was changed. Restore "
                          f'"environment": "{environment}"; a policy change is a separate approval (PF-A4.3).')

    def admin_wizard(self):
        """Section 3.3 registered mode: create, migrate (schema 1 -> 2) or complete pf-config.json. It reads the file
        itself and never calls ensure_config/load_app_config, so an absent, refused or mismatched file and a missing
        group each reach their own outcome."""
        context = self.context
        path = self.config_dir / "pf-config.json"
        pf_command = self.pf_command()
        target = inspect_editable_target(path)
        before = None
        if not target.present:
            mode = "create"
            values = load_admin_example(self.control_dir / "pf-config.example.json", pf_command)
            values.update(project=context.compose_project, environment=context.approved_environment)
        else:
            before = pf_config.parse_admin_config(target.data, label=str(path))
            if before.problems:
                refuse_admin_config(path, before, rerun=f"{pf_command} config admin",
                                    prefix=pf_install.launcher_prefix(context.installation_root),
                                    release_id=context.control.release_id)
            values = dict(before.values)
            self.refuse_admin_mismatch(path, values)
            mode = "migrate" if before.schema_version == 1 else "complete"
            if mode == "migrate":
                try:
                    pf_config.migrate_admin_config(before)
                except pf_config.ConfigError as exc:
                    raise Failure(f"admin-config-migration-blocked: {path} (legacy schema 1) cannot become schema 2: "
                                  f"{str(exc).split(': ', 1)[1]}. Nothing was changed; correct the value by hand, then "
                                  f"run '{pf_command} config admin' again.") from exc
        locations = {"workspace_write_group": f"{context.paths.workspace}, {context.paths.configuration}",
                     "backup_read_group": f"{context.paths.backups}, {context.paths.recovery}"}
        with cancelled_before_summary(path):
            asked = admin_group_questions(values, mode=mode, locations=locations)
        if mode == "complete" and not asked:
            remove_editable_leftovers(target)
            log(f"config-current: {path} is current (admin configuration schema 2); nothing to change.")
            return
        document = pf_config.admin_document(values)
        try:
            data = pf_config.render_admin_config(document)
        except pf_config.ConfigError as exc:
            raise Failure(f"config-changed: {path}: {exc}; nothing was written.") from exc
        rows = pf_config.admin_config_changes(before, document, asked=asked)
        admin_summary(
            path, mode=mode, before=before, document=document, asked=asked,
            instance_line=(f"Instance: {context.slug} ({context.instance_id}), project {context.compose_project}, "
                           f"profile {context.profile.id} {context.profile.version}"),
            environment_line=(f"Environment label: {values['environment']} (approved policy "
                              f"{context.approved_environment} revision {context.approved_policy.revision}; unchanged)"),
            app_hint=f"Application variables are not in this file; use '{pf_command} config app'.")
        record = {"schema_version": 1, "operation_id": self.operation_id, "completed": utc(), "file": "pf-config.json",
                  "mode": mode, "profile_id": None, "schema_before": None if before is None else before.schema_version,
                  "schema_after": pf_config.ADMIN_CONFIG_SCHEMA_VERSION,
                  "before_sha256": pf_instance.sha256_bytes(target.data) if target.present else None,
                  "after_sha256": pf_instance.sha256_bytes(data), "changes": rows}
        self.check_config_change(record)
        confirm_write(path)
        changed = [row["key"] for row in rows if row["action"] != "kept"]
        write_reviewed(path, data, target, create_gid=grp.getgrnam(values["workspace_write_group"]).gr_gid,
                       op8=self.operation_id[-8:], keys=changed)
        record["completed"] = utc()
        self.record_config_change(record, path, data)
        log(f"Wrote {path} (admin configuration schema 2; {mode}).")
        if before is not None and before.implicit:
            log("implicit-materialized: legacy implicit values are now explicit: " + ", ".join(before.implicit) + ".")

    def deployed_evidence(self):
        """The section 3.4 deployed predicate (read-only, fail closed): None when never deployed, else the evidence."""
        try:
            os.lstat(str(self.state / "deployed.json"))
            return "state/deployed.json"
        except FileNotFoundError:
            pass
        except OSError:
            return "unreadable state"
        root = self.context.installation_root
        for directory in (self.state, root / pf_install.OPERATIONS_RELATIVE):
            try:
                os.listdir(str(directory))
            except FileNotFoundError:
                pass
            except OSError:
                return "unreadable state"
        for item in pf_install.operations(root):
            if item["phase"] == "unreadable":
                return "unreadable state"
            plan = item["plan"]
            if item["kind"] == "migrate-legacy" and item["phase"] == "completed" and plan["instance"] is not None \
                    and plan["instance"]["instance_id"] == self.context.instance_id:
                return "a completed v2.5 migration"
        return None

    def instance_deployed(self):
        """Whether credentials may never be generated, asked or rewritten for this instance (section 3.4)."""
        return self.deployed_evidence() is not None

    def check_config_change(self, record):
        """Validate a config-change record before the writer runs (a violation is a programming error)."""
        errors = pf_install.validate_marked(record, pf_config.CONFIG_CHANGE_SCHEMA["$defs"]["record"],
                                           defs=pf_config.CONFIG_CHANGE_SCHEMA["$defs"])
        errors += pf_config.config_change_problems(record)
        if errors:
            raise Failure(f"config-audit-invalid: Internal error: the change record for "
                          f"{self.config_dir / str(record.get('file'))} failed its schema ({errors[0]}); nothing was "
                          "written.")

    def write_config_change(self, record):
        """The private audit record of one completed wizard write (section 3.10); no secret, no .env hash."""
        self.check_config_change(record)
        self.write_private_json(pf_config.CONFIG_CHANGE_NAME, record)

    def record_config_change(self, record, path, data):
        """Step 5 after the editable file was published: write the audit record. An interrupt or I/O error here is
        reported from an observation of the target (section 3.9), never as if nothing had been written; the record
        itself stays best effort (section 3.10)."""
        try:
            self.write_config_change(record)
        except (KeyboardInterrupt, OSError) as exc:
            try:
                observed = pf_install.read_regular_file(path)
            except OSError:
                observed = None
            state = "holds the new content" if observed == data \
                else "was written, but it now holds other bytes or cannot be read"
            reason = (exc.strerror or str(exc)) if isinstance(exc, OSError) else (str(exc) or "interrupted")
            raise Failure(f"config-audit-not-recorded: {path} {state}; its change record "
                          f"{self.operation_dir / pf_config.CONFIG_CHANGE_NAME} was not recorded ({reason}). Run the "
                          "command again to review the file.") from exc

    def app_note(self, item, current):
        pf_command = self.pf_command()
        name = current.get(item.key)
        if item.code == "zone-unknown-on-host":
            return (f"zone-unknown-on-host: SITE_TIMEZONE {name} kept; it is not in this host's zone data ({item.reason})."
                    " The backend checks it with its own zone data at startup; change it by hand only if the factory "
                    "calendar zone is really wrong.")
        if item.code == "zone-data-unavailable":
            return (f"zone-data-unavailable: SITE_TIMEZONE {name} kept; host zone data is unavailable (searched "
                    f"{item.reason}); the backend checks it at startup.")
        return (f"password-weak-for-new-deployment: POSTGRES_PASSWORD is kept unchanged, but it is shorter than 32 "
                f"characters and the first '{pf_command} deploy' will refuse it. Either set a value of at least 32 "
                f"characters by hand, or leave the line as 'POSTGRES_PASSWORD=' (empty) and run '{pf_command} config "
                "app' again to generate one.")

    def app_refusal(self, item, path, evidence):
        if item.code == "migration-issue":
            return Failure(f"migration-issue: config/.env holds values that cannot be frozen literally: {item.key}: "
                           f"{item.reason}. Nothing was changed or regenerated; fix the file explicitly.")
        if item.code == "zone-data-unavailable":
            return Failure(f"zone-data-unavailable: No IANA zone data was found on this host (searched {item.reason}); "
                           "SITE_TIMEZONE cannot be verified, so nothing was changed. Install the host's zone data or "
                           f"write SITE_TIMEZONE in {path} by hand.")
        return Failure(f"app-credential-unusable: {path} has no usable {item.key} ({item.reason}) and "
                       f"{self.context.slug} is treated as deployed ({evidence}; the evidence is state/deployed.json, a "
                       "completed v2.5 migration, or unreadable state); a new value would not match the initialized "
                       f"database, so nothing was generated or changed. Restore {item.key} from the instance's recovery "
                       "bundle or your records, then run again.")

    def app_answers(self, plan, declaration, current):
        """Ask every planned ``ask`` item in declaration order, plus a kept ``localhost`` hostname when the proxy
        binding is chosen here; returns ({key: value}, {derived keys}). A key absent from the answers stays kept."""
        variables = {variable.key: variable for variable in declaration.variables}
        answers, derived = {}, set()

        def checked(kind):
            def validate(value):
                problem = app_value_problem(kind, value)
                if problem is not None:
                    raise Failure(problem)
                return value
            return validate

        def zone_checked(value):
            validate_timezone_name(value)
            status, detail = pf_config.zone_status(value)
            if status != "ok":
                raise Failure(f"SITE_TIMEZONE {value!r} is not in the host zone data ({detail}); enter a zone such as "
                              "America/Los_Angeles.")
            return value

        for item in plan:
            variable = variables[item.key]
            # A kept `localhost` cannot serve a DSM Reverse Proxy: when the access mode is asked in this run and the
            # proxy binding is chosen, that hostname is uncertain and asked like a missing one (no localhost default).
            proxy_host = variable.kind == "host" and item.action == "kept" \
                and answers.get("PARTFLOW_BIND_IP") == "127.0.0.1" and current.get(item.key) == "localhost"
            if item.action != "ask" and not proxy_host:
                continue
            if variable.kind == "access":
                log("Access mode:")
                log("  1. Direct LAN access to the NAS IP (recommended for initial staging verification)")
                log("  2. DSM Reverse Proxy; bind PartFlow to 127.0.0.1")
                mode = self.ask(item.key, variable.question, default="1", validate=validate_access_mode)
                answers[item.key] = self.choose_lan_ipv4() if mode == "1" else "127.0.0.1"
            elif variable.kind == "host":
                if answers.get("PARTFLOW_BIND_IP", current.get("PARTFLOW_BIND_IP")) != "127.0.0.1":
                    answers[item.key] = "localhost"
                    derived.add(item.key)
                else:
                    default = item.default if item.default not in (None, "localhost") else None
                    answer = self.ask(item.key, variable.question, default=default, validate=validate_allowed_host)
                    if not (proxy_host and answer == current.get(item.key)):
                        answers[item.key] = answer
            elif variable.kind == "timezone":
                answers[item.key] = self.ask(item.key, variable.question, default=item.default, validate=zone_checked)
            elif variable.kind == "port":
                answers[item.key] = self.ask(item.key, variable.question, default=item.default,
                                             validate=validate_http_port)
            else:
                answers[item.key] = self.ask(item.key, variable.question, default=item.default,
                                             validate=checked(variable.kind))
        return answers, derived

    def app_wizard(self, *, inside_deploy=False):
        """Section 3.4: create or complete ``<configuration>/.env`` for the record's profile declaration. Existing
        secrets are preserved byte for byte; a secret is generated only when absent on a never-deployed instance;
        credentials are never rewritten once deployed. Returns the resulting values (never printed)."""
        context = self.context
        pf_command = self.pf_command()
        try:
            declaration = pf_config.app_declaration(context.profile.id)
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        keys = declaration.keys
        path = self.config_dir / ".env"
        target = inspect_editable_target(path)
        example_path = self.control_dir / declaration.example_name

        def example_invalid(detail):
            return Failure(f"app-example-invalid: The installed example {example_path} does not declare every "
                           f"{declaration.profile_id} variable exactly once ({detail}); nothing was changed. Run "
                           f"'{pf_command} doctor' and reinstall the control release.")

        try:
            example_bytes = pf_install.read_regular_file(example_path)
            example = pf_config.parse_app_env(example_bytes, label=str(example_path), allowed_keys=keys,
                                              require_all=False)
        except OSError as exc:
            raise example_invalid(str(exc.strerror or exc)) from exc
        except pf_config.ConfigError as exc:
            raise example_invalid(str(exc)) from exc
        current = None
        if target.present:
            try:
                current = pf_config.parse_app_env(target.data, label=str(path), allowed_keys=keys, require_all=False)
            except pf_config.ConfigError as exc:
                raise Failure(f"app-config-invalid: {path}: {exc}. Nothing was changed; fix the line by hand, then "
                              f"run '{pf_command} config app' again.") from exc
            for key in pf_config.SECRET_KEYS:
                if current.get(key):
                    self.redactor.add(current[key])
        evidence = self.deployed_evidence()
        plan = pf_config.plan_app_config(declaration, current=current, example=example, deployed=evidence is not None,
                                         check=app_value_problem, zone=pf_config.zone_status,
                                         canonical=app_canonical_value)
        for item in plan:
            if item.action == "refuse":
                raise self.app_refusal(item, path, evidence)
        notes = [self.app_note(item, current or {}) for item in plan if item.action == "kept" and item.code]
        if all(item.action == "kept" for item in plan):
            remove_editable_leftovers(target)
            log(f"config-current: {path} has every value the {declaration.profile_id} profile declares; nothing to "
                "change.")
            for note in notes:
                log(note)
            return dict(current)
        if inside_deploy:
            log("Configure the new PartFlow staging environment. Press Enter to accept a shown default.")
        with cancelled_before_summary(path):
            answers, derived = self.app_answers(plan, declaration, current or {})
        generated = False
        final = dict(current or {})
        set_values, append = {}, {}
        for item in plan:
            if item.key in answers:
                value = answers[item.key]
            elif item.action == "generate":
                value = secrets.token_hex(declaration.generated_secret_hex_bytes)
                self.redactor.add(value)
                generated = True
            else:
                continue
            final[item.key] = value
            if current is not None and item.key in current:
                set_values[item.key] = value
            elif current is not None:
                append[item.key] = value
        if current is None:
            base, set_values = example_bytes, {key: final[key] for key in keys}
        else:
            base = target.data
        try:
            data = pf_config.rewrite_app_env(base, keys=keys, set_values=set_values, append=append)
        except pf_config.ConfigError as exc:
            if current is None:
                raise example_invalid(str(exc)) from exc
            raise Failure(f"app-config-invalid: {path}: {exc}. Nothing was changed; fix the line by hand, then run "
                          f"'{pf_command} config app' again.") from exc
        if inside_deploy:
            self.validate_deploy_env(dict(final), require_strong_password=True)
        mode = "complete" if target.present else "create"
        by_key = {item.key: item for item in plan}
        log(f"Application variables {path} (profile {declaration.profile_id}; {mode})")
        rows = []
        for variable in declaration.variables:
            key, item = variable.key, by_key[variable.key]
            if variable.secret:
                word = "set" if item.action == "generate" else "unchanged"
                extra = f"; generated, {2 * declaration.generated_secret_hex_bytes} hexadecimal characters" \
                    if item.action == "generate" else ""
                log(f"  {key}: {word} (not shown{extra})")
                rows.append({"key": key, "action": word, "before": None, "after": None})
                continue
            new, old = final[key], (current or {}).get(key)
            if item.action == "kept" and key not in answers:
                text, action = f"{new} (kept", "kept"
            elif key in derived:
                text = f"{old if old else '(missing)'} -> {new} (derived from access mode"
                action = "changed" if current is not None and key in current else "added"
            elif current is not None and key in current:
                text, action = f"{old if old else '(empty)'} -> {new} (changed: asked", "changed"
            elif current is not None:
                text, action = f"(missing) -> {new} (asked", "added"
            else:
                text, action = f"{new} (asked", "added"
            if variable.kind == "timezone":
                status = pf_config.zone_status(new)[0]
                text += "; " + {"ok": "present in host zone data",
                                "zone-data-unavailable": "host zone data unavailable"}.get(status,
                                                                                          "not in host zone data")
            log(f"  {key}: {text})")
            rows.append({"key": key, "action": action, "before": old if action in ("kept", "changed") else None,
                         "after": new})
        for note in notes:
            log("Notes: " + note)
        credentials = [variable.key for variable in declaration.variables if variable.credential]
        if credentials:
            log("Database connection: PARTFLOW_DATABASE_URL is generated with percent-encoded credentials; nothing is "
                "spliced raw.")
            log("Credentials (" + ", ".join(credentials) + ") are never changed by this wizard after the first "
                "deployment.")
        record = None
        if not inside_deploy:
            record = {"schema_version": 1, "operation_id": self.operation_id, "completed": utc(), "file": ".env",
                      "mode": mode, "profile_id": declaration.profile_id, "schema_before": None, "schema_after": None,
                      "before_sha256": None, "after_sha256": None, "changes": rows}
            self.check_config_change(record)
            confirm_write(path, secret=generated)
        elif not prompt_yes_no("Write .env with these settings and continue", default=True):
            raise Failure("Cancelled before .env was created.")
        changed = [row["key"] for row in rows if row["action"] != "kept" and row["action"] != "unchanged"]
        write_reviewed(path, data, target, create_gid=self.workspace_gid, op8=self.operation_id[-8:], keys=changed,
                       secret=generated)
        if record is not None:
            record["completed"] = utc()
            self.record_config_change(record, path, data)
            log(f"Wrote {path} (profile {declaration.profile_id}; {mode}).")
        return final

    def ensure_listener_available(self, values):
        address = values["PARTFLOW_BIND_IP"]
        port = int(values["PARTFLOW_HTTP_PORT"])
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind((address, port))
        except OSError as exc:
            raise Failure(
                f"Cannot bind {address}:{port}; choose another port/address or stop the conflicting service before deploy."
            ) from exc

    def assert_new_deployment(self):
        if (self.state / "deployed.json").exists():
            raise Failure("This project already has a managed deployment record. Use update, not deploy.")
        # PF-A1.3: the exact inventory replaces the former project-prefix scan; any owned or
        # blocking container, volume or network refuses (adoption is never automatic).
        self.require_empty_target("deploy")

    # ------------------------------------------- source store and manifest (PF-A1.2)

    def approved_remote(self):
        """The one approved source remote: the configured GitHub repository over HTTPS."""
        if self.remote_override is not None:
            return self.remote_override
        return GITHUB_HTTPS + self.config["repository"] + ".git"

    def store_git(self, argv, *, cwd, stdin=None, stdout=None, timeout=None):
        """Git for the protected store only; every call carries ``--git-dir`` and goes through the runner."""
        return self.command(["git", *argv], cwd=cwd, input_file=stdin, output=stdout,
                            timeout=timeout or TIMEOUT_GIT_FETCH, effect=git_effect(argv))

    def source_store(self):
        return pf_source.SourceStore(self.context.installation_root / pf_instance.SOURCES_RELATIVE,
                                     self.approved_remote(), self.store_git, protocols=self.source_protocols)

    @contextlib.contextmanager
    def store_lock(self, store):
        """Exclusive lock for store creation/fetch; the lock inode is created once and never removed."""
        try:
            handle = pf_instance.acquire_source_lock(store.lock_path)
        except pf_instance.ContextError as exc:
            raise Failure(str(exc)) from exc
        try:
            yield handle
        finally:
            handle.release()

    def materialize_source(self, target, destination):
        """Fetch ``target['sha']`` into the protected store and export its tree to a private candidate.

        Replaces the former clone into the workspace parent: the writable
        checkout is never used as a remote, alternate or object source, and the
        exported tree carries no ``.git`` metadata, links, submodules or LFS pointers.
        """
        if self.operation_dir is None:
            raise Failure("Source materialization requires a locked operation.")
        sha = target["sha"]
        if not SHA_RE.fullmatch(sha):
            raise Failure("A full commit SHA is required to materialize a source tree.")
        store = self.source_store()
        try:
            with self.store_lock(store):
                store.ensure()
                store.fetch_commit(sha, timeout=TIMEOUT_GIT_FETCH)
            store.export(sha, destination, timeout=TIMEOUT_GIT_FETCH)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc
        self.compose("config", "-q", root=destination)

    def load_source_manifest(self):
        """The protected manifest of the last deployed tree, or None (no provenance recorded)."""
        try:
            return pf_source.load_manifest(self.context.source_manifest_path, pf_instance.parse_strict_json)
        except (pf_source.SourceError, pf_instance.ContextError) as exc:
            raise Failure(f"Protected source manifest is unusable: {exc}") from exc

    @staticmethod
    def refuse_reserved_candidate_paths(candidate, *, allow=()):
        """A candidate tree is deployed whole; a file the manifest policy would ignore must not be in it.

        ``allow`` names top-level entries a caller consumes and removes before deployment
        (the runtime ``.env`` a format-1 recovery bundle stored inside its source archive).
        """
        reserved = [path for path in pf_source.reserved_paths(candidate, excludes=SOURCE_EXCLUDES) if path not in allow]
        if reserved:
            raise Failure("Candidate source carries files the workspace manifest cannot verify (reserved "
                          "artifact names): " + ", ".join(reserved[:10]) + ". Nothing was replaced.")

    def record_source_manifest(self, tree, revision, *, verified):
        """Write the manifest of the tree that was deployed (built from the private candidate)."""
        source = {"kind": "git_commit", "commit": revision, "remote": self.approved_remote()} if verified \
            else {"kind": "unknown"}
        try:
            manifest = pf_source.build_manifest(tree, source=source, excludes=SOURCE_EXCLUDES)
            digest_value = pf_source.write_manifest(self.context.source_manifest_path, manifest)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc
        return digest_value

    def workspace_status(self, root=None):
        """fd-safe comparison of the workspace with the protected manifest; never runs Git there.

        ``head`` is the commit the protected manifest records (or None), ``dirty`` is
        True when the workspace differs from that manifest or no manifest exists, and
        ``provenance`` is ``git_commit`` only for a matching, commit-backed manifest.
        """
        root = Path(root or self.root)
        manifest = self.load_source_manifest()
        if manifest is None:
            return {"head": None, "dirty": True, "changes": ["<no-protected-manifest>"], "provenance": "unknown",
                    "manifest_commit": None}
        try:
            report = pf_source.compare_manifest(root, manifest, excludes=SOURCE_EXCLUDES)
        except (pf_source.SourceError, OSError) as exc:
            raise Failure(f"Workspace comparison failed: {exc}") from exc
        source = manifest["source"]
        commit = source.get("commit") if source["kind"] == "git_commit" else None
        matches = report["matches"]
        return {
            "head": commit,
            "dirty": not matches,
            "changes": pf_source.change_summary(report),
            "provenance": "git_commit" if (matches and commit) else "unknown",
            "manifest_commit": commit,
        }

    def current_target(self, destination):
        """``deploy --current``: prove the workspace equals a commit of the approved remote.

        The workspace's own ``.git/HEAD`` is read as data only, as a *hint* of which
        commit to fetch into the protected store. The commit is then exported to the
        private candidate ``destination`` and the workspace must equal that tree byte
        for byte; otherwise the provenance stays unknown and no SHA is assigned.
        """
        status = self.workspace_status()
        hint = status["head"] if status["provenance"] == "git_commit" else pf_source.workspace_head_hint(self.root)
        if hint is None:
            raise Failure(
                "deploy --current: the workspace has unknown provenance (no protected manifest and no readable "
                "commit hint). Deploy an explicit source with --latest, --commit or --release; a ZIP or an "
                "unverified checkout is never assigned a commit SHA."
            )
        self.materialize_source({"sha": hint}, destination)
        try:
            candidate = pf_source.build_manifest(destination, source={"kind": "git_commit", "commit": hint,
                                                                       "remote": self.approved_remote()},
                                                 excludes=SOURCE_EXCLUDES)
            report = pf_source.compare_manifest(self.root, candidate, excludes=SOURCE_EXCLUDES)
        except (pf_source.SourceError, OSError) as exc:
            raise Failure(f"Workspace comparison failed: {exc}") from exc
        if not report["matches"]:
            raise Failure(
                f"deploy --current: the workspace differs from commit {hint} of the approved remote, so its "
                "provenance is unknown. Commit and push the changes, then deploy that commit explicitly, or "
                "restore the workspace. Differences: " + ", ".join(pf_source.change_summary(report))
            )
        return {"sha": hint, "ref": "current-checkout", "release_id": None, "published_at": None, "prerelease": None}

    def create_deployed_source_archive(self, destination, revision):
        """Archive the exact deployed revision from the protected store, or from a workspace proven equal to it."""
        destination = Path(destination)
        store = self.source_store()
        store_has_commit = False
        if store.exists():
            try:
                store.verify()
                store_has_commit = store.has_commit(revision)
            except pf_source.SourceError as exc:
                raise Failure(str(exc)) from exc
        if store_has_commit:
            with tempfile.TemporaryDirectory(prefix="deployed-source-", dir=self.state) as folder:
                tree = Path(folder) / "tree"
                try:
                    store.export(revision, tree, timeout=TIMEOUT_GIT_FETCH)
                except pf_source.SourceError as exc:
                    raise Failure(str(exc)) from exc
                create_source_archive(tree, destination)
            return
        manifest = self.load_source_manifest()
        if manifest is not None and manifest["source"]["kind"] == "git_commit" \
                and manifest["source"]["commit"] == revision:
            # The archive is built from the workspace bytes while each file is proven equal to
            # the manifest (one read per file): no separate compare-then-copy window.
            try:
                pf_source.archive_verified_tree(self.root, manifest, destination, excludes=SOURCE_EXCLUDES)
            except pf_source.SourceError as exc:
                raise Failure("Cannot archive the deployed source from the workspace: " + str(exc)) from exc
            return
        raise Failure(
            "Cannot reconstruct the exact deployed source revision: it is neither in the protected source store "
            "nor proven equal to the workspace by the protected manifest. No destructive operation will continue "
            "until the deployed source can be archived."
        )

    def prove_tree_commit(self, tree, revision):
        """True only when the protected store holds ``revision`` and its exported tree equals ``tree`` byte/mode.

        Checkpoint or bundle metadata alone never assigns a commit to a tree; without the
        store's proof the tree is recorded with unknown provenance.
        """
        if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
            return False
        store = self.source_store()
        if not store.exists():
            return False
        try:
            store.verify()
            if not store.has_commit(revision):
                return False
            with tempfile.TemporaryDirectory(prefix="prove-source-", dir=self.state) as folder:
                exported = Path(folder) / "tree"
                store.export(revision, exported, timeout=TIMEOUT_GIT_FETCH)
                manifest = pf_source.build_manifest(
                    exported, source={"kind": "git_commit", "commit": revision, "remote": self.approved_remote()},
                    excludes=SOURCE_EXCLUDES)
                return bool(pf_source.compare_manifest(tree, manifest, excludes=SOURCE_EXCLUDES)["matches"])
        except (pf_source.SourceError, OSError) as exc:
            raise Failure("Source provenance check failed: " + str(exc)) from exc

    def compose(self, *args, root=None, override=None, timeout=None, env=None, **kwargs):
        """One Compose invocation with frozen inputs: fixed project, files, env-file and directory.

        ``env`` carries only approved per-call value overrides (COMPOSE_VALUE_OVERRIDES), refused
        before any process starts. A mutating verb in ``pf_docker.ENVELOPE_VERBS`` first passes the
        Compose envelope for exactly these effective inputs (PF-A1.3).
        """
        root = Path(root or self.root)
        values, env_file = self.compose_inputs(env)
        if "effect" not in kwargs:
            # Production wiring of the unresolved-effect journal: every mutating Compose verb
            # (and every exec that is not a known read-only program) carries a descriptor,
            # built from the caller's arguments before the managed run label is added.
            kwargs["effect"] = compose_effect(self.config["project"], args)
        verb = str(args[0]) if args else ""
        if verb == "run":
            args = ("run", "--label", "partflow.admin.project=" + self.config["project"], *args[1:])
        cli = self.compose_cli()
        compose_file = self.control_dir / "compose.nas.yaml"
        if not compose_file.is_file():
            raise Failure("Missing installed control/compose.nas.yaml.")
        selected = Path(override) if override else self.override
        selected = selected if selected.exists() else None
        command = self.compose_prefix(cli, root, env_file, selected)
        # Application values reach Compose only as allowlisted child variables derived from
        # the frozen snapshot (plus the core-generated repository root, encoded database URL
        # and instance ID); no editable file is read by Compose and no host variable is inherited.
        child = pf_config.child_values(values, workspace=root, instance_id=self.context.instance_id)
        if verb in pf_docker.ENVELOPE_VERBS:
            self.require_envelope(root, selected, child, value_overrides=sorted(env or {}))
        if timeout is None:
            data_programs = ("pg_dump", "pg_dumpall", "pg_restore", "createdb", "dropdb")
            if verb == "build":
                timeout = TIMEOUT_BUILD
            elif verb == "run" or (verb == "exec" and any(str(word) in data_programs for word in args)):
                timeout = TIMEOUT_DATA
            else:
                timeout = TIMEOUT_COMPOSE
        return self.command(command + list(args), env=child, timeout=timeout, **kwargs)

    @contextlib.contextmanager
    def lock(self, pending_route=None, *, freeze=True):
        """Hold this instance's stable lock for one mutating operation.

        The lock inode lives under <installation-root>/locks and is never
        created here or removed by purge. ``pending_route`` is the command
        name; PENDING_ROUTES decides whether it may enter an existing journal.
        Inside the lock the operation directory is created and the application
        configuration is frozen (PF-A1.2) before any effect.
        """
        try:
            handle = pf_instance.acquire_instance_lock(self.context)
        except pf_instance.ContextError as exc:
            raise Failure(str(exc)) from exc
        try:
            # PF-A2.1: inside the lock, before the journal is read or any operation begins.
            self.require_install_binding(pending_route or "operation")
            journal = self.read_journal()
            if journal is not None:
                self.check_pending_route(journal, pending_route)
            # Private runtime state is created only here, inside a locked mutation route.
            if not self.state.is_dir():
                self.state.mkdir(mode=0o700)
                os.chmod(self.state, 0o700)
            self.begin_operation(pending_route or "operation", freeze=freeze)
            yield handle
        finally:
            self.end_operation()
            handle.release()

    def require_install_binding(self, route):
        """PF-A2.1 inside the instance lock: the binding re-check, then the install gate (read-only).

        ``control-binding-changed``: bootstrap.conf no longer binds the running release or record.json changed
        since validate_context ran in main() (a `pf install control` can complete in between).
        ``install-operation-pending`` / ``legacy-control-active``: pf_install.require_no_pending_install.
        """
        context = self.context
        conf_path = context.installation_root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME
        detail = None
        try:
            conf = pf_bootstrap.parse_bootstrap_conf(pf_instance.read_bytes_nofollow(conf_path), label=str(conf_path),
                                                     error=pf_instance.ContextError)
            record = pf_instance.read_bytes_nofollow(context.record_path)
        except (OSError, UnicodeDecodeError, pf_instance.ContextError) as exc:
            detail = f"the binding cannot be read: {exc}"
        else:
            if self.running_release is not None and Path(conf["control_release"]) != Path(self.running_release):
                detail = f"bootstrap.conf now binds {Path(conf['control_release']).name}"
            elif pf_instance.sha256_bytes(record) != context.record_sha256:
                detail = "record.json changed after selection"
        if detail is not None:
            raise Failure(f"control-binding-changed: The control binding of instance {context.slug} changed while "
                          f"{route} was starting ({detail}); nothing was changed. Run the command again.")
        try:
            pf_install.require_no_pending_install(context.installation_root, context.instance_id, route=route)
        except pf_install.InstallError as exc:
            raise Failure(f"{exc.code}: {exc}") from exc

    def log_installation(self):
        """PF-A2.1 (read-only): the bound control generation, open install operations, an active legacy control."""
        for line in pf_install.describe_installation(self.context.installation_root,
                                                     instance_ids={self.context.instance_id}):
            log(line)

    def staging(self):
        # The protected approved environment decides; config agreement is enforced on load.
        if self.context.approved_environment != "staging":
            raise Failure("Mutating lifecycle commands support staging only. This is not a production deployment package.")

    def revision(self):
        """The deployed source revision recorded in protected state. The workspace is never consulted."""
        deployed = self.state / "deployed.json"
        if deployed.is_file():
            value = load_json(deployed).get("sha", "")
            if isinstance(value, str) and SHA_RE.fullmatch(value):
                return value
        raise Failure("No verified deployed source revision is recorded in protected state (deployed.json).")

    # PostgreSQL client programs run inside the db service as direct argv (no shell string,
    # A12-R03); the connecting role is the frozen application configuration's POSTGRES_USER,
    # which database_ready() requires the running container to agree with.

    def sql(self, database, sql, *, mutation=None):
        """One psql statement on ``database``.

        A statement that is not a ``SELECT``/``SHOW`` query is journaled as an unresolved effect
        when the child times out or is interrupted (``mutation`` overrides the classification;
        read-only statements record nothing).
        """
        quote_identifier(database)
        if mutation is None:
            mutation = sql.lstrip().split(None, 1)[0].upper() not in SQL_READ_ONLY_KEYWORDS if sql.strip() else True
        effect = {"kind": "database", "verb": "sql", "database": database,
                  "statement": sql[:EFFECT_TARGET_WIDTH]} if mutation else None
        return self.compose("exec", "-T", "db", "psql", "-X", "-v", "ON_ERROR_STOP=1",
                            "-U", self.env()["POSTGRES_USER"], "-d", database, "-At", "-c", sql, effect=effect)

    def database_program(self, program, *arguments, **kwargs):
        """``createdb``/``dropdb``/``pg_dump``/``pg_dumpall``/``pg_restore`` inside the db service."""
        return self.compose("exec", "-T", "db", program, "-U", self.env()["POSTGRES_USER"], *arguments, **kwargs)

    def drop_database(self, name):
        quote_identifier(name)
        self.database_program("dropdb", name)

    def db_heads(self, database=None):
        database = database or self.env()["POSTGRES_DB"]
        if self.sql(database, "SELECT to_regclass('public.alembic_version') IS NOT NULL;") != "t":
            return []
        return sorted(filter(None, self.sql(database,
            "SELECT version_num FROM public.alembic_version ORDER BY version_num;").splitlines()))

    def inspect(self, service):
        ids = self.compose("ps", "-a", "-q", service).splitlines()
        if len(ids) != 1:
            raise Failure(f"Expected exactly one existing {service} container; initialize the stack first.")
        return json.loads(self.docker("inspect", ids[0]))[0]

    def database_ready(self):
        values = self.env()
        info = self.inspect("db")
        if not info["State"].get("Running"):
            raise Failure("The database container is not running.")
        actual_env = dict(v.split("=", 1) for v in info["Config"]["Env"] if "=" in v)
        for key in ("POSTGRES_DB", "POSTGRES_USER"):
            if actual_env.get(key) != values[key]:
                raise Failure(f"The database container and .env disagree on {key}; refusing to target a different database.")
        self.sql(values["POSTGRES_DB"], "SELECT 1;")
        major = int(self.sql("postgres", "SHOW server_version_num;")) // 10000
        if major != 16:
            raise Failure("This package was designed for PostgreSQL 16; major-version upgrades require manual planning.")
        return major

    def make_override(self, images, path):
        # One exact grammar (pf_docker): the envelope parses its expected images from these bytes.
        try:
            data = pf_docker.render_image_override(images)
        except pf_docker.DockerScopeError as exc:
            raise Failure("Invalid retained image reference.") from exc
        temporary = Path(path).with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def retain_images(self, backup_id):
        images = {}
        for service in ("backend", "frontend"):
            image_id = self.inspect(service)["Image"]
            reference = f"{self.config['project']}-{service}:backup-{backup_id.lower()}"
            self.docker("tag", image_id, reference)
            self.created_image_refs.append(reference)
            images[service] = {"id": image_id, "reference": reference}
        return images

    def verify_images(self, images):
        for value in images.values():
            actual = json.loads(self.docker("image", "inspect", value["reference"]))[0]["Id"]
            if actual != value["id"]:
                raise Failure("A retained image is missing or changed. Rollback refuses an unverified rebuild.")

    def image_contract(self, root=None, override=None):
        code = (
            "import hashlib,json; from pathlib import Path; "
            "from alembic.config import Config; from alembic.script import ScriptDirectory; "
            "b=Path('/app'); files=[b/'alembic.ini']+"
            "[p for p in (b/'alembic').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc']; "
            "print(json.dumps({'files':{str(p.relative_to(b)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},"
            "'heads':sorted(ScriptDirectory.from_config(Config('/app/alembic.ini')).get_heads())}))"
        )
        return json.loads(self.compose("run", "--rm", "--no-deps", "-T", "backend",
            "uv", "run", "python", "-c", code, root=root, override=override))

    def ensure_local_contract(self, images=None):
        """Verify the running image/database contract without trusting writable repo files."""
        override = self.state / "inspect-images.yaml"
        if images is None:
            images = self.retain_images(utc().lower() + "-inspect-" + uuid.uuid4().hex[:6])
        self.make_override(images, override)
        contract = self.image_contract(override=override)
        if self.db_heads() != contract["heads"]:
            raise Failure("The live database is not at the deployed image's Alembic head.")
        return contract

    def free_space(self):
        try:
            free = shutil.disk_usage(self.backups_root).free
        except OSError as exc:
            raise Failure(f"Cannot measure free space on the registered backup volume {self.backups_root}: {exc}") from exc
        if free < self.config["minimum_free_mb"] * 1024 * 1024:
            raise Failure("Insufficient free space on the backup volume.")

    def create_database(self, name):
        quote_identifier(name)
        self.database_program("createdb", "--owner=" + self.env()["POSTGRES_USER"], "--template=template0", name)

    def restore_into(self, database, dump):
        self.create_database(database)
        with Path(dump).open("rb") as stream:
            self.database_program("pg_restore", "-d", database, "--exit-on-error", "--no-owner", "--no-privileges",
                                  input_file=stream)

    def snapshot(self, reason, source_verified=True):
        self.free_space()
        try:
            revision = self.revision()
        except Failure:
            if source_verified:
                raise
            # Unknown provenance: no commit is invented; the id carries a zero placeholder only.
            revision = None
        backup_id = f"{utc()}-{(revision or '0' * 40)[:12]}-{uuid.uuid4().hex[:6]}"
        self.ensure_backup_tree()
        folder = self.backups_dir / backup_id
        folder.mkdir(mode=0o700)
        log("Creating deployed-source + database checkpoint: " + backup_id)
        images = self.retain_images(backup_id)
        contract = self.ensure_local_contract(images)
        workspace = self.workspace_status()
        workspace_drift = bool(
            workspace["dirty"] or (workspace["head"] is not None and workspace["head"] != revision)
        )
        metadata = {
            "format": 2, "id": backup_id, "created_at": utc(), "reason": reason,
            "status": "incomplete", "source_revision": revision,
            "source_provenance": "git_commit" if (source_verified and revision) else "unknown",
            "source_verified": source_verified, "project": self.config["project"],
            "repository": self.config["repository"], "environment": self.config["environment"],
            "database": self.env()["POSTGRES_DB"], "database_user": self.env()["POSTGRES_USER"],
            "postgres_major": self.database_ready(), "database_heads": self.db_heads(),
            "images": images, "migration_files": contract["files"],
            "workspace_head": workspace["head"], "workspace_dirty": workspace["dirty"],
            "workspace_differs_from_deployed": workspace_drift,
        }
        write_json(folder / "manifest.json", metadata)

        source = folder / "source.tar.gz"
        if source_verified:
            try:
                self.create_deployed_source_archive(source, revision)
            except Failure:
                if reason != "before-rollback":
                    raise
                source_verified = False
                metadata["source_verified"] = False
                metadata["source_provenance"] = "unknown"
                create_source_archive(self.root, source)
                log("Exact deployed source could not be reconstructed; preserving an emergency data/workspace checkpoint.")
        else:
            create_source_archive(self.root, source)

        files = ["source.tar.gz"]
        if workspace_drift:
            workspace_archive = folder / "workspace.tar.gz"
            create_source_archive(self.root, workspace_archive)
            files.append("workspace.tar.gz")
            metadata["workspace_archive"] = "workspace.tar.gz"
            log("Writable repository differs from the deployed revision; current workspace was archived separately.")

        partial = folder / "database.dump.partial"
        with partial.open("wb") as stream:
            self.database_program("pg_dump", "-d", self.env()["POSTGRES_DB"], "--format=custom", "--no-owner",
                                  "--no-privileges", output=stream)
        if not partial.stat().st_size:
            raise Failure("The database dump is empty; checkpoint is incomplete.")
        dump = folder / "database.dump"
        partial.rename(dump)
        with dump.open("rb") as stream, (folder / "database.list").open("wb") as output:
            self.compose("exec", "-T", "db", "pg_restore", "--list", input_file=stream, output=output)
        files.extend(["database.dump", "database.list"])
        verification_db = "pf_verify_" + uuid.uuid4().hex[:20]
        log("Verifying the dump with a full restore into " + verification_db)
        self.restore_into(verification_db, dump)
        if self.db_heads(verification_db) != metadata["database_heads"]:
            raise Failure("Restored Alembic revisions do not match the snapshot. Verification database retained.")
        self.drop_database(verification_db)
        metadata.update({
            "status": "complete", "restore_test": "passed",
            "source_verified": source_verified,
            "checksums": {name: digest(folder / name) for name in files},
        })
        write_json(folder / "manifest.json", metadata)
        (folder / "manifest.sha256").write_text(digest(folder / "manifest.json") + "\n")
        self.publish_backup_permissions(folder)
        log("Checkpoint verified: " + str(folder))
        return metadata

    def ensure_backup_tree(self):
        """Create the checkpoint tree inside an explicit mutation; construction never does this."""
        for directory in (self.backups_root, self.revisions_root, self.backups_dir):
            if not directory.is_dir():
                directory.mkdir(mode=0o750)
            os.chown(directory, -1, self.backup_gid)
            os.chmod(directory, 0o750)

    def ensure_recovery_tree(self):
        for directory in (self.recovery_root.parent, self.recovery_root):
            if not directory.is_dir():
                directory.mkdir(mode=0o750)
            os.chown(directory, -1, self.backup_gid)
            os.chmod(directory, 0o750)

    def snapshots(self):
        result = []
        if not self.backups_dir.is_dir():
            return result
        for folder in self.backups_dir.iterdir():
            if folder.is_dir() and BACKUP_RE.fullmatch(folder.name):
                try:
                    metadata = load_json(folder / "manifest.json")
                    result.append(metadata)
                except (OSError, ValueError):
                    result.append({"id": folder.name, "status": "invalid", "source_revision": "unknown"})
        return sorted(result, key=lambda m: m["id"], reverse=True)

    def display_page(self, items, page):
        selected, pages, start = page_items(items, page)
        log(f"Backups: newest first | page {page}/{pages} | {len(items)} total")
        for number, item in enumerate(selected, start + 1):
            log(f"{number:>3}. {item['id']}  [{item['status']}]  "
                f"{item.get('reason', '')}  DB={','.join(item.get('database_heads', [])) or 'uninitialized'}")
        return pages

    def choose_snapshot(self, requested=None):
        items = self.snapshots()
        if not items:
            raise Failure("No revision checkpoints exist yet. Legacy dump-only backups cannot restore source.")
        if requested:
            matches = [item for item in items if item["id"] == requested or item.get("source_revision") == requested]
            if len(matches) != 1:
                raise Failure("Specify an exact backup ID, or a full SHA with exactly one matching backup.")
            return self.verify_snapshot(matches[0]["id"])
        if unattended():
            raise Failure("Interactive selection requires a terminal; pass a backup ID instead.")
        page = 1
        while True:
            pages = self.display_page(items, page)
            answer = input("Choose a number, n=next, p=previous, q=cancel: ").strip().lower()
            if answer == "q":
                raise Failure("Cancelled.")
            if answer == "n":
                page = min(pages, page + 1)
            elif answer == "p":
                page = max(1, page - 1)
            elif answer.isdigit() and 1 <= int(answer) <= len(items):
                return self.verify_snapshot(items[int(answer) - 1]["id"])

    def verify_snapshot(self, backup_id):
        if not BACKUP_RE.fullmatch(backup_id):
            raise Failure("Invalid backup ID.")
        folder = self.backups_dir / backup_id
        if digest(folder / "manifest.json") != (folder / "manifest.sha256").read_text().strip():
            raise Failure("Backup manifest checksum mismatch.")
        metadata = load_json(folder / "manifest.json")
        if metadata.get("status") != "complete" or metadata.get("format") not in (1, 2) or metadata.get("id") != backup_id:
            raise Failure("The selected checkpoint is incomplete or unsupported.")
        if metadata.get("project") != self.config["project"] or metadata.get("repository") != self.config["repository"]:
            raise Failure("Checkpoint belongs to a different deployment.")
        required = ("source.tar.gz", "database.dump", "database.list")
        for name in required:
            if digest(folder / name) != metadata["checksums"].get(name):
                raise Failure("Backup checksum mismatch: " + name)
        if metadata.get("format") == 2 and metadata.get("workspace_archive"):
            name = metadata["workspace_archive"]
            if digest(folder / name) != metadata["checksums"].get(name):
                raise Failure("Backup checksum mismatch: " + name)
        return metadata

    def pause(self, kind, **extra):
        write_json(self.pending, {"operation": kind, "phase": "paused", "started": utc(), **extra})
        self.compose("stop", "frontend", "backend")
        for service in ("frontend", "backend"):
            if self.inspect(service)["State"].get("Running"):
                raise Failure("Application writes have not been stopped.")

    def phase(self, phase, **fields):
        data = load_json(self.pending)
        data.update({"phase": phase, **fields})
        write_json(self.pending, data)

    def fail_closed(self):
        """After a failed operation with a pending journal: stop this instance's own one-off Compose
        containers (exact PF-A1.3 inventory), then the application services (PF-A1.4).

        Runs under the failing operation's lock, through the same context and runner. A cached daemon
        refusal ends it before any further process; a one-off that vanished after the inventory (an
        exited ``--rm`` job) is reported and skipped. Nothing is selected by the legacy run label.
        """
        if not self.pending.exists():
            return
        daemon_refused = False
        try:
            inventory = self.docker_inventory()
        except DaemonFailure as exc:
            log("WARNING: Could not confirm application shutdown: " + str(exc).splitlines()[0])
            inventory, daemon_refused = None, True
        except Failure as exc:
            log("WARNING: Could not list this instance's one-off containers: " + str(exc).splitlines()[0])
            inventory = None
        if inventory is not None:
            for container_id in pf_docker.owned_oneoffs(inventory):
                try:
                    self.docker("stop", "--time", "30", container_id)
                except DaemonFailure as exc:
                    log("WARNING: Could not confirm application shutdown: " + str(exc).splitlines()[0])
                    daemon_refused = True
                    break
                except Failure as exc:
                    # The first line of the daemon's answer ("No such container" for a vanished --rm job).
                    lines = [line.strip() for line in str(exc).splitlines() if line.strip()] or [type(exc).__name__]
                    log(f"WARNING: Could not stop one-off container {container_id[:12]}: {lines[min(1, len(lines) - 1)]}")
        if not daemon_refused:
            try:
                self.compose("stop", "frontend", "backend")
            except Failure as exc:
                log("WARNING: Could not confirm application shutdown: " + str(exc).splitlines()[0])
        log(f"Operation incomplete. Application services are intentionally stopped. Inspect 'pf --instance "
            f"{self.context.slug} status'. Automation and Compose writes remain blocked.")

    def wait_health(self, service):
        end = time.monotonic() + self.config["health_timeout_seconds"]
        while time.monotonic() < end:
            state = self.inspect(service)["State"]
            if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
                return
            time.sleep(2)
        raise Failure(f"{service} did not become healthy within the configured timeout.")

    def activate(self, images, expected_heads):
        self.verify_images(images)
        self.make_override(images, self.override)
        self.compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "backend")
        self.wait_health("backend")
        if self.db_heads() != expected_heads:
            raise Failure("Live Alembic revision differs from the selected application.")
        self.phase("opening-frontend")
        self.compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "frontend")
        self.wait_health("frontend")
        response = self.compose("exec", "-T", "frontend", "wget", "-q", "-O", "-", "http://127.0.0.1:5173/api/health")
        data = json.loads(response)
        if data.get("status") != "ok" or data.get("database") != "connected":
            raise Failure("Frontend/API/database health check failed.")
        self.phase("health-checked")
        log("Application health checks passed. Perform the UI/workflow and network-access smoke tests separately.")

    def replace_source(self, candidate, revision, *, verified=True):
        # The private candidate is inventoried before the workspace is touched; the
        # protected manifest then records what was deployed (workspace generation switch
        # and deployed-artifact persistence remain PF-A3).
        candidate = Path(candidate)
        try:
            self.refuse_reserved_candidate_paths(candidate)
            source = {"kind": "git_commit", "commit": revision, "remote": self.approved_remote()} if verified \
                else {"kind": "unknown"}
            manifest = pf_source.build_manifest(candidate, source=source, excludes=SOURCE_EXCLUDES)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc
        if any(entry["kind"] != "file" for entry in manifest["entries"]):
            raise Failure("Candidate source contains an unsupported link/special file; nothing was replaced.")
        self.phase("changing-source")
        # v2.5 keeps all runtime control/configuration outside repo/. The writable
        # repository can therefore be replaced as one application working tree.
        for item in list(self.root.iterdir()):
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in candidate.iterdir():
            destination = self.root / item.name
            if item.is_dir() and not item.is_symlink():
                shutil.copytree(item, destination, symlinks=False)
            elif item.is_file() and not item.is_symlink():
                shutil.copy2(item, destination)
            else:
                raise Failure("Downloaded source contains an unsupported link/special file.")
        self.publish_workspace_permissions()
        try:
            pf_source.write_manifest(self.context.source_manifest_path, manifest)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc
        self.phase("source-replaced")

    def github(self, path, missing=False):
        # Unauthenticated GitHub API only: no credential is taken from the inherited environment
        # (the installed launcher provides none); protected credentials are a later-WP decision.
        request = urllib.request.Request("https://api.github.com/repos/" + self.config["repository"] + "/" + path,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PartFlow-NAS-Admin/" + VERSION,
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if missing and exc.code == 404:
                return None
            raise Failure(f"GitHub HTTP {exc.code}; check access, rate limits and connectivity. No deployment occurred.") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise Failure("GitHub request failed; no offline/stale release fallback is used.") from exc

    def release(self, channel):
        if channel == "stable":
            return self.github("releases/latest", missing=True)
        # Publication date, not tag spelling or the first API page, selects the preview feed.
        releases = []
        page = 1
        while True:
            batch = self.github(f"releases?per_page=100&page={page}")
            releases.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            if page > 100:
                raise Failure("Release history is too large for this controller; narrow the release policy manually.")
        return select_release(releases, channel)

    def resolve(self, *, latest=False, commit=None, release=None, channel=None):
        selected = None
        if latest:
            ref = self.config["branch"]
        elif commit:
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
                raise Failure("--commit requires a Git SHA, not a branch or tag.")
            ref = commit
        else:
            if not release or release == "latest":
                selected = self.release(channel or self.config["release_channel"])
            else:
                selected = self.github("releases/tags/" + urllib.parse.quote(release, safe=""))
            if selected is None:
                return None
            if selected.get("draft"):
                raise Failure("Draft releases cannot be deployed.")
            ref = selected["tag_name"]
        details = self.github("commits/" + urllib.parse.quote(ref, safe=""))
        sha = details["sha"]
        if not SHA_RE.fullmatch(sha):
            raise Failure("GitHub returned an invalid source SHA.")
        result = {"sha": sha, "ref": ref, "release_id": selected["id"] if selected else None,
                  "published_at": selected.get("published_at") if selected else None,
                  "prerelease": selected.get("prerelease") if selected else None}
        observed_path = self.state / "observed-tags.json"
        observed = load_json(observed_path) if observed_path.exists() else {}
        if selected and ref in observed and observed[ref] != sha:
            raise Failure("A previously observed release tag moved. Publish a new tag instead.")
        if selected:
            observed[ref] = sha
            write_json(observed_path, observed)
        return result

    def require_ci(self, sha):
        workflow = urllib.parse.quote(self.config["ci_workflow"], safe="")
        runs = self.github(f"actions/workflows/{workflow}/runs?head_sha={sha}&event=push&per_page=100")["workflow_runs"]
        runs = [run for run in runs if run.get("head_sha") == sha and run.get("event") == "push"
                and run.get("head_branch") == self.config["branch"]
                and run.get("head_repository", {}).get("full_name") == self.config["repository"]]
        if not runs:
            raise Deferred("No CI push run was found for the exact target SHA on the configured branch.")
        latest = max(runs, key=lambda run: (run["run_number"], run.get("run_attempt", 1)))
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            raise Deferred("The latest CI run for the exact target SHA is not a completed success.")
        log("Verified GitHub CI: " + latest["html_url"])

    def automatic_guard(self, current, candidate, target_sha):
        workspace = self.workspace_status()
        if workspace["provenance"] != "git_commit":
            raise Deferred("Automatic updates require a workspace with a protected manifest established by one "
                           "successful manual update first.")
        if workspace["head"] != current or workspace["dirty"]:
            raise Deferred(
                "Automatic update requires the writable repository to match the deployed SHA exactly. "
                "Use a manual update so workspace changes can be archived and reviewed."
            )
        try:
            store = self.source_store()
            with self.store_lock(store):
                store.ensure()
                store.fetch_commit(current, timeout=TIMEOUT_GIT_FETCH)
            ancestor = store.is_ancestor(current, target_sha)
        except pf_source.SourceError as exc:
            raise Deferred("Ancestry of the deployed SHA cannot be established in the protected store: " + str(exc)) from exc
        if not ancestor:
            raise Deferred("The selected release is not a descendant of the deployed SHA; automatic downgrade/divergence is refused.")
        for relative in AUTO_REVIEW_PATHS:
            old, new = self.root / relative, candidate / relative
            if (old.exists() != new.exists()) or (old.exists() and digest(old) != digest(new)):
                raise Deferred("Deployment/configuration file changed; manual review required: " + relative)
        if migration_files(self.root) != migration_files(candidate):
            raise Deferred("Migration files changed. The scheduler never applies migrations.")

    def build_target(self, candidate, sha):
        suffix = sha[:12] + "-" + uuid.uuid4().hex[:6]
        images = {service: f"{self.config['project']}-{service}:candidate-{suffix}"
                  for service in ("backend", "frontend")}
        override = self.state / "candidate-images.yaml"
        self.make_override(images, override)
        for service in images:
            log("Building candidate " + service + "; the running images are not replaced.")
            log(self.compose("build", service, root=candidate, override=override))
        pinned = {service: {"reference": reference, "id": json.loads(self.docker("image", "inspect", reference))[0]["Id"]}
                  for service, reference in images.items()}
        return pinned, override

    def deploy(self, target=None, *, use_current=False, skip_ci=False):
        self.staging()
        self.assert_new_deployment()
        self.command(["git", "--version"], effect=None)
        self.docker("version", "--format", "{{.Server.Version}}")
        self.free_space()
        values = self.prepare_new_env()
        self.ensure_listener_available(values)
        self.compose("config", "-q")

        if use_current:
            if target is not None:
                raise Failure("Internal deploy source selection conflict.")
        elif target is None:
            raise Failure("A resolved deployment target is required.")

        def gate_ci(sha):
            if skip_ci:
                log("WARNING: CI verification explicitly bypassed for this manual staging deployment.")
            else:
                self.require_ci(sha)

        if not use_current:
            gate_ci(target["sha"])
        with contextlib.ExitStack() as stack:
            # Builds always consume a private immutable candidate exported from the protected
            # store; for --current the workspace must first prove equal to that candidate.
            folder = stack.enter_context(tempfile.TemporaryDirectory(prefix="initial-candidate-", dir=self.state))
            source = Path(folder) / "repo"
            if use_current:
                target = self.current_target(source)
                gate_ci(target["sha"])
            else:
                self.materialize_source(target, source)

            images, override = self.build_target(source, target["sha"])
            contract = self.image_contract(root=source, override=override)
            if contract["files"] != migration_files(source):
                raise Failure("Built backend image does not contain the selected migration source.")
            if len(contract["heads"]) != 1:
                raise Failure("Initial deployment requires exactly one Alembic head in the selected source.")

            confirm(
                "DEPLOY " + target["sha"][:12],
                "Create a new PartFlow staging deployment. No existing project data will be adopted or deleted.\n"
                + f"Source: {target['ref']} -> {target['sha']}\n"
                + self.environment_summary(values),
            )
            write_json(self.pending, {
                "operation": "deploy", "phase": "confirmed", "started": utc(),
                "target": target, "database": values["POSTGRES_DB"],
            })

            if use_current:
                self.publish_workspace_permissions()
                self.record_source_manifest(source, target["sha"], verified=True)
                self.phase("source-ready")
            else:
                self.replace_source(source, target["sha"])

            self.phase("starting-database")
            self.compose("up", "-d", "--no-deps", "db")
            self.wait_health("db")
            self.database_ready()
            if self.db_heads():
                raise Failure("The supposedly new database already contains an Alembic revision. New deploy refuses to adopt it.")

            self.phase("migrating-database")
            self.compose(
                "run", "--rm", "--no-deps", "-T", "backend",
                "uv", "run", "alembic", "upgrade", "head",
                override=override,
            )
            if self.db_heads() != contract["heads"]:
                raise Failure("Initial database migration did not reach the selected application's Alembic head.")

            self.phase("activating")
            self.activate(images, contract["heads"])
            write_json(self.state / "deployed.json", {
                **target, "deployed_at": utc(), "checkpoint": None,
                "initial_deploy": True, "database_heads": contract["heads"],
            })
            self.pending.unlink()
            log("Initial deployment complete.")
            log("Run the UI/workflow and firewall smoke tests, then create the first baseline checkpoint with: sudo pf backup")

    def plan_missing(self, operation):
        return Failure(f"plan-missing: This interrupted {operation} predates frozen deletion plans (PF-A1.3); "
                       "automatic resume is refused. Review the remaining resources manually; nothing was deleted.")

    def abort_deploy(self):
        self.staging()
        if not self.pending.exists():
            raise Failure("No incomplete initial deployment exists.")
        pending = load_json(self.pending)
        if pending.get("operation") != "deploy":
            raise Failure("The pending operation is not an initial deployment; abort-deploy refuses to touch it.")
        if (self.state / "deployed.json").exists():
            raise Failure("A managed deployment record already exists; abort-deploy is only for an incomplete first deployment.")
        safe_phases = {
            "confirmed", "changing-source", "source-replaced", "source-ready",
            "starting-database", "migrating-database", "activating", "aborting",
        }
        if pending.get("phase") not in safe_phases:
            raise Failure(
                "Frontend access may already have opened. Automatic first-deploy cleanup is refused; preserve the database and review recovery manually."
            )
        project = self.config["project"]
        if pending.get("phase") == "aborting":
            # Resume: the frozen plan only (closed); nothing newly discovered is ever added.
            if "deletion_plan" not in pending:
                raise self.plan_missing("abort-deploy")
            plan = self.load_deletion_plan(pending, kind="abort-deploy")
            self.verify_resume_daemon(plan, pending)
            self._topology_checked = True
            self.log_plan(plan, title="Frozen abort-deploy plan (resume; only these items are removed):")
            confirm(
                "RESUME ABORT DEPLOY " + project,
                "An earlier abort-deploy froze this plan and began removing resources. Resume only the remaining "
                "planned items; the source checkout and .env are kept.",
            )
        else:
            database = pending.get("database") or self.env()["POSTGRES_DB"]
            plan = self.plan_for("abort-deploy", self.docker_inventory(), command="abort-deploy")
            self.log_plan(plan, title="Abort-deploy plan (exact resources of this instance; images are retained):")
            confirm(
                "ABORT DEPLOY " + project,
                f"Delete containers and Docker volumes created by the incomplete first deployment for database {database}. "
                "The source checkout and .env are kept so deployment can be retried. This is allowed only before frontend access opened.",
            )
            reference = self.write_deletion_plan(plan)
            self.durable_phase("aborting", deletion_plan=reference, deleted=[])
        # LIFECYCLE section 8: per-resource proof, never `compose down -v` as a substitute.
        self.execute_deletion_plan(plan)
        if self.override.exists():
            self.override.unlink()
        self.pending.unlink()
        log("Incomplete first deployment resources were removed. Source and .env were kept; rerun deploy when ready.")

    def instance_summary(self, plan):
        """What a purge would delete and retain, from a deletion plan (never from name prefixes)."""
        try:
            values = self.env()
        except Failure:
            values = {}
        revision = "unknown"
        try:
            revision = self.revision()
        except Failure:
            pass
        candidates = plan["candidates"]

        def keys(kind):
            return [(item["identity"].get("name") or item["key"]) if kind == "container" else item["key"]
                    for item in candidates if item["kind"] == kind]

        return {
            "project": self.config["project"],
            "root": str(self.root),
            "database": values.get("POSTGRES_DB", "unknown"),
            "database_user": values.get("POSTGRES_USER", "unknown"),
            "revision": revision,
            "containers": keys("container"),
            "volumes": keys("volume"),
            "networks": keys("network"),
            "images": keys("image") + list(plan.get("pending_images") or []),
            "exclusions": list(plan["exclusions"]),
            "bind_paths": list(plan["bind_paths"]),
            "checkpoints": len(self.snapshots()),
            "state_present": self.state.is_dir() and any(
                item.name != "operation.lock" for item in self.state.iterdir()
            ),
            "env_present": (self.config_dir / ".env").exists(),
        }

    def log_instance_summary(self, summary):
        log("Selected PartFlow instance:")
        log("  Project: " + summary["project"])
        log("  Root: " + summary["root"])
        log("  Database: " + summary["database"] + " | user: " + summary["database_user"])
        log("  Source: " + (summary["revision"] if summary["revision"] != "unknown" else "unverified"))
        log("  Containers: " + str(len(summary["containers"])))
        log("  Volumes: " + (", ".join(summary["volumes"]) or "none"))
        log("  Networks: " + (", ".join(summary["networks"]) or "none"))
        log("  PartFlow image tags (deleted only when the recovery bundle covers their image ID): "
            + str(len(summary["images"])))
        for entry in summary.get("exclusions", []):
            log(f"  Retained: {entry['reason'] if entry['kind'] == 'image' else entry['class']} {entry['kind']} "
                f"{entry['key']}")
        for path in summary.get("bind_paths", []):
            log("  Retained bind path (never deleted): " + path)
        log("  Revision checkpoints: " + str(summary["checkpoints"]))

    def database_inventory(self):
        rows = self.sql(
            "postgres",
            "SELECT datname, datallowconn FROM pg_database "
            "WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY datname;",
        )
        result = []
        for line in rows.splitlines():
            if not line:
                continue
            parts = line.split("|", 1)
            if len(parts) != 2:
                raise Failure("Unexpected PostgreSQL database inventory output.")
            quote_identifier(parts[0])
            result.append({"name": parts[0], "allow_connections": parts[1] == "t"})
        return result

    def dump_database(self, database, destination):
        quote_identifier(database)
        destination = Path(destination)
        with destination.open("wb") as output:
            self.database_program("pg_dump", "-d", database, "--format=custom", "--no-owner", "--no-privileges",
                                  output=output)
        if not destination.is_file() or not destination.stat().st_size:
            raise Failure("Database dump is empty: " + database)
        with destination.open("rb") as stream:
            self.compose("exec", "-T", "db", "pg_restore", "--list", input_file=stream)

    def create_tree_archive(self, source, destination, arcname):
        source, destination = Path(source), Path(destination)
        with tarfile.open(destination, "w:gz") as archive:
            if source.exists():
                archive.add(source, arcname=arcname, recursive=True)
        # Read every member to catch truncated/corrupt archives before deletion.
        with tarfile.open(destination, "r:gz") as archive:
            archive.getmembers()

    def extract_tree_archive(self, archive_path, destination):
        destination = Path(destination).resolve()
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                member_path = Path(member.name)
                target = (destination / member_path).resolve()
                if (member_path.is_absolute()
                        or (target != destination and destination not in target.parents)
                        or not (member.isdir() or member.isfile())):
                    raise Failure("Unsafe recovery archive member: " + member.name)
            for member in members:
                target = destination / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    os.chmod(target, member.mode & 0o777 & ~0o022)

    def available_snapshot_image_refs(self):
        refs = set()
        missing = []
        for item in self.snapshots():
            if item.get("status") != "complete":
                continue
            for value in item.get("images", {}).values():
                reference = value.get("reference") if isinstance(value, dict) else None
                if not reference:
                    continue
                try:
                    self.docker("image", "inspect", reference)
                    refs.add(reference)
                except Failure:
                    missing.append(reference)
        return sorted(refs), sorted(set(missing))

    def binding_blocked(self, inventory, created, checkpoint):
        """A blocker that appeared after ``PURGE`` was confirmed and services were stopped (RI-21).

        It is a plan change, not a pre-confirmation refusal. The application is reopened only
        when every blocker is ``resource-shared`` (a foreign user of this instance's volume or
        network, which the activation never touches); any other blocker is a resource Compose
        could adopt or recreate, so the services stay stopped and the journal stays paused.
        """
        self.write_private_json("inventory-binding.json", dict(
            inventory.record(), created_image_refs=created, compare_plans="resource-blocked"))
        lines = [f"  - {item.cls}: {item.kind} {self.resource_name(item)}: {item.reason}"
                 for item in inventory.blockers]
        head = (f"plan-changed: {len(inventory.blockers)} resource(s) became blocked while the recovery bundle was "
                "created, after 'PURGE' was confirmed and the application services were stopped:\n"
                + "\n".join(lines) + "\nPurge stops before deletion; nothing was deleted. ")
        if all(item.cls == "resource-shared" for item in inventory.blockers):
            return PlanChanged(head + "The application is reopened.", checkpoint)
        return PlanChanged(
            head + "The application is NOT reopened: Compose could adopt or recreate a blocking resource. Review "
            f"them with 'pf status --instance {self.context.slug}', remove or resolve them, then run "
            f"'pf resume --instance {self.context.slug}'. Legacy or foreign resources are never adopted "
            "automatically (adoption is PF-A2).", None)

    def create_purge_recovery(self, preliminary):
        """Create a verified recovery bundle before destructive project purge; return (manifest, binding plan).

        PF-A1.3: after ``images.tar`` is verified the binding inventory and plan are built, the
        plan must equal the preliminary one (owned tags modulo the tags this operation created),
        and ``resources_before_purge`` is sealed from the binding candidates. The manifest is
        written once and never rewritten.

        The active database/source and current images are mandatory. Historical
        rollback image tags are included when still present. All non-template
        databases in this project's dedicated PostgreSQL container are preserved.
        PostgreSQL globals are archived for manual recovery but are not executed
        automatically during restore.
        """
        self.database_ready()
        checkpoint = self.snapshot("before-purge")
        recovery_id = f"purge-{utc()}-{(checkpoint['source_revision'] or '0' * 40)[:12]}-{uuid.uuid4().hex[:6]}"
        self.ensure_recovery_tree()
        folder = self.recovery_root / recovery_id
        folder.mkdir(mode=0o700)
        db_dir = folder / "databases"
        state_dir = folder / "state"
        saved_config_dir = folder / "configuration"
        db_dir.mkdir(mode=0o700)
        state_dir.mkdir(mode=0o700)
        saved_config_dir.mkdir(mode=0o700)
        log("Creating full purge recovery bundle: " + recovery_id)

        checkpoint_folder = self.backups_dir / checkpoint["id"]
        shutil.copy2(checkpoint_folder / "source.tar.gz", folder / "source.tar.gz")
        if checkpoint.get("workspace_archive"):
            shutil.copy2(checkpoint_folder / checkpoint["workspace_archive"], folder / "workspace.tar.gz")
        shutil.copy2(checkpoint_folder / "database.dump", db_dir / "active.dump")
        shutil.copy2(checkpoint_folder / "database.list", db_dir / "active.list")

        # The bundle preserves the configuration this operation consumed: the frozen
        # snapshot rendering (literal values), not whatever the editable file holds now.
        if self.frozen is None:
            raise Failure("No frozen application configuration for this purge; recovery bundle refused.")
        try:
            frozen_bytes = pf_config.render_app_env(self.frozen.values)
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        with (saved_config_dir / ".env").open("wb") as handle:
            handle.write(frozen_bytes)
        admin_config = self.config_dir / "pf-config.json"
        if admin_config.is_file():
            shutil.copy2(admin_config, saved_config_dir / "pf-config.json")

        inventory = self.database_inventory()
        active = self.env()["POSTGRES_DB"]
        databases = []
        for item in inventory:
            name = item["name"]
            record = dict(item)
            if name == active:
                record["dump"] = "databases/active.dump"
                record["heads"] = self.db_heads(name)
                databases.append(record)
                continue
            dump_name = "db-" + hashlib.sha256(name.encode()).hexdigest()[:16] + ".dump"
            dump_path = db_dir / dump_name
            changed_connections = False
            try:
                if not item["allow_connections"]:
                    self.sql("postgres", f"ALTER DATABASE {quote_identifier(name)} ALLOW_CONNECTIONS true;",
                             mutation=True)
                    changed_connections = True
                self.dump_database(name, dump_path)
                verify_name = "pf_verify_" + uuid.uuid4().hex[:20]
                self.restore_into(verify_name, dump_path)
                self.drop_database(verify_name)
            finally:
                if changed_connections:
                    self.sql("postgres", f"ALTER DATABASE {quote_identifier(name)} ALLOW_CONNECTIONS false;",
                             mutation=True)
            record["dump"] = "databases/" + dump_name
            record["heads"] = self.db_heads(name) if item["allow_connections"] else []
            databases.append(record)

        globals_path = folder / "postgres-globals.sql"
        with globals_path.open("wb") as output:
            self.database_program("pg_dumpall", "-d", "postgres", "--globals-only", output=output)
        if not globals_path.stat().st_size:
            raise Failure("PostgreSQL globals archive is empty; purge recovery is incomplete.")

        # Preserve the whole rollback/checkpoint history independently from the
        # normal backups tree so --delete-backups remains recoverable.
        self.create_tree_archive(
            self.backups_dir,
            folder / "revision-checkpoints.tar.gz",
            self.config["project"],
        )

        state_files = []
        for name in ("deployed.json", "last-reset.json", "observed-tags.json"):
            source = self.state / name
            if source.is_file():
                shutil.copy2(source, state_dir / name)
                state_files.append(name)

        image_refs, missing_history_images = self.available_snapshot_image_refs()
        for value in checkpoint["images"].values():
            image_refs.append(value["reference"])
        image_refs = sorted(set(image_refs))
        images_path = folder / "images.tar"
        if not image_refs:
            raise Failure("No active PartFlow application images were available for recovery.")
        self.docker("image", "save", "-o", images_path, *image_refs)
        if not images_path.stat().st_size:
            raise Failure("Docker image recovery archive is empty.")
        with tarfile.open(images_path, "r:") as archive:
            archive.getmembers()

        binding_inventory = self.docker_inventory()
        created = sorted(set(self.created_image_refs))
        if binding_inventory.blockers:
            raise self.binding_blocked(binding_inventory, created, checkpoint)
        binding = self.plan_for("purge", binding_inventory, command="purge", recovery_id=recovery_id,
                                covered_image_refs=set(image_refs))
        try:
            pf_docker.compare_plans(preliminary, binding, created_image_refs=set(created))
            comparison = "equal"
        except pf_docker.DockerScopeError as exc:
            self.write_private_json("inventory-binding.json", dict(
                binding_inventory.record(), created_image_refs=created, compare_plans="plan-changed",
                findings=[finding.render() for finding in exc.findings]))
            raise PlanChanged("plan-changed: The deletion candidates changed while the recovery bundle was created ("
                              + "; ".join(f"{finding.path} {finding.message}" for finding in exc.findings[:10])
                              + "); purge stops before deletion and the application is reopened.", checkpoint) from exc
        self.write_private_json("inventory-binding.json", dict(
            binding_inventory.record(), created_image_refs=created, compare_plans=comparison))

        def candidates(kind):
            return [item["key"] for item in binding["candidates"] if item["kind"] == kind]

        resources = {"containers": candidates("container"), "volumes": candidates("volume"),
                     "networks": candidates("network"), "images": candidates("image")}
        manifest = {
            "format": 2,
            "kind": "partflow-purge-recovery",
            "status": "complete",
            "id": recovery_id,
            "created_at": utc(),
            "project": self.config["project"],
            "environment": self.config["environment"],
            "repository": self.config["repository"],
            "root": str(self.root),
            "instance_id": self.context.instance_id,
            "slug": self.context.slug,
            "source_revision": checkpoint["source_revision"],
            "source_verified": bool(checkpoint.get("source_verified")),
            "source_provenance": checkpoint.get("source_provenance", "unknown"),
            "active_checkpoint": checkpoint["id"],
            "database": active,
            "database_user": self.env()["POSTGRES_USER"],
            "database_heads": checkpoint["database_heads"],
            "postgres_major": checkpoint["postgres_major"],
            "databases": databases,
            "active_images": checkpoint["images"],
            "saved_image_refs": image_refs,
            "missing_historical_image_refs": missing_history_images,
            "state_files": state_files,
            "workspace_archive": "workspace.tar.gz" if checkpoint.get("workspace_archive") else None,
            "workspace_head": checkpoint.get("workspace_head"),
            "workspace_dirty": checkpoint.get("workspace_dirty", False),
            "resources_before_purge": resources,
            "automatic_merge_supported": False,
            "restore_scope": (
                "Functional instance state: exact deployed source, writable workspace when it differs, "
                "external runtime configuration, active and retained databases, current application images, "
                "available rollback images, revision checkpoints. "
                "Docker container/network IDs and extra PostgreSQL roles are not recreated bit-for-bit."
            ),
        }
        files = [
            "source.tar.gz", "postgres-globals.sql", "revision-checkpoints.tar.gz", "images.tar",
            "databases/active.dump", "databases/active.list", "configuration/.env",
        ]
        if checkpoint.get("workspace_archive"):
            files.append("workspace.tar.gz")
        if (saved_config_dir / "pf-config.json").is_file():
            files.append("configuration/pf-config.json")
        files.extend(record["dump"] for record in databases if record["name"] != active)
        files.extend("state/" + name for name in state_files)
        manifest["checksums"] = {name: digest(folder / name) for name in files}
        write_json(folder / "manifest.json", manifest)
        (folder / "manifest.sha256").write_text(digest(folder / "manifest.json") + "\n", encoding="utf-8")
        self.publish_backup_permissions(folder)
        log("Recovery bundle verified: " + str(folder))
        if missing_history_images:
            log("WARNING: Some old rollback image tags were already missing before purge. Their checkpoint files are preserved, but those old image layers cannot be reconstructed automatically.")
        return manifest, binding

    def recoveries(self):
        """Bundle candidates of the selected instance only: ``<recovery>/<compose_project>/purge-*``.

        Neither ``--project`` nor any sibling project directory is ever listed (PF-A1.4).
        """
        base = self.recovery_root
        result = []
        if not base.is_dir():
            return result
        for folder in base.iterdir():
            # The same rule as verify_recovery: a real directory, never a link followed elsewhere.
            if not RECOVERY_RE.fullmatch(folder.name) or not real_directory(folder):
                continue
            try:
                metadata = load_json(folder / "manifest.json")
                metadata["_folder"] = str(folder)
                result.append(metadata)
            except (OSError, ValueError):
                result.append({"id": folder.name, "project": base.name, "status": "invalid", "_folder": str(folder)})
        return sorted(result, key=lambda item: item["id"], reverse=True)

    def verify_recovery(self, item):
        folder = Path(item.get("_folder") or self.recovery_root / item["id"])
        # PF-A1.4: restore authority is the selected instance's own recovery directory, exactly.
        if folder.parent != self.recovery_root or not real_directory(folder):
            raise Failure(
                f"recovery-outside-instance: {folder} is not a bundle directory of instance {self.context.slug} "
                f"({self.recovery_root}); only the selected instance's own recovery bundles can be listed or "
                "restored. Nothing was changed.")
        if not RECOVERY_RE.fullmatch(folder.name):
            raise Failure("Invalid recovery bundle path.")
        manifest_path = folder / "manifest.json"
        if digest(manifest_path) != (folder / "manifest.sha256").read_text(encoding="utf-8").strip():
            raise Failure("Recovery manifest checksum mismatch.")
        metadata = load_json(manifest_path)
        if metadata.get("kind") != "partflow-purge-recovery" or metadata.get("status") != "complete":
            raise Failure("Recovery bundle is incomplete or unsupported.")
        state_files = metadata.get("state_files", [])
        # The checked value is the value restore_instance consumes: a list, never a string iterated
        # per character or any other shape normalized here; a non-string entry fails the allowlist.
        if not isinstance(state_files, list):
            raise Failure(
                f"recovery-state-file-refused: bundle {metadata.get('id', folder.name)} lists state files as "
                f"{type(state_files).__name__}, not a list of file names; only {', '.join(RESTORABLE_STATE_FILES)} "
                "can be restored into protected state. Nothing was changed.")
        for name in state_files:
            if name not in RESTORABLE_STATE_FILES:
                raise Failure(
                    f"recovery-state-file-refused: bundle {metadata.get('id', folder.name)} lists state file "
                    f"{name!r}; only {', '.join(RESTORABLE_STATE_FILES)} can be restored into protected state. "
                    "Nothing was changed.")
        for name, checksum in metadata.get("checksums", {}).items():
            path = folder / name
            if not path.is_file() or digest(path) != checksum:
                raise Failure("Recovery bundle checksum mismatch: " + name)
        metadata["_folder"] = str(folder)
        return metadata

    def display_recoveries(self, items, page=1):
        selected, pages, start = page_items(items, page)
        log(f"Purge recovery bundles | newest first | page {page}/{pages} | {len(items)} total")
        for number, item in enumerate(selected, start + 1):
            log(
                f"{number:>3}. {item['id']}  [{item.get('status', 'unknown')}]  "
                f"project={item.get('project', 'unknown')}  db={item.get('database', 'unknown')}  "
                f"source={(item.get('source_revision') or 'unknown')[:12]}"
            )
        return pages

    def choose_recovery(self, requested=None):
        items = self.recoveries()
        if not items:
            raise Failure("No purge recovery bundles were found.")
        if requested:
            matches = [item for item in items if item["id"] == requested]
            if len(matches) != 1:
                raise Failure("Specify one exact purge recovery ID.")
            return self.verify_recovery(matches[0])
        if unattended():
            raise Failure("Interactive recovery selection requires a terminal; pass a recovery ID.")
        page = 1
        while True:
            pages = self.display_recoveries(items, page)
            answer = input("Choose a recovery number, n=next, p=previous, q=cancel: ").strip().lower()
            if answer == "q":
                raise Failure("Cancelled.")
            if answer == "n":
                page = min(pages, page + 1)
            elif answer == "p":
                page = max(1, page - 1)
            elif answer.isdigit() and 1 <= int(answer) <= len(items):
                return self.verify_recovery(items[int(answer) - 1])

    def finish_purge_cleanup(self, recovery_id, plan, *, delete_backups, reset_admin_config):
        # Docker deletion runs exactly the frozen plan (PF-A1.3). State/.env are removed last so
        # an interrupted Docker cleanup remains diagnosable and resumable; bind paths are never deleted.
        self.execute_deletion_plan(plan)

        if delete_backups and self.backups_dir.exists():
            shutil.rmtree(self.backups_dir)
        env_path = self.config_dir / ".env"
        if env_path.exists():
            env_path.unlink()
        legacy_marker = self.root / "DEPLOYED_SOURCE.txt"
        if legacy_marker.exists():
            legacy_marker.unlink()
        if self.state.exists():
            shutil.rmtree(self.state)
        if reset_admin_config:
            config = self.config_dir / "pf-config.json"
            if config.exists():
                try:
                    config.unlink()
                except OSError as exc:
                    log("WARNING: purge completed but local admin config could not be removed: " + str(exc))

        log("Purge complete for " + self.config["project"] + ".")
        log("Verified recovery bundle retained at: " + str(self.recovery_root / recovery_id))
        log("The writable repository and root-owned control plane remain. Runtime .env was removed from config/.")
        if reset_admin_config:
            log(f"Next: {self.pf_command()} config admin, then {self.pf_command()} deploy --latest.")
        else:
            log(f"Next: {self.pf_command()} deploy --latest.")

    def purge(self, *, delete_backups=None, reset_admin_config=False):
        self.staging()

        # A power loss or Docker error after the final destructive confirmation
        # leaves a resumable journal until the last filesystem cleanup step.
        if self.pending.exists():
            pending = load_json(self.pending)
            if pending.get("operation") == "purge" and pending.get("phase") == "deleting" and pending.get("recovery"):
                if "deletion_plan" not in pending:
                    raise self.plan_missing("purge")
                recovery_id = pending["recovery"]
                matches = [item for item in self.recoveries() if item.get("id") == recovery_id]
                if len(matches) != 1:
                    raise Failure("Interrupted purge recovery bundle is missing or ambiguous; manual recovery is required.")
                self.verify_recovery(matches[0])
                plan = self.load_deletion_plan(pending, kind="purge")
                self.verify_resume_daemon(plan, pending)
                self._topology_checked = True
                self.log_plan(plan, title="Frozen purge plan (resume; only these items are removed):")
                confirm(
                    "RESUME PURGE " + self.config["project"] + " " + recovery_id,
                    "An earlier purge passed all confirmations and began deleting resources. Resume only the remaining cleanup using the already verified recovery bundle.",
                )
                self.finish_purge_cleanup(
                    recovery_id, plan,
                    delete_backups=bool(pending.get("delete_backups")),
                    reset_admin_config=bool(pending.get("reset_admin_config")),
                )
                return
            raise Failure("An incomplete managed operation exists. Resolve it before purge so recovery state is unambiguous.")

        # Preliminary (advisory) plan: blockers refuse here, before any confirmation, pause or bundle.
        inventory = self.docker_inventory()
        preliminary = self.plan_for("purge", inventory, command="purge")
        self.write_private_json("inventory-preliminary.json", inventory.record())
        summary = self.instance_summary(preliminary)
        self.log_instance_summary(summary)
        if not summary["containers"] and not summary["volumes"] and not summary["state_present"] and not summary["env_present"]:
            raise Failure("No active or residual deployment state was found for this project.")
        if not (self.config_dir / ".env").is_file():
            raise Failure("config/.env is missing. A database volume cannot be safely destroyed without first proving a recoverable database backup.")
        self.database_ready()
        self.ensure_local_contract()

        confirm(
            "PURGE " + self.config["project"],
            "This is a destructive staging teardown. The selected project's exact containers, volumes, networks and covered PartFlow image tags listed above, runtime state, and config/.env are candidates for deletion. The writable repo, bind-mounted paths and installed control plane are retained. A verified recovery bundle is created before any destructive Docker deletion.",
        )

        # Stop writes first so the recovery bundle is a stable point-in-time state.
        self.pause("purge", project=self.config["project"])
        recovery = None
        checkpoint = None
        try:
            recovery, binding = self.create_purge_recovery(preliminary)
            checkpoint = self.verify_snapshot(recovery["active_checkpoint"])
            self.phase("recovery-ready", recovery=recovery["id"], active_checkpoint=checkpoint["id"])
            log("Recovery summary:")
            log("  Bundle: " + recovery["id"])
            log("  Path: " + str(self.recovery_root / recovery["id"]))
            log("  Active database: " + recovery["database"])
            log("  Preserved databases: " + ", ".join(item["name"] for item in recovery["databases"]))
            log("  Saved Docker image tags: " + str(len(recovery["saved_image_refs"])))
            log("  Revision checkpoints archived: yes")
            self.log_plan(binding, title="Binding deletion plan (frozen before the final confirmation):")

            # Second gate proves the operator understands which database becomes inaccessible.
            confirm(
                "DELETE " + recovery["database"],
                "The verified recovery bundle is complete. Continuing will remove the selected project's PostgreSQL Docker volume. The active database can be restored from the recovery bundle, but automatic merge into a later production history is intentionally not supported.",
            )

            if delete_backups is None:
                delete_backups = prompt_yes_no(
                    "Delete the normal revision checkpoint tree too (it is already archived inside the purge recovery bundle)",
                    default=True,
                )
            if delete_backups:
                confirm(
                    "DELETE BACKUPS " + self.config["project"],
                    "Normal revision checkpoints will be deleted from backups/revisions after the purge recovery bundle has preserved them.",
                )

            if reset_admin_config:
                confirm(
                    "RESET ADMIN CONFIG " + self.config["project"],
                    "config/pf-config.json will also be deleted. The next control command recreates it from the installed root-owned template.",
                )

            challenge = secrets.token_hex(3).upper()
            confirm(
                "ERASE " + self.config["project"] + " " + challenge,
                "FINAL CONFIRMATION. After this point the controller will start deleting Docker resources. Recovery bundle: "
                + recovery["id"],
            )
            reference = self.write_deletion_plan(binding)
            self.durable_phase(
                "deleting",
                recovery=recovery["id"],
                deletion_plan=reference,
                deleted=[],
                delete_backups=bool(delete_backups),
                reset_admin_config=bool(reset_admin_config),
            )
        except Exception as exc:
            # No destructive deletion has happened yet. Reopen the exact current
            # application if the recovery checkpoint was created successfully.
            if checkpoint is None and isinstance(exc, PlanChanged):
                checkpoint = exc.checkpoint
            if checkpoint is not None:
                try:
                    self.activate(checkpoint["images"], checkpoint["database_heads"])
                    if self.pending.exists():
                        self.pending.unlink()
                    log("Purge cancelled/failed before deletion; application services were restored.")
                except Exception as resume_exc:
                    log("WARNING: Could not automatically resume after pre-delete purge failure: " + str(resume_exc))
            raise

        self.finish_purge_cleanup(
            recovery["id"], binding,
            delete_backups=bool(delete_backups),
            reset_admin_config=bool(reset_admin_config),
        )

    def replace_source_for_recovery(self, candidate, revision, *, verified=False):
        """Restore the writable repository; executable control stays external/root-owned."""
        candidate = Path(candidate)
        # Runtime secrets/state never belong in the writable repository (v1 bundles carried them).
        for legacy in (candidate / ".env", candidate / "DEPLOYED_SOURCE.txt"):
            if legacy.exists():
                legacy.unlink()
        verified = bool(verified and isinstance(revision, str) and SHA_RE.fullmatch(revision))
        try:
            self.refuse_reserved_candidate_paths(candidate)
            source = {"kind": "git_commit", "commit": revision, "remote": self.approved_remote()} if verified \
                else {"kind": "unknown"}
            manifest = pf_source.build_manifest(candidate, source=source, excludes=SOURCE_EXCLUDES)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc
        for item in list(self.root.iterdir()):
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in candidate.iterdir():
            copy_tree_entry(item, self.root / item.name)
        self.publish_workspace_permissions()
        try:
            pf_source.write_manifest(self.context.source_manifest_path, manifest)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc

    def restore_runtime_environment(self, recovery, extracted_source=None):
        folder = Path(recovery["_folder"])
        source = folder / "configuration" / ".env"
        if not source.is_file() and extracted_source is not None:
            legacy = Path(extracted_source) / ".env"
            if legacy.is_file():
                source = legacy
        if not source.is_file():
            raise Failure("Recovery bundle does not contain a runtime .env; exact restore is refused.")
        self.config_dir.mkdir(mode=0o2770, parents=True, exist_ok=True)
        temporary = self.config_dir / (".env.restore-" + uuid.uuid4().hex[:8])
        shutil.copy2(source, temporary)
        os.chown(temporary, -1, self.workspace_gid)
        os.chmod(temporary, 0o660)
        os.replace(temporary, self.config_dir / ".env")
        self.publish_config_permissions()

    def restore_revision_checkpoints(self, recovery):
        archive = Path(recovery["_folder"]) / "revision-checkpoints.tar.gz"
        temporary = Path(tempfile.mkdtemp(prefix="restore-checkpoints-", dir=self.state))
        try:
            self.extract_tree_archive(archive, temporary)
            source = temporary / self.config["project"]
            if source.is_dir():
                if self.backups_dir.exists():
                    shutil.rmtree(self.backups_dir)
                self.backups_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, self.backups_dir)
                self.publish_backup_permissions(self.backups_dir)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def restore_instance(self, recovery, *, side_by_side=False):
        recovery = self.verify_recovery(recovery)
        folder = Path(recovery["_folder"])
        if recovery["postgres_major"] != 16:
            raise Failure("This recovery bundle is not PostgreSQL 16; automatic restore is refused.")

        if side_by_side:
            if recovery["project"] != self.config["project"]:
                raise Failure("Side-by-side recovery must come from the same PartFlow project.")
            self.database_ready()
            name = "pf_recovery_" + utc().lower().replace("t", "_").replace("z", "") + "_" + uuid.uuid4().hex[:6]
            name = name[:63]
            confirm(
                "RESTORE COPY " + name,
                "Restore the purged active database as an isolated recovery database. The current PartFlow application/database will not be changed. No automatic merge into Movement history will be attempted.",
            )
            self.restore_into(name, folder / "databases/active.dump")
            log("Recovery database created: " + name)
            log("It is intentionally not connected to the active application. Compare/export data explicitly; do not merge immutable Movement history by ad hoc SQL.")
            return

        if recovery["project"] != self.config["project"]:
            raise Failure("Exact restore must be run from the bootstrap root/config for the same project.")
        if Path(recovery["root"]).resolve() != self.root:
            raise Failure("Exact restore must run from the original repository root recorded in the recovery bundle.")
        if (self.state / "deployed.json").exists():
            raise Failure("A managed deployment record already exists. Exact restore refuses to overwrite it.")
        # PF-A1.3: exact inventory; any owned or blocking topology resource refuses before any confirmation.
        # Purge the current instance first, or use --side-by-side to recover data without replacing it.
        self.require_empty_target("restore-instance")

        log("Restore target summary:")
        log("  Project: " + recovery["project"])
        log("  Source: " + str(recovery.get("source_revision") or "unknown provenance"))
        log("  Active database: " + recovery["database"])
        log("  Preserved databases: " + ", ".join(item["name"] for item in recovery["databases"]))
        log("  Recovery bundle: " + recovery["id"])
        with tempfile.TemporaryDirectory(prefix="restore-source-", dir=self.state) as temp:
            candidate = Path(temp)
            archive = folder / (recovery.get("workspace_archive") or "source.tar.gz")
            extract_source(archive, candidate)
            # v1 bundles stored .env inside source.tar.gz; v2 stores it separately. Anything
            # else the manifest could not verify, and any provenance-proof failure, stops here:
            # before any confirmation, pending journal or configuration change. restore-instance
            # has no automatic journal route, so a refusal after the journal would wedge it.
            self.refuse_reserved_candidate_paths(candidate, allow=(".env",))
            restored_exact = not recovery.get("workspace_archive")
            verified = (restored_exact and bool(recovery.get("source_verified"))
                        and self.prove_tree_commit(candidate, recovery["source_revision"]))
            confirm(
                "RESTORE INSTANCE " + recovery["project"],
                "This recreates the purged functional instance from its recovery bundle. Docker container/network IDs are newly created. PostgreSQL globals are preserved as evidence but extra roles are not automatically executed.",
            )
            confirm(
                "RESTORE " + recovery["database"] + " " + recovery["id"],
                "Final restore confirmation. Repository workspace, runtime .env, application images, active database, retained databases, and revision checkpoints will be restored into an empty project. The current pf-config.json remains authoritative.",
            )

            write_json(self.pending, {
                "operation": "restore-instance", "phase": "confirmed", "started": utc(),
                "recovery": recovery["id"], "database": recovery["database"],
            })
            self.restore_runtime_environment(recovery, extracted_source=candidate)
            # The restored .env is the configuration this operation consumes from here on.
            self.freeze_app_config(explicit=True)
            self.replace_source_for_recovery(candidate, recovery["source_revision"], verified=verified)

        self.phase("loading-images")
        self.docker("image", "load", "-i", folder / "images.tar")
        self.verify_images(recovery["active_images"])
        self.make_override(recovery["active_images"], self.override)

        self.phase("starting-database")
        self.compose("up", "-d", "--no-deps", "db")
        self.wait_health("db")
        values = self.env()
        if values["POSTGRES_DB"] != recovery["database"] or values["POSTGRES_USER"] != recovery["database_user"]:
            raise Failure("Recovered .env database identity does not match the recovery manifest.")

        # Replace the empty init database with the verified logical dump.
        self.drop_database(recovery["database"])
        self.restore_into(recovery["database"], folder / "databases/active.dump")
        if self.db_heads(recovery["database"]) != recovery["database_heads"]:
            raise Failure("Restored active database Alembic revision does not match the recovery bundle.")

        for record in recovery["databases"]:
            if record["name"] == recovery["database"]:
                continue
            self.restore_into(record["name"], folder / record["dump"])
            if not record["allow_connections"]:
                self.sql("postgres", f"ALTER DATABASE {quote_identifier(record['name'])} ALLOW_CONNECTIONS false;",
                         mutation=True)

        self.phase("restoring-checkpoints")
        self.restore_revision_checkpoints(recovery)
        for name in recovery.get("state_files", []):
            source = folder / "state" / name
            if source.is_file() and name != "deployed.json":
                shutil.copy2(source, self.state / name)

        self.phase("activating")
        self.activate(recovery["active_images"], recovery["database_heads"])
        deployed_source = folder / "state/deployed.json"
        if deployed_source.is_file():
            shutil.copy2(deployed_source, self.state / "deployed.json")
        else:
            write_json(self.state / "deployed.json", {
                "sha": recovery["source_revision"], "ref": "restore:" + recovery["id"],
                "deployed_at": utc(), "checkpoint": recovery["active_checkpoint"],
            })
        if self.pending.exists():
            self.pending.unlink()
        log("Instance restore complete. Run UI/workflow/network smoke tests before accepting the recovered staging instance.")
        if recovery.get("missing_historical_image_refs"):
            log("WARNING: Some historical rollback image tags were already missing when the purge bundle was created. Those old rollback points remain source/data archives but may not be directly activatable.")
        log("postgres-globals.sql is preserved in the bundle for manual review; it was not executed automatically.")

    def update(self, target, *, automatic=False, allow_migrations=False, skip_ci=False):
        self.staging()
        self.database_ready()
        current = self.revision()
        workspace = self.workspace_status()
        workspace_matches_deployed = workspace["head"] == current and not workspace["dirty"]
        if current == target["sha"] and workspace_matches_deployed:
            log("Already at " + current + " with a clean matching workspace; no update needed.")
            return
        if automatic and not workspace_matches_deployed:
            raise Deferred("Automatic update refuses a workspace that differs from the deployed revision; run a manual update.")
        if not workspace_matches_deployed:
            log("WARNING: Writable repo differs from the deployed revision. A workspace archive will be included in the pre-update checkpoint before replacement.")
        if automatic and (not self.config["auto_update"] or not target.get("release_id")):
            raise Failure("Unattended update is disabled or the target is not a published release.")
        if automatic or not skip_ci:
            self.require_ci(target["sha"])
        else:
            log("WARNING: CI verification explicitly bypassed for this manual staging update.")
        self.free_space()
        current_contract = self.ensure_local_contract()
        with tempfile.TemporaryDirectory(prefix="candidate-", dir=self.state) as folder:
            candidate = Path(folder) / "repo"
            self.materialize_source(target, candidate)
            if automatic:
                self.automatic_guard(current, candidate, target["sha"])
            target_files = migration_files(candidate)
            # A changed contract is rejected before spending time building candidate images.
            if current_contract["files"] != target_files and not allow_migrations:
                raise Deferred("Migration files differ; review then use update --allow-migrations manually.")
            images, override = self.build_target(candidate, target["sha"])
            target_contract = self.image_contract(root=candidate, override=override)
            if target_contract["files"] != target_files:
                raise Failure("Built candidate image does not contain the selected migration source.")
            changed = schema_gate(current_contract["files"], target_files, self.db_heads(),
                                  target_contract["heads"], allow=allow_migrations and not automatic)
            if not automatic:
                confirm("UPDATE " + target["sha"][:12],
                        f"Deploy {target['ref']} -> {target['sha']}\nMigration required: {changed}. Application access will pause.")
            self.pause("update", target=target, migration_required=changed)
            checkpoint = self.snapshot("before-update")
            self.phase("backup-ready", checkpoint=checkpoint["id"])
            if changed:
                # Run on a clone of the data before touching the live database.
                rehearsal = "pf_migrate_" + uuid.uuid4().hex[:20]
                self.restore_into(rehearsal, self.backups_dir / checkpoint["id"] / "database.dump")
                self.compose("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head",
                             root=candidate, override=override, env={"POSTGRES_DB": rehearsal})
                if self.db_heads(rehearsal) != target_contract["heads"]:
                    raise Failure("Migration rehearsal did not reach the target head.")
                self.drop_database(rehearsal)
                self.phase("migrating-live")
                self.compose("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head",
                             root=candidate, override=override)
            self.replace_source(candidate, target["sha"])
            self.phase("activating", checkpoint=checkpoint["id"])
            self.activate(images, target_contract["heads"])
            write_json(self.state / "deployed.json", {**target, "deployed_at": utc(), "checkpoint": checkpoint["id"]})
            self.pending.unlink()
            log("Update complete. Previous revision checkpoint: " + checkpoint["id"])

    def swap_database(self, prepared):
        current = self.env()["POSTGRES_DB"]
        retained = "pf_keep_" + utc().lower() + "_" + uuid.uuid4().hex[:6]
        connections = self.sql("postgres", "SELECT count(*) FROM pg_stat_activity WHERE datname = '" + current + "';")
        if connections != "0":
            raise Failure("Other database sessions remain. Close IDE/psql connections; the script will not kill them.")
        self.phase("switching-database", database_switch={"current": current, "prepared": prepared, "retained": retained})
        self.sql("postgres", database_swap_sql(current, prepared, retained), mutation=True)
        self.phase("database-switched")
        log("Previous database retained (connections disabled): " + retained)
        return retained

    def rollback(self, requested=None, restore_database=False):
        self.staging()
        selected = self.choose_snapshot(requested)
        if not selected.get("source_verified"):
            raise Failure("This is an emergency data checkpoint, not a verified source rollback target.")
        if selected["database"] != self.env()["POSTGRES_DB"] or selected["database_user"] != self.env()["POSTGRES_USER"]:
            raise Failure("Checkpoint database identity differs from the current instance.")
        if selected["postgres_major"] != self.database_ready():
            raise Failure("Cross-major PostgreSQL restoration is not supported here.")
        self.verify_images(selected["images"])
        incomplete = self.pending.exists()
        previous_operation = load_json(self.pending) if incomplete else {}
        with tempfile.TemporaryDirectory(prefix="rollback-", dir=self.state) as folder:
            candidate = Path(folder)
            extract_source(self.backups_dir / selected["id"] / "source.tar.gz", candidate)
            self.refuse_reserved_candidate_paths(candidate)  # before any confirmation, pause or effect
            if migration_files(candidate) != selected["migration_files"]:
                raise Failure("Checkpoint migration fingerprint mismatch.")
            if not restore_database:
                if incomplete and not (previous_operation.get("operation") == "update"
                                       and previous_operation.get("migration_required") is False):
                    raise Failure("The incomplete operation may have changed data/schema; review recovery with --restore-db.")
                current_contract = self.ensure_local_contract()
                if current_contract["files"] != selected["migration_files"] or self.db_heads() != selected["database_heads"]:
                    raise Failure("Database/schema compatibility is not established. Code-only rollback refused; review --restore-db.")
            # The checkpoint's own flag does not assign provenance: the tree is `git_commit` only
            # when the protected store proves it, otherwise it is recorded as unknown. The proof
            # reads the store (and refuses a commit that tracks a reserved name), so it runs
            # before any confirmation, pause, snapshot or database swap.
            verified = self.prove_tree_commit(candidate, selected["source_revision"])
            phrase =("RESTORE " + self.env()["POSTGRES_DB"] + " " if restore_database else "ROLLBACK ") + selected["id"]
            confirm(phrase, ("Database will return to the selected backup time. Newer writes will no longer appear in the active app; the current DB is retained."
                             if restore_database else "Only code/images will change. Current data is kept. Schema equality does not prove all business-semantic compatibility."))
            self.pause("rollback", selected=selected["id"])
            emergency = self.snapshot("before-rollback", source_verified=not incomplete or not restore_database)
            self.phase("backup-ready", checkpoint=emergency["id"])
            retained = None
            if restore_database:
                prepared = "pf_restore_" + uuid.uuid4().hex[:20]
                self.restore_into(prepared, self.backups_dir / selected["id"] / "database.dump")
                if self.db_heads(prepared) != selected["database_heads"]:
                    raise Failure("Restored schema does not match the selected checkpoint.")
                retained = self.swap_database(prepared)
            self.replace_source(candidate, selected["source_revision"], verified=verified)
            self.phase("activating", checkpoint=emergency["id"])
            self.activate(selected["images"], selected["database_heads"])
            write_json(self.state / "deployed.json", {"sha": selected["source_revision"], "ref": "rollback:" + selected["id"],
                       "deployed_at": utc(), "checkpoint": emergency["id"], "retained_database": retained})
            self.pending.unlink()
            log("Rollback complete. Safety checkpoint: " + emergency["id"])

    def reset_database(self):
        self.staging()
        self.database_ready()
        contract = self.ensure_local_contract()
        database = self.env()["POSTGRES_DB"]
        confirm("RESET " + database,
                "All current application data, including configuration/master data, will be removed from the active instance. A verified backup and retained database are created first. This does not make staging production-ready.")
        self.pause("reset-db")
        checkpoint = self.snapshot("before-reset")
        self.phase("backup-ready", checkpoint=checkpoint["id"])
        prepared = "pf_clean_" + uuid.uuid4().hex[:20]
        self.create_database(prepared)
        override = self.state / "reset-images.yaml"
        self.make_override(checkpoint["images"], override)
        self.compose("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head",
                     override=override, env={"POSTGRES_DB": prepared})
        if self.db_heads(prepared) != contract["heads"]:
            raise Failure("The clean database did not reach the current schema head.")
        retained = self.swap_database(prepared)
        self.phase("activating", checkpoint=checkpoint["id"])
        self.activate(checkpoint["images"], contract["heads"])
        write_json(self.state / "last-reset.json", {"time": utc(), "checkpoint": checkpoint["id"], "retained_database": retained})
        self.pending.unlink()
        log("Clean database is active. Configure Departments/Areas/Operations/Stations again.")

    def resume(self):
        self.staging()
        if not self.pending.exists():
            raise Failure("No interrupted operation exists.")
        pending = load_json(self.pending)
        if pending["phase"] not in ("paused", "backup-ready"):
            raise Failure("Source/database may have changed. Use the recorded checkpoint with rollback --restore-db instead.")
        self.database_ready()
        contract = self.ensure_local_contract()
        confirm("RESUME " + self.env()["POSTGRES_DB"], "Resume the unchanged deployment after a pre-change failure.")
        images = self.retain_images(utc().lower() + "-resume-" + uuid.uuid4().hex[:6])
        self.activate(images, contract["heads"])
        self.pending.unlink()

    def permissions(self):
        self.require_trusted_context()
        # Refuse every unsafe target before the first effect on any tree.
        for tree in (self.root, self.config_dir, self.backups_dir, self.recovery_root):
            if tree.is_dir():
                self.refuse_multiply_linked(tree)
        self.ensure_backup_tree()
        self.ensure_recovery_tree()
        self.publish_workspace_permissions()
        self.publish_config_permissions()
        self.publish_backup_permissions(self.backups_dir)
        self.publish_backup_permissions(self.recovery_root)
        log("Permissions normalized.")
        log("  repo/: group=" + self.config["workspace_write_group"] + " full read/write/delete via directory group-write")
        log("  config/: group=" + self.config["workspace_write_group"] + " read/write")
        log("  backups/, recovery/: group=" + self.config["backup_read_group"] + " read/copy only")
        log("  control/: users read-only, root-owned/root-modifiable; .pf-state-*: root only")

    def live_sections(self, sections):
        """Run read-only probes one by one; report each unavailable section instead of stopping."""
        unavailable = []
        reported = False
        for label, probe in sections:
            try:
                log(f"{label}: {probe()}")
            except DaemonFailure as exc:
                # PF-A1.3: the cached daemon refusal; no further transport was attempted.
                unavailable.append(label)
                log(f"{label}: unavailable: {exc.code}")
                if not reported:
                    log("  " + str(exc).split(": ", 1)[-1])
                    reported = True
            except (Failure, OSError, ValueError, KeyError) as exc:
                unavailable.append(label)
                detail = str(exc).strip().splitlines()
                log(f"{label}: unavailable: {detail[0] if detail else type(exc).__name__}")
        return unavailable

    def log_registered_tools(self):
        """Offline: which host executables the bootstrap registered and whether each resolves as trusted."""
        try:
            rows = self.runner.describe()
        except Failure as exc:
            log("Registered tools: unavailable: " + str(exc).splitlines()[0])
            return
        parts = []
        for tool, state, detail in rows:
            if state == "ok":
                parts.append(f"{tool}={detail}")
            elif state == "unregistered":
                parts.append(f"{tool}=unregistered")
            else:
                parts.append(f"{tool}=REFUSED ({detail.split(':', 1)[0]})")
        log("Registered tools (bootstrap/tools.conf; PATH is never searched): " + ", ".join(parts))

    def env_presence(self):
        path = self.config_dir / ".env"
        if not path.is_file():
            raise Failure("missing: " + str(path))
        return "present"

    def env_status(self):
        self.env_presence()
        self.env()
        return "present and valid"

    def describe_zone_data(self):
        """PF-A2.2 (read-only): SITE_TIMEZONE against the host's installed zone data. Runtime operations keep the
        A1 grammar check; the backend validates the value with its own image zone data at startup."""
        name = self.load_app_env()["SITE_TIMEZONE"]
        status, detail = pf_config.zone_status(name)
        if status == "ok":
            return f"SITE_TIMEZONE {name}: ok in {detail}"
        if status == "zone-data-unavailable":
            return f"SITE_TIMEZONE {name}: host zone data unavailable (searched {detail})"
        return f"SITE_TIMEZONE {name}: unknown in host zone data (the backend checks its own zone data at startup)"

    def doctor(self):
        log(f"PartFlow NAS Admin {VERSION} ({CHECKPOINT} checkpoint)")
        self.log_context()
        self.log_installation()
        validation = self.ensure_validation()
        self.log_validation()
        self.log_journal(self.read_journal(), trusted=validation.private_state_trusted)
        self.log_effects()
        log("Python: " + sys.version.split()[0])
        self.log_registered_tools()
        if not validation.mutation_allowed:
            self.refuse_live_checks("protected context refused (" + ", ".join(validation.refused_codes()) + ")")
        try:
            self.ensure_config()
        except Failure as exc:
            log("Runtime configuration: unavailable: " + str(exc).splitlines()[0])
            self.refuse_live_checks("runtime configuration rejected")
        unavailable = self.live_sections((
            ("Git", lambda: self.command(["git", "--version"], effect=None)),
            ("Docker daemon", self.describe_daemon),
            ("Runtime configuration", lambda: (
                f"ok | project: {self.config['project']} | environment: {self.config['environment']}"
                f" | auto-update: proposal {str(self.config['auto_update']).lower()} (unattended apply not permitted"
                f" by protected policy revision {self.context.approved_policy.revision})"
                f" | channel: {self.config['release_channel']}"
                f" | workspace group: {self.config['workspace_write_group']}"
                f" | backup group: {self.config['backup_read_group']}"
                + (" | schema 2" if self.config_schema_version == 2
                   else f" | schema 1 (legacy; '{self.pf_command()} config admin' makes it explicit)")
            )),
            ("Runtime .env", self.env_status),
            ("Timezone data", self.describe_zone_data),
            ("Compose envelope", self.describe_envelope),
            ("Free space", lambda: self.free_space() or "ok"),
        ))
        log("Database volume capacity, NAS recovery, and production readiness are not certified by doctor.")
        log("Default doctor is read-only: it created, repaired and migrated nothing.")
        if unavailable:
            raise Failure("Doctor found unavailable components: " + ", ".join(unavailable))

    def status(self):
        # Identity, trust summary and journal come from protected state and are
        # shown before any app config, .env, Git or Docker access (A1-T16). A refused
        # context or a rejected configuration ends here with zero transport calls (A11-R05).
        self.log_context()
        self.log_installation()
        validation = self.log_trust_summary()
        self.log_journal(self.read_journal(), trusted=validation.private_state_trusted)
        self.log_effects()
        if not validation.mutation_allowed:
            self.refuse_live_checks("protected context refused (" + ", ".join(validation.refused_codes()) + ")")
        try:
            self.ensure_config()
        except Failure as exc:
            log("Runtime configuration: unavailable: " + str(exc).splitlines()[0])
            self.refuse_live_checks("runtime configuration rejected")
        log("Runtime configuration: ok | project: " + self.config["project"])
        unavailable = self.live_sections((
            ("Docker daemon", self.describe_daemon),
            ("Managed resources", self.describe_inventory),
            ("Runtime .env", self.env_presence),
            ("Deployed source", self.revision),
            ("Workspace", self.describe_workspace),
            ("Revision checkpoints", lambda: str(len(self.snapshots()))),
            ("Database revisions", lambda: ", ".join(self.db_heads()) or "uninitialized"),
            ("Compose services", lambda: "\n" + self.compose("ps")),
        ))
        if unavailable:
            raise Failure("Status is partial; live data unavailable for: " + ", ".join(unavailable))

    def describe_workspace(self):
        workspace = self.workspace_status()
        try:
            deployed = self.revision()
        except Failure:
            deployed = None
        differs = workspace["dirty"] or (deployed is not None and workspace["head"] != deployed)
        text = (f"provenance {workspace['provenance']} | manifest commit {workspace['head'] or 'none'}"
                f" | differs from deployed: {differs}")
        if workspace["changes"]:
            text += " | changes: " + ", ".join(workspace["changes"][:10])
        return text

    def compose_ps(self, args):
        """``pf ps``: one ``compose ps`` rebuilt from the parsed options only (PF-A1.4).

        Read-only: no lock, operation directory, envelope or inventory. The Compose argv carries the
        fixed project, files, env-file and directory; no operator token is forwarded as typed.
        """
        services = checked_services(args.services)
        argv = ["ps"]
        if args.all:
            argv.append("--all")
        if args.quiet:
            argv.append("--quiet")
        if args.services_only:
            argv.append("--services")
        if args.status:
            argv += ["--status", args.status]
        if args.format:
            argv += ["--format", args.format]
        self.compose(*argv, *services, stream=True, timeout=TIMEOUT_DIAGNOSTIC)

    def compose_logs(self, args):
        """``pf logs``: bounded, redacted ``compose logs`` rebuilt from the parsed options only (PF-A1.4).

        ``--follow`` ends at TIMEOUT_LOGS_FOLLOW or at the runner's stream cap; either bound ends with
        ``logs-bound-reached`` (decided from the runner's result for this child, never from its text).
        """
        services = checked_services(args.services)
        argv = ["logs", "--tail", str(args.tail)]
        if args.follow:
            argv.append("--follow")
        if args.timestamps:
            argv.append("--timestamps")
        if args.no_color:
            argv.append("--no-color")
        if args.no_log_prefix:
            argv.append("--no-log-prefix")
        if args.since:
            argv += ["--since", args.since]
        if args.until:
            argv += ["--until", args.until]
        timeout = TIMEOUT_LOGS_FOLLOW if args.follow else TIMEOUT_DIAGNOSTIC
        # The daemon probe and the Compose version run first, so the next child is `logs` itself.
        self.compose_cli()
        started = len(self.runner.history)
        try:
            self.compose(*argv, *services, stream=True, timeout=timeout)
        except DaemonFailure:
            raise
        except Failure as exc:
            results = self.runner.history[started:]
            if results and results[-1].timed_out and "logs" in results[-1].argv:
                raise Failure(
                    f"logs-bound-reached: 'pf logs' stopped at its bound ({timeout:.0f} s or 64 MiB of output); the "
                    "output above is complete up to that point. Nothing was changed.") from exc
            raise


def checked_services(names):
    """Service names for ``pf ps``/``pf logs``: only the managed topology's services (pf_docker.SERVICES)."""
    for name in names:
        if name not in pf_docker.SERVICES:
            raise Failure(f"unknown service '{name}'; services: {', '.join(pf_docker.SERVICES)}")
    return list(names)


def project_name(value):
    """argparse type of ``--project``: a Compose project name (PROJECT_RE); never a path component."""
    if not PROJECT_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid Compose project name {value!r} (lowercase letters, digits, "
                                         "'_' and '-', at most 40 characters)")
    return value


def tail_count(value):
    """argparse type of ``pf logs --tail``."""
    if not re.fullmatch(r"\d{1,5}", value) or not 1 <= int(value) <= LOGS_MAX_TAIL:
        raise argparse.ArgumentTypeError(f"--tail must be an integer from 1 to {LOGS_MAX_TAIL}")
    return int(value)


def since_value(value):
    """argparse type of ``pf logs --since/--until`` (SINCE_RE)."""
    if not SINCE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a duration such as 30m or 2h, or an RFC 3339 date/time")
    return value


def parser():
    # PF-A1.4: abbreviations are refused everywhere (OD-A14-10); `pf --inst x` is unknown-option.
    result = argparse.ArgumentParser(description="PartFlow NAS staging administration; use --help on a command.",
                                     allow_abbrev=False)
    result.add_argument("--instance", help="Registered instance slug or UUID; required when several instances exist and no protected default is set")
    subs = result.add_subparsers(dest="command", required=True)

    def add(name, **kwargs):
        return subs.add_parser(name, allow_abbrev=False, **kwargs)

    for name in ("doctor", "status", "permissions", "backup", "reset-db", "resume", "abort-deploy"):
        add(name)

    add("instances", help="List registered instances from the protected registry (no Docker access)")
    pf_install.add_parser(subs)

    # PF-A2.2: the config wizards. --configuration/--project select the pre-registration mode of `config admin`;
    # `config app` refuses them (check_config_options), so they are accepted there only to name the refusal.
    config = add("config", help="Create, migrate or complete pf-config.json (admin) or .env (app) with a wizard")
    verbs = config.add_subparsers(dest="config_verb", required=True)
    for verb, text in (("admin", "Admin configuration wizard (pf-config.json; schema migration 1 -> 2)"),
                       ("app", "Application variable wizard (.env) for the record's profile")):
        command = verbs.add_parser(verb, allow_abbrev=False, help=text)
        command.add_argument("--configuration", help=argparse.SUPPRESS if verb == "app" else
                             "Pre-registration mode: an unregistered configuration directory (no --instance)")
        command.add_argument("--project", help=argparse.SUPPRESS if verb == "app" else
                             "Pre-registration mode: the Compose project the file names")

    deploy = add("deploy", help="Create a brand-new managed staging deployment")
    deploy_selection = deploy.add_mutually_exclusive_group()
    deploy_selection.add_argument("--current", action="store_true", help="Deploy the current clean Git checkout (default when no selector is given)")
    deploy_selection.add_argument("--latest", action="store_true", help="Resolve the configured branch tip to a fixed SHA and deploy it")
    deploy_selection.add_argument("--commit", help="Deploy one explicit Git commit SHA")
    deploy_selection.add_argument("--release", help="Deploy a published release tag or 'latest'")
    deploy.add_argument("--channel", choices=("stable", "prerelease"))
    deploy.add_argument("--skip-ci", action="store_true", help="Explicit manual staging exception; CI is checked by default")

    purge = add("purge", help="Create a full recovery bundle, then remove one managed staging instance")
    purge.add_argument("--project", type=project_name, help="Legacy alias: select the registered instance whose Compose project is unique; prefer --instance")
    backup_policy = purge.add_mutually_exclusive_group()
    backup_policy.add_argument("--delete-backups", action="store_true", help="Delete normal revision checkpoints after archiving them into the recovery bundle")
    backup_policy.add_argument("--keep-backups", action="store_true", help="Keep normal revision checkpoints after purge")
    purge.add_argument("--reset-admin-config", action="store_true", help="Also delete config/pf-config.json after a separate confirmation")

    recoveries = add("recoveries", help="List verified purge-recovery bundle candidates of the selected instance")
    recoveries.add_argument("--page", type=int, default=1)
    recoveries.add_argument("--project", type=project_name, help="Legacy alias for selection; with --instance it must name that instance's project")

    restore = add("restore-instance", help="Restore a purged instance or recover its old database side-by-side")
    restore.add_argument("recovery_id", nargs="?")
    restore.add_argument("--project", type=project_name, help="Legacy alias for selection; the restore target and the bundles listed are always the selected registered instance's own")
    restore.add_argument("--side-by-side", action="store_true", help="Restore only the old active database under a separate recovery DB name; do not replace the current instance")

    backups = add("backups", help="List revision checkpoints, newest first, 10 per page")
    backups.add_argument("--page", type=int, default=1)
    rollback = add("rollback")
    rollback.add_argument("backup_id", nargs="?")
    rollback.add_argument("--restore-db", action="store_true")
    update = add("update")
    selection = update.add_mutually_exclusive_group()
    selection.add_argument("--latest", action="store_true", help="Resolve the current configured branch tip to a fixed SHA")
    selection.add_argument("--commit")
    selection.add_argument("--release", help="Published tag or 'latest' (default)")
    update.add_argument("--channel", choices=("stable", "prerelease"))
    update.add_argument("--allow-migrations", action="store_true")
    update.add_argument("--skip-ci", action="store_true", help="Explicit manual staging exception; never used by the scheduler")
    check = add("release-check")
    check.add_argument("--channel", choices=("stable", "prerelease"))
    check.add_argument("--apply", action="store_true",
                       help="refused in this checkpoint: needs a protected policy grant (PF-A4.3)")

    ps = add("ps", help="Show the instance's Compose containers (read-only)")
    ps.add_argument("-a", "--all", action="store_true")
    ps.add_argument("-q", "--quiet", action="store_true")
    ps.add_argument("--services", dest="services_only", action="store_true", help="List service names only")
    ps.add_argument("--status", choices=PS_STATUSES)
    ps.add_argument("--format", choices=("table", "json"))
    ps.add_argument("services", nargs="*", metavar="service", help="db, backend or frontend")

    logs = add("logs", help="Show bounded, redacted service logs (read-only)")
    logs.add_argument("--tail", type=tail_count, default=LOGS_DEFAULT_TAIL,
                      help=f"Lines per service, 1-{LOGS_MAX_TAIL} (default {LOGS_DEFAULT_TAIL})")
    logs.add_argument("-f", "--follow", action="store_true",
                      help=f"Follow; ends after {TIMEOUT_LOGS_FOLLOW:.0f} s or 64 MiB of output")
    logs.add_argument("-t", "--timestamps", action="store_true")
    logs.add_argument("--no-color", action="store_true")
    logs.add_argument("--no-log-prefix", action="store_true")
    logs.add_argument("--since", type=since_value, help="Duration such as 30m or 2h, or an RFC 3339 date/time")
    logs.add_argument("--until", type=since_value, help="Duration such as 30m or 2h, or an RFC 3339 date/time")
    logs.add_argument("services", nargs="*", metavar="service", help="db, backend or frontend")
    return result


# ------------------------------------------------------------ explicit dispatch (PF-A1.4)
# Every CLI word is one row here; main() derives its gates from the row and runs the handler in
# exactly one branch. There is no catch-all route: a word outside DISPATCH is a named refusal.


@dataclasses.dataclass(frozen=True)
class Route:
    name: str
    mutability: str      # "read-only" | "registry-read" | "mutating" | "conditional" (mutating only with --apply)
                         # | "installation" (pf install: its own operation journal and locks, PF-A2.1)
    lock: bool
    trusted_launch: bool
    trusted_context: bool
    pending: str         # "any" | "refuse" | the route's own name (it has a PENDING_ROUTES entry)
    preflight: str       # "none" | "owned" | "empty-target" | "plan" | "restore" | "apply" (where the topology check runs)
    fail_closed: str     # "never" | "always" | "unless-side-by-side" | "if-apply"
    unattended: str      # "allowed" | "terminal" | "policy"
    policy_class: str    # "" | "backup" | "permissions" | "release-check"
    handler: str         # dotted name; DT-4 proves it resolves


def _read_only(name, handler, *, mutability="read-only", trusted=False):
    return Route(name, mutability, False, trusted, trusted, "any", "none", "never", "allowed", "", handler)


def _locked(name, mutability, pending, preflight, fail_closed, unattended_rule, policy_class, handler):
    return Route(name, mutability, True, True, True, pending, preflight, fail_closed, unattended_rule, policy_class,
                 handler)


DISPATCH = {route.name: route for route in (
    _read_only("instances", "display_registry", mutability="registry-read"),
    _read_only("status", "Controller.status"),
    _read_only("doctor", "Controller.doctor"),
    _read_only("backups", "Controller.display_page"),
    _read_only("recoveries", "Controller.display_recoveries"),
    _read_only("ps", "Controller.compose_ps", trusted=True),
    _read_only("logs", "Controller.compose_logs", trusted=True),
    # PF-A2.1: installer verbs; unlocked here (each verb takes its own locks inside pf_install), a terminal
    # and a trusted launch for every verb except `install status`.
    Route("install", "installation", False, True, False, "any", "none", "never", "terminal", "",
          "pf_install.run_installed"),
    _locked("permissions", "mutating", "refuse", "none", "never", "policy", "permissions", "Controller.permissions"),
    _locked("deploy", "mutating", "refuse", "empty-target", "always", "terminal", "", "Controller.deploy"),
    _locked("abort-deploy", "mutating", "abort-deploy", "plan", "always", "terminal", "", "Controller.abort_deploy"),
    _locked("purge", "mutating", "purge", "plan", "never", "terminal", "", "Controller.purge"),
    _locked("restore-instance", "mutating", "refuse", "restore", "unless-side-by-side", "terminal", "",
            "Controller.restore_instance"),
    _locked("backup", "mutating", "refuse", "owned", "never", "policy", "backup", "Controller.snapshot"),
    _locked("reset-db", "mutating", "refuse", "owned", "always", "terminal", "", "Controller.reset_database"),
    _locked("rollback", "mutating", "rollback", "owned", "always", "terminal", "", "Controller.rollback"),
    _locked("resume", "mutating", "resume", "owned", "always", "terminal", "", "Controller.resume"),
    _locked("update", "mutating", "refuse", "owned", "always", "terminal", "", "Controller.update"),
    _locked("release-check", "conditional", "refuse", "apply", "if-apply", "policy", "release-check",
            "Controller.resolve"),
    # PF-A2.2: the config wizards (registered mode); pre-registration mode leaves main() before any registry read
    # of the instance path (config_admin_unregistered).
    _locked("config", "mutating", "refuse", "none", "never", "terminal", "", "Controller.configure"),
)}
KNOWN_COMMANDS = frozenset(DISPATCH)
# Commands that never take the instance lock and never mutate managed state.
READ_ONLY_COMMANDS = frozenset(name for name, route in DISPATCH.items() if route.mutability in ("read-only",
                                                                                              "registry-read"))
# Effect classifier only (compose_effect): Compose verbs that change nothing. No route forwards these
# words; `ps` and `logs` are rebuilt from parsed options by Controller.compose_ps/compose_logs.
COMPOSE_READ_ONLY_VERBS = {"ps", "logs", "version", "top", "images", "port", "ls", "events", "stats"}

# Former catch-all Compose words (word -> guidance group). Each is a named refusal before anything is read.
REMOVED_COMPOSE_ROUTES = {
    **{word: "start" for word in ("up", "start", "restart", "create", "scale", "watch", "unpause")},
    **{word: "stop" for word in ("down", "stop", "kill", "pause", "rm")},
    **{word: "oneoff" for word in ("run", "exec", "cp", "attach")},
    **{word: "image" for word in ("build", "pull", "push")},
    **{word: "view" for word in ("version", "top", "images", "port", "ls", "events", "stats", "wait")},
}
REMOVED_ROUTE_GUIDANCE = {
    "start": "Containers are started only by managed commands: pf resume (reopen after a pre-change failure), "
             "pf update, pf rollback, pf deploy (first deployment) or pf restore-instance.",
    "stop": "Containers are stopped or removed only by managed commands: pf purge (with a verified recovery "
            "bundle) or pf abort-deploy (incomplete first deployment); a managed stop/start arrives with PF-A4. "
            "Volumes are never removed outside those flows.",
    "oneoff": "One-off containers, shells and file copies are not available. Inspect with pf status, pf ps or "
              "pf logs; change data with pf backup, pf reset-db or pf rollback --restore-db.",
    "image": "Images are built only by pf deploy and pf update from the protected source store.",
    "view": "Only ps and logs are available as read-only Compose views; pf doctor reports the Compose version.",
    # PF-A2.2: still used: `pf config` without admin/app is refused with this guidance (classify_command).
    "config": "Raw compose config output is not available through this route; use pf doctor, which validates the "
              "resolved model privately.",
}
CONFIG_VERBS = ("admin", "app", "-h", "--help")
# Leading Compose/Docker global options: the project, files, env-file, directory and daemon are fixed.
COMPOSE_GLOBAL_OPTIONS = frozenset({
    "-f", "--file", "-p", "--project-name", "--project-directory", "--env-file", "--profile",
    "--parallel", "--progress", "--ansi", "--compatibility", "--dry-run", "--all-resources",
    "-c", "--context", "-H", "--host", "--config", "-D", "--debug", "-l", "--log-level",
    "--tls", "--tlscacert", "--tlscert", "--tlskey", "--tlsverify",
})


@dataclasses.dataclass(frozen=True)
class EntryRoute:
    id: str              # "E1".."E11"
    entry: str
    target_route: str    # a DISPATCH name | "*" (every DISPATCH route) | "refuse" | "fail_closed" | "installer-init"
    mutability: str      # target's mutability | "per-route" for "*" | "none" for "refuse" | "mutating"
    lock: str            # "per-route" | "none" | "held" (the failing operation's own lock, until finally)
    pending: str         # "per-route" | "n/a" | "existing-journal" (fail_closed acts only when one exists)
    test: str            # "module.Class.test_name" in tests/
    owner: str           # "PF-A1.4" or the later owner of a static-only row


# Non-CLI entry routes (audit data; DT-8 proves every row against DISPATCH and its named test).
ENTRY_ROUTES = (
    EntryRoute("E1", "repository pf.sh", "refuse", "none", "none", "n/a",
               "test_pf_admin.PureTests.test_repository_launcher_refuses_operational_execution", "PF-A1.4"),
    EntryRoute("E2", "<root>/bootstrap/pf", "*", "per-route", "per-route", "per-route",
               "test_entry_routes.InstalledLauncherRoutes.test_cli1_removed_routes_through_the_installed_launcher",
               "PF-A1.4"),
    EntryRoute("E3", "legacy <home>/control/pf.sh", "refuse", "none", "none", "n/a",
               "test_entry_routes.SchedulerWrappers.test_sw4_legacy_control_wrapper_reports_the_command_word",
               "PF-A1.4"),
    EntryRoute("E4", "global launcher (created by install-control.sh init when absent) -> <root>/bootstrap/pf", "*",
               "per-route", "per-route", "per-route",
               "test_install.LauncherBinding.test_lb2_global_launcher_execs_the_bootstrap", "PF-A2.1"),
    EntryRoute("E5", "backup.sh", "backup", "mutating", "yes", "refuse",
               "test_entry_routes.SchedulerWrappers.test_sw6_installed_wrappers_reach_the_policy_gates", "PF-A1.4"),
    EntryRoute("E6", "release-check.sh", "release-check", "conditional", "yes", "refuse",
               "test_entry_routes.SchedulerWrappers.test_sw6_installed_wrappers_reach_the_policy_gates", "PF-A1.4"),
    EntryRoute("E7", "python pf-admin.py without the handshake", "refuse", "none", "none", "n/a",
               "test_instance_context.PendingJournalVisibility."
               "test_legacy_control_directory_is_diagnostics_only_and_executes_no_payload", "PF-A1.4"),
    EntryRoute("E8", "pf_bootstrap.py run from elsewhere", "refuse", "none", "none", "n/a",
               "test_entry_routes.DispatchTables.test_e8_verifier_refuses_outside_bootstrap", "PF-A1.4"),
    EntryRoute("E9", "main() except -> fail_closed()", "fail_closed", "mutating", "held", "existing-journal",
               "test_entry_routes.ErrorHandler.test_eh1_only_owned_oneoffs_are_stopped_then_the_application",
               "PF-A1.4"),
    EntryRoute("E10", "SIGINT/SIGTERM/SIGHUP/SIGQUIT", "fail_closed", "mutating", "held", "existing-journal",
               "test_entry_routes.ErrorHandler."
               "test_eh7_a_real_signal_through_the_installed_launcher_runs_fail_closed_before_the_lock_is_free",
               "PF-A1.4"),
    EntryRoute("E11", "install-control.sh", "installer-init", "mutating", "none", "install-journal",
               "test_install.RepositoryInstaller.test_ri1_init_end_to_end", "PF-A2.1"),
)


def classify_command(rest):
    """Classify the words after the global options before any registry read (PF-A1.4); pure.

    Returns ("route", name) or ("help", None); every other word raises a named Failure. No refusal
    here reads the registry, builds a Controller, takes a lock or starts a process.
    """
    if not rest:
        return "route", "ps"  # v2.5 compatibility: `pf` alone is the read-only `pf ps`
    word = rest[0]
    if word in ("-h", "--help"):
        return "help", None
    if word == "config" and rest[1:2] and rest[1] in CONFIG_VERBS:
        return "route", word
    if word == "config":
        # PF-A2.2: the former Compose alias is the config group; any other spelling keeps the removed-route refusal.
        raise Failure("compose-route-removed: 'pf config' without 'admin' or 'app' no longer forwards to Docker "
                      f"Compose. {REMOVED_ROUTE_GUIDANCE['config']} Use 'pf config admin' or 'pf config app'. Nothing "
                      "was read or changed.")
    if word in DISPATCH:
        return "route", word
    if word in REMOVED_COMPOSE_ROUTES:
        raise Failure(f"compose-route-removed: 'pf {word}' no longer forwards to Docker Compose. "
                      f"{REMOVED_ROUTE_GUIDANCE[REMOVED_COMPOSE_ROUTES[word]]} Nothing was read or changed.")
    if word.startswith("-"):
        option = word.split("=", 1)[0] if word.startswith("--") else word[:2]
        if option in COMPOSE_GLOBAL_OPTIONS:
            raise Failure(f"compose-override-refused: '{option}' is a Compose or Docker global option; the project, "
                          "files, env-file, project directory and daemon are fixed by the controller. Nothing was "
                          "read or changed.")
        raise Failure(f"unknown-option: '{option}' is not a pf option; only --instance may precede the command, and "
                      "abbreviations are refused. Nothing was read or changed.")
    raise Failure(f"unknown-command: Unknown command '{word}'. Managed commands: {', '.join(sorted(KNOWN_COMMANDS))}. "
                  "Read-only Compose views: ps, logs. Nothing was read or changed.")


def route_preflight(route, args):
    """The topology preflight main() runs right after the lock: "owned" or "none" (PF-A1.3 OD-A13-03).

    deploy, purge, abort-deploy and exact restore-instance run their stronger empty-target or frozen-plan
    checks inside the handler instead.
    """
    if route.preflight == "owned":
        return "owned"
    if route.preflight == "restore" and args.side_by_side:
        return "owned"
    if route.preflight == "apply" and args.apply:
        return "owned"
    return "none"


def route_fail_closed(route, args):
    """Whether an exception inside this route's locked body runs fail_closed()."""
    if route.fail_closed == "always":
        return True
    if route.fail_closed == "unless-side-by-side":
        return not args.side_by_side
    if route.fail_closed == "if-apply":
        return bool(args.apply)
    return False


def require_attended(route, args, controller, *, explicit_instance, selected_by):
    """The unattended gate (PF-A1.4, ARCH section 5): applied to every locked route before the lock.

    Without a terminal a confirmation route is refused, every other route must name its instance and
    then needs a protected policy grant for its operation class. ``release-check --apply`` is left to
    the auto-apply gate, which gives the specific code.
    """
    if not unattended():
        return
    slug = controller.context.slug
    if route.unattended == "terminal":
        raise Failure(f"terminal-required: '{route.name}' asks for a typed confirmation and cannot run without a "
                      "terminal (scheduled task, script, or ssh without -t). Run it interactively: sudo pf --instance "
                      f"{slug} {route.name}. Nothing was changed.")
    if not explicit_instance:
        raise Failure(f"instance-required-unattended: '{route.name}' is running without a terminal and selected "
                      f"instance {slug} by {selected_by}. Unattended commands must name the instance: pf --instance "
                      f"<slug|uuid> {route.name}. Nothing was changed.")
    automatic_apply = route.mutability == "conditional" and bool(getattr(args, "apply", False))
    if route.unattended == "policy" and not automatic_apply and not controller.policy_permits(route.policy_class):
        raise Deferred(f"policy-grant-required: unattended '{route.name}' needs a protected policy that permits this "
                       f"class of operation; approved policy revision {controller.context.approved_policy.revision} "
                       f"of instance {slug} permits no unattended operation in this checkpoint (grants arrive with "
                       f"PF-A4.3). Run it from an interactive terminal: sudo pf --instance {slug} {route.name}. "
                       "Nothing was changed.")


def display_registry(registry):
    """Read-only listing of the protected registry: records, journals and unpublished registrations."""
    root = registry.root
    default = registry.default_instance_id
    log(f"Registered instances at {root} | default: {default or 'none (explicit --instance required when several exist)'}")
    for line in pf_install.describe_installation(root):
        log(line)
    rows = registry.records()
    if not rows:
        log("  (no registrations)")
    for number, (entry, context, error) in enumerate(rows, 1):
        marker = " [default]" if entry.instance_id == default else ""
        if context is None:
            log(f"{number:>3}. {entry.slug}  {entry.instance_id}{marker}  record=INVALID: {error}")
            continue
        journal_path = context.journal_path
        try:
            journal = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(journal_path), label=str(journal_path))
            journal_text = f"{journal.get('operation')}/{journal.get('phase')}" if isinstance(journal, dict) else "unreadable"
        except FileNotFoundError:
            journal_text = "none"
        except (OSError, pf_instance.ContextError):
            journal_text = "unreadable"
        log(
            f"{number:>3}. {context.slug}  {context.instance_id}{marker}  project={context.compose_project}"
            f"  environment={context.approved_environment}  state={context.state}"
            f"  control={context.control.release_id}  journal={journal_text}\n"
            f"     workspace={context.paths.workspace}"
        )
    for pending in pf_instance.pending_registrations(root):
        if pending.kind == "reservation" and pending.record is not None:
            log(f"PENDING registration {pending.instance_id} slug={pending.slug} project={pending.record['compose_project']}"
                " (durable reservation of an interrupted registration; rerun the same registration to complete it,"
                " or have an administrator remove the reservation explicitly)")
        else:
            log(f"UNKNOWN state: {pending.path}: {pending.error} (not deleted; an administrator must inspect it)")
    for (engine_id, project), owners in sorted(pf_instance.daemon_project_conflicts(registry).items()):
        log(f"CONFLICT: compose project {project!r} on daemon {engine_id} is claimed by {', '.join(owners)}; "
            "every party refuses mutation until an administrator repairs the registry (no record is chosen automatically)")
    return len(rows)


def bind_installation_root(root, running_release):
    """The root handed over by the bootstrap must be the installation whose pinned control
    release is the code running now. Nothing (registry, journals, records) is read from a root
    that fails this binding; the verifier already proved the real root before exec."""
    root = Path(root)
    reason = pf_instance.canonical_path_error(str(root))
    if reason is not None:
        raise Failure(f"Installation root {root!s} is not one canonical absolute POSIX path ({reason}); nothing was read.")
    conf_path = root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME
    try:
        conf = pf_instance.pf_bootstrap.parse_bootstrap_conf(
            pf_instance.read_bytes_nofollow(conf_path), label=str(conf_path), error=pf_instance.ContextError)
    except (OSError, UnicodeDecodeError, pf_instance.ContextError) as exc:
        raise Failure(f"Installation root {root} has no usable bootstrap configuration ({exc}); nothing was read.") from exc
    if running_release is not None and Path(conf["control_release"]) != Path(running_release):
        raise Failure(
            f"Installation root {root} pins control release {conf['control_release']} but this control code runs "
            f"from {running_release}; the root is not the installation that launched it. Nothing was read."
        )
    return root


def check_config_options(args, *, explicit_instance):
    """PF-A2.2 ``config-option-invalid`` (exit 2), before any registry read."""
    verb = getattr(args, "config_verb", None)
    if verb is None:
        return
    configuration, project = args.configuration, args.project
    if (verb == "app" and (configuration is not None or project is not None)) \
            or (project is not None and configuration is None) or (configuration is not None and explicit_instance):
        raise OptionRefused("config-option-invalid: --configuration [--project] selects a configuration directory that "
                            "is not registered yet and cannot be combined with --instance or used with 'config app'; "
                            "--project needs --configuration. Nothing was read or changed.")
    if project is not None and not PROJECT_RE.fullmatch(project):
        raise OptionRefused(f"config-option-invalid: --project {project!r} is not a Compose project name (1-40 "
                            "characters a-z 0-9 _ - starting with a letter or digit). Nothing was read or changed.")


def config_admin_unregistered(root, args, *, running_release, trusted_launch):
    """Section 3.3 pre-registration mode: create or complete a schema 2 pf-config.json in a configuration directory
    a fresh `pf install register` will name. Install and registry state, path rules and conflicts are checked before
    any question and again under the registry lock (LOCK_NB, held only around the reload, the checks and the write).
    No instance lock, no audit record (no instance exists yet); a legacy schema 1 file is never migrated here."""
    root = Path(root)
    prefix = pf_install.launcher_prefix(root)
    directory_text = args.configuration
    project = args.project
    if not trusted_launch:
        raise Failure("Mutating commands must start through the installed bootstrap launcher (Python isolated mode, "
                      "sanitized environment).")
    if unattended():
        raise Failure("terminal-required: 'config' asks for a typed confirmation and cannot run without a terminal "
                      "(scheduled task, script, or ssh without -t). Run it interactively: "
                      f"{prefix} config admin --configuration {directory_text}. Nothing was changed.")

    def state_checks():
        report = pf_install._Report()
        pf_install._open_operation_checks(report, root)
        pf_install._pending_registration_checks(report, root)
        # A registered record that cannot be loaded hides its configuration directory from the conflict checks
        # below: refuse it with the A2.1 register code (fail closed) instead of treating DIR as unregistered.
        pf_install._registry_checks(report, root)
        if report.conflicts:
            raise Failure("\n".join([f"{report.conflicts[0].code}: pf config admin was refused and nothing was created:"]
                                    + [f"  - {item.code}: {item.subject}: {item.detail}" for item in report.conflicts]))

    def path_checks():
        reason = pf_instance.canonical_path_error(directory_text)
        if reason is not None:
            findings = [("path-noncanonical", directory_text, reason)]
        else:
            checker = pf_bootstrap.PathChecker(root)
            pf_instance._data_directory(checker, Path(directory_text), "configuration", protected=False)
            findings = [(item.code, item.path, item.message) for item in checker.findings if item.severity == "refuse"]
        if findings:
            raise Failure(f"config-dir-invalid: {directory_text} cannot hold a configuration ({len(findings)} "
                          "finding(s)); nothing was created:\n"
                          + "\n".join(f"  - {code}: {path}: {message}" for code, path, message in findings))

    def path_conflict(detail, slug):
        return Failure(f"config-path-conflict: {directory_text} {detail}; nothing was created. A registered "
                       f"configuration directory is changed with '{prefix} --instance {slug} config admin'; otherwise "
                       "choose another directory.")

    def conflict_checks():
        try:
            registry = pf_instance.load_registry(root)
        except pf_instance.ContextError as exc:
            raise Failure(f"registry-invalid: {exc}; pf config admin was refused and nothing was created.") from exc
        directory = Path(directory_text)
        for _, context, _ in registry.records():
            if context is not None and context.paths.configuration == directory:
                raise path_conflict(f"is the registered configuration directory of instance {context.slug}",
                                    context.slug)
        inventory = pf_instance.inventory_of(registry)
        problems = pf_instance.managed_path_conflicts({"configuration": directory}, "(unregistered)", inventory, root)
        if problems:
            owner = next((owner for owner, _, other in inventory if other == directory
                          or pf_instance._contains(other, directory) or pf_instance._contains(directory, other)), None)
            slug = owner.split(":", 1)[1] if owner and owner.startswith("reservation:") else owner or "<slug>"
            raise path_conflict("conflicts with registered managed paths: "
                                + "; ".join(f"{code}: {message}" for code, _, message in problems), slug)

    state_checks()
    path_checks()
    conflict_checks()
    path = Path(directory_text) / "pf-config.json"
    target = inspect_editable_target(path)
    before = None
    if not target.present:
        mode = "create"
        values = load_admin_example(Path(running_release) / "pf-config.example.json", prefix)
        if project is None:
            def validate_project(answer):
                if not PROJECT_RE.fullmatch(answer):
                    raise Failure(pf_config.ADMIN_RULES["project"])
                return answer
            with cancelled_before_summary(path):
                project = ask_answer("project", "Compose project", validate=validate_project)
        values.update(project=project, environment=pf_instance.SUPPORTED_ENVIRONMENTS[0])
    else:
        before = pf_config.parse_admin_config(target.data, label=str(path))
        if before.problems:
            rerun = f"{prefix} config admin --configuration {directory_text}" \
                + (f" --project {project}" if project is not None else "")
            refuse_admin_config(path, before, rerun=rerun, prefix=prefix, release_id=Path(running_release).name)
        if before.schema_version == 1:
            log(f"admin-config-legacy-unregistered: {path} is a legacy schema 1 file in a directory that is not "
                "registered; it was left unchanged because a v2.5 control may still read it. 'pf install register' "
                f"accepts it as it is; after registration run '{prefix} --instance <slug> config admin' to make it "
                "explicit.")
            for key in ("backup_read_group", "workspace_write_group"):
                if not group_exists(before.values[key]):
                    log(f"{key} {before.values[key]!r} does not exist on this host; fix it by hand first.")
            return 0
        values = dict(before.values)
        if project is not None and values["project"] != project:
            raise Failure(f"admin-config-mismatch: {path} names project {values['project']!r}, not {project!r}; nothing "
                          "was changed.")
        if values["environment"] not in pf_instance.SUPPORTED_ENVIRONMENTS:
            raise Failure(f"admin-config-mismatch: {path} names environment {values['environment']!r}; registration "
                          f"approves {', '.join(pf_instance.SUPPORTED_ENVIRONMENTS)} only, and nothing was changed.")
        mode = "complete"
    location = f"{directory_text} and the paths registered later"
    with cancelled_before_summary(path):
        asked = admin_group_questions(values, mode=mode,
                                      locations={"workspace_write_group": location, "backup_read_group": location})

    def locked(action):
        try:
            handle = pf_instance.acquire_registry_lock(root)
        except pf_instance.LockBusy as exc:
            raise Failure(f"config-busy: Another operation holds {root / pf_instance.REGISTRY_LOCK_RELATIVE}; nothing "
                          "was changed. Try again after it finishes.") from exc
        except pf_instance.ContextError as exc:
            raise Failure(str(exc)) from exc
        try:
            state_checks()
            path_checks()
            conflict_checks()
            action()
        finally:
            handle.release()

    if mode == "complete" and not asked:
        if target.removable_leftovers:
            locked(lambda: remove_editable_leftovers(target))
        log(f"config-current: {path} is current (admin configuration schema 2); nothing to change.")
        return 0
    document = pf_config.admin_document(values)
    try:
        data = pf_config.render_admin_config(document)
    except pf_config.ConfigError as exc:
        raise Failure(f"config-changed: {path}: {exc}; nothing was written.") from exc
    rows = pf_config.admin_config_changes(before, document, asked=asked)
    admin_summary(path, mode=mode, before=before, document=document, asked=asked,
                  instance_line=f"Instance: (not registered yet), project {values['project']}",
                  environment_line=f"Environment label: {values['environment']} (the approved policy is chosen by "
                                   "'pf install register'; unchanged)",
                  app_hint=f"Application variables are not in this file; after registration use '{prefix} --instance "
                           "<slug> config app'.")
    confirm_write(path)
    changed = [row["key"] for row in rows if row["action"] != "kept"]
    locked(lambda: write_reviewed(path, data, target,
                                  create_gid=grp.getgrnam(values["workspace_write_group"]).gr_gid,
                                  op8=uuid.uuid4().hex[:8], keys=changed))
    log(f"Wrote {path} (admin configuration schema 2; {mode}). Next: '{prefix} install register' with "
        f"--configuration {directory_text} --project {values['project']}.")
    return 0


def main(argv=None, *, installation_root=None, running_release=None, trusted_launch=None):
    """CLI entry point.

    ``installation_root``/``running_release``/``trusted_launch`` are in-process
    harness parameters; the installed launcher supplies ``--installation-root``
    and the process facts are read from ``__file__`` and ``sys.flags``.
    """
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.", file=sys.stderr)
        return 2
    os.umask(0o077)
    argv = list(sys.argv[1:] if argv is None else argv)
    if running_release is None:
        running_release = RUNNING_RELEASE
    if trusted_launch is None:
        trusted_launch = bool(sys.flags.isolated)

    controller = None
    managed_started = False
    held_lock = contextlib.ExitStack()
    try:
        # The installation root is the bootstrap→release handshake: exactly one
        # "--installation-root=<root>" as the first argument, placed there by the
        # installed verifier (or the in-process harness parameter). It is not an
        # operator option: any further spelling of it, before or after the command,
        # refuses the whole invocation before any registry or instance state is read.
        root = installation_root
        handshake = pf_instance.ROOT_HANDSHAKE_OPTION + "="
        if argv and argv[0].startswith(handshake) and installation_root is None:
            root = argv.pop(0)[len(handshake):]
        injected = pf_instance.pf_bootstrap.operator_supplied_root_arguments(argv)
        if injected:
            raise Failure(
                "root-override: " + " ".join(injected) + ": the installation root is chosen by the installed "
                "bootstrap; --installation-root is not an operator option. Nothing was read and nothing was changed."
            )
        globals_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        globals_parser.add_argument("--instance")
        options, rest = globals_parser.parse_known_args(argv)
        if root is None:
            # Without a protected installation root nothing here is trusted: neither
            # the writable repository copy nor an unregistered legacy control directory
            # may run as the control plane. The legacy launcher prints its read-only
            # report by itself (pf.sh, control mode) without executing this file.
            raise Failure(
                "Refusing to run pf-admin.py without an installed bootstrap (--installation-root). "
                "Use the installed launcher: sudo pf <command>. Legacy v2.5 layouts are diagnostics-only "
                "until the PF-A2 migration registers them."
            )

        root = bind_installation_root(root, running_release)
        # PF-A1.4: every word is classified before the registry is read. A removed Compose word, a
        # Compose/Docker global option, any other leading option and an unknown word are refused here
        # without a registry read, a Controller, a lock or a child process. Help and usage errors
        # are answered by argparse (exit 0/2), also before any state is read.
        _, name = classify_command(rest)
        args = parser().parse_args(rest if rest else [name])
        route = DISPATCH[args.command]
        if args.command == "install":
            # PF-A2.1: the installer reads the registry and takes its locks itself (registry lock first).
            return pf_install.run_installed(root, args, running_release=running_release, trusted_launch=trusted_launch,
                                            interaction=pf_install.Interaction(unattended, input_line, log))
        # PF-A2.2: config option combinations are refused before any registry read; --configuration selects the
        # pre-registration mode of `config admin`, which takes only the registry lock (never an instance lock).
        check_config_options(args, explicit_instance=options.instance is not None)
        if getattr(args, "configuration", None) is not None:
            return config_admin_unregistered(root, args, running_release=running_release,
                                             trusted_launch=trusted_launch)
        try:
            registry = pf_instance.load_registry(root)
        except pf_instance.ContextError as exc:
            raise Failure(str(exc)) from exc

        def select(project=None):
            try:
                context = pf_instance.resolve_instance(registry, instance=options.instance, project=project)
            except pf_instance.ContextError as exc:
                raise Failure(str(exc)) from exc
            validation = pf_instance.validate_context(context, running_release=running_release, interpreter=sys.executable)
            return Controller(context, validation=validation, running_release=running_release)

        if args.command == "instances":
            display_registry(registry)
            return 0
        project = getattr(args, "project", None)
        controller = select(project=project)
        context = controller.context
        if options.instance is not None and project is not None and project != context.compose_project:
            raise Failure(f"selection-conflict: --project {project} does not match the selected instance {context.slug} "
                          f"(project {context.compose_project}). Use --instance alone. Nothing was changed.")
        if route.trusted_context:
            # PF-A2.2: the config wizards read pf-config.json themselves (absent, refused or mismatched files reach
            # their own outcome); the protected-context refusal still runs for every trusted route.
            controller.require_trusted_context(load_config=route.name != "config")
        if route.trusted_launch and not trusted_launch:
            raise Failure(
                ("Mutating commands" if route.lock else "Compose views (ps, logs)")
                + " must start through the installed bootstrap launcher (Python isolated mode, sanitized environment)."
            )

        if not route.lock:
            if args.command == "doctor":
                controller.doctor()
            elif args.command == "status":
                controller.status()
            elif args.command == "backups":
                controller.display_page(controller.snapshots(), args.page)
            elif args.command == "recoveries":
                controller.display_recoveries(controller.recoveries(), args.page)
            elif args.command == "ps":
                controller.compose_ps(args)
            elif args.command == "logs":
                controller.compose_logs(args)
            return 0

        # Every locked route: validated context and sanitized launch (above), then the unattended and
        # auto-apply gates before the lock, the stable lock with its explicit journal route, and the
        # ownership preflight before any confirmation, journal write, pause, tag or Compose child.
        require_attended(route, args, controller, explicit_instance=options.instance is not None,
                         selected_by="protected default" if registry.default_instance_id is not None
                         else "single registration")
        if route.mutability == "conditional" and args.apply and not controller.policy_permits("auto-apply"):
            slug, revision = context.slug, context.approved_policy.revision
            raise Deferred(
                f"auto-apply-not-permitted: release apply needs a protected policy that permits it; approved policy "
                f"revision {revision} of instance {slug} does not (automatic apply is off in this checkpoint). The "
                f"editable auto_update setting is a proposal only. Nothing was changed. Check with 'pf --instance "
                f"{slug} release-check' and apply manually with 'pf --instance {slug} update --release <tag>'.")
        held_lock.enter_context(controller.lock(pending_route=route.name, freeze=route.name != "config"))
        if route_preflight(route, args) == "owned":
            controller.require_topology_owned(route.name)
        managed_started = route_fail_closed(route, args)

        if args.command == "permissions":
            controller.permissions()
        elif args.command == "deploy":
            use_current = args.current or not (args.latest or args.commit or args.release)
            target = None if use_current else controller.resolve(
                latest=args.latest, commit=args.commit, release=args.release, channel=args.channel
            )
            if not use_current and target is None:
                raise Failure("No published release exists for this channel. Select prerelease, --latest, or an explicit commit.")
            controller.deploy(target, use_current=use_current, skip_ci=args.skip_ci)
        elif args.command == "purge":
            delete_backups = True if args.delete_backups else False if args.keep_backups else None
            controller.purge(delete_backups=delete_backups, reset_admin_config=args.reset_admin_config)
        elif args.command == "restore-instance":
            # The mutation target is the selected registered instance, never a
            # path embedded in the (untrusted) recovery bundle.
            recovery = controller.choose_recovery(args.recovery_id)
            controller.restore_instance(recovery, side_by_side=args.side_by_side)
            managed_started = False
        elif args.command == "backup":
            controller.database_ready()
            controller.ensure_local_contract()
            controller.snapshot("scheduled-or-manual-backup")
            log("Copy the entire checkpoint directory off-NAS. It contains production-like database data even though runtime .env is stored separately.")
        elif args.command == "reset-db":
            controller.reset_database()
        elif args.command == "config":
            controller.configure(args)
        elif args.command == "abort-deploy":
            controller.abort_deploy()
        elif args.command == "rollback":
            controller.rollback(args.backup_id, args.restore_db)
        elif args.command == "resume":
            controller.resume()
        elif args.command == "update":
            target = controller.resolve(latest=args.latest, commit=args.commit, release=args.release, channel=args.channel)
            if target is None:
                raise Failure("No published release exists for this channel. Select prerelease or use --latest manually.")
            controller.update(target, allow_migrations=args.allow_migrations, skip_ci=args.skip_ci)
        elif args.command == "release-check":
            target = controller.resolve(channel=args.channel)
            if target is None:
                log("No published release for this channel. No update attempted.")
                return 0
            log("Selected release: " + target["ref"] + " -> " + target["sha"])
            log("Deployed source: " + controller.revision())
            if not args.apply:
                log("Check-only. No code or database changes were made.")
                return 0
            # Unreachable in A1 (the auto-apply gate refuses first); kept for the PF-A4.3 grant.
            controller.update(target, automatic=True)
        return 0
    except (Failure, pf_instance.ContextError, OSError, ValueError, KeyError, KeyboardInterrupt) as exc:
        try:
            print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        finally:
            # After a hangup the terminal is gone and the report above fails; the fail-closed
            # stop must still run, under the lock, before this process exits.
            if controller is not None and managed_started:
                controller.fail_closed()
        return 20 if isinstance(exc, Deferred) else 2 if isinstance(exc, OptionRefused) else 1
    finally:
        held_lock.close()


# The shared interrupt handler lives in pf_runner since PF-A2.1 (also installed by pf_install.main for
# `install-control.sh init`); re-exported here under the same names.
INTERRUPT_SIGNALS = pf_runner.INTERRUPT_SIGNALS
install_interrupt_handlers = pf_runner.install_interrupt_handlers


if __name__ == "__main__":
    install_interrupt_handlers()
    sys.exit(main())
