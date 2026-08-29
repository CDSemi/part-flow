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
  transfer of ONE QuantityFlow — whole, or a part of it (Phase 8: the
  flow splits first inside the same command) — into the station's
  Area. 201 on a fresh transfer, 200 on an idempotent replay (same ``device_event_id``
  + same confirmed intent — whatever happened to the station, Area
  or Operation since), 409 on a mismatched reuse, 409 on a station
  deactivated or rebound away from the confirmed destination Area,
  409 with ``confirmation_required`` when a PLANNED flow leaves its
  route (a different Area OR a different Operation than the planned
  step) and the deviation is not yet confirmed, 422 for a confirmed
  deviation without a reason and for a quantity exceeding the source.
- ``GET  /areas/{area_id}/inventory`` — the ACTIVE quantity currently
  in an Area grouped per PN, the refresh source after a transfer.

Phase 6 — the one-shot Machine-Area processing commands, each ONE
QuantityFlow (whole, or since Phase 8 a part of it), 201 fresh / 200
idempotent replay / 409 mismatched reuse, 422 for a quantity exceeding
the flow, 409 for a flow not in the station's
Area, a wrong processing state, or a retired / other-Area /
maintenance Machine:

- ``POST /scan-stations/{station_id}/machine-assignments`` — assign
  QUEUED quantity to a Machine (``ASSIGNED_TO_MACHINE`` →
  ``ON_MACHINE``); Machine-first and PN-first entry points both land
  here.
- ``POST /scan-stations/{station_id}/machine-releases`` — the QUEUE
  action: ON_MACHINE quantity returns to the Area queue
  (``RELEASED_FROM_MACHINE`` → ``QUEUED``).
- ``POST /scan-stations/{station_id}/area-completions`` — the DONE
  action: ON_MACHINE quantity completes processing at the Area
  (``AREA_COMPLETED`` → ``READY_TO_TRANSFER``, Machine cleared, Area
  kept). Phase 7: the same endpoint WITHOUT ``machine_id`` is the
  direct-processing DONE of an Area without Machines (PROCESSING →
  ``AREA_COMPLETED`` with no Machine → ``READY_TO_TRANSFER``); a DONE
  without a Machine on quantity in a Machine Area, or with a Machine on
  directly processing quantity, is 409 with nothing recorded.

- ``POST /scan-stations/{station_id}/machine-scans/resolve`` — a
  Machine barcode (``PF:MACHINE:<asset-tag>``) or a manual Asset Tag
  resolved at the station into the ONE-SHOT assignment context: the
  Machine preselected (404 unknown; 409 retired, other Area, or under
  maintenance) and the QUEUED flows of the station's Area to select or
  scan a PN for (several → ``requires_selection``). A read: nothing is
  stored, no Machine session exists — the next scan starts fresh.

The transfer response additionally reports the implicit
``AREA_COMPLETED`` (``completed_movement_id`` / ``completed_machine_id``)
appended in the same command when the quantity was still actively
processing — ON_MACHINE (the Machine reported) or, Phase 7, PROCESSING
in an Area without Machines (``completed_machine_id`` null). Every flow
in the read models carries its derived ``processing_state`` (from its
latest Movement and the mode of the Area it is in — an Area without
Machines holds arriving quantity as PROCESSING, never queued),
``machine_id`` and ``available_actions`` (PN-first: QUEUED → ASSIGN,
TRANSFER; PROCESSING → DONE, TRANSFER; ON_MACHINE → DONE, QUEUE,
TRANSFER; READY_TO_TRANSFER → TRANSFER), the PN resolution flags
``requires_selection`` when several flows match, and the Area inventory
reports the Area mode (``has_machines``) and splits queued quantity,
quantity per Machine card (ON_MACHINE only, with the derived Machine
state), directly processing quantity and finished quantity.

Phase 8 — partial quantity and the explicit merge:

