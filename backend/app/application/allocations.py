"""Work Order Allocation (Phase 10 — PROJECT_PROFILE §8.2, §8.12, §18).

The assignment of STOCKED PN quantity to WorkOrderDemand records —
independent from PartMovement by design: an allocation never references
a Movement or a QuantityFlow, never alters Movement history, and is
recorded in its own append-only table (`work_order_allocations`).

Rules owned here:

- **Derived, never counted.** Every quantity this module reasons about
  is derived from history: the PN's stocked quantity is the sum of its
  effective `STOCKED` Movements (`projections.stocked_quantity_of`);
  its ACTIVE allocation is the sum of its allocation rows that no
  reversal row references; available stocked quantity is the
  difference. `work_order_demands.allocated_quantity` is a maintained
  projection of the demand's active allocation, updated inside the
  allocation transaction under the demand row lock and rebuildable
  from the rows alone (`rebuild_allocated_quantities`).
- **Canonical demand ordering** (PROJECT_PROFILE §18 Allocation Order):
  the suggestion walks the PN's outstanding demand — Hot rank first
  (lowest `priority_rank` = highest priority; unranked last), then
  dated demand earliest due date first with undated demand after all
  dated demand ordered by the parent Work Order's `received_date`
  (oldest first) — the received date orders UNDATED demand only —,
  then the stable deterministic tie-breaker (demand id ascending — an
  implementation detail, not a business rule; it also resolves dated
  demand sharing one due date) — and proposes for each demand the
  smaller of its remaining shortage and what is still unallocated of
  the quantity being allocated.
- **The confirmed allocation quantity is explicit** (§18 Receiving
  Confirmation: "the total active allocation must equal the portion
  of stocked quantity being allocated"): the command names the
  quantity being allocated — at the Stockroom the just-stocked
  quantity the operator confirmed — and the lines must sum to exactly
  it (refused otherwise, nothing written); the quantity is part of the
  idempotency fingerprint, so a replay with another quantity is a
  conflict; and it is an optimistic precondition against the stock —
  a quantity the PN's available stocked quantity no longer covers
  (someone allocated meanwhile) is refused with nothing written, so a
  dialog opened on a stale figure can never allocate it.
- **Two invariants, enforced under locks** (§8.12): the total active
  allocation of a PN never exceeds its available stocked quantity, and
  a demand's allocation never exceeds its requested quantity — an
  allocation beyond the remaining shortage is refused (the one
  exception is the authorized beyond-demand correction,
  `allocate_beyond_demand` (Phase 14 slice 5): a separate typed
  Management command, one demand line, more than its remaining demand,
  mandatory reason, never above available stock, recorded
  `exceeds_demand`). Both are judged inside ONE transaction under the ONE
  shared PN-level advisory lock (serializing every allocation and
  reversal of one PN, so two concurrent confirmations can never jointly
  exceed the available quantity — and serializing a reversal, which
  returns a business shortage to its demand line, against the Scan
  Station receipt that judges the PN's active demand) plus
  `FOR UPDATE` on every affected demand row
  (serializing against the demand edit's committed-quantity floor and
  against a release) and on every affected Work Order row (the
  completion projection). A confirmation also takes the Hot advisory
  lock after the PN lock, the Stockroom station FOR KEY SHARE (station
  confirmation only), and locks the ranked demand rows its Hot removal
  can shift in the same ascending demand pass (lock order: PN → Hot →
  station → demand rows → Work Orders; a reversal takes PN → demand →
  Work Order and never the Hot lock).
- **Automatic Hot removal** (Phase 12 follow-up, owner decision OD1;
  PROJECT_PROFILE §21): a ranked line this confirmation fully allocates
  — its Work Order completing included — leaves the Hot list in the
  same transaction, the remaining ranks close the gap and every rank
  change is audited with the allocation as its cause
  (`hot_ranks.remove_from_hot_list`). A reversal that makes the demand
  active again re-adds nothing.
- **Operator adjustment** (§18 Receiving Confirmation): the confirmed
  lines may differ from the suggestion; the command re-computes the
  canonical suggestion for the confirmed total under the locks and
  records `is_manual_override` on every line that differs — audit
  context, never a rule. At the station (Phase 14 slice 4) an
  adjustment needs the station role's `ADJUST_SUGGESTED_ALLOCATION`,
  and the confirmation itself (and the station's suggestion read,
  `suggest_station_allocation`) its `CONFIRM_SUGGESTED_ALLOCATION`,
  judged after the idempotency re-check (`app.application.station_access`).
- **Completion is derived** (§8.2, §18 Work Order Completion): a Work
  Order is complete when every one of its demand lines is fully
  allocated. `work_orders.completed_at` is the persisted done-date
  projection: set to the timestamp of the event that left the last
  open line fully allocated — the allocation that filled it, or the
  demand change (a Save lowering its Qty to its allocated quantity, or
  the removal of the last short line) that made the remaining lines
  all fully allocated (`complete_after_demand_change`, audited with
  its cause) — cleared by a reversal that reopens one, rebuildable
  from the allocation rows, replayed command by command in commit
  order, and those completion audit rows (`rebuild_completed_at`) — the
  done date is the event that last turned the Work Order complete,
  never simply the newest effective row (a beyond-demand correction or
  a reversal can land on a Work Order that stays complete). A completed Work Order leaves the active
  list and becomes read-only history (`app.application.work_orders`).
  Movement history is never touched by any of this.
- **Adjustment is a reversal** (§8.12 "every adjustment must be
  auditable"): an allocation is taken back by appending a REVERSAL row
  that references it (`reverses_allocation_id`, UNIQUE — at most once,
  even under a race) with a mandatory reason; a smaller allocation is
  a reversal plus a new allocation. Rows are never edited or deleted.
- **Idempotency** exactly as for every production command (SLICE1
  §14): a request fingerprint in the row metadata; the same
  `device_event_id` + same intent replays the original committed
  result whatever changed since; a mismatched reuse is an explicit
  conflict; a race lost at COMMIT replays the winner; every refusal
  writes nothing.
- **Two entry points, one command** (Phase 14 slice 3): the Stockroom
  station's receiving confirmation (`confirm_station_allocation`, source
  STOCKROOM, `allocated_by_worker_id` from the station Area's mode) and
  the Management allocation (`allocate_from_stock`, source MANAGEMENT);
  the reversal is Management-only. Which one runs is decided by the
  entry point, never by an optional argument. `actor_user_id` is the
  signed-in User of a Management command (NULL at the station); the
  legacy `actor_reference` text column is kept for history and written
  no more. A Management command replayed by another User is refused
  (`RecordedByAnotherUserError`). The beyond-demand correction
  (`allocate_beyond_demand`, Phase 14 slice 5) is a third, separate
  Management command; `management_allocation_context` is the read its
  Management workflow judges from.
- Deliberately absent: any return of stocked quantity to production
  (PROJECT_PROFILE §32 open decision 1).
"""

import datetime
import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final, Literal, NamedTuple

from sqlalchemy import Select, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.application import audit, hot_ranks, station_access, station_identity, user_access
from app.application.common import (
    device_event_id_text,
    is_bindable_id,
    optional_text,
    required_text,
)
from app.application.errors import (
    RECORDED_BY_ANOTHER_USER_MESSAGE,
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
    RecordedByAnotherUserError,
    SuggestionChangedError,
)
from app.application.part_numbers import acquire_part_number_lock, canonical_part_number
from app.application.projections import stocked_quantity_of
from app.domain.enums import AllocationSource, AuditEntityType, AuditEventType, StationCommand
from app.infrastructure.models import (
    ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    AuditEvent,
    ScanStation,
    WorkOrder,
    WorkOrderAllocation,
    WorkOrderDemand,
)

# Immutable metadata keys on every allocation row of one command: the
# idempotency fingerprint (the comparison value of a replay) and the
# command block naming the kind, its size and the completion effect it
# had — read back verbatim on replay, never re-derived.
FINGERPRINT_KEY: Final = "request_fingerprint"
COMMAND_KEY: Final = "command"

