"""Integration tests for the Phase 13 migrations.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0014_phase13_workers`, `0015_phase13_badge_check`,
`0016_phase13_environment_audit`, `0017_phase13_machine_audit` and
`0018_phase13_pn_check_collation` add (IMPLEMENTATION_ROADMAP Phase 13;
PROJECT_PROFILE §7, §8.13, §10, §28; owner decisions OD-3, OD-10,
S2-F6). Later Phase 13 slices extend this module:

- exact head boundary: `0018_phase13_pn_check_collation` is the single
  head;
- the `workers` table shape and its exact constraint names; no FK from
  or to it;
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
  while a PN only 0018 admits exists.

Phase 13 is the current head, so this module carries the head-level
coverage. When a later phase adds its migration, pin this module to the
last Phase 13 revision and move the head-level coverage into that
phase's schema test.
"""

import datetime
import importlib.util
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
from sqlalchemy.exc import IntegrityError, ProgrammingError

from alembic import command
from app.domain.enums import AuditEntityType
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PHASE12_REVISION = "0013_phase12_priority"
_PHASE13_REVISION = "0014_phase13_workers"
_BADGE_CHECK_REVISION = "0015_phase13_badge_check"
_ENVIRONMENT_AUDIT_REVISION = "0016_phase13_environment_audit"
_MACHINE_AUDIT_REVISION = "0017_phase13_machine_audit"
_HEAD_REVISION = "0018_phase13_pn_check_collation"
_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "20261004_0014_phase13_workers.py"
_BADGE_CHECK_MIGRATION_FILE = _VERSIONS_DIR / "20261004_0015_phase13_badge_check.py"
_ENVIRONMENT_AUDIT_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0016_phase13_environment_audit.py"
_MACHINE_AUDIT_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0017_phase13_machine_audit.py"
_PN_CHECK_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0018_phase13_pn_check_collation.py"
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


def test_no_foreign_key_points_from_or_to_workers(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert inspector.get_foreign_keys("workers") == []
    for table in inspector.get_table_names():
        for foreign_key in inspector.get_foreign_keys(table):
            assert foreign_key["referred_table"] != "workers", table


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
    for entity in (*_ENVIRONMENT_ENTITIES, "Machine"):
        _insert_audit(connection, "CREATED", entity)
    for refused in ("MachineLifecycleEvent", "User", "ApplicationPolicy"):

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
