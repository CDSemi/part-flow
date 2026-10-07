"""Integration tests for the Phase 12 Hot rank migration.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0013_phase12_priority` adds (IMPLEMENTATION_ROADMAP
Phase 12 — Priority Management; PROJECT_PROFILE §21; invariant H1):

- exact boundary: the module migrates to `0013_phase12_priority`; the
  positive CHECK and the UNIQUE on `work_order_demands.priority_rank` exist with
  their exact names, and PostgreSQL refuses a duplicate rank and a rank
  of 0 while admitting any number of unranked demands;
- the Hot list idempotency index on `audit_events` stores the JSONB
  subscript expression the application emits, partial on
  `WorkOrderDemand` rows;
- the refusing pre-check: existing ranks that are not exactly 1..N (a
  duplicate, a rank of 0, a gap) refuse the upgrade, which leaves the
  database at its starting revision (0012, or 0011 when 0012 was
  pending too) with the ranks untouched; dense ranks upgrade cleanly;
- clean downgrade back to the Phase 11 boundary with a successful
  re-upgrade.

This module is pinned to `0013_phase12_priority` (Phase 13 added the
Workers migration 0014): every assertion documents the Phase 12
boundary as it shipped, and the head-level coverage (models↔schema
parity at head) lives in the current head phase's schema test
(`test_phase14_schema.py` since Phase 14 slice 1).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PHASE10_REVISION = "0011_phase10_stock_allocation"
_PHASE11_REVISION = "0012_phase11_tracking_index"
_PHASE12_REVISION = "0013_phase12_priority"
_CHECK = "ck_work_order_demands_priority_rank_positive"
_UNIQUE = "uq_work_order_demands_priority_rank"
_INDEX = "ix_audit_events_hot_list_device_event_id"


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


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def migrated_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated 0013 → base → 0013 through real Alembic runs."""
    name = "partflow_test_phase12_schema"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    command.upgrade(config, _PHASE12_REVISION)
    command.downgrade(config, "base")
    command.upgrade(config, _PHASE12_REVISION)
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


def _seed_demand(connection: Connection, rank: int | None) -> int:
    """One Work Order with one demand line carrying ``rank`` (raw SQL, so
    it works at 0012 as well as at 0013)."""
    work_order_id = connection.execute(
        sa.text("INSERT INTO work_orders (received_date) VALUES (CURRENT_DATE) RETURNING id")
    ).scalar_one()
    return int(
        connection.execute(
            sa.text(
                "INSERT INTO work_order_demands"
                " (work_order_id, part_number, request_type, requested_quantity, priority_rank)"
                " VALUES (:work_order_id, 'PN-HOT', 'NEW', 5, :rank) RETURNING id"
            ),
            {"work_order_id": work_order_id, "rank": rank},
        ).scalar_one()
    )


