"""Integration tests for migration ``0036_phase17_outgoing_mail`` (Phase 17).

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then verifies
what the email address of a User and the outgoing mail server of
Administration → Settings add (owner decision of 2026-11-13;
IMPLEMENTATION_ROADMAP Phase 17 item 8):

- exact head boundary: ``0036_phase17_outgoing_mail`` is the single head,
  and the models match the schema migrated to it;
- the migration's literals equal the model constants and the section id;
- a fresh install stores no address, no mail server (port 587, STARTTLS)
  and no mail server password, and the CHECKs refuse every value outside
  the rules while a canonical address, a whole mail server and one password
  row are accepted;
- an upgrade from 0035 keeps every User and every other policy section;
- the downgrade drops the columns and the password table while nothing was
  written, and refuses once an address, a mail server, a password, a port
  or security other than the defaults, or an audit row of the
  ``mail-server`` section exists, the revision staying at 0036.
"""

import datetime
import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.application import policies
from app.domain.enums import MailSecurity, ThemePreference
from app.infrastructure import models

# Schema assertions: owner connections in the application-role test mode
# (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0035_phase16_backup_policy"
_HEAD_REVISION = "0036_phase17_outgoing_mail"
_MIGRATION_FILE = _BACKEND_DIR / "alembic" / "versions" / "20261116_0036_phase17_outgoing_mail.py"
_HEAD_TEMPLATE_DATABASE = "partflow_test_phase17_mail_template"
_DEFAULTS = {
    "mail_host": None,
    "mail_port": 587,
    "mail_security": "STARTTLS",
    "mail_username": None,
    "mail_sender": None,
}
_WHOLE_SERVER = (
    "UPDATE application_policy SET mail_host = 'smtp.example.com', mail_port = 465,"
    " mail_security = 'TLS', mail_username = 'partflow', mail_sender = 'PartFlow@example.com'"
)
_PASSWORD_ROW = "INSERT INTO mail_server_credentials (id, password) VALUES (1, 'secret')"


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
    spec = importlib.util.spec_from_file_location("phase17_outgoing_mail", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _policy_row(connection: Connection) -> dict[str, Any]:
    return dict(connection.execute(sa.text("SELECT * FROM application_policy")).one()._mapping)


def _mail_columns(connection: Connection) -> list[str]:
    """The columns 0036 adds, the password table's included."""
    return [
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT table_name || '.' || column_name FROM information_schema.columns"
                " WHERE (table_name = 'application_policy' AND column_name LIKE 'mail\\_%')"
                " OR (table_name = 'users' AND column_name = 'email')"
                " OR table_name = 'mail_server_credentials'"
                " ORDER BY 1"
            )
        ).scalars()
    ]


