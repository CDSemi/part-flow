"""Integration tests for the Phase 13 Workers registry migration.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0014_phase13_workers` adds (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §8.13, §10; owner decisions OD-3, OD-10):

- exact head boundary: `0014_phase13_workers` is head;
- the `workers` table shape and its exact constraint names; no FK from
  or to it;
- the database CHECKs refuse every non-canonical badge (empty, padded,
  lowercase, `PF:` in any case, over 128 characters), a partial avatar,
  a non-image type and an avatar above 2 MiB, while the UNIQUE refuses a
  duplicate canonical badge — so case-insensitive uniqueness holds in
  PostgreSQL itself;
- the widened audit vocabulary: entity `Worker` and event `DELETED`
  are admitted, other values still refused;
- models↔migration metadata parity at head (moved here from the
  Phase 12 schema test, which is now pinned to 0013);
- clean downgrade back to the Phase 12 boundary with a successful
  re-upgrade, and the refusing downgrade while Worker configuration or
  Worker audit history exists (never deleted).

Phase 13 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to
`0014_phase13_workers` and move the head-level coverage into that
phase's schema test.
"""

import datetime
import importlib.util
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError, ProgrammingError

from alembic import command
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PHASE12_REVISION = "0013_phase12_priority"
_PHASE13_REVISION = "0014_phase13_workers"
_MIGRATION_FILE = _BACKEND_DIR / "alembic" / "versions" / "20261004_0014_phase13_workers.py"
_WORKER_CHECKS = {
    "ck_workers_badge_barcode_canonical",
    "ck_workers_avatar_image_shape",
    "ck_workers_avatar_image_type",
    "ck_workers_avatar_image_size",
}


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _create_temp_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))


def _drop_temp_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase13_migration", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def migrated_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated head → base → head through real Alembic runs."""
    name = "partflow_test_phase13_schema"
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


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    """Per-test connection whose transaction is always rolled back."""
    with migrated_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _insert_worker(connection: Connection, badge: str, **avatar: object) -> None:
    columns = ["name", "badge_barcode", *avatar]
    values = [":name", ":badge", *(f":{column}" for column in avatar)]
    connection.execute(
        sa.text(f"INSERT INTO workers ({', '.join(columns)}) VALUES ({', '.join(values)})"),
        {"name": "Alex Tran", "badge": badge, **avatar},
    )


def _insert_audit(connection: Connection, event_type: str, entity_type: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
            " VALUES (:event_type, :entity_type, '1', now())"
        ),
        {"event_type": event_type, "entity_type": entity_type},
    )


def _refused_by(connection: Connection, constraint: str, statement: Callable[[], None]) -> None:
    """Run ``statement`` inside a savepoint; it must fail on ``constraint``."""
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        statement()
    savepoint.rollback()
    assert constraint in str(raised.value.orig)


# ---------------------------------------------------------------------------
# Head and shape
# ---------------------------------------------------------------------------


def test_head_is_the_phase13_revision(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        assert _version(connection) == _PHASE13_REVISION


def test_workers_table_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(column["name"]): column for column in inspector.get_columns("workers")}
    expected = {
        "id": (sa.Integer, False),
        "name": (sa.Text, False),
        "badge_barcode": (sa.Text, False),
        "avatar_image": (sa.LargeBinary, True),
        "avatar_image_type": (sa.Text, True),
        "avatar_image_updated_at": (sa.DateTime, True),
        "is_active": (sa.Boolean, False),
        "created_at": (sa.DateTime, False),
        "updated_at": (sa.DateTime, False),
    }
    assert set(columns) == set(expected)
    for name, (type_, nullable) in expected.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is nullable, name
    for name in ("avatar_image_updated_at", "created_at", "updated_at"):
        assert getattr(columns[name]["type"], "timezone", None) is True


