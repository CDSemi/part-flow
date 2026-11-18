"""Movement-history archival runs (Phase 16, history archival design B; build step 1).

The application side of Administration → History archival & purge, and the
one selection that both its preview and the host job's export run
(``app.application.history_archive_job``). It is the only module that reads
the retention period (PROJECT_PROFILE §28: retention settings live in
Administration, never in production workflow logic). Nothing here deletes
anything; build step 1 archives without deletion.

Terms (design §2):

- a **flow component** is a set of Quantity Flows connected by lineage edges
  (split and merge, undone ones included): the connected components of the
  edges, every flow without an edge alone. Its id is its smallest flow id.
  Only splits and merges write Movements on several flows in one command, and
  an Undo writes each ``REVERSED`` row on the flow of its original, so a
  component holds whole commands and whole correction chains;
- a flow is **closed** when its status is not ``ACTIVE``;
- the **cutoff** of a run is the start of a day in the site time zone
  (``SITE_TIMEZONE``): by default the day the retention period before
  today; a run may choose an older day, never a newer one.

A component is **eligible** at a cutoff when every flow in it is closed,
every flow has at least one Movement, and every Movement of every flow has
both ``occurred_at`` and ``server_received_at`` before the cutoff (design
§3.1). Only whole components are archived. For one cutoff the eligible set
can only shrink over time: a flow closes only with a Movement, and an Undo
that reopens a flow writes Movements.

The preview runs the selection read-only within the preview time limit of
the settings and reports the components, flows and Movements, their date
range and an estimated archive size. A request recomputes the preview and
stores it on the run, which starts ``APPROVED`` (the request is the
approval); at most one run is open. A run may be cancelled while
``APPROVED`` or ``WAITING_FOR_COPY``; the off-host copy of a
``WAITING_FOR_COPY`` run is confirmed once, against the manifest SHA-256 the
person was shown. Every state change and the copy confirmation append one
``HistoryArchiveRun`` audit row (``entity_id`` the run id) in the same
transaction.
"""

import calendar
import datetime
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NamedTuple, NoReturn
from zoneinfo import ZoneInfo

import psycopg.errors
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.application import audit, user_access
from app.application.common import commit, flush, is_bindable_id
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.policies import maintenance_reason
from app.core.config import get_settings
from app.domain.enums import AuditEntityType, AuditEventType, HistoryArchiveRunState
from app.infrastructure.models import (
    HISTORY_ARCHIVE_FINAL_STATES,
    HISTORY_ARCHIVE_OPEN_STATES,
    ApplicationPolicy,
    HistoryArchiveRun,
)

#: The one group of build step 1 (the optional groups are a later step).
GROUP_MOVEMENT_HISTORY: Final = "MOVEMENT_HISTORY"
#: No cutoff before this day: PartFlow holds no history that old.
EARLIEST_CUTOFF_DATE: Final = datetime.date(2000, 1, 1)
#: The estimate of the compressed archive per byte of the archived rows in
#: the database (measured on realistic rows: JSON Lines, gzip level 6).
ARCHIVE_BYTES_PER_ROW_BYTE: Final = 0.35
#: Fixed archive files (manifest, context, README, checksums), estimated.
ARCHIVE_FIXED_BYTES: Final = 8_192
DEFAULT_RUN_LIMIT: Final = 50

_OPEN_RUN_INDEX: Final = "uq_history_archive_runs_open"
_REQUEST_REASON_MESSAGE: Final = "Enter the reason for this run: 1 to 500 characters of plain text."
_NO_RETENTION_MESSAGE: Final = (
    "No retention period is set, so no history is eligible for archival. Set a retention"
    " period first."
)
_OPEN_RUN_MESSAGE: Final = (
    "Another archival run is still open. Cancel it or wait until it finishes before requesting"
    " a new run."
)
#: The "constraint" the run guard trigger names when it refuses a cutoff
#: newer than the shortest allowed period (SQLSTATE 23514).
_CUTOFF_FLOOR_GUARD: Final = "trg_history_archive_runs_guard_mutation"
_CUTOFF_FLOOR_MESSAGE: Final = (
    "The database refused the cutoff as newer than the shortest allowed period allows now, so"
    " no run was requested. Reload the settings and choose an older day."
)
_SQLSTATE_QUERY_CANCELED: Final = "57014"


