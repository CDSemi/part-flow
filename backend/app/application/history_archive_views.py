"""Viewing an archive in PartFlow (Phase 16, history archival design D16 and §9.3;
build step 5).

The application side of Open for viewing, Close viewing and the viewer page
of Administration → History archival & purge. A holder of Run history
archival and purge opens the archive of a finished run whose archive was
verified (a view, ``REQUESTED``); the host job (``history_archive_job``:
``load-view``) checks every file of the archive folder against its
``SHA256SUMS`` and the run's manifest SHA-256 and loads the files into the
viewer tables (``archive_view_*``, every row keyed by its view), never into
live history (``LOADING``, then ``OPEN``). A person closes it (``CLOSING``),
or the job does once the days of the setting have run out, and the job
removes its loaded rows (``close-view``: ``CLOSED``). Nothing here writes a
viewer row: the application role only reads them.

- Which runs can be opened: a finished run (a final state) whose archive
  was verified (``verified_at``, so its folder was published) — a run
  ``COMPLETED`` (``PURGED``, or ``EXPORT_ONLY`` from step 1),
  ``COMPLETED_WITH_SKIPS`` (``PURGED`` or ``NOTHING_PURGED``), and a run
  that was ``CANCELLED`` or ``FAILED`` after its verification. At most one
  view of a run is active (``REQUESTED``, ``LOADING``, ``OPEN``), and at most
  the setting's number of views is active at once: a request beyond it is
  refused (409); the requests are serialized by a transaction-level advisory
  lock, which the database's guard takes too.
- A ``REQUESTED`` view may be cancelled; an ``OPEN`` view closed (Close
  viewing). A view's days count from its opening, with the setting as it
  stands (``open_until``).
- The viewer reads an ``OPEN`` view only, in one ``REPEATABLE READ, READ
  ONLY`` snapshot: the search of its Movements (by Part Number, Work Order
  Number, flow, Area, Operation, Machine, Worker and a date range, newest
  first, paged by the id of the last Movement delivered as Tracking pages
  its history), one flow's whole history with its lineage and route, and the
  names to search by. Every name comes from the archive's ``context.json``
  (``archive_view_names``), so master data renamed or deleted since reads as
  it was at export.

Every change of state appends one ``HistoryArchiveView`` audit row in the
same transaction (``actor_user_id`` the signed-in User, ``metadata.source``
``web``; the job's rows ``maintenance``).
"""

import datetime
import json
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, NamedTuple, cast

from sqlalchemy import Connection, RowMapping, Table, func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from app.application import audit, history_archive, user_access
from app.application.common import MAX_ROW_ID, commit, flush, is_bindable_id
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.part_numbers import canonical_part_number
from app.application.transfers import ROUTE_DEVIATION_KEY
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    HistoryArchiveViewState,
    MovementType,
)
from app.infrastructure.models import (
    HISTORY_ARCHIVE_VIEW_ACTIVE_STATES,
    HISTORY_ARCHIVE_VIEW_FINAL_STATES,
    ApplicationPolicy,
    ArchiveViewFlow,
    ArchiveViewLineage,
    ArchiveViewMovement,
    ArchiveViewName,
    ArchiveViewRoute,
    HistoryArchiveRun,
    HistoryArchiveView,
)

_STATE = HistoryArchiveViewState
#: The views table, for the Core reads of the host job (a Session or a Connection).
VIEWS: Final = cast(Table, HistoryArchiveView.__table__)
#: The transaction-level advisory lock that serializes the requests of views
#: (the guard ``trg_history_archive_views_guard_mutation`` takes it too).
LIMIT_LOCK: Final = "partflow:history-archive-views"
#: The "constraint" the guard names when it refuses a request beyond the
#: setting of runs loaded at once (SQLSTATE 23514).
LIMIT_GUARD: Final = "trg_history_archive_views_guard_mutation"
ACTIVE_INDEX: Final = "uq_history_archive_views_run_active"
DEFAULT_VIEW_LIMIT: Final = 50
DEFAULT_MOVEMENT_LIMIT: Final = 50
MAX_MOVEMENT_LIMIT: Final = 200
#: How long a view may stay open past its days, or stay CLOSING, before the
#: host job's ``status`` reports that the job could not close it (the job
#: starts every 5 minutes; an hour is twelve starts).
CLOSE_GRACE: Final = datetime.timedelta(hours=1)
#: A Movement id (bigint) the viewer can look for.
MAX_MOVEMENT_ID: Final = 9_223_372_036_854_775_807
#: The views that may hold loaded rows (the job removes them).
LOADED_STATES: Final = (_STATE.LOADING, _STATE.OPEN, _STATE.CLOSING)
#: Where the movement's command context records its demand line and its User.
_CONTEXT_KEY: Final = "context"


class ViewSettings(NamedTuple):
    """The viewing settings of History archival & purge, as stored."""

    #: The days a loaded archive stays open from its opening.
    days: int
    #: How many runs may be loaded (or wait to be loaded) at once.
    max_loaded: int


def read_view_settings(connection: Session | Connection) -> ViewSettings:
    """The stored viewing settings (unlocked; a column select)."""
    row = connection.execute(
        select(
            ApplicationPolicy.archive_view_days, ApplicationPolicy.archive_view_max_loaded
        ).where(ApplicationPolicy.id == 1)
    ).one()
    return ViewSettings(int(row[0]), int(row[1]))


def open_until(opened_at: datetime.datetime | None, days: int) -> datetime.datetime | None:
    """When an open view's days run out (the setting as it stands; None before it opened)."""
    if opened_at is None:
        return None
    return opened_at + datetime.timedelta(days=days)


