"""Installed bootstrap verifier and shared protected-path primitives (PF-A1.1).

Standard library only, Python 3.9 language baseline, no imports from a control
release. A copy of this file is installed in ``<installation-root>/bootstrap/``
by the trusted installer together with ``pf`` (the shell launcher) and
``bootstrap.conf``. The launcher runs this copy first; it verifies the
interpreter, the bootstrap configuration, the pinned control release and every
ancestor before any release code is executed, then replaces itself with
``<release>/pf-admin.py`` through ``os.execv``.

Trust assumption (ARCHITECTURE.md section 4, unchanged from r3): the host
administrator installs ``bootstrap/`` from reviewed bytes and enters through the
host's trusted privilege entry (``sudo``). This module cannot authenticate its
own bytes; it can only refuse to run *other* code that is not protected.

The same file is also installed inside each control release so that
``pf_instance.py`` reuses one implementation of the path/ACL checks after the
launch. The release copy is pinned by the control inventory and verified here
before it is imported.
"""
import dataclasses
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

TRUSTED_UID = 0
BOOTSTRAP_DIR = "bootstrap"
LAUNCHER_NAME = "pf"
BOOTSTRAP_CONF_NAME = "bootstrap.conf"
BOOTSTRAP_MODULE_NAME = "pf_bootstrap.py"
CONTROL_INVENTORY_NAME = "control-manifest.json"
CONTROL_ENTRY_POINT = "pf-admin.py"
REQUIRED_RELEASE_FILES = ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "compose.nas.yaml")
BOOTSTRAP_CONF_KEYS = ("interpreter", "control_release", "control_release_sha256")
RELEASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_RE = re.compile(r"[a-f0-9]{64}\Z")
EXIT_REFUSED = 2
ROOT_HANDSHAKE_OPTION = "--installation-root"


class BootstrapError(RuntimeError):
    """A diagnostic result of the pre-launch trust checks. Nothing was executed or changed."""


