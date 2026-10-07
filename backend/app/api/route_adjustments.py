"""AssignedRoute adjustment endpoints (Phase 14 slice 6 — PROJECT_PROFILE §8.10, §17).

Thin routes over `app.application.route_adjustments` (the command) and
`app.application.tracking.assigned_routes_of` (the editor read): request
schemas validate shape only (``extra="forbid"``), every rule and the
transaction live in the Application layer, and the central handlers
translate the typed errors.

Surface (both need *Assign and edit Routes*, ``ASSIGN_ROUTES``):

- ``POST /quantity-flows/{quantity_flow_id}/route-adjustments`` — replace
  the future (unreferenced) steps of one ACTIVE PLANNED flow's own
  AssignedRoute with a mandatory reason; audited as ``ROUTE_ADJUSTED``
  with the signed-in User. 201 fresh, 200 on an idempotent replay of the
  same ``device_event_id`` + body (rebuilt from the audit row alone);
  404 unknown flow; 409 ``route_changed`` when the future steps differ
  from ``expected_future_step_ids``, 409 for a Floating or closed flow,
  an unchanged tail, an inactive reference, a mismatched reuse or
  ``recorded_by_another_user``; 422 for a missing reason or an invalid
  step — every refusal writes nothing.
- ``GET /tracking/assigned-routes?part_number=…`` — every ACTIVE PLANNED
  flow of the PN with its whole snapshot (steps locked or editable,
  preferred Machine and instructions) — the read only the adjustment
  dialog uses; 404 unknown PN, 422 invalid PN.

The acting User (``actor_user_id``) is derived from the session, never
from a request body.
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.authorization import RequirePermission
from app.api.dependencies import SessionDep
from app.api.route_templates import RouteStepRequest
from app.api.tracking import (
    FlowPositionResponse,
    RouteTemplateRef,
    TrackingAreaRef,
    TrackingMachineRef,
    TrackingOperationRef,
    flow_position_response,
)
from app.application import route_adjustments, tracking
from app.application.authentication import Principal
from app.application.route_templates import RouteStepInput
from app.domain.enums import Permission

router = APIRouter(prefix="/api")

RouteAdjusterDep = Annotated[Principal, Depends(RequirePermission(Permission.ASSIGN_ROUTES))]


class RouteAdjustmentRequest(BaseModel):
    """The new future steps of one flow's AssignedRoute (no actor, no station)."""

    model_config = ConfigDict(extra="forbid")

    device_event_id: str
    # The editable tail as read, in order; [] allowed.
    expected_future_step_ids: list[StrictInt]
    # The new tail in route order; [] allowed.
    steps: list[RouteStepRequest]
    reason: str


class AssignedStepResponse(BaseModel):
    id: int
    sequence: int
    area_id: int
    operation_id: int | None
    expected_duration: datetime.timedelta | None
    preferred_machine_id: int | None
    instructions: str | None


class RouteAdjustmentResponse(BaseModel):
    device_event_id: str
    quantity_flow_id: int
    part_number: str
    assigned_route_id: int
    kept_through_sequence: int
    reason: str
    # The whole route after the adjustment.
    steps: list[AssignedStepResponse]


class EditorStepResponse(BaseModel):
    id: int
    sequence: int
    area: TrackingAreaRef
    operation: TrackingOperationRef | None
    expected_duration: datetime.timedelta | None
    preferred_machine: TrackingMachineRef | None
    instructions: str | None
    state: tracking.RouteStepState
    # A past step: never editable.
    locked: bool


class AdjustableFlowResponse(BaseModel):
    quantity_flow_id: int
    quantity: int
    position: FlowPositionResponse | None
    off_route: bool
    source_template: RouteTemplateRef | None
    kept_through_sequence: int
    future_step_ids: list[int]
    steps: list[EditorStepResponse]


class AssignedRoutesResponse(BaseModel):
    part_number: str
    # ACTIVE PLANNED flows, newest first.
    flows: list[AdjustableFlowResponse]


def _adjustment_response(
    result: route_adjustments.RouteAdjustmentResult,
) -> RouteAdjustmentResponse:
    return RouteAdjustmentResponse(
        device_event_id=result.device_event_id,
        quantity_flow_id=result.quantity_flow_id,
        part_number=result.part_number,
        assigned_route_id=result.assigned_route_id,
        kept_through_sequence=result.kept_through_sequence,
        reason=result.reason,
        steps=[AssignedStepResponse(**step._asdict()) for step in result.steps],
    )


@router.post("/quantity-flows/{quantity_flow_id}/route-adjustments")
def adjust_assigned_route(
    principal: RouteAdjusterDep,
    quantity_flow_id: int,
    body: RouteAdjustmentRequest,
    session: SessionDep,
    response: Response,
) -> RouteAdjustmentResponse:
    result = route_adjustments.adjust_assigned_route(
        session,
        actor_user_id=principal.user_id,
        quantity_flow_id=quantity_flow_id,
        device_event_id=body.device_event_id,
        expected_future_step_ids=body.expected_future_step_ids,
        steps=[
            RouteStepInput(
                area_id=step.area_id,
                operation_id=step.operation_id,
                expected_duration=step.expected_duration,
                preferred_machine_id=step.preferred_machine_id,
                instructions=step.instructions,
            )
            for step in body.steps
        ],
        reason=body.reason,
    )
    response.status_code = 201 if result.created else 200
    return _adjustment_response(result)


def _editor_step(step: tracking.EditorStep) -> EditorStepResponse:
    machine = step.preferred_machine
    operation = step.operation
    return EditorStepResponse(
        id=step.step.id,
        sequence=step.step.sequence,
        area=TrackingAreaRef(
            id=step.area.id,
            name=step.area.name,
            color=step.area.color,
            is_terminal=step.area.is_terminal,
        ),
        operation=(
            TrackingOperationRef(
                id=operation.id,
                code=operation.code,
                name=operation.name,
                is_external=operation.is_external,
            )
            if operation is not None
            else None
        ),
        expected_duration=step.step.expected_duration,
        preferred_machine=(
            TrackingMachineRef(id=machine.id, name=machine.name) if machine is not None else None
        ),
        instructions=step.step.instructions,
        state=step.state,
        locked=step.locked,
    )


@router.get("/tracking/assigned-routes")
def get_assigned_routes(
    principal: RouteAdjusterDep, session: SessionDep, part_number: str
) -> AssignedRoutesResponse:
    routes = tracking.assigned_routes_of(session, part_number)
    return AssignedRoutesResponse(
        part_number=routes.part_number,
        flows=[
            AdjustableFlowResponse(
                quantity_flow_id=route.flow.id,
                quantity=route.flow.quantity,
                position=flow_position_response(route.position, route.current_area),
                off_route=route.off_route,
                source_template=(
                    RouteTemplateRef(id=route.source_template.id, name=route.source_template.name)
                    if route.source_template is not None
                    else None
                ),
                kept_through_sequence=route.kept_through_sequence,
                future_step_ids=route.future_step_ids,
                steps=[_editor_step(step) for step in route.steps],
            )
            for route in routes.flows
        ],
    )
