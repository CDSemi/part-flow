"""Integration tests for migration ``0039_security_settings`` (Administration → Security).

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then verifies
the schema of the password rules and Remember me (owner request of
2026-11-17):

- the cases run against this revision inside the current chain; the head
  boundary and the models parity moved to
  ``tests/test_history_archive_schema.py`` with ``0040_phase16_history_archive``;
- the migration's literals equal the model constants and the section ids;
- a fresh install holds today's behavior — 6 to 256 characters, no
  composition rule, Remember me not offered (30 days when it is) — and every
  existing User session is unremembered; the CHECKs refuse a length outside
  4-256, a minimum above the maximum and a Remember me time outside 1-365
  days;
- the downgrade restores 0038 while nothing differs from the defaults, and
  refuses — the revision staying at 0039 — for each trigger separately: a
  changed policy value, a remembered session and an audit row of either
  section.
"""

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.application import policies
from app.infrastructure import models, schema_revision

# Schema assertions: owner connections in the application-role test mode
# (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0038_phase17_passkeys"
_HEAD_REVISION = "0039_security_settings"
_CODE_HEAD = schema_revision.code_head()
_MIGRATION_FILE = _BACKEND_DIR / "alembic" / "versions" / "20261117_0039_security_settings.py"
_HEAD_TEMPLATE_DATABASE = "partflow_test_security_settings_template"
_POLICY_COLUMNS = (
    "password_min_length",
    "password_max_length",
    "password_require_digit",
    "password_require_special",
    "password_require_uppercase",
    "password_require_lowercase",
    "remember_me_offered",
    "remember_me_expires",
    "remember_me_days",
)
_DEFAULTS = {
    "password_min_length": 6,
    "password_max_length": 256,
    "password_require_digit": False,
    "password_require_special": False,
    "password_require_uppercase": False,
    "password_require_lowercase": False,
    "remember_me_offered": False,
    "remember_me_expires": True,
    "remember_me_days": 30,
}
_TOKEN = "'\\x" + "cd" * 32 + "'::bytea"


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
    spec = importlib.util.spec_from_file_location("security_settings", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _columns(engine: Engine, table: str) -> set[str]:
    return {str(column["name"]) for column in inspect(engine).get_columns(table)}


def _user(connection: Connection) -> int:
    role_id = connection.execute(sa.text("SELECT min(id) FROM roles")).scalar_one()
    return int(
        connection.execute(
            sa.text(
                "INSERT INTO users (login_name, display_name, role_id)"
                " VALUES ('ann', 'Ann', :role_id) RETURNING id"
            ),
            {"role_id": role_id},
        ).scalar_one()
    )


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def head_template(admin_engine: Engine) -> Iterator[str]:
    """A fresh install migrated to head, cloned (CREATE DATABASE … TEMPLATE) per case."""
    _create_temp_database(admin_engine, _HEAD_TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_HEAD_TEMPLATE_DATABASE)), "head")
    yield _HEAD_TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _HEAD_TEMPLATE_DATABASE)


