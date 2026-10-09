"""The release ``migrate`` and ``revision`` use cases (Phase 16 slice 3; CD4).

``run_migrate`` applies this image's pending Alembic revisions on ONE
connection in ONE transaction, in this order (every refusal before the
upgrade leaves the database untouched):

0. This image's code head and known revisions; then, with
   ``--pre-release-backup NAME`` and before any connection (step 0b,
   Phase 16 slice 5): the backup directory ``<backup-dir>/NAME`` passes
   every ``backup-verify`` check and is fresh — started at or after
   ``--backup-not-before``, or at most ``--max-backup-age-minutes`` old.
1. ``lock_timeout`` for the transaction, then the ``partflow:migrate``
   advisory transaction lock (not granted → ``migrate_running``). The
   lowercase key can never equal a Part Number lock's input, and no
   application transaction takes it.
2. The database revision; a revision this release does not know →
   ``revision_unknown``. Then (step 2b, with a pre-release backup): the
   backup holds exactly this revision (an anomalous ``alembic_version``
   never matches) of this database, and a pending migration needs the
   not-before proof (an age limit cannot prove that the dump holds every
   write committed before the freeze → ``backup_freshness_unproven``).
3. When revisions are pending: a pending revision that needs autocommit
   or ``CONCURRENTLY`` → ``non_transactional_migration``; a session of
   the API (``application_name = 'partflow-api'``) connected to this
   database → ``backend_connected`` (a best-effort guard; the write
   freeze itself is stopping ``backend``).
4. The upgrade inside the open transaction (``alembic/env.py`` adopts the
   connection and never commits it).
5. ``apply_grants`` — the database-role grants (Phase 16 slice 4,
   ``database_roles.apply_grants``); it runs on every run that reaches
   it, pending or not. A refusal (roles missing or unsafe, a table
   without a class, a foreign grantor) rolls the upgrade back too.
6. The revision re-read inside the transaction must be the code head.
7. COMMIT. A failure during COMMIT itself is ``outcome_unknown``: the
   migration may or may not have been applied.

``revision_report`` is read-only (one READ ONLY transaction): the
release identity, the expected and database revisions, the pending and
non-transactional revisions and what the backend's readiness would answer
under the configured override.

Both return complete reports (also on refusal and failure) that the CLI
prints as one JSON document; ``backend`` never migrates.

``run_provision_roles`` and ``run_apply_grants`` (Phase 16 slice 4) are the
standalone ``provision-roles`` and ``apply-grants`` commands over
``app.application.database_roles``: one connection, one transaction, the
same failure classification and report conventions as ``migrate``.
"""

import datetime
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, NoReturn

import psycopg.errors
from sqlalchemy import Connection, Engine, RootTransaction, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError

from app.application import backups, database_roles
from app.application.readiness import SchemaState, evaluate, override_ignored
from app.core.config import SecretFileError, get_settings, read_secret_line
from app.infrastructure import schema_revision

logger = logging.getLogger(__name__)