class ArchivalSettings(NamedTuple):
    """The settings of Administration → History archival & purge, as stored."""

    retention_period_months: int | None
    shortest_retention_months: int
    max_wait_days: int
    start_minute: int
    preview_seconds: int


def read_settings(session: Session | Connection) -> ArchivalSettings:
    """The stored settings (unlocked; a column select, no policy instance)."""
    row = session.execute(
        select(
            ApplicationPolicy.retention_period_months,
            ApplicationPolicy.shortest_retention_months,
            ApplicationPolicy.archive_max_wait_days,
            ApplicationPolicy.archive_start_minute,
            ApplicationPolicy.archive_preview_seconds,
        ).where(ApplicationPolicy.id == 1)
    ).one()
    return ArchivalSettings(*row)


# ---------------------------------------------------------------------------
# The site calendar and the cutoff
# ---------------------------------------------------------------------------


def now() -> datetime.datetime:
    """The current instant (the one seam the calendar tests pin)."""
    return datetime.datetime.now(datetime.UTC)


def site_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().site_timezone)


def months_before(day: datetime.date, months: int) -> datetime.date:
    """The same day ``months`` calendar months earlier, clamped to the month's last day."""
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return datetime.date(year, month + 1, min(day.day, last))


def start_of_day(day: datetime.date, zone: ZoneInfo) -> datetime.datetime:
    """The instant a site-calendar day starts, in UTC."""
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=zone).astimezone(datetime.UTC)


def site_date_of(moment: datetime.datetime, zone: ZoneInfo) -> datetime.date:
    return moment.astimezone(zone).date()


class Cutoff(NamedTuple):
    #: The site-calendar day whose start is the cutoff.
    day: datetime.date
    #: The start of that day (UTC): history older than it may be archived.
    instant: datetime.datetime


def latest_cutoff_date(
    retention_period_months: int | None, today: datetime.date
) -> datetime.date | None:
    """The newest cutoff day the retention period allows today (None: no retention period)."""
    if retention_period_months is None:
        return None
    return months_before(today, retention_period_months)


def resolve_cutoff(settings: ArchivalSettings, requested: datetime.date | None) -> Cutoff:
    """The cutoff of a preview or request: the requested day, or the newest one allowed.

    A run may choose an older cutoff, never a newer one than the retention
    period allows today (design §3.1, D8).
    """
    zone = site_zone()
    today = site_date_of(now(), zone)
    latest = latest_cutoff_date(settings.retention_period_months, today)
    if latest is None:
        raise ConflictError(_NO_RETENTION_MESSAGE)
    day = latest if requested is None else requested
    if day > latest:
        raise InvalidInputError(
            f"The cutoff cannot be newer than {latest.isoformat()}: the retention period of"
            f" {settings.retention_period_months} months keeps everything since then. Choose"
            " that day or an older one."
        )
    if day < EARLIEST_CUTOFF_DATE:
        raise InvalidInputError(
            f"The cutoff must be on or after {EARLIEST_CUTOFF_DATE.isoformat()}."
        )
    return Cutoff(day, start_of_day(day, zone))


def shortest_period_allows(cutoff: datetime.datetime, shortest_retention_months: int) -> bool:
    """Whether a cutoff is not newer than the shortest allowed period allows today.

    The job re-checks it before an export, so a run requested before the
    shortest allowed period was lengthened never archives newer history.
    """
    zone = site_zone()
    newest = start_of_day(months_before(site_date_of(now(), zone), shortest_retention_months), zone)
    return cutoff <= newest


# ---------------------------------------------------------------------------
# The selection (shared by the preview and the export)
# ---------------------------------------------------------------------------


