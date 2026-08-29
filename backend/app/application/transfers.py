"""Scan Station transfer to an Area queue (Phase 5 — IMPLEMENTATION_ROADMAP).

The one Application command that MOVES physical production quantity
(PROJECT_PROFILE §15 Core Scan Workflow; §12 Processing Ownership; §17
Route Deviation): a whole QuantityFlow leaves its current Area and
enters the Area an active Scan Station is bound to. It appends the
immutable `TRANSFERRED` PartMovement and updates the current-position
projection — one submission, ONE database transaction, idempotent per
`device_event_id`. This module also owns the source/route/Operation
assessment the Scan Station read models reuse, so the candidates a
station presents and the transfer it later records are judged by the
same rules.

Rules owned here:

- The source AND the destination are always EXPLICIT: the command
  takes exactly one QuantityFlow chosen by the operator (with the Area
  the operator saw it in as a precondition) and the destination Area
  the operator resolved and confirmed at the station (an optimistic
  precondition checked under the Scan Station row lock — a station
  deactivated or rebound to another Area since the confirmation is
  refused with nothing written, so the UI can never confirm Area A
  while the backend records Area B). It never picks, ranks, or
  combines flows.
  With several valid sources the read model returns them all and the
  UI requires a selection before any write (PROJECT_PROFILE §10
  Barcode Resolution, §15).
- Candidate resolution respects current position, route, Operation and
  station context — never "every active flow outside the target Area"
  (ROADMAP Phase 5): a flow qualifies only while it is ACTIVE, holds
  its whole quantity in an Area other than the station's, and the
  station's Area accepts production (active, non-terminal). A
  FLOATING flow has no route expectation; a PLANNED flow's next
  expected snapshot step — its Area AND, when the step defines one,
  its Operation — is compared against the station's Area and the
  resolved Operation. Only a full match records the step on the
  Movement (`assigned_route_step_id`); a different Area OR a different
  active Operation is a ROUTE DEVIATION that is refused until the
  operator explicitly confirms it with a reason, and a confirmed
  deviation is recorded in the Movement metadata — kind, expected step
  /Area/Operation, actual Area/Operation, reason, and the step is
  never recorded as matched (PROJECT_PROFILE §17 steps 1–5, 7). No
  Movement type beyond `TRANSFERRED` exists in this phase, so the
  deviation is recorded ON the transfer, and the previous route stays
  untouched: route adjustment and the separate
  `ROUTE_DEVIATION_CONFIRMED`/`ROUTE_ADJUSTED` events arrive with route
  editing (Phase 9+).
- The Operation is resolved from the station Area's configuration
  (SLICE1_DATA_MODEL §12 applied to the destination): a step-defined
  Operation of the matched route step, else the single active
  Operation, else an explicit choice — several active Operations
  without a choice are an ambiguity that blocks the write.
- Partial quantity (Phase 8): a confirmed quantity smaller than the
  flow's SPLITS the flow first, atomically inside the same command —
  the source closes, a selected child of exactly the confirmed
  quantity (with its own route snapshot copy when PLANNED) is the one
  completed and transferred, the remainder child stays at the source
  in exactly the source's state (`app.application.lineage`). The
  command's Movements are then the three `SPLIT` rows (sequence 1–3)
  followed by the action rows (4, or 4–5 with the implicit completion)
  under one `device_event_id`, replayed as a whole. The whole quantity
  never splits; a larger quantity exceeds the source (PROJECT_PROFILE
  §6 rule 7) and is refused.
- Order of operations mirrors the release command: idempotency check
  first — before ANY station/Area/Operation state is consulted, so a
  transport retry of a committed transfer replays the original result
  even after the flow moved again or the station was deactivated or
  rebound — then the source flow row lock (`FOR UPDATE`) that
  serializes concurrent transfers of one flow and a second idempotency
  check under it, then the Scan Station row lock and its
  precondition, then validation, then the writes. Any failure before
  COMMIT leaves zero writes.
- Row locks held until COMMIT (lock order: flow → station → source
  Machine → target Area → Operation): the target Area exactly like the
  release starting Area, so Area deactivation (which checks for active
  quantity under the same lock) and a transfer into that Area have one
  serial outcome; the selected Operation, re-validated under its lock,
  so Operation deactivation and a transfer recording it also have one
  serial outcome.
- Implicit Area completion (Phase 6 and 7; PROJECT_PROFILE §8.11,
  §15): a transfer of quantity that is still actively processing —
  ON_MACHINE in a Machine Area, or PROCESSING in an Area without
  Machines (direct processing, Phase 7) — completes processing at the
  source Area: ONE application command appends `AREA_COMPLETED`
  (command_sequence 1; the source Machine recorded for ON_MACHINE
  quantity, NO Machine for directly processing quantity) immediately
  followed by `TRANSFERRED` (command_sequence 2), both under the same
  `device_event_id`, either both written or neither. The source
  Machine row, when there is one, is locked so its derived state is
  re-judged under the lock. Quantity that is QUEUED or already
  READY_TO_TRANSFER transfers with `TRANSFERRED` alone. A replay of the
  whole command returns the original result including the completion
  Movement. The source Area's mode is judged from its active Machines
  under the flow lock.
- Destination mode (Phase 7): a transfer into an Area without Machines
  hands the quantity to direct processing — no queue, Machine NULL,
  the Operation recorded on the `TRANSFERRED` (an Area with several
  active Operations needs the explicit choice; nothing is picked).
  Nothing here differs from a Machine Area: the destination mode is a
  derivation of the read models, not a branch of the write.
- The projection update (`quantity_flows.current_area_id`, and
  `current_machine_id` cleared) is written in the same transaction as
  the Movements and stays rebuildable from Movement history alone
  (SLICE1 §15) — verified by the projection replay.
- Explicitly NOT here (later phases): the explicit merge
  (`app.application.merges`), Repair/Scrap/Undo (Phase 9), Worker
  sessions, Stockroom `STOCKED` (Phase 10) — a transfer into a terminal
  Area is therefore refused.
"""

