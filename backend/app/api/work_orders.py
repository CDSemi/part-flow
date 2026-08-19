"""Work Order intake endpoints (Phase 4 — Work Orders view, demand save).

HTTP surface for the Work Orders management view (GUI_DESIGN
§11.1–§11.3): listing/searching the WO list, loading Work Order
Details with demand lines, creating a Work Order with its demand
draft, saving edits, and removing demand lines. Business demand only —
production release lives on its own surface
(``app.api.production_release``) and no endpoint here can touch
QuantityFlows, PartMovements, or the current-position projection.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"`` — a client that submits a server-owned field such
as ``status``, ``allocated_quantity``, or ``priority_rank`` is
rejected instead of silently ignored), the Application layer owns
every business rule and the one-save-one-transaction protocol, and
the central handlers in ``app.api.errors`` translate typed failures.

Deliberate surface decisions:

- ``work_order_number`` is nullable in requests and responses: blank
  confirmed input persists NULL (the UI renders ``—``); an entered
  number travels and is stored verbatim. ``GET /work-orders?number=``
  is the exact-resolution lookup the New Work Order dialog uses to
  open an existing number instead of duplicating it; a POST that
  still carries an existing number is a 409 that creates nothing.
- One Save = one request = one transaction: ``POST /work-orders``
  saves the New Work Order dialog (header + all lines), ``PATCH
  /work-orders/{id}`` saves the Work Order Details draft (header
  edits + ``line_edits`` + ``new_lines``) — each all-or-nothing with
  its audit rows.
- ``DELETE /work-orders/{id}/demands/{id}`` enforces the canonical
  removal rules (PROJECT_PROFILE §13, §8.2) in the backend, never
  only in the UI: a saved demand deletes only while no production
  quantity has ever been released for it, and the last demand line
  of a Work Order is never removable (a Work Order contains one or
  more demand records; nothing auto-deletes the Work Order) — either
  violation is a 409 that removes nothing. There is still no
  Completed Work Orders surface (Phase 10+).
- ``priority_rank`` and ``allocated_quantity`` appear only in
  responses: Hot ranking (Phase 12) and allocation (Phase 10) own
  those values.
- The audit ``actor_reference`` is never client-writable: no request
  carries an actor, so audit rows from this surface stay NULL until an
  authenticated (or server-configured) identity exists (Phase 14).
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.api.dependencies import SessionDep
from app.application import work_orders
from app.application.common import UNSET
from app.application.work_orders import WorkOrderDetail, WorkOrderSummary
from app.domain.enums import RequestType

router = APIRouter(prefix="/api")


class WorkOrderDemandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    work_order_id: int
    # Canonical uppercase PN, kept by the demand itself (no FK to the
    # optional master).
    part_number: str
    request_type: RequestType
    requested_quantity: int
    allocated_quantity: int
    due_date: datetime.date | None
    priority_rank: int | None
    job_numbers: list[str]
    requester: str | None
    reason: str | None
    notes: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WorkOrderSummaryResponse(BaseModel):
    id: int
    # NULL = internal Work Order without an external number; the UI
    # renders `—` (display-only, never persisted).
    work_order_number: str | None
    received_date: datetime.date
    due_date: datetime.date | None
    status: str
    demand_line_count: int
    part_numbers: list[str]


class WorkOrderDetailResponse(BaseModel):
    id: int
    work_order_number: str | None
    received_date: datetime.date
    due_date: datetime.date | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    demands: list[WorkOrderDemandResponse]


class WorkOrderDemandCreateRequest(BaseModel):
    """One new demand line (Add Part flow, GUI_DESIGN §11.3)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # Strict: a quantity is an integer, never a coerced bool/float/text
    # — uncertain input is rejected, not reinterpreted.
    requested_quantity: StrictInt
    # Manual entry defaults to NEW in the Application layer.
    request_type: RequestType | None = None
    due_date: datetime.date | None = None
    job_numbers: list[str] = Field(default_factory=list)
    requester: str | None = None
    reason: str | None = None
    notes: str | None = None


