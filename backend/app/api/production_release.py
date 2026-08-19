"""Explicit production release endpoint (Phase 4 — GUI_DESIGN §11.4).

HTTP surface for the "Release to production…" action carried by each
saved demand row. The route stays thin orchestration: the request
schema validates shape only (``extra="forbid"``), the Application
command owns every rule and the one-submission-one-transaction
protocol (SLICE1_DATA_MODEL §8, §13–§14), and the central handlers in
``app.api.errors`` translate typed failures — including the
confirmation-required response that carries the existing active
distribution.

Deliberate surface decisions:

- The endpoint is addressed through the initiating WorkOrderDemand
  (``/work-orders/{id}/demands/{id}/release``): the UI always releases
  from a saved demand row, and the demand context is a required part
  of the request fingerprint. This is informational release context
  only — no Movement→Demand foreign key exists and WorkOrderDemand
  never owns Movement.
- ``device_event_id`` is the client-generated UUID idempotency key,
  reused verbatim on transport retries: a replay of the committed
  release returns the original result with 200 (a fresh release is
  201) and creates nothing; reuse with a different normalized request
  is an explicit 409 that creates nothing.
- ``route_mode`` is required — the release flow explicitly confirms
  it (FLOATING by default in the UI, never defaulted here);
  ``route_template_id`` travels only with a PLANNED release.
- No client-writable actor exists on this surface (same rule as the
  intake endpoints): the informational actor context stays absent
  until an authenticated identity exists (Phase 14).
"""

import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.dependencies import SessionDep
from app.application import production_release
from app.application.production_release import ProductionRelease
from app.domain.enums import RouteMode

router = APIRouter(prefix="/api")


class ProductionReleaseRequest(BaseModel):
    """The confirmed release submission (GUI_DESIGN §11.4)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    # Strict: a quantity is an integer, never a coerced bool/float/text.
    quantity: StrictInt
    route_mode: RouteMode
    # Required for PLANNED, forbidden for FLOATING.
    route_template_id: int | None = None
    starting_area_id: int
    operation_id: int
    # Explicit active-quantity confirmation (SLICE1 §8.2): set by the
    # UI only after showing the existing distribution.
    confirm_active_quantity: StrictBool = False
    device_event_id: str


class ProductionReleaseResponse(BaseModel):
    """The committed release result (SLICE1 §8.6)."""

    quantity_flow_id: int
    part_number: str
    quantity: int
    route_mode: RouteMode
    # The AssignedRoute snapshot id for a PLANNED release, null for
    # FLOATING.
    assigned_route_id: int | None
    current_area_id: int
    operation_id: int
    movement_id: int
    device_event_id: str
    occurred_at: datetime.datetime


def _response(result: ProductionRelease) -> ProductionReleaseResponse:
    flow = result.flow
    movement = result.movement
    return ProductionReleaseResponse(
        quantity_flow_id=flow.id,
        part_number=flow.part_number,
        quantity=flow.quantity,
        route_mode=RouteMode(flow.route_mode),
        assigned_route_id=flow.assigned_route_id,
        current_area_id=flow.current_area_id,
        operation_id=movement.operation_id,
        movement_id=movement.id,
        device_event_id=movement.device_event_id,
        occurred_at=movement.occurred_at,
    )


@router.post("/work-orders/{work_order_id}/demands/{demand_id}/release")
def release_to_production(
    work_order_id: int,
    demand_id: int,
    body: ProductionReleaseRequest,
    session: SessionDep,
    response: Response,
) -> ProductionReleaseResponse:
    result = production_release.release_to_production(
        session,
        work_order_id=work_order_id,
        work_order_demand_id=demand_id,
        part_number=body.part_number,
        quantity=body.quantity,
        route_mode=body.route_mode,
        route_template_id=body.route_template_id,
        starting_area_id=body.starting_area_id,
        operation_id=body.operation_id,
        confirm_active_quantity=body.confirm_active_quantity,
        device_event_id=body.device_event_id,
    )
    response.status_code = 201 if result.created else 200
    return _response(result)
