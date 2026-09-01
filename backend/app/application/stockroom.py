"""Stockroom completion — the `STOCKED` arrival (Phase 10, PROJECT_PROFILE §18).

The scan of quantity into the terminal Stockroom Area is the SAME
one-shot arrival command as the Scan Station transfer
(`app.application.transfers.record_arrival`), recorded as `STOCKED`
instead of `TRANSFERRED`: nothing about source selection, the confirmed
destination precondition, the lock order (flow → station → source
Machine → target Area → Operation), the route assessment and its
explicit deviation confirmation, partial quantity through the
in-command SPLIT (Phase 8 — never a parallel quantity mechanism), the
implicit `AREA_COMPLETED` of actively processing quantity, the
whole-command idempotency (fingerprint replay / mismatch conflict /
race lost at COMMIT) or the zero-write refusals is different. What the
stocking adds is the meaning of the arrival:

- the destination must be a terminal Area — judged on the locked Area
  re-read, exactly where a transfer refuses one — and the station must
  be bound to it (a Stockroom station);
- the quantity is manufacturing-complete: the flow closes
  (`status = STOCKED`, `closed_at`, Machine cleared, the Stockroom as
  its last position) and never returns to active inventory — the read
  models, the projection replay and every production command treat it
  like a scrapped flow (PROJECT_PROFILE §11 `introduced = active +
  stocked + scrapped`);
- the stocked quantity becomes available for Work Order Allocation
  (`app.application.allocations`) — a separate record that never
  touches Movement history; the receiving confirmation dialog
  (GUI_DESIGN §10) follows the `STOCKED` write as its own command;
- a `STOCKED` command is not undoable from the station: returning
  stocked quantity to active production is PROJECT_PROFILE §32 open
  decision 1, and an allocation may already depend on it
  (`app.application.undo` refuses it explicitly);
- Repair never stocks: the Repair intent on a Stockroom arrival is
  refused with nothing written.
"""

from sqlalchemy.orm import Session

from app.application.transfers import AreaTransfer, record_arrival


def stock_into_station_area(
    session: Session,
    *,
    station_id: str,
    part_number: object,
    quantity_flow_id: int,
    source_area_id: int,
    target_area_id: int,
    quantity: object,
    operation_id: int | None,
    confirm_route_deviation: object,
    route_deviation_reason: str | None,
    device_event_id: object,
) -> AreaTransfer:
    """Stock a QuantityFlow — or a part of it — at the station's terminal Area, ONE transaction.

    Returns the committed `STOCKED` command (``movement_type ==
    "STOCKED"``), 201-fresh or replayed; every refusal writes nothing.
    """
    return record_arrival(
        session,
        kind="STOCK",
        station_id=station_id,
        part_number=part_number,
        quantity_flow_id=quantity_flow_id,
        source_area_id=source_area_id,
        target_area_id=target_area_id,
        quantity=quantity,
        operation_id=operation_id,
        confirm_route_deviation=confirm_route_deviation,
        route_deviation_reason=route_deviation_reason,
        repair=False,
        repair_reason=None,
        device_event_id=device_event_id,
    )
