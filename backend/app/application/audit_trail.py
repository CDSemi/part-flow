"""The PN audit trail (Phase 14 slice 7 — PROJECT_PROFILE §21 Tracking
"correction history", §28; owner decision OD-P15: scoped to one PN).

One read-only, keyset-paged reader of everything recorded about one
canonical PN outside its production Movements:

- **Scope** (recomputed on every page, nothing cached between pages):
  the ``audit_events`` rows of the PN's master (``PartNumber``), of every
  Work Order Demand line that ever requested the PN — a line deleted
  since is recovered from its own ``CREATED`` row (a saved line's PN
  never changes, so a line belongs to one PN for life) — of their Work
  Orders (header edits and the completion a demand change caused, shared
  by every PN the Work Order requests) and of the AssignedRoutes of the
  PN's Quantity Flows (``ROUTE_ADJUSTED``); plus the PN's
  ``work_order_allocations`` rows that Management recorded or that
  reverse an allocation (their own audit record). Routine Stockroom
  confirmations stay in the allocation history and production Movements
  in the Movement history; configuration entities, Users, Roles, policies
  and station devices are never in any PN's scope.
- **Order**: newest first — ``(at DESC, source_rank DESC, id DESC)`` over
  one ``UNION ALL`` of both tables (an audit row ranks before an
  allocation row of the same instant). The cursor names the last entry a
  page delivered; the server resolves its time within the PN's scope, so
  pages never repeat a row and never skip one committed before the first
  page was read. ``at`` is the writer's transaction-start time
  (``func.now()``): a row committed while the trail is open may appear on
  a later page or only after reopening — never lost.
- **Presentation data only**: each entry carries its kind, the subject
  (Work Order, demand line, Quantity Flow), the recorded User (or the
  legacy actor text), the reason, the displayed field changes (image
  digests reduced to booleans), the Hot list cause, the allocation facts
  or the replaced and new route tail. Idempotency keys, fingerprints,
  command references and digests are never returned.

Plain ``SELECT``s under the request's session: no row lock, no advisory
lock — the reader never waits on and never blocks a writer. Nothing is
written and the read itself is not audited.
"""

import datetime
from collections.abc import Collection, Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Final, NamedTuple

from sqlalchemy import ColumnElement, Integer, Text, func, literal, or_, select, tuple_, union_all
from sqlalchemy.orm import Session

from app.application import tracking, user_access
from app.application.allocations import COMPLETION_AUDIT_KEY
from app.application.errors import InvalidInputError, NotFoundError
from app.application.hot_ranks import HOT_LIST_CHANGE_KEY
from app.application.route_adjustments import ROUTE_ADJUSTMENT_KEY
from app.domain.enums import AllocationSource, AuditEntityType, AuditEventType
from app.infrastructure.models import (
    Area,
    AuditEvent,
    Machine,
    Operation,
    QuantityFlow,
    WorkOrder,
    WorkOrderAllocation,
    WorkOrderDemand,
)


class AuditTrailSource(StrEnum):
    AUDIT = "AUDIT"  # an audit_events row
    ALLOCATION = "ALLOCATION"  # a work_order_allocations row


class AuditTrailKind(StrEnum):
    PART_NUMBER_CREATED = "PART_NUMBER_CREATED"
    PART_NUMBER_UPDATED = "PART_NUMBER_UPDATED"
    PART_NUMBER_IMAGE_CHANGED = "PART_NUMBER_IMAGE_CHANGED"
    PART_NUMBER_DELETED = "PART_NUMBER_DELETED"
    WORK_ORDER_CREATED = "WORK_ORDER_CREATED"
    WORK_ORDER_UPDATED = "WORK_ORDER_UPDATED"
    # The completion a demand save or a line deletion caused (the
    # allocation rows are the record of an allocation-caused one).
    WORK_ORDER_COMPLETED = "WORK_ORDER_COMPLETED"
    DEMAND_CREATED = "DEMAND_CREATED"
    DEMAND_UPDATED = "DEMAND_UPDATED"
    PRIORITY_CHANGED = "PRIORITY_CHANGED"
    ROUTE_ADJUSTED = "ROUTE_ADJUSTED"
    ALLOCATED = "ALLOCATED"
    ALLOCATION_REVERSED = "ALLOCATION_REVERSED"
    ALLOCATED_BEYOND_DEMAND = "ALLOCATED_BEYOND_DEMAND"
    # Total-function fallback for a combination no writer produces.
    CHANGE_RECORDED = "CHANGE_RECORDED"


