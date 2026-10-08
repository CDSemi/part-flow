"""Alembic migration environment.

Two online paths:

- Default (``alembic upgrade head`` in development, tests and the OPS-lane
  staging): uses the same database configuration as the backend
  application so migrations always target the database the API runs
  against, on a connection and transaction of its own. A caller may
  pre-set `sqlalchemy.url` on the Alembic config (the test suite does, to
  migrate an isolated temporary database); only then is the application
  setting not consulted.
- External connection (production ``python -m app.cli migrate``, Phase 16
  slice 3): the caller passes an open connection, already inside its
  transaction, as ``config.attributes["connection"]``. Alembic runs
  inside that transaction and never commits it; the application setting
  is not consulted.
"""

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.infrastructure.models import Base

config = context.config
external_connection = config.attributes.get("connection")
if external_connection is None and not config.get_main_option("sqlalchemy.url"):
    from app.core.config import get_settings

    # ConfigParser interpolation reserves "%": escape the percent-encoded URL;
    # get_main_option returns it unescaped.
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

# The Phase 3 domain metadata (app/infrastructure/models.py) — the real
# model metadata Alembic compares against for autogenerate support.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if external_connection is not None:
        # app.cli migrate owns this connection and its transaction (CD4):
        # Alembic runs inside it and never commits it.
        context.configure(connection=external_connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
