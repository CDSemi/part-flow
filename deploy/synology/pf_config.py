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
                     "verification_record", "runner_acknowledgement", "generation_seal")
# PF-A3.3 AM-11: the side-by-side recovery and the cleanup are journaled operations of their own.
OPERATION_KINDS = ("deploy", "update", "backup", "rollback", "reset-db", "purge", "restore-instance", "abort-deploy",
                   "restore-side-by-side", "cleanup")
JOURNAL_PHASE_NAMES = ("planned", "preparing", "initializing", "activating", "preserving", "migrating",
                       "syncing-workspace", "workspace_sync_pending", "capturing", "verifying", "preserving-current",
                       "restoring-candidate", "switching", "deleting", "finalizing", "preparing-target",
                       "restoring-data", "completed", "failed_preserved", "needs_operator", "cancelled")
TERMINAL_PHASES = frozenset({"completed", "failed_preserved", "needs_operator", "cancelled"})
# PF-A3.2: needs_operator is terminal (its journal is never advanced) but stays blocking until a superseding recovery
# operation takes over (section 3.2).
BLOCKING_TERMINAL = frozenset({"needs_operator"})
# Allowed phases per operation kind (the LIFECYCLE section 2 phase table as the A3.1 contract reads it); every
# terminal phase is allowed for every kind. PF-A3.2 amendment AM-8 (SPEC section 2.3): the initial deploy also cuts the
# workspace over after its activation (syncing-workspace, workspace_sync_pending).
JOURNAL_PHASES = {
    "deploy": frozenset({"planned", "preparing", "initializing", "activating", "syncing-workspace",
                         "workspace_sync_pending", "finalizing"}) | TERMINAL_PHASES,
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
    # PF-A3.3 AM-18: preserve-then-abort after the frontend opened.
    "abort-deploy": frozenset({"planned", "preserving", "deleting", "finalizing"}) | TERMINAL_PHASES,
    # PF-A3.3 AM-11.
    "restore-side-by-side": frozenset({"planned", "preparing-target", "restoring-data", "activating", "verifying",
                                       "finalizing"}) | TERMINAL_PHASES,
    "cleanup": frozenset({"planned", "capturing", "deleting", "finalizing"}) | TERMINAL_PHASES,
}
# PF-A3.2 (SPEC section 2.4, normative): the order of the non-terminal phases of each kind; terminal phases follow any
# phase. Effect phases of a plan are non-decreasing in this order (AM-1).
PHASE_ORDER = {
    "deploy": ("planned", "preparing", "initializing", "activating", "syncing-workspace", "workspace_sync_pending",
               "finalizing"),
    "update": ("planned", "preparing", "preserving", "migrating", "activating", "syncing-workspace",
               "workspace_sync_pending", "finalizing"),
    "backup": ("planned", "capturing", "verifying", "finalizing"),
    "rollback": ("planned", "preparing", "preserving-current", "restoring-candidate", "switching", "activating",
                 "syncing-workspace", "workspace_sync_pending", "finalizing"),
    "reset-db": ("planned", "preparing", "preserving", "initializing", "switching", "activating", "finalizing"),
    "purge": ("planned", "preparing", "preserving", "capturing", "verifying", "deleting", "finalizing"),
    "restore-instance": ("planned", "preparing-target", "restoring-data", "activating", "syncing-workspace",
                         "workspace_sync_pending", "finalizing"),
    "abort-deploy": ("planned", "preserving", "deleting", "finalizing"),
    "restore-side-by-side": ("planned", "preparing-target", "restoring-data", "activating", "verifying", "finalizing"),
    "cleanup": ("planned", "capturing", "deleting", "finalizing"),
}
# AM-1: the phase of an effect is a non-terminal phase with a live effect (never planned, never the pending state).
EFFECT_PHASES = tuple(name for name in JOURNAL_PHASE_NAMES
                      if name not in TERMINAL_PHASES and name not in ("planned", "workspace_sync_pending"))
WORKSPACE_MODES = ("switch", "keep", "pending", "record-current", "untouched")
GENERATION_PATTERN = "^wsg-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
WORKSPACE_UNTOUCHED_KINDS = ("backup", "reset-db", "purge", "abort-deploy", "restore-side-by-side", "cleanup")
EVIDENCE_LIMIT = 2000
# PF-A3.3 AM-15: the closed effect vocabulary of the two new kinds.
KIND_EFFECT_TYPES = {"cleanup": ("database-drop", "resource-delete", "capture", "file-write"),
                     "restore-side-by-side": ("image-load", "database-restore", "service-change", "verification")}
# PF-A3.3 (SPEC section 2.5, AM-13): the functional verification checks besides the per-store data checks. An entry
# ending in ":" matches exactly one check by prefix (topology:<project>).
FUNCTIONAL_CHECKS = ("topology:", "isolation:network", "isolation:listener", "isolation:mounts", "isolation:restart",
                     "images:archive", "images:running", "source:archive", "workspace:archive", "history:archive",
                     "state:files", "config:bundle", "deployment:record", "health:backend", "health:frontend",
                     "heads:runtime", "app-invariants")
FUNCTIONAL_MUST_PASS = ("topology:", "isolation:network", "isolation:listener", "isolation:mounts",
                        "isolation:restart", "images:running", "health:backend", "health:frontend", "heads:runtime")
FUNCTIONAL_EXCLUDABLE = ("config:bundle", "deployment:record", "workspace:archive", "history:archive", "state:files")
# PF-A3.3 section 3.9: the candidate databases a cleanup may drop (a name a closed operation recorded).
CLEANUP_CANDIDATE_RE = re.compile(r"pf_(?:verify|migrate|restore|clean)_[0-9a-f]{20}\Z")
VERIFY_PROJECT_PATTERN = "^pfverify-[0-9a-f]{12}$"
RECOVER_PROJECT_PATTERN = "^pfrecover-[0-9a-f]{12}$"
# PF-A3.3 section 3.3: the application invariant command and its capability probe (identical argv everywhere).
RECONCILE_ARGV = ("uv", "run", "--no-sync", "python", "-m", "app.cli", "reconcile", "--max-findings", "1")
RECONCILE_PROBE_CODE = ("import importlib.util,sys; sys.exit(0 if importlib.util.find_spec("
                        "'app.application.reconciliation') else 3)")
RECONCILE_PROBE_ARGV = ("uv", "run", "--no-sync", "python", "-c", RECONCILE_PROBE_CODE)
RECONCILE_CHECK_IDS = tuple("abcdefghij")
EFFECT_TYPES = ("source-stage", "source-switch", "image-build", "image-tag", "image-load", "service-change",
                "database-create", "database-migrate", "database-restore", "database-switch", "database-drop",
                "database-alter", "resource-delete", "artifact-seal", "capture", "verification", "file-write")
# PF-A3.3 (additive, recorded as a deviation): the preserve-then-abort capture of section 3.7a.
MANIFEST_REASONS = ("scheduled-or-manual-backup", "emergency-manual", "before-update", "before-rollback",
                    "before-reset", "before-purge", "legacy", "before-abort")
# Rule 5: the reasons of an emergency preservation (PF-A3.3: reset-db and abort-deploy preserve like a rollback).
EMERGENCY_REASONS = ("emergency-manual", "before-rollback", "before-reset", "before-abort")
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
# AM-4: the workspace decision of an approved plan (section 3.7); the generation pattern and the reason length are
# cross-field rules.
_L_WORKSPACE_PLAN = _record({"mode": {"enum": list(WORKSPACE_MODES)}, "generation_id": _l_null("string"),
                             "container": _l_null("path"), "reason": _l_null("string")})