# Displayed fields per audited entity, in display order (values compared
# and returned verbatim from the JSON).
TRAIL_FIELDS: Final[Mapping[AuditEntityType, tuple[str, ...]]] = {
    AuditEntityType.PART_NUMBER: ("name", "current_revision", "erp_id", "image"),
    AuditEntityType.WORK_ORDER: ("work_order_number", "received_date", "due_date", "status"),
    AuditEntityType.WORK_ORDER_DEMAND: (
        "request_type",
        "requested_quantity",
        "due_date",
        "job_numbers",
        "requester",
        "reason",
        "notes",
        "priority_rank",
    ),
}
# Identity keys shown through the entry's subject, never as a change.
IDENTITY_FIELDS: Final[Mapping[AuditEntityType, tuple[str, ...]]] = {
    AuditEntityType.PART_NUMBER: ("part_number",),
    AuditEntityType.WORK_ORDER_DEMAND: ("work_order_id", "part_number"),
}
# Keys a kind label states by itself, never returned as a change.
KIND_ONLY_FIELDS: Final[Mapping[AuditEntityType, tuple[str, ...]]] = {
    AuditEntityType.WORK_ORDER: ("completed_at",),
}
DEFAULT_TRAIL_LIMIT: Final = 50
MAX_TRAIL_LIMIT: Final = 200

# The sort rank of each source among rows of one instant (DESC order).
_SOURCE_RANK: Final[Mapping[AuditTrailSource, int]] = {
    AuditTrailSource.AUDIT: 1,
    AuditTrailSource.ALLOCATION: 0,
}
_IMAGE_FIELD: Final = "image"
_BOTH_OR_NEITHER: Final = "Give both before_source and before_id, or neither."

TrailValue = bool | int | str | list[str] | None


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


class TrailCursor(NamedTuple):
    """The last entry a page delivered."""

    source: AuditTrailSource
    id: int


class TrailChange(NamedTuple):
    field: str
    before: TrailValue
    after: TrailValue


class TrailSubject(NamedTuple):
    work_order_id: int | None
    # The Work Order's CURRENT number; None for an internal Work Order.
    work_order_number: str | None
    work_order_demand_id: int | None
    # None unless work_order_demand_id is set; False for a deleted line.
    demand_exists: bool | None
    quantity_flow_id: int | None


class TrailPriority(NamedTuple):
    # Passed through unvalidated (forward-compatible vocabularies).
    action: str | None
    trigger: str | None
    removal_reason: str | None


class TrailAllocation(NamedTuple):
    quantity: int
    source: str
    is_manual_override: bool
    exceeds_demand: bool
    reverses_allocation_id: int | None
    station_id: str | None


class TrailRouteStep(NamedTuple):
    sequence: int
    area: Area
    operation: Operation | None
    expected_duration: datetime.timedelta | None
    # Read by id — a retired Machine is still named.
    preferred_machine: Machine | None
    instructions: str | None


class TrailRoute(NamedTuple):
    kept_through_sequence: int
    # The replaced tail and the new tail (steps after the kept-through
    # sequence), ascending.
    before_steps: list[TrailRouteStep]
    after_steps: list[TrailRouteStep]


class TrailEntry(NamedTuple):
    source: AuditTrailSource
    id: int
    occurred_at: datetime.datetime
    kind: AuditTrailKind
    actor_user: user_access.UserRef | None
    # The legacy actor text of a row recorded without a User.
    legacy_actor: str | None
    reason: str | None
    subject: TrailSubject
    changes: list[TrailChange]
    priority: TrailPriority | None
    completion_trigger: str | None
    allocation: TrailAllocation | None
    route: TrailRoute | None


class TrailPage(NamedTuple):
    part_number: str
    entries: list[TrailEntry]
    total: int
    has_more: bool
    # The cursor of the next (older) page; None on the last page.
    next_before: TrailCursor | None


