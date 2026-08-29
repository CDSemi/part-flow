"""Current-position projection reconciliation (SLICE1_DATA_MODEL §15).

PartMovement history is the source of truth for production state;
`quantity_flows.current_area_id`, `quantity_flows.current_machine_id`
and — since Phase 8 — the flow lifecycle `quantity_flows.status` are
maintained projections for hot read paths. This module is the replay
procedure that rebuilds every projection value from Movement history
(plus the append-only lineage edges) alone, so reconciliation checks
and tests can assert the stored projection never drifts from history.
It reads only — it never writes production data.

Lineage-aware derivation (Phase 8): a `SPLIT` or `MERGED` Movement is
a lineage event, not a position change — the quantity it carries keeps
the Area, the Machine, the Operation and the holding state it had. So
the **position-bearing** Movement of a flow is its newest Movement that
is not a lineage event; a flow whose only Movement so far is the
lineage event that created it (a fresh split child, a merge result)
inherits that Movement from its parent(s) — the parent's newest
position-bearing Movement written BEFORE the child existed — recursively
up the lineage. Every read model and command derives the state from
that effective Movement (`effective_latest_movements`), never from the
lineage row itself, which is why a lineage row re-states neither the
Machine nor the state.
"""

from collections.abc import Iterable
from typing import Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.errors import ConflictError
from app.application.machines import areas_with_machines
from app.domain.enums import MovementType, ProcessingState
from app.infrastructure.models import PartMovement, QuantityFlowLineage

# The two Movement types that record descent instead of a position.
LINEAGE_MOVEMENT_TYPES: Final = (MovementType.SPLIT, MovementType.MERGED)


class CurrentPosition(NamedTuple):
    """The derived position of one ACTIVE flow: Area, optional Machine, state."""

    area_id: int
    machine_id: int | None
    processing_state: ProcessingState


def processing_state_of(movement_type: str, *, direct_processing: bool) -> ProcessingState:
    """The holding state a flow's effective LATEST Movement leaves it in (§12).

    Only an ``ASSIGNED_TO_MACHINE`` puts quantity on a Machine and only
    an ``AREA_COMPLETED`` finishes it; every other Movement — the
    arrival in an Area (``RECEIVED``, ``TRANSFERRED``) and the return
    from a Machine (``RELEASED_FROM_MACHINE``) — leaves it held by the
    Area: QUEUED in an Area with Machines (QUEUE_AND_ASSIGN), PROCESSING
    in an Area without Machines, which directly owns and processes the
    quantity (Phase 7; ``direct_processing`` is that Area mode, judged
    from the Area's active Machines — never configured). A NULL
    Machine alone never means queued: finished and directly processing
    quantity have no Machine either. A lineage event (``SPLIT``,
    ``MERGED``) never reaches this function: the caller resolves the
    position-bearing Movement first (`effective_latest_movements`).
    """
    if movement_type in LINEAGE_MOVEMENT_TYPES:  # pragma: no cover - caller contract
        raise ConflictError(f"{movement_type} is a lineage event and carries no holding state.")
    if movement_type == MovementType.ASSIGNED_TO_MACHINE:
        return ProcessingState.ON_MACHINE
    if movement_type == MovementType.AREA_COMPLETED:
        return ProcessingState.READY_TO_TRANSFER
    return ProcessingState.PROCESSING if direct_processing else ProcessingState.QUEUED


def is_actively_processing(state: ProcessingState) -> bool:
    """Quantity a transfer completes implicitly (PROJECT_PROFILE §8.11):
    ON_MACHINE in a Machine Area, PROCESSING in an Area without Machines."""
    return state in (ProcessingState.ON_MACHINE, ProcessingState.PROCESSING)


# ---------------------------------------------------------------------------
# Newest Movements
# ---------------------------------------------------------------------------


def latest_movements(session: Session, flow_ids: Iterable[int] | None) -> dict[int, PartMovement]:
    """The newest Movement per flow, lineage events INCLUDED (unlocked).

    "Latest" is the highest Movement id — the append-only BIGSERIAL
    write order. ``None`` considers every flow. This is the row whose
    ``to_area_id`` is the flow's current Area; the holding state and
    the Machine come from `effective_latest_movements`.
    """
    wanted = None if flow_ids is None else list(flow_ids)
    if wanted is not None and not wanted:
        return {}
    latest = select(
        PartMovement.quantity_flow_id, func.max(PartMovement.id).label("movement_id")
    ).group_by(PartMovement.quantity_flow_id)
    if wanted is not None:
        latest = latest.where(PartMovement.quantity_flow_id.in_(wanted))
    newest = latest.subquery()
    rows = session.scalars(
        select(PartMovement).join(newest, newest.c.movement_id == PartMovement.id)
    )
    return {movement.quantity_flow_id: movement for movement in rows}


def _latest_position_bearing(
    session: Session, flow_id: int, before_movement_id: int | None
) -> PartMovement | None:
    query = (
        select(PartMovement)
        .where(
            PartMovement.quantity_flow_id == flow_id,
            PartMovement.movement_type.not_in(LINEAGE_MOVEMENT_TYPES),
        )
        .order_by(PartMovement.id.desc())
        .limit(1)
    )
    if before_movement_id is not None:
        query = query.where(PartMovement.id < before_movement_id)
    return session.scalar(query)


