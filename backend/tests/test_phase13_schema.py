"""Integration tests for the Phase 13 migrations.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0014_phase13_workers`, `0015_phase13_badge_check`,
`0016_phase13_environment_audit`, `0017_phase13_machine_audit`,
`0018_phase13_pn_check_collation`, `0019_phase13_worker_identity`,
`0020_phase13_worker_sessions`, `0021_phase13_badge_confirmation`,
`0022_phase13_undo_reason_policy`, `0023_phase13_part_number_master`,
`0024_phase13_planned_routes`, `0025_phase13_display_settings` and
`0026_phase13_station_theme` add (IMPLEMENTATION_ROADMAP Phase 13;
PROJECT_PROFILE §7, §8.1, §8.4, §8.8–§8.11, §8.12, §8.13, §10, §16,
§19, §21, §28; GUI_DESIGN §2.1; owner decisions OD-2, OD-3, OD-5, OD-6,
OD-10, OD-11, OD-13, S2-F6). Later Phase 13 slices extend this module:

- exact head boundary: `0026_phase13_station_theme` is the single
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
  option is off or a policy audit row records an option;
- the Undo reason policy (0022): `application_policy.undo_reason_required`
  (boolean, NOT NULL, default `false`), seeded off; the upgrade keeps the
  existing policy, audit and Movement rows; the downgrade restores the
  0021 boundary and refuses while the policy is on or a
  `correction-permissions` audit row exists, never because of a
  `REVERSED` row carrying a reason;
- Part Number details (0023): the six nullable `part_numbers` columns
  (`name`, `current_revision`, `erp_id`, `image`, `image_type`,
  `image_updated_at`) with no index, the three image CHECKs with their
  exact names and literals (all-or-none, PNG/JPEG/WebP, 1 byte to
  2 MiB), and no FK referencing `part_numbers` at all; the upgrade keeps
  every master and audit row; the downgrade restores the 0022 boundary
  and refuses while any master carries a detail or an image, never
  because of `PartNumber` audit rows;
- Planned Routes (0024): `route_steps.preferred_machine_id` (nullable,
  FK to `machines`) and `assigned_route_steps.preferred_machine_id`
  (nullable, deliberately no FK), the index
  `ix_assigned_routes_source_route_template_id` and `RouteTemplate` in
  the audit entity CHECK — which the hand-written model CHECK names too;
  the upgrade keeps every template, step, snapshot and audit row (the new
  columns NULL); the downgrade restores the 0023 boundary and refuses
  while a preferred Machine or a `RouteTemplate` audit row exists;
- display settings (0025): `departments.board_seconds_per_row` /
  `board_min_page_seconds` (integer, NOT NULL, defaults 3 / 6) and the
  Due Soon policy columns `application_policy.due_soon_min_days` /
  `due_soon_lead_time_percent` / `due_soon_max_days` (defaults 2 / 15 /
  7) with their exact CHECK names and literals; the upgrade gives every
  existing Department and the singleton the defaults and keeps the
  earlier policy values and audit rows; the downgrade restores the 0024
  boundary and refuses while a value differs from its default, a
  `due-soon` audit row exists or a Department audit row changed a
  rotation setting;
- the station theme preference (0026): `scan_stations.theme_preference`
  (text, nullable, no default, no index) with the CHECK
  `ck_scan_stations_theme_preference` admitting exactly `DARK` and
  `LIGHT` (NULL passes), repeated verbatim from the model; the upgrade
  leaves every existing station without a preference (never
  backfilled); the downgrade restores the 0025 boundary and refuses
  while any station holds a saved preference.

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
from typing import cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from alembic import command
from app.application import policies
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
_BADGE_CONFIRMATION_REVISION = "0021_phase13_badge_confirmation"
_UNDO_REASON_POLICY_REVISION = "0022_phase13_undo_reason_policy"
_PART_NUMBER_MASTER_REVISION = "0023_phase13_part_number_master"
_PLANNED_ROUTES_REVISION = "0024_phase13_planned_routes"
_DISPLAY_SETTINGS_REVISION = "0025_phase13_display_settings"
_HEAD_REVISION = "0026_phase13_station_theme"
_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "20261004_0014_phase13_workers.py"
_BADGE_CHECK_MIGRATION_FILE = _VERSIONS_DIR / "20261004_0015_phase13_badge_check.py"
_ENVIRONMENT_AUDIT_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0016_phase13_environment_audit.py"
_MACHINE_AUDIT_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0017_phase13_machine_audit.py"
_PN_CHECK_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0018_phase13_pn_check_collation.py"
_WORKER_IDENTITY_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0019_phase13_worker_identity.py"
_WORKER_SESSIONS_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0020_phase13_worker_sessions.py"
_BADGE_CONFIRMATION_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0021_phase13_badge_confirmation.py"
_UNDO_REASON_POLICY_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0022_phase13_undo_reason_policy.py"
_PART_NUMBER_MASTER_MIGRATION_FILE = _VERSIONS_DIR / "20261005_0023_phase13_part_number_master.py"
_PLANNED_ROUTES_MIGRATION_FILE = _VERSIONS_DIR / "20261006_0024_phase13_planned_routes.py"
_DISPLAY_SETTINGS_MIGRATION_FILE = _VERSIONS_DIR / "20261006_0025_phase13_display_settings.py"
_STATION_THEME_MIGRATION_FILE = _VERSIONS_DIR / "20261006_0026_phase13_station_theme.py"
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
    # 0025
    "ck_application_policy_due_soon_min_days_range",
    "ck_application_policy_due_soon_max_days_range",
    "ck_application_policy_due_soon_lead_time_percent_range",
    "ck_application_policy_due_soon_window_order",
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
        "undo_reason_required",  # 0022
        "due_soon_min_days",  # 0025
        "due_soon_lead_time_percent",  # 0025
        "due_soon_max_days",  # 0025
    }
    undo_reason = policy["undo_reason_required"]
    assert isinstance(undo_reason["type"], sa.Boolean) and undo_reason["nullable"] is False
    assert str(undo_reason["default"]) == "false"
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
    # 0024 (slice 8) appends RouteTemplate after this literal.
    assert set(re.findall(r"'([^']*)'", migration._POLICY_ENTITY_TYPES)) == {
        entity.value for entity in AuditEntityType
    } - {AuditEntityType.ROUTE_TEMPLATE.value}


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


# ---------------------------------------------------------------------------
# Undo reason policy (0022)
# ---------------------------------------------------------------------------


def _undo_reason_required(connection: Connection) -> object:
    return connection.execute(
        sa.text("SELECT undo_reason_required FROM application_policy")
    ).scalar_one()


def _insert_policy_audit(connection: Connection, entity_id: str, snapshot: object) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy', :entity_id,"
            " now(), CAST(:before AS jsonb), CAST(:after AS jsonb))"
        ),
        {"entity_id": entity_id, "before": json.dumps(snapshot), "after": json.dumps(snapshot)},
    )


def _insert_reversal_with_reason(connection: Connection, seeded: dict[str, int]) -> None:
    """A station REVERSED row carrying a reason, compensating the seeded station RECEIVED."""
    _execute(
        connection,
        "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type, quantity,"
        " from_area_id, to_area_id, operation_id, station_id, reverses_movement_id, reason,"
        " occurred_at, server_received_at, device_event_id) SELECT quantity_flow_id,"
        " part_number, 'REVERSED', quantity, to_area_id, to_area_id, operation_id, station_id,"
        " id, 'wrong PN', now(), now(), 'SEED-UNDO' FROM part_movements WHERE id = :id",
        id=seeded["station_movement"],
    )


def test_undo_reason_policy_shape(migrated_engine: Engine) -> None:
    columns = {
        str(column["name"]): column
        for column in inspect(migrated_engine).get_columns("application_policy")
    }
    column = columns["undo_reason_required"]
    assert isinstance(column["type"], sa.Boolean)
    assert column["nullable"] is False
    assert str(column["default"]) == "false"


def test_undo_reason_policy_migration_repeats_the_application_literals() -> None:
    migration = _load_migration(_UNDO_REASON_POLICY_MIGRATION_FILE)
    assert migration.down_revision == _BADGE_CONFIRMATION_REVISION
    assert migration._SECTION == policies.CORRECTION_PERMISSIONS_SECTION
    assert models.ApplicationPolicy.undo_reason_required.key == migration._COLUMN
    source = _UNDO_REASON_POLICY_MIGRATION_FILE.read_text(encoding="utf-8")
    assert f"entity_id = '{migration._SECTION}'" in source
    assert f"WHERE {migration._COLUMN})" in source


def test_undo_reason_policy_is_seeded_off(connection: Connection) -> None:
    assert _undo_reason_required(connection) is False
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        _execute(connection, "UPDATE application_policy SET undo_reason_required = NULL")
    savepoint.rollback()
    assert 'null value in column "undo_reason_required"' in str(raised.value.orig)
    # A REVERSED row admits a reason, and none (F1: the CHECK predates 0022).
    seeded = _seed_production(connection)
    _insert_reversal_with_reason(connection, seeded)


def test_downgrade_to_badge_confirmation_revision_drops_the_column(admin_engine: Engine) -> None:
    name = "partflow_test_phase13_downgrade_s6"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        command.downgrade(config, _BADGE_CONFIRMATION_REVISION)
        engine = create_engine(url)
        try:
            columns = {
                str(column["name"]) for column in inspect(engine).get_columns("application_policy")
            }
            assert "undo_reason_required" not in columns
            with engine.connect() as connection:
                assert _version(connection) == _BADGE_CONFIRMATION_REVISION
        finally:
            engine.dispose()
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _undo_reason_required(connection) is False
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def _refused_undo_reason_downgrade(url: URL) -> None:
    with pytest.raises(ProgrammingError, match="Undo reason policy configuration exists"):
        command.downgrade(_alembic_config(url), _BADGE_CONFIRMATION_REVISION)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
    finally:
        engine.dispose()


def test_downgrade_refuses_while_the_undo_reason_policy_is_on(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _execute(connection, "UPDATE application_policy SET undo_reason_required = true")
        _refused_undo_reason_downgrade(refused_database)
        with engine.connect() as connection:
            assert _undo_reason_required(connection) is True
    finally:
        engine.dispose()


def test_downgrade_refuses_while_correction_permissions_audit_exists(
    refused_database: URL,
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_policy_audit(
                connection, "correction-permissions", {"undo_reason_required": False}
            )
        _refused_undo_reason_downgrade(refused_database)
        with engine.connect() as connection:
            kept = connection.execute(
                sa.text(
                    "SELECT count(*) FROM audit_events WHERE entity_id = 'correction-permissions'"
                )
            ).scalar_one()
            assert _undo_reason_required(connection) is False
        assert kept == 1
    finally:
        engine.dispose()


def test_a_reversal_reason_alone_never_blocks_the_downgrade(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_reversal_with_reason(connection, _seed_production(connection))
        command.downgrade(_alembic_config(refused_database), _BADGE_CONFIRMATION_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _BADGE_CONFIRMATION_REVISION
            reasons = connection.execute(
                sa.text("SELECT reason FROM part_movements WHERE movement_type = 'REVERSED'")
            ).all()
        assert [tuple(row) for row in reasons] == [("wrong PN",)]
    finally:
        engine.dispose()


def test_upgrade_keeps_the_policy_audit_and_reversal_rows(admin_engine: Engine) -> None:
    """0021 → head keeps the stored policy, its audit rows and the Movements."""
    name = "partflow_test_phase13_undo_reason_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _BADGE_CONFIRMATION_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _execute(
                    connection,
                    "UPDATE application_policy SET worker_session_timeout_minutes = 30,"
                    " badge_confirm_undo = false",
                )
                _insert_policy_audit(connection, "worker-sessions", {"badge_confirm_undo": False})
                _insert_reversal_with_reason(connection, _seed_production(connection))
                audits = _rows(connection, "audit_events")
                movements = _rows(connection, "part_movements")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _policy_row(connection) == (1, 30, True, True, False)
                assert _undo_reason_required(connection) is False
                assert _rows(connection, "audit_events") == audits
                assert _rows(connection, "part_movements") == movements
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


# ---------------------------------------------------------------------------
# Part Number details and image (0023)
# ---------------------------------------------------------------------------

_PART_NUMBER_DETAIL_COLUMNS = {
    "name": sa.Text,
    "current_revision": sa.Text,
    "erp_id": sa.Text,
    "image": sa.LargeBinary,
    "image_type": sa.Text,
    "image_updated_at": sa.DateTime,
}
_PART_NUMBER_IMAGE_CHECKS = {
    "ck_part_numbers_image_shape": "PART_NUMBER_IMAGE_SHAPE_SQL",
    "ck_part_numbers_image_type": "PART_NUMBER_IMAGE_TYPE_SQL",
    "ck_part_numbers_image_size": "PART_NUMBER_IMAGE_SIZE_SQL",
}


def _insert_master(connection: Connection, part_number: str, **details: object) -> None:
    columns = ["part_number", *details]
    values = [":part_number", *(f":{column}" for column in details)]
    connection.execute(
        sa.text(f"INSERT INTO part_numbers ({', '.join(columns)}) VALUES ({', '.join(values)})"),
        {"part_number": part_number, **details},
    )


def test_part_number_detail_columns_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(column["name"]): column for column in inspector.get_columns("part_numbers")}
    assert set(columns) == {"part_number", "created_at", "updated_at"} | set(
        _PART_NUMBER_DETAIL_COLUMNS
    )
    for name, column_type in _PART_NUMBER_DETAIL_COLUMNS.items():
        column = columns[name]
        assert isinstance(column["type"], column_type), name
        assert column["nullable"] is True, name
        assert column["default"] is None, name
    assert cast(sa.DateTime, columns["image_updated_at"]["type"]).timezone is True
    # No index and nothing unique beyond the natural key: the details
    # are never looked up.
    assert inspector.get_indexes("part_numbers") == []
    assert inspector.get_unique_constraints("part_numbers") == []


def test_part_number_image_checks_have_exact_names_and_literals(migrated_engine: Engine) -> None:
    checks = {
        str(check["name"])
        for check in inspect(migrated_engine).get_check_constraints("part_numbers")
    }
    assert checks == {"ck_part_numbers_part_number_canonical", *_PART_NUMBER_IMAGE_CHECKS}
    migration = _load_migration(_PART_NUMBER_MASTER_MIGRATION_FILE)
    assert migration.down_revision == _UNDO_REASON_POLICY_REVISION
    for name, constant in _PART_NUMBER_IMAGE_CHECKS.items():
        assert getattr(migration, f"_{constant}") == getattr(models, constant), name
    # The PN image repeats the S1 avatar rules exactly, column for column.
    assert models.PART_NUMBER_IMAGE_TYPE_SQL.replace("image_type", "avatar_image_type") == (
        "avatar_image_type IN ('image/png', 'image/jpeg', 'image/webp')"
    )
    assert "BETWEEN 1 AND 2097152" in models.PART_NUMBER_IMAGE_SIZE_SQL


def test_no_foreign_key_references_part_numbers(migrated_engine: Engine) -> None:
    """The structural proof that deleting a master can never cascade
    into, or be blocked by, production data."""
    with migrated_engine.connect() as connection:
        referencing = connection.execute(
            sa.text(
                "SELECT conname FROM pg_constraint"
                " WHERE contype = 'f' AND confrelid = 'part_numbers'::regclass"
            )
        ).all()
    assert referencing == []


def test_part_number_image_checks_refuse_invalid_rows(connection: Connection) -> None:
    now = datetime.datetime.now(datetime.UTC)
    _refused_by(
        connection,
        "ck_part_numbers_image_shape",
        lambda: _insert_master(connection, "PN-PARTIAL", image=b"\x89PNG"),
    )
    _refused_by(
        connection,
        "ck_part_numbers_image_shape",
        lambda: _insert_master(connection, "PN-NOSTAMP", image=b"\x89PNG", image_type="image/png"),
    )
    _refused_by(
        connection,
        "ck_part_numbers_image_type",
        lambda: _insert_master(
            connection,
            "PN-GIF",
            image=b"GIF89a",
            image_type="image/gif",
            image_updated_at=now,
        ),
    )
    _refused_by(
        connection,
        "ck_part_numbers_image_size",
        lambda: _insert_master(
            connection, "PN-EMPTY", image=b"", image_type="image/png", image_updated_at=now
        ),
    )

    def oversized() -> None:
        connection.execute(
            sa.text(
                "INSERT INTO part_numbers (part_number, image, image_type, image_updated_at)"
                " VALUES ('PN-BIG', decode(repeat('00', 2097153), 'hex'), 'image/png', now())"
            )
        )

    _refused_by(connection, "ck_part_numbers_image_size", oversized)

    _insert_master(connection, "PN-PLAIN")
    _insert_master(
        connection,
        "PN-FULL",
        name="Bracket",
        current_revision="C",
        erp_id="ERP-1",
        image=b"\x00" * 2097152,
        image_type="image/webp",
        image_updated_at=now,
    )


def _part_number_rows(connection: Connection) -> list[dict[str, object]]:
    return [
        dict(row._mapping)
        for row in connection.execute(sa.text("SELECT * FROM part_numbers ORDER BY part_number"))
    ]


def test_upgrade_keeps_existing_masters_and_their_audit_rows(admin_engine: Engine) -> None:
    """0022 → head keeps every master (the six new columns NULL) and
    every audit row."""
    name = "partflow_test_phase13_pn_master_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _UNDO_REASON_POLICY_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                for part_number in ("PN-A", "PN-B"):
                    _insert_master(connection, part_number)
                    _execute(
                        connection,
                        "INSERT INTO audit_events (event_type, entity_type, entity_id,"
                        " occurred_at, after_data) VALUES ('CREATED', 'PartNumber', :pn, now(),"
                        " CAST(:after AS jsonb))",
                        pn=part_number,
                        after=json.dumps({"part_number": part_number}),
                    )
                masters = _part_number_rows(connection)
                audits = _rows(connection, "audit_events")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _part_number_rows(connection) == [
                    {**row, **dict.fromkeys(_PART_NUMBER_DETAIL_COLUMNS)} for row in masters
                ]
                assert _rows(connection, "audit_events") == audits
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_downgrade_to_undo_reason_revision_drops_the_details(admin_engine: Engine) -> None:
    """Masters without details (and PartNumber UPDATED / DELETED audit
    rows) never block the downgrade; the re-upgrade restores the head."""
    name = "partflow_test_phase13_downgrade_s7"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _insert_master(connection, "PN-PLAIN")
                for event_type in ("UPDATED", "DELETED"):
                    _execute(
                        connection,
                        "INSERT INTO audit_events (event_type, entity_type, entity_id,"
                        " occurred_at) VALUES (:event_type, 'PartNumber', 'PN-GONE', now())",
                        event_type=event_type,
                    )
            command.downgrade(config, _UNDO_REASON_POLICY_REVISION)
            inspector = inspect(engine)
            columns = {str(column["name"]) for column in inspector.get_columns("part_numbers")}
            assert columns == {"part_number", "created_at", "updated_at"}
            checks = {
                str(check["name"]) for check in inspector.get_check_constraints("part_numbers")
            }
            assert checks == {"ck_part_numbers_part_number_canonical"}
            with engine.connect() as connection:
                assert _version(connection) == _UNDO_REASON_POLICY_REVISION
                kept = _part_number_rows(connection)
            assert [row["part_number"] for row in kept] == ["PN-PLAIN"]
            with engine.connect() as connection:
                deleted = connection.execute(
                    sa.text("SELECT count(*) FROM audit_events WHERE entity_id = 'PN-GONE'")
                ).scalar_one()
            assert deleted == 2
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


@pytest.mark.parametrize(
    "details",
    [
        {"name": "Bracket"},
        {"current_revision": "C"},
        {"erp_id": "ERP-1"},
        {
            "image": b"\x89PNG",
            "image_type": "image/png",
            "image_updated_at": datetime.datetime(2026, 10, 5, tzinfo=datetime.UTC),
        },
    ],
    ids=["name", "revision", "erp-id", "image"],
)
def test_downgrade_refuses_while_part_number_details_exist(
    refused_database: URL, details: dict[str, object]
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_master(connection, "PN-KEPT", **details)
            stored = _part_number_rows(connection)
        with pytest.raises(ProgrammingError, match="Part Number details exist"):
            command.downgrade(_alembic_config(refused_database), _UNDO_REASON_POLICY_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            assert _part_number_rows(connection) == stored
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Planned Routes (0024)
# ---------------------------------------------------------------------------

_PREFERRED_MACHINE_FK = "fk_route_steps_preferred_machine_id_machines"
_SOURCE_TEMPLATE_INDEX = "ix_assigned_routes_source_route_template_id"
_ROUTE_TABLES = ("route_templates", "route_steps", "assigned_routes", "assigned_route_steps")


def test_preferred_machine_columns_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for table in ("route_steps", "assigned_route_steps"):
        columns = {str(column["name"]): column for column in inspector.get_columns(table)}
        column = columns["preferred_machine_id"]
        assert isinstance(column["type"], sa.Integer)
        assert column["nullable"] is True
        assert column["default"] is None
    step_fks = {
        str(fk["name"]): fk
        for fk in inspector.get_foreign_keys("route_steps")
        if fk["constrained_columns"] == ["preferred_machine_id"]
    }
    assert set(step_fks) == {_PREFERRED_MACHINE_FK}
    assert step_fks[_PREFERRED_MACHINE_FK]["referred_table"] == "machines"
    assert step_fks[_PREFERRED_MACHINE_FK]["referred_columns"] == ["id"]
    # The snapshot copy carries no FK: production commands never lock a
    # Machine row through it (S8-OD20).
    assert not [
        fk
        for fk in inspector.get_foreign_keys("assigned_route_steps")
        if "preferred_machine_id" in fk["constrained_columns"]
    ]


def test_source_template_index(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    indexes = {str(index["name"]): index for index in inspector.get_indexes("assigned_routes")}
    assert indexes[_SOURCE_TEMPLATE_INDEX]["column_names"] == ["source_route_template_id"]
    assert indexes[_SOURCE_TEMPLATE_INDEX]["unique"] is False
    # No reader filters by the preferred Machine: no index on either column.
    for table in ("route_steps", "assigned_route_steps"):
        for index in inspector.get_indexes(table):
            assert "preferred_machine_id" not in index["column_names"]


def test_model_entity_check_names_every_enum_member() -> None:
    # The model CHECK is a hand-written list and compare_metadata does
    # not compare CHECK text: assert it names exactly the enum.
    checks = [
        constraint
        for constraint in cast(sa.Table, models.AuditEvent.__table__).constraints
        if isinstance(constraint, sa.CheckConstraint)
        and constraint.name == "ck_audit_events_entity_type"
    ]
    assert len(checks) == 1
    sqltext = str(checks[0].sqltext)
    assert set(re.findall(r"'([^']*)'", sqltext)) == {e.value for e in AuditEntityType}


def test_planned_routes_migration_restores_the_0020_literal() -> None:
    planned_routes = _load_migration(_PLANNED_ROUTES_MIGRATION_FILE)
    worker_sessions = _load_migration(_WORKER_SESSIONS_MIGRATION_FILE)
    assert planned_routes._PREVIOUS_ENTITY_TYPES == worker_sessions._POLICY_ENTITY_TYPES
    widened = worker_sessions._POLICY_ENTITY_TYPES[:-1] + ", 'RouteTemplate')"
    assert widened == planned_routes._ROUTE_TEMPLATE_ENTITY_TYPES
    assert set(re.findall(r"'([^']*)'", planned_routes._ROUTE_TEMPLATE_ENTITY_TYPES)) == {
        entity.value for entity in AuditEntityType
    }


def test_audit_admits_the_route_template_entity(connection: Connection) -> None:
    _insert_audit(connection, "CREATED", "RouteTemplate")
    _insert_audit(connection, "DELETED", "RouteTemplate")
    _refused_by(
        connection,
        "ck_audit_events_entity_type",
        lambda: _insert_audit(connection, "CREATED", "AssignedRoute"),
    )


def _seed_routes(connection: Connection) -> dict[str, int]:
    """Raw-SQL rows valid at 0023 and at head: a template with a step
    without an Operation, its snapshot, a Machine and an Area audit row."""
    department = _scalar_id(
        connection, "INSERT INTO departments (name) VALUES ('Route Seed') RETURNING id"
    )
    area = _scalar_id(
        connection,
        "INSERT INTO areas (department_id, name) VALUES (:department, 'Route Area') RETURNING id",
        department=department,
    )
    operation = _scalar_id(
        connection,
        "INSERT INTO operations (area_id, code) VALUES (:area, 'RT-OP') RETURNING id",
        area=area,
    )
    machine = _scalar_id(
        connection,
        "INSERT INTO machines (area_id, name, asset_tag) VALUES (:area, 'Lathe', 'RT-1')"
        " RETURNING id",
        area=area,
    )
    template = _scalar_id(
        connection,
        "INSERT INTO route_templates (name, description) VALUES ('Bracket', 'Two steps')"
        " RETURNING id",
    )
    _execute(
        connection,
        "INSERT INTO route_steps (route_template_id, sequence, area_id, operation_id,"
        " expected_duration, instructions) VALUES (:template, 10, :area, :operation,"
        " interval '4 hours', 'Deburr'), (:template, 20, :area, NULL, NULL, NULL)",
        template=template,
        area=area,
        operation=operation,
    )
    assigned = _scalar_id(
        connection,
        "INSERT INTO assigned_routes (source_route_template_id) VALUES (:template) RETURNING id",
        template=template,
    )
    _execute(
        connection,
        "INSERT INTO assigned_route_steps (assigned_route_id, sequence, area_id, operation_id,"
        " expected_duration, instructions) VALUES (:assigned, 10, :area, :operation,"
        " interval '4 hours', 'Deburr'), (:assigned, 20, :area, NULL, NULL, NULL)",
        assigned=assigned,
        area=area,
        operation=operation,
    )
    _insert_audit(connection, "CREATED", "Area")
    return {"template": template, "machine": machine, "assigned": assigned}


def _route_rows(connection: Connection) -> dict[str, list[dict[str, object]]]:
    return {table: _rows(connection, table) for table in (*_ROUTE_TABLES, "audit_events")}


def test_upgrade_keeps_routes_snapshots_and_audit_rows(admin_engine: Engine) -> None:
    """0023 → head keeps every template, step, snapshot and audit row;
    both new columns are NULL (no backfill)."""
    name = "partflow_test_phase13_routes_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _PART_NUMBER_MASTER_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _seed_routes(connection)
                before = _route_rows(connection)
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                after = _route_rows(connection)
            for table in ("route_steps", "assigned_route_steps"):
                assert after[table] == [
                    {**row, "preferred_machine_id": None} for row in before[table]
                ]
            for table in ("route_templates", "assigned_routes", "audit_events"):
                assert after[table] == before[table]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_downgrade_to_part_number_master_revision_restores_the_boundary(
    admin_engine: Engine,
) -> None:
    """Routes without preferred Machines (and other entities' audit
    rows) never block the downgrade; the re-upgrade restores the head."""
    name = "partflow_test_phase13_downgrade_s8"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _seed_routes(connection)
            command.downgrade(config, _PART_NUMBER_MASTER_REVISION)
            inspector = inspect(engine)
            for table in ("route_steps", "assigned_route_steps"):
                columns = {str(column["name"]) for column in inspector.get_columns(table)}
                assert "preferred_machine_id" not in columns
            assert _SOURCE_TEMPLATE_INDEX not in {
                str(index["name"]) for index in inspector.get_indexes("assigned_routes")
            }
            assert "'RouteTemplate'" not in _audit_checks(engine)["ck_audit_events_entity_type"]
            with engine.connect() as connection:
                assert _version(connection) == _PART_NUMBER_MASTER_REVISION
                assert len(_rows(connection, "route_steps")) == 2
                assert len(_rows(connection, "assigned_route_steps")) == 2
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE route_steps SET preferred_machine_id = :machine WHERE sequence = 10",
        "UPDATE assigned_route_steps SET preferred_machine_id = :machine WHERE sequence = 10",
    ],
    ids=["route-step", "assigned-route-step"],
)
def test_downgrade_refuses_while_a_preferred_machine_exists(
    refused_database: URL, statement: str
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            seeded = _seed_routes(connection)
            _execute(connection, statement, machine=seeded["machine"])
            stored = _route_rows(connection)
        with pytest.raises(ProgrammingError, match="Preferred Machines are recorded"):
            command.downgrade(_alembic_config(refused_database), _PART_NUMBER_MASTER_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            assert _route_rows(connection) == stored
    finally:
        engine.dispose()


def test_downgrade_refuses_while_route_template_audit_history_exists(
    refused_database: URL,
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_audit(connection, "CREATED", "RouteTemplate")
        # The re-created 0020 entity CHECK refuses the RouteTemplate row.
        with pytest.raises(IntegrityError, match="ck_audit_events_entity_type"):
            command.downgrade(_alembic_config(refused_database), _PART_NUMBER_MASTER_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            kept = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'RouteTemplate'")
            ).scalar_one()
        assert kept == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Department display settings and the Due Soon policy (0025)
# ---------------------------------------------------------------------------

# (table, column) → server default.
_DISPLAY_SETTINGS_COLUMNS = {
    ("departments", "board_seconds_per_row"): "3",
    ("departments", "board_min_page_seconds"): "6",
    ("application_policy", "due_soon_min_days"): "2",
    ("application_policy", "due_soon_lead_time_percent"): "15",
    ("application_policy", "due_soon_max_days"): "7",
}
# Constraint name → (table, model constant).
_DISPLAY_SETTINGS_CHECKS = {
    "ck_departments_board_seconds_per_row_range": (
        "departments",
        models.DEPARTMENT_BOARD_SECONDS_PER_ROW_SQL,
    ),
    "ck_departments_board_min_page_seconds_range": (
        "departments",
        models.DEPARTMENT_BOARD_MIN_PAGE_SECONDS_SQL,
    ),
    "ck_application_policy_due_soon_min_days_range": (
        "application_policy",
        models.POLICY_DUE_SOON_MIN_DAYS_SQL,
    ),
    "ck_application_policy_due_soon_max_days_range": (
        "application_policy",
        models.POLICY_DUE_SOON_MAX_DAYS_SQL,
    ),
    "ck_application_policy_due_soon_lead_time_percent_range": (
        "application_policy",
        models.POLICY_DUE_SOON_PERCENT_SQL,
    ),
    "ck_application_policy_due_soon_window_order": (
        "application_policy",
        models.POLICY_DUE_SOON_ORDER_SQL,
    ),
}
_DEFAULT_DEPARTMENT_SNAPSHOT = {
    "name": "D",
    "is_active": True,
    "board_seconds_per_row": 3,
    "board_min_page_seconds": 6,
}


def _due_soon_row(connection: Connection) -> tuple[object, ...]:
    return tuple(
        connection.execute(
            sa.text(
                "SELECT due_soon_min_days, due_soon_lead_time_percent, due_soon_max_days"
                " FROM application_policy"
            )
        ).one()
    )


def _department_settings(connection: Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            sa.text(
                "SELECT name, board_seconds_per_row, board_min_page_seconds FROM departments"
                " ORDER BY id"
            )
        )
    ]


def _insert_department_audit(
    connection: Connection, event_type: str, before: object, after: object
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data) VALUES (:event_type, 'Department', '1', now(),"
            " CAST(:before AS jsonb), CAST(:after AS jsonb))"
        ),
        {"event_type": event_type, "before": json.dumps(before), "after": json.dumps(after)},
    )


def test_display_settings_columns_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for (table, name), default in _DISPLAY_SETTINGS_COLUMNS.items():
        columns = {str(column["name"]): column for column in inspector.get_columns(table)}
        column = columns[name]
        assert isinstance(column["type"], sa.Integer), name
        assert column["nullable"] is False, name
        assert str(column["default"]) == default, name
        # No reader filters on a display setting: no index.
        for index in inspector.get_indexes(table):
            assert name not in index["column_names"], (table, index["name"])


def test_display_settings_checks_have_exact_names_and_literals(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for name, (table, _literal) in _DISPLAY_SETTINGS_CHECKS.items():
        assert name in {str(check["name"]) for check in inspector.get_check_constraints(table)}
    model_checks = {
        str(constraint.name): str(constraint.sqltext)
        for model in (models.Department, models.ApplicationPolicy)
        for constraint in cast(sa.Table, model.__table__).constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    for name, (_table, literal) in _DISPLAY_SETTINGS_CHECKS.items():
        assert model_checks[name] == literal, name


def test_display_settings_migration_repeats_the_model_literals() -> None:
    migration = _load_migration(_DISPLAY_SETTINGS_MIGRATION_FILE)
    assert migration.down_revision == _PLANNED_ROUTES_REVISION
    assert migration._SECTION == policies.DUE_SOON_SECTION
    assert migration._SECONDS_PER_ROW_SQL == models.DEPARTMENT_BOARD_SECONDS_PER_ROW_SQL
    assert migration._MIN_PAGE_SECONDS_SQL == models.DEPARTMENT_BOARD_MIN_PAGE_SECONDS_SQL
    assert migration._DUE_SOON_MIN_DAYS_SQL == models.POLICY_DUE_SOON_MIN_DAYS_SQL
    assert migration._DUE_SOON_MAX_DAYS_SQL == models.POLICY_DUE_SOON_MAX_DAYS_SQL
    assert migration._DUE_SOON_PERCENT_SQL == models.POLICY_DUE_SOON_PERCENT_SQL
    assert migration._DUE_SOON_ORDER_SQL == models.POLICY_DUE_SOON_ORDER_SQL
    assert (
        migration._SECONDS_PER_ROW_MIN,
        migration._SECONDS_PER_ROW_MAX,
        migration._SECONDS_PER_ROW_DEFAULT,
    ) == (
        models.BOARD_SECONDS_PER_ROW_MIN,
        models.BOARD_SECONDS_PER_ROW_MAX,
        models.BOARD_SECONDS_PER_ROW_DEFAULT,
    )
    assert (
        migration._MIN_PAGE_SECONDS_MIN,
        migration._MIN_PAGE_SECONDS_MAX,
        migration._MIN_PAGE_SECONDS_DEFAULT,
    ) == (
        models.BOARD_MIN_PAGE_SECONDS_MIN,
        models.BOARD_MIN_PAGE_SECONDS_MAX,
        models.BOARD_MIN_PAGE_SECONDS_DEFAULT,
    )
    assert (
        migration._DUE_SOON_MIN_DAYS_DEFAULT,
        migration._DUE_SOON_PERCENT_DEFAULT,
        migration._DUE_SOON_MAX_DAYS_DEFAULT,
    ) == (
        models.DUE_SOON_MIN_DAYS_DEFAULT,
        models.DUE_SOON_LEAD_TIME_PERCENT_DEFAULT,
        models.DUE_SOON_MAX_DAYS_DEFAULT,
    )
    # The literal range texts state the named bounds.
    assert models.DEPARTMENT_BOARD_SECONDS_PER_ROW_SQL.endswith(
        f"BETWEEN {models.BOARD_SECONDS_PER_ROW_MIN} AND {models.BOARD_SECONDS_PER_ROW_MAX}"
    )
    assert models.DEPARTMENT_BOARD_MIN_PAGE_SECONDS_SQL.endswith(
        f"BETWEEN {models.BOARD_MIN_PAGE_SECONDS_MIN} AND {models.BOARD_MIN_PAGE_SECONDS_MAX}"
    )
    for literal in (models.POLICY_DUE_SOON_MIN_DAYS_SQL, models.POLICY_DUE_SOON_MAX_DAYS_SQL):
        assert literal.endswith(
            f"BETWEEN {models.DUE_SOON_DAYS_MIN} AND {models.DUE_SOON_DAYS_MAX}"
        )
    assert models.POLICY_DUE_SOON_PERCENT_SQL.endswith(
        f"BETWEEN {models.DUE_SOON_PERCENT_MIN} AND {models.DUE_SOON_PERCENT_MAX}"
    )
    # The downgrade guard names the same section and the same defaults.
    source = _DISPLAY_SETTINGS_MIGRATION_FILE.read_text(encoding="utf-8")
    assert f"entity_id = '{migration._SECTION}'" in source
    assert (
        f"board_seconds_per_row <> {models.BOARD_SECONDS_PER_ROW_DEFAULT}"
        f" OR board_min_page_seconds <> {models.BOARD_MIN_PAGE_SECONDS_DEFAULT}"
    ) in source
    assert f"due_soon_min_days <> {models.DUE_SOON_MIN_DAYS_DEFAULT}" in source
    assert f"due_soon_lead_time_percent <> {models.DUE_SOON_LEAD_TIME_PERCENT_DEFAULT}" in source
    assert f"due_soon_max_days <> {models.DUE_SOON_MAX_DAYS_DEFAULT}" in source


def test_display_settings_are_seeded_with_the_defaults(connection: Connection) -> None:
    assert _due_soon_row(connection) == (2, 15, 7)
    _execute(connection, "INSERT INTO departments (name) VALUES ('Display Seed')")
    assert _department_settings(connection)[-1] == ("Display Seed", 3, 6)


@pytest.mark.parametrize(
    ("constraint", "statement"),
    [
        (
            "ck_departments_board_seconds_per_row_range",
            "UPDATE departments SET board_seconds_per_row = 0",
        ),
        (
            "ck_departments_board_seconds_per_row_range",
            "UPDATE departments SET board_seconds_per_row = 61",
        ),
        (
            "ck_departments_board_min_page_seconds_range",
            "UPDATE departments SET board_min_page_seconds = 0",
        ),
        (
            "ck_departments_board_min_page_seconds_range",
            "UPDATE departments SET board_min_page_seconds = 301",
        ),
        (
            "ck_application_policy_due_soon_min_days_range",
            "UPDATE application_policy SET due_soon_min_days = -1",
        ),
        (
            "ck_application_policy_due_soon_min_days_range",
            "UPDATE application_policy SET due_soon_min_days = 366, due_soon_max_days = 365",
        ),
        (
            "ck_application_policy_due_soon_max_days_range",
            "UPDATE application_policy SET due_soon_max_days = 366",
        ),
        (
            "ck_application_policy_due_soon_lead_time_percent_range",
            "UPDATE application_policy SET due_soon_lead_time_percent = 0",
        ),
        (
            "ck_application_policy_due_soon_lead_time_percent_range",
            "UPDATE application_policy SET due_soon_lead_time_percent = 101",
        ),
        (
            "ck_application_policy_due_soon_window_order",
            "UPDATE application_policy SET due_soon_min_days = 5, due_soon_max_days = 3",
        ),
    ],
)
def test_database_refuses_out_of_range_display_settings(
    connection: Connection, constraint: str, statement: str
) -> None:
    _execute(connection, "INSERT INTO departments (name) VALUES ('Range Seed')")
    _refused_by(connection, constraint, lambda: _execute(connection, statement))


def test_database_admits_the_display_settings_boundaries(connection: Connection) -> None:
    _execute(connection, "INSERT INTO departments (name) VALUES ('Boundary Seed')")
    for per_row, dwell in ((1, 1), (60, 300)):
        _execute(
            connection,
            "UPDATE departments SET board_seconds_per_row = :per_row,"
            " board_min_page_seconds = :dwell",
            per_row=per_row,
            dwell=dwell,
        )
    for low, percent, high in ((0, 1, 0), (365, 100, 365), (3, 15, 3)):
        _execute(
            connection,
            "UPDATE application_policy SET due_soon_min_days = :low,"
            " due_soon_lead_time_percent = :percent, due_soon_max_days = :high",
            low=low,
            percent=percent,
            high=high,
        )
        assert _due_soon_row(connection) == (low, percent, high)


def test_downgrade_to_planned_routes_revision_drops_the_settings(admin_engine: Engine) -> None:
    """Department CREATED rows and name-only UPDATED rows that merely carry
    the keys never block the downgrade; the re-upgrade restores the head."""
    name = "partflow_test_phase13_downgrade_s9"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _execute(connection, "INSERT INTO departments (name) VALUES ('Kept')")
                created = {**_DEFAULT_DEPARTMENT_SNAPSHOT, "name": "Kept"}
                _insert_department_audit(connection, "CREATED", None, created)
                _insert_department_audit(
                    connection, "UPDATED", created, {**created, "name": "Renamed"}
                )
            command.downgrade(config, _PLANNED_ROUTES_REVISION)
            inspector = inspect(engine)
            for table, column in _DISPLAY_SETTINGS_COLUMNS:
                assert column not in {str(c["name"]) for c in inspector.get_columns(table)}
            for check, (table, _literal) in _DISPLAY_SETTINGS_CHECKS.items():
                assert check not in {
                    str(found["name"]) for found in inspector.get_check_constraints(table)
                }
            with engine.connect() as connection:
                assert _version(connection) == _PLANNED_ROUTES_REVISION
                kept = connection.execute(
                    sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'Department'")
                ).scalar_one()
                assert kept == 2
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _department_settings(connection) == [("Kept", 3, 6)]
                assert _due_soon_row(connection) == (2, 15, 7)
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def _refused_display_settings_downgrade(url: URL, message: str) -> None:
    with pytest.raises(ProgrammingError, match=message):
        command.downgrade(_alembic_config(url), _PLANNED_ROUTES_REVISION)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE departments SET board_seconds_per_row = 2",
        "UPDATE departments SET board_min_page_seconds = 10",
        "UPDATE application_policy SET due_soon_min_days = 1",
        "UPDATE application_policy SET due_soon_lead_time_percent = 20",
        "UPDATE application_policy SET due_soon_max_days = 9",
    ],
    ids=["seconds-per-row", "min-page-seconds", "min-days", "percent", "max-days"],
)
def test_downgrade_refuses_while_a_display_setting_differs_from_its_default(
    refused_database: URL, statement: str
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _execute(connection, "INSERT INTO departments (name) VALUES ('Configured')")
            _execute(connection, statement)
            departments = _department_settings(connection)
            policy = _due_soon_row(connection)
        _refused_display_settings_downgrade(
            refused_database, "Display settings configuration exists"
        )
        with engine.connect() as connection:
            assert _department_settings(connection) == departments
            assert _due_soon_row(connection) == policy
    finally:
        engine.dispose()


def test_downgrade_refuses_while_due_soon_audit_exists(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_policy_audit(
                connection,
                "due-soon",
                {"due_soon_min_days": 2, "due_soon_lead_time_percent": 15, "due_soon_max_days": 7},
            )
        _refused_display_settings_downgrade(refused_database, "Display settings history exists")
        with engine.connect() as connection:
            kept = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_id = 'due-soon'")
            ).scalar_one()
        assert kept == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("key", ["board_seconds_per_row", "board_min_page_seconds"])
def test_downgrade_refuses_while_a_department_audit_changed_a_rotation_setting(
    refused_database: URL, key: str
) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            # The values are back at their defaults; the history remains.
            _insert_department_audit(
                connection,
                "UPDATED",
                _DEFAULT_DEPARTMENT_SNAPSHOT,
                {**_DEFAULT_DEPARTMENT_SNAPSHOT, key: 9},
            )
        _refused_display_settings_downgrade(refused_database, "Display settings history exists")
        with engine.connect() as connection:
            kept = connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'Department'")
            ).scalar_one()
        assert kept == 1
    finally:
        engine.dispose()


def test_upgrade_gives_departments_and_the_policy_their_defaults(admin_engine: Engine) -> None:
    """0024 → head: every existing Department gets 3 / 6 and the singleton
    2 / 15 / 7; the earlier policy values and every audit row are kept."""
    name = "partflow_test_phase13_display_settings_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _PLANNED_ROUTES_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _execute(connection, "INSERT INTO departments (name) VALUES ('Machine Shop')")
                _execute(
                    connection, "INSERT INTO departments (name, is_active) VALUES ('Old', false)"
                )
                _execute(
                    connection,
                    "UPDATE application_policy SET worker_session_timeout_minutes = 30,"
                    " undo_reason_required = true",
                )
                _insert_policy_audit(
                    connection, "worker-sessions", {"worker_session_timeout_minutes": 30}
                )
                _insert_policy_audit(
                    connection, "correction-permissions", {"undo_reason_required": True}
                )
                _insert_department_audit(
                    connection, "CREATED", None, {"name": "Machine Shop", "is_active": True}
                )
                audits = _rows(connection, "audit_events")
                departments = _rows(connection, "departments")
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _rows(connection, "departments") == [
                    {**row, "board_seconds_per_row": 3, "board_min_page_seconds": 6}
                    for row in departments
                ]
                assert _due_soon_row(connection) == (2, 15, 7)
                assert _policy_row(connection) == (1, 30, True, True, True)
                assert _undo_reason_required(connection) is True
                assert _rows(connection, "audit_events") == audits
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


# ---------------------------------------------------------------------------
# Scan Station theme preference (0026)
# ---------------------------------------------------------------------------

_THEME_CHECK = "ck_scan_stations_theme_preference"


def _insert_station(connection: Connection, station_id: str) -> None:
    """A Department, an Area and one Scan Station by raw SQL (valid at 0025 and at head)."""
    department = _scalar_id(
        connection,
        "INSERT INTO departments (name) VALUES (:name) RETURNING id",
        name=f"Theme {station_id}",
    )
    area = _scalar_id(
        connection,
        "INSERT INTO areas (department_id, name) VALUES (:department, 'Theme Area') RETURNING id",
        department=department,
    )
    _execute(
        connection,
        "INSERT INTO scan_stations (station_id, area_id) VALUES (:station, :area)",
        station=station_id,
        area=area,
    )


def _station_themes(connection: Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            sa.text("SELECT station_id, theme_preference FROM scan_stations ORDER BY station_id")
        )
    ]


def _station_rows(connection: Connection) -> list[dict[str, object]]:
    return [
        dict(row._mapping)
        for row in connection.execute(sa.text("SELECT * FROM scan_stations ORDER BY station_id"))
    ]


def test_station_theme_column_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(column["name"]): column for column in inspector.get_columns("scan_stations")}
    column = columns["theme_preference"]
    assert isinstance(column["type"], sa.Text)
    assert column["nullable"] is True
    assert column["default"] is None
    # Only ever read by primary key: no index.
    for index in inspector.get_indexes("scan_stations"):
        assert "theme_preference" not in index["column_names"], index["name"]


def test_station_theme_check_has_exact_name_and_literal(migrated_engine: Engine) -> None:
    checks = {
        str(check["name"]): str(check["sqltext"])
        for check in inspect(migrated_engine).get_check_constraints("scan_stations")
    }
    assert set(re.findall(r"'([^']*)'", checks[_THEME_CHECK])) == {"DARK", "LIGHT"}
    with migrated_engine.connect() as connection:
        definition = str(
            connection.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"
                ),
                {"name": _THEME_CHECK},
            ).scalar_one()
        )
    assert set(re.findall(r"'([^']*)'", definition)) == {"DARK", "LIGHT"}
    model_checks = {
        str(constraint.name): str(constraint.sqltext)
        for constraint in cast(sa.Table, models.ScanStation.__table__).constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert model_checks[_THEME_CHECK] == models.SCAN_STATION_THEME_PREFERENCE_SQL


def test_migration_repeats_the_model_theme_check_verbatim() -> None:
    migration = _load_migration(_STATION_THEME_MIGRATION_FILE)
    assert migration.down_revision == _DISPLAY_SETTINGS_REVISION
    assert migration._THEME_CHECK == _THEME_CHECK
    assert migration._THEME_PREFERENCE_SQL == models.SCAN_STATION_THEME_PREFERENCE_SQL


@pytest.mark.parametrize("theme", [None, "DARK", "LIGHT"])
def test_database_theme_check_admits(connection: Connection, theme: str | None) -> None:
    _insert_station(connection, "THEME-OK")
    _execute(
        connection,
        "UPDATE scan_stations SET theme_preference = :theme WHERE station_id = 'THEME-OK'",
        theme=theme,
    )
    assert ("THEME-OK", theme) in _station_themes(connection)


@pytest.mark.parametrize("theme", ["dark", "Light", "", "AUTO"])
def test_database_theme_check_refuses(connection: Connection, theme: str) -> None:
    _insert_station(connection, "THEME-BAD")
    _refused_by(
        connection,
        _THEME_CHECK,
        lambda: _execute(
            connection,
            "UPDATE scan_stations SET theme_preference = :theme WHERE station_id = 'THEME-BAD'",
            theme=theme,
        ),
    )


def test_downgrade_to_display_settings_revision_drops_the_theme_column(
    admin_engine: Engine,
) -> None:
    """A station without a preference never blocks the downgrade; the
    re-upgrade restores the head with the station still unset."""
    name = "partflow_test_phase13_downgrade_s10"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _insert_station(connection, "KEPT-1")
            command.downgrade(config, _DISPLAY_SETTINGS_REVISION)
            inspector = inspect(engine)
            assert "theme_preference" not in {
                str(column["name"]) for column in inspector.get_columns("scan_stations")
            }
            assert _THEME_CHECK not in {
                str(check["name"]) for check in inspector.get_check_constraints("scan_stations")
            }
            with engine.connect() as connection:
                assert _version(connection) == _DISPLAY_SETTINGS_REVISION
                kept = connection.execute(sa.text("SELECT station_id FROM scan_stations"))
                assert kept.scalar_one() == "KEPT-1"
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _station_themes(connection) == [("KEPT-1", None)]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)


def test_downgrade_refuses_while_a_station_theme_is_saved(refused_database: URL) -> None:
    engine = create_engine(refused_database)
    try:
        with engine.begin() as connection:
            _insert_station(connection, "THEMED-1")
            _execute(
                connection,
                "UPDATE scan_stations SET theme_preference = 'LIGHT' WHERE station_id = 'THEMED-1'",
            )
        with pytest.raises(ProgrammingError, match="hold a saved theme preference"):
            command.downgrade(_alembic_config(refused_database), _DISPLAY_SETTINGS_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            assert _station_themes(connection) == [("THEMED-1", "LIGHT")]
    finally:
        engine.dispose()


def test_upgrade_keeps_existing_stations_without_preference(admin_engine: Engine) -> None:
    """0025 → head: every existing station keeps its row with no preference."""
    name = "partflow_test_phase13_station_theme_upgrade"
    _create_temp_database(admin_engine, name)
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    config = _alembic_config(url)
    try:
        command.upgrade(config, _DISPLAY_SETTINGS_REVISION)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _insert_station(connection, "OLD-1")
                _insert_station(connection, "OLD-2")
                _execute(
                    connection,
                    "UPDATE scan_stations SET is_active = false WHERE station_id = 'OLD-2'",
                )
                stations = _station_rows(connection)
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _version(connection) == _HEAD_REVISION
                assert _station_rows(connection) == [
                    {**row, "theme_preference": None} for row in stations
                ]
        finally:
            engine.dispose()
    finally:
        _drop_temp_database(admin_engine, name)
