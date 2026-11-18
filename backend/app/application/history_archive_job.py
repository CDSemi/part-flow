"""The host job of Movement-history archival (Phase 16, history archival design §5, §7;
build step 1: runs without deletion).

``python -m app.cli history-archive <command>`` runs these steps for the host
script ``deploy/production/archive.sh``, as the maintenance database role
(``partflow_maintenance``), in the ``maintenance`` service where the archive
folder (``PARTFLOW_ARCHIVE_DIR``) and the optional off-host copy folder
(``PARTFLOW_ARCHIVE_COPY_DIR``) are mounted. Nothing here deletes database
rows: in step 1 a run ends after the off-host copy as ``COMPLETED`` with the
result ``EXPORT_ONLY``.

Commands (each prints one JSON report; ``JobReport``):

- ``settings`` — what the host script needs (the Start minute of the hourly
  work), read-only;
- ``recover`` — called right after the host script took its lock: a run
  found ``RUNNING`` is stale (no other command holds the job's advisory
  lock) and becomes ``FAILED`` with that reason; unfinished ``.partial``
  folders of finished runs are removed (in step 1 no run deleted anything);
- ``next`` — the run the job must work on: the open ``APPROVED`` run
  (``export``), or a ``WAITING_FOR_COPY`` run that is confirmed or can be
  copied now (``copy``);
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
  is refused);
- ``copy --run N`` — with a copy folder: copies the archive there, re-reads
  and checks every file, publishes the copy and finishes the run; without
  one (or while it is unavailable): ``WAITING_FOR_COPY`` until a person
  confirms the copy in Administration, then finishes the run;
- ``status`` — the open run, the last finished run and the archive folder's
  space, for ``check.sh``; a run waiting for its copy confirmation longer
  than the waiting limit of the settings is a finding.

Exit codes: 0 the step is done (``result`` says what happened); 1 refused,
nothing changed (an unknown run, a run in another state, another
history-archive command running); 2 could not run (configuration, database
unreachable, an internal error before the run changed) or its outcome is
unknown; 3 the run failed — it is now ``FAILED`` with its reason.

Every command that works on a run sets the transaction-local setting
``partflow.archive_run_id`` to it: the run and record guard triggers admit
the job's changes only then (and only from a role that may write archive
records). Every change of state appends one ``HistoryArchiveRun`` audit row
(no actor; ``metadata.source`` ``maintenance``). Audit rows are inserted
without ``RETURNING``: the maintenance role may append them, never read
them.

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

Memory grows with the number of flows, not with the size of their history:
the Movement, lineage, flow and route rows are streamed, a flow keeps only
its content hash and the few Movement fields its record reads until the
flows file is written, the records reach the database in batches, and every
data file is written in ascending id order, so its id-set hash is computed
as the ids come.
"""

import contextlib
import datetime
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NamedTuple, cast

from sqlalchemy import Connection, Engine, RowMapping, Table, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from app.application import history_archive
from app.application.migration import RunError, is_connection_failure
from app.core.config import get_settings
from app.domain.enums import (
    AuditEventType,
    HistoryArchiveRunResult,
    HistoryArchiveRunState,
    MovementType,
)
from app.infrastructure import archive_files, backup_files
from app.infrastructure.models import HistoryArchivedFlow, HistoryArchiveRun

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
            connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": JOB_LOCK}
            )
    finally:
        connection.close()


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


def _change(connection: Connection, run: RowMapping, command: str, **values: object) -> RowMapping:
    """Update the run (marked as the job's); a change of state appends its audit row."""
    _mark(connection, int(run["id"]))
    after = (
        connection.execute(
            update(_RUNS).where(_RUNS.c.id == run["id"]).values(**values).returning(*_RUNS.c)
        )
        .mappings()
        .one()
    )
    if after["state"] != run["state"]:
        _audit(connection, run, after, command)
    return after


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
    return {
        "id": int(run["id"]),
        "state": str(run["state"]),
        "cutoff": iso(run["cutoff"]),
        "requested_at": iso(run["requested_at"]),
        "archive_name": run["archive_name"],
        "manifest_sha256": run["manifest_sha256"],
        "copy_confirmed": run["copy_confirmed_at"] is not None,
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
) -> JobReport:
    """Run one command; a refusal, a failed run and any error become a complete report."""
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
        return _record_failure(engine, report, failure.reason, None)
    except Exception as exc:
        if report.run_started:
            return _record_failure(
                engine,
                report,
                f"The host job stopped on an internal error ({type(exc).__name__}). Nothing was"
                " deleted. See the job log.",
                exc,
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
    engine: Engine, report: JobReport, reason: str, exc: BaseException | None
) -> JobReport:
    if report.run_id is None:  # pragma: no cover - every failing command names its run
        return _fail(report, "internal_error", reason, EXIT_COULD_NOT_RUN, exc)
    try:
        report.state = _fail_run(engine, report.run_id, reason, report.command)
    except Exception as marking:
        # The run stays as it is; the next recover makes a stale RUNNING run FAILED.
        return _fail(report, "outcome_unknown", reason, EXIT_COULD_NOT_RUN, exc or marking)
    return _fail(report, "run_failed", reason, EXIT_RUN_FAILED, exc)


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