def lineage_roots(connection: Session | Connection) -> dict[int, int]:
    """The component id (smallest flow id) of every flow that has a lineage edge.

    Union-find over every edge, void ones included: an undone split or
    merge still joins the flows its Movements and reversals are on.
    """
    parent: dict[int, int] = {}

    def find(flow_id: int) -> int:
        root = flow_id
        while parent[root] != root:
            root = parent[root]
        while parent[flow_id] != root:
            parent[flow_id], flow_id = root, parent[flow_id]
        return root

    for first, second in connection.execute(
        text("SELECT parent_flow_id, child_flow_id FROM quantity_flow_lineage")
    ):
        for flow_id in (int(first), int(second)):
            parent.setdefault(flow_id, flow_id)
        left, right = find(int(first)), find(int(second))
        if left != right:
            parent[max(left, right)] = min(left, right)
    return {flow_id: find(flow_id) for flow_id in parent}


#: The eligible closed flows at :cutoff. ``:flow_ids``/``:roots`` carry the
#: component of every flow with a lineage edge; any other flow is its own
#: component. A component with an ACTIVE flow is never eligible.
ELIGIBLE_FLOWS_SQL: Final = """
WITH components AS (
    SELECT c.flow_id, c.root
    FROM unnest(CAST(:flow_ids AS integer[]), CAST(:roots AS integer[])) AS c(flow_id, root)
),
open_roots AS (
    SELECT DISTINCT c.root FROM components c JOIN quantity_flows f ON f.id = c.flow_id
    WHERE f.status = 'ACTIVE'
),
closed_flows AS (
    SELECT f.id AS flow_id, coalesce(c.root, f.id) AS component_id, f.part_number, f.status,
        count(m.id) AS movements, min(m.id) AS first_movement_id,
        max(m.id) AS last_movement_id, min(m.occurred_at) AS first_occurred_at,
        max(m.occurred_at) AS last_occurred_at, max(m.server_received_at) AS last_received_at,
        coalesce(sum(pg_column_size(m.*)), 0) AS row_bytes
    FROM quantity_flows f
    LEFT JOIN components c ON c.flow_id = f.id
    LEFT JOIN part_movements m ON m.quantity_flow_id = f.id
    WHERE f.status <> 'ACTIVE'
    GROUP BY f.id, c.root
),
eligible AS (
    SELECT component_id FROM closed_flows cf
    WHERE NOT EXISTS (SELECT 1 FROM open_roots o WHERE o.root = cf.component_id)
    GROUP BY component_id
    HAVING bool_and(movements > 0) AND max(last_occurred_at) < :cutoff
        AND max(last_received_at) < :cutoff
)
"""


def selection_parameters(roots: Mapping[int, int], cutoff: datetime.datetime) -> dict[str, Any]:
    ordered = sorted(roots.items())
    return {
        "flow_ids": [flow_id for flow_id, _ in ordered],
        "roots": [root for _, root in ordered],
        "cutoff": cutoff,
    }


class PreviewCounts(NamedTuple):
    components: int
    flows: int
    movements: int
    first_occurred_at: datetime.datetime | None
    last_occurred_at: datetime.datetime | None
    estimated_archive_bytes: int


@dataclass(frozen=True)
class Preview:
    cutoff: Cutoff
    retention_period_months: int
    latest_cutoff_date: datetime.date
    counts: PreviewCounts
    duration_ms: int
    time_limit_seconds: int


class _Deadline:
    """The preview time limit across its statements (``statement_timeout`` per statement)."""

    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        self.ends = time.monotonic() + seconds

    def arm(self, session: Session) -> None:
        remaining = int((self.ends - time.monotonic()) * 1000)
        if remaining <= 0:
            raise ConflictError(_timed_out_message(self.seconds))
        session.execute(
            text("SELECT set_config('statement_timeout', :limit, true)"),
            {"limit": f"{remaining}ms"},
        )


def _timed_out_message(seconds: int) -> str:
    return (
        f"The preview did not finish within its time limit of {seconds} seconds, so nothing was"
        " requested. Try again at a quieter time, or raise the preview time limit."
    )


