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
- ``POST /allocations`` — the confirmed allocation of one PN's stocked
  quantity to demand lines (the receiving confirmation with
  ``station_id`` set — a Stockroom station; a Management allocation
  without it). 201 fresh, 200 on an idempotent replay of the same
  ``device_event_id`` + same intent, 422 when the lines do not sum to
  the explicit ``allocation_quantity`` (or when it is missing), 409 on
  a mismatched reuse, on a line beyond its remaining shortage, or on an
  allocation quantity the available stocked quantity no longer covers
  (a stale figure) — every refusal writes nothing.
- ``POST /allocations/{allocation_id}/reversals`` — the auditable
  adjustment: takes one allocation back with a mandatory reason (a
  smaller allocation is a reversal plus a new confirmation). 201 /
  200 / 409 as above; 409 when already reversed (also under a race).
- ``GET  /allocations?part_number=…&work_order_demand_id=…&work_order_id=…``
  — the allocation rows (allocations and reversals) for audit display.

No authorization is enforced or simulated (Phase 14): the ``actor``
field is never client-writable — audit rows stay NULL until an
authenticated identity exists.
"""

import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.dependencies import SessionDep
from app.application import allocations

router = APIRouter(prefix="/api")


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
    """The confirmed allocation (the receiving confirmation, or Management)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # The explicit quantity being allocated — at the Stockroom the
    # just-stocked quantity the operator confirmed. The lines must sum
    # to exactly it; the available stocked quantity must still cover
    # it when the command is judged (a stale figure is refused).
    allocation_quantity: StrictInt
    lines: list[AllocationLineRequest]
    # The Stockroom station confirming the receiving allocation; omitted
    # for a Management allocation.
    station_id: str | None = None
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
    result = allocations.confirm_allocation(
        session,
        part_number=body.part_number,
        allocation_quantity=body.allocation_quantity,
        lines=[line.model_dump() for line in body.lines],
        station_id=body.station_id,
        reason=body.reason,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return _response(result)


class AllocationReversalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    station_id: str | None = None
    device_event_id: str


@router.post("/allocations/{allocation_id}/reversals")
def reverse_allocation(
    allocation_id: int,
    body: AllocationReversalRequest,
    session: SessionDep,
    response: Response,
) -> AllocationResponse:
    result = allocations.reverse_allocation(
        session,
        allocation_id=allocation_id,
        reason=body.reason,
        station_id=body.station_id,
        device_event_id=body.device_event_id,
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
    allocated_at: datetime.datetime
    device_event_id: str
    command_sequence: int


@router.get("/allocations")
def list_allocations(
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
