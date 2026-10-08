#!/usr/bin/env python3
"""Conservative lifecycle commands for the supplied PartFlow NAS staging stack.

Python standard library only. No application or database business rules live here.
Local configuration and this controller are never replaced by downloaded source.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
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
CHECKPOINT = "PF-A3.3"
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
# state_files may list for a restore into protected state (PF-A1.4). PF-A3.1: one tuple, owned by pf_config.
RESTORABLE_STATE_FILES = pf_config.RESTORABLE_STATE_FILES
# PF-A3.1: deployed-source artifacts, verification records and the strict bundle reader (SPEC sections 2.2, 3.1).
DEPLOYMENT_ID_RE = re.compile(r"dep-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
VERIFICATION_ID_RE = re.compile(r"ver-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
MANIFEST_READ_LIMIT = 8 * 1024 * 1024
# The runtime .env and a state file are restored from their verified payload bytes, read whole (audit AF-1).
SMALL_PAYLOAD_LIMIT = 16 * 1024 * 1024
ARTIFACT_MARGIN = 64 * 1024 * 1024
ARCHIVE_MARGIN = 256 * 1024 * 1024
LIFECYCLE_DEFS = pf_config.LIFECYCLE_SCHEMA["$defs"]
CLASS_NAMES = {"healthy_checkpoint": "healthy", "emergency_preservation": "emergency", "partial": "partial"}
LEVEL_NAMES = {"captured": "captured", "failed": "failed", "data_restore_verified": "data-restore",
               "functional_recovery_verified": "functional"}
PASSED_LEVELS = ("data_restore_verified", "functional_recovery_verified")
# Read-only PostgreSQL inventory statements (section 3.8); every answer is checked before it is used.
FACTS_SQL = ("SELECT d.datname, pg_get_userbyid(d.datdba), pg_encoding_to_char(d.encoding), d.datcollate, "
             "d.datctype, d.datallowconn FROM pg_database d WHERE NOT d.datistemplate AND d.datname <> 'postgres' "
             "ORDER BY d.datname;")
EXTENSIONS_SQL = "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
ROW_COUNTS_SQL = ("SELECT n.nspname || '.' || c.relname, (xpath('/row/c/text()', query_to_xml(format('SELECT "
                  "count(*) AS c FROM %I.%I', n.nspname, c.relname), false, true, '')))[1]::text FROM pg_class c "
                  "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname NOT IN "
                  "('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_toast%' ORDER BY 1;")
ROLES_SQL = ("SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls FROM "
             "pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname;")
AVAILABLE_EXTENSIONS_SQL = "SELECT name FROM pg_available_extensions ORDER BY name;"
ROW_NAME_RE = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\Z")
PG_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
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


class StageFailed(Failure):
    """PF-A3.2: the source-stage effect failed; only private files were written (section 3.1 step 4)."""

    def __init__(self, detail):
        super().__init__(f"deployment-stage-failed: {detail}")
        self.detail = detail


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


def _identity_only(text):
    """One evidence line: control characters removed, at most 300 characters (AM-16 observations)."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(text))[:300]


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


def create_source_archive(root, destination):
    """PF-A3.1: a thin wrapper over the fd-safe writer (links and special files refused, as before)."""
    try:
        return pf_source.archive_tree(root, destination, excludes=SOURCE_EXCLUDES, unsupported="refuse")
    except pf_source.SourceError as exc:
        raise Failure(f"Source backup refuses this tree: {exc}") from exc


def lifecycle_errors(value, name, *, plan=None):
    """Schema (A1 subset + A2.1 markers) and then cross-field problems of one lifecycle record; [] when valid.
    PF-A3.2: a journal is checked against its ``plan`` when one is given."""
    errors = pf_install.validate_marked(value, LIFECYCLE_DEFS[name], defs=LIFECYCLE_DEFS)
    return errors or pf_config.lifecycle_problems(value, name, plan=plan)


def bundle_failure(code, bundle_id, detail, *, tail="Nothing was changed."):
    """A coded bundle refusal (section 4.6); ``code`` is also its listing tag."""
    exc = Failure(f"{code}: {bundle_id}: {detail}. {tail}")
    exc.code = code
    return exc


def failure_code(exc):
    """The section 4.6 code of a refusal (the attribute, else the copy's leading token), for listings."""
    code = getattr(exc, "code", None)
    if code:
        return code
    head = str(exc).split(":", 1)[0]
    return head if re.fullmatch(r"[a-z0-9-]{3,64}", head) else "unreadable"


@dataclasses.dataclass(frozen=True)
class BundleView:
    """One strictly read checkpoint or purge bundle (section 3.1): the schema 1 manifest (migrated in memory for a
    legacy one), its on-disk manifest hash and the computed verification level. Accessors follow the section 3.6
    mapping of the former format 2 keys."""

    folder: Path
    manifest: dict
    manifest_sha256: str
    legacy: object
    level: str
    latest_verification_id: object = None

    @property
    def bundle_id(self):
        return self.manifest["bundle_id"]

    @property
    def bundle_kind(self):
        return self.manifest["bundle_kind"]

    @property
    def capture_class(self):
        return self.manifest["capture_class"]

    @property
    def reason(self):
        return self.manifest["reason"]

    @property
    def display_reason(self):
        legacy = self.manifest["legacy"]
        return "legacy:" + str(legacy["claimed_reason"]) if legacy is not None else self.reason

    @property
    def compose_project(self):
        return self.manifest["source_instance"]["compose_project"]

    @property
    def repository(self):
        return self.manifest["source_instance"]["repository"]

    @property
    def environment(self):
        return self.manifest["source_instance"]["environment"]

    @property
    def workspace_root(self):
        return self.manifest["source_instance"]["workspace"]

    @property
    def images(self):
        """{service: {"reference", "id"}} of the backend/frontend images the bundle recorded (non-null only)."""
        return {service: {"reference": image["reference"], "id": image["id"]}
                for service in pf_docker.BUILT_SERVICES for image in [self.manifest["images"][service]]
                if image is not None}

    def image(self, service):
        return self.manifest["images"][service]

    @property
    def database_heads(self):
        return list(self.manifest["compatibility"]["alembic_heads_live"])

    @property
    def migration_files(self):
        return dict(self.manifest["compatibility"]["migration_files"])

    @property
    def postgres_major(self):
        return self.manifest["postgresql"]["major"]

    @property
    def source_hypothesis(self):
        """The commit a provenance proof is attempted for: the recorded commit, or a legacy 40-hex claim. It is never
        written into a record or pointer (section 3.6 mapping)."""
        commit = self.manifest["source"]["commit"]
        if commit is not None:
            return commit
        legacy = self.manifest["legacy"]
        claim = legacy["claimed_source_revision"] if legacy is not None else None
        return claim if isinstance(claim, str) and SHA_RE.fullmatch(claim) else None

    @property
    def source_display(self):
        commit = self.manifest["source"]["commit"]
        if commit is not None:
            return "git_commit " + commit[:12]
        claim = self.source_hypothesis
        return "claimed " + claim[:12] if claim else "unknown"

    @property
    def stores(self):
        return list(self.manifest["stores"])

    @property
    def active_store(self):
        return next(store for store in self.manifest["stores"] if store["role"] == "active")

    @property
    def database(self):
        return self.active_store["database"]

    @property
    def database_user(self):
        return self.active_store["owner"]

    @property
    def source_payload(self):
        return self.manifest["source"]["payload"]

    @property
    def workspace_payload(self):
        return self.manifest["workspace"]["payload"]

    @property
    def derived_from(self):
        return self.manifest["derived_from"]

    @property
    def purge(self):
        return self.manifest["purge"]

    def payload(self, path):
        return next((item for item in self.manifest["payloads"] if item["path"] == path), None)

    def payloads_of(self, kind):
        return [item for item in self.manifest["payloads"] if item["type"] == kind]


@dataclasses.dataclass(frozen=True)
class InvalidBundle:
    """A bundle folder whose strict read failed (listed as ``[invalid: <code>]``, never selectable)."""

    folder: Path
    code: str
    detail: str

    @property
    def bundle_id(self):
        return self.folder.name


@dataclasses.dataclass(frozen=True)
class DeploymentView:
    """The deployment ``deployed.json`` points to (section 3.3 Read): ``mismatch`` is None for a verified record."""

    deployment_id: str
    folder: Path
    record: object
    record_sha256: str
    mismatch: object


@dataclasses.dataclass(frozen=True)
class StagedDeployment:
    """An unsealed deployment staged after the final confirmation (section 3.3 Stage)."""

    deployment_id: str
    kind: str
    staging: Path
    source: dict
    images: object
    previous_deployment_id: object
    migration_files_sha256: str


@dataclasses.dataclass(frozen=True)
class IsolatedTopology:
    """PF-A3.3 (section 3.1): one verification or recovery Compose project of an operation: its generated project and
    UUID, the private directory holding ``app.env``/``compose.json``/``topology.json``, the generated values (the
    throwaway database password; never logged) and the image IDs it runs."""

    project: str
    uuid: str
    purpose: str
    directory: Path
    values: types.MappingProxyType
    images: types.MappingProxyType
    model_sha256: str

    @property
    def compose_file(self):
        return self.directory / "compose.json"

    @property
    def env_file(self):
        return self.directory / "app.env"


class FunctionalFailed(Failure):
    """PF-A3.3: a functional verification wrote its failed record (section 3.2 step 8); the topology is kept."""


class RecoveryTargetLost(Failure):
    """PF-A3.3 (section 3.6): the side-by-side target's volume or data checks changed while interrupted."""


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


def write_editable_file(path, data, *, expected, expected_identity, create_gid, op8, keys=None, create_mode=0o660):
    """Section 3.7 steps 1-5: refuse unless the target still equals the reviewed observation, remove classified
    leftovers, write a private temp, take the target's ownership and mode (or root:<create_gid> ``create_mode`` for a
    new file: the configuration target of the permission policy in force, PF-A2.3, verified with fstat),
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
                os.fchmod(fd, create_mode)
                created = os.fstat(fd)
                if (created.st_uid, created.st_gid, stat.S_IMODE(created.st_mode)) != (pf_instance.TRUSTED_UID,
                                                                                       create_gid, create_mode):
                    raise Failure(f"permissions-verify-failed: configuration: {path.name} does not hold its target "
                                  f"after the change (mode {stat.S_IMODE(created.st_mode):04o} gid {created.st_gid}); "
                                  "nothing was written.")
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


def write_reviewed(path, data, target, *, create_gid, op8, keys, secret=False, create_mode=0o660):
    """write_editable_file against the reviewed observation ``target``. An interrupt is reported from an observation
    of the target (old or new bytes), never assumed."""
    try:
        write_editable_file(path, data, expected=target.data if target.present else None,
                            expected_identity=target.identity, create_gid=create_gid, op8=op8, keys=keys,
                            create_mode=create_mode)
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


def admin_summary(path, *, mode, before, document, asked, instance_line, environment_line, app_hint,
                  permission_lines=()):
    """Section 4.6 admin summary (no confirmation). ``permission_lines``: the PF-A2.3 proposal lines (registered)."""
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
    for line in permission_lines:
        log(line)
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


# ------------------------------------------------------------ permission policy (PF-A2.3)
# `pf permissions check|plan|apply`: the semantic permission policy, its revision-bound approval in private state and
# the fd-safe apply engine (pf_instance). Lifecycle flows give fresh artifacts explicit policy targets
# (Controller.publish_fresh/apply_single); no flow walks an existing editable tree.

PERMISSION_RECORD_NAME = "permission-policy.json"
PERMISSION_PLAN_NAME = "permission-plan.json"
PERMISSION_CHANGES_NAME = "permission-changes.jsonl"
PERMISSION_EFFECTS_NAME = "permission-effects.jsonl"
PERMISSION_OUTCOME_NAME = "permission-apply.json"
PERMISSION_APPROVAL_COPY = "permission-approval.json"
PERMISSION_SCOPES = pf_config.PERMISSION_SCOPES
EDITABLE_SCOPES = ("workspace", "configuration")
PROTECTED_SCOPES = ("backups", "recovery", "private_state")
# `pf permissions` verbs (classify_command); bare `pf permissions` no longer changes anything (OD-A23-08).
PERMISSIONS_VERBS = ("check", "plan", "apply", "-h", "--help")
# Routes whose trusted-context check skips the app-config load and whose lock takes no app-config snapshot.
NO_CONFIG_ROUTES = frozenset({"config", "permissions apply"})
SCOPE_LABELS = {"workspace": "Workspace", "configuration": "Configuration", "control": "Control release",
                "backups": "Backups", "recovery": "Recovery bundles", "private_state": "Private state"}
PERMISSION_DETAIL_LIMIT = 50
PERMISSION_BLOCKER_LIMIT = 10
GROUP_MEMBER_LIMIT = 20
PERMISSION_HOOKS = ("after-plan", "after-fence", "between-chown-chmod", "after-verify", "after-approval-copy",
                    "after-approval")
PERMISSIONS_CANCELLED = "permissions-cancelled: Cancelled; no permission was changed and no policy was approved."


class PermissionFault(Exception):
    """Test seam only (Controller.permission_fault): an interruption after K applied operations."""


class _EffectFree(Failure):
    """A step 9 refusal after the fences were restored: the run changed nothing (section 3.6)."""


class _VerifyFailed(Failure):
    """permissions-verify-failed after the re-inventory (section 3.6 step 11): the journal stays interrupted."""


_COPY_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_COPY_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _copy_unsupported(shown, detail="links and special files are never copied"):
    return Failure(f"Unsupported local deployment path: {shown} ({detail})")


def _copy_open(dir_fd, name, flags, info, shown):
    """Open ``name`` below ``dir_fd`` without following a link and require the identity its lstat saw."""
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _copy_unsupported(shown) from exc
        raise
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)             or stat.S_IFMT(opened.st_mode) != stat.S_IFMT(info.st_mode):
        os.close(fd)
        raise _copy_unsupported(shown, "replaced while it was copied; nothing was read through it")
    return fd


def _copy_fresh_at(source_dir_fd, source_name, target_dir_fd, target_name, shown):
    """One entry of copy_fresh, relative to held directory descriptors on both sides (sections 3.10, S5)."""
    info = os.stat(source_name, dir_fd=source_dir_fd, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        source_fd = _copy_open(source_dir_fd, source_name, _COPY_DIR_FLAGS, info, shown)
        try:
            os.mkdir(target_name, 0o700, dir_fd=target_dir_fd)  # content-only: the mode comes from its scope target
            try:
                target_fd = os.open(target_name, _COPY_DIR_FLAGS, dir_fd=target_dir_fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise Failure(f"{shown}: the new copy was replaced while it was written; nothing was written "
                                  "through it.") from exc
                raise
            try:
                made = os.fstat(target_fd)
                if made.st_uid != os.geteuid() or stat.S_IMODE(made.st_mode) & 0o077:
                    raise Failure(f"{shown}: the new copy was replaced while it was written; nothing was written "
                                  "through it.")
                for name in sorted(os.listdir(source_fd)):
                    _copy_fresh_at(source_fd, name, target_fd, name, f"{shown}/{name}")
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)
    elif stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            raise _copy_unsupported(shown, f"{info.st_nlink} hard links; a name outside may share the file")
        source_fd = _copy_open(source_dir_fd, source_name, _COPY_FILE_FLAGS, info, shown)
        temporary = "." + target_name + ".fresh-" + uuid.uuid4().hex[:8]
        try:
            if os.fstat(source_fd).st_nlink != 1:
                raise _copy_unsupported(shown, "hard links; a name outside may share the file")
            target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                                dir_fd=target_dir_fd)
            try:
                with os.fdopen(source_fd, "rb", closefd=False) as reader,                         os.fdopen(target_fd, "wb", closefd=False) as writer:
                    shutil.copyfileobj(reader, writer)  # bytes only: no mode, no owner, no xattr
                os.replace(temporary, target_name, src_dir_fd=target_dir_fd, dst_dir_fd=target_dir_fd)
            except BaseException:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=target_dir_fd)
                raise
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)
    else:
        raise _copy_unsupported(shown)


def copy_fresh(source, destination):
    """Content-only copy (section 3.10): directories are created 0700 and regular files are copied into new inodes;
    no mode, owner or extended attribute (ACL) is copied. A link, special file or hard-linked file raises Failure.
    Below the two given parents every step is descriptor-relative and no-follow on both sides: a source entry is
    opened O_NOFOLLOW and must keep the identity its lstat saw, and a new directory is entered through a no-follow
    descriptor, so an editor's swap can neither redirect a read nor a write. A file is copied to a private temporary
    sibling and renamed over ``destination``, so the result is always a new inode."""
    source, destination = Path(source), Path(destination)
    source_parent = os.open(str(source.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        target_parent = os.open(str(destination.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            _copy_fresh_at(source_parent, source.name, target_parent, destination.name, str(source))
        finally:
            os.close(target_parent)
    finally:
        os.close(source_parent)


def permission_confirm(phrase, summary):
    """The one typed confirmation of `pf permissions apply` (and --resume/--abandon): a mismatch, end of input or
    Ctrl-C is permissions-cancelled (the shared confirm() would give a generic mismatch copy)."""
    log(summary)
    if unattended():
        raise Failure("This operation requires an interactive terminal; no --yes bypass exists.")
    try:
        answer = input_line(f"Type exactly '{phrase}': ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise Failure(PERMISSIONS_CANCELLED) from exc
    if answer != phrase:
        raise Failure(PERMISSIONS_CANCELLED)


@dataclasses.dataclass(frozen=True)
class EffectivePolicy:
    """The permission policy in force (section 3.3): an approved record, or the derived policy (revision 0)."""

    policy: dict
    revision: int
    kind: str                 # "approved" | "derived"
    record_sha256: object     # sha256 of the record bytes, None when derived
    record: object
    record_bytes: object
    # Derived policy only (section 3.5): storage scopes whose root is unsafe or unreadable, so no group was read from
    # it; their group is a placeholder that is never shown, proposed or applied (the scope is blocked).
    unavailable: frozenset = frozenset()

    @property
    def label(self):
        if self.kind == "approved":
            return f"permission policy revision {self.revision} (permission policy {self.record['policy_sha256'][:12]})"
        return "permission policy not approved (derived)"


@dataclasses.dataclass
class ScopePlan:
    """One scope of a permission plan (section 3.5): inventory, differences, ceiling, freeze and blockers."""

    scope: str
    root: Path
    target: object
    gid: object
    inventory: object = None
    executables: frozenset = frozenset()
    changes: list = dataclasses.field(default_factory=list)
    categories: dict = dataclasses.field(default_factory=lambda: {"group": 0, "mode": 0, "special-bits": 0})
    differs: int = 0
    violations: list = dataclasses.field(default_factory=list)
    freeze: str = "not needed"
    blockers: list = dataclasses.field(default_factory=list)
    notes: list = dataclasses.field(default_factory=list)
    group_unavailable: bool = False

    @property
    def entries(self):
        return self.inventory.entries if self.inventory is not None else ()

    @property
    def total(self):
        return len(self.entries)


@dataclasses.dataclass
class PermissionPlan:
    policy: dict
    policy_sha256: str
    scopes: tuple
    scope_plans: dict
    changes: list
    changes_bytes: bytes
    plan_sha256: str
    gids: dict

    @property
    def freeze_scopes(self):
        return [scope for scope in self.scopes if self.scope_plans[scope].freeze == "needed"]

    @property
    def blockers(self):
        return [(scope, *item) for scope in self.scopes for item in self.scope_plans[scope].blockers]


def permission_entry_target(target, gid, entry, executables):
    """(mode, gid) target of one inventoried entry (sections 3.2/3.4). Executables come from approved metadata
    (``executables``: scope-relative paths), never from a file extension."""
    if entry.type == "dir":
        mode = target.dir_mode
    elif target.exec_mode is not None and entry.relative in executables:
        mode = target.exec_mode
    else:
        mode = target.file_mode
    return mode, (gid if target.gid_rule == "set" else entry.gid)


def access_reading(mode):
    """A friendly reading of a folder mode for the wizard's 'Now' line (never an input)."""
    group = (mode >> 3) & 7
    text = ACCESS_LEVELS.get(group & 6, "group access " + pf_config.symbolic_mode(mode, "dir"))
    return text + ("; keeps the folder's group on new files" if mode & 0o2000 else "")


ACCESS_LEVELS = {6: pf_config.ACCESS_LABELS["read_write"], 4: pf_config.ACCESS_LABELS["read_only"],
                 0: pf_config.ACCESS_LABELS["none"]}


def depth_order(relative):
    """Reverse depth order of the apply (section 3.6 step 10): deepest first, the scope root last."""
    return (-(relative.count("/") + 1) if relative else 0, relative)


@dataclasses.dataclass(frozen=True)
class OperationView:
    """PF-A3.2 (section 4.2): what a PENDING_ROUTES predicate sees of the blocking operation. A permissions view has
    ``kind == "permissions"``, the A2.3 phase and no plan or journal."""

    kind: str
    phase: str
    plan: object = None
    journal: object = None

    def effect_state(self, effect_id):
        return pf_config.effect_state(self.journal, effect_id) if self.journal is not None else None


def _view_index(view):
    """A one-operation OperationIndex of ``view`` (the gate's input for a PENDING_ROUTES predicate)."""
    if view.kind == "permissions":
        return pf_config.OperationIndex((), (), (), (), {"operation": "permissions", "phase": view.phase,
                                                         "operation_id": "permissions"}, None, False, None, 0)
    entry = pf_config.OperationEntry(view.plan["operation_id"], "blocking", plan=view.plan, journal=view.journal)
    return pf_config.OperationIndex((entry,), (entry,), (), (), None, None, False, None, 0)


def _gate_accepts(route):
    """A PENDING_ROUTES predicate: whether the section 3.3 gate lets ``route`` act next to ``view`` (enter it as
    resume or an alias, supersede it, or run beside it)."""
    def accepts(view):
        bundle = (view.plan or {}).get("input_bundle") or {}
        decision = pf_config.gate_decision(_view_index(view), route, slug="instance",
                                           request={"bundle_id": bundle.get("bundle_id")})
        return decision.action != "refuse"

    return accepts


# Explicit routes next to an open operation (LIFECYCLE.md section 1, step 2; PF-A3.2 section 3.3). A command absent
# from this table is refused while an operation is open. The predicates are the section 3.3 gate itself
# (pf_config.gate_decision); the descriptions are the human text of each route.
PENDING_ROUTES = {
    "resume": (
        _gate_accepts("resume"),
        "continue the interrupted operation from its journal (or reopen the unchanged deployment when no data or "
        "source effect started)",
    ),
    "rollback": (
        _gate_accepts("rollback"),
        "roll back to a healthy checkpoint (use --restore-db when data/schema may have changed; an emergency or "
        "partial capture is evidence and data, never a rollback target)",
    ),
    "abort-deploy": (
        _gate_accepts("abort-deploy"),
        "remove the incomplete first deployment (after frontend access opened, the current database is preserved "
        "first)",
    ),
    # PF-A3.3 (section 4.2): an open cleanup is resumed by `pf cleanup --apply` with the same selectors.
    "cleanup": (
        _gate_accepts("cleanup apply"),
        "resume the interrupted cleanup (`pf cleanup --apply` with the same selectors aliases `pf resume`)",
    ),
    "purge": (
        _gate_accepts("purge"),
        "resume the recorded purge deletion plan with the already verified recovery bundle",
    ),
    # PF-A2.3: the open permission apply journal (section 3.7).
    "permissions apply": (
        _gate_accepts("permissions apply"),
        "resume (--resume) or compensate (--abandon) the interrupted permission apply; once its permission policy "
        "revision is written only --resume is legal",
    ),
    # PF-A3.1: an attended emergency capture next to an interrupted lifecycle operation; journal-less (section 4.2).
    "backup emergency": (
        _gate_accepts("backup emergency"),
        "capture the current data as emergency preservation (does not change the interrupted operation)",
    ),
    # PF-A3.2 (section 4.2): the two routes whose DISPATCH pending cell became their own name.
    "backup": (
        _gate_accepts("backup"),
        "resume the interrupted backup (`pf backup` aliases `pf resume`), or capture a manual backup while an "
        "activated deployment waits for its workspace refresh",
    ),
    "restore-instance": (
        _gate_accepts("restore-instance"),
        "resume the interrupted exact restore of the same bundle (`pf restore-instance <same bundle>` aliases "
        "`pf resume`)",
    ),
}
# The CLI spelling of a route whose DISPATCH key is not its command line (section 4.2).
PENDING_ROUTE_COMMANDS = dict(pf_config.ROUTE_COMMANDS)
# PF-A3.2: the routes whose resume uses the frozen admin configuration of the operation they may alias (section 3.8).
ALIAS_ROUTES = {"purge": ("purge",), "restore-instance": ("restore-instance", "restore-side-by-side"),
                "abort-deploy": ("abort-deploy",), "backup": ("backup",), "cleanup apply": ("cleanup",)}
# Lifecycle routes that freeze the admin configuration with their operation (section 3.8; not config/permissions).
LIFECYCLE_ROUTES = frozenset({"deploy", "update", "rollback", "reset-db", "backup", "purge", "restore-instance",
                              "abort-deploy", "resume", "backup emergency", "release-check", "cleanup apply"})
CRASH_POINTS = ("before-intent", "after-intent", "after-effect")
# PF-A3.3 (section 6): the test-only seam points between the steps of a functional verification, a teardown, a
# generation seal and the cleanup loop (``inside:<label>``); never settable from the CLI.
INSIDE_PREFIX = "inside:"


class SimulatedCrash(BaseException):
    """Test seam (section 6): raised by Controller._crash_point; bypasses every ``except Exception`` and fail_closed,
    as a dying process does. Never raised in production (the seam is None and not settable from the CLI)."""


class EffectStep:
    """The mutable record of one running effect (section 3.1 step 4): evidence, retained artifacts and whether the
    step proved a partial state."""

    def __init__(self, effect, evidence=None):
        self.effect = effect
        self.evidence = evidence
        self.retained = []
        self.partial = False
        self.outcome = "complete"


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
        # PF-A2.3: the permission targets in force (read once per operation) and the apply's counters. The two
        # test seams (section 3.9) are None in production and never settable from the CLI.
        self._permission_targets = None
        self._effect_seq = self._effect_count = self._changed_count = 0
        self._current_scope = None
        self.permission_fault = None
        self.permission_hook = None
        # PF-A3.2: the operation this process opened or re-entered (plan, its hash and the latest journal generation),
        # the gate's decision, the effect a child belongs to, the frozen admin configuration source chosen before the
        # lock, and the in-process crash seam of the tests (section 6; None in production, never settable from the CLI).
        self.plan = None
        self.plan_sha256 = None
        self.journal = None
        self.gate = None
        self._current_effect = None
        self._config_entry = None
        self._crash_point = None
        self._deployed_tree = None
        self._workspace_note = None
        self._config_bytes = None
        self._interval_entry = None
        self._config_selected = False
        self._reentered = False
        # PF-A3.3: the isolated topology a bound call targets (section 3.1 step 6), the phases whose capacity this
        # process re-checked, and whether the lock was taken observe-only (no operation directory, section 3.9).
        self._bound = None
        self._capacity_checked = set()
        self._observe_only = False

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

    def load_app_config(self):
        """Read-only strict load of config/pf-config.json against the installed app schema.

        Missing or invalid configuration is a diagnostic failure; the file is
        never created from the template or rewritten here.
        """
        if self._config_entry is not None:
            # PF-A3.2 (section 3.8): a resumed operation parses the copy it froze, never the editable file.
            return self.load_frozen_admin_config(self._config_entry)
        path = self.config_dir / "pf-config.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except OSError as exc:
            raise Failure(
                f"Runtime configuration is missing or unreadable: {path} ({exc.strerror}). "
                f"The controller does not create it; create it with '{self.pf_command()} config admin'."
            ) from exc
        config = self.parse_app_config(data, str(path))
        self._config_bytes = data
        return config

    def parse_app_config(self, data, label):
        """The shared parse and binding checks of pf-config.json bytes (the editable file or a frozen copy)."""
        path = label
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
            grp.getgrnam(config["backup_read_group"])
            grp.getgrnam(config["workspace_write_group"])
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
                stream=False, accept_exit=(0,), quiet=False):
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

        PF-A3.3 (section 4.7): ``accept_exit`` lists the exit codes returned as success; with anything but ``(0,)`` the
        result is (stdout, returncode). ``quiet``: the child's stdout and stderr never reach a Failure text, a log
        line, the journal or evidence; a refused exit, a timeout or a truncated stdout fails with the exit status only.
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
        accepted = result.returncode in accept_exit and not result.timed_out and not result.interrupted
        if quiet and accepted and result.stdout_truncated:
            raise Failure(f"{tool} failed (stdout truncated).")
        if not accepted:
            status = "timed out" if result.timed_out else "interrupted" if result.interrupted \
                else f"exit {result.returncode}"
            if quiet:
                raise Failure(f"{tool} failed ({status}).")
            raise Failure(f"{tool} failed ({status}).\n{pf_runner.failure_detail(result)}")
        if tuple(accept_exit) != (0,):
            return result.stdout.strip(), result.returncode
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
                operation = (self.plan or {}).get("kind") or self._operation_command or "the operation"
                text = (head + f" Every further Docker/Compose step is refused. {operation} stopped in phase "
                        f"{(self.journal or {}).get('phase') or 'unknown'}; review it with 'pf status --instance {context.slug}'. "
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

    def docker_inventory(self, *, scope=None):
        """Exact, read-only inventory of this instance's resources on the bound daemon (ARCH section 8). PF-A3.3:
        ``scope`` (project, UUID) inventories an isolated topology instead (its own project and label)."""
        if self._inventory_active:
            raise Failure("Internal error: nested Docker inventory.")
        self._inventory_active = True
        try:
            return self._observe_inventory(*(scope or (self.context.compose_project, self.context.instance_id)))
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

    def _observe_inventory(self, project, instance_id):
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

    def preflight_record_name(self):
        """The inventory preflight record of this process: ``inventory-preflight.json`` for a new operation; a
        re-entered operation keeps its original record and gets the next ``inventory-preflight-resume-<n>.json``."""
        if self.gate is None or self.gate.action != "reenter" or self.operation_dir is None:
            return "inventory-preflight.json"
        taken = [name for name in os.listdir(str(self.operation_dir))
                 if re.fullmatch(r"inventory-preflight-resume-[0-9]+\.json", name)]
        return f"inventory-preflight-resume-{len(taken) + 1}.json"

    def require_topology_owned(self, command):
        """Ownership preflight: refuse any blocker before Compose could adopt or recreate it (OD-A13-03)."""
        inventory = self.docker_inventory()
        self.write_private_json(self.preflight_record_name(), dict(inventory.record(), command=str(command)))
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
        self.write_private_json(self.preflight_record_name(), dict(inventory.record(), command=str(command)))
        present = [item for item in inventory.blockers + inventory.owned
                   if item.kind in ("container", "volume", "network")]
        if present:
            item = present[0]
            raise Failure(f"resource-target-not-empty: {command} requires an empty target: {item.kind} "
                          f"{self.resource_name(item)} ({item.cls}) already exists for project "
                          f"{self.context.compose_project}. Nothing was changed.")
        self._topology_checked = True
        return inventory

    def plan_for(self, kind, inventory, *, command, recovery_id=None, covered_image_refs=None, select=None):
        """A deletion plan of ``kind`` for this operation; blockers refuse before any confirmation."""
        observation = self.verify_daemon()
        try:
            plan = pf_docker.plan_deletion(inventory, kind=kind, operation_id=self.operation_id,
                                           daemon=observation, recovery_id=recovery_id,
                                           covered_image_refs=covered_image_refs, select=select)
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
        if kind != "isolated-topology":
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

    def write_deletion_plan(self, plan, *, name="deletion-plan.json", once=False):
        """Persist the binding plan durably (O_EXCL|O_NOFOLLOW temp, fsync, rename, directory fsync). PF-A3.3: the
        plan kinds isolated-topology and image-tags, a ``name`` below the operation directory and ``once`` (exclusive
        create: a write-once plan whose existing bytes must be the same plan)."""
        expected = {"purge": "bound", "abort-deploy": "none", "isolated-topology": "none",
                    "image-tags": "none"}.get(plan.get("kind"))
        if self.operation_dir is None or expected is None or plan.get("image_coverage") != expected \
                or plan.get("operation_id") != self.operation_id:
            raise Failure("plan-invalid: only a binding plan of this locked operation can be frozen; nothing was "
                          "deleted.")
        data = pf_docker.plan_bytes(plan)
        path = self.operation_dir / name
        if once:
            try:
                pf_instance.write_once(path.parent, path.name, data, 0o600)
            except FileExistsError as exc:
                raise Failure(f"plan-invalid: {name} of operation {self.operation_id} already exists; a write-once "
                              "deletion plan is never replaced. Nothing was deleted.") from exc
        else:
            pf_config._write_private(path, data, 0o600)
        return {"operation_id": self.operation_id, "path": str(path), "sha256": pf_instance.sha256_bytes(data)}

    def load_frozen_deletion_plan(self, kind, expected_sha256, *, name="deletion-plan.json", instance_id=None):
        """The frozen deletion plan of this operation (``deletion-plan.json`` in its own directory): exact path,
        regular file, hash, instance, kind and operation (PF-A3.2: the hash comes from the plan or the journal's
        deletion approval). PF-A3.3: ``name`` (a topology teardown or cleanup plan) and ``instance_id`` (a topology
        UUID; default this instance)."""
        def invalid(detail):
            return Failure("plan-invalid: The frozen deletion plan of this operation is missing, not the expected "
                           "regular file, of the wrong kind or operation, or does not match its recorded hash; "
                           "nothing was deleted.\n  detail: " + detail)

        path = self.operation_dir / name
        try:
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
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
            return pf_docker.load_plan(b"".join(chunks), expected_sha256=expected_sha256,
                                       instance_id=instance_id or self.context.instance_id, kind=kind,
                                       operation_id=self.operation_id)
        except pf_docker.DockerScopeError as exc:
            raise invalid("; ".join(finding.message for finding in exc.findings)) from exc

    def deletion_progress(self, progress="deletion-progress.json"):
        """The A1.3 deletion entries of this operation (``deletion-progress.json``); [] before the first item."""
        try:
            return pf_instance.read_private_list(self.operation_dir / progress)
        except pf_instance.ContextError as exc:
            raise Failure(f"plan-invalid: {progress} of operation {self.operation_id} is unreadable "
                          f"({exc}); nothing was deleted.") from exc

    def plan_drift(self, kind, key, reason, deleted):
        removed = sum(1 for entry in deleted if entry.get("outcome") == "removed")
        exc = Failure(f"plan-drift: Planned {kind} {key} changed after the plan was frozen ({reason}); deletion "
                      f"stopped. Already removed: {removed}. The journal keeps the frozen plan; inspect with "
                      f"'pf status --instance {self.context.slug}'.")
        exc.code = "plan-drift"
        return exc

    def require_plan_engine(self, plan, observation, deleted):
        """The verified daemon must be the engine the frozen plan was built on."""
        if plan["daemon"]["engine_id"] != observation.engine_id:
            raise self.plan_drift("daemon", plan["daemon"]["engine_id"],
                                  "the endpoint answers as engine " + observation.engine_id, deleted)

    def execute_deletion_plan(self, plan, *, progress="deletion-progress.json", on_item=None):
        """Execute exactly the frozen plan: re-observe every item and its users before its effect.

        Never prunes, never ``compose down``, never forces an image removal. PF-A3.2: the deleted
        list is persisted durably in ``deletion-progress.json`` after each item, so a resume continues
        the same closed plan and never adds a resource. PF-A3.3: ``progress`` names the list of a topology teardown
        or a cleanup plan; an isolated-topology plan re-inspects the inventory of its own project and UUID;
        ``on_item(item)`` runs before each item's removal (the test seam ``inside:<label>``).
        """
        scope = (plan["compose_project"], plan["instance_id"]) if plan.get("kind") == "isolated-topology" else None
        deleted = self.deletion_progress(progress)
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
        inventory = prove(self.docker_inventory(scope=scope), plan["candidates"])
        for item in plan["candidates"]:
            if (item["kind"], item["key"]) in done:
                continue
            prove(inventory, [item])
            by_id, by_name = inventory.index(item["kind"])
            if pf_docker.compare_identity(item, by_id, by_name) == "absent":
                deleted.append({"kind": item["kind"], "key": item["key"], "outcome": "already-absent"})
            else:
                if on_item is not None:
                    on_item(item)
                identity = item["identity"]
                if item["kind"] == "container":
                    self.docker("rm", "-f", identity["id"])
                elif item["kind"] == "network":
                    self.docker("network", "rm", identity["id"])
                elif item["kind"] == "volume":
                    self.docker("volume", "rm", identity["name"])
                else:
                    self.docker("image", "rm", identity["reference"])
                inventory = self.docker_inventory(scope=scope)
                by_id, by_name = inventory.index(item["kind"])
                if pf_docker.compare_identity(item, by_id, by_name) != "absent":
                    exc = Failure(f"plan-effect-unconfirmed: {item['kind']} {item['key']} is still present after "
                                  "removal; deletion stopped and the journal keeps the plan.")
                    exc.code = "plan-effect-unconfirmed"
                    raise exc
                deleted.append({"kind": item["kind"], "key": item["key"], "outcome": "removed"})
            pf_instance.rewrite_private_list(self.operation_dir / progress, deleted)
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
        """Application values for this process: the frozen snapshot inside an operation, else the proposal. PF-A3.3:
        inside ``bound(topology)`` the isolated topology's values (its generated database password)."""
        if self._bound is not None:
            return dict(self._bound.values)
        if self.frozen is not None:
            return dict(self.frozen.values)
        if self.operation_dir is not None:
            raise Failure("This operation has no frozen application configuration; config/.env was absent "
                          "when the operation started and has not been created by the operation itself.")
        if not (self.control_dir / "compose.nas.yaml").is_file():
            raise Failure("Missing installed control/compose.nas.yaml.")
        return self.load_app_env()

    def freeze_app_config(self, *, explicit=False, source_bytes=None):
        """Render the current ``config/.env`` into the operation's private immutable snapshot.

        Implicitly (from ``lock``) it freezes once; a later implicit call only verifies that
        the editable file still equals the frozen source. ``explicit=True`` is used by the
        operations that create or restore ``.env`` themselves. PF-A3.2: ``source_bytes`` (restore-instance) freezes
        the verified bundle ``.env`` bytes instead of the editable file, before the plan is written (section 3.4).
        """
        if self.operation_dir is None:
            raise Failure("Application configuration can only be frozen inside a locked operation.")
        path = self.config_dir / ".env"
        if source_bytes is not None:
            data = bytes(source_bytes)
            explicit = True
        else:
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
        self._permission_targets = None
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
        if str(command) in LIFECYCLE_ROUTES:
            self.freeze_admin_config()
        if freeze and (self.config_dir / ".env").is_file():
            self.freeze_app_config()

    def freeze_admin_config(self):
        """PF-A3.2 (section 3.8): the exact bytes of ``config/pf-config.json`` become ``admin-config.json`` (0400) and
        this operation parses only them from here on. A missing file freezes nothing (the routes that need it refused
        before the lock)."""
        path = self.config_dir / "pf-config.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise Failure(f"Cannot read {path}: {exc.strerror or exc}") from exc
        pf_instance._write_private_file(self.operation_dir / "admin-config.json", data, 0o400)
        if self._config is None or self._config_bytes != data:
            self._config = self.parse_app_config(data, str(path))
            self._config_bytes = data
        return {"sha256": pf_instance.sha256_bytes(data), "bytes": len(data)}

    def admin_config_ref(self):
        """$defs.config_ref of this operation's frozen admin configuration, or None."""
        try:
            data = pf_instance.read_bytes_nofollow(self.operation_dir / "admin-config.json")
        except FileNotFoundError:
            return None
        return {"sha256": pf_instance.sha256_bytes(data), "bytes": len(data)}

    def load_frozen_admin_config(self, entry):
        """The admin configuration operation ``entry`` froze, verified against its plan (section 3.8); a note when the
        editable file differs or is absent."""
        expected = entry.plan.get("admin_config") if entry.plan else None
        path = self.context.operations_dir / entry.operation_id / "admin-config.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except OSError as exc:
            raise Failure(f"plan-input-changed: the frozen admin configuration of operation {entry.operation_id} "
                          f"is unreadable ({exc.strerror or exc}). Nothing was changed.") from exc
        if expected is None or pf_instance.sha256_bytes(data) != expected["sha256"] or len(data) != expected["bytes"]:
            raise Failure(f"plan-input-changed: the frozen admin configuration of operation {entry.operation_id} no "
                          "longer matches its plan (admin-config.json hash). Nothing was changed.")
        config = self.parse_app_config(data, str(path))
        try:
            editable = pf_instance.read_bytes_nofollow(self.config_dir / "pf-config.json")
        except OSError:
            editable = None
        if editable != data:
            log(f"note: admin-config-proposal-differs: config/pf-config.json differs from (or is missing against) the "
                f"copy operation {entry.operation_id} froze; it stays a proposal.")
        return config

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
        self.plan = self.plan_sha256 = self.journal = None
        self._current_effect = None
        self.release_deployed_tree()
        self.reset_operation_scope()
        if self._runner is not None:
            self._runner.effects_path = None
            self._runner.spawn_callback = None

    # ------------------------------------------- operation index and journal (PF-A3.2 sections 3.1-3.3)

    def operation_index(self):
        """Read-only (section 3.2): the classified operations of this instance. Never creates or repairs anything; an
        operations directory that cannot be opened safely is fail-closed evidence (every mutating route refuses)."""
        try:
            files, overflow = pf_instance.scan_operations(self.context.operations_dir,
                                                          limit=pf_instance.OPERATION_SCAN_LIMIT)
        except pf_instance.ContextError as exc:
            files, overflow = (pf_instance.OperationFiles("00000000T000000Z-operations-00000000", None, None,
                                                          str(exc)),), 0
        try:
            pending = pf_instance.read_bytes_nofollow(self.pending)
        except FileNotFoundError:
            pending = None
        except OSError:
            pending = b"\x00unreadable"
        return pf_config.classify_operations(
            files, permissions_journal=pending, overflow=overflow,
            validate=lambda value, name, plan=None: lifecycle_errors(value, name, plan=plan))

    def runner_records(self, index=None):
        """Section 3.11a: every runner record (``unresolved-effects.json``) with its ownership, as
        [(record, "open" | "reconciled", sequence)]. Read-only; the files are never edited or deleted."""
        index = index if index is not None else self.operation_index()
        found = []
        try:
            names = sorted(os.listdir(str(self.context.operations_dir)))
        except OSError:
            return found
        for name in names:
            path = self.context.operations_dir / name / "unresolved-effects.json"
            if not os.path.lexists(str(path)):
                continue
            try:
                items = pf_runner.load_unresolved_effects(path)
            except pf_runner.RunnerError as exc:
                found.append(({"operation_id": name, "outcome": "unreadable", "tool": "?", "argv": [],
                               "recorded_at": "?", "error": str(exc)}, "open", None))
                continue
            state, sequence = pf_config.runner_records_state(index.entry(name), index)
            for item in items:
                found.append((dict(item, operation_id=name), state, sequence))
        return found

    def unresolved_effects(self, index=None):
        """The open runner records of section 3.11a (records of a journaled, terminal operation are reconciled)."""
        return [record for record, state, _ in self.runner_records(index) if state == "open"]

    @staticmethod
    def _record_summary(item):
        effect = item.get("effect")
        if isinstance(effect, dict):
            # The descriptor names what to reconcile (kind, verb, targets); argv is the detail.
            return " ".join(str(part) for part in (effect.get("kind"), effect.get("verb"), effect.get("service"),
                                                   effect.get("database"), *(effect.get("targets") or []),
                                                   effect.get("statement")) if part)
        return " ".join(item.get("argv", []))[:120]

    def log_effects(self, index=None):
        records = self.unresolved_effects(index)
        if not records:
            return
        log(f"UNRESOLVED EFFECTS recorded by earlier operations: {len(records)} (observe before retrying):")
        for item in records[-10:]:
            log(f"  {item.get('recorded_at')} {item.get('operation_id')}: {item.get('tool')} "
                f"{self._record_summary(item)} -> {item.get('outcome')}")

    def read_journal(self):
        """The A2.3 permissions apply journal (``state/pending.json``), None, or an unreadable-journal marker. Never
        creates or repairs it. PF-A3.2: lifecycle operations never write it (their journals live under operations/)."""
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

    def plan_effect(self, effect_id):
        for effect in self.plan["effects"]:
            if effect["effect_id"] == effect_id:
                return effect
        raise Failure(f"Internal error: effect {effect_id} is not in the plan of operation {self.operation_id}.")

    def effect_state(self, effect_id):
        return pf_config.effect_state(self.journal, effect_id)

    def effects_of(self, role=None, *, type=None, target=None):
        """The plan effects with ``role`` (pf_config.effect_role), ``type`` or a ``target`` prefix."""
        found = []
        for effect in self.plan["effects"]:
            if role is not None and pf_config.effect_role(effect) != role:
                continue
            if type is not None and effect["type"] != type:
                continue
            if target is not None and not effect["target"].startswith(target):
                continue
            found.append(effect)
        return found

    @staticmethod
    def precondition(effect, key):
        """The value of the ``<key>:<value>`` precondition of a plan effect, or None."""
        for item in effect["preconditions"]:
            if item.startswith(key + ":"):
                return item[len(key) + 1:]
        return None

    def _journal_draft(self, plan, plan_sha256, deletion, confirmed):
        journal = {
            "schema_version": 1, "operation_id": plan["operation_id"], "plan_sha256": plan_sha256,
            "kind": plan["kind"], "sequence": 1, "phase": "planned", "updated_at": utc(),
            "approvals": [{"plan_sha256": plan_sha256, "confirmed_at": confirmed, "method": "typed-phrase"}]
            if plan["confirmation"] is not None else [],
            "effects": [{"effect_id": effect["effect_id"], "state": "not_started", "observed_at": None,
                         "evidence": None} for effect in plan["effects"]],
            "unresolved_effect": None, "retained_artifacts": [], "last_error": None, "legal_next": [],
            "result": None, "deletion": deletion}
        journal["legal_next"] = pf_config.legal_next(plan, journal, slug=self.context.slug)
        return journal

    def permission_policy_ref(self):
        found = self.read_permission_record()
        if found is None:
            return None
        return {"revision": found[0]["revision"], "sha256": pf_instance.sha256_bytes(found[1])}

    def open_operation(self, kind, *, effects, workspace, confirmation, images, source, coverage=(), supersedes=None,
                       input_bundle=None, deletion=None, inventory_sha256=None, deletion_plan_sha256=None,
                       summary_text=None):
        """Section 3.1 steps 2-3: the frozen OperationPlan (validated, exclusive create) and journal generation 1,
        right after the final confirmation and before the first effect. A problem is an internal error raised before
        any write."""
        if self.operation_dir is None or self.plan is not None:
            raise Failure("Internal error: an operation plan is written once, inside its locked operation.")
        numbered = []
        for index, effect in enumerate(effects, 1):
            numbered.append({"effect_id": f"e{index:04d}", "type": effect["type"], "target": effect["target"],
                             "phase": effect["phase"], "preconditions": [str(item) for item in
                                                                          effect.get("preconditions", ())],
                             "postcondition": effect["postcondition"],
                             "preservation_refs": list(effect.get("preservation_refs", ()))})
        context = self.context
        frozen_ref = None
        if self.frozen is not None:
            rendered = pf_instance.read_bytes_nofollow(self.frozen.env_file)
            frozen_ref = {"sha256": pf_instance.sha256_bytes(rendered), "bytes": len(rendered)}
        plan = {
            "schema_version": 1, "operation_id": self.operation_id, "kind": kind, "created_at": utc(),
            "instance": {"instance_id": context.instance_id, "slug": context.slug,
                         "compose_project": context.compose_project, "daemon_engine_id": context.daemon.engine_id,
                         "record_sha256": context.record_sha256},
            "producer": self.producer(),
            "environment_policy": {"revision": context.approved_policy.revision,
                                   "sha256": context.approved_policy.sha256},
            "permission_policy": self.permission_policy_ref(),
            "source": source, "images": images, "frozen_config": frozen_ref, "admin_config": self.admin_config_ref(),
            "resources": {"inventory_sha256": inventory_sha256, "deletion_plan_sha256": deletion_plan_sha256},
            "coverage": list(coverage), "confirmation": confirmation,
            "limits": {"timeout_seconds": int(TIMEOUT_DATA),
                       "minimum_free_bytes": int(self.config["minimum_free_mb"]) * 1024 * 1024},
            "effects": numbered, "recovery_route": [], "supersedes": supersedes, "workspace": workspace,
            "input_bundle": input_bundle,
        }
        confirmed = utc()
        plan["recovery_route"] = pf_config.legal_next(plan, self._journal_draft(plan, "0" * 64, deletion, confirmed),
                                                      slug=context.slug)
        problems = lifecycle_errors(plan, "operation_plan")
        if problems:
            raise Failure(f"Internal error: the {kind} operation plan is invalid ({problems[0]}); nothing was written "
                          "or changed.")
        data = pf_instance.normalize_json(plan)
        plan_sha256 = pf_instance.sha256_bytes(data)
        journal = self._journal_draft(plan, plan_sha256, deletion, confirmed)
        problems = lifecycle_errors(journal, "operation_journal", plan=plan)
        if problems:
            raise Failure(f"Internal error: the first journal generation of the {kind} operation is invalid "
                          f"({problems[0]}); nothing was written or changed.")
        pf_instance.write_plan_once(self.operation_dir, data)
        if summary_text is not None and confirmation is not None:
            # PF-A3.3 (section 2.2): the exact confirmed summary text, bound by plan.confirmation.summary_sha256.
            text = summary_text.encode("utf-8")
            if pf_instance.sha256_bytes(text) != confirmation["summary_sha256"]:
                raise Failure("Internal error: the confirmation summary does not match its plan hash.")
            pf_instance.write_once(self.operation_dir, "confirmation-summary.txt", text)
        self.plan, self.plan_sha256 = plan, plan_sha256
        pf_instance.write_journal_generation(self.operation_dir, pf_instance.normalize_json(journal))
        self.journal = journal
        self._append_attempt("open")
        self._arm_runner()
        return plan

    def _arm_runner(self):
        self.runner.spawn_callback = self._record_child

    def journal_update(self, *, phase=None, effects=None, unresolved=..., retained=(), last_error=..., result=...,
                       deletion=..., approval=None):
        """Section 3.1 step 6: one journal generation. The current generation is re-read no-follow and must be the
        one this process wrote (else ``journal-changed``, no write); the next one is validated before it is
        written. ``effects``: {effect_id: (state, observed_at, evidence)}."""
        operation, slug = self.operation_id, self.context.slug
        path = self.operation_dir / "journal.json"
        expected = self.journal["sequence"]
        try:
            current = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(path), label=str(path))
        except (OSError, pf_instance.ContextError) as exc:
            raise Failure(f"journal-changed: the journal of operation {operation} cannot be re-read ({exc}); it "
                          f"stopped before its next effect. Run 'pf --instance {slug} status'.") from exc
        if not isinstance(current, dict) or current != self.journal:
            found = current.get("sequence") if isinstance(current, dict) else "?"
            raise Failure(f"journal-changed: the journal of operation {operation} changed under this process "
                          f"(sequence {expected} expected, {found} found); it stopped before its next effect. Run "
                          f"'pf --instance {slug} status'.")
        new = copy.deepcopy(current)
        new["sequence"] = expected + 1
        new["updated_at"] = utc()
        if phase is not None:
            new["phase"] = phase
        for effect_id, (state, observed_at, evidence) in (effects or {}).items():
            for item in new["effects"]:
                if item["effect_id"] == effect_id:
                    item.update(state=state, observed_at=observed_at, evidence=self._evidence(evidence))
        if unresolved is not ...:
            new["unresolved_effect"] = unresolved
        for artifact in retained:
            if artifact not in new["retained_artifacts"]:
                new["retained_artifacts"].append(artifact)
        if last_error is not ...:
            new["last_error"] = last_error
        if result is not ...:
            new["result"] = result
        if deletion is not ...:
            new["deletion"] = deletion
        if approval is not None:
            new["approvals"].append(approval)
        new["legal_next"] = pf_config.legal_next(self.plan, new, slug=slug)
        problems = lifecycle_errors(new, "operation_journal", plan=self.plan)
        if problems:
            raise Failure(f"Internal error: journal generation {new['sequence']} of operation {operation} is invalid "
                          f"({problems[0]}); it was not written.")
        pf_instance.write_journal_generation(self.operation_dir, pf_instance.normalize_json(new))
        self.journal = new
        return new

    def _evidence(self, value):
        """Evidence holds identities only (AM-9): redacted, one line, at most 2000 characters."""
        if value is None:
            return None
        text = " ".join(self.redactor.text(str(value)).split())
        return text[:pf_config.EVIDENCE_LIMIT] or None

    def _error(self, exc, code=None):
        """$defs.error of a failure: its code and first line (redacted, at most 2000 characters, no value)."""
        if code is None:
            if isinstance(exc, KeyboardInterrupt):
                code = "interrupted"
            elif isinstance(exc, Failure) and "(timed out)" in str(exc).split("\n", 1)[0]:
                code = "timeout"
            elif isinstance(exc, OSError):
                code = "os-error"
            else:
                code = getattr(exc, "code", None)
                head = str(exc).split(":", 1)[0]
                if not (isinstance(code, str) and re.fullmatch(r"[a-z0-9-]{1,64}", code)):
                    code = head if re.fullmatch(r"[a-z0-9-]{3,64}", head) else "failed"
        lines = [line for line in self.redactor.text(str(exc) or type(exc).__name__).splitlines() if line.strip()]
        message = (lines[0] if lines else type(exc).__name__)[:2000]
        return {"code": code, "message": message}

    def _crash(self, effect_id, when):
        """The in-process crash seam (section 6): ``_crash_point`` is (effect id or target, point) or a callable."""
        point = self._crash_point
        if point is None:
            return
        if callable(point):
            point(effect_id, when)
            return
        selector, wanted = point
        effect = self.plan_effect(effect_id)
        if wanted == when and selector in (effect_id, effect["target"], effect["target"].split(":", 1)[0] + ":*"):
            raise SimulatedCrash(f"{when} {effect_id}")

    @contextlib.contextmanager
    def effect(self, effect_id, *, evidence=None):
        """Section 3.1 step 4 for one plan effect: the intent generation (state unknown, unresolved_effect) before the
        step, the completion generation after the step observed its postcondition, a best-effort failure generation
        when it fails. The child process groups it spawns are recorded in children.json."""
        effect = self.plan_effect(effect_id)
        self._crash(effect_id, "before-intent")
        self.journal_update(phase=effect["phase"], effects={effect_id: ("unknown", None, evidence)},
                            unresolved=effect_id)
        step = EffectStep(effect, evidence)
        self._current_effect = effect_id
        try:
            self._crash(effect_id, "after-intent")
            yield step
            self._crash(effect_id, "after-effect")
        except SimulatedCrash:
            raise
        except BaseException as exc:
            self._current_effect = None
            self._record_failure(effect_id, exc, partial=step.partial, evidence=step.evidence, retained=step.retained)
            raise
        finally:
            self._current_effect = None
        self.journal_update(effects={effect_id: (step.outcome, utc(), step.evidence)}, unresolved=None,
                            retained=step.retained)

    def _record_failure(self, effect_id, exc, *, partial=False, evidence=None, retained=()):
        """The one best-effort failure generation of a failing effect (section 3.1 step 4)."""
        try:
            self.journal_update(effects={effect_id: ("partial" if partial else "unknown", None, evidence)},
                                unresolved=effect_id, last_error=self._error(exc), retained=retained)
        except (Failure, OSError, ValueError) as inner:
            log("WARNING: the failure of effect " + effect_id + " could not be journaled: "
                + (str(inner).splitlines() or ["?"])[0])

    def run_effect(self, effect_id, action, *, evidence=None):
        """Run one plan effect unless the journal records it complete. An effect an earlier process left unknown or
        partial is observed first (section 3.5): observed complete -> recorded complete without a new child;
        needs_operator -> the terminal generation; refused -> the operation stays open; else redone under a new
        intent generation (its cleanup first). Returns the EffectStep, or None when nothing ran."""
        state = self.effect_state(effect_id)
        if state == "complete":
            return None
        cleanup = None
        if state in ("unknown", "partial"):
            effect = self.plan_effect(effect_id)
            observed, detail, cleanup = self.observe_effect(self.plan, self.journal, effect)
            log(f"observed: {effect_id} {effect['type']} {effect['target']}: {observed} ({detail})")
            if observed == "complete":
                self.journal_update(effects={effect_id: ("complete", utc(), detail)}, unresolved=None,
                                    retained=self.observed_retained(effect))
                return None
            if observed == "needs_operator":
                raise self.write_needs_operator(effect, detail)
            if observed == "refuse":
                raise Failure(detail)
            attempts = 1 + sum(1 for line in [str(self.journal_effect(effect_id).get("evidence") or "")]
                               if "attempt " in line)
            evidence = f"attempt {attempts + 1} after: {detail}" + (f"; {evidence}" if evidence else "")
        with self.effect(effect_id, evidence=evidence) as step:
            if cleanup is not None:
                cleanup()
            action(step)
        return step

    def observed_retained(self, effect):
        """The retained artifacts of an effect an earlier process completed but could not journal (section 3.5): the
        retained workspace generation of W2, a displaced checkpoint-history tree, the bundle of a capture."""
        target = effect["target"]
        if target.startswith("workspace:retain:"):
            return [{"kind": "workspace-generation", "name": target.split(":", 2)[2], "sha256": None}]
        if effect["type"] == "file-write" and target.startswith("checkpoint-history"):
            name = f"{self.config['project']}.pre-restore-{self.operation_id[-8:]}"
            if os.path.lexists(str(self.revisions_root / name)):
                return [{"kind": "checkpoint-history", "name": name, "sha256": None}]
            return []
        if effect["type"] == "capture":
            bundle_id = self.attempt_bundle(effect, self.journal_effect(effect["effect_id"])["evidence"])
            try:
                if target == "purge-bundle":
                    view = self.verify_recovery(self.recovery_root / bundle_id)
                    return [{"kind": "purge-bundle", "name": bundle_id, "sha256": view.manifest_sha256}]
                view = self.verify_snapshot(bundle_id)
            except Failure:
                return []
            return [{"kind": "checkpoint", "name": view.bundle_id, "sha256": view.manifest_sha256}]
        return []

    def journal_effect(self, effect_id):
        return next(item for item in self.journal["effects"] if item["effect_id"] == effect_id)

    def write_needs_operator(self, effect, detail):
        """The terminal ``needs_operator`` generation (no automatic continuation is safe); returns the Failure the
        caller raises (exit 1)."""
        code = "database-switch-unknown" if effect["type"] == "database-switch" else "effect-unknown"
        steps = None
        if code == "database-switch-unknown":
            message = (f"database-switch-unknown: the database rename of operation {self.operation_id} left "
                       f"{detail}; neither the old nor the new binding is complete.")
        else:
            message = (f"effect-unknown: effect {effect['effect_id']} ({effect['type']} {effect['target']}) of "
                       f"operation {self.operation_id} has an unknown outcome: {detail}. Retrying could repeat a "
                       "change that may already have happened.")
        self.journal_update(phase="needs_operator", unresolved=effect["effect_id"],
                            effects={effect["effect_id"]: ("unknown", None, detail)},
                            last_error={"code": code, "message": message[:2000]},
                            result={"outcome": "needs_operator", "deployment_id": None})
        steps = "; ".join(self.journal["legal_next"]) or "none"
        return Failure(f"{message} Supported next steps: {steps}.")

    def close_operation(self, phase, *, deployment_id=None, last_error=...):
        """The terminal generation: completed (succeeded), cancelled (own staging removed first) or
        failed_preserved."""
        outcome = {"completed": "succeeded", "cancelled": "cancelled", "failed_preserved": "failed_preserved"}[phase]
        if phase == "cancelled":
            self.remove_own_staging()
        self.journal_update(phase=phase, result={"outcome": outcome, "deployment_id": deployment_id},
                            unresolved=None, last_error=last_error)

    def _append_attempt(self, action, route=None):
        """Section 3.1 step 7: one entry per process that opens or re-enters the operation."""
        path = self.operation_dir / "attempts.json"
        entries = pf_instance.read_private_list(path)
        entries.append({"attempt": len(entries) + 1, "pid": os.getpid(), "boot_id": pf_instance.boot_id(),
                        "started_at": utc(), "route": route or self._operation_command or "operation",
                        "release_id": self.context.control.release_id, "action": action})
        pf_instance.rewrite_private_list(path, entries)

    def _record_child(self, spec, process):
        """pf_runner.ProcessRunner.spawn_callback (section 4.3): one children.json entry per effect-carrying child of
        a journaled effect. A failed write is reported and never stops the child."""
        effect_id = self._current_effect
        if effect_id is None or self.operation_dir is None or self.plan is None:
            return
        try:
            path = self.operation_dir / "children.json"
            entries = pf_instance.read_private_list(path)
            entries.append({"effect_id": effect_id, "tool": spec.tool, "pgid": process.pid,
                            "boot_id": pf_instance.boot_id(), "start_ticks": pf_instance.process_start_ticks(process.pid),
                            "recorded_at": utc()})
            pf_instance.rewrite_private_list(path, entries)
        except (OSError, pf_instance.ContextError) as exc:
            log(f"note: child-record-failed: {(str(getattr(exc, 'strerror', None) or exc)).splitlines()[0]}; a later "
                "resume probes the daemon and database only.")

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

    def log_operations(self, index=None, *, trusted=True, operation_id=None):
        """Section 3.10: the operations block from protected files only, before any app config, .env, Git or Docker
        access. ``operation_id``: the detail of one operation (status --operation)."""
        index = index if index is not None else self.operation_index()
        if not trusted:
            log("Operations (read from UNVERIFIED private state; see trust findings; shown for recovery orientation "
                "only, not as protected truth):")
        if operation_id is not None:
            return self.log_operation_detail(index, operation_id)
        slug = self.context.slug
        if index.overflow:
            log(f"Operations: index overflow ({index.overflow} entries in operations/; mutating routes are refused, see "
                "SYNOLOGY_ADMIN §16)")
        elif not index.blocking and index.permissions is None and index.legacy is None:
            log("Operations: none open")
        for entry in index.blocking:
            if entry.cls == "invalid":
                log(f"Operations: {entry.operation_id} invalid: {entry.error} [blocking]")
                log(f"  next: review it with 'pf --instance {slug} status --operation {entry.operation_id}' (SYNOLOGY_ADMIN "
                    "§16); every mutating route is refused")
                continue
            self._log_operation_summary(index, entry)
        if index.permissions is not None:
            journal = index.permissions
            log(f"Operations: permissions apply {journal.get('operation_id')} phase {journal.get('phase')} [blocking]")
            log(f"  next: pf --instance {slug} permissions apply --resume | --abandon")
        if index.legacy is not None:
            log(f"Operations: state/pending.json {index.legacy.get('operation')}/{index.legacy.get('phase')} [blocking] "
                "(journal-format-unsupported; see SYNOLOGY_ADMIN §16)")
        if index.recent:
            log("Recent operations: " + "; ".join(
                f"{item.operation_id} {item.kind} {item.journal['phase']} {item.journal['updated_at']}"
                for item in index.recent))
        self.log_generations(index)
        self.log_integrated(index)
        records = self.runner_records(index)
        if records:
            open_ops = sorted({record["operation_id"] for record, state, _ in records if state == "open"})
            no_journal = sorted({record["operation_id"] for record, state, _ in records if state == "open"
                                 and (index.entry(record["operation_id"]) is None
                                      or index.entry(record["operation_id"]).cls == "no-journal")})
            reconciled = sum(1 for _, state, _ in records if state == "reconciled")
            opened = sum(1 for _, state, _ in records if state == "open")
            acknowledged = sum(1 for _, state, _ in records if state == "acknowledged")
            log(f"Runner effects: {opened} open" + (f" (no journal: {', '.join(no_journal)})" if no_journal else "")
                + (f" ({', '.join(item for item in open_ops if item not in no_journal)})"
                   if [item for item in open_ops if item not in no_journal] else "")
                + f"; {reconciled} reconciled by journals; {acknowledged} acknowledged")
        return index

    def log_integrated(self, index):
        """PF-A3.3 (section 3.14), from protected files only: the derived lifecycle line, recovery targets, kept
        isolated topologies and sealed workspace generations (each only when present)."""
        slug = self.context.slug
        purged = purged_by(index)
        if purged is not None:
            bundle = next((item for item in purged.journal["retained_artifacts"] if item["kind"] == "purge-bundle"),
                          None)
            level = "unknown"
            if bundle is not None and bundle["sha256"]:
                level = LEVEL_NAMES.get(self.verification_level(bundle["name"], bundle["sha256"], quiet=True)[0], "?")
            name = bundle["name"] if bundle else "?"
            log(f"Lifecycle: purged by instance purge {purged.operation_id} ({purged.journal['updated_at']}); recovery "
                f"bundle {name} [{level}]; next: pf --instance {slug} restore-instance {name} | pf --instance {slug} "
                "deploy")
        targets, kept = [], []
        for entry in index.entries:
            if entry.plan is None or entry.journal is None:
                continue
            directory = self.context.operations_dir / entry.operation_id / "isolated"
            for project, value in self.operation_topologies(entry.plan):
                try:
                    record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                        directory / project / "topology.json"), label="topology.json")
                except (OSError, pf_instance.ContextError):
                    record = None
                state = record.get("state") if isinstance(record, dict) else None
                if state in (None, "removed"):
                    continue
                if entry.kind == "restore-side-by-side":
                    bundle = (entry.plan["input_bundle"] or {}).get("bundle_id")
                    shown = state if state in ("running", "stopped") else "unknown"
                    targets.append(f"{project} from bundle {bundle} (operation {entry.operation_id}, uuid "
                                   f"{value[:8]}) {shown}")
                elif any(item["kind"] == "isolated-topology" and item["name"] == project
                         for item in entry.journal["retained_artifacts"]):
                    reason = (entry.journal["last_error"] or {}).get("code") or "kept"
                    kept.append(f"{project} (operation {entry.operation_id}, {reason})")
        if targets:
            log("Recovery targets: " + "; ".join(targets))
        if kept:
            log("Isolated topologies kept: " + "; ".join(kept))
        sealed = self.backups_root / "generations" / self.context.compose_project
        try:
            count = len([name for name in os.listdir(str(sealed))
                         if re.fullmatch(pf_config.GENERATION_PATTERN[1:-1], name)]) if real_directory(sealed) else 0
        except OSError:
            count = 0
        if count:
            log(f"Sealed workspace generations: {count} in {sealed}")

    def _log_operation_summary(self, index, entry):
        plan, journal = entry.plan, entry.journal
        slug = self.context.slug
        state = "needs operator" if journal["phase"] == "needs_operator" else "blocking"
        log(f"Operations: {entry.operation_id} {entry.kind} phase {journal['phase']} sequence {journal['sequence']} "
            f"updated {journal['updated_at']} [{state}]")
        if journal["last_error"] is not None:
            log(f"  last error: {journal['last_error']['code']}: {journal['last_error']['message']}")
        unresolved = journal["unresolved_effect"]
        if unresolved is not None:
            effect = next(item for item in plan["effects"] if item["effect_id"] == unresolved)
            log(f"  unresolved effect: {unresolved} {effect['type']} {effect['target']} "
                f"({pf_config.effect_state(journal, unresolved)})")
        if pf_config.in_workspace_switch(plan, journal):
            log("  workspace: the registered workspace path may be absent between the two renames of the workspace "
                "switch; only 'resume' (or 'resume --keep-workspace') may continue")
        if journal["retained_artifacts"]:
            log("  retained: " + ", ".join(f"{item['kind']} {item['name']}" for item in journal["retained_artifacts"]))
        routes = pf_config.operation_routes(plan, journal, slug=slug)
        log(f"  next (recorded at sequence {journal['sequence']}): " + (
            "; ".join(f"{command}: {description}" for _, command, description in routes) or
            f"none automatic; review it with 'pf --instance {slug} status --operation {entry.operation_id}'"))
        if index.pair is not None and index.pair[1].operation_id == entry.operation_id:
            backup = index.pair[0].operation_id
            log(f"  waits: backup operation {backup} is open; run 'pf --instance {slug} resume --operation {backup}' "
                "(or add --abandon) first, then this operation's routes apply")
        if plan["supersedes"]:
            superseded = index.entry(plan["supersedes"])
            log(f"  superseded: {plan['supersedes']} ({superseded.kind if superseded else 'unknown'}) by this "
                "operation")

    def log_operation_detail(self, index, operation_id):
        """``status --operation``: the plan summary, every effect, the attempts and the children count (no frozen
        value, no config.env byte, no hash of a secret)."""
        entry = index.entry(operation_id)
        if entry is None:
            raise Failure(f"operation-not-found: no operation {operation_id} exists for instance {self.context.slug}. "
                          "Nothing was changed.")
        directory = self.context.operations_dir / operation_id
        if entry.cls == "invalid":
            log(f"Operation {operation_id}: invalid ({entry.error}); every mutating route is refused (SYNOLOGY_ADMIN §16)")
        elif entry.cls == "no-journal":
            log(f"Operation {operation_id}: no journal (an operation without a lifecycle journal; never blocking)")
        else:
            plan, journal = entry.plan, entry.journal
            confirmation = plan["confirmation"]["phrase"] if plan["confirmation"] else "none"
            log(f"Operation {operation_id}: {plan['kind']} created {plan['created_at']} | class {entry.cls}"
                + (f" (superseded by {entry.superseded_by})" if entry.superseded_by else "")
                + f" | confirmation {confirmation} | workspace {plan['workspace']['mode']}"
                + (f" ({plan['workspace']['reason']})" if plan["workspace"]["reason"] else "")
                + f" | supersedes {plan['supersedes'] or 'none'}"
                + f" | input bundle {plan['input_bundle']['bundle_id'] if plan['input_bundle'] else 'none'}")
            log(f"  journal: phase {journal['phase']} sequence {journal['sequence']} updated {journal['updated_at']}"
                + (f" | result {journal['result']['outcome']}" if journal["result"] else ""))
            if journal["last_error"] is not None:
                log(f"  last error: {journal['last_error']['code']}: {journal['last_error']['message']}")
            states = {item["effect_id"]: item for item in journal["effects"]}
            for effect in plan["effects"]:
                item = states[effect["effect_id"]]
                log(f"  {effect['effect_id']} {effect['phase']} {effect['type']} {effect['target']} {item['state']} "
                    f"{item['observed_at'] or '-'} {item['evidence'] or '-'}")
            if journal["retained_artifacts"]:
                log("  retained: " + ", ".join(f"{item['kind']} {item['name']}"
                                                for item in journal["retained_artifacts"]))
            routes = pf_config.operation_routes(plan, journal, slug=self.context.slug)
            if routes:
                log("  next: " + "; ".join(f"{command}: {description}" for _, command, description in routes))
            self.log_operation_evidence(directory, plan)
        try:
            attempts = pf_instance.read_private_list(directory / "attempts.json")
        except (OSError, pf_instance.ContextError) as exc:
            attempts = []
            log(f"  attempts: unreadable ({exc})")
        for item in attempts:
            if isinstance(item, dict):
                log(f"  attempt {item.get('attempt')}: {item.get('action')} by {item.get('route')} pid {item.get('pid')} "
                    f"at {item.get('started_at')} (release {item.get('release_id')})")
        try:
            children = pf_instance.read_private_list(directory / "children.json")
            log(f"  children: {len(children)} recorded process group(s)")
        except (OSError, pf_instance.ContextError) as exc:
            log(f"  children: unreadable ({exc})")
        for record, state, sequence in self.runner_records(index):
            if record["operation_id"] != operation_id:
                continue
            suffix = f" (reconciled by journal sequence {sequence})" if state == "reconciled" else " (open)"
            log(f"  runner record: {record.get('recorded_at')} {operation_id}: {record.get('tool')} "
                f"{self._record_summary(record)} -> {record.get('outcome')}{suffix}")
        return index

    def log_operation_evidence(self, directory, plan):
        """PF-A3.3 (section 3.14) ``status --operation`` additions: the confirmation summary (first three lines), the
        capacity decisions, the application-invariant outcomes and the topology records (identities only)."""
        try:
            text = pf_instance.read_bytes_nofollow(directory / "confirmation-summary.txt").decode("utf-8", "replace")
            for line in text.splitlines()[:3]:
                log("  summary: " + line)
        except OSError:
            pass
        try:
            decisions = pf_instance.read_private_list(directory / "capacity.json")
        except (OSError, pf_instance.ContextError):
            decisions = []
        mib = 1024 * 1024
        for item in decisions:
            if isinstance(item, dict):
                log(f"  capacity: {item.get('phase')} device {item.get('device')} ({', '.join(item.get('roles') or [])})"
                    f" need {int(item.get('need_bytes') or 0) // mib} MiB free {int(item.get('free_bytes') or 0) // mib}"
                    f" MiB floor {int(item.get('floor_bytes') or 0) // mib} MiB {item.get('result')}")
        for label in ("source", "restored", "activated"):
            try:
                record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                    directory / f"app-check-{label}.json"), label="app-check")
            except (OSError, pf_instance.ContextError):
                continue
            if isinstance(record, dict):
                log(f"  app invariants {label}: {record.get('capability')} {record.get('outcome')} "
                    f"{record.get('summary') or '-'}")
        for project, value in self.operation_topologies(plan):
            try:
                record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                    directory / "isolated" / project / "topology.json"), label="topology.json")
            except (OSError, pf_instance.ContextError):
                log(f"  topology {project} uuid {value[:8]} not created")
                continue
            if isinstance(record, dict):
                teardowns = record.get("teardowns") or []
                log(f"  topology {project} uuid {value[:8]} {record.get('state')}; teardowns {len(teardowns)} "
                    + (",".join(str(item.get("state")) for item in teardowns) or "-"))

    def generation_listing(self):
        """(container, [generation names], [stage names]) of the workspace generation container (no-follow, read-only);
        an absent or unreadable container lists nothing."""
        container = pf_instance.generation_container(self.context)
        try:
            fd = os.open(str(container), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return container, [], []
        try:
            names = sorted(os.listdir(fd))
        finally:
            os.close(fd)
        pattern = re.compile(pf_config.GENERATION_PATTERN[1:-1])
        generations = [name for name in names if pattern.fullmatch(name)]
        stages = [name for name in names if name.startswith("stage-") and pattern.fullmatch(name[len("stage-"):])]
        return container, generations, stages

    def log_generations(self, index):
        """Section 3.7 Tracking: retained (unsealed) generations and stages, cross-referenced with the journals."""
        container, generations, stages = self.generation_listing()
        owners = {}
        for entry in index.entries:
            if entry.plan is None:
                continue
            generation = entry.plan["workspace"]["generation_id"]
            if generation:
                owners[generation] = entry
        if generations:
            latest = generations[-1]
            owner = owners.get(latest)
            referenced = owner is not None and owner.journal is not None and any(
                item["kind"] == "workspace-generation" and item["name"] == latest
                for item in owner.journal["retained_artifacts"])
            log(f"Workspace generations: {len(generations)} unsealed in {container} (latest {latest} "
                + (f"from operation {owner.operation_id})" if referenced else "unreferenced)"))
        if stages:
            parts = []
            for name in stages:
                owner = owners.get(name[len("stage-"):])
                if owner is not None and owner.cls == "blocking":
                    parts.append(f"{name} in progress ({owner.operation_id})")
                else:
                    parts.append(f"{name} superseded-stage ({owner.operation_id if owner else 'unreferenced'})")
            log("  stages: " + "; ".join(parts))

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

    def require_trusted_context(self, *, load_config=True, route=None, index=None, operation=None):
        """Refuse privileged mutation unless the protected context and app configuration validated cleanly.

        Runs before any lock, journal write, transport call or filesystem effect. PF-A2.2: ``load_config=False``
        (the ``config`` route only) skips the final configuration load so the wizard can reach an absent, refused
        or mismatched pf-config.json; the protected-context refusal always runs. PF-A3.2: ``route``/``index`` (a
        read-only, pre-lock operation_index) select the journal-proven workspace-switch exception of section 3.7a
        and the frozen admin configuration of an operation this route may re-enter (section 3.8).
        """
        validation = self.ensure_validation()
        if not validation.mutation_allowed:
            entry = self.workspace_interval_exception(route, validation, index, operation=operation)
            if entry is None:
                raise Failure(
                    "Protected context validation refused mutation:\n" + "\n".join(validation.blocking_messages())
                )
            self._interval_entry = entry.operation_id
            log(f"note: workspace-switch-in-progress {entry.operation_id}: the workspace path is absent between the two "
                "renames; only resume may continue.")
        # A rejected editable configuration blocks the mutation here, not after effects started.
        if load_config:
            if index is not None and route is not None:
                self._config_entry = self.config_source(route, index, operation=operation)
                self._config_selected = True
            self.ensure_config()

    def workspace_interval_exception(self, route, validation, index, *, operation=None):
        """Section 3.7a: the blocking operation that proves an absent registered workspace is the gap between the two
        renames of its workspace switch, or None. Only ``resume``; the only refuse finding must be the missing
        workspace; the journal must place the operation inside its interval and the retained generation must be
        the old workspace inode recorded at W2."""
        if route != "resume" or index is None:
            return None
        refused = [finding for finding in validation.findings if finding.severity == "refuse"]
        workspace = str(self.context.paths.workspace)
        # The absent path is reported twice (the registered path and its last path component); both must name the
        # workspace and its absence, and nothing else may be refused.
        if not any(finding.code == "registered-path-missing" for finding in refused) or any(
                str(finding.path) != workspace or not (
                    finding.code == "registered-path-missing"
                    or (finding.code == "path-component" and "No such file or directory" in finding.message))
                for finding in refused):
            return None
        blocking = [entry for entry in index.blocking if entry.cls == "blocking"]
        if len(blocking) != 1 or index.permissions is not None or index.legacy is not None or index.overflow \
                or len(index.blocking) != 1:
            return None
        entry = blocking[0]
        if operation is not None and operation != entry.operation_id:
            return None
        if entry.plan["workspace"]["mode"] not in ("switch", "pending") \
                or not pf_config.in_workspace_switch(entry.plan, entry.journal):
            return None
        ids = pf_config.workspace_effect_ids(entry.plan)
        # The old identity is recorded by W1 (intent and completion) and repeated by W2 when W2 completed.
        evidence = {}
        for item in entry.journal["effects"]:
            if item["effect_id"] in ids:
                evidence.update(self.identities(item["evidence"]))
        old = evidence.get("old")
        container = pf_instance.generation_container(self.context)
        try:
            fd = os.open(str(container), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            observed = pf_instance.identity_at(fd, entry.plan["workspace"]["generation_id"])
        finally:
            os.close(fd)
        return entry if old is not None and observed == old else None

    @staticmethod
    def identities(evidence):
        """{"old": (dev, ino), "new": (dev, ino)} parsed from W-effect evidence ``old:<dev>:<ino> new:<dev>:<ino>``."""
        found = {}
        for word in str(evidence or "").split():
            match = re.fullmatch(r"(old|new):([0-9]+):([0-9]+)", word)
            if match:
                found[match.group(1)] = (int(match.group(2)), int(match.group(3)))
        return found

    def config_source(self, route, index, *, operation=None):
        """Section 3.8: the operation whose frozen admin configuration this route reads (``resume`` and the
        alias-capable routes next to exactly one blocking operation of their kind), else None (the editable file)."""
        blocking = [entry for entry in index.blocking if entry.cls == "blocking"]
        if index.overflow or index.legacy is not None or index.permissions is not None:
            return None
        if route == "resume":
            if operation is not None:
                chosen = [entry for entry in blocking if entry.operation_id == operation]
            else:
                chosen = blocking if len(blocking) == 1 else []
        elif route in ALIAS_ROUTES:
            chosen = [entry for entry in blocking if entry.kind in ALIAS_ROUTES[route]]
            chosen = chosen if len(chosen) == 1 and len(blocking) == 1 else []
        else:
            return None
        if len(chosen) != 1 or chosen[0].plan.get("admin_config") is None:
            return None
        return chosen[0]

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

    # ------------------------------------------------------------ permission policy (PF-A2.3)

    def scope_root(self, scope):
        """The root of one permission scope: registered paths, the bound control release and private state."""
        paths = self.context.paths
        return {"workspace": paths.workspace, "configuration": paths.configuration,
                "control": self.context.control.path, "backups": paths.backups, "recovery": paths.recovery,
                "private_state": paths.private_state}[scope]

    @property
    def permission_record_path(self):
        return self.context.paths.private_state / PERMISSION_RECORD_NAME

    def _hook(self, name):
        """Test seam (section 3.9): None in production and never settable from the CLI."""
        if self.permission_hook is not None:
            self.permission_hook(name)

    def approval_invalid(self, reason):
        return Failure(f"permission-approval-invalid: The approved permission policy record {self.permission_record_path} "
                       f"cannot be used ({reason}); it is not replaced automatically and no derived policy is used. See "
                       "SYNOLOGY_ADMIN §16.")

    def read_permission_record(self):
        """The approved permission policy record (section 3.3 step 1): None when absent, else (record, bytes).
        Anything else is permission-approval-invalid; there is never a fallback to the derived policy."""
        path = self.permission_record_path
        try:
            info = os.lstat(str(path))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise self.approval_invalid(exc.strerror or str(exc)) from exc
        if not stat.S_ISREG(info.st_mode):
            raise self.approval_invalid("not a regular file")
        if info.st_uid != pf_instance.TRUSTED_UID:
            raise self.approval_invalid(f"owner uid {info.st_uid}; the trusted owner is uid 0")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise self.approval_invalid(f"mode {stat.S_IMODE(info.st_mode):04o} grants group or other access")
        if info.st_nlink != 1:
            raise self.approval_invalid(f"{info.st_nlink} hard links")
        try:
            data = pf_instance.read_bytes_nofollow(path)
            record = pf_instance.parse_strict_json(data, label=str(path))
        except (OSError, pf_instance.ContextError) as exc:
            raise self.approval_invalid(str(exc)) from exc
        defs = pf_config.PERMISSION_APPROVAL_SCHEMA["$defs"]
        errors = pf_install.validate_marked(record, defs["record"], defs=defs)
        if not errors:
            errors = pf_config.permission_approval_problems(record, instance_id=self.context.instance_id)
        if errors:
            raise self.approval_invalid(errors[0])
        return record, data

    def admin_values_for_permissions(self):
        """(pf-config.json values, None) or (None, problem): the permission verbs read it only for
        workspace_write_group and the proposals (section 4.2); never raises."""
        try:
            return self.ensure_config(), None
        except Failure as exc:
            return None, str(exc).splitlines()[0]

    def group_missing(self, scope, group):
        return Failure(f"permission-group-missing: Group {group} for {scope} does not exist on this host; groups are "
                       f"never created. Choose an existing group in '{self.pf_command()} permissions apply'. Nothing "
                       "was changed.")

    def root_group(self, scope):
        """The group name of the gid on a protected storage root (only root can have set it, section 3.3)."""
        root = self.scope_root(scope)
        try:
            gid = os.lstat(str(root)).st_gid
        except OSError as exc:
            raise Failure(f"{root}: {exc.strerror or exc}") from exc
        try:
            return grp.getgrgid(gid).gr_name
        except KeyError as exc:
            raise Failure(f"permission-group-missing: Group gid {gid} on {root} has no group name for {scope}; groups "
                          f"are never created. Choose an existing group in '{self.pf_command()} permissions apply'. "
                          "Nothing was changed.") from exc

    def permission_policy(self, admin=None, unsafe=None):
        """The effective permission policy (section 3.3): the approved record, else the derived policy.

        ``unsafe`` (check/plan only, section 3.5) is the scope-attributable finding map: the derived group of a
        backups/recovery scope listed there is not read from its untrusted root, and a root that cannot be read is
        added to it as that scope's blocker instead of failing the whole report."""
        found = self.read_permission_record()
        if found is not None:
            record, data = found
            return EffectivePolicy(record["policy"], record["revision"], "approved", pf_instance.sha256_bytes(data),
                                   record, data)
        values, problem = admin if admin is not None else self.admin_values_for_permissions()
        if values is None:
            raise Failure(f"permission-policy-invalid: The permission policy is invalid: workspace_write_group is "
                          f"unavailable ({problem}).")
        groups, unavailable = {}, set()
        for scope in ("backups", "recovery"):
            if unsafe is not None and scope in unsafe:
                unavailable.add(scope)
                continue
            try:
                groups[scope] = self.root_group(scope)
            except Failure as exc:
                if unsafe is None or not isinstance(exc.__cause__, OSError):
                    raise
                unsafe[scope] = [f"{exc}; the derived group of {scope} cannot be read from it"]
                unavailable.add(scope)
        # A blocked scope's placeholder is the editable group (already required to resolve); it is never shown.
        for scope in unavailable:
            groups[scope] = values["workspace_write_group"]
        policy = pf_config.derive_permission_policy(values, backups_group=groups["backups"],
                                                    recovery_group=groups["recovery"])
        problems = pf_config.permission_policy_problems(policy)
        if problems:
            raise Failure(f"permission-policy-invalid: The permission policy is invalid: {problems[0]}.")
        return EffectivePolicy(policy, 0, "derived", None, None, None, frozenset(unavailable))

    def resolve_policy_gids(self, policy):
        """{scope: gid or None}; a missing group is permission-group-missing (groups are never created)."""
        gids = {}
        for scope in PERMISSION_SCOPES:
            group = policy["permissions"][scope].get("group")
            if group is None:
                gids[scope] = None
                continue
            try:
                gids[scope] = grp.getgrnam(group).gr_gid
            except KeyError as exc:
                raise self.group_missing(scope, group) from exc
        return gids

    def permission_targets(self):
        """(EffectivePolicy, {scope: ScopeTarget}, {scope: gid}) of the policy in force, read once per operation."""
        if self._permission_targets is None:
            effective = self.permission_policy()
            compiled = pf_config.compile_permission_policy(effective.policy)
            self._permission_targets = (effective, compiled, self.resolve_policy_gids(effective.policy))
        return self._permission_targets

    def workspace_executables(self):
        """Designated workspace executables from the protected source manifest (section 3.4); None without one."""
        manifest = self.load_source_manifest()
        if manifest is None:
            return None
        return frozenset(entry["path"] for entry in manifest["entries"]
                         if entry["kind"] == "file" and entry.get("executable"))

    def permission_findings(self):
        """Section 3.5: (context-level refuse findings, {scope: [rendered scope-attributable findings]})."""
        roots = {scope: self.scope_root(scope) for scope in ("workspace", "configuration", "backups", "recovery")}
        # A finding on (or above) the installation root, private state or the control release also invalidates the
        # approval record and the source manifest, even when it is an ancestor shared with a data root as well.
        protected = (Path(self.context.installation_root), self.scope_root("private_state"),
                     self.scope_root("control"))
        context, unsafe = [], {}
        for finding in self.ensure_validation().findings:
            if finding.severity != "refuse":
                continue
            path = Path(finding.path)
            hit = [scope for scope, root in roots.items() if path == root or path in root.parents or root in path.parents]
            for scope in hit:
                unsafe.setdefault(scope, []).append(finding.render())
            if not hit or any(path == item or path in item.parents for item in protected):
                context.append(finding)
        return context, unsafe

    def _scope_plan(self, scope, target, gid, *, unsafe=None, fenced=False):
        """Inventory and differences of one scope (section 3.5); read-only."""
        plan = ScopePlan(scope, self.scope_root(scope), target, gid)
        if unsafe:
            plan.blockers = [("scope-path-unsafe", "", text) for text in unsafe]
            return plan
        inventory = pf_instance.inventory_scope(scope, plan.root, owner_rule=target.owner_rule,
                                                limit=pf_source.MANIFEST_ENTRY_LIMIT)
        plan.inventory = inventory
        plan.blockers = list(inventory.blockers)
        if scope == "workspace":
            designated = self.workspace_executables()
            if designated is None:
                plan.notes.append("workspace-no-source-manifest: no protected source manifest exists, so no workspace "
                                  "file is designated executable")
            plan.executables = designated or frozenset()
        if scope == "control":
            for entry in inventory.entries:
                for text in pf_config.ceiling_violations(target, mode=entry.mode, uid=entry.uid, gid=entry.gid,
                                                         kind=entry.type, policy_gid=gid):
                    plan.violations.append((entry.relative, text))
            plan.differs = len({relative for relative, _ in plan.violations})
            return plan
        root_entry = None
        for entry in inventory.entries:
            if entry.relative == "":
                root_entry = entry
            mode, new_gid = permission_entry_target(target, gid, entry, plan.executables)
            if (entry.mode, entry.gid) == (mode, new_gid):
                continue
            plan.differs += 1
            plan.categories["group"] += entry.gid != new_gid
            plan.categories["mode"] += entry.mode != mode
            plan.categories["special-bits"] += bool(entry.mode & (0o7000 if entry.type == "file" else 0o5000))
            plan.changes.append([scope, entry.relative, entry.dev, entry.ino, entry.type, entry.mode, entry.gid, mode,
                                 new_gid])
        if scope in EDITABLE_SCOPES and not plan.blockers and (fenced or any(item[1] for item in plan.changes)):
            reason = None
            if not pf_instance.proc_scan_available():
                reason = f"{pf_instance.PROC_ROOT}/self/fd is not a readable directory, so open handles cannot be scanned"
            elif "" in inventory.default_acl_dirs:
                reason = "the scope root carries an ACL, which could grant traversal regardless of its mode"
            elif root_entry is None or root_entry.uid != pf_instance.TRUSTED_UID:
                reason = "the scope root is not owned by uid 0"
            if reason is not None:
                plan.freeze = "unavailable"
                plan.blockers.append(("editor-freeze-unavailable", "", reason))
            else:
                plan.freeze = "needed"
                if not any(item[1] == "" for item in plan.changes):
                    # The fence is lifted by an explicit root operation, even when the root already complies.
                    mode, new_gid = permission_entry_target(target, gid, root_entry, plan.executables)
                    plan.changes.append([scope, "", root_entry.dev, root_entry.ino, "dir", root_entry.mode,
                                         root_entry.gid, mode, new_gid])
        plan.changes.sort(key=lambda item: item[1])
        return plan

    def _permission_plan(self, policy, scopes, *, unsafe=None, fenced=frozenset(), unavailable=frozenset()):
        """The plan of ``policy`` over ``scopes`` (section 3.5): per-scope plans, the change list bytes and hash."""
        try:
            compiled = pf_config.compile_permission_policy(policy)
        except pf_config.ConfigError as exc:
            raise Failure(f"permission-policy-invalid: The permission policy is invalid: {str(exc).split(': ', 1)[-1]}.") \
                from exc
        gids = self.resolve_policy_gids(policy)
        plans = {scope: self._scope_plan(scope, compiled[scope], gids[scope], unsafe=(unsafe or {}).get(scope),
                                         fenced=scope in fenced) for scope in scopes}
        for scope in scopes:
            plans[scope].group_unavailable = scope in unavailable
        changes = [change for scope in scopes for change in plans[scope].changes]
        policy_sha256 = pf_instance.sha256_bytes(pf_instance.normalize_json(policy))
        header = {"schema_version": 1, "policy_sha256": policy_sha256, "scopes": list(scopes),
                  "change_count": len(changes)}
        try:
            data = pf_config.changes_bytes(header, changes)
        except pf_config.ConfigError as exc:
            raise Failure(f"Internal error: {exc}; nothing was changed.") from exc
        return PermissionPlan(policy, policy_sha256, tuple(scopes), plans, changes, data,
                              pf_instance.sha256_bytes(data), gids)

    def permission_proposals(self, effective, admin):
        """[(scope, proposed group, group in force)] where pf-config.json proposes another group (section 3.3)."""
        if admin is None:
            return []
        rows = []
        scopes = ("backups", "recovery") + (EDITABLE_SCOPES if effective.kind == "approved" else ())
        for scope in PERMISSION_SCOPES:
            if scope not in scopes or scope in effective.unavailable:
                continue
            proposed = admin["backup_read_group" if scope in ("backups", "recovery") else "workspace_write_group"]
            current = effective.policy["permissions"][scope]["group"]
            if proposed != current:
                rows.append((scope, proposed, current))
        return rows

    def log_permission_notes(self, effective, proposals):
        if effective.kind == "derived":
            log(f"permission-policy-unapproved: Permission policy not approved yet: backups/recovery groups come from "
                f"their folders, workspace/configuration groups from pf-config.json; '{self.pf_command()} permissions "
                "apply' approves a permission policy.")
        in_force = f"permission policy revision {effective.revision}" if effective.kind == "approved" \
            else "the unapproved derived policy"
        for scope, proposed, current in proposals:
            log(f"permission-group-proposal: pf-config.json proposes group {proposed} for {scope}; {in_force} uses "
                f"{current} until '{self.pf_command()} permissions apply' approves the change.")

    def _log_scope_block(self, plan, *, details):
        target = plan.target
        log(f"{plan.scope} — {plan.root}")
        if plan.scope == "private_state":
            log("  Policy: owner only (directories 0700, files 0600; no group)")
        elif plan.group_unavailable:
            log("  Policy: group unavailable (the derived group comes from the folder, which is unsafe or unreadable; "
                f"see its blockers) | {pf_config.ACCESS_LABELS[target.access]}")
        else:
            parts = [f"group {target.group} (gid {plan.gid})", pf_config.ACCESS_LABELS[target.access]]
            if plan.scope in EDITABLE_SCOPES:
                parts.append("inheritance " + ("yes" if target.inherit else "no"))
            if plan.scope == "workspace":
                parts.append("scripts " + pf_config.EXECUTABLE_LABELS[target.executables])
            if plan.scope == "control":
                parts.append("scripts Owner only; installed by 'pf install', checked, not changed")
            log("  Policy: " + " | ".join(parts))
            try:
                members = list(grp.getgrnam(target.group).gr_mem)
            except KeyError:
                members = []
            shown = ", ".join(members[:GROUP_MEMBER_LIMIT]) or "(none listed)"
            log(f"  Members of {target.group}: {shown}{' …' if len(members) > GROUP_MEMBER_LIMIT else ''} (at most "
                f"{GROUP_MEMBER_LIMIT}; primary-group and directory-service members are not listed)")
            if plan.scope in ("backups", "recovery"):
                log("  " + BACKUP_GROUP_CONSEQUENCE)
        if plan.scope == "control":
            log(f"  Changes: control: ceiling violations {len(plan.violations)} (of {plan.total} entries)")
        else:
            counts = plan.categories
            log(f"  Changes: group {counts['group']}, mode {counts['mode']}, special-bits {counts['special-bits']} "
                f"(total {len(plan.changes)} of {plan.total} entries)")
        unavailable = [message for code, _, message in plan.blockers if code == "editor-freeze-unavailable"]
        log("  Freeze: " + ("needed (bulk change below the root)" if plan.freeze == "needed"
                            else f"unavailable ({unavailable[0]})" if unavailable else "not needed"))
        if plan.blockers:
            log("  Blockers:")
            by_code = {}
            for code, relative, message in plan.blockers:
                by_code.setdefault(code, []).append((relative, message))
            for code, items in by_code.items():
                for relative, message in items[:PERMISSION_BLOCKER_LIMIT]:
                    if code == "editor-freeze-unavailable":
                        log(f"    {code}: Bulk change of {plan.scope} needs a verified editor freeze, which is unavailable "
                            f"here ({message}). Deselect it with --scope or follow SYNOLOGY_ADMIN §2.")
                    else:
                        log(f"    {code}: {plan.scope}: {relative or '.'}: {message}")
                if len(items) > PERMISSION_BLOCKER_LIMIT:
                    log(f"    … and {len(items) - PERMISSION_BLOCKER_LIMIT} more")
        for relative, violation in plan.violations[:PERMISSION_BLOCKER_LIMIT]:
            log(f"    control-ceiling: control: {relative or '.'}: {violation}; the installed control is changed only "
                "by 'pf install control'.")
        if len(plan.violations) > PERMISSION_BLOCKER_LIMIT:
            log(f"    … and {len(plan.violations) - PERMISSION_BLOCKER_LIMIT} more")
        for note in plan.notes:
            log("  Note: " + note)
        if details:
            for _, relative, _, _, kind, before_mode, before_gid, after_mode, after_gid in \
                    plan.changes[:PERMISSION_DETAIL_LIMIT]:
                log(f"    {relative or '.'}: {before_mode:04o} {pf_config.symbolic_mode(before_mode, kind)} gid "
                    f"{before_gid} -> {after_mode:04o} {pf_config.symbolic_mode(after_mode, kind)} gid {after_gid}")
            if len(plan.changes) > PERMISSION_DETAIL_LIMIT:
                log(f"    … and {len(plan.changes) - PERMISSION_DETAIL_LIMIT} more")

    def log_permission_plan(self, plan, *, details):
        for scope in plan.scopes:
            self._log_scope_block(plan.scope_plans[scope], details=details)
        log(f"Plan hash: {plan.plan_sha256[:12]} ({len(plan.changes)} changes)")

    @staticmethod
    def plan_mode_applied(plan):
        if plan.scope == "control":
            return "check-only" + (f" ({plan.differs} ceiling violation(s))" if plan.differs else "")
        if plan.inventory is None or (plan.total and plan.differs >= plan.total):
            return "no"
        return "yes" if not plan.differs else f"partial ({plan.differs} differ)"

    def log_permission_status(self, rows):
        """Section 4.8: three statuses per scope; never one green line. ``rows``: (scope, mode_applied, ScopePlan)."""
        for scope, mode_applied, plan in rows:
            log(f"{scope}: mode_applied: {mode_applied}")
            acl = plan is not None and any(code == "scope-entry-acl" for code, _, _ in plan.blockers)
            log("         effective_access_verified: not verified — computed from mode bits"
                + ("; ACL entries present: not computed" if acl else "")
                + "; SMB share permissions and DSM ACLs are not observable by this control (PF-A5.1)")
            text = "         future_file_behavior_verified: not verified — "
            if plan is not None and scope in EDITABLE_SCOPES and plan.target.inherit:
                text += f"setgid on directories carries group {plan.target.group}, not write access; "
            text += "new-file modes depend on the creating client's umask or SMB create mask; "
            defaults = len(plan.inventory.default_acl_dirs) if plan is not None and plan.inventory is not None else 0
            if defaults:
                text += f"default ACL present on {defaults} dirs; "
            log(text + "files created by pf get explicit modes and are verified after creation")

    @staticmethod
    def selected_scopes(args):
        chosen = set(getattr(args, "scopes", None) or PERMISSION_SCOPES)
        return tuple(scope for scope in PERMISSION_SCOPES if scope in chosen)

    def _permissions_report(self, args, *, verb):
        """`pf permissions check|plan` (section 4.9): read-only, no lock, nothing written anywhere."""
        context_findings, unsafe = self.permission_findings()
        if context_findings:
            for finding in self.ensure_validation().findings:
                if finding.severity == "refuse":
                    log("  " + finding.render())
            raise Failure("permissions-context-refused: The protected context is refused (see the findings above), so "
                          "the permission policy cannot be read safely; no scope was inspected.")
        admin, problem = self.admin_values_for_permissions()
        if admin is None:
            log(f"Note: pf-config.json is unavailable ({problem}); its proposals are not shown.")
        effective = self.permission_policy(admin=(admin, problem), unsafe=unsafe)
        scopes = self.selected_scopes(args)
        plan = self._permission_plan(effective.policy, scopes, unsafe=unsafe, unavailable=effective.unavailable)
        title = "Permission plan" if verb == "plan" else "Permission check"
        log(f"{title} for instance {self.context.slug} — {effective.label}")
        journal = self.read_journal()
        if journal is not None and journal.get("operation") == "permissions":
            log(f"an apply is in progress: {journal.get('operation_id')} (counts are a point-in-time observation)")
        proposals = self.permission_proposals(effective, admin)
        self.log_permission_notes(effective, proposals)
        log("")
        log("== Effective policy ==")
        self.log_permission_plan(plan, details=verb == "plan" and bool(getattr(args, "details", False)))
        if verb == "plan" and proposals:
            candidate = json.loads(json.dumps(effective.policy))
            for scope, proposed, _ in proposals:
                candidate["permissions"][scope]["group"] = proposed
            log("")
            log(f"== With pf-config.json proposals == candidate (not approved; '{self.pf_command()} permissions apply' "
                "would ask for it)")
            self.log_permission_plan(self._permission_plan(candidate, scopes, unsafe=unsafe,
                                                           unavailable=effective.unavailable),
                                     details=bool(getattr(args, "details", False)))
        log("")
        self.log_permission_status([(scope, self.plan_mode_applied(plan.scope_plans[scope]), plan.scope_plans[scope])
                                    for scope in scopes])
        blockers = plan.blockers
        if blockers:
            raise Failure(f"permissions-blocked: The plan has {len(blockers)} blocker(s); see above.")
        if verb == "check":
            differ = sum(plan.scope_plans[scope].differs for scope in scopes)
            if differ:
                raise Failure(f"permissions-differ: {differ} {'entry differs' if differ == 1 else 'entries differ'} from "
                              f"{effective.label}; see above.")
            log(f"Permissions match {effective.label}.")
        return 0

    def permissions_check(self, args):
        return self._permissions_report(args, verb="check")

    def permissions_plan(self, args):
        return self._permissions_report(args, verb="plan")

    def _ask_choice(self, stage, question, options, default):
        """One numbered choice; ``options``: [(value, label)]. Returns the chosen value."""
        line = "  ".join(f"{number}. {label}" for number, (_, label) in enumerate(options, 1))
        values = [value for value, _ in options]
        default_number = str(values.index(default) + 1) if default in values else None

        def validate(answer):
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return values[int(answer) - 1]
            raise Failure("Choose one of the listed numbers.")

        return ask_answer(stage, f"{question}: {line}", default=default_number, validate=validate)

    def _ask_policy_group(self, scope, default, proposal):
        listed = list(host_groups()[:GROUP_LIST_LIMIT])
        names = [name for name, _ in listed]
        for extra in (default, proposal):
            if extra and extra not in names and group_exists(extra):
                listed.append((extra, grp.getgrnam(extra).gr_gid))
                names.append(extra)
        rendered = []
        for number, (name, gid) in enumerate(listed, 1):
            mark = " (proposed by pf-config.json)" if name == proposal and proposal != default else ""
            rendered.append(f"{number}. {name} (gid {gid}){mark}")
        log("  Group:  " + ("  ".join(rendered) or "(no group detected)") + "  [or type a name]")

        def validate(answer):
            if answer.isdigit() and 1 <= int(answer) <= len(listed):
                return listed[int(answer) - 1][0]
            if pf_instance._anchored_fullmatch(pf_config._POLICY_GROUP_PATTERN, answer) and group_exists(answer):
                return answer
            raise Failure(f"{answer!r} is not a listed number or an existing group on this host; groups are never "
                          "created.")

        default_number = str(names.index(default) + 1) if default in names else None
        return ask_answer(f"{scope}.group", "  Group: choose a number or type a group name", default=default_number,
                          validate=validate)

    def permission_wizard(self, base, admin):
        """Section 4.6: numbered questions for workspace, configuration, backups and recovery; control and private
        state are shown, not asked. Returns the candidate policy. No octal or symbolic input is ever accepted."""
        candidate = json.loads(json.dumps(base.policy))
        permissions = candidate["permissions"]
        log("Permission policy wizard: answer with a number; Enter keeps the shown default; q cancels (nothing is "
            "changed).")
        try:
            for scope in ("workspace", "configuration", "backups", "recovery"):
                item = permissions[scope]
                root = self.scope_root(scope)
                info = os.lstat(str(root))
                try:
                    now_group = grp.getgrgid(info.st_gid).gr_name
                except KeyError:
                    now_group = f"gid {info.st_gid}"
                log("")
                log(f"{SCOPE_LABELS[scope]} — {root}")
                log(f"  Now: group {now_group} | {access_reading(stat.S_IMODE(info.st_mode))}")
                storage = scope in ("backups", "recovery")
                if storage:
                    log("  " + BACKUP_GROUP_CONSEQUENCE)
                proposal = admin.get("backup_read_group" if storage else "workspace_write_group") if admin else None
                item["group"] = self._ask_policy_group(scope, item["group"], proposal)
                choices = ("read_only", "none") if storage else ("read_write", "read_only", "none")
                item["access"] = self._ask_choice(f"{scope}.access", "  Access",
                                                  [(value, pf_config.ACCESS_LABELS[value]) for value in choices],
                                                  item["access"])
                if not storage:
                    item["inherit_group"] = self._ask_choice(
                        f"{scope}.inherit_group", "  Keep the folder's group on new files and folders?",
                        [(True, "Yes"), (False, "No")], item["inherit_group"])
                if scope == "workspace":
                    log("  Files marked executable in the deployed source keep an owner execute bit so the workspace "
                        "still matches its source manifest.")
                    options = [("owner_only", "Owner only")]
                    if item["access"] != "none":
                        options.append(("owner_and_group", "Owner and group"))
                    current = item["executables"] if item["executables"] in dict(options) else "owner_only"
                    item["executables"] = self._ask_choice(
                        "workspace.executables", "  Script execution for files marked executable in the deployed "
                        "source", options, current)
        except ConfigCancelled as exc:
            raise Failure(PERMISSIONS_CANCELLED) from exc
        permissions["control"] = {"group": permissions["workspace"]["group"], "access": "none",
                                  "executables": "owner_only"}
        permissions["private_state"] = {"access": "owner_only"}
        log("")
        log("Control release: No group access (installed by 'pf install'; checked, not changed)")
        log("Private state: owner only (fixed)")
        return candidate

    def _permission_changed(self, what):
        return Failure(f"permissions-changed-before-apply: {what} changed after the plan was confirmed; run the "
                       "command again to review it. Nothing was changed.")

    def _check_permission_policy(self, candidate):
        problems = pf_config.permission_policy_problems(candidate)
        if problems:
            raise Failure(f"permission-policy-invalid: The permission policy is invalid: {problems[0]}.")
        unsupported = pf_config.permission_policy_unsupported(candidate)
        if unsupported:
            raise Failure(f"permission-policy-unsupported: The permission policy choice {unsupported[0]}. Nothing was "
                          "changed.")

    def _permission_summary(self, title, base, policy, plan):
        lines = [title]
        rows = pf_config.policy_diff(base.policy, policy)
        if rows:
            lines += [f"  {scope}.{field}: {json.dumps(old)} -> {json.dumps(new)}" for scope, field, old, new in rows]
        else:
            lines.append("  no policy change")
        lines.append(f"Plan hash: {plan.plan_sha256[:12]} ({len(plan.changes)} changes)")
        if plan.freeze_scopes:
            lines.append("Editor freeze: " + ", ".join(plan.freeze_scopes) + " (start the command from outside these "
                                                                            "folders, for example 'cd /')")
        return "\n".join(lines)

    def permissions_apply(self, args):
        """`pf permissions apply [--resume|--abandon]` inside the instance lock (sections 3.6, 3.7)."""
        journal = self.read_journal()
        is_open = journal is not None and journal.get("operation") == "permissions"
        if getattr(args, "resume", False) or getattr(args, "abandon", False):
            if not is_open:
                raise Failure("permissions-nothing-pending: No interrupted permission apply is open. Nothing was "
                              "changed.")
            return self._permissions_resume(journal) if args.resume else self._permissions_abandon(journal)
        if is_open:
            raise Failure(f"permissions-apply-pending: An interrupted permission apply {journal.get('operation_id')} is "
                          f"open; run '{self.pf_command()} permissions apply --resume' or '--abandon' first. Nothing "
                          "was changed.")
        admin, problem = self.admin_values_for_permissions()
        base = self.permission_policy(admin=(admin, problem))
        scopes = self.selected_scopes(args)
        log(f"Permission apply for instance {self.context.slug} — {base.label}")
        self.log_permission_notes(base, self.permission_proposals(base, admin))
        candidate = self.permission_wizard(base, admin)
        self._check_permission_policy(candidate)
        plan = self._permission_plan(candidate, scopes)
        log("")
        self.log_permission_plan(plan, details=bool(getattr(args, "details", False)))
        if plan.blockers:
            raise Failure(f"permissions-blocked: The plan has {len(plan.blockers)} blocker(s); see above. Nothing was "
                          "changed.")
        if base.kind == "approved" and candidate == base.policy and not plan.changes:
            log(f"permissions-current: Permissions already match permission policy revision {base.revision}; nothing to "
                "change.")
            return 0
        permission_confirm("APPLY PERMISSIONS " + self.context.slug, self._permission_summary(
            f"Permission policy for instance {self.context.slug}: {base.label} -> candidate", base, candidate, plan))
        self._hook("after-plan")
        # Step 7: revalidate after the confirmation (the SS-2 site: ensure_validation, cache cleared first).
        self.validation = None
        if not self.ensure_validation().mutation_allowed:
            raise self._permission_changed("The protected context")
        current = self.read_permission_record()
        if (current[1] if current is not None else None) != base.record_bytes:
            raise self._permission_changed("The approved permission policy record")
        try:
            gids = self.resolve_policy_gids(candidate)
        except Failure as exc:
            raise self._permission_changed("A group") from exc
        if gids != plan.gids:
            raise self._permission_changed("A group")
        for scope in scopes:
            try:
                identity = pf_instance.path_identity(self.scope_root(scope))
            except pf_instance.ContextError as exc:
                raise self._permission_changed(f"The {scope} folder") from exc
            if identity != plan.scope_plans[scope].inventory.root_identity:
                raise self._permission_changed(f"The {scope} folder")
        # Step 8: persist the intent.
        confirmed = [{"operation_id": self.operation_id, "plan_sha256": plan.plan_sha256}]
        self._write_permission_intent(plan, base)
        write_json(self.pending, {"operation": "permissions", "phase": "applying", "started": utc(),
                                  "operation_id": self.operation_id, "plan_sha256": plan.plan_sha256,
                                  "confirmed_plans": confirmed})
        return self._permission_execute(
            plan, action="apply", apply_op=self.operation_id, apply_dir=self.operation_dir, base_revision=base.revision,
            base_sha256=base.record_sha256, base_policy_sha256=base.record["policy_sha256"] if base.record else None,
            already_approved=False, fenced_now=frozenset(), confirmed_plans=confirmed, start_seq=0)

    def _write_permission_intent(self, plan, base):
        """Step 8: the confirmed change list, then the frozen plan (validated before it is written)."""
        pf_instance._write_private_file(self.operation_dir / PERMISSION_CHANGES_NAME, plan.changes_bytes, 0o600)
        roots = []
        for scope in plan.scopes:
            scope_plan = plan.scope_plans[scope]
            dev, ino = scope_plan.inventory.root_identity
            root_entry = next(entry for entry in scope_plan.entries if entry.relative == "")
            roots.append({"scope": scope, "dev": dev, "ino": ino,
                          "gid": plan.gids[scope] if scope_plan.target.gid_rule == "set" else root_entry.gid,
                          "fenced": scope_plan.freeze == "needed"})
        document = {"schema_version": 1, "operation_id": self.operation_id, "instance_id": self.context.instance_id,
                    "created": utc(), "base_revision": base.revision, "base_sha256": base.record_sha256,
                    "policy_sha256": plan.policy_sha256, "policy": plan.policy, "scopes": list(plan.scopes),
                    "freeze_scopes": plan.freeze_scopes, "roots": roots, "change_count": len(plan.changes),
                    "plan_sha256": plan.plan_sha256}
        self._check_permission_record(document, "plan")
        pf_instance._write_private_file(self.operation_dir / PERMISSION_PLAN_NAME,
                                        pf_instance.normalize_json(document), 0o600)

    @staticmethod
    def _check_permission_record(document, name):
        defs = pf_config.PERMISSION_APPLY_SCHEMA["$defs"]
        errors = pf_install.validate_marked(document, defs[name], defs=defs)
        if name == "effect" and not errors:
            errors = pf_config.effect_problems(document)
        if errors:
            raise Failure(f"Internal error: a permission {name} record failed its schema ({errors[0]}); nothing more "
                          "was written.")

    def _effect_line(self, kind, scope, relative, entry_type, dev, ino, before_mode, after_mode, before_gid,
                     after_gid):
        self._effect_seq += 1
        return {"seq": self._effect_seq, "kind": kind, "scope": scope, "path": relative, "dev": dev, "ino": ino,
                "type": entry_type, "before_mode": before_mode, "after_mode": after_mode, "before_gid": before_gid,
                "after_gid": after_gid, "operation_id": self.operation_id}

    def _append_effects(self, path, lines):
        """Write-ahead: validated, normalized lines appended and fsynced before their operations run."""
        for line in lines:
            self._check_permission_record(line, "effect")
        data = b"".join(pf_instance.normalize_json(line) + b"\n" for line in lines)
        created = not os.path.lexists(str(path))
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        if created:
            pf_instance._fsync_directory(Path(path).parent)
        self._effect_count += len(lines)

    def _journal_invalid(self, path, number):
        return Failure(f"permissions-journal-invalid: The permission effect journal {path} is unreadable at line "
                       f"{number}; review it manually (SYNOLOGY_ADMIN §16). Nothing was changed.")

    def _read_effects(self, path):
        """(effect lines, valid byte length). Every line is newline-terminated normalized JSON valid against
        $defs.effect with strictly increasing seq; a final line without a newline is a torn write (ignored)."""
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except FileNotFoundError:
            return [], 0
        except OSError as exc:
            raise self._journal_invalid(path, 0) from exc
        lines, offset, number, last = [], 0, 0, 0
        defs = pf_config.PERMISSION_APPLY_SCHEMA["$defs"]
        while offset < len(data):
            end = data.find(b"\n", offset)
            number += 1
            if end < 0:
                break
            raw = data[offset:end]
            try:
                line = pf_instance.parse_strict_json(raw, label=str(path))
            except pf_instance.ContextError as exc:
                raise self._journal_invalid(path, number) from exc
            if (pf_instance.normalize_json(line) != raw or pf_install.validate_marked(line, defs["effect"], defs=defs)
                    or pf_config.effect_problems(line) or line["seq"] <= last):
                raise self._journal_invalid(path, number)
            last = line["seq"]
            lines.append(line)
            offset = end + 1
        return lines, offset

    def _mark_interrupted(self):
        """Best effort: pending.json phase ``interrupted``. A failure here leaves ``applying``, which every route
        treats identically (section 3.6), so it is deliberately not raised over the original error."""
        try:
            journal = self.read_journal()
            if journal is not None and journal.get("operation") == "permissions":
                journal["phase"] = "interrupted"
                write_json(self.pending, journal)
        except (OSError, ValueError):
            pass

    def _write_permission_outcome(self, *, apply_op, action, result, plan_sha256, unplanned, conflicts,
                                  approved_revision, statuses):
        scopes = []
        for scope in PERMISSION_SCOPES:
            mode_applied, notes = statuses.get(scope, ("not-selected", []))
            scopes.append({"scope": scope, "mode_applied": mode_applied, "effective_access_verified": "not verified",
                           "future_file_behavior_verified": "not verified", "notes": list(notes)})
        outcome = {"schema_version": 1, "operation_id": self.operation_id, "apply_operation_id": apply_op,
                   "completed": utc(), "action": action, "result": result, "plan_sha256": plan_sha256,
                   "changed": self._changed_count, "unplanned": unplanned, "conflicts": conflicts,
                   "approved_revision": approved_revision, "scopes": scopes}
        self._check_permission_record(outcome, "outcome")
        pf_instance._write_private_file(self.operation_dir / PERMISSION_OUTCOME_NAME,
                                        pf_instance.normalize_json(outcome), 0o600)

    def _restore_fences(self, fences, root_fds, effects_path):
        for scope in reversed(list(fences)):
            original = fences[scope]
            fd = root_fds[scope]
            info = os.fstat(fd)
            self._append_effects(effects_path, [self._effect_line(
                "fence", scope, "", "dir", info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), original,
                info.st_gid, info.st_gid)])
            os.fchmod(fd, original)
        fences.clear()

    def _apply_operations(self, scope, operations, root_fd, directories, effects_path):
        """Section 3.6 step 10 for one scope: batches of write-ahead effect lines, then the engine per entry."""
        between = lambda: self._hook("between-chown-chmod")  # noqa: E731 - the test seam of section 3.9
        for start in range(0, len(operations), pf_instance.EFFECT_BATCH):
            batch = operations[start:start + pf_instance.EFFECT_BATCH]
            self._append_effects(effects_path, [self._effect_line(
                "entry", scope, entry.relative, entry.type, entry.dev, entry.ino, entry.mode, mode, entry.gid, gid)
                for entry, mode, gid in batch])
            parent = (None, None)
            try:
                for entry, mode, gid in batch:
                    self._current_scope = scope
                    if entry.relative == "":
                        pf_instance.apply_entry_target(root_fd, None, entry, mode=mode, gid=gid, between=between)
                    else:
                        folder, _, name = entry.relative.rpartition("/")
                        if parent[0] != folder:
                            if parent[1] is not None:
                                os.close(parent[1])
                            parent = (None, None)
                            fd, name = pf_instance.open_entry_parent(root_fd, entry.relative, directories)
                            parent = (folder, fd)
                        pf_instance.apply_entry_target(parent[1], name, entry, mode=mode, gid=gid, between=between)
                    self._changed_count += 1
                    if self.permission_fault is not None and self._changed_count >= self.permission_fault:
                        raise PermissionFault(f"test fault after {self._changed_count} operation(s)")
            finally:
                if parent[1] is not None:
                    os.close(parent[1])

    def _verify_scope(self, scope_plan, excluded):
        """Step 11: re-inventory; every applied identity must hold its target. Returns (differing paths, notes)."""
        inventory = pf_instance.inventory_scope(scope_plan.scope, scope_plan.root,
                                                owner_rule=scope_plan.target.owner_rule,
                                                limit=pf_source.MANIFEST_ENTRY_LIMIT)
        by_identity = {(entry.dev, entry.ino): entry for entry in inventory.entries}
        applied = {(entry.dev, entry.ino) for entry in scope_plan.entries}
        differing, notes = [], []

        def excluded_path(relative):
            # This journal's own files (its operation directories and pending.json) change while it runs.
            return any(relative == prefix or relative.startswith(prefix + "/") for prefix in excluded)

        for entry in scope_plan.entries:
            now = by_identity.get((entry.dev, entry.ino))
            if now is None:
                if not excluded_path(entry.relative):
                    notes.append(f"permission-entry-gone: {scope_plan.scope}: {entry.relative or '.'} disappeared "
                                 "after the plan")
                continue
            if (now.mode, now.gid) != permission_entry_target(scope_plan.target, scope_plan.gid, now,
                                                              scope_plan.executables):
                differing.append(now.relative or ".")

        for entry in inventory.entries:
            if (entry.dev, entry.ino) not in applied and not excluded_path(entry.relative):
                notes.append(f"permission-entry-unplanned: {scope_plan.scope}: {entry.relative} appeared after the plan; "
                             f"'{self.pf_command()} permissions check' reports it")
        for code, relative, message in inventory.blockers:
            if not excluded_path(relative):
                notes.append(f"permission-entry-unplanned: {scope_plan.scope}: {relative or '.'}: {code}: {message}")
        return differing, notes

    def _permission_execute(self, plan, *, action, apply_op, apply_dir, base_revision, base_sha256,
                            base_policy_sha256, already_approved, fenced_now, confirmed_plans, start_seq):
        """Steps 9-13 (section 3.6) of an apply or a resume; the intent is already persisted."""
        pf = self.pf_command()
        effects_path = apply_dir / PERMISSION_EFFECTS_NAME
        self._effect_seq, self._effect_count, self._changed_count, self._current_scope = start_seq, 0, 0, None
        selected = [scope for scope in plan.scopes if scope != "control"]
        freeze = plan.freeze_scopes
        root_fds, fences = {}, {}
        statuses = {"control": ("check-only", [])} if "control" in plan.scopes else {}
        unplanned = 0
        try:
            for scope in selected:
                scope_plan = plan.scope_plans[scope]
                root_fds[scope] = pf_instance.open_scope_root(scope_plan.root, scope_plan.inventory.root_identity)
            # Step 9: fence every freeze scope before any entry of any scope changes, then one scan.
            for scope in freeze:
                if scope in fenced_now:
                    continue
                info = os.fstat(root_fds[scope])
                before = stat.S_IMODE(info.st_mode)
                fenced = (before & 0o2000) | 0o700
                self._append_effects(effects_path, [self._effect_line("fence", scope, "", "dir", info.st_dev,
                                                                      info.st_ino, before, fenced, info.st_gid,
                                                                      info.st_gid)])
                os.fchmod(root_fds[scope], fenced)
                fences[scope] = before
            operations = {}
            if freeze:
                self._hook("after-fence")
                # The authoritative inventory comes first, so the open-handle scan also covers every entry created
                # between the plan and the fence (a holder there could keep racing the change otherwise).
                confirmed = {tuple(change[:4]) for change in plan.changes}
                for scope in freeze:
                    scope_plan = plan.scope_plans[scope]
                    fresh = self._scope_plan(scope, scope_plan.target, scope_plan.gid, fenced=True)
                    if fresh.blockers:
                        self._log_scope_block(fresh, details=False)
                        raise _EffectFree(f"permissions-blocked: The plan has {len(fresh.blockers)} blocker(s); see "
                                          "above. Nothing was changed.")
                    extra = [change for change in fresh.changes if tuple(change[:4]) not in confirmed]
                    unplanned += len(extra)
                    if unplanned > pf_instance.UNPLANNED_LIMIT:
                        raise _EffectFree(self._permission_changed(
                            f"{scope} (more than {pf_instance.UNPLANNED_LIMIT} entries)").args[0])
                    now = {tuple(change[:4]) for change in fresh.changes}
                    skipped = [change[1] or "." for change in scope_plan.changes if tuple(change[:4]) not in now]
                    if skipped:
                        fresh.notes.append(f"{len(skipped)} confirmed change(s) skipped: the entry now complies or is "
                                           "gone: " + ", ".join(skipped[:10]))
                    operations[scope] = fresh
                identities = frozenset((entry.dev, entry.ino) for scope in freeze
                                       for scope_plan in (plan.scope_plans[scope], operations[scope])
                                       for entry in scope_plan.entries)
                try:
                    holders = pf_instance.open_handles(identities)
                except pf_instance.ContextError as exc:
                    raise _EffectFree(f"editor-freeze-unavailable: Bulk change of {', '.join(freeze)} needs a verified "
                                      f"editor freeze, which is unavailable here ({exc}). Deselect it with --scope or "
                                      "follow SYNOLOGY_ADMIN §2.") from exc
                if holders:
                    shown = ", ".join(f"{pid} {comm} uid {uid} {holder}" for pid, comm, uid, holder in holders[:10])
                    raise _EffectFree(f"editor-freeze-refused: {', '.join(freeze)} is still in use by {len(holders)} "
                                      f"process(es) ({shown}); the fence was removed. A shell or sudo whose working "
                                      "directory is inside the folder counts too: start the command from outside it "
                                      "(for example 'cd /'). Nothing was changed.")
            # Step 10: apply scope by scope in the fixed order; entries deepest first, the root last.
            for scope in selected:
                scope_plan = operations.get(scope, plan.scope_plans[scope])
                by_path = {entry.relative: entry for entry in scope_plan.entries}
                directories = {entry.relative: (entry.dev, entry.ino) for entry in scope_plan.entries
                               if entry.type == "dir"}
                work = sorted(((by_path[change[1]], change[7], change[8]) for change in scope_plan.changes),
                              key=lambda item: depth_order(item[0].relative))
                self._apply_operations(scope, work, root_fds[scope], directories, effects_path)
                fences.pop(scope, None)
            # Step 11: verify each selected scope by re-inventory.
            excluded = {"operations/" + apply_op, "operations/" + self.operation_id, "state/pending.json"}
            failed = []
            for scope in selected:
                scope_plan = operations.get(scope, plan.scope_plans[scope])
                differing, notes = self._verify_scope(scope_plan, excluded if scope == "private_state" else set())
                statuses[scope] = ("partial" if differing else "yes", list(scope_plan.notes) + notes)
                if differing:
                    failed.append((scope, differing))
            if failed:
                scope, differing = failed[0]
                self._write_permission_outcome(apply_op=apply_op, action=action, result="interrupted",
                                               plan_sha256=plan.plan_sha256, unplanned=unplanned, conflicts=0,
                                               approved_revision=None, statuses=statuses)
                raise _VerifyFailed(f"permissions-verify-failed: {scope}: {', '.join(differing[:10])} does not hold its "
                                    f"target after the change (re-inventory); the apply is not complete. Run "
                                    f"'{pf} permissions apply --resume' or '--abandon'.")
            self._hook("after-verify")
            # Step 12: approve (only after every selected scope verified).
            approved_revision = base_revision + 1 if already_approved else None
            if not already_approved and (base_revision == 0 or plan.policy_sha256 != base_policy_sha256):
                record = {"schema_version": 1, "instance_id": self.context.instance_id, "revision": base_revision + 1,
                          "approved": utc(), "operation_id": apply_op, "policy_sha256": plan.policy_sha256,
                          "previous_sha256": base_sha256, "confirmed_plans": list(confirmed_plans),
                          "policy": plan.policy}
                defs = pf_config.PERMISSION_APPROVAL_SCHEMA["$defs"]
                errors = pf_install.validate_marked(record, defs["record"], defs=defs) \
                    or pf_config.permission_approval_problems(record, instance_id=self.context.instance_id)
                if errors:
                    raise Failure(f"Internal error: the approval record failed its schema ({errors[0]}).")
                data = pf_instance.normalize_json(record)
                pf_instance._write_private_file(self.operation_dir / PERMISSION_APPROVAL_COPY, data, 0o600)
                self._hook("after-approval-copy")
                pf_instance._write_private_file(self.permission_record_path, data, 0o600)
                self._hook("after-approval")
                approved_revision = record["revision"]
            # Step 13: close.
            self._write_permission_outcome(apply_op=apply_op, action=action, result="completed",
                                           plan_sha256=plan.plan_sha256, unplanned=unplanned, conflicts=0,
                                           approved_revision=approved_revision, statuses=statuses)
            self.pending.unlink()
        except _EffectFree:
            try:
                self._restore_fences(fences, root_fds, effects_path)
            except BaseException as restore_exc:
                self._mark_interrupted()
                raise Failure(f"permissions-interrupted: The permission apply was interrupted after "
                              f"{self._effect_count} recorded change(s) while its fence was removed; it is NOT "
                              f"complete. Run '{pf} permissions apply --resume' or '--abandon'.") from restore_exc
            if action == "apply":
                self._write_permission_outcome(apply_op=apply_op, action=action, result="abandoned",
                                               plan_sha256=plan.plan_sha256, unplanned=0, conflicts=0,
                                               approved_revision=None, statuses={})
                self.pending.unlink()
            else:
                # A resume keeps the original apply's journal open: its earlier effects stay compensable.
                self._write_permission_outcome(apply_op=apply_op, action=action, result="interrupted",
                                               plan_sha256=plan.plan_sha256, unplanned=0, conflicts=0,
                                               approved_revision=None, statuses={})
                self._mark_interrupted()
            raise
        except _VerifyFailed:
            self._mark_interrupted()
            raise
        except pf_instance.PermissionEntryChanged as exc:
            self._mark_interrupted()
            raise Failure(f"permissions-entry-changed: {self._current_scope}: {exc.relative or '.'} changed during the "
                          f"apply ({exc.detail}); it was not touched. {self._changed_count} object(s) were changed "
                          f"before; run '{pf} permissions apply --resume' or '--abandon'.") from exc
        except pf_instance.PermissionVerifyFailed as exc:
            self._mark_interrupted()
            raise Failure(f"permissions-verify-failed: {self._current_scope}: {exc.relative or '.'} does not hold its "
                          f"target after the change ({exc.observed}); the apply is not complete. Run '{pf} permissions "
                          "apply --resume' or '--abandon'.") from exc
        except BaseException as exc:
            self._mark_interrupted()
            raise Failure(f"permissions-interrupted: The permission apply was interrupted after {self._effect_count} "
                          f"recorded change(s); it is NOT complete. Run '{pf} permissions apply --resume' or "
                          "'--abandon'.") from exc
        finally:
            for fd in root_fds.values():
                os.close(fd)
            self._permission_targets = None
        revision = approved_revision or base_revision
        log("")
        self.log_permission_status([(scope, statuses[scope][0] if scope in statuses else "not-selected",
                                     plan.scope_plans.get(scope)) for scope in PERMISSION_SCOPES])
        for scope in selected:
            for note in statuses[scope][1]:
                log("  Note: " + note)
        log(f"permissions-applied: Permission policy revision {revision} applied.")
        return 0

    def _load_permission_journal(self, journal):
        """The open apply journal: (apply op id, its directory, the frozen plan, effect lines, valid byte length)."""
        apply_op = journal.get("operation_id")
        if not isinstance(apply_op, str) or not re.fullmatch(pf_config._OPERATION_ID[1:-1], apply_op):
            raise self._journal_invalid(self.pending, 1)
        apply_dir = self.context.operations_dir / apply_op
        path = apply_dir / PERMISSION_PLAN_NAME
        try:
            data = pf_instance.read_bytes_nofollow(path)
            document = pf_instance.parse_strict_json(data, label=str(path))
        except (OSError, pf_instance.ContextError) as exc:
            raise self._journal_invalid(path, 1) from exc
        defs = pf_config.PERMISSION_APPLY_SCHEMA["$defs"]
        if (pf_install.validate_marked(document, defs["plan"], defs=defs) or document["operation_id"] != apply_op
                or document["instance_id"] != self.context.instance_id
                or pf_config.permission_policy_problems(document["policy"])
                or pf_instance.sha256_bytes(pf_instance.normalize_json(document["policy"]))
                != document["policy_sha256"]):
            raise self._journal_invalid(path, 1)
        effects, valid = self._read_effects(apply_dir / PERMISSION_EFFECTS_NAME)
        return apply_op, apply_dir, document, effects, valid

    def _fenced_scopes(self, effects, document):
        """Scopes whose root is still at the fence mode an earlier invocation of this journal set (section 3.7)."""
        fenced = set()
        for scope in document["freeze_scopes"]:
            lines = [line for line in effects if line["kind"] == "fence" and line["scope"] == scope]
            if not lines:
                continue
            last = lines[-1]
            if last["after_mode"] != (last["before_mode"] & 0o2000) | 0o700 or last["after_mode"] == last["before_mode"]:
                continue
            try:
                info = os.lstat(str(self.scope_root(scope)))
            except OSError:
                continue
            if (info.st_dev, info.st_ino) == (last["dev"], last["ino"]) \
                    and stat.S_IMODE(info.st_mode) == last["after_mode"]:
                fenced.add(scope)
        return frozenset(fenced)

    def _permissions_resume(self, journal):
        """`pf permissions apply --resume` (section 3.7): revalidate, re-plan with the frozen policy, confirm once,
        then steps 9-13; effect lines continue the original journal."""
        apply_op, apply_dir, document, effects, valid = self._load_permission_journal(journal)
        policy = document["policy"]
        self.validation = None
        if not self.ensure_validation().mutation_allowed:
            raise self._permission_changed("The protected context")
        compiled = pf_config.compile_permission_policy(policy)
        for root in document["roots"]:
            scope, target = root["scope"], compiled[root["scope"]]
            if target.gid_rule == "set":
                try:
                    gid = grp.getgrnam(target.group).gr_gid
                except KeyError as exc:
                    raise self._permission_changed(f"Group {target.group}") from exc
                if gid != root["gid"]:
                    raise self._permission_changed(f"Group {target.group}")
            try:
                identity = pf_instance.path_identity(self.scope_root(scope))
            except pf_instance.ContextError as exc:
                raise self._permission_changed(f"The {scope} folder") from exc
            if identity != (root["dev"], root["ino"]):
                raise self._permission_changed(f"The {scope} folder")
        current = self.read_permission_record()
        already, base_policy_sha256 = False, None
        if current is None:
            if document["base_sha256"] is not None:
                raise self._permission_changed("The approved permission policy record")
        elif pf_instance.sha256_bytes(current[1]) == document["base_sha256"]:
            base_policy_sha256 = current[0]["policy_sha256"]
        elif pf_config.approval_matches_journal(current[0], document):
            already = True
        else:
            raise self._permission_changed("The approved permission policy record")
        fenced_now = self._fenced_scopes(effects, document)
        plan = self._permission_plan(policy, document["scopes"], fenced=fenced_now)
        log(f"Resume of permission apply {apply_op} for instance {self.context.slug} (frozen permission policy "
            f"{document['policy_sha256'][:12]}{'; its revision is already written' if already else ''})")
        self.log_permission_plan(plan, details=False)
        if plan.blockers:
            raise Failure(f"permissions-blocked: The plan has {len(plan.blockers)} blocker(s); see above. The "
                          "interrupted apply stays open; nothing more was changed.")
        base = EffectivePolicy(policy, document["base_revision"], "approved", None, None, None)
        permission_confirm("RESUME PERMISSIONS " + self.context.slug, self._permission_summary(
            f"Resume: the remaining changes of permission apply {apply_op}", base, policy, plan))
        self._hook("after-plan")
        pf_instance._write_private_file(self.operation_dir / PERMISSION_CHANGES_NAME, plan.changes_bytes, 0o600)
        effects_path = apply_dir / PERMISSION_EFFECTS_NAME
        if os.path.lexists(str(effects_path)) and os.lstat(str(effects_path)).st_size > valid:
            # A torn final line was never executed (write-ahead); cut it before the journal continues.
            fd = os.open(str(effects_path), os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.ftruncate(fd, valid)
                os.fsync(fd)
            finally:
                os.close(fd)
        confirmed = list(journal.get("confirmed_plans") or [{"operation_id": apply_op,
                                                             "plan_sha256": document["plan_sha256"]}])
        confirmed.append({"operation_id": self.operation_id, "plan_sha256": plan.plan_sha256})
        write_json(self.pending, dict(journal, phase="applying", confirmed_plans=confirmed))
        return self._permission_execute(
            plan, action="resume", apply_op=apply_op, apply_dir=apply_dir, base_revision=document["base_revision"],
            base_sha256=document["base_sha256"], base_policy_sha256=base_policy_sha256, already_approved=already,
            fenced_now=fenced_now, confirmed_plans=confirmed, start_seq=effects[-1]["seq"] if effects else 0)

    def _permissions_abandon(self, journal):
        """`pf permissions apply --abandon` (section 3.7): compensate from the effect journal in reverse order."""
        apply_op, apply_dir, document, effects, _ = self._load_permission_journal(journal)
        current = self.read_permission_record()
        if current is not None and pf_config.approval_matches_journal(current[0], document):
            raise Failure(f"permissions-already-approved: Permission policy revision {current[0]['revision']} was "
                          f"already written by this apply; only '{self.pf_command()} permissions apply --resume' can "
                          "finish it.")
        permission_confirm("ABANDON PERMISSIONS " + self.context.slug,
                           f"Abandon permission apply {apply_op}: {len(effects)} recorded change(s) are compensated in "
                           "reverse order; an object changed since the interruption is left as it is and listed.")
        self._changed_count = 0
        conflicts = []
        root_fds = {}
        try:
            for line in reversed(effects):
                scope, relative = line["scope"], line["path"]
                where = f"{scope}:{relative or '.'}"
                if scope not in root_fds:
                    root = self.scope_root(scope)
                    try:
                        root_fds[scope] = pf_instance.open_scope_root(root, pf_instance.path_identity(root))
                    except pf_instance.ContextError:
                        root_fds[scope] = None
                if root_fds[scope] is None:
                    conflicts.append(where)
                    continue
                try:
                    fd = self._open_effect_object(root_fds[scope], line)
                except (OSError, pf_instance.ContextError):
                    conflicts.append(where)
                    continue
                try:
                    info = os.fstat(fd)
                    kind = "dir" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "other"
                    acl = pf_instance.inspect_acl_fd(fd)
                    if (info.st_dev, info.st_ino, kind) != (line["dev"], line["ino"], line["type"]) \
                            or acl.state.kind == "unknown" or pf_instance.POSIX_ACL_ACCESS in acl.names:
                        conflicts.append(where)
                        continue
                    mode, gid = stat.S_IMODE(info.st_mode), info.st_gid
                    before, after = (line["before_mode"], line["before_gid"]), (line["after_mode"], line["after_gid"])
                    if (mode, gid) == before:
                        continue
                    half = gid in (before[1], after[1]) and mode in (before[0], before[0] & ~0o6000, after[0])
                    if (mode, gid) != after and not half:
                        conflicts.append(where)
                        continue
                    if gid != before[1]:
                        os.fchown(fd, -1, before[1])
                    os.fchmod(fd, before[0])
                    self._changed_count += 1
                finally:
                    os.close(fd)
        finally:
            for fd in root_fds.values():
                if fd is not None:
                    os.close(fd)
        result = "abandoned-with-conflicts" if conflicts else "abandoned"
        statuses = {scope: ("no", [f"conflict: {item}" for item in conflicts if item.startswith(scope + ":")])
                    for scope in document["scopes"]}
        self._write_permission_outcome(apply_op=apply_op, action="abandon", result=result, plan_sha256=None,
                                       unplanned=0, conflicts=len(conflicts), approved_revision=None,
                                       statuses=statuses)
        self.pending.unlink()
        log(f"Permission apply {apply_op} abandoned: {self._changed_count} object(s) restored; the permission policy "
            "record was not touched.")
        if conflicts:
            raise Failure(f"permissions-abandon-conflicts: {len(conflicts)} object(s) changed after the interruption and "
                          f"were left as they are: {', '.join(conflicts[:10])}.")
        return 0

    @staticmethod
    def _open_effect_object(root_fd, line):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC \
            | (os.O_DIRECTORY if line["type"] == "dir" else 0)
        if line["path"] == "":
            return os.dup(root_fd)
        parent, name = pf_instance.open_entry_parent(root_fd, line["path"], {})
        try:
            return os.open(name, flags, dir_fd=parent)
        finally:
            os.close(parent)

    def _scope_relative(self, scope, path):
        root, path = self.scope_root(scope), Path(path)
        if path == root:
            return ""
        try:
            return path.relative_to(root).as_posix()
        except ValueError as exc:
            raise Failure(f"Internal error: {path} is not inside the {scope} scope {root}.") from exc

    def _publish(self, scope, inventory, *, executables=frozenset()):
        """Explicit targets for a fresh or single entry set (section 3.10), then verification by the engine."""
        effective, compiled, gids = self.permission_targets()
        target, gid = compiled[scope], gids[scope]
        acl = set()
        for code, relative, message in inventory.blockers:
            if code == "scope-entry-acl":
                if scope in PROTECTED_SCOPES:
                    raise Failure(f"fresh-entry-acl: {scope}: new entry {relative or '.'} inherited an ACL from its "
                                  "folder; the policy cannot describe it, so the operation stopped. Remove the default "
                                  f"ACL from {self.scope_root(scope)} (SYNOLOGY_ADMIN §16).")
                acl.add(relative)
                continue
            raise Failure(f"{code}: {scope}: {relative or '.'}: {message}; nothing was published over it.")
        work = []
        for entry in inventory.entries:
            if entry.relative in acl:
                log(f"fresh-entry-acl: {scope}: new entry {entry.relative or '.'} has an ACL; its mode was left as "
                    "created.")
                continue
            if scope == "workspace" and entry.uid != pf_instance.TRUSTED_UID:
                log(f"workspace-concurrent-entry: {scope}: {entry.relative} was created by an editor (uid {entry.uid}) "
                    "during this operation; it was left untouched.")
                continue
            mode, new_gid = permission_entry_target(target, gid, entry, executables)
            if (entry.mode, entry.gid) != (mode, new_gid):
                work.append((entry, mode, new_gid))
        if not work:
            return
        work.sort(key=lambda item: depth_order(item[0].relative))
        directories = {entry.relative: (entry.dev, entry.ino) for entry in inventory.entries if entry.type == "dir"}
        root_fd = pf_instance.open_scope_root(inventory.root, inventory.root_identity)
        try:
            for entry, mode, new_gid in work:
                if entry.relative == "":
                    pf_instance.apply_entry_target(root_fd, None, entry, mode=mode, gid=new_gid)
                    continue
                parent, name = pf_instance.open_entry_parent(root_fd, entry.relative, directories)
                try:
                    pf_instance.apply_entry_target(parent, name, entry, mode=mode, gid=new_gid)
                finally:
                    os.close(parent)
        except pf_instance.PermissionEntryChanged as exc:
            raise Failure(f"permissions-entry-changed: {scope}: {exc.relative or '.'} changed while its permission "
                          f"target was set ({exc.detail}); it was not touched.") from exc
        except pf_instance.PermissionVerifyFailed as exc:
            raise Failure(f"permissions-verify-failed: {scope}: {exc.relative or '.'} does not hold its target after "
                          f"the change ({exc.observed}).") from exc
        finally:
            os.close(root_fd)

    def publish_fresh(self, scope, path, *, executables=frozenset()):
        """Exact policy targets for the subtree this operation just created (no freeze, no effect journal)."""
        _, compiled, _ = self.permission_targets()
        inventory = pf_instance.inventory_scope(scope, self.scope_root(scope), owner_rule=compiled[scope].owner_rule,
                                                limit=pf_source.MANIFEST_ENTRY_LIMIT,
                                                subtree=self._scope_relative(scope, path) or None)
        self._publish(scope, inventory, executables=executables)

    def apply_single(self, scope, path, kind):
        """The policy target for one existing entry (a registered directory, .env), with the engine checks."""
        _, compiled, _ = self.permission_targets()
        inventory = pf_instance.inventory_scope(scope, self.scope_root(scope), owner_rule=compiled[scope].owner_rule,
                                                limit=pf_source.MANIFEST_ENTRY_LIMIT,
                                                subtree=self._scope_relative(scope, path) or None, recurse=False)
        if not inventory.blockers and (len(inventory.entries) != 1 or inventory.entries[0].type != kind):
            raise Failure(f"{scope}: {path} is not one existing {kind}; nothing was changed.")
        self._publish(scope, inventory)

    def permission_proposal_lines(self):
        """The admin wizard's summary lines (section 4.5): both pf-config.json groups are proposals."""
        found = self.read_permission_record()
        pf_command = self.pf_command()
        if found is not None:
            return [f"backup_read_group and workspace_write_group are proposals: permission policy revision "
                    f"{found[0]['revision']} stays in force until '{pf_command} permissions apply' approves a change."]
        return [f"backup_read_group is a proposal: backups and recovery bundles keep the group of their folders until "
                f"'{pf_command} permissions apply' approves a change. workspace_write_group applies to files pf "
                f"creates in the workspace and configuration until the first '{pf_command} permissions apply' "
                "approves a permission policy."]

    def configuration_create_target(self, workspace_group):
        """(gid, mode) of a file the config writer creates (OD-A22-11): the configuration target of the approved
        permission policy, else the derived one (``workspace_group``, read and edit)."""
        found = self.read_permission_record()
        if found is not None:
            target = pf_config.compile_permission_policy(found[0]["policy"])["configuration"]
            return self.resolve_policy_gids(found[0]["policy"])["configuration"], target.file_mode
        try:
            return grp.getgrnam(workspace_group).gr_gid, 0o660
        except KeyError as exc:
            raise self.group_missing("configuration", workspace_group) from exc

    def describe_permissions(self):
        """The doctor line (section 4.5): no scope walk."""
        try:
            admin, _ = self.admin_values_for_permissions()
            found = self.read_permission_record()
            if found is not None:
                effective = EffectivePolicy(found[0]["policy"], found[0]["revision"], "approved", None, found[0], found[1])
                text = effective.label
            else:
                text = (f"permission policy not approved; derived (backups/recovery groups from their folders, "
                        f"workspace/configuration from pf-config.json); run '{self.pf_command()} permissions plan', then "
                        "'apply'")
                effective = self.permission_policy(admin=(admin, None)) if admin is not None else None
            if effective is not None:
                proposals = self.permission_proposals(effective, admin)
                if proposals:
                    text += " | proposals: " + ", ".join(f"{scope} {current} -> {proposed}"
                                                         for scope, proposed, current in proposals)
            journal = self.read_journal()
            if journal is not None and journal.get("operation") == "permissions":
                text += f" | interrupted apply {journal.get('operation_id')}"
        except Failure as exc:
            text = "unavailable: " + str(exc).splitlines()[0]
        return text

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
            # OD-A22-16 / PF-A2.3: the configuration file target of the permission policy in force.
            self.apply_single("configuration", env_path, "file")
            self.freeze_app_config()
            return values

        # PF-A2.2: the missing-.env branch is the app-variable wizard (the record's profile declaration).
        values = self.app_wizard(inside_deploy=True)
        log("Created " + str(env_path) + " with the configuration permission target. It remains outside the "
            "repository.")
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
            app_hint=f"Application variables are not in this file; use '{pf_command} config app'.",
            permission_lines=self.permission_proposal_lines())
        record = {"schema_version": 1, "operation_id": self.operation_id, "completed": utc(), "file": "pf-config.json",
                  "mode": mode, "profile_id": None, "schema_before": None if before is None else before.schema_version,
                  "schema_after": pf_config.ADMIN_CONFIG_SCHEMA_VERSION,
                  "before_sha256": pf_instance.sha256_bytes(target.data) if target.present else None,
                  "after_sha256": pf_instance.sha256_bytes(data), "changes": rows}
        self.check_config_change(record)
        confirm_write(path)
        changed = [row["key"] for row in rows if row["action"] != "kept"]
        create_gid, create_mode = self.configuration_create_target(values["workspace_write_group"])
        write_reviewed(path, data, target, create_gid=create_gid, op8=self.operation_id[-8:], keys=changed,
                       create_mode=create_mode)
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
        create_gid, create_mode = self.configuration_create_target(self.config["workspace_write_group"])
        write_reviewed(path, data, target, create_gid=create_gid, op8=self.operation_id[-8:], keys=changed,
                       secret=generated, create_mode=create_mode)
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

    def candidate_manifest(self, tree, revision, *, verified):
        """The pf_source manifest of a private candidate tree with its proven provenance (a commit only when the
        protected store proved it); a pure read."""
        source = {"kind": "git_commit", "commit": revision, "remote": self.approved_remote()} \
            if verified and isinstance(revision, str) and SHA_RE.fullmatch(revision) else {"kind": "unknown"}
        try:
            return pf_source.build_manifest(tree, source=source, excludes=SOURCE_EXCLUDES)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc

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

    CANNOT_RECONSTRUCT = (
        "Cannot reconstruct the exact deployed source revision: it is neither in the protected source store "
        "nor proven equal to the workspace by the protected manifest. No destructive operation will continue "
        "until the deployed source can be archived.")

    def deployed_source_origin(self, revision):
        """Read-only (section 3.4 preflight): "protected-store" or "workspace-proven" when the exact tree of
        ``revision`` can be archived as proven deployed source, else None."""
        if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
            return None
        store = self.source_store()
        if store.exists():
            try:
                store.verify()
                if store.has_commit(revision):
                    return "protected-store"
            except pf_source.SourceError:
                pass
        manifest = self.load_source_manifest()
        if manifest is not None and manifest["source"]["kind"] == "git_commit" \
                and manifest["source"]["commit"] == revision:
            try:
                if pf_source.compare_manifest(self.root, manifest, excludes=SOURCE_EXCLUDES)["matches"]:
                    return "workspace-proven"
            except (pf_source.SourceError, OSError):
                return None
        return None

    def create_deployed_source_archive(self, destination, revision):
        """Archive the exact deployed revision from the protected store, or from a workspace proven equal to it.

        PF-A3.1: both paths archive with archive_verified_tree (bytes proven equal to a manifest while archived) and
        return {"origin", "manifest", "expanded_bytes", "members", "members_sha256"} for the payload entry."""
        destination = Path(destination)
        store = self.source_store()
        store_has_commit = False
        if isinstance(revision, str) and SHA_RE.fullmatch(revision) and store.exists():
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
                    manifest = pf_source.build_manifest(
                        tree, source={"kind": "git_commit", "commit": revision, "remote": self.approved_remote()},
                        excludes=SOURCE_EXCLUDES)
                    _, expanded, members_sha256 = pf_source.archive_verified_tree(tree, manifest, destination,
                                                                                  excludes=SOURCE_EXCLUDES)
                except pf_source.SourceError as exc:
                    raise Failure(str(exc)) from exc
            return {"origin": "protected-store", "manifest": manifest, "expanded_bytes": expanded,
                    "members": len(manifest["entries"]), "members_sha256": members_sha256}
        manifest = self.load_source_manifest()
        if manifest is not None and manifest["source"]["kind"] == "git_commit" \
                and manifest["source"]["commit"] == revision:
            # The archive is built from the workspace bytes while each file is proven equal to
            # the manifest (one read per file): no separate compare-then-copy window.
            try:
                _, expanded, members_sha256 = pf_source.archive_verified_tree(self.root, manifest, destination,
                                                                              excludes=SOURCE_EXCLUDES)
            except pf_source.SourceError as exc:
                raise Failure("Cannot archive the deployed source from the workspace: " + str(exc)) from exc
            return {"origin": "workspace-proven", "manifest": manifest, "expanded_bytes": expanded,
                    "members": len(manifest["entries"]), "members_sha256": members_sha256}
        raise Failure(self.CANNOT_RECONSTRUCT)

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

    # ------------------------------------------- deployed-source artifact store (PF-A3.1 section 3.3)

    @property
    def deployments_dir(self):
        return self.context.artifacts_dir / "deployments"

    @property
    def verifications_dir(self):
        return self.context.artifacts_dir / "verifications"

    def producer(self):
        """$defs.producer: the control release, profile and instance record this process runs with."""
        context = self.context
        return {"control_release_id": context.control.release_id, "control_sha256": context.control.sha256,
                "profile_id": context.profile.id, "profile_version": context.profile.version,
                "profile_sha256": context.profile.sha256, "instance_record_sha256": context.record_sha256}

    def read_pointer(self):
        """``state/deployed.json`` (strict JSON object) or None when absent. Read-only."""
        path = self.state / "deployed.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise Failure(f"Protected deployment pointer is unreadable: {path}: {exc.strerror or exc}") from exc
        try:
            value = pf_instance.parse_strict_json(data, label=str(path))
        except pf_instance.ContextError as exc:
            raise Failure(f"Protected deployment pointer is invalid: {exc}") from exc
        if not isinstance(value, dict):
            raise Failure(f"Protected deployment pointer is invalid: {path} is not a JSON object")
        return value

    @staticmethod
    def private_dir(path):
        """A private (0700) directory pf creates under protected private state; an existing one must be real."""
        path = Path(path)
        if not real_directory(path):
            os.mkdir(str(path), 0o700)
            os.chmod(str(path), 0o700)
        return path

    def current_deployment(self):
        """The deployment ``deployed.json`` points to (read-only; never raises on a mismatch): None for a pointer
        without ``deployment_id`` (a deployment created before PF-A3.1, or a failed seal), else a DeploymentView whose
        ``mismatch`` names the first file that differs from the record ("<file>: <detail>")."""
        pointer = self.read_pointer()
        if pointer is None or "deployment_id" not in pointer:
            return None
        deployment_id = pointer["deployment_id"]
        if not isinstance(deployment_id, str) or not DEPLOYMENT_ID_RE.fullmatch(deployment_id):
            return DeploymentView(str(deployment_id), None, None, None, "deployed.json: deployment_id is malformed")
        folder = self.deployments_dir / deployment_id
        try:
            dir_fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return DeploymentView(deployment_id, folder, None, None, "deployment folder: missing or not a directory")
        try:
            record, record_sha256, mismatch = self._deployment_check(dir_fd, deployment_id,
                                                                     pointer.get("deployment_record_sha256"))
        finally:
            os.close(dir_fd)
        return DeploymentView(deployment_id, folder, record, record_sha256, mismatch)

    def _deployment_check(self, dir_fd, deployment_id, expected_sha256):
        try:
            data = self._bundle_file(dir_fd, "deployment-record.json", MANIFEST_READ_LIMIT)
        except (OSError, ValueError) as exc:
            return None, None, f"deployment-record.json: {getattr(exc, 'strerror', None) or exc}"
        record_sha256 = pf_instance.sha256_bytes(data)
        if record_sha256 != expected_sha256:
            return None, record_sha256, "deployment-record.json: differs from deployed.json"
        try:
            record = pf_instance.parse_strict_json(data, label="deployment-record.json")
        except pf_instance.ContextError as exc:
            return None, record_sha256, f"deployment-record.json: {exc}"
        problems = [] if data == pf_instance.normalize_json(record) else ["not normalized"]
        problems = problems or lifecycle_errors(record, "deployment_record")
        if not problems and (record["deployment_id"] != deployment_id
                             or record["instance_id"] != self.context.instance_id):
            problems = ["names another deployment or instance"]
        if problems:
            return None, record_sha256, f"deployment-record.json: {problems[0]}"
        for path, size, sha256 in (("source.tar.gz", record["source"]["archive"]["size"],
                                    record["source"]["archive"]["sha256"]),
                                   ("source-manifest.json", None, record["source"]["manifest"]["sha256"]),
                                   ("compose-resolved.json", None, record["compose"]["file_sha256"]),
                                   ("config.env", record["config"]["bytes"], record["config"]["sha256"])):
            try:
                self._verify_payload(dir_fd, deployment_id, path, size, sha256)
            except Failure as exc:
                return record, record_sha256, getattr(exc, "detail", None) or path
        return record, record_sha256, None

    def deployed_commit(self):
        """The deployed source commit (section 3.3 Read): a valid record's ``source.commit`` (may be None), else the
        legacy ``deployed.json`` ``sha`` when it is a full SHA, else None. Never a claim: a pre-A3.1 rollback or
        restore wrote the checkpoint's unproven claim there, so such a pointer's ``sha`` counts only when the protected
        source manifest records that same commit as ``git_commit`` (audit AF-3); an A3.1 pointer whose seal failed
        carries a proven commit or null."""
        view = self.current_deployment()
        if view is not None and view.mismatch is None:
            return view.record["source"]["commit"]
        pointer = self.read_pointer() or {}
        sha = pointer.get("sha")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            return None
        ref = pointer.get("ref")
        if "deployment_id" in pointer or "deployment_seal_failed" in pointer or not isinstance(ref, str) \
                or not ref.startswith(("rollback:", "restore:")):
            return sha
        try:
            manifest = self.load_source_manifest()
        except Failure:
            return None
        proven = manifest is not None and manifest["source"]["kind"] == "git_commit" \
            and manifest["source"].get("commit") == sha
        return sha if proven else None

    def describe_deployed_source(self):
        commit = self.deployed_commit()
        return commit if commit is not None else "unknown provenance (no proven commit is recorded)"

    def deployment_inventory(self, view):
        """(unsealed staging count, unreferenced sealed deployments): read-only, best effort while an operation may
        be writing. A sealed deployment is referenced when it is current or in the current record's chain."""
        try:
            names = os.listdir(str(self.deployments_dir)) if real_directory(self.deployments_dir) else []
        except OSError:
            names = []
        staging = sum(1 for name in names if name.startswith(".staging-"))
        referenced = set()
        record = view.record if view is not None else None
        if view is not None:
            referenced.add(view.deployment_id)
        while record is not None and record.get("previous_deployment_id") and len(referenced) < 1000:
            previous = record["previous_deployment_id"]
            referenced.add(previous)
            try:
                record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                    self.deployments_dir / previous / "deployment-record.json"), label=previous)
                record = record if isinstance(record, dict) else None
            except (OSError, pf_instance.ContextError):
                record = None
        unreferenced = sum(1 for name in names if DEPLOYMENT_ID_RE.fullmatch(name) and name not in referenced)
        return staging, unreferenced

    def describe_deployment(self):
        """The status/doctor deployment line (read-only, no lock, no write)."""
        pointer = self.read_pointer()
        view = self.current_deployment()
        if view is None:
            if pointer is None:
                text = "none (no deployed.json)"
            elif pointer.get("deployment_seal_failed"):
                text = (f"not recorded (seal failed in operation {pointer['deployment_seal_failed']}; the next "
                        "deploy/update/rollback seals one)")
            else:
                text = "legacy (no deployment record; created before PF-A3.1)"
        elif view.mismatch is not None:
            text = f"{view.deployment_id} deployment-artifact-mismatch: {view.mismatch}"
        else:
            source = view.record["source"]
            provenance = "git_commit " + source["commit"][:12] if source["provenance"] == "git_commit" else "unknown"
            text = f"{view.deployment_id} ({provenance}), sealed {view.record['created_at']}"
        staging, unreferenced = self.deployment_inventory(view)
        if staging:
            text += f"\nUnsealed deployment staging: {staging}"
        if unreferenced:
            text += f"\nUnreferenced deployments: {unreferenced}"
        return text

    @staticmethod
    def artifact_free_bytes(path):
        info = os.statvfs(str(path))
        return info.f_bavail * info.f_frsize

    def deployment_preflight(self, manifest):
        """Read-only, before the final confirmation (section 3.3): room for one deployed-source artifact."""
        directory = self.deployments_dir if real_directory(self.deployments_dir) else self.context.artifacts_dir
        need = sum(entry.get("size", 0) for entry in manifest["entries"]) + ARTIFACT_MARGIN
        mib = 1024 * 1024
        try:
            free = self.artifact_free_bytes(directory)
        except OSError as exc:
            raise Failure(f"artifact-capacity: free space in {directory} cannot be measured "
                          f"({exc.strerror or exc}). Nothing was changed.") from exc
        if free < need:
            raise Failure(f"artifact-capacity: {-(-need // mib)} MiB needed in {directory}, {free // mib} MiB free. "
                          "Nothing was changed.")

    def image_identity(self, image, *, reference):
        """$defs.image of one local image (ID or reference): one ``docker image inspect``; never archived."""
        data = json.loads(self.docker("image", "inspect", image))[0]
        image_id = data.get("Id")
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise Failure(f"Image {str(image)[:80]} has no image ID of the form sha256:<64 hex>.")
        platform = None
        os_name, architecture, variant = data.get("Os"), data.get("Architecture"), data.get("Variant")
        if isinstance(os_name, str) and os_name and isinstance(architecture, str) and architecture:
            platform = os_name + "/" + architecture + ("/" + variant if isinstance(variant, str) and variant else "")
        digests = sorted(item for item in (data.get("RepoDigests") or []) if isinstance(item, str))
        return {"reference": reference, "id": image_id, "platform": platform, "repo_digests": digests,
                "archived": False}

    def stage_deployment(self, candidate, manifest, *, kind, images=None, ref=None, deployment_id=None, pointer=None):
        """Stage the deployed-source artifact: the plan's first effect (PF-A3.2 ``source-stage deployment:<dep>``).

        ``manifest`` is the candidate's pf_source manifest with its proven provenance; ``images`` the backend/
        frontend images ({service: {"reference", "id"}}) known without effect (restore-instance: observed at seal).
        PF-A3.2: ``deployment_id`` is the plan's pre-assigned ID and ``pointer`` the base of the deployed.json the
        pointer effect writes; both are kept with the staged details in deployment-artifact.json so a resumed process
        can seal and point without the private candidate. Any failure -> StageFailed (``deployment-stage-failed``)."""
        candidate = Path(candidate)
        current = self.read_pointer() or {}
        previous = current.get("deployment_id")
        previous = previous if isinstance(previous, str) and DEPLOYMENT_ID_RE.fullmatch(previous) else None
        deployment_id = deployment_id or f"dep-{utc()}-{uuid.uuid4().hex[:8]}"
        staging = self.deployments_dir / (".staging-" + deployment_id)
        try:
            self.refuse_reserved_candidate_paths(candidate)
            if any(entry["kind"] != "file" for entry in manifest["entries"]):
                raise Failure("the candidate contains an unsupported link or special file")
            self.private_dir(self.deployments_dir)
            os.mkdir(str(staging), 0o700)
            os.chmod(str(staging), 0o700)
            archive = staging / "source.tar.gz"
            count, expanded, members_sha256 = pf_source.archive_verified_tree(candidate, manifest, archive,
                                                                              excludes=SOURCE_EXCLUDES)
            fd = os.open(str(archive), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                os.fsync(fd)
                archive_sha256, archive_size = self._fd_sha256(fd), os.fstat(fd).st_size
            finally:
                os.close(fd)
            manifest_data = pf_source.manifest_bytes(manifest)
            pf_instance._write_private_file(staging / "source-manifest.json", manifest_data, 0o600)
            source = manifest["source"]
            git = source["kind"] == "git_commit"
            staged_source = {
                "provenance": "git_commit" if git else "unknown", "commit": source.get("commit") if git else None,
                "remote": source.get("remote") if git else None, "ref": ref,
                "archive": {"path": "source.tar.gz", "size": archive_size, "sha256": archive_sha256},
                "manifest": {"path": "source-manifest.json", "sha256": pf_instance.sha256_bytes(manifest_data),
                             "entries_sha256": pf_source.entries_digest(manifest), "files": count},
                "expanded_bytes": expanded, "members_sha256": members_sha256}
            identities = None
            if images is not None:
                identities = {service: dict(images[service]) if kind == "restore-instance" else
                              self.image_identity(images[service]["id"], reference=images[service]["reference"])
                              for service in pf_docker.BUILT_SERVICES}
            staged = StagedDeployment(deployment_id, kind, staging, staged_source, identities, previous,
                                      pf_instance.sha256_bytes(pf_instance.normalize_json(migration_files(candidate))))
            problems = lifecycle_errors(self._deployment_record(staged, draft=True), "deployment_record")
            if problems:
                raise Failure(f"the draft deployment record is invalid ({problems[0]})")
            self.write_private_json("deployment-artifact.json", {
                "deployment_id": deployment_id, "state": "staged", "source_sha256": archive_sha256,
                "entries_sha256": staged_source["manifest"]["entries_sha256"],
                "staged": {"source": staged_source, "images": identities, "previous_deployment_id": previous,
                           "migration_files_sha256": staged.migration_files_sha256, "pointer": pointer or {}}})
            return staged
        except DaemonFailure:
            raise
        except (Failure, OSError, pf_source.SourceError, pf_instance.ContextError, ValueError, KeyError) as exc:
            raise StageFailed((str(exc).splitlines() or [type(exc).__name__])[0]) from exc

    def _deployment_record(self, staged, *, draft=False, images=None, compose=None, config=None, heads=None,
                           server=None, restored_from=None):
        """The DeploymentRecord of ``staged``; ``draft`` fills the seal-time values with placeholders so a writer bug
        fails before any effect (section 3.3 step 5)."""
        placeholder = "0" * 64
        if draft:
            images = {}
            for service in pf_docker.BUILT_SERVICES:
                known = (staged.images or {}).get(service) or {}
                images[service] = dict({"reference": f"{self.context.compose_project}-{service}:draft",
                                        "id": "sha256:" + placeholder, "repo_digests": [], "archived": False},
                                       **{key: known[key] for key in ("reference", "id") if key in known})
                images[service]["platform"] = known.get("platform") or "linux/amd64"
                images[service].setdefault("repo_digests", [])
                images[service]["archived"] = False
            images["db"] = {"reference": pf_docker.DB_IMAGE, "id": "sha256:" + placeholder, "platform": "linux/amd64",
                            "repo_digests": [], "archived": False}
            compose = {"path": "compose-resolved.json", "file_sha256": placeholder, "model_sha256": placeholder,
                       "compose_version": "draft", "installed_file_sha256": placeholder,
                       "override_sha256": placeholder}
            config = {"path": "config.env", "sha256": placeholder, "bytes": 1, "admin_config_sha256": None}
            heads, server = ["draft"], 160000
            if staged.kind == "restore-instance":
                restored_from = {"bundle_id": "purge-00000000T000000Z-000000000000-000000",
                                 "manifest_sha256": placeholder}
        source = {key: staged.source[key] for key in ("provenance", "commit", "remote", "ref", "archive", "manifest")}
        stamp = utc()
        return {
            "schema_version": 1, "deployment_id": staged.deployment_id, "instance_id": self.context.instance_id,
            "compose_project": self.context.compose_project, "created_at": stamp,
            "operation": {"kind": staged.kind, "operation_id": self.operation_id or "00000000T000000Z-draft-00000000"},
            "previous_deployment_id": staged.previous_deployment_id, "source": source, "images": images,
            "helpers": [], "strategy": dict(pf_config.STRATEGY), "compose": compose, "config": config,
            "producer": self.producer(),
            "database": {"server_version_num": server, "alembic_heads": heads,
                         "migration_files_sha256": staged.migration_files_sha256},
            "activation": {"result": "activated", "health": "passed", "completed_at": stamp},
            "restored_from": restored_from,
        }

    def _seal_compose(self, staging):
        """Step 2: the newest approved render whose full input key is the activation's, copied and re-hashed."""
        record_path = self.operation_dir / "compose-envelope.json"
        document = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(record_path), label=str(record_path))
        installed = pf_instance.sha256_bytes(pf_instance.read_bytes_nofollow(self.control_dir / "compose.nas.yaml"))
        override = pf_instance.sha256_bytes(pf_instance.read_bytes_nofollow(self.override))
        key = {"override_sha256": override, "frozen_env_sha256": self.frozen.env_sha256,
               "project_directory": str(self.root), "instance_id": self.context.instance_id,
               "compose_file_sha256": installed}
        renders = [render for render in document.get("renders", []) if isinstance(render, dict)
                   and render.get("result") == "approved" and isinstance(render.get("resolved_file"), str)
                   and re.fullmatch(r"compose-[0-9]+\.json", render["resolved_file"])
                   and isinstance(render.get("inputs"), dict)
                   and all(render["inputs"].get(name) == value for name, value in key.items())]
        if not renders:
            raise Failure("no approved resolved Compose render of the activation's inputs was recorded")
        render = renders[-1]
        data = pf_instance.read_bytes_nofollow(self.operation_dir / render["resolved_file"])
        model = pf_instance.parse_strict_json(data, label=render["resolved_file"])
        if pf_instance.sha256_bytes(pf_instance.normalize_json(model)) != render.get("resolved_sha256"):
            raise Failure("the recorded resolved Compose model does not re-hash to its approval")
        target = staging / "compose-resolved.json"
        pf_instance._write_private_file(target, data, 0o600)
        if pf_instance.sha256_bytes(pf_instance.read_bytes_nofollow(target)) != pf_instance.sha256_bytes(data):
            raise Failure("the copied resolved Compose model differs from its source")
        return {"path": "compose-resolved.json", "file_sha256": pf_instance.sha256_bytes(data),
                "model_sha256": render["resolved_sha256"],
                "compose_version": str(render.get("compose_version") or self.compose_version or "unknown"),
                "installed_file_sha256": installed, "override_sha256": override}

    def _seal_config(self, staging):
        """Step 3: the operation's frozen app snapshot, verified, as config.env (secrets: private state only)."""
        try:
            pf_config.verify_frozen(self.frozen)
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        data = pf_instance.read_bytes_nofollow(self.frozen.env_file)
        pf_instance._write_private_file(staging / "config.env", data, 0o600)
        try:
            admin = pf_instance.sha256_bytes(pf_instance.read_bytes_nofollow(self.config_dir / "pf-config.json"))
        except FileNotFoundError:
            admin = None
        return {"path": "config.env", "sha256": pf_instance.sha256_bytes(data), "bytes": len(data),
                "admin_config_sha256": admin}

    def seal_deployment(self, staged, *, restored_from=None):
        """Seal ``staged`` after ``activate()`` passed, before the pointer is written (section 3.3 Seal)."""
        images = dict(staged.images or {})
        for service in pf_docker.BUILT_SERVICES:
            known = images.get(service)
            if known is None or "platform" not in known:
                running = self.inspect(service)["Image"]
                if known is not None and running != known["id"]:
                    raise Failure(f"the running {service} image {running[:19]} is not the restored {known['id'][:19]}")
                reference = known["reference"] if known is not None else self.inspect_reference(service)
                images[service] = self.image_identity(running, reference=reference)
        images["db"] = self.image_identity(self.inspect("db")["Image"], reference=pf_docker.DB_IMAGE)
        compose = self._seal_compose(staged.staging)
        config = self._seal_config(staged.staging)
        record = self._deployment_record(staged, images=images, compose=compose, config=config,
                                         heads=self.db_heads(), server=self.server_version_num(),
                                         restored_from=restored_from)
        problems = lifecycle_errors(record, "deployment_record")
        if problems:
            raise Failure(f"the deployment record is invalid ({problems[0]})")
        data = pf_instance.normalize_json(record)
        pf_instance._write_private_file(staged.staging / "deployment-record.json", data, 0o600)
        pf_instance._fsync_directory(staged.staging)
        parent_fd = os.open(str(self.deployments_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            pf_instance.publish_private_dir(parent_fd, staged.staging.name, staged.deployment_id)
        finally:
            os.close(parent_fd)
        self.publish_fresh("private_state", self.deployments_dir / staged.deployment_id)
        return {"deployment_id": staged.deployment_id, "record_sha256": pf_instance.sha256_bytes(data)}

    def inspect_reference(self, service):
        """The tag the active override names for ``service`` (for an image observed at seal)."""
        try:
            images = pf_docker.parse_image_override(pf_instance.read_bytes_nofollow(self.override),
                                                    project=self.context.compose_project)
        except (OSError, pf_docker.DockerScopeError) as exc:
            raise Failure(f"the active image override cannot be read ({exc})") from exc
        return images[service]

    # ------------------------------------------- PostgreSQL facts (PF-A3.1 section 3.8)

    def server_version_num(self):
        text = self.sql("postgres", "SHOW server_version_num;")
        if not re.fullmatch(r"[0-9]{5,7}", text or ""):
            raise Failure("Unexpected PostgreSQL server version output.")
        return int(text)

    def database_rows(self):
        """{name: per-database facts} of every non-template database except ``postgres`` (read-only)."""
        rows = {}
        for line in self.sql("postgres", FACTS_SQL).splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) != 6 or not PG_IDENTIFIER_RE.fullmatch(parts[0]) or not PG_IDENTIFIER_RE.fullmatch(parts[1]) \
                    or parts[5] not in ("t", "f") or not 1 <= len(parts[2]) <= 32 \
                    or not all(1 <= len(value) <= 128 for value in parts[3:5]):
                raise Failure("Unexpected PostgreSQL inventory output.")
            rows[parts[0]] = {"name": parts[0], "owner": parts[1], "encoding": parts[2], "collate": parts[3],
                              "ctype": parts[4], "allow_connections": parts[5] == "t"}
        return rows

    def extensions(self, database):
        result = []
        for line in self.sql(database, EXTENSIONS_SQL).splitlines():
            if not line:
                continue
            name, separator, version = line.partition("|")
            if not separator or not PG_IDENTIFIER_RE.fullmatch(name) or not 1 <= len(version) <= 64:
                raise Failure("Unexpected PostgreSQL inventory output.")
            result.append({"name": name, "version": version})
        return result

    def row_counts(self, database):
        """$defs.row_counts of ``database``: one read-only statement over every user table."""
        pairs = []
        for line in self.sql(database, ROW_COUNTS_SQL).splitlines():
            if not line:
                continue
            name, separator, count = line.partition("|")
            if not separator or not ROW_NAME_RE.fullmatch(name) or not re.fullmatch(r"[0-9]+", count):
                raise Failure("Unexpected PostgreSQL inventory output.")
            pairs.append([name, int(count)])
        pairs.sort()
        return {"tables": len(pairs), "total_rows": sum(count for _, count in pairs),
                "sha256": pf_instance.sha256_bytes(pf_instance.normalize_json(pairs))}

    def role_inventory(self):
        """Role evidence (no password, no hash); never executed at restore (OD-16-28)."""
        roles = []
        for line in self.sql("postgres", ROLES_SQL).splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) != 7 or not PG_IDENTIFIER_RE.fullmatch(parts[0]) or any(item not in ("t", "f")
                                                                                   for item in parts[1:]):
                raise Failure("Unexpected PostgreSQL inventory output.")
            flags = [item == "t" for item in parts[1:]]
            roles.append(dict(zip(("name", "superuser", "create_role", "create_db", "login", "replication",
                                   "bypass_rls"), [parts[0]] + flags)))
        return roles

    def available_extensions(self):
        names = set()
        for line in self.sql("postgres", AVAILABLE_EXTENSIONS_SQL).splitlines():
            if not line:
                continue
            if not PG_IDENTIFIER_RE.fullmatch(line):
                raise Failure("Unexpected PostgreSQL inventory output.")
            names.add(line)
        return names

    @contextlib.contextmanager
    def connection_window(self, database, allow_connections):
        """A store that refuses connections (``pf_keep_*``) is opened for the queries and dumps inside the block and
        closed again in ``finally`` (both changes are journaled mutations)."""
        if allow_connections:
            yield
            return
        self.sql("postgres", f"ALTER DATABASE {quote_identifier(database)} ALLOW_CONNECTIONS true;", mutation=True)
        try:
            yield
        finally:
            self.sql("postgres", f"ALTER DATABASE {quote_identifier(database)} ALLOW_CONNECTIONS false;",
                     mutation=True)

    def store_facts(self, row, *, counts):
        """Extensions, heads and (writers stopped) row counts of one connectable store."""
        name = row["name"]
        return dict(row, extensions=self.extensions(name), heads=self.db_heads(name),
                    row_counts=self.row_counts(name) if counts else None)

    def database_facts(self, databases, *, counts=False):
        """Section 3.8 facts of ``databases`` (window-aware: a non-connectable store is opened for its queries)."""
        rows = self.database_rows()
        facts = []
        for name in databases:
            row = rows.get(name)
            if row is None:
                raise Failure(f"Unexpected PostgreSQL inventory output: database {name} is not listed.")
            with self.connection_window(name, row["allow_connections"]):
                facts.append(self.store_facts(row, counts=counts))
        return facts

    @staticmethod
    def store_record(facts, *, role, dump, listing, group):
        """$defs.store of one captured database."""
        return {"store_id": "postgresql:" + facts["name"], "kind": "postgresql_logical",
                "strategy": dict(pf_config.STRATEGY), "database": facts["name"], "role": role,
                "allow_connections": facts["allow_connections"], "owner": facts["owner"],
                "encoding": facts["encoding"], "collate": facts["collate"], "ctype": facts["ctype"],
                "extensions": facts["extensions"], "alembic_heads": sorted(set(facts["heads"])),
                "row_counts": facts["row_counts"], "dump": dump, "list": listing, "consistency_group": group}

    def dump_store(self, database, folder, relative):
        """``pg_dump --format=custom`` of ``database`` to ``<relative>.partial`` then ``relative`` (non-empty)."""
        target = Path(folder) / relative
        partial = target.with_name(target.name + ".partial")
        with partial.open("xb") as stream:
            self.database_program("pg_dump", "-d", database, "--format=custom", "--no-owner", "--no-privileges",
                                  output=stream)
        if not partial.stat().st_size:
            raise Failure("The database dump is empty; checkpoint is incomplete.")
        os.replace(str(partial), str(target))
        return target

    def write_dump_list(self, dump, listing):
        """``pg_restore --list`` of a dump into ``listing`` (the store's list payload)."""
        with Path(dump).open("rb") as stream, Path(listing).open("xb") as output:
            self.compose("exec", "-T", "db", "pg_restore", "--list", input_file=stream, output=output)
        if not Path(listing).stat().st_size:
            raise Failure("The database dump list is empty; the dump is unreadable.")

    # ------------------------------------------- captures (PF-A3.1 section 3.4)

    def observe_contract(self):
        """Read-only (no raise on mismatch): live heads, the running backend image, its image contract (on a retained
        tag of that ID) and, with a valid deployment record, the record's backend image (section 3.4)."""
        observation = {"live_heads": self.db_heads(), "image_heads": None, "files": None, "backend_image_id": None,
                       "expected_backend_image_id": None, "matches": False, "kind": "schema-image-mismatch",
                       "detail": ""}
        view = self.current_deployment()
        if view is not None and view.mismatch is None:
            observation["expected_backend_image_id"] = view.record["images"]["backend"]["id"]
        try:
            observation["backend_image_id"] = self.inspect("backend")["Image"]
        except DaemonFailure:
            raise
        except Failure as exc:
            observation["detail"] = "the backend image cannot be identified: " + str(exc).splitlines()[0]
            return observation
        try:
            images = self.retain_images(utc().lower() + "-observe-" + uuid.uuid4().hex[:6])
            override = self.state / "inspect-images.yaml"
            self.make_override(images, override)
            contract = self.image_contract(override=override)
            observation["image_heads"] = sorted(set(contract["heads"]))
            observation["files"] = dict(contract["files"])
        except DaemonFailure:
            raise
        except (Failure, ValueError, KeyError, TypeError) as exc:
            observation["detail"] = "the image contract cannot be read: " + (str(exc).splitlines() or ["?"])[0]
            return observation
        if observation["image_heads"] != observation["live_heads"]:
            observation["detail"] = (f"live Alembic heads {','.join(observation['live_heads']) or 'none'} differ from "
                                     f"the image's {','.join(observation['image_heads']) or 'none'}")
        elif observation["expected_backend_image_id"] not in (None, observation["backend_image_id"]):
            observation.update(kind="deployment-image-mismatch",
                               detail="the running backend image is not the deployment record's")
        else:
            observation.update(matches=True, kind=None)
        return observation

    def deployment_image_mismatch(self, service, running, view, *, paused):
        head = (f"deployment-image-mismatch: the running {service} image {running[7:19]} is not deployment "
                f"{view.deployment_id}'s {view.record['images'][service]['id'][7:19]}")
        if paused:
            return Failure(head + f"; the checkpoint was not created. Application services are stopped; 'pf --instance "
                           f"{self.context.slug} resume' reopens the unchanged deployment.")
        return Failure(head + ". A healthy checkpoint would bind the wrong images. Nothing was changed.")

    def workspace_archive_limit(self, detail, *, changed):
        slug = self.context.slug
        return Failure(
            f"workspace-archive-limit: the editable workspace differs from the deployed source and cannot be archived "
            f"within the archive limits ({detail}). Move the oversized or deeply nested files out of the repository "
            f"workspace and retry; 'pf --instance {slug} backup --emergency' preserves the database and records the "
            "workspace as excluded." + ("" if changed else " Nothing was changed."))

    def workspace_archive_preflight(self, view):
        """Read-only (audit AF-6): a capture that a drifted workspace would make refuse on an archive limit is refused
        before any confirmation or pause, so the application keeps running."""
        valid = view is not None and view.mismatch is None
        commit = view.record["source"]["commit"] if valid else self.deployed_commit()
        workspace = self.workspace_status()
        if not (workspace["dirty"] or (workspace["head"] is not None and workspace["head"] != commit)):
            return
        try:
            problem = pf_source.tree_limit_problem(self.root, excludes=SOURCE_EXCLUDES)
        except (pf_source.SourceError, OSError) as exc:
            raise Failure("Source backup refuses this workspace: " + str(exc)) from exc
        if problem is not None:
            raise self.workspace_archive_limit(problem, changed=False)

    def capture_preflight(self, kind):
        """Read-only, before any confirmation, pause or effect of `pf backup`, update, reset-db, purge and rollback
        (section 3.4): the deployment image binding and the provable deployed source of a healthy capture."""
        view = self.current_deployment()
        self.workspace_archive_preflight(view)
        if view is not None and view.mismatch is None:
            for service in pf_docker.BUILT_SERVICES:
                try:
                    running = self.inspect(service)["Image"]
                except DaemonFailure:
                    raise
                except Failure:
                    continue  # an absent container is refused by the capture itself
                if running != view.record["images"][service]["id"] and kind != "rollback":
                    raise self.deployment_image_mismatch(service, running, view, paused=False)
            return
        if kind == "rollback":
            return  # preserve_current falls back to an emergency preservation
        origin = self.deployed_source_origin(self.deployed_commit())
        if view is not None:
            if origin is None:
                raise Failure(f"deployment-artifact-mismatch: {view.deployment_id}: {view.mismatch.split(':', 1)[0]} "
                              "differs from the deployment record, and the deployed source cannot be proven from the "
                              "protected source store or the workspace either. Keep the folder as evidence; see "
                              "SYNOLOGY_ADMIN §16. Nothing was changed.")
            label = "protected source store" if origin == "protected-store" else "proven workspace"
            log(f"note: deployment-artifact-mismatch: {view.deployment_id}: {view.mismatch.split(':', 1)[0]} differs "
                f"from the deployment record; it is kept as evidence and not used. The checkpoint takes the source "
                f"from the {label}.")
            return
        if origin is None:
            raise Failure(self.CANNOT_RECONSTRUCT)

    def observe_quiescence(self):
        """$defs.quiescence: writers are stopped when neither backend nor frontend runs (missing = absent)."""
        states = {}
        for service in pf_docker.BUILT_SERVICES:
            try:
                states[service] = "running" if self.inspect(service)["State"].get("Running") else "stopped"
            except DaemonFailure:
                raise
            except Failure:
                states[service] = "absent"
        mode = "writers_stopped" if "running" not in states.values() else "single_store_snapshot"
        return {"mode": mode, "observed_at": utc(), "backend": states["backend"], "frontend": states["frontend"]}

    def retain_image(self, service, backup_id):
        """Tag the running ``service`` image for this bundle and return its $defs.image identity."""
        image_id = self.inspect(service)["Image"]
        reference = f"{self.config['project']}-{service}:backup-{backup_id.lower()}"
        self.docker("tag", image_id, reference)
        self.created_image_refs.append(reference)
        identity = self.image_identity(reference, reference=reference)
        if identity["id"] != image_id:
            raise Failure(f"The retained {service} tag {reference} does not name the running image.")
        return identity

    def _capture_images(self, backup_id, *, healthy, deployment, exclusions):
        """Images of a capture: backend/frontend tagged, db identified (not tagged); returns (images, binding)."""
        images = {}
        for service in pf_docker.BUILT_SERVICES:
            try:
                images[service] = self.retain_image(service, backup_id)
            except DaemonFailure:
                raise
            except (Failure, ValueError, KeyError, IndexError) as exc:
                if healthy:
                    raise
                images[service] = None
                exclusions.append({"item": "image:" + service, "reason": "the running image could not be identified: "
                                   + (str(exc).splitlines() or ["?"])[0][:300]})
        db_id = self.inspect("db")["Image"]
        try:
            images["db"] = self.image_identity(db_id, reference=pf_docker.DB_IMAGE)
        except DaemonFailure:
            raise
        except (Failure, ValueError, KeyError, IndexError) as exc:
            if healthy:
                raise
            images["db"] = None
            exclusions.append({"item": "image:db", "reason": "the database image could not be identified: "
                               + (str(exc).splitlines() or ["?"])[0][:300]})
        binding = None
        if deployment is not None:
            for service in pf_docker.BUILT_SERVICES:
                image = images[service]
                if image is not None and image["id"] != deployment.record["images"][service]["id"]:
                    if healthy:
                        raise self.deployment_image_mismatch(service, image["id"], deployment, paused=True)
                    binding = binding or (service, image["id"])
        return images, binding, db_id

    def _capture_source(self, folder, *, healthy, deployment, mismatch_view, unsupported, exclusions):
        """The source payload of a capture (section 3.4 step 4): (source section, payload entry or None)."""
        target = folder / "source.tar.gz"
        if deployment is not None:
            record = deployment.record["source"]
            try:
                copy_fresh(deployment.folder / "source.tar.gz", target)
                entry = self._archive_payload(folder, "source.tar.gz", "source_archive",
                                              expected=(record["archive"]["size"], record["archive"]["sha256"]))
                return ({"provenance": record["provenance"], "commit": record["commit"], "remote": record["remote"],
                         "origin": "deployment-artifact", "payload": "source.tar.gz",
                         "entries_sha256": record["manifest"]["entries_sha256"]}, entry)
            except (Failure, OSError, pf_source.SourceError) as exc:
                if healthy:
                    raise Failure("The deployment artifact could not be copied: " + str(exc).splitlines()[0]) from exc
                self._discard(target)
        commit = self.deployed_commit() if deployment is None else None
        try:
            result = self.create_deployed_source_archive(target, commit)
        except DaemonFailure:
            raise
        except Failure:
            if healthy:
                raise
            self._discard(target)
            result = None
        if result is not None:
            if mismatch_view is not None:
                reason = (f"deployment {mismatch_view.deployment_id}: {mismatch_view.mismatch.split(':', 1)[0]} "
                          "differs from its record; kept as evidence")
                exclusions.append({"item": "deployment-record", "reason": reason[:500]})
            elif deployment is None:
                exclusions.append({"item": "deployment-record",
                                   "reason": "the deployment predates PF-A3.1 or its seal failed; no record exists"})
            entry = self._archive_payload(folder, "source.tar.gz", "source_archive", archive=result)
            return ({"provenance": "git_commit", "commit": commit, "remote": result["manifest"]["source"]["remote"],
                     "origin": result["origin"], "payload": "source.tar.gz",
                     "entries_sha256": pf_source.entries_digest(result["manifest"])}, entry)
        exclusions.append({"item": "deployment-record", "reason": "no verified deployment record bound the source"})
        try:
            archived = pf_source.archive_tree(self.root, target, excludes=SOURCE_EXCLUDES, unsupported=unsupported)
        except pf_source.SourceError as exc:
            if unsupported == "refuse" and "unsupported entry" in str(exc):
                raise Failure("the workspace holds a link or special file that a source replacement would destroy ("
                              + str(exc) + ")") from exc
            exclusions.append({"item": "source", "reason": "neither the deployed source nor the workspace could be "
                               "archived: " + str(exc)[:300]})
            return ({"provenance": "unknown", "commit": None, "remote": None, "origin": "none", "payload": None,
                     "entries_sha256": None}, None)
        entry = self._archive_payload(folder, "source.tar.gz", "source_archive", archive=archived)
        return ({"provenance": "unknown", "commit": None, "remote": None, "origin": "workspace-unverified",
                 "payload": "source.tar.gz", "entries_sha256": pf_source.entries_digest(archived["manifest"])},
                dict(entry, unsupported=archived["unsupported"]))

    @staticmethod
    def _discard(path):
        try:
            os.unlink(str(path))
        except FileNotFoundError:
            pass

    def _archive_payload(self, folder, path, kind, *, archive=None, expected=None):
        """$defs.payload of a tar.gz pf extracts: size, hash and the pass 1 member inventory of its bytes."""
        fd = os.open(str(Path(folder) / path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            size, sha256 = os.fstat(fd).st_size, self._fd_sha256(fd)
            if expected is not None and (size, sha256) != tuple(expected):
                raise Failure(f"{path} does not re-hash to its source after the copy")
            if archive is None:
                inventory = pf_source.inspect_archive(fd, limits=pf_source.SOURCE_LIMITS)
                counts = (inventory.expanded_bytes, len(inventory.members), inventory.members_sha256)
            else:
                counts = (archive["expanded_bytes"], archive["members"], archive["members_sha256"])
        finally:
            os.close(fd)
        return {"path": path, "type": kind, "size": size, "sha256": sha256, "store": None, "sensitive": False,
                "expanded_bytes": counts[0], "members": counts[1], "members_sha256": counts[2]}

    @staticmethod
    def file_payload(folder, path, kind, *, store=None, sensitive=False):
        """$defs.payload of a file pf never extracts (dumps, lists, globals, images, configuration, state)."""
        target = Path(folder) / path
        return {"path": path, "type": kind, "size": target.stat().st_size, "sha256": digest(target), "store": store,
                "sensitive": sensitive, "expanded_bytes": None, "members": None, "members_sha256": None}

    def _record_capture(self, view, record, mismatch):
        if self.operation_dir is None:
            return
        path = self.operation_dir / "captures.json"
        entries = []
        if os.path.lexists(str(path)):
            entries = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(path), label=str(path))
        entries.append({"bundle_id": view.bundle_id, "bundle_kind": view.bundle_kind,
                        "capture_class": view.capture_class, "manifest_sha256": view.manifest_sha256,
                        "verification_id": record["verification_id"] if record else None,
                        "level": record["level"] if record else None, "result": record["result"] if record else None,
                        "mismatch": mismatch})
        self.write_private_json("captures.json", entries)

    def write_manifest(self, folder, manifest):
        """Seal a bundle: exactly normalize_json(manifest) (no newline) and its manifest.sha256, both fsynced."""
        problems = lifecycle_errors(manifest, "recovery_manifest")
        if problems:
            raise Failure(f"Internal error: the {manifest['bundle_kind']} manifest of {manifest['bundle_id']} is "
                          f"invalid ({len(problems)} problem(s): {'; '.join(problems[:3])}); nothing was sealed.")
        data = pf_instance.normalize_json(manifest)
        pf_instance._write_private_file(Path(folder) / "manifest.json", data, 0o600)
        sha256 = pf_instance.sha256_bytes(data)
        pf_instance._write_private_file(Path(folder) / "manifest.sha256", (sha256 + "\n").encode("ascii"), 0o600)
        return sha256

    def _capture(self, reason, *, capture_class, observation=None, unsupported="refuse", bundle_id=None,
                 verify_name=None, verify=True):
        """The common capture body (section 3.4 steps 1-9) of a checkpoint: healthy, emergency or partial. PF-A3.2:
        ``bundle_id``/``verify_name`` are the plan's pre-assigned IDs; ``verify=False`` (the backup kind) stops at the
        sealed manifest and verify_capture() runs step 8 as its own effect."""
        if self.operation_dir is None:
            raise Failure("Internal error: a capture runs inside a locked operation.")
        healthy = capture_class == "healthy_checkpoint"
        self.free_space()
        view = self.current_deployment()
        deployment = view if view is not None and view.mismatch is None else None
        mismatch_view = view if view is not None and view.mismatch is not None else None
        commit = deployment.record["source"]["commit"] if deployment is not None else self.deployed_commit()
        backup_id = bundle_id or f"{utc()}-{(commit or '0' * 40)[:12]}-{uuid.uuid4().hex[:6]}"
        self.ensure_backup_tree()
        folder = self.backups_dir / backup_id
        folder.mkdir(mode=0o700)
        log(("Creating deployed-source + database checkpoint: " if healthy else
             "Creating emergency preservation: ") + backup_id)
        major = self.database_ready()
        server = self.server_version_num()
        quiescence = self.observe_quiescence()
        exclusions = []
        images, binding, db_id = self._capture_images(backup_id, healthy=healthy, deployment=deployment,
                                                      exclusions=exclusions)
        live_heads = sorted(set(self.db_heads()))
        mismatch = None
        if healthy:
            contract = self.ensure_local_contract({service: images[service] for service in pf_docker.BUILT_SERVICES})
            image_heads, files = sorted(set(contract["heads"])), dict(contract["files"])
        else:
            observation = observation or self.observe_contract()
            image_heads, files = observation["image_heads"], dict(observation["files"] or {})
            if not observation["matches"] or binding is not None:
                kind = observation["kind"] if not observation["matches"] else "deployment-image-mismatch"
                detail = observation["detail"] if not observation["matches"] else \
                    f"the running {binding[0]} image is not the deployment record's"
                mismatch = {"kind": kind, "live_heads": live_heads, "image_heads": image_heads,
                            "backend_image_id": observation["backend_image_id"]
                            if re.fullmatch(r"sha256:[0-9a-f]{64}", observation["backend_image_id"] or "") else None,
                            "expected_backend_image_id": observation["expected_backend_image_id"],
                            "detail": detail[:500]}
            if not files:
                exclusions.append({"item": "migration-files", "reason": "the image contract could not be read"})
        source, source_entry = self._capture_source(folder, healthy=healthy, deployment=deployment,
                                                    mismatch_view=mismatch_view, unsupported=unsupported,
                                                    exclusions=exclusions)
        payloads = []
        unsupported_entries = []
        if source_entry is not None:
            unsupported_entries = list(source_entry.pop("unsupported", []))
            payloads.append(source_entry)
        workspace = self.workspace_status()
        drift = bool(workspace["dirty"] or (workspace["head"] is not None and workspace["head"] != commit))
        workspace_payload = None
        if drift and source["origin"] != "workspace-unverified":
            try:
                archived = pf_source.archive_tree(self.root, folder / "workspace.tar.gz", excludes=SOURCE_EXCLUDES,
                                                  unsupported=unsupported)
            except pf_source.ArchiveLimitExceeded as exc:
                if unsupported != "record":
                    raise self.workspace_archive_limit(str(exc), changed=True) from exc
                # Audit AF-6: `pf backup --emergency` (no source replacement follows) preserves the data anyway and
                # records the workspace it could not archive.
                archived = None
                exclusions.append({"item": "workspace", "reason": ("the drifted workspace exceeds the archive "
                                                                   "limits and was not archived: " + str(exc))[:500]})
                log("WARNING: the writable repository differs from the deployed revision but exceeds the archive "
                    "limits (" + str(exc) + "); it was not archived and is recorded as excluded.")
            except pf_source.SourceError as exc:
                raise Failure("Source backup refuses this workspace: " + str(exc)) from exc
            if archived is not None:
                unsupported_entries += archived["unsupported"]
                payloads.append(self._archive_payload(folder, "workspace.tar.gz", "workspace_archive",
                                                      archive=archived))
                workspace_payload = "workspace.tar.gz"
                log("Writable repository differs from the deployed revision; current workspace was archived "
                    "separately.")
        database = self.env()["POSTGRES_DB"]
        dump = self.dump_store(database, folder, "database.dump")
        self.write_dump_list(dump, folder / "database.list")
        facts = self.database_facts([database], counts=quiescence["mode"] == "writers_stopped")[0]
        store = self.store_record(facts, role="active", dump="database.dump", listing="database.list",
                                  group="active")
        payloads += [self.file_payload(folder, "database.dump", "database_dump", store=store["store_id"]),
                     self.file_payload(folder, "database.list", "database_list", store=store["store_id"])]
        exclusions += [{"item": "bind-mounts", "reason": "never captured (no bind paths in this profile)"},
                       {"item": "retained-databases", "reason": "a checkpoint captures the active database only; "
                        "retained pf_keep_* databases are captured by a purge bundle"},
                       {"item": "external-databases", "reason": "databases outside the db service are not captured"}]
        if not healthy and exclusions and {item["item"] for item in exclusions} & pf_config.REQUIRED_ARTIFACT_ITEMS:
            capture_class = "partial"
        extension_names = ", ".join(item["name"] for item in store["extensions"]) or "none"
        manifest = {
            "schema_version": 1, "bundle_id": backup_id, "bundle_kind": "checkpoint", "created_at": utc(),
            "reason": reason, "capture_class": capture_class,
            "source_instance": self.source_instance(), "producer": self.producer(), "quiescence": quiescence,
            "source": source,
            "deployment": {"deployment_id": deployment.deployment_id, "record_sha256": deployment.record_sha256}
            if deployment is not None and source["origin"] == "deployment-artifact" else None,
            "images": images, "postgresql": {"server_version_num": server, "major": major, "image_id": db_id},
            "roles": self.role_inventory(), "stores": [store],
            "consistency_groups": [{"group_id": "active", "stores": [store["store_id"]],
                                    "claim": "transactional-single-store"}],
            "compatibility": {"alembic_heads_live": live_heads, "alembic_heads_image": image_heads,
                              "migration_files": files, "mismatch": mismatch},
            "payloads": sorted(payloads, key=lambda item: item["path"]),
            "workspace": {"differs_from_deployed": drift, "payload": workspace_payload,
                          "unsupported_entries": unsupported_entries[:200]},
            "exclusions": exclusions,
            "manual_prerequisites": [f"PostgreSQL major {major} server with extensions: {extension_names}"],
            "derived_from": None, "purge": None, "legacy": None,
        }
        manifest_sha256 = self.write_manifest(folder, manifest)
        sealed = BundleView(folder, manifest, manifest_sha256, None, "captured")
        if not verify:
            self._record_capture(sealed, None, mismatch["kind"] if mismatch else None)
            log(f"Checkpoint {backup_id}: {capture_class}, sealed (verification follows)")
            return sealed
        return self.verify_capture(sealed, verify_name)

    def verify_capture(self, view, verify_name):
        """Step 8 of a capture and the publish (PF-A3.2: the backup kind's own verification effect): every store
        restored from the bundle's own dump into the named ``pf_verify_*`` candidate, the data_restore_verified record,
        then the backups targets of the fresh checkpoint."""
        folder = view.folder
        mismatch = view.manifest["compatibility"]["mismatch"]
        dumps = {store["store_id"]: folder / store["dump"] for store in view.manifest["stores"]}
        record, passed = self.verify_bundle(view, dumps, started_at=view.manifest["created_at"],
                                            names=[verify_name] if verify_name else None)
        self._record_capture(view, record, mismatch["kind"] if mismatch else None)
        if not passed:
            failed = next(check for check in record["checks"] if check["result"] == "failed")
            exc = Failure(f"Verification of {view.bundle_id} failed ({failed['name']}: {failed['detail']}). "
                          "Verification database retained.")
            exc.code = "verification-failed"
            raise exc
        # PF-A2.3: explicit backups targets for the fresh checkpoint only, verified after the change.
        self.publish_fresh("backups", folder)
        result = dataclasses.replace(view, level=record["level"], latest_verification_id=record["verification_id"])
        log(f"Checkpoint {view.bundle_id}: {view.capture_class}, {record['level']}")
        return result

    def source_instance(self):
        return {"instance_id": self.context.instance_id, "slug": self.context.slug,
                "compose_project": self.context.compose_project, "environment": self.config["environment"],
                "repository": self.config["repository"], "workspace": str(self.root)}

    def verify_store(self, store, dump, candidate, *, compatibility=False, drop=True):
        """Restore one store's dump into ``candidate`` (created with the store's locale) and check heads, locale and
        row counts (section 3.5/3.8): (checks, passed, candidate created). The candidate is dropped when ``drop`` and
        every check passed; otherwise it is retained (evidence) and the caller decides."""
        store_id = store["store_id"]
        checks = []

        def check(name, result, detail=""):
            checks.append({"name": f"{name}:{store_id}", "result": result, "detail": detail[:500]})

        if compatibility:
            owner, user = store["owner"], self.env()["POSTGRES_USER"]
            if owner is not None and owner != user:
                check("owner", "failed", f"owner {owner} is not the frozen POSTGRES_USER {user}")
            else:
                check("owner", "passed")
            available = self.available_extensions()
            missing = [item["name"] for item in store["extensions"] if item["name"] not in available]
            check("extensions", "failed" if missing else "passed",
                  f"extension {missing[0]} is not available on this server" if missing else "")
            if missing or (owner is not None and owner != user):
                return self._remaining(checks, store_id, "not reached: compatibility failed"), False, False
        locale = (store["encoding"], store["collate"], store["ctype"])
        locale = None if None in locale else locale
        try:
            self.create_database(candidate, locale=locale)
        except DaemonFailure:
            raise
        except Failure as exc:
            detail = (f"locale {store['collate']}/{store['ctype']} is not available on this server" if locale
                      else (str(exc).splitlines() or ["createdb failed"])[0])
            check("restore", "failed", detail)
            return self._remaining(checks, store_id, "not reached: the candidate could not be created"), False, False
        try:
            fd = os.open(str(dump), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "rb") as stream:
                self.database_program("pg_restore", "-d", candidate, "--exit-on-error", "--no-owner",
                                      "--no-privileges", input_file=stream)
            check("restore", "passed")
        except DaemonFailure:
            raise
        except (Failure, OSError) as exc:
            check("restore", "failed", (str(exc).splitlines() or ["pg_restore failed"])[0])
            return self._remaining(checks, store_id, "not reached: the restore failed"), False, True
        heads = sorted(set(self.db_heads(candidate)))
        check("heads", "passed" if heads == store["alembic_heads"] else "failed",
              "" if heads == store["alembic_heads"] else
              f"restored heads {','.join(heads) or 'none'} vs captured {','.join(store['alembic_heads']) or 'none'}")
        if locale is None:
            check("locale", "not_run", "the store recorded no locale (legacy)")
        else:
            row = self.database_rows().get(candidate) or {}
            differing = [field for field, value in zip(("encoding", "collate", "ctype"), locale)
                         if row.get(field) != value]
            check("locale", "failed" if differing else "passed",
                  f"{differing[0]} differs ({store[differing[0]]} vs {row.get(differing[0])})" if differing else "")
        if store["row_counts"] is None:
            check("rows", "not_run", "no row counts were recorded (writers were not stopped)")
        else:
            counts = self.row_counts(candidate)
            expected = store["row_counts"]
            check("rows", "passed" if counts == expected else "failed", "" if counts == expected else
                  f"row counts differ ({expected['tables']}/{expected['total_rows']} vs "
                  f"{counts['tables']}/{counts['total_rows']})")
        passed = all(item["result"] != "failed" for item in checks)
        if passed and drop:
            self.drop_database(candidate)
            checks.append({"name": f"drop:{candidate}", "result": "passed", "detail": ""})
        return checks, passed, True

    @staticmethod
    def _remaining(checks, store_id, detail):
        present = {item["name"] for item in checks}
        for name in ("restore", "heads", "locale", "rows"):
            if f"{name}:{store_id}" not in present:
                checks.append({"name": f"{name}:{store_id}", "result": "failed", "detail": detail})
        return checks

    def verify_bundle(self, view, dumps, *, started_at, names=None):
        """Restore every store of ``view`` from its own dump (``dumps``: {store_id: path}) into a fresh
        ``pf_verify_*`` candidate (PF-A3.2: ``names``, the pre-assigned candidates, in store order), then write the
        data_restore_verified record bound to the manifest hash."""
        checks, used, passed = [], [], True
        planned = list(names or [])
        for index, store in enumerate(view.manifest["stores"]):
            candidate = planned[index] if index < len(planned) else "pf_verify_" + uuid.uuid4().hex[:20]
            used.append(candidate)
            store_checks, ok, _ = self.verify_store(store, dumps[store["store_id"]], candidate)
            checks += store_checks
            if not ok:
                passed = False
                break
        record = self.write_verification(view, level="data_restore_verified", result="passed" if passed else "failed",
                                         target={"kind": "isolated-database", "names": used, "removed": passed},
                                         checks=checks, started_at=started_at)
        return record, passed

    def write_verification(self, view, *, level, result, target, checks, started_at, environment=None):
        """An external VerificationRecord bound to ``view``'s exact manifest hash (section 3.5); the bundle and its
        manifest are never edited. PF-A3.3: data_restore_verified and functional_recovery_verified records (never
        ``captured``); ``environment`` the isolated server's values (None: the A3.1 live read)."""
        if level == "captured":
            raise Failure("Internal error: a verification record is never written with level captured.")
        if environment is None:
            environment = {"server_version_num": self.server_version_num(), "engine_id": self.context.daemon.engine_id,
                           "compose_version": self.compose_version}
        record = {
            "schema_version": 1, "verification_id": f"ver-{utc()}-{uuid.uuid4().hex[:8]}",
            "bundle_id": view.bundle_id, "bundle_kind": view.bundle_kind, "manifest_sha256": view.manifest_sha256,
            "level": level, "result": result, "target": target, "checks": checks, "producer": self.producer(),
            "strategy": dict(pf_config.STRATEGY), "environment": environment,
            "operation_id": self.operation_id, "started_at": started_at, "finished_at": utc(),
        }
        problems = lifecycle_errors(record, "verification_record")
        if problems:
            raise Failure(f"Internal error: the verification record of {view.bundle_id} is invalid ({problems[0]}).")
        directory = self.private_dir(self.private_dir(self.verifications_dir) / view.bundle_id)
        pf_instance._write_private_file(directory / (record["verification_id"] + ".json"),
                                        pf_instance.normalize_json(record), 0o600)
        return record

    def snapshot(self, reason, *, bundle_id=None, verify_name=None):
        """A healthy checkpoint (name kept; section 3.4): today's equality gate, the exact deployed source and the
        deployment image binding; returns the sealed BundleView with its data_restore_verified level. PF-A3.2: the
        plan's pre-assigned bundle ID and verification candidate."""
        return self._capture(reason, capture_class="healthy_checkpoint", bundle_id=bundle_id, verify_name=verify_name)

    def capture_emergency(self, reason="emergency-manual", *, observation=None, bundle_id=None, verify_name=None):
        """Emergency preservation (section 3.4): the actual database, images and source with mismatch evidence and
        no schema/image equality gate. `pf backup --emergency` (reason emergency-manual) shows the observed contract
        and asks one typed confirmation first; a link in the workspace is then recorded instead of refused."""
        manual = reason == "emergency-manual"
        if manual:
            self.database_ready()
        observation = observation or self.observe_contract()
        if manual:
            log("Observed contract: live heads " + (",".join(observation["live_heads"]) or "none") + " | image heads "
                + (",".join(observation["image_heads"] or []) or "unknown") + " | running backend image "
                + (observation["backend_image_id"] or "unknown") + " | deployment backend image "
                + (observation["expected_backend_image_id"] or "not recorded") + " | match: "
                + ("yes" if observation["matches"] else "no (" + observation["detail"] + ")"))
            confirm("EMERGENCY BACKUP " + self.context.compose_project,
                    "Capture the current database, images and source as emergency preservation. Nothing is "
                    "switched, restored or replaced; the capture is evidence and data for repair or export, never a "
                    "rollback target.")
        view = self._capture(reason, capture_class="emergency_preservation", observation=observation,
                             unsupported="record" if manual else "refuse", bundle_id=bundle_id,
                             verify_name=verify_name)
        if manual:
            log(f"Emergency preservation {view.bundle_id} captured ({view.level}). It is evidence and data for repair "
                "or export, not a rollback target.")
        return view

    def preservation_failed(self, database, detail):
        slug = self.context.slug
        kind = self.plan["kind"] if self.plan is not None else "rollback"
        label = {"reset-db": "the reset", "abort-deploy": "the abort"}.get(kind, "the rollback")
        return Failure(
            f"preservation-failed: the current database {database} could not be preserved ({detail}); {label} did "
            "not restore or switch anything and the current data is unchanged. Application services stay stopped. "
            f"Preserve it manually (a pg_dump of {database} to a protected location) or fix the cause and run "
            f"'pf --instance {slug} backup --emergency', then retry; 'pf --instance {slug} resume' reopens the "
            "unchanged deployment.")

    def preserve_current(self, reason, *, stores, bundle_id=None, verify_name=None, step=None):
        """INV-09 before an overwrite: a healthy checkpoint when the contract holds, else emergency preservation; the
        active store must be sealed with a passed data_restore_verified record, else ``preservation-failed``."""
        database = stores[0] if stores else self.env()["POSTGRES_DB"]
        view = None
        try:
            observation = self.observe_contract()
            if observation["matches"]:
                try:
                    view = self._capture(reason, capture_class="healthy_checkpoint", observation=observation,
                                         bundle_id=bundle_id, verify_name=verify_name)
                except DaemonFailure:
                    raise
                except (Failure, OSError, pf_source.SourceError) as exc:
                    log("note: a healthy checkpoint was not possible (" + (str(exc).splitlines() or ["?"])[0]
                        + "); capturing emergency preservation instead.")
                    # A failed restore test keeps its candidate as evidence: the fallback verifies into its own
                    # candidate, recorded in the effect evidence before it is created.
                    verify_name = "pf_verify_" + uuid.uuid4().hex[:20]
                    if step is not None:
                        step.evidence = (step.evidence or "") + f" verify:{verify_name}"
                        self.journal_update(effects={self._current_effect: ("unknown", None, step.evidence)})
            if view is None:
                if bundle_id is not None and os.path.lexists(str(self.backups_dir / bundle_id)):
                    # The healthy attempt left its folder (never selectable); the fallback needs its own ID.
                    bundle_id = f"{utc()}-{bundle_id.split('-')[1]}-{uuid.uuid4().hex[:6]}"
                view = self.capture_emergency(reason, observation=observation, bundle_id=bundle_id,
                                              verify_name=verify_name)
        except DaemonFailure:
            raise
        except (Failure, OSError, pf_source.SourceError, pf_config.ConfigError) as exc:
            raise self.preservation_failed(database, (str(exc).splitlines() or [type(exc).__name__])[0][:300]) from exc
        covered = {store["database"] for store in view.stores}
        if view.level not in PASSED_LEVELS or not set(stores) <= covered:
            raise self.preservation_failed(database, f"bundle {view.bundle_id} has level {view.level}")
        if view.capture_class != "healthy_checkpoint":
            mismatch = view.manifest["compatibility"]["mismatch"]
            log(f"Current data preserved as {view.capture_class} {view.bundle_id}"
                + (f" ({mismatch['kind']}: {mismatch['detail']})." if mismatch else "."))
        return view

    def compose(self, *args, root=None, override=None, timeout=None, env=None, topology=None, **kwargs):
        """One Compose invocation with frozen inputs: fixed project, files, env-file and directory.

        ``env`` carries only approved per-call value overrides (COMPOSE_VALUE_OVERRIDES), refused
        before any process starts. A mutating verb in ``pf_docker.ENVELOPE_VERBS`` first passes the
        Compose envelope for exactly these effective inputs (PF-A1.3). PF-A3.3: ``topology`` (or the binding of
        ``bound``) executes the validated isolated model instead (section 3.1 step 6).
        """
        topology = topology if topology is not None else self._bound
        if topology is not None:
            return self.topology_compose(topology, *args, timeout=timeout, env=env, root=root, override=override,
                                         **kwargs)
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

    # ------------------------------------------- isolated topology (PF-A3.3 section 3.1)

    def inside(self, label):
        """The test-only crash seam ``inside:<label>`` (section 6); a no-op in production."""
        point = self._crash_point
        if point is None:
            return
        if callable(point):
            point(INSIDE_PREFIX + label, "inside")
            return
        selector, wanted = point
        if wanted == "inside" and selector == label:
            raise SimulatedCrash("inside " + label)

    def isolation_failure(self, detail):
        exc = Failure(f"verification-isolation-unsupported: {detail}; the bundle cannot be verified in an isolated "
                      "topology on this host. Nothing was changed.")
        exc.code = "verification-isolation-unsupported"
        return exc

    def topology_directory(self, project, *, create=False):
        """``<operation>/isolated/<project>/`` (0700)."""
        base = self.operation_dir / "isolated"
        directory = base / project
        if create:
            for path in (base, directory):
                if not real_directory(path):
                    os.mkdir(str(path), 0o700)
                    os.chmod(str(path), 0o700)
        return directory

    def topology_record(self, project):
        """The operation's ``topology.json`` of ``project`` (identities only), or None."""
        path = self.topology_directory(project) / "topology.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise Failure(f"plan-invalid: {path} cannot be read ({exc.strerror or exc}). Nothing was changed.") from exc
        try:
            record = pf_instance.parse_strict_json(data, label=str(path))
        except pf_instance.ContextError as exc:
            raise Failure(f"plan-invalid: {path} is not valid JSON ({exc}). Nothing was changed.") from exc
        if not isinstance(record, dict) or record.get("project") != project:
            raise Failure(f"plan-invalid: {path} names another topology. Nothing was changed.")
        return record

    def write_topology_record(self, project, record):
        pf_instance._write_private_file(self.topology_directory(project, create=True) / "topology.json",
                                        pf_instance.normalize_json(record), 0o600)
        return record

    def update_topology_record(self, project, **changes):
        record = self.topology_record(project)
        if record is None:
            return None
        record.update(changes)
        return self.write_topology_record(project, record)

    def topology_values(self, view):
        """Section 3.1 step 1: the bundle's verified ``config_env`` values (a legacy format 1 bundle: the selected
        instance's frozen values with the active store's database and owner), a generated 48-hex database password and
        the loopback render address. A value with '$' is refused."""
        payloads = view.payloads_of("config_env")
        if payloads:
            data = self.read_small_payload(view, payloads[0]["path"], "config_env")
            try:
                values = dict(pf_config.parse_app_env(data, label=view.bundle_id + " config_env"))
            except pf_config.ConfigError as exc:
                raise self.isolation_failure(f"the bundle's configuration cannot be parsed ({exc})") from exc
        else:
            base = dict(self.frozen.values) if self.frozen is not None else dict(self.load_app_env())
            store = view.active_store
            values = {key: base[key] for key in pf_config.APP_KEYS}
            values["POSTGRES_DB"] = store["database"]
            values["POSTGRES_USER"] = store["owner"] or base["POSTGRES_USER"]
        return self.generated_values(values)

    def generated_values(self, values):
        """The topology values of section 3.1 step 1 from ``values``: '$' refused, a generated 48-hex password, the
        loopback render address (step 3 removes every port), checked like any frozen configuration."""
        values = dict(values)
        for key in pf_config.APP_KEYS:
            if key != "POSTGRES_PASSWORD" and "$" in str(values.get(key, "")):
                raise self.isolation_failure(f"{key} contains '$'")
        values["POSTGRES_PASSWORD"] = secrets.token_hex(24)
        values["PARTFLOW_BIND_IP"] = "127.0.0.1"
        self.redactor.add(values["POSTGRES_PASSWORD"])
        try:
            self.check_app_values(values)
        except Failure as exc:
            raise self.isolation_failure("the topology values are invalid (" + str(exc).splitlines()[0] + ")") from exc
        return values

    def topology_expectation(self, project, topology_uuid, directory, values):
        child = pf_config.child_values(values, workspace=directory, instance_id=topology_uuid)
        expectation = pf_docker.ComposeExpectation(
            project=project, instance_id=topology_uuid, repo_root=str(directory),
            values=types.MappingProxyType({name: values[name] for name in pf_config.APP_KEYS}),
            database_url=child["PARTFLOW_DATABASE_URL"], images=None)
        return expectation, child

    def _read_render(self, argv, directory, *, project, env=None, what):
        """One ``config --format json`` child into an unlinked private temporary file of ``directory``."""
        handle = tempfile.TemporaryFile(dir=str(directory))
        with handle:
            try:
                # Classifier-computed (read-only `config`), as render_compose.
                self.command(argv, env=env, output=handle, timeout=TIMEOUT_DIAGNOSTIC,
                             effect=compose_effect(project, argv[-3:]))
            except DaemonFailure:
                raise
            except Failure as exc:
                raise self.isolation_failure(f"{what} failed ({str(exc).splitlines()[0]})") from exc
            handle.flush()
            handle.seek(0)
            data = handle.read(pf_docker.RENDER_LIMIT + 1)
        if len(data) > pf_docker.RENDER_LIMIT or not data.strip():
            raise self.isolation_failure(f"{what} returned no usable model")
        try:
            return pf_instance.parse_strict_json(data, label="topology render")
        except pf_instance.ContextError as exc:
            raise self.isolation_failure(f"{what} returned invalid JSON") from exc

    def render_topology(self, project, values, *, topology_uuid, directory):
        """Section 3.1 step 2: the installed compose.nas.yaml rendered for the topology's own project, UUID, private
        directory and values (never render_compose: that binds the instance's project and frozen env-file), validated
        by the unchanged envelope rules. ``directory`` already holds ``app.env``. Returns (model, expectation)."""
        expectation, child = self.topology_expectation(project, topology_uuid, directory, values)
        argv = self.compose_cli() + ["--project-directory", str(directory), "--env-file", str(directory / "app.env"),
                                     "-p", project, "-f", str(self.control_dir / "compose.nas.yaml"),
                                     "config", "--format", "json"]
        model = self._read_render(argv, directory, project=project, env=child, what="the topology render")
        try:
            pf_docker.validate_envelope(model, expectation)
        except pf_docker.DockerScopeError as exc:
            raise self.isolation_failure(f"the rendered topology is not the reviewed PartFlow topology ({exc.code})") \
                from exc
        return model, expectation

    def isolated_model(self, project, topology_uuid, directory, values, images):
        """Section 3.1 steps 2-4: render, transform (pf_docker.isolate_model), validate independently."""
        model, expectation = self.render_topology(project, values, topology_uuid=topology_uuid, directory=directory)
        isolated = pf_docker.isolate_model(model, images=images)
        try:
            pf_docker.validate_isolated_model(isolated, expectation, images)
        except pf_docker.DockerScopeError as exc:
            finding = exc.findings[0] if exc.findings else pf_docker.Finding(exc.code, "$", "refused")
            raise self.isolation_failure(f"the isolated model is refused ({finding.code} at {finding.path})") from exc
        return isolated, expectation

    def isolation_preflight(self, view, images, *, values=None):
        """Sections 3.4/3.6, read-only before any confirmation: steps 1-4 with a non-persisting render in a private
        temporary directory removed in ``finally``; nothing outlives the call."""
        values = self.generated_values(values) if values is not None else self.topology_values(view)
        temporary = Path(tempfile.mkdtemp(prefix="isolation-preflight-", dir=str(self.operation_dir)))
        try:
            pf_instance._write_private_file(temporary / "app.env", pf_config.render_app_env(values), 0o600)
            self.isolated_model("pfverify-" + "0" * 12, str(uuid.uuid4()), temporary, values, images)
        finally:
            parent = os.open(str(temporary.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.remove_private_tree_at(parent, temporary.name)
            finally:
                os.close(parent)

    def topology_images(self, view, effect=None):
        """{service: image ID} a topology of ``view`` runs: the bundle's recorded images; a legacy db without a
        recorded image runs as the local postgres:16 ID the plan recorded (``db-image:<id>``)."""
        images = {service: image["id"] for service, image in view.images.items()}
        db = view.image("db")
        if db is not None:
            images["db"] = db["id"]
        elif effect is not None and self.precondition(effect, "db-image"):
            images["db"] = self.precondition(effect, "db-image")
        missing = [service for service in pf_docker.SERVICES if service not in images]
        if missing:
            raise self.isolation_failure(f"legacy bundle {view.bundle_id} has no usable {missing[0]} image")
        return images

    def isolated_topology(self, view, *, project, topology_uuid, purpose, images):
        """Section 3.1 steps 1-5 inside an open operation: the files are written once (``app.env``, ``compose.json``,
        ``topology.json``) and reused on every re-entry when ``compose.json`` re-hashes to the recorded model; then the
        model must render back identically. Returns the IsolatedTopology."""
        directory = self.topology_directory(project, create=True)
        record = self.topology_record(project)
        compose_file = directory / "compose.json"
        if record is not None and os.path.lexists(str(compose_file)):
            if record.get("topology_uuid") != topology_uuid:
                raise Failure(f"plan-invalid: topology {project} records another UUID. Nothing was executed.")
            data = pf_instance.read_bytes_nofollow(compose_file)
            if pf_instance.sha256_bytes(data) != record.get("model_sha256"):
                raise Failure(f"plan-invalid: {compose_file} no longer matches its recorded model hash; the topology is "
                              "kept and nothing was executed.")
            try:
                values = dict(pf_config.parse_app_env(pf_instance.read_bytes_nofollow(directory / "app.env"),
                                                      label="topology app.env"))
            except (OSError, pf_config.ConfigError) as exc:
                raise Failure(f"plan-invalid: the topology values of {project} cannot be read; nothing was executed.") \
                    from exc
            self.redactor.add(values["POSTGRES_PASSWORD"])
            topology = IsolatedTopology(project, topology_uuid, purpose, directory, types.MappingProxyType(values),
                                        types.MappingProxyType(dict(images)), record["model_sha256"])
            expectation, _ = self.topology_expectation(project, topology_uuid, directory, values)
            self.render_back(topology, expectation)
            return topology
        values = self.topology_values(view)
        if os.path.lexists(str(directory / "app.env")):
            os.unlink(str(directory / "app.env"))  # a crash between app.env and compose.json: new values
        pf_instance.write_once(directory, "app.env", pf_config.render_app_env(values))
        model, expectation = self.isolated_model(project, topology_uuid, directory, values, images)
        data = pf_instance.normalize_json(model)
        pf_instance.write_once(directory, "compose.json", data)
        teardowns = list((record or {}).get("teardowns") or [])
        self.write_topology_record(project, {
            "schema_version": 1, "project": project, "topology_uuid": topology_uuid, "purpose": purpose,
            "bundle_id": view.bundle_id, "manifest_sha256": view.manifest_sha256,
            "model_sha256": pf_instance.sha256_bytes(data), "created_at": utc(), "state": "created",
            "container_ids": [], "volume": None, "network": None, "data_checks_sha256": None,
            "teardowns": teardowns, "removed_at": None})
        topology = IsolatedTopology(project, topology_uuid, purpose, directory, types.MappingProxyType(values),
                                    types.MappingProxyType(dict(images)), pf_instance.sha256_bytes(data))
        self.render_back(topology, expectation)
        return topology

    def render_back(self, topology, expectation):
        """Section 3.1 step 5: ``compose -p <p> -f compose.json config`` must validate under step 4 with the literal
        values (the escape calibration of the envelope)."""
        argv = self.compose_cli() + ["-p", topology.project, "-f", str(topology.compose_file), "--project-directory",
                                     str(topology.directory), "config", "--format", "json"]
        model = self._read_render(argv, topology.directory, project=topology.project,
                                  what="the render-back of the isolated model")
        try:
            pf_docker.validate_isolated_model(model, expectation, dict(topology.images))
        except pf_docker.DockerScopeError as exc:
            finding = exc.findings[0] if exc.findings else pf_docker.Finding(exc.code, "$", "refused")
            raise self.isolation_failure(f"the Compose implementation does not reproduce the isolated model "
                                         f"({finding.code} at {finding.path})") from exc

    @contextlib.contextmanager
    def bound(self, topology):
        """Section 3.1 step 6: inside the block every database helper, ``inspect`` and ``wait_health`` reads the
        topology's values and runs against its project; the runner redacts its password. Never nested."""
        if self._bound is not None:
            raise Failure("Internal error: topology bindings are never nested.")
        self.redactor.add(topology.values["POSTGRES_PASSWORD"])
        self._bound = topology
        try:
            yield topology
        finally:
            self._bound = None

    def topology_compose(self, topology, *args, timeout=None, env=None, root=None, override=None, **kwargs):
        """Section 3.1 step 6: ``compose -p <project> -f <compose.json> --project-directory <dir> <verb> ...``; no
        env-file, no instance value, the A1 host environment only."""
        if env or root is not None or override is not None:
            raise Failure("Internal error: a topology Compose call takes no instance inputs.")
        # The descriptor is built from the caller's arguments before the managed run label is added (as compose()).
        effect = kwargs.pop("effect") if "effect" in kwargs else compose_effect(topology.project, args)
        verb = str(args[0]) if args else ""
        if verb == "run":
            args = ("run", "--label", "partflow.admin.project=" + topology.project, *args[1:])
        command = self.compose_cli() + ["-p", topology.project, "-f", str(topology.compose_file),
                                        "--project-directory", str(topology.directory)]
        if timeout is None:
            data_programs = ("pg_dump", "pg_dumpall", "pg_restore", "createdb", "dropdb")
            timeout = TIMEOUT_DATA if verb == "run" or (verb == "exec" and any(str(word) in data_programs
                                                                              for word in args)) else TIMEOUT_COMPOSE
        return self.command(command + [str(word) for word in args], timeout=timeout, effect=effect, **kwargs)

    def require_images_present(self, topology):
        """Section 3.1 step 7: every image ID the topology runs is present (``docker image inspect <id>``)."""
        for service in pf_docker.SERVICES:
            image_id = topology.images[service]
            if not self.image_present(image_id):
                raise self.isolation_failure(f"image {image_id[7:19]} ({service}) is not present")

    def topology_resources(self, project, topology_uuid):
        """The topology's inventory (its own project and UUID label): owned + blocking resources, or []."""
        inventory = self.docker_inventory(scope=(project, topology_uuid))
        return [item for item in inventory.owned + inventory.blockers if item.kind in ("container", "volume", "network")]

    def volume_identity(self, project):
        """(name, CreatedAt) of ``<project>_postgres_data``, or None."""
        name = pf_docker.topology_names(project)["volume"]["postgres_data"]
        try:
            rows = pf_docker.parse_field_lines(self.docker("volume", "inspect", "--format", pf_docker.VOLUME_FIELDS,
                                                           name), kind="volume")
        except DaemonFailure:
            raise
        except (Failure, pf_docker.DockerScopeError):
            return None
        return {"name": rows[0]["name"], "created_at": rows[0]["created_at"]} if rows else None

    def record_topology_resources(self, topology):
        containers, network, _ = self.observe_isolation(topology)
        self.update_topology_record(topology.project, state="running",
                                    container_ids=sorted(item["id"] for item in containers),
                                    volume=self.volume_identity(topology.project),
                                    network=pf_docker.topology_names(topology.project)["network"]["default"])

    def observe_isolation(self, topology):
        """Section 3.1 step 8: the topology's containers and network as the daemon reports them, and the failed
        ``isolation:*`` checks ({check: detail})."""
        ids = [line.strip() for line in self.compose("ps", "-a", "-q", topology=topology).splitlines() if line.strip()]
        containers = []
        if ids:
            containers = pf_docker.parse_isolation_lines(self.docker(
                "container", "inspect", "--format", pf_docker.ISOLATION_CONTAINER_FIELDS, *ids), kind="container")
        network_name = pf_docker.topology_names(topology.project)["network"]["default"]
        try:
            found = pf_docker.parse_isolation_lines(self.docker(
                "network", "inspect", "--format", pf_docker.ISOLATION_NETWORK_FIELDS, network_name), kind="network")
            network = found[0] if found else None
        except DaemonFailure:
            raise
        except (Failure, pf_docker.DockerScopeError):
            network = None
        return containers, network, pf_docker.isolation_findings(containers, network, project=topology.project,
                                                                 topology_uuid=topology.uuid)

    def teardown_topology(self, project, *, final):
        """Section 3.1 step 9: a write-once A1.3 deletion plan of the topology's own project and UUID, recorded in
        ``topology.json`` before its first removal and continued (never re-planned) after an interruption. The final
        teardown also removes ``compose.json`` and ``app.env``. Returns True when done; a blocker, plan drift or an
        unconfirmed removal records the entry ``refused`` and keeps the topology (False)."""
        record = self.topology_record(project)
        if record is None:
            return True
        teardowns = list(record.get("teardowns") or [])
        frozen = next((entry for entry in teardowns if entry.get("state") == "frozen"), None)
        number = frozen["n"] if frozen is not None else len(teardowns) + 1
        name = f"isolated/{project}/deletion-plan-{number}.json"
        try:
            if frozen is not None:
                plan = self.load_frozen_deletion_plan("isolated-topology", frozen["plan_sha256"], name=name,
                                                      instance_id=record["topology_uuid"])
            else:
                inventory = self.docker_inventory(scope=(project, record["topology_uuid"]))
                plan = self.plan_for("isolated-topology", inventory, command="teardown " + project)
                reference = self.write_deletion_plan(plan, name=name, once=True)
                teardowns.append({"n": number, "plan_sha256": reference["sha256"], "state": "frozen"})
                self.update_topology_record(project, teardowns=teardowns, state="tearing-down")
            self.inside("teardown")
            self.execute_deletion_plan(plan, progress=f"isolated/{project}/deletion-progress-{number}.json",
                                       on_item=lambda item: self.inside("teardown-item"))
        except DaemonFailure:
            raise
        except Failure as exc:
            if failure_code(exc) not in ("resource-blocked", "plan-drift", "plan-effect-unconfirmed"):
                raise
            entries = [dict(entry, state="refused") if entry["n"] == number else entry for entry in teardowns]
            if not any(entry["n"] == number for entry in entries):
                entries.append({"n": number, "plan_sha256": None, "state": "refused"})
            self.update_topology_record(project, teardowns=entries, state="stopped")
            log(f"note: isolated-topology-kept: {project}: {str(exc).splitlines()[0][:300]}")
            return False
        entries = [dict(entry, state="done") if entry["n"] == number else entry for entry in teardowns]
        if final:
            fd = os.open(str(self.topology_directory(project)), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                         | os.O_CLOEXEC)
            try:
                for item in ("compose.json", "app.env"):
                    if pf_instance.identity_at(fd, item) is not None:
                        os.unlink(item, dir_fd=fd)
                os.fsync(fd)
            finally:
                os.close(fd)
            self.update_topology_record(project, teardowns=entries, state="removed", removed_at=utc(),
                                        container_ids=[])
        else:
            self.update_topology_record(project, teardowns=entries, state="created", container_ids=[])
        return True

    def new_topology(self, prefix):
        """Section 2.2: a generated project (``pfverify-``/``pfrecover-`` + 12 hex) and topology UUID, refused before
        the confirmation (``topology-name-collision``) when a registered instance uses the project or any container,
        volume or network carries its project label or derived names."""
        project, topology_uuid = prefix + secrets.token_hex(6), str(uuid.uuid4())
        try:
            registry = pf_instance.load_registry(self.context.installation_root)
            owners = [context.slug for _, context, _ in registry.records()
                      if context is not None and context.compose_project == project]
        except pf_instance.ContextError:
            owners = []
        found = None
        if owners:
            found = f"registered instance {owners[0]}"
        else:
            inventory = self.docker_inventory(scope=(project, topology_uuid))
            items = [item for item in inventory.owned + inventory.blockers + inventory.excluded
                     if item.kind in ("container", "volume", "network")]
            if items:
                found = f"{items[0].kind} {self.resource_name(items[0])}"
        if found is not None:
            exc = Failure(f"topology-name-collision: the generated project {project} is already used ({found}). Run the "
                          "command again. Nothing was changed.")
            exc.code = "topology-name-collision"
            raise exc
        return project, topology_uuid

    def kept_topologies(self, index=None):
        """[(project, uuid, operation_id, purpose)] of every isolated topology this instance's operations recorded
        (topology.json not ``removed``) or pre-assigned, whose project + UUID still has Docker resources."""
        index = index if index is not None else self.operation_index()
        found, seen = [], set()
        for entry in index.entries:
            if entry.plan is None:
                continue
            for project, value in self.operation_topologies(entry.plan):
                if (project, value) in seen:
                    continue
                seen.add((project, value))
                if self.topology_resources(project, value):
                    purpose = "recovery target" if pf_docker.RECOVER_PROJECT_RE.fullmatch(project) else "verification"
                    found.append((project, value, entry.operation_id, purpose))
        return found

    def require_no_isolated_topology(self):
        """Section 3.4 preview ``isolated-topology-present``: a topology running this instance's images makes its image
        tags foreign-in-use, so the instance purge refuses before the confirmation."""
        for project, _, operation_id, purpose in self.kept_topologies():
            selector = f" --recovery-target {project}" if purpose == "recovery target" else ""
            exc = Failure(f"isolated-topology-present: {project} ({purpose}, operation {operation_id}) still has Docker "
                          "resources running this instance's images; the instance purge cannot classify its image tags. "
                          f"Remove it first: '{self.pf_command()} cleanup --apply{selector}'. Nothing was changed.")
            exc.code = "isolated-topology-present"
            raise exc

    def operation_topologies(self, plan=None):
        """[(project, uuid)] the plan pre-assigned (``topology:``/``topology-uuid:`` preconditions)."""
        found = []
        for effect in (plan or self.plan)["effects"]:
            project, value = self.precondition(effect, "topology"), self.precondition(effect, "topology-uuid")
            if project and value and (project, value) not in found:
                found.append((project, value))
        return found

    # ------------------------------------------- application invariants (PF-A3.3 section 3.3)

    def app_invariants(self, label, *, topology=None):
        """``app.cli reconcile`` inside the exact image (the instance's, or the topology's): the capability probe,
        then the run when available. Output never leaves the parser; ``app-check-<label>.json`` holds the summary."""
        started = utc()

        def run(argv, accept):
            return self.compose("run", "--rm", "--no-deps", "-T", "backend", *argv, topology=topology,
                                accept_exit=accept, quiet=True)

        capability, result = "unknown", pf_config.ReconcileResult("incomplete", None, "")
        try:
            _, code = run(pf_config.RECONCILE_PROBE_ARGV, (0, 3))
            capability = "available" if code == 0 else "unavailable"
        except DaemonFailure:
            raise
        except Failure:
            capability = "unknown"
        if capability == "unavailable":
            result = pf_config.ReconcileResult("unavailable", 3, "")
        elif capability == "available":
            try:
                stdout, code = run(pf_config.RECONCILE_ARGV, (0, 1, 2))
                result = pf_config.parse_reconcile_report(stdout, code)
            except DaemonFailure:
                raise
            except Failure:
                result = pf_config.ReconcileResult("incomplete", None, "")
        self.write_private_json(f"app-check-{label}.json", {
            "schema_version": 1, "label": label, "started_at": started, "finished_at": utc(),
            "capability": capability, "exit_code": result.exit_code, "outcome": result.outcome,
            "summary": result.summary})
        return result

    def load_app_check(self, label):
        """The ReconcileResult an earlier step of this operation recorded (``app-check-<label>.json``), or None."""
        path = self.operation_dir / f"app-check-{label}.json"
        try:
            record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(path), label=str(path))
        except (OSError, pf_instance.ContextError):
            return None
        if not isinstance(record, dict) or record.get("outcome") not in ("clean", "mismatch", "error", "incomplete",
                                                                        "unavailable"):
            return None
        return pf_config.ReconcileResult(record["outcome"], record.get("exit_code"), str(record.get("summary") or ""))

    # ------------------------------------------- functional verification (PF-A3.3 section 3.2)

    def verification_records(self, bundle_id, manifest_sha256):
        """The valid verification records of ``bundle_id`` bound to ``manifest_sha256`` (read-only)."""
        directory = self.context.artifacts_dir / "verifications" / bundle_id
        found = []
        try:
            names = sorted(os.listdir(str(directory))) if real_directory(directory) else []
        except OSError:
            names = []
        for name in names:
            if ".tmp-" in name:
                continue
            try:
                data = pf_instance.read_bytes_nofollow(directory / name)
                record = pf_instance.parse_strict_json(data, label=name)
            except (OSError, pf_instance.ContextError):
                continue
            if not isinstance(record, dict) or data != pf_instance.normalize_json(record) \
                    or lifecycle_errors(record, "verification_record"):
                continue
            if record["bundle_id"] == bundle_id and record["manifest_sha256"] == manifest_sha256 \
                    and name == record["verification_id"] + ".json":
                found.append(record)
        return sorted(found, key=lambda item: item["verification_id"])

    @staticmethod
    def functional_names(view, topology):
        """The ordered check names of section 2.5 for ``view`` (the per-store data checks, then FUNCTIONAL_CHECKS)."""
        names = ["topology:" + topology.project]
        for store in view.stores:
            names += [f"{prefix}:{store['store_id']}" for prefix in ("owner", "extensions", "restore", "heads",
                                                                      "locale", "rows")]
        names += ["images:archive", "source:archive", "workspace:archive", "history:archive", "state:files",
                  "config:bundle", "deployment:record", "isolation:network", "isolation:listener", "isolation:mounts",
                  "isolation:restart", "images:running", "health:backend", "health:frontend", "heads:runtime",
                  "app-invariants"]
        return names

    def payload_checks(self, view, topology):
        """Section 3.2 step 4 in the isolation-independent order; each check dict ``passed``/``failed``/``not_run``."""
        checks = []

        def add(name, result, detail=""):
            checks.append({"name": name, "result": result, "detail": str(detail)[:500]})

        recorded = [image["id"] for image in view.images.values()]
        db = view.image("db")
        if db is not None:
            recorded.append(db["id"])
        try:
            dir_fd = os.open(str(view.folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                payload = view.payload("images.tar")
                if payload is None:
                    raise Failure("images.tar is not a payload of the bundle")
                fd, _ = self._check_payload(dir_fd, view.bundle_id, "images.tar", payload["size"], payload["sha256"])
            finally:
                os.close(dir_fd)
            try:
                proof = pf_source.image_archive_proof(fd, recorded)
            finally:
                os.close(fd)
            detail = f"proved {len(proof.image_ids)} image(s), {proof.layers_checked} layer(s)"
            if db is None:
                detail = (f"db image not recorded (legacy; excluded image:db); db ran as local postgres:16 "
                          f"{topology.images['db'][7:19]}; " + detail)
            add("images:archive", "passed", detail)
        except pf_source.ArchiveRefused as exc:
            add("images:archive", "failed", f"{exc.code}: {exc.reason}")
        except Failure as exc:
            add("images:archive", "failed", str(exc).splitlines()[0])
        temporary = Path(tempfile.mkdtemp(prefix="verify-source-", dir=str(self.operation_dir)))
        try:
            tree = self.extract_payload(view, view.source_payload, temporary / "source")
            expected = view.manifest["source"]["entries_sha256"]
            if expected is not None and pf_source.entries_digest(tree) != expected:
                add("source:archive", "failed", "the extracted tree differs from source.entries_sha256")
            else:
                add("source:archive", "passed", "entries " + (expected[:12] if expected else "not recorded (legacy)"))
        except (Failure, OSError, pf_source.SourceError) as exc:
            add("source:archive", "failed", (str(exc).splitlines() or ["?"])[0])
        finally:
            parent = os.open(str(temporary.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.remove_private_tree_at(parent, temporary.name)
            finally:
                os.close(parent)
        for name, path, limits in (("workspace:archive", view.workspace_payload, pf_source.SOURCE_LIMITS),
                                   ("history:archive", "revision-checkpoints.tar.gz"
                                    if view.payload("revision-checkpoints.tar.gz") else None, pf_source.HISTORY_LIMITS)):
            if path is None:
                add(name, "not_run", f"excluded: {name.split(':')[0]} payload absent")
                continue
            try:
                inventory = self.inspect_payload(view, path, limits=limits)
                add(name, "passed", f"{len(inventory.members)} member(s)")
            except Failure as exc:
                add(name, "failed", str(exc).splitlines()[0])
        state_files = (view.purge or {}).get("state_files") or []
        if not state_files:
            add("state:files", "not_run", "excluded: no state files listed")
        else:
            try:
                for name in state_files:
                    self.read_small_payload(view, "state/" + name, "state_file")
                add("state:files", "passed", f"{len(state_files)} state file(s)")
            except Failure as exc:
                add("state:files", "failed", str(exc).splitlines()[0])
        payloads = view.payloads_of("config_env")
        if not payloads:
            add("config:bundle", "not_run", "unavailable: legacy format 1 excludes config_env")
        else:
            try:
                values = pf_config.parse_app_env(self.read_small_payload(view, payloads[0]["path"], "config_env"),
                                                 label="config_env")
                self.check_app_values(dict(values))
                store = view.active_store
                if values["POSTGRES_DB"] != store["database"] or (store["owner"] is not None
                                                                  and values["POSTGRES_USER"] != store["owner"]):
                    add("config:bundle", "failed", "POSTGRES_DB/POSTGRES_USER differ from the active store")
                else:
                    add("config:bundle", "passed", "password substituted")
            except (Failure, pf_config.ConfigError) as exc:
                add("config:bundle", "failed", str(exc).splitlines()[0][:300])
        excluded = {item["item"]: item["reason"] for item in view.manifest["exclusions"]}
        records = view.payloads_of("deployment_record")
        if not records:
            add("deployment:record", "not_run", "excluded: " + excluded.get("deployment-record", "no deployment record"))
        else:
            try:
                data = self.read_small_payload(view, records[0]["path"], "deployment_record")
                for item in view.payloads_of("compose_resolved"):
                    self.read_small_payload(view, item["path"], "compose_resolved")
                record = pf_instance.parse_strict_json(data, label="deployment-record.json")
                problems = lifecycle_errors(record, "deployment_record") if isinstance(record, dict) \
                    else ["not an object"]
                if problems:
                    raise Failure("the deployment record is invalid (" + problems[0] + ")")
                for service in pf_docker.SERVICES:
                    image = view.image(service)
                    if image is not None and record["images"][service]["id"] != image["id"]:
                        raise Failure(f"the deployment record's {service} image is not the bundle's")
                add("deployment:record", "passed", "record " + pf_instance.sha256_bytes(data)[:12])
            except (Failure, pf_instance.ContextError) as exc:
                add("deployment:record", "failed", str(exc).splitlines()[0][:300])
        return checks

    def store_restores(self, view, topology):
        """Section 3.2 step 3 (purge) / the side-by-side data effect, bound to the topology: the db service up, the
        active store's init database dropped, every store restored and checked in manifest order; a non-connectable
        store is closed after its checks. Returns (checks, passed)."""
        checks = []
        self.require_images_present(topology)
        self.compose("up", "-d", "--no-build", "--no-deps", "db")
        self.wait_health("db")
        self.record_topology_resources(topology)
        self.inside("data")
        active = view.active_store["database"]
        if active in self.database_names():
            self.drop_database(active)
        for store in view.stores:
            store_checks, ok, _ = self.verify_store(store, view.folder / store["dump"], store["database"],
                                                    compatibility=True, drop=False)
            checks += store_checks
            if not ok:
                return checks, False
            if not store["allow_connections"]:
                self.sql("postgres", f"ALTER DATABASE {quote_identifier(store['database'])} ALLOW_CONNECTIONS false;",
                         mutation=True)
            self.inside("store")
        return checks, True

    def runtime_checks(self, view, topology):
        """Section 3.2 step 5 after the backend and frontend run: isolation, running images, health, runtime heads."""
        checks = []

        def add(name, result, detail=""):
            checks.append({"name": name, "result": result, "detail": str(detail)[:500]})

        containers, _, failures = self.observe_isolation(topology)
        for name in ("isolation:network", "isolation:listener", "isolation:mounts", "isolation:restart"):
            add(name, "failed" if name in failures else "passed", failures.get(name, ""))
        wrong = [item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) for item in containers
                 if item["image"] != topology.images.get(item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL))]
        add("images:running", "failed" if wrong or not containers else "passed",
            ("service(s) " + ", ".join(str(item) for item in wrong) + " run another image ID") if wrong else
            "no container" if not containers else ", ".join(f"{service} {topology.images[service][7:19]}"
                                                            for service in pf_docker.SERVICES))
        for service in ("backend", "frontend"):
            try:
                self.wait_health(service)
                if service == "frontend":
                    data = json.loads(self.compose("exec", "-T", "frontend", "wget", "-q", "-O", "-",
                                                   "http://127.0.0.1:5173/api/health"))
                    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("database") != "connected":
                        raise Failure("the API health answer is not ok/connected")
                add("health:" + service, "passed", "healthy")
            except DaemonFailure:
                raise
            except (Failure, ValueError) as exc:
                add("health:" + service, "failed", (str(exc).splitlines() or ["?"])[0])
        expected = view.manifest["compatibility"]["alembic_heads_image"] if view.legacy is None \
            else view.manifest["compatibility"]["alembic_heads_live"]
        heads = sorted(set(self.db_heads(view.active_store["database"])))
        add("heads:runtime", "passed" if heads == sorted(expected or []) else "failed",
            f"runtime heads {','.join(heads) or 'none'}" if heads == sorted(expected or [])
            else f"runtime heads {','.join(heads) or 'none'} vs {','.join(expected or []) or 'none'}")
        return checks

    def functional_failure(self, view, check, topology, mode):
        detail = f"{view.bundle_id}: {check['name']}: {check['detail']}"
        if mode == "purge":
            text = (f"functional-verification-failed: {detail}. The isolated stack {topology.project} was stopped and "
                    f"kept for inspection ('{self.pf_command()} cleanup --apply' removes it). The instance purge stops "
                    "before deletion; the application is reopened.")
        else:
            text = (f"functional-verification-failed: {detail}. The recovery target {topology.project} was stopped and "
                    f"kept for inspection ('{self.pf_command()} cleanup --apply --recovery-target {topology.project}' "
                    f"removes it). Operation {self.operation_id} closed failed_preserved.")
        exc = FunctionalFailed(text)
        exc.code = "functional-verification-failed"
        return exc

    def functional_verification(self, view, topology, *, mode, step=None, started_at=None):
        """Section 3.2: the functional recovery verification of ``view`` in ``topology``. ``mode``: purge (the data
        steps run here, the topology is torn down after a pass) or side-by-side (data from ``data-checks.json``; the
        target is kept). Writes the VerificationRecord (bound to the manifest hash and this operation) and returns it;
        a failed check stops the topology, writes the failed record and raises FunctionalFailed; a pass whose teardown
        is refused raises ``isolated-topology-kept`` after the passed record."""
        started_at = started_at or utc()
        planned = self.functional_names(view, topology)
        checks = [{"name": "topology:" + topology.project, "result": "passed",
                   "detail": f"uuid {topology.uuid[:8]}; model {topology.model_sha256[:12]}"}]
        environment = None
        failed = None

        def first_failed():
            return next((item for item in checks if item["result"] == "failed"), None)

        with self.bound(topology):
            try:
                if mode == "purge":
                    data_checks, _ = self.store_restores(view, topology)
                else:
                    data_checks = self.recorded_data_checks(view, topology)
                checks += data_checks
                failed = first_failed()
                if failed is None:
                    self.inside("payloads")
                    checks += self.payload_checks(view, topology)
                    failed = first_failed()
                if failed is None:
                    for service in ("backend", "frontend"):
                        self.compose("up", "-d", "--no-build", "--no-deps", service)
                    self.inside("runtime")
                    checks += self.runtime_checks(view, topology)
                    failed = first_failed()
                if failed is None:
                    restored = self.app_invariants("restored", topology=topology)
                    if mode == "purge":
                        source = self.load_app_check("source") or pf_config.ReconcileResult("incomplete", None, "")
                        check = pf_config.app_invariants_check(source, restored, mode="purge")
                    else:
                        check = pf_config.app_invariants_check(None, restored, mode="side-by-side",
                                                               recorded_summary=self.recorded_oracle(view))
                    checks.append(check)
                    failed = first_failed()
                environment = {"server_version_num": self.server_version_num(),
                               "engine_id": self.context.daemon.engine_id, "compose_version": self.compose_version}
            except DaemonFailure:
                raise
            except Failure as exc:
                if getattr(exc, "code", None) == "verification-isolation-unsupported":
                    raise
                present = {item["name"] for item in checks}
                name = next((item for item in planned if item not in present), "app-invariants")
                checks.append({"name": name, "result": "failed", "detail": str(exc).splitlines()[0][:500]})
                failed = first_failed()
            if failed is not None:
                present = {item["name"] for item in checks}
                checks += [{"name": name, "result": "failed", "detail": f"not reached: {failed['name']}"[:500]}
                           for name in planned if name not in present]
                try:
                    self.compose("stop")
                except DaemonFailure:
                    raise
                except Failure as exc:
                    log("WARNING: the isolated stack could not be stopped: " + str(exc).splitlines()[0])
            if environment is None:
                try:
                    environment = {"server_version_num": self.server_version_num(),
                                   "engine_id": self.context.daemon.engine_id, "compose_version": self.compose_version}
                except DaemonFailure:
                    raise
                except Failure:
                    environment = {"server_version_num": None, "engine_id": self.context.daemon.engine_id,
                                   "compose_version": self.compose_version}
        names = [store["database"] for store in view.stores]
        kind = "isolated-topology" if mode == "purge" else "recovery-target"
        if failed is not None:
            record = self.write_verification(view, level="functional_recovery_verified", result="failed",
                                             target={"kind": "isolated-database", "names": names, "removed": False},
                                             checks=checks, started_at=started_at, environment=environment)
            self.update_topology_record(topology.project, state="stopped")
            if step is not None:
                step.retained.append({"kind": kind, "name": topology.project, "sha256": None})
                step.evidence = (step.evidence + " " if step.evidence else "") + "record:" + record["verification_id"]
            self._record_capture(view, record, None)
            raise self.functional_failure(view, failed, topology, mode)
        removed = False
        if mode == "purge":
            self.inside("before-teardown")
            removed = self.teardown_topology(topology.project, final=True)
        record = self.write_verification(view, level="functional_recovery_verified", result="passed",
                                         target={"kind": "isolated-database", "names": names, "removed": removed},
                                         checks=checks, started_at=started_at, environment=environment)
        self._record_capture(view, record, None)
        if step is not None:
            step.evidence = (step.evidence + " " if step.evidence else "") + "record:" + record["verification_id"]
        if mode == "purge" and not removed:
            if step is not None:
                step.retained.append({"kind": "isolated-topology", "name": topology.project, "sha256": None})
            exc = Failure(f"isolated-topology-kept: the final bundle {view.bundle_id} passed functional verification "
                          f"(record {record['verification_id']}), but the isolated stack {topology.project} could not "
                          f"be removed ({self.teardown_reason(topology.project)}); the instance purge cannot delete "
                          "while it exists. The instance purge stops before deletion; the application is reopened. "
                          f"'{self.pf_command()} cleanup --apply' removes the stack later.")
            exc.code = "isolated-topology-kept"
            raise exc
        if mode != "purge" and step is not None:
            step.retained.append({"kind": "recovery-target", "name": topology.project, "sha256": None})
        self.publish_fresh("recovery", view.folder)
        return record

    def teardown_reason(self, project):
        record = self.topology_record(project) or {}
        refused = [entry for entry in record.get("teardowns") or [] if entry.get("state") == "refused"]
        return "teardown refused" if refused else "teardown incomplete"

    def recorded_oracle(self, view):
        """Section 3.3 side-by-side rule: the app-invariants summary of a passed functional record of this manifest
        (the bundle's purge oracle), or None."""
        for record in reversed(self.verification_records(view.bundle_id, view.manifest_sha256)):
            if record["level"] != "functional_recovery_verified" or record["result"] != "passed":
                continue
            for check in record["checks"]:
                if check["name"] == "app-invariants" and check["result"] == "passed" \
                        and check["detail"].startswith("source and restored equal: "):
                    return check["detail"][len("source and restored equal: "):]
        return None

    def recorded_data_checks(self, view, topology):
        """Side-by-side verification step 3: the store checks of ``data-checks.json`` (its hash recorded in
        ``topology.json``), with every store's heads re-read from the running isolated server."""
        record = self.topology_record(topology.project) or {}
        path = topology.directory / "data-checks.json"
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except OSError as exc:
            raise Failure("recovery-target-lost: data-checks.json is missing") from exc
        if pf_instance.sha256_bytes(data) != record.get("data_checks_sha256"):
            raise Failure("recovery-target-lost: data-checks.json differs from its recorded hash")
        checks = pf_instance.parse_strict_json(data, label="data-checks.json")
        for store in view.stores:
            name = store["database"]
            flag = None if store["allow_connections"] else name
            try:
                if flag:
                    self.sql("postgres", f"ALTER DATABASE {quote_identifier(flag)} ALLOW_CONNECTIONS true;",
                             mutation=True)
                heads = sorted(set(self.db_heads(name)))
            finally:
                if flag:
                    self.sql("postgres", f"ALTER DATABASE {quote_identifier(flag)} ALLOW_CONNECTIONS false;",
                             mutation=True)
            if heads != store["alembic_heads"]:
                checks = [dict(item, result="failed", detail=f"runtime heads {','.join(heads) or 'none'} differ")
                          if item["name"] == "heads:" + store["store_id"] else item for item in checks]
        return checks

    @contextlib.contextmanager
    def lock(self, pending_route=None, *, freeze=True, resume=None, request=None, observe_only=False):
        """Hold this instance's stable lock for one mutating operation.

        The lock inode lives under <installation-root>/locks and is never
        created here or removed by purge. ``pending_route`` is the command
        name; the PF-A3.2 gate (section 3.3) decides whether it starts a new
        operation, re-enters the blocking one (``resume`` and the aliases),
        supersedes it or is refused. Inside the lock the operation directory is
        created and the application configuration is frozen (PF-A1.2) before
        any effect; a re-entry binds the existing directory and its frozen
        configuration instead and writes nothing yet.

        PF-A3.3 (section 4.7): ``observe_only`` (the cleanup report and the runner-record acknowledgement) holds the
        same lock, runs the install-binding check and the gate, and then neither begins an operation nor freezes
        anything: no operation directory is created and only classifier-proven read-only children can start.
        """
        try:
            handle = pf_instance.acquire_instance_lock(self.context)
        except pf_instance.ContextError as exc:
            raise Failure(str(exc)) from exc
        try:
            # PF-A2.1: inside the lock, before the journal is read or any operation begins.
            self.require_install_binding(pending_route or "operation")
            # Every lock passes the gate; an unnamed route (helpers, tests) is refused next to any open operation.
            self.gate_route(pending_route or "operation", dict(request or {}, **(resume or {})))
            if observe_only:
                if self.gate is None or self.gate.action != "observe":
                    raise Failure("Internal error: an observe-only lock needs an observe decision of the gate.")
                self._observe_only = True
            elif self.gate is not None and self.gate.action == "observe":
                raise Failure("Internal error: an observe decision of the gate needs an observe-only lock.")
            elif self.gate is not None and self.gate.action == "reenter":
                self.bind_operation(self.gate.entry, route=pending_route)
            else:
                # Private runtime state is created only here, inside a locked mutation route.
                if not self.state.is_dir():
                    self.state.mkdir(mode=0o700)
                    os.chmod(self.state, 0o700)
                self.begin_operation(pending_route or "operation", freeze=freeze)
            yield handle
        finally:
            self._observe_only = False
            self.end_operation()
            handle.release()

    def gate_route(self, route, request):
        """Section 3.3 inside the lock: rebuild the index, decide, and re-prove the pre-lock selections (the frozen
        admin configuration of section 3.8, the workspace-interval exception of section 3.7a). Writes nothing."""
        index = self.operation_index()
        decision = pf_config.gate_decision(index, route, slug=self.context.slug, request=request,
                                           private_state=str(self.context.paths.private_state))
        if decision.action == "refuse":
            raise Failure(decision.message)
        selected = self._config_entry.operation_id if self._config_entry is not None else None
        entered = decision.entry.operation_id if decision.action == "reenter" else None
        if self._config_selected and selected != entered and (
                selected is not None or decision.entry.plan.get("admin_config") is not None):
            raise Failure(f"plan-input-changed: the operation '{PENDING_ROUTE_COMMANDS.get(route, route)}' acts on changed while it was "
                          "starting (the open operations differ from the ones read before the lock). Nothing was "
                          "changed; run the command again.")
        if self._interval_entry is not None:
            validation = self.ensure_validation()
            again = self.workspace_interval_exception(route, validation, index, operation=request.get("operation"))
            if again is None or again.operation_id != self._interval_entry:
                raise Failure("Protected context validation refused mutation:\n"
                              + "\n".join(validation.blocking_messages()))
        self.gate = decision
        return decision

    def bind_operation(self, entry, *, route=None):
        """Re-enter ``entry`` (section 3.6): the same operation ID and directory, its plan and journal, the runner's
        effect file, the frozen admin configuration and the plan's frozen application snapshot. Writes nothing; a
        missing or changed snapshot refuses ``plan-input-changed``."""
        directory = self.context.operations_dir / entry.operation_id
        self.operation_id, self.operation_dir = entry.operation_id, directory
        self.reset_operation_scope(route or "resume")
        # The renders of earlier processes keep their compose-<n>.json names (exclusive create); this one continues.
        self._envelope_sequence = max((int(match.group(1)) for match in (
            re.fullmatch(r"compose-([0-9]+)\.json", name) for name in os.listdir(str(directory))) if match), default=0)
        self._permission_targets = None
        self.runner.effects_path = directory / "unresolved-effects.json"
        self.plan, self.plan_sha256, self.journal = entry.plan, entry.plan_sha256, entry.journal
        self._snapshots = 1 + sum(1 for name in os.listdir(str(directory)) if re.fullmatch(r"refreeze-[0-9]+", name))
        if entry.plan["admin_config"] is not None:
            self._config_entry = entry
            self._config = self.load_frozen_admin_config(entry)
        frozen = entry.plan["frozen_config"]
        if frozen is not None:
            try:
                self.frozen = pf_config.load_frozen_app_config(directory, frozen["sha256"], frozen["bytes"])
            except pf_config.ConfigError as exc:
                text = str(exc)
                raise Failure(f"plan-input-changed: frozen application configuration of operation {entry.operation_id} "
                              f"no longer matches its plan ({text.split(': ', 1)[-1]}). Nothing was changed.") from exc
            for key in pf_config.SECRET_KEYS:
                self.redactor.add(self.frozen.values[key])
            try:
                editable = pf_instance.sha256_bytes(pf_instance.read_bytes_nofollow(self.config_dir / ".env"))
            except OSError:
                editable = None
            if editable is not None and editable != self.frozen.source_sha256:
                log(f"note: config-proposal-differs: config/.env differs from the configuration operation "
                    f"{entry.operation_id} froze; it stays a proposal for the next operation.")
        self._arm_runner()

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
        major = self.server_version_num() // 10000
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
        """Tag the running backend/frontend images; PF-A3.1: each value is the enriched $defs.image identity."""
        return {service: self.retain_image(service, backup_id) for service in pf_docker.BUILT_SERVICES}

    def verify_images(self, images):
        """Every built service's retained tag still names the recorded image ID (a re-pointed tag is refused)."""
        for service in pf_docker.BUILT_SERVICES:
            value = images[service]
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

    def create_database(self, name, *, locale=None):
        """``createdb`` from template0; ``locale`` (encoding, collate, ctype) of a captured store when known."""
        quote_identifier(name)
        arguments = ["--owner=" + self.env()["POSTGRES_USER"], "--template=template0"]
        if locale is not None and None not in locale:
            arguments += ["--encoding=" + locale[0], "--lc-collate=" + locale[1], "--lc-ctype=" + locale[2]]
        self.database_program("createdb", *arguments, name)

    def restore_into(self, database, dump, *, locale=None):
        self.create_database(database, locale=locale)
        fd = os.open(str(dump), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            self.database_program("pg_restore", "-d", database, "--exit-on-error", "--no-owner", "--no-privileges",
                                  input_file=stream)

    def ensure_backup_tree(self):
        """Create the checkpoint tree inside an explicit mutation; construction never does this. PF-A2.3: each
        directory gets the backups directory target of the permission policy in force (apply_single)."""
        for directory in (self.backups_root, self.revisions_root, self.backups_dir):
            if not directory.is_dir():
                directory.mkdir(mode=0o750)
            self.apply_single("backups", directory, "dir")

    def ensure_recovery_tree(self):
        for directory in (self.recovery_root.parent, self.recovery_root):
            if not directory.is_dir():
                directory.mkdir(mode=0o750)
            self.apply_single("recovery", directory, "dir")

    # ------------------------------------------- strict bundle reader (PF-A3.1 section 3.1)

    def _listed(self, kind, folder):
        """One listing entry: the strict read, or the folder with the code of its refusal (never raised here)."""
        try:
            return self.read_bundle(kind, folder, quiet=True)
        except Failure as exc:
            return InvalidBundle(Path(folder), failure_code(exc), str(exc))

    def snapshots(self):
        """Every checkpoint folder of this instance, newest first: a BundleView or an InvalidBundle. Read-only: a
        legacy manifest migrates in memory and nothing is written."""
        if not real_directory(self.backups_dir):
            return []
        self.ensure_config()  # the instance binding (step 6) reads the configured repository
        items = [self._listed("checkpoint", folder) for folder in self.backups_dir.iterdir()
                 if BACKUP_RE.fullmatch(folder.name)]
        return sorted(items, key=lambda item: item.bundle_id, reverse=True)

    @staticmethod
    def bundle_suffix(item):
        legacy = item.manifest["legacy"]
        return f"  legacy-format-{legacy['format']}" if legacy is not None else ""

    def display_page(self, items, page):
        selected, pages, start = page_items(items, page)
        log(f"Backups: newest first | page {page}/{pages} | {len(items)} total")
        for number, item in enumerate(selected, start + 1):
            if isinstance(item, InvalidBundle):
                log(f"{number:>3}. {item.bundle_id}  [invalid: {item.code}]")
                continue
            log(f"{number:>3}. {item.bundle_id}  [{CLASS_NAMES[item.capture_class]}|{LEVEL_NAMES[item.level]}]  "
                f"{item.display_reason}  DB={','.join(item.database_heads) or 'uninitialized'}  "
                f"source={item.source_display}{self.bundle_suffix(item)}")
        return pages

    def choose_snapshot(self, requested=None):
        items = self.snapshots()
        if not items:
            raise Failure("No revision checkpoints exist yet. Legacy dump-only backups cannot restore source.")
        if requested:
            matches = [item for item in items if item.bundle_id == requested
                       or (isinstance(item, BundleView) and item.source_hypothesis == requested)]
            if len(matches) != 1:
                raise Failure("Specify an exact backup ID, or a full SHA with exactly one matching backup.")
            return self.verify_snapshot(matches[0].bundle_id)
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
                return self.verify_snapshot(items[int(answer) - 1].bundle_id)

    def verify_snapshot(self, backup_id):
        """The strict read of one checkpoint of this instance (name kept; section 3.1)."""
        if not isinstance(backup_id, str) or not BACKUP_RE.fullmatch(backup_id):
            raise Failure("Invalid backup ID.")
        return self.read_bundle("checkpoint", self.backups_dir / backup_id)

    def read_bundle(self, kind, folder, *, quiet=False):
        """Steps 1-9 of section 3.1 before any confirmation, journal, extraction, image load or database effect.
        Writes nothing except, inside an open operation, a legacy manifest's migration record and migrated bytes.
        ``quiet``: listings (no notes are logged)."""
        folder = Path(folder)
        name = folder.name
        if kind == "purge-bundle":
            if folder.parent != self.recovery_root or not real_directory(folder):
                raise Failure(
                    f"recovery-outside-instance: {folder} is not a bundle directory of instance {self.context.slug} "
                    f"({self.recovery_root}); only the selected instance's own recovery bundles can be listed or "
                    "restored. Nothing was changed.")
            if not RECOVERY_RE.fullmatch(name):
                raise Failure("Invalid recovery bundle path.")
        else:
            if folder.parent != self.backups_dir or not BACKUP_RE.fullmatch(name):
                raise Failure("Invalid backup ID.")
            if not real_directory(folder):
                raise bundle_failure("manifest-missing", name, "the checkpoint folder is missing or not a directory")
        try:
            dir_fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            raise bundle_failure("manifest-missing", name, f"the bundle folder cannot be opened ({exc.strerror})") \
                from exc
        try:
            return self._read_bundle_at(kind, folder, dir_fd, quiet=quiet)
        except OSError as exc:
            raise bundle_failure("bundle-unreadable", name, f"{exc.strerror or exc}") from exc
        finally:
            os.close(dir_fd)

    @staticmethod
    def _bundle_file(dir_fd, name, limit):
        """A protected manifest file of a bundle folder: no-follow, regular, root-owned, not group/other writable."""
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_uid != pf_instance.TRUSTED_UID \
                    or stat.S_IMODE(info.st_mode) & 0o022:
                raise ValueError(f"{name} is not a protected regular file of at most {limit} bytes")
            data = b""
            while len(data) <= limit:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                data += block
            if len(data) > limit:
                raise ValueError(f"{name} exceeds {limit} bytes")
            return data
        finally:
            os.close(fd)

    @staticmethod
    def open_bundle_payload(dir_fd, path):
        """A payload of a bundle folder opened component by component without following any link (section 3.1
        step 7); FileNotFoundError, or OSError(ELOOP/ENOTDIR) for a link on the way."""
        parts = path.split("/")
        current = os.dup(dir_fd)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
                os.close(current)
                current = child
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=current)
        finally:
            os.close(current)

    @staticmethod
    def _fd_sha256(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        hasher = hashlib.sha256()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                return hasher.hexdigest()
            hasher.update(block)

    def _check_payload(self, dir_fd, bundle_id, path, size, sha256, *, tail="Nothing was changed."):
        """Step 7 for one payload: an open descriptor of a no-follow, unlinked regular file with exactly ``size``
        (None: any) bytes and ``sha256``; the caller closes it. Returns (fd, size)."""
        try:
            fd = self.open_bundle_payload(dir_fd, path)
        except FileNotFoundError as exc:
            raise bundle_failure("bundle-payload-mismatch", bundle_id, f"{path}: missing", tail=tail) from exc
        except OSError as exc:
            reason = "missing" if exc.errno == errno.ENOENT else "not a regular file"
            raise bundle_failure("bundle-payload-mismatch", bundle_id, f"{path}: {reason}", tail=tail) from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise bundle_failure("bundle-payload-mismatch", bundle_id, f"{path}: not a regular file", tail=tail)
            if info.st_nlink != 1:
                raise bundle_failure("bundle-payload-mismatch", bundle_id, f"{path}: linked", tail=tail)
            if size is not None and info.st_size != size:
                raise bundle_failure("bundle-payload-mismatch", bundle_id, f"{path}: size", tail=tail)
            if self._fd_sha256(fd) != sha256:
                raise bundle_failure("bundle-payload-mismatch", bundle_id, f"{path}: hash", tail=tail)
            os.lseek(fd, 0, os.SEEK_SET)
            return fd, info.st_size
        except BaseException:
            os.close(fd)
            raise

    def _verify_payload(self, dir_fd, bundle_id, path, size, sha256):
        fd, actual = self._check_payload(dir_fd, bundle_id, path, size, sha256)
        os.close(fd)
        return actual

    @staticmethod
    def _folder_entries(dir_fd, limit=20000):
        """Every entry below a bundle folder descriptor (no-follow walk; files are never opened): [(path, is_dir)]."""
        found = []
        stack = [(os.dup(dir_fd), "")]
        try:
            while stack:
                fd, prefix = stack.pop()
                try:
                    with os.scandir(fd) as listing:
                        entries = sorted((entry.name, entry.is_dir(follow_symlinks=False)) for entry in listing)
                    for name, is_dir in entries:
                        path = prefix + name
                        found.append((path, is_dir))
                        if len(found) > limit:
                            raise ValueError("the bundle folder has too many entries")
                        if is_dir:
                            stack.append((os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                                  dir_fd=fd), path + "/"))
                finally:
                    os.close(fd)
        finally:
            for fd, _ in stack:
                os.close(fd)
        return found

    def _bundle_entries(self, dir_fd, name):
        """_folder_entries of one bundle folder; an oversized folder is a coded refusal of that bundle only, so a
        listing shows it as ``[invalid: bundle-unlisted-file]`` instead of failing (audit AF-4)."""
        try:
            return self._folder_entries(dir_fd)
        except ValueError as exc:
            raise bundle_failure("bundle-unlisted-file", name, str(exc)) from exc

    @staticmethod
    def _unlisted(entries, payload_paths):
        allowed = {"manifest.json", "manifest.sha256"} | set(payload_paths)
        for path in payload_paths:
            parts = path.split("/")
            allowed.update("/".join(parts[:index]) for index in range(1, len(parts)))
        return [path for path, _ in entries if path not in allowed]

    def _refuse_state_files(self, bundle_id, state_files):
        """The PF-A1.4 restore allowlist of a purge bundle's state files (checked value = consumed value)."""
        if not isinstance(state_files, list):
            raise Failure(
                f"recovery-state-file-refused: bundle {bundle_id} lists state files as {type(state_files).__name__}, "
                f"not a list of file names; only {', '.join(RESTORABLE_STATE_FILES)} can be restored into protected "
                "state. Nothing was changed.")
        for item in state_files:
            if item not in RESTORABLE_STATE_FILES:
                raise Failure(
                    f"recovery-state-file-refused: bundle {bundle_id} lists state file {item!r}; only "
                    f"{', '.join(RESTORABLE_STATE_FILES)} can be restored into protected state. Nothing was changed.")

    def _read_bundle_at(self, kind, folder, dir_fd, *, quiet):
        name = folder.name
        try:
            data = self._bundle_file(dir_fd, "manifest.json", MANIFEST_READ_LIMIT)
            hash_text = self._bundle_file(dir_fd, "manifest.sha256", 4096)
        except FileNotFoundError as exc:
            raise bundle_failure("manifest-missing", name, "manifest.json or manifest.sha256 is missing") from exc
        except (OSError, ValueError) as exc:
            raise bundle_failure("manifest-invalid", name, f"1 problem(s): {exc}") from exc
        manifest_sha256 = pf_instance.sha256_bytes(data)
        lines = hash_text.decode("ascii", "replace").splitlines()
        if not lines or lines[0].strip() != manifest_sha256:
            raise bundle_failure("manifest-checksum-mismatch", name, "manifest.json does not match manifest.sha256")
        try:
            parsed = pf_instance.parse_strict_json(data, label=f"{name}/manifest.json")
        except pf_instance.ContextError as exc:
            raise bundle_failure("manifest-invalid", name, f"1 problem(s): {exc}") from exc
        supported = ("this control reads schema_version 1 and legacy formats 1 and 2")
        legacy_record = None
        if isinstance(parsed, dict) and "schema_version" in parsed:
            if type(parsed["schema_version"]) is not int or parsed["schema_version"] != 1:
                raise bundle_failure("manifest-schema-unsupported", name,
                                     f"schema_version {parsed['schema_version']!r} ({supported})")
            problems = [] if data == pf_instance.normalize_json(parsed) else ["not normalized"]
            problems = problems or lifecycle_errors(parsed, "recovery_manifest")
            if not problems and parsed["bundle_id"] != name:
                problems.append(f"bundle_id {parsed['bundle_id']} is not the folder name")
            if not problems and parsed["bundle_kind"] != kind:
                problems.append(f"bundle_kind {parsed['bundle_kind']} is not {kind}")
            if problems:
                raise bundle_failure("manifest-invalid", name,
                                     f"{len(problems)} problem(s): " + "; ".join(problems[:10]))
            manifest = parsed
            if kind == "purge-bundle":
                self._refuse_state_files(name, manifest["purge"]["state_files"])
                for item in manifest["purge"]["state_files"]:
                    payload = next((entry for entry in manifest["payloads"] if entry["path"] == "state/" + item), None)
                    if payload is None or payload["type"] != "state_file":
                        raise Failure(
                            f"recovery-state-file-refused: bundle {name} lists state file {item!r} without a "
                            f"state/{item} state_file payload; only verified payloads are restored. Nothing was "
                            "changed.")
            self._bind_instance(kind, name, manifest)
            for payload in manifest["payloads"]:
                self._verify_payload(dir_fd, name, payload["path"], payload["size"], payload["sha256"])
            unlisted = self._unlisted(self._bundle_entries(dir_fd, name),
                                      [item["path"] for item in manifest["payloads"]])
            if unlisted:
                raise bundle_failure("bundle-unlisted-file", name, f"{unlisted[0]} is not listed in the manifest")
        elif isinstance(parsed, dict) and type(parsed.get("format")) is int and parsed["format"] in (1, 2):
            if parsed.get("id") != name:
                raise bundle_failure("manifest-schema-unsupported", name,
                                     f"legacy id {parsed.get('id')!r} is not the folder name ({supported})")
            if kind == "purge-bundle":
                self._refuse_state_files(name, parsed.get("state_files", []))
            checksums = parsed.get("checksums")
            if not isinstance(checksums, dict) or not checksums:
                raise bundle_failure("manifest-schema-unsupported", name,
                                     f"the legacy checksum map is empty or missing ({supported})")
            sizes = {}
            for path, digest_value in sorted(checksums.items()):
                if pf_config.payload_type_for(path) is None or not isinstance(digest_value, str):
                    raise bundle_failure("manifest-schema-unsupported", name,
                                         f"legacy payload {path!r} has no payload type ({supported})")
                sizes[path] = self._verify_payload(dir_fd, name, path, None, digest_value)
            unlisted = self._unlisted(self._bundle_entries(dir_fd, name), list(checksums))
            try:
                manifest, legacy_record = pf_config.migrate_legacy_manifest(
                    parsed, legacy_sha256=manifest_sha256, bundle_kind=kind, payload_sizes=sizes,
                    unlisted_entries=unlisted)
            except pf_config.ConfigError as exc:
                raise Failure(f"{exc} ({supported}). Nothing was changed.") from exc
            problems = lifecycle_errors(manifest, "recovery_manifest")
            if problems:
                raise bundle_failure("manifest-schema-unsupported", name,
                                     f"legacy manifest cannot be migrated ({problems[0]})")
            self._bind_instance(kind, name, manifest)
        else:
            raise bundle_failure("manifest-schema-unsupported", name, f"unrecognized manifest shape ({supported})")
        level, latest = self.verification_level(name, manifest_sha256, quiet=quiet)
        view = BundleView(folder, manifest, manifest_sha256, legacy_record, level, latest)
        if legacy_record is not None and self.operation_dir is not None:
            pf_instance._write_private_file(self.operation_dir / f"manifest-migration-{name}.json",
                                            pf_instance.normalize_json(legacy_record), 0o600)
            pf_instance._write_private_file(self.operation_dir / f"migrated-{name}.json",
                                            pf_instance.normalize_json(manifest), 0o600)
            if not quiet:
                log(f"note: legacy-manifest-migrated: {name} format {legacy_record['legacy_format']} read as "
                    f"{manifest['capture_class']}; limitations: {'; '.join(legacy_record['limitations'])}.")
        return view

    def _bind_instance(self, kind, name, manifest):
        """Step 6: a checkpoint names this instance's project and repository (data checks; never a target). PF-A3.3
        (section 3.5): a purge bundle's identity is decided by the restore routes (``restore-target-mismatch``); its
        recorded paths, project and daemon are provenance only."""
        source = manifest["source_instance"]
        if kind != "checkpoint":
            return
        if source["compose_project"] != self.context.compose_project or source["repository"] != self.config["repository"]:
            raise Failure("Checkpoint belongs to a different deployment.")

    def require_restore_identity(self, view):
        """Section 3.5 identity (exact and side-by-side): a non-legacy bundle of the selected instance UUID; a legacy
        bundle (no recorded UUID) of the selected project. Otherwise ``restore-target-mismatch`` before any
        confirmation (a bundle of another instance, including one from before a host loss, needs an import route with
        policy approval that this control does not provide; owner deviation OD-A33-09)."""
        source = view.manifest["source_instance"]
        if source["instance_id"] is not None:
            if source["instance_id"] == self.context.instance_id:
                return
            owner = f"instance {source['instance_id'][:8]}"
        else:
            if source["compose_project"] == self.context.compose_project:
                return
            owner = f"project {source['compose_project']}"
        exc = Failure(f"restore-target-mismatch: bundle {view.bundle_id} belongs to {owner}, the selected instance is "
                      f"{self.context.slug} ({self.context.instance_id[:8]}). Exact and side-by-side restore only use "
                      "the selected instance's own bundles; a bundle of another instance (including one from before a "
                      "host loss) needs an import route with policy approval, which this control does not provide "
                      "(SYNOLOGY_ADMIN §12). Nothing was changed.")
        exc.code = "restore-target-mismatch"
        raise exc

    def local_image_id(self, reference):
        """The image ID ``reference`` names on the bound daemon, or None (read-only)."""
        try:
            data = json.loads(self.docker("image", "inspect", reference))
        except DaemonFailure:
            raise
        except (Failure, ValueError):
            return None
        image_id = data[0].get("Id") if isinstance(data, list) and data and isinstance(data[0], dict) else None
        return image_id if isinstance(image_id, str) and pf_docker.IMAGE_ID_RE.fullmatch(image_id) else None

    def other_instances_on_daemon(self):
        """Slugs of the other registered instances bound to this instance's daemon (registry read, no lock)."""
        try:
            registry = pf_instance.load_registry(self.context.installation_root)
        except pf_instance.ContextError:
            return []
        return sorted(context.slug for _, context, _ in registry.records()
                      if context is not None and context.instance_id != self.context.instance_id
                      and context.daemon.engine_id == self.context.daemon.engine_id)

    def prove_bundle_images(self, view, required):
        """Section 3.5/3.6, read-only before the confirmation: ``images.tar`` proves ``required`` IDs; returns the
        ImageArchiveProof (its repo_tags drive the retag check)."""
        payload = view.payload("images.tar")
        if payload is None:
            raise self.isolation_failure(f"bundle {view.bundle_id} has no images.tar")
        dir_fd = os.open(str(view.folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fd, _ = self._check_payload(dir_fd, view.bundle_id, "images.tar", payload["size"], payload["sha256"])
        finally:
            os.close(dir_fd)
        try:
            return pf_source.image_archive_proof(fd, required)
        except pf_source.ArchiveRefused as exc:
            raise bundle_failure(exc.code, view.bundle_id, f"images.tar: {exc.reason}") from exc
        finally:
            os.close(fd)

    def restore_db_image(self, view):
        """Section 3.5 database image (OD-A31-06): (image-tag effect spec or None, summary lines). A recorded db image
        is proven inside images.tar first; postgres:16 absent -> tagged to it after the load (daemon-wide, not owned);
        another local ID -> the note db-image-changed; a legacy null -> the note db-image-unrecorded."""
        db = view.image("db")
        local = self.local_image_id(pf_docker.DB_IMAGE)
        if db is None:
            log("note: db-image-unrecorded: the legacy bundle recorded no database image; local postgres:16 "
                + (local[7:19] if local else "absent, pulled by Compose") + " is used (logical restore)")
            return None, []
        if local == db["id"]:
            return None, []
        if local is not None:
            log(f"note: db-image-changed: the local postgres:16 {local[7:19]} differs from the bundle's {db['id'][7:19]}; "
                "the logical restore checks the PostgreSQL major after the db start.")
            return None, []
        # The tag will name the image the bundle's archive provides: proven inside images.tar first (read-only).
        self.prove_bundle_images(view, [db["id"]])
        others = self.other_instances_on_daemon()
        line = (f"The daemon-wide tag postgres:16 is absent; it will name the bundle's PostgreSQL image {db['id'][7:19]} "
                f"for every instance on this daemon ({', '.join(others) or 'no other registered instance'}). It is "
                "never removed by pf.")
        effect = {"phase": "preparing-target", "type": "image-tag", "target": "image:postgres:16=" + db["id"][7:19],
                  "postcondition": "postgres:16 names " + db["id"],
                  "preconditions": ["db-image-id:" + db["id"], "after the image load"]}
        return effect, [line]

    def act_image_tag(self, step, effect):
        """The ``image-tag image:postgres:16=<id12>`` effect: tag the loaded bundle db image as postgres:16; an existing
        tag naming another ID is never re-pointed (``db-image-changed-during-restore``)."""
        wanted = self.precondition(effect, "db-image-id")
        local = self.local_image_id(pf_docker.DB_IMAGE)
        if local is None:
            self.docker("image", "tag", wanted, pf_docker.DB_IMAGE)
            local = self.local_image_id(pf_docker.DB_IMAGE)
        if local != wanted:
            raise Failure(self.db_image_changed(local, wanted))
        step.evidence = "postgres:16 " + wanted[7:19]
        log(f"note: db-image-tagged: postgres:16 was absent; it now names the bundle's database image {wanted[7:19]} "
            "for every instance on this daemon.")

    def db_image_changed(self, local, wanted):
        return (f"db-image-changed-during-restore: postgres:16 now names {str(local)[7:19]}, not the bundle's database "
                f"image {wanted[7:19]}; the bundle's images were loaded; nothing else changed. Operation "
                f"{self.operation_id} stays open in preparing-target. Restore the tag or run '{self.pf_command()} resume "
                f"--operation {self.operation_id} --abandon'.")

    def verification_level(self, bundle_id, manifest_sha256, *, quiet=False):
        """Step 9 (read-only): (level, latest verification id) of the valid records bound to this manifest hash.
        An invalid record is reported and ignored, never repaired."""
        directory = self.context.artifacts_dir / "verifications" / bundle_id
        records = []
        try:
            names = sorted(os.listdir(str(directory))) if real_directory(directory) else []
        except OSError:
            names = []
        for file_name in names:
            if ".tmp-" in file_name:
                continue  # an atomic _write_private_file temporary: absent or complete, never half a record
            detail = None
            try:
                data = pf_instance.read_bytes_nofollow(directory / file_name)
                record = pf_instance.parse_strict_json(data, label=file_name)
                problems = ([] if data == pf_instance.normalize_json(record) else ["not normalized"]) \
                    or (lifecycle_errors(record, "verification_record") if isinstance(record, dict)
                        else ["not an object"])
                if not problems and (record["bundle_id"] != bundle_id
                                     or file_name != record["verification_id"] + ".json"):
                    problems = ["the record names another bundle or file"]
                if problems:
                    detail = problems[0]
            except (OSError, pf_instance.ContextError) as exc:
                detail = str(exc)
            if detail is not None:
                if not quiet:
                    log(f"note: verification-record-invalid: {file_name}: {detail}; ignored.")
                continue
            if record["manifest_sha256"] == manifest_sha256:
                records.append(record)
        records.sort(key=lambda item: item["verification_id"])
        passed = {item["level"] for item in records if item["result"] == "passed"}
        if "functional_recovery_verified" in passed:
            level = "functional_recovery_verified"
        elif "data_restore_verified" in passed:
            level = "data_restore_verified"
        elif any(item["result"] == "failed" for item in records):
            level = "failed"
        else:
            level = "captured"
        return level, (records[-1]["verification_id"] if records else None)

    def pause(self, kind=None, **extra):
        """The ``services:stop:frontend,backend`` effect body: stop the writers and observe them stopped. PF-A3.2:
        it no longer writes a journal (the operation journal records the effect)."""
        self.compose("stop", "frontend", "backend")
        for service in ("frontend", "backend"):
            if self.inspect(service)["State"].get("Running"):
                raise Failure("Application writes have not been stopped.")

    def activation_complete(self):
        """Section 3.9: every ``service:<svc>:start`` effect of phase activating is complete (False without one)."""
        if self.plan is None:
            return False
        starts = [effect for effect in self.plan["effects"] if effect["phase"] == "activating"
                  and pf_config.effect_role(effect) in ("backend", "frontend")]
        return bool(starts) and all(self.effect_state(effect["effect_id"]) == "complete" for effect in starts)

    def fail_closed_allowed(self):
        """Section 3.9 (phase-aware): only for a blocking operation whose kind's DISPATCH column allows it and whose
        activation is not complete; never for purge or backup, never without a blocking operation, never for a
        refused re-entry."""
        if self.plan is None or self.journal is None or self.journal["phase"] in pf_config.CLOSED_PHASES:
            return False
        if self.gate is not None and self.gate.action == "reenter" and not self._reentered:
            return False
        if pf_config.KIND_FAIL_CLOSED[self.plan["kind"]] == "never":
            return False
        if self.activation_complete():
            log(f"Application left running (activation completed in operation {self.operation_id}).")
            return False
        return True

    def fail_closed(self):
        """After a failed operation with a blocking journal: stop this instance's own one-off Compose
        containers (exact PF-A1.3 inventory), then the application services (PF-A1.4).

        Runs under the failing operation's lock, through the same context and runner. A cached daemon
        refusal ends it before any further process; a one-off that vanished after the inventory (an
        exited ``--rm`` job) is reported and skipped. Nothing is selected by the legacy run label.
        PF-A3.2: only while fail_closed_allowed() holds (section 3.9).
        """
        if not self.fail_closed_allowed():
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

    def activate_backend(self, images, expected_heads):
        """The ``service:backend:start`` effect: the selected images, the backend up and healthy on the expected
        heads (PF-A3.2 split of the A3.1 activate())."""
        self.verify_images(images)
        self.make_override(images, self.override)
        self.compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "backend")
        self.wait_health("backend")
        if self.db_heads() != expected_heads:
            raise Failure("Live Alembic revision differs from the selected application.")

    def activate_frontend(self, images):
        """The ``service:frontend:start`` effect; the API health check is part of its postcondition."""
        self.make_override(images, self.override)
        self.compose("up", "-d", "--no-deps", "--no-build", "--force-recreate", "frontend")
        self.wait_health("frontend")
        response = self.compose("exec", "-T", "frontend", "wget", "-q", "-O", "-", "http://127.0.0.1:5173/api/health")
        data = json.loads(response)
        if data.get("status") != "ok" or data.get("database") != "connected":
            raise Failure("Frontend/API/database health check failed.")
        log("Application health checks passed. Perform the UI/workflow and network-access smoke tests separately.")

    def activate(self, images, expected_heads):
        self.activate_backend(images, expected_heads)
        self.activate_frontend(images)

    def publish_source_tree(self, candidate, manifest, target):
        """PF-A2.3/PF-A3.2 W1 body: content-only copies of the tree's top-level names into the new directory
        ``target``, then the workspace root target and explicit targets for exactly the names copied (executables
        from ``manifest``), through one inventory of ``target`` as the workspace scope root."""
        names = sorted(item.name for item in Path(candidate).iterdir())
        for name in names:
            copy_fresh(Path(candidate) / name, Path(target) / name)
        designated = frozenset(entry["path"] for entry in manifest["entries"]
                               if entry["kind"] == "file" and entry.get("executable"))
        _, compiled, _ = self.permission_targets()
        inventory = pf_instance.inventory_scope("workspace", Path(target), owner_rule=compiled["workspace"].owner_rule,
                                                limit=pf_source.MANIFEST_ENTRY_LIMIT)
        self._publish("workspace", inventory, executables=designated)

    def write_workspace_manifest(self, manifest):
        """W4 / deploy --current: the protected workspace source manifest; returns the hash of its bytes."""
        try:
            return pf_source.write_manifest(self.context.source_manifest_path, manifest)
        except pf_source.SourceError as exc:
            raise Failure(str(exc)) from exc

    # ------------------------------------------- workspace generation switch (PF-A3.2 section 3.7)

    def workspace_preflight(self, manifest):
        """Read-only, before the confirmation (and again at the syncing point): ("switch", None) or ("pending",
        "<reason>: <detail>") for the first matching unavailability reason of section 3.7."""
        workspace = self.root
        parent = workspace.parent
        name = pf_instance.GENERATION_CONTAINER_PREFIX + self.context.instance_id
        container = parent / name
        try:
            info, parent_info = os.lstat(str(workspace)), os.lstat(str(parent))
        except OSError as exc:
            return "pending", f"workspace-is-mount-point: the workspace cannot be examined ({exc.strerror})"
        if info.st_dev != parent_info.st_dev:
            return "pending", "workspace-is-mount-point: the workspace is a mount point, subvolume or shared-folder root"
        try:
            parent_fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            return "pending", f"generation-container-unsafe: the workspace parent cannot be opened ({exc.strerror})"
        try:
            problem = pf_instance.generation_container_problem(parent_fd, name, info.st_dev)
        finally:
            os.close(parent_fd)
        if problem is not None:
            return "pending", "generation-container-unsafe: " + problem
        try:
            registry = pf_instance.load_registry(self.context.installation_root)
            inventory = pf_instance.inventory_of(registry)
        except pf_instance.ContextError as exc:
            return "pending", f"generation-container-collision: the registry cannot be read ({exc})"[:200]
        for owner, role, path in inventory:
            path = Path(path)
            if path == container or pf_instance._contains(path, container) or pf_instance._contains(container, path):
                return "pending", f"generation-container-collision: {role} of {owner}"[:200]
        try:
            fd = os.open(str(workspace), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as exc:
            return "pending", f"workspace-root-acl: the workspace cannot be opened ({exc.strerror})"
        try:
            acl = pf_instance.inspect_acl_fd(fd)
        finally:
            os.close(fd)
        if acl.state.kind != "none":
            return "pending", "workspace-root-acl: the workspace root carries an ACL a new root inode cannot keep"
        need = sum(entry.get("size", 0) for entry in manifest["entries"]) \
            + int(self.config["minimum_free_mb"]) * 1024 * 1024
        try:
            free = self.artifact_free_bytes(parent)
        except OSError as exc:
            return "pending", f"workspace-capacity: free space cannot be measured ({exc.strerror})"
        if free < need:
            mib = 1024 * 1024
            return "pending", f"workspace-capacity: {-(-need // mib)} MiB needed, {free // mib} MiB free"
        return "switch", None

    def workspace_plan(self, mode, reason=None):
        """$defs.workspace_plan of a new operation (section 3.7; AM-4)."""
        if mode in ("switch", "pending"):
            return {"mode": mode, "generation_id": f"wsg-{utc()}-{uuid.uuid4().hex[:8]}",
                    "container": str(pf_instance.generation_container(self.context)),
                    "reason": (reason or "")[:200] if mode == "pending" else None}
        return {"mode": mode, "generation_id": None, "container": None, "reason": None}

    def workspace_decision(self, manifest, keep_workspace):
        """(workspace plan, confirmation summary lines) for deploy/update/rollback/restore-instance."""
        if keep_workspace:
            return self.workspace_plan("keep"), ["Workspace refresh: kept (--keep-workspace)"]
        mode, reason = self.workspace_preflight(manifest)
        container, generations, _ = self.generation_listing()
        size = 0
        for name in generations:
            for current, _, files in os.walk(str(container / name)):
                for file_name in files:
                    try:
                        size += os.lstat(os.path.join(current, file_name)).st_size
                    except OSError:
                        pass
        try:
            free = self.artifact_free_bytes(self.root.parent) // (1024 * 1024)
        except OSError:
            free = "unknown"
        lines = ["Workspace refresh: generation switch" if mode == "switch" else
                 f"Workspace refresh: unavailable ({reason}); the workspace is left untouched and the operation ends "
                 "in workspace_sync_pending until you decide",
                 f"Retained workspace generations: {len(generations)} ({size // (1024 * 1024)} MiB, unsealed, never "
                 f"deleted); free space on the workspace device: {free} MiB"]
        return self.workspace_plan(mode, reason), lines

    def workspace_effect_specs(self, workspace, *, deployment_id, tree_digest=None):
        """The W1-W4 effect specs of a switch or pending plan (section 3.7), else []."""
        generation = workspace["generation_id"]
        if workspace["mode"] not in ("switch", "pending"):
            return []
        stage = {"phase": "syncing-workspace", "type": "source-stage", "target": "workspace:stage:" + generation,
                 "postcondition": f"stage tree equals deployment {deployment_id} entries",
                 "preconditions": ["workspace preflight available"]
                 + ([f"workspace-tree:{tree_digest}"] if tree_digest else [])}
        return [stage,
                {"phase": "syncing-workspace", "type": "source-switch", "target": "workspace:retain:" + generation,
                 "postcondition": f"old workspace is the retained generation {generation}",
                 "preconditions": ["workspace is the old tree", "the retained name is absent"]},
                {"phase": "syncing-workspace", "type": "source-switch", "target": "workspace:bind:" + generation,
                 "postcondition": "workspace is the staged tree",
                 "preconditions": ["workspace path absent", "stage is the new tree"]},
                {"phase": "syncing-workspace", "type": "file-write", "target": "source-manifest",
                 "postcondition": "bytes sha256 recorded at completion", "preconditions": ["workspace is the new tree"]}]

    @contextlib.contextmanager
    def workspace_fds(self):
        """(parent descriptor, container descriptor or None) of the workspace parent and the generation container."""
        parent_fd = os.open(str(self.root.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        container_fd = None
        try:
            name = pf_instance.GENERATION_CONTAINER_PREFIX + self.context.instance_id
            try:
                container_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                       dir_fd=parent_fd)
            except FileNotFoundError:
                container_fd = None
            yield parent_fd, container_fd
        finally:
            if container_fd is not None:
                os.close(container_fd)
            os.close(parent_fd)

    def workspace_observation(self):
        """{"W", "CG", "S"} identities of the workspace, the retained generation and the stage (section 3.7)."""
        generation = self.plan["workspace"]["generation_id"]
        with self.workspace_fds() as (parent_fd, container_fd):
            return {"W": pf_instance.identity_at(parent_fd, self.root.name),
                    "CG": pf_instance.identity_at(container_fd, generation) if container_fd is not None else None,
                    "S": pf_instance.identity_at(container_fd, "stage-" + generation)
                    if container_fd is not None else None}

    def workspace_evidence(self):
        """The old/new identities recorded by the W effects' evidence."""
        found = {}
        for effect_id in pf_config.workspace_effect_ids(self.plan) or ():
            found.update(self.identities(self.journal_effect(effect_id)["evidence"]))
        return found

    @staticmethod
    def identity_text(identity):
        return f"{identity[0]}:{identity[1]}" if identity else "none"

    def workspace_tree(self):
        """(tree, manifest of the provenance W4 records, expected entries digest) of the workspace switch: the
        operation's deployed tree, or the restore bundle's workspace archive tree (unknown provenance)."""
        effect = self.effects_of("workspace", type="source-stage")[0]
        archive_digest = self.precondition(effect, "workspace-tree")
        if archive_digest is not None:
            tree = self.restore_workspace_tree()
            manifest = pf_source.build_manifest(tree, source={"kind": "unknown"}, excludes=SOURCE_EXCLUDES)
            return tree, manifest, archive_digest
        tree = self.deployed_tree()
        return tree, self.deployed_manifest(), self.plan["source"]["entries_sha256"]

    def w1_stage(self, step):
        """W1: the staged new workspace tree in the generation container (same filesystem as the workspace)."""
        generation = self.plan["workspace"]["generation_id"]
        stage = "stage-" + generation
        tree, manifest, expected = self.workspace_tree()
        with self.workspace_fds() as (parent_fd, container_fd):
            old = pf_instance.identity_at(parent_fd, self.root.name)
            if old is None:
                raise Failure("workspace-generation-mismatch: the workspace is absent before its stage was built; "
                              "nothing was moved or deleted.")
            step.evidence = f"old:{self.identity_text(old)}"
            name = pf_instance.GENERATION_CONTAINER_PREFIX + self.context.instance_id
            if container_fd is None:
                pf_instance.create_generation_container(parent_fd, name)
            else:
                problem = pf_instance.generation_container_problem(parent_fd, name, os.fstat(parent_fd).st_dev)
                if problem is not None:
                    raise Failure(f"generation-container-unsafe: {problem}; nothing was moved or deleted.")
        with self.workspace_fds() as (parent_fd, container_fd):
            if pf_instance.identity_at(container_fd, stage) is not None:
                pf_instance.remove_private_tree_at(container_fd, stage)
            os.mkdir(stage, 0o700, dir_fd=container_fd)
        target = pf_instance.generation_container(self.context) / stage
        self.publish_source_tree(tree, manifest, target)
        digest = pf_source.entries_digest(pf_source.build_manifest(target, source={"kind": "unknown"},
                                                                   excludes=SOURCE_EXCLUDES))
        if digest != expected:
            raise Failure(f"workspace-stage-mismatch: the staged workspace tree {digest[:12]} is not the selected "
                          f"tree {expected[:12]}; the workspace was not changed.")
        os.sync()
        with self.workspace_fds() as (parent_fd, container_fd):
            new = pf_instance.identity_at(container_fd, stage)
        step.evidence = f"old:{self.identity_text(old)} new:{self.identity_text(new)}"

    def w2_retain(self, step):
        """W2: rename the old workspace to the retained generation (descriptor-relative, no-clobber, fsync both)."""
        generation = self.plan["workspace"]["generation_id"]
        recorded = self.workspace_evidence()
        old, new = recorded.get("old"), recorded.get("new")
        step.evidence = f"old:{self.identity_text(old)} new:{self.identity_text(new)}"
        with self.workspace_fds() as (parent_fd, container_fd):
            if old is None or pf_instance.identity_at(parent_fd, self.root.name) != old \
                    or pf_instance.identity_at(container_fd, generation) is not None:
                raise Failure(self.workspace_mismatch(self.workspace_observation()))
            pf_instance.rename_noreplace_at(parent_fd, self.root.name, container_fd, generation)
        step.retained.append({"kind": "workspace-generation", "name": generation, "sha256": None})

    def w3_bind(self, step):
        """W3: bind the staged tree as the workspace (the name must be absent, even an empty directory refuses)."""
        generation = self.plan["workspace"]["generation_id"]
        recorded = self.workspace_evidence()
        old, new = recorded.get("old"), recorded.get("new")
        step.evidence = f"old:{self.identity_text(old)} new:{self.identity_text(new)}"
        with self.workspace_fds() as (parent_fd, container_fd):
            if pf_instance.identity_at(parent_fd, self.root.name) is not None \
                    or pf_instance.identity_at(container_fd, "stage-" + generation) != new:
                raise Failure(self.workspace_mismatch(self.workspace_observation()))
            pf_instance.rename_noreplace_at(container_fd, "stage-" + generation, parent_fd, self.root.name)

    def w4_manifest(self, step):
        """W4: the protected source manifest of the new workspace, only after a proven bind."""
        _, manifest, _ = self.workspace_tree()
        digest_value = self.write_workspace_manifest(manifest)
        step.evidence = f"bytes sha256 {digest_value}"

    def workspace_mismatch(self, observed, *, keep=False):
        recorded = self.workspace_evidence()

        def describe(label, identity):
            if identity is None:
                return f"{label} absent"
            if identity == recorded.get("old"):
                return f"{label} is the old workspace"
            if identity == recorded.get("new"):
                return f"{label} is the staged tree"
            return f"{label} is an unknown inode {self.identity_text(identity)}"

        op = self.operation_id
        steps = f"pf --instance {self.context.slug} resume --operation {op} --keep-workspace" + (
            "" if observed.get("W") is not None else
            " (recreate the workspace directory as root first: nothing can be rebound)")
        return (f"workspace-generation-mismatch: the workspace switch of operation {op} found "
                f"{describe('the workspace', observed.get('W'))}, {describe('the retained generation', observed.get('CG'))}, "
                f"{describe('the stage', observed.get('S'))}; nothing was moved or deleted. Supported next steps: "
                f"{steps}. Nothing was changed.")

    def workspace_validation(self):
        """Section 3.7a after W3 (or a keep rebind): the registered workspace validates again."""
        validation = pf_instance.validate_context(self.context, running_release=self.running_release)
        workspace = str(self.context.paths.workspace)
        found = [finding for finding in validation.findings
                 if finding.severity == "refuse" and str(finding.path) == workspace]
        if found:
            message = (f"workspace-validation-failed: after the workspace switch of operation {self.operation_id} "
                       f"the registered workspace does not validate ({found[0].code}: {found[0].message}); the source "
                       f"manifest was not rewritten. Fix the finding, then run 'pf --instance {self.context.slug} "
                       f"resume --operation {self.operation_id}'.")
            self.journal_update(last_error={"code": "workspace-validation-failed", "message": message[:2000]})
            raise Failure(message)

    def run_workspace(self):
        """Section 3.7 at the syncing point: the preflight again, then W1-W4 (each observed when an earlier process
        left it unknown), or workspace_sync_pending when the switch is unavailable."""
        ids = pf_config.workspace_effect_ids(self.plan)
        if ids is None or all(self.effect_state(effect_id) == "complete" for effect_id in ids):
            return
        if self.effect_state(ids[0]) == "not_started":
            _, manifest, _ = self.workspace_tree()
            mode, reason = self.workspace_preflight(manifest)
            if mode != "switch":
                deployment = self.plan["source"]["deployment_id"]
                message = (f"workspace-sync-pending: deployment {deployment} is active and recorded, but the workspace "
                           f"was not refreshed ({reason}). Operation {self.operation_id} stays open in "
                           f"workspace_sync_pending. Run 'pf --instance {self.context.slug} resume --operation "
                           f"{self.operation_id}' after fixing the cause, or add --keep-workspace to keep the current "
                           "workspace.")
                self.journal_update(phase="workspace_sync_pending",
                                    last_error={"code": "workspace-sync-pending", "message": message[:2000]})
                raise Failure(message)
        # W1's intent generation records the old workspace identity, so a crash inside W1 can still be reconciled
        # against it (the evidence of an earlier attempt keeps the identity it recorded first).
        old = self.workspace_evidence().get("old")
        if old is None:
            with self.workspace_fds() as (parent_fd, _):
                old = pf_instance.identity_at(parent_fd, self.root.name)
        self.run_effect(ids[0], self.w1_stage, evidence=f"old:{self.identity_text(old)}" if old else None)
        self.run_effect(ids[1], self.w2_retain)
        self.run_effect(ids[2], self.w3_bind)
        if self.effect_state(ids[3]) != "complete":
            self.workspace_validation()
        self.run_effect(ids[3], self.w4_manifest)

    def observe_workspace(self, effect):
        """Section 3.7 reconciliation of an unknown W effect: (classification, detail, cleanup)."""
        ids = pf_config.workspace_effect_ids(self.plan)
        generation = self.plan["workspace"]["generation_id"]
        observed = self.workspace_observation()
        evidence = self.workspace_evidence()
        index = ids.index(effect["effect_id"])
        if index == 3:
            _, manifest, _ = self.workspace_tree()
            try:
                current = pf_instance.read_bytes_nofollow(self.context.source_manifest_path)
            except OSError:
                current = None
            # pf_source.write_manifest writes manifest_bytes plus one newline.
            if current == pf_source.manifest_bytes(manifest) + b"\n":
                return "complete", f"bytes sha256 {pf_instance.sha256_bytes(current)}", None
            return "redo", "the source manifest is not the new tree's", None
        if index == 0:
            stage = observed.get("S")
            if observed.get("W") == evidence.get("old") and observed.get("CG") is None and stage is not None:
                target = pf_instance.generation_container(self.context) / ("stage-" + generation)
                _, _, expected = self.workspace_tree()
                try:
                    digest = pf_source.entries_digest(pf_source.build_manifest(
                        target, source={"kind": "unknown"}, excludes=SOURCE_EXCLUDES))
                except (pf_source.SourceError, OSError):
                    digest = None
                if digest == expected:
                    return "complete", (f"old:{self.identity_text(evidence.get('old'))} "
                                        f"new:{self.identity_text(stage)}"), None
            if observed.get("W") == evidence.get("old") and observed.get("CG") is None:
                return "redo", "stage incomplete", self.remove_own_stage
            return "refuse", self.workspace_mismatch(observed), None
        row = pf_config.workspace_reconcile(evidence, observed)
        text = f"old:{self.identity_text(evidence.get('old'))} new:{self.identity_text(evidence.get('new'))}"
        if index == 1:
            if row == "retain-not-started":
                return "redo", "retain not started", None
            if row in ("between-renames", "bind-done"):
                return "complete", text, None
            if row == "stage-incomplete":
                return "redo", "stage incomplete", self.restage
            return "refuse", self.workspace_mismatch(observed), None
        if row == "between-renames":
            return "redo", "crash between the renames", None
        if row == "bind-done":
            return "complete", text, None
        return "refuse", self.workspace_mismatch(observed), None

    def remove_own_stage(self):
        """Remove this operation's own workspace stage, descriptor-relative inside the container (never anything
        else)."""
        generation = self.plan["workspace"]["generation_id"]
        with self.workspace_fds() as (_, container_fd):
            if container_fd is not None and pf_instance.identity_at(container_fd, "stage-" + generation) is not None:
                pf_instance.remove_private_tree_at(container_fd, "stage-" + generation)

    def restage(self):
        """Row "stage incomplete" under a W2 redo: remove the own partial stage and build it again (W1's body)."""
        self.remove_own_stage()
        step = EffectStep(self.plan_effect(pf_config.workspace_effect_ids(self.plan)[0]))
        self.w1_stage(step)
        self.journal_update(effects={step.effect["effect_id"]: ("complete", utc(), step.evidence)})

    def keep_workspace_row(self):
        """(section 3.7 reconciliation row or None when no W effect started, observation) for ``--keep-workspace``;
        raises its refusal (bind done; a foreign change with nothing to rebind). Read-only, so ``resume`` decides it
        before the confirmation."""
        ids = pf_config.workspace_effect_ids(self.plan)
        observed = self.workspace_observation()
        if all(self.effect_state(effect_id) == "not_started" for effect_id in ids):
            return None, observed
        evidence = self.workspace_evidence()
        row = pf_config.workspace_reconcile(evidence, observed) if evidence.get("old") else "stage-incomplete"
        if row == "bind-done":
            raise Failure(f"keep-workspace-not-legal: operation {self.operation_id} is in "
                          f"{self.journal['phase']}; --keep-workspace applies only to the workspace refresh. "
                          "Nothing was changed.")
        if row == "foreign" and observed.get("W") is None:
            raise Failure(self.workspace_mismatch(observed))
        return row, observed

    def keep_workspace(self):
        """``resume --keep-workspace`` (section 3.6/3.7): leave or rebind the old tree, remove the own stage, record
        the W effects ``kept by operator`` and close. A retained generation that W2 moved but did not journal is linked
        to the journal when the old tree stays there."""
        ids = pf_config.workspace_effect_ids(self.plan)
        generation = self.plan["workspace"]["generation_id"]
        row, observed = self.keep_workspace_row()
        rebound = False
        retained = []
        if row is not None:
            if row in ("between-renames", "stage-lost"):
                with self.workspace_fds() as (parent_fd, container_fd):
                    pf_instance.rename_noreplace_at(container_fd, generation, parent_fd, self.root.name)
                rebound = True
            elif observed.get("CG") is not None and observed.get("CG") == self.workspace_evidence().get("old"):
                # The old tree stays in the container as the retained generation (W2 renamed it); record it as W2's
                # completion would have.
                retained = [{"kind": "workspace-generation", "name": generation, "sha256": None}]
            self.remove_own_stage()
        if rebound:
            self.workspace_validation()
        note = "kept by operator" + (" (old tree rebound)" if rebound else "")
        self.journal_update(effects={effect_id: ("complete", utc(), note) for effect_id in ids}, unresolved=None,
                            retained=retained)
        log("Workspace kept: it was not refreshed (manifest unchanged; provenance as observed).")
        self.finish_operation()

    # ------------------------------------------- staging and the deployed tree (PF-A3.2 sections 3.6, 3.13)

    def remove_own_staging(self):
        """An operation's own unsealed ``.staging-<dep>`` (descriptor-relative); a sealed deployment is never
        removed."""
        dep = self.plan["source"]["deployment_id"] if self.plan is not None else None
        if not dep or not real_directory(self.deployments_dir):
            return
        fd = os.open(str(self.deployments_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = None
            try:
                info = os.stat(".staging-" + dep, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            if info is not None and stat.S_ISDIR(info.st_mode):
                pf_instance.remove_private_tree_at(fd, ".staging-" + dep)
        finally:
            os.close(fd)

    def sweep_staging(self, index=None):
        """Section 3.13 (OD-A31-13): remove every ``.staging-<dep>`` no blocking plan references, except the staging of
        the running deployment after a seal failure; links and non-directories are reported, never removed."""
        if not real_directory(self.deployments_dir):
            return
        index = index if index is not None else self.operation_index()
        # A superseded operation's staging stays referenced only while its supersession chain is open (a withdrawn
        # recovery gives it the blocking role back); once the chain ends in a closed recovery it is never reopened.
        referenced = {entry.plan["source"]["deployment_id"] for entry in index.entries
                      if entry.plan is not None and (entry.cls in ("blocking", "invalid") or (
                          entry.cls == "superseded" and not pf_config.superseded_and_closed(index, entry)))}
        if self.plan is not None:
            referenced.add(self.plan["source"]["deployment_id"])
        active = None
        pointer = self.read_pointer() or {}
        failed_op = pointer.get("deployment_seal_failed")
        if isinstance(failed_op, str) and OPERATION_ID_RE.fullmatch(failed_op):
            try:
                record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                    self.context.operations_dir / failed_op / "deployment-artifact.json"), label="deployment-artifact")
                if isinstance(record, dict) and record.get("state") == "seal-failed":
                    active = record.get("deployment_id")
            except (OSError, pf_instance.ContextError):
                active = None
        fd = os.open(str(self.deployments_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for name in sorted(os.listdir(fd)):
                if not name.startswith(".staging-") or not DEPLOYMENT_ID_RE.fullmatch(name[len(".staging-"):]):
                    continue
                dep = name[len(".staging-"):]
                if dep in referenced:
                    continue
                if dep == active:
                    log(f"note: unsealed-active-staging: {dep}: the staging of the running deployment (seal failed in "
                        f"operation {failed_op}) is kept.")
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    log(f"note: staging-not-removed: {name} is not a directory (a link or file is reported, never "
                        "removed).")
                    continue
                pf_instance.remove_private_tree_at(fd, name)
                log(f"note: staging-removed: {dep}")
        finally:
            os.close(fd)

    def deployment_folder(self, dep):
        """The sealed ``deployments/<dep>`` or, before the seal, ``.staging-<dep>`` (real directories only)."""
        for name in (dep, ".staging-" + dep):
            folder = self.deployments_dir / name
            if real_directory(folder):
                return folder
        raise Failure(f"plan-input-changed: the deployed source {dep} of operation {self.operation_id} is neither "
                      "staged nor sealed. Nothing was changed.")

    def deployed_manifest(self):
        """The staged (or sealed) source manifest of this operation's deployment, with its proven provenance."""
        folder = self.deployment_folder(self.plan["source"]["deployment_id"])
        try:
            return pf_source.load_manifest(folder / "source-manifest.json", pf_instance.parse_strict_json)
        except (pf_source.SourceError, pf_instance.ContextError, OSError) as exc:
            raise Failure(f"plan-input-changed: the staged source manifest of operation {self.operation_id} is "
                          f"unusable ({exc}). Nothing was changed.") from exc

    def deployed_tree(self):
        """Section 3.6 source rule: the operation's deployed tree, re-extracted from its own staged or sealed
        ``source.tar.gz`` into a private temporary directory under state/; its entries digest must equal the plan's
        ``source.entries_sha256``. Cached for this process, removed by end_operation."""
        if self._deployed_tree is not None:
            return self._deployed_tree[1]
        folder = self.deployment_folder(self.plan["source"]["deployment_id"])
        self.ensure_state_dir()
        temporary = Path(tempfile.mkdtemp(prefix="deployed-tree-", dir=str(self.state)))
        self._deployed_tree = (temporary, temporary / "tree")
        fd = os.open(str(folder / "source.tar.gz"), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try:
                inventory = pf_source.inspect_archive(fd, limits=pf_source.SOURCE_LIMITS)
                parent_fd = os.open(str(temporary), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    manifest = pf_source.extract_archive(fd, parent_fd, "tree", inventory,
                                                         limits=pf_source.SOURCE_LIMITS)
                finally:
                    os.close(parent_fd)
            except pf_source.ArchiveRefused as exc:
                raise self.archive_failure("source.tar.gz", exc) from exc
        finally:
            os.close(fd)
        if pf_source.entries_digest(manifest) != self.plan["source"]["entries_sha256"]:
            raise Failure(f"plan-input-changed: the deployed source of operation {self.operation_id} no longer "
                          "matches its plan (entries digest). Nothing was changed.")
        return self._deployed_tree[1]

    def release_deployed_tree(self):
        if self._deployed_tree is None:
            return
        temporary = self._deployed_tree[0]
        self._deployed_tree = None
        try:
            fd = os.open(str(temporary.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return
        try:
            pf_instance.remove_private_tree_at(fd, temporary.name)
        except OSError as exc:
            log(f"note: a temporary deployed tree could not be removed ({exc.strerror or exc}): {temporary}")
        finally:
            os.close(fd)

    def ensure_state_dir(self):
        if not self.state.is_dir():
            self.state.mkdir(mode=0o700)
            os.chmod(self.state, 0o700)

    # ------------------------------------------- restart observation and the still-running probe (section 3.5)

    @staticmethod
    def database_target(target):
        """(name, heads or None, flag or None) of a ``database:<name>[:heads=<h>,...|:allow_connections=false]``
        target."""
        parts = target.split(":")
        name = parts[1] if len(parts) > 1 else ""
        heads = flag = None
        for part in parts[2:]:
            if part.startswith("heads="):
                heads = sorted(item for item in part[len("heads="):].split(",") if item)
            elif part.startswith("allow_connections="):
                flag = part
        return name, heads, flag

    def database_effect_unresolved(self, plan, journal):
        """Whether the unresolved effect (or, for a superseded operation, any unknown/partial effect) is a database
        effect (section 3.5 part (c))."""
        unresolved = journal["unresolved_effect"]
        effects = [effect for effect in plan["effects"]
                   if (effect["effect_id"] == unresolved) or (unresolved is None and pf_config.effect_state(
                       journal, effect["effect_id"]) in ("unknown", "partial"))]
        # PF-A3.3: a topology's database effect runs in the isolated server, never in the instance's.
        return any(effect["type"].startswith("database-") and not effect["target"].startswith("topology:")
                   for effect in effects)

    def db_running(self):
        try:
            return bool(self.inspect("db")["State"].get("Running"))
        except DaemonFailure:
            raise
        except Failure:
            return False

    def still_running(self, entry, *, database_effects=None):
        """Section 3.5 still-running probe over ``entry`` (read-only; never kills): [(kind, detail)] hits. Raises
        ``effect-probe-unavailable`` when process groups were recorded but /proc or the boot ID cannot be read."""
        hits = []
        directory = self.context.operations_dir / entry.operation_id
        unresolved = entry.journal["unresolved_effect"] if entry.journal else None
        try:
            children = pf_instance.read_private_list(directory / "children.json")
        except (OSError, pf_instance.ContextError) as exc:
            raise Failure(f"effect-probe-unavailable: operation {entry.operation_id} recorded child process groups, but "
                          f"this host cannot inspect processes (children.json: {exc}); without that probe a "
                          "still-running effect cannot be excluded. Nothing was changed.") from exc
        if children:
            current = pf_instance.boot_id()
            for child in children:
                if not isinstance(child, dict):
                    continue
                if current is None or child.get("boot_id") is None:
                    raise Failure(f"effect-probe-unavailable: operation {entry.operation_id} recorded child process "
                                  "groups, but this host cannot inspect processes (boot ID unreadable); without that "
                                  "probe a still-running effect cannot be excluded. Nothing was changed.")
                if child["boot_id"] != current:
                    continue
                members = pf_runner.group_members(child["pgid"])
                if members is None:
                    raise Failure(f"effect-probe-unavailable: operation {entry.operation_id} recorded child process "
                                  "groups, but this host cannot inspect processes (/proc unavailable); without that "
                                  "probe a still-running effect cannot be excluded. Nothing was changed.")
                if members:
                    ticks = pf_instance.process_start_ticks(child["pgid"])
                    if ticks is None or ticks == child.get("start_ticks"):
                        hits.append((child.get("effect_id") or unresolved,
                                     f"process group {child['pgid']} ({child.get('tool')})"))
        inventory = self.docker_inventory()
        oneoffs = set(pf_docker.owned_oneoffs(inventory))
        if oneoffs:
            running = set(line.strip() for line in self.docker("ps", "-q", "--no-trunc").splitlines() if line.strip())
            for container_id in sorted(oneoffs & running):
                hits.append((unresolved, f"one-off container {container_id[:12]}"))
        if database_effects is None:
            database_effects = entry.plan is not None and self.database_effect_unresolved(entry.plan, entry.journal)
        if database_effects and self.db_running():
            count = self.sql("postgres", "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' "
                                         "AND pid <> pg_backend_pid();")
            if count.strip() not in ("0", ""):
                hits.append((unresolved, f"database session on {self.env()['POSTGRES_DB']}"))
        return hits

    def require_not_running(self, entry, *, database_effects=None):
        hits = self.still_running(entry, database_effects=database_effects)
        if hits:
            effect_id, detail = hits[0]
            effect = next((item for item in entry.plan["effects"] if item["effect_id"] == effect_id), None) \
                if entry.plan else None
            described = f"{effect_id} ({effect['type']} {effect['target']})" if effect else str(effect_id)
            raise Failure(f"effect-still-running: effect {described} of operation {entry.operation_id} still has a "
                          f"running {detail}. resume never stops it; wait until it ends, then run 'pf --instance "
                          f"{self.context.slug} resume --operation {entry.operation_id}' again. Nothing was changed.")

    def start_db_for_observation(self):
        """Section 3.5 part (c) recovery action after the confirmation: only the db service (no writer), then
        database_ready(); a failure is ``database-unavailable`` with the operation unchanged."""
        try:
            self.compose("up", "-d", "--no-deps", "--no-build", "db")
            self.wait_health("db")
            self.database_ready()
        except DaemonFailure:
            raise
        except Failure as exc:
            unresolved = self.journal["unresolved_effect"] or "?"
            raise Failure(f"database-unavailable: the database service could not be started to observe effect "
                          f"{unresolved} of operation {self.operation_id} ({str(exc).splitlines()[0]}). The operation "
                          "is unchanged. Nothing else was changed.") from exc

    def database_names(self):
        return set(self.database_rows())

    def observe_effect(self, plan, journal, effect):
        """Section 3.5: (classification, detail, cleanup) of one unresolved effect, from the daemon, the database or
        the filesystem; never from an exit code. classification: complete | not_started | redo | needs_operator |
        refuse. ``cleanup`` (or None) runs inside the redo's own intent generation."""
        etype, target, eid = effect["type"], effect["target"], effect["effect_id"]
        role = pf_config.effect_role(effect)
        evidence = self.journal_effect(eid)["evidence"] or ""
        if role == "workspace":
            return self.observe_workspace(effect)
        if etype == "source-stage":
            dep = target.split(":", 1)[1]
            if real_directory(self.deployments_dir / dep):
                return "complete", f"sealed {dep}", None
            if os.path.lexists(str(self.deployments_dir / (".staging-" + dep))):
                return "redo", f"own staging {dep} incomplete", self.remove_own_staging
            return "not_started", "neither staged nor sealed", None
        if role == "stop":
            states = [self.inspect(service)["State"].get("Running") for service in ("frontend", "backend")]
            return ("complete", "stopped", None) if not any(states) else ("redo", "a writer still runs", None)
        if role in ("backend", "frontend", "db-start"):
            service = {"backend": "backend", "frontend": "frontend", "db-start": "db"}[role]
            try:
                state = self.inspect(service)
            except DaemonFailure:
                raise
            except Failure:
                return "redo", f"{service} absent", None
            healthy = state["State"].get("Running") and state["State"].get("Health", {}).get("Status") == "healthy"
            planned = target.rsplit(":", 1)[-1] if role != "db-start" else None
            if healthy and (planned is None or state["Image"][7:19] == planned):
                if role == "frontend":
                    try:
                        data = json.loads(self.compose("exec", "-T", "frontend", "wget", "-q", "-O", "-",
                                                       "http://127.0.0.1:5173/api/health"))
                    except (Failure, ValueError):
                        data = {}
                    if data.get("status") != "ok" or data.get("database") != "connected":
                        return "redo", "API health not ok", None
                return "complete", f"running healthy {state['Image'][7:19]}", None
            return "redo", f"{service} not running healthy on the planned image", None
        if role == "topology":
            return self.observe_side_by_side(effect)
        if self.plan["kind"] == "cleanup":
            return self.observe_cleanup(effect)
        if etype == "capture":
            return self.observe_capture(effect, evidence)
        if etype == "verification":
            return self.observe_verification(effect, evidence)
        if etype == "database-switch":
            _, current, prepared, retained = target.split(":")
            names = self.database_names()
            present = {name for name in (current, prepared, retained) if name in names}
            if present == {current, retained}:
                return "complete", f"current {current}, retained {retained}", None
            if present == {current, prepared}:
                return "not_started", f"current {current}, prepared {prepared}", None
            return "needs_operator", ", ".join(f"{name} {'present' if name in present else 'absent'}"
                                               for name in (current, prepared, retained)), None
        if etype.startswith("database-"):
            return self.observe_database(effect, evidence)
        if etype == "image-load":
            recovery = self.op_recovery()
            if self.plan["kind"] == "restore-side-by-side":
                images = self.topology_images(recovery, self.side_by_side_effect("database-restore"))
                missing = [service for service, image_id in images.items() if not self.image_present(image_id)]
            else:
                missing = [service for service, image in recovery.images.items() if image is not None
                           and not self.image_present(image["id"])]
            return ("complete", "planned image IDs present", None) if not missing else \
                ("redo", "missing " + ",".join(missing), None)
        if etype == "image-tag":
            wanted = self.precondition(effect, "db-image-id")
            local = self.local_image_id(pf_docker.DB_IMAGE)
            if local == wanted:
                return "complete", "postgres:16 " + wanted[7:19], None
            if local is None:
                return "redo", "postgres:16 absent", None
            return "refuse", self.db_image_changed(local, wanted), None
        if role == "seal":
            dep = target.split(":", 1)[1]
            view = self.sealed_view(dep)
            if view is not None:
                return "complete", f"sealed {dep}", None
            return "redo", "staging complete, seal not published", None
        if etype == "resource-delete":
            return "redo", "continue the frozen deletion plan", None
        if etype == "file-write":
            return self.observe_file(effect)
        return "redo", "re-run", None

    def image_present(self, image_id):
        try:
            self.docker("image", "inspect", image_id)
            return True
        except DaemonFailure:
            raise
        except Failure:
            return False

    def observe_capture(self, effect, evidence):
        bundle_id = self.attempt_bundle(effect, evidence)
        if effect["target"] == "purge-bundle":
            folder = self.recovery_root / bundle_id
            reader = lambda: self.verify_recovery(folder)  # noqa: E731
        else:
            folder = self.backups_dir / bundle_id
            reader = lambda: self.verify_snapshot(bundle_id)  # noqa: E731
        # A capture that verifies inline restores into the attempt's own pf_verify_* candidate; a redo drops it.
        cleanup = (lambda: self.drop_owned(self.owned_verify_names(effect, evidence)))  # noqa: E731
        if not os.path.lexists(str(folder)):
            return "not_started", f"bundle:{bundle_id} absent", cleanup
        try:
            view = reader()
        except Failure as exc:
            return "redo", f"bundle:{bundle_id} without a sealed manifest ({failure_code(exc)})", cleanup
        if self.plan["kind"] == "backup":
            return "complete", f"bundle:{bundle_id} sealed", None
        if view.level in PASSED_LEVELS:
            return "complete", f"bundle:{bundle_id} sealed and {view.level}", None
        return "redo", f"bundle:{bundle_id} sealed without a passed record", cleanup

    def owned_verify_names(self, capture, *evidences):
        """The pf_verify_* candidates this operation named for one capture: the plan's pre-assigned name and every
        name an attempt recorded in its evidence (never any other database)."""
        names = []
        texts = list(evidences) + [self.journal_effect(capture["effect_id"])["evidence"]]
        for text in texts:
            for name in re.findall(r"pf_verify_[0-9a-f]{20}", text or ""):
                if name not in names:
                    names.append(name)
        planned = self.precondition(capture, "verify")
        if planned and planned not in names:
            names.append(planned)
        return names

    def attempt_bundle(self, effect, evidence):
        """The bundle ID of the capture attempt in flight: the evidence's ``bundle:<id>``, else the plan's."""
        match = re.search(r"bundle:((?:purge-)?[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{6})", evidence or "")
        return match.group(1) if match else self.precondition(effect, "bundle")

    def observe_verification(self, effect, evidence):
        if self.plan["kind"] == "restore-side-by-side":
            return self.observe_side_by_side(effect)
        if self.plan["kind"] == "purge" and effect["postcondition"] == FUNCTIONAL_POSTCONDITION:
            # Section 3.12: a crash in the verification never infers completion; resume and abandon both tear the
            # topology down by its exact plan and reopen (the pre-deletion rule of A3.2).
            return "redo", "functional verification interrupted (the topology is torn down, the application reopened)", \
                None
        capture = self.capture_effect_for(effect)
        bundle_id = self.attempt_bundle(capture, self.journal_effect(capture["effect_id"])["evidence"])
        try:
            view = self.verify_recovery(self.recovery_root / bundle_id) if capture["target"] == "purge-bundle" \
                else self.verify_snapshot(bundle_id)
        except Failure as exc:
            return "redo", f"bundle {bundle_id} unreadable ({failure_code(exc)})", None
        if view.level in PASSED_LEVELS:
            return "complete", f"passed {view.latest_verification_id}", None
        names = self.owned_verify_names(capture, evidence)
        return "redo", f"no passed record for {bundle_id}", (lambda: self.drop_owned(names))

    def capture_effect_for(self, verification):
        captures = [effect for effect in self.plan["effects"] if effect["type"] == "capture"
                    and effect["effect_id"] < verification["effect_id"]]
        return captures[-1]

    def drop_owned(self, names):
        """Drop owned candidate databases named by the plan or evidence when they exist (dropdb of an owned name)."""
        existing = self.database_names()
        for name in names:
            if name in existing:
                self.drop_database(name)

    def observe_database(self, effect, evidence):
        etype, eid = effect["type"], effect["effect_id"]
        name, heads, flag = self.database_target(effect["target"])
        names = self.database_names()
        candidate = pf_config.CANDIDATE_RE.fullmatch(name) is not None
        if candidate:
            if etype in ("database-restore", "database-create"):
                if name in names:
                    return "redo", f"{name} exists (never inferred complete)", (lambda: self.drop_owned([name]))
                return "not_started", f"{name} absent", None
            if etype == "database-drop":
                return ("complete", f"{name} absent", None) if name not in names else ("redo", f"{name} exists", None)
            if etype == "database-migrate":
                if name in names and sorted(set(self.db_heads(name))) == heads:
                    return "complete", f"heads {','.join(heads)}", None
                return "redo", f"{name} not at heads {','.join(heads)}", (lambda: self.recreate_candidate(effect))
        if self.plan["kind"] == "restore-instance" and etype in ("database-drop", "database-restore"):
            backend = self.effects_of("backend")
            if backend and self.effect_state(backend[0]["effect_id"]) != "not_started":
                raise Failure(f"operation-journal-invalid: {self.operation_id}: the journal records effect {eid} "
                              "unresolved after the backend start, against its own plan order. The operation's state "
                              "cannot be proven, so every mutating route is refused; status, doctor, backups, "
                              "recoveries, ps and logs work. See SYNOLOGY_ADMIN §16. Nothing was changed.")
            if etype == "database-drop":
                return "redo", f"{name} {'present' if name in names else 'absent'}; the restore follows", None
            return "redo", f"target {name} redone from the bundle", (lambda: self.drop_owned([name]))
        if etype == "database-alter":
            rows = self.database_rows()
            if name in rows and not rows[name]["allow_connections"]:
                return "complete", "flag false", None
            return "redo", "flag not false", None
        if etype == "database-migrate":
            current = sorted(set(self.db_heads(name))) if name in names else []
            if current == heads:
                return "complete", f"heads {','.join(heads) or 'none'}", None
            pre = re.search(r"pre-heads:([A-Za-z0-9_,]*)", evidence)
            before = sorted(item for item in pre.group(1).split(",") if item) if pre else None
            if before is not None and current == before:
                return "needs_operator", (f"live heads {','.join(current) or 'none'} equal the pre-heads (a lost result; "
                                          "a partial non-transactional change cannot be excluded)"), None
            return "needs_operator", f"live heads {','.join(current) or 'none'} are neither the pre-heads nor the " \
                                     f"target {','.join(heads)}", None
        if etype == "database-drop":
            return ("complete", f"{name} absent", None) if name not in names else ("redo", f"{name} exists", None)
        if etype == "database-restore":
            return "redo", f"{name} redone", (lambda: self.drop_owned([name]))
        return "redo", "re-run", None

    def recreate_candidate(self, migrate):
        """A candidate whose migration is unknown is dropped and recreated by its creation effect's body (section 3.5
        'drop the candidate and redo from its creation effect')."""
        name, _, _ = self.database_target(migrate["target"])
        self.drop_owned([name])
        creation = next(effect for effect in self.plan["effects"] if effect["type"] in ("database-restore",
                                                                                        "database-create")
                        and self.database_target(effect["target"])[0] == name)
        self.effect_action(creation, {})(EffectStep(creation))

    def observe_file(self, effect):
        target = effect["target"]
        expected = None
        match = re.fullmatch(r"bytes sha256 ([0-9a-f]{64})", effect["postcondition"])
        if match:
            expected = match.group(1)
        path = self.file_target_path(target)
        if target.startswith("purge-cleanup:") or target.startswith("override:"):
            if target == "purge-cleanup:backups" and not (self.journal["deletion"] or {}).get("delete_backups"):
                return "complete", "kept", None
            if target == "purge-cleanup:admin-config" and not (self.journal["deletion"] or {}).get(
                    "reset_admin_config"):
                return "complete", "kept", None
            return ("complete", "absent", None) if not os.path.lexists(str(path)) else ("redo", "present", None)
        if target == "checkpoint-history":
            return self.observe_history(effect)
        if target.startswith("registry:state="):
            try:
                record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(self.context.record_path),
                                                       label="record.json")
            except (OSError, pf_instance.ContextError):
                record = {}
            state = target.split("=", 1)[1]
            if isinstance(record, dict) and record.get("state") == state:
                return "complete", f"state {state}", None
            return "redo", f"state {record.get('state') if isinstance(record, dict) else '?'}", None
        if target.startswith(("remove:", "generation:")):
            return self.observe_cleanup_file(effect)
        if target == "pointer:deployed.json":
            pointer = self.read_pointer() or {}
            dep = self.plan["source"]["deployment_id"]
            if pointer.get("deployment_id") == dep or pointer.get("deployment_seal_failed") == self.operation_id:
                data = pf_instance.read_bytes_nofollow(path)
                return "complete", f"bytes sha256 {pf_instance.sha256_bytes(data)}", None
            return "redo", "deployed.json does not name this deployment", None
        if target == "last-reset":
            try:
                record = load_json(path)
            except (OSError, ValueError):
                record = {}
            checkpoint = self.precondition(effect, "checkpoint")
            if record.get("checkpoint") == checkpoint:
                return "complete", "last-reset.json names the checkpoint", None
            return "redo", "last-reset.json not written", None
        try:
            data = pf_instance.read_bytes_nofollow(path)
        except OSError:
            data = None
        if data is not None and expected is not None and pf_instance.sha256_bytes(data) == expected:
            return "complete", f"bytes sha256 {expected}", None
        return "redo", "bytes differ or absent", None

    def file_target_path(self, target):
        if target == "pointer:deployed.json":
            return self.state / "deployed.json"
        if target == "config:.env":
            return self.config_dir / ".env"
        if target == "source-manifest":
            return self.context.source_manifest_path
        if target.startswith("state-file:"):
            return self.state / target.split(":", 1)[1]
        if target == "last-reset":
            return self.state / "last-reset.json"
        if target == "override:active-images.yaml":
            return self.override
        if target.startswith("purge-cleanup:"):
            return {"backups": self.backups_dir, "env": self.config_dir / ".env", "state": self.state,
                    "admin-config": self.config_dir / "pf-config.json"}[target.split(":", 1)[1]]
        return self.revisions_root / self.config["project"]

    # ------------------------------------------- authority, decisions and resume (sections 3.6, 3.8)

    def check_authority(self, plan):
        """Section 3.8: the plan's authorities equal the current protected state, else plan-authority-changed."""
        context = self.context
        producer = plan["producer"]
        permission = self.permission_policy_ref()
        compared = (
            ("instance record", plan["instance"]["record_sha256"], context.record_sha256),
            ("environment policy", f"{plan['environment_policy']['revision']}:{plan['environment_policy']['sha256']}",
             f"{context.approved_policy.revision}:{context.approved_policy.sha256}"),
            ("permission policy", json.dumps(plan["permission_policy"], sort_keys=True),
             json.dumps(permission, sort_keys=True)),
            ("control release", f"{producer['control_release_id']}:{producer['control_sha256']}",
             f"{context.control.release_id}:{context.control.sha256}"),
            ("profile", producer["profile_sha256"], context.profile.sha256),
            ("daemon engine", plan["instance"]["daemon_engine_id"], context.daemon.engine_id),
        )
        for field, recorded, current in compared:
            if recorded != current and field == "instance record" and self.own_state_write(plan):
                continue  # OD-A33-08: the operation's own registry state write is the only accepted change
            if recorded != current:
                short = lambda value: pf_instance.sha256_bytes(str(value).encode("utf-8"))[:12]  # noqa: E731
                raise Failure(f"plan-authority-changed: {field} changed after operation {plan['operation_id']} was "
                              f"approved ({short(recorded)} -> {short(current)}); resume refuses to mix authorities. "
                              f"Restore the approved {field} or have an administrator review the instance. Nothing was "
                              "changed.")

    def own_state_write(self, plan):
        """OD-A33-08: the record changed only by this operation's registry state effect (started): the current record
        with its state set back to the effect's ``record-state`` precondition and its revision decremented hashes to
        the plan's record hash. Any other change stays ``plan-authority-changed``."""
        effects = [effect for effect in plan["effects"] if pf_config.effect_role(effect) == "registry"]
        if not effects or self.journal is None:
            return False
        effect = effects[0]
        if pf_config.effect_state(self.journal, effect["effect_id"]) == "not_started":
            return False
        try:
            record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(self.context.record_path),
                                                   label="record.json")
        except (OSError, pf_instance.ContextError):
            return False
        before = self.precondition(effect, "record-state")
        if not isinstance(record, dict) or not before or record.get("state") != effect["target"].split("=", 1)[1] \
                or not isinstance(record.get("record_revision"), int) or record["record_revision"] < 2:
            return False
        original = dict(record, state=before, record_revision=record["record_revision"] - 1)
        return pf_instance.sha256_bytes(pf_instance.normalize_json(original)) == plan["instance"]["record_sha256"]

    def resume_phrase(self, action):
        op8 = self.operation_id[-8:]
        kind, project = self.plan["kind"], self.context.compose_project
        if action == "abandon" and kind == "restore-instance":
            return f"ABANDON RESTORE {project} {op8}"
        if action == "abandon" and kind == "restore-side-by-side":
            targets = self.operation_topologies()
            return f"ABANDON RECOVERY TARGET {targets[0][0] if targets else op8}"
        if action == "abandon":
            return f"ABANDON {op8}"
        if action == "keep-workspace":
            return f"KEEP WORKSPACE {op8}"
        if action == "forward" and kind == "purge" and self.journal["deletion"] is not None:
            return f"RESUME PURGE {project} {self.plan_bundle_id()}"
        if action == "forward" and kind == "abort-deploy":
            return f"RESUME ABORT DEPLOY {project}"
        return f"RESUME {op8}"

    def plan_bundle_id(self):
        capture = self.effects_of(type="capture", target="purge-bundle")
        return self.precondition(capture[0], "bundle") if capture else "?"

    def resume_operation(self, operation_id=None, *, abandon=False, keep_workspace=False, alias=None):
        """``pf resume [--operation ID] [--abandon | --keep-workspace]`` and the aliases (section 3.6): authority,
        probe, observation, decision and one typed confirmation before the attempt is recorded; then exactly one
        action. A refused or declined resume changes no operation file."""
        self.staging()
        entry = self.gate.entry
        plan, journal = self.plan, self.journal
        kind, phase, op = plan["kind"], journal["phase"], self.operation_id
        self.check_authority(plan)
        if kind == "restore-instance" and journal["deletion"] is not None and not abandon:
            raise Failure(f"abandon-in-progress: operation {op} (restore-instance) is being abandoned with a frozen "
                          f"deletion plan; only 'pf --instance {self.context.slug} resume --operation {op} --abandon' "
                          "continues it. Nothing was changed.")
        database_effect = self.database_effect_unresolved(plan, journal)
        self.require_not_running(entry, database_effects=database_effect)
        deferred = database_effect and not self.db_running()
        observed = None
        # An abandon decides from the journal alone (section 3.6); it never needs (or re-reads the inputs of) the
        # unresolved effect's observation.
        if journal["unresolved_effect"] is not None and not deferred and not abandon:
            effect = self.plan_effect(journal["unresolved_effect"])
            observed, detail, _ = self.observe_effect(plan, journal, effect)
            log(f"observed: {effect['effect_id']} {effect['type']} {effect['target']}: {observed} ({detail})")
            # A refusing observation (workspace-generation-mismatch, checkpoint-history-unknown) is a refusal check of
            # section 3.1 step 7: decided before the confirmation and the attempt entry. --keep-workspace decides its
            # own section 3.7 row below.
            if observed == "refuse" and not keep_workspace:
                raise Failure(detail)
        decision = pf_config.resume_decision(plan, journal, observed)
        if abandon and not decision.abandon_legal:
            reached = [effect for effect in plan["effects"]
                       if pf_config.effect_state(journal, effect["effect_id"]) != "not_started"]
            last = reached[-1] if reached else plan["effects"][0]
            steps = "; ".join(journal["legal_next"]) or "none"
            raise Failure(f"abandon-not-legal: operation {op} ({kind}) is past effect {last['effect_id']} "
                          f"({last['type']} {last['target']}); abandoning would leave {decision.reason} without its "
                          f"recovery. Legal next: {steps}. Nothing was changed.")
        if keep_workspace and not decision.keep_legal:
            raise Failure(f"keep-workspace-not-legal: operation {op} is in {phase}; --keep-workspace applies only to "
                          "the workspace refresh. Nothing was changed.")
        if keep_workspace:
            self.keep_workspace_row()  # the bind-done and foreign-without-workspace rows refuse here (read-only)
        action = "abandon" if abandon else "keep-workspace" if keep_workspace else decision.action
        body = self.abandon_body(decision) if action == "abandon" else None
        label = {"forward": "forward", "reopen": "reopen unchanged deployment", "withdraw": "withdraw (no reopen)",
                 "close": "close", "abandon": "abandon", "keep-workspace": "keep workspace",
                 "needs_operator": "forward"}[action]
        log(f"Resuming operation {op} ({kind}, phase {phase}): {label}")
        if deferred:
            log(f"note: start the database service, then observe {journal['unresolved_effect']} and continue")
        if action == "forward" and kind == "purge" and journal["deletion"] is not None:
            self.prepare_purge_resume()
        if action == "forward" and kind == "abort-deploy":
            self.prepare_abort_resume()
        if action == "forward":
            self.prefetch_inputs()
        if action == "abandon" and kind == "restore-instance":
            deletion_plan = self.prepare_restore_abandon()
        else:
            deletion_plan = None
        confirm(self.resume_phrase(action), self.resume_summary(action, decision, body=body))
        if deferred and (action not in ("abandon", "keep-workspace") or body in ("reopen", "withdraw")):
            # Section 3.5 part (c): only the db service, after the confirmation and before any operation file
            # changes; a failing start leaves the operation unchanged (database-unavailable, no fail-closed).
            self.start_db_for_observation()
        self._reentered = True
        self._append_attempt({"forward": "resume-forward", "reopen": "reopen-unchanged", "withdraw": "reopen-unchanged",
                              "close": "abandon", "abandon": "abandon", "keep-workspace": "keep-workspace",
                              "needs_operator": "resume-forward"}[action] if alias is None else f"alias:{alias}",
                             route=alias or "resume")
        self.journal_update(approval={"plan_sha256": self.plan_sha256, "confirmed_at": utc(),
                                      "method": "typed-phrase"})
        if deferred:
            if journal["unresolved_effect"] is not None and action not in ("abandon", "keep-workspace"):
                effect = self.plan_effect(journal["unresolved_effect"])
                observed, detail, _ = self.observe_effect(self.plan, self.journal, effect)
                log(f"observed: {effect['effect_id']} {effect['type']} {effect['target']}: {observed} ({detail})")
                if observed == "needs_operator":
                    raise self.write_needs_operator(effect, detail)
                decision = pf_config.resume_decision(self.plan, self.journal, observed)
                action = decision.action
        if action == "needs_operator":
            effect = self.plan_effect(journal["unresolved_effect"])
            _, detail, _ = self.observe_effect(self.plan, self.journal, effect)
            raise self.write_needs_operator(effect, detail)
        if action == "keep-workspace":
            return self.keep_workspace()
        if action == "abandon" and kind == "restore-instance":
            return self.abandon_restore_instance(deletion_plan)
        if action == "abandon" and kind == "restore-side-by-side":
            return self.abandon_side_by_side()
        if action == "abandon" and kind == "cleanup":
            return self.abandon_cleanup()
        if action == "abandon":
            action = body
        if action == "close":
            return self.close_abandoned()
        if action == "withdraw":
            return self.withdraw_superseding()
        if action == "reopen":
            return self.reopen_and_cancel()
        return self.run_plan({})

    def abandon_body(self, decision):
        """Section 3.6 ``--abandon`` (legal rows only): what it does besides closing ``cancelled``. ``close`` (nothing
        outside private files, a backup), ``reopen`` (owned candidates dropped, a purge's connection flags restored,
        the unchanged deployment reopened) or ``withdraw`` (a superseding operation: owned candidates dropped,
        services left as they are). restore-instance has its own abandon (None)."""
        if self.plan["kind"] in ("restore-instance", "restore-side-by-side", "cleanup"):
            return None
        if self.plan["kind"] == "backup" or decision.action == "close":
            return "close"
        if decision.action in ("reopen", "withdraw"):
            return decision.action
        # The forward rows where abandon is legal (a reset-db or rollback candidate): drop it, then reopen/withdraw.
        return "withdraw" if self.plan["supersedes"] is not None else "reopen"

    def prefetch_inputs(self):
        """Section 3.6 Inputs, before the confirmation: the strict re-read of ``plan.input_bundle`` when a remaining
        forward step reads it (the rollback's selected checkpoint for its candidate restore and activation; the
        restore bundle for every remaining step but the seal, the pointer and a workspace switch of the source
        tree). A changed input refuses ``plan-input-changed`` with no operation file changed."""
        kind = self.plan["kind"]
        if self.plan["input_bundle"] is None or kind not in ("rollback", "restore-instance"):
            return
        stage = self.effects_of("workspace", type="source-stage")
        bundle_tree = kind == "restore-instance" and bool(stage) and \
            self.precondition(stage[0], "workspace-tree") is not None
        for effect in self.plan["effects"]:
            if self.effect_state(effect["effect_id"]) == "complete":
                continue
            role = pf_config.effect_role(effect)
            if role in ("seal", "pointer") or (role == "workspace" and not bundle_tree):
                continue
            (self.op_selected if kind == "rollback" else self.op_recovery)()
            return

    def resume_summary(self, action, decision, *, body=None):
        op = self.operation_id
        if action == "abandon" and body == "reopen":
            return (f"Abandon operation {op}: owned candidates are dropped, the unchanged deployment is reopened and "
                    "the operation is cancelled.")
        if action == "abandon" and body == "withdraw":
            return (f"Abandon operation {op}: owned candidates are dropped, its own staging is removed, application "
                    "services stay as they are and the superseded operation's routes apply again.")
        text = {"forward": f"Continue operation {op} forward from its journal ({decision.reason}).",
                "reopen": f"Reopen the unchanged deployment and cancel operation {op} (no data or source effect "
                          "started; owned candidates are dropped).",
                "withdraw": f"Withdraw operation {op}: its own staging is removed, application services stay as they "
                            "are and the superseded operation's routes apply again.",
                "close": f"Close operation {op} (only private files were written).",
                "abandon": f"Abandon operation {op}.",
                "keep-workspace": f"Keep the current workspace for operation {op}; it is not refreshed.",
                "needs_operator": f"Observe operation {op}."}[action]
        return text

    def close_abandoned(self):
        """``--abandon`` / close where only private effects (or a backup's capture/verification) happened."""
        if self.plan["kind"] == "backup":
            for effect in self.effects_of(type="capture"):
                bundle_id = self.attempt_bundle(effect, self.journal_effect(effect["effect_id"])["evidence"])
                folder = self.backups_dir / bundle_id if bundle_id else None
                if folder is not None and os.path.lexists(str(folder)) \
                        and self.effect_state(effect["effect_id"]) != "complete":
                    self.journal_update(retained=[{"kind": "bundle-attempt", "name": bundle_id, "sha256": None}])
        self.close_operation("cancelled")
        log(f"Operation {self.operation_id} cancelled.")
        return 0

    def withdraw_superseding(self):
        """Section 3.6: a superseding operation never reopens; it drops its owned candidates (a rollback abandoned in
        its candidate restore), removes its own staging, closes cancelled and leaves the application services as they
        are. The superseded operation's routes apply again."""
        if any(pf_config.effect_role(effect) == "candidate" and self.effect_state(effect["effect_id"]) != "not_started"
               for effect in self.plan["effects"]):
            self.database_ready()
            self.drop_owned(self.owned_candidates())
        self.close_operation("cancelled")
        superseded = self.operation_index().entry(self.plan["supersedes"])
        log(f"Operation {self.operation_id} withdrawn (cancelled); application services were left as they are.")
        if superseded is not None and superseded.journal is not None:
            routes = pf_config.operation_routes(superseded.plan, superseded.journal, slug=self.context.slug)
            log(f"Operation {superseded.operation_id} ({superseded.kind}) is blocking again. Legal next: "
                + ("; ".join(f"{command}: {description}" for _, command, description in routes) or "none"))
        return 0

    def reopen_unchanged(self):
        """Reopen the unchanged deployment (today's ``pf resume``; a recovery action, not a plan effect)."""
        self.database_ready()
        contract = self.ensure_local_contract()
        images = self.retain_images(utc().lower() + "-resume-" + uuid.uuid4().hex[:6])
        self.activate(images, contract["heads"])

    def restore_allow_connections(self):
        """A purge's listed non-connectable stores whose flag an interrupted window left open are closed again."""
        capture = self.effects_of(type="capture", target="purge-bundle")
        listed = self.precondition(capture[0], "allow_connections=false") if capture else None
        if not listed:
            return
        rows = self.database_rows()
        for name in listed.split(","):
            if name in rows and rows[name]["allow_connections"]:
                self.sql("postgres", f"ALTER DATABASE {quote_identifier(name)} ALLOW_CONNECTIONS false;",
                         mutation=True)

    def owned_candidates(self):
        names = []
        for effect in self.plan["effects"]:
            if effect["type"].startswith("database-"):
                name = self.database_target(effect["target"])[0]
                if pf_config.CANDIDATE_RE.fullmatch(name) and name not in names:
                    names.append(name)
        return names

    def reopen_and_cancel(self):
        """Section 3.6 reopen: owned candidates dropped, a purge's connection flags restored, the unchanged deployment
        reopened; then cancelled."""
        self.database_ready()
        self.drop_owned(self.owned_candidates())
        retained = []
        if self.plan["kind"] == "purge":
            self.restore_allow_connections()
            retained = self.teardown_operation_topologies()
        self.reopen_unchanged()
        if retained:
            self.journal_update(retained=retained)
        self.close_operation("cancelled")
        log(f"Operation {self.operation_id} cancelled; the unchanged deployment was reopened.")
        return 0

    def teardown_operation_topologies(self):
        """Section 3.12 purge rows: every topology this operation pre-assigned is torn down by its exact plan (a frozen
        teardown continued); a refused teardown keeps it and returns its retained ``isolated-topology`` artifact."""
        retained = []
        for project, value in self.operation_topologies():
            record = self.topology_record(project)
            if record is None:
                if not self.topology_resources(project, value):
                    continue
                # Created by Compose before topology.json was written: the plan's pre-assigned identity proves it.
                self.write_topology_record(project, {
                    "schema_version": 1, "project": project, "topology_uuid": value, "purpose": "verification",
                    "bundle_id": None, "manifest_sha256": None, "model_sha256": None, "created_at": utc(),
                    "state": "stopped", "container_ids": [], "volume": None, "network": None,
                    "data_checks_sha256": None, "teardowns": [], "removed_at": None})
            elif record.get("state") == "removed" and not self.topology_resources(project, value):
                continue
            if not self.teardown_topology(project, final=True):
                retained.append({"kind": "isolated-topology", "name": project, "sha256": None})
        return retained

    def prepare_purge_resume(self):
        """A purge in deleting/finalizing: the strict bundle re-read, the frozen plan and the daemon (before the
        RESUME PURGE confirmation). PF-A3.3 section 3.4: in deleting the gate re-checks the exact bundle (its strict
        re-read is step 1) and its exact record only (steps 1-2)."""
        deletion = self.effects_of(type="resource-delete")
        deleting = bool(deletion) and self.effect_state(deletion[0]["effect_id"]) != "complete"
        if deleting:
            self.purge_deletion_gate(None, live=False)
        else:
            bundle_id = self.plan_bundle_id()
            try:
                self.verify_recovery(self.recovery_root / bundle_id)
            except Failure as exc:
                raise Failure(f"plan-input-changed: recovery bundle {bundle_id} of operation {self.operation_id} no "
                              f"longer reads or verifies ({failure_code(exc)}); the operation stays open in "
                              f"{self.journal['phase']}. Restore the bundle folder byte-identically from an off-NAS "
                              "copy, then resume (SYNOLOGY_ADMIN §16). Nothing was changed.") from exc
        if deleting:
            plan = self.load_frozen_deletion_plan("purge", self.journal["deletion"]["plan_sha256"])
            self.require_plan_engine(plan, self.verify_daemon(), self.deletion_progress())
            self._topology_checked = True
            self.log_plan(plan, title="Frozen purge plan (resume; only these items are removed):")

    def prepare_abort_resume(self):
        plan = self.load_frozen_deletion_plan("abort-deploy", self.plan["resources"]["deletion_plan_sha256"])
        self.require_plan_engine(plan, self.verify_daemon(), self.deletion_progress())
        self._topology_checked = True
        self.log_plan(plan, title="Frozen abort-deploy plan (resume; only these items are removed):")

    def prepare_restore_abandon(self):
        """Section 3.6 restore-instance abandon, before its confirmation: the deletion plan (abort-deploy scope) is
        built and shown when the db start may have created resources, or the frozen one is reused."""
        if self.journal["deletion"] is not None:
            plan = self.load_frozen_deletion_plan("abort-deploy", self.journal["deletion"]["plan_sha256"])
            self._topology_checked = True
            self.log_plan(plan, title="Frozen restore-instance abandon plan (resume; only these items are removed):")
            return plan
        db_start = self.effects_of("db-start")
        if db_start and self.effect_state(db_start[0]["effect_id"]) != "not_started":
            plan = self.plan_for("abort-deploy", self.docker_inventory(), command="restore-instance --abandon")
            self.log_plan(plan, title="Restore-instance abandon plan (exact resources of this instance; images are "
                                      "retained):")
            return plan
        return None

    def abandon_restore_instance(self, deletion_plan):
        """Section 3.6: delete what this restore created (frozen A1.3 abort-deploy scope), remove the .env it wrote
        (bytes unchanged only), give an edited .env back, remove the own staging and close cancelled."""
        if deletion_plan is not None and self.journal["deletion"] is None:
            reference = self.write_deletion_plan(deletion_plan)
            self.journal_update(deletion={"plan_sha256": reference["sha256"], "delete_backups": False,
                                          "reset_admin_config": False, "confirmed_at": utc()})
        if deletion_plan is not None:
            self.execute_deletion_plan(deletion_plan)
        env_effect = self.effects_of(target="config:.env")
        if env_effect and self.effect_state(env_effect[0]["effect_id"]) != "not_started":
            expected = re.fullmatch(r"bytes sha256 ([0-9a-f]{64})", env_effect[0]["postcondition"]).group(1)
            path = self.config_dir / ".env"
            try:
                data = pf_instance.read_bytes_nofollow(path)
            except FileNotFoundError:
                data = None
            if data is not None and pf_instance.sha256_bytes(data) == expected:
                os.unlink(str(path))
            elif data is not None:
                log(f"note: env-kept-changed: config/.env changed after operation {self.operation_id} wrote it; it was "
                    "kept.")
            proposal = ".env.proposal-" + self.operation_id[-8:]
            if not os.path.lexists(str(path)) and os.path.lexists(str(self.config_dir / proposal)):
                fd = os.open(str(self.config_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    pf_instance.rename_noreplace_at(fd, proposal, fd, ".env")
                finally:
                    os.close(fd)
        registry = [effect for effect in self.plan["effects"] if pf_config.effect_role(effect) == "registry"]
        if registry and self.effect_state(registry[0]["effect_id"]) != "not_started":
            # OD-A33-08: the abandoned restore leaves a purged instance again (its claim is released again).
            try:
                pf_instance.write_record_state(self.context, "purged", allowed_from=("registered",))
            except pf_instance.ContextError as exc:
                raise Failure(f"{exc}; the record of instance {self.context.slug} keeps state registered. Run "
                              f"'{self.pf_command()} resume --operation {self.operation_id} --abandon' again.") from exc
        self.close_operation("cancelled")
        log(f"Restore operation {self.operation_id} abandoned; the resources it created were removed. Loaded image "
            "tags are retained.")
        return 0

    # ------------------------------------------- the plan runner and the effect bodies (sections 3.4, 3.5)

    def run_plan(self, ctx):
        """Run every effect of the plan that is not complete, in plan order (first run and resume alike), then close
        the operation. ``ctx`` carries the in-memory inputs of a first run (the private candidate, the purge's binding
        plan); a resumed process rebuilds what it needs from the plan, its files and the strictly re-read inputs.
        A backup's caught capture or verification failure closes it failed_preserved (OD-A32-13)."""
        if self.plan["kind"] == "restore-side-by-side":
            return self.run_side_by_side(ctx)
        if self.plan["kind"] == "cleanup":
            return self.run_cleanup(ctx)
        if self.plan["kind"] != "backup":
            return self._run_plan(ctx)
        try:
            return self._run_plan(ctx)
        except (Failure, OSError, pf_source.SourceError) as exc:
            if self.journal is None or self.journal["phase"] in pf_config.TERMINAL_PHASES:
                raise
            self.close_operation("failed_preserved", last_error=self._error(exc))
            log(f"Backup operation {self.operation_id} closed failed_preserved; the capture is kept as evidence and is "
                "not selectable.")
            raise

    def _run_plan(self, ctx):
        for effect in self.plan["effects"]:
            if self.journal["phase"] in pf_config.TERMINAL_PHASES:
                return 0
            effect_id = effect["effect_id"]
            if self.effect_state(effect_id) == "complete":
                continue
            role = pf_config.effect_role(effect)
            if role == "workspace":
                self.run_workspace()
                continue
            if role == "seal" and self.effect_state(effect_id) == "partial":
                # Section 3.4: a seal failure after a healthy activation is final for this operation (the pointer
                # records deployment_seal_failed); a later resume of its workspace switch never redoes it, so the
                # operation closes failed_preserved and the journal and deployed.json agree.
                continue
            if role == "deletion" and self.plan["kind"] == "purge" and self.journal["deletion"] is None:
                self.purge_approve_deletion(ctx)
            # PF-A3.3 section 3.8: the capacity re-check at the start of a listed phase, before its first intent.
            self.capacity_check(effect["phase"])
            self.run_effect(effect_id, self.effect_action(effect, ctx), evidence=self.intent_evidence(effect))
        return self.finish_operation()

    def intent_evidence(self, effect):
        """The identities known before an effect (section 3.1 step 4): the live heads before a live migration, the
        pre-existing checkpoint history, the capture attempt."""
        if self.effect_state(effect["effect_id"]) != "not_started":
            return None
        if effect["type"] == "database-migrate":
            name, _, _ = self.database_target(effect["target"])
            if pf_config.CANDIDATE_RE.fullmatch(name):
                return None
            heads = self.db_heads(name) if name in self.database_names() else []
            return "pre-heads:" + ",".join(sorted(set(heads)))
        if effect["type"] == "capture":
            return f"bundle:{self.precondition(effect, 'bundle')} verify:{self.precondition(effect, 'verify')}"
        if effect["target"] == "checkpoint-history":
            revisions = self.revisions_root
            existing = None
            if real_directory(revisions):
                fd = os.open(str(revisions), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    existing = pf_instance.identity_at(fd, self.config["project"])
                finally:
                    os.close(fd)
            return "existing:" + self.identity_text(existing)
        return None

    def finish_operation(self):
        """The terminal generation after the last effect: completed, or failed_preserved when the seal failed after a
        healthy activation (the A3.1 non-wedging rule; no compose stop)."""
        seal = self.effects_of("seal")
        if seal and self.effect_state(seal[0]["effect_id"]) == "partial":
            detail = (self.journal_effect(seal[0]["effect_id"])["evidence"] or "seal-failed").split(": ", 1)[-1]
            message = (f"deployment-record-incomplete: the application was activated and passed health checks, but its "
                       f"deployment record could not be sealed ({detail}). The operation was closed and the deployment "
                       "is treated as one without a record; the next deploy, update, rollback or restore-instance "
                       f"seals one. Run 'pf --instance {self.context.slug} status'.")
            self.close_operation("failed_preserved", last_error={"code": "deployment-record-incomplete",
                                                                 "message": message[:2000]})
            raise Failure(message)
        deployment_id = self.plan["source"]["deployment_id"] if seal else None
        self.close_operation("completed", deployment_id=deployment_id)
        self.completion_log()
        return 0

    def completion_log(self):
        kind, plan = self.plan["kind"], self.plan
        log(f"Operation {self.operation_id} completed.")
        if kind == "deploy":
            log("Initial deployment complete.")
            log("Run the UI/workflow and firewall smoke tests, then create the first baseline checkpoint with: sudo pf "
                "backup")
        elif kind == "update":
            log("Update complete. Previous revision checkpoint: " + self.operation_checkpoint())
        elif kind == "rollback":
            log("Rollback complete. Safety checkpoint: " + self.operation_checkpoint())
        elif kind == "reset-db":
            log("Clean database is active. Configure Departments/Areas/Operations/Stations again.")
        elif kind == "restore-instance":
            log("Instance restore complete. Run UI/workflow/network smoke tests before accepting the recovered staging "
                "instance.")
            log("postgres-globals.sql is preserved in the bundle for manual review; it was not executed automatically.")
        elif kind == "abort-deploy":
            log("Incomplete first deployment resources were removed. Source and .env were kept; rerun deploy when "
                "ready.")
        elif kind == "backup":
            view = self.verify_snapshot(self.operation_checkpoint())
            log(f"Checkpoint class: {view.capture_class}")
            log(f"Verification level: {view.level} (record {view.latest_verification_id})")
            log("Copy the entire checkpoint directory off-NAS. It contains production-like database data even though "
                "runtime .env is stored separately.")
        if plan["workspace"]["mode"] == "keep":
            log("Workspace refresh: kept (--keep-workspace); the workspace was not changed.")

    def effect_action(self, effect, ctx):
        """The body of one plan effect (section 3.4), by type and target."""
        etype, target = effect["type"], effect["target"]
        role = pf_config.effect_role(effect)
        if role == "topology":
            return self.topology_action(effect)
        if self.plan["kind"] == "cleanup":
            return self.cleanup_action(effect)
        if etype == "source-stage" and target.startswith("deployment:"):
            return lambda step: self.act_stage(step, ctx)
        if etype == "file-write" and target == "source-manifest":
            return lambda step: self.act_record_current(step, ctx)
        if role == "stop":
            return lambda step: self.pause()
        if role == "db-start":
            return lambda step: self.act_db_start(step)
        if etype == "capture":
            return lambda step: self.act_capture(step, effect, ctx)
        if etype == "verification":
            return lambda step: self.act_verification(step, effect, ctx)
        if etype.startswith("database-") and etype != "database-switch":
            return lambda step: self.act_database(step, effect, ctx)
        if etype == "database-switch":
            _, current, prepared, retained = target.split(":")
            return lambda step: self.swap_database(prepared, retained, current=current)
        if etype == "image-load":
            return lambda step: self.act_image_load(step)
        if etype == "image-tag":
            return lambda step: self.act_image_tag(step, effect)
        if role == "backend":
            return lambda step: self.activate_backend(self.activation_images(ctx), self.activation_heads(effect))
        if role == "frontend":
            return lambda step: self.act_frontend(step, ctx)
        if role == "seal":
            return lambda step: self.act_seal(step)
        if role == "pointer":
            return lambda step: self.act_pointer(step)
        if etype == "resource-delete":
            return lambda step: self.act_delete(step)
        if etype == "file-write":
            return lambda step: self.act_file(step, effect, ctx)
        raise Failure(f"Internal error: no body for effect {effect['effect_id']} ({etype} {target}).")

    def act_stage(self, step, ctx):
        if "candidate" not in ctx:
            raise Failure("Internal error: a deployment is staged only by the process that confirmed it.")
        staged = self.stage_deployment(ctx["candidate"], ctx["manifest"], kind=self.plan["kind"],
                                       images=ctx.get("images"), ref=ctx.get("ref"),
                                       deployment_id=self.plan["source"]["deployment_id"],
                                       pointer=ctx.get("pointer"))
        step.evidence = f"staged {staged.deployment_id}"

    def act_record_current(self, step, ctx):
        if "candidate" not in ctx:
            raise Failure("Internal error: deploy --current records the manifest of its own candidate only.")
        manifest = self.candidate_manifest(ctx["candidate"], self.plan["source"]["commit"], verified=True)
        step.evidence = f"bytes sha256 {self.write_workspace_manifest(manifest)}"

    def act_db_start(self, step):
        self.compose("up", "-d", "--no-deps", "db")
        self.wait_health("db")
        if self.plan["kind"] == "restore-instance":
            recovery = self.op_recovery()
            values = self.env()
            owner = recovery.database_user
            if values["POSTGRES_DB"] != recovery.database or (owner is not None and values["POSTGRES_USER"] != owner):
                raise Failure("Recovered .env database identity does not match the recovery manifest.")
            return
        self.database_ready()
        migrate = self.effects_of(type="database-migrate")
        if self.plan["kind"] == "deploy" and migrate and self.effect_state(migrate[0]["effect_id"]) == "not_started" \
                and self.db_heads():
            raise Failure("The supposedly new database already contains an Alembic revision. New deploy refuses to "
                          "adopt it.")
        step.evidence = "db healthy"

    def capture_ids(self, effect, step):
        """(bundle ID, verification candidate) of a capture attempt: the plan's, or for a backup's new attempt (a redo)
        a new bundle ID, the earlier attempt's folder recorded as ``bundle-attempt`` (section 3.5)."""
        bundle_id = self.precondition(effect, "bundle")
        verify = self.precondition(effect, "verify")
        if self.plan["kind"] == "backup" and step.evidence and step.evidence.startswith("attempt "):
            used = re.findall(r"bundle:([0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{6})", step.evidence)
            last = used[-1] if used else bundle_id
            if os.path.lexists(str(self.backups_dir / last)):
                step.retained.append({"kind": "bundle-attempt", "name": last, "sha256": None})
            bundle_id = f"{utc()}-{last.split('-')[1]}-{uuid.uuid4().hex[:6]}"
            verify = "pf_verify_" + uuid.uuid4().hex[:20]
        return bundle_id, verify

    def act_capture(self, step, effect, ctx):
        kind = self.plan["kind"]
        bundle_id, verify = self.capture_ids(effect, step)
        step.evidence = (step.evidence + " " if step.evidence and "attempt " in step.evidence else "") + \
            f"bundle:{bundle_id} verify:{verify}"
        if effect["target"] == "purge-bundle":
            checkpoint = ctx.get("checkpoint") or self.verify_snapshot(self.precondition(effect, "checkpoint"))
            view, binding = self.capture_purge_bundle(ctx["preliminary"], checkpoint=checkpoint, bundle_id=bundle_id)
            ctx.update(recovery=view, binding=binding)
            step.retained.append({"kind": "purge-bundle", "name": view.bundle_id, "sha256": view.manifest_sha256})
            return
        reason = effect["target"].split(":", 1)[1]
        if kind == "backup":
            try:
                view = self._capture(reason, capture_class="healthy_checkpoint", bundle_id=bundle_id,
                                     verify_name=verify, verify=False)
            except (Failure, OSError, pf_source.SourceError) as exc:
                if not isinstance(exc, DaemonFailure) and os.path.lexists(str(self.backups_dir / bundle_id)):
                    step.retained.append({"kind": "bundle-attempt", "name": bundle_id, "sha256": None})
                raise
        elif kind in ("rollback", "reset-db", "abort-deploy"):
            # PF-A3.3 sections 3.7/3.7a: reset-db and abort-deploy preserve the current data like a rollback (healthy
            # when the contract holds, else emergency preservation).
            view = self.preserve_current(reason, stores=[self.env()["POSTGRES_DB"]], bundle_id=bundle_id, step=step,
                                         verify_name=verify)
        else:
            if kind == "purge":
                self.database_ready()
            view = self.snapshot(reason, bundle_id=bundle_id, verify_name=verify)
        ctx["checkpoint" if kind != "backup" else "view"] = view
        if view.bundle_id != bundle_id:
            step.evidence += f" bundle:{view.bundle_id}"
        if kind in ("rollback", "reset-db", "abort-deploy"):
            step.evidence += " class:" + view.capture_class
        step.retained.append({"kind": "checkpoint", "name": view.bundle_id, "sha256": view.manifest_sha256})
        if kind == "reset-db":
            missing = [service for service in pf_docker.BUILT_SERVICES if view.image(service) is None]
            if missing:
                exc = Failure(f"reset-images-unidentified: the current data were preserved as "
                              f"{CLASS_NAMES[view.capture_class]} {view.bundle_id}, but its {missing[0]} image could not be "
                              f"identified, so no clean database is activated. The preservation was kept; operation "
                              f"{self.operation_id} failed closed and '{self.pf_command()} resume' reopens the unchanged "
                              "deployment.")
                exc.code = "reset-images-unidentified"
                raise exc

    def operation_checkpoint(self):
        """The checkpoint this operation captured (its retained artifact), else the plan's pre-assigned ID."""
        found = [item["name"] for item in self.journal["retained_artifacts"] if item["kind"] == "checkpoint"]
        if found:
            return found[-1]
        captures = self.effects_of(type="capture")
        return str(self.precondition(captures[0], "bundle")) if captures else "none"

    def act_verification(self, step, effect, ctx):
        if self.plan["kind"] == "restore-side-by-side":
            return self.act_side_by_side_verification(step, effect)
        capture = self.capture_effect_for(effect)
        bundle_id = self.attempt_bundle(capture, self.journal_effect(capture["effect_id"])["evidence"])
        if self.plan["kind"] == "purge" and effect["postcondition"] == A32_PURGE_POSTCONDITION:
            # Section 3.18: an A3.2-opened purge keeps its frozen semantics (data_restore_verified).
            view = ctx.get("recovery") or self.verify_recovery(self.recovery_root / bundle_id)
            names = ["pf_verify_" + uuid.uuid4().hex[:20] for _ in view.stores]
            step.evidence = f"bundle:{bundle_id} verify:{','.join(names)}"
            ctx["recovery"] = self.verify_purge_bundle(view, names)
            return
        if self.plan["kind"] == "purge":
            # Section 3.4: the functional verification of the exact final bundle in an isolated topology, inside the
            # quiescence window; the record ID is the normative evidence the deletion gate binds.
            view = ctx.get("recovery") or self.verify_recovery(self.recovery_root / bundle_id)
            project, topology_uuid = self.precondition(effect, "topology"), self.precondition(effect, "topology-uuid")
            step.evidence = f"bundle:{bundle_id} topology:{project} uuid:{topology_uuid[:8]}"
            images = self.topology_images(view)
            topology = self.isolated_topology(view, project=project, topology_uuid=topology_uuid,
                                              purpose="verification", images=images)
            self.inside("topology")
            record = self.functional_verification(view, topology, mode="purge", step=step)
            log(f"Final bundle {view.bundle_id}: functional recovery verified in isolated topology {project} (record "
                f"{record['verification_id']}; topology removed)")
            ctx["recovery"] = dataclasses.replace(view, level=record["level"],
                                                  latest_verification_id=record["verification_id"])
            return
        view = ctx.get("view") or self.verify_snapshot(bundle_id)
        verify = (re.findall(r"verify:(pf_verify_[0-9a-f]{20})", self.journal_effect(capture["effect_id"])["evidence"]
                             or "") or [self.precondition(capture, "verify")])[-1]
        if step.evidence and "attempt " in step.evidence:
            verify = "pf_verify_" + uuid.uuid4().hex[:20]
        step.evidence = (step.evidence + " " if step.evidence else "") + f"bundle:{bundle_id} verify:{verify}"
        ctx["view"] = self.verify_capture(view, verify)

    def act_database(self, step, effect, ctx):
        etype = effect["type"]
        name, heads, flag = self.database_target(effect["target"])
        kind = self.plan["kind"]
        if etype == "database-create":
            self.create_database(name)
        elif etype == "database-drop":
            if name in self.database_names():
                self.drop_database(name)
        elif etype == "database-alter":
            self.sql("postgres", f"ALTER DATABASE {quote_identifier(name)} ALLOW_CONNECTIONS false;", mutation=True)
        elif etype == "database-restore":
            if name.startswith("pf_migrate_"):
                checkpoint = ctx.get("checkpoint") or self.verify_snapshot(
                    self.precondition(self.effects_of(type="capture")[0], "bundle"))
                self.restore_into(name, checkpoint.folder / checkpoint.active_store["dump"])
            elif name.startswith("pf_restore_"):
                self.restore_candidate(self.op_selected(), name)
            else:
                recovery = self.op_recovery()
                store = next(item for item in recovery.stores if item["database"] == name)
                if name in self.database_names():
                    self.drop_database(name)
                self.restore_into(name, recovery.folder / store["dump"])
                if store["role"] == "active" and self.db_heads(name) != recovery.database_heads:
                    raise Failure("Restored active database Alembic revision does not match the recovery bundle.")
        elif etype == "database-migrate":
            env = None if name == self.env()["POSTGRES_DB"] else {"POSTGRES_DB": name}
            if kind == "reset-db":
                override = self.state / "reset-images.yaml"
                self.make_override(self.activation_images(ctx), override)
                root = None
            else:
                override = self.state / "candidate-images.yaml"
                self.make_override(self.activation_images(ctx), override)
                root = ctx.get("candidate") or self.deployed_tree()
            self.compose("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head",
                         root=root, override=override, env=env)
            reached = sorted(set(self.db_heads(name)))
            if reached != heads:
                if name.startswith("pf_migrate_"):
                    raise Failure("Migration rehearsal did not reach the target head.")
                if name.startswith("pf_clean_"):
                    raise Failure("The clean database did not reach the current schema head.")
                if kind == "deploy":
                    raise Failure("Initial database migration did not reach the selected application's Alembic head.")
                raise Failure("The live database migration did not reach the target head.")
            step.evidence = (step.evidence + " " if step.evidence else "") + "heads:" + ",".join(reached)

    def act_image_load(self, step):
        recovery = self.op_recovery()
        self.docker("image", "load", "-i", recovery.folder / "images.tar")
        self.verify_images(recovery.images)
        self.make_override(recovery.images, self.override)
        step.evidence = "images:" + ",".join(image["id"][7:19] for image in recovery.images.values() if image)

    def activation_images(self, ctx):
        """The backend/frontend images an activation or migration of this operation uses."""
        kind = self.plan["kind"]
        if kind == "restore-instance":
            return self.op_recovery().images
        if kind == "rollback":
            return self.op_selected().images
        if kind == "reset-db":
            # PF-A3.3 section 3.7: the actual (possibly fallback) checkpoint the capture recorded.
            checkpoint = ctx.get("checkpoint") or self.verify_snapshot(self.operation_checkpoint())
            return checkpoint.images
        return {service: {"reference": self.plan["images"][service]["reference"],
                          "id": self.plan["images"][service]["id"]} for service in pf_docker.BUILT_SERVICES}

    def activation_heads(self, effect):
        value = self.precondition(effect, "heads")
        return sorted(item for item in (value or "").split(",") if item)

    def staged_record(self):
        """The operation's deployment-artifact.json (state, deployment ID and the staged details)."""
        path = self.operation_dir / "deployment-artifact.json"
        try:
            record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(path), label=str(path))
        except (OSError, pf_instance.ContextError) as exc:
            raise Failure(f"plan-input-changed: the staged deployment record of operation {self.operation_id} is "
                          f"unreadable ({exc}). Nothing was changed.") from exc
        if not isinstance(record, dict) or record.get("deployment_id") != self.plan["source"]["deployment_id"] \
                or not isinstance(record.get("staged"), dict):
            raise Failure(f"plan-input-changed: the staged deployment record of operation {self.operation_id} names "
                          "another deployment. Nothing was changed.")
        return record

    def staged_deployment(self):
        """The StagedDeployment of this operation rebuilt from its staged record (section 3.6)."""
        record = self.staged_record()
        staged = record["staged"]
        dep = record["deployment_id"]
        return StagedDeployment(dep, self.plan["kind"], self.deployments_dir / (".staging-" + dep), staged["source"],
                                staged["images"], staged["previous_deployment_id"], staged["migration_files_sha256"])

    def sealed_view(self, dep):
        """A valid sealed record of ``dep`` (the A3.1 read), else None."""
        folder = self.deployments_dir / dep
        if not real_directory(folder):
            return None
        fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            data = self._bundle_file(fd, "deployment-record.json", MANIFEST_READ_LIMIT)
            record, record_sha256, mismatch = self._deployment_check(fd, dep, pf_instance.sha256_bytes(data))
        except (OSError, ValueError):
            return None
        finally:
            os.close(fd)
        return None if mismatch is not None else (record, record_sha256)

    def act_seal(self, step):
        staged = self.staged_deployment()
        record = self.staged_record()
        restored_from = self.plan["input_bundle"] if self.plan["kind"] == "restore-instance" else None
        try:
            sealed = self.seal_deployment(staged, restored_from=restored_from)
        except DaemonFailure:
            raise
        except (Failure, OSError, pf_instance.ContextError, pf_config.ConfigError, pf_docker.DockerScopeError,
                ValueError, KeyError, TypeError) as exc:
            detail = (str(exc).splitlines() or [type(exc).__name__])[0][:300]
            self.write_private_json("deployment-artifact.json", dict(record, state="seal-failed", detail=detail))
            step.outcome = "partial"
            step.evidence = "seal-failed: " + detail
            return
        self.write_private_json("deployment-artifact.json", dict(record, state="sealed",
                                                                 record_sha256=sealed["record_sha256"]))
        step.evidence = f"sealed {sealed['deployment_id']} record {sealed['record_sha256'][:12]}"
        log(f"Deployment {sealed['deployment_id']} sealed (record {sealed['record_sha256'][:12]}).")

    def act_pointer(self, step):
        record = self.staged_record()
        dep = record["deployment_id"]
        pointer = dict(record["staged"]["pointer"], deployed_at=utc())
        seal = self.effects_of("seal")[0]
        if self.effect_state(seal["effect_id"]) == "complete":
            sealed = self.sealed_view(dep)
            if sealed is None:
                raise Failure(f"deployment-artifact-mismatch: the sealed record of {dep} cannot be read for the "
                              "pointer.")
            pointer.update(deployment_id=dep, deployment_record_sha256=sealed[1])
        else:
            pointer["deployment_seal_failed"] = self.operation_id
        self.ensure_state_dir()
        write_json(self.state / "deployed.json", pointer)
        if self.plan["kind"] == "restore-instance":
            # PF-A2.3 FL-9: a restore publishes the private state files it (re)creates with the policy target.
            self.publish_fresh("private_state", self.state / "deployed.json")
        step.evidence = "bytes sha256 " + pf_instance.sha256_bytes(pf_instance.read_bytes_nofollow(
            self.state / "deployed.json"))

    def act_delete(self, step):
        kind = self.plan["kind"]
        if kind == "purge":
            plan = self.load_frozen_deletion_plan("purge", self.journal["deletion"]["plan_sha256"])
        else:
            plan = self.load_frozen_deletion_plan("abort-deploy", self.plan["resources"]["deletion_plan_sha256"])
        deleted = self.execute_deletion_plan(plan)
        step.evidence = f"removed {sum(1 for item in deleted if item.get('outcome') == 'removed')} of " \
                        f"{len(plan['candidates'])} planned items"

    def act_file(self, step, effect, ctx):
        target = effect["target"]
        if target == "config:.env":
            return self.act_install_env(step, effect, ctx)
        if target == "checkpoint-history":
            return self.act_checkpoint_history(step, effect)
        if target.startswith("state-file:"):
            name = target.split(":", 1)[1]
            data = self.read_small_payload(self.op_recovery(), "state/" + name, "state_file")
            self.ensure_state_dir()
            pf_instance._write_private_file(self.state / name, data, 0o600)
            self.publish_fresh("private_state", self.state / name)
            step.evidence = "bytes sha256 " + pf_instance.sha256_bytes(data)
            return None
        if target == "last-reset":
            switch = self.effects_of(type="database-switch")[0]
            retained = switch["target"].split(":")[3]
            write_json(self.state / "last-reset.json", {"time": utc(),
                                                        "checkpoint": self.precondition(effect, "checkpoint"),
                                                        "retained_database": retained})
            log("Previous database retained (connections disabled): " + retained)
            return None
        if target == "override:active-images.yaml":
            if os.path.lexists(str(self.override)):
                os.unlink(str(self.override))
            step.evidence = "absent"
            return None
        if target.startswith("purge-cleanup:"):
            return self.act_purge_cleanup(step, target.split(":", 1)[1])
        if target.startswith("registry:state="):
            return self.act_registry_state(step, effect)
        raise Failure(f"Internal error: no body for file-write {target}.")

    def act_registry_state(self, step, effect):
        """OD-A33-08 (applied; LIFECYCLE section 8 step 7): the record's state through a registry transaction (the
        registry lock taken non-blocking while the instance lock is held). ``purged`` releases the project claim; a
        restore or deploy of a purged record re-checks the claim under the registry lock and writes ``registered``."""
        state = effect["target"].split("=", 1)[1]
        before = self.precondition(effect, "record-state")
        allowed = (before,) if before else ("registered", "active")
        try:
            data, previous = pf_instance.write_record_state(
                self.context, state, allowed_from=allowed,
                check=(lambda: self.require_project_claim(locked=True)) if state != "purged" else None)
        except pf_instance.LockBusy as exc:
            raise Failure(f"registry-busy: an installation transaction holds the registry lock; the record state of "
                          f"instance {self.context.slug} was not changed. Run '{self.pf_command()} resume' after it "
                          "finishes.") from exc
        except pf_instance.ContextError as exc:
            raise Failure(str(exc)) from exc
        step.evidence = f"state {state} record {pf_instance.sha256_bytes(data)[:12]}" + (
            "" if previous is not None else " (already)")
        if state == "purged":
            log(f"Registry: instance {self.context.slug} is purged; its project claim {self.context.compose_project} "
                "is released (restore-instance or deploy claims it again).")

    def require_project_claim(self, *, locked=False):
        """OD-A33-08: a purged record holds no claim, so restoring or deploying it re-checks that no other
        non-purged record (or pending registration) claims (daemon, project). ``locked``: the caller re-checks under
        the registry lock it holds; otherwise this is the read-only preview."""
        try:
            registry = pf_instance.load_registry(self.context.installation_root)
        except pf_instance.ContextError as exc:
            raise Failure(f"registry-invalid: {exc}. Nothing was changed.") from exc
        key = (self.context.daemon.engine_id, self.context.compose_project)
        owners = []
        for entry, context, _ in registry.records():
            if context is not None and context.instance_id != self.context.instance_id and context.state != "purged" \
                    and (context.daemon.engine_id, context.compose_project) == key:
                owners.append(context.slug)
        for pending in pf_instance.pending_registrations(registry.root):
            if pending.record is not None and pending.instance_id != self.context.instance_id \
                    and (pending.record["daemon"]["engine_id"], pending.record["compose_project"]) == key:
                owners.append("reservation:" + pending.slug)
        if owners:
            raise Failure(f"instance-claim-taken: project {self.context.compose_project} on daemon "
                          f"{self.context.daemon.engine_id} is claimed by {', '.join(owners)} since instance "
                          f"{self.context.slug} was purged; the purged record cannot claim it again. Nothing was "
                          "changed.")

    # ------------------------------------------- restore-instance evidence (PF-A3.3 section 3.3)

    def act_frontend(self, step, ctx):
        """The ``service:frontend:start`` effect; a restore-instance first records the application invariants of the
        activated instance once (``app-check-activated.json``: a present file is never re-run, a crash before it is
        re-run by the forward resume of this effect). Evidence only, never blocking."""
        if self.plan["kind"] == "restore-instance":
            self.activated_invariants()
        self.activate_frontend(self.activation_images(ctx))

    def activated_invariants(self):
        if os.path.lexists(str(self.operation_dir / "app-check-activated.json")):
            return None
        result = self.app_invariants("activated")
        if result.outcome == "clean":
            text = "clean"
        elif result.outcome == "mismatch":
            recorded = self.recorded_oracle(self.op_recovery())
            text = "mismatch equal to the bundle's verification" if recorded == result.summary \
                else "mismatch (an incident, RUNBOOK §8)"
        elif result.outcome == "unavailable":
            text = "unavailable"
        else:
            text = "could not run"
        log("Application invariants: " + text)
        return result

    # ------------------------------------------- side-by-side recovery (PF-A3.3 section 3.6)

    def side_by_side_effect(self, etype):
        found = [effect for effect in self.plan["effects"] if effect["type"] == etype
                 and pf_config.effect_role(effect) == "topology"]
        return found[0] if found else None

    def restore_side_by_side(self, recovery):
        """``pf restore-instance <id> --side-by-side`` (kind ``restore-side-by-side``): the exact bundle restored into
        a kept, functionally verified isolated topology beside the running instance; nothing of the live instance is
        written (no override, .env, workspace, pointer or database change)."""
        self.staging()
        view = recovery
        for service in pf_docker.BUILT_SERVICES:
            if view.image(service) is None:
                raise self.isolation_failure(f"legacy bundle {view.bundle_id} has no usable {service} image")
        images = {service: view.image(service)["id"] for service in pf_docker.BUILT_SERVICES}
        db_image = None
        if view.image("db") is not None:
            images["db"] = view.image("db")["id"]
        else:
            db_image = self.local_image_id(pf_docker.DB_IMAGE)
            if db_image is None:
                raise self.isolation_failure("local postgres:16 is absent and the legacy bundle recorded no database "
                                             "image")
            images["db"] = db_image
        absent = [service for service in pf_docker.SERVICES if not self.image_present(images[service])]
        if absent:
            proof = self.prove_bundle_images(view, [images[service] for service in absent])
            for tag, image_id in proof.repo_tags:
                local = self.local_image_id(tag)
                if local is not None and local != image_id:
                    exc = Failure(f"image-load-would-retag: images.tar of bundle {view.bundle_id} tags {tag} as "
                                  f"{image_id[7:19]}, but that tag already names {local[7:19]} on this daemon; loading "
                                  "would re-point it. Nothing was changed.")
                    exc.code = "image-load-would-retag"
                    raise exc
        self.isolation_preflight(view, images)
        project, topology_uuid = self.new_topology("pfrecover-")
        self.capacity_preflight("restore-side-by-side", view=view, load=bool(absent))
        phrase = "RESTORE COPY " + view.bundle_id
        summary = (f"Recovery target {project} (UUID {topology_uuid}) beside the running instance: own volume and "
                   "internal network, no published port (not even loopback), no scheduler, generated database "
                   "password. The running instance, its data, listener, image override and workspace are not changed. "
                   "No automatic merge into Movement history.")
        confirm(phrase, summary)
        identity = ["topology:" + project, "topology-uuid:" + topology_uuid, "bundle:" + view.bundle_id]
        effects = []
        if absent:
            effects.append({"phase": "preparing-target", "type": "image-load", "target": "images:" + view.bundle_id,
                            "postcondition": "backend/frontend/db IDs present",
                            "preconditions": ["load-only (no override, no tag verification)", "bundle re-read"]})
        effects += [
            {"phase": "restoring-data", "type": "database-restore", "target": f"topology:{project}:stores",
             "postcondition": "restored and checked",
             "preconditions": identity + (["db-image:" + db_image] if db_image else [])},
            {"phase": "activating", "type": "service-change", "target": f"topology:{project}:start",
             "postcondition": f"running {images['backend'][7:19]},{images['frontend'][7:19]}",
             "preconditions": identity},
            {"phase": "verifying", "type": "verification", "target": "topology:" + project,
             "postcondition": "passed functional_recovery_verified record (kept)", "preconditions": identity}]
        self._op_recovery = (self.operation_id, view)
        self.open_operation(
            "restore-side-by-side", effects=effects, workspace=self.workspace_plan("untouched"), images={},
            confirmation=self.confirmation_ref(phrase, summary),
            source={"provenance": "not_applicable", "commit": None, "entries_sha256": None, "deployment_id": None},
            input_bundle={"bundle_id": view.bundle_id, "manifest_sha256": view.manifest_sha256})
        return self.run_plan({})

    def run_side_by_side(self, ctx):
        """The side-by-side runner: a failed verification closes failed_preserved with the target kept; a lost target
        (changed volume or data checks after an interruption) is torn down and closed failed_preserved."""
        try:
            return self._run_plan(ctx)
        except FunctionalFailed as exc:
            self.close_operation("failed_preserved", last_error=self._error(exc))
            raise
        except RecoveryTargetLost as exc:
            retained = []
            for project, _ in self.operation_topologies():
                if not self.teardown_topology(project, final=True):
                    retained.append({"kind": "recovery-target", "name": project, "sha256": None})
            if retained:
                self.journal_update(retained=retained)
            note = (f"recovery-target-lost: {exc.project}: its data volume or data checks changed while the operation "
                    "was interrupted; it was removed. Run the side-by-side restore again.")
            self.close_operation("failed_preserved", last_error={"code": "recovery-target-lost", "message": note})
            log("note: " + note)
            raise Failure(note) from exc

    def topology_action(self, effect):
        etype = effect["type"]
        if etype == "database-restore":
            return lambda step: self.act_topology_stores(step, effect)
        if etype == "service-change":
            return lambda step: self.act_topology_start(step, effect)
        if etype == "verification":
            return lambda step: self.act_side_by_side_verification(step, effect)
        raise Failure(f"Internal error: no body for effect {effect['effect_id']} ({etype} {effect['target']}).")

    def side_by_side_topology(self, effect, *, existing):
        view = self.op_recovery()
        project, topology_uuid = self.precondition(effect, "topology"), self.precondition(effect, "topology-uuid")
        stores = self.side_by_side_effect("database-restore")
        images = self.topology_images(view, stores)
        if existing:
            self.require_target_identity(project)
        return view, self.isolated_topology(view, project=project, topology_uuid=topology_uuid,
                                            purpose="side-by-side", images=images)

    def target_lost_reason(self, project):
        """None when the recovery target still has the volume identity and the data-checks hash ``topology.json``
        recorded; else why it is lost."""
        record = self.topology_record(project)
        if record is None or not os.path.lexists(str(self.topology_directory(project) / "compose.json")):
            return "its topology files are missing"
        try:
            data = pf_instance.read_bytes_nofollow(self.topology_directory(project) / "data-checks.json")
        except OSError:
            return "data-checks.json is missing"
        if pf_instance.sha256_bytes(data) != record.get("data_checks_sha256"):
            return "data-checks.json differs from its recorded hash"
        if record.get("volume") is None or self.volume_identity(project) != record["volume"]:
            return "its data volume is missing or was replaced"
        return None

    def require_target_identity(self, project):
        reason = self.target_lost_reason(project)
        if reason is not None:
            exc = RecoveryTargetLost(f"recovery-target-lost: {project}: {reason}")
            exc.project = project
            raise exc

    def act_topology_stores(self, step, effect):
        """``database-restore topology:<p>:stores``: the topology files once; a redo teardown when resources of the
        project + UUID exist; the db service; every store restored and checked; ``data-checks.json`` and its hash."""
        view, topology = self.side_by_side_topology(effect, existing=False)
        step.evidence = f"topology:{topology.project} uuid:{topology.uuid[:8]}"
        if self.topology_resources(topology.project, topology.uuid):
            if not self.teardown_topology(topology.project, final=False):
                raise Failure(f"isolated-topology-kept: the redo teardown of {topology.project} was refused; the data "
                              "restore was not repeated.")
        started = utc()
        with self.bound(topology):
            try:
                checks, passed = self.store_restores(view, topology)
            except DaemonFailure:
                raise
            except Failure as exc:
                if getattr(exc, "code", None) == "verification-isolation-unsupported":
                    raise
                checks, passed = [{"name": "restore:" + view.active_store["store_id"], "result": "failed",
                                   "detail": str(exc).splitlines()[0][:500]}], False
            if not passed:
                try:
                    self.compose("stop")
                except DaemonFailure:
                    raise
                except Failure as exc:
                    log("WARNING: the recovery target could not be stopped: " + str(exc).splitlines()[0])
        data = pf_instance.normalize_json(checks)
        pf_instance._write_private_file(topology.directory / "data-checks.json", data, 0o600)
        self.update_topology_record(topology.project, data_checks_sha256=pf_instance.sha256_bytes(data))
        if not passed:
            failed = next(check for check in checks if check["result"] == "failed")
            planned = self.functional_names(view, topology)
            present = {item["name"] for item in checks}
            all_checks = [{"name": "topology:" + topology.project, "result": "passed",
                           "detail": f"uuid {topology.uuid[:8]}; model {topology.model_sha256[:12]}"}] + checks + [
                {"name": name, "result": "failed", "detail": f"not reached: {failed['name']}"[:500]}
                for name in planned if name not in present and not name.startswith("topology:")]
            record = self.write_verification(view, level="functional_recovery_verified", result="failed",
                                             target={"kind": "isolated-database",
                                                     "names": [store["database"] for store in view.stores],
                                                     "removed": False},
                                             checks=all_checks, started_at=started,
                                             environment={"server_version_num": None,
                                                          "engine_id": self.context.daemon.engine_id,
                                                          "compose_version": self.compose_version})
            self.update_topology_record(topology.project, state="stopped")
            step.retained.append({"kind": "recovery-target", "name": topology.project, "sha256": None})
            step.evidence += " record:" + record["verification_id"]
            raise self.functional_failure(view, failed, topology, "side-by-side")

    def act_topology_start(self, step, effect):
        """``service-change topology:<p>:start``: never on an empty init database (the recorded volume identity and
        data checks are required); the backend, then the frontend (``up -d --no-build --no-deps``, idempotent)."""
        _, topology = self.side_by_side_topology(effect, existing=True)
        with self.bound(topology):
            for service in ("backend", "frontend"):
                self.compose("up", "-d", "--no-build", "--no-deps", service)
        step.evidence = f"running {topology.images['backend'][7:19]},{topology.images['frontend'][7:19]}"

    def act_side_by_side_verification(self, step, effect):
        view, topology = self.side_by_side_topology(effect, existing=True)
        with self.bound(topology):
            for service in ("backend", "frontend"):
                self.compose("up", "-d", "--no-build", "--no-deps", service)
        record = self.functional_verification(view, topology, mode="side-by-side", step=step)
        self.update_topology_record(topology.project, state="running")
        log(f"Recovery target {topology.project} from bundle {view.bundle_id}: functional recovery verified (record "
            f"{record['verification_id']}); it is kept (no listener; inspect it as root with docker exec).")

    def observe_side_by_side(self, effect):
        """Section 3.12 side-by-side rows (never inferred complete from existence)."""
        if effect["type"] == "database-restore":
            return "redo", "the data restore is redone after a redo teardown (never inferred complete)", None
        project = self.precondition(effect, "topology")
        reason = self.target_lost_reason(project)
        if reason is not None:
            def lost():
                exc = RecoveryTargetLost(f"recovery-target-lost: {project}: {reason}")
                exc.project = project
                raise exc
            return "redo", f"recovery-target-lost: {reason}", lost
        return "redo", "forward on the recorded volume and data checks", None

    def abandon_side_by_side(self):
        """``resume --abandon`` of a side-by-side recovery (legal in every phase): the final teardown, cancelled; a
        refused teardown closes failed_preserved with the target recorded (no dead end)."""
        retained = []
        for project, value in self.operation_topologies():
            if self.topology_record(project) is None:
                if not self.topology_resources(project, value):
                    continue
                self.write_topology_record(project, {
                    "schema_version": 1, "project": project, "topology_uuid": value, "purpose": "side-by-side",
                    "bundle_id": None, "manifest_sha256": None, "model_sha256": None, "created_at": utc(),
                    "state": "stopped", "container_ids": [], "volume": None, "network": None,
                    "data_checks_sha256": None, "teardowns": [], "removed_at": None})
            if not self.teardown_topology(project, final=True):
                retained.append({"kind": "recovery-target", "name": project, "sha256": None})
        if retained:
            self.journal_update(retained=retained)
            self.close_operation("failed_preserved", last_error={
                "code": "isolated-topology-kept",
                "message": "the teardown of the recovery target was refused; it is kept and recorded"})
            log(f"Operation {self.operation_id} closed failed_preserved; the recovery target is kept ('"
                f"{self.pf_command()} cleanup --apply --recovery-target {retained[0]['name']}' removes it).")
            return 0
        self.close_operation("cancelled")
        log(f"Operation {self.operation_id} abandoned; the recovery target was removed.")
        return 0

    # ------------------------------------------- capacity model (PF-A3.3 section 3.8)

    CAPACITY_PHASES = {
        "backup": ("capturing", "verifying"), "update": ("preserving", "migrating"),
        "rollback": ("preserving-current", "restoring-candidate"), "reset-db": ("preserving", "initializing"),
        "purge": ("preserving", "capturing", "verifying"), "abort-deploy": ("preserving",),
        "restore-instance": ("preparing-target",), "restore-side-by-side": ("preparing-target", "restoring-data"),
        "deploy": ("preparing",), "cleanup": ("capturing",)}

    def database_sizes(self):
        """{database: pg_database_size} (read-only; an empty or unreadable answer counts nothing)."""
        sizes = {}
        try:
            text = self.sql("postgres", "SELECT datname, pg_database_size(datname) FROM pg_database WHERE NOT "
                                        "datistemplate;")
        except DaemonFailure:
            raise
        except Failure:
            return sizes
        for line in (text or "").splitlines():
            name, separator, size = line.partition("|")
            if separator and PG_IDENTIFIER_RE.fullmatch(name) and re.fullmatch(r"[0-9]{1,18}", size):
                sizes[name] = int(size)
        return sizes

    @staticmethod
    def tree_bytes(path, limit=200000):
        total = count = 0
        for current, dirs, files in os.walk(str(path)):
            for name in files:
                try:
                    total += os.lstat(os.path.join(current, name)).st_size
                except OSError:
                    pass
                count += 1
                if count > limit:
                    return total
        return total

    def capacity_needs(self, kind, *, view=None, load=False, sizes=None):
        """Section 3.8 table: [(phase, role, path, bytes)] of ``kind`` (estimates)."""
        needs = []
        docker = self.docker_root()
        backups, private = self.backups_root, self.context.paths.private_state
        if kind in ("backup", "update", "rollback", "reset-db", "purge", "abort-deploy"):
            db = self.database_sizes() if sizes is None else sizes
            active = self.env()["POSTGRES_DB"]
            live = db.get(active, 0)
            deployment = self.current_deployment()
            source = deployment.record["source"]["archive"]["size"] if deployment is not None \
                and deployment.mismatch is None else 0
            phase = {"backup": "capturing", "rollback": "preserving-current"}.get(kind, "preserving")
            needs.append((phase, "backups", backups, live + source))
            needs.append(("verifying" if kind == "backup" else phase, "docker-root", docker,
                          pf_config.restored_estimate(live_bytes=live)))
            if kind == "update":
                needs.append(("migrating", "docker-root", docker, 2 * live))
            if kind == "rollback" and view is not None:
                payload = view.payload(view.active_store["dump"]) or {"size": 0}
                needs.append(("restoring-candidate", "docker-root", docker,
                              pf_config.restored_estimate(dump_bytes=payload["size"])))
            if kind == "reset-db":
                needs.append(("initializing", "docker-root", docker, 0))
            if kind == "purge":
                total = sum(db.values())
                recovery = self.recovery_root if real_directory(self.recovery_root) else self.recovery_root.parent
                needs.append(("capturing", "recovery", recovery, total + source + self.tree_bytes(self.backups_dir)
                              + self.tree_bytes(self.state)))
                needs.append(("capturing", "docker-root", docker, pf_config.restored_estimate(live_bytes=live)))
                needs.append(("verifying", "docker-root", docker, pf_config.restored_estimate(live_bytes=total)))
                needs.append(("verifying", "private-state", private, source * 4 + ARCHIVE_MARGIN))
        elif kind in ("restore-instance", "restore-side-by-side"):
            dumps = sum(pf_config.restored_estimate(dump_bytes=(view.payload(store["dump"]) or {"size": 0})["size"])
                        for store in view.stores)
            images = (view.payload("images.tar") or {"size": 0})["size"] if load else 0
            if kind == "restore-instance":
                needs.append(("preparing-target", "docker-root", docker, images + dumps))
                source = view.payload(view.source_payload) or {"expanded_bytes": 0, "size": 0}
                expanded = source.get("expanded_bytes") or 4 * source["size"]
                needs.append(("preparing-target", "private-state", private, expanded + ARCHIVE_MARGIN))
                needs.append(("preparing-target", "artifacts", self.context.artifacts_dir,
                              source["size"] + ARTIFACT_MARGIN))
                needs.append(("preparing-target", "workspace", self.root.parent, expanded))
            else:
                needs.append(("preparing-target", "docker-root", docker, images))
                needs.append(("restoring-data", "docker-root", docker, dumps))
        elif kind == "deploy":
            needs.append(("preparing", "docker-root", docker, 0))
        elif kind == "cleanup":
            for gen in (sizes or {}).get("generations", ()):
                needs.append(("capturing", "backups", backups,
                              self.tree_bytes(pf_instance.generation_container(self.context) / gen)))
        return needs

    def docker_root(self):
        observation = self.verify_daemon()
        root = getattr(observation, "root_dir", None)
        return Path(root) if root else None

    def capacity_unmeasurable(self, path, role, detail):
        text = (f"capacity-unmeasurable: free space of {path or 'the Docker data root'} ({role}) cannot be measured "
                f"({detail}). Nothing was changed.")
        if (self.plan or {}).get("kind") == "backup" or self._operation_command == "backup":
            text += f" '{self.pf_command()} backup --emergency' preserves the database without this check."
        exc = Failure(text)
        exc.code = "capacity-unmeasurable"
        return exc

    def measure(self, path, role):
        """(st_dev, free bytes) of ``path`` (registered roles: of its nearest existing ancestor; the Docker root must
        exist). OSError or an unknown Docker root -> ``capacity-unmeasurable``."""
        if path is None:
            raise self.capacity_unmeasurable(None, role, "the daemon reported no DockerRootDir")
        current = Path(path)
        if role != "docker-root":
            while not os.path.lexists(str(current)) and current != current.parent:
                current = current.parent
        try:
            info = os.statvfs(str(current))
            device = os.stat(str(current)).st_dev
        except OSError as exc:
            raise self.capacity_unmeasurable(path, role, exc.strerror or str(exc)) from exc
        return device, info.f_bavail * info.f_frsize

    def capacity_decide(self, needs, *, phase=None, tail="Nothing was changed."):
        """Measure every device of ``needs`` (phase-filtered), record the decisions in ``capacity.json`` and refuse a
        shortfall (``capacity-insufficient``); never deletes anything to make room."""
        selected = [item for item in needs if phase is None or item[0] == phase]
        if not selected:
            return
        floor = int(self.config["minimum_free_mb"]) * 1024 * 1024
        measured, frees = [], {}
        for item_phase, role, path, size in selected:
            device, free = self.measure(path, role)
            frees[device] = free
            measured.append((item_phase, role, str(path), device, size))
        shortfalls = pf_config.capacity_shortfalls(measured, frees, floor, phase=phase)
        decisions = []
        for device in sorted(frees):
            entries = [item for item in measured if item[3] == device]
            short = next((item for item in shortfalls if item.device == device), None)
            decisions.append({"phase": phase or "preflight", "device": device,
                              "roles": sorted({item[1] for item in entries}),
                              "paths": sorted({item[2] for item in entries}),
                              "need_bytes": sum(item[4] for item in entries), "free_bytes": frees[device],
                              "floor_bytes": floor, "result": "short" if short else "ok"})
        if self.operation_dir is not None:
            path = self.operation_dir / "capacity.json"
            try:
                existing = pf_instance.read_private_list(path)
            except pf_instance.ContextError:
                existing = []
            pf_instance.rewrite_private_list(path, existing + decisions)
        if shortfalls:
            mib = 1024 * 1024
            parts = [f"{item.need // mib} MiB on device {item.device} ({', '.join(item.roles)}: {', '.join(item.paths)}), "
                     f"{item.free // mib} MiB free including the {item.floor // mib} MiB safety floor"
                     for item in shortfalls]
            exc = Failure(f"capacity-insufficient: {phase or 'the operation'} needs " + "; ".join(parts) + ". " + tail)
            exc.code = "capacity-insufficient"
            raise exc

    def capacity_preflight(self, kind, **sizes):
        """Section 3.8: before the confirmation, every phase of ``kind`` summed per device with one floor."""
        self.capacity_decide(self.capacity_needs(kind, **sizes))

    def capacity_check(self, phase):
        """Section 3.8 re-check at the start of ``phase`` (before its first effect's intent generation)."""
        kind = self.plan["kind"]
        if phase not in self.CAPACITY_PHASES.get(kind, ()) or phase in self._capacity_checked:
            return
        self._capacity_checked.add(phase)
        extra = {}
        if kind in ("restore-instance", "restore-side-by-side", "rollback") and self.plan["input_bundle"]:
            extra["view"] = self.op_recovery() if kind != "rollback" else self.op_selected()
        if kind == "restore-side-by-side":
            extra["load"] = bool(self.effects_of(type="image-load"))
        if kind == "cleanup":
            extra["sizes"] = {"generations": pf_config.cleanup_selectors(self.plan)["generations"]}
        try:
            needs = self.capacity_needs(kind, **extra)
        except DaemonFailure:
            raise
        self.capacity_decide(needs, phase=phase, tail=f"The operation stopped before {phase}.")

    # ------------------------------------------- pf cleanup (PF-A3.3 section 3.9)

    @staticmethod
    def folder_identity(path):
        try:
            info = os.lstat(str(path))
        except OSError:
            return None
        return f"{info.st_dev}:{info.st_ino}" if stat.S_ISDIR(info.st_mode) else None

    def history_duplicated(self, name):
        """Whether every checkpoint of the displaced history ``name`` exists in the active history with the same bundle
        ID and manifest hash (read-only). Returns (duplicated, [(checkpoint, level, present)])."""
        listing = []
        displaced = self.revisions_root / name
        duplicated = True
        try:
            names = sorted(os.listdir(str(displaced)))
        except OSError:
            return False, listing
        for item in names:
            if not BACKUP_RE.fullmatch(item):
                continue
            try:
                data = pf_instance.read_bytes_nofollow(displaced / item / "manifest.json")
            except OSError:
                duplicated = False
                listing.append((item, "unreadable", False))
                continue
            try:
                active = pf_instance.read_bytes_nofollow(self.backups_dir / item / "manifest.json")
            except OSError:
                active = None
            present = active is not None and pf_instance.sha256_bytes(active) == pf_instance.sha256_bytes(data)
            level, _ = self.verification_level(item, pf_instance.sha256_bytes(data), quiet=True)
            listing.append((item, LEVEL_NAMES.get(level, level), present))
            duplicated = duplicated and present
        return duplicated, listing

    def cleanup_observations(self, index):
        """The read-only observations of section 3.9 candidate discovery."""
        names = set()
        current = None
        try:
            current = self.env()["POSTGRES_DB"]
            # Read-only and without a container inspect: a stopped database answers nothing (no names).
            names = self.database_names()
        except DaemonFailure:
            raise
        except Failure:
            names = set()
        topologies = {}
        for entry in index.entries:
            if entry.plan is None:
                continue
            for project, value in self.operation_topologies(entry.plan):
                if project not in topologies and self.topology_resources(project, value):
                    topologies[project] = value
        attempts, histories = {}, {}
        for entry in index.entries:
            for artifact in (entry.journal or {}).get("retained_artifacts", []):
                if artifact["kind"] == "bundle-attempt" and artifact["name"] not in attempts:
                    folder = self.backups_dir / artifact["name"]
                    if not real_directory(folder):
                        attempts[artifact["name"]] = None
                        continue
                    try:
                        self.verify_snapshot(artifact["name"])
                        attempts[artifact["name"]] = "sealed"
                    except Failure:
                        attempts[artifact["name"]] = "unsealed"
                if artifact["kind"] == "checkpoint-history" and artifact["name"] not in histories:
                    if real_directory(self.revisions_root / artifact["name"]):
                        histories[artifact["name"]] = "duplicated" if self.history_duplicated(artifact["name"])[0] \
                            else "unique"
                    else:
                        histories[artifact["name"]] = None
        _, generations, stages = self.generation_listing()
        pointer = self.read_pointer() or {}
        active = None
        failed_op = pointer.get("deployment_seal_failed")
        if isinstance(failed_op, str) and OPERATION_ID_RE.fullmatch(failed_op):
            try:
                record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                    self.context.operations_dir / failed_op / "deployment-artifact.json"), label="deployment-artifact")
                active = record.get("deployment_id") if isinstance(record, dict) else None
            except (OSError, pf_instance.ContextError):
                active = None
        abandoned = []
        loaded = {}
        for entry in index.entries:
            if entry.plan is None or entry.journal is None or entry.kind != "restore-instance" \
                    or entry.journal["phase"] != "cancelled":
                continue
            for effect in entry.plan["effects"]:
                if effect["type"] == "image-load":
                    text = pf_config._effect_evidence(entry.journal, effect["effect_id"]) or ""
                    for short in re.findall(r"[0-9a-f]{12}", text.split("images:", 1)[-1]):
                        loaded[short] = entry.operation_id
        if loaded:
            inventory = self.docker_inventory()
            for item in inventory.owned:
                if item.kind == "image" and item.identity["image_id"][7:19] in loaded:
                    abandoned.append((item.key, loaded[item.identity["image_id"][7:19]]))
        return {"databases": names, "current_database": current, "topologies": topologies, "attempts": attempts,
                "stages": {name[len("stage-"):] for name in stages}, "generations": set(generations),
                "workspace_generation": None, "histories": histories,
                "pf_recovery": {name for name in names if name.startswith("pf_recovery_")},
                "pf_keep": {name for name in names if name.startswith("pf_keep_")},
                "unsealed_active_staging": active, "abandoned_tags": abandoned}

    def attempt_tags(self, bundle_id, index):
        """``<project>-<svc>:backup-<id>`` tags of a bundle attempt that no readable checkpoint or purge manifest names
        (the owned instance tag grammar; never a prefix)."""
        inventory = self.docker_inventory()
        named = set()
        for item in self.snapshots():
            if isinstance(item, BundleView):
                named.update(image["reference"] for image in item.images.values())
        for item in self.recoveries():
            if isinstance(item, BundleView):
                named.update(item.purge["saved_image_refs"])
        wanted = {f"{self.context.compose_project}-{service}:backup-{bundle_id.lower()}"
                  for service in pf_docker.BUILT_SERVICES}
        return sorted(item.key for item in inventory.owned if item.kind == "image" and item.key in wanted
                      and item.key not in named)

    def cleanup(self, apply=False, generations=(), histories=(), targets=()):
        """``pf cleanup`` (observe-only report) and ``pf cleanup --apply`` (the journaled ``cleanup`` operation)."""
        index = self.operation_index()
        slug = self.context.slug
        if not apply:
            log(f"Cleanup report of instance {slug} (read-only; nothing is changed):")
            if index.overflow or any(item.cls == "invalid" for item in index.blocking):
                self.log_operations(index)
                log("Cleanup candidates: not listed while the operation index is overflowing or invalid "
                    "(SYNOLOGY_ADMIN §16)")
                return 0
        items = pf_config.cleanup_candidates(index, self.cleanup_observations(index))
        selector_only = {"generation": "--generation", "checkpoint-history": "--checkpoint-history",
                         "recovery-target": "--recovery-target"}
        if not apply:
            if not items:
                log("Cleanup candidates: none")
            for item in items:
                if item.cls == "report-only":
                    log(f"  report-only (never removed): {item.detail} {item.name}"
                        + (f" (operation {item.operation_id})" if item.operation_id else ""))
                elif item.cls in selector_only:
                    extra = ""
                    if item.cls == "checkpoint-history":
                        _, listing = self.history_duplicated(item.name)
                        extra = "; " + ", ".join(f"{name} [{level}] "
                                                 + ("also in the active history (same manifest)" if present
                                                    else "ONLY HERE") for name, level, present in listing)
                    log(f"  {item.cls} {item.name} (operation {item.operation_id}; only with {selector_only[item.cls]} "
                        f"{item.name}){extra}")
                else:
                    log(f"  {item.cls} {item.name} (operation {item.operation_id})")
            log(f"Remove the default set with '{self.pf_command()} cleanup --apply'.")
            return 0
        # --apply: freeze the closed list of the default set and the selected items.
        def unknown(selector, name, reason):
            exc = Failure(f"cleanup-target-unknown: {selector} {name} is not a cleanup candidate of instance {slug} "
                          f"({reason}). Nothing was changed.")
            exc.code = "cleanup-target-unknown"
            return exc

        by_class = {}
        for item in items:
            by_class.setdefault(item.cls, {})[item.name] = item
        chosen = [item for item in items if item.cls in ("candidate-database", "isolated-topology", "bundle-attempt",
                                                          "workspace-stage")]
        for selector, cls, names in (("--generation", "generation", generations),
                                     ("--checkpoint-history", "checkpoint-history", histories),
                                     ("--recovery-target", "recovery-target", targets)):
            for name in names:
                item = by_class.get(cls, {}).get(name)
                if item is None:
                    raise unknown(selector, name, "not recorded by a closed operation or no longer present")
                if cls == "checkpoint-history" and item.detail != "duplicated":
                    _, listing = self.history_duplicated(name)
                    unique = next((entry[0] for entry in listing if not entry[2]), "?")
                    exc = Failure(f"cleanup-history-unique-checkpoint: {name} holds checkpoint {unique} that is not in "
                                  "the active history (or differs from it); removing it would delete the only copy. "
                                  "Nothing was changed.")
                    exc.code = "cleanup-history-unique-checkpoint"
                    raise exc
                chosen.append(item)
        if not chosen:
            exc = Failure(f"cleanup-nothing: instance {slug} has nothing to clean up. Nothing was changed.")
            exc.code = "cleanup-nothing"
            raise exc
        self.capacity_preflight("cleanup", sizes={"generations": tuple(generations)})
        op8 = self.operation_id[-8:]
        lines = []
        for item in chosen:
            lines.append(f"  {item.cls} {item.name} (recorded by operation {item.operation_id})")
        summary = (f"Remove the disposable leftovers of instance {slug} recorded by closed operations (a prefix is "
                   "never authority; every item is re-observed immediately before its removal and kept when it "
                   "changed):\n" + "\n".join(lines))
        log(summary)
        effects, plans = [], {}
        tags = []
        for item in chosen:
            if item.cls in ("isolated-topology", "recovery-target"):
                inventory = self.docker_inventory(scope=(item.name, item.identity))
                plan = self.plan_for("isolated-topology", inventory, command="cleanup " + item.name)
                plans[item.name] = (self.write_deletion_plan(plan, name=f"deletion-plan-{item.name}.json", once=True),
                                    item)
            if item.cls == "bundle-attempt":
                tags += self.attempt_tags(item.name, index)
        if tags:
            plan = self.plan_for("image-tags", self.docker_inventory(), command="cleanup image tags",
                                 select={("image", tag) for tag in tags})
            plans["image-tags"] = (self.write_deletion_plan(plan, name="deletion-plan-image-tags.json", once=True),
                                   None)
        for item in chosen:
            if item.cls == "generation":
                container = pf_instance.generation_container(self.context)
                effects.append({"phase": "capturing", "type": "capture", "target": "generation:" + item.name,
                                "postcondition": "sealed", "preconditions": [
                                    "from:" + item.operation_id,
                                    "identity:" + str(self.folder_identity(container / item.name))]})
        for item in chosen:
            if item.cls == "candidate-database":
                effects.append({"phase": "deleting", "type": "database-drop", "target": "database:" + item.name,
                                "postcondition": "absent", "preconditions": ["from:" + item.operation_id]})
        for key, (reference, item) in sorted(plans.items()):
            preconditions = ["plan-sha256:" + reference["sha256"]]
            if item is not None:
                preconditions += ["from:" + item.operation_id, "topology:" + item.name, "topology-uuid:" + item.identity]
            effects.append({"phase": "deleting", "type": "resource-delete", "target": "deletion-plan:" + key,
                            "postcondition": "every planned item removed, already absent or kept",
                            "preconditions": preconditions})
        container = pf_instance.generation_container(self.context)
        for item in chosen:
            if item.cls == "bundle-attempt":
                path = self.backups_dir / item.name
                target = "remove:bundle-attempt:" + item.name
            elif item.cls == "workspace-stage":
                path = container / ("stage-" + item.name)
                target = "remove:stage:" + item.name
            elif item.cls == "generation":
                path = container / item.name
                target = "remove:generation:" + item.name
            elif item.cls == "checkpoint-history":
                path = self.revisions_root / item.name
                target = "remove:checkpoint-history:" + item.name
            else:
                continue
            preconditions = ["from:" + item.operation_id, "identity:" + str(self.folder_identity(path))]
            if item.cls == "generation":
                preconditions.append("seal-before-remove")
            effects.append({"phase": "deleting", "type": "file-write", "target": target,
                            "postcondition": "absent or kept", "preconditions": preconditions})
        phrase = f"CLEANUP {self.context.compose_project} {op8}"
        confirm(phrase, summary)
        for item in chosen:
            if item.cls == "checkpoint-history":
                _, listing = self.history_duplicated(item.name)
                confirm("DELETE CHECKPOINT HISTORY " + item.name, f"The displaced history {item.name} holds "
                        + ", ".join(f"{name} [{level}] also in the active history (same manifest)"
                                    for name, level, _ in listing) + "; it is removed.")
            if item.cls == "recovery-target":
                confirm("REMOVE RECOVERY TARGET " + item.name, f"The side-by-side recovery target {item.name} "
                        "(its containers, internal network and data volume) is removed.")
        self.open_operation(
            "cleanup", effects=effects, workspace=self.workspace_plan("untouched"), images={},
            confirmation=self.confirmation_ref(phrase, summary),
            source={"provenance": "not_applicable", "commit": None, "entries_sha256": None, "deployment_id": None})
        return self.run_plan({})

    def run_cleanup(self, ctx):
        """Section 3.9: the frozen list in plan order; every per-item refusal or failure keeps the item (effect
        ``partial``) and the cleanup continues; it closes failed_preserved listing the kept items. A drifted or
        unreachable daemon stops it open (``resume`` continues, ``--abandon`` closes)."""
        for effect in self.plan["effects"]:
            if self.journal["phase"] in pf_config.TERMINAL_PHASES:
                return 0
            state = self.effect_state(effect["effect_id"])
            evidence = self.journal_effect(effect["effect_id"])["evidence"] or ""
            if state == "complete" or (state == "partial" and evidence.startswith("kept:")):
                continue
            self.capacity_check(effect["phase"])
            self.run_effect(effect["effect_id"], self.cleanup_action(effect))
            self.inside("cleanup-item")
        kept = [effect for effect in self.plan["effects"] if self.effect_state(effect["effect_id"]) == "partial"]
        if kept:
            listing = ", ".join(f"{effect['type']} {effect['target']}" for effect in kept)
            message = f"cleanup-items-kept: {len(kept)} item(s) were kept: {listing}"[:2000]
            self.close_operation("failed_preserved", last_error={"code": "cleanup-items-kept", "message": message})
            raise Failure(f"cleanup-items-kept: cleanup {self.operation_id} closed failed_preserved; kept: {listing}")
        self.close_operation("completed")
        log(f"Cleanup {self.operation_id} completed.")
        return 0

    def kept(self, step, code, text):
        """A per-item refusal or failure: the item is kept, the effect ``partial`` and the cleanup continues."""
        step.outcome = "partial"
        step.evidence = f"kept: {code}"
        log(text)

    def cleanup_action(self, effect):
        etype, target = effect["type"], effect["target"]
        op = self.operation_id

        def guarded(body):
            def action(step):
                try:
                    body(step)
                except DaemonFailure:
                    raise
                except SimulatedCrash:
                    raise
                except (Failure, OSError, pf_instance.ContextError, pf_source.SourceError) as exc:
                    code = failure_code(exc) if isinstance(exc, Failure) else "os-error"
                    if code == "plan-drift" and "Planned daemon " in str(exc):
                        raise
                    detail = (str(getattr(exc, "strerror", None) or exc).splitlines() or ["?"])[0][:300]
                    self.kept(step, code, f"cleanup-item-failed: {target}: {code}: {detail}; it was kept; cleanup "
                                          f"{op} continues and closes failed_preserved.")
            return action

        if etype == "capture":
            return guarded(lambda step: self.seal_generation(step, target.split(":", 1)[1], effect))
        if etype == "database-drop":
            return guarded(lambda step: self.cleanup_drop(step, target.split(":", 1)[1]))
        if etype == "resource-delete":
            return guarded(lambda step: self.cleanup_delete(step, effect))
        return guarded(lambda step: self.cleanup_remove(step, effect))

    def cleanup_drop(self, step, name):
        """A recorded leftover candidate database: still present, not current, no open session; then dropped."""
        op = self.operation_id
        names = self.database_names()
        if name not in names:
            step.evidence = "already-absent"
            return
        if name == self.env()["POSTGRES_DB"]:
            return self.kept(step, "cleanup-item-changed", f"cleanup-item-changed: database {name} changed after the "
                             f"cleanup was approved (it is the current database); it was kept; cleanup {op} continues.")
        sessions = self.sql("postgres", f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{name}';").strip()
        if sessions not in ("", "0"):
            return self.kept(step, "cleanup-item-busy", f"cleanup-item-busy: {name} has {sessions} open session(s); it "
                             f"was kept; cleanup {op} continues.")
        self.drop_database(name)
        if name in self.database_names():
            raise Failure(f"plan-effect-unconfirmed: database {name} is still present after the drop")
        step.evidence = "removed"

    def cleanup_delete(self, step, effect):
        """A frozen topology/target or image-tag deletion plan of this cleanup, executed item by item."""
        key = effect["target"].split(":", 1)[1]
        digest = self.precondition(effect, "plan-sha256")
        topology_uuid = self.precondition(effect, "topology-uuid")
        kind = "image-tags" if key == "image-tags" else "isolated-topology"
        plan = self.load_frozen_deletion_plan(kind, digest, name=f"deletion-plan-{key}.json",
                                              instance_id=topology_uuid if kind == "isolated-topology" else None)
        deleted = self.execute_deletion_plan(plan, progress=f"deletion-progress-{key}.json",
                                             on_item=lambda item: self.inside("cleanup-delete"))
        step.evidence = f"removed {sum(1 for item in deleted if item.get('outcome') == 'removed')} of " \
                        f"{len(plan['candidates'])} planned items"
        recorded = self.precondition(effect, "from")
        if kind == "isolated-topology" and recorded:
            # Section 3.17: the recording operation's topology files (the throwaway password) go with the target.
            directory = self.context.operations_dir / recorded / "isolated" / key
            if real_directory(directory):
                fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    for name in ("compose.json", "app.env"):
                        if pf_instance.identity_at(fd, name) is not None:
                            os.unlink(name, dir_fd=fd)
                    record = None
                    if pf_instance.identity_at(fd, "topology.json") is not None:
                        record = pf_instance.parse_strict_json(pf_instance.read_bytes_nofollow(
                            directory / "topology.json"), label="topology.json")
                    if isinstance(record, dict):
                        record.update(state="removed", removed_at=utc(), container_ids=[])
                        pf_instance._write_private_file(directory / "topology.json",
                                                        pf_instance.normalize_json(record), 0o600)
                    os.fsync(fd)
                finally:
                    os.close(fd)

    def cleanup_path(self, target):
        """(parent directory, entry name) of a ``remove:*`` / ``generation:`` cleanup target."""
        container = pf_instance.generation_container(self.context)
        if target.startswith("remove:bundle-attempt:"):
            return self.backups_dir, target.split(":", 2)[2]
        if target.startswith("remove:stage:"):
            return container, "stage-" + target.split(":", 2)[2]
        if target.startswith("remove:generation:"):
            return container, target.split(":", 2)[2]
        if target.startswith("remove:checkpoint-history:"):
            return self.revisions_root, target.split(":", 2)[2]
        raise Failure(f"Internal error: no cleanup path for {target}.")

    def seal_path(self, gen):
        return self.backups_root / "generations" / self.context.compose_project / gen

    def read_seal(self, gen):
        """The valid ``seal.json`` of ``gen`` (AM-17) whose archive re-hashes, else None (read-only)."""
        folder = self.seal_path(gen)
        try:
            data = pf_instance.read_bytes_nofollow(folder / "seal.json")
            record = pf_instance.parse_strict_json(data, label="seal.json")
        except (OSError, pf_instance.ContextError):
            return None
        if not isinstance(record, dict) or lifecycle_errors(record, "generation_seal") \
                or record["generation_id"] != gen or record["instance_id"] != self.context.instance_id:
            return None
        try:
            if digest(folder / "workspace.tar.gz") != record["archive"]["sha256"]:
                return None
        except OSError:
            return None
        return record

    def cleanup_remove(self, step, effect):
        """``remove:*``: the item must still have the identity recorded at plan time; a displaced history is
        re-checked against the active history and a generation against its seal and open handles immediately before
        the descriptor-relative removal (links removed, never followed)."""
        target, op = effect["target"], self.operation_id
        parent, name = self.cleanup_path(target)
        recorded = self.precondition(effect, "identity")
        identity = self.folder_identity(parent / name)
        if identity is None:
            step.evidence = "already-absent"
            return
        if identity != recorded:
            return self.kept(step, "cleanup-item-changed", f"cleanup-item-changed: {target} changed after the cleanup "
                             f"was approved (identity {identity} vs {recorded}); it was kept; cleanup {op} continues.")
        if target.startswith("remove:bundle-attempt:"):
            try:
                self.verify_snapshot(name)
                return self.kept(step, "bundle-attempt-sealed", f"note: bundle-attempt-sealed: {name} now reads as a "
                                 "sealed bundle; it is kept.")
            except Failure:
                pass
        if target.startswith("remove:checkpoint-history:") and not self.history_duplicated(name)[0]:
            return self.kept(step, "cleanup-item-changed", f"cleanup-item-changed: {target} changed after the cleanup "
                             f"was approved (a checkpoint is no longer in the active history); it was kept; cleanup "
                             f"{op} continues.")
        if target.startswith("remove:generation:"):
            capture = next((item for item in self.plan["effects"] if item["target"] == "generation:" + name), None)
            seal = self.read_seal(name)
            if capture is None or self.effect_state(capture["effect_id"]) != "complete" or seal is None:
                return self.kept(step, "not-sealed", f"cleanup-item-failed: {target}: not sealed; it was kept; cleanup "
                                 f"{op} continues and closes failed_preserved.")
            self.inside("before-remove")
            fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                identities = pf_instance.tree_identities(fd, name, limit=pf_source.MANIFEST_ENTRY_LIMIT)
            finally:
                os.close(fd)
            try:
                holders = pf_instance.open_handles(identities)
            except pf_instance.ContextError as exc:
                return self.kept(step, "cleanup-item-changed", f"cleanup-item-changed: {target} changed after the "
                                 f"cleanup was approved (open handles cannot be verified: {exc}); it was kept; cleanup "
                                 f"{op} continues.")
            current = pf_source.entries_digest(pf_source.build_manifest(parent / name, source={"kind": "unknown"},
                                                                        excludes=()))
            if holders or current != seal["entries_sha256"]:
                reason = f"{len(holders)} open handle(s)" if holders else \
                    f"content {current[:12]} vs sealed {seal['entries_sha256'][:12]}"
                return self.kept(step, "cleanup-item-changed", f"cleanup-item-changed: {target} changed after the "
                                 f"cleanup was approved ({reason}); it was kept; cleanup {op} continues.")
        fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if pf_instance.identity_at(fd, name) is not None and f"{pf_instance.identity_at(fd, name)[0]}:" \
                    f"{pf_instance.identity_at(fd, name)[1]}" != recorded:
                return self.kept(step, "cleanup-item-changed", f"cleanup-item-changed: {target} changed after the "
                                 f"cleanup was approved; it was kept; cleanup {op} continues.")
            pf_instance.remove_private_tree_at(fd, name)
            os.fsync(fd)
        finally:
            os.close(fd)
        step.evidence = "removed"

    def seal_generation(self, step, gen, effect=None):
        """Section 3.9 generation seal: container rule, a link-free inventory, no open handle, digest d1, the archive
        (partial, fsync, rename, pass 1), handles and digest d2 again, then ``seal.json`` (AM-17)."""
        op = self.operation_id
        container = pf_instance.generation_container(self.context)
        source = container / gen

        def keep(code, text):
            self.kept(step, code, text)

        parent_fd = os.open(str(container.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            problem = pf_instance.generation_container_problem(parent_fd, container.name,
                                                               os.lstat(str(self.root.parent)).st_dev)
        finally:
            os.close(parent_fd)
        if problem is not None:
            return keep("generation-container-unsafe", f"cleanup-item-failed: generation:{gen}: "
                        f"generation-container-unsafe: {problem}; it was kept; cleanup {op} continues and closes "
                        "failed_preserved.")
        if effect is not None and self.precondition(effect, "identity") != self.folder_identity(source):
            return keep("cleanup-item-changed", f"cleanup-item-changed: generation:{gen} changed after the cleanup was "
                        f"approved; it was kept; cleanup {op} continues.")
        for relative, kind, fd, _ in pf_source.walk_tree(source, excludes=()):
            if fd is not None:
                os.close(fd)
            if kind != "file":
                return keep("generation-unsupported-entry", f"generation-unsupported-entry: {gen} holds a link or "
                            f"special file ({relative}); it was not sealed and is kept; cleanup {op} continues and "
                            "closes failed_preserved.")

        def handles():
            fd = os.open(str(container), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                identities = pf_instance.tree_identities(fd, gen, limit=pf_source.MANIFEST_ENTRY_LIMIT)
            finally:
                os.close(fd)
            return pf_instance.open_handles(identities)

        try:
            holders = handles()
        except pf_instance.ContextError as exc:
            return keep("generation-handles-unverifiable", f"generation-handles-unverifiable: {gen}: open handles "
                        f"cannot be verified on this host ({exc}); it was not sealed and is kept; cleanup {op} "
                        "continues and closes failed_preserved.")
        if holders:
            listed = "; ".join(f"{pid} {comm} {kind}" for pid, comm, _, kind in holders[:5])
            return keep("generation-in-use", f"generation-in-use: {gen}: {len(holders)} open handle(s) ({listed}); it "
                        f"was not sealed and is kept; cleanup {op} continues and closes failed_preserved.")
        checked = utc()
        first = pf_source.entries_digest(pf_source.build_manifest(source, source={"kind": "unknown"}, excludes=()))
        folder = self.seal_path(gen)
        for directory in (self.backups_root, self.backups_root / "generations",
                          self.backups_root / "generations" / self.context.compose_project):
            if not real_directory(directory):
                directory.mkdir(mode=0o750)
                self.apply_single("backups", directory, "dir")
        if real_directory(folder):
            remove_parent = os.open(str(folder.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.remove_private_tree_at(remove_parent, folder.name)
            finally:
                os.close(remove_parent)
        folder.mkdir(mode=0o750)
        partial = folder / "workspace.tar.gz.partial"
        archived = pf_source.archive_tree(source, partial, excludes=(), unsupported="refuse")
        fd = os.open(str(partial), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(partial), str(folder / "workspace.tar.gz"))
        fd = os.open(str(folder / "workspace.tar.gz"), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            pf_source.inspect_archive(fd, limits=pf_source.SOURCE_LIMITS)
            size, sha256 = os.fstat(fd).st_size, self._fd_sha256(fd)
        finally:
            os.close(fd)
        self.inside("seal")
        try:
            later = handles()
        except pf_instance.ContextError:
            later = [("?", "?", 0, "unverifiable")]
        second = pf_source.entries_digest(pf_source.build_manifest(source, source={"kind": "unknown"}, excludes=()))
        if later or first != second:
            remove_parent = os.open(str(folder.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.remove_private_tree_at(remove_parent, folder.name)
            finally:
                os.close(remove_parent)
            return keep("generation-unstable", f"generation-unstable: {gen} changed while it was sealed ({first[:12]} "
                        f"vs {second[:12]}); the seal was discarded and the generation kept; cleanup {op} continues "
                        "and closes failed_preserved.")
        retained_by = self.precondition(effect, "from") if effect is not None else None
        record = {"schema_version": 1, "generation_id": gen, "instance_id": self.context.instance_id,
                  "retained_by_operation": retained_by if retained_by and retained_by != op else None,
                  "sealed_by_operation": op, "entries_sha256": first,
                  "archive": {"path": "workspace.tar.gz", "size": size, "sha256": sha256,
                              "members": archived["members"], "expanded_bytes": archived["expanded_bytes"],
                              "members_sha256": archived["members_sha256"]},
                  "handles": {"checked_at": checked, "result": "none-open"}, "sealed_at": utc()}
        problems = lifecycle_errors(record, "generation_seal")
        if problems:
            raise Failure(f"Internal error: the generation seal of {gen} is invalid ({problems[0]}).")
        pf_instance._write_private_file(folder / "seal.json", pf_instance.normalize_json(record), 0o600)
        self.publish_fresh("backups", folder)
        step.evidence = "sealed " + first[:12]
        step.retained.append({"kind": "generation-seal", "name": gen, "sha256": None})

    def observe_cleanup(self, effect):
        """Section 3.12 cleanup rows."""
        etype, target = effect["type"], effect["target"]
        if etype == "capture":
            gen = target.split(":", 1)[1]
            if self.read_seal(gen) is not None:
                return "complete", "sealed " + self.read_seal(gen)["entries_sha256"][:12], None
            return "redo", "no valid seal (a partial seal folder is removed and redone)", None
        if etype == "database-drop":
            name = target.split(":", 1)[1]
            return ("complete", f"{name} absent", None) if name not in self.database_names() else \
                ("redo", f"{name} exists", None)
        if etype == "resource-delete":
            return "redo", "continue the frozen deletion plan", None
        return self.observe_cleanup_file(effect)

    def observe_cleanup_file(self, effect):
        parent, name = self.cleanup_path(effect["target"])
        if self.folder_identity(parent / name) is None:
            return "complete", "absent", None
        return "redo", "present", None

    def abandon_cleanup(self):
        """``resume --abandon`` of a cleanup: before any deleting effect started -> cancelled; after -> closed
        failed_preserved listing every not-yet-removed item (all disposable)."""
        deleting = [effect for effect in self.plan["effects"] if effect["phase"] == "deleting"]
        started = any(self.effect_state(effect["effect_id"]) != "not_started" for effect in deleting)
        if not started:
            self.close_operation("cancelled")
            log(f"Cleanup {self.operation_id} abandoned before any removal; nothing was removed.")
            return 0
        remaining = [effect["target"] for effect in deleting if self.effect_state(effect["effect_id"]) != "complete"]
        message = ("cleanup-abandoned: not removed: " + (", ".join(remaining) or "none"))[:2000]
        self.close_operation("failed_preserved", last_error={"code": "cleanup-abandoned", "message": message})
        log(f"Cleanup {self.operation_id} abandoned; {message}")
        return 0

    # ------------------------------------------- runner-record acknowledgement (PF-A3.3 section 3.10)

    def acknowledge_effects(self, operation_id):
        """``pf resume --operation <op> --acknowledge`` under the observe-only lock: the still-running probe, the
        observation of each runner record, one typed confirmation, then ``acknowledgement-<sha12>.json`` (exclusive
        create) is the only file written. Nothing is started, stopped or repaired."""
        index = self.operation_index()
        entry = index.entry(operation_id)

        def refuse(reason):
            exc = Failure(f"acknowledge-not-legal: operation {operation_id} {reason}. Nothing was changed.")
            exc.code = "acknowledge-not-legal"
            return exc

        if entry is None:
            raise refuse("does not exist")
        if entry.cls != "no-journal":
            raise refuse(f"has a journal; use '{self.pf_command()} resume --operation {operation_id}'")
        directory = self.context.operations_dir / operation_id
        try:
            data = pf_instance.read_bytes_nofollow(directory / "unresolved-effects.json")
            records = pf_runner.load_unresolved_effects(directory / "unresolved-effects.json")
        except (OSError, pf_runner.RunnerError):
            raise refuse("has no runner records")
        if not records:
            raise refuse("has no runner records")
        digest = pf_instance.sha256_bytes(data)
        if digest in entry.acknowledged:
            raise refuse("is already acknowledged at this record hash")
        hits = []
        oneoffs = set(pf_docker.owned_oneoffs(self.docker_inventory()))
        if oneoffs:
            running = {line.strip() for line in self.docker("ps", "-q", "--no-trunc").splitlines() if line.strip()}
            hits += [f"one-off container {item[:12]}" for item in sorted(oneoffs & running)]
        if self.db_running():
            count = self.sql("postgres", "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' "
                                         "AND pid <> pg_backend_pid();")
            if count.strip() not in ("0", ""):
                hits.append(f"{count.strip()} database session(s)")
        if hits:
            raise Failure(f"effect-still-running: operation {operation_id} still has a running {hits[0]}; an "
                          "acknowledgement needs a quiet instance. Nothing was changed.")
        observations = self.observe_records(records)
        log(f"Runner records of operation {operation_id} ({len(records)}):")
        for record in records:
            log(f"  {record.get('recorded_at')}: {record.get('tool')} {self._record_summary(record)} -> "
                f"{record.get('outcome')}")
        for line in observations:
            log("  observed: " + line)
        confirm("ACKNOWLEDGE " + operation_id[-8:],
                "Acknowledge that the observed state is the one you accept; nothing is started, stopped or repaired. "
                "'pf install' then no longer waits for these records.")
        record = {"schema_version": 1, "operation_id": operation_id, "records_sha256": digest,
                  "record_count": len(records), "observations": [item[:300] for item in observations][:64],
                  "acknowledged_at": utc(), "release_id": self.context.control.release_id, "attempt_pid": os.getpid()}
        problems = lifecycle_errors(record, "runner_acknowledgement")
        if problems:
            raise Failure(f"Internal error: the acknowledgement record is invalid ({problems[0]}); nothing was written.")
        try:
            pf_instance.write_once(directory, f"acknowledgement-{digest[:12]}.json", pf_instance.normalize_json(record))
        except FileExistsError as exc:
            raise refuse("is already acknowledged at this record hash") from exc
        log(f"acknowledged: runner records of operation {operation_id} ({len(records)}) acknowledged after "
            "observation; 'pf install' no longer waits for them.")
        return 0

    def observe_records(self, records):
        """Section 3.10 step 2: the current state of what each record names (identities only)."""
        lines = []
        services = {}
        for service in pf_docker.SERVICES:
            try:
                services[service] = "running" if self.inspect(service)["State"].get("Running") else "stopped"
            except DaemonFailure:
                raise
            except Failure:
                services[service] = "absent"
        lines.append("services " + ", ".join(f"{name} {state}" for name, state in services.items()))
        if services.get("db") == "running":
            try:
                lines.append("live heads " + (",".join(self.db_heads()) or "none"))
                names = self.database_names()
            except DaemonFailure:
                raise
            except Failure:
                names = set()
        else:
            names = set()
        for record in records[:60]:
            effect = record.get("effect") if isinstance(record.get("effect"), dict) else {}
            database = effect.get("database")
            text = f"{record.get('tool')} {effect.get('verb') or '?'}"
            if database:
                text += f" {database} {'present' if database in names else 'absent'}"
            lines.append(_identity_only(text))
        return lines[:64]

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

    def deploy(self, target=None, *, use_current=False, skip_ci=False, keep_workspace=False):
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
            # PF-A3.1: the candidate's manifest (provenance proven by the protected store above), the read-only
            # artifact capacity preflight before the confirmation. PF-A3.2: the workspace preflight (section 3.7).
            manifest = self.candidate_manifest(source, target["sha"], verified=True)
            self.deployment_preflight(manifest)
            self.capacity_preflight("deploy")
            purged = self.context.state == "purged"
            if purged:
                # OD-A33-08: a purged record holds no claim; a new deployment claims (daemon, project) again.
                self.require_project_claim()
            if use_current:
                workspace, lines = self.workspace_plan("record-current"), [
                    "Workspace refresh: none (--current deploys the workspace that is already the selected tree)"]
            else:
                workspace, lines = self.workspace_decision(manifest, keep_workspace)
            summary = ("Create a new PartFlow staging deployment. No existing project data will be adopted or deleted.\n"
                       + f"Source: {target['ref']} -> {target['sha']}\n" + self.environment_summary(values)
                       + "\n" + "\n".join(lines))
            phrase = "DEPLOY " + target["sha"][:12]
            confirm(phrase, summary)
            self.sweep_staging()
            deployment_id = f"dep-{utc()}-{uuid.uuid4().hex[:8]}"
            heads = sorted(contract["heads"])
            identities = {service: self.image_identity(images[service]["id"], reference=images[service]["reference"])
                          for service in pf_docker.BUILT_SERVICES}
            effects = [{"phase": "preparing", "type": "source-stage", "target": "deployment:" + deployment_id,
                        "postcondition": f"staged {deployment_id}", "preconditions": ["confirmed"]}]
            if purged:
                effects.append({"phase": "preparing", "type": "file-write", "target": "registry:state=registered",
                                "postcondition": "record state registered",
                                "preconditions": ["record-state:purged", "no other record claims the project"]})
            if use_current:
                effects.append({"phase": "preparing", "type": "file-write", "target": "source-manifest",
                                "postcondition": "bytes sha256 recorded at completion",
                                "preconditions": ["the workspace equals the selected commit"]})
                # PF-A2.3: no bulk change of the editable workspace without a verified editor freeze.
                log(f"Workspace permissions were not changed; check them with '{self.pf_command()} permissions check "
                    "--scope workspace'.")
            effects += [
                {"phase": "initializing", "type": "service-change", "target": "service:db:start",
                 "postcondition": "db healthy", "preconditions": ["no database of this instance exists"]},
                {"phase": "initializing", "type": "database-migrate",
                 "target": f"database:{values['POSTGRES_DB']}:heads={','.join(heads)}", "postcondition": "heads equal",
                 "preconditions": ["heads empty"]}]
            effects += self.activation_specs(identities, heads, deployment_id)
            effects += self.workspace_effect_specs(workspace, deployment_id=deployment_id)
            ctx = {"candidate": source, "manifest": manifest, "images": images, "ref": target["ref"],
                   "pointer": {**target, "checkpoint": None, "initial_deploy": True, "database_heads": heads}}
            return self.start_operation(
                "deploy", ctx, effects=effects, workspace=workspace, images=identities,
                confirmation=self.confirmation_ref(phrase, summary),
                source={"provenance": "git_commit", "commit": target["sha"],
                        "entries_sha256": pf_source.entries_digest(manifest), "deployment_id": deployment_id})

    @staticmethod
    def confirmation_ref(phrase, summary):
        return {"phrase": phrase[:120], "summary_sha256": pf_instance.sha256_bytes(summary.encode("utf-8"))}

    @staticmethod
    def activation_specs(images, heads, deployment_id, *, seal=True):
        """The activating effects (section 3.4): backend and frontend starts (the API health check is part of the
        frontend's postcondition), then the seal and the pointer."""
        def short(service):
            return images[service]["id"][7:19] if service in images else "bundle"

        effects = [
            {"phase": "activating", "type": "service-change", "target": f"service:backend:start:{short('backend')}",
             "postcondition": f"running healthy {short('backend')}", "preconditions": ["heads:" + ",".join(heads)]},
            {"phase": "activating", "type": "service-change", "target": f"service:frontend:start:{short('frontend')}",
             "postcondition": f"running healthy {short('frontend')}", "preconditions": ["backend healthy"]}]
        if seal:
            effects += [
                {"phase": "activating", "type": "artifact-seal", "target": "deployment:" + deployment_id,
                 "postcondition": "sealed", "preconditions": ["activation passed"]},
                {"phase": "activating", "type": "file-write", "target": "pointer:deployed.json",
                 "postcondition": "bytes sha256 recorded at completion", "preconditions": ["seal attempted"]}]
        return effects

    def start_operation(self, kind, ctx, **plan_args):
        """Write the plan and journal generation 1, then run the plan (section 3.1). A failure of the source-stage
        effect (only private files written) closes the operation cancelled after removing its own staging."""
        self.open_operation(kind, **plan_args)
        try:
            return self.run_plan(ctx)
        except StageFailed as exc:
            self.close_operation("cancelled")
            raise Failure(f"deployment-stage-failed: {exc.detail}. The application, database and workspace were not "
                          f"changed; operation {self.operation_id} was closed (cancelled) and its staging "
                          "removed.") from exc

    def abort_deploy(self):
        """``pf abort-deploy``: a new operation superseding the incomplete deploy (section 3.3), allowed only while the
        deploy's frontend effect has not started. Its frozen deletion plan is approved in generation 1; a repeated
        ``abort-deploy`` while it is open aliases ``pf resume`` (the gate re-enters it)."""
        self.staging()
        superseded = self.gate.entry if self.gate is not None and self.gate.action == "supersede" else None
        if superseded is None or superseded.kind != "deploy":
            raise Failure("No incomplete initial deployment exists.")
        if (self.state / "deployed.json").exists():
            raise Failure("A managed deployment record already exists; abort-deploy is only for an incomplete first "
                          "deployment.")
        self.require_not_running(superseded, database_effects=False)
        project = self.config["project"]
        try:
            database = self.env()["POSTGRES_DB"]
        except Failure:
            database = "unknown"
        # PF-A3.3 section 3.7a: after the frontend opened users may have written data; it is preserved first.
        preserve = pf_config._frontend_started(superseded.plan, superseded.journal)
        checkpoint_id = None
        if preserve:
            try:
                self.database_ready()
            except DaemonFailure:
                raise
            except Failure as exc:
                raise Failure(f"preservation-failed: the current database {database} is not reachable "
                              f"({str(exc).splitlines()[0]}), so the preservation that must precede the abort would "
                              f"fail. Start it with '{self.pf_command()} resume' or preserve it manually first. Nothing "
                              "was changed.") from exc
            self.capacity_preflight("abort-deploy")
            checkpoint_id = f"{utc()}-{'0' * 12}-{uuid.uuid4().hex[:6]}"
        plan = self.plan_for("abort-deploy", self.docker_inventory(), command="abort-deploy")
        self.log_plan(plan, title="Abort-deploy plan (exact resources of this instance; images are retained):")
        if preserve:
            summary = (f"Frontend access was opened, so users may have written data. The current database is preserved "
                       f"as checkpoint {checkpoint_id} (healthy or emergency; or the fallback ID the capture records) in "
                       f"{self.revisions_root}/{project} before the deployment's containers and volumes are deleted. "
                       "An emergency preservation is data and evidence, never a rollback target.\n"
                       f"Delete containers and Docker volumes created by the incomplete first deployment for database "
                       f"{database}. The source checkout and .env are kept so deployment can be retried.")
        else:
            summary = (f"Delete containers and Docker volumes created by the incomplete first deployment for database "
                       f"{database}. The source checkout and .env are kept so deployment can be retried. This is allowed "
                       "only before frontend access opened.")
        phrase = "ABORT DEPLOY " + project
        confirm(phrase, summary)
        reference = self.write_deletion_plan(plan)
        effects = []
        if preserve:
            effects += [
                {"phase": "preserving", "type": "service-change", "target": "services:stop:frontend,backend",
                 "postcondition": "stopped", "preconditions": ["confirmed"]},
                {"phase": "preserving", "type": "capture", "target": "checkpoint:before-abort",
                 "postcondition": "sealed and data_restore_verified",
                 "preconditions": ["bundle:" + checkpoint_id, "verify:pf_verify_" + uuid.uuid4().hex[:20],
                                   "writers stopped"], "preservation_refs": [checkpoint_id]}]
        effects += [{"phase": "deleting", "type": "resource-delete", "target": "deletion-plan",
                     "postcondition": "every planned item removed or already absent",
                     "preconditions": ["deletion plan " + reference["sha256"]],
                     "preservation_refs": [checkpoint_id] if checkpoint_id else []},
                    {"phase": "finalizing", "type": "file-write", "target": "override:active-images.yaml",
                     "postcondition": "absent", "preconditions": ["deletion complete"]}]
        registry = [effect for effect in superseded.plan["effects"] if pf_config.effect_role(effect) == "registry"]
        if registry and pf_config.effect_state(superseded.journal, registry[0]["effect_id"]) != "not_started":
            # OD-A33-08: the aborted first deployment of a purged instance leaves it purged again.
            effects.append({"phase": "finalizing", "type": "file-write", "target": "registry:state=purged",
                            "postcondition": "record state purged",
                            "preconditions": ["record-state:registered", "deletion complete"]})
        return self.start_operation(
            "abort-deploy", {}, effects=effects, workspace=self.workspace_plan("untouched"), images={},
            confirmation=self.confirmation_ref(phrase, summary), summary_text=summary,
            source={"provenance": "not_applicable", "commit": None, "entries_sha256": None, "deployment_id": None},
            supersedes=superseded.operation_id, deletion_plan_sha256=reference["sha256"],
            deletion={"plan_sha256": reference["sha256"], "delete_backups": False, "reset_admin_config": False,
                      "confirmed_at": utc()})

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

    def create_tree_archive(self, source, destination, arcname):
        """The revision-checkpoint history archive (``<arcname>/...``). PF-A3.1: links and special entries are
        refused while writing, and pass 1 of the importer measures the written archive for its payload entry; an
        archive the importer would refuse is never sealed into a bundle. Returns (expanded_bytes, members, sha)."""
        source, destination = Path(source), Path(destination)

        def select(info):
            if not (info.isfile() or info.isdir()):
                raise Failure(f"The checkpoint history holds a link or special file: {info.name}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode &= 0o777
            return info

        with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            if real_directory(source):
                archive.add(source, arcname=arcname, recursive=True, filter=select)
        fd = os.open(str(destination), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            inventory = pf_source.inspect_archive(fd, limits=pf_source.HISTORY_LIMITS)
        except pf_source.ArchiveRefused as exc:
            raise self.archive_failure("revision-checkpoints.tar.gz", exc) from exc
        finally:
            os.close(fd)
        return inventory.expanded_bytes, len(inventory.members), inventory.members_sha256

    def available_snapshot_image_refs(self):
        """Retained image tags of every checkpoint whose strict read succeeds (legacy migrated in memory); an
        unreadable checkpoint is skipped with a note."""
        refs = set()
        missing = []
        for item in self.snapshots():
            if isinstance(item, InvalidBundle):
                log(f"note: checkpoint {item.bundle_id} is unreadable ({item.code}); its image tags are not covered.")
                continue
            for value in item.images.values():
                reference = value["reference"]
                try:
                    self.docker("image", "inspect", reference)
                    refs.add(reference)
                except DaemonFailure:
                    raise
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
        """Create a verified recovery bundle before destructive project purge; return (BundleView, binding plan).

        PF-A1.3: after ``images.tar`` is verified the binding inventory and plan are built, the
        plan must equal the preliminary one (owned tags modulo the tags this operation created),
        and ``resources_before_purge`` is sealed from the binding candidates. The manifest is
        written once and never rewritten.

        PF-A3.1 (section 3.6): a schema 1 purge bundle. Payloads copied from the before-purge checkpoint and the
        current deployment are re-hashed after the copy; every store (active and retained, inside its connection
        window) carries its facts, heads and row counts; the db image is saved by ID with the application images;
        the manifest is sealed and then every store is restored from the bundle's own payloads into a
        ``pf_verify_*`` candidate (data_restore_verified record bound to this manifest's hash). PostgreSQL globals
        are archived as evidence and never executed automatically during restore.
        """
        self.database_ready()
        checkpoint = self.snapshot("before-purge")
        view, binding = self.capture_purge_bundle(preliminary, checkpoint=checkpoint)
        try:
            return self.verify_purge_bundle(view, None), binding
        except PlanChanged:
            raise
        except Failure as exc:
            if failure_code(exc) == "purge-bundle-verification-failed":
                raise PlanChanged(str(exc), checkpoint) from exc
            raise

    def capture_purge_bundle(self, preliminary, *, checkpoint, bundle_id=None):
        """PF-A3.2 ``capture purge-bundle`` (create_purge_recovery steps 2-5 and 7): the sealed bundle and the binding
        deletion plan; its verification is the next effect (verify_purge_bundle)."""
        commit = checkpoint.manifest["source"]["commit"]
        recovery_id = bundle_id or f"purge-{utc()}-{(commit or '0' * 40)[:12]}-{uuid.uuid4().hex[:6]}"
        self.ensure_recovery_tree()
        folder = self.recovery_root / recovery_id
        folder.mkdir(mode=0o700)
        for name in ("databases", "configuration"):
            (folder / name).mkdir(mode=0o700)
        log("Creating full purge recovery bundle: " + recovery_id)
        tail = "The purge stops before deletion; the application is reopened."
        try:
            return self._purge_bundle(preliminary, checkpoint, recovery_id, folder, tail)
        except PlanChanged:
            raise
        except Failure as exc:
            if failure_code(exc) in ("bundle-payload-mismatch", "purge-bundle-verification-failed"):
                raise PlanChanged(str(exc), checkpoint) from exc
            raise

    def _rehashed(self, folder, recovery_id, path, size, sha256, tail):
        """A payload copied into the bundle re-hashes to its source entry (no-follow, below the bundle folder)."""
        dir_fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            return self._verify_payload_tail(dir_fd, recovery_id, path, size, sha256, tail)
        finally:
            os.close(dir_fd)

    def _verify_payload_tail(self, dir_fd, bundle_id, path, size, sha256, tail):
        fd, actual = self._check_payload(dir_fd, bundle_id, path, size, sha256, tail=tail)
        os.close(fd)
        return actual

    def _purge_bundle(self, preliminary, checkpoint, recovery_id, folder, tail):
        payloads, exclusions, prerequisites = [], [], []
        active_store = checkpoint.active_store

        def copied(source, target, entry, **changes):
            copy_fresh(source, folder / target)
            self._rehashed(folder, recovery_id, target, entry["size"], entry["sha256"], tail)
            payloads.append(dict(entry, path=target, **changes))

        # PF-A2.3: content-only copies (no mode, owner or ACL xattr); publish_fresh sets the recovery targets.
        copied(checkpoint.folder / checkpoint.source_payload, "source.tar.gz", checkpoint.payload(checkpoint.source_payload))
        workspace_payload = None
        if checkpoint.workspace_payload:
            copied(checkpoint.folder / checkpoint.workspace_payload, "workspace.tar.gz",
                   checkpoint.payload(checkpoint.workspace_payload))
            workspace_payload = "workspace.tar.gz"
        copied(checkpoint.folder / active_store["dump"], "databases/active.dump", checkpoint.payload(active_store["dump"]))
        copied(checkpoint.folder / active_store["list"], "databases/active.list", checkpoint.payload(active_store["list"]))

        deployment = self.current_deployment()
        deployment_ref = None
        if deployment is not None and deployment.mismatch is None:
            (folder / "deployment").mkdir(mode=0o700)
            copied(deployment.folder / "deployment-record.json", "deployment/deployment-record.json",
                   {"size": None, "sha256": deployment.record_sha256, "type": "deployment_record", "store": None,
                    "sensitive": True, "expanded_bytes": None, "members": None, "members_sha256": None})
            copied(deployment.folder / "compose-resolved.json", "deployment/compose-resolved.json",
                   {"size": None, "sha256": deployment.record["compose"]["file_sha256"], "type": "compose_resolved",
                    "store": None, "sensitive": True, "expanded_bytes": None, "members": None,
                    "members_sha256": None})
            for item in payloads[-2:]:
                item["size"] = (folder / item["path"]).stat().st_size
            deployment_ref = {"deployment_id": deployment.deployment_id, "record_sha256": deployment.record_sha256}
        else:
            reason = ("the deployment predates PF-A3.1 or its seal failed" if deployment is None else
                      f"deployment {deployment.deployment_id}: {deployment.mismatch}")
            exclusions.append({"item": "deployment-record", "reason": reason[:500]})
            prerequisites.append("no deployment record is bundled: verify the restored source, images and "
                                 "configuration manually before relying on the restored instance")

        # The bundle preserves the configuration this operation consumed: the frozen
        # snapshot rendering (literal values), not whatever the editable file holds now.
        if self.frozen is None:
            raise Failure("No frozen application configuration for this purge; recovery bundle refused.")
        try:
            frozen_bytes = pf_config.render_app_env(self.frozen.values)
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        pf_instance._write_private_file(folder / "configuration" / ".env", frozen_bytes, 0o600)
        payloads.append(self.file_payload(folder, "configuration/.env", "config_env", sensitive=True))
        admin_config = self.config_dir / "pf-config.json"
        if admin_config.is_file():
            copy_fresh(admin_config, folder / "configuration" / "pf-config.json")
            payloads.append(self.file_payload(folder, "configuration/pf-config.json", "admin_config", sensitive=True))

        quiescence = self.observe_quiescence()
        if quiescence["mode"] != "writers_stopped":
            raise Failure("Application writers are running; a purge bundle needs stopped writers (pause first).")
        if self.plan is not None and self.plan["kind"] == "purge" and any(
                effect["postcondition"] == FUNCTIONAL_POSTCONDITION for effect in self.plan["effects"]):
            # PF-A3.3 section 3.3: the source side of the equality oracle, inside the quiescence window, before the
            # manifest is sealed. Without it the functional claim cannot be made (fail closed).
            source = self.app_invariants("source")
            if source.outcome in ("error", "incomplete"):
                exc = Failure(f"app-check-failed: the application invariant check of the source could not complete "
                              f"({source.outcome}); without it the final bundle cannot be functionally verified. The "
                              "instance purge stops before deletion; the application is reopened.")
                exc.code = "app-check-failed"
                raise exc
            if source.outcome == "mismatch":
                log("note: app-invariants-mismatch-in-source: the instance being removed by this instance purge already "
                    "has reconciliation findings (an incident, RUNBOOK §8); the bundle reproduces them")
        rows = self.database_rows()
        active = self.env()["POSTGRES_DB"]
        if active not in rows:
            raise Failure(f"Unexpected PostgreSQL inventory output: the active database {active} is not listed.")
        stores = []
        for name in sorted(rows):
            row = rows[name]
            if name == active:
                facts = self.store_facts(row, counts=True)
                store = self.store_record(facts, role="active", dump="databases/active.dump",
                                          listing="databases/active.list", group="purge")
                if store["alembic_heads"] != active_store["alembic_heads"]:
                    raise Failure("The active database changed after the before-purge checkpoint (Alembic heads).")
            else:
                stem = "databases/db-" + hashlib.sha256(name.encode()).hexdigest()[:16]
                with self.connection_window(name, row["allow_connections"]):
                    dump = self.dump_store(name, folder, stem + ".dump")
                    self.write_dump_list(dump, folder / (stem + ".list"))
                    facts = self.store_facts(row, counts=True)
                store = self.store_record(facts, role="retained", dump=stem + ".dump", listing=stem + ".list",
                                          group="purge")
                payloads += [self.file_payload(folder, stem + ".dump", "database_dump", store=store["store_id"]),
                             self.file_payload(folder, stem + ".list", "database_list", store=store["store_id"])]
            stores.append(store)
        for item in payloads:
            if item["path"] in ("databases/active.dump", "databases/active.list"):
                item["store"] = "postgresql:" + active

        globals_path = folder / "postgres-globals.sql"
        with globals_path.open("xb") as output:
            self.database_program("pg_dumpall", "-d", "postgres", "--globals-only", output=output)
        if not globals_path.stat().st_size:
            raise Failure("PostgreSQL globals archive is empty; purge recovery is incomplete.")
        payloads.append(self.file_payload(folder, "postgres-globals.sql", "postgres_globals", sensitive=True))
        roles = self.role_inventory()

        # Preserve the whole rollback/checkpoint history independently from the
        # normal backups tree so --delete-backups remains recoverable.
        counts = self.create_tree_archive(self.backups_dir, folder / "revision-checkpoints.tar.gz",
                                          self.config["project"])
        payloads.append(dict(self.file_payload(folder, "revision-checkpoints.tar.gz", "checkpoint_history"),
                             expanded_bytes=counts[0], members=counts[1], members_sha256=counts[2]))

        state_files = []
        for name in RESTORABLE_STATE_FILES:
            source = self.state / name
            if source.is_file():
                if not state_files:
                    (folder / "state").mkdir(mode=0o700)
                copy_fresh(source, folder / "state" / name)
                state_files.append(name)
                payloads.append(self.file_payload(folder, "state/" + name, "state_file", sensitive=True))

        image_refs, missing_history_images = self.available_snapshot_image_refs()
        image_refs = sorted(set(image_refs) | {value["reference"] for value in checkpoint.images.values()})
        db_image = checkpoint.image("db")
        images_path = folder / "images.tar"
        if not image_refs:
            raise Failure("No active PartFlow application images were available for recovery.")
        # PF-A3.1: the database image layers are saved by ID with the application tags (never re-tagged).
        self.docker("image", "save", "-o", images_path, *image_refs, db_image["id"])
        if not images_path.stat().st_size:
            raise Failure("Docker image recovery archive is empty.")
        with tarfile.open(images_path, "r:") as archive:
            archive.getmembers()
        payloads.append(self.file_payload(folder, "images.tar", "image_archive"))

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

        images = {service: dict(checkpoint.image(service), archived=True) for service in ("backend", "frontend", "db")}
        bind_paths = list(binding.get("bind_paths") or [])
        exclusions += [
            {"item": "bind-mounts", "reason": "never captured (no bind paths in this profile)" if not bind_paths
             else ("retained, never captured: " + ", ".join(bind_paths))[:500]},
            {"item": "external-databases", "reason": "databases outside the db service are not captured"}]
        extension_names = sorted({item["name"] for store in stores for item in store["extensions"]})
        prerequisites = [f"PostgreSQL major {checkpoint.postgres_major} server with extensions: "
                         + (", ".join(extension_names) or "none"),
                         "roles in postgres-globals.sql are evidence; recreate any role other than "
                         f"{self.env()['POSTGRES_USER']} manually"] + prerequisites
        manifest = {
            "schema_version": 1, "bundle_id": recovery_id, "bundle_kind": "purge-bundle", "created_at": utc(),
            "reason": "before-purge", "capture_class": "healthy_checkpoint",
            "source_instance": self.source_instance(), "producer": self.producer(), "quiescence": quiescence,
            "source": dict(checkpoint.manifest["source"]), "deployment": deployment_ref, "images": images,
            "postgresql": {"server_version_num": self.server_version_num(), "major": checkpoint.postgres_major,
                           "image_id": checkpoint.manifest["postgresql"]["image_id"]},
            "roles": roles, "stores": stores,
            "consistency_groups": [{"group_id": "purge", "stores": [store["store_id"] for store in stores],
                                    "claim": "writers-stopped"}],
            "compatibility": {"alembic_heads_live": checkpoint.database_heads,
                              "alembic_heads_image": checkpoint.manifest["compatibility"]["alembic_heads_image"],
                              "migration_files": checkpoint.migration_files, "mismatch": None},
            "payloads": sorted(payloads, key=lambda item: item["path"]),
            "workspace": {"differs_from_deployed": checkpoint.manifest["workspace"]["differs_from_deployed"],
                          "payload": workspace_payload, "unsupported_entries": []},
            "exclusions": exclusions, "manual_prerequisites": prerequisites, "derived_from": checkpoint.bundle_id,
            "purge": {"resources_before_purge": {"containers": candidates("container"),
                                                 "volumes": candidates("volume"),
                                                 "networks": candidates("network"), "images": candidates("image")},
                      "saved_image_refs": image_refs, "missing_historical_image_refs": missing_history_images,
                      "state_files": state_files,
                      "restore_scope": (
                          "Functional instance state: exact deployed source, writable workspace when it differs, "
                          "external runtime configuration, deployment record and resolved Compose model, active and "
                          "retained databases, current application and database images, available rollback images, "
                          "revision checkpoints. Docker container/network IDs and extra PostgreSQL roles are not "
                          "recreated bit-for-bit.")},
            "legacy": None,
        }
        manifest_sha256 = self.write_manifest(folder, manifest)
        sealed = BundleView(folder, manifest, manifest_sha256, None, "captured")
        if missing_history_images:
            log("WARNING: Some old rollback image tags were already missing before purge. Their checkpoint files are preserved, but those old image layers cannot be reconstructed automatically.")
        return sealed, binding

    def verify_purge_bundle(self, view, verify_names):
        """PF-A3.2 ``verification bundle:purge`` (step 6): every store restored from the bundle's own payloads before
        deletion is possible, then the recovery targets of the fresh bundle."""
        folder = view.folder
        stores = view.manifest["stores"]
        record, passed = self.verify_bundle(view, {store["store_id"]: folder / store["dump"] for store in stores},
                                            started_at=view.manifest["created_at"], names=verify_names)
        self._record_capture(view, record, None)
        if not passed:
            failed = next(check for check in record["checks"] if check["result"] == "failed")
            exc = Failure(f"purge-bundle-verification-failed: {view.bundle_id}: {failed['name']}: {failed['detail']}. "
                          "Verification database retained. The purge stops before deletion; the application is "
                          "reopened.")
            exc.code = "purge-bundle-verification-failed"
            raise exc
        self.publish_fresh("recovery", folder)
        log("Recovery bundle verified: " + str(folder))
        return dataclasses.replace(view, level=record["level"], latest_verification_id=record["verification_id"])

    def recoveries(self):
        """Bundle candidates of the selected instance only: ``<recovery>/<compose_project>/purge-*`` (a real
        directory, never a link followed elsewhere), newest first, each a BundleView or an InvalidBundle.

        Neither ``--project`` nor any sibling project directory is ever listed (PF-A1.4). Read-only.
        """
        base = self.recovery_root
        if not real_directory(base):
            return []
        self.ensure_config()
        items = [self._listed("purge-bundle", folder) for folder in base.iterdir()
                 if RECOVERY_RE.fullmatch(folder.name) and real_directory(folder)]
        return sorted(items, key=lambda item: item.bundle_id, reverse=True)

    def verify_recovery(self, item):
        """The strict read of one purge bundle of this instance (name kept). ``item``: a listing entry, a folder
        path, or {"_folder"|"id"}. PF-A1.4: restore authority is the instance's own recovery directory, exactly."""
        if isinstance(item, (BundleView, InvalidBundle)):
            folder = item.folder
        elif isinstance(item, dict):
            folder = Path(item.get("_folder") or self.recovery_root / item["id"])
        else:
            folder = Path(item)
        return self.read_bundle("purge-bundle", folder)

    def display_recoveries(self, items, page=1):
        selected, pages, start = page_items(items, page)
        log(f"Purge recovery bundles | newest first | page {page}/{pages} | {len(items)} total")
        for number, item in enumerate(selected, start + 1):
            if isinstance(item, InvalidBundle):
                log(f"{number:>3}. {item.bundle_id}  [invalid: {item.code}]")
                continue
            log(f"{number:>3}. {item.bundle_id}  [{LEVEL_NAMES[item.level]}]  project={item.compose_project}  "
                f"db={item.database}  source={item.source_display}  derived_from={item.derived_from}"
                f"{self.bundle_suffix(item)}")
        return pages

    def choose_recovery(self, requested=None):
        items = self.recoveries()
        if not items:
            raise Failure("No purge recovery bundles were found.")
        if requested:
            matches = [item for item in items if item.bundle_id == requested]
            if len(matches) != 1:
                raise Failure("Specify one exact purge recovery ID.")
            return self.verify_recovery(matches[0].folder)
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
                return self.verify_recovery(items[int(answer) - 1].folder)

    def finish_purge_cleanup(self, recovery_id, plan, *, delete_backups, reset_admin_config):
        """The deletion and the four cleanup bodies in their order (a composed helper; a purge runs each as its own
        journaled effect: resource-delete, then purge-cleanup:backups, env, state, admin-config)."""
        self.execute_deletion_plan(plan)
        for name in ("backups", "env", "state", "admin-config"):
            self.purge_cleanup(name, delete_backups=delete_backups, reset_admin_config=reset_admin_config)
        self.purge_complete_log(recovery_id, reset_admin_config=reset_admin_config)

    def purge_cleanup(self, name, *, delete_backups, reset_admin_config):
        """One finalizing cleanup of a purge (section 3.4); idempotent (an absent target is already removed).
        State/.env are removed last so an interrupted Docker cleanup remains diagnosable; bind paths are never
        deleted; operations/, artifacts/, home/, the lock and the registry are never touched (section 3.12)."""
        if name == "backups":
            if delete_backups and self.backups_dir.exists():
                shutil.rmtree(self.backups_dir)
            return "removed" if delete_backups else "kept"
        if name == "env":
            env_path = self.config_dir / ".env"
            if env_path.exists():
                env_path.unlink()
            legacy_marker = self.root / "DEPLOYED_SOURCE.txt"
            if legacy_marker.exists():
                legacy_marker.unlink()
            return "absent"
        if name == "state":
            if self.state.exists():
                shutil.rmtree(self.state)
            return "absent"
        if reset_admin_config:
            config = self.config_dir / "pf-config.json"
            if config.exists():
                try:
                    config.unlink()
                except OSError as exc:
                    log("WARNING: purge completed but local admin config could not be removed: " + str(exc))
            return "absent"
        return "kept"

    def purge_complete_log(self, recovery_id, *, reset_admin_config):
        log("Purge complete for " + self.config["project"] + ".")
        log("Verified recovery bundle retained at: " + str(self.recovery_root / recovery_id))
        log("The writable repository and root-owned control plane remain. Runtime .env was removed from config/.")
        if reset_admin_config:
            log(f"Next: {self.pf_command()} config admin, then {self.pf_command()} deploy --latest.")
        else:
            log(f"Next: {self.pf_command()} deploy --latest.")

    def act_purge_cleanup(self, step, name):
        deletion = self.journal["deletion"] or {}
        step.evidence = self.purge_cleanup(name, delete_backups=bool(deletion.get("delete_backups")),
                                           reset_admin_config=bool(deletion.get("reset_admin_config")))
        if name == "admin-config":
            self.purge_complete_log(self.plan_bundle_id(), reset_admin_config=bool(deletion.get("reset_admin_config")))

    def purge(self, *, delete_backups=None, reset_admin_config=False):
        """``pf purge`` (section 3.4): the plan at ``PURGE <project>``; stop, checkpoint, purge bundle and its
        verification as effects; then the confirmations and the binding deletion plan frozen in one journal
        generation (``deletion``); then the deletion and the cleanups. A failure or a declined confirmation before
        the deletion generation reopens the application in-process and closes the operation cancelled (A3.1
        behaviour). A repeated ``pf purge`` in deleting/finalizing aliases ``pf resume`` (the gate re-enters)."""
        self.staging()
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
        self.capture_preflight("purge")
        self.ensure_local_contract()
        rows = self.database_rows()
        closed = sorted(name for name, row in rows.items() if not row["allow_connections"])
        # PF-A3.3 section 3.4 preview: no isolated topology of this instance may hold resources (they make the
        # instance's image tags foreign-in-use); the isolated model of the current deployment must be renderable here;
        # the generated topology name must be free; the capacity of every affected filesystem.
        self.require_no_isolated_topology()
        running = {service: self.inspect(service)["Image"] for service in pf_docker.SERVICES}
        self.isolation_preflight(None, running, values=dict(self.env()))
        project, topology_uuid = self.new_topology("pfverify-")
        self.capacity_preflight("purge")
        text = ("This is a destructive staging teardown. The selected project's exact containers, volumes, networks and "
                "covered PartFlow image tags listed above, runtime state, and config/.env are candidates for deletion. "
                "The writable repo, bind-mounted paths and installed control plane are retained. A verified recovery "
                "bundle is created before any destructive Docker deletion.\n"
                f"Final bundle verification: restored and functionally checked in an isolated stack {project} (internal "
                "network, no published port, generated database password) while the application stays stopped; "
                "downtime lasts until deletion or reopen.\n"
                "Application invariant check: decided inside the application image during the instance purge "
                "(unavailable for images before P16-S1).")
        phrase = "PURGE " + self.config["project"]
        confirm(phrase, text)
        view = self.current_deployment()
        commit = (view.record["source"]["commit"] if view is not None and view.mismatch is None
                  else self.deployed_commit()) or "0" * 40
        checkpoint_id = f"{utc()}-{commit[:12]}-{uuid.uuid4().hex[:6]}"
        recovery_id = f"purge-{utc()}-{commit[:12]}-{uuid.uuid4().hex[:6]}"
        stores = ",".join(sorted(rows))
        effects = [
            {"phase": "preserving", "type": "service-change", "target": "services:stop:frontend,backend",
             "postcondition": "stopped", "preconditions": ["confirmed"]},
            {"phase": "preserving", "type": "capture", "target": "checkpoint:before-purge",
             "postcondition": "sealed and data_restore_verified",
             "preconditions": ["bundle:" + checkpoint_id, "verify:pf_verify_" + uuid.uuid4().hex[:20],
                               "writers stopped"]},
            {"phase": "capturing", "type": "capture", "target": "purge-bundle",
             "postcondition": "sealed and data_restore_verified",
             "preconditions": ["bundle:" + recovery_id, "checkpoint:" + checkpoint_id, "stores:" + stores]
             + (["allow_connections=false:" + ",".join(closed)] if closed else []),
             "preservation_refs": [checkpoint_id]},
            {"phase": "verifying", "type": "verification", "target": "bundle:purge",
             "postcondition": FUNCTIONAL_POSTCONDITION,
             "preconditions": ["topology:" + project, "topology-uuid:" + topology_uuid, "bundle:" + recovery_id]},
            {"phase": "deleting", "type": "resource-delete", "target": "deletion-plan",
             "postcondition": "every planned item removed or already absent",
             "preconditions": ["ERASE confirmed", "deletion approval journaled"],
             "preservation_refs": [checkpoint_id, recovery_id]}]
        for name in ("backups", "env", "state", "admin-config"):
            effects.append({"phase": "finalizing", "type": "file-write", "target": "purge-cleanup:" + name,
                            "postcondition": "absent", "preconditions": ["deletion complete"]})
        # OD-A33-08 (applied): the registry tombstone of LIFECYCLE section 8 step 7, written last through a registry
        # transaction; the purged record holds no project claim.
        effects.append({"phase": "finalizing", "type": "file-write", "target": "registry:state=purged",
                        "postcondition": "record state purged",
                        "preconditions": ["record-state:" + self.context.state, "deletion complete"]})
        ctx = {"preliminary": preliminary, "delete_backups": delete_backups,
               "reset_admin_config": reset_admin_config}
        self.open_operation(
            "purge", effects=effects, workspace=self.workspace_plan("untouched"), images=self.running_images(),
            confirmation=self.confirmation_ref(phrase, text),
            source=self.running_source(), inventory_sha256=pf_instance.sha256_bytes(
                pf_instance.normalize_json(inventory.record())),
            coverage=[{"store_id": "postgresql:" + name, "strategy_id": "postgresql-logical", "included": True,
                       "reason": "every database of the db service"} for name in sorted(rows)])
        try:
            return self.run_plan(ctx)
        except Exception as exc:
            if self.journal is None or self.journal["deletion"] is not None \
                    or self.journal["phase"] in pf_config.TERMINAL_PHASES:
                raise
            # No destructive deletion has happened yet: reopen the exact current application in-process.
            reopen = not (isinstance(exc, PlanChanged) and exc.checkpoint is None)
            if reopen and self.effect_state(self.effects_of("stop")[0]["effect_id"]) != "not_started":
                try:
                    self.restore_allow_connections()
                    self.reopen_unchanged()
                    self.close_operation("cancelled", last_error=self._error(exc))
                    log("Purge cancelled/failed before deletion; application services were restored.")
                except Exception as resume_exc:
                    log("WARNING: Could not automatically resume after pre-delete purge failure: " + str(resume_exc))
            elif reopen:
                self.close_operation("cancelled", last_error=self._error(exc))
            raise

    def purge_approve_deletion(self, ctx):
        """The second stage of the purge approval (section 3.4, OD-A32-21): the recovery summary, DELETE <db>, the
        backup choice, RESET ADMIN CONFIG, ERASE <project> <challenge>, the deletion gate, the binding deletion plan
        written, then one journal generation setting ``deletion``. First run only (a resume after a crash in these
        steps reopens instead)."""
        if "recovery" not in ctx or "binding" not in ctx:
            raise Failure("Internal error: a purge deletion is approved only by the process that verified its bundle.")
        recovery, binding = ctx["recovery"], ctx["binding"]
        checkpoint = ctx.get("checkpoint")
        delete_backups, reset_admin_config = ctx.get("delete_backups"), ctx.get("reset_admin_config")
        log("Recovery summary:")
        log("  Bundle: " + recovery.bundle_id)
        log("  Path: " + str(self.recovery_root / recovery.bundle_id))
        log("  Active database: " + recovery.database)
        log("  Preserved databases: " + ", ".join(store["database"] for store in recovery.stores))
        log("  Saved Docker image tags: " + str(len(recovery.purge["saved_image_refs"])))
        log("  Revision checkpoints archived: yes")
        log("  Verification: " + LEVEL_NAMES[recovery.level] + (
            " (the exact bundle restored and functionally checked in an isolated topology)"
            if recovery.level == "functional_recovery_verified" else
            " (every store restored from the bundle's own payloads)"))
        if checkpoint is not None:
            log("  Before-purge checkpoint: " + checkpoint.bundle_id)
        self.log_plan(binding, title="Binding deletion plan (frozen before the final confirmation):")
        # Second gate proves the operator understands which database becomes inaccessible.
        confirm(
            "DELETE " + recovery.database,
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
            + recovery.bundle_id,
        )
        # PF-A3.3 deletion gate (section 3.4): the exact bundle, its exact functional record of this operation,
        # stopped writers, an unchanged source, full coverage and an unchanged binding inventory, immediately before
        # the deletion approval is journaled.
        self.purge_deletion_gate(binding, live=True)
        reference = self.write_deletion_plan(binding)
        self.journal_update(deletion={"plan_sha256": reference["sha256"], "delete_backups": bool(delete_backups),
                                      "reset_admin_config": bool(reset_admin_config), "confirmed_at": utc()})

    def gate_refusal(self, code, text):
        exc = Failure(f"{code}: {text}")
        exc.code = code
        return exc

    def purge_deletion_gate(self, binding, *, live=True):
        """Section 3.4 deletion gate. ``live``: all six steps (first run, right after ERASE); else steps 1-2 only (a
        resume in deleting/finalizing: the source may already be partly deleted). Returns the gated BundleView."""
        tail = "The instance purge stops before deletion; the application is reopened."
        capture = self.effects_of(type="capture", target="purge-bundle")[0]
        verification = self.effects_of(type="verification", target="bundle:purge")[0]
        artifact = next((item for item in self.journal["retained_artifacts"] if item["kind"] == "purge-bundle"), None)
        bundle_id = artifact["name"] if artifact else self.attempt_bundle(capture, self.journal_effect(
            capture["effect_id"])["evidence"])
        try:
            view = self.verify_recovery(self.recovery_root / bundle_id)
        except Failure as exc:
            raise Failure(f"plan-input-changed: recovery bundle {bundle_id} of operation {self.operation_id} no longer "
                          f"reads or verifies ({failure_code(exc)}); deletion stays blocked and the operation stays "
                          "open. Restore the bundle folder byte-identically from an off-NAS copy, then resume "
                          "(SYNOLOGY_ADMIN §16). Nothing was changed.") from exc
        if artifact is None or artifact["sha256"] != view.manifest_sha256:
            raise Failure(f"plan-input-changed: recovery bundle {bundle_id} of operation {self.operation_id} is not the "
                          "sealed final bundle of this operation (manifest hash). Deletion is blocked. " + tail)
        # Step 2: the exact record of this operation, this bundle and this manifest, named by the effect's evidence.
        evidence = self.journal_effect(verification["effect_id"])["evidence"] or ""
        named = re.findall(r"record:(ver-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8})", evidence)
        a32 = verification["postcondition"] == A32_PURGE_POSTCONDITION
        level = "data_restore_verified" if a32 else "functional_recovery_verified"
        records = [record for record in self.verification_records(view.bundle_id, view.manifest_sha256)
                   if record["level"] == level and record["result"] == "passed"
                   and record["operation_id"] == self.operation_id
                   and (a32 or (named and record["verification_id"] == named[-1]))]
        if not records:
            raise self.gate_refusal("purge-bundle-unverified", (
                f"{view.bundle_id}: no passed {level} record of operation {self.operation_id} for manifest "
                f"{view.manifest_sha256[:12]}; deletion is blocked. " + tail))
        if not live:
            return view
        # Step 3: writers stopped.
        quiescence = self.observe_quiescence()
        if quiescence["mode"] != "writers_stopped":
            service = "backend" if quiescence["backend"] == "running" else "frontend"
            raise self.gate_refusal("purge-writers-running", (
                f"{service} is running; the final bundle is valid only while writers stay stopped. Deletion is "
                "blocked. " + tail))
        # Step 4: the source is unchanged (database set, heads, row counts).
        rows = self.database_rows()
        stores = {store["database"]: store for store in view.stores}
        if set(rows) != set(stores):
            raise self.gate_refusal("purge-source-changed", (
                f"database set: the databases differ from the final bundle ({','.join(sorted(stores))} vs "
                f"{','.join(sorted(rows))}). Deletion is blocked. " + tail))
        for name, store in sorted(stores.items()):
            with self.connection_window(name, rows[name]["allow_connections"]):
                heads = sorted(set(self.db_heads(name)))
                counts = self.row_counts(name)
            if heads != store["alembic_heads"]:
                raise self.gate_refusal("purge-source-changed", (
                    f"{store['store_id']}: Alembic heads differ from the final bundle ({','.join(store['alembic_heads'])}"
                    f" vs {','.join(heads)}). Deletion is blocked. " + tail))
            if store["row_counts"] is not None and counts != store["row_counts"]:
                raise self.gate_refusal("purge-source-changed", (
                    f"{store['store_id']}: row counts differ from the final bundle ({store['row_counts']['total_rows']} "
                    f"vs {counts['total_rows']}). Deletion is blocked. " + tail))
        # Step 5: coverage.
        problems = pf_config.purge_coverage(view.manifest, binding, rows)
        if problems:
            item, reason = problems[0]
            raise self.gate_refusal("coverage-incomplete", f"{item}: {reason}. Deletion is blocked. " + tail)
        # Step 6: the binding inventory is unchanged (the verification topology is gone at this point).
        fresh = self.plan_for("purge", self.docker_inventory(), command="purge", recovery_id=binding["recovery_id"],
                              covered_image_refs=set(view.purge["saved_image_refs"]))
        try:
            pf_docker.compare_plans(binding, fresh, created_image_refs=set(self.created_image_refs))
        except pf_docker.DockerScopeError as exc:
            raise PlanChanged("plan-changed: The deletion candidates changed after the final bundle was verified ("
                              + "; ".join(f"{finding.path} {finding.message}" for finding in exc.findings[:10])
                              + "); the instance purge stops before deletion and the application is reopened.",
                              True) from exc
        return view

    def running_images(self):
        """$defs.image of the running backend/frontend images (plan images of kinds that do not replace them); an
        image that cannot be identified is omitted (its identity is observed by the effects)."""
        found = {}
        for service in pf_docker.BUILT_SERVICES:
            try:
                running = self.inspect(service)["Image"]
                identity = self.image_identity(running, reference=self.inspect_reference(service))
            except DaemonFailure:
                raise
            except (Failure, ValueError, KeyError, IndexError):
                continue
            if identity["platform"] is not None:
                found[service] = identity
        return found

    def running_source(self):
        """The plan source of a kind that keeps the deployed source: the current valid deployment record, else the
        proven commit, else unknown."""
        view = self.current_deployment()
        if view is not None and view.mismatch is None:
            source = view.record["source"]
            return {"provenance": source["provenance"], "commit": source["commit"],
                    "entries_sha256": source["manifest"]["entries_sha256"], "deployment_id": None}
        commit = self.deployed_commit()
        return {"provenance": "git_commit" if commit else "unknown", "commit": commit, "entries_sha256": None,
                "deployment_id": None}

    def runtime_environment_bytes(self, recovery, extracted_source=None):
        """The verified runtime .env of a strictly read bundle: its ``config_env`` payload or, for a format 1
        bundle, the ``.env`` taken out of its verified, extracted source payload (``extracted_source``: that file).
        A ``configuration/.env`` the manifest does not list is never opened (audit AF-1)."""
        payloads = recovery.payloads_of("config_env")
        if len(payloads) > 1:
            raise Failure("Recovery bundle lists more than one runtime .env; exact restore is refused.")
        if payloads:
            return self.read_small_payload(recovery, payloads[0]["path"], "config_env")
        if extracted_source is not None:
            legacy = Path(extracted_source)
            if legacy.is_file() and not legacy.is_symlink() and legacy.stat().st_size <= SMALL_PAYLOAD_LIMIT:
                return legacy.read_bytes()
        raise Failure("Recovery bundle does not contain a runtime .env; exact restore is refused.")

    def restore_runtime_environment(self, data):
        """Install ``data`` (the verified runtime .env bytes of runtime_environment_bytes) as the configuration
        directory's .env."""
        existed = real_directory(self.config_dir)
        self.config_dir.mkdir(mode=0o2770, parents=True, exist_ok=True)
        if not existed:
            self.apply_single("configuration", self.config_dir, "dir")
        # PF-A2.3: a new file whose temporary gets the configuration file target before the rename; no other
        # configuration file is touched.
        temporary = self.config_dir / (".env.restore-" + uuid.uuid4().hex[:8])
        pf_instance._write_private_file(temporary, data, 0o600)
        self.apply_single("configuration", temporary, "file")
        os.replace(temporary, self.config_dir / ".env")

    def history_tree(self, view, temporary):
        """The bundle's checkpoint history extracted through the safe importer (HISTORY_LIMITS) below ``temporary``;
        (tree of <project> or None, its entries digest or None)."""
        if view.payload("revision-checkpoints.tar.gz") is None:
            return None, None
        if not real_directory(temporary):
            os.mkdir(str(temporary), 0o700)
        self.extract_payload(view, "revision-checkpoints.tar.gz", Path(temporary) / "history",
                             limits=pf_source.HISTORY_LIMITS)
        source = Path(temporary) / "history" / self.config["project"]
        if not real_directory(source):
            return None, None
        return source, pf_source.entries_digest(pf_source.build_manifest(source, source={"kind": "unknown"},
                                                                          excludes=()))

    def history_digest(self, path):
        try:
            return pf_source.entries_digest(pf_source.build_manifest(path, source={"kind": "unknown"}, excludes=()))
        except (pf_source.SourceError, OSError):
            return None

    def ensure_revisions_root(self):
        for directory in (self.backups_root, self.revisions_root):
            if not directory.is_dir():
                directory.mkdir(mode=0o750)
            self.apply_single("backups", directory, "dir")

    def restore_revision_checkpoints(self, view):
        """Restore the archived checkpoint history of a bundle (PF-A3.2 ``file-write checkpoint-history``, section 3.5;
        OD-A32-24): an existing tree with the bundle's digest is left as it is; any other existing tree is renamed
        (no-clobber) to ``<project>.pre-restore-<op8>`` and recorded; then a content-only copy is published under a
        private temporary name and renamed into place. Nothing is ever removed recursively. Returns (evidence,
        retained artifacts)."""
        project = self.config["project"]
        temporary = Path(tempfile.mkdtemp(prefix="restore-checkpoints-", dir=str(self.state)))
        retained = []
        try:
            source, digest = self.history_tree(view, temporary)
            if source is None:
                return "absent in the bundle", retained
            self.ensure_revisions_root()
            fd = os.open(str(self.revisions_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                staged_name = f".{project}.restore-{self.operation_id[-8:]}"
                if pf_instance.identity_at(fd, staged_name) is not None:
                    pf_instance.remove_private_tree_at(fd, staged_name)
                existing = pf_instance.identity_at(fd, project)
                displaced = f"{project}.pre-restore-{self.operation_id[-8:]}"
                if existing is None and pf_instance.identity_at(fd, displaced) is not None:
                    # A redo after a crash between the displacement and the publication: still recorded.
                    retained.append({"kind": "checkpoint-history", "name": displaced, "sha256": None})
                if existing is not None:
                    if self.history_digest(self.revisions_root / project) == digest:
                        return f"entries {digest} (already present)", retained
                    pf_instance.rename_noreplace_at(fd, project, fd, displaced)
                    retained.append({"kind": "checkpoint-history", "name": displaced, "sha256": None})
                    log(f"note: checkpoint-history-displaced: the existing {project} history was kept as {displaced}.")
                copy_fresh(source, self.revisions_root / staged_name)
                self.publish_fresh("backups", self.revisions_root / staged_name)
                pf_instance.rename_noreplace_at(fd, staged_name, fd, project)
            finally:
                os.close(fd)
            return f"entries {digest}", retained
        finally:
            parent = os.open(str(temporary.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.remove_private_tree_at(parent, temporary.name)
            finally:
                os.close(parent)

    def act_checkpoint_history(self, step, effect):
        evidence, retained = self.restore_revision_checkpoints(self.op_recovery())
        step.evidence = ((step.evidence + " ") if step.evidence else "") + evidence
        step.retained += retained

    def observe_history(self, effect):
        """Section 3.5 ``file-write checkpoint-history`` observation."""
        project = self.config["project"]
        expected = effect["postcondition"].split(" ", 1)[1]
        recorded = re.search(r"existing:([0-9]+:[0-9]+|none)", self.journal_effect(effect["effect_id"])["evidence"]
                             or "")
        current = None
        if real_directory(self.revisions_root):
            fd = os.open(str(self.revisions_root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                current = pf_instance.identity_at(fd, project)
            finally:
                os.close(fd)
        if current is not None and self.history_digest(self.revisions_root / project) == expected:
            return "complete", f"entries {expected}", None
        if current is None or (recorded and recorded.group(1) == self.identity_text(current)):
            return "redo", "history not yet restored", None
        message = (f"checkpoint-history-unknown: {self.revisions_root}/{project} of operation {self.operation_id} is "
                   "neither the tree that existed before the restore nor the restored history; nothing was moved or "
                   "deleted. Move the foreign tree aside as root (SYNOLOGY_ADMIN §16), then run 'pf --instance "
                   f"{self.context.slug} resume --operation {self.operation_id}'. Nothing was changed.")
        return "refuse", message, None

    def op_recovery(self):
        """The restore's input bundle, strictly re-read (section 3.8 Inputs): another manifest hash or an unreadable
        bundle is ``plan-input-changed``. Cached for this process."""
        cached = getattr(self, "_op_recovery", None)
        if cached is not None and cached[0] == self.operation_id:
            return cached[1]
        bundle = self.plan["input_bundle"]
        try:
            view = self.verify_recovery(self.recovery_root / bundle["bundle_id"])
        except Failure as exc:
            raise Failure(f"plan-input-changed: recovery bundle {bundle['bundle_id']} of operation {self.operation_id} "
                          f"no longer reads or verifies ({failure_code(exc)}). Nothing was changed.") from exc
        if view.manifest_sha256 != bundle["manifest_sha256"]:
            raise Failure(f"plan-input-changed: recovery bundle {bundle['bundle_id']} of operation {self.operation_id} "
                          "no longer matches its plan (manifest hash). Nothing was changed.")
        self._op_recovery = (self.operation_id, view)
        return view

    def op_selected(self):
        """The rollback's selected checkpoint, strictly re-read; another manifest hash is ``plan-input-changed``."""
        cached = getattr(self, "_op_selected", None)
        if cached is not None and cached[0] == self.operation_id:
            return cached[1]
        bundle = self.plan["input_bundle"]
        try:
            view = self.verify_snapshot(bundle["bundle_id"])
        except Failure as exc:
            raise Failure(f"plan-input-changed: checkpoint {bundle['bundle_id']} of operation {self.operation_id} no "
                          f"longer reads or verifies ({failure_code(exc)}). Nothing was changed.") from exc
        if view.manifest_sha256 != bundle["manifest_sha256"]:
            raise Failure(f"plan-input-changed: checkpoint {bundle['bundle_id']} of operation {self.operation_id} no "
                          "longer matches its plan (manifest hash). Nothing was changed.")
        self._op_selected = (self.operation_id, view)
        return view

    def restore_workspace_tree(self):
        """The restore bundle's workspace archive tree (without .env/DEPLOYED_SOURCE.txt), extracted again from the
        strictly re-read bundle into a private temporary directory (section 3.6 source rule); cached."""
        cached = getattr(self, "_restore_tree", None)
        if cached is not None and cached[0] == self.operation_id and Path(cached[1]).is_dir():
            return Path(cached[1])
        recovery = self.op_recovery()
        self.ensure_state_dir()
        if self._deployed_tree is None:
            self._deployed_tree = (Path(tempfile.mkdtemp(prefix="deployed-tree-", dir=str(self.state))), None)
        workspace = self._deployed_tree[0] / "workspace"
        self.extract_payload(recovery, recovery.workspace_payload, workspace)
        for name in (".env", "DEPLOYED_SOURCE.txt"):
            path = workspace / name
            if path.is_file() and not path.is_symlink():
                path.unlink()
        self.refuse_reserved_candidate_paths(workspace)
        self._restore_tree = (self.operation_id, workspace)
        return workspace

    def restore_env_bytes(self):
        """The bundle's runtime .env bytes for a resumed ``config:.env`` effect (format 1: from its source archive)."""
        recovery = self.op_recovery()
        if recovery.payloads_of("config_env"):
            return self.runtime_environment_bytes(recovery)
        temporary = Path(tempfile.mkdtemp(prefix="restore-env-", dir=str(self.state)))
        try:
            self.extract_payload(recovery, recovery.source_payload, temporary / "source")
            return self.runtime_environment_bytes(recovery, extracted_source=temporary / "source" / ".env")
        finally:
            parent = os.open(str(temporary.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.remove_private_tree_at(parent, temporary.name)
            finally:
                os.close(parent)

    def act_install_env(self, step, effect, ctx):
        """``file-write config:.env`` (section 3.8 Restore .env): the verified bundle bytes; an existing .env with other
        bytes is renamed (no-clobber) to ``.env.proposal-<op8>`` first and never rewritten or deleted."""
        data = ctx.get("runtime_env") or self.restore_env_bytes()
        path = self.config_dir / ".env"
        try:
            existing = pf_instance.read_bytes_nofollow(path)
        except FileNotFoundError:
            existing = None
        if existing is not None and existing != data:
            proposal = ".env.proposal-" + self.operation_id[-8:]
            fd = os.open(str(self.config_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                pf_instance.rename_noreplace_at(fd, ".env", fd, proposal)
            finally:
                os.close(fd)
            log(f"note: env-proposal-preserved: config/.env differed from the bundle's; it was kept as {proposal}.")
        if existing != data:
            self.restore_runtime_environment(data)
        step.evidence = "bytes sha256 " + pf_instance.sha256_bytes(data)

    def freeze_restore_snapshot(self, data):
        """Section 3.4 restore-instance: the bundle-derived snapshot frozen before the plan (AM-10); an existing
        snapshot with the same rendered bytes is that snapshot."""
        try:
            values = self.check_app_values(pf_config.parse_app_env(data, label="bundle .env"))
            rendered = pf_config.render_app_env(values)
        except pf_config.ConfigError as exc:
            raise Failure(str(exc)) from exc
        if self.frozen is not None and pf_instance.sha256_bytes(rendered) == self.frozen.env_sha256:
            return self.frozen
        return self.freeze_app_config(explicit=True, source_bytes=data)

    def restore_instance(self, recovery, *, side_by_side=False, keep_workspace=False):
        """``recovery``: the BundleView of choose_recovery (strictly read before any confirmation)."""
        if not isinstance(recovery, BundleView):
            recovery = self.verify_recovery(recovery)
        if recovery.postgres_major != 16:
            raise Failure("This recovery bundle is not PostgreSQL 16; automatic restore is refused.")
        # PF-A3.3 section 3.5: only the selected instance's own bundles (same UUID; legacy: same project). Paths,
        # project, daemon and every mutation target come from the selected registration; the bundle's recorded
        # paths are provenance only.
        self.require_restore_identity(recovery)
        if side_by_side:
            return self.restore_side_by_side(recovery)
        root = recovery.workspace_root
        if root is not None and Path(root) != self.root:
            log(f"note: bundle-workspace-differs: the bundle recorded workspace {root}; the selected instance's "
                f"registered workspace {self.root} governs.")
        if (self.state / "deployed.json").exists():
            raise Failure("A managed deployment record already exists. Exact restore refuses to overwrite it.")
        # PF-A1.3: exact inventory; any owned or blocking topology resource refuses before any confirmation.
        # Purge the current instance first, or use --side-by-side to recover data without replacing it.
        self.require_empty_target("restore-instance")
        purged = self.context.state == "purged"
        if purged:
            # OD-A33-08: the purged record holds no claim; restoring it claims (daemon, project) again.
            self.require_project_claim()
        db_tag, db_lines = self.restore_db_image(recovery)
        self.capacity_preflight("restore-instance", view=recovery)

        log("Restore target summary:")
        log("  Project: " + recovery.compose_project)
        log("  Source: " + recovery.source_display)
        log("  Active database: " + recovery.database)
        log("  Preserved databases: " + ", ".join(store["database"] for store in recovery.stores))
        log("  Recovery bundle: " + recovery.bundle_id)
        with tempfile.TemporaryDirectory(prefix="restore-source-", dir=self.state) as temp:
            # PF-A3.1: the deployed source is always the source payload (never the workspace archive). Anything the
            # manifest could not verify, and any provenance-proof failure, stops here: before any confirmation, plan
            # or configuration change.
            candidate = Path(temp) / "source"
            tree = self.extract_payload(recovery, recovery.source_payload, candidate)
            self.require_source_identity(recovery, tree, recovery.source_payload)
            legacy_env = None
            if (candidate / ".env").is_file() and not (candidate / ".env").is_symlink():
                # Format 1 stored the runtime .env inside source.tar.gz: it leaves the deployed tree here and is
                # read below as the bundle's runtime .env.
                legacy_env = Path(temp) / "legacy.env"
                os.replace(str(candidate / ".env"), str(legacy_env))
            marker = candidate / "DEPLOYED_SOURCE.txt"
            if marker.is_file() and not marker.is_symlink():
                marker.unlink()
            self.refuse_reserved_candidate_paths(candidate)
            hypothesis = recovery.source_hypothesis
            verified = self.prove_tree_commit(candidate, hypothesis)
            revision = hypothesis if verified else None
            manifest = self.candidate_manifest(candidate, revision, verified=verified)
            workspace_tree, workspace_digest = candidate, None
            if recovery.workspace_payload:
                workspace_tree = Path(temp) / "workspace"
                self.extract_payload(recovery, recovery.workspace_payload, workspace_tree)
                for name in (".env", "DEPLOYED_SOURCE.txt"):
                    path = workspace_tree / name
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                self.refuse_reserved_candidate_paths(workspace_tree)
                workspace_digest = pf_source.entries_digest(pf_source.build_manifest(
                    workspace_tree, source={"kind": "unknown"}, excludes=SOURCE_EXCLUDES))
            history_digest = None
            if recovery.payload("revision-checkpoints.tar.gz") is not None:
                # A hostile checkpoint history is refused here, before any confirmation or plan (PB-4); its tree
                # digest is the checkpoint-history effect's postcondition.
                self.inspect_payload(recovery, "revision-checkpoints.tar.gz", limits=pf_source.HISTORY_LIMITS)
                _, history_digest = self.history_tree(recovery, Path(temp) / "history-check")
            # Audit AF-1: the runtime .env and the state files are their verified payload bytes, read before any
            # confirmation or plan; an unlisted file in the bundle folder is never opened.
            runtime_env = self.runtime_environment_bytes(recovery, extracted_source=legacy_env)
            state_files = [(name, self.read_small_payload(recovery, "state/" + name, "state_file"))
                           for name in recovery.purge["state_files"] if name != "deployed.json"]
            self.deployment_preflight(manifest)
            workspace_manifest = manifest if workspace_digest is None else pf_source.build_manifest(
                workspace_tree, source={"kind": "unknown"}, excludes=SOURCE_EXCLUDES)
            workspace, lines = self.workspace_decision(workspace_manifest, keep_workspace)
            confirm(
                "RESTORE INSTANCE " + recovery.compose_project,
                "This recreates the purged functional instance from its recovery bundle. Docker container/network IDs are newly created. PostgreSQL globals are preserved as evidence but extra roles are not automatically executed.",
            )
            phrase = "RESTORE " + recovery.database + " " + recovery.bundle_id
            summary = ("Final restore confirmation. Repository workspace, runtime .env, application images, active "
                       "database, retained databases, and revision checkpoints will be restored into an empty project. "
                       "The current pf-config.json remains authoritative.\n" + "\n".join(lines + db_lines)
                       + ("\nRegistry: the purged record of this instance is registered again (it claims project "
                          f"{self.context.compose_project} on its daemon)." if purged else ""))
            confirm(phrase, summary)
            self.sweep_staging()
            # Section 3.4 (AM-10): the bundle-derived snapshot is frozen before the plan, so every resume binds it.
            self.freeze_restore_snapshot(runtime_env)
            deployment_id = f"dep-{utc()}-{uuid.uuid4().hex[:8]}"
            active = recovery.active_store
            effects = [
                {"phase": "preparing-target", "type": "source-stage", "target": "deployment:" + deployment_id,
                 "postcondition": f"staged {deployment_id}", "preconditions": ["confirmed"]},
                {"phase": "preparing-target", "type": "file-write", "target": "config:.env",
                 "postcondition": "bytes sha256 " + pf_instance.sha256_bytes(runtime_env),
                 "preconditions": ["an edited .env is kept as .env.proposal-" + self.operation_id[-8:]]}]
            if purged:
                # OD-A33-08: the claim is taken again (re-checked under the registry lock) before any Docker effect.
                effects.append({"phase": "preparing-target", "type": "file-write", "target": "registry:state=registered",
                                "postcondition": "record state registered",
                                "preconditions": ["record-state:purged", "no other record claims the project"]})
            effects += [
                {"phase": "preparing-target", "type": "image-load", "target": "images:" + recovery.bundle_id,
                 "postcondition": "backend/frontend IDs present", "preconditions": ["bundle re-read"]}]
            if db_tag is not None:
                effects.append(db_tag)
            effects += [
                {"phase": "preparing-target", "type": "service-change", "target": "service:db:start",
                 "postcondition": "db healthy", "preconditions": ["the target was empty at plan time"]},
                {"phase": "restoring-data", "type": "database-drop", "target": "database:" + recovery.database,
                 "postcondition": "absent", "preconditions": ["the init database of the new volume"]},
                {"phase": "restoring-data", "type": "database-restore", "target": "database:" + recovery.database,
                 "postcondition": "restored and checked", "preconditions": ["from:" + recovery.bundle_id,
                                                                            "dump:" + active["dump"]]}]
            for store in recovery.stores:
                if store["role"] == "active":
                    continue
                effects.append({"phase": "restoring-data", "type": "database-restore",
                                "target": "database:" + store["database"], "postcondition": "restored and checked",
                                "preconditions": ["from:" + recovery.bundle_id, "dump:" + store["dump"]]})
                if not store["allow_connections"]:
                    effects.append({"phase": "restoring-data", "type": "database-alter",
                                    "target": f"database:{store['database']}:allow_connections=false",
                                    "postcondition": "flag false", "preconditions": ["restored"]})
            if history_digest is not None:
                effects.append({"phase": "restoring-data", "type": "file-write", "target": "checkpoint-history",
                                "postcondition": "entries " + history_digest,
                                "preconditions": ["a differing existing tree is displaced, never removed"]})
            for name, data in state_files:
                effects.append({"phase": "restoring-data", "type": "file-write", "target": "state-file:" + name,
                                "postcondition": "bytes sha256 " + pf_instance.sha256_bytes(data),
                                "preconditions": ["from:" + recovery.bundle_id]})
            images = {service: image for service, image in recovery.images.items() if image is not None}
            effects += self.activation_specs(images, sorted(recovery.database_heads), deployment_id)
            effects += self.workspace_effect_specs(workspace, deployment_id=deployment_id,
                                                   tree_digest=workspace_digest)
            self._restore_tree = (self.operation_id, workspace_tree) if workspace_digest is not None else None
            ctx = {"candidate": candidate, "manifest": manifest, "images": recovery.images,
                   "ref": "restore:" + recovery.bundle_id, "runtime_env": runtime_env,
                   "pointer": {"sha": revision, "ref": "restore:" + recovery.bundle_id,
                               "checkpoint": recovery.derived_from}}
            self._op_recovery = (self.operation_id, recovery)
            code = self.start_operation(
                "restore-instance", ctx, effects=effects, workspace=workspace, images={},
                confirmation=self.confirmation_ref(phrase, summary),
                source={"provenance": "git_commit" if verified else "unknown", "commit": revision,
                        "entries_sha256": pf_source.entries_digest(manifest), "deployment_id": deployment_id},
                input_bundle={"bundle_id": recovery.bundle_id, "manifest_sha256": recovery.manifest_sha256})
            self._restore_tree = None
        if recovery.purge["missing_historical_image_refs"]:
            log("WARNING: Some historical rollback image tags were already missing when the purge bundle was created. Those old rollback points remain source/data archives but may not be directly activatable.")
        return code

    def update(self, target, *, automatic=False, allow_migrations=False, skip_ci=False, keep_workspace=False):
        self.staging()
        self.database_ready()
        # PF-A3.1: the deployment record first, then the legacy pointer; never a claimed or unproven commit.
        current = self.deployed_commit()
        workspace = self.workspace_status()
        workspace_matches_deployed = current is not None and workspace["head"] == current and not workspace["dirty"]
        if current == target["sha"] and workspace_matches_deployed:
            log("Already at " + current + " with a clean matching workspace; no update needed.")
            return 0
        if automatic and current is None:
            raise Deferred("Automatic update refuses a deployment whose source commit is unknown; run a manual update.")
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
        # PF-A3.1: deployment image binding and a provable deployed source, read-only, before any effect.
        self.capture_preflight("update")
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
            manifest = self.candidate_manifest(candidate, target["sha"], verified=True)
            self.deployment_preflight(manifest)
            self.capacity_preflight("update")
            workspace_plan, lines = self.workspace_decision(manifest, keep_workspace)
            phrase = "UPDATE " + target["sha"][:12]
            summary = (f"Deploy {target['ref']} -> {target['sha']}\nMigration required: {changed}. Application access "
                       "will pause.\n" + "\n".join(lines))
            if not automatic:
                confirm(phrase, summary)
            self.sweep_staging()
            deployment_id = f"dep-{utc()}-{uuid.uuid4().hex[:8]}"
            view = self.current_deployment()
            commit = (view.record["source"]["commit"] if view is not None and view.mismatch is None else current) \
                or "0" * 40
            checkpoint_id = f"{utc()}-{commit[:12]}-{uuid.uuid4().hex[:6]}"
            heads = sorted(target_contract["heads"])
            database = self.env()["POSTGRES_DB"]
            identities = {service: self.image_identity(images[service]["id"], reference=images[service]["reference"])
                          for service in pf_docker.BUILT_SERVICES}
            effects = [
                {"phase": "preparing", "type": "source-stage", "target": "deployment:" + deployment_id,
                 "postcondition": f"staged {deployment_id}", "preconditions": ["confirmed"]},
                {"phase": "preserving", "type": "service-change", "target": "services:stop:frontend,backend",
                 "postcondition": "stopped", "preconditions": ["staged"]},
                {"phase": "preserving", "type": "capture", "target": "checkpoint:before-update",
                 "postcondition": "sealed and data_restore_verified",
                 "preconditions": ["bundle:" + checkpoint_id, "verify:pf_verify_" + uuid.uuid4().hex[:20],
                                   "writers stopped"]}]
            if changed:
                rehearsal = "pf_migrate_" + uuid.uuid4().hex[:20]
                effects += [
                    {"phase": "migrating", "type": "database-restore", "target": "database:" + rehearsal,
                     "postcondition": "restored and checked", "preconditions": ["from:" + checkpoint_id],
                     "preservation_refs": [checkpoint_id]},
                    {"phase": "migrating", "type": "database-migrate",
                     "target": f"database:{rehearsal}:heads={','.join(heads)}", "postcondition": "heads equal",
                     "preconditions": ["rehearsal candidate restored"], "preservation_refs": [checkpoint_id]},
                    {"phase": "migrating", "type": "database-drop", "target": "database:" + rehearsal,
                     "postcondition": "absent", "preconditions": ["rehearsal passed"],
                     "preservation_refs": [checkpoint_id]},
                    {"phase": "migrating", "type": "database-migrate",
                     "target": f"database:{database}:heads={','.join(heads)}", "postcondition": "heads equal",
                     "preconditions": ["writers stopped since the checkpoint"], "preservation_refs": [checkpoint_id]}]
            effects += self.activation_specs(identities, heads, deployment_id)
            effects += self.workspace_effect_specs(workspace_plan, deployment_id=deployment_id)
            ctx = {"candidate": candidate, "manifest": manifest, "images": images, "ref": target["ref"],
                   "pointer": {**target, "checkpoint": checkpoint_id}}
            return self.start_operation(
                "update", ctx, effects=effects, workspace=workspace_plan, images=identities,
                confirmation=self.confirmation_ref(phrase, summary) if not automatic else None,
                source={"provenance": "git_commit", "commit": target["sha"],
                        "entries_sha256": pf_source.entries_digest(manifest), "deployment_id": deployment_id},
                coverage=[{"store_id": "postgresql:" + database, "strategy_id": "postgresql-logical",
                           "included": True, "reason": "the active store"}])

    def swap_database(self, prepared, retained=None, *, current=None):
        """The ``database-switch`` effect: one transaction renames the current database to the plan's retained name
        and the prepared candidate to the current name. PF-A3.2: both names are pre-assigned by the plan."""
        current = current or self.env()["POSTGRES_DB"]
        retained = retained or "pf_keep_" + utc().lower() + "_" + uuid.uuid4().hex[:6]
        connections = self.sql("postgres", "SELECT count(*) FROM pg_stat_activity WHERE datname = '" + current + "';")
        if connections != "0":
            raise Failure("Other database sessions remain. Close IDE/psql connections; the script will not kill them.")
        self.sql("postgres", database_swap_sql(current, prepared, retained), mutation=True)
        log("Previous database retained (connections disabled): " + retained)
        return retained

    def archive_failure(self, payload, exc):
        """Operator copy of an ArchiveRefused (section 4.6)."""
        if exc.code == "archive-member-refused":
            member = repr(exc.member)[:200]
            return Failure(f"archive-member-refused: {payload}: {exc.reason}: {member}. Nothing was extracted.")
        return Failure(f"{exc.code}: {payload}: {exc.reason}. Nothing was extracted.")

    def extract_payload(self, view, path, destination, *, limits=pf_source.SOURCE_LIMITS):
        """Import one tar.gz payload of a strictly read bundle into the new private directory ``destination``
        (section 3.2): the payload is re-verified through one no-follow descriptor, inspected (pass 1), checked for
        capacity and extracted (pass 2) from that same descriptor. Returns the manifest of the written bytes."""
        payload = view.payload(path)
        destination = Path(destination)
        dir_fd = os.open(str(view.folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fd, _ = self._check_payload(dir_fd, view.bundle_id, path, payload["size"], payload["sha256"])
        finally:
            os.close(dir_fd)
        try:
            expected = (payload["expanded_bytes"], payload["members"], payload["members_sha256"])
            try:
                inventory = pf_source.inspect_archive(fd, limits=limits, expected=expected)
            except pf_source.ArchiveRefused as exc:
                raise self.archive_failure(path, exc) from exc
            need = inventory.expanded_bytes + ARCHIVE_MARGIN
            try:
                free = self.artifact_free_bytes(destination.parent)
            except OSError as exc:
                raise Failure(f"archive-capacity: {path}: free space cannot be measured ({exc.strerror}). Nothing was "
                              "extracted.") from exc
            if free < need:
                raise Failure(f"archive-capacity: {path}: {-(-need // 1048576)} MiB needed in {destination.parent}, "
                              f"{free // 1048576} MiB free. Nothing was extracted.")
            parent_fd = os.open(str(destination.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                return pf_source.extract_archive(fd, parent_fd, destination.name, inventory, limits=limits)
            except pf_source.ArchiveRefused as exc:
                raise self.archive_failure(path, exc) from exc
            except (pf_source.SourceError, OSError) as exc:
                raise Failure(f"archive-changed: {path}: {exc}. Nothing was extracted.") from exc
            finally:
                os.close(parent_fd)
        finally:
            os.close(fd)

    def inspect_payload(self, view, path, *, limits=pf_source.SOURCE_LIMITS):
        """Pass 1 only (no byte extracted) of one archive payload of a strictly read bundle: an importer refusal
        surfaces before any confirmation or journal (the archive is extracted, and inspected again, later)."""
        payload = view.payload(path)
        dir_fd = os.open(str(view.folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fd, _ = self._check_payload(dir_fd, view.bundle_id, path, payload["size"], payload["sha256"])
        finally:
            os.close(dir_fd)
        try:
            return pf_source.inspect_archive(fd, limits=limits, expected=(payload["expanded_bytes"],
                                                                          payload["members"],
                                                                          payload["members_sha256"]))
        except pf_source.ArchiveRefused as exc:
            raise self.archive_failure(path, exc) from exc
        finally:
            os.close(fd)

    def read_small_payload(self, view, path, kind):
        """The verified bytes of one small file payload (``kind``: config_env, state_file) of a strictly read bundle:
        opened through one no-follow descriptor like extract_payload and hashed again as read, so the bytes consumed
        are the bytes verified. A file the manifest does not list as ``kind`` is never opened (section 3.1 step 8,
        section 3.7; audit AF-1)."""
        payload = view.payload(path)
        if payload is None or payload["type"] != kind:
            raise bundle_failure("bundle-payload-mismatch", view.bundle_id, f"{path}: not a listed {kind} payload")
        if payload["size"] > SMALL_PAYLOAD_LIMIT:
            raise bundle_failure("bundle-payload-mismatch", view.bundle_id, f"{path}: size")
        dir_fd = os.open(str(view.folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fd, _ = self._check_payload(dir_fd, view.bundle_id, path, payload["size"], payload["sha256"])
        finally:
            os.close(dir_fd)
        try:
            data = b""
            while len(data) <= payload["size"]:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                data += block
        finally:
            os.close(fd)
        if len(data) != payload["size"] or pf_instance.sha256_bytes(data) != payload["sha256"]:
            raise bundle_failure("bundle-payload-mismatch", view.bundle_id, f"{path}: hash")
        return data

    @staticmethod
    def require_source_identity(view, tree, payload):
        """An extracted source tree equals the bundle's recorded tree identity when one is recorded."""
        expected = view.manifest["source"]["entries_sha256"]
        if expected is not None and pf_source.entries_digest(tree) != expected:
            raise Failure(f"source-manifest-mismatch: {payload}: the extracted tree differs from the recorded source "
                          "manifest. Nothing was changed.")

    def restore_candidate(self, view, prepared):
        """`rollback --restore-db`: restore the selected checkpoint into ``prepared`` (the store's locale), run the
        section 3.8 compatibility checks and write that checkpoint's verification record before any swap; on
        failure the candidate is dropped and ``checkpoint-incompatible`` is raised."""
        store = view.active_store
        started = utc()
        checks, passed, created = self.verify_store(store, view.folder / store["dump"], prepared, compatibility=True,
                                                    drop=False)
        self.write_verification(view, level="data_restore_verified", result="passed" if passed else "failed",
                                target={"kind": "isolated-database", "names": [prepared], "removed": False},
                                checks=checks, started_at=started)
        if not passed:
            if created:
                self.drop_database(prepared)
            failed = next(check for check in checks if check["result"] == "failed")
            raise Failure(f"checkpoint-incompatible: {view.bundle_id}: {failed['detail'] or failed['name']}. The "
                          "candidate database was dropped; the current database is unchanged.")

    def rollback(self, requested=None, restore_database=False, *, keep_workspace=False):
        self.staging()
        superseded = self.gate.entry if self.gate is not None and self.gate.action == "supersede" else None
        selected = self.choose_snapshot(requested)
        # PF-A3.1 target rule: only a healthy checkpoint (schema 1 or legacy) is a rollback target.
        if selected.capture_class != "healthy_checkpoint":
            raise Failure(f"checkpoint-not-rollback-target: {selected.bundle_id} is a {selected.capture_class} "
                          "capture: evidence and data for repair or export, never a rollback target. Nothing was "
                          "changed.")
        values = self.env()
        store = selected.active_store
        if store["database"] != values["POSTGRES_DB"] or (store["owner"] is not None
                                                           and store["owner"] != values["POSTGRES_USER"]):
            raise Failure("Checkpoint database identity differs from the current instance.")
        if selected.postgres_major != self.database_ready():
            raise Failure("Cross-major PostgreSQL restoration is not supported here.")
        self.verify_images(selected.images)
        recorded_db = selected.manifest["postgresql"]["image_id"]
        running_db = self.inspect("db")["Image"]
        if recorded_db is not None and running_db != recorded_db:
            log(f"note: db-image-changed: the running database image {running_db[7:19]} differs from the checkpoint's "
                f"{recorded_db[7:19]}; PostgreSQL major {selected.postgres_major} matches.")
        with tempfile.TemporaryDirectory(prefix="rollback-", dir=self.state) as folder:
            candidate = Path(folder) / "source"
            tree = self.extract_payload(selected, selected.source_payload, candidate)
            self.refuse_reserved_candidate_paths(candidate)  # before any confirmation, pause or effect
            self.require_source_identity(selected, tree, selected.source_payload)
            if migration_files(candidate) != selected.migration_files:
                raise Failure("Checkpoint migration fingerprint mismatch.")
            if not restore_database:
                # PF-A3.2 (section 3.3): a code-only rollback may supersede only an update without a database effect.
                if superseded is not None and not (superseded.kind == "update" and not any(
                        effect["type"].startswith("database-") for effect in superseded.plan["effects"])):
                    raise Failure("The incomplete operation may have changed data/schema; review recovery with --restore-db.")
                current_contract = self.ensure_local_contract()
                if current_contract["files"] != selected.migration_files or self.db_heads() != selected.database_heads:
                    raise Failure("Database/schema compatibility is not established. Code-only rollback refused; review --restore-db.")
                if selected.level == "failed":
                    log(f"note: verification-failed: {selected.bundle_id}'s last restore test failed; code-only rollback "
                        "does not use its database dump.")
            # The checkpoint's own claim does not assign provenance: the tree is `git_commit` only
            # when the protected store proves the hypothesis, otherwise it is recorded as unknown. The
            # proof reads the store (and refuses a commit that tracks a reserved name), so it runs
            # before any confirmation, pause, capture or database swap.
            hypothesis = selected.source_hypothesis
            verified = self.prove_tree_commit(candidate, hypothesis)
            revision = hypothesis if verified else None
            manifest = self.candidate_manifest(candidate, revision, verified=verified)
            self.capture_preflight("rollback")
            self.deployment_preflight(manifest)
            workspace, lines = self.workspace_decision(manifest, keep_workspace)
            if superseded is not None:
                # Section 3.5: nothing of the superseded operation may still run before this plan is written.
                self.require_not_running(superseded)
            self.capacity_preflight("rollback", view=selected if restore_database else None)
            checkpoint_id = f"{utc()}-{(self.deployed_commit() or '0' * 40)[:12]}-{uuid.uuid4().hex[:6]}"
            retained = "pf_keep_" + utc().lower() + "_" + uuid.uuid4().hex[:6] if restore_database else None
            phrase = ("RESTORE " + values["POSTGRES_DB"] + " " if restore_database else "ROLLBACK ") + selected.bundle_id
            summary = ("Database will return to the selected backup time. Newer writes will no longer appear in the "
                       "active app; the current DB is retained." if restore_database else
                       "Only code/images will change. Current data is kept. Schema equality does not prove all "
                       "business-semantic compatibility.") + "\n" + "\n".join(lines) + (
                f"\nSupersedes the incomplete {superseded.kind} operation {superseded.operation_id}."
                if superseded is not None else "") + (
                f"\nData choice: the active database returns to checkpoint {selected.bundle_id} "
                f"({selected.manifest['created_at']}). Writes after that time stay in the retained database {retained} "
                f"and in the before-rollback preservation {checkpoint_id} (or the fallback ID the capture records); "
                "nothing is merged." if restore_database else "")
            confirm(phrase, summary)
            self.sweep_staging()
            deployment_id = f"dep-{utc()}-{uuid.uuid4().hex[:8]}"
            database = values["POSTGRES_DB"]
            effects = [
                {"phase": "preparing", "type": "source-stage", "target": "deployment:" + deployment_id,
                 "postcondition": f"staged {deployment_id}", "preconditions": ["confirmed"]},
                {"phase": "preserving-current", "type": "service-change", "target": "services:stop:frontend,backend",
                 "postcondition": "stopped", "preconditions": ["staged"]},
                {"phase": "preserving-current", "type": "capture", "target": "checkpoint:before-rollback",
                 "postcondition": "sealed and data_restore_verified",
                 "preconditions": ["bundle:" + checkpoint_id, "verify:pf_verify_" + uuid.uuid4().hex[:20],
                                   "writers stopped"]}]
            if restore_database:
                prepared = "pf_restore_" + uuid.uuid4().hex[:20]
                effects += [
                    {"phase": "restoring-candidate", "type": "database-restore", "target": "database:" + prepared,
                     "postcondition": "restored and checked", "preconditions": ["from:" + selected.bundle_id],
                     "preservation_refs": [checkpoint_id]},
                    {"phase": "switching", "type": "database-switch",
                     "target": f"database-switch:{database}:{prepared}:{retained}",
                     "postcondition": "current is the prepared database; retained exists",
                     "preconditions": ["no other session on the current database"],
                     "preservation_refs": [checkpoint_id]}]
            images = {service: image for service, image in selected.images.items()
                      if image is not None and image.get("platform")}
            effects += self.activation_specs(selected.images, sorted(selected.database_heads), deployment_id)
            effects += self.workspace_effect_specs(workspace, deployment_id=deployment_id)
            ctx = {"candidate": candidate, "manifest": manifest, "images": selected.images,
                   "ref": "rollback:" + selected.bundle_id,
                   "pointer": {"sha": revision, "ref": "rollback:" + selected.bundle_id, "checkpoint": checkpoint_id,
                               "retained_database": retained}}
            self._op_selected = (self.operation_id, selected)
            return self.start_operation(
                "rollback", ctx, effects=effects, workspace=workspace, images=images,
                confirmation=self.confirmation_ref(phrase, summary), summary_text=summary,
                source={"provenance": "git_commit" if verified else "unknown", "commit": revision,
                        "entries_sha256": pf_source.entries_digest(manifest), "deployment_id": deployment_id},
                supersedes=superseded.operation_id if superseded is not None else None,
                input_bundle={"bundle_id": selected.bundle_id, "manifest_sha256": selected.manifest_sha256},
                coverage=[{"store_id": "postgresql:" + database, "strategy_id": "postgresql-logical",
                           "included": True, "reason": "the active store"}])

    def reset_database(self):
        self.staging()
        self.database_ready()
        # PF-A3.3 section 3.7: decided by the observed contract; a schema/image mismatch is preserved as emergency
        # preservation first (the clean database is migrated to the running image's heads), a deployment-image mismatch
        # and an unreadable contract are refused before the confirmation.
        observation = self.observe_contract()
        if observation["matches"]:
            self.capture_preflight("reset-db")
            preservation = "healthy checkpoint"
        elif observation["kind"] == "schema-image-mismatch" and observation["image_heads"] is not None \
                and observation["backend_image_id"]:
            view = self.current_deployment()
            expected = observation["expected_backend_image_id"]
            if expected is not None and expected != observation["backend_image_id"]:
                raise self.reset_image_mismatch(observation, view)
            self.workspace_archive_preflight(view)
            preservation = "emergency preservation (schema-image-mismatch)"
        elif observation["kind"] == "deployment-image-mismatch":
            raise self.reset_image_mismatch(observation, self.current_deployment())
        else:
            exc = Failure(f"reset-contract-unknown: the running backend image's Alembic contract cannot be read "
                          f"({observation['detail'] or 'unknown'}), so the clean database has no target heads. Nothing "
                          "was changed.")
            exc.code = "reset-contract-unknown"
            raise exc
        self.capacity_preflight("reset-db")
        database = self.env()["POSTGRES_DB"]
        heads = sorted(observation["image_heads"])
        commit = (self.deployed_commit() or "0" * 40)
        checkpoint_id = f"{utc()}-{commit[:12]}-{uuid.uuid4().hex[:6]}"
        prepared = "pf_clean_" + uuid.uuid4().hex[:20]
        retained = "pf_keep_" + utc().lower() + "_" + uuid.uuid4().hex[:6]
        phrase = "RESET " + database
        summary = ("All current application data, including configuration/master data, will be removed from the "
                   "active instance. A verified backup and retained database are created first. This does not make "
                   "staging production-ready.\n"
                   f"Current data preservation: {preservation} {checkpoint_id} (or the fallback ID recorded by the "
                   f"capture); the current database is kept as {retained}.\n"
                   f"Clean database: migrated to the running image's heads {','.join(heads) or 'none'}.")
        confirm(phrase, summary)
        images = self.running_images()
        effects = [
            {"phase": "preserving", "type": "service-change", "target": "services:stop:frontend,backend",
             "postcondition": "stopped", "preconditions": ["confirmed"]},
            {"phase": "preserving", "type": "capture", "target": "checkpoint:before-reset",
             "postcondition": "sealed and data_restore_verified",
             "preconditions": ["bundle:" + checkpoint_id, "verify:pf_verify_" + uuid.uuid4().hex[:20],
                               "writers stopped"]},
            {"phase": "initializing", "type": "database-create", "target": "database:" + prepared,
             "postcondition": "exists", "preconditions": ["checkpoint sealed"], "preservation_refs": [checkpoint_id]},
            {"phase": "initializing", "type": "database-migrate", "target": f"database:{prepared}:heads={','.join(heads)}",
             "postcondition": "heads equal", "preconditions": ["clean candidate created"],
             "preservation_refs": [checkpoint_id]},
            {"phase": "switching", "type": "database-switch", "target": f"database-switch:{database}:{prepared}:{retained}",
             "postcondition": "current is the prepared database; retained exists",
             "preconditions": ["no other session on the current database"], "preservation_refs": [checkpoint_id]}]
        effects += self.activation_specs(images, heads, None, seal=False)
        effects.append({"phase": "finalizing", "type": "file-write", "target": "last-reset",
                        "postcondition": "bytes sha256 recorded at completion",
                        "preconditions": ["checkpoint:" + checkpoint_id]})
        return self.start_operation(
            "reset-db", {}, effects=effects, workspace=self.workspace_plan("untouched"), images=images,
            confirmation=self.confirmation_ref(phrase, summary), source=self.running_source(), summary_text=summary,
            coverage=[{"store_id": "postgresql:" + database, "strategy_id": "postgresql-logical", "included": True,
                       "reason": "the active store"}])

    def reset_image_mismatch(self, observation, view):
        dep = view.deployment_id if view is not None else "(no record)"
        expected = observation["expected_backend_image_id"] or "sha256:" + "?" * 12
        exc = Failure(f"reset-deployment-image-mismatch: the running backend image "
                      f"{str(observation['backend_image_id'])[7:19]} is not deployment {dep}'s {expected[7:19]}; "
                      "reset-db would activate an image the deployment record does not name. Converge the deployment "
                      f"first ('{self.pf_command()} update …' or 'rollback'). Nothing was changed.")
        exc.code = "reset-deployment-image-mismatch"
        return exc

    def backup_operation(self):
        """``pf backup`` (section 3.4): the capture through the sealed manifest, then the verification, as two
        journaled effects. A caught capture or verification failure closes the backup failed_preserved in-process
        (OD-A32-13; not blocking, no fail_closed); only a crash or an interrupt leaves it open for ``resume``."""
        self.database_ready()
        self.capture_preflight("backup")
        self.ensure_local_contract()
        self.capacity_preflight("backup")
        view = self.current_deployment()
        commit = (view.record["source"]["commit"] if view is not None and view.mismatch is None
                  else self.deployed_commit()) or "0" * 40
        bundle_id = f"{utc()}-{commit[:12]}-{uuid.uuid4().hex[:6]}"
        database = self.env()["POSTGRES_DB"]
        reason = "scheduled-or-manual-backup"
        effects = [
            {"phase": "capturing", "type": "capture", "target": "checkpoint:" + reason,
             "postcondition": "sealed and data_restore_verified",
             "preconditions": ["bundle:" + bundle_id, "verify:pf_verify_" + uuid.uuid4().hex[:20]]},
            {"phase": "verifying", "type": "verification", "target": "bundle:" + reason,
             "postcondition": "passed data_restore_verified record", "preconditions": ["sealed manifest"]}]
        self.open_operation(
            "backup", effects=effects, workspace=self.workspace_plan("untouched"), confirmation=None,
            images=self.running_images(), source=self.running_source(),
            coverage=[{"store_id": "postgresql:" + database, "strategy_id": "postgresql-logical", "included": True,
                       "reason": "the active store"}])
        return self.run_plan({})

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
        index = self.log_operations(trusted=validation.private_state_trusted)
        self.log_effects(index)
        log("Python: " + sys.version.split()[0])
        self.log_registered_tools()
        log("Permissions: " + self.describe_permissions())
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
            ("Deployment", self.describe_deployment),
        ))
        log("Database volume capacity, NAS recovery, and production readiness are not certified by doctor.")
        log("Default doctor is read-only: it created, repaired and migrated nothing.")
        if unavailable:
            raise Failure("Doctor found unavailable components: " + ", ".join(unavailable))

    def status(self, operation_id=None):
        # Identity, trust summary and journal come from protected state and are
        # shown before any app config, .env, Git or Docker access (A1-T16). A refused
        # context or a rejected configuration ends here with zero transport calls (A11-R05).
        # PF-A3.2 (section 3.10): the operations block reads only protected operation files; ``--operation``
        # prints the detail of one operation and ends there (nothing read beyond the scan and its files).
        self.log_context()
        self.log_installation()
        validation = self.log_trust_summary()
        index = self.log_operations(trusted=validation.private_state_trusted, operation_id=operation_id)
        if operation_id is not None:
            return 0
        self.log_effects(index)
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
            ("Deployed source", self.describe_deployed_source),
            ("Deployment", self.describe_deployment),
            ("Workspace", self.describe_workspace),
            ("Revision checkpoints", lambda: str(len(self.snapshots()))),
            ("Database revisions", lambda: ", ".join(self.db_heads()) or "uninitialized"),
            ("Compose services", lambda: "\n" + self.compose("ps")),
        ))
        try:
            # PF-A3.3 (section 3.14): the live part, only when there is something to clean up.
            items = [item for item in pf_config.cleanup_candidates(index, self.cleanup_observations(index))
                     if item.cls != "report-only"]
            if items:
                log(f"Cleanup candidates: {len(items)} ({self.pf_command()} cleanup)")
        except DaemonFailure:
            pass
        except (Failure, OSError, ValueError, KeyError) as exc:
            log("Cleanup candidates: unavailable: " + (str(exc).splitlines() or ["?"])[0])
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


KEEP_WORKSPACE_HELP = "Keep the current workspace instead of the generation switch (recorded in the plan)"
# PF-A3.3: the postcondition of the instance purge's verification effect (an A3.2-opened plan keeps its own string).
FUNCTIONAL_POSTCONDITION = "passed functional_recovery_verified record"
A32_PURGE_POSTCONDITION = "passed data_restore_verified record"


def generation_value(value):
    """argparse type of ``cleanup --generation``: a workspace generation ID."""
    if not re.fullmatch(pf_config.GENERATION_PATTERN[1:-1], value or ""):
        raise argparse.ArgumentTypeError(f"{value!r} is not a workspace generation ID (wsg-<stamp>-<8 hex>)")
    return value


def history_value(value):
    """argparse type of ``cleanup --checkpoint-history``: ``<project>.pre-restore-<8 hex>``."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}\.pre-restore-[0-9a-f]{8}", value or ""):
        raise argparse.ArgumentTypeError(f"{value!r} is not a displaced checkpoint history (<project>.pre-restore-<8 "
                                         "hex>)")
    return value


def recovery_target_value(value):
    """argparse type of ``cleanup --recovery-target``: ``pfrecover-<12 hex>``."""
    if not pf_docker.RECOVER_PROJECT_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(f"{value!r} is not a recovery target project (pfrecover-<12 hex>)")
    return value


def operation_id_value(value):
    """argparse type of --operation: the operation ID pattern (a usage error, exit 2, otherwise)."""
    if not OPERATION_ID_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(f"{value!r} is not an operation ID (<stamp>-<command>-<8 hex>)")
    return value


def parser():
    # PF-A1.4: abbreviations are refused everywhere (OD-A14-10); `pf --inst x` is unknown-option.
    result = argparse.ArgumentParser(description="PartFlow NAS staging administration; use --help on a command.",
                                     allow_abbrev=False)
    result.add_argument("--instance", help="Registered instance slug or UUID; required when several instances exist and no protected default is set")
    subs = result.add_subparsers(dest="command", required=True)

    def add(name, **kwargs):
        return subs.add_parser(name, allow_abbrev=False, **kwargs)

    for name in ("doctor", "reset-db", "abort-deploy"):
        add(name)
    # PF-A3.2 (section 4.1): resume by journal; status of one operation.
    status = add("status", help="Show the instance's state; --operation prints one operation's journal (read-only)")
    status.add_argument("--operation", type=operation_id_value, help="An operation ID (open or closed)")
    resume = add("resume", help="Continue, reopen, abandon or keep the workspace of the interrupted operation")
    resume.add_argument("--operation", type=operation_id_value,
                        help="The operation to resume (default: the single blocking operation)")
    resume_action = resume.add_mutually_exclusive_group()
    resume_action.add_argument("--abandon", action="store_true",
                               help="Cancel the operation where a cancel route exists")
    resume_action.add_argument("--keep-workspace", action="store_true",
                               help="Keep the current workspace (workspace refresh phases only)")
    resume_action.add_argument("--acknowledge", action="store_true",
                               help="Acknowledge the observed runner records of an operation without a journal "
                                    "(needs --operation)")
    # PF-A3.3 (section 4.1): the observe-only cleanup report and the journaled cleanup.
    cleanup = add("cleanup", help="Report disposable leftovers (read-only); --apply removes the default set")
    cleanup.add_argument("--apply", action="store_true", help="Remove the reported default set (journaled)")
    cleanup.add_argument("--generation", action="append", type=generation_value, default=[],
                         help="Also seal and retire this retained workspace generation (repeatable)")
    cleanup.add_argument("--checkpoint-history", action="append", type=history_value, default=[],
                         help="Also remove this displaced checkpoint history (repeatable)")
    cleanup.add_argument("--recovery-target", action="append", type=recovery_target_value, default=[],
                         help="Also remove this side-by-side recovery target (repeatable)")
    backup = add("backup", help="Create a verified healthy checkpoint (--emergency: emergency preservation)")
    backup.add_argument("--emergency", action="store_true",
                        help="Capture the current data even when the schema/image contract does not hold (terminal "
                             "only; evidence and data, never a rollback target)")

    # PF-A2.3: `pf permissions check|plan|apply`; bare `pf permissions` is refused by classify_command.
    permissions = add("permissions", help="Check, preview or apply the semantic permission policy")
    permission_verbs = permissions.add_subparsers(dest="permissions_verb", required=True)
    for verb, text in (("check", "Compare every scope with the permission policy in force (read-only)"),
                       ("plan", "Preview the changes, groups and plan hash (read-only)"),
                       ("apply", "Wizard, one typed confirmation, then apply and verify (terminal only)")):
        command = permission_verbs.add_parser(verb, allow_abbrev=False, help=text)
        command.add_argument("--scope", action="append", dest="scopes", choices=PERMISSION_SCOPES,
                             help="Limit to one scope (repeatable; default: all)")
        if verb != "check":
            command.add_argument("--details", action="store_true", help="Show octal and symbolic targets per entry")
        if verb == "apply":
            journal = command.add_mutually_exclusive_group()
            journal.add_argument("--resume", action="store_true", help="Finish the interrupted permission apply")
            journal.add_argument("--abandon", action="store_true",
                                 help="Compensate the interrupted permission apply from its effect journal")

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
    deploy.add_argument("--keep-workspace", action="store_true", help=KEEP_WORKSPACE_HELP)

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
    restore.add_argument("--side-by-side", action="store_true", help="Restore the bundle into a kept, isolated recovery target beside the running instance (no listener; the instance is not changed)")
    restore.add_argument("--keep-workspace", action="store_true", help=KEEP_WORKSPACE_HELP)

    backups = add("backups", help="List revision checkpoints, newest first, 10 per page")
    backups.add_argument("--page", type=int, default=1)
    rollback = add("rollback")
    rollback.add_argument("backup_id", nargs="?")
    rollback.add_argument("--restore-db", action="store_true")
    rollback.add_argument("--keep-workspace", action="store_true", help=KEEP_WORKSPACE_HELP)
    update = add("update")
    selection = update.add_mutually_exclusive_group()
    selection.add_argument("--latest", action="store_true", help="Resolve the current configured branch tip to a fixed SHA")
    selection.add_argument("--commit")
    selection.add_argument("--release", help="Published tag or 'latest' (default)")
    update.add_argument("--channel", choices=("stable", "prerelease"))
    update.add_argument("--allow-migrations", action="store_true")
    update.add_argument("--skip-ci", action="store_true", help="Explicit manual staging exception; never used by the scheduler")
    update.add_argument("--keep-workspace", action="store_true", help=KEEP_WORKSPACE_HELP)
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
    policy_class: str    # "" | "backup" | "release-check" (PF-A2.3: "permissions" retired, OD-A23-11)
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
    # PF-A2.3: the read-only verbs keep a trusted launch (they read protected state) but not a trusted context: they
    # map refuse findings onto scopes themselves (section 3.5). apply is terminal-only with its own journal route.
    Route("permissions check", "read-only", False, True, False, "any", "none", "never", "allowed", "",
          "Controller.permissions_check"),
    Route("permissions plan", "read-only", False, True, False, "any", "none", "never", "allowed", "",
          "Controller.permissions_plan"),
    _locked("permissions apply", "mutating", "permissions apply", "none", "never", "terminal", "",
            "Controller.permissions_apply"),
    _locked("deploy", "mutating", "refuse", "empty-target", "always", "terminal", "", "Controller.deploy"),
    _locked("abort-deploy", "mutating", "abort-deploy", "plan", "always", "terminal", "", "Controller.abort_deploy"),
    _locked("purge", "mutating", "purge", "plan", "never", "terminal", "", "Controller.purge"),
    _locked("restore-instance", "mutating", "restore-instance", "restore", "unless-side-by-side", "terminal", "",
            "Controller.restore_instance"),
    _locked("backup", "mutating", "backup", "owned", "never", "policy", "backup", "Controller.backup_operation"),
    # PF-A3.1: attended emergency preservation (section 4.2); its own pending route, never a policy grant.
    _locked("backup emergency", "mutating", "backup emergency", "owned", "never", "terminal", "",
            "Controller.capture_emergency"),
    _locked("reset-db", "mutating", "refuse", "owned", "always", "terminal", "", "Controller.reset_database"),
    _locked("rollback", "mutating", "rollback", "owned", "always", "terminal", "", "Controller.rollback"),
    _locked("resume", "mutating", "resume", "owned", "always", "terminal", "", "Controller.resume_operation"),
    # PF-A3.3 (section 4.2): mutating only with --apply; the report and resume --acknowledge are observe-only locks.
    _locked("cleanup", "conditional", "cleanup", "none", "never", "terminal", "", "Controller.cleanup"),
    _locked("update", "mutating", "refuse", "owned", "always", "terminal", "", "Controller.update"),
    _locked("release-check", "conditional", "refuse", "apply", "if-apply", "policy", "release-check",
            "Controller.resolve"),
    # PF-A2.2: the config wizards (registered mode); pre-registration mode leaves main() before any registry read
    # of the instance path (config_admin_unregistered).
    _locked("config", "mutating", "refuse", "none", "never", "terminal", "", "Controller.configure"),
)}
# First command words (PF-A2.3: "permissions <verb>" rows share the word "permissions").
KNOWN_COMMANDS = frozenset(name.split(" ", 1)[0] for name in DISPATCH)
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
              "pf logs; change data with pf backup, pf reset-db or pf rollback --restore-db. Recovered data beside "
              "the instance: pf restore-instance <id> --side-by-side.",
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
    EntryRoute("E5", "backup.sh", "backup", "mutating", "yes", "backup",
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
    if word == "permissions":
        if rest[1:2] and rest[1] in PERMISSIONS_VERBS:
            return "route", word
        raise OptionRefused("permissions-verb-required: 'pf permissions' no longer changes anything by itself. Use "
                            "'pf permissions check' (compare), 'pf permissions plan' (preview) or 'pf permissions "
                            "apply' (wizard, one confirmation, then apply). Nothing was read or changed.")
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


def route_key(args):
    """The DISPATCH key of parsed arguments: the command word, plus the verb for `permissions` (PF-A2.3)."""
    if args.command == "permissions":
        return "permissions " + args.permissions_verb
    if args.command == "backup" and getattr(args, "emergency", False):
        return "backup emergency"
    return args.command


def check_permission_options(args):
    """PF-A2.3: --resume/--abandon act on the frozen plan; --scope is refused with them (exit 2), before any
    registry read."""
    if getattr(args, "permissions_verb", None) == "apply" and (args.resume or args.abandon) and args.scopes:
        raise OptionRefused("permissions-option-invalid: --resume and --abandon act on the frozen plan of the "
                            "interrupted apply; --scope cannot be combined with them. Nothing was read or changed.")


def check_keep_workspace(args):
    """PF-A3.2 (section 4.1): --keep-workspace where no workspace switch exists is a usage error (exit 2)."""
    if getattr(args, "keep_workspace", False) and (getattr(args, "current", False) or getattr(args, "side_by_side",
                                                                                                 False)):
        raise OptionRefused("usage-error: --keep-workspace has no effect here (deploy --current and restore-instance "
                            "--side-by-side never switch the workspace). Nothing was read or changed.")
    if args.command == "deploy" and getattr(args, "keep_workspace", False)             and not (args.latest or args.commit or args.release):
        raise OptionRefused("usage-error: --keep-workspace has no effect here (deploy --current never switches the "
                            "workspace). Nothing was read or changed.")


def gate_request(args):
    """The section 3.3 gate inputs of one parsed command line."""
    delete = True if getattr(args, "delete_backups", False) else False if getattr(args, "keep_backups", False)         else None
    return {"operation": getattr(args, "operation", None), "abandon": bool(getattr(args, "abandon", False)),
            "keep_workspace": bool(getattr(args, "keep_workspace", False)), "delete_backups": delete,
            "reset_admin_config": bool(getattr(args, "reset_admin_config", False)),
            "bundle_id": getattr(args, "recovery_id", None), "side_by_side": bool(getattr(args, "side_by_side", False)),
            "generations": tuple(getattr(args, "generation", None) or ()),
            "histories": tuple(getattr(args, "checkpoint_history", None) or ()),
            "targets": tuple(getattr(args, "recovery_target", None) or ())}


# PF-A3.3: the gate routes taken with the observe-only lock (section 3.9 report, section 3.10 acknowledgement).
OBSERVE_ONLY_ROUTES = frozenset({"cleanup report", "resume acknowledge"})


def gate_route_name(route, args):
    """The section 3.3/3.11 gate route of a parsed command line: ``cleanup report``/``cleanup apply`` and ``resume
    acknowledge`` are distinct gate rows of one DISPATCH word."""
    if route.name == "cleanup":
        return "cleanup apply" if args.apply else "cleanup report"
    if route.name == "resume" and getattr(args, "acknowledge", False):
        return "resume acknowledge"
    return route.name


def check_integrated_options(args):
    """PF-A3.3 (section 4.1) usage errors (exit 2), before any registry read: cleanup selectors need --apply; resume
    --acknowledge needs --operation."""
    if args.command == "cleanup" and not args.apply and (args.generation or args.checkpoint_history
                                                         or args.recovery_target):
        raise OptionRefused("usage-error: --generation, --checkpoint-history and --recovery-target select items of "
                            "'pf cleanup --apply'; the report takes none. Nothing was read or changed.")
    if args.command == "resume" and getattr(args, "acknowledge", False) and not args.operation:
        raise OptionRefused("usage-error: --acknowledge names the operation whose runner records are acknowledged; "
                            "add --operation <ID>. Nothing was read or changed.")


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
        spelled = PENDING_ROUTE_COMMANDS.get(route.name, route.name)
        raise Failure(f"terminal-required: '{spelled}' asks for a typed confirmation and cannot run without a "
                      "terminal (scheduled task, script, or ssh without -t). Run it interactively: sudo pf --instance "
                      f"{slug} {spelled}. Nothing was changed.")
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


def purged_by(index):
    """PF-A3.3 (section 3.4): the completed instance purge that is the instance's latest lifecycle outcome (no later
    completed deploy or restore-instance), or None; derived from the protected completed journals."""
    latest = None
    for entry in index.entries:
        if entry.cls != "closed" or entry.journal["phase"] != "completed":
            continue
        if entry.kind in ("purge", "deploy", "restore-instance"):
            if latest is None or (entry.journal["updated_at"], entry.operation_id) > \
                    (latest.journal["updated_at"], latest.operation_id):
                latest = entry
    return latest if latest is not None and latest.kind == "purge" else None


def journal_column(context):
    """PF-A3.2 (section 3.10): the ``instances`` journal column from protected files only: ``<kind>/<phase>`` of the
    blocking lifecycle operation, ``permissions/<phase>``, ``invalid`` or ``none`` (read-only)."""
    try:
        files, overflow = pf_instance.scan_operations(context.operations_dir)
    except pf_instance.ContextError:
        return "invalid"
    try:
        pending = pf_instance.read_bytes_nofollow(context.journal_path)
    except FileNotFoundError:
        pending = None
    except OSError:
        pending = b"\x00unreadable"
    index = pf_config.classify_operations(
        files, permissions_journal=pending, overflow=overflow,
        validate=lambda value, name, plan=None: lifecycle_errors(value, name, plan=plan))
    if index.overflow or index.legacy is not None or any(item.cls == "invalid" for item in index.blocking):
        return "invalid"
    if index.permissions is not None:
        return f"permissions/{index.permissions.get('phase')}"
    if index.blocking:
        return "; ".join(f"{item.kind}/{item.phase}" for item in index.blocking)
    if purged_by(index) is not None:
        return "none  lifecycle=purged"
    return "none"


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
        journal_text = journal_column(context)
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
        route = DISPATCH[route_key(args)]
        if args.command == "install":
            # PF-A2.1: the installer reads the registry and takes its locks itself (registry lock first).
            return pf_install.run_installed(root, args, running_release=running_release, trusted_launch=trusted_launch,
                                            interaction=pf_install.Interaction(unattended, input_line, log))
        # PF-A2.2: config option combinations are refused before any registry read; --configuration selects the
        # pre-registration mode of `config admin`, which takes only the registry lock (never an instance lock).
        check_config_options(args, explicit_instance=options.instance is not None)
        check_permission_options(args)
        check_integrated_options(args)
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
        check_keep_workspace(args)
        gate_name = gate_route_name(route, args)
        if route.trusted_context:
            # PF-A2.2: the config wizards read pf-config.json themselves (absent, refused or mismatched files reach
            # their own outcome); the protected-context refusal still runs for every trusted route. PF-A2.3:
            # `permissions apply` reads it only for workspace_write_group and the proposals. PF-A3.2: a locked route
            # reads the operation index first (read-only) for the section 3.7a exception and the section 3.8
            # admin configuration source; the gate re-checks both under the lock.
            index = controller.operation_index() if route.lock else None
            controller.require_trusted_context(load_config=route.name not in NO_CONFIG_ROUTES, route=gate_name,
                                               index=index, operation=getattr(args, "operation", None))
        if route.trusted_launch and not trusted_launch:
            raise Failure(
                ("Mutating commands" if route.lock else "Compose views (ps, logs)")
                + " must start through the installed bootstrap launcher (Python isolated mode, sanitized environment)."
            )

        if not route.lock:
            if args.command == "doctor":
                controller.doctor()
            elif args.command == "status":
                controller.status(args.operation)
            elif args.command == "backups":
                controller.display_page(controller.snapshots(), args.page)
            elif args.command == "recoveries":
                controller.display_recoveries(controller.recoveries(), args.page)
            elif args.command == "ps":
                controller.compose_ps(args)
            elif args.command == "logs":
                controller.compose_logs(args)
            elif route.name == "permissions check":
                return controller.permissions_check(args)
            elif route.name == "permissions plan":
                return controller.permissions_plan(args)
            return 0

        # Every locked route: validated context and sanitized launch (above), then the unattended and
        # auto-apply gates before the lock, the stable lock with its explicit journal route, and the
        # ownership preflight before any confirmation, journal write, pause, tag or Compose child.
        require_attended(route, args, controller, explicit_instance=options.instance is not None,
                         selected_by="protected default" if registry.default_instance_id is not None
                         else "single registration")
        if route.policy_class == "release-check" and args.apply and not controller.policy_permits("auto-apply"):
            slug, revision = context.slug, context.approved_policy.revision
            raise Deferred(
                f"auto-apply-not-permitted: release apply needs a protected policy that permits it; approved policy "
                f"revision {revision} of instance {slug} does not (automatic apply is off in this checkpoint). The "
                f"editable auto_update setting is a proposal only. Nothing was changed. Check with 'pf --instance "
                f"{slug} release-check' and apply manually with 'pf --instance {slug} update --release <tag>'.")
        held_lock.enter_context(controller.lock(pending_route=gate_name, freeze=route.name not in NO_CONFIG_ROUTES,
                                                request=gate_request(args),
                                                observe_only=gate_name in OBSERVE_ONLY_ROUTES))
        if route_preflight(route, args) == "owned":
            controller.require_topology_owned(route.name)
        managed_started = route_fail_closed(route, args)

        if gate_name == "resume acknowledge":
            # PF-A3.3 (section 3.10): observe-only; the gate never re-enters the acknowledged directory.
            controller.acknowledge_effects(args.operation)
        elif controller.gate is not None and controller.gate.action == "reenter":
            # PF-A3.2 (section 3.3): `resume` and the aliases re-enter the blocking operation by its journal (PF-A3.3:
            # also `cleanup --apply` with the selectors of an open cleanup).
            controller.resume_operation(getattr(args, "operation", None), abandon=bool(getattr(args, "abandon", False)),
                                        keep_workspace=bool(getattr(args, "keep_workspace", False)),
                                        alias=None if route.name == "resume" else route.name)
        elif args.command == "cleanup":
            controller.cleanup(apply=args.apply, generations=tuple(args.generation),
                               histories=tuple(args.checkpoint_history), targets=tuple(args.recovery_target))
        elif route.name == "permissions apply":
            controller.permissions_apply(args)
        elif args.command == "deploy":
            use_current = args.current or not (args.latest or args.commit or args.release)
            target = None if use_current else controller.resolve(
                latest=args.latest, commit=args.commit, release=args.release, channel=args.channel
            )
            if not use_current and target is None:
                raise Failure("No published release exists for this channel. Select prerelease, --latest, or an explicit commit.")
            controller.deploy(target, use_current=use_current, skip_ci=args.skip_ci,
                              keep_workspace=args.keep_workspace)
        elif args.command == "purge":
            delete_backups = True if args.delete_backups else False if args.keep_backups else None
            controller.purge(delete_backups=delete_backups, reset_admin_config=args.reset_admin_config)
        elif args.command == "restore-instance":
            # The mutation target is the selected registered instance, never a
            # path embedded in the (untrusted) recovery bundle.
            recovery = controller.choose_recovery(args.recovery_id)
            controller.restore_instance(recovery, side_by_side=args.side_by_side, keep_workspace=args.keep_workspace)
            managed_started = False
        elif route.name == "backup emergency":
            controller.capture_emergency()
            log("Copy the entire checkpoint directory off-NAS. It contains production-like database data even though runtime .env is stored separately.")
        elif args.command == "backup":
            controller.backup_operation()
        elif args.command == "reset-db":
            controller.reset_database()
        elif args.command == "config":
            controller.configure(args)
        elif args.command == "abort-deploy":
            controller.abort_deploy()
        elif args.command == "rollback":
            controller.rollback(args.backup_id, args.restore_db, keep_workspace=args.keep_workspace)
        elif args.command == "update":
            target = controller.resolve(latest=args.latest, commit=args.commit, release=args.release, channel=args.channel)
            if target is None:
                raise Failure("No published release exists for this channel. Select prerelease or use --latest manually.")
            controller.update(target, allow_migrations=args.allow_migrations, skip_ci=args.skip_ci,
                              keep_workspace=args.keep_workspace)
        elif args.command == "release-check":
            target = controller.resolve(channel=args.channel)
            if target is None:
                log("No published release for this channel. No update attempted.")
                return 0
            log("Selected release: " + target["ref"] + " -> " + target["sha"])
            log("Deployed source: " + controller.describe_deployed_source())
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
