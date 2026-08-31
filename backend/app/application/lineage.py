"""Quantity lineage staging — SPLIT and MERGED (Phase 8 — IMPLEMENTATION_ROADMAP).

The in-transaction building blocks the production commands use to
split one QuantityFlow or merge several (PROJECT_PROFILE §8.7, §8.11,
§11 Quantity Splitting / Quantity Merging / Quantity Integrity). Nothing
here commits or locks: the calling command holds the flow row lock(s)
and the station lock, has validated the action, and commits the staged
rows together with its own action Movement as ONE application command
under ONE `device_event_id`.

What a SPLIT records (`stage_split`):

- the source flow closes — `status = SPLIT`, `closed_at` set, the
  Machine projection cleared — and is never active inventory again;
- two child flows are created with the source's PN, the source's
  current Area, Machine and route mode, and — for a PLANNED source —
  their OWN AssignedRoute snapshot copies (the snapshot is one-to-one
  with its flow; two active flows never share one) positioned at the
  source's last known route step;
- three immutable `SPLIT` Movements under the command's
  `device_event_id`: on the source (its whole quantity — the
  consumption, `command_sequence` 1), on the selected child (2) and on
  the remainder child (3); child quantities sum exactly to the source
  quantity, every row stays in ONE Area at the Station and references
  no Machine, and the Operation is carried forward from the source's
  effective latest Movement;
- one `quantity_flow_lineage` edge per child (source → child, SPLIT).

The requested action then applies to the SELECTED child only (sequence
4 onward); the remainder keeps exactly the state the source had —
derived by following its lineage to the source's last position-bearing
Movement (`app.application.projections`). Quantity is conserved by
construction and re-verified in tests: Σ children = source.

What a MERGE records (`stage_merge`):

- every source flow closes (`status = MERGED`) with one `MERGED`
  Movement each (sequence 1..N, ascending flow id; the consumption);
- one resulting flow with the sum of the sources' quantities, the
  shared PN, Area, Machine and route mode (a PLANNED result gets its
  own snapshot copy at the shared route position) and one `MERGED`
  Movement (sequence N + 1; the descent);
- one lineage edge per source (source → result, MERGED).

Compatibility of the sources is the caller's rule (`app.application.merges`);
`route_context` below is the route part of it.
"""

import datetime
from typing import Any, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.common import flush
from app.application.projections import reversed_movement_ids
from app.domain.enums import LineageRelation, MovementType, QuantityFlowStatus, RouteMode
from app.infrastructure.models import (
    AssignedRoute,
    AssignedRouteStep,
    PartMovement,
    QuantityFlow,
    QuantityFlowLineage,
)


class RouteContext(NamedTuple):
    """The comparable route position of one flow (merge compatibility)."""

    route_mode: str
    # Every snapshot step as (sequence, area, operation, duration,
    # instructions) — empty for FLOATING.
    steps: tuple[tuple[int, int, int | None, datetime.timedelta | None, str | None], ...]
    # The sequence of the last snapshot step a Movement of the flow
    # fulfilled (None for FLOATING).
    last_known_sequence: int | None


def snapshot_steps(session: Session, assigned_route_id: int) -> list[AssignedRouteStep]:
    return list(
        session.scalars(
            select(AssignedRouteStep)
            .where(AssignedRouteStep.assigned_route_id == assigned_route_id)
            .order_by(AssignedRouteStep.sequence)
        )
    )


def last_known_step_id(session: Session, flow_id: int) -> int | None:
    """The snapshot step the flow's newest step-referencing Movement fulfilled.

    Reversed Movements do not count (Phase 9): an undone transfer never
    happened for the route position — the reversal restores the
    expectation the flow had before it.
    """
    return session.scalar(
        select(PartMovement.assigned_route_step_id)
        .where(
            PartMovement.quantity_flow_id == flow_id,
            PartMovement.assigned_route_step_id.is_not(None),
            PartMovement.id.not_in(reversed_movement_ids(flow_id)),
        )
        .order_by(PartMovement.id.desc())
        .limit(1)
    )


