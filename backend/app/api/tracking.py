"""PN Tracking endpoints (Phase 11 — GUI_DESIGN §7; PROJECT_PROFILE §21).

The read-only management surface of `app.application.tracking`:

- ``GET /tracking`` — the filtered PN list in the canonical demand
  order, one bounded page (`offset` / `limit`, the matching `total`).
  Search reaches the PN and the Work Order Number / Job Numbers of any
  of its demands; the selects (Area, Operation, Machine, Request Type,
  Hot only, status, due window) are judged server-side on the derived
  row. `status` defaults to ``ACTIVE`` — pass ``ALL`` for everything.
- ``GET /tracking/detail?part_number=`` — one PN's detail: the optional
  master, the derived barcode, the open demand with released /
  allocated / shortage figures, the current quantity by Area / Machine
  (the shared monitoring derivation), the stocked quantity per Area
  with the active allocation, the §11 reconciliation figures, the
  Quantity Flows with lineage, routes and traces, the allocation
  history, and the first page of the immutable Movement history.
- ``GET /tracking/movements?part_number=&before=`` — a further page of
  that history (keyset on the append-only Movement id, newest first).

A PN is addressed by its canonical value in a query parameter (a PN is
an opaque string that may carry path-hostile characters); the input is
canonicalized by the one domain rule. Reads only: nothing is written,
and every derived time value stays with the display's shared clock.
"""

import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.dependencies import SessionDep
from app.application import tracking
from app.application.allocations import DemandContext
from app.application.production_board import BoardLocation, LocationState
from app.application.transfers import ROUTE_DEVIATION_KEY

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


class TrackingAreaRef(BaseModel):
    id: int
    name: str
    color: str | None
    is_terminal: bool


class TrackingMachineRef(BaseModel):
    id: int
    name: str


class TrackingOperationRef(BaseModel):
    id: int
    code: str
    name: str | None
    is_external: bool


class TrackingWorkOrderRef(BaseModel):
    work_order_id: int
    work_order_number: str | None
    work_order_demand_id: int
    request_type: str


def _area(area: Any) -> TrackingAreaRef:
    return TrackingAreaRef(
        id=area.id, name=area.name, color=area.color, is_terminal=area.is_terminal
    )


def _machine(machine: Any) -> TrackingMachineRef | None:
    return TrackingMachineRef(id=machine.id, name=machine.name) if machine is not None else None


def _operation(operation: Any) -> TrackingOperationRef | None:
    if operation is None:
        return None
    return TrackingOperationRef(
        id=operation.id,
        code=operation.code,
        name=operation.name,
        is_external=operation.is_external,
    )


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


class TrackingDemandResponse(BaseModel):
    work_order_id: int
    work_order_number: str | None
    work_order_demand_id: int
    request_type: str
    requested_quantity: int
    allocated_quantity: int
    job_numbers: list[str]
    due_date: datetime.date | None
    priority_rank: int | None


class DistributionResponse(BaseModel):
    area: TrackingAreaRef
    quantity: int
    # Stocked (manufacturing-complete) quantity in a terminal Area, as
    # opposed to active quantity in production.
    stocked: bool


class TrackingRowResponse(BaseModel):
    part_number: str
    # The optional PartNumber master exists (PROJECT_PROFILE §8.1); the
    # canonical PN and its history render normally either way.
    has_master: bool
    barcode_value: str
    hot_rank: int | None
    # The OPEN demand context in canonical order (empty = history only).
    demands: list[TrackingDemandResponse]
    distribution: list[DistributionResponse]
    active_quantity: int
    stocked_quantity: int
    scrapped_quantity: int
    # The earliest due date among the open demands; null when none is dated.
    next_due_date: datetime.date | None
    status: tracking.TrackingStatus


class TrackingListResponse(BaseModel):
    rows: list[TrackingRowResponse]
    # Rows matching the filters; `rows` is the page at `offset`.
    total: int
    offset: int
    limit: int
    has_more: bool