_OPEN_STATES: Final = (_STATE.APPROVED, _STATE.RUNNING, _STATE.WAITING_FOR_COPY)


def next_run(engine: Engine | None, folders: JobFolders) -> JobReport:
    """The run the job must work on, and what to run for it."""

    def work(engine: Engine, report: JobReport) -> None:
        with engine.connect() as connection:
            run = _open_run(connection)
        action = "none"
        if run is not None:
            report.run_id, report.state = int(run["id"]), str(run["state"])
            if run["state"] == _STATE.APPROVED:
                action = "export"
            elif run["state"] == _STATE.RUNNING:
                action = "running"
            elif run["copy_confirmed_at"] is not None or folders.copy_dir is not None:
                action = "copy"
            else:
                action = "wait"
        report.details["run"] = _run_summary(run)
        report.result, report.exit_code = action, EXIT_DONE

    return _command("next", engine, work)


def status(engine: Engine | None, folders: JobFolders) -> JobReport:
    """The open run and its age, the last finished run and the archive folder's space.

    A run waiting for its copy confirmation longer than the waiting limit
    of the settings (``archive_max_wait_days``) is a finding.
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
    """Fail stale RUNNING runs; remove unfinished folders of finished runs."""

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
                            " start of the job). Nothing was deleted; request a new run."
                        ),
                        finished_at=_now(),
                    )
                    failed.append(int(run["id"]))
                states = {
                    int(row.id): str(row.state)
                    for row in connection.execute(select(_RUNS.c.id, _RUNS.c.state))
                }
            removed: list[str] = []
            kept: list[str] = []
            for root in (archive_dir, folders.copy_dir):
                if root is None or not backup_files.is_real_directory(root / PARTIAL_DIR):
                    continue
                for name in backup_files.entry_names(root / PARTIAL_DIR):
                    match = NAME_PATTERN.fullmatch(name)
                    run_state = None if match is None else states.get(int(match.group("run")))
                    if run_state is not None and history_archive.is_final(run_state):
                        backup_files.remove_tree(root / PARTIAL_DIR / name)
                        removed.append(str(root / PARTIAL_DIR / name))
                    else:
                        kept.append(str(root / PARTIAL_DIR / name))
        report.details.update(
            {"failed_run_ids": failed, "removed_partial": removed, "kept_partial": kept}
        )
        report.result = "recovered" if failed or removed else "nothing_to_recover"
        report.exit_code = EXIT_DONE

    return _command("recover", engine, work)


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


def movement_lines(
    connection: Connection, flow_ids: Sequence[int]
) -> Iterator[tuple[int, int, bytes, dict[str, Any]]]:
    """(id, flow, canonical line, value) of every Movement of the flows, by id (streamed)."""
    result = connection.execute(
        text(
            "SELECT m.id, m.quantity_flow_id, to_jsonb(m) AS row FROM part_movements m"
            " JOIN unnest(CAST(:ids AS integer[])) AS e(flow_id)"
            " ON e.flow_id = m.quantity_flow_id ORDER BY m.id"
        ),
        {"ids": list(flow_ids)},
        execution_options=_STREAM,
    )
    for row in result:
        value: dict[str, Any] = row.row
        line = archive_files.canonical_json(value) + b"\n"
        yield int(row.id), int(row.quantity_flow_id), line, value


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
    areas: set[int] = field(default_factory=set)
    operations: set[int] = field(default_factory=set)
    machines: set[int] = field(default_factory=set)
    workers: set[int] = field(default_factory=set)
    stations: set[str] = field(default_factory=set)
    demands: set[int] = field(default_factory=set)
    users: set[int] = field(default_factory=set)
    part_numbers: set[str] = field(default_factory=set)

    def add_int(self, target: set[int], value: object) -> None:
        if isinstance(value, int) and not isinstance(value, bool):
            target.add(value)


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


def _reversed_commands(connection: Connection, flow_ids: Sequence[int]) -> set[str]:
    """The command ids of the archived flows' undone commands."""
    return {
        str(value)
        for value in connection.execute(
            text(
                "SELECT DISTINCT o.device_event_id FROM part_movements r"
                " JOIN unnest(CAST(:ids AS integer[])) AS e(flow_id)"
                " ON e.flow_id = r.quantity_flow_id"
                " JOIN part_movements o ON o.id = r.reverses_movement_id"
            ),
            {"ids": list(flow_ids)},
        ).scalars()
    }


