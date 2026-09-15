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
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid


def _load_sibling_module(name):
    """Import a module from this file's own directory by absolute path (no sys.path search)."""
    if name in sys.modules and getattr(sys.modules[name], "__file__", None) == str(Path(__file__).resolve().parent / (name + ".py")):
        return sys.modules[name]
    path = Path(__file__).resolve().parent / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load control module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Shared trust primitives live in the bootstrap-owned module. The installed
# bootstrap copy verified this release (including its own sibling copy) before
# this file was imported; see pf_bootstrap.py.
pf_bootstrap = _load_sibling_module("pf_bootstrap")
Finding = pf_bootstrap.Finding
AclState = pf_bootstrap.AclState
PathChecker = pf_bootstrap.PathChecker
inspect_posix_acl = pf_bootstrap.inspect_posix_acl
parse_posix_acl = pf_bootstrap.parse_posix_acl
sha256_bytes = pf_bootstrap.sha256_bytes
read_bytes_nofollow = pf_bootstrap.read_bytes_nofollow
TRUSTED_UID = pf_bootstrap.TRUSTED_UID
CONTROL_INVENTORY_NAME = pf_bootstrap.CONTROL_INVENTORY_NAME
BOOTSTRAP_DIR = pf_bootstrap.BOOTSTRAP_DIR
LAUNCHER_NAME = pf_bootstrap.LAUNCHER_NAME
BOOTSTRAP_CONF_NAME = pf_bootstrap.BOOTSTRAP_CONF_NAME
BOOTSTRAP_MODULE_NAME = pf_bootstrap.BOOTSTRAP_MODULE_NAME
RELEASE_ID_RE = pf_bootstrap.RELEASE_ID_RE

SCHEMA_VERSION = 1
REGISTRY_RELATIVE = Path("registry") / "instances.json"
REGISTRY_LOCK_RELATIVE = Path("locks") / "registry.lock"
RESERVATIONS_RELATIVE = Path("registry") / "reservations"
STAGING_RELATIVE = Path("staging")
ROLE_NAMES = ("workspace", "configuration", "backups", "recovery")
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


def parse_strict_json(data, *, label):
    """Parse UTF-8 JSON, rejecting duplicate keys and non-finite numbers."""
    return pf_bootstrap.parse_strict_json(data, label=label, error=ContextError)


