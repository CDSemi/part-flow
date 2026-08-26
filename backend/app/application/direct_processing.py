"""Direct Area processing — Areas without Machines (Phase 7 — IMPLEMENTATION_ROADMAP).

Exactly two Area modes exist and the mode FOLLOWS FROM the Area's
Machines (PROJECT_PROFILE §12; no per-Area assignment-mode
configuration, no single-Machine auto-assignment): an Area with one or
more active Machines uses `QUEUE_AND_ASSIGN`
(`app.application.machine_processing`); an Area with zero active
Machines has no queue — it directly owns and processes the quantity
it receives. Nothing in this module is configured; the mode is judged
from the Area's active Machines at the moment of every command and
every read (`app.application.machines.area_has_machines`).

What direct processing means for the immutable history (PROJECT_PROFILE
§7 Area Completion, §8.11, §12 Area Without Machines):

- **Arrival** — a `RECEIVED` (production release into such a starting
  Area) or a `TRANSFERRED` into such an Area records the Operation the
  quantity is there for (resolved from the Area's configuration, or
  chosen explicitly when the Area supports several — never picked
  silently) and NO Machine; the derived holding state is `PROCESSING`
  (`app.application.projections.processing_state_of`). No assignment,
  queue or Machine event exists for the quantity.
- **Manual DONE** — `complete_direct_processing` below: one whole
  PROCESSING QuantityFlow completes processing at its Area and waits as
  `READY_TO_TRANSFER` on the finished rack. Exactly one immutable
  `AREA_COMPLETED` Movement is appended, with `source_machine_id` NULL
  (the Movement shape widened by migration 0008), the Operation carried
  forward from the flow's latest Movement, the Scan Station recorded,
  and the Area kept as the physical location. It is the same DONE
  command kind as the Machine-Area DONE, submitted without a Machine —
  the request fingerprint (Machine None) keeps the two intents apart
  under one `device_event_id`.
- **Implicit completion on transfer** — owned by
  `app.application.transfers`: a transfer of PROCESSING quantity to
  another Area appends `AREA_COMPLETED` (sequence 1, no Machine) then
  `TRANSFERRED` (sequence 2) as ONE atomic command under ONE
  `device_event_id`, exactly like the `ON_MACHINE` case; quantity
  already `READY_TO_TRANSFER` transfers with `TRANSFERRED` alone.

Guarantees, unchanged from Phases 5–6: one database transaction per
command, idempotency per `device_event_id` (replay of the same intent
returns the original committed result whatever happened since; a
mismatched reuse — including a Machine DONE reusing a direct DONE id —
is an explicit conflict; a race lost at COMMIT replays the winner),
serialization on the flow row lock (a DONE and a transfer of one flow
have exactly one winner), the Scan Station row lock with the
station-bound-to-the-flow's-Area precondition, and whole-QuantityFlow
only — partial DONE is refused with zero writes until SPLIT (Phase 8).

The Area's mode is judged at the command under the flow lock: a DONE
without a Machine on quantity in an Area that HAS Machines is refused
(that quantity is queued, on a Machine or finished — never "directly
processing"), and a DONE with a Machine on directly processing quantity
is refused by the Machine-Area command. A first Machine added to an
Area later, or the last one retired, changes the Area's mode for the
quantity it holds from then on — the history stays exactly what was
recorded.

Explicitly NOT here: SPLIT/MERGED and partial completion (Phase 8),
Worker identity, Undo (Phase 9), Repair, Scrap, Stockroom.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.common import device_event_id_text
from app.application.errors import ConflictError, InvalidInputError
from app.application.machine_processing import (
    MachineProcessingResult,
    command_metadata,
    commit_or_replay,
    committed_command,
    in_area_movement,
    lock_flow_and_station,
    replay_or_conflict,
    request_fingerprint,
)
from app.application.part_numbers import canonical_part_number
from app.domain.enums import MovementType, ProcessingState

_ACTION = "Completion"


def _validated_quantity(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError(f"{_ACTION} quantity must be a positive whole number.")
    return value


def complete_direct_processing(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    quantity: object,
    device_event_id: object,
) -> MachineProcessingResult:
    """DONE without a Machine: complete one whole PROCESSING QuantityFlow, ONE transaction.

    Same protocol as the Machine-Area commands: input shape →
    fingerprint → idempotency fast path → flow lock → idempotency
    re-check → station lock and preconditions → state check → the one
    Movement + projection touch → COMMIT (or replay of a race winner).
    The Machine projection is already NULL for directly processing
    quantity and stays NULL; the Area stays the location.
    """
    pn = canonical_part_number(part_number)
    confirmed_quantity = _validated_quantity(quantity)
    event_id = device_event_id_text(device_event_id)
    fingerprint = request_fingerprint(
        kind="DONE",
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        machine_id=None,
        quantity=confirmed_quantity,
    )
    committed = committed_command(session, event_id)
    if committed:
        return replay_or_conflict(committed, "DONE", fingerprint)

    context = lock_flow_and_station(
        session,
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        quantity=confirmed_quantity,
        action=_ACTION,
    )
    committed = committed_command(session, event_id)
    if committed:
        return replay_or_conflict(committed, "DONE", fingerprint)

    # -- Direct processing only (PROJECT_PROFILE §12) --------------------
    if context.state == ProcessingState.READY_TO_TRANSFER:
        raise ConflictError(
            f"Quantity Flow {context.flow.id} has already completed processing at Area"
            f" '{context.area.name}' (DONE) and waits for transfer. Nothing was recorded."
        )
    if not context.direct_processing:
        # The Area has Machines: its quantity is queued, on a Machine or
        # finished — a Machine-Area DONE names the Machine it is on.
        raise ConflictError(
            f"Area '{context.area.name}' has Machines: quantity there is completed from"
            " the Machine it is assigned to, not directly. Select the Machine the"
            " quantity is on. Nothing was recorded."
        )
    if context.state != ProcessingState.PROCESSING:  # pragma: no cover - derivation invariant
        raise ConflictError(
            f"Quantity Flow {context.flow.id} is not directly processing at Area"
            f" '{context.area.name}'. Nothing was recorded."
        )
    if context.flow.current_machine_id is not None:  # pragma: no cover - projection invariant
        raise ConflictError(
            f"Quantity Flow {context.flow.id} references a Machine although Area"
            f" '{context.area.name}' has none. Nothing was recorded."
        )

    # -- The one write, inside the open transaction ----------------------
    movement = in_area_movement(
        context,
        movement_type=MovementType.AREA_COMPLETED,
        source_machine_id=None,
        destination_machine_id=None,
        event_id=event_id,
        metadata=command_metadata("DONE", fingerprint),
    )
    session.add(movement)
    # Projection: the Area stays, the Machine was and stays NULL; the
    # finished state is told by the Movement just appended.
    context.flow.current_machine_id = None
    context.flow.updated_at = func.now()
    return commit_or_replay(
        session, movement, kind="DONE", event_id=event_id, fingerprint=fingerprint
    )
