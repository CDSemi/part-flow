"""AssignedRoute adjustment (Phase 14 slice 6 — PROJECT_PROFILE §8.10, §17).

A Manager holding *Assign and edit Routes* replaces the **future** steps
of one ACTIVE, PLANNED Quantity Flow's own AssignedRoute (owner decision
OD-P11). Rules owned here (the pure part lives in
``app.domain.assigned_route``):

- **Past steps are immutable.** The kept-through sequence ``K`` is the
  highest sequence of a step any PartMovement references — reversed
  Movements and their REVERSED rows included. Every step up to ``K``
  stays as it is (PostgreSQL refuses any UPDATE of a step, and the FK
  refuses deleting a referenced one); the steps after ``K`` are replaced
  as a whole by the request's list (zero or more steps), numbered
  ``K+1 …`` contiguously. The Route Template, every other flow's
  snapshot, the Movement history and the flow row itself never change.
- **Stale-read guard.** The request names the future step ids it was
  edited against (``expected_future_step_ids``); a different current
  tail — the quantity moved on, or another User adjusted the route — is
  refused (409 ``route_changed``). ``K`` never decreases and past rows
  never change, so equal ids mean the same boundary and the same tail.
- **Reason and audit.** A reason is mandatory. Each adjustment appends
  exactly one ``audit_events`` row — ``ROUTE_ADJUSTED`` on
  ``AssignedRoute`` (``entity_id`` the route id) with the whole route
  before and after (step ids included: Movements and deviation metadata
  name steps by id) and the signed-in User. It is never a Movement: it
  moves no quantity. A tail identical to the current one is refused and
  writes nothing.
- **Idempotency.** The audit row is the command's idempotency record,
  found by its ``device_event_id`` through the UNIQUE partial index
  (``models.ROUTE_ADJUSTMENT_DEVICE_EVENT_ID``): a retry with the same
  body replays the original answer from the row alone; a different body
  is refused; a row recorded by another User is refused (R-1). The
  identity is never part of the fingerprint.

Lock order: PN advisory lock → the flow row FOR UPDATE → the referenced
Machines → Areas → Operations FOR KEY SHARE, ascending
(``route_templates.lock_step_references``) → step DELETE / INSERT →
audit INSERT. A transfer, stocking, machine command, quantity event or
merge of the same flow serializes on the flow row lock and is re-judged
against the route at its own commit; an Undo or a release of the same
PN serializes on the PN lock.

The Tracking reads of the recorded adjustments (``route_adjustment_notes``,
``route_adjustment_total``) live here too; the editor read is
``tracking.assigned_routes_of``.
"""

import datetime
import hashlib
import json
from collections.abc import Collection, Sequence
from typing import Any, Final, NamedTuple

from sqlalchemy import Exists, Text, cast, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application import audit, user_access
from app.application.common import device_event_id_text, flush, is_bindable_id
from app.application.errors import (
    RECORDED_BY_ANOTHER_USER_MESSAGE,
    AssignedRouteChangedError,
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
    RecordedByAnotherUserError,
)
from app.application.lineage import snapshot_steps
from app.application.part_numbers import acquire_part_number_lock
from app.application.route_templates import (
    RouteStepInput,
    lock_step_references,
    shape_route_steps,
)
from app.domain.assigned_route import (
    StepContent,
    future_part,
    is_unchanged,
    kept_through_sequence,
)
from app.domain.enums import AuditEntityType, AuditEventType, QuantityFlowStatus, RouteMode
from app.infrastructure.models import (
    ROUTE_ADJUSTMENT_DEVICE_EVENT_ID,
    AssignedRouteStep,
    AuditEvent,
    PartMovement,
    QuantityFlow,
)

# The metadata block of the adjustment's audit row (its idempotency record).
ROUTE_ADJUSTMENT_KEY: Final = "route_adjustment"

# The UNIQUE partial index a concurrent duplicate device_event_id trips.
_ADJUSTMENT_INDEX: Final = "uq_audit_events_route_adjustment_device_event_id"

_REASON_REQUIRED: Final = (
    "A route adjustment needs a reason. Enter why the route is adjusted — nothing is"
    " changed until then."
)
_REASON_NOT_TEXT: Final = "The reason must be plain text."
_EXPECTED_MALFORMED: Final = "The route could not be checked — reload the route and try again."
_UNCHANGED: Final = "These steps match the current route. Nothing was changed."
_REUSED_ID: Final = (
    "This device_event_id was already used for a different route adjustment. Nothing was"
    " changed — a new adjustment needs a new device_event_id."
)


class AdjustedStep(NamedTuple):
    """One step of the route after an adjustment (read back from the audit
    ``after_data`` on replay)."""

    id: int
    sequence: int
    area_id: int
    operation_id: int | None
    expected_duration: datetime.timedelta | None
    preferred_machine_id: int | None
    instructions: str | None