import datetime
import hashlib
import json
from typing import Any, Final, Literal, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.common import device_event_id_text, optional_text, required_flag
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
    RouteDeviationConfirmationRequiredError,
)
from app.application.lineage import split_prefix, stage_split
from app.application.machine_processing import (
    FINGERPRINT_KEY,
    command_metadata,
    committed_command,
    no_longer_active,
)
from app.application.machines import (
    area_has_machines,
    assigned_quantity,
    lock_machine,
    note_assignment_change,
)
from app.application.part_numbers import canonical_part_number
from app.application.projections import (
    effective_latest_movement,
    is_actively_processing,
    processing_state_of,
)
from app.domain.enums import MovementType, ProcessingState, QuantityFlowStatus, RouteMode
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    AssignedRouteStep,
    Machine,
    Operation,
    PartMovement,
    QuantityFlow,
    ScanStation,
)

# Keys of the immutable TRANSFERRED metadata. The fingerprint is the
# idempotency comparison value (same mechanism as the RECEIVED release,
# SLICE1 §14; shared with the in-Area commands); the route-deviation
# block records a confirmed deviation (PROJECT_PROFILE §17 step 4).
_FINGERPRINT_KEY: Final = FINGERPRINT_KEY
_ROUTE_DEVIATION_KEY: Final = "route_deviation"

_DEVICE_EVENT_ID_CONSTRAINT: Final = DEVICE_EVENT_ID_CONSTRAINT

RouteStatus = Literal["FLOATING", "ON_ROUTE", "DEVIATION"]
DeviationKind = Literal["AREA", "OPERATION"]


class RouteAssessment(NamedTuple):
    """How the station's Area relates to one flow's route (read + write)."""

    status: RouteStatus
    # The snapshot step the transfer fulfils (ON_ROUTE only) — recorded
    # as the Movement's `assigned_route_step_id` ONLY when the resolved
    # Operation also matches the step (see `operation_deviates`).
    matched_step: AssignedRouteStep | None
    # The step the route expected next (PLANNED only; None when the
    # flow's last known step is the final one).
    expected_next_step: AssignedRouteStep | None
    # The flow's last route position known from Movement history
    # (PLANNED only).
    last_known_step: AssignedRouteStep | None