class TrailScope(NamedTuple):
    """Everything one PN's trail reads, by id (§ scope in the module doc)."""

    part_number: str
    # Every demand line that ever requested the PN → its Work Order.
    demand_work_order: dict[int, int]
    # The lines that still exist (the others were deleted since).
    existing_demand_ids: frozenset[int]
    # The AssignedRoute of each of the PN's flows → that flow.
    route_flow: dict[int, int]


_NO_SUBJECT: Final = TrailSubject(None, None, None, None, None)


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def _entity(entity_type: str) -> AuditEntityType | None:
    try:
        return AuditEntityType(entity_type)
    except ValueError:
        return None


def _block(metadata: Mapping[str, Any] | None, key: str) -> dict[str, Any] | None:
    block = (metadata or {}).get(key)
    return block if isinstance(block, dict) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def classify_audit_row(
    entity_type: str,
    event_type: str,
    before_data: Mapping[str, Any] | None,
    after_data: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
) -> AuditTrailKind:
    """The kind of one audit row (a total function: an unexpected
    combination is ``CHANGE_RECORDED``, never an error)."""
    if entity_type == AuditEntityType.PART_NUMBER:
        if event_type == AuditEventType.CREATED:
            return AuditTrailKind.PART_NUMBER_CREATED
        if event_type == AuditEventType.UPDATED:
            keys = set(before_data or {}) | set(after_data or {})
            if keys == {_IMAGE_FIELD}:
                return AuditTrailKind.PART_NUMBER_IMAGE_CHANGED
            return AuditTrailKind.PART_NUMBER_UPDATED
        if event_type == AuditEventType.DELETED:
            return AuditTrailKind.PART_NUMBER_DELETED
    elif entity_type == AuditEntityType.WORK_ORDER:
        if event_type == AuditEventType.CREATED:
            return AuditTrailKind.WORK_ORDER_CREATED
        if event_type == AuditEventType.UPDATED:
            if COMPLETION_AUDIT_KEY in (metadata or {}):
                return AuditTrailKind.WORK_ORDER_COMPLETED
            return AuditTrailKind.WORK_ORDER_UPDATED
    elif entity_type == AuditEntityType.WORK_ORDER_DEMAND:
        if event_type == AuditEventType.CREATED:
            return AuditTrailKind.DEMAND_CREATED
        if event_type == AuditEventType.UPDATED:
            if HOT_LIST_CHANGE_KEY in (metadata or {}):
                return AuditTrailKind.PRIORITY_CHANGED
            return AuditTrailKind.DEMAND_UPDATED
    elif entity_type == AuditEntityType.ASSIGNED_ROUTE:
        if event_type == AuditEventType.ROUTE_ADJUSTED:
            return AuditTrailKind.ROUTE_ADJUSTED
    return AuditTrailKind.CHANGE_RECORDED


def classify_allocation_row(row: WorkOrderAllocation) -> AuditTrailKind:
    """A reversal (also of a correction), a beyond-demand correction, or
    a Management allocation."""
    if row.reverses_allocation_id is not None:
        return AuditTrailKind.ALLOCATION_REVERSED
    if row.exceeds_demand:
        return AuditTrailKind.ALLOCATED_BEYOND_DEMAND
    return AuditTrailKind.ALLOCATED


def _shown(field: str, value: Any) -> TrailValue:
    """A stored value as the trail returns it: an image digest becomes
    whether an image is set — a digest is never returned."""
    if field == _IMAGE_FIELD:
        return value is not None
    shown: TrailValue = value
    return shown


def _is_blank(value: Any) -> bool:
    return value is None or value == []


def field_changes(
    entity_type: str,
    event_type: str,
    before_data: Mapping[str, Any] | None,
    after_data: Mapping[str, Any] | None,
) -> list[TrailChange]:
    """The displayed field changes of one audit row, in ``TRAIL_FIELDS``
    order: a creation lists its set fields, a deletion its last values,
    an edit the fields whose stored values differ (an absent key is
    null). Identity and kind-only keys are never listed."""
    entity = _entity(entity_type)
    fields = TRAIL_FIELDS.get(entity, ()) if entity is not None else ()
    before, after = before_data or {}, after_data or {}
    changes: list[TrailChange] = []
    for field in fields:
        old, new = before.get(field), after.get(field)
        if event_type == AuditEventType.CREATED:
            if not _is_blank(new):
                changes.append(TrailChange(field, None, _shown(field, new)))
        elif event_type == AuditEventType.DELETED:
            if not _is_blank(old):
                changes.append(TrailChange(field, _shown(field, old), None))
        elif event_type == AuditEventType.UPDATED and old != new:
            changes.append(TrailChange(field, _shown(field, old), _shown(field, new)))
    return changes