def _demand(entry: DemandContext) -> TrackingDemandResponse:
    return TrackingDemandResponse(
        work_order_id=entry.work_order.id,
        work_order_number=entry.work_order.work_order_number,
        work_order_demand_id=entry.demand.id,
        request_type=entry.demand.request_type,
        requested_quantity=entry.demand.requested_quantity,
        allocated_quantity=entry.demand.allocated_quantity,
        job_numbers=list(entry.demand.job_numbers),
        due_date=entry.demand.due_date,
        priority_rank=entry.demand.priority_rank,
    )


def _distribution(entry: tracking.DistributionEntry) -> DistributionResponse:
    return DistributionResponse(
        area=_area(entry.area), quantity=entry.quantity, stocked=entry.stocked
    )


def _row(row: tracking.TrackingRow) -> TrackingRowResponse:
    return TrackingRowResponse(
        part_number=row.part_number,
        has_master=row.has_master,
        barcode_value=f"PF:PN:{row.part_number}",
        hot_rank=row.hot_rank,
        demands=[_demand(entry) for entry in row.demands],
        distribution=[_distribution(entry) for entry in row.distribution],
        active_quantity=row.active_quantity,
        stocked_quantity=row.stocked_quantity,
        scrapped_quantity=row.scrapped_quantity,
        next_due_date=row.next_due_date,
        status=row.status,
    )


@router.get("/tracking")
def list_tracking(
    session: SessionDep,
    search: str | None = None,
    area_id: int | None = None,
    operation_id: int | None = None,
    machine_id: int | None = None,
    request_type: Literal["NEW", "MODIFY"] | None = None,
    hot_only: bool = False,
    status: tracking.StatusFilter = "ACTIVE",
    due: tracking.DueWindow = "ANY",
    offset: int = Query(0, ge=0),
    limit: int = Query(tracking.DEFAULT_ROW_LIMIT, ge=1, le=tracking.MAX_ROW_LIMIT),
) -> TrackingListResponse:
    page = tracking.tracking_list(
        session,
        tracking.TrackingFilters(
            search=search,
            area_id=area_id,
            operation_id=operation_id,
            machine_id=machine_id,
            request_type=request_type,
            hot_only=hot_only,
            status=status,
            due=due,
        ),
        offset=offset,
        limit=limit,
    )
    return TrackingListResponse(
        rows=[_row(row) for row in page.rows],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        has_more=page.has_more,
    )


# ---------------------------------------------------------------------------
# The detail
# ---------------------------------------------------------------------------


class TrackingMasterResponse(BaseModel):
    """The optional master record — only its existence and timestamps
    exist before Part Numbers management (Phase 13) adds metadata."""

    part_number: str
    created_at: datetime.datetime


class DetailDemandResponse(TrackingDemandResponse):
    released_quantity: int
    # `requested − allocated` (never negative): what the demand still needs.
    shortage: int


class LocationResponse(BaseModel):
    area: TrackingAreaRef
    # The executor of MACHINE quantity; on DONE quantity the completing
    # Machine as secondary context only.
    machine: TrackingMachineRef | None
    activity: str | None
    quantity: int
    state: LocationState
    since: datetime.datetime | None


class FlowPositionResponse(BaseModel):
    area: TrackingAreaRef
    machine: TrackingMachineRef | None
    operation: TrackingOperationRef
    activity: str | None
    state: LocationState
    since: datetime.datetime


class TraceStepResponse(BaseModel):
    movement_id: int
    # The flow the arrival was written on — an ancestor's for an
    # inherited step.
    quantity_flow_id: int
    movement_type: str
    area: TrackingAreaRef
    occurred_at: datetime.datetime
    repair: bool
    inherited: bool


class RouteStepResponse(BaseModel):
    id: int
    sequence: int
    area: TrackingAreaRef
    operation: TrackingOperationRef | None
    expected_duration: datetime.timedelta | None
    state: tracking.RouteStepState