def _counts(session: Session, cutoff: datetime.datetime, deadline: _Deadline) -> PreviewCounts:
    deadline.arm(session)
    roots = lineage_roots(session)
    deadline.arm(session)
    row = session.execute(
        text(
            ELIGIBLE_FLOWS_SQL
            + "SELECT count(DISTINCT cf.component_id) AS components, count(*) AS flows,"
            " coalesce(sum(cf.movements), 0) AS movements,"
            " min(cf.first_occurred_at) AS first_occurred_at,"
            " max(cf.last_occurred_at) AS last_occurred_at,"
            " coalesce(sum(cf.row_bytes), 0) AS row_bytes"
            " FROM closed_flows cf JOIN eligible e ON e.component_id = cf.component_id"
        ),
        selection_parameters(roots, cutoff),
    ).one()
    session.execute(text("SELECT set_config('statement_timeout', '0', true)"))
    flows = int(row.flows)
    estimate = (
        0
        if flows == 0
        else int(int(row.row_bytes) * ARCHIVE_BYTES_PER_ROW_BYTE) + ARCHIVE_FIXED_BYTES
    )
    return PreviewCounts(
        components=int(row.components),
        flows=flows,
        movements=int(row.movements),
        first_occurred_at=row.first_occurred_at,
        last_occurred_at=row.last_occurred_at,
        estimated_archive_bytes=estimate,
    )


def _is_timeout(exc: DBAPIError) -> bool:
    return (
        isinstance(exc.orig, psycopg.errors.QueryCanceled)
        or getattr(exc.orig, "sqlstate", None) == _SQLSTATE_QUERY_CANCELED
    )


def _preview_counts(session: Session, cutoff: datetime.datetime, seconds: int) -> PreviewCounts:
    deadline = _Deadline(seconds)
    try:
        return _counts(session, cutoff, deadline)
    except DBAPIError as exc:
        if _is_timeout(exc):
            session.rollback()
            raise ConflictError(_timed_out_message(seconds)) from exc
        raise


def preview(session: Session, cutoff_date: datetime.date | None) -> Preview:
    """What a run at this cutoff would archive (read-only, within the time limit).

    It reads in its own ``REPEATABLE READ, READ ONLY`` transaction on the
    request's engine, so its statements see one snapshot and it never
    writes.
    """
    started = time.monotonic()
    with Session(session.get_bind()) as reader:
        reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        try:
            settings = read_settings(reader)
            cutoff = resolve_cutoff(settings, cutoff_date)
            counts = _preview_counts(reader, cutoff.instant, settings.preview_seconds)
        finally:
            reader.rollback()
    retention = settings.retention_period_months
    latest = latest_cutoff_date(retention, site_date_of(now(), site_zone()))
    if retention is None or latest is None:  # pragma: no cover - resolve_cutoff refused it
        raise ConflictError(_NO_RETENTION_MESSAGE)
    return Preview(
        cutoff=cutoff,
        retention_period_months=retention,
        latest_cutoff_date=latest,
        counts=counts,
        duration_ms=int((time.monotonic() - started) * 1000),
        time_limit_seconds=settings.preview_seconds,
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


#: The audited facet of a run (every column but the timestamps' formats).
RUN_COLUMNS: Final = tuple(column.name for column in HistoryArchiveRun.__table__.columns)


def run_values(run: HistoryArchiveRun) -> dict[str, Any]:
    return {column: getattr(run, column) for column in RUN_COLUMNS}


def run_snapshot(values: Mapping[Any, Any]) -> dict[str, Any]:
    """The audit snapshot of a run: every column, times as UTC text, and its groups."""
    snapshot: dict[str, Any] = {
        column: iso(value) if isinstance(value, datetime.datetime) else value
        for column, value in ((column, values[column]) for column in RUN_COLUMNS)
    }
    snapshot["groups"] = [GROUP_MOVEMENT_HISTORY]
    return snapshot


def _audit_run(
    session: Session,
    run: HistoryArchiveRun,
    *,
    before: dict[str, Any] | None,
    command: str,
    actor_user_id: int,
) -> None:
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED if before is None else AuditEventType.UPDATED,
        entity_type=AuditEntityType.HISTORY_ARCHIVE_RUN,
        entity_id=str(run.id),
        before_data=before,
        after_data=run_snapshot(run_values(run)),
        actor_user_id=actor_user_id,
        metadata={"source": "web", "command": command},
    )


