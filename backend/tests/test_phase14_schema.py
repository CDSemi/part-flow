"""Integration tests for the Phase 14 migrations.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0029_phase14_sign_in` adds (IMPLEMENTATION_ROADMAP
Phase 14; PROJECT_PROFILE §7 User; owner decisions OD-P1–OD-P5,
OD-P17) — pinned to `0029` since slice 4 added the next revision — and
what `0030_phase14_station_devices` adds (owner decisions OD-P6,
OD-S4-1) — pinned to `0030` since slice 5 added the next revision — and
what `0031_phase14_beyond_demand` adds (owner decisions OD-P12/P13) —
pinned to `0031` since slice 6 added the next revision — and what
`0032_phase14_route_adjusted` adds (owner decision OD-P11):

- exact head boundary: `0032_phase14_route_adjusted` is the single
  head (the 0029, 0030 and 0031 cases run against their own revision);
- the sign-in policy columns on `application_policy` (types, server
  defaults, the seeded row's values) with their exact range CHECKs;
- the `user_credentials` and `user_sessions` shapes with their exact
  constraint and index names (the partial open-session index
  included), and the three `actor_user_id` columns with their FKs and
  no index;
- every migration literal repeats its model constant
  (`_END_REASONS == UserSessionEndReason`, `_SECTION ==
  policies.SIGN_IN_SECTION`);
- the database refuses out-of-range policy values, a non-scrypt hash, a
  negative counter, a 31-byte or duplicate token digest, an unknown end
  reason and an end time without a reason;
- the 0029 downgrade on a clean 0029 database restores the previous
  boundary and re-upgrades; it refuses — keeping the version at 0029 —
  for each of its triggers separately (a credential, a session, an
  `actor_user_id` on each of the three tables, a `sign-in` audit row,
  each non-default policy value);
- 0030: the `scan_station_devices` shape with its exact constraint
  names and CHECK refusals, the NOT NULL `scan_station_role_id` set to
  the seeded Operator role, the widened audit entity CHECK, literal
  parity with the model constants, models↔migration metadata parity at
  head; up/down/up on a clean head; the downgrade refuses with a device
  row and, separately, with a `ScanStationDevice` audit row; the upgrade
  refuses (exact message) when no role is named Operator;
- 0031: `work_order_allocations.exceeds_demand` (boolean, NOT NULL,
  default false) and `ck_work_order_allocations_exceeds_demand_shape`
  with literal parity and each of its refusals, models↔migration
  metadata parity at head; pre-0031 rows (a station allocation with a
  Worker, Management allocations with and without a User, a reversal)
  read false across the upgrade and survive a clean downgrade; the
  downgrade refuses (exact message) while a correction row exists;
- 0032: the widened audit event and entity CHECKs (literal parity with
  the enums and the model constraints), the UNIQUE partial
  `uq_audit_events_route_adjustment_device_event_id` in the JSONB
  subscript form the application's lookup uses (EXPLAIN), the
  `ix_part_movements_assigned_route_step_id` index, the
  `trg_assigned_route_steps_forbid_update` trigger (UPDATE refused even
  for zero rows; DELETE of an unreferenced step and INSERT allowed),
  models↔migration metadata parity at head; up/down/up on a clean head;
  the downgrade refuses (exact message) while an `AssignedRoute` or
  `ROUTE_ADJUSTED` audit row exists.
"""

import functools
import importlib.util
import json
import os
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
from app.application import policies
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    StationDeviceRevokedReason,
    UserSessionEndReason,
)
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0028_phase13_users_roles"
_SIGN_IN_REVISION = "0029_phase14_sign_in"
_DEVICES_REVISION = "0030_phase14_station_devices"
_CORRECTION_REVISION = "0031_phase14_beyond_demand"
_ROUTE_REVISION = "0032_phase14_route_adjusted"
_HEAD_REVISION = _ROUTE_REVISION
_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "20261006_0029_phase14_sign_in.py"
_DEVICES_MIGRATION_FILE = _VERSIONS_DIR / "20261007_0030_phase14_station_devices.py"
_CORRECTION_MIGRATION_FILE = _VERSIONS_DIR / "20261007_0031_phase14_beyond_demand.py"
_ROUTE_MIGRATION_FILE = _VERSIONS_DIR / "20261007_0032_phase14_route_adjusted.py"
_TEMPLATE_DATABASE = "partflow_test_phase14_template"
_DEVICES_TEMPLATE_DATABASE = "partflow_test_phase14_devices_template"
_ACTOR_TABLES = ("audit_events", "machine_lifecycle_events", "work_order_allocations")
_POLICY_DEFAULTS = {
    "user_session_expires": (sa.Boolean, "true", True),
    "user_session_days": (sa.Integer, "30", 30),
    "sign_in_lockout_attempts": (sa.Integer, "10", 10),
    "sign_in_lockout_minutes": (sa.Integer, "15", 15),
    "require_password_change": (sa.Boolean, "true", True),
}
_HASH = "scrypt$32768$8$1$c2FsdA==$a2V5"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _create_temp_database(admin_engine: Engine, name: str, template: str | None = None) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        suffix = f' TEMPLATE "{template}"' if template else ""
        connection.execute(sa.text(f'CREATE DATABASE "{name}"{suffix}'))