# Embedded copy of contracts/lifecycle-records.schema.json (section 2.4); a test asserts the two stay equal.
LIFECYCLE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Deployment Admin lifecycle records v1 (PF-A3.1, PF-A3.2 amendments AM-1..AM-10, PF-A3.3 amendments "
             "AM-11..AM-18)",
    "description": (
        "Frozen wire schemas of the lifecycle records: $defs.operation_plan and $defs.operation_journal (v1 with the "
        "PF-A3.2 amendments AM-1..AM-10 and the PF-A3.3 amendments AM-11..AM-18; written as "
        "<private_state>/operations/<operation-id>/plan.json and "
        "journal.json; frozen_config is the SHA-256 and length of the rendered app.env bytes the operation consumes), "
        "$defs.deployment_record (<private_state>/artifacts/deployments/<id>/"
        "deployment-record.json), $defs.recovery_manifest (manifest.json of a checkpoint or purge bundle), "
        "$defs.verification_record (<private_state>/artifacts/verifications/<bundle-id>/<verification-id>.json), "
        "$defs.runner_acknowledgement (<private_state>/operations/<operation-id>/acknowledgement-<sha12>.json, AM-16) "
        "and $defs.generation_seal (<backups>/generations/<project>/<generation-id>/seal.json, AM-17). "
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
                           "postcondition": _l_str(None, 1, 300), "preservation_refs": _l_items("$defs.bundle_id"),
                           "phase": {"enum": list(EFFECT_PHASES)}}),
        "effect_state": _record({"effect_id": _l_str(_L_EFFECT),
                                 "state": {"enum": ["not_started", "complete", "partial", "unknown"]},
                                 "observed_at": _l_null("$defs.stamp"), "evidence": _l_null("string")}),
        "approval": _record({"plan_sha256": _l_str(_L_SHA), "confirmed_at": _l_str(_L_STAMP),
                             "method": {"enum": ["typed-phrase", "policy-grant"]}}),
        "retained_artifact": _record({"kind": {"enum": ["checkpoint", "purge-bundle", "deployment", "database",
                                                        "image-tag", "staging", "workspace-generation",
                                                        "bundle-attempt", "checkpoint-history", "isolated-topology",
                                                        "recovery-target", "generation-seal"]},
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
        "workspace_plan": _L_WORKSPACE_PLAN,
        "deletion_approval": _record({"plan_sha256": _l_str(_L_SHA), "delete_backups": _L_BOOL,
                                      "reset_admin_config": _L_BOOL, "confirmed_at": _l_str(_L_STAMP)}),
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
            "recovery_route": _l_items("string"),
            "supersedes": _l_null("$defs.operation_id"),
            "admin_config": _l_null("$defs.config_ref"),
            "workspace": _L_WORKSPACE_PLAN,
            "input_bundle": _l_null("$defs.bundle_ref")}),
        "operation_journal": _record({
            "schema_version": {"const": 1}, "operation_id": _l_str(_L_OPERATION), "plan_sha256": _l_str(_L_SHA),
            "kind": {"enum": list(OPERATION_KINDS)}, "sequence": _l_int(1),
            "phase": {"enum": list(JOURNAL_PHASE_NAMES)}, "updated_at": _l_str(_L_STAMP),
            "approvals": _l_items("$defs.approval"), "effects": _l_items("$defs.effect_state"),
            "unresolved_effect": _l_null("$defs.effect_id"),
            "retained_artifacts": _l_items("$defs.retained_artifact"), "last_error": _l_null("$defs.error"),
            "legal_next": _l_items("string"), "result": _l_null("$defs.terminal"),
            "deletion": _l_null("$defs.deletion_approval")}),
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
        # PF-A3.3 AM-16: the attended acknowledgement of the runner records of a no-journal operation directory.
        "runner_acknowledgement": _record({
            "schema_version": {"const": 1}, "operation_id": _l_str(_L_OPERATION), "records_sha256": _l_str(_L_SHA),
            "record_count": _l_int(1), "observations": _l_items("string"), "acknowledged_at": _l_str(_L_STAMP),
            "release_id": _l_str(None, 1, 128), "attempt_pid": _l_int(1)}),
        # PF-A3.3 AM-17: the seal of a retained workspace generation before its retirement.
        "generation_seal": _record({
            "schema_version": {"const": 1}, "generation_id": _l_str(GENERATION_PATTERN),
            "instance_id": _l_str(_UUID), "retained_by_operation": _l_null("$defs.operation_id"),
            "sealed_by_operation": _l_str(_L_OPERATION), "entries_sha256": _l_str(_L_SHA),
            "archive": _record({"path": {"const": "workspace.tar.gz"}, "size": _l_int(1), "sha256": _l_str(_L_SHA),
                                "members": _l_int(), "expanded_bytes": _l_int(), "members_sha256": _l_str(_L_SHA)}),
            "handles": _record({"checked_at": _l_str(_L_STAMP), "result": {"const": "none-open"}}),
            "sealed_at": _l_str(_L_STAMP)}),
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
        if not is_legacy and reason not in EMERGENCY_REASONS:
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
    # PF-A3.3 AM-13: a functional record of a purge bundle is derived from recorded evidence (the per-store data rules
    # and every FUNCTIONAL_CHECKS entry). A checkpoint is never functionally verified (OD-A33-22); the reserved A3.1
    # corpus example of that shape keeps its expectation (SA3-3).
    functional = level == "functional_recovery_verified" and record["bundle_kind"] == "purge-bundle"
    if level == "data_restore_verified" or functional:
        if target["kind"] != "isolated-database" or not target["names"]:
            add(f"{level}: an isolated-database target with at least one name")
        stores = [name[len("restore:"):] for name in names if name.startswith("restore:")]
        if not stores:
            add(f"{level}: at least one restore:<store_id> check")
        by_name = {check["name"]: check for check in checks}
        for store in stores:
            for prefix in ("restore", "heads", "locale", "rows"):
                check = by_name.get(f"{prefix}:{store}")
                if check is None:
                    add(f"{level}: check {prefix}:{store} is missing")
                elif prefix in ("restore", "heads") and check["result"] == "not_run":
                    add(f"{level}: check {prefix}:{store} must run")
    if functional:
        problems += _functional_problems(checks)
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


def _functional_problems(checks):
    """AM-13: every FUNCTIONAL_CHECKS entry present (``topology:`` exactly once, by prefix); only the excludable
    payload checks and the application invariants may be not_run, each with its reason prefix."""
    problems = []
    add = problems.append
    by_name = {check["name"]: check for check in checks}
    for entry in FUNCTIONAL_CHECKS:
        if entry.endswith(":"):
            matched = [name for name in by_name if name.startswith(entry) and len(name) > len(entry)]
            if len(matched) != 1:
                add(f"functional_recovery_verified: exactly one {entry}<project> check is required")
            continue
        if entry not in by_name:
            add(f"functional_recovery_verified: check {entry} is missing")
    for name, check in by_name.items():
        if check["result"] != "not_run":
            continue
        detail = check["detail"]
        if name == "app-invariants":
            if not detail.startswith("unavailable:"):
                add("functional_recovery_verified: app-invariants may be not_run only as unavailable:")
        elif name in FUNCTIONAL_EXCLUDABLE:
            if not detail.startswith(("unavailable:", "excluded:")):
                add(f"functional_recovery_verified: {name} may be not_run only as unavailable: or excluded:")
        elif name.startswith("topology:") or name in FUNCTIONAL_CHECKS:
            add(f"functional_recovery_verified: check {name} must run")
    return problems


def _acknowledgement_problems(record):
    """AM-16 (the record_count consumer check needs the records bytes and runs where they are read)."""
    problems = []
    if not record["observations"] and record["record_count"] < 1:
        problems.append("record_count: at least one record")
    if len(record["observations"]) > 64:
        problems.append("observations: more than 64 entries")
    for index, item in enumerate(record["observations"]):
        if not isinstance(item, str) or len(item) > 300 or _CONTROL_RE.search(item):
            problems.append(f"observations[{index}]: more than 300 characters or a control character")
    return problems


def _seal_problems(record):
    """AM-17 (the entries digest is a consumer check against the sealed tree)."""
    problems = []
    archive = record["archive"]
    if archive["members"] < 0 or archive["expanded_bytes"] < 0:
        problems.append("archive: negative counts")
    if record["retained_by_operation"] is not None and record["retained_by_operation"] == record["sealed_by_operation"]:
        problems.append("retained_by_operation: the sealing operation never retained the generation")
    if record["sealed_at"] < record["handles"]["checked_at"]:
        problems.append("sealed_at precedes handles.checked_at")
    return problems


def workspace_effect_targets(generation):
    """The four workspace effects of a plan with workspace mode switch or pending (section 3.7): [(type, target)]."""
    return [("source-stage", "workspace:stage:" + generation), ("source-switch", "workspace:retain:" + generation),
            ("source-switch", "workspace:bind:" + generation), ("file-write", "source-manifest")]


def _is_workspace_effect(effect):
    return effect["target"].startswith("workspace:") or (effect["type"] == "file-write"
                                                         and effect["target"] == "source-manifest"
                                                         and effect["phase"] == "syncing-workspace")


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _plan_problems(plan):
    problems = []
    add = problems.append
    effects = plan["effects"]
    kind = plan["kind"]
    if len(effects) > 1024:
        add("effects: more than 1024 entries")
    if len(plan["coverage"]) > 256:
        add("coverage: more than 256 entries")
    ids = [effect["effect_id"] for effect in effects]
    if ids != sorted(set(ids)):
        add("effects: effect ids must be unique and increasing")
    # AM-1: each effect names its phase; phases of the kind only, non-decreasing in PHASE_ORDER.
    order = PHASE_ORDER[kind]
    reached = 0
    for effect in effects:
        phase = effect["phase"]
        if phase not in order or phase not in JOURNAL_PHASES[kind]:
            add(f"effects.{effect['effect_id']}.phase: {phase} is not a phase of {kind}")
            continue
        index = order.index(phase)
        if index < reached:
            add(f"effects.{effect['effect_id']}.phase: {phase} comes before the phase of an earlier effect "
                f"(phase order of {kind})")
        reached = max(reached, index)
    # AM-6: the abort-deploy plan freezes its deletion plan; a purge binds its deletion plan in the journal later.
    if (kind == "abort-deploy") != (plan["resources"]["deletion_plan_sha256"] is not None):
        add("resources.deletion_plan_sha256: set exactly for abort-deploy")
    if (plan["source"]["provenance"] == "git_commit") != (plan["source"]["commit"] is not None):
        add("source: provenance git_commit exactly when commit is set")
    for key, image in plan["images"].items():
        if key not in ("backend", "frontend", "db"):
            add(f"images.{key}: not a PartFlow service")
        elif image["platform"] is None:
            add(f"images.{key}.platform: required")
        else:
            problems += _image_problems(image, f"images.{key}")
    # AM-2: only a recovery operation supersedes a blocking operation.
    supersedes = plan["supersedes"]
    if supersedes is not None and kind not in ("rollback", "abort-deploy"):
        add(f"supersedes: a {kind} operation never supersedes another operation (rollback and abort-deploy only)")
    if kind == "abort-deploy" and supersedes is None:
        add("supersedes: required for abort-deploy (it supersedes the incomplete deploy)")
    if supersedes is not None and supersedes == plan["operation_id"]:
        add("supersedes: an operation cannot supersede itself")
    # AM-5, extended by AM-11 for the two PF-A3.3 kinds.
    if kind == "restore-side-by-side":
        if plan["input_bundle"] is None:
            add("input_bundle: required for restore-side-by-side (AM-11)")
    elif kind == "cleanup":
        if plan["input_bundle"] is not None:
            add("input_bundle: null for cleanup (AM-11)")
    elif (kind in ("rollback", "restore-instance")) != (plan["input_bundle"] is not None):
        add("input_bundle: set exactly for rollback and restore-instance")
    # AM-15: the closed per-kind effect vocabulary of the PF-A3.3 kinds.
    allowed = KIND_EFFECT_TYPES.get(kind)
    if allowed is not None:
        for effect in effects:
            if effect["type"] not in allowed:
                add(f"effects.{effect['effect_id']}.type: {effect['type']} is not an effect of {kind} (AM-15)")
    # AM-18: preserve-then-abort captures only after its stop, and the capture's bundle is a preservation reference.
    if kind == "abort-deploy":
        stopped = False
        for effect in effects:
            if effect["type"] == "service-change" and effect["target"].startswith("services:stop:"):
                stopped = True
            if effect["type"] == "capture":
                if not stopped:
                    add(f"effects.{effect['effect_id']}: an abort-deploy capture needs a preceding stop effect (AM-18)")
                bundle = next((item[len("bundle:"):] for item in effect["preconditions"]
                               if item.startswith("bundle:")), None)
                if bundle is None or bundle not in effect["preservation_refs"]:
                    add(f"effects.{effect['effect_id']}: the abort-deploy capture's preservation_refs name its "
                        "bundle (AM-18)")
    # AM-4: the workspace decision.
    workspace = plan["workspace"]
    mode, generation, container, reason = (workspace["mode"], workspace["generation_id"], workspace["container"],
                                           workspace["reason"])
    switching = mode in ("switch", "pending")
    if switching != (generation is not None) or switching != (container is not None):
        add("workspace: generation_id and container are set exactly for mode switch or pending")
    if generation is not None and not _match(GENERATION_PATTERN, generation):
        add("workspace.generation_id: not a workspace generation id (wsg-<stamp>-<8 hex>)")
    if (mode == "pending") != (reason is not None):
        add("workspace.reason: set exactly for mode pending")
    if reason is not None and (len(reason) > 200 or _CONTROL_RE.search(reason)):
        add("workspace.reason: more than 200 characters or a control character")
    if (mode == "untouched") != (kind in WORKSPACE_UNTOUCHED_KINDS):
        add("workspace.mode: untouched exactly for backup, reset-db, purge and abort-deploy")
    if mode == "record-current" and kind != "deploy":
        add("workspace.mode: record-current only for deploy")
    found = [(effect["type"], effect["target"]) for effect in effects if _is_workspace_effect(effect)]
    expected = workspace_effect_targets(generation) if switching and isinstance(generation, str) else []
    if found != expected:
        add("workspace: the plan carries the four workspace effects exactly for mode switch or pending")
    for effect in effects:
        if effect["target"].startswith("workspace:") and effect["phase"] != "syncing-workspace":
            add(f"effects.{effect['effect_id']}.phase: a workspace effect belongs to syncing-workspace")
    return problems