def normalize_json(value):
    """Normalized record bytes: sorted keys, UTF-8, no whitespace, no NaN."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


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
class ContextValidation:
    context: InstanceContext
    findings: tuple
    acl_state: str
    private_state_trusted: bool = True

    @property
    def mutation_allowed(self):
        return not any(finding.severity == "refuse" for finding in self.findings)

    def blocking_messages(self):
        return [finding.render() for finding in self.findings if finding.severity == "refuse"]

    def refused_codes(self):
        return sorted({finding.code for finding in self.findings if finding.severity == "refuse"})


def _checker(root, checker=None):
    return checker if checker is not None else PathChecker(root)


def validate_installation_root(root, checker=None, *, interpreter=None):
    """Read-only trust checks for the installation root, bootstrap, interpreter and pinned release.

    Delegates the pre-launch anchor checks to pf_bootstrap (the same code the
    installed launcher ran before this release was imported) and adds the
    registry, lock, reservation, staging, instance, profile and policy containers.
    """
    root = Path(root)
    checker = _checker(root, checker)
    pf_bootstrap.verify_installation_anchor(root, interpreter=interpreter, checker=checker)
    if checker.root_dev is None:
        return checker
    for relative in ("registry", REGISTRY_RELATIVE.parent / "reservations", "locks", "instances", "releases",
                     "profiles", "policies", STAGING_RELATIVE):
        checker.protected(root / relative, kind="dir")
    checker.protected(registry_path(root), kind="file")
    checker.protected(root / REGISTRY_LOCK_RELATIVE, kind="file")
    return checker


def path_identity(path):
    """No-follow component walk. Returns (st_dev, st_ino) of an existing directory leaf.

    Raises ContextError for symbolic-link components (aliases are not accepted
    as managed-path identity), for a leaf that is not a directory, and for a
    missing leaf. ``Path.resolve()`` alone is not security validation.
    """
    path = Path(path)
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise ContextError(f"{path}: managed paths must be normalized absolute paths")
    current = Path(path.anchor)
    info = _managed_directory_info(current)
    for part in path.parts[1:]:
        current = current / part
        info = _managed_directory_info(current)
    return (info.st_dev, info.st_ino)


def _managed_directory_info(current):
    """lstat one component of a managed path; it must be a real directory, not a symlink."""
    try:
        info = os.lstat(str(current))
    except OSError as exc:
        raise ContextError(f"{current}: {exc.strerror or exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise ContextError(f"{current}: symbolic link in a managed path is not supported")
    if not stat.S_ISDIR(info.st_mode):
        raise ContextError(f"{current}: not a directory")
    return info


def _contains(outer, inner):
    return outer == inner or outer in inner.parents


def managed_path_conflicts(candidate, owner, others, root):
    """Global managed-path inventory check (A11-R03).

    ``candidate``: {role: Path} for one instance; ``others``: iterable of
    (owner, role, Path) for every other published record and pending
    reservation. Every candidate role is compared with the installation root,
    with the candidate's other roles and with every other owner's roles for
    equality and containment in both directions, and by inode identity when the
    directories exist. Returns a list of (code, path, message).
    """
    problems = []
    root = Path(root)
    entries = [(owner, role, Path(path)) for role, path in candidate.items()]
    identities = {}
    for _, role, path in entries:
        if _contains(root, path):
            problems.append(("path-inside-root", path, f"{role} must not live inside the installation root"))
        if _contains(path, root):
            problems.append(("root-inside-path", path, f"installation root must not live inside {role}"))
        try:
            identities[(owner, role)] = path_identity(path)
        except ContextError as exc:
            problems.append(("path-component", path, f"{role}: {exc}"))
    for index, (_, role_a, path_a) in enumerate(entries):
        for _, role_b, path_b in entries[index + 1:]:
            if path_a == path_b:
                problems.append(("path-duplicate", path_a, f"{role_a} and {role_b} are the same path"))
            elif _contains(path_a, path_b):
                problems.append(("path-nested", path_b, f"{role_b} lies inside {role_a}"))
            elif _contains(path_b, path_a):
                problems.append(("path-nested", path_a, f"{role_a} lies inside {role_b}"))
    for other_owner, other_role, other_path in others:
        other_path = Path(other_path)
        try:
            other_identity = path_identity(other_path)
        except ContextError:
            other_identity = None
        for _, role, path in entries:
            label = f"{role} vs {other_owner}.{other_role}"
            if path == other_path:
                problems.append(("path-duplicate", path, label + ": same path"))
            elif _contains(other_path, path):
                problems.append(("path-nested", path, label + ": lies inside the other instance's path"))
            elif _contains(path, other_path):
                problems.append(("path-nested", path, label + ": contains the other instance's path"))
            elif other_identity is not None and identities.get((owner, role)) == other_identity:
                problems.append(("path-alias", path, label + ": same directory inode through a different name"))
    seen = {}
    for key, identity in identities.items():
        if identity in seen and candidate[seen[identity][1]] != candidate[key[1]]:
            problems.append(("path-alias", candidate[key[1]], f"{key[1]} and {seen[identity][1]} are the same directory"))
        seen.setdefault(identity, key)
    return problems


def inventory_of(registry, *, exclude_instance_id=None):
    """(owner, role, path) for every loadable published record and every pending reservation."""
    entries = []
    for entry, context, error in registry.records():
        if context is None or entry.instance_id == exclude_instance_id:
            continue
        for role in ROLE_NAMES:
            entries.append((context.slug, role, getattr(context.paths, role)))
    for pending in pending_registrations(registry.root):
        if pending.record is None or pending.instance_id == exclude_instance_id:
            continue
        for role in ROLE_NAMES:
            entries.append(("reservation:" + pending.slug, role, Path(pending.record["paths"][role])))
    return entries


def validate_context(context, *, running_release=None, interpreter=None, checker=None, registry=None):
    """Context + installed trust anchor -> validation result. Read-only lstat/xattr/hash checks."""
    root = context.installation_root
    checker = _checker(root, checker)
    validate_installation_root(root, checker, interpreter=interpreter)
    conf = checker.bootstrap_conf
    # Control release pinned by this instance must be the release the bootstrap runs.
    if context.control.path.parent != root / "releases" or context.control.path.name != context.control.release_id:
        checker.refuse("control-path", context.control.path, "control path must be <root>/releases/<release_id>")
    else:
        if conf is not None and Path(conf["control_release"]) != context.control.path:
            checker.refuse("control-release-mismatch", context.control.path,
                           f"instance {context.slug} pins release {context.control.release_id}; the installed bootstrap "
                           f"runs {Path(conf['control_release']).name}. A control binding change is an explicit "
                           "installation transaction, not a runtime fallback")
        inventory = pf_bootstrap.verify_release(checker, context.control.path,
                                                expected_inventory_sha256=context.control.sha256,
                                                expected_release_id=context.control.release_id)
        if inventory is not None and PROFILE_COMPOSE_FILE not in inventory["files"]:
            checker.refuse("control-compose-missing", context.control.path,
                           f"installed control release does not list {PROFILE_COMPOSE_FILE}")
    if running_release is not None and Path(running_release) != context.control.path:
        checker.refuse("control-release-mismatch", running_release,
                       f"instance {context.slug} pins release {context.control.release_id}; running code is elsewhere")
    # Approved profile and policy bytes, inside their protected containers.
    for label, binding, expected_parent in (("profile", context.profile, root / "profiles"),
                                            ("policy", context.approved_policy, root / "policies")):
        if binding.path.parent != expected_parent:
            checker.refuse(label + "-location", binding.path, f"{label} must live directly under {expected_parent}")
    _validate_profile(checker, context)
    _validate_policy(checker, context)
    # Registration record, private state (including journal/state files) and stable lock.
    private_findings_before = len(checker.findings)
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
        # The state directory itself is private (0700); entries below it are covered by
        # owner/link/mode-writability/ACL checks and are shielded by the directory mode.
        if checker.protected(context.state_dir, kind="dir", allow_group_read=False) is not None:
            checker.protected_tree(context.state_dir, allow_group_read=True)
    else:
        checker.note("state-missing", context.state_dir, "no private runtime state yet (not created by diagnostics)")
    checker.protected(context.lock_path, kind="file", allow_group_read=False)
    private_state_trusted = not any(
        finding.severity == "refuse" for finding in checker.findings[private_findings_before:]
    )
    # Registered data paths: workspace/configuration are editor-writable leaves
    # in protected locations; backups/recovery are protected authoritative storage.
    for name in ("workspace", "configuration"):
        _data_directory(checker, getattr(context.paths, name), name, protected=False)
    for name in ("backups", "recovery"):
        _data_directory(checker, getattr(context.paths, name), name, protected=True)
    # Global managed-path inventory: this instance against every other registration and reservation.
    if registry is None:
        try:
            registry = load_registry(root)
        except ContextError as exc:
            checker.refuse("registry-invalid", registry_path(root), str(exc))
    if registry is not None:
        candidate = {role: getattr(context.paths, role) for role in ROLE_NAMES}
        others = inventory_of(registry, exclude_instance_id=context.instance_id)
        for code, path, message in managed_path_conflicts(candidate, context.slug, others, root):
            checker.refuse(code, path, message)
    acl_state = "posix" if "posix" in checker.acl_states else "none"
    if "unknown" in checker.acl_states:
        acl_state = "unknown"
    return ContextValidation(context=context, findings=tuple(checker.findings), acl_state=acl_state,
                             private_state_trusted=private_state_trusted)


def _data_directory(checker, path, name, *, protected):
    """A registered data directory: protected ancestors always; the leaf per its role."""
    path = Path(path)
    if not checker.ancestors(path):
        return
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
    if info.st_uid != TRUSTED_UID:
        checker.refuse("untrusted-owner", path, f"{name}: owner uid {info.st_uid}; trusted owner is uid {TRUSTED_UID}")
    if protected:
        if pf_bootstrap.writable_by_others(info.st_mode):
            checker.refuse("storage-replaceable", path,
                           f"{name}: mode {oct(stat.S_IMODE(info.st_mode))}; backup/recovery storage must not be "
                           "group/world writable")
        checker.acl(path)
    else:
        # Editor-writable by design (SMB group access); the location is protected by the ancestor walk.
        checker.acl(path, editor_writable=True)


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
                or pf_bootstrap.writable_by_others(info.st_mode):
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


def render_bootstrap_conf(interpreter, control_release, control_release_sha256):
    return (
        "# Deployment Admin bootstrap configuration. Data only; never sourced by a shell.\n"
        f"interpreter={interpreter}\n"
        f"control_release={control_release}\n"
        f"control_release_sha256={control_release_sha256}\n"
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
    ``release_files``: mapping relative name -> bytes for ``releases/<release_id>/``; must include
    the required control files. The bootstrap copy of ``pf_bootstrap.py`` is taken from it.
    ``profile``: (file name, bytes) for ``profiles/``.
    ``policy_documents``: mapping file name -> bytes for ``policies/``.
    Refuses an existing non-empty root. Trusted-installer/fixture primitive, not an operator wizard.
    """
    root = Path(root)
    if not root.is_absolute():
        raise ContextError("Installation root must be absolute.")
    if os.path.lexists(str(root)) and os.listdir(str(root)):
        raise ContextError("Refusing to initialize a non-empty installation root: " + str(root))
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ContextError("Invalid release_id: " + release_id)
    missing = [name for name in pf_bootstrap.REQUIRED_RELEASE_FILES if name not in release_files]
    if missing:
        raise ContextError("Release files missing required control files: " + ", ".join(missing))
    if not os.path.lexists(str(root)):
        _create_private_dir(root, 0o755)
    else:
        os.chmod(str(root), 0o755)
    for relative in (BOOTSTRAP_DIR, "registry", RESERVATIONS_RELATIVE, "locks", "instances", "releases",
                     "profiles", "policies", STAGING_RELATIVE):
        _create_private_dir(root / relative, 0o700)
    release_dir = root / "releases" / release_id
    _create_private_dir(release_dir, 0o700)
    for name, data in release_files.items():
        if "/" in name or name.startswith(".") or name == CONTROL_INVENTORY_NAME:
            raise ContextError("Release files must be flat, visible names: " + name)
        _write_private_file(release_dir / name, data, 0o600)
    inventory_bytes = build_control_inventory(release_id, release_files)
    _write_private_file(release_dir / CONTROL_INVENTORY_NAME, inventory_bytes, 0o600)
    _write_private_file(root / BOOTSTRAP_DIR / LAUNCHER_NAME, launcher, 0o700)
    _write_private_file(root / BOOTSTRAP_DIR / BOOTSTRAP_MODULE_NAME, release_files[BOOTSTRAP_MODULE_NAME], 0o600)
    _write_private_file(root / BOOTSTRAP_DIR / BOOTSTRAP_CONF_NAME,
                        render_bootstrap_conf(interpreter, str(release_dir), sha256_bytes(inventory_bytes)), 0o600)
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


