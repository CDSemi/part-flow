"""Strict application ``.env`` parsing and the private frozen configuration snapshot (PF-A1.2).

The editable ``config/.env`` is a *proposal*. Parsing is data parsing only: no
``source``, no evaluation, no command substitution, no host-variable expansion.
Only the PartFlow A1 allowlist (ARCHITECTURE.md section 6) is accepted; unknown,
duplicate, missing, multiline and control-character entries are rejected before
any mutation. Accepted values are rendered into one private snapshot whose
literal round-trip is tested for ``$``, quotes, backslashes and URL credentials.
Existing values that cannot be rendered are an explicit migration issue; they are
never regenerated.

Python standard library only, Python 3.9 language baseline.
"""
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import sysconfig
import types
from typing import Optional
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


# PF-A2.1: the admin-config validator parses with the one strict JSON parser of the release.
pf_bootstrap = _load_sibling_module("pf_bootstrap")
# PF-A2.2: the A1 schema subset validator (no cycle: pf_instance never loads pf_config).
pf_instance = _load_sibling_module("pf_instance")

APP_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "SITE_TIMEZONE",
    "PARTFLOW_BIND_IP",
    "PARTFLOW_HTTP_PORT",
    "PARTFLOW_ALLOWED_HOST",
)
SECRET_KEYS = ("POSTGRES_PASSWORD",)
# Core-generated keys: never read from the editable file, always derived by the adapter.
# DEPLOY_ADMIN_INSTANCE_ID (PF-A1.3) is the protected instance UUID; Compose stamps it as the
# ownership label of every resource it creates, so it never comes from the editable file.
GENERATED_KEYS = ("PARTFLOW_REPO_ROOT", "PARTFLOW_DATABASE_URL", "DEPLOY_ADMIN_INSTANCE_ID")
KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
UNQUOTED_VALUE_RE = re.compile(r"[^\s'\"#]+\Z")
SNAPSHOT_FILE = "app.env"
SNAPSHOT_RECORD = "frozen-config.json"
SCHEMA_VERSION = 1


class ConfigError(RuntimeError):
    """A rejected proposal. Nothing was written or changed."""


# ------------------------------------------------------------ admin configuration (PF-A2.1, PF-A2.2)

ADMIN_CONFIG_SCHEMA_VERSION = 2
ADMIN_CONFIG_KEYS = ("repository", "branch", "project", "environment", "release_channel", "auto_update",
                     "ci_workflow", "health_timeout_seconds", "minimum_free_mb", "backup_read_group",
                     "workspace_write_group")
# The editable ``config/pf-config.json`` keys and their defaults (formerly pf-admin.py DEFAULTS).
# PF-A2.2: frozen schema 1 implicit values; never edit. A file without schema_version reads them for every
# omitted key, and `pf config admin` materializes exactly these values on migration (never the example's).
ADMIN_CONFIG_DEFAULTS = {
    "repository": "CDSemi/part-flow", "branch": "main",
    "project": "partflow-staging", "environment": "staging",
    "release_channel": "stable", "auto_update": False,
    "ci_workflow": "ci.yml", "health_timeout_seconds": 180,
    "minimum_free_mb": 2048,
    # Revision checkpoints and purge recovery bundles can contain database data.
    # Purge recovery also contains the external runtime .env. Keep these artifacts
    # read-only to the configured DSM group so they can be copied over SMB safely.
    "backup_read_group": "users",
    "workspace_write_group": "users",
}


_GROUP_PATTERN = ("^[^\\u0000-\\u001f\\u007f:\\s](?:[^\\u0000-\\u001f\\u007f:]{0,62}"
                  "[^\\u0000-\\u001f\\u007f:\\s])?$")
# Embedded copy of contracts/admin-config.schema.json (PF-A2.2 section 2.3); a test asserts the two stay identical.
# Flat document: the A1 subset validator (pf_instance.validate_against_schema) needs no markers.
ADMIN_CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin admin configuration v2 (PF-A2.2)",
    "description": ("Editable <configuration>/pf-config.json. Strict UTF-8 JSON: duplicate keys and non-finite "
                    "numbers are refused by the parser before this schema runs. Every key is required. A file "
                    "without schema_version is the legacy schema 1 form: it is read with the frozen schema 1 "
                    "implicit values and is only ever rewritten by an explicit 'pf config admin'. The environment "
                    "value is an editable label that must equal the approved policy environment; it never changes "
                    "the policy. Validated with the A1 subset validator (pf_instance.validate_against_schema)."),
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", *ADMIN_CONFIG_KEYS],
    "properties": {
        "schema_version": {"const": 2},
        "repository": {"const": "CDSemi/part-flow"},
        "branch": {"type": "string", "pattern": "^(?!.*\\.\\.)(?!.*//)[A-Za-z0-9][A-Za-z0-9._/-]{0,199}(?<!/)$"},
        "project": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,39}$"},
        "environment": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
        "release_channel": {"enum": ["stable", "prerelease"]},
        "auto_update": {"type": "boolean"},
        "ci_workflow": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"},
        "health_timeout_seconds": {"type": "integer", "minimum": 1},
        "minimum_free_mb": {"type": "integer", "minimum": 1},
        "backup_read_group": {"type": "string", "pattern": _GROUP_PATTERN},
        "workspace_write_group": {"type": "string", "pattern": _GROUP_PATTERN},
    },
}
# The operator wording of each schema 2 rule (section 3.2 step 5); pattern rules name their human rule.
_GROUP_RULE = "an existing group name of 1-64 characters without control characters, ':' or surrounding whitespace"
ADMIN_RULES = {
    "repository": 'must be "CDSemi/part-flow"',
    "branch": "does not match the schema 2 rule (a branch name of 1-200 characters A-Z a-z 0-9 . _ / - starting "
              "with a letter or digit, without '..', '//' or a trailing '/')",
    "project": "does not match the schema 2 rule (1-40 characters a-z 0-9 _ - starting with a letter or digit)",
    "environment": "does not match the schema 2 rule (1-32 characters a-z 0-9 - starting with a letter)",
    "release_channel": "must be one of stable, prerelease",
    "auto_update": "must be a JSON boolean",
    "ci_workflow": "does not match the schema 2 rule (a workflow file name of 1-100 characters A-Z a-z 0-9 . _ - "
                   "starting with a letter or digit)",
    "health_timeout_seconds": "must be a positive integer",
    "minimum_free_mb": "must be a positive integer",
    "backup_read_group": "does not match the schema 2 rule (" + _GROUP_RULE + ")",
    "workspace_write_group": "does not match the schema 2 rule (" + _GROUP_RULE + ")",
}


@dataclasses.dataclass(frozen=True)
class AdminConfig:
    """One parsed ``pf-config.json`` (PF-A2.2 section 3.2). Loading never migrates (INV-06)."""

    schema_version: int          # 0 (undetermined), 1 (legacy), 2
    values: Optional[dict]       # the 11 effective settings; None when problems
    implicit: tuple              # schema 1 keys taken from ADMIN_CONFIG_DEFAULTS
    problems: tuple              # ordered messages (section 3.2)
    code: Optional[str]          # None | "admin-config-invalid" | "admin-config-version-unsupported"
    declared_version: object = None   # the raw schema_version value of an unsupported file (copy only)


def _schema1_problems(supplied):
    """The A1/A2.1 rules and messages for a document without schema_version, in their order."""
    problems = []
    unknown = set(supplied) - set(ADMIN_CONFIG_DEFAULTS)
    if unknown:
        problems.append("Unknown configuration keys: " + ", ".join(sorted(unknown)))
    config = dict(ADMIN_CONFIG_DEFAULTS)
    config.update(supplied)
    if config["repository"] != "CDSemi/part-flow":
        problems.append("This controller is scoped to CDSemi/part-flow.")
    if not isinstance(config["project"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", config["project"]):
        problems.append("Invalid Compose project name.")
    if config["release_channel"] not in ("stable", "prerelease"):
        problems.append("release_channel must be stable or prerelease.")
    if type(config["auto_update"]) is not bool:
        problems.append("auto_update must be a JSON boolean.")
    for name in ("health_timeout_seconds", "minimum_free_mb"):
        if type(config[name]) is not int or config[name] <= 0:
            problems.append(f"{name} must be a positive integer.")
    for name in ("backup_read_group", "workspace_write_group"):
        if not isinstance(config[name], str) or not config[name].strip():
            problems.append(f"{name} must be a non-empty DSM group name.")
    return config, problems


def _key_problems(document):
    """``{key}: {rule}`` for every present ADMIN_CONFIG_KEYS value that fails its schema 2 property."""
    problems = []
    for key in ADMIN_CONFIG_KEYS:
        if key in document and pf_instance.validate_against_schema(document[key],
                                                                   ADMIN_CONFIG_SCHEMA["properties"][key], key):
            problems.append(f"{key}: {ADMIN_RULES[key]}")
    return problems


def _parse_admin(data, label):
    """(AdminConfig, schema 1 merged dict or None). The merged dict keeps the exact A2.1 return shape."""
    try:
        supplied = pf_bootstrap.parse_strict_json(data, label=label)
    except pf_bootstrap.BootstrapError as exc:
        return AdminConfig(0, None, (), (str(exc),), "admin-config-invalid"), None
    if not isinstance(supplied, dict):
        return AdminConfig(0, None, (), ("Runtime configuration must be a JSON object: " + label,),
                           "admin-config-invalid"), None
    if "schema_version" not in supplied:
        merged, problems = _schema1_problems(supplied)
        implicit = tuple(key for key in ADMIN_CONFIG_KEYS if key not in supplied)
        return AdminConfig(1, None if problems else merged, implicit, tuple(problems),
                           "admin-config-invalid" if problems else None), merged
    declared = supplied["schema_version"]
    if not (type(declared) is int and declared == ADMIN_CONFIG_SCHEMA_VERSION):
        return AdminConfig(0, None, (), (f"{label} declares schema_version {json.dumps(declared)}; this control "
                                         "reads schema 2 and the legacy form without schema_version.",),
                           "admin-config-version-unsupported", declared), None
    problems = []
    unknown = set(supplied) - {"schema_version", *ADMIN_CONFIG_KEYS}
    if unknown:
        problems.append("Unknown configuration keys: " + ", ".join(sorted(unknown)))
    missing = [key for key in ADMIN_CONFIG_KEYS if key not in supplied]
    if missing:
        problems.append("Missing configuration keys (schema 2 lists every key): " + ", ".join(missing))
    problems += _key_problems(supplied)
    values = None if problems else {key: supplied[key] for key in ADMIN_CONFIG_KEYS}
    return AdminConfig(2, values, (), tuple(problems), "admin-config-invalid" if problems else None), None


def parse_admin_config(data, *, label):
    """Versioned strict parse of ``pf-config.json`` bytes (PF-A2.2 section 3.2); pure, never migrates."""
    return _parse_admin(data, label)[0]


def validate_admin_config(data, *, label):
    """Pure validation of ``pf-config.json`` bytes -> (config, problems) (the A2.1 smoke contract).

    Schema 1: the exact A2.1 return (the config merged with the frozen implicit values, even with problems;
    None when the bytes are not a JSON object). Schema 2: (the 11 settings or None, problems). Any other
    version: (None, problems). ``config`` never carries ``schema_version``; callers add the comparison with a
    registered record and the group lookup.
    """
    config, merged = _parse_admin(data, label)
    if config.schema_version == 1:
        return merged, list(config.problems)
    return (dict(config.values) if config.values is not None else None), list(config.problems)


def admin_document(values):
    """The schema 2 document of the 11 settings: ``{"schema_version": 2, **values}`` in ADMIN_CONFIG_KEYS order."""
    return {"schema_version": ADMIN_CONFIG_SCHEMA_VERSION, **{key: values[key] for key in ADMIN_CONFIG_KEYS}}


def migrate_admin_config(config):
    """Explicit migration 1 -> 2 (section 3.3): every explicit value kept, every implicit key materialized from
    the frozen ADMIN_CONFIG_DEFAULTS (never from an example). Never repairs: a value that schema 2 refuses
    raises ConfigError ``admin-config-migration-blocked: {key}: {rule}[; ...]``."""
    if config.schema_version != 1 or config.values is None:
        raise ConfigError("admin-config-migration-blocked: only a valid legacy schema 1 configuration migrates")
    document = admin_document(config.values)
    problems = _key_problems(document)
    if problems:
        raise ConfigError("admin-config-migration-blocked: " + "; ".join(problems))
    return document


def render_admin_config(document):
    """Deterministic bytes of a schema 2 document: 2-space JSON, schema_version first, then ADMIN_CONFIG_KEYS
    order, trailing newline. The bytes are parsed back and must give the same valid document."""
    if set(document) != {"schema_version", *ADMIN_CONFIG_KEYS}:
        raise ConfigError("render: a schema 2 document holds exactly schema_version and the 11 settings")
    ordered = {"schema_version": document["schema_version"], **{key: document[key] for key in ADMIN_CONFIG_KEYS}}
    data = (json.dumps(ordered, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    parsed = parse_admin_config(data, label="rendered pf-config.json")
    if parsed.problems or admin_document(parsed.values) != ordered:
        raise ConfigError("render: the rendered admin configuration does not round-trip: "
                          + "; ".join(parsed.problems or ("values differ",)))
    return data


def admin_config_changes(before, document, *, asked):
    """Audit and summary rows (section 3.10) for one admin write, in ADMIN_CONFIG_KEYS order.

    ``before``: the parsed current file (None in create mode); ``asked``: {key: answer} of the questions.
    Actions: created (create mode, from the example), added (asked in create mode), kept, materialized
    (schema 1 implicit value made explicit), changed (an asked value replaced an existing one).
    """
    rows = []
    for key in ADMIN_CONFIG_KEYS:
        after = document[key]
        if before is None:
            rows.append({"key": key, "action": "added" if key in asked else "created", "before": None,
                         "after": after})
        elif key in asked:
            rows.append({"key": key, "action": "changed", "before": before.values[key], "after": after})
        elif key in before.implicit:
            rows.append({"key": key, "action": "materialized", "before": None, "after": after})
        else:
            rows.append({"key": key, "action": "kept", "before": before.values[key], "after": after})
    return rows


# ------------------------------------------------------------ config-change audit record (PF-A2.2)

CONFIG_CHANGE_ACTIONS = ("created", "kept", "materialized", "changed", "added", "unchanged", "set")
CONFIG_CHANGE_NAME = "config-change.json"
_SHA256 = "^[a-f0-9]{64}$"
# Embedded copy of contracts/config-change.schema.json (section 2.5); a test asserts the two stay identical.
# Validated with pf_install.validate_marked (the A2.1 description markers) plus config_change_problems.
CONFIG_CHANGE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin config-change record v1 (PF-A2.2)",
    "description": ("Private audit record <root>/instances/<uuid>/operations/<operation-id>/config-change.json of "
                    "one completed 'pf config' write. Strict UTF-8 JSON, normalized bytes, mode 0600. Normative "
                    "markers as in install-operation.schema.json: a nullable value and an array item rule are "
                    "written as the description \"null or <target>\" or \"items <target>\" (<target>: "
                    "$defs.<name>, string, sha256 or scalar; scalar = a string, a non-boolean integer or a "
                    "boolean). They are enforced only by pf_install.validate_marked; every consumer validates with "
                    "it. Cross-field rules (pf_config.config_change_problems): file '.env' has a profile_id and "
                    "null schema/hash fields, file 'pf-config.json' has a null profile_id and schema_after 2; a key "
                    "in pf_config.SECRET_KEYS has null before/after and action unchanged or set, every other key "
                    "never has unchanged or set. No secret value and no .env hash ever appears."),
    "$defs": {
        "record": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "operation_id", "completed", "file", "mode", "profile_id",
                         "schema_before", "schema_after", "before_sha256", "after_sha256", "changes"],
            "properties": {
                "schema_version": {"const": 1},
                "operation_id": {"type": "string", "pattern": "^[0-9]{8}T[0-9]{6}Z-config-[0-9a-f]{8}$"},
                "completed": {"type": "string", "pattern": "^[0-9]{8}T[0-9]{6}Z$"},
                "file": {"enum": ["pf-config.json", ".env"]},
                "mode": {"enum": ["create", "migrate", "complete"]},
                "profile_id": {"description": "null or string"},
                "schema_before": {"enum": [None, 1, 2]},
                "schema_after": {"enum": [None, 2]},
                "before_sha256": {"description": "null or sha256"},
                "after_sha256": {"description": "null or sha256"},
                "changes": {"type": "array", "description": "items $defs.change"},
            },
        },
        "change": {
            "type": "object",
            "additionalProperties": False,
            "required": ["key", "action", "before", "after"],
            "properties": {
                "key": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,63}$"},
                "action": {"enum": list(CONFIG_CHANGE_ACTIONS)},
                "before": {"description": "null or scalar"},
                "after": {"description": "null or scalar"},
            },
        },
    },
}