def _journal_problems(journal, plan=None):
    problems = []
    add = problems.append
    phase = journal["phase"]
    kind = journal["kind"]
    if phase not in JOURNAL_PHASES[kind]:
        add(f"phase {phase} is not a phase of {kind}")
    if (journal["result"] is not None) != (phase in TERMINAL_PHASES):
        add("result: set exactly in a terminal phase")
    unresolved = journal["unresolved_effect"]
    if unresolved is not None and not any(effect["effect_id"] == unresolved
                                          and effect["state"] in ("partial", "unknown")
                                          for effect in journal["effects"]):
        add("unresolved_effect: names no partial or unknown effect")
    if any(approval["plan_sha256"] != journal["plan_sha256"] for approval in journal["approvals"]):
        add("approvals: every approval binds plan_sha256")
    # AM-9: evidence holds identities, never values.
    for effect in journal["effects"]:
        evidence = effect["evidence"]
        if evidence is not None and len(evidence) > EVIDENCE_LIMIT:
            add(f"effects.{effect['effect_id']}.evidence: more than {EVIDENCE_LIMIT} characters")
        if evidence is not None and _CONTROL_RE.search(evidence):
            add(f"effects.{effect['effect_id']}.evidence: control character")
    deletion = journal["deletion"]
    if deletion is not None and kind not in ("purge", "abort-deploy", "restore-instance"):
        add(f"deletion: a {kind} journal never carries a deletion approval")
    if plan is None:
        return problems
    # The rules that bind a journal to its approved plan (section 2.3, AM-6/AM-8).
    if plan["kind"] != kind or plan["operation_id"] != journal["operation_id"]:
        add("plan: the journal names another operation or kind than its plan")
        return problems
    planned = {effect["effect_id"]: effect for effect in plan["effects"]}
    if [effect["effect_id"] for effect in journal["effects"]] != list(planned):
        add("effects: the journal's effects are not exactly the plan's effects")
        return problems
    states = {effect["effect_id"]: effect["state"] for effect in journal["effects"]}
    if phase == "workspace_sync_pending" and plan["workspace"]["mode"] not in ("switch", "pending"):
        add("phase workspace_sync_pending: the plan's workspace mode is neither switch nor pending")
    if kind == "purge" and deletion is None and any(
            planned[eid]["type"] == "resource-delete" and state != "not_started" for eid, state in states.items()):
        add("deletion: required once a resource-delete effect of the purge started")
    if kind == "abort-deploy" and deletion is not None \
            and deletion["plan_sha256"] != plan["resources"]["deletion_plan_sha256"]:
        add("deletion.plan_sha256: differs from the plan's frozen deletion plan")
    if kind == "restore-instance" and deletion is not None:
        started = [eid for eid, state in states.items()
                   if planned[eid]["phase"] == "restoring-data" and state != "not_started"]
        if started or phase not in ("preparing-target", "cancelled") or deletion["delete_backups"] \
                or deletion["reset_admin_config"]:
            add("deletion: a restore-instance carries a deletion approval only for an abandon in preparing-target "
                "before any restoring-data effect started")
    return problems


_LIFECYCLE_RULES = {"recovery_manifest": _manifest_problems, "deployment_record": _deployment_problems,
                    "verification_record": _verification_problems, "operation_plan": _plan_problems,
                    "operation_journal": _journal_problems, "runner_acknowledgement": _acknowledgement_problems,
                    "generation_seal": _seal_problems}


def lifecycle_problems(value, name, *, plan=None):
    """The cross-field rules of section 2.4 for one schema-valid record (run after pf_install.validate_marked); []
    when valid. Pure. A value that is not schema-valid is reported, never repaired. PF-A3.2: a journal is also checked
    against its schema-valid ``plan`` when one is given (the rules of SPEC section 2.3 that bind the two)."""
    if name not in _LIFECYCLE_RULES:
        raise ConfigError(f"lifecycle-record-unknown: {name!r}")
    try:
        if name == "operation_journal":
            return _journal_problems(value, plan)
        return _LIFECYCLE_RULES[name](value)
    except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
        return [f"$: cross-field rules not evaluated ({type(exc).__name__}: {exc}); validate the schema first"]


# ------------------------------------------------------------ operation index, routes and decisions (PF-A3.2)
# Pure interpretation of the protected operation files (SPEC sections 3.2, 3.3, 3.6, 3.7, 3.11a). Nothing here reads
# a clock, a daemon or a filesystem except load_frozen_app_config, which reads its own operation's snapshot files.

OPERATION_SCAN_LIMIT = 20000
OPERATION_ID_RE = re.compile(_L_OPERATION[1:-1])
CLOSED_PHASES = frozenset({"completed", "cancelled", "failed_preserved"})
CANDIDATE_RE = re.compile(r"pf_(?:migrate|restore|clean)_[0-9a-f]{20}\Z")
SWITCH_KINDS = ("deploy", "update", "rollback", "restore-instance")
EMERGENCY_KINDS = ("deploy", "update", "rollback", "reset-db")
# DISPATCH fail_closed column per operation kind (pf-admin.py DISPATCH; section 3.9): the route that creates the kind.
KIND_FAIL_CLOSED = {"deploy": "always", "update": "always", "rollback": "always", "reset-db": "always",
                    "abort-deploy": "always", "restore-instance": "unless-side-by-side", "purge": "never",
                    "backup": "never", "restore-side-by-side": "never", "cleanup": "never"}
ROUTE_COMMANDS = {"backup emergency": "backup --emergency", "cleanup report": "cleanup",
                  "cleanup apply": "cleanup --apply", "resume acknowledge": "resume --acknowledge"}


@dataclasses.dataclass(frozen=True)
class OperationEntry:
    """One operation directory as the index reads it. ``cls``: no-journal | invalid | blocking | superseded |
    closed. ``plan``/``journal`` are the parsed records of a valid entry (a plan alone for no-journal)."""

    operation_id: str
    cls: str
    plan: object = None
    plan_sha256: object = None
    journal: object = None
    error: object = None
    superseded_by: object = None
    # PF-A3.3 (section 3.10): the SHA-256 of the directory's unresolved-effects.json bytes (None when absent) and the
    # records hashes its valid runner_acknowledgement files bind.
    records_sha256: object = None
    acknowledged: frozenset = frozenset()

    @property
    def kind(self):
        return (self.journal or self.plan or {}).get("kind")

    @property
    def phase(self):
        return (self.journal or {}).get("phase")


@dataclasses.dataclass(frozen=True)
class OperationIndex:
    """The classified operations of one instance (section 3.2). ``blocking``: blocking lifecycle entries (valid
    non-terminal or needs_operator, and invalid ones); ``permissions``: the A2.3 permissions journal (a dict) or None;
    ``legacy``: any other ``state/pending.json`` content (journal-format-unsupported) or None; ``conflict``: more than
    one blocking entry that is not the allowed pair; ``pair``: (backup entry, pending-switch entry) or None;
    ``overflow``: the number of directory entries seen when the scan stopped at its bound, else 0."""

    entries: tuple
    blocking: tuple
    recent: tuple
    superseded: tuple
    permissions: object
    legacy: object
    conflict: bool
    pair: object
    overflow: int

    def entry(self, operation_id):
        return next((item for item in self.entries if item.operation_id == operation_id), None)

    @property
    def open_count(self):
        return len(self.blocking) + (self.permissions is not None) + (self.legacy is not None)


@dataclasses.dataclass(frozen=True)
class ResumeDecision:
    """Section 3.6 for one re-entered operation. ``action``: forward | reopen | withdraw | close | needs_operator.
    ``outcome``: the terminal phase the action ends in when it succeeds (``completed`` for forward)."""

    action: str
    outcome: str
    abandon_legal: bool
    keep_legal: bool
    reason: str


def effect_state(journal, effect_id):
    for effect in journal["effects"]:
        if effect["effect_id"] == effect_id:
            return effect["state"]
    return None


def effect_role(effect):
    """The section 3.6 role of one plan effect (pure, from type, target and phase)."""
    kind, target = effect["type"], effect["target"]
    if target.startswith("workspace:") or (kind == "file-write" and target == "source-manifest"
                                           and effect["phase"] == "syncing-workspace"):
        return "workspace"
    # PF-A3.3: the effects of an isolated topology (side-by-side) and the registry state write (OD-A33-08).
    if target.startswith("topology:"):
        return "topology"
    if kind == "file-write" and target.startswith("registry:"):
        return "registry"
    if (kind == "source-stage" and target.startswith("deployment:")) or (
            kind == "file-write" and target == "source-manifest"):
        return "staging"
    if kind == "service-change" and target.startswith("services:stop:"):
        return "stop"
    if kind == "service-change" and target.startswith("service:frontend:start"):
        return "frontend"
    if kind == "service-change" and target.startswith("service:backend:start"):
        return "backend"
    if kind == "service-change" and target == "service:db:start":
        return "db-start"
    if kind in ("capture", "verification", "artifact-seal", "resource-delete", "database-switch", "image-load"):
        return {"capture": "capture", "verification": "verification", "artifact-seal": "seal",
                "resource-delete": "deletion", "database-switch": "switch", "image-load": "image-load"}[kind]
    if kind.startswith("database-"):
        name = target.split(":")[1] if target.count(":") >= 1 else ""
        return "candidate" if CANDIDATE_RE.fullmatch(name) else ("migration" if kind == "database-migrate"
                                                                  else "data")
    if kind == "file-write" and target == "pointer:deployed.json":
        return "pointer"
    return "file"


