"""The backup artifact rules (Phase 16 slice 5; CD7, CD4, OD-16-10).

One backup is one directory ``<backup-dir>/<NAME>/`` holding exactly
``partflow.dump`` (``pg_dump --format=custom`` streamed from inside ``db``),
``partflow.dump.list`` (its ``pg_restore --list``), ``manifest.json``
(schema v1) and ``SHA256SUMS``. ``deploy/production/backup.sh`` writes the
dump files to ``<backup-dir>/.partial/<NAME>/``; this module turns them into
a published backup, verifies one, decides whether one is fresh enough for a
migration, and applies the daily retention. It never connects to a
database: everything comes from the artifact and the host-supplied
identity.

- ``publish_backup`` (``backup-manifest``): checks the dump files,
  assembles ``.partial/<NAME>.publish/`` (manifest, checksums), verifies it
  and publishes it with one ``rename``. A crash never leaves a half backup
  at the root.
- ``verify_backup`` (``backup-verify``, ``migrate``, ``backup-rotate``):
  read-only checks ``directory``, ``files``, ``sha256sums``, ``manifest``,
  ``dump_header``, ``list``, ``expect_database``.
- ``check_freshness`` (``migrate``): the dump started at or after a
  not-before instant, or is at most N minutes old.
- ``rotate_backups`` (``backup-rotate``): keeps the newest verified daily
  backups and the newest verified daily of each of the newest ISO weeks;
  never touches pre-release, manual, unknown or invalid entries.
"""

import datetime
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from app.infrastructure import backup_files, schema_revision
from app.infrastructure.database_privileges import TABLE_CLASSES

REPORT_VERSION: Final = 1
MANIFEST_VERSION: Final = 1
DEFAULT_BACKUP_DIR: Final = Path("/backups")
DEFAULT_MAX_AGE_MINUTES: Final = 60
MAX_TEXT_LENGTH: Final = 500

#: The ``pg_dump`` options of every backup; ``backup.sh`` passes exactly these.
DUMP_OPTIONS: Final = [
    "--format=custom",
    "--no-owner",
    "--no-privileges",
    "--lock-wait-timeout=60s",
]

DUMP_FILE: Final = "partflow.dump"
LIST_FILE: Final = "partflow.dump.list"
MANIFEST_FILE: Final = "manifest.json"
SUMS_FILE: Final = "SHA256SUMS"
REVISION_FILE: Final = "alembic_version.sql"
PARTIAL_DIR: Final = ".partial"
PUBLISH_SUFFIX: Final = ".publish"
DELETE_SUFFIX: Final = ".delete"
#: The published files, in ``SHA256SUMS`` order (sorted by name).
SUMMED_FILES: Final = (MANIFEST_FILE, DUMP_FILE, LIST_FILE)
REQUIRED_FILES: Final = (DUMP_FILE, LIST_FILE, MANIFEST_FILE, SUMS_FILE)

NAME_PATTERN: Final = re.compile(
    r"^(?P<stamp>[0-9]{8}T[0-9]{6}Z)-(?P<kind>daily|manual|pre-release)"
    r"(?:-(?P<label>[A-Za-z0-9][A-Za-z0-9._-]{0,63}))?$"
)
RELEASE_TAG_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REVISION_PATTERN: Final = re.compile(r"[A-Za-z0-9_]{1,32}")
COMMIT_PATTERN: Final = re.compile(r"[0-9a-f]{40}")
IMAGE_ID_PATTERN: Final = re.compile(r"sha256:[0-9a-f]{64}")
ENVIRONMENT_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{0,31}")
HOST_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,100}")
UTC_PATTERN: Final = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_SHA256_LINE: Final = re.compile(r"([0-9a-f]{64})  (\S+)")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_SERVER_MAJOR: Final = re.compile(r"(\d+)")

_MAX_ALEMBIC_ROWS: Final = 10
_MAX_ALEMBIC_ROW_LENGTH: Final = 64
_PARTIAL_MAX_AGE: Final = datetime.timedelta(hours=24)
_FUTURE_TOLERANCE: Final = datetime.timedelta(minutes=2)

BackupKind = Literal["daily", "manual", "pre-release"]
CheckStatus = Literal["pass", "fail", "not_run", "skipped"]
CHECK_IDS: Final = (
    "directory",
    "files",
    "sha256sums",
    "manifest",
    "dump_header",
    "list",
    "expect_database",
)


# ---------------------------------------------------------------------------
# Names, text rules, time
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackupName:
    name: str
    stamp: datetime.datetime
    kind: BackupKind
    label: str | None


def parse_name(value: str) -> BackupName | None:
    """The parsed backup name, or None when ``value`` is not one.

    ``label`` is required for ``pre-release`` and forbidden otherwise.
    """
    match = NAME_PATTERN.fullmatch(value)
    if match is None:
        return None
    kind = match.group("kind")
    label = match.group("label")
    if (kind == "pre-release") != (label is not None):
        return None
    try:
        stamp = datetime.datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%SZ")
    except ValueError:
        return None
    if kind == "daily":
        return BackupName(value, stamp.replace(tzinfo=datetime.UTC), "daily", None)
    if kind == "manual":
        return BackupName(value, stamp.replace(tzinfo=datetime.UTC), "manual", None)
    return BackupName(value, stamp.replace(tzinfo=datetime.UTC), "pre-release", label)


def backup_text(value: str) -> str:
    """An operator or reason text: trimmed, 1-500 characters, no control character, no " or \\."""
    trimmed = value.strip()
    if not 1 <= len(trimmed) <= MAX_TEXT_LENGTH:
        raise ValueError(f"must be 1 to {MAX_TEXT_LENGTH} characters after trimming")
    if any(unicodedata.category(character) == "Cc" for character in trimmed):
        raise ValueError("must not contain control characters")
    if '"' in trimmed or "\\" in trimmed:
        raise ValueError('must not contain " or \\')
    return trimmed


