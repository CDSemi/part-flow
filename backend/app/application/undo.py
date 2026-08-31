"""Command-level Undo (Phase 9 — IMPLEMENTATION_ROADMAP; PROJECT_PROFILE §16).

Undo corrects a recent scanning mistake by reversing one complete
**application command** — never one arbitrary row of a multi-Movement
action: the whole command identified by its `device_event_id` (an
implicit `AREA_COMPLETED` + `TRANSFERRED` transfer, a SPLIT-prefixed
partial action, a MERGE) reverses as one, or not at all. The original
Movements are never deleted or edited (append-only history): the Undo
appends one compensating `REVERSED` Movement per original row, in
reverse order, each referencing its original through
`reverses_movement_id` — at most one reversal per original (UNIQUE),
so a command can never be undone twice, not even by a race between two
concurrent submissions.

**How state is restored.** Nothing re-states position on a `REVERSED`
row: every derivation (`app.application.projections`) EXCLUDES the
reversed pair — the `REVERSED` rows and the originals they reference —
so the effective Movement of every involved flow is simply the one
before the undone command, which restores the Area, the Machine, the
Operation, the route position and the holding state at once, also
through lineage. The maintained projection is updated in the same
transaction from exactly that derivation, so projection and replay can
never disagree. Flow lifecycle: a flow the command CLOSED (the split
or merge sources, a scrapped flow) reopens as ACTIVE; a flow the
command CREATED (split children, a merge result, an added flow) closes
as `REVERSED` and never counts as quantity again; lineage edges of an
undone SPLIT/MERGED stay stored (append-only) but no longer count.
Quantity is conserved by construction: reversal never changes any
flow's quantity — it only moves flows between open and closed.

**Eligibility** — judged under the row locks, zero writes otherwise:

- the command exists, matches the confirmed PN, and was recorded at
  THIS Scan Station (a Management-recorded event — the release
  `RECEIVED` — is never undone from a station); the station is still
  active and still bound to the Area the command acted in;
- the command is the MOST RECENT thing that happened to every flow it
  touched: any later Movement on any involved flow makes it ineligible
  (§16 — Undo targets the most recent eligible operation; correcting
  older history is the broader Manager/Admin correction of a later
  phase);
- the command was not already reversed, and is not itself an Undo — a
  reversal is permanent and corrected forward, never un-reversed;
- restoring quantity onto a Machine requires that Machine to still be
  active (its row is locked; a Maintenance override does not block —
  the quantity physically never left the Machine); restoring quantity
  into an Area requires that Area to still be active (locked like a
  transfer destination, so Area deactivation and an Undo have one
  serial outcome).

**Confirmation and audit.** The read model (`undo_preview`) serves the
mandatory summary confirmation — original action, quantity, source and
destination, Machine, timestamp, and the exact effect of the reversal
— before anything is submitted; the command stores the restored state
verbatim in the `REVERSED` rows' metadata (`undo` block), so an
idempotent replay returns the identical original response whatever
happened since. The whole command follows the Phase 6 protocol: ONE
transaction, idempotent per its own `device_event_id`, fingerprint
mismatch an explicit conflict, a race lost at COMMIT replays the
winner.

Deliberate boundaries (no simulation of later phases): no Worker
identity on the reversal (Worker sessions, Phase 13 — the badge/final
gate is a frontend gate on the same command), no reason-when-configured
(the configuration does not exist before Phase 13), and no role
authorization — Operators/Managers/Admins arrive with Users/RBAC
(Phase 14); until then the API surface carries no pretend permission
check.
"""

import datetime
import hashlib
import json
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.application.common import device_event_id_text
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.machine_processing import (
    COMMAND_KEY,
    FINGERPRINT_KEY,
    command_metadata,
    committed_command,
)
from app.application.machines import (
    area_has_machines,
    assigned_quantity,
    lock_machine,
    note_assignment_change,
)
from app.application.part_numbers import canonical_part_number
from app.application.projections import effective_latest_movement, processing_state_of
from app.application.transfers import require_production_station
from app.domain.enums import MovementType, ProcessingState, QuantityFlowStatus
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    Machine,
    PartMovement,
    QuantityFlow,
    ScanStation,
)