# ------------------------------------------------------------------ small helpers


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes_nofollow(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        chunks = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def _reject_constant(name):
    raise BootstrapError("Non-finite JSON number is not allowed: " + name)


def parse_strict_json(data, *, label, error=BootstrapError):
    """Parse UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise error(f"{label}: not valid UTF-8: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except BootstrapError as exc:
        raise error(f"{label}: {exc}") from exc
    except ValueError as exc:
        raise error(f"{label}: invalid JSON: {exc}") from exc


def canonical_path_error(value):
    """Reason why ``value`` is not the single canonical spelling of an absolute POSIX path, or None.

    Accepted: a string with exactly one leading '/', at least one component, no empty
    ('//'), '.' or '..' component, no trailing '/' and no control characters. Every other
    spelling that may name the same filesystem object ('//a/b', '/a//b', '/a/./b', '/a/b/')
    is refused rather than normalized, so no two spellings of one location can coexist in
    trusted state and lexical containment checks stay sound (A11-R03).
    """
    if not isinstance(value, str):
        return "not a string"
    if not value.startswith("/"):
        return "not an absolute path"
    if value == "/":
        return "the filesystem root itself is not a managed path"
    if value.startswith("//"):
        return "a double leading slash is an implementation-defined POSIX namespace, not a canonical path"
    components = value.split("/")[1:]
    if any(part == "" for part in components):
        return "empty component (repeated or trailing '/')"
    if any(part in (".", "..") for part in components):
        return "'.' or '..' component"
    if any(ord(char) < 0x20 or char == "\x7f" for char in value):
        return "control character"
    return None


def canonical_path(value, *, label, error=BootstrapError):
    """Return ``Path(value)`` when ``value`` is canonical (see ``canonical_path_error``); fail closed otherwise."""
    reason = canonical_path_error(value)
    if reason is not None:
        raise error(f"path-noncanonical: {label} must be one canonical absolute POSIX path ({reason}): {value!r}")
    return Path(value)


def writable_by_others(mode):
    return bool(stat.S_IMODE(mode) & 0o022)


def replacement_resistant_ancestor(info):
    """A trusted-owner entry inside this directory cannot be renamed or unlinked by editors."""
    if info.st_uid != TRUSTED_UID:
        return False
    if not writable_by_others(info.st_mode):
        return True
    # A sticky world-writable directory (for example /tmp) still protects
    # root-owned entries from being renamed or removed by other users.
    return bool(info.st_mode & stat.S_ISVTX)


# --------------------------------------------------------------------------- ACL


@dataclasses.dataclass(frozen=True)
class AclState:
    kind: str  # "none", "posix", "unknown"
    write_grants: tuple
    detail: str = ""


ACL_USER_OBJ, ACL_USER, ACL_GROUP_OBJ, ACL_GROUP, ACL_MASK, ACL_OTHER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
ACL_WRITE = 0x02
ACL_VERSION = 0x0002
POSIX_ACL_NAMES = ("system.posix_acl_access", "system.posix_acl_default")


def parse_posix_acl(blob):
    """Parse a Linux system.posix_acl_* xattr blob into (tag, perm, id) tuples."""
    if len(blob) < 4 or (len(blob) - 4) % 8:
        raise ValueError("unexpected ACL blob length")
    (version,) = struct.unpack("<I", blob[:4])
    if version != ACL_VERSION:
        raise ValueError(f"unsupported ACL version {version}")
    entries = []
    for offset in range(4, len(blob), 8):
        tag, perm, identifier = struct.unpack("<HHI", blob[offset:offset + 8])
        entries.append((tag, perm, identifier))
    return entries


def inspect_posix_acl(path):
    """Report ACL authority beyond the POSIX mode bits.

    ``none`` is claimed only when the extended-attribute query succeeded and
    listed no ACL attribute. Any failed or unsupported query, and any ACL
    attribute this module does not understand, is ``unknown``: the absence of
    additional authority has not been established.
    """
    if not hasattr(os, "listxattr"):
        return AclState("unknown", (), "extended attribute API unavailable; ACL state cannot be verified")
    try:
        names = os.listxattr(str(path), follow_symlinks=False)
    except OSError as exc:
        return AclState("unknown", (), f"ACL query failed ({errno.errorcode.get(exc.errno, exc.errno)}): "
                                        "additional ACL authority cannot be excluded")
    grants = []
    kind = "none"
    for name in names:
        if name in POSIX_ACL_NAMES:
            try:
                entries = parse_posix_acl(os.getxattr(str(path), name, follow_symlinks=False))
            except (OSError, ValueError) as exc:
                return AclState("unknown", tuple(grants), f"{name} present but unreadable: {exc}")
            kind = "posix"
            mask = None
            for tag, perm, _ in entries:
                if tag == ACL_MASK:
                    mask = perm
            for tag, perm, identifier in entries:
                effective = perm if mask is None or tag in (ACL_USER_OBJ, ACL_OTHER) else perm & mask
                if tag in (ACL_USER, ACL_GROUP, ACL_GROUP_OBJ, ACL_OTHER) and effective & ACL_WRITE:
                    who = {ACL_USER: f"user {identifier}", ACL_GROUP: f"group {identifier}",
                           ACL_GROUP_OBJ: "owning group", ACL_OTHER: "others"}[tag]
                    grants.append(f"{name} grants write to {who}")
        elif "acl" in name.lower():
            return AclState("unknown", tuple(grants), f"unknown ACL attribute {name}; mutation limited")
    return AclState(kind, tuple(grants))


# ----------------------------------------------------------------- path checker


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str  # "refuse" blocks execution/mutation; "note" is informational
    code: str
    path: str
    message: str

    def render(self):
        return f"[{self.severity}] {self.code}: {self.path}: {self.message}"


class PathChecker:
    """Accumulates findings from no-follow checks. Never follows links, never writes."""

    def __init__(self, root):
        self.root = Path(root)
        self.findings = []
        self.acl_states = set()
        self.root_dev = None
        self.bootstrap_conf = None
        self.checked_ancestors = set()

    def blocking(self):
        return [finding.render() for finding in self.findings if finding.severity == "refuse"]

    def refuse(self, code, path, message):
        self.findings.append(Finding("refuse", code, str(path), message))

    def note(self, code, path, message):
        self.findings.append(Finding("note", code, str(path), message))

    def acl(self, path, *, editor_writable=False):
        """Record ACL authority for one path. Unknown always limits mutation."""
        state = inspect_posix_acl(path)
        self.acl_states.add(state.kind)
        for message in state.write_grants:
            if editor_writable:
                self.note("acl-write-editor-leaf", path, message)
            else:
                self.refuse("acl-write", path, message)
        if state.kind == "unknown":
            self.refuse("acl-unknown", path, state.detail or "ACL state not understood; mutation limited")
        return state

    def ancestors(self, path):
        """Check every directory above ``path`` without following links.

        Every component must be a directory (not a symlink), owned by the
        trusted uid, replacement-resistant and free of unknown/extra ACL
        authority. Returns False on the first refusal.
        """
        path = Path(path)
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current = current / part
            if current in self.checked_ancestors:
                continue
            try:
                info = os.lstat(str(current))
            except OSError as exc:
                self.refuse("ancestor-missing", current, str(exc))
                return False
            if stat.S_ISLNK(info.st_mode):
                self.refuse("ancestor-symlink", current, "symbolic link in a protected path")
                return False
            if not stat.S_ISDIR(info.st_mode):
                self.refuse("ancestor-not-directory", current, "not a directory")
                return False
            if not replacement_resistant_ancestor(info):
                self.refuse(
                    "ancestor-replaceable", current,
                    f"owner uid {info.st_uid} mode {oct(stat.S_IMODE(info.st_mode))}: an editor could replace entries",
                )
                return False
            self.acl(current)
            self.checked_ancestors.add(current)
        return True

    def protected(self, path, *, kind, allow_group_read=True, require_nlink_one=True, check_device=True):
        """No-follow checks for one protected entry. Returns the lstat result or None."""
        path = Path(path)
        try:
            info = os.lstat(str(path))
        except OSError as exc:
            self.refuse("missing", path, str(exc))
            return None
        if stat.S_ISLNK(info.st_mode):
            self.refuse("symlink", path, "protected entry is a symbolic link")
            return None
        if kind == "dir" and not stat.S_ISDIR(info.st_mode):
            self.refuse("not-directory", path, "expected a directory")
            return None
        if kind == "file" and not stat.S_ISREG(info.st_mode):
            self.refuse("not-regular", path, "expected a regular file")
            return None
        if info.st_uid != TRUSTED_UID:
            self.refuse("untrusted-owner", path, f"owner uid {info.st_uid}; trusted owner is uid {TRUSTED_UID}")
        if writable_by_others(info.st_mode):
            self.refuse("writable", path, f"mode {oct(stat.S_IMODE(info.st_mode))} is group/world writable")
        if not allow_group_read and stat.S_IMODE(info.st_mode) & 0o077:
            self.refuse("exposed", path, f"mode {oct(stat.S_IMODE(info.st_mode))} exposes private state")
        if kind == "file" and require_nlink_one and info.st_nlink != 1:
            self.refuse("hardlinked", path, f"{info.st_nlink} links; another name can change this content")
        if check_device and self.root_dev is not None and info.st_dev != self.root_dev:
            self.refuse("mount-boundary", path, "different device than the installation root; unsupported mount boundary")
        self.acl(path)
        return info

    def protected_tree(self, directory, *, allow_group_read=True):
        """Every entry below a protected directory must itself be protected."""
        for current, dirs, files in os.walk(str(directory), followlinks=False):
            for name in dirs:
                self.protected(Path(current) / name, kind="dir", allow_group_read=allow_group_read)
            for name in files:
                self.protected(Path(current) / name, kind="file", allow_group_read=allow_group_read)


# ------------------------------------------------------------ bootstrap contents


def parse_bootstrap_conf(data, *, label, error=BootstrapError):
    """KEY=VALUE data parsing. No shell evaluation; only known keys with normalized absolute paths."""
    values = {}
    for number, raw in enumerate(data.decode("utf-8", "strict").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise error(f"{label}:{number}: expected key=value")
        key, value = line.split("=", 1)
        if key not in BOOTSTRAP_CONF_KEYS:
            raise error(f"{label}:{number}: unknown key {key!r}")
        if key in values:
            raise error(f"{label}:{number}: duplicate key {key!r}")
        if key == "control_release_sha256":
            if not SHA256_RE.fullmatch(value):
                raise error(f"{label}:{number}: control_release_sha256 must be 64 lowercase hex characters")
        else:
            parts = value.split("/")
            if (not re.fullmatch(r"/[A-Za-z0-9._/-]+", value) or any(part in ("", ".", "..") for part in parts[1:])
                    or value.endswith("/")):
                raise error(f"{label}:{number}: {key} must be a normalized absolute path")
        values[key] = value
    missing = [key for key in BOOTSTRAP_CONF_KEYS if key not in values]
    if missing:
        raise error(f"{label}: missing keys {', '.join(missing)}")
    return values


def load_control_inventory(release_dir, *, error=BootstrapError):
    """Strictly parse ``<release>/control-manifest.json``; returns (inventory, sha256 of its bytes)."""
    path = Path(release_dir) / CONTROL_INVENTORY_NAME
    data = read_bytes_nofollow(path)
    inventory = parse_strict_json(data, label=str(path), error=error)
    expected_keys = {"schema_version", "release_id", "files"}
    if not isinstance(inventory, dict) or set(inventory) != expected_keys:
        raise error(f"{path}: control inventory keys must be exactly {sorted(expected_keys)}")
    if inventory["schema_version"] is True or inventory["schema_version"] != 1:
        raise error(f"{path}: unsupported control inventory schema_version")
    if not isinstance(inventory["release_id"], str) or not RELEASE_ID_RE.fullmatch(inventory["release_id"]):
        raise error(f"{path}: invalid release_id")
    files = inventory["files"]
    if not isinstance(files, dict) or not files:
        raise error(f"{path}: files must be a non-empty object")
    for name, value in files.items():
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith("."):
            raise error(f"{path}: unsupported inventory entry name {name!r}")
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise error(f"{path}: {name}: sha256 must be 64 lowercase hex characters")
    return inventory, sha256_bytes(data)


def verify_release(checker, release_dir, *, expected_inventory_sha256=None, expected_release_id=None,
                   required_files=REQUIRED_RELEASE_FILES):
    """Protected tree, inventory hash pin, per-file hashes, no unlisted files. Returns the inventory or None."""
    release_dir = Path(release_dir)
    if checker.protected(release_dir, kind="dir") is None:
        return None
    checker.protected_tree(release_dir)
    try:
        inventory, inventory_sha = load_control_inventory(release_dir)
    except (BootstrapError, OSError) as exc:
        checker.refuse("control-inventory-invalid", release_dir, str(exc))
        return None
    if expected_inventory_sha256 is not None and inventory_sha != expected_inventory_sha256:
        checker.refuse("control-inventory-hash", release_dir,
                       "installed control inventory does not match the protected pin")
    if expected_release_id is not None and inventory["release_id"] != expected_release_id:
        checker.refuse("control-release-id", release_dir,
                       f"inventory release_id {inventory['release_id']} != expected {expected_release_id}")
    listed = set()
    for name, expected in inventory["files"].items():
        path = release_dir / name
        listed.add(path)
        try:
            actual = sha256_bytes(read_bytes_nofollow(path))
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                checker.refuse("control-file-symlink", path, "inventoried control file is a symbolic link (not followed)")
            else:
                checker.refuse("control-file-missing", path, str(exc))
            continue
        if actual != expected:
            checker.refuse("control-file-hash", path, "installed control file differs from its inventory hash")
    for name in required_files:
        if name not in inventory["files"]:
            checker.refuse("control-file-required", release_dir / name, "required control file is not in the inventory")
    for current, _, files in os.walk(str(release_dir), followlinks=False):
        for name in files:
            path = Path(current) / name
            if path not in listed and path != release_dir / CONTROL_INVENTORY_NAME:
                checker.refuse("control-file-unlisted", path, "file is not part of the installed control inventory")
    return inventory


def verify_installation_anchor(root, *, interpreter=None, isolated=None, checker=None, running_bootstrap=None):
    """Read-only trust checks that must pass before any release code runs.

    Covers the installation root and its ancestors, the bootstrap directory
    (launcher, configuration, this verifier), the registered interpreter and
    its ancestors, and the pinned control release. Returns the checker; the
    parsed configuration is in ``checker.bootstrap_conf`` when it was valid.
    """
    root = Path(root)
    checker = checker or PathChecker(root)
    if isolated is False:
        checker.refuse("not-isolated", str(interpreter or sys.executable),
                       "interpreter was not started in isolated mode (-I); use the installed launcher")
    reason = canonical_path_error(str(root))
    if reason is not None:
        checker.refuse("root-not-canonical", root, "installation root must be one canonical absolute POSIX path (" + reason + ")")
        return checker
    if not checker.ancestors(root):
        return checker
    info = checker.protected(root, kind="dir")
    if info is None:
        return checker
    checker.root_dev = info.st_dev
    bootstrap = root / BOOTSTRAP_DIR
    checker.protected(bootstrap, kind="dir")
    launcher_info = checker.protected(bootstrap / LAUNCHER_NAME, kind="file")
    if launcher_info is not None and not launcher_info.st_mode & stat.S_IXUSR:
        checker.refuse("launcher-not-executable", bootstrap / LAUNCHER_NAME, "installed launcher is not executable")
    checker.protected(bootstrap / BOOTSTRAP_MODULE_NAME, kind="file")
    if running_bootstrap is not None and Path(running_bootstrap) != bootstrap / BOOTSTRAP_MODULE_NAME:
        checker.refuse("bootstrap-module-location", running_bootstrap,
                       "verifier is not running from <root>/bootstrap/" + BOOTSTRAP_MODULE_NAME)
    conf_path = bootstrap / BOOTSTRAP_CONF_NAME
    conf = None
    if checker.protected(conf_path, kind="file") is not None:
        try:
            conf = parse_bootstrap_conf(read_bytes_nofollow(conf_path), label=str(conf_path))
        except (BootstrapError, OSError, UnicodeDecodeError) as exc:
            checker.refuse("bootstrap-conf-invalid", conf_path, str(exc))
    if conf is None:
        return checker
    checker.bootstrap_conf = conf
    registered_interpreter = Path(conf["interpreter"])
    if os.path.realpath(str(registered_interpreter)) != str(registered_interpreter):
        checker.refuse("interpreter-not-canonical", registered_interpreter,
                       "bootstrap must register the canonical interpreter path")
    if checker.ancestors(registered_interpreter):
        # The interpreter may legitimately live on another filesystem than the root.
        interpreter_info = checker.protected(registered_interpreter, kind="file", require_nlink_one=False,
                                             check_device=False)
        if interpreter_info is not None and not interpreter_info.st_mode & stat.S_IXUSR:
            checker.refuse("interpreter-not-executable", registered_interpreter,
                           "registered interpreter is not executable")
    if interpreter is not None and os.path.realpath(str(interpreter)) != str(registered_interpreter):
        checker.refuse("interpreter-mismatch", interpreter,
                       "process interpreter is not the registered bootstrap interpreter")
    release = Path(conf["control_release"])
    if release.parent != root / "releases" or not RELEASE_ID_RE.fullmatch(release.name):
        checker.refuse("bootstrap-release-outside-root", release, "control release must be <root>/releases/<id>")
        return checker
    checker.protected(root / "releases", kind="dir")
    verify_release(checker, release, expected_inventory_sha256=conf["control_release_sha256"],
                   expected_release_id=release.name)
    return checker


def operator_supplied_root_arguments(arguments):
    """Operator arguments that try to name an installation root. The root is chosen only by the
    installed bootstrap; ``--installation-root`` is the verifier→release handshake, never a CLI option."""
    return [item for item in arguments if item == ROOT_HANDSHAKE_OPTION or item.startswith(ROOT_HANDSHAKE_OPTION + "=")]


def main(argv=None):
    """Launcher entry: verify, then exec the pinned release entry point. Never imports release code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != ROOT_HANDSHAKE_OPTION:
        print("pf_bootstrap: usage: --installation-root <root> [pf arguments]", file=sys.stderr)
        return EXIT_REFUSED
    root = Path(argv[1])
    rest = argv[2:]
    injected = operator_supplied_root_arguments(rest)
    if injected:
        print("ERROR: installed bootstrap refused the command line:", file=sys.stderr)
        print(f"  [refuse] root-override: {' '.join(injected)}: the installation root is chosen by the installed "
              "bootstrap; --installation-root is not an operator option", file=sys.stderr)
        print("No control code was executed and nothing was changed.", file=sys.stderr)
        return EXIT_REFUSED
    checker = verify_installation_anchor(
        root, interpreter=sys.executable, isolated=bool(sys.flags.isolated),
        running_bootstrap=Path(__file__).resolve(),
    )
    blocking = checker.blocking()
    if blocking:
        print("ERROR: installed bootstrap refused to run the control release:", file=sys.stderr)
        for line in blocking:
            print("  " + line, file=sys.stderr)
        print("No control code was executed and nothing was changed.", file=sys.stderr)
        return EXIT_REFUSED
    conf = checker.bootstrap_conf
    interpreter = conf["interpreter"]
    entry = str(Path(conf["control_release"]) / CONTROL_ENTRY_POINT)
    # The handshake is one fixed first argument; the release accepts it only in this
    # position and refuses any further spelling of it (see pf-admin.py main()).
    command = [interpreter, "-I", "-B", entry, ROOT_HANDSHAKE_OPTION + "=" + str(root), *rest]
    try:
        os.execv(interpreter, command)
    except OSError as exc:
        print("ERROR: cannot execute the verified control release: " + str(exc), file=sys.stderr)
        return EXIT_REFUSED
    return EXIT_REFUSED  # pragma: no cover - execv does not return


if __name__ == "__main__":
    sys.exit(main())
