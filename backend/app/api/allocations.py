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
  never resolves the User principal. 201 fresh, 200 on an idempotent
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
- ``GET  /allocations?part_number=…&work_order_demand_id=…&work_order_id=…``
  — the allocation rows (allocations and reversals) for audit display;
  needs View production data or Edit Work Order Allocation.

The acting User (``actor_user_id``) is derived from the session, never
from a request body (Phase 14 slice 3); station rows carry none.
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.authorization import RequireAnyPermission, RequirePermission
from app.api.dependencies import SessionDep
from app.application import allocations
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
    session: SessionDep, part_number: str, quantity: int | None = None
) -> AllocationSuggestionResponse:
    suggestion = allocations.suggest_allocation(session, part_number=part_number, quantity=quantity)
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
    allocation_reason: str | None
    # Set on a reversal row: the allocation it takes back.
    reverses_allocation_id: int | None
    station_id: str | None
    actor_reference: str | None
    # The signed-in User of a Management allocation or reversal.
    actor_user_id: int | None
    allocated_at: datetime.datetime
    command_sequence: int


class AllocationResponse(BaseModel):
    kind: Literal["ALLOCATE", "REVERSE_ALLOCATION"]
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
        kind="ALLOCATE" if result.kind == "ALLOCATE" else "REVERSE_ALLOCATION",
        part_number=result.part_number,
        allocation_quantity=result.allocation_quantity,
        rows=[_row(row) for row in result.rows],
        completed_work_order_ids=result.completed_work_order_ids,
        reopened_work_order_ids=result.reopened_work_order_ids,
        device_event_id=result.device_event_id,
    )


@router.post("/allocations")
def confirm_allocation(
    body: AllocationRequest, session: SessionDep, response: Response
) -> AllocationResponse:
    result = allocations.confirm_station_allocation(
        session,
        station_id=body.station_id,
        part_number=body.part_number,
        allocation_quantity=body.allocation_quantity,
        lines=[line.model_dump() for line in body.lines],
        reason=body.reason,
        device_event_id=body.device_event_id,
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


class AllocationRecordResponse(BaseModel):
    id: int
    part_number: str
    work_order_demand_id: int
    quantity: int
    source: str
    is_manual_override: bool
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