def trail_priority(metadata: Mapping[str, Any] | None, demand_id: int | None) -> TrailPriority:
    """The Hot list cause of a rank row: the action, the trigger of a rank
    change made outside the Hot command, and the removal reason of the
    line it removed (None on a line that only shifted)."""
    block = _block(metadata, HOT_LIST_CHANGE_KEY) or {}
    cause = block.get("cause")
    trigger: str | None = None
    removal_reason: str | None = None
    if isinstance(cause, dict):
        trigger = _text(cause.get("trigger"))
        removed = cause.get("removed")
        if isinstance(removed, list):
            for item in removed:
                if isinstance(item, dict) and item.get("work_order_demand_id") == demand_id:
                    removal_reason = _text(item.get("reason"))
    return TrailPriority(_text(block.get("action")), trigger, removal_reason)


def completion_trigger(metadata: Mapping[str, Any] | None) -> str | None:
    """What caused a Work Order completion row (passed through)."""
    return _text((_block(metadata, COMPLETION_AUDIT_KEY) or {}).get("trigger"))


def trail_cursor(before_source: str | None, before_id: int | None) -> TrailCursor | None:
    """The page cursor of a request: both parts or neither."""
    if before_source is None and before_id is None:
        return None
    if before_source is None or before_id is None:
        raise InvalidInputError(_BOTH_OR_NEITHER)
    return TrailCursor(AuditTrailSource(before_source), before_id)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def _scope(session: Session, pn: str) -> TrailScope:
    demand_work_order: dict[int, int] = {}
    existing: set[int] = set()
    for demand_id, work_order_id in session.execute(
        select(WorkOrderDemand.id, WorkOrderDemand.work_order_id).where(
            WorkOrderDemand.part_number == pn
        )
    ):
        demand_work_order[int(demand_id)] = int(work_order_id)
        existing.add(int(demand_id))
    # Lines deleted since: their CREATED row names the PN and the Work
    # Order (a saved line's PN never changes).
    for entity_id, work_order_id in session.execute(
        select(
            AuditEvent.entity_id,
            AuditEvent.after_data.op("->>", return_type=Text)("work_order_id"),
        ).where(
            AuditEvent.entity_type == AuditEntityType.WORK_ORDER_DEMAND,
            AuditEvent.event_type == AuditEventType.CREATED,
            AuditEvent.after_data.op("->>", return_type=Text)("part_number") == pn,
        )
    ):
        demand_work_order.setdefault(int(entity_id), int(work_order_id))
    route_flow = {
        int(route_id): int(flow_id)
        for route_id, flow_id in session.execute(
            select(QuantityFlow.assigned_route_id, QuantityFlow.id).where(
                QuantityFlow.part_number == pn, QuantityFlow.assigned_route_id.is_not(None)
            )
        )
    }
    return TrailScope(pn, demand_work_order, frozenset(existing), route_flow)


def _ids_text(ids: Iterable[int]) -> list[str]:
    # The writers' entity_id form.
    return [str(value) for value in sorted(set(ids))]


def _audit_predicate(scope: TrailScope) -> ColumnElement[bool]:
    terms: list[ColumnElement[bool]] = [
        (AuditEvent.entity_type == AuditEntityType.PART_NUMBER)
        & (AuditEvent.entity_id == scope.part_number)
    ]
    for entity_type, ids in (
        (AuditEntityType.WORK_ORDER_DEMAND, scope.demand_work_order),
        (AuditEntityType.WORK_ORDER, scope.demand_work_order.values()),
        (AuditEntityType.ASSIGNED_ROUTE, scope.route_flow),
    ):
        if ids:
            terms.append(
                (AuditEvent.entity_type == entity_type) & AuditEvent.entity_id.in_(_ids_text(ids))
            )
    return or_(*terms)


