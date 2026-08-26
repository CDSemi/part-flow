"""Integration tests for the Phase 7 direct-processing schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 7 invariants PostgreSQL must enforce
(IMPLEMENTATION_ROADMAP Phase 7; PROJECT_PROFILE §7 Area Completion,
§8.11, §12 Area Without Machines; SLICE1_DATA_MODEL §18):

- exact head boundary: exactly the Phase 3 + 3.5 + 4 tables — Phase 7
  adds no table and no column (the holding state `PROCESSING` is
  derived, never stored; no `machine_assignment_mode` and no
  `worker_identification_mode` exist), and no Phase 8+ column;
- the `AREA_COMPLETED` shape widened: a completion may carry NO source
  Machine (direct processing) or its source Machine (Machine Area), and
  never a destination Machine; it still stays in ONE Area at a Station;
- every other type's shape unchanged: RECEIVED / TRANSFERRED never
  reference a Machine, assignment needs exactly a destination Machine,
  release exactly a source Machine;
- the stored shape CHECK is the one the model declares (byte-equal
  expression), the Phase 6 expression the migration re-declares for its
  downgrade equals the Phase 6 migration's, and models↔migration
  metadata parity holds at head;
- a Machine-less completion is append-only like every Movement;
- clean downgrade back to the Phase 6 boundary with a successful
  re-upgrade, and the downgrade refusing to drop Phase 7 history (a
  completion without a Machine cannot satisfy the Phase 6 shape).

Phase 7 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to
`0008_phase7_direct_processing` and move the head-level coverage into
that phase's schema test.
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
_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"

_PHASE6_REVISION = "0007_phase6_machine_assignment"
_PHASE7_REVISION = "0008_phase7_direct_processing"

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
    name = "partflow_test_phase7_schema"
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
    """Area A (an Operation, a Machine) and Area B (an Operation, NO Machine),
    a Station in each, one flow in A and one in B."""

    def __init__(self, connection: Connection) -> None:
        department_id = connection.execute(
            sa.insert(models.Department)
            .values(name=_unique("DEPT"))
            .returning(models.Department.id)
        ).scalar_one()
        self.area_a, self.operation_a = self._area(connection, department_id)
        self.machine_a = connection.execute(
            sa.insert(models.Machine)
            .values(area_id=self.area_a, name=_unique("MACHINE"), asset_tag=_unique("CD"))
            .returning(models.Machine.id)
        ).scalar_one()
        self.area_b, self.operation_b = self._area(connection, department_id)
        self.station_a = self._station(connection, self.area_a)
        self.station_b = self._station(connection, self.area_b)
        self.part_number = _unique("PN")
        self.flow_a = self._flow(connection, self.area_a)
        self.flow_b = self._flow(connection, self.area_b)

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

    @staticmethod
    def _station(connection: Connection, area_id: int) -> str:
        station_id = _unique("ST")
        connection.execute(
            sa.insert(models.ScanStation).values(station_id=station_id, area_id=area_id)
        )
        return station_id

    def _flow(self, connection: Connection, area_id: int) -> int:
        return connection.execute(
            sa.insert(models.QuantityFlow)
            .values(part_number=self.part_number, quantity=10, current_area_id=area_id)
            .returning(models.QuantityFlow.id)
        ).scalar_one()

    def movement(
        self, movement_type: str, *, direct: bool = False, **overrides: Any
    ) -> dict[str, Any]:
        """The canonical shape of each type; ``direct`` is the Machine-less
        completion in Area B; overrides break the shape on purpose."""
        now = datetime.datetime.now(datetime.UTC)
        area = self.area_b if direct else self.area_a
        values: dict[str, Any] = {
            "quantity_flow_id": self.flow_b if direct else self.flow_a,
            "part_number": self.part_number,
            "movement_type": movement_type,
            "quantity": 10,
            "from_area_id": area,
            "to_area_id": area,
            "operation_id": self.operation_b if direct else self.operation_a,
            "station_id": self.station_b if direct else self.station_a,
            "source_machine_id": None,
            "destination_machine_id": None,
            "occurred_at": now,
            "server_received_at": now,
            "device_event_id": str(uuid.uuid4()),
        }
        if movement_type == "RECEIVED":
            values.update(from_area_id=None, station_id=None)
        elif movement_type == "TRANSFERRED":
            values.update(
                to_area_id=self.area_a if direct else self.area_b,
                operation_id=self.operation_a if direct else self.operation_b,
            )
        elif movement_type == "ASSIGNED_TO_MACHINE":
            values.update(destination_machine_id=self.machine_a)
        elif movement_type == "RELEASED_FROM_MACHINE" or not direct:
            # RELEASED_FROM_MACHINE, and the Machine-Area AREA_COMPLETED.
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


def _load_migration(file_name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, _VERSIONS_DIR / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shape_check(connection: Connection) -> str:
    return str(
        connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_part_movements_movement_shape'"
            )
        ).scalar_one()
    )


class TestMigrationSchema:
    def test_head_creates_exactly_the_phase7_boundary(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        # Phase 7 adds no table: no workers, scan_sessions, or
        # work_order_allocations pre-implemented.
        assert tables == _EXPECTED_TABLES | {"alembic_version"}

    def test_phase7_adds_no_column_and_no_phase8_plus_column(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        area_columns = {column["name"] for column in inspector.get_columns("areas")}
        # The holding state is derived: nothing stores PROCESSING.
        assert flow_columns.isdisjoint({"processing_state", "parent_flow_id"})
        assert movement_columns.isdisjoint(
            {
                "processing_state",
                "worker_id",
                "scan_session_id",
                "machine_id",
                "movement_reason",
                "reverses_movement_id",
            }
        )
        # Exactly two Area modes, following from the Area's Machines —
        # no configured mode; Worker identification stays with Workers.
        assert area_columns.isdisjoint(
            {"machine_assignment_mode", "assignment_mode", "worker_identification_mode"}
        )

    def test_movement_type_check_is_unchanged(self, connection: Connection) -> None:
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
        for absent in ("SPLIT", "MERGED", "REVERSED", "STOCKED", "SCRAPPED", "PROCESSING"):
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
        # Byte-equal to the mapping's expression: the migration inlines
        # it deliberately (never importing the mutable model module),
        # and re-declares the Phase 6 expression for its downgrade.
        phase7 = _load_migration(
            "20260826_0008_phase7_direct_processing.py", "phase7_direct_processing_migration"
        )
        phase6 = _load_migration(
            "20260826_0007_phase6_machine_assignment.py", "phase6_machine_assignment_migration"
        )
        assert phase7._PHASE7_SHAPE == models.MOVEMENT_SHAPE_SQL
        assert phase7._PHASE6_SHAPE == phase6._PHASE6_SHAPE
        assert phase7._PHASE7_SHAPE != phase7._PHASE6_SHAPE
        # The widening is exactly the completion's source Machine.
        stored = _shape_check(connection)
        assert stored.count("source_machine_id IS NOT NULL") == 1  # RELEASED only

    def test_models_metadata_matches_the_migrated_schema(self, migrated_engine: Engine) -> None:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with migrated_engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diffs = compare_metadata(context, models.Base.metadata)
        assert diffs == []

    def test_downgrade_restores_the_phase6_boundary(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase7_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE6_REVISION)
            engine = create_engine(url)
            try:
                assert set(inspect(engine).get_table_names()) == _EXPECTED_TABLES | {
                    "alembic_version"
                }
                with engine.connect() as connection:
                    # A completion needs its Machine again.
                    assert _shape_check(connection).count("source_machine_id IS NOT NULL") == 2
                    seed = _Seed(connection)
                    _rejected(connection, seed.movement("AREA_COMPLETED", direct=True))
                    _insert(connection, seed.movement("AREA_COMPLETED"))
                    connection.rollback()
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)

    def test_downgrade_refuses_to_drop_phase7_history(self, admin_engine: Engine) -> None:
        """A database holding a direct-processing completion (no Machine)
        cannot satisfy the Phase 6 shape: the downgrade fails loudly and
        leaves the schema and the history exactly as they were."""
        name = "partflow_test_phase7_downgrade_history"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    movement_id = _insert(connection, seed.movement("AREA_COMPLETED", direct=True))
                    connection.commit()
                with pytest.raises(DBAPIError):
                    command.downgrade(config, _PHASE6_REVISION)
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
                    assert version == _PHASE7_REVISION
                    assert _shape_check(connection).count("source_machine_id IS NOT NULL") == 1
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestMovementShape:
    @pytest.mark.parametrize(
        ("movement_type", "direct"),
        [
            ("RECEIVED", False),
            ("TRANSFERRED", False),
            ("TRANSFERRED", True),
            ("ASSIGNED_TO_MACHINE", False),
            ("RELEASED_FROM_MACHINE", False),
            ("AREA_COMPLETED", False),
            ("AREA_COMPLETED", True),
        ],
    )
    def test_canonical_shape_of_every_type_is_accepted(
        self, connection: Connection, movement_type: str, direct: bool
    ) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement(movement_type, direct=direct))
        table = models.PartMovement.__table__
        row = connection.execute(sa.select(table).where(table.c.id == movement_id)).one()
        assert row.movement_type == movement_type
        assert row.command_sequence == 1
        if movement_type == "AREA_COMPLETED":
            assert row.source_machine_id == (None if direct else seed.machine_a)
            assert row.destination_machine_id is None

    def test_completion_accepts_a_source_machine_or_none_never_a_destination(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        # Direct processing: no Machine at all.
        _insert(connection, seed.movement("AREA_COMPLETED", direct=True))
        # Machine Area: the source Machine it left.
        _insert(connection, seed.movement("AREA_COMPLETED"))
        # A destination Machine never belongs to a completion.
        _rejected(
            connection,
            seed.movement("AREA_COMPLETED", direct=True, destination_machine_id=seed.machine_a),
        )
        _rejected(
            connection, seed.movement("AREA_COMPLETED", destination_machine_id=seed.machine_a)
        )
        # The FK still guards a referenced Machine.
        _rejected(connection, seed.movement("AREA_COMPLETED", source_machine_id=-1))

    def test_direct_completion_stays_in_one_area_at_a_station(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement("AREA_COMPLETED", direct=True, to_area_id=seed.area_a))
        _rejected(connection, seed.movement("AREA_COMPLETED", direct=True, from_area_id=None))
        _rejected(connection, seed.movement("AREA_COMPLETED", direct=True, station_id=None))

    def test_other_types_keep_their_phase6_shape(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _rejected(connection, seed.movement("RECEIVED", source_machine_id=seed.machine_a))
        _rejected(connection, seed.movement("TRANSFERRED", source_machine_id=seed.machine_a))
        _rejected(connection, seed.movement("TRANSFERRED", direct=True, from_area_id=seed.area_a))
        _rejected(connection, seed.movement("ASSIGNED_TO_MACHINE", destination_machine_id=None))
        # A release without a Machine is NOT widened: only a completion is.
        _rejected(connection, seed.movement("RELEASED_FROM_MACHINE", source_machine_id=None))

    def test_direct_completion_is_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement("AREA_COMPLETED", direct=True))
        for statement in (
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(source_machine_id=seed.machine_a),
            sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)