@dataclass
class _Written:
    counts: dict[str, int]
    files: dict[str, dict[str, Any]]
    manifest_sha256: str
    archive_bytes: int


#: Records inserted per statement while the flows file is written.
_RECORD_BATCH: Final = 1000


def _write_movements(
    connection: Connection,
    folder: Path,
    flow_ids: Sequence[int],
    context: _Context,
) -> tuple[archive_files.JsonLinesWriter, dict[int, str], dict[int, list[_Event]], set[int]]:
    """The Movements file; per flow its content hash and the few fields its record reads."""
    writer = archive_files.JsonLinesWriter(folder / MOVEMENTS_FILE)
    hashers: dict[int, Any] = defaultdict(hashlib.sha256)
    events: dict[int, list[_Event]] = defaultdict(list)
    reversed_ids: set[int] = set()
    try:
        for movement_id, flow_id, line, value in movement_lines(connection, flow_ids):
            writer.write(movement_id, value)
            hashers[flow_id].update(line)
            kind = value.get("movement_type")
            if kind == MovementType.REVERSED and value.get("reverses_movement_id") is not None:
                reversed_ids.add(int(value["reverses_movement_id"]))
            elif kind in _RECORD_KINDS:
                events[flow_id].append(_event(movement_id, value))
            for key in ("from_area_id", "to_area_id"):
                context.add_int(context.areas, value.get(key))
            context.add_int(context.operations, value.get("operation_id"))
            for key in ("source_machine_id", "destination_machine_id"):
                context.add_int(context.machines, value.get(key))
            context.add_int(context.workers, value.get("worker_id"))
            if isinstance(value.get("station_id"), str):
                context.stations.add(value["station_id"])
            movement_context = (value.get("metadata") or {}).get("context")
            if isinstance(movement_context, dict):
                context.add_int(context.users, movement_context.get("actor_user_id"))
    finally:
        writer.close()
    return (
        writer,
        {flow: hasher.hexdigest() for flow, hasher in hashers.items()},
        events,
        reversed_ids,
    )