def workspace_effect_ids(plan):
    """(W1, W2, W3, W4) effect ids of a plan with workspace effects, else None."""
    ids = [effect["effect_id"] for effect in plan["effects"] if effect_role(effect) == "workspace"]
    return tuple(ids) if len(ids) == 4 else None


def in_workspace_switch(plan, journal):
    """Section 3.2: W2 (workspace:retain) started and W3 (workspace:bind) not complete. Only then may the registered
    workspace path be absent."""
    ids = workspace_effect_ids(plan)
    if ids is None or journal["phase"] in TERMINAL_PHASES:
        return False
    return effect_state(journal, ids[1]) != "not_started" and effect_state(journal, ids[2]) != "complete"


def _validated(files, validate):
    """Classify one OperationFiles entry into an OperationEntry without the supersession and blocking rules."""
    operation_id = files.operation_id
    if files.error is not None:
        return OperationEntry(operation_id, "invalid", error=files.error)
    if files.journal_bytes is None:
        plan = None
        if files.plan_bytes is not None:
            try:
                plan = pf_instance.parse_strict_json(files.plan_bytes, label="plan.json")
            except pf_instance.ContextError:
                plan = None
        records = getattr(files, "records_sha256", None)
        return OperationEntry(operation_id, "no-journal", plan=plan if isinstance(plan, dict) else None,
                              records_sha256=records,
                              acknowledged=_acknowledged(files, validate) if records is not None else frozenset())
    if files.plan_bytes is None:
        return OperationEntry(operation_id, "invalid", error="journal.json without plan.json")
    records = {}
    for name, data, record in (("plan", files.plan_bytes, "operation_plan"),
                               ("journal", files.journal_bytes, "operation_journal")):
        try:
            value = pf_instance.parse_strict_json(data, label=name + ".json")
        except pf_instance.ContextError as exc:
            return OperationEntry(operation_id, "invalid", error=f"{name}.json: {exc}")
        if not isinstance(value, dict) or data != pf_instance.normalize_json(value):
            return OperationEntry(operation_id, "invalid", error=f"{name}.json: not the normalized JSON object form")
        problems = validate(value, record, plan=records.get("plan"))
        if problems:
            return OperationEntry(operation_id, "invalid", error=f"{name}.json: {problems[0]}")
        records[name] = value
    plan, journal = records["plan"], records["journal"]
    plan_sha256 = pf_instance.sha256_bytes(files.plan_bytes)
    if plan["operation_id"] != operation_id or journal["operation_id"] != operation_id:
        return OperationEntry(operation_id, "invalid", error="the records name another operation than the directory")
    if journal["plan_sha256"] != plan_sha256:
        return OperationEntry(operation_id, "invalid", error="journal.plan_sha256 is not the SHA-256 of plan.json")
    if journal["kind"] != plan["kind"]:
        return OperationEntry(operation_id, "invalid", error="the journal's kind is not the plan's kind")
    closed = journal["phase"] in CLOSED_PHASES
    return OperationEntry(operation_id, "closed" if closed else "blocking", plan=plan, plan_sha256=plan_sha256,
                          journal=journal)


def _acknowledged(files, validate):
    """Section 3.10: the records hashes the directory's valid acknowledgement files bind (schema, file name, operation
    and, for the current records, the record count)."""
    found = set()
    for name, data in getattr(files, "acknowledgements", ()) or ():
        try:
            value = pf_instance.parse_strict_json(data, label=name)
        except pf_instance.ContextError:
            continue
        if not isinstance(value, dict) or data != pf_instance.normalize_json(value) \
                or validate(value, "runner_acknowledgement"):
            continue
        if value["operation_id"] != files.operation_id or name != f"acknowledgement-{value['records_sha256'][:12]}.json":
            continue
        if value["records_sha256"] == files.records_sha256 and value["record_count"] != files.records_count:
            continue
        found.add(value["records_sha256"])
    return frozenset(found)


def _default_validate(value, name, *, plan=None):
    # The A2.1 marker validator lives in pf_install, which loads this module; it is resolved at call time (both
    # modules are fully loaded by then), never at import.
    pf_install = _load_sibling_module("pf_install")
    defs = LIFECYCLE_SCHEMA["$defs"]
    return pf_install.validate_marked(value, defs[name], defs=defs) or lifecycle_problems(value, name, plan=plan)


def _is_pending_switch(entry):
    """A deploy/update/rollback/restore-instance waiting in workspace_sync_pending with untouched W effects and its
    activation, seal and pointer done (section 3.3 backup row)."""
    if entry.cls != "blocking" or entry.kind not in SWITCH_KINDS or entry.phase != "workspace_sync_pending":
        return False
    plan, journal = entry.plan, entry.journal
    ids = workspace_effect_ids(plan)
    if ids is None or any(effect_state(journal, eid) != "not_started" for eid in ids):
        return False
    for effect in plan["effects"]:
        role = effect_role(effect)
        state = effect_state(journal, effect["effect_id"])
        if role in ("backend", "frontend", "pointer") and state != "complete":
            return False
        if role == "seal" and state not in ("complete", "partial"):
            return False
    return True


def parse_pending_journal(data):
    """``state/pending.json`` bytes -> ("permissions", dict) | ("legacy", dict) | None (absent)."""
    if data is None:
        return None
    try:
        value = pf_instance.parse_strict_json(data, label="pending.json")
    except pf_instance.ContextError as exc:
        return "legacy", {"operation": "<unreadable>", "phase": "<unreadable>", "error": str(exc)}
    if isinstance(value, dict) and value.get("operation") == "permissions":
        return "permissions", value
    if not isinstance(value, dict):
        value = {"operation": "<unreadable>", "phase": "<unreadable>", "error": "not a JSON object"}
    return "legacy", value


def classify_operations(files, *, permissions_journal, overflow=0, validate=None):
    """Section 3.2: the OperationIndex of ``files`` (pf_instance.scan_operations entries). ``permissions_journal``:
    the bytes of ``state/pending.json`` or None. ``validate(value, record, plan=None)`` returns the schema and
    cross-field problems of one lifecycle record (default: pf_install.validate_marked + lifecycle_problems)."""
    validate = validate or _default_validate
    entries = {item.operation_id: _validated(item, validate) for item in files}
    # Supersession: a later valid operation whose plan supersedes X and whose journal is not cancelled.
    superseders = {}
    for entry in entries.values():
        if entry.plan is not None and entry.journal is not None and entry.cls in ("blocking", "closed") \
                and entry.plan.get("supersedes") and entry.journal["phase"] != "cancelled":
            superseders.setdefault(entry.plan["supersedes"], []).append(entry.operation_id)
    for operation_id, entry in list(entries.items()):
        # A superseding plan is written only while the operation it names is the single blocking one (section 3.3),
        # so naming it is the proof; the creation stamps only order several superseders (a wall clock stepped back
        # between the two plans must never leave both blocking). Same-second IDs order by kind, not time.
        later = sorted((entries[item].plan["created_at"], item) for item in superseders.get(operation_id, ()))
        if entry.cls == "blocking" and later:
            entries[operation_id] = dataclasses.replace(entry, cls="superseded", superseded_by=later[-1][1])
    ordered = tuple(entries[name] for name in sorted(entries))
    blocking = tuple(item for item in ordered if item.cls in ("blocking", "invalid"))
    closed = sorted((item for item in ordered if item.cls == "closed"),
                    key=lambda item: (item.journal["updated_at"], item.operation_id), reverse=True)
    pending = parse_pending_journal(permissions_journal)
    permissions = pending[1] if pending and pending[0] == "permissions" else None
    legacy = pending[1] if pending and pending[0] == "legacy" else None
    pair = None
    if len(blocking) == 2 and permissions is None and legacy is None:
        backups = [item for item in blocking if item.cls == "blocking" and item.kind == "backup"
                   and item.phase not in TERMINAL_PHASES]
        switches = [item for item in blocking if _is_pending_switch(item)]
        if len(backups) == 1 and len(switches) == 1:
            pair = (backups[0], switches[0])
    count = len(blocking) + (permissions is not None) + (legacy is not None)
    return OperationIndex(entries=ordered, blocking=blocking, recent=tuple(closed[:5]),
                          superseded=tuple(item for item in ordered if item.cls == "superseded"),
                          permissions=permissions, legacy=legacy, conflict=count > 1 and pair is None, pair=pair,
                          overflow=int(overflow or 0))


def superseding_entry(index, entry):
    """The operation that supersedes ``entry`` (a superseded entry), or None."""
    return index.entry(entry.superseded_by) if entry.superseded_by else None


def final_superseder(index, entry):
    """The last operation of ``entry``'s supersession chain (U <- R1 <- R2 gives R2); ``entry`` itself when it is not
    superseded, None when a link is missing or the chain loops."""
    seen = set()
    while entry is not None and entry.cls == "superseded":
        if entry.operation_id in seen:
            return None
        seen.add(entry.operation_id)
        entry = superseding_entry(index, entry)
    return entry


def superseded_and_closed(index, entry):
    """A superseded entry whose supersession chain ends in a closed, not cancelled operation: it is reconciled and
    never reopened (section 3.11a)."""
    last = final_superseder(index, entry) if entry is not None and entry.cls == "superseded" else None
    return last is not None and last.cls == "closed" and last.journal["phase"] != "cancelled"


def runner_records_state(entry, index):
    """Section 3.11a: ("open", None) or ("reconciled", sequence) for the runner records of one operation directory.
    A superseded operation is reconciled by the closing sequence of the last operation of its supersession chain.
    PF-A3.3 (section 3.10): ("acknowledged", sha12) for a no-journal directory whose current records bytes an attended
    acknowledgement binds; records appended later change the hash and are open again."""
    if entry is not None and entry.cls == "no-journal" and entry.records_sha256 is not None \
            and entry.records_sha256 in entry.acknowledged:
        return "acknowledged", entry.records_sha256[:12]
    if entry is None or entry.cls in ("blocking", "invalid", "no-journal"):
        return "open", None
    if entry.cls == "closed":
        return "reconciled", entry.journal["sequence"]
    if superseded_and_closed(index, entry):
        return "reconciled", final_superseder(index, entry).journal["sequence"]
    return "open", None


def _prefix(slug):
    return f"pf --instance {slug}"


def _capture_bundle(plan, reason):
    """The pre-assigned bundle ID of the plan's capture ``checkpoint:<reason>`` (precondition ``bundle:<id>``)."""
    for effect in plan["effects"]:
        if effect["type"] == "capture" and effect["target"] == "checkpoint:" + reason:
            for item in effect["preconditions"]:
                if item.startswith("bundle:"):
                    return item[len("bundle:"):]
    return None