def route_context(session: Session, flow: QuantityFlow) -> RouteContext:
    if flow.route_mode != RouteMode.PLANNED or flow.assigned_route_id is None:
        return RouteContext(str(flow.route_mode), (), None)
    steps = snapshot_steps(session, flow.assigned_route_id)
    known = last_known_step_id(session, flow.id)
    last_sequence = next((step.sequence for step in steps if step.id == known), None)
    return RouteContext(
        str(flow.route_mode),
        tuple(
            (
                step.sequence,
                step.area_id,
                step.operation_id,
                step.expected_duration,
                step.instructions,
            )
            for step in steps
        ),
        last_sequence,
    )


class SnapshotCopy(NamedTuple):
    route: AssignedRoute
    # The copied steps by sequence.
    steps: dict[int, AssignedRouteStep]


def copy_assigned_route(
    session: Session, assigned_route_id: int, *, source_route_template_id: int | None
) -> SnapshotCopy:
    """An independent copy of a flow's snapshot for a new flow (flushed).

    The provenance recorded on the copy is the caller's: a split child
    carries its source's `source_route_template_id`; a merge result
    carries it only when every source snapshot agrees (`shared_provenance`),
    else NULL — never one source's provenance picked arbitrarily. The
    copy is otherwise as independent of the original as the original is
    of its template (PROJECT_PROFILE §8.10).
    """
    original = session.get(AssignedRoute, assigned_route_id)
    if original is None:  # pragma: no cover - FK guarantees the row
        raise ValueError(f"AssignedRoute {assigned_route_id} does not exist.")
    route = AssignedRoute(source_route_template_id=source_route_template_id)
    session.add(route)
    flush(session, {})
    steps = [
        AssignedRouteStep(
            assigned_route_id=route.id,
            sequence=step.sequence,
            area_id=step.area_id,
            operation_id=step.operation_id,
            expected_duration=step.expected_duration,
            instructions=step.instructions,
        )
        for step in snapshot_steps(session, assigned_route_id)
    ]
    session.add_all(steps)
    flush(session, {})
    return SnapshotCopy(route, {step.sequence: step for step in steps})


def _lineage_movement(
    *,
    flow: QuantityFlow,
    movement_type: MovementType,
    quantity: int,
    area_id: int,
    operation_id: int,
    assigned_route_step_id: int | None,
    station_id: str,
    event_id: str,
    sequence: int,
    metadata: dict[str, Any],
) -> PartMovement:
    return PartMovement(
        quantity_flow_id=flow.id,
        part_number=flow.part_number,
        movement_type=movement_type,
        quantity=quantity,
        from_area_id=area_id,
        to_area_id=area_id,
        operation_id=operation_id,
        assigned_route_step_id=assigned_route_step_id,
        station_id=station_id,
        source_machine_id=None,
        destination_machine_id=None,
        occurred_at=func.now(),
        server_received_at=func.now(),
        device_event_id=event_id,
        command_sequence=sequence,
        metadata_=metadata,
    )


def _close(flow: QuantityFlow, status: QuantityFlowStatus) -> None:
    flow.status = status
    flow.closed_at = func.now()
    flow.current_machine_id = None
    flow.updated_at = func.now()


def _descendant(source: QuantityFlow, quantity: int, assigned_route_id: int | None) -> QuantityFlow:
    return QuantityFlow(
        part_number=source.part_number,
        quantity=quantity,
        status=QuantityFlowStatus.ACTIVE,
        route_mode=source.route_mode,
        assigned_route_id=assigned_route_id,
        current_area_id=source.current_area_id,
        current_machine_id=source.current_machine_id,
    )


def shared_provenance(session: Session, flows: list[QuantityFlow]) -> int | None:
    """The one `source_route_template_id` every PLANNED flow's snapshot carries, else None.

    Structurally equal snapshots may descend from different Route
    Templates (or from none); a merge result then records no
    provenance rather than guessing one.
    """
    route_ids = {flow.assigned_route_id for flow in flows if flow.assigned_route_id is not None}
    if not route_ids:
        return None
    provenance = {
        session.get_one(AssignedRoute, route_id).source_route_template_id for route_id in route_ids
    }
    return next(iter(provenance)) if len(provenance) == 1 else None