def config_change_problems(record):
    """The cross-field rules of a config-change record (section 2.5); [] when valid. Run after validate_marked."""
    problems = []
    if not isinstance(record, dict):
        return ["record: not an object"]
    if record.get("file") == ".env":
        if not isinstance(record.get("profile_id"), str):
            problems.append("file .env needs a profile_id")
        for name in ("schema_before", "schema_after", "before_sha256", "after_sha256"):
            if record.get(name) is not None:
                problems.append(f"file .env needs a null {name} (no .env hash is ever recorded)")
    elif record.get("file") == "pf-config.json":
        if record.get("profile_id") is not None:
            problems.append("file pf-config.json needs a null profile_id")
        if record.get("schema_after") != ADMIN_CONFIG_SCHEMA_VERSION:
            problems.append("file pf-config.json needs schema_after 2")
        if record.get("mode") == "create" and (record.get("schema_before") is not None
                                               or record.get("before_sha256") is not None):
            problems.append("a created pf-config.json has no schema_before and no before_sha256")
    for index, change in enumerate(record.get("changes") or ()):
        if not isinstance(change, dict):
            continue
        where = f"changes[{index}] {change.get('key')}"
        if change.get("key") in SECRET_KEYS:
            if change.get("before") is not None or change.get("after") is not None:
                problems.append(where + ": a secret key never records a value")
            if change.get("action") not in ("unchanged", "set"):
                problems.append(where + ": a secret key records only unchanged or set")
        elif change.get("action") in ("unchanged", "set"):
            problems.append(where + ": unchanged and set apply to secret keys only")
    return problems


def _reject_control_characters(value, where):
    # The character is never echoed: the line may hold a secret (PF-A2.2 audit).
    for char in value:
        if ord(char) < 0x20 or char == "\x7f":
            raise ConfigError(f"{where}: a control character is not supported (not shown)")


def _utf8_error(data, label, exc):
    """``{label}:{line}: not valid UTF-8`` without the offending bytes (the line may hold a secret)."""
    line = bytes(data[:exc.start]).count(b"\n") + 1
    return ConfigError(f"{label}:{line}: not valid UTF-8 (the bytes are not shown)")


def value_render_issue(value):
    """Why ``value`` cannot be rendered into the private snapshot, or None.

    The snapshot renders every value single-quoted, the one form whose contents are
    literal for the strict parser here and for Docker Compose's env-file reader. A
    value containing a single quote, or ending in a backslash (which would escape the
    closing quote for some readers), therefore has no unambiguous rendering.
    """
    if not isinstance(value, str):
        return "not a string"
    for char in value:
        if ord(char) < 0x20 or char == "\x7f":
            return f"control character U+{ord(char):04X}"
    if "'" in value:
        return "single quote (') has no unambiguous single-quoted rendering"
    if value.endswith("\\"):
        return "trailing backslash would escape the closing quote"
    return None


def _parse_double_quoted(body, where):
    """Escapes inside double quotes: ``\\\\`` and ``\\"`` only; anything else is unsupported."""
    result = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            if index + 1 >= len(body):
                raise ConfigError(f"{where}: dangling backslash in a double-quoted value")
            nxt = body[index + 1]
            if nxt not in ("\\", '"'):
                # The escaped character is never echoed: the value may be a secret (PF-A2.2 audit).
                raise ConfigError(f"{where}: unsupported escape in a double-quoted value "
                                  "(only \\\\ and \\\" are accepted; no $ expansion)")
            result.append(nxt)
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def parse_app_env(data, *, label, allowed_keys=APP_KEYS, require_all=True):
    """Strictly parse ``.env`` bytes into {key: literal value}.

    Grammar: UTF-8; ``\\n`` or ``\\r\\n`` line ends (a bare ``\\r`` is rejected); blank
    lines and ``#`` comment lines; ``KEY=VALUE`` with no whitespace around ``=`` and no
    ``export`` prefix. VALUE is one of: unquoted (no whitespace, quotes or ``#``; ``$``
    and ``\\`` are literal), single-quoted (literal, cannot contain ``'``), or
    double-quoted (only ``\\\\`` and ``\\"`` escapes, no expansion). Text after a quoted
    value must be blank or a ``#`` comment. Unknown and duplicate keys are rejected;
    with ``require_all`` every allowed key must be present.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ConfigError(f"{label}: expected bytes")
    if b"\x00" in data:
        raise ConfigError(f"{label}: NUL byte")
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _utf8_error(data, label, exc) from exc
    values = {}
    for number, raw in enumerate(text.split("\n"), 1):
        where = f"{label}:{number}"
        if raw.endswith("\r"):
            raw = raw[:-1]
        if "\r" in raw:
            raise ConfigError(f"{where}: bare carriage return")
        _reject_control_characters(raw.replace("\t", ""), where)
        line = raw.strip(" \t")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{where}: expected KEY=VALUE")
        key, rest = raw.split("=", 1)
        if key != key.strip() or not KEY_RE.fullmatch(key):
            raise ConfigError(f"{where}: invalid key {key.strip()!r} (no whitespace around '=', no 'export')")
        if key not in allowed_keys:
            raise ConfigError(f"{where}: unknown key {key!r}; accepted keys are {', '.join(allowed_keys)}")
        if key in values:
            raise ConfigError(f"{where}: duplicate key {key!r}")
        if rest[:1] == "'":
            end = rest.find("'", 1)
            if end < 0:
                raise ConfigError(f"{where}: unterminated single-quoted value")
            value = rest[1:end]
            tail = rest[end + 1:]
        elif rest[:1] == '"':
            end = 1
            while True:
                end = rest.find('"', end)
                if end < 0:
                    raise ConfigError(f"{where}: unterminated double-quoted value")
                backslashes = 0
                probe = end - 1
                while probe > 0 and rest[probe] == "\\":
                    backslashes += 1
                    probe -= 1
                if backslashes % 2 == 0:
                    break
                end += 1
            value = _parse_double_quoted(rest[1:end], where)
            tail = rest[end + 1:]
        else:
            value = rest.rstrip(" \t")
            tail = ""
            if value == "":
                pass
            elif not UNQUOTED_VALUE_RE.fullmatch(value):
                raise ConfigError(f"{where}: unquoted value of {key} contains whitespace, quotes or '#'; "
                                  "quote it with single quotes")
        if tail.strip(" \t") and not tail.strip(" \t").startswith("#"):
            raise ConfigError(f"{where}: unexpected text after the quoted value of {key}")
        _reject_control_characters(value, where)
        values[key] = value
    if require_all:
        missing = [key for key in allowed_keys if key not in values]
        if missing:
            raise ConfigError(f"{label}: missing keys {', '.join(missing)}")
    return values


def render_app_env(values, *, keys=APP_KEYS):
    """Private snapshot bytes: one ``KEY='literal'`` line per key, in allowlist order."""
    lines = ["# Deployment Admin frozen application configuration. Generated; do not edit."]
    for key in keys:
        if key not in values:
            raise ConfigError(f"render: missing {key}")
        issue = value_render_issue(values[key])
        if issue is not None:
            raise ConfigError(f"render: {key}: {issue}")
        lines.append(f"{key}='{values[key]}'")
    return ("\n".join(lines) + "\n").encode("utf-8")


def unsupported_values(values):
    """{key: issue} for every accepted value that has no unambiguous snapshot rendering."""
    problems = {}
    for key, value in values.items():
        issue = value_render_issue(value)
        if issue is not None:
            problems[key] = issue
    return problems


def database_url(user, password, database, *, host="db", port=5432):
    """``postgresql+psycopg://`` URL with percent-encoded credentials (exact round-trip)."""
    return "postgresql+psycopg://{}:{}@{}:{}/{}".format(
        urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe=""), host, port,
        urllib.parse.quote(database, safe=""),
    )


def child_values(values, *, workspace, instance_id):
    """Allowlisted app values plus the core-generated keys for one Compose invocation."""
    result = {key: values[key] for key in APP_KEYS}
    result["PARTFLOW_REPO_ROOT"] = str(workspace)
    result["PARTFLOW_DATABASE_URL"] = database_url(values["POSTGRES_USER"], values["POSTGRES_PASSWORD"],
                                                   values["POSTGRES_DB"])
    result["DEPLOY_ADMIN_INSTANCE_ID"] = str(instance_id)
    return result


CHILD_KEYS = APP_KEYS + GENERATED_KEYS


# ------------------------------------------------------------ app-variable declarations (PF-A2.2)


@dataclasses.dataclass(frozen=True)
class AppVariable:
    key: str
    kind: str            # identifier | database | secret | timezone | access | port | host (tests may add others)
    credential: bool     # initialized-database credential: never generated, asked or rewritten once deployed
    secret: bool         # never shown, never asked; preserved byte for byte or generated when absent
    question: str


@dataclasses.dataclass(frozen=True)
class AppDeclaration:
    """The application variables one profile declares (a static table; no loader, no file-based declaration)."""

    profile_id: str
    example_name: str
    variables: tuple
    generated_secret_hex_bytes: int

    @property
    def keys(self):
        return tuple(variable.key for variable in self.variables)


APP_DECLARATIONS = {
    "partflow-staging-legacy": AppDeclaration(
        profile_id="partflow-staging-legacy", example_name="nas.env.example", generated_secret_hex_bytes=32,
        variables=(
            AppVariable("POSTGRES_USER", "identifier", True, False, "PostgreSQL user"),
            AppVariable("POSTGRES_PASSWORD", "secret", True, True, ""),
            # "database": the identifier rule plus the maintenance/template database refusal (POSTGRES_DB only).
            AppVariable("POSTGRES_DB", "database", True, False, "PostgreSQL database"),
            AppVariable("SITE_TIMEZONE", "timezone", False, False, "Factory IANA timezone"),
            AppVariable("PARTFLOW_BIND_IP", "access", False, False, "Select access mode"),
            AppVariable("PARTFLOW_HTTP_PORT", "port", False, False, "PartFlow HTTP port"),
            AppVariable("PARTFLOW_ALLOWED_HOST", "host", False, False, "Exact internal Reverse Proxy hostname"),
        )),
}


def app_declaration(profile_id):
    """The static declaration of ``profile_id``; any other profile is refused (S9: no generic loading)."""
    declaration = APP_DECLARATIONS.get(profile_id)
    if declaration is None:
        raise ConfigError(f"app-profile-undeclared: Profile {profile_id} declares no application variables in this "
                          "control; nothing was changed.")
    return declaration


@dataclasses.dataclass(frozen=True)
class AppPlanItem:
    """One planned key. ``action``: kept | ask | generate | refuse. ``code`` names the refusal, or the note of a
    kept item (zone-unknown-on-host, zone-data-unavailable, password-weak-for-new-deployment)."""

    key: str
    action: str
    code: Optional[str]
    reason: str
    default: Optional[str]


def plan_app_config(declaration, *, current, example, deployed, check, zone, canonical=None):
    """Plan per declared key (section 3.4); pure. Nothing here generates, asks or writes.

    ``current``: the parsed editable file (None when absent); ``example``: the parsed installed example;
    ``check(kind, value)``: None when the value is valid as written, else the reason; ``zone(name)``:
    zone_status; ``canonical(kind, value)``: the canonical spelling of a non-canonical value, or None.
    """
    current = current or {}
    items = []
    zone_data = None
    for variable in declaration.variables:
        key, kind = variable.key, variable.kind
        value = current.get(key)
        present = value not in (None, "")
        absent = "missing" if value is None else "empty"
        if variable.secret:
            if present:
                issue = value_render_issue(value) or ("shorter than 4 characters" if len(value) < 4 else None)
                if issue is not None:
                    items.append(AppPlanItem(key, "refuse", "migration-issue", issue, None))
                elif len(value) < 32 and not deployed:
                    items.append(AppPlanItem(key, "kept", "password-weak-for-new-deployment",
                                             "shorter than 32 characters", None))
                else:
                    items.append(AppPlanItem(key, "kept", None, "", None))
            elif deployed:
                items.append(AppPlanItem(key, "refuse", "app-credential-unusable", absent, None))
            else:
                items.append(AppPlanItem(key, "generate", None, absent, None))
            continue
        problem = check(kind, value) if present else None
        example_value = example.get(key)
        example_default = example_value if example_value not in (None, "") \
            and check(kind, example_value) is None else None
        if variable.credential:
            if present and problem is None:
                items.append(AppPlanItem(key, "kept", None, "", None))
            elif deployed:
                items.append(AppPlanItem(key, "refuse", "app-credential-unusable", problem or absent, None))
            else:
                items.append(AppPlanItem(key, "ask", None, problem or absent, None if present else example_default))
            continue
        if kind == "timezone":
            if present and problem is None:
                status, detail = zone(value)
                if status == "invalid-name":
                    # Not a zone name at all (a `.`/`..` component): invalid independent of host data, so it is
                    # asked like a grammar-invalid value instead of kept with a misleading host-data note.
                    problem = detail
                else:
                    note = {"ok": None, "zone-data-unavailable": "zone-data-unavailable"}.get(status,
                                                                                            "zone-unknown-on-host")
                    items.append(AppPlanItem(key, "kept", note, detail, None))
                    continue
            if zone_data is None:
                zone_data = zone("UTC")
            if zone_data[0] == "zone-data-unavailable":
                # An answer could not be verified against host zone data: refuse instead of asking.
                items.append(AppPlanItem(key, "refuse", "zone-data-unavailable", zone_data[1], None))
                continue
            default = None
            if not present and example_default is not None and zone(example_default)[0] == "ok":
                default = example_default
            items.append(AppPlanItem(key, "ask", None, problem or absent, default))
            continue
        if present and problem is None:
            items.append(AppPlanItem(key, "kept", None, "", None))
        elif present:
            items.append(AppPlanItem(key, "ask", None, problem,
                                     canonical(kind, value) if canonical is not None else None))
        else:
            items.append(AppPlanItem(key, "ask", None, absent, example_default))
    return items


def render_value(value):
    """One ``.env`` value as written by the wizard: unquoted when UNQUOTED_VALUE_RE matches, else single-quoted
    when that rendering is unambiguous, else ConfigError (nothing is escaped or altered)."""
    if not isinstance(value, str):
        raise ConfigError("render: value is not a string")
    if UNQUOTED_VALUE_RE.fullmatch(value):
        return value
    issue = value_render_issue(value)
    if issue is not None:
        raise ConfigError(f"render: value has no unambiguous rendering ({issue})")
    return f"'{value}'"


