"""Work Order Allocation endpoints (Phase 10 — PROJECT_PROFILE §8.12, §18; GUI_DESIGN §10).

Thin routes over `app.application.allocations`: request schemas
validate shape only (``extra="forbid"``), every rule and transaction
lives in the Application layer, and the central handlers translate the
typed errors.

Surface:

- ``GET  /allocations/suggestion?part_number=…&quantity=…`` — the
  canonical allocation suggestion for a PN (a read): the PN's stocked,
  allocated and available stocked quantity, and every outstanding
  demand line in the canonical demand ordering with the requested,
  previously allocated, remaining shortage and proposed quantity. The
  quantity defaults to the whole available stocked quantity and is
  capped at it. The Stockroom station calls it with the just-stocked
  quantity right after the ``STOCKED`` write.
- ``POST /allocations`` — the Stockroom station's receiving
  confirmation: the confirmed allocation of one PN's stocked quantity
  to demand lines (``station_id`` required). A Scan Station route — it
  never resolves the User principal; it requires an enrolled device of
  that station. 201 fresh, 200 on an idempotent
  replay of the same
  ``device_event_id`` + same intent, 422 when the lines do not sum to
  the explicit ``allocation_quantity`` (or when it is missing), 409 on
  a mismatched reuse, on a line beyond its remaining shortage, or on an
  allocation quantity the available stocked quantity no longer covers
  (a stale figure) — every refusal writes nothing.
- ``POST /allocations/management`` — the Management allocation of
  stocked quantity (allocate-later): the same command without a
  station, recorded with the signed-in User; needs Edit Work Order
  Allocation. Same answers as above, plus 409
  ``recorded_by_another_user`` when another User recorded the
  ``device_event_id``.
- ``POST /allocations/{allocation_id}/reversals`` — the auditable
  adjustment, Management only: takes one allocation back with a
  mandatory reason (a smaller allocation is a reversal plus a new
  allocation); needs Edit Work Order Allocation. 201 / 200 / 409 as
  above; 409 when already reversed (also under a race).
- ``POST /allocations/corrections`` — the authorized beyond-demand
  correction (Phase 14 slice 5, PROJECT_PROFILE §8.12): one demand line,
  MORE than its remaining demand, never above the available stocked
  quantity, mandatory reason, recorded ``exceeds_demand`` with the
  signed-in User; needs Edit Work Order Allocation. 201 / 200 replay;
  409 within the remaining demand, beyond the available stock, on a
  mismatched reuse or ``recorded_by_another_user``.
- ``GET  /allocations/management/context?part_number=…`` or
  ``?work_order_demand_id=…`` — the stock figures, demand lines and
  active allocations (with who recorded them) a Management allocation,
  reversal or correction is prepared from: the PN's open Work Order
  Demand, or one line whatever its Work Order's state; needs Edit Work
  Order Allocation (it serves only that write workflow).
- ``GET  /allocations?part_number=…&work_order_demand_id=…&work_order_id=…``
  — the allocation rows (allocations and reversals) for audit display;
  needs View production data or Edit Work Order Allocation.

The acting User (``actor_user_id``) is derived from the session, never
from a request body (Phase 14 slice 3); station rows carry none.

The two station routes (the suggestion and ``POST /allocations``)
require an enrolled station device (Phase 14 slice 4, owner decision
OD-P6; ``X-PartFlow-Station-Device``): 401 ``station_device_required``
without one, and for the confirmation 403 ``station_device_mismatch``
when the body's ``station_id`` is not the device's station. The role
applied at Scan Stations must grant ``CONFIRM_SUGGESTED_ALLOCATION``
(and ``ADJUST_SUGGESTED_ALLOCATION`` for lines that differ from the
suggestion; 409 when ``suggestion_unchanged`` shows the suggestion went
stale) — 403 ``station_permission_denied`` otherwise.
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.authorization import RequireAnyPermission, RequirePermission, StationDeviceDep
from app.api.dependencies import SessionDep
from app.api.user_refs import UserRefResponse, user_ref_response
from app.application import allocations, station_devices
from app.application.authentication import Principal
from app.domain.enums import Permission

router = APIRouter(prefix="/api")

AllocationEditorDep = Annotated[
    Principal, Depends(RequirePermission(Permission.EDIT_WORK_ORDER_ALLOCATION))
]
AllocationReaderDep = Annotated[
    Principal,
    Depends(
        RequireAnyPermission(Permission.VIEW_PRODUCTION_DATA, Permission.EDIT_WORK_ORDER_ALLOCATION)
    ),
]


class SuggestedLineResponse(BaseModel):
    work_order_id: int
    work_order_number: str | None
    received_date: datetime.date
    work_order_demand_id: int
    priority_rank: int | None
    due_date: datetime.date | None
    requested_quantity: int
    previously_allocated_quantity: int
    remaining_shortage: int
    proposed_quantity: int


class AllocationSuggestionResponse(BaseModel):
    part_number: str
    quantity: int
    stocked_quantity: int
    active_allocated_quantity: int
    available_stocked_quantity: int
    proposed_total: int
    # Quantity no outstanding demand can take: it stays in stock.
    unallocated_quantity: int
    lines: list[SuggestedLineResponse]


@router.get("/allocations/suggestion")
def get_allocation_suggestion(
    device: StationDeviceDep,
    session: SessionDep,
    part_number: str,
    quantity: int | None = None,
) -> AllocationSuggestionResponse:
    suggestion = allocations.suggest_station_allocation(
        session, part_number=part_number, quantity=quantity
    )
    return AllocationSuggestionResponse(
        part_number=suggestion.part_number,
        quantity=suggestion.quantity,
        stocked_quantity=suggestion.stocked_quantity,
        active_allocated_quantity=suggestion.active_allocated_quantity,
        available_stocked_quantity=suggestion.available_stocked_quantity,
        proposed_total=suggestion.proposed_total,
        unallocated_quantity=suggestion.unallocated_quantity,
        lines=[
            SuggestedLineResponse(
                work_order_id=line.work_order.id,
                work_order_number=line.work_order.work_order_number,
                received_date=line.work_order.received_date,
                work_order_demand_id=line.demand.id,
                priority_rank=line.demand.priority_rank,
                due_date=line.demand.due_date,
                requested_quantity=line.requested_quantity,
                previously_allocated_quantity=line.previously_allocated_quantity,
                remaining_shortage=line.remaining_shortage,
                proposed_quantity=line.proposed_quantity,
            )
            for line in suggestion.lines
        ],
    )


class AllocationLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_order_demand_id: int
    quantity: StrictInt


class AllocationRequest(BaseModel):
    """The Stockroom station's receiving confirmation."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # The explicit quantity being allocated — at the Stockroom the
    # just-stocked quantity the operator confirmed. The lines must sum
    # to exactly it; the available stocked quantity must still cover
    # it when the command is judged (a stale figure is refused).
    allocation_quantity: StrictInt
    lines: list[AllocationLineRequest]
    # The Stockroom station confirming the receiving allocation.
    station_id: str
    reason: str | None = None
    device_event_id: str
    # The client sends the suggestion it was shown, unmodified (Phase 14
    # slice 4): it only chooses which refusal explains lines that differ
    # from the suggestion recomputed under the locks — never part of the
    # idempotency fingerprint.
    suggestion_unchanged: StrictBool = False


