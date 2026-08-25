"""Integration tests for the Phase 5 Movement widening schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 5 invariants PostgreSQL must enforce
(IMPLEMENTATION_ROADMAP Phase 5; SLICE1_DATA_MODEL §18):

- exact head boundary: exactly the Phase 3 + 3.5 + 4 tables — Phase 5
  adds no table — and no Phase 6+ column pre-implemented;
- the movement-type CHECK admits exactly `RECEIVED` and `TRANSFERRED`;
- `part_movements.station_id`: nullable, a foreign key to
  `scan_stations.station_id`, NULL on a RECEIVED release;
- the per-type shape CHECK: RECEIVED has no source Area; TRANSFERRED
  needs a source Area different from its destination and a Station;
- the stored shape CHECK is the one the model declares (byte-equal
  expression), and models↔migration metadata parity holds at head;
- TRANSFERRED rows are append-only exactly like RECEIVED rows;
- clean downgrade back to the Phase 4 boundary
  (`0005_phase4_release_index`) with a successful re-upgrade, and the
  downgrade refusing to drop TRANSFERRED history.

The module fixture migrates head → base → head, so a downgrade that
leaves any object behind fails the module before any test runs. Every
test runs in its own rolled-back transaction against isolated data.

Phase 5 is the current head, so this module carries the head-level
coverage (models↔migration parity, exact table set). When a later phase
adds its migration, pin this module to `0006_phase5_transfer` and move
the head-level coverage into that phase's schema test.
"""

import datetime
import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent

_PHASE4_REVISION = "0005_phase4_release_index"

_EXPECTED_TABLES = {
    "departments",
    "areas",
    "operations",
    "part_numbers",
    "work_orders",
    "work_order_demands",
    "route_templates",
    "route_steps",
    "assigned_routes",
    "assigned_route_steps",
    "quantity_flows",
    "part_movements",
    "scan_stations",
    "machines",
    "machine_lifecycle_events",
    "machine_asset_tag_config",
    "audit_events",
}


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
    name = "partflow_test_phase5_schema"
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


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class _Seed:
    """Two Areas (each with an Operation), a Scan Station, one flow."""

    def __init__(self, connection: Connection) -> None:
        department_id = connection.execute(
            sa.insert(models.Department)
            .values(name=_unique("DEPT"))
            .returning(models.Department.id)
        ).scalar_one()
        self.area_a, self.operation_a = self._area(connection, department_id)
        self.area_b, self.operation_b = self._area(connection, department_id)
        self.station_id = _unique("ST")
        connection.execute(
            sa.insert(models.ScanStation).values(station_id=self.station_id, area_id=self.area_b)
        )
        self.part_number = _unique("PN")
        self.flow_id = connection.execute(
            sa.insert(models.QuantityFlow)
            .values(part_number=self.part_number, quantity=10, current_area_id=self.area_a)
            .returning(models.QuantityFlow.id)
        ).scalar_one()

    @staticmethod
    def _area(connection: Connection, department_id: int) -> tuple[int, int]:
        area_id = connection.execute(
            sa.insert(models.Area)
            .values(department_id=department_id, name=_unique("AREA"))
            .returning(models.Area.id)
        ).scalar_one()
        operation_id = connection.execute(
            sa.insert(models.Operation)
            .values(area_id=area_id, code=_unique("OP"))
            .returning(models.Operation.id)
        ).scalar_one()
        return area_id, operation_id

    def movement(self, **overrides: Any) -> dict[str, Any]:
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, Any] = {
            "quantity_flow_id": self.flow_id,
            "part_number": self.part_number,
            "movement_type": "TRANSFERRED",
            "quantity": 10,
            "from_area_id": self.area_a,
            "to_area_id": self.area_b,
            "operation_id": self.operation_b,
            "station_id": self.station_id,
            "occurred_at": now,
            "server_received_at": now,
            "device_event_id": str(uuid.uuid4()),
        }
        values.update(overrides)
        return values


def _insert(connection: Connection, values: dict[str, Any]) -> int:
    return connection.execute(
        sa.insert(models.PartMovement).values(**values).returning(models.PartMovement.id)
    ).scalar_one()