#: The command kind of the authorized beyond-demand correction (Phase 14
#: slice 5) — its fingerprint ``command`` and its metadata block ``kind``.
ALLOCATE_BEYOND_DEMAND: Final = "ALLOCATE_BEYOND_DEMAND"

_REVERSES_UNIQUE_CONSTRAINT: Final = "uq_work_order_allocations_reverses_allocation_id"


# ---------------------------------------------------------------------------
# Derivations — every quantity comes from history
# ---------------------------------------------------------------------------


def _effective_allocation_rows() -> Select[tuple[WorkOrderAllocation]]:
    """Allocation rows that still count: not reversals, and not reversed."""
    reversal = aliased(WorkOrderAllocation)
    return select(WorkOrderAllocation).where(
        WorkOrderAllocation.reverses_allocation_id.is_(None),
        ~select(reversal.id)
        .where(reversal.reverses_allocation_id == WorkOrderAllocation.id)
        .exists(),
    )


def active_allocations_by_demand(
    session: Session, work_order_demand_ids: Collection[int]
) -> dict[int, int]:
    """ACTIVE allocated quantity per demand, derived from the rows (0 when absent)."""
    ids = [int(demand_id) for demand_id in work_order_demand_ids]
    if not ids:
        return {}
    reversal = aliased(WorkOrderAllocation)
    rows = session.execute(
        select(WorkOrderAllocation.work_order_demand_id, func.sum(WorkOrderAllocation.quantity))
        .where(
            WorkOrderAllocation.work_order_demand_id.in_(ids),
            WorkOrderAllocation.reverses_allocation_id.is_(None),
            ~select(reversal.id)
            .where(reversal.reverses_allocation_id == WorkOrderAllocation.id)
            .exists(),
        )
        .group_by(WorkOrderAllocation.work_order_demand_id)
    )
    return {int(demand_id): int(total) for demand_id, total in rows}


def active_allocated_quantities(session: Session, part_numbers: Collection[str]) -> dict[str, int]:
    """The ACTIVE allocation of several PNs, derived from the rows.

    One grouped query for a read model that reports many PNs at once
    (the Area Board's Stockroom column); a PN with no active allocation
    is simply absent.
    """
    wanted = list(part_numbers)
    if not wanted:
        return {}
    reversal = aliased(WorkOrderAllocation)
    rows = session.execute(
        select(WorkOrderAllocation.part_number, func.sum(WorkOrderAllocation.quantity))
        .where(
            WorkOrderAllocation.part_number.in_(wanted),
            WorkOrderAllocation.reverses_allocation_id.is_(None),
            ~select(reversal.id)
            .where(reversal.reverses_allocation_id == WorkOrderAllocation.id)
            .exists(),
        )
        .group_by(WorkOrderAllocation.part_number)
    )
    return {str(part_number): int(total) for part_number, total in rows}


def active_allocated_quantity_of(session: Session, part_number: str) -> int:
    """The PN's total ACTIVE allocation, derived from the rows."""
    return active_allocated_quantities(session, [part_number]).get(part_number, 0)


class StockPosition(NamedTuple):
    """The PN's stocked, allocated and available quantities — all derived."""

    stocked_quantity: int
    active_allocated_quantity: int

    @property
    def available_stocked_quantity(self) -> int:
        return self.stocked_quantity - self.active_allocated_quantity


def stock_position_of(session: Session, part_number: str) -> StockPosition:
    """`stocked − active allocation` for one PN (PROJECT_PROFILE §8.12/§18)."""
    return StockPosition(
        stocked_quantity=stocked_quantity_of(session, part_number),
        active_allocated_quantity=active_allocated_quantity_of(session, part_number),
    )


def canonical_demand_order(query: Select[Any]) -> Select[Any]:
    """Apply the canonical demand ordering (PROJECT_PROFILE §18) to a demand query.

    The query must join `WorkOrder`. Hot rank first (lowest rank number
    = highest priority, unranked last); within one priority, dated
    demand earliest due date first, undated demand after all dated
    demand ordered by the parent Work Order's received date (oldest
    first); equal values — dated demand sharing a due date, undated
    demand sharing a received date — resolved by the stable
    deterministic tie-breaker (demand id ascending, an implementation
    detail). The received date is a criterion for UNDATED demand only:
    two dated lines with the same due date order by the tie-breaker,
    never by which Work Order arrived first.
    """
    return query.order_by(
        WorkOrderDemand.priority_rank.asc().nulls_last(),
        WorkOrderDemand.due_date.asc().nulls_last(),
        case((WorkOrderDemand.due_date.is_(None), WorkOrder.received_date), else_=None).asc(),
        WorkOrderDemand.id.asc(),
    )


class DemandContext(NamedTuple):
    """One OPEN Work Order Demand of a PN, with its Work Order."""

    demand: WorkOrderDemand
    work_order: WorkOrder


def open_demand_context(
    session: Session, part_numbers: Collection[str]
) -> dict[str, list[DemandContext]]:
    """The OPEN demands of each PN, in the canonical demand order.

    OPEN is the Work Order not completed (PROJECT_PROFILE §18): a
    completed Work Order is history and never supplies a monitoring
    row's Hot rank, dates or Work Order / Job Number metadata, even
    while quantity it released is still in production. The demands of
    one PN are returned as they are, in the canonical order — the
    caller presents all of them and takes the FIRST as the defining
    one; nothing is picked arbitrarily or summed into a single
    ambiguous value.

    This is the ONE monitoring demand context: the Production Board,
    the Area Board and the Area inventory the Scan Station renders all
    read it here, so no two surfaces can disagree about what a PN is
    currently being worked for.
    """
    wanted = set(part_numbers)
    if not wanted:
        return {}
    query = (
        select(WorkOrderDemand, WorkOrder)
        .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
        .where(
            WorkOrderDemand.part_number.in_(wanted),
            WorkOrder.completed_at.is_(None),
        )
    )
    contexts: dict[str, list[DemandContext]] = {}
    for demand, work_order in session.execute(canonical_demand_order(query)):
        contexts.setdefault(demand.part_number, []).append(DemandContext(demand, work_order))
    return contexts


class OutstandingDemand(NamedTuple):
    """One demand of the PN with remaining shortage, in canonical order."""

    demand: WorkOrderDemand
    work_order: WorkOrder
    allocated_quantity: int

    @property
    def shortage(self) -> int:
        return max(self.demand.requested_quantity - self.allocated_quantity, 0)


def outstanding_demands(session: Session, part_number: str) -> list[OutstandingDemand]:
    """The PN's demand lines with shortage > 0, in canonical order.

    A demand on a completed Work Order has no shortage by definition
    (completion means every line is fully allocated), so it never
    appears; the allocated quantity is derived from the rows, never
    read from the projection column.
    """
    rows = list(
        session.execute(
            canonical_demand_order(
                select(WorkOrderDemand, WorkOrder)
                .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
                .where(WorkOrderDemand.part_number == part_number)
            )
        )
    )
    allocated = active_allocations_by_demand(session, [demand.id for demand, _ in rows])
    outstanding = [
        OutstandingDemand(demand, work_order, allocated.get(demand.id, 0))
        for demand, work_order in rows
    ]
    return [item for item in outstanding if item.shortage > 0]


# ---------------------------------------------------------------------------
# The suggestion (read model, PROJECT_PROFILE §18 Receiving Confirmation)
# ---------------------------------------------------------------------------


class SuggestedLine(NamedTuple):
    """One row of the receiving confirmation dialog (GUI_DESIGN §10)."""

    work_order: WorkOrder
    demand: WorkOrderDemand
    requested_quantity: int
    previously_allocated_quantity: int
    remaining_shortage: int
    proposed_quantity: int


