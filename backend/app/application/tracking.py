"""PN Tracking read model (Phase 11 — PROJECT_PROFILE §21 Tracking, GUI_DESIGN §7).

The primary management interface: a PN-centric list every Manager can
search and filter, and one read-only detail per Part Number. Both are
derived from the current-position projection and the immutable
Movement history — nothing here writes, and no value is a stored
counter. The derivations are the ones every other Phase 11 read model
already uses, so PN Tracking can never disagree with the Production
Board, the Area Board or the Scan Station about a quantity:

- **Current quantity by Area / Machine** is `production_board.flow_positions`
  grouped by `production_board.group_locations` — the branch-aware
  monitoring derivation (`projections.effective_positions`): the entry
  time of the OLDEST lineage branch, a Machine only where every branch
  agrees, the holding state from the effective position-bearing
  Movement and the Area mode.
- **Stocked** and **scrapped** quantity are the Σ of the effective
  `STOCKED` / `SCRAPPED` Movements of the PN (net of reversed scraps);
  **introduced** quantity the Σ of its effective `RECEIVED` and
  `QUANTITY_ADJUSTED` Movements — the terms of the §11 reconciliation
  `introduced = active + stocked + scrapped`.
- **Demand** is the PN's OPEN Work Order Demands in the canonical order
  (`allocations.open_demand_context` — the one monitoring demand
  context), each with its released quantity, its active allocation and
  its remaining shortage. Business demand is reported beside the
  physical quantity and never conflated with it.
- **Quantity Flows** keep their lineage: the effective SPLIT / MERGED
  edges name every parent and child, a consumed or closed flow stays
  listed with its status, and an undone command's edges are void.
- **Routes**: a `PLANNED` flow shows its immutable AssignedRoute
  snapshot with each step judged done / current / future from the
  flow's last known step (`lineage.last_known_step_id` — reversed
  Movements never count) and every confirmed deviation read back from
  the `TRANSFERRED` / `STOCKED` Movement that recorded it. Every flow
  also carries its **actual route trace** derived from Movement
  history: the Areas its quantity arrived in, in order — repeated
  Areas preserved, a Repair transfer marked explicitly, a split child
  inheriting the trace of its single parent up to the split (a merge
  result starts at the merge — its sources keep their own traces), and
  reversed arrivals excluded. `AREA_COMPLETED` never extends a trace:
  completion happens inside the source Area.
- **Movement history** is every Movement of the PN, newest first,
  paged on the append-only id (`before_movement_id`) so a long history
  is never loaded whole; a reversed original is reported together with
  the `REVERSED` row that undid it, and neither is ever hidden.

Derived status of a PN: ``ACTIVE`` while any quantity is in production;
otherwise ``COMPLETED`` when no open demand remains (only history);
otherwise ``STOCKED`` when AVAILABLE stocked quantity — effective
``STOCKED`` minus the active allocation — waits for that open demand;
otherwise ``OPEN`` — open demand with no quantity in production and no
unallocated stock (nothing released yet, everything scrapped, a release
undone, or every stocked piece already allocated to earlier work).

Long history never loads whole: the Movement history and the Scrap
history (the same immutable history restricted to ``SCRAPPED`` rows)
page in reverse-chronological order ``(occurred_at DESC, id DESC)`` on a
keyset the server resolves from a Movement id; the Quantity Flows list
pages in ONE order — every ACTIVE flow first (oldest first), then the
closed flows newest first — with ``flows_limit`` a hard bound of every
page and a keyset the server resolves from the last flow delivered; the
allocation history pages on ``(allocated_at DESC, id DESC)`` resolved
from an allocation id.
"""

import calendar
import datetime
from collections.abc import Collection, Iterable, Mapping
from typing import Any, Final, Literal, NamedTuple

from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

from app.application.allocations import (
    DemandContext,
    active_allocated_quantities,
    active_allocated_quantity_of,
    open_demand_context,
)
from app.application.errors import NotFoundError
from app.application.lineage import last_known_step_id, snapshot_steps
from app.application.part_numbers import canonical_part_number
from app.application.production_board import (
    BoardLocation,
    FlowPosition,
    effective_totals_by_area,
    flow_positions,
    group_locations,
)
from app.application.production_release import released_quantities
from app.application.projections import effective_lineage_edges, reversed_movement_ids
from app.application.transfers import ROUTE_DEVIATION_KEY
from app.application.work_orders import site_today
from app.domain.enums import MovementReason, MovementType, QuantityFlowStatus, RouteMode
from app.infrastructure.models import (
    PART_NUMBER_BARCODE_PREFIX,
    Area,
    AssignedRoute,
    AssignedRouteStep,
    Machine,
    Operation,
    PartMovement,
    PartNumber,
    QuantityFlow,
    QuantityFlowLineage,
    RouteTemplate,
    WorkOrder,
    WorkOrderAllocation,
    WorkOrderDemand,
)

TrackingStatus = Literal["ACTIVE", "STOCKED", "OPEN", "COMPLETED"]
StatusFilter = Literal["ACTIVE", "STOCKED", "OPEN", "COMPLETED", "ALL"]
DueWindow = Literal["ANY", "OVERDUE", "THIS_WEEK", "THIS_MONTH"]
RouteStepState = Literal["DONE", "CURRENT", "FUTURE"]

# Bounds of one answer (long-data behaviour): the list pages on
# offset / limit over the derived rows in their one deterministic
# order; every paged detail section — the flows, the allocation
# entries, the Movement and Scrap history — is bounded by its limit
# and continued on a server-resolved keyset.
DEFAULT_ROW_LIMIT: Final = 100
MAX_ROW_LIMIT: Final = 200
DEFAULT_FLOW_LIMIT: Final = 50
MAX_FLOW_LIMIT: Final = 200
DEFAULT_ALLOCATION_LIMIT: Final = 100
MAX_ALLOCATION_LIMIT: Final = 200
DEFAULT_MOVEMENT_LIMIT: Final = 50
MAX_MOVEMENT_LIMIT: Final = 200
DEFAULT_SCRAP_LIMIT: Final = 20

