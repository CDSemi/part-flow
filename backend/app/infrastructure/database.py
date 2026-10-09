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


def build_engine(
    database_url: str,
    *,
    application_name: str | None = None,
    connect_timeout: int | None = None,
) -> Engine:
    """The engine; ``application_name`` names its sessions in ``pg_stat_activity``.

    ``migrate`` refuses while a session named ``partflow-api`` is
    connected (Phase 16 slice 3), so the API and the CLI name theirs.
    ``connect_timeout`` (seconds) bounds establishing a connection
    (``status``, Phase 16 slice 6). Statement parameters are never part of
    an exception's text (``hide_parameters``): a logged traceback of a
    failing statement must not print a badge, a login name or a token.
    """
    connect_args: dict[str, object] = {}
    if application_name is not None:
        connect_args["application_name"] = application_name
    if connect_timeout is not None:
        connect_args["connect_timeout"] = connect_timeout
    # pool_pre_ping avoids handing out stale connections after a database
    # restart, which matters for a long-running development stack.
    return create_engine(
        database_url, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args
    )