def rewrite_app_env(base, *, keys, set_values, append):
    """Line-preserving rewrite of ``.env`` bytes (section 3.4); pure.

    Each line of a key in ``set_values`` becomes ``KEY=<render_value>`` with its own line ending; every other line
    keeps its exact bytes. Keys in ``append`` are appended in ``keys`` order after ensuring a final newline. The
    result is parsed back strictly (``allowed_keys=keys``, every key required) and must equal the intended values,
    otherwise ConfigError and nothing is returned. Each key of ``set_values`` must occur exactly once in ``base``.
    """
    try:
        text = bytes(base).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _utf8_error(base, "base", exc) from exc
    intended = parse_app_env(base, label="base", allowed_keys=keys, require_all=False)
    for key in list(set_values) + list(append):
        if key not in keys:
            raise ConfigError(f"rewrite: {key} is not a declared key")
    pieces = text.split("\n")
    seen = {}
    for index, piece in enumerate(pieces):
        body = piece[:-1] if piece.endswith("\r") else piece
        stripped = body.strip(" \t")
        if not stripped or stripped.startswith("#") or "=" not in body:
            continue
        key = body.split("=", 1)[0]
        if key in set_values:
            seen[key] = seen.get(key, 0) + 1
            pieces[index] = f"{key}={render_value(set_values[key])}" + ("\r" if piece.endswith("\r") else "")
    for key in set_values:
        if seen.get(key) != 1:
            raise ConfigError(f"{key} occurs {seen.get(key, 0)} times; exactly one line is required")
    text = "\n".join(pieces)
    extra = [key for key in keys if key in append]
    if extra:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "".join(f"{key}={render_value(append[key])}\n" for key in extra)
    result = text.encode("utf-8")
    intended.update(set_values)
    intended.update({key: append[key] for key in extra})
    try:
        parsed = parse_app_env(result, label="rewritten .env", allowed_keys=keys, require_all=True)
    except ConfigError as exc:
        raise ConfigError("the rewritten .env does not round-trip through the strict parser: " + str(exc)) from exc
    if parsed != intended:
        raise ConfigError("the rewritten .env values differ from the intended values; nothing was written")
    return result


# ------------------------------------------------------------ host zone data (PF-A2.2 section 3.5)

# The A1 SITE_TIMEZONE grammar (pf-admin validate_timezone_name uses the same expression).
TIMEZONE_RE = re.compile(r"(?:UTC|[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+)\Z")


def compiled_tzpath():
    """The interpreter's compile-time TZPATH (what zoneinfo uses when PYTHONTZPATH is unset); absolute entries
    only. PYTHONTZPATH is never consulted, so the answer does not depend on the caller's environment."""
    value = sysconfig.get_config_var("TZPATH")
    if not value:
        return ()
    return tuple(entry for entry in str(value).split(os.pathsep) if os.path.isabs(entry))


def zone_status(name, *, tzpath=None):
    """(status, detail) of ``name`` in the host's installed zone data; read-only, never blocks.

    status: ``ok`` (detail: the directory), ``unknown-zone``, ``invalid-name`` or ``zone-data-unavailable``
    (detail: the searched path). Links inside the zone data are followed (system data uses them).
    """
    tzpath = compiled_tzpath() if tzpath is None else tuple(tzpath)
    searched = os.pathsep.join(tzpath) or "(no compiled TZPATH)"
    if not isinstance(name, str) or not TIMEZONE_RE.fullmatch(name) or name.startswith("/") \
            or any(part in (".", "..") for part in name.split("/")):
        return "invalid-name", "not an IANA zone name"
    directories = [entry for entry in tzpath if os.path.isdir(entry)]
    if not directories:
        return "zone-data-unavailable", searched
    for directory in directories:
        candidate = os.path.join(directory, name)
        if not os.path.exists(candidate):
            continue
        try:
            fd = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            return "unknown-zone", directory
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return "unknown-zone", directory
            return ("ok" if os.read(fd, 4) == b"TZif" else "unknown-zone"), directory
        finally:
            os.close(fd)
    return "unknown-zone", searched


# ------------------------------------------------------------- frozen snapshot


@dataclasses.dataclass(frozen=True)
class FrozenAppConfig:
    """Immutable reference to the private snapshot one operation consumes."""

    operation_id: str
    directory: Path
    env_file: Path
    env_sha256: str
    source_sha256: str
    values: types.MappingProxyType

    def child_values(self, workspace, instance_id):
        return child_values(self.values, workspace=workspace, instance_id=instance_id)


def _utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_private(path, data, mode):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(str(temporary), mode)
        os.replace(str(temporary), str(path))
    except BaseException:
        try:
            os.unlink(str(temporary))
        except OSError:
            pass
        raise
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_nofollow(path):
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


def freeze_app_config(values, *, source_bytes, operation_id, operation_dir):
    """Render the approved values into ``<operation_dir>/app.env`` (0400) and record it.

    ``values`` must already be validated by the adapter. The rendered file is parsed
    back and compared literally before the reference is returned, so a value that
    would not survive the round-trip never becomes an approved snapshot.
    """
    operation_dir = Path(operation_dir)
    problems = unsupported_values(values)
    if problems:
        raise ConfigError("migration-issue: existing configuration values cannot be frozen literally: "
                          + "; ".join(f"{key}: {issue}" for key, issue in sorted(problems.items()))
                          + ". The value was not changed or regenerated; fix config/.env explicitly.")
    rendered = render_app_env(values)
    parsed = parse_app_env(rendered, label="snapshot")
    if parsed != {key: values[key] for key in APP_KEYS}:
        raise ConfigError("snapshot round-trip failed; nothing was frozen")
    env_path = operation_dir / SNAPSHOT_FILE
    if os.path.lexists(str(env_path)):
        raise ConfigError(f"{env_path} already exists; a snapshot is written once per operation")
    _write_private(env_path, rendered, 0o400)
    env_sha = hashlib.sha256(rendered).hexdigest()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    record = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "created_at": _utc(),
        "env_file": SNAPSHOT_FILE,
        "env_file_sha256": env_sha,
        "env_file_bytes": len(rendered),
        "source_env_sha256": source_sha,
        "keys": list(APP_KEYS),
    }
    _write_private(operation_dir / SNAPSHOT_RECORD,
                   json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n", 0o400)
    return FrozenAppConfig(operation_id=operation_id, directory=operation_dir, env_file=env_path,
                           env_sha256=env_sha, source_sha256=source_sha,
                           values=types.MappingProxyType(dict(parsed)))


def verify_frozen(frozen):
    """Re-read the snapshot; its bytes, hash and parsed values must equal the frozen reference."""
    try:
        data = _read_nofollow(frozen.env_file)
    except OSError as exc:
        raise ConfigError(f"frozen snapshot unreadable: {frozen.env_file}: {exc.strerror or exc}") from exc
    if hashlib.sha256(data).hexdigest() != frozen.env_sha256:
        raise ConfigError(f"frozen snapshot changed on disk: {frozen.env_file}; the operation stops")
    if parse_app_env(data, label=str(frozen.env_file)) != dict(frozen.values):
        raise ConfigError(f"frozen snapshot no longer parses to the approved values: {frozen.env_file}")
    return frozen


# ------------------------------------------------------------ permission policy (PF-A2.3)
# The semantic permission policy (design r2 schema, PERMISSIONS.md): strict parsing, the exact compiler of the mode
# table and scope floors, and the frozen approval/apply records. Pure: nothing here reads the host or writes a file.

PERMISSION_POLICY_VERSION = 1
PERMISSION_SCOPES = ("workspace", "configuration", "control", "backups", "recovery", "private_state")
ACCESS_LABELS = {"read_write": "Read and edit", "read_only": "View and copy", "none": "No group access"}
EXECUTABLE_LABELS = {"none": "No script execution", "owner_only": "Owner only", "owner_and_group": "Owner and group"}
_POLICY_GROUP_PATTERN = ("^[^\\s\\u0000-\\u001F\\u007F/:](?:[^\\u0000-\\u001F\\u007F/:]*"
                         "[^\\s\\u0000-\\u001F\\u007F/:])?$")
# Embedded copy of contracts/permission-policy.schema.json (the r2 schema, byte copy of the design package); a test
# asserts json.loads(file) == this value and the file's sha256.
PERMISSION_POLICY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin readable permission policy (design r2)",
    "description": ("Standalone design fragment, not accepted by the v2.5 runtime. Host groups, effective ACLs, "
                    "protected paths and operator approval require runtime checks."),
    "type": "object",
    "additionalProperties": False,
    "required": ["policy_version", "permissions"],
    "properties": {
        "policy_version": {"const": 1},
        "permissions": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workspace", "configuration", "control", "backups", "recovery", "private_state"],
            "properties": {
                "workspace": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "access", "executables", "inherit_group"],
                    "properties": {
                        "group": {"$ref": "#/$defs/groupName"},
                        "access": {"type": "string", "enum": ["none", "read_only", "read_write"]},
                        "executables": {"type": "string", "enum": ["none", "owner_only", "owner_and_group"]},
                        "inherit_group": {"type": "boolean"},
                    },
                    "allOf": [{
                        "if": {"properties": {"access": {"const": "none"}}, "required": ["access"]},
                        "then": {"properties": {"executables": {"enum": ["none", "owner_only"]}}},
                    }],
                },
                "configuration": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "access", "inherit_group"],
                    "properties": {
                        "group": {"$ref": "#/$defs/groupName"},
                        "access": {"type": "string", "enum": ["none", "read_only", "read_write"]},
                        "inherit_group": {"type": "boolean"},
                    },
                },
                "control": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "access", "executables"],
                    "properties": {
                        "group": {"$ref": "#/$defs/groupName"},
                        "access": {"type": "string", "enum": ["none", "read_only"]},
                        "executables": {"const": "owner_only"},
                    },
                },
                "backups": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "access"],
                    "properties": {
                        "group": {"$ref": "#/$defs/groupName"},
                        "access": {"type": "string", "enum": ["none", "read_only"]},
                    },
                },
                "recovery": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["group", "access"],
                    "properties": {
                        "group": {"$ref": "#/$defs/groupName"},
                        "access": {"type": "string", "enum": ["none", "read_only"]},
                    },
                },
                "private_state": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["access"],
                    "properties": {"access": {"const": "owner_only"}},
                },
            },
        },
    },
    "$defs": {
        "groupName": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _POLICY_GROUP_PATTERN},
    },
}


def _inline_policy_schema(schema):
    """The A1-subset form of the r2 schema (section 2.3): every ``{"$ref": "#/$defs/<name>"}`` replaced by that
    definition, the top-level ``$defs`` and the one ``allOf`` removed. permission_policy_problems enforces the
    removed allOf (access none => executables not owner_and_group) as a semantic rule. Pure."""
    definitions = schema["$defs"]

    def inline(node):
        if isinstance(node, dict):
            if set(node) == {"$ref"}:
                return inline(definitions[node["$ref"][len("#/$defs/"):]])
            return {key: inline(value) for key, value in node.items() if key != "allOf"}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    return inline({key: value for key, value in schema.items() if key != "$defs"})


PERMISSION_POLICY_SUBSET = _inline_policy_schema(PERMISSION_POLICY_SCHEMA)

_OPERATION_ID = "^[0-9]{8}T[0-9]{6}Z-permissions-apply-[0-9a-f]{8}$"
_UTC = "^[0-9]{8}T[0-9]{6}Z$"
_UUID = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_MARKERS = ("Normative markers as in install-operation.schema.json (\"null or <target>\", \"items <target>\"), enforced "
            "only by pf_install.validate_marked.")
# Embedded copy of contracts/permission-approval.schema.json (section 2.5); a test asserts the two stay identical.
PERMISSION_APPROVAL_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin approved permission policy record v1 (PF-A2.3)",
    "description": ("Protected record <private_state>/permission-policy.json (root, 0600, normalized JSON bytes). "
                    "Written only by 'pf permissions apply' (or its --resume) when a confirmed apply completed; a byte "
                    "copy is kept as operations/<op-id>/permission-approval.json of the approving invocation. "
                    + _MARKERS + " Cross-field rules (pf_config.permission_approval_problems): policy passes "
                    "pf_config.permission_policy_problems and pf_config.permission_policy_unsupported; policy_sha256 = "
                    "sha256(normalize_json(policy)); revision 1 has a null previous_sha256 and every later revision "
                    "the sha256 of the previous record bytes; instance_id equals the selected instance; operation_id "
                    "is the apply whose journal produced the approval (never a resume's id); confirmed_plans is "
                    "non-empty, its first element names operation_id and every element names an apply or resume of "
                    "that journal."),
    "$defs": {
        "record": {
            "type": "object",
            "additionalProperties": False,
            "required": ["schema_version", "instance_id", "revision", "approved", "operation_id", "policy_sha256",
                         "previous_sha256", "confirmed_plans", "policy"],
            "properties": {
                "schema_version": {"const": 1},
                "instance_id": {"type": "string", "pattern": _UUID},
                "revision": {"type": "integer", "minimum": 1},
                "approved": {"type": "string", "pattern": _UTC},
                "operation_id": {"type": "string", "pattern": _OPERATION_ID},
                "policy_sha256": {"type": "string", "pattern": _SHA256},
                "previous_sha256": {"description": "null or sha256"},
                "confirmed_plans": {"type": "array", "description": "items $defs.confirmed_plan"},
                "policy": {"type": "object"},
            },
        },
        "confirmed_plan": {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation_id", "plan_sha256"],
            "properties": {
                "operation_id": {"type": "string", "pattern": _OPERATION_ID},
                "plan_sha256": {"type": "string", "pattern": _SHA256},
            },
        },
    },
}


def _scope_enum():
    return {"type": "string", "enum": list(PERMISSION_SCOPES)}


def _record(properties):
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