class AreaTransfer(NamedTuple):
    """One committed transfer result, read from the immutable Movement.

    Every field comes from the ``TRANSFERRED`` row itself, never from
    the mutable QuantityFlow projection, so a fresh result and every
    later idempotent replay carry the identical original values.
    ``created`` is False for an idempotent replay.
    """

    movement_id: int
    quantity_flow_id: int
    part_number: str
    quantity: int
    from_area_id: int
    to_area_id: int
    operation_id: int
    station_id: str
    assigned_route_step_id: int | None
    route_deviation: dict[str, Any] | None
    # The implicit AREA_COMPLETED of the same command when the quantity
    # was still actively processing at the source: its Movement id, and
    # the Machine it left (ON_MACHINE, Phase 6) or None for directly
    # processing quantity (Phase 7). Both None for a transfer of queued
    # or finished quantity.
    completed_movement_id: int | None
    completed_machine_id: int | None
    # Set when the command split the source first (Phase 8): the
    # consumed source flow, the remainder child that stayed at the
    # source in its state, and its quantity. All None for a whole-flow
    # transfer.
    source_quantity_flow_id: int | None
    remainder_quantity_flow_id: int | None
    remainder_quantity: int | None
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


# ---------------------------------------------------------------------------
# Station context (shared with the Scan Station read models)
# ---------------------------------------------------------------------------


def require_production_station(session: Session, station_id: str) -> tuple[ScanStation, Area]:
    """The station and its bound Area, both fit for production use.

    An unknown station is not found; an inactive station or an
    inactive bound Area accepts no production use (PROJECT_PROFILE §10
    "Inactive entities must not accept production updates", §15). The
    Station Selector never substitutes another station.
    """
    station = session.get(ScanStation, station_id)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
        )
    area = session.get(Area, station.area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' bound to Scan Station '{station_id}' is inactive"
            " and accepts no production use."
        )
    return station, area


def require_transfer_target(area: Area) -> None:
    """A transfer enters an Area queue or direct processing — never the Stockroom."""
    if area.is_terminal:
        raise ConflictError(
            f"Area '{area.name}' is a terminal Area. Receiving finished quantity there"
            " is the Stockroom workflow, not a transfer."
        )


def active_area_operations(session: Session, area_id: int) -> list[Operation]:
    return list(
        session.scalars(
            select(Operation)
            .where(Operation.area_id == area_id, Operation.is_active.is_(True))
            .order_by(Operation.code, Operation.id)
        )
    )


# ---------------------------------------------------------------------------
# Route assessment (PROJECT_PROFILE §17)
# ---------------------------------------------------------------------------


def assess_route(session: Session, flow: QuantityFlow, target_area_id: int) -> RouteAssessment:
    """Compare the station's Area with the flow's route expectation.

    A FLOATING flow has no expectation to deviate from. For a PLANNED
    flow the last known route position is the newest Movement that
    references a snapshot step (the `RECEIVED` always does; a confirmed
    deviation references none, so the position stays where the route
    was last followed); the expected next step is the following
    snapshot step. Only an exact match of that step's Area is ON_ROUTE
    — a later step (skipping ahead), an earlier one (going back) and an
    Area outside the route are all deviations that need explicit
    confirmation. A route may visit the same Area more than once; the
    sequence, not the Area alone, decides.
    """
    if flow.route_mode != RouteMode.PLANNED or flow.assigned_route_id is None:
        return RouteAssessment("FLOATING", None, None, None)
    steps = list(
        session.scalars(
            select(AssignedRouteStep)
            .where(AssignedRouteStep.assigned_route_id == flow.assigned_route_id)
            .order_by(AssignedRouteStep.sequence)
        )
    )
    last_known_step_id = session.scalar(
        select(PartMovement.assigned_route_step_id)
        .where(
            PartMovement.quantity_flow_id == flow.id,
            PartMovement.assigned_route_step_id.is_not(None),
        )
        .order_by(PartMovement.id.desc())
        .limit(1)
    )
    last_known: AssignedRouteStep | None = None
    expected_next: AssignedRouteStep | None = None
    for index, step in enumerate(steps):
        if step.id == last_known_step_id:
            last_known = step
            if index + 1 < len(steps):
                expected_next = steps[index + 1]
            break
    if expected_next is not None and expected_next.area_id == target_area_id:
        return RouteAssessment("ON_ROUTE", expected_next, expected_next, last_known)
    return RouteAssessment("DEVIATION", None, expected_next, last_known)


def operation_deviates(assessment: RouteAssessment, operation_id: int) -> bool:
    """True when the step the Area matched defines a DIFFERENT Operation.

    The Operation is part of the route expectation (PROJECT_PROFILE
    §8.9/§17): a step without an Operation accepts any Operation of its
    Area; a step with one is fulfilled only by that Operation. Anything
    else is a deviation — never a silently recorded matched step.
    """
    step = assessment.matched_step
    return step is not None and step.operation_id is not None and step.operation_id != operation_id


