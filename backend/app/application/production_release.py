"""Explicit production release (Phase 4 — the Slice 1 release command).

The one Application command that introduces physical production
quantity (PROJECT_PROFILE §13; SLICE1_DATA_MODEL §8–§14): it creates a
QuantityFlow with its route mode, snapshots an AssignedRoute **only**
for a `PLANNED` release, appends the immutable `RECEIVED` PartMovement,
and establishes the current-position projection — one submission, ONE
database transaction, idempotent per `device_event_id`.

Rules owned here:

- Order of operations follows SLICE1_DATA_MODEL §13 exactly:
  idempotency check first (a transport retry of a committed release
  returns the original result even if entities changed state since),
  then validation, then the active-quantity confirmation check, then
  the inserts. Any failure before COMMIT leaves zero writes.
- Releases of one canonical PN are SERIALIZED by a transaction-scoped
  PostgreSQL advisory lock keyed on the PN string itself: two
  concurrent unconfirmed releases of the same PN (any demand, any
  Work Order) can never both pass the active-quantity check. The key
  deliberately never references the optional, hard-deletable
  PartNumber master and needs no new table. After the blocking lock
  is acquired, the ``device_event_id`` + fingerprint check runs AGAIN:
  a concurrent identical retry that was in flight while the original
  committed replays the original result instead of tripping over the
  now-active quantity.
- The result of a release is built from the immutable ``RECEIVED``
  Movement (plus its immutable snapshot step for ``PLANNED``), never
  from the mutable QuantityFlow projection: an idempotent replay
  returns the ORIGINAL release result — release-time starting Area
  included — even after the flow has since moved on.
- The deterministic request fingerprint (SLICE1 §14) covers the
  canonical PN, quantity, Route Mode, RouteTemplate when `PLANNED`,
  starting Area, Operation, and the initiating WorkOrderDemand
  context. It is persisted in the `RECEIVED` metadata: the Movement
  row, found via `UNIQUE (device_event_id, command_sequence)` (a release
  is a one-Movement command), IS the idempotency record
  — no separate idempotency table or framework exists.
- The WorkOrderDemand context is informational release context for
  audit display (SLICE1 §3/§11): no `work_order_demand_id` FK exists
  on Movement and WorkOrderDemand never owns Movement. The immutable
  metadata context is also the canonical evidence that a demand has
  released quantity — the basis of the removal rule of PROJECT_PROFILE
  §13 (`demand_has_released_quantity`).
- Releasing a PN with ACTIVE flows requires the explicit confirmation
  flag set by the UI after showing the existing distribution; a
  confirmed release creates a separate flow and NEVER merges.
- A demand may be released in SEVERAL parts (20 of 50, then 12, then
  18). The released quantity of a demand is DERIVED from the immutable
  ``RECEIVED`` metadata context — no stored counter, no migration, no
  second source of truth — and the remaining quantity
  (``requested_quantity`` − released) is the hard server-side cap: a
  release beyond it is refused and creates nothing, so quantity can
  never be over-released against business demand.
- A release enters production at a STARTING Area: an inactive Area and
  a terminal Area (the Stockroom end of the flow, PROJECT_PROFILE §18)
  are both refused here, never only in the UI.
- The release transaction appends **no** generic `audit_events` row:
  the `RECEIVED` PartMovement is itself the immutable production audit
  record (SLICE1 §16).
"""

import datetime
import hashlib
import json
import uuid
from collections.abc import Collection
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.common import flush, required_flag
from app.application.errors import (
    ActiveQuantityConfirmationRequiredError,
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.part_numbers import canonical_part_number
from app.domain.enums import MovementType, QuantityFlowStatus, RouteMode
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    AssignedRoute,
    AssignedRouteStep,
    Operation,
    PartMovement,
    QuantityFlow,
    RouteStep,
    RouteTemplate,
    WorkOrderDemand,
)

