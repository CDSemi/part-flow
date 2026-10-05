"""Hot rank maintenance shared by every rank writer (Phase 12 follow-up — OD1 / OD3).

``work_order_demands.priority_rank`` has exactly two writers: the Hot
list command (``app.application.hot_list``) and
:func:`remove_from_hot_list` here, which takes an entry off the list
when the change that caused it is not a Hot list change:

- **Automatic removal** (owner decision OD1, PROJECT_PROFILE §14, §21):
  a Hot entry whose demand becomes inactive — the line fully allocated
  (``requested_quantity <= allocated_quantity``), which includes its
  Work Order completing — leaves the list in the same transaction as
  the allocation confirmation (``allocations.confirm_allocation``) or
  the quantity-lowering Work Order save
  (``work_orders.update_work_order``) that made it inactive. No
  confirmation is asked: the triggering action is the confirmed one. A
  reversal or quantity increase that makes the demand active again
  re-adds nothing (invariant H2: no ranked demand is inactive).
- **Confirmed line deletion** (owner decision OD3, PROJECT_PROFILE
  §13): the typed-confirmed removal of a Hot demand line takes it off
  the list and deletes the line in one transaction
  (``work_orders.delete_work_order_demand``).

Either way the remaining ranks close the gap (invariant H1 — the
ranks stay exactly 1..N) and every rank change is audited exactly like
a Hot list change (one ``UPDATED`` ``WorkOrderDemand`` row per changed
demand) with metadata that names the cause.

This is a leaf module — it imports neither ``hot_list`` nor
``allocations`` nor ``work_orders`` — so allocation and the Work Order
save can use it without an import cycle. Callers reach every function
through the module attribute (``hot_ranks.<fn>``) so tests can pause
them.

Lock order (one global order for every transaction): the PN advisory
locks (ascending) → the Hot advisory lock → a Scan Station row (FOR
KEY SHARE for allocation and reversal) → demand rows FOR UPDATE in ONE
ascending pass → Work Order rows FOR UPDATE ascending. A holder of the
Hot lock locks, in that one pass, every demand row whose rank it may
write — its own lines plus :attr:`HotRankScope.shift_ids` — before any
Work Order row.
"""

from collections.abc import Collection, Mapping
from enum import StrEnum
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import flush
from app.domain.enums import AuditEntityType, AuditEventType
from app.domain.hot_list import close_gaps, removal_shift_scope
from app.infrastructure.models import WorkOrder, WorkOrderDemand

#: The metadata block of every Hot rank audit row (the Hot list
#: command's and this module's); the command's ``device_event_id`` in it
#: is indexed (`ix_audit_events_hot_list_device_event_id`).
HOT_LIST_CHANGE_KEY: Final = "hot_list_change"

#: The ONE advisory lock serializing every Hot rank writer.
_HOT_LIST_LOCK_KEY: Final = "partflow:hot-list"

# A defect guard only: every rank writer holds the Hot lock, so the
# UNIQUE rank can never be lost to a race at flush or COMMIT.
HOT_RANK_CONFLICTS: Final = {
    "uq_work_order_demands_priority_rank": (
        "The Hot list changed while this change was being saved. Nothing was saved — try again."
    )
}


def acquire_hot_list_lock(session: Session) -> None:
    """Serialize this transaction against every other Hot rank writer.

    ``pg_advisory_xact_lock`` releases with the transaction. The Hot
    lock takers are the Hot list command, an allocation confirmation, a
    Work Order save that edits any requested quantity and a demand-line
    deletion confirmed for a Hot line. Each takes it after its PN
    advisory locks and before any row lock, and no transaction takes a
    PN lock after it.

    The key is a 64-bit hash (``hashtextextended``). A collision with a
    PN lock key would merge the two advisory locks and could, in theory,
    invert the PN → Hot order (probability about N/2^64); PostgreSQL's
    deadlock detector would then abort one transaction (40P01) with
    nothing committed.
    """
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(_HOT_LIST_LOCK_KEY, 0)))
    )


class HotRankScope(NamedTuple):
    """The ranks a removal outside the command judges and may write."""

    # Every ranked demand id → its stored rank, read under the Hot lock.
    ranks: dict[int, int]
    # The caller's candidate ids that are ranked.
    ranked_candidates: frozenset[int]
    # ``removal_shift_scope(ranks, candidates)`` — ranked ids that are not
    # candidates but can shift; the caller locks them with its own rows.
    shift_ids: frozenset[int]


def hot_rank_scope(session: Session, candidate_ids: Collection[int]) -> HotRankScope:
    """The current ranks and the rows a removal of candidates can touch.

    The caller MUST already hold the Hot lock: the ranks are read
    without row locks and are stable because every rank writer holds
    that lock. Called exactly once per Hot-lock-taking allocation,
    quantity-editing save or confirmed Hot line deletion — after the Hot
    lock and before any demand row lock (the test seam for that point).
    """
    ranks = {
        int(demand_id): int(rank)
        for demand_id, rank in session.execute(
            select(WorkOrderDemand.id, WorkOrderDemand.priority_rank).where(
                WorkOrderDemand.priority_rank.is_not(None)
            )
        )
    }
    candidates = frozenset(int(demand_id) for demand_id in candidate_ids)
    return HotRankScope(
        ranks=ranks,
        ranked_candidates=frozenset(demand_id for demand_id in candidates if demand_id in ranks),
        shift_ids=removal_shift_scope(ranks, candidates),
    )


