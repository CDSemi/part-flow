"""Integration tests for the Phase 3 domain schema and its invariants.

Runs the real Alembic migrations against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the canonical invariants PostgreSQL must enforce:

- PartNumber natural key and canonical-form CHECK;
- no FK from production tables to the optional part_numbers master;
- nullable Work Order Number with partial uniqueness;
- WorkOrderDemand request-type and positive-quantity checks;
- route_mode / assigned_route_id consistency and snapshot ownership;
- QuantityFlow positive quantity and current_area_id integrity;
- PartMovement composite PN agreement, unique device_event_id,
  RECEIVED shape, and trigger-enforced append-only immutability;
- clean, complete downgrade.

The fixture migrates up → down → up, so a downgrade that leaves any
object behind fails the module before any test runs. Every test runs in
its own rolled-back transaction against isolated data; the development
database configured in DATABASE_URL is never touched beyond CREATE/DROP
of the dedicated test databases.

This module is the Phase 3 boundary: it migrates to the Phase 3
revision `0002_phase3_domain` — never to `head` — so its exact-table-set
and absent-deferred-column assertions keep guarding what Phase 3 itself
created even as later migrations (Phase 3.5+) extend the schema.
Head-level coverage (models↔migration parity, Phase 3.5 invariants)
lives in test_phase35_schema.py.
"""

import datetime
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

# The Phase 3 boundary revision this module migrates to (never head).
_PHASE3_REVISION = "0002_phase3_domain"

_PHASE3_TABLES = {
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
}

_PRODUCTION_TABLES_KEEPING_PN_BY_VALUE = ("work_order_demands", "quantity_flows", "part_movements")


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # env.py honors a pre-set URL, so the temporary database is migrated
    # instead of the application database.
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
    """Temporary database migrated up → down → up through real Alembic runs."""
    name = "partflow_test_phase3_schema"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    command.upgrade(config, _PHASE3_REVISION)
    # Reversibility gate: the downgrade must remove everything it
    # created (tables, trigger, function) or the second upgrade fails.
    command.downgrade(config, "base")
    command.upgrade(config, _PHASE3_REVISION)
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
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _create_department(connection: Connection) -> int:
    return connection.execute(
        sa.insert(models.Department).values(name=_unique("DEPT")).returning(models.Department.id)
    ).scalar_one()


def _create_area(connection: Connection, department_id: int) -> int:
    return connection.execute(
        sa.insert(models.Area)
        .values(department_id=department_id, name=_unique("AREA"))
        .returning(models.Area.id)
    ).scalar_one()


def _create_operation(connection: Connection, area_id: int) -> int:
    return connection.execute(
        sa.insert(models.Operation)
        .values(area_id=area_id, code=_unique("OP"))
        .returning(models.Operation.id)
    ).scalar_one()


def _create_production_context(connection: Connection) -> tuple[int, int]:
    """Create Department → Area → Operation; return (area_id, operation_id)."""
    department_id = _create_department(connection)
    area_id = _create_area(connection, department_id)
    operation_id = _create_operation(connection, area_id)
    return area_id, operation_id


def _create_work_order(connection: Connection, **overrides: Any) -> int:
    values: dict[str, Any] = {"received_date": datetime.date(2026, 8, 18), **overrides}
    return connection.execute(
        sa.insert(models.WorkOrder).values(**values).returning(models.WorkOrder.id)
    ).scalar_one()


def _create_demand(connection: Connection, work_order_id: int, **overrides: Any) -> int:
    values: dict[str, Any] = {
        "work_order_id": work_order_id,
        "part_number": _unique("PN").upper(),
        "request_type": "NEW",
        "requested_quantity": 10,
        **overrides,
    }
    return connection.execute(
        sa.insert(models.WorkOrderDemand).values(**values).returning(models.WorkOrderDemand.id)
    ).scalar_one()


