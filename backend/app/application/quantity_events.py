"""Auditable quantity events — Scrap and quantity addition (Phase 9).

The two Scan Station commands that intentionally change the amount of
quantity in active production (PROJECT_PROFILE §11 Quantity Model /
Quantity Integrity / Scrap; §8.11), each ONE database transaction,
idempotent per `device_event_id`, following the Phase 6 command
protocol (`app.application.machine_processing`). Both keep the
reconciliation `introduced = active + stocked + scrapped` exact: an
intentional correction is always an explicit auditable event, never a
silent edit.

**Scrap** (`scrap_flow`): damaged quantity leaves active production
with one canonical `SCRAPPED` Movement per confirmed action —
`PF:SCRAP` counting, correction and reset are client-side and change
no production state; one confirmation creates exactly ONE auditable
operation for the total. The common scrap reason is mandatory (also
CHECK-enforced). A full scrap closes the flow (`status = SCRAPPED`,
`closed_at`); a partial scrap SPLITS the flow first inside the same
command (Phase 8 machinery — three `SPLIT` rows, then the `SCRAPPED`
on the selected child, which closes) and the remainder keeps exactly
the source's state — including staying ON its Machine. Scrapping
quantity that is ON_MACHINE takes the Machine row lock, records the
Machine it left (`source_machine_id`) and re-judges the derived
Machine state; every other state (QUEUED, PROCESSING,
READY_TO_TRANSFER) scraps without a Machine reference. History and
lineage stay complete: the scrapped flow keeps its last position and
its Movements, and the scrapped total of a PN stays visible in the
read models (`app.application.scan_station`).

**Quantity addition** (`add_quantity`): physical quantity found at the
station's Area that was NOT transferred from another Area enters
production as a NEW QuantityFlow whose first Movement is
`QUANTITY_ADJUSTED` with `direction = INCREASE` (Movement metadata) —
a quantity-introducing event exactly like a `RECEIVED`, but always
scan-driven and always with a mandatory reason. It is never hidden as
a transfer, never changes any Work Order Demand requested quantity,
and never guesses a demand context: the new flow is FLOATING and
carries no demand linkage. The addition exists only where the Scan
Station offers it (GUI_DESIGN §4.7 item 3): the PN must already have
ACTIVE quantity in the station's Area — introducing a PN with no
quantity there is the Receive Quantity intake, a different workflow.
The added quantity enters the Area queue (Area with Machines) or
direct processing (Area without Machines) — derived, not stored. The
Scan Station row is locked and re-read under the lock (active, still
bound to the resolved Area — a rebound or deactivated station refuses
with zero writes) and the target Area row is locked like a transfer
destination, so a station configuration change, an Area deactivation
and an addition each have one serial outcome.

Both commands: invalid, stale or conflicting input performs no write;
replay of the same `device_event_id` + same intent returns the
original committed result; a mismatched reuse is an explicit conflict;
a race lost at COMMIT replays the winner. Undo of either command is
`app.application.undo` (they are eligible commands like any other).
Explicitly NOT here: Worker identity (Phase 13), any Manager approval
flow (none exists for the operator-allowed addition), `STOCKED`
(Phase 10).
"""

import datetime
import hashlib
import json
from typing import Any, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.common import device_event_id_text, flush, required_text
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.lineage import split_prefix
from app.application.machine_processing import (
    FINGERPRINT_KEY,
    command_metadata,
    command_size,
    committed_command,
    lock_flow_and_station,
    split_if_partial,
)
from app.application.machines import (
    area_has_machines,
    assigned_quantity,
    lock_machine,
    note_assignment_change,
)
from app.application.part_numbers import canonical_part_number
from app.application.projections import processing_state_of
from app.application.transfers import require_production_station, resolve_arrival_operation
from app.domain.enums import (
    AdjustmentDirection,
    MovementType,
    ProcessingState,
    QuantityFlowStatus,
    RouteMode,
)
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    Machine,
    PartMovement,
    QuantityFlow,
    ScanStation,
)