def retained_checkpoint(journal):
    """PF-A3.3 (section 3.7): the checkpoint a capture of this journal actually sealed (its retained ``checkpoint``
    artifact; a preserve_current fallback ID differs from the plan's pre-assigned one), or None."""
    found = [item["name"] for item in journal["retained_artifacts"] if item["kind"] == "checkpoint"]
    return found[-1] if found else None


def rollback_target(plan, journal):
    """The healthy checkpoint a superseding ``rollback --restore-db`` names for a blocking update/rollback/reset-db:
    the operation's own completed before-update/before-reset capture, the rollback's selected checkpoint, else a
    placeholder. PF-A3.3: the retained checkpoint artifact first (the actual ID); an emergency before-reset is never a
    rollback target, so a reset whose capture fell back to emergency preservation names the placeholder."""
    if plan["kind"] == "rollback" and plan["input_bundle"] is not None:
        return plan["input_bundle"]["bundle_id"]
    reason = {"update": "before-update", "reset-db": "before-reset"}.get(plan["kind"])
    if reason is not None:
        for effect in plan["effects"]:
            if effect["type"] == "capture" and effect["target"] == "checkpoint:" + reason \
                    and effect_state(journal, effect["effect_id"]) == "complete":
                if plan["kind"] == "reset-db" and "emergency" in str(_effect_evidence(journal, effect["effect_id"])):
                    return "<checkpoint>"
                return retained_checkpoint(journal) or _capture_bundle(plan, reason)
    return "<checkpoint>"


def _effect_evidence(journal, effect_id):
    for effect in journal["effects"]:
        if effect["effect_id"] == effect_id:
            return effect["evidence"]
    return None


def _stop_started(plan, journal):
    return any(effect_role(effect) == "stop" and effect_state(journal, effect["effect_id"]) != "not_started"
               for effect in plan["effects"])


def _frontend_started(plan, journal):
    return any(effect_role(effect) == "frontend" and effect_state(journal, effect["effect_id"]) != "not_started"
               for effect in plan["effects"])


def _pointer_started(plan, journal):
    """The deployment pointer effect started: a first deployment is then recorded and no longer abortable."""
    return any(effect_role(effect) == "pointer" and effect_state(journal, effect["effect_id"]) != "not_started"
               for effect in plan["effects"])


def cleanup_selector_words(plan):
    """PF-A3.3 (section 3.11): the explicit selectors a cleanup plan was approved with, as command-line words, derived
    from its effect targets (generation seals, history removals, recovery-target deletion plans)."""
    selectors = cleanup_selectors(plan)
    words = ""
    for gen in selectors["generations"]:
        words += " --generation " + gen
    for name in selectors["histories"]:
        words += " --checkpoint-history " + name
    for project in selectors["targets"]:
        words += " --recovery-target " + project
    return words


def cleanup_selectors(plan):
    """{"generations", "histories", "targets"} (sorted tuples) of a cleanup plan's explicit selectors."""
    generations, histories, targets = set(), set(), set()
    for effect in plan["effects"]:
        target = effect["target"]
        if effect["type"] == "capture" and target.startswith("generation:"):
            generations.add(target.split(":", 1)[1])
        elif effect["type"] == "file-write" and target.startswith("remove:checkpoint-history:"):
            histories.add(target.split(":", 2)[2])
        elif effect["type"] == "resource-delete" and target.startswith("deletion-plan:") \
                and _match(RECOVER_PROJECT_PATTERN, target.split(":", 1)[1]):
            targets.add(target.split(":", 1)[1])
    return {"generations": tuple(sorted(generations)), "histories": tuple(sorted(histories)),
            "targets": tuple(sorted(targets))}


def operation_routes(plan, journal, *, slug):
    """Section 3.3: the legal next commands of one lifecycle operation as [(route_key, command, description)]. The
    single source for the gate copy, the journal's legal_next and status."""
    op = plan["operation_id"]
    kind, phase = plan["kind"], journal["phase"]
    prefix = _prefix(slug)
    routes = []
    if phase in CLOSED_PHASES:
        return routes
    resume = f"{prefix} resume --operation {op}"
    if in_workspace_switch(plan, journal):
        return [("resume", resume, "finish the workspace switch (bind the staged tree, then the source manifest)"),
                ("resume keep-workspace", resume + " --keep-workspace",
                 "rebind the old workspace tree and keep it (the workspace is not refreshed)")]
    if kind == "restore-instance" and journal["deletion"] is not None:
        return [("resume abandon", resume + " --abandon",
                 "continue the frozen deletion plan of the accepted abandon (only route)")]
    decision = resume_decision(plan, journal) if phase != "needs_operator" else None
    if decision is not None:
        routes.append(("resume", resume, {
            "forward": "continue the recorded operation forward from its journal",
            "reopen": "reopen the unchanged deployment (no data or source effect started)",
            "withdraw": "withdraw this recovery operation; the superseded operation's routes apply again",
            "close": "close the operation (nothing outside its private files was changed)",
        }.get(decision.action, "continue the recorded operation")))
        if decision.abandon_legal:
            what = {"restore-instance": "remove what this restore created",
                    "restore-side-by-side": "remove the recovery target it created",
                    "cleanup": "close it, keeping every item not yet removed",
                    "abort-deploy": "close it; the incomplete deploy is blocking again"}.get(
                kind, "close it without a further effect")
            routes.append(("resume abandon", resume + " --abandon", "cancel the operation (" + what + ")"))
        if decision.keep_legal:
            routes.append(("resume keep-workspace", resume + " --keep-workspace",
                           "keep the current workspace; the application stays activated and recorded"))
    if kind in ("update", "rollback", "reset-db") and (_stop_started(plan, journal) or phase == "needs_operator"):
        routes.append(("rollback", f"{prefix} rollback {rollback_target(plan, journal)} --restore-db",
                       "roll back to a healthy checkpoint, restoring its database (the current data is preserved "
                       "first)"))
    if kind == "reset-db":
        switch = [effect for effect in plan["effects"] if effect["type"] == "database-switch"]
        capture = [effect for effect in plan["effects"] if effect["type"] == "capture"]
        if switch and capture and effect_state(journal, switch[0]["effect_id"]) != "not_started" \
                and "emergency" in str(_effect_evidence(journal, capture[0]["effect_id"])):
            retained = switch[0]["target"].split(":")[3]
            routes.append(("manual", f"restore the retained database {retained} manually",
                           "the before-reset capture is emergency preservation, never a rollback target "
                           "(SYNOLOGY_ADMIN §11)"))
    if kind == "deploy" and not _pointer_started(plan, journal):
        routes.append(("abort-deploy", f"{prefix} abort-deploy",
                       "remove the incomplete first deployment (after frontend access opened, the current database is "
                       "preserved first)"))
    if kind == "restore-side-by-side" and plan["input_bundle"] is not None:
        routes.append(("restore-instance", f"{prefix} restore-instance {plan['input_bundle']['bundle_id']} "
                       "--side-by-side", "resume the side-by-side recovery of the same bundle (aliases pf resume)"))
    if kind == "cleanup":
        routes.append(("cleanup", f"{prefix} cleanup --apply" + cleanup_selector_words(plan),
                       "resume the interrupted cleanup (pf cleanup --apply with the same selectors aliases pf resume)"))
    if kind in EMERGENCY_KINDS:
        routes.append(("backup emergency", f"{prefix} backup --emergency",
                       "capture the current data as emergency preservation (does not change this operation)"))
    if kind == "purge" and phase in ("deleting", "finalizing"):
        routes.append(("purge", f"{prefix} purge", "resume the frozen purge deletion (pf purge aliases pf resume)"))
    if kind == "restore-instance" and plan["input_bundle"] is not None:
        routes.append(("restore-instance", f"{prefix} restore-instance {plan['input_bundle']['bundle_id']}",
                       "resume the exact restore of the same bundle (aliases pf resume)"))
    if kind == "backup":
        routes.append(("backup", f"{prefix} backup", "resume the interrupted backup (pf backup aliases pf resume)"))
    if kind == "abort-deploy":
        routes.append(("abort-deploy", f"{prefix} abort-deploy",
                       "resume the frozen abort-deploy deletion (aliases pf resume)"))
    if kind in SWITCH_KINDS and phase == "workspace_sync_pending" and _is_pending_switch(
            OperationEntry(op, "blocking", plan=plan, journal=journal)):
        routes.append(("backup", f"{prefix} backup",
                       "capture a manual backup while the activated deployment waits for its workspace refresh"))
    return routes


def legal_next(plan, journal, *, slug):
    """The journal's ``legal_next``: the exact command lines of operation_routes."""
    return [command for _, command, _ in operation_routes(plan, journal, slug=slug)]


def resume_decision(plan, journal, observation=None):
    """Section 3.6 (pure): what ``resume`` does for this journal. ``observation``: the controller's classification of
    the unresolved effect when it decides the row ("needs_operator" forces that outcome)."""
    kind, phase = plan["kind"], journal["phase"]
    superseding = plan["supersedes"] is not None
    if phase in TERMINAL_PHASES:
        return ResumeDecision("none", phase, False, False, "the journal is terminal")
    if observation == "needs_operator":
        return ResumeDecision("needs_operator", "needs_operator", False, False, "no automatic continuation is safe")
    states = {effect["effect_id"]: effect["state"] for effect in journal["effects"]}
    reached = [effect for effect in plan["effects"] if states[effect["effect_id"]] != "not_started"]
    roles = {effect_role(effect) for effect in reached}
    ids = workspace_effect_ids(plan)
    keep = ids is not None and (phase == "workspace_sync_pending" or (
        "workspace" in roles and states[ids[2]] != "complete"))
    if phase == "workspace_sync_pending" or "workspace" in roles:
        return ResumeDecision("forward", "completed", False, keep, "workspace refresh")
    if not reached:
        return ResumeDecision("close", "cancelled", True, False, "no effect started")
    if roles <= {"staging"}:
        return ResumeDecision("close", "cancelled", True, False, "only private effects started")
    restore_back = "withdraw" if superseding else "reopen"
    if kind == "abort-deploy":
        # AM-18: abandon is legal while every deleting effect is not_started (the preservation only).
        deleting = any(effect["phase"] in ("deleting", "finalizing") for effect in reached)
        return ResumeDecision("forward", "completed", not deleting, False,
                              "frozen deletion" if deleting else "preserve-then-abort")
    if kind == "deploy":
        return ResumeDecision("forward", "completed", False, False, "forward")
    if kind == "restore-side-by-side":
        # Section 3.6: the live instance is never touched; --abandon (final teardown) is legal in every phase.
        return ResumeDecision("forward", "completed", True, False, "recovery target")
    if kind == "cleanup":
        # Section 3.12: forward continues the frozen list; --abandon is legal before and after the deletions.
        return ResumeDecision("forward", "completed", True, False, "frozen cleanup list")
    if kind == "backup":
        return ResumeDecision("forward", "completed", True, False, "new attempt or verification")
    if kind == "purge":
        if journal["deletion"] is None:
            return ResumeDecision("reopen", "cancelled", True, False, "no deletion started")
        return ResumeDecision("forward", "completed", False, False, "frozen deletion")
    if kind == "restore-instance":
        data_started = any(effect["phase"] != "preparing-target" for effect in reached)
        return ResumeDecision("forward", "completed", not data_started, False,
                              "data restore started" if data_started else "preparing the target")
    pre_data = {"staging", "stop", "capture", "verification"}
    if roles <= pre_data:
        return ResumeDecision(restore_back, "cancelled", True, False, "no database effect started")
    if roles <= pre_data | {"candidate"}:
        # Owned candidates only: update rehearsal (dropped, then reopen); reset-db/rollback candidate (redo forward).
        if kind == "update":
            return ResumeDecision(restore_back, "cancelled", True, False, "only the owned rehearsal candidate")
        return ResumeDecision("forward", "completed", True, False, "owned candidate only")
    return ResumeDecision("forward", "completed", False, False, "a live effect started")