class RouteDeviationResponse(BaseModel):
    movement_id: int
    occurred_at: datetime.datetime
    kind: str
    expected_area: TrackingAreaRef | None
    expected_operation: TrackingOperationRef | None
    actual_area: TrackingAreaRef
    actual_operation: TrackingOperationRef | None
    reason: str | None
    station_id: str | None


class LineageLinkResponse(BaseModel):
    quantity_flow_id: int
    relation: str


class RouteTemplateRef(BaseModel):
    id: int
    name: str


class FlowResponse(BaseModel):
    id: int
    quantity: int
    status: str
    route_mode: str
    created_at: datetime.datetime
    closed_at: datetime.datetime | None
    # The derived current position — ACTIVE flows only.
    position: FlowPositionResponse | None
    parents: list[LineageLinkResponse]
    children: list[LineageLinkResponse]
    # The actual route trace derived from Movement history.
    trace: list[TraceStepResponse]
    # PLANNED only: the immutable AssignedRoute snapshot.
    route_steps: list[RouteStepResponse]
    source_template: RouteTemplateRef | None
    off_route: bool
    deviations: list[RouteDeviationResponse]


class AllocationResponse(BaseModel):
    id: int
    quantity: int
    work_order: TrackingWorkOrderRef
    source: str
    is_manual_override: bool
    allocation_reason: str | None
    # Set on a reversal row: the allocation it takes back.
    reverses_allocation_id: int | None
    # Set on an allocation that was taken back: the reversal row.
    reversed_by_allocation_id: int | None
    station_id: str | None
    allocated_at: datetime.datetime


class MovementRouteStepRef(BaseModel):
    id: int
    sequence: int


class MovementLineageResponse(BaseModel):
    parent_flow_id: int
    child_flow_id: int
    relation: str


class MovementResponse(BaseModel):
    id: int
    quantity_flow_id: int
    movement_type: str
    quantity: int
    from_area: TrackingAreaRef | None
    to_area: TrackingAreaRef
    operation: TrackingOperationRef
    source_machine: TrackingMachineRef | None
    destination_machine: TrackingMachineRef | None
    station_id: str | None
    occurred_at: datetime.datetime
    device_event_id: str
    command_sequence: int
    movement_reason: str | None
    reason: str | None
    # Set on a REVERSED row: the original it undoes.
    reverses_movement_id: int | None
    # Set on an original that was undone: the REVERSED row.
    reversed_by_movement_id: int | None
    assigned_route_step: MovementRouteStepRef | None
    # A confirmed route deviation exactly as the Movement recorded it.
    route_deviation: dict[str, Any] | None
    lineage: list[MovementLineageResponse]
    # The initiating demand of a RECEIVED (informational context).
    demand: TrackingWorkOrderRef | None


class MovementPageResponse(BaseModel):
    movements: list[MovementResponse]
    total: int
    has_more: bool
    # Pass as `before` for the next (older) page; null on the last page.
    next_before_movement_id: int | None


class TrackingDetailResponse(BaseModel):
    part_number: str
    master: TrackingMasterResponse | None
    barcode_value: str
    status: tracking.TrackingStatus
    demands: list[DetailDemandResponse]
    locations: list[LocationResponse]
    stocked: list[DistributionResponse]
    active_quantity: int
    stocked_quantity: int
    allocated_quantity: int
    available_stocked_quantity: int
    scrapped_quantity: int
    introduced_quantity: int
    flows: list[FlowResponse]
    flow_total: int
    allocations: list[AllocationResponse]
    allocation_total: int
    movements: MovementPageResponse


def _work_order_ref(demand: Any, work_order: Any) -> TrackingWorkOrderRef:
    return TrackingWorkOrderRef(
        work_order_id=work_order.id,
        work_order_number=work_order.work_order_number,
        work_order_demand_id=demand.id,
        request_type=demand.request_type,
    )


def _detail_demand(entry: tracking.TrackingDemand) -> DetailDemandResponse:
    base = _demand(entry.context)
    return DetailDemandResponse(
        **base.model_dump(), released_quantity=entry.released_quantity, shortage=entry.shortage
    )