class AllocationSuggestion(NamedTuple):
    part_number: str
    # The quantity the caller wants to allocate (the just-stocked
    # quantity at the Stockroom), capped at what is available.
    quantity: int
    stocked_quantity: int
    active_allocated_quantity: int
    available_stocked_quantity: int
    lines: list[SuggestedLine]

    @property
    def proposed_total(self) -> int:
        return sum(line.proposed_quantity for line in self.lines)

    @property
    def unallocated_quantity(self) -> int:
        """Quantity no outstanding demand can take — it stays in stock."""
        return self.quantity - self.proposed_total


def _propose(outstanding: list[OutstandingDemand], quantity: int) -> list[SuggestedLine]:
    """Greedy fill in canonical order, each line up to its remaining shortage."""
    remaining = quantity
    lines: list[SuggestedLine] = []
    for item in outstanding:
        proposed = min(item.shortage, remaining)
        remaining -= proposed
        lines.append(
            SuggestedLine(
                work_order=item.work_order,
                demand=item.demand,
                requested_quantity=item.demand.requested_quantity,
                previously_allocated_quantity=item.allocated_quantity,
                remaining_shortage=item.shortage,
                proposed_quantity=proposed,
            )
        )
    return lines


def suggest_allocation(
    session: Session, *, part_number: object, quantity: object | None = None
) -> AllocationSuggestion:
    """The canonical allocation suggestion for a PN (a read — nothing is written).

    ``quantity`` defaults to the PN's whole available stocked quantity
    and is capped at it: a suggestion never proposes what is not in
    stock. Every outstanding demand of the PN is listed — also those
    the quantity does not reach (proposed 0) — so the operator sees the
    full canonical order and may adjust before confirming.
    """
    pn = canonical_part_number(part_number)
    position = stock_position_of(session, pn)
    available = max(position.available_stocked_quantity, 0)
    if quantity is None:
        wanted = available
    else:
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise InvalidInputError("Allocation quantity must be a whole number of 0 or more.")
        wanted = min(quantity, available)
    return AllocationSuggestion(
        part_number=pn,
        quantity=wanted,
        stocked_quantity=position.stocked_quantity,
        active_allocated_quantity=position.active_allocated_quantity,
        available_stocked_quantity=position.available_stocked_quantity,
        lines=_propose(outstanding_demands(session, pn), wanted),
    )


def suggest_station_allocation(
    session: Session, *, part_number: object, quantity: object | None = None
) -> AllocationSuggestion:
    """The suggestion a Scan Station reads (Phase 14 slice 4): refused first
    (403) unless the role applied at Scan Stations may confirm suggested
    allocations; otherwise exactly ``suggest_allocation``."""
    station_access.require_station_capability(session, StationCommand.ALLOCATION)
    return suggest_allocation(session, part_number=part_number, quantity=quantity)


# ---------------------------------------------------------------------------
# Command results and idempotency
# ---------------------------------------------------------------------------


class AllocationRow(NamedTuple):
    """One committed allocation row, read back from the immutable table."""

    allocation_id: int
    work_order_demand_id: int
    work_order_id: int
    part_number: str
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
    command_sequence: int


class AllocationResult(NamedTuple):
    """One committed allocation command (a confirmation or a reversal)."""

    kind: str
    part_number: str
    # The quantity the command allocated (the confirmed allocation
    # quantity) or took back (a reversal) — the sum of its rows.
    allocation_quantity: int
    rows: list[AllocationRow]
    # Work Orders this command completed / reopened — recorded in the
    # row metadata at command time, replayed verbatim.
    completed_work_order_ids: list[int]
    reopened_work_order_ids: list[int]
    device_event_id: str
    created: bool


def _reused_for_other_intent() -> IdempotencyConflictError:
    return IdempotencyConflictError(
        "This device_event_id was already used for a different allocation"
        " request. Nothing was recorded — a new intent needs a new"
        " device_event_id."
    )


def committed_allocation_command(
    session: Session, device_event_id: str
) -> list[WorkOrderAllocation]:
    """Every allocation row recorded under the id, in command sequence."""
    return list(
        session.scalars(
            select(WorkOrderAllocation)
            .where(WorkOrderAllocation.device_event_id == device_event_id)
            .order_by(WorkOrderAllocation.command_sequence)
        )
    )


