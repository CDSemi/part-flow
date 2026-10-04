"""Hot list endpoints (Phase 12 — PROJECT_PROFILE §21 Priority Management; GUI_DESIGN §8).

Thin routes over `app.application.hot_list`: request schemas validate
shape only (``extra="forbid"``, strict integers), every rule and the
one-change-one-transaction protocol live in the Application layer, and
the central handlers in ``app.api.errors`` translate typed failures.

``priority_rank`` is written ONLY through ``POST /hot-list/changes``:
the Work Order intake rejects the field, and every other surface only
reads it.

Surface:

- ``GET  /hot-list`` — the Department and its Hot entries in rank
  order, inactive entries (completed Work Order, fully allocated line)
  included and flagged.
- ``GET  /hot-list/candidates?search=…`` | ``?barcode=…`` — eligible
  demand for the Add dialog (unranked, active demand of an open Work
  Order) in the canonical demand order. The two parameters are
  mutually exclusive (both is 422); a search, or no parameter at all,
  returns at most 50 rows and reports more as ``truncated``; a
  ``PF:PN:`` barcode returns every eligible demand of its PN.
- ``POST /hot-list/changes`` — ONE single-entry change (ADD at the
  bottom, REMOVE, MOVE_UP, MOVE_DOWN, DRAG, UNDO, REDO). 201 applied,
  200 on an idempotent replay of the same ``device_event_id`` + same
  change (the original ``changes``, the CURRENT ``entries`` — null when
  no single active Department exists any more, so a committed change
  still replays and the list is left to a fresh read); 409 with
  ``hot_list_changed: true`` and the current entries when
  ``expected_order`` is not the current order; 409 on a mismatched id
  reuse or an ineligible demand; 404 for a demand that no longer
  exists; 422 for anything that is not one single-entry change of the
  named action. Every refusal writes nothing.

Department contract: every route resolves the single active Department
— none is 404, several is 409 (only the replay of an already committed
change answers regardless, as above) — and there is deliberately no
``department_id`` parameter (the rank is one column per demand).

No authorization is enforced or simulated (Phase 14): no request
carries an actor, so the audit rows stay NULL.
"""

import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.dependencies import SessionDep
from app.application import hot_list
from app.application.hot_list import HotEntry, HotLocation
from app.application.production_board import LocationState
from app.domain.enums import RequestType
from app.domain.hot_list import HotListAction

router = APIRouter(prefix="/api")


class HotListAreaRef(BaseModel):
    id: int
    name: str
    color: str | None


class HotListMachineRef(BaseModel):
    id: int
    name: str


class HotListLocationResponse(BaseModel):
    area: HotListAreaRef
    machine: HotListMachineRef | None
    # The external Operation's name, as on the Production Board.
    activity: str | None
    # MACHINE / QUEUE / PROCESSING / DONE — active quantity only.
    state: LocationState
    quantity: int


class HotListEntryResponse(BaseModel):
    work_order_demand_id: int
    # Null only in a candidate list.
    rank: int | None
    part_number: str
    work_order_id: int
    # Null = internal Work Order (rendered `—` with its label).
    work_order_number: str | None
    work_order_received_date: datetime.date
    work_order_completed: bool
    request_type: RequestType
    job_numbers: list[str]
    requested_quantity: int
    allocated_quantity: int
    shortage_quantity: int
    # Active demand of an open Work Order (PROJECT_PROFILE §14).
    active: bool
    released_quantity: int
    due_date: datetime.date | None
    # The PN's ACTIVE quantity in the Department's Areas — PN-level,
    # identical for every demand of the PN.
    part_number_locations: list[HotListLocationResponse]


class HotListDepartmentRef(BaseModel):
    id: int
    name: str


class HotListResponse(BaseModel):
    department: HotListDepartmentRef
    entries: list[HotListEntryResponse]


class HotListCandidatesResponse(BaseModel):
    # The PN a barcode named; null for a search.
    part_number: str | None
    candidates: list[HotListEntryResponse]
    already_listed_count: int
    truncated: bool