# The Movements that bring quantity INTO an Area — the steps of the
# actual route trace. AREA_COMPLETED is completion inside the source
# Area and lineage events are not positions (PROJECT_PROFILE §21).
_ARRIVAL_TYPES: Final = (
    MovementType.RECEIVED,
    MovementType.TRANSFERRED,
    MovementType.QUANTITY_ADJUSTED,
    MovementType.STOCKED,
)
_INTRODUCING_TYPES: Final = (MovementType.RECEIVED, MovementType.QUANTITY_ADJUSTED)
_LINEAGE_TYPES: Final = (MovementType.SPLIT, MovementType.MERGED)
_UNRANKED: Final = 1_000_000_000
_MAX_LINEAGE_DEPTH: Final = 10_000


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class TrackingFilters(NamedTuple):
    """The list's server-side filters (GUI_DESIGN §7.1)."""

    search: str | None = None
    area_id: int | None = None
    operation_id: int | None = None
    machine_id: int | None = None
    request_type: str | None = None
    hot_only: bool = False
    status: StatusFilter = "ACTIVE"
    due: DueWindow = "ANY"


class DistributionEntry(NamedTuple):
    """The PN's quantity in one Area — active, or stocked in a terminal Area."""

    area: Area
    quantity: int
    stocked: bool


class TrackingRow(NamedTuple):
    part_number: str
    has_master: bool
    hot_rank: int | None
    # The OPEN demand context in canonical order (empty = history only).
    demands: list[DemandContext]
    distribution: list[DistributionEntry]
    active_quantity: int
    stocked_quantity: int
    # The PN's ACTIVE allocation and the stocked quantity it leaves
    # unallocated (`stocked − allocated`, never negative).
    allocated_quantity: int
    available_stocked_quantity: int
    scrapped_quantity: int
    # The earliest due date among the open demands; None when none is dated.
    next_due_date: datetime.date | None
    status: TrackingStatus


class TrackingPage(NamedTuple):
    rows: list[TrackingRow]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total


class TrackingDemand(NamedTuple):
    context: DemandContext
    released_quantity: int

    @property
    def shortage(self) -> int:
        return max(
            self.context.demand.requested_quantity - self.context.demand.allocated_quantity, 0
        )


class TraceStep(NamedTuple):
    """One arrival of the flow's quantity in an Area (the actual route trace)."""

    movement: PartMovement
    area: Area
    repair: bool
    # Written on an ancestor before this flow existed (inherited through
    # a single-parent SPLIT descent).
    inherited: bool


class RouteStepView(NamedTuple):
    step: AssignedRouteStep
    area: Area
    operation: Operation | None
    state: RouteStepState


class RouteDeviationView(NamedTuple):
    """One confirmed route deviation, as the Movement recorded it (§17)."""

    movement: PartMovement
    kind: str
    expected_area: Area | None
    expected_operation: Operation | None
    actual_area: Area
    actual_operation: Operation | None
    reason: str | None


class LineageLink(NamedTuple):
    flow_id: int
    relation: str


class TrackingFlow(NamedTuple):
    flow: QuantityFlow
    # The derived current position and its Area — ACTIVE flows only.
    position: FlowPosition | None
    current_area: Area | None
    parents: list[LineageLink]
    children: list[LineageLink]
    trace: list[TraceStep]
    # PLANNED only: the snapshot steps with their derived state.
    route_steps: list[RouteStepView]
    source_template: RouteTemplate | None
    # An ACTIVE PLANNED flow whose current Area is not its last known
    # step's Area (it left the route on a confirmed deviation).
    off_route: bool
    deviations: list[RouteDeviationView]


class FlowPage(NamedTuple):
    """One bounded page of the PN's flows in the one flow order."""

    flows: list[TrackingFlow]
    total: int
    has_more: bool
    # The id of the last flow delivered — `before` of the next page.
    next_before_flow_id: int | None


class AllocationEntry(NamedTuple):
    allocation: WorkOrderAllocation
    demand: WorkOrderDemand
    work_order: WorkOrder
    reversed_by_allocation_id: int | None


class AllocationPage(NamedTuple):
    entries: list[AllocationEntry]
    total: int
    has_more: bool
    next_before_allocation_id: int | None


class HistoryMovement(NamedTuple):
    movement: PartMovement
    # The REVERSED row that undid this Movement, when it was undone.
    reversed_by_movement_id: int | None
    # The lineage edges of a SPLIT / MERGED command (as recorded).
    lineage: list[QuantityFlowLineage]
    # The initiating demand of a RECEIVED (informational context).
    demand: DemandContext | None
    route_step: AssignedRouteStep | None


class HistoryReferences(NamedTuple):
    areas: Mapping[int, Area]
    operations: Mapping[int, Operation]
    machines: Mapping[int, Machine]


class MovementPage(NamedTuple):
    movements: list[HistoryMovement]
    total: int
    has_more: bool
    references: HistoryReferences


class TrackingDetail(NamedTuple):
    part_number: str
    master: PartNumber | None
    barcode_value: str
    status: TrackingStatus
    demands: list[TrackingDemand]
    locations: list[BoardLocation]
    stocked: list[DistributionEntry]
    active_quantity: int
    stocked_quantity: int
    allocated_quantity: int
    scrapped_quantity: int
    introduced_quantity: int
    flows: FlowPage
    allocations: AllocationPage
    movements: MovementPage
    # The PN's SCRAPPED Movements — the same immutable history, newest first.
    scrap_history: MovementPage


