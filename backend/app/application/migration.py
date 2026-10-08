"""The release ``migrate`` and ``revision`` use cases (Phase 16 slice 3; CD4).

``run_migrate`` applies this image's pending Alembic revisions on ONE
connection in ONE transaction, in this order (every refusal before the
upgrade leaves the database untouched):

1. ``lock_timeout`` for the transaction, then the ``partflow:migrate``
   advisory transaction lock (not granted → ``migrate_running``). The
   lowercase key can never equal a Part Number lock's input, and no
   application transaction takes it.
2. The database revision; a revision this release does not know →
   ``revision_unknown``.
3. When revisions are pending: a pending revision that needs autocommit
   or ``CONCURRENTLY`` → ``non_transactional_migration``; a session of
   the API (``application_name = 'partflow-api'``) connected to this
   database → ``backend_connected`` (a best-effort guard; the write
   freeze itself is stopping ``backend``).
4. The upgrade inside the open transaction (``alembic/env.py`` adopts the
   connection and never commits it).
5. ``apply_grants`` — the P16-S4 hook, a recorded no-op until then; it
   runs on every successful run, pending or not.
6. The revision re-read inside the transaction must be the code head.
7. COMMIT. A failure during COMMIT itself is ``outcome_unknown``: the
   migration may or may not have been applied.

``revision_report`` is read-only (one READ ONLY transaction): the
release identity, the expected and database revisions, the pending and
non-transactional revisions and what the backend's readiness would answer
under the configured override.

Both return complete reports (also on refusal and failure) that the CLI
prints as one JSON document; ``backend`` never migrates.
"""

import datetime
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Final, Literal

import psycopg.errors
from sqlalchemy import Connection, Engine, RootTransaction, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError

from app.application.readiness import SchemaState, evaluate, override_ignored
from app.infrastructure import schema_revision

logger = logging.getLogger(__name__)

REPORT_VERSION: Final = 1
DEFAULT_LOCK_TIMEOUT_SECONDS: Final = 30
MAX_TEXT_LENGTH: Final = 500
API_APPLICATION_NAME: Final = schema_revision.API_APPLICATION_NAME
_MIGRATE_LOCK: Final = "partflow:migrate"
_BACKUP_VERIFICATION: Final = "pending: backup-verify arrives with P16-S5"
_GRANTS_NOT_INSTALLED: Final = "Database-role hardening is not installed yet (P16-S4)."

MIGRATE_MESSAGES: Final = {
    "migrate_running": "Another migrate is running on this database. Nothing was changed.",
    "revision_unknown": (
        "The database is at revision {revision}, which this release does not know (it is"
        " newer, or from another branch). Nothing was changed. Use the release that created"
        " it, or follow the rollback decision tree."
    ),
    "non_transactional_migration": (
        "Revision {revision} contains a statement that cannot run inside the migrate"
        " transaction (autocommit_block or CONCURRENTLY). It needs its own documented"
        " procedure. Nothing was changed."
    ),
    "backend_connected": (
        "The PartFlow backend is still connected to this database. Stop it first (the write"
        " freeze), then run migrate again. Nothing was changed."
    ),
    "database_unavailable": "PartFlow could not reach its database. Nothing was changed.",
    "lock_not_available": (
        "The migration waited too long for a table lock (another session holds it, for"
        " example a reconciliation or a backup). Nothing was changed. Run migrate again when"
        " it has finished."
    ),
    "migration_failed": (
        "The migration failed and was rolled back. Nothing was changed. The cause is in the"
        " log above."
    ),
    "internal_error": "migrate failed unexpectedly (internal error). Nothing was changed.",
    "outcome_unknown": (
        "The database connection failed while committing the migration: it may or may not"
        ' have been applied. Run "python -m app.cli revision" before doing anything else.'
    ),
}
REVISION_MESSAGES: Final = {
    "database_unavailable": "PartFlow could not reach its database.",
    "internal_error": "revision failed unexpectedly (internal error).",
}
_REFUSALS: Final = frozenset(
    {"migrate_running", "revision_unknown", "non_transactional_migration", "backend_connected"}
)

