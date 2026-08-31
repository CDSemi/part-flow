"""Explicit quantity merge — MERGED (Phase 8 — IMPLEMENTATION_ROADMAP).

The one Scan Station command that converges several QuantityFlows of
ONE PN into one resulting QuantityFlow (PROJECT_PROFILE §11 Quantity
Merging: "History must preserve how merged quantity arrived"), ONE
database transaction, idempotent per `device_event_id`, following the
Phase 6 command protocol (`app.application.machine_processing`).

Rules owned here:

- Never automatic: flows of one PN are merged only by an explicit
  command naming every source flow (at least two, distinct). Nothing
  is picked, ranked or combined by the system — the same PN in one
  Area stays separate quantities until an operator merges them.
- Compatibility is judged under the source row locks and is STRICT —
  a merge happens only when exactly one resulting state exists without
  guessing: every source is ACTIVE, carries the PN, sits in the
  station's Area (the station is bound to it), holds the same derived
  processing state, the same Machine (ON_MACHINE sources on one
  Machine; otherwise none), the same recorded Operation (its effective
  latest Movement), and the same route context (`lineage.route_context`
  — all FLOATING, or all PLANNED with structurally equal snapshots at
  the same last-known step). Any difference is refused explicitly with
  zero writes.
- Quantity conservation: the resulting flow's quantity is exactly the
  sum of the sources'; every source closes (`status = MERGED`,
  `closed_at`) inside the same transaction and is never active
  inventory again; a `MERGED` Movement is appended on every source
  (sequence 1..N, ascending flow id) and on the result (N + 1), all
  under the one `device_event_id`; one lineage edge per source records
  the ancestry (`quantity_flow_lineage`, N → 1).
- The result inherits the shared position: Area, Machine (the Machine
  row locked and re-read — its assigned total is unchanged by a merge),
  Operation, route mode and — for PLANNED — its own snapshot copy at
  the shared route position; its derived holding state equals the
  sources'.
- Idempotency exactly like the in-Area commands: the fingerprint
  (command kind, station, PN, the sorted source flow ids) is stored on
  every Movement; same id + same fingerprint replays the original
  result, a mismatch is an explicit conflict, a race lost at COMMIT
  replays the winner. Lock order: source flows ascending by id →
  station → Machine. The result is built from the immutable MERGED
  rows ALONE: the shared holding state and Machine the merge produced
  are recorded in the command's metadata (``merge`` block) at merge
  time, so a replay returns the identical original response whatever
  the resulting flow, the Machine or the Area's mode did since.
- The read models offer `Combine quantities` ONLY for flows this
  module judges compatible (`combinable_groups` — the same context key
  the command enforces, `merge_context`), so the frontend never carries
  a compatibility rule of its own; the command re-judges under the
  locks and remains the authority.
- Not here: SPLIT (partial-quantity actions split inside their own
  command, `machine_processing.split_if_partial`), Undo (Phase 9).
"""

import datetime
import hashlib
import json
from typing import Any, Final, NamedTuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.common import device_event_id_text
from app.application.errors import (
    ConflictError,
    IdempotencyConflictError,
    InvalidInputError,
    NotFoundError,
)
from app.application.lineage import RouteContext, route_context, stage_merge
from app.application.machine_processing import (
    FINGERPRINT_KEY,
    command_metadata,
    committed_command,
    no_longer_active,
)
from app.application.machines import area_has_machines, lock_machine
from app.application.part_numbers import canonical_part_number
from app.application.projections import effective_latest_movement, processing_state_of
from app.domain.enums import MovementType, ProcessingState, QuantityFlowStatus
from app.infrastructure.models import (
    DEVICE_EVENT_ID_CONSTRAINT,
    Area,
    PartMovement,
    QuantityFlow,
    ScanStation,
)

# Immutable record of the context the merge produced (the sources'
# shared state and Machine), written on every MERGED row of the command
# and read back verbatim on replay — never re-derived from current state.
MERGE_KEY: Final = "merge"


class MergeContext(NamedTuple):
    """The production context a merge needs to be identical across its sources.

    One value per flow; flows merge only when every value is equal —
    the resulting flow then has exactly this context, nothing guessed.
    """

    processing_state: ProcessingState
    machine_id: int | None
    operation_id: int
    route: RouteContext