@dataclasses.dataclass(frozen=True)
class GateDecision:
    """Section 3.3 for one locked route. ``action``: new | reenter | supersede | refuse. ``entry``: the blocking
    operation a re-entry or supersession acts on. ``code``/``message``: the refusal (first line exact, section 4.7).
    ``alias``: the route name when a route other than ``resume`` re-enters an operation."""

    action: str
    entry: object = None
    code: str = ""
    message: str = ""
    alias: str = ""


def _route_spelling(route):
    return ROUTE_COMMANDS.get(route, route)


def operation_open_message(entry, route, slug):
    routes = operation_routes(entry.plan, entry.journal, slug=slug)
    listing = "; ".join(f"{command}: {description}" for _, command, description in routes) or \
        f"none automatic; review it with '{_prefix(slug)} status --operation {entry.operation_id}'"
    return (f"operation-open: operation {entry.operation_id} ({entry.kind}, phase {entry.phase}) is incomplete; "
            f"'{_route_spelling(route)}' is not a legal next action for it. Legal next: {listing}. Nothing was changed.")


def needs_operator_message(entry, slug):
    plan, journal = entry.plan, entry.journal
    error = journal["last_error"] or {"message": "the outcome of an effect could not be proven"}
    stopped = journal["phase"]
    unresolved = journal["unresolved_effect"]
    for effect in plan["effects"]:
        if effect["effect_id"] == unresolved:
            stopped = effect["phase"]
    steps = "; ".join(command for _, command, _ in operation_routes(plan, journal, slug=slug)) or "none"
    return (f"operation-needs-operator: operation {entry.operation_id} ({entry.kind}) stopped at {stopped}: "
            f"{error['message'].splitlines()[0]} No automatic continuation is safe. Supported next steps: {steps}. "
            "Nothing was changed.")


def gate_decision(index, route, *, slug, request=None, private_state="<private_state>"):
    """Section 3.3 (pure): the decision of one locked mutating ``route`` ("resume", "backup", "backup emergency",
    "purge", ...) against ``index``. ``request``: operation, abandon, keep_workspace, delete_backups (True | False |
    None), reset_admin_config, bundle_id, side_by_side."""
    request = request or {}
    prefix = _prefix(slug)
    wanted = request.get("operation")
    if route == "cleanup report":
        # PF-A3.3 section 3.11: the observe-only report runs next to any operation (it lists nothing else when the
        # index is overflowing or invalid; the handler prints the diagnostic line).
        return GateDecision("observe")
    if index.overflow:
        return GateDecision("refuse", code="operation-index-overflow", message=(
            f"operation-index-overflow: {private_state}/operations holds more than {OPERATION_SCAN_LIMIT} entries, so "
            "an open operation could be hidden; every mutating route is refused. status, doctor, backups, recoveries, "
            "ps and logs work. See SYNOLOGY_ADMIN §16 (archiving closed operations). Nothing was changed."))
    if index.legacy is not None:
        legacy = index.legacy
        return GateDecision("refuse", code="journal-format-unsupported", message=(
            f"journal-format-unsupported: {private_state}/state/pending.json records {legacy.get('operation')}/"
            f"{legacy.get('phase')} in a format this control does not run. It can only come from an unsupported "
            "control change; see SYNOLOGY_ADMIN §16. Nothing was changed."))
    invalid = [item for item in index.blocking if item.cls == "invalid"]
    if invalid:
        return GateDecision("refuse", entry=invalid[0], code="operation-journal-invalid", message=(
            f"operation-journal-invalid: {invalid[0].operation_id}: {invalid[0].error}. The operation's state cannot "
            "be proven, so every mutating route is refused; status, doctor, backups, recoveries, ps and logs work. "
            "See SYNOLOGY_ADMIN §16. Nothing was changed."))
    if index.conflict:
        listed = [f"{item.operation_id} {item.kind}/{item.phase}" for item in index.blocking]
        if index.permissions is not None:
            listed.append(f"{index.permissions.get('operation_id')} permissions/{index.permissions.get('phase')}")
        first = index.blocking[0].operation_id if index.blocking else index.permissions.get("operation_id")
        return GateDecision("refuse", code="operation-conflict", message=(
            f"operation-conflict: {len(listed)} operations of instance {slug} are open ({'; '.join(listed)}); every "
            f"mutating route is refused until an administrator reviews them with '{prefix} status --operation "
            f"{first}'. Nothing was changed."))
    if route == "resume acknowledge":
        # PF-A3.3 section 3.10: observe-only next to any operation, except the A3.2 workspace-interval row; the gate
        # never re-enters the acknowledged directory.
        for item in index.blocking:
            if item.cls == "blocking" and in_workspace_switch(item.plan, item.journal):
                return GateDecision("refuse", entry=item, code="operation-open", message=operation_open_message(
                    item, route, slug))
        return GateDecision("observe")
    if index.permissions is not None:
        if route == "permissions apply":
            return GateDecision("new")
        journal = index.permissions
        return GateDecision("refuse", code="operation-open", message=(
            f"operation-open: operation {journal.get('operation_id')} (permissions, phase {journal.get('phase')}) is "
            f"incomplete; '{_route_spelling(route)}' is not a legal next action for it. Legal next: {prefix} "
            "permissions apply --resume: finish the interrupted permission apply; "
            f"{prefix} permissions apply --abandon: compensate it from its effect journal. Nothing was changed."))
    blocking = list(index.blocking)
    if route == "resume" and wanted is not None and all(item.operation_id != wanted for item in blocking):
        entry = index.entry(wanted)
        if entry is None or entry.cls == "no-journal":
            return GateDecision("refuse", code="operation-not-found", message=(
                f"operation-not-found: no operation {wanted} exists for instance {slug}. Nothing was changed."))
        state = f"superseded by {entry.superseded_by}" if entry.cls == "superseded" else entry.phase
        return GateDecision("refuse", entry=entry, code="operation-not-open", message=(
            f"operation-not-open: operation {wanted} is {state}; nothing to resume. Nothing was changed."))
    if not blocking:
        if route == "resume":
            return GateDecision("refuse", code="nothing-to-resume",
                                message="nothing-to-resume: No incomplete operation exists. Nothing was changed.")
        return GateDecision("new")
    if index.pair is not None:
        # The pending switch waits untouched while its backup is open: starting (or keeping) its W effects would end
        # the pair, and two blocking operations that are not the pair refuse every route (audit F1).
        backup, switch = index.pair
        backup_resume = f"{prefix} resume --operation {backup.operation_id}"
        legal = (f"{backup_resume}: resume the interrupted backup; {backup_resume} --abandon: close it (a partial "
                 "capture folder is kept as bundle-attempt)")
        if route == "resume" and wanted == backup.operation_id:
            return GateDecision("reenter", entry=backup)
        if route == "resume" and wanted == switch.operation_id:
            return GateDecision("refuse", entry=switch, code="operation-open", message=(
                f"operation-open: operation {switch.operation_id} ({switch.kind}, phase {switch.phase}) waits for "
                f"backup operation {backup.operation_id}, which is open; its workspace refresh and --keep-workspace "
                f"run only after the backup is closed. Legal next: {legal}. Nothing was changed."))
        listed = [f"{item.operation_id} {item.kind}/{item.phase}" for item in blocking]
        if route == "resume":
            return GateDecision("refuse", code="operation-conflict", message=(
                f"operation-conflict: {len(listed)} operations of instance {slug} are open ({'; '.join(listed)}); "
                f"name the backup with '{backup_resume}' (only that resume, its --abandon and the diagnostics are "
                f"legal while both are open; operation {switch.operation_id} continues afterwards). Nothing was "
                "changed."))
        return GateDecision("refuse", code="operation-open", message=(
            f"operation-open: operations {' and '.join(item.operation_id for item in blocking)} are open; "
            f"'{_route_spelling(route)}' is not a legal next action for them. Legal next: {legal}. Nothing was "
            "changed."))
    entry = blocking[0]
    plan, journal = entry.plan, entry.journal
    kind, phase = entry.kind, entry.phase
    if in_workspace_switch(plan, journal) and route != "resume":
        return GateDecision("refuse", entry=entry, code="operation-open", message=operation_open_message(
            entry, route, slug))
    if route == "resume":
        if phase == "needs_operator":
            return GateDecision("refuse", entry=entry, code="operation-needs-operator",
                                message=needs_operator_message(entry, slug))
        return GateDecision("reenter", entry=entry)
    if route == "rollback" and kind in ("update", "rollback", "reset-db") \
            and (_stop_started(plan, journal) or phase == "needs_operator"):
        return GateDecision("supersede", entry=entry)
    if route == "abort-deploy" and kind == "deploy" and not _pointer_started(plan, journal):
        # PF-A3.3 section 3.7a: after the frontend opened the abort preserves the current database first.
        return GateDecision("supersede", entry=entry)
    if route == "abort-deploy" and kind == "abort-deploy" and phase != "needs_operator":
        return GateDecision("reenter", entry=entry, alias=route)
    if route == "backup" and kind == "backup" and phase != "needs_operator":
        return GateDecision("reenter", entry=entry, alias=route)
    if route == "backup" and _is_pending_switch(entry):
        return GateDecision("new", entry=entry)
    if route == "backup emergency" and kind in EMERGENCY_KINDS:
        return GateDecision("new", entry=entry)
    if route == "purge" and kind == "purge" and phase in ("deleting", "finalizing"):
        deletion = journal["deletion"] or {}
        requested = []
        if request.get("delete_backups") is not None and request["delete_backups"] != deletion.get("delete_backups"):
            requested.append("--delete-backups" if request["delete_backups"] else "--keep-backups")
        if request.get("reset_admin_config") and not deletion.get("reset_admin_config"):
            requested.append("--reset-admin-config")
        if requested:
            recorded = ("delete backups" if deletion.get("delete_backups") else "keep backups") + \
                (", reset admin config" if deletion.get("reset_admin_config") else "")
            return GateDecision("refuse", entry=entry, code="plan-inputs-conflict", message=(
                f"plan-inputs-conflict: 'purge {' '.join(requested)}' asks for {' and '.join(requested)}, but the open "
                f"purge operation {entry.operation_id} was approved with {recorded}. Run '{prefix} resume --operation "
                f"{entry.operation_id}' or finish it first. Nothing was changed."))
        return GateDecision("reenter", entry=entry, alias=route)
    if route == "restore-instance" and kind == "restore-side-by-side" and request.get("side_by_side"):
        recorded = plan["input_bundle"]["bundle_id"]
        if request.get("bundle_id") == recorded:
            return GateDecision("reenter", entry=entry, alias=route)
        asked = request.get("bundle_id") or "an interactively chosen bundle"
        return GateDecision("refuse", entry=entry, code="plan-inputs-conflict", message=(
            f"plan-inputs-conflict: 'restore-instance {asked} --side-by-side' asks for {asked}, but the open "
            f"restore-side-by-side operation {entry.operation_id} was approved with {recorded}. Run '{prefix} resume "
            f"--operation {entry.operation_id}' or finish it first. Nothing was changed."))
    if route == "cleanup apply" and kind == "cleanup":
        recorded = cleanup_selectors(plan)
        asked = {key: tuple(sorted(request.get(key) or ())) for key in ("generations", "histories", "targets")}
        if asked == recorded:
            return GateDecision("reenter", entry=entry, alias=route)
        return GateDecision("refuse", entry=entry, code="plan-inputs-conflict", message=(
            f"plan-inputs-conflict: 'cleanup --apply' asks for other selectors than the open cleanup operation "
            f"{entry.operation_id} was approved with ('{prefix} cleanup --apply{cleanup_selector_words(plan)}'). Run "
            f"'{prefix} resume --operation {entry.operation_id}' or finish it first. Nothing was changed."))
    if route == "restore-instance" and kind == "restore-instance":
        recorded = plan["input_bundle"]["bundle_id"]
        if request.get("bundle_id") == recorded and not request.get("side_by_side"):
            return GateDecision("reenter", entry=entry, alias=route)
        asked = (request.get("bundle_id") or "an interactively chosen bundle") + \
            (" --side-by-side" if request.get("side_by_side") else "")
        return GateDecision("refuse", entry=entry, code="plan-inputs-conflict", message=(
            f"plan-inputs-conflict: 'restore-instance {asked}' asks for {asked}, but the open restore-instance "
            f"operation {entry.operation_id} was approved with {recorded}. Run '{prefix} resume --operation "
            f"{entry.operation_id}' or finish it first. Nothing was changed."))
    return GateDecision("refuse", entry=entry, code="operation-open", message=operation_open_message(entry, route, slug))