def _fingerprint(normalized: dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_from_rows(
    session: Session, rows: Sequence[WorkOrderAllocation], *, created: bool
) -> AllocationResult:
    block = (rows[-1].metadata_ or {}).get(COMMAND_KEY)
    if not isinstance(block, dict) or not isinstance(block.get("kind"), str):
        raise _reused_for_other_intent()
    work_order_ids: dict[int, int] = {
        int(demand_id): int(work_order_id)
        for demand_id, work_order_id in session.execute(
            select(WorkOrderDemand.id, WorkOrderDemand.work_order_id).where(
                WorkOrderDemand.id.in_({row.work_order_demand_id for row in rows})
            )
        )
    }
    return AllocationResult(
        kind=str(block["kind"]),
        part_number=rows[0].part_number,
        allocation_quantity=int(
            block.get("allocation_quantity", sum(row.quantity for row in rows))
        ),
        rows=[
            AllocationRow(
                allocation_id=row.id,
                work_order_demand_id=row.work_order_demand_id,
                work_order_id=int(work_order_ids[row.work_order_demand_id]),
                part_number=row.part_number,
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
                command_sequence=row.command_sequence,
            )
            for row in rows
        ],
        completed_work_order_ids=[int(x) for x in block.get("completed_work_order_ids", [])],
        reopened_work_order_ids=[int(x) for x in block.get("reopened_work_order_ids", [])],
        device_event_id=rows[0].device_event_id,
        created=created,
    )


def _replay_or_conflict(
    session: Session,
    rows: Sequence[WorkOrderAllocation],
    fingerprint: str,
    actor_user_id: int | None,
) -> AllocationResult:
    """Replay the committed command, or refuse a reuse.

    The fingerprint is checked first (a different intent is the plain
    idempotency conflict); then the stored actor must be the caller's —
    a Management command recorded by another User, or before sign-in
    existed (NULL), is not replayed to this one. Station rows carry
    NULL, so a station replay compares NULL with NULL.
    """
    stored = (rows[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    if stored != fingerprint or any(
        (row.metadata_ or {}).get(FINGERPRINT_KEY) != stored for row in rows
    ):
        raise _reused_for_other_intent()
    if any(row.actor_user_id != actor_user_id for row in rows):
        raise RecordedByAnotherUserError(RECORDED_BY_ANOTHER_USER_MESSAGE)
    return _result_from_rows(session, rows, created=False)


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


def _acquire_part_number_allocation_lock(session: Session, part_number: str) -> None:
    """Take the ONE shared PN-level lock for an allocation write.

    Available stocked quantity is a PN-level figure: two confirmations
    of the same PN judged against the same snapshot could jointly
    exceed it, so they queue here. Stocking only ever ADDS availability
    (a `STOCKED` command is never undone), so the stocked side needs
    no lock — a reading under this lock is at worst conservative.

    A REVERSAL additionally returns a business shortage to its demand
    line — the PN regains active Work Order Demand — which is the same
    PN-level threshold a Scan Station receipt judges its entry
    condition on, so the lock is the shared one
    (`part_numbers.acquire_part_number_lock`) rather than a namespace
    of its own: a separate namespace would let a reversal and a receipt
    each hold "their" lock and invalidate the other's precondition.
    """
    acquire_part_number_lock(session, part_number)


def _lock_demands(session: Session, demand_ids: Collection[int]) -> dict[int, WorkOrderDemand]:
    """The demand rows locked ascending and RE-READ under the lock."""
    locked: dict[int, WorkOrderDemand] = {}
    for demand_id in sorted(set(demand_ids)):
        demand = session.get(
            WorkOrderDemand, demand_id, with_for_update=True, populate_existing=True
        )
        if demand is None:
            raise InvalidInputError(f"Demand line {demand_id} does not exist.")
        locked[demand_id] = demand
    return locked


def _lock_work_orders(session: Session, work_order_ids: Collection[int]) -> dict[int, WorkOrder]:
    locked: dict[int, WorkOrder] = {}
    for work_order_id in sorted(set(work_order_ids)):
        work_order = session.get(
            WorkOrder, work_order_id, with_for_update=True, populate_existing=True
        )
        if work_order is None:  # pragma: no cover - FK guarantees the row
            raise InvalidInputError(f"Work Order {work_order_id} does not exist.")
        locked[work_order_id] = work_order
    return locked


def _require_stockroom_station(session: Session, station_id: str) -> ScanStation:
    """The station a receiving confirmation records — active, bound to a terminal Area.

    Read for audit identity. It takes FOR KEY SHARE on the station
    before the demand pass (lock order of the Phase 12 follow-up: it is
    the lock the allocation rows' FK would take later anyway, taken
    early so no demand row holder waits on a station) and re-reads it
    under that lock. The caller takes its PN lock (and, for a
    confirmation, the Hot lock) first, so no row lock precedes an
    advisory lock. FOR KEY SHARE conflicts only with FOR UPDATE and
    DELETE, so a station edit is not blocked.
    """
    station = session.get(
        ScanStation,
        station_id,
        with_for_update={"read": True, "key_share": True},
        populate_existing=True,
    )
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was allocated."
        )
    area = session.get(Area, station.area_id)
    if area is None or not area.is_terminal:
        raise ConflictError(
            f"Scan Station '{station_id}' is not bound to a terminal Area. Allocation is"
            " confirmed at the Stockroom (or from Management). Nothing was allocated."
        )
    return station


# ---------------------------------------------------------------------------
# Completion projection (PROJECT_PROFILE §8.2)
# ---------------------------------------------------------------------------


def _work_order_is_complete(
    session: Session, work_order_id: int, deltas: Mapping[int, int]
) -> bool:
    """Every demand line of the Work Order fully allocated, judged from the
    committed rows PLUS the deltas this command is about to write."""
    lines = list(
        session.execute(
            select(WorkOrderDemand.id, WorkOrderDemand.requested_quantity).where(
                WorkOrderDemand.work_order_id == work_order_id
            )
        )
    )
    if not lines:  # pragma: no cover - a Work Order always has a demand line
        return False
    allocated = active_allocations_by_demand(session, [demand_id for demand_id, _ in lines])
    return all(
        allocated.get(demand_id, 0) + deltas.get(demand_id, 0) >= requested
        for demand_id, requested in lines
    )


def _apply_completion(
    session: Session, work_orders: Mapping[int, WorkOrder], deltas: Mapping[int, int]
) -> tuple[list[int], list[int]]:
    """Set / clear `completed_at` on the locked Work Orders for this command.

    Judged BEFORE the rows are inserted (the table is append-only, so
    the completion effect must be known when the rows' metadata is
    written): the committed active allocation plus this command's
    per-demand deltas. Returns (completed ids, reopened ids). The done
    date is the allocation event's own timestamp (`func.now()` of this
    transaction — the same value the rows' `allocated_at` carry).
    """
    completed: list[int] = []
    reopened: list[int] = []
    for work_order_id in sorted(work_orders):
        work_order = work_orders[work_order_id]
        is_complete = _work_order_is_complete(session, work_order_id, deltas)
        if is_complete and work_order.completed_at is None:
            work_order.completed_at = func.now()
            work_order.updated_at = func.now()
            completed.append(work_order_id)
        elif not is_complete and work_order.completed_at is not None:
            work_order.completed_at = None
            work_order.updated_at = func.now()
            reopened.append(work_order_id)
    return completed, reopened


#: Audit metadata key of a completion a demand change caused — the record
#: `rebuild_completed_at` replays beside the allocation rows.
COMPLETION_AUDIT_KEY: Final = "completion"

#: The demand change that left a Work Order fully allocated.
DemandChangeTrigger = Literal["WORK_ORDER_SAVE", "DEMAND_LINE_REMOVAL"]


def complete_after_demand_change(
    session: Session,
    work_order: WorkOrder,
    *,
    trigger: DemandChangeTrigger,
    actor_user_id: int,
) -> bool:
    """Complete an open Work Order a demand change left fully allocated (§8.2).

    Completion is derived from allocation whatever write makes every
    current line fully allocated: a Save that lowers the last short
    line's requested quantity to its allocated quantity, or the removal
    of the last short line, completes the Work Order exactly as the
    allocation that fills that line would. Only completing is possible
    here — a completed Work Order is read-only for demand changes, so a
    demand change never reopens one (a reversal does).

    The caller holds the Work Order row lock (re-read under it — the
    lock every allocation and reversal of its lines also takes, so the
    judgement and a concurrent allocation serialize) and has flushed
    its demand change: the judgement reads the current lines and the
    committed active allocation. The done date is this transaction's
    own timestamp — the completing event, as `_apply_completion` uses
    the allocation's — and the completion is audited on the Work Order
    with its cause (the allocation rows carry theirs in their command
    metadata) and the signed-in User who made the demand change.
    Returns whether the Work Order completed.
    """
    if work_order.completed_at is not None or not _work_order_is_complete(
        session, work_order.id, {}
    ):
        return False
    # `now()` is the transaction timestamp: the same instant the audit
    # row's `occurred_at` records, which the done-date replay relies on.
    completed_at = session.scalar(select(func.now()))
    if completed_at is None:  # pragma: no cover - now() is never NULL
        raise RuntimeError("The database returned no transaction timestamp.")
    work_order.completed_at = completed_at
    work_order.updated_at = completed_at
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.WORK_ORDER,
        entity_id=str(work_order.id),
        before_data={"completed_at": None},
        after_data={"completed_at": completed_at.isoformat()},
        actor_user_id=actor_user_id,
        metadata={COMPLETION_AUDIT_KEY: {"trigger": trigger}},
    )
    return True


# ---------------------------------------------------------------------------
# The confirmation command
# ---------------------------------------------------------------------------


class ConfirmedLine(NamedTuple):
    work_order_demand_id: int
    quantity: int


def _normalized_lines(lines: Sequence[Mapping[str, Any]]) -> list[ConfirmedLine]:
    if not lines:
        raise InvalidInputError("An allocation needs at least one demand line with a quantity.")
    seen: set[int] = set()
    normalized: list[ConfirmedLine] = []
    for line in lines:
        demand_id = line.get("work_order_demand_id")
        quantity = line.get("quantity")
        if not isinstance(demand_id, int) or isinstance(demand_id, bool):
            raise InvalidInputError("work_order_demand_id must be a whole number.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise InvalidInputError(
                f"Allocation quantity for demand line {demand_id} must be a positive whole"
                " number. Leave a line out instead of allocating 0 to it."
            )
        if demand_id in seen:
            raise InvalidInputError(
                f"Demand line {demand_id} appears more than once. Combine the quantities"
                " into one line."
            )
        seen.add(demand_id)
        normalized.append(ConfirmedLine(demand_id, quantity))
    return normalized


#: C-2 (Phase 14 slice 4): the suggestion shown went stale and the station
#: role may not adjust suggested allocations.
_STALE_SUGGESTION_MESSAGE: Final = (
    "The suggested allocation changed since it was shown, and Scan Stations are not allowed"
    " to adjust suggested allocations. Nothing was allocated."
)


def _differs_from_suggestion(
    confirmed: Sequence[ConfirmedLine], suggested: Mapping[int, int]
) -> bool:
    """Whether the confirmed lines differ from the canonical suggestion: a
    line's quantity differs, or a line the suggestion proposes is missing."""
    sent = {line.work_order_demand_id for line in confirmed}
    return any(
        suggested.get(line.work_order_demand_id, 0) != line.quantity for line in confirmed
    ) or any(quantity > 0 and demand_id not in sent for demand_id, quantity in suggested.items())


def confirm_station_allocation(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    allocation_quantity: object,
    lines: Sequence[Mapping[str, Any]],
    reason: str | None = None,
    device_event_id: object,
    suggestion_unchanged: bool = False,
) -> AllocationResult:
    """The Stockroom station's receiving confirmation (PROJECT_PROFILE §18).

    The routine Operator workflow: source STOCKROOM, the Worker identity
    of the station Area's mode, no User (``actor_user_id`` NULL). The
    role applied at Scan Stations must grant confirming suggested
    allocations, and adjusting them when the lines differ from the
    suggestion recomputed under the locks (Phase 14 slice 4);
    ``suggestion_unchanged`` — the client sent the suggestion it was
    shown, unmodified — only chooses which refusal explains a mismatch
    and is never part of the fingerprint.
    """
    return _confirm_allocation(
        session,
        station_id=station_id,
        actor_user_id=None,
        part_number=part_number,
        allocation_quantity=allocation_quantity,
        lines=lines,
        reason=reason,
        device_event_id=device_event_id,
        suggestion_unchanged=suggestion_unchanged,
    )


def allocate_from_stock(
    session: Session,
    *,
    actor_user_id: int,
    part_number: object,
    allocation_quantity: object,
    lines: Sequence[Mapping[str, Any]],
    reason: str | None = None,
    device_event_id: object,
) -> AllocationResult:
    """A Management allocation of stocked quantity (allocate-later, §8.12).

    Source MANAGEMENT, recorded with the signed-in User; no station and
    no Worker identity. The routine limit holds: never beyond a line's
    remaining shortage.
    """
    return _confirm_allocation(
        session,
        station_id=None,
        actor_user_id=actor_user_id,
        part_number=part_number,
        allocation_quantity=allocation_quantity,
        lines=lines,
        reason=reason,
        device_event_id=device_event_id,
    )


def _confirm_allocation(
    session: Session,
    *,
    station_id: str | None,
    actor_user_id: int | None,
    part_number: object,
    allocation_quantity: object,
    lines: Sequence[Mapping[str, Any]],
    reason: str | None,
    device_event_id: object,
    suggestion_unchanged: bool = False,
) -> AllocationResult:
    """Allocate stocked quantity of one PN to demand lines, ONE transaction.

    Exactly one of ``station_id`` (the Stockroom confirmation) and
    ``actor_user_id`` (a Management allocation) is set — the entry
    point decides, never a client input.
    ``allocation_quantity`` is the explicit quantity being allocated:
    the lines must sum to exactly it, and the PN's available stocked
    quantity must still cover it when the command is judged under the
    locks. Order: input shape → fingerprint → idempotency fast path →
    the per-PN advisory lock → the Hot advisory lock → the Stockroom
    station FOR KEY SHARE (when ``station_id`` is set) → the demand row
    locks in ONE ascending pass (the lines plus every ranked row a Hot
    removal of them can shift, ``hot_ranks.hot_rank_scope``) → the Work
    Order row locks (ascending) → idempotency re-check → (station only)
    the station role's ``CONFIRM_SUGGESTED_ALLOCATION`` → validation
    under the locks (PN agreement, shortage per line, available stocked
    quantity for the allocation quantity) → the canonical suggestion →
    (station only) the adjustment check → the completion → the
    automatic Hot removal of every ranked line this command fully
    allocates (OD1 — its Work Order completing included; the remaining
    ranks close the gap, audited) → the rows and the projection →
    COMMIT (or replay of a race winner). A replay never touches the Hot
    list: it returns before any lock.
    """
    assert (station_id is None) != (actor_user_id is None), "one of station or User"
    pn = canonical_part_number(part_number)
    confirmed = _normalized_lines(lines)
    if (
        not isinstance(allocation_quantity, int)
        or isinstance(allocation_quantity, bool)
        or allocation_quantity <= 0
    ):
        raise InvalidInputError("The allocation quantity must be a positive whole number.")
    total = sum(line.quantity for line in confirmed)
    if total != allocation_quantity:
        raise InvalidInputError(
            f"The allocated lines sum to {total} pcs but the allocation quantity is"
            f" {allocation_quantity} pcs. The total active allocation must equal the"
            " quantity being allocated — adjust the lines until they add up."
            " Nothing was allocated."
        )
    reason_text = optional_text(reason, "The allocation reason")
    event_id = device_event_id_text(device_event_id)
    source = AllocationSource.STOCKROOM if station_id is not None else AllocationSource.MANAGEMENT
    # The key set is the pre-Phase 14 one: "actor" stays, always None, so
    # every command committed before slice 3 still replays to the same
    # hash. Identity is never part of the fingerprint — a different User
    # is refused by the actor comparison of the replay instead.
    fingerprint = _fingerprint(
        {
            "command": "ALLOCATE",
            "part_number": pn,
            "allocation_quantity": allocation_quantity,
            "lines": [list(line) for line in sorted(confirmed)],
            "station_id": station_id,
            "actor": None,
            "reason": reason_text,
        }
    )

    # -- Idempotency fast path (SLICE1 §14) ------------------------------
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint, actor_user_id)

    # -- Serialize per PN and on the Hot list, then lock the rows --------
    # The Hot lock is taken unconditionally: whether a line is ranked is
    # only known under it, and taking it later — once a demand row is
    # held — would invert the Hot command's Hot lock → demand row order.
    _acquire_part_number_allocation_lock(session, pn)
    hot_ranks.acquire_hot_list_lock(session)
    station = _require_stockroom_station(session, station_id) if station_id is not None else None
    line_ids = sorted(line.work_order_demand_id for line in confirmed)
    scope = hot_ranks.hot_rank_scope(session, line_ids)
    # ONE ascending pass over the lines and every ranked row a Hot
    # removal of them can shift; only the lines are this command's own.
    locked = _lock_demands(session, set(line_ids) | scope.shift_ids)
    demands = {demand_id: locked[demand_id] for demand_id in line_ids}
    work_orders = _lock_work_orders(session, {demand.work_order_id for demand in demands.values()})

    # -- Idempotency RE-CHECK after the blocking locks -------------------
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint, actor_user_id)
    # Phase 14 slice 4: the station role must grant the confirmation (the
    # Management allocation is authorized by its route instead).
    if station_id is not None:
        station_access.require_station_capability(session, StationCommand.ALLOCATION)

    # -- Validation under the locks -------------------------------------
    for line in confirmed:
        demand = demands[line.work_order_demand_id]
        if demand.part_number != pn:
            raise InvalidInputError(
                f"Demand line {demand.id} is for Part Number '{demand.part_number}', not"
                f" '{pn}'. Stocked quantity is allocated to its own PN's demand only."
                " Nothing was allocated."
            )
    allocated = active_allocations_by_demand(session, list(demands))
    for line in confirmed:
        demand = demands[line.work_order_demand_id]
        shortage = demand.requested_quantity - allocated.get(demand.id, 0)
        if line.quantity > shortage:
            raise ConflictError(
                f"Demand line {demand.id} (Work Order {demand.work_order_id}) can take"
                f" {max(shortage, 0)} pcs more ({allocated.get(demand.id, 0)} of"
                f" {demand.requested_quantity} pcs already allocated), not"
                f" {line.quantity}. Allocation never exceeds the requested quantity."
                " Nothing was allocated."
            )
    # The confirmed allocation quantity is an optimistic precondition
    # against the stock: judged here, under the per-PN lock, on the
    # derived figure — never on the figure the dialog was opened with.
    position = stock_position_of(session, pn)
    if allocation_quantity > position.available_stocked_quantity:
        raise ConflictError(
            f"Only {max(position.available_stocked_quantity, 0)} pcs of Part Number '{pn}'"
            f" are available in stock ({position.stocked_quantity} stocked,"
            f" {position.active_allocated_quantity} already allocated);"
            f" {allocation_quantity} pcs cannot be allocated — the available quantity"
            " changed since the allocation was prepared. Reload the suggestion and"
            " confirm again. Nothing was allocated."
        )

    # -- The canonical suggestion for the confirmed total (audit) --------
    # Judged under the locks so the override flag records what the
    # server would have proposed at this very moment.
    suggested = {
        line.demand.id: line.proposed_quantity
        for line in _propose(outstanding_demands(session, pn), total)
    }
    # -- The station adjustment check (Phase 14 slice 4) ------------------
    # Lines differing from that suggestion are an adjustment, which the
    # station role must grant — unless the client sent the suggestion it
    # was shown unmodified: then the suggestion went stale (C-2).
    if station_id is not None and _differs_from_suggestion(confirmed, suggested):
        if suggestion_unchanged and not station_access.station_may(
            session, StationCommand.ALLOCATION_ADJUSTMENT
        ):
            raise SuggestionChangedError(_STALE_SUGGESTION_MESSAGE)
        station_access.require_station_capability(
            session, StationCommand.ALLOCATION, StationCommand.ALLOCATION_ADJUSTMENT
        )
    identity = (
        station_identity.resolve_station_identity(session, station)
        if station is not None
        else station_identity.NO_IDENTITY
    )

    # -- Writes — all inside the one open transaction --------------------
    # The completion effect is judged first (append-only rows carry it
    # in their metadata — part of the immutable record, replayed
    # verbatim, never re-derived from a later state).
    deltas = {line.work_order_demand_id: line.quantity for line in confirmed}
    completed, reopened = _apply_completion(session, work_orders, deltas)
    # -- Automatic Hot removal (OD1), BEFORE any allocation row is staged:
    # its flushes must not carry the rows whose idempotency race is
    # translated only at COMMIT below. Judged on the figure the
    # projection writes; completion implies a fully allocated line.
    removals = {
        demand_id: (
            hot_ranks.HotRemovalReason.WORK_ORDER_COMPLETED
            if demands[demand_id].work_order_id in completed
            else hot_ranks.HotRemovalReason.FULLY_ALLOCATED
        )
        for demand_id in sorted(scope.ranked_candidates)
        if allocated.get(demand_id, 0) + deltas[demand_id] >= demands[demand_id].requested_quantity
    }
    if removals:
        hot_ranks.remove_from_hot_list(
            session,
            scope=scope,
            locked=locked,
            removals=removals,
            action=hot_ranks.HotRankEventAction.AUTO_REMOVE,
            trigger=hot_ranks.HotRankTrigger.ALLOCATION,
            reference={
                "device_event_id": event_id,
                "source": str(source),
                "station_id": station_id or None,
            },
            actor_user_id=actor_user_id,
        )
    metadata: dict[str, Any] = {
        FINGERPRINT_KEY: fingerprint,
        COMMAND_KEY: {
            "kind": "ALLOCATE",
            "size": len(confirmed),
            "allocation_quantity": allocation_quantity,
            "completed_work_order_ids": completed,
            "reopened_work_order_ids": reopened,
        },
    }
    rows = [
        WorkOrderAllocation(
            part_number=pn,
            work_order_demand_id=line.work_order_demand_id,
            quantity=line.quantity,
            source=source,
            is_manual_override=suggested.get(line.work_order_demand_id, 0) != line.quantity,
            allocation_reason=reason_text,
            reverses_allocation_id=None,
            station_id=station.station_id if station is not None else None,
            actor_user_id=actor_user_id,
            allocated_by_worker_id=identity.worker_id,
            allocated_at=func.now(),
            device_event_id=event_id,
            command_sequence=sequence,
            metadata_=metadata,
        )
        for sequence, line in enumerate(confirmed, start=1)
    ]
    session.add_all(rows)
    # Projection: the demand's active allocation, under its row lock.
    for line in confirmed:
        demand = demands[line.work_order_demand_id]
        demand.allocated_quantity = allocated.get(demand.id, 0) + line.quantity
        demand.updated_at = func.now()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_allocation_command(session, event_id)
            if winner:
                return _replay_or_conflict(session, winner, fingerprint, actor_user_id)
        raise
    return _result_from_rows(session, rows, created=True)


