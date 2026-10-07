"""Deployment Admin installer: preflight, staged control/config generations and the journaled switch (PF-A2.1).

Python standard library only, Python 3.9 language baseline. Loaded as a sibling module of the pinned
control release (``pf install …`` through the installed bootstrap) and run directly by the repository
wrapper ``install-control.sh init`` (a new installation root only).

Every install operation follows one protocol (LIFECYCLE sections 1-3, 10):

* inputs are collected stage by stage, then a read-only preflight gathers *every* conflict before
  anything moves (one report, exit 1);
* the frozen plan is shown and confirmed with a typed phrase; nothing is written before that;
* the operation's locks are taken non-blocking (registry first, then the legacy v2.5 lock or the
  instance locks in UUID order; ``init`` holds a flock on its private sibling build directory);
* the preflight runs again under the locks and must reproduce the plan;
* ``plan.json`` and the first ``journal.json`` are published together as one atomic intent
  (``install-operations/.<id>.new`` renamed to ``<id>``);
* each effect is journaled ``intended`` (with the identity of what it creates), executed, observed
  from disk and journaled ``complete``; ``resume`` observes every effect before it decides anything.

Candidate control bytes are read as data (no-follow, bounded, ``ast`` 3.9 parse, literal constants only)
and execute only in the smoke check, after the typed trust decision; what is staged is exactly what was
shown. This module never starts a process itself: every child goes through ``pf_runner``.
"""
import argparse
import ast
import dataclasses
import datetime as dt
import errno
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid


def _load_sibling_module(name):
    """Import a module from this file's own directory by absolute path (no sys.path search)."""
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
pf_instance = _load_sibling_module("pf_instance")
pf_runner = _load_sibling_module("pf_runner")
pf_config = _load_sibling_module("pf_config")
pf_docker = _load_sibling_module("pf_docker")

INSTALL_CONTRACT = 1
INSTALL_SCHEMA_VERSION = 1
OPERATIONS_RELATIVE = pf_instance.INSTALL_OPERATIONS_RELATIVE
ROOT_HOME_RELATIVE = pf_instance.ROOT_HOME_RELATIVE
KINDS = ("init", "register", "migrate-legacy", "control")
TERMINAL_PHASES = ("completed", "cancelled", "rolled_back", "abandoned")
OPEN_PHASES = ("planned", "prepared", "validated", "switching", "verifying", "rolling_back", "abandoning",
               "needs_operator")
PHASES = OPEN_PHASES + TERMINAL_PHASES
INPUT_STAGES = {"init": ("root",), "register": ("slug", "project", "workspace", "configuration", "backups",
                "recovery"), "migrate-legacy": ("legacy-home", "workspace", "slug"), "control": ("candidate",),
                "resume": ()}
CONTROL_RELEASE_FILES = {  # release name -> path relative to the candidate repository root
    "pf-admin.py": "deploy/synology/pf-admin.py", "pf_instance.py": "deploy/synology/pf_instance.py",
    "pf_bootstrap.py": "deploy/synology/pf_bootstrap.py", "pf_runner.py": "deploy/synology/pf_runner.py",
    "pf_config.py": "deploy/synology/pf_config.py", "pf_source.py": "deploy/synology/pf_source.py",
    "pf_docker.py": "deploy/synology/pf_docker.py", "pf_install.py": "deploy/synology/pf_install.py",
    "compose.nas.yaml": "compose.nas.yaml",
    "pf-config.example.json": "deploy/synology/pf-config.example.json",
    "nas.env.example": "deploy/synology/nas.env.example",
}
BOOTSTRAP_FILES = {"pf": "pf.sh", "pf_bootstrap.py": "deploy/synology/pf_bootstrap.py",
                   "backup.sh": "deploy/synology/backup.sh", "release-check.sh": "deploy/synology/release-check.sh"}
WRAPPER_NAMES = ("backup.sh", "release-check.sh")
SOURCE_FILE_LIMIT = 4 * 1024 * 1024
FREE_MARGIN_BYTES = 64 * 1024 * 1024
SMOKE_TIMEOUT = 60.0
LAUNCHER_TEMPLATE = "#!/bin/sh\n# Deployment Admin installed launcher\nexec {root}/bootstrap/pf \"$@\"\n"
DEFAULT_LAUNCHER = Path("/usr/local/bin/pf")
LEGACY_LAUNCHER_RE = re.compile(r'#!/bin/sh\n# PartFlow NAS installed launcher\nexec "(?P<control>/[^"\n]+)/pf\.sh" '
                                r'"\$@"\n\Z')
INSTALLED_LAUNCHER_RE = re.compile(r'#!/bin/sh\n# Deployment Admin installed launcher\nexec '
                                   r'(?P<root>/[A-Za-z0-9._/-]+)/bootstrap/pf "\$@"\n\Z')
LAUNCHER_LIMIT = 4096
PROFILE_NAME = "partflow-staging-legacy.json"
POLICY_NAME = "staging.json"
DEFAULT_DOCKER_ENDPOINT = "unix:///var/run/docker.sock"
# Fixed absolute candidates per tool id (the pf.sh PATH directories); never a PATH search.
TOOL_CANDIDATES = {
    "docker": ("/usr/local/bin/docker", "/usr/bin/docker", "/var/packages/ContainerManager/target/usr/bin/docker",
               "/var/packages/Docker/target/usr/bin/docker"),
    "docker_compose": ("/usr/local/bin/docker-compose", "/usr/bin/docker-compose",
                       "/var/packages/ContainerManager/target/usr/bin/docker-compose",
                       "/var/packages/Docker/target/usr/bin/docker-compose"),
    "git": ("/usr/local/bin/git", "/usr/bin/git", "/var/packages/Git/target/bin/git"),
    "ip": ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip"),
    "hostname": ("/usr/bin/hostname", "/bin/hostname"),
}
INTERPRETER_CANDIDATES = ("/usr/bin/python3", "/usr/local/bin/python3", "/var/packages/Python3.9/target/usr/bin/python3")
OPERATION_ID_RE = re.compile(r"inst-([0-9]{8}T[0-9]{6}Z)-([0-9a-f]{8})\Z")
CONTENT_ADDRESSED_RE = re.compile(r"r-[0-9a-f]{16}\Z")
LEGACY_STAGED_RE = re.compile(r"\.(\.env|pf-config\.json)\.pf-migrate-[0-9a-f]{8}\Z")
LEGACY_FILE_NAMES = {"env": ".env", "admin-config": "pf-config.json"}
SMOKE_PROGRAM = r'''
import importlib.util, json, sys
from pathlib import Path
release, root = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("pf_admin_candidate", str(release / "pf-admin.py"))
admin = importlib.util.module_from_spec(spec)
sys.modules["pf_admin_candidate"] = admin
spec.loader.exec_module(admin)
inst, cfg = admin.pf_instance, admin.pf_config
result = {"python": list(sys.version_info[:3]), "version": admin.VERSION, "checkpoint": admin.CHECKPOINT,
          "install_contract": admin.pf_install.INSTALL_CONTRACT,
          "install_schema_version": admin.pf_install.INSTALL_SCHEMA_VERSION,
          "registry_ok": False, "records": {}, "configs": {}}
try:
    registry = inst.load_registry(root)
except Exception:
    registry = None
if registry is not None:
    result["registry_ok"] = True
    for entry, context, error in registry.records():
        result["records"][entry.instance_id] = "ok" if context is not None else "record-invalid"
        if context is None:
            continue
        found = {}
        try:
            data = inst.read_bytes_nofollow(context.paths.configuration / "pf-config.json")
        except FileNotFoundError:
            found["config"] = "missing"
        except OSError:
            found["config"] = "invalid"
        else:
            config, problems = cfg.validate_admin_config(data, label="pf-config.json")
            found["config"] = "ok" if not problems and config["project"] == context.compose_project \
                and config["environment"] == context.approved_environment else "invalid"
        try:
            data = inst.read_bytes_nofollow(context.paths.configuration / ".env")
        except FileNotFoundError:
            found["env"] = "missing"
        except OSError:
            found["env"] = "invalid"
        else:
            try:
                cfg.parse_app_env(data, label=".env")
                found["env"] = "ok"
            except cfg.ConfigError:
                found["env"] = "invalid"
        result["configs"][entry.instance_id] = found
print(json.dumps(result, sort_keys=True))
'''

# ------------------------------------------------------------------ wire schema (contracts/install-operation)

_UTC = "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
_OPID = "^inst-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
_SHA = "^[a-f0-9]{64}$"
_PATH = "^/[^\\u0000-\\u001f\\u007f]+$"
_UUID = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_RELEASE = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


def _object(properties, description=None):
    result = {"type": "object", "additionalProperties": False, "required": sorted(properties),
              "properties": properties}
    if description:
        result["description"] = description
    return result


# Embedded copy of contracts/install-operation.schema.json; a test asserts the two stay identical. The
# runtime validator is pf_instance.validate_against_schema (the A1 keyword subset, which has no array
# items and no nullable unions). Those two rules are carried by ``description`` markers that _validate
# below enforces: "null or <target>", "items <target>" and "map <target>", where <target> is
# "$defs.<name>", "string", "sha256", "path" or "release-id".
INSTALL_OPERATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin install operation v1 (PF-A2.1)",
    "description": ("Frozen InstallPlan and InstallJournal of one install operation under "
                    "<root>/install-operations/<operation-id>/. Strict UTF-8 JSON; duplicate keys, non-finite "
                    "numbers and unknown keys are rejected; booleans are not integers. Normative markers: a nullable "
                    "value, an array item rule and a map value rule are written as the description \"null or "
                    "<target>\", \"items <target>\" or \"map <target>\" (<target>: $defs.<name>, string, sha256, "
                    "path or release-id). They are enforced by pf_install.validate_document only; a generic JSON "
                    "Schema validator ignores them, so every consumer validates with pf_install.validate_document."),
    "$defs": {
        "plan": _object({
            "schema_version": {"const": 1},
            "operation_id": {"type": "string", "pattern": _OPID},
            "kind": {"enum": list(KINDS)},
            "created": {"type": "string", "pattern": _UTC},
            "root": {"type": "string", "pattern": _PATH},
            "install_contract": {"type": "integer", "minimum": 1},
            "running_release": {"description": "null or release-id"},
            "candidate": {"description": "null or $defs.candidate"},
            "selected_existing_release": {"type": "boolean"},
            "init_documents": {"description": "null or $defs.init_documents"},
            "instance": {"description": "null or $defs.instance"},
            "legacy": {"description": "null or $defs.legacy"},
            "launcher": {"description": "null or $defs.launcher"},
            "bindings": {"type": "array", "description": "items $defs.binding"},
            "config_baseline": {"type": "array", "description": "items $defs.config_baseline"},
            "verify_baseline": {"type": "array", "description": "items $defs.verify_baseline"},
            "app_operation_required": {"enum": ["none", "update"]},
            "notes": {"type": "array", "description": "items $defs.note"},
        }),
        "candidate": _object({
            "release_id": {"type": "string", "pattern": _RELEASE},
            "inventory_sha256": {"type": "string", "pattern": _SHA},
            "files": {"type": "object", "description": "map sha256"},
            "bootstrap_files": {"type": "object", "description": "map sha256"},
            "version": {"type": "string", "minLength": 1, "maxLength": 128},
            "checkpoint": {"type": "string", "minLength": 1, "maxLength": 128},
            "source_root": {"description": "null or path"},
        }),
        "init_documents": _object({
            "profile": _object({"name": {"type": "string", "minLength": 1},
                                "sha256": {"type": "string", "pattern": _SHA},
                                "version": {"type": "string", "minLength": 1, "maxLength": 128}}),
            "policy": _object({"name": {"type": "string", "minLength": 1},
                               "sha256": {"type": "string", "pattern": _SHA},
                               "revision": {"type": "integer", "minimum": 1}}),
        }),
        "instance": _object({
            "instance_id": {"type": "string", "pattern": _UUID},
            "slug": {"type": "string", "pattern": pf_instance.NAME_PATTERN},
            "compose_project": {"type": "string", "pattern": pf_instance.NAME_PATTERN},
            "approved_environment": {"enum": list(pf_instance.SUPPORTED_ENVIRONMENTS)},
            "daemon": _object({"endpoint": {"type": "string", "pattern": "^unix:///[^\\u0000-\\u001f\\u007f]+$"},
                               "engine_id": {"type": "string", "minLength": 1, "maxLength": 256}}),
            "paths": _object({role: {"type": "string", "pattern": _PATH} for role in pf_instance.ROLE_NAMES}),
        }),
        "legacy": _object({
            "home": {"type": "string", "pattern": _PATH},
            "control_dir": {"type": "string", "pattern": _PATH},
            "state_dir": {"type": "string", "pattern": _PATH},
            "lock_path": {"type": "string", "pattern": _PATH},
            "lock_created": {"type": "boolean"},
            "files": {"type": "array", "description": "items $defs.legacy_file"},
            "state_report": {"type": "array", "description": "items $defs.state_file"},
        }),
        "legacy_file": _object({
            "role": {"enum": ["env", "admin-config"]},
            "source": {"type": "string", "pattern": _PATH},
            "target": {"type": "string", "pattern": _PATH},
            "staged": {"type": "string", "pattern": _PATH},
            "sha256": {"type": "string", "pattern": _SHA},
            "size": {"type": "integer", "minimum": 0},
            "mode": {"type": "integer", "minimum": 0},
            "gid": {"type": "integer", "minimum": 0},
        }),
        "state_file": _object({"name": {"type": "string", "minLength": 1},
                               "size": {"type": "integer", "minimum": 0},
                               "sha256": {"type": "string", "pattern": _SHA}}),
        "launcher": _object({
            "path": {"type": "string", "pattern": _PATH},
            "before_kind": {"enum": ["absent", "legacy", "installed", "foreign"]},
            "before_sha256": {"description": "null or sha256"},
            "after_text": {"description": "null or string"},
            "action": {"enum": ["create", "leave"]},
        }),
        "binding": _object({
            "effect_id": {"type": "string", "minLength": 1},
            "type": {"enum": ["bootstrap-conf", "tools-conf", "record", "wrapper"]},
            "target": {"type": "string", "pattern": _PATH},
            "before_text": {"description": "null or string"},
            "before_sha256": {"description": "null or sha256"},
            "after_text": {"type": "string"},
            "after_sha256": {"type": "string", "pattern": _SHA},
        }),
        "config_baseline": _object({
            "instance_id": {"type": "string", "pattern": _UUID},
            "config": {"enum": ["ok", "missing", "invalid"]},
            "env": {"enum": ["ok", "missing", "invalid"]},
        }),
        "verify_baseline": _object({
            "instance_id": {"type": "string", "pattern": _UUID},
            "code": {"type": "string", "minLength": 1},
            "path": {"type": "string", "minLength": 1},
        }),
        "note": _object({"code": {"type": "string", "minLength": 1}, "detail": {"type": "string"}}),
        "journal": _object({
            "schema_version": {"const": 1},
            "operation_id": {"type": "string", "pattern": _OPID},
            "kind": {"enum": list(KINDS)},
            "plan_sha256": {"type": "string", "pattern": _SHA},
            "phase": {"enum": list(PHASES)},
            "sequence": {"type": "integer", "minimum": 1},
            "started": {"type": "string", "pattern": _UTC},
            "updated": {"type": "string", "pattern": _UTC},
            "effects": {"type": "array", "description": "items $defs.effect"},
            "result": {"description": "null or $defs.result"},
            "last_error": {"description": "null or string"},
            "next": {"type": "array", "description": "items string"},
        }),
        "effect": _object({
            "effect_id": {"type": "string", "minLength": 1},
            "type": {"type": "string", "minLength": 1},
            "state": {"enum": ["intended", "complete", "reversed"]},
            "observed": {"enum": [None, "not_started", "complete", "partial", "unknown"]},
            "target_identity": {"description": "null or $defs.target_identity"},
            "at": {"type": "string", "pattern": _UTC},
        }),
        "target_identity": _object({"st_dev": {"type": "integer", "minimum": 0},
                                    "st_ino": {"type": "integer", "minimum": 0}}),
        "result": _object({
            "release_id": {"type": "string", "pattern": _RELEASE},
            "bootstrap_conf_sha256": {"type": "string", "pattern": _SHA},
            "records": {"type": "object", "description": "map sha256"},
            "instance_id": {"description": "null or string"},
            "launcher": {"description": "null or $defs.result_launcher"},
        }),
        "result_launcher": _object({"path": {"type": "string", "pattern": _PATH},
                                    "sha256": {"type": "string", "pattern": _SHA}}),
    },
}


def _validate_target(value, target, path, errors):
    defs = INSTALL_OPERATION_SCHEMA["$defs"]
    if target.startswith("$defs."):
        _validate_def(value, target[len("$defs."):], path, errors)
    elif target == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
    elif target == "sha256":
        if not (isinstance(value, str) and re.fullmatch(_SHA[1:-1], value)):
            errors.append(f"{path}: expected sha256")
    elif target == "path":
        if not (isinstance(value, str) and pf_instance.canonical_path_error(value) is None):
            errors.append(f"{path}: expected a canonical absolute path")
    elif target == "release-id":
        if not (isinstance(value, str) and re.fullmatch(_RELEASE[1:-1], value)):
            errors.append(f"{path}: expected a release id")
    else:  # pragma: no cover - schema authoring error
        raise InstallError("schema-invalid", f"unknown schema marker target {target!r} ({sorted(defs)})")


def _walk(value, schema, path, errors):
    if not isinstance(value, dict):
        return
    for name, sub in schema.get("properties", {}).items():
        if name not in value:
            continue
        marker = sub.get("description", "")
        item = value[name]
        where = f"{path}.{name}"
        if marker.startswith("null or "):
            if item is not None:
                _validate_target(item, marker[len("null or "):], where, errors)
        elif marker.startswith("items "):
            if isinstance(item, list):
                for index, element in enumerate(item):
                    _validate_target(element, marker[len("items "):], f"{where}[{index}]", errors)
        elif marker.startswith("map "):
            if isinstance(item, dict):
                for key, element in item.items():
                    _validate_target(element, marker[len("map "):], f"{where}.{key}", errors)
        elif sub.get("type") == "object":
            _walk(item, sub, where, errors)


def _validate_def(value, name, path, errors):
    schema = INSTALL_OPERATION_SCHEMA["$defs"][name]
    errors.extend(pf_instance.validate_against_schema(value, schema, path))
    _walk(value, schema, path, errors)
    return errors


def validate_document(value, name):
    """Schema errors of a plan (``name="plan"``) or journal (``name="journal"``); [] when valid."""
    return _validate_def(value, name, "$", [])


# ------------------------------------------------------------------------- errors and records


class InstallError(RuntimeError):
    """An install refusal or failure. ``code`` is the operator code; the message is operator copy (§4.3)."""

    def __init__(self, code, message, *, exit_code=1):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class PreflightRefused(InstallError):
    def __init__(self, conflicts):
        conflicts = tuple(conflicts)
        lines = [f"Preflight found {len(conflicts)} conflict(s); nothing was changed:"]
        lines += [f"  - {item.code}: {item.subject}: {item.detail}" for item in conflicts]
        lines.append("Resolve every item, then run the same command again.")
        super().__init__("install-preflight-refused", "\n".join(lines))
        self.conflicts = conflicts


class Cancelled(InstallError):
    def __init__(self, stage, message=None):
        super().__init__("install-cancelled", message or f"Cancelled at {stage}; nothing was changed.")
        self.stage = stage


@dataclasses.dataclass(frozen=True)
class Conflict:
    code: str
    subject: str
    detail: str