MigrateResult = Literal["upgraded", "already_current", "refused", "failed", "outcome_unknown"]
RevisionState = Literal[
    "current", "upgrade_available", "unknown_revision", "blocked_non_transactional"
]
_Phase = Literal["checks", "upgrade", "grants", "verify"]

#: The SQLSTATEs (besides class 08, connection exception) psycopg raises
#: as ``OperationalError`` that mean the connection is gone or was refused:
#: admin/crash shutdown, cannot connect now, too many connections. Every
#: other ``OperationalError`` SQLSTATE (a statement refusal such as a lock
#: or statement timeout or a deadlock, or a statement failure such as a
#: full disk or a program limit) leaves the database reachable.
_CONNECTION_SQLSTATES: Final = frozenset({"57P01", "57P02", "57P03", "53300"})


def operator_text(value: str) -> str:
    """A backup reference or reason: trimmed, 1-500 characters, no control character."""
    trimmed = value.strip()
    if not 1 <= len(trimmed) <= MAX_TEXT_LENGTH:
        raise ValueError(f"must be 1 to {MAX_TEXT_LENGTH} characters after trimming")
    if any(unicodedata.category(character) == "Cc" for character in trimmed):
        raise ValueError("must not contain control characters")
    return trimmed


@dataclass(frozen=True)
class Backup:
    """Exactly one of a pre-release backup reference or a no-backup reason."""

    reference: str | None = None
    no_backup_reason: str | None = None

    def document(self) -> dict[str, object]:
        if self.reference is not None:
            return {
                "kind": "reference",
                "reference": self.reference,
                "verified": False,
                "verification": _BACKUP_VERIFICATION,
            }
        return {"kind": "none", "reason": self.no_backup_reason}


@dataclass(frozen=True)
class RunError:
    code: str
    message: str
    exception: BaseException | None = None


@dataclass
class MigrateReport:
    started_at: datetime.datetime
    release: str | None
    commit: str | None
    backup: Backup
    result: MigrateResult = "failed"
    finished_at: datetime.datetime | None = None
    expected_revision: str | None = None
    revision_before: str | None = None
    revision_after: str | None = None
    applied_revisions: list[str] = field(default_factory=list)
    grants: dict[str, str] | None = None
    error: RunError | None = None

    @property
    def exit_code(self) -> int:
        if self.result in ("upgraded", "already_current"):
            return 0
        return 1 if self.result == "refused" else 2


@dataclass
class RevisionReport:
    release: str | None
    commit: str | None
    accepted_revision: str | None
    state: RevisionState | None = None
    expected_revision: str | None = None
    database_revision: str | None = None
    override_ignored: bool | None = None
    readiness: SchemaState | None = None
    pending_revisions: list[str] = field(default_factory=list)
    non_transactional_revisions: list[str] = field(default_factory=list)
    error: RunError | None = None

    @property
    def exit_code(self) -> int:
        if self.error is not None or self.state is None:
            return 2
        return 0 if self.state == "current" else 1


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _duration_ms(report: MigrateReport) -> int:
    finished = report.finished_at or report.started_at
    return int((finished - report.started_at).total_seconds() * 1000)


def _error_document(error: RunError | None) -> dict[str, str] | None:
    return None if error is None else {"code": error.code, "message": error.message}


def migrate_document(report: MigrateReport) -> dict[str, object]:
    """The JSON document of one migrate run (keys in the contract order)."""
    return {
        "report_version": REPORT_VERSION,
        "command": "migrate",
        "result": report.result,
        "exit_code": report.exit_code,
        "started_at": _iso(report.started_at),
        "finished_at": _iso(report.finished_at),
        "duration_ms": _duration_ms(report),
        "release": report.release,
        "commit": report.commit,
        "expected_revision": report.expected_revision,
        "revision_before": report.revision_before,
        "revision_after": report.revision_after,
        "applied_revisions": list(report.applied_revisions),
        "backup": report.backup.document(),
        "grants": report.grants,
        "error": _error_document(report.error),
    }


def migrate_summary(report: MigrateReport) -> str:
    milliseconds = _duration_ms(report)
    if report.result == "upgraded":
        return (
            f"migrate: upgraded {report.revision_before or 'empty database'} ->"
            f" {report.revision_after} ({len(report.applied_revisions)} revisions)"
            f" in {milliseconds} ms"
        )
    if report.result == "already_current":
        return f"migrate: already at {report.revision_after}; nothing to apply ({milliseconds} ms)"
    message = report.error.message if report.error is not None else report.result
    return f"migrate: {message}"