def _context_document(
    connection: Connection, context: _Context, requester_id: int
) -> dict[str, list[dict[str, Any]]]:
    """The master data the archived rows name, so the archive reads without PartFlow."""
    tables = {name: (table, key, columns) for name, table, key, columns in _CONTEXT_TABLES}
    demands = _rows(connection, *tables["work_order_demands"], sorted(context.demands))
    wanted: dict[str, Sequence[object]] = {
        "areas": sorted(context.areas),
        "operations": sorted(context.operations),
        "machines": sorted(context.machines),
        "workers": sorted(context.workers),
        "scan_stations": sorted(context.stations),
        "work_orders": sorted({int(row["work_order_id"]) for row in demands}),
        "users": sorted(context.users | {requester_id}),
        "part_numbers": sorted(context.part_numbers),
    }
    document = {name: _rows(connection, *tables[name], ids) for name, ids in wanted.items()}
    document["work_order_demands"] = demands
    return document


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
    connection: Connection, table: str, key: str, columns: str, ids: Sequence[object]
) -> list[dict[str, Any]]:
    """The named rows of one context table as ``to_jsonb``, in key order."""
    if not ids:
        return []
    array = "text[]" if key in ("station_id", "part_number") else "integer[]"
    return [
        dict(row.value)
        for row in connection.execute(
            text(
                f"SELECT to_jsonb(t) AS value FROM (SELECT {columns} FROM {table}"
                f" WHERE {key} = ANY(CAST(:ids AS {array}))) t ORDER BY t.{key}"
            ),
            {"ids": list(ids)},
        )
    ]


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
    flows: Sequence[FlowFacts],
) -> _Written:
    run_id = int(run["id"])
    flow_ids = [facts.flow_id for facts in flows]
    context = _Context()
    movements, hashes, events, reversed_ids = _write_movements(
        connection, folder, flow_ids, context
    )
    void_commands = _reversed_commands(connection, flow_ids)

    lineage = archive_files.JsonLinesWriter(folder / LINEAGE_FILE)
    void_by_flow: dict[int, list[str]] = defaultdict(list)
    try:
        for row in connection.execute(
            text(
                "SELECT id, to_jsonb(l) AS row FROM quantity_flow_lineage l"
                " WHERE l.parent_flow_id = ANY(CAST(:ids AS integer[])) ORDER BY id"
            ),
            {"ids": flow_ids},
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

    # Each record goes to the flows file and, in batches, to the database
    # (rolled back with the export's transaction if a later check fails);
    # what a flow kept for its record is released once it is written.
    flows_writer = archive_files.JsonLinesWriter(folder / FLOWS_FILE)
    batch: list[dict[str, Any]] = []
    route_ids: set[int] = set()
    by_id = {facts.flow_id: facts for facts in flows}
    try:
        for row in connection.execute(
            text(
                "SELECT f.id, f.assigned_route_id, f.current_area_id, f.current_machine_id,"
                " to_jsonb(f) AS row FROM quantity_flows f"
                " WHERE f.id = ANY(CAST(:ids AS integer[])) ORDER BY f.id"
            ),
            {"ids": flow_ids},
            execution_options=_STREAM,
        ):
            facts = by_id[int(row.id)]
            record = _record(
                run_id,
                facts,
                events.pop(facts.flow_id, ()),
                reversed_ids,
                hashes.pop(facts.flow_id),
                void_by_flow.pop(facts.flow_id, ()),
            )
            flows_writer.write(
                facts.flow_id,
                {
                    "component_id": facts.component_id,
                    "flow": row.row,
                    "record": _record_json(record),
                },
            )
            batch.append(record)
            if len(batch) >= _RECORD_BATCH:
                connection.execute(insert(_RECORDS), batch)
                batch = []
            context.part_numbers.add(facts.part_number)
            context.add_int(context.areas, row.current_area_id)
            context.add_int(context.machines, row.current_machine_id)
            for key in ("stocked_area_id", "scrapped_area_id"):
                context.add_int(context.areas, record[key])
            context.add_int(context.demands, record["received_demand_id"])
            if row.assigned_route_id is not None:
                route_ids.add(int(row.assigned_route_id))
        if batch:
            connection.execute(insert(_RECORDS), batch)
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
                " WHERE r.id = ANY(CAST(:ids AS integer[])) ORDER BY r.id"
            ),
            {"ids": sorted(route_ids)},
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

    archive_files.write_document(
        folder / CONTEXT_FILE,
        archive_files.readable_json(
            _context_document(connection, context, int(run["requested_by_user_id"]))
        ),
    )
    archive_files.write_document(folder / README_FILE, _README.encode("utf-8"))

    counts = {
        "components": len({facts.component_id for facts in flows}),
        "flows": len(flows),
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
    manifest = _manifest(connection, run, name, flows, counts, files)
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
    flows: Sequence[FlowFacts],
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
        "first_occurred_at": iso(min(facts.first_occurred_at for facts in flows)),
        "last_occurred_at": iso(max(facts.last_occurred_at for facts in flows)),
        "files": {file: dict(entry) for file, entry in sorted(files.items())},
    }


