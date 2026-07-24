"""FastAPI application entry point for the PartFlow backend."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
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
    return app


app = create_app()
