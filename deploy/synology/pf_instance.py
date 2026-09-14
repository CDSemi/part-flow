"""Protected instance context, registry, bootstrap trust and stable locks (PF-A1.1).

Python standard library only. Loading, resolving and validating never create,
repair or migrate state. The only writers in this module are the explicit
transactions ``initialize_installation_root`` and ``register_instance``; both
are for fresh, disposable installations (fixtures and future installers) and
never touch containers, credentials or an existing legacy NAS layout.

Trust model (see ARCHITECTURE.md sections 3-4): the installed bootstrap
launcher is the trust anchor chosen by the host administrator. Code in this
module validates the installation root, the release it runs from, the
registry, the records and the registered paths against that anchor. It does
not claim to authenticate itself after import.
"""
import dataclasses
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import uuid

SCHEMA_VERSION = 1
TRUSTED_UID = 0
CONTROL_INVENTORY_NAME = "control-manifest.json"
BOOTSTRAP_DIR = "bootstrap"
LAUNCHER_NAME = "pf"
BOOTSTRAP_CONF_NAME = "bootstrap.conf"
REGISTRY_RELATIVE = Path("registry") / "instances.json"
REGISTRY_LOCK_RELATIVE = Path("locks") / "registry.lock"
STATE_SUBDIR = "state"
PROFILE_ID = "partflow-staging-legacy"
PROFILE_COMPOSE_FILE = "compose.nas.yaml"
PROFILE_APPLICATION = "partflow"
SUPPORTED_ENVIRONMENTS = ("staging",)
# The A1 registry envelope (work-packages/PF-A1.md section 4).
REGISTRY_KEYS = ("schema_version", "default_instance_id", "instances")
REGISTRY_ENTRY_KEYS = ("instance_id", "slug", "record_path")
UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
NAME_PATTERN = "^[a-z0-9][a-z0-9_-]{0,39}$"
PATH_PATTERN = "^/[^\\u0000-\\u001f\\u007f]+$"
SHA256_PATTERN = "^[a-f0-9]{64}$"
RELEASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

# Embedded copy of contracts/instance-record.schema.json (r3). A test asserts
# the two stay identical; the runtime validator below implements exactly the
# keywords this envelope uses.
INSTANCE_RECORD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin protected InstanceRecord v1",
    "description": (
        "A1 envelope. Structural validation only; runtime also verifies trusted registration, "
        "paths, ownership, ACLs, referenced bytes, daemon identity and policy. Examples are not "
        "installed records."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "instance_id", "slug", "state", "record_revision", "compose_project",
        "approved_environment", "daemon", "paths", "control", "profile", "approved_policy",
        "approved_config_revision",
    ],
    "properties": {
        "schema_version": {"const": 1},
        "instance_id": {"type": "string", "pattern": UUID_PATTERN},
        "slug": {"type": "string", "pattern": NAME_PATTERN},
        "state": {"enum": ["registered", "active", "purged"]},
        "record_revision": {"type": "integer", "minimum": 1},
        "compose_project": {"type": "string", "pattern": NAME_PATTERN},
        "approved_environment": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,39}$"},
        "daemon": {
            "type": "object",
            "additionalProperties": False,
            "required": ["endpoint", "engine_id", "scope", "rootless"],
            "properties": {
                "endpoint": {"type": "string", "pattern": "^unix:///[^\\u0000-\\u001f\\u007f]+$"},
                "engine_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "scope": {"const": "local"},
                "rootless": {"const": False},
            },
        },
        "paths": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workspace", "configuration", "backups", "recovery", "private_state"],
            "properties": {
                "workspace": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "configuration": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "backups": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "recovery": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "private_state": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
            },
        },
        "control": {
            "type": "object",
            "additionalProperties": False,
            "required": ["release_id", "path", "sha256"],
            "properties": {
                "release_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "path": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "sha256": {"type": "string", "pattern": SHA256_PATTERN},
            },
        },
        "profile": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "version", "path", "sha256"],
            "properties": {
                "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,79}$"},
                "version": {"type": "string", "minLength": 1, "maxLength": 128},
                "path": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "sha256": {"type": "string", "pattern": SHA256_PATTERN},
            },
        },
        "approved_policy": {
            "type": "object",
            "additionalProperties": False,
            "required": ["revision", "path", "sha256"],
            "properties": {
                "revision": {"type": "integer", "minimum": 1},
                "path": {"type": "string", "minLength": 2, "pattern": PATH_PATTERN},
                "sha256": {"type": "string", "pattern": SHA256_PATTERN},
            },
        },
        "approved_config_revision": {"type": "integer", "minimum": 0},
    },
}


class ContextError(RuntimeError):
    """A diagnostic result. Raising it never implies state was changed."""


class LockBusy(ContextError):
    """The stable lock is held by another process."""


# --------------------------------------------------------------------------- JSON


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContextError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def _reject_constant(name):
    raise ContextError("Non-finite JSON number is not allowed: " + name)


def parse_strict_json(data, *, label):
    """Parse UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContextError(f"{label}: not valid UTF-8: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except ValueError as exc:
        raise ContextError(f"{label}: invalid JSON: {exc}") from exc


def normalize_json(value):
    """Normalized record bytes: sorted keys, UTF-8, no whitespace, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


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


# ---------------------------------------------------------------- schema validation


def _anchored_fullmatch(pattern, value):
    if pattern.startswith("^") and pattern.endswith("$"):
        # JSON Schema patterns are ECMA-262 where `$` is a hard end anchor.
        return re.fullmatch(pattern[1:-1], value) is not None
    return re.search(pattern, value) is not None