def utc_text(value: datetime.datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` (second precision)."""
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime.datetime | None:
    """A ``YYYY-MM-DDTHH:MM:SSZ`` instant, or None."""
    if UTC_PATTERN.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.UTC)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _clean_row(row: str) -> str:
    cut = row[:_MAX_ALEMBIC_ROW_LENGTH]
    return "".join("?" if unicodedata.category(c) == "Cc" else c for c in cut)


@dataclass(frozen=True)
class BackupError:
    code: str
    message: str
    exception: BaseException | None = None


def _error_document(error: BackupError | None) -> dict[str, str] | None:
    return None if error is None else {"code": error.code, "message": error.message}


# ---------------------------------------------------------------------------
# The dump files: listing and alembic_version
# ---------------------------------------------------------------------------


def listing_problem(listing: backup_files.TocListing) -> str | None:
    """Why a ``pg_restore --list`` output is unusable (the contract's ``{problem}``)."""
    if (
        listing.archive_created is None
        or listing.archive_zone != "UTC"
        or _archive_time(listing.archive_created) is None
    ):
        return 'no "Archive created at … UTC" line'
    if listing.archive_format != "CUSTOM":
        return "format is not CUSTOM"
    if listing.dbname is None:
        return "no dbname"
    if listing.toc_entries is None or listing.toc_entries < 1:
        return "no TOC entries"
    if listing.server_version is None or _SERVER_MAJOR.match(listing.server_version) is None:
        return "no server version"
    return None


def _archive_time(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.UTC)


def _parse_listing(path: Path) -> backup_files.TocListing:
    text = backup_files.read_bytes(path).decode("utf-8", errors="replace")
    return backup_files.parse_toc_listing(text)


@dataclass(frozen=True)
class AlembicData:
    revision: str | None
    rows: list[str] | None
    row_count: int

    @property
    def anomalous(self) -> bool:
        return self.rows is not None and self.revision is None


def alembic_data(rows: list[str] | None) -> AlembicData:
    """The revision of the dumped ``alembic_version`` rows (None = no table or anomalous)."""
    if rows is None:
        return AlembicData(None, None, 0)
    kept = [_clean_row(row) for row in rows[:_MAX_ALEMBIC_ROWS]]
    if len(rows) == 1 and REVISION_PATTERN.fullmatch(rows[0]) is not None:
        return AlembicData(rows[0], kept, 1)
    return AlembicData(None, kept, len(rows))


def alembic_warning(data: AlembicData) -> str:
    return f"alembic_version holds {data.row_count} rows ({', '.join(data.rows or [])})"


def extra_tables_warning(tables: list[str]) -> str:
    return f"extra tables in the dump: {', '.join(tables)}"


# ---------------------------------------------------------------------------
# backup-manifest: publish a backup
# ---------------------------------------------------------------------------

PUBLISH_MESSAGES: Final = {
    "partial_missing": (
        "The dump files of {name} are not in {dir}/.partial/{name}/ (expected partflow.dump,"
        " partflow.dump.list and alembic_version.sql). Nothing was published."
    ),
    "dump_invalid": (
        "The dump of {name} is empty or is not a PostgreSQL custom-format archive. Nothing was"
        " published."
    ),
    "list_invalid": (
        "The table of contents of {name} cannot be read ({problem}). Nothing was published."
    ),
    "revision_invalid": (
        "The alembic_version data in {name} cannot be parsed (the COPY block has no end"
        " marker). Nothing was published."
    ),
    "tables_incomplete": (
        "The dump of {name} has no data entry for {tables}, which this release expects at"
        " revision {revision}. Nothing was published."
    ),
    "name_exists": "A backup named {name} already exists. Nothing was published.",
    "io_error": (
        "The backup files of {name} could not be read or written ({reason}). Nothing was published."
    ),
    "internal_error": (
        "backup-manifest failed unexpectedly (internal error). Nothing was published."
    ),
}
_PUBLISH_REFUSALS: Final = frozenset(
    {
        "partial_missing",
        "dump_invalid",
        "list_invalid",
        "revision_invalid",
        "tables_incomplete",
        "name_exists",
    }
)


@dataclass(frozen=True)
class ManifestRequest:
    """The host-supplied identity of one backup (``backup-manifest`` options)."""

    name: BackupName
    operator: str
    reason: str
    release_tag: str
    release_commit: str | None
    expected_revision: str | None
    environment: str
    host: str
    image_backend: str | None
    image_web: str | None
    image_db: str | None
    tool_tag: str | None
    tool_commit: str | None


@dataclass
class PublishReport:
    name: str
    result: Literal["published", "refused", "failed"] = "failed"
    path: str | None = None
    manifest_sha256: str | None = None
    alembic_revision: str | None = None
    dump_started_at: str | None = None
    dump_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    error: BackupError | None = None

    @property
    def exit_code(self) -> int:
        if self.result == "published":
            return 0
        return 1 if self.result == "refused" else 2


class _PublishRefusal(Exception):
    def __init__(self, code: str, **values: str) -> None:
        super().__init__(code)
        self.code = code
        self.values = values


def _manifest_bytes(manifest: dict[str, object]) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _sums_bytes(digests: dict[str, backup_files.FileDigest]) -> bytes:
    return "".join(f"{digests[name].sha256}  {name}\n" for name in SUMMED_FILES).encode("ascii")


def _build_manifest(
    request: ManifestRequest,
    *,
    listing: backup_files.TocListing,
    alembic: AlembicData,
    tables_checked: bool,
    extra_tables: list[str],
    code_head: str,
    completed_at: datetime.datetime,
    dump: backup_files.FileDigest,
    dump_list: backup_files.FileDigest,
) -> dict[str, object]:
    assert listing.archive_created is not None and listing.server_version is not None
    started = _archive_time(listing.archive_created)
    assert started is not None
    major = _SERVER_MAJOR.match(listing.server_version)
    assert major is not None
    return {
        "manifest_version": MANIFEST_VERSION,
        "name": request.name.name,
        "kind": request.name.kind,
        "label": request.name.label,
        "environment": request.environment,
        "host": request.host,
        "operator": request.operator,
        "reason": request.reason,
        "dump_started_at": utc_text(started),
        "completed_at": utc_text(completed_at),
        "release": {
            "tag": request.release_tag,
            "commit": request.release_commit,
            "expected_revision": request.expected_revision,
        },
        "tool": {"tag": request.tool_tag, "commit": request.tool_commit, "code_head": code_head},
        "images": {
            "backend": request.image_backend,
            "web": request.image_web,
            "db": request.image_db,
        },
        "alembic_revision": alembic.revision,
        "alembic_rows": alembic.rows,
        "database": {
            "name": listing.dbname,
            "server_version": listing.server_version,
            "server_major": int(major.group(1)),
            "pg_dump_version": listing.pg_dump_version,
        },
        "dump": {
            "format": "custom",
            "options": list(DUMP_OPTIONS),
            "toc_entries": listing.toc_entries,
            "table_data_entries": len(listing.table_data),
            "tables_checked": tables_checked,
            "extra_tables": extra_tables,
        },
        "files": [
            {"name": DUMP_FILE, "bytes": dump.size, "sha256": dump.sha256},
            {"name": LIST_FILE, "bytes": dump_list.size, "sha256": dump_list.sha256},
        ],
    }


def _publish(
    backup_dir: Path,
    request: ManifestRequest,
    report: PublishReport,
    *,
    code_head: str,
    now: datetime.datetime,
) -> None:
    name = request.name.name
    target = backup_dir / name
    if backup_files.exists(target):
        raise _PublishRefusal("name_exists")
    partial = backup_dir / PARTIAL_DIR / name
    if not all(
        backup_files.is_regular_file(partial / file)
        for file in (DUMP_FILE, LIST_FILE, REVISION_FILE)
    ):
        raise _PublishRefusal("partial_missing")
    if backup_files.read_prefix(partial / DUMP_FILE, 5) != backup_files.CUSTOM_ARCHIVE_MAGIC:
        raise _PublishRefusal("dump_invalid")
    listing = _parse_listing(partial / LIST_FILE)
    problem = listing_problem(listing)
    if problem is not None:
        raise _PublishRefusal("list_invalid", problem=problem)
    script = backup_files.read_bytes(partial / REVISION_FILE).decode("utf-8", errors="replace")
    try:
        alembic = alembic_data(backup_files.parse_alembic_copy(script))
    except backup_files.CopyBlockError:
        raise _PublishRefusal("revision_invalid") from None
    report.alembic_revision = alembic.revision
    if alembic.anomalous:
        report.warnings.append(alembic_warning(alembic))
    tables_checked = alembic.revision is not None and alembic.revision == code_head
    extra_tables: list[str] = []
    if tables_checked:
        present = set(listing.table_data)
        missing = sorted(set(TABLE_CLASSES) - present)
        if missing:
            raise _PublishRefusal(
                "tables_incomplete", tables=", ".join(missing), revision=code_head
            )
        extra_tables = sorted(present - set(TABLE_CLASSES))
        if extra_tables:
            report.warnings.append(extra_tables_warning(extra_tables))
    report.warnings.sort()

    # Assemble below .partial/, then publish with one rename.
    assembly = backup_dir / PARTIAL_DIR / f"{name}{PUBLISH_SUFFIX}"
    backup_files.move_directory(partial, assembly)
    dump = backup_files.digest(assembly / DUMP_FILE)
    dump_list = backup_files.digest(assembly / LIST_FILE)
    manifest = _build_manifest(
        request,
        listing=listing,
        alembic=alembic,
        tables_checked=tables_checked,
        extra_tables=extra_tables,
        code_head=code_head,
        completed_at=now,
        dump=dump,
        dump_list=dump_list,
    )
    manifest_content = _manifest_bytes(manifest)
    backup_files.write_new_file(assembly / MANIFEST_FILE, manifest_content)
    manifest_digest = backup_files.digest(assembly / MANIFEST_FILE)
    sums = {MANIFEST_FILE: manifest_digest, DUMP_FILE: dump, LIST_FILE: dump_list}
    backup_files.write_new_file(assembly / SUMS_FILE, _sums_bytes(sums))
    backup_files.remove_file(assembly / REVISION_FILE)
    for file in (DUMP_FILE, LIST_FILE):
        backup_files.sync_file(assembly / file)
    backup_files.set_private_modes(assembly, list(REQUIRED_FILES))
    check = VerifyResult(name)
    _verify_contents(assembly, request.name, check, expect_database=None)
    if not check.verified:
        failed = check.first_failure()
        detail = f"{failed[0]}: {failed[1]}" if failed else "unreadable"
        raise RuntimeError(f"the assembled backup failed its own verification ({detail})")
    backup_files.move_directory(assembly, target)
    report.path = str(target)
    report.manifest_sha256 = manifest_digest.sha256
    report.dump_started_at = str(manifest["dump_started_at"])
    report.dump_bytes = dump.size


def publish_backup(
    backup_dir: Path,
    request: ManifestRequest,
    *,
    now: datetime.datetime | None = None,
) -> PublishReport:
    """``backup-manifest``: publish ``.partial/<NAME>/`` as ``<NAME>/``; always a report.

    The completeness check compares with this image's code head.
    """
    report = PublishReport(name=request.name.name)
    name = request.name.name
    try:
        code_head = schema_revision.code_head()
        _publish(backup_dir, request, report, code_head=code_head, now=now or _now())
    except _PublishRefusal as refusal:
        message = PUBLISH_MESSAGES[refusal.code].format(name=name, dir=backup_dir, **refusal.values)
        return _publish_failure(report, refusal.code, message)
    except OSError as exc:
        message = PUBLISH_MESSAGES["io_error"].format(name=name, reason=_reason(exc))
        return _publish_failure(report, "io_error", message, exc)
    except Exception as exc:
        return _publish_failure(report, "internal_error", PUBLISH_MESSAGES["internal_error"], exc)
    report.result = "published"
    return report


def _publish_failure(
    report: PublishReport, code: str, message: str, exception: BaseException | None = None
) -> PublishReport:
    report.result = "refused" if code in _PUBLISH_REFUSALS else "failed"
    report.path = None
    report.manifest_sha256 = None
    report.error = BackupError(code, message, exception)
    return report


def publish_document(report: PublishReport) -> dict[str, object]:
    """The JSON document of one backup-manifest run (keys in the contract order)."""
    return {
        "report_version": REPORT_VERSION,
        "command": "backup-manifest",
        "result": report.result,
        "exit_code": report.exit_code,
        "name": report.name,
        "path": report.path,
        "manifest_sha256": report.manifest_sha256,
        "alembic_revision": report.alembic_revision,
        "dump_started_at": report.dump_started_at,
        "dump_bytes": report.dump_bytes,
        "warnings": list(report.warnings),
        "error": _error_document(report.error),
    }


def publish_summary(report: PublishReport) -> str:
    if report.result != "published" or report.error is not None:
        message = report.error.message if report.error is not None else report.result
        return f"backup-manifest: {message}"
    summary = (
        f"backup-manifest: published {report.name} ({report.dump_bytes} bytes, revision"
        f" {report.alembic_revision or 'none'})"
    )
    if report.warnings:
        summary += f", {len(report.warnings)} warnings"
    return summary


# ---------------------------------------------------------------------------
# backup-verify
# ---------------------------------------------------------------------------

VERIFY_MESSAGES: Final = {
    "backup_invalid": "The backup {name} failed verification ({check}: {detail}). Do not use it.",
    "io_error": "The backup {name} could not be read ({reason}).",
    "internal_error": "backup-verify failed unexpectedly (internal error).",
}

_TOP_KEYS: Final = frozenset(
    {
        "manifest_version",
        "name",
        "kind",
        "label",
        "environment",
        "host",
        "operator",
        "reason",
        "dump_started_at",
        "completed_at",
        "release",
        "tool",
        "images",
        "alembic_revision",
        "alembic_rows",
        "database",
        "dump",
        "files",
    }
)


@dataclass(frozen=True)
class Manifest:
    """The fields of a manifest that passed the ``manifest`` check."""

    name: str
    kind: BackupKind
    release_tag: str
    expected_revision: str | None
    alembic_revision: str | None
    alembic_rows: list[str] | None
    database_name: str
    dump_started_at: datetime.datetime
    toc_entries: int
    table_data_entries: int
    extra_tables: list[str]

    @property
    def anomalous(self) -> bool:
        return self.alembic_rows is not None and self.alembic_revision is None


@dataclass
class Check:
    id: str
    status: CheckStatus = "not_run"
    detail: str | None = None


@dataclass
class VerifyResult:
    name: str
    checks: list[Check] = field(default_factory=lambda: [Check(id) for id in CHECK_IDS])
    raw_manifest: dict[str, object] | None = None
    manifest: Manifest | None = None
    extra_entries: list[str] = field(default_factory=list)
    dump_bytes: int = 0
    manifest_sha256: str | None = None
    error: BackupError | None = None

    def check(self, check_id: str) -> Check:
        return next(check for check in self.checks if check.id == check_id)

    def first_failure(self) -> tuple[str, str] | None:
        for check in self.checks:
            if check.status == "fail":
                return check.id, check.detail or "failed"
        return None

    @property
    def verified(self) -> bool:
        return self.error is None and all(
            check.status in ("pass", "skipped") for check in self.checks
        )

    @property
    def result(self) -> Literal["verified", "invalid", "failed"]:
        if self.error is not None and self.error.code != "backup_invalid":
            return "failed"
        return "verified" if self.verified else "invalid"

    @property
    def exit_code(self) -> int:
        return {"verified": 0, "invalid": 1, "failed": 2}[self.result]


def _is_str(value: object, pattern: re.Pattern[str] | None = None) -> bool:
    return isinstance(value, str) and (pattern is None or pattern.fullmatch(value) is not None)


def _is_optional_str(value: object, pattern: re.Pattern[str]) -> bool:
    return value is None or _is_str(value, pattern)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return backup_text(value) == value
    except ValueError:
        return False


def _has_keys(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def _invalid_key(raw: dict[str, object]) -> str | None:
    """The first manifest key that is missing or invalid (None = every key is valid)."""
    release = raw.get("release")
    tool = raw.get("tool")
    images = raw.get("images")
    database = raw.get("database")
    dump = raw.get("dump")
    rows = raw.get("alembic_rows")
    checks: list[tuple[str, bool]] = [
        ("name", _is_str(raw.get("name"))),
        ("kind", raw.get("kind") in ("daily", "manual", "pre-release")),
        ("label", _is_optional_str(raw.get("label"), RELEASE_TAG_PATTERN)),
        ("environment", _is_str(raw.get("environment"), ENVIRONMENT_PATTERN)),
        ("host", _is_str(raw.get("host"), HOST_PATTERN)),
        ("operator", _is_text(raw.get("operator"))),
        ("reason", _is_text(raw.get("reason"))),
        ("dump_started_at", _is_str(raw.get("dump_started_at"), UTC_PATTERN)),
        ("completed_at", _is_str(raw.get("completed_at"), UTC_PATTERN)),
        (
            "release",
            _has_keys(release, {"tag", "commit", "expected_revision"})
            and isinstance(release, dict)
            and _is_str(release["tag"], RELEASE_TAG_PATTERN)
            and _is_optional_str(release["commit"], COMMIT_PATTERN)
            and _is_optional_str(release["expected_revision"], REVISION_PATTERN),
        ),
        (
            "tool",
            _has_keys(tool, {"tag", "commit", "code_head"})
            and isinstance(tool, dict)
            and _is_optional_str(tool["tag"], RELEASE_TAG_PATTERN)
            and _is_optional_str(tool["commit"], COMMIT_PATTERN)
            and _is_str(tool["code_head"], REVISION_PATTERN),
        ),
        (
            "images",
            _has_keys(images, {"backend", "web", "db"})
            and isinstance(images, dict)
            and all(_is_optional_str(value, IMAGE_ID_PATTERN) for value in images.values()),
        ),
        ("alembic_revision", _is_optional_str(raw.get("alembic_revision"), REVISION_PATTERN)),
        (
            "alembic_rows",
            rows is None
            or (
                isinstance(rows, list)
                and len(rows) <= _MAX_ALEMBIC_ROWS
                and all(_is_str(row) and len(row) <= _MAX_ALEMBIC_ROW_LENGTH for row in rows)
                and (raw.get("alembic_revision") is None or rows == [raw.get("alembic_revision")])
            ),
        ),
        (
            "database",
            _has_keys(database, {"name", "server_version", "server_major", "pg_dump_version"})
            and isinstance(database, dict)
            and _is_str(database["name"])
            and bool(database["name"])
            and _is_str(database["server_version"])
            and _is_int(database["server_major"])
            and (database["pg_dump_version"] is None or _is_str(database["pg_dump_version"])),
        ),
        (
            "dump",
            _has_keys(
                dump,
                {
                    "format",
                    "options",
                    "toc_entries",
                    "table_data_entries",
                    "tables_checked",
                    "extra_tables",
                },
            )
            and isinstance(dump, dict)
            and dump["format"] == "custom"
            and dump["options"] == DUMP_OPTIONS
            and _is_int(dump["toc_entries"])
            and dump["toc_entries"] >= 1
            and _is_int(dump["table_data_entries"])
            and dump["table_data_entries"] >= 0
            and isinstance(dump["tables_checked"], bool)
            and isinstance(dump["extra_tables"], list)
            and all(_is_str(table) for table in dump["extra_tables"])
            and dump["extra_tables"] == sorted(dump["extra_tables"]),
        ),
    ]
    if rows is None and raw.get("alembic_revision") is not None:
        return "alembic_rows"
    for key, valid in checks:
        if not valid:
            return key
    return None


def manifest_problem(raw: object, name: BackupName) -> str | None:
    """Why ``manifest.json`` is invalid apart from its ``files`` digests (None = valid).

    The object, ``manifest_version``, key set, every key's value and the
    name/kind/label agreement with the directory name — no file is read
    (``status``, Phase 16 slice 6). ``_manifest_detail`` adds the digests.
    """
    if not isinstance(raw, dict):
        return f"{MANIFEST_FILE}: is not a JSON object"
    if raw.get("manifest_version") != MANIFEST_VERSION or not _is_int(raw["manifest_version"]):
        return f"{MANIFEST_FILE}: manifest_version is not {MANIFEST_VERSION}"
    missing = sorted(_TOP_KEYS - set(raw))
    if missing:
        return f"{MANIFEST_FILE}: {missing[0]} is missing"
    unknown = sorted(set(raw) - _TOP_KEYS)
    if unknown:
        return f"{MANIFEST_FILE}: {unknown[0]} is not a manifest key"
    invalid = _invalid_key(raw)
    if invalid is not None:
        return f"{MANIFEST_FILE}: {invalid} is invalid"
    if raw["name"] != name.name:
        return f"{MANIFEST_FILE}: name differs from the directory name"
    if raw["kind"] != name.kind or raw["label"] != name.label:
        return f"{MANIFEST_FILE}: kind or label differs from the directory name"
    return None


def _manifest_detail(
    raw: object, name: BackupName, digests: dict[str, backup_files.FileDigest]
) -> str | None:
    """Why ``manifest.json`` is invalid (None = valid)."""
    problem = manifest_problem(raw, name)
    if problem is not None:
        return problem
    assert isinstance(raw, dict)
    expected_files = [
        {"name": file, "bytes": digests[file].size, "sha256": digests[file].sha256}
        for file in (DUMP_FILE, LIST_FILE)
    ]
    if raw["files"] != expected_files:
        return f"{MANIFEST_FILE}: files do not match {DUMP_FILE} and {LIST_FILE}"
    return None


def _manifest_of(raw: dict[str, object]) -> Manifest:
    """The typed view of a manifest that passed ``_manifest_detail``."""
    release = raw["release"]
    database = raw["database"]
    dump = raw["dump"]
    assert isinstance(release, dict) and isinstance(database, dict) and isinstance(dump, dict)
    started = parse_utc(str(raw["dump_started_at"]))
    assert started is not None
    kind = raw["kind"]
    assert kind in ("daily", "manual", "pre-release")
    rows = raw["alembic_rows"]
    revision = raw["alembic_revision"]
    return Manifest(
        name=str(raw["name"]),
        kind=kind,
        release_tag=str(release["tag"]),
        expected_revision=release["expected_revision"],
        alembic_revision=revision if isinstance(revision, str) else None,
        alembic_rows=[str(row) for row in rows] if isinstance(rows, list) else None,
        database_name=str(database["name"]),
        dump_started_at=started,
        toc_entries=int(dump["toc_entries"]),
        table_data_entries=int(dump["table_data_entries"]),
        extra_tables=[str(table) for table in dump["extra_tables"]],
    )


def _sums_detail(content: bytes, digests: dict[str, backup_files.FileDigest]) -> str | None:
    """Why ``SHA256SUMS`` does not match the files (None = it matches)."""
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError:
        return f"{SUMS_FILE}: is not ASCII text"
    lines = text.split("\n")
    if not text.endswith("\n") or len(lines) != len(SUMMED_FILES) + 1:
        return f"{SUMS_FILE}: must hold exactly three lines"
    for line, expected_name in zip(lines, SUMMED_FILES, strict=False):
        match = _SHA256_LINE.fullmatch(line)
        if match is None or match.group(2) != expected_name:
            return (
                f"{SUMS_FILE}: must list {MANIFEST_FILE}, {DUMP_FILE} and {LIST_FILE}, sorted,"
                " as '<sha256>  <name>'"
            )
        if match.group(1) != digests[expected_name].sha256:
            return f"{expected_name}: sha256 differs from {SUMS_FILE}"
    return None


def _verify_contents(
    path: Path, name: BackupName, result: VerifyResult, *, expect_database: str | None
) -> None:
    """Every check after ``directory``; raises ``OSError`` on a read failure."""
    result.check("directory").status = "pass"
    files = result.check("files")
    for file in REQUIRED_FILES:
        if not backup_files.is_regular_file(path / file):
            files.status, files.detail = (
                "fail",
                f"{file}: missing, a symbolic link or not a regular file",
            )
            return
    files.status = "pass"
    result.extra_entries = [
        entry for entry in backup_files.entry_names(path) if entry not in REQUIRED_FILES
    ]
    _verify_files(path, name, result, expect_database)


def _verify_files(
    path: Path, name: BackupName, result: VerifyResult, expect_database: str | None
) -> None:
    digests = {file: backup_files.digest(path / file) for file in SUMMED_FILES}
    result.dump_bytes = digests[DUMP_FILE].size
    result.manifest_sha256 = digests[MANIFEST_FILE].sha256

    sums = result.check("sha256sums")
    sums.detail = _sums_detail(backup_files.read_bytes(path / SUMS_FILE), digests)
    sums.status = "pass" if sums.detail is None else "fail"

    manifest = result.check("manifest")
    try:
        raw: object = json.loads(backup_files.read_bytes(path / MANIFEST_FILE).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raw = None
        manifest.detail = f"{MANIFEST_FILE}: is not valid JSON"
    else:
        if isinstance(raw, dict):
            result.raw_manifest = raw
        manifest.detail = _manifest_detail(raw, name, digests)
    manifest.status = "pass" if manifest.detail is None else "fail"
    if manifest.status == "pass" and result.raw_manifest is not None:
        result.manifest = _manifest_of(result.raw_manifest)

    header = result.check("dump_header")
    if backup_files.read_prefix(path / DUMP_FILE, 5) == backup_files.CUSTOM_ARCHIVE_MAGIC:
        header.status = "pass"
    else:
        header.status, header.detail = "fail", f"{DUMP_FILE}: does not start with PGDMP"

    listing_check = result.check("list")
    listing = _parse_listing(path / LIST_FILE)
    problem = listing_problem(listing)
    if problem is not None:
        listing_check.detail = f"{LIST_FILE}: cannot be read ({problem})"
    elif result.manifest is not None:
        if listing.dbname != result.manifest.database_name:
            listing_check.detail = f"{LIST_FILE}: dbname differs from the manifest"
        elif listing.toc_entries != result.manifest.toc_entries:
            listing_check.detail = f"{LIST_FILE}: TOC entries differ from the manifest"
        elif len(listing.table_data) != result.manifest.table_data_entries:
            listing_check.detail = f"{LIST_FILE}: TABLE DATA entries differ from the manifest"
    listing_check.status = "pass" if listing_check.detail is None else "fail"

    expect = result.check("expect_database")
    if expect_database is None:
        expect.status = "skipped"
    elif result.manifest is None:
        expect.status = "not_run"
    elif result.manifest.database_name == expect_database:
        expect.status = "pass"
    else:
        expect.status, expect.detail = (
            "fail",
            f"{MANIFEST_FILE}: database {result.manifest.database_name}, not {expect_database}",
        )


def _directory_detail(path: Path, name: BackupName | None) -> str | None:
    if not backup_files.exists(path):
        return "not found"
    if name is None:
        return "the name is not a backup name"
    if backup_files.is_symlink(path):
        return "is a symbolic link"
    if not backup_files.is_real_directory(path):
        return "is not a directory"
    if path.parent.name == PARTIAL_DIR:
        return "is inside .partial/ (not published)"
    return None


def verify_backup(
    backup_dir: Path, name: str, *, expect_database: str | None = None
) -> VerifyResult:
    """``backup-verify``: the read-only checks of ``<backup-dir>/<NAME>``; always a result."""
    parsed = parse_name(name)
    if parsed is None:
        # Never a path: a name outside the grammar is not looked up at all.
        result = VerifyResult(name)
        directory = result.check("directory")
        directory.status, directory.detail = "fail", "the name is not a backup name"
        return _with_invalid(result)
    try:
        return verify_directory(backup_dir / name, expect_database=expect_database)
    except Exception as exc:
        # Any unexpected failure is still a complete result (exit 2).
        return verify_failure(name, exc)


def verify_directory(path: Path, *, expect_database: str | None = None) -> VerifyResult:
    """The §3.3 checks of one backup directory; always a result (never raises ``OSError``)."""
    parsed = parse_name(path.name)
    result = VerifyResult(path.name)
    try:
        detail = _directory_detail(path, parsed)
        if detail is not None or parsed is None:
            directory = result.check("directory")
            directory.status, directory.detail = "fail", detail
            return _with_invalid(result)
        _verify_contents(path, parsed, result, expect_database=expect_database)
    except OSError as exc:
        # The checks reached so far keep their status; the others stay not_run.
        message = VERIFY_MESSAGES["io_error"].format(name=path.name, reason=_reason(exc))
        result.error = BackupError("io_error", message, exc)
        return result
    return _with_invalid(result)


def _with_invalid(result: VerifyResult) -> VerifyResult:
    failure = result.first_failure()
    if failure is not None:
        message = VERIFY_MESSAGES["backup_invalid"].format(
            name=result.name, check=failure[0], detail=failure[1]
        )
        result.error = BackupError("backup_invalid", message)
    return result


def _flat(raw: dict[str, object] | None, *path: str) -> object:
    value: object = raw
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _flat_str(raw: dict[str, object] | None, *path: str) -> str | None:
    value = _flat(raw, *path)
    return value if isinstance(value, str) else None


def release_matches_revision(raw: dict[str, object] | None) -> bool | None:
    """Whether the recorded release expects the dumped revision (None = either unknown)."""
    expected = _flat_str(raw, "release", "expected_revision")
    revision = _flat_str(raw, "alembic_revision")
    if expected is None or revision is None:
        return None
    return expected == revision


def verify_document(result: VerifyResult) -> dict[str, object]:
    """The JSON document of one backup-verify run (keys in the contract order)."""
    raw = result.raw_manifest
    kind = _flat_str(raw, "kind")
    extra_tables = _flat(raw, "dump", "extra_tables")
    rows = _flat(raw, "alembic_rows")
    return {
        "report_version": REPORT_VERSION,
        "command": "backup-verify",
        "result": result.result,
        "exit_code": result.exit_code,
        "name": result.name,
        "kind": kind if kind in ("daily", "manual", "pre-release") else None,
        "release_tag": _flat_str(raw, "release", "tag"),
        "alembic_revision": _flat_str(raw, "alembic_revision"),
        "database_name": _flat_str(raw, "database", "name"),
        "dump_started_at": _flat_str(raw, "dump_started_at"),
        "dump_bytes": result.dump_bytes,
        "manifest_sha256": result.manifest_sha256,
        "release_matches_revision": release_matches_revision(raw),
        "checks": [
            {"id": check.id, "status": check.status, "detail": check.detail}
            for check in result.checks
        ],
        "extra_entries": list(result.extra_entries),
        "extra_tables": list(extra_tables) if isinstance(extra_tables, list) else [],
        "alembic_rows": list(rows) if isinstance(rows, list) else None,
        "error": _error_document(result.error),
    }


def verify_summary(result: VerifyResult) -> str:
    if result.result == "verified":
        raw = result.raw_manifest
        return (
            f"backup-verify: {result.name} verified ({result.dump_bytes} bytes, revision"
            f" {_flat_str(raw, 'alembic_revision') or 'none'}, started"
            f" {_flat_str(raw, 'dump_started_at')})"
        )
    message = result.error.message if result.error is not None else result.result
    return f"backup-verify: {message}"


def verify_failure(name: str, exception: BaseException) -> VerifyResult:
    """An unexpected failure of backup-verify (exit 2)."""
    result = VerifyResult(name)
    result.error = BackupError("internal_error", VERIFY_MESSAGES["internal_error"], exception)
    return result


# ---------------------------------------------------------------------------
# Freshness (migrate --pre-release-backup)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Freshness:
    rule: Literal["not_before", "max_age"]
    not_before: datetime.datetime | None = None
    max_age_minutes: int | None = None
    age_minutes: int | None = None

    def document(self) -> dict[str, object]:
        if self.rule == "not_before":
            assert self.not_before is not None
            return {"rule": "not_before", "not_before": utc_text(self.not_before)}
        return {
            "rule": "max_age",
            "max_age_minutes": self.max_age_minutes,
            "age_minutes": self.age_minutes,
        }


class StaleBackupError(Exception):
    """The backup is older than the freshness rule allows."""

    def __init__(self, freshness: Freshness, started: datetime.datetime) -> None:
        super().__init__(freshness.rule)
        self.freshness = freshness
        self.started = started


def check_freshness(
    started: datetime.datetime,
    *,
    not_before: datetime.datetime | None,
    max_age_minutes: int,
    now: datetime.datetime,
) -> Freshness:
    """The freshness proof of a dump that started at ``started``; raises ``StaleBackupError``.

    With ``not_before`` the dump must have started at or after it (second
    precision). Otherwise it is at most ``max_age_minutes`` old and at most
    two minutes in the future (a later stamp is a clock error).
    """
    if not_before is not None:
        freshness = Freshness("not_before", not_before=not_before.replace(microsecond=0))
        if started < not_before.replace(microsecond=0):
            raise StaleBackupError(freshness, started)
        return freshness
    age = now - started
    freshness = Freshness(
        "max_age",
        max_age_minutes=max_age_minutes,
        age_minutes=int(age.total_seconds() // 60),
    )
    if age > datetime.timedelta(minutes=max_age_minutes) or -age > _FUTURE_TOLERANCE:
        raise StaleBackupError(freshness, started)
    return freshness


# ---------------------------------------------------------------------------
# backup-rotate (OD-16-10)
# ---------------------------------------------------------------------------

ROTATE_MESSAGES: Final = {
    "backup_invalid": (
        "Daily backups {names} failed verification; they were not counted or deleted. Review"
        " and remove them by hand (OPERATIONS_RUNBOOK §9)."
    ),
    "delete_failed": (
        "Some backups could not be removed ({names}: {reason}). The kept backups are unchanged."
    ),
    "io_error": "The backup directory {dir} could not be read ({reason}). Nothing was removed.",
    "internal_error": "backup-rotate failed unexpectedly (internal error).",
}


@dataclass
class RotateReport:
    dry_run: bool
    keep_daily: int
    keep_weekly: int
    kept: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    invalid: list[dict[str, str]] = field(default_factory=list)
    partial_removed: list[str] = field(default_factory=list)
    error: BackupError | None = None

    @property
    def result(self) -> Literal["rotated", "rotated_with_invalid", "failed"]:
        if self.error is not None and self.error.code != "backup_invalid":
            return "failed"
        return "rotated_with_invalid" if self.invalid else "rotated"

    @property
    def exit_code(self) -> int:
        return {"rotated": 0, "rotated_with_invalid": 1, "failed": 2}[self.result]


def select_keep(
    stamps: dict[str, datetime.datetime], keep_daily: int, keep_weekly: int
) -> set[str]:
    """The kept names: the newest ``keep_daily`` ∪ the newest of each of the newest
    ``keep_weekly`` ISO weeks (UTC) that hold one — weeks overlap the daily window,
    as restic ``forget --keep-daily D --keep-weekly W``."""
    newest_first = sorted(stamps, key=lambda name: (stamps[name], name), reverse=True)
    keep = set(newest_first[:keep_daily])
    weeks: set[tuple[int, int]] = set()
    for name in newest_first:
        if len(weeks) >= keep_weekly:
            break
        iso = stamps[name].isocalendar()
        week = (iso.year, iso.week)
        if week not in weeks:
            weeks.add(week)
            keep.add(name)
    return keep


def _manifest_identity(path: Path) -> tuple[object, object] | None:
    """``(kind, name)`` of a backup's manifest; None when it cannot be read or parsed."""
    try:
        raw = json.loads(backup_files.read_bytes(path / MANIFEST_FILE).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        # Reported as "manifest unreadable" and never deleted.
        return None
    if not isinstance(raw, dict):
        return None
    return raw.get("kind"), raw.get("name")


def _stale_partials(partial_root: Path, now: datetime.datetime) -> list[str]:
    if not backup_files.is_real_directory(partial_root):
        return []
    stale: list[str] = []
    for entry in backup_files.entry_names(partial_root):
        base = entry
        for suffix in (PUBLISH_SUFFIX, DELETE_SUFFIX):
            if entry.endswith(suffix):
                base = entry[: -len(suffix)]
        parsed = parse_name(base)
        if parsed is not None and now - parsed.stamp > _PARTIAL_MAX_AGE:
            stale.append(entry)
    return stale


def _rotate(backup_dir: Path, report: RotateReport, now: datetime.datetime) -> None:
    verified: dict[str, datetime.datetime] = {}
    for entry in backup_files.entry_names(backup_dir):
        parsed = parse_name(entry)
        path = backup_dir / entry
        if parsed is None or parsed.kind != "daily" or not backup_files.is_real_directory(path):
            continue
        identity = _manifest_identity(path)
        if identity is None:
            report.skipped.append({"name": entry, "reason": "manifest unreadable"})
            continue
        if identity != ("daily", entry):
            report.skipped.append({"name": entry, "reason": "manifest disagrees with the name"})
            continue
        result = verify_directory(path)
        if result.verified:
            verified[entry] = parsed.stamp
            continue
        failure = result.first_failure()
        if failure is None:
            # A read failure: the check that was running is the first one not run.
            running = next(check.id for check in result.checks if check.status == "not_run")
            cause = result.error.exception if result.error is not None else None
            reason = _reason(cause) if isinstance(cause, OSError) else "unknown"
            failure = (running, f"cannot be read ({reason})")
        report.invalid.append({"name": entry, "check": failure[0], "detail": failure[1]})

    keep = select_keep(verified, report.keep_daily, report.keep_weekly)
    report.kept = sorted(keep)
    to_delete = sorted(set(verified) - keep)
    partial_root = backup_dir / PARTIAL_DIR
    stale = _stale_partials(partial_root, now)
    if report.dry_run:
        report.deleted = to_delete
        report.partial_removed = stale
        return

    failures: list[tuple[str, str]] = []
    if to_delete:
        backup_files.make_private_directory(partial_root)
    for name in to_delete:
        doomed = partial_root / f"{name}{DELETE_SUFFIX}"
        try:
            backup_files.move_directory(backup_dir / name, doomed)
        except OSError as exc:
            failures.append((name, _reason(exc)))
            continue
        report.deleted.append(name)
        try:
            backup_files.remove_tree(doomed)
        except OSError as exc:
            # Off the root already; the next run removes the rest once it is old.
            failures.append((name, _reason(exc)))
    for entry in stale:
        try:
            backup_files.remove_tree(partial_root / entry)
        except OSError as exc:
            failures.append((f"{PARTIAL_DIR}/{entry}", _reason(exc)))
            continue
        report.partial_removed.append(entry)
    if failures:
        names = ", ".join(name for name, _ in failures)
        message = ROTATE_MESSAGES["delete_failed"].format(names=names, reason=failures[0][1])
        report.error = BackupError("delete_failed", message)


def rotate_backups(
    backup_dir: Path,
    *,
    keep_daily: int,
    keep_weekly: int,
    dry_run: bool = False,
    now: datetime.datetime | None = None,
) -> RotateReport:
    """``backup-rotate``: apply the daily retention; always a report."""
    report = RotateReport(dry_run=dry_run, keep_daily=keep_daily, keep_weekly=keep_weekly)
    try:
        _rotate(backup_dir, report, now or _now())
    except OSError as exc:
        message = ROTATE_MESSAGES["io_error"].format(dir=backup_dir, reason=_reason(exc))
        report.error = BackupError("io_error", message, exc)
        return report
    except Exception as exc:
        report.error = BackupError("internal_error", ROTATE_MESSAGES["internal_error"], exc)
        return report
    for listed in (report.deleted, report.partial_removed):
        listed.sort()
    report.skipped.sort(key=lambda item: item["name"])
    report.invalid.sort(key=lambda item: item["name"])
    if report.error is None and report.invalid:
        names = ", ".join(item["name"] for item in report.invalid)
        report.error = BackupError(
            "backup_invalid", ROTATE_MESSAGES["backup_invalid"].format(names=names)
        )
    return report


def rotate_document(report: RotateReport) -> dict[str, object]:
    """The JSON document of one backup-rotate run (keys in the contract order)."""
    return {
        "report_version": REPORT_VERSION,
        "command": "backup-rotate",
        "result": report.result,
        "exit_code": report.exit_code,
        "dry_run": report.dry_run,
        "keep_daily": report.keep_daily,
        "keep_weekly": report.keep_weekly,
        "kept": list(report.kept),
        "deleted": list(report.deleted),
        "skipped": [dict(item) for item in report.skipped],
        "invalid": [dict(item) for item in report.invalid],
        "partial_removed": list(report.partial_removed),
        "error": _error_document(report.error),
    }


def rotate_summary(report: RotateReport) -> str:
    if report.result == "failed":
        message = report.error.message if report.error is not None else report.result
        return f"backup-rotate: {message}"
    summary = f"backup-rotate: kept {len(report.kept)} daily backups, deleted {len(report.deleted)}"
    if report.invalid:
        summary += f", {len(report.invalid)} failed verification and were kept for review"
    if report.dry_run:
        summary += " (dry run)"
    return summary