def expired(view: Mapping[Any, Any], days: int, moment: datetime.datetime) -> bool:
    """Whether an OPEN view's days have run out at ``moment``."""
    until = open_until(view["opened_at"], days)
    return view["state"] == _STATE.OPEN and until is not None and until <= moment


# ---------------------------------------------------------------------------
# The audit row and the job's summary
# ---------------------------------------------------------------------------

#: The audited facet of a view: every column (times as UTC text).
VIEW_COLUMNS: Final = tuple(column.name for column in HistoryArchiveView.__table__.columns)


def view_snapshot(values: Mapping[Any, Any]) -> dict[str, Any]:
    """The audit snapshot of a view: every column, times as UTC text."""
    return {
        column: history_archive.iso(value) if isinstance(value, datetime.datetime) else value
        for column, value in ((column, values[column]) for column in VIEW_COLUMNS)
    }


def view_values(view: HistoryArchiveView) -> dict[str, Any]:
    return {column: getattr(view, column) for column in VIEW_COLUMNS}


def view_summary(view: Mapping[Any, Any], settings: ViewSettings) -> dict[str, Any]:
    """A view as the host job reports it (``settings``, ``next``, ``status``, its commands)."""
    loaded = (
        None
        if view["opened_at"] is None
        else {
            "movements": view["loaded_movements"],
            "flows": view["loaded_flows"],
            "lineage_edges": view["loaded_lineage_edges"],
            "assigned_routes": view["loaded_assigned_routes"],
            "names": view["loaded_names"],
        }
    )
    return {
        "id": int(view["id"]),
        "run_id": int(view["run_id"]),
        "state": str(view["state"]),
        "requested_at": history_archive.iso(view["requested_at"]),
        "requested_by_user_id": view["requested_by_user_id"],
        "started_at": history_archive.iso(view["started_at"]),
        "opened_at": history_archive.iso(view["opened_at"]),
        "open_until": history_archive.iso(open_until(view["opened_at"], settings.days)),
        "loaded": loaded,
        "closing_started_at": history_archive.iso(view["closing_started_at"]),
        "closed_by_user_id": view["closed_by_user_id"],
        "finished_at": history_archive.iso(view["finished_at"]),
        "failure_reason": view["failure_reason"],
    }


class PendingView(NamedTuple):
    """A view the host job has work for, and that work."""

    view: RowMapping
    #: ``close_view`` (CLOSING, or OPEN past its days), ``load_view``
    #: (REQUESTED) or ``recover`` (LOADING: the next start's ``recover``
    #: removes what it loaded and fails it).
    action: str


def pending_views(
    connection: Session | Connection, moment: datetime.datetime
) -> tuple[list[PendingView], ViewSettings]:
    """Every view the host job has work for, closing work first (oldest view
    first), then loading, then the stale loads ``recover`` fails.

    Two indexed reads of small tables, so the cheap check of every 5-minute
    start can afford them (the application role may read both).
    """
    settings = read_view_settings(connection)
    rows = (
        connection.execute(
            select(VIEWS)
            .where(
                VIEWS.c.state.in_((_STATE.REQUESTED, _STATE.LOADING, _STATE.OPEN, _STATE.CLOSING))
            )
            .order_by(VIEWS.c.id)
        )
        .mappings()
        .all()
    )
    found: dict[str, list[PendingView]] = {"close_view": [], "load_view": [], "recover": []}
    for row in rows:
        if row["state"] == _STATE.CLOSING or expired(row, settings.days, moment):
            found["close_view"].append(PendingView(row, "close_view"))
        elif row["state"] == _STATE.REQUESTED:
            found["load_view"].append(PendingView(row, "load_view"))
        elif row["state"] == _STATE.LOADING:
            found["recover"].append(PendingView(row, "recover"))
    return found["close_view"] + found["load_view"] + found["recover"], settings


# ---------------------------------------------------------------------------
# Requests: open for viewing, cancel, close
# ---------------------------------------------------------------------------


def _audit_view(
    session: Session,
    view: HistoryArchiveView,
    *,
    before: dict[str, Any] | None,
    command: str,
    actor_user_id: int,
) -> None:
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED if before is None else AuditEventType.UPDATED,
        entity_type=AuditEntityType.HISTORY_ARCHIVE_VIEW,
        entity_id=str(view.id),
        before_data=before,
        after_data=view_snapshot(view_values(view)),
        actor_user_id=actor_user_id,
        metadata={"source": "web", "command": command},
    )