def _type_matches(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ContextError("Unsupported schema type keyword: " + str(expected))


def _same_json_value(left, right):
    # Booleans are not integers, even though Python treats bool as int.
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    return left == right


def validate_against_schema(value, schema, path="$", errors=None):
    """Validate a parsed JSON value against the subset of JSON Schema used by the A1 contracts."""
    errors = [] if errors is None else errors
    supported = {
        "$schema", "title", "description", "type", "const", "enum", "pattern", "required",
        "additionalProperties", "properties", "minimum", "minLength", "maxLength",
    }
    unsupported = set(schema) - supported
    if unsupported:
        raise ContextError("Schema uses unsupported keywords: " + ", ".join(sorted(unsupported)))
    if "const" in schema and not _same_json_value(value, schema["const"]):
        errors.append(f"{path}: must equal {json.dumps(schema['const'])}")
        return errors
    if "enum" in schema and not any(_same_json_value(value, item) for item in schema["enum"]):
        errors.append(f"{path}: must be one of {json.dumps(schema['enum'])}")
        return errors
    if "type" in schema and not _type_matches(value, schema["type"]):
        errors.append(f"{path}: expected {schema['type']}")
        return errors
    if isinstance(value, str):
        if "pattern" in schema and not _anchored_fullmatch(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                errors.append(f"{path}: missing required key {name}")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                errors.append(f"{path}: unknown keys {', '.join(unknown)}")
        for name, subschema in properties.items():
            if name in value:
                validate_against_schema(value[name], subschema, f"{path}.{name}", errors)
    return errors


def validate_instance_record(record):
    errors = validate_against_schema(record, INSTANCE_RECORD_SCHEMA)
    if errors:
        raise ContextError("Invalid InstanceRecord: " + "; ".join(errors))
    for name, value in record["paths"].items():
        _require_normalized_absolute(value, "paths." + name)
    for name in ("control", "profile", "approved_policy"):
        _require_normalized_absolute(record[name]["path"], name + ".path")
    return record


def _require_normalized_absolute(value, label):
    path = Path(value)
    if not path.is_absolute() or str(path) != value or any(part in ("..", ".") for part in path.parts):
        raise ContextError(f"{label} must be a normalized absolute path without '.' or '..': {value}")


# ------------------------------------------------------------------- data classes


@dataclasses.dataclass(frozen=True)
class DaemonBinding:
    endpoint: str
    engine_id: str
    scope: str
    rootless: bool


@dataclasses.dataclass(frozen=True)
class RegisteredPaths:
    workspace: Path
    configuration: Path
    backups: Path
    recovery: Path
    private_state: Path


@dataclasses.dataclass(frozen=True)
class ControlBinding:
    release_id: str
    path: Path
    sha256: str


@dataclasses.dataclass(frozen=True)
class ProfileBinding:
    id: str
    version: str
    path: Path
    sha256: str


@dataclasses.dataclass(frozen=True)
class PolicyBinding:
    revision: int
    path: Path
    sha256: str


@dataclasses.dataclass(frozen=True)
class InstanceContext:
    """Immutable selected instance. App parameters never mutate it."""

    installation_root: Path
    instance_id: str
    slug: str
    state: str
    record_revision: int
    compose_project: str
    approved_environment: str
    daemon: DaemonBinding
    paths: RegisteredPaths
    control: ControlBinding
    profile: ProfileBinding
    approved_policy: PolicyBinding
    approved_config_revision: int
    record_path: Path
    record_sha256: str

    @property
    def lock_path(self):
        return self.installation_root / "locks" / (self.instance_id + ".lock")

    @property
    def registry_lock_path(self):
        return self.installation_root / REGISTRY_LOCK_RELATIVE

    @property
    def state_dir(self):
        return self.paths.private_state / STATE_SUBDIR

    @property
    def journal_path(self):
        return self.state_dir / "pending.json"


def context_from_record(root, record, record_path, record_sha256):
    validate_instance_record(record)
    return InstanceContext(
        installation_root=Path(root),
        instance_id=record["instance_id"],
        slug=record["slug"],
        state=record["state"],
        record_revision=record["record_revision"],
        compose_project=record["compose_project"],
        approved_environment=record["approved_environment"],
        daemon=DaemonBinding(**record["daemon"]),
        paths=RegisteredPaths(**{name: Path(value) for name, value in record["paths"].items()}),
        control=ControlBinding(record["control"]["release_id"], Path(record["control"]["path"]),
                               record["control"]["sha256"]),
        profile=ProfileBinding(record["profile"]["id"], record["profile"]["version"],
                               Path(record["profile"]["path"]), record["profile"]["sha256"]),
        approved_policy=PolicyBinding(record["approved_policy"]["revision"],
                                      Path(record["approved_policy"]["path"]),
                                      record["approved_policy"]["sha256"]),
        approved_config_revision=record["approved_config_revision"],
        record_path=Path(record_path),
        record_sha256=record_sha256,
    )


# ------------------------------------------------------------------------ registry


@dataclasses.dataclass(frozen=True)
class RegistryEntry:
    instance_id: str
    slug: str
    record_path: Path


@dataclasses.dataclass(frozen=True)
class Registry:
    root: Path
    path: Path
    default_instance_id: object
    entries: tuple
    sha256: str

    def entry(self, instance_id):
        for entry in self.entries:
            if entry.instance_id == instance_id:
                return entry
        return None

    def load_record(self, entry):
        """Strictly load one registered record. No fallback to another instance."""
        try:
            data = read_bytes_nofollow(entry.record_path)
        except OSError as exc:
            raise ContextError(f"Registered record for {entry.slug} cannot be read: {exc}") from exc
        record = parse_strict_json(data, label=str(entry.record_path))
        context = context_from_record(self.root, record, entry.record_path, sha256_bytes(data))
        if context.instance_id != entry.instance_id or context.slug != entry.slug:
            raise ContextError(
                f"Registry entry {entry.slug}/{entry.instance_id} disagrees with its record "
                f"{context.slug}/{context.instance_id}."
            )
        if context.paths.private_state != entry.record_path.parent:
            raise ContextError(f"Record {entry.slug} is not stored in its registered private_state directory.")
        return context

    def records(self):
        """Load every record; per-entry diagnostics instead of a single failure."""
        loaded = []
        for entry in self.entries:
            try:
                loaded.append((entry, self.load_record(entry), None))
            except ContextError as exc:
                loaded.append((entry, None, str(exc)))
        return loaded


def registry_path(root):
    return Path(root) / REGISTRY_RELATIVE


def _validate_registry_document(root, document, label):
    if not isinstance(document, dict):
        raise ContextError(f"{label}: registry must be a JSON object.")
    unknown = sorted(set(document) - set(REGISTRY_KEYS))
    missing = [key for key in REGISTRY_KEYS if key not in document]
    if unknown or missing:
        raise ContextError(f"{label}: registry keys invalid; unknown={unknown} missing={missing}")
    if not _same_json_value(document["schema_version"], SCHEMA_VERSION):
        raise ContextError(f"{label}: unsupported registry schema_version {document['schema_version']!r}")
    default = document["default_instance_id"]
    if default is not None and not (isinstance(default, str) and _anchored_fullmatch(UUID_PATTERN, default)):
        raise ContextError(f"{label}: default_instance_id must be null or a UUID")
    if not isinstance(document["instances"], list):
        raise ContextError(f"{label}: instances must be an array")
    entries = []
    seen_ids, seen_slugs, seen_paths = set(), set(), set()
    instances_root = Path(root) / "instances"
    for index, item in enumerate(document["instances"]):
        where = f"{label}: instances[{index}]"
        if not isinstance(item, dict) or set(item) != set(REGISTRY_ENTRY_KEYS):
            raise ContextError(f"{where}: entry keys must be exactly {list(REGISTRY_ENTRY_KEYS)}")
        instance_id, slug, record_path = item["instance_id"], item["slug"], item["record_path"]
        if not (isinstance(instance_id, str) and _anchored_fullmatch(UUID_PATTERN, instance_id)):
            raise ContextError(f"{where}: invalid instance_id")
        if not (isinstance(slug, str) and _anchored_fullmatch(NAME_PATTERN, slug)):
            raise ContextError(f"{where}: invalid slug")
        if not isinstance(record_path, str):
            raise ContextError(f"{where}: record_path must be a string")
        _require_normalized_absolute(record_path, where + ".record_path")
        expected = instances_root / instance_id / "record.json"
        if Path(record_path) != expected:
            raise ContextError(f"{where}: record_path must be {expected}, inside the installation root")
        if instance_id in seen_ids:
            raise ContextError(f"{where}: duplicate instance_id {instance_id}")
        if slug in seen_slugs:
            raise ContextError(f"{where}: duplicate slug {slug}")
        if record_path in seen_paths:
            raise ContextError(f"{where}: duplicate record_path {record_path}")
        seen_ids.add(instance_id)
        seen_slugs.add(slug)
        seen_paths.add(record_path)
        entries.append(RegistryEntry(instance_id, slug, Path(record_path)))
    if default is not None and default not in seen_ids:
        raise ContextError(f"{label}: default_instance_id {default} is not a registered instance")
    return default, tuple(entries)


def load_registry(root):
    """Protected bootstrap root -> strict registration records/default. No side effects."""
    root = Path(root)
    if not root.is_absolute():
        raise ContextError("Installation root must be an absolute path: " + str(root))
    path = registry_path(root)
    try:
        data = read_bytes_nofollow(path)
    except OSError as exc:
        raise ContextError(f"Instance registry cannot be read: {path}: {exc}") from exc
    document = parse_strict_json(data, label=str(path))
    default, entries = _validate_registry_document(root, document, str(path))
    return Registry(root=root, path=path, default_instance_id=default, entries=entries,
                    sha256=sha256_bytes(data))


def unpublished_registrations(registry):
    """Instance directories that exist but are not published in the registry (interrupted registrations)."""
    instances_root = registry.root / "instances"
    published = {entry.instance_id for entry in registry.entries}
    orphans = []
    try:
        names = sorted(os.listdir(instances_root))
    except OSError:
        return orphans
    for name in names:
        if name in published or not _anchored_fullmatch(UUID_PATTERN, name):
            continue
        orphans.append(instances_root / name)
    return orphans


def resolve_instance(registry, *, instance=None, project=None):
    """Registry + explicit ID/default selection -> immutable InstanceContext.

    Selection order (ARCHITECTURE.md section 4): explicit ``--instance`` (UUID or
    slug), legacy ``--project`` alias when it maps to exactly one registration,
    the protected default, then a single registration. Nothing is read from the
    process environment and no other instance is used as a fallback.
    """
    entries = registry.entries
    if instance is not None:
        matches = [entry for entry in entries if instance in (entry.instance_id, entry.slug)]
        if len(matches) != 1:
            raise ContextError(f"--instance {instance!r} does not identify exactly one registered instance.")
        return registry.load_record(matches[0])
    if project is not None:
        matches = []
        for entry, context, error in registry.records():
            if context is not None and context.compose_project == project and context.state != "purged":
                matches.append(context)
        if len(matches) != 1:
            raise ContextError(
                f"--project {project!r} does not map to exactly one registered active instance; use --instance."
            )
        return matches[0]
    if registry.default_instance_id is not None:
        entry = registry.entry(registry.default_instance_id)
        return registry.load_record(entry)
    active = []
    for entry, context, error in registry.records():
        if error is not None:
            raise ContextError(f"Cannot select automatically; registered record {entry.slug} is invalid: {error}")
        if context.state != "purged":
            active.append(context)
    if len(active) == 1:
        return active[0]
    if not active:
        raise ContextError("No active registered instance exists in " + str(registry.root))
    names = ", ".join(sorted(context.slug for context in active))
    raise ContextError(
        "Multiple registered instances and no protected default; pass --instance <slug|uuid>. "
        "Registered: " + names
    )


# ------------------------------------------------------------ protected path checks


@dataclasses.dataclass(frozen=True)
class Finding:
    severity: str  # "refuse" blocks mutation; "note" is informational
    code: str
    path: str
    message: str

    def render(self):
        return f"[{self.severity}] {self.code}: {self.path}: {self.message}"


@dataclasses.dataclass(frozen=True)
class ContextValidation:
    context: InstanceContext
    findings: tuple
    acl_state: str

    @property
    def mutation_allowed(self):
        return not any(finding.severity == "refuse" for finding in self.findings)

    def blocking_messages(self):
        return [finding.render() for finding in self.findings if finding.severity == "refuse"]


def _lstat(path):
    try:
        return os.lstat(str(path))
    except OSError as exc:
        raise ContextError(f"Cannot lstat {path}: {exc}") from exc


def _writable_by_others(mode):
    return bool(stat.S_IMODE(mode) & 0o022)


def _replacement_resistant_ancestor(info):
    """A root-owned entry inside this directory cannot be renamed/unlinked by editors."""
    if info.st_uid != TRUSTED_UID:
        return False
    if not _writable_by_others(info.st_mode):
        return True
    # A sticky world-writable directory (for example /tmp) protects root-owned
    # entries from being renamed or removed by other users.
    return bool(info.st_mode & stat.S_ISVTX)


class _Checker:
    def __init__(self, root):
        self.root = Path(root)
        self.findings = []
        self.acl_states = set()
        self.root_dev = None
        self.bootstrap_conf = None

    def blocking(self):
        return [finding.render() for finding in self.findings if finding.severity == "refuse"]

    def refuse(self, code, path, message):
        self.findings.append(Finding("refuse", code, str(path), message))

    def note(self, code, path, message):
        self.findings.append(Finding("note", code, str(path), message))

    def ancestors(self, path, *, stop_at=None):
        """Check every component above ``path`` (down to ``stop_at`` exclusive) without following links."""
        path = Path(path)
        current = Path(path.anchor)
        for part in path.parts[1:-1]:
            current = current / part
            if stop_at is not None and current == stop_at:
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
            if not _replacement_resistant_ancestor(info):
                self.refuse(
                    "ancestor-replaceable", current,
                    f"owner uid {info.st_uid} mode {oct(stat.S_IMODE(info.st_mode))}: an editor could replace entries",
                )
                return False
        return True

    def protected(self, path, *, kind, allow_group_read=True, require_nlink_one=True):
        """No-follow lstat checks for one protected entry. Returns the stat or None."""
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
        if _writable_by_others(info.st_mode):
            self.refuse("writable", path, f"mode {oct(stat.S_IMODE(info.st_mode))} is group/world writable")
        if not allow_group_read and stat.S_IMODE(info.st_mode) & 0o077:
            self.refuse("exposed", path, f"mode {oct(stat.S_IMODE(info.st_mode))} exposes private state")
        if kind == "file" and require_nlink_one and info.st_nlink != 1:
            self.refuse("hardlinked", path, f"{info.st_nlink} links; another name can change this content")
        if self.root_dev is not None and info.st_dev != self.root_dev:
            self.refuse("mount-boundary", path, "different device than the installation root; unsupported mount boundary")
        self.acl(path, info)
        return info

    def acl(self, path, info):
        state = inspect_posix_acl(path)
        self.acl_states.add(state.kind)
        for message in state.write_grants:
            self.refuse("acl-write", path, message)
        if state.kind == "unknown":
            self.refuse("acl-unknown", path, state.detail or "ACL attributes present but not understood; mutation limited")

    def protected_tree(self, directory):
        """Every entry below a protected directory must itself be protected (release trees)."""
        directory = Path(directory)
        for current, dirs, files in os.walk(str(directory), followlinks=False):
            for name in dirs:
                self.protected(Path(current) / name, kind="dir")
            for name in files:
                self.protected(Path(current) / name, kind="file")


@dataclasses.dataclass(frozen=True)
class AclState:
    kind: str  # "none", "posix", "unknown"
    write_grants: tuple
    detail: str = ""


_ACL_USER_OBJ, _ACL_USER, _ACL_GROUP_OBJ, _ACL_GROUP, _ACL_MASK, _ACL_OTHER = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
_ACL_WRITE = 0x02
_ACL_VERSION = 0x0002


def parse_posix_acl(blob):
    """Parse a Linux system.posix_acl_* xattr blob into (tag, perm, id) tuples."""
    if len(blob) < 4 or (len(blob) - 4) % 8:
        raise ValueError("unexpected ACL blob length")
    (version,) = struct.unpack("<I", blob[:4])
    if version != _ACL_VERSION:
        raise ValueError(f"unsupported ACL version {version}")
    entries = []
    for offset in range(4, len(blob), 8):
        tag, perm, identifier = struct.unpack("<HHI", blob[offset:offset + 8])
        entries.append((tag, perm, identifier))
    return entries


def inspect_posix_acl(path):
    """Report ACL entries granting write access beyond the POSIX mode bits."""
    if not hasattr(os, "listxattr"):
        return AclState("unknown", (), "extended attribute API unavailable; ACL state cannot be verified")
    try:
        names = os.listxattr(str(path), follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENODATA, errno.ENOENT):
            return AclState("none", ())
        return AclState("unknown", (), f"cannot list extended attributes: {exc}")
    grants = []
    kind = "none"
    for name in names:
        if name in ("system.posix_acl_access", "system.posix_acl_default"):
            try:
                entries = parse_posix_acl(os.getxattr(str(path), name, follow_symlinks=False))
            except (OSError, ValueError) as exc:
                return AclState("unknown", (), f"{name} present but unreadable: {exc}")
            kind = "posix"
            mask = None
            for tag, perm, _ in entries:
                if tag == _ACL_MASK:
                    mask = perm
            for tag, perm, identifier in entries:
                effective = perm if mask is None or tag in (_ACL_USER_OBJ, _ACL_OTHER) else perm & mask
                if tag in (_ACL_USER, _ACL_GROUP, _ACL_GROUP_OBJ, _ACL_OTHER) and effective & _ACL_WRITE:
                    who = {_ACL_USER: f"user {identifier}", _ACL_GROUP: f"group {identifier}",
                           _ACL_GROUP_OBJ: "owning group", _ACL_OTHER: "others"}[tag]
                    grants.append(f"{name} grants write to {who}")
        elif "acl" in name.lower():
            return AclState("unknown", tuple(grants), f"unknown ACL attribute {name}; mutation limited")
    return AclState(kind, tuple(grants))


def parse_bootstrap_conf(data, *, label):
    """KEY=VALUE data parsing. No shell evaluation; only known keys with absolute normalized paths."""
    values = {}
    allowed = ("interpreter", "control_release")
    for number, raw in enumerate(data.decode("utf-8", "strict").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ContextError(f"{label}:{number}: expected key=value")
        key, value = line.split("=", 1)
        if key not in allowed:
            raise ContextError(f"{label}:{number}: unknown key {key!r}")
        if key in values:
            raise ContextError(f"{label}:{number}: duplicate key {key!r}")
        parts = value.split("/")
        if (not re.fullmatch(r"/[A-Za-z0-9._/-]+", value) or any(part in ("", ".", "..") for part in parts[1:])
                or value.endswith("/")):
            raise ContextError(f"{label}:{number}: {key} must be a normalized absolute path")
        values[key] = value
    missing = [key for key in allowed if key not in values]
    if missing:
        raise ContextError(f"{label}: missing keys {', '.join(missing)}")
    return values


def load_control_inventory(release_dir):
    path = Path(release_dir) / CONTROL_INVENTORY_NAME
    data = read_bytes_nofollow(path)
    inventory = parse_strict_json(data, label=str(path))
    expected_keys = {"schema_version", "release_id", "files"}
    if not isinstance(inventory, dict) or set(inventory) != expected_keys:
        raise ContextError(f"{path}: control inventory keys must be exactly {sorted(expected_keys)}")
    if not _same_json_value(inventory["schema_version"], SCHEMA_VERSION):
        raise ContextError(f"{path}: unsupported control inventory schema_version")
    if not isinstance(inventory["release_id"], str) or not RELEASE_ID_RE.fullmatch(inventory["release_id"]):
        raise ContextError(f"{path}: invalid release_id")
    files = inventory["files"]
    if not isinstance(files, dict) or not files:
        raise ContextError(f"{path}: files must be a non-empty object")
    for name, value in files.items():
        if not re.fullmatch(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", name) or name.startswith("."):
            raise ContextError(f"{path}: unsupported inventory entry name {name!r}")
        if not isinstance(value, str) or not _anchored_fullmatch(SHA256_PATTERN, value):
            raise ContextError(f"{path}: {name}: sha256 must be 64 lowercase hex characters")
    return inventory, sha256_bytes(data)


def validate_installation_root(root, checker=None):
    """Read-only trust checks for the protected installation root and bootstrap. Returns findings."""
    root = Path(root)
    checker = checker or _Checker(root)
    if not root.is_absolute() or any(part in ("..", ".") for part in root.parts):
        checker.refuse("root-not-normalized", root, "installation root must be a normalized absolute path")
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
    conf_path = bootstrap / BOOTSTRAP_CONF_NAME
    if checker.protected(conf_path, kind="file") is not None:
        try:
            conf = parse_bootstrap_conf(read_bytes_nofollow(conf_path), label=str(conf_path))
        except (ContextError, OSError, UnicodeDecodeError) as exc:
            checker.refuse("bootstrap-conf-invalid", conf_path, str(exc))
            conf = None
        if conf is not None:
            checker.bootstrap_conf = conf
            interpreter = Path(conf["interpreter"])
            if checker.ancestors(interpreter):
                # The interpreter may live on another filesystem than the root.
                saved_dev, checker.root_dev = checker.root_dev, None
                interpreter_info = checker.protected(interpreter, kind="file", require_nlink_one=False)
                checker.root_dev = saved_dev
                if interpreter_info is not None and not interpreter_info.st_mode & stat.S_IXUSR:
                    checker.refuse("interpreter-not-executable", interpreter, "registered interpreter is not executable")
            if os.path.realpath(str(interpreter)) != str(interpreter):
                checker.refuse("interpreter-not-canonical", interpreter,
                               "bootstrap must register the canonical interpreter path")
            release = Path(conf["control_release"])
            if release.parent != root / "releases":
                checker.refuse("bootstrap-release-outside-root", release, "control release must be under <root>/releases")
    checker.protected(root / "registry", kind="dir")
    checker.protected(registry_path(root), kind="file")
    checker.protected(root / "locks", kind="dir")
    checker.protected(root / REGISTRY_LOCK_RELATIVE, kind="file")
    checker.protected(root / "instances", kind="dir")
    checker.protected(root / "releases", kind="dir")
    return checker


def validate_release(checker, release_dir, expected_sha256=None, expected_release_id=None):
    release_dir = Path(release_dir)
    if checker.protected(release_dir, kind="dir") is None:
        return None
    checker.protected_tree(release_dir)
    try:
        inventory, inventory_sha = load_control_inventory(release_dir)
    except (ContextError, OSError) as exc:
        checker.refuse("control-inventory-invalid", release_dir, str(exc))
        return None
    if expected_sha256 is not None and inventory_sha != expected_sha256:
        checker.refuse("control-inventory-hash", release_dir,
                       "installed control inventory does not match the protected record")
    if expected_release_id is not None and inventory["release_id"] != expected_release_id:
        checker.refuse("control-release-id", release_dir,
                       f"inventory release_id {inventory['release_id']} != registered {expected_release_id}")
    listed = set()
    for name, expected in inventory["files"].items():
        path = release_dir / name
        listed.add(path)
        try:
            actual = sha256_bytes(read_bytes_nofollow(path))
        except OSError as exc:
            checker.refuse("control-file-missing", path, str(exc))
            continue
        if actual != expected:
            checker.refuse("control-file-hash", path, "installed control file differs from its inventory hash")
    for current, _, files in os.walk(str(release_dir), followlinks=False):
        for name in files:
            path = Path(current) / name
            if path not in listed and path != release_dir / CONTROL_INVENTORY_NAME:
                checker.refuse("control-file-unlisted", path, "file is not part of the installed control inventory")
    return inventory


def validate_context(context, *, running_release=None, interpreter=None, checker=None):
    """Context + installed trust anchor -> validation result. Read-only lstat/ACL/hash checks."""
    root = context.installation_root
    checker = checker or _Checker(root)
    validate_installation_root(root, checker)
    conf = getattr(checker, "bootstrap_conf", None)
    if interpreter is not None and conf is not None:
        if os.path.realpath(str(interpreter)) != conf["interpreter"]:
            checker.refuse("interpreter-mismatch", interpreter,
                           "process interpreter is not the registered bootstrap interpreter")
    # Control release pinned by this instance.
    if context.control.path.parent != root / "releases" or context.control.path.name != context.control.release_id:
        checker.refuse("control-path", context.control.path, "control path must be <root>/releases/<release_id>")
    else:
        inventory = validate_release(checker, context.control.path, context.control.sha256, context.control.release_id)
        if inventory is not None and PROFILE_COMPOSE_FILE not in inventory["files"]:
            checker.refuse("control-compose-missing", context.control.path,
                           f"installed control release does not list {PROFILE_COMPOSE_FILE}")
    if running_release is not None and Path(running_release) != context.control.path:
        checker.refuse("control-release-mismatch", running_release,
                       f"instance {context.slug} pins release {context.control.release_id}; a control binding change is an "
                       "explicit installation transaction, not a runtime fallback")
    # Approved profile and policy bytes.
    _validate_profile(checker, context)
    _validate_policy(checker, context)
    # Registration record, private state and stable locks.
    private_state = context.paths.private_state
    if private_state != root / "instances" / context.instance_id:
        checker.refuse("private-state-path", private_state, "private_state must be <root>/instances/<instance_id>")
    checker.protected(private_state, kind="dir", allow_group_read=False)
    if context.record_path != private_state / "record.json":
        checker.refuse("record-path", context.record_path, "record must be <private_state>/record.json")
    record_info = checker.protected(context.record_path, kind="file", allow_group_read=False)
    if record_info is not None:
        try:
            if sha256_bytes(read_bytes_nofollow(context.record_path)) != context.record_sha256:
                checker.refuse("record-changed", context.record_path, "record bytes changed after selection")
        except OSError as exc:
            checker.refuse("record-unreadable", context.record_path, str(exc))
    if os.path.lexists(str(context.state_dir)):
        checker.protected(context.state_dir, kind="dir", allow_group_read=False)
    else:
        checker.note("state-missing", context.state_dir, "no private runtime state yet (not created by diagnostics)")
    checker.protected(context.lock_path, kind="file", allow_group_read=False)
    # Registered data paths. Workspace/configuration are editor-writable by
    # design; backups/recovery must not sit under an editor-replaceable ancestor.
    for name in ("workspace", "configuration"):
        path = getattr(context.paths, name)
        _data_directory(checker, path, name, protected=False)
    for name in ("backups", "recovery"):
        path = getattr(context.paths, name)
        _data_directory(checker, path, name, protected=True)
    _overlap_checks(checker, context)
    acl_state = "posix" if "posix" in checker.acl_states else "none"
    if "unknown" in checker.acl_states:
        acl_state = "unknown"
    return ContextValidation(context=context, findings=tuple(checker.findings), acl_state=acl_state)


def _data_directory(checker, path, name, *, protected):
    try:
        info = os.lstat(str(path))
    except OSError as exc:
        checker.refuse("registered-path-missing", path, f"{name}: {exc}")
        return
    if stat.S_ISLNK(info.st_mode):
        checker.refuse("registered-path-symlink", path, f"{name}: registered path is a symbolic link")
        return
    if not stat.S_ISDIR(info.st_mode):
        checker.refuse("registered-path-not-directory", path, f"{name}: not a directory")
        return
    if protected:
        checker.ancestors(path)
        if info.st_uid != TRUSTED_UID or _writable_by_others(info.st_mode):
            checker.refuse("storage-replaceable", path,
                           f"{name}: owner uid {info.st_uid} mode {oct(stat.S_IMODE(info.st_mode))}; "
                           "backup/recovery storage must be root-owned and not group/world writable")


def _overlap_checks(checker, context):
    root = context.installation_root
    for name in ("workspace", "configuration", "backups", "recovery"):
        path = getattr(context.paths, name)
        if path == root or root in path.parents:
            checker.refuse("path-inside-root", path, f"{name} must not live inside the installation root")
        if path in root.parents:
            checker.refuse("root-inside-path", path, f"installation root must not live inside {name}")


def load_profile(context):
    data = read_bytes_nofollow(context.profile.path)
    if sha256_bytes(data) != context.profile.sha256:
        raise ContextError("Approved profile bytes do not match the protected record hash.")
    profile = parse_strict_json(data, label=str(context.profile.path))
    expected = {"schema_version", "id", "version", "application", "compose_file"}
    if not isinstance(profile, dict) or set(profile) != expected:
        raise ContextError(f"Profile keys must be exactly {sorted(expected)}.")
    if not _same_json_value(profile["schema_version"], SCHEMA_VERSION):
        raise ContextError("Unsupported profile schema_version.")
    if profile["id"] != context.profile.id or profile["version"] != context.profile.version:
        raise ContextError("Profile id/version differ from the protected record.")
    if profile["id"] != PROFILE_ID or profile["application"] != PROFILE_APPLICATION:
        raise ContextError(f"A1 installs only the {PROFILE_ID} profile; arbitrary profiles are not loaded.")
    if profile["compose_file"] != PROFILE_COMPOSE_FILE:
        raise ContextError(f"The legacy PartFlow binding must use {PROFILE_COMPOSE_FILE}.")
    return profile


def load_policy(context):
    data = read_bytes_nofollow(context.approved_policy.path)
    if sha256_bytes(data) != context.approved_policy.sha256:
        raise ContextError("Approved policy bytes do not match the protected record hash.")
    policy = parse_strict_json(data, label=str(context.approved_policy.path))
    expected = {"schema_version", "revision", "environment"}
    if not isinstance(policy, dict) or set(policy) != expected:
        raise ContextError(f"Policy keys must be exactly {sorted(expected)}.")
    if not _same_json_value(policy["schema_version"], SCHEMA_VERSION):
        raise ContextError("Unsupported policy schema_version.")
    if not _type_matches(policy["revision"], "integer") or policy["revision"] != context.approved_policy.revision:
        raise ContextError("Policy revision differs from the protected record.")
    if policy["environment"] != context.approved_environment:
        raise ContextError("Policy environment differs from the approved environment in the record.")
    return policy


def _validate_profile(checker, context):
    if checker.protected(context.profile.path, kind="file") is None:
        return
    try:
        load_profile(context)
    except (ContextError, OSError) as exc:
        checker.refuse("profile-invalid", context.profile.path, str(exc))


def _validate_policy(checker, context):
    if checker.protected(context.approved_policy.path, kind="file") is None:
        return
    try:
        load_policy(context)
    except (ContextError, OSError) as exc:
        checker.refuse("policy-invalid", context.approved_policy.path, str(exc))
        return
    if context.approved_environment not in SUPPORTED_ENVIRONMENTS:
        checker.refuse("environment-unsupported", context.approved_policy.path,
                       f"approved environment {context.approved_environment!r} is not enabled in A1")


# ------------------------------------------------------------------------- locks


class LockHandle:
    """An open, flock-held descriptor on an existing registered lock file. Never unlinks."""

    def __init__(self, path, fd, info):
        self.path = Path(path)
        self.fd = fd
        self.inode = (info.st_dev, info.st_ino)

    def release(self):
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def acquire_lock(path, *, busy_message):
    """Open an existing protected lock file and take an exclusive non-blocking flock."""
    path = Path(path)
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ContextError(f"Registered lock is missing or unreadable: {path}: {exc}. Locks are created only by "
                           "registration and are never created implicitly.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != TRUSTED_UID or info.st_nlink != 1 \
                or _writable_by_others(info.st_mode):
            raise ContextError(f"Lock file is not a protected root-owned regular file: {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(busy_message) from exc
    except BaseException:
        os.close(fd)
        raise
    return LockHandle(path, fd, info)


def acquire_instance_lock(context):
    """Valid context -> stable lock handle. Opens the registered lock; creation only during registration."""
    return acquire_lock(
        context.lock_path,
        busy_message=f"Another operation holds the lock of instance {context.slug}; try again after it finishes.",
    )


def acquire_registry_lock(root):
    return acquire_lock(
        Path(root) / REGISTRY_LOCK_RELATIVE,
        busy_message="Another registration/default change holds the registry lock; try again later.",
    )


# ------------------------------------------------------------ explicit transactions


def _fsync_directory(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_private_file(path, data, mode=0o600):
    """Create-or-replace with flush/fsync and atomic rename inside the same directory."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(str(temporary), mode)
        os.replace(str(temporary), str(path))
    except BaseException:
        try:
            os.unlink(str(temporary))
        except OSError:
            pass
        raise
    _fsync_directory(path.parent)


def _create_private_dir(path, mode=0o700):
    os.mkdir(str(path), mode)
    os.chmod(str(path), mode)


def _create_lock_file(path):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    os.close(fd)
    os.chmod(str(path), 0o600)
    _fsync_directory(Path(path).parent)


def render_bootstrap_conf(interpreter, control_release):
    return (
        "# Deployment Admin bootstrap configuration. Data only; never sourced by a shell.\n"
        f"interpreter={interpreter}\n"
        f"control_release={control_release}\n"
    ).encode("utf-8")


def build_control_inventory(release_id, files):
    """files: mapping relative name -> bytes. Returns normalized inventory bytes."""
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ContextError("Invalid release_id: " + release_id)
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "files": {name: sha256_bytes(data) for name, data in sorted(files.items())},
    }
    return normalize_json(inventory)


def initialize_installation_root(root, *, launcher, interpreter, release_id, release_files,
                                 profile, policy_documents):
    """Create a fresh, disposable protected installation root.

    ``launcher``: launcher script bytes installed as ``bootstrap/pf``.
    ``interpreter``: canonical absolute interpreter path written to bootstrap.conf.
    ``release_files``: mapping relative name -> bytes for ``releases/<release_id>/``.
    ``profile``: (file name, bytes) for ``profiles/``.
    ``policy_documents``: mapping file name -> bytes for ``policies/``.
    Refuses an existing non-empty root. Trusted-installer/fixture primitive, not an operator wizard.
    """
    root = Path(root)
    if not root.is_absolute():
        raise ContextError("Installation root must be absolute.")
    if os.path.lexists(str(root)) and os.listdir(str(root)):
        raise ContextError("Refusing to initialize a non-empty installation root: " + str(root))
    if not os.path.lexists(str(root)):
        _create_private_dir(root, 0o755)
    else:
        os.chmod(str(root), 0o755)
    for name in (BOOTSTRAP_DIR, "registry", "locks", "instances", "releases", "profiles", "policies"):
        _create_private_dir(root / name, 0o700)
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ContextError("Invalid release_id: " + release_id)
    release_dir = root / "releases" / release_id
    _create_private_dir(release_dir, 0o700)
    for name, data in release_files.items():
        if "/" in name or name.startswith(".") or name == CONTROL_INVENTORY_NAME:
            raise ContextError("Release files must be flat, visible names: " + name)
        _write_private_file(release_dir / name, data, 0o600)
    _write_private_file(release_dir / CONTROL_INVENTORY_NAME, build_control_inventory(release_id, release_files), 0o600)
    _write_private_file(root / BOOTSTRAP_DIR / LAUNCHER_NAME, launcher, 0o700)
    _write_private_file(root / BOOTSTRAP_DIR / BOOTSTRAP_CONF_NAME,
                        render_bootstrap_conf(interpreter, str(release_dir)), 0o600)
    _write_private_file(registry_path(root), normalize_json(
        {"schema_version": SCHEMA_VERSION, "default_instance_id": None, "instances": []}), 0o600)
    _create_lock_file(root / REGISTRY_LOCK_RELATIVE)
    profile_name, profile_bytes = profile
    _write_private_file(root / "profiles" / profile_name, profile_bytes, 0o600)
    for name, data in policy_documents.items():
        _write_private_file(root / "policies" / name, data, 0o600)
    _fsync_directory(root)
    return {
        "root": root,
        "release_dir": release_dir,
        "profile_path": root / "profiles" / profile_name,
        "policy_paths": {name: root / "policies" / name for name in policy_documents},
    }


def _write_registry(root, document):
    default, entries = _validate_registry_document(root, document, "registry update")
    _write_private_file(registry_path(root), normalize_json(document), 0o600)
    return default, entries


IDENTITY_FIELDS = ("slug", "compose_project", "approved_environment", "daemon", "control", "profile",
                   "approved_policy")
DATA_PATH_FIELDS = ("workspace", "configuration", "backups", "recovery")


def _same_registration_identity(existing, record):
    if not isinstance(existing, dict) or not isinstance(existing.get("paths"), dict):
        return False
    if any(existing.get(field) != record[field] for field in IDENTITY_FIELDS):
        return False
    return all(existing["paths"].get(name) == record["paths"][name] for name in DATA_PATH_FIELDS)


def register_instance(root, spec):
    """Minimal registration transaction for a fresh disposable installation.

    ``spec`` keys: slug, compose_project, approved_environment, daemon {endpoint, engine_id},
    paths {workspace, configuration, backups, recovery}, control_release_id, profile_path,
    policy_path and optional instance_id. Steps: preflight all records/paths, reserve identity
    under the registry lock, create the private record + stable instance lock, atomically publish
    the registration. Never changes the default, live containers or credentials.
    """
    root = Path(root)
    checker = validate_installation_root(root)
    if checker.blocking():
        raise ContextError("Installation root is not trusted; registration refused:\n" + "\n".join(checker.blocking()))
    unknown = set(spec) - {"slug", "compose_project", "approved_environment", "daemon", "paths",
                           "control_release_id", "profile_path", "policy_path", "instance_id"}
    if unknown:
        raise ContextError("Unknown registration fields: " + ", ".join(sorted(unknown)))
    release_id = spec["control_release_id"]
    if not RELEASE_ID_RE.fullmatch(str(release_id)):
        raise ContextError("Invalid control_release_id.")
    release_dir = root / "releases" / release_id
    inventory = validate_release(checker, release_dir, None, release_id)
    if inventory is None or checker.blocking():
        raise ContextError("Control release is not trusted; registration refused:\n" + "\n".join(checker.blocking()))
    _, inventory_sha = load_control_inventory(release_dir)
    profile_path = Path(spec["profile_path"])
    policy_path = Path(spec["policy_path"])
    if profile_path.parent != root / "profiles" or policy_path.parent != root / "policies":
        raise ContextError("Profile and policy must live under <root>/profiles and <root>/policies.")
    profile_bytes = read_bytes_nofollow(profile_path)
    policy_bytes = read_bytes_nofollow(policy_path)
    profile_document = parse_strict_json(profile_bytes, label=str(profile_path))
    policy_document = parse_strict_json(policy_bytes, label=str(policy_path))
    instance_id = spec.get("instance_id") or str(uuid.uuid4())
    daemon = dict(spec["daemon"])
    daemon.setdefault("scope", "local")
    daemon.setdefault("rootless", False)
    record = {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "slug": spec["slug"],
        "state": "registered",
        "record_revision": 1,
        "compose_project": spec["compose_project"],
        "approved_environment": spec["approved_environment"],
        "daemon": daemon,
        "paths": {
            "workspace": str(spec["paths"]["workspace"]),
            "configuration": str(spec["paths"]["configuration"]),
            "backups": str(spec["paths"]["backups"]),
            "recovery": str(spec["paths"]["recovery"]),
            "private_state": str(root / "instances" / instance_id),
        },
        "control": {"release_id": release_id, "path": str(release_dir), "sha256": inventory_sha},
        "profile": {
            "id": profile_document.get("id") if isinstance(profile_document, dict) else None,
            "version": profile_document.get("version") if isinstance(profile_document, dict) else None,
            "path": str(profile_path), "sha256": sha256_bytes(profile_bytes),
        },
        "approved_policy": {
            "revision": policy_document.get("revision") if isinstance(policy_document, dict) else None,
            "path": str(policy_path), "sha256": sha256_bytes(policy_bytes),
        },
        "approved_config_revision": 0,
    }
    record_bytes = normalize_json(record)
    context = context_from_record(root, record, root / "instances" / instance_id / "record.json",
                                  sha256_bytes(record_bytes))
    # Profile/policy semantics and registered data paths are checked before anything is written.
    load_profile(context)
    load_policy(context)
    if context.approved_environment not in SUPPORTED_ENVIRONMENTS:
        raise ContextError(f"Environment {context.approved_environment!r} is not enabled in A1.")
    preflight = _Checker(root)
    preflight.root_dev = None
    for name in ("workspace", "configuration"):
        _data_directory(preflight, getattr(context.paths, name), name, protected=False)
    for name in ("backups", "recovery"):
        _data_directory(preflight, getattr(context.paths, name), name, protected=True)
    _overlap_checks(preflight, context)
    seen = set()
    for name in ("workspace", "configuration", "backups", "recovery"):
        value = getattr(context.paths, name)
        if value in seen:
            preflight.refuse("path-duplicate", value, f"{name} duplicates another registered path")
        seen.add(value)
    if preflight.findings:
        raise ContextError("Registered paths are not acceptable; registration refused:\n"
                           + "\n".join(f.render() for f in preflight.findings))

    with acquire_registry_lock(root):
        registry = load_registry(root)
        if registry.entry(instance_id) is not None:
            raise ContextError(f"instance_id {instance_id} is already registered.")
        if any(entry.slug == context.slug for entry in registry.entries):
            raise ContextError(f"slug {context.slug!r} is already registered.")
        for entry, other, error in registry.records():
            if other is None:
                raise ContextError(f"Registered record {entry.slug} is invalid ({error}); fix it before registering.")
            if (other.daemon.engine_id, other.compose_project) == (context.daemon.engine_id, context.compose_project) \
                    and other.state != "purged":
                raise ContextError(
                    f"Compose project {context.compose_project!r} is already registered on daemon "
                    f"{context.daemon.engine_id} by instance {other.slug}."
                )
            for name in ("workspace", "configuration", "backups", "recovery"):
                if getattr(other.paths, name) == getattr(context.paths, name):
                    raise ContextError(f"{name} path is already registered by instance {other.slug}.")
        private_state = context.paths.private_state
        resumed = False
        for orphan in unpublished_registrations(registry):
            try:
                existing = parse_strict_json(read_bytes_nofollow(orphan / "record.json"), label=str(orphan))
            except (OSError, ContextError) as exc:
                raise ContextError(f"Unpublished registration {orphan.name} is unreadable ({exc}); "
                                   "an administrator must remove it explicitly.") from exc
            same_identity = _same_registration_identity(existing, record)
            if (isinstance(existing, dict) and existing.get("slug") == context.slug) or orphan.name == instance_id:
                if not same_identity:
                    raise ContextError(f"Unpublished registration {orphan.name} conflicts with this request; "
                                       "an administrator must remove it explicitly.")
                # Complete the interrupted registration with its original identity.
                instance_id = orphan.name
                record = dict(existing)
                record_bytes = normalize_json(record)
                private_state = orphan
                context = context_from_record(root, record, private_state / "record.json", sha256_bytes(record_bytes))
                resumed = True
                break
        if not resumed:
            _create_private_dir(private_state, 0o700)
            _write_private_file(private_state / "record.json", record_bytes, 0o600)
            _create_private_dir(private_state / STATE_SUBDIR, 0o700)
        else:
            if not (private_state / STATE_SUBDIR).is_dir():
                _create_private_dir(private_state / STATE_SUBDIR, 0o700)
        lock_path = root / "locks" / (instance_id + ".lock")
        if not os.path.lexists(str(lock_path)):
            _create_lock_file(lock_path)
        _fsync_directory(private_state)
        document = {
            "schema_version": SCHEMA_VERSION,
            "default_instance_id": registry.default_instance_id,
            "instances": [
                {"instance_id": entry.instance_id, "slug": entry.slug, "record_path": str(entry.record_path)}
                for entry in registry.entries
            ] + [{"instance_id": instance_id, "slug": context.slug, "record_path": str(private_state / 'record.json')}],
        }
        _write_registry(root, document)
    return load_registry(root).load_record(RegistryEntry(instance_id, context.slug, private_state / "record.json"))


def set_default_instance(root, instance_id):
    """Explicit default change under the registry lock. Registration never calls this."""
    root = Path(root)
    with acquire_registry_lock(root):
        registry = load_registry(root)
        if instance_id is not None and registry.entry(instance_id) is None:
            raise ContextError(f"{instance_id} is not a registered instance; default unchanged.")
        document = {
            "schema_version": SCHEMA_VERSION,
            "default_instance_id": instance_id,
            "instances": [
                {"instance_id": entry.instance_id, "slug": entry.slug, "record_path": str(entry.record_path)}
                for entry in registry.entries
            ],
        }
        _write_registry(root, document)
    return load_registry(root)