class ManagementAllocationRequest(BaseModel):
    """A Management allocation of stocked quantity (no station, no actor field)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    allocation_quantity: StrictInt
    lines: list[AllocationLineRequest]
    reason: str | None = None
    device_event_id: str


class AllocationRowResponse(BaseModel):
    allocation_id: int
    work_order_demand_id: int
    work_order_id: int
    part_number: str
    quantity: int
    source: Literal["STOCKROOM", "MANAGEMENT"]
    is_manual_override: bool
    # Recorded by the authorized beyond-demand correction (Phase 14 slice 5).
    exceeds_demand: bool
    allocation_reason: str | None
    # Set on a reversal row: the allocation it takes back.
    reverses_allocation_id: int | None
    station_id: str | None
    actor_reference: str | None
    # The signed-in User of a Management allocation, reversal or correction.
    actor_user_id: int | None
    allocated_at: datetime.datetime
    command_sequence: int


AllocationKind = Literal["ALLOCATE", "REVERSE_ALLOCATION", "ALLOCATE_BEYOND_DEMAND"]

# Explicit: an unknown kind is a programming error (500), never silently
# reported as another kind.
_KINDS: dict[str, AllocationKind] = {
    "ALLOCATE": "ALLOCATE",
    "REVERSE_ALLOCATION": "REVERSE_ALLOCATION",
    allocations.ALLOCATE_BEYOND_DEMAND: "ALLOCATE_BEYOND_DEMAND",
}


class AllocationResponse(BaseModel):
    kind: AllocationKind
    part_number: str
    # The quantity allocated (or, on a reversal, taken back).
    allocation_quantity: int
    rows: list[AllocationRowResponse]
    # Work Orders this command completed (every line fully allocated)
    # or reopened (a reversal) — the derived completion effect.
    completed_work_order_ids: list[int]
    reopened_work_order_ids: list[int]
    device_event_id: str


def _row(row: allocations.AllocationRow) -> AllocationRowResponse:
    return AllocationRowResponse(
        allocation_id=row.allocation_id,
        work_order_demand_id=row.work_order_demand_id,
        work_order_id=row.work_order_id,
        part_number=row.part_number,
        quantity=row.quantity,
        source="STOCKROOM" if row.source == "STOCKROOM" else "MANAGEMENT",
        is_manual_override=row.is_manual_override,
        exceeds_demand=row.exceeds_demand,
        allocation_reason=row.allocation_reason,
        reverses_allocation_id=row.reverses_allocation_id,
        station_id=row.station_id,
        actor_reference=row.actor_reference,
        actor_user_id=row.actor_user_id,
        allocated_at=row.allocated_at,
        command_sequence=row.command_sequence,
    )


def _response(result: allocations.AllocationResult) -> AllocationResponse:
    return AllocationResponse(
        kind=_KINDS[result.kind],
        part_number=result.part_number,
        allocation_quantity=result.allocation_quantity,
        rows=[_row(row) for row in result.rows],
        completed_work_order_ids=result.completed_work_order_ids,
        reopened_work_order_ids=result.reopened_work_order_ids,
        device_event_id=result.device_event_id,
    )


@router.post("/allocations")
def confirm_allocation(
    device: StationDeviceDep, body: AllocationRequest, session: SessionDep, response: Response
) -> AllocationResponse:
    station_devices.require_station_binding(device, body.station_id)
    result = allocations.confirm_station_allocation(
        session,
        station_id=body.station_id,
        part_number=body.part_number,
        allocation_quantity=body.allocation_quantity,
        lines=[line.model_dump() for line in body.lines],
        reason=body.reason,
        device_event_id=body.device_event_id,
        suggestion_unchanged=body.suggestion_unchanged,
    )
    response.status_code = 201 if result.created else 200
    return _response(result)


@router.post("/allocations/management")
def allocate_from_stock(
    principal: AllocationEditorDep,
    body: ManagementAllocationRequest,
    session: SessionDep,
    response: Response,
) -> AllocationResponse:
    result = allocations.allocate_from_stock(
        session,
        actor_user_id=principal.user_id,
        part_number=body.part_number,
        allocation_quantity=body.allocation_quantity,
        lines=[line.model_dump() for line in body.lines],
        reason=body.reason,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return _response(result)


class AllocationReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    device_event_id: str


@router.post("/allocations/{allocation_id}/reversals")
def reverse_allocation(
    principal: AllocationEditorDep,
    allocation_id: int,
    body: AllocationReversalRequest,
    session: SessionDep,
    response: Response,
) -> AllocationResponse:
    result = allocations.reverse_allocation(
        session,
        allocation_id=allocation_id,
        reason=body.reason,
        device_event_id=body.device_event_id,
        actor_user_id=principal.user_id,
    )
    response.status_code = 201 if result.created else 200
    return _response(result)


class AllocationCorrectionRequest(BaseModel):
    """The authorized beyond-demand correction (no station, no actor, no flag)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    work_order_demand_id: StrictInt
    quantity: StrictInt
    reason: str
    device_event_id: str