@dataclasses.dataclass(frozen=True)
class CandidateSource:
    source_root: object
    release_files: dict
    bootstrap_files: dict
    inventory_bytes: bytes
    inventory_sha256: str
    release_id: str
    version: str
    checkpoint: str
    install_contract: int
    install_schema_version: int


@dataclasses.dataclass(frozen=True)
class LauncherState:
    path: Path
    kind: str
    target: object
    data: bytes


@dataclasses.dataclass(frozen=True)
class Interaction:
    """Built by the caller; pf_install never reads sys.stdin directly."""

    unattended: object
    ask: object
    say: object


class _SmokeFailed(Exception):
    pass


class _VerifyFailed(Exception):
    pass


class _NeedsOperator(Exception):
    def __init__(self, effect_id, target, before, after, observed):
        super().__init__(effect_id)
        self.effect_id, self.target, self.before, self.after, self.observed = effect_id, target, before, after, observed


# --------------------------------------------------------------------------------- small helpers


def _utc_text(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _text_sha(text):
    return _sha(text.encode("utf-8"))


def _document_bytes(value):
    return pf_instance.normalize_json(value)


def _read_regular(path, *, single_link=False):
    """Bytes of a regular file: opened without following links and without blocking (a FIFO or device is refused
    on the open descriptor, so an editor-writable directory cannot stall the installer while it holds locks),
    bounded by SOURCE_FILE_LIMIT. ``single_link`` also refuses a file with another hard link. Raises
    FileNotFoundError when absent and OSError otherwise."""
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        if single_link and info.st_nlink != 1:
            raise OSError(errno.EMLINK, "not a single-link file", str(path))
        chunks, total = [], 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > SOURCE_FILE_LIMIT:
                raise OSError(errno.EFBIG, f"more than {SOURCE_FILE_LIMIT} bytes", str(path))
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read_optional(path):
    """Bytes of a regular file read without following links, or None when it is absent."""
    try:
        return _read_regular(path)
    except FileNotFoundError:
        return None


def _identity(path):
    info = os.lstat(str(path))
    return {"st_dev": info.st_dev, "st_ino": info.st_ino}


def _same_identity(path, identity):
    try:
        return identity is not None and _identity(path) == identity
    except OSError:
        return False


def _op8(operation_id):
    return operation_id[-8:]


def new_operation_id(now=None):
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"inst-{stamp}-{uuid.uuid4().hex[:8]}"


def release_id_for(inventory_sha256):
    """Content-addressed release id: ``r-`` + the first 16 hex digits of the content digest.

    The digest is the SHA-256 of the release-id-free content inventory
    ``normalize_json({"schema_version": 1, "files": {name: sha256}})`` (see content_digest): the A1
    inventory embeds the release id, so its own hash cannot also define the id.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", str(inventory_sha256)):
        raise InstallError("release-id-invalid", "a release id needs a SHA-256 hex digest")
    return "r-" + inventory_sha256[:16]


def content_digest(file_hashes):
    return _sha(pf_instance.normalize_json({"schema_version": 1, "files": dict(sorted(file_hashes.items()))}))


def _literal_assignments(source, names):
    tree = ast.parse(source, feature_version=(3, 9))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in names and isinstance(node.value, ast.Constant):
            found[node.targets[0].id] = node.value.value
    return found


def _read_source_file(path):
    """(bytes, None) or (None, (code, detail)): no-follow, regular, bounded; never blocks on a FIFO."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, ("source-missing", "missing; the candidate must be a complete repository tree")
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None, ("source-unreadable", "symbolic link; candidate files are read without following links")
        return None, ("source-unreadable", f"cannot be read ({exc.strerror or exc})")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, ("source-unreadable", "not a regular file")
        if info.st_size > SOURCE_FILE_LIMIT:
            return None, ("source-too-large", f"{info.st_size} bytes exceeds the {SOURCE_FILE_LIMIT} byte limit")
        chunks, total = [], 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > SOURCE_FILE_LIMIT:
                return None, ("source-too-large", f"more than {SOURCE_FILE_LIMIT} bytes")
            chunks.append(block)
        return b"".join(chunks), None
    finally:
        os.close(fd)


def _candidate_literals(release_files, conflicts, subject):
    """INSTALL_CONTRACT/INSTALL_SCHEMA_VERSION (pf_install.py) and VERSION/CHECKPOINT (pf-admin.py)."""
    values = {}
    for name, keys in (("pf_install.py", ("INSTALL_CONTRACT", "INSTALL_SCHEMA_VERSION")),
                       ("pf-admin.py", ("VERSION", "CHECKPOINT"))):
        data = release_files.get(name)
        if data is None:
            continue
        try:
            values.update(_literal_assignments(data, keys))
        except (SyntaxError, ValueError):
            continue
    for key in ("INSTALL_CONTRACT", "INSTALL_SCHEMA_VERSION"):
        if type(values.get(key)) is not int:
            conflicts.append(Conflict("install-contract-incompatible", subject,
                                      f"pf_install.py has no integer {key} literal; this control cannot be installed "
                                      "by this installer"))
    for key in ("VERSION", "CHECKPOINT"):
        if not isinstance(values.get(key), str) or not values.get(key):
            conflicts.append(Conflict("source-syntax", subject, f"pf-admin.py has no string {key} literal"))
    return values


def read_candidate(source_root):
    """Pure read + ast of a repository-shaped candidate tree. Never executes it.

    Raises PreflightRefused with every source conflict collected (missing, unreadable, too large,
    syntax). The release id is content-addressed (release_id_for/content_digest).
    """
    reason = pf_instance.canonical_path_error(str(source_root))
    if reason is not None:
        raise PreflightRefused([Conflict("source-missing", str(source_root),
                                         f"the source must be one canonical absolute path ({reason})")])
    source_root = Path(source_root)
    conflicts, contents = [], {}
    for relative in sorted(set(CONTROL_RELEASE_FILES.values()) | set(BOOTSTRAP_FILES.values())):
        data, problem = _read_source_file(source_root / relative)
        if problem is not None:
            conflicts.append(Conflict(problem[0], str(source_root / relative), problem[1]))
            continue
        if relative.endswith(".py"):
            try:
                ast.parse(data, filename=relative, feature_version=(3, 9))
            except (SyntaxError, ValueError) as exc:
                conflicts.append(Conflict("source-syntax", str(source_root / relative),
                                          f"does not parse as Python 3.9 ({type(exc).__name__}: "
                                          f"{getattr(exc, 'msg', exc)}, line {getattr(exc, 'lineno', '?')})"))
                continue
        contents[relative] = data
    release_files = {name: contents[path] for name, path in CONTROL_RELEASE_FILES.items() if path in contents}
    bootstrap_files = {name: contents[path] for name, path in BOOTSTRAP_FILES.items() if path in contents}
    literals = _candidate_literals(release_files, conflicts, str(source_root)) if not conflicts else {}
    if conflicts:
        raise PreflightRefused(conflicts)
    hashes = {name: _sha(data) for name, data in release_files.items()}
    release_id = release_id_for(content_digest(hashes))
    inventory = pf_instance.build_control_inventory(release_id, release_files)
    return CandidateSource(source_root, release_files, bootstrap_files, inventory, _sha(inventory), release_id,
                           literals["VERSION"], literals["CHECKPOINT"], literals["INSTALL_CONTRACT"],
                           literals["INSTALL_SCHEMA_VERSION"])


def read_retained_release(root, release_id):
    """A retained release read as data: verify_release with its own inventory, content address, ast literals."""
    root = Path(root)
    subject = str(root / "releases" / str(release_id))
    if not isinstance(release_id, str) or not pf_bootstrap.RELEASE_ID_RE.fullmatch(release_id):
        raise PreflightRefused([Conflict("release-not-retained", subject, "not a release id")])
    path = root / "releases" / release_id
    if not os.path.lexists(str(path)):
        raise PreflightRefused([Conflict("release-not-retained", subject,
                                         "no such retained release under <root>/releases; list them with 'install status'")])
    try:
        inventory, inventory_sha = pf_bootstrap.load_control_inventory(path)
    except (pf_bootstrap.BootstrapError, OSError) as exc:
        raise PreflightRefused([Conflict("release-not-retained", subject, f"inventory unreadable ({exc})")]) from exc
    checker = pf_bootstrap.PathChecker(root)
    pf_bootstrap.verify_release(checker, path, expected_inventory_sha256=inventory_sha, expected_release_id=release_id)
    if checker.blocking():
        raise PreflightRefused([Conflict("release-not-retained", subject,
                                         "the retained release does not verify: " + checker.blocking()[0])])
    if CONTENT_ADDRESSED_RE.fullmatch(release_id) and release_id_for(content_digest(inventory["files"])) != release_id:
        raise PreflightRefused([Conflict("release-not-retained", subject,
                                         "the inventory does not match its content-addressed release id")])
    files = {}
    for name, expected in inventory["files"].items():
        data = pf_instance.read_bytes_nofollow(path / name)
        if _sha(data) != expected:
            raise PreflightRefused([Conflict("release-not-retained", subject, f"{name} changed while it was read")])
        files[name] = data
    conflicts = []
    literals = _candidate_literals(files, conflicts, subject)
    if conflicts:
        raise PreflightRefused(conflicts)
    bootstrap = {"pf_bootstrap.py": files["pf_bootstrap.py"]} if "pf_bootstrap.py" in files else {}
    return CandidateSource(None, files, bootstrap, pf_instance.read_bytes_nofollow(path / pf_bootstrap.CONTROL_INVENTORY_NAME),
                           inventory_sha, release_id, literals["VERSION"], literals["CHECKPOINT"],
                           literals["INSTALL_CONTRACT"], literals["INSTALL_SCHEMA_VERSION"])


# ------------------------------------------------------------------------------------- launcher


def classify_launcher(path):
    """absent | legacy (v2.5 template) | installed (exactly LAUNCHER_TEMPLATE) | foreign (anything else)."""
    path = Path(path)
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return LauncherState(path, "absent", None, b"")
    except OSError:
        return LauncherState(path, "foreign", None, b"")
    if not stat.S_ISREG(info.st_mode) or info.st_size > LAUNCHER_LIMIT:
        return LauncherState(path, "foreign", None, b"")
    try:
        data = pf_instance.read_bytes_nofollow(path)
        text = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return LauncherState(path, "foreign", None, b"")
    match = INSTALLED_LAUNCHER_RE.match(text)
    if match:
        return LauncherState(path, "installed", match.group("root"), data)
    match = LEGACY_LAUNCHER_RE.match(text)
    if match:
        return LauncherState(path, "legacy", match.group("control"), data)
    return LauncherState(path, "foreign", None, data)


def render_launcher(root):
    root = str(root)
    if pf_instance.canonical_path_error(root) is not None or not re.fullmatch(r"/[A-Za-z0-9._/-]+", root):
        raise InstallError("launcher-root-unsupported",
                           f"the installation root {root!r} cannot be embedded in a launcher (only A-Z a-z 0-9 . _ / -)")
    return LAUNCHER_TEMPLATE.format(root=root).encode("utf-8")


def launcher_prefix(root, launcher_path=DEFAULT_LAUNCHER):
    """``sudo pf`` only when the global launcher is the installed launcher of this root."""
    state = classify_launcher(launcher_path)
    if state.kind == "installed" and state.target == str(root):
        return "sudo pf" if Path(launcher_path) == DEFAULT_LAUNCHER else "sudo " + str(launcher_path)
    return f"sudo {Path(root) / pf_instance.BOOTSTRAP_DIR / pf_instance.LAUNCHER_NAME}"


def render_next(root, journal, launcher_path=DEFAULT_LAUNCHER):
    prefix = launcher_prefix(root, launcher_path)
    steps = [f"'{prefix} {verb}'" for verb in journal.get("next") or []]
    if journal.get("phase") == "needs_operator" and steps:
        steps[0] = "repair the named target, then " + steps[0]
    return " or ".join(steps) or "none"


def unreadable_step(root, item):
    """The manual next step of an entry of install-operations/ that is not a readable operation."""
    return (f"inspect {_operations_dir(root) / item['operation_id']} as root and move it out of "
            f"{_operations_dir(root)} (keep a copy: an operation journal is evidence), then run the command again")


def _next_text(root, item):
    return unreadable_step(root, item) if item["phase"] == "unreadable" else render_next(root, item["journal"] or {})


# ----------------------------------------------------------------------------- reading operations


def _operations_dir(base):
    return Path(base) / OPERATIONS_RELATIVE


def _load_json_document(path, name):
    data = pf_instance.read_bytes_nofollow(path)
    value = pf_instance.parse_strict_json(data, label=str(path))
    errors = validate_document(value, name)
    if errors:
        raise InstallError("install-journal-invalid", f"{path}: " + "; ".join(errors[:5]))
    return value, data


def load_operation(root, operation_id):
    """(plan, plan_sha256, journal) of one operation, strictly validated; the journal must pin the plan bytes."""
    if not OPERATION_ID_RE.fullmatch(str(operation_id)):
        raise InstallError("install-journal-invalid", f"{operation_id!r} is not an install operation id")
    directory = _operations_dir(root) / operation_id
    try:
        plan, plan_bytes = _load_json_document(directory / "plan.json", "plan")
        journal, _ = _load_json_document(directory / "journal.json", "journal")
    except (OSError, pf_instance.ContextError) as exc:
        raise InstallError("install-journal-invalid", f"{directory}: {exc}") from exc
    plan_sha = _sha(plan_bytes)
    if journal["plan_sha256"] != plan_sha or plan["operation_id"] != operation_id \
            or journal["operation_id"] != operation_id or journal["kind"] != plan["kind"]:
        raise InstallError("install-journal-invalid", f"{directory}: the journal does not pin this plan")
    return plan, plan_sha, journal


def operations(root):
    """Read-only summaries of every operation directory, sorted by id. Unreadable ones are reported as such."""
    directory = _operations_dir(root)
    try:
        names = sorted(os.listdir(str(directory)))
    except OSError:
        return []
    result = []
    for name in names:
        if name.startswith(".") and name.endswith(".new"):
            result.append({"operation_id": name, "kind": None, "phase": "intent", "updated": None, "next": [],
                           "plan": None, "journal": None, "error": "effect-free intent directory"})
            continue
        try:
            plan, _, journal = load_operation(root, name)
        except InstallError as exc:
            result.append({"operation_id": name, "kind": None, "phase": "unreadable", "updated": None,
                           "next": [], "plan": None, "journal": None, "error": str(exc)})
            continue
        result.append({"operation_id": name, "kind": plan["kind"], "phase": journal["phase"],
                       "updated": journal["updated"], "next": list(journal["next"]), "plan": plan,
                       "journal": journal, "error": None})
    return result


def pending_operations(root, kinds=KINDS):
    """Open operations (OPEN_PHASES, needs_operator included) and, failing closed, unreadable ones."""
    return [item for item in operations(root)
            if (item["phase"] in OPEN_PHASES and item["kind"] in kinds) or item["phase"] == "unreadable"]


def require_no_pending_install(root, instance_id, *, route="this command"):
    """The install gate of Controller.lock (read-only). Raises InstallError
    ``install-operation-pending`` or ``legacy-control-active``."""
    root = Path(root)
    prefix = launcher_prefix(root)
    for item in operations(root):
        plan = item["plan"]
        open_phase = item["phase"] in OPEN_PHASES or item["phase"] == "unreadable"
        pinned = plan is not None and plan["instance"] is not None and plan["instance"]["instance_id"] == instance_id
        if open_phase and (item["kind"] in (None, "control", "init") or pinned):
            raise InstallError(
                "install-operation-pending",
                f"Install operation {item['operation_id']} ({item['kind'] or 'unreadable'}) is open in phase "
                f"{item['phase']}; {route} was refused and nothing was changed. Inspect it with '{prefix} install "
                f"status', then follow its next step ({_next_text(root, item)}).")
        if item["kind"] == "migrate-legacy" and item["phase"] == "completed" and pinned \
                and os.path.lexists(plan["legacy"]["control_dir"]):
            raise InstallError(
                "legacy-control-active",
                f"Instance {plan['instance']['slug']} was migrated from v2.5 (operation {item['operation_id']}) and "
                f"its v2.5 control {plan['legacy']['control_dir']} is still the active control plane; {route} was "
                "refused and nothing was changed. Keep using v2.5 for changes until legacy adoption is installed "
                "(OD-A21-05); status, doctor, ps and logs work here.")


def describe_installation(root, *, instance_ids=None):
    """Read-only display lines: the bound control generation, retained releases, open install operations
    and active legacy controls (of ``instance_ids`` when given)."""
    root = Path(root)
    lines = []
    conf_path = root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME
    try:
        conf_bytes = pf_instance.read_bytes_nofollow(conf_path)
        conf = pf_bootstrap.parse_bootstrap_conf(conf_bytes, label=str(conf_path))
        bound = Path(conf["control_release"]).name
        try:
            retained = sorted(name for name in os.listdir(str(root / "releases")) if name != bound)
        except OSError:
            retained = []
        lines.append(f"Control: bound {bound} (bootstrap.conf sha256 {_sha(conf_bytes)[:12]}) retained: "
                     f"{', '.join(retained) or 'none'}")
    except (OSError, UnicodeDecodeError, pf_bootstrap.BootstrapError) as exc:
        lines.append(f"Control: unavailable ({exc})")
    for item in operations(root):
        if item["phase"] in OPEN_PHASES or item["phase"] == "unreadable":
            lines.append(f"INSTALL OPERATION {item['operation_id']} kind={item['kind'] or 'unknown'} "
                         f"phase={item['phase']}: next: {_next_text(root, item)}")
        plan = item["plan"]
        if item["kind"] == "migrate-legacy" and item["phase"] == "completed" \
                and (instance_ids is None or plan["instance"]["instance_id"] in instance_ids) \
                and os.path.lexists(plan["legacy"]["control_dir"]):
            lines.append(f"LEGACY CONTROL ACTIVE {plan['legacy']['control_dir']}: mutating commands refused "
                         "(legacy-control-active)")
    return lines


# ---------------------------------------------------------------------------------------- runner


def installer_runner(root_home, *, tools, interpreter, docker_endpoint):
    """The installer's one process boundary: the registered tools plus the ``interpreter`` tool id."""
    registered = dict(tools or {})
    if interpreter is not None:
        registered["interpreter"] = str(interpreter)
    root_home = Path(root_home)
    try:
        return pf_runner.ProcessRunner(registered, home=root_home, docker_config=root_home / ".docker",
                                       docker_host=docker_endpoint or DEFAULT_DOCKER_ENDPOINT)
    except pf_runner.RunnerError as exc:
        raise InstallError("tool-untrusted", str(exc)) from exc


def _installed_runner(root, docker_endpoint=None):
    """Runner of an installed root: bootstrap/tools.conf plus the bootstrap.conf interpreter (read-only)."""
    bootstrap = Path(root) / pf_instance.BOOTSTRAP_DIR
    try:
        conf = pf_bootstrap.parse_bootstrap_conf(pf_instance.read_bytes_nofollow(bootstrap / pf_instance.BOOTSTRAP_CONF_NAME),
                                                 label="bootstrap.conf")
        tools = pf_bootstrap.parse_tools_conf(pf_instance.read_bytes_nofollow(bootstrap / pf_bootstrap.TOOLS_CONF_NAME),
                                              label="tools.conf")
    except (OSError, UnicodeDecodeError, pf_bootstrap.BootstrapError) as exc:
        raise InstallError("root-untrusted", f"the installed bootstrap cannot be read ({exc})") from exc
    return installer_runner(Path(root) / ROOT_HOME_RELATIVE, tools=tools, interpreter=conf["interpreter"],
                            docker_endpoint=docker_endpoint)


# ------------------------------------------------------------------------------------ preflight


class _Report:
    def __init__(self):
        self.conflicts = []
        self.notes = []

    def conflict(self, code, subject, detail):
        self.conflicts.append(Conflict(code, str(subject), detail))

    def note(self, code, detail):
        entry = {"code": code, "detail": detail}
        if entry not in self.notes:
            self.notes.append(entry)


@dataclasses.dataclass
class _Preflight:
    plan: dict
    conflicts: list
    notes: list
    candidate: object = None
    runner: object = None
    leftovers: tuple = ()


def _empty_plan(kind, root, operation_id, created):
    return {
        "schema_version": 1, "operation_id": operation_id, "kind": kind, "created": created, "root": str(root),
        "install_contract": INSTALL_CONTRACT, "running_release": None, "candidate": None,
        "selected_existing_release": False, "init_documents": None, "instance": None, "legacy": None,
        "launcher": None, "bindings": [], "config_baseline": [], "verify_baseline": [],
        "app_operation_required": "none", "notes": [],
    }


def _candidate_document(candidate):
    return {"release_id": candidate.release_id, "inventory_sha256": candidate.inventory_sha256,
            "files": {name: _sha(data) for name, data in sorted(candidate.release_files.items())},
            "bootstrap_files": {name: _sha(data) for name, data in sorted(candidate.bootstrap_files.items())},
            "version": candidate.version, "checkpoint": candidate.checkpoint,
            "source_root": None if candidate.source_root is None else str(candidate.source_root)}


def _free_space(report, path, needed):
    try:
        info = os.statvfs(str(path))
    except OSError as exc:
        report.conflict("free-space", path, f"free space cannot be measured ({exc.strerror or exc})")
        return
    available = info.f_bavail * info.f_frsize
    if available < needed + FREE_MARGIN_BYTES:
        report.conflict("free-space", path, f"{available} bytes free; {needed + FREE_MARGIN_BYTES} needed "
                                            "(staged bytes plus a 64 MiB margin). Free space, then run again.")


def _launcher_plan(report, kind, launcher_path, root):
    if launcher_path is None:
        return None
    state = classify_launcher(launcher_path)
    if state.kind != "absent":
        report.note("launcher-left", f"{launcher_path} exists ({state.kind}) and is left unchanged; use "
                                     f"{Path(root) / pf_instance.BOOTSTRAP_DIR / pf_instance.LAUNCHER_NAME}")
        return {"path": str(launcher_path), "before_kind": state.kind,
                "before_sha256": _sha(state.data) if state.data else None, "after_text": None, "action": "leave"}
    checker = pf_bootstrap.PathChecker(root)
    if not checker.ancestors(Path(launcher_path)):
        report.conflict("launcher-parent-untrusted", launcher_path, checker.blocking()[0] + "; choose another "
                        "--launcher-path or --no-launcher")
        return None
    try:
        text = render_launcher(root).decode("utf-8")
    except InstallError as exc:
        report.conflict("root-noncanonical", root, str(exc))
        return None
    return {"path": str(launcher_path), "before_kind": "absent", "before_sha256": None, "after_text": text,
            "action": "create"}


def _registry_checks(report, root):
    try:
        registry = pf_instance.load_registry(root)
    except pf_instance.ContextError as exc:
        report.conflict("registry-invalid", pf_instance.registry_path(root), str(exc))
        return None, []
    contexts = []
    for entry, context, error in registry.records():
        if context is None:
            report.conflict("registry-record-invalid", entry.record_path, f"{entry.slug}: {error}")
        else:
            contexts.append(context)
    return registry, contexts


def _pending_registration_checks(report, root, *, own_instance_id=None):
    for pending in pf_instance.pending_registrations(root):
        if own_instance_id is not None and pending.instance_id == own_instance_id:
            continue
        report.conflict("registry-pending", pending.path, f"{pending.kind}: {pending.error or 'durable reservation'}; "
                        "finish or remove it explicitly first")


def _open_operation_checks(report, root):
    for item in pending_operations(root):
        report.conflict("install-operation-pending", item["operation_id"],
                        f"install operation ({item['kind'] or 'unreadable'}) is open in phase {item['phase']}; follow "
                        f"its next step ({_next_text(root, item)}) first")


def _verify_baseline(root, contexts):
    baseline = []
    for context in contexts:
        validation = pf_instance.validate_context(context, running_release=None)
        for finding in validation.findings:
            if finding.severity == "refuse":
                entry = {"instance_id": context.instance_id, "code": finding.code, "path": finding.path}
                if entry not in baseline:
                    baseline.append(entry)
    return baseline


def _root_trust_check(report, root):
    checker = pf_instance.validate_installation_root(root)
    blocking = checker.blocking()
    if blocking:
        report.conflict("root-untrusted", root, blocking[0] + (f" (+{len(blocking) - 1} more)" if len(blocking) > 1 else ""))
    return checker


def _config_categories(context):
    """(config, env) categories of one instance's live configuration under the running release."""
    directory = context.paths.configuration
    data = _read_optional_safe(directory / "pf-config.json")
    if data is None:
        config = "missing"
    elif data is False:
        config = "invalid"
    else:
        values, problems = pf_config.validate_admin_config(data, label="pf-config.json")
        config = "ok" if not problems and values["project"] == context.compose_project \
            and values["environment"] == context.approved_environment else "invalid"
    data = _read_optional_safe(directory / ".env")
    if data is None:
        env = "missing"
    elif data is False:
        env = "invalid"
    else:
        try:
            pf_config.parse_app_env(data, label=".env")
            env = "ok"
        except pf_config.ConfigError:
            env = "invalid"
    return config, env


def _read_optional_safe(path):
    """Bytes, None when absent, False when not a readable regular file (never blocks; see _read_regular)."""
    try:
        return _read_regular(path)
    except FileNotFoundError:
        return None
    except OSError:
        return False


def _admin_config_check(report, path, data, *, project=None, environment="staging"):
    """Validated admin config dict or None. ``project`` None: the config decides it."""
    if data is None:
        report.conflict("admin-config-missing", path, "create pf-config.json by hand from the release's "
                        "pf-config.example.json (A2.1 creates no configuration), then run again")
        return None
    config, problems = pf_config.validate_admin_config(data, label=str(path))
    if problems:
        report.conflict("admin-config-invalid", path, problems[0])
        return None
    if project is not None and config["project"] != project:
        report.conflict("admin-config-mismatch", path, f"project {config['project']!r} differs from the requested "
                        f"project {project!r}")
        return None
    if config["environment"] != environment:
        report.conflict("admin-config-mismatch", path, f"environment {config['environment']!r} is not {environment!r}")
        return None
    for name in ("backup_read_group", "workspace_write_group"):
        try:
            grp.getgrnam(config[name])
        except KeyError:
            report.conflict("group-missing", path, f"{name} {config[name]!r} does not exist on this host; nothing is "
                            "created or substituted (PERMISSIONS section 1)")
    return config


def _app_env_note(report, path):
    data = _read_optional_safe(path)
    if data in (None, False):
        return
    try:
        pf_config.parse_app_env(data, label=str(path))
    except pf_config.ConfigError as exc:
        report.note("app-env-unparsed", f"{exc}; PF-A2.2 owns the configuration migration")


def _daemon_check(report, runner, endpoint, root):
    """Offline endpoint trust, then the read-only ``docker info`` through the runner. Returns the engine id."""
    if not isinstance(endpoint, str) or not endpoint.startswith(pf_instance.UNIX_SCHEME):
        report.conflict("daemon-endpoint-scheme", endpoint, "the Docker endpoint must be a unix:// socket")
        return None, endpoint
    resolved = pf_instance.UNIX_SCHEME + os.path.realpath(endpoint[len(pf_instance.UNIX_SCHEME):])
    findings = pf_instance.daemon_endpoint_findings(pf_bootstrap.PathChecker(root), resolved)
    blocked = False
    for severity, code, message in findings:
        report.conflict(code if code != "daemon-endpoint-missing" else "daemon-unreachable", resolved, message)
        blocked = True
    if blocked:
        return None, resolved
    spec = pf_runner.ProcessSpec(tool="docker", argv=tuple(pf_docker.DAEMON_PROBE_ARGV[1:]), cwd=str(root),
                                 env=runner.environment(), timeout=30.0, effect=None, label="docker info")
    try:
        result = runner.run(spec)
    except pf_runner.RunnerError as exc:
        report.conflict("daemon-unreachable", resolved, f"the read-only docker info probe could not run ({exc})")
        return None, resolved
    if not result.ok:
        report.conflict("daemon-unreachable", resolved, "docker info failed: " + pf_runner.failure_detail(result, 300))
        return None, resolved
    try:
        observation = pf_docker.parse_daemon_info(result.stdout, endpoint=resolved)
    except pf_docker.DockerScopeError as exc:
        report.conflict("daemon-unreachable", resolved, f"docker info answer refused ({exc.code})")
        return None, resolved
    if observation.rootless:
        report.conflict("daemon-rootless", resolved, "a rootless daemon is not supported; register the rootful system "
                        "daemon socket")
        return None, resolved
    return observation.engine_id, resolved


def _registration_checks(report, root, registry, *, slug, project, paths, engine_id, own_instance_id=None,
                         check_project=True):
    if not isinstance(slug, str) or not re.fullmatch(pf_instance.NAME_PATTERN[1:-1], slug):
        report.conflict("slug-invalid", slug, "a slug is 1-40 characters of a-z 0-9 _ - starting with a letter or digit")
    if check_project and (not isinstance(project, str) or not re.fullmatch(pf_instance.NAME_PATTERN[1:-1], project)):
        report.conflict("project-invalid", project, "a Compose project is 1-40 characters of a-z 0-9 _ -")
    if registry is not None:
        for entry, context, _ in registry.records():
            if entry.slug == slug:
                report.conflict("slug-taken", slug, f"already registered as {entry.instance_id}")
            if context is not None and engine_id is not None and context.state != "purged" \
                    and (context.daemon.engine_id, context.compose_project) == (engine_id, project):
                report.conflict("project-taken", project, f"already registered on daemon {engine_id} by {context.slug}")
        for pending in pf_instance.pending_registrations(root):
            if pending.record is not None and pending.instance_id != own_instance_id and pending.slug == slug:
                report.conflict("slug-taken", slug, f"reserved by pending registration {pending.instance_id}")
    checker = pf_bootstrap.PathChecker(root)
    valid = {}
    for role in pf_instance.ROLE_NAMES:
        value = paths.get(role)
        reason = pf_instance.canonical_path_error(value) if isinstance(value, str) else "missing"
        if reason is not None:
            report.conflict("path-noncanonical", value, f"{role}: {reason}; nothing is normalized")
            continue
        valid[role] = Path(value)
        if not os.path.lexists(value):
            report.conflict("registered-path-missing", value, f"{role} does not exist; nothing is created (OD-A21-10). "
                            "Create it with the documented owner, group and mode, then run again")
            continue
        pf_instance._data_directory(checker, Path(value), role, protected=role in ("backups", "recovery"))
    for finding in checker.findings:
        if finding.severity == "refuse":
            report.conflict(finding.code, finding.path, finding.message)
    if registry is not None and len(valid) == len(pf_instance.ROLE_NAMES):
        others = pf_instance.inventory_of(registry, exclude_instance_id=own_instance_id)
        for code, path, message in pf_instance.managed_path_conflicts(valid, slug, others, root):
            if code not in ("path-component",):
                report.conflict(code, path, message)


def _preflight_registration_common(report, root, plan, request, runner, *, project, config_path, config_data,
                                   legacy=False):
    """register / migrate-legacy: root, registry, daemon, paths, admin config, groups, app env."""
    _root_trust_check(report, root)
    _open_operation_checks(report, root)
    registry, contexts = _registry_checks(report, root)
    _pending_registration_checks(report, root)
    endpoint = request.get("docker_endpoint") or DEFAULT_DOCKER_ENDPOINT
    config = _admin_config_check(report, config_path, config_data, project=None if legacy else project)
    if legacy and config is not None:
        project = config["project"]
    engine_id, resolved = _daemon_check(report, runner, endpoint, root)
    slug = request.get("slug")
    paths = {role: request.get(role) for role in pf_instance.ROLE_NAMES}
    _registration_checks(report, root, registry, slug=slug, project=project, paths=paths, engine_id=engine_id,
                         check_project=not (legacy and config is None))  # the admin-config conflict names the cause
    if all(isinstance(value, str) for value in paths.values()) and project and engine_id:
        plan["instance"] = {"instance_id": request.get("instance_id") or str(uuid.uuid4()), "slug": slug,
                            "compose_project": project, "approved_environment": "staging",
                            "daemon": {"endpoint": resolved, "engine_id": engine_id},
                            "paths": {role: paths[role] for role in pf_instance.ROLE_NAMES}}
    plan["verify_baseline"] = _verify_baseline(root, contexts)
    report.note("default-unchanged", "the registry default is not changed by an install operation")
    return registry


def _preflight_register(report, root, plan, request, runner):
    configuration = request.get("configuration")
    config_path = Path(configuration) / "pf-config.json" if isinstance(configuration, str) else Path("/nonexistent")
    data = _read_optional_safe(config_path) if isinstance(configuration, str) else None
    if data is False:
        report.conflict("admin-config-invalid", config_path, "pf-config.json cannot be read")
        data = b"\x00"
    _preflight_registration_common(report, root, plan, request, runner, project=request.get("project"),
                                   config_path=config_path, config_data=data)
    if isinstance(configuration, str):
        _app_env_note(report, Path(configuration) / ".env")
    _free_space(report, root, 0)


def _legacy_file(report, role, source, target, staged):
    """Plan entry for one legacy file, a conflict, or None (nothing to copy)."""
    name = LEGACY_FILE_NAMES[role]
    source_info = _nofollow_regular(report, source)
    target_info = _nofollow_regular(report, target)
    if source_info is False or target_info is False:
        return None
    if source_info is None:
        return None
    try:
        source_bytes = _read_regular(source, single_link=True)
        target_bytes = None if target_info is None else _read_regular(target, single_link=True)
    except OSError as exc:  # replaced after the lstat: never block on it or read another file's bytes
        report.conflict("legacy-file-unsafe", getattr(exc, "filename", None) or source,
                        f"changed while it was read ({exc.strerror or exc}); replace it with a regular file first")
        return None
    if target_info is not None:
        if target_bytes != source_bytes:
            legacy_path = source
            report.conflict("legacy-env-conflict" if role == "env" else "legacy-admin-config-conflict", target,
                            f"{legacy_path} and {target} both exist and differ. Neither is chosen automatically: "
                            f"compare them, keep the correct one at {target}, move the other out of both locations, "
                            "then run the command again.")
        else:
            report.note("legacy-copy-retained", f"{source} equals {target}; nothing is copied and the legacy copy is "
                                                "retained (remove it only after legacy adoption)")
        return None
    report.note("legacy-copy-retained", f"{source} is copied to {target}; the legacy copy is retained (v2.5 still "
                                        "reads it; remove it only after legacy adoption)")
    return {"role": role, "source": str(source), "target": str(target), "staged": str(staged),
            "sha256": _sha(source_bytes), "size": len(source_bytes), "mode": stat.S_IMODE(source_info.st_mode),
            "gid": source_info.st_gid, "_name": name}


def _nofollow_regular(report, path):
    """lstat result of a regular single-link file, None when absent, False (with a conflict) otherwise."""
    try:
        info = os.lstat(str(path))
    except FileNotFoundError:
        return None
    except OSError as exc:
        report.conflict("legacy-file-unsafe", path, f"cannot be examined ({exc.strerror or exc})")
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        report.conflict("legacy-file-unsafe", path, "not a regular single-link file (links are not followed); "
                                                     "replace it with a regular file first")
        return False
    return info


def _probe_lock(path):
    """True when another process holds ``path`` (LOCK_NB probe, released at once). Read-only."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


def _preflight_migrate(report, root, plan, request, runner, *, under_lock):
    home = request.get("legacy-home")
    workspace = request.get("workspace")
    for label, value in (("legacy-home", home), ("workspace", workspace)):
        reason = pf_instance.canonical_path_error(value) if isinstance(value, str) else "missing"
        if reason is not None:
            report.conflict("path-noncanonical", value, f"{label}: {reason}; nothing is normalized")
    if report.conflicts:
        return
    home, workspace = Path(home), Path(workspace)
    request = dict(request, configuration=str(home / "config"), backups=str(home / "backups"),
                   recovery=str(home / "recovery"))
    control_dir, config_dir = home / "control", home / "config"
    for path in (control_dir, config_dir):
        try:
            if not stat.S_ISDIR(os.lstat(str(path)).st_mode):
                raise OSError("not a directory")
        except OSError as exc:
            report.conflict("legacy-layout-missing", path, f"the v2.5 layout needs this directory ({exc}); "
                            "--legacy-home must name the v2.5 home that holds control/ and config/")
    op8 = _op8(plan["operation_id"])
    for name in sorted(os.listdir(str(config_dir))) if os.path.isdir(str(config_dir)) else []:
        if LEGACY_STAGED_RE.fullmatch(name) and not name.endswith(op8):
            report.conflict("legacy-file-unsafe", config_dir / name, "a staged copy of another install operation "
                            "remains; resolve that operation first")
    files = []
    for role, source, target in (("env", workspace / ".env", config_dir / ".env"),
                                 ("admin-config", workspace / "deploy" / "synology" / "pf-config.json",
                                  config_dir / "pf-config.json")):
        staged = config_dir / f".{LEGACY_FILE_NAMES[role]}.pf-migrate-{op8}"
        entry = _legacy_file(report, role, source, target, staged)
        if entry is not None:
            entry.pop("_name")
            files.append(entry)
    admin = next((item for item in files if item["role"] == "admin-config"), None)
    config_path = Path(admin["source"]) if admin else config_dir / "pf-config.json"
    data = _read_optional_safe(config_path)
    if data is False:
        report.conflict("admin-config-invalid", config_path, "pf-config.json cannot be read")
        data = None
    project = None
    if data is not None:
        values, problems = pf_config.validate_admin_config(data, label=str(config_path))
        if not problems:
            project = values["project"]
    registry = _preflight_registration_common(report, root, plan, request, runner, project=project,
                                              config_path=config_path, config_data=data, legacy=True)
    del registry
    env_entry = next((item for item in files if item["role"] == "env"), None)
    _app_env_note(report, Path(env_entry["source"]) if env_entry else config_dir / ".env")
    project = plan["instance"]["compose_project"] if plan["instance"] else project
    # Every check that does not need the project runs first, so one report holds every conflict (A2-T01).
    launcher_path = None if request.get("no_launcher") else Path(request.get("launcher_path") or DEFAULT_LAUNCHER)
    plan["launcher"] = _launcher_plan(report, "migrate-legacy", launcher_path, root)
    _free_space(report, config_dir if os.path.isdir(str(config_dir)) else root, sum(item["size"] for item in files))
    if project is None:
        report.note("legacy-state-unchecked", f"the v2.5 state checks ({home}/.pf-state-<project>/pending.json and its "
                                              "operation.lock) need the project of a valid pf-config.json; they run "
                                              "after it is fixed")
        return
    state_dir = home / (".pf-state-" + project)
    lock_path = state_dir / "operation.lock"
    checker = pf_bootstrap.PathChecker(root)
    lock_created = False
    if not os.path.lexists(str(state_dir)):
        report.conflict("legacy-state-missing", state_dir, "run any v2.5 command such as 'status' once so v2.5 creates "
                        "its state directory, then run again")
    elif checker.protected(state_dir, kind="dir", allow_group_read=False) is None or checker.blocking():
        report.conflict("legacy-file-unsafe", state_dir, (checker.blocking() or ["not a protected directory"])[0])
    else:
        if not os.path.lexists(str(lock_path)):
            lock_created = True
            report.note("legacy-lock-created", f"{lock_path} is absent and is created empty, as v2.5 itself would")
        elif checker.protected(lock_path, kind="file") is None or checker.blocking():
            report.conflict("legacy-file-unsafe", lock_path, (checker.blocking() or ["not a protected file"])[0])
        elif not under_lock and _probe_lock(lock_path):
            report.conflict("install-busy", lock_path, "a v2.5 command holds its operation lock; let it finish first")
        if os.path.lexists(str(state_dir / "pending.json")):
            report.conflict("legacy-pending-operation", state_dir / "pending.json",
                            "v2.5 recorded an incomplete operation; finish or resolve it with v2.5 first")
    state_report = []
    if os.path.isdir(str(state_dir)) and not os.path.islink(str(state_dir)):
        for name in sorted(os.listdir(str(state_dir))):
            path = state_dir / name
            if name in ("operation.lock", "pending.json") or not os.path.isfile(str(path)) or os.path.islink(str(path)):
                continue
            data = _read_optional_safe(path)
            if isinstance(data, bytes):
                state_report.append({"name": name, "size": len(data), "sha256": _sha(data)})
        if state_report:
            report.note("legacy-state-not-imported", f"{len(state_report)} legacy state file(s) in {state_dir} are "
                                                     "reported and not imported (adoption, OD-A21-06)")
    plan["legacy"] = {"home": str(home), "control_dir": str(control_dir), "state_dir": str(state_dir),
                      "lock_path": str(lock_path), "lock_created": lock_created, "files": files,
                      "state_report": state_report}
    report.note("legacy-control-retained", f"{control_dir} and its launcher stay the working v2.5 control plane")
    report.note("legacy-resources-not-adopted", "Docker resources, containers, volumes and credentials are not "
                                                "adopted; pf mutation stays refused until legacy adoption (OD-A21-05)")


def _instance_journal_checks(report, contexts):
    for context in contexts:
        if os.path.lexists(str(context.journal_path)):
            report.conflict("instance-operation-pending", context.journal_path,
                            f"instance {context.slug} has an incomplete operation; finish it with pf first")
        try:
            names = sorted(os.listdir(str(context.operations_dir)))
        except OSError:
            names = []
        for name in names:
            path = context.operations_dir / name / "unresolved-effects.json"
            try:
                effects = pf_runner.load_unresolved_effects(path)
            except pf_runner.RunnerError:
                effects = [None]
            if effects:
                report.conflict("instance-effects-unresolved", path,
                                f"instance {context.slug} recorded unresolved effects; observe and resolve them first")


def _bootstrap_equality(report, root, candidate, running_release):
    bootstrap = Path(root) / pf_instance.BOOTSTRAP_DIR
    pairs = [("pf_bootstrap.py", bootstrap / pf_instance.BOOTSTRAP_MODULE_NAME)]
    if "pf" in candidate.bootstrap_files:
        pairs.insert(0, ("pf", bootstrap / pf_instance.LAUNCHER_NAME))
    for name, installed in pairs:
        current = _read_optional_safe(installed)
        data = candidate.bootstrap_files.get(name, candidate.release_files.get(name))
        if not isinstance(current, bytes) or data != current:
            label = BOOTSTRAP_FILES[name] if candidate.source_root is not None else name
            report.conflict("bootstrap-change-unsupported", installed,
                            f"{label} differs from the installed bootstrap copy; changing the launcher or verifier is "
                            "a launcher migration (PF-A4.3). Reinstall into a new root or keep these bytes unchanged.")
    if running_release is not None:
        running = _read_optional_safe(Path(running_release) / "pf_bootstrap.py")
        if not isinstance(running, bytes) or running != _read_optional_safe(bootstrap / pf_instance.BOOTSTRAP_MODULE_NAME):
            report.conflict("bootstrap-copy-mismatch", bootstrap / pf_instance.BOOTSTRAP_MODULE_NAME,
                            "the running release's pf_bootstrap.py differs from the installed bootstrap copy")


def _preflight_control(report, root, plan, request, runner, running_release):
    _root_trust_check(report, root)
    _open_operation_checks(report, root)
    registry, contexts = _registry_checks(report, root)
    _pending_registration_checks(report, root)
    candidate = None
    source, release = request.get("source"), request.get("release")
    try:
        candidate = read_candidate(source) if source is not None else read_retained_release(root, release)
    except PreflightRefused as exc:
        report.conflicts.extend(exc.conflicts)
    conf_path = Path(root) / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME
    try:
        conf_bytes = pf_instance.read_bytes_nofollow(conf_path)
        conf = pf_bootstrap.parse_bootstrap_conf(conf_bytes, label=str(conf_path))
    except (OSError, UnicodeDecodeError, pf_bootstrap.BootstrapError) as exc:
        report.conflict("root-untrusted", conf_path, str(exc))
        return None, False
    bound = Path(conf["control_release"])
    plan["running_release"] = Path(running_release).name if running_release is not None else bound.name
    if candidate is None:
        return None, False
    plan["candidate"] = _candidate_document(candidate)
    if (candidate.install_contract, candidate.install_schema_version) != (INSTALL_CONTRACT, INSTALL_SCHEMA_VERSION):
        report.conflict("install-contract-incompatible", candidate.release_id,
                        f"INSTALL_CONTRACT/INSTALL_SCHEMA_VERSION {candidate.install_contract}/"
                        f"{candidate.install_schema_version} differ from the running {INSTALL_CONTRACT}/"
                        f"{INSTALL_SCHEMA_VERSION}")
    _bootstrap_equality(report, root, candidate, running_release)
    unchanged = candidate.inventory_sha256 == conf["control_release_sha256"] and candidate.release_id == bound.name
    if release is not None and release == bound.name:
        unchanged = True
    target = Path(root) / "releases" / candidate.release_id
    if source is not None and os.path.lexists(str(target)) and not unchanged:
        checker = pf_bootstrap.PathChecker(root)
        pf_bootstrap.verify_release(checker, target, expected_inventory_sha256=candidate.inventory_sha256,
                                    expected_release_id=candidate.release_id)
        if checker.blocking():
            report.conflict("release-id-collision", target, "a release with this content-addressed id exists and does "
                            "not verify against the candidate inventory: " + checker.blocking()[0])
        else:
            plan["selected_existing_release"] = True
    if release is not None:
        plan["selected_existing_release"] = True
    _instance_journal_checks(report, contexts)
    baseline = []
    for context in sorted(contexts, key=lambda item: item.instance_id):
        config, env = _config_categories(context)
        baseline.append({"instance_id": context.instance_id, "config": config, "env": env})
        if (config, env) != ("ok", "ok"):
            report.note("config-baseline-invalid", f"instance {context.slug}: pf-config.json {config}, .env {env} under "
                                                   "the running release (not a regression of the candidate)")
    plan["config_baseline"] = baseline
    plan["verify_baseline"] = _verify_baseline(root, contexts)
    if not plan["selected_existing_release"]:
        _free_space(report, root, sum(len(data) for data in candidate.release_files.values()))
    bound_files = {}
    try:
        bound_files = pf_bootstrap.load_control_inventory(bound)[0]["files"]
    except (pf_bootstrap.BootstrapError, OSError):
        pass
    if bound_files.get("compose.nas.yaml") != plan["candidate"]["files"].get("compose.nas.yaml"):
        plan["app_operation_required"] = "update"
        report.note("app-operation-required", "the new release changes compose.nas.yaml; the running application keeps "
                                              "its containers until the next 'pf --instance <slug> update'")
    plan["bindings"] = _control_bindings(root, conf, conf_bytes, candidate, contexts, registry)
    report.note("default-unchanged", "the registry default is not changed by an install operation")
    return candidate, unchanged


def _control_bindings(root, conf, conf_bytes, candidate, contexts, registry):
    root = Path(root)
    release_dir = root / "releases" / candidate.release_id
    after_conf = pf_instance.render_bootstrap_conf(conf["interpreter"], str(release_dir), candidate.inventory_sha256)
    bindings = [_binding("bind-bootstrap-conf", "bootstrap-conf",
                         root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME, conf_bytes, after_conf)]
    for context in sorted(contexts, key=lambda item: item.instance_id):
        before = pf_instance.read_bytes_nofollow(context.record_path)
        record = pf_instance.parse_strict_json(before, label=str(context.record_path))
        record["control"] = {"release_id": candidate.release_id, "path": str(release_dir),
                             "sha256": candidate.inventory_sha256}
        record["record_revision"] = record["record_revision"] + 1
        bindings.append(_binding("rebind-record:" + context.instance_id, "record", context.record_path, before,
                                 pf_instance.normalize_json(record)))
    for name in WRAPPER_NAMES:
        data = candidate.bootstrap_files.get(name)
        if data is None:
            continue
        current = _read_optional(root / pf_instance.BOOTSTRAP_DIR / name)
        if current != data:
            bindings.append(_binding("replace-wrapper:" + name, "wrapper", root / pf_instance.BOOTSTRAP_DIR / name,
                                     current, data))
    return bindings


def _binding(effect_id, kind, target, before, after):
    return {"effect_id": effect_id, "type": kind, "target": str(target),
            "before_text": None if before is None else before.decode("utf-8"),
            "before_sha256": None if before is None else _sha(before),
            "after_text": after.decode("utf-8"), "after_sha256": _sha(after)}


def _init_documents(version):
    profile = pf_instance.normalize_json({"schema_version": 1, "id": pf_instance.PROFILE_ID, "version": version,
                                          "application": pf_instance.PROFILE_APPLICATION,
                                          "compose_file": pf_instance.PROFILE_COMPOSE_FILE})
    policy = pf_instance.normalize_json({"schema_version": 1, "revision": 1, "environment": "staging"})
    return profile, policy


def _other_filesystem(root):
    """True when the existing directory ``root`` is a mount point or not on its parent's device (a btrfs
    subvolume or DSM shared folder): the init build directory is renamed onto it, which cannot cross devices."""
    try:
        return os.path.ismount(str(root)) or os.lstat(str(root)).st_dev != os.lstat(str(root.parent)).st_dev
    except OSError:
        return True


def _init_leftovers(report, root, *, own=None, notes=True):
    """Classify every ``.<name>.init-*`` sibling of ``root``. Returns the own, unlocked leftovers.

    ``notes`` False (the re-check under the locks): an own leftover found then is left for the next init."""
    root = Path(root)
    parent = root.parent
    prefix = "." + root.name + ".init-"
    own_leftovers = []
    try:
        names = sorted(os.listdir(str(parent)))
    except OSError:
        return own_leftovers
    for name in names:
        if not name.startswith(prefix) or (own is not None and parent / name == Path(own)):
            continue
        path = parent / name
        verdict = _leftover_verdict(path, root, name[len(prefix):])
        if verdict == "busy":
            report.conflict("install-busy", path, "another init holds this build directory; let it finish first")
        elif verdict == "own":
            if not notes:
                continue
            own_leftovers.append(path)
            report.note("init-leftover-removed", f"{path} is an unpublished build of an interrupted init; it is "
                                                 "removed after the confirmation")
        else:
            report.conflict("init-leftover-unknown", path, f"not recognized as an own unpublished build ({verdict}); "
                                                           "inspect it and remove it by hand, then run again")
    return own_leftovers


def _leftover_verdict(path, root, op8):
    if not re.fullmatch(r"[0-9a-f]{8}", op8):
        return "name"
    try:
        info = os.lstat(str(path))
    except OSError as exc:
        return str(exc)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != pf_bootstrap.TRUSTED_UID:
        return "not a root-owned directory"
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        return str(exc)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "busy"
        names = os.listdir(str(path))
        if names:
            operations_dir = path / OPERATIONS_RELATIVE
            try:
                entries = os.listdir(str(operations_dir))
            except OSError:
                return "no install-operations/"
            if not entries:
                # An own crash window: write_intent created install-operations/ but not the intent yet, or the
                # removal of a cancelled build stopped after the operation directory.
                if names != [str(OPERATIONS_RELATIVE)]:
                    return "an empty install-operations/ beside other content"
                entries = None
            elif len(entries) != 1:
                return "install-operations/ must hold exactly one operation"
        if names and entries is not None:
            entry = entries[0]
            operation_id = entry[1:-4] if entry.startswith(".") and entry.endswith(".new") else entry
            match = OPERATION_ID_RE.fullmatch(operation_id)
            if not match or match.group(2) != op8:
                return "operation id does not match the directory name"
            plan_bytes = _read_optional_safe(operations_dir / entry / "plan.json")
            if isinstance(plan_bytes, bytes):
                try:
                    plan = pf_instance.parse_strict_json(plan_bytes, label="plan.json")
                except pf_instance.ContextError:
                    plan = None
                if not isinstance(plan, dict) or plan.get("kind") != "init" or plan.get("root") != str(root):
                    return "its plan is not an init of this root"
        if os.path.lexists(str(root)) and (not os.path.isdir(str(root)) or os.listdir(str(root))):
            return "the root already exists"
        return "own"
    finally:
        os.close(fd)


def _init_tools(report, requested):
    tools = {}
    for tool, path in sorted((requested or {}).items()):
        if tool not in pf_bootstrap.TOOL_IDS:
            report.conflict("tool-untrusted", tool, f"unknown tool id; known ids: {', '.join(pf_bootstrap.TOOL_IDS)}")
            continue
        try:
            pf_runner.resolve_trusted_executable(path)
        except pf_runner.RunnerError as exc:
            report.conflict("tool-untrusted", path, str(exc))
            continue
        tools[tool] = path
    for tool, candidates in TOOL_CANDIDATES.items():
        if tool in tools or tool in (requested or {}):
            continue
        for path in candidates:
            if not os.path.lexists(path):
                continue
            try:
                pf_runner.resolve_trusted_executable(path)
            except pf_runner.RunnerError:
                continue
            tools[tool] = path
            break
    return tools


def _preflight_init(report, root_text, plan, request, *, under_lock=False, own_build=None):
    candidate = None
    try:
        candidate = read_candidate(request["source_root"])
    except PreflightRefused as exc:
        report.conflicts.extend(exc.conflicts)
    reason = pf_instance.canonical_path_error(root_text) if isinstance(root_text, str) else "missing"
    if reason is not None:
        report.conflict("root-noncanonical", root_text, f"the installation root must be one canonical absolute path "
                                                        f"({reason}); nothing is normalized")
        return candidate, None, ()
    root = Path(root_text)
    checker = pf_bootstrap.PathChecker(root)
    if not checker.ancestors(root):
        report.conflict("root-untrusted", root, checker.blocking()[0])
    if os.path.lexists(root_text) and (os.path.islink(root_text) or not os.path.isdir(root_text)
                                       or os.listdir(root_text)):
        report.conflict("root-exists", root, "init creates a new installation root only; choose an absent or empty "
                                             "directory (an existing root is managed with 'pf install')")
    elif os.path.isdir(root_text) and _other_filesystem(root):
        report.conflict("root-exists", root, "the empty root is a mount point or another filesystem than its parent "
                                             f"{root.parent}; init publishes with one atomic rename of a sibling build "
                                             "directory, so choose an absent path on the parent's filesystem")
    try:
        render_launcher(root)
    except InstallError as exc:
        report.conflict("root-noncanonical", root, str(exc))
    # Under the locks the scan runs again without this init's own build: another init's busy build (one that
    # confirmed at the same time) refuses here, before anything is published (CC-6).
    leftovers = tuple(_init_leftovers(report, root, own=own_build, notes=not under_lock))
    interpreter = request.get("interpreter") or os.path.realpath(sys.executable)
    runner = None
    try:
        pf_runner.resolve_trusted_executable(interpreter)
        if os.path.realpath(interpreter) != interpreter:
            raise pf_runner.RunnerError(f"interpreter-noncanonical: {interpreter} resolves to "
                                        f"{os.path.realpath(interpreter)}; register the canonical path")
    except pf_runner.RunnerError as exc:
        report.conflict("interpreter-untrusted", interpreter, str(exc))
        interpreter = None
    tools = _init_tools(report, request.get("tools"))
    if interpreter is not None:
        runner = installer_runner(root / ROOT_HOME_RELATIVE, tools=tools, interpreter=interpreter, docker_endpoint=None)
        spec = pf_runner.ProcessSpec(tool="interpreter", argv=("-I", "-B", "-c", "import sys; print(list(sys.version_info[:3]))"),
                                     cwd=str(root.parent) if os.path.isdir(str(root.parent)) else "/",
                                     env=runner.environment(), timeout=SMOKE_TIMEOUT, effect=None, label="version")
        try:
            result = runner.run(spec)
            version = json.loads(result.stdout) if result.ok else None
        except (pf_runner.RunnerError, ValueError):
            version = None
        if not (isinstance(version, list) and len(version) == 3 and all(type(part) is int for part in version)):
            report.conflict("interpreter-untrusted", interpreter, "the version probe did not answer")
        elif tuple(version) < (3, 9):
            report.conflict("interpreter-too-old", interpreter, f"Python {'.'.join(map(str, version))}; 3.9 or newer "
                                                                "is required")
    launcher_path = None if request.get("no_launcher") else Path(request.get("launcher_path") or DEFAULT_LAUNCHER)
    plan["launcher"] = _launcher_plan(report, "init", launcher_path, root)
    if candidate is not None:
        plan["candidate"] = _candidate_document(candidate)
        profile, policy = _init_documents(candidate.version)
        plan["init_documents"] = {"profile": {"name": PROFILE_NAME, "sha256": _sha(profile), "version": candidate.version},
                                  "policy": {"name": POLICY_NAME, "sha256": _sha(policy), "revision": 1}}
        if interpreter is not None:
            conf = pf_instance.render_bootstrap_conf(interpreter, str(root / "releases" / candidate.release_id),
                                                     candidate.inventory_sha256)
            # Descriptive bindings: both files are bytes of the one build-root effect, so both carry its effect id;
            # init resolves them by type (_apply_build_root), never with _Run.binding(effect_id).
            plan["bindings"] = [
                _binding("build-root", "bootstrap-conf",
                         root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME, None, conf),
                _binding("build-root", "tools-conf", root / pf_instance.BOOTSTRAP_DIR / pf_bootstrap.TOOLS_CONF_NAME,
                         None, pf_bootstrap.render_tools_conf(tools)),
            ]
        if (candidate.install_contract, candidate.install_schema_version) != (INSTALL_CONTRACT, INSTALL_SCHEMA_VERSION):
            report.conflict("install-contract-incompatible", candidate.release_id, "the candidate's install contract "
                            "differs from this installer's")
        _free_space(report, root.parent if os.path.isdir(str(root.parent)) else "/",
                    sum(len(data) for data in candidate.release_files.values())
                    + sum(len(data) for data in candidate.bootstrap_files.values()))
    return candidate, runner, leftovers


def _preflight(root, kind, request, *, runner, running_release, under_lock=False, pinned=None, own_build=None):
    """All conflicts of one operation kind, read-only. Returns _Preflight. ``pinned`` (operation_id, created,
    instance_id) reproduces a plan under the locks."""
    if kind not in KINDS:
        raise InstallError("install-kind-unknown", f"unknown install kind {kind!r}")
    report = _Report()
    operation_id, created, instance_id = pinned or (new_operation_id(), _utc_text(), None)
    root_text = str(request.get("root") if kind == "init" else root)
    plan = _empty_plan(kind, root_text, operation_id, created)
    candidate, leftovers = None, ()
    if kind == "init":
        candidate, runner, leftovers = _preflight_init(report, root_text, plan, request, under_lock=under_lock,
                                                       own_build=own_build)
    elif kind == "control":
        candidate, unchanged = _preflight_control(report, Path(root), plan, request, runner, running_release)
        if unchanged and not report.conflicts:
            wrappers = [Path(item["target"]).name for item in plan["bindings"] if item["type"] == "wrapper"]
            extra = "" if not wrappers else (
                f" The candidate's scheduler wrapper(s) {', '.join(wrappers)} differ from "
                f"{Path(root) / pf_instance.BOOTSTRAP_DIR}; a wrapper-only change is not installed on its own: it is "
                "installed together with the next control release whose files change.")
            raise InstallError("control-unchanged", f"Release {candidate.release_id} is already the bound control; "
                                                    "nothing was changed." + extra, exit_code=0)
    else:
        request = dict(request)
        if instance_id is not None:
            request["instance_id"] = instance_id
        if kind == "register":
            _preflight_register(report, Path(root), plan, request, runner)
        else:
            _preflight_migrate(report, Path(root), plan, request, runner, under_lock=under_lock)
    plan["notes"] = list(report.notes)
    if not report.conflicts:
        errors = validate_document(plan, "plan")
        if errors:
            raise InstallError("install-plan-invalid", "the computed plan does not match its schema: " + "; ".join(errors))
    return _Preflight(plan, report.conflicts, report.notes, candidate, runner, leftovers)


def preflight(root, kind, request, *, runner, running_release):
    """Read-only preflight of one kind: (plan, conflicts, notes). Takes no lock (a legacy lock probe
    releases at once). Raises InstallError ``control-unchanged`` (exit 0) for an identical candidate."""
    result = _preflight(root, kind, request, runner=runner, running_release=running_release)
    return result.plan, result.conflicts, result.notes


# --------------------------------------------------------------------------------------- summary


def confirmation_phrase(plan):
    kind = plan["kind"]
    if kind == "init" or (kind == "control" and not plan["selected_existing_release"]):
        return "INSTALL CONTROL " + plan["candidate"]["release_id"]
    if kind == "control":
        return "SELECT CONTROL " + plan["candidate"]["release_id"]
    if kind == "register":
        return "REGISTER " + plan["instance"]["slug"]
    return "MIGRATE " + plan["instance"]["slug"]


def render_summary(plan, *, default_slug=None, slugs=None):
    kind, root = plan["kind"], plan["root"]
    lines = [f"Install plan {_op8(plan['operation_id'])} ({kind})",
             f"Plan sha256: {_sha(_document_bytes(plan))[:12]} (operation {plan['operation_id']})",
             f"Installation root: {root}"]
    candidate = plan["candidate"]
    if candidate is not None:
        sha12 = candidate["inventory_sha256"][:12]
        lines.append(f"Control: {plan['running_release'] or 'none'} -> {candidate['release_id']} ({candidate['checkpoint']}, "
                     f"{len(candidate['files'])} files, inventory {sha12})")
        if plan["selected_existing_release"]:
            lines.append("  The release is already published; it is selected again after the same smoke and checks.")
    if plan["init_documents"] is not None:
        lines.append("Approved policy: staging revision 1; profile partflow-staging-legacy "
                     + plan["init_documents"]["profile"]["version"])
        for binding in plan["bindings"]:
            lines.append(f"  {Path(binding['target']).name}: " + "; ".join(
                line for line in binding["after_text"].splitlines() if line and not line.startswith("#")))
    if kind == "control":
        lines.append("Instances rebound: " + (", ".join(slugs or []) or "none"))
    elif plan["instance"] is not None:
        instance = plan["instance"]
        lines.append(f"Instance: {instance['slug']} project={instance['compose_project']} "
                     f"daemon={instance['daemon']['engine_id']}")
        lines.append(f"  Docker endpoint: {instance['daemon']['endpoint']} (resolved; read-only docker info only)")
        lines.append("Paths:")
        for role in pf_instance.ROLE_NAMES:
            value = instance["paths"][role]
            marker = " (as given)" if role == "workspace" and Path(value).name != "repo" else ""
            lines.append(f"  {role}: {value}{marker}")
    legacy = plan["legacy"]
    if legacy is not None:
        lines.append("Legacy files:")
        for item in legacy["files"]:
            lines.append(f"  copy {item['source']} -> {item['target']} ({item['size']} bytes, mode "
                         f"{oct(item['mode'])}, gid {item['gid']}; contents not shown)")
        if not legacy["files"]:
            lines.append("  none to copy")
        lines.append(f"The v2.5 control {legacy['home']}/control and its launcher stay in place and remain the control "
                     f"plane for every change to {plan['instance']['slug']}. pf mutating commands on "
                     f"{plan['instance']['slug']} stay refused (legacy-control-active) until legacy adoption is installed "
                     "(OD-A21-05); status, doctor, ps and logs work.")
        lines.append(f"Legacy runtime state in {legacy['state_dir']} is not imported; the v2.5 lock is held during this "
                     "operation.")
    launcher = plan["launcher"]
    if launcher is not None:
        action = "create" if launcher["action"] == "create" else \
            f"left unchanged (use {Path(root) / pf_instance.BOOTSTRAP_DIR / pf_instance.LAUNCHER_NAME})"
        lines.append(f"Global launcher {launcher['path']}: {action}")
    lines.append(f"Default instance: unchanged ({default_slug or 'none'})")
    app = ("The new release changes compose.nas.yaml: the running application keeps its containers; the next 'pf "
           "--instance <slug> update' applies the new topology (a separate application operation).") \
        if plan["app_operation_required"] == "update" else "No application operation is required."
    lines.append("Application: containers, volumes, images, credentials and data are not touched. " + app)
    lines.append("Notes:")
    for note in plan["notes"]:
        lines.append(f"  - {note['code']}: {note['detail']}")
    return lines


# ---------------------------------------------------------------------------------- execution


def _journal_write(path, data):
    """The one journal/plan writer (fsync, atomic rename, parent fsync). Tests' forked-crash seam."""
    pf_instance._write_private_file(path, data, 0o600)


