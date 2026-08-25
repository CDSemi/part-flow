"""Integration tests for the Phase 4 audit persistence schema.

Runs the real Alembic migration chain against an isolated, temporary
PostgreSQL database (created and dropped by the module fixture), then
verifies the Phase 4 invariants PostgreSQL must enforce:

- exact Phase 4 table boundary: exactly the Phase 3 + Phase 3.5 tables
  plus `audit_events` — no Workers/Users/ScanSession tables and no
  Phase 5+ production columns pre-implemented;
- `audit_events` shape per SLICE1_DATA_MODEL §16–§17: event-type and
  entity-type vocabulary CHECKs, polymorphic FK-free `entity_id`,
  nullable reference-free `actor_reference`, nullable jsonb
  before/after/metadata snapshots, and the `(entity_type, entity_id,
  id)` history index;
- trigger-enforced append-only immutability (UPDATE, DELETE, and
  TRUNCATE all raise), same pattern as `part_movements` and
  `machine_lifecycle_events`;
- the `0005_phase4_release_index` partial expression index that
  serves the released-quantity derivation, stored with the exact
  JSONB subscript expression the application emits;
- models↔migration metadata parity at head;
- clean downgrade back to the Phase 3.5 boundary
  (`0003_phase35_environment`) with a successful re-upgrade.

The module fixture migrates head → base → head, so a downgrade that
leaves any object behind fails the module before any test runs. Every
test runs in its own rolled-back transaction against isolated data; the
development database configured in DATABASE_URL is never touched beyond
CREATE/DROP of the dedicated test databases.

Phase 4 is the current head, so this module carries the head-level
coverage (models↔migration parity, exact table set). Phase 4 owns TWO
revisions — `0004_phase4_audit` (the `audit_events` table) and
`0005_phase4_release_index` (the release-context index) — so the phase
boundary is the later one. When a later phase adds its migration, pin
this module to `0005_phase4_release_index` and move the head-level
coverage into that phase's schema test — the same handoff
test_phase3_schema.py and test_phase35_schema.py already made.
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

_PHASE35_REVISION = "0003_phase35_environment"
# The FIRST of Phase 4's two revisions — the target of the "0005 removes
# only its index" downgrade test, deliberately not the phase boundary
# (that is `0005_phase4_release_index`, the current head).
_PHASE4_AUDIT_REVISION = "0004_phase4_audit"

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

_PHASE4_TABLES = {"audit_events"}


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
    name = "partflow_test_phase4_schema"
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


def _audit_values(**overrides: Any) -> dict[str, Any]:
    return {
        "event_type": "CREATED",
        "entity_type": "WorkOrder",
        "entity_id": "1",
        "occurred_at": datetime.datetime.now(datetime.UTC),
        **overrides,
    }


def _insert_audit_event(connection: Connection, **overrides: Any) -> int:
    return connection.execute(
        sa.insert(models.AuditEvent)
        .values(**_audit_values(**overrides))
        .returning(models.AuditEvent.id)
    ).scalar_one()


def _rejected(connection: Connection, statement: sa.Insert | sa.Update | sa.Delete) -> None:
    """Assert the statement is rejected by a database integrity rule."""
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(statement)


class TestMigrationSchema:
    def test_head_creates_exactly_the_phase4_boundary(self, migrated_engine: Engine) -> None:
        tables = set(inspect(migrated_engine).get_table_names())
        expected = _PHASE3_TABLES | _PHASE35_TABLES | _PHASE4_TABLES
        assert tables >= expected
        # Nothing beyond the Phase 4 scope (plus Alembic's own version
        # table) exists — no workers, users, scan_sessions,
        # work_order_allocations, or other pre-implemented future
        # tables.
        assert tables - expected == {"alembic_version"}

    def test_no_phase5_plus_columns_exist(self, migrated_engine: Engine) -> None:
        inspector = inspect(migrated_engine)
        flow_columns = {column["name"] for column in inspector.get_columns("quantity_flows")}
        movement_columns = {column["name"] for column in inspector.get_columns("part_movements")}
        area_columns = {column["name"] for column in inspector.get_columns("areas")}
        step_columns = {column["name"] for column in inspector.get_columns("route_steps")}
        wo_columns = {column["name"] for column in inspector.get_columns("work_orders")}
        # Machine assignment / current-machine projection is Phase 6;
        # SPLIT lineage is Phase 8.
        assert "current_machine_id" not in flow_columns
        assert "parent_flow_id" not in flow_columns
        # No PartMovement widening of any kind in Phase 4.
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
        # The derived done date arrives with Phase 10 allocation.
        assert "completed_at" not in wo_columns

    def test_movement_type_check_is_not_pre_widened(self, connection: Connection) -> None:
        # Phase 4 adds audit persistence only: the movement-type
        # vocabulary stays exactly RECEIVED until Phase 5 widens it.
        check_clause = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_part_movements_movement_type'"
            )
        ).scalar_one()
        assert "RECEIVED" in check_clause
        assert "TRANSFERRED" not in check_clause

    def test_audit_event_column_shape(self, migrated_engine: Engine) -> None:
        columns = {
            column["name"]: column
            for column in inspect(migrated_engine).get_columns("audit_events")
        }
        assert set(columns) == {
            "id",
            "event_type",
            "entity_type",
            "entity_id",
            "actor_reference",
            "occurred_at",
            "before_data",
            "after_data",
            "metadata",
        }
        assert isinstance(columns["id"]["type"], sa.BigInteger)
        for required in ("event_type", "entity_type", "entity_id", "occurred_at"):
            assert columns[required]["nullable"] is False, required
        for optional in ("actor_reference", "before_data", "after_data", "metadata"):
            assert columns[optional]["nullable"] is True, optional

    def test_audit_events_carry_no_foreign_key(self, migrated_engine: Engine) -> None:
        # entity_id is polymorphic by design (internal PK for
        # WorkOrder/WorkOrderDemand, canonical PN string for
        # PartNumber): integrity comes from writing the audit row in
        # the same transaction as the audited change, never from an FK.
        assert inspect(migrated_engine).get_foreign_keys("audit_events") == []

    def test_audit_events_history_index_exists(self, migrated_engine: Engine) -> None:
        indexes = {
            index["name"]: index for index in inspect(migrated_engine).get_indexes("audit_events")
        }
        history = indexes["ix_audit_events_entity_type_entity_id_id"]
        assert history["column_names"] == ["entity_type", "entity_id", "id"]

    def test_release_context_index_matches_the_query_it_serves(
        self, migrated_engine: Engine
    ) -> None:
        """`0005_phase4_release_index` must index the expression the
        application actually emits, restricted to RECEIVED.

        `released_quantities` derives a demand's released quantity from
        the immutable RECEIVED metadata context (no counter, no FK, no
        column). PostgreSQL only uses an expression index when the
        stored expression matches the query's expression tree, so the
        index carries the JSONB SUBSCRIPT form SQLAlchemy renders —
        `metadata['context'] ->> '...'` — and NOT the `->` operator
        form. This asserts the stored definition rather than a query
        plan: plans are planner- and statistics-dependent, the index
        definition is not.
        """
        with migrated_engine.connect() as conn:
            definition = conn.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes"
                    " WHERE tablename = 'part_movements' AND indexname = :name"
                ),
                {"name": "ix_part_movements_received_demand_context"},
            ).scalar_one()
        normalized = " ".join(definition.split())
        assert "metadata['context'::text] ->> 'work_order_demand_id'::text" in normalized
        assert "::integer" in normalized
        assert "WHERE (movement_type = 'RECEIVED'::text)" in normalized

    def test_downgrading_only_the_index_revision_keeps_the_audit_schema(
        self, admin_engine: Engine
    ) -> None:
        # 0005 adds one index and nothing else: downgrading it must
        # remove exactly that index and leave the Phase 4 audit schema
        # (0004) completely intact.
        name = "partflow_test_phase4_index_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE4_AUDIT_REVISION)
            engine = create_engine(url)
            try:
                tables = set(inspect(engine).get_table_names())
                assert tables == (
                    _PHASE3_TABLES | _PHASE35_TABLES | _PHASE4_TABLES | {"alembic_version"}
                )
                with engine.connect() as connection:
                    remaining = connection.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_indexes"
                            " WHERE indexname = 'ix_part_movements_received_demand_context'"
                        )
                    ).scalar_one()
                assert remaining == 0
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)

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
        assert "ck_audit_events_event_type" in names
        assert "ck_audit_events_entity_type" in names
        doubled = {name for name in names if name.count("ck_") > 1}
        assert doubled == set()

    def test_downgrade_restores_the_phase35_boundary(self, admin_engine: Engine) -> None:
        # Downgrading BOTH Phase 4 revisions (0005 then 0004) must leave
        # exactly the Phase 3.5 schema behind — the release-context
        # index, audit_events, its trigger and its function removed,
        # nothing of Phase 3/3.5 touched — and a re-upgrade must succeed
        # from there.
        name = "partflow_test_phase4_downgrade"
        _create_temp_database(admin_engine, name)
        url = make_url(os.environ["DATABASE_URL"]).set(database=name)
        config = _alembic_config(url)
        try:
            command.upgrade(config, "head")
            command.downgrade(config, _PHASE35_REVISION)
            engine = create_engine(url)
            try:
                tables = set(inspect(engine).get_table_names())
                assert tables == _PHASE3_TABLES | _PHASE35_TABLES | {"alembic_version"}
                with engine.connect() as connection:
                    leftover_functions = connection.execute(
                        sa.text(
                            "SELECT proname FROM pg_proc"
                            " WHERE proname = 'partflow_audit_events_forbid_mutation'"
                        )
                    ).all()
                assert leftover_functions == []
            finally:
                engine.dispose()
            command.upgrade(config, "head")
        finally:
            _drop_temp_database(admin_engine, name)


class TestAuditEventShape:
    def test_creation_event_is_recorded(self, connection: Connection) -> None:
        event_id = _insert_audit_event(
            connection,
            entity_type="WorkOrder",
            entity_id="42",
            actor_reference="dev-seed",
            before_data=None,
            after_data={"work_order_number": "007125"},
            metadata_={"source": "manual entry"},
        )
        row = connection.execute(
            sa.select(
                models.AuditEvent.event_type,
                models.AuditEvent.entity_type,
                models.AuditEvent.entity_id,
                models.AuditEvent.before_data,
                models.AuditEvent.after_data,
            ).where(models.AuditEvent.id == event_id)
        ).one()
        assert (row.event_type, row.entity_type, row.entity_id) == ("CREATED", "WorkOrder", "42")
        # before_data is NULL for creation events (SLICE1 §16).
        assert row.before_data is None
        assert row.after_data == {"work_order_number": "007125"}

    def test_update_event_records_both_snapshots(self, connection: Connection) -> None:
        event_id = _insert_audit_event(
            connection,
            event_type="UPDATED",
            entity_type="WorkOrderDemand",
            entity_id="7",
            before_data={"requested_quantity": 10},
            after_data={"requested_quantity": 25},
        )
        row = connection.execute(
            sa.select(models.AuditEvent.before_data, models.AuditEvent.after_data).where(
                models.AuditEvent.id == event_id
            )
        ).one()
        assert row.before_data == {"requested_quantity": 10}
        assert row.after_data == {"requested_quantity": 25}

    def test_pn_master_events_use_the_canonical_pn_as_entity_id(
        self, connection: Connection
    ) -> None:
        # For PartNumber the natural key is the canonical PN string —
        # entity_id is text so it carries it directly.
        part_number = _unique("PN").upper()
        event_id = _insert_audit_event(connection, entity_type="PartNumber", entity_id=part_number)
        stored = connection.execute(
            sa.select(models.AuditEvent.entity_id).where(models.AuditEvent.id == event_id)
        ).scalar_one()
        assert stored == part_number

    def test_actor_reference_stays_nullable(self, connection: Connection) -> None:
        # Until authentication exists (Phase 14) the actor is NULL or
        # an explicitly configured development/system identifier.
        _insert_audit_event(connection, actor_reference=None)

    @pytest.mark.parametrize("invalid", ["DELETED", "RELEASED", "created", ""])
    def test_unknown_event_types_are_rejected(self, connection: Connection, invalid: str) -> None:
        values = _audit_values(event_type=invalid)
        _rejected(connection, sa.insert(models.AuditEvent).values(**values))

    @pytest.mark.parametrize(
        "invalid",
        # Production activity and Machine lifecycle are never
        # audit_events entities: PartMovement is the production audit
        # record and machine_lifecycle_events owns Machine history.
        ["QuantityFlow", "PartMovement", "Machine", "workorder", ""],
    )
    def test_unknown_entity_types_are_rejected(self, connection: Connection, invalid: str) -> None:
        values = _audit_values(entity_type=invalid)
        _rejected(connection, sa.insert(models.AuditEvent).values(**values))

    def test_entity_id_is_required(self, connection: Connection) -> None:
        values = _audit_values(entity_id=None)
        _rejected(connection, sa.insert(models.AuditEvent).values(**values))

    def test_audit_rows_never_depend_on_the_audited_row(self, connection: Connection) -> None:
        # Polymorphic, FK-free by design: an audit row for an entity id
        # that no longer exists (e.g. a hard-deleted PN master) stays
        # valid history.
        _insert_audit_event(connection, entity_type="PartNumber", entity_id=_unique("GONE").upper())


class TestAuditEventImmutability:
    def test_update_is_rejected_by_postgresql(self, connection: Connection) -> None:
        event_id = _insert_audit_event(connection)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                sa.update(models.AuditEvent)
                .where(models.AuditEvent.id == event_id)
                .values(after_data={"rewritten": True})
            )

    def test_delete_is_rejected_by_postgresql(self, connection: Connection) -> None:
        event_id = _insert_audit_event(connection)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(sa.delete(models.AuditEvent).where(models.AuditEvent.id == event_id))

    def test_truncate_is_rejected_by_postgresql(self, connection: Connection) -> None:
        # Statement-level trigger: even a bulk wipe of the audit
        # history is impossible for the application.
        _insert_audit_event(connection)
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(sa.text("TRUNCATE audit_events"))

    def test_edits_append_new_rows_preserving_prior_history(self, connection: Connection) -> None:
        # Append-only history per entity: a later UPDATED row joins the
        # earlier CREATED row — nothing is rewritten — and the history
        # index columns return them in write order.
        entity_id = "314"
        first = _insert_audit_event(
            connection,
            entity_type="WorkOrderDemand",
            entity_id=entity_id,
            after_data={"requested_quantity": 10},
        )
        second = _insert_audit_event(
            connection,
            event_type="UPDATED",
            entity_type="WorkOrderDemand",
            entity_id=entity_id,
            before_data={"requested_quantity": 10},
            after_data={"requested_quantity": 25},
        )
        history = connection.execute(
            sa.select(models.AuditEvent.id, models.AuditEvent.event_type)
            .where(
                models.AuditEvent.entity_type == "WorkOrderDemand",
                models.AuditEvent.entity_id == entity_id,
            )
            .order_by(models.AuditEvent.id)
        ).all()
        assert [(row.id, row.event_type) for row in history] == [
            (first, "CREATED"),
            (second, "UPDATED"),
        ]