@router.post("/allocations/corrections")
def allocate_beyond_demand(
    principal: AllocationEditorDep,
    body: AllocationCorrectionRequest,
    session: SessionDep,
    response: Response,
) -> AllocationResponse:
    result = allocations.allocate_beyond_demand(
        session,
        actor_user_id=principal.user_id,
        part_number=body.part_number,
        work_order_demand_id=body.work_order_demand_id,
        quantity=body.quantity,
        reason=body.reason,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return _response(result)


class ContextAllocationResponse(BaseModel):
    allocation_id: int
    quantity: int
    source: Literal["STOCKROOM", "MANAGEMENT"]
    is_manual_override: bool
    exceeds_demand: bool
    allocation_reason: str | None
    station_id: str | None
    allocated_at: datetime.datetime
    actor_user: UserRefResponse | None


class ContextLineResponse(BaseModel):
    work_order_id: int
    work_order_number: str | None
    work_order_completed: bool
    received_date: datetime.date
    work_order_demand_id: int
    request_type: Literal["NEW", "MODIFY"]
    due_date: datetime.date | None
    priority_rank: int | None
    requested_quantity: int
    # The derived active allocation (never the projection).
    allocated_quantity: int
    remaining_shortage: int
    beyond_demand_quantity: int
    # Oldest first.
    active_allocations: list[ContextAllocationResponse]


class AllocationContextResponse(BaseModel):
    part_number: str
    stocked_quantity: int
    active_allocated_quantity: int
    available_stocked_quantity: int
    # Canonical demand order.
    lines: list[ContextLineResponse]


def _context_line(line: allocations.ContextLine) -> ContextLineResponse:
    return ContextLineResponse(
        work_order_id=line.work_order.id,
        work_order_number=line.work_order.work_order_number,
        work_order_completed=line.work_order.completed_at is not None,
        received_date=line.work_order.received_date,
        work_order_demand_id=line.demand.id,
        request_type="NEW" if line.demand.request_type == "NEW" else "MODIFY",
        due_date=line.demand.due_date,
        priority_rank=line.demand.priority_rank,
        requested_quantity=line.demand.requested_quantity,
        allocated_quantity=line.allocated_quantity,
        remaining_shortage=line.remaining_shortage,
        beyond_demand_quantity=line.beyond_demand_quantity,
        active_allocations=[
            ContextAllocationResponse(
                allocation_id=entry.allocation.id,
                quantity=entry.allocation.quantity,
                source="STOCKROOM" if entry.allocation.source == "STOCKROOM" else "MANAGEMENT",
                is_manual_override=entry.allocation.is_manual_override,
                exceeds_demand=entry.allocation.exceeds_demand,
                allocation_reason=entry.allocation.allocation_reason,
                station_id=entry.allocation.station_id,
                allocated_at=entry.allocation.allocated_at,
                actor_user=user_ref_response(entry.actor_user),
            )
            for entry in line.active_allocations
        ],
    )


@router.get("/allocations/management/context")
def get_management_allocation_context(
    principal: AllocationEditorDep,
    session: SessionDep,
    part_number: str | None = None,
    work_order_demand_id: int | None = None,
) -> AllocationContextResponse:
    context = allocations.management_allocation_context(
        session, part_number=part_number, work_order_demand_id=work_order_demand_id
    )
    return AllocationContextResponse(
        part_number=context.part_number,
        stocked_quantity=context.position.stocked_quantity,
        active_allocated_quantity=context.position.active_allocated_quantity,
        available_stocked_quantity=context.position.available_stocked_quantity,
        lines=[_context_line(line) for line in context.lines],
    )


class AllocationRecordResponse(BaseModel):
    id: int
    part_number: str
    work_order_demand_id: int
    quantity: int
    source: str
    is_manual_override: bool
    exceeds_demand: bool
    allocation_reason: str | None
    reverses_allocation_id: int | None
    station_id: str | None
    actor_reference: str | None
    actor_user_id: int | None
    allocated_at: datetime.datetime
    device_event_id: str
    command_sequence: int


@router.get("/allocations")
def list_allocations(
    principal: AllocationReaderDep,
    session: SessionDep,
    part_number: str | None = None,
    work_order_demand_id: int | None = None,
    work_order_id: int | None = None,
) -> list[AllocationRecordResponse]:
    return [
        AllocationRecordResponse(
            id=row.id,
            part_number=row.part_number,
            work_order_demand_id=row.work_order_demand_id,
            quantity=row.quantity,
            source=row.source,
            is_manual_override=row.is_manual_override,
            exceeds_demand=row.exceeds_demand,
            allocation_reason=row.allocation_reason,
            reverses_allocation_id=row.reverses_allocation_id,
            station_id=row.station_id,
            actor_reference=row.actor_reference,
            actor_user_id=row.actor_user_id,
            allocated_at=row.allocated_at,
            device_event_id=row.device_event_id,
            command_sequence=row.command_sequence,
        )
        for row in allocations.list_allocations(
            session,
            part_number=part_number,
            work_order_demand_id=work_order_demand_id,
            work_order_id=work_order_id,
        )
    ]