def _add_user(connection: Connection, login: str, email: str | None = None) -> None:
    role_id = connection.execute(sa.text("SELECT min(id) FROM roles")).scalar_one()
    connection.execute(
        sa.text(
            "INSERT INTO users (login_name, display_name, role_id, email)"
            " VALUES (:login, :login, :role_id, :email)"
        ),
        {"login": login, "role_id": role_id, "email": email},
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
    name = "partflow_test_phase17_mail_case"
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
    assert migration._SECTION == policies.MAIL_SERVER_SECTION
    assert migration._USER_EMAIL_CHECK == ("ck_users_email_canonical", models.USER_EMAIL_SQL)
    assert [column for column, _, _ in migration._POLICY_COLUMNS] == list(_DEFAULTS)
    assert [default for _, _, default in migration._POLICY_COLUMNS] == [
        None,
        str(_DEFAULTS["mail_port"]),
        f"'{_DEFAULTS['mail_security']}'",
        None,
        None,
    ]
    assert [sql for _, sql in migration._POLICY_CHECKS] == [
        models.POLICY_MAIL_HOST_SQL,
        models.POLICY_MAIL_PORT_SQL,
        models.POLICY_MAIL_SECURITY_SQL,
        models.POLICY_MAIL_USERNAME_SQL,
        models.POLICY_MAIL_SENDER_SQL,
        models.POLICY_MAIL_SERVER_SHAPE_SQL,
    ]
    assert models.MailServerCredential.__tablename__ == migration._CREDENTIALS
    assert migration._PASSWORD_CHECK == (
        "ck_mail_server_credentials_password_length",
        models.MAIL_SERVER_PASSWORD_SQL,
    )
    assert (
        f"mail_port BETWEEN {models.MAIL_PORT_MIN} AND {models.MAIL_PORT_MAX}"
    ) == models.POLICY_MAIL_PORT_SQL
    assert (
        "mail_security IN (" + ", ".join(f"'{value}'" for value in MailSecurity) + ")"
    ) == models.POLICY_MAIL_SECURITY_SQL
    assert f"BETWEEN 1 AND {models.MAIL_USERNAME_MAX_LENGTH}" in models.POLICY_MAIL_USERNAME_SQL
    assert f"BETWEEN 1 AND {models.MAIL_PASSWORD_MAX_LENGTH}" in models.MAIL_SERVER_PASSWORD_SQL
    assert f"<= {models.MAIL_HOST_MAX_LENGTH}" in models.POLICY_MAIL_HOST_SQL


# ---------------------------------------------------------------------------
# A fresh install and the checks
# ---------------------------------------------------------------------------


def test_a_fresh_install_stores_no_address_and_no_mail_server(head_engine: Engine) -> None:
    with head_engine.connect() as connection:
        row = _policy_row(connection)
        emails = connection.execute(sa.text("SELECT count(email) FROM users")).scalar_one()
        passwords = connection.execute(
            sa.text("SELECT count(*) FROM mail_server_credentials")
        ).scalar_one()
    assert {key: row[key] for key in _DEFAULTS} == _DEFAULTS
    assert "mail_password" not in row
    assert (emails, passwords) == (0, 0)


@pytest.mark.parametrize(
    "email",
    ["Ann@example.com", "a.b+c@mail.example.co", "x@xn--bcher-kva.example", "o'neil@ex-ample.org"],
)
def test_the_user_check_accepts_a_canonical_address(head_engine: Engine, email: str) -> None:
    with head_engine.begin() as connection:
        _add_user(connection, "ann", email)


@pytest.mark.parametrize(
    "email",
    [
        "",
        "ann",
        "ann@example",
        "ann@Example.com",
        "ann@@example.com",
        "an n@example.com",
        "ann@exa_mple.com",
        "ännchen@example.com",
        "a" * 65 + "@example.com",
        "ann@" + "a" * 250 + ".com",
    ],
)
def test_the_user_check_refuses_a_non_canonical_address(head_engine: Engine, email: str) -> None:
    with (
        pytest.raises(IntegrityError, match="ck_users_email_canonical"),
        head_engine.begin() as (connection),
    ):
        _add_user(connection, "ann", email)


def test_the_policy_checks_accept_a_whole_mail_server(head_engine: Engine) -> None:
    with head_engine.begin() as connection:
        connection.execute(sa.text(_WHOLE_SERVER))
        connection.execute(sa.text(_PASSWORD_ROW))
        connection.execute(
            sa.text(
                "UPDATE application_policy SET mail_security = 'NONE', mail_port = 25,"
                " mail_username = NULL"
            )
        )
        connection.execute(sa.text("UPDATE application_policy SET mail_host = '192.168.1.20'"))
        connection.execute(sa.text("UPDATE application_policy SET mail_host = 'fd00::25'"))


@pytest.mark.parametrize(
    "assignment",
    [
        "mail_host = 'Smtp.example.com'",
        "mail_host = 'smtp example.com'",
        "mail_host = ''",
        "mail_port = 0",
        "mail_port = 65536",
        "mail_port = NULL",
        "mail_security = 'SSL'",
        "mail_security = NULL",
        "mail_username = ''",
        "mail_sender = 'PartFlow'",
        "mail_sender = NULL",
        "mail_host = NULL",
        "mail_host = NULL, mail_sender = NULL",
        "mail_security = 'NONE'",
    ],
)
def test_the_policy_checks_refuse_a_partial_or_invalid_server(
    head_engine: Engine, assignment: str
) -> None:
    with head_engine.begin() as connection:
        connection.execute(sa.text(_WHOLE_SERVER))
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignment}"))
    with head_engine.connect() as connection:
        assert _policy_row(connection)["mail_host"] == "smtp.example.com"


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO mail_server_credentials (id, password) VALUES (2, 'secret')",
        "INSERT INTO mail_server_credentials (id, password) VALUES (1, '')",
        "INSERT INTO mail_server_credentials (id, password) VALUES (1, NULL)",
        "INSERT INTO mail_server_credentials (id, password) VALUES (1, repeat('x', 1025))",
    ],
    ids=["second-row", "empty", "null", "too-long"],
)
def test_the_password_table_holds_one_password_of_1_to_1024_characters(
    head_engine: Engine, statement: str
) -> None:
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(statement))
    with head_engine.begin() as connection:
        connection.execute(sa.text(_PASSWORD_ROW))
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(_PASSWORD_ROW))


