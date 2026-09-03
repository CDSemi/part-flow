"""Production Board read model (Phase 11 — PROJECT_PROFILE §21, GUI_DESIGN §5).

The Department-wide, read-only large-display board: every Part Number
the Department is working on, with its current quantity distribution
across Areas and Machines, the time each distributed portion has been
in its current position, the stocked and scrapped quantities, the Work
Order context (Work Order Number, Job Numbers, requested / allocated
quantity), the due and received dates the display derives its
countdown and `Total Days` from, and the Hot rank — in the canonical
board order. There is no per-Area mode (v18 — decided): per-Area
monitoring is the Area Board's responsibility.

Everything is derived from the current-position projection and the
immutable Movement history — nothing here writes, and no value is a
stored counter:

- **Active quantity** is every ACTIVE QuantityFlow whose current Area
  belongs to the Department. Its holding state (ON_MACHINE / QUEUED /
  PROCESSING / READY_TO_TRANSFER) comes from the flow's effective
  latest position-bearing Movement and the mode of its Area
  (`projections.processing_state_of` — the same derivation every Scan
  Station read model uses), the Machine from that Movement (the
  executor of ON_MACHINE quantity; on finished quantity only the
  completing Machine as secondary context), and the **entry time**
  (`since`) is that Movement's `occurred_at`: a split child inherits
  its parent's entry through the lineage, an undone command restores
  the entry time of the state it restored. The board groups quantity
  per (Area, state, Machine, External activity) and shows the OLDEST
  entry of a group, so an unusually long stay is never hidden by a
  newer portion.
- **External activity**: quantity whose recorded Operation is
  external (`Operation.is_external`) carries the Operation's name as
  its activity — the display shows it in place of the generic
  processing label. The Area text stays the Area's own name.
- **Stocked quantity** is the sum of the effective `STOCKED` Movements
  into the Department's terminal Areas, per Area — manufacturing-
  complete quantity physically held in the Stockroom (Phase 10) — and
  is presented as a location without an entry time.
- **Scrapped quantity** is the sum of the effective `SCRAPPED`
  Movements recorded in the Department's Areas (net of reversed
  scraps), per PN.
- **Demand context**: the PN's OPEN Work Order Demands (the Work Order
  not completed, PROJECT_PROFILE §18) plus the demands that released
  the active flows (from the `RECEIVED` context — a finished Work Order
  whose quantity is still in production stays named), in the canonical
  demand order (`allocations.canonical_demand_order`: Hot rank, dated
  earliest first, undated by the Work Order's received date, the
  demand id as the deterministic tie-breaker). The FIRST demand of a
  row defines its Hot rank, due date and received date — the values
  the board sorts by and derives `N days left` / `Total Days` from. A
  row whose quantity has no demand context at all (a Phase 9 quantity
  addition, a merge across demands) keeps a null due date and takes
  its received date from the day its oldest active flow was created
  on the site calendar, so `Total Days` still has a meaning.
- **Row selection**: a PN with active quantity in the Department is
  always a row. A PN with NO active quantity is a row only while it has
  stocked quantity in the Department AND an open demand — stocked
  quantity waiting for (or partially) allocated to open work; once its
  every Work Order is complete, the PN leaves the board.
- **Board order** (`board_row_sort_key`): rows with active quantity
  first, rows that are entirely stocked last (completed demand, a
  presentation choice — not a business rule), each group in the
  canonical demand order of its defining demand; a row without demand
  context sorts after every row with one, by PN.

Time in location versus the expected duration of the active Route
Step (PROJECT_PROFILE §21 "may be highlighted") is not judged here —
the display flags an unusually long stay from `since` alone.
"""

import datetime
from collections.abc import Iterable
from typing import Final, Literal, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.application.allocations import canonical_demand_order
from app.application.errors import ConflictError, NotFoundError
from app.application.machines import areas_with_machines
from app.application.projections import effective_latest_movements, processing_state_of
from app.application.scan_station import work_order_contexts
from app.application.work_orders import site_timezone
from app.domain.enums import MovementType, ProcessingState, QuantityFlowStatus
from app.infrastructure.models import (
    Area,
    Department,
    Machine,
    Operation,
    PartMovement,
    QuantityFlow,
    WorkOrder,
    WorkOrderDemand,
)

