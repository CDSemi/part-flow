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

Phase 10 — ``POST /scan-stations/{station_id}/stockings``: the
Stockroom arrival — the transfer's request shape (minus Repair) recorded
as ``STOCKED`` at a station bound to a terminal Area; the flow closes as
manufacturing-complete and the receiving allocation follows through
``/api/allocations``. The PN resolution reports the PN's stocked and
available stocked quantity and, at a Stockroom station, marks the
candidates as stock sources.

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

Phase 9 — Undo, corrections, and auditable quantity events
(PROJECT_PROFILE §8.11, §11, §14, §16):

- the transfer accepts the explicit Repair intent (``repair`` +
  mandatory ``repair_reason``): the same command records
  ``movement_reason = REPAIR`` and the reason on the ``TRANSFERRED``
  row; 409 when the destination was never visited by the quantity —
  Repair returns quantity to correct earlier work, anything else is a
  normal transfer. The PN resolution marks each transfer candidate
  ``repair_available`` (the station's Area is previously visited) and
  reports the PN's ``scrapped_quantity`` (net of reversed scraps);
- ``POST /scan-stations/{station_id}/scraps`` — one confirmed Scrap =
  ONE auditable ``SCRAPPED`` operation with its mandatory reason; the
  flow (or the split-off part — partial via the same in-command SPLIT)
  closes and leaves active production, history and lineage complete;
- ``POST /scan-stations/{station_id}/quantity-additions`` — found
  physical quantity enters as a NEW FLOATING QuantityFlow with a
  ``QUANTITY_ADJUSTED · INCREASE`` Movement (mandatory reason, no
  demand change ever); 409 when the PN has no active quantity in the
  station's Area (that is the Receive Quantity intake, not an
  addition);
- ``GET /scan-stations/{station_id}/undo-preview/{device_event_id}`` —
  the §16 summary confirmation of undoing one complete command:
  original action, quantity, source/destination, Machine, timestamp,
  the exact effect of the reversal, and whether Undo is currently
  eligible (with the reason when it is not). A read, no locks;
Phase 10.5 — ``POST /scan-stations/{station_id}/receipts``: the
confirmed `Receive Quantity` of GUI_DESIGN §4.7 / PROJECT_PROFILE §14.
One transaction introduces the PN's quantity where no active Work
Order Demand remains: the PartNumber master on first use, the internal
blank-number Work Order created or reused, the WorkOrderDemand, the
QuantityFlow, an AssignedRoute snapshot for ``PLANNED`` only, and the
immutable ``RECEIVED`` Movement carrying the station and the resolved
Operation. A receipt NEVER joins existing quantity: beside active
quantity of the PN it creates a SEPARATE flow, and only after the
explicit ``confirm_active_quantity`` the wizard sets once it showed
the distribution. 201 fresh / 200 idempotent replay / 409 mismatched
reuse; 409 with nothing recorded for a stale station, Area or
Operation context, a terminal Area and active demand that appeared
meanwhile, 409 with ``confirmation_required`` and the existing active
distribution while that confirmation is missing, and 409 with
``selection_required`` listing the candidates when several internal
blank-number MODIFY Work Orders are plausible — a first match is never
guessed. The receipt carries the ``scanned_at`` of the resolution that
opened it, so ``received_date`` follows the SCAN and not the
confirmation (§14); a naive or future scan timestamp is a 422 with
nothing recorded, and so is one older than the intake scan window
UNLESS the receipt is already committed — then it replays. The PN
resolution reports ``scanned_at``, ``intake_available``,
``part_number_known``, those ``internal_work_orders`` and the PN's
``active_quantity`` distribution.

- ``POST /scan-stations/{station_id}/undos`` — reverse the COMPLETE
  command recorded under ``reverses_device_event_id`` as one: a
  compensating ``REVERSED`` Movement per original row (originals
  preserved, at most one reversal each — DB-enforced), flows the
  command closed reopen, flows it created close as ``REVERSED``, and
  the projection is restored from the reversal-aware derivation. 201
  fresh / 200 idempotent replay of the Undo's own ``device_event_id``
  / 409 mismatched reuse; 409 with nothing written when the command is
  ineligible: recorded by Management or at another station, already
  reversed, itself a reversal, no longer the most recent operation of
  its quantity, or restoring onto a retired Machine or into a
  deactivated Area. No Worker identity and no role authorization
  exist yet (Phases 13/14) — nothing here pretends otherwise.
