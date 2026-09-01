"""Integration tests for the Phase 9 Undo/corrections schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 9 invariants PostgreSQL must enforce
(IMPLEMENTATION_ROADMAP Phase 9; PROJECT_PROFILE §8.11, §11, §14, §16;
SLICE1_DATA_MODEL §18):

- exact head boundary: the Phase 3–8 tables unchanged (no new table),
  the three new `part_movements` columns, and no Phase 10+ column;
- the movement-type CHECK admits `SCRAPPED`, `QUANTITY_ADJUSTED` and
  `REVERSED` and nothing later; the shape CHECK pins each new branch
  and is byte-equal to the model's expression, and the Phase 8
  expression the migration re-declares for its downgrade equals the
  Phase 8 migration's;
- `movement_reason` limited to `REPAIR` on a `TRANSFERRED` only; the
  free-text `reason` mandatory for Scrap, quantity adjustments and
  every Repair; `reverses_movement_id` set exactly on a `REVERSED`,
  FK-checked, and UNIQUE — the database itself refuses a second
  reversal of one Movement;
- the QuantityFlow lifecycle: `status` widened with SCRAPPED /
  REVERSED, `closed_at` agreement unchanged;
- append-only enforcement covers the new Movement types unchanged;
- clean downgrade back to the Phase 8 boundary with a successful
  re-upgrade, and the downgrade refusing to drop Phase 9 history.

This module is PINNED to `0010_phase9_undo_corrections` (Phase 10
added migration 0011): every assertion documents the Phase 9 boundary
as it shipped, and the head-level coverage (models↔schema parity, the
current shape CHECK) lives in `test_phase10_schema.py`.
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

_PHASE8_REVISION = "0009_phase8_split_merge"
_PHASE9_REVISION = "0010_phase9_undo_corrections"

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
    """Temporary database migrated 0010 → base → 0010 through real Alembic runs."""
    name = "partflow_test_phase9_schema"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    command.upgrade(config, _PHASE9_REVISION)
    command.downgrade(config, "base")
    command.upgrade(config, _PHASE9_REVISION)
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
            "movement_reason": None,
            "reason": None,
            "reverses_movement_id": None,
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
    def test_phase9_keeps_exactly_the_phase8_tables(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        assert tables == _EXPECTED_TABLES | {"alembic_version"}

    def test_phase9_adds_exactly_the_three_columns(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        assert {"movement_reason", "reason", "reverses_movement_id"} <= movement_columns
        # The Worker columns stay deferred (Phase 13); no Phase 10+
        # column exists.
        assert movement_columns.isdisjoint({"worker_id", "scan_session_id"})
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        assert flow_columns.isdisjoint({"parent_flow_id", "processing_state", "stocked_at"})
        # The Phase 10 done date does not exist at this boundary.
        work_order_columns = {column["name"] for column in inspector.get_columns("work_orders")}
        assert "completed_at" not in work_order_columns

    def test_movement_type_check_admits_exactly_the_phase9_types(
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
        ):
            assert present in check_clause
        for absent in ("STOCKED", "ROUTE_ADJUSTED", "ROUTE_DEVIATION_CONFIRMED"):
            assert absent not in check_clause

    def test_shape_and_reason_checks_are_the_ones_the_model_declares(
        self, connection: Connection
    ) -> None:
        phase9 = _load_migration("20260831_0010_phase9_undo_corrections.py", "phase9_migration")
        phase8 = _load_migration("20260828_0009_phase8_split_merge.py", "phase8_migration")
        phase10 = _load_migration("20260901_0011_phase10_stock_allocation.py", "phase10_migration")
        # The Phase 9 expression is what the Phase 10 migration restores
        # on downgrade; the model now carries the Phase 10 shape.
        assert phase9._PHASE9_SHAPE == phase10._PHASE9_SHAPE
        assert phase9._PHASE9_SHAPE != models.MOVEMENT_SHAPE_SQL
        assert phase9._PHASE8_SHAPE == phase8._PHASE8_SHAPE
        assert phase9._PHASE9_SHAPE != phase9._PHASE8_SHAPE
        assert phase9._MOVEMENT_REASON_SQL == models.MOVEMENT_REASON_SQL
        assert phase9._REASON_REQUIRED_SQL == models.MOVEMENT_REASON_REQUIRED_SQL
        assert phase9._REVERSES_SQL == models.MOVEMENT_REVERSES_SQL
        stored = _constraint(connection, "ck_part_movements_movement_shape")
        for value in ("'SCRAPPED'", "'QUANTITY_ADJUSTED'", "'REVERSED'"):
            assert value in stored

    def test_flow_status_check_admits_exactly_the_phase9_statuses(
        self, connection: Connection
    ) -> None:
        status = _constraint(connection, "ck_quantity_flows_status")
        for value in ("ACTIVE", "SPLIT", "MERGED", "SCRAPPED", "REVERSED"):
            assert value in status
        assert "STOCKED" not in status

    def test_downgrade_restores_the_phase8_boundary(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase9_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, _PHASE9_REVISION)
            command.downgrade(config, _PHASE8_REVISION)
            engine = create_engine(url)
            try:
                columns = {
                    column["name"] for column in inspect(engine).get_columns("part_movements")
                }
                assert columns.isdisjoint({"movement_reason", "reason", "reverses_movement_id"})
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    for movement_type in ("SCRAPPED", "QUANTITY_ADJUSTED", "REVERSED"):
                        values = seed.movement(movement_type)
                        for gone in ("movement_reason", "reason", "reverses_movement_id"):
                            values.pop(gone)
                        if movement_type == "QUANTITY_ADJUSTED":
                            values["from_area_id"] = None
                        _rejected(connection, values)
                    connection.rollback()
            finally:
                engine.dispose()
            command.upgrade(config, _PHASE9_REVISION)
        finally:
            _drop_temp_database(admin_engine, name)

    def test_downgrade_refuses_to_drop_phase9_history(self, admin_engine: Engine) -> None:
        """A database holding Phase 9 history cannot satisfy the Phase 8
        checks: the downgrade fails loudly before any column would be
        dropped, leaving schema and history exactly as they were."""
        name = "partflow_test_phase9_downgrade_history"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, _PHASE9_REVISION)
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    seed = _Seed(connection)
                    movement_id = _insert(connection, seed.movement("SCRAPPED", reason="damaged"))
                    connection.commit()
                with pytest.raises(DBAPIError):
                    command.downgrade(config, _PHASE8_REVISION)
                with engine.connect() as connection:
                    movements = models.PartMovement.__table__
                    assert (
                        connection.execute(
                            sa.select(sa.func.count())
                            .select_from(movements)
                            .where(movements.c.id == movement_id)
                        ).scalar_one()
                        == 1
                    )
                    version = connection.execute(
                        sa.text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    assert version == _PHASE9_REVISION
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestMovementShape:
    def test_scrapped_stays_in_one_area_and_may_name_the_source_machine(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        _insert(connection, seed.movement("SCRAPPED", reason="damaged"))
        _insert(
            connection,
            seed.movement("SCRAPPED", reason="damaged", source_machine_id=seed.machine),
        )
        _rejected(
            connection, seed.movement("SCRAPPED", reason="damaged", to_area_id=seed.other_area)
        )
        _rejected(connection, seed.movement("SCRAPPED", reason="damaged", from_area_id=None))
        _rejected(connection, seed.movement("SCRAPPED", reason="damaged", station_id=None))
        _rejected(
            connection,
            seed.movement("SCRAPPED", reason="damaged", destination_machine_id=seed.machine),
        )
        # The reason is mandatory at the database level.
        _rejected(connection, seed.movement("SCRAPPED"))

    def test_quantity_adjusted_introduces_at_a_station_without_machines(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        _insert(connection, seed.movement("QUANTITY_ADJUSTED", from_area_id=None, reason="found"))
        # A source Area, a missing Station, a Machine reference and a
        # missing reason are all refused.
        _rejected(connection, seed.movement("QUANTITY_ADJUSTED", reason="found"))
        _rejected(
            connection,
            seed.movement("QUANTITY_ADJUSTED", from_area_id=None, reason="found", station_id=None),
        )
        _rejected(
            connection,
            seed.movement(
                "QUANTITY_ADJUSTED",
                from_area_id=None,
                reason="found",
                destination_machine_id=seed.machine,
            ),
        )
        _rejected(connection, seed.movement("QUANTITY_ADJUSTED", from_area_id=None))

    def test_reversed_references_exactly_one_original_at_most_once(
        self, connection: Connection
    ) -> None:
        seed = _Seed(connection)
        original = _insert(connection, seed.movement("TRANSFERRED", to_area_id=seed.other_area))
        # In-Area and cross-Area compensations are both valid shapes.
        _insert(
            connection,
            seed.movement(
                "REVERSED",
                from_area_id=seed.other_area,
                to_area_id=seed.area,
                reverses_movement_id=original,
            ),
        )
        in_area_original = _insert(connection, seed.movement("AREA_COMPLETED"))
        _insert(connection, seed.movement("REVERSED", reverses_movement_id=in_area_original))
        # A REVERSED without its original, an original reference on
        # another type, a missing Station, a Machine reference, an
        # unknown original, and a SECOND reversal of one original are
        # all refused by the database itself.
        _rejected(connection, seed.movement("REVERSED"))
        _rejected(connection, seed.movement("TRANSFERRED", reverses_movement_id=original))
        _rejected(
            connection,
            seed.movement("REVERSED", reverses_movement_id=original, station_id=None),
        )
        _rejected(
            connection,
            seed.movement(
                "REVERSED", reverses_movement_id=original, source_machine_id=seed.machine
            ),
        )
        _rejected(connection, seed.movement("REVERSED", reverses_movement_id=-1))
        _rejected(connection, seed.movement("REVERSED", reverses_movement_id=original))

    def test_movement_reason_is_repair_on_a_transfer_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        _insert(
            connection,
            seed.movement(
                "TRANSFERRED",
                to_area_id=seed.other_area,
                movement_reason="REPAIR",
                reason="rework the finish",
            ),
        )
        # REPAIR without its mandatory reason, on a non-transfer, and an
        # unknown intent value are all refused.
        _rejected(
            connection,
            seed.movement("TRANSFERRED", to_area_id=seed.other_area, movement_reason="REPAIR"),
        )
        _rejected(connection, seed.movement("AREA_COMPLETED", movement_reason="REPAIR", reason="x"))
        _rejected(
            connection,
            seed.movement(
                "TRANSFERRED", to_area_id=seed.other_area, movement_reason="REWORK", reason="x"
            ),
        )

    def test_new_movement_types_are_append_only(self, connection: Connection) -> None:
        seed = _Seed(connection)
        movement_id = _insert(connection, seed.movement("SCRAPPED", reason="damaged"))
        for statement in (
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(quantity=5),
            sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
        ):
            with pytest.raises(DBAPIError), connection.begin_nested():
                connection.execute(statement)


class TestFlowLifecycle:
    def test_new_statuses_agree_with_closed_at(self, connection: Connection) -> None:
        seed = _Seed(connection)
        flow = models.QuantityFlow
        now = datetime.datetime.now(datetime.UTC)

        def _set(**values: Any) -> None:
            connection.execute(sa.update(flow).where(flow.id == seed.flow).values(**values))

        for status in ("SCRAPPED", "REVERSED"):
            with connection.begin_nested():
                _set(status=status, closed_at=now)
            with connection.begin_nested():
                _set(status="ACTIVE", closed_at=None)
        with pytest.raises(IntegrityError), connection.begin_nested():
            _set(status="SCRAPPED")
        with pytest.raises(IntegrityError), connection.begin_nested():
            _set(status="STOCKED", closed_at=now)
