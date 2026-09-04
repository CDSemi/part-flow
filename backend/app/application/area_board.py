"""Area Board read model (Phase 11 — PROJECT_PROFILE §21, GUI_DESIGN §6).

The Management monitoring view of one Department read Area by Area:
the All Areas overview and the per-Area detail are TWO PRESENTATIONS OF
THIS ONE READ, never two reads that could disagree.

The per-Area content is the Area inventory the Scan Station renders —
`scan_station.area_inventory`, unchanged and unwrapped: the Area mode
(`has_machines`), every ACTIVE flow per PN with its derived holding
state, the Machine cards holding only actively assigned quantity, and
the queued / directly processing / finished groups with their totals.
The Area Board adds nothing to that model and reimplements none of it,
so the two views can never drift apart (PROJECT_PROFILE §21 "the same
shared Area/Machine monitoring layout ... without visual drift"). What
this module adds is the context a monitoring view shows around it and a
station does not have to load:

- the Area's active **Operations**, for the column and card headers;
- the **scrapped** quantity recorded in the Area per PN (net of
  reversed scraps) — the `{n} scrapped` line of the shared PN row;
- for a terminal Area (the Stockroom, Phase 10) the **stocked** lines:
  quantity is manufacturing-complete there and its flows are closed, so
  a terminal Area holds no ACTIVE inventory at all — its column would
  be permanently empty without them. Each line carries the PN's active
  allocation beside the stocked quantity, which is what the Stockroom
  row states in place of a due countdown (`allocated 50/50`).

Every value is derived from the current-position projection and the
immutable Movement history, exactly like the Production Board; nothing
here writes and no value is a stored counter. The per-flow monitoring
context the shared row presents — the entry timestamp its `Time in
Area` derives from, the Machine that completed finished quantity, and
the Work Order Demand's Job Numbers, due date and Hot rank — belongs to
the shared model itself (`scan_station.FlowInArea`), so the Scan
Station shows the same values for the same quantity.

Scope: the read reports fixed timestamps and dates only. Search,
sorting (due date / priority / time in Area / quantity) and the
overview/detail layout choice are presentation state of the view
(GUI_DESIGN §6.1) — there is no canonical Area Board order to own here,
unlike the Production Board's single canonical board order.
"""

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.allocations import active_allocated_quantities
from app.application.production_board import effective_totals_by_area, resolve_department
from app.application.scan_station import AreaInventory, area_inventory
from app.application.transfers import active_area_operations
from app.domain.enums import MovementType
from app.infrastructure.models import Area, Department, Operation


class StockedLine(NamedTuple):
    """One PN held in a terminal Area (Phase 10, PROJECT_PROFILE §18).

    ``quantity`` is the gross stocked quantity of the PN in THIS Area
    (the effective ``STOCKED`` Movements into it); ``allocated_quantity``
    is the PN's total ACTIVE allocation — Work Order Allocation is a PN
    -level connection to demand and is never divided per Area, so the
    Stockroom row states the two side by side instead of pretending the
    allocation belongs to one Area's stock.
    """

    part_number: str
    quantity: int
    allocated_quantity: int


class AreaBoardArea(NamedTuple):
    """One Area column / detail page."""

    inventory: AreaInventory
    operations: list[Operation]
    # Scrapped quantity recorded in this Area, per PN (net of reversed
    # scraps); a PN without scrap is absent.
    scrapped: dict[str, int]
    # Terminal Areas only; empty everywhere else.
    stocked: list[StockedLine]

    @property
    def area(self) -> Area:
        return self.inventory.area


class AreaBoard(NamedTuple):
    department: Department
    areas: list[AreaBoardArea]


def area_board(session: Session, department_id: int | None) -> AreaBoard:
    """Every Area of one Department with its monitoring content.

    The Department is resolved by the SAME rule the Production Board
    uses (`production_board.resolve_department`): an explicit id must
    exist, and without one a single active Department is used while an
    ambiguous configuration is refused — a monitoring view is never
    silently pointed at the wrong Department.

    Areas are the Department's ACTIVE ones, ordered by name (the tab
    strip and the column order). An inactive Area can never hold active
    quantity — the deactivation command refuses while it does
    (`app.application.environment`) — so nothing in production is
    hidden by that filter.
    """
    department = resolve_department(session, department_id)
    areas = list(
        session.scalars(
            select(Area)
            .where(Area.department_id == department.id, Area.is_active.is_(True))
            .order_by(Area.name, Area.id)
        )
    )
    area_ids = [area.id for area in areas]
    scrapped = effective_totals_by_area(session, MovementType.SCRAPPED, area_ids)
    stocked = effective_totals_by_area(
        session, MovementType.STOCKED, [area.id for area in areas if area.is_terminal]
    )
    allocated = active_allocated_quantities(
        session, {part_number for part_number, _area_id in stocked}
    )
    return AreaBoard(
        department=department,
        areas=[
            AreaBoardArea(
                inventory=area_inventory(session, area.id),
                operations=active_area_operations(session, area.id),
                scrapped={
                    part_number: quantity
                    for (part_number, scrapped_area_id), quantity in scrapped.items()
                    if scrapped_area_id == area.id
                },
                stocked=sorted(
                    (
                        StockedLine(
                            part_number=part_number,
                            quantity=quantity,
                            allocated_quantity=allocated.get(part_number, 0),
                        )
                        for (part_number, stocked_area_id), quantity in stocked.items()
                        if stocked_area_id == area.id
                    ),
                    key=lambda line: line.part_number,
                ),
            )
            for area in areas
        ],
    )