def _child_snapshot(
    session: Session,
    source: QuantityFlow,
    known_step_id: int | None,
    *,
    source_route_template_id: int | None = None,
    inherit_provenance: bool = True,
) -> tuple[int | None, dict[int, AssignedRouteStep], int | None]:
    """(copied route id, copied steps by sequence, copied last-known step id).

    By default the copy inherits the source snapshot's provenance (a
    split child); a merge passes the sources' shared provenance instead.
    """
    if source.assigned_route_id is None:
        return None, {}, None
    if inherit_provenance:
        source_route_template_id = session.get_one(
            AssignedRoute, source.assigned_route_id
        ).source_route_template_id
    copy = copy_assigned_route(
        session, source.assigned_route_id, source_route_template_id=source_route_template_id
    )
    known_sequence = next(
        (
            step.sequence
            for step in snapshot_steps(session, source.assigned_route_id)
            if step.id == known_step_id
        ),
        None,
    )
    known_copy = copy.steps.get(known_sequence) if known_sequence is not None else None
    return copy.route.id, copy.steps, known_copy.id if known_copy is not None else None


# ---------------------------------------------------------------------------
# SPLIT
# ---------------------------------------------------------------------------


class SplitStaging(NamedTuple):
    source: QuantityFlow
    selected: QuantityFlow
    remainder: QuantityFlow
    # The three SPLIT Movements in command order (sequence 1, 2, 3).
    movements: list[PartMovement]
    # The selected child's snapshot steps by sequence ({} for FLOATING):
    # the action's route references must point into the CHILD's snapshot.
    selected_steps: dict[int, AssignedRouteStep]
    # The command_sequence the action Movement continues with.
    next_sequence: int


def stage_split(
    session: Session,
    *,
    source: QuantityFlow,
    selected_quantity: int,
    operation_id: int,
    station_id: str,
    event_id: str,
    metadata: dict[str, Any],
) -> SplitStaging:
    """Stage the split of ``source`` into a selected child and a remainder.

    Precondition (the caller's, under the source row lock): the source
    is ACTIVE and ``0 < selected_quantity < source.quantity``. The
    child flows and their snapshot copies are flushed (their ids are
    needed); the Movements are returned UNSTAGED so the caller adds
    them in command order together with its own action Movement —
    nothing of this command is inserted into `part_movements` before
    the caller decides to.
    """
    if not 0 < selected_quantity < source.quantity:  # pragma: no cover - caller contract
        raise ValueError("A split needs a selected quantity strictly inside the source.")
    area_id = source.current_area_id
    known_step_id = last_known_step_id(session, source.id)
    selected_route, selected_steps, selected_known = _child_snapshot(session, source, known_step_id)
    remainder_route, _, remainder_known = _child_snapshot(session, source, known_step_id)
    selected = _descendant(source, selected_quantity, selected_route)
    remainder = _descendant(source, source.quantity - selected_quantity, remainder_route)
    session.add_all([selected, remainder])
    flush(session, {})
    _close(source, QuantityFlowStatus.SPLIT)
    session.add_all(
        [
            QuantityFlowLineage(
                relation=LineageRelation.SPLIT,
                parent_flow_id=source.id,
                child_flow_id=child.id,
                device_event_id=event_id,
            )
            for child in (selected, remainder)
        ]
    )
    common: dict[str, Any] = {
        "area_id": area_id,
        "operation_id": operation_id,
        "station_id": station_id,
        "event_id": event_id,
        "metadata": metadata,
    }
    movements = [
        _lineage_movement(
            flow=source,
            movement_type=MovementType.SPLIT,
            quantity=source.quantity,
            assigned_route_step_id=None,
            sequence=1,
            **common,
        ),
        _lineage_movement(
            flow=selected,
            movement_type=MovementType.SPLIT,
            quantity=selected.quantity,
            assigned_route_step_id=selected_known,
            sequence=2,
            **common,
        ),
        _lineage_movement(
            flow=remainder,
            movement_type=MovementType.SPLIT,
            quantity=remainder.quantity,
            assigned_route_step_id=remainder_known,
            sequence=3,
            **common,
        ),
    ]
    return SplitStaging(source, selected, remainder, movements, selected_steps, 4)