def _command_spans(connection: Connection, flows: Sequence[FlowFacts]) -> list[str]:
    """Commands with a Movement outside the archived components, or in two of them."""
    return [
        str(value)
        for value in connection.execute(
            text(
                "WITH archived AS (SELECT * FROM unnest(CAST(:ids AS integer[]),"
                " CAST(:components AS integer[])) AS a(flow_id, component_id)),"
                " commands AS (SELECT DISTINCT m.device_event_id FROM part_movements m"
                " JOIN archived a ON a.flow_id = m.quantity_flow_id)"
                " SELECT c.device_event_id FROM commands c"
                " JOIN part_movements o ON o.device_event_id = c.device_event_id"
                " LEFT JOIN archived a ON a.flow_id = o.quantity_flow_id"
                " GROUP BY c.device_event_id"
                " HAVING count(DISTINCT a.component_id) > 1 OR bool_or(a.flow_id IS NULL)"
                " ORDER BY c.device_event_id LIMIT 5"
            ),
            {
                "ids": [facts.flow_id for facts in flows],
                "components": [facts.component_id for facts in flows],
            },
        ).scalars()
    ]


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
            flows = eligible_flows(connection, run["cutoff"])
            if not flows:
                after = _change(
                    connection,
                    run,
                    "export",
                    state=_STATE.NOTHING_ELIGIBLE.value,
                    finished_at=_now(),
                )
            else:
                written, after = _export_snapshot(connection, folder, run, name, flows)
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
    flows: Sequence[FlowFacts],
) -> tuple[_Written, RowMapping]:
    """The files and the records of the eligible flows, inside the export's snapshot."""
    _check_against_preview(run, flows)
    if backup_files.exists(folder):
        raise _RunFailed(f"The unfinished folder {PARTIAL_DIR}/{name} already exists.")
    backup_files.make_private_directory(folder)
    try:
        written = _write_archive(connection, folder, run, name, flows)
        spans = _command_spans(connection, flows)
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


