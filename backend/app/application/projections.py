"""Current-position projection reconciliation (SLICE1_DATA_MODEL §15).

PartMovement history is the source of truth for production state;
`quantity_flows.current_area_id` is a maintained projection for hot
read paths. This module is the replay procedure that rebuilds every
projection value from Movement history alone, so reconciliation checks
and tests can assert the stored projection never drifts from history.
It reads only — it never writes production data.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.models import PartMovement


def rebuild_current_area_ids(session: Session) -> dict[int, int]:
    """Rebuild each flow's current Area from Movement history alone.

    The projection value is defined as the ``to_area_id`` of the flow's
    latest Movement (SLICE1 §15); "latest" is the highest Movement id —
    the append-only BIGSERIAL write order. Every flow appears: a
    QuantityFlow's first Movement is always its ``RECEIVED``.
    """
    latest = (
        select(
            PartMovement.quantity_flow_id,
            func.max(PartMovement.id).label("movement_id"),
        )
        .group_by(PartMovement.quantity_flow_id)
        .subquery()
    )
    rows = session.execute(
        select(PartMovement.quantity_flow_id, PartMovement.to_area_id).join(
            latest, latest.c.movement_id == PartMovement.id
        )
    )
    return {quantity_flow_id: to_area_id for quantity_flow_id, to_area_id in rows}