# Embedded copy of contracts/permission-apply.schema.json (section 2.6); a test asserts the two stay identical.
PERMISSION_APPLY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin permission apply journal v1 (PF-A2.3)",
    "description": ("Private records of one 'pf permissions apply' journal under <private_state>/operations/<op-id>/: "
                    "permission-plan.json ($defs.plan), permission-changes.jsonl (one $defs.changes_header line, then "
                    "one change array [scope, path, dev, ino, type, before_mode, before_gid, after_mode, after_gid] "
                    "per line, checked by pf_config.change_problems; plan_sha256 = sha256 of the exact file bytes), "
                    "permission-effects.jsonl (one normalized $defs.effect line per changed object, appended and "
                    "fsynced before the change runs; paths checked by pf_config.effect_problems) and "
                    "permission-apply.json ($defs.outcome, one per apply, resume or abandon invocation). Strict UTF-8 "
                    "JSON, normalized bytes, mode 0600. " + _MARKERS + " effective_access_verified and "
                    "future_file_behavior_verified are the constant 'not verified': no A2.3 code path performs an "
                    "account test (PERMISSIONS section 5)."),
    "$defs": {
        "revision": {"type": "integer", "minimum": 1},
        "scope": _scope_enum(),
        "plan_root": _record({
            "scope": _scope_enum(),
            "dev": {"type": "integer", "minimum": 0},
            "ino": {"type": "integer", "minimum": 0},
            "gid": {"type": "integer", "minimum": 0},
            "fenced": {"type": "boolean"},
        }),
        "plan": _record({
            "schema_version": {"const": 1},
            "operation_id": {"type": "string", "pattern": _OPERATION_ID},
            "instance_id": {"type": "string", "pattern": _UUID},
            "created": {"type": "string", "pattern": _UTC},
            "base_revision": {"type": "integer", "minimum": 0},
            "base_sha256": {"description": "null or sha256"},
            "policy_sha256": {"type": "string", "pattern": _SHA256},
            "policy": {"type": "object"},
            "scopes": {"type": "array", "description": "items $defs.scope"},
            "freeze_scopes": {"type": "array", "description": "items $defs.scope"},
            "roots": {"type": "array", "description": "items $defs.plan_root"},
            "change_count": {"type": "integer", "minimum": 0},
            "plan_sha256": {"type": "string", "pattern": _SHA256},
        }),
        "changes_header": _record({
            "schema_version": {"const": 1},
            "policy_sha256": {"type": "string", "pattern": _SHA256},
            "scopes": {"type": "array", "description": "items $defs.scope"},
            "change_count": {"type": "integer", "minimum": 0},
        }),
        "effect": _record({
            "seq": {"type": "integer", "minimum": 1},
            "kind": {"enum": ["fence", "entry"]},
            "scope": _scope_enum(),
            "path": {"type": "string"},
            "dev": {"type": "integer", "minimum": 0},
            "ino": {"type": "integer", "minimum": 0},
            "type": {"enum": ["dir", "file"]},
            "before_mode": {"type": "integer", "minimum": 0},
            "after_mode": {"type": "integer", "minimum": 0},
            "before_gid": {"type": "integer", "minimum": 0},
            "after_gid": {"type": "integer", "minimum": 0},
            "operation_id": {"type": "string", "pattern": _OPERATION_ID},
        }),
        "outcome": _record({
            "schema_version": {"const": 1},
            "operation_id": {"type": "string", "pattern": _OPERATION_ID},
            "apply_operation_id": {"type": "string", "pattern": _OPERATION_ID},
            "completed": {"type": "string", "pattern": _UTC},
            "action": {"enum": ["apply", "resume", "abandon"]},
            "result": {"enum": ["completed", "current", "interrupted", "abandoned", "abandoned-with-conflicts"]},
            "plan_sha256": {"description": "null or sha256"},
            "changed": {"type": "integer", "minimum": 0},
            "unplanned": {"type": "integer", "minimum": 0},
            "conflicts": {"type": "integer", "minimum": 0},
            "approved_revision": {"description": "null or $defs.revision"},
            "scopes": {"type": "array", "description": "items $defs.scope_status"},
        }),
        "scope_status": _record({
            "scope": _scope_enum(),
            "mode_applied": {"enum": ["yes", "partial", "no", "not-selected", "check-only"]},
            "effective_access_verified": {"const": "not verified"},
            "future_file_behavior_verified": {"const": "not verified"},
            "notes": {"type": "array", "description": "items string"},
        }),
    },
}


@dataclasses.dataclass(frozen=True)
class ScopeTarget:
    """The compiled target of one scope (section 3.2). Modes are outputs, never inputs (PERMISSIONS section 2)."""

    scope: str
    group: Optional[str]
    access: str
    inherit: bool
    executables: str
    dir_mode: int
    file_mode: int
    exec_mode: Optional[int]
    gid_rule: str        # "set" | "unmanaged"
    owner_rule: str      # "preserve" | "trusted"
    apply_rule: str      # "exact" | "ceiling"


_FILE_MODES = {"none": 0o600, "read_only": 0o640, "read_write": 0o660}
_DIR_MODES = {"none": 0o700, "read_only": 0o750, "read_write": 0o770}
_EXECUTE_BITS = {"none": 0, "owner_only": 0o100, "owner_and_group": 0o110}


def parse_permission_policy(data, *, label):
    """Strict parse of permission policy bytes -> (document or None, problems). Pure."""
    try:
        document = pf_bootstrap.parse_strict_json(data, label=label)
    except pf_bootstrap.BootstrapError as exc:
        return None, [str(exc)]
    problems = permission_policy_problems(document)
    return (None if problems else document), problems


def permission_policy_problems(document):
    """Schema (A1 subset of the r2 schema) and semantic problems of a parsed policy; [] when valid. No key outside
    the schema is ever read; there is no numeric mode field and no second representation."""
    problems = pf_instance.validate_against_schema(document, PERMISSION_POLICY_SUBSET, "$")
    if problems:
        return problems
    workspace = document["permissions"]["workspace"]
    if workspace["access"] == "none" and workspace["executables"] == "owner_and_group":
        return ["$.permissions.workspace: group execution needs group access (access none with executables "
                "owner_and_group)"]
    return []


def permission_policy_unsupported(document):
    """Valid choices this control refuses to activate (OD-A23-18), as operator copy fragments; [] when none."""
    if permission_policy_problems(document):
        return []
    if document["permissions"]["workspace"]["executables"] == "none":
        return ["workspace.executables = none is valid but not supported by this control (script execution none "
                "would make the workspace differ from the deployed-source manifest; choose Owner only)"]
    return []


def derive_permission_policy(admin_values, *, backups_group, recovery_group):
    """The derived policy of an instance without an approval (section 3.3): the A1/A2.2 modes, the editable scopes'
    group from ``workspace_write_group`` and the backups/recovery groups passed in (the gid on their roots)."""
    group = admin_values["workspace_write_group"]
    return {"policy_version": PERMISSION_POLICY_VERSION, "permissions": {
        "workspace": {"group": group, "access": "read_write", "executables": "owner_and_group", "inherit_group": True},
        "configuration": {"group": group, "access": "read_write", "inherit_group": True},
        "control": {"group": group, "access": "none", "executables": "owner_only"},
        "backups": {"group": backups_group, "access": "read_only"},
        "recovery": {"group": recovery_group, "access": "read_only"},
        "private_state": {"access": "owner_only"},
    }}


def compile_permission_policy(document):
    """{scope: ScopeTarget} for a valid policy (section 3.2); pure, total and deterministic. ConfigError on an
    invalid document. Workspace ``executables: none`` compiles (its exec mode is the file mode); activation is
    refused separately (permission_policy_unsupported)."""
    problems = permission_policy_problems(document)
    if problems:
        raise ConfigError("permission-policy-invalid: " + problems[0])
    permissions = document["permissions"]
    targets = {}
    for scope in ("workspace", "configuration"):
        item = permissions[scope]
        access, inherit = item["access"], item["inherit_group"]
        executables = item.get("executables", "none")
        file_mode = _FILE_MODES[access]
        targets[scope] = ScopeTarget(
            scope, item["group"], access, inherit, executables, _DIR_MODES[access] | (0o2000 if inherit else 0),
            file_mode, file_mode | _EXECUTE_BITS[executables] if scope == "workspace" else None,
            "set", "preserve", "exact")
    control = permissions["control"]
    targets["control"] = ScopeTarget(
        "control", control["group"], control["access"], False, "owner_only", _DIR_MODES[control["access"]],
        _FILE_MODES[control["access"]], _FILE_MODES[control["access"]] | 0o100,
        "set" if control["access"] == "read_only" else "unmanaged", "trusted", "ceiling")
    for scope in ("backups", "recovery"):
        item = permissions[scope]
        targets[scope] = ScopeTarget(scope, item["group"], item["access"], False, "none", _DIR_MODES[item["access"]],
                                     _FILE_MODES[item["access"]], None, "set", "trusted", "exact")
    targets["private_state"] = ScopeTarget("private_state", None, "owner_only", False, "none", 0o700, 0o600, None,
                                           "unmanaged", "trusted", "exact")
    return {scope: targets[scope] for scope in PERMISSION_SCOPES}


def ceiling_violations(target, *, mode, uid, gid, kind, policy_gid):
    """The control ceiling (section 3.2): every violation of one entry as copy; [] when it complies."""
    violations = []
    if uid != 0:
        violations.append(f"owner uid {uid}; the trusted owner is uid 0")
    if mode & 0o7000:
        violations.append(f"mode {mode:04o} carries a setuid, setgid or sticky bit")
    if mode & 0o022:
        violations.append(f"mode {mode:04o} grants group or other write")
    if mode & 0o007:
        violations.append(f"mode {mode:04o} grants other access")
    group_read = target.access == "read_only" and policy_gid is not None and gid == policy_gid
    if mode & 0o040 and not group_read:
        violations.append(f"mode {mode:04o} grants group read, which the permission policy does not")
    if mode & 0o010 and (kind == "file" or not group_read):
        violations.append(f"mode {mode:04o} grants group execute, which the permission policy does not")
    return violations


def symbolic_mode(mode, kind):
    """Absolute symbolic form of a mode (``u=rw,g=rw,o=``), with ``, setgid`` (and any other special bit)."""
    def part(bits):
        return "".join(letter for letter, bit in (("r", 4), ("w", 2), ("x", 1)) if bits & bit)

    text = f"u={part(mode >> 6 & 7)},g={part(mode >> 3 & 7)},o={part(mode & 7)}"
    for bit, name in ((0o4000, "setuid"), (0o2000, "setgid"), (0o1000, "sticky")):
        if mode & bit:
            text += ", " + name
    return text


def policy_diff(before, after):
    """[(scope, field, old, new)] for every field that differs, in scope order; ``before`` None lists everything."""
    rows = []
    for scope in PERMISSION_SCOPES:
        old = {} if before is None else before["permissions"][scope]
        new = after["permissions"][scope]
        for field in list(new) + [name for name in old if name not in new]:
            if before is None or old.get(field) != new.get(field):
                rows.append((scope, field, old.get(field), new.get(field)))
    return rows


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def permission_approval_problems(record, *, instance_id, previous_bytes=None):
    """The cross-field rules of an approval record (section 2.5); [] when valid. Run after validate_marked."""
    if not isinstance(record, dict):
        return ["record: not an object"]
    problems = []
    policy = record.get("policy")
    policy_problems = permission_policy_problems(policy)
    problems += ["policy: " + item for item in policy_problems]
    if not policy_problems:
        problems += ["policy: " + item for item in permission_policy_unsupported(policy)]
        if record.get("policy_sha256") != _sha256(pf_instance.normalize_json(policy)):
            problems.append("policy_sha256 is not the sha256 of the normalized policy")
    revision, previous = record.get("revision"), record.get("previous_sha256")
    if revision == 1 and previous is not None:
        problems.append("revision 1 has a null previous_sha256")
    if type(revision) is int and revision > 1 and previous is None:
        problems.append(f"revision {revision} names the sha256 of the previous record")
    if previous_bytes is not None and previous != _sha256(previous_bytes):
        problems.append("previous_sha256 is not the sha256 of the previous record bytes")
    if record.get("instance_id") != instance_id:
        problems.append(f"instance_id {record.get('instance_id')!r} is not the selected instance {instance_id}")
    plans = record.get("confirmed_plans")
    if isinstance(plans, list):
        if not plans:
            problems.append("confirmed_plans is empty")
        elif not isinstance(plans[0], dict) or plans[0].get("operation_id") != record.get("operation_id"):
            problems.append("the first confirmed plan does not name operation_id")
    return problems


def approval_matches_journal(record, plan):
    """Whether the approval record is the one this apply journal writes (section 3.7 rule)."""
    return (isinstance(record, dict) and isinstance(plan, dict) and type(plan.get("base_revision")) is int
            and record.get("revision") == plan["base_revision"] + 1
            and record.get("policy_sha256") == plan.get("policy_sha256")
            and record.get("previous_sha256") == plan.get("base_sha256")
            and record.get("operation_id") == plan.get("operation_id"))


def _relative_problem(path):
    """Why ``path`` is not a scope-relative POSIX path ("" = the scope root), or None."""
    if not isinstance(path, str):
        return "path is not a string"
    if path == "":
        return None
    if path.startswith("/") or "\x00" in path or any(part in ("", ".", "..") for part in path.split("/")):
        return f"path {path!r} is not a scope-relative path without '..' or a leading '/'"
    return None


def change_problems(line):
    """Problems of one permission-changes.jsonl change array; [] when valid."""
    if not isinstance(line, list) or len(line) != 9:
        return ["change: expected [scope, path, dev, ino, type, before_mode, before_gid, after_mode, after_gid]"]
    scope, path, dev, ino, kind, before_mode, before_gid, after_mode, after_gid = line
    problems = []
    if scope not in PERMISSION_SCOPES:
        problems.append(f"change: {scope!r} is not a scope")
    problem = _relative_problem(path)
    if problem is not None:
        problems.append("change: " + problem)
    for name, value in (("dev", dev), ("ino", ino), ("before_mode", before_mode), ("before_gid", before_gid),
                        ("after_mode", after_mode), ("after_gid", after_gid)):
        if type(value) is not int or value < 0:
            problems.append(f"change: {name} is not a non-negative integer")
        elif name.endswith("_mode") and value > 0o7777:
            problems.append(f"change: {name} {value} is not a permission mode")
    if kind not in ("dir", "file"):
        problems.append(f"change: type {kind!r} is not dir or file")
    return problems


def changes_bytes(header, changes):
    """The exact bytes of permission-changes.jsonl (section 3.5); plan_sha256 is their sha256. ConfigError when
    the header or a change is invalid (a programming error: nothing is written)."""
    problems = pf_instance.validate_against_schema(header, PERMISSION_APPLY_SCHEMA["$defs"]["changes_header"], "$")
    if not problems:
        problems += [f"$.scopes: {scope!r} is not a scope" for scope in header["scopes"]
                     if scope not in PERMISSION_SCOPES]
        if header["change_count"] != len(changes):
            problems.append(f"$.change_count {header['change_count']} != {len(changes)} change lines")
    for index, change in enumerate(changes):
        problems += [f"line {index + 2}: {item}" for item in change_problems(list(change))]
    if problems:
        raise ConfigError("permission-changes-invalid: " + problems[0])
    return b"".join([pf_instance.normalize_json(header) + b"\n"]
                    + [pf_instance.normalize_json(list(change)) + b"\n" for change in changes])


def effect_problems(line):
    """The cross-field rules of one effect line (section 2.6): the path is scope-relative; [] when valid."""
    if not isinstance(line, dict):
        return ["effect: not an object"]
    problem = _relative_problem(line.get("path"))
    return [] if problem is None else ["effect: " + problem]


# ------------------------------------------------------------ lifecycle records (PF-A3.1)
# Frozen wire schemas of the five lifecycle records (contracts/lifecycle-records.schema.json, equal as parsed JSON),
# the pure cross-field rules (lifecycle_problems) and the pure, deterministic legacy manifest migration. Validation
# order for every record: pf_install.validate_marked(value, LIFECYCLE_SCHEMA["$defs"][name], defs=...) and then
# lifecycle_problems(value, name). Nothing here reads a clock, a daemon or a filesystem.

_L_SHA = "^[0-9a-f]{64}$"
_L_STAMP = "^[0-9]{8}T[0-9]{6}Z$"
_L_COMMIT = "^[0-9a-f]{40}$"
_L_IMAGE_ID = "^sha256:[0-9a-f]{64}$"
_L_IMAGE_REF = "^[a-z0-9][a-z0-9._/-]*:[A-Za-z0-9_.-]+$"
_L_HEAD = "^[A-Za-z0-9_]{1,64}$"
_L_RELATIVE = "^[A-Za-z0-9._/-]{1,512}$"
_L_DEPLOYMENT = "^dep-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
_L_VERIFICATION = "^ver-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
_L_CHECKPOINT = "^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{6}$"
_L_PURGE = "^purge-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{6}$"
_L_BUNDLE = "^(?:purge-)?[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{6}$"
_L_OPERATION = "^[0-9]{8}T[0-9]{6}Z-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{8}$"
_L_EFFECT = "^e[0-9]{4}$"
_L_PG_NAME = "^[A-Za-z0-9_]{1,63}$"
_L_SLUG = "^[a-z0-9][a-z0-9_-]{0,39}$"
_L_STORE_ID = "^postgresql:[A-Za-z0-9_]{1,63}$"
_PLATFORM_RE = re.compile(r"[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9]+)?\Z")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

