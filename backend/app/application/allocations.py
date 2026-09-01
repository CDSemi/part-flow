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
  allocation beyond the remaining shortage is refused (no "explicitly
  authorized correction" exists before Phase 14 authorization, so none
  is simulated). Both are judged inside ONE transaction under a
  per-PN advisory lock (serializing every allocation and reversal of
  one PN, so two concurrent confirmations can never jointly exceed the
  available quantity) plus `FOR UPDATE` on every affected demand row
  (serializing against the demand edit's committed-quantity floor and
  against a release) and on every affected Work Order row (the
  completion projection).
- **Operator adjustment** (§18 Receiving Confirmation): the confirmed
  lines may differ from the suggestion; the command re-computes the
  canonical suggestion for the confirmed total under the locks and
  records `is_manual_override` on every line that differs — audit
  context, never a rule.
- **Completion is derived** (§8.2, §18 Work Order Completion): a Work
  Order is complete when every one of its demand lines is fully
  allocated. `work_orders.completed_at` is the persisted done-date
  projection: set to the timestamp of the allocation event that fully
  allocated the last open line, cleared by a reversal that reopens
  one, rebuildable from the allocation rows alone
  (`rebuild_completed_at`). A completed Work Order leaves the active
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
- Deliberately absent: authorization for Management adjustments
  (Phase 14 — the `source` and the reference-free `actor_reference`
  record who/where without pretending to authorize), Worker identity
  (Phase 13), and any return of stocked quantity to production
  (PROJECT_PROFILE §32 open decision 1).