class RouteAdjustmentResult(NamedTuple):
    """One committed adjustment, rebuilt from its audit row alone."""

    device_event_id: str
    quantity_flow_id: int
    part_number: str
    assigned_route_id: int
    kept_through_sequence: int
    reason: str
    # The whole route after the adjustment, in sequence order.
    steps: list[AdjustedStep]
    created: bool


class RouteAdjustmentNote(NamedTuple):
    """One recorded adjustment of a snapshot, as a Tracking flow block shows it."""

    audit_event_id: int
    occurred_at: datetime.datetime
    reason: str
    kept_through_sequence: int
    # The signed-in User who adjusted the route (display values only).
    actor_user: user_access.UserRef | None


# ---------------------------------------------------------------------------
# Shared reads
# ---------------------------------------------------------------------------


def _is_referenced() -> Exists:
    """Whether any Movement — reversed ones and REVERSED rows included —
    names the step (served by ``ix_part_movements_assigned_route_step_id``)."""
    return (
        select(PartMovement.id)
        .where(PartMovement.assigned_route_step_id == AssignedRouteStep.id)
        .exists()
    )


def kept_through_sequences(session: Session, route_ids: Collection[int]) -> dict[int, int]:
    """Per route, the kept-through sequence: the highest sequence of a step
    any Movement references (one grouped query).

    A step a Movement names is never deleted (FK) and therefore never
    editable. A route with no referenced step is absent (a defect for a
    PLANNED flow).
    """
    if not route_ids:
        return {}
    referenced = _is_referenced()
    rows = session.execute(
        select(AssignedRouteStep.assigned_route_id, func.max(AssignedRouteStep.sequence))
        .where(AssignedRouteStep.assigned_route_id.in_(route_ids), referenced)
        .group_by(AssignedRouteStep.assigned_route_id)
    )
    return {int(route_id): int(sequence) for route_id, sequence in rows}


def _block(row: AuditEvent) -> dict[str, Any]:
    block = (row.metadata_ or {}).get(ROUTE_ADJUSTMENT_KEY)
    return block if isinstance(block, dict) else {}


def route_adjustment_notes(
    session: Session, route_ids: Collection[int]
) -> dict[int, list[RouteAdjustmentNote]]:
    """Every recorded adjustment of each route, oldest first — unbounded,
    like the deviation notes (adjustments are human-driven and few)."""
    notes: dict[int, list[RouteAdjustmentNote]] = {route_id: [] for route_id in route_ids}
    if not route_ids:
        return notes
    rows = list(
        session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == AuditEntityType.ASSIGNED_ROUTE,
                AuditEvent.entity_id.in_([str(route_id) for route_id in route_ids]),
            )
            .order_by(AuditEvent.id)
        )
    )
    actors = user_access.user_refs(
        session, {row.actor_user_id for row in rows if row.actor_user_id is not None}
    )
    for row in rows:
        block = _block(row)
        notes[int(row.entity_id)].append(
            RouteAdjustmentNote(
                audit_event_id=row.id,
                occurred_at=row.occurred_at,
                reason=str(block.get("reason", "")),
                kept_through_sequence=int(block.get("kept_through_sequence", 0)),
                actor_user=(
                    actors.get(row.actor_user_id) if row.actor_user_id is not None else None
                ),
            )
        )
    return notes