"""

import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.dependencies import SessionDep
from app.application import (
    direct_processing,
    intake,
    machine_processing,
    merges,
    quantity_events,
    scan_station,
    stockroom,
    transfers,
    undo,
)
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
FlowActionLiteral = Literal["ASSIGN", "DONE", "QUEUE", "TRANSFER", "SCRAP"]
MachineStateLiteral = Literal["MAINTENANCE", "RUNNING", "IDLE"]
FlowStatusLiteral = Literal["ACTIVE", "SPLIT", "MERGED", "SCRAPPED", "REVERSED", "STOCKED"]


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
    # Phase 9: the station's Area was previously visited by this
    # quantity — only then is `Return quantity for repair` offered.
    repair_available: bool
    work_order: WorkOrderContextResponse | None


class InternalWorkOrderResponse(BaseModel):
    """One reusable internal blank-number MODIFY Work Order (§14).

    Presented by its business facts; the ids travel for the request
    only — an internal key is never the user-facing Work Order
    identifier (PROJECT_PROFILE §7).
    """

    work_order_id: int
    work_order_demand_id: int
    received_date: datetime.date
    due_date: datetime.date | None
    requested_quantity: int
    job_numbers: list[str]


def _internal_work_order(
    candidate: intake.InternalWorkOrderCandidate,
) -> InternalWorkOrderResponse:
    return InternalWorkOrderResponse(
        work_order_id=candidate.work_order_id,
        work_order_demand_id=candidate.work_order_demand_id,
        received_date=candidate.received_date,
        due_date=candidate.due_date,
        requested_quantity=candidate.requested_quantity,
        job_numbers=candidate.job_numbers,
    )


class ActiveQuantityResponse(BaseModel):
    """One existing ACTIVE quantity of the PN, as the confirmation shows it.

    The same shape the confirmation-required 409 carries, so the
    wizard renders one distribution whether it came with the
    resolution or with the server's refusal.
    """

    quantity_flow_id: int
    quantity: int
    route_mode: str
    current_area_id: int
    current_area_name: str


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
    # Phase 10.5: a demand line of the PN still has a business
    # shortage (`requested_quantity > allocated_quantity`), so its
    # quantity belongs to a production release, not to a receipt.
    has_active_demand: bool
    # Phase 10.5 `Receive Quantity`: the station may introduce this PN
    # here, whether its PartNumber master already exists, and the
    # internal blank-number MODIFY Work Orders a MODIFY receipt may
    # reuse (several REQUIRE an explicit selection — never a guess).
    intake_available: bool
    part_number_known: bool
    internal_work_orders: list[InternalWorkOrderResponse]
    # Phase 10.5 (PROJECT_PROFILE §14): the PN's existing ACTIVE
    # distribution. A receipt never joins it, so while this is
    # non-empty the wizard shows it and takes the operator's explicit
    # confirmation that the receipt creates a SEPARATE quantity.
    active_quantity: list[ActiveQuantityResponse]
    transfer_blocked_reason: str | None
    # Several flows match: the operator must select exactly one.
    requires_selection: bool
    # Phase 8: the groups of in-Area flows the server judges combinable
    # (`Combine quantities`); the client offers the action for these only.
    combine_groups: list[list[int]]
    # Phase 9: the PN's total scrapped quantity, net of reversed scraps.
    scrapped_quantity: int
    # Phase 10: the PN's stocked quantity and its unallocated part
    # (derived); at a Stockroom station the candidates are stock
    # sources and `stock_available` says the STOCKED arrival applies.
    stocked_quantity: int
    available_stocked_quantity: int
    stock_available: bool
    # Phase 10.5: the instant this scan resolved. `Receive Quantity`
    # sends it back with the confirmed receipt — `received_date`
    # defaults to the SCAN, never to the confirmation (§14).
    scanned_at: datetime.datetime


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
                repair_available=candidate.repair_available,
                work_order=_work_order(candidate.work_order),
            )
            for candidate in result.candidates
        ],
        operations=[_operation_ref(operation) for operation in result.operations],
        has_active_demand=result.has_active_demand,
        intake_available=result.intake_available,
        part_number_known=result.part_number_known,
        internal_work_orders=[
            _internal_work_order(candidate) for candidate in result.internal_work_orders
        ],
        active_quantity=[
            ActiveQuantityResponse(
                quantity_flow_id=entry.quantity_flow_id,
                quantity=entry.quantity,
                route_mode=entry.route_mode,
                current_area_id=entry.current_area_id,
                current_area_name=entry.current_area_name,
            )
            for entry in result.active_quantity
        ],
        transfer_blocked_reason=result.transfer_blocked_reason,
        requires_selection=result.requires_selection,
        combine_groups=result.combine_groups,
        scrapped_quantity=result.scrapped_quantity,
        stocked_quantity=result.stocked_quantity,
        available_stocked_quantity=result.available_stocked_quantity,
        stock_available=result.stock_available,
        scanned_at=result.scanned_at,
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
    # Phase 9 Repair (PROJECT_PROFILE §14): the explicit `Return
    # quantity for repair` intent — the same transfer command recorded
    # as `movement_reason = REPAIR` with its mandatory reason. Never
    # inferred; the destination must be previously visited.
    repair: StrictBool = False
    repair_reason: str | None = None
    device_event_id: str


class AreaTransferResponse(BaseModel):
    """The committed arrival, read from the immutable TRANSFERRED (or STOCKED) Movement."""

    movement_id: int
    # TRANSFERRED for a transfer; STOCKED for the Stockroom arrival
    # (Phase 10) — the same command shape with a different meaning.
    movement_type: str
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
    # Present for a Repair (Phase 9): the typed intent and its reason.
    movement_reason: str | None
    reason: str | None
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
        repair=body.repair,
        repair_reason=body.repair_reason,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return _arrival_response(result)


def _arrival_response(result: transfers.AreaTransfer) -> AreaTransferResponse:
    return AreaTransferResponse(
        movement_id=result.movement_id,
        movement_type=result.movement_type,
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
        movement_reason=result.movement_reason,
        reason=result.reason,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


# ---------------------------------------------------------------------------
# Stockroom arrival — STOCKED (Phase 10)
# ---------------------------------------------------------------------------


class StockRequest(BaseModel):
    """The confirmed Stockroom arrival (GUI_DESIGN §10): the transfer shape
    without the Repair intent — quantity stocked at a terminal Area is
    manufacturing-complete, never returned for repair by this command."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    quantity_flow_id: int
    source_area_id: int
    # The terminal Area the operator confirmed at the Stockroom station
    # — an optimistic precondition checked under the station lock.
    target_area_id: int
    quantity: StrictInt
    operation_id: int | None = None
    confirm_route_deviation: StrictBool = False
    route_deviation_reason: str | None = None
    device_event_id: str