def _registry_document(registry, extra_entries=(), default=None, keep_default=True):
    return {
        "schema_version": SCHEMA_VERSION,
        "default_instance_id": registry.default_instance_id if keep_default else default,
        "instances": [
            {"instance_id": entry.instance_id, "slug": entry.slug, "record_path": str(entry.record_path)}
            for entry in registry.entries
        ] + list(extra_entries),
    }


# ----------------------------------------------------- reservations and staging


@dataclasses.dataclass(frozen=True)
class PendingRegistration:
    """A durable reservation (registry/reservations/<uuid>.json) or an instance directory in unknown state."""

    kind: str  # "reservation" | "unknown-instance-directory" | "unknown-staging-directory"
    path: Path
    instance_id: str
    slug: str = ""
    record: object = None
    error: str = ""


def pending_registrations(root):
    """Every reservation and every unexplained directory. Read-only; nothing is repaired or deleted."""
    root = Path(root)
    pending = []
    reservations = root / RESERVATIONS_RELATIVE
    try:
        names = sorted(os.listdir(reservations))
    except OSError:
        names = []
    reserved_ids = set()
    for name in names:
        path = reservations / name
        if not name.endswith(".json") or not _anchored_fullmatch(UUID_PATTERN, name[:-5]):
            pending.append(PendingRegistration("reservation", path, name, error="unexpected file in reservations/"))
            continue
        instance_id = name[:-5]
        reserved_ids.add(instance_id)
        try:
            record = parse_strict_json(read_bytes_nofollow(path), label=str(path))
            validate_instance_record(record)
            if record["instance_id"] != instance_id:
                raise ContextError("reservation file name does not match its instance_id")
            pending.append(PendingRegistration("reservation", path, instance_id, record["slug"], record))
        except (OSError, ContextError) as exc:
            pending.append(PendingRegistration("reservation", path, instance_id, error=str(exc)))
    try:
        registry = load_registry(root)
        published = {entry.instance_id for entry in registry.entries}
    except ContextError:
        published = set()
    instances_root = root / "instances"
    try:
        names = sorted(os.listdir(instances_root))
    except OSError:
        names = []
    for name in names:
        if name in published or name in reserved_ids:
            continue
        pending.append(PendingRegistration("unknown-instance-directory", instances_root / name, name,
                                           error="instance directory without registry entry or reservation"))
    staging = root / STAGING_RELATIVE
    try:
        names = sorted(os.listdir(staging))
    except OSError:
        names = []
    for name in names:
        owner = name.split(".", 1)[0]
        if owner in reserved_ids:
            continue
        pending.append(PendingRegistration("unknown-staging-directory", staging / name, owner,
                                           error="staging directory without a matching reservation"))
    return pending