def _drop_temp_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _url(name: str) -> URL:
    return make_url(os.environ["DATABASE_URL"]).set(database=name)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase14_sign_in_migration", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def migrated_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated 0029 → previous → 0029 through real Alembic
    runs (the 0029 cases are pinned to their own boundary)."""
    name = "partflow_test_phase14_schema"
    _create_temp_database(admin_engine, name)
    config = _alembic_config(_url(name))
    command.upgrade(config, _SIGN_IN_REVISION)
    command.downgrade(config, _PREVIOUS_REVISION)
    command.upgrade(config, _SIGN_IN_REVISION)
    engine = create_engine(_url(name))
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


@pytest.fixture(scope="module")
def head_template(admin_engine: Engine) -> Iterator[str]:
    """A clean database at 0029, cloned (CREATE DATABASE … TEMPLATE) per case."""
    _create_temp_database(admin_engine, _TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_TEMPLATE_DATABASE)), _SIGN_IN_REVISION)
    yield _TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _TEMPLATE_DATABASE)


@pytest.fixture
def head_database(admin_engine: Engine, head_template: str) -> Iterator[URL]:
    name = "partflow_test_phase14_downgrade"
    _create_temp_database(admin_engine, name, template=head_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


def _scalar_id(connection: Connection, statement: str, **params: object) -> int:
    return int(connection.execute(sa.text(statement), params).scalar_one())


def _insert_user(connection: Connection, login: str = "signin") -> int:
    return _scalar_id(
        connection,
        "INSERT INTO users (login_name, display_name, role_id)"
        " SELECT :login, 'Sign In', id FROM roles WHERE name = 'Administrator' RETURNING id",
        login=login,
    )


def _insert_credential(connection: Connection, user_id: int, password_hash: str = _HASH) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO user_credentials (user_id, password_hash, password_is_temporary,"
            " password_changed_at) VALUES (:user_id, :hash, true, now())"
        ),
        {"user_id": user_id, "hash": password_hash},
    )


def _insert_session(connection: Connection, user_id: int, digest: bytes = b"\x01" * 32) -> int:
    return _scalar_id(
        connection,
        "INSERT INTO user_sessions (user_id, token_digest) VALUES (:user_id, :digest) RETURNING id",
        user_id=user_id,
        digest=digest,
    )


def _refused_by(connection: Connection, constraint: str, statement: Callable[[], object]) -> None:
    """Run ``statement`` inside a savepoint; it must fail on ``constraint``."""
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        statement()
    savepoint.rollback()
    assert constraint in str(raised.value.orig)


# ---------------------------------------------------------------------------
# Head and shape
# ---------------------------------------------------------------------------


def test_alembic_has_a_single_head() -> None:
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    assert ScriptDirectory.from_config(config).get_heads() == [_HEAD_REVISION]


def test_the_sign_in_revision_boundary(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        assert _version(connection) == _SIGN_IN_REVISION
    migration = _load_migration()
    assert migration.revision == _SIGN_IN_REVISION
    assert migration.down_revision == _PREVIOUS_REVISION


def test_sign_in_policy_columns(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(c["name"]): c for c in inspector.get_columns("application_policy")}
    for name, (type_, default, _seeded) in _POLICY_DEFAULTS.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is False, name
        assert str(columns[name]["default"]) == default, name
    checks = {
        str(check["name"]): str(check["sqltext"])
        for check in inspector.get_check_constraints("application_policy")
    }
    assert "ck_application_policy_user_session_days_range" in checks
    assert "ck_application_policy_sign_in_lockout_attempts_range" in checks
    assert "ck_application_policy_sign_in_lockout_minutes_range" in checks
    with migrated_engine.connect() as connection:
        row = connection.execute(sa.text("SELECT * FROM application_policy")).mappings().one()
    for name, (_type, _default, seeded) in _POLICY_DEFAULTS.items():
        assert row[name] == seeded, name


def test_user_credentials_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(c["name"]): c for c in inspector.get_columns("user_credentials")}
    expected = {
        "user_id": (sa.Integer, False),
        "password_hash": (sa.Text, False),
        "password_is_temporary": (sa.Boolean, False),
        "password_changed_at": (sa.DateTime, False),
        "failed_attempts": (sa.Integer, False),
        "locked_until": (sa.DateTime, True),
        "created_at": (sa.DateTime, False),
    }
    assert set(columns) == set(expected)
    for name, (type_, nullable) in expected.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is nullable, name
    assert str(columns["failed_attempts"]["default"]) == "0"
    pk = inspector.get_pk_constraint("user_credentials")
    assert (pk["name"], pk["constrained_columns"]) == ("pk_user_credentials", ["user_id"])
    fks = inspector.get_foreign_keys("user_credentials")
    assert [(fk["name"], fk["referred_table"]) for fk in fks] == [
        ("fk_user_credentials_user_id_users", "users")
    ]
    assert {str(c["name"]) for c in inspector.get_check_constraints("user_credentials")} == {
        "ck_user_credentials_password_hash_format",
        "ck_user_credentials_failed_attempts_non_negative",
    }
    # Credentials never live on the users row.
    user_columns = {str(c["name"]) for c in inspector.get_columns("users")}
    assert not {name for name in user_columns if "password" in name or "credential" in name}


def test_user_sessions_shape(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    columns = {str(c["name"]): c for c in inspector.get_columns("user_sessions")}
    expected = {
        "id": (sa.BigInteger, False),
        "user_id": (sa.Integer, False),
        "token_digest": (sa.LargeBinary, False),
        "created_at": (sa.DateTime, False),
        "ended_at": (sa.DateTime, True),
        "end_reason": (sa.Text, True),
    }
    assert set(columns) == set(expected)
    for name, (type_, nullable) in expected.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is nullable, name
    assert inspector.get_pk_constraint("user_sessions")["name"] == "pk_user_sessions"
    assert [
        (fk["name"], fk["referred_table"]) for fk in inspector.get_foreign_keys("user_sessions")
    ] == [("fk_user_sessions_user_id_users", "users")]
    assert [
        (uq["name"], uq["column_names"]) for uq in inspector.get_unique_constraints("user_sessions")
    ] == [("uq_user_sessions_token_digest", ["token_digest"])]
    assert {str(c["name"]) for c in inspector.get_check_constraints("user_sessions")} == {
        "ck_user_sessions_token_digest_length",
        "ck_user_sessions_end_reason",
        "ck_user_sessions_end_shape",
    }
    indexes = {
        str(index["name"]): index
        for index in inspector.get_indexes("user_sessions")
        if not index.get("duplicates_constraint")
    }
    assert set(indexes) == {"ix_user_sessions_user_id_open"}
    assert indexes["ix_user_sessions_user_id_open"]["column_names"] == ["user_id"]
    with migrated_engine.connect() as connection:
        definition = connection.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": "ix_user_sessions_user_id_open"},
        ).scalar_one()
    assert str(definition).endswith("WHERE (ended_at IS NULL)")


def test_actor_user_id_columns(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    for table in _ACTOR_TABLES:
        columns = {str(c["name"]): c for c in inspector.get_columns(table)}
        column = columns["actor_user_id"]
        assert isinstance(column["type"], sa.Integer), table
        assert column["nullable"] is True, table
        assert column["default"] is None, table
        fks = {str(fk["name"]): fk for fk in inspector.get_foreign_keys(table)}
        fk = fks[f"fk_{table}_actor_user_id_users"]
        assert (fk["referred_table"], fk["constrained_columns"]) == ("users", ["actor_user_id"])
        # Users are never deleted, so no index serves the FK.
        assert not [
            index
            for index in inspector.get_indexes(table)
            if "actor_user_id" in index["column_names"]
        ], table


def test_migration_literals_repeat_the_model_constants() -> None:
    migration = _load_migration()
    assert migration._SESSION_DAYS_SQL == models.POLICY_USER_SESSION_DAYS_SQL
    assert migration._LOCKOUT_ATTEMPTS_SQL == models.POLICY_SIGN_IN_LOCKOUT_ATTEMPTS_SQL
    assert migration._LOCKOUT_MINUTES_SQL == models.POLICY_SIGN_IN_LOCKOUT_MINUTES_SQL
    assert migration._HASH_FORMAT_SQL == models.USER_CREDENTIAL_HASH_FORMAT_SQL
    assert migration._FAILED_ATTEMPTS_SQL == models.USER_CREDENTIAL_FAILED_ATTEMPTS_SQL
    assert migration._TOKEN_DIGEST_SQL == models.USER_SESSION_TOKEN_DIGEST_SQL
    assert migration._END_REASON_SQL == models.USER_SESSION_END_REASON_SQL
    assert migration._END_SHAPE_SQL == models.USER_SESSION_END_SHAPE_SQL
    assert tuple(UserSessionEndReason) == migration._END_REASONS
    assert migration._SECTION == policies.SIGN_IN_SECTION
    assert migration._ACTOR_TABLES == _ACTOR_TABLES
    assert (models.USER_SESSION_DAYS_MIN, models.USER_SESSION_DAYS_MAX) == (1, 365)
    assert (models.SIGN_IN_LOCKOUT_ATTEMPTS_MIN, models.SIGN_IN_LOCKOUT_ATTEMPTS_MAX) == (3, 100)
    assert (models.SIGN_IN_LOCKOUT_MINUTES_MIN, models.SIGN_IN_LOCKOUT_MINUTES_MAX) == (1, 1440)


# ---------------------------------------------------------------------------
# Database refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("user_session_days", 0, "ck_application_policy_user_session_days_range"),
        ("user_session_days", 366, "ck_application_policy_user_session_days_range"),
        ("sign_in_lockout_attempts", 2, "ck_application_policy_sign_in_lockout_attempts_range"),
        ("sign_in_lockout_attempts", 101, "ck_application_policy_sign_in_lockout_attempts_range"),
        ("sign_in_lockout_minutes", 0, "ck_application_policy_sign_in_lockout_minutes_range"),
        ("sign_in_lockout_minutes", 1441, "ck_application_policy_sign_in_lockout_minutes_range"),
    ],
)
def test_policy_ranges_are_refused(
    connection: Connection, column: str, value: int, constraint: str
) -> None:
    _refused_by(
        connection,
        constraint,
        lambda: connection.execute(
            sa.text(f"UPDATE application_policy SET {column} = :value"), {"value": value}
        ),
    )


def test_credential_and_session_rows_are_refused_when_malformed(connection: Connection) -> None:
    user_id = _insert_user(connection)
    _refused_by(
        connection,
        "ck_user_credentials_password_hash_format",
        lambda: _insert_credential(connection, user_id, "bcrypt$2b$12$abc"),
    )
    _insert_credential(connection, user_id)
    _refused_by(
        connection,
        "ck_user_credentials_failed_attempts_non_negative",
        lambda: connection.execute(
            sa.text("UPDATE user_credentials SET failed_attempts = -1 WHERE user_id = :id"),
            {"id": user_id},
        ),
    )
    _refused_by(
        connection,
        "ck_user_sessions_token_digest_length",
        lambda: _insert_session(connection, user_id, b"\x02" * 31),
    )
    session_id = _insert_session(connection, user_id)
    _refused_by(
        connection,
        "uq_user_sessions_token_digest",
        lambda: _insert_session(connection, user_id),
    )
    _refused_by(
        connection,
        "ck_user_sessions_end_reason",
        lambda: connection.execute(
            sa.text(
                "UPDATE user_sessions SET ended_at = now(), end_reason = 'EXPIRED' WHERE id = :id"
            ),
            {"id": session_id},
        ),
    )
    _refused_by(
        connection,
        "ck_user_sessions_end_shape",
        lambda: connection.execute(
            sa.text("UPDATE user_sessions SET ended_at = now() WHERE id = :id"),
            {"id": session_id},
        ),
    )
    for reason in UserSessionEndReason:
        savepoint = connection.begin_nested()
        connection.execute(
            sa.text("UPDATE user_sessions SET ended_at = now(), end_reason = :r WHERE id = :id"),
            {"r": str(reason), "id": session_id},
        )
        savepoint.rollback()


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_clean_downgrade_restores_the_previous_boundary(head_database: URL) -> None:
    config = _alembic_config(head_database)
    engine = create_engine(head_database)
    try:
        command.downgrade(config, _PREVIOUS_REVISION)
        inspector = inspect(engine)
        assert not {"user_credentials", "user_sessions"} & set(inspector.get_table_names())
        policy_columns = {str(c["name"]) for c in inspector.get_columns("application_policy")}
        assert not set(_POLICY_DEFAULTS) & policy_columns
        for table in _ACTOR_TABLES:
            assert "actor_user_id" not in {str(c["name"]) for c in inspector.get_columns(table)}
        with engine.connect() as connection:
            assert _version(connection) == _PREVIOUS_REVISION
        command.upgrade(config, _SIGN_IN_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _SIGN_IN_REVISION
    finally:
        engine.dispose()


def _seed_machine_event(connection: Connection, user_id: int) -> None:
    department = _scalar_id(
        connection, "INSERT INTO departments (name) VALUES ('Sign-in Seed') RETURNING id"
    )
    area = _scalar_id(
        connection,
        "INSERT INTO areas (department_id, name) VALUES (:department, 'Seed Area') RETURNING id",
        department=department,
    )
    machine = _scalar_id(
        connection,
        "INSERT INTO machines (area_id, name, asset_tag) VALUES (:area, 'Lathe', 'SI-1')"
        " RETURNING id",
        area=area,
    )
    connection.execute(
        sa.text(
            "INSERT INTO machine_lifecycle_events (machine_id, event_type, occurred_at,"
            " before_state, after_state, actor_user_id)"
            " VALUES (:machine, 'RETIRED', now(), 'ACTIVE', 'RETIRED', :user_id)"
        ),
        {"machine": machine, "user_id": user_id},
    )


def _seed_allocation(connection: Connection, user_id: int) -> None:
    work_order = _scalar_id(
        connection, "INSERT INTO work_orders (received_date) VALUES (current_date) RETURNING id"
    )
    demand = _scalar_id(
        connection,
        "INSERT INTO work_order_demands (work_order_id, part_number, request_type,"
        " requested_quantity) VALUES (:work_order, 'PN-SI', 'NEW', 5) RETURNING id",
        work_order=work_order,
    )
    connection.execute(
        sa.text(
            "INSERT INTO work_order_allocations (part_number, work_order_demand_id, quantity,"
            " source, allocated_at, device_event_id, actor_user_id)"
            " VALUES ('PN-SI', :demand, 2, 'MANAGEMENT', now(), 'SI-ALLOC', :user_id)"
        ),
        {"demand": demand, "user_id": user_id},
    )


def _seed_audit(connection: Connection, entity_id: str, user_id: int | None) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " actor_user_id) VALUES ('UPDATED', 'ApplicationPolicy', :entity_id, now(), :user_id)"
        ),
        {"entity_id": entity_id, "user_id": user_id},
    )


def _policy_change(statement: str) -> Callable[[Connection, int], None]:
    def apply(connection: Connection, _user_id: int) -> None:
        connection.execute(sa.text(f"UPDATE application_policy SET {statement}"))

    return apply


def _open_session(connection: Connection, user_id: int) -> None:
    _insert_session(connection, user_id)


def _actor_audit(connection: Connection, user_id: int) -> None:
    _seed_audit(connection, "due-soon", user_id)


def _sign_in_audit(connection: Connection, _user_id: int) -> None:
    _seed_audit(connection, "sign-in", None)


_TRIGGERS: dict[str, Callable[[Connection, int], None]] = {
    "credential": _insert_credential,
    "session": _open_session,
    "audit actor": _actor_audit,
    "machine lifecycle actor": _seed_machine_event,
    "allocation actor": _seed_allocation,
    "sign-in audit row": _sign_in_audit,
    "sessions never expire": _policy_change("user_session_expires = false"),
    "session days": _policy_change("user_session_days = 31"),
    "lockout attempts": _policy_change("sign_in_lockout_attempts = 11"),
    "lockout minutes": _policy_change("sign_in_lockout_minutes = 16"),
    "no forced change": _policy_change("require_password_change = false"),
}


@pytest.mark.parametrize("trigger", sorted(_TRIGGERS))
def test_downgrade_refuses_while_sign_in_data_exists(head_database: URL, trigger: str) -> None:
    engine = create_engine(head_database)
    try:
        with engine.begin() as connection:
            user_id = _insert_user(connection)
            _TRIGGERS[trigger](connection, user_id)
        with pytest.raises(ProgrammingError, match="Sign-in data or configuration exists"):
            command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _SIGN_IN_REVISION
        assert {"user_credentials", "user_sessions"} <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_downgrade_is_not_refused_by_a_plain_user(head_database: URL) -> None:
    """A User without a credential belongs to 0028; only 0028's own refusal applies."""
    engine = create_engine(head_database)
    try:
        with engine.begin() as connection:
            _insert_user(connection)
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _PREVIOUS_REVISION
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 0030 — enrolled Scan Station devices (Phase 14 slice 4)
# ---------------------------------------------------------------------------

