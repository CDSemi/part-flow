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
