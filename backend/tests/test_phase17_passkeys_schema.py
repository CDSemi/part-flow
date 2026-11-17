"""Integration tests for migration ``0038_phase17_passkeys`` (Phase 17).

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then verifies
the schema of passkeys (owner decision of 2026-11-13; IMPLEMENTATION_ROADMAP
Phase 17 item 8):

- exact head boundary: ``0038_phase17_passkeys`` is the single head, and
  the models match the schema migrated to it;
- the migration's literals equal the model constants;
- a fresh install holds no passkey and no pending registration; every
  existing challenge is a second step; the CHECKs refuse an empty or
  over-long credential id, empty credential data, an empty host, a name
  of 0 or 81 characters, a negative counter, a registration challenge of
  the wrong length, an unknown purpose, a second step without a User, a
  passkey challenge of the wrong length or without its host and a passkey
  sign-in without its challenge, while UNIQUE refuses a credential id
  twice; a passkey sign-in has no User until a passkey answers;
- the downgrade restores 0037 while no passkey data exists, and refuses
  — the revision staying at 0038 — for each trigger separately: a
  passkey, a pending registration, a passkey sign-in and a passkey request
  on a second step.
"""

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.domain.enums import SignInChallengePurpose
from app.infrastructure import models

# Schema assertions: owner connections in the application-role test mode
# (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0037_phase17_two_step"
_HEAD_REVISION = "0038_phase17_passkeys"
_MIGRATION_FILE = _BACKEND_DIR / "alembic" / "versions" / "20261117_0038_phase17_passkeys.py"
_HEAD_TEMPLATE_DATABASE = "partflow_test_phase17_passkeys_template"
_TABLES = ("user_passkey_setups", "user_passkeys")
_CHALLENGE = "'\\x" + "ab" * 32 + "'::bytea"
_TOKEN = "'\\x" + "cd" * 32 + "'::bytea"
_ID = "'\\x" + "ef" * 16 + "'::bytea"
_DATA = "'\\x" + "01" * 64 + "'::bytea"


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
    spec = importlib.util.spec_from_file_location("phase17_passkeys", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _tables(connection: Connection) -> list[str]:
    return [
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables WHERE table_name IN"
                " ('user_passkeys', 'user_passkey_setups') ORDER BY 1"
            )
        ).scalars()
    ]


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


def _passkey(id_bytes: str = _ID, name: str = "'Laptop'") -> str:
    return (
        "INSERT INTO user_passkeys (user_id, credential_id, credential_data, rp_id, name)"
        f" VALUES (:u, {id_bytes}, {_DATA}, 'pf.example.com', {name})"
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
    name = "partflow_test_phase17_passkeys_case"
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


def test_alembic_has_a_single_head() -> None:
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    assert ScriptDirectory.from_config(config).get_heads() == [_HEAD_REVISION]


def test_models_metadata_matches_the_migrated_schema(head_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with head_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def test_the_migration_literals_match_the_model() -> None:
    migration = _load_migration()
    assert migration.revision == _HEAD_REVISION
    assert migration.down_revision == _PREVIOUS_REVISION
    assert (models.UserPasskey.__tablename__, models.UserPasskeySetup.__tablename__) == (
        migration._PASSKEYS,
        migration._SETUPS,
    )
    assert [
        migration._CREDENTIAL_ID_SQL,
        migration._CREDENTIAL_DATA_SQL,
        migration._RP_ID_SQL,
        migration._NAME_SQL,
        migration._SIGN_COUNT_SQL,
        migration._SETUP_CHALLENGE_SQL,
        migration._PURPOSE_SQL,
        migration._USER_SQL,
        migration._PASSKEY_CHALLENGE_SQL,
        migration._PASSKEY_RP_ID_SQL,
        migration._PASSKEY_SHAPE_SQL,
    ] == [
        models.PASSKEY_CREDENTIAL_ID_SQL,
        models.PASSKEY_CREDENTIAL_DATA_SQL,
        models.PASSKEY_RP_ID_SQL,
        models.PASSKEY_NAME_SQL,
        models.PASSKEY_SIGN_COUNT_SQL,
        models.PASSKEY_SETUP_CHALLENGE_SQL,
        models.CHALLENGE_PURPOSE_SQL,
        models.CHALLENGE_USER_SQL,
        models.CHALLENGE_PASSKEY_SQL,
        models.CHALLENGE_PASSKEY_RP_ID_SQL,
        models.CHALLENGE_PASSKEY_SHAPE_SQL,
    ]
    assert tuple(SignInChallengePurpose) == migration._PURPOSES
    assert models.PASSKEY_NAME_MAX_LENGTH == 80


# ---------------------------------------------------------------------------
# A fresh install and the checks
# ---------------------------------------------------------------------------


def test_a_fresh_install_holds_no_passkey(head_engine: Engine) -> None:
    with head_engine.connect() as connection:
        assert _tables(connection) == sorted(_TABLES)
        for table in _TABLES:
            assert connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    columns = {
        str(c["name"]): c for c in inspect(head_engine).get_columns("user_sign_in_challenges")
    }
    assert columns["user_id"]["nullable"] is True
    assert columns["purpose"]["nullable"] is False


def test_an_existing_challenge_is_a_second_step(head_engine: Engine) -> None:
    with head_engine.begin() as connection:
        user_id = _user(connection)
        connection.execute(
            sa.text(
                f"INSERT INTO user_sign_in_challenges (user_id, token_digest) VALUES (:u, {_TOKEN})"
            ),
            {"u": user_id},
        )
        assert (
            connection.execute(sa.text("SELECT purpose FROM user_sign_in_challenges")).scalar_one()
            == "SECOND_STEP"
        )


@pytest.mark.parametrize(
    "statement",
    [
        _passkey(id_bytes="''::bytea"),
        _passkey(id_bytes="'\\x" + "01" * 1024 + "'::bytea"),
        "INSERT INTO user_passkeys (user_id, credential_id, credential_data, rp_id, name)"
        f" VALUES (:u, {_ID}, ''::bytea, 'pf.example.com', 'Laptop')",
        "INSERT INTO user_passkeys (user_id, credential_id, credential_data, rp_id, name)"
        f" VALUES (:u, {_ID}, {_DATA}, '', 'Laptop')",
        _passkey(name="''"),
        _passkey(name="'" + "n" * 81 + "'"),
        "INSERT INTO user_passkeys (user_id, credential_id, credential_data, rp_id, name,"
        f" sign_count) VALUES (:u, {_ID}, {_DATA}, 'pf.example.com', 'Laptop', -1)",
        "INSERT INTO user_passkey_setups (user_id, challenge, rp_id)"
        " VALUES (:u, '\\x00'::bytea, 'pf.example.com')",
        "INSERT INTO user_passkey_setups (user_id, challenge, rp_id)"
        f" VALUES (:u, {_CHALLENGE}, '')",
        "INSERT INTO user_sign_in_challenges (user_id, token_digest, purpose)"
        f" VALUES (:u, {_TOKEN}, 'PASSWORDLESS')",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest) VALUES (NULL, {_TOKEN})",
        "INSERT INTO user_sign_in_challenges (user_id, token_digest, passkey_challenge,"
        f" passkey_rp_id) VALUES (:u, {_TOKEN}, '\\x00'::bytea, 'pf.example.com')",
        "INSERT INTO user_sign_in_challenges (user_id, token_digest, passkey_challenge)"
        f" VALUES (:u, {_TOKEN}, {_CHALLENGE})",
        "INSERT INTO user_sign_in_challenges (user_id, token_digest, purpose)"
        f" VALUES (NULL, {_TOKEN}, 'PASSKEY_SIGN_IN')",
    ],
)
def test_the_checks_refuse_values_outside_the_rules(head_engine: Engine, statement: str) -> None:
    with head_engine.begin() as connection:
        user_id = _user(connection)
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(statement), {"u": user_id} if ":u" in statement else {})