# ---------------------------------------------------------------------------
# The reversal (adjustment) command
# ---------------------------------------------------------------------------


def reverse_allocation(
    session: Session,
    *,
    allocation_id: int,
    reason: object,
    device_event_id: object,
    actor_user_id: int,
) -> AllocationResult:
    """Take one allocation back — the auditable adjustment (§8.12), ONE transaction.

    Management only (Phase 14 slice 3): source MANAGEMENT, no station,
    no Worker identity, recorded with the signed-in User. Appends a
    REVERSAL row referencing the allocation (UNIQUE — once), returns the
    quantity to the PN's available stock, lowers the demand's projection
    and reopens the Work Order when it was complete. The original row is
    never touched. A smaller allocation is this reversal followed by a
    new allocation.
    """
    reason_text = required_text(reason, "The adjustment reason")
    event_id = device_event_id_text(device_event_id)
    # The pre-Phase 14 key set, "station_id" and "actor" constant None, so
    # every reversal committed before slice 3 still replays to its hash.
    fingerprint = _fingerprint(
        {
            "command": "REVERSE_ALLOCATION",
            "allocation_id": allocation_id,
            "reason": reason_text,
            "station_id": None,
            "actor": None,
        }
    )
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint, actor_user_id)

    original = session.get(WorkOrderAllocation, allocation_id)
    if original is None:
        raise NotFoundError(f"Allocation {allocation_id} does not exist.")
    if original.reverses_allocation_id is not None:
        raise ConflictError(
            f"Allocation {allocation_id} is itself a reversal. A reversal is permanent —"
            " allocate the quantity again instead of reversing the reversal."
            " Nothing was recorded."
        )
    _acquire_part_number_allocation_lock(session, original.part_number)
    # No Hot lock: a reversal never removes a Hot entry, and nothing is
    # re-added (OD1).
    demand = _lock_demands(session, [original.work_order_demand_id])[original.work_order_demand_id]
    work_order = _lock_work_orders(session, [demand.work_order_id])[demand.work_order_id]
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint, actor_user_id)
    already = session.scalar(
        select(WorkOrderAllocation.id)
        .where(WorkOrderAllocation.reverses_allocation_id == original.id)
        .limit(1)
    )
    if already is not None:
        raise ConflictError(
            f"Allocation {allocation_id} has already been reversed. Nothing was recorded."
        )
    allocated_before = active_allocations_by_demand(session, [demand.id]).get(demand.id, 0)
    completed, reopened = _apply_completion(
        session, {work_order.id: work_order}, {demand.id: -original.quantity}
    )
    metadata: dict[str, Any] = {
        FINGERPRINT_KEY: fingerprint,
        COMMAND_KEY: {
            "kind": "REVERSE_ALLOCATION",
            "size": 1,
            "allocation_quantity": original.quantity,
            "completed_work_order_ids": completed,
            "reopened_work_order_ids": reopened,
        },
    }
    row = WorkOrderAllocation(
        part_number=original.part_number,
        work_order_demand_id=original.work_order_demand_id,
        quantity=original.quantity,
        source=AllocationSource.MANAGEMENT,
        is_manual_override=True,
        allocation_reason=reason_text,
        reverses_allocation_id=original.id,
        station_id=None,
        actor_user_id=actor_user_id,
        allocated_by_worker_id=None,
        allocated_at=func.now(),
        device_event_id=event_id,
        command_sequence=1,
        metadata_=metadata,
    )
    session.add(row)
    demand.allocated_quantity = allocated_before - original.quantity
    demand.updated_at = func.now()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        constraint = getattr(diagnostics, "constraint_name", None)
        if constraint == ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_allocation_command(session, event_id)
            if winner:
                return _replay_or_conflict(session, winner, fingerprint, actor_user_id)
        if constraint == _REVERSES_UNIQUE_CONSTRAINT:
            raise ConflictError(
                f"Allocation {allocation_id} has already been reversed. Nothing was recorded."
            ) from exc
        raise
    return _result_from_rows(session, [row], created=True)