class HotListChangeRequest(BaseModel):
    """One confirmed single-entry change of the Hot list."""

    model_config = ConfigDict(extra="forbid")

    device_event_id: str
    action: HotListAction
    # Demand ids in the rank order the manager saw (may be empty).
    expected_order: list[StrictInt]
    new_order: list[StrictInt]


class HotListChangeLineResponse(BaseModel):
    work_order_demand_id: int
    part_number: str
    work_order_number: str | None
    previous_rank: int | None
    new_rank: int | None


class HotListChangeResponse(BaseModel):
    device_event_id: str
    action: HotListAction
    created: bool
    changes: list[HotListChangeLineResponse]
    # The committed list (201) or the current list (replay); null only for
    # a replay while no single active Department exists — read the list.
    entries: list[HotListEntryResponse] | None


def _location(location: HotLocation) -> HotListLocationResponse:
    return HotListLocationResponse(
        area=HotListAreaRef(
            id=location.area_id, name=location.area_name, color=location.area_color
        ),
        machine=(
            HotListMachineRef(id=location.machine_id, name=location.machine_name)
            if location.machine_id is not None and location.machine_name is not None
            else None
        ),
        activity=location.activity,
        state=location.state,
        quantity=location.quantity,
    )


def entry_response(entry: HotEntry) -> HotListEntryResponse:
    """One entry on the wire — shared with the stale-change 409 body."""
    return HotListEntryResponse(
        work_order_demand_id=entry.work_order_demand_id,
        rank=entry.rank,
        part_number=entry.part_number,
        work_order_id=entry.work_order_id,
        work_order_number=entry.work_order_number,
        work_order_received_date=entry.work_order_received_date,
        work_order_completed=entry.work_order_completed,
        request_type=entry.request_type,
        job_numbers=entry.job_numbers,
        requested_quantity=entry.requested_quantity,
        allocated_quantity=entry.allocated_quantity,
        shortage_quantity=entry.shortage_quantity,
        active=entry.active,
        released_quantity=entry.released_quantity,
        due_date=entry.due_date,
        part_number_locations=[_location(location) for location in entry.part_number_locations],
    )


@router.get("/hot-list")
def get_hot_list(session: SessionDep) -> HotListResponse:
    result = hot_list.hot_list(session)
    return HotListResponse(
        department=HotListDepartmentRef(id=result.department.id, name=result.department.name),
        entries=[entry_response(entry) for entry in result.entries],
    )


@router.get("/hot-list/candidates")
def get_hot_list_candidates(
    session: SessionDep, search: str | None = None, barcode: str | None = None
) -> HotListCandidatesResponse:
    result = hot_list.hot_list_candidates(session, search=search, barcode=barcode)
    return HotListCandidatesResponse(
        part_number=result.part_number,
        candidates=[entry_response(entry) for entry in result.candidates],
        already_listed_count=result.already_listed_count,
        truncated=result.truncated,
    )


@router.post("/hot-list/changes")
def apply_hot_list_change(
    body: HotListChangeRequest, session: SessionDep, response: Response
) -> HotListChangeResponse:
    result = hot_list.apply_hot_list_change(
        session,
        device_event_id=body.device_event_id,
        action=body.action,
        expected_order=body.expected_order,
        new_order=body.new_order,
    )
    response.status_code = 201 if result.created else 200
    return HotListChangeResponse(
        device_event_id=result.device_event_id,
        action=result.action,
        created=result.created,
        changes=[
            HotListChangeLineResponse(
                work_order_demand_id=line.work_order_demand_id,
                part_number=line.part_number,
                work_order_number=line.work_order_number,
                previous_rank=line.previous_rank,
                new_rank=line.new_rank,
            )
            for line in result.changes
        ],
        entries=(
            [entry_response(entry) for entry in result.entries]
            if result.entries is not None
            else None
        ),
    )
