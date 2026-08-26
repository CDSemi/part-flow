"""Machine-Area processing commands (Phase 6 — IMPLEMENTATION_ROADMAP).

The three one-shot Scan Station commands of an Area with Machines
(PROJECT_PROFILE §12 `QUEUE_AND_ASSIGN`, §7 Area Completion, §8.6,
§8.11, §15), each ONE database transaction, idempotent per
`device_event_id`, and each appending exactly one immutable Movement:

- **assign** — `ASSIGNED_TO_MACHINE`: a whole QUEUED QuantityFlow
  becomes ON_MACHINE; the Machine becomes the current executor
  (`quantity_flows.current_machine_id`). Machine-first (Machine scan)
  and PN-first (assign action on a queued row) entry points submit the
  same command. Never automatic — an Area with one Machine behaves
  exactly like one with several.
- **QUEUE** — `RELEASED_FROM_MACHINE`: unfinished or paused ON_MACHINE
  quantity returns to the Area queue (QUEUED). Never completed work.
- **DONE** — `AREA_COMPLETED`: ON_MACHINE quantity has completed
  processing at the current Area and waits as READY_TO_TRANSFER on the
  finished rack: the Machine clears from the position, the Area stays
  the physical location. DONE and QUEUE are two distinct actions and
  never merge. DONE is quantity-scoped — never a PN status, never
  `STOCKED`.

The holding state is DERIVED from the flow's latest Movement
(`app.application.projections.processing_state_of`): a NULL
`current_machine_id` is either QUEUED or READY_TO_TRANSFER, and the two
are never confused — assignment accepts QUEUED only, QUEUE and DONE
accept ON_MACHINE only.

Rules owned here:

- Source and Machine are always EXPLICIT: the command takes exactly one
  QuantityFlow chosen by the operator and one Machine (scanned or
  selected). Nothing is picked, ranked, combined or auto-assigned.
- Whole-QuantityFlow only (temporary limitation until SPLIT, Phase 8):
  a smaller quantity is refused with a clear message and no write; a
  larger one exceeds the source.
- Idempotency exactly like the transfer (SLICE1 §14): the request
  fingerprint (command kind, station, flow, PN, Machine, quantity) is
  stored on the Movement; same `device_event_id` + same fingerprint
  replays the original result whatever happened since, a mismatch is
  an explicit conflict, a race lost at COMMIT replays the winner.
- Serialization on the flow row lock (`FOR UPDATE`, taken before the
  idempotency re-check): two commands on one flow — assign versus
  assign, assign versus QUEUE, DONE versus a transfer — have exactly
  one winner; the loser re-reads the flow after the winner committed
  and fails the state precondition instead of applying twice.
- The Scan Station row is locked and must be active and bound to the
  Area the flow is currently in (the actions exist only for quantity in
  the station's Area — a station rebound meanwhile is refused).
- The Machine row is locked (`FOR UPDATE`, re-read under the lock) for
  every command: an assignment refuses a retired Machine, a Machine of
  another Area and a Machine under Maintenance; QUEUE and DONE take the
  Machine the flow is actually on (the submitted Machine is an
  optimistic precondition). The derived operational state
  (Maintenance > Running > Idle) is re-judged under that lock and
  `state_changed_at` moves only when it actually changes.
- Lock order: flow → station → Machine — the prefix of the transfer's
  flow → station → Machine → Area → Operation; Machine retirement locks
  the Machine alone and reads, so no order ever contradicts another.
- The Operation and the Scan Station are recorded on every in-Area
  Movement: the Operation is the one the quantity is in the Area for
  (carried forward from the flow's latest Movement — an in-Area event
  is no new Operation choice), the Station is the one the command was
  recorded at. No snapshot step: an in-Area event creates no route
  visit (PROJECT_PROFILE §8.11).
- Explicitly NOT here: direct Area processing (Phase 7), SPLIT/MERGED
  (Phase 8), Worker identity, Undo (Phase 9 — the command relationship
  it needs is `device_event_id` + `command_sequence`), Repair, Scrap,
  Stockroom.
"""