def workspace_reconcile(evidence, observed):
    """Section 3.7 reconciliation matrix row: ``evidence`` {"old": (dev, ino), "new": (dev, ino) | None}; ``observed``
    {"W": identity | None, "CG": identity | None, "S": identity | None}. Returns retain-not-started |
    between-renames | bind-done | stage-incomplete | stage-lost | foreign."""
    old, new = evidence.get("old"), evidence.get("new")
    w, cg, s = observed.get("W"), observed.get("CG"), observed.get("S")
    if old is None:
        return "foreign"
    if w == old and cg is None:
        if s is not None and new is not None and s == new:
            return "retain-not-started"
        return "stage-incomplete"
    if w is None and cg == old:
        if s is not None and new is not None and s == new:
            return "between-renames"
        if s is None:
            return "stage-lost"
        return "foreign"
    if w is not None and new is not None and w == new and cg == old and s is None:
        return "bind-done"
    return "foreign"


# ------------------------------------------------------------ integrated operations (PF-A3.3, pure)
# The application-invariant oracle (section 3.3), the capacity model (section 3.8), the instance purge coverage
# (section 3.4 gate step 5) and the cleanup candidate discovery (section 3.9). No clock, daemon or filesystem.

RECONCILE_RESULT_EXIT = {"clean": 0, "mismatch": 1, "error": 2}
RECONCILE_SUMMARY_LIMIT = 500


@dataclasses.dataclass(frozen=True)
class ReconcileResult:
    """One ``app.cli reconcile`` run (section 3.3): ``outcome`` clean | mismatch | error | incomplete | unavailable;
    ``summary`` the identity-only check summary (never findings, PNs, entity IDs or output text)."""

    outcome: str
    exit_code: object
    summary: str