# ---------------------------------------------------------------------------
# Shared derivations
# ---------------------------------------------------------------------------


def derived_status(
    active_quantity: int, available_stocked_quantity: int, open_demands: Collection[Any]
) -> TrackingStatus:
    """The PN's status (module docstring): stock counts only while it is
    still available to the open demand — stock allocated to earlier
    work belongs to that work."""
    if active_quantity > 0:
        return "ACTIVE"
    if not open_demands:
        return "COMPLETED"
    if available_stocked_quantity > 0:
        return "STOCKED"
    return "OPEN"


def next_due_date(demands: Iterable[DemandContext]) -> datetime.date | None:
    dated = [entry.demand.due_date for entry in demands if entry.demand.due_date is not None]
    return min(dated) if dated else None


def due_window_bounds(
    window: DueWindow, today: datetime.date
) -> tuple[datetime.date | None, datetime.date | None]:
    """The inclusive due-date bounds (``None`` = open) a window stands for.

    Judged on the site calendar: OVERDUE is before today; THIS_WEEK
    runs from today to the end of the calendar week (Sunday); THIS_MONTH
    from today to the last day of the month. A PN with no dated open
    demand matches only ANY.
    """
    if window == "OVERDUE":
        return None, today - datetime.timedelta(days=1)
    if window == "THIS_WEEK":
        return today, today + datetime.timedelta(days=6 - today.weekday())
    if window == "THIS_MONTH":
        last_day = calendar.monthrange(today.year, today.month)[1]
        return today, today.replace(day=last_day)
    return None, None


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _matching_part_numbers(session: Session, search: str) -> set[str]:
    """The PNs a search text reaches: the PN itself, or a Work Order
    Number / Job Number of ANY of its demands (a completed Work Order
    is still findable by its number)."""
    pattern = _like_pattern(search)
    by_pn = select(QuantityFlow.part_number).where(QuantityFlow.part_number.ilike(pattern))
    by_demand_pn = select(WorkOrderDemand.part_number).where(
        WorkOrderDemand.part_number.ilike(pattern)
    )
    by_demand = (
        select(WorkOrderDemand.part_number)
        .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        .where(
            or_(
                WorkOrder.work_order_number.ilike(pattern),
                func.array_to_string(WorkOrderDemand.job_numbers, " ").ilike(pattern),
            )
        )
    )
    found: set[str] = set()
    for query in (by_pn, by_demand_pn, by_demand):
        found.update(str(pn) for pn in session.scalars(query.distinct()))
    return found


def _tracked_part_numbers(session: Session) -> set[str]:
    """Every PN with production history or an open demand."""
    with_flows = select(QuantityFlow.part_number).distinct()
    with_open_demand = (
        select(WorkOrderDemand.part_number)
        .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        .where(WorkOrder.completed_at.is_(None))
        .distinct()
    )
    found: set[str] = set()
    for query in (with_flows, with_open_demand):
        found.update(str(pn) for pn in session.scalars(query))
    return found


def _active_flows(session: Session, part_numbers: Collection[str]) -> list[QuantityFlow]:
    if not part_numbers:
        return []
    return list(
        session.scalars(
            select(QuantityFlow)
            .where(
                QuantityFlow.part_number.in_(part_numbers),
                QuantityFlow.status == QuantityFlowStatus.ACTIVE,
            )
            .order_by(QuantityFlow.part_number, QuantityFlow.id)
        )
    )


def _all_areas(session: Session) -> dict[int, Area]:
    return {area.id: area for area in session.scalars(select(Area))}


def _totals_by_part_number(totals: Mapping[tuple[str, int], int]) -> dict[str, int]:
    found: dict[str, int] = {}
    for (pn, _area_id), quantity in totals.items():
        found[pn] = found.get(pn, 0) + quantity
    return found


def _masters(session: Session, part_numbers: Collection[str]) -> dict[str, PartNumber]:
    if not part_numbers:
        return {}
    return {
        master.part_number: master
        for master in session.scalars(
            select(PartNumber).where(PartNumber.part_number.in_(part_numbers))
        )
    }


def _distribution(
    locations: Iterable[BoardLocation],
    stocked: Mapping[tuple[str, int], int],
    pn: str,
    areas: Mapping[int, Area],
) -> list[DistributionEntry]:
    active_by_area: dict[int, int] = {}
    for location in locations:
        active_by_area[location.area.id] = (
            active_by_area.get(location.area.id, 0) + location.quantity
        )
    entries = [
        DistributionEntry(area=areas[area_id], quantity=quantity, stocked=False)
        for area_id, quantity in active_by_area.items()
    ] + [
        DistributionEntry(area=areas[area_id], quantity=quantity, stocked=True)
        for (stocked_pn, area_id), quantity in stocked.items()
        if stocked_pn == pn
    ]
    entries.sort(key=lambda entry: (entry.stocked, entry.area.name, entry.area.id))
    return entries


