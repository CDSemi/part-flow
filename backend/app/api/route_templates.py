"""RouteTemplate endpoints: release selection and Planned Routes management.

Phase 4 (GUI_DESIGN §11.4): ``GET /api/route-templates`` serves the
release flow's ``PLANNED`` selection — the active RouteTemplates with
their ordered steps, so the UI can offer an existing Route and preview
its first step. It stays active-only.

Phase 13 slice 8 (GUI_DESIGN §13): Management → Planned Routes —
``GET /api/route-templates/management`` (active and archived, with
usage), ``POST`` create, ``PUT`` full replacement of name, description
and the ordered step set (requests carry no step ids or sequences — the
list order is the route order), ``POST …/{id}/archive`` (ever-used
only, idempotent), ``DELETE …/{id}`` (never-used only) and
``GET …/{id}/usage``. Durations travel as ISO 8601 (``PT4H``); the
``*_on`` fields are site dates (``work_orders.site_date_of``). The rules
live in ``app.application.route_templates``.

Access (Phase 14 slice 3, ``app.api.route_access``): the active list
stays public (the release dialog and the Scan Station read it); the
management list and the usage read need View production data or Manage
Planned Routes; every write needs Manage Planned Routes and is audited
with the signed-in User (``actor_user_id``).
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.authorization import PLANNED_ROUTES_READ, RequireAnyPermission, RequirePermission
from app.api.dependencies import SessionDep
from app.application import route_templates, work_orders
from app.application.authentication import Principal
from app.application.route_templates import (
    RouteStepInput,
    RouteTemplateDetail,
    RouteTemplateRecord,
)
from app.domain.enums import Permission
from app.infrastructure.models import RouteStep

router = APIRouter(prefix="/api")

RouteTemplateManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.MANAGE_ROUTE_TEMPLATES))
]
RouteTemplateReaderDep = Annotated[Principal, Depends(RequireAnyPermission(*PLANNED_ROUTES_READ))]


class RouteStepResponse(BaseModel):
    id: int
    sequence: int
    area_id: int
    operation_id: int | None
    # Advisory only, and copied verbatim into the AssignedRoute
    # snapshot at release (SLICE1_DATA_MODEL §10) — part of the step
    # shape, so the read model exposes it instead of dropping it.
    expected_duration: datetime.timedelta | None
    # Advisory preferred Machine by stable id (PROJECT_PROFILE §8.9);
    # possibly stale (moved or retired since the save).
    preferred_machine_id: int | None
    instructions: str | None


class RouteTemplateResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    steps: list[RouteStepResponse]


class RouteTemplateManagementResponse(BaseModel):
    id: int
    name: str
    description: str | None
    archived_at: datetime.datetime | None
    archived_on: datetime.date | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    updated_on: datetime.date
    ever_used: bool
    usage_count: int
    steps: list[RouteStepResponse]


class RouteStepRequest(BaseModel):
    """One step; no id and no sequence — the list order is the route order."""

    model_config = ConfigDict(extra="forbid")

    area_id: int
    # Required by the Application rule (a null is refused with the step
    # number), so the client gets the domain message, not a schema one.
    operation_id: int | None
    expected_duration: datetime.timedelta | None = None
    preferred_machine_id: int | None = None
    instructions: str | None = None


class RouteTemplateWriteRequest(BaseModel):
    """The full template; the acting User is never client-writable (it is
    the signed-in User, Phase 14 slice 3)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    steps: list[RouteStepRequest]


class RouteTemplateUsageFlowResponse(BaseModel):
    quantity_flow_id: int
    part_number: str
    released_on: datetime.date


class RouteTemplateUsageResponse(BaseModel):
    template_id: int
    total: int
    flows: list[RouteTemplateUsageFlowResponse]


def _step_response(step: RouteStep) -> RouteStepResponse:
    return RouteStepResponse(
        id=step.id,
        sequence=step.sequence,
        area_id=step.area_id,
        operation_id=step.operation_id,
        expected_duration=step.expected_duration,
        preferred_machine_id=step.preferred_machine_id,
        instructions=step.instructions,
    )


