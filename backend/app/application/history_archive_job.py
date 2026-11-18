"""The host job of Movement-history archival (Phase 16, history archival design §5, §7;
build step 1: runs without deletion; build step 2: the purge).

``python -m app.cli history-archive <command>`` runs these steps for the host
script ``deploy/production/archive.sh``, as the maintenance database role
(``partflow_maintenance``), in the ``maintenance`` service where the archive
folder (``PARTFLOW_ARCHIVE_DIR``) and the optional off-host copy folder
(``PARTFLOW_ARCHIVE_COPY_DIR``) are mounted — never the backup folder: the
host script takes the pre-purge backup and passes its name and dump start.

Commands (each prints one JSON report; ``JobReport``):

- ``settings`` — what the host script needs (the Start minute of the hourly
  work), read-only;
- ``recover`` — called right after the host script took its lock: a run
  found ``RUNNING`` is stale (no other command holds the job's advisory
  lock; also after a restore of the pre-purge backup, which is taken while
  the run is RUNNING) and becomes ``FAILED`` with that reason; a ``PURGING``
  run is never failed here — the next ``purge`` resumes it; unfinished
  ``.partial`` entries of finished runs are removed; the purge receipt of a
  finished run the job copied off the host is copied again where the copy
  is missing;
- ``next`` — the run the job must work on and its next step: ``export``
  (APPROVED), ``verify`` (exported), ``copy`` (verified, not yet copied or
  confirmed; or WAITING_FOR_COPY that is confirmed or whose copy folder is
  usable now), ``backup`` (copied or confirmed: the host script takes the
  pre-purge backup — after the requester was re-checked), ``purge``
  (PURGING: resume), ``wait``, ``running`` or ``none``;
- ``export --run N`` — ``APPROVED`` → ``RUNNING``; then, in ONE ``REPEATABLE
  READ`` snapshot, the eligible components (``history_archive``), the files
  into ``<archive dir>/.partial/run-<id>-<UTC stamp>/``, and the
  carry-forward records; a run whose counts exceed its stored preview, or
  whose cutoff is now newer than the shortest allowed period, fails; with
  nothing eligible it ends ``NOTHING_ELIGIBLE``;
- ``verify --run N`` — re-reads every file (``SHA256SUMS``, the manifest's
  SHA-256, row counts and id sets, each flow's ``content_sha256``), compares
  them with the records and, in a fresh snapshot, with the database; a
  component that changed since the export is marked skipped; then the
  folder is published by renaming it out of ``.partial`` (an existing name
  is refused); when every component was skipped nothing is left to purge:
  the run finishes ``COMPLETED_WITH_SKIPS`` with the result
  ``NOTHING_PURGED`` and its receipt, with no copy and no backup;
- ``copy --run N`` — right before it copies or accepts a confirmation, the
  published folder is checked again against its manifest SHA-256 and
  checksums; with a usable copy folder the archive is copied there,
  re-read and checked, ``copied_at`` is set and the run stays ``RUNNING``;
  with a confirmation in Administration a ``WAITING_FOR_COPY`` run goes back
  to ``RUNNING``; otherwise the run waits in ``WAITING_FOR_COPY`` (without
  re-hashing the archive while its copy folder stays unavailable);
- ``purge --run N [--backup NAME --backup-started-at UTC]`` — a ``RUNNING``
  run with its verified, copied archive: the backup must be of the kind
  ``pre-purge`` with the run's archive name as its label, its dump must have
  started after the verification and not in the future, the requester must
  still be active and still hold the archival key, and the published folder
  must still be the verified one; then ``RUNNING`` → ``PURGING`` with the
  backup recorded, the batches, the receipt and ``COMPLETED`` /
  ``COMPLETED_WITH_SKIPS``; a ``PURGING`` run is resumed without a backup;
- ``status`` — the open run, the last finished run and the archive folder's
  space, for ``check.sh``; a run waiting for its copy confirmation longer
  than the waiting limit of the settings, a failed last run and a purge
  receipt missing from the copy folder are findings.

The purge (design §7.5) works in batches of whole components, at most the
Movements and Part Numbers per batch of the settings (a larger component
forms a batch of its own). Each batch is ONE ``READ COMMITTED`` transaction
with ``partflow.archive_run_id`` set locally: the PN advisory locks of its
Part Numbers through ``part_numbers.acquire_part_number_locks`` (the sorted
order every command uses), then the re-check of each component under the
locks — every flow still in its recorded closed status, its Movement count
and last Movement id unchanged —; a component that fails is marked skipped
and stays; the Movements of the others are deleted (the row guard
``trg_part_movements_guard_delete`` admits exactly the archived rows of
this run), their records marked purged, and the run's counts raised; a
deleted count that differs from the records rolls the batch back and fails
the run. An interrupted purge leaves whole components only, and the next
start resumes it: a killed process, and a database error that does not last
(``is_transient``: a lost connection, the server shutting down, a deadlock,
a serialization failure, a lock not available, a statement timeout), which
ends the command with exit 2 ``interrupted`` and leaves the run PURGING; any
other error fails the run. The purge receipt ``<archive name>.purged.json`` is
written beside the published folder (which never changes) with one atomic
rename, its SHA-256 recorded on the run, and copied into the copy folder
and checked when the job copied the archive there (a run whose copy a
person confirmed gets no copy: the off-host copy of the archive folder
carries the receipt with it).

Exit codes: 0 the step is done (``result`` says what happened); 1 refused,
nothing changed (an unknown run, a run in another state, another
history-archive command running, a missing or wrong pre-purge backup); 2
could not run (configuration, database unreachable, an internal error
before the run changed), its outcome is unknown, or a purge was interrupted
and stays PURGING; 3 the run failed — it is now ``FAILED`` with its reason
(never reported for a run that had ended otherwise).

Every command that works on a run sets the transaction-local setting
``partflow.archive_run_id`` to it: the run and record guard triggers admit
the job's changes only then (and only from a role that may write archive
records). Every change of state, and the off-host copy, appends one
``HistoryArchiveRun`` audit row (no actor; ``metadata.source``
``maintenance``). Audit rows are inserted without ``RETURNING``: the
maintenance role may append them, never read them.

The archive folder (design §5): ``movements.jsonl.gz`` (every column of every
archived Movement, one canonical JSON object per line, ordered by id),
``flows.jsonl.gz`` (each flow with its component and carry-forward record),
``lineage.jsonl.gz`` (the components' lineage edges, ``void`` for an undone
command), ``assigned_routes.jsonl.gz`` (each Assigned Route with its steps),
``context.json`` (the Areas, Operations, Machines, Workers, Scan Stations,
Work Orders, demand lines, Users and Part Numbers the rows name),
``manifest.json`` (format, run, cutoff, groups, revision, release, counts,
and per file its SHA-256, size, rows and id-set hash), ``README.txt`` and
``SHA256SUMS`` (``sha256sum -c SHA256SUMS`` in the folder checks every
other file). JSON values are PostgreSQL's ``to_jsonb`` of each row read with
``TimeZone`` UTC, re-encoded canonically (sorted keys, compact, UTF-8); a
flow's ``content_sha256`` is the SHA-256 of its Movements' lines, in id
order, newline included.

Memory does not grow with the archived flows (B2 rehearsal D2): the
archived set is a temporary table of the command's transaction
(``SELECTION``, which also keeps every join with it linear: D1); every row
and file is streamed; a flow is held only while its Movements are still
coming (at its last one its record is complete and goes to the database);
the flows file is written from those records; ``context.json`` is written
row by row; and every data file is written in ascending id order, so its
id-set hash is computed as the ids come.
"""

import contextlib
import datetime
import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NamedTuple, cast

from sqlalchemy import Connection, Engine, RowMapping, Table, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.application import backups, history_archive
from app.application.migration import RunError, is_connection_failure
from app.application.part_numbers import acquire_part_number_locks
from app.core.config import get_settings
from app.domain.enums import (
    AuditEventType,
    HistoryArchiveRunResult,
    HistoryArchiveRunState,
    MovementType,
    Permission,
)
from app.infrastructure import archive_files, backup_files
from app.infrastructure.models import HistoryArchivedFlow, HistoryArchiveRun

logger = logging.getLogger(__name__)

REPORT_VERSION: Final = 1
ARCHIVE_FORMAT: Final = "partflow-history-archive"
ARCHIVE_FORMAT_VERSION: Final = 1
ARCHIVE_DIR_ENV: Final = "PARTFLOW_ARCHIVE_DIR"
COPY_DIR_ENV: Final = "PARTFLOW_ARCHIVE_COPY_DIR"
PARTIAL_DIR: Final = ".partial"
#: The session-level advisory lock every command that changes something holds.
JOB_LOCK: Final = "partflow:history-archive"
RUN_SETTING: Final = "partflow.archive_run_id"

MOVEMENTS_FILE: Final = "movements.jsonl.gz"
FLOWS_FILE: Final = "flows.jsonl.gz"
LINEAGE_FILE: Final = "lineage.jsonl.gz"
ROUTES_FILE: Final = "assigned_routes.jsonl.gz"
CONTEXT_FILE: Final = "context.json"
MANIFEST_FILE: Final = "manifest.json"
README_FILE: Final = "README.txt"
SUMS_FILE: Final = archive_files.SUMS_FILE
#: The JSON Lines files, with a row count and an id-set hash each.
DATA_FILES: Final = (ROUTES_FILE, FLOWS_FILE, LINEAGE_FILE, MOVEMENTS_FILE)
#: Every file of an archive folder, sorted by name.
ARCHIVE_FILES: Final = tuple(
    sorted((*DATA_FILES, CONTEXT_FILE, MANIFEST_FILE, README_FILE, SUMS_FILE))
)
NAME_PATTERN: Final = re.compile(r"run-(?P<run>[0-9]+)-(?P<stamp>[0-9]{8}T[0-9]{6}Z)")
#: An entry below ``.partial/``: an unfinished archive folder or purge receipt.
PARTIAL_PATTERN: Final = re.compile(
    r"run-(?P<run>[0-9]+)-(?P<stamp>[0-9]{8}T[0-9]{6}Z)(?:\.purged\.json)?"
)
RECEIPT_SUFFIX: Final = history_archive.RECEIPT_SUFFIX
RECEIPT_FORMAT: Final = "partflow-history-archive-purge-receipt"
RECEIPT_FORMAT_VERSION: Final = 1
#: The kind of the backup the host script takes before a purge.
PRE_PURGE_KIND: Final = "pre-purge"
#: How far a dump start may lie in the future (a clock error beyond it).
_BACKUP_FUTURE_TOLERANCE: Final = datetime.timedelta(minutes=2)

EXIT_DONE: Final = 0
EXIT_REFUSED: Final = 1
EXIT_COULD_NOT_RUN: Final = 2
EXIT_RUN_FAILED: Final = 3

_RUNS: Final = cast(Table, HistoryArchiveRun.__table__)
_RECORDS: Final = cast(Table, HistoryArchivedFlow.__table__)
_STREAM: Final = {"stream_results": True, "yield_per": 5000}
_STATE = HistoryArchiveRunState

_MESSAGES: Final = {
    "configuration_invalid": (
        "PartFlow is not configured: check DATABASE_URL, or DATABASE_HOST, DATABASE_NAME,"
        " DATABASE_USER and DATABASE_PASSWORD_FILE. Nothing was changed."
    ),
    "archive_dir_missing": (
        f"The archive folder is not configured: set {ARCHIVE_DIR_ENV} in the maintenance"
        " service. Nothing was changed."
    ),
    "archive_dir_unusable": (
        "The archive folder {path} does not exist or cannot be written. Nothing was changed."
    ),
    "database_unavailable": "PartFlow could not reach its database. Nothing was changed.",
    "job_running": "Another history-archive command is running. Nothing was changed.",
    "internal_error": "history-archive failed unexpectedly (internal error). See the error output.",
}


class JobFolders(NamedTuple):
    """The host folders of the maintenance service (never known to the backend)."""

    archive_dir: Path | None
    copy_dir: Path | None


def folders_from(environ: Mapping[str, str]) -> JobFolders:
    def folder(name: str) -> Path | None:
        value = environ.get(name, "").strip()
        return Path(value) if value else None

    return JobFolders(folder(ARCHIVE_DIR_ENV), folder(COPY_DIR_ENV))


@dataclass
class JobReport:
    command: str
    started_at: datetime.datetime
    result: str = "failed"
    exit_code: int = EXIT_COULD_NOT_RUN
    run_id: int | None = None
    state: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: RunError | None = None
    finished_at: datetime.datetime | None = None
    #: The command changed the run, so an unexpected error makes it FAILED.
    run_started: bool = False


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _CouldNotRun(_Refused):
    pass


