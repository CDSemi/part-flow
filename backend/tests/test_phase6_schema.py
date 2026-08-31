"""Integration tests for the Phase 6 Machine assignment / Area completion schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 6 invariants PostgreSQL must enforce
(IMPLEMENTATION_ROADMAP Phase 6; PROJECT_PROFILE §8.6, §8.11, §12;
SLICE1_DATA_MODEL §15/§18):

- exact head boundary: exactly the Phase 3 + 3.5 + 4 tables — Phase 6
  adds no table — and no Phase 7+ column pre-implemented;
- `quantity_flows.current_machine_id`: nullable FK to `machines.id`,
  indexed; `part_movements.source_machine_id` / `destination_machine_id`:
  nullable FKs to `machines.id`;
- `part_movements.command_sequence`: NOT NULL, default 1, positive; the
  row-level idempotency key is now `(device_event_id, command_sequence)`
  — a two-Movement command shares one id, a third row with a reused
  pair is refused;
- the movement-type CHECK admits exactly the five Phase 6 types;
- the per-type shape CHECK: every in-Area type stays in one Area with a
  Station and exactly its Machine reference; RECEIVED and TRANSFERRED
  reference no Machine;
- the stored shape CHECK is the one the model declares (byte-equal
  expression), and models↔migration metadata parity holds at head;
- the new rows are append-only exactly like RECEIVED rows;
- clean downgrade back to the Phase 5 boundary (`0006_phase5_transfer`)
  with a successful re-upgrade, and the downgrade refusing to drop
  Phase 6 history (a Machine-referencing row, and a two-Movement
  command that cannot satisfy the Phase 5 uniqueness).

The module fixture migrates to the Phase 6 revision → base → the
Phase 6 revision, so a downgrade that leaves any object behind fails
the module before any test runs. Every test runs in its own rolled-back
transaction against isolated data.

Pinned to `0007_phase6_machine_assignment` since Phase 7 added
`0008_phase7_direct_processing`; the head-level coverage
(models↔migration parity, exact table set, the shape CHECK equal to the
model's expression) lives in `test_phase7_schema.py`. The Phase 6 shape
assertions below hold at the Phase 6 revision, whose expression the
Phase 7 migration re-declares byte-equal for its downgrade.
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

_PHASE5_REVISION = "0006_phase5_transfer"
_PHASE6_REVISION = "0007_phase6_machine_assignment"

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
    name = "partflow_test_phase6_schema"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    command.upgrade(config, _PHASE6_REVISION)
    command.downgrade(config, "base")
    command.upgrade(config, _PHASE6_REVISION)
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
    """Two Areas (each with an Operation and a Machine), a Station in A, one flow in A."""

    def __init__(self, connection: Connection) -> None:
        department_id = connection.execute(
            sa.insert(models.Department)
            .values(name=_unique("DEPT"))
            .returning(models.Department.id)
        ).scalar_one()
        self.area_a, self.operation_a, self.machine_a = self._area(connection, department_id)
        self.area_b, self.operation_b, self.machine_b = self._area(connection, department_id)
        self.station_id = _unique("ST")
        connection.execute(
            sa.insert(models.ScanStation).values(station_id=self.station_id, area_id=self.area_a)
        )
        self.part_number = _unique("PN")
        self.flow_id = connection.execute(
            sa.insert(models.QuantityFlow)
            .values(part_number=self.part_number, quantity=10, current_area_id=self.area_a)
            .returning(models.QuantityFlow.id)
        ).scalar_one()

    @staticmethod
    def _area(connection: Connection, department_id: int) -> tuple[int, int, int]:
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
        machine_id = connection.execute(
            sa.insert(models.Machine)
            .values(area_id=area_id, name=_unique("MACHINE"), asset_tag=_unique("CD"))
            .returning(models.Machine.id)
        ).scalar_one()
        return area_id, operation_id, machine_id

    def movement(self, movement_type: str, **overrides: Any) -> dict[str, Any]:
        """The canonical shape of each type; overrides break it on purpose."""
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, Any] = {
            "quantity_flow_id": self.flow_id,
            "part_number": self.part_number,
            "movement_type": movement_type,
            "quantity": 10,
            "from_area_id": self.area_a,
            "to_area_id": self.area_a,
            "operation_id": self.operation_a,
            "station_id": self.station_id,
            "source_machine_id": None,
            "destination_machine_id": None,
            "occurred_at": now,
            "server_received_at": now,
            "device_event_id": str(uuid.uuid4()),
        }
        if movement_type == "RECEIVED":
            values.update(from_area_id=None, station_id=None)
        elif movement_type == "TRANSFERRED":
            values.update(to_area_id=self.area_b, operation_id=self.operation_b)
        elif movement_type == "ASSIGNED_TO_MACHINE":
            values.update(destination_machine_id=self.machine_a)
        else:  # RELEASED_FROM_MACHINE / AREA_COMPLETED
            values.update(source_machine_id=self.machine_a)
        values.update(overrides)
        return values


def _insert(connection: Connection, values: dict[str, Any]) -> int:
    return connection.execute(
        sa.insert(models.PartMovement).values(**values).returning(models.PartMovement.id)
    ).scalar_one()


def _rejected(connection: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        _insert(connection, values)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "phase6_machine_assignment_migration",
        _BACKEND_DIR / "alembic" / "versions" / "20260826_0007_phase6_machine_assignment.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationSchema:
    def test_phase6_creates_exactly_the_phase6_boundary(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        # Phase 6 adds no table: no workers, scan_sessions, or
        # work_order_allocations pre-implemented.
        assert tables == _EXPECTED_TABLES | {"alembic_version"}

    def test_no_phase7_plus_columns_exist(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        area_columns = {column["name"] for column in inspector.get_columns("areas")}
        assert "current_machine_id" in flow_columns
        assert "parent_flow_id" not in flow_columns
        assert {"source_machine_id", "destination_machine_id", "command_sequence"} <= (
            movement_columns
        )
        assert movement_columns.isdisjoint(
            {
                "worker_id",
                "scan_session_id",
                "machine_id",
                "movement_reason",
                "reverses_movement_id",
            }
        )
        assert "worker_identification_mode" not in area_columns

    def test_machine_reference_columns_shape(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {c["name"]: c for c in inspector.get_columns("quantity_flows")}
        assert flow_columns["current_machine_id"]["nullable"] is True
        flow_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("quantity_flows")}
        machine_fk = flow_fks["fk_quantity_flows_current_machine_id_machines"]
        assert machine_fk["constrained_columns"] == ["current_machine_id"]
        assert machine_fk["referred_table"] == "machines"
        indexes = {index["name"] for index in inspector.get_indexes("quantity_flows")}
        assert "ix_quantity_flows_current_machine_id" in indexes

        movement_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("part_movements")}
        for name, column in (
            ("fk_part_movements_source_machine_id_machines", "source_machine_id"),
            ("fk_part_movements_destination_machine_id_machines", "destination_machine_id"),
        ):
            assert movement_fks[name]["constrained_columns"] == [column]
            assert movement_fks[name]["referred_table"] == "machines"

    def test_command_sequence_column_and_idempotency_key(self, connection: Connection) -> None:
        inspector = inspect(connection)
        column = next(
            c for c in inspector.get_columns("part_movements") if c["name"] == "command_sequence"
        )
        assert column["nullable"] is False
        assert column["default"] == "1"
        uniques = {u["name"]: u for u in inspector.get_unique_constraints("part_movements")}
        assert "uq_part_movements_device_event_id" not in uniques
        assert uniques["uq_part_movements_device_event_id_command_sequence"]["column_names"] == [
            "device_event_id",
            "command_sequence",
        ]

        seed = _Seed(connection)
        event_id = str(uuid.uuid4())
        # A two-Movement command shares one device_event_id.
        _insert(connection, seed.movement("AREA_COMPLETED", device_event_id=event_id))
        _insert(
            connection,
            seed.movement("TRANSFERRED", device_event_id=event_id, command_sequence=2),
        )
        # A reused (id, sequence) pair is refused; sequence 0 is refused.
        _rejected(connection, seed.movement("TRANSFERRED", device_event_id=event_id))
        _rejected(connection, seed.movement("AREA_COMPLETED", command_sequence=0))
        # Omitting the sequence defaults it to 1.
        movement_id = _insert(connection, seed.movement("ASSIGNED_TO_MACHINE"))
        sequence = connection.execute(
            sa.select(models.PartMovement.__table__.c.command_sequence).where(
                models.PartMovement.__table__.c.id == movement_id
            )
        ).scalar_one()
        assert sequence == 1

    def test_movement_type_check_admits_exactly_the_phase6_types(
        self, connection: Connection
    ) -> None:
        check_clause = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_part_movements_movement_type'"
            )
        ).scalar_one()
        for present in (
            "RECEIVED",
            "TRANSFERRED",
            "ASSIGNED_TO_MACHINE",
            "RELEASED_FROM_MACHINE",
            "AREA_COMPLETED",
        ):
            assert present in check_clause
        for absent in ("SPLIT", "MERGED", "REVERSED", "STOCKED", "SCRAPPED"):
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
        assert "ck_part_movements_command_sequence_positive" in names
        # At the Phase 6 revision a completion still needs its Machine
        # (Phase 7 widened the model's expression; the head-level
        # byte-equality lives in test_phase7_schema).
        module = _load_migration()
        stored = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_part_movements_movement_shape'"
            )
        ).scalar_one()
        assert "source_machine_id IS NOT NULL" in stored
        assert module._PHASE6_EVENT_UNIQUE == models.DEVICE_EVENT_ID_CONSTRAINT

    def test_downgrade_restores_the_phase5_boundary(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase6_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, _PHASE6_REVISION)
            command.downgrade(config, _PHASE5_REVISION)
            engine = create_engine(url)
            try:
                inspector = inspect(engine)
                assert set(inspector.get_table_names()) == _EXPECTED_TABLES | {"alembic_version"}
                movement_columns = {c["name"] for c in inspector.get_columns("part_movements")}
                assert movement_columns.isdisjoint(
                    {"source_machine_id", "destination_machine_id", "command_sequence"}
                )
                assert "current_machine_id" not in {
                    c["name"] for c in inspector.get_columns("quantity_flows")
                }
                uniques = {u["name"] for u in inspector.get_unique_constraints("part_movements")}
                assert "uq_part_movements_device_event_id" in uniques
                assert "uq_part_movements_device_event_id_command_sequence" not in uniques
                with engine.connect() as connection:
                    checks = {
                        cname: definition
                        for cname, definition in connection.execute(
                            sa.text(
                                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                                " WHERE conrelid = 'part_movements'::regclass AND contype = 'c'"
                            )
                        )
                    }
                assert "ck_part_movements_command_sequence_positive" not in checks
                assert "AREA_COMPLETED" not in checks["ck_part_movements_movement_type"]
                assert "machine" not in checks["ck_part_movements_movement_shape"]
            finally:
                engine.dispose()
            command.upgrade(config, _PHASE6_REVISION)
        finally:
            _drop_temp_database(admin_engine, name)

    @pytest.mark.parametrize("history", ["machine_row", "two_movement_command"])
    def test_downgrade_refuses_to_drop_phase6_history(
        self, admin_engine: Engine, history: str
    ) -> None:
        """A database holding Phase 6 history — a Machine-referencing row,
        or a two-Movement command under one id — cannot satisfy the
        Phase 5 constraints: the downgrade fails loudly and leaves the
        schema and the history exactly as they were."""
        name = f"partflow_test_phase6_downgrade_{history}"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, _PHASE6_REVISION)
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    if history == "machine_row":
                        movement_id = _insert(connection, seed.movement("ASSIGNED_TO_MACHINE"))
                    else:
                        event_id = str(uuid.uuid4())
                        _insert(
                            connection,
                            seed.movement("AREA_COMPLETED", device_event_id=event_id),
                        )
                        movement_id = _insert(
                            connection,
                            seed.movement(
                                "TRANSFERRED", device_event_id=event_id, command_sequence=2
                            ),
                        )
                    connection.commit()
                with pytest.raises(DBAPIError):
                    command.downgrade(config, _PHASE5_REVISION)
                with engine.connect() as connection:
                    table = models.PartMovement.__table__
                    assert (
                        connection.execute(
                            sa.select(sa.func.count())
                            .select_from(table)
                            .where(table.c.id == movement_id)
                        ).scalar_one()
                        == 1
                    )
                    version = connection.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                assert version == _PHASE6_REVISION
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestMovementShape:
    @pytest.mark.parametrize(
        "movement_type",
        [
            "RECEIVED",
            "TRANSFERRED",
            "ASSIGNED_TO_MACHINE",
            "RELEASED_FROM_MACHINE",
            "AREA_COMPLETED",
        ],
    )
    def test_canonical_shape_of_every_type_is_accepted(
        self, connection: Connection, movement_type: str
    ) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement(movement_type))
        table = models.PartMovement.__table__
        # Column-explicit: the model has later-phase columns the pinned
        # Phase 6 schema does not.
        row = connection.execute(
            sa.select(table.c.movement_type, table.c.command_sequence).where(
                table.c.id == movement_id
            )
        ).one()
        assert row.movement_type == movement_type
        assert row.command_sequence == 1

    def test_received_and_transferred_never_reference_a_machine(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement("RECEIVED", destination_machine_id=seed.machine_a))
        _rejected(connection, seed.movement("RECEIVED", source_machine_id=seed.machine_a))
        _rejected(connection, seed.movement("TRANSFERRED", source_machine_id=seed.machine_a))
        _rejected(connection, seed.movement("TRANSFERRED", destination_machine_id=seed.machine_b))

    def test_assignment_carries_exactly_a_destination_machine(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement("ASSIGNED_TO_MACHINE", destination_machine_id=None))
        _rejected(
            connection, seed.movement("ASSIGNED_TO_MACHINE", source_machine_id=seed.machine_a)
        )
        _rejected(connection, seed.movement("ASSIGNED_TO_MACHINE", destination_machine_id=-1))

    @pytest.mark.parametrize("movement_type", ["RELEASED_FROM_MACHINE", "AREA_COMPLETED"])
    def test_release_and_completion_carry_exactly_a_source_machine(
        self, connection: Connection, movement_type: str
    ) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement(movement_type, source_machine_id=None))
        _rejected(connection, seed.movement(movement_type, destination_machine_id=seed.machine_a))
        _rejected(connection, seed.movement(movement_type, source_machine_id=-1))

    @pytest.mark.parametrize(
        "movement_type", ["ASSIGNED_TO_MACHINE", "RELEASED_FROM_MACHINE", "AREA_COMPLETED"]
    )
    def test_in_area_types_stay_in_one_area_at_a_station(
        self, connection: Connection, movement_type: str
    ) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement(movement_type, to_area_id=seed.area_b))
        _rejected(connection, seed.movement(movement_type, from_area_id=None))
        _rejected(connection, seed.movement(movement_type, station_id=None))

    @pytest.mark.parametrize("invalid", ["SPLIT", "MERGED", "REVERSED", "STOCKED", ""])
    def test_later_phase_movement_types_are_rejected(
        self, connection: Connection, invalid: str
    ) -> None:
        seed = _Seed(connection)
        _rejected(connection, {**seed.movement("AREA_COMPLETED"), "movement_type": invalid})

    @pytest.mark.parametrize(
        "movement_type", ["ASSIGNED_TO_MACHINE", "RELEASED_FROM_MACHINE", "AREA_COMPLETED"]
    )
    def test_phase6_rows_are_append_only(self, connection: Connection, movement_type: str) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement(movement_type))
        for statement in (
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(source_machine_id=None, destination_machine_id=None),
            sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)

    def test_flow_projection_accepts_only_existing_machines(self, connection: Connection) -> None:
        seed = _Seed(connection)
        connection.execute(
            sa.update(models.QuantityFlow)
            .where(models.QuantityFlow.id == seed.flow_id)
            .values(current_machine_id=seed.machine_a)
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                sa.update(models.QuantityFlow)
                .where(models.QuantityFlow.id == seed.flow_id)
                .values(current_machine_id=-1)
            )