_DEVICE_CHECKS = {
    "ck_scan_station_devices_enrollment_code_digest_length",
    "ck_scan_station_devices_token_digest_length",
    "ck_scan_station_devices_code_or_token",
    "ck_scan_station_devices_activation_shape",
    "ck_scan_station_devices_revocation_shape",
    "ck_scan_station_devices_revoked_reason",
    "ck_scan_station_devices_replaced_shape",
    "ck_scan_station_devices_last_seen_shape",
    "ck_scan_station_devices_no_self_replace",
}
_NO_OPERATOR = (
    "No role is named Operator. Rename the role whose permissions Scan Stations should use"
    " to Operator, run the upgrade, then rename it back."
)


def _load_devices_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase14_station_devices_migration", _DEVICES_MIGRATION_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def devices_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated 0030 → 0029 → 0030 through real Alembic runs
    (the 0030 cases are pinned to their own boundary)."""
    name = "partflow_test_phase14_devices_schema"
    _create_temp_database(admin_engine, name)
    config = _alembic_config(_url(name))
    command.upgrade(config, _DEVICES_REVISION)
    command.downgrade(config, _SIGN_IN_REVISION)
    command.upgrade(config, _DEVICES_REVISION)
    engine = create_engine(_url(name))
    yield engine
    engine.dispose()
    _drop_temp_database(admin_engine, name)


@pytest.fixture
def devices_connection(devices_engine: Engine) -> Iterator[Connection]:
    """Per-test connection at 0030 whose transaction is always rolled back."""
    with devices_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


@pytest.fixture(scope="module")
def devices_template(admin_engine: Engine) -> Iterator[str]:
    _create_temp_database(admin_engine, _DEVICES_TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_DEVICES_TEMPLATE_DATABASE)), _DEVICES_REVISION)
    yield _DEVICES_TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _DEVICES_TEMPLATE_DATABASE)


@pytest.fixture
def devices_head_database(admin_engine: Engine, devices_template: str) -> Iterator[URL]:
    name = "partflow_test_phase14_devices_downgrade"
    _create_temp_database(admin_engine, name, template=devices_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


def _insert_station(connection: Connection, station_id: str = "DEV-ST") -> str:
    department = _scalar_id(
        connection, "INSERT INTO departments (name) VALUES (:name) RETURNING id", name=station_id
    )
    area = _scalar_id(
        connection,
        "INSERT INTO areas (department_id, name) VALUES (:department, :name) RETURNING id",
        department=department,
        name=station_id,
    )
    connection.execute(
        sa.text("INSERT INTO scan_stations (station_id, area_id) VALUES (:station, :area)"),
        {"station": station_id, "area": area},
    )
    return station_id


def _insert_device(connection: Connection, station_id: str, **columns: object) -> int:
    values: dict[str, object] = {
        "station_id": station_id,
        "label": "Device",
        "enrollment_expires_at": sa.func.now(),
        "enrollment_code_digest": b"\x01" * 32,
        **columns,
    }
    table = models.Base.metadata.tables["scan_station_devices"]
    return int(
        connection.execute(sa.insert(table).values(values).returning(table.c.id)).scalar_one()
    )


def test_head_is_the_station_devices_revision(devices_engine: Engine) -> None:
    with devices_engine.connect() as connection:
        assert _version(connection) == _DEVICES_REVISION
    migration = _load_devices_migration()
    assert migration.revision == _DEVICES_REVISION
    assert migration.down_revision == _SIGN_IN_REVISION


def test_scan_station_devices_shape(devices_engine: Engine) -> None:
    inspector = inspect(devices_engine)
    columns = {str(c["name"]): c for c in inspector.get_columns("scan_station_devices")}
    expected = {
        "id": (sa.Integer, False),
        "station_id": (sa.Text, False),
        "label": (sa.Text, False),
        "enrollment_code_digest": (sa.LargeBinary, True),
        "enrollment_expires_at": (sa.DateTime, False),
        "token_digest": (sa.LargeBinary, True),
        "replaces_device_id": (sa.Integer, True),
        "issued_at": (sa.DateTime, False),
        "activated_at": (sa.DateTime, True),
        "last_seen_at": (sa.DateTime, True),
        "revoked_at": (sa.DateTime, True),
        "revoked_reason": (sa.Text, True),
    }
    assert set(columns) == set(expected)
    for name, (type_, nullable) in expected.items():
        assert isinstance(columns[name]["type"], type_), name
        assert columns[name]["nullable"] is nullable, name
    assert inspector.get_pk_constraint("scan_station_devices")["name"] == "pk_scan_station_devices"
    assert {
        (str(fk["name"]), fk["referred_table"])
        for fk in inspector.get_foreign_keys("scan_station_devices")
    } == {
        ("fk_scan_station_devices_station_id_scan_stations", "scan_stations"),
        (
            "fk_scan_station_devices_replaces_device_id_scan_station_devices",
            "scan_station_devices",
        ),
    }
    assert {
        (str(uq["name"]), tuple(uq["column_names"]))
        for uq in inspector.get_unique_constraints("scan_station_devices")
    } == {
        ("uq_scan_station_devices_enrollment_code_digest", ("enrollment_code_digest",)),
        ("uq_scan_station_devices_token_digest", ("token_digest",)),
    }
    checks = {str(c["name"]) for c in inspector.get_check_constraints("scan_station_devices")}
    assert checks == _DEVICE_CHECKS
    assert not [
        index
        for index in inspector.get_indexes("scan_station_devices")
        if not index.get("duplicates_constraint")
    ]


def test_the_station_role_pointer_is_the_seeded_operator_role(devices_engine: Engine) -> None:
    inspector = inspect(devices_engine)
    columns = {str(c["name"]): c for c in inspector.get_columns("application_policy")}
    column = columns["scan_station_role_id"]
    assert isinstance(column["type"], sa.Integer) and column["nullable"] is False
    assert column["default"] is None
    fks = {str(fk["name"]): fk for fk in inspector.get_foreign_keys("application_policy")}
    fk = fks["fk_application_policy_scan_station_role_id_roles"]
    assert (fk["referred_table"], fk["constrained_columns"]) == ("roles", ["scan_station_role_id"])
    with devices_engine.connect() as connection:
        pointer, operator = connection.execute(
            sa.text(
                "SELECT p.scan_station_role_id, r.id FROM application_policy p"
                " JOIN roles r ON r.name = 'Operator'"
            )
        ).one()
    assert pointer == operator


def test_device_migration_literals_repeat_the_model_constants() -> None:
    migration = _load_devices_migration()
    assert (
        migration._DIGEST_LEN_SQL.format(col="enrollment_code_digest")
        == models.SCAN_STATION_DEVICE_ENROLLMENT_CODE_DIGEST_SQL
    )
    assert (
        migration._DIGEST_LEN_SQL.format(col="token_digest")
        == models.SCAN_STATION_DEVICE_TOKEN_DIGEST_SQL
    )
    assert migration._CODE_OR_TOKEN_SQL == models.SCAN_STATION_DEVICE_CODE_OR_TOKEN_SQL
    assert migration._ACTIVATION_SHAPE_SQL == models.SCAN_STATION_DEVICE_ACTIVATION_SHAPE_SQL
    assert migration._REVOCATION_SHAPE_SQL == models.SCAN_STATION_DEVICE_REVOCATION_SHAPE_SQL
    assert migration._REVOKED_REASON_SQL == models.SCAN_STATION_DEVICE_REVOKED_REASON_SQL
    assert migration._REPLACED_SHAPE_SQL == models.SCAN_STATION_DEVICE_REPLACED_SHAPE_SQL
    assert migration._LAST_SEEN_SHAPE_SQL == models.SCAN_STATION_DEVICE_LAST_SEEN_SHAPE_SQL
    assert migration._NO_SELF_REPLACE_SQL == models.SCAN_STATION_DEVICE_NO_SELF_REPLACE_SQL
    assert tuple(StationDeviceRevokedReason) == migration._REVOKED_REASONS
    entity_check = next(
        constraint
        for constraint in models.Base.metadata.tables["audit_events"].constraints
        if constraint.name == "ck_audit_events_entity_type"
    )
    assert isinstance(entity_check, sa.CheckConstraint)
    # The model carries the latest vocabulary since slice 6 widened it
    # again (0032 re-creates it from exactly this literal plus its own).
    assert str(entity_check.sqltext) == (migration._DEVICE_ENTITY_TYPES[:-1] + ", 'AssignedRoute')")
    assert (
        migration._PREVIOUS_ENTITY_TYPES[:-1] + ", 'ScanStationDevice')"
    ) == migration._DEVICE_ENTITY_TYPES
    assert models.SCAN_STATION_DEVICE_LABEL_MAX == 80


def test_device_rows_are_refused_when_malformed(devices_connection: Connection) -> None:
    connection = devices_connection
    station = _insert_station(connection)
    now = sa.func.now()
    refusals: list[tuple[str, dict[str, object]]] = [
        (
            "ck_scan_station_devices_code_or_token",
            {"token_digest": b"\x02" * 32, "activated_at": now},
        ),
        ("ck_scan_station_devices_code_or_token", {"enrollment_code_digest": None}),
        (
            "ck_scan_station_devices_enrollment_code_digest_length",
            {"enrollment_code_digest": b"\x03" * 31},
        ),
        ("ck_scan_station_devices_activation_shape", {"activated_at": now}),
        ("ck_scan_station_devices_revocation_shape", {"revoked_at": now}),
        (
            "ck_scan_station_devices_revoked_reason",
            {"revoked_at": now, "revoked_reason": "LOST"},
        ),
        (
            "ck_scan_station_devices_replaced_shape",
            {"revoked_at": now, "revoked_reason": "REPLACED"},
        ),
        ("ck_scan_station_devices_last_seen_shape", {"last_seen_at": now}),
    ]
    for constraint, columns in refusals:
        _refused_by(
            connection,
            constraint,
            functools.partial(_insert_device, connection, station, **columns),
        )
    _refused_by(
        connection,
        "fk_scan_station_devices_station_id_scan_stations",
        functools.partial(
            _insert_device, connection, "NO-SUCH", enrollment_code_digest=bytes([5]) * 32
        ),
    )
    device = _insert_device(connection, station)
    _refused_by(
        connection,
        "ck_scan_station_devices_no_self_replace",
        lambda: connection.execute(
            sa.text("UPDATE scan_station_devices SET replaces_device_id = id WHERE id = :id"),
            {"id": device},
        ),
    )
    _refused_by(
        connection,
        "uq_scan_station_devices_enrollment_code_digest",
        lambda: _insert_device(connection, station),
    )
    active: dict[str, object] = {
        "enrollment_code_digest": None,
        "token_digest": b"\x04" * 32,
        "activated_at": now,
    }
    _insert_device(connection, station, **active)
    _refused_by(
        connection,
        "uq_scan_station_devices_token_digest",
        lambda: _insert_device(connection, station, **active),
    )
    # The widened audit vocabulary admits the device entity.
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
            " VALUES ('CREATED', 'ScanStationDevice', :id, now())"
        ),
        {"id": str(device)},
    )


def test_clean_device_downgrade_restores_the_sign_in_boundary(
    devices_head_database: URL,
) -> None:
    config = _alembic_config(devices_head_database)
    engine = create_engine(devices_head_database)
    try:
        command.downgrade(config, _SIGN_IN_REVISION)
        inspector = inspect(engine)
        assert "scan_station_devices" not in set(inspector.get_table_names())
        policy_columns = {str(c["name"]) for c in inspector.get_columns("application_policy")}
        assert "scan_station_role_id" not in policy_columns
        with engine.connect() as connection:
            assert _version(connection) == _SIGN_IN_REVISION
            check = connection.execute(
                sa.text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname = 'ck_audit_events_entity_type'"
                )
            ).scalar_one()
        assert "ScanStationDevice" not in str(check)
        command.upgrade(config, _DEVICES_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _DEVICES_REVISION
    finally:
        engine.dispose()


def _device_row(connection: Connection) -> None:
    _insert_device(connection, _insert_station(connection))


def _device_audit_row(connection: Connection) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
            " VALUES ('CREATED', 'ScanStationDevice', '1', now())"
        )
    )


_DEVICE_TRIGGERS: dict[str, Callable[[Connection], None]] = {
    "device row": _device_row,
    "device audit row": _device_audit_row,
}


@pytest.mark.parametrize("trigger", sorted(_DEVICE_TRIGGERS))
def test_device_downgrade_refuses_while_device_data_exists(
    devices_head_database: URL, trigger: str
) -> None:
    engine = create_engine(devices_head_database)
    try:
        with engine.begin() as connection:
            _DEVICE_TRIGGERS[trigger](connection)
        with pytest.raises(ProgrammingError, match="Scan Station device data exists"):
            command.downgrade(_alembic_config(devices_head_database), _SIGN_IN_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _DEVICES_REVISION
        assert "scan_station_devices" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_the_upgrade_refuses_without_an_operator_role(admin_engine: Engine) -> None:
    name = "partflow_test_phase14_no_operator"
    _create_temp_database(admin_engine, name)
    engine = create_engine(_url(name))
    try:
        config = _alembic_config(_url(name))
        command.upgrade(config, _SIGN_IN_REVISION)
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE roles SET name = 'Line Crew' WHERE name = 'Operator'")
            )
        with pytest.raises(ProgrammingError) as raised:
            command.upgrade(config, _CORRECTION_REVISION)
        assert _NO_OPERATOR in str(raised.value.orig)
        with engine.connect() as connection:
            assert _version(connection) == _SIGN_IN_REVISION
        assert "scan_station_devices" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        _drop_temp_database(admin_engine, name)


# ---------------------------------------------------------------------------
# 0031 — the authorized beyond-demand correction (Phase 14 slice 5)
# ---------------------------------------------------------------------------

_EXCEEDS_DEMAND_CHECK = "ck_work_order_allocations_exceeds_demand_shape"
_CORRECTION_TEMPLATE_DATABASE = "partflow_test_phase14_correction_template"


def _load_correction_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase14_beyond_demand_migration", _CORRECTION_MIGRATION_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def correction_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated 0031 → 0030 → 0031 through real Alembic
    runs (the 0031 cases are pinned to their own boundary)."""
    name = "partflow_test_phase14_correction_schema"
    _create_temp_database(admin_engine, name)
    config = _alembic_config(_url(name))
    command.upgrade(config, _CORRECTION_REVISION)
    command.downgrade(config, _DEVICES_REVISION)
    command.upgrade(config, _CORRECTION_REVISION)
    engine = create_engine(_url(name))
    yield engine
    engine.dispose()
    _drop_temp_database(admin_engine, name)


