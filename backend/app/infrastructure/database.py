"""Database engine lifecycle.

This module owns the SQLAlchemy engine. The health endpoint's database
read is ``app.infrastructure.schema_revision.read_database_revision``
(Phase 16 slice 3). The domain schema mappings live in
app/infrastructure/models.py and are migrated by Alembic.
"""

from sqlalchemy import Engine, create_engine


class DatabaseUnavailableError(Exception):
    """Raised when the database cannot be reached or queried.

    Intentionally carries no driver details so callers can report the
    condition without leaking connection strings or raw errors.
    """


def build_engine(database_url: str, *, application_name: str | None = None) -> Engine:
    """The engine; ``application_name`` names its sessions in ``pg_stat_activity``.

    ``migrate`` refuses while a session named ``partflow-api`` is
    connected (Phase 16 slice 3), so the API and the CLI name theirs.
    """
    connect_args = {} if application_name is None else {"application_name": application_name}
    # pool_pre_ping avoids handing out stale connections after a database
    # restart, which matters for a long-running development stack.
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
