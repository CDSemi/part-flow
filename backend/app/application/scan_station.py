"""Scan Station read models (Phase 5 — GUI_DESIGN §4.1, §4.3, §4.7, §4.10).

Everything a Scan Station needs to LOAD before and after a transfer,
read-only and derived from the environment configuration and the
current-position projection:

- the station context — the station, its bound Area with Department
  and color, the Area's active Operations, and whether the Area has
  Machines (the header/statistics mode of GUI_DESIGN §4.3);
- the PN scan resolution — the scanned barcode or manual entry
  canonicalized, the PN's active quantity in the station's Area, and
  the transfer candidates elsewhere, each judged by the SAME route
  rules the transfer command applies (`app.application.transfers`).
  Several candidates are returned as they are: the read model never
  picks one, ranks one as "the" source, or sums them — the operator
  selects exactly one (PROJECT_PROFILE §10 Barcode Resolution, §15
  step 5);
- the Area inventory — the ACTIVE flows currently in an Area grouped
  per PN, refreshed by the station after every confirmed transfer
  (PROJECT_PROFILE §15 step 10).

Nothing here writes. Every flow is reported with its DERIVED
processing state (Phase 6 — QUEUED / ON_MACHINE / READY_TO_TRANSFER,
from the flow's latest Movement) and the Machine it is on, so a
station can offer only the currently valid actions (assign on queued
rows, DONE/QUEUE on Machine rows) and a transfer of ON_MACHINE
quantity can announce the implicit completion. Boundaries: no
Worker/Machine barcode resolution here (a Machine scan resolves in the
Machines surface), no Receive Quantity intake from the station (the
resolution reports whether active demand exists so the UI can present
the honest placeholder), no direct processing state (Phase 7).
"""

from typing import Final, Literal, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.errors import InvalidInputError, NotFoundError
from app.application.machine_processing import latest_movements
from app.application.part_numbers import canonical_part_number
from app.application.projections import processing_state_of
from app.application.transfers import (
    RouteStatus,
    active_area_operations,
    assess_route,
    require_production_station,
    suggested_operation_id,
)
from app.domain.enums import MovementType, ProcessingState, QuantityFlowStatus
from app.infrastructure.models import (
    PART_NUMBER_BARCODE_PREFIX,
    Area,
    Department,
    Machine,
    Operation,
    PartMovement,
    QuantityFlow,
    ScanStation,
    WorkOrder,
    WorkOrderDemand,
)

# The RECEIVED metadata context key the release command writes
# (production_release); read here only to present the Work Order
# context line of the transfer recap (GUI_DESIGN §4.7 item 2).
_CONTEXT_KEY: Final = "context"
_DEMAND_ID_KEY: Final = "work_order_demand_id"


class StationContext(NamedTuple):
    station: ScanStation
    area: Area
    department: Department
    operations: list[Operation]
    has_machines: bool


def station_context(session: Session, station_id: str) -> StationContext:
    """The station and its production environment, fit for production use."""
    station, area = require_production_station(session, station_id)
    department = session.get(Department, area.department_id)
    if department is None:  # pragma: no cover - FK guarantees the row
        raise InvalidInputError(f"Department {area.department_id} does not exist.")
    machine_count = session.scalar(
        select(func.count())
        .select_from(Machine)
        .where(Machine.area_id == area.id, Machine.retired_on.is_(None))
    )
    return StationContext(
        station=station,
        area=area,
        department=department,
        operations=active_area_operations(session, area.id),
        has_machines=bool(machine_count),
    )


# ---------------------------------------------------------------------------
# PN scan resolution
# ---------------------------------------------------------------------------