# Keys of the immutable RECEIVED metadata (SLICE1 §11/§14). The
# fingerprint is the idempotency comparison value; the context block
# carries the informational initiating WorkOrderDemand (and actor,
# until authentication exists — Phase 14).
_FINGERPRINT_KEY: Final = "request_fingerprint"
_CONTEXT_KEY: Final = "context"
_DEMAND_ID_KEY: Final = "work_order_demand_id"
_ACTOR_KEY: Final = "actor"

_DEVICE_EVENT_ID_CONSTRAINT: Final = DEVICE_EVENT_ID_CONSTRAINT

# Namespace of the per-PN advisory lock key, hashed together with the
# canonical PN so release serialization never collides with any other
# future advisory lock use.
_RELEASE_LOCK_NAMESPACE: Final = "partflow:production-release:part-number:"


class ProductionRelease(NamedTuple):
    """One committed release result (SLICE1 §8.6), immutable by source.

    Every field is read from the ``RECEIVED`` Movement (and, for
    ``PLANNED``, its immutable AssignedRoute snapshot step) — never
    from the mutable QuantityFlow projection — so a fresh release and
    every later idempotent replay carry the identical original values.
    ``created`` is False for an idempotent replay.
    """

    quantity_flow_id: int
    part_number: str
    quantity: int
    route_mode: RouteMode
    assigned_route_id: int | None
    starting_area_id: int
    operation_id: int
    movement_id: int
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


# ---------------------------------------------------------------------------
# Input normalization — pure shape checks, no database access
# ---------------------------------------------------------------------------


def _validated_release_quantity(value: object) -> int:
    # bool is an int subclass — an explicit true/false is never a
    # quantity.
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError("Release quantity must be a positive whole number.")
    return value


def _validated_route_mode(value: object) -> RouteMode:
    if isinstance(value, RouteMode):
        return value
    if isinstance(value, str):
        try:
            return RouteMode(value)
        except ValueError:
            pass
    raise InvalidInputError("Route Mode must be FLOATING or PLANNED.")


def _normalized_device_event_id(value: object) -> str:
    """Normalize the client-generated idempotency key (SLICE1 §14).

    The client generates one UUID per release submission and reuses it
    on every transport retry. Normalizing to the canonical hyphenated
    lowercase form keeps 'same id' deterministic whatever textual UUID
    variant a client sends.
    """
    if not isinstance(value, str):
        raise InvalidInputError("device_event_id must be text.")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise InvalidInputError("device_event_id must be a UUID.") from None


def _request_fingerprint(
    *,
    part_number: str,
    quantity: int,
    route_mode: RouteMode,
    route_template_id: int | None,
    starting_area_id: int,
    operation_id: int,
    work_order_demand_id: int,
) -> str:
    """Deterministic canonical hash of the normalized request (SLICE1 §14)."""
    normalized = {
        "part_number": part_number,
        "quantity": quantity,
        "route_mode": str(route_mode),
        "route_template_id": route_template_id,
        "starting_area_id": starting_area_id,
        "operation_id": operation_id,
        "work_order_demand_id": work_order_demand_id,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Per-PN serialization (transaction-scoped advisory lock)
# ---------------------------------------------------------------------------


def _acquire_part_number_release_lock(session: Session, part_number: str) -> None:
    """Serialize releases of one canonical PN for this transaction.

    ``pg_advisory_xact_lock`` blocks until the concurrent release of
    the same PN commits or rolls back, and releases automatically with
    this transaction — no new table, and no dependency on the optional
    PartNumber master (the key is derived from the PN string itself).
    A hash collision between two different PNs merely serializes them
    too — harmless.
    """
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"{_RELEASE_LOCK_NAMESPACE}{part_number}", 0)
            )
        )
    )


# ---------------------------------------------------------------------------
# Idempotency (SLICE1 §14) — the committed Movement is the record
# ---------------------------------------------------------------------------