"""

import datetime
import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Final, NamedTuple

from sqlalchemy import Select, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.application.common import device_event_id_text, optional_text, required_text
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.part_numbers import canonical_part_number
from app.application.projections import stocked_quantity_of
from app.domain.enums import AllocationSource
from app.infrastructure.models import (
    ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT,
    Area,
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

# Namespace of the per-PN advisory lock: distinct from the release's
# namespace, so allocation serialization never collides with it.
_ALLOCATION_LOCK_NAMESPACE: Final = "partflow:allocation:part-number:"
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


def active_allocated_quantity_of(session: Session, part_number: str) -> int:
    """The PN's total ACTIVE allocation, derived from the rows."""
    reversal = aliased(WorkOrderAllocation)
    total = session.scalar(
        select(func.coalesce(func.sum(WorkOrderAllocation.quantity), 0)).where(
            WorkOrderAllocation.part_number == part_number,
            WorkOrderAllocation.reverses_allocation_id.is_(None),
            ~select(reversal.id)
            .where(reversal.reverses_allocation_id == WorkOrderAllocation.id)
            .exists(),
        )
    )
    return int(total or 0)


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
    allocation_reason: str | None
    reverses_allocation_id: int | None
    station_id: str | None
    actor_reference: str | None
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
                allocation_reason=row.allocation_reason,
                reverses_allocation_id=row.reverses_allocation_id,
                station_id=row.station_id,
                actor_reference=row.actor_reference,
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
    session: Session, rows: Sequence[WorkOrderAllocation], fingerprint: str
) -> AllocationResult:
    stored = (rows[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    if stored != fingerprint or any(
        (row.metadata_ or {}).get(FINGERPRINT_KEY) != stored for row in rows
    ):
        raise _reused_for_other_intent()
    return _result_from_rows(session, rows, created=False)


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


def _acquire_part_number_allocation_lock(session: Session, part_number: str) -> None:
    """Serialize every allocation and reversal of one PN for this transaction.

    Available stocked quantity is a PN-level figure: two confirmations
    of the same PN judged against the same snapshot could jointly
    exceed it, so they queue here. Stocking only ever ADDS availability
    (a `STOCKED` command is never undone), so the stocked side needs
    no lock — a reading under this lock is at worst conservative.
    """
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"{_ALLOCATION_LOCK_NAMESPACE}{part_number}", 0)
            )
        )
    )


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

    Read for audit identity only: the allocation itself is PN-level and
    needs no station lock (the STOCKED Movement was recorded under the
    station lock already).
    """
    station = session.get(ScanStation, station_id)
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


def confirm_allocation(
    session: Session,
    *,
    part_number: object,
    allocation_quantity: object,
    lines: Sequence[Mapping[str, Any]],
    station_id: str | None = None,
    actor: str | None = None,
    reason: str | None = None,
    device_event_id: object,
) -> AllocationResult:
    """Allocate stocked quantity of one PN to demand lines, ONE transaction.

    The Stockroom receiving confirmation (``station_id`` set — the
    routine Operator workflow, PROJECT_PROFILE §18) and a Management
    allocation (``station_id`` None) are the same command.
    ``allocation_quantity`` is the explicit quantity being allocated:
    the lines must sum to exactly it, and the PN's available stocked
    quantity must still cover it when the command is judged under the
    locks. Order: input shape → fingerprint → idempotency fast path →
    the per-PN advisory lock → the demand row locks (ascending) → the
    Work Order row locks (ascending) → idempotency re-check →
    validation under the locks (PN agreement, shortage per line,
    available stocked quantity for the allocation quantity) → the
    rows, the projection and the completion → COMMIT (or replay of a
    race winner).
    """
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
    reason_text = optional_text(reason)
    event_id = device_event_id_text(device_event_id)
    source = AllocationSource.STOCKROOM if station_id is not None else AllocationSource.MANAGEMENT
    fingerprint = _fingerprint(
        {
            "command": "ALLOCATE",
            "part_number": pn,
            "allocation_quantity": allocation_quantity,
            "lines": [list(line) for line in sorted(confirmed)],
            "station_id": station_id,
            "actor": actor,
            "reason": reason_text,
        }
    )

    # -- Idempotency fast path (SLICE1 §14) ------------------------------
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

    station = _require_stockroom_station(session, station_id) if station_id is not None else None

    # -- Serialize per PN, then lock the demand and Work Order rows -----
    _acquire_part_number_allocation_lock(session, pn)
    demands = _lock_demands(session, [line.work_order_demand_id for line in confirmed])
    work_orders = _lock_work_orders(session, {demand.work_order_id for demand in demands.values()})

    # -- Idempotency RE-CHECK after the blocking locks -------------------
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

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

    # -- Writes — all inside the one open transaction --------------------
    # The completion effect is judged first (append-only rows carry it
    # in their metadata — part of the immutable record, replayed
    # verbatim, never re-derived from a later state).
    deltas = {line.work_order_demand_id: line.quantity for line in confirmed}
    completed, reopened = _apply_completion(session, work_orders, deltas)
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
            actor_reference=actor,
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
                return _replay_or_conflict(session, winner, fingerprint)
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
    station_id: str | None = None,
    actor: str | None = None,
    device_event_id: object,
) -> AllocationResult:
    """Take one allocation back — the auditable adjustment (§8.12), ONE transaction.

    Appends a REVERSAL row referencing the allocation (UNIQUE — once),
    returns the quantity to the PN's available stock, lowers the
    demand's projection and reopens the Work Order when it was
    complete. The original row is never touched. A smaller allocation
    is this reversal followed by a new confirmation.
    """
    reason_text = required_text(reason, "The adjustment reason")
    event_id = device_event_id_text(device_event_id)
    fingerprint = _fingerprint(
        {
            "command": "REVERSE_ALLOCATION",
            "allocation_id": allocation_id,
            "reason": reason_text,
            "station_id": station_id,
            "actor": actor,
        }
    )
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

    station = _require_stockroom_station(session, station_id) if station_id is not None else None
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
    demand = _lock_demands(session, [original.work_order_demand_id])[original.work_order_demand_id]
    work_order = _lock_work_orders(session, [demand.work_order_id])[demand.work_order_id]
    committed = committed_allocation_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)
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
        source=AllocationSource.STOCKROOM if station is not None else AllocationSource.MANAGEMENT,
        is_manual_override=True,
        allocation_reason=reason_text,
        reverses_allocation_id=original.id,
        station_id=station.station_id if station is not None else None,
        actor_reference=actor,
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
                return _replay_or_conflict(session, winner, fingerprint)
        if constraint == _REVERSES_UNIQUE_CONSTRAINT:
            raise ConflictError(
                f"Allocation {allocation_id} has already been reversed. Nothing was recorded."
            ) from exc
        raise
    return _result_from_rows(session, [row], created=True)


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
    """Every Work Order's done date from the allocation rows alone.

    Complete exactly when every demand line's active allocation covers
    its requested quantity; the done date is then the newest effective
    allocation row's `allocated_at` — the event that completed the last
    open line (after a reversal reopened a Work Order, the allocation
    that completes it again is newer than every earlier row).
    """
    allocated = rebuild_allocated_quantities(session)
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
    for work_order_id, demand_id, requested in lines:
        by_work_order.setdefault(int(work_order_id), []).append((int(demand_id), int(requested)))
    effective = _effective_allocation_rows().subquery()
    newest = {
        int(work_order_id): stamp
        for work_order_id, stamp in session.execute(
            select(WorkOrderDemand.work_order_id, func.max(effective.c.allocated_at))
            .join(WorkOrderDemand, WorkOrderDemand.id == effective.c.work_order_demand_id)
            .group_by(WorkOrderDemand.work_order_id)
        )
    }
    return {
        work_order_id: (
            newest.get(work_order_id)
            if demands
            and all(allocated.get(demand_id, 0) >= requested for demand_id, requested in demands)
            else None
        )
        for work_order_id, demands in by_work_order.items()
    }
