#!/usr/bin/env python3
"""Conservative lifecycle commands for the supplied PartFlow NAS staging stack.

Python standard library only. No application or database business rules live here.
Local configuration and this controller are never replaced by downloaded source.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import grp
import gzip
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "2.5.0"
PAGE_SIZE = 10
DEFAULTS = {
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
# Runtime control/configuration lives outside the writable repository in v2.5.
SOURCE_EXCLUDES = {".git", ".env", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
AUTO_REVIEW_PATHS = (
    ".env.example", "compose.yaml", "backend/Dockerfile", "frontend/Dockerfile",
    "backend/.dockerignore", "frontend/.dockerignore", ".github/workflows/ci.yml",
)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BACKUP_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}-[0-9a-f]{6}\Z")
RECOVERY_RE = re.compile(r"purge-\d{8}T\d{6}Z-[0-9a-f]{12}-[0-9a-f]{6}\Z")
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
TIMEZONE_RE = re.compile(r"(?:UTC|[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+)\Z")
HOST_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")



class Failure(RuntimeError):
    pass


class Deferred(Failure):
    """A scheduled update needs human review; exit 20, never silently succeed."""


def log(message):
    print(message, flush=True)


def utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def read_dotenv(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise Failure("Invalid .env entry; expected KEY=VALUE.")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise Failure("Unsupported multiline or malformed quoted .env value.")
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def validate_timezone_name(value):
    if not TIMEZONE_RE.fullmatch(value):
        raise Failure("SITE_TIMEZONE must be UTC or an IANA-style zone such as America/Los_Angeles.")
    return value


def validate_identifier(value):
    quote_identifier(value)
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


def render_env_template(template_path, values):
    template_path = Path(template_path)
    required = set(REQUIRED_NAS_ENV_KEYS)
    seen = set()
    rendered = []
    for original in template_path.read_text(encoding="utf-8").splitlines():
        stripped = original.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                if key in seen:
                    raise Failure(f"Duplicate {key} in {template_path}.")
                value = str(values[key])
                if "\n" in value or "\r" in value:
                    raise Failure(f"Unsafe newline in {key}.")
                rendered.append(f"{key}={value}")
                seen.add(key)
                continue
        rendered.append(original)
    missing = required - seen
    if missing:
        raise Failure("NAS environment sample is missing required fields: " + ", ".join(sorted(missing)))
    return "\n".join(rendered) + "\n"


def prompt_value(label, default=None, validator=None):
    if not sys.stdin.isatty():
        raise Failure("Initial deployment configuration requires an interactive terminal.")
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default not in (None, ""):
            value = str(default)
        if not value:
            log("A value is required.")
            continue
        try:
            return validator(value) if validator else value
        except Failure as exc:
            log("Invalid value: " + str(exc))


def prompt_yes_no(label, default=True):
    if not sys.stdin.isatty():
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


def confirm(phrase, warning):
    log(warning)
    if not sys.stdin.isatty():
        raise Failure("This operation requires an interactive terminal; no --yes bypass exists.")
    answer = input(f"Type exactly '{phrase}': ").strip()
    if answer != phrase:
        raise Failure("Confirmation did not match; nothing was changed.")


class Controller:
    def __init__(self, root, *, home=None, control_dir=None, config_dir=None):
        self.root = Path(root).resolve()
        self.home = Path(home or os.environ.get("PF_HOME") or self.root.parent).resolve()
        self.control_dir = Path(control_dir or os.environ.get("PF_CONTROL_DIR") or self.home / "control").resolve()
        self.config_dir = Path(config_dir or os.environ.get("PF_CONFIG_DIR") or self.home / "config").resolve()
        self.admin_dir = self.control_dir  # Compatibility name used by a few helpers.

        if not self.control_dir.is_dir():
            raise Failure(
                "Missing installed root-owned control directory: " + str(self.control_dir)
                + ". Run deploy/synology/install-control.sh from the repository first."
            )

        self.config = dict(DEFAULTS)
        config = self.config_dir / "pf-config.json"
        example = self.control_dir / "pf-config.example.json"

        # Bootstrap the writable host configuration from the root-owned template.
        # The default DSM `users` group can edit config without modifying the
        # executable control plane.
        try:
            bootstrap_gid = grp.getgrnam(DEFAULTS["workspace_write_group"]).gr_gid
        except KeyError as exc:
            raise Failure(
                f"Workspace group '{DEFAULTS['workspace_write_group']}' does not exist on this host."
            ) from exc
        self.config_dir.mkdir(mode=0o2770, parents=True, exist_ok=True)
        os.chown(self.config_dir, -1, bootstrap_gid)
        os.chmod(self.config_dir, 0o2770)

        if not config.exists():
            if not example.is_file():
                raise Failure("Missing control/pf-config.example.json; cannot initialize local admin configuration.")
            supplied = load_json(example)
            unknown = set(supplied) - set(DEFAULTS)
            if unknown:
                raise Failure("Unknown configuration keys in pf-config.example.json: " + ", ".join(sorted(unknown)))
            initial = dict(DEFAULTS)
            initial.update(supplied)
            write_json(config, initial)
            os.chown(config, -1, bootstrap_gid)
            os.chmod(config, 0o660)
            log("Created writable host configuration from control/pf-config.example.json: " + str(config))

        supplied = load_json(config)
        unknown = set(supplied) - set(DEFAULTS)
        if unknown:
            raise Failure("Unknown configuration keys: " + ", ".join(sorted(unknown)))
        self.config.update(supplied)
        if self.config["repository"] != "CDSemi/part-flow":
            raise Failure("This controller is scoped to CDSemi/part-flow.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", self.config["project"]):
            raise Failure("Invalid Compose project name.")
        if self.config["release_channel"] not in ("stable", "prerelease"):
            raise Failure("release_channel must be stable or prerelease.")
        if type(self.config["auto_update"]) is not bool:
            raise Failure("auto_update must be a JSON boolean.")
        for name in ("health_timeout_seconds", "minimum_free_mb"):
            if type(self.config[name]) is not int or self.config[name] <= 0:
                raise Failure(f"{name} must be a positive integer.")
        for name in ("backup_read_group", "workspace_write_group"):
            if not isinstance(self.config[name], str) or not self.config[name].strip():
                raise Failure(f"{name} must be a non-empty DSM group name.")
        try:
            self.backup_gid = grp.getgrnam(self.config["backup_read_group"]).gr_gid
            self.workspace_gid = grp.getgrnam(self.config["workspace_write_group"]).gr_gid
        except KeyError as exc:
            raise Failure(
                "Configured DSM group does not exist. Check backup_read_group and workspace_write_group in "
                + str(config)
            ) from exc

        self.state = self.home / (".pf-state-" + self.config["project"])
        self.backups_root = self.home / "backups"
        self.revisions_root = self.backups_root / "revisions"
        self.backups_dir = self.revisions_root / self.config["project"]
        self.recovery_root = self.home / "recovery" / self.config["project"]
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backups_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        self.recovery_root.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chmod(self.state, 0o700)
        for directory in (self.backups_root, self.revisions_root, self.backups_dir, self.recovery_root.parent, self.recovery_root):
            os.chown(directory, -1, self.backup_gid)
            os.chmod(directory, 0o750)
        self.publish_backup_permissions(self.backups_dir)
        self.publish_backup_permissions(self.recovery_root)
        self.publish_config_permissions()
        self.pending = self.state / "pending.json"
        self.override = self.state / "active-images.yaml"
        self.cli = None

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

    def publish_config_permissions(self):
        """Allow the configured workspace group to manage host configuration."""
        self.config_dir.mkdir(mode=0o2770, parents=True, exist_ok=True)
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
        if (self.root / ".git").is_dir():
            try:
                self.command(["git", "-c", f"safe.directory={self.root}",
                              "config", "core.sharedRepository", "group"], cwd=self.root)
            except Failure as exc:
                log("WARNING: Could not set Git core.sharedRepository=group: " + str(exc))

    def assert_control_plane_secure(self):
        """Refuse a writable executable control plane when running as root."""
        required = (
            self.control_dir,
            self.control_dir / "pf.sh",
            self.control_dir / "pf-admin.py",
            self.control_dir / "compose.nas.yaml",
        )
        for path in required:
            if not path.exists():
                raise Failure("Installed control-plane file is missing: " + str(path))
            info = path.stat()
            if info.st_uid != 0:
                raise Failure("Installed control-plane path is not owned by root: " + str(path))
            if info.st_mode & 0o022:
                raise Failure("Installed control-plane path is group/world writable: " + str(path))

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
                output = self.command(command)
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
        while True:
            answer = prompt_value("NAS LAN IPv4 (enter an address or detected number)", default)
            if answer.isdigit() and addresses and 1 <= int(answer) <= len(addresses):
                return addresses[int(answer) - 1]
            try:
                return validate_ipv4(answer, allow_loopback=False)
            except Failure as exc:
                log("Invalid value: " + str(exc))

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
        sample_path = self.control_dir / "nas.env.example"

        if env_path.exists():
            values = self.validate_deploy_env(read_dotenv(env_path), require_strong_password=True)
            log("Existing .env found; POSTGRES_PASSWORD will not be displayed or changed.")
            log(self.environment_summary(values))
            if not prompt_yes_no("Reuse this existing .env for the new deployment", default=True):
                raise Failure("Existing .env was left unchanged. Move or edit it explicitly, then rerun deploy.")
            os.chown(env_path, -1, self.workspace_gid)
            os.chmod(env_path, 0o660)
            return values

        if not sample_path.is_file():
            raise Failure("Missing installed control/nas.env.example; cannot initialize config/.env.")
        sample = read_dotenv(sample_path)
        missing = [key for key in REQUIRED_NAS_ENV_KEYS if key not in sample]
        if missing:
            raise Failure("NAS environment sample is missing required fields: " + ", ".join(missing))

        log("Configure the new PartFlow staging environment. Press Enter to accept a shown default.")
        postgres_user = prompt_value("PostgreSQL user", sample.get("POSTGRES_USER") or "partflow_staging", validate_identifier)
        postgres_db = prompt_value("PostgreSQL database", sample.get("POSTGRES_DB") or "partflow_staging", validate_identifier)
        timezone = prompt_value("Factory IANA timezone", sample.get("SITE_TIMEZONE") or "UTC", validate_timezone_name)

        log("Access mode:")
        log("  1. Direct LAN access to the NAS IP (recommended for initial staging verification)")
        log("  2. DSM Reverse Proxy; bind PartFlow to 127.0.0.1")
        mode = prompt_value("Select access mode", "1", validate_access_mode)
        if mode == "1":
            bind_ip = self.choose_lan_ipv4()
            allowed_host = "localhost"
        else:
            bind_ip = "127.0.0.1"
            default_host = sample.get("PARTFLOW_ALLOWED_HOST")
            if default_host == "localhost":
                default_host = None
            allowed_host = prompt_value("Exact internal Reverse Proxy hostname", default_host, validate_allowed_host)

        port = prompt_value("PartFlow HTTP port", sample.get("PARTFLOW_HTTP_PORT") or "5173", validate_http_port)
        values = {
            "POSTGRES_USER": postgres_user,
            "POSTGRES_PASSWORD": secrets.token_hex(32),
            "POSTGRES_DB": postgres_db,
            "SITE_TIMEZONE": timezone,
            "PARTFLOW_BIND_IP": bind_ip,
            "PARTFLOW_HTTP_PORT": port,
            "PARTFLOW_ALLOWED_HOST": allowed_host,
        }
        self.validate_deploy_env(values, require_strong_password=True)
        log("A 64-character cryptographically random hexadecimal POSTGRES_PASSWORD was generated and will not be printed.")
        log(self.environment_summary(values))
        if not prompt_yes_no("Write .env with these settings and continue", default=True):
            raise Failure("Cancelled before .env was created.")

        rendered = render_env_template(sample_path, values)
        temporary = env_path.with_name(".env.tmp-" + uuid.uuid4().hex[:8])
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.chown(temporary, -1, self.workspace_gid)
            os.chmod(temporary, 0o660)
            os.replace(temporary, env_path)
            os.chown(env_path, -1, self.workspace_gid)
            os.chmod(env_path, 0o660)
        finally:
            if temporary.exists():
                temporary.unlink()
        log("Created " + str(env_path) + " with group-write access for " + self.config["workspace_write_group"] + ". It remains outside the repository.")
        return values

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

    def project_resources(self):
        project = self.config["project"]
        containers = self.docker("ps", "-a", "-q", "--filter", "label=com.docker.compose.project=" + project).splitlines()
        volumes = self.docker("volume", "ls", "--format", "{{.Name}}").splitlines()
        prefix = project + "_"
        project_volumes = [name for name in volumes if name == prefix + "postgres_data" or name.startswith(prefix)]
        return {"containers": [value for value in containers if value], "volumes": project_volumes}

    def assert_new_deployment(self):
        if (self.state / "deployed.json").exists():
            raise Failure("This project already has a managed deployment record. Use update, not deploy.")
        resources = self.project_resources()
        if resources["containers"] or resources["volumes"]:
            raise Failure(
                "Existing Docker resources were found for project " + self.config["project"]
                + ". New deploy refuses to adopt or overwrite them; use status/update or inspect the leftover resources first."
            )

    def workspace_status(self, root=None):
        root = Path(root or self.root).resolve()
        if not (root / ".git").is_dir():
            return {"head": None, "dirty": True, "changes": ["<non-git-workspace>"]}
        head = self.command(["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"], cwd=root)
        if not SHA_RE.fullmatch(head):
            raise Failure("Git returned an invalid workspace SHA.")
        changed = self.command(["git", "-c", f"safe.directory={root}", "diff", "HEAD", "--name-only"], cwd=root).splitlines()
        untracked = self.command(["git", "-c", f"safe.directory={root}", "ls-files", "--others", "--exclude-standard"], cwd=root).splitlines()
        changes = [item for item in changed + untracked if item]
        return {"head": head, "dirty": bool(changes), "changes": changes}

    def current_target(self):
        status = self.workspace_status()
        if status["head"] is None:
            raise Failure("deploy --current requires a Git checkout. Use --latest, --commit, or --release otherwise.")
        if status["dirty"]:
            raise Failure(
                "deploy --current requires a clean working tree so the deployed revision is exact. "
                "Commit/revert the workspace first. Changed paths: " + ", ".join(status["changes"][:10])
            )
        return {"sha": status["head"], "ref": "current-checkout", "release_id": None, "published_at": None, "prerelease": None}

    def create_deployed_source_archive(self, destination, revision):
        """Archive the exact deployed Git revision, independent of workspace edits."""
        destination = Path(destination)
        if (self.root / ".git").is_dir():
            try:
                self.command(["git", "-c", f"safe.directory={self.root}", "cat-file", "-e", revision + "^{commit}"], cwd=self.root)
                raw = destination.with_name(destination.name + ".tar")
                try:
                    self.command(["git", "-c", f"safe.directory={self.root}", "archive", "--format=tar", "-o", raw, revision], cwd=self.root)
                    with raw.open("rb") as source, gzip.open(destination, "wb") as output:
                        shutil.copyfileobj(source, output)
                finally:
                    if raw.exists():
                        raw.unlink()
                with tarfile.open(destination, "r:gz") as archive:
                    archive.getmembers()
                return
            except Failure:
                pass
        status = self.workspace_status()
        if status["head"] == revision and not status["dirty"]:
            create_source_archive(self.root, destination)
            return
        raise Failure(
            "Cannot reconstruct the exact deployed source revision from the local Git repository. "
            "No destructive operation will continue until the deployed source can be archived."
        )

    def command(self, argv, *, cwd=None, output=None, input_file=None, text=None, env=None, clean_env_keys=()):
        child_env = os.environ.copy()
        for name in clean_env_keys:
            child_env.pop(name, None)
        child_env["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            child_env.update(env)
        try:
            result = subprocess.run(
                [str(v) for v in argv], cwd=cwd or self.root, env=child_env,
                stdin=input_file, input=text.encode() if text is not None else None,
                stdout=output if output is not None else subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
        except OSError as exc:
            raise Failure(f"Cannot execute {argv[0]}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace")[-5000:].strip()
            # Command arguments can include SQL/paths. Never print environment secrets.
            raise Failure(f"{argv[0]} failed (exit {result.returncode}).\n{detail}")
        return result.stdout.decode("utf-8", "replace").strip() if result.stdout is not None else ""

    def docker(self, *args, **kwargs):
        return self.command(["docker", *args], **kwargs)

    def compose(self, *args, root=None, override=None, **kwargs):
        root = Path(root or self.root).resolve()
        if args and args[0] == "run":
            args = ("run", "--label", "partflow.admin.project=" + self.config["project"], *args[1:])
        if self.cli is None:
            try:
                self.docker("compose", "version")
                self.cli = ["docker", "compose"]
            except Failure:
                self.command(["docker-compose", "version"])
                self.cli = ["docker-compose"]

        compose_file = self.control_dir / "compose.nas.yaml"
        env_path = self.config_dir / ".env"
        if not compose_file.is_file():
            raise Failure("Missing installed control/compose.nas.yaml.")
        if not env_path.is_file():
            raise Failure("Missing config/.env. Run deploy to create it or restore the host configuration.")

        command = self.cli + [
            "--project-directory", str(root),
            "--env-file", str(env_path),
            "-p", self.config["project"],
            "-f", str(compose_file),
        ]
        selected = Path(override) if override else self.override
        if selected.exists():
            command += ["-f", str(selected)]

        # The env file lives outside the writable repository. Compose receives it
        # explicitly, and build contexts receive the exact source root separately.
        base_env = read_dotenv(env_path)
        explicit_env = kwargs.pop("env", None) or {}
        child_env = dict(base_env)
        child_env["PARTFLOW_REPO_ROOT"] = str(root)
        child_env.update(explicit_env)
        clean = set(base_env) | {"PARTFLOW_REPO_ROOT"}
        return self.command(
            command + list(args), cwd=root, clean_env_keys=clean, env=child_env, **kwargs
        )

    @contextlib.contextmanager
    def lock(self, allow_pending=False):
        with (self.state / "operation.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Failure("Another PartFlow operation is running; try again after it finishes.") from exc
            if self.pending.exists() and not allow_pending:
                raise Failure("A previous operation is incomplete. Run status, then resume or rollback; automation is blocked.")
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def staging(self):
        if self.config["environment"] != "staging":
            raise Failure("Mutating lifecycle commands support staging only. This is not a production deployment package.")

    def env(self):
        path = self.config_dir / ".env"
        if not path.is_file() or not (self.control_dir / "compose.nas.yaml").is_file():
            raise Failure("Missing config/.env or installed control/compose.nas.yaml.")
        result = read_dotenv(path)
        for key in ("POSTGRES_DB", "POSTGRES_USER"):
            quote_identifier(result.get(key, ""))
        if result["POSTGRES_DB"] in ("postgres", "template0", "template1"):
            raise Failure("The application cannot use a PostgreSQL maintenance/template database.")
        return result

    def revision(self, root=None):
        root = Path(root or self.root).resolve()
        if root != self.root:
            if (root / ".git").is_dir():
                revision = self.command(["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"], cwd=root)
                if revision and SHA_RE.fullmatch(revision):
                    return revision
            marker = root / "DEPLOYED_SOURCE.txt"
            legacy = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
            if SHA_RE.fullmatch(legacy):
                return legacy
            raise Failure("Cannot determine source revision for the selected source tree.")

        deployed = self.state / "deployed.json"
        if deployed.is_file():
            value = load_json(deployed).get("sha", "")
            if SHA_RE.fullmatch(value):
                return value

        # v2.4 migration compatibility: accept the old marker until the first
        # v2.5 managed operation writes deployed.json in external state.
        legacy_marker = self.root / "DEPLOYED_SOURCE.txt"
        legacy = legacy_marker.read_text(encoding="utf-8").strip() if legacy_marker.is_file() else ""
        if SHA_RE.fullmatch(legacy):
            return legacy

        status = self.workspace_status()
        if status["head"] and not status["dirty"]:
            return status["head"]
        raise Failure("No verified deployed source revision is recorded in external state.")

    def sql(self, database, sql):
        quote_identifier(database)
        return self.compose("exec", "-T", "db", "sh", "-c",
            'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -At -c "$2"',
            "pf", database, sql)

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
        lines = ["services:"]
        for service in ("backend", "frontend"):
            reference = images[service]["reference"] if isinstance(images[service], dict) else images[service]
            if not IMAGE_RE.fullmatch(reference):
                raise Failure("Invalid retained image reference.")
            lines += [f"  {service}:", f"    image: {json.dumps(reference)}"]
        temporary = Path(path).with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, path)

    def retain_images(self, backup_id):
        images = {}
        for service in ("backend", "frontend"):
            image_id = self.inspect(service)["Image"]
            reference = f"{self.config['project']}-{service}:backup-{backup_id.lower()}"
            self.docker("tag", image_id, reference)
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
        if shutil.disk_usage(self.home).free < self.config["minimum_free_mb"] * 1024 * 1024:
            raise Failure("Insufficient free space on the source/backup volume.")

    def create_database(self, name):
        quote_identifier(name)
        self.compose("exec", "-T", "db", "sh", "-c",
            'exec createdb -U "$POSTGRES_USER" --owner="$POSTGRES_USER" --template=template0 "$1"', "pf", name)

    def restore_into(self, database, dump):
        self.create_database(database)
        with Path(dump).open("rb") as stream:
            self.compose("exec", "-T", "db", "sh", "-c",
                'exec pg_restore -U "$POSTGRES_USER" -d "$1" --exit-on-error --no-owner --no-privileges',
                "pf", database, input_file=stream)

    def snapshot(self, reason, source_verified=True):
        self.free_space()
        try:
            revision = self.revision()
        except Failure:
            if source_verified:
                raise
            revision = "0" * 40
        backup_id = f"{utc()}-{revision[:12]}-{uuid.uuid4().hex[:6]}"
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
            self.compose("exec", "-T", "db", "sh", "-c",
                'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges',
                output=stream)
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
        self.compose("exec", "-T", "db", "sh", "-c",
            'exec dropdb -U "$POSTGRES_USER" "$1"', "pf", verification_db)
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

    def snapshots(self):
        result = []
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
        if not sys.stdin.isatty():
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
        if self.pending.exists():
            try:
                jobs = self.docker("ps", "-q", "--filter", "label=partflow.admin.project=" + self.config["project"]).splitlines()
                for job in jobs:
                    self.docker("stop", "--time", "30", job)
                self.compose("stop", "frontend", "backend")
            except Failure as exc:
                log("WARNING: Could not confirm application shutdown: " + str(exc))
            log("Operation incomplete. Application services are intentionally stopped.")
            log("Inspect 'pf.sh status'. Automation and Compose writes remain blocked.")

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
        response = self.compose("exec", "-T", "frontend", "wget", "-q", "-O", "-", "http://localhost:5173/api/health")
        data = json.loads(response)
        if data.get("status") != "ok" or data.get("database") != "connected":
            raise Failure("Frontend/API/database health check failed.")
        self.phase("health-checked")
        log("Application health checks passed. Perform the UI/workflow and network-access smoke tests separately.")

    def replace_source(self, candidate, revision):
        self.phase("changing-source")
        # v2.5 keeps all runtime control/configuration outside repo/. The writable
        # repository can therefore be replaced as one application working tree.
        for item in list(self.root.iterdir()):
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in Path(candidate).iterdir():
            destination = self.root / item.name
            if item.is_dir() and not item.is_symlink():
                shutil.copytree(item, destination, symlinks=False)
            elif item.is_file() and not item.is_symlink():
                shutil.copy2(item, destination)
            else:
                raise Failure("Downloaded source contains an unsupported link/special file.")
        self.publish_workspace_permissions()
        self.phase("source-replaced")

    def github(self, path, missing=False):
        request = urllib.request.Request("https://api.github.com/repos/" + self.config["repository"] + "/" + path,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PartFlow-NAS-Admin/" + VERSION,
                     "X-GitHub-Api-Version": "2022-11-28"})
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            request.add_header("Authorization", "Bearer " + token)
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

    def clone(self, target, destination):
        self.command(["git", "clone", "--no-checkout", "--", "https://github.com/" + self.config["repository"] + ".git", str(destination)], cwd=self.root.parent)
        self.command(["git", "checkout", "--detach", target["sha"]], cwd=destination)
        actual = self.command(["git", "rev-parse", "HEAD"], cwd=destination)
        if actual != target["sha"]:
            raise Failure("Cloned checkout does not match the pinned GitHub SHA.")
        for item in destination.rglob("*"):
            if ".git" not in item.relative_to(destination).parts and item.is_symlink():
                raise Failure("Downloaded source has a symbolic link; manual review is required.")
        self.compose("config", "-q", root=destination)

    def automatic_guard(self, current, candidate):
        if not (self.root / ".git").is_dir():
            raise Deferred("Automatic updates require a Git checkout established by one successful manual update first.")
        workspace = self.workspace_status()
        if workspace["head"] != current or workspace["dirty"]:
            raise Deferred(
                "Automatic update requires the writable repository to match the deployed SHA exactly. "
                "Use a manual update so workspace changes can be archived and reviewed."
            )
        try:
            self.command(["git", "merge-base", "--is-ancestor", current, "HEAD"], cwd=candidate)
        except Failure as exc:
            raise Deferred("The selected release is not a descendant of the deployed SHA; automatic downgrade/divergence is refused.") from exc
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
        self.command(["git", "--version"])
        self.docker("version", "--format", "{{.Server.Version}}")
        self.free_space()
        values = self.prepare_new_env()
        self.ensure_listener_available(values)
        self.compose("config", "-q")

        if use_current:
            if target is not None:
                raise Failure("Internal deploy source selection conflict.")
            target = self.current_target()
        elif target is None:
            raise Failure("A resolved deployment target is required.")

        if skip_ci:
            log("WARNING: CI verification explicitly bypassed for this manual staging deployment.")
        else:
            self.require_ci(target["sha"])

        with contextlib.ExitStack() as stack:
            if use_current:
                source = self.root
            else:
                folder = stack.enter_context(tempfile.TemporaryDirectory(prefix="initial-candidate-", dir=self.state))
                source = Path(folder) / "repo"
                self.clone(target, source)

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
            "starting-database", "migrating-database", "activating",
        }
        if pending.get("phase") not in safe_phases:
            raise Failure(
                "Frontend access may already have opened. Automatic first-deploy cleanup is refused; preserve the database and review recovery manually."
            )
        database = pending.get("database") or self.env()["POSTGRES_DB"]
        confirm(
            "ABORT DEPLOY " + self.config["project"],
            f"Delete containers and Docker volumes created by the incomplete first deployment for database {database}. "
            "The source checkout and .env are kept so deployment can be retried. This is allowed only before frontend access opened.",
        )
        self.compose("down", "--volumes", "--remove-orphans")
        if self.override.exists():
            self.override.unlink()
        self.pending.unlink()
        log("Incomplete first deployment resources were removed. Source and .env were kept; rerun deploy when ready.")

    def detailed_project_resources(self):
        """Return only Docker resources owned by this Compose project."""
        project = self.config["project"]
        containers = [value for value in self.docker(
            "ps", "-a", "-q", "--filter", "label=com.docker.compose.project=" + project
        ).splitlines() if value]

        volumes = set(value for value in self.docker(
            "volume", "ls", "--format", "{{.Name}}", "--filter", "label=com.docker.compose.project=" + project
        ).splitlines() if value)
        # Older Compose releases did not consistently label every volume. The
        # project-name prefix is safe here because the exact project is already
        # selected and confirmed interactively.
        for value in self.docker("volume", "ls", "--format", "{{.Name}}").splitlines():
            if value.startswith(project + "_"):
                volumes.add(value)

        networks = set(value for value in self.docker(
            "network", "ls", "--format", "{{.Name}}", "--filter", "label=com.docker.compose.project=" + project
        ).splitlines() if value)
        for value in self.docker("network", "ls", "--format", "{{.Name}}").splitlines():
            if value.startswith(project + "_"):
                networks.add(value)

        image_refs = []
        for value in self.docker("image", "ls", "--format", "{{.Repository}}:{{.Tag}}").splitlines():
            if value.startswith(project + "-backend:") or value.startswith(project + "-frontend:"):
                image_refs.append(value)

        return {
            "containers": containers,
            "volumes": sorted(volumes),
            "networks": sorted(networks),
            "images": sorted(set(image_refs)),
        }

    def discover_instances(self):
        """Discover PartFlow Compose projects using the external-control layout."""
        groups = {}
        ids = [value for value in self.docker("ps", "-a", "-q").splitlines() if value]
        if ids:
            infos = json.loads(self.docker("inspect", *ids))
            for info in infos:
                labels = (info.get("Config", {}).get("Labels") or {})
                project = labels.get("com.docker.compose.project")
                service = labels.get("com.docker.compose.service")
                working = labels.get("com.docker.compose.project.working_dir")
                if not project or service not in ("db", "backend", "frontend") or not working:
                    continue
                root = Path(working).resolve()
                home = root.parent
                config_path = home / "config" / "pf-config.json"
                control_path = home / "control" / "pf-admin.py"
                if not config_path.is_file() or not control_path.is_file():
                    continue
                try:
                    cfg = load_json(config_path)
                except (OSError, ValueError):
                    continue
                if cfg.get("repository") != "CDSemi/part-flow" or cfg.get("project") != project:
                    continue
                key = (project, str(root))
                item = groups.setdefault(key, {
                    "project": project, "root": str(root), "home": str(home),
                    "services": set(), "running": set(),
                })
                item["services"].add(service)
                if info.get("State", {}).get("Running"):
                    item["running"].add(service)

        local_key = (self.config["project"], str(self.root))
        groups.setdefault(local_key, {
            "project": self.config["project"], "root": str(self.root),
            "home": str(self.home), "services": set(), "running": set(),
        })

        results = []
        for item in groups.values():
            home = Path(item["home"])
            env = {}
            try:
                env_path = home / "config" / ".env"
                if env_path.is_file():
                    env = read_dotenv(env_path)
            except (OSError, Failure):
                pass
            revision = "unknown"
            try:
                deployed = home / (".pf-state-" + item["project"]) / "deployed.json"
                if deployed.is_file():
                    candidate = load_json(deployed).get("sha", "")
                    if SHA_RE.fullmatch(candidate):
                        revision = candidate
            except (OSError, ValueError):
                pass
            results.append({
                **item,
                "services": sorted(item["services"]),
                "running": sorted(item["running"]),
                "database": env.get("POSTGRES_DB", "unknown"),
                "database_user": env.get("POSTGRES_USER", "unknown"),
                "revision": revision,
            })
        return sorted(results, key=lambda item: (item["project"], item["root"]))

    def display_instances(self, items, page=1):
        selected, pages, start = page_items(items, page)
        log(f"Managed PartFlow instances | page {page}/{pages} | {len(items)} total")
        for number, item in enumerate(selected, start + 1):
            running = ",".join(item["running"]) or "none"
            services = ",".join(item["services"]) or "filesystem-only"
            log(
                f"{number:>3}. {item['project']}  DB={item['database']}  "
                f"running={running}  services={services}\n"
                f"     root={item['root']}  revision={item['revision'][:12] if item['revision'] != 'unknown' else 'unknown'}"
            )
        return pages

    def choose_instance(self, requested=None):
        items = self.discover_instances()
        if requested:
            matches = [item for item in items if item["project"] == requested]
            if len(matches) != 1:
                raise Failure("--project must identify exactly one managed PartFlow instance.")
            return matches[0]
        if len(items) == 1:
            return items[0]
        if not sys.stdin.isatty():
            raise Failure("Multiple managed PartFlow instances exist. Pass --project explicitly.")
        page = 1
        while True:
            pages = self.display_instances(items, page)
            answer = input("Choose an instance number, n=next, p=previous, q=cancel: ").strip().lower()
            if answer == "q":
                raise Failure("Cancelled.")
            if answer == "n":
                page = min(pages, page + 1)
            elif answer == "p":
                page = max(1, page - 1)
            elif answer.isdigit() and 1 <= int(answer) <= len(items):
                return items[int(answer) - 1]

    def instance_summary(self):
        resources = self.detailed_project_resources()
        values = read_dotenv(self.config_dir / ".env") if (self.config_dir / ".env").is_file() else {}
        revision = "unknown"
        try:
            revision = self.revision()
        except Failure:
            pass
        return {
            "project": self.config["project"],
            "root": str(self.root),
            "database": values.get("POSTGRES_DB", "unknown"),
            "database_user": values.get("POSTGRES_USER", "unknown"),
            "revision": revision,
            "containers": resources["containers"],
            "volumes": resources["volumes"],
            "networks": resources["networks"],
            "images": resources["images"],
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
        log("  PartFlow image tags: " + str(len(summary["images"])))
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
            self.compose(
                "exec", "-T", "db", "sh", "-c",
                'exec pg_dump -U "$POSTGRES_USER" -d "$1" --format=custom --no-owner --no-privileges',
                "pf", database, output=output,
            )
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

    def create_purge_recovery(self):
        """Create a verified recovery bundle before destructive project purge.

        The active database/source and current images are mandatory. Historical
        rollback image tags are included when still present. All non-template
        databases in this project's dedicated PostgreSQL container are preserved.
        PostgreSQL globals are archived for manual recovery but are not executed
        automatically during restore.
        """
        self.database_ready()
        checkpoint = self.snapshot("before-purge")
        recovery_id = f"purge-{utc()}-{checkpoint['source_revision'][:12]}-{uuid.uuid4().hex[:6]}"
        folder = self.recovery_root / recovery_id
        folder.mkdir(mode=0o700, parents=True)
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

        env_source = self.config_dir / ".env"
        if not env_source.is_file():
            raise Failure("config/.env disappeared while creating recovery; purge is refused.")
        shutil.copy2(env_source, saved_config_dir / ".env")
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
                    self.sql("postgres", f"ALTER DATABASE {quote_identifier(name)} ALLOW_CONNECTIONS true;")
                    changed_connections = True
                self.dump_database(name, dump_path)
                verify_name = "pf_verify_" + uuid.uuid4().hex[:20]
                self.restore_into(verify_name, dump_path)
                self.compose("exec", "-T", "db", "sh", "-c", 'exec dropdb -U "$POSTGRES_USER" "$1"', "pf", verify_name)
            finally:
                if changed_connections:
                    self.sql("postgres", f"ALTER DATABASE {quote_identifier(name)} ALLOW_CONNECTIONS false;")
            record["dump"] = "databases/" + dump_name
            record["heads"] = self.db_heads(name) if item["allow_connections"] else []
            databases.append(record)

        globals_path = folder / "postgres-globals.sql"
        with globals_path.open("wb") as output:
            self.compose(
                "exec", "-T", "db", "sh", "-c",
                'exec pg_dumpall -U "$POSTGRES_USER" -d postgres --globals-only',
                output=output,
            )
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
        self.command(["docker", "image", "save", "-o", images_path, *image_refs])
        if not images_path.stat().st_size:
            raise Failure("Docker image recovery archive is empty.")
        with tarfile.open(images_path, "r:") as archive:
            archive.getmembers()

        resources = self.detailed_project_resources()
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
            "home": str(self.home),
            "source_revision": checkpoint["source_revision"],
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
        return manifest

    def recoveries(self, project=None):
        base = self.home / "recovery"
        result = []
        if not base.is_dir():
            return result
        project_dirs = [base / project] if project else [item for item in base.iterdir() if item.is_dir()]
        for project_dir in project_dirs:
            if not project_dir.is_dir():
                continue
            for folder in project_dir.iterdir():
                if not folder.is_dir() or not RECOVERY_RE.fullmatch(folder.name):
                    continue
                try:
                    metadata = load_json(folder / "manifest.json")
                    metadata["_folder"] = str(folder)
                    result.append(metadata)
                except (OSError, ValueError):
                    result.append({"id": folder.name, "project": project_dir.name, "status": "invalid", "_folder": str(folder)})
        return sorted(result, key=lambda item: item["id"], reverse=True)

    def verify_recovery(self, item):
        folder = Path(item.get("_folder") or self.recovery_root / item["id"])
        if not folder.is_dir() or not RECOVERY_RE.fullmatch(folder.name):
            raise Failure("Invalid recovery bundle path.")
        manifest_path = folder / "manifest.json"
        if digest(manifest_path) != (folder / "manifest.sha256").read_text(encoding="utf-8").strip():
            raise Failure("Recovery manifest checksum mismatch.")
        metadata = load_json(manifest_path)
        if metadata.get("kind") != "partflow-purge-recovery" or metadata.get("status") != "complete":
            raise Failure("Recovery bundle is incomplete or unsupported.")
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
                f"source={item.get('source_revision', 'unknown')[:12]}"
            )
        return pages

    def choose_recovery(self, requested=None, project=None):
        items = self.recoveries(project=project)
        if not items:
            raise Failure("No purge recovery bundles were found.")
        if requested:
            matches = [item for item in items if item["id"] == requested]
            if len(matches) != 1:
                raise Failure("Specify one exact purge recovery ID.")
            return self.verify_recovery(matches[0])
        if not sys.stdin.isatty():
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

    def finish_purge_cleanup(self, recovery_id, *, delete_backups, reset_admin_config):
        resources = self.detailed_project_resources()
        # Delete in dependency order. State/.env are removed last so an
        # interrupted Docker cleanup remains diagnosable and resumable.
        for container in resources["containers"]:
            self.docker("rm", "-f", container)
        for network in resources["networks"]:
            self.docker("network", "rm", network)
        for volume in resources["volumes"]:
            self.docker("volume", "rm", volume)
        for image in resources["images"]:
            self.docker("image", "rm", image)

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
        log("Next: sudo pf deploy --latest")

    def purge(self, *, delete_backups=None, reset_admin_config=False):
        self.staging()

        # A power loss or Docker error after the final destructive confirmation
        # leaves a resumable journal until the last filesystem cleanup step.
        if self.pending.exists():
            pending = load_json(self.pending)
            if pending.get("operation") == "purge" and pending.get("phase") == "deleting" and pending.get("recovery"):
                recovery_id = pending["recovery"]
                matches = [item for item in self.recoveries(project=self.config["project"]) if item.get("id") == recovery_id]
                if len(matches) != 1:
                    raise Failure("Interrupted purge recovery bundle is missing or ambiguous; manual recovery is required.")
                self.verify_recovery(matches[0])
                confirm(
                    "RESUME PURGE " + self.config["project"] + " " + recovery_id,
                    "An earlier purge passed all confirmations and began deleting resources. Resume only the remaining cleanup using the already verified recovery bundle.",
                )
                self.finish_purge_cleanup(
                    recovery_id,
                    delete_backups=bool(pending.get("delete_backups")),
                    reset_admin_config=bool(pending.get("reset_admin_config")),
                )
                return
            raise Failure("An incomplete managed operation exists. Resolve it before purge so recovery state is unambiguous.")

        summary = self.instance_summary()
        self.log_instance_summary(summary)
        if not summary["containers"] and not summary["volumes"] and not summary["state_present"] and not summary["env_present"]:
            raise Failure("No active or residual deployment state was found for this project.")
        if not (self.config_dir / ".env").is_file():
            raise Failure("config/.env is missing. A database volume cannot be safely destroyed without first proving a recoverable database backup.")
        self.database_ready()
        self.ensure_local_contract()

        confirm(
            "PURGE " + self.config["project"],
            "This is a destructive staging teardown. The selected project's containers, volumes, networks, PartFlow image tags, runtime state, and config/.env are candidates for deletion. The writable repo and installed control plane are retained. A verified recovery bundle is created before any destructive Docker deletion.",
        )

        # Stop writes first so the recovery bundle is a stable point-in-time state.
        self.pause("purge", project=self.config["project"])
        recovery = None
        checkpoint = None
        try:
            recovery = self.create_purge_recovery()
            checkpoint = self.verify_snapshot(recovery["active_checkpoint"])
            self.phase("recovery-ready", recovery=recovery["id"], active_checkpoint=checkpoint["id"])
            log("Recovery summary:")
            log("  Bundle: " + recovery["id"])
            log("  Path: " + str(self.recovery_root / recovery["id"]))
            log("  Active database: " + recovery["database"])
            log("  Preserved databases: " + ", ".join(item["name"] for item in recovery["databases"]))
            log("  Saved Docker image tags: " + str(len(recovery["saved_image_refs"])))
            log("  Revision checkpoints archived: yes")

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
            self.phase(
                "deleting",
                recovery=recovery["id"],
                delete_backups=bool(delete_backups),
                reset_admin_config=bool(reset_admin_config),
            )
        except Exception:
            # No destructive deletion has happened yet. Reopen the exact current
            # application if the recovery checkpoint was created successfully.
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
            recovery["id"],
            delete_backups=bool(delete_backups),
            reset_admin_config=bool(reset_admin_config),
        )

    def replace_source_for_recovery(self, candidate, revision):
        """Restore the writable repository; executable control stays external/root-owned."""
        candidate = Path(candidate)
        for item in list(self.root.iterdir()):
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in candidate.iterdir():
            copy_tree_entry(item, self.root / item.name)
        # Runtime secrets/state no longer belong in the writable repository.
        for legacy in (self.root / ".env", self.root / "DEPLOYED_SOURCE.txt"):
            if legacy.exists():
                legacy.unlink()
        self.publish_workspace_permissions()

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
        temporary = Path(tempfile.mkdtemp(prefix="restore-checkpoints-", dir=self.home))
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
        resources = self.detailed_project_resources()
        if resources["containers"] or resources["volumes"]:
            raise Failure("Exact restore requires an empty target project. Purge the current instance first, or use --side-by-side to recover data without replacing it.")
        if (self.state / "deployed.json").exists():
            raise Failure("A managed deployment record already exists. Exact restore refuses to overwrite it.")

        log("Restore target summary:")
        log("  Project: " + recovery["project"])
        log("  Source: " + recovery["source_revision"])
        log("  Active database: " + recovery["database"])
        log("  Preserved databases: " + ", ".join(item["name"] for item in recovery["databases"]))
        log("  Recovery bundle: " + recovery["id"])
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
        with tempfile.TemporaryDirectory(prefix="restore-source-", dir=self.home) as temp:
            candidate = Path(temp)
            archive = folder / (recovery.get("workspace_archive") or "source.tar.gz")
            extract_source(archive, candidate)
            # v1 bundles stored .env inside source.tar.gz; v2 stores it separately.
            self.restore_runtime_environment(recovery, extracted_source=candidate)
            self.replace_source_for_recovery(candidate, recovery["source_revision"])

        self.phase("loading-images")
        self.command(["docker", "image", "load", "-i", folder / "images.tar"])
        self.verify_images(recovery["active_images"])
        self.make_override(recovery["active_images"], self.override)

        self.phase("starting-database")
        self.compose("up", "-d", "--no-deps", "db")
        self.wait_health("db")
        values = self.env()
        if values["POSTGRES_DB"] != recovery["database"] or values["POSTGRES_USER"] != recovery["database_user"]:
            raise Failure("Recovered .env database identity does not match the recovery manifest.")

        # Replace the empty init database with the verified logical dump.
        self.compose("exec", "-T", "db", "sh", "-c", 'exec dropdb -U "$POSTGRES_USER" "$1"', "pf", recovery["database"])
        self.restore_into(recovery["database"], folder / "databases/active.dump")
        if self.db_heads(recovery["database"]) != recovery["database_heads"]:
            raise Failure("Restored active database Alembic revision does not match the recovery bundle.")

        for record in recovery["databases"]:
            if record["name"] == recovery["database"]:
                continue
            self.restore_into(record["name"], folder / record["dump"])
            if not record["allow_connections"]:
                self.sql("postgres", f"ALTER DATABASE {quote_identifier(record['name'])} ALLOW_CONNECTIONS false;")

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
            self.clone(target, candidate)
            if automatic:
                self.automatic_guard(current, candidate)
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
                self.compose("exec", "-T", "db", "sh", "-c", 'exec dropdb -U "$POSTGRES_USER" "$1"', "pf", rehearsal)
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
        self.sql("postgres", database_swap_sql(current, prepared, retained))
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
            if migration_files(candidate) != selected["migration_files"]:
                raise Failure("Checkpoint migration fingerprint mismatch.")
            if not restore_database:
                if incomplete and not (previous_operation.get("operation") == "update"
                                       and previous_operation.get("migration_required") is False):
                    raise Failure("The incomplete operation may have changed data/schema; review recovery with --restore-db.")
                current_contract = self.ensure_local_contract()
                if current_contract["files"] != selected["migration_files"] or self.db_heads() != selected["database_heads"]:
                    raise Failure("Database/schema compatibility is not established. Code-only rollback refused; review --restore-db.")
            phrase = ("RESTORE " + self.env()["POSTGRES_DB"] + " " if restore_database else "ROLLBACK ") + selected["id"]
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
            self.replace_source(candidate, selected["source_revision"])
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
        self.assert_control_plane_secure()
        self.publish_workspace_permissions()
        self.publish_config_permissions()
        self.publish_backup_permissions(self.backups_dir)
        self.publish_backup_permissions(self.recovery_root)
        log("Permissions normalized.")
        log("  repo/: group=" + self.config["workspace_write_group"] + " full read/write/delete via directory group-write")
        log("  config/: group=" + self.config["workspace_write_group"] + " read/write")
        log("  backups/, recovery/: group=" + self.config["backup_read_group"] + " read/copy only")
        log("  control/: users read-only, root-owned/root-modifiable; .pf-state-*: root only")

    def doctor(self):
        log("PartFlow NAS Admin " + VERSION)
        self.assert_control_plane_secure()
        log("Python: " + sys.version.split()[0])
        log("Git: " + self.command(["git", "--version"]))
        log("Docker: " + self.docker("version", "--format", "{{.Server.Version}}"))
        self.env()
        self.compose("config", "-q")
        self.free_space()
        log("Control plane: root-owned and not group/world writable")
        log("Repository: " + str(self.root))
        log("Runtime config: " + str(self.config_dir))
        log("Compose config and source-volume free-space checks passed.")
        log("Project: " + self.config["project"] + " | environment: " + self.config["environment"])
        log("Auto-update: " + str(self.config["auto_update"]) + " | channel: " + self.config["release_channel"])
        log("Workspace write access: group=" + self.config["workspace_write_group"] + " repo/config writable")
        log("Backup SMB access: group=" + self.config["backup_read_group"] + " directories=0750 files=0640")
        log("Database volume capacity, NAS recovery, and production readiness are not certified by doctor.")

    def status(self):
        deployed = self.revision()
        workspace = self.workspace_status()
        log("Deployed source: " + deployed)
        log("Workspace HEAD: " + (workspace["head"] or "non-git"))
        log("Workspace differs from deployed: " + str(workspace["dirty"] or workspace["head"] != deployed))
        if workspace["changes"]:
            log("Workspace changes: " + ", ".join(workspace["changes"][:10]))
        log("Project: " + self.config["project"])
        log("Revision checkpoints: " + str(len(self.snapshots())))
        log("Database revisions: " + ", ".join(self.db_heads()))
        log(self.compose("ps"))
        if self.pending.exists():
            log("INCOMPLETE OPERATION:\n" + json.dumps(load_json(self.pending), indent=2))
        else:
            log("No incomplete managed operation.")

    def passthrough(self, args):
        if not args:
            args = ["ps"]
        if args[0].startswith("-"):
            raise Failure("Compose global overrides are not accepted; the project/file are fixed.")
        if args[0] in ("down", "rm") and any(v == "--volumes" or v.startswith("--volumes=")
                                                   or v.startswith("-") and not v.startswith("--") and "v" in v
                                                   for v in args[1:]):
            raise Failure("Volume deletion is blocked. Use reset-db or purge for backed-up destructive workflows.")
        read_only = args[0] in ("ps", "logs", "config", "version", "top", "images", "port")
        with contextlib.nullcontext() if read_only else self.lock():
            if self.cli is None:
                self.compose("version")
            env_path = self.config_dir / ".env"
            values = read_dotenv(env_path)
            child_env = os.environ.copy()
            for name in set(values) | {"PARTFLOW_REPO_ROOT"}:
                child_env.pop(name, None)
            child_env.update(values)
            child_env["PARTFLOW_REPO_ROOT"] = str(self.root)
            command = self.cli + [
                "--project-directory", str(self.root),
                "--env-file", str(env_path),
                "-p", self.config["project"],
                "-f", str(self.control_dir / "compose.nas.yaml"),
            ]
            if self.override.exists():
                command += ["-f", str(self.override)]
            result = subprocess.call(command + args, cwd=self.root, env=child_env)
            if result:
                raise Failure(f"Compose exited with status {result}.")


def parser():
    result = argparse.ArgumentParser(description="PartFlow NAS staging administration; use --help on a command.")
    subs = result.add_subparsers(dest="command", required=True)
    for name in ("doctor", "status", "permissions", "backup", "reset-db", "resume", "abort-deploy"):
        subs.add_parser(name)

    instances = subs.add_parser("instances", help="List managed PartFlow instances visible to this Docker daemon")
    instances.add_argument("--page", type=int, default=1)

    deploy = subs.add_parser("deploy", help="Create a brand-new managed staging deployment")
    deploy_selection = deploy.add_mutually_exclusive_group()
    deploy_selection.add_argument("--current", action="store_true", help="Deploy the current clean Git checkout (default when no selector is given)")
    deploy_selection.add_argument("--latest", action="store_true", help="Resolve the configured branch tip to a fixed SHA and deploy it")
    deploy_selection.add_argument("--commit", help="Deploy one explicit Git commit SHA")
    deploy_selection.add_argument("--release", help="Deploy a published release tag or 'latest'")
    deploy.add_argument("--channel", choices=("stable", "prerelease"))
    deploy.add_argument("--skip-ci", action="store_true", help="Explicit manual staging exception; CI is checked by default")

    purge = subs.add_parser("purge", help="Create a full recovery bundle, then remove one managed staging instance")
    purge.add_argument("--project", help="Select one exact managed Compose project; otherwise choose interactively when needed")
    backup_policy = purge.add_mutually_exclusive_group()
    backup_policy.add_argument("--delete-backups", action="store_true", help="Delete normal revision checkpoints after archiving them into the recovery bundle")
    backup_policy.add_argument("--keep-backups", action="store_true", help="Keep normal revision checkpoints after purge")
    purge.add_argument("--reset-admin-config", action="store_true", help="Also delete config/pf-config.json after a separate confirmation")

    recoveries = subs.add_parser("recoveries", help="List verified purge-recovery bundle candidates")
    recoveries.add_argument("--page", type=int, default=1)
    recoveries.add_argument("--project")

    restore = subs.add_parser("restore-instance", help="Restore a purged instance or recover its old database side-by-side")
    restore.add_argument("recovery_id", nargs="?")
    restore.add_argument("--project", help="Filter recovery selection by project")
    restore.add_argument("--side-by-side", action="store_true", help="Restore only the old active database under a separate recovery DB name; do not replace the current instance")

    backups = subs.add_parser("backups", help="List revision checkpoints, newest first, 10 per page")
    backups.add_argument("--page", type=int, default=1)
    rollback = subs.add_parser("rollback")
    rollback.add_argument("backup_id", nargs="?")
    rollback.add_argument("--restore-db", action="store_true")
    update = subs.add_parser("update")
    selection = update.add_mutually_exclusive_group()
    selection.add_argument("--latest", action="store_true", help="Resolve the current configured branch tip to a fixed SHA")
    selection.add_argument("--commit")
    selection.add_argument("--release", help="Published tag or 'latest' (default)")
    update.add_argument("--channel", choices=("stable", "prerelease"))
    update.add_argument("--allow-migrations", action="store_true")
    update.add_argument("--skip-ci", action="store_true", help="Explicit manual staging exception; never used by the scheduler")
    check = subs.add_parser("release-check")
    check.add_argument("--channel", choices=("stable", "prerelease"))
    check.add_argument("--apply", action="store_true")
    return result


def main(argv=None, root=None):
    if sys.version_info < (3, 9):
        print("Python 3.9 or newer is required.", file=sys.stderr)
        return 2
    os.umask(0o077)
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {
        "doctor", "status", "permissions", "backup", "reset-db", "resume", "deploy", "abort-deploy",
        "instances", "purge", "recoveries", "restore-instance",
        "backups", "rollback", "update", "release-check",
    }
    controller = None
    managed_started = False
    held_lock = contextlib.ExitStack()
    try:
        if root is None:
            source_dir = Path(__file__).resolve().parent
            if source_dir.name != "control":
                raise Failure(
                    "Refusing to execute the writable repository copy of pf-admin.py as the NAS control plane. "
                    "Run the installed root-owned launcher: sudo pf <command>."
                )
            default_home = Path(os.environ.get("PF_HOME", source_dir.parent))
            default_root = Path(os.environ.get("PF_REPO_ROOT", default_home / "repo"))
        else:
            default_root = Path(root)

        if not argv or argv[0] not in known and argv[0] not in ("-h", "--help"):
            controller = Controller(default_root)
            controller.assert_control_plane_secure()
            controller.passthrough(argv)
            return 0
        args = parser().parse_args(argv)
        controller = Controller(default_root)
        controller.assert_control_plane_secure()

        # Cross-instance selection commands acquire the selected instance lock
        # themselves. Holding the bootstrap instance lock here would deadlock
        # when it is also the selected target.
        cross_instance = args.command in ("instances", "purge", "recoveries", "restore-instance")
        if not cross_instance:
            held_lock.enter_context(controller.lock(allow_pending=args.command in (
                "doctor", "status", "backups", "rollback", "resume", "abort-deploy"
            )))

        if args.command == "doctor":
            controller.doctor()
        elif args.command == "permissions":
            controller.permissions()
        elif args.command == "status":
            controller.status()
        elif args.command == "instances":
            controller.display_instances(controller.discover_instances(), args.page)
        elif args.command == "deploy":
            use_current = args.current or not (args.latest or args.commit or args.release)
            target = None if use_current else controller.resolve(
                latest=args.latest, commit=args.commit, release=args.release, channel=args.channel
            )
            if not use_current and target is None:
                raise Failure("No published release exists for this channel. Select prerelease, --latest, or an explicit commit.")
            managed_started = True
            controller.deploy(target, use_current=use_current, skip_ci=args.skip_ci)
        elif args.command == "purge":
            selected = controller.choose_instance(args.project)
            target_controller = Controller(Path(selected["root"]), home=Path(selected["home"]))
            if target_controller.config["project"] != selected["project"]:
                raise Failure("Selected Docker project and its local pf-config.json disagree; purge refused.")
            delete_backups = True if args.delete_backups else False if args.keep_backups else None
            with target_controller.lock():
                target_controller.purge(
                    delete_backups=delete_backups,
                    reset_admin_config=args.reset_admin_config,
                )
        elif args.command == "recoveries":
            controller.display_recoveries(controller.recoveries(project=args.project), args.page)
        elif args.command == "restore-instance":
            recovery = controller.choose_recovery(args.recovery_id, project=args.project)
            target_root = controller.root if args.side_by_side else Path(recovery["root"])
            target_controller = Controller(target_root, home=Path(recovery.get("home") or target_root.parent))
            controller = target_controller
            with target_controller.lock():
                managed_started = not args.side_by_side
                target_controller.restore_instance(recovery, side_by_side=args.side_by_side)
                managed_started = False
        elif args.command == "backups":
            controller.display_page(controller.snapshots(), args.page)
        elif args.command == "backup":
            controller.database_ready()
            controller.ensure_local_contract()
            controller.snapshot("scheduled-or-manual-backup")
            log("Copy the entire checkpoint directory off-NAS. It contains production-like database data even though runtime .env is stored separately.")
        elif args.command == "reset-db":
            managed_started = True
            controller.reset_database()
        elif args.command == "abort-deploy":
            managed_started = True
            controller.abort_deploy()
        elif args.command == "rollback":
            managed_started = True
            controller.rollback(args.backup_id, args.restore_db)
        elif args.command == "resume":
            managed_started = True
            controller.resume()
        elif args.command == "update":
            target = controller.resolve(latest=args.latest, commit=args.commit, release=args.release, channel=args.channel)
            if target is None:
                raise Failure("No published release exists for this channel. Select prerelease or use --latest manually.")
            managed_started = True
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
            managed_started = True
            controller.update(target, automatic=True)
        return 0
    except (Failure, OSError, ValueError, KeyError, KeyboardInterrupt) as exc:
        print("ERROR: " + str(exc), file=sys.stderr, flush=True)
        if controller is not None and managed_started:
            controller.fail_closed()
        return 20 if isinstance(exc, Deferred) else 1
    finally:
        held_lock.close()


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