def _create_assigned_route(connection: Connection, area_id: int) -> int:
    assigned_route_id = connection.execute(
        sa.insert(models.AssignedRoute).values().returning(models.AssignedRoute.id)
    ).scalar_one()
    connection.execute(
        sa.insert(models.AssignedRouteStep).values(
            assigned_route_id=assigned_route_id, sequence=1, area_id=area_id
        )
    )
    return assigned_route_id


def _create_flow(connection: Connection, area_id: int, **overrides: Any) -> int:
    values: dict[str, Any] = {
        "part_number": _unique("PN").upper(),
        "quantity": 25,
        "current_area_id": area_id,
        **overrides,
    }
    return connection.execute(
        sa.insert(models.QuantityFlow).values(**values).returning(models.QuantityFlow.id)
    ).scalar_one()


def _movement_values(
    connection: Connection, flow_id: int, area_id: int, operation_id: int, **overrides: Any
) -> dict[str, Any]:
    part_number = connection.execute(
        sa.select(models.QuantityFlow.part_number).where(models.QuantityFlow.id == flow_id)
    ).scalar_one()
    now = datetime.datetime.now(datetime.UTC)
    return {
        "quantity_flow_id": flow_id,
        "part_number": part_number,
        "movement_type": "RECEIVED",
        "quantity": 25,
        "from_area_id": None,
        "to_area_id": area_id,
        "operation_id": operation_id,
        "occurred_at": now,
        "server_received_at": now,
        "device_event_id": str(uuid.uuid4()),
        **overrides,
    }


def _insert_movement(
    connection: Connection, flow_id: int, area_id: int, operation_id: int, **overrides: Any
) -> int:
    return connection.execute(
        sa.insert(models.PartMovement)
        .values(**_movement_values(connection, flow_id, area_id, operation_id, **overrides))
        .returning(models.PartMovement.id)
    ).scalar_one()


def _rejected(connection: Connection, statement: sa.Insert | sa.Update | sa.Delete) -> None:
    """Assert the statement is rejected by a database integrity rule."""
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(statement)


