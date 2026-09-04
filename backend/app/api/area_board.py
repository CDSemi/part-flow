"""Area Board endpoint (Phase 11 — GUI_DESIGN §6; PROJECT_PROFILE §21).

``GET /area-board`` — one read for the whole Management view
(`app.application.area_board`): every active Area of the Department
with the SHARED Area monitoring shape (`app.api.area_inventory` — the
same contract `GET /areas/{id}/inventory` answers the Scan Station
with), its active Operations, the scrapped quantity per PN, and, for a
terminal Area, the stocked lines with their active allocation.

The All Areas overview and the per-Area detail are two presentations of
this one answer, so switching tabs never re-reads and the two can never
show different quantities. ``department_id`` selects the Department;
omitted, the single active Department is used — none is 404, several is
409 naming them.

A read: nothing is written, and every derived time value (the dwell
time, the due countdown) is left to the view's shared clock — the
response carries only the fixed source timestamps and dates.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.area_inventory import (
    AreaInventoryResponse,
    OperationRef,
    area_inventory_response,
    operation_ref,
)
from app.api.dependencies import SessionDep
from app.application import area_board

router = APIRouter(prefix="/api")


class StockedLineResponse(BaseModel):
    """One PN held in a terminal Area, with its active allocation."""

    part_number: str
    quantity: int
    allocated_quantity: int


class ScrappedLineResponse(BaseModel):
    part_number: str
    quantity: int


class AreaBoardAreaResponse(BaseModel):
    # The shared Area monitoring model, identical to the Scan Station's.
    inventory: AreaInventoryResponse
    operations: list[OperationRef]
    scrapped: list[ScrappedLineResponse]
    # Terminal Areas only (Stockroom): manufacturing-complete quantity
    # whose flows are closed, so it is never part of the inventory.
    stocked: list[StockedLineResponse]


class AreaBoardDepartmentRef(BaseModel):
    id: int
    name: str


class AreaBoardResponse(BaseModel):
    department: AreaBoardDepartmentRef
    areas: list[AreaBoardAreaResponse]


def _area(entry: area_board.AreaBoardArea) -> AreaBoardAreaResponse:
    return AreaBoardAreaResponse(
        inventory=area_inventory_response(entry.inventory),
        operations=[operation_ref(operation) for operation in entry.operations],
        scrapped=[
            ScrappedLineResponse(part_number=part_number, quantity=quantity)
            for part_number, quantity in sorted(entry.scrapped.items())
        ],
        stocked=[
            StockedLineResponse(
                part_number=line.part_number,
                quantity=line.quantity,
                allocated_quantity=line.allocated_quantity,
            )
            for line in entry.stocked
        ],
    )


@router.get("/area-board")
def get_area_board(session: SessionDep, department_id: int | None = None) -> AreaBoardResponse:
    board = area_board.area_board(session, department_id)
    return AreaBoardResponse(
        department=AreaBoardDepartmentRef(id=board.department.id, name=board.department.name),
        areas=[_area(entry) for entry in board.areas],
    )