def _committed_release(session: Session, device_event_id: str) -> PartMovement | None:
    # A release is a one-Movement command; the first row of whatever
    # command reused the id is enough for the fingerprint comparison.
    return session.scalar(
        select(PartMovement)
        .where(PartMovement.device_event_id == device_event_id)
        .order_by(PartMovement.command_sequence)
        .limit(1)
    )


def _result_from_movement(
    session: Session, movement: PartMovement, *, created: bool
) -> ProductionRelease:
    """Build the release result from immutable rows only.

    The ``RECEIVED`` Movement carries the release-time PN, quantity,
    starting Area (``to_area_id``), Operation, Movement id,
    ``device_event_id`` and ``occurred_at``; the route mode and the
    snapshot reference derive from its ``assigned_route_step_id``
    against the immutable ``assigned_route_steps`` snapshot. Nothing
    here reads the mutable QuantityFlow projection, so a replay is
    byte-identical to the original response whatever happened to the
    flow since.
    """
    assigned_route_id: int | None = None
    if movement.assigned_route_step_id is not None:
        assigned_route_id = session.scalar(
            select(AssignedRouteStep.assigned_route_id).where(
                AssignedRouteStep.id == movement.assigned_route_step_id
            )
        )
    return ProductionRelease(
        quantity_flow_id=movement.quantity_flow_id,
        part_number=movement.part_number,
        quantity=movement.quantity,
        route_mode=RouteMode.PLANNED if assigned_route_id is not None else RouteMode.FLOATING,
        assigned_route_id=assigned_route_id,
        starting_area_id=movement.to_area_id,
        operation_id=movement.operation_id,
        movement_id=movement.id,
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
        created=created,
    )


def _replay_or_conflict(
    session: Session, movement: PartMovement, fingerprint: str
) -> ProductionRelease:
    """Resolve a duplicate ``device_event_id`` against the committed row."""
    stored = (movement.metadata_ or {}).get(_FINGERPRINT_KEY)
    # A release replays ONLY a release: an id first used by another
    # command kind (a transfer or a Machine-Area command since Phase 6)
    # is a conflicting reuse even if its fingerprint text happened to
    # match — the command kind is checked explicitly, never inferred
    # from the fingerprint's key set.
    if stored != fingerprint or movement.movement_type != MovementType.RECEIVED:
        raise IdempotencyConflictError(
            "This device_event_id was already used for a different release"
            " request. Nothing was created — a new release intent needs a"
            " new device_event_id."
        )
    return _result_from_movement(session, movement, created=False)


# ---------------------------------------------------------------------------
# Active-quantity distribution (SLICE1 §8.2)
# ---------------------------------------------------------------------------


def _existing_active_distribution(session: Session, part_number: str) -> list[dict[str, Any]]:
    """The PN's current ACTIVE distribution, as shown for confirmation."""
    active_rows = session.execute(
        select(QuantityFlow, Area.name)
        .join(Area, Area.id == QuantityFlow.current_area_id)
        .where(
            QuantityFlow.part_number == part_number,
            QuantityFlow.status == QuantityFlowStatus.ACTIVE,
        )
        .order_by(QuantityFlow.id)
    ).all()
    return [
        {
            "quantity_flow_id": flow.id,
            "quantity": flow.quantity,
            "route_mode": flow.route_mode,
            "current_area_id": flow.current_area_id,
            "current_area_name": area_name,
        }
        for flow, area_name in active_rows
    ]


# ---------------------------------------------------------------------------
# The release command
# ---------------------------------------------------------------------------


