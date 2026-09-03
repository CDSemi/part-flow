"""FastAPI application entry point for the PartFlow backend."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.allocations import router as allocations_router
from app.api.environment import router as environment_router
from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.machines import router as machines_router
from app.api.part_numbers import router as part_numbers_router
from app.api.production_board import router as production_board_router
from app.api.production_release import router as production_release_router
from app.api.route_templates import router as route_templates_router
from app.api.scan_station import router as scan_station_router
from app.api.work_orders import router as work_orders_router
from app.core.config import get_settings
from app.infrastructure.database import build_engine

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # get_settings() validates required configuration; a missing
    # DATABASE_URL aborts startup here instead of failing per-request.
    settings = get_settings()
    app.state.engine = build_engine(settings.database_url)
    try:
        yield
    finally:
        app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="PartFlow API", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(environment_router)
    app.include_router(machines_router)
    app.include_router(part_numbers_router)
    app.include_router(work_orders_router)
    app.include_router(production_release_router)
    app.include_router(route_templates_router)
    app.include_router(scan_station_router)
    app.include_router(allocations_router)
    app.include_router(production_board_router)
    register_exception_handlers(app)
    return app


app = create_app()