def route_deviation_context(
    assessment: RouteAssessment,
    *,
    kind: DeviationKind,
    target_area_id: int,
    operation_id: int | None,
) -> dict[str, Any]:
    """The deviation as the confirmation dialog presents and the Movement records it."""
    expected = assessment.expected_next_step
    last_known = assessment.last_known_step
    return {
        "kind": kind,
        "expected_next_area_id": expected.area_id if expected is not None else None,
        "expected_next_step_id": expected.id if expected is not None else None,
        "expected_next_sequence": expected.sequence if expected is not None else None,
        "expected_operation_id": expected.operation_id if expected is not None else None,
        "last_known_step_id": last_known.id if last_known is not None else None,
        "actual_area_id": target_area_id,
        "actual_operation_id": operation_id,
    }


# ---------------------------------------------------------------------------
# Operation resolution at the destination (SLICE1 §12 applied to transfer)
# ---------------------------------------------------------------------------


def suggested_operation_id(operations: list[Operation], assessment: RouteAssessment) -> int | None:
    """The Operation the destination resolves to WITHOUT a choice, if any.

    The matched route step's Operation when it is one of the Area's
    active Operations; else the single active Operation; else None —
    the operator must choose (or the Area is unconfigured).
    """
    active_ids = {operation.id for operation in operations}
    step = assessment.matched_step
    if step is not None and step.operation_id is not None and step.operation_id in active_ids:
        return step.operation_id
    if len(operations) == 1:
        return operations[0].id
    return None


def _lock_operation(session: Session, operation_id: int) -> Operation | None:
    """The Operation row locked until COMMIT and RE-READ under that lock.

    Operation deactivation is a plain UPDATE of this row, so it blocks
    behind a transfer that already holds the lock (and commits with the
    Operation still active), or commits first and the transfer re-reads
    the inactive row below and refuses — one serial outcome, never a
    Movement recorded against an Operation deactivated "at the same
    time".

    ``populate_existing`` is essential: the unlocked
    ``active_area_operations`` listing that precedes this call has
    already put the Operation into the Session identity map, and a bare
    ``session.get(..., with_for_update=True)`` would take the lock but
    hand back that stale object — a deactivation committed between the
    listing and the lock would go unseen. Forcing the row state to be
    reloaded from the locked SELECT closes that window.
    """
    return session.get(Operation, operation_id, with_for_update=True, populate_existing=True)


def _resolve_operation(
    session: Session,
    area: Area,
    assessment: RouteAssessment,
    requested_operation_id: int | None,
) -> Operation:
    """Resolve, lock and re-validate the destination Operation.

    A resolved Operation different from the matched step's Operation is
    NOT rejected here: it is a route deviation the caller confirms
    explicitly (`operation_deviates`).
    """
    operations = active_area_operations(session, area.id)
    if not operations:
        raise ConflictError(
            f"Area '{area.name}' has no active Operation configured."
            " Configure an Operation for the Area before transferring quantity into it."
        )
    suggested = suggested_operation_id(operations, assessment)
    if requested_operation_id is None:
        if suggested is None:
            raise InvalidInputError(
                f"Area '{area.name}' supports several Operations. Choose the"
                " Operation this quantity is transferred for."
            )
        requested_operation_id = suggested
    operation = _lock_operation(session, requested_operation_id)
    if operation is None:
        raise InvalidInputError(f"Operation {requested_operation_id} does not exist.")
    if operation.area_id != area.id:
        raise InvalidInputError(
            f"Operation '{operation.code}' does not belong to Area '{area.name}'."
        )
    # Re-validated under the row lock: the unlocked listing above may
    # predate a concurrent deactivation.
    if not operation.is_active:
        raise ConflictError(
            f"Operation '{operation.code}' is inactive and cannot accept transferred quantity."
        )
    return operation


# ---------------------------------------------------------------------------
# Input normalization — pure shape checks, no database access
# ---------------------------------------------------------------------------