def unpublished_registrations(registry):
    """Compatibility view: paths of pending reservations and unexplained directories."""
    return [pending.path for pending in pending_registrations(registry.root)]


def _same_registration_identity(existing, record):
    if not isinstance(existing, dict) or not isinstance(existing.get("paths"), dict):
        return False
    for field in ("slug", "compose_project", "approved_environment", "daemon", "control", "profile",
                  "approved_policy"):
        if existing.get(field) != record[field]:
            return False
    return all(existing["paths"].get(name) == record["paths"][name] for name in ROLE_NAMES)


def _write_reservation(root, record):
    """Durable intent: the complete proposed record, before any instance directory exists."""
    path = Path(root) / RESERVATIONS_RELATIVE / (record["instance_id"] + ".json")
    _write_private_file(path, normalize_json(record), 0o600)
    return path


def _clear_reservation(root, instance_id):
    path = Path(root) / RESERVATIONS_RELATIVE / (instance_id + ".json")
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _stage_instance_dir(root, record):
    """Build instances/<uuid> completely under staging/, then publish it with one atomic rename."""
    root = Path(root)
    instance_id = record["instance_id"]
    staging_root = root / STAGING_RELATIVE
    # Leftovers from an earlier attempt of this same reservation are ours to discard.
    for name in os.listdir(staging_root):
        if name.split(".", 1)[0] == instance_id:
            _remove_tree(staging_root / name)
    staged = staging_root / (instance_id + "." + uuid.uuid4().hex[:8])
    _create_private_dir(staged, 0o700)
    _write_private_file(staged / "record.json", normalize_json(record), 0o600)
    _create_private_dir(staged / STATE_SUBDIR, 0o700)
    _fsync_directory(staged / STATE_SUBDIR)
    _fsync_directory(staged)
    _publish_instance_dir(staged, root / "instances" / instance_id)