def _location(location: BoardLocation) -> LocationResponse:
    return LocationResponse(
        area=_area(location.area),
        machine=_machine(location.machine),
        activity=location.activity,
        quantity=location.quantity,
        state=location.state,
        since=location.since,
    )


def _flow(entry: tracking.TrackingFlow) -> FlowResponse:
    position = entry.position
    return FlowResponse(
        id=entry.flow.id,
        quantity=entry.flow.quantity,
        status=entry.flow.status,
        route_mode=entry.flow.route_mode,
        created_at=entry.flow.created_at,
        closed_at=entry.flow.closed_at,
        position=(
            FlowPositionResponse(
                area=_area(entry.current_area),
                machine=_machine(position.machine),
                operation=TrackingOperationRef(
                    id=position.operation.id,
                    code=position.operation.code,
                    name=position.operation.name,
                    is_external=position.operation.is_external,
                ),
                activity=position.activity,
                state=position.state,
                since=position.position.entered_at,
            )
            if position is not None and entry.current_area is not None
            else None
        ),
        parents=[
            LineageLinkResponse(quantity_flow_id=link.flow_id, relation=link.relation)
            for link in entry.parents
        ],
        children=[
            LineageLinkResponse(quantity_flow_id=link.flow_id, relation=link.relation)
            for link in entry.children
        ],
        trace=[
            TraceStepResponse(
                movement_id=step.movement.id,
                quantity_flow_id=step.movement.quantity_flow_id,
                movement_type=step.movement.movement_type,
                area=_area(step.area),
                occurred_at=step.movement.occurred_at,
                repair=step.repair,
                inherited=step.inherited,
            )
            for step in entry.trace
        ],
        route_steps=[
            RouteStepResponse(
                id=view.step.id,
                sequence=view.step.sequence,
                area=_area(view.area),
                operation=_operation(view.operation),
                expected_duration=view.step.expected_duration,
                state=view.state,
            )
            for view in entry.route_steps
        ],
        source_template=(
            RouteTemplateRef(id=entry.source_template.id, name=entry.source_template.name)
            if entry.source_template is not None
            else None
        ),
        off_route=entry.off_route,
        deviations=[
            RouteDeviationResponse(
                movement_id=deviation.movement.id,
                occurred_at=deviation.movement.occurred_at,
                kind=deviation.kind,
                expected_area=_area(deviation.expected_area)
                if deviation.expected_area is not None
                else None,
                expected_operation=_operation(deviation.expected_operation),
                actual_area=_area(deviation.actual_area),
                actual_operation=_operation(deviation.actual_operation),
                reason=deviation.reason,
                station_id=deviation.movement.station_id,
            )
            for deviation in entry.deviations
        ],
    )


def _allocation(entry: tracking.AllocationEntry) -> AllocationResponse:
    row = entry.allocation
    return AllocationResponse(
        id=row.id,
        quantity=row.quantity,
        work_order=_work_order_ref(entry.demand, entry.work_order),
        source=row.source,
        is_manual_override=row.is_manual_override,
        allocation_reason=row.allocation_reason,
        reverses_allocation_id=row.reverses_allocation_id,
        reversed_by_allocation_id=entry.reversed_by_allocation_id,
        station_id=row.station_id,
        allocated_at=row.allocated_at,
    )