class _RunFailed(Exception):
    """The run must fail with this reason (the caller records it)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


iso = history_archive.iso


def _stamp(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def job_document(report: JobReport) -> dict[str, object]:
    """The JSON document of one command; the keys the host script reads are top-level."""
    finished = report.finished_at or report.started_at
    return {
        "report_version": REPORT_VERSION,
        "command": f"history-archive {report.command}",
        "result": report.result,
        "exit_code": report.exit_code,
        "started_at": iso(report.started_at),
        "finished_at": iso(report.finished_at),
        "duration_ms": int((finished - report.started_at).total_seconds() * 1000),
        "run_id": report.run_id,
        "state": report.state,
        **report.details,
        "error": (
            None
            if report.error is None
            else {"code": report.error.code, "message": report.error.message}
        ),
    }


def job_summary(report: JobReport) -> str:
    run = f" run {report.run_id}" if report.run_id is not None else ""
    state = f" ({report.state})" if report.state is not None else ""
    message = f": {report.error.message}" if report.error is not None else ""
    return f"history-archive {report.command}:{run} {report.result}{state}{message}"


def _finish(report: JobReport, result: str, exit_code: int) -> JobReport:
    report.result = result
    report.exit_code = exit_code
    report.finished_at = _now()
    return report


def _fail(
    report: JobReport, code: str, message: str, exit_code: int, exc: BaseException | None = None
) -> JobReport:
    report.error = RunError(code, message, exc)
    result = {EXIT_REFUSED: "refused", EXIT_RUN_FAILED: "failed"}.get(exit_code, "could_not_run")
    return _finish(report, result, exit_code)


# ---------------------------------------------------------------------------
# Database helpers (Core, as the maintenance role may: no RETURNING on audit_events)
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _job_lock(engine: Engine) -> Iterator[None]:
    """The job's session-level advisory lock, held on its own autocommit connection."""
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        locked = connection.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": JOB_LOCK}
        ).scalar_one()
        if not locked:
            raise _Refused("job_running", _MESSAGES["job_running"])
        try:
            yield
        finally:
            # Best effort: the lock ends with its session anyway, so a lost
            # lock connection never turns the command's outcome into an error.
            try:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": JOB_LOCK}
                )
            except SQLAlchemyError as exc:
                logger.warning(
                    "history-archive: the job lock was not released (%s); it ended with its"
                    " session",
                    type(exc).__name__,
                )
    finally:
        try:
            connection.close()
        except SQLAlchemyError as exc:
            logger.warning("history-archive: the lock connection did not close (%s)", exc)


def _mark(connection: Connection, run_id: int) -> None:
    connection.execute(
        text("SELECT set_config(:name, :value, true)"), {"name": RUN_SETTING, "value": str(run_id)}
    )


def _run_row(connection: Connection, run_id: int, *, lock: bool) -> RowMapping | None:
    query = select(_RUNS).where(_RUNS.c.id == run_id)
    if lock:
        query = query.with_for_update()
    return connection.execute(query).mappings().one_or_none()


def _audit(connection: Connection, before: RowMapping, after: RowMapping, command: str) -> None:
    connection.execute(
        text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data, metadata) VALUES (:event_type, :entity_type, :entity_id,"
            " now(), CAST(:before AS jsonb), CAST(:after AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": AuditEventType.UPDATED.value,
            "entity_type": "HistoryArchiveRun",
            "entity_id": str(after["id"]),
            "before": json.dumps(history_archive.run_snapshot(before)),
            "after": json.dumps(history_archive.run_snapshot(after)),
            "metadata": json.dumps({"source": "maintenance", "command": command}),
        },
    )


def _change(
    connection: Connection,
    run: RowMapping,
    command: str,
    *,
    audit: bool = False,
    **values: object,
) -> RowMapping:
    """Update the run (marked as the job's); a state change, or ``audit``, appends an audit row."""
    _mark(connection, int(run["id"]))
    after = (
        connection.execute(
            update(_RUNS).where(_RUNS.c.id == run["id"]).values(**values).returning(*_RUNS.c)
        )
        .mappings()
        .one()
    )
    if audit or after["state"] != run["state"]:
        _audit(connection, run, after, command)
    return after


#: Makes a run FAILED with a reason (engine, run id, reason, command) → its state.
FailRun = Callable[[Engine, int, str, str], str | None]


def _fail_run(engine: Engine, run_id: int, reason: str, command: str) -> str | None:
    """Make the run FAILED with its reason, unless it is finished; returns its state."""
    with engine.connect() as connection, connection.begin():
        run = _run_row(connection, run_id, lock=True)
        if run is None:
            return None
        if history_archive.is_final(str(run["state"])):
            return str(run["state"])
        after = _change(
            connection,
            run,
            command,
            state=_STATE.FAILED.value,
            failure_reason=reason[:2000],
            finished_at=_now(),
        )
        return str(after["state"])


def _run_summary(run: RowMapping | None) -> dict[str, Any] | None:
    if run is None:
        return None
    purged = (
        None
        if run["purge_started_at"] is None
        else {
            "components": run["purged_components"],
            "flows": run["purged_flows"],
            "movements": run["purged_movements"],
        }
    )
    return {
        "id": int(run["id"]),
        "state": str(run["state"]),
        "cutoff": iso(run["cutoff"]),
        "requested_at": iso(run["requested_at"]),
        "archive_name": run["archive_name"],
        "manifest_sha256": run["manifest_sha256"],
        "copied": run["copied_at"] is not None,
        "copy_confirmed": run["copy_confirmed_at"] is not None,
        "skipped_components": run["skipped_components"],
        "skipped_flows": run["skipped_flows"],
        "pre_purge_backup_name": run["pre_purge_backup_name"],
        "purged": purged,
        "receipt_sha256": run["receipt_sha256"],
        "finished_at": iso(run["finished_at"]),
        "result": run["result"],
        "failure_reason": run["failure_reason"],
    }


def _require_archive_dir(folders: JobFolders) -> Path:
    if folders.archive_dir is None:
        raise _CouldNotRun("archive_dir_missing", _MESSAGES["archive_dir_missing"])
    if not archive_files.is_writable_directory(folders.archive_dir):
        raise _CouldNotRun(
            "archive_dir_unusable",
            _MESSAGES["archive_dir_unusable"].format(path=folders.archive_dir),
        )
    return folders.archive_dir


def _command(
    command: str,
    engine: Engine | None,
    work: Callable[[Engine, JobReport], None],
    *,
    run_id: int | None = None,
    fail_run: FailRun = _fail_run,
) -> JobReport:
    """Run one command; a refusal, a failed run and any error become a complete report.

    ``fail_run`` makes the run FAILED (the purge's also writes the receipt
    of what it deleted so far).
    """
    report = JobReport(command=command, started_at=_now(), run_id=run_id)
    if engine is None:
        return _fail(
            report, "configuration_invalid", _MESSAGES["configuration_invalid"], EXIT_COULD_NOT_RUN
        )
    try:
        work(engine, report)
    except _CouldNotRun as refusal:
        return _fail(report, refusal.code, refusal.message, EXIT_COULD_NOT_RUN)
    except _Refused as refusal:
        return _fail(report, refusal.code, refusal.message, EXIT_REFUSED)
    except _RunFailed as failure:
        return _record_failure(engine, report, failure.reason, None, fail_run)
    except Exception as exc:
        if report.state == _STATE.PURGING and is_transient(exc):
            return _interrupted(report, exc)
        if report.run_started:
            deleted = (
                "Whole components only were deleted; the purge receipt lists them."
                if report.state == _STATE.PURGING
                else "Nothing was deleted."
            )
            return _record_failure(
                engine,
                report,
                f"The host job stopped on an internal error ({type(exc).__name__}). {deleted}"
                " See the job log.",
                exc,
                fail_run,
            )
        if isinstance(exc, SQLAlchemyError) and is_connection_failure(exc):
            return _fail(
                report,
                "database_unavailable",
                _MESSAGES["database_unavailable"],
                EXIT_COULD_NOT_RUN,
                exc,
            )
        return _fail(report, "internal_error", _MESSAGES["internal_error"], EXIT_COULD_NOT_RUN, exc)
    return _finish(report, report.result, report.exit_code)


def _record_failure(
    engine: Engine,
    report: JobReport,
    reason: str,
    exc: BaseException | None,
    fail_run: FailRun,
) -> JobReport:
    if report.run_id is None:  # pragma: no cover - every failing command names its run
        return _fail(report, "internal_error", reason, EXIT_COULD_NOT_RUN, exc)
    try:
        state = fail_run(engine, report.run_id, reason, report.command)
    except Exception as marking:
        # The run stays as it is: the next recover makes a stale RUNNING run
        # FAILED, and the next purge resumes a PURGING one.
        return _fail(report, "outcome_unknown", reason, EXIT_COULD_NOT_RUN, exc or marking)
    report.state = state
    if state is None or state == _STATE.FAILED:
        return _fail(report, "run_failed", reason, EXIT_RUN_FAILED, exc)
    # The run had already ended on its own (a purge that completed before its
    # lock connection was lost, say): it did not fail.
    logger.warning(
        "history-archive %s: run %s is %s; the error after it is not its failure: %s",
        report.command,
        report.run_id,
        state,
        reason,
    )
    if report.exit_code == EXIT_DONE:
        return _finish(report, report.result, EXIT_DONE)
    return _fail(
        report,
        "outcome_unknown",
        f"Run {report.run_id} is {state}; the command stopped after that on an error. {reason}",
        EXIT_COULD_NOT_RUN,
        exc,
    )


#: SQLSTATEs of a statement the database refused only for the moment
#: (deadlock_detected, serialization_failure, lock_not_available).
_TRANSIENT_SQLSTATES: Final = frozenset({"40P01", "40001", "55P03"})


def is_transient(exc: BaseException) -> bool:
    """A database error after which a purge resumes rather than fails (design §7.5).

    A lost or refused connection, the server shutting down (57P01, 57P02,
    57P03), a deadlock (40P01), a serialization failure (40001), a lock not
    available (55P03), or a statement cancelled by a timeout (57014 with a
    timeout as its reason; a cancel by a person is not transient).
    """
    if not isinstance(exc, SQLAlchemyError):
        return False
    if is_connection_failure(exc):
        return True
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate in _TRANSIENT_SQLSTATES:
        return True
    if sqlstate == "57014":
        diagnostic = getattr(original, "diag", None)
        primary = getattr(diagnostic, "message_primary", None) or str(original)
        return "timeout" in primary
    return False


def _interrupted(report: JobReport, exc: BaseException) -> JobReport:
    """A purge stopped by a transient database error: the run stays PURGING (exit 2).

    The batch in progress was rolled back; ``purged`` and ``skipped`` say what
    the committed batches did, and the next start resumes the purge.
    """
    report.error = RunError(
        "purge_interrupted",
        f"Run {report.run_id} stopped purging on a database error that does not last"
        f" ({type(exc).__name__}). It stays PURGING: the batch in progress was rolled back,"
        " whole components only were deleted, and the next start resumes the purge.",
        exc,
    )
    return _finish(report, "interrupted", EXIT_COULD_NOT_RUN)


# ---------------------------------------------------------------------------
# settings, next, status
# ---------------------------------------------------------------------------


def settings(engine: Engine | None, folders: JobFolders) -> JobReport:
    """What the host script needs: the Start minute of the hourly work."""

    def work(engine: Engine, report: JobReport) -> None:
        with engine.connect() as connection:
            stored = history_archive.read_settings(connection)
        report.details.update(
            {
                "start_minute": stored.start_minute,
                "archive_dir": None if folders.archive_dir is None else str(folders.archive_dir),
                "copy_dir": None if folders.copy_dir is None else str(folders.copy_dir),
                "site_timezone": get_settings().site_timezone,
            }
        )
        report.result, report.exit_code = "read", EXIT_DONE

    return _command("settings", engine, work)


def _open_run(connection: Connection) -> RowMapping | None:
    return (
        connection.execute(
            select(_RUNS)
            .where(_RUNS.c.state.in_([state.value for state in _OPEN_STATES]))
            .order_by(_RUNS.c.id)
            .limit(1)
        )
        .mappings()
        .one_or_none()
    )


_OPEN_STATES: Final = (
    _STATE.APPROVED,
    _STATE.RUNNING,
    _STATE.WAITING_FOR_COPY,
    _STATE.PURGING,
)


def _copy_dir_usable(folders: JobFolders) -> bool:
    return folders.copy_dir is not None and archive_files.is_writable_directory(folders.copy_dir)


def _next_action(run: RowMapping | None, folders: JobFolders) -> str:
    """What the host script runs next for the open run (a read; no lock, no hash)."""
    if run is None:
        return "none"
    state = run["state"]
    if state == _STATE.APPROVED:
        return "export"
    if state == _STATE.PURGING:
        return "purge"
    if state == _STATE.WAITING_FOR_COPY:
        # Only a confirmation or a usable copy folder lets the copy step
        # progress; without either it would only wait again (B1 review minor 7).
        if run["copy_confirmed_at"] is not None or _copy_dir_usable(folders):
            return "copy"
        return "wait"
    if run["exported_at"] is None:
        return "running"
    if run["verified_at"] is None:
        return "verify"
    if run["copied_at"] is None and run["copy_confirmed_at"] is None:
        return "copy"
    return "backup"


#: The key the requester of a run must still hold when the run purges (design §6.2).
_ARCHIVAL_KEY: Final = Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE.value


def _requester_problem(connection: Connection, run: RowMapping) -> str | None:
    """Why the User who requested the run may no longer have it purged (None: they may)."""
    row = (
        connection.execute(
            text(
                "SELECT u.display_name, u.is_active, EXISTS (SELECT 1 FROM role_permissions p"
                " WHERE p.role_id = u.role_id AND p.permission = :key) AS holds"
                " FROM users u WHERE u.id = :id"
            ),
            {"id": int(run["requested_by_user_id"]), "key": _ARCHIVAL_KEY},
        )
        .mappings()
        .one_or_none()
    )
    who = (
        f"user {int(run['requested_by_user_id'])}"
        if row is None
        else f"{row['display_name']} (user {int(run['requested_by_user_id'])})"
    )
    if row is None or not row["is_active"]:
        return (
            f"The User who requested run {int(run['id'])}, {who}, is no longer active, so its"
            " history was not purged. Nothing was deleted; a holder of Run history archival and"
            " purge may request a new run."
        )
    if not row["holds"]:
        return (
            f"The User who requested run {int(run['id'])}, {who}, no longer holds Run history"
            " archival and purge, so its history was not purged. Nothing was deleted; a holder"
            " of the key may request a new run."
        )
    return None


