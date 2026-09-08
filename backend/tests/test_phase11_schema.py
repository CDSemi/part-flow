"""Integration tests for the Phase 11 read-path index migration.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the boundary `0012_phase11_tracking_index` adds
(IMPLEMENTATION_ROADMAP Phase 11 — PN Tracking; GUI_DESIGN §7.2 item 5):

- exact head boundary: no table, column or constraint beyond Phase 10 —
  exactly one composite index `(part_number, occurred_at, id)` on
  `part_movements`, the access path of the per-PN reverse-chronological
  Movement history read and its keyset paging;
- models↔migration metadata parity at head (moved here from the Phase
  10 schema test, which is now pinned to 0011);
- clean downgrade back to the Phase 10 boundary (the index gone, the
  schema otherwise untouched) with a successful re-upgrade.

Phase 11 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to
`0012_phase11_tracking_index` and move the head-level coverage into
that phase's schema test.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PHASE10_REVISION = "0011_phase10_stock_allocation"
_PHASE11_REVISION = "0012_phase11_tracking_index"
_INDEX = "ix_part_movements_part_number_occurred_at_id"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.render_as_string(hide_password=False))
    return config


def _create_temp_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))


def _drop_temp_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def migrated_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated head → base → head through real Alembic runs."""
    name = "partflow_test_phase11_schema"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()
    _drop_temp_database(admin_engine, name)


def _index_columns(engine: Engine) -> dict[str, list[str]]:
    return {
        str(index["name"]): [str(column) for column in index["column_names"]]
        for index in inspect(engine).get_indexes("part_movements")
    }


def test_head_is_the_phase11_revision(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM alembic_version"))
        assert version.scalar_one() == _PHASE11_REVISION


def test_head_adds_exactly_the_history_index(migrated_engine: Engine) -> None:
    indexes = _index_columns(migrated_engine)
    assert indexes[_INDEX] == ["part_number", "occurred_at", "id"]
    # The Phase 3–10 indexes of the table are untouched.
    assert indexes["ix_part_movements_quantity_flow_id_id"] == ["quantity_flow_id", "id"]
    columns = {column["name"] for column in inspect(migrated_engine).get_columns("part_movements")}
    # No column arrived with the index (Phase 13's Worker columns stay absent).
    assert "worker_id" not in columns and "scan_session_id" not in columns


def test_models_metadata_matches_the_migrated_schema(migrated_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def test_downgrade_restores_the_phase10_boundary(admin_engine: Engine) -> None:
    name = "partflow_test_phase11_downgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _PHASE10_REVISION)
        engine = create_engine(url)
        try:
            indexes = _index_columns(engine)
            assert _INDEX not in indexes
            assert "ix_part_movements_quantity_flow_id_id" in indexes
            assert "work_order_allocations" in inspect(engine).get_table_names()
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert _INDEX in _index_columns(engine)
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)