def test_the_user_model_round_trips_every_column(head_engine: Engine) -> None:
    """S-9 at the head: the ORM writes and reads back a User, theme and email included."""
    from sqlalchemy.orm import Session

    stamp = datetime.datetime(2026, 10, 6, 8, 30, tzinfo=datetime.UTC)
    with head_engine.connect() as connection:
        role_id = connection.execute(
            sa.text("SELECT id FROM roles WHERE name = 'Operator'")
        ).scalar_one()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            user = models.User(
                login_name="roundtrip",
                display_name="Round Trip",
                role_id=role_id,
                avatar_image=b"\x89PNG\r\n\x1a\n",
                avatar_image_type="image/png",
                avatar_image_updated_at=stamp,
                theme_preference=ThemePreference.LIGHT,
                email="Round.Trip@example.com",
                is_active=False,
            )
            session.add(user)
            session.flush()
            user_id = user.id
            session.expunge_all()
            loaded = session.get(models.User, user_id)
            assert loaded is not None
            assert (
                loaded.login_name,
                loaded.display_name,
                loaded.role_id,
                loaded.avatar_image,
                loaded.avatar_image_type,
                loaded.avatar_image_updated_at,
                loaded.theme_preference,
                loaded.email,
                loaded.is_active,
            ) == (
                "roundtrip",
                "Round Trip",
                role_id,
                b"\x89PNG\r\n\x1a\n",
                "image/png",
                stamp,
                "LIGHT",
                "Round.Trip@example.com",
                False,
            )
            assert loaded.created_at is not None and loaded.updated_at is not None
            loaded.theme_preference = None
            loaded.email = None
            session.flush()
            session.expire_all()
            reloaded = session.get(models.User, user_id)
            assert reloaded is not None
            assert (reloaded.theme_preference, reloaded.email) == (None, None)


def test_a_sender_without_a_host_is_refused(head_engine: Engine) -> None:
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE application_policy SET mail_sender = 'partflow@example.com'")
        )


# ---------------------------------------------------------------------------
# Upgrade from 0035 and the downgrade
# ---------------------------------------------------------------------------


def test_the_upgrade_keeps_users_and_every_other_section(admin_engine: Engine) -> None:
    name = "partflow_test_phase17_mail_upgrade"
    _create_temp_database(admin_engine, name)
    engine = create_engine(_url(name))
    try:
        command.upgrade(_alembic_config(_url(name)), _PREVIOUS_REVISION)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE application_policy SET due_soon_min_days = 3,"
                    " backup_keep_daily = 14, retention_period_months = 24"
                )
            )
            role_id = connection.execute(sa.text("SELECT min(id) FROM roles")).scalar_one()
            connection.execute(
                sa.text(
                    "INSERT INTO users (login_name, display_name, role_id)"
                    " VALUES ('ann', 'Ann', :role_id)"
                ),
                {"role_id": role_id},
            )
            before = _policy_row(connection)
        command.upgrade(_alembic_config(_url(name)), "head")
        with engine.connect() as connection:
            assert _version(connection) == _HEAD_REVISION
            after = _policy_row(connection)
            user = connection.execute(
                sa.text("SELECT login_name, email FROM users WHERE login_name = 'ann'")
            ).one()
        assert {key: after[key] for key in _DEFAULTS} == _DEFAULTS
        assert {key: after[key] for key in before} == before
        assert tuple(user) == ("ann", None)
    finally:
        engine.dispose()
        _drop_temp_database(admin_engine, name)


def test_the_downgrade_drops_untouched_columns(head_database: URL, head_engine: Engine) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
        assert _mail_columns(connection) == []
    command.upgrade(_alembic_config(head_database), "head")
    with head_engine.connect() as connection:
        row = _policy_row(connection)
    assert {key: row[key] for key in _DEFAULTS} == _DEFAULTS


@pytest.mark.parametrize(
    "written",
    [
        "INSERT_USER",
        _WHOLE_SERVER,
        _PASSWORD_ROW,
        "UPDATE application_policy SET mail_port = 25",
        "UPDATE application_policy SET mail_security = 'TLS'",
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
        " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy', 'mail-server', now(),"
        " '{}'::jsonb, '{}'::jsonb)",
    ],
    ids=["user-email", "mail-server", "password", "port", "security", "audit-row"],
)
def test_the_downgrade_refuses_once_written(
    head_database: URL, head_engine: Engine, written: str
) -> None:
    with head_engine.begin() as connection:
        if written == "INSERT_USER":
            _add_user(connection, "ann", "ann@example.com")
        else:
            connection.execute(sa.text(written))
    with pytest.raises(DBAPIError, match="mail server configuration exist"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _HEAD_REVISION
        # The policy columns, users.email and the password table's id, password, updated_at.
        assert len(_mail_columns(connection)) == len(_DEFAULTS) + 1 + 3
