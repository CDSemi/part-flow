"""Integration tests for the Phase 3.5 minimum environment setup schema.

Runs the real Alembic migrations to head against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the environment-setup invariants PostgreSQL must enforce:

- exact Phase 3.5 table boundary (no audit_events, no Workers/Users, no
  Phase 4–6 production columns pre-implemented);
- Area display/terminal configuration and PF:AREA barcode ownership,
  including assign-once barcode stability (assignable from NULL, never
  changed or cleared afterwards);
- Operation configuration fields;
- Scan Station stable URL-safe identity (one URL path segment:
  ASCII letters, digits, '.', '_' and '-' only), Area binding, and
  active flag (no
  database freeze of the binding — rebinding is an Application-layer
  configuration workflow);
- Machine Asset Tag uniqueness/canonical shape/immutability, active
  display-name uniqueness per Area, the maintenance-override shape, and
  the fixed Area of an active Machine (area_id changes only inside the
  RETIRED → ACTIVE reactivation update);
- machine_lifecycle_events RETIRED/REACTIVATED shape checks and
  trigger-enforced append-only immutability;
- the singleton Machine Asset Tag format configuration;
- models↔migration metadata parity at head;
- clean downgrade back to the Phase 3 boundary and to base.

The module fixture migrates head → base → head, so a Phase 3.5
downgrade that leaves any object behind fails the module before any
test runs. Every test runs in its own rolled-back transaction against
isolated data; the development database configured in DATABASE_URL is
never touched beyond CREATE/DROP of the dedicated test databases.
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

_PHASE35_TABLES = {
    "scan_stations",
    "machines",
    "machine_lifecycle_events",
    "machine_asset_tag_config",
}


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
    """Temporary database migrated head → base → head through real Alembic runs."""
    name = "partflow_test_phase35_schema"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    command.upgrade(config, "head")
    # Reversibility gate: the full downgrade must remove everything the
    # chain created (tables, triggers, functions, columns) or the
    # second upgrade fails.
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
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _create_department(connection: Connection) -> int:
    return connection.execute(
        sa.insert(models.Department).values(name=_unique("DEPT")).returning(models.Department.id)
    ).scalar_one()


def _create_area(connection: Connection, department_id: int | None = None, **overrides: Any) -> int:
    if department_id is None:
        department_id = _create_department(connection)
    values: dict[str, Any] = {"department_id": department_id, "name": _unique("AREA"), **overrides}
    return connection.execute(
        sa.insert(models.Area).values(**values).returning(models.Area.id)
    ).scalar_one()


def _create_machine(connection: Connection, area_id: int, **overrides: Any) -> int:
    values: dict[str, Any] = {
        "area_id": area_id,
        "name": _unique("Machine"),
        "asset_tag": _unique("CD"),
        **overrides,
    }
    return connection.execute(
        sa.insert(models.Machine).values(**values).returning(models.Machine.id)
    ).scalar_one()


def _lifecycle_values(machine_id: int, **overrides: Any) -> dict[str, Any]:
    return {
        "machine_id": machine_id,
        "event_type": "RETIRED",
        "occurred_at": datetime.datetime.now(datetime.UTC),
        "before_state": "ACTIVE",
        "after_state": "RETIRED",
        **overrides,
    }


def _insert_lifecycle_event(connection: Connection, machine_id: int, **overrides: Any) -> int:
    return connection.execute(
        sa.insert(models.MachineLifecycleEvent)
        .values(**_lifecycle_values(machine_id, **overrides))
        .returning(models.MachineLifecycleEvent.id)
    ).scalar_one()


def _rejected(connection: Connection, statement: sa.Insert | sa.Update | sa.Delete) -> None:
    """Assert the statement is rejected by a database integrity rule."""
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(statement)


class TestMigrationSchema:
    def test_head_creates_exactly_the_phase35_boundary(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        expected = _PHASE3_TABLES | _PHASE35_TABLES
        assert tables >= expected
        # Nothing beyond the Phase 3.5 scope (plus Alembic's own
        # version table) exists — no audit_events, workers, users,
        # scan_sessions, or other pre-implemented future tables.
        assert tables - expected == {"alembic_version"}

    def test_no_phase4_plus_columns_exist(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        area_columns = {column["name"] for column in inspector.get_columns("areas")}
        step_columns = {column["name"] for column in inspector.get_columns("route_steps")}
        wo_columns = {column["name"] for column in inspector.get_columns("work_orders")}
        # Machine assignment / current-machine projection is Phase 6.
        assert "current_machine_id" not in flow_columns
        assert "parent_flow_id" not in flow_columns
        # No PartMovement widening of any kind in Phase 3.5.
        assert movement_columns.isdisjoint(
            {
                "station_id",
                "worker_id",
                "scan_session_id",
                "machine_id",
                "movement_reason",
                "reverses_movement_id",
            }
        )
        # Direct-processing Worker identification is Phase 7.
        assert "worker_identification_mode" not in area_columns
        assert "preferred_machine_id" not in step_columns
        assert "completed_at" not in wo_columns

    def test_machine_barcode_is_never_an_independent_column(self, migrated_engine: Engine) -> None:
        # The Machine barcode is always derived from the immutable
        # Asset Tag (PF:MACHINE:<asset-tag>) — storing it separately
        # would create a second, divergeable identity.
        columns = {column["name"] for column in inspect(migrated_engine).get_columns("machines")}
        assert "barcode_value" not in columns
        assert "barcode" not in columns

    def test_lifecycle_events_carry_no_worker_or_user_reference(
        self, migrated_engine: Engine
    ) -> None:
        # Actor identity stays a nullable, reference-free value in
        # Phase 3.5 — no Worker or User foreign key exists.
        inspector = inspect(migrated_engine)
        referred = {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("machine_lifecycle_events")
        }
        assert referred == {"machines", "areas"}
        actor = next(
            column
            for column in inspector.get_columns("machine_lifecycle_events")
            if column["name"] == "actor"
        )
        assert actor["nullable"] is True

    def test_models_metadata_matches_the_migrated_schema(self, migrated_engine: Engine) -> None:
        # The SQLAlchemy mappings and the hand-written migrations must
        # describe the same schema at head, or later autogenerate runs
        # and ORM usage would silently disagree with the database.
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext

        with migrated_engine.connect() as conn:
            context = MigrationContext.configure(conn)
            diffs = compare_metadata(context, models.Base.metadata)
        assert diffs == []

    def test_check_constraint_names_are_deterministic_and_not_doubled(
        self, migrated_engine: Engine
    ) -> None:
        with migrated_engine.connect() as conn:
            names = {
                row[0]
                for row in conn.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conname LIKE 'ck!_%' ESCAPE '!'"
                    )
                )
            }
        assert "ck_areas_barcode_value_namespace" in names
        assert "ck_scan_stations_station_id_canonical" in names
        assert "ck_machines_asset_tag_canonical" in names
        assert "ck_machines_maintenance_shape" in names
        assert "ck_machine_lifecycle_events_state_shape" in names
        assert "ck_machine_asset_tag_config_singleton" in names
        doubled = {name for name in names if name.count("ck_") > 1}
        assert doubled == set()

    def test_downgrade_restores_the_phase3_boundary(self, admin_engine: Engine) -> None:
        # Downgrading only the Phase 3.5 revision must leave exactly
        # the Phase 3 schema behind — every Phase 3.5 table, column,
        # trigger, and function removed, nothing of Phase 3 touched —
        # and a re-upgrade must succeed from there.
        name = "partflow_test_phase35_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE3_REVISION)
            engine = create_engine(url)
            try:
                inspector = inspect(engine)
                tables = set(inspector.get_table_names())
                assert tables == _PHASE3_TABLES | {"alembic_version"}
                area_columns = {column["name"] for column in inspector.get_columns("areas")}
                assert area_columns.isdisjoint({"description", "color", "icon_url", "is_terminal"})
                operation_columns = {
                    column["name"] for column in inspector.get_columns("operations")
                }
                assert operation_columns.isdisjoint(
                    {"description", "default_expected_duration", "is_external"}
                )
                with engine.connect() as connection:
                    leftover_functions = connection.execute(
                        sa.text(
                            "SELECT proname FROM pg_proc WHERE proname IN"
                            " ('partflow_machines_forbid_asset_tag_change',"
                            "  'partflow_machines_forbid_area_change',"
                            "  'partflow_areas_forbid_barcode_change',"
                            "  'partflow_machine_lifecycle_events_forbid_mutation')"
                        )
                    ).all()
                assert leftover_functions == []
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)


class TestAreaConfiguration:
    def test_display_properties_and_terminal_flag_are_stored(self, connection: Connection) -> None:
        area_id = _create_area(
            connection,
            description="Stockroom shelving",
            color="var(--a-stockroom)",
            icon_url="https://example.invalid/stockroom.svg",
            is_terminal=True,
        )
        row = connection.execute(
            sa.select(
                models.Area.description,
                models.Area.color,
                models.Area.icon_url,
                models.Area.is_terminal,
            ).where(models.Area.id == area_id)
        ).one()
        assert row.description == "Stockroom shelving"
        assert row.color == "var(--a-stockroom)"
        assert row.icon_url == "https://example.invalid/stockroom.svg"
        assert row.is_terminal is True

    def test_is_terminal_defaults_to_false(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        stored = connection.execute(
            sa.select(models.Area.is_terminal).where(models.Area.id == area_id)
        ).scalar_one()
        assert stored is False

    def test_area_barcode_owns_the_pf_area_namespace(self, connection: Connection) -> None:
        _create_area(connection, barcode_value=f"PF:AREA:{_unique('recv')}")

    @pytest.mark.parametrize(
        "invalid",
        ["AREA:1", "PF:AREA:", "PF:AREA:has space", "PF:MACHINE:CD-0001", "recv-dock", ""],
    )
    def test_non_namespace_barcodes_are_rejected(
        self, connection: Connection, invalid: str
    ) -> None:
        department_id = _create_department(connection)
        _rejected(
            connection,
            sa.insert(models.Area).values(
                department_id=department_id, name=_unique("AREA"), barcode_value=invalid
            ),
        )

    def test_assigned_barcodes_stay_unique(self, connection: Connection) -> None:
        barcode = f"PF:AREA:{_unique('dock')}"
        _create_area(connection, barcode_value=barcode)
        department_id = _create_department(connection)
        _rejected(
            connection,
            sa.insert(models.Area).values(
                department_id=department_id, name=_unique("AREA"), barcode_value=barcode
            ),
        )

    def test_barcode_may_be_assigned_once_from_null(self, connection: Connection) -> None:
        # NULL → a valid value is the normal configuration step, and an
        # UPDATE rewriting the identical value is a harmless no-op.
        area_id = _create_area(connection)
        barcode = f"PF:AREA:{_unique('dock')}"
        connection.execute(
            sa.update(models.Area).where(models.Area.id == area_id).values(barcode_value=barcode)
        )
        connection.execute(
            sa.update(models.Area).where(models.Area.id == area_id).values(barcode_value=barcode)
        )
        stored = connection.execute(
            sa.select(models.Area.barcode_value).where(models.Area.id == area_id)
        ).scalar_one()
        assert stored == barcode

    def test_assigned_barcode_is_never_changed(self, connection: Connection) -> None:
        area_id = _create_area(connection, barcode_value=f"PF:AREA:{_unique('dock')}")
        with pytest.raises(DBAPIError, match="stable once assigned"), connection.begin_nested():
            connection.execute(
                sa.update(models.Area)
                .where(models.Area.id == area_id)
                .values(barcode_value=f"PF:AREA:{_unique('other')}")
            )

    def test_assigned_barcode_is_never_cleared(self, connection: Connection) -> None:
        area_id = _create_area(connection, barcode_value=f"PF:AREA:{_unique('dock')}")
        with pytest.raises(DBAPIError, match="stable once assigned"), connection.begin_nested():
            connection.execute(
                sa.update(models.Area).where(models.Area.id == area_id).values(barcode_value=None)
            )


class TestOperationConfiguration:
    def test_configuration_fields_are_stored(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        operation_id = connection.execute(
            sa.insert(models.Operation)
            .values(
                area_id=area_id,
                code=_unique("OP"),
                description="Outside plating service",
                default_expected_duration=datetime.timedelta(days=3),
                is_external=True,
            )
            .returning(models.Operation.id)
        ).scalar_one()
        row = connection.execute(
            sa.select(
                models.Operation.description,
                models.Operation.default_expected_duration,
                models.Operation.is_external,
            ).where(models.Operation.id == operation_id)
        ).one()
        assert row.description == "Outside plating service"
        assert row.default_expected_duration == datetime.timedelta(days=3)
        assert row.is_external is True

    def test_is_external_defaults_to_false(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        operation_id = connection.execute(
            sa.insert(models.Operation)
            .values(area_id=area_id, code=_unique("OP"))
            .returning(models.Operation.id)
        ).scalar_one()
        stored = connection.execute(
            sa.select(models.Operation.is_external).where(models.Operation.id == operation_id)
        ).scalar_one()
        assert stored is False


class TestScanStation:
    def test_station_id_is_the_stable_natural_key(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        station_id = _unique("LATHE-ST").upper()
        connection.execute(
            sa.insert(models.ScanStation).values(station_id=station_id, area_id=area_id)
        )
        _rejected(
            connection,
            sa.insert(models.ScanStation).values(station_id=station_id, area_id=area_id),
        )

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "HAS SPACE",
            "TAB\tID",
            "LINE\nID",
            # URL-unsafe: the Station ID travels verbatim as one URL
            # path segment (/scan-station/<station-id>).
            "ST/1",
            "ST?1",
            "ST#1",
            "ST:1",
            "ST%1",
            "ST&1",
            "ST+1",
            "STÄ-1",
        ],
    )
    def test_non_canonical_station_ids_are_rejected(
        self, connection: Connection, invalid: str
    ) -> None:
        area_id = _create_area(connection)
        _rejected(
            connection,
            sa.insert(models.ScanStation).values(station_id=invalid, area_id=area_id),
        )

    def test_url_safe_station_ids_are_accepted(self, connection: Connection) -> None:
        # Exactly the simple URL-safe identifier charset is storable:
        # ASCII letters, digits, '.', '_' and '-'.
        area_id = _create_area(connection)
        for station_id in ("LATHE-ST-01", "st_0.9-A"):
            connection.execute(
                sa.insert(models.ScanStation).values(station_id=station_id, area_id=area_id)
            )

    def test_station_requires_a_real_area_binding(self, connection: Connection) -> None:
        _rejected(
            connection,
            sa.insert(models.ScanStation).values(station_id=_unique("ST"), area_id=None),
        )
        _rejected(
            connection,
            sa.insert(models.ScanStation).values(station_id=_unique("ST"), area_id=999_999_999),
        )

    def test_is_active_defaults_to_true_and_stays_editable(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        station_id = _unique("ST")
        connection.execute(
            sa.insert(models.ScanStation).values(station_id=station_id, area_id=area_id)
        )
        stored = connection.execute(
            sa.select(models.ScanStation.is_active).where(
                models.ScanStation.station_id == station_id
            )
        ).scalar_one()
        assert stored is True
        # Deactivation is a normal configuration change. Rebinding a
        # station (station_id/area_id) is deliberately NOT frozen by a
        # database trigger — it is a configuration workflow controlled
        # at the Application layer.
        connection.execute(
            sa.update(models.ScanStation)
            .where(models.ScanStation.station_id == station_id)
            .values(is_active=False)
        )


class TestMachine:
    def test_asset_tags_are_unique_forever(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        other_area_id = _create_area(connection)
        asset_tag = _unique("CD")
        # A retired Machine keeps its Asset Tag — uniqueness still
        # blocks reuse by a new record, even in another Area.
        _create_machine(
            connection, area_id, asset_tag=asset_tag, retired_on=datetime.date(2025, 11, 3)
        )
        _rejected(
            connection,
            sa.insert(models.Machine).values(
                area_id=other_area_id, name=_unique("Machine"), asset_tag=asset_tag
            ),
        )

    @pytest.mark.parametrize("invalid", ["", "CD 0001", "CD:0001", "CD\t1"])
    def test_non_canonical_asset_tags_are_rejected(
        self, connection: Connection, invalid: str
    ) -> None:
        area_id = _create_area(connection)
        _rejected(
            connection,
            sa.insert(models.Machine).values(
                area_id=area_id, name=_unique("Machine"), asset_tag=invalid
            ),
        )

    def test_asset_tag_is_immutable_in_postgresql(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        with pytest.raises(DBAPIError, match="immutable"), connection.begin_nested():
            connection.execute(
                sa.update(models.Machine)
                .where(models.Machine.id == machine_id)
                .values(asset_tag=_unique("CD"))
            )

    def test_other_machine_fields_stay_editable(self, connection: Connection) -> None:
        # Immutability guards exactly the Asset Tag — display name and
        # asset metadata edits remain normal configuration changes.
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        connection.execute(
            sa.update(models.Machine)
            .where(models.Machine.id == machine_id)
            .values(name=_unique("Renamed"), notes="Relocated within the cell")
        )

    def test_active_display_names_are_unique_per_area(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        name = _unique("Lathe")
        _create_machine(connection, area_id, name=name)
        _rejected(
            connection,
            sa.insert(models.Machine).values(area_id=area_id, name=name, asset_tag=_unique("CD")),
        )

    def test_display_name_reuse_across_areas_and_replacements(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        other_area_id = _create_area(connection)
        name = _unique("Lathe")
        # Retired predecessor keeps the name; the active replacement
        # record may reuse it (PROJECT_PROFILE §7 Machine).
        _create_machine(connection, area_id, name=name, retired_on=datetime.date(2026, 2, 14))
        _create_machine(connection, area_id, name=name)
        # The same name in another Area never collides.
        _create_machine(connection, other_area_id, name=name)

    def test_maintenance_context_requires_an_active_override(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        _rejected(
            connection,
            sa.insert(models.Machine).values(
                area_id=area_id,
                name=_unique("Machine"),
                asset_tag=_unique("CD"),
                maintenance_note="Spindle bearing replacement",
            ),
        )
        _rejected(
            connection,
            sa.insert(models.Machine).values(
                area_id=area_id,
                name=_unique("Machine"),
                asset_tag=_unique("CD"),
                maintenance_expected_return=datetime.date(2026, 9, 1),
            ),
        )
        _create_machine(
            connection,
            area_id,
            maintenance_since=datetime.datetime.now(datetime.UTC),
            maintenance_note="Spindle bearing replacement",
            maintenance_expected_return=datetime.date(2026, 9, 1),
        )

    def test_machine_requires_a_real_area_binding(self, connection: Connection) -> None:
        _rejected(
            connection,
            sa.insert(models.Machine).values(
                area_id=999_999_999, name=_unique("Machine"), asset_tag=_unique("CD")
            ),
        )

    def test_active_machine_area_is_fixed(self, connection: Connection) -> None:
        # Moving capacity is a replacement (retire + new record), never
        # an edit of an active Machine.
        area_id = _create_area(connection)
        other_area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        with pytest.raises(DBAPIError, match="reactivation"), connection.begin_nested():
            connection.execute(
                sa.update(models.Machine)
                .where(models.Machine.id == machine_id)
                .values(area_id=other_area_id)
            )

    def test_retired_machine_area_stays_fixed_while_retired(self, connection: Connection) -> None:
        # The Area change is permitted only INSIDE the reactivation
        # update — a retired record that stays retired never moves.
        area_id = _create_area(connection)
        other_area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id, retired_on=datetime.date(2026, 2, 14))
        with pytest.raises(DBAPIError, match="reactivation"), connection.begin_nested():
            connection.execute(
                sa.update(models.Machine)
                .where(models.Machine.id == machine_id)
                .values(area_id=other_area_id)
            )

    def test_reactivation_update_may_change_the_area(self, connection: Connection) -> None:
        # RETIRED → ACTIVE in one UPDATE may carry the forward-looking
        # Area change of the physical machine that moved while retired.
        area_id = _create_area(connection)
        other_area_id = _create_area(connection)
        moved_id = _create_machine(connection, area_id, retired_on=datetime.date(2026, 2, 14))
        connection.execute(
            sa.update(models.Machine)
            .where(models.Machine.id == moved_id)
            .values(retired_on=None, area_id=other_area_id)
        )
        row = connection.execute(
            sa.select(models.Machine.area_id, models.Machine.retired_on).where(
                models.Machine.id == moved_id
            )
        ).one()
        assert (row.area_id, row.retired_on) == (other_area_id, None)
        # Reactivation in place (no move) stays valid as well.
        in_place_id = _create_machine(connection, area_id, retired_on=datetime.date(2025, 11, 3))
        connection.execute(
            sa.update(models.Machine)
            .where(models.Machine.id == in_place_id)
            .values(retired_on=None)
        )


class TestMachineLifecycleEvents:
    def test_retirement_event_is_recorded(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        event_id = _insert_lifecycle_event(
            connection,
            machine_id,
            actor="M. Chen (Production Manager)",
            reason="Replaced by asset CD-0512",
        )
        row = connection.execute(
            sa.select(
                models.MachineLifecycleEvent.event_type,
                models.MachineLifecycleEvent.before_state,
                models.MachineLifecycleEvent.after_state,
            ).where(models.MachineLifecycleEvent.id == event_id)
        ).one()
        assert (row.event_type, row.before_state, row.after_state) == (
            "RETIRED",
            "ACTIVE",
            "RETIRED",
        )

    def test_actor_stays_nullable_reference_free(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        _insert_lifecycle_event(connection, machine_id, actor=None)

    def test_reactivation_may_record_a_forward_area_move(self, connection: Connection) -> None:
        from_area_id = _create_area(connection)
        to_area_id = _create_area(connection)
        machine_id = _create_machine(connection, from_area_id)
        _insert_lifecycle_event(
            connection,
            machine_id,
            event_type="REACTIVATED",
            before_state="RETIRED",
            after_state="ACTIVE",
            from_area_id=from_area_id,
            to_area_id=to_area_id,
            reason="Returned after overhaul",
        )

    def test_unknown_event_types_are_rejected(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        values = _lifecycle_values(machine_id, event_type="CREATED")
        _rejected(connection, sa.insert(models.MachineLifecycleEvent).values(**values))

    def test_state_pair_must_match_the_event_type(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        values = _lifecycle_values(machine_id, before_state="RETIRED", after_state="ACTIVE")
        _rejected(connection, sa.insert(models.MachineLifecycleEvent).values(**values))

    def test_area_move_is_reactivation_only_and_complete(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        other_area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        # A RETIRED event never carries an Area move.
        values = _lifecycle_values(machine_id, from_area_id=area_id, to_area_id=other_area_id)
        _rejected(connection, sa.insert(models.MachineLifecycleEvent).values(**values))
        # An incomplete pair is rejected.
        values = _lifecycle_values(
            machine_id,
            event_type="REACTIVATED",
            before_state="RETIRED",
            after_state="ACTIVE",
            from_area_id=area_id,
            to_area_id=None,
        )
        _rejected(connection, sa.insert(models.MachineLifecycleEvent).values(**values))
        # A "move" to the same Area is not a move.
        values = _lifecycle_values(
            machine_id,
            event_type="REACTIVATED",
            before_state="RETIRED",
            after_state="ACTIVE",
            from_area_id=area_id,
            to_area_id=area_id,
        )
        _rejected(connection, sa.insert(models.MachineLifecycleEvent).values(**values))

    def test_update_is_rejected_by_postgresql(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        event_id = _insert_lifecycle_event(connection, machine_id)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                sa.update(models.MachineLifecycleEvent)
                .where(models.MachineLifecycleEvent.id == event_id)
                .values(reason="rewritten")
            )

    def test_delete_is_rejected_by_postgresql(self, connection: Connection) -> None:
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        event_id = _insert_lifecycle_event(connection, machine_id)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                sa.delete(models.MachineLifecycleEvent).where(
                    models.MachineLifecycleEvent.id == event_id
                )
            )

    def test_truncate_is_rejected_by_postgresql(self, connection: Connection) -> None:
        # Statement-level trigger: even a bulk wipe of the lifecycle
        # history is impossible for the application.
        area_id = _create_area(connection)
        machine_id = _create_machine(connection, area_id)
        _insert_lifecycle_event(connection, machine_id)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(sa.text("TRUNCATE machine_lifecycle_events"))


class TestMachineAssetTagConfig:
    def test_format_row_is_stored_with_defaults(self, connection: Connection) -> None:
        connection.execute(
            sa.insert(models.MachineAssetTagConfig).values(id=1, prefix="CD-", digits=4)
        )
        row = connection.execute(
            sa.select(
                models.MachineAssetTagConfig.prefix,
                models.MachineAssetTagConfig.digits,
                models.MachineAssetTagConfig.next_sequence,
            )
        ).one()
        assert (row.prefix, row.digits, row.next_sequence) == ("CD-", 4, 1)

    def test_configuration_is_a_singleton(self, connection: Connection) -> None:
        connection.execute(
            sa.insert(models.MachineAssetTagConfig).values(id=1, prefix="CD-", digits=4)
        )
        # A second row is impossible under any id: id = 1 is taken (PK)
        # and every other id fails the singleton CHECK.
        _rejected(
            connection,
            sa.insert(models.MachineAssetTagConfig).values(id=1, prefix="XX-", digits=4),
        )
        _rejected(
            connection,
            sa.insert(models.MachineAssetTagConfig).values(id=2, prefix="XX-", digits=4),
        )

    def test_empty_prefix_is_valid(self, connection: Connection) -> None:
        connection.execute(
            sa.insert(models.MachineAssetTagConfig).values(id=1, prefix="", digits=4)
        )

    @pytest.mark.parametrize("invalid", ["CD ", "CD:", "C D-", "CD\t"])
    def test_prefix_rejects_whitespace_and_colon(
        self, connection: Connection, invalid: str
    ) -> None:
        _rejected(
            connection,
            sa.insert(models.MachineAssetTagConfig).values(id=1, prefix=invalid, digits=4),
        )

    @pytest.mark.parametrize("invalid", [0, 9, -1])
    def test_digits_stay_within_the_configured_range(
        self, connection: Connection, invalid: int
    ) -> None:
        _rejected(
            connection,
            sa.insert(models.MachineAssetTagConfig).values(id=1, prefix="CD-", digits=invalid),
        )

    def test_next_sequence_is_always_positive(self, connection: Connection) -> None:
        _rejected(
            connection,
            sa.insert(models.MachineAssetTagConfig).values(
                id=1, prefix="CD-", digits=4, next_sequence=0
            ),
        )