REPORT_VERSION: Final = 1
DEFAULT_LOCK_TIMEOUT_SECONDS: Final = 30
MAX_TEXT_LENGTH: Final = 500
API_APPLICATION_NAME: Final = schema_revision.API_APPLICATION_NAME
_MIGRATE_LOCK: Final = "partflow:migrate"

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
    "backup_not_found": (
        "The pre-release backup {reference} was not found in {dir}. Name a backup created by"
        " backup.sh (for example 20261008T140000Z-pre-release-v1.0.0-rc.2). Nothing was"
        " changed."
    ),
    "backup_invalid": (
        "The pre-release backup {reference} failed verification ({check}: {detail}). Do not use"
        " it; take a new backup. Nothing was changed."
    ),
    "backup_unreadable": (
        "The pre-release backup {reference} could not be read ({reason}). Nothing was changed."
    ),
    "backup_stale": (
        "The pre-release backup {reference} was started at {started}, before {not_before}: it"
        " may miss changes made since. Take a new backup inside the write freeze. Nothing was"
        " changed."
    ),
    "backup_stale_age": (
        "The pre-release backup {reference} is {age} minutes old (the limit is {max} minutes):"
        " it may miss recent changes. Take a new backup inside the write freeze, or name the"
        " freeze time with --backup-not-before. Nothing was changed."
    ),
    "backup_revision_mismatch": (
        "The pre-release backup {reference} holds database revision {backup_revision}, but the"
        " database is at {revision}: it is not a backup of the current database. Nothing was"
        " changed."
    ),
    "backup_revision_anomalous": (
        "The pre-release backup {reference} holds an unusual alembic_version ({n} rows), so it"
        " cannot be matched to database revision {revision}. Nothing was changed."
    ),
    "backup_freshness_unproven": (
        "A migration is pending: name the write-freeze time with --backup-not-before. Nothing"
        " was changed."
    ),
    "backup_database_mismatch": (
        "The pre-release backup {reference} is of database {backup_database}, not of"
        " {database}. Nothing was changed."
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
#: The database-role refusals of the grants step (their copy comes from
#: ``database_roles.GRANT_MESSAGES``, Phase 16 slice 4).
_GRANT_REFUSALS: Final = frozenset(
    {
        "roles_not_provisioned",
        "roles_incomplete",
        "role_unsafe",
        "role_owns_objects",
        "foreign_grantor",
        "table_unclassified",
        "table_missing",
        "not_superuser",
    }
)
#: The pre-release backup refusals (Phase 16 slice 5); ``backup_unreadable``
#: is a failure (exit 2), never a refusal.
_BACKUP_REFUSALS: Final = frozenset(
    {
        "backup_not_found",
        "backup_invalid",
        "backup_stale",
        "backup_revision_mismatch",
        "backup_freshness_unproven",
        "backup_database_mismatch",
    }
)
_REFUSALS: Final = (
    frozenset(
        {"migrate_running", "revision_unknown", "non_transactional_migration", "backend_connected"}
    )
    | _GRANT_REFUSALS
    | _BACKUP_REFUSALS
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


@dataclass
class Backup:
    """Exactly one of a pre-release backup name or a no-backup reason.

    For a backup name: where to find it, the freshness rule (``not_before``,
    else ``max_age_minutes``), and what its verification found
    (``verification`` is ``passed``, the refusal code, or ``not_run``).
    """

    reference: str | None = None
    no_backup_reason: str | None = None
    backup_dir: Path = backups.DEFAULT_BACKUP_DIR
    not_before: datetime.datetime | None = None
    max_age_minutes: int = backups.DEFAULT_MAX_AGE_MINUTES
    verified: bool = False
    verification: str = "not_run"
    manifest: backups.Manifest | None = None
    manifest_sha256: str | None = None
    freshness: backups.Freshness | None = None
    warnings: list[str] = field(default_factory=list)

    def document(self) -> dict[str, object]:
        if self.reference is None:
            return {"kind": "none", "reason": self.no_backup_reason}
        manifest = self.manifest
        return {
            "kind": "reference",
            "reference": self.reference,
            "verified": self.verified,
            "verification": self.verification,
            "dump_started_at": (
                backups.utc_text(manifest.dump_started_at) if manifest is not None else None
            ),
            "alembic_revision": manifest.alembic_revision if manifest is not None else None,
            "database": manifest.database_name if manifest is not None else None,
            "release": manifest.release_tag if manifest is not None else None,
            "manifest_sha256": self.manifest_sha256,
            "freshness": self.freshness.document() if self.freshness is not None else None,
            "warnings": list(self.warnings),
        }


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
    grants: dict[str, object] | None = None
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


def apply_grants(connection: Connection) -> dict[str, object]:
    """The database-role hook (step 5): the grants, inside the migrate transaction.

    Without the PartFlow database roles and with ``DATABASE_ROLES_REQUIRED``
    off (development, test) it changes nothing and says so.
    """
    return database_roles.apply_grants(
        connection,
        roles=database_roles.configured_roles(),
        required=get_settings().database_roles_required,
    )


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
    def __init__(self, code: str, *, message: str | None = None, **values: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.values = values

    def render(self) -> str:
        return self.message or MIGRATE_MESSAGES[self.code].format(**self.values)


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
    # Rolled back: no grant of this run survives.
    report.grants = None
    report.finished_at = _now()
    return report


def _refuse_backup(
    backup: Backup, code: str, message_key: str | None = None, **values: str
) -> NoReturn:
    """Record a refusal of the pre-release backup and raise it."""
    backup.verified = False
    backup.verification = code
    message = MIGRATE_MESSAGES[message_key or code].format(reference=backup.reference, **values)
    raise _Refused(code, message=message)


def _read_failure_reason(exception: BaseException | None) -> str:
    if isinstance(exception, OSError) and exception.strerror:
        return exception.strerror
    return type(exception).__name__ if exception is not None else "unknown"


def _verify_backup(backup: Backup, now: datetime.datetime) -> None:
    """Step 0b (no database): the named backup is verified and fresh; raises ``_Refused``."""
    assert backup.reference is not None
    location = {"dir": str(backup.backup_dir)}
    if backups.parse_name(backup.reference) is None:
        _refuse_backup(backup, "backup_not_found", **location)
    result = backups.verify_backup(backup.backup_dir, backup.reference)
    if result.error is not None and result.error.code == "internal_error":
        assert result.error.exception is not None
        raise result.error.exception
    backup.manifest_sha256 = result.manifest_sha256
    if result.error is not None and result.error.code == "io_error":
        reason = _read_failure_reason(result.error.exception)
        _refuse_backup(backup, "backup_unreadable", reason=reason)
    if result.check("directory").detail == "not found":
        _refuse_backup(backup, "backup_not_found", **location)
    failure = result.first_failure()
    if failure is not None or result.manifest is None:
        check, detail = failure or ("manifest", "unreadable")
        _refuse_backup(backup, "backup_invalid", check=check, detail=detail)
    manifest = result.manifest
    backup.manifest = manifest
    if manifest.anomalous:
        rows = manifest.alembic_rows or []
        backup.warnings.append(backups.alembic_warning(backups.AlembicData(None, rows, len(rows))))
    if manifest.extra_tables:
        backup.warnings.append(backups.extra_tables_warning(manifest.extra_tables))
    backup.warnings.sort()
    try:
        backup.freshness = backups.check_freshness(
            manifest.dump_started_at,
            not_before=backup.not_before,
            max_age_minutes=backup.max_age_minutes,
            now=now,
        )
    except backups.StaleBackupError as stale:
        backup.freshness = stale.freshness
        started = backups.utc_text(stale.started)
        if stale.freshness.not_before is not None:
            not_before = backups.utc_text(stale.freshness.not_before)
            _refuse_backup(backup, "backup_stale", started=started, not_before=not_before)
        _refuse_backup(
            backup,
            "backup_stale",
            "backup_stale_age",
            age=str(stale.freshness.age_minutes),
            max=str(backup.max_age_minutes),
        )
    backup.verified = True
    backup.verification = "passed"


def _match_backup(
    connection: Connection, backup: Backup, current: str | None, pending: list[str]
) -> None:
    """Step 2b (inside the transaction): the backup is of this database at this revision."""
    manifest = backup.manifest
    assert manifest is not None
    revision = current or "none"
    if manifest.anomalous:
        _refuse_backup(
            backup,
            "backup_revision_mismatch",
            "backup_revision_anomalous",
            n=str(len(manifest.alembic_rows or [])),
            revision=revision,
        )
    if manifest.alembic_revision != current:
        _refuse_backup(
            backup,
            "backup_revision_mismatch",
            backup_revision=manifest.alembic_revision or "none",
            revision=revision,
        )
    database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if manifest.database_name != database:
        _refuse_backup(
            backup,
            "backup_database_mismatch",
            backup_database=manifest.database_name,
            database=database,
        )
    freshness = backup.freshness
    if pending and (freshness is None or freshness.rule != "not_before"):
        _refuse_backup(backup, "backup_freshness_unproven")


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
    if report.backup.reference is not None:
        _match_backup(connection, report.backup, current, pending)
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
    if backup.reference is not None:
        try:
            _verify_backup(backup, _now())
        except _Refused as refusal:
            # Nothing is connected yet: the database is untouched.
            return migrate_failure(report, refusal.code, message=refusal.render())
        except Exception as exc:
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
            return migrate_failure(report, refusal.code, message=refusal.render())
        except database_roles.DatabaseRolesRefusal as refusal:
            # The grants step refused: the upgrade of this run is discarded too.
            _discard(transaction)
            return migrate_failure(report, refusal.code, message=refusal.message)
        except Exception as exc:
            # Every failure before COMMIT rolls the whole run back.
            _discard(transaction)
            return migrate_failure(report, _failure_code(exc, phase[0]), exception=exc)
        try:
            commit_transaction(transaction)
        except Exception as exc:
            # COMMIT itself failed: the outcome is unknown, never "nothing changed". The grants ran
            # in the same transaction, so their outcome is unknown too (as in run_apply_grants).
            report.result = "outcome_unknown"
            report.error = RunError("outcome_unknown", MIGRATE_MESSAGES["outcome_unknown"], exc)
            report.revision_after = report.revision_before
            report.grants = None
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


# ---------------------------------------------------------------------------
# provision-roles and apply-grants (Phase 16 slice 4)
# ---------------------------------------------------------------------------

#: A role password: 16-128 printable ASCII characters without spaces (OD-S4-7).
_ROLE_PASSWORD: Final = re.compile(r"[\x21-\x7e]{16,128}")
_SECRET_FILE_PROBLEMS: Final = {
    "not_utf8": "is not UTF-8 text",
    "empty": "is empty",
    "multiline": "must hold exactly one line",
}
_PASSWORD_RULE: Final = "must hold 16 to 128 printable ASCII characters without spaces"

PROVISION_RUN_MESSAGES: Final = {
    "password_file_invalid": "The password file of {role} ({path}) {problem}. Nothing was changed.",
    "passwords_equal": (
        "{app} and {maintenance} must have different passwords. Nothing was changed."
    ),
    "database_unavailable": "PartFlow could not reach its database. Nothing was changed.",
    "lock_not_available": (
        "provision-roles waited too long for a lock. Nothing was changed. Run it again."
    ),
    "internal_error": "provision-roles failed unexpectedly (internal error). Nothing was changed.",
    "outcome_unknown": (
        "The database connection failed while committing: the database roles may or may not"
        " have been changed. Run provision-roles again (it is safe to repeat)."
    ),
}
GRANT_RUN_MESSAGES: Final = {
    "migrate_running": (
        "A migrate or another apply-grants is running on this database. Nothing was changed."
    ),
    "revision_mismatch": (
        "The database is at revision {database}, but this release grants for revision"
        " {expected}. Run apply-grants with the release that matches the database, or migrate"
        " first. Nothing was changed."
    ),
    "database_unavailable": "PartFlow could not reach its database. Nothing was changed.",
    "lock_not_available": (
        "apply-grants waited too long for a lock (another session holds it). Nothing was"
        " changed. Run it again."
    ),
    "internal_error": "apply-grants failed unexpectedly (internal error). Nothing was changed.",
    "outcome_unknown": (
        "The database connection failed while committing: the grants may or may not have"
        " been applied. Run apply-grants again (it is safe to repeat)."
    ),
}
_ROLE_RUN_REFUSALS: Final = frozenset(
    {
        "migrate_running",
        "revision_mismatch",
        "provision_running",
        "role_name_conflict",
        "concurrent_change",
        *_GRANT_REFUSALS,
    }
)

ProvisionResult = Literal["provisioned", "refused", "failed", "outcome_unknown"]
GrantsResult = Literal["applied", "refused", "failed", "outcome_unknown"]


class PasswordFileError(Exception):
    """A role password file is unusable; nothing was read from the database."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def read_role_password(path: Path, role: str) -> str:
    """One role password file: one UTF-8 line of 16-128 printable ASCII characters.

    The message names the role and the path, never the content.
    """
    try:
        password = read_secret_line(path)
    except SecretFileError as exc:
        problem = (
            f"cannot be read ({exc.reason})"
            if exc.kind == "unreadable"
            else _SECRET_FILE_PROBLEMS[exc.kind]
        )
        message = PROVISION_RUN_MESSAGES["password_file_invalid"].format(
            role=role, path=path, problem=problem
        )
        raise PasswordFileError("password_file_invalid", message) from None
    if _ROLE_PASSWORD.fullmatch(password) is None:
        message = PROVISION_RUN_MESSAGES["password_file_invalid"].format(
            role=role, path=path, problem=_PASSWORD_RULE
        )
        raise PasswordFileError("password_file_invalid", message)
    return password


def read_role_passwords(
    roles: database_roles.DatabaseRoles, app_file: Path, maintenance_file: Path
) -> database_roles.DatabaseRoles:
    """Both role passwords, validated before any connection is opened."""
    passwords = database_roles.DatabaseRoles(
        read_role_password(app_file, roles.app),
        read_role_password(maintenance_file, roles.maintenance),
    )
    if passwords.app == passwords.maintenance:
        message = PROVISION_RUN_MESSAGES["passwords_equal"].format(
            app=roles.app, maintenance=roles.maintenance
        )
        raise PasswordFileError("passwords_equal", message)
    return passwords


def _role_run_result(code: str) -> Literal["refused", "failed"]:
    return "refused" if code in _ROLE_RUN_REFUSALS else "failed"


def _exit_code(result: str, success: str) -> int:
    if result == success:
        return 0
    return 1 if result == "refused" else 2


@dataclass
class ProvisionReport:
    started_at: datetime.datetime
    result: ProvisionResult = "failed"
    finished_at: datetime.datetime | None = None
    owner_role: str | None = None
    roles: list[dict[str, object]] = field(default_factory=list)
    error: RunError | None = None

    @property
    def exit_code(self) -> int:
        return _exit_code(self.result, "provisioned")


@dataclass
class GrantsReport:
    started_at: datetime.datetime
    release: str | None
    result: GrantsResult = "failed"
    finished_at: datetime.datetime | None = None
    expected_revision: str | None = None
    database_revision: str | None = None
    grants: dict[str, object] | None = None
    error: RunError | None = None

    @property
    def exit_code(self) -> int:
        return _exit_code(self.result, "applied")


def _role_duration_ms(report: ProvisionReport | GrantsReport) -> int:
    finished = report.finished_at or report.started_at
    return int((finished - report.started_at).total_seconds() * 1000)


def provision_failure(
    report: ProvisionReport,
    code: str,
    message: str,
    *,
    exception: BaseException | None = None,
) -> ProvisionReport:
    """Finish ``report`` as a refusal or failure: nothing was changed."""
    report.result = _role_run_result(code)
    report.roles = []
    report.error = RunError(code, message, exception)
    report.finished_at = _now()
    return report


def grants_failure(
    report: GrantsReport,
    code: str,
    message: str,
    *,
    exception: BaseException | None = None,
) -> GrantsReport:
    """Finish ``report`` as a refusal or failure: nothing was changed."""
    report.result = _role_run_result(code)
    report.grants = None
    report.error = RunError(code, message, exception)
    report.finished_at = _now()
    return report


def _role_failure_code(exc: BaseException) -> str:
    if _is_lock_timeout(exc):
        return "lock_not_available"
    if isinstance(exc, SQLAlchemyError) and _is_connection_failure(exc):
        return "database_unavailable"
    return "internal_error"


def provision_document(report: ProvisionReport) -> dict[str, object]:
    """The JSON document of one provision-roles run (keys in the contract order)."""
    return {
        "report_version": REPORT_VERSION,
        "command": "provision-roles",
        "result": report.result,
        "exit_code": report.exit_code,
        "started_at": _iso(report.started_at),
        "finished_at": _iso(report.finished_at),
        "duration_ms": _role_duration_ms(report),
        "owner_role": report.owner_role,
        "roles": list(report.roles),
        "error": _error_document(report.error),
    }


def provision_summary(report: ProvisionReport) -> str:
    if report.result != "provisioned" or report.error is not None:
        message = report.error.message if report.error is not None else report.result
        return f"provision-roles: {message}"
    actions = ", ".join(f"{role['name']} {role['action']}" for role in report.roles)
    return (
        f"provision-roles: {actions}; passwords set from the secret files"
        f" ({_role_duration_ms(report)} ms)"
    )


def grants_document(report: GrantsReport) -> dict[str, object]:
    """The JSON document of one apply-grants run (keys in the contract order)."""
    return {
        "report_version": REPORT_VERSION,
        "command": "apply-grants",
        "result": report.result,
        "exit_code": report.exit_code,
        "started_at": _iso(report.started_at),
        "finished_at": _iso(report.finished_at),
        "duration_ms": _role_duration_ms(report),
        "release": report.release,
        "expected_revision": report.expected_revision,
        "database_revision": report.database_revision,
        "grants": report.grants,
        "error": _error_document(report.error),
    }


def grants_summary(report: GrantsReport) -> str:
    if report.result != "applied" or report.grants is None:
        message = report.error.message if report.error is not None else report.result
        return f"apply-grants: {message}"
    roles = report.grants["roles"]
    assert isinstance(roles, dict)
    summary = (
        f"apply-grants: granted {report.grants['tables']} tables to {roles['application']}"
        f" and {roles['maintenance']} at revision {report.database_revision}"
        f" ({_role_duration_ms(report)} ms)"
    )
    foreign = report.grants["foreign_grantees"]
    assert isinstance(foreign, list)
    if foreign:
        summary += (
            f"; {len(foreign)} privileges of other database roles were left in place"
            " (see reconcile check h)"
        )
    return summary


def run_provision_roles(
    engine: Engine,
    report: ProvisionReport,
    *,
    roles: database_roles.DatabaseRoles,
    passwords: database_roles.DatabaseRoles,
    lock_timeout_seconds: int,
) -> ProvisionReport:
    """One provision-roles transaction; always a complete report."""
    try:
        connection = engine.connect()
    except SQLAlchemyError as exc:
        message = PROVISION_RUN_MESSAGES["database_unavailable"]
        return provision_failure(report, "database_unavailable", message, exception=exc)
    try:
        transaction = connection.begin()
        try:
            report.owner_role = str(connection.execute(text("SELECT current_user")).scalar_one())
            entries = database_roles.provision_roles(
                connection,
                roles=roles,
                passwords=passwords,
                lock_timeout_seconds=lock_timeout_seconds,
            )
        except database_roles.DatabaseRolesRefusal as refusal:
            _discard(transaction)
            return provision_failure(report, refusal.code, refusal.message)
        except Exception as exc:
            # Every failure before COMMIT rolls the whole run back.
            _discard(transaction)
            code = _role_failure_code(exc)
            return provision_failure(report, code, PROVISION_RUN_MESSAGES[code], exception=exc)
        try:
            commit_transaction(transaction)
        except Exception as exc:
            # COMMIT itself failed: the outcome is unknown, never "nothing changed".
            report.result = "outcome_unknown"
            report.error = RunError(
                "outcome_unknown", PROVISION_RUN_MESSAGES["outcome_unknown"], exc
            )
            report.finished_at = _now()
            return report
    finally:
        connection.close()
    report.result = "provisioned"
    report.roles = entries
    report.finished_at = _now()
    return report


def _apply_grants_in_transaction(
    connection: Connection,
    report: GrantsReport,
    roles: database_roles.DatabaseRoles,
    lock_timeout_seconds: int,
) -> None:
    connection.execute(
        text("SELECT set_config('lock_timeout', :timeout, true)"),
        {"timeout": f"{lock_timeout_seconds}s"},
    )
    # The superuser check precedes the revision read (a role without
    # privileges must get not_superuser, never an internal error).
    user, superuser = connection.execute(
        text("SELECT rolname, rolsuper FROM pg_roles WHERE rolname = current_user")
    ).one()
    if not superuser:
        message = database_roles.GRANT_MESSAGES["not_superuser"].format(role=user)
        raise database_roles.DatabaseRolesRefusal("not_superuser", message)
    granted = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _MIGRATE_LOCK},
    ).scalar_one()
    if not granted:
        message = GRANT_RUN_MESSAGES["migrate_running"]
        raise database_roles.DatabaseRolesRefusal("migrate_running", message)
    report.database_revision = schema_revision.read_revision(connection)
    if report.database_revision != report.expected_revision:
        message = GRANT_RUN_MESSAGES["revision_mismatch"].format(
            database=report.database_revision or "none", expected=report.expected_revision
        )
        raise database_roles.DatabaseRolesRefusal("revision_mismatch", message)
    report.grants = database_roles.apply_grants(connection, roles=roles, required=True)


def run_apply_grants(
    engine: Engine,
    report: GrantsReport,
    *,
    roles: database_roles.DatabaseRoles,
    lock_timeout_seconds: int,
) -> GrantsReport:
    """One apply-grants transaction at this release's head; always a complete report."""
    try:
        report.expected_revision = schema_revision.code_head()
    except schema_revision.MigrationScriptsError as exc:
        message = GRANT_RUN_MESSAGES["internal_error"]
        return grants_failure(report, "internal_error", message, exception=exc)
    try:
        connection = engine.connect()
    except SQLAlchemyError as exc:
        message = GRANT_RUN_MESSAGES["database_unavailable"]
        return grants_failure(report, "database_unavailable", message, exception=exc)
    try:
        transaction = connection.begin()
        try:
            _apply_grants_in_transaction(connection, report, roles, lock_timeout_seconds)
        except database_roles.DatabaseRolesRefusal as refusal:
            _discard(transaction)
            return grants_failure(report, refusal.code, refusal.message)
        except Exception as exc:
            _discard(transaction)
            code = _role_failure_code(exc)
            return grants_failure(report, code, GRANT_RUN_MESSAGES[code], exception=exc)
        try:
            commit_transaction(transaction)
        except Exception as exc:
            report.result = "outcome_unknown"
            report.grants = None
            report.error = RunError("outcome_unknown", GRANT_RUN_MESSAGES["outcome_unknown"], exc)
            report.finished_at = _now()
            return report
    finally:
        connection.close()
    report.result = "applied"
    report.finished_at = _now()
    return report