def _row_sort_key(row: TrackingRow) -> tuple[int, datetime.date, int, datetime.date, int, str]:
    """The canonical demand ordering of the row's defining open demand
    (PROJECT_PROFILE §18); rows without one last, by PN."""
    first = row.demands[0] if row.demands else None
    far = datetime.date.max
    if first is None:
        return (_UNRANKED, far, 1, far, _UNRANKED, row.part_number)
    demand = first.demand
    return (
        demand.priority_rank if demand.priority_rank is not None else _UNRANKED,
        demand.due_date if demand.due_date is not None else far,
        1 if demand.due_date is None else 0,
        first.work_order.received_date if demand.due_date is None else far,
        demand.id,
        row.part_number,
    )


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def _passes(
    row: TrackingRow,
    positions: Iterable[FlowPosition],
    filters: TrackingFilters,
    due_bounds: tuple[datetime.date | None, datetime.date | None],
) -> bool:
    if filters.status != "ALL" and row.status != filters.status:
        return False
    if filters.request_type is not None and not any(
        entry.demand.request_type == filters.request_type for entry in row.demands
    ):
        return False
    if filters.hot_only and row.hot_rank is None:
        return False
    if filters.area_id is not None and not any(
        entry.area.id == filters.area_id for entry in row.distribution
    ):
        return False
    if filters.operation_id is not None and not any(
        entry.operation.id == filters.operation_id for entry in positions
    ):
        return False
    if filters.machine_id is not None and not any(
        entry.state == "MACHINE"
        and entry.machine is not None
        and entry.machine.id == filters.machine_id
        for entry in positions
    ):
        return False
    if filters.due != "ANY":
        lower, upper = due_bounds
        due = row.next_due_date
        if due is None:
            return False
        if lower is not None and due < lower:
            return False
        if upper is not None and due > upper:
            return False
    return True


def tracking_list(
    session: Session,
    filters: TrackingFilters,
    *,
    offset: int = 0,
    limit: int = DEFAULT_ROW_LIMIT,
) -> TrackingPage:
    """The filtered PN rows in the canonical order, one bounded page."""
    part_numbers = _tracked_part_numbers(session)
    search = (filters.search or "").strip()
    if search:
        part_numbers &= _matching_part_numbers(session, search)
    areas = _all_areas(session)
    positions = flow_positions(session, _active_flows(session, part_numbers))
    positions_by_pn: dict[str, list[FlowPosition]] = {}
    for entry in positions.values():
        positions_by_pn.setdefault(entry.flow.part_number, []).append(entry)
    groups = group_locations(positions.values(), areas)
    stocked = effective_totals_by_area(session, MovementType.STOCKED, areas.keys())
    scrapped_by_pn = _totals_by_part_number(
        effective_totals_by_area(session, MovementType.SCRAPPED, areas.keys())
    )
    stocked_by_pn = _totals_by_part_number(stocked)
    # One grouped query for every PN's active allocation — never per row.
    allocated_by_pn = active_allocated_quantities(session, stocked_by_pn.keys())
    demands = open_demand_context(session, part_numbers)
    masters = _masters(session, part_numbers)
    due_bounds = due_window_bounds(filters.due, site_today())

    rows: list[TrackingRow] = []
    for pn in part_numbers:
        locations = groups.get(pn, [])
        context = demands.get(pn, [])
        active_quantity = sum(location.quantity for location in locations)
        stocked_quantity = stocked_by_pn.get(pn, 0)
        allocated_quantity = allocated_by_pn.get(pn, 0)
        available_stocked = max(stocked_quantity - allocated_quantity, 0)
        first = context[0] if context else None
        row = TrackingRow(
            part_number=pn,
            has_master=pn in masters,
            hot_rank=first.demand.priority_rank if first is not None else None,
            demands=context,
            distribution=_distribution(locations, stocked, pn, areas),
            active_quantity=active_quantity,
            stocked_quantity=stocked_quantity,
            allocated_quantity=allocated_quantity,
            available_stocked_quantity=available_stocked,
            scrapped_quantity=scrapped_by_pn.get(pn, 0),
            next_due_date=next_due_date(context),
            status=derived_status(active_quantity, available_stocked, context),
        )
        if _passes(row, positions_by_pn.get(pn, []), filters, due_bounds):
            rows.append(row)
    rows.sort(key=_row_sort_key)
    return TrackingPage(
        rows=rows[offset : offset + limit], total=len(rows), offset=offset, limit=limit
    )


# ---------------------------------------------------------------------------
# The detail
# ---------------------------------------------------------------------------


def _first_movement_id(session: Session, flow_id: int) -> int | None:
    return session.scalar(
        select(func.min(PartMovement.id)).where(PartMovement.quantity_flow_id == flow_id)
    )


def _own_arrivals(
    session: Session, flow_id: int, before_movement_id: int | None
) -> list[PartMovement]:
    query = select(PartMovement).where(
        PartMovement.quantity_flow_id == flow_id,
        PartMovement.movement_type.in_(_ARRIVAL_TYPES),
        PartMovement.id.not_in(reversed_movement_ids(flow_id)),
    )
    if before_movement_id is not None:
        query = query.where(PartMovement.id < before_movement_id)
    return list(session.scalars(query.order_by(PartMovement.id)))


def _effective_parents(session: Session, flow_id: int) -> list[LineageLink]:
    """The flow's parents by the EFFECTIVE lineage (an undone descent void)."""
    return [
        LineageLink(edge.parent_flow_id, edge.relation)
        for edge in effective_lineage_edges(session, [flow_id])
        if edge.child_flow_id == flow_id
    ]


def flow_trace(
    session: Session,
    flow_id: int,
    areas: Mapping[int, Area],
) -> list[TraceStep]:
    """The actual route trace of one flow, derived from Movement history.

    The flow's own effective arrivals, prefixed — while the descent has
    exactly ONE parent (a SPLIT child) — by that parent's arrivals
    written before the child existed, recursively up the WHOLE
    single-parent ancestry (each ancestor's lineage is read from history
    as the walk reaches it, so the prefix never depends on which flows a
    detail page happens to list); a merge result has several parents
    whose traces are their own, so its trace starts at the merge.
    Repeated Areas are preserved (the trace is history), a Repair
    transfer is flagged, reversed arrivals never count.
    """
    steps: list[TraceStep] = []
    current = flow_id
    bound: int | None = None
    inherited = False
    seen: set[int] = set()
    for _ in range(_MAX_LINEAGE_DEPTH):
        if current in seen:
            break
        seen.add(current)
        own = [
            TraceStep(
                movement=movement,
                area=areas[movement.to_area_id],
                repair=movement.movement_reason == MovementReason.REPAIR,
                inherited=inherited,
            )
            for movement in _own_arrivals(session, current, bound)
        ]
        steps = own + steps
        parents = _effective_parents(session, current)
        if len(parents) != 1:
            break
        bound = _first_movement_id(session, current)
        current = parents[0].flow_id
        inherited = True
    return steps