class WorkOrderDemandEditRequest(BaseModel):
    """A partial edit of one saved demand line.

    The PN is deliberately absent: a different PN is a new line, never
    a rewrite of a saved one. Omitted fields keep their values; an
    explicit ``null`` due date is the valid "No due date" choice.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    request_type: RequestType | None = None
    requested_quantity: StrictInt | None = None
    due_date: datetime.date | None = None
    job_numbers: list[str] | None = None
    requester: str | None = None
    reason: str | None = None
    notes: str | None = None


class WorkOrderCreateRequest(BaseModel):
    """The New Work Order dialog's Save demand (GUI_DESIGN §11.3)."""

    model_config = ConfigDict(extra="forbid")

    # Blank/omitted persists NULL on an internal Work Order — no
    # temporary number is ever generated; an entered value is verbatim.
    work_order_number: str | None = None
    # Defaults to the current date during manual creation.
    received_date: datetime.date | None = None
    due_date: datetime.date | None = None
    lines: list[WorkOrderDemandCreateRequest]


class WorkOrderUpdateRequest(BaseModel):
    """The Work Order Details dialog's Save demand (GUI_DESIGN §11.2)."""

    model_config = ConfigDict(extra="forbid")

    # The audited Work Order Number edit (PROJECT_PROFILE §7): an
    # internal Work Order may receive its real external number later.
    work_order_number: str | None = None
    due_date: datetime.date | None = None
    line_edits: list[WorkOrderDemandEditRequest] = Field(default_factory=list)
    new_lines: list[WorkOrderDemandCreateRequest] = Field(default_factory=list)


def _summary_response(summary: WorkOrderSummary) -> WorkOrderSummaryResponse:
    work_order = summary.work_order
    return WorkOrderSummaryResponse(
        id=work_order.id,
        work_order_number=work_order.work_order_number,
        received_date=work_order.received_date,
        due_date=work_order.due_date,
        status=work_order.status,
        demand_line_count=summary.demand_line_count,
        part_numbers=summary.part_numbers,
    )


def _detail_response(detail: WorkOrderDetail) -> WorkOrderDetailResponse:
    work_order = detail.work_order
    return WorkOrderDetailResponse(
        id=work_order.id,
        work_order_number=work_order.work_order_number,
        received_date=work_order.received_date,
        due_date=work_order.due_date,
        status=work_order.status,
        created_at=work_order.created_at,
        updated_at=work_order.updated_at,
        demands=[WorkOrderDemandResponse.model_validate(demand) for demand in detail.demands],
    )


@router.get("/work-orders")
def list_work_orders(
    session: SessionDep, search: str | None = None, number: str | None = None
) -> list[WorkOrderSummaryResponse]:
    return [
        _summary_response(summary)
        for summary in work_orders.list_work_orders(session, search=search, number=number)
    ]


@router.get("/work-orders/{work_order_id}")
def get_work_order(work_order_id: int, session: SessionDep) -> WorkOrderDetailResponse:
    return _detail_response(work_orders.get_work_order(session, work_order_id))


@router.post("/work-orders", status_code=201)
def create_work_order(body: WorkOrderCreateRequest, session: SessionDep) -> WorkOrderDetailResponse:
    detail = work_orders.create_work_order(
        session,
        work_order_number=body.work_order_number,
        received_date=body.received_date,
        due_date=body.due_date,
        lines=[line.model_dump() for line in body.lines],
    )
    return _detail_response(detail)


@router.patch("/work-orders/{work_order_id}")
def update_work_order(
    work_order_id: int, body: WorkOrderUpdateRequest, session: SessionDep
) -> WorkOrderDetailResponse:
    provided = body.model_dump(exclude_unset=True)
    detail = work_orders.update_work_order(
        session,
        work_order_id,
        # Omitted keeps the current value; an explicit null (or blank)
        # persists NULL — both are audited edits.
        work_order_number=provided.get("work_order_number", UNSET),
        due_date=provided.get("due_date", UNSET),
        line_edits=[edit.model_dump(exclude_unset=True) for edit in body.line_edits],
        new_lines=[line.model_dump() for line in body.new_lines],
    )
    return _detail_response(detail)


@router.delete("/work-orders/{work_order_id}/demands/{demand_id}", status_code=204)
def delete_work_order_demand(work_order_id: int, demand_id: int, session: SessionDep) -> None:
    """Remove one saved demand line (PROJECT_PROFILE §13, §8.2).

    Blocked with 409 once any quantity for the demand has been
    released, and blocked with 409 for the Work Order's last demand
    line; removal never cascades to the PartNumber master,
    QuantityFlows, PartMovements, release history, or other demand
    lines for the same PN.
    """
    work_orders.delete_work_order_demand(session, work_order_id, demand_id)
