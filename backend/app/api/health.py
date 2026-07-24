"""Operational health endpoint.

This is infrastructure monitoring, not domain behavior. The route stays
thin: it delegates the actual database check to the infrastructure layer
and translates the outcome into an HTTP response.
"""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.core.config import get_settings
from app.infrastructure.database import DatabaseUnavailableError, ping_database

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    database: Literal["connected"]


class HealthUnavailableResponse(BaseModel):
    status: Literal["unavailable"]
    service: str
    database: Literal["unreachable"]
    detail: str


@router.get(
    "/api/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthUnavailableResponse}},
)
def get_health(request: Request) -> HealthResponse | JSONResponse:
    settings = get_settings()
    engine: Engine = request.app.state.engine
    try:
        ping_database(engine)
    except DatabaseUnavailableError:
        # Generic, actionable response: no connection strings, driver
        # errors, or stack traces cross the API boundary.
        unavailable = HealthUnavailableResponse(
            status="unavailable",
            service=settings.service_name,
            database="unreachable",
            detail="Database is unreachable. Verify that PostgreSQL is running and retry.",
        )
        return JSONResponse(status_code=503, content=unavailable.model_dump())
    return HealthResponse(status="ok", service=settings.service_name, database="connected")