def next_run(engine: Engine | None, folders: JobFolders) -> JobReport:
    """The run the job must work on, and what to run for it.

    Before it hands out the ``backup`` step it re-checks the requester (so no
    backup is taken for a run that may not purge): a requester who is no
    longer active or no longer holds the archival key makes the run FAILED
    (exit 3).
    """

    def work(engine: Engine, report: JobReport) -> None:
        with engine.connect() as connection:
            run = _open_run(connection)
            action = _next_action(run, folders)
            problem = (
                _requester_problem(connection, run)
                if run is not None and action == "backup"
                else None
            )
        if run is not None:
            report.run_id, report.state = int(run["id"]), str(run["state"])
            if problem is not None:
                with _job_lock(engine):
                    report.state = _fail_run(engine, int(run["id"]), problem, "next")
                raise _RunFailed(problem)
        report.details["run"] = _run_summary(run)
        if action == "backup" and run is not None:
            report.details["backup"] = {"kind": PRE_PURGE_KIND, "label": run["archive_name"]}
        report.result, report.exit_code = action, EXIT_DONE

    return _command("next", engine, work)


def status(engine: Engine | None, folders: JobFolders) -> JobReport:
    """The open run and its age, the last finished run and the archive folder's space.

    A run waiting for its copy confirmation longer than the waiting limit
    of the settings (``archive_max_wait_days``) is a finding, and so is the
    purge receipt of a run the job copied off the host that is missing (or
    differs) beside the archive's copy in a usable copy folder
    (``receipt_not_copied``; the next ``recover`` copies it again).
    """

    def work(engine: Engine, report: JobReport) -> None:
        with engine.connect() as connection:
            max_wait_days = history_archive.read_settings(connection).max_wait_days
            run = _open_run(connection)
            last = (
                connection.execute(
                    select(_RUNS)
                    .where(_RUNS.c.finished_at.is_not(None))
                    .order_by(_RUNS.c.finished_at.desc(), _RUNS.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
        findings: list[str] = []
        moment = _now()
        open_summary = _run_summary(run)
        if run is not None and open_summary is not None:
            open_summary["age_seconds"] = int((moment - run["requested_at"]).total_seconds())
            if run["state"] == _STATE.WAITING_FOR_COPY and run["copy_confirmed_at"] is None:
                waiting = moment - (run["verified_at"] or run["requested_at"])
                open_summary["waiting_seconds"] = int(waiting.total_seconds())
                if waiting > datetime.timedelta(days=max_wait_days):
                    findings.append("copy_confirmation_overdue")
        if last is not None and last["state"] == _STATE.FAILED:
            findings.append("last_run_failed")
        report.details["open_run"] = open_summary
        report.details["last_finished_run"] = _run_summary(last)
        report.details["archive_dir"] = _folder_status(folders.archive_dir)
        report.details["copy_dir"] = (
            None if folders.copy_dir is None else _folder_status(folders.copy_dir)
        )
        if not report.details["archive_dir"]["usable"]:
            findings.append("archive_dir_unusable")
        if folders.copy_dir is not None and not report.details["copy_dir"]["usable"]:
            findings.append("copy_dir_unusable")
        with engine.connect() as connection:
            receipts = _copied_receipts(connection)
        missing = [
            name
            for name, digest in receipts
            if _receipt_copy_state(folders, name, digest) not in (None, "ok")
        ]
        report.details["receipts_not_copied"] = missing
        if missing:
            findings.append("receipt_not_copied")
        report.details["max_wait_days"] = max_wait_days
        report.details["findings"] = findings
        report.result = "finding" if findings else "ok"
        report.exit_code = EXIT_REFUSED if findings else EXIT_DONE

    return _command("status", engine, work)


def _folder_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": None, "usable": False, "free_bytes": None, "total_bytes": None}
    usable = archive_files.is_writable_directory(path)
    free: int | None = None
    total: int | None = None
    partial: list[str] = []
    if usable:
        free, total = archive_files.free_space(path)
        if backup_files.is_real_directory(path / PARTIAL_DIR):
            partial = backup_files.entry_names(path / PARTIAL_DIR)
    return {
        "path": str(path),
        "usable": usable,
        "free_bytes": free,
        "total_bytes": total,
        "partial": partial,
    }


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------


def recover(engine: Engine | None, folders: JobFolders) -> JobReport:
    """Fail stale RUNNING runs; clean up after finished runs; never touch a PURGING run.

    A RUNNING run found here is stale: nothing else holds the job's lock (or
    the database was restored from the pre-purge backup, which is taken while
    the run is RUNNING). A PURGING run stays as it is: the next ``purge``
    resumes it. Unfinished ``.partial`` entries (archive folders and receipt
    files) of finished runs are removed; a name the job does not know is
    kept and listed. The purge receipt of a finished run whose archive the job
    copied off the host is copied again when it is missing or differs beside
    the archive's copy in a usable copy folder.
    """

    def work(engine: Engine, report: JobReport) -> None:
        archive_dir = _require_archive_dir(folders)
        with _job_lock(engine):
            failed: list[int] = []
            with engine.connect() as connection, connection.begin():
                stale = (
                    connection.execute(
                        select(_RUNS)
                        .where(_RUNS.c.state == _STATE.RUNNING.value)
                        .order_by(_RUNS.c.id)
                        .with_for_update()
                    )
                    .mappings()
                    .all()
                )
                for run in stale:
                    _change(
                        connection,
                        run,
                        "recover",
                        state=_STATE.FAILED.value,
                        failure_reason=(
                            "The host job stopped while the run was RUNNING (found by the next"
                            " start of the job, also after a restore of its pre-purge backup)."
                            " Nothing was deleted; request a new run."
                        ),
                        finished_at=_now(),
                    )
                    failed.append(int(run["id"]))
                states = {
                    int(row.id): str(row.state)
                    for row in connection.execute(select(_RUNS.c.id, _RUNS.c.state))
                }
                receipts = _copied_receipts(connection)
            removed: list[str] = []
            kept: list[str] = []
            for root in (archive_dir, folders.copy_dir):
                if root is None or not backup_files.is_real_directory(root / PARTIAL_DIR):
                    continue
                for name in backup_files.entry_names(root / PARTIAL_DIR):
                    match = PARTIAL_PATTERN.fullmatch(name)
                    run_state = None if match is None else states.get(int(match.group("run")))
                    if run_state is not None and history_archive.is_final(run_state):
                        backup_files.remove_tree(root / PARTIAL_DIR / name)
                        removed.append(str(root / PARTIAL_DIR / name))
                    else:
                        kept.append(str(root / PARTIAL_DIR / name))
            copied, problems = _copy_missing_receipts(archive_dir, folders, receipts)
        report.details.update(
            {
                "failed_run_ids": failed,
                "removed_partial": removed,
                "kept_partial": kept,
                "copied_receipts": copied,
                "receipt_problems": problems,
            }
        )
        report.result = "recovered" if failed or removed or copied else "nothing_to_recover"
        report.exit_code = EXIT_DONE

    return _command("recover", engine, work)


def _copied_receipts(connection: Connection) -> list[tuple[str, str]]:
    """(archive name, receipt SHA-256) of every finished run the job copied off the host."""
    return [
        (str(row.archive_name), str(row.receipt_sha256))
        for row in connection.execute(
            select(_RUNS.c.archive_name, _RUNS.c.receipt_sha256)
            .where(
                _RUNS.c.finished_at.is_not(None),
                _RUNS.c.copied_at.is_not(None),
                _RUNS.c.receipt_sha256.is_not(None),
            )
            .order_by(_RUNS.c.id)
        )
    ]


def _receipt_copy_state(folders: JobFolders, name: str, digest: str) -> str | None:
    """``ok``, ``missing`` or ``different`` for the receipt beside the archive's copy.

    None when it cannot be judged: no usable copy folder, or the archive's
    copy is not in it (another copy folder than the one the run used).
    """
    copy_dir = folders.copy_dir
    if copy_dir is None or not _copy_dir_usable(folders):
        return None
    if not backup_files.is_real_directory(copy_dir / name):
        return None
    receipt = copy_dir / f"{name}{RECEIPT_SUFFIX}"
    if not backup_files.is_regular_file(receipt):
        return "missing"
    try:
        actual = archive_files.digest(receipt).sha256
    except OSError:
        return "missing"
    return "ok" if actual == digest else "different"


def _copy_missing_receipts(
    archive_dir: Path, folders: JobFolders, receipts: Sequence[tuple[str, str]]
) -> tuple[list[str], list[str]]:
    """Copy the receipts that are missing or differ beside the archive copies (best effort)."""
    copied: list[str] = []
    problems: list[str] = []
    for name, digest in receipts:
        if _receipt_copy_state(folders, name, digest) in (None, "ok"):
            continue
        assert folders.copy_dir is not None
        problem = _copy_receipt(archive_dir, folders.copy_dir, name, digest)
        if problem is None:
            copied.append(str(folders.copy_dir / f"{name}{RECEIPT_SUFFIX}"))
        else:
            problems.append(problem)
    return copied, problems


# ---------------------------------------------------------------------------
# The archive content
# ---------------------------------------------------------------------------


class FlowFacts(NamedTuple):
    flow_id: int
    component_id: int
    part_number: str
    status: str
    movements: int
    first_movement_id: int
    last_movement_id: int
    first_occurred_at: datetime.datetime
    last_occurred_at: datetime.datetime


def eligible_flows(connection: Connection, cutoff: datetime.datetime) -> list[FlowFacts]:
    """Every flow of every eligible component at the cutoff, by flow id."""
    roots = history_archive.lineage_roots(connection)
    rows = connection.execute(
        text(
            history_archive.ELIGIBLE_FLOWS_SQL
            + "SELECT cf.flow_id, cf.component_id, cf.part_number, cf.status, cf.movements,"
            " cf.first_movement_id, cf.last_movement_id, cf.first_occurred_at,"
            " cf.last_occurred_at FROM closed_flows cf"
            " JOIN eligible e ON e.component_id = cf.component_id ORDER BY cf.flow_id"
        ),
        history_archive.selection_parameters(roots, cutoff),
    )
    return [
        FlowFacts(
            int(row.flow_id),
            int(row.component_id),
            str(row.part_number),
            str(row.status),
            int(row.movements),
            int(row.first_movement_id),
            int(row.last_movement_id),
            row.first_occurred_at,
            row.last_occurred_at,
        )
        for row in rows
    ]


def _utc(connection: Connection) -> None:
    """``to_jsonb`` renders times in the session time zone: always UTC here."""
    connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))


#: The archived set of an export or a verification: one row per archived flow
#: in a temporary table of the command's transaction, with a primary key and
#: analyzed once filled. Every statement that joins the set is so planned on
#: its true size and can probe its index: joined as arrays of every flow id
#: (``unnest``), the planner expected about a hundred rows and could pick a
#: nested loop that rescans them for every Movement (the rehearsal's
#: quadratic plan). It also keeps the flows' facts out of this process. The
#: maintenance role creates it with the TEMPORARY privilege PUBLIC holds on
#: the database (``apply-grants`` revokes only CREATE there).
SELECTION: Final = "pg_temp.history_archive_selection"
_EXPORT_SELECTION: Final = f"""
CREATE TEMPORARY TABLE {SELECTION.removeprefix("pg_temp.")} (
    flow_id integer PRIMARY KEY,
    component_id integer NOT NULL,
    part_number text NOT NULL,
    status text NOT NULL,
    movements integer NOT NULL,
    first_movement_id bigint NOT NULL,
    last_movement_id bigint NOT NULL,
    first_occurred_at timestamptz NOT NULL,
    last_occurred_at timestamptz NOT NULL
) ON COMMIT DROP
"""


class _Selection(NamedTuple):
    """What an export's archived set holds (the manifest's counts and time span)."""

    components: int
    flows: int
    movements: int
    first_occurred_at: datetime.datetime | None
    last_occurred_at: datetime.datetime | None


def _select_archived(connection: Connection, cutoff: datetime.datetime) -> _Selection:
    """Fill the export's archived set: every flow of every eligible component at the cutoff."""
    connection.execute(text(_EXPORT_SELECTION))
    roots = history_archive.lineage_roots(connection)
    connection.execute(
        text(
            history_archive.ELIGIBLE_FLOWS_SQL
            + f"INSERT INTO {SELECTION} SELECT cf.flow_id, cf.component_id, cf.part_number,"
            " cf.status, cf.movements, cf.first_movement_id, cf.last_movement_id,"
            " cf.first_occurred_at, cf.last_occurred_at FROM closed_flows cf"
            " JOIN eligible e ON e.component_id = cf.component_id"
        ),
        history_archive.selection_parameters(roots, cutoff),
    )
    connection.execute(text(f"ANALYZE {SELECTION}"))
    row = connection.execute(
        text(
            "SELECT count(DISTINCT component_id) AS components, count(*) AS flows,"
            " coalesce(sum(movements), 0) AS movements, min(first_occurred_at) AS first_at,"
            f" max(last_occurred_at) AS last_at FROM {SELECTION}"
        )
    ).one()
    return _Selection(
        int(row.components), int(row.flows), int(row.movements), row.first_at, row.last_at
    )


class _Event(NamedTuple):
    """The fields a carry-forward record reads from one Movement, and nothing else.

    The export keeps one per RECEIVED, QUANTITY_ADJUSTED, STOCKED or SCRAPPED
    Movement until the flows file is written, so it holds no whole row:
    ``occurred_at`` and ``demand`` (the stored
    ``metadata.context.work_order_demand_id``) only for RECEIVED.
    """

    kind: str
    movement_id: int
    quantity: Any
    to_area_id: Any
    occurred_at: Any = None
    demand: Any = None


#: The Movement types a carry-forward record is derived from.
_RECORD_KINDS: Final = frozenset(
    (
        MovementType.RECEIVED,
        MovementType.QUANTITY_ADJUSTED,
        MovementType.STOCKED,
        MovementType.SCRAPPED,
    )
)


def _event(movement_id: int, value: Mapping[str, Any]) -> _Event:
    kind = str(value.get("movement_type"))
    if kind != MovementType.RECEIVED:
        return _Event(kind, movement_id, value.get("quantity"), value.get("to_area_id"))
    context = (value.get("metadata") or {}).get("context")
    demand = context.get("work_order_demand_id") if isinstance(context, dict) else None
    return _Event(
        kind,
        movement_id,
        value.get("quantity"),
        value.get("to_area_id"),
        value.get("occurred_at"),
        demand,
    )


@dataclass
class _Context:
    """The master-data ids the archived rows name (``context.json``), as they are read.

    The demand lines, their Work Orders and the Part Numbers, which grow with
    the archived flows, are selected by the database from the records and
    the archived set instead.
    """

    areas: set[int] = field(default_factory=set)
    operations: set[int] = field(default_factory=set)
    machines: set[int] = field(default_factory=set)
    workers: set[int] = field(default_factory=set)
    stations: set[str] = field(default_factory=set)
    users: set[int] = field(default_factory=set)

    def add_int(self, target: set[int], value: object) -> None:
        if isinstance(value, int) and not isinstance(value, bool):
            target.add(value)

    def add_movement(self, value: Mapping[str, Any]) -> None:
        for key in ("from_area_id", "to_area_id"):
            self.add_int(self.areas, value.get(key))
        self.add_int(self.operations, value.get("operation_id"))
        for key in ("source_machine_id", "destination_machine_id"):
            self.add_int(self.machines, value.get(key))
        self.add_int(self.workers, value.get("worker_id"))
        if isinstance(value.get("station_id"), str):
            self.stations.add(value["station_id"])
        movement_context = (value.get("metadata") or {}).get("context")
        if isinstance(movement_context, dict):
            self.add_int(self.users, movement_context.get("actor_user_id"))


def _demand_id(event: _Event) -> int | None:
    demand = event.demand
    if demand is None:
        return None
    if isinstance(demand, int) and not isinstance(demand, bool):
        return demand
    if isinstance(demand, str) and demand.isdigit():
        return int(demand)
    raise _RunFailed(
        f"Movement {event.movement_id} names the demand line {demand!r}, which is not a number;"
        " its history does not fit the carry-forward record. Nothing was archived."
    )


def _one_effective(
    flow_id: int, label: str, events: Sequence[_Event], reversed_ids: set[int]
) -> _Event | None:
    effective = [
        event for event in events if event.kind == label and event.movement_id not in reversed_ids
    ]
    if len(effective) > 1:
        raise _RunFailed(
            f"Quantity Flow {flow_id} has {len(effective)} effective {label} Movements; its"
            " history does not fit the carry-forward record. Run reconcile. Nothing was"
            " archived."
        )
    return effective[0] if effective else None


def _parse_time(value: object) -> datetime.datetime:
    if not isinstance(value, str):  # pragma: no cover - to_jsonb renders every time as text
        raise _RunFailed("A Movement time is not text; nothing was archived.")
    return datetime.datetime.fromisoformat(value)


def _record(
    run_id: int,
    facts: FlowFacts,
    events: Sequence[_Event],
    reversed_ids: set[int],
    content_sha256: str,
    void_command_ids: Sequence[str],
) -> dict[str, Any]:
    """The carry-forward record of one flow (design §4.1)."""
    receipts = [event for event in events if event.kind == MovementType.RECEIVED]
    if len(receipts) > 1 or any(event.movement_id in reversed_ids for event in receipts):
        raise _RunFailed(
            f"Quantity Flow {facts.flow_id} has {len(receipts)} RECEIVED Movements or a"
            " reversed one; its history does not fit the carry-forward record. Run reconcile."
            " Nothing was archived."
        )
    received = receipts[0] if receipts else None
    adjusted = _one_effective(facts.flow_id, "QUANTITY_ADJUSTED", events, reversed_ids)
    stocked = _one_effective(facts.flow_id, "STOCKED", events, reversed_ids)
    scrapped = _one_effective(facts.flow_id, "SCRAPPED", events, reversed_ids)
    return {
        "run_id": run_id,
        "flow_id": facts.flow_id,
        "part_number": facts.part_number,
        "component_id": facts.component_id,
        "flow_status": facts.status,
        "movement_count": facts.movements,
        "first_movement_id": facts.first_movement_id,
        "last_movement_id": facts.last_movement_id,
        "first_occurred_at": facts.first_occurred_at,
        "last_occurred_at": facts.last_occurred_at,
        "content_sha256": content_sha256,
        "received_quantity": None if received is None else int(received.quantity),
        "received_demand_id": None if received is None else _demand_id(received),
        "received_at": None if received is None else _parse_time(received.occurred_at),
        "adjusted_quantity": None if adjusted is None else int(adjusted.quantity),
        "stocked_quantity": None if stocked is None else int(stocked.quantity),
        "stocked_area_id": None if stocked is None else int(stocked.to_area_id),
        "scrapped_quantity": None if scrapped is None else int(scrapped.quantity),
        "scrapped_area_id": None if scrapped is None else int(scrapped.to_area_id),
        "void_command_ids": sorted(set(void_command_ids)),
    }


def _record_json(record: Mapping[str, Any]) -> dict[str, Any]:
    """A record as the flows file holds it (no run id, times as UTC text)."""
    return {
        key: iso(value) if isinstance(value, datetime.datetime) else value
        for key, value in record.items()
        if key != "run_id"
    }


def _reversals(connection: Connection) -> tuple[set[int], set[str]]:
    """The Movements the archived flows' REVERSED rows undo, and their commands' ids.

    Every REVERSED row, and only one, names the Movement it undoes
    (``ck_part_movements_reverses_shape``); a record counts no undone
    Movement, and the lineage edge of an undone command is void.
    """
    reversed_ids: set[int] = set()
    commands: set[str] = set()
    for row in connection.execute(
        text(
            f"SELECT o.id, o.device_event_id FROM {SELECTION} a"
            " JOIN part_movements r ON r.quantity_flow_id = a.flow_id"
            " JOIN part_movements o ON o.id = r.reverses_movement_id"
            " WHERE r.reverses_movement_id IS NOT NULL"
        )
    ):
        reversed_ids.add(int(row.id))
        commands.add(str(row.device_event_id))
    return reversed_ids, commands


@dataclass
class _Written:
    counts: dict[str, int]
    files: dict[str, dict[str, Any]]
    manifest_sha256: str
    archive_bytes: int


#: Records inserted per statement while the flows file is written.
_RECORD_BATCH: Final = 1000


@dataclass
class _OpenFlow:
    """A flow whose Movements are still coming: its content hash so far and its record's events."""

    hasher: Any = field(default_factory=hashlib.sha256)
    events: list[_Event] = field(default_factory=list)


#: Every archived Movement by id, with, on the flow's last Movement only, the
#: facts its record carries; the hash join with the analyzed archived set
#: keeps the id order of the primary key, so nothing is sorted.
_ARCHIVED_MOVEMENTS_SQL: Final = (
    "SELECT m.id, m.quantity_flow_id, to_jsonb(m) AS row,"
    + ",".join(
        f" CASE WHEN m.id = a.last_movement_id THEN a.{column} END AS {column}"
        for column in (
            "component_id",
            "part_number",
            "status",
            "movements",
            "first_movement_id",
            "first_occurred_at",
            "last_occurred_at",
        )
    )
    + f" FROM {SELECTION} a JOIN part_movements m ON m.quantity_flow_id = a.flow_id ORDER BY m.id"
)


def _write_movements(
    connection: Connection,
    folder: Path,
    run_id: int,
    selection: _Selection,
    reversed_ids: set[int],
    void_by_flow: dict[int, list[str]],
    context: _Context,
) -> archive_files.JsonLinesWriter:
    """The Movements file, and each flow's record once its last Movement is written.

    A flow's Movements are hashed as they come; at its last Movement its
    record is complete and goes to the database in batches (rolled back with
    the export's transaction if a later check fails), and what the flow kept
    is released. Only flows whose Movements are still coming are held.
    """
    writer = archive_files.JsonLinesWriter(folder / MOVEMENTS_FILE)
    open_flows: dict[int, _OpenFlow] = {}
    batch: list[dict[str, Any]] = []
    recorded = 0
    try:
        for row in connection.execute(text(_ARCHIVED_MOVEMENTS_SQL), execution_options=_STREAM):
            movement_id, flow_id, value = int(row.id), int(row.quantity_flow_id), row.row
            line = archive_files.canonical_json(value) + b"\n"
            writer.write_line(movement_id, line)
            flow = open_flows.get(flow_id)
            if flow is None:
                flow = open_flows[flow_id] = _OpenFlow()
            flow.hasher.update(line)
            if value.get("movement_type") in _RECORD_KINDS:
                flow.events.append(_event(movement_id, value))
            context.add_movement(value)
            if row.component_id is None:
                continue
            del open_flows[flow_id]
            facts = FlowFacts(
                flow_id,
                int(row.component_id),
                str(row.part_number),
                str(row.status),
                int(row.movements),
                int(row.first_movement_id),
                movement_id,
                row.first_occurred_at,
                row.last_occurred_at,
            )
            record = _record(
                run_id,
                facts,
                flow.events,
                reversed_ids,
                flow.hasher.hexdigest(),
                void_by_flow.pop(flow_id, ()),
            )
            for key in ("stocked_area_id", "scrapped_area_id"):
                context.add_int(context.areas, record[key])
            batch.append(record)
            recorded += 1
            if len(batch) >= _RECORD_BATCH:
                connection.execute(insert(_RECORDS), batch)
                batch = []
        if batch:
            connection.execute(insert(_RECORDS), batch)
    finally:
        writer.close()
    if open_flows or recorded != selection.flows:
        raise _RunFailed(
            f"The export recorded {recorded} of {selection.flows} archived flows; the Movements of"
            f" {len(open_flows)} ended before their last one. Nothing was archived; this is a"
            " defect to report."
        )
    return writer


#: The record columns the flows file holds (``_record_json``: no run id).
_RECORD_COLUMNS: Final = (
    "flow_id",
    "part_number",
    "component_id",
    "flow_status",
    "movement_count",
    "first_movement_id",
    "last_movement_id",
    "first_occurred_at",
    "last_occurred_at",
    "content_sha256",
    "received_quantity",
    "received_demand_id",
    "received_at",
    "adjusted_quantity",
    "stocked_quantity",
    "stocked_area_id",
    "scrapped_quantity",
    "scrapped_area_id",
    "void_command_ids",
)


def _context_sections(
    connection: Connection, run_id: int, context: _Context, requester_id: int
) -> Iterator[tuple[str, Iterator[dict[str, Any]]]]:
    """``context.json`` in key order: the master data the archived rows name, streamed.

    The demand lines are those the records name, their Work Orders those
    lines', and the Part Numbers the archived flows'; the database selects
    them, so nothing that grows with the archived flows is held here.
    """
    tables = {name: (table, key, columns) for name, table, key, columns in _CONTEXT_TABLES}
    demands = "SELECT received_demand_id FROM history_archived_flows WHERE run_id = :run_id"
    chosen: dict[str, str | Sequence[object]] = {
        "areas": sorted(context.areas),
        "machines": sorted(context.machines),
        "operations": sorted(context.operations),
        "part_numbers": f"SELECT part_number FROM {SELECTION}",
        "scan_stations": sorted(context.stations),
        "users": sorted(context.users | {requester_id}),
        "work_order_demands": demands,
        "work_orders": f"SELECT work_order_id FROM work_order_demands WHERE id IN ({demands})",
        "workers": sorted(context.workers),
    }
    for name in sorted(chosen):
        yield name, _rows(connection, *tables[name], chosen[name], run_id)


#: (document key, table, key column, columns) of ``context.json``; never images,
#: badges or credentials.
_CONTEXT_TABLES: Final = (
    ("areas", "areas", "id", "id, department_id, name, barcode_value, is_terminal"),
    ("operations", "operations", "id", "id, area_id, code, name, description, is_external"),
    ("machines", "machines", "id", "id, area_id, name, asset_tag"),
    ("workers", "workers", "id", "id, name"),
    ("scan_stations", "scan_stations", "station_id", "station_id, area_id"),
    (
        "work_orders",
        "work_orders",
        "id",
        "id, work_order_number, received_date, due_date, completed_at",
    ),
    (
        "work_order_demands",
        "work_order_demands",
        "id",
        "id, work_order_id, part_number, request_type, requested_quantity, due_date, job_numbers",
    ),
    ("users", "users", "id", "id, login_name, display_name"),
    ("part_numbers", "part_numbers", "part_number", "part_number, name, current_revision, erp_id"),
)


def _rows(
    connection: Connection,
    table: str,
    key: str,
    columns: str,
    chosen: str | Sequence[object],
    run_id: int,
) -> Iterator[dict[str, Any]]:
    """The chosen rows of one context table as ``to_jsonb``, in key order, streamed.

    ``chosen`` is the keys themselves or a subquery (of ``:run_id``) giving them.
    """
    params: dict[str, object]
    if isinstance(chosen, str):
        where, params = f"{key} IN ({chosen})", {"run_id": run_id}
    elif not chosen:
        return
    else:
        array = "text[]" if key in ("station_id", "part_number") else "integer[]"
        where, params = f"{key} = ANY(CAST(:ids AS {array}))", {"ids": list(chosen)}
    for row in connection.execute(
        text(
            f"SELECT to_jsonb(t) AS value FROM (SELECT {columns} FROM {table}"
            f" WHERE {where}) t ORDER BY t.{key}"
        ),
        params,
        execution_options=_STREAM,
    ):
        yield dict(row.value)


_README: Final = """PartFlow Movement-history archive
==================================

This folder is one archival run of PartFlow: the Movement history of whole
flow components (Quantity Flows connected by splits and merges) whose every
flow was closed and whose every Movement happened before the cutoff. It is
read with standard tools and needs no PartFlow:

    sha256sum -c SHA256SUMS            check every file (run it in this folder)
    zcat movements.jsonl.gz | jq .     read the Movements

Files
-----

movements.jsonl.gz
    Every archived Movement (table part_movements), one JSON object per
    line, ordered by id, with every column of the table under its own
    name: part_number is the canonical Part Number, device_event_id names
    the command that wrote the row, and reverses_movement_id the Movement
    a REVERSED row undoes.

flows.jsonl.gz
    Every archived Quantity Flow, one per line: "component_id" (the smallest
    flow id of its component), "flow" (every column of quantity_flows) and
    "record" (its carry-forward record: flow_status, movement_count,
    first_movement_id, last_movement_id, first_occurred_at,
    last_occurred_at, content_sha256 — the SHA-256 of the flow's lines in
    movements.jsonl.gz, in id order —, received_quantity,
    received_demand_id, received_at, adjusted_quantity, stocked_quantity,
    stocked_area_id, scrapped_quantity, scrapped_area_id and
    void_command_ids, the commands of its undone splits and merges).

lineage.jsonl.gz
    The lineage edges of the archived components (every column of
    quantity_flow_lineage) with "void": true for an edge whose split or
    merge was undone.

assigned_routes.jsonl.gz
    The Assigned Routes of the archived Planned flows: "assigned_route"
    (every column) and "steps" (every column of each step, in order).

context.json
    The Areas, Operations, Machines, Workers, Scan Stations, Work Orders,
    demand lines, Users and Part Numbers the rows name, with their codes and
    names as they were when the archive was written.

manifest.json
    The format and its version, the run (number, cutoff, reason, requester),
    the archive's groups, the database revision, the release, the counts,
    and for every other file its SHA-256, size, rows and id-set hash (the
    SHA-256 of the sorted ids, one decimal number and a line break each).

SHA256SUMS
    The SHA-256 of every other file, in the format sha256sum reads.

All times are UTC. Nothing in this folder is ever changed or deleted by
PartFlow.
"""


def _write_archive(
    connection: Connection,
    folder: Path,
    run: RowMapping,
    name: str,
    selection: _Selection,
) -> _Written:
    run_id = int(run["id"])
    context = _Context()
    reversed_ids, void_commands = _reversals(connection)

    # The lineage first: a record names the commands of its void edges.
    lineage = archive_files.JsonLinesWriter(folder / LINEAGE_FILE)
    void_by_flow: dict[int, list[str]] = defaultdict(list)
    try:
        for row in connection.execute(
            text(
                "SELECT id, to_jsonb(l) AS row FROM quantity_flow_lineage l"
                f" WHERE l.parent_flow_id IN (SELECT flow_id FROM {SELECTION}) ORDER BY id"
            ),
            execution_options=_STREAM,
        ):
            value = dict(row.row)
            void = str(value["device_event_id"]) in void_commands
            value["void"] = void
            lineage.write(int(row.id), value)
            if void:
                for key in ("parent_flow_id", "child_flow_id"):
                    void_by_flow[int(value[key])].append(str(value["device_event_id"]))
    finally:
        lineage.close()

    movements = _write_movements(
        connection, folder, run_id, selection, reversed_ids, void_by_flow, context
    )

    # The flows file in flow order, each flow with the record written above.
    flows_writer = archive_files.JsonLinesWriter(folder / FLOWS_FILE)
    try:
        for row in connection.execute(
            text(
                "SELECT f.current_area_id, f.current_machine_id, to_jsonb(f) AS flow,"
                + ",".join(f" r.{column}" for column in _RECORD_COLUMNS)
                + f" FROM {SELECTION} a"
                " JOIN history_archived_flows r ON r.run_id = :run_id AND r.flow_id = a.flow_id"
                " JOIN quantity_flows f ON f.id = a.flow_id ORDER BY a.flow_id"
            ),
            {"run_id": run_id},
            execution_options=_STREAM,
        ):
            record = {column: getattr(row, column) for column in _RECORD_COLUMNS}
            flows_writer.write(
                int(row.flow_id),
                {
                    "component_id": row.component_id,
                    "flow": row.flow,
                    "record": _record_json(record),
                },
            )
            context.add_int(context.areas, row.current_area_id)
            context.add_int(context.machines, row.current_machine_id)
    finally:
        flows_writer.close()

    routes = archive_files.JsonLinesWriter(folder / ROUTES_FILE)
    try:
        # One row per route with its steps, so no route's steps wait for the others.
        for row in connection.execute(
            text(
                "SELECT r.id, to_jsonb(r) AS row, (SELECT coalesce(jsonb_agg(to_jsonb(s)"
                " ORDER BY s.sequence, s.id), '[]'::jsonb) FROM assigned_route_steps s"
                " WHERE s.assigned_route_id = r.id) AS steps FROM assigned_routes r"
                " WHERE r.id IN (SELECT f.assigned_route_id FROM quantity_flows f"
                f" JOIN {SELECTION} a ON a.flow_id = f.id) ORDER BY r.id"
            ),
            execution_options=_STREAM,
        ):
            steps: list[dict[str, Any]] = row.steps
            for step in steps:
                context.add_int(context.areas, step.get("area_id"))
                context.add_int(context.operations, step.get("operation_id"))
                context.add_int(context.machines, step.get("preferred_machine_id"))
            routes.write(int(row.id), {"assigned_route": row.row, "steps": steps})
    finally:
        routes.close()

    archive_files.write_readable_sections(
        folder / CONTEXT_FILE,
        _context_sections(connection, run_id, context, int(run["requested_by_user_id"])),
    )
    archive_files.write_document(folder / README_FILE, _README.encode("utf-8"))

    counts = {
        "components": selection.components,
        "flows": selection.flows,
        "movements": movements.rows,
        "lineage_edges": lineage.rows,
        "assigned_routes": routes.rows,
    }
    files: dict[str, dict[str, Any]] = {}
    for writer in (routes, flows_writer, lineage, movements):
        file_digest = archive_files.digest(writer.path)
        files[writer.path.name] = {
            "sha256": file_digest.sha256,
            "bytes": file_digest.size,
            "rows": writer.rows,
            "id_set_sha256": writer.ids.sha256(),
        }
    for document in (CONTEXT_FILE, README_FILE):
        file_digest = archive_files.digest(folder / document)
        files[document] = {"sha256": file_digest.sha256, "bytes": file_digest.size}
    manifest = _manifest(connection, run, name, selection, counts, files)
    archive_files.write_document(folder / MANIFEST_FILE, archive_files.readable_json(manifest))
    manifest_digest = archive_files.digest(folder / MANIFEST_FILE)
    sums = {file: entry["sha256"] for file, entry in files.items()}
    sums[MANIFEST_FILE] = manifest_digest.sha256
    archive_files.write_document(folder / SUMS_FILE, archive_files.sums_text(sums))
    total = sum(archive_files.digest(folder / file).size for file in ARCHIVE_FILES)
    return _Written(counts, files, manifest_digest.sha256, total)


def _manifest(
    connection: Connection,
    run: RowMapping,
    name: str,
    selection: _Selection,
    counts: Mapping[str, int],
    files: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    configured = get_settings()
    zone = history_archive.site_zone()
    revisions = [
        str(value)
        for value in connection.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()
    ]
    return {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_FORMAT_VERSION,
        "archive_name": name,
        "created_at": iso(_now()),
        "groups": [history_archive.GROUP_MOVEMENT_HISTORY],
        "run": {
            "id": int(run["id"]),
            "cutoff": iso(run["cutoff"]),
            "cutoff_date": history_archive.site_date_of(run["cutoff"], zone).isoformat(),
            "reason": run["reason"],
            "requested_by_user_id": int(run["requested_by_user_id"]),
            "requested_at": iso(run["requested_at"]),
        },
        "site_timezone": configured.site_timezone,
        "alembic_revision": ",".join(revisions) or None,
        "release": {"tag": configured.release_tag, "commit": configured.release_commit},
        "counts": dict(counts),
        "first_occurred_at": iso(selection.first_occurred_at),
        "last_occurred_at": iso(selection.last_occurred_at),
        "files": {file: dict(entry) for file, entry in sorted(files.items())},
    }


#: Commands with a Movement outside the archived components, or in two of them.
#: Both joins with the archived set probe its primary key, and its statistics
#: are true, so no plan rescans it per Movement however the LIMIT is costed.
COMMAND_SPANS_SQL: Final = (
    "SELECT c.device_event_id FROM (SELECT DISTINCT m.device_event_id"
    f" FROM {SELECTION} a JOIN part_movements m ON m.quantity_flow_id = a.flow_id) c"
    " JOIN part_movements o ON o.device_event_id = c.device_event_id"
    f" LEFT JOIN {SELECTION} s ON s.flow_id = o.quantity_flow_id"
    " GROUP BY c.device_event_id"
    " HAVING count(DISTINCT s.component_id) > 1 OR bool_or(s.flow_id IS NULL)"
    " ORDER BY c.device_event_id LIMIT 5"
)


def _command_spans(connection: Connection) -> list[str]:
    """Commands with a Movement outside the archived components, or in two of them."""
    return [str(value) for value in connection.execute(text(COMMAND_SPANS_SQL)).scalars()]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _start_run(engine: Engine, run_id: int) -> RowMapping:
    """APPROVED → RUNNING (its own transaction); a cutoff now too new fails the run."""
    with engine.connect() as connection, connection.begin():
        run = _run_row(connection, run_id, lock=True)
        if run is None:
            raise _Refused(
                "unknown_run", f"There is no archival run {run_id}. Nothing was changed."
            )
        if run["state"] != _STATE.APPROVED:
            raise _Refused(
                "wrong_state",
                f"Run {run_id} is {run['state']}, not APPROVED. Nothing was changed.",
            )
        stored = history_archive.read_settings(connection)
        if not history_archive.shortest_period_allows(
            run["cutoff"], stored.shortest_retention_months
        ):
            raise _RunFailed(
                f"The cutoff {iso(run['cutoff'])} is newer than the shortest allowed period of"
                f" {stored.shortest_retention_months} months allows now. Nothing was archived;"
                " request a new run."
            )
        return _change(connection, run, "export", state=_STATE.RUNNING.value, started_at=_now())


def export(engine: Engine | None, folders: JobFolders, run_id: int) -> JobReport:
    """Export an APPROVED run's archive into ``.partial`` and record what it holds."""

    def work(engine: Engine, report: JobReport) -> None:
        archive_dir = _require_archive_dir(folders)
        with _job_lock(engine):
            run = _start_run(engine, run_id)
            report.run_started = True
            report.state = str(run["state"])
            _export(engine, archive_dir, run, report)

    return _command("export", engine, work, run_id=run_id)


def _export(engine: Engine, archive_dir: Path, run: RowMapping, report: JobReport) -> None:
    run_id = int(run["id"])
    name = f"run-{run_id}-{_stamp(_now())}"
    if backup_files.exists(archive_dir / name):
        raise _RunFailed(
            f"An archive folder named {name} already exists in the archive folder (after a"
            " restore, run numbers can repeat). Nothing was archived; request a new run."
        )
    partial_root = archive_dir / PARTIAL_DIR
    backup_files.make_private_directory(partial_root)
    folder = partial_root / name
    written: _Written | None = None
    with engine.connect() as base:
        connection = base.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            _utc(connection)
            _mark(connection, run_id)
            selection = _select_archived(connection, run["cutoff"])
            if not selection.flows:
                after = _change(
                    connection,
                    run,
                    "export",
                    state=_STATE.NOTHING_ELIGIBLE.value,
                    finished_at=_now(),
                )
            else:
                written, after = _export_snapshot(connection, folder, run, name, selection)
    report.state = str(after["state"])
    if written is None:
        report.details["counts"] = dict.fromkeys(
            ("components", "flows", "movements", "lineage_edges", "assigned_routes"), 0
        )
        report.result, report.exit_code = "nothing_eligible", EXIT_DONE
        return
    report.details.update(
        {
            "archive_name": name,
            "manifest_sha256": written.manifest_sha256,
            "archive_bytes": written.archive_bytes,
            "counts": written.counts,
        }
    )
    report.result, report.exit_code = "exported", EXIT_DONE


def _export_snapshot(
    connection: Connection,
    folder: Path,
    run: RowMapping,
    name: str,
    selection: _Selection,
) -> tuple[_Written, RowMapping]:
    """The files and the records of the eligible flows, inside the export's snapshot."""
    _check_against_preview(run, selection)
    if backup_files.exists(folder):
        raise _RunFailed(f"The unfinished folder {PARTIAL_DIR}/{name} already exists.")
    backup_files.make_private_directory(folder)
    try:
        written = _write_archive(connection, folder, run, name, selection)
        spans = _command_spans(connection)
        if spans:
            raise _RunFailed(
                "These commands have Movements outside the archived components or in two of"
                f" them: {', '.join(spans)}. Nothing was archived; this is a defect to report."
            )
        after = _change(
            connection,
            run,
            "export",
            archive_name=name,
            exported_at=_now(),
            exported_components=written.counts["components"],
            exported_flows=written.counts["flows"],
            exported_movements=written.counts["movements"],
            exported_lineage_edges=written.counts["lineage_edges"],
            exported_assigned_routes=written.counts["assigned_routes"],
            archive_bytes=written.archive_bytes,
            manifest_sha256=written.manifest_sha256,
        )
    except BaseException:
        backup_files.remove_tree(folder)
        raise
    return written, after


def _check_against_preview(run: RowMapping, selection: _Selection) -> None:
    found = {
        "components": selection.components,
        "flows": selection.flows,
        "movements": selection.movements,
    }
    larger = [
        f"{key} {found[key]} > {int(run[f'preview_{key}'])}"
        for key in ("components", "flows", "movements")
        if found[key] > int(run[f"preview_{key}"])
    ]
    if larger:
        raise _RunFailed(
            "The export found more than the run's preview ("
            + ", ".join(larger)
            + "). Nothing was archived; this is a defect to report."
        )


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@dataclass
class _FileFacts:
    #: Per archived flow, the content SHA-256 its record in the flows file holds.
    content: dict[int, str]


def _check_files(folder: Path, run: RowMapping) -> _FileFacts:
    """Every file against SHA256SUMS and the manifest; counts, id sets and flow hashes."""
    if not backup_files.is_real_directory(folder):
        raise _RunFailed(f"The exported folder {folder.name} is missing. Nothing was published.")
    names = backup_files.entry_names(folder)
    if tuple(names) != ARCHIVE_FILES:
        raise _RunFailed(
            f"The archive folder {folder.name} holds {', '.join(names)} instead of the archive"
            " files. Nothing was published."
        )
    try:
        sums = archive_files.read_sums(folder / SUMS_FILE)
    except archive_files.SumsError as exc:
        raise _RunFailed(f"{exc}. Nothing was published.") from exc
    if set(sums) != set(ARCHIVE_FILES) - {SUMS_FILE}:
        raise _RunFailed(
            "SHA256SUMS does not list exactly the archive files. Nothing was published."
        )
    for file, expected in sorted(sums.items()):
        if archive_files.digest(folder / file).sha256 != expected:
            raise _RunFailed(
                f"{file} does not match its SHA-256 in SHA256SUMS. Nothing was published."
            )
    if sums[MANIFEST_FILE] != run["manifest_sha256"]:
        raise _RunFailed(
            "manifest.json is not the manifest the export recorded. Nothing was published."
        )
    manifest = json.loads(backup_files.read_bytes(folder / MANIFEST_FILE))
    listed = manifest.get("files", {})
    for file in (*DATA_FILES, CONTEXT_FILE, README_FILE):
        entry = listed.get(file, {})
        size = archive_files.digest(folder / file).size
        if entry.get("sha256") != sums[file] or entry.get("bytes") != size:
            raise _RunFailed(f"manifest.json does not describe {file}. Nothing was published.")
    content: dict[int, str] = {}
    hashes = _FileHashes()
    for file in DATA_FILES:  # the flows file before the Movements file
        ids = archive_files.IdSet()
        rows = 0
        try:
            for line, value in archive_files.read_json_lines(folder / file):
                rows += 1
                if file == MOVEMENTS_FILE:
                    movement_id = int(value["id"])
                    ids.add(movement_id)
                    hashes.add(int(value["quantity_flow_id"]), movement_id, line)
                elif file == FLOWS_FILE:
                    flow_id = int(value["flow"]["id"])
                    ids.add(flow_id)
                    record = value["record"]
                    content[flow_id] = record.get("content_sha256")
                    hashes.expect(flow_id, content[flow_id], record.get("last_movement_id"))
                elif file == LINEAGE_FILE:
                    ids.add(int(value["id"]))
                else:
                    ids.add(int(value["assigned_route"]["id"]))
        except archive_files.IdOrderError as exc:
            raise _RunFailed(
                f"{file} does not hold its rows in id order ({exc}). Nothing was published."
            ) from exc
        entry = listed[file]
        if entry.get("rows") != rows or entry.get("id_set_sha256") != ids.sha256():
            raise _RunFailed(
                f"{file} does not hold the rows its manifest entry counts. Nothing was published."
            )
    hashes.check()
    return _FileFacts(content)


class _FileHashes:
    """Each flow's Movements in the Movements file against the content SHA-256 of its record.

    The file is in id order, so a flow's lines come interleaved with others';
    a flow's hash is held only until the line of its record's last Movement,
    then checked and released. The first problem is kept and raised by
    ``check``, after the file's own counts were checked: the smallest flow
    whose lines do not give its record's hash (or that has no line, or a
    line after its last Movement), else a line of a flow without a record.
    """

    def __init__(self) -> None:
        #: flow → (its record's SHA-256, its last Movement id); None once checked.
        self._expected: dict[int, tuple[str, int] | None] = {}
        self._open: dict[int, Any] = {}
        self._mismatch: int | None = None
        self._without_record = False

    def expect(self, flow_id: int, sha256: object, last_movement_id: object) -> None:
        last = -1  # not a Movement id: the flow never checks out
        if isinstance(last_movement_id, int) and not isinstance(last_movement_id, bool):
            last = last_movement_id
        self._expected[flow_id] = (str(sha256), last)

    def _mismatched(self, flow_id: int) -> None:
        if self._mismatch is None or flow_id < self._mismatch:
            self._mismatch = flow_id

    def add(self, flow_id: int, movement_id: int, line: bytes) -> None:
        if flow_id not in self._expected:
            self._without_record = True
            return
        expected = self._expected[flow_id]
        if expected is None:
            self._mismatched(flow_id)  # a line after its last Movement
            return
        hasher = self._open.get(flow_id)
        if hasher is None:
            hasher = self._open[flow_id] = hashlib.sha256()
        hasher.update(line)
        if movement_id == expected[1]:
            del self._open[flow_id]
            self._expected[flow_id] = None
            if hasher.hexdigest() != expected[0]:
                self._mismatched(flow_id)

    def check(self) -> None:
        for flow_id, expected in self._expected.items():
            if expected is not None:  # its last Movement never came
                self._mismatched(flow_id)
        if self._mismatch is not None:
            raise _RunFailed(
                f"The Movements of Quantity Flow {self._mismatch} in {MOVEMENTS_FILE} do not match"
                " its record. Nothing was published."
            )
        if self._without_record:
            raise _RunFailed(
                f"{MOVEMENTS_FILE} holds Movements of flows without a record. Nothing was"
                " published."
            )


def verify(engine: Engine | None, folders: JobFolders, run_id: int) -> JobReport:
    """Verify the exported archive against its files and a fresh snapshot; publish it."""

    def work(engine: Engine, report: JobReport) -> None:
        archive_dir = _require_archive_dir(folders)
        with _job_lock(engine):
            with engine.connect() as connection:
                run = _run_row(connection, run_id, lock=False)
            if run is None:
                raise _Refused(
                    "unknown_run", f"There is no archival run {run_id}. Nothing was changed."
                )
            if run["state"] != _STATE.RUNNING or run["exported_at"] is None or run["verified_at"]:
                raise _Refused(
                    "wrong_state",
                    f"Run {run_id} is {run['state']} and not waiting for its verification."
                    " Nothing was changed.",
                )
            report.run_started = True
            name = str(run["archive_name"])
            folder = archive_dir / PARTIAL_DIR / name
            facts = _check_files(folder, run)
            target = archive_dir / name
            if backup_files.exists(target):
                raise _RunFailed(
                    f"An archive folder named {name} already exists in the archive folder."
                    " Nothing was published; request a new run."
                )
            after, skipped = _verify_in_database(engine, run, facts, folder, target)
            nothing_left = skipped[0] >= int(after["exported_components"])
            if nothing_left:
                # Every component changed after the export: nothing is left to
                # purge, so neither a copy nor a backup is needed.
                after, receipt = _finish_nothing_to_purge(engine, archive_dir, int(run["id"]))
                report.details["receipt"] = receipt
        report.state = str(after["state"])
        report.details.update(
            {
                "archive_name": name,
                "manifest_sha256": run["manifest_sha256"],
                "skipped_components": skipped[0],
                "skipped_flows": skipped[1],
            }
        )
        report.result = "nothing_to_purge" if nothing_left else "verified"
        report.exit_code = EXIT_DONE

    return _command("verify", engine, work, run_id=run_id)


def _finish_nothing_to_purge(
    engine: Engine, archive_dir: Path, run_id: int
) -> tuple[RowMapping, dict[str, Any]]:
    """RUNNING → COMPLETED_WITH_SKIPS (NOTHING_PURGED) with its receipt, no copy, no backup."""
    with engine.connect() as connection, connection.begin():
        current = _run_row(connection, run_id, lock=True)
        if current is None or current["state"] != _STATE.RUNNING:  # pragma: no cover - job lock
            raise _Refused("wrong_state", f"Run {run_id} changed meanwhile. Nothing was changed.")
        state = _STATE.COMPLETED_WITH_SKIPS.value
        result = HistoryArchiveRunResult.NOTHING_PURGED.value
        content = _receipt_content(connection, current, state=state, result=result, failure=None)
        digest = _write_receipt(archive_dir, str(current["archive_name"]), content)
        after = _change(
            connection,
            current,
            "verify",
            state=state,
            result=result,
            receipt_sha256=digest,
            finished_at=_now(),
        )
    receipt = {
        "name": f"{current['archive_name']}{RECEIPT_SUFFIX}",
        "sha256": digest,
        "copied_to": None,
        "copy_problem": None,
    }
    return after, receipt


_VERIFY_SELECTION: Final = f"""
CREATE TEMPORARY TABLE {SELECTION.removeprefix("pg_temp.")} (
    flow_id integer PRIMARY KEY,
    component_id integer NOT NULL,
    content_sha256 text NOT NULL,
    flow_status text NOT NULL,
    live_status text,
    live_last_movement_id bigint
) ON COMMIT DROP
"""

#: Every live Movement of the run's flows by id, with, on a flow's last one
#: only, what its record says (the same plan as the export's Movements).
_LIVE_MOVEMENTS_SQL: Final = (
    "SELECT m.id, m.quantity_flow_id, to_jsonb(m) AS row,"
    " CASE WHEN m.id = a.live_last_movement_id THEN a.component_id END AS component_id,"
    " CASE WHEN m.id = a.live_last_movement_id THEN a.content_sha256 END AS content_sha256"
    f" FROM {SELECTION} a JOIN part_movements m ON m.quantity_flow_id = a.flow_id ORDER BY m.id"
)


def _verify_in_database(
    engine: Engine,
    run: RowMapping,
    facts: _FileFacts,
    folder: Path,
    target: Path,
) -> tuple[RowMapping, tuple[int, int]]:
    """The records against the archive, and the live history against the records.

    The run's records go to the archived set with each flow's live status
    and last live Movement; each flow's live Movements are hashed in id
    order and checked at its last one, so only flows whose Movements are
    still coming are held. A component with a flow whose live Movements or
    status differ from its record is skipped.
    """
    run_id = int(run["id"])
    with engine.connect() as base:
        connection = base.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            _utc(connection)
            _mark(connection, run_id)
            connection.execute(text(_VERIFY_SELECTION))
            connection.execute(
                text(
                    f"INSERT INTO {SELECTION} SELECT r.flow_id, r.component_id, r.content_sha256,"
                    " r.flow_status, f.status, (SELECT max(m.id) FROM part_movements m"
                    " WHERE m.quantity_flow_id = r.flow_id) FROM history_archived_flows r"
                    " LEFT JOIN quantity_flows f ON f.id = r.flow_id WHERE r.run_id = :run_id"
                ),
                {"run_id": run_id},
            )
            connection.execute(text(f"ANALYZE {SELECTION}"))
            stored = int(connection.execute(text(f"SELECT count(*) FROM {SELECTION}")).scalar_one())
            if stored != len(facts.content):
                raise _RunFailed(
                    "The archive's flows are not the flows the export recorded. Nothing was"
                    " published."
                )
            for row in connection.execute(
                text(f"SELECT flow_id, content_sha256 FROM {SELECTION} ORDER BY flow_id"),
                execution_options=_STREAM,
            ):
                flow_id = int(row.flow_id)
                if flow_id not in facts.content:
                    raise _RunFailed(
                        "The archive's flows are not the flows the export recorded. Nothing was"
                        " published."
                    )
                if row.content_sha256 != facts.content[flow_id]:
                    raise _RunFailed(
                        f"The record of Quantity Flow {flow_id} differs from the archive."
                        " Nothing was published."
                    )
            # A flow without a live Movement or with another status has changed.
            changed = {
                int(value)
                for value in connection.execute(
                    text(
                        f"SELECT DISTINCT component_id FROM {SELECTION}"
                        " WHERE live_last_movement_id IS NULL"
                        " OR live_status IS DISTINCT FROM flow_status"
                    )
                ).scalars()
            }
            open_hashers: dict[int, Any] = {}
            for row in connection.execute(text(_LIVE_MOVEMENTS_SQL), execution_options=_STREAM):
                flow_id = int(row.quantity_flow_id)
                hasher = open_hashers.get(flow_id)
                if hasher is None:
                    hasher = open_hashers[flow_id] = hashlib.sha256()
                hasher.update(archive_files.canonical_json(row.row) + b"\n")
                if row.component_id is not None:
                    del open_hashers[flow_id]
                    if hasher.hexdigest() != row.content_sha256:
                        changed.add(int(row.component_id))
            if open_hashers:  # pragma: no cover - one snapshot: every last Movement comes
                raise _RunFailed(
                    "The live Movements of some archived flows ended before their last one."
                    " Nothing was published; this is a defect to report."
                )
            skipped_flows = 0
            if changed:
                skipped_flows = int(
                    connection.execute(
                        text(
                            "UPDATE history_archived_flows SET skipped_at = :moment"
                            " WHERE run_id = :run_id"
                            " AND component_id = ANY(CAST(:components AS integer[]))"
                        ),
                        {"moment": _now(), "run_id": run_id, "components": sorted(changed)},
                    ).rowcount
                )
            after = _change(
                connection,
                run,
                "verify",
                verified_at=_now(),
                skipped_components=len(changed),
                skipped_flows=skipped_flows,
            )
            backup_files.move_directory(folder, target)
    return after, (len(changed), skipped_flows)


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


def _copy_archive(source: Path, copy_dir: Path, name: str) -> Path:
    """Copy a published archive into the copy folder, re-read and check it, publish it there."""
    sums = archive_files.read_sums(source / SUMS_FILE)
    target = copy_dir / name
    if backup_files.exists(target):
        _check_copy(target, sums)
        return target
    partial_root = copy_dir / PARTIAL_DIR
    backup_files.make_private_directory(partial_root)
    partial = partial_root / name
    if backup_files.exists(partial):
        backup_files.remove_tree(partial)
    backup_files.make_private_directory(partial)
    try:
        for file in ARCHIVE_FILES:
            archive_files.copy_file(source / file, partial / file)
        _check_copy(partial, sums)
        backup_files.move_directory(partial, target)
    except BaseException:
        if backup_files.exists(partial):
            backup_files.remove_tree(partial)
        raise
    return target


class _CopyMismatchError(Exception):
    pass


def _check_copy(folder: Path, sums: Mapping[str, str]) -> None:
    if tuple(backup_files.entry_names(folder)) != ARCHIVE_FILES:
        raise _CopyMismatchError(f"{folder} does not hold exactly the archive files")
    if archive_files.read_sums(folder / SUMS_FILE) != dict(sums):
        raise _CopyMismatchError(f"{folder}/{SUMS_FILE} differs from the archive's")
    for file, expected in sums.items():
        if archive_files.digest(folder / file).sha256 != expected:
            raise _CopyMismatchError(f"{folder}/{file} does not match its SHA-256")


def _deleted_so_far(run: RowMapping) -> str:
    """What the run deleted, for a failure reason."""
    if run["state"] == _STATE.PURGING:
        return (
            "The purge stopped: whole components only were deleted, the purge receipt lists"
            " them; request a new run for the rest."
        )
    return "Nothing was deleted; request a new run."


def _check_published(source: Path, run: RowMapping) -> None:
    """The published archive is still the one the run verified (its manifest and checksums)."""
    try:
        sums = archive_files.read_sums(source / SUMS_FILE)
        if sums.get(MANIFEST_FILE) != run["manifest_sha256"]:
            raise _CopyMismatchError("its manifest is not the one the export recorded")
        _check_copy(source, sums)
    except (OSError, archive_files.SumsError, _CopyMismatchError) as exc:
        raise _RunFailed(
            f"The published archive {source.name} changed after its verification ({exc})."
            f" {_deleted_so_far(run)}"
        ) from exc


def copy(engine: Engine | None, folders: JobFolders, run_id: int) -> JobReport:
    """Copy the verified archive off the host, or accept a confirmation, or wait for a person.

    A copied or confirmed run stays (or becomes again) RUNNING: the host
    script takes the pre-purge backup next. The published folder is checked
    against its manifest SHA-256 and checksums right before a copy or a
    confirmation's acceptance — never while the run only waits (B1 review
    minor 7: no hourly re-hash while the copy folder stays unavailable).
    """

    def work(engine: Engine, report: JobReport) -> None:
        archive_dir = _require_archive_dir(folders)
        with _job_lock(engine):
            with engine.connect() as connection:
                run = _run_row(connection, run_id, lock=False)
            if run is None:
                raise _Refused(
                    "unknown_run", f"There is no archival run {run_id}. Nothing was changed."
                )
            fresh = (
                run["state"] == _STATE.RUNNING
                and run["verified_at"] is not None
                and run["copied_at"] is None
                and run["copy_confirmed_at"] is None
            )
            if not fresh and run["state"] != _STATE.WAITING_FOR_COPY:
                raise _Refused(
                    "wrong_state",
                    f"Run {run_id} is {run['state']} and not waiting for its off-host copy."
                    " Nothing was changed.",
                )
            report.run_started = True
            report.state = str(run["state"])
            name = str(run["archive_name"])
            source = archive_dir / name
            report.details["archive_name"] = name
            usable = _copy_dir_usable(folders)
            confirmed = run["copy_confirmed_at"] is not None
            copied_to: Path | None = None
            problem: str | None = None
            if folders.copy_dir is not None and not usable:
                problem = f"The copy folder {folders.copy_dir} does not exist or cannot be written."
            if usable or confirmed:
                _check_published_folder(source, run)
            if usable:
                assert folders.copy_dir is not None
                try:
                    copied_to = _copy_archive(source, folders.copy_dir, name)
                except (OSError, archive_files.SumsError, _CopyMismatchError) as exc:
                    problem = f"The copy into {folders.copy_dir} failed: {exc}"
            report.details["copied_to"] = None if copied_to is None else str(copied_to)
            report.details["copy_problem"] = problem
            after = _advance_copy(engine, run, copied=copied_to is not None)
        report.state = str(after["state"])
        if copied_to is not None:
            report.result = "copied"
        elif after["state"] == _STATE.RUNNING:
            report.result = "confirmed"
        else:
            report.result = "waiting_for_copy"
        report.exit_code = EXIT_DONE

    return _command("copy", engine, work, run_id=run_id)


def _check_published_folder(source: Path, run: RowMapping) -> None:
    if not backup_files.is_real_directory(source):
        raise _RunFailed(
            f"The published archive folder {source.name} is missing. {_deleted_so_far(run)}"
        )
    _check_published(source, run)


def _advance_copy(engine: Engine, run: RowMapping, *, copied: bool) -> RowMapping:
    """Copied or confirmed: RUNNING (the copy is audited); otherwise WAITING_FOR_COPY."""
    with engine.connect() as connection, connection.begin():
        current = _run_row(connection, int(run["id"]), lock=True)
        if current is None:  # pragma: no cover - runs are never deleted
            raise _Refused("unknown_run", "The run disappeared. Nothing was changed.")
        if current["state"] not in (_STATE.RUNNING, _STATE.WAITING_FOR_COPY):
            raise _Refused(
                "wrong_state",
                f"Run {current['id']} became {current['state']} meanwhile. Nothing was changed.",
            )
        if copied:
            return _change(
                connection,
                current,
                "copy",
                audit=True,
                state=_STATE.RUNNING.value,
                copied_at=_now(),
            )
        if current["copy_confirmed_at"] is not None:
            return _change(connection, current, "copy", state=_STATE.RUNNING.value)
        if current["state"] == _STATE.RUNNING:
            return _change(connection, current, "copy", state=_STATE.WAITING_FOR_COPY.value)
        return current


# ---------------------------------------------------------------------------
# The purge receipt
# ---------------------------------------------------------------------------


def _receipt_content(
    connection: Connection,
    run: RowMapping,
    *,
    state: str,
    result: str | None,
    failure: str | None,
) -> bytes:
    """The purge receipt of a run, as canonical JSON (one line and a line break).

    Derived from the run and its records only, never from the clock, so
    writing it again (a purge resumed after the receipt was written but
    before the run finished) gives the same bytes. Each component is
    ``PURGED`` (its Movements were deleted), ``SKIPPED`` (it changed after
    the export, found by the verification or the purge's re-check; its
    Movements stay) or ``RETAINED`` (a failed purge did not reach it; its
    Movements stay).
    """
    components: dict[int, dict[str, Any]] = {}
    for row in connection.execute(
        select(
            _RECORDS.c.component_id,
            _RECORDS.c.flow_id,
            _RECORDS.c.part_number,
            _RECORDS.c.movement_count,
            _RECORDS.c.purged_at,
            _RECORDS.c.skipped_at,
        )
        .where(_RECORDS.c.run_id == run["id"])
        .order_by(_RECORDS.c.component_id, _RECORDS.c.flow_id)
    ).mappings():
        outcome = (
            "PURGED"
            if row["purged_at"] is not None
            else "SKIPPED"
            if row["skipped_at"] is not None
            else "RETAINED"
        )
        entry = components.setdefault(
            int(row["component_id"]),
            {
                "component_id": int(row["component_id"]),
                "outcome": outcome,
                "part_numbers": [],
                "flow_ids": [],
                "movements": 0,
            },
        )
        if entry["outcome"] != outcome:
            raise _RunFailed(
                f"Component {entry['component_id']} of run {run['id']} is partly {outcome} and"
                f" partly {entry['outcome']}; only whole components are purged. This is a"
                " defect to report."
            )
        if row["part_number"] not in entry["part_numbers"]:
            entry["part_numbers"].append(str(row["part_number"]))
        entry["flow_ids"].append(int(row["flow_id"]))
        entry["movements"] += int(row["movement_count"])
    counts = {
        outcome.lower(): {
            "components": sum(1 for entry in components.values() if entry["outcome"] == outcome),
            "flows": sum(
                len(entry["flow_ids"])
                for entry in components.values()
                if entry["outcome"] == outcome
            ),
            "movements": sum(
                entry["movements"] for entry in components.values() if entry["outcome"] == outcome
            ),
        }
        for outcome in ("PURGED", "SKIPPED", "RETAINED")
    }
    backup = (
        None
        if run["pre_purge_backup_name"] is None
        else {
            "name": run["pre_purge_backup_name"],
            "dump_started_at": iso(run["pre_purge_backup_started_at"]),
        }
    )
    document = {
        "format": RECEIPT_FORMAT,
        "format_version": RECEIPT_FORMAT_VERSION,
        "archive_name": run["archive_name"],
        "manifest_sha256": run["manifest_sha256"],
        "run": {
            "id": int(run["id"]),
            "cutoff": iso(run["cutoff"]),
            "cutoff_date": history_archive.site_date_of(
                run["cutoff"], history_archive.site_zone()
            ).isoformat(),
            "state": state,
            "result": result,
            "failure_reason": failure,
        },
        "pre_purge_backup": backup,
        "purge_started_at": iso(run["purge_started_at"]),
        "counts": counts,
        "components": list(components.values()),
    }
    return archive_files.canonical_json(document) + b"\n"


def _write_receipt(archive_dir: Path, name: str, content: bytes) -> str:
    """Write ``<archive name>.purged.json`` beside the published folder (atomically)."""
    archive_files.replace_document(
        archive_dir / f"{name}{RECEIPT_SUFFIX}", content, archive_dir / PARTIAL_DIR
    )
    return archive_files.sha256_of_bytes(content)


def _copy_receipt(archive_dir: Path, copy_dir: Path, name: str, digest: str) -> str | None:
    """Copy the receipt beside the archive's copy and check it; returns the problem, if any."""
    source = archive_dir / f"{name}{RECEIPT_SUFFIX}"
    target = copy_dir / f"{name}{RECEIPT_SUFFIX}"
    try:
        content = backup_files.read_bytes(source)
        if archive_files.sha256_of_bytes(content) != digest:
            return f"The purge receipt {source} does not match the SHA-256 the run recorded."
        archive_files.replace_document(target, content, copy_dir / PARTIAL_DIR)
        if archive_files.digest(target).sha256 != digest:
            return f"The copy of the purge receipt in {copy_dir} does not match its SHA-256."
    except OSError as exc:
        return f"The purge receipt could not be copied into {copy_dir}: {exc}"
    return None


def _finish_receipt(
    archive_dir: Path, folders: JobFolders, run: RowMapping, content: bytes
) -> dict[str, Any]:
    """Write the receipt and, for a run the job copied off the host, copy it too."""
    name = str(run["archive_name"])
    digest = _write_receipt(archive_dir, name, content)
    copied_to: str | None = None
    problem: str | None = None
    if run["copied_at"] is not None:
        if not _copy_dir_usable(folders):
            problem = (
                f"The copy folder {folders.copy_dir} is not usable; the next recover copies the"
                " purge receipt."
            )
        else:
            assert folders.copy_dir is not None
            problem = _copy_receipt(archive_dir, folders.copy_dir, name, digest)
            if problem is None:
                copied_to = str(folders.copy_dir / f"{name}{RECEIPT_SUFFIX}")
    return {
        "name": f"{name}{RECEIPT_SUFFIX}",
        "sha256": digest,
        "copied_to": copied_to,
        "copy_problem": problem,
    }


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


class PurgeBackup(NamedTuple):
    """The pre-purge backup the host script took (``--backup``, ``--backup-started-at``)."""

    name: str
    started_at: str


class _PurgeRecord(NamedTuple):
    flow_id: int
    part_number: str
    flow_status: str
    movement_count: int
    last_movement_id: int


@dataclass
class PurgeComponent:
    """One whole component a purge deletes (or skips) in one batch."""

    component_id: int
    records: list[_PurgeRecord] = field(default_factory=list)

    @property
    def part_numbers(self) -> frozenset[str]:
        return frozenset(record.part_number for record in self.records)

    @property
    def movements(self) -> int:
        return sum(record.movement_count for record in self.records)


def purge_batches(
    components: Sequence[PurgeComponent], max_movements: int, max_part_numbers: int
) -> list[list[PurgeComponent]]:
    """Whole components per batch, at most ``max_movements`` Movements and
    ``max_part_numbers`` Part Numbers each; a component over a limit on its
    own forms a batch of its own. The order is kept."""
    batches: list[list[PurgeComponent]] = []
    current: list[PurgeComponent] = []
    movements = 0
    part_numbers: frozenset[str] = frozenset()
    for component in components:
        joined = part_numbers | component.part_numbers
        if current and (
            movements + component.movements > max_movements or len(joined) > max_part_numbers
        ):
            batches.append(current)
            current, movements, joined = [], 0, component.part_numbers
        current.append(component)
        movements += component.movements
        part_numbers = joined
    if current:
        batches.append(current)
    return batches


class _PurgeMismatchError(_RunFailed):
    """The deleted count of a batch differs from its records (the batch was rolled back)."""


def _parsed_backup(run: RowMapping, backup: PurgeBackup) -> datetime.datetime:
    """The dump start of a valid pre-purge backup of this run; refuses any other."""
    name = str(run["archive_name"])
    parsed = backups.parse_name(backup.name)
    if parsed is None or parsed.kind != PRE_PURGE_KIND or parsed.label != name:
        raise _Refused(
            "backup_invalid",
            f"The backup {backup.name!r} is not a pre-purge backup of this run's archive: its"
            f" name must be <YYYYMMDDTHHMMSSZ>-{PRE_PURGE_KIND}-{name}. Nothing was changed.",
        )
    started = backups.parse_utc(backup.started_at)
    if started is None:
        raise _Refused(
            "backup_invalid",
            f"The dump start {backup.started_at!r} is not a UTC time YYYY-MM-DDTHH:MM:SSZ."
            " Nothing was changed.",
        )
    if started <= run["verified_at"]:
        raise _Refused(
            "backup_invalid",
            f"The dump of {backup.name} started at {backups.utc_text(started)}, not after the"
            f" verification of run {run['id']} ({iso(run['verified_at'])}): it may not hold the"
            " verified history. Take a new pre-purge backup. Nothing was changed.",
        )
    if started > _now() + _BACKUP_FUTURE_TOLERANCE:
        raise _Refused(
            "backup_invalid",
            f"The dump of {backup.name} started at {backups.utc_text(started)}, in the future"
            " (a clock error). Nothing was changed.",
        )
    return started


def _start_purge(
    engine: Engine, archive_dir: Path, run_id: int, backup: PurgeBackup | None
) -> tuple[RowMapping, bool]:
    """RUNNING → PURGING with the backup evidence, or a PURGING run to resume (True)."""
    with engine.connect() as connection:
        run = _run_row(connection, run_id, lock=False)
    if run is None:
        raise _Refused("unknown_run", f"There is no archival run {run_id}. Nothing was changed.")
    source = archive_dir / str(run["archive_name"])
    if run["state"] == _STATE.PURGING:
        if backup is not None and (backup.name, backups.parse_utc(backup.started_at)) != (
            run["pre_purge_backup_name"],
            run["pre_purge_backup_started_at"],
        ):
            raise _Refused(
                "backup_mismatch",
                f"Run {run_id} is already purging with the backup"
                f" {run['pre_purge_backup_name']}; resume it without a backup. Nothing was"
                " changed.",
            )
        return run, True
    ready = (
        run["state"] == _STATE.RUNNING
        and run["verified_at"] is not None
        and (run["copied_at"] is not None or run["copy_confirmed_at"] is not None)
    )
    if not ready:
        raise _Refused(
            "wrong_state",
            f"Run {run_id} is {run['state']} and not ready to purge (a purge needs a RUNNING run"
            " whose archive was verified and copied off the host). Nothing was changed.",
        )
    if backup is None:
        raise _Refused(
            "backup_required",
            f"Run {run_id} needs its pre-purge backup: take it with backup.sh --kind"
            f" {PRE_PURGE_KIND} --label {run['archive_name']} and pass --backup and"
            " --backup-started-at. Nothing was changed.",
        )
    started = _parsed_backup(run, backup)
    with engine.connect() as connection:
        problem = _requester_problem(connection, run)
    if problem is not None:
        raise _RunFailed(problem)
    # Right before the purge: the published archive is still the verified one.
    _check_published_folder(source, run)
    with engine.connect() as connection, connection.begin():
        current = _run_row(connection, run_id, lock=True)
        if current is None or current["state"] != _STATE.RUNNING:  # pragma: no cover - job lock
            raise _Refused("wrong_state", f"Run {run_id} changed meanwhile. Nothing was changed.")
        after = _change(
            connection,
            current,
            "purge",
            state=_STATE.PURGING.value,
            pre_purge_backup_name=backup.name,
            pre_purge_backup_started_at=started,
            purge_started_at=_now(),
            purged_components=0,
            purged_flows=0,
            purged_movements=0,
        )
    return after, False


def _pending_components(connection: Connection, run_id: int) -> list[PurgeComponent]:
    """The run's components not yet purged or skipped, PN-coherent order."""
    components: dict[int, PurgeComponent] = {}
    for row in connection.execute(
        select(
            _RECORDS.c.component_id,
            _RECORDS.c.flow_id,
            _RECORDS.c.part_number,
            _RECORDS.c.flow_status,
            _RECORDS.c.movement_count,
            _RECORDS.c.last_movement_id,
        )
        .where(
            _RECORDS.c.run_id == run_id,
            _RECORDS.c.purged_at.is_(None),
            _RECORDS.c.skipped_at.is_(None),
        )
        .order_by(_RECORDS.c.component_id, _RECORDS.c.flow_id)
    ):
        components.setdefault(int(row.component_id), PurgeComponent(int(row.component_id)))
        components[int(row.component_id)].records.append(
            _PurgeRecord(
                int(row.flow_id),
                str(row.part_number),
                str(row.flow_status),
                int(row.movement_count),
                int(row.last_movement_id),
            )
        )
    return sorted(
        components.values(),
        key=lambda component: (min(component.part_numbers), component.component_id),
    )


class _BatchOutcome(NamedTuple):
    purged_components: int
    purged_flows: int
    purged_movements: int
    skipped_components: int
    skipped_flows: int


#: Deletes a batch's Movements (every row judged by the row guard).
_DELETE_MOVEMENTS_SQL: Final = (
    "DELETE FROM part_movements WHERE quantity_flow_id = ANY(CAST(:ids AS integer[]))"
)
#: Marks a batch's records skipped or purged ({column}), never twice.
_MARK_RECORDS_SQL: Final = (
    "UPDATE history_archived_flows SET {column} = :moment WHERE run_id = :run_id"
    " AND flow_id = ANY(CAST(:ids AS integer[])) AND purged_at IS NULL AND skipped_at IS NULL"
)


def _purge_batch(engine: Engine, run_id: int, batch: Sequence[PurgeComponent]) -> _BatchOutcome:
    """One batch in ONE READ COMMITTED transaction (design §7.5)."""
    flow_ids = sorted(record.flow_id for component in batch for record in component.records)
    with engine.connect() as base:
        connection = base.execution_options(isolation_level="READ COMMITTED")
        with connection.begin():
            _mark(connection, run_id)
            acquire_part_number_locks(
                connection, {pn for component in batch for pn in component.part_numbers}
            )
            # The state after the locks: a snapshot taken while waiting is no authority.
            statuses = {
                int(row.id): str(row.status)
                for row in connection.execute(
                    text(
                        "SELECT id, status FROM quantity_flows"
                        " WHERE id = ANY(CAST(:ids AS integer[]))"
                    ),
                    {"ids": flow_ids},
                )
            }
            counts = {
                int(row.quantity_flow_id): (int(row.movements), int(row.last_id))
                for row in connection.execute(
                    text(
                        "SELECT quantity_flow_id, count(*) AS movements, max(id) AS last_id"
                        " FROM part_movements WHERE quantity_flow_id = ANY(CAST(:ids AS integer[]))"
                        " GROUP BY quantity_flow_id"
                    ),
                    {"ids": flow_ids},
                )
            }
            passing: list[PurgeComponent] = []
            failing: list[PurgeComponent] = []
            for component in batch:
                unchanged = all(
                    statuses.get(record.flow_id) == record.flow_status
                    and counts.get(record.flow_id)
                    == (record.movement_count, record.last_movement_id)
                    for record in component.records
                )
                (passing if unchanged else failing).append(component)
            moment = _now()
            failing_flows = [record.flow_id for c in failing for record in c.records]
            # Every flow id list goes as ONE array parameter: a batch may hold
            # more flows than a statement takes parameters (65,535).
            if failing_flows:
                marked = connection.execute(
                    text(_MARK_RECORDS_SQL.format(column="skipped_at")),
                    {"moment": moment, "run_id": run_id, "ids": failing_flows},
                ).rowcount
                if marked != len(failing_flows):
                    raise _PurgeMismatchError(
                        f"The purge marked {marked} records skipped, {len(failing_flows)} expected."
                        " The batch was rolled back."
                    )
            passing_flows = [record.flow_id for c in passing for record in c.records]
            archived = sum(component.movements for component in passing)
            if passing_flows:
                deleted = connection.execute(
                    text(_DELETE_MOVEMENTS_SQL), {"ids": passing_flows}
                ).rowcount
                if deleted != archived:
                    raise _PurgeMismatchError(
                        f"A purge batch deleted {deleted} Movements of components"
                        f" {', '.join(str(c.component_id) for c in passing[:5])}"
                        f"{' …' if len(passing) > 5 else ''}, but their records hold {archived}."
                        " The batch was rolled back; nothing of it was deleted."
                    )
                marked = connection.execute(
                    text(_MARK_RECORDS_SQL.format(column="purged_at")),
                    {"moment": moment, "run_id": run_id, "ids": passing_flows},
                ).rowcount
                if marked != len(passing_flows):
                    raise _PurgeMismatchError(
                        f"The purge marked {marked} records purged, {len(passing_flows)} expected."
                        " The batch was rolled back."
                    )
            outcome = _BatchOutcome(
                len(passing), len(passing_flows), archived, len(failing), len(failing_flows)
            )
            connection.execute(
                update(_RUNS)
                .where(_RUNS.c.id == run_id, _RUNS.c.state == _STATE.PURGING.value)
                .values(
                    purged_components=_RUNS.c.purged_components + outcome.purged_components,
                    purged_flows=_RUNS.c.purged_flows + outcome.purged_flows,
                    purged_movements=_RUNS.c.purged_movements + outcome.purged_movements,
                    skipped_components=_RUNS.c.skipped_components + outcome.skipped_components,
                    skipped_flows=_RUNS.c.skipped_flows + outcome.skipped_flows,
                )
            )
    return outcome


def _finish_purge(
    engine: Engine, archive_dir: Path, folders: JobFolders, run_id: int
) -> tuple[RowMapping, dict[str, Any]]:
    """PURGING → COMPLETED / COMPLETED_WITH_SKIPS with the receipt (written first)."""
    with engine.connect() as connection, connection.begin():
        current = _run_row(connection, run_id, lock=True)
        if current is None or current["state"] != _STATE.PURGING:  # pragma: no cover - job lock
            raise _Refused("wrong_state", f"Run {run_id} changed meanwhile. Nothing was changed.")
        pending = _pending_components(connection, run_id)
        if pending:  # pragma: no cover - the batches reached every component
            raise _RunFailed(
                f"{len(pending)} components of run {run_id} were neither purged nor skipped."
                " This is a defect to report."
            )
        skipped = int(current["skipped_components"] or 0)
        state = (_STATE.COMPLETED_WITH_SKIPS if skipped else _STATE.COMPLETED).value
        result = (
            HistoryArchiveRunResult.PURGED
            if int(current["purged_components"] or 0) > 0
            else HistoryArchiveRunResult.NOTHING_PURGED
        ).value
        content = _receipt_content(connection, current, state=state, result=result, failure=None)
        receipt = _finish_receipt(archive_dir, folders, current, content)
        after = _change(
            connection,
            current,
            "purge",
            state=state,
            result=result,
            receipt_sha256=receipt["sha256"],
            finished_at=_now(),
        )
    return after, receipt


def _fail_purging(folders: JobFolders) -> FailRun:
    """A purge's ``fail_run``: a PURGING run also gets the receipt of what was deleted."""

    def fail(engine: Engine, run_id: int, reason: str, command: str) -> str | None:
        with engine.connect() as connection, connection.begin():
            run = _run_row(connection, run_id, lock=True)
            if run is None:
                return None
            if history_archive.is_final(str(run["state"])):
                return str(run["state"])
            digest: str | None = None
            text_reason = reason
            if run["state"] == _STATE.PURGING and folders.archive_dir is not None:
                try:
                    content = _receipt_content(
                        connection, run, state=_STATE.FAILED.value, result=None, failure=reason
                    )
                    digest = _finish_receipt(folders.archive_dir, folders, run, content)["sha256"]
                except (OSError, _RunFailed) as exc:
                    text_reason = f"{reason} The purge receipt could not be written ({exc})."
            after = _change(
                connection,
                run,
                command,
                state=_STATE.FAILED.value,
                failure_reason=text_reason[:2000],
                finished_at=_now(),
                receipt_sha256=digest,
            )
            return str(after["state"])

    return fail


def _report_totals(report: JobReport, run: RowMapping) -> None:
    """``purged`` and ``skipped``: the run's totals as stored."""
    report.details["purged"] = {
        "components": int(run["purged_components"] or 0),
        "flows": int(run["purged_flows"] or 0),
        "movements": int(run["purged_movements"] or 0),
    }
    report.details["skipped"] = {
        "components": int(run["skipped_components"] or 0),
        "flows": int(run["skipped_flows"] or 0),
    }


def _add_outcome(report: JobReport, outcome: _BatchOutcome) -> None:
    """Add a committed batch to the run's totals in the report."""
    purged, skipped = report.details["purged"], report.details["skipped"]
    purged["components"] += outcome.purged_components
    purged["flows"] += outcome.purged_flows
    purged["movements"] += outcome.purged_movements
    skipped["components"] += outcome.skipped_components
    skipped["flows"] += outcome.skipped_flows


def purge(
    engine: Engine | None, folders: JobFolders, run_id: int, backup: PurgeBackup | None = None
) -> JobReport:
    """Purge the archived Movements of a copied run, or resume a PURGING run (design §7.5)."""

    def work(engine: Engine, report: JobReport) -> None:
        archive_dir = _require_archive_dir(folders)
        with _job_lock(engine):
            run, resumed = _start_purge(engine, archive_dir, run_id, backup)
            report.run_started = True
            report.state = str(run["state"])
            name = str(run["archive_name"])
            report.details["archive_name"] = name
            report.details["resumed"] = resumed
            report.details["backup"] = {
                "name": run["pre_purge_backup_name"],
                "dump_started_at": iso(run["pre_purge_backup_started_at"]),
            }
            # The run's totals, kept current batch by batch: a report that ends
            # early (a failed or interrupted purge) says what was deleted.
            _report_totals(report, run)
            if resumed:
                # A resumed purge too: the published archive is still the
                # verified one before anything more is deleted (a first start
                # checked it right before it set PURGING).
                _check_published_folder(archive_dir / name, run)
            with engine.connect() as connection:
                stored = history_archive.read_settings(connection)
                pending = _pending_components(connection, run_id)
            batches = purge_batches(
                pending, stored.purge_batch_movements, stored.purge_batch_part_numbers
            )
            for batch in batches:
                _add_outcome(report, _purge_batch(engine, run_id, batch))
            after, receipt = _finish_purge(engine, archive_dir, folders, run_id)
            report.state = str(after["state"])
            _report_totals(report, after)
            report.details.update({"batches": len(batches), "receipt": receipt})
            report.result = (
                "completed" if after["state"] == _STATE.COMPLETED else "completed_with_skips"
            )
            report.exit_code = EXIT_DONE

    return _command("purge", engine, work, run_id=run_id, fail_run=_fail_purging(folders))


def environment_folders() -> JobFolders:
    """The folders of the maintenance service, from its environment."""
    return folders_from(os.environ)