def part_number_from_scan(barcode: object | None, part_number: object | None) -> str:
    """Canonical PN from a scanned barcode or a manual entry (PROJECT_PROFILE §10).

    Exactly one of the two is given. A scanned value must carry the
    exact ``PF:PN:`` prefix — raw PN text, other ``PF:`` namespaces and
    unknown barcodes are rejected with nothing resolved; the entire
    suffix is the PN candidate and is canonicalized by the one domain
    rule (never cleaned up). Manual entry canonicalizes the entered
    value directly.
    """
    if (barcode is None) == (part_number is None):
        raise InvalidInputError("Provide exactly one of a scanned barcode or a Part Number.")
    if part_number is not None:
        return canonical_part_number(part_number)
    if not isinstance(barcode, str):
        raise InvalidInputError("The scanned barcode must be text.")
    scanned = barcode.strip()
    if not scanned.startswith(PART_NUMBER_BARCODE_PREFIX):
        raise InvalidInputError(
            "Unknown barcode. Scan a Part Number barcode (PF:PN:…) or enter the"
            " Part Number manually."
        )
    return canonical_part_number(scanned[len(PART_NUMBER_BARCODE_PREFIX) :])


class WorkOrderContext(NamedTuple):
    work_order_id: int
    work_order_number: str | None
    work_order_demand_id: int
    request_type: str


class FlowInArea(NamedTuple):
    quantity_flow_id: int
    quantity: int
    route_mode: str
    # Derived from the latest Movement (PROJECT_PROFILE §12): a NULL
    # Machine is QUEUED or READY_TO_TRANSFER, never "queued" by itself.
    processing_state: ProcessingState
    machine_id: int | None
    work_order: WorkOrderContext | None


class TransferCandidate(NamedTuple):
    quantity_flow_id: int
    quantity: int
    route_mode: str
    current_area: Area
    # ON_MACHINE quantity is completed implicitly by the transfer
    # (AREA_COMPLETED + TRANSFERRED) — the confirmation says so.
    processing_state: ProcessingState
    machine_id: int | None
    route_status: RouteStatus
    expected_next_area: Area | None
    # The Operation the Planned Route expects at the next step (None:
    # FLOATING, no next step, or a step without an Operation). An
    # ON_ROUTE transfer for a DIFFERENT active Operation is an
    # Operation deviation the transfer command confirms explicitly.
    expected_operation_id: int | None
    # The Operation the destination resolves to without a choice, if
    # any; None means the operator must choose among ``operations``.
    suggested_operation_id: int | None
    work_order: WorkOrderContext | None


Resolution = Literal["ALREADY_IN_AREA", "TRANSFER_SOURCE_AVAILABLE", "NO_TRANSFERABLE_QUANTITY"]


class ScanResolution(NamedTuple):
    part_number: str
    station: ScanStation
    area: Area
    resolution: Resolution
    in_area: list[FlowInArea]
    candidates: list[TransferCandidate]
    operations: list[Operation]
    has_active_demand: bool
    # Set when the station's Area can never receive a transfer
    # (terminal Area): candidates are still listed for information.
    transfer_blocked_reason: str | None


def _work_order_contexts(session: Session, flow_ids: list[int]) -> dict[int, WorkOrderContext]:
    """The initiating Work Order Demand of each flow, from its RECEIVED context."""
    if not flow_ids:
        return {}
    demand_id_value = PartMovement.metadata_[_CONTEXT_KEY][_DEMAND_ID_KEY].as_integer()
    rows = session.execute(
        select(
            PartMovement.quantity_flow_id,
            WorkOrder.id,
            WorkOrder.work_order_number,
            WorkOrderDemand.id,
            WorkOrderDemand.request_type,
        )
        .join(WorkOrderDemand, WorkOrderDemand.id == demand_id_value)
        .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        .where(
            PartMovement.movement_type == MovementType.RECEIVED,
            PartMovement.quantity_flow_id.in_(flow_ids),
        )
    )
    return {
        flow_id: WorkOrderContext(wo_id, wo_number, demand_id, request_type)
        for flow_id, wo_id, wo_number, demand_id, request_type in rows
    }