# ---------------------------------------------------------------------------
# The authorized beyond-demand correction (Phase 14 slice 5)
# ---------------------------------------------------------------------------


def allocate_beyond_demand(
    session: Session,
    *,
    actor_user_id: int,
    part_number: object,
    work_order_demand_id: object,
    quantity: object,
    reason: object,
    device_event_id: object,
) -> AllocationResult:
    """Allocate stocked quantity to ONE demand line beyond its remaining
    demand — the explicitly authorized correction (PROJECT_PROFILE §8.12),
    ONE transaction.

    A distinct Management intent, never a relaxation of routine
    allocation: the quantity must be MORE than the line's remaining
    shortage (within it the routine allocation serves — refused here, so
    the recorded flag stays truthful), never above the PN's available
    stocked quantity, and the reason is mandatory. One row: source
    MANAGEMENT, ``is_manual_override``, ``exceeds_demand`` (CHECK-guarded),
    the signed-in User. A completed Work Order stays complete with its
    done date unchanged; an open one completes when this fills its last
    short line. A ranked line leaves the Hot list exactly as after a
    routine full allocation (OD1). Lock order and idempotency are the
    Management allocation's (PN → Hot → demand rows ascending → Work
    Order; a replay by another User is refused). Reversible by
    ``reverse_allocation`` like any allocation.
    """
    pn = canonical_part_number(part_number)
    if not isinstance(work_order_demand_id, int) or isinstance(work_order_demand_id, bool):
        raise InvalidInputError("work_order_demand_id must be a whole number.")
    demand_id = work_order_demand_id
    if not is_bindable_id(demand_id):
        # Names no row: answered as missing before any query.
        raise InvalidInputError(f"Demand line {demand_id} does not exist.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise InvalidInputError("The correction quantity must be a positive whole number.")
    reason_text = required_text(reason, "The correction reason")
    event_id = device_event_id_text(device_event_id)
    # Identity is never part of the fingerprint — a different User is
    # refused by the actor comparison of the replay instead.
    fingerprint = _fingerprint(
        {
            "command": ALLOCATE_BEYOND_DEMAND,
            "part_number": pn,
            "work_order_demand_id": demand_id,
            "quantity": quantity,
            "reason": reason_text,
        }
    )

    # -- Idempotency fast path (SLICE1 §14) ------------------------------
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint, actor_user_id)

    # -- Locks: the Management allocation's order -------------------------
    _acquire_part_number_allocation_lock(session, pn)
    hot_ranks.acquire_hot_list_lock(session)
    scope = hot_ranks.hot_rank_scope(session, [demand_id])
    locked = _lock_demands(session, {demand_id} | scope.shift_ids)
    demand = locked[demand_id]
    work_orders = _lock_work_orders(session, {demand.work_order_id})

    # -- Idempotency RE-CHECK after the blocking locks -------------------
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint, actor_user_id)

    # -- Validation under the locks -------------------------------------
    if demand.part_number != pn:
        raise InvalidInputError(
            f"Demand line {demand.id} is for Part Number '{demand.part_number}', not"
            f" '{pn}'. Stocked quantity is allocated to its own PN's demand only."
            " Nothing was allocated."
        )
    allocated = active_allocations_by_demand(session, [demand_id]).get(demand_id, 0)
    shortage = max(demand.requested_quantity - allocated, 0)
    if quantity <= shortage:
        raise ConflictError(
            f"Demand line {demand.id} (Work Order {demand.work_order_id}) still needs"
            f" {shortage} pcs, so {quantity} pcs fit within its remaining demand. Use"
            " Allocate from stock instead — a beyond-demand correction must allocate more"
            " than the remaining demand. Nothing was allocated."
        )
    position = stock_position_of(session, pn)
    if quantity > position.available_stocked_quantity:
        raise ConflictError(
            f"Only {max(position.available_stocked_quantity, 0)} pcs of Part Number '{pn}'"
            f" are available in stock ({position.stocked_quantity} stocked,"
            f" {position.active_allocated_quantity} already allocated); {quantity} pcs"
            " cannot be allocated. A correction never exceeds the available stocked"
            " quantity. Nothing was allocated."
        )

    # -- Writes — all inside the one open transaction --------------------
    completed, reopened = _apply_completion(session, work_orders, {demand_id: quantity})
    # Automatic Hot removal (OD1), BEFORE the row is staged (as in
    # `_confirm_allocation`): the line is now more than fully allocated.
    if demand_id in scope.ranked_candidates:
        hot_ranks.remove_from_hot_list(
            session,
            scope=scope,
            locked=locked,
            removals={
                demand_id: (
                    hot_ranks.HotRemovalReason.WORK_ORDER_COMPLETED
                    if demand.work_order_id in completed
                    else hot_ranks.HotRemovalReason.FULLY_ALLOCATED
                )
            },
            action=hot_ranks.HotRankEventAction.AUTO_REMOVE,
            trigger=hot_ranks.HotRankTrigger.ALLOCATION,
            reference={
                "device_event_id": event_id,
                "source": str(AllocationSource.MANAGEMENT),
                "station_id": None,
            },
            actor_user_id=actor_user_id,
        )
    row = WorkOrderAllocation(
        part_number=pn,
        work_order_demand_id=demand_id,
        quantity=quantity,
        source=AllocationSource.MANAGEMENT,
        is_manual_override=True,
        exceeds_demand=True,
        allocation_reason=reason_text,
        reverses_allocation_id=None,
        station_id=None,
        actor_reference=None,
        allocated_by_worker_id=None,
        actor_user_id=actor_user_id,
        allocated_at=func.now(),
        device_event_id=event_id,
        command_sequence=1,
        metadata_={
            FINGERPRINT_KEY: fingerprint,
            COMMAND_KEY: {
                "kind": ALLOCATE_BEYOND_DEMAND,
                "size": 1,
                "allocation_quantity": quantity,
                "requested_quantity": demand.requested_quantity,
                "allocated_before": allocated,
                "completed_work_order_ids": completed,
                "reopened_work_order_ids": reopened,
            },
        },
    )
    session.add(row)
    demand.allocated_quantity = allocated + quantity
    demand.updated_at = func.now()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_allocation_command(session, event_id)
            if winner:
                return _replay_or_conflict(session, winner, fingerprint, actor_user_id)
        raise
    return _result_from_rows(session, [row], created=True)