def _response(detail: RouteTemplateDetail) -> RouteTemplateResponse:
    return RouteTemplateResponse(
        id=detail.template.id,
        name=detail.template.name,
        description=detail.template.description,
        created_at=detail.template.created_at,
        updated_at=detail.template.updated_at,
        steps=[_step_response(step) for step in detail.steps],
    )


def _management_response(record: RouteTemplateRecord) -> RouteTemplateManagementResponse:
    template = record.template
    return RouteTemplateManagementResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        archived_at=template.archived_at,
        archived_on=(
            work_orders.site_date_of(template.archived_at)
            if template.archived_at is not None
            else None
        ),
        created_at=template.created_at,
        updated_at=template.updated_at,
        updated_on=work_orders.site_date_of(template.updated_at),
        ever_used=record.ever_used,
        usage_count=record.usage_count,
        steps=[_step_response(step) for step in record.steps],
    )


def _step_inputs(body: RouteTemplateWriteRequest) -> list[RouteStepInput]:
    return [
        RouteStepInput(
            area_id=step.area_id,
            operation_id=step.operation_id,
            expected_duration=step.expected_duration,
            preferred_machine_id=step.preferred_machine_id,
            instructions=step.instructions,
        )
        for step in body.steps
    ]


@router.get("/route-templates")
def list_route_templates(session: SessionDep) -> list[RouteTemplateResponse]:
    """The active RouteTemplates with ordered steps (release selection)."""
    return [_response(detail) for detail in route_templates.list_active_route_templates(session)]


# A literal segment, declared before every `{template_id}` route.
@router.get("/route-templates/management")
def list_route_template_records(
    principal: RouteTemplateReaderDep, session: SessionDep
) -> list[RouteTemplateManagementResponse]:
    """Every template (active first, then name, id) with usage."""
    return [
        _management_response(record) for record in route_templates.list_route_templates(session)
    ]


@router.post("/route-templates", status_code=201)
def create_route_template(
    principal: RouteTemplateManagerDep, body: RouteTemplateWriteRequest, session: SessionDep
) -> RouteTemplateManagementResponse:
    record = route_templates.create_route_template(
        session,
        name=body.name,
        description=body.description,
        steps=_step_inputs(body),
        actor_user_id=principal.user_id,
    )
    return _management_response(record)


@router.put("/route-templates/{template_id}")
def replace_route_template(
    principal: RouteTemplateManagerDep,
    template_id: int,
    body: RouteTemplateWriteRequest,
    session: SessionDep,
) -> RouteTemplateManagementResponse:
    record = route_templates.replace_route_template(
        session,
        template_id,
        name=body.name,
        description=body.description,
        steps=_step_inputs(body),
        actor_user_id=principal.user_id,
    )
    return _management_response(record)


@router.post("/route-templates/{template_id}/archive")
def archive_route_template(
    principal: RouteTemplateManagerDep, template_id: int, session: SessionDep
) -> RouteTemplateManagementResponse:
    return _management_response(
        route_templates.archive_route_template(
            session, template_id, actor_user_id=principal.user_id
        )
    )


@router.delete("/route-templates/{template_id}", status_code=204)
def delete_route_template(
    principal: RouteTemplateManagerDep, template_id: int, session: SessionDep
) -> None:
    route_templates.delete_route_template(session, template_id, actor_user_id=principal.user_id)


@router.get("/route-templates/{template_id}/usage")
def route_template_usage(
    principal: RouteTemplateReaderDep, template_id: int, session: SessionDep
) -> RouteTemplateUsageResponse:
    """The Quantity Flows released with the template, newest first
    (at most ``route_templates.USAGE_LIST_LIMIT``), and the total."""
    usage = route_templates.route_template_usage(session, template_id)
    return RouteTemplateUsageResponse(
        template_id=template_id,
        total=usage.total,
        flows=[
            RouteTemplateUsageFlowResponse(
                quantity_flow_id=entry.quantity_flow_id,
                part_number=entry.part_number,
                released_on=work_orders.site_date_of(entry.released_at),
            )
            for entry in usage.flows
        ],
    )
