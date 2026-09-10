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
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

VERSION = "2.2.0"
PAGE_SIZE = 10
DEFAULTS = {
    "repository": "CDSemi/part-flow", "branch": "main",
    "project": "partflow-staging", "environment": "staging",
    "release_channel": "stable", "auto_update": False,
    "ci_workflow": "ci.yml", "health_timeout_seconds": 180,
    "minimum_free_mb": 2048,
    # Revision checkpoints can contain database dumps and a source archive
    # that includes .env. Keep them unreadable to ordinary users while
    # allowing trusted DSM administrators to inspect/copy them over SMB.
    "backup_read_group": "administrators",
}
# These paths are deployment-local and survive application source updates/rollbacks.
# In particular, the running admin controller never self-updates mid-operation.
LOCAL_PATHS = (
    Path(".env"),
    Path("compose.nas.yaml"),
    Path("pf.sh"),
    Path("deploy/synology"),
    Path("DEPLOYED_SOURCE.txt"),
)
LEGACY_ADMIN_PATHS = (
    Path("pf-admin.py"),
    Path("backup.sh"),
    Path("release-check.sh"),
    Path("pf-config.json"),
    Path("pf-config.example.json"),
    Path("nas.env.example"),
    Path("PF_ADMIN_GUIDE.md"),
    Path("PF_ADMIN_GUIDE.vi.md"),
    Path("TEST_REPORT.md"),
    Path("pf-admin-tests"),
)
SOURCE_EXCLUDES = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}
AUTO_REVIEW_PATHS = (
    ".env.example", "compose.yaml", "backend/Dockerfile", "frontend/Dockerfile",
    "backend/.dockerignore", "frontend/.dockerignore", ".github/workflows/ci.yml",
)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BACKUP_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}-[0-9a-f]{6}\Z")
IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*:[a-zA-Z0-9_.-]+\Z")


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


def is_local_path(relative):
    relative = Path(relative)
    return any(relative == local or local in relative.parents for local in LOCAL_PATHS)


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