def _route_steps(
    session: Session,
    flow: QuantityFlow,
    areas: Mapping[int, Area],
    operations: Mapping[int, Operation],
) -> tuple[list[RouteStepView], bool]:
    if flow.route_mode != RouteMode.PLANNED or flow.assigned_route_id is None:
        return [], False
    steps = snapshot_steps(session, flow.assigned_route_id)
    known_id = last_known_step_id(session, flow.id)
    known = next((step for step in steps if step.id == known_id), None)
    active = flow.status == QuantityFlowStatus.ACTIVE
    views: list[RouteStepView] = []
    for step in steps:
        state: RouteStepState
        if known is None or step.sequence > known.sequence:
            state = "FUTURE"
        elif step.sequence < known.sequence or not active:
            state = "DONE"
        else:
            state = "CURRENT"
        views.append(
            RouteStepView(
                step=step,
                area=areas[step.area_id],
                operation=operations.get(step.operation_id) if step.operation_id else None,
                state=state,
            )
        )
    off_route = active and known is not None and known.area_id != flow.current_area_id
    return views, off_route


def _deviations(
    session: Session,
    flow: QuantityFlow,
    areas: Mapping[int, Area],
    operations: Mapping[int, Operation],
) -> list[RouteDeviationView]:
    movements = session.scalars(
        select(PartMovement)
        .where(
            PartMovement.quantity_flow_id == flow.id,
            PartMovement.metadata_.has_key(ROUTE_DEVIATION_KEY),
            PartMovement.id.not_in(reversed_movement_ids(flow.id)),
        )
        .order_by(PartMovement.id)
    )
    found: list[RouteDeviationView] = []
    for movement in movements:
        recorded = (movement.metadata_ or {})[ROUTE_DEVIATION_KEY]
        expected_area_id = recorded.get("expected_next_area_id")
        expected_operation_id = recorded.get("expected_operation_id")
        actual_operation_id = recorded.get("actual_operation_id")
        found.append(
            RouteDeviationView(
                movement=movement,
                kind=str(recorded.get("kind")),
                expected_area=areas.get(expected_area_id) if expected_area_id else None,
                expected_operation=(
                    operations.get(expected_operation_id) if expected_operation_id else None
                ),
                actual_area=areas[movement.to_area_id],
                actual_operation=(
                    operations.get(actual_operation_id) if actual_operation_id else None
                ),
                reason=recorded.get("reason"),
            )
        )
    return found


def _operations(session: Session, operation_ids: Collection[int]) -> dict[int, Operation]:
    if not operation_ids:
        return {}
    return {
        operation.id: operation
        for operation in session.scalars(select(Operation).where(Operation.id.in_(operation_ids)))
    }


def _flow_cursor(session: Session, pn: str, before_flow_id: int) -> QuantityFlow:
    """The flow a paging cursor names — of THIS PN, or the cursor is
    rejected (never read as a bare ``id < before`` that could skip
    another PN's, or a nonexistent, position)."""
    flow = session.scalar(
        select(QuantityFlow).where(
            QuantityFlow.id == before_flow_id, QuantityFlow.part_number == pn
        )
    )
    if flow is None:
        raise NotFoundError(f"Quantity Flow {before_flow_id} is not part of {pn}'s history.")
    return flow


def _flows_of(
    session: Session, pn: str, *, before_flow_id: int | None, limit: int
) -> tuple[list[QuantityFlow], int, bool]:
    """One bounded page of the PN's flows, with the total and whether more remain.

    The flows have ONE order: every ACTIVE flow first — the current
    state, oldest first (id ascending) — then the closed flows newest
    first (id descending). ``limit`` bounds the whole page, ACTIVE
    flows included, so a PN with more ACTIVE flows than the limit still
    answers one bounded page; ``before_flow_id`` names the last flow a
    page delivered and the server resolves its position in that order
    from the flow itself (an ACTIVE cursor continues with the younger
    ACTIVE flows and then the closed ones from the top; a closed cursor
    with the older closed flows), so every flow is reached exactly once.
    The cursor must be a flow of this PN.
    """
    total = int(
        session.scalar(
            select(func.count()).select_from(QuantityFlow).where(QuantityFlow.part_number == pn)
        )
        or 0
    )
    active_query = (
        select(QuantityFlow)
        .where(QuantityFlow.part_number == pn, QuantityFlow.status == QuantityFlowStatus.ACTIVE)
        .order_by(QuantityFlow.id)
    )
    closed_query = (
        select(QuantityFlow)
        .where(QuantityFlow.part_number == pn, QuantityFlow.status != QuantityFlowStatus.ACTIVE)
        .order_by(QuantityFlow.id.desc())
    )
    read_active = True
    if before_flow_id is not None:
        cursor = _flow_cursor(session, pn, before_flow_id)
        if cursor.status == QuantityFlowStatus.ACTIVE:
            active_query = active_query.where(QuantityFlow.id > cursor.id)
        else:
            read_active = False
            closed_query = closed_query.where(QuantityFlow.id < cursor.id)
    page: list[QuantityFlow] = []
    if read_active:
        page.extend(session.scalars(active_query.limit(limit + 1)))
    if len(page) <= limit:
        page.extend(session.scalars(closed_query.limit(limit + 1 - len(page))))
    has_more = len(page) > limit
    return page[:limit], total, has_more


