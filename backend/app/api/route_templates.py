"""Read-only RouteTemplate endpoint (Phase 4 — GUI_DESIGN §11.4).

The one route here serves the release flow's ``PLANNED`` selection:
the active RouteTemplates with their ordered steps, so the UI can
offer an existing Route and preview its first step (the release
command validates the confirmed starting Area/Operation against that
first step). This is deliberately NOT Planned Routes management —
no create, update, or archive exists on this surface (GUI_DESIGN §13
stays a later phase).
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import SessionDep
from app.application import route_templates
from app.application.route_templates import RouteTemplateDetail

router = APIRouter(prefix="/api")


class RouteStepResponse(BaseModel):
    id: int
    sequence: int
    area_id: int
    operation_id: int | None
    # Advisory only, and copied verbatim into the AssignedRoute
    # snapshot at release (SLICE1_DATA_MODEL §10) — part of the step
    # shape, so the read model exposes it instead of dropping it.
    expected_duration: datetime.timedelta | None
    instructions: str | None


class RouteTemplateResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    steps: list[RouteStepResponse]


def _response(detail: RouteTemplateDetail) -> RouteTemplateResponse:
    return RouteTemplateResponse(
        id=detail.template.id,
        name=detail.template.name,
        description=detail.template.description,
        created_at=detail.template.created_at,
        updated_at=detail.template.updated_at,
        steps=[
            RouteStepResponse(
                id=step.id,
                sequence=step.sequence,
                area_id=step.area_id,
                operation_id=step.operation_id,
                expected_duration=step.expected_duration,
                instructions=step.instructions,
            )
            for step in detail.steps
        ],
    )


@router.get("/route-templates")
def list_route_templates(session: SessionDep) -> list[RouteTemplateResponse]:
    """The active RouteTemplates with ordered steps (read-only)."""
    return [_response(detail) for detail in route_templates.list_active_route_templates(session)]