# Immutable record of the addition's direction and the state it entered
# (queued or directly processing, judged at command time) — read back
# verbatim on replay, never re-derived from the Area's current mode.
ADJUSTMENT_KEY = "adjustment"


def _reused_for_other_kind() -> IdempotencyConflictError:
    return IdempotencyConflictError(
        "This device_event_id was already used for a different production"
        " request. Nothing was recorded — a new intent needs a new"
        " device_event_id."
    )


# ---------------------------------------------------------------------------
# Scrap
# ---------------------------------------------------------------------------


class ScrapResult(NamedTuple):
    """One committed Scrap, read from its immutable SCRAPPED Movement."""

    movement_id: int
    # The flow the SCRAPPED row closed: the source itself, or the
    # selected child of the split a partial scrap performed.
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    # The Machine the scrapped quantity left; None unless it was
    # ON_MACHINE.
    machine_id: int | None
    reason: str
    station_id: str
    # Set when only a part of the source was scrapped (Phase 8 SPLIT
    # inside the same command): the consumed source and the remainder
    # that stays in production in exactly the source's state.
    source_quantity_flow_id: int | None
    remainder_quantity_flow_id: int | None
    remainder_quantity: int | None
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


def _scrap_fingerprint(
    *, station_id: str, quantity_flow_id: int, part_number: str, quantity: int, reason: str
) -> str:
    normalized = {
        "command": "SCRAP",
        "station_id": station_id,
        "quantity_flow_id": quantity_flow_id,
        "part_number": part_number,
        "quantity": quantity,
        "reason": reason,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scrap_result(command: list[PartMovement], *, created: bool) -> ScrapResult:
    split, action = split_prefix(command)
    movement = action[-1]
    if (
        len(action) != 1
        or movement.movement_type != MovementType.SCRAPPED
        or movement.from_area_id is None
        or movement.station_id is None
        or movement.reason is None
    ):
        raise _reused_for_other_kind()
    return ScrapResult(
        movement_id=movement.id,
        quantity_flow_id=movement.quantity_flow_id,
        part_number=movement.part_number,
        quantity=movement.quantity,
        area_id=movement.to_area_id,
        machine_id=movement.source_machine_id,
        reason=movement.reason,
        station_id=movement.station_id,
        source_quantity_flow_id=split.source_quantity_flow_id if split else None,
        remainder_quantity_flow_id=split.remainder_quantity_flow_id if split else None,
        remainder_quantity=split.remainder_quantity if split else None,
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
        created=created,
    )


def _scrap_replay_or_conflict(command: list[PartMovement], fingerprint: str) -> ScrapResult:
    _, action = split_prefix(command)
    stored = (action[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    if stored != fingerprint or any(
        (row.metadata_ or {}).get(FINGERPRINT_KEY) != stored for row in command
    ):
        raise _reused_for_other_kind()
    return _scrap_result(command, created=False)


def scrap_flow(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    quantity: object,
    reason: object,
    device_event_id: object,
) -> ScrapResult:
    """Remove damaged quantity from active production, ONE transaction.

    Same protocol as the in-Area commands: input shape → fingerprint →
    idempotency fast path → flow + station locks → idempotency
    re-check → state reads → the writes → COMMIT (or replay of a race
    winner). Any failure before COMMIT leaves zero writes.
    """
    pn = canonical_part_number(part_number)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise InvalidInputError("Scrap quantity must be a positive whole number.")
    scrap_reason = required_text(reason, "The scrap reason")
    event_id = device_event_id_text(device_event_id)
    fingerprint = _scrap_fingerprint(
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        quantity=quantity,
        reason=scrap_reason,
    )
    committed = committed_command(session, event_id)
    if committed:
        return _scrap_replay_or_conflict(committed, fingerprint)

    context = lock_flow_and_station(
        session,
        station_id=station_id,
        quantity_flow_id=quantity_flow_id,
        part_number=pn,
        quantity=quantity,
        action="Scrap",
    )
    committed = committed_command(session, event_id)
    if committed:
        return _scrap_replay_or_conflict(committed, fingerprint)

    # -- The Machine the quantity would leave (ON_MACHINE only) ---------
    machine: Machine | None = None
    if context.state == ProcessingState.ON_MACHINE:
        if context.flow.current_machine_id is None:  # pragma: no cover - projection invariant
            raise ConflictError(
                f"Quantity Flow {context.flow.id} is on a Machine according to its"
                " history but its projection carries none. Nothing was recorded."
            )
        machine = lock_machine(session, context.flow.current_machine_id)
        if machine is None:  # pragma: no cover - FK guarantees the row
            raise ConflictError(f"Machine {context.flow.current_machine_id} does not exist.")

    # -- Writes — all inside the one open transaction ------------------
    assigned_before = assigned_quantity(session, machine.id) if machine is not None else 0
    metadata = command_metadata("SCRAP", fingerprint, size=command_size(context, 1))
    # A partial scrap removes only the selected child; the remainder
    # keeps the source's state — including staying on its Machine.
    context, command, sequence = split_if_partial(
        session, context, event_id=event_id, metadata=metadata
    )
    command.append(
        PartMovement(
            quantity_flow_id=context.flow.id,
            part_number=pn,
            movement_type=MovementType.SCRAPPED,
            quantity=context.flow.quantity,
            from_area_id=context.area.id,
            to_area_id=context.area.id,
            operation_id=context.latest.operation_id,
            assigned_route_step_id=None,
            station_id=context.station.station_id,
            source_machine_id=machine.id if machine is not None else None,
            destination_machine_id=None,
            reason=scrap_reason,
            occurred_at=func.now(),
            server_received_at=func.now(),
            device_event_id=event_id,
            command_sequence=sequence,
            metadata_=metadata,
        )
    )
    session.add_all(command)
    # The scrapped flow closes and leaves active production; its last
    # position and its history stay (PROJECT_PROFILE §11 Scrap).
    context.flow.status = QuantityFlowStatus.SCRAPPED
    context.flow.closed_at = func.now()
    context.flow.current_machine_id = None
    context.flow.updated_at = func.now()
    if machine is not None:
        note_assignment_change(
            machine,
            assigned_before=assigned_before,
            assigned_after=assigned_before - context.flow.quantity,
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_command(session, event_id)
            if winner:
                return _scrap_replay_or_conflict(winner, fingerprint)
        raise
    return _scrap_result(command, created=True)


# ---------------------------------------------------------------------------
# Quantity addition — QUANTITY_ADJUSTED · INCREASE
# ---------------------------------------------------------------------------


class QuantityAdditionResult(NamedTuple):
    """One committed addition, read from its immutable Movement."""

    movement_id: int
    # The NEW QuantityFlow the addition introduced.
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    operation_id: int
    # QUEUED in a Machine Area, PROCESSING in an Area without Machines —
    # judged at command time and recorded immutably for replay.
    processing_state: ProcessingState
    reason: str
    station_id: str
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


def _addition_fingerprint(
    *,
    station_id: str,
    part_number: str,
    quantity: int,
    reason: str,
    operation_id: int | None,
) -> str:
    normalized = {
        "command": "ADD",
        "station_id": station_id,
        "part_number": part_number,
        "quantity": quantity,
        "reason": reason,
        "operation_id": operation_id,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _addition_result(command: list[PartMovement], *, created: bool) -> QuantityAdditionResult:
    movement = command[-1]
    recorded = (movement.metadata_ or {}).get(ADJUSTMENT_KEY)
    if (
        len(command) != 1
        or movement.movement_type != MovementType.QUANTITY_ADJUSTED
        or movement.station_id is None
        or movement.reason is None
        or not isinstance(recorded, dict)
        or "processing_state" not in recorded
    ):
        raise _reused_for_other_kind()
    return QuantityAdditionResult(
        movement_id=movement.id,
        quantity_flow_id=movement.quantity_flow_id,
        part_number=movement.part_number,
        quantity=movement.quantity,
        area_id=movement.to_area_id,
        operation_id=movement.operation_id,
        processing_state=ProcessingState(recorded["processing_state"]),
        reason=movement.reason,
        station_id=movement.station_id,
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
        created=created,
    )


def _addition_replay_or_conflict(
    command: list[PartMovement], fingerprint: str
) -> QuantityAdditionResult:
    stored = (command[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    if stored != fingerprint:
        raise _reused_for_other_kind()
    return _addition_result(command, created=False)


def _lock_witness_flow(session: Session, part_number: str, area: Area) -> QuantityFlow | None:
    """One ACTIVE flow of the PN in the Area, locked until COMMIT — or None.

    The authoritative "existing in-Area quantity" precondition of the
    addition: candidates are picked with a fresh-snapshot SELECT, then
    locked and RE-READ under the row lock (``populate_existing``), so a
    candidate that was transferred away, scrapped or consumed between
    the pick and the lock is discarded and the next one tried; None
    means no ACTIVE flow of the PN remains in the Area at this moment.
    Holding the returned row's lock until COMMIT guarantees the witness
    is still there when the addition commits — every command that
    could remove it serializes behind the same flow row lock.
    """
    for _ in range(1_000):  # bounded defensively; one retry is the realistic worst case
        candidate_id = session.scalar(
            select(QuantityFlow.id)
            .where(
                QuantityFlow.part_number == part_number,
                QuantityFlow.status == QuantityFlowStatus.ACTIVE,
                QuantityFlow.current_area_id == area.id,
            )
            .order_by(QuantityFlow.id)
            .limit(1)
        )
        if candidate_id is None:
            return None
        witness = session.get(
            QuantityFlow, candidate_id, with_for_update=True, populate_existing=True
        )
        if (
            witness is not None
            and witness.status == QuantityFlowStatus.ACTIVE
            and witness.current_area_id == area.id
        ):
            return witness
        # The candidate left the Area or closed while we waited for its
        # lock; the next SELECT sees the committed state and re-picks.
    raise ConflictError(  # pragma: no cover - defensive bound
        f"Could not establish the existing quantity of Part Number '{part_number}'"
        f" in Area '{area.name}'. Nothing was recorded."
    )


def add_quantity(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity: object,
    reason: object,
    operation_id: int | None,
    device_event_id: object,
) -> QuantityAdditionResult:
    """Introduce found physical quantity at the station's Area, ONE transaction.

    Creates a new FLOATING QuantityFlow with its `QUANTITY_ADJUSTED ·
    INCREASE` first Movement; requested quantities are never touched.
    Order: input shape → fingerprint → idempotency fast path → station
    context → the in-Area precondition under the WITNESS flow row lock
    (held until COMMIT, so removing the last in-Area quantity and the
    addition have one serial outcome) → the STATION row lock with the
    authoritative active/binding re-check (a station deactivated or
    rebound meanwhile is refused with nothing written) → the
    idempotency re-check → the
    locked Area re-read → the
    Operation lock → the writes → COMMIT (or replay of a race winner).
    """
    pn = canonical_part_number(part_number)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise InvalidInputError("Added quantity must be a positive whole number.")
    addition_reason = required_text(reason, "The addition reason")
    event_id = device_event_id_text(device_event_id)
    fingerprint = _addition_fingerprint(
        station_id=station_id,
        part_number=pn,
        quantity=quantity,
        reason=addition_reason,
        operation_id=operation_id,
    )
    committed = committed_command(session, event_id)
    if committed:
        return _addition_replay_or_conflict(committed, fingerprint)

    station, area = require_production_station(session, station_id)
    # The addition exists only beside existing quantity (GUI_DESIGN
    # §4.7 item 3): a PN with nothing ACTIVE in this Area is received
    # through the intake workflow, never through an addition. The
    # precondition is judged AUTHORITATIVELY under a row lock: one
    # ACTIVE flow of the PN in this Area — the witness — is locked
    # `FOR UPDATE` and re-read under the lock, and the lock is held
    # until COMMIT. Every command that could move or close that flow
    # (transfer, scrap, split, merge, undo) takes the flow row lock
    # first, so the addition and the removal of the last in-Area
    # quantity have one serial outcome: the remover either committed
    # before (the re-pick below sees the new state and refuses, or
    # finds another witness) or blocks behind the witness lock until
    # the addition committed beside still-existing quantity. The lock
    # order — witness flow → Area → Operation — is a sub-sequence of
    # the established flow → station → Machine → Area → Operation
    # order, so no cycle is possible.
    if _lock_witness_flow(session, pn, area) is None:
        raise ConflictError(
            f"Part Number '{pn}' has no active quantity in Area '{area.name}'."
            " Add more quantity applies beside existing quantity only — use"
            " Receive Quantity or a transfer instead. Nothing was recorded."
        )
    # The Scan Station row locked until COMMIT and RE-READ under the
    # lock, judged AUTHORITATIVELY there (the unlocked read above may
    # predate a concurrent configuration change): the station must
    # still be active and still bound to exactly the Area the addition
    # was resolved for — a station deactivated or rebound meanwhile is
    # refused with nothing written, so a `QUANTITY_ADJUSTED` can never
    # carry the `station_id` of a station that no longer belongs to
    # its Area. The rebinding/deactivation UPDATE blocks behind this
    # lock, so a configuration change and an addition have one serial
    # outcome. Lock position: witness flow → STATION → Area →
    # Operation — the established order.
    locked_station = session.get(
        ScanStation, station_id, with_for_update=True, populate_existing=True
    )
    if locked_station is None:  # pragma: no cover - it existed on the read above
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    station = locked_station
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was recorded."
        )
    if station.area_id != area.id:
        raise ConflictError(
            f"Scan Station '{station_id}' is no longer bound to Area '{area.name}' —"
            " its configuration changed since the addition was prepared. Reload the"
            " station and confirm the addition again. Nothing was recorded."
        )
    # Idempotency RE-CHECK after the blocking locks (SLICE1 §14), same
    # protocol as every command that waits on a row lock: a transport
    # retry that raced the original submission replays it here.
    committed = committed_command(session, event_id)
    if committed:
        return _addition_replay_or_conflict(committed, fingerprint)
    # The Area row locked until COMMIT, flags judged on the locked
    # re-read (the same protocol as a transfer destination): Area
    # deactivation and an addition have one serial outcome.
    session.refresh(area, with_for_update=True)
    if not area.is_active:
        raise ConflictError(f"Area '{area.name}' is inactive and cannot accept added quantity.")
    if area.is_terminal:
        raise ConflictError(
            f"Area '{area.name}' is a terminal Area and holds finished quantity only."
            " Physical quantity is added where it is processed. Nothing was recorded."
        )
    operation = resolve_arrival_operation(session, area, operation_id)
    # The derived arrival state at command time (PROJECT_PROFILE §12).
    state = processing_state_of(
        MovementType.QUANTITY_ADJUSTED,
        direct_processing=not area_has_machines(session, area.id),
    )
    flow = QuantityFlow(
        part_number=pn,
        quantity=quantity,
        status=QuantityFlowStatus.ACTIVE,
        route_mode=RouteMode.FLOATING,
        assigned_route_id=None,
        current_area_id=area.id,
        current_machine_id=None,
    )
    session.add(flow)
    flush(session, {})
    metadata: dict[str, Any] = {
        **command_metadata("ADD", fingerprint, size=1),
        ADJUSTMENT_KEY: {
            "direction": AdjustmentDirection.INCREASE.value,
            "processing_state": state.value,
        },
    }
    movement = PartMovement(
        quantity_flow_id=flow.id,
        part_number=pn,
        movement_type=MovementType.QUANTITY_ADJUSTED,
        quantity=quantity,
        from_area_id=None,
        to_area_id=area.id,
        operation_id=operation.id,
        assigned_route_step_id=None,
        station_id=station.station_id,
        source_machine_id=None,
        destination_machine_id=None,
        reason=addition_reason,
        occurred_at=func.now(),
        server_received_at=func.now(),
        device_event_id=event_id,
        command_sequence=1,
        metadata_=metadata,
    )
    session.add(movement)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_command(session, event_id)
            if winner:
                return _addition_replay_or_conflict(winner, fingerprint)
        raise
    return _addition_result([movement], created=True)