def _tracking_flows(
    session: Session,
    pn: str,
    positions: Mapping[int, FlowPosition] | None,
    areas: Mapping[int, Area],
    *,
    before_flow_id: int | None,
    limit: int,
) -> FlowPage:
    """One page of `TrackingFlow` blocks. `positions` is the detail's
    derivation of every ACTIVE flow of the PN; a continuation page
    (`None`) derives the positions of the ACTIVE flows it carries."""
    flows, total, has_more = _flows_of(session, pn, before_flow_id=before_flow_id, limit=limit)
    if positions is None:
        positions = flow_positions(
            session, [flow for flow in flows if flow.status == QuantityFlowStatus.ACTIVE]
        )
    # The lineage links a block names (its parents and children); the
    # trace walks the ancestry from history on its own.
    edges = effective_lineage_edges(session, [flow.id for flow in flows])
    parents_of: dict[int, list[LineageLink]] = {}
    children_of: dict[int, list[LineageLink]] = {}
    for edge in edges:
        parents_of.setdefault(edge.child_flow_id, []).append(
            LineageLink(edge.parent_flow_id, edge.relation)
        )
        children_of.setdefault(edge.parent_flow_id, []).append(
            LineageLink(edge.child_flow_id, edge.relation)
        )
    route_ids = [flow.assigned_route_id for flow in flows if flow.assigned_route_id is not None]
    all_steps = (
        list(
            session.scalars(
                select(AssignedRouteStep).where(AssignedRouteStep.assigned_route_id.in_(route_ids))
            )
        )
        if route_ids
        else []
    )
    operation_ids = {step.operation_id for step in all_steps if step.operation_id is not None}
    operation_ids.update(
        int(operation_id)
        for movement in session.scalars(
            select(PartMovement).where(
                PartMovement.part_number == pn,
                PartMovement.metadata_.has_key(ROUTE_DEVIATION_KEY),
            )
        )
        for operation_id in (
            (movement.metadata_ or {})[ROUTE_DEVIATION_KEY].get("expected_operation_id"),
            (movement.metadata_ or {})[ROUTE_DEVIATION_KEY].get("actual_operation_id"),
        )
        if operation_id is not None
    )
    operations = _operations(session, operation_ids)
    # The snapshot's provenance is informational: the template's CURRENT
    # name labels the snapshot, whose steps stay what they were.
    source_template_of = (
        {
            route.id: route.source_route_template_id
            for route in session.scalars(
                select(AssignedRoute).where(AssignedRoute.id.in_(route_ids))
            )
        }
        if route_ids
        else {}
    )
    template_ids = {
        template_id for template_id in source_template_of.values() if template_id is not None
    }
    templates = (
        {
            template.id: template
            for template in session.scalars(
                select(RouteTemplate).where(RouteTemplate.id.in_(template_ids))
            )
        }
        if template_ids
        else {}
    )

    result: list[TrackingFlow] = []
    for flow in flows:
        route_steps, off_route = _route_steps(session, flow, areas, operations)
        template_id = (
            source_template_of.get(flow.assigned_route_id)
            if flow.assigned_route_id is not None
            else None
        )
        result.append(
            TrackingFlow(
                flow=flow,
                position=positions.get(flow.id),
                current_area=areas[flow.current_area_id] if flow.id in positions else None,
                parents=parents_of.get(flow.id, []),
                children=children_of.get(flow.id, []),
                trace=flow_trace(session, flow.id, areas),
                route_steps=route_steps,
                source_template=templates.get(template_id) if template_id is not None else None,
                off_route=off_route,
                deviations=_deviations(session, flow, areas, operations),
            )
        )
    return FlowPage(
        flows=result,
        total=total,
        has_more=has_more,
        next_before_flow_id=flows[-1].id if has_more and flows else None,
    )


def flow_page_of(
    session: Session, part_number: object, before_flow_id: int | None, limit: int
) -> FlowPage:
    """A further page of one PN's Quantity Flows (canonicalized; unknown
    PN, or a cursor that is not a flow of the PN → 404)."""
    pn = _require_tracked(session, part_number)
    areas = _all_areas(session)
    return _tracking_flows(session, pn, None, areas, before_flow_id=before_flow_id, limit=limit)


# ---------------------------------------------------------------------------
# Movement history (paged) and allocation history
# ---------------------------------------------------------------------------