def _reconcile_object(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate key")
        seen[key] = value
    return seen


def _reject_constant(name):
    raise ValueError("non-finite number " + name)


def parse_reconcile_report(stdout, exit_code, *, truncated=False):
    """Section 3.3 (pure): exactly one strict JSON report object consistent with the exit code, else ``incomplete``
    ("could not run", RUNBOOK §7). The summary is ``result=<result>;<id>:<status>:<count>,...`` in check order."""
    incomplete = ReconcileResult("incomplete", exit_code, "")
    if truncated or not isinstance(stdout, str) or not stdout.strip():
        return incomplete
    try:
        value = json.loads(stdout, object_pairs_hook=_reconcile_object, parse_constant=_reject_constant)
    except ValueError:
        return incomplete
    if not isinstance(value, dict) or value.get("report_version") != 1 or value.get("command") != "reconcile":
        return incomplete
    result, code = value.get("result"), value.get("exit_code")
    if type(code) is not int or code != exit_code or result not in RECONCILE_RESULT_EXIT \
            or RECONCILE_RESULT_EXIT[result] != exit_code:
        return incomplete
    checks = value.get("checks")
    if not isinstance(checks, list):
        return incomplete
    parts, seen = [], set()
    for check in checks:
        if not isinstance(check, dict):
            return incomplete
        check_id, status, count = check.get("id"), check.get("status"), check.get("finding_count")
        if check_id not in RECONCILE_CHECK_IDS or check_id in seen or not isinstance(status, str) \
                or not re.fullmatch(r"[a-z_-]{1,32}", status) or type(count) is not int or count < 0:
            return incomplete
        seen.add(check_id)
        parts.append(f"{check_id}:{status}:{count}")
    summary = (f"result={result};" + ",".join(parts))[:RECONCILE_SUMMARY_LIMIT]
    return ReconcileResult(result, exit_code, summary)


APP_CHECK_UNAVAILABLE = ("unavailable: the application image predates app.cli reconcile (P16-S1); r3 read-backs only "
                         "(heads, row counts, health)")


def app_invariants_check(source, restored, *, mode, recorded_summary=None):
    """The ``app-invariants`` check dict of section 2.5 (pure). ``mode``: purge (equality oracle of the quiesced source
    and the restored copy) or side-by-side (no live source: clean, or a mismatch equal to the bundle's recorded purge
    oracle summary ``recorded_summary``). ``source``/``restored``: ReconcileResult."""
    def check(result, detail):
        return {"name": "app-invariants", "result": result, "detail": detail[:500]}

    if restored.outcome == "unavailable" and (mode != "purge" or source.outcome == "unavailable"):
        return check("not_run", APP_CHECK_UNAVAILABLE)
    if mode == "purge" and (source.outcome == "unavailable") != (restored.outcome == "unavailable"):
        return check("failed", "the source and restored capability probes disagree for the same image IDs")
    if mode == "purge":
        if source.outcome in ("clean", "mismatch") and restored.outcome in ("clean", "mismatch") \
                and source.summary == restored.summary:
            return check("passed", "source and restored equal: " + restored.summary)
        return check("failed", f"source {source.outcome} ({source.summary or '-'}) vs restored {restored.outcome} "
                               f"({restored.summary or '-'})")
    if restored.outcome == "clean":
        return check("passed", "clean: " + restored.summary)
    if restored.outcome == "mismatch" and recorded_summary is not None and restored.summary == recorded_summary:
        return check("passed", "mismatch equal to the bundle's verification: " + restored.summary)
    return check("failed", f"restored {restored.outcome} ({restored.summary or '-'})")


@dataclasses.dataclass(frozen=True)
class Shortfall:
    """One device that cannot hold the phase's needs plus the safety floor (section 3.8)."""

    phase: str
    device: int
    roles: tuple
    paths: tuple
    need: int
    free: int
    floor: int


def restored_estimate(*, dump_bytes=None, live_bytes=None):
    """Section 3.8: ``restored(dump)`` = max(4 x dump, dump + 256 MiB); ``restored(live)`` = 2 x pg_database_size."""
    if dump_bytes is not None:
        return max(4 * int(dump_bytes), int(dump_bytes) + 256 * 1024 * 1024)
    return 2 * int(live_bytes or 0)


def capacity_shortfalls(needs, frees, floor_bytes, *, phase=None):
    """Section 3.8 (pure). ``needs``: [(phase, role, path, st_dev, bytes)]; ``frees``: {st_dev: free bytes}. Needs on
    one device are summed across roles (all phases, or ``phase`` only); the floor is added once per device. Returns
    [Shortfall] for every device whose need + floor exceeds its free bytes, in device order."""
    grouped = {}
    for item_phase, role, path, device, size in needs:
        if phase is not None and item_phase != phase:
            continue
        entry = grouped.setdefault(device, {"need": 0, "roles": [], "paths": [], "phases": []})
        entry["need"] += int(size)
        if role not in entry["roles"]:
            entry["roles"].append(role)
        if str(path) not in entry["paths"]:
            entry["paths"].append(str(path))
        if item_phase not in entry["phases"]:
            entry["phases"].append(item_phase)
    shortfalls = []
    for device in sorted(grouped):
        entry = grouped[device]
        free = int(frees[device])
        if entry["need"] + int(floor_bytes) > free:
            shortfalls.append(Shortfall(phase or "/".join(entry["phases"]), device, tuple(entry["roles"]),
                                        tuple(entry["paths"]), entry["need"], free, int(floor_bytes)))
    return shortfalls


def purge_coverage(manifest, binding, rows):
    """Section 3.4 gate step 5 (pure): [(item, reason)] of what the final bundle does not cover; [] when covered.
    Every database of the db service is a captured postgresql-logical store; the only deletable persistent store is
    ``<project>_postgres_data``; no candidate is external or shared; bind paths are recorded exclusions."""
    problems = []
    stores = {store["database"]: store for store in manifest["stores"]}
    for name in sorted(rows):
        store = stores.get(name)
        if store is None:
            problems.append((f"database {name}", "not a store of the final bundle"))
        elif store["kind"] != "postgresql_logical" or store["strategy"].get("id") != "postgresql-logical":
            problems.append((f"database {name}", "not captured with the postgresql-logical strategy"))
    project = binding["compose_project"]
    volumes = [item["key"] for item in binding["candidates"] if item["kind"] == "volume"]
    if any(volume != project + "_postgres_data" for volume in volumes):
        problems.append(("volumes", "a deletion candidate other than " + project + "_postgres_data"))
    for item in binding["candidates"]:
        if item["kind"] in ("volume", "network") and set(item.get("users") or ()) - {
                entry["key"] for entry in binding["candidates"] if entry["kind"] == "container"}:
            problems.append((f"{item['kind']} {item['key']}", "shared with a container outside the plan"))
        if item["kind"] == "volume" and (item["identity"].get("driver") not in (None, "local")
                                         or item["identity"].get("scope") not in (None, "local")):
            problems.append((f"volume {item['key']}", "external or non-local"))
    excluded = {entry["item"]: entry["reason"] for entry in manifest["exclusions"]}
    for path in binding.get("bind_paths") or ():
        if "bind-mounts" not in excluded or path not in excluded["bind-mounts"]:
            problems.append((f"bind {path}", "not recorded as a bind-retained exclusion of the final bundle"))
    return problems


@dataclasses.dataclass(frozen=True)
class CleanupItem:
    """One cleanup candidate (section 3.9): ``cls`` candidate-database | isolated-topology | bundle-attempt |
    workspace-stage | generation | checkpoint-history | recovery-target | report-only; ``identity`` the recorded identity
    the effect re-observes (database name, project/UUID, folder dev:ino, ...)."""

    cls: str
    name: str
    operation_id: str
    identity: object = None
    detail: str = ""


def operation_closed(index, entry):
    """Section 3.9: closed, or superseded with a closed, non-cancelled final superseder. Blocking, invalid,
    needs_operator and pair entries are never closed."""
    if entry.cls == "closed":
        return True
    return entry.cls == "superseded" and superseded_and_closed(index, entry)


def _recorded_names(plan, journal):
    texts = []
    for effect in plan["effects"]:
        texts.append(effect["target"])
        texts += effect["preconditions"]
    texts += [item["evidence"] or "" for item in journal["effects"]]
    found = []
    for text in texts:
        for name in re.findall(r"pf_(?:verify|migrate|restore|clean)_[0-9a-f]{20}", text):
            if name not in found:
                found.append(name)
    return found


def cleanup_candidates(index, observations):
    """Section 3.9 candidate discovery (pure). ``observations``: {"databases": set of names, "current_database",
    "topologies": {project: uuid with resources observed}, "attempts": {bundle_id: "sealed" | "unsealed" | None},
    "stages": set of generation ids with a stage, "generations": set, "workspace_generation": id or None,
    "histories": {name: "duplicated" | "unique" | None}, "pf_recovery": set, "pf_keep": set,
    "unsealed_active_staging": dep or None, "abandoned_tags": [(tag, operation_id)]}. Returns [CleanupItem]; a prefix is
    never authority (only names a closed operation recorded)."""
    items = []
    databases = set(observations.get("databases") or ())
    current = observations.get("current_database")
    open_names, pending_switch = set(), set()
    for entry in index.entries:
        if entry.plan is None or entry.journal is None:
            continue
        if not operation_closed(index, entry):
            open_names.update(_recorded_names(entry.plan, entry.journal))
        for effect in entry.plan["effects"]:
            if effect["type"] == "database-switch" and effect_state(entry.journal, effect["effect_id"]) != "complete":
                pending_switch.add(effect["target"].split(":")[2])
    seen = set()
    topologies = observations.get("topologies") or {}
    for entry in index.entries:
        if entry.plan is None or entry.journal is None or not operation_closed(index, entry):
            continue
        plan, journal, op = entry.plan, entry.journal, entry.operation_id
        for name in _recorded_names(plan, journal):
            if name in databases and name != current and name not in open_names and name not in pending_switch \
                    and ("db", name) not in seen:
                seen.add(("db", name))
                items.append(CleanupItem("candidate-database", name, op, name))
        projects = {}
        for effect in plan["effects"]:
            project = next((item.split(":", 1)[1] for item in effect["preconditions"]
                            if item.startswith("topology:")), None)
            uuid_value = next((item.split(":", 1)[1] for item in effect["preconditions"]
                               if item.startswith("topology-uuid:")), None)
            if project and uuid_value:
                projects[project] = uuid_value
        retained = {item["name"] for item in journal["retained_artifacts"] if item["kind"] == "isolated-topology"}
        for project, uuid_value in sorted(projects.items()):
            recover = _match(RECOVER_PROJECT_PATTERN, project)
            if topologies.get(project) != uuid_value or ("topology", project) in seen:
                continue
            if recover and plan["kind"] == "restore-side-by-side" \
                    and journal["phase"] in ("completed", "failed_preserved"):
                seen.add(("topology", project))
                items.append(CleanupItem("recovery-target", project, op, uuid_value))
            elif not recover and (plan["kind"] == "purge" or project in retained):
                seen.add(("topology", project))
                items.append(CleanupItem("isolated-topology", project, op, uuid_value))
        attempts = observations.get("attempts") or {}
        for artifact in journal["retained_artifacts"]:
            if artifact["kind"] == "bundle-attempt" and ("attempt", artifact["name"]) not in seen:
                state = attempts.get(artifact["name"])
                if state is None:
                    continue
                seen.add(("attempt", artifact["name"]))
                items.append(CleanupItem("bundle-attempt" if state == "unsealed" else "report-only",
                                         artifact["name"], op, artifact["name"],
                                         "" if state == "unsealed" else "bundle-attempt-sealed"))
            if artifact["kind"] == "workspace-generation" and artifact["name"] in (observations.get("generations")
                                                                                   or ()) \
                    and artifact["name"] != observations.get("workspace_generation") \
                    and ("generation", artifact["name"]) not in seen:
                seen.add(("generation", artifact["name"]))
                items.append(CleanupItem("generation", artifact["name"], op, artifact["name"]))
            if artifact["kind"] == "checkpoint-history" and plan["kind"] == "restore-instance" \
                    and ("history", artifact["name"]) not in seen \
                    and (observations.get("histories") or {}).get(artifact["name"]) is not None:
                seen.add(("history", artifact["name"]))
                items.append(CleanupItem("checkpoint-history", artifact["name"], op, artifact["name"],
                                         (observations.get("histories") or {})[artifact["name"]]))
        generation = plan["workspace"]["generation_id"]
        if generation and generation in (observations.get("stages") or ()) and ("stage", generation) not in seen:
            seen.add(("stage", generation))
            items.append(CleanupItem("workspace-stage", generation, op, generation))
    for name in sorted(observations.get("pf_recovery") or ()):
        items.append(CleanupItem("report-only", name, "", name, "legacy-recovery-database"))
    for name in sorted(observations.get("pf_keep") or ()):
        items.append(CleanupItem("report-only", name, "", name, "retained-database"))
    if observations.get("unsealed_active_staging"):
        items.append(CleanupItem("report-only", observations["unsealed_active_staging"], "", None,
                                 "unsealed-active-staging"))
    for tag, operation in observations.get("abandoned_tags") or ():
        items.append(CleanupItem("report-only", tag, operation, tag, "abandoned-restore-tag"))
    return items


def load_frozen_app_config(operation_dir, expected_sha256, expected_bytes=None):
    """Section 3.8: the snapshot of ``operation_dir`` (or one of its ``refreeze-<n>`` directories) whose rendered
    ``app.env`` bytes have ``expected_sha256`` (and length ``expected_bytes``), verified; ConfigError when none
    matches or its bytes changed. Record timestamps and IDs play no part; snapshots with the same rendered bytes are
    the same snapshot."""
    operation_dir = Path(operation_dir)
    directories = [operation_dir]
    try:
        names = sorted(os.listdir(str(operation_dir)))
    except OSError as exc:
        raise ConfigError(f"plan-input-changed: the operation directory cannot be listed ({exc.strerror})") from exc
    directories += [operation_dir / name for name in names if re.fullmatch(r"refreeze-[0-9]{1,4}", name)
                    and not os.path.islink(str(operation_dir / name))]
    for directory in directories:
        try:
            data = _read_nofollow(directory / SNAPSHOT_RECORD)
            record = json.loads(data.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict) or record.get("env_file_sha256") != expected_sha256 \
                or (expected_bytes is not None and record.get("env_file_bytes") != expected_bytes):
            continue
        env_path = directory / SNAPSHOT_FILE
        try:
            rendered = _read_nofollow(env_path)
        except OSError as exc:
            raise ConfigError(f"plan-input-changed: the frozen snapshot {env_path} is unreadable "
                              f"({exc.strerror})") from exc
        if hashlib.sha256(rendered).hexdigest() != expected_sha256:
            raise ConfigError(f"plan-input-changed: the frozen snapshot {env_path} changed on disk")
        values = parse_app_env(rendered, label=str(env_path))
        source = record.get("source_env_sha256")
        frozen = FrozenAppConfig(operation_id=str(record.get("operation_id")), directory=directory, env_file=env_path,
                                 env_sha256=expected_sha256, source_sha256=source if isinstance(source, str) else "",
                                 values=types.MappingProxyType(dict(values)))
        return verify_frozen(frozen)
    raise ConfigError("plan-input-changed: no frozen snapshot of this operation has the approved rendered bytes")


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
        # Audit AF-1: a listed state file is restored from its verified payload only; nothing else is opened.
        for name in purge_section["state_files"]:
            if types.get("state/" + name) != "state_file":
                raise refuse(f"state file {name} has no state/{name} payload in the checksum map")
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