# Immutable record of what the reversal did, written on every REVERSED
# row of the Undo command and read back verbatim on replay.
UNDO_KEY: Final = "undo"

# The database backstop against a double reversal (models.py).
_REVERSES_UNIQUE_CONSTRAINT: Final = "uq_part_movements_reverses_movement_id"


class ReversedMovement(NamedTuple):
    """One compensating row and the original it undoes."""

    movement_id: int
    reverses_movement_id: int
    original_movement_type: str


class RestoredFlow(NamedTuple):
    """One involved flow after the reversal.

    ``status`` ACTIVE for a reopened or repositioned flow (with its
    restored Area/Machine); REVERSED for a flow whose creating command
    was undone (closed, no position).
    """

    quantity_flow_id: int
    quantity: int
    status: QuantityFlowStatus
    current_area_id: int | None
    current_machine_id: int | None


class UndoResult(NamedTuple):
    """One committed Undo, read from its immutable REVERSED Movements."""

    reverses_device_event_id: str
    reversed_kind: str | None
    part_number: str
    station_id: str
    movements: list[ReversedMovement]
    flows: list[RestoredFlow]
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


# ---------------------------------------------------------------------------
# Shared command reading
# ---------------------------------------------------------------------------


def _command_kind(rows: list[PartMovement]) -> str | None:
    block = (rows[-1].metadata_ or {}).get(COMMAND_KEY)
    if isinstance(block, dict) and isinstance(block.get("kind"), str):
        return str(block["kind"])
    return None


def _involved_flow_ids(rows: list[PartMovement]) -> list[int]:
    seen: dict[int, None] = {}
    for row in rows:
        seen.setdefault(row.quantity_flow_id, None)
    return sorted(seen)


def _already_reversed(session: Session, rows: list[PartMovement]) -> bool:
    return (
        session.scalar(
            select(PartMovement.id)
            .where(PartMovement.reverses_movement_id.in_([row.id for row in rows]))
            .limit(1)
        )
        is not None
    )