def _effective_total(session: Session, pn: str, movement_types: Collection[MovementType]) -> int:
    reversal = aliased(PartMovement)
    total = session.scalar(
        select(func.coalesce(func.sum(PartMovement.quantity), 0)).where(
            PartMovement.part_number == pn,
            PartMovement.movement_type.in_(movement_types),
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
    )
    return int(total or 0)


def _demand_contexts(session: Session, demand_ids: Collection[int]) -> dict[int, DemandContext]:
    if not demand_ids:
        return {}
    rows = session.execute(
        select(WorkOrderDemand, WorkOrder)
        .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        .where(WorkOrderDemand.id.in_(demand_ids))
    )
    return {demand.id: DemandContext(demand, work_order) for demand, work_order in rows}


def _received_demand_id(movement: PartMovement) -> int | None:
    context = (movement.metadata_ or {}).get("context")
    if not isinstance(context, dict):
        return None
    demand_id = context.get("work_order_demand_id")
    return int(demand_id) if isinstance(demand_id, int) else None


def _demand_of(demands: Mapping[int, DemandContext], demand_id: int | None) -> DemandContext | None:
    return demands.get(demand_id) if demand_id is not None else None


def _history_cursor(
    session: Session, pn: str, before_movement_id: int
) -> tuple[datetime.datetime, int]:
    """The ``(occurred_at, id)`` keyset a Movement id stands for.

    The public cursor is the id of the last row a page delivered; the
    server resolves its timestamp so the continuation follows the SAME
    reverse-chronological order the page had — never the id order
    alone, which a backdated ``occurred_at`` would disagree with.
    """
    occurred_at = session.scalar(
        select(PartMovement.occurred_at).where(
            PartMovement.id == before_movement_id, PartMovement.part_number == pn
        )
    )
    if occurred_at is None:
        raise NotFoundError(f"Movement {before_movement_id} is not part of {pn}'s history.")
    return occurred_at, before_movement_id


def movement_history(
    session: Session,
    pn: str,
    *,
    before_movement_id: int | None = None,
    limit: int = DEFAULT_MOVEMENT_LIMIT,
    movement_types: Collection[MovementType] | None = None,
) -> MovementPage:
    """One page of the PN's immutable Movement history, newest first.

    Reverse-chronological — ``(occurred_at DESC, id DESC)``, the id
    breaking ties deterministically — with keyset paging on that same
    order: ``before_movement_id`` names the last row a page delivered
    and the server resolves its ``(occurred_at, id)``, so history that
    grows while the reader pages never shifts a page and no row is
    skipped or repeated. ``movement_types`` restricts the read (the
    Scrap history is this read for ``SCRAPPED`` rows). Every row is
    reported — a reversed original beside the ``REVERSED`` row that
    undid it — with the audit context the row carries: the lineage
    edges of a SPLIT / MERGED command, the initiating demand of a
    ``RECEIVED``, the fulfilled snapshot step.
    """
    scope: Select[tuple[PartMovement]] = select(PartMovement).where(PartMovement.part_number == pn)
    if movement_types is not None:
        scope = scope.where(PartMovement.movement_type.in_(movement_types))
    query = scope
    if before_movement_id is not None:
        cursor = _history_cursor(session, pn, before_movement_id)
        query = query.where(tuple_(PartMovement.occurred_at, PartMovement.id) < cursor)
    page = list(
        session.scalars(
            query.order_by(PartMovement.occurred_at.desc(), PartMovement.id.desc()).limit(limit + 1)
        )
    )
    has_more = len(page) > limit
    page = page[:limit]
    total = int(session.scalar(select(func.count()).select_from(scope.subquery())) or 0)
    ids = [movement.id for movement in page]
    reversed_by: dict[int, int] = {}
    if ids:
        for reversal_id, original_id in session.execute(
            select(PartMovement.id, PartMovement.reverses_movement_id).where(
                PartMovement.reverses_movement_id.in_(ids)
            )
        ):
            reversed_by[int(original_id)] = int(reversal_id)
    lineage_event_ids = {
        movement.device_event_id for movement in page if movement.movement_type in _LINEAGE_TYPES
    }
    edges_by_event: dict[str, list[QuantityFlowLineage]] = {}
    if lineage_event_ids:
        for edge in session.scalars(
            select(QuantityFlowLineage)
            .where(QuantityFlowLineage.device_event_id.in_(lineage_event_ids))
            .order_by(QuantityFlowLineage.id)
        ):
            edges_by_event.setdefault(edge.device_event_id, []).append(edge)
    # The initiating demand of each RECEIVED row (None elsewhere).
    received_demand_ids = {
        movement.id: (
            _received_demand_id(movement)
            if movement.movement_type == MovementType.RECEIVED
            else None
        )
        for movement in page
    }
    demands = _demand_contexts(
        session,
        {demand_id for demand_id in received_demand_ids.values() if demand_id is not None},
    )
    step_ids = {
        movement.assigned_route_step_id
        for movement in page
        if movement.assigned_route_step_id is not None
    }
    steps = (
        {
            step.id: step
            for step in session.scalars(
                select(AssignedRouteStep).where(AssignedRouteStep.id.in_(step_ids))
            )
        }
        if step_ids
        else {}
    )
    machine_ids = {
        machine_id
        for movement in page
        for machine_id in (movement.source_machine_id, movement.destination_machine_id)
        if machine_id is not None
    }
    machines = (
        {
            machine.id: machine
            for machine in session.scalars(select(Machine).where(Machine.id.in_(machine_ids)))
        }
        if machine_ids
        else {}
    )
    operations = _operations(session, {movement.operation_id for movement in page})
    movements = [
        HistoryMovement(
            movement=movement,
            reversed_by_movement_id=reversed_by.get(movement.id),
            lineage=(
                [
                    edge
                    for edge in edges_by_event.get(movement.device_event_id, [])
                    if movement.quantity_flow_id in (edge.parent_flow_id, edge.child_flow_id)
                ]
                if movement.movement_type in _LINEAGE_TYPES
                else []
            ),
            demand=_demand_of(demands, received_demand_ids[movement.id]),
            route_step=(
                steps.get(movement.assigned_route_step_id)
                if movement.assigned_route_step_id is not None
                else None
            ),
        )
        for movement in page
    ]
    return MovementPage(
        movements=movements,
        total=total,
        has_more=has_more,
        references=HistoryReferences(
            areas=_all_areas(session), operations=operations, machines=machines
        ),
    )


def allocation_history(
    session: Session,
    pn: str,
    *,
    before_allocation_id: int | None = None,
    limit: int = DEFAULT_ALLOCATION_LIMIT,
) -> AllocationPage:
    """One page of the PN's allocation rows (allocations and reversals),
    newest first — ``(allocated_at DESC, id DESC)`` with keyset paging
    resolved from the id of the last row delivered."""
    scope = select(WorkOrderAllocation).where(WorkOrderAllocation.part_number == pn)
    query = scope
    if before_allocation_id is not None:
        allocated_at = session.scalar(
            select(WorkOrderAllocation.allocated_at).where(
                WorkOrderAllocation.id == before_allocation_id,
                WorkOrderAllocation.part_number == pn,
            )
        )
        if allocated_at is None:
            raise NotFoundError(f"Allocation {before_allocation_id} is not part of {pn}'s history.")
        query = query.where(
            tuple_(WorkOrderAllocation.allocated_at, WorkOrderAllocation.id)
            < (allocated_at, before_allocation_id)
        )
    rows = list(
        session.scalars(
            query.order_by(
                WorkOrderAllocation.allocated_at.desc(), WorkOrderAllocation.id.desc()
            ).limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    total = int(session.scalar(select(func.count()).select_from(scope.subquery())) or 0)
    demands = _demand_contexts(session, {row.work_order_demand_id for row in rows})
    reversed_by: dict[int, int] = {}
    if rows:
        for reversal_id, original_id in session.execute(
            select(WorkOrderAllocation.id, WorkOrderAllocation.reverses_allocation_id).where(
                WorkOrderAllocation.reverses_allocation_id.in_([row.id for row in rows])
            )
        ):
            reversed_by[int(original_id)] = int(reversal_id)
    return AllocationPage(
        entries=[
            AllocationEntry(
                allocation=row,
                demand=demands[row.work_order_demand_id].demand,
                work_order=demands[row.work_order_demand_id].work_order,
                reversed_by_allocation_id=reversed_by.get(row.id),
            )
            for row in rows
        ],
        total=total,
        has_more=has_more,
        next_before_allocation_id=rows[-1].id if has_more and rows else None,
    )


def allocation_history_of(
    session: Session, part_number: object, before_allocation_id: int | None, limit: int
) -> AllocationPage:
    """`allocation_history` for a raw PN input (canonicalized; unknown → 404)."""
    pn = _require_tracked(session, part_number)
    return allocation_history(session, pn, before_allocation_id=before_allocation_id, limit=limit)


def _is_tracked(session: Session, pn: str) -> bool:
    for query in (
        select(QuantityFlow.id).where(QuantityFlow.part_number == pn),
        select(WorkOrderDemand.id).where(WorkOrderDemand.part_number == pn),
        select(PartNumber.part_number).where(PartNumber.part_number == pn),
    ):
        if session.scalar(query.limit(1)) is not None:
            return True
    return False


def _require_tracked(session: Session, part_number: object) -> str:
    pn = canonical_part_number(part_number)
    if not _is_tracked(session, pn):
        raise NotFoundError(f"Part Number {pn} is not known to PartFlow.")
    return pn


def movement_history_of(
    session: Session,
    part_number: object,
    before_movement_id: int | None,
    limit: int,
    movement_types: Collection[MovementType] | None = None,
) -> MovementPage:
    """`movement_history` for a raw PN input (canonicalized; unknown → 404)."""
    pn = _require_tracked(session, part_number)
    return movement_history(
        session,
        pn,
        before_movement_id=before_movement_id,
        limit=limit,
        movement_types=movement_types,
    )


def tracking_detail(
    session: Session,
    part_number: object,
    *,
    movements_before: int | None = None,
    movements_limit: int = DEFAULT_MOVEMENT_LIMIT,
    flows_limit: int = DEFAULT_FLOW_LIMIT,
    allocations_limit: int = DEFAULT_ALLOCATION_LIMIT,
    scrap_limit: int = DEFAULT_SCRAP_LIMIT,
) -> TrackingDetail:
    """The read-only detail of one PN (GUI_DESIGN §7.2).

    The canonical PN is the identity: a PN whose master record was
    deleted (or never created) is answered normally with the master
    absent — the history and current state are untouched. Unknown to
    production, demand and master alike → 404.
    """
    pn = _require_tracked(session, part_number)
    areas = _all_areas(session)
    positions = flow_positions(session, _active_flows(session, [pn]))
    locations = group_locations(positions.values(), areas).get(pn, [])
    stocked_by_area = effective_totals_by_area(session, MovementType.STOCKED, areas.keys())
    stocked = [
        DistributionEntry(area=areas[area_id], quantity=quantity, stocked=True)
        for (stocked_pn, area_id), quantity in sorted(
            stocked_by_area.items(), key=lambda item: (areas[item[0][1]].name, item[0][1])
        )
        if stocked_pn == pn
    ]
    active_quantity = sum(location.quantity for location in locations)
    stocked_quantity = sum(entry.quantity for entry in stocked)
    context = open_demand_context(session, [pn]).get(pn, [])
    released = released_quantities(session, [entry.demand.id for entry in context])
    allocated_quantity = active_allocated_quantity_of(session, pn)
    available_stocked = max(stocked_quantity - allocated_quantity, 0)
    master = _masters(session, [pn]).get(pn)
    return TrackingDetail(
        part_number=pn,
        master=master,
        barcode_value=f"{PART_NUMBER_BARCODE_PREFIX}{pn}",
        status=derived_status(active_quantity, available_stocked, context),
        demands=[
            TrackingDemand(context=entry, released_quantity=released.get(entry.demand.id, 0))
            for entry in context
        ],
        locations=locations,
        stocked=stocked,
        active_quantity=active_quantity,
        stocked_quantity=stocked_quantity,
        allocated_quantity=allocated_quantity,
        scrapped_quantity=_effective_total(session, pn, (MovementType.SCRAPPED,)),
        introduced_quantity=_effective_total(session, pn, _INTRODUCING_TYPES),
        flows=_tracking_flows(
            session, pn, positions, areas, before_flow_id=None, limit=flows_limit
        ),
        allocations=allocation_history(session, pn, limit=allocations_limit),
        movements=movement_history(
            session, pn, before_movement_id=movements_before, limit=movements_limit
        ),
        scrap_history=movement_history(
            session, pn, limit=scrap_limit, movement_types=(MovementType.SCRAPPED,)
        ),
    )
