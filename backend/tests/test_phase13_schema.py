"""Integration tests for the Phase 13 migrations.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0014_phase13_workers`, `0015_phase13_badge_check`,
`0016_phase13_environment_audit`, `0017_phase13_machine_audit`,
`0018_phase13_pn_check_collation`, `0019_phase13_worker_identity`,
`0020_phase13_worker_sessions` and `0021_phase13_badge_confirmation` add
(IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §7, §8.4, §8.11,
§8.12, §8.13, §10, §16, §19, §28; owner decisions OD-2, OD-3, OD-10,
S2-F6). Later Phase 13 slices extend this module:

- exact head boundary: `0021_phase13_badge_confirmation` is the single
  head;
- the `workers` table shape and its exact constraint names; no FK from
  it, and the only FKs to it are the three identity references
  (`areas.fixed_worker_id`, `part_movements.worker_id`,
  `work_order_allocations.allocated_by_worker_id`) and the session's
  Worker (`worker_sessions.worker_id`);
- the identity columns (0019): exact types, nullability and defaults,
  no index, the four CHECKs with their exact
  names and literals; the upgrade leaves every existing row without
  identity (never backfilled); the downgrade restores the 0018 boundary
  and refuses while identity history or Area mode configuration exists;
- the database CHECKs refuse every non-canonical badge (empty, padded,
  lowercase, `PF:` in any case, over 128 characters), a partial avatar,
  a non-image type and an avatar above 2 MiB, while the UNIQUE refuses a
  duplicate canonical badge — so case-insensitive uniqueness holds in
  PostgreSQL itself; the badge CHECK compares under the "C" collation,
  so it admits a badge the OS libc case tables would uppercase (`ɤ`)
  where the 0014 CHECK refused it;
- the widened audit vocabulary: entity `Worker`, the environment
  configuration entities (`Department`, `Area`, `Operation`,
  `ScanStation`, `MachineAssetTagConfig`), `Machine` and event `DELETED`
  are admitted, other values (`MachineLifecycleEvent` included) still
  refused, and the database entity CHECK names exactly the
  `AuditEntityType` members;
- the canonical PN CHECK of all four PN tables compares under the "C"
  collation, so it admits a PN the OS libc case tables would uppercase
  (`ɤ`) and still refuses ASCII lowercase, ASCII whitespace and the
  empty string;
- models↔migration metadata parity at head (moved here from the
  Phase 12 schema test, which is now pinned to 0013);
- clean downgrade back to the Phase 12 boundary with a successful
  re-upgrade, and the refusing downgrade while Worker configuration or
  Worker audit history exists (never deleted); the 0015 downgrade
  restores the 0014 CHECK and refuses while a row only 0015 admits
  exists; the 0016 downgrade restores the Worker vocabulary and refuses
  while environment audit history exists; the 0017 downgrade restores
  the environment vocabulary and refuses while Machine audit history
  exists; the 0018 downgrade restores the libc PN CHECK and refuses
  while a PN only 0018 admits exists;
- Worker Sessions (0020): the `worker_sessions`, `application_policy`,
  `areas.worker_session_timeout_minutes` and
  `part_movements.scan_session_id` shapes with their exact names and
  literals, the seeded policy row, the CHECKs, the open-session partial
  UNIQUE, the composite session FK and the mutation-guard trigger; the
  upgrade preserves every existing row (never backfilled); the downgrade
  restores the 0019 boundary and refuses while session history, timeout
  configuration or a policy audit row exists;
- badge-confirmation options (0021): the three boolean
  `application_policy` columns (NOT NULL, default `true`), seeded `true`
  on the singleton; the upgrade keeps the existing policy and audit
  rows; the downgrade restores the 0020 boundary and refuses while an
  option is off or a policy audit row records an option.

Phase 13 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to the
last Phase 13 revision and move the head-level coverage into that
phase's schema test.
"""

import datetime
import importlib.util
import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from alembic import command
from app.domain.enums import AuditEntityType, WorkerIdentificationMode, WorkerSessionEndReason
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PHASE12_REVISION = "0013_phase12_priority"
_PHASE13_REVISION = "0014_phase13_workers"
_BADGE_CHECK_REVISION = "0015_phase13_badge_check"
_ENVIRONMENT_AUDIT_REVISION = "0016_phase13_environment_audit"
_MACHINE_AUDIT_REVISION = "0017_phase13_machine_audit"
_PN_CHECK_REVISION = "0018_phase13_pn_check_collation"
_WORKER_IDENTITY_REVISION = "0019_phase13_worker_identity"
_WORKER_SESSIONS_REVISION = "0020_phase13_worker_sessions"
_HEAD_REVISION = "0021_phase13_badge_confirmation"
_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "20261004_0014_phase13_workers.py"
_BADGE_CHECK_MIGRATION_FILE = _VERSIONS_DIR / "20261004_0015_phase13_badge_check.py"
_ENVIRONMENT_AUDIT_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0016_phase13_environment_audit.py"
_MACHINE_AUDIT_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0017_phase13_machine_audit.py"
_PN_CHECK_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0018_phase13_pn_check_collation.py"
_WORKER_IDENTITY_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0019_phase13_worker_identity.py"
_WORKER_SESSIONS_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0020_phase13_worker_sessions.py"
_BADGE_CONFIRMATION_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0021_phase13_badge_confirmation.py"
_PHASE3_MIGRATION_FILE = _VERSIONS_DIR / "20260818_0002_phase3_minimum_domain_foundation.py"
_PHASE10_MIGRATION_FILE = _VERSIONS_DIR / "20260901_0011_phase10_stock_allocation.py"
# Python 3.12 (Unicode 15) leaves `ɤ` (U+0264) unchanged; the glibc
# `upper()` of the database collation maps it to U+A7CB.
_LIBC_UPPERCASED_BADGE = "ɤ1"
_LIBC_UPPERCASED_PN = "PNɤ1"
# Every table that keeps a PN by value under the canonical PN CHECK.
_PN_TABLES = ("part_numbers", "work_order_demands", "quantity_flows", "work_order_allocations")
_ENVIRONMENT_ENTITIES = ("Department", "Area", "Operation", "ScanStation", "MachineAssetTagConfig")
_WORKER_CHECKS = {
    "ck_workers_badge_barcode_canonical",
    "ck_workers_avatar_image_shape",
    "ck_workers_avatar_image_type",
    "ck_workers_avatar_image_size",
}


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


def _load_migration(path: Path = _MIGRATION_FILE) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"phase13_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def migrated_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated head → base → head through real Alembic runs."""
    name = "partflow_test_phase13_schema"
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


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _insert_worker(connection: Connection, badge: str, **avatar: object) -> None:
    columns = ["name", "badge_barcode", *avatar]
    values = [":name", ":badge", *(f":{column}" for column in avatar)]
    connection.execute(
        sa.text(f"INSERT INTO workers ({', '.join(columns)}) VALUES ({', '.join(values)})"),
        {"name": "Alex Tran", "badge": badge, **avatar},
    )


def _insert_audit(connection: Connection, event_type: str, entity_type: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
            " VALUES (:event_type, :entity_type, '1', now())"
        ),
        {"event_type": event_type, "entity_type": entity_type},
    )


def _refused_by(connection: Connection, constraint: str, statement: Callable[[], None]) -> None:
    """Run ``statement`` inside a savepoint; it must fail on ``constraint``."""
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        statement()
    savepoint.rollback()
    assert constraint in str(raised.value.orig)


# ---------------------------------------------------------------------------
# Head and shape
# ---------------------------------------------------------------------------


def test_head_is_the_phase13_revision(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        assert _version(connection) == _HEAD_REVISION


def test_alembic_has_a_single_head() -> None:
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    assert ScriptDirectory.from_config(config).get_heads() == [_HEAD_REVISION]


def test_workers_table_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(column["name"]): column for column in inspector.get_columns("workers")}
    expected = {
        "id": (sa.Integer, False),
        "name": (sa.Text, False),
        "badge_barcode": (sa.Text, False),
        "avatar_image": (sa.LargeBinary, True),
        "avatar_image_type": (sa.Text, True),
        "avatar_image_updated_at": (sa.DateTime, True),
        "is_active": (sa.Boolean, False),
        "created_at": (sa.DateTime, False),
        "updated_at": (sa.DateTime, False),
    }
    assert set(columns) == set(expected)
    for name, (type_, nullable) in expected.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is nullable, name
    for name in ("avatar_image_updated_at", "created_at", "updated_at"):
        assert getattr(columns[name]["type"], "timezone", None) is True