def _first_movement_id(session: Session, flow_id: int) -> int | None:
    return session.scalar(
        select(func.min(PartMovement.id)).where(PartMovement.quantity_flow_id == flow_id)
    )


def parent_flow_ids(session: Session, child_flow_id: int) -> list[int]:
    """The parents a flow descends from, ascending id (empty for a released flow)."""
    return list(
        session.scalars(
            select(QuantityFlowLineage.parent_flow_id)
            .where(QuantityFlowLineage.child_flow_id == child_flow_id)
            .order_by(QuantityFlowLineage.parent_flow_id)
        )
    )


def effective_latest_movement(session: Session, flow_id: int) -> PartMovement:
    """The newest POSITION-BEARING Movement the flow's state follows.

    The flow's own newest non-lineage Movement when it has one; else
    the flow was created by a lineage event and inherits its parent's
    position: the parent's newest non-lineage Movement written before
    the child's creating Movement, recursively. A merge validated that
    every source held the same position (`app.application.merges`), so
    the lowest parent id is a deterministic, equivalent choice. Every
    flow ultimately descends from a released flow whose first Movement
    is its ``RECEIVED``, so the walk always terminates on a Movement.
    """
    current = flow_id
    bound: int | None = None
    for _ in range(10_000):  # bounded defensively; lineage depth is tiny in practice
        movement = _latest_position_bearing(session, current, bound)
        if movement is not None:
            return movement
        parents = parent_flow_ids(session, current)
        if not parents:
            break
        bound = _first_movement_id(session, current)
        current = parents[0]
    raise ConflictError(f"Quantity Flow {flow_id} has no position-bearing Movement history.")


def effective_latest_movements(
    session: Session, flow_ids: Iterable[int]
) -> dict[int, PartMovement]:
    """`effective_latest_movement` for several flows (read models, unlocked)."""
    return {flow_id: effective_latest_movement(session, flow_id) for flow_id in set(flow_ids)}


# ---------------------------------------------------------------------------
# Lineage graph
# ---------------------------------------------------------------------------


def consumed_flow_ids(session: Session, flow_ids: Iterable[int] | None = None) -> set[int]:
    """The flows history says were consumed — every parent of a lineage edge.

    The stored lifecycle (`quantity_flows.status` SPLIT / MERGED) must
    agree with this set: a flow is closed exactly when it was consumed.
    """
    query = select(QuantityFlowLineage.parent_flow_id).distinct()
    if flow_ids is not None:
        wanted = list(flow_ids)
        if not wanted:
            return set()
        query = query.where(QuantityFlowLineage.parent_flow_id.in_(wanted))
    return {int(flow_id) for flow_id in session.scalars(query)}


def origin_flow_ids(session: Session, flow_id: int) -> set[int]:
    """The released flows (no parents) a flow ultimately descends from.

    A split child has exactly one origin; a merge result may have
    several — one per consumed ancestry line.
    """
    origins: set[int] = set()
    frontier = [flow_id]
    seen: set[int] = set()
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        parents = parent_flow_ids(session, current)
        if parents:
            frontier.extend(parents)
        else:
            origins.add(current)
    return origins


# ---------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------


def rebuild_current_positions(session: Session) -> dict[int, CurrentPosition]:
    """Rebuild each ACTIVE flow's current position from history alone.

    The projection is defined by the flow's Movements and lineage
    (SLICE1 §15): the Area is the ``to_area_id`` of the flow's newest
    Movement (a lineage event stays in the Area it happened in); the
    Machine is the ``destination_machine_id`` of the flow's effective
    newest position-bearing Movement, which is set exactly on an
    ``ASSIGNED_TO_MACHINE`` (shape CHECK) — so a release, a completion
    and a transfer all clear it and a lineage event keeps it; the
    holding state follows from that Movement's type and the Area's
    current mode (Machines or not). A flow consumed by a SPLIT or a
    MERGED (a parent in the lineage graph, `consumed_flow_ids`) is
    closed and therefore absent — it is never active inventory. Every
    other flow appears: a QuantityFlow's first Movement is always its
    ``RECEIVED`` or the lineage event that created it.
    """
    latest = latest_movements(session, None)
    consumed = consumed_flow_ids(session)
    active_ids = [flow_id for flow_id in latest if flow_id not in consumed]
    effective = effective_latest_movements(session, active_ids)
    # The Area mode is part of the derivation (§12): the same arrival
    # Movement is QUEUED in a Machine Area and PROCESSING in an Area
    # without Machines.
    machine_areas = areas_with_machines(
        session, {latest[flow_id].to_area_id for flow_id in active_ids}
    )
    return {
        flow_id: CurrentPosition(
            area_id=latest[flow_id].to_area_id,
            machine_id=effective[flow_id].destination_machine_id,
            processing_state=processing_state_of(
                effective[flow_id].movement_type,
                direct_processing=latest[flow_id].to_area_id not in machine_areas,
            ),
        )
        for flow_id in active_ids
    }


def rebuild_current_area_ids(session: Session) -> dict[int, int]:
    """The Area part of the projection alone (kept for the Phase 4/5 replays)."""
    return {
        flow_id: position.area_id
        for flow_id, position in rebuild_current_positions(session).items()
    }
