"""Shared Area monitoring response shapes (Phase 5 → Phase 11).

ONE wire contract for the Area/Machine monitoring model, used by every
endpoint that presents it: the Scan Station (`GET
/scan-stations/{id}/context`, the scan resolutions and `GET
/areas/{id}/inventory`) and the Area Board (`GET /area-board`). The two
views render the same quantity through the same shared components, so
they must receive the same fields from the same converters — a second
schema for the same read model is exactly the drift PROJECT_PROFILE §21
forbids between them.

Schemas and converters only: no rules, no queries. The Application read
model (`app.application.scan_station`) owns every derivation.
"""

import datetime
from typing import Literal

from pydantic import BaseModel

from app.application.scan_station import (
    AreaInventory,
    FlowInArea,
    InventoryLine,
    MachineInventory,
    WorkOrderContext,
)
from app.domain.enums import MachineOperationalState
from app.infrastructure.models import Area, Machine, Operation


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
    # Monitoring context of the demand this quantity descends from
    # (GUI_DESIGN §4.10 PN row): the external Job Numbers, the due date
    # the countdown is derived from (NULL is valid data), the Hot rank,
    # and the Work Order's received date, which orders undated demand.
    job_numbers: list[str]
    due_date: datetime.date | None
    priority_rank: int | None
    received_date: datetime.date


ProcessingStateLiteral = Literal["QUEUED", "PROCESSING", "ON_MACHINE", "READY_TO_TRANSFER"]
FlowActionLiteral = Literal["ASSIGN", "DONE", "QUEUE", "TRANSFER", "SCRAP"]
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
    # The Machine that completed READY_TO_TRANSFER quantity — completion
    # context only (the quantity is no longer assigned to it), NULL in
    # every other state and where merged branches disagree.
    completed_machine_id: int | None
    # The fixed instant this quantity entered its current position; the
    # displayed `Time in Area` is derived from it at render.
    entered_at: datetime.datetime
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


def machine_state(state: MachineOperationalState) -> MachineStateLiteral:
    if state is MachineOperationalState.MAINTENANCE:
        return "MAINTENANCE"
    if state is MachineOperationalState.RUNNING:
        return "RUNNING"
    return "IDLE"


def machine_ref(machine: Machine, state: MachineStateLiteral) -> MachineRef:
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


def area_ref(area: Area) -> AreaRef:
    return AreaRef(
        id=area.id,
        name=area.name,
        color=area.color,
        description=area.description,
        is_terminal=area.is_terminal,
    )


def operation_ref(operation: Operation) -> OperationRef:
    return OperationRef(
        id=operation.id,
        code=operation.code,
        name=operation.name,
        is_external=operation.is_external,
    )


def work_order_context(context: WorkOrderContext | None) -> WorkOrderContextResponse | None:
    if context is None:
        return None
    return WorkOrderContextResponse(
        work_order_id=context.work_order_id,
        work_order_number=context.work_order_number,
        work_order_demand_id=context.work_order_demand_id,
        request_type=context.request_type,
        job_numbers=list(context.job_numbers),
        due_date=context.due_date,
        priority_rank=context.priority_rank,
        received_date=context.received_date,
    )


def flow_response(item: FlowInArea) -> FlowInAreaResponse:
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
        completed_machine_id=item.completed_machine_id,
        entered_at=item.entered_at,
        available_actions=list(item.available_actions),
        work_order=work_order_context(item.work_order),
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


def inventory_lines(lines: list[InventoryLine]) -> list[InventoryLineResponse]:
    return [
        InventoryLineResponse(
            part_number=line.part_number,
            total_quantity=line.total_quantity,
            flows=[flow_response(item) for item in line.flows],
        )
        for line in lines
    ]


def machine_card(card: MachineInventory) -> MachineInventoryResponse:
    return MachineInventoryResponse(
        machine=machine_ref(card.machine, machine_state(card.operational_state)),
        lines=inventory_lines(card.lines),
        total_quantity=card.total_quantity,
    )


def area_inventory_response(inventory: AreaInventory) -> AreaInventoryResponse:
    """The shared Area monitoring answer — the ONE converter of it."""
    return AreaInventoryResponse(
        area=area_ref(inventory.area),
        has_machines=inventory.has_machines,
        lines=inventory_lines(inventory.lines),
        total_part_numbers=inventory.total_part_numbers,
        total_quantity=inventory.total_quantity,
        queued=inventory_lines(inventory.queued),
        queued_quantity=inventory.queued_quantity,
        machines=[machine_card(card) for card in inventory.machines],
        on_machine_quantity=inventory.on_machine_quantity,
        processing=inventory_lines(inventory.processing),
        processing_quantity=inventory.processing_quantity,
        finished=inventory_lines(inventory.finished),
        finished_quantity=inventory.finished_quantity,
    )