def revision_document(report: RevisionReport) -> dict[str, object]:
    """The JSON document of one revision run (keys in the contract order)."""
    return {
        "report_version": REPORT_VERSION,
        "command": "revision",
        "state": report.state,
        "exit_code": report.exit_code,
        "release": report.release,
        "commit": report.commit,
        "expected_revision": report.expected_revision,
        "database_revision": report.database_revision,
        "accepted_revision": report.accepted_revision,
        "override_ignored": report.override_ignored,
        "readiness": report.readiness,
        "pending_revisions": list(report.pending_revisions),
        "non_transactional_revisions": list(report.non_transactional_revisions),
        "error": _error_document(report.error),
    }


def revision_summary(report: RevisionReport) -> str:
    if report.error is not None:
        return f"revision: {report.error.message}"
    return (
        f"revision: {report.state} (database {report.database_revision or 'empty'},"
        f" release head {report.expected_revision}, readiness {report.readiness})"
    )


def apply_grants(connection: Connection) -> dict[str, str]:
    """The P16-S4 database-role hook: runs in the migrate transaction; a no-op until S4."""
    return {"status": "not_applicable", "detail": _GRANTS_NOT_INSTALLED}


def commit_transaction(transaction: RootTransaction) -> None:
    transaction.commit()


def _is_lock_timeout(exc: BaseException) -> bool:
    return isinstance(exc, DBAPIError) and isinstance(exc.orig, psycopg.errors.LockNotAvailable)


def _is_connection_failure(exc: BaseException) -> bool:
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    if isinstance(exc, InterfaceError):
        return True
    if not isinstance(exc, OperationalError):
        return False
    # A client-side failure (refused, closed by the server) carries no SQLSTATE.
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return sqlstate is None or sqlstate.startswith("08") or sqlstate in _CONNECTION_SQLSTATES


def _discard(transaction: RootTransaction) -> None:
    """Roll back; on a lost connection the server discards the transaction itself."""
    try:
        transaction.rollback()
    except SQLAlchemyError as exc:
        logger.warning("migrate: rollback failed (%s); the server discards it", type(exc).__name__)


class _Refused(Exception):
    def __init__(self, code: str, **values: str) -> None:
        super().__init__(code)
        self.code = code
        self.values = values


class _RevisionNotReachedError(Exception):
    """The re-read revision is not the code head after the upgrade."""


def migrate_failure(
    report: MigrateReport,
    code: str,
    *,
    message: str | None = None,
    exception: BaseException | None = None,
) -> MigrateReport:
    """Finish ``report`` as a refusal or failure: nothing was applied."""
    report.result = "refused" if code in _REFUSALS else "failed"
    report.error = RunError(code, message or MIGRATE_MESSAGES[code], exception)
    report.revision_after = report.revision_before
    report.applied_revisions = []
    report.finished_at = _now()
    return report


def _migrate_in_transaction(
    connection: Connection,
    report: MigrateReport,
    known: frozenset[str],
    lock_timeout_seconds: int,
    phase: list[_Phase],
) -> None:
    """Steps 1-6 inside the open transaction; raises ``_Refused`` for a refusal."""
    connection.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{lock_timeout_seconds}s"},
    )
    granted = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _MIGRATE_LOCK},
    ).scalar_one()
    if not granted:
        raise _Refused("migrate_running")
    current = schema_revision.read_revision(connection)
    report.revision_before = current
    if current is not None and current not in known:
        raise _Refused("revision_unknown", revision=current)
    pending = schema_revision.pending_revisions(current)
    if pending:
        blocked = schema_revision.non_transactional_revisions(pending)
        if blocked:
            raise _Refused("non_transactional_migration", revision=blocked[0])
        connected = connection.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND pid <> pg_backend_pid() AND application_name = :name"
            ),
            {"name": API_APPLICATION_NAME},
        ).scalar_one()
        if connected:
            raise _Refused("backend_connected")
        phase[0] = "upgrade"
        schema_revision.upgrade_to_head(connection)
    phase[0] = "grants"
    report.grants = apply_grants(connection)
    phase[0] = "verify"
    after = schema_revision.read_revision(connection)
    if after != report.expected_revision:
        raise _RevisionNotReachedError(
            f"the database is at {after} after the upgrade, not {report.expected_revision}"
        )
    report.applied_revisions = pending
    report.revision_after = after