def copy_local_paths(root, destination):
    root, destination = Path(root), Path(destination)
    for relative in LOCAL_PATHS:
        source = root / relative
        if source.exists() or source.is_symlink():
            copy_tree_entry(source, destination / relative)


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
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.admin_dir = self.root / "deploy" / "synology"
        legacy = [str(path) for path in LEGACY_ADMIN_PATHS if (self.root / path).exists()]
        if legacy:
            raise Failure(
                "Legacy root-level NAS admin files detected: " + ", ".join(legacy)
                + ". Move pf-config.json to deploy/synology/ if needed, then remove the old duplicates."
            )
        self.config = dict(DEFAULTS)
        config = self.admin_dir / "pf-config.json"
        if config.exists():
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
        if not isinstance(self.config["backup_read_group"], str) or not self.config["backup_read_group"].strip():
            raise Failure("backup_read_group must be a non-empty DSM group name.")
        try:
            self.backup_gid = grp.getgrnam(self.config["backup_read_group"]).gr_gid
        except KeyError as exc:
            raise Failure(
                f"Backup read group '{self.config['backup_read_group']}' does not exist. "
                "Set backup_read_group in deploy/synology/pf-config.json to a trusted DSM group."
            ) from exc
        self.state = self.root.parent / (".pf-state-" + self.config["project"])
        self.backups_root = self.root.parent / "backups"
        self.revisions_root = self.backups_root / "revisions"
        self.backups_dir = self.revisions_root / self.config["project"]
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backups_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
        os.chmod(self.state, 0o700)
        for directory in (self.backups_root, self.revisions_root, self.backups_dir):
            os.chown(directory, -1, self.backup_gid)
            os.chmod(directory, 0o750)
        # Repair checkpoints created by older Admin v2.x versions as well as
        # applying the policy to future checkpoints.
        self.publish_backup_permissions(self.backups_dir)
        self.pending = self.state / "pending.json"
        self.override = self.state / "active-images.yaml"
        self.cli = None

    def publish_backup_permissions(self, root):
        """Make backup artifacts read-only to the configured trusted DSM group.

        The controller runs with a restrictive umask so state and temporary files
        stay private. Backups are the exception: administrators need to inspect
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
        root = Path(root or self.root)
        if args and args[0] == "run":
            args = ("run", "--label", "partflow.admin.project=" + self.config["project"], *args[1:])
        if self.cli is None:
            try:
                self.docker("compose", "version")
                self.cli = ["docker", "compose"]
            except Failure:
                self.command(["docker-compose", "version"])
                self.cli = ["docker-compose"]
        command = self.cli + ["-p", self.config["project"], "-f", str(root / "compose.nas.yaml")]
        selected = Path(override) if override else self.override
        if selected.exists():
            command += ["-f", str(selected)]
        # Shell exports must not silently redirect this deployment to another DB.
        # Let Compose parse its own .env, then apply only explicit rehearsal overrides.
        dotenv_keys = read_dotenv(root / ".env").keys() if (root / ".env").is_file() else ()
        return self.command(command + list(args), cwd=root, clean_env_keys=dotenv_keys, **kwargs)

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
        path = self.root / ".env"
        if not path.is_file() or not (self.root / "compose.nas.yaml").is_file():
            raise Failure("Missing repo-root .env or compose.nas.yaml.")
        result = read_dotenv(path)
        for key in ("POSTGRES_DB", "POSTGRES_USER"):
            quote_identifier(result.get(key, ""))
        if result["POSTGRES_DB"] in ("postgres", "template0", "template1"):
            raise Failure("The application cannot use a PostgreSQL maintenance/template database.")
        return result

    def revision(self, root=None):
        root = Path(root or self.root)
        marker = root / "DEPLOYED_SOURCE.txt"
        marked = marker.read_text().strip() if marker.exists() else None
        if (root / ".git").is_dir():
            revision = self.command(["git", "-c", f"safe.directory={root}",
                                     "rev-parse", "HEAD"], cwd=root)
            if marked and marked != revision:
                raise Failure("DEPLOYED_SOURCE.txt disagrees with Git HEAD; resolve the source identity first.")
        else:
            revision = marked
        if not revision or not SHA_RE.fullmatch(revision):
            raise Failure("Record the exact 40-character source SHA in DEPLOYED_SOURCE.txt first.")
        return revision

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
        override = self.state / "inspect-images.yaml"
        if images is None:
            images = self.retain_images(utc().lower() + "-inspect-" + uuid.uuid4().hex[:6])
        self.make_override(images, override)
        contract = self.image_contract(override=override)
        if migration_files(self.root) != contract["files"]:
            raise Failure("Local migration files differ from the deployed backend image; source identity is not verified.")
        if self.db_heads() != contract["heads"]:
            raise Failure("The live database is not at the deployed image's Alembic head.")
        return contract

    def free_space(self):
        if shutil.disk_usage(self.root.parent).free < self.config["minimum_free_mb"] * 1024 * 1024:
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
            revision = "0" * 40  # Emergency data preservation, never a source rollback target.
        backup_id = f"{utc()}-{revision[:12]}-{uuid.uuid4().hex[:6]}"
        folder = self.backups_dir / backup_id
        folder.mkdir(mode=0o700)
        log("Creating source + database checkpoint: " + backup_id)
        metadata = {
            "format": 1, "id": backup_id, "created_at": utc(), "reason": reason,
            "status": "incomplete", "source_revision": revision,
            "source_verified": source_verified, "project": self.config["project"],
            "repository": self.config["repository"], "environment": self.config["environment"],
            "database": self.env()["POSTGRES_DB"], "database_user": self.env()["POSTGRES_USER"],
            "postgres_major": self.database_ready(), "database_heads": self.db_heads(),
            "images": self.retain_images(backup_id),
        }
        write_json(folder / "manifest.json", metadata)
        if source_verified:
            try:
                self.ensure_local_contract(metadata["images"])
            except Failure:
                if reason != "before-rollback":
                    raise
                source_verified = False
                metadata["source_verified"] = False
                log("Current source/schema is unverified; preserving an emergency data-only recovery checkpoint.")
        source = folder / "source.tar.gz"
        create_source_archive(self.root, source)
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
        verification_db = "pf_verify_" + uuid.uuid4().hex[:20]
        log("Verifying the dump with a full restore into " + verification_db)
        self.restore_into(verification_db, dump)
        if self.db_heads(verification_db) != metadata["database_heads"]:
            raise Failure("Restored Alembic revisions do not match the snapshot. Verification database retained.")
        self.compose("exec", "-T", "db", "sh", "-c",
            'exec dropdb -U "$POSTGRES_USER" "$1"', "pf", verification_db)
        metadata.update({"status": "complete", "restore_test": "passed",
                         "migration_files": migration_files(self.root) if source_verified else {},
                         "checksums": {name: digest(folder / name) for name in
                                       ("source.tar.gz", "database.dump", "database.list")}})
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
        if metadata.get("status") != "complete" or metadata.get("format") != 1 or metadata.get("id") != backup_id:
            raise Failure("The selected checkpoint is incomplete or unsupported.")
        if metadata.get("project") != self.config["project"] or metadata.get("repository") != self.config["repository"]:
            raise Failure("Checkpoint belongs to a different deployment.")
        for name in ("source.tar.gz", "database.dump", "database.list"):
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
        # Preserve deployment-local tools/configuration while replacing application source.
        # This avoids changing the controller that is currently executing.
        with tempfile.TemporaryDirectory(prefix="local-tools-", dir=self.state) as folder:
            preserved = Path(folder)
            copy_local_paths(self.root, preserved)
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
            copy_local_paths(preserved, self.root)
        (self.root / "DEPLOYED_SOURCE.txt").write_text(revision + "\n")
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
        copy_local_paths(self.root, destination)
        (destination / "DEPLOYED_SOURCE.txt").write_text(target["sha"] + "\n")
        self.compose("config", "-q", root=destination)

    def automatic_guard(self, current, candidate):
        if not (self.root / ".git").is_dir():
            raise Deferred("Automatic updates require a Git checkout established by one successful manual update first.")
        changed = self.command(["git", "-c", f"safe.directory={self.root}", "diff", "HEAD", "--name-only"]).splitlines()
        untracked = self.command(["git", "-c", f"safe.directory={self.root}", "ls-files", "--others", "--exclude-standard"]).splitlines()
        unexpected = [p for p in changed + untracked if not is_local_path(p)]
        if unexpected:
            raise Deferred("Local source modifications/untracked files require manual review: " + ", ".join(unexpected[:10]))
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

    def update(self, target, *, automatic=False, allow_migrations=False, skip_ci=False):
        self.staging()
        self.database_ready()
        current = self.revision()
        if current == target["sha"]:
            if automatic or (self.root / ".git").is_dir():
                log("Already at " + current + "; no update needed.")
                return
            log("Same SHA, but this is an archive installation. Preparing its first managed Git checkout.")
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

    def doctor(self):
        log("PartFlow NAS Admin " + VERSION)
        log("Python: " + sys.version.split()[0])
        log("Git: " + self.command(["git", "--version"]))
        log("Docker: " + self.docker("version", "--format", "{{.Server.Version}}"))
        self.env()
        self.compose("config", "-q")
        self.free_space()
        log("Compose config and source-volume free-space checks passed.")
        log("Project: " + self.config["project"] + " | environment: " + self.config["environment"])
        log("Auto-update: " + str(self.config["auto_update"]) + " | channel: " + self.config["release_channel"])
        log("Backup SMB access: group=" + self.config["backup_read_group"] + " directories=0750 files=0640")
        log("Database volume capacity, NAS recovery, and production readiness are not certified by doctor.")

    def status(self):
        log("Source: " + self.revision())
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
            raise Failure("Volume deletion is blocked. Use reset-db for a backed-up clean staging database.")
        read_only = args[0] in ("ps", "logs", "config", "version", "top", "images", "port")
        # A long-running log viewer must not prevent maintenance operations.
        with contextlib.nullcontext() if read_only else self.lock():
            # Inherit stdin/stdout for interactive exec and streaming logs.
            if self.cli is None:
                self.compose("version")
            command = self.cli + ["-p", self.config["project"], "-f", str(self.root / "compose.nas.yaml")]
            if self.override.exists():
                command += ["-f", str(self.override)]
            child_env = os.environ.copy()
            if (self.root / ".env").is_file():
                for name in read_dotenv(self.root / ".env"):
                    child_env.pop(name, None)
            result = subprocess.call(command + args, cwd=self.root, env=child_env)
            if result:
                raise Failure(f"Compose exited with status {result}.")


def parser():
    result = argparse.ArgumentParser(description="PartFlow NAS staging administration; use --help on a command.")
    subs = result.add_subparsers(dest="command", required=True)
    for name in ("doctor", "status", "backup", "reset-db", "resume"):
        subs.add_parser(name)
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
    known = {"doctor", "status", "backup", "reset-db", "resume", "backups", "rollback", "update", "release-check"}
    controller = None
    managed_started = False
    held_lock = contextlib.ExitStack()
    try:
        if not argv or argv[0] not in known and argv[0] not in ("-h", "--help"):
            default_root = Path(os.environ.get("PF_REPO_ROOT", Path(__file__).resolve().parents[2]))
            controller = Controller(root or default_root)
            controller.passthrough(argv)
            return 0
        args = parser().parse_args(argv)
        default_root = Path(os.environ.get("PF_REPO_ROOT", Path(__file__).resolve().parents[2]))
        controller = Controller(root or default_root)
        held_lock.enter_context(controller.lock(allow_pending=args.command in ("doctor", "status", "backups", "rollback", "resume")))
        if args.command == "doctor":
            controller.doctor()
        elif args.command == "status":
            controller.status()
        elif args.command == "backups":
            controller.display_page(controller.snapshots(), args.page)
        elif args.command == "backup":
            controller.database_ready()
            controller.ensure_local_contract()
            controller.snapshot("scheduled-or-manual-backup")
            log("Copy the entire checkpoint directory off-NAS. It contains .env secrets.")
        elif args.command == "reset-db":
            managed_started = True
            controller.reset_database()
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
