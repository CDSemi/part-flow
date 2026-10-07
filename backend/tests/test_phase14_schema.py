"""Integration tests for the Phase 14 migrations.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0029_phase14_sign_in` adds (IMPLEMENTATION_ROADMAP
Phase 14; PROJECT_PROFILE §7 User; owner decisions OD-P1–OD-P5,
OD-P17):

- exact head boundary: `0029_phase14_sign_in` is the single head
  (moved here from the Phase 13 schema test, which is now pinned to
  `0028_phase13_users_roles`);
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
- models↔migration metadata parity at head;
- the downgrade on a clean head restores the previous boundary and
  re-upgrades; it refuses — keeping the version at head — for each of
  its triggers separately (a credential, a session, an `actor_user_id`
  on each of the three tables, a `sign-in` audit row, each non-default
  policy value).
"""

import importlib.util
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
from app.domain.enums import UserSessionEndReason
from app.infrastructure import models

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0028_phase13_users_roles"
_SIGN_IN_REVISION = "0029_phase14_sign_in"
_HEAD_REVISION = _SIGN_IN_REVISION
_MIGRATION_FILE = _BACKEND_DIR / "alembic" / "versions" / "20261006_0029_phase14_sign_in.py"
_TEMPLATE_DATABASE = "partflow_test_phase14_template"
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
    """Temporary database migrated head → previous → head through real Alembic runs."""
    name = "partflow_test_phase14_schema"
    _create_temp_database(admin_engine, name)
    config = _alembic_config(_url(name))
    command.upgrade(config, "head")
    command.downgrade(config, _PREVIOUS_REVISION)
    command.upgrade(config, "head")
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
    """A clean database at head, cloned (CREATE DATABASE … TEMPLATE) per case."""
    _create_temp_database(admin_engine, _TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_TEMPLATE_DATABASE)), "head")
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


def test_head_is_the_phase14_revision(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        assert _version(connection) == _HEAD_REVISION
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


def test_models_metadata_matches_the_migrated_schema(migrated_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


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
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
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
            assert _version(connection) == _HEAD_REVISION
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