def _allocation_predicate(pn: str) -> ColumnElement[bool]:
    # Every Management-recorded row and every reversal ever recorded;
    # routine Stockroom confirmations stay in the allocation history.
    return (WorkOrderAllocation.part_number == pn) & or_(
        WorkOrderAllocation.source == AllocationSource.MANAGEMENT,
        WorkOrderAllocation.reverses_allocation_id.is_not(None),
    )


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _cursor_key(
    session: Session, scope: TrailScope, cursor: TrailCursor
) -> tuple[datetime.datetime, int, int]:
    """The ``(at, source_rank, id)`` keyset the cursor stands for, resolved
    within the PN's scope; an entry outside it is 404."""
    at: datetime.datetime | None
    if cursor.source is AuditTrailSource.AUDIT:
        at = session.scalar(
            select(AuditEvent.occurred_at).where(
                AuditEvent.id == cursor.id, _audit_predicate(scope)
            )
        )
    else:
        at = session.scalar(
            select(WorkOrderAllocation.allocated_at).where(
                WorkOrderAllocation.id == cursor.id, _allocation_predicate(scope.part_number)
            )
        )
    if at is None:
        raise NotFoundError(
            f"That audit trail entry is not part of {scope.part_number}'s audit trail"
            " — reload the audit trail."
        )
    return at, _SOURCE_RANK[cursor.source], cursor.id


def _count(session: Session, predicate: ColumnElement[bool], model: type[Any]) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(predicate)) or 0)


def audit_trail_of(
    session: Session,
    part_number: object,
    *,
    before: TrailCursor | None = None,
    limit: int = DEFAULT_TRAIL_LIMIT,
) -> TrailPage:
    """One page of the PN's audit trail, newest first (module doc).

    The PN is canonicalized (unknown → 404); the scope and the total are
    recomputed for every page; a cursor outside the PN's trail is 404.
    """
    pn = tracking.require_tracked(session, part_number)
    scope = _scope(session, pn)
    audit_predicate = _audit_predicate(scope)
    allocation_predicate = _allocation_predicate(pn)
    audit_rank = literal(_SOURCE_RANK[AuditTrailSource.AUDIT], Integer)
    allocation_rank = literal(_SOURCE_RANK[AuditTrailSource.ALLOCATION], Integer)
    audit_branch = select(
        AuditEvent.occurred_at.label("at"),
        audit_rank.label("source_rank"),
        AuditEvent.id.label("id"),
    ).where(audit_predicate)
    allocation_branch = select(
        WorkOrderAllocation.allocated_at, allocation_rank, WorkOrderAllocation.id
    ).where(allocation_predicate)
    if before is not None:
        key = _cursor_key(session, scope, before)
        audit_branch = audit_branch.where(
            tuple_(AuditEvent.occurred_at, audit_rank, AuditEvent.id) < key
        )
        allocation_branch = allocation_branch.where(
            tuple_(WorkOrderAllocation.allocated_at, allocation_rank, WorkOrderAllocation.id) < key
        )
    merged = union_all(audit_branch, allocation_branch).subquery()
    keys = list(
        session.execute(
            select(merged.c.source_rank, merged.c.id)
            .order_by(merged.c.at.desc(), merged.c.source_rank.desc(), merged.c.id.desc())
            .limit(limit + 1)
        )
    )
    has_more = len(keys) > limit
    keys = keys[:limit]
    total = _count(session, audit_predicate, AuditEvent) + _count(
        session, allocation_predicate, WorkOrderAllocation
    )
    audit_rank_value = _SOURCE_RANK[AuditTrailSource.AUDIT]
    audit_ids = [int(row_id) for rank, row_id in keys if rank == audit_rank_value]
    allocation_ids = [int(row_id) for rank, row_id in keys if rank != audit_rank_value]
    audit_rows = (
        {
            row.id: row
            for row in session.scalars(select(AuditEvent).where(AuditEvent.id.in_(audit_ids)))
        }
        if audit_ids
        else {}
    )
    allocation_rows = (
        {
            row.id: row
            for row in session.scalars(
                select(WorkOrderAllocation).where(WorkOrderAllocation.id.in_(allocation_ids))
            )
        }
        if allocation_ids
        else {}
    )
    ordered: list[AuditEvent | WorkOrderAllocation] = [
        audit_rows[int(row_id)] if rank == audit_rank_value else allocation_rows[int(row_id)]
        for rank, row_id in keys
    ]
    entries = _entries(session, scope, ordered)
    last = entries[-1] if entries else None
    return TrailPage(
        part_number=pn,
        entries=entries,
        total=total,
        has_more=has_more,
        next_before=TrailCursor(last.source, last.id) if has_more and last is not None else None,
    )


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------


