"""Database engine lifecycle and connectivity checks.

This module owns the SQLAlchemy engine and the health-check ping used by
the operational health endpoint. The domain schema mappings live in
app/infrastructure/models.py and are migrated by Alembic.
"""

import logging

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(Exception):
    """Raised when the database cannot be reached or queried.

    Intentionally carries no driver details so callers can report the
    condition without leaking connection strings or raw errors.
    """


def build_engine(database_url: str) -> Engine:
    # pool_pre_ping avoids handing out stale connections after a database
    # restart, which matters for a long-running development stack.
    return create_engine(database_url, pool_pre_ping=True)


def ping_database(engine: Engine) -> None:
    """Execute a real SELECT 1 against the configured database."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # Log operational context (exception type/chain) for diagnosis.
        # The URL is not logged because it contains credentials.
        logger.error("Database health check failed: %s", type(exc).__name__, exc_info=exc)
        raise DatabaseUnavailableError() from exc