class SplitRecord(NamedTuple):
    """A committed split, read back from the command's SPLIT rows."""

    source_quantity_flow_id: int
    selected_quantity_flow_id: int
    remainder_quantity_flow_id: int
    remainder_quantity: int


def split_prefix(command: list[PartMovement]) -> tuple[SplitRecord | None, list[PartMovement]]:
    """Separate a command's leading SPLIT rows from its action rows.

    A partial-quantity command is exactly: SPLIT on the source, SPLIT
    on the selected child, SPLIT on the remainder, then the action
    rows on the selected child. Anything else with leading SPLIT rows
    is not a well-formed command of this kind (None, unchanged rows).
    """
    if (
        len(command) < 4
        or any(row.movement_type != MovementType.SPLIT for row in command[:3])
        or command[3].movement_type == MovementType.SPLIT
    ):
        return None, command
    source, selected, remainder = command[:3]
    action = command[3:]
    if (
        selected.quantity_flow_id != action[0].quantity_flow_id
        or selected.quantity + remainder.quantity != source.quantity
    ):
        return None, command
    return (
        SplitRecord(
            source_quantity_flow_id=source.quantity_flow_id,
            selected_quantity_flow_id=selected.quantity_flow_id,
            remainder_quantity_flow_id=remainder.quantity_flow_id,
            remainder_quantity=remainder.quantity,
        ),
        action,
    )


# ---------------------------------------------------------------------------
# MERGED
# ---------------------------------------------------------------------------


class MergeStaging(NamedTuple):
    result: QuantityFlow
    # The MERGED Movements in command order: one per source (sequence
    # 1..N, ascending flow id) then the result's (N + 1).
    movements: list[PartMovement]


def stage_merge(
    session: Session,
    *,
    sources: list[QuantityFlow],
    operation_id: int,
    station_id: str,
    event_id: str,
    metadata: dict[str, Any],
) -> MergeStaging:
    """Stage the merge of ``sources`` (validated compatible, locked) into one flow.

    The result inherits the shared PN, Area, Machine and route mode;
    a PLANNED result gets its own snapshot copy of the first source's
    snapshot (every source's snapshot was validated structurally equal)
    at the shared last-known step, with the provenance every source
    shares — or none when the sources descend from different Route
    Templates. Movements are returned unstaged (see `stage_split`).
    """
    ordered = sorted(sources, key=lambda flow: flow.id)
    first = ordered[0]
    area_id = first.current_area_id
    route_id, _, known = _child_snapshot(
        session,
        first,
        last_known_step_id(session, first.id),
        source_route_template_id=shared_provenance(session, ordered),
        inherit_provenance=False,
    )
    result = _descendant(first, sum(flow.quantity for flow in ordered), route_id)
    session.add(result)
    flush(session, {})
    for source in ordered:
        _close(source, QuantityFlowStatus.MERGED)
    session.add_all(
        [
            QuantityFlowLineage(
                relation=LineageRelation.MERGED,
                parent_flow_id=source.id,
                child_flow_id=result.id,
                device_event_id=event_id,
            )
            for source in ordered
        ]
    )
    common: dict[str, Any] = {
        "area_id": area_id,
        "operation_id": operation_id,
        "station_id": station_id,
        "event_id": event_id,
        "metadata": metadata,
    }
    movements = [
        _lineage_movement(
            flow=source,
            movement_type=MovementType.MERGED,
            quantity=source.quantity,
            assigned_route_step_id=None,
            sequence=index,
            **common,
        )
        for index, source in enumerate(ordered, start=1)
    ]
    movements.append(
        _lineage_movement(
            flow=result,
            movement_type=MovementType.MERGED,
            quantity=result.quantity,
            assigned_route_step_id=known,
            sequence=len(ordered) + 1,
            **common,
        )
    )
    return MergeStaging(result, movements)