LIFECYCLE_RECORDS = ("operation_plan", "operation_journal", "deployment_record", "recovery_manifest",
                     "verification_record")
OPERATION_KINDS = ("deploy", "update", "backup", "rollback", "reset-db", "purge", "restore-instance", "abort-deploy")
JOURNAL_PHASE_NAMES = ("planned", "preparing", "initializing", "activating", "preserving", "migrating",
                       "syncing-workspace", "workspace_sync_pending", "capturing", "verifying", "preserving-current",
                       "restoring-candidate", "switching", "deleting", "finalizing", "preparing-target",
                       "restoring-data", "completed", "failed_preserved", "needs_operator", "cancelled")
TERMINAL_PHASES = frozenset({"completed", "failed_preserved", "needs_operator", "cancelled"})
# Allowed phases per operation kind (the LIFECYCLE section 2 phase table as the A3.1 contract reads it); every
# terminal phase is allowed for every kind. A3.2 owns the runtime journal and may amend this table only through the
# reviewed SPEC amendment of the section 2.4.2 freeze rule, before its first writer ships.
JOURNAL_PHASES = {
    "deploy": frozenset({"planned", "preparing", "initializing", "activating", "finalizing"}) | TERMINAL_PHASES,
    "update": frozenset({"planned", "preparing", "preserving", "migrating", "activating", "syncing-workspace",
                         "workspace_sync_pending", "finalizing"}) | TERMINAL_PHASES,
    "backup": frozenset({"planned", "capturing", "verifying", "finalizing"}) | TERMINAL_PHASES,
    "rollback": frozenset({"planned", "preparing", "preserving-current", "restoring-candidate", "switching",
                           "activating", "syncing-workspace", "workspace_sync_pending", "finalizing"}) | TERMINAL_PHASES,
    "reset-db": frozenset({"planned", "preparing", "preserving", "initializing", "switching", "activating",
                           "finalizing"}) | TERMINAL_PHASES,
    "purge": frozenset({"planned", "preparing", "preserving", "capturing", "verifying", "deleting",
                        "finalizing"}) | TERMINAL_PHASES,
    "restore-instance": frozenset({"planned", "preparing-target", "restoring-data", "activating",
                                   "syncing-workspace", "workspace_sync_pending", "finalizing"}) | TERMINAL_PHASES,
    "abort-deploy": frozenset({"planned", "deleting", "finalizing"}) | TERMINAL_PHASES,
}
EFFECT_TYPES = ("source-stage", "source-switch", "image-build", "image-tag", "image-load", "service-change",
                "database-create", "database-migrate", "database-restore", "database-switch", "database-drop",
                "database-alter", "resource-delete", "artifact-seal", "capture", "verification", "file-write")
MANIFEST_REASONS = ("scheduled-or-manual-backup", "emergency-manual", "before-update", "before-rollback",
                    "before-reset", "before-purge", "legacy")
CAPTURE_CLASSES = ("healthy_checkpoint", "emergency_preservation", "partial")
SOURCE_ORIGINS = ("deployment-artifact", "protected-store", "workspace-proven", "workspace-unverified",
                  "legacy-claim", "none")
PAYLOAD_TYPES = ("source_archive", "workspace_archive", "database_dump", "database_list", "postgres_globals",
                 "checkpoint_history", "image_archive", "config_env", "admin_config", "state_file",
                 "deployment_record", "compose_resolved")
SENSITIVE_PAYLOAD_TYPES = frozenset({"config_env", "admin_config", "postgres_globals", "state_file",
                                     "deployment_record", "compose_resolved"})
CHECKPOINT_FORBIDDEN_TYPES = frozenset({"config_env", "admin_config", "compose_resolved", "deployment_record",
                                        "postgres_globals"})
REQUIRED_ARTIFACT_ITEMS = frozenset({"image:backend", "image:frontend", "image:db", "source", "migration-files"})
VERIFICATION_LEVELS = ("captured", "data_restore_verified", "functional_recovery_verified")
STRATEGY = {"id": "postgresql-logical", "version": 1}
DB_IMAGE_REFERENCE = "postgres:16"
# The protected state files a purge bundle may carry for a restore into protected state (PF-A1.4).
RESTORABLE_STATE_FILES = ("deployed.json", "last-reset.json", "observed-tags.json")
# Fixed limitation strings of migrated legacy manifests (section 3.7).
LEGACY_LIMITATIONS = {
    "no-verification": "legacy manifest: no verification record; restore_test claim is not evidence",
    "provenance": "legacy manifest: provenance claim is not evidence",
    "source-instance": "legacy manifest: instance identity or workspace path not recorded",
    "producer": "legacy manifest: producing control release, profile and instance record not recorded",
    "quiescence": "legacy manifest: writer state (quiescence) not recorded",
    "server-version": "legacy manifest: PostgreSQL server version and database image identity not recorded",
    "roles": "legacy manifest: role inventory not recorded (postgres-globals.sql, when present, is evidence only)",
    "locale-extensions": "locale and extensions not recorded; checked on the restored candidate only",
    "retained-heads": "legacy manifest: Alembic heads of a non-connectable retained database were not recorded",
    "migration-files": "legacy manifest: migration file fingerprint not recorded",
    "format-1-env": "format 1 stores the runtime .env inside the source archive",
}
_LEGACY_REQUIRED_LIMITATIONS = ("no-verification", "provenance", "producer", "quiescence", "server-version", "roles",
                                "locale-extensions")


def _l_str(pattern=None, minimum=None, maximum=None):
    value = {"type": "string"}
    if pattern is not None:
        value["pattern"] = pattern
    if minimum is not None:
        value["minLength"] = minimum
    if maximum is not None:
        value["maxLength"] = maximum
    return value


def _l_int(minimum=0):
    return {"type": "integer", "minimum": minimum}


def _l_null(target):
    return {"description": "null or " + target}


def _l_items(target):
    return {"type": "array", "description": "items " + target}


def _l_map(target):
    return {"type": "object", "description": "map " + target}


_L_BOOL = {"type": "boolean"}
_L_IMAGE = _record({"reference": _l_str(_L_IMAGE_REF), "id": _l_str(_L_IMAGE_ID), "platform": _l_null("string"),
                    "repo_digests": _l_items("string"), "archived": _L_BOOL})
_L_PRODUCER = _record({"control_release_id": _l_str(None, 1, 128), "control_sha256": _l_str(_L_SHA),
                       "profile_id": _l_str(None, 1, 128), "profile_version": _l_str(None, 1, 128),
                       "profile_sha256": _l_str(_L_SHA), "instance_record_sha256": _l_str(_L_SHA)})
_L_STRATEGY = _record({"id": {"const": "postgresql-logical"}, "version": {"const": 1}})
_L_STATE = {"enum": ["running", "stopped", "absent"]}

# Embedded copy of contracts/lifecycle-records.schema.json (section 2.4); a test asserts the two stay equal.
LIFECYCLE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin lifecycle records v1 (PF-A3.1)",
    "description": (
        "Frozen wire schemas of the lifecycle records: $defs.operation_plan and $defs.operation_journal (frozen v1, "
        "no runtime writer in PF-A3.1), $defs.deployment_record (<private_state>/artifacts/deployments/<id>/"
        "deployment-record.json), $defs.recovery_manifest (manifest.json of a checkpoint or purge bundle) and "
        "$defs.verification_record (<private_state>/artifacts/verifications/<bundle-id>/<verification-id>.json). "
        "Strict UTF-8 JSON written as exactly pf_instance.normalize_json(value) (sorted keys, no whitespace, no "
        "trailing newline); duplicate keys, non-finite numbers and unknown keys are rejected; booleans are not "
        "integers. Normative markers: a nullable value, an array item rule and a map value rule are written as the "
        "description \"null or <target>\", \"items <target>\" or \"map <target>\" (<target>: $defs.<name>, string, "
        "sha256, path or scalar), enforced by pf_install.validate_marked. Cross-field rules, array limits, path "
        "canonical forms and patterns of nullable values are enforced by pf_config.lifecycle_problems. "
        "manifest.sha256 holds the hex SHA-256 of the exact manifest.json file bytes plus a newline."),
    "$defs": {
        "sha256": _l_str(_L_SHA),
        "stamp": _l_str(_L_STAMP),
        "uuid": _l_str(_UUID),
        "slug": _l_str(_L_SLUG),
        "commit": _l_str(_L_COMMIT),
        "image_id": _l_str(_L_IMAGE_ID),
        "image_ref": _l_str(_L_IMAGE_REF),
        "head": _l_str(_L_HEAD),
        "heads": {"type": "array"},
        "relative_path": _l_str(_L_RELATIVE),
        "deployment_id": _l_str(_L_DEPLOYMENT),
        "verification_id": _l_str(_L_VERIFICATION),
        "bundle_id": _l_str(_L_BUNDLE),
        "operation_id": _l_str(_L_OPERATION),
        "effect_id": _l_str(_L_EFFECT),
        "pg_name": _l_str(_L_PG_NAME),
        "image": _L_IMAGE,
        "producer": _L_PRODUCER,
        "quiescence": _record({"mode": {"enum": ["writers_stopped", "single_store_snapshot"]},
                               "observed_at": _l_str(_L_STAMP), "backend": _L_STATE, "frontend": _L_STATE}),
        "strategy_ref": _L_STRATEGY,
        "extension": _record({"name": _l_str(_L_PG_NAME), "version": _l_str(None, 1, 64)}),
        "role": _record({"name": _l_str(_L_PG_NAME), "superuser": _L_BOOL, "create_role": _L_BOOL,
                         "create_db": _L_BOOL, "login": _L_BOOL, "replication": _L_BOOL, "bypass_rls": _L_BOOL}),
        "row_counts": _record({"tables": _l_int(), "total_rows": _l_int(), "sha256": _l_str(_L_SHA)}),
        "store": _record({
            "store_id": _l_str(_L_STORE_ID), "kind": {"const": "postgresql_logical"}, "strategy": _L_STRATEGY,
            "database": _l_str(_L_PG_NAME), "role": {"enum": ["active", "retained"]},
            "allow_connections": _L_BOOL, "owner": _l_null("$defs.pg_name"), "encoding": _l_null("string"),
            "collate": _l_null("string"), "ctype": _l_null("string"), "extensions": _l_items("$defs.extension"),
            "alembic_heads": _l_items("$defs.head"), "row_counts": _l_null("$defs.row_counts"),
            "dump": _l_str(_L_RELATIVE), "list": _l_null("$defs.relative_path"),
            "consistency_group": _l_str(None, 1, 40)}),
        "payload": _record({
            "path": _l_str(_L_RELATIVE), "type": {"enum": list(PAYLOAD_TYPES)}, "size": _l_int(),
            "sha256": _l_str(_L_SHA), "store": _l_null("string"), "sensitive": _L_BOOL,
            "expanded_bytes": _l_null("scalar"), "members": _l_null("scalar"),
            "members_sha256": _l_null("sha256")}),
        "exclusion": _record({"item": _l_str(None, 1, 200), "reason": _l_str(None, 1, 500)}),
        "check": _record({"name": _l_str(None, 1, 80), "result": {"enum": ["passed", "failed", "not_run"]},
                          "detail": _l_str(None, 0, 500)}),
        "effect": _record({"effect_id": _l_str(_L_EFFECT), "type": {"enum": list(EFFECT_TYPES)},
                           "target": _l_str(None, 1, 300), "preconditions": _l_items("string"),
                           "postcondition": _l_str(None, 1, 300), "preservation_refs": _l_items("$defs.bundle_id")}),
        "effect_state": _record({"effect_id": _l_str(_L_EFFECT),
                                 "state": {"enum": ["not_started", "complete", "partial", "unknown"]},
                                 "observed_at": _l_null("$defs.stamp"), "evidence": _l_null("string")}),
        "approval": _record({"plan_sha256": _l_str(_L_SHA), "confirmed_at": _l_str(_L_STAMP),
                             "method": {"enum": ["typed-phrase", "policy-grant"]}}),
        "retained_artifact": _record({"kind": {"enum": ["checkpoint", "purge-bundle", "deployment", "database",
                                                        "image-tag", "staging"]},
                                      "name": _l_str(None, 1, 300), "sha256": _l_null("sha256")}),
        "policy_ref": _record({"revision": _l_int(1), "sha256": _l_str(_L_SHA)}),
        "config_ref": _record({"sha256": _l_str(_L_SHA), "bytes": _l_int()}),
        "coverage_entry": _record({"store_id": _l_str(None, 1, 200), "strategy_id": _l_str(None, 1, 80),
                                   "included": _L_BOOL, "reason": {"type": "string"}}),
        "confirmation": _record({"phrase": _l_str(None, 1, 120), "summary_sha256": _l_str(_L_SHA)}),
        "error": _record({"code": _l_str("^[a-z0-9-]{1,64}$"), "message": _l_str(None, 1, 2000)}),
        "terminal": _record({"outcome": {"enum": ["succeeded", "failed_preserved", "needs_operator", "cancelled"]},
                             "deployment_id": _l_null("$defs.deployment_id")}),
        "deployment_ref": _record({"deployment_id": _l_str(_L_DEPLOYMENT), "record_sha256": _l_str(_L_SHA)}),
        "bundle_ref": _record({"bundle_id": _l_str(_L_BUNDLE), "manifest_sha256": _l_str(_L_SHA)}),
        "consistency_group": _record({"group_id": _l_str("^[a-z0-9-]{1,40}$"), "stores": _l_items("string"),
                                      "claim": {"enum": ["transactional-single-store", "writers-stopped", "none"]}}),
        "mismatch": _record({"kind": {"enum": ["schema-image-mismatch", "deployment-image-mismatch"]},
                             "live_heads": _l_items("$defs.head"), "image_heads": _l_null("$defs.heads"),
                             "backend_image_id": _l_null("$defs.image_id"),
                             "expected_backend_image_id": _l_null("$defs.image_id"), "detail": {"type": "string"}}),
        "legacy": _record({"format": {"enum": [1, 2]}, "manifest_sha256": _l_str(_L_SHA),
                           "claimed_reason": _l_null("string"), "claimed_source_revision": _l_null("string"),
                           "claimed_source_verified": _l_null("scalar"), "claimed_restore_test": _l_null("string"),
                           "limitations": _l_items("string")}),
        "purge_section": _record({
            "resources_before_purge": _record({"containers": _l_items("string"), "volumes": _l_items("string"),
                                               "networks": _l_items("string"), "images": _l_items("string")}),
            "saved_image_refs": _l_items("string"), "missing_historical_image_refs": _l_items("string"),
            "state_files": _l_items("string"), "restore_scope": {"type": "string"}}),
        "operation_plan": _record({
            "schema_version": {"const": 1}, "operation_id": _l_str(_L_OPERATION),
            "kind": {"enum": list(OPERATION_KINDS)}, "created_at": _l_str(_L_STAMP),
            "instance": _record({"instance_id": _l_str(_UUID), "slug": _l_str(_L_SLUG),
                                 "compose_project": _l_str(_L_SLUG), "daemon_engine_id": _l_str(None, 1, 256),
                                 "record_sha256": _l_str(_L_SHA)}),
            "producer": _L_PRODUCER,
            "environment_policy": _record({"revision": _l_int(1), "sha256": _l_str(_L_SHA)}),
            "permission_policy": _l_null("$defs.policy_ref"),
            "source": _record({"provenance": {"enum": ["git_commit", "unknown", "not_applicable"]},
                               "commit": _l_null("$defs.commit"), "entries_sha256": _l_null("sha256"),
                               "deployment_id": _l_null("$defs.deployment_id")}),
            "images": _l_map("$defs.image"),
            "frozen_config": _l_null("$defs.config_ref"),
            "resources": _record({"inventory_sha256": _l_null("sha256"), "deletion_plan_sha256": _l_null("sha256")}),
            "coverage": _l_items("$defs.coverage_entry"),
            "confirmation": _l_null("$defs.confirmation"),
            "limits": _record({"timeout_seconds": _l_int(1), "minimum_free_bytes": _l_int()}),
            "effects": _l_items("$defs.effect"),
            "recovery_route": _l_items("string")}),
        "operation_journal": _record({
            "schema_version": {"const": 1}, "operation_id": _l_str(_L_OPERATION), "plan_sha256": _l_str(_L_SHA),
            "kind": {"enum": list(OPERATION_KINDS)}, "sequence": _l_int(1),
            "phase": {"enum": list(JOURNAL_PHASE_NAMES)}, "updated_at": _l_str(_L_STAMP),
            "approvals": _l_items("$defs.approval"), "effects": _l_items("$defs.effect_state"),
            "unresolved_effect": _l_null("$defs.effect_id"),
            "retained_artifacts": _l_items("$defs.retained_artifact"), "last_error": _l_null("$defs.error"),
            "legal_next": _l_items("string"), "result": _l_null("$defs.terminal")}),
        "deployment_record": _record({
            "schema_version": {"const": 1}, "deployment_id": _l_str(_L_DEPLOYMENT), "instance_id": _l_str(_UUID),
            "compose_project": _l_str(_L_SLUG), "created_at": _l_str(_L_STAMP),
            "operation": _record({"kind": {"enum": ["deploy", "update", "rollback", "restore-instance"]},
                                  "operation_id": _l_str(_L_OPERATION)}),
            "previous_deployment_id": _l_null("$defs.deployment_id"),
            "source": _record({
                "provenance": {"enum": ["git_commit", "unknown", "not_applicable"]},
                "commit": _l_null("$defs.commit"), "remote": _l_null("string"), "ref": _l_null("string"),
                "archive": _record({"path": {"const": "source.tar.gz"}, "size": _l_int(1), "sha256": _l_str(_L_SHA)}),
                "manifest": _record({"path": {"const": "source-manifest.json"}, "sha256": _l_str(_L_SHA),
                                     "entries_sha256": _l_str(_L_SHA), "files": _l_int(1)})}),
            "images": _record({"backend": _L_IMAGE, "frontend": _L_IMAGE, "db": _L_IMAGE}),
            "helpers": _l_items("$defs.image"),
            "strategy": _L_STRATEGY,
            "compose": _record({"path": {"const": "compose-resolved.json"}, "file_sha256": _l_str(_L_SHA),
                                "model_sha256": _l_str(_L_SHA), "compose_version": {"type": "string"},
                                "installed_file_sha256": _l_str(_L_SHA), "override_sha256": _l_str(_L_SHA)}),
            "config": _record({"path": {"const": "config.env"}, "sha256": _l_str(_L_SHA), "bytes": _l_int(1),
                               "admin_config_sha256": _l_null("sha256")}),
            "producer": _L_PRODUCER,
            "database": _record({"server_version_num": _l_int(90000), "alembic_heads": _l_items("$defs.head"),
                                 "migration_files_sha256": _l_str(_L_SHA)}),
            "activation": _record({"result": {"const": "activated"}, "health": {"const": "passed"},
                                   "completed_at": _l_str(_L_STAMP)}),
            "restored_from": _l_null("$defs.bundle_ref")}),
        "recovery_manifest": _record({
            "schema_version": {"const": 1}, "bundle_id": _l_str(_L_BUNDLE),
            "bundle_kind": {"enum": ["checkpoint", "purge-bundle"]}, "created_at": _l_str(_L_STAMP),
            "reason": {"enum": list(MANIFEST_REASONS)}, "capture_class": {"enum": list(CAPTURE_CLASSES)},
            "source_instance": _record({"instance_id": _l_null("$defs.uuid"), "slug": _l_null("$defs.slug"),
                                        "compose_project": _l_str(_L_SLUG), "environment": _l_str(None, 1, 64),
                                        "repository": _l_str(None, 1, 300), "workspace": _l_null("path")}),
            "producer": _l_null("$defs.producer"),
            "quiescence": _l_null("$defs.quiescence"),
            "source": _record({"provenance": {"enum": ["git_commit", "unknown"]}, "commit": _l_null("$defs.commit"),
                               "remote": _l_null("string"), "origin": {"enum": list(SOURCE_ORIGINS)},
                               "payload": _l_null("$defs.relative_path"), "entries_sha256": _l_null("sha256")}),
            "deployment": _l_null("$defs.deployment_ref"),
            "images": _record({"backend": _l_null("$defs.image"), "frontend": _l_null("$defs.image"),
                               "db": _l_null("$defs.image")}),
            "postgresql": _record({"server_version_num": _l_null("scalar"), "major": _l_int(9),
                                   "image_id": _l_null("$defs.image_id")}),
            "roles": _l_items("$defs.role"),
            "stores": _l_items("$defs.store"),
            "consistency_groups": _l_items("$defs.consistency_group"),
            "compatibility": _record({"alembic_heads_live": _l_items("$defs.head"),
                                      "alembic_heads_image": _l_null("$defs.heads"),
                                      "migration_files": _l_map("sha256"), "mismatch": _l_null("$defs.mismatch")}),
            "payloads": _l_items("$defs.payload"),
            "workspace": _record({"differs_from_deployed": _L_BOOL, "payload": _l_null("$defs.relative_path"),
                                  "unsupported_entries": _l_items("string")}),
            "exclusions": _l_items("$defs.exclusion"),
            "manual_prerequisites": _l_items("string"),
            "derived_from": _l_null("$defs.bundle_id"),
            "purge": _l_null("$defs.purge_section"),
            "legacy": _l_null("$defs.legacy")}),
        "verification_record": _record({
            "schema_version": {"const": 1}, "verification_id": _l_str(_L_VERIFICATION),
            "bundle_id": _l_str(_L_BUNDLE), "bundle_kind": {"enum": ["checkpoint", "purge-bundle"]},
            "manifest_sha256": _l_str(_L_SHA), "level": {"enum": list(VERIFICATION_LEVELS)},
            "result": {"enum": ["passed", "failed"]},
            "target": _record({"kind": {"enum": ["isolated-database", "none"]}, "names": _l_items("$defs.pg_name"),
                               "removed": _L_BOOL}),
            "checks": _l_items("$defs.check"), "producer": _L_PRODUCER, "strategy": _L_STRATEGY,
            "environment": _record({"server_version_num": _l_null("scalar"), "engine_id": _l_str(None, 1, 256),
                                    "compose_version": _l_null("string")}),
            "operation_id": _l_str(_L_OPERATION), "started_at": _l_str(_L_STAMP), "finished_at": _l_str(_L_STAMP)}),
    },
}