def _active_views(session: Session) -> list[HistoryArchiveView]:
    return list(
        session.scalars(
            select(HistoryArchiveView)
            .where(HistoryArchiveView.state.in_(HISTORY_ARCHIVE_VIEW_ACTIVE_STATES))
            .order_by(HistoryArchiveView.id)
        )
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _limit_message(max_loaded: int, active: Sequence[HistoryArchiveView], run_id: int) -> str:
    listed = ", ".join(f"view {view.id} of run {view.run_id} ({view.state})" for view in active)
    verb = "is" if len(active) == 1 else "are"
    return (
        f"At most {_plural(max_loaded, 'run')} may be loaded for viewing at once (History"
        f" archival & purge), and {len(active)} {verb} loaded or waiting: {listed}. Close a"
        f" view, or wait until it is closed, before opening run {run_id}."
    )


_LIMIT_RACE_MESSAGE: Final = (
    "History archival & purge allows no more runs loaded for viewing at once now. Close a view"
    " before opening another run."
)


def request_view(session: Session, *, run_id: int, actor_user_id: int) -> HistoryArchiveView:
    """Open a run's archive for viewing (REQUESTED); the host job loads it.

    Refused for an unknown run (404), a run that is not finished or whose
    archive was never verified, a run already open for viewing (or waiting
    to be loaded), and when the setting's number of views is active already
    (409). The requests are serialized by an advisory lock taken first, which
    the database's guard takes again.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": LIMIT_LOCK}
    )
    run = session.get(HistoryArchiveRun, run_id) if is_bindable_id(run_id) else None
    if run is None:
        raise NotFoundError(f"There is no archival run {run_id}.")
    if not history_archive.is_final(run.state):
        raise ConflictError(
            f"Run {run.id} is {run.state} and not finished: only the archive of a finished run"
            " can be opened for viewing."
        )
    if run.verified_at is None:
        raise ConflictError(
            f"Run {run.id} ended {run.state} before its archive was verified, so it has no"
            " archive to view."
        )
    active = _active_views(session)
    same = next((view for view in active if view.run_id == run.id), None)
    if same is not None:
        raise ConflictError(
            f"Run {run.id} is already open for viewing, or waiting to be loaded (view"
            f" {same.id}, {same.state})."
        )
    settings = read_view_settings(session)
    if len(active) >= settings.max_loaded:
        raise ConflictError(_limit_message(settings.max_loaded, active, run.id))
    view = HistoryArchiveView(
        run_id=run.id, state=_STATE.REQUESTED, requested_by_user_id=actor_user_id
    )
    session.add(view)
    conflicts = {
        ACTIVE_INDEX: f"Run {run.id} is already open for viewing, or waiting to be loaded.",
        LIMIT_GUARD: _LIMIT_RACE_MESSAGE,
    }
    flush(session, conflicts)
    session.refresh(view)
    _audit_view(session, view, before=None, command="open", actor_user_id=actor_user_id)
    commit(session, conflicts)
    return view


def _locked_view(session: Session, view_id: int) -> HistoryArchiveView:
    view = (
        session.get(HistoryArchiveView, view_id, with_for_update=True, populate_existing=True)
        if is_bindable_id(view_id)
        else None
    )
    if view is None:
        raise NotFoundError(f"There is no archive view {view_id}.")
    return view


def cancel_view(session: Session, view_id: int, *, actor_user_id: int) -> HistoryArchiveView:
    """Cancel a REQUESTED view; cancelling a cancelled view changes nothing.

    A LOADING view is the host job's; an OPEN view is closed instead; a
    finished view never changes.
    """
    view = _locked_view(session, view_id)
    if view.state == _STATE.CANCELLED:
        return view
    if view.state == _STATE.LOADING:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} is being loaded on the server now and cannot be"
            " cancelled. Close it once it is open."
        )
    if view.state == _STATE.OPEN:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} is open (OPEN): close it instead of cancelling"
            " it."
        )
    if view.state == _STATE.CLOSING:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} is being closed (CLOSING) and cannot be"
            " cancelled."
        )
    if view.state != _STATE.REQUESTED:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} is finished ({view.state}) and cannot be"
            " cancelled."
        )
    before = view_snapshot(view_values(view))
    view.state = _STATE.CANCELLED
    view.finished_at = func.now()
    view.cancelled_by_user_id = actor_user_id
    flush(session, {})
    session.refresh(view)
    _audit_view(session, view, before=before, command="cancel", actor_user_id=actor_user_id)
    commit(session, {})
    return view


def close_view(session: Session, view_id: int, *, actor_user_id: int) -> HistoryArchiveView:
    """Close an OPEN view (Close viewing): CLOSING; the host job removes its rows.

    Closing a view that is closing or closed changes nothing. A REQUESTED
    view is cancelled instead, and a LOADING view is the host job's until it
    is open.
    """
    view = _locked_view(session, view_id)
    if view.state in (_STATE.CLOSING, _STATE.CLOSED):
        return view
    if view.state == _STATE.REQUESTED:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} waits to be loaded (REQUESTED): cancel it"
            " instead of closing it."
        )
    if view.state == _STATE.LOADING:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} is being loaded on the server now. Close it once"
            " it is open."
        )
    if view.state != _STATE.OPEN:
        raise ConflictError(
            f"View {view.id} of run {view.run_id} is finished ({view.state}) and cannot be closed."
        )
    before = view_snapshot(view_values(view))
    view.state = _STATE.CLOSING
    view.closing_started_at = func.now()
    view.closed_by_user_id = actor_user_id
    flush(session, {})
    session.refresh(view)
    _audit_view(session, view, before=before, command="close", actor_user_id=actor_user_id)
    commit(session, {})
    return view


# ---------------------------------------------------------------------------
# The views list (Administration) and the run's view
# ---------------------------------------------------------------------------


class ViewUser(NamedTuple):
    user_id: int
    display_name: str


class ViewRun(NamedTuple):
    """What the view's run exported: what a load brings into the database."""

    archive_name: str | None
    movements: int
    flows: int
    lineage_edges: int
    assigned_routes: int
    archive_bytes: int


@dataclass(frozen=True)
class ViewEntry:
    view: HistoryArchiveView
    run: ViewRun
    requested_by: ViewUser
    cancelled_by: ViewUser | None
    closed_by: ViewUser | None
    open_until: datetime.datetime | None


def _entries(session: Session, views: Sequence[HistoryArchiveView]) -> list[ViewEntry]:
    settings = read_view_settings(session)
    runs = {
        int(row.id): ViewRun(
            row.archive_name,
            int(row.exported_movements or 0),
            int(row.exported_flows or 0),
            int(row.exported_lineage_edges or 0),
            int(row.exported_assigned_routes or 0),
            int(row.archive_bytes or 0),
        )
        for row in session.execute(
            select(
                HistoryArchiveRun.id,
                HistoryArchiveRun.archive_name,
                HistoryArchiveRun.exported_movements,
                HistoryArchiveRun.exported_flows,
                HistoryArchiveRun.exported_lineage_edges,
                HistoryArchiveRun.exported_assigned_routes,
                HistoryArchiveRun.archive_bytes,
            ).where(HistoryArchiveRun.id.in_({view.run_id for view in views}))
        )
    }
    ids = {
        user_id
        for view in views
        for user_id in (
            view.requested_by_user_id,
            view.cancelled_by_user_id,
            view.closed_by_user_id,
        )
        if user_id is not None
    }
    users = {
        user_id: ViewUser(ref.id, ref.display_name)
        for user_id, ref in user_access.user_refs(session, ids).items()
    }

    def user(user_id: int | None) -> ViewUser | None:
        if user_id is None:
            return None
        return users.get(user_id, ViewUser(user_id, f"user {user_id}"))

    return [
        ViewEntry(
            view,
            runs[view.run_id],
            user(view.requested_by_user_id) or ViewUser(view.requested_by_user_id, ""),
            user(view.cancelled_by_user_id),
            user(view.closed_by_user_id),
            open_until(view.opened_at, settings.days),
        )
        for view in views
    ]


def view_entry(session: Session, view: HistoryArchiveView) -> ViewEntry:
    return _entries(session, [view])[0]


def list_views(session: Session, *, limit: int = DEFAULT_VIEW_LIMIT) -> list[ViewEntry]:
    """The newest views first."""
    if not 1 <= limit <= 500:
        raise InvalidInputError("limit must be a whole number from 1 to 500.")
    views = list(
        session.scalars(
            select(HistoryArchiveView).order_by(HistoryArchiveView.id.desc()).limit(limit)
        )
    )
    return _entries(session, views)


# ---------------------------------------------------------------------------
# The viewer: reads of an OPEN view, in one snapshot
# ---------------------------------------------------------------------------


class NameRef(NamedTuple):
    """A named record of the archive (Area, Machine, Worker) as it was at export."""

    id: int
    #: None when the archive does not name it.
    name: str | None


class OperationRef(NamedTuple):
    id: int
    code: str | None
    name: str | None


class UserRef(NamedTuple):
    user_id: int
    display_name: str | None


class DemandRef(NamedTuple):
    work_order_demand_id: int
    work_order_id: int | None
    work_order_number: str | None
    request_type: str | None


class WorkOrderRef(NamedTuple):
    work_order_id: int
    work_order_number: str | None


class LineageRef(NamedTuple):
    parent_flow_id: int
    child_flow_id: int
    relation: str
    #: The split or merge was undone.
    void: bool


class StepRef(NamedTuple):
    id: int
    sequence: int | None


class ViewerMovement(NamedTuple):
    """One archived Movement with the names it carried at export."""

    id: int
    quantity_flow_id: int
    part_number: str
    movement_type: str
    quantity: int
    from_area: NameRef | None
    to_area: NameRef
    operation: OperationRef
    source_machine: NameRef | None
    destination_machine: NameRef | None
    station_id: str | None
    worker: NameRef | None
    occurred_at: datetime.datetime
    device_event_id: str
    command_sequence: int
    movement_reason: str | None
    reason: str | None
    reverses_movement_id: int | None
    reversed_by_movement_id: int | None
    assigned_route_step: StepRef | None
    route_deviation: dict[str, Any] | None
    lineage: list[LineageRef]
    demand: DemandRef | None
    actor_user: UserRef | None


class MovementFilters(NamedTuple):
    """The search of a view's Movements; None matches everything."""

    part_number: object = None
    work_order_number: object = None
    quantity_flow_id: int | None = None
    area_id: int | None = None
    operation_id: int | None = None
    machine_id: int | None = None
    worker_id: int | None = None
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None


class MovementPage(NamedTuple):
    movements: list[ViewerMovement]
    #: The Movements of the view that match the filters.
    total: int
    has_more: bool
    #: Pass as ``before`` for the next (older) page; None on the last page.
    next_before_movement_id: int | None


class RouteStepView(NamedTuple):
    id: int
    sequence: int
    area: NameRef
    operation: OperationRef | None
    preferred_machine: NameRef | None
    expected_duration: str | None
    instructions: str | None


class RouteView(NamedTuple):
    assigned_route_id: int
    source_route_template_id: int | None
    created_at: datetime.datetime | None
    steps: list[RouteStepView]


class FlowLink(NamedTuple):
    quantity_flow_id: int
    relation: str
    void: bool


class ViewerFlow(NamedTuple):
    """One archived flow with its carry-forward record, lineage and route."""

    id: int
    part_number: str
    #: The Part Number's name at export (None without a master).
    part_number_name: str | None
    component_id: int
    quantity: int
    status: str
    route_mode: str
    created_at: datetime.datetime
    closed_at: datetime.datetime | None
    record: dict[str, Any]
    stocked_area: NameRef | None
    scrapped_area: NameRef | None
    demand: DemandRef | None
    work_orders: list[WorkOrderRef]
    parents: list[FlowLink]
    children: list[FlowLink]
    route: RouteView | None
    movements: list[ViewerMovement]


class ViewNames(NamedTuple):
    """The names a view's Movements can be searched by, as they were at export."""

    areas: list[NameRef]
    operations: list[OperationRef]
    machines: list[NameRef]
    workers: list[NameRef]


@dataclass(frozen=True)
class _ReadView:
    id: int
    run_id: int


def _reader(session: Session) -> Session:
    """A REPEATABLE READ, READ ONLY session on the request's engine: one snapshot."""
    reader = Session(session.get_bind())
    reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    return reader


def _open_view(reader: Session, view_id: int) -> _ReadView:
    """The view, which must be OPEN to be read."""
    row = (
        reader.execute(
            select(
                HistoryArchiveView.id, HistoryArchiveView.run_id, HistoryArchiveView.state
            ).where(HistoryArchiveView.id == view_id)
        ).one_or_none()
        if is_bindable_id(view_id)
        else None
    )
    if row is None:
        raise NotFoundError(f"There is no archive view {view_id}.")
    state = str(row.state)
    if state == _STATE.OPEN:
        return _ReadView(int(row.id), int(row.run_id))
    if state in (_STATE.REQUESTED, _STATE.LOADING):
        raise ConflictError(
            f"View {row.id} of run {row.run_id} is not loaded yet ({state}): the host job loads it"
            " within minutes of the request."
        )
    if state == _STATE.CLOSING:
        raise ConflictError(
            f"View {row.id} of run {row.run_id} is closed (CLOSING): the host job removes its"
            " loaded rows at its next start. Open the run for viewing again to read it."
        )
    if state == _STATE.CLOSED:
        raise ConflictError(
            f"View {row.id} of run {row.run_id} is closed (CLOSED): its loaded rows are removed."
            " Open the run for viewing again to read it."
        )
    raise ConflictError(
        f"View {row.id} of run {row.run_id} ended {state} and holds nothing to read. Open the run"
        " for viewing again."
    )


@dataclass
class _Names:
    """The names of one view, read once per page for the keys it names."""

    view_id: int
    wanted: dict[str, set[str]] = field(default_factory=dict)
    found: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def want(self, kind: str, key: object) -> None:
        if key is None or isinstance(key, bool):
            return
        self.wanted.setdefault(kind, set()).add(str(key))

    def read(self, reader: Session) -> None:
        pairs = [(kind, key) for kind, keys in self.wanted.items() for key in keys]
        if not pairs:
            return
        for kind, key, value in reader.execute(
            select(ArchiveViewName.kind, ArchiveViewName.key, ArchiveViewName.value).where(
                ArchiveViewName.view_id == self.view_id,
                tuple_(ArchiveViewName.kind, ArchiveViewName.key).in_(pairs),
            )
        ):
            self.found[(str(kind), str(key))] = dict(value)
        self.wanted = {}

    def value(self, kind: str, key: object) -> dict[str, Any] | None:
        if key is None:
            return None
        return self.found.get((kind, str(key)))

    def ref(self, kind: str, key: int | None) -> NameRef | None:
        if key is None:
            return None
        value = self.value(kind, key) or {}
        name = value.get("name")
        return NameRef(int(key), None if name is None else str(name))

    def operation(self, key: int | None) -> OperationRef | None:
        if key is None:
            return None
        value = self.value("operations", key) or {}
        code, name = value.get("code"), value.get("name")
        return OperationRef(
            int(key), None if code is None else str(code), None if name is None else str(name)
        )

    def user(self, key: int | None) -> UserRef | None:
        if key is None:
            return None
        value = self.value("users", key) or {}
        name = value.get("display_name")
        return UserRef(int(key), None if name is None else str(name))

    def work_order(self, key: int | None) -> WorkOrderRef | None:
        if key is None:
            return None
        value = self.value("work_orders", key) or {}
        number = value.get("work_order_number")
        return WorkOrderRef(int(key), None if number is None else str(number))

    def demand(self, key: int | None) -> DemandRef | None:
        if key is None:
            return None
        value = self.value("work_order_demands", key) or {}
        work_order_id = _int_or_none(value.get("work_order_id"))
        work_order = self.work_order(work_order_id)
        request_type = value.get("request_type")
        return DemandRef(
            int(key),
            work_order_id,
            None if work_order is None else work_order.work_order_number,
            None if request_type is None else str(request_type),
        )


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _context_value(movement: ArchiveViewMovement, key: str) -> int | None:
    recorded = movement.metadata_ or {}
    context = recorded.get(_CONTEXT_KEY)
    return _int_or_none(context.get(key)) if isinstance(context, dict) else None


def _decorate(
    reader: Session, view_id: int, movements: Sequence[ArchiveViewMovement]
) -> list[ViewerMovement]:
    """The page's Movements with what Tracking shows beside each: names, the
    REVERSED row of an undone original, the lineage of a split or merge, the
    demand line of a release, the route step and the acting User."""
    if not movements:
        return []
    ids = [movement.id for movement in movements]
    reversed_by = {
        int(original): int(reversal)
        for reversal, original in reader.execute(
            select(ArchiveViewMovement.id, ArchiveViewMovement.reverses_movement_id).where(
                ArchiveViewMovement.view_id == view_id,
                ArchiveViewMovement.reverses_movement_id.in_(ids),
            )
        )
    }
    lineage_events = {
        movement.device_event_id
        for movement in movements
        if movement.movement_type in (MovementType.SPLIT, MovementType.MERGED)
    }
    edges: dict[str, list[ArchiveViewLineage]] = {}
    if lineage_events:
        for edge in reader.scalars(
            select(ArchiveViewLineage)
            .where(
                ArchiveViewLineage.view_id == view_id,
                ArchiveViewLineage.device_event_id.in_(lineage_events),
            )
            .order_by(ArchiveViewLineage.id)
        ):
            edges.setdefault(edge.device_event_id, []).append(edge)
    steps = _route_steps(
        reader,
        view_id,
        {movement.quantity_flow_id for movement in movements if movement.assigned_route_step_id},
    )
    names = _Names(view_id)
    for movement in movements:
        for area in (movement.from_area_id, movement.to_area_id):
            names.want("areas", area)
        names.want("operations", movement.operation_id)
        for machine in (movement.source_machine_id, movement.destination_machine_id):
            names.want("machines", machine)
        names.want("workers", movement.worker_id)
        names.want("users", _context_value(movement, "actor_user_id"))
        if movement.movement_type == MovementType.RECEIVED:
            names.want("work_order_demands", _context_value(movement, "work_order_demand_id"))
    names.read(reader)
    # The Work Orders of the demand lines just read.
    for movement in movements:
        demand = names.value("work_order_demands", _context_value(movement, "work_order_demand_id"))
        if movement.movement_type == MovementType.RECEIVED and demand is not None:
            names.want("work_orders", _int_or_none(demand.get("work_order_id")))
    names.read(reader)
    entries: list[ViewerMovement] = []
    for movement in movements:
        to_area = names.ref("areas", movement.to_area_id)
        operation = names.operation(movement.operation_id)
        recorded = movement.metadata_ or {}
        deviation = recorded.get(ROUTE_DEVIATION_KEY)
        step_id = movement.assigned_route_step_id
        entries.append(
            ViewerMovement(
                id=movement.id,
                quantity_flow_id=movement.quantity_flow_id,
                part_number=movement.part_number,
                movement_type=movement.movement_type,
                quantity=movement.quantity,
                from_area=names.ref("areas", movement.from_area_id),
                to_area=to_area if to_area is not None else NameRef(movement.to_area_id, None),
                operation=(
                    operation
                    if operation is not None
                    else OperationRef(movement.operation_id, None, None)
                ),
                source_machine=names.ref("machines", movement.source_machine_id),
                destination_machine=names.ref("machines", movement.destination_machine_id),
                station_id=movement.station_id,
                worker=names.ref("workers", movement.worker_id),
                occurred_at=movement.occurred_at,
                device_event_id=movement.device_event_id,
                command_sequence=movement.command_sequence,
                movement_reason=movement.movement_reason,
                reason=movement.reason,
                reverses_movement_id=movement.reverses_movement_id,
                reversed_by_movement_id=reversed_by.get(movement.id),
                assigned_route_step=(
                    None if step_id is None else StepRef(step_id, steps.get(step_id))
                ),
                route_deviation=dict(deviation) if isinstance(deviation, dict) else None,
                lineage=(
                    [
                        LineageRef(
                            edge.parent_flow_id, edge.child_flow_id, edge.relation, edge.void
                        )
                        for edge in edges.get(movement.device_event_id, [])
                        if movement.quantity_flow_id in (edge.parent_flow_id, edge.child_flow_id)
                    ]
                    if movement.movement_type in (MovementType.SPLIT, MovementType.MERGED)
                    else []
                ),
                demand=(
                    names.demand(_context_value(movement, "work_order_demand_id"))
                    if movement.movement_type == MovementType.RECEIVED
                    else None
                ),
                actor_user=names.user(_context_value(movement, "actor_user_id")),
            )
        )
    return entries


def _route_steps(reader: Session, view_id: int, flow_ids: Collection[int]) -> dict[int, int]:
    """The sequence of each step of the Assigned Routes of these flows (step id → sequence)."""
    if not flow_ids:
        return {}
    sequences: dict[int, int] = {}
    for steps in reader.scalars(
        select(ArchiveViewRoute.steps).where(
            ArchiveViewRoute.view_id == view_id,
            ArchiveViewRoute.assigned_route_id.in_(
                select(ArchiveViewFlow.assigned_route_id).where(
                    ArchiveViewFlow.view_id == view_id,
                    ArchiveViewFlow.flow_id.in_(set(flow_ids)),
                )
            ),
        )
    ):
        for step in steps:
            step_id, sequence = _int_or_none(step.get("id")), _int_or_none(step.get("sequence"))
            if step_id is not None and sequence is not None:
                sequences[step_id] = sequence
    return sequences


def _bounded_id(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if not is_bindable_id(value):
        raise InvalidInputError(f"{label} must be a whole number from 1 to {MAX_ROW_ID}.")
    return value


def _work_order_number(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise InvalidInputError("A Work Order Number must not be blank or contain a NUL character.")
    return value


def _conditions(reader: Session, view_id: int, filters: MovementFilters) -> list[Any] | None:
    """The WHERE terms of a search (None: nothing can match)."""
    movement = ArchiveViewMovement
    terms: list[Any] = [movement.view_id == view_id]
    if filters.part_number is not None:
        terms.append(movement.part_number == canonical_part_number(filters.part_number))
    flow = _bounded_id(filters.quantity_flow_id, "The flow")
    if flow is not None:
        terms.append(movement.quantity_flow_id == flow)
    area = _bounded_id(filters.area_id, "The Area")
    if area is not None:
        terms.append(or_(movement.from_area_id == area, movement.to_area_id == area))
    operation = _bounded_id(filters.operation_id, "The Operation")
    if operation is not None:
        terms.append(movement.operation_id == operation)
    machine = _bounded_id(filters.machine_id, "The Machine")
    if machine is not None:
        terms.append(
            or_(movement.source_machine_id == machine, movement.destination_machine_id == machine)
        )
    worker = _bounded_id(filters.worker_id, "The Worker")
    if worker is not None:
        terms.append(movement.worker_id == worker)
    if (
        filters.date_from is not None
        and filters.date_to is not None
        and filters.date_from > filters.date_to
    ):
        raise InvalidInputError("The first day of the search must not be after its last day.")
    zone = history_archive.site_zone()
    if filters.date_from is not None:
        terms.append(movement.occurred_at >= history_archive.start_of_day(filters.date_from, zone))
    if filters.date_to is not None:
        terms.append(
            movement.occurred_at
            < history_archive.start_of_day(filters.date_to + datetime.timedelta(days=1), zone)
        )
    number = _work_order_number(filters.work_order_number)
    if number is not None:
        work_orders = [
            int(key)
            for key in reader.scalars(
                select(ArchiveViewName.key).where(
                    ArchiveViewName.view_id == view_id,
                    ArchiveViewName.kind == "work_orders",
                    ArchiveViewName.value["work_order_number"].astext == number,
                )
            )
        ]
        if not work_orders:
            return None
        # Two steps, never a join: the flows first (their Work Order index), then
        # their Movements through the flow index. A view loaded minutes ago has no
        # statistics of its own yet, and a join planned on stale ones can scan
        # every Movement of the view once per flow (the step 5 rehearsal).
        flows = list(
            reader.scalars(
                select(ArchiveViewFlow.flow_id).where(
                    ArchiveViewFlow.view_id == view_id,
                    ArchiveViewFlow.work_order_ids.overlap(work_orders),
                )
            )
        )
        if not flows:
            return None
        terms.append(movement.quantity_flow_id.in_(flows))
    return terms


def search_movements(
    session: Session,
    view_id: int,
    filters: MovementFilters,
    *,
    before_movement_id: int | None = None,
    limit: int = DEFAULT_MOVEMENT_LIMIT,
) -> MovementPage:
    """One page of an open view's Movements matching the filters, newest first.

    Reverse-chronological ``(occurred_at DESC, id DESC)`` with keyset paging
    on that order, as Tracking pages a PN's history: ``before_movement_id``
    names the last Movement a page delivered (of this view: 404 otherwise),
    and the server resolves its time. Area matches either end of a Movement
    (from or to), Machine either machine; the dates are site-calendar days,
    both included; a Work Order Number finds the Movements of the flows
    released for that Work Order and of the flows split or merged from them.
    """
    if not 1 <= limit <= MAX_MOVEMENT_LIMIT:
        raise InvalidInputError(f"limit must be a whole number from 1 to {MAX_MOVEMENT_LIMIT}.")
    with _reader(session) as reader:
        try:
            view = _open_view(reader, view_id)
            terms = _conditions(reader, view.id, filters)
            cursor = None
            if before_movement_id is not None:
                occurred_at = (
                    reader.scalar(
                        select(ArchiveViewMovement.occurred_at).where(
                            ArchiveViewMovement.view_id == view.id,
                            ArchiveViewMovement.id == before_movement_id,
                        )
                    )
                    if 0 < before_movement_id <= MAX_MOVEMENT_ID
                    else None
                )
                if occurred_at is None:
                    raise NotFoundError(f"Movement {before_movement_id} is not in view {view.id}.")
                cursor = (occurred_at, before_movement_id)
            if terms is None:
                return MovementPage([], 0, False, None)
            total = int(
                reader.scalar(select(func.count()).select_from(ArchiveViewMovement).where(*terms))
                or 0
            )
            query = select(ArchiveViewMovement).where(*terms)
            if cursor is not None:
                query = query.where(
                    tuple_(ArchiveViewMovement.occurred_at, ArchiveViewMovement.id) < cursor
                )
            rows = list(
                reader.scalars(
                    query.order_by(
                        ArchiveViewMovement.occurred_at.desc(), ArchiveViewMovement.id.desc()
                    ).limit(limit + 1)
                )
            )
            has_more = len(rows) > limit
            rows = rows[:limit]
            return MovementPage(
                _decorate(reader, view.id, rows),
                total,
                has_more,
                rows[-1].id if has_more and rows else None,
            )
        finally:
            reader.rollback()


def flow_history(session: Session, view_id: int, flow_id: int) -> ViewerFlow:
    """One archived flow of an open view: its whole history (newest first), its
    carry-forward record, its lineage (parents and children, void ones marked)
    and its Assigned Route snapshot, with the names as they were at export."""
    with _reader(session) as reader:
        try:
            view = _open_view(reader, view_id)
            flow = (
                reader.get(ArchiveViewFlow, (view.id, flow_id)) if is_bindable_id(flow_id) else None
            )
            if flow is None:
                raise NotFoundError(f"Quantity Flow {flow_id} is not in view {view.id}.")
            movements = list(
                reader.scalars(
                    select(ArchiveViewMovement)
                    .where(
                        ArchiveViewMovement.view_id == view.id,
                        ArchiveViewMovement.quantity_flow_id == flow.flow_id,
                    )
                    .order_by(ArchiveViewMovement.occurred_at.desc(), ArchiveViewMovement.id.desc())
                )
            )
            edges = list(
                reader.scalars(
                    select(ArchiveViewLineage)
                    .where(
                        ArchiveViewLineage.view_id == view.id,
                        or_(
                            ArchiveViewLineage.parent_flow_id == flow.flow_id,
                            ArchiveViewLineage.child_flow_id == flow.flow_id,
                        ),
                    )
                    .order_by(ArchiveViewLineage.id)
                )
            )
            route = (
                None
                if flow.assigned_route_id is None
                else reader.get(ArchiveViewRoute, (view.id, flow.assigned_route_id))
            )
            names = _Names(view.id)
            record = dict(flow.record)
            names.want("part_numbers", flow.part_number)
            names.want("areas", _int_or_none(record.get("stocked_area_id")))
            names.want("areas", _int_or_none(record.get("scrapped_area_id")))
            names.want("work_order_demands", flow.received_demand_id)
            for work_order in flow.work_order_ids:
                names.want("work_orders", work_order)
            steps = [] if route is None else list(route.steps)
            for step in steps:
                names.want("areas", _int_or_none(step.get("area_id")))
                names.want("operations", _int_or_none(step.get("operation_id")))
                names.want("machines", _int_or_none(step.get("preferred_machine_id")))
            names.read(reader)
            demand_row = names.value("work_order_demands", flow.received_demand_id)
            if demand_row is not None:
                names.want("work_orders", _int_or_none(demand_row.get("work_order_id")))
                names.read(reader)
            master = names.value("part_numbers", flow.part_number) or {}
            return ViewerFlow(
                id=flow.flow_id,
                part_number=flow.part_number,
                part_number_name=None if master.get("name") is None else str(master["name"]),
                component_id=flow.component_id,
                quantity=flow.quantity,
                status=flow.status,
                route_mode=flow.route_mode,
                created_at=flow.created_at,
                closed_at=flow.closed_at,
                record=record,
                stocked_area=names.ref("areas", _int_or_none(record.get("stocked_area_id"))),
                scrapped_area=names.ref("areas", _int_or_none(record.get("scrapped_area_id"))),
                demand=names.demand(flow.received_demand_id),
                work_orders=[
                    ref
                    for ref in (names.work_order(work_order) for work_order in flow.work_order_ids)
                    if ref is not None
                ],
                parents=[
                    FlowLink(edge.parent_flow_id, edge.relation, edge.void)
                    for edge in edges
                    if edge.child_flow_id == flow.flow_id
                ],
                children=[
                    FlowLink(edge.child_flow_id, edge.relation, edge.void)
                    for edge in edges
                    if edge.parent_flow_id == flow.flow_id
                ],
                route=None if route is None else _route_view(route, steps, names),
                movements=_decorate(reader, view.id, movements),
            )
        finally:
            reader.rollback()


def _route_view(
    route: ArchiveViewRoute, steps: Iterable[dict[str, Any]], names: _Names
) -> RouteView:
    views: list[RouteStepView] = []
    for step in steps:
        area_id = _int_or_none(step.get("area_id"))
        step_id, sequence = _int_or_none(step.get("id")), _int_or_none(step.get("sequence"))
        if area_id is None or step_id is None or sequence is None:
            continue
        duration, instructions = step.get("expected_duration"), step.get("instructions")
        views.append(
            RouteStepView(
                id=step_id,
                sequence=sequence,
                area=names.ref("areas", area_id) or NameRef(area_id, None),
                operation=names.operation(_int_or_none(step.get("operation_id"))),
                preferred_machine=names.ref(
                    "machines", _int_or_none(step.get("preferred_machine_id"))
                ),
                expected_duration=None if duration is None else str(duration),
                instructions=None if instructions is None else str(instructions),
            )
        )
    return RouteView(
        route.assigned_route_id, route.source_route_template_id, route.created_at, views
    )


def view_names(session: Session, view_id: int) -> ViewNames:
    """The Areas, Operations, Machines and Workers an open view names, as they were at export."""
    with _reader(session) as reader:
        try:
            view = _open_view(reader, view_id)
            sections: dict[str, list[tuple[int, dict[str, Any]]]] = {
                "areas": [],
                "operations": [],
                "machines": [],
                "workers": [],
            }
            for kind, key, value in reader.execute(
                select(ArchiveViewName.kind, ArchiveViewName.key, ArchiveViewName.value).where(
                    ArchiveViewName.view_id == view.id,
                    ArchiveViewName.kind.in_(tuple(sections)),
                )
            ):
                identifier = _int_or_none(key)
                if identifier is not None:
                    sections[str(kind)].append((identifier, dict(value)))

            def named(kind: str) -> list[NameRef]:
                return [
                    NameRef(identifier, None if value.get("name") is None else str(value["name"]))
                    for identifier, value in sorted(sections[kind], key=lambda item: item[0])
                ]

            return ViewNames(
                areas=named("areas"),
                operations=[
                    OperationRef(
                        identifier,
                        None if value.get("code") is None else str(value["code"]),
                        None if value.get("name") is None else str(value["name"]),
                    )
                    for identifier, value in sorted(
                        sections["operations"], key=lambda item: item[0]
                    )
                ],
                machines=named("machines"),
                workers=named("workers"),
            )
        finally:
            reader.rollback()


# ---------------------------------------------------------------------------
# The host job's reads (status)
# ---------------------------------------------------------------------------


def unclosed(view: Mapping[Any, Any], days: int, moment: datetime.datetime) -> bool:
    """Whether the job could not close a view: CLOSING longer than ``CLOSE_GRACE``, or
    OPEN that long past its days."""
    if view["state"] == _STATE.CLOSING:
        started = view["closing_started_at"]
        return started is not None and moment - started > CLOSE_GRACE
    until = open_until(view["opened_at"], days)
    return view["state"] == _STATE.OPEN and until is not None and moment - until > CLOSE_GRACE


def is_final(state: str) -> bool:
    return state in HISTORY_ARCHIVE_VIEW_FINAL_STATES


def json_text(value: object) -> str:
    """A value as compact JSON text (a jsonb column of a COPY row)."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