def _publish_instance_dir(staged, target):
    os.rename(str(staged), str(target))
    _fsync_directory(Path(target).parent)


def _remove_tree(path):
    """Remove a staging tree this transaction created. Never used on published or unknown state."""
    for current, dirs, files in os.walk(str(path), topdown=False, followlinks=False):
        for name in files:
            os.unlink(os.path.join(current, name))
        for name in dirs:
            os.rmdir(os.path.join(current, name))
    os.rmdir(str(path))


def _existing_instance_dir_state(root, record):
    """None when absent; 'published-record' when record.json matches; raises for unknown state."""
    target = Path(root) / "instances" / record["instance_id"]
    if not os.path.lexists(str(target)):
        return None
    record_path = target / "record.json"
    try:
        existing = read_bytes_nofollow(record_path)
    except FileNotFoundError as exc:
        raise ContextError(
            f"Instance directory {target} exists without record.json and is not explained by the reservation "
            "protocol; an administrator must inspect it. Nothing was changed."
        ) from exc
    if existing != normalize_json(record):
        raise ContextError(
            f"Instance directory {target} holds a record that differs from the reserved identity; "
            "an administrator must inspect it. Nothing was changed."
        )
    return "published-record"


def register_instance(root, spec):
    """Minimal registration transaction for a fresh disposable installation (A11-R04 protocol).

    Steps, all under the registry lock:
      1. preflight: trusted root/release, profile/policy semantics, registered paths, global
         managed-path inventory against published records and pending reservations;
      2. reserve: write registry/reservations/<uuid>.json (the complete record) before any
         instance directory exists — this holds UUID/slug/daemon-project/paths durably;
      3. stage instances/<uuid> under staging/ and publish it with one atomic rename;
      4. create the stable lock (kept if it already exists);
      5. publish the registry entry atomically; the default is never changed;
      6. clear the reservation.
    A retry with the same request converges on the same UUID from whichever step failed,
    including a publication that completed before the caller saw success. Unknown state
    (directories not explained by a reservation) is reported, never deleted.
    """
    root = Path(root)
    checker = validate_installation_root(root)
    if checker.blocking():
        raise ContextError("Installation root is not trusted; registration refused:\n" + "\n".join(checker.blocking()))
    unknown = set(spec) - {"slug", "compose_project", "approved_environment", "daemon", "paths",
                           "control_release_id", "profile_path", "policy_path", "instance_id"}
    if unknown:
        raise ContextError("Unknown registration fields: " + ", ".join(sorted(unknown)))
    release_id = str(spec["control_release_id"])
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ContextError("Invalid control_release_id.")
    release_dir = root / "releases" / release_id
    conf = checker.bootstrap_conf
    if conf is None or Path(conf["control_release"]) != release_dir:
        raise ContextError("Registration must pin the control release installed by the bootstrap: "
                           + str(conf["control_release"] if conf else "<no bootstrap configuration>"))
    _, inventory_sha = pf_bootstrap.load_control_inventory(release_dir, error=ContextError)
    profile_path = Path(spec["profile_path"])
    policy_path = Path(spec["policy_path"])
    if profile_path.parent != root / "profiles" or policy_path.parent != root / "policies":
        raise ContextError("Profile and policy must live under <root>/profiles and <root>/policies.")
    profile_bytes = read_bytes_nofollow(profile_path)
    policy_bytes = read_bytes_nofollow(policy_path)
    profile_document = parse_strict_json(profile_bytes, label=str(profile_path))
    policy_document = parse_strict_json(policy_bytes, label=str(policy_path))
    daemon = dict(spec["daemon"])
    daemon.setdefault("scope", "local")
    daemon.setdefault("rootless", False)

    def build_record(instance_id):
        return {
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

    requested_id = spec.get("instance_id")
    record = build_record(requested_id or str(uuid.uuid4()))
    context = context_from_record(root, record, root / "instances" / record["instance_id"] / "record.json",
                                  sha256_bytes(normalize_json(record)))
    load_profile(context)
    load_policy(context)
    if context.approved_environment not in SUPPORTED_ENVIRONMENTS:
        raise ContextError(f"Environment {context.approved_environment!r} is not enabled in A1.")
    preflight = PathChecker(root)
    for name in ("workspace", "configuration"):
        _data_directory(preflight, getattr(context.paths, name), name, protected=False)
    for name in ("backups", "recovery"):
        _data_directory(preflight, getattr(context.paths, name), name, protected=True)
    if preflight.blocking():
        raise ContextError("Registered paths are not acceptable; registration refused:\n"
                           + "\n".join(preflight.blocking()))

    with acquire_registry_lock(root):
        registry = load_registry(root)
        candidate_paths = {role: getattr(context.paths, role) for role in ROLE_NAMES}
        # 1a. Idempotent success: the same request is already published.
        for entry, other, error in registry.records():
            if other is None:
                raise ContextError(f"Registered record {entry.slug} is invalid ({error}); fix it before registering.")
            if other.slug == context.slug:
                published_record = parse_strict_json(read_bytes_nofollow(other.record_path), label=str(other.record_path))
                if _same_registration_identity(published_record, record) and (requested_id in (None, other.instance_id)):
                    _clear_reservation(root, other.instance_id)
                    return other
                raise ContextError(f"slug {context.slug!r} is already registered.")
        # 1b. Resume a durable reservation for the same request; refuse a conflicting one.
        pending = pending_registrations(root)
        for item in pending:
            if item.kind != "reservation":
                continue
            if item.record is None:
                raise ContextError(f"Reservation {item.path} is unreadable ({item.error}); an administrator must "
                                   "inspect it explicitly. Nothing was changed.")
            same_slug = item.slug == context.slug
            same_id = item.instance_id == record["instance_id"]
            if same_slug or same_id:
                if not _same_registration_identity(item.record, record) or (requested_id and requested_id != item.instance_id):
                    raise ContextError(f"Reservation {item.instance_id} for slug {item.slug!r} conflicts with this "
                                       "request; an administrator must remove it explicitly. Nothing was changed.")
                record = dict(item.record)
                context = context_from_record(root, record, root / "instances" / record["instance_id"] / "record.json",
                                              sha256_bytes(normalize_json(record)))
                break
        # 2. Collision checks against published records and every other reservation.
        reserved_ids = {entry.instance_id for entry in registry.entries}
        for entry, other, _ in registry.records():
            if other is None:
                continue
            if (other.daemon.engine_id, other.compose_project) == (context.daemon.engine_id, context.compose_project) \
                    and other.state != "purged":
                raise ContextError(
                    f"Compose project {context.compose_project!r} is already registered on daemon "
                    f"{context.daemon.engine_id} by instance {other.slug}."
                )
        for item in pending:
            if item.kind != "reservation" or item.record is None or item.instance_id == record["instance_id"]:
                continue
            reserved_ids.add(item.instance_id)
            if item.slug == context.slug:
                raise ContextError(f"slug {context.slug!r} is reserved by pending registration {item.instance_id}.")
            if (item.record["daemon"]["engine_id"], item.record["compose_project"]) == \
                    (context.daemon.engine_id, context.compose_project):
                raise ContextError(
                    f"Compose project {context.compose_project!r} on daemon {context.daemon.engine_id} is reserved "
                    f"by pending registration {item.instance_id}."
                )
        if record["instance_id"] in reserved_ids:
            raise ContextError(f"instance_id {record['instance_id']} is already registered or reserved.")
        others = inventory_of(registry, exclude_instance_id=record["instance_id"])
        problems = managed_path_conflicts(candidate_paths, context.slug, others, root)
        if problems:
            raise ContextError("Managed paths overlap or alias other managed paths; registration refused:\n"
                               + "\n".join(f"[refuse] {code}: {path}: {message}" for code, path, message in problems))
        for item in pending:
            if item.kind != "reservation" and item.instance_id == record["instance_id"]:
                raise ContextError(f"{item.path}: {item.error}; an administrator must inspect it. Nothing was changed.")
        # 3. Durable reservation before any instance directory exists.
        reservation_path = root / RESERVATIONS_RELATIVE / (record["instance_id"] + ".json")
        if not reservation_path.is_file():
            _write_reservation(root, record)
        # 4. Instance directory: staged and published atomically, or already there from an earlier attempt.
        if _existing_instance_dir_state(root, record) is None:
            _stage_instance_dir(root, record)
        # 5. Stable lock: created once, kept forever.
        lock_path = root / "locks" / (record["instance_id"] + ".lock")
        if not os.path.lexists(str(lock_path)):
            _create_lock_file(lock_path)
        # 6. Registry publication (atomic replace); the default is untouched.
        private_state = root / "instances" / record["instance_id"]
        if registry.entry(record["instance_id"]) is None:
            _write_registry(root, _registry_document(registry, [{
                "instance_id": record["instance_id"], "slug": context.slug, "record_path": str(private_state / "record.json"),
            }]))
        # 7. The reservation has done its job.
        _clear_reservation(root, record["instance_id"])
    return load_registry(root).load_record(RegistryEntry(record["instance_id"], context.slug, private_state / "record.json"))


def set_default_instance(root, instance_id):
    """Explicit default change under the registry lock. Registration never calls this."""
    root = Path(root)
    with acquire_registry_lock(root):
        registry = load_registry(root)
        if instance_id is not None and registry.entry(instance_id) is None:
            raise ContextError(f"{instance_id} is not a registered instance; default unchanged.")
        _write_registry(root, _registry_document(registry, default=instance_id, keep_default=False))
    return load_registry(root)