- every command above accepts a ``quantity`` smaller than the flow's:
  the flow is SPLIT first inside the same command (the source closes,
  the selected child receives the action, the remainder keeps the
  source's state) and the response names the consumed
  ``source_quantity_flow_id`` and the ``remainder_quantity_flow_id``
  with its quantity (all null for a whole-flow command); the whole
  quantity never splits, a larger quantity stays 422;
- ``POST /scan-stations/{station_id}/merges`` — merge at least two
  ACTIVE flows of ONE PN in the station's Area into one resulting flow
  (201 fresh / 200 replay / 409 mismatched reuse; 409 with nothing
  recorded when the flows' production context differs — state,
  Machine, Operation or route context — or a flow is not in the Area).
  Never automatic: only the flows named are merged. Closed (split or
  merged) flows never appear in the read models again. The PN
  resolution reports ``combine_groups`` — the in-Area flows the server
  judges combinable by the same rule — so the station offers
  `Combine quantities` for exactly those.
"""

import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.dependencies import SessionDep
from app.application import direct_processing, machine_processing, merges, scan_station, transfers
from app.application.scan_station import FlowInArea, MachineInventory, WorkOrderContext
from app.domain.enums import MachineOperationalState
from app.infrastructure.models import Area, Machine, Operation

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


ProcessingStateLiteral = Literal["QUEUED", "PROCESSING", "ON_MACHINE", "READY_TO_TRANSFER"]
FlowActionLiteral = Literal["ASSIGN", "DONE", "QUEUE", "TRANSFER"]
MachineStateLiteral = Literal["MAINTENANCE", "RUNNING", "IDLE"]


class RecordedOperationRef(BaseModel):
    """The Operation recorded on a flow's latest Movement — presented as
    recorded, whatever its current activation (an inactive Operation is
    still the one existing quantity is in the Area for)."""

    id: int
    code: str
    name: str | None
    is_external: bool
    is_active: bool


class FlowInAreaResponse(BaseModel):
    part_number: str
    quantity_flow_id: int
    quantity: int
    route_mode: str
    # The Operation recorded on the flow's latest Movement — independent
    # of the active Operations the station offers for new arrivals.
    operation: RecordedOperationRef
    # Derived from the flow's latest Movement and the Area's mode
    # (PROJECT_PROFILE §12); machine_id is set exactly while ON_MACHINE.
    processing_state: ProcessingStateLiteral
    machine_id: int | None
    # The actions currently valid for this flow (PN-first, §15).
    available_actions: list[FlowActionLiteral]
    work_order: WorkOrderContextResponse | None


class MachineRef(BaseModel):
    id: int
    name: str
    asset_tag: str
    barcode_value: str
    # Derived (PROJECT_PROFILE §8.6) with the moment it last changed.
    operational_state: MachineStateLiteral
    state_changed_at: datetime.datetime
    maintenance_since: datetime.datetime | None
    maintenance_note: str | None
    maintenance_expected_return: datetime.date | None


def _machine_state(state: MachineOperationalState) -> MachineStateLiteral:
    if state is MachineOperationalState.MAINTENANCE:
        return "MAINTENANCE"
    if state is MachineOperationalState.RUNNING:
        return "RUNNING"
    return "IDLE"


def _machine_ref(machine: Machine, state: MachineStateLiteral) -> MachineRef:
    return MachineRef(
        id=machine.id,
        name=machine.name,
        asset_tag=machine.asset_tag,
        barcode_value=machine.barcode_value,
        operational_state=state,
        state_changed_at=machine.state_changed_at,
        maintenance_since=machine.maintenance_since,
        maintenance_note=machine.maintenance_note,
        maintenance_expected_return=machine.maintenance_expected_return,
    )


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
        part_number=item.part_number,
        quantity_flow_id=item.quantity_flow_id,
        quantity=item.quantity,
        route_mode=item.route_mode,
        operation=RecordedOperationRef(
            id=item.operation.id,
            code=item.operation.code,
            name=item.operation.name,
            is_external=item.operation.is_external,
            is_active=item.operation.is_active,
        ),
        processing_state=item.processing_state.value,
        machine_id=item.machine_id,
        available_actions=list(item.available_actions),
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
    # ON_MACHINE and PROCESSING quantity is completed implicitly by the
    # transfer (AREA_COMPLETED + TRANSFERRED in one command).
    processing_state: ProcessingStateLiteral
    machine_id: int | None
    # FLOATING: no route expectation. ON_ROUTE: the station's Area is
    # the next Planned Route step. DEVIATION: the transfer needs the
    # explicit route-deviation confirmation.
    route_status: Literal["FLOATING", "ON_ROUTE", "DEVIATION"]
    expected_next_area: AreaRef | None
    # The Operation the Planned Route expects at its next step (null:
    # FLOATING, route end, or a step without an Operation). Choosing a
    # different Operation at an ON_ROUTE destination is a route
    # deviation the transfer confirms explicitly with a reason.
    expected_operation_id: int | None
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
    # Several flows match: the operator must select exactly one.
    requires_selection: bool
    # Phase 8: the groups of in-Area flows the server judges combinable
    # (`Combine quantities`); the client offers the action for these only.
    combine_groups: list[list[int]]


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
                processing_state=candidate.processing_state.value,
                machine_id=candidate.machine_id,
                route_status=candidate.route_status,
                expected_next_area=(
                    _area_ref(candidate.expected_next_area)
                    if candidate.expected_next_area is not None
                    else None
                ),
                expected_operation_id=candidate.expected_operation_id,
                suggested_operation_id=candidate.suggested_operation_id,
                work_order=_work_order(candidate.work_order),
            )
            for candidate in result.candidates
        ],
        operations=[_operation_ref(operation) for operation in result.operations],
        has_active_demand=result.has_active_demand,
        transfer_blocked_reason=result.transfer_blocked_reason,
        requires_selection=result.requires_selection,
        combine_groups=result.combine_groups,
    )


# ---------------------------------------------------------------------------
# Machine scan resolution — one-shot assignment context (Phase 6)
# ---------------------------------------------------------------------------


class MachineScanResolveRequest(BaseModel):
    """Exactly one of a scanned Machine barcode or a manually entered Asset Tag."""

    model_config = ConfigDict(extra="forbid")

    barcode: str | None = None
    asset_tag: str | None = None


class MachineScanResolveResponse(BaseModel):
    """The one-shot Assign to Machine context. Nothing is stored server-side."""

    station_id: str
    area: AreaRef
    machine: MachineRef
    assigned_quantity: int
    # Every QUEUED flow of the Area, all PNs, in PN order — never
    # picked; more than one means the operator selects or scans a PN.
    queued: list[FlowInAreaResponse]
    requires_selection: bool


@router.post("/scan-stations/{station_id}/machine-scans/resolve")
def resolve_machine_scan(
    station_id: str, body: MachineScanResolveRequest, session: SessionDep
) -> MachineScanResolveResponse:
    result = scan_station.resolve_machine_scan(
        session, station_id, barcode=body.barcode, asset_tag=body.asset_tag
    )
    return MachineScanResolveResponse(
        station_id=result.station.station_id,
        area=_area_ref(result.area),
        machine=_machine_ref(result.machine, _machine_state(result.operational_state)),
        assigned_quantity=result.assigned_quantity,
        queued=[_flow(item) for item in result.queued],
        requires_selection=result.requires_selection,
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
    # The destination Area the operator resolved and confirmed at the
    # station — an optimistic precondition: the station must still be
    # bound to exactly this Area when the transfer is recorded.
    target_area_id: int
    # Strict: a quantity is an integer, never a coerced bool/float/text.
    # The whole flow, or a part of it (Phase 8 — split first).
    quantity: StrictInt
    # Optional when the destination resolves it (single Operation or
    # route-step Operation); required when several are configured.
    operation_id: int | None = None
    # Explicit route-deviation confirmation (PROJECT_PROFILE §17): set
    # by the UI only after showing the deviation, together with the
    # mandatory reason (§17 step 7) recorded on the Movement.
    confirm_route_deviation: StrictBool = False
    route_deviation_reason: str | None = None
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
    # Present when the quantity was actively processing at the source:
    # the implicit AREA_COMPLETED of the same command, and the Machine
    # left (ON_MACHINE) — null for directly processing quantity.
    completed_movement_id: int | None
    completed_machine_id: int | None
    # Present when only a part of the source moved (Phase 8): the
    # consumed source flow and the remainder left at the source.
    source_quantity_flow_id: int | None
    remainder_quantity_flow_id: int | None
    remainder_quantity: int | None
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
        target_area_id=body.target_area_id,
        quantity=body.quantity,
        operation_id=body.operation_id,
        confirm_route_deviation=body.confirm_route_deviation,
        route_deviation_reason=body.route_deviation_reason,
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
        completed_movement_id=result.completed_movement_id,
        completed_machine_id=result.completed_machine_id,
        source_quantity_flow_id=result.source_quantity_flow_id,
        remainder_quantity_flow_id=result.remainder_quantity_flow_id,
        remainder_quantity=result.remainder_quantity,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


# ---------------------------------------------------------------------------
# Machine-Area processing commands (Phase 6)
# ---------------------------------------------------------------------------


class MachineProcessingRequest(BaseModel):
    """One confirmed in-Area action on ONE QuantityFlow, whole or in part.

    ``machine_id`` is the Machine to assign to (assignment) or the
    Machine the quantity is on (QUEUE / DONE — an optimistic
    precondition). ``quantity`` is strict; smaller than the flow's it
    splits the flow first (Phase 8).
    """

    model_config = ConfigDict(extra="forbid")

    part_number: str
    quantity_flow_id: int
    machine_id: int
    quantity: StrictInt
    device_event_id: str


class AreaCompletionRequest(BaseModel):
    """The confirmed DONE on ONE QuantityFlow, whole or in part.

    With ``machine_id``: the Machine the quantity is on (optimistic
    precondition) in a Machine Area. Without it (Phase 7): the
    direct-processing DONE of an Area without Machines — the same
    wizard without a Machine field. The two are distinct intents under
    one ``device_event_id``.
    """

    model_config = ConfigDict(extra="forbid")

    part_number: str
    quantity_flow_id: int
    machine_id: int | None = None
    quantity: StrictInt
    device_event_id: str


class MachineProcessingResponse(BaseModel):
    """The committed action, read from its immutable Movement."""

    movement_id: int
    movement_type: str
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    # Null for a direct-processing DONE (Area without Machines).
    machine_id: int | None
    operation_id: int
    station_id: str
    processing_state: ProcessingStateLiteral
    # Present when only a part of the flow was acted on (Phase 8).
    source_quantity_flow_id: int | None
    remainder_quantity_flow_id: int | None
    remainder_quantity: int | None
    device_event_id: str
    occurred_at: datetime.datetime


def _processing_response(
    result: machine_processing.MachineProcessingResult, response: Response
) -> MachineProcessingResponse:
    response.status_code = 201 if result.created else 200
    return MachineProcessingResponse(
        movement_id=result.movement_id,
        movement_type=result.movement_type,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        quantity=result.quantity,
        area_id=result.area_id,
        machine_id=result.machine_id,
        operation_id=result.operation_id,
        station_id=result.station_id,
        processing_state=result.processing_state.value,
        source_quantity_flow_id=result.source_quantity_flow_id,
        remainder_quantity_flow_id=result.remainder_quantity_flow_id,
        remainder_quantity=result.remainder_quantity,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


@router.post("/scan-stations/{station_id}/machine-assignments")
def assign_to_machine(
    station_id: str, body: MachineProcessingRequest, session: SessionDep, response: Response
) -> MachineProcessingResponse:
    result = machine_processing.assign_to_machine(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity_flow_id=body.quantity_flow_id,
        machine_id=body.machine_id,
        quantity=body.quantity,
        device_event_id=body.device_event_id,
    )
    return _processing_response(result, response)


@router.post("/scan-stations/{station_id}/machine-releases")
def release_to_queue(
    station_id: str, body: MachineProcessingRequest, session: SessionDep, response: Response
) -> MachineProcessingResponse:
    result = machine_processing.release_to_queue(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity_flow_id=body.quantity_flow_id,
        machine_id=body.machine_id,
        quantity=body.quantity,
        device_event_id=body.device_event_id,
    )
    return _processing_response(result, response)


@router.post("/scan-stations/{station_id}/area-completions")
def complete_area_processing(
    station_id: str, body: AreaCompletionRequest, session: SessionDep, response: Response
) -> MachineProcessingResponse:
    if body.machine_id is None:
        result = direct_processing.complete_direct_processing(
            session,
            station_id=station_id,
            part_number=body.part_number,
            quantity_flow_id=body.quantity_flow_id,
            quantity=body.quantity,
            device_event_id=body.device_event_id,
        )
    else:
        result = machine_processing.complete_at_machine(
            session,
            station_id=station_id,
            part_number=body.part_number,
            quantity_flow_id=body.quantity_flow_id,
            machine_id=body.machine_id,
            quantity=body.quantity,
            device_event_id=body.device_event_id,
        )
    return _processing_response(result, response)


# ---------------------------------------------------------------------------
# Explicit merge (Phase 8)
# ---------------------------------------------------------------------------


class MergeRequest(BaseModel):
    """The confirmed merge of the named ACTIVE flows of one PN in the station's Area."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    quantity_flow_ids: list[StrictInt]
    device_event_id: str


class MergeResponse(BaseModel):
    """The committed merge, read from its immutable MERGED Movements."""

    movement_id: int
    # The resulting flow.
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    machine_id: int | None
    operation_id: int
    station_id: str
    processing_state: ProcessingStateLiteral
    # The consumed sources, ascending id.
    source_quantity_flow_ids: list[int]
    device_event_id: str
    occurred_at: datetime.datetime


@router.post("/scan-stations/{station_id}/merges")
def merge_flows(
    station_id: str, body: MergeRequest, session: SessionDep, response: Response
) -> MergeResponse:
    result = merges.merge_flows(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity_flow_ids=body.quantity_flow_ids,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return MergeResponse(
        movement_id=result.movement_id,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        quantity=result.quantity,
        area_id=result.area_id,
        machine_id=result.machine_id,
        operation_id=result.operation_id,
        station_id=result.station_id,
        processing_state=result.processing_state.value,
        source_quantity_flow_ids=result.source_quantity_flow_ids,
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


class MachineInventoryResponse(BaseModel):
    """One Machine card: ON_MACHINE quantity only (PROJECT_PROFILE §12)."""

    machine: MachineRef
    lines: list[InventoryLineResponse]
    total_quantity: int


class AreaInventoryResponse(BaseModel):
    area: AreaRef
    # The Area mode (PROJECT_PROFILE §12): true → queued / Machine cards
    # / finished; false → directly processing / finished, with no
    # placeholder cards and structurally zero queued/on-Machine figures.
    has_machines: bool
    # Every ACTIVE flow per PN whatever its state (Phase 5 shape).
    lines: list[InventoryLineResponse]
    total_part_numbers: int
    total_quantity: int
    # Phase 6: the same flows split by derived state — queued and
    # finished are Area summary figures; on-Machine quantity sits on
    # the Machine cards (every active Machine, with or without quantity).
    queued: list[InventoryLineResponse]
    queued_quantity: int
    machines: list[MachineInventoryResponse]
    on_machine_quantity: int
    # Phase 7: directly processing quantity (Areas without Machines).
    processing: list[InventoryLineResponse]
    processing_quantity: int
    finished: list[InventoryLineResponse]
    finished_quantity: int


def _lines(lines: list[scan_station.InventoryLine]) -> list[InventoryLineResponse]:
    return [
        InventoryLineResponse(
            part_number=line.part_number,
            total_quantity=line.total_quantity,
            flows=[_flow(item) for item in line.flows],
        )
        for line in lines
    ]


def _machine_card(card: MachineInventory) -> MachineInventoryResponse:
    return MachineInventoryResponse(
        machine=_machine_ref(card.machine, _machine_state(card.operational_state)),
        lines=_lines(card.lines),
        total_quantity=card.total_quantity,
    )


@router.get("/areas/{area_id}/inventory")
def get_area_inventory(area_id: int, session: SessionDep) -> AreaInventoryResponse:
    inventory = scan_station.area_inventory(session, area_id)
    return AreaInventoryResponse(
        area=_area_ref(inventory.area),
        has_machines=inventory.has_machines,
        lines=_lines(inventory.lines),
        total_part_numbers=inventory.total_part_numbers,
        total_quantity=inventory.total_quantity,
        queued=_lines(inventory.queued),
        queued_quantity=inventory.queued_quantity,
        machines=[_machine_card(card) for card in inventory.machines],
        on_machine_quantity=inventory.on_machine_quantity,
        processing=_lines(inventory.processing),
        processing_quantity=inventory.processing_quantity,
        finished=_lines(inventory.finished),
        finished_quantity=inventory.finished_quantity,
    )