def release_to_production(
    session: Session,
    *,
    work_order_id: int,
    work_order_demand_id: int,
    part_number: object,
    quantity: object,
    route_mode: object,
    route_template_id: int | None,
    starting_area_id: int,
    operation_id: int,
    confirm_active_quantity: object,
    device_event_id: object,
    actor: str | None = None,
) -> ProductionRelease:
    """Release production quantity as ONE idempotent transaction.

    Validates everything before any write; a replayed submission (same
    ``device_event_id`` + same normalized request) returns the original
    committed result and creates nothing, a mismatched reuse is an
    explicit idempotency conflict that creates nothing.
    """
    # -- Pure input shape (no database) -------------------------------
    pn = canonical_part_number(part_number)
    release_quantity = _validated_release_quantity(quantity)
    mode = _validated_route_mode(route_mode)
    confirmed = required_flag(confirm_active_quantity, "confirm_active_quantity")
    if mode is RouteMode.PLANNED:
        if route_template_id is None:
            raise InvalidInputError("A PLANNED release requires a Route Template.")
    elif route_template_id is not None:
        raise InvalidInputError(
            "A FLOATING release takes no Route Template — it has no"
            " AssignedRoute; its route trace is derived from Movement"
            " history."
        )
    event_id = _normalized_device_event_id(device_event_id)
    fingerprint = _request_fingerprint(
        part_number=pn,
        quantity=release_quantity,
        route_mode=mode,
        route_template_id=route_template_id if mode is RouteMode.PLANNED else None,
        starting_area_id=starting_area_id,
        operation_id=operation_id,
        work_order_demand_id=work_order_demand_id,
    )

    # -- Idempotency fast path (SLICE1 §13/§14) -------------------------
    # A committed retry replays without ever waiting on the PN lock.
    committed = _committed_release(session, event_id)
    if committed is not None:
        return _replay_or_conflict(session, committed, fingerprint)

    # -- Serialize per canonical PN --------------------------------------
    # Blocks until any concurrent release of this PN finishes, so two
    # unconfirmed releases can never both pass the active-quantity
    # check below.
    _acquire_part_number_release_lock(session, pn)

    # -- Idempotency RE-CHECK after the blocking lock --------------------
    # A concurrent identical retry may have waited here while the
    # original committed: it must replay the original result, not trip
    # over the now-active quantity of its own release.
    committed = _committed_release(session, event_id)
    if committed is not None:
        return _replay_or_conflict(session, committed, fingerprint)

    # -- Validation before write ---------------------------------------
    # The demand row is locked for the duration of the transaction so a
    # concurrent demand removal serializes against this release: the
    # removal rule (PROJECT_PROFILE §13) can never race past a release
    # in flight for the same demand.
    demand = session.get(WorkOrderDemand, work_order_demand_id, with_for_update=True)
    if demand is None or demand.work_order_id != work_order_id:
        raise NotFoundError(
            f"Demand line {work_order_demand_id} does not exist on Work Order {work_order_id}."
        )
    if demand.part_number != pn:
        raise InvalidInputError(
            f"Part Number '{pn}' does not match demand line {demand.id}"
            f" ('{demand.part_number}'). A release is initiated from the"
            " demand's own PN."
        )

    # -- Remaining demand quantity (partial/multiple release) -----------
    # Derived from the immutable RECEIVED history, under the demand row
    # lock taken above: a concurrent release of the same demand is
    # serialized, so two partial releases can never jointly exceed the
    # requested quantity.
    already_released = demand_released_quantity(session, demand.id)
    remaining = demand.requested_quantity - already_released
    if remaining <= 0:
        raise ConflictError(
            f"Demand line {demand.id} is fully released"
            f" ({already_released} of {demand.requested_quantity} pcs)."
            " There is no remaining quantity to release."
        )
    if release_quantity > remaining:
        raise ConflictError(
            f"Only {remaining} pcs remain to release on demand line {demand.id}"
            f" ({already_released} of {demand.requested_quantity} pcs already"
            f" released). Release {remaining} pcs or less."
        )

    # The starting Area row is locked until COMMIT: Area deactivation
    # takes the same row lock before its active-quantity check, so a
    # concurrent release-vs-deactivation always has exactly one serial
    # outcome and an inactive Area can never end up holding a fresh
    # ACTIVE flow.
    area = session.get(Area, starting_area_id, with_for_update=True)
    if area is None:
        raise InvalidInputError(f"Area {starting_area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' is inactive and cannot accept a production release."
        )
    if area.is_terminal:
        # A terminal Area is where finished quantity ENDS (Stockroom,
        # PROJECT_PROFILE §18) — it is never a configured starting Area
        # (SLICE1_DATA_MODEL §8.1), so production never enters there.
        raise ConflictError(
            f"Area '{area.name}' is a terminal Area and never starts production."
            " Release into the configured starting Area instead."
        )
    operation = session.get(Operation, operation_id)
    if operation is None:
        raise InvalidInputError(f"Operation {operation_id} does not exist.")
    if operation.area_id != area.id:
        raise InvalidInputError(
            f"Operation '{operation.code}' does not belong to Area '{area.name}'."
        )
    if not operation.is_active:
        raise ConflictError(
            f"Operation '{operation.code}' is inactive and cannot accept a production release."
        )

    template_steps: list[RouteStep] = []
    template: RouteTemplate | None = None
    if mode is RouteMode.PLANNED:
        template = session.get(RouteTemplate, route_template_id)
        if template is None:
            raise InvalidInputError(f"Route Template {route_template_id} does not exist.")
        if template.archived_at is not None:
            raise ConflictError(
                f"Route Template '{template.name}' is archived and is never"
                " offered for new route assignments."
            )
        template_steps = list(
            session.scalars(
                select(RouteStep)
                .where(RouteStep.route_template_id == template.id)
                .order_by(RouteStep.sequence)
            )
        )
        if not template_steps:
            raise InvalidInputError(
                f"Route Template '{template.name}' has no steps to release against."
            )
        first_step = template_steps[0]
        # Mismatch is a validation failure, never a silent adjustment
        # (SLICE1 §10).
        if first_step.area_id != area.id:
            raise InvalidInputError(
                f"The first step of Route Template '{template.name}' does not"
                f" start in Area '{area.name}'. The snapshot's first step"
                " must match the confirmed starting Area."
            )
        if first_step.operation_id is not None and first_step.operation_id != operation.id:
            raise InvalidInputError(
                f"The first step of Route Template '{template.name}' defines"
                " a different Operation than the confirmed one."
            )

    # -- Active-quantity safety (SLICE1 §8.2) ---------------------------
    # Serialized by the PN advisory lock above: no concurrent release
    # of this PN can commit between this check and our own COMMIT.
    distribution = _existing_active_distribution(session, pn)
    if distribution and not confirmed:
        raise ActiveQuantityConfirmationRequiredError(
            f"Part Number '{pn}' already has active production quantity."
            " Review the existing distribution and confirm the intent to"
            " release a separate Quantity Flow — existing quantity is never"
            " merged.",
            existing_active_quantity=distribution,
        )

    # -- Writes (SLICE1 §13) — all inside the one open transaction ------
    snapshot: AssignedRoute | None = None
    first_snapshot_step: AssignedRouteStep | None = None
    if template is not None:
        snapshot = AssignedRoute(source_route_template_id=template.id)
        session.add(snapshot)
        flush(session, {})
        snapshot_steps = [
            AssignedRouteStep(
                assigned_route_id=snapshot.id,
                sequence=step.sequence,
                area_id=step.area_id,
                operation_id=step.operation_id,
                expected_duration=step.expected_duration,
                instructions=step.instructions,
            )
            for step in template_steps
        ]
        session.add_all(snapshot_steps)
        flush(session, {})
        first_snapshot_step = snapshot_steps[0]

    flow = QuantityFlow(
        part_number=pn,
        quantity=release_quantity,
        status=QuantityFlowStatus.ACTIVE,
        route_mode=mode,
        assigned_route_id=snapshot.id if snapshot is not None else None,
        # The projection is set by the INSERT itself: no projection-less
        # row ever exists (SLICE1 §9/§15).
        current_area_id=area.id,
    )
    session.add(flow)
    flush(session, {})

    context: dict[str, Any] = {_DEMAND_ID_KEY: demand.id}
    if actor is not None:
        context[_ACTOR_KEY] = actor
    movement = PartMovement(
        quantity_flow_id=flow.id,
        part_number=pn,
        movement_type=MovementType.RECEIVED,
        quantity=release_quantity,
        from_area_id=None,
        to_area_id=area.id,
        operation_id=operation.id,
        assigned_route_step_id=(
            first_snapshot_step.id if first_snapshot_step is not None else None
        ),
        # Synchronous online semantics: both server-assigned and equal
        # (SLICE1 §14).
        occurred_at=func.now(),
        server_received_at=func.now(),
        device_event_id=event_id,
        metadata_={_FINGERPRINT_KEY: fingerprint, _CONTEXT_KEY: context},
    )
    session.add(movement)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == _DEVICE_EVENT_ID_CONSTRAINT:
            # A concurrent submission with the same device_event_id won
            # the race at COMMIT: resolve it exactly like a pre-checked
            # duplicate — original result on a matching fingerprint,
            # explicit conflict otherwise. Nothing of this attempt
            # persisted (the rollback discarded flow, snapshot, and
            # Movement together).
            winner = _committed_release(session, event_id)
            if winner is not None:
                return _replay_or_conflict(session, winner, fingerprint)
        raise
    # Built from the committed Movement — the same immutable source a
    # later replay reads — so fresh and replay responses are identical.
    return _result_from_movement(session, movement, created=True)