# ---------------------------------------------------------------------------
# The Management allocation context (Phase 14 slice 5, a read)
# ---------------------------------------------------------------------------


class ContextAllocation(NamedTuple):
    """One active allocation row of a line, with who recorded it."""

    allocation: WorkOrderAllocation
    actor_user: user_access.UserRef | None


class ContextLine(NamedTuple):
    """One demand line as the Management allocation workflow judges it."""

    demand: WorkOrderDemand
    work_order: WorkOrder
    # Derived from the rows, never the projection.
    allocated_quantity: int
    # Not reversed, not reversals; oldest first.
    active_allocations: list[ContextAllocation]

    @property
    def remaining_shortage(self) -> int:
        return max(self.demand.requested_quantity - self.allocated_quantity, 0)

    @property
    def beyond_demand_quantity(self) -> int:
        return max(self.allocated_quantity - self.demand.requested_quantity, 0)


class AllocationContext(NamedTuple):
    part_number: str
    position: StockPosition
    lines: list[ContextLine]


def management_allocation_context(
    session: Session, *, part_number: object | None = None, work_order_demand_id: int | None = None
) -> AllocationContext:
    """The stock figures and demand lines a Management allocation, reversal
    or beyond-demand correction is prepared from (a read — nothing is
    locked or written; every write re-judges under its own locks).

    Exactly one scope: a PN — every demand line of its OPEN Work Orders
    (fully and over-allocated lines included) in the canonical demand
    order, Tracking's Active WO Demand scope; or one demand line —
    whatever its Work Order's state (a completed Work Order's allocation
    is adjusted from its Work Order Details).
    """
    if (part_number is None) == (work_order_demand_id is None):
        raise InvalidInputError("Give exactly one of part_number or work_order_demand_id.")
    pairs: list[tuple[WorkOrderDemand, WorkOrder]]
    if work_order_demand_id is not None:
        missing = NotFoundError(f"Demand line {work_order_demand_id} does not exist.")
        if not is_bindable_id(work_order_demand_id):
            raise missing
        found = session.execute(
            select(WorkOrderDemand, WorkOrder)
            .join(WorkOrder, WorkOrder.id == WorkOrderDemand.work_order_id)
            .where(WorkOrderDemand.id == work_order_demand_id)
        ).one_or_none()
        if found is None:
            raise missing
        demand, work_order = found
        pn = demand.part_number
        pairs = [(demand, work_order)]
    else:
        pn = canonical_part_number(part_number)
        pairs = [
            (context.demand, context.work_order)
            for context in open_demand_context(session, [pn]).get(pn, [])
        ]
    demand_ids = [demand.id for demand, _ in pairs]
    allocated = active_allocations_by_demand(session, demand_ids)
    rows: list[WorkOrderAllocation] = (
        list(
            session.scalars(
                _effective_allocation_rows()
                .where(WorkOrderAllocation.work_order_demand_id.in_(demand_ids))
                .order_by(WorkOrderAllocation.id)
            )
        )
        if demand_ids
        else []
    )
    actors = user_access.user_refs(
        session, [row.actor_user_id for row in rows if row.actor_user_id is not None]
    )
    by_demand: dict[int, list[ContextAllocation]] = {}
    for row in rows:
        by_demand.setdefault(row.work_order_demand_id, []).append(
            ContextAllocation(
                row, actors.get(row.actor_user_id) if row.actor_user_id is not None else None
            )
        )
    return AllocationContext(
        part_number=pn,
        position=stock_position_of(session, pn),
        lines=[
            ContextLine(
                demand=demand,
                work_order=work_order,
                allocated_quantity=allocated.get(demand.id, 0),
                active_allocations=by_demand.get(demand.id, []),
            )
            for demand, work_order in pairs
        ],
    )