import datetime
import hashlib
import json
from typing import Any, Final, Literal, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.common import device_event_id_text
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.machines import (
    assigned_quantity,
    lock_machine,
    note_assignment_change,
)
from app.application.part_numbers import canonical_part_number
from app.application.projections import processing_state_of
from app.domain.enums import MovementType, ProcessingState, QuantityFlowStatus
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    Machine,
    PartMovement,
    QuantityFlow,
    ScanStation,
)

# Immutable metadata keys shared with the transfer command: the
# fingerprint is the idempotency comparison value (SLICE1 §14); the
# command block names the application command a Movement belongs to
# and how many Movements it appended (the relationship Undo reverses as
# one, PROJECT_PROFILE §16).
FINGERPRINT_KEY: Final = "request_fingerprint"
COMMAND_KEY: Final = "command"

CommandKind = Literal["ASSIGN", "QUEUE", "DONE", "TRANSFER"]

_MOVEMENT_TYPE_BY_KIND: Final[dict[CommandKind, MovementType]] = {
    "ASSIGN": MovementType.ASSIGNED_TO_MACHINE,
    "QUEUE": MovementType.RELEASED_FROM_MACHINE,
    "DONE": MovementType.AREA_COMPLETED,
}


class MachineProcessingResult(NamedTuple):
    """One committed in-Area command, read from its immutable Movement.

    Every field comes from the Movement row itself, never from the
    mutable projection, so a fresh result and every later idempotent
    replay carry the identical original values. ``created`` is False
    for a replay.
    """

    movement_id: int
    movement_type: str
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    machine_id: int
    operation_id: int
    station_id: str
    processing_state: ProcessingState
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


# ---------------------------------------------------------------------------
# Derived state (shared with the read models and the transfer)
# ---------------------------------------------------------------------------


def latest_movement(session: Session, flow_id: int) -> PartMovement:
    """The flow's newest Movement — the row its derived position follows."""
    movement = session.scalar(
        select(PartMovement)
        .where(PartMovement.quantity_flow_id == flow_id)
        .order_by(PartMovement.id.desc())
        .limit(1)
    )
    if movement is None:  # pragma: no cover - every flow starts with RECEIVED
        raise ConflictError(f"Quantity Flow {flow_id} has no Movement history.")
    return movement


def latest_movements(session: Session, flow_ids: list[int]) -> dict[int, PartMovement]:
    """The newest Movement per flow, for read models (unlocked)."""
    if not flow_ids:
        return {}
    latest = (
        select(PartMovement.quantity_flow_id, func.max(PartMovement.id).label("movement_id"))
        .where(PartMovement.quantity_flow_id.in_(flow_ids))
        .group_by(PartMovement.quantity_flow_id)
        .subquery()
    )
    rows = session.scalars(
        select(PartMovement).join(latest, latest.c.movement_id == PartMovement.id)
    )
    return {movement.quantity_flow_id: movement for movement in rows}


def command_metadata(kind: CommandKind, fingerprint: str, *, size: int = 1) -> dict[str, Any]:
    return {FINGERPRINT_KEY: fingerprint, COMMAND_KEY: {"kind": kind, "size": size}}


# ---------------------------------------------------------------------------
# Input normalization — pure shape checks, no database access
# ---------------------------------------------------------------------------