class HotRemovalReason(StrEnum):
    """Why one entry left the Hot list outside the command."""

    FULLY_ALLOCATED = "FULLY_ALLOCATED"
    WORK_ORDER_COMPLETED = "WORK_ORDER_COMPLETED"
    LINE_DELETED = "LINE_DELETED"


class HotRankEventAction(StrEnum):
    """The audit ``action`` of a rank change made outside the command.

    ``AUTO_REMOVE`` is automatic (no confirmation); ``LINE_DELETE`` is the
    manager's typed-confirmed demand-line deletion.
    """

    AUTO_REMOVE = "AUTO_REMOVE"
    LINE_DELETE = "LINE_DELETE"


class HotRankTrigger(StrEnum):
    """The command that caused a rank change outside the Hot list command."""

    ALLOCATION = "ALLOCATION"
    WORK_ORDER_SAVE = "WORK_ORDER_SAVE"
    DEMAND_LINE_REMOVAL = "DEMAND_LINE_REMOVAL"


class HotRankChange(NamedTuple):
    """One demand whose rank a removal cleared or shifted."""

    work_order_demand_id: int
    part_number: str
    work_order_number: str | None
    previous_rank: int
    new_rank: int | None


def remove_from_hot_list(
    session: Session,
    *,
    scope: HotRankScope,
    locked: Mapping[int, WorkOrderDemand],
    removals: Mapping[int, HotRemovalReason],
    action: HotRankEventAction,
    trigger: HotRankTrigger,
    reference: Mapping[str, Any],
    actor: str | None,
) -> list[HotRankChange]:
    """Take ``removals`` off the Hot list, close the gaps and audit every change.

    Stages the writes in the caller's open transaction; the caller
    commits. ``locked`` holds the demand rows the caller locked FOR
    UPDATE (and re-read) in its one ascending pass — its own lines plus
    ``scope.shift_ids``. Nothing whose flush can lose an idempotency or
    uniqueness race the caller translates only at COMMIT may be pending
    when this is called: the two flushes here would surface it first.
    """
    if not removals:
        return []
    # -- Defect guards: unreachable under the lock protocol ---------------
    stray = sorted(set(removals) - scope.ranked_candidates)
    if stray:
        raise RuntimeError(f"Hot removal of demands that are not ranked candidates: {stray}.")
    new_ranks = close_gaps(scope.ranks, removals)
    unlocked = sorted(set(new_ranks) - set(locked))
    if unlocked:
        raise RuntimeError(f"Hot removal would change ranks of unlocked demands: {unlocked}.")
    drifted = sorted(
        demand_id
        for demand_id in new_ranks
        if locked[demand_id].priority_rank != scope.ranks[demand_id]
    )
    if drifted:
        raise RuntimeError(f"Hot ranks changed under the Hot lock: {drifted}.")

    # -- Identity snapshot (no lock: the number is display metadata) ------
    work_orders = {
        work_order.id: work_order
        for work_order in session.scalars(
            select(WorkOrder).where(
                WorkOrder.id.in_({locked[demand_id].work_order_id for demand_id in new_ranks})
            )
        )
    }

    # -- Writes: clear first, then assign, so the UNIQUE rank never sees
    # a transient duplicate ---------------------------------------------------
    for demand_id in new_ranks:
        locked[demand_id].priority_rank = None
    flush(session, HOT_RANK_CONFLICTS)
    for demand_id, rank in new_ranks.items():
        locked[demand_id].priority_rank = rank
        locked[demand_id].updated_at = func.now()
    flush(session, HOT_RANK_CONFLICTS)

    changes = sorted(
        (
            HotRankChange(
                work_order_demand_id=demand_id,
                part_number=locked[demand_id].part_number,
                work_order_number=work_orders[locked[demand_id].work_order_id].work_order_number,
                previous_rank=scope.ranks[demand_id],
                new_rank=rank,
            )
            for demand_id, rank in new_ranks.items()
        ),
        key=lambda change: (
            change.new_rank is None,
            change.new_rank or 0,
            change.work_order_demand_id,
        ),
    )
    # Every row of this transaction carries the identical cause; no
    # ``device_event_id`` or ``fingerprint`` at the block level, so the
    # Hot command's idempotency lookup never matches these rows.
    cause = {
        "trigger": str(trigger),
        "reference": dict(reference),
        "removed": [
            {"work_order_demand_id": demand_id, "reason": str(removals[demand_id])}
            for demand_id in sorted(removals)
        ],
    }
    for sequence, change in enumerate(changes, start=1):
        audit.append_audit_event(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type=AuditEntityType.WORK_ORDER_DEMAND,
            entity_id=str(change.work_order_demand_id),
            before_data={"priority_rank": change.previous_rank},
            after_data={"priority_rank": change.new_rank},
            actor_reference=actor,
            metadata={
                HOT_LIST_CHANGE_KEY: {
                    "action": str(action),
                    "sequence": sequence,
                    "work_order_demand_id": change.work_order_demand_id,
                    "part_number": change.part_number,
                    "work_order_id": locked[change.work_order_demand_id].work_order_id,
                    "work_order_number": change.work_order_number,
                    "cause": cause,
                }
            },
        )
    return changes