def _match(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern[1:-1], value) is not None


def relative_path_problem(value):
    """Why ``value`` is not a canonical bundle-relative path (section 2.4), or None."""
    if not _match(_L_RELATIVE, value):
        return f"{value!r} is not a relative path of [A-Za-z0-9._/-]"
    if value.startswith("/") or value.endswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        return f"{value!r} is not canonical (leading or trailing '/', empty, '.' or '..' component)"
    return None


def _count(value):
    return type(value) is int and value >= 0


def _heads_problems(value, where):
    if not isinstance(value, list):
        return [f"{where}: expected an array of Alembic heads"]
    problems = [f"{where}[{index}]: not an Alembic head" for index, item in enumerate(value)
                if not _match(_L_HEAD, item)]
    if not problems and (value != sorted(set(value)) or len(value) > 16):
        problems.append(f"{where}: heads must be sorted, unique and at most 16")
    return problems


def _image_problems(image, where):
    if image is None:
        return []
    platform = image.get("platform")
    if platform is not None and not _PLATFORM_RE.match(platform):
        return [f"{where}.platform: {platform!r} is not os/architecture[/variant]"]
    return []


def _manifest_problems(m):
    problems = []
    add = problems.append
    legacy = m["legacy"]
    is_legacy = legacy is not None
    kind, cls, reason = m["bundle_kind"], m["capture_class"], m["reason"]
    excluded = {item["item"] for item in m["exclusions"]}
    if _match(_L_PURGE, m["bundle_id"]) != (kind == "purge-bundle"):
        add("bundle_id does not match bundle_kind")
    # Rule 12 (array limits) first: the later rules iterate these arrays.
    for key, limit in (("stores", 256), ("roles", 1024), ("exclusions", 256), ("manual_prerequisites", 64),
                       ("consistency_groups", 256), ("payloads", 4096)):
        if len(m[key]) > limit:
            add(f"{key}: more than {limit} entries (rule 12)")
    if len(m["compatibility"]["migration_files"]) > 20000:
        add("compatibility.migration_files: more than 20000 entries (rule 12)")
    if len(m["compatibility"]["alembic_heads_live"]) > 16:
        add("compatibility.alembic_heads_live: more than 16 heads (rule 12)")
    if len(m["workspace"]["unsupported_entries"]) > 200:
        add("workspace.unsupported_entries: more than 200 entries")
    for index, store in enumerate(m["stores"]):
        if len(store["extensions"]) > 256:
            add(f"stores[{index}].extensions: more than 256 entries (rule 12)")
        if len(store["alembic_heads"]) > 16:
            add(f"stores[{index}].alembic_heads: more than 16 heads (rule 12)")
    compatibility = m["compatibility"]
    if compatibility["alembic_heads_image"] is not None:
        problems += _heads_problems(compatibility["alembic_heads_image"], "compatibility.alembic_heads_image")
    mismatch = compatibility["mismatch"]
    if mismatch is not None and mismatch["image_heads"] is not None:
        problems += _heads_problems(mismatch["image_heads"], "compatibility.mismatch.image_heads")
    # Rule 1: the payload inventory.
    payloads = m["payloads"]
    if not payloads:
        add("payloads: empty (rule 1)")
    seen = set()
    for index, payload in enumerate(payloads):
        where = f"payloads[{index}]"
        problem = relative_path_problem(payload["path"])
        if problem:
            add(f"{where}.path: {problem}")
        if payload["path"] in seen:
            add(f"{where}.path: duplicate path {payload['path']!r} (rule 1)")
        seen.add(payload["path"])
        kind_ = payload["type"]
        if kind_ != "state_file" and payload["size"] < 1:
            add(f"{where}: an empty {kind_} payload (rule 1)")
        if kind_ in ("database_dump", "source_archive", "image_archive") and payload["sha256"] == EMPTY_SHA256:
            add(f"{where}: the SHA-256 of an empty file is never a {kind_} (rule 1)")
        sensitive = kind_ in SENSITIVE_PAYLOAD_TYPES or (is_legacy and legacy["format"] == 1
                                                         and kind_ == "source_archive")
        if sensitive and not payload["sensitive"]:
            add(f"{where}: a {kind_} payload must be sensitive (rule 1)")
        for key in ("expanded_bytes", "members"):
            if payload[key] is not None and not _count(payload[key]):
                add(f"{where}.{key}: not a non-negative integer")
    by_path = {payload["path"]: payload for payload in payloads}
    types = {payload["type"] for payload in payloads}
    # Rule 2: stores and their payloads.
    stores = m["stores"]
    store_ids = [store["store_id"] for store in stores]
    if not stores:
        add("stores: empty; an empty checksum map is never a stateful recovery (rule 2)")
    if len(set(store_ids)) != len(store_ids):
        add("stores: duplicate store_id")
    referenced = {}
    quiescence = m["quiescence"]
    for index, store in enumerate(stores):
        where = f"stores[{index}]"
        if store["store_id"] != "postgresql:" + store["database"]:
            add(f"{where}.store_id: must be postgresql:<database>")
        for key, kind_ in (("dump", "database_dump"), ("list", "database_list")):
            path = store[key]
            if path is None:
                continue
            problem = relative_path_problem(path)
            if problem:
                add(f"{where}.{key}: {problem}")
            payload = by_path.get(path)
            if payload is None or payload["type"] != kind_ or payload["store"] != store["store_id"]:
                add(f"{where}.{key}: no {kind_} payload {path!r} of this store (rule 2)")
            referenced[path] = store["store_id"]
        if store["encoding"] is not None and not 1 <= len(store["encoding"]) <= 32:
            add(f"{where}.encoding: must be 1-32 characters")
        for key in ("collate", "ctype"):
            if store[key] is not None and not 1 <= len(store[key]) <= 128:
                add(f"{where}.{key}: must be 1-128 characters")
        if store["row_counts"] is not None and (quiescence is None or quiescence["mode"] != "writers_stopped"):
            add(f"{where}.row_counts: recorded without quiescence mode writers_stopped (rule 9)")
    for index, payload in enumerate(payloads):
        if payload["type"] in ("database_dump", "database_list"):
            if payload["store"] not in store_ids or referenced.get(payload["path"]) != payload["store"]:
                add(f"payloads[{index}]: {payload['type']} {payload['path']!r} names no store that references it "
                    "(rule 2)")
        elif payload["store"] is not None:
            add(f"payloads[{index}].store: only database dumps and lists name a store")
    for section, kind_ in (("source", "source_archive"), ("workspace", "workspace_archive")):
        path = m[section]["payload"]
        if path is not None:
            problem = relative_path_problem(path)
            if problem:
                add(f"{section}.payload: {problem}")
            if path not in by_path or by_path[path]["type"] != kind_:
                add(f"{section}.payload: no {kind_} payload {path!r} (rule 2)")
    # Rule 3: one active store; every store in exactly the one group it names.
    if len([store for store in stores if store["role"] == "active"]) != 1:
        add("stores: exactly one active store is required (rule 3)")
    groups = {}
    for index, group in enumerate(m["consistency_groups"]):
        if group["group_id"] in groups:
            add(f"consistency_groups[{index}]: duplicate group_id")
        groups[group["group_id"]] = group
        for store_id in group["stores"]:
            if store_id not in store_ids:
                add(f"consistency_groups[{index}]: unknown store {store_id!r} (rule 3)")
    for store in stores:
        holders = [group["group_id"] for group in m["consistency_groups"] if store["store_id"] in group["stores"]]
        if holders != [store["consistency_group"]]:
            add(f"store {store['store_id']}: must be in exactly the one consistency group it names (rule 3)")
    # Rule 9: quiescence and claims (claim none is the legacy branch and is exempt from the multi-store rule).
    if quiescence is not None:
        stopped = quiescence["backend"] in ("stopped", "absent") and quiescence["frontend"] in ("stopped", "absent")
        if (quiescence["mode"] == "writers_stopped") != stopped:
            add("quiescence: mode writers_stopped exactly when backend and frontend are stopped or absent (rule 9)")
    for index, group in enumerate(m["consistency_groups"]):
        claim = group["claim"]
        if (claim == "none") != is_legacy:
            add(f"consistency_groups[{index}]: claim none exactly for a legacy manifest (rule 9)")
        if claim == "transactional-single-store" and len(group["stores"]) != 1:
            add(f"consistency_groups[{index}]: transactional-single-store needs a one-store group (rule 9)")
        if claim != "none" and len(group["stores"]) > 1 and claim != "writers-stopped":
            add(f"consistency_groups[{index}]: a multi-store group claims writers-stopped (rule 9)")
        if claim == "writers-stopped" and (quiescence is None or quiescence["mode"] != "writers_stopped"):
            add(f"consistency_groups[{index}]: writers-stopped needs quiescence mode writers_stopped (rule 9)")
    # Images and the exclusions that stand for a missing artifact.
    images = m["images"]
    for service in ("backend", "frontend", "db"):
        problems += _image_problems(images[service], f"images.{service}")
        if images[service] is None and "image:" + service not in excluded:
            add(f"images.{service}: null without the exclusion image:{service}")
    if m["source"]["payload"] is None and "source" not in excluded:
        add("source.payload: null without the exclusion source")
    # Rule 4: healthy checkpoint.
    if cls == "healthy_checkpoint":
        if mismatch is not None:
            add("healthy_checkpoint: compatibility.mismatch must be null (rule 4)")
        if images["backend"] is None or images["frontend"] is None:
            add("healthy_checkpoint: images.backend and images.frontend are required (rule 4)")
        if not is_legacy:
            if images["db"] is None:
                add("healthy_checkpoint: images.db is required (rule 4)")
            if any(image is not None and image["platform"] is None for image in images.values()):
                add("healthy_checkpoint: every image needs a platform (rule 4)")
            if compatibility["alembic_heads_image"] != compatibility["alembic_heads_live"]:
                add("healthy_checkpoint: alembic_heads_image must equal alembic_heads_live (rule 4)")
            if m["source"]["origin"] not in ("deployment-artifact", "protected-store", "workspace-proven"):
                add("healthy_checkpoint: the source must be the exact deployed source (rule 4)")
            if m["deployment"] is None and "deployment-record" not in excluded:
                add("healthy_checkpoint: deployment or the exclusion deployment-record is required (rule 4)")
            if kind == "checkpoint" and not compatibility["migration_files"]:
                add("healthy_checkpoint: migration_files is empty (rule 4)")
        else:
            if images["db"] is not None or "image:db" not in excluded:
                add("legacy healthy_checkpoint: images.db is null with the exclusion image:db (rule 4)")
            if compatibility["alembic_heads_image"] is not None:
                add("legacy healthy_checkpoint: alembic_heads_image is null (rule 4)")
            if m["source"]["origin"] != "legacy-claim":
                add("legacy healthy_checkpoint: source.origin is legacy-claim (rule 4)")
            if m["deployment"] is not None or "deployment-record" not in excluded:
                add("legacy healthy_checkpoint: deployment is null with the exclusion deployment-record (rule 4)")
            if kind == "checkpoint" and (not compatibility["migration_files"] or legacy["format"] != 2):
                add("legacy healthy_checkpoint: a format 2 migration fingerprint is required (rule 4)")
    # Rule 5: emergency preservation.
    if cls == "emergency_preservation":
        if kind != "checkpoint":
            add("emergency_preservation: only a checkpoint (rule 5)")
        if any(store["list"] is None for store in stores):
            add("emergency_preservation: every store needs a dump and a list (rule 5)")
        if not is_legacy and reason not in ("emergency-manual", "before-rollback"):
            add("emergency_preservation: reason must be emergency-manual or before-rollback (rule 5)")
    # Rule 6: partial.
    if cls == "partial":
        if not excluded & REQUIRED_ARTIFACT_ITEMS:
            add("partial: needs an exclusion naming a required artifact (rule 6)")
        if kind == "purge-bundle":
            add("partial: never a purge bundle (rule 6)")
    # Rule 7: purge bundle.
    if kind == "purge-bundle":
        if cls != "healthy_checkpoint":
            add("purge-bundle: class must be healthy_checkpoint (rule 7)")
        if m["purge"] is None or not _match(_L_CHECKPOINT, m["derived_from"]):
            add("purge-bundle: purge and a checkpoint derived_from are required (rule 7)")
        for required in ("source_archive", "postgres_globals", "checkpoint_history", "image_archive"):
            if required not in types:
                add(f"purge-bundle: a {required} payload is required (rule 7)")
        for service in ("backend", "frontend"):
            if images[service] is not None and not images[service]["archived"]:
                add(f"purge-bundle: images.{service} must be archived (rule 7)")
        if not is_legacy:
            if "config_env" not in types:
                add("purge-bundle: a config_env payload is required (rule 7)")
            if not ({"deployment_record", "compose_resolved"} <= types) and "deployment-record" not in excluded:
                add("purge-bundle: deployment_record and compose_resolved payloads or the exclusion "
                    "deployment-record are required (rule 7)")
            if images["db"] is not None and not images["db"]["archived"]:
                add("purge-bundle: images.db must be archived (rule 7)")
            if quiescence is None or quiescence["mode"] != "writers_stopped":
                add("purge-bundle: quiescence writers_stopped is required (rule 7)")
            if len(m["consistency_groups"]) != 1 or m["consistency_groups"][0]["claim"] != "writers-stopped" \
                    or sorted(m["consistency_groups"][0]["stores"]) != sorted(store_ids):
                add("purge-bundle: one writers-stopped consistency group holding every store (rule 7)")
            if any(store["row_counts"] is None for store in stores):
                add("purge-bundle: every store needs row_counts (rule 7)")
        else:
            env_excluded = (legacy["format"] == 1 and "config_env" in excluded
                            and LEGACY_LIMITATIONS["format-1-env"] in legacy["limitations"])
            if "config_env" not in types and not env_excluded:
                add("legacy purge-bundle: a config_env payload or the format 1 exclusion config_env (rule 7)")
            if types & {"deployment_record", "compose_resolved"} or "deployment-record" not in excluded:
                add("legacy purge-bundle: no deployment record payloads, with the exclusion deployment-record "
                    "(rule 7)")
            if images["db"] is not None or "image:db" not in excluded:
                add("legacy purge-bundle: images.db is null with the exclusion image:db (rule 7)")
    # Rule 8: checkpoint (the backups share is group-readable).
    if kind == "checkpoint":
        if m["purge"] is not None or m["derived_from"] is not None:
            add("checkpoint: purge and derived_from must be null (rule 8)")
        forbidden = sorted(types & CHECKPOINT_FORBIDDEN_TYPES)
        if forbidden:
            add("checkpoint: the group-readable checkpoint carries no " + ", ".join(forbidden) + " (rule 8)")
        if any(image is not None and image["archived"] for image in images.values()):
            add("checkpoint: images are never archived in a checkpoint (rule 8)")
    # Rule 10: provenance and origin.
    source = m["source"]
    if (source["provenance"] == "git_commit") != (source["commit"] is not None and source["remote"] is not None):
        add("source: provenance git_commit exactly when commit and remote are set (rule 10)")
    if source["provenance"] == "unknown" and source["commit"] is not None:
        add("source: unknown provenance names no commit (rule 10)")
    if source["origin"] in ("workspace-unverified", "legacy-claim") and source["provenance"] != "unknown":
        add(f"source: origin {source['origin']} has unknown provenance (rule 10)")
    if (source["origin"] == "none") != (source["payload"] is None):
        add("source: origin none exactly when the source payload is null (rule 10)")
    if (source["origin"] == "legacy-claim") != is_legacy:
        add("source: origin legacy-claim exactly for a legacy manifest (rule 10)")
    hashed = source["origin"] in ("deployment-artifact", "protected-store", "workspace-proven", "workspace-unverified")
    if (source["entries_sha256"] is not None) != hashed:
        add("source: entries_sha256 exactly for a tree pf archived (rule 10)")
    # Rule 11: legacy-unknowable fields.
    if is_legacy != (reason == "legacy"):
        add("reason legacy exactly for a legacy manifest (rule 11)")
    postgresql = m["postgresql"]
    if postgresql["server_version_num"] is not None and not _count(postgresql["server_version_num"]):
        add("postgresql.server_version_num: not a non-negative integer")
    instance = m["source_instance"]
    if is_legacy:
        limitations = set(legacy["limitations"])
        required = [LEGACY_LIMITATIONS[key] for key in _LEGACY_REQUIRED_LIMITATIONS]
        if None in (instance["instance_id"], instance["slug"], instance["workspace"]):
            required.append(LEGACY_LIMITATIONS["source-instance"])
        for text in required:
            if text not in limitations:
                add(f"legacy.limitations: missing {text!r} (rule 11)")
        if source["provenance"] != "unknown":
            add("legacy: a legacy claim is never a proof; provenance is unknown (rule 11)")
        if m["producer"] is not None or quiescence is not None or postgresql["server_version_num"] is not None \
                or postgresql["image_id"] is not None or m["roles"]:
            add("legacy: producer, quiescence, server version, database image and roles are null (rule 11)")
        if any(store["encoding"] is not None or store["collate"] is not None or store["ctype"] is not None
               or store["extensions"] or store["row_counts"] is not None for store in stores):
            add("legacy: store locale, extensions and row counts are null (rule 11)")
    else:
        if m["producer"] is None or quiescence is None or postgresql["server_version_num"] is None \
                or postgresql["image_id"] is None:
            add("producer, quiescence, postgresql.server_version_num and postgresql.image_id are required (rule 11)")
        if None in (instance["instance_id"], instance["slug"], instance["workspace"]):
            add("source_instance: instance_id, slug and workspace are required (rule 11)")
        for store in stores:
            if store["owner"] is None or not store["encoding"] or not store["collate"] or not store["ctype"]:
                add(f"store {store['store_id']}: owner, encoding, collate and ctype are required (rule 11)")
    return problems