class TestMigrationSchema:
    def test_migration_creates_exactly_the_phase3_tables(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        assert tables >= _PHASE3_TABLES
        # Nothing beyond the canonical Phase 3 scope (plus Alembic's own
        # version table) exists — no audit_events, scan_stations,
        # machines, or other pre-implemented future tables.
        assert tables - _PHASE3_TABLES == {"alembic_version"}

    def test_no_deferred_columns_exist(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        area_columns = {column["name"] for column in inspector.get_columns("areas")}
        step_columns = {column["name"] for column in inspector.get_columns("route_steps")}
        wo_columns = {column["name"] for column in inspector.get_columns("work_orders")}
        assert "current_machine_id" not in flow_columns
        assert "parent_flow_id" not in flow_columns
        assert movement_columns.isdisjoint(
            {
                "station_id",
                "worker_id",
                "scan_session_id",
                "movement_reason",
                "reverses_movement_id",
            }
        )
        assert area_columns.isdisjoint({"is_terminal", "worker_identification_mode"})
        assert "preferred_machine_id" not in step_columns
        assert "completed_at" not in wo_columns

    def test_no_table_references_the_part_numbers_master(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        for table in inspector.get_table_names():
            referred = {
                foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys(table)
            }
            assert "part_numbers" not in referred, table

    def test_production_tables_keep_pn_by_value(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        for table in _PRODUCTION_TABLES_KEEPING_PN_BY_VALUE:
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert "part_number" in columns, table
            assert "part_number_id" not in columns, table

    def test_movement_flow_pn_agreement_is_a_composite_foreign_key(
        self, migrated_engine: Engine
    ) -> None:
        foreign_keys = inspect(migrated_engine).get_foreign_keys("part_movements")
        composite = [
            foreign_key
            for foreign_key in foreign_keys
            if foreign_key["referred_table"] == "quantity_flows"
        ]
        assert len(composite) == 1
        assert composite[0]["constrained_columns"] == ["quantity_flow_id", "part_number"]
        assert composite[0]["referred_columns"] == ["id", "part_number"]

    def test_assigned_routes_carry_no_flow_back_reference(self, migrated_engine: Engine) -> None:
        columns = {
            column["name"] for column in inspect(migrated_engine).get_columns("assigned_routes")
        }
        assert "quantity_flow_id" not in columns

    def test_check_constraint_names_are_deterministic_and_not_doubled(
        self, migrated_engine: Engine
    ) -> None:
        # Explicit names are marked as already conventionalized
        # (conv()/op.f()), so the ck_%(table_name)s_%(constraint_name)s
        # naming convention must not wrap them a second time.
        with migrated_engine.connect() as conn:
            names = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conname LIKE 'ck!_%' ESCAPE '!'"
                    )
                )
            }
        assert "ck_part_numbers_part_number_canonical" in names
        assert "ck_quantity_flows_route_mode_assigned_route" in names
        assert "ck_part_movements_received_shape" in names
        doubled = {name for name in names if name.count("ck_") > 1}
        assert doubled == set()

    # NOTE: models↔migration metadata parity is asserted at head in
    # test_phase35_schema.py — the model metadata describes the full
    # current schema, which this Phase-3-boundary database predates.

    def test_downgrade_removes_every_created_object(self, admin_engine: Engine) -> None:
        name = "partflow_test_phase3_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, _PHASE3_REVISION)
            command.downgrade(config, "base")
            engine = create_engine(url)
            try:
                assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
                with engine.connect() as connection:
                    leftover_functions = connection.execute(
                        sa.text(
                            "SELECT proname FROM pg_proc"
                            " WHERE proname = 'partflow_part_movements_forbid_mutation'"
                        )
                    ).all()
                assert leftover_functions == []
            finally:
                engine.dispose()
        finally:
            _drop_temp_database(admin_engine, name)


class TestPartNumberMaster:
    def test_canonical_pn_is_the_natural_primary_key(self, connection: Connection) -> None:
        connection.execute(sa.insert(models.PartNumber).values(part_number="ABC-123"))
        _rejected(connection, sa.insert(models.PartNumber).values(part_number="ABC-123"))

    @pytest.mark.parametrize("invalid", ["abc-123", "ABC 123", "ABC\t123", "ABC\n123", ""])
    def test_non_canonical_values_are_rejected(self, connection: Connection, invalid: str) -> None:
        # Internal space, tab, and newline must each be rejected by the
        # database CHECK itself, independent of domain normalization.
        _rejected(connection, sa.insert(models.PartNumber).values(part_number=invalid))

    def test_production_rows_never_depend_on_the_master(self, connection: Connection) -> None:
        # Demand, flow, and movement all commit for a canonical PN that
        # has no part_numbers master row at all.
        area_id, operation_id = _create_production_context(connection)
        part_number = _unique("PN").upper()
        work_order_id = _create_work_order(connection)
        _create_demand(connection, work_order_id, part_number=part_number)
        flow_id = _create_flow(connection, area_id, part_number=part_number)
        _insert_movement(connection, flow_id, area_id, operation_id)
        master_rows = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartNumber)
            .where(models.PartNumber.part_number == part_number)
        ).scalar_one()
        assert master_rows == 0


class TestWorkOrder:
    def test_multiple_internal_work_orders_may_hold_null_numbers(
        self, connection: Connection
    ) -> None:
        first = _create_work_order(connection, work_order_number=None)
        second = _create_work_order(connection, work_order_number=None)
        assert first != second

    def test_non_null_work_order_numbers_stay_unique(self, connection: Connection) -> None:
        number = _unique("WO")
        _create_work_order(connection, work_order_number=number)
        _rejected(
            connection,
            sa.insert(models.WorkOrder).values(
                work_order_number=number, received_date=datetime.date(2026, 8, 18)
            ),
        )