def merge_context(
    session: Session, flow: QuantityFlow, latest: PartMovement, *, direct_processing: bool
) -> MergeContext:
    return MergeContext(
        processing_state=processing_state_of(
            latest.movement_type, direct_processing=direct_processing
        ),
        machine_id=flow.current_machine_id,
        operation_id=latest.operation_id,
        route=route_context(session, flow),
    )


def combinable_groups(
    session: Session,
    flows: list[QuantityFlow],
    latest: dict[int, PartMovement],
    *,
    direct_processing: bool,
) -> list[list[int]]:
    """The groups of at least two ACTIVE flows (one PN, one Area) that may merge.

    Read-model helper: every group is one identical `merge_context`;
    flows without a partner form no group. Ascending flow ids within a
    group, groups ordered by their first flow.
    """
    grouped: dict[MergeContext, list[int]] = {}
    for flow in sorted(flows, key=lambda item: item.id):
        key = merge_context(session, flow, latest[flow.id], direct_processing=direct_processing)
        grouped.setdefault(key, []).append(flow.id)
    return [ids for ids in grouped.values() if len(ids) >= 2]


class MergeResult(NamedTuple):
    """One committed merge, read from its immutable MERGED Movements."""

    movement_id: int
    quantity_flow_id: int
    part_number: str
    quantity: int
    area_id: int
    machine_id: int | None
    operation_id: int
    station_id: str
    processing_state: ProcessingState
    source_quantity_flow_ids: list[int]
    device_event_id: str
    occurred_at: datetime.datetime
    created: bool


def _validated_flow_ids(value: object) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) < 2
        or any(not isinstance(item, int) or isinstance(item, bool) for item in value)
    ):
        raise InvalidInputError("A merge names at least two Quantity Flows to merge.")
    ids = sorted({int(item) for item in value})
    if len(ids) != len(value):
        raise InvalidInputError("A merge names each Quantity Flow once.")
    return ids


