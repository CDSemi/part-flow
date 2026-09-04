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

- the Machine scan resolution (Phase 6, PROJECT_PROFILE §15
  Machine-first) — a ``PF:MACHINE:<asset-tag>`` barcode resolved at the
  station into a **one-shot assignment context**: the Machine
  preselected (active, not under maintenance, in the station's Area)
  and the QUEUED flows of that Area the operator may select or scan a
  PN for. Nothing is remembered server-side — no Machine session, no
  sticky Machine state; the next scan starts fresh.

Nothing here writes. Only ACTIVE flows are inventory: a flow consumed
by a SPLIT or a MERGED (Phase 8) is closed and never listed again —
its children or its merge result are. Every flow is reported with its
DERIVED processing state (from the flow's effective latest
position-bearing Movement — its own, or the one inherited through its
lineage — AND the mode of the
Area it is in — Phase 6 QUEUED / ON_MACHINE / READY_TO_TRANSFER in a
Machine Area, Phase 7 PROCESSING in an Area without Machines, which
directly owns the quantity with no queue and no Machine), the Machine
it is on, and the actions currently valid for it
(``available_actions``: QUEUED → ASSIGN, plus TRANSFER as in Phase 5;
ON_MACHINE → DONE, QUEUE, TRANSFER — the transfer completing
implicitly; PROCESSING → DONE, TRANSFER — the direct-processing DONE
without a Machine, the transfer completing implicitly;
READY_TO_TRANSFER → TRANSFER only), so a station offers exactly the
valid choices. Several matching flows are always returned as they are
with ``requires_selection`` set — the operator selects exactly one,
nothing is picked. The Area inventory separates queued quantity,
quantity on each Machine (the Machine cards — ON_MACHINE quantity only,
with the derived Machine operational state), directly processing
quantity (Areas without Machines — no placeholder cards, no
queued/on-Machine figures) and finished quantity (READY_TO_TRANSFER —
Area summary, never a Machine card). Boundaries: no Worker barcodes.

Receive Quantity (Phase 10.5, PROJECT_PROFILE §14): the resolution
also reports whether the station may INTRODUCE this PN here — no
active Work Order Demand (a demand line with a remaining business
shortage), no active quantity anywhere, and an Area that can start
production — together with the internal blank-number MODIFY Work
Orders a MODIFY receipt may reuse and whether the PartNumber master
already exists. The read model judges the entry condition; the command
(`app.application.intake`) re-judges it authoritatively when it
writes.

Stockroom (Phase 10): at a station bound to a terminal Area the same
resolution lists the PN's candidates as the sources the operator STOCKS
from (`stock_available`; a transfer stays blocked there), and every
resolution carries the PN's derived stocked and available stocked
quantity. Stocked flows are closed and never inventory.
"""

from typing import Final, Literal, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.application import allocations, intake
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.machines import (
    area_has_machines,
    areas_with_machines,
    assigned_quantities,
    operational_state,
)
from app.application.merges import combinable_groups
from app.application.part_numbers import canonical_part_number
from app.application.projections import (
    effective_latest_movements,
    origin_flow_ids,
    processing_state_of,
    visited_area_ids,
)
from app.application.transfers import (
    RouteStatus,
    active_area_operations,
    assess_route,
    require_production_station,
    suggested_operation_id,
)
from app.domain.enums import (
    MachineOperationalState,
    MovementType,
    ProcessingState,
    QuantityFlowStatus,
)
from app.infrastructure.models import (
    MACHINE_BARCODE_PREFIX,
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
    return StationContext(
        station=station,
        area=area,
        department=department,
        operations=active_area_operations(session, area.id),
        # The Area mode (PROJECT_PROFILE §12): follows from its active
        # Machines — the same judgement every command and derivation uses.
        has_machines=area_has_machines(session, area.id),
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
    if scanned.startswith(MACHINE_BARCODE_PREFIX):
        raise InvalidInputError(
            "This is a Machine barcode (PF:MACHINE:…). A Machine scan starts the"
            " one-shot Assign to Machine workflow — it is not a Part Number."
        )
    if not scanned.startswith(PART_NUMBER_BARCODE_PREFIX):
        raise InvalidInputError(
            "Unknown barcode. Scan a Part Number barcode (PF:PN:…) or enter the"
            " Part Number manually."
        )
    return canonical_part_number(scanned[len(PART_NUMBER_BARCODE_PREFIX) :])


FlowAction = Literal["ASSIGN", "DONE", "QUEUE", "TRANSFER", "SCRAP"]

# The actions valid for a flow by its derived state (PROJECT_PROFILE
# §12 Area Processing States; §15 PN-first). TRANSFER is recorded at
# the DESTINATION station; it is listed here so the source station can
# say whether the quantity may leave — from ON_MACHINE and PROCESSING
# it completes implicitly (AREA_COMPLETED + TRANSFERRED), from QUEUED it
# leaves unprocessed exactly as in Phase 5. PROCESSING (an Area without
# Machines, Phase 7) offers the direct-processing DONE — no ASSIGN and
# no QUEUE ever exist there. SCRAP (Phase 9) removes damaged quantity
# from active production and is valid in every state — a partial scrap
# splits inside the command and the remainder keeps its state.
_ACTIONS_BY_STATE: Final[dict[ProcessingState, tuple[FlowAction, ...]]] = {
    ProcessingState.QUEUED: ("ASSIGN", "TRANSFER", "SCRAP"),
    ProcessingState.PROCESSING: ("DONE", "TRANSFER", "SCRAP"),
    ProcessingState.ON_MACHINE: ("DONE", "QUEUE", "TRANSFER", "SCRAP"),
    ProcessingState.READY_TO_TRANSFER: ("TRANSFER", "SCRAP"),
}


def available_actions(state: ProcessingState) -> list[FlowAction]:
    return list(_ACTIONS_BY_STATE[state])


class WorkOrderContext(NamedTuple):
    work_order_id: int
    work_order_number: str | None
    work_order_demand_id: int
    request_type: str


class FlowInArea(NamedTuple):
    part_number: str
    quantity_flow_id: int
    quantity: int
    route_mode: str
    # The Operation the quantity is in the Area for — the one RECORDED
    # on its latest Movement (the arrival, or carried forward by the
    # in-Area events), loaded whatever its current activation: existing
    # quantity keeps its recorded Operation even after that Operation
    # was deactivated, independent of the active Operations a station
    # offers for NEW arrivals. A direct-processing Area names it on the
    # DONE summary and tells external processing apart by it.
    operation: Operation
    # Derived from the latest Movement and the Area's mode (PROJECT_PROFILE
    # §12): a NULL Machine is QUEUED, PROCESSING or READY_TO_TRANSFER,
    # never "queued" by itself.
    processing_state: ProcessingState
    machine_id: int | None
    available_actions: list[FlowAction]
    work_order: WorkOrderContext | None


class TransferCandidate(NamedTuple):
    quantity_flow_id: int
    quantity: int
    route_mode: str
    current_area: Area
    # ON_MACHINE and PROCESSING quantity is completed implicitly by the
    # transfer (AREA_COMPLETED + TRANSFERRED) — the confirmation says so.
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
    # Phase 9 Repair (PROJECT_PROFILE §14): True when the station's
    # Area is one this quantity actually visited before (effective,
    # lineage-aware history) — only then may the station offer `Return
    # quantity for repair` for this candidate. The intent itself stays
    # an explicit operator choice, never a suggestion.
    repair_available: bool
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
    # Phase 10.5: the PN still has a business shortage somewhere
    # (`intake.has_active_demand` — `requested_quantity >
    # allocated_quantity` on any of its demand lines). While it does,
    # quantity belongs to a production release from Management and the
    # station never receives it (PROJECT_PROFILE §14, GUI_DESIGN §4.7).
    has_active_demand: bool
    # Phase 10.5 `Receive Quantity`: the station may introduce this PN
    # here — no active demand, no active quantity anywhere, and an
    # Area that can start production. With the candidates a MODIFY
    # receipt may reuse (§14 — several REQUIRE an explicit selection)
    # and whether the PartNumber master already exists (the Step 1
    # copy tells a known PN from a new one).
    intake_available: bool
    part_number_known: bool
    internal_work_orders: list[intake.InternalWorkOrderCandidate]
    # Set when the station's Area can never receive a transfer
    # (terminal Area): candidates are still listed for information.
    transfer_blocked_reason: str | None
    # More than one flow of the PN in the Area (or more than one
    # transfer candidate): the operator must select exactly one before
    # any action — nothing is picked or combined (PROJECT_PROFILE §15).
    requires_selection: bool
    # Phase 8 `Combine quantities`: the groups of in-Area flows whose
    # production context is identical per the merge command's own rule
    # (`merges.combinable_groups`) — at least two per group. The station
    # offers the combine action for exactly these; it never judges
    # compatibility itself, and nothing is ever combined automatically.
    combine_groups: list[list[int]]
    # Phase 9: the PN's total scrapped quantity (net of reversed
    # scraps) — displayed wherever the PN is presented operationally
    # (PROJECT_PROFILE §11 Scrap).
    scrapped_quantity: int
    # Phase 10 (PROJECT_PROFILE §18): the PN's stocked quantity and what
    # of it is still unallocated — both derived (`STOCKED` history minus
    # active allocation rows). At a Stockroom station the candidates
    # are the sources the operator stocks from; the receiving
    # confirmation follows the `STOCKED` write as its own command.
    stocked_quantity: int
    available_stocked_quantity: int
    # True at a station bound to a terminal Area: the arrival command
    # there is the Stockroom `STOCKED`, never a transfer.
    stock_available: bool


def scrapped_quantity_of(session: Session, part_number: str) -> int:
    """The PN's total scrapped quantity, net of reversed scraps (Phase 9).

    The sum of every effective ``SCRAPPED`` Movement of the PN — a
    scrap that was undone (its row referenced by a ``REVERSED``) never
    counts. This is the `scrapped` term of the §11 reconciliation
    `introduced = active + stocked + scrapped`.
    """
    reversal = aliased(PartMovement)
    total = session.scalar(
        select(func.coalesce(func.sum(PartMovement.quantity), 0)).where(
            PartMovement.part_number == part_number,
            PartMovement.movement_type == MovementType.SCRAPPED,
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
    )
    return int(total or 0)


def _recorded_operations(session: Session, latest: dict[int, PartMovement]) -> dict[int, Operation]:
    """The Operations recorded on the latest Movements, active or not."""
    operation_ids = {movement.operation_id for movement in latest.values()}
    if not operation_ids:
        return {}
    rows = session.scalars(select(Operation).where(Operation.id.in_(operation_ids)))
    return {operation.id: operation for operation in rows}


def _work_order_contexts(session: Session, flow_ids: list[int]) -> dict[int, WorkOrderContext]:
    """The initiating Work Order Demand of each flow, from its RECEIVED context.

    A flow created by a SPLIT or a MERGED (Phase 8) has no RECEIVED of
    its own: its context is the one of the released flow(s) it descends
    from — reported only when every origin names the same demand, so a
    merge of quantity from different demands carries no single context
    rather than a guessed one.
    """
    if not flow_ids:
        return {}
    origins = {flow_id: origin_flow_ids(session, flow_id) for flow_id in flow_ids}
    received = _received_contexts(session, sorted(set[int]().union(*origins.values())))
    contexts: dict[int, WorkOrderContext] = {}
    for flow_id, origin_ids in origins.items():
        found = {received[origin] for origin in origin_ids if origin in received}
        if len(found) == 1:
            contexts[flow_id] = next(iter(found))
    return contexts


def _received_contexts(session: Session, flow_ids: list[int]) -> dict[int, WorkOrderContext]:
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
    latest = effective_latest_movements(session, [flow.id for flow in flows])
    recorded = _recorded_operations(session, latest)
    # Every flow's state depends on the mode of the Area it is in.
    machine_areas = areas_with_machines(session, {flow.current_area_id for flow in flows})
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
    in_area_flows: list[QuantityFlow] = []
    candidates: list[TransferCandidate] = []
    for flow in flows:
        state = processing_state_of(
            latest[flow.id].movement_type,
            direct_processing=flow.current_area_id not in machine_areas,
        )
        if flow.current_area_id == area.id:
            in_area_flows.append(flow)
            in_area.append(
                FlowInArea(
                    flow.part_number,
                    flow.id,
                    flow.quantity,
                    flow.route_mode,
                    recorded[latest[flow.id].operation_id],
                    state,
                    flow.current_machine_id,
                    available_actions(state),
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
                repair_available=area.id in visited_area_ids(session, flow.id),
                work_order=contexts.get(flow.id),
            )
        )

    receive = intake.intake_context(session, pn, area, has_active_quantity=bool(flows))
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
    position = allocations.stock_position_of(session, pn)
    return ScanResolution(
        part_number=pn,
        station=station,
        area=area,
        resolution=resolution,
        in_area=in_area,
        candidates=candidates,
        operations=operations,
        has_active_demand=receive.has_active_demand,
        intake_available=receive.available,
        part_number_known=receive.part_number_known,
        internal_work_orders=receive.work_orders,
        transfer_blocked_reason=blocked,
        requires_selection=len(in_area) > 1 or (not in_area and len(candidates) > 1),
        combine_groups=combinable_groups(
            session, in_area_flows, latest, direct_processing=area.id not in machine_areas
        ),
        scrapped_quantity=scrapped_quantity_of(session, pn),
        stocked_quantity=position.stocked_quantity,
        available_stocked_quantity=position.available_stocked_quantity,
        stock_available=area.is_terminal and bool(candidates),
    )


# ---------------------------------------------------------------------------
# Machine scan resolution — the one-shot assignment context (Phase 6)
# ---------------------------------------------------------------------------


def asset_tag_from_scan(barcode: object | None, asset_tag: object | None) -> str:
    """The Asset Tag from a scanned ``PF:MACHINE:`` barcode or a manual entry.

    Exactly one of the two is given. The barcode must carry the exact
    ``PF:MACHINE:`` prefix (a PN barcode or an unknown barcode is refused
    — never treated as a Machine); the entire suffix is the Asset Tag.
    """
    if (barcode is None) == (asset_tag is None):
        raise InvalidInputError("Provide exactly one of a scanned barcode or an Asset Tag.")
    if asset_tag is not None:
        if not isinstance(asset_tag, str) or not asset_tag.strip():
            raise InvalidInputError("The Asset Tag must not be empty.")
        return asset_tag.strip()
    if not isinstance(barcode, str):
        raise InvalidInputError("The scanned barcode must be text.")
    scanned = barcode.strip()
    if scanned.startswith(PART_NUMBER_BARCODE_PREFIX):
        raise InvalidInputError(
            "This is a Part Number barcode (PF:PN:…), not a Machine barcode."
            " Scan the Machine's Asset Tag label to assign quantity to it."
        )
    tag = (
        scanned[len(MACHINE_BARCODE_PREFIX) :] if scanned.startswith(MACHINE_BARCODE_PREFIX) else ""
    )
    if not tag:
        raise InvalidInputError(
            "Unknown barcode. Scan a Machine barcode (PF:MACHINE:<asset-tag>) to start"
            " the Assign to Machine workflow."
        )
    return tag


class MachineScanResolution(NamedTuple):
    """The one-shot assignment context a Machine scan opens (§15 Machine-first).

    The Machine is preselected; ``queued`` lists every QUEUED flow of the
    station's Area (all PNs) the operator may select or scan a PN for —
    several are returned as they are (``requires_selection``). Nothing is
    stored: the context lives in the dialog only.
    """

    station: ScanStation
    area: Area
    machine: Machine
    operational_state: MachineOperationalState
    assigned_quantity: int
    queued: list[FlowInArea]
    requires_selection: bool


def resolve_machine_scan(
    session: Session, station_id: str, *, barcode: object | None, asset_tag: object | None
) -> MachineScanResolution:
    """Resolve a Machine barcode at a station into the assignment context.

    Refused with nothing resolved (PROJECT_PROFILE §15 "clearly reject"):
    an unknown Asset Tag, a retired Machine (accepts no new scans), a
    Machine of another Area (invalid Area/Machine combination) and a
    Machine under maintenance (accepts no new assignment). The
    assignment command re-validates all of this under the Machine row
    lock — this read only prepares the dialog.
    """
    station, area = require_production_station(session, station_id)
    tag = asset_tag_from_scan(barcode, asset_tag)
    machine = session.scalar(select(Machine).where(Machine.asset_tag == tag))
    if machine is None:
        raise NotFoundError(f"No Machine has the Asset Tag '{tag}'. Nothing was resolved.")
    if machine.retired_on is not None:
        raise ConflictError(
            f"Machine '{machine.name}' ({machine.asset_tag}) is retired and accepts no scans."
        )
    if machine.area_id != area.id:
        raise ConflictError(
            f"Machine '{machine.name}' ({machine.asset_tag}) belongs to another Area."
            f" Scan Station '{station_id}' assigns quantity in Area '{area.name}' only."
        )
    if machine.maintenance_since is not None:
        raise ConflictError(
            f"Machine '{machine.name}' ({machine.asset_tag}) is under maintenance and"
            " accepts no new assignment."
        )
    assigned = assigned_quantities(session, [machine.id]).get(machine.id, 0)
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
    latest = effective_latest_movements(session, [flow.id for flow in flows])
    recorded = _recorded_operations(session, latest)
    contexts = _work_order_contexts(session, [flow.id for flow in flows])
    queued = [
        FlowInArea(
            flow.part_number,
            flow.id,
            flow.quantity,
            flow.route_mode,
            recorded[latest[flow.id].operation_id],
            ProcessingState.QUEUED,
            None,
            available_actions(ProcessingState.QUEUED),
            contexts.get(flow.id),
        )
        for flow in flows
        # The resolved Machine is active and in this Area, so the Area
        # is a Machine Area: its held quantity is QUEUED, never PROCESSING.
        if processing_state_of(latest[flow.id].movement_type, direct_processing=False)
        == ProcessingState.QUEUED
    ]
    return MachineScanResolution(
        station=station,
        area=area,
        machine=machine,
        operational_state=operational_state(machine, assigned),
        assigned_quantity=assigned,
        queued=queued,
        requires_selection=len(queued) > 1,
    )


# ---------------------------------------------------------------------------
# Area inventory
# ---------------------------------------------------------------------------


class InventoryLine(NamedTuple):
    part_number: str
    total_quantity: int
    flows: list[FlowInArea]


class MachineInventory(NamedTuple):
    """One Machine card: the Machine and the ON_MACHINE quantity it holds.

    Every active (non-retired) Machine of the Area appears, with or
    without quantity — a Machine card holds ONLY actively assigned
    quantity; finished quantity belongs to the Area summary
    (PROJECT_PROFILE §12).
    """

    machine: Machine
    operational_state: MachineOperationalState
    lines: list[InventoryLine]
    total_quantity: int


class AreaInventory(NamedTuple):
    area: Area
    # The Area mode (PROJECT_PROFILE §12): with Machines the quantity
    # splits into queued / Machine cards / finished; without Machines
    # into directly processing / finished — no placeholder cards and no
    # queued or on-Machine figures (they are structurally zero).
    has_machines: bool
    # Every ACTIVE flow in the Area per PN, whatever its state (the
    # Phase 5 shape, kept).
    lines: list[InventoryLine]
    total_part_numbers: int
    total_quantity: int
    # Phase 6 separation by derived processing state.
    queued: list[InventoryLine]
    queued_quantity: int
    machines: list[MachineInventory]
    on_machine_quantity: int
    # Phase 7: directly processing quantity of an Area without Machines.
    processing: list[InventoryLine]
    processing_quantity: int
    finished: list[InventoryLine]
    finished_quantity: int


def _lines(flows: list[FlowInArea]) -> list[InventoryLine]:
    grouped: dict[str, list[FlowInArea]] = {}
    for item in flows:
        grouped.setdefault(item.part_number, []).append(item)
    return [
        InventoryLine(pn, sum(item.quantity for item in items), items)
        for pn, items in grouped.items()
    ]


def area_inventory(session: Session, area_id: int) -> AreaInventory:
    """ACTIVE quantity currently in an Area, per PN and per processing state.

    ``lines`` keeps every flow per PN; ``queued`` / ``machines`` /
    ``processing`` / ``finished`` split the same flows by their derived
    state so they never double-count: queued, directly processing and
    finished quantity are Area summary figures, on-Machine quantity is
    grouped per Machine card. Exactly one of the two Area modes applies
    (``has_machines``): a Machine Area never has directly processing
    quantity, an Area without Machines never has queued or on-Machine
    quantity.
    """
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
    latest = effective_latest_movements(session, [flow.id for flow in flows])
    recorded = _recorded_operations(session, latest)
    active_machines = list(
        session.scalars(
            select(Machine)
            .where(Machine.area_id == area.id, Machine.retired_on.is_(None))
            .order_by(Machine.name, Machine.id)
        )
    )
    # The Area mode and the Machine cards come from the SAME listing.
    has_machines = bool(active_machines)
    items: list[FlowInArea] = []
    for flow in flows:
        state = processing_state_of(
            latest[flow.id].movement_type, direct_processing=not has_machines
        )
        items.append(
            FlowInArea(
                flow.part_number,
                flow.id,
                flow.quantity,
                flow.route_mode,
                recorded[latest[flow.id].operation_id],
                state,
                flow.current_machine_id,
                available_actions(state),
                contexts.get(flow.id),
            )
        )
    lines = _lines(items)
    queued = _lines([item for item in items if item.processing_state == ProcessingState.QUEUED])
    processing = _lines(
        [item for item in items if item.processing_state == ProcessingState.PROCESSING]
    )
    finished = _lines(
        [item for item in items if item.processing_state == ProcessingState.READY_TO_TRANSFER]
    )
    on_machine = [item for item in items if item.processing_state == ProcessingState.ON_MACHINE]

    cards = []
    for machine in active_machines:
        # The card's quantity and the derived state come from the SAME
        # ON_MACHINE flows (a flow on a Machine is always in the
        # Machine's Area), so the two can never disagree.
        held = [item for item in on_machine if item.machine_id == machine.id]
        held_quantity = sum(item.quantity for item in held)
        cards.append(
            MachineInventory(
                machine=machine,
                operational_state=operational_state(machine, held_quantity),
                lines=_lines(held),
                total_quantity=held_quantity,
            )
        )
    return AreaInventory(
        area=area,
        has_machines=has_machines,
        lines=lines,
        total_part_numbers=len(lines),
        total_quantity=sum(line.total_quantity for line in lines),
        queued=queued,
        queued_quantity=sum(line.total_quantity for line in queued),
        machines=cards,
        on_machine_quantity=sum(item.quantity for item in on_machine),
        processing=processing,
        processing_quantity=sum(line.total_quantity for line in processing),
        finished=finished,
        finished_quantity=sum(line.total_quantity for line in finished),
    )
