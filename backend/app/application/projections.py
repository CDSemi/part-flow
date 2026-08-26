"""Current-position projection reconciliation (SLICE1_DATA_MODEL §15).

PartMovement history is the source of truth for production state;
`quantity_flows.current_area_id` and `quantity_flows.current_machine_id`
are maintained projections for hot read paths. This module is the
replay procedure that rebuilds every projection value from Movement
history alone, so reconciliation checks and tests can assert the stored
projection never drifts from history. It reads only — it never writes
production data.
"""

from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.machines import areas_with_machines
from app.domain.enums import MovementType, ProcessingState
from app.infrastructure.models import PartMovement


class CurrentPosition(NamedTuple):
    """The derived position of one flow: Area, optional Machine, state."""

    area_id: int
    machine_id: int | None
    processing_state: ProcessingState


def processing_state_of(movement_type: str, *, direct_processing: bool) -> ProcessingState:
    """The holding state a flow's LATEST Movement leaves it in (§12).

    Only an ``ASSIGNED_TO_MACHINE`` puts quantity on a Machine and only
    an ``AREA_COMPLETED`` finishes it; every other Movement — the
    arrival in an Area (``RECEIVED``, ``TRANSFERRED``) and the return
    from a Machine (``RELEASED_FROM_MACHINE``) — leaves it held by the
    Area: QUEUED in an Area with Machines (QUEUE_AND_ASSIGN), PROCESSING
    in an Area without Machines, which directly owns and processes the
    quantity (Phase 7; ``direct_processing`` is that Area mode, judged
    from the Area's active Machines — never configured). A NULL
    Machine alone never means queued: finished and directly processing
    quantity have no Machine either.
    """
    if movement_type == MovementType.ASSIGNED_TO_MACHINE:
        return ProcessingState.ON_MACHINE
    if movement_type == MovementType.AREA_COMPLETED:
        return ProcessingState.READY_TO_TRANSFER
    return ProcessingState.PROCESSING if direct_processing else ProcessingState.QUEUED


def is_actively_processing(state: ProcessingState) -> bool:
    """Quantity a transfer completes implicitly (PROJECT_PROFILE §8.11):
    ON_MACHINE in a Machine Area, PROCESSING in an Area without Machines."""
    return state in (ProcessingState.ON_MACHINE, ProcessingState.PROCESSING)


def _latest_movements(session: Session) -> list[PartMovement]:
    latest = (
        select(
            PartMovement.quantity_flow_id,
            func.max(PartMovement.id).label("movement_id"),
        )
        .group_by(PartMovement.quantity_flow_id)
        .subquery()
    )
    return list(
        session.scalars(select(PartMovement).join(latest, latest.c.movement_id == PartMovement.id))
    )


def rebuild_current_positions(session: Session) -> dict[int, CurrentPosition]:
    """Rebuild each flow's current position from Movement history alone.

    The projection is defined by the flow's latest Movement (SLICE1
    §15; "latest" is the highest Movement id — the append-only
    BIGSERIAL write order): the Area is its ``to_area_id``; the Machine
    is its ``destination_machine_id``, which is set exactly on an
    ``ASSIGNED_TO_MACHINE`` (shape CHECK) — so a release, a completion
    and a transfer all clear it; the holding state follows from the
    Movement type and the Area's current mode (Machines or not). Every
    flow appears: a QuantityFlow's first Movement is always its
    ``RECEIVED``.
    """
    latest = _latest_movements(session)
    # The Area mode is part of the derivation (§12): the same arrival
    # Movement is QUEUED in a Machine Area and PROCESSING in an Area
    # without Machines.
    machine_areas = areas_with_machines(session, {movement.to_area_id for movement in latest})
    return {
        movement.quantity_flow_id: CurrentPosition(
            area_id=movement.to_area_id,
            machine_id=movement.destination_machine_id,
            processing_state=processing_state_of(
                movement.movement_type,
                direct_processing=movement.to_area_id not in machine_areas,
            ),
        )
        for movement in latest
    }


def rebuild_current_area_ids(session: Session) -> dict[int, int]:
    """The Area part of the projection alone (kept for the Phase 4/5 replays)."""
    return {
        flow_id: position.area_id
        for flow_id, position in rebuild_current_positions(session).items()
    }