# ---------------------------------------------------------------------------
# Listing (audit visibility) and the projection replays
# ---------------------------------------------------------------------------


def list_allocations(
    session: Session,
    *,
    part_number: object | None = None,
    work_order_demand_id: int | None = None,
    work_order_id: int | None = None,
) -> list[WorkOrderAllocation]:
    """Every allocation row (allocations and reversals) matching the filters, oldest first."""
    query = select(WorkOrderAllocation)
    if part_number is not None:
        query = query.where(WorkOrderAllocation.part_number == canonical_part_number(part_number))
    if work_order_demand_id is not None:
        query = query.where(WorkOrderAllocation.work_order_demand_id == work_order_demand_id)
    if work_order_id is not None:
        query = query.where(
            WorkOrderAllocation.work_order_demand_id.in_(
                select(WorkOrderDemand.id).where(WorkOrderDemand.work_order_id == work_order_id)
            )
        )
    return list(session.scalars(query.order_by(WorkOrderAllocation.id)))


def rebuild_allocated_quantities(session: Session) -> dict[int, int]:
    """Every demand's active allocation from the rows alone (the projection replay)."""
    reversal = aliased(WorkOrderAllocation)
    rows = session.execute(
        select(WorkOrderAllocation.work_order_demand_id, func.sum(WorkOrderAllocation.quantity))
        .where(
            WorkOrderAllocation.reverses_allocation_id.is_(None),
            ~select(reversal.id)
            .where(reversal.reverses_allocation_id == WorkOrderAllocation.id)
            .exists(),
        )
        .group_by(WorkOrderAllocation.work_order_demand_id)
    )
    return {int(demand_id): int(total) for demand_id, total in rows}


def rebuild_completed_at(session: Session) -> dict[int, datetime.datetime | None]:
    """Every Work Order's done date from the allocation rows and the
    demand-change completion audit rows (a read: plain SELECTs, no lock).

    The allocation commands are replayed in commit order — rows grouped
    by ``device_event_id``, commands ordered by their smallest row id
    (two commands touching one Work Order serialize on its row lock and
    insert after it, so their ids follow commit order; ``allocated_at``,
    the transaction start, does not). After each command every Work
    Order it touched is judged against its CURRENT lines: turning
    complete sets its replayed done date to the command's
    ``allocated_at``, turning incomplete clears it; staying complete
    keeps it — a beyond-demand correction, or a reversal leaving a Work
    Order complete, never moves the done date (Phase 14 slice 5).

    A Work Order not complete now (every current line's active
    allocation covers its requested quantity) is ``None``; otherwise
    the newer of its replayed done date and its newest demand-change
    completion (``complete_after_demand_change`` — the audit row's
    ``occurred_at`` is its ``completed_at``). A completed Work Order
    takes no demand change, so its requested quantities are frozen from
    its last completing event on: the replay is exact up to that event,
    and when the event was a demand change the replay turned complete
    at an earlier allocation command, so the audit row is the newer.
    """
    lines = list(
        session.execute(
            select(
                WorkOrderDemand.work_order_id,
                WorkOrderDemand.id,
                WorkOrderDemand.requested_quantity,
            )
        )
    )
    by_work_order: dict[int, list[tuple[int, int]]] = {}
    work_order_of: dict[int, int] = {}
    for work_order_id, demand_id, requested in lines:
        by_work_order.setdefault(int(work_order_id), []).append((int(demand_id), int(requested)))
        work_order_of[int(demand_id)] = int(work_order_id)
    # Rows in id order, so a command's first appearance is its smallest id.
    commands: dict[str, list[tuple[int, int, bool, datetime.datetime]]] = {}
    for event_id, demand_id, quantity, reverses_id, allocated_at in session.execute(
        select(
            WorkOrderAllocation.device_event_id,
            WorkOrderAllocation.work_order_demand_id,
            WorkOrderAllocation.quantity,
            WorkOrderAllocation.reverses_allocation_id,
            WorkOrderAllocation.allocated_at,
        ).order_by(WorkOrderAllocation.id)
    ):
        commands.setdefault(str(event_id), []).append(
            (int(demand_id), int(quantity), reverses_id is not None, allocated_at)
        )

    allocated: dict[int, int] = {}

    def is_complete(work_order_id: int) -> bool:
        demands = by_work_order.get(work_order_id, [])
        return bool(demands) and all(
            allocated.get(demand_id, 0) >= requested for demand_id, requested in demands
        )

    replayed: dict[int, datetime.datetime | None] = {}
    for rows in commands.values():
        touched: set[int] = set()
        for demand_id, quantity, is_reversal, _ in rows:
            allocated[demand_id] = allocated.get(demand_id, 0) + (
                -quantity if is_reversal else quantity
            )
            touched.add(work_order_of[demand_id])
        stamp = rows[0][3]
        for work_order_id in sorted(touched):
            if is_complete(work_order_id):
                if replayed.get(work_order_id) is None:
                    replayed[work_order_id] = stamp
            else:
                replayed[work_order_id] = None

    audited: dict[int, datetime.datetime] = {
        int(entity_id): stamp
        for entity_id, stamp in session.execute(
            select(AuditEvent.entity_id, func.max(AuditEvent.occurred_at))
            .where(
                AuditEvent.entity_type == AuditEntityType.WORK_ORDER,
                AuditEvent.metadata_.has_key(COMPLETION_AUDIT_KEY),
            )
            .group_by(AuditEvent.entity_id)
        )
    }
    result: dict[int, datetime.datetime | None] = {}
    for work_order_id in by_work_order:
        if not is_complete(work_order_id):
            result[work_order_id] = None
            continue
        stamps = [
            stamp
            for stamp in (replayed.get(work_order_id), audited.get(work_order_id))
            if stamp is not None
        ]
        result[work_order_id] = max(stamps) if stamps else None
    return result
