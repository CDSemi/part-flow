"""The read-only operational status report (Phase 16 slice 6; PLAN CD6, OD-16-11).

``python -m app.cli status`` (and the ``status`` ops service that
``deploy/production/check.sh`` runs) prints one JSON document: the release
identity, the database (size, connections, lock waits), the Movement count
and size, the schema readiness the backend would report, and the age of the
newest published backup. Sections are evaluated independently, so a
database failure still reports the backups. It never changes anything.

Database: one connection (``application_name`` ``partflow-cli``, so
``migrate`` never counts it as the API), one ``READ ONLY`` transaction with
``SET LOCAL statement_timeout`` (default 20 s, below ``migrate``'s 30 s lock
wait) and ``lock_timeout`` 5 s. The ``part_movements`` count is the last
statement: it is the only one that locks a production table (ACCESS
SHARE), so a release's ``migrate`` is at most delayed, never aborted.

Schema: the committed readiness rule (``readiness.evaluate``) over
``schema_revision.read_revision`` — the same functions the backend and the
``revision`` command use. Backup: the newest published directory by the
backup name grammar (any kind) whose manifest is valid; no dump is read.

Exit: 2 when the run could not complete (configuration, database,
internal error), else 1 when there is a finding, else 0.
"""

import datetime
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import psycopg.errors
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.application import backups, migration
from app.application.readiness import evaluate
from app.infrastructure import backup_files, schema_revision

REPORT_VERSION: Final = 1
DEFAULT_MAX_BACKUP_AGE_HOURS: Final = 26
DEFAULT_STATEMENT_TIMEOUT_SECONDS: Final = 20
LOCK_TIMEOUT_SECONDS: Final = 5
CONNECT_TIMEOUT_SECONDS: Final = 10
BACKUP_LOCK_DIR: Final = ".backup.lock"
ARCHIVAL_REASON: Final = "Archival proposals arrive with P16-S10."
#: A completion time further ahead than this is a clock problem, not a fresh backup.
FUTURE_TOLERANCE: Final = datetime.timedelta(minutes=5)

#: The exact Movement count and size: the last statement (ACCESS SHARE on part_movements).
MOVEMENTS_SQL: Final = (
    "SELECT count(*), pg_total_relation_size('part_movements') FROM part_movements"
)
_LOCK_OWNER_KEYS: Final = ("by", "started_at", "name", "release")
_MAX_LOCK_VALUE_LENGTH: Final = 200
_MIB: Final = 1024 * 1024

FINDING_MESSAGES: Final = {
    "schema_not_ready": (
        "The database is at revision {database_revision} but release {release} expects"
        " {expected_revision}: PartFlow refuses changes until the release is completed or"
        " rolled back."
    ),
    "backup_missing": "No published backup was found in {dir}.",
    "backup_stale": (
        "The newest backup {name} completed {age_hours} hours ago; the limit is {max} hours."
    ),
    "backup_time_in_future": (
        "The backup {name} records a completion time in the future ({completed_at}); check the"
        " host clock."
    ),
    "backup_manifest_unreadable": "The backup {name} has no valid manifest; it was not counted.",
    "backup_dir_unreadable": (
        "The backup directory {dir} cannot be read ({reason}). Run status as the account that"
        ' owns it (docker compose run --user "$(id -u):$(id -g)").'
    ),
}
ERROR_MESSAGES: Final = {
    "configuration_invalid": (
        "PartFlow is not configured: check DATABASE_URL, or DATABASE_HOST, DATABASE_NAME,"
        " DATABASE_USER and DATABASE_PASSWORD_FILE. Nothing was checked in the database."
    ),
    "database_unavailable": "The PartFlow database could not be reached.",
    "lock_timeout": (
        "The status query waited more than 5 s for a table lock. A migration may be running."
    ),
    "statement_timeout": "The status query exceeded the statement timeout of {n} s.",
    "database_error": "The status query failed on a database error ({name}).",
    "internal_error": "status failed unexpectedly (internal error).",
}

Result = Literal["ok", "attention", "error"]


@dataclass(frozen=True)
class Finding:
    code: str
    message: str


@dataclass
class RunError:
    code: str
    message: str
    exception: BaseException | None = None


@dataclass
class StatusReport:
    started_at: datetime.datetime
    release_tag: str | None
    release_commit: str | None
    statement_timeout_seconds: int
    max_backup_age_hours: int
    finished_at: datetime.datetime | None = None
    database: dict[str, Any] = field(default_factory=dict)
    movements: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] = field(default_factory=dict)
    backup: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    error: RunError | None = None

    @property
    def result(self) -> Result:
        if self.error is not None:
            return "error"
        return "attention" if self.findings else "ok"

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "attention": 1, "error": 2}[self.result]