def test_migrated_revision_is_the_phase12_revision(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        assert _version(connection) == _PHASE12_REVISION


def test_rank_check_and_unique_exist_with_exact_names(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    checks = {
        str(check["name"]): str(check["sqltext"])
        for check in inspector.get_check_constraints("work_order_demands")
    }
    assert "priority_rank >= 1" in checks[_CHECK]
    uniques = {
        str(unique["name"]): unique["column_names"]
        for unique in inspector.get_unique_constraints("work_order_demands")
    }
    assert uniques[_UNIQUE] == ["priority_rank"]


def test_database_refuses_a_duplicate_rank(connection: Connection) -> None:
    _seed_demand(connection, 1)
    with pytest.raises(IntegrityError) as raised:
        _seed_demand(connection, 1)
    assert raised.value.orig is not None
    assert _UNIQUE in str(raised.value.orig)


def test_database_refuses_a_rank_of_zero(connection: Connection) -> None:
    with pytest.raises(IntegrityError) as raised:
        _seed_demand(connection, 0)
    assert _CHECK in str(raised.value.orig)


def test_database_admits_several_unranked_demands(connection: Connection) -> None:
    for _ in range(3):
        _seed_demand(connection, None)
    count = connection.execute(
        sa.text("SELECT count(*) FROM work_order_demands WHERE priority_rank IS NULL")
    ).scalar_one()
    assert count >= 3


def test_idempotency_index_stores_the_subscript_expression(migrated_engine: Engine) -> None:
    """PostgreSQL only uses an expression index whose stored expression
    matches the query's tree: the JSONB SUBSCRIPT form the application
    emits — not the `->` operator form — partial on WorkOrderDemand."""
    with migrated_engine.connect() as connection:
        definition = connection.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": _INDEX},
        ).scalar_one()
    assert "ON public.audit_events" in definition
    assert "metadata['hot_list_change'::text] ->> 'device_event_id'::text" in definition
    assert "WHERE (entity_type = 'WorkOrderDemand'::text)" in definition


def test_the_idempotency_lookup_uses_the_index(connection: Connection) -> None:
    """The planner matches the lookup's expression to the stored one."""
    lookup = sa.select(models.AuditEvent.id).where(
        models.AuditEvent.entity_type == "WorkOrderDemand",
        models.HOT_LIST_DEVICE_EVENT_ID == "00000000-0000-0000-0000-000000000000",
    )
    sql = str(lookup.compile(connection, compile_kwargs={"literal_binds": True}))
    connection.execute(sa.text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(str(line) for line in connection.execute(sa.text(f"EXPLAIN {sql}")).scalars())
    assert _INDEX in plan


@pytest.fixture(scope="module")
def phase11_database(admin_engine: Engine) -> Iterator[URL]:
    """A database at the Phase 11 boundary for the pre-check cases."""
    name = "partflow_test_phase12_precheck"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    command.upgrade(_alembic_config(url), _PHASE11_REVISION)
    yield url
    _drop_temp_database(admin_engine, name)


def _reseed(engine: Engine, ranks: list[int | None]) -> dict[int, int | None]:
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM work_order_demands"))
        return {_seed_demand(connection, rank): rank for rank in ranks}


def _stored_ranks(engine: Engine) -> dict[int, int | None]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("SELECT id, priority_rank FROM work_order_demands"))
        return {int(demand_id): rank for demand_id, rank in rows}


@pytest.mark.parametrize(
    ("ranks", "offending_rank"),
    [
        pytest.param([1, 2, 2, None], 2, id="duplicate"),
        pytest.param([0, 1, 2], 0, id="zero"),
        pytest.param([1, 3, None], 3, id="gap"),
    ],
)
def test_upgrade_refuses_ranks_that_are_not_dense(
    phase11_database: URL, ranks: list[int | None], offending_rank: int
) -> None:
    engine = create_engine(phase11_database)
    try:
        seeded = _reseed(engine, ranks)
        with pytest.raises(RuntimeError) as refused:
            command.upgrade(_alembic_config(phase11_database), _PHASE12_REVISION)
        message = str(refused.value)
        offending = [demand_id for demand_id, rank in seeded.items() if rank == offending_rank]
        for demand_id in offending:
            assert f"work_order_demands.id {demand_id} (rank {offending_rank})" in message
        assert "Nothing was changed" in message
        # Transactional DDL: still at 0012, and no rank was rewritten.
        with engine.connect() as connection:
            assert _version(connection) == _PHASE11_REVISION
        assert _stored_ranks(engine) == seeded
        uniques = {
            unique["name"]
            for unique in inspect(engine).get_unique_constraints("work_order_demands")
        }
        assert _UNIQUE not in uniques
    finally:
        engine.dispose()


def test_a_refused_multi_revision_upgrade_stays_at_its_starting_revision(
    admin_engine: Engine,
) -> None:
    """env.py runs every pending revision in ONE transaction: refusing
    0013 also rolls back 0012, and the message names no wrong revision."""
    name = "partflow_test_phase12_precheck_multi"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    engine = create_engine(url)
    try:
        command.upgrade(_alembic_config(url), _PHASE10_REVISION)
        seeded = _reseed(engine, [1, 1])
        with pytest.raises(RuntimeError) as refused:
            command.upgrade(_alembic_config(url), _PHASE12_REVISION)
        message = str(refused.value)
        assert "Nothing was changed" in message
        assert "stays at the revision it started from" in message
        assert _PHASE11_REVISION not in message
        with engine.connect() as connection:
            assert _version(connection) == _PHASE10_REVISION
        assert _stored_ranks(engine) == seeded
        # 0012's index was rolled back with the refused 0013.
        indexes = {index["name"] for index in inspect(engine).get_indexes("part_movements")}
        assert "ix_part_movements_part_number_occurred_at_id" not in indexes
    finally:
        engine.dispose()
        _drop_temp_database(admin_engine, name)


def test_upgrade_accepts_dense_ranks(phase11_database: URL) -> None:
    engine = create_engine(phase11_database)
    try:
        seeded = _reseed(engine, [2, None, 1, 3, None])
        command.upgrade(_alembic_config(phase11_database), _PHASE12_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _PHASE12_REVISION
        assert _stored_ranks(engine) == seeded
    finally:
        engine.dispose()


def test_downgrade_restores_the_phase11_boundary(admin_engine: Engine) -> None:
    name = "partflow_test_phase12_downgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _PHASE12_REVISION)
        command.downgrade(config, _PHASE11_REVISION)
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            checks = {
                check["name"] for check in inspector.get_check_constraints("work_order_demands")
            }
            uniques = {
                unique["name"] for unique in inspector.get_unique_constraints("work_order_demands")
            }
            indexes = {index["name"] for index in inspector.get_indexes("audit_events")}
            assert _CHECK not in checks and _UNIQUE not in uniques and _INDEX not in indexes
            # The Phase 4 history index of the table is untouched.
            assert "ix_audit_events_entity_type_entity_id_id" in indexes
        finally:
            engine.dispose()
        command.upgrade(config, _PHASE12_REVISION)
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            assert _UNIQUE in {
                unique["name"] for unique in inspector.get_unique_constraints("work_order_demands")
            }
            assert _INDEX in {index["name"] for index in inspector.get_indexes("audit_events")}
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)
