"""Integration tests for migration ``0037_phase17_two_step`` (Phase 17).

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then verifies
the schema of two-step sign-in (owner decisions of 2026-11-13 and
2026-11-17; IMPLEMENTATION_ROADMAP Phase 17 item 8):

- the cases run against this revision inside the current chain; the head
  boundary and the models parity moved to
  ``tests/test_phase17_passkeys_schema.py`` with ``0038_phase17_passkeys``;
- the migration's literals equal the model constants: the three tables'
  CHECKs, the policy columns with their defaults and ranges, the
  wrong-code counter, the User session end reasons (the Phase 14 ones of
  0029 plus ``TWO_STEP_RESET``) and the audited section;
- a fresh install holds no factor, code or challenge, the policy row holds
  every default and each credential's wrong-code counter is 0; the CHECKs
  refuse a key of the wrong length, a negative step, digests of the wrong
  length, an emailed code without its time, an end without its reason, an
  unknown end reason, each policy value just outside its range and a
  negative wrong-code counter, while UNIQUE refuses a second token or the
  same code twice for one User; a session may end ``TWO_STEP_RESET``;
- the downgrade restores 0036 while nothing two-step exists, and refuses
  — the revision staying at 0037 — for each trigger separately: a row in
  each table, a session ended ``TWO_STEP_RESET``, each policy value off
  its default and a ``two-step`` audit row.
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
from app.domain.enums import SignInChallengeEndReason, UserSessionEndReason
from app.infrastructure import models, schema_revision

# Schema assertions: owner connections in the application-role test mode
# (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0036_phase17_outgoing_mail"
_HEAD_REVISION = "0037_phase17_two_step"
_CODE_HEAD = schema_revision.code_head()
_VERSIONS = _BACKEND_DIR / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS / "20261117_0037_phase17_two_step.py"
_SIGN_IN_MIGRATION_FILE = _VERSIONS / "20261006_0029_phase14_sign_in.py"
_HEAD_TEMPLATE_DATABASE = "partflow_test_phase17_two_step_template"
_TABLES = ("user_totp_factors", "user_recovery_codes", "user_sign_in_challenges")
_DIGEST = "'\\x" + "ab" * 32 + "'::bytea"
_SECRET = "'\\x" + "cd" * 20 + "'::bytea"
_HASH = "'scrypt$16384$8$1$c2FsdA$a2V5'"
# Every policy column of the revision: (column, default, minimum, maximum).
_POLICY_COLUMNS = (
    ("mail_timeout_seconds", 20, 5, 120),
    ("two_step_window_minutes", 15, 1, 60),
    ("two_step_max_codes", 5, 1, 20),
    ("two_step_lockout_codes", 10, 3, 100),
    ("two_step_email_codes", 3, 1, 10),
    ("two_step_email_interval_seconds", 60, 0, 3600),
    ("two_step_email_code_digits", 8, 6, 10),
    ("two_step_recovery_codes", 10, 5, 20),
    ("two_step_recovery_code_length", 12, 8, 20),
    ("two_step_setup_minutes", 15, 1, 60),
    ("two_step_clock_drift_steps", 1, 0, 2),
)


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


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_migration() -> ModuleType:
    return _load(_MIGRATION_FILE, "phase17_two_step")


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _tables(connection: Connection) -> list[str]:
    return [
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables WHERE table_name IN"
                " ('user_totp_factors', 'user_recovery_codes', 'user_sign_in_challenges')"
                " ORDER BY 1"
            )
        ).scalars()
    ]


def _user(connection: Connection, login: str = "ann") -> int:
    role_id = connection.execute(sa.text("SELECT min(id) FROM roles")).scalar_one()
    return int(
        connection.execute(
            sa.text(
                "INSERT INTO users (login_name, display_name, role_id)"
                " VALUES (:login, 'Ann', :role_id) RETURNING id"
            ),
            {"login": login, "role_id": role_id},
        ).scalar_one()
    )


def _credential(connection: Connection, user_id: int) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO user_credentials (user_id, password_hash, password_is_temporary,"
            f" password_changed_at) VALUES (:u, {_HASH}, false, now())"
        ),
        {"u": user_id},
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
    name = "partflow_test_phase17_two_step_case"
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
    assert (
        models.UserTotpFactor.__tablename__,
        models.UserRecoveryCode.__tablename__,
        models.UserSignInChallenge.__tablename__,
    ) == (migration._FACTORS, migration._CODES, migration._CHALLENGES)
    assert [
        migration._TOTP_SECRET_SQL,
        migration._LAST_USED_STEP_SQL,
        migration._CODE_DIGEST_SQL,
        migration._TOKEN_DIGEST_SQL,
        migration._FAILED_ATTEMPTS_SQL,
        migration._EMAIL_CODE_SQL,
        migration._EMAIL_CODE_SHAPE_SQL,
        migration._EMAIL_CODES_SENT_SQL,
        migration._END_REASON_SQL,
        migration._END_SHAPE_SQL,
        migration._FAILED_CODES_SQL,
    ] == [
        models.TOTP_SECRET_SQL,
        models.TOTP_LAST_USED_STEP_SQL,
        models.RECOVERY_CODE_DIGEST_SQL,
        models.CHALLENGE_TOKEN_DIGEST_SQL,
        models.CHALLENGE_FAILED_ATTEMPTS_SQL,
        models.CHALLENGE_EMAIL_CODE_SQL,
        models.CHALLENGE_EMAIL_CODE_SHAPE_SQL,
        models.CHALLENGE_EMAIL_CODES_SENT_SQL,
        models.CHALLENGE_END_REASON_SQL,
        models.CHALLENGE_END_SHAPE_SQL,
        models.USER_CREDENTIAL_FAILED_CODES_SQL,
    ]
    assert (
        "end_reason IN (" + ", ".join(f"'{reason}'" for reason in SignInChallengeEndReason) + ")"
    ) == models.CHALLENGE_END_REASON_SQL
    assert migration._SECTION == policies.TWO_STEP_SECTION


def test_the_session_end_reasons_extend_the_phase14_ones() -> None:
    migration = _load_migration()
    sign_in = _load(_SIGN_IN_MIGRATION_FILE, "phase14_sign_in")
    assert migration._SESSION_END_REASONS_BEFORE == sign_in._END_REASONS
    assert tuple(UserSessionEndReason) == migration._SESSION_END_REASONS
    assert migration._reasons_sql(migration._SESSION_END_REASONS) == (
        models.USER_SESSION_END_REASON_SQL
    )
    assert migration._reasons_sql(migration._SESSION_END_REASONS_BEFORE) == sign_in._END_REASON_SQL


def test_the_policy_columns_match_the_model() -> None:
    migration = _load_migration()
    assert migration._POLICY_COLUMNS == _POLICY_COLUMNS
    ranges = {
        "mail_timeout_seconds": models.MAIL_TIMEOUT_SECONDS_RANGE,
        **models.TWO_STEP_POLICY_RANGES,
    }
    table = models.Base.metadata.tables["application_policy"]
    checks = {str(c.name): str(c.sqltext) for c in table.constraints if hasattr(c, "sqltext")}
    for column, default, minimum, maximum in _POLICY_COLUMNS:
        assert ranges[column] == (minimum, maximum), column
        server_default = table.c[column].server_default
        assert isinstance(server_default, sa.DefaultClause), column
        assert str(server_default.arg) == str(default), column
        assert checks[f"ck_application_policy_{column}_range"] == (
            f"{column} BETWEEN {minimum} AND {maximum}"
        )
    assert list(ranges) == [column for column, *_ in _POLICY_COLUMNS]


# ---------------------------------------------------------------------------
# A fresh install and the checks
# ---------------------------------------------------------------------------


def test_a_fresh_install_holds_no_factor_code_or_challenge(head_engine: Engine) -> None:
    with head_engine.connect() as connection:
        assert _tables(connection) == sorted(_TABLES)
        for table in _TABLES:
            assert connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_a_fresh_install_holds_the_policy_defaults(head_engine: Engine) -> None:
    columns = ", ".join(column for column, *_ in _POLICY_COLUMNS)
    with head_engine.connect() as connection:
        row = connection.execute(sa.text(f"SELECT {columns} FROM application_policy")).one()
    assert tuple(row) == tuple(default for _, default, *_ in _POLICY_COLUMNS)


def test_a_new_credential_has_no_wrong_code(head_engine: Engine) -> None:
    inspector = inspect(head_engine)
    column = {str(c["name"]): c for c in inspector.get_columns("user_credentials")}["failed_codes"]
    assert column["nullable"] is False
    with head_engine.begin() as connection:
        user_id = _user(connection)
        _credential(connection, user_id)
        assert (
            connection.execute(
                sa.text("SELECT failed_codes FROM user_credentials WHERE user_id = :u"),
                {"u": user_id},
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO user_totp_factors (user_id, secret) VALUES (:u, '\\x00'::bytea)",
        f"INSERT INTO user_totp_factors (user_id, secret, last_used_step)"
        f" VALUES (:u, {_SECRET}, -1)",
        "INSERT INTO user_recovery_codes (user_id, code_digest) VALUES (:u, '\\x00'::bytea)",
        "INSERT INTO user_sign_in_challenges (user_id, token_digest) VALUES (:u, '\\x00'::bytea)",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest, failed_attempts)"
        f" VALUES (:u, {_DIGEST}, -1)",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest, email_code_digest)"
        f" VALUES (:u, {_DIGEST}, {_DIGEST})",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest, email_code_digest,"
        f" email_code_sent_at) VALUES (:u, {_DIGEST}, '\\x00'::bytea, now())",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest, email_codes_sent)"
        f" VALUES (:u, {_DIGEST}, -1)",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest, ended_at)"
        f" VALUES (:u, {_DIGEST}, now())",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest, ended_at, end_reason)"
        f" VALUES (:u, {_DIGEST}, now(), 'EXPIRED')",
        "INSERT INTO user_credentials (user_id, password_hash, password_is_temporary,"
        f" password_changed_at, failed_codes) VALUES (:u, {_HASH}, false, now(), -1)",
        "INSERT INTO user_sessions (user_id, token_digest, ended_at, end_reason)"
        f" VALUES (:u, {_DIGEST}, now(), 'TWO_STEP_REMOVED')",
    ],
)
def test_the_checks_refuse_values_outside_the_rules(head_engine: Engine, statement: str) -> None:
    with head_engine.begin() as connection:
        user_id = _user(connection)
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(statement), {"u": user_id})


@pytest.mark.parametrize(
    ("column", "value"),
    [
        pytest.param(column, value, id=f"{column}={value}")
        for column, _default, minimum, maximum in _POLICY_COLUMNS
        for value in (minimum - 1, maximum + 1)
    ],
)
def test_the_policy_ranges_are_refused(head_engine: Engine, column: str, value: int) -> None:
    refused = pytest.raises(IntegrityError, match=f"ck_application_policy_{column}_range")
    with refused, head_engine.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {column} = {value}"))


def test_a_session_may_end_two_step_reset(head_engine: Engine) -> None:
    with head_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO user_sessions (user_id, token_digest, ended_at, end_reason)"
                f" VALUES (:u, {_DIGEST}, now(), 'TWO_STEP_RESET')"
            ),
            {"u": _user(connection)},
        )


def test_uniques_refuse_a_second_token_or_the_same_code(head_engine: Engine) -> None:
    with head_engine.begin() as connection:
        user_id = _user(connection)
        connection.execute(
            sa.text(
                f"INSERT INTO user_recovery_codes (user_id, code_digest) VALUES (:u, {_DIGEST})"
            ),
            {"u": user_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO user_sign_in_challenges (user_id, token_digest)"
                f" VALUES (:u, {_DIGEST})"
            ),
            {"u": user_id},
        )
        connection.execute(
            sa.text(f"INSERT INTO user_totp_factors (user_id, secret) VALUES (:u, {_SECRET})"),
            {"u": user_id},
        )
    for statement in (
        f"INSERT INTO user_recovery_codes (user_id, code_digest) VALUES (:u, {_DIGEST})",
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest) VALUES (:u, {_DIGEST})",
        f"INSERT INTO user_totp_factors (user_id, secret) VALUES (:u, {_SECRET})",
    ):
        with pytest.raises(IntegrityError), head_engine.begin() as connection:
            connection.execute(sa.text(statement), {"u": user_id})


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def test_the_downgrade_restores_the_previous_schema(
    head_database: URL, head_engine: Engine
) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    inspector = inspect(head_engine)
    policy_columns = {str(c["name"]) for c in inspector.get_columns("application_policy")}
    credential_columns = {str(c["name"]) for c in inspector.get_columns("user_credentials")}
    with head_engine.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
        assert _tables(connection) == []
    assert not policy_columns & {column for column, *_ in _POLICY_COLUMNS}
    assert "failed_codes" not in credential_columns
    refused = pytest.raises(IntegrityError, match="ck_user_sessions_end_reason")
    with refused, head_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO user_sessions (user_id, token_digest, ended_at, end_reason)"
                f" VALUES (:u, {_DIGEST}, now(), 'TWO_STEP_RESET')"
            ),
            {"u": _user(connection)},
        )
    command.upgrade(_alembic_config(head_database), "head")
    with head_engine.connect() as connection:
        assert _version(connection) == _CODE_HEAD
        assert _tables(connection) == sorted(_TABLES)


def _off_default(column: str, default: int, minimum: int, maximum: int) -> str:
    value = minimum if default != minimum else maximum
    return f"UPDATE application_policy SET {column} = {value}"


_DOWNGRADE_TRIGGERS = {
    "factor": f"INSERT INTO user_totp_factors (user_id, secret) VALUES (:u, {_SECRET})",
    "recovery-code": (
        f"INSERT INTO user_recovery_codes (user_id, code_digest) VALUES (:u, {_DIGEST})"
    ),
    "challenge": (
        f"INSERT INTO user_sign_in_challenges (user_id, token_digest) VALUES (:u, {_DIGEST})"
    ),
    "session-ended-by-reset": (
        "INSERT INTO user_sessions (user_id, token_digest, ended_at, end_reason)"
        f" VALUES (:u, {_DIGEST}, now(), 'TWO_STEP_RESET')"
    ),
    "audit-row": (
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
        " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy', 'two-step', now(),"
        " '{}'::jsonb, '{}'::jsonb)"
    ),
    **{column: _off_default(column, *bounds) for column, *bounds in _POLICY_COLUMNS},
}


@pytest.mark.parametrize("trigger", list(_DOWNGRADE_TRIGGERS))
def test_the_downgrade_refuses_while_two_step_data_exists(
    head_database: URL, head_engine: Engine, trigger: str
) -> None:
    statement = _DOWNGRADE_TRIGGERS[trigger]
    with head_engine.begin() as connection:
        user_id = _user(connection)
        connection.execute(sa.text(statement), {"u": user_id} if ":u" in statement else {})
    with pytest.raises(DBAPIError, match="Two-step sign-in data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _CODE_HEAD
        assert _tables(connection) == sorted(_TABLES)