def _deployment_problems(record):
    problems = []
    add = problems.append
    source = record["source"]
    if source["provenance"] == "not_applicable":
        add("source-required: the PartFlow profile deploys a source tree (not_applicable is reserved)")
    if (source["provenance"] == "git_commit") != (source["commit"] is not None and source["remote"] is not None):
        add("source: provenance git_commit exactly when commit and remote are set")
    if source["provenance"] == "unknown" and source["commit"] is not None:
        add("source: unknown provenance names no commit")
    heads = record["database"]["alembic_heads"]
    if len(heads) != 1:
        add("database.alembic_heads: exactly one head")
    project = record["compose_project"]
    for service in ("backend", "frontend", "db"):
        image = record["images"][service]
        problems += _image_problems(image, f"images.{service}")
        if image["platform"] is None:
            add(f"images.{service}.platform: required")
        if image["archived"]:
            add(f"images.{service}.archived: false in a deployment record")
    for service in ("backend", "frontend"):
        if not record["images"][service]["reference"].startswith(f"{project}-{service}:"):
            add(f"images.{service}.reference: must be {project}-{service}:<tag>")
    if record["images"]["db"]["reference"] != DB_IMAGE_REFERENCE:
        add(f"images.db.reference: must be {DB_IMAGE_REFERENCE}")
    if record["helpers"]:
        add("helpers: the PartFlow profile declares no helper image")
    if (record["restored_from"] is not None) != (record["operation"]["kind"] == "restore-instance"):
        add("restored_from: set exactly for restore-instance")
    return problems


def _verification_problems(record):
    problems = []
    add = problems.append
    checks = record["checks"]
    target = record["target"]
    if not checks:
        add("checks: at least one check")
    names = [check["name"] for check in checks]
    if len(set(names)) != len(names):
        add("checks: duplicate check name")
    level = record["level"]
    if level == "captured" and target["kind"] != "none":
        add("captured: target kind none")
    if level == "data_restore_verified":
        if target["kind"] != "isolated-database" or not target["names"]:
            add("data_restore_verified: an isolated-database target with at least one name")
        stores = [name[len("restore:"):] for name in names if name.startswith("restore:")]
        if not stores:
            add("data_restore_verified: at least one restore:<store_id> check")
        by_name = {check["name"]: check for check in checks}
        for store in stores:
            for prefix in ("restore", "heads", "locale", "rows"):
                check = by_name.get(f"{prefix}:{store}")
                if check is None:
                    add(f"data_restore_verified: check {prefix}:{store} is missing")
                elif prefix in ("restore", "heads") and check["result"] == "not_run":
                    add(f"data_restore_verified: check {prefix}:{store} must run")
    passed = all(check["result"] in ("passed", "not_run") for check in checks) \
        and any(check["result"] == "passed" for check in checks)
    if (record["result"] == "passed") != passed:
        add("result: passed exactly when every check passed or did not run and one passed")
    server = record["environment"]["server_version_num"]
    if server is not None and not _count(server):
        add("environment.server_version_num: not a non-negative integer")
    if record["finished_at"] < record["started_at"]:
        add("finished_at precedes started_at")
    return problems


def _plan_problems(plan):
    problems = []
    add = problems.append
    effects = plan["effects"]
    if len(effects) > 1024:
        add("effects: more than 1024 entries")
    if len(plan["coverage"]) > 256:
        add("coverage: more than 256 entries")
    ids = [effect["effect_id"] for effect in effects]
    if ids != sorted(set(ids)):
        add("effects: effect ids must be unique and increasing")
    if (plan["kind"] in ("purge", "abort-deploy")) != (plan["resources"]["deletion_plan_sha256"] is not None):
        add("resources.deletion_plan_sha256: set exactly for purge and abort-deploy")
    if (plan["source"]["provenance"] == "git_commit") != (plan["source"]["commit"] is not None):
        add("source: provenance git_commit exactly when commit is set")
    for key, image in plan["images"].items():
        if key not in ("backend", "frontend", "db"):
            add(f"images.{key}: not a PartFlow service")
        elif image["platform"] is None:
            add(f"images.{key}.platform: required")
        else:
            problems += _image_problems(image, f"images.{key}")
    return problems


def _journal_problems(journal):
    problems = []
    add = problems.append
    phase = journal["phase"]
    if phase not in JOURNAL_PHASES[journal["kind"]]:
        add(f"phase {phase} is not a phase of {journal['kind']}")
    if (journal["result"] is not None) != (phase in TERMINAL_PHASES):
        add("result: set exactly in a terminal phase")
    unresolved = journal["unresolved_effect"]
    if unresolved is not None and not any(effect["effect_id"] == unresolved
                                          and effect["state"] in ("partial", "unknown")
                                          for effect in journal["effects"]):
        add("unresolved_effect: names no partial or unknown effect")
    if any(approval["plan_sha256"] != journal["plan_sha256"] for approval in journal["approvals"]):
        add("approvals: every approval binds plan_sha256")
    return problems


_LIFECYCLE_RULES = {"recovery_manifest": _manifest_problems, "deployment_record": _deployment_problems,
                    "verification_record": _verification_problems, "operation_plan": _plan_problems,
                    "operation_journal": _journal_problems}


def lifecycle_problems(value, name):
    """The cross-field rules of section 2.4 for one schema-valid record (run after pf_install.validate_marked); []
    when valid. Pure. A value that is not schema-valid is reported, never repaired."""
    if name not in _LIFECYCLE_RULES:
        raise ConfigError(f"lifecycle-record-unknown: {name!r}")
    try:
        return _LIFECYCLE_RULES[name](value)
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        return [f"$: cross-field rules not evaluated ({type(exc).__name__}: {exc}); validate the schema first"]


# ------------------------------------------------------------ legacy manifests (PF-A3.1 section 3.7)