class _References(NamedTuple):
    work_order_numbers: Mapping[int, str | None]
    actors: Mapping[int, user_access.UserRef]
    areas: Mapping[int, Area]
    operations: Mapping[int, Operation]
    machines: Mapping[int, Machine]


def _route_blocks(row: AuditEvent) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    """The kept-through sequence and the replaced / new tails (raw step
    snapshots after it, ascending) of a ROUTE_ADJUSTED row."""
    block = _block(row.metadata_, ROUTE_ADJUSTMENT_KEY) or {}
    kept = int(block.get("kept_through_sequence", 0))

    def tail(data: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        steps = (data or {}).get("steps")
        if not isinstance(steps, list):
            return []
        return sorted(
            (step for step in steps if isinstance(step, dict) and int(step["sequence"]) > kept),
            key=lambda step: int(step["sequence"]),
        )

    return kept, tail(row.before_data), tail(row.after_data)


def _by_id(session: Session, model: type[Any], ids: Collection[int]) -> dict[int, Any]:
    if not ids:
        return {}
    return {row.id: row for row in session.scalars(select(model).where(model.id.in_(ids)))}


def _references(
    session: Session, scope: TrailScope, rows: Sequence[AuditEvent | WorkOrderAllocation]
) -> _References:
    work_order_ids: set[int] = set()
    actor_ids: set[int] = set()
    area_ids: set[int] = set()
    operation_ids: set[int] = set()
    machine_ids: set[int] = set()
    for row in rows:
        if row.actor_user_id is not None:
            actor_ids.add(row.actor_user_id)
        if isinstance(row, WorkOrderAllocation):
            work_order_ids.add(scope.demand_work_order[row.work_order_demand_id])
        elif row.entity_type == AuditEntityType.WORK_ORDER:
            work_order_ids.add(int(row.entity_id))
        elif row.entity_type == AuditEntityType.WORK_ORDER_DEMAND:
            work_order_ids.add(scope.demand_work_order[int(row.entity_id)])
        elif row.entity_type == AuditEntityType.ASSIGNED_ROUTE:
            _, old, new = _route_blocks(row)
            for step in (*old, *new):
                area_ids.add(int(step["area_id"]))
                if step.get("operation_id") is not None:
                    operation_ids.add(int(step["operation_id"]))
                if step.get("preferred_machine_id") is not None:
                    machine_ids.add(int(step["preferred_machine_id"]))
    numbers: dict[int, str | None] = (
        {
            int(work_order_id): number
            for work_order_id, number in session.execute(
                select(WorkOrder.id, WorkOrder.work_order_number).where(
                    WorkOrder.id.in_(work_order_ids)
                )
            )
        }
        if work_order_ids
        else {}
    )
    return _References(
        work_order_numbers=numbers,
        actors=user_access.user_refs(session, actor_ids),
        areas=_by_id(session, Area, area_ids),
        operations=_by_id(session, Operation, operation_ids),
        machines=_by_id(session, Machine, machine_ids),
    )


def _route_step(row: AuditEvent, step: Mapping[str, Any], refs: _References) -> TrailRouteStep:
    area_id = int(step["area_id"])
    area = refs.areas.get(area_id)
    if area is None:
        # Areas are deactivated, never deleted: a defect, never invented.
        raise RuntimeError(f"Audit row {row.id} names Area {area_id}, which does not exist.")
    operation_id = step.get("operation_id")
    machine_id = step.get("preferred_machine_id")
    seconds = step.get("expected_duration_seconds")
    return TrailRouteStep(
        sequence=int(step["sequence"]),
        area=area,
        operation=refs.operations.get(int(operation_id)) if operation_id is not None else None,
        expected_duration=(
            datetime.timedelta(seconds=float(seconds)) if seconds is not None else None
        ),
        preferred_machine=refs.machines.get(int(machine_id)) if machine_id is not None else None,
        instructions=_text(step.get("instructions")),
    )


def _subject(
    scope: TrailScope,
    refs: _References,
    *,
    work_order_id: int | None = None,
    demand_id: int | None = None,
    flow_id: int | None = None,
) -> TrailSubject:
    if demand_id is not None:
        work_order_id = scope.demand_work_order[demand_id]
    return TrailSubject(
        work_order_id=work_order_id,
        work_order_number=(
            refs.work_order_numbers.get(work_order_id) if work_order_id is not None else None
        ),
        work_order_demand_id=demand_id,
        demand_exists=demand_id in scope.existing_demand_ids if demand_id is not None else None,
        quantity_flow_id=flow_id,
    )


def _actor(
    row: AuditEvent | WorkOrderAllocation, refs: _References
) -> tuple[user_access.UserRef | None, str | None]:
    if row.actor_user_id is not None:
        return refs.actors.get(row.actor_user_id), None
    return None, row.actor_reference


def _allocation_entry(row: WorkOrderAllocation, scope: TrailScope, refs: _References) -> TrailEntry:
    actor_user, legacy_actor = _actor(row, refs)
    return TrailEntry(
        source=AuditTrailSource.ALLOCATION,
        id=row.id,
        occurred_at=row.allocated_at,
        kind=classify_allocation_row(row),
        actor_user=actor_user,
        legacy_actor=legacy_actor,
        reason=row.allocation_reason,
        subject=_subject(scope, refs, demand_id=row.work_order_demand_id),
        changes=[],
        priority=None,
        completion_trigger=None,
        allocation=TrailAllocation(
            quantity=row.quantity,
            source=row.source,
            is_manual_override=row.is_manual_override,
            exceeds_demand=row.exceeds_demand,
            reverses_allocation_id=row.reverses_allocation_id,
            station_id=row.station_id,
        ),
        route=None,
    )


def _audit_entry(row: AuditEvent, scope: TrailScope, refs: _References) -> TrailEntry:
    kind = classify_audit_row(
        row.entity_type, row.event_type, row.before_data, row.after_data, row.metadata_
    )
    subject = _NO_SUBJECT
    reason: str | None = None
    route: TrailRoute | None = None
    demand_id: int | None = None
    if row.entity_type == AuditEntityType.WORK_ORDER:
        subject = _subject(scope, refs, work_order_id=int(row.entity_id))
    elif row.entity_type == AuditEntityType.WORK_ORDER_DEMAND:
        demand_id = int(row.entity_id)
        subject = _subject(scope, refs, demand_id=demand_id)
    elif row.entity_type == AuditEntityType.ASSIGNED_ROUTE:
        subject = _subject(scope, refs, flow_id=scope.route_flow[int(row.entity_id)])
        reason = _text((_block(row.metadata_, ROUTE_ADJUSTMENT_KEY) or {}).get("reason"))
    if kind is AuditTrailKind.ROUTE_ADJUSTED:
        kept, old, new = _route_blocks(row)
        route = TrailRoute(
            kept_through_sequence=kept,
            before_steps=[_route_step(row, step, refs) for step in old],
            after_steps=[_route_step(row, step, refs) for step in new],
        )
    actor_user, legacy_actor = _actor(row, refs)
    return TrailEntry(
        source=AuditTrailSource.AUDIT,
        id=row.id,
        occurred_at=row.occurred_at,
        kind=kind,
        actor_user=actor_user,
        legacy_actor=legacy_actor,
        reason=reason,
        subject=subject,
        changes=field_changes(row.entity_type, row.event_type, row.before_data, row.after_data),
        priority=(
            trail_priority(row.metadata_, demand_id)
            if kind is AuditTrailKind.PRIORITY_CHANGED
            else None
        ),
        completion_trigger=(
            completion_trigger(row.metadata_)
            if kind is AuditTrailKind.WORK_ORDER_COMPLETED
            else None
        ),
        allocation=None,
        route=route,
    )


def _entries(
    session: Session, scope: TrailScope, rows: Sequence[AuditEvent | WorkOrderAllocation]
) -> list[TrailEntry]:
    refs = _references(session, scope, rows)
    return [
        _allocation_entry(row, scope, refs)
        if isinstance(row, WorkOrderAllocation)
        else _audit_entry(row, scope, refs)
        for row in rows
    ]