def test_workers_constraints_have_exact_names(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert inspector.get_pk_constraint("workers")["name"] == "pk_workers"
    uniques = {
        str(unique["name"]): unique["column_names"]
        for unique in inspector.get_unique_constraints("workers")
    }
    assert uniques == {"uq_workers_badge_barcode": ["badge_barcode"]}
    checks = {str(check["name"]) for check in inspector.get_check_constraints("workers")}
    assert checks == _WORKER_CHECKS
    # Only the UNIQUE's own index — no secondary index.
    assert {str(index["name"]) for index in inspector.get_indexes("workers")} <= {
        "uq_workers_badge_barcode"
    }


def test_no_foreign_key_points_from_or_to_workers(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert inspector.get_foreign_keys("workers") == []
    for table in inspector.get_table_names():
        for foreign_key in inspector.get_foreign_keys(table):
            assert foreign_key["referred_table"] != "workers", table


def test_migration_repeats_the_model_badge_check_verbatim() -> None:
    migration = _load_migration()
    assert migration._WORKER_BADGE_BARCODE_SQL == models.WORKER_BADGE_BARCODE_SQL


# ---------------------------------------------------------------------------
# Database CHECKs and UNIQUE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("badge", ["", " X", "X ", "PF:1", "pf:1", "abc1", "Abc1", "A" * 129])
def test_database_refuses_a_non_canonical_badge(connection: Connection, badge: str) -> None:
    _refused_by(
        connection,
        "ck_workers_badge_barcode_canonical",
        lambda: _insert_worker(connection, badge),
    )


def test_database_refuses_a_partial_avatar(connection: Connection) -> None:
    _refused_by(
        connection,
        "ck_workers_avatar_image_shape",
        lambda: _insert_worker(connection, "PARTIAL", avatar_image=b"\x89PNG"),
    )


def test_database_refuses_a_non_image_type(connection: Connection) -> None:
    _refused_by(
        connection,
        "ck_workers_avatar_image_type",
        lambda: _insert_worker(
            connection,
            "GIF",
            avatar_image=b"GIF89a",
            avatar_image_type="image/gif",
            avatar_image_updated_at=datetime.datetime.now(datetime.UTC),
        ),
    )


def test_database_refuses_an_avatar_above_2_mib(connection: Connection) -> None:
    def oversized() -> None:
        connection.execute(
            sa.text(
                "INSERT INTO workers (name, badge_barcode, avatar_image, avatar_image_type,"
                " avatar_image_updated_at) VALUES ('Big', 'BIG',"
                " decode(repeat('00', 2097153), 'hex'), 'image/png', now())"
            )
        )

    _refused_by(connection, "ck_workers_avatar_image_size", oversized)


def test_database_admits_valid_workers(connection: Connection) -> None:
    _insert_worker(connection, "A" * 128)
    _insert_worker(connection, "STRASSE")
    connection.execute(
        sa.text(
            "INSERT INTO workers (name, badge_barcode, avatar_image, avatar_image_type,"
            " avatar_image_updated_at, is_active) VALUES ('Full', 'FULL 1',"
            " decode(repeat('00', 2097152), 'hex'), 'image/webp', now(), false)"
        )
    )
    count = connection.execute(sa.text("SELECT count(*) FROM workers")).scalar_one()
    assert count == 3


def test_database_refuses_a_duplicate_canonical_badge(connection: Connection) -> None:
    """A lowercase variant cannot be stored at all (canonical CHECK), so
    the plain UNIQUE is case-insensitive at the database level."""
    _insert_worker(connection, "ABC1")
    _refused_by(connection, "uq_workers_badge_barcode", lambda: _insert_worker(connection, "ABC1"))
    _refused_by(
        connection,
        "ck_workers_badge_barcode_canonical",
        lambda: _insert_worker(connection, "abc1"),
    )


# ---------------------------------------------------------------------------
# Audit vocabulary
# ---------------------------------------------------------------------------


def test_audit_admits_the_worker_entity_and_the_deleted_event(connection: Connection) -> None:
    _insert_audit(connection, "CREATED", "Worker")
    _insert_audit(connection, "DELETED", "PartNumber")
    _refused_by(
        connection,
        "ck_audit_events_entity_type",
        lambda: _insert_audit(connection, "CREATED", "Machine"),
    )
    _refused_by(
        connection,
        "ck_audit_events_event_type",
        lambda: _insert_audit(connection, "ARCHIVED", "Worker"),
    )


# ---------------------------------------------------------------------------
# Parity and downgrade
# ---------------------------------------------------------------------------


def test_models_metadata_matches_the_migrated_schema(migrated_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def _audit_checks(engine: Engine) -> dict[str, str]:
    return {
        str(check["name"]): str(check["sqltext"])
        for check in inspect(engine).get_check_constraints("audit_events")
    }


def test_downgrade_restores_the_phase12_boundary(admin_engine: Engine) -> None:
    name = "partflow_test_phase13_downgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _PHASE12_REVISION)
        engine = create_engine(url)
        try:
            assert "workers" not in inspect(engine).get_table_names()
            checks = _audit_checks(engine)
            entity_check = checks["ck_audit_events_entity_type"]
            event_check = checks["ck_audit_events_event_type"]
            for entity in ("WorkOrder", "WorkOrderDemand", "PartNumber"):
                assert f"'{entity}'" in entity_check
            assert "'Worker'" not in entity_check
            assert "'CREATED'" in event_check and "'UPDATED'" in event_check
            assert "'DELETED'" not in event_check
            with engine.connect() as connection:
                assert _version(connection) == _PHASE12_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert "workers" in inspect(engine).get_table_names()
            assert "'Worker'" in _audit_checks(engine)["ck_audit_events_entity_type"]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


@pytest.fixture
def refused_database(admin_engine: Engine) -> Iterator[URL]:
    """A database at head, re-created for each refusing-downgrade case."""
    name = "partflow_test_phase13_downgrade_refused"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    command.upgrade(_alembic_config(url), "head")
    yield url
    _drop_temp_database(admin_engine, name)


def _row_counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        workers = connection.execute(sa.text("SELECT count(*) FROM workers")).scalar_one()
        audits = connection.execute(
            sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'Worker'")
        ).scalar_one()
    return int(workers), int(audits)


def test_downgrade_refuses_while_worker_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_worker(connection, "HIST1")
            _insert_audit(connection, "CREATED", "Worker")
        # The re-created Phase 4 entity CHECK refuses the Worker audit row.
        with pytest.raises(IntegrityError, match="ck_audit_events_entity_type"):
            command.downgrade(_alembic_config(refused_database), _PHASE12_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _PHASE13_REVISION
        assert _row_counts(engine) == (1, 1)
    finally:
        engine.dispose()


def test_downgrade_refuses_while_worker_configuration_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_worker(connection, "CONFIG1")
        with pytest.raises(ProgrammingError, match="workers holds Worker configuration"):
            command.downgrade(_alembic_config(refused_database), _PHASE12_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _PHASE13_REVISION
        assert _row_counts(engine) == (1, 0)
    finally:
        engine.dispose()