def test_a_credential_id_is_unique_and_a_passkey_sign_in_has_no_user(
    head_engine: Engine,
) -> None:
    with head_engine.begin() as connection:
        user_id = _user(connection)
        connection.execute(sa.text(_passkey()), {"u": user_id})
        connection.execute(
            sa.text(
                "INSERT INTO user_sign_in_challenges (user_id, token_digest, purpose,"
                f" passkey_challenge, passkey_rp_id) VALUES (NULL, {_TOKEN}, 'PASSKEY_SIGN_IN',"
                f" {_CHALLENGE}, 'pf.example.com')"
            )
        )
    refused = pytest.raises(IntegrityError, match="uq_user_passkeys_credential_id")
    with refused, head_engine.begin() as connection:
        connection.execute(sa.text(_passkey(name="'Phone'")), {"u": user_id})


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def test_the_downgrade_restores_the_previous_schema(
    head_database: URL, head_engine: Engine
) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    columns = {
        str(c["name"]): c for c in inspect(head_engine).get_columns("user_sign_in_challenges")
    }
    with head_engine.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
        assert _tables(connection) == []
    assert columns["user_id"]["nullable"] is False
    assert not {"purpose", "passkey_challenge", "passkey_rp_id"} & set(columns)
    command.upgrade(_alembic_config(head_database), "head")
    with head_engine.connect() as connection:
        assert _version(connection) == _HEAD_REVISION
        assert _tables(connection) == sorted(_TABLES)


_DOWNGRADE_TRIGGERS = {
    "passkey": _passkey(),
    "registration": (
        "INSERT INTO user_passkey_setups (user_id, challenge, rp_id)"
        f" VALUES (:u, {_CHALLENGE}, 'pf.example.com')"
    ),
    "passkey-sign-in": (
        "INSERT INTO user_sign_in_challenges (user_id, token_digest, purpose, passkey_challenge,"
        f" passkey_rp_id) VALUES (NULL, {_TOKEN}, 'PASSKEY_SIGN_IN', {_CHALLENGE},"
        " 'pf.example.com')"
    ),
    "passkey-second-step": (
        "INSERT INTO user_sign_in_challenges (user_id, token_digest, passkey_challenge,"
        f" passkey_rp_id) VALUES (:u, {_TOKEN}, {_CHALLENGE}, 'pf.example.com')"
    ),
}


@pytest.mark.parametrize("trigger", list(_DOWNGRADE_TRIGGERS))
def test_the_downgrade_refuses_while_passkey_data_exists(
    head_database: URL, head_engine: Engine, trigger: str
) -> None:
    statement = _DOWNGRADE_TRIGGERS[trigger]
    with head_engine.begin() as connection:
        user_id = _user(connection)
        connection.execute(sa.text(statement), {"u": user_id} if ":u" in statement else {})
    with pytest.raises(DBAPIError, match="Passkey data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _HEAD_REVISION
        assert _tables(connection) == sorted(_TABLES)