def _rejected(connection: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        _insert(connection, values)


class TestMigrationSchema:
    def test_head_creates_exactly_the_phase5_boundary(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        # Phase 5 adds no table: no workers, scan_sessions, or
        # work_order_allocations pre-implemented.
        assert tables == _EXPECTED_TABLES | {"alembic_version"}

    def test_no_phase6_plus_columns_exist(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        area_columns = {column["name"] for column in inspector.get_columns("areas")}
        assert "current_machine_id" not in flow_columns
        assert "parent_flow_id" not in flow_columns
        assert "station_id" in movement_columns
        assert movement_columns.isdisjoint(
            {
                "worker_id",
                "scan_session_id",
                "machine_id",
                "source_machine_id",
                "destination_machine_id",
                "movement_reason",
                "reverses_movement_id",
            }
        )
        assert "worker_identification_mode" not in area_columns

    def test_station_id_column_shape(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        column = next(
            column
            for column in inspector.get_columns("part_movements")
            if column["name"] == "station_id"
        )
        assert isinstance(column["type"], sa.Text)
        assert column["nullable"] is True
        foreign_keys = {fk["name"]: fk for fk in inspector.get_foreign_keys("part_movements")}
        station_fk = foreign_keys["fk_part_movements_station_id_scan_stations"]
        assert station_fk["constrained_columns"] == ["station_id"]
        assert station_fk["referred_table"] == "scan_stations"
        assert station_fk["referred_columns"] == ["station_id"]

    def test_movement_type_check_admits_exactly_received_and_transferred(
        self, connection: Connection
    ) -> None:
        check_clause = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_part_movements_movement_type'"
            )
        ).scalar_one()
        assert "RECEIVED" in check_clause and "TRANSFERRED" in check_clause
        for absent in ("ASSIGNED_TO_MACHINE", "AREA_COMPLETED", "SPLIT", "REVERSED", "STOCKED"):
            assert absent not in check_clause

    def test_shape_check_is_the_one_the_model_declares(self, connection: Connection) -> None:
        names = set(
            connection.scalars(
                sa.text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE conrelid = 'part_movements'::regclass AND contype = 'c'"
                )
            )
        )
        assert "ck_part_movements_movement_shape" in names
        assert "ck_part_movements_received_shape" not in names
        # Byte-equal to the mapping's expression: the migration inlines
        # it deliberately (never importing the mutable model module).
        spec = importlib.util.spec_from_file_location(
            "phase5_transfer_migration",
            _BACKEND_DIR / "alembic" / "versions" / "20260825_0006_phase5_transfer.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module._PHASE5_SHAPE == models.MOVEMENT_SHAPE_SQL

    def test_models_metadata_matches_the_migrated_schema(self, migrated_engine: Engine) -> None:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with migrated_engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diffs = compare_metadata(context, models.Base.metadata)
        assert diffs == []

    def test_downgrade_restores_the_phase4_boundary(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase5_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE4_REVISION)
            engine = create_engine(url)
            try:
                inspector = inspect(engine)
                assert set(inspector.get_table_names()) == _EXPECTED_TABLES | {"alembic_version"}
                columns = {column["name"] for column in inspector.get_columns("part_movements")}
                assert "station_id" not in columns
                with engine.connect() as connection:
                    checks = {
                        name: definition
                        for name, definition in connection.execute(
                            sa.text(
                                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                                " WHERE conrelid = 'part_movements'::regclass AND contype = 'c'"
                            )
                        )
                    }
                assert "ck_part_movements_received_shape" in checks
                assert "ck_part_movements_movement_shape" not in checks
                assert "TRANSFERRED" not in checks["ck_part_movements_movement_type"]
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)

    def test_downgrade_refuses_to_drop_transferred_history(self, admin_engine: Engine) -> None:
        """A database holding TRANSFERRED rows cannot satisfy the Phase 4
        checks: the downgrade fails loudly and leaves the schema — and
        the history — exactly as it was."""
        name = "partflow_test_phase5_downgrade_history"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    movement_id = _insert(connection, seed.movement())
                    connection.commit()
                with pytest.raises(DBAPIError):
                    command.downgrade(config, _PHASE4_REVISION)
                with engine.connect() as connection:
                    assert (
                        connection.execute(
                            sa.select(models.PartMovement.__table__.c.station_id).where(
                                models.PartMovement.__table__.c.id == movement_id
                            )
                        ).scalar_one()
                        == seed.station_id
                    )
                    version = connection.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                assert version == "0006_phase5_transfer"
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestMovementShape:
    def test_transferred_canonical_shape_is_accepted(self, connection: Connection) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement())
        row = connection.execute(
            sa.select(models.PartMovement.__table__).where(
                models.PartMovement.__table__.c.id == movement_id
            )
        ).one()
        assert row.movement_type == "TRANSFERRED"
        assert row.from_area_id == seed.area_a and row.to_area_id == seed.area_b
        assert row.station_id == seed.station_id

    def test_received_keeps_no_source_area_and_no_station_requirement(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        _insert(
            connection,
            seed.movement(movement_type="RECEIVED", from_area_id=None, station_id=None),
        )
        _rejected(connection, seed.movement(movement_type="RECEIVED", station_id=None))

    def test_transferred_requires_a_different_source_area(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement(from_area_id=None))
        _rejected(connection, seed.movement(from_area_id=seed.area_b))

    def test_transferred_requires_an_existing_station(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement(station_id=None))
        _rejected(connection, seed.movement(station_id="NO-SUCH-STATION"))

    @pytest.mark.parametrize("invalid", ["AREA_COMPLETED", "ASSIGNED_TO_MACHINE", "SPLIT", ""])
    def test_later_phase_movement_types_are_rejected(
        self, connection: Connection, invalid: str
    ) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement(movement_type=invalid))

    def test_transferred_rows_are_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement())
        for statement in (
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(to_area_id=seed.area_a),
            sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)