@pytest.fixture
def head_database(admin_engine: Engine, head_template: str) -> Iterator[URL]:
    name = "partflow_test_security_settings_case"
    _create_temp_database(admin_engine, name, template=head_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


@pytest.fixture
def head_engine(head_database: URL) -> Iterator[Engine]:
    engine = create_engine(head_database)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Head and literals
# ---------------------------------------------------------------------------


def test_the_migration_literals_match_the_model() -> None:
    migration = _load_migration()
    assert migration.revision == _HEAD_REVISION
    assert migration.down_revision == _PREVIOUS_REVISION
    assert models.ApplicationPolicy.__tablename__ == migration._POLICY
    assert models.UserSession.__tablename__ == migration._SESSIONS
    assert migration._SECTIONS == (policies.PASSWORD_RULES_SECTION, policies.REMEMBER_ME_SECTION)
    assert {
        column: (minimum, maximum)
        for column, _default, minimum, maximum in migration._RANGE_COLUMNS
    } == models.SECURITY_POLICY_RANGES
    assert migration._LENGTH_ORDER_SQL == models.POLICY_PASSWORD_LENGTH_ORDER_SQL
    defaults = {column: default for column, default, *_ in migration._RANGE_COLUMNS} | dict(
        migration._SWITCH_COLUMNS
    )
    assert defaults == _DEFAULTS


# ---------------------------------------------------------------------------
# A fresh install and the checks
# ---------------------------------------------------------------------------


def test_a_fresh_install_holds_todays_behavior(head_engine: Engine) -> None:
    with head_engine.begin() as connection:
        row = (
            connection.execute(
                sa.text(f"SELECT {', '.join(_POLICY_COLUMNS)} FROM application_policy")
            )
            .mappings()
            .one()
        )
        assert dict(row) == _DEFAULTS
        user_id = _user(connection)
        connection.execute(
            sa.text(f"INSERT INTO user_sessions (user_id, token_digest) VALUES (:u, {_TOKEN})"),
            {"u": user_id},
        )
        assert (
            connection.execute(sa.text("SELECT remembered FROM user_sessions")).scalar_one()
            is False
        )


@pytest.mark.parametrize(
    "assignment",
    [
        "password_min_length = 3",
        "password_min_length = 257",
        "password_max_length = 3",
        "password_max_length = 257",
        "password_min_length = 20, password_max_length = 19",
        "remember_me_days = 0",
        "remember_me_days = 366",
        "password_require_digit = NULL",
        "remember_me_offered = NULL",
    ],
)
def test_the_checks_refuse_values_outside_the_rules(head_engine: Engine, assignment: str) -> None:
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignment}"))


@pytest.mark.parametrize(
    "assignment",
    [
        "password_min_length = 4, password_max_length = 4",
        "password_min_length = 256",
        "remember_me_days = 1",
        "remember_me_days = 365",
    ],
)
def test_the_checks_admit_the_bounds(head_engine: Engine, assignment: str) -> None:
    with head_engine.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignment}"))


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def test_the_downgrade_restores_the_previous_schema(
    head_database: URL, head_engine: Engine
) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
    assert not set(_POLICY_COLUMNS) & _columns(head_engine, "application_policy")
    assert "remembered" not in _columns(head_engine, "user_sessions")
    command.upgrade(_alembic_config(head_database), "head")
    with head_engine.connect() as connection:
        assert _version(connection) == _CODE_HEAD
    assert set(_POLICY_COLUMNS) <= _columns(head_engine, "application_policy")


_DOWNGRADE_TRIGGERS = {
    "minimum-length": "UPDATE application_policy SET password_min_length = 8",
    "maximum-length": "UPDATE application_policy SET password_max_length = 64",
    "digit": "UPDATE application_policy SET password_require_digit = true",
    "special": "UPDATE application_policy SET password_require_special = true",
    "uppercase": "UPDATE application_policy SET password_require_uppercase = true",
    "lowercase": "UPDATE application_policy SET password_require_lowercase = true",
    "offered": "UPDATE application_policy SET remember_me_offered = true",
    "until-signed-out": "UPDATE application_policy SET remember_me_expires = false",
    "days": "UPDATE application_policy SET remember_me_days = 90",
    "remembered-session": (
        f"INSERT INTO user_sessions (user_id, token_digest, remembered) VALUES (:u, {_TOKEN}, true)"
    ),
    "password-rules-audit": (
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
        " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy', 'password-rules', now(),"
        " '{}'::jsonb, '{}'::jsonb)"
    ),
    "remember-me-audit": (
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
        " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy', 'remember-me', now(),"
        " '{}'::jsonb, '{}'::jsonb)"
    ),
}


@pytest.mark.parametrize("trigger", list(_DOWNGRADE_TRIGGERS))
def test_the_downgrade_refuses_while_security_data_exists(
    head_database: URL, head_engine: Engine, trigger: str
) -> None:
    statement = _DOWNGRADE_TRIGGERS[trigger]
    with head_engine.begin() as connection:
        user_id = _user(connection)
        connection.execute(sa.text(statement), {"u": user_id} if ":u" in statement else {})
    with pytest.raises(DBAPIError, match="Security settings data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _CODE_HEAD
    assert set(_POLICY_COLUMNS) <= _columns(head_engine, "application_policy")