# ---------------------------------------------------------------------------
# Released-quantity evidence for the demand removal rule
# ---------------------------------------------------------------------------


def released_quantities(session: Session, work_order_demand_ids: Collection[int]) -> dict[int, int]:
    """Released quantity per demand, derived from Movement history.

    ONE set-based aggregate over the immutable ``RECEIVED`` metadata
    context (SLICE1 §3/§11/§14), so a Work Order read — or the whole WO
    list — never issues a per-demand lookup. There is no
    Movement→Demand foreign key (deliberately forbidden: WorkOrderDemand
    never owns Movement) and no stored counter: the release command
    always records the initiating demand id in the ``RECEIVED``
    metadata, and `part_movements` rows can never be updated or
    deleted, so the answer never regresses and needs no reconciliation.

    Demands without release evidence are simply absent from the result
    (callers read them as 0). The values feed both the removal rule of
    PROJECT_PROFILE §13 and the read models: a demand's remaining
    quantity is ``requested_quantity`` minus this value, and a Work
    Order whose every current demand has no remaining quantity reads as
    RELEASED (GUI_DESIGN §11.1).
    """
    ids = [int(demand_id) for demand_id in work_order_demand_ids]
    if not ids:
        return {}
    demand_id_value = PartMovement.metadata_[_CONTEXT_KEY][_DEMAND_ID_KEY].as_integer()
    rows = session.execute(
        select(demand_id_value, func.sum(PartMovement.quantity))
        .where(
            # Only a RECEIVED Movement is release evidence: later
            # movement types may carry demand context for other
            # reasons without meaning "this demand released".
            PartMovement.movement_type == MovementType.RECEIVED,
            demand_id_value.in_(ids),
        )
        .group_by(demand_id_value)
    )
    return {int(demand_id): int(total) for demand_id, total in rows if demand_id is not None}


def demand_released_quantity(session: Session, work_order_demand_id: int) -> int:
    """Total quantity ever released for one demand (0 when never released)."""
    return released_quantities(session, [work_order_demand_id]).get(work_order_demand_id, 0)


def demand_has_released_quantity(session: Session, work_order_demand_id: int) -> bool:
    """True when any committed release recorded this demand as its context."""
    return demand_released_quantity(session, work_order_demand_id) > 0
