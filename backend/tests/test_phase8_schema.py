"""Integration tests for the Phase 8 quantity-lineage schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 8 invariants PostgreSQL must enforce
(IMPLEMENTATION_ROADMAP Phase 8; PROJECT_PROFILE §8.7, §8.11, §11;
SLICE1_DATA_MODEL §18):

- exact head boundary: the Phase 3–7 tables plus exactly
  `quantity_flow_lineage`; no `parent_flow_id` column (N → 1 could not
  live in one column), no Phase 9+ column;
- the movement-type CHECK admits `SPLIT` and `MERGED` and nothing
  later; the shape CHECK pins a lineage event to ONE Area at a Station
  with NO Machine; the stored shape CHECK is byte-equal to the model's
  expression, and the Phase 7 expression the migration re-declares for
  its downgrade equals the Phase 7 migration's;
- the QuantityFlow lifecycle: `status` limited to ACTIVE / SPLIT /
  MERGED, `closed_at` set exactly for a closed flow;
- the lineage edge table: relation CHECK, distinct parent/child, one
  edge per (parent, child), both FKs, append-only trigger;
- models↔migration metadata parity at head;
- clean downgrade back to the Phase 7 boundary with a successful
  re-upgrade, and the downgrade refusing to drop Phase 8 history.

Phase 8 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to
`0009_phase8_split_merge` and move the head-level coverage into that
phase's schema test.
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

_PHASE7_REVISION = "0008_phase7_direct_processing"
_PHASE8_REVISION = "0009_phase8_split_merge"

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
    "quantity_flow_lineage",
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
    name = "partflow_test_phase8_schema"
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
    """One Area with an Operation, a Machine and a Station; two flows of one PN."""

    def __init__(self, connection: Connection) -> None:
        department_id = connection.execute(
            sa.insert(models.Department)
            .values(name=_unique("DEPT"))
            .returning(models.Department.id)
        ).scalar_one()
        self.area = connection.execute(
            sa.insert(models.Area)
            .values(department_id=department_id, name=_unique("AREA"))
            .returning(models.Area.id)
        ).scalar_one()
        self.other_area = connection.execute(
            sa.insert(models.Area)
            .values(department_id=department_id, name=_unique("AREA"))
            .returning(models.Area.id)
        ).scalar_one()
        self.operation = connection.execute(
            sa.insert(models.Operation)
            .values(area_id=self.area, code=_unique("OP"))
            .returning(models.Operation.id)
        ).scalar_one()
        self.machine = connection.execute(
            sa.insert(models.Machine)
            .values(area_id=self.area, name=_unique("MACHINE"), asset_tag=_unique("CD"))
            .returning(models.Machine.id)
        ).scalar_one()
        self.station = _unique("ST")
        connection.execute(
            sa.insert(models.ScanStation).values(station_id=self.station, area_id=self.area)
        )
        self.part_number = _unique("PN")
        self.flow = self._flow(connection)
        self.child = self._flow(connection)

    def _flow(self, connection: Connection) -> int:
        return connection.execute(
            sa.insert(models.QuantityFlow)
            .values(part_number=self.part_number, quantity=10, current_area_id=self.area)
            .returning(models.QuantityFlow.id)
        ).scalar_one()

    def movement(self, movement_type: str, **overrides: Any) -> dict[str, Any]:
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, Any] = {
            "quantity_flow_id": self.flow,
            "part_number": self.part_number,
            "movement_type": movement_type,
            "quantity": 10,
            "from_area_id": self.area,
            "to_area_id": self.area,
            "operation_id": self.operation,
            "station_id": self.station,
            "source_machine_id": None,
            "destination_machine_id": None,
            "occurred_at": now,
            "server_received_at": now,
            "device_event_id": str(uuid.uuid4()),
        }
        values.update(overrides)
        return values

    def edge(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "relation": "SPLIT",
            "parent_flow_id": self.flow,
            "child_flow_id": self.child,
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


def _insert_edge(connection: Connection, values: dict[str, Any]) -> int:
    return connection.execute(
        sa.insert(models.QuantityFlowLineage)
        .values(**values)
        .returning(models.QuantityFlowLineage.id)
    ).scalar_one()


def _edge_rejected(connection: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        _insert_edge(connection, values)


def _load_migration(file_name: str, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, _VERSIONS_DIR / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint(connection: Connection, name: str) -> str:
    return str(
        connection.execute(
            sa.text(f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = '{name}'")
        ).scalar_one()
    )


class TestMigrationSchema:
    def test_head_creates_exactly_the_phase8_boundary(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        assert tables == _EXPECTED_TABLES | {"alembic_version"}

    def test_phase8_adds_no_parent_column_and_no_phase9_plus_column(
        self, migrated_engine: Engine
    ) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        edge_columns = {column["name"] for column in inspector.get_columns("quantity_flow_lineage")}
        # Lineage is the edge table, never a single parent column.
        assert flow_columns.isdisjoint({"parent_flow_id", "processing_state"})
        assert movement_columns.isdisjoint(
            {"worker_id", "scan_session_id", "movement_reason", "reverses_movement_id"}
        )
        assert edge_columns == {
            "id",
            "relation",
            "parent_flow_id",
            "child_flow_id",
            "device_event_id",
            "created_at",
        }

    def test_movement_type_check_admits_exactly_the_phase8_types(
        self, connection: Connection
    ) -> None:
        check_clause = _constraint(connection, "ck_part_movements_movement_type")
        for present in (
            "RECEIVED",
            "TRANSFERRED",
            "ASSIGNED_TO_MACHINE",
            "RELEASED_FROM_MACHINE",
            "AREA_COMPLETED",
            "SPLIT",
            "MERGED",
        ):
            assert present in check_clause
        for absent in ("REVERSED", "STOCKED", "SCRAPPED", "QUANTITY_ADJUSTED"):
            assert absent not in check_clause

    def test_shape_check_is_the_one_the_model_declares(self, connection: Connection) -> None:
        phase8 = _load_migration("20260828_0009_phase8_split_merge.py", "phase8_migration")
        phase7 = _load_migration(
            "20260826_0008_phase7_direct_processing.py", "phase7_direct_processing_migration"
        )
        assert phase8._PHASE8_SHAPE == models.MOVEMENT_SHAPE_SQL
        assert phase8._PHASE7_SHAPE == phase7._PHASE7_SHAPE
        assert phase8._PHASE8_SHAPE != phase8._PHASE7_SHAPE
        stored = _constraint(connection, "ck_part_movements_movement_shape")
        assert "'SPLIT'" in stored and "'MERGED'" in stored

    def test_flow_lifecycle_checks_are_stored(self, connection: Connection) -> None:
        status = _constraint(connection, "ck_quantity_flows_status")
        for value in ("ACTIVE", "SPLIT", "MERGED"):
            assert value in status
        assert "STOCKED" not in status
        assert "closed_at IS NULL" in _constraint(connection, "ck_quantity_flows_status_closed_at")

    def test_models_metadata_matches_the_migrated_schema(self, migrated_engine: Engine) -> None:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with migrated_engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diffs = compare_metadata(context, models.Base.metadata)
        assert diffs == []

    def test_downgrade_restores_the_phase7_boundary(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase8_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE7_REVISION)
            engine = create_engine(url)
            try:
                assert set(inspect(engine).get_table_names()) == (
                    _EXPECTED_TABLES - {"quantity_flow_lineage"}
                ) | {"alembic_version"}
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    _rejected(connection, seed.movement("SPLIT"))
                    _rejected(connection, seed.movement("MERGED"))
                    names = set(
                        connection.scalars(
                            sa.text(
                                "SELECT conname FROM pg_constraint"
                                " WHERE conrelid = 'quantity_flows'::regclass"
                            )
                        )
                    )
                    assert "ck_quantity_flows_status" not in names
                    assert "ck_quantity_flows_status_closed_at" not in names
                    functions = set(
                        connection.scalars(
                            sa.text("SELECT proname FROM pg_proc WHERE proname LIKE 'partflow_%'")
                        )
                    )
                    assert "partflow_quantity_flow_lineage_forbid_mutation" not in functions
                    connection.rollback()
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)

    def test_downgrade_refuses_to_drop_phase8_history(self, admin_engine: Engine) -> None:
        """A database holding lineage history cannot satisfy the Phase 7
        checks: the downgrade fails loudly before the edge table would be
        dropped, leaving schema and history exactly as they were."""
        name = "partflow_test_phase8_downgrade_history"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    movement_id = _insert(connection, seed.movement("SPLIT"))
                    edge_id = _insert_edge(connection, seed.edge())
                    connection.commit()
                with pytest.raises(DBAPIError):
                    command.downgrade(config, _PHASE7_REVISION)
                with engine.connect() as connection:
                    movements = models.PartMovement.__table__
                    edges = models.QuantityFlowLineage.__table__
                    assert (
                        connection.execute(
                            sa.select(sa.func.count())
                            .select_from(movements)
                            .where(movements.c.id == movement_id)
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        connection.execute(
                            sa.select(sa.func.count())
                            .select_from(edges)
                            .where(edges.c.id == edge_id)
                        ).scalar_one()
                        == 1
                    )
                    version = connection.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    assert version == _PHASE8_REVISION
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestMovementShape:
    @pytest.mark.parametrize("movement_type", ["SPLIT", "MERGED"])
    def test_lineage_event_stays_in_one_area_at_a_station_without_a_machine(
        self, connection: Connection, movement_type: str
    ) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement(movement_type))
        table = models.PartMovement.__table__
        row = connection.execute(sa.select(table).where(table.c.id == movement_id)).one()
        assert row.movement_type == movement_type
        assert row.source_machine_id is None and row.destination_machine_id is None
        _rejected(connection, seed.movement(movement_type, to_area_id=seed.other_area))
        _rejected(connection, seed.movement(movement_type, from_area_id=None))
        _rejected(connection, seed.movement(movement_type, station_id=None))
        _rejected(connection, seed.movement(movement_type, source_machine_id=seed.machine))
        _rejected(connection, seed.movement(movement_type, destination_machine_id=seed.machine))

    def test_other_types_keep_their_phase7_shape(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _insert(connection, seed.movement("AREA_COMPLETED"))
        _insert(connection, seed.movement("AREA_COMPLETED", source_machine_id=seed.machine))
        _rejected(connection, seed.movement("RECEIVED", station_id=None))
        _rejected(connection, seed.movement("TRANSFERRED"))
        _rejected(connection, seed.movement("ASSIGNED_TO_MACHINE"))
        _rejected(connection, seed.movement("RELEASED_FROM_MACHINE"))

    def test_lineage_event_is_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement("SPLIT"))
        for statement in (
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(quantity=5),
            sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)


class TestFlowLifecycle:
    def test_status_and_closed_at_agree(self, connection: Connection) -> None:
        seed = _Seed(connection)
        flow = models.QuantityFlow
        now = datetime.datetime.now(datetime.UTC)

        def _set(**values: Any) -> None:
            connection.execute(sa.update(flow).where(flow.id == seed.flow).values(**values))

        for status in ("SPLIT", "MERGED"):
            with connection.begin_nested():
                _set(status=status, closed_at=now)
            with connection.begin_nested():
                _set(status="ACTIVE", closed_at=None)
        # A closure without its timestamp, a timestamp without a closure,
        # and a status of a later phase are all refused.
        with pytest.raises(IntegrityError), connection.begin_nested():
            _set(status="SPLIT")
        with pytest.raises(IntegrityError), connection.begin_nested():
            _set(closed_at=now)
        with pytest.raises(IntegrityError), connection.begin_nested():
            _set(status="STOCKED", closed_at=now)


class TestLineageEdges:
    def test_edge_shape(self, connection: Connection) -> None:
        seed = _Seed(connection)
        edge_id = _insert_edge(connection, seed.edge())
        table = models.QuantityFlowLineage.__table__
        row = connection.execute(sa.select(table).where(table.c.id == edge_id)).one()
        assert row.relation == "SPLIT" and row.created_at is not None
        # One edge per (parent, child).
        _edge_rejected(connection, seed.edge(relation="MERGED"))
        _edge_rejected(connection, seed.edge(child_flow_id=seed.flow))
        _edge_rejected(connection, seed.edge(relation="REVERSED", child_flow_id=seed.child))
        _edge_rejected(connection, seed.edge(parent_flow_id=-1))
        _edge_rejected(connection, seed.edge(child_flow_id=-1))
        # The reverse direction is a different edge.
        _insert_edge(
            connection,
            seed.edge(relation="MERGED", parent_flow_id=seed.child, child_flow_id=seed.flow),
        )

    def test_edges_are_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        edge_id = _insert_edge(connection, seed.edge())
        table = models.QuantityFlowLineage
        for statement in (
            sa.update(table).where(table.id == edge_id).values(relation="MERGED"),
            sa.delete(table).where(table.id == edge_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)
        with pytest.raises(DBAPIError), connection.begin_nested():
            connection.execute(sa.text("TRUNCATE quantity_flow_lineage"))
