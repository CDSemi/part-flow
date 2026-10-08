"""FastAPI application entry point for the PartFlow backend."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.allocations import router as allocations_router
from app.api.area_board import router as area_board_router
from app.api.csrf import CsrfMiddleware, NoStoreMiddleware
from app.api.environment import router as environment_router
from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.hot_list import router as hot_list_router
from app.api.machines import router as machines_router
from app.api.part_numbers import router as part_numbers_router
from app.api.policies import router as policies_router
from app.api.production_board import router as production_board_router
from app.api.production_release import router as production_release_router
from app.api.release_gate import ReleaseGate, ReleaseGateMiddleware
from app.api.roles import router as roles_router
from app.api.route_adjustments import router as route_adjustments_router
from app.api.route_templates import router as route_templates_router
from app.api.scan_station import router as scan_station_router
from app.api.session import router as session_router
from app.api.setup import router as setup_router
from app.api.station_devices import router as station_devices_router
from app.api.tracking import router as tracking_router
from app.api.users import router as users_router
from app.api.work_order_import import router as work_order_import_router
from app.api.work_orders import router as work_orders_router
from app.api.workers import router as workers_router
from app.application import first_run
from app.application.readiness import ReadinessMonitor, override_ignored
from app.core.config import get_settings
from app.infrastructure import schema_revision
from app.infrastructure.database import build_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _migration_scripts() -> tuple[str, frozenset[str]]:
    """This image's Alembic head and known revisions; unreadable scripts fail startup."""
    try:
        return schema_revision.code_head(), schema_revision.known_revisions()
    except schema_revision.MigrationScriptsError as exc:
        raise RuntimeError(
            f"PartFlow cannot read its migration scripts (alembic/): {exc}."
            " The backend image is incomplete."
        ) from exc


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # get_settings() validates required configuration; a missing or invalid
    # database connection (DATABASE_URL, or the file-based DATABASE_HOST,
    # DATABASE_NAME, DATABASE_USER and DATABASE_PASSWORD_FILE form) aborts
    # startup here instead of failing per-request.
    settings = get_settings()
    # Phase 16 slice 3: the expected schema revision is this image's own
    # single Alembic head (OD-16-06).
    expected_revision, known_revisions = _migration_scripts()
    accepted_revision = settings.accept_schema_revision
    if override_ignored(accepted_revision, known_revisions):
        logger.warning(
            "ACCEPT_SCHEMA_REVISION names %s, a revision this release already knows;"
            " the override is ignored.",
            accepted_revision,
        )
    app.state.engine = build_engine(
        settings.database_url, application_name=schema_revision.API_APPLICATION_NAME
    )
    app.state.readiness = ReadinessMonitor(
        app.state.engine,
        expected_revision=expected_revision,
        known_revisions=known_revisions,
        accepted_revision=accepted_revision,
    )
    app.state.release_gate = ReleaseGate(
        enforced_release=settings.release_tag if settings.enforce_client_release else None,
        readiness=app.state.readiness,
    )
    # Phase 14 slice 1: while no administrator exists, print the
    # first-run setup token once (never fails startup).
    first_run.announce_if_open(app.state.engine, app.state.setup_gate)
    try:
        yield
    finally:
        app.state.engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="PartFlow API", lifespan=lifespan)
    # One first-run setup token per process (in memory only).
    app.state.setup_gate = first_run.SetupGate()
    # The last added is outermost: NoStore -> ReleaseGate -> CSRF -> routes,
    # so a stale page gets the release refusal before a CSRF refusal and
    # session paths keep no-store (app.api.csrf, app.api.release_gate).
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(ReleaseGateMiddleware)
    app.add_middleware(NoStoreMiddleware)
    app.include_router(health_router)
    app.include_router(session_router)
    app.include_router(setup_router)
    app.include_router(environment_router)
    app.include_router(workers_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(policies_router)
    app.include_router(machines_router)
    app.include_router(part_numbers_router)
    app.include_router(work_orders_router)
    app.include_router(work_order_import_router)
    app.include_router(production_release_router)
    app.include_router(route_templates_router)
    app.include_router(scan_station_router)
    app.include_router(station_devices_router)
    app.include_router(allocations_router)
    app.include_router(production_board_router)
    app.include_router(area_board_router)
    app.include_router(tracking_router)
    app.include_router(route_adjustments_router)
    app.include_router(hot_list_router)
    register_exception_handlers(app)
    return app


app = create_app()