def _check_against_preview(run: RowMapping, flows: Sequence[FlowFacts]) -> None:
    found = {
        "components": len({facts.component_id for facts in flows}),
        "flows": len(flows),
        "movements": sum(facts.movements for facts in flows),
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
    hashers: dict[int, Any] = defaultdict(hashlib.sha256)
    for file in DATA_FILES:
        ids = archive_files.IdSet()
        rows = 0
        try:
            for line, value in archive_files.read_json_lines(folder / file):
                rows += 1
                if file == MOVEMENTS_FILE:
                    ids.add(int(value["id"]))
                    hashers[int(value["quantity_flow_id"])].update(line)
                elif file == FLOWS_FILE:
                    flow_id = int(value["flow"]["id"])
                    ids.add(flow_id)
                    content[flow_id] = value["record"].get("content_sha256")
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
    hashes = {flow: hasher.hexdigest() for flow, hasher in hashers.items()}
    for flow_id, recorded in content.items():
        if recorded != hashes.get(flow_id):
            raise _RunFailed(
                f"The Movements of Quantity Flow {flow_id} in {MOVEMENTS_FILE} do not match its"
                " record. Nothing was published."
            )
    if set(hashes) - set(content):
        raise _RunFailed(
            f"{MOVEMENTS_FILE} holds Movements of flows without a record. Nothing was published."
        )
    return _FileFacts(content)


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
        report.state = str(after["state"])
        report.details.update(
            {
                "archive_name": name,
                "manifest_sha256": run["manifest_sha256"],
                "skipped_components": skipped[0],
                "skipped_flows": skipped[1],
            }
        )
        report.result, report.exit_code = "verified", EXIT_DONE

    return _command("verify", engine, work, run_id=run_id)


def _verify_in_database(
    engine: Engine,
    run: RowMapping,
    facts: _FileFacts,
    folder: Path,
    target: Path,
) -> tuple[RowMapping, tuple[int, int]]:
    run_id = int(run["id"])
    with engine.connect() as base:
        connection = base.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            _utc(connection)
            _mark(connection, run_id)
            stored = {
                int(row.flow_id): row
                for row in connection.execute(
                    select(
                        _RECORDS.c.flow_id,
                        _RECORDS.c.component_id,
                        _RECORDS.c.content_sha256,
                        _RECORDS.c.flow_status,
                    ).where(_RECORDS.c.run_id == run_id)
                ).mappings()
            }
            if set(stored) != set(facts.content):
                raise _RunFailed(
                    "The archive's flows are not the flows the export recorded. Nothing was"
                    " published."
                )
            for flow_id, row in stored.items():
                if row["content_sha256"] != facts.content[flow_id]:
                    raise _RunFailed(
                        f"The record of Quantity Flow {flow_id} differs from the archive."
                        " Nothing was published."
                    )
            live: dict[int, Any] = defaultdict(hashlib.sha256)
            for _movement_id, flow_id, line, _value in movement_lines(connection, sorted(stored)):
                live[flow_id].update(line)
            statuses = {
                int(row.id): str(row.status)
                for row in connection.execute(
                    text(
                        "SELECT id, status FROM quantity_flows"
                        " WHERE id = ANY(CAST(:ids AS integer[]))"
                    ),
                    {"ids": sorted(stored)},
                )
            }
            changed = sorted(
                {
                    int(row["component_id"])
                    for flow_id, row in stored.items()
                    if flow_id not in live
                    or live[flow_id].hexdigest() != row["content_sha256"]
                    or statuses.get(flow_id) != row["flow_status"]
                }
            )
            skipped_flows = 0
            if changed:
                skipped_flows = int(
                    connection.execute(
                        update(_RECORDS)
                        .where(_RECORDS.c.run_id == run_id, _RECORDS.c.component_id.in_(changed))
                        .values(skipped_at=_now())
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
            " Nothing was deleted; request a new run."
        ) from exc


def copy(engine: Engine | None, folders: JobFolders, run_id: int) -> JobReport:
    """Copy the verified archive off the host and finish the run, or wait for a person."""

    def work(engine: Engine, report: JobReport) -> None:
        archive_dir = _require_archive_dir(folders)
        with _job_lock(engine):
            with engine.connect() as connection:
                run = _run_row(connection, run_id, lock=False)
            if run is None:
                raise _Refused(
                    "unknown_run", f"There is no archival run {run_id}. Nothing was changed."
                )
            verified = run["state"] == _STATE.RUNNING and run["verified_at"] is not None
            if not verified and run["state"] != _STATE.WAITING_FOR_COPY:
                raise _Refused(
                    "wrong_state",
                    f"Run {run_id} is {run['state']} and not waiting for its off-host copy."
                    " Nothing was changed.",
                )
            report.run_started = True
            name = str(run["archive_name"])
            source = archive_dir / name
            if not backup_files.is_real_directory(source):
                raise _RunFailed(
                    f"The published archive folder {name} is missing. Nothing was deleted;"
                    " request a new run."
                )
            _check_published(source, run)
            report.details["archive_name"] = name
            copied_to: Path | None = None
            problem: str | None = None
            if folders.copy_dir is not None:
                if not archive_files.is_writable_directory(folders.copy_dir):
                    problem = (
                        f"The copy folder {folders.copy_dir} does not exist or cannot be written."
                    )
                else:
                    try:
                        copied_to = _copy_archive(source, folders.copy_dir, name)
                    except (OSError, archive_files.SumsError, _CopyMismatchError) as exc:
                        problem = f"The copy into {folders.copy_dir} failed: {exc}"
            report.details["copied_to"] = None if copied_to is None else str(copied_to)
            report.details["copy_problem"] = problem
            after = _finish_or_wait(engine, run, copied=copied_to is not None)
        report.state = str(after["state"])
        if after["state"] == _STATE.COMPLETED:
            report.result = "completed"
        else:
            report.result = "waiting_for_copy"
        report.exit_code = EXIT_DONE

    return _command("copy", engine, work, run_id=run_id)


def _finish_or_wait(engine: Engine, run: RowMapping, *, copied: bool) -> RowMapping:
    with engine.connect() as connection, connection.begin():
        current = _run_row(connection, int(run["id"]), lock=True)
        if current is None:  # pragma: no cover - runs are never deleted
            raise _Refused("unknown_run", "The run disappeared. Nothing was changed.")
        if current["state"] not in (_STATE.RUNNING, _STATE.WAITING_FOR_COPY):
            raise _Refused(
                "wrong_state",
                f"Run {current['id']} became {current['state']} meanwhile. Nothing was changed.",
            )
        if copied or current["copy_confirmed_at"] is not None:
            values: dict[str, Any] = {
                "state": _STATE.COMPLETED.value,
                "result": HistoryArchiveRunResult.EXPORT_ONLY.value,
                "finished_at": _now(),
            }
            if copied:
                values["copied_at"] = _now()
            return _change(connection, current, "copy", **values)
        if current["state"] == _STATE.RUNNING:
            return _change(connection, current, "copy", state=_STATE.WAITING_FOR_COPY.value)
        return current


def environment_folders() -> JobFolders:
    """The folders of the maintenance service, from its environment."""
    return folders_from(os.environ)
