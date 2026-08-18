"""Alembic migration environment.

Uses the same DATABASE_URL configuration as the backend application so
migrations always target the database the API runs against. A caller may
pre-set `sqlalchemy.url` on the Alembic config (the test suite does, to
migrate an isolated temporary database); only then is the application
setting not consulted.
"""

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.infrastructure.models import Base

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    from app.core.config import get_settings

    config.set_main_option("sqlalchemy.url", get_settings().database_url)

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