def _failure_code(exc: BaseException, phase: _Phase) -> str:
    if _is_lock_timeout(exc):
        return "lock_not_available"
    if isinstance(exc, SQLAlchemyError) and _is_connection_failure(exc):
        return "database_unavailable"
    if phase == "upgrade" or (phase == "grants" and isinstance(exc, SQLAlchemyError)):
        return "migration_failed"
    return "internal_error"


def run_migrate(
    engine: Engine,
    *,
    backup: Backup,
    lock_timeout_seconds: int,
    release: str | None,
    commit: str | None,
    started_at: datetime.datetime | None = None,
) -> MigrateReport:
    """One migrate run; always a complete report."""
    report = MigrateReport(
        started_at=started_at or _now(), release=release, commit=commit, backup=backup
    )
    try:
        report.expected_revision = schema_revision.code_head()
        known = schema_revision.known_revisions()
    except schema_revision.MigrationScriptsError as exc:
        return migrate_failure(report, "internal_error", exception=exc)
    try:
        connection = engine.connect()
    except SQLAlchemyError as exc:
        return migrate_failure(report, "database_unavailable", exception=exc)
    try:
        transaction = connection.begin()
        phase: list[_Phase] = ["checks"]
        try:
            _migrate_in_transaction(connection, report, known, lock_timeout_seconds, phase)
        except _Refused as refusal:
            _discard(transaction)
            message = MIGRATE_MESSAGES[refusal.code].format(**refusal.values)
            return migrate_failure(report, refusal.code, message=message)
        except Exception as exc:
            # Every failure before COMMIT rolls the whole run back.
            _discard(transaction)
            return migrate_failure(report, _failure_code(exc, phase[0]), exception=exc)
        try:
            commit_transaction(transaction)
        except Exception as exc:
            # COMMIT itself failed: the outcome is unknown, never "nothing changed".
            report.result = "outcome_unknown"
            report.error = RunError("outcome_unknown", MIGRATE_MESSAGES["outcome_unknown"], exc)
            report.revision_after = report.revision_before
            report.finished_at = _now()
            return report
    finally:
        connection.close()
    report.result = "upgraded" if report.applied_revisions else "already_current"
    report.finished_at = _now()
    return report


def revision_report(
    engine: Engine,
    *,
    release: str | None,
    commit: str | None,
    accepted_revision: str | None,
) -> RevisionReport:
    """The read-only revision state; always a complete report."""
    report = RevisionReport(release=release, commit=commit, accepted_revision=accepted_revision)
    try:
        expected = schema_revision.code_head()
        known = schema_revision.known_revisions()
    except schema_revision.MigrationScriptsError as exc:
        report.error = RunError("internal_error", REVISION_MESSAGES["internal_error"], exc)
        return report
    report.expected_revision = expected
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SELECT set_config('lock_timeout', '5s', true)"))
            connection.execute(text("SELECT set_config('statement_timeout', '30s', true)"))
            database = schema_revision.read_revision(connection)
    except SQLAlchemyError as exc:
        code = "database_unavailable" if _is_connection_failure(exc) else "internal_error"
        report.error = RunError(code, REVISION_MESSAGES[code], exc)
        return report
    report.database_revision = database
    report.override_ignored = override_ignored(accepted_revision, known)
    report.readiness = evaluate(
        expected_revision=expected,
        database_revision=database,
        accepted_revision=accepted_revision,
        known_revisions=known,
    )
    if database == expected:
        report.state = "current"
    elif database is not None and database not in known:
        report.state = "unknown_revision"
    else:
        report.pending_revisions = schema_revision.pending_revisions(database)
        report.non_transactional_revisions = schema_revision.non_transactional_revisions(
            report.pending_revisions
        )
        report.state = (
            "blocked_non_transactional"
            if report.non_transactional_revisions
            else "upgrade_available"
        )
    return report