_LEGACY_FIXED_TYPES = {
    "source.tar.gz": "source_archive", "workspace.tar.gz": "workspace_archive", "database.dump": "database_dump",
    "database.list": "database_list", "postgres-globals.sql": "postgres_globals",
    "revision-checkpoints.tar.gz": "checkpoint_history", "images.tar": "image_archive",
    "configuration/.env": "config_env", "configuration/pf-config.json": "admin_config",
}
_LEGACY_DATABASE_FILE = re.compile(r"databases/[A-Za-z0-9._-]+\.(dump|list)\Z")
_PURGE_ONLY_TYPES = frozenset({"postgres_globals", "checkpoint_history", "image_archive", "config_env",
                               "admin_config", "state_file"})


def payload_type_for(name):
    """The payload type of one legacy ``checksums`` name (the fixed section 3.7 map), or None."""
    if not isinstance(name, str):
        return None
    if name in _LEGACY_FIXED_TYPES:
        return _LEGACY_FIXED_TYPES[name]
    match = _LEGACY_DATABASE_FILE.match(name)
    if match is not None and relative_path_problem(name) is None:
        return "database_dump" if match.group(1) == "dump" else "database_list"
    if name.startswith("state/") and name[len("state/"):] in RESTORABLE_STATE_FILES:
        return "state_file"
    return None


def _legacy_image(entry, *, archived):
    if not isinstance(entry, dict) or not _match(_L_IMAGE_REF, entry.get("reference")) \
            or not _match(_L_IMAGE_ID, entry.get("id")):
        return None
    return {"reference": entry["reference"], "id": entry["id"], "platform": None, "repo_digests": [],
            "archived": bool(archived)}


def _legacy_strings(value):
    return list(value) if isinstance(value, list) and all(isinstance(item, str) for item in value) else None


def _legacy_stores(legacy, *, purge, types, database, heads, owner, limitations, refuse):
    """The migrated stores and the dump/list payload -> store map (section 3.7 table)."""
    stores, payload_store = [], {}
    if purge:
        databases = legacy.get("databases")
        if not isinstance(databases, list) or not databases:
            raise refuse("databases is missing or empty")
        names = set()
        for item in databases:
            if not isinstance(item, dict) or not _match(_L_PG_NAME, item.get("name")) \
                    or type(item.get("allow_connections")) is not bool \
                    or types.get(item.get("dump")) != "database_dump":
                raise refuse("a databases entry is malformed or names no dump payload")
            store_heads = item.get("heads", [])
            if not isinstance(store_heads, list) or len(store_heads) > 16 \
                    or not all(_match(_L_HEAD, head) for head in store_heads):
                raise refuse(f"database {item['name']}: heads are malformed")
            if item["name"] in names:
                raise refuse(f"database {item['name']} is listed twice")
            names.add(item["name"])
            active = item["name"] == database
            store_id = "postgresql:" + item["name"]
            listing = "databases/active.list" if active and "databases/active.list" in types else None
            if not item["allow_connections"] and not store_heads \
                    and LEGACY_LIMITATIONS["retained-heads"] not in limitations:
                limitations.append(LEGACY_LIMITATIONS["retained-heads"])
            stores.append({"store_id": store_id, "database": item["name"],
                           "role": "active" if active else "retained", "allow_connections": item["allow_connections"],
                           "owner": owner if active else None,
                           "alembic_heads": sorted(set(heads if active else store_heads)),
                           "dump": item["dump"], "list": listing})
            payload_store[item["dump"]] = store_id
            if listing:
                payload_store[listing] = store_id
        if database not in names:
            raise refuse("the active database has no databases entry")
    else:
        if types.get("database.dump") != "database_dump":
            raise refuse("a checkpoint stores its database as database.dump")
        listing = "database.list" if "database.list" in types else None
        store_id = "postgresql:" + database
        stores.append({"store_id": store_id, "database": database, "role": "active", "allow_connections": True,
                       "owner": owner, "alembic_heads": sorted(set(heads)), "dump": "database.dump",
                       "list": listing})
        payload_store["database.dump"] = store_id
        if listing:
            payload_store[listing] = store_id
    for name, kind in types.items():
        if kind in ("database_dump", "database_list") and name not in payload_store:
            raise refuse(f"payload {name!r} belongs to no listed database")
    for store in stores:
        store.update(kind="postgresql_logical", strategy=dict(STRATEGY), encoding=None, collate=None, ctype=None,
                     extensions=[], row_counts=None, consistency_group="legacy")
    return stores, payload_store


def _legacy_purge_section(legacy, refuse):
    state_files = _legacy_strings(legacy.get("state_files", []))
    if state_files is None or any(name not in RESTORABLE_STATE_FILES for name in state_files):
        raise refuse("state_files is not a list of restorable state file names")
    resources = legacy.get("resources_before_purge", {})
    if not isinstance(resources, dict):
        raise refuse("resources_before_purge is malformed")
    section = {}
    for key in ("containers", "volumes", "networks", "images"):
        values = _legacy_strings(resources.get(key, []))
        if values is None:
            raise refuse(f"resources_before_purge.{key} is malformed")
        section[key] = values
    lists = {}
    for key in ("saved_image_refs", "missing_historical_image_refs"):
        values = _legacy_strings(legacy.get(key, []))
        if values is None:
            raise refuse(f"{key} is malformed")
        lists[key] = values
    scope = legacy.get("restore_scope", "")
    return {"resources_before_purge": section, "saved_image_refs": lists["saved_image_refs"],
            "missing_historical_image_refs": lists["missing_historical_image_refs"], "state_files": state_files,
            "restore_scope": scope if isinstance(scope, str) else ""}


def migrate_legacy_manifest(legacy, *, legacy_sha256, bundle_kind, payload_sizes, unlisted_entries=()):
    """(manifest, record): the schema 1 RecoveryManifest of a verified legacy format 1/2 manifest (section 3.7).

    Pure and deterministic: the result depends only on the parsed legacy value, its on-disk SHA-256 and the verified
    payload sizes ({name: size}); no clock, daemon or filesystem. ``unlisted_entries`` (folder names the reader saw
    and never opened) are copied into the migration record only. Raises ConfigError("manifest-schema-unsupported:
    ...") when the legacy document lacks or malforms an input the migration needs; nothing is invented.
    """
    bundle_id = legacy.get("id") if isinstance(legacy, dict) else None

    def refuse(detail):
        return ConfigError(f"manifest-schema-unsupported: {bundle_id}: {detail}")

    if not isinstance(legacy, dict):
        raise refuse("the legacy manifest is not an object")
    fmt = legacy.get("format")
    if type(fmt) is not int or fmt not in (1, 2):
        raise refuse("format is not 1 or 2")
    if legacy.get("status") != "complete":
        raise refuse("status is not complete")
    purge = bundle_kind == "purge-bundle"
    if not _match(_L_PURGE if purge else _L_CHECKPOINT, bundle_id):
        raise refuse("id does not match the bundle kind")
    if purge and legacy.get("kind") != "partflow-purge-recovery":
        raise refuse("kind is not partflow-purge-recovery")
    if not _match(_L_STAMP, legacy.get("created_at")):
        raise refuse("created_at is not a UTC stamp")
    checksums = legacy.get("checksums")
    if not isinstance(checksums, dict) or not checksums:
        raise refuse("checksums is missing or empty; an empty checksum map is never a stateful recovery")
    types = {}
    for name, digest in checksums.items():
        kind = payload_type_for(name)
        if kind is None:
            raise refuse(f"payload {name!r} has no payload type")
        if not _match(_L_SHA, digest):
            raise refuse(f"payload {name!r} has no SHA-256")
        if not _count(payload_sizes.get(name)):
            raise refuse(f"payload {name!r} was not verified")
        if kind in _PURGE_ONLY_TYPES and not purge:
            raise refuse(f"payload {name!r} does not belong to a checkpoint")
        types[name] = kind
    if "source.tar.gz" not in types:
        raise refuse("no source.tar.gz payload")
    if "database_dump" not in types.values():
        raise refuse("no database dump payload")
    project, repository = legacy.get("project"), legacy.get("repository")
    environment, database = legacy.get("environment"), legacy.get("database")
    major, heads = legacy.get("postgres_major"), legacy.get("database_heads")
    if not _match(_L_SLUG, project):
        raise refuse("project is missing or malformed")
    if not isinstance(repository, str) or not 1 <= len(repository) <= 300:
        raise refuse("repository is missing or malformed")
    if not isinstance(environment, str) or not 1 <= len(environment) <= 64:
        raise refuse("environment is missing or malformed")
    if not _match(_L_PG_NAME, database):
        raise refuse("database is missing or malformed")
    if type(major) is not int or major < 9:
        raise refuse("postgres_major is missing or malformed")
    if not isinstance(heads, list) or len(heads) > 16 or not all(_match(_L_HEAD, head) for head in heads):
        raise refuse("database_heads is missing or malformed")
    owner = legacy["database_user"] if _match(_L_PG_NAME, legacy.get("database_user")) else None
    limitations = [LEGACY_LIMITATIONS["no-verification"], LEGACY_LIMITATIONS["provenance"]]
    exclusions = [{"item": "deployment-record",
                   "reason": "legacy manifest: no deployment record (the bundle predates PF-A3.1)"},
                  {"item": "image:db", "reason": f"db image identity not recorded by format {fmt}"}]
    # Images: the legacy IDs, never a daemon observation (R3).
    saved = (_legacy_strings(legacy.get("saved_image_refs")) or []) if purge else []
    recorded = legacy.get("active_images" if purge else "images")
    recorded = recorded if isinstance(recorded, dict) else {}
    images = {}
    for service in ("backend", "frontend"):
        entry = recorded.get(service)
        images[service] = _legacy_image(entry, archived=purge and isinstance(entry, dict)
                                        and entry.get("reference") in saved)
        if images[service] is None:
            exclusions.append({"item": "image:" + service,
                               "reason": f"the legacy manifest has no well-formed {service} image entry"})
    images["db"] = None
    stores, payload_store = _legacy_stores(legacy, purge=purge, types=types, database=database, heads=heads,
                                           owner=owner, limitations=limitations, refuse=refuse)
    workspace_archive = legacy.get("workspace_archive")
    if workspace_archive is not None and types.get(workspace_archive) != "workspace_archive":
        raise refuse("workspace_archive names no workspace archive payload")
    if purge:
        root = legacy.get("root")
        instance = {"instance_id": legacy.get("instance_id") if _match(_UUID, legacy.get("instance_id")) else None,
                    "slug": legacy.get("slug") if _match(_L_SLUG, legacy.get("slug")) else None,
                    "compose_project": project, "environment": environment, "repository": repository,
                    "workspace": root if isinstance(root, str) and pf_instance.canonical_path_error(root) is None
                    else None}
        differs = workspace_archive is not None
    else:
        instance = {"instance_id": None, "slug": None, "compose_project": project, "environment": environment,
                    "repository": repository, "workspace": None}
        differs = legacy.get("workspace_differs_from_deployed")
        differs = differs if type(differs) is bool else workspace_archive is not None
    if None in (instance["instance_id"], instance["slug"], instance["workspace"]):
        limitations.append(LEGACY_LIMITATIONS["source-instance"])
    limitations += [LEGACY_LIMITATIONS[key] for key in ("producer", "quiescence", "server-version", "roles",
                                                         "locale-extensions")]
    migration = {}
    if purge:
        limitations.append(LEGACY_LIMITATIONS["migration-files"])
    elif fmt == 2:
        files = legacy.get("migration_files")
        if not isinstance(files, dict) or not all(isinstance(key, str) and _match(_L_SHA, value)
                                                  for key, value in files.items()):
            raise refuse("migration_files is missing or malformed")
        migration = dict(files)
    else:
        exclusions.append({"item": "migration-files", "reason": "format 1 recorded no migration file fingerprint"})
        limitations.append(LEGACY_LIMITATIONS["migration-files"])
    if purge and fmt == 1 and "configuration/.env" not in types:
        exclusions.append({"item": "config_env", "reason": LEGACY_LIMITATIONS["format-1-env"]})
        limitations.append(LEGACY_LIMITATIONS["format-1-env"])
    payloads = [{"path": name, "type": types[name], "size": payload_sizes[name], "sha256": checksums[name],
                 "store": payload_store.get(name),
                 "sensitive": types[name] in SENSITIVE_PAYLOAD_TYPES or (fmt == 1 and types[name] == "source_archive"),
                 "expanded_bytes": None, "members": None, "members_sha256": None} for name in sorted(types)]
    prerequisites = [f"PostgreSQL major {major} server",
                     "legacy bundle: verify locale and extensions on the restored candidate"]
    purge_section = derived_from = None
    if purge:
        prerequisites.append("roles in postgres-globals.sql are evidence; recreate any role other than "
                             f"{owner or database} manually")
        derived_from = legacy.get("active_checkpoint")
        if not _match(_L_CHECKPOINT, derived_from):
            raise refuse("active_checkpoint is missing or malformed")
        purge_section = _legacy_purge_section(legacy, refuse)
    # Class (section 3.7): a purge bundle is complete (rule 7, checked by the reader) or not restorable.
    claimed_reason = legacy.get("reason") if isinstance(legacy.get("reason"), str) else None
    verified_claim = legacy.get("source_verified")
    lists_complete = all(store["list"] is not None for store in stores)
    images_complete = images["backend"] is not None and images["frontend"] is not None
    if purge:
        capture_class = "healthy_checkpoint"
    elif (fmt == 2 and claimed_reason != "before-rollback" and verified_claim is True
          and legacy.get("restore_test") == "passed" and lists_complete and images_complete and migration):
        capture_class = "healthy_checkpoint"
    elif lists_complete and (claimed_reason == "before-rollback" or verified_claim is not True) and migration:
        capture_class = "emergency_preservation"
    else:
        capture_class = "partial"
    revision = legacy.get("source_revision")
    restore_test = legacy.get("restore_test")
    manifest = {
        "schema_version": 1, "bundle_id": bundle_id, "bundle_kind": bundle_kind, "created_at": legacy["created_at"],
        "reason": "legacy", "capture_class": capture_class, "source_instance": instance, "producer": None,
        "quiescence": None,
        "source": {"provenance": "unknown", "commit": None, "remote": None, "origin": "legacy-claim",
                   "payload": "source.tar.gz", "entries_sha256": None},
        "deployment": None, "images": images,
        "postgresql": {"server_version_num": None, "major": major, "image_id": None},
        "roles": [], "stores": stores,
        "consistency_groups": [{"group_id": "legacy", "stores": [store["store_id"] for store in stores],
                                "claim": "none"}],
        "compatibility": {"alembic_heads_live": sorted(set(heads)), "alembic_heads_image": None,
                          "migration_files": migration, "mismatch": None},
        "payloads": payloads,
        "workspace": {"differs_from_deployed": differs, "payload": workspace_archive, "unsupported_entries": []},
        "exclusions": exclusions, "manual_prerequisites": prerequisites, "derived_from": derived_from,
        "purge": purge_section,
        "legacy": {"format": fmt, "manifest_sha256": legacy_sha256, "claimed_reason": claimed_reason,
                   "claimed_source_revision": revision if isinstance(revision, str) else None,
                   "claimed_source_verified": verified_claim if isinstance(verified_claim, (str, int)) else None,
                   "claimed_restore_test": restore_test if isinstance(restore_test, str) else None,
                   "limitations": limitations},
    }
    record = {"schema_version": 1, "bundle_id": bundle_id, "legacy_format": fmt, "before_sha256": legacy_sha256,
              "after_sha256": _sha256(pf_instance.normalize_json(manifest)), "class": capture_class,
              "limitations": list(limitations), "unlisted_entries": sorted(unlisted_entries)}
    return manifest, record