LocationState = Literal["MACHINE", "QUEUE", "PROCESSING", "DONE", "STOCKED"]

# Presentation order of the states inside one Area (GUI_DESIGN §5).
_STATE_ORDER: Final[dict[str, int]] = {
    "MACHINE": 0,
    "QUEUE": 1,
    "PROCESSING": 2,
    "DONE": 3,
    "STOCKED": 4,
}

_LOCATION_STATE_OF: Final[dict[ProcessingState, LocationState]] = {
    ProcessingState.ON_MACHINE: "MACHINE",
    ProcessingState.QUEUED: "QUEUE",
    ProcessingState.PROCESSING: "PROCESSING",
    ProcessingState.READY_TO_TRANSFER: "DONE",
}


class BoardLocation(NamedTuple):
    """One distributed quantity position of a PN on the board."""

    area: Area
    # The executor of MACHINE quantity; on DONE quantity only the
    # completing Machine as secondary context (never the executor).
    machine: Machine | None
    # The external Operation the quantity is out for (its name), when
    # its recorded Operation is external.
    activity: str | None
    quantity: int
    state: LocationState
    # When the OLDEST portion of this group entered its position — None
    # where elapsed time does not apply (stocked quantity).
    since: datetime.datetime | None


class BoardDemand(NamedTuple):
    demand: WorkOrderDemand
    work_order: WorkOrder


class BoardRow(NamedTuple):
    part_number: str
    hot_rank: int | None
    due_date: datetime.date | None
    received_date: datetime.date
    locations: list[BoardLocation]
    active_quantity: int
    stocked_quantity: int
    scrapped_quantity: int
    # The demand context in canonical order; the first entry defines
    # the row's Hot rank, due date and received date.
    demands: list[BoardDemand]

    @property
    def total_quantity(self) -> int:
        return self.active_quantity + self.stocked_quantity

    @property
    def is_stocked_only(self) -> bool:
        return self.active_quantity == 0


class ProductionBoard(NamedTuple):
    department: Department
    rows: list[BoardRow]
    active_part_numbers: int
    active_quantity: int
    stocked_quantity: int
    scrapped_quantity: int


# ---------------------------------------------------------------------------
# Department resolution
# ---------------------------------------------------------------------------


def resolve_department(session: Session, department_id: int | None) -> Department:
    """The Department the board shows.

    An explicit id must exist. Without one the board is unambiguous
    only for a single active Department (the initial single-Department
    deployment, PROJECT_PROFILE §22): none → 404, several → 409 naming
    them, so a display is never silently pointed at the wrong
    Department.
    """
    if department_id is not None:
        department = session.get(Department, department_id)
        if department is None:
            raise NotFoundError(f"Department {department_id} does not exist.")
        return department
    active = list(
        session.scalars(
            select(Department)
            .where(Department.is_active.is_(True))
            .order_by(Department.name, Department.id)
        )
    )
    if not active:
        raise NotFoundError("No active Department is configured.")
    if len(active) > 1:
        names = ", ".join(f"{department.name} (id {department.id})" for department in active)
        raise ConflictError(
            f"Several active Departments exist ({names}). Open the board for one Department"
            " with its department_id."
        )
    return active[0]


# ---------------------------------------------------------------------------
# History-derived quantities
# ---------------------------------------------------------------------------