def _utc_text(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _database_section(
    status: str = "error",
    *,
    name: str | None = None,
    server_version: str | None = None,
    connected_role: str | None = None,
    size_bytes: int | None = None,
    connections: int | None = None,
    waiting: int | None = None,
    longest_wait_seconds: float | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "name": name,
        "server_version": server_version,
        "connected_role": connected_role,
        "size_bytes": size_bytes,
        "connections": connections,
        "locks": {"waiting": waiting, "longest_wait_seconds": longest_wait_seconds},
    }


def _not_run_movements() -> dict[str, Any]:
    return {"status": "not_run", "rows": None, "total_bytes": None}


def _not_run_schema(accepted_revision: str | None) -> dict[str, Any]:
    return {
        "status": "not_run",
        "readiness": None,
        "expected_revision": None,
        "database_revision": None,
        "accepted_revision": accepted_revision,
    }


# ---------------------------------------------------------------------------
# Database, Movements, schema
# ---------------------------------------------------------------------------


def database_run_error(exc: SQLAlchemyError, statement_timeout_seconds: int) -> RunError:
    """The run-level error of a database failure (the committed S3 classification)."""
    if migration.is_connection_failure(exc):
        return RunError("database_unavailable", ERROR_MESSAGES["database_unavailable"], exc)
    original = getattr(exc, "orig", None)
    if isinstance(original, psycopg.errors.LockNotAvailable):
        return RunError("lock_timeout", ERROR_MESSAGES["lock_timeout"], exc)
    if isinstance(original, psycopg.errors.QueryCanceled):
        message = ERROR_MESSAGES["statement_timeout"].format(n=statement_timeout_seconds)
        return RunError("statement_timeout", message, exc)
    if isinstance(exc, DBAPIError):
        name = type(original).__name__ if original is not None else type(exc).__name__
        return RunError("database_error", ERROR_MESSAGES["database_error"].format(name=name), exc)
    return RunError("internal_error", ERROR_MESSAGES["internal_error"], exc)


def _schema_section(
    connection: Connection,
    report: StatusReport,
    accepted_revision: str | None,
    scripts: tuple[str, frozenset[str]] | None,
) -> None:
    if scripts is None:
        report.schema = _not_run_schema(accepted_revision)
        report.schema["status"] = "error"
        return
    expected, known = scripts
    report.schema = _not_run_schema(accepted_revision)
    report.schema["status"] = "error"
    report.schema["expected_revision"] = expected
    database_revision = schema_revision.read_revision(connection)
    readiness = evaluate(
        expected_revision=expected,
        database_revision=database_revision,
        accepted_revision=accepted_revision,
        known_revisions=known,
    )
    report.schema.update(
        status="finding" if readiness == "mismatch" else "ok",
        readiness=readiness,
        database_revision=database_revision,
    )
    if readiness == "mismatch":
        message = FINDING_MESSAGES["schema_not_ready"].format(
            database_revision=database_revision or "none",
            release=report.release_tag or "unknown",
            expected_revision=expected,
        )
        report.findings.append(Finding("schema_not_ready", message))


def _read_database(
    engine: Engine,
    report: StatusReport,
    accepted_revision: str | None,
    scripts: tuple[str, frozenset[str]] | None,
) -> None:
    """Every database statement in the §3.5 order inside one READ ONLY transaction."""
    seconds = int(report.statement_timeout_seconds)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text(f"SET LOCAL statement_timeout = '{seconds}s'"))
        connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_SECONDS}s'"))
        if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise RuntimeError("the status transaction is not read-only")
        row = connection.execute(
            text(
                "SELECT current_database() AS name,"
                " current_setting('server_version') AS server_version,"
                " current_user AS role,"
                " pg_database_size(current_database()) AS size,"
                " (SELECT count(*) FROM pg_stat_activity"
                "  WHERE datname = current_database()) AS connections"
            )
        ).one()
        report.database = _database_section(
            "error",
            name=str(row.name),
            server_version=str(row.server_version),
            connected_role=str(row.role),
            size_bytes=int(row.size),
            connections=int(row.connections),
        )
        locks = connection.execute(
            text(
                "SELECT count(*) AS waiting, coalesce(extract(epoch FROM"
                " greatest(max(clock_timestamp() - waitstart), interval '0')), 0) AS longest"
                " FROM pg_locks WHERE NOT granted AND database ="
                " (SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        ).one()
        report.database["locks"] = {
            "waiting": int(locks.waiting),
            "longest_wait_seconds": round(float(locks.longest), 1),
        }
        _schema_section(connection, report, accepted_revision, scripts)
        # Last: the only statement that locks a production table (ACCESS SHARE).
        report.movements = {"status": "error", "rows": None, "total_bytes": None}
        movements = connection.execute(text(MOVEMENTS_SQL)).one()
        report.movements = {
            "status": "ok",
            "rows": int(movements[0]),
            "total_bytes": int(movements[1]),
        }
    report.database["status"] = "ok"


def _database_part(engine: Engine, report: StatusReport, accepted_revision: str | None) -> None:
    """The database, schema and Movement sections; the first failure is the run error."""
    scripts = _migration_scripts(report)
    try:
        _read_database(engine, report, accepted_revision, scripts)
    except SQLAlchemyError as exc:
        report.database["status"] = "error"
        if report.error is None:
            report.error = database_run_error(exc, report.statement_timeout_seconds)
    except Exception as exc:
        report.database["status"] = "error"
        if report.error is None:
            report.error = RunError("internal_error", ERROR_MESSAGES["internal_error"], exc)


def _migration_scripts(report: StatusReport) -> tuple[str, frozenset[str]] | None:
    try:
        return schema_revision.code_head(), schema_revision.known_revisions()
    except schema_revision.MigrationScriptsError as exc:
        report.error = RunError("internal_error", ERROR_MESSAGES["internal_error"], exc)
        return None


# ---------------------------------------------------------------------------
# Backup age
# ---------------------------------------------------------------------------


def _lock_section(backup_dir: Path, names: list[str]) -> dict[str, Any]:
    lock: dict[str, Any] = {"present": False, "by": None, "started_at": None, "name": None}
    if BACKUP_LOCK_DIR not in names:
        return lock
    lock["present"] = True
    try:
        content = backup_files.read_bytes(backup_dir / BACKUP_LOCK_DIR / "owner")
        lines = content.decode("utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return lock
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key in _LOCK_OWNER_KEYS and key not in values:
            values[key] = value.strip()[:_MAX_LOCK_VALUE_LENGTH]
    lock["by"] = values.get("by")
    lock["started_at"] = values.get("started_at")
    # backup.sh writes the backup's name, release.sh the release directory.
    lock["name"] = values.get("name", values.get("release"))
    return lock


@dataclass(frozen=True)
class _Published:
    name: backups.BackupName
    raw: dict[str, Any]
    completed_at: datetime.datetime


def _read_manifest(backup_dir: Path, name: backups.BackupName) -> _Published | None:
    """The published backup when its manifest is valid (no digest is computed)."""
    try:
        content = backup_files.read_bytes(backup_dir / name.name / backups.MANIFEST_FILE)
        raw: object = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if backups.manifest_problem(raw, name) is not None or not isinstance(raw, dict):
        return None
    completed = backups.parse_utc(str(raw["completed_at"]))
    if completed is None:
        return None
    return _Published(name, raw, completed)


def _age_hours(completed: datetime.datetime, now: datetime.datetime) -> float:
    return max(0.0, round((now - completed).total_seconds() / 3600, 1))


def _dump_bytes(raw: dict[str, Any]) -> int | None:
    files = raw.get("files")
    if not isinstance(files, list):
        return None
    for entry in files:
        if isinstance(entry, dict) and entry.get("name") == backups.DUMP_FILE:
            size = entry.get("bytes")
            return size if isinstance(size, int) and not isinstance(size, bool) else None
    return None


def backup_section(
    backup_dir: Path | None, max_age_hours: int, now: datetime.datetime
) -> tuple[dict[str, Any], list[Finding]]:
    """The backup section and its findings (``not_applicable`` without a directory)."""
    section: dict[str, Any] = {
        "status": "not_applicable",
        "directory": None,
        "max_age_hours": max_age_hours,
        "latest": None,
        "latest_daily": None,
        "candidates": 0,
        "lock": {"present": False, "by": None, "started_at": None, "name": None},
    }
    if backup_dir is None:
        return section, []
    directory = str(backup_dir)
    section["directory"] = directory
    findings: list[Finding] = []
    try:
        names = backup_files.entry_names(backup_dir)
        candidates = [
            parsed
            for parsed in (backups.parse_name(entry) for entry in names)
            if parsed is not None and backup_files.is_real_directory(backup_dir / parsed.name)
        ]
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        message = FINDING_MESSAGES["backup_dir_unreadable"].format(dir=directory, reason=reason)
        section["status"] = "finding"
        return section, [Finding("backup_dir_unreadable", message)]
    section["lock"] = _lock_section(backup_dir, names)
    section["candidates"] = len(candidates)
    candidates.sort(key=lambda parsed: (parsed.stamp, parsed.name), reverse=True)
    latest: _Published | None = None
    latest_daily: _Published | None = None
    for candidate in candidates:
        published = _read_manifest(backup_dir, candidate)
        if published is None:
            if latest is None:
                message = FINDING_MESSAGES["backup_manifest_unreadable"].format(name=candidate.name)
                findings.append(Finding("backup_manifest_unreadable", message))
            continue
        if latest is None:
            latest = published
        if candidate.kind == "daily":
            latest_daily = published
            break
    if latest is None:
        message = FINDING_MESSAGES["backup_missing"].format(dir=directory)
        findings.append(Finding("backup_missing", message))
    else:
        age = _age_hours(latest.completed_at, now)
        release = latest.raw.get("release")
        revision = latest.raw.get("alembic_revision")
        section["latest"] = {
            "name": latest.name.name,
            "kind": latest.name.kind,
            "completed_at": _utc_text(latest.completed_at),
            "age_hours": age,
            "release_tag": release.get("tag") if isinstance(release, dict) else None,
            "alembic_revision": revision if isinstance(revision, str) else None,
            "dump_bytes": _dump_bytes(latest.raw),
        }
        if latest.completed_at - now > FUTURE_TOLERANCE:
            message = FINDING_MESSAGES["backup_time_in_future"].format(
                name=latest.name.name, completed_at=_utc_text(latest.completed_at)
            )
            findings.append(Finding("backup_time_in_future", message))
        elif age > max_age_hours:
            message = FINDING_MESSAGES["backup_stale"].format(
                name=latest.name.name, age_hours=age, max=max_age_hours
            )
            findings.append(Finding("backup_stale", message))
    if latest_daily is not None:
        section["latest_daily"] = {
            "name": latest_daily.name.name,
            "completed_at": _utc_text(latest_daily.completed_at),
            "age_hours": _age_hours(latest_daily.completed_at, now),
        }
    section["status"] = "finding" if findings else "ok"
    return section, findings


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def collect_status(
    engine: Engine | None,
    *,
    release_tag: str | None,
    release_commit: str | None,
    accepted_revision: str | None,
    backup_dir: Path | None,
    max_backup_age_hours: int = DEFAULT_MAX_BACKUP_AGE_HOURS,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    now: Callable[[], datetime.datetime] | None = None,
) -> StatusReport:
    """Always a complete report; ``engine`` None = the configuration is invalid."""
    clock = now or (lambda: datetime.datetime.now(datetime.UTC))
    report = StatusReport(
        started_at=clock(),
        release_tag=release_tag,
        release_commit=release_commit,
        statement_timeout_seconds=statement_timeout_seconds,
        max_backup_age_hours=max_backup_age_hours,
    )
    report.database = _database_section("error")
    report.movements = _not_run_movements()
    report.schema = _not_run_schema(accepted_revision)
    if engine is None:
        report.error = RunError("configuration_invalid", ERROR_MESSAGES["configuration_invalid"])
    else:
        _database_part(engine, report, accepted_revision)
    try:
        report.backup, backup_findings = backup_section(backup_dir, max_backup_age_hours, clock())
    except Exception as exc:
        # An unexpected failure still yields a complete report (exit 2).
        report.backup, backup_findings = backup_section(None, max_backup_age_hours, clock())
        if report.error is None:
            report.error = RunError("internal_error", ERROR_MESSAGES["internal_error"], exc)
    report.findings.extend(backup_findings)
    report.findings.sort(key=lambda finding: (finding.code, finding.message))
    report.finished_at = clock()
    return report


def status_document(report: StatusReport) -> dict[str, object]:
    """The §4.4 JSON document, keys in their fixed order."""
    finished = report.finished_at or report.started_at
    duration = int((finished - report.started_at).total_seconds() * 1000)
    return {
        "report_version": REPORT_VERSION,
        "command": "status",
        "result": report.result,
        "exit_code": report.exit_code,
        "started_at": _utc_text(report.started_at),
        "finished_at": _utc_text(finished),
        "duration_ms": max(0, duration),
        "release": {"tag": report.release_tag, "commit": report.release_commit},
        "database": report.database,
        "movements": report.movements,
        "schema": report.schema,
        "backup": report.backup,
        "archival": {
            "status": "not_applicable",
            "reason": ARCHIVAL_REASON,
            "awaiting_approval": None,
        },
        "findings": [
            {"code": finding.code, "message": finding.message} for finding in report.findings
        ],
        "error": (
            None
            if report.error is None
            else {"code": report.error.code, "message": report.error.message}
        ),
    }


def status_summary(report: StatusReport) -> str:
    """The one stderr line."""
    if report.error is not None:
        return f"status: ERROR — {report.error.code}"
    if report.findings:
        codes = sorted({finding.code for finding in report.findings})
        return f"status: ATTENTION — {', '.join(codes)}"
    size = report.database.get("size_bytes") or 0
    rows = report.movements.get("rows")
    latest = report.backup.get("latest")
    age = latest["age_hours"] if isinstance(latest, dict) else "n/a"
    return (
        f"status: ok (database {size / _MIB:.1f} MiB, {rows} Movements, schema"
        f" {report.schema.get('readiness')}, backup {age} h old)"
    )