def route_adjustment_total(session: Session, part_number: str) -> int:
    """How many adjustments the PN's flows' snapshots carry — part of the
    Tracking detail's flows revision, so an adjustment re-reads the
    appended flow pages."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .join(
                QuantityFlow,
                AuditEvent.entity_id == cast(QuantityFlow.assigned_route_id, Text),
            )
            .where(
                AuditEvent.entity_type == AuditEntityType.ASSIGNED_ROUTE,
                QuantityFlow.part_number == part_number,
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Input shape (no database)
# ---------------------------------------------------------------------------


def _reason_text(value: object) -> str:
    # Explicit checks with the RT copy (common.required_text has its own).
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(_REASON_REQUIRED)
    if "\x00" in value:
        raise InvalidInputError(_REASON_NOT_TEXT)
    return value.strip()


def _expected_ids(value: object) -> list[int]:
    # bool is an int subclass — true/false is never a step id.
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise InvalidInputError(_EXPECTED_MALFORMED)
    ids = [int(item) for item in value]
    if len(set(ids)) != len(ids):
        raise InvalidInputError(_EXPECTED_MALFORMED)
    return ids


def _normalized_instructions(value: str | None) -> str | None:
    # Normalization only: NUL is refused with the step number at shaping.
    return (value.strip() or None) if value is not None else None


def _seconds(value: datetime.timedelta | None) -> float | None:
    return value.total_seconds() if value is not None else None


def _fingerprint(
    quantity_flow_id: int,
    expected: list[int],
    steps: Sequence[RouteStepInput],
    reason: str,
) -> str:
    normalized = {
        "quantity_flow_id": quantity_flow_id,
        "expected_future_step_ids": expected,
        "steps": [
            [
                step.area_id,
                step.operation_id,
                _seconds(step.expected_duration),
                step.preferred_machine_id,
                _normalized_instructions(step.instructions),
            ]
            for step in steps
        ],
        "reason": reason,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Audit snapshot and replay
# ---------------------------------------------------------------------------


def _route_snapshot(steps: Sequence[AssignedRouteStep]) -> dict[str, Any]:
    """The whole route, in sequence order, with step ids."""
    return {
        "steps": [
            {
                "id": step.id,
                "sequence": step.sequence,
                "area_id": step.area_id,
                "operation_id": step.operation_id,
                # Seconds as a JSON number (timedelta is not JSON).
                "expected_duration_seconds": _seconds(step.expected_duration),
                "preferred_machine_id": step.preferred_machine_id,
                "instructions": step.instructions,
            }
            for step in steps
        ]
    }


def _result(
    route_id: int, after: dict[str, Any], block: dict[str, Any], *, created: bool
) -> RouteAdjustmentResult:
    """The answer, built from the audit row's content alone — identical on
    replay whatever happened to the route or the flow since."""
    return RouteAdjustmentResult(
        device_event_id=str(block["device_event_id"]),
        quantity_flow_id=int(block["quantity_flow_id"]),
        part_number=str(block["part_number"]),
        assigned_route_id=route_id,
        kept_through_sequence=int(block["kept_through_sequence"]),
        reason=str(block["reason"]),
        steps=[
            AdjustedStep(
                id=int(step["id"]),
                sequence=int(step["sequence"]),
                area_id=int(step["area_id"]),
                operation_id=step["operation_id"],
                expected_duration=(
                    datetime.timedelta(seconds=step["expected_duration_seconds"])
                    if step["expected_duration_seconds"] is not None
                    else None
                ),
                preferred_machine_id=step["preferred_machine_id"],
                instructions=step["instructions"],
            )
            for step in after["steps"]
        ],
        created=created,
    )


def _committed_adjustment(session: Session, device_event_id: str) -> AuditEvent | None:
    # Literally the indexed expression and its predicate (models.py) —
    # the JSONB subscript form the planner matches.
    indexed_id = ROUTE_ADJUSTMENT_DEVICE_EVENT_ID
    return session.scalar(
        select(AuditEvent).where(
            AuditEvent.entity_type == AuditEntityType.ASSIGNED_ROUTE,
            indexed_id == device_event_id,
        )
    )


def _replay_or_conflict(
    row: AuditEvent, adjustment_fingerprint: str, actor_user_id: int
) -> RouteAdjustmentResult:
    """Resolve a duplicate ``device_event_id``: the fingerprint first, then
    the recording User must be the caller (a NULL actor differs)."""
    block = _block(row)
    if block.get("fingerprint") != adjustment_fingerprint:
        raise IdempotencyConflictError(_REUSED_ID)
    if row.actor_user_id != actor_user_id:
        raise RecordedByAnotherUserError(RECORDED_BY_ANOTHER_USER_MESSAGE)
    return _result(int(row.entity_id), row.after_data or {"steps": []}, block, created=False)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def _content(step: AssignedRouteStep) -> StepContent:
    return StepContent(
        area_id=step.area_id,
        operation_id=step.operation_id,
        expected_duration=step.expected_duration,
        preferred_machine_id=step.preferred_machine_id,
        instructions=step.instructions,
    )


def adjust_assigned_route(
    session: Session,
    *,
    actor_user_id: int,
    quantity_flow_id: int,
    device_event_id: object,
    expected_future_step_ids: object,
    steps: Sequence[RouteStepInput],
    reason: object,
) -> RouteAdjustmentResult:
    """Replace the future steps of one ACTIVE PLANNED flow's AssignedRoute.

    ONE transaction; idempotent per ``device_event_id``; every refusal —
    shape, missing or ineligible flow, stale tail, unchanged tail, an
    invalid step reference, a reused id — writes nothing.
    """
    # -- Pure input shape (no database) ---------------------------------
    event_id = device_event_id_text(device_event_id)
    reason_text = _reason_text(reason)
    expected = _expected_ids(expected_future_step_ids)
    # Identity is never part of the fingerprint — another User is refused
    # by the actor comparison of the replay instead.
    adjustment_fingerprint = _fingerprint(quantity_flow_id, expected, steps, reason_text)

    # -- Idempotency fast path: a committed retry never waits ------------
    committed = _committed_adjustment(session, event_id)
    if committed is not None:
        return _replay_or_conflict(committed, adjustment_fingerprint, actor_user_id)

    missing = f"Quantity Flow {quantity_flow_id} does not exist."
    if not is_bindable_id(quantity_flow_id):
        raise NotFoundError(missing)
    # Unlocked read for the PN only — a flow's PN never changes.
    flow = session.get(QuantityFlow, quantity_flow_id)
    if flow is None:
        raise NotFoundError(missing)

    # -- Locks: PN advisory → flow row -------------------------------------
    acquire_part_number_lock(session, flow.part_number)
    locked = session.get(
        QuantityFlow, quantity_flow_id, with_for_update=True, populate_existing=True
    )
    if locked is None:
        raise NotFoundError(missing)
    flow = locked

    # -- Idempotency RE-CHECK once the locks are granted ------------------
    committed = _committed_adjustment(session, event_id)
    if committed is not None:
        return _replay_or_conflict(committed, adjustment_fingerprint, actor_user_id)

    # -- Eligibility under the locks ----------------------------------------
    if flow.status != QuantityFlowStatus.ACTIVE:
        raise ConflictError(
            f"Quantity Flow {flow.id} is no longer active, so its route can no longer"
            " change. Nothing was changed."
        )
    route_id = flow.assigned_route_id
    if flow.route_mode != RouteMode.PLANNED or route_id is None:
        raise ConflictError(
            f"Quantity Flow {flow.id} follows a Floating Route — it has no assigned route"
            " to change. Nothing was changed."
        )
    current = snapshot_steps(session, route_id)
    referenced = session.scalars(
        select(AssignedRouteStep.sequence).where(
            AssignedRouteStep.assigned_route_id == route_id, _is_referenced()
        )
    )
    try:
        kept = kept_through_sequence(referenced)
    except ValueError:
        # Defect guard: a PLANNED flow always references a step (its
        # RECEIVED, SPLIT or MERGED Movement).
        raise ConflictError(
            f"Quantity Flow {flow.id} has no recorded route position, so its route cannot"
            " be changed safely. Nothing was changed."
        ) from None
    future = future_part(current, lambda step: step.sequence, kept)
    if [step.id for step in future] != expected:
        raise AssignedRouteChangedError(
            f"The route of Quantity Flow {flow.id} changed since you opened it — the quantity"
            " moved on or the route was changed by someone else. Nothing was changed. Review"
            " the current route and make the change again."
        )
    shaped = shape_route_steps(steps, allow_empty=True, first_number=kept + 1)
    if is_unchanged([_content(step) for step in future], shaped):
        raise ConflictError(_UNCHANGED)
    lock_step_references(session, shaped, first_number=kept + 1)

    # -- Writes: delete the old tail BEFORE inserting the new one ----------
    # (the unit of work orders INSERTs before DELETEs inside one flush,
    # which would trip the non-deferrable UNIQUE (route, sequence)).
    before = _route_snapshot(current)
    if future:
        session.execute(
            delete(AssignedRouteStep).where(AssignedRouteStep.id.in_([step.id for step in future]))
        )
        flush(session, {})
    added = [
        AssignedRouteStep(
            assigned_route_id=route_id,
            sequence=sequence,
            area_id=step.area_id,
            operation_id=step.operation_id,
            expected_duration=step.expected_duration,
            preferred_machine_id=step.preferred_machine_id,
            instructions=step.instructions,
        )
        for sequence, step in enumerate(shaped, start=kept + 1)
    ]
    session.add_all(added)
    flush(session, {})
    after = _route_snapshot([step for step in current if step.sequence <= kept] + added)
    block = {
        "device_event_id": event_id,
        "fingerprint": adjustment_fingerprint,
        "quantity_flow_id": flow.id,
        "part_number": flow.part_number,
        "reason": reason_text,
        "kept_through_sequence": kept,
    }
    audit.append_audit_event(
        session,
        event_type=AuditEventType.ROUTE_ADJUSTED,
        entity_type=AuditEntityType.ASSIGNED_ROUTE,
        entity_id=str(route_id),
        before_data=before,
        after_data=after,
        actor_user_id=actor_user_id,
        metadata={ROUTE_ADJUSTMENT_KEY: block},
    )
    result = _result(route_id, after, block, created=True)

    # -- Commit; a duplicate id committed meanwhile (another flow, so
    # another PN lock) trips the UNIQUE index: replay or refuse ------------
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) != _ADJUSTMENT_INDEX:
            raise
        committed = _committed_adjustment(session, event_id)
        if committed is None:
            raise
        return _replay_or_conflict(committed, adjustment_fingerprint, actor_user_id)
    return result