def _planned_effects(plan):
    kind = plan["kind"]
    if kind == "init":
        effects = [("build-root", "build-root"), ("smoke", "smoke"), ("publish-root", "publish-root"),
                   ("verify", "verify")]
        if plan["launcher"] is not None and plan["launcher"]["action"] == "create":
            effects.append(("bind-launcher", "bind-launcher"))
        return effects
    if kind == "register":
        return [("register-instance", "register-instance"), ("verify", "verify")]
    if kind == "migrate-legacy":
        files = plan["legacy"]["files"]
        effects = [("stage-legacy-file:" + item["role"], "stage-legacy-file") for item in files]
        effects.append(("validate", "validate"))
        effects += [("publish-legacy-file:" + item["role"], "publish-legacy-file") for item in files]
        effects += [("register-instance", "register-instance"), ("verify", "verify")]
        if plan["launcher"] is not None and plan["launcher"]["action"] == "create":
            effects.append(("bind-launcher", "bind-launcher"))
        return effects
    effects = [] if plan["selected_existing_release"] else [("stage-release", "stage-release")]
    effects.append(("smoke", "smoke"))
    if not plan["selected_existing_release"]:
        effects.append(("publish-release", "publish-release"))
    effects.append(("pre-bind-verify", "pre-bind-verify"))
    effects += [(binding["effect_id"], binding["effect_id"].split(":", 1)[0]) for binding in plan["bindings"]]
    effects.append(("verify", "verify"))
    return effects


