"""Movement-history archival runs (Phase 16, history archival design B; build steps 1, 2, 4).

The application side of Administration → History archival & purge, and the
one selection that both its preview and the host job's export run
(``app.application.history_archive_job``). It is the only module that reads
the retention period (PROJECT_PROFILE §28: retention settings live in
Administration, never in production workflow logic). Nothing here deletes
anything: the host job purges (build step 2), as the maintenance database
role, under the Movement deletion guard.

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
that reopens a flow writes Movements. A purged component has no Movement
left, so it is never eligible again.

The preview runs the selection read-only within the preview time limit of
the settings and reports the components, flows and Movements, their date
range and an estimated archive size. A request recomputes the preview and
stores it on the run, which starts ``APPROVED`` (the request is the
approval); at most one run is open (``PURGING`` included). A run may be
cancelled while ``APPROVED`` or ``WAITING_FOR_COPY``, never while the job
works on it (``RUNNING``, ``PURGING``); the off-host copy of a
``WAITING_FOR_COPY`` run is confirmed once, against the manifest SHA-256 the
person was shown. Every state change and the copy confirmation append one
``HistoryArchiveRun`` audit row (``entity_id`` the run id) in the same
transaction.

Automatic runs (build step 4; design §6.2, D9-D11): the host job's
``history-archive evaluate`` asks ``evaluate_automatic`` whether a run is
needed now — automatic runs on, a retention period set, no run open, the
last automatic run ended at least the days between automatic runs ago, the
standing authorization valid while the approval step is off, the measure
above its limit, something eligible at the retention cutoff — and creates
it: ``PROPOSED`` while the approval step is required, ``APPROVED`` under the
standing authorization otherwise, with the preview and an automatic reason.
A holder of the key approves a ``PROPOSED`` run with a reason of their own
(``APPROVED``) or dismisses it (``DISMISSED``, mirroring Cancel: no reason,
and dismissing a dismissed run changes nothing). The standing authorization
is the audit row that switched the approval step off; it is valid while
its User is active and holds Run history archival and purge
(``standing_authorization``), the same test the job applies to the
requester, the approver and that User before the backup and the purge
(``archival_authority``), and while no run created under it failed that
test (until a key holder saves the approval setting again). A run created
under it proceeds only while the approval step stayed off since
(``withdrawn_since``), automatic runs are on, and the retention period
still allows its cutoff (``retention_problem``).
"""

import calendar
import datetime
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NamedTuple, NoReturn
from zoneinfo import ZoneInfo