def _open_run(session: Session) -> HistoryArchiveRun | None:
    return session.scalar(
        select(HistoryArchiveRun)
        .where(HistoryArchiveRun.state.in_(HISTORY_ARCHIVE_OPEN_STATES))
        .order_by(HistoryArchiveRun.id)
        .limit(1)
    )


def _refuse_open_run(existing: HistoryArchiveRun | None) -> NoReturn:
    if existing is None:
        raise ConflictError(_OPEN_RUN_MESSAGE)
    raise ConflictError(
        f"Run {existing.id} is still open ({existing.state}). Cancel it or wait until it"
        " finishes before requesting a new run."
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def request_run(
    session: Session, *, reason: object, cutoff_date: datetime.date | None, actor_user_id: int
) -> HistoryArchiveRun:
    """Request (and so approve) a run; its preview is recomputed and stored.

    Refused while another run is open, without a retention period, for a
    cutoff newer than the retention period allows, and when nothing is
    eligible. The database refuses a second open run even in a race
    (``uq_history_archive_runs_open``; the refusal names the run that won
    when it is still open) and a cutoff newer than the shortest allowed
    period (the run guard trigger, also when the period was lengthened
    meanwhile): both are a 409, never a server error.
    """
    clean_reason = maintenance_reason(reason, _REQUEST_REASON_MESSAGE)
    settings = read_settings(session)
    cutoff = resolve_cutoff(settings, cutoff_date)
    existing = _open_run(session)
    if existing is not None:
        _refuse_open_run(existing)
    counts = _preview_counts(session, cutoff.instant, settings.preview_seconds)
    if counts.flows == 0:
        raise ConflictError(
            f"Nothing is eligible before {cutoff.day.isoformat()}, so no run was requested."
        )
    run = HistoryArchiveRun(
        state=HistoryArchiveRunState.APPROVED,
        cutoff=cutoff.instant,
        reason=clean_reason,
        requested_by_user_id=actor_user_id,
        preview_components=counts.components,
        preview_flows=counts.flows,
        preview_movements=counts.movements,
        preview_first_occurred_at=counts.first_occurred_at,
        preview_last_occurred_at=counts.last_occurred_at,
        preview_archive_bytes=counts.estimated_archive_bytes,
    )
    session.add(run)
    conflicts = {_OPEN_RUN_INDEX: _OPEN_RUN_MESSAGE, _CUTOFF_FLOOR_GUARD: _CUTOFF_FLOOR_MESSAGE}
    try:
        flush(session, {_CUTOFF_FLOOR_GUARD: _CUTOFF_FLOOR_MESSAGE})
    except IntegrityError as exc:
        # Lost the race for the one open run (flush rolled back): name the
        # winner; the violation stays the refusal's context.
        if _constraint_name(exc) != _OPEN_RUN_INDEX:
            raise
        _refuse_open_run(_open_run(session))
    session.refresh(run)
    _audit_run(session, run, before=None, command="request", actor_user_id=actor_user_id)
    commit(session, conflicts)
    return run


def _locked_run(session: Session, run_id: int) -> HistoryArchiveRun:
    run = (
        session.get(HistoryArchiveRun, run_id, with_for_update=True, populate_existing=True)
        if is_bindable_id(run_id)
        else None
    )
    if run is None:
        raise NotFoundError(f"There is no archival run {run_id}.")
    return run


def cancel_run(session: Session, run_id: int, *, actor_user_id: int) -> HistoryArchiveRun:
    """Cancel an APPROVED or WAITING_FOR_COPY run; cancelling a cancelled run changes nothing."""
    run = _locked_run(session, run_id)
    if run.state == HistoryArchiveRunState.CANCELLED:
        return run
    if run.state == HistoryArchiveRunState.RUNNING:
        raise ConflictError(
            f"Run {run.id} is running on the server now and cannot be cancelled. Wait until it"
            " stops."
        )
    if run.state not in (HistoryArchiveRunState.APPROVED, HistoryArchiveRunState.WAITING_FOR_COPY):
        raise ConflictError(f"Run {run.id} is finished ({run.state}) and cannot be cancelled.")
    before = run_snapshot(run_values(run))
    run.state = HistoryArchiveRunState.CANCELLED
    run.finished_at = now()
    run.cancelled_by_user_id = actor_user_id
    flush(session, {})
    _audit_run(session, run, before=before, command="cancel", actor_user_id=actor_user_id)
    commit(session, {})
    return run


def confirm_copy(
    session: Session, run_id: int, *, manifest_sha256: object, actor_user_id: int
) -> HistoryArchiveRun:
    """Confirm that the archive of a WAITING_FOR_COPY run was copied off the host.

    ``manifest_sha256`` is the value the person was shown; another value is
    refused, so a stale screen never confirms another archive. The host job
    finishes the run at its next start. Confirming again changes nothing.
    """
    run = _locked_run(session, run_id)
    if run.state != HistoryArchiveRunState.WAITING_FOR_COPY:
        raise ConflictError(
            f"Run {run.id} is {run.state}; only a run waiting for its off-host copy can be"
            " confirmed."
        )
    if manifest_sha256 != run.manifest_sha256:
        raise ConflictError(
            f"The archive of run {run.id} has the manifest SHA-256 {run.manifest_sha256}, not the"
            " one confirmed. Reload the run and check the copy again."
        )
    if run.copy_confirmed_at is not None:
        return run
    before = run_snapshot(run_values(run))
    run.copy_confirmed_at = now()
    run.copy_confirmed_by_user_id = actor_user_id
    flush(session, {})
    _audit_run(
        session, run, before=before, command="copy_confirmation", actor_user_id=actor_user_id
    )
    commit(session, {})
    return run


class RunUser(NamedTuple):
    user_id: int
    display_name: str


@dataclass(frozen=True)
class RunView:
    run: HistoryArchiveRun
    requested_by: RunUser
    copy_confirmed_by: RunUser | None
    cancelled_by: RunUser | None


def _users(session: Session, ids: set[int]) -> dict[int, RunUser]:
    return {
        user_id: RunUser(ref.id, ref.display_name)
        for user_id, ref in user_access.user_refs(session, ids).items()
    }


def views(session: Session, runs: Sequence[HistoryArchiveRun]) -> list[RunView]:
    ids = {
        user_id
        for run in runs
        for user_id in (
            run.requested_by_user_id,
            run.copy_confirmed_by_user_id,
            run.cancelled_by_user_id,
        )
        if user_id is not None
    }
    users = _users(session, ids)

    def user(user_id: int | None) -> RunUser | None:
        return None if user_id is None else users.get(user_id, RunUser(user_id, f"user {user_id}"))

    result: list[RunView] = []
    for run in runs:
        requested = user(run.requested_by_user_id)
        assert requested is not None
        result.append(
            RunView(
                run, requested, user(run.copy_confirmed_by_user_id), user(run.cancelled_by_user_id)
            )
        )
    return result


def list_runs(session: Session, *, limit: int = DEFAULT_RUN_LIMIT) -> list[RunView]:
    """The newest runs first."""
    if not 1 <= limit <= 500:
        raise InvalidInputError("limit must be a whole number from 1 to 500.")
    runs = list(
        session.scalars(
            select(HistoryArchiveRun).order_by(HistoryArchiveRun.id.desc()).limit(limit)
        )
    )
    return views(session, runs)


def run_view(session: Session, run: HistoryArchiveRun) -> RunView:
    return views(session, [run])[0]


def is_final(state: str) -> bool:
    return state in HISTORY_ARCHIVE_FINAL_STATES