COMMIT_EFFECT = {"init": "publish-root", "register": "register-instance", "migrate-legacy": "register-instance",
                 "control": "bind-bootstrap-conf"}
PHASE_ORDER = ("planned", "prepared", "validated", "switching", "verifying", "completed")
DURING = {"build-root": "planned", "stage-release": "planned", "stage-legacy-file": "planned",
          "publish-root": "switching", "bind-bootstrap-conf": "switching", "rebind-record": "switching",
          "replace-wrapper": "switching", "register-instance": "switching", "verify": "verifying",
          "bind-launcher": "verifying"}
AFTER = {"build-root": "prepared", "stage-release": "prepared", "stage-legacy-file": "prepared", "smoke": "validated",
         "validate": "validated"}
CHECK_TYPES = ("smoke", "validate", "pre-bind-verify", "verify")
CONSUMER = {"build-root": "publish-root", "stage-release": "publish-release", "stage-legacy-file": "publish-legacy-file"}


class _Run:
    """One install operation being executed or resumed. Holds its locks for its whole life."""

    def __init__(self, root, plan, plan_sha, journal, *, base, runner, interaction, candidate=None):
        self.root = Path(root)
        self.plan = plan
        self.plan_sha = plan_sha
        self.journal = journal
        self.base = Path(base)
        self.runner = runner
        self.interaction = interaction
        self.candidate = candidate
        self.registry_lock = None
        self.handles = []
        self.build_fd = None
        self.messages = []

    # -- paths and journal ----------------------------------------------------------------

    @property
    def operation_id(self):
        return self.plan["operation_id"]

    @property
    def op_dir(self):
        return self.base / OPERATIONS_RELATIVE / self.operation_id

    @property
    def build_dir(self):
        root = Path(self.plan["root"])
        return root.parent / f".{root.name}.init-{_op8(self.operation_id)}"

    def say(self, text):
        if self.interaction is not None:
            self.interaction.say(text)

    def save(self, **changes):
        self.journal.update(changes)
        self.journal["sequence"] += 1
        self.journal["updated"] = _utc_text()
        errors = validate_document(self.journal, "journal")
        if errors:
            raise InstallError("install-journal-invalid", "journal generation refused: " + "; ".join(errors))
        _journal_write(self.op_dir / "journal.json", _document_bytes(self.journal))

    def entry(self, effect_id):
        for item in self.journal["effects"]:
            if item["effect_id"] == effect_id:
                return item
        return None

    def _set_entry(self, effect_id, etype, **fields):
        item = self.entry(effect_id)
        if item is None:
            item = {"effect_id": effect_id, "type": etype, "state": "intended", "observed": None,
                    "target_identity": None, "at": _utc_text()}
            self.journal["effects"].append(item)
        item.update(fields, at=_utc_text())
        return item

    def _advance(self, phase):
        current = self.journal["phase"]
        if phase is None or current not in PHASE_ORDER:
            return current
        return phase if PHASE_ORDER.index(phase) > PHASE_ORDER.index(current) else current

    def intend(self, effect_id, etype, observed=None):
        self._set_entry(effect_id, etype, state="intended", observed=observed)
        self.save(phase=self._advance(DURING.get(etype)))

    def complete(self, effect_id, etype, *, identity=None, observed=None):
        fields = {"state": "complete"}
        if observed is not None:
            fields["observed"] = observed
        if identity is not None:
            fields["target_identity"] = identity
        self._set_entry(effect_id, etype, **fields)
        self.save(phase=self._advance(AFTER.get(etype)))

    def committed(self):
        item = self.entry(COMMIT_EFFECT[self.plan["kind"]])
        if item is not None and self.plan["kind"] == "init" and item["state"] != "complete" \
                and os.path.lexists(str(self.build_dir)):
            return False  # publish-root is one rename: while the build directory exists nothing was published
        return item is not None

    def direction(self):
        if self.entry("abandon") is not None:
            return "abandon"
        if self.entry("rollback") is not None:
            return "rollback"
        return "forward"

    def next_steps(self):
        steps = ["install resume"]
        if self._abandon_reason() is None:
            steps.append("install resume --abandon")
        return steps

    # -- locks ----------------------------------------------------------------------------

    def _acquire(self, path, label):
        try:
            handle = pf_instance.acquire_lock(path, busy_message=f"{label} is held")
        except pf_instance.LockBusy as exc:
            raise InstallError("install-busy", f"Another operation holds {path}; nothing was changed. Try again after "
                                               "it finishes.") from exc
        except pf_instance.ContextError as exc:
            raise InstallError("install-busy", f"{path} cannot be locked ({exc}); nothing was changed.") from exc
        self.handles.append(handle)
        return handle

    def lock(self):
        kind = self.plan["kind"]
        if kind == "init":
            if self.base == self.root:  # resume after publication: the registry lock only
                self.registry_lock = self._acquire(self.root / pf_instance.REGISTRY_LOCK_RELATIVE, "the registry lock")
            return
        self.registry_lock = self._acquire(self.root / pf_instance.REGISTRY_LOCK_RELATIVE, "the registry lock")
        if kind == "migrate-legacy":
            lock_path = Path(self.plan["legacy"]["lock_path"])
            if not os.path.lexists(str(lock_path)):
                try:
                    pf_instance._create_lock_file(lock_path)
                except FileExistsError:
                    pass
            self._acquire(lock_path, "the v2.5 operation lock")
        elif kind == "control":
            registry = pf_instance.load_registry(self.root)
            for entry in sorted(registry.entries, key=lambda item: item.instance_id):
                self._acquire(self.root / "locks" / (entry.instance_id + ".lock"), f"the lock of instance {entry.slug}")

    def lock_build(self):
        """init: create the private sibling build directory and hold flock on it."""
        build = self.build_dir
        pf_instance._create_private_dir(build, 0o700)
        fd = os.open(str(build), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            pf_instance._remove_tree(build)
            raise InstallError("install-busy", f"Another operation holds {build}; nothing was changed. Try again after "
                                               "it finishes.") from exc
        self.build_fd = fd

    def release(self):
        for handle in reversed(self.handles):
            handle.release()
        self.handles = []
        if self.build_fd is not None:
            os.close(self.build_fd)
            self.build_fd = None

    # -- intent ---------------------------------------------------------------------------

    def write_intent(self):
        operations_dir = self.base / OPERATIONS_RELATIVE
        if not os.path.lexists(str(operations_dir)):
            pf_instance._create_private_dir(operations_dir, 0o700)
        intent = operations_dir / f".{self.operation_id}.new"
        pf_instance._create_private_dir(intent, 0o700)
        _journal_write(intent / "plan.json", _document_bytes(self.plan))
        _journal_write(intent / "journal.json", _document_bytes(self.journal))
        os.rename(str(intent), str(self.op_dir))
        pf_instance._fsync_directory(operations_dir)

    # -- observation ----------------------------------------------------------------------

    def binding(self, effect_id):
        return next(item for item in self.plan["bindings"] if item["effect_id"] == effect_id)

    def legacy_file(self, role):
        return next(item for item in self.plan["legacy"]["files"] if item["role"] == role)

    def release_dir(self):
        return self.root / "releases" / self.plan["candidate"]["release_id"]

    def staging_dir(self):
        return self.op_dir / "release.staging"

    def observe(self, effect_id, etype):
        """(state, detail): not_started | complete | partial | unknown, from disk."""
        method = getattr(self, "_observe_" + etype.replace("-", "_"), None)
        return method(effect_id) if method else ("not_started", "")

    def _observe_file(self, effect_id):
        binding = self.binding(effect_id)
        data = _read_optional_safe(Path(binding["target"]))
        if data is False:
            return "unknown", "unreadable"
        if data is None:
            return ("not_started", "absent") if binding["before_sha256"] is None else ("unknown", "absent")
        digest = _sha(data)
        if digest == binding["after_sha256"]:
            return "complete", digest
        if digest == binding["before_sha256"]:
            return "not_started", digest
        return "unknown", digest

    _observe_bind_bootstrap_conf = _observe_file
    _observe_rebind_record = _observe_file
    _observe_replace_wrapper = _observe_file

    def _observe_build_root(self, effect_id):
        if not os.path.lexists(str(self.build_dir)):
            return "partial", "build directory absent"
        names = os.listdir(str(self.build_dir))
        return ("not_started", "") if names == [str(OPERATIONS_RELATIVE)] else ("partial", "partial build")

    def _observe_stage_release(self, effect_id):
        staging = self.staging_dir()
        if not os.path.lexists(str(staging)):
            return "not_started", "absent"
        checker = pf_bootstrap.PathChecker(self.root)
        candidate = self.plan["candidate"]
        pf_bootstrap.verify_release(checker, staging, expected_inventory_sha256=candidate["inventory_sha256"],
                                    expected_release_id=candidate["release_id"])
        return ("partial", checker.blocking()[0]) if checker.blocking() else ("complete", "")

    def _observe_publish_release(self, effect_id):
        source, target = self.staging_dir(), self.release_dir()
        identity = (self.entry("stage-release") or {}).get("target_identity")
        if not os.path.lexists(str(target)):
            return "not_started", "" if os.path.lexists(str(source)) else "nothing staged"
        if not os.path.lexists(str(source)) and _same_identity(target, identity):
            checker = pf_bootstrap.PathChecker(self.root)
            pf_bootstrap.verify_release(checker, target, expected_inventory_sha256=self.plan["candidate"]["inventory_sha256"],
                                        expected_release_id=self.plan["candidate"]["release_id"])
            if not checker.blocking():
                return "complete", ""
        return "unknown", "staging/release state not written by this operation"

    def _observe_publish_root(self, effect_id):
        build, root = self.build_dir, self.root
        identity = (self.entry("build-root") or {}).get("target_identity")
        if not (os.path.lexists(str(root)) and os.listdir(str(root))):
            return "not_started", "" if os.path.lexists(str(build)) else "nothing built"
        if not os.path.lexists(str(build)) and _same_identity(root, identity):
            if not pf_instance.validate_installation_root(root).blocking():
                return "complete", ""
        return "unknown", "build/root state not written by this operation"

    def _observe_stage_legacy_file(self, effect_id):
        item = self.legacy_file(effect_id.split(":", 1)[1])
        staged, target = Path(item["staged"]), Path(item["target"])
        identity = (self.entry(effect_id) or {}).get("target_identity")
        if identity is not None and _same_identity(target, identity):
            return "complete", "published"
        try:
            info = os.lstat(str(staged))
        except FileNotFoundError:
            return "not_started", "absent"
        data = _read_optional_safe(staged)
        if stat.S_ISREG(info.st_mode) and isinstance(data, bytes) and _sha(data) == item["sha256"] \
                and stat.S_IMODE(info.st_mode) == item["mode"] and info.st_gid == item["gid"]:
            return "complete", ""
        return "partial", "own staged copy differs"

    def _observe_publish_legacy_file(self, effect_id):
        role = effect_id.split(":", 1)[1]
        item = self.legacy_file(role)
        target = Path(item["target"])
        identity = (self.entry("stage-legacy-file:" + role) or {}).get("target_identity")
        if not os.path.lexists(str(target)):
            return "not_started", "absent"
        data = _read_optional_safe(target)
        if not isinstance(data, bytes):
            return "unknown", "unreadable"
        if _sha(data) == item["sha256"]:
            if _same_identity(target, identity):
                return "complete", "the planned copy"
            return "unknown", "a file with the planned bytes but another identity (not this operation's copy)"
        # Section 3.10: the hash of a secret-bearing .env is never printed or journaled outside plan.json.
        return "unknown", "a file with different bytes" if role == "env" else _sha(data)

    def _observe_bind_launcher(self, effect_id):
        launcher = self.plan["launcher"]
        data = _read_optional_safe(Path(launcher["path"]))
        if data is None:
            return "not_started", "absent"
        if isinstance(data, bytes) and data == launcher["after_text"].encode("utf-8"):
            return "complete", _sha(data)
        # Another writer's launcher (or anything else at the path): section 3.7 never takes or replaces an
        # existing launcher, so resume treats it as the live EEXIST case and leaves it, never as unknown.
        return "left", "present and not written by this operation"

    def _observe_register_instance(self, effect_id):
        instance = self.plan["instance"]
        instance_id = instance["instance_id"]
        reservation = self.root / pf_instance.RESERVATIONS_RELATIVE / (instance_id + ".json")
        directory = self.root / "instances" / instance_id
        try:
            registry = pf_instance.load_registry(self.root)
        except pf_instance.ContextError as exc:
            return "unknown", f"registry unreadable ({exc})"
        staging = [name for name in os.listdir(str(self.root / pf_instance.STAGING_RELATIVE))
                   if name.split(".", 1)[0] == instance_id]
        published = registry.entry(instance_id) is not None
        record_bytes = _read_optional_safe(directory / "record.json")
        if isinstance(record_bytes, bytes) and not self._record_matches(record_bytes):
            return "unknown", "record differs from the pinned identity"
        if published and not os.path.lexists(str(reservation)) and isinstance(record_bytes, bytes):
            return "complete", ""
        if not published and not os.path.lexists(str(reservation)) and not os.path.lexists(str(directory)) and not staging:
            return "not_started", ""
        return "partial", "reservation, directory or staging of an unfinished registration"

    def _record_matches(self, data):
        instance = self.plan["instance"]
        try:
            record = pf_instance.parse_strict_json(data, label="record.json")
            pf_instance.validate_instance_record(record)
        except pf_instance.ContextError:
            return False
        if pf_instance.normalize_json(record) != data:
            return False
        return (record["instance_id"], record["slug"], record["compose_project"], record["approved_environment"],
                record["daemon"]["engine_id"], record["state"], record["record_revision"]) == \
            (instance["instance_id"], instance["slug"], instance["compose_project"], instance["approved_environment"],
             instance["daemon"]["engine_id"], "registered", 1) \
            and all(record["paths"][role] == instance["paths"][role] for role in pf_instance.ROLE_NAMES)

    # -- forward ---------------------------------------------------------------------------

    def forward(self):
        for effect_id, etype in _planned_effects(self.plan):
            item = self.entry(effect_id)
            if item is not None and item["state"] == "complete" and (etype in CHECK_TYPES or self._consumed(etype)):
                continue
            if item is not None and item["state"] == "complete" and etype == "bind-launcher" \
                    and item["target_identity"] is None:
                continue  # another writer won the no-clobber creation; nothing of this operation is there
            if etype in CHECK_TYPES:
                self.intend(effect_id, etype)
                _apply_effect(self, effect_id, etype)
                self.complete(effect_id, etype)
                continue
            if item is not None and self._consumed(etype):
                continue
            state, detail = self.observe(effect_id, etype)
            if state == "left":  # bind-launcher only: journaled like the live no-clobber EEXIST (identity null)
                if item is None:
                    self.intend(effect_id, etype)
                self.complete(effect_id, etype)
                self._launcher_left()
                continue
            if state == "complete":
                if item is None or item["state"] != "complete":
                    self.intend(effect_id, etype, observed="complete")
                    self._after_observed_complete(effect_id, etype)
                    self.complete(effect_id, etype, identity=self._created_identity(effect_id, etype),
                                  observed="complete")
                continue
            if state == "unknown":
                raise self._needs_operator(effect_id, etype, detail)
            if state == "partial" or (state == "not_started" and etype in ("stage-release", "build-root")
                                      and self.candidate is None):
                if etype in ("stage-release", "build-root"):
                    raise _StagingLost(effect_id)
                self._reconcile(effect_id, etype)
            self.intend(effect_id, etype, observed=state)
            identity = _apply_effect(self, effect_id, etype)
            self.complete(effect_id, etype, identity=identity, observed=None)
        self.finish()

    def _consumed(self, etype):
        consumer = CONSUMER.get(etype)
        if consumer is None:
            return False
        return any(item["type"] == consumer for item in self.journal["effects"])

    def _created_identity(self, effect_id, etype):
        if etype == "stage-legacy-file":
            item = self.legacy_file(effect_id.split(":", 1)[1])
            path = Path(item["staged"]) if os.path.lexists(item["staged"]) else Path(item["target"])
            return _identity(path)
        if etype == "stage-release":
            return _identity(self.staging_dir())
        if etype == "bind-launcher":
            return _identity(Path(self.plan["launcher"]["path"]))
        return None

    def _after_observed_complete(self, effect_id, etype):
        if etype == "publish-root":
            self.base = self.root
        if etype == "bind-launcher":
            self._remove_own_launcher_temp()
        if etype == "publish-legacy-file":
            staged = Path(self.legacy_file(effect_id.split(":", 1)[1])["staged"])
            if _same_identity(staged, (self.entry("stage-legacy-file:" + effect_id.split(":", 1)[1]) or {}).get(
                    "target_identity")):
                os.unlink(str(staged))
                pf_instance._fsync_directory(staged.parent)

    def _reconcile(self, effect_id, etype):
        """A partial own state that the effect redoes from the start."""
        if etype == "stage-legacy-file":
            staged = Path(self.legacy_file(effect_id.split(":", 1)[1])["staged"])
            if os.path.lexists(str(staged)):
                os.unlink(str(staged))
                pf_instance._fsync_directory(staged.parent)
        # register-instance: the A1 registration converges from its reservation (steps 1a/1b).

    def finish(self):
        result = self.result_document()
        self.save(phase="completed", result=result, next=[], last_error=None)

    def result_document(self):
        conf_path = self.root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME
        conf_bytes = pf_instance.read_bytes_nofollow(conf_path)
        conf = pf_bootstrap.parse_bootstrap_conf(conf_bytes, label=str(conf_path))
        records = {}
        for entry in pf_instance.load_registry(self.root).entries:
            records[entry.instance_id] = _sha(pf_instance.read_bytes_nofollow(entry.record_path))
        launcher = None
        if self._own_launcher():
            data = _read_optional_safe(Path(self.plan["launcher"]["path"]))
            launcher = {"path": self.plan["launcher"]["path"], "sha256": _sha(data)}
        return {"release_id": Path(conf["control_release"]).name, "bootstrap_conf_sha256": _sha(conf_bytes),
                "records": records,
                "instance_id": self.plan["instance"]["instance_id"] if self.plan["instance"] else None,
                "launcher": launcher}

    def _own_launcher(self):
        """True when this operation created the global launcher and it is still exactly that file.

        A creation journaled ``complete`` names its identity (None: another writer won, nothing is ours). One
        journaled only ``intended`` (a crash after the link) is ours when the path holds exactly the rendered bytes:
        the preflight saw the path absent and this operation is the only writer of those bytes for this root.
        """
        created = self.entry("bind-launcher")
        launcher = self.plan["launcher"]
        if created is None or launcher is None:
            return False
        path = Path(launcher["path"])
        if _read_optional_safe(path) != launcher["after_text"].encode("utf-8"):
            return False
        if created["state"] == "intended":
            return True
        return created["target_identity"] is not None and _same_identity(path, created["target_identity"])

    # -- effects ---------------------------------------------------------------------------

    def apply(self, effect_id, etype):
        return getattr(self, "_apply_" + etype.replace("-", "_"))(effect_id)

    def _apply_build_root(self, effect_id):
        candidate = self.candidate
        bindings = {item["type"]: item for item in self.plan["bindings"]}
        conf = pf_bootstrap.parse_bootstrap_conf(bindings["bootstrap-conf"]["after_text"].encode("utf-8"), label="plan")
        tools = pf_bootstrap.parse_tools_conf(bindings["tools-conf"]["after_text"].encode("utf-8"), label="plan")
        profile, policy = _init_documents(candidate.version)
        documents = self.plan["init_documents"]
        if _sha(profile) != documents["profile"]["sha256"] or _sha(policy) != documents["policy"]["sha256"]:
            raise InstallError("install-plan-changed", "the init profile/policy bytes differ from the plan")
        pf_instance.initialize_installation_root(
            self.root, build_dir=self.build_dir, launcher=candidate.bootstrap_files["pf"],
            interpreter=conf["interpreter"], release_id=candidate.release_id, release_files=candidate.release_files,
            profile=(PROFILE_NAME, profile), policy_documents={POLICY_NAME: policy}, tools=tools,
            wrappers={name: candidate.bootstrap_files[name] for name in WRAPPER_NAMES})
        built_conf = pf_instance.read_bytes_nofollow(self.build_dir / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME)
        if _sha(built_conf) != bindings["bootstrap-conf"]["after_sha256"]:
            raise InstallError("install-plan-changed", "the built bootstrap.conf differs from the plan")
        return _identity(self.build_dir)

    def _apply_stage_release(self, effect_id):
        candidate = self.candidate
        staging = self.staging_dir()
        pf_instance._create_private_dir(staging, 0o700)
        for name, data in sorted(candidate.release_files.items()):
            pf_instance._write_private_file(staging / name, data, 0o600)
        pf_instance._write_private_file(staging / pf_bootstrap.CONTROL_INVENTORY_NAME, candidate.inventory_bytes, 0o600)
        pf_instance._fsync_directory(staging)
        state, detail = self._observe_stage_release(effect_id)
        if state != "complete":
            raise InstallError("install-smoke-failed", f"the staged release does not verify after writing ({detail})")
        return _identity(staging)

    def _smoke_paths(self):
        kind = self.plan["kind"]
        if kind == "init":
            return self.build_dir / "releases" / self.plan["candidate"]["release_id"], self.build_dir
        if not self.plan["selected_existing_release"] and os.path.lexists(str(self.staging_dir())):
            return self.staging_dir(), self.root
        return self.release_dir(), self.root

    def _apply_smoke(self, effect_id):
        release, smoke_root = self._smoke_paths()
        cwd = self.op_dir / "smoke-cwd"
        if not os.path.lexists(str(cwd)):
            pf_instance._create_private_dir(cwd, 0o700)
        spec = pf_runner.ProcessSpec(tool="interpreter", argv=("-I", "-B", "-c", SMOKE_PROGRAM, str(release),
                                                               str(smoke_root)),
                                     cwd=str(cwd), env=self.runner.environment(), timeout=SMOKE_TIMEOUT, effect=None,
                                     label="smoke")
        try:
            result = self.runner.run(spec)
        except pf_runner.RunnerError as exc:
            raise _SmokeFailed(str(exc)) from exc
        outcome = {"ok": False, "returncode": result.returncode, "timed_out": result.timed_out, "output": None}
        try:
            output = json.loads(result.stdout) if result.ok else None
        except ValueError:
            output = None
        problem = self._smoke_problem(result, output)
        outcome.update(ok=problem is None, output=output, problem=problem)
        pf_instance._write_private_file(self.op_dir / "smoke.json", _document_bytes(outcome), 0o600)
        if problem is not None:
            raise _SmokeFailed(problem)
        checker = pf_bootstrap.PathChecker(self.root)
        candidate = self.plan["candidate"]
        pf_bootstrap.verify_release(checker, release, expected_inventory_sha256=candidate["inventory_sha256"],
                                    expected_release_id=candidate["release_id"])
        if checker.blocking():
            raise _SmokeFailed("the release changed during the smoke: " + checker.blocking()[0])
        if self.plan["kind"] == "init":
            bootstrap = self.build_dir / pf_instance.BOOTSTRAP_DIR
            for name, digest in candidate["bootstrap_files"].items():
                data = _read_optional_safe(bootstrap / name)
                if not isinstance(data, bytes) or _sha(data) != digest:
                    raise _SmokeFailed(f"bootstrap/{name} differs from the plan after the smoke")
        return None

    def _smoke_problem(self, result, output):
        if not result.ok:
            detail = pf_runner.failure_detail(result, 400).splitlines()
            return f"exit {result.returncode}" + (f": {detail[-1]}" if detail else "")
        if not isinstance(output, dict):
            return "the smoke output is not strict JSON"
        python = output.get("python")
        if not (isinstance(python, list) and len(python) == 3 and all(type(part) is int for part in python)
                and tuple(python[:2]) >= (3, 9)):
            return "the interpreter is older than Python 3.9"
        if (output.get("install_contract"), output.get("install_schema_version")) != (INSTALL_CONTRACT,
                                                                                       INSTALL_SCHEMA_VERSION):
            return "the candidate's install contract differs from the running one"
        if output.get("registry_ok") is not True:
            return "the candidate cannot load the registry"
        records = output.get("records") or {}
        bad = sorted(key for key, value in records.items() if value != "ok")
        if bad:
            return "the candidate cannot load the record of " + ", ".join(bad)
        configs = output.get("configs") or {}
        slugs = self._slugs()
        for item in self.plan["config_baseline"]:
            found = configs.get(item["instance_id"]) or {}
            for field in ("config", "env"):
                if item[field] == "ok" and found.get(field) != "ok":
                    return f"config of {slugs.get(item['instance_id'], item['instance_id'])} rejected by the candidate ({field})"
        return None

    def _slugs(self):
        try:
            return {entry.instance_id: entry.slug for entry in pf_instance.load_registry(self.root).entries}
        except pf_instance.ContextError:
            return {}

    def _apply_publish_root(self, effect_id):
        root = self.root
        os.rename(str(self.build_dir), str(root))
        pf_instance._fsync_directory(root.parent)
        self.base = root
        return None

    def _apply_publish_release(self, effect_id):
        target = self.release_dir()
        os.rename(str(self.staging_dir()), str(target))
        pf_instance._fsync_directory(target.parent)
        return None

    def _apply_pre_bind_verify(self, effect_id):
        checker = pf_bootstrap.PathChecker(self.root)
        candidate = self.plan["candidate"]
        pf_bootstrap.verify_release(checker, self.release_dir(), expected_inventory_sha256=candidate["inventory_sha256"],
                                    expected_release_id=candidate["release_id"])
        if checker.blocking():
            raise _SmokeFailed("pre-bind verification failed: " + checker.blocking()[0])
        return None

    def _write_binding(self, effect_id):
        binding = self.binding(effect_id)
        mode = 0o700 if binding["type"] == "wrapper" else 0o600
        pf_instance._write_private_file(Path(binding["target"]), binding["after_text"].encode("utf-8"), mode)
        return None

    _apply_bind_bootstrap_conf = _write_binding
    _apply_rebind_record = _write_binding
    _apply_replace_wrapper = _write_binding

    def _apply_verify(self, effect_id):
        problems = self.verify_problems()
        if problems:
            raise _VerifyFailed(problems[0])
        return None

    def verify_problems(self):
        root = self.root
        checker = pf_instance.validate_installation_root(root)
        if checker.blocking():
            return ["installation root: " + checker.blocking()[0]]
        try:
            registry = pf_instance.load_registry(root)
        except pf_instance.ContextError as exc:
            return [f"registry: {exc}"]
        allowed = {(item["instance_id"], item["code"], item["path"]) for item in self.plan["verify_baseline"]}
        slugs = []
        for entry, context, error in registry.records():
            if context is None:
                return [f"record {entry.slug}: {error}"]
            slugs.append(entry.slug)
            validation = pf_instance.validate_context(context, running_release=None)
            for finding in validation.findings:
                if finding.severity == "refuse" and (context.instance_id, finding.code, finding.path) not in allowed:
                    return [f"instance {context.slug}: {finding.render()}"]
        cwd = self.op_dir / "smoke-cwd"
        if not os.path.lexists(str(cwd)):
            pf_instance._create_private_dir(cwd, 0o700)
        spec = pf_runner.ProcessSpec(
            tool="interpreter", argv=("-I", "-B", str(root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_MODULE_NAME),
                                      pf_instance.ROOT_HANDSHAKE_OPTION, str(root), "instances"),
            cwd=str(cwd), env=self.runner.environment(), timeout=SMOKE_TIMEOUT, effect=None, label="verify")
        try:
            result = self.runner.run(spec)
        except pf_runner.RunnerError as exc:
            return [f"end-to-end run: {exc}"]
        if not result.ok:
            detail = pf_runner.failure_detail(result, 400).splitlines()
            return [f"end-to-end 'instances' through the bootstrap: exit {result.returncode}"
                    + (f" ({detail[-1]})" if detail else "")]
        missing = [slug for slug in slugs if f". {slug}  " not in result.stdout]
        if missing:
            return ["end-to-end 'instances' does not list " + ", ".join(missing)]
        return []

    def _apply_bind_launcher(self, effect_id):
        launcher = self.plan["launcher"]
        path = Path(launcher["path"])
        temp = path.parent / f".pf.tmp-{_op8(self.operation_id)}"
        if os.path.lexists(str(temp)):
            os.unlink(str(temp))
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o755)
        try:
            os.write(fd, launcher["after_text"].encode("utf-8"))
            os.fchmod(fd, 0o755)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(str(temp), str(path))
            identity = _identity(temp)
        except FileExistsError:
            identity = None
        finally:
            os.unlink(str(temp))
            pf_instance._fsync_directory(path.parent)
        if identity is None:
            self._launcher_left()
        return identity

    def _launcher_left(self):
        self._remove_own_launcher_temp()
        self.say(f"Note: launcher-left: {self.plan['launcher']['path']} appeared while installing; another writer "
                 f"won and nothing was replaced. Use "
                 f"{self.root / pf_instance.BOOTSTRAP_DIR / pf_instance.LAUNCHER_NAME}.")

    def _remove_own_launcher_temp(self):
        """Remove ``.pf.tmp-<op8>`` left by a stop inside bind-launcher: it is this operation's when it is the
        launcher's own inode (a stop between the link and the unlink) or holds exactly the rendered bytes."""
        launcher = self.plan["launcher"]
        if launcher is None or launcher["after_text"] is None:
            return
        path = Path(launcher["path"])
        temp = path.parent / f".pf.tmp-{_op8(self.operation_id)}"
        try:
            info = os.lstat(str(temp))
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            return
        same_inode = _same_identity(path, {"st_dev": info.st_dev, "st_ino": info.st_ino})
        if same_inode or _read_optional_safe(temp) == launcher["after_text"].encode("utf-8"):
            os.unlink(str(temp))
            pf_instance._fsync_directory(path.parent)

    def _apply_register_instance(self, effect_id):
        instance = self.plan["instance"]
        conf = pf_bootstrap.parse_bootstrap_conf(pf_instance.read_bytes_nofollow(
            self.root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME), label="bootstrap.conf")
        spec = {"slug": instance["slug"], "compose_project": instance["compose_project"],
                "approved_environment": instance["approved_environment"],
                "daemon": {"endpoint": instance["daemon"]["endpoint"], "engine_id": instance["daemon"]["engine_id"]},
                "paths": dict(instance["paths"]), "control_release_id": Path(conf["control_release"]).name,
                "profile_path": self.root / "profiles" / PROFILE_NAME, "policy_path": self.root / "policies" / POLICY_NAME,
                "instance_id": instance["instance_id"]}
        try:
            pf_instance.register_instance(self.root, spec, registry_lock=self.registry_lock)
        except pf_instance.ContextError as exc:
            raise InstallError("install-verify-failed", f"Operation {self.operation_id} could not register "
                                                        f"{instance['slug']} ({exc}); nothing further was changed. Run "
                                                        f"'{launcher_prefix(self.root)} install status' and follow its "
                                                        "next step.") from exc
        return None

    def _apply_stage_legacy_file(self, effect_id):
        item = self.legacy_file(effect_id.split(":", 1)[1])
        try:
            data = _read_regular(item["source"], single_link=True)
        except OSError as exc:  # a FIFO, link or other file swapped in after the preflight (never blocks)
            raise InstallError("install-plan-changed", f"{item['source']} changed after the summary was shown "
                                                       f"({exc.strerror or exc}); nothing was changed. Run the "
                                                       "command again to review a new plan.") from exc
        if _sha(data) != item["sha256"]:
            raise InstallError("install-plan-changed", f"{item['source']} changed after the summary was shown "
                                                       "(content hash); nothing was changed. Run the command again "
                                                       "to review a new plan.")
        staged = Path(item["staged"])
        fd = os.open(str(staged), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fchown(fd, -1, item["gid"])
            os.fchmod(fd, item["mode"])
            os.fsync(fd)
        finally:
            os.close(fd)
        pf_instance._fsync_directory(staged.parent)
        state, detail = self._observe_stage_legacy_file(effect_id)
        if state != "complete":
            raise InstallError("install-smoke-failed", f"the staged copy {staged} does not match the plan ({detail})")
        return _identity(staged)

    def _apply_validate(self, effect_id):
        legacy = self.plan["legacy"]
        instance = self.plan["instance"]
        admin = next((item for item in legacy["files"] if item["role"] == "admin-config"), None)
        path = Path(admin["staged"]) if admin and os.path.lexists(admin["staged"]) else \
            Path(admin["target"]) if admin else Path(instance["paths"]["configuration"]) / "pf-config.json"
        report = _Report()
        config = _admin_config_check(report, path, _read_optional(path), project=instance["compose_project"])
        if config is None or report.conflicts:
            first = report.conflicts[0]
            raise _SmokeFailed(f"{first.code}: {first.subject}: {first.detail}")
        env = next((item for item in legacy["files"] if item["role"] == "env"), None)
        if env is not None:
            staged = Path(env["staged"]) if os.path.lexists(env["staged"]) else Path(env["target"])
            data = _read_optional(staged)
            if data is None or _sha(data) != env["sha256"]:
                raise _SmokeFailed(f"{staged} does not match the planned .env hash")
        return None

    def _apply_publish_legacy_file(self, effect_id):
        role = effect_id.split(":", 1)[1]
        item = self.legacy_file(role)
        staged, target = Path(item["staged"]), Path(item["target"])
        try:
            os.link(str(staged), str(target))
        except FileExistsError:
            state, detail = self._observe_publish_legacy_file(effect_id)
            if state != "complete":
                raise self._needs_operator(effect_id, "publish-legacy-file", detail)
        os.unlink(str(staged))
        pf_instance._fsync_directory(target.parent)
        return None

    # -- failure routes -------------------------------------------------------------------

    def _needs_operator(self, effect_id, etype, observed):
        before, after = self._expectations(effect_id, etype)
        return _NeedsOperator(effect_id, self._target_of(effect_id, etype), before, after, observed)

    def _target_of(self, effect_id, etype):
        if etype in ("bind-bootstrap-conf", "rebind-record", "replace-wrapper"):
            return self.binding(effect_id)["target"]
        if etype in ("stage-legacy-file", "publish-legacy-file"):
            item = self.legacy_file(effect_id.split(":", 1)[1])
            return item["staged"] if etype == "stage-legacy-file" else item["target"]
        if etype == "bind-launcher":
            return self.plan["launcher"]["path"]
        if etype == "register-instance":
            return str(self.root / "instances" / self.plan["instance"]["instance_id"] / "record.json")
        if etype == "publish-release":
            return str(self.release_dir())
        if etype == "publish-root":
            return str(self.root)
        return effect_id

    def _expectations(self, effect_id, etype):
        if etype in ("bind-bootstrap-conf", "rebind-record", "replace-wrapper"):
            binding = self.binding(effect_id)
            return binding["before_sha256"] or "absent", binding["after_sha256"]
        if etype in ("stage-legacy-file", "publish-legacy-file"):
            role = effect_id.split(":", 1)[1]
            # Section 3.10: the .env hash stays in the private plan only.
            return "absent", "the planned .env copy" if role == "env" else self.legacy_file(role)["sha256"]
        if etype == "bind-launcher":
            return "absent", _text_sha(self.plan["launcher"]["after_text"])
        return "absent", "the pinned identity"

    def enter_needs_operator(self, exc):
        abandon_hint = self._abandon_reason() is None
        evidence = (f"{exc.effect_id}: {exc.target}: expected {exc.before} or {exc.after}, found {exc.observed}")
        try:
            self.save(phase="needs_operator", last_error=evidence, next=self.next_steps())
        except OSError:
            pass
        prefix = launcher_prefix(self.root)
        hint = f" or '{prefix} install resume --abandon'" if abandon_hint else ""
        return InstallError(
            "install-needs-operator",
            f"Operation {self.operation_id} found {exc.target} in a state it did not write (expected {exc.before} or "
            f"{exc.after}, found {exc.observed}); nothing else was changed. Restore {exc.target}, then run '{prefix} "
            f"install resume'{hint}. The journal {self.op_dir / 'journal.json'} records the evidence.")

    def cancel(self, *, reason_code, message):
        """Before the commit point: journal ``cancelled`` first, then reverse and remove own staging."""
        self.save(phase="cancelled", next=[], last_error=message[:500] if reason_code != "install-cancelled" else None)
        kind = self.plan["kind"]
        if kind == "init":
            self._remove_build()
            return
        if kind == "control":
            staging = self.staging_dir()
            if os.path.lexists(str(staging)):
                pf_instance._remove_tree(staging)
            publish = self.entry("publish-release")
            if publish is not None and _same_identity(self.release_dir(),
                                                      (self.entry("stage-release") or {}).get("target_identity")):
                # One rename takes it out of releases/ first, so an interrupted removal never leaves a partial
                # release behind (a re-run would see release-id-collision); the remainder stays in the op dir.
                aside = self.op_dir / "release.cancelled"
                os.rename(str(self.release_dir()), str(aside))
                pf_instance._fsync_directory(self.release_dir().parent)
                pf_instance._remove_tree(aside)
            return
        if kind == "migrate-legacy":
            self._remove_legacy_copies()

    def _remove_build(self):
        build = self.build_dir
        if not os.path.lexists(str(build)):
            return
        for name in sorted(os.listdir(str(build))):
            if name != str(OPERATIONS_RELATIVE):
                path = build / name
                if os.path.isdir(str(path)) and not os.path.islink(str(path)):
                    pf_instance._remove_tree(path)
                else:
                    os.unlink(str(path))
        operations_dir = build / OPERATIONS_RELATIVE
        if os.path.lexists(str(operations_dir)):
            pf_instance._remove_tree(operations_dir)
        os.rmdir(str(build))
        pf_instance._fsync_directory(build.parent)

    def _remove_legacy_copies(self):
        for item in self.plan["legacy"]["files"]:
            identity = (self.entry("stage-legacy-file:" + item["role"]) or {}).get("target_identity")
            target, staged = Path(item["target"]), Path(item["staged"])
            data = _read_optional_safe(target)
            if isinstance(data, bytes) and _sha(data) == item["sha256"] and _same_identity(target, identity):
                os.unlink(str(target))
                pf_instance._fsync_directory(target.parent)
            if os.path.lexists(str(staged)):
                os.unlink(str(staged))
                pf_instance._fsync_directory(staged.parent)

    def restore_bindings(self):
        """Reverse order (wrappers, records, conf) from the plan's before_text; each restore journaled."""
        for binding in reversed(self.plan["bindings"]):
            effect_id = binding["effect_id"]
            item = self.entry(effect_id)
            if item is not None and item["state"] == "reversed":
                continue
            etype = effect_id.split(":", 1)[0]
            state, detail = self._observe_file(effect_id)
            if state == "unknown":
                raise self._needs_operator(effect_id, etype, detail)
            if state == "complete":
                target = Path(binding["target"])
                if binding["before_text"] is None:
                    os.unlink(str(target))
                    pf_instance._fsync_directory(target.parent)
                else:
                    mode = 0o700 if binding["type"] == "wrapper" else 0o600
                    pf_instance._write_private_file(target, binding["before_text"].encode("utf-8"), mode)
            if item is not None:
                self._set_entry(effect_id, etype, state="reversed", observed=state)
                self.save()

    def rollback(self):
        if self.entry("rollback") is None:
            self._set_entry("rollback", "rollback", state="intended")
        self.save(phase="rolling_back", next=["install resume"])
        self.restore_bindings()
        self._set_entry("rollback", "rollback", state="complete")
        self.save(phase="rolled_back", next=[])

    def _abandon_reason(self):
        kind = self.plan["kind"]
        if kind == "init":
            return "it already published the installation root"
        if kind in ("register", "migrate-legacy"):
            return self._discard_precondition()
        return None

    def _discard_precondition(self):
        instance = self.plan["instance"]
        instance_id = instance["instance_id"]
        try:
            registry = pf_instance.load_registry(self.root)
        except pf_instance.ContextError as exc:
            return f"the registry cannot be read ({exc})"
        if registry.default_instance_id == instance_id:
            return "the registry default is this instance"
        directory = self.root / "instances" / instance_id
        if os.path.lexists(str(directory)):
            data = _read_optional_safe(directory / "record.json")
            if isinstance(data, bytes) and not self._record_matches(data):
                return "the record bytes changed after registration"
            for name in (pf_instance.OPERATIONS_SUBDIR, pf_instance.STATE_SUBDIR, pf_instance.ARTIFACTS_SUBDIR):
                try:
                    if os.listdir(str(directory / name)):
                        return f"{name}/ of the instance is not empty"
                except FileNotFoundError:
                    pass
            if _probe_lock(self.root / "locks" / (instance_id + ".lock")):
                return "the instance lock is held"
        return None

    def abandon(self):
        kind = self.plan["kind"]
        reason = self._abandon_reason()
        if reason is not None and self.journal["phase"] != "abandoning":
            raise InstallError("abandon-not-possible", f"Operation {self.operation_id} cannot be abandoned: {reason}. "
                                                       f"Run '{launcher_prefix(self.root)} install resume' to finish it.")
        if self.entry("abandon") is None:
            self._set_entry("abandon", "abandon", state="intended")
        self.save(phase="abandoning", next=["install resume"])
        if kind == "control":
            self.restore_bindings()
            staging = self.staging_dir()
            if os.path.lexists(str(staging)):
                pf_instance._remove_tree(staging)
        else:
            if self.plan["launcher"] is not None:
                self._remove_own_launcher_temp()  # before the launcher: its identity names the temp as ours
            if self._own_launcher():
                path = Path(self.plan["launcher"]["path"])
                self._set_entry("remove-launcher", "remove-launcher", state="intended")
                self.save()
                os.unlink(str(path))
                pf_instance._fsync_directory(path.parent)
                self._set_entry("remove-launcher", "remove-launcher", state="complete")
                self.save()
            self._discard_registration()
            if kind == "migrate-legacy":
                self._remove_legacy_copies()
        self._set_entry("abandon", "abandon", state="complete")
        self.save(phase="abandoned", next=[])

    def _discard_registration(self):
        instance_id = self.plan["instance"]["instance_id"]
        directory = self.root / "instances" / instance_id
        retain = self.op_dir / "discarded-instance"
        state, _ = self._observe_register_instance("register-instance")
        if state == "not_started" and not os.path.lexists(str(retain)):
            return
        current = directory if os.path.lexists(str(directory)) else retain
        expected = _read_optional_safe(current / "record.json")
        if not isinstance(expected, bytes) or not self._record_matches(expected):
            expected = None
        self._set_entry("discard-registration", "discard-registration", state="intended")
        self.save()
        try:
            pf_instance.discard_unused_registration(self.root, instance_id, expected, retain,
                                                    registry_lock=self.registry_lock)
        except pf_instance.DiscardRefused as exc:
            raise _NeedsOperator("discard-registration", str(directory), "an unused registration", "discarded",
                                 str(exc)) from exc
        self._set_entry("discard-registration", "discard-registration", state="complete")
        self.save()


class _StagingLost(Exception):
    """The staged control/build bytes are not in memory after a crash; never rebuilt from the source."""


def _apply_effect(run, effect_id, etype):
    """Execute one effect (tests' forked-crash seam). Returns the created object's identity or None."""
    return run.apply(effect_id, etype)


def _cancel_copy(run, phase):
    kind = run.plan["kind"]
    op = run.operation_id
    if kind == "init":
        return f"Cancelled during {phase}; the private build directory {run.build_dir} of operation {op} was removed " \
               "and nothing else was changed."
    if kind == "control":
        return f"Cancelled during {phase}; the private staging of operation {op} was removed and the installed control " \
               "is unchanged."
    if kind == "register":
        return f"Cancelled before registration; operation {op} wrote no registry state."
    files = ", ".join(item["target"] for item in run.plan["legacy"]["files"]) or "files (none were planned)"
    return f"Cancelled during {phase}; the copied {files} were removed, the legacy files are unchanged and nothing was " \
           "registered."


def _drive(run, action):
    """Run ``action`` (forward/rollback/abandon) with the per-kind failure handling of section 3.4."""
    try:
        action()
    except _NeedsOperator as exc:
        if run.plan["kind"] == "init" and not run.committed():
            # Before publication the journal lives in the private build: needs_operator there would name a
            # resume that cannot reach it. Another writer owns the root; nothing of this init was published.
            _cancel(run, reason_code="install-failed", message=f"{exc.target}: {exc.observed}")
            raise InstallError("install-failed",
                               f"Operation {run.operation_id} found {exc.target} created by another writer before it "
                               f"published ({exc.observed}); it was cancelled and its private build directory "
                               f"{run.build_dir} was removed. Nothing was published. Choose an absent root, then run "
                               "'sudo sh ./deploy/synology/install-control.sh init' again.") from None
        raise run.enter_needs_operator(exc) from None
    except _VerifyFailed as exc:
        detail = str(exc)
        if run.plan["kind"] == "control":
            try:
                run.rollback()
            except _NeedsOperator as inner:
                raise run.enter_needs_operator(inner) from None
            except (KeyboardInterrupt, OSError, pf_instance.ContextError) as inner:
                try:
                    run.save(last_error=f"rollback interrupted ({inner or type(inner).__name__})"[:500],
                             next=["install resume"])
                except BaseException:  # noqa: B902 - best effort only; the journal already says rolling_back
                    pass
                raise InstallError(
                    "install-interrupted",
                    f"Control release {run.plan['candidate']['release_id']} did not verify after binding ({detail}), "
                    f"and restoring the previous binding was interrupted ({inner or type(inner).__name__}). Operation "
                    f"{run.operation_id} stays open in phase {run.journal['phase']}; the bindings may be mixed and every "
                    f"instance route is refused until it ends. Run '{launcher_prefix(run.root)} install resume' to "
                    "finish the restore. The application was not touched.") from None
            old = run.plan["running_release"] or "none"
            raise InstallError("install-verify-failed",
                               f"Control release {run.plan['candidate']['release_id']} did not verify after binding "
                               f"({detail}); the previous binding {old} was restored (operation {run.operation_id} "
                               "rolled_back). The application was not touched.") from None
        run.save(phase="needs_operator", last_error=detail[:500], next=run.next_steps())
        raise InstallError("install-verify-failed",
                           f"Operation {run.operation_id} could not verify its result ({detail}); nothing further was "
                           f"changed. Run '{launcher_prefix(run.root)} install status' and follow its next step.") from None
    except (_SmokeFailed, _StagingLost, KeyboardInterrupt, InstallError, OSError, pf_instance.ContextError) as exc:
        phase = run.journal["phase"]
        if run.committed() or run.direction() != "forward":
            try:
                run.save(last_error=(str(exc) or type(exc).__name__)[:500], next=run.next_steps())
            except BaseException:  # noqa: B902 - best effort only; the journal already says where it stopped
                pass
            if isinstance(exc, InstallError):
                raise
            raise InstallError("install-interrupted",
                               f"Operation {run.operation_id} ({run.plan['kind']}) stopped in phase {phase} after its "
                               f"commit point ({exc or type(exc).__name__}); it stays open and nothing else was changed. "
                               f"Run '{launcher_prefix(run.root)} install resume' (or 'install resume --abandon' where "
                               "legal).") from None
        if isinstance(exc, _StagingLost):
            _cancel(run, reason_code="install-cancelled", message="staging lost")
            raise InstallError("install-cancelled",
                               f"Operation {run.operation_id} stopped before its staged bytes were complete; they are "
                               "never rebuilt from the source. Its staging was removed and nothing else was changed "
                               "(cancelled). Run the original command again.") from None
        if isinstance(exc, KeyboardInterrupt):
            _cancel(run, reason_code="install-cancelled", message="interrupted")
            raise Cancelled(phase, _cancel_copy(run, phase)) from None
        if isinstance(exc, _SmokeFailed):
            _cancel(run, reason_code="install-smoke-failed", message=str(exc))
            raise InstallError("install-smoke-failed",
                               f"The control release {run.plan['candidate']['release_id'] if run.plan['candidate'] else ''} "
                               f"failed its smoke check ({exc}); it was not activated, its staging was removed and the "
                               "installed control is unchanged.") from None
        _cancel(run, reason_code=getattr(exc, "code", "install-failed"), message=str(exc))
        if isinstance(exc, InstallError):
            raise
        if run.plan["kind"] == "init":
            raise InstallError("install-failed", f"Operation {run.operation_id} failed during {phase} ({exc}); it was "
                                                 f"cancelled and its private build directory {run.build_dir} was "
                                                 "removed. Nothing was published. Fix the cause, then run 'sudo sh "
                                                 "./deploy/synology/install-control.sh init' again.") from None
        raise InstallError("install-failed", f"Operation {run.operation_id} failed during {phase} ({exc}); it was "
                                             "cancelled and its own staging removed. Nothing else was changed.") from None


def _cancel(run, *, reason_code, message):
    """``run.cancel`` whose own interruption is reported as it is: the journal already says ``cancelled`` (it is
    written first), but some of this operation's own staging or copies may remain."""
    try:
        run.cancel(reason_code=reason_code, message=message)
    except (KeyboardInterrupt, OSError) as exc:
        if run.journal["phase"] != "cancelled":
            raise
        raise InstallError("install-interrupted",
                           f"Operation {run.operation_id} is cancelled, but removing its own staging or copies was "
                           f"interrupted ({exc or type(exc).__name__}); nothing else was changed. What remains stays "
                           "inside its operation directory or is recognized by the next run of the same command; run "
                           "it again when needed.") from None


def _comparable(plan):
    value = {key: item for key, item in plan.items() if key not in ("operation_id", "created", "notes")}
    if value.get("legacy") is not None:
        # The lock this operation itself created under its locks is not a change of the plan's subject.
        value["legacy"] = dict(value["legacy"], lock_created=None)
    return value


def _plan_difference(first, second):
    first, second = _comparable(first), _comparable(second)
    for key in sorted(set(first) | set(second)):
        if first.get(key) != second.get(key):
            return key
    return None


def _remove_intent_leftovers(run):
    """An effect-free ``.<id>.new`` intent of an earlier interrupted operation is removed, with a note."""
    directory = run.root / OPERATIONS_RELATIVE
    for name in sorted(os.listdir(str(directory))):
        if name.startswith(".inst-") and name.endswith(".new") and pf_instance.INSTALL_OPERATION_NAME_RE.fullmatch(name):
            pf_instance._remove_tree(directory / name)
            pf_instance._fsync_directory(directory)
            run.say(f"Note: install-intent-removed: {directory / name} was an effect-free intent of an interrupted "
                    "operation; it was removed.")


def execute(root, plan, *, runner, interaction, candidate=None, leftovers=(), running_release=None, request=None):
    """Section 3.2 steps 4-8 for a confirmed plan; returns the final journal. Holds its locks inside.

    ``request`` (the parsed and prompted options) makes the preflight run again under the locks; every
    planned byte and hash must be reproduced (``install-plan-changed`` otherwise, nothing written).
    """
    kind = plan["kind"]
    root = Path(plan["root"] if kind == "init" else root)
    journal = {"schema_version": 1, "operation_id": plan["operation_id"], "kind": kind,
               "plan_sha256": _sha(_document_bytes(plan)), "phase": "planned", "sequence": 1,
               "started": _utc_text(), "updated": _utc_text(), "effects": [], "result": None, "last_error": None,
               "next": []}
    run = _Run(root, plan, journal["plan_sha256"], journal, base=root, runner=runner, interaction=interaction,
               candidate=candidate)
    try:
        try:
            if kind == "init":
                for path in leftovers:
                    _remove_own_leftover(path, root)
                run.lock_build()
                run.base = run.build_dir
            else:
                run.lock()
            if request is not None:
                pinned = (plan["operation_id"], plan["created"], (plan["instance"] or {}).get("instance_id"))
                again = _preflight(root, kind, request, runner=runner, running_release=running_release,
                                   under_lock=True, pinned=pinned, own_build=run.build_dir if kind == "init" else None)
                difference = _plan_difference(plan, again.plan)
                if again.conflicts or difference is not None:
                    subject = again.conflicts[0].subject if again.conflicts else difference
                    detail = f"{again.conflicts[0].code}: {again.conflicts[0].detail}" if again.conflicts else \
                        "the recomputed plan differs"
                    raise InstallError("install-plan-changed", f"{subject} changed after the summary was shown "
                                                               f"({detail}); nothing was changed. Run the command again "
                                                               "to review a new plan.")
            if kind != "init":
                _remove_intent_leftovers(run)
            run.write_intent()
        except BaseException as exc:
            if kind == "init" and run.build_fd is not None:
                run.release()
                run._remove_build()
            elif kind != "init" and os.path.lexists(str(run.op_dir)):
                # The intent rename happened (the stop came after it, e.g. in the directory fsync): an open
                # operation in phase planned with no effect. It is cancelled, never reported as nothing written.
                _cancel(run, reason_code="install-cancelled" if isinstance(exc, KeyboardInterrupt) else
                        getattr(exc, "code", "install-failed"), message=str(exc) or type(exc).__name__)
                if isinstance(exc, KeyboardInterrupt):
                    raise Cancelled("planned", _cancel_copy(run, "planned")) from None
                raise
            if isinstance(exc, KeyboardInterrupt):
                raise Cancelled("locks", "Cancelled before the intent was written; nothing was changed.") from None
            raise
        _drive(run, run.forward)
        return run.journal
    finally:
        run.release()


def _remove_own_leftover(path, root):
    """Probe-and-hold an own unpublished build, re-check it, remove it (operation directory last)."""
    verdict = _leftover_verdict(path, root, path.name.rsplit("-", 1)[-1])
    if verdict != "own":
        raise InstallError("install-plan-changed", f"{path} changed after the summary was shown ({verdict}); nothing was "
                                                   "changed. Run the command again to review a new plan.")
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InstallError("install-busy", f"Another operation holds {path}; nothing was changed. Try again after it "
                                               "finishes.") from exc
        for name in sorted(os.listdir(str(path))):
            if name != str(OPERATIONS_RELATIVE):
                entry = path / name
                if os.path.isdir(str(entry)) and not os.path.islink(str(entry)):
                    pf_instance._remove_tree(entry)
                else:
                    os.unlink(str(entry))
        if os.path.lexists(str(path / OPERATIONS_RELATIVE)):
            pf_instance._remove_tree(path / OPERATIONS_RELATIVE)
        os.rmdir(str(path))
        pf_instance._fsync_directory(path.parent)
    finally:
        os.close(fd)


def _select_operation(root, operation_id):
    if operation_id is not None:
        return operation_id
    items = operations(root)
    open_items = [item for item in items if item["phase"] in OPEN_PHASES]
    unreadable = [item for item in items if item["phase"] == "unreadable"]
    if not open_items and unreadable:
        # It gates every route (fail closed), so "nothing to do" would be a hidden dead end.
        item = unreadable[0]
        raise InstallError("install-operation-unreadable",
                           f"{_operations_dir(root) / item['operation_id']} is not a readable install operation "
                           f"({item['error']}); it refuses every route and resume cannot continue it. Next: "
                           f"{unreadable_step(root, item)}.")
    if not open_items:
        raise InstallError("install-nothing-pending", "No open install operation; nothing to do.", exit_code=0)
    if len(open_items) > 1:
        raise InstallError("install-operation-pending", "Several install operations are open: "
                           + ", ".join(item["operation_id"] for item in open_items) + "; pass --operation <id>.")
    return open_items[0]["operation_id"]


def resume(root, *, operation_id=None, abandon=False, runner, interaction, confirm=True):
    """``pf install resume [--operation ID] [--abandon]``: observe every effect before deciding anything."""
    root = Path(root)
    operation_id = _select_operation(root, operation_id)
    plan, plan_sha, journal = load_operation(root, operation_id)
    if journal["phase"] in TERMINAL_PHASES:
        raise InstallError("install-nothing-pending", "No open install operation; nothing to do.", exit_code=0)
    run = _Run(root, plan, plan_sha, journal, base=root, runner=runner, interaction=interaction)
    direction = run.direction()
    if abandon and direction == "forward":
        reason = run._abandon_reason()
        if reason is not None:
            raise InstallError("abandon-not-possible", f"Operation {operation_id} cannot be abandoned: {reason}. Run "
                                                       f"'{launcher_prefix(root)} install resume' to finish it.")
    if confirm:
        interaction.say(f"Install operation {operation_id} ({plan['kind']}) is open in phase {journal['phase']}.")
        if journal["last_error"]:
            interaction.say("Last error: " + journal["last_error"])
        verb = "ABANDON" if abandon and direction == "forward" else "RESUME"
        _confirm(interaction, f"{verb} {_op8(operation_id)}")
    try:
        run.lock()
        # The journal may have advanced while this process waited for the confirmation.
        plan, plan_sha, journal = load_operation(root, operation_id)
        run.journal = journal
        if journal["phase"] in TERMINAL_PHASES:
            raise InstallError("install-nothing-pending", "No open install operation; nothing to do.", exit_code=0)
        direction = run.direction()
        for leftover in _own_temp_files(run):
            interaction.say(f"Note: leftover temporary file {leftover} is ignored; it is not part of any observation and "
                            "is removed only by the write it belongs to.")
        unknown = _first_unknown(run, "abandon" if abandon and direction == "forward" else direction)
        if unknown is not None:
            raise run.enter_needs_operator(unknown)
        if direction == "rollback":
            _drive(run, run.rollback)
        elif direction == "abandon" or abandon:
            _drive(run, run.abandon)
        else:
            if journal["phase"] == "needs_operator":
                run.save(phase=_phase_from_effects(run), last_error=None, next=["install resume"])
            _drive(run, run.forward)
        return run.journal
    finally:
        run.release()


def _own_temp_files(run):
    """``<target>.tmp-*`` siblings of this operation's write targets and its launcher temp (read-only)."""
    targets = [Path(binding["target"]) for binding in run.plan["bindings"]]
    if run.plan["legacy"] is not None:
        targets += [Path(item["target"]) for item in run.plan["legacy"]["files"]]
    found = []
    for target in targets:
        try:
            names = os.listdir(str(target.parent))
        except OSError:
            continue
        found += [target.parent / name for name in sorted(names) if name.startswith(target.name + ".tmp-")]
    launcher = run.plan["launcher"]
    if launcher is not None:
        temp = Path(launcher["path"]).parent / f".pf.tmp-{_op8(run.operation_id)}"
        if os.path.lexists(str(temp)):
            found.append(temp)
    return found


def _phase_from_effects(run):
    phase = "planned"
    for item in run.journal["effects"]:
        for candidate in (DURING.get(item["type"]), AFTER.get(item["type"]) if item["state"] == "complete" else None):
            if candidate and PHASE_ORDER.index(candidate) > PHASE_ORDER.index(phase):
                phase = candidate
    return phase


def _first_unknown(run, direction):
    """Pre-pass: any effect whose observation is ``unknown`` stops resume before anything changes."""
    if direction != "forward":
        if run.plan["kind"] != "control":
            return None  # discard-registration checks its own preconditions under the locks
        for binding in run.plan["bindings"]:
            item = run.entry(binding["effect_id"])
            if item is not None and item["state"] == "reversed":
                continue
            state, detail = run._observe_file(binding["effect_id"])
            if state == "unknown":
                return run._needs_operator(binding["effect_id"], binding["effect_id"].split(":", 1)[0], detail)
        return None
    for effect_id, etype in _planned_effects(run.plan):
        if etype in CHECK_TYPES or (run.entry(effect_id) is not None and run._consumed(etype)):
            continue
        if etype in ("build-root", "stage-release"):
            continue
        item = run.entry(effect_id)
        if etype == "bind-launcher" and item is not None and item["state"] == "complete" \
                and item["target_identity"] is None:
            continue
        state, detail = run.observe(effect_id, etype)
        if state == "unknown":
            return run._needs_operator(effect_id, etype, detail)
    return None


# ------------------------------------------------------------------------------------------- CLI


def add_parser(subparsers):
    """The ``install`` subcommand tree of pf-admin.py (abbreviations refused everywhere)."""
    install = subparsers.add_parser("install", allow_abbrev=False,
                                    help="Installer: status, register, migrate-legacy, control, resume (PF-A2.1)")
    verbs = install.add_subparsers(dest="install_verb", required=True)
    verbs.add_parser("status", allow_abbrev=False, help="List install operations (read-only)")
    register = verbs.add_parser("register", allow_abbrev=False, help="Register an instance with existing directories")
    for option in ("--slug", "--project", "--workspace", "--configuration", "--backups", "--recovery"):
        register.add_argument(option)
    register.add_argument("--docker-endpoint", default=None)
    migrate = verbs.add_parser("migrate-legacy", allow_abbrev=False, help="Copy legacy v2.5 config and register it")
    migrate.add_argument("--legacy-home")
    migrate.add_argument("--workspace")
    migrate.add_argument("--slug")
    launcher = migrate.add_mutually_exclusive_group()
    launcher.add_argument("--launcher-path")
    launcher.add_argument("--no-launcher", action="store_true")
    migrate.add_argument("--docker-endpoint", default=None)
    control = verbs.add_parser("control", allow_abbrev=False, help="Install or select a control release")
    selection = control.add_mutually_exclusive_group()
    selection.add_argument("--source")
    selection.add_argument("--release")
    resume_parser = verbs.add_parser("resume", allow_abbrev=False, help="Resume or abandon the open install operation")
    resume_parser.add_argument("--operation")
    resume_parser.add_argument("--abandon", action="store_true")
    return install


def _ask(interaction, prompt):
    try:
        answer = interaction.ask(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    return answer


def _inputs(kind, values, interaction):
    """Prompt each missing input stage, in INPUT_STAGES order; q, empty, EOF or Ctrl-C cancel."""
    stages = INPUT_STAGES[kind]
    request = dict(values)
    for number, stage in enumerate(stages, 1):
        if request.get(stage) is not None:
            continue
        answer = _ask(interaction, f"[{number}/{len(stages) + 1}] {stage} (q cancels): ")
        if answer is None or answer.strip() in ("", "q"):
            raise Cancelled(stage)
        request[stage] = answer.strip()
    return request


def _confirm(interaction, phrase):
    answer = _ask(interaction, f"Type exactly '{phrase}' to continue (q cancels): ")
    if answer is None or answer.strip() != phrase:
        raise Cancelled("summary")


def _status(root, interaction):
    items = operations(root)
    if not items:
        interaction.say("No install operations.")
    for item in items:
        line = (f"INSTALL OPERATION {item['operation_id']} kind={item['kind'] or 'unknown'} phase={item['phase']}"
                f" updated={item['updated'] or '-'}")
        if item["phase"] in OPEN_PHASES or item["phase"] == "unreadable":
            line += f": next: {_next_text(root, item)}"
        if item["error"]:
            line += f" ({item['error']})"
        interaction.say(line)
    return 0


def _report_preflight(result, interaction):
    for note in result.notes:
        interaction.say(f"Note: {note['code']}: {note['detail']}")
    if result.conflicts:
        raise PreflightRefused(result.conflicts)


def _say_summary(root, result, interaction):
    default_slug, slugs = None, []
    try:
        registry = pf_instance.load_registry(root)
        default_slug = next((entry.slug for entry in registry.entries if entry.instance_id == registry.default_instance_id),
                            None)
        slugs = [entry.slug for entry in sorted(registry.entries, key=lambda item: item.instance_id)]
    except pf_instance.ContextError:
        pass
    for line in render_summary(result.plan, default_slug=default_slug, slugs=slugs):
        interaction.say(line)


def _finish_message(journal, plan, root):
    kind = plan["kind"]
    result = journal["result"] or {}
    if kind == "control":
        return f"Control release {result.get('release_id')} is bound (operation {plan['operation_id']} completed)."
    if kind == "init":
        return f"Installation root {plan['root']} initialized with release {result.get('release_id')} (operation " \
               f"{plan['operation_id']} completed). Next: create the configuration by hand, then " \
               f"'{launcher_prefix(plan['root'], Path(plan['launcher']['path']) if plan['launcher'] else DEFAULT_LAUNCHER)}" \
               " install register'."
    return f"Instance {plan['instance']['slug']} registered (operation {plan['operation_id']} completed); the default " \
           "is unchanged."


def run_installed(root, args, *, running_release, trusted_launch, interaction):
    """``pf install …`` from the installed, verified release. Returns the exit status; prints its own copy."""
    root = Path(root)
    verb = args.install_verb
    try:
        if verb == "status":
            return _status(root, interaction)
        if not trusted_launch:
            raise InstallError("untrusted-launch", "Mutating commands must start through the installed bootstrap "
                                                   "launcher (Python isolated mode, sanitized environment).")
        if interaction.unattended():
            raise InstallError("terminal-required", f"'install {verb}' asks for a typed confirmation and cannot run "
                                                    f"without a terminal. Run it interactively: "
                                                    f"{launcher_prefix(root)} install {verb} …. Nothing was changed.")
        if verb == "resume":
            journal = resume(root, operation_id=args.operation, abandon=args.abandon,
                             runner=_installed_runner(root), interaction=interaction)
            interaction.say(f"Install operation {journal['operation_id']} is {journal['phase']}.")
            return 0
        kind = {"register": "register", "migrate-legacy": "migrate-legacy", "control": "control"}[verb]
        values = {}
        if kind == "register":
            values = {"slug": args.slug, "project": args.project, "workspace": args.workspace,
                      "configuration": args.configuration, "backups": args.backups, "recovery": args.recovery}
        elif kind == "migrate-legacy":
            values = {"legacy-home": args.legacy_home, "workspace": args.workspace, "slug": args.slug,
                      "launcher_path": args.launcher_path, "no_launcher": args.no_launcher}
        else:
            values = {"candidate": args.source if args.source is not None else args.release}
        request = _inputs(kind, values, interaction)
        if kind == "control":
            candidate = request.pop("candidate")
            # The flag the operator typed decides (section 4.1); only a prompted answer is classified.
            if args.source is not None:
                request["source"] = candidate
            elif args.release is not None:
                request["release"] = candidate
            else:
                request["source" if candidate.startswith("/") else "release"] = candidate
        if kind != "control":
            request["docker_endpoint"] = args.docker_endpoint or DEFAULT_DOCKER_ENDPOINT
        runner = _installed_runner(root, request.get("docker_endpoint"))
        result = _preflight(root, kind, request, runner=runner, running_release=running_release)
        _report_preflight(result, interaction)
        _say_summary(root, result, interaction)
        _confirm(interaction, confirmation_phrase(result.plan))
        journal = execute(root, result.plan, runner=runner, interaction=interaction, candidate=result.candidate,
                          running_release=running_release, request=request)
        interaction.say(_finish_message(journal, result.plan, root))
        return 0
    except InstallError as exc:
        if exc.exit_code == 0:
            interaction.say(str(exc))
        else:
            print(f"ERROR: {exc.code}: {exc}", file=sys.stderr, flush=True)
        return exc.exit_code
    except KeyboardInterrupt:
        print("ERROR: install-cancelled: Cancelled; nothing was changed.", file=sys.stderr, flush=True)
        return 1


# ----------------------------------------------------------------------- repository entry (init)


def unattended():
    """True when no operator terminal is attached (the same fail-closed predicate as pf-admin.py)."""
    stream = sys.stdin
    if stream is None:
        return True
    try:
        return not stream.isatty()
    except (ValueError, OSError, AttributeError):  # closed or detached stream
        return True


def _say(text):
    print(text, flush=True)


def _tool_option(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected <tool id>=<absolute path>")
    tool, path = value.split("=", 1)
    return tool, path


def init_parser():
    parser = argparse.ArgumentParser(prog="install-control.sh init", allow_abbrev=False,
                                     description="Initialize a new protected Deployment Admin installation root.")
    parser.add_argument("--root")
    parser.add_argument("--interpreter")
    parser.add_argument("--tool", action="append", type=_tool_option, default=[])
    launcher = parser.add_mutually_exclusive_group()
    launcher.add_argument("--launcher-path")
    launcher.add_argument("--no-launcher", action="store_true")
    return parser


def run_init(arguments, interaction, *, source_root):
    """The init protocol (inputs, preflight, summary, confirmation, execute). Returns the exit status."""
    args = init_parser().parse_args(arguments)
    try:
        request = _inputs("init", {"root": args.root}, interaction)
        request.update(source_root=str(source_root), interpreter=args.interpreter,
                       tools=dict(args.tool), launcher_path=args.launcher_path, no_launcher=args.no_launcher)
        result = _preflight(None, "init", request, runner=None, running_release=None)
        _report_preflight(result, interaction)
        _say_summary(Path(request["root"]), result, interaction)
        _confirm(interaction, confirmation_phrase(result.plan))
        journal = execute(request["root"], result.plan, runner=result.runner, interaction=interaction,
                          candidate=result.candidate, leftovers=result.leftovers, request=request)
        interaction.say(_finish_message(journal, result.plan, request["root"]))
        return 0
    except InstallError as exc:
        if exc.exit_code == 0:
            interaction.say(str(exc))
        else:
            print(f"ERROR: {exc.code}: {exc}", file=sys.stderr, flush=True)
        return exc.exit_code
    except KeyboardInterrupt:
        print("ERROR: install-cancelled: Cancelled; nothing was changed.", file=sys.stderr, flush=True)
        return 1


def main(argv=None):
    """Repository entry (``install-control.sh init``): isolated interpreter, uid 0 and a terminal required."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not sys.flags.isolated:
        print("ERROR: installer-isolation-required: run through install-control.sh (python -I -B).", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("ERROR: installer-root-required: Run as root: sudo sh ./deploy/synology/install-control.sh init --root "
              "<root>", file=sys.stderr)
        return 2
    if unattended():
        print("ERROR: installer-terminal-required: Interactive terminal required; installation has no --yes bypass.",
              file=sys.stderr)
        return 2
    pf_runner.install_interrupt_handlers()
    if not argv:
        print("Usage: sudo sh ./deploy/synology/install-control.sh init [--root <root>] [--interpreter <python3>] "
              "[--tool <id>=<path>]... [--launcher-path <path> | --no-launcher]", file=sys.stderr)
        return 2
    if argv[0] != "init":
        print(f"ERROR: installer-verb-installed-only: install-control.sh only initializes a new installation root. "
              f"Run '{argv[0]}' from the installed control: sudo <root>/bootstrap/pf install {argv[0]} …. Nothing was "
              "read or changed.", file=sys.stderr)
        return 2
    source_root = Path(__file__).resolve().parents[2]
    return run_init(argv[1:], Interaction(unattended, input, _say), source_root=source_root)


if __name__ == "__main__":
    sys.exit(main())