class TestWorkOrderDemand:
    def test_request_type_allows_only_new_and_modify(self, connection: Connection) -> None:
        work_order_id = _create_work_order(connection)
        _create_demand(connection, work_order_id, request_type="NEW")
        _create_demand(connection, work_order_id, request_type="MODIFY")
        for invalid in ("REPAIR", "new", ""):
            _rejected(
                connection,
                sa.insert(models.WorkOrderDemand).values(
                    work_order_id=work_order_id,
                    part_number=_unique("PN").upper(),
                    request_type=invalid,
                    requested_quantity=1,
                ),
            )

    @pytest.mark.parametrize("quantity", [0, -1])
    def test_requested_quantity_must_be_positive(
        self, connection: Connection, quantity: int
    ) -> None:
        work_order_id = _create_work_order(connection)
        _rejected(
            connection,
            sa.insert(models.WorkOrderDemand).values(
                work_order_id=work_order_id,
                part_number=_unique("PN").upper(),
                request_type="NEW",
                requested_quantity=quantity,
            ),
        )

    def test_due_date_is_nullable_valid_data(self, connection: Connection) -> None:
        work_order_id = _create_work_order(connection)
        demand_id = _create_demand(connection, work_order_id, due_date=None)
        stored = connection.execute(
            sa.select(models.WorkOrderDemand.due_date).where(models.WorkOrderDemand.id == demand_id)
        ).scalar_one()
        assert stored is None

    def test_demand_pn_must_be_canonical(self, connection: Connection) -> None:
        work_order_id = _create_work_order(connection)
        _rejected(
            connection,
            sa.insert(models.WorkOrderDemand).values(
                work_order_id=work_order_id,
                part_number="abc-123",
                request_type="NEW",
                requested_quantity=1,
            ),
        )


class TestQuantityFlowRouteMode:
    def test_floating_default_carries_no_assigned_route(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        row = connection.execute(
            sa.select(models.QuantityFlow.route_mode, models.QuantityFlow.assigned_route_id).where(
                models.QuantityFlow.id == flow_id
            )
        ).one()
        assert row.route_mode == "FLOATING"
        assert row.assigned_route_id is None

    def test_floating_with_assigned_route_is_rejected(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        assigned_route_id = _create_assigned_route(connection, area_id)
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(),
                quantity=5,
                route_mode="FLOATING",
                assigned_route_id=assigned_route_id,
                current_area_id=area_id,
            ),
        )

    def test_planned_without_assigned_route_is_rejected(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(),
                quantity=5,
                route_mode="PLANNED",
                assigned_route_id=None,
                current_area_id=area_id,
            ),
        )

    def test_planned_with_its_snapshot_is_accepted(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        assigned_route_id = _create_assigned_route(connection, area_id)
        _create_flow(connection, area_id, route_mode="PLANNED", assigned_route_id=assigned_route_id)

    def test_one_snapshot_is_never_shared_by_two_flows(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        assigned_route_id = _create_assigned_route(connection, area_id)
        _create_flow(connection, area_id, route_mode="PLANNED", assigned_route_id=assigned_route_id)
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(),
                quantity=5,
                route_mode="PLANNED",
                assigned_route_id=assigned_route_id,
                current_area_id=area_id,
            ),
        )

    def test_invalid_route_mode_is_rejected(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(),
                quantity=5,
                route_mode="GUIDED",
                current_area_id=area_id,
            ),
        )


class TestQuantityFlowIntegrity:
    @pytest.mark.parametrize("quantity", [0, -5])
    def test_quantity_must_be_positive(self, connection: Connection, quantity: int) -> None:
        area_id, _ = _create_production_context(connection)
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(), quantity=quantity, current_area_id=area_id
            ),
        )

    def test_flow_pn_must_be_canonical(self, connection: Connection) -> None:
        area_id, _ = _create_production_context(connection)
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number="abc 123", quantity=5, current_area_id=area_id
            ),
        )

    def test_current_area_projection_is_not_nullable(self, connection: Connection) -> None:
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(), quantity=5, current_area_id=None
            ),
        )

    def test_current_area_must_reference_a_real_area(self, connection: Connection) -> None:
        _rejected(
            connection,
            sa.insert(models.QuantityFlow).values(
                part_number=_unique("PN").upper(), quantity=5, current_area_id=999_999_999
            ),
        )