def _validated_quantity(value: object, action: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError(f"{action} quantity must be a positive whole number.")
    return value


def _request_fingerprint(
    *,
    kind: CommandKind,
    station_id: str,
    quantity_flow_id: int,
    part_number: str,
    machine_id: int,
    quantity: int,
) -> str:
    """Deterministic canonical hash of the normalized request (SLICE1 §14)."""
    normalized = {
        "command": kind,
        "station_id": station_id,
        "quantity_flow_id": quantity_flow_id,
        "part_number": part_number,
        "machine_id": machine_id,
        "quantity": quantity,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Idempotency (SLICE1 §14) — the committed Movement is the record
# ---------------------------------------------------------------------------


def committed_command(session: Session, device_event_id: str) -> list[PartMovement]:
    """Every Movement of the command recorded under the id, in sequence."""
    return list(
        session.scalars(
            select(PartMovement)
            .where(PartMovement.device_event_id == device_event_id)
            .order_by(PartMovement.command_sequence)
        )
    )


def _result_from_movement(movement: PartMovement, *, created: bool) -> MachineProcessingResult:
    machine_id = (
        movement.destination_machine_id
        if movement.movement_type == MovementType.ASSIGNED_TO_MACHINE
        else movement.source_machine_id
    )
    if movement.from_area_id is None or movement.station_id is None or machine_id is None:
        # The shape CHECK makes this unreachable for the three in-Area
        # types; reaching it means the id belongs to another command.
        raise IdempotencyConflictError(
            "This device_event_id belongs to a different kind of production"
            " event. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    return MachineProcessingResult(
        movement_id=movement.id,
        movement_type=movement.movement_type,
        quantity_flow_id=movement.quantity_flow_id,
        part_number=movement.part_number,
        quantity=movement.quantity,
        area_id=movement.to_area_id,
        machine_id=machine_id,
        operation_id=movement.operation_id,
        station_id=movement.station_id,
        processing_state=processing_state_of(movement.movement_type),
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
        created=created,
    )


def _replay_or_conflict(
    command: list[PartMovement], kind: CommandKind, fingerprint: str
) -> MachineProcessingResult:
    movement = command[0]
    stored = (movement.metadata_ or {}).get(FINGERPRINT_KEY)
    if (
        len(command) != 1
        or stored != fingerprint
        or movement.movement_type != _MOVEMENT_TYPE_BY_KIND[kind]
    ):
        raise IdempotencyConflictError(
            "This device_event_id was already used for a different production"
            " request. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    return _result_from_movement(movement, created=False)


# ---------------------------------------------------------------------------
# Shared command protocol
# ---------------------------------------------------------------------------


class _Context(NamedTuple):
    flow: QuantityFlow
    station: ScanStation
    area: Area
    latest: PartMovement
    state: ProcessingState


def _lock_flow_and_station(
    session: Session,
    *,
    station_id: str,
    quantity_flow_id: int,
    part_number: str,
    quantity: int,
    action: str,
) -> _Context:
    """The flow and the station under their row locks, validated.

    Lock order flow → station. The station must be active and bound to
    the Area the flow is in NOW: the in-Area actions exist only for
    quantity in the station's Area.
    """
    flow = session.get(QuantityFlow, quantity_flow_id, with_for_update=True)
    if flow is None:
        raise InvalidInputError(f"Quantity Flow {quantity_flow_id} does not exist.")
    station = session.get(ScanStation, station_id, with_for_update=True)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was recorded."
        )
    if flow.part_number != part_number:
        raise InvalidInputError(
            f"Part Number '{part_number}' does not match Quantity Flow {flow.id}"
            f" ('{flow.part_number}'). {action} applies to the scanned PN's own quantity."
        )
    if flow.status != QuantityFlowStatus.ACTIVE:
        raise ConflictError(f"Quantity Flow {flow.id} is no longer active.")
    if station.area_id != flow.current_area_id:
        raise ConflictError(
            f"Quantity Flow {flow.id} is not in the Area Scan Station '{station_id}' is"
            " bound to — it moved, or the station was rebound, since the action was"
            " prepared. Reload the station and scan again. Nothing was recorded."
        )
    area = session.get(Area, flow.current_area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {flow.current_area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' is inactive and accepts no production use. Nothing was recorded."
        )
    if quantity != flow.quantity:
        if quantity > flow.quantity:
            raise InvalidInputError(
                f"{action} quantity {quantity} exceeds the {flow.quantity} pcs at the"
                " source position. Nothing was recorded."
            )
        raise InvalidInputError(
            f"Partial {action.lower()} is not supported yet: this Quantity Flow holds"
            f" {flow.quantity} pcs and is handled as a whole. Enter {flow.quantity} pcs"
            " or cancel — nothing was recorded."
        )
    latest = latest_movement(session, flow.id)
    return _Context(flow, station, area, latest, processing_state_of(latest.movement_type))


def _machine_on_flow(session: Session, context: _Context, machine_id: int, action: str) -> Machine:
    """The Machine the flow is actually on, locked; the submitted one is a precondition."""
    if context.state != ProcessingState.ON_MACHINE or context.flow.current_machine_id is None:
        if context.state == ProcessingState.READY_TO_TRANSFER:
            raise ConflictError(
                f"Quantity Flow {context.flow.id} has already completed processing at"
                f" Area '{context.area.name}' (DONE) and waits for transfer — it is not"
                f" on a Machine. Nothing was recorded."
            )
        raise ConflictError(
            f"Quantity Flow {context.flow.id} is queued in Area '{context.area.name}',"
            f" not on a Machine. {action} applies to Machine-assigned quantity only."
            " Nothing was recorded."
        )
    if context.flow.current_machine_id != machine_id:
        raise ConflictError(
            f"Quantity Flow {context.flow.id} is not on the selected Machine — its"
            " assignment changed since the action was prepared. Reload the station and"
            " try again. Nothing was recorded."
        )
    machine = lock_machine(session, machine_id)
    if machine is None:  # pragma: no cover - FK guarantees the row
        raise InvalidInputError(f"Machine {machine_id} does not exist.")
    return machine


def _commit_or_replay(
    session: Session,
    movement: PartMovement,
    *,
    kind: CommandKind,
    event_id: str,
    fingerprint: str,
) -> MachineProcessingResult:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == DEVICE_EVENT_ID_CONSTRAINT:
            # A concurrent submission with the same device_event_id won
            # the race at COMMIT: nothing of this attempt persisted.
            winner = committed_command(session, event_id)
            if winner:
                return _replay_or_conflict(winner, kind, fingerprint)
        raise
    return _result_from_movement(movement, created=True)


def _in_area_movement(
    context: _Context,
    *,
    movement_type: MovementType,
    source_machine_id: int | None,
    destination_machine_id: int | None,
    event_id: str,
    metadata: dict[str, Any],
) -> PartMovement:
    return PartMovement(
        quantity_flow_id=context.flow.id,
        part_number=context.flow.part_number,
        movement_type=movement_type,
        quantity=context.flow.quantity,
        from_area_id=context.area.id,
        to_area_id=context.area.id,
        operation_id=context.latest.operation_id,
        assigned_route_step_id=None,
        station_id=context.station.station_id,
        source_machine_id=source_machine_id,
        destination_machine_id=destination_machine_id,
        occurred_at=func.now(),
        server_received_at=func.now(),
        device_event_id=event_id,
        command_sequence=1,
        metadata_=metadata,
    )


# ---------------------------------------------------------------------------
# The commands
# ---------------------------------------------------------------------------


def assign_to_machine(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    machine_id: int,
    quantity: object,
    device_event_id: object,
) -> MachineProcessingResult:
    """Assign one whole QUEUED QuantityFlow to a Machine, ONE transaction."""
    pn = canonical_part_number(part_number)
    confirmed_quantity = _validated_quantity(quantity, "Assignment")
    event_id = device_event_id_text(device_event_id)
    fingerprint = _request_fingerprint(
        kind="ASSIGN",
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        machine_id=machine_id,
        quantity=confirmed_quantity,
    )
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(committed, "ASSIGN", fingerprint)

    context = _lock_flow_and_station(
        session,
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        quantity=confirmed_quantity,
        action="Assignment",
    )
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(committed, "ASSIGN", fingerprint)

    if context.state == ProcessingState.ON_MACHINE:
        raise ConflictError(
            f"Quantity Flow {context.flow.id} is already on a Machine. Return it to the"
            " queue (QUEUE) before assigning it to another Machine. Nothing was recorded."
        )
    if context.state == ProcessingState.READY_TO_TRANSFER:
        raise ConflictError(
            f"Quantity Flow {context.flow.id} has completed processing at Area"
            f" '{context.area.name}' (DONE) and waits for transfer — finished quantity is"
            " not assigned to a Machine. Nothing was recorded."
        )

    # -- The Machine under its row lock, re-read under the lock --------
    machine = lock_machine(session, machine_id)
    if machine is None:
        raise InvalidInputError(f"Machine {machine_id} does not exist.")
    if machine.retired_on is not None:
        raise ConflictError(
            f"Machine '{machine.name}' is retired and accepts no assignment. Nothing was recorded."
        )
    if machine.area_id != context.area.id:
        raise ConflictError(
            f"Machine '{machine.name}' belongs to another Area. Quantity in Area"
            f" '{context.area.name}' is assigned only to that Area's Machines."
            " Nothing was recorded."
        )
    if machine.maintenance_since is not None:
        raise ConflictError(
            f"Machine '{machine.name}' is under maintenance and accepts no new"
            " assignment. Nothing was recorded."
        )

    # -- Writes — all inside the one open transaction ------------------
    # The assigned quantity is read BEFORE the Movement is staged: a
    # later autoflush would otherwise surface a lost idempotency race
    # as a flush error instead of at COMMIT.
    before = assigned_quantity(session, machine.id, exclude_flow_id=context.flow.id)
    movement = _in_area_movement(
        context,
        movement_type=MovementType.ASSIGNED_TO_MACHINE,
        source_machine_id=None,
        destination_machine_id=machine.id,
        event_id=event_id,
        metadata=command_metadata("ASSIGN", fingerprint),
    )
    session.add(movement)
    context.flow.current_machine_id = machine.id
    context.flow.updated_at = func.now()
    note_assignment_change(
        machine, assigned_before=before, assigned_after=before + context.flow.quantity
    )
    return _commit_or_replay(
        session, movement, kind="ASSIGN", event_id=event_id, fingerprint=fingerprint
    )


def _leave_machine(
    session: Session,
    *,
    kind: CommandKind,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    machine_id: int,
    quantity: object,
    device_event_id: object,
) -> MachineProcessingResult:
    action = "Return to queue" if kind == "QUEUE" else "Completion"
    pn = canonical_part_number(part_number)
    confirmed_quantity = _validated_quantity(quantity, action)
    event_id = device_event_id_text(device_event_id)
    fingerprint = _request_fingerprint(
        kind=kind,
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        machine_id=machine_id,
        quantity=confirmed_quantity,
    )
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(committed, kind, fingerprint)

    context = _lock_flow_and_station(
        session,
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        quantity=confirmed_quantity,
        action=action,
    )
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(committed, kind, fingerprint)

    machine = _machine_on_flow(session, context, machine_id, action)

    before = assigned_quantity(session, machine.id)
    movement = _in_area_movement(
        context,
        movement_type=_MOVEMENT_TYPE_BY_KIND[kind],
        source_machine_id=machine.id,
        destination_machine_id=None,
        event_id=event_id,
        metadata=command_metadata(kind, fingerprint),
    )
    session.add(movement)
    # The Machine clears from the position either way; the Area stays
    # the location. QUEUED versus READY_TO_TRANSFER is told apart by
    # the Movement just appended, never by the cleared column.
    context.flow.current_machine_id = None
    context.flow.updated_at = func.now()
    note_assignment_change(
        machine, assigned_before=before, assigned_after=before - context.flow.quantity
    )
    return _commit_or_replay(
        session, movement, kind=kind, event_id=event_id, fingerprint=fingerprint
    )


def release_to_queue(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    machine_id: int,
    quantity: object,
    device_event_id: object,
) -> MachineProcessingResult:
    """QUEUE: return one whole ON_MACHINE QuantityFlow to the Area queue."""
    return _leave_machine(
        session,
        kind="QUEUE",
        station_id=station_id,
        part_number=part_number,
        quantity_flow_id=quantity_flow_id,
        machine_id=machine_id,
        quantity=quantity,
        device_event_id=device_event_id,
    )


def complete_at_machine(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    machine_id: int,
    quantity: object,
    device_event_id: object,
) -> MachineProcessingResult:
    """DONE: complete processing of one whole ON_MACHINE QuantityFlow at its Area."""
    return _leave_machine(
        session,
        kind="DONE",
        station_id=station_id,
        part_number=part_number,
        quantity_flow_id=quantity_flow_id,
        machine_id=machine_id,
        quantity=quantity,
        device_event_id=device_event_id,
    )
