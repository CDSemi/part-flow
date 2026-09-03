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

Reversal-aware derivation (Phase 9, PROJECT_PROFILE §16): a
command-level Undo appends one compensating `REVERSED` Movement per
original Movement (`reverses_movement_id`) and never touches the
originals. The restored state is derived by EXCLUDING the reversed
pair — every `REVERSED` row and every Movement one references — from
the derivations: the effective position-bearing Movement of a flow
whose newest command was undone is simply the Movement before that
command, which restores the Area, the Machine, the Operation, the
route position and the holding state at once, also through a lineage
walk (an undone SPLIT/MERGED reopens its sources — their lineage
edges no longer count, `consumed_flow_ids`). A `SCRAPPED` closes its
flow, so it never bears an active position either. Nothing here reads
state off a `REVERSED` row beyond its `to_area_id`, which by
construction of the compensating motion is the restored Area.

Stockroom (Phase 10, PROJECT_PROFILE §18): a `STOCKED` Movement ends
the flow's active life — the quantity is manufacturing-complete in the
terminal Area and never active inventory again — so a flow whose
newest effective Movement is its `STOCKED` is absent from the replay
exactly like a scrapped one; the stocked total of a PN
(`stocked_quantity_of`) is the `stocked` term of the §11
reconciliation and the quantity Work Order Allocation draws from.
"""

from collections.abc import Iterable
from typing import Final, NamedTuple

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, aliased

from app.application.errors import ConflictError
from app.application.machines import areas_with_machines
from app.domain.enums import MovementType, ProcessingState
from app.infrastructure.models import PartMovement, QuantityFlowLineage

# The two Movement types that record descent instead of a position.
LINEAGE_MOVEMENT_TYPES: Final = (MovementType.SPLIT, MovementType.MERGED)

# Movement types that never bear an active position: descent (Phase 8),
# the removal from active production, the compensating reversal
# (Phase 9) and the Stockroom completion (Phase 10) — a flow whose
# newest effective Movement is one of these is closed, or derives its
# position from an earlier Movement.
NON_POSITION_BEARING_TYPES: Final = (
    MovementType.SPLIT,
    MovementType.MERGED,
    MovementType.SCRAPPED,
    MovementType.REVERSED,
    MovementType.STOCKED,
)

# The two closures that end a flow's active life for good: its newest
# effective Movement being one of these makes it inactive inventory.
CLOSING_MOVEMENT_TYPES: Final = (MovementType.SCRAPPED, MovementType.STOCKED)


def reversed_movement_ids(flow_id: int) -> Select[tuple[int | None]]:
    """The Movements of one flow undone by a ``REVERSED`` row (subquery).

    A compensating row always belongs to the same flow as its original,
    so the per-flow filter is complete. Used with ``.not_in`` to
    exclude the undone originals from every derivation.
    """
    return select(PartMovement.reverses_movement_id).where(
        PartMovement.quantity_flow_id == flow_id,
        PartMovement.reverses_movement_id.is_not(None),
    )


class CurrentPosition(NamedTuple):
    """The derived position of one ACTIVE flow: Area, optional Machine, state."""

    area_id: int
    machine_id: int | None
    processing_state: ProcessingState


def processing_state_of(movement_type: str, *, direct_processing: bool) -> ProcessingState:
    """The holding state a flow's effective LATEST Movement leaves it in (§12).

    Only an ``ASSIGNED_TO_MACHINE`` puts quantity on a Machine and only
    an ``AREA_COMPLETED`` finishes it; every other Movement — the
    arrival in an Area (``RECEIVED``, ``TRANSFERRED``, and since
    Phase 9 the ``QUANTITY_ADJUSTED`` addition, which arrives exactly
    like a release) and the return
    from a Machine (``RELEASED_FROM_MACHINE``) — leaves it held by the
    Area: QUEUED in an Area with Machines (QUEUE_AND_ASSIGN), PROCESSING
    in an Area without Machines, which directly owns and processes the
    quantity (Phase 7; ``direct_processing`` is that Area mode, judged
    from the Area's active Machines — never configured). A NULL
    Machine alone never means queued: finished and directly processing
    quantity have no Machine either. A lineage event (``SPLIT``,
    ``MERGED``), a ``SCRAPPED`` and a ``REVERSED`` (Phase 9) never
    reach this function: the caller resolves the
    position-bearing Movement first (`effective_latest_movements`).
    """
    if movement_type in NON_POSITION_BEARING_TYPES:  # pragma: no cover - caller contract
        raise ConflictError(f"{movement_type} carries no holding state to derive.")
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
    """The newest EFFECTIVE Movement per flow, lineage events INCLUDED (unlocked).

    "Latest" is the highest Movement id — the append-only BIGSERIAL
    write order — after excluding every ``REVERSED`` row and every
    Movement one reverses (Phase 9: an undone command never bears
    state; a ``REVERSED`` row's own ``to_area_id`` IS the restored
    Area, but the Movement before the undone command states the same
    Area, so exclusion keeps one uniform rule). ``None`` considers
    every flow. A flow whose every Movement was reversed (its creating
    command was undone) is absent — it is never active inventory. This
    is the row whose ``to_area_id`` is the flow's current Area; the
    holding state and the Machine come from
    `effective_latest_movements`.
    """
    wanted = None if flow_ids is None else list(flow_ids)
    if wanted is not None and not wanted:
        return {}
    reversal = aliased(PartMovement)
    latest = (
        select(PartMovement.quantity_flow_id, func.max(PartMovement.id).label("movement_id"))
        .where(
            PartMovement.movement_type != MovementType.REVERSED,
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
        .group_by(PartMovement.quantity_flow_id)
    )
    if wanted is not None:
        latest = latest.where(PartMovement.quantity_flow_id.in_(wanted))
    newest = latest.subquery()
    rows = session.scalars(
        select(PartMovement).join(newest, newest.c.movement_id == PartMovement.id)
    )
    return {movement.quantity_flow_id: movement for movement in rows}


def _latest_position_bearing(
    session: Session,
    flow_id: int,
    before_movement_id: int | None,
    exclude_device_event_id: str | None,
) -> PartMovement | None:
    query = (
        select(PartMovement)
        .where(
            PartMovement.quantity_flow_id == flow_id,
            PartMovement.movement_type.not_in(NON_POSITION_BEARING_TYPES),
            PartMovement.id.not_in(reversed_movement_ids(flow_id)),
        )
        .order_by(PartMovement.id.desc())
        .limit(1)
    )
    if before_movement_id is not None:
        query = query.where(PartMovement.id < before_movement_id)
    if exclude_device_event_id is not None:
        query = query.where(PartMovement.device_event_id != exclude_device_event_id)
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


def effective_latest_movement(
    session: Session, flow_id: int, *, exclude_device_event_id: str | None = None
) -> PartMovement:
    """The newest POSITION-BEARING Movement the flow's state follows.

    The flow's own newest non-lineage, non-reversed Movement when it
    has one; else
    the flow was created by a lineage event and inherits its parent's
    position: the parent's newest non-lineage Movement written before
    the child's creating Movement, recursively. A merge validated that
    every source held the same position (`app.application.merges`), so
    the lowest parent id is a deterministic, equivalent choice. Every
    flow ultimately descends from a released flow whose first Movement
    is its ``RECEIVED`` (or, Phase 9, a ``QUANTITY_ADJUSTED``
    addition), so the walk always terminates on a Movement — except
    for a flow whose whole history was reversed, which is closed and
    never derived. ``exclude_device_event_id`` additionally ignores
    one command's rows: the Undo command uses it to derive the state a
    reversal restores BEFORE its compensating rows exist.
    """
    current = flow_id
    bound: int | None = None
    for _ in range(10_000):  # bounded defensively; lineage depth is tiny in practice
        movement = _latest_position_bearing(session, current, bound, exclude_device_event_id)
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
    """`effective_latest_movement` for several flows (read models, unlocked).

    The same derivation, in one grouped query for every flow whose OWN
    history holds a position-bearing, non-reversed Movement (the
    common case); only a flow that has none — created by a lineage
    event and never moved since — takes the per-flow lineage walk.
    """
    wanted = set(flow_ids)
    if not wanted:
        return {}
    reversal = aliased(PartMovement)
    newest = (
        select(PartMovement.quantity_flow_id, func.max(PartMovement.id).label("movement_id"))
        .where(
            PartMovement.quantity_flow_id.in_(wanted),
            PartMovement.movement_type.not_in(NON_POSITION_BEARING_TYPES),
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
        .group_by(PartMovement.quantity_flow_id)
        .subquery()
    )
    found = {
        movement.quantity_flow_id: movement
        for movement in session.scalars(
            select(PartMovement).join(newest, newest.c.movement_id == PartMovement.id)
        )
    }
    for flow_id in wanted - found.keys():
        found[flow_id] = effective_latest_movement(session, flow_id)
    return found


# ---------------------------------------------------------------------------
# Lineage graph
# ---------------------------------------------------------------------------


def _reversed_command_ids() -> Select[tuple[str]]:
    """The ``device_event_id`` of every command that was undone (subquery).

    A command is undone exactly when one of its Movements is referenced
    by a ``REVERSED`` row (an Undo always reverses the complete
    command, PROJECT_PROFILE §16). The append-only lineage edges of an
    undone SPLIT/MERGED stay stored but no longer count.
    """
    original = aliased(PartMovement)
    return (
        select(original.device_event_id)
        .join(PartMovement, PartMovement.reverses_movement_id == original.id)
        .distinct()
    )


def consumed_flow_ids(session: Session, flow_ids: Iterable[int] | None = None) -> set[int]:
    """The flows history says were consumed — every parent of an EFFECTIVE edge.

    An edge of an undone SPLIT/MERGED command (Phase 9) is void: its
    parent was reopened by the reversal and counts as consumed no
    longer. The stored lifecycle (`quantity_flows.status`) must
    agree with this set: a flow is closed as SPLIT / MERGED exactly
    when it is effectively consumed.
    """
    query = (
        select(QuantityFlowLineage.parent_flow_id)
        .where(QuantityFlowLineage.device_event_id.not_in(_reversed_command_ids()))
        .distinct()
    )
    if flow_ids is not None:
        wanted = list(flow_ids)
        if not wanted:
            return set()
        query = query.where(QuantityFlowLineage.parent_flow_id.in_(wanted))
    return {int(flow_id) for flow_id in session.scalars(query)}


def visited_area_ids(session: Session, flow_id: int) -> set[int]:
    """Every Area the flow's quantity has actually been in (Repair, §14).

    The ``to_area_id`` of every effective position-bearing Movement of
    the flow and of every lineage ancestor's Movements written before
    the descent — reversed history excluded: an undone transfer is not
    a visit. This is the set a Repair may return the quantity to.
    """
    visited: set[int] = set()
    frontier: list[tuple[int, int | None]] = [(flow_id, None)]
    seen: set[int] = set()
    while frontier:
        current, bound = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        query = select(PartMovement.to_area_id).where(
            PartMovement.quantity_flow_id == current,
            PartMovement.movement_type.not_in(NON_POSITION_BEARING_TYPES),
            PartMovement.id.not_in(reversed_movement_ids(current)),
        )
        if bound is not None:
            query = query.where(PartMovement.id < bound)
        visited.update(int(area_id) for area_id in session.scalars(query.distinct()))
        parent_bound = _first_movement_id(session, current)
        for parent in parent_flow_ids(session, current):
            frontier.append((parent, parent_bound))
    return visited


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
    effective Movement (a lineage event stays in the Area it happened
    in; reversed history never counts); the
    Machine is the ``destination_machine_id`` of the flow's effective
    newest position-bearing Movement, which is set exactly on an
    ``ASSIGNED_TO_MACHINE`` (shape CHECK) — so a release, a completion
    and a transfer all clear it and a lineage event keeps it; the
    holding state follows from that Movement's type and the Area's
    current mode (Machines or not). Absent — never active inventory —
    are: a flow consumed by an effective SPLIT or MERGED (a parent in
    the effective lineage graph, `consumed_flow_ids`), a flow whose
    newest effective Movement is its ``SCRAPPED`` (Phase 9 — its
    quantity left active production) or its ``STOCKED`` (Phase 10 —
    manufacturing-complete in the terminal Area), and a flow with no
    effective Movement at all (the command that created it was undone). Every
    other flow appears: a QuantityFlow's first Movement is always its
    ``RECEIVED``, a ``QUANTITY_ADJUSTED`` addition, or the lineage
    event that created it — and an undone Scrap or consumption leaves
    the flow exactly where its remaining effective history says.
    """
    latest = latest_movements(session, None)
    consumed = consumed_flow_ids(session)
    active_ids = [
        flow_id
        for flow_id, movement in latest.items()
        if flow_id not in consumed and movement.movement_type not in CLOSING_MOVEMENT_TYPES
    ]
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


def stocked_quantity_of(session: Session, part_number: str) -> int:
    """The PN's total stocked quantity (Phase 10, PROJECT_PROFILE §18).

    The sum of every effective ``STOCKED`` Movement of the PN — the
    `stocked` term of the §11 reconciliation `introduced = active +
    stocked + scrapped` and the gross quantity Work Order Allocation
    draws from. Reversed history is excluded for uniformity with every
    other derivation, although a ``STOCKED`` command is never undone
    (PROJECT_PROFILE §32 open decision 1).
    """
    reversal = aliased(PartMovement)
    total = session.scalar(
        select(func.coalesce(func.sum(PartMovement.quantity), 0)).where(
            PartMovement.part_number == part_number,
            PartMovement.movement_type == MovementType.STOCKED,
            ~select(reversal.id).where(reversal.reverses_movement_id == PartMovement.id).exists(),
        )
    )
    return int(total or 0)


def rebuild_current_area_ids(session: Session) -> dict[int, int]:
    """The Area part of the projection alone (kept for the Phase 4/5 replays)."""
    return {
        flow_id: position.area_id
        for flow_id, position in rebuild_current_positions(session).items()
    }