def _request_fingerprint(*, station_id: str, part_number: str, flow_ids: list[int]) -> str:
    normalized = {
        "command": "MERGE",
        "station_id": station_id,
        "part_number": part_number,
        "quantity_flow_ids": flow_ids,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _result_from_command(command: list[PartMovement], *, created: bool) -> MergeResult:
    result_row = command[-1]
    sources = command[:-1]
    if result_row.from_area_id is None or result_row.station_id is None:
        raise IdempotencyConflictError(  # pragma: no cover - shape CHECK
            "This device_event_id belongs to a different kind of production"
            " event. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    # The context the merge produced, as recorded at merge time on the
    # immutable row — a replay never consults the resulting flow, the
    # Machine or the Area's current mode.
    recorded = (result_row.metadata_ or {}).get(MERGE_KEY)
    if not isinstance(recorded, dict) or "processing_state" not in recorded:
        raise IdempotencyConflictError(  # pragma: no cover - written by this module
            "This device_event_id belongs to a different kind of production"
            " event. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    return MergeResult(
        movement_id=result_row.id,
        quantity_flow_id=result_row.quantity_flow_id,
        part_number=result_row.part_number,
        quantity=result_row.quantity,
        area_id=result_row.to_area_id,
        machine_id=recorded.get("machine_id"),
        operation_id=result_row.operation_id,
        station_id=result_row.station_id,
        processing_state=ProcessingState(recorded["processing_state"]),
        source_quantity_flow_ids=[row.quantity_flow_id for row in sources],
        device_event_id=result_row.device_event_id,
        occurred_at=result_row.occurred_at,
        created=created,
    )


def _replay_or_conflict(command: list[PartMovement], fingerprint: str) -> MergeResult:
    stored = (command[-1].metadata_ or {}).get(FINGERPRINT_KEY)
    well_formed = (
        len(command) >= 3
        and all(row.movement_type == MovementType.MERGED for row in command)
        and all((row.metadata_ or {}).get(FINGERPRINT_KEY) == stored for row in command)
    )
    if stored != fingerprint or not well_formed:
        raise IdempotencyConflictError(
            "This device_event_id was already used for a different production"
            " request. Nothing was recorded — a new intent needs a new"
            " device_event_id."
        )
    return _result_from_command(command, created=False)


def _refuse(reason: str) -> ConflictError:
    return ConflictError(
        f"These Quantity Flows cannot be merged: {reason} Merge only quantities with"
        " one identical production context. Nothing was recorded."
    )


def merge_flows(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_ids: object,
    device_event_id: object,
) -> MergeResult:
    """Merge the named ACTIVE QuantityFlows of one PN into one, ONE transaction."""
    pn = canonical_part_number(part_number)
    flow_ids = _validated_flow_ids(quantity_flow_ids)
    event_id = device_event_id_text(device_event_id)
    fingerprint = _request_fingerprint(station_id=station_id, part_number=pn, flow_ids=flow_ids)
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(committed, fingerprint)

    # -- Source flows under their row locks, ascending id ----------------
    flows: list[QuantityFlow] = []
    for flow_id in flow_ids:
        flow = session.get(QuantityFlow, flow_id, with_for_update=True)
        if flow is None:
            raise InvalidInputError(f"Quantity Flow {flow_id} does not exist.")
        flows.append(flow)
    committed = committed_command(session, event_id)
    if committed:
        return _replay_or_conflict(committed, fingerprint)

    # -- Station under its row lock, bound to the flows' Area -----------
    station = session.get(ScanStation, station_id, with_for_update=True)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
            " Nothing was recorded."
        )
    area = session.get(Area, station.area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' is inactive and accepts no production use. Nothing was recorded."
        )

    # -- Compatibility: one resulting state, no guessing ----------------
    for flow in flows:
        if flow.part_number != pn:
            raise InvalidInputError(
                f"Part Number '{pn}' does not match Quantity Flow {flow.id}"
                f" ('{flow.part_number}'). A merge combines the scanned PN's own quantity."
            )
        if flow.status != QuantityFlowStatus.ACTIVE:
            raise ConflictError(no_longer_active(flow))
        if flow.current_area_id != area.id:
            raise _refuse(
                f"Quantity Flow {flow.id} is not in Area '{area.name}', the Area Scan"
                f" Station '{station_id}' is bound to."
            )
    direct = not area_has_machines(session, area.id)
    latest = {flow.id: effective_latest_movement(session, flow.id) for flow in flows}
    contexts = {
        flow.id: merge_context(session, flow, latest[flow.id], direct_processing=direct)
        for flow in flows
    }
    if len(set(contexts.values())) != 1:
        # The same key the read model groups by; the message names the
        # first component that differs.
        if len({context.processing_state for context in contexts.values()}) != 1:
            raise _refuse(
                "they are in different processing states ("
                + ", ".join(
                    f"{flow.id}: {contexts[flow.id].processing_state.value}" for flow in flows
                )
                + ")."
            )
        if len({context.machine_id for context in contexts.values()}) != 1:
            raise _refuse("they are not on the same Machine.")
        if len({context.operation_id for context in contexts.values()}) != 1:
            raise _refuse("they are recorded for different Operations.")
        raise _refuse(
            "their route context differs (route mode, Planned Route snapshot or route position)."
        )
    shared = next(iter(contexts.values()))
    state = shared.processing_state
    machine_id = shared.machine_id
    if (state == ProcessingState.ON_MACHINE) != (machine_id is not None):  # pragma: no cover
        raise ConflictError("The Machine projection disagrees with the derived state.")
    if machine_id is not None:
        machine = lock_machine(session, machine_id)
        if machine is None:  # pragma: no cover - FK guarantees the row
            raise ConflictError(f"Machine {machine_id} does not exist.")
        # The Machine row lock alone is needed: the assigned total is
        # unchanged by a merge, so the derived Machine state never moves.

    # -- Writes — all inside the one open transaction ------------------
    operation_id = shared.operation_id
    metadata: dict[str, Any] = {
        **command_metadata("MERGE", fingerprint, size=len(flows) + 1),
        MERGE_KEY: {"processing_state": state.value, "machine_id": machine_id},
    }
    staged = stage_merge(
        session,
        sources=flows,
        operation_id=operation_id,
        station_id=station.station_id,
        event_id=event_id,
        metadata=metadata,
    )
    session.add_all(staged.movements)
    staged.result.updated_at = func.now()
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        diagnostics = getattr(exc.orig, "diag", None)
        if getattr(diagnostics, "constraint_name", None) == DEVICE_EVENT_ID_CONSTRAINT:
            winner = committed_command(session, event_id)
            if winner:
                return _replay_or_conflict(winner, fingerprint)
        raise
    return _result_from_command(staged.movements, created=True)