def _validated_quantity(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError("Transfer quantity must be a positive whole number.")
    return value


def _request_fingerprint(
    *,
    station_id: str,
    quantity_flow_id: int,
    part_number: str,
    source_area_id: int,
    target_area_id: int,
    quantity: int,
    operation_id: int | None,
    route_deviation_reason: str | None,
) -> str:
    """Deterministic canonical hash of the normalized request (SLICE1 §14).

    Covers the whole CONFIRMED intent — station, flow, PN, source and
    destination Area, quantity, Operation choice, and the deviation
    reason when one was given. The explicit confirmation flag itself is
    intent-to-proceed, not request content (like the release's
    active-quantity confirmation), and is excluded.
    """
    normalized = {
        "station_id": station_id,
        "quantity_flow_id": quantity_flow_id,
        "part_number": part_number,
        "source_area_id": source_area_id,
        "target_area_id": target_area_id,
        "quantity": quantity,
        "operation_id": operation_id,
        "route_deviation_reason": route_deviation_reason,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Idempotency (SLICE1 §14) — the committed Movement is the record
# ---------------------------------------------------------------------------


def _committed_transfer(session: Session, device_event_id: str) -> list[PartMovement]:
    """The whole command recorded under the id — one or two Movements."""
    return committed_command(session, device_event_id)


def _remap_assessment(
    assessment: RouteAssessment, steps_by_sequence: dict[int, AssignedRouteStep]
) -> RouteAssessment:
    """The same assessment expressed in a split child's own snapshot copy.

    The child's snapshot is a structural copy of its parent's, so every
    step maps by sequence; the recorded step and deviation must point
    into the CHILD's snapshot (cross-table agreement with the flow's own
    AssignedRoute).
    """

    def _step(step: AssignedRouteStep | None) -> AssignedRouteStep | None:
        return None if step is None else steps_by_sequence[step.sequence]

    return RouteAssessment(
        assessment.status,
        _step(assessment.matched_step),
        _step(assessment.expected_next_step),
        _step(assessment.last_known_step),
    )


def _result_from_command(command: list[PartMovement], *, created: bool) -> AreaTransfer:
    split, action = split_prefix(command)
    movement = action[-1]
    completed = action[0] if len(action) == 2 else None
    if movement.from_area_id is None or movement.station_id is None:
        # The database shape CHECK makes this unreachable for a
        # TRANSFERRED command; another kind of row reusing the id is a
        # client defect caught by the fingerprint check before this point.
        raise IdempotencyConflictError(
            "This device_event_id belongs to a different kind of production"
            " event. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    return AreaTransfer(
        movement_id=movement.id,
        quantity_flow_id=movement.quantity_flow_id,
        part_number=movement.part_number,
        quantity=movement.quantity,
        from_area_id=movement.from_area_id,
        to_area_id=movement.to_area_id,
        operation_id=movement.operation_id,
        station_id=movement.station_id,
        assigned_route_step_id=movement.assigned_route_step_id,
        route_deviation=(movement.metadata_ or {}).get(_ROUTE_DEVIATION_KEY),
        completed_movement_id=completed.id if completed is not None else None,
        completed_machine_id=completed.source_machine_id if completed is not None else None,
        source_quantity_flow_id=split.source_quantity_flow_id if split else None,
        remainder_quantity_flow_id=split.remainder_quantity_flow_id if split else None,
        remainder_quantity=split.remainder_quantity if split else None,
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
        created=created,
    )


def _replay_or_conflict(command: list[PartMovement], fingerprint: str) -> AreaTransfer:
    """A committed command replays only as the SAME transfer intent.

    The command is a transfer when — after its optional SPLIT prefix
    (Phase 8) — its last Movement is the TRANSFERRED row and, when two
    action rows exist, the first is its implicit AREA_COMPLETED; every
    row of the command carries the same fingerprint.
    """
    _, action = split_prefix(command)
    movement = action[-1]
    stored = (movement.metadata_ or {}).get(_FINGERPRINT_KEY)
    well_formed = (
        movement.movement_type == MovementType.TRANSFERRED
        and (
            len(action) == 1
            or (len(action) == 2 and action[0].movement_type == MovementType.AREA_COMPLETED)
        )
        and all((row.metadata_ or {}).get(_FINGERPRINT_KEY) == stored for row in command)
    )
    if stored != fingerprint or not well_formed:
        raise IdempotencyConflictError(
            "This device_event_id was already used for a different production"
            " request. Nothing was recorded — a new transfer intent needs a new"
            " device_event_id."
        )
    return _result_from_command(command, created=False)


# ---------------------------------------------------------------------------
# Confirmed destination precondition (station row lock)
# ---------------------------------------------------------------------------


def _require_confirmed_station(
    session: Session, station_id: str, target_area_id: int
) -> tuple[ScanStation, Area]:
    """The station locked until COMMIT, still bound to the confirmed Area.

    Same refusals as `require_production_station`, plus the optimistic
    precondition: the Area the operator confirmed must be the Area the
    station is bound to NOW. A rebound station is refused with nothing
    written — the operator re-loads the station and confirms again.
    """
    station = session.get(ScanStation, station_id, with_for_update=True)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was transferred."
        )
    if station.area_id != target_area_id:
        raise ConflictError(
            f"Scan Station '{station_id}' is no longer bound to the confirmed destination"
            " Area — its configuration changed since the transfer was prepared. Reload"
            " the station and confirm the transfer again. Nothing was transferred."
        )
    area = session.get(Area, station.area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' bound to Scan Station '{station_id}' is inactive"
            " and accepts no production use."
        )
    return station, area


def _deviation_message(
    session: Session,
    assessment: RouteAssessment,
    deviation: dict[str, Any],
    target: Area,
    operation: Operation,
) -> str:
    expected = assessment.expected_next_step
    if deviation["kind"] == "OPERATION":
        expected_operation = (
            session.get(Operation, expected.operation_id)
            if expected is not None and expected.operation_id is not None
            else None
        )
        expected_code = expected_operation.code if expected_operation is not None else "?"
        lead = (
            f"Operation '{operation.code}' is not the Operation this Quantity Flow's Planned"
            f" Route expects at Area '{target.name}' (planned: '{expected_code}')."
        )
    else:
        expected_area = session.get(Area, expected.area_id) if expected is not None else None
        lead = (
            f"Area '{target.name}' is not the next step of this Quantity Flow's Planned"
            f" Route (next expected: '{expected_area.name}')."
            if expected_area is not None
            else f"Area '{target.name}' is not on this Quantity Flow's Planned Route"
            " — the route has no further step."
        )
    return (
        lead + " Confirm the route deviation with a reason to record the actual transfer;"
        " nothing is recorded until confirmed."
    )


# ---------------------------------------------------------------------------
# The transfer command
# ---------------------------------------------------------------------------


def transfer_to_station_area(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    source_area_id: int,
    target_area_id: int,
    quantity: object,
    operation_id: int | None,
    confirm_route_deviation: object,
    route_deviation_reason: str | None,
    device_event_id: object,
) -> AreaTransfer:
    """Move a QuantityFlow — or a part of it — into the station's Area, ONE transaction.

    Validates everything before any write; a replayed submission (same
    ``device_event_id`` + same confirmed intent) returns the original
    committed result and creates nothing — whatever happened to the
    station, Area or Operation since; a mismatched reuse is an explicit
    idempotency conflict that creates nothing.
    """
    # -- Pure input shape (no database) -------------------------------
    pn = canonical_part_number(part_number)
    confirmed_quantity = _validated_quantity(quantity)
    deviation_confirmed = required_flag(confirm_route_deviation, "confirm_route_deviation")
    reason = optional_text(route_deviation_reason)
    event_id = device_event_id_text(device_event_id)
    fingerprint = _request_fingerprint(
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        source_area_id=source_area_id,
        target_area_id=target_area_id,
        quantity=confirmed_quantity,
        operation_id=operation_id,
        route_deviation_reason=reason,
    )

    # -- Idempotency fast path (SLICE1 §14) ------------------------------
    # Before any station/Area/Operation state is read: a committed
    # transfer replays whatever the configuration looks like now.
    committed = _committed_transfer(session, event_id)
    if committed:
        return _replay_or_conflict(committed, fingerprint)

    # -- Source flow under row lock ------------------------------------
    # Serializes concurrent transfers of the same flow: the loser
    # re-reads the row after the winner committed and fails the source
    # precondition below instead of moving quantity twice.
    flow = session.get(QuantityFlow, quantity_flow_id, with_for_update=True)
    if flow is None:
        raise InvalidInputError(f"Quantity Flow {quantity_flow_id} does not exist.")

    # -- Idempotency RE-CHECK after the blocking lock --------------------
    committed = _committed_transfer(session, event_id)
    if committed:
        return _replay_or_conflict(committed, fingerprint)

    # -- Station context under the station row lock ---------------------
    # The confirmed destination is an optimistic precondition: the
    # station must still be active and still bound to exactly the Area
    # the operator confirmed, checked on the locked row so a concurrent
    # deactivation or rebind and this transfer have one serial outcome.
    station, target = _require_confirmed_station(session, station_id, target_area_id)

    # -- Validation before write ---------------------------------------
    if flow.part_number != pn:
        raise InvalidInputError(
            f"Part Number '{pn}' does not match Quantity Flow {flow.id}"
            f" ('{flow.part_number}'). A transfer moves the scanned PN's own quantity."
        )
    if flow.status != QuantityFlowStatus.ACTIVE:
        raise ConflictError(no_longer_active(flow))
    if flow.current_area_id == target.id:
        raise ConflictError(
            f"Quantity Flow {flow.id} is already in Area '{target.name}'. Nothing to transfer."
        )
    if flow.current_area_id != source_area_id:
        raise ConflictError(
            f"Quantity Flow {flow.id} is no longer in the selected source Area — it moved"
            " since the source was chosen. Scan the Part Number again to see the current"
            " sources."
        )
    if confirmed_quantity > flow.quantity:
        raise InvalidInputError(
            f"Transfer quantity {confirmed_quantity} exceeds the {flow.quantity} pcs"
            f" available in the source. Nothing was transferred."
        )
    partial = confirmed_quantity < flow.quantity
    source = session.get(Area, source_area_id)
    if source is None:  # pragma: no cover - FK guarantees the row
        raise InvalidInputError(f"Area {source_area_id} does not exist.")

    # -- Source processing state (Phase 6 / 7) --------------------------
    # Derived from the flow's latest Movement and the source Area's mode
    # (its active Machines, judged now under the flow lock): actively
    # processing quantity — ON_MACHINE, or PROCESSING in an Area without
    # Machines — is completed implicitly by this transfer (AREA_COMPLETED
    # first). An ON_MACHINE source locks its Machine now — lock order
    # flow → station → Machine → target Area → Operation; a directly
    # processing source has no Machine to lock. Queued or finished
    # quantity transfers with TRANSFERRED alone.
    latest = effective_latest_movement(session, flow.id)
    source_state = processing_state_of(
        latest.movement_type, direct_processing=not area_has_machines(session, source.id)
    )
    completes_source = is_actively_processing(source_state)
    source_machine: Machine | None = None
    if source_state == ProcessingState.ON_MACHINE:
        if flow.current_machine_id is None:  # pragma: no cover - projection invariant
            raise ConflictError(
                f"Quantity Flow {flow.id} is on a Machine according to its history but"
                " its projection carries none. Nothing was transferred."
            )
        source_machine = lock_machine(session, flow.current_machine_id)
        if source_machine is None:  # pragma: no cover - FK guarantees the row
            raise ConflictError(f"Machine {flow.current_machine_id} does not exist.")

    # The target Area row is locked until COMMIT (same protocol as the
    # release starting Area): deactivation checks for active quantity
    # under this lock, so an inactive Area can never receive a flow.
    # The Area flags are judged ONLY on this locked re-read — the
    # unlocked station read above may predate a concurrent Area edit
    # (deactivation or a terminal flag set meanwhile).
    session.refresh(target, with_for_update=True)
    if not target.is_active:
        raise ConflictError(
            f"Area '{target.name}' is inactive and cannot accept transferred quantity."
        )
    require_transfer_target(target)

    assessment = assess_route(session, flow, target.id)
    operation = _resolve_operation(session, target, assessment, operation_id)

    # -- Route expectation: Area AND Operation (PROJECT_PROFILE §17) ------
    deviation: dict[str, Any] | None = None
    matched_step: AssignedRouteStep | None = assessment.matched_step
    if assessment.status == "DEVIATION":
        deviation = route_deviation_context(
            assessment, kind="AREA", target_area_id=target.id, operation_id=operation.id
        )
    elif operation_deviates(assessment, operation.id):
        deviation = route_deviation_context(
            assessment, kind="OPERATION", target_area_id=target.id, operation_id=operation.id
        )
        # The step is NOT fulfilled: never record it as matched.
        matched_step = None
    if deviation is not None:
        if not deviation_confirmed:
            raise RouteDeviationConfirmationRequiredError(
                _deviation_message(session, assessment, deviation, target, operation),
                route_deviation=deviation,
            )
        if reason is None:
            # §17 step 7: a confirmed deviation records who, when and
            # WHY — the reason is mandatory, never defaulted.
            raise InvalidInputError(
                "A route deviation needs a reason. Enter why the quantity leaves its"
                " Planned Route — nothing is recorded until then."
            )

    # -- Writes — all inside the one open transaction ------------------
    # One application command: the SPLIT prefix when only a part moves
    # (Phase 8), the implicit AREA_COMPLETED (when the quantity was
    # actively processing — with its source Machine, or none for direct
    # processing) then the TRANSFERRED, numbered by command_sequence
    # under the one device_event_id — all or nothing.
    command: list[PartMovement] = []
    action_rows = 2 if completes_source else 1
    size = (3 if partial else 0) + action_rows
    # Read before any Movement is staged (no autoflush surprises).
    assigned_before = assigned_quantity(session, source_machine.id) if source_machine else 0
    metadata = command_metadata("TRANSFER", fingerprint, size=size)
    sequence = 1
    if partial:
        # The source closes; the SELECTED child (its own snapshot copy
        # when PLANNED, positioned where the source was) is what
        # completes and moves; the remainder stays at the source in the
        # source's state. Route references are re-expressed in the
        # child's snapshot.
        staged = stage_split(
            session,
            source=flow,
            selected_quantity=confirmed_quantity,
            operation_id=latest.operation_id,
            station_id=station.station_id,
            event_id=event_id,
            metadata=metadata,
        )
        command.extend(staged.movements)
        sequence = staged.next_sequence
        flow = staged.selected
        if staged.selected_steps:
            assessment = _remap_assessment(assessment, staged.selected_steps)
            matched_step = (
                staged.selected_steps[matched_step.sequence] if matched_step is not None else None
            )
            if deviation is not None:
                deviation = route_deviation_context(
                    assessment,
                    kind=deviation["kind"],
                    target_area_id=target.id,
                    operation_id=operation.id,
                )
    if completes_source:
        command.append(
            PartMovement(
                quantity_flow_id=flow.id,
                part_number=pn,
                movement_type=MovementType.AREA_COMPLETED,
                quantity=flow.quantity,
                from_area_id=source.id,
                to_area_id=source.id,
                operation_id=latest.operation_id,
                assigned_route_step_id=None,
                station_id=station.station_id,
                source_machine_id=source_machine.id if source_machine is not None else None,
                destination_machine_id=None,
                occurred_at=func.now(),
                server_received_at=func.now(),
                device_event_id=event_id,
                command_sequence=sequence,
                metadata_=metadata,
            )
        )
        sequence += 1
    transfer_metadata: dict[str, Any] = dict(metadata)
    if deviation is not None:
        transfer_metadata[_ROUTE_DEVIATION_KEY] = {
            **deviation,
            "confirmed": True,
            "reason": reason,
        }
    command.append(
        PartMovement(
            quantity_flow_id=flow.id,
            part_number=pn,
            movement_type=MovementType.TRANSFERRED,
            quantity=flow.quantity,
            from_area_id=source.id,
            to_area_id=target.id,
            operation_id=operation.id,
            assigned_route_step_id=matched_step.id if matched_step is not None else None,
            station_id=station.station_id,
            occurred_at=func.now(),
            server_received_at=func.now(),
            device_event_id=event_id,
            command_sequence=sequence,
            metadata_=transfer_metadata,
        )
    )
    # Added in command order: the unit of work inserts rows of one
    # table in that order, so the BIGSERIAL ids follow it (the
    # projection replay defines "latest" by id).
    session.add_all(command)
    # Projection update in the same transaction (SLICE1 §15): the Area
    # moves, the Machine — if any — clears, and its derived state is
    # re-judged under the Machine lock.
    if source_machine is not None:
        note_assignment_change(
            source_machine,
            assigned_before=assigned_before,
            assigned_after=assigned_before - confirmed_quantity,
        )
    flow.current_area_id = target.id
    flow.current_machine_id = None
    flow.updated_at = func.now()

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == _DEVICE_EVENT_ID_CONSTRAINT:
            # A concurrent submission with the same device_event_id won
            # the race at COMMIT: nothing of this attempt persisted.
            winner = _committed_transfer(session, event_id)
            if winner:
                return _replay_or_conflict(winner, fingerprint)
        raise
    return _result_from_command(command, created=True)