def _newest_effective_movement_ids(session: Session, flow_ids: list[int]) -> dict[int, int]:
    """The newest EFFECTIVE Movement per flow: reversed pairs never count.

    §16 — after a confirmed Undo the context advances to the previous
    operation: a command followed only by reversals (its own later
    commands all undone) is still "the most recent eligible completed
    operation" of its quantity.
    """
    reversal = aliased(PartMovement)
    rows = session.execute(
        select(PartMovement.quantity_flow_id, func.max(PartMovement.id))
        .where(
            PartMovement.quantity_flow_id.in_(flow_ids),
            PartMovement.movement_type != MovementType.REVERSED,
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
        .group_by(PartMovement.quantity_flow_id)
    )
    return {int(flow_id): int(movement_id) for flow_id, movement_id in rows}


def _first_movement_ids(session: Session, flow_ids: list[int]) -> dict[int, int]:
    rows = session.execute(
        select(PartMovement.quantity_flow_id, func.min(PartMovement.id))
        .where(PartMovement.quantity_flow_id.in_(flow_ids))
        .group_by(PartMovement.quantity_flow_id)
    )
    return {int(flow_id): int(movement_id) for flow_id, movement_id in rows}


def _ineligibility(session: Session, station: ScanStation, rows: list[PartMovement]) -> str | None:
    """Why the command cannot be undone — None when it is eligible.

    Advisory on the unlocked preview read; authoritative when the Undo
    command re-judges it under the flow row locks.
    """
    # The fundamental refusals first: what a command IS decides before
    # where it was recorded.
    if (
        any(row.movement_type == MovementType.REVERSED for row in rows)
        or _command_kind(rows) == "UNDO"
    ):
        return (
            "This action is itself a reversal. A reversal is permanent — record the"
            " intended action again instead of reversing the reversal."
        )
    if any(row.station_id is None for row in rows):
        return (
            "This event was recorded by Management, not at a Scan Station."
            " It cannot be undone from a station."
        )
    if any(row.station_id != station.station_id for row in rows):
        return (
            "This action was recorded at a different Scan Station. Undo it at the"
            " station that recorded it."
        )
    if station.area_id != rows[-1].to_area_id:
        return (
            "This Scan Station is no longer bound to the Area where the action was"
            " recorded — its configuration changed since. The action cannot be"
            " undone from here."
        )
    if _already_reversed(session, rows):
        return "This action has already been reversed."
    flow_ids = _involved_flow_ids(rows)
    newest = _newest_effective_movement_ids(session, flow_ids)
    command_ids = {row.id for row in rows}
    if any(newest.get(flow_id) not in command_ids for flow_id in flow_ids):
        return (
            "Later activity exists for this quantity: the action is no longer the"
            " most recent recorded operation and cannot be undone. Undo the more"
            " recent actions first."
        )
    return None


class _Restoration(NamedTuple):
    """What the reversal will do to every involved flow."""

    flows: list[RestoredFlow]
    # Restored Machine per flow id (flows ending ACTIVE on a Machine).
    machines: dict[int, int | None]


def _plan_restoration(
    session: Session,
    rows: list[PartMovement],
    flows: dict[int, QuantityFlow],
) -> _Restoration:
    """Derive each involved flow's post-reversal lifecycle and position.

    A flow the command created (its first Movement belongs to the
    command) closes as REVERSED; a flow the command closed reopens as
    ACTIVE; every other flow stays ACTIVE. The position of every flow
    ending ACTIVE is the reversal-aware derivation EXCLUDING the undone
    command — exactly what the projections replay will derive after the
    REVERSED rows exist.
    """
    event_id = rows[0].device_event_id
    command_ids = {row.id for row in rows}
    first = _first_movement_ids(session, list(flows))
    restored: list[RestoredFlow] = []
    machines: dict[int, int | None] = {}
    for flow_id in sorted(flows):
        flow = flows[flow_id]
        if first[flow_id] in command_ids:
            restored.append(
                RestoredFlow(flow_id, flow.quantity, QuantityFlowStatus.REVERSED, None, None)
            )
            continue
        movement = effective_latest_movement(session, flow_id, exclude_device_event_id=event_id)
        machine_id = movement.destination_machine_id
        restored.append(
            RestoredFlow(
                flow_id,
                flow.quantity,
                QuantityFlowStatus.ACTIVE,
                movement.to_area_id,
                machine_id,
            )
        )
        machines[flow_id] = machine_id
    return _Restoration(restored, machines)


# ---------------------------------------------------------------------------
# Read model — the summary confirmation (§16)
# ---------------------------------------------------------------------------


class UndoMovementSummary(NamedTuple):
    """One original Movement of the command, as the confirmation lists it."""

    movement_id: int
    movement_type: str
    movement_reason: str | None
    quantity: int
    from_area: Area | None
    to_area: Area
    machine_id: int | None
    operation_id: int


class RestoredFlowPreview(NamedTuple):
    """The effect of the reversal on one flow, for the confirmation."""

    quantity_flow_id: int
    quantity: int
    status: QuantityFlowStatus
    area: Area | None
    machine_id: int | None
    processing_state: ProcessingState | None


class UndoPreview(NamedTuple):
    reverses_device_event_id: str
    station_id: str
    kind: str | None
    part_number: str
    # The acted-on quantity: the last action row's.
    quantity: int
    occurred_at: datetime.datetime
    eligible: bool
    ineligible_reason: str | None
    movements: list[UndoMovementSummary]
    restored: list[RestoredFlowPreview]


def undo_preview(session: Session, station_id: str, device_event_id: object) -> UndoPreview:
    """The summary confirmation of undoing one command — a read, no locks.

    The Undo command re-judges eligibility under the row locks; this
    read prepares the §16 confirmation (original action, quantity,
    source and destination, Machine, timestamp, and the effect of the
    reversal) and says whether Undo is currently possible and why not.
    """
    station, _ = require_production_station(session, station_id)
    event_id = device_event_id_text(device_event_id)
    rows = committed_command(session, event_id)
    if not rows:
        raise NotFoundError(f"No production event was recorded under '{event_id}'.")
    reason = _ineligibility(session, station, rows)
    areas: dict[int, Area] = {}

    def _area(area_id: int) -> Area:
        if area_id not in areas:
            loaded = session.get(Area, area_id)
            if loaded is None:  # pragma: no cover - FK guarantees the row
                raise NotFoundError(f"Area {area_id} does not exist.")
            areas[area_id] = loaded
        return areas[area_id]

    movements = [
        UndoMovementSummary(
            movement_id=row.id,
            movement_type=row.movement_type,
            movement_reason=row.movement_reason,
            quantity=row.quantity,
            from_area=_area(row.from_area_id) if row.from_area_id is not None else None,
            to_area=_area(row.to_area_id),
            machine_id=(
                row.destination_machine_id
                if row.movement_type == MovementType.ASSIGNED_TO_MACHINE
                else row.source_machine_id
            ),
            operation_id=row.operation_id,
        )
        for row in rows
    ]
    restored: list[RestoredFlowPreview] = []
    if reason is None:
        flows: dict[int, QuantityFlow] = {}
        for flow_id in _involved_flow_ids(rows):
            flow = session.get(QuantityFlow, flow_id)
            if flow is None:  # pragma: no cover - FK guarantees the row
                raise NotFoundError(f"Quantity Flow {flow_id} does not exist.")
            flows[flow_id] = flow
        plan = _plan_restoration(session, rows, flows)
        for item in plan.flows:
            state: ProcessingState | None = None
            if item.status == QuantityFlowStatus.ACTIVE and item.current_area_id is not None:
                movement = effective_latest_movement(
                    session, item.quantity_flow_id, exclude_device_event_id=event_id
                )
                state = processing_state_of(
                    movement.movement_type,
                    direct_processing=not area_has_machines(session, item.current_area_id),
                )
            restored.append(
                RestoredFlowPreview(
                    quantity_flow_id=item.quantity_flow_id,
                    quantity=item.quantity,
                    status=item.status,
                    area=_area(item.current_area_id) if item.current_area_id is not None else None,
                    machine_id=item.current_machine_id,
                    processing_state=state,
                )
            )
    return UndoPreview(
        reverses_device_event_id=event_id,
        station_id=station.station_id,
        kind=_command_kind(rows),
        part_number=rows[0].part_number,
        quantity=rows[-1].quantity,
        occurred_at=rows[-1].occurred_at,
        eligible=reason is None,
        ineligible_reason=reason,
        movements=movements,
        restored=restored,
    )


# ---------------------------------------------------------------------------
# The Undo command
# ---------------------------------------------------------------------------


def _request_fingerprint(
    *, station_id: str, part_number: str, reverses_device_event_id: str
) -> str:
    normalized = {
        "command": "UNDO",
        "station_id": station_id,
        "part_number": part_number,
        "reverses_device_event_id": reverses_device_event_id,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_from_command(
    session: Session, command: list[PartMovement], *, created: bool
) -> UndoResult:
    last = command[-1]
    recorded = (last.metadata_ or {}).get(UNDO_KEY)
    if (
        any(row.movement_type != MovementType.REVERSED for row in command)
        or not isinstance(recorded, dict)
        or last.station_id is None
    ):
        raise IdempotencyConflictError(
            "This device_event_id belongs to a different kind of production"
            " event. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    originals = {
        int(row.id): str(row.movement_type)
        for row in session.scalars(
            select(PartMovement).where(
                PartMovement.id.in_(
                    [row.reverses_movement_id for row in command if row.reverses_movement_id]
                )
            )
        )
    }
    return UndoResult(
        reverses_device_event_id=str(recorded.get("reverses_device_event_id")),
        reversed_kind=recorded.get("kind"),
        part_number=last.part_number,
        station_id=last.station_id,
        movements=[
            ReversedMovement(
                movement_id=row.id,
                reverses_movement_id=row.reverses_movement_id or 0,
                original_movement_type=originals.get(row.reverses_movement_id or 0, ""),
            )
            for row in command
        ],
        flows=[
            RestoredFlow(
                quantity_flow_id=int(item["quantity_flow_id"]),
                quantity=int(item["quantity"]),
                status=QuantityFlowStatus(item["status"]),
                current_area_id=item.get("current_area_id"),
                current_machine_id=item.get("current_machine_id"),
            )
            for item in recorded.get("flows", [])
        ],
        device_event_id=last.device_event_id,
        occurred_at=last.occurred_at,
        created=created,
    )


def _replay_or_conflict(
    session: Session, command: list[PartMovement], fingerprint: str
) -> UndoResult:
    stored = (command[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    if stored != fingerprint or any(
        (row.metadata_ or {}).get(FINGERPRINT_KEY) != stored for row in command
    ):
        raise IdempotencyConflictError(
            "This device_event_id was already used for a different production"
            " request. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    return _result_from_command(session, command, created=False)


def undo_command(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    reverses_device_event_id: object,
    device_event_id: object,
) -> UndoResult:
    """Reverse one complete committed command, ONE transaction.

    Validates everything under the row locks before any write; an
    ineligible, stale or conflicting request performs zero writes. A
    replayed submission (same own ``device_event_id`` + same intent)
    returns the original committed result; a mismatched reuse is an
    explicit idempotency conflict.
    """
    pn = canonical_part_number(part_number)
    reverses_id = device_event_id_text(reverses_device_event_id)
    event_id = device_event_id_text(device_event_id)
    if event_id == reverses_id:
        raise InvalidInputError(
            "The Undo needs its own device_event_id — it is a new production"
            " event and never reuses the id of the action it reverses."
        )
    fingerprint = _request_fingerprint(
        station_id=station_id, part_number=pn, reverses_device_event_id=reverses_id
    )

    # -- Idempotency fast path (SLICE1 §14) ------------------------------
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

    # -- The command to reverse (immutable rows, no lock needed) ---------
    rows = committed_command(session, reverses_id)
    if not rows:
        raise InvalidInputError(
            f"No production event was recorded under '{reverses_id}'. Nothing was reversed."
        )
    if any(row.part_number != pn for row in rows):
        raise InvalidInputError(
            f"Part Number '{pn}' does not match the recorded action"
            f" ('{rows[0].part_number}'). Nothing was reversed."
        )

    # -- Involved flows under their row locks, ascending id --------------
    flows: dict[int, QuantityFlow] = {}
    for flow_id in _involved_flow_ids(rows):
        flow = session.get(QuantityFlow, flow_id, with_for_update=True)
        if flow is None:  # pragma: no cover - FK guarantees the row
            raise InvalidInputError(f"Quantity Flow {flow_id} does not exist.")
        flows[flow_id] = flow

    # -- Idempotency RE-CHECK after the blocking locks -------------------
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(session, committed, fingerprint)

    # -- Station under its row lock --------------------------------------
    station = session.get(ScanStation, station_id, with_for_update=True)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was reversed."
        )

    # -- Eligibility, authoritative under the locks ----------------------
    reason = _ineligibility(session, station, rows)
    if reason is not None:
        raise ConflictError(f"{reason} Nothing was reversed.")

    # -- The restoration plan (reads only, before any write) -------------
    plan = _plan_restoration(session, rows, flows)
    restored_by_flow = {item.quantity_flow_id: item for item in plan.flows}

    # Machine deltas: quantity leaving the Machine a flow is currently
    # on, quantity returning to the Machine a flow is restored onto.
    deltas: dict[int, int] = {}
    for flow_id, flow in flows.items():
        if flow.current_machine_id is not None:
            deltas[flow.current_machine_id] = deltas.get(flow.current_machine_id, 0) - flow.quantity
        restored_machine = restored_by_flow[flow_id].current_machine_id
        if restored_machine is not None:
            deltas[restored_machine] = deltas.get(restored_machine, 0) + flow.quantity
    machines: dict[int, Machine] = {}
    assigned_before: dict[int, int] = {}
    for machine_id in sorted(deltas):
        machine = lock_machine(session, machine_id)
        if machine is None:  # pragma: no cover - FK guarantees the row
            raise ConflictError(f"Machine {machine_id} does not exist.")
        machines[machine_id] = machine
        assigned_before[machine_id] = assigned_quantity(session, machine_id)
    for item in plan.flows:
        if item.current_machine_id is not None:
            machine = machines[item.current_machine_id]
            if machine.retired_on is not None:
                raise ConflictError(
                    f"Machine '{machine.name}' was retired since. The reversal would"
                    " return quantity to it and cannot proceed. Nothing was reversed."
                )

    # Restored target Areas, locked like a transfer destination and
    # judged on the locked re-read: an Area deactivated since cannot
    # silently receive restored active quantity.
    for area_id in sorted(
        {item.current_area_id for item in plan.flows if item.current_area_id is not None}
    ):
        area = session.get(Area, area_id, with_for_update=True, populate_existing=True)
        if area is None:  # pragma: no cover - FK guarantees the row
            raise ConflictError(f"Area {area_id} does not exist.")
        if not area.is_active:
            raise ConflictError(
                f"Area '{area.name}' was deactivated since. The reversal would return"
                " quantity to it and cannot proceed. Reactivate the Area first."
                " Nothing was reversed."
            )

    # -- Writes — all inside the one open transaction --------------------
    metadata: dict[str, Any] = {
        **command_metadata("UNDO", fingerprint, size=len(rows)),
        UNDO_KEY: {
            "reverses_device_event_id": reverses_id,
            "kind": _command_kind(rows),
            "flows": [
                {
                    "quantity_flow_id": item.quantity_flow_id,
                    "quantity": item.quantity,
                    "status": item.status.value,
                    "current_area_id": item.current_area_id,
                    "current_machine_id": item.current_machine_id,
                }
                for item in plan.flows
            ],
        },
    }
    command: list[PartMovement] = []
    for sequence, original in enumerate(reversed(rows), start=1):
        command.append(
            PartMovement(
                quantity_flow_id=original.quantity_flow_id,
                part_number=pn,
                movement_type=MovementType.REVERSED,
                quantity=original.quantity,
                from_area_id=original.to_area_id,
                to_area_id=(
                    original.from_area_id
                    if original.movement_type == MovementType.TRANSFERRED
                    and original.from_area_id is not None
                    else original.to_area_id
                ),
                operation_id=original.operation_id,
                assigned_route_step_id=None,
                station_id=station.station_id,
                source_machine_id=None,
                destination_machine_id=None,
                reverses_movement_id=original.id,
                occurred_at=func.now(),
                server_received_at=func.now(),
                device_event_id=event_id,
                command_sequence=sequence,
                metadata_=metadata,
            )
        )
    session.add_all(command)
    # Projection restore in the same transaction, from exactly the
    # derivation the replay uses (SLICE1 §15).
    for item in plan.flows:
        flow = flows[item.quantity_flow_id]
        if item.status == QuantityFlowStatus.ACTIVE:
            flow.status = QuantityFlowStatus.ACTIVE
            flow.closed_at = None
            if item.current_area_id is not None:
                flow.current_area_id = item.current_area_id
            flow.current_machine_id = item.current_machine_id
        else:
            flow.status = QuantityFlowStatus.REVERSED
            flow.closed_at = func.now()
            flow.current_machine_id = None
        flow.updated_at = func.now()
    for machine_id, delta in deltas.items():
        note_assignment_change(
            machines[machine_id],
            assigned_before=assigned_before[machine_id],
            assigned_after=assigned_before[machine_id] + delta,
        )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        constraint = getattr(diagnostics, "constraint_name", None)
        if constraint == DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_command(session, event_id)
            if winner:
                return _replay_or_conflict(session, winner, fingerprint)
        if constraint == _REVERSES_UNIQUE_CONSTRAINT:
            # A concurrent Undo of the same command won at COMMIT:
            # nothing of this attempt persisted.
            raise ConflictError(
                "This action has already been reversed. Nothing was reversed."
            ) from exc
        raise
    return _result_from_command(session, command, created=True)