def _effective_totals_by_area(
    session: Session, movement_type: MovementType, area_ids: Iterable[int]
) -> dict[tuple[str, int], int]:
    """Σ quantity of the effective Movements of one type per (PN, Area)."""
    wanted = list(area_ids)
    if not wanted:
        return {}
    reversal = aliased(PartMovement)
    rows = session.execute(
        select(
            PartMovement.part_number,
            PartMovement.to_area_id,
            func.sum(PartMovement.quantity),
        )
        .where(
            PartMovement.movement_type == movement_type,
            PartMovement.to_area_id.in_(wanted),
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
        .group_by(PartMovement.part_number, PartMovement.to_area_id)
    )
    return {(str(pn), int(area_id)): int(total) for pn, area_id, total in rows}


def _demand_context(
    session: Session, part_numbers: set[str], flow_ids: list[int]
) -> dict[str, list[BoardDemand]]:
    """The demands per PN in canonical order: open demand plus the
    demands that released the active flows."""
    if not part_numbers:
        return {}
    released_demand_ids = {
        context.work_order_demand_id for context in work_order_contexts(session, flow_ids).values()
    }
    query = (
        select(WorkOrderDemand, WorkOrder)
        .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        .where(
            WorkOrderDemand.part_number.in_(part_numbers),
            (WorkOrder.completed_at.is_(None)) | (WorkOrderDemand.id.in_(released_demand_ids)),
        )
    )
    contexts: dict[str, list[BoardDemand]] = {}
    for demand, work_order in session.execute(canonical_demand_order(query)):
        contexts.setdefault(demand.part_number, []).append(BoardDemand(demand, work_order))
    return contexts


def _location_sort_key(location: BoardLocation) -> tuple[str, int, int, str, int]:
    return (
        location.area.name,
        location.area.id,
        _STATE_ORDER[location.state],
        location.machine.name if location.machine is not None else "",
        location.machine.id if location.machine is not None else 0,
    )


def _has_open_demand(demands: list[BoardDemand]) -> bool:
    return any(entry.work_order.completed_at is None for entry in demands)


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------


def production_board(session: Session, department_id: int | None) -> ProductionBoard:
    """The Department's board rows, derived and in the canonical order."""
    department = resolve_department(session, department_id)
    areas = {
        area.id: area
        for area in session.scalars(select(Area).where(Area.department_id == department.id))
    }
    flows = list(
        session.scalars(
            select(QuantityFlow)
            .where(
                QuantityFlow.current_area_id.in_(areas.keys()),
                QuantityFlow.status == QuantityFlowStatus.ACTIVE,
            )
            .order_by(QuantityFlow.part_number, QuantityFlow.id)
        )
        if areas
        else []
    )
    flow_ids = [flow.id for flow in flows]
    latest = effective_latest_movements(session, flow_ids)
    machine_areas = areas_with_machines(session, {flow.current_area_id for flow in flows})
    operation_ids = {movement.operation_id for movement in latest.values()}
    operations = (
        {
            operation.id: operation
            for operation in session.scalars(
                select(Operation).where(Operation.id.in_(operation_ids))
            )
        }
        if operation_ids
        else {}
    )
    machine_ids = {
        machine_id
        for movement in latest.values()
        for machine_id in (movement.destination_machine_id, movement.source_machine_id)
        if machine_id is not None
    }
    machines = (
        {
            machine.id: machine
            for machine in session.scalars(select(Machine).where(Machine.id.in_(machine_ids)))
        }
        if machine_ids
        else {}
    )

    # Active quantity grouped per PN and (Area, state, Machine, activity).
    groups: dict[str, dict[tuple[int, str, int | None, str | None], BoardLocation]] = {}
    oldest_flow: dict[str, datetime.datetime] = {}
    for flow in flows:
        movement = latest[flow.id]
        state = _LOCATION_STATE_OF[
            processing_state_of(
                movement.movement_type,
                direct_processing=flow.current_area_id not in machine_areas,
            )
        ]
        # The executor of ON_MACHINE quantity is the assignment's
        # destination Machine; finished quantity names the completing
        # Machine (the AREA_COMPLETED's source Machine) as context only.
        machine_id = (
            movement.destination_machine_id if state == "MACHINE" else movement.source_machine_id
        )
        if state not in ("MACHINE", "DONE"):
            machine_id = None
        operation = operations[movement.operation_id]
        activity = (operation.name or operation.code) if operation.is_external else None
        key = (flow.current_area_id, state, machine_id, activity)
        per_pn = groups.setdefault(flow.part_number, {})
        existing = per_pn.get(key)
        if existing is None:
            per_pn[key] = BoardLocation(
                area=areas[flow.current_area_id],
                machine=machines[machine_id] if machine_id is not None else None,
                activity=activity,
                quantity=flow.quantity,
                state=state,
                since=movement.occurred_at,
            )
        else:
            since = existing.since
            if since is None or movement.occurred_at < since:
                since = movement.occurred_at
            per_pn[key] = existing._replace(quantity=existing.quantity + flow.quantity, since=since)
        created = flow.created_at
        if flow.part_number not in oldest_flow or created < oldest_flow[flow.part_number]:
            oldest_flow[flow.part_number] = created

    terminal_area_ids = [area.id for area in areas.values() if area.is_terminal]
    stocked = _effective_totals_by_area(session, MovementType.STOCKED, terminal_area_ids)
    scrapped = _effective_totals_by_area(session, MovementType.SCRAPPED, areas.keys())
    stocked_by_pn: dict[str, int] = {}
    for (pn, _area_id), quantity in stocked.items():
        stocked_by_pn[pn] = stocked_by_pn.get(pn, 0) + quantity
    scrapped_by_pn: dict[str, int] = {}
    for (pn, _area_id), quantity in scrapped.items():
        scrapped_by_pn[pn] = scrapped_by_pn.get(pn, 0) + quantity

    part_numbers = set(groups) | set(stocked_by_pn)
    demands = _demand_context(session, part_numbers, flow_ids)
    zone = ZoneInfo(site_timezone())

    rows: list[BoardRow] = []
    for pn in sorted(part_numbers):
        active_locations = list(groups.get(pn, {}).values())
        context = demands.get(pn, [])
        if not active_locations and not _has_open_demand(context):
            # Stocked quantity of finished work: no longer the
            # Department's business on the board.
            continue
        locations = active_locations + [
            BoardLocation(
                area=areas[area_id],
                machine=None,
                activity=None,
                quantity=quantity,
                state="STOCKED",
                since=None,
            )
            for (stocked_pn, area_id), quantity in stocked.items()
            if stocked_pn == pn
        ]
        locations.sort(key=_location_sort_key)
        first = context[0] if context else None
        if first is not None:
            received_date = first.work_order.received_date
        else:
            received_date = oldest_flow[pn].astimezone(zone).date()
        rows.append(
            BoardRow(
                part_number=pn,
                hot_rank=first.demand.priority_rank if first is not None else None,
                due_date=first.demand.due_date if first is not None else None,
                received_date=received_date,
                locations=locations,
                active_quantity=sum(location.quantity for location in active_locations),
                stocked_quantity=stocked_by_pn.get(pn, 0),
                scrapped_quantity=scrapped_by_pn.get(pn, 0),
                demands=context,
            )
        )
    rows.sort(key=board_row_sort_key)
    active_rows = [row for row in rows if not row.is_stocked_only]
    return ProductionBoard(
        department=department,
        rows=rows,
        active_part_numbers=len(active_rows),
        active_quantity=sum(row.active_quantity for row in rows),
        stocked_quantity=sum(row.stocked_quantity for row in rows),
        scrapped_quantity=sum(row.scrapped_quantity for row in rows),
    )


def board_row_sort_key(
    row: BoardRow,
) -> tuple[int, int, int, datetime.date, int, datetime.date, int, str]:
    """The canonical board order, expressed as a sort key.

    Active rows before entirely stocked rows; then the canonical demand
    order of the row's defining demand — Hot rank (unranked last),
    dated earliest first, undated after all dated by the Work Order's
    received date, the demand id as the tie-breaker — with a row
    without demand context after every row that has one, by PN.
    """
    first = row.demands[0] if row.demands else None
    far = datetime.date.max
    if first is None:
        return (int(row.is_stocked_only), 1, 0, far, 1, far, 0, row.part_number)
    demand = first.demand
    return (
        int(row.is_stocked_only),
        0,
        demand.priority_rank if demand.priority_rank is not None else 1_000_000_000,
        demand.due_date if demand.due_date is not None else far,
        1 if demand.due_date is None else 0,
        first.work_order.received_date if demand.due_date is None else far,
        demand.id,
        row.part_number,
    )