def test_workers_constraints_have_exact_names(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert inspector.get_pk_constraint("workers")["name"] == "pk_workers"
    uniques = {
        str(unique["name"]): unique["column_names"]
        for unique in inspector.get_unique_constraints("workers")
    }
    assert uniques == {"uq_workers_badge_barcode": ["badge_barcode"]}
    checks = {str(check["name"]) for check in inspector.get_check_constraints("workers")}
    assert checks == _WORKER_CHECKS
    # Only the UNIQUE's own index — no secondary index.
    assert {str(index["name"]) for index in inspector.get_indexes("workers")} <= {
        "uq_workers_badge_barcode"
    }


def test_worker_foreign_keys_are_exactly_the_identity_references(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    assert inspector.get_foreign_keys("workers") == []
    incoming = {
        str(foreign_key["name"]): (
            table,
            foreign_key["constrained_columns"],
            foreign_key["referred_columns"],
        )
        for table in inspector.get_table_names()
        for foreign_key in inspector.get_foreign_keys(table)
        if foreign_key["referred_table"] == "workers"
    }
    assert incoming == {
        "fk_areas_fixed_worker_id_workers": ("areas", ["fixed_worker_id"], ["id"]),
        "fk_part_movements_worker_id_workers": ("part_movements", ["worker_id"], ["id"]),
        "fk_work_order_allocations_allocated_by_worker_id_workers": (
            "work_order_allocations",
            ["allocated_by_worker_id"],
            ["id"],
        ),
        "fk_worker_sessions_worker_id_workers": ("worker_sessions", ["worker_id"], ["id"]),
    }


# The identity columns 0019 adds: (table, column) → nullable.
_IDENTITY_COLUMNS = {
    ("areas", "worker_identification_mode"): False,
    ("areas", "fixed_worker_id"): True,
    ("part_movements", "worker_id"): True,
    ("work_order_allocations", "allocated_by_worker_id"): True,
}
_IDENTITY_CHECKS = {
    ("areas", "ck_areas_worker_identification_mode"),
    ("areas", "ck_areas_fixed_worker_shape"),
    ("part_movements", "ck_part_movements_worker_requires_station"),
    ("work_order_allocations", "ck_work_order_allocations_worker_requires_station"),
}
_IDENTITY_FOREIGN_KEYS = {
    ("areas", "fk_areas_fixed_worker_id_workers"),
    ("part_movements", "fk_part_movements_worker_id_workers"),
    ("work_order_allocations", "fk_work_order_allocations_allocated_by_worker_id_workers"),
}


def test_identity_columns_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for (table, name), nullable in _IDENTITY_COLUMNS.items():
        columns = {str(column["name"]): column for column in inspector.get_columns(table)}
        column = columns[name]
        assert column["nullable"] is nullable, name
        if name == "worker_identification_mode":
            assert isinstance(column["type"], sa.Text)
            assert "'DISABLED'" in str(column["default"])
        else:
            assert isinstance(column["type"], sa.Integer), name
            assert column["default"] is None, name
        # No index on any identity column: no reader filters by Worker.
        for index in inspector.get_indexes(table):
            assert name not in index["column_names"], (table, index["name"])


def test_identity_checks_have_exact_names_and_literals(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for table, name in _IDENTITY_CHECKS:
        assert name in {str(check["name"]) for check in inspector.get_check_constraints(table)}
    migration = _load_migration(_WORKER_IDENTITY_MIGRATION_FILE)
    assert migration._WORKER_IDENTIFICATION_MODE_SQL == models.WORKER_IDENTIFICATION_MODE_SQL
    assert migration._AREA_FIXED_WORKER_SQL == models.AREA_FIXED_WORKER_SQL
    assert migration._MOVEMENT_WORKER_STATION_SQL == models.MOVEMENT_WORKER_STATION_SQL
    assert migration._ALLOCATION_WORKER_STATION_SQL == models.ALLOCATION_WORKER_STATION_SQL
    assert set(re.findall(r"'([^']*)'", models.WORKER_IDENTIFICATION_MODE_SQL)) == {
        mode.value for mode in WorkerIdentificationMode
    }


def test_migration_repeats_the_model_badge_check_verbatim() -> None:
    migration = _load_migration(_BADGE_CHECK_MIGRATION_FILE)
    assert migration._WORKER_BADGE_BARCODE_SQL == models.WORKER_BADGE_BARCODE_SQL
    # Its downgrade restores exactly the CHECK 0014 created.
    original = _load_migration()
    assert migration._PHASE13_S1_BADGE_BARCODE_SQL == original._WORKER_BADGE_BARCODE_SQL


# ---------------------------------------------------------------------------
# Database CHECKs and UNIQUE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("badge", ["", " X", "X ", "PF:1", "pf:1", "abc1", "Abc1", "A" * 129])
def test_database_refuses_a_non_canonical_badge(connection: Connection, badge: str) -> None:
    _refused_by(
        connection,
        "ck_workers_badge_barcode_canonical",
        lambda: _insert_worker(connection, badge),
    )


def test_database_refuses_a_partial_avatar(connection: Connection) -> None:
    _refused_by(
        connection,
        "ck_workers_avatar_image_shape",
        lambda: _insert_worker(connection, "PARTIAL", avatar_image=b"\x89PNG"),
    )


def test_database_refuses_a_non_image_type(connection: Connection) -> None:
    _refused_by(
        connection,
        "ck_workers_avatar_image_type",
        lambda: _insert_worker(
            connection,
            "GIF",
            avatar_image=b"GIF89a",
            avatar_image_type="image/gif",
            avatar_image_updated_at=datetime.datetime.now(datetime.UTC),
        ),
    )


def test_database_refuses_an_avatar_above_2_mib(connection: Connection) -> None:
    def oversized() -> None:
        connection.execute(
            sa.text(
                "INSERT INTO workers (name, badge_barcode, avatar_image, avatar_image_type,"
                " avatar_image_updated_at) VALUES ('Big', 'BIG',"
                " decode(repeat('00', 2097153), 'hex'), 'image/png', now())"
            )
        )

    _refused_by(connection, "ck_workers_avatar_image_size", oversized)


def test_database_admits_valid_workers(connection: Connection) -> None:
    _insert_worker(connection, "A" * 128)
    _insert_worker(connection, "STRASSE")
    # Domain-canonical, though the OS libc would uppercase it: the CHECK
    # never depends on the libc case tables.
    _insert_worker(connection, _LIBC_UPPERCASED_BADGE)
    connection.execute(
        sa.text(
            "INSERT INTO workers (name, badge_barcode, avatar_image, avatar_image_type,"
            " avatar_image_updated_at, is_active) VALUES ('Full', 'FULL 1',"
            " decode(repeat('00', 2097152), 'hex'), 'image/webp', now(), false)"
        )
    )
    count = connection.execute(sa.text("SELECT count(*) FROM workers")).scalar_one()
    assert count == 4


def test_database_refuses_a_duplicate_canonical_badge(connection: Connection) -> None:
    """A lowercase variant cannot be stored at all (canonical CHECK), so
    the plain UNIQUE is case-insensitive at the database level."""
    _insert_worker(connection, "ABC1")
    _refused_by(connection, "uq_workers_badge_barcode", lambda: _insert_worker(connection, "ABC1"))
    _refused_by(
        connection,
        "ck_workers_badge_barcode_canonical",
        lambda: _insert_worker(connection, "abc1"),
    )


# ---------------------------------------------------------------------------
# Audit vocabulary
# ---------------------------------------------------------------------------


def test_audit_admits_the_worker_entity_and_the_deleted_event(connection: Connection) -> None:
    _insert_audit(connection, "CREATED", "Worker")
    _insert_audit(connection, "DELETED", "PartNumber")
    _refused_by(
        connection,
        "ck_audit_events_entity_type",
        lambda: _insert_audit(connection, "CREATED", "MachineLifecycleEvent"),
    )
    _refused_by(
        connection,
        "ck_audit_events_event_type",
        lambda: _insert_audit(connection, "ARCHIVED", "Worker"),
    )


def test_audit_entity_check_names_exactly_the_enum(migrated_engine: Engine) -> None:
    entity_check = _audit_checks(migrated_engine)["ck_audit_events_entity_type"]
    assert set(re.findall(r"'([^']*)'", entity_check)) == {e.value for e in AuditEntityType}


def test_audit_admits_the_environment_entities(connection: Connection) -> None:
    for entity in (*_ENVIRONMENT_ENTITIES, "Machine", "ApplicationPolicy"):
        _insert_audit(connection, "CREATED", entity)
    for refused in ("MachineLifecycleEvent", "User", "WorkerSession"):

        def insert(entity: str = refused) -> None:
            _insert_audit(connection, "CREATED", entity)

        _refused_by(connection, "ck_audit_events_entity_type", insert)


def test_machine_audit_migration_restores_the_0016_literal() -> None:
    machine_audit = _load_migration(_MACHINE_AUDIT_MIGRATION_FILE)
    environment_audit = _load_migration(_ENVIRONMENT_AUDIT_MIGRATION_FILE)
    assert machine_audit._ENVIRONMENT_ENTITY_TYPES == environment_audit._ENVIRONMENT_ENTITY_TYPES


# ---------------------------------------------------------------------------
# Canonical PN CHECK under the "C" collation
# ---------------------------------------------------------------------------


def test_pn_check_migration_repeats_the_model_literal() -> None:
    pn_check = _load_migration(_PN_CHECK_MIGRATION_FILE)
    assert pn_check._CANONICAL_PART_NUMBER_SQL == models.CANONICAL_PART_NUMBER_SQL
    # Its downgrade restores exactly the CHECK 0002 and 0011 created.
    phase3 = _load_migration(_PHASE3_MIGRATION_FILE)
    phase10 = _load_migration(_PHASE10_MIGRATION_FILE)
    assert (
        pn_check._PHASE3_CANONICAL_PART_NUMBER_SQL
        == phase3._CANONICAL_PN
        == phase10._CANONICAL_PART_NUMBER_SQL
    )
    assert [table for table, _ in pn_check._CHECKS] == list(_PN_TABLES)


def _pn_check_texts(engine: Engine) -> dict[str, str]:
    inspector = inspect(engine)
    return {
        table: str(check["sqltext"])
        for table in _PN_TABLES
        for check in inspector.get_check_constraints(table)
        if check["name"] == f"ck_{table}_part_number_canonical"
    }


def test_pn_checks_compare_under_the_c_collation(migrated_engine: Engine) -> None:
    checks = _pn_check_texts(migrated_engine)
    assert set(checks) == set(_PN_TABLES)
    for table, sqltext in checks.items():
        assert sqltext.count('COLLATE "C"') == 2, table


def _insert_part_number(connection: Connection, part_number: str) -> None:
    connection.execute(
        sa.text("INSERT INTO part_numbers (part_number) VALUES (:pn)"), {"pn": part_number}
    )


def _stored_pn_check(connection: Connection, table: str) -> str:
    """The table's own CHECK expression as PostgreSQL stores it."""
    definition = str(
        connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = :name AND conrelid = CAST(:table AS regclass)"
            ),
            {"name": f"ck_{table}_part_number_canonical", "table": table},
        ).scalar_one()
    )
    assert definition.startswith("CHECK ")
    return definition.removeprefix("CHECK ")


_ADMITTED_PNS = ("PNɤ1", "PNƛ1", "PN-001", "PNꟋ1")
_REFUSED_PNS = ("pn1", "P N", "P\tN", "")


def test_database_pn_check_admits_libc_uppercased_and_refuses_non_canonical(
    connection: Connection,
) -> None:
    _insert_part_number(connection, _LIBC_UPPERCASED_PN)
    for refused in _REFUSED_PNS:

        def insert(part_number: str = refused) -> None:
            _insert_part_number(connection, part_number)

        _refused_by(connection, "ck_part_numbers_part_number_canonical", insert)


@pytest.mark.parametrize("table", _PN_TABLES)
def test_each_pn_table_check_evaluates_the_canonical_rule(
    connection: Connection, table: str
) -> None:
    """Evaluates each table's own stored CHECK without building parent rows."""
    expression = _stored_pn_check(connection, table)
    query = sa.text(f"SELECT {expression} FROM (VALUES (CAST(:pn AS text))) AS t(part_number)")
    for admitted in _ADMITTED_PNS:
        assert connection.execute(query, {"pn": admitted}).scalar_one() is True, admitted
    for refused in _REFUSED_PNS:
        assert connection.execute(query, {"pn": refused}).scalar_one() is False, refused


# ---------------------------------------------------------------------------
# Parity and downgrade
# ---------------------------------------------------------------------------


def test_models_metadata_matches_the_migrated_schema(migrated_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def _audit_checks(engine: Engine) -> dict[str, str]:
    return {
        str(check["name"]): str(check["sqltext"])
        for check in inspect(engine).get_check_constraints("audit_events")
    }


def test_downgrade_restores_the_phase12_boundary(admin_engine: Engine) -> None:
    name = "partflow_test_phase13_downgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _PHASE12_REVISION)
        engine = create_engine(url)
        try:
            assert "workers" not in inspect(engine).get_table_names()
            checks = _audit_checks(engine)
            entity_check = checks["ck_audit_events_entity_type"]
            event_check = checks["ck_audit_events_event_type"]
            for entity in ("WorkOrder", "WorkOrderDemand", "PartNumber"):
                assert f"'{entity}'" in entity_check
            assert "'Worker'" not in entity_check
            assert "'CREATED'" in event_check and "'UPDATED'" in event_check
            assert "'DELETED'" not in event_check
            with engine.connect() as connection:
                assert _version(connection) == _PHASE12_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            assert "workers" in inspect(engine).get_table_names()
            assert "'Worker'" in _audit_checks(engine)["ck_audit_events_entity_type"]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_downgrade_to_badge_check_revision_restores_the_s1_vocabulary(
    admin_engine: Engine,
) -> None:
    name = "partflow_test_phase13_downgrade_s2"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _BADGE_CHECK_REVISION)
        engine = create_engine(url)
        try:
            entity_check = _audit_checks(engine)["ck_audit_events_entity_type"]
            assert "'Worker'" in entity_check
            for entity in _ENVIRONMENT_ENTITIES:
                assert f"'{entity}'" not in entity_check
            with engine.connect() as connection:
                assert _version(connection) == _BADGE_CHECK_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


@pytest.fixture
def refused_database(admin_engine: Engine) -> Iterator[URL]:
    """A database at head, re-created for each refusing-downgrade case."""
    name = "partflow_test_phase13_downgrade_refused"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    command.upgrade(_alembic_config(url), "head")
    yield url
    _drop_temp_database(admin_engine, name)


def _row_counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        workers = connection.execute(sa.text("SELECT count(*) FROM workers")).scalar_one()
        audits = connection.execute(
            sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'Worker'")
        ).scalar_one()
    return int(workers), int(audits)


def test_downgrade_refuses_while_worker_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_worker(connection, "HIST1")
            _insert_audit(connection, "CREATED", "Worker")
        # The re-created Phase 4 entity CHECK refuses the Worker audit row.
        with pytest.raises(IntegrityError, match="ck_audit_events_entity_type"):
            command.downgrade(_alembic_config(refused_database), _PHASE12_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
        assert _row_counts(engine) == (1, 1)
    finally:
        engine.dispose()


def test_downgrade_refuses_while_worker_configuration_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_worker(connection, "CONFIG1")
        with pytest.raises(ProgrammingError, match="workers holds Worker configuration"):
            command.downgrade(_alembic_config(refused_database), _PHASE12_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
        assert _row_counts(engine) == (1, 0)
    finally:
        engine.dispose()


def test_badge_check_downgrade_restores_the_libc_check(refused_database: URL) -> None:
    """Without 0015 the CHECK refuses a domain-canonical badge (the 500
    0015 removes); re-upgrading admits it again."""
    config = _alembic_config(refused_database)
    command.downgrade(config, _PHASE13_REVISION)
    engine = create_engine(refused_database)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _PHASE13_REVISION
            _refused_by(
                connection,
                "ck_workers_badge_barcode_canonical",
                lambda: _insert_worker(connection, _LIBC_UPPERCASED_BADGE),
            )
            connection.rollback()
        command.upgrade(config, "head")
        with engine.begin() as connection:
            _insert_worker(connection, _LIBC_UPPERCASED_BADGE)
            assert _version(connection) == _HEAD_REVISION
    finally:
        engine.dispose()


def test_badge_check_downgrade_refuses_a_badge_only_0015_admits(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_worker(connection, _LIBC_UPPERCASED_BADGE)
        with pytest.raises(IntegrityError, match="ck_workers_badge_barcode_canonical"):
            command.downgrade(_alembic_config(refused_database), _PHASE13_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
        assert _row_counts(engine) == (1, 0)
    finally:
        engine.dispose()


def test_downgrade_refuses_while_environment_audit_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_audit(connection, "CREATED", "Area")
        with pytest.raises(IntegrityError, match="ck_audit_events_entity_type"):
            command.downgrade(_alembic_config(refused_database), _BADGE_CHECK_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            areas = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'Area'")
            ).scalar_one()
        assert areas == 1
    finally:
        engine.dispose()


def test_downgrade_to_environment_audit_revision_drops_machine(admin_engine: Engine) -> None:
    name = "partflow_test_phase13_downgrade_s2b"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _ENVIRONMENT_AUDIT_REVISION)
        engine = create_engine(url)
        try:
            entity_check = _audit_checks(engine)["ck_audit_events_entity_type"]
            assert "'Machine'" not in entity_check
            for entity in _ENVIRONMENT_ENTITIES:
                assert f"'{entity}'" in entity_check
            for table, sqltext in _pn_check_texts(engine).items():
                assert "COLLATE" not in sqltext, table
            with engine.connect() as connection:
                assert _version(connection) == _ENVIRONMENT_AUDIT_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
            assert "'Machine'" in _audit_checks(engine)["ck_audit_events_entity_type"]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_downgrade_refuses_while_machine_audit_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_audit(connection, "CREATED", "Machine")
        with pytest.raises(IntegrityError, match="ck_audit_events_entity_type"):
            command.downgrade(_alembic_config(refused_database), _ENVIRONMENT_AUDIT_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            machines = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'Machine'")
            ).scalar_one()
        assert machines == 1
    finally:
        engine.dispose()


def test_pn_check_downgrade_restores_the_libc_check(refused_database: URL) -> None:
    """Without 0018 the CHECK refuses a domain-canonical PN (the 500 0018
    removes); re-upgrading admits it again."""
    config = _alembic_config(refused_database)
    command.downgrade(config, _MACHINE_AUDIT_REVISION)
    engine = create_engine(refused_database)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _MACHINE_AUDIT_REVISION
            _refused_by(
                connection,
                "ck_part_numbers_part_number_canonical",
                lambda: _insert_part_number(connection, _LIBC_UPPERCASED_PN),
            )
            connection.rollback()
        command.upgrade(config, "head")
        with engine.begin() as connection:
            _insert_part_number(connection, _LIBC_UPPERCASED_PN)
            assert _version(connection) == _HEAD_REVISION
    finally:
        engine.dispose()


def test_pn_check_downgrade_refuses_a_pn_only_0018_admits(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_part_number(connection, _LIBC_UPPERCASED_PN)
        with pytest.raises(IntegrityError, match="ck_part_numbers_part_number_canonical"):
            command.downgrade(_alembic_config(refused_database), _MACHINE_AUDIT_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            stored = connection.execute(
                sa.text("SELECT count(*) FROM part_numbers WHERE part_number = :pn"),
                {"pn": _LIBC_UPPERCASED_PN},
            ).scalar_one()
        assert stored == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Worker identity (0019)
# ---------------------------------------------------------------------------


def _scalar_id(connection: Connection, statement: str, **params: object) -> int:
    return int(connection.execute(sa.text(statement), params).scalar_one())


def _seed_production(connection: Connection) -> dict[str, int]:
    """Raw-SQL production rows valid at 0018 and at head: a station
    Movement, a Management RECEIVED and a station allocation row."""
    department = _scalar_id(
        connection, "INSERT INTO departments (name) VALUES ('Seed') RETURNING id"
    )
    area = _scalar_id(
        connection,
        "INSERT INTO areas (department_id, name) VALUES (:department, 'Seed Area') RETURNING id",
        department=department,
    )
    operation = _scalar_id(
        connection,
        "INSERT INTO operations (area_id, code) VALUES (:area, 'OP') RETURNING id",
        area=area,
    )
    connection.execute(
        sa.text("INSERT INTO scan_stations (station_id, area_id) VALUES ('SEED-1', :area)"),
        {"area": area},
    )
    flow = _scalar_id(
        connection,
        "INSERT INTO quantity_flows (part_number, quantity, current_area_id)"
        " VALUES ('PN-SEED', 5, :area) RETURNING id",
        area=area,
    )
    movement_ids = []
    for device_event_id, station_id in (("SEED-MGMT", None), ("SEED-STATION", "SEED-1")):
        movement_ids.append(
            _scalar_id(
                connection,
                "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type,"
                " quantity, to_area_id, operation_id, station_id, occurred_at,"
                " server_received_at, device_event_id) VALUES (:flow, 'PN-SEED', 'RECEIVED',"
                " 5, :area, :operation, :station, now(), now(), :event) RETURNING id",
                flow=flow,
                area=area,
                operation=operation,
                station=station_id,
                event=device_event_id,
            )
        )
    work_order = _scalar_id(
        connection, "INSERT INTO work_orders (received_date) VALUES (current_date) RETURNING id"
    )
    demand = _scalar_id(
        connection,
        "INSERT INTO work_order_demands (work_order_id, part_number, request_type,"
        " requested_quantity) VALUES (:work_order, 'PN-SEED', 'NEW', 5) RETURNING id",
        work_order=work_order,
    )
    allocation = _scalar_id(
        connection,
        "INSERT INTO work_order_allocations (part_number, work_order_demand_id, quantity,"
        " source, station_id, allocated_at, device_event_id)"
        " VALUES ('PN-SEED', :demand, 2, 'STOCKROOM', 'SEED-1', now(), 'SEED-ALLOC')"
        " RETURNING id",
        demand=demand,
    )
    return {
        "area": area,
        "management_movement": movement_ids[0],
        "station_movement": movement_ids[1],
        "allocation": allocation,
    }


_COUNTED_TABLES = (
    "areas",
    "part_movements",
    "work_order_allocations",
    "quantity_flows",
    "audit_events",
)


def _table_counts(connection: Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
        for table in _COUNTED_TABLES
    }


def _assert_seed_without_identity(connection: Connection, seeded: dict[str, int]) -> None:
    movement_workers = connection.execute(
        sa.text("SELECT worker_id FROM part_movements WHERE id IN (:management, :station)"),
        {"management": seeded["management_movement"], "station": seeded["station_movement"]},
    ).scalars()
    assert list(movement_workers) == [None, None]
    allocated_by = connection.execute(
        sa.text("SELECT allocated_by_worker_id FROM work_order_allocations WHERE id = :id"),
        {"id": seeded["allocation"]},
    ).scalar_one()
    assert allocated_by is None


def _set_area_fixed(connection: Connection, area_id: int, badge: str) -> None:
    connection.execute(
        sa.text(
            "UPDATE areas SET worker_identification_mode = 'FIXED', fixed_worker_id ="
            " (SELECT id FROM workers WHERE badge_barcode = :badge) WHERE id = :id"
        ),
        {"badge": badge, "id": area_id},
    )


def test_upgrade_leaves_existing_rows_without_identity(admin_engine: Engine) -> None:
    """Pre-existing production rows stay NULL — never backfilled (CD4)."""
    name = "partflow_test_phase13_identity_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _PN_CHECK_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seeded = _seed_production(connection)
                before = _table_counts(connection)
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _table_counts(connection) == before
                _assert_seed_without_identity(connection, seeded)
                mode, fixed = connection.execute(
                    sa.text(
                        "SELECT worker_identification_mode, fixed_worker_id FROM areas"
                        " WHERE id = :id"
                    ),
                    {"id": seeded["area"]},
                ).one()
                assert (mode, fixed) == ("DISABLED", None)
                # A later Fixed Worker configuration never rewrites history.
                _insert_worker(connection, "SEED-W")
                _set_area_fixed(connection, seeded["area"], "SEED-W")
                _assert_seed_without_identity(connection, seeded)
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_identity_checks_refuse_non_canonical_rows(connection: Connection) -> None:
    seeded = _seed_production(connection)
    _insert_worker(connection, "CHECK-W")
    worker = _scalar_id(connection, "SELECT id FROM workers WHERE badge_barcode = 'CHECK-W'")

    def movement_without_station() -> None:
        connection.execute(
            sa.text(
                "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type,"
                " quantity, to_area_id, operation_id, worker_id, occurred_at,"
                " server_received_at, device_event_id) SELECT quantity_flow_id, part_number,"
                " 'RECEIVED', 1, to_area_id, operation_id, :worker, now(), now(), 'CK-1'"
                " FROM part_movements WHERE id = :id"
            ),
            {"worker": worker, "id": seeded["management_movement"]},
        )

    def allocation_without_station() -> None:
        connection.execute(
            sa.text(
                "INSERT INTO work_order_allocations (part_number, work_order_demand_id,"
                " quantity, source, allocated_by_worker_id, allocated_at, device_event_id)"
                " SELECT part_number, work_order_demand_id, 1, 'MANAGEMENT', :worker, now(),"
                " 'CK-2' FROM work_order_allocations WHERE id = :id"
            ),
            {"worker": worker, "id": seeded["allocation"]},
        )

    def area_update(assignments: str) -> Callable[[], None]:
        def update() -> None:
            connection.execute(
                sa.text(f"UPDATE areas SET {assignments} WHERE id = :id"),
                {"id": seeded["area"], "worker": worker},
            )

        return update

    _refused_by(connection, "ck_part_movements_worker_requires_station", movement_without_station)
    _refused_by(
        connection,
        "ck_work_order_allocations_worker_requires_station",
        allocation_without_station,
    )
    _refused_by(
        connection,
        "ck_areas_fixed_worker_shape",
        area_update("worker_identification_mode = 'FIXED'"),
    )
    _refused_by(connection, "ck_areas_fixed_worker_shape", area_update("fixed_worker_id = :worker"))
    _refused_by(
        connection,
        "ck_areas_worker_identification_mode",
        area_update("worker_identification_mode = 'MANUAL'"),
    )
    # The canonical shapes are admitted.
    area_update("worker_identification_mode = 'FIXED', fixed_worker_id = :worker")()
    area_update("worker_identification_mode = 'SCANNED', fixed_worker_id = NULL")()


def test_downgrade_to_pn_check_revision_restores_the_boundary(admin_engine: Engine) -> None:
    name = "partflow_test_phase13_downgrade_s3"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _PN_CHECK_REVISION)
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            for table, column in _IDENTITY_COLUMNS:
                names = {str(item["name"]) for item in inspector.get_columns(table)}
                assert column not in names, (table, column)
            for table, check in _IDENTITY_CHECKS:
                names = {str(item["name"]) for item in inspector.get_check_constraints(table)}
                assert check not in names, (table, check)
            for table, foreign_key in _IDENTITY_FOREIGN_KEYS:
                names = {str(item["name"]) for item in inspector.get_foreign_keys(table)}
                assert foreign_key not in names, (table, foreign_key)
            with engine.connect() as connection:
                assert _version(connection) == _PN_CHECK_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
            columns = {str(item["name"]) for item in inspect(engine).get_columns("part_movements")}
            assert "worker_id" in columns
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_downgrade_refuses_while_identity_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            seeded = _seed_production(connection)
            _insert_worker(connection, "HIST-W")
            worker = _scalar_id(connection, "SELECT id FROM workers WHERE badge_barcode = 'HIST-W'")
            connection.execute(
                sa.text(
                    "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type,"
                    " quantity, to_area_id, operation_id, station_id, worker_id, occurred_at,"
                    " server_received_at, device_event_id) SELECT quantity_flow_id,"
                    " part_number, 'RECEIVED', 1, to_area_id, operation_id, station_id,"
                    " :worker, now(), now(), 'HIST-1' FROM part_movements WHERE id = :id"
                ),
                {"worker": worker, "id": seeded["station_movement"]},
            )
        with pytest.raises(ProgrammingError, match="production records carry Worker identity"):
            command.downgrade(_alembic_config(refused_database), _PN_CHECK_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            kept = connection.execute(
                sa.text("SELECT count(*) FROM part_movements WHERE worker_id = :worker"),
                {"worker": worker},
            ).scalar_one()
        assert kept == 1
    finally:
        engine.dispose()


def test_downgrade_refuses_while_allocation_identity_exists(refused_database: URL) -> None:
    """The allocation branch of the refusal alone: every Movement carries
    no Worker and every Area is Disabled (an Area switched back after
    Fixed-mode Stockroom allocations were recorded)."""
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            seeded = _seed_production(connection)
            _insert_worker(connection, "ALLOC-W")
            worker = _scalar_id(
                connection, "SELECT id FROM workers WHERE badge_barcode = 'ALLOC-W'"
            )
            allocation = _scalar_id(
                connection,
                "INSERT INTO work_order_allocations (part_number, work_order_demand_id,"
                " quantity, source, station_id, allocated_by_worker_id, allocated_at,"
                " device_event_id) SELECT part_number, work_order_demand_id, 1, source,"
                " station_id, :worker, now(), 'HIST-ALLOC' FROM work_order_allocations"
                " WHERE id = :id RETURNING id",
                worker=worker,
                id=seeded["allocation"],
            )
            movements = connection.execute(
                sa.text("SELECT count(*) FROM part_movements WHERE worker_id IS NOT NULL")
            ).scalar_one()
            configured = connection.execute(
                sa.text("SELECT count(*) FROM areas WHERE worker_identification_mode <> 'DISABLED'")
            ).scalar_one()
        assert (movements, configured) == (0, 0)
        with pytest.raises(ProgrammingError, match="production records carry Worker identity"):
            command.downgrade(_alembic_config(refused_database), _PN_CHECK_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            kept = connection.execute(
                sa.text("SELECT allocated_by_worker_id FROM work_order_allocations WHERE id = :id"),
                {"id": allocation},
            ).scalar_one()
        assert kept == worker
    finally:
        engine.dispose()


def test_downgrade_refuses_while_area_mode_configuration_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            seeded = _seed_production(connection)
            _insert_worker(connection, "MODE-W")
            _set_area_fixed(connection, seeded["area"], "MODE-W")
        with pytest.raises(ProgrammingError, match="areas hold Worker ID mode configuration"):
            command.downgrade(_alembic_config(refused_database), _PN_CHECK_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            mode = connection.execute(
                sa.text("SELECT worker_identification_mode FROM areas WHERE id = :id"),
                {"id": seeded["area"]},
            ).scalar_one()
        assert mode == "FIXED"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Worker Sessions and the timeout policy (0020)
# ---------------------------------------------------------------------------

_SESSION_COLUMNS = {
    "id": (sa.BigInteger, False),
    "station_id": (sa.Text, False),
    "area_id": (sa.Integer, False),
    "worker_id": (sa.Integer, False),
    "started_at": (sa.DateTime, False),
    "expires_at": (sa.DateTime, False),
    "ended_at": (sa.DateTime, True),
    "end_reason": (sa.Text, True),
}
_SESSION_CHECKS = {
    "ck_worker_sessions_end_reason",
    "ck_worker_sessions_end_shape",
    "ck_worker_sessions_expiry_after_start",
    "ck_worker_sessions_end_within_window",
    "ck_worker_sessions_expired_at_expiry",
}
_SESSION_FOREIGN_KEYS = {
    "fk_worker_sessions_station_id_scan_stations": (
        ["station_id"],
        "scan_stations",
        ["station_id"],
    ),
    "fk_worker_sessions_area_id_areas": (["area_id"], "areas", ["id"]),
    "fk_worker_sessions_worker_id_workers": (["worker_id"], "workers", ["id"]),
}
_POLICY_CHECKS = {
    "ck_application_policy_singleton",
    "ck_application_policy_worker_session_timeout_range",
}
_SESSION_FK = "fk_part_movements_scan_session_worker_sessions"
# What 0020 adds outside its own tables: (table, constraint).
_SESSION_CONSTRAINTS = {
    ("areas", "ck_areas_worker_session_timeout_range"),
    ("part_movements", "ck_part_movements_session_requires_worker"),
}


def _execute(connection: Connection, statement: str, **params: object) -> None:
    connection.execute(sa.text(statement), params)


def _insert_session(
    connection: Connection,
    *,
    station_id: str,
    area_id: int,
    worker_id: int,
    started: str = "now() - interval '1 minute'",
    expires: str = "now() + interval '10 minutes'",
    ended: str = "NULL",
    reason: str | None = None,
) -> int:
    return _scalar_id(
        connection,
        "INSERT INTO worker_sessions (station_id, area_id, worker_id, started_at, expires_at,"
        f" ended_at, end_reason) VALUES (:station, :area, :worker, {started}, {expires},"
        f" {ended}, :reason) RETURNING id",
        station=station_id,
        area=area_id,
        worker=worker_id,
        reason=reason,
    )


def _seed_session(connection: Connection, badge: str) -> tuple[dict[str, int], int, int]:
    """Seeded production rows, a Worker and one open session at SEED-1."""
    seeded = _seed_production(connection)
    _insert_worker(connection, badge)
    worker = _scalar_id(connection, "SELECT id FROM workers WHERE badge_barcode = :b", b=badge)
    session_id = _insert_session(
        connection, station_id="SEED-1", area_id=seeded["area"], worker_id=worker
    )
    return seeded, worker, session_id


def test_worker_sessions_table_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(column["name"]): column for column in inspector.get_columns("worker_sessions")}
    assert set(columns) == set(_SESSION_COLUMNS)
    for name, (type_, nullable) in _SESSION_COLUMNS.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is nullable, name
    for name in ("started_at", "expires_at", "ended_at"):
        assert getattr(columns[name]["type"], "timezone", None) is True, name
        # Written by the Application from the session clock.
        assert columns[name]["default"] is None, name

    policy = {str(column["name"]): column for column in inspector.get_columns("application_policy")}
    assert set(policy) == {
        "id",
        "worker_session_timeout_minutes",
        "created_at",
        "updated_at",
        *models.BADGE_CONFIRMATION_OPTIONS,  # 0021
    }
    timeout = policy["worker_session_timeout_minutes"]
    assert isinstance(timeout["type"], sa.Integer) and timeout["nullable"] is False
    assert str(timeout["default"]) == "15"
    assert policy["id"]["default"] is None

    area = {str(column["name"]): column for column in inspector.get_columns("areas")}
    override = area["worker_session_timeout_minutes"]
    assert isinstance(override["type"], sa.Integer)
    assert (override["nullable"], override["default"]) == (True, None)

    movement = {str(column["name"]): column for column in inspector.get_columns("part_movements")}
    scan_session = movement["scan_session_id"]
    assert isinstance(scan_session["type"], sa.BigInteger)
    assert (scan_session["nullable"], scan_session["default"]) == (True, None)
    # No reader filters by session: no index on the Movement column.
    for index in inspector.get_indexes("part_movements"):
        assert "scan_session_id" not in index["column_names"], index["name"]


def test_worker_sessions_constraints_have_exact_names(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert inspector.get_pk_constraint("worker_sessions")["name"] == "pk_worker_sessions"
    checks = {str(check["name"]) for check in inspector.get_check_constraints("worker_sessions")}
    assert checks == _SESSION_CHECKS
    uniques = {
        str(unique["name"]): unique["column_names"]
        for unique in inspector.get_unique_constraints("worker_sessions")
    }
    assert uniques == {
        "uq_worker_sessions_id_worker_id_station_id": ["id", "worker_id", "station_id"]
    }
    foreign_keys = {
        str(fk["name"]): (fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
        for fk in inspector.get_foreign_keys("worker_sessions")
    }
    assert foreign_keys == _SESSION_FOREIGN_KEYS
    indexes = {
        str(index["name"]): (
            index["column_names"],
            index["unique"],
            str(index.get("dialect_options", {}).get("postgresql_where")),
        )
        for index in inspector.get_indexes("worker_sessions")
        if not index.get("duplicates_constraint")
    }
    assert indexes == {
        "uq_worker_sessions_open_station": (["station_id"], True, "(ended_at IS NULL)"),
        "ix_worker_sessions_open_worker": (["worker_id"], False, "(ended_at IS NULL)"),
    }
    incoming = {
        str(fk["name"]): (table, fk["constrained_columns"], fk["referred_columns"])
        for table in inspector.get_table_names()
        for fk in inspector.get_foreign_keys(table)
        if fk["referred_table"] == "worker_sessions"
    }
    assert incoming == {
        _SESSION_FK: (
            "part_movements",
            ["scan_session_id", "worker_id", "station_id"],
            ["id", "worker_id", "station_id"],
        )
    }
    assert inspector.get_pk_constraint("application_policy")["name"] == "pk_application_policy"
    policy_checks = {
        str(check["name"]) for check in inspector.get_check_constraints("application_policy")
    }
    assert policy_checks == _POLICY_CHECKS
    for table, name in _SESSION_CONSTRAINTS:
        assert name in {str(check["name"]) for check in inspector.get_check_constraints(table)}


def test_worker_sessions_migration_repeats_the_model_literals() -> None:
    migration = _load_migration(_WORKER_SESSIONS_MIGRATION_FILE)
    assert (migration._TIMEOUT_MIN, migration._TIMEOUT_MAX, migration._TIMEOUT_DEFAULT) == (
        models.WORKER_SESSION_TIMEOUT_MIN,
        models.WORKER_SESSION_TIMEOUT_MAX,
        models.WORKER_SESSION_TIMEOUT_DEFAULT,
    )
    assert migration._POLICY_TIMEOUT_SQL == models.POLICY_WORKER_SESSION_TIMEOUT_SQL
    assert migration._AREA_TIMEOUT_SQL == models.AREA_WORKER_SESSION_TIMEOUT_SQL
    assert migration._END_REASON_SQL == models.WORKER_SESSION_END_REASON_SQL
    assert migration._END_SHAPE_SQL == models.WORKER_SESSION_END_SHAPE_SQL
    assert migration._EXPIRY_AFTER_START_SQL == models.WORKER_SESSION_EXPIRY_AFTER_START_SQL
    assert migration._END_WITHIN_WINDOW_SQL == models.WORKER_SESSION_END_WITHIN_WINDOW_SQL
    assert migration._EXPIRED_AT_EXPIRY_SQL == models.WORKER_SESSION_EXPIRED_AT_EXPIRY_SQL
    assert migration._MOVEMENT_SESSION_WORKER_SQL == models.MOVEMENT_SESSION_WORKER_SQL
    machine_audit = _load_migration(_MACHINE_AUDIT_MIGRATION_FILE)
    assert migration._MACHINE_ENTITY_TYPES == machine_audit._MACHINE_ENTITY_TYPES
    assert set(re.findall(r"'([^']*)'", models.WORKER_SESSION_END_REASON_SQL)) == {
        reason.value for reason in WorkerSessionEndReason
    }
    assert set(re.findall(r"'([^']*)'", migration._POLICY_ENTITY_TYPES)) == {
        entity.value for entity in AuditEntityType
    }


def test_application_policy_is_one_seeded_row(connection: Connection) -> None:
    rows = connection.execute(
        sa.text("SELECT id, worker_session_timeout_minutes FROM application_policy")
    ).all()
    assert [tuple(row) for row in rows] == [(1, 15)]
    _refused_by(
        connection,
        "ck_application_policy_singleton",
        lambda: _execute(connection, "INSERT INTO application_policy (id) VALUES (2)"),
    )
    _seed_production(connection)
    for value in (0, 721):

        def set_policy(value: int = value) -> None:
            _execute(
                connection,
                "UPDATE application_policy SET worker_session_timeout_minutes = :value",
                value=value,
            )

        _refused_by(connection, "ck_application_policy_worker_session_timeout_range", set_policy)

        def set_override(value: int = value) -> None:
            _execute(
                connection, "UPDATE areas SET worker_session_timeout_minutes = :value", value=value
            )

        _refused_by(connection, "ck_areas_worker_session_timeout_range", set_override)
    for value in (1, 720):
        _execute(
            connection,
            "UPDATE application_policy SET worker_session_timeout_minutes = :value",
            value=value,
        )
        _execute(
            connection, "UPDATE areas SET worker_session_timeout_minutes = :value", value=value
        )
    _execute(connection, "UPDATE areas SET worker_session_timeout_minutes = NULL")


def _guard_refuses(connection: Connection, statement: str, message: str, **params: object) -> None:
    savepoint = connection.begin_nested()
    with pytest.raises(DBAPIError, match=message):
        connection.execute(sa.text(statement), params)
    savepoint.rollback()


def test_worker_sessions_guard_lets_only_an_open_session_end_or_slide(
    connection: Connection,
) -> None:
    seeded, worker, session_id = _seed_session(connection, "GUARD-W")
    _insert_worker(connection, "GUARD-OTHER")
    other = _scalar_id(connection, "SELECT id FROM workers WHERE badge_barcode = 'GUARD-OTHER'")
    history = "worker_sessions rows are audit history"
    frozen = "only an open session's expiry and end may change"
    _guard_refuses(connection, "DELETE FROM worker_sessions WHERE id = :id", history, id=session_id)
    # Listed first, so its BEFORE TRUNCATE trigger fires first.
    _guard_refuses(connection, "TRUNCATE worker_sessions, part_movements", history)
    for assignment in (
        "worker_id = :other",
        "area_id = area_id + 1",
        "station_id = 'OTHER'",
        "started_at = started_at - interval '1 second'",
    ):
        _guard_refuses(
            connection,
            f"UPDATE worker_sessions SET {assignment} WHERE id = :id",
            frozen,
            id=session_id,
            other=other,
        )
    # An open session may slide and end.
    _execute(
        connection,
        "UPDATE worker_sessions SET expires_at = expires_at + interval '5 minutes' WHERE id = :id",
        id=session_id,
    )
    _execute(
        connection,
        "UPDATE worker_sessions SET ended_at = now(), end_reason = 'SWITCHED' WHERE id = :id",
        id=session_id,
    )
    # A closed session is history: nothing changes any more.
    for assignment in ("expires_at = expires_at + interval '1 minute'", "end_reason = 'EXPIRED'"):
        _guard_refuses(
            connection,
            f"UPDATE worker_sessions SET {assignment} WHERE id = :id",
            frozen,
            id=session_id,
        )
    assert worker != other and seeded["area"] > 0


def test_worker_sessions_checks_refuse_invalid_rows(connection: Connection) -> None:
    seeded, worker, _ = _seed_session(connection, "CHECK-S")
    past = "now() - interval '1 hour'"
    cases: list[tuple[str, dict[str, str]]] = [
        ("ck_worker_sessions_end_reason", {"ended": "now()", "reason": "SIGNED_OUT"}),
        ("ck_worker_sessions_end_shape", {"ended": "now()"}),
        ("ck_worker_sessions_end_shape", {"reason": "SWITCHED"}),
        (
            "ck_worker_sessions_expired_at_expiry",
            {
                "started": past,
                "expires": "now() - interval '10 minutes'",
                "ended": "now() - interval '20 minutes'",
                "reason": "EXPIRED",
            },
        ),
        (
            "ck_worker_sessions_end_within_window",
            {"expires": "now()", "ended": "now() + interval '1 minute'", "reason": "SWITCHED"},
        ),
        ("ck_worker_sessions_expiry_after_start", {"started": "now()", "expires": "now()"}),
        # A second open session at the station.
        ("uq_worker_sessions_open_station", {}),
    ]
    for constraint, values in cases:

        def insert(values: dict[str, str] = values) -> None:
            _insert_session(
                connection, station_id="SEED-1", area_id=seeded["area"], worker_id=worker, **values
            )

        _refused_by(connection, constraint, insert)
    # The admitted shapes: a closed SWITCHED row and an EXPIRED row at its expiry.
    _insert_session(
        connection,
        station_id="SEED-1",
        area_id=seeded["area"],
        worker_id=worker,
        started=past,
        expires="now() - interval '10 minutes'",
        ended="now() - interval '10 minutes'",
        reason="EXPIRED",
    )


def test_movement_session_must_match_its_worker_and_station(connection: Connection) -> None:
    seeded, worker, session_id = _seed_session(connection, "FK-S")
    _insert_worker(connection, "FK-OTHER")
    other = _scalar_id(connection, "SELECT id FROM workers WHERE badge_barcode = 'FK-OTHER'")
    _execute(
        connection,
        "INSERT INTO scan_stations (station_id, area_id) VALUES ('SEED-2', :area)",
        area=seeded["area"],
    )

    def movement(event: str, worker_id: int | None, station: str) -> Callable[[], None]:
        def insert() -> None:
            _execute(
                connection,
                "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type,"
                " quantity, to_area_id, operation_id, station_id, worker_id, scan_session_id,"
                " occurred_at, server_received_at, device_event_id) SELECT quantity_flow_id,"
                " part_number, 'RECEIVED', 1, to_area_id, operation_id, :station, :worker,"
                " :session, now(), now(), :event FROM part_movements WHERE id = :id",
                station=station,
                worker=worker_id,
                session=session_id,
                event=event,
                id=seeded["station_movement"],
            )

        return insert

    _refused_by(connection, _SESSION_FK, movement("FK-1", other, "SEED-1"))
    _refused_by(connection, _SESSION_FK, movement("FK-2", worker, "SEED-2"))
    _refused_by(
        connection, "ck_part_movements_session_requires_worker", movement("FK-3", None, "SEED-1")
    )
    movement("FK-4", worker, "SEED-1")()


def test_downgrade_to_worker_identity_revision_restores_the_boundary(
    admin_engine: Engine,
) -> None:
    name = "partflow_test_phase13_downgrade_s4"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _WORKER_IDENTITY_REVISION)
        engine = create_engine(url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert {"worker_sessions", "application_policy"}.isdisjoint(tables)
            area_columns = {str(column["name"]) for column in inspector.get_columns("areas")}
            assert "worker_session_timeout_minutes" not in area_columns
            movement_columns = {
                str(column["name"]) for column in inspector.get_columns("part_movements")
            }
            assert "scan_session_id" not in movement_columns
            for table, check in _SESSION_CONSTRAINTS:
                names = {str(item["name"]) for item in inspector.get_check_constraints(table)}
                assert check not in names, (table, check)
            fks = {str(item["name"]) for item in inspector.get_foreign_keys("part_movements")}
            assert _SESSION_FK not in fks
            machine_audit = _load_migration(_MACHINE_AUDIT_MIGRATION_FILE)
            entity_check = _audit_checks(engine)["ck_audit_events_entity_type"]
            assert set(re.findall(r"'([^']*)'", entity_check)) == set(
                re.findall(r"'([^']*)'", machine_audit._MACHINE_ENTITY_TYPES)
            )
            with engine.connect() as connection:
                assert _version(connection) == _WORKER_IDENTITY_REVISION
                leftovers = connection.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_proc"
                        " WHERE proname = 'partflow_worker_sessions_guard_mutation'"
                    )
                ).scalar_one()
            assert leftovers == 0
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                policy = connection.execute(
                    sa.text("SELECT id, worker_session_timeout_minutes FROM application_policy")
                ).all()
            assert [tuple(row) for row in policy] == [(1, 15)]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def _refused_downgrade(url: URL, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        command.downgrade(_alembic_config(url), _WORKER_IDENTITY_REVISION)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
    finally:
        engine.dispose()


def test_downgrade_refuses_while_session_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _seed_session(connection, "DOWN-S")
        _refused_downgrade(refused_database, ProgrammingError, "holds Worker Session history")
        with engine.connect() as connection:
            kept = connection.execute(sa.text("SELECT count(*) FROM worker_sessions")).scalar_one()
        assert kept == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "configure",
    [
        "UPDATE areas SET worker_session_timeout_minutes = 5",
        "UPDATE application_policy SET worker_session_timeout_minutes = 30",
    ],
)
def test_downgrade_refuses_while_timeout_configuration_exists(
    refused_database: URL, configure: str
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _seed_production(connection)
            _execute(connection, configure)
        _refused_downgrade(refused_database, ProgrammingError, "timeout configuration exists")
        with engine.connect() as connection:
            policy = connection.execute(
                sa.text("SELECT worker_session_timeout_minutes FROM application_policy")
            ).scalar_one()
            override = connection.execute(
                sa.text("SELECT max(worker_session_timeout_minutes) FROM areas")
            ).scalar_one()
        assert (policy, override) in {(15, 5), (30, None)}
    finally:
        engine.dispose()


def test_downgrade_refuses_while_policy_audit_history_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_audit(connection, "UPDATED", "ApplicationPolicy")
        # The re-created 0017 entity CHECK refuses the ApplicationPolicy row.
        _refused_downgrade(refused_database, IntegrityError, "ck_audit_events_entity_type")
        with engine.connect() as connection:
            kept = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'ApplicationPolicy'")
            ).scalar_one()
        assert kept == 1
    finally:
        engine.dispose()


def _rows(connection: Connection, table: str) -> list[dict[str, object]]:
    return [
        dict(row._mapping)
        for row in connection.execute(sa.text(f"SELECT * FROM {table} ORDER BY id"))
    ]


def test_upgrade_preserves_existing_rows_without_sessions(admin_engine: Engine) -> None:
    """0019 → head keeps every row and backfills nothing (CD4)."""
    name = "partflow_test_phase13_sessions_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _WORKER_IDENTITY_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                seeded = _seed_production(connection)
                _insert_worker(connection, "UP-W")
                worker = _scalar_id(
                    connection, "SELECT id FROM workers WHERE badge_barcode = 'UP-W'"
                )
                # A station Movement that recorded a Worker (slice 3).
                _execute(
                    connection,
                    "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type,"
                    " quantity, to_area_id, operation_id, station_id, worker_id, occurred_at,"
                    " server_received_at, device_event_id) SELECT quantity_flow_id,"
                    " part_number, 'RECEIVED', 1, to_area_id, operation_id, station_id,"
                    " :worker, now(), now(), 'UP-1' FROM part_movements WHERE id = :id",
                    worker=worker,
                    id=seeded["station_movement"],
                )
                _insert_audit(connection, "CREATED", "Area")
                movements = _rows(connection, "part_movements")
                areas = _rows(connection, "areas")
                before = _table_counts(connection)
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _table_counts(connection) == before
                assert _rows(connection, "part_movements") == [
                    {**row, "scan_session_id": None} for row in movements
                ]
                assert _rows(connection, "areas") == [
                    {**row, "worker_session_timeout_minutes": None} for row in areas
                ]
                validated = {
                    str(row.conname): bool(row.convalidated)
                    for row in connection.execute(
                        sa.text(
                            "SELECT conname, convalidated FROM pg_constraint WHERE conname IN"
                            " (:fk, 'ck_part_movements_session_requires_worker',"
                            " 'ck_areas_worker_session_timeout_range')"
                        ),
                        {"fk": _SESSION_FK},
                    )
                }
                assert validated == {
                    _SESSION_FK: True,
                    "ck_part_movements_session_requires_worker": True,
                    "ck_areas_worker_session_timeout_range": True,
                }
                policy = connection.execute(
                    sa.text("SELECT id, worker_session_timeout_minutes FROM application_policy")
                ).all()
                assert [tuple(row) for row in policy] == [(1, 15)]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


# ---------------------------------------------------------------------------
# Badge-confirmation options (0021)
# ---------------------------------------------------------------------------


def _policy_row(connection: Connection) -> tuple[object, ...]:
    return tuple(
        connection.execute(
            sa.text(
                "SELECT id, worker_session_timeout_minutes, badge_confirm_done,"
                " badge_confirm_queue, badge_confirm_undo FROM application_policy"
            )
        ).one()
    )


def test_badge_confirmation_options_shape(migrated_engine: Engine) -> None:
    columns = {
        str(column["name"]): column
        for column in inspect(migrated_engine).get_columns("application_policy")
    }
    for name in models.BADGE_CONFIRMATION_OPTIONS:
        column = columns[name]
        assert isinstance(column["type"], sa.Boolean), name
        assert column["nullable"] is False, name
        assert str(column["default"]) == "true", name


def test_badge_confirmation_migration_repeats_the_model_literal() -> None:
    migration = _load_migration(_BADGE_CONFIRMATION_MIGRATION_FILE)
    assert migration._BADGE_OPTIONS == models.BADGE_CONFIRMATION_OPTIONS
    assert migration.down_revision == _WORKER_SESSIONS_REVISION


def test_badge_confirmation_options_are_seeded_on(connection: Connection) -> None:
    assert _policy_row(connection) == (1, 15, True, True, True)
    for name in models.BADGE_CONFIRMATION_OPTIONS:

        def set_null(name: str = name) -> None:
            _execute(connection, f"UPDATE application_policy SET {name} = NULL")

        savepoint = connection.begin_nested()
        with pytest.raises(IntegrityError) as raised:
            set_null()
        savepoint.rollback()
        assert f'null value in column "{name}"' in str(raised.value.orig)


def test_downgrade_to_worker_sessions_revision_drops_the_options(admin_engine: Engine) -> None:
    name = "partflow_test_phase13_downgrade_s5"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _WORKER_SESSIONS_REVISION)
        engine = create_engine(url)
        try:
            columns = {
                str(column["name"]) for column in inspect(engine).get_columns("application_policy")
            }
            assert columns.isdisjoint(models.BADGE_CONFIRMATION_OPTIONS)
            with engine.connect() as connection:
                assert _version(connection) == _WORKER_SESSIONS_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _policy_row(connection) == (1, 15, True, True, True)
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def _refused_options_downgrade(url: URL) -> None:
    with pytest.raises(ProgrammingError, match="Badge-confirmation configuration exists"):
        command.downgrade(_alembic_config(url), _WORKER_SESSIONS_REVISION)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
    finally:
        engine.dispose()


@pytest.mark.parametrize("option", models.BADGE_CONFIRMATION_OPTIONS)
def test_downgrade_refuses_while_an_option_is_off(refused_database: URL, option: str) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _execute(connection, f"UPDATE application_policy SET {option} = false")
        _refused_options_downgrade(refused_database)
        with engine.connect() as connection:
            stored = connection.execute(
                sa.text(f"SELECT {option} FROM application_policy")
            ).scalar_one()
        assert stored is False
    finally:
        engine.dispose()


def test_downgrade_refuses_while_policy_audit_records_an_option(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    snapshot = {
        "worker_session_timeout_minutes": 15,
        "badge_confirm_done": True,
        "badge_confirm_queue": True,
        "badge_confirm_undo": True,
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
                    " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy',"
                    " 'worker-sessions', now(), CAST(:before AS jsonb), CAST(:after AS jsonb))"
                ),
                {
                    "before": json.dumps({**snapshot, "worker_session_timeout_minutes": 20}),
                    "after": json.dumps(snapshot),
                },
            )
        _refused_options_downgrade(refused_database)
        with engine.connect() as connection:
            kept = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'ApplicationPolicy'")
            ).scalar_one()
            assert _policy_row(connection) == (1, 15, True, True, True)
        assert kept == 1
    finally:
        engine.dispose()


def test_upgrade_keeps_the_policy_and_its_audit_rows(admin_engine: Engine) -> None:
    """0020 → head keeps the stored timeout and the policy audit history."""
    name = "partflow_test_phase13_options_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _WORKER_SESSIONS_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _execute(
                    connection, "UPDATE application_policy SET worker_session_timeout_minutes = 30"
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO audit_events (event_type, entity_type, entity_id,"
                        " occurred_at, before_data, after_data) VALUES ('UPDATED',"
                        " 'ApplicationPolicy', 'worker-sessions', now(), CAST(:before AS jsonb),"
                        " CAST(:after AS jsonb))"
                    ),
                    {
                        "before": json.dumps({"worker_session_timeout_minutes": 15}),
                        "after": json.dumps({"worker_session_timeout_minutes": 30}),
                    },
                )
                audits = _rows(connection, "audit_events")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _policy_row(connection) == (1, 30, True, True, True)
                assert _rows(connection, "audit_events") == audits
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)