@router.post("/scan-stations/{station_id}/stockings")
def stock_at_station_area(
    station_id: str,
    body: StockRequest,
    session: SessionDep,
    response: Response,
) -> AreaTransferResponse:
    """Record the `STOCKED` arrival of ONE QuantityFlow (whole, or a part of
    it — split first) at the station's terminal Area: 201 fresh, 200 on an
    idempotent replay, 409 on a mismatched id reuse, a non-terminal Area,
    a stale source, or an unconfirmed route deviation; 422 for a quantity
    exceeding the source. The flow closes as STOCKED; the allocation
    confirmation that follows is its own command (`POST /allocations`)."""
    result = stockroom.stock_into_station_area(
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
    return _arrival_response(result)


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
# Scrap and quantity addition (Phase 9)
# ---------------------------------------------------------------------------


class ScrapRequest(BaseModel):
    """The confirmed Scrap of damaged quantity — one auditable operation."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    quantity_flow_id: int
    # The total counted with PF:SCRAP; smaller than the flow's it
    # splits the flow first (Phase 8) and the remainder keeps its state.
    quantity: StrictInt
    # The one common scrap reason — mandatory.
    reason: str
    device_event_id: str


class ScrapResponse(BaseModel):
    """The committed Scrap, read from its immutable SCRAPPED Movement."""

    movement_id: int
    # The flow the scrap closed (the source, or the selected child of a
    # partial scrap).
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    # The Machine the scrapped quantity left; null unless ON_MACHINE.
    machine_id: int | None
    reason: str
    station_id: str
    # Present when only a part of the source was scrapped (Phase 8).
    source_quantity_flow_id: int | None
    remainder_quantity_flow_id: int | None
    remainder_quantity: int | None
    device_event_id: str
    occurred_at: datetime.datetime


@router.post("/scan-stations/{station_id}/scraps")
def scrap_quantity(
    station_id: str, body: ScrapRequest, session: SessionDep, response: Response
) -> ScrapResponse:
    result = quantity_events.scrap_flow(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity_flow_id=body.quantity_flow_id,
        quantity=body.quantity,
        reason=body.reason,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return ScrapResponse(
        movement_id=result.movement_id,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        quantity=result.quantity,
        area_id=result.area_id,
        machine_id=result.machine_id,
        reason=result.reason,
        station_id=result.station_id,
        source_quantity_flow_id=result.source_quantity_flow_id,
        remainder_quantity_flow_id=result.remainder_quantity_flow_id,
        remainder_quantity=result.remainder_quantity,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


class QuantityAdditionRequest(BaseModel):
    """The confirmed addition of found physical quantity (Add more quantity).

    Recorded as `QUANTITY_ADJUSTED · INCREASE` on a NEW QuantityFlow —
    never hidden as a transfer, never changing a requested quantity.
    """

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # No MAX and no default (GUI_DESIGN §4.7): the entered quantity.
    quantity: StrictInt
    # Mandatory.
    reason: str
    # Optional when the Area resolves it (single active Operation);
    # required when several are configured.
    operation_id: int | None = None
    device_event_id: str


class QuantityAdditionResponse(BaseModel):
    """The committed addition, read from its immutable Movement."""

    movement_id: int
    # The NEW QuantityFlow the addition introduced.
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    operation_id: int
    # QUEUED (Area with Machines) or PROCESSING (Area without).
    processing_state: ProcessingStateLiteral
    reason: str
    station_id: str
    device_event_id: str
    occurred_at: datetime.datetime


@router.post("/scan-stations/{station_id}/quantity-additions")
def add_quantity(
    station_id: str, body: QuantityAdditionRequest, session: SessionDep, response: Response
) -> QuantityAdditionResponse:
    result = quantity_events.add_quantity(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity=body.quantity,
        reason=body.reason,
        operation_id=body.operation_id,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return QuantityAdditionResponse(
        movement_id=result.movement_id,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        quantity=result.quantity,
        area_id=result.area_id,
        operation_id=result.operation_id,
        processing_state=result.processing_state.value,
        reason=result.reason,
        station_id=result.station_id,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


# ---------------------------------------------------------------------------
# Receive Quantity — the MODIFY intake (Phase 10.5)
# ---------------------------------------------------------------------------


class ReceiptRequest(BaseModel):
    """The confirmed `Receive Quantity` (GUI_DESIGN §4.7 step 3).

    The station sends the whole intent exactly as the operator
    confirmed it; `Confirm receipt` is the only write point.
    """

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # No MAX and no default: the physically received quantity.
    quantity: StrictInt
    # Editable defaults live in the UI (MODIFY / FLOATING); the
    # command never assumes one.
    request_type: str
    route_mode: str
    # Only a PLANNED receipt carries a Planned Route.
    route_template_id: int | None = None
    # Optional when the Area resolves it (single active Operation);
    # required when several are configured.
    operation_id: int | None = None
    # Owned by the WorkOrderDemand — the PN never owns a due date.
    due_date: datetime.date | None = None
    reason: str | None = None
    # The explicitly selected internal blank-number MODIFY Work Order
    # when several were plausible; omitted otherwise.
    work_order_id: int | None = None
    # The `scanned_at` of the PN resolution that opened this wizard,
    # carried unchanged through every step: `received_date` defaults to
    # the SCAN (§14), so a receipt confirmed after site midnight still
    # records the day it was scanned. Required — the server never falls
    # back to its own clock.
    scanned_at: datetime.datetime
    # PROJECT_PROFILE §14: a receipt NEVER joins existing quantity.
    # Set only after the wizard showed the PN's existing active
    # distribution and the operator confirmed that this receipt
    # creates a SEPARATE quantity; the server refuses an unconfirmed
    # receipt beside active quantity with nothing recorded. It is not
    # part of the request fingerprint — confirming continues the same
    # submission under the same `device_event_id`.
    confirm_active_quantity: bool = False
    device_event_id: str


class ReceiptResponse(BaseModel):
    """The committed receipt, read from its immutable Movement."""

    movement_id: int
    quantity_flow_id: int
    part_number: str
    quantity: int
    request_type: Literal["NEW", "MODIFY"]
    route_mode: Literal["FLOATING", "PLANNED"]
    assigned_route_id: int | None
    area_id: int
    operation_id: int
    # QUEUED (Area with Machines) or PROCESSING (Area without).
    processing_state: ProcessingStateLiteral
    work_order_id: int
    work_order_demand_id: int
    # An existing internal Work Order took this receipt (§14 reuse).
    work_order_reused: bool
    reason: str | None
    station_id: str
    device_event_id: str
    occurred_at: datetime.datetime


@router.post("/scan-stations/{station_id}/receipts")
def receive_quantity(
    station_id: str, body: ReceiptRequest, session: SessionDep, response: Response
) -> ReceiptResponse:
    """Record one confirmed `Receive Quantity` as ONE transaction: 201 fresh,
    200 on an idempotent replay (whatever the scan window says by then),
    409 on a mismatched id reuse, on a stale station / Area / Operation
    context, on a terminal Area and on active demand that appeared
    meanwhile, 409 with ``confirmation_required`` and the existing active
    distribution while the separate-quantity confirmation of
    PROJECT_PROFILE §14 is missing, and 409 with ``selection_required``
    when several internal blank-number MODIFY Work Orders are plausible;
    422 for an invalid PN, quantity, Request Type, Route Mode, Planned
    Route or scan timestamp (naive, in the future, or — for a receipt not
    yet recorded — older than the intake scan window)."""
    result = intake.receive_quantity(
        session,
        station_id=station_id,
        part_number=body.part_number,
        quantity=body.quantity,
        request_type=body.request_type,
        route_mode=body.route_mode,
        route_template_id=body.route_template_id,
        operation_id=body.operation_id,
        due_date=body.due_date,
        reason=body.reason,
        work_order_id=body.work_order_id,
        scanned_at=body.scanned_at,
        confirm_active_quantity=body.confirm_active_quantity,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return ReceiptResponse(
        movement_id=result.movement_id,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        quantity=result.quantity,
        request_type=result.request_type.value,
        route_mode=result.route_mode.value,
        assigned_route_id=result.assigned_route_id,
        area_id=result.area_id,
        operation_id=result.operation_id,
        processing_state=result.processing_state.value,
        work_order_id=result.work_order_id,
        work_order_demand_id=result.work_order_demand_id,
        work_order_reused=result.work_order_reused,
        reason=result.reason,
        station_id=result.station_id,
        device_event_id=result.device_event_id,
        occurred_at=result.occurred_at,
    )


# ---------------------------------------------------------------------------
# Undo (Phase 9 — PROJECT_PROFILE §16)
# ---------------------------------------------------------------------------


class UndoMovementSummaryResponse(BaseModel):
    """One original Movement of the command, for the summary confirmation."""

    movement_id: int
    movement_type: str
    movement_reason: str | None
    quantity: int
    from_area: AreaRef | None
    to_area: AreaRef
    machine_id: int | None
    operation_id: int


class RestoredFlowResponse(BaseModel):
    """The effect of the reversal on one Quantity Flow."""

    quantity_flow_id: int
    quantity: int
    # ACTIVE: reopened/repositioned with the restored Area/Machine.
    # REVERSED: the command created this flow, so it closes.
    status: FlowStatusLiteral
    current_area_id: int | None
    current_machine_id: int | None


class RestoredFlowPreviewResponse(BaseModel):
    quantity_flow_id: int
    quantity: int
    status: FlowStatusLiteral
    area: AreaRef | None
    machine_id: int | None
    processing_state: ProcessingStateLiteral | None


class UndoPreviewResponse(BaseModel):
    """The §16 summary confirmation of undoing one command — a read.

    ``eligible`` says whether the Undo command would currently be
    accepted and ``ineligible_reason`` why not; the command itself
    re-judges everything under its row locks.
    """

    reverses_device_event_id: str
    station_id: str
    # The command kind as recorded (TRANSFER, ASSIGN, QUEUE, DONE,
    # MERGE, SCRAP, ADD); null for a pre-Phase-6 row without one.
    kind: str | None
    part_number: str
    quantity: int
    occurred_at: datetime.datetime
    eligible: bool
    ineligible_reason: str | None
    movements: list[UndoMovementSummaryResponse]
    restored: list[RestoredFlowPreviewResponse]


@router.get("/scan-stations/{station_id}/undo-preview/{device_event_id}")
def get_undo_preview(
    station_id: str, device_event_id: str, session: SessionDep
) -> UndoPreviewResponse:
    result = undo.undo_preview(session, station_id, device_event_id)
    return UndoPreviewResponse(
        reverses_device_event_id=result.reverses_device_event_id,
        station_id=result.station_id,
        kind=result.kind,
        part_number=result.part_number,
        quantity=result.quantity,
        occurred_at=result.occurred_at,
        eligible=result.eligible,
        ineligible_reason=result.ineligible_reason,
        movements=[
            UndoMovementSummaryResponse(
                movement_id=item.movement_id,
                movement_type=item.movement_type,
                movement_reason=item.movement_reason,
                quantity=item.quantity,
                from_area=_area_ref(item.from_area) if item.from_area is not None else None,
                to_area=_area_ref(item.to_area),
                machine_id=item.machine_id,
                operation_id=item.operation_id,
            )
            for item in result.movements
        ],
        restored=[
            RestoredFlowPreviewResponse(
                quantity_flow_id=item.quantity_flow_id,
                quantity=item.quantity,
                status=item.status.value,
                area=_area_ref(item.area) if item.area is not None else None,
                machine_id=item.machine_id,
                processing_state=(
                    item.processing_state.value if item.processing_state is not None else None
                ),
            )
            for item in result.restored
        ],
    )


class UndoRequest(BaseModel):
    """The confirmed reversal of one complete committed command."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # The command to reverse — every Movement recorded under it.
    reverses_device_event_id: str
    # The Undo's OWN idempotency key (a new production event).
    device_event_id: str


class ReversedMovementResponse(BaseModel):
    movement_id: int
    reverses_movement_id: int
    original_movement_type: str


class UndoResponse(BaseModel):
    """The committed Undo, read from its immutable REVERSED Movements."""

    reverses_device_event_id: str
    # The reversed command's kind as recorded (TRANSFER, ASSIGN, ...).
    reversed_kind: str | None
    part_number: str
    station_id: str
    movements: list[ReversedMovementResponse]
    flows: list[RestoredFlowResponse]
    device_event_id: str
    occurred_at: datetime.datetime


@router.post("/scan-stations/{station_id}/undos")
def undo_production_command(
    station_id: str, body: UndoRequest, session: SessionDep, response: Response
) -> UndoResponse:
    result = undo.undo_command(
        session,
        station_id=station_id,
        part_number=body.part_number,
        reverses_device_event_id=body.reverses_device_event_id,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return UndoResponse(
        reverses_device_event_id=result.reverses_device_event_id,
        reversed_kind=result.reversed_kind,
        part_number=result.part_number,
        station_id=result.station_id,
        movements=[
            ReversedMovementResponse(
                movement_id=item.movement_id,
                reverses_movement_id=item.reverses_movement_id,
                original_movement_type=item.original_movement_type,
            )
            for item in result.movements
        ],
        flows=[
            RestoredFlowResponse(
                quantity_flow_id=item.quantity_flow_id,
                quantity=item.quantity,
                status=item.status.value,
                current_area_id=item.current_area_id,
                current_machine_id=item.current_machine_id,
            )
            for item in result.flows
        ],
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
