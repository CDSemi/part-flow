"""Production Board endpoint (Phase 11 — GUI_DESIGN §5; PROJECT_PROFILE §21).

``GET /production-board`` — the Department-wide board read model
(`app.application.production_board`): the rows in the canonical board
order with their distributed quantity per Area / Machine / External
activity, the fixed entry timestamps the display derives dwell times
from, the stocked and scrapped quantities, the OPEN demand context (Work
Order Number, Job Numbers, requested / allocated quantity) and the
Hot rank, plus the Department totals of the footer. ``department_id``
selects the Department; omitted, the single active Department is
used — none is 404, several is 409 naming them (a display is never
silently pointed at the wrong Department). A read: nothing is
written, and every derived time value (dwell, due countdown, Total
Days) is left to the display's shared clock — the response carries
only the fixed source timestamps and dates.
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import SessionDep
from app.application import production_board
from app.application.production_board import LocationState

router = APIRouter(prefix="/api")


class BoardAreaRef(BaseModel):
    id: int
    name: str
    color: str | None
    is_terminal: bool


class BoardMachineRef(BaseModel):
    id: int
    name: str


class BoardLocationResponse(BaseModel):
    area: BoardAreaRef
    # The executor of MACHINE quantity; on DONE quantity the completing
    # Machine as secondary context only.
    machine: BoardMachineRef | None
    # The external Operation's name for quantity out at an external
    # activity (rendered in place of the generic processing label).
    activity: str | None
    quantity: int
    state: LocationState
    # When the oldest portion of this group entered its position; null
    # where elapsed time does not apply (STOCKED).
    since: datetime.datetime | None


class BoardDemandResponse(BaseModel):
    work_order_id: int
    work_order_number: str | None
    work_order_demand_id: int
    request_type: str
    requested_quantity: int
    allocated_quantity: int
    job_numbers: list[str]
    due_date: datetime.date | None
    priority_rank: int | None


class BoardRowResponse(BaseModel):
    part_number: str
    hot_rank: int | None
    due_date: datetime.date | None
    received_date: datetime.date
    locations: list[BoardLocationResponse]
    active_quantity: int
    stocked_quantity: int
    scrapped_quantity: int
    total_quantity: int
    demands: list[BoardDemandResponse]


class BoardDepartmentRef(BaseModel):
    id: int
    name: str


class ProductionBoardResponse(BaseModel):
    department: BoardDepartmentRef
    rows: list[BoardRowResponse]
    # Footer totals: PNs with active quantity, active / stocked /
    # scrapped quantity of the rows shown.
    active_part_numbers: int
    active_quantity: int
    stocked_quantity: int
    scrapped_quantity: int


def _location(location: production_board.BoardLocation) -> BoardLocationResponse:
    return BoardLocationResponse(
        area=BoardAreaRef(
            id=location.area.id,
            name=location.area.name,
            color=location.area.color,
            is_terminal=location.area.is_terminal,
        ),
        machine=(
            BoardMachineRef(id=location.machine.id, name=location.machine.name)
            if location.machine is not None
            else None
        ),
        activity=location.activity,
        quantity=location.quantity,
        state=location.state,
        since=location.since,
    )


def _demand(entry: production_board.BoardDemand) -> BoardDemandResponse:
    return BoardDemandResponse(
        work_order_id=entry.work_order.id,
        work_order_number=entry.work_order.work_order_number,
        work_order_demand_id=entry.demand.id,
        request_type=entry.demand.request_type,
        requested_quantity=entry.demand.requested_quantity,
        allocated_quantity=entry.demand.allocated_quantity,
        job_numbers=list(entry.demand.job_numbers),
        due_date=entry.demand.due_date,
        priority_rank=entry.demand.priority_rank,
    )


def _row(row: production_board.BoardRow) -> BoardRowResponse:
    return BoardRowResponse(
        part_number=row.part_number,
        hot_rank=row.hot_rank,
        due_date=row.due_date,
        received_date=row.received_date,
        locations=[_location(location) for location in row.locations],
        active_quantity=row.active_quantity,
        stocked_quantity=row.stocked_quantity,
        scrapped_quantity=row.scrapped_quantity,
        total_quantity=row.total_quantity,
        demands=[_demand(entry) for entry in row.demands],
    )


@router.get("/production-board")
def get_production_board(
    session: SessionDep, department_id: int | None = None
) -> ProductionBoardResponse:
    board = production_board.production_board(session, department_id)
    return ProductionBoardResponse(
        department=BoardDepartmentRef(id=board.department.id, name=board.department.name),
        rows=[_row(row) for row in board.rows],
        active_part_numbers=board.active_part_numbers,
        active_quantity=board.active_quantity,
        stocked_quantity=board.stocked_quantity,
        scrapped_quantity=board.scrapped_quantity,
    )