def _movement(
    entry: tracking.HistoryMovement, refs: tracking.HistoryReferences
) -> MovementResponse:
    movement = entry.movement
    recorded = movement.metadata_ or {}
    deviation = recorded.get(ROUTE_DEVIATION_KEY)
    return MovementResponse(
        id=movement.id,
        quantity_flow_id=movement.quantity_flow_id,
        movement_type=movement.movement_type,
        quantity=movement.quantity,
        from_area=(
            _area(refs.areas[movement.from_area_id]) if movement.from_area_id is not None else None
        ),
        to_area=_area(refs.areas[movement.to_area_id]),
        operation=TrackingOperationRef(
            id=refs.operations[movement.operation_id].id,
            code=refs.operations[movement.operation_id].code,
            name=refs.operations[movement.operation_id].name,
            is_external=refs.operations[movement.operation_id].is_external,
        ),
        source_machine=_machine(
            refs.machines.get(movement.source_machine_id)
            if movement.source_machine_id is not None
            else None
        ),
        destination_machine=_machine(
            refs.machines.get(movement.destination_machine_id)
            if movement.destination_machine_id is not None
            else None
        ),
        station_id=movement.station_id,
        occurred_at=movement.occurred_at,
        device_event_id=movement.device_event_id,
        command_sequence=movement.command_sequence,
        movement_reason=movement.movement_reason,
        reason=movement.reason,
        reverses_movement_id=movement.reverses_movement_id,
        reversed_by_movement_id=entry.reversed_by_movement_id,
        assigned_route_step=(
            MovementRouteStepRef(id=entry.route_step.id, sequence=entry.route_step.sequence)
            if entry.route_step is not None
            else None
        ),
        route_deviation=dict(deviation) if isinstance(deviation, dict) else None,
        lineage=[
            MovementLineageResponse(
                parent_flow_id=edge.parent_flow_id,
                child_flow_id=edge.child_flow_id,
                relation=edge.relation,
            )
            for edge in entry.lineage
        ],
        demand=(
            _work_order_ref(entry.demand.demand, entry.demand.work_order)
            if entry.demand is not None
            else None
        ),
    )


def _movement_page(page: tracking.MovementPage) -> MovementPageResponse:
    movements = [_movement(entry, page.references) for entry in page.movements]
    return MovementPageResponse(
        movements=movements,
        total=page.total,
        has_more=page.has_more,
        next_before_movement_id=movements[-1].id if page.has_more and movements else None,
    )


@router.get("/tracking/detail")
def get_tracking_detail(
    session: SessionDep,
    part_number: str,
    movements_before: int | None = None,
    movements_limit: int = Query(
        tracking.DEFAULT_MOVEMENT_LIMIT, ge=1, le=tracking.MAX_MOVEMENT_LIMIT
    ),
) -> TrackingDetailResponse:
    detail = tracking.tracking_detail(
        session,
        part_number,
        movements_before=movements_before,
        movements_limit=movements_limit,
    )
    return TrackingDetailResponse(
        part_number=detail.part_number,
        master=(
            TrackingMasterResponse(
                part_number=detail.master.part_number, created_at=detail.master.created_at
            )
            if detail.master is not None
            else None
        ),
        barcode_value=detail.barcode_value,
        status=detail.status,
        demands=[_detail_demand(entry) for entry in detail.demands],
        locations=[_location(location) for location in detail.locations],
        stocked=[_distribution(entry) for entry in detail.stocked],
        active_quantity=detail.active_quantity,
        stocked_quantity=detail.stocked_quantity,
        allocated_quantity=detail.allocated_quantity,
        available_stocked_quantity=max(detail.stocked_quantity - detail.allocated_quantity, 0),
        scrapped_quantity=detail.scrapped_quantity,
        introduced_quantity=detail.introduced_quantity,
        flows=[_flow(entry) for entry in detail.flows],
        flow_total=detail.flow_total,
        allocations=[_allocation(entry) for entry in detail.allocations],
        allocation_total=detail.allocation_total,
        movements=_movement_page(detail.movements),
    )


@router.get("/tracking/movements")
def get_tracking_movements(
    session: SessionDep,
    part_number: str,
    before: int | None = None,
    limit: int = Query(tracking.DEFAULT_MOVEMENT_LIMIT, ge=1, le=tracking.MAX_MOVEMENT_LIMIT),
) -> MovementPageResponse:
    """A further page of one PN's Movement history (newest first, older
    than ``before``). The PN is canonicalized; an unknown PN is 404."""
    return _movement_page(tracking.movement_history_of(session, part_number, before, limit))