@pytest.fixture
def correction_connection(correction_engine: Engine) -> Iterator[Connection]:
    """Per-test connection at head whose transaction is always rolled back."""
    with correction_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


@pytest.fixture(scope="module")
def correction_template(admin_engine: Engine) -> Iterator[str]:
    _create_temp_database(admin_engine, _CORRECTION_TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_CORRECTION_TEMPLATE_DATABASE)), _CORRECTION_REVISION)
    yield _CORRECTION_TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _CORRECTION_TEMPLATE_DATABASE)


@pytest.fixture
def correction_head_database(admin_engine: Engine, correction_template: str) -> Iterator[URL]:
    name = "partflow_test_phase14_correction_downgrade"
    _create_temp_database(admin_engine, name, template=correction_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


def _insert_demand(connection: Connection, part_number: str = "PN-BD") -> int:
    work_order = _scalar_id(
        connection, "INSERT INTO work_orders (received_date) VALUES (current_date) RETURNING id"
    )
    return _scalar_id(
        connection,
        "INSERT INTO work_order_demands (work_order_id, part_number, request_type,"
        " requested_quantity) VALUES (:work_order, :pn, 'NEW', 10) RETURNING id",
        work_order=work_order,
        pn=part_number,
    )


def _insert_allocation(connection: Connection, demand: int, event: str, **columns: object) -> int:
    """One allocation row through SQL; ``exceeds_demand`` only when given
    (so the same helper writes pre-0031 rows)."""
    values: dict[str, object] = {
        "part_number": "PN-BD",
        "work_order_demand_id": demand,
        "quantity": 2,
        "source": "MANAGEMENT",
        "device_event_id": event,
        **columns,
    }
    names = ", ".join([*values, "allocated_at"])
    placeholders = ", ".join([*(f":{name}" for name in values), "now()"])
    return _scalar_id(
        connection,
        f"INSERT INTO work_order_allocations ({names}) VALUES ({placeholders}) RETURNING id",
        **values,
    )


def test_head_is_the_beyond_demand_revision(correction_engine: Engine) -> None:
    with correction_engine.connect() as connection:
        assert _version(connection) == _CORRECTION_REVISION
    migration = _load_correction_migration()
    assert migration.revision == _CORRECTION_REVISION
    assert migration.down_revision == _DEVICES_REVISION
    assert len(_CORRECTION_REVISION) <= 32


def test_exceeds_demand_column_and_check(correction_engine: Engine) -> None:
    inspector = inspect(correction_engine)
    columns = {str(c["name"]): c for c in inspector.get_columns("work_order_allocations")}
    column = columns["exceeds_demand"]
    assert isinstance(column["type"], sa.Boolean)
    assert column["nullable"] is False
    assert str(column["default"]) == "false"
    checks = {
        str(check["name"]) for check in inspector.get_check_constraints("work_order_allocations")
    }
    assert _EXCEEDS_DEMAND_CHECK in checks
    indexed = {
        column_name
        for index in inspector.get_indexes("work_order_allocations")
        for column_name in index["column_names"]
    }
    assert "exceeds_demand" not in indexed


def test_correction_migration_literals_repeat_the_model_constants() -> None:
    migration = _load_correction_migration()
    assert migration._EXCEEDS_DEMAND_SQL == models.ALLOCATION_EXCEEDS_DEMAND_SQL
    assert migration._CHECK == _EXCEEDS_DEMAND_CHECK
    assert models.WorkOrderAllocation.__tablename__ == migration._TABLE
    assert migration._COLUMN == "exceeds_demand"


def test_correction_rows_are_refused_when_malformed(correction_connection: Connection) -> None:
    """BC-14: a row recorded ``exceeds_demand`` is Management, reasoned,
    User-recorded, never a reversal, never a station or Worker row."""
    connection = correction_connection
    user = _insert_user(connection, "correction")
    station = _insert_station(connection, "BD-ST")
    worker = _scalar_id(
        connection,
        "INSERT INTO workers (name, badge_barcode) VALUES ('Worker', 'BD-W1') RETURNING id",
    )
    demand = _insert_demand(connection)
    valid: dict[str, object] = {
        "exceeds_demand": True,
        "allocation_reason": "customer accepted overage",
        "actor_user_id": user,
        "is_manual_override": True,
    }
    original = _insert_allocation(connection, demand, "BD-OK", **valid)
    malformed: dict[str, dict[str, object]] = {
        "stockroom source": {"source": "STOCKROOM"},
        "no reason": {"allocation_reason": None},
        "a reversal": {"reverses_allocation_id": original},
        "a station": {"station_id": station},
        "a worker": {"station_id": station, "allocated_by_worker_id": worker},
        "no user": {"actor_user_id": None},
    }
    for label, change in malformed.items():
        _refused_by(
            connection,
            _EXCEEDS_DEMAND_CHECK,
            functools.partial(
                _insert_allocation, connection, demand, f"BD-{label}", **{**valid, **change}
            ),
        )
    # The same shapes without the flag are not this CHECK's concern.
    _insert_allocation(connection, demand, "BD-PLAIN", allocation_reason=None)


def _pre_correction_rows(connection: Connection) -> list[int]:
    """Representative rows written at 0030: a station allocation with a
    Worker, Management allocations with and without a User, a reversal."""
    user = _insert_user(connection, "precorrection")
    station = _insert_station(connection, "PRE-ST")
    worker = _scalar_id(
        connection,
        "INSERT INTO workers (name, badge_barcode) VALUES ('Worker', 'PRE-W1') RETURNING id",
    )
    demand = _insert_demand(connection)
    station_row = _insert_allocation(
        connection,
        demand,
        "PRE-1",
        source="STOCKROOM",
        station_id=station,
        allocated_by_worker_id=worker,
    )
    legacy_row = _insert_allocation(connection, demand, "PRE-2")
    actor_row = _insert_allocation(connection, demand, "PRE-3", actor_user_id=user)
    reversal_row = _insert_allocation(
        connection,
        demand,
        "PRE-4",
        reverses_allocation_id=legacy_row,
        allocation_reason="wrong line",
        actor_user_id=user,
        is_manual_override=True,
    )
    return [station_row, legacy_row, actor_row, reversal_row]


def test_existing_rows_cross_the_revision_unflagged(correction_head_database: URL) -> None:
    config = _alembic_config(correction_head_database)
    engine = create_engine(correction_head_database)
    try:
        command.downgrade(config, _DEVICES_REVISION)
        with engine.begin() as connection:
            ids = _pre_correction_rows(connection)
        command.upgrade(config, _CORRECTION_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _CORRECTION_REVISION
            flags: dict[int, bool] = {
                int(row.id): bool(row.exceeds_demand)
                for row in connection.execute(
                    sa.text(
                        "SELECT id, exceeds_demand FROM work_order_allocations WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            }
            assert flags == dict.fromkeys(ids, False)
            validated = connection.execute(
                sa.text("SELECT convalidated FROM pg_constraint WHERE conname = :name"),
                {"name": _EXCEEDS_DEMAND_CHECK},
            ).scalar_one()
            assert validated is True
        # A clean downgrade (no correction row) keeps every row intact.
        command.downgrade(config, _DEVICES_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _DEVICES_REVISION
            kept = connection.execute(
                sa.text("SELECT count(*) FROM work_order_allocations WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).scalar_one()
            assert kept == len(ids)
        columns = {str(c["name"]) for c in inspect(engine).get_columns("work_order_allocations")}
        assert "exceeds_demand" not in columns
        command.upgrade(config, _CORRECTION_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _CORRECTION_REVISION
    finally:
        engine.dispose()


def test_correction_downgrade_refuses_while_a_correction_exists(
    correction_head_database: URL,
) -> None:
    engine = create_engine(correction_head_database)
    try:
        with engine.begin() as connection:
            _insert_allocation(
                connection,
                _insert_demand(connection),
                "BD-REFUSE",
                exceeds_demand=True,
                allocation_reason="customer accepted overage",
                actor_user_id=_insert_user(connection, "refuse"),
                is_manual_override=True,
            )
        with pytest.raises(ProgrammingError) as raised:
            command.downgrade(_alembic_config(correction_head_database), _DEVICES_REVISION)
        assert "Beyond-demand allocation corrections exist; refusing downgrade" in str(
            raised.value.orig
        )
        with engine.connect() as connection:
            assert _version(connection) == _CORRECTION_REVISION
        columns = {str(c["name"]) for c in inspect(engine).get_columns("work_order_allocations")}
        assert "exceeds_demand" in columns
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 0032 — the AssignedRoute adjustment (Phase 14 slice 6)
# ---------------------------------------------------------------------------

_ADJUSTMENT_INDEX = "uq_audit_events_route_adjustment_device_event_id"
_STEP_REFERENCE_INDEX = "ix_part_movements_assigned_route_step_id"
_FORBID_UPDATE_TRIGGER = "trg_assigned_route_steps_forbid_update"
_ROUTE_TEMPLATE_DATABASE = "partflow_test_phase14_route_template"
_ADJUSTMENT_EVENT = "00000000-0000-0000-0000-00000000a0a0"


def _load_route_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase14_route_adjusted_migration", _ROUTE_MIGRATION_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def route_engine(admin_engine: Engine) -> Iterator[Engine]:
    """Temporary database migrated head → 0031 → head through real Alembic runs."""
    name = "partflow_test_phase14_route_schema"
    _create_temp_database(admin_engine, name)
    config = _alembic_config(_url(name))
    command.upgrade(config, "head")
    command.downgrade(config, _CORRECTION_REVISION)
    command.upgrade(config, "head")
    engine = create_engine(_url(name))
    yield engine
    engine.dispose()
    _drop_temp_database(admin_engine, name)


@pytest.fixture
def route_connection(route_engine: Engine) -> Iterator[Connection]:
    """Per-test connection at head whose transaction is always rolled back."""
    with route_engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


@pytest.fixture(scope="module")
def route_template(admin_engine: Engine) -> Iterator[str]:
    _create_temp_database(admin_engine, _ROUTE_TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_ROUTE_TEMPLATE_DATABASE)), "head")
    yield _ROUTE_TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _ROUTE_TEMPLATE_DATABASE)


@pytest.fixture
def route_head_database(admin_engine: Engine, route_template: str) -> Iterator[URL]:
    name = "partflow_test_phase14_route_downgrade"
    _create_temp_database(admin_engine, name, template=route_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


def _insert_route(connection: Connection, name: str = "ROUTE-ADJ") -> tuple[int, list[int]]:
    """One AssignedRoute with two steps in a fresh Area; returns (route, step ids)."""
    department = _scalar_id(
        connection, "INSERT INTO departments (name) VALUES (:name) RETURNING id", name=name
    )
    area = _scalar_id(
        connection,
        "INSERT INTO areas (department_id, name) VALUES (:department, :name) RETURNING id",
        department=department,
        name=name,
    )
    route = _scalar_id(connection, "INSERT INTO assigned_routes DEFAULT VALUES RETURNING id")
    steps = [
        _scalar_id(
            connection,
            "INSERT INTO assigned_route_steps (assigned_route_id, sequence, area_id)"
            " VALUES (:route, :sequence, :area) RETURNING id",
            route=route,
            sequence=sequence,
            area=area,
        )
        for sequence in (1, 2)
    ]
    return route, steps


def _insert_adjustment_audit(
    connection: Connection, entity_id: str, *, entity_type: str = "AssignedRoute"
) -> None:
    metadata = {"route_adjustment": {"device_event_id": _ADJUSTMENT_EVENT}}
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, metadata)"
            " VALUES (:event_type, :entity_type, :entity_id, now(), CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": "ROUTE_ADJUSTED" if entity_type == "AssignedRoute" else "UPDATED",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata": json.dumps(metadata),
        },
    )


def test_head_is_the_route_adjusted_revision(route_engine: Engine) -> None:
    with route_engine.connect() as connection:
        assert _version(connection) == _ROUTE_REVISION
    migration = _load_route_migration()
    assert migration.revision == _ROUTE_REVISION
    assert migration.down_revision == _CORRECTION_REVISION
    assert len(_ROUTE_REVISION) <= 32


def _model_check(name: str) -> str:
    for constraint in models.Base.metadata.tables["audit_events"].constraints:
        if isinstance(constraint, sa.CheckConstraint) and constraint.name == name:
            return str(constraint.sqltext)
    raise AssertionError(name)


def test_route_migration_literals_repeat_the_model_constants() -> None:
    migration = _load_route_migration()
    event_types = "event_type IN ({})".format(", ".join(f"'{v}'" for v in AuditEventType))
    entity_types = "entity_type IN ({})".format(", ".join(f"'{v}'" for v in AuditEntityType))
    route_event_types, route_entity_types = (
        migration._ROUTE_EVENT_TYPES,
        migration._ROUTE_ENTITY_TYPES,
    )
    assert route_event_types == event_types
    assert route_entity_types == entity_types
    assert route_event_types == _model_check("ck_audit_events_event_type")
    assert route_entity_types == _model_check("ck_audit_events_entity_type")
    # The downgrade restores exactly the previous vocabularies.
    assert migration._PREVIOUS_EVENT_TYPES == "event_type IN ('CREATED', 'UPDATED', 'DELETED')"
    assert migration._PREVIOUS_ENTITY_TYPES == _load_devices_migration()._DEVICE_ENTITY_TYPES
    assert migration._ADJUSTMENT_INDEX == _ADJUSTMENT_INDEX
    assert migration._STEP_REFERENCE_INDEX == _STEP_REFERENCE_INDEX
    assert migration._FORBID_UPDATE_TRIGGER == _FORBID_UPDATE_TRIGGER


def test_audit_vocabulary_is_widened(route_engine: Engine) -> None:
    with route_engine.connect() as connection:
        definitions = {
            str(name): str(definition)
            for name, definition in connection.execute(
                sa.text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname IN ('ck_audit_events_event_type',"
                    " 'ck_audit_events_entity_type')"
                )
            )
        }
    assert "'ROUTE_ADJUSTED'::text" in definitions["ck_audit_events_event_type"]
    assert "'AssignedRoute'::text" in definitions["ck_audit_events_entity_type"]
    assert "'ScanStationDevice'::text" in definitions["ck_audit_events_entity_type"]


def test_adjustment_index_stores_the_subscript_expression(route_engine: Engine) -> None:
    """UNIQUE, partial on AssignedRoute, in the JSONB SUBSCRIPT form the
    application emits (not the `->` operator form)."""
    with route_engine.connect() as connection:
        definition = connection.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": _ADJUSTMENT_INDEX},
        ).scalar_one()
    assert "CREATE UNIQUE INDEX" in definition
    assert "ON public.audit_events" in definition
    assert "metadata['route_adjustment'::text] ->> 'device_event_id'::text" in definition
    assert "WHERE (entity_type = 'AssignedRoute'::text)" in definition


def test_the_adjustment_lookup_uses_the_index(route_connection: Connection) -> None:
    """The planner matches the application's lookup to the stored expression."""
    lookup = sa.select(models.AuditEvent.id).where(
        models.AuditEvent.entity_type == "AssignedRoute",
        models.ROUTE_ADJUSTMENT_DEVICE_EVENT_ID == _ADJUSTMENT_EVENT,
    )
    sql = str(lookup.compile(route_connection, compile_kwargs={"literal_binds": True}))
    route_connection.execute(sa.text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        str(line) for line in route_connection.execute(sa.text(f"EXPLAIN {sql}")).scalars()
    )
    assert _ADJUSTMENT_INDEX in plan


def test_step_reference_index_exists(route_engine: Engine) -> None:
    indexes = {
        str(index["name"]): index for index in inspect(route_engine).get_indexes("part_movements")
    }
    assert indexes[_STEP_REFERENCE_INDEX]["column_names"] == ["assigned_route_step_id"]
    assert indexes[_STEP_REFERENCE_INDEX]["unique"] is False


def test_models_metadata_matches_the_migrated_schema(route_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with route_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def test_a_device_event_id_names_one_adjustment(route_connection: Connection) -> None:
    connection = route_connection
    _insert_adjustment_audit(connection, "1")
    # The same metadata shape on another entity is outside the partial index.
    _insert_adjustment_audit(connection, "2", entity_type="WorkOrderDemand")
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        _insert_adjustment_audit(connection, "3")
    savepoint.rollback()
    assert _ADJUSTMENT_INDEX in str(raised.value.orig)


def test_assigned_route_steps_are_never_updated(route_connection: Connection) -> None:
    connection = route_connection
    route, steps = _insert_route(connection)
    for statement in (
        "UPDATE assigned_route_steps SET instructions = 'x' WHERE id = :step",
        # Statement-level: even a zero-row UPDATE is refused.
        "UPDATE assigned_route_steps SET instructions = 'x' WHERE id = -1 AND id = :step",
    ):
        savepoint = connection.begin_nested()
        with pytest.raises(ProgrammingError) as raised:
            connection.execute(sa.text(statement), {"step": steps[0]})
        savepoint.rollback()
        assert "past steps are immutable" in str(raised.value.orig)
    # An unreferenced step may be deleted, and new steps inserted.
    connection.execute(
        sa.text("DELETE FROM assigned_route_steps WHERE id = :step"), {"step": steps[1]}
    )
    connection.execute(
        sa.text(
            "INSERT INTO assigned_route_steps (assigned_route_id, sequence, area_id)"
            " SELECT :route, 2, area_id FROM assigned_route_steps WHERE id = :step"
        ),
        {"route": route, "step": steps[0]},
    )
    count = connection.execute(
        sa.text("SELECT count(*) FROM assigned_route_steps WHERE assigned_route_id = :route"),
        {"route": route},
    ).scalar_one()
    assert count == 2


def test_clean_route_downgrade_restores_the_correction_boundary(
    route_head_database: URL,
) -> None:
    config = _alembic_config(route_head_database)
    engine = create_engine(route_head_database)
    try:
        command.downgrade(config, _CORRECTION_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _CORRECTION_REVISION
            checks = {
                str(name): str(definition)
                for name, definition in connection.execute(
                    sa.text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                        " WHERE conname IN ('ck_audit_events_event_type',"
                        " 'ck_audit_events_entity_type')"
                    )
                )
            }
            triggers = connection.execute(
                sa.text("SELECT count(*) FROM pg_trigger WHERE tgname = :name"),
                {"name": _FORBID_UPDATE_TRIGGER},
            ).scalar_one()
        assert "ROUTE_ADJUSTED" not in checks["ck_audit_events_event_type"]
        assert "AssignedRoute" not in checks["ck_audit_events_entity_type"]
        assert "ScanStationDevice" in checks["ck_audit_events_entity_type"]
        assert triggers == 0
        inspector = inspect(engine)
        indexes = {
            str(index["name"])
            for table in ("audit_events", "part_movements")
            for index in inspector.get_indexes(table)
        }
        assert not indexes & {_ADJUSTMENT_INDEX, _STEP_REFERENCE_INDEX}
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _version(connection) == _ROUTE_REVISION
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("event_type", "entity_type"), [("ROUTE_ADJUSTED", "Area"), ("UPDATED", "AssignedRoute")]
)
def test_route_downgrade_refuses_while_an_adjustment_is_recorded(
    route_head_database: URL, event_type: str, entity_type: str
) -> None:
    """Either half of the vocabulary blocks the downgrade; the row is kept."""
    engine = create_engine(route_head_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
                    " VALUES (:event_type, :entity_type, '1', now())"
                ),
                {"event_type": event_type, "entity_type": entity_type},
            )
        with pytest.raises(ProgrammingError) as raised:
            command.downgrade(_alembic_config(route_head_database), _CORRECTION_REVISION)
        assert "Assigned Route adjustments are recorded; refusing downgrade" in str(
            raised.value.orig
        )
        with engine.connect() as connection:
            assert _version(connection) == _ROUTE_REVISION
            kept = connection.execute(
                sa.text(
                    "SELECT count(*) FROM audit_events"
                    " WHERE entity_type = 'AssignedRoute' OR event_type = 'ROUTE_ADJUSTED'"
                )
            ).scalar_one()
            assert kept == 1
    finally:
        engine.dispose()
