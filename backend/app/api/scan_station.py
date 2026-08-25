"""Scan Station endpoints (Phase 5 — GUI_DESIGN §4; PROJECT_PROFILE §15).

The HTTP surface of the Scan Station transfer slice. Every route stays
thin: request schemas validate shape only (``extra="forbid"``), the
Application read models and the transfer command own every rule, and
the central handlers in ``app.api.errors`` translate typed failures —
including the route-deviation confirmation that carries the deviation
the dialog presents.

Surface:

- ``GET  /scan-stations/{station_id}/context`` — what the station
  renders on load: the bound Area (with Department and color), its
  active Operations, and whether it has Machines. An unknown station
  is 404, an inactive station or Area 409 — never a silent fallback.
- ``POST /scan-stations/{station_id}/scans/resolve`` — a PN barcode
  (``PF:PN:…``) or a manual PN entry resolved at the station: the PN's
  quantity already in the Area and the explicit transfer candidates
  elsewhere. A read: it records nothing, and with several candidates
  it returns all of them — the client must make the operator select
  exactly one.
- ``POST /scan-stations/{station_id}/transfers`` — the confirmed
  transfer of ONE whole QuantityFlow into the station's Area. 201 on a
  fresh transfer, 200 on an idempotent replay (same ``device_event_id``
  + same request), 409 on a mismatched reuse, 409 with
  ``confirmation_required`` when a PLANNED flow leaves its route and
  the deviation is not yet confirmed, 422 for partial quantity.
- ``GET  /areas/{area_id}/inventory`` — the ACTIVE quantity currently
  in an Area grouped per PN, the refresh source after a transfer.
"""

import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.dependencies import SessionDep
from app.application import scan_station, transfers
from app.application.scan_station import FlowInArea, WorkOrderContext
from app.infrastructure.models import Area, Operation

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Shared response shapes
# ---------------------------------------------------------------------------


class AreaRef(BaseModel):
    id: int
    name: str
    color: str | None
    description: str | None
    is_terminal: bool


class OperationRef(BaseModel):
    id: int
    code: str
    name: str | None
    is_external: bool


class WorkOrderContextResponse(BaseModel):
    work_order_id: int
    # NULL for an internal blank-number Work Order (rendered `—`).
    work_order_number: str | None
    work_order_demand_id: int
    request_type: str


class FlowInAreaResponse(BaseModel):
    quantity_flow_id: int
    quantity: int
    route_mode: str
    work_order: WorkOrderContextResponse | None


def _area_ref(area: Area) -> AreaRef:
    return AreaRef(
        id=area.id,
        name=area.name,
        color=area.color,
        description=area.description,
        is_terminal=area.is_terminal,
    )


def _operation_ref(operation: Operation) -> OperationRef:
    return OperationRef(
        id=operation.id,
        code=operation.code,
        name=operation.name,
        is_external=operation.is_external,
    )


def _work_order(context: WorkOrderContext | None) -> WorkOrderContextResponse | None:
    if context is None:
        return None
    return WorkOrderContextResponse(
        work_order_id=context.work_order_id,
        work_order_number=context.work_order_number,
        work_order_demand_id=context.work_order_demand_id,
        request_type=context.request_type,
    )


def _flow(item: FlowInArea) -> FlowInAreaResponse:
    return FlowInAreaResponse(
        quantity_flow_id=item.quantity_flow_id,
        quantity=item.quantity,
        route_mode=item.route_mode,
        work_order=_work_order(item.work_order),
    )


# ---------------------------------------------------------------------------
# Station context
# ---------------------------------------------------------------------------


class DepartmentRef(BaseModel):
    id: int
    name: str


class StationContextResponse(BaseModel):
    station_id: str
    department: DepartmentRef
    area: AreaRef
    operations: list[OperationRef]
    # Header/statistics mode (GUI_DESIGN §4.3): an Area with Machines
    # queues quantity; one without processes it directly.
    has_machines: bool


@router.get("/scan-stations/{station_id}/context")
def get_station_context(station_id: str, session: SessionDep) -> StationContextResponse:
    context = scan_station.station_context(session, station_id)
    return StationContextResponse(
        station_id=context.station.station_id,
        department=DepartmentRef(id=context.department.id, name=context.department.name),
        area=_area_ref(context.area),
        operations=[_operation_ref(operation) for operation in context.operations],
        has_machines=context.has_machines,
    )


# ---------------------------------------------------------------------------
# PN scan resolution
# ---------------------------------------------------------------------------


class ScanResolveRequest(BaseModel):
    """Exactly one of a scanned barcode or a manually entered PN."""

    model_config = ConfigDict(extra="forbid")

    barcode: str | None = None
    part_number: str | None = None


class TransferCandidateResponse(BaseModel):
    quantity_flow_id: int
    quantity: int
    route_mode: str
    current_area: AreaRef
    # FLOATING: no route expectation. ON_ROUTE: the station's Area is
    # the next Planned Route step. DEVIATION: the transfer needs the
    # explicit route-deviation confirmation.
    route_status: Literal["FLOATING", "ON_ROUTE", "DEVIATION"]
    expected_next_area: AreaRef | None
    # The Operation the transfer resolves to without a choice; null
    # means the operator must choose one of ``operations``.
    suggested_operation_id: int | None
    work_order: WorkOrderContextResponse | None


