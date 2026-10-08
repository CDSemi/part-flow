"""Operational health endpoints (Phase 16 slice 3: liveness and readiness).

This is infrastructure monitoring, not domain behavior. The routes stay
thin:

- ``GET /api/health/live`` — liveness: the process answers. No database
  access, never gated; container health checks use it, so a schema
  mismatch never makes a container unhealthy.
- ``GET /api/health`` — readiness and the clients' connectivity ping:
  reads the database revision on every call (``ReadinessMonitor.observe``)
  and reports the release identity. 200 when the schema is ``current`` or
  ``accepted``; 503 ``not_ready`` on a mismatch; 503 ``unavailable`` when
  the database cannot be read.
"""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.application.readiness import ReadinessMonitor, SchemaReadiness
from app.core.config import get_settings

router = APIRouter()

NOT_READY_DETAIL = (
    "The database schema does not match this release. Changes are refused until an"
    " administrator completes or rolls back the release."
)
UNAVAILABLE_DETAIL = "Database is unreachable. Verify that PostgreSQL is running and retry."


class LivenessResponse(BaseModel):
    status: Literal["live"]
    service: str
    release: str
    commit: str | None


class HealthResponse(BaseModel):
    # `schema` shadows a BaseModel attribute: the field is schema_state,
    # serialized (and validated) by its alias.
    status: Literal["ok"]
    service: str
    database: Literal["connected"]
    release: str
    commit: str | None
    schema_state: Literal["current", "accepted"] = Field(alias="schema")
    expected_revision: str
    database_revision: str | None
    accepted_revision: str | None


class HealthNotReadyResponse(BaseModel):
    status: Literal["not_ready"]
    service: str
    database: Literal["connected"]
    release: str
    commit: str | None
    schema_state: Literal["mismatch"] = Field(alias="schema")
    expected_revision: str
    database_revision: str | None
    accepted_revision: str | None
    detail: str
    not_ready: Literal[True]


class HealthUnavailableResponse(BaseModel):
    status: Literal["unavailable"]
    service: str
    database: Literal["unreachable"]
    release: str
    commit: str | None
    schema_state: Literal["unknown"] = Field(alias="schema")
    expected_revision: str
    database_revision: None
    accepted_revision: str | None
    detail: str


@router.get("/api/health/live", response_model=LivenessResponse)
def get_liveness() -> LivenessResponse:
    settings = get_settings()
    return LivenessResponse(
        status="live",
        service=settings.service_name,
        release=settings.release_tag,
        commit=settings.release_commit,
    )


@router.get(
    "/api/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthNotReadyResponse | HealthUnavailableResponse}},
)
def get_health(request: Request) -> HealthResponse | JSONResponse:
    settings = get_settings()
    monitor: ReadinessMonitor = request.app.state.readiness
    readiness: SchemaReadiness = monitor.observe()
    body: dict[str, object] = {
        "status": "ok",
        "service": settings.service_name,
        "database": "connected",
        "release": settings.release_tag,
        "commit": settings.release_commit,
        "schema": readiness.state,
        "expected_revision": readiness.expected_revision,
        "database_revision": readiness.database_revision,
        "accepted_revision": readiness.accepted_revision,
    }
    if readiness.state == "unknown":
        # Generic, actionable response: no connection strings, driver
        # errors, or stack traces cross the API boundary.
        body.update(status="unavailable", database="unreachable", detail=UNAVAILABLE_DETAIL)
        unavailable = HealthUnavailableResponse.model_validate(body)
        return JSONResponse(status_code=503, content=unavailable.model_dump(by_alias=True))
    if readiness.state == "mismatch":
        body.update(status="not_ready", detail=NOT_READY_DETAIL, not_ready=True)
        not_ready = HealthNotReadyResponse.model_validate(body)
        return JSONResponse(status_code=503, content=not_ready.model_dump(by_alias=True))
    return HealthResponse.model_validate(body)