import psycopg.errors
from sqlalchemy import Connection, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.application import audit, user_access
from app.application.common import commit, flush, is_bindable_id
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.policies import (
    DATA_RETENTION_SECTION,
    STANDING_AUTHORIZATION_WITHDRAWN,
    maintenance_reason,
)
from app.core.config import get_settings
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    HistoryArchiveMeasure,
    HistoryArchiveRunState,
    HistoryArchiveRunTrigger,
    Permission,
)
from app.infrastructure.models import (
    ARCHIVE_VIEWER_TABLES,
    HISTORY_ARCHIVE_FINAL_STATES,
    HISTORY_ARCHIVE_OPEN_STATES,
    ApplicationPolicy,
    HistoryArchiveRun,
    HistoryArchiveView,
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
    #: The purge batch limits (build step 2): whole components per batch,
    #: at most this many Movements and Part Numbers (one oversized
    #: component forms a batch of its own).
    purge_batch_movements: int
    purge_batch_part_numbers: int


def read_settings(session: Session | Connection) -> ArchivalSettings:
    """The stored settings (unlocked; a column select, no policy instance)."""
    row = session.execute(
        select(
            ApplicationPolicy.retention_period_months,
            ApplicationPolicy.shortest_retention_months,
            ApplicationPolicy.archive_max_wait_days,
            ApplicationPolicy.archive_start_minute,
            ApplicationPolicy.archive_preview_seconds,
            ApplicationPolicy.archive_purge_batch_movements,
            ApplicationPolicy.archive_purge_batch_part_numbers,
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

    def arm(self, session: Session | Connection) -> None:
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


def _counts(
    session: Session | Connection, cutoff: datetime.datetime, deadline: _Deadline
) -> PreviewCounts:
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
    if existing.state == HistoryArchiveRunState.PROPOSED:
        raise ConflictError(
            f"Run {existing.id} is waiting for approval (PROPOSED). Approve or dismiss it before"
            " requesting a new run."
        )
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
        trigger=HistoryArchiveRunTrigger.MANUAL,
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
    """Cancel an APPROVED or WAITING_FOR_COPY run; cancelling a cancelled run changes nothing.

    A RUNNING or PURGING run is the host job's: refused (a PURGING run is
    finished by the job, never stopped half-way by a person). A PROPOSED run
    is dismissed, not cancelled (``dismiss_run``).
    """
    run = _locked_run(session, run_id)
    if run.state == HistoryArchiveRunState.CANCELLED:
        return run
    if run.state == HistoryArchiveRunState.PROPOSED:
        raise ConflictError(
            f"Run {run.id} waits for approval (PROPOSED): dismiss it instead of cancelling it."
        )
    if run.state in (HistoryArchiveRunState.RUNNING, HistoryArchiveRunState.PURGING):
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


_APPROVAL_REASON_MESSAGE: Final = (
    "Enter the reason for approving this run: 1 to 500 characters of plain text."
)


def approve_run(
    session: Session, run_id: int, *, reason: object, actor_user_id: int
) -> HistoryArchiveRun:
    """Approve a PROPOSED (automatic) run with a reason of the approver's own: APPROVED.

    The approver, the time (the database's) and the reason are recorded
    once. Approving a run this User approved already changes nothing (a
    repeated request); a run that is not PROPOSED is refused otherwise, also
    when another User won a race to approve it. The host job re-checks the
    approver before the pre-purge backup and before the purge.
    """
    clean_reason = maintenance_reason(reason, _APPROVAL_REASON_MESSAGE)
    run = _locked_run(session, run_id)
    if run.state != HistoryArchiveRunState.PROPOSED:
        if run.approved_by_user_id == actor_user_id:
            return run
        raise ConflictError(
            f"Run {run.id} is {run.state}; only a run waiting for approval (PROPOSED) can be"
            " approved."
        )
    before = run_snapshot(run_values(run))
    run.state = HistoryArchiveRunState.APPROVED
    run.approved_by_user_id = actor_user_id
    run.approved_at = func.now()
    run.approval_reason = clean_reason
    flush(session, {})
    session.refresh(run)
    _audit_run(session, run, before=before, command="approve", actor_user_id=actor_user_id)
    commit(session, {})
    return run


def dismiss_run(session: Session, run_id: int, *, actor_user_id: int) -> HistoryArchiveRun:
    """Dismiss a PROPOSED run (DISMISSED); dismissing a dismissed run changes nothing.

    It mirrors ``cancel_run``: no reason, the end time and who dismissed it
    are recorded. A run that was approved, or one the host job works on, is
    refused (an approved run is cancelled instead).
    """
    run = _locked_run(session, run_id)
    if run.state == HistoryArchiveRunState.DISMISSED:
        return run
    if run.state in (HistoryArchiveRunState.APPROVED, HistoryArchiveRunState.WAITING_FOR_COPY):
        raise ConflictError(
            f"Run {run.id} is {run.state}; only a run waiting for approval (PROPOSED) can be"
            " dismissed. Cancel it instead."
        )
    if run.state in (HistoryArchiveRunState.RUNNING, HistoryArchiveRunState.PURGING):
        raise ConflictError(
            f"Run {run.id} is running on the server now and cannot be dismissed. Wait until it"
            " stops."
        )
    if run.state != HistoryArchiveRunState.PROPOSED:
        raise ConflictError(f"Run {run.id} is finished ({run.state}) and cannot be dismissed.")
    before = run_snapshot(run_values(run))
    run.state = HistoryArchiveRunState.DISMISSED
    run.finished_at = now()
    run.dismissed_by_user_id = actor_user_id
    flush(session, {})
    _audit_run(session, run, before=before, command="dismiss", actor_user_id=actor_user_id)
    commit(session, {})
    return run


class RunUser(NamedTuple):
    user_id: int
    display_name: str


class RunAuthorization(NamedTuple):
    """The standing authorization an automatic run was created under."""

    audit_event_id: int
    authorized_by: RunUser | None
    authorized_at: datetime.datetime | None


@dataclass(frozen=True)
class RunView:
    run: HistoryArchiveRun
    #: None exactly for an automatic run.
    requested_by: RunUser | None
    copy_confirmed_by: RunUser | None
    cancelled_by: RunUser | None
    approved_by: RunUser | None = None
    dismissed_by: RunUser | None = None
    authorization: RunAuthorization | None = None
    #: The newest view of its archive (build step 5): its id and state.
    archive_view: tuple[int, str] | None = None

    @property
    def viewable(self) -> bool:
        """Whether its archive can be opened for viewing: finished, verified."""
        return is_final(self.run.state) and self.run.verified_at is not None


def _users(session: Session, ids: set[int]) -> dict[int, RunUser]:
    return {
        user_id: RunUser(ref.id, ref.display_name)
        for user_id, ref in user_access.user_refs(session, ids).items()
    }


def _authorization_rows(
    session: Session, ids: set[int]
) -> dict[int, tuple[int | None, datetime.datetime | None]]:
    """(the actor, the time) of each audit row that is a standing authorization."""
    if not ids:
        return {}
    return {
        int(row.id): (row.actor_user_id, row.occurred_at)
        for row in session.execute(
            text(
                "SELECT id, actor_user_id, occurred_at FROM audit_events"
                " WHERE id = ANY(CAST(:ids AS bigint[]))"
            ),
            {"ids": sorted(ids)},
        )
    }


def views(session: Session, runs: Sequence[HistoryArchiveRun]) -> list[RunView]:
    authorizations = _authorization_rows(
        session, {run.authorization_id for run in runs if run.authorization_id is not None}
    )
    ids = {
        user_id
        for run in runs
        for user_id in (
            run.requested_by_user_id,
            run.copy_confirmed_by_user_id,
            run.cancelled_by_user_id,
            run.approved_by_user_id,
            run.dismissed_by_user_id,
        )
        if user_id is not None
    } | {actor for actor, _at in authorizations.values() if actor is not None}
    users = _users(session, ids)

    def user(user_id: int | None) -> RunUser | None:
        return None if user_id is None else users.get(user_id, RunUser(user_id, f"user {user_id}"))

    def authorization(audit_event_id: int | None) -> RunAuthorization | None:
        if audit_event_id is None:
            return None
        actor, at = authorizations.get(audit_event_id, (None, None))
        return RunAuthorization(audit_event_id, user(actor), at)

    archive_views = _newest_views(session, {run.id for run in runs})
    return [
        RunView(
            run,
            user(run.requested_by_user_id),
            user(run.copy_confirmed_by_user_id),
            user(run.cancelled_by_user_id),
            approved_by=user(run.approved_by_user_id),
            dismissed_by=user(run.dismissed_by_user_id),
            authorization=authorization(run.authorization_id),
            archive_view=archive_views.get(run.id),
        )
        for run in runs
    ]


def _newest_views(session: Session, run_ids: set[int]) -> dict[int, tuple[int, str]]:
    """The newest view (its id and state) of each of these runs that has one."""
    if not run_ids:
        return {}
    newest = (
        select(func.max(HistoryArchiveView.id))
        .where(HistoryArchiveView.run_id.in_(run_ids))
        .group_by(HistoryArchiveView.run_id)
    )
    return {
        int(row.run_id): (int(row.id), str(row.state))
        for row in session.execute(
            select(
                HistoryArchiveView.run_id, HistoryArchiveView.id, HistoryArchiveView.state
            ).where(HistoryArchiveView.id.in_(newest))
        )
    }


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


#: The purge receipt beside a published archive folder: ``<archive name>`` + this.
RECEIPT_SUFFIX: Final = ".purged.json"


def receipt_name(archive_name: str | None, receipt_sha256: str | None) -> str | None:
    """The file name of a run's purge receipt, once it was written (None before)."""
    if archive_name is None or receipt_sha256 is None:
        return None
    return f"{archive_name}{RECEIPT_SUFFIX}"


# ---------------------------------------------------------------------------
# Automatic runs (build step 4; design §6.2, D9-D11)
# ---------------------------------------------------------------------------


class AutomaticRunSettings(NamedTuple):
    """The automatic-run settings of Administration → History archival & purge, as stored."""

    enabled: bool
    measure: str
    limit_movement_rows: int
    limit_history_months: int
    limit_database_gib: int
    approval_required: bool
    interval_days: int
    #: The audit row of the standing authorization, exactly while the approval step is off.
    authorization_id: int | None


def read_automatic_settings(connection: Session | Connection) -> AutomaticRunSettings:
    """The stored automatic-run settings (unlocked; a column select)."""
    row = connection.execute(
        select(
            ApplicationPolicy.archive_automatic_runs,
            ApplicationPolicy.archive_measure,
            ApplicationPolicy.archive_limit_movement_rows,
            ApplicationPolicy.archive_limit_history_months,
            ApplicationPolicy.archive_limit_database_gib,
            ApplicationPolicy.archive_approval_required,
            ApplicationPolicy.archive_interval_days,
            ApplicationPolicy.archive_authorization_id,
        ).where(ApplicationPolicy.id == 1)
    ).one()
    return AutomaticRunSettings(*row)


#: The key a User needs to request, approve or authorize a run (design D7, §6.2).
ARCHIVAL_KEY: Final = Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE


class ArchivalAuthority(NamedTuple):
    """Whether a User may still have a run purged: active, and holding the archival key."""

    user_id: int
    #: None: there is no such User.
    display_name: str | None
    active: bool
    holds_key: bool

    @property
    def who(self) -> str:
        if self.display_name is None:
            return f"user {self.user_id}"
        return f"{self.display_name} (user {self.user_id})"

    @property
    def valid(self) -> bool:
        return self.active and self.holds_key


def archival_authority(connection: Session | Connection, user_id: int) -> ArchivalAuthority:
    """The User as they stand now (the host job's re-check, and the standing authorization's)."""
    row = (
        connection.execute(
            text(
                "SELECT u.display_name, u.is_active, EXISTS (SELECT 1 FROM role_permissions p"
                " WHERE p.role_id = u.role_id AND p.permission = :key) AS holds"
                " FROM users u WHERE u.id = :id"
            ),
            {"id": user_id, "key": ARCHIVAL_KEY.value},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return ArchivalAuthority(user_id, None, False, False)
    return ArchivalAuthority(
        user_id, str(row["display_name"]), bool(row["is_active"]), bool(row["holds"])
    )


class StandingAuthorization(NamedTuple):
    """The standing authorization of automatic runs: the audit row that switched the
    approval step off (design D9), with its User as they stand now."""

    audit_event_id: int
    #: The User who switched the approval step off (None: the row names no User).
    authority: ArchivalAuthority | None
    authorized_at: datetime.datetime | None
    reason: str | None
    #: The newest run created under it that failed because its User could no
    #: longer run it (``authorization_failed``; None: no such run). A run failed
    #: by a withdrawal names an authorization the setting no longer holds, and a
    #: run stopped by the settings (automatic runs off, a retention period that
    #: no longer allows its cutoff) is a plain failure.
    failed_run_id: int | None = None

    @property
    def problem(self) -> str | None:
        """Why it is no longer valid (None: it is valid).

        A run that failed under it keeps it invalid, even once its User is
        active and holds the key again, until a key holder saves the approval
        setting again (a renewal, or switching the step on: design §6.2);
        without such a run its validity is its User's, now.
        """
        authority = self.authority
        if authority is None:  # pragma: no cover - every such row names its User
            return (
                f"The standing authorization of automatic runs (audit row {self.audit_event_id})"
                " names no User. No automatic run is created until a holder of Run history"
                " archival and purge saves the approval setting again."
            )
        if self.failed_run_id is not None:
            return (
                f"Run {self.failed_run_id} failed because the User who switched off the approval"
                f" step of automatic runs, {authority.who}, could no longer run it. This standing"
                " authorization stays invalid, even once that User is active and holds Run"
                " history archival and purge again: no automatic run is created until a holder"
                " of the key saves the approval setting again."
            )
        if not authority.active:
            return (
                "The User who switched off the approval step of automatic runs,"
                f" {authority.who}, is no longer active. No automatic run is created until a"
                " holder of Run history archival and purge saves the approval setting again."
            )
        if not authority.holds_key:
            return (
                "The User who switched off the approval step of automatic runs,"
                f" {authority.who}, no longer holds Run history archival and purge. No"
                " automatic run is created until a holder of the key saves the approval setting"
                " again."
            )
        return None

    @property
    def valid(self) -> bool:
        return self.problem is None

    def document(self) -> dict[str, Any]:
        """Its JSON form (the settings API, ``history-archive evaluate`` and ``status``)."""
        authority = self.authority
        return {
            "audit_event_id": self.audit_event_id,
            "authorized_by": (
                None
                if authority is None
                else {
                    "user_id": authority.user_id,
                    "display_name": authority.display_name or f"user {authority.user_id}",
                }
            ),
            "authorized_at": iso(self.authorized_at),
            "reason": self.reason,
            "valid": self.valid,
            "problem": self.problem,
        }


def standing_authorization(
    connection: Session | Connection, audit_event_id: int | None
) -> StandingAuthorization | None:
    """The standing authorization of this audit row, valid or not (None: no row named)."""
    if audit_event_id is None:
        return None
    row = (
        connection.execute(
            text(
                "SELECT actor_user_id, occurred_at, metadata ->> 'reason' AS reason"
                " FROM audit_events WHERE id = :id"
            ),
            {"id": audit_event_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:  # pragma: no cover - a foreign key names the row
        return StandingAuthorization(audit_event_id, None, None, None)
    actor = row["actor_user_id"]
    authority = None if actor is None else archival_authority(connection, int(actor))
    failed_run_id = connection.execute(
        select(func.max(HistoryArchiveRun.id)).where(
            HistoryArchiveRun.authorization_id == audit_event_id,
            HistoryArchiveRun.authorization_failed.is_(True),
        )
    ).scalar_one()
    return StandingAuthorization(
        audit_event_id, authority, row["occurred_at"], row["reason"], failed_run_id
    )


def withdrawn_since(connection: Session | Connection, audit_event_id: int) -> bool:
    """Whether the approval step of automatic runs was switched on again after this
    standing authorization: a later ``data-retention`` audit row withdrew it, even
    if the step is off again since (a new authorization). A renewal withdraws
    nothing: the step stays off. The host job reads it, with the setting itself,
    before a run created under the authorization may be backed up or purged
    (design §6.2: such a run proceeds only while the step stays off).
    """
    return bool(
        connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM audit_events WHERE entity_type = :entity_type"
                " AND entity_id = :entity_id AND id > :id"
                " AND metadata ->> 'standing_authorization' = :withdrawn)"
            ),
            {
                "entity_type": AuditEntityType.APPLICATION_POLICY.value,
                "entity_id": DATA_RETENTION_SECTION,
                "id": audit_event_id,
                "withdrawn": STANDING_AUTHORIZATION_WITHDRAWN,
            },
        ).scalar_one()
    )


def retention_problem(
    connection: Session | Connection, run_id: int, cutoff: datetime.datetime
) -> str | None:
    """Why the retention period as it stands now no longer allows the cutoff of a run
    created under the standing authorization (None: it still does).

    Nobody reviewed such a run's cutoff, so the setting that would make it now
    must still allow it: a cutoff newer than the newest one the current
    retention period allows today, or no retention period, stops it before its
    pre-purge backup and its purge (review B4, minor 1) — a plain failure,
    which leaves the standing authorization valid. A manual run and an
    approved proposal keep the cutoff a person chose.
    """
    zone = site_zone()
    day = site_date_of(cutoff, zone).isoformat()
    retention = read_settings(connection).retention_period_months
    latest = latest_cutoff_date(retention, site_date_of(now(), zone))
    if retention is None or latest is None:
        return (
            f"No retention period is set now, so the history of run {run_id}, created without"
            f" approval with the cutoff {day}, was not purged. Nothing was deleted; no history is"
            " eligible until a retention period is set."
        )
    if cutoff <= start_of_day(latest, zone):
        return None
    return (
        f"The retention period is now {_months(retention)}: it keeps everything since"
        f" {latest.isoformat()}, and run {run_id} was created without approval with the newer"
        f" cutoff {day}, so its history was not purged. Nothing was deleted; the next automatic"
        " run takes the cutoff the retention period allows then, and a holder of Run history"
        " archival and purge may request a new run."
    )


#: One gibibyte: the unit of the database-size limit.
GIB: Final = 1024**3
#: The live Movement rows: an exact count (one scan of the table, as ``status`` counts them).
LIVE_MOVEMENT_ROWS_SQL: Final = "SELECT count(*) FROM part_movements"
#: The oldest retained Movement, exactly, through the index
#: ``ix_part_movements_part_number_occurred_at_id`` alone: a loose index scan
#: that steps from one Part Number to the next and takes each one's oldest
#: Movement — two index probes per Part Number, never a scan of the table.
OLDEST_RETAINED_MOVEMENT_SQL: Final = (
    "WITH RECURSIVE pns AS ("
    "(SELECT part_number FROM part_movements ORDER BY part_number LIMIT 1)"
    " UNION ALL"
    " SELECT (SELECT m.part_number FROM part_movements m WHERE m.part_number > pns.part_number"
    " ORDER BY m.part_number LIMIT 1) FROM pns WHERE pns.part_number IS NOT NULL)"
    " SELECT min((SELECT m.occurred_at FROM part_movements m"
    " WHERE m.part_number = pns.part_number ORDER BY m.occurred_at LIMIT 1))"
    " FROM pns WHERE pns.part_number IS NOT NULL"
)
#: The database's size: the exact sum of its files on disk.
DATABASE_SIZE_SQL: Final = "SELECT pg_database_size(current_database())"
#: The disk space of the viewer tables of viewing an archive (build step 5) with
#: their indexes and TOAST: every loaded view's rows and the space closed views
#: freed in them. A copy of archived history, never live history, so the
#: database-size measure leaves it out.
VIEWER_BYTES_SQL: Final = (
    "SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0) FROM pg_class c"
    " JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
)
#: How each measure is read (the report's ``measure.read``): all exact, no estimate.
MEASURE_READS: Final = {
    HistoryArchiveMeasure.LIVE_MOVEMENT_ROWS.value: "count",
    HistoryArchiveMeasure.RETAINED_HISTORY_AGE.value: "index",
    HistoryArchiveMeasure.DATABASE_SIZE.value: "size",
}


class Measurement(NamedTuple):
    """What the measure of automatic runs read now, and whether it passes its limit."""

    measure: str
    #: Live Movement rows, the database's bytes without the viewer tables, or
    #: the time of the oldest retained Movement (None: no Movement is retained).
    value: int | datetime.datetime | None
    #: Rows, months or GiB, as the setting of the measure holds it.
    limit: int
    passes: bool
    #: The age measure only: the instant the oldest retained Movement is older
    #: than when it passes (the cutoff's day, the limit's months earlier).
    threshold: datetime.datetime | None = None
    #: The size measure only: the database's whole size, and the space of the
    #: viewer tables subtracted from it (``value`` is the difference).
    database_bytes: int | None = None
    viewer_bytes: int | None = None

    def document(self) -> dict[str, Any]:
        value = iso(self.value) if isinstance(self.value, datetime.datetime) else self.value
        return {
            "name": self.measure,
            "read": MEASURE_READS[self.measure],
            "value": value,
            "limit": self.limit,
            "threshold": iso(self.threshold),
            "passes": self.passes,
            "database_bytes": self.database_bytes,
            "viewer_bytes": self.viewer_bytes,
        }


def database_bytes(connection: Session | Connection) -> int:
    """The database's size in bytes (the seam the size tests pin)."""
    return int(connection.execute(text(DATABASE_SIZE_SQL)).scalar_one())


def viewer_bytes(connection: Session | Connection) -> int:
    """The disk space of the viewer tables with their indexes and TOAST (``status`` reports
    it; the database-size measure subtracts it)."""
    return int(
        connection.execute(
            text(VIEWER_BYTES_SQL), {"tables": list(ARCHIVE_VIEWER_TABLES)}
        ).scalar_one()
    )


def measure(
    connection: Session | Connection,
    settings: AutomaticRunSettings,
    cutoff: Cutoff,
    zone: ZoneInfo,
) -> Measurement:
    """Read the measure of the settings and compare it with its limit.

    Live Movement rows pass above their limit; the database size above its
    limit in GiB; the retained history when its oldest Movement is older
    than the cutoff by more than the limit's months (strictly older than the
    start of the cutoff's day that many months earlier).

    The database size leaves out the viewer tables of viewing an archive
    (their rows, indexes and TOAST, and the space closed views freed in them):
    a loaded view is a copy of archived history, and opening one must never
    make an automatic run (review of step 5, M1). Both sizes are reported.
    """
    if settings.measure == HistoryArchiveMeasure.RETAINED_HISTORY_AGE:
        oldest = connection.execute(text(OLDEST_RETAINED_MOVEMENT_SQL)).scalar_one()
        threshold = start_of_day(months_before(cutoff.day, settings.limit_history_months), zone)
        return Measurement(
            settings.measure,
            oldest,
            settings.limit_history_months,
            oldest is not None and oldest < threshold,
            threshold,
        )
    if settings.measure == HistoryArchiveMeasure.DATABASE_SIZE:
        whole = database_bytes(connection)
        viewer = viewer_bytes(connection)
        size = max(whole - viewer, 0)
        return Measurement(
            settings.measure,
            size,
            settings.limit_database_gib,
            size > settings.limit_database_gib * GIB,
            database_bytes=whole,
            viewer_bytes=viewer,
        )
    rows = int(connection.execute(text(LIVE_MOVEMENT_ROWS_SQL)).scalar_one())
    return Measurement(
        settings.measure, rows, settings.limit_movement_rows, rows > settings.limit_movement_rows
    )


def _months(count: int) -> str:
    return f"{count} month" if count == 1 else f"{count} months"


def _days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def _older(months: int) -> str:
    return "older" if months == 0 else f"more than {_months(months)} older"


def _gib(size: int) -> str:
    return f"{size:,} bytes ({size / GIB:.1f} GiB)"


def automatic_reason(
    measurement: Measurement, retention_period_months: int, cutoff: Cutoff, zone: ZoneInfo
) -> str:
    """The reason of an automatic run: the measure, its value, its limit and the retention."""
    retention = f"retention {_months(retention_period_months)}"
    value = measurement.value
    if isinstance(value, datetime.datetime):
        return (
            f"Oldest retained Movement {site_date_of(value, zone).isoformat()}"
            f" {_older(measurement.limit)} than the cutoff {cutoff.day.isoformat()}; {retention}"
        )
    assert value is not None
    if measurement.measure == HistoryArchiveMeasure.DATABASE_SIZE:
        return f"Database size {_gib(value)} above the limit {measurement.limit:,} GiB; {retention}"
    return f"Live Movement rows {value:,} above the limit {measurement.limit:,}; {retention}"


def _below_limit_message(measurement: Measurement, cutoff: Cutoff, zone: ZoneInfo) -> str:
    value = measurement.value
    if measurement.measure == HistoryArchiveMeasure.RETAINED_HISTORY_AGE:
        if not isinstance(value, datetime.datetime):
            return "No Movement is retained, so no run was created."
        return (
            f"The oldest retained Movement, {site_date_of(value, zone).isoformat()}, is not"
            f" {_older(measurement.limit)} than the cutoff {cutoff.day.isoformat()}, so no run"
            " was created."
        )
    assert isinstance(value, int)
    if measurement.measure == HistoryArchiveMeasure.DATABASE_SIZE:
        return (
            f"Database size {_gib(value)} is not above the limit {measurement.limit:,} GiB, so no"
            " run was created."
        )
    return (
        f"Live Movement rows {value:,} are not above the limit {measurement.limit:,}, so no run"
        " was created."
    )


#: Every result of ``history-archive evaluate``, one per reason, in the order
#: the conditions are checked (the first that fails decides; ``settings_changed``
#: is the job's, when a setting the decision was made from changed while it
#: evaluated: ``AutomaticDecision.settings_hold``).
EVALUATE_RESULTS: Final = (
    "off",
    "no_retention_period",
    "run_open",
    "not_due",
    "authorization_invalid",
    "below_limit",
    "nothing_eligible",
    "settings_changed",
    "created",
)


class LastAutomaticRun(NamedTuple):
    id: int
    state: str
    finished_at: datetime.datetime


class EvaluationTimeoutError(Exception):
    """The measure and the preview of an evaluation ran out of the preview time limit."""


def _evaluation_timed_out(seconds: int) -> str:
    return (
        "The evaluation of automatic runs did not finish within the preview time limit of"
        f" {seconds} seconds, so no run was created. The next hourly work evaluates again; raise"
        " the preview time limit if this repeats."
    )


@dataclass
class AutomaticDecision:
    """What ``history-archive evaluate`` decided, and every fact it read for it."""

    settings: AutomaticRunSettings
    retention_period_months: int | None
    time_limit_seconds: int
    result: str = "off"
    message: str = ""
    cutoff: Cutoff | None = None
    #: The open run (its id and state), with ``run_open``.
    open_run: tuple[int, str] | None = None
    last_automatic_run: LastAutomaticRun | None = None
    next_due_at: datetime.datetime | None = None
    authorization: StandingAuthorization | None = None
    measurement: Measurement | None = None
    preview: PreviewCounts | None = None
    preview_duration_ms: int | None = None
    #: With ``created``: the state of the run to create and its automatic reason.
    state: str | None = None
    reason: str | None = None

    def decided(self, result: str, message: str) -> "AutomaticDecision":
        self.result, self.message = result, message
        return self

    def settings_hold(self, connection: Session | Connection) -> bool:
        """Whether the settings this decision was made from are still the stored ones:
        automatic runs on, the measure and its three limits, the approval step and
        its authorization, the days between automatic runs, and the retention
        period (the job checks again in its insert's transaction: review B4,
        minor 1)."""
        return (
            read_automatic_settings(connection) == self.settings
            and read_settings(connection).retention_period_months == self.retention_period_months
        )

    def created_message(self, run_id: int) -> str:
        if self.state == HistoryArchiveRunState.PROPOSED:
            return (
                f"Run {run_id} was proposed: {self.reason}. It waits for a holder of Run history"
                " archival and purge to approve or dismiss it."
            )
        return (
            f"Run {run_id} was created APPROVED under the standing authorization of audit row"
            f" {self.settings.authorization_id}: {self.reason}. The host job works on it"
            " without approval."
        )

    def facts(self) -> dict[str, Any]:
        """The report's own keys (everything the evaluation read; null when not read)."""
        preview = self.preview
        last = self.last_automatic_run
        return {
            "automatic_runs": self.settings.enabled,
            "approval_required": self.settings.approval_required,
            "retention_period_months": self.retention_period_months,
            "cutoff": None if self.cutoff is None else iso(self.cutoff.instant),
            "cutoff_date": None if self.cutoff is None else self.cutoff.day.isoformat(),
            "interval_days": self.settings.interval_days,
            "open_run": (
                None
                if self.open_run is None
                else {"id": self.open_run[0], "state": self.open_run[1]}
            ),
            "last_automatic_run": (
                None
                if last is None
                else {"id": last.id, "state": last.state, "finished_at": iso(last.finished_at)}
            ),
            "next_due_at": iso(self.next_due_at),
            "standing_authorization": (
                None if self.authorization is None else self.authorization.document()
            ),
            "measure": None if self.measurement is None else self.measurement.document(),
            "preview": (
                None
                if preview is None
                else {
                    "components": preview.components,
                    "flows": preview.flows,
                    "movements": preview.movements,
                    "first_occurred_at": iso(preview.first_occurred_at),
                    "last_occurred_at": iso(preview.last_occurred_at),
                    "estimated_archive_bytes": preview.estimated_archive_bytes,
                    "duration_ms": self.preview_duration_ms,
                    "time_limit_seconds": self.time_limit_seconds,
                }
            ),
            "message": self.message,
        }


def evaluate_automatic(connection: Connection) -> AutomaticDecision:
    """Whether an automatic run is needed now, and which (reads only; the job creates it).

    The conditions are checked cheapest first, and the first that fails is
    the result: automatic runs off (``off``); no retention period
    (``no_retention_period``); a run open, PROPOSED included (``run_open``);
    the last automatic run (any final state) ended less than the days
    between automatic runs ago (``not_due``); with the approval step off, its
    standing authorization's User no longer active or no longer holding the
    key, or a run that failed under it until the approval setting is saved
    again (``authorization_invalid``); the measure not above its limit
    (``below_limit``); nothing eligible at the retention cutoff
    (``nothing_eligible``). Otherwise ``created``: a run PROPOSED while the
    approval step is required, APPROVED under the standing authorization
    otherwise, with the preview's counts and the automatic reason. The
    measure and the preview share the preview time limit
    (``EvaluationTimeoutError``).
    """
    automatic = read_automatic_settings(connection)
    settings = read_settings(connection)
    decision = AutomaticDecision(
        automatic, settings.retention_period_months, settings.preview_seconds
    )
    if not automatic.enabled:
        return decision.decided(
            "off", "Automatic runs are off (History archival & purge), so no run was created."
        )
    zone = site_zone()
    moment = now()
    retention = settings.retention_period_months
    latest = latest_cutoff_date(retention, site_date_of(moment, zone))
    if retention is None or latest is None:
        return decision.decided(
            "no_retention_period",
            "No retention period is set, so no history is eligible and no run was created.",
        )
    cutoff = decision.cutoff = Cutoff(latest, start_of_day(latest, zone))
    open_row = connection.execute(
        select(HistoryArchiveRun.id, HistoryArchiveRun.state)
        .where(HistoryArchiveRun.state.in_(HISTORY_ARCHIVE_OPEN_STATES))
        .order_by(HistoryArchiveRun.id)
        .limit(1)
    ).one_or_none()
    if open_row is not None:
        decision.open_run = (int(open_row.id), str(open_row.state))
        return decision.decided(
            "run_open",
            f"Run {open_row.id} is still open ({open_row.state}); no automatic run is created"
            " while a run is open.",
        )
    last = connection.execute(
        select(HistoryArchiveRun.id, HistoryArchiveRun.state, HistoryArchiveRun.finished_at)
        .where(
            HistoryArchiveRun.trigger == HistoryArchiveRunTrigger.AUTOMATIC,
            HistoryArchiveRun.finished_at.is_not(None),
        )
        .order_by(HistoryArchiveRun.finished_at.desc(), HistoryArchiveRun.id.desc())
        .limit(1)
    ).one_or_none()
    if last is not None:
        decision.last_automatic_run = LastAutomaticRun(
            int(last.id), str(last.state), last.finished_at
        )
        due = last.finished_at + datetime.timedelta(days=automatic.interval_days)
        decision.next_due_at = due
        if moment < due:
            return decision.decided(
                "not_due",
                f"The last automatic run, run {last.id}, ended at {iso(last.finished_at)}; the"
                f" next automatic run is due from {iso(due)} ({_days(automatic.interval_days)}"
                " between automatic runs).",
            )
    if not automatic.approval_required:
        decision.authorization = standing_authorization(connection, automatic.authorization_id)
        problem = (
            "The approval step of automatic runs is off without a standing authorization."
            if decision.authorization is None
            else decision.authorization.problem
        )
        if problem is not None:
            return decision.decided("authorization_invalid", problem)
    deadline = _Deadline(settings.preview_seconds)
    try:
        deadline.arm(connection)
        measurement = decision.measurement = measure(connection, automatic, cutoff, zone)
        if not measurement.passes:
            return decision.decided("below_limit", _below_limit_message(measurement, cutoff, zone))
        started = time.monotonic()
        preview = decision.preview = _counts(connection, cutoff.instant, deadline)
        decision.preview_duration_ms = int((time.monotonic() - started) * 1000)
    except ConflictError as exc:
        # The deadline ran out between two statements.
        raise EvaluationTimeoutError(_evaluation_timed_out(settings.preview_seconds)) from exc
    except DBAPIError as exc:
        if _is_timeout(exc):
            raise EvaluationTimeoutError(_evaluation_timed_out(settings.preview_seconds)) from exc
        raise
    if preview.flows == 0:
        return decision.decided(
            "nothing_eligible",
            f"Nothing is eligible before {cutoff.day.isoformat()} (retention"
            f" {_months(retention)}), so no run was created.",
        )
    decision.state = (
        HistoryArchiveRunState.PROPOSED
        if automatic.approval_required
        else HistoryArchiveRunState.APPROVED
    ).value
    decision.reason = automatic_reason(measurement, retention, cutoff, zone)
    return decision.decided("created", "")