class ScanResolveResponse(BaseModel):
    part_number: str
    station_id: str
    area: AreaRef
    resolution: Literal["ALREADY_IN_AREA", "TRANSFER_SOURCE_AVAILABLE", "NO_TRANSFERABLE_QUANTITY"]
    in_area: list[FlowInAreaResponse]
    # Every valid source, in QuantityFlow order — never ranked, never
    # combined. More than one means the operator must select.
    candidates: list[TransferCandidateResponse]
    operations: list[OperationRef]
    has_active_demand: bool
    transfer_blocked_reason: str | None


@router.post("/scan-stations/{station_id}/scans/resolve")
def resolve_scan(
    station_id: str, body: ScanResolveRequest, session: SessionDep
) -> ScanResolveResponse:
    result = scan_station.resolve_part_number_scan(
        session, station_id, barcode=body.barcode, part_number=body.part_number
    )
    return ScanResolveResponse(
        part_number=result.part_number,
        station_id=result.station.station_id,
        area=_area_ref(result.area),
        resolution=result.resolution,
        in_area=[_flow(item) for item in result.in_area],
        candidates=[
            TransferCandidateResponse(
                quantity_flow_id=candidate.quantity_flow_id,
                quantity=candidate.quantity,
                route_mode=candidate.route_mode,
                current_area=_area_ref(candidate.current_area),
                route_status=candidate.route_status,
                expected_next_area=(
                    _area_ref(candidate.expected_next_area)
                    if candidate.expected_next_area is not None
                    else None
                ),
                suggested_operation_id=candidate.suggested_operation_id,
                work_order=_work_order(candidate.work_order),
            )
            for candidate in result.candidates
        ],
        operations=[_operation_ref(operation) for operation in result.operations],
        has_active_demand=result.has_active_demand,
        transfer_blocked_reason=result.transfer_blocked_reason,
    )


# ---------------------------------------------------------------------------
# Transfer command
# ---------------------------------------------------------------------------


class AreaTransferRequest(BaseModel):
    """The confirmed transfer submission (GUI_DESIGN §4.7 item 2)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # The ONE source the operator selected, and the Area it was shown
    # in — a precondition, so quantity that moved meanwhile is refused.
    quantity_flow_id: int
    source_area_id: int
    # Strict: a quantity is an integer, never a coerced bool/float/text.
    # Phase 5 accepts only the flow's whole quantity.
    quantity: StrictInt
    # Optional when the destination resolves it (single Operation or
    # route-step Operation); required when several are configured.
    operation_id: int | None = None
    # Explicit route-deviation confirmation (PROJECT_PROFILE §17): set
    # by the UI only after showing the deviation.
    confirm_route_deviation: StrictBool = False
    device_event_id: str


class AreaTransferResponse(BaseModel):
    """The committed transfer, read from the immutable TRANSFERRED Movement."""

    movement_id: int
    quantity_flow_id: int
    part_number: str
    quantity: int
    from_area_id: int
    to_area_id: int
    operation_id: int
    station_id: str
    assigned_route_step_id: int | None
    # Present when the transfer was a confirmed route deviation.
    route_deviation: dict[str, object] | None
    device_event_id: str
    occurred_at: datetime.datetime


@router.post("/scan-stations/{station_id}/transfers")
def transfer_to_station_area(
    station_id: str,
    body: AreaTransferRequest,
    session: SessionDep,
    response: Response,
) -> AreaTransferResponse:
    result = transfers.transfer_to_station_area(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity_flow_id=body.quantity_flow_id,
        source_area_id=body.source_area_id,
        quantity=body.quantity,
        operation_id=body.operation_id,
        confirm_route_deviation=body.confirm_route_deviation,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return AreaTransferResponse(
        movement_id=result.movement_id,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        quantity=result.quantity,
        from_area_id=result.from_area_id,
        to_area_id=result.to_area_id,
        operation_id=result.operation_id,
        station_id=result.station_id,
        assigned_route_step_id=result.assigned_route_step_id,
        route_deviation=result.route_deviation,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


# ---------------------------------------------------------------------------
# Area inventory
# ---------------------------------------------------------------------------


class InventoryLineResponse(BaseModel):
    part_number: str
    total_quantity: int
    flows: list[FlowInAreaResponse]


class AreaInventoryResponse(BaseModel):
    area: AreaRef
    lines: list[InventoryLineResponse]
    total_part_numbers: int
    total_quantity: int


@router.get("/areas/{area_id}/inventory")
def get_area_inventory(area_id: int, session: SessionDep) -> AreaInventoryResponse:
    inventory = scan_station.area_inventory(session, area_id)
    return AreaInventoryResponse(
        area=_area_ref(inventory.area),
        lines=[
            InventoryLineResponse(
                part_number=line.part_number,
                total_quantity=line.total_quantity,
                flows=[_flow(item) for item in line.flows],
            )
            for line in inventory.lines
        ],
        total_part_numbers=inventory.total_part_numbers,
        total_quantity=inventory.total_quantity,
    )