def resolve_part_number_scan(
    session: Session, station_id: str, *, barcode: object | None, part_number: object | None
) -> ScanResolution:
    """Resolve a PN at a station into in-Area quantity and explicit transfer sources."""
    station, area = require_production_station(session, station_id)
    pn = part_number_from_scan(barcode, part_number)
    flows = list(
        session.scalars(
            select(QuantityFlow)
            .where(
                QuantityFlow.part_number == pn,
                QuantityFlow.status == QuantityFlowStatus.ACTIVE,
            )
            .order_by(QuantityFlow.id)
        )
    )
    contexts = _work_order_contexts(session, [flow.id for flow in flows])
    latest = latest_movements(session, [flow.id for flow in flows])
    operations = active_area_operations(session, area.id)
    area_names: dict[int, Area] = {area.id: area}

    def _area(area_id: int) -> Area:
        if area_id not in area_names:
            loaded = session.get(Area, area_id)
            if loaded is None:  # pragma: no cover - FK guarantees the row
                raise InvalidInputError(f"Area {area_id} does not exist.")
            area_names[area_id] = loaded
        return area_names[area_id]

    in_area: list[FlowInArea] = []
    candidates: list[TransferCandidate] = []
    for flow in flows:
        state = processing_state_of(latest[flow.id].movement_type)
        if flow.current_area_id == area.id:
            in_area.append(
                FlowInArea(
                    flow.id,
                    flow.quantity,
                    flow.route_mode,
                    state,
                    flow.current_machine_id,
                    contexts.get(flow.id),
                )
            )
            continue
        assessment = assess_route(session, flow, area.id)
        expected = assessment.expected_next_step
        candidates.append(
            TransferCandidate(
                quantity_flow_id=flow.id,
                quantity=flow.quantity,
                route_mode=flow.route_mode,
                current_area=_area(flow.current_area_id),
                processing_state=state,
                machine_id=flow.current_machine_id,
                route_status=assessment.status,
                expected_next_area=_area(expected.area_id) if expected is not None else None,
                expected_operation_id=expected.operation_id if expected is not None else None,
                suggested_operation_id=suggested_operation_id(operations, assessment),
                work_order=contexts.get(flow.id),
            )
        )

    has_active_demand = (
        session.scalar(select(WorkOrderDemand.id).where(WorkOrderDemand.part_number == pn).limit(1))
        is not None
    )
    resolution: Resolution
    if in_area:
        resolution = "ALREADY_IN_AREA"
    elif candidates:
        resolution = "TRANSFER_SOURCE_AVAILABLE"
    else:
        resolution = "NO_TRANSFERABLE_QUANTITY"
    blocked: str | None = None
    if area.is_terminal:
        blocked = (
            f"Area '{area.name}' is a terminal Area. Receiving finished quantity there is"
            " the Stockroom workflow, not a transfer."
        )
    return ScanResolution(
        part_number=pn,
        station=station,
        area=area,
        resolution=resolution,
        in_area=in_area,
        candidates=candidates,
        operations=operations,
        has_active_demand=has_active_demand,
        transfer_blocked_reason=blocked,
    )


# ---------------------------------------------------------------------------
# Area inventory
# ---------------------------------------------------------------------------


class InventoryLine(NamedTuple):
    part_number: str
    total_quantity: int
    flows: list[FlowInArea]


class AreaInventory(NamedTuple):
    area: Area
    lines: list[InventoryLine]
    total_part_numbers: int
    total_quantity: int


def area_inventory(session: Session, area_id: int) -> AreaInventory:
    """ACTIVE quantity currently in an Area, grouped per canonical PN."""
    area = session.get(Area, area_id)
    if area is None:
        raise NotFoundError(f"Area {area_id} does not exist.")
    flows = list(
        session.scalars(
            select(QuantityFlow)
            .where(
                QuantityFlow.current_area_id == area.id,
                QuantityFlow.status == QuantityFlowStatus.ACTIVE,
            )
            .order_by(QuantityFlow.part_number, QuantityFlow.id)
        )
    )
    contexts = _work_order_contexts(session, [flow.id for flow in flows])
    latest = latest_movements(session, [flow.id for flow in flows])
    grouped: dict[str, list[FlowInArea]] = {}
    for flow in flows:
        grouped.setdefault(flow.part_number, []).append(
            FlowInArea(
                flow.id,
                flow.quantity,
                flow.route_mode,
                processing_state_of(latest[flow.id].movement_type),
                flow.current_machine_id,
                contexts.get(flow.id),
            )
        )
    lines = [
        InventoryLine(pn, sum(item.quantity for item in items), items)
        for pn, items in grouped.items()
    ]
    return AreaInventory(
        area=area,
        lines=lines,
        total_part_numbers=len(lines),
        total_quantity=sum(line.total_quantity for line in lines),
    )