class TestPartMovement:
    def test_received_movement_is_recorded_with_flow_pn(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        movement_id = _insert_movement(connection, flow_id, area_id, operation_id)
        recorded = connection.execute(
            sa.select(models.PartMovement.part_number).where(models.PartMovement.id == movement_id)
        ).scalar_one()
        flow_pn = connection.execute(
            sa.select(models.QuantityFlow.part_number).where(models.QuantityFlow.id == flow_id)
        ).scalar_one()
        assert recorded == flow_pn

    def test_movement_cannot_carry_another_flows_pn(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_a = _create_flow(connection, area_id)
        flow_b = _create_flow(connection, area_id)
        pn_of_b = connection.execute(
            sa.select(models.QuantityFlow.part_number).where(models.QuantityFlow.id == flow_b)
        ).scalar_one()
        values = _movement_values(connection, flow_a, area_id, operation_id, part_number=pn_of_b)
        _rejected(connection, sa.insert(models.PartMovement).values(**values))

    def test_device_event_id_is_unique(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        event_id = str(uuid.uuid4())
        _insert_movement(connection, flow_id, area_id, operation_id, device_event_id=event_id)
        values = _movement_values(
            connection, flow_id, area_id, operation_id, device_event_id=event_id
        )
        _rejected(connection, sa.insert(models.PartMovement).values(**values))

    def test_received_shape_forbids_a_source_area(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        values = _movement_values(connection, flow_id, area_id, operation_id, from_area_id=area_id)
        _rejected(connection, sa.insert(models.PartMovement).values(**values))

    def test_future_movement_types_are_not_pre_implemented(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        values = _movement_values(
            connection, flow_id, area_id, operation_id, movement_type="TRANSFERRED"
        )
        _rejected(connection, sa.insert(models.PartMovement).values(**values))

    @pytest.mark.parametrize("quantity", [0, -1])
    def test_movement_quantity_must_be_positive(
        self, connection: Connection, quantity: int
    ) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        values = _movement_values(connection, flow_id, area_id, operation_id, quantity=quantity)
        _rejected(connection, sa.insert(models.PartMovement).values(**values))

    def test_history_rebuilds_the_current_area_projection(self, connection: Connection) -> None:
        # The projection value is defined as the to_area_id of the
        # flow's latest Movement — Phase 3 history must already carry
        # enough data to rebuild it.
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        _insert_movement(connection, flow_id, area_id, operation_id)
        latest_to_area = connection.execute(
            sa.select(models.PartMovement.to_area_id)
            .where(models.PartMovement.quantity_flow_id == flow_id)
            .order_by(models.PartMovement.id.desc())
            .limit(1)
        ).scalar_one()
        projected = connection.execute(
            sa.select(models.QuantityFlow.current_area_id).where(models.QuantityFlow.id == flow_id)
        ).scalar_one()
        assert projected == latest_to_area


class TestPartMovementImmutability:
    def test_update_is_rejected_by_postgresql(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        movement_id = _insert_movement(connection, flow_id, area_id, operation_id)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                sa.update(models.PartMovement)
                .where(models.PartMovement.id == movement_id)
                .values(quantity=1)
            )

    def test_delete_is_rejected_by_postgresql(self, connection: Connection) -> None:
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        movement_id = _insert_movement(connection, flow_id, area_id, operation_id)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id)
            )

    def test_truncate_is_rejected_by_postgresql(self, connection: Connection) -> None:
        # The statement-level trigger also guards the TRUNCATE path, so
        # even a bulk wipe of history is impossible for the application.
        area_id, operation_id = _create_production_context(connection)
        flow_id = _create_flow(connection, area_id)
        _insert_movement(connection, flow_id, area_id, operation_id)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(sa.text("TRUNCATE part_movements"))
