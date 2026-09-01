"""Integration tests for the Phase 10 Stockroom / allocation schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 10 invariants PostgreSQL must enforce
(IMPLEMENTATION_ROADMAP Phase 10; PROJECT_PROFILE §8.2, §8.12, §18;
SLICE1_DATA_MODEL §17/§18):

- exact head boundary: the Phase 3–9 tables plus exactly
  `work_order_allocations`; `work_orders.completed_at` with its partial
  keyset index; no Phase 13+ column;
- the movement-type CHECK admits `STOCKED` and nothing later; the
  shape CHECK pins a `STOCKED` to two DIFFERENT Areas at a Station
  with no Machine and is byte-equal to the model's expression, and the
  Phase 9 expression the migration re-declares for its downgrade
  equals the Phase 9 migration's;
- the QuantityFlow lifecycle: `status` widened with STOCKED,
  `closed_at` agreement unchanged;
- the allocation table: canonical PN, positive quantity, the source
  vocabulary, the mandatory reversal reason, `reverses_allocation_id`
  FK + UNIQUE (one reversal per allocation, also under a race), the
  `device_event_id` + `command_sequence` idempotency pair, the demand
  and station FKs, and the raise-on-write trigger;
- models↔migration metadata parity at head (moved here from the
  Phase 9 schema test, which is now pinned to 0010);
- clean downgrade back to the Phase 9 boundary with a successful
  re-upgrade, and the downgrade refusing to drop Phase 10 history.

Phase 10 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to
`0011_phase10_stock_allocation` and move the head-level coverage into
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

_PHASE9_REVISION = "0010_phase9_undo_corrections"
_PHASE10_REVISION = "0011_phase10_stock_allocation"

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
    "work_order_allocations",
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
    name = "partflow_test_phase10_schema"
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
    """A production Area and a terminal Area, a Station in each, one flow, one demand."""

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
        self.stockroom = connection.execute(
            sa.insert(models.Area)
            .values(department_id=department_id, name=_unique("STOCK"), is_terminal=True)
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
        self.stock_station = _unique("ST")
        connection.execute(
            sa.insert(models.ScanStation).values(
                station_id=self.stock_station, area_id=self.stockroom
            )
        )
        self.part_number = _unique("PN")
        self.flow = connection.execute(
            sa.insert(models.QuantityFlow)
            .values(part_number=self.part_number, quantity=10, current_area_id=self.area)
            .returning(models.QuantityFlow.id)
        ).scalar_one()
        self.work_order = connection.execute(
            sa.insert(models.WorkOrder)
            .values(received_date=datetime.date(2026, 9, 1))
            .returning(models.WorkOrder.id)
        ).scalar_one()
        self.demand = connection.execute(
            sa.insert(models.WorkOrderDemand)
            .values(
                work_order_id=self.work_order,
                part_number=self.part_number,
                request_type="NEW",
                requested_quantity=10,
            )
            .returning(models.WorkOrderDemand.id)
        ).scalar_one()

    def movement(self, movement_type: str, **overrides: Any) -> dict[str, Any]:
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, Any] = {
            "quantity_flow_id": self.flow,
            "part_number": self.part_number,
            "movement_type": movement_type,
            "quantity": 10,
            "from_area_id": self.area,
            "to_area_id": self.stockroom,
            "operation_id": self.operation,
            "station_id": self.stock_station,
            "source_machine_id": None,
            "destination_machine_id": None,
            "movement_reason": None,
            "reason": None,
            "reverses_movement_id": None,
            "occurred_at": now,
            "server_received_at": now,
            "device_event_id": str(uuid.uuid4()),
        }
        values.update(overrides)
        return values

    def allocation(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "part_number": self.part_number,
            "work_order_demand_id": self.demand,
            "quantity": 5,
            "source": "STOCKROOM",
            "is_manual_override": False,
            "allocation_reason": None,
            "reverses_allocation_id": None,
            "station_id": self.stock_station,
            "actor_reference": None,
            "allocated_at": datetime.datetime.now(datetime.UTC),
            "device_event_id": str(uuid.uuid4()),
            "command_sequence": 1,
            "metadata_": None,
        }
        values.update(overrides)
        return values


def _insert_movement(connection: Connection, values: dict[str, Any]) -> int:
    return connection.execute(
        sa.insert(models.PartMovement).values(**values).returning(models.PartMovement.id)
    ).scalar_one()


def _insert_allocation(connection: Connection, values: dict[str, Any]) -> int:
    return connection.execute(
        sa.insert(models.WorkOrderAllocation)
        .values(**values)
        .returning(models.WorkOrderAllocation.id)
    ).scalar_one()


def _rejected_movement(connection: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        _insert_movement(connection, values)


def _rejected_allocation(connection: Connection, values: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError), connection.begin_nested():
        _insert_allocation(connection, values)


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
    def test_head_adds_exactly_the_allocation_table(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        assert tables == _EXPECTED_TABLES | {"alembic_version"}

    def test_phase10_columns_and_index(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        work_order_columns = {column["name"] for column in inspector.get_columns("work_orders")}
        assert "completed_at" in work_order_columns
        indexes = {index["name"]: index for index in inspector.get_indexes("work_orders")}
        assert indexes["ix_work_orders_completed_at_id"]["column_names"] == ["completed_at", "id"]
        allocation_columns = {
            column["name"] for column in inspector.get_columns("work_order_allocations")
        }
        assert allocation_columns == {
            "id",
            "part_number",
            "work_order_demand_id",
            "quantity",
            "source",
            "is_manual_override",
            "allocation_reason",
            "reverses_allocation_id",
            "station_id",
            "actor_reference",
            "allocated_at",
            "device_event_id",
            "command_sequence",
            "metadata",
        }
        # Workers stay deferred (Phase 13): no worker column anywhere.
        assert "allocated_by_worker_id" not in allocation_columns
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        assert movement_columns.isdisjoint({"worker_id", "scan_session_id"})
        # Allocation never references Movement or a QuantityFlow.
        assert allocation_columns.isdisjoint({"part_movement_id", "quantity_flow_id"})

    def test_movement_type_check_admits_exactly_the_phase10_types(
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
            "SCRAPPED",
            "QUANTITY_ADJUSTED",
            "REVERSED",
            "STOCKED",
        ):
            assert present in check_clause
        for absent in ("ROUTE_ADJUSTED", "ROUTE_DEVIATION_CONFIRMED"):
            assert absent not in check_clause

    def test_shape_check_is_the_one_the_model_declares(self, connection: Connection) -> None:
        phase10 = _load_migration("20260901_0011_phase10_stock_allocation.py", "phase10_migration")
        phase9 = _load_migration("20260831_0010_phase9_undo_corrections.py", "phase9_migration")
        assert phase10._PHASE10_SHAPE == models.MOVEMENT_SHAPE_SQL
        assert phase10._PHASE9_SHAPE == phase9._PHASE9_SHAPE
        assert phase10._PHASE10_SHAPE != phase10._PHASE9_SHAPE
        assert phase10._REVERSAL_REASON_SQL == models.ALLOCATION_REVERSAL_REASON_SQL
        assert phase10._CANONICAL_PART_NUMBER_SQL == models.CANONICAL_PART_NUMBER_SQL
        assert "'STOCKED'" in _constraint(connection, "ck_part_movements_movement_shape")

    def test_flow_status_check_admits_exactly_the_phase10_statuses(
        self, connection: Connection
    ) -> None:
        status = _constraint(connection, "ck_quantity_flows_status")
        for value in ("ACTIVE", "SPLIT", "MERGED", "SCRAPPED", "REVERSED", "STOCKED"):
            assert value in status

    def test_models_metadata_matches_the_migrated_schema(self, migrated_engine: Engine) -> None:
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with migrated_engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diffs = compare_metadata(context, models.Base.metadata)
        assert diffs == []

    def test_downgrade_restores_the_phase9_boundary(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase10_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE9_REVISION)
            engine = create_engine(url)
            try:
                inspector = inspect(engine)
                assert "work_order_allocations" not in inspector.get_table_names()
                columns = {column["name"] for column in inspector.get_columns("work_orders")}
                assert "completed_at" not in columns
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    _rejected_movement(connection, seed.movement("STOCKED"))
                    connection.rollback()
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)

    @pytest.mark.parametrize("history", ["stocked_movement", "allocation", "completed_work_order"])
    def test_downgrade_refuses_to_drop_phase10_history(
        self, admin_engine: Engine, history: str
    ) -> None:
        """A database holding Phase 10 history cannot pass back to the
        Phase 9 boundary: the downgrade fails loudly before any table or
        column would be dropped, leaving schema and history as they were."""
        name = f"partflow_test_phase10_downgrade_{history}"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    if history == "stocked_movement":
                        _insert_movement(connection, seed.movement("STOCKED"))
                    elif history == "allocation":
                        _insert_allocation(connection, seed.allocation())
                    else:
                        connection.execute(
                            sa.update(models.WorkOrder)
                            .where(models.WorkOrder.id == seed.work_order)
                            .values(completed_at=datetime.datetime.now(datetime.UTC))
                        )
                    connection.commit()
                with pytest.raises(DBAPIError):
                    command.downgrade(config, _PHASE9_REVISION)
                with engine.connect() as connection:
                    version = connection.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    assert version == _PHASE10_REVISION
                    assert "work_order_allocations" in inspect(engine).get_table_names()
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestMovementShape:
    def test_stocked_moves_between_two_areas_at_a_station_without_a_machine(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        _insert_movement(connection, seed.movement("STOCKED"))
        # Same Area, no source Area, no Station, and any Machine reference
        # are refused — the terminal flag is an Application rule.
        _rejected_movement(connection, seed.movement("STOCKED", to_area_id=seed.area))
        _rejected_movement(connection, seed.movement("STOCKED", from_area_id=None))
        _rejected_movement(connection, seed.movement("STOCKED", station_id=None))
        _rejected_movement(connection, seed.movement("STOCKED", source_machine_id=seed.machine))
        _rejected_movement(
            connection, seed.movement("STOCKED", destination_machine_id=seed.machine)
        )
        # Repair is a transfer intent, never a stocking.
        _rejected_movement(
            connection, seed.movement("STOCKED", movement_reason="REPAIR", reason="x")
        )

    def test_stocked_is_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        movement_id = _insert_movement(connection, seed.movement("STOCKED"))
        for statement in (
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(quantity=5),
            sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)


class TestFlowLifecycle:
    def test_stocked_status_agrees_with_closed_at(self, connection: Connection) -> None:
        seed = _Seed(connection)
        flow = models.QuantityFlow
        now = datetime.datetime.now(datetime.UTC)
        with connection.begin_nested():
            connection.execute(
                sa.update(flow).where(flow.id == seed.flow).values(status="STOCKED", closed_at=now)
            )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(sa.update(flow).where(flow.id == seed.flow).values(closed_at=None))


class TestAllocationTable:
    def test_row_shape(self, connection: Connection) -> None:
        seed = _Seed(connection)
        original = _insert_allocation(connection, seed.allocation())
        _insert_allocation(connection, seed.allocation(source="MANAGEMENT", station_id=None))
        # Canonical PN, positive quantity, the source vocabulary, the
        # demand and station FKs.
        _rejected_allocation(connection, seed.allocation(part_number="pn lower"))
        _rejected_allocation(connection, seed.allocation(quantity=0))
        _rejected_allocation(connection, seed.allocation(source="OPERATOR"))
        _rejected_allocation(connection, seed.allocation(work_order_demand_id=-1))
        _rejected_allocation(connection, seed.allocation(station_id="NOPE"))
        _rejected_allocation(connection, seed.allocation(command_sequence=0))
        # A reversal references an existing allocation and says why —
        # and each allocation is reversed at most once.
        _rejected_allocation(connection, seed.allocation(reverses_allocation_id=original))
        _rejected_allocation(
            connection, seed.allocation(reverses_allocation_id=-1, allocation_reason="x")
        )
        _insert_allocation(
            connection, seed.allocation(reverses_allocation_id=original, allocation_reason="x")
        )
        _rejected_allocation(
            connection, seed.allocation(reverses_allocation_id=original, allocation_reason="y")
        )

    def test_one_command_may_write_several_rows_under_one_id(self, connection: Connection) -> None:
        seed = _Seed(connection)
        event_id = str(uuid.uuid4())
        _insert_allocation(connection, seed.allocation(device_event_id=event_id))
        _insert_allocation(
            connection, seed.allocation(device_event_id=event_id, command_sequence=2)
        )
        _rejected_allocation(
            connection, seed.allocation(device_event_id=event_id, command_sequence=2)
        )

    def test_rows_are_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        allocation_id = _insert_allocation(connection, seed.allocation())
        table = models.WorkOrderAllocation
        for statement in (
            sa.update(table).where(table.id == allocation_id).values(quantity=1),
            sa.delete(table).where(table.id == allocation_id),
            sa.text("TRUNCATE work_order_allocations"),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)
