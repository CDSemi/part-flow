"""Integration tests for the backup policy of Administration → Backups (Phase 16).

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 16, "backups set
in Administration"): the host's backup task runs every hour and reads how
often it takes a scheduled backup and how many its rotation keeps.

- ``GET /api/policies/backups`` (signed in) and ``PUT`` (Configure system
  settings): a full replace of strict integers; a refused value writes
  nothing, a no-op writes nothing, an effective change is audited once as
  ``ApplicationPolicy`` / ``backups`` with its actor, and no other section
  changes;
- ``python -m app.cli backup-policy``: the stored policy as one JSON
  document, and a failure document when the database cannot be read.

The API commits real transactions; the section is restored after each test
and the module database is dropped afterwards. The migration itself is
covered by ``tests/test_phase16_backup_policy_schema.py``.
"""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import admin_of, anonymous_client, client_as

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_backup_policy"
_DB_URL_ENV = "DATABASE_URL"
_POLICY_PATH = "/api/policies/backups"
_DUE_SOON_POLICY_PATH = "/api/policies/due-soon"
_SECTION = "backups"
_DEFAULTS = {
    "backup_interval_hours": 1,
    "backup_keep_hourly": 48,
    "backup_keep_daily": 30,
    "backup_keep_weekly": 52,
}
_CHANGED = {
    "backup_interval_hours": 6,
    "backup_keep_hourly": 24,
    "backup_keep_daily": 14,
    "backup_keep_weekly": 8,
}
_E_INTERVAL = "Back up every 1, 2, 3, 4, 6, 8, 12 or 24 hours."
_E_HOURLY = "Hourly backups kept must be a whole number from 0 to 720."
_E_DAILY = "Daily backups kept must be a whole number from 1 to 3650."
_E_WEEKLY = "Weekly backups kept must be a whole number from 0 to 520."


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
    admin_engine = create_engine(make_url(os.environ[_DB_URL_ENV]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    url = make_url(os.environ[_DB_URL_ENV]).set(database=_TEST_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    yield url
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    original_url = os.environ[_DB_URL_ENV]
    os.environ[_DB_URL_ENV] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ[_DB_URL_ENV] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def default_policy(client: TestClient) -> Iterator[None]:
    """Every test leaves the backup policy at the owner's defaults."""
    yield
    _ok(admin_of(client).put(_POLICY_PATH, json=_DEFAULTS))


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _values(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "updated_at"}


def _policy_audits(engine: Engine) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, before_data, after_data, actor_user_id"
                    " FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
                    " AND entity_id = :entity_id ORDER BY id"
                ),
                {"entity_id": _SECTION},
            )
        )


def _audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one())


def _stored_row(engine: Engine) -> dict[str, Any]:
    """The whole singleton row, with its xmin (a changed xmin means an UPDATE ran)."""
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT xmin::text AS row_xmin, * FROM application_policy")
        ).one()
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# GET / PUT /api/policies/backups
# ---------------------------------------------------------------------------


def test_the_policy_reads_as_the_owner_defaults(client: TestClient) -> None:
    policy = _ok(admin_of(client).get(_POLICY_PATH))
    assert set(policy) == {*_DEFAULTS, "updated_at"}
    assert _values(policy) == _DEFAULTS


def test_an_effective_put_is_stored_and_audited_once(client: TestClient, db_engine: Engine) -> None:
    before = len(_policy_audits(db_engine))
    due_soon = _values(_ok(admin_of(client).get(_DUE_SOON_POLICY_PATH)))

    assert _values(_ok(admin_of(client).put(_POLICY_PATH, json=_CHANGED))) == _CHANGED
    assert _values(_ok(admin_of(client).get(_POLICY_PATH))) == _CHANGED
    row = _stored_row(db_engine)
    assert {key: row[key] for key in _CHANGED} == _CHANGED

    rows = _policy_audits(db_engine)
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == ("UPDATED", _DEFAULTS, _CHANGED, admin_of(client).user_id)
    # No other section changes.
    assert _values(_ok(admin_of(client).get(_DUE_SOON_POLICY_PATH))) == due_soon


def test_an_identical_put_writes_nothing(client: TestClient, db_engine: Engine) -> None:
    stored = _ok(admin_of(client).put(_POLICY_PATH, json=_CHANGED))
    row = _stored_row(db_engine)
    count = _audit_count(db_engine)

    assert _ok(admin_of(client).put(_POLICY_PATH, json=_CHANGED)) == stored
    assert _audit_count(db_engine) == count
    # No UPDATE at all: the row version and its timestamp are unchanged.
    assert _stored_row(db_engine) == row


@pytest.mark.parametrize(
    ("hours", "hourly", "daily", "weekly"),
    [
        (24, 0, 1, 0),
        (12, 720, 3650, 520),
        (2, 48, 30, 52),
        (3, 1, 2, 3),
        (4, 0, 7, 0),
        (8, 5, 5, 5),
    ],
)
def test_every_allowed_value_is_admitted(
    client: TestClient, hours: int, hourly: int, daily: int, weekly: int
) -> None:
    body = {
        "backup_interval_hours": hours,
        "backup_keep_hourly": hourly,
        "backup_keep_daily": daily,
        "backup_keep_weekly": weekly,
    }
    assert _values(_ok(admin_of(client).put(_POLICY_PATH, json=body))) == body


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("backup_interval_hours", 0, _E_INTERVAL),
        ("backup_interval_hours", 5, _E_INTERVAL),
        ("backup_interval_hours", 48, _E_INTERVAL),
        ("backup_keep_hourly", -1, _E_HOURLY),
        ("backup_keep_hourly", 721, _E_HOURLY),
        ("backup_keep_daily", 0, _E_DAILY),
        ("backup_keep_daily", 3651, _E_DAILY),
        ("backup_keep_weekly", -1, _E_WEEKLY),
        ("backup_keep_weekly", 521, _E_WEEKLY),
    ],
)
def test_refused_values_write_nothing(
    client: TestClient, db_engine: Engine, field: str, value: int, message: str
) -> None:
    row = _stored_row(db_engine)
    count = _audit_count(db_engine)
    response = admin_of(client).put(_POLICY_PATH, json={**_DEFAULTS, field: value})
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": message}
    assert _stored_row(db_engine) == row
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize(
    "body",
    [
        {key: value for key, value in _DEFAULTS.items() if key != "backup_keep_weekly"},
        {**_DEFAULTS, "backup_interval_hours": "1"},
        {**_DEFAULTS, "backup_keep_daily": 30.0},
        {**_DEFAULTS, "backup_keep_hourly": True},
        {**_DEFAULTS, "backup_keep_hourly": None},
        {**_DEFAULTS, "extra": 1},
    ],
    ids=["missing-field", "string", "float", "bool", "null", "extra-field"],
)
def test_malformed_bodies_are_refused_by_the_schema(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    row = _stored_row(db_engine)
    count = _audit_count(db_engine)
    assert admin_of(client).put(_POLICY_PATH, json=body).status_code == 422
    assert _stored_row(db_engine) == row
    assert _audit_count(db_engine) == count


def test_reading_needs_a_sign_in_and_writing_needs_configure_system_settings(
    client: TestClient, db_engine: Engine
) -> None:
    assert anonymous_client(client).get(_POLICY_PATH).status_code == 401
    assert anonymous_client(client).put(_POLICY_PATH, json=_CHANGED).status_code == 401
    reader = client_as(client)
    assert _values(_ok(reader.get(_POLICY_PATH))) == _DEFAULTS
    count = _audit_count(db_engine)
    assert reader.put(_POLICY_PATH, json=_CHANGED).status_code == 403
    assert _audit_count(db_engine) == count

    writer = client_as(client, Permission.CONFIGURE_SYSTEM_SETTINGS)
    assert _values(_ok(writer.put(_POLICY_PATH, json=_CHANGED))) == _CHANGED
    assert _policy_audits(db_engine)[-1].actor_user_id == writer.user_id


# ---------------------------------------------------------------------------
# python -m app.cli backup-policy
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_database(api_database_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The in-process CLI reads the module database."""
    monkeypatch.setenv(_DB_URL_ENV, api_database_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_cli_prints_the_stored_policy(
    client: TestClient, cli_database: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["backup-policy"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out) == {
        "report_version": 1,
        "command": "backup-policy",
        "result": "read",
        "exit_code": 0,
        "interval_hours": 1,
        "keep_hourly": 48,
        "keep_daily": 30,
        "keep_weekly": 52,
        "error": None,
    }
    assert "backup-policy: a scheduled backup every hour; keep 48 hourly, 30 daily" in err

    _ok(admin_of(client).put(_POLICY_PATH, json=_CHANGED))
    assert cli.main(["backup-policy"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert (
        document["interval_hours"],
        document["keep_hourly"],
        document["keep_daily"],
        document["keep_weekly"],
    ) == (6, 24, 14, 8)


def test_the_cli_reports_a_database_it_cannot_reach(
    api_database_url: URL, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = api_database_url.set(database="partflow_test_backup_policy_missing")
    monkeypatch.setenv(_DB_URL_ENV, missing.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        assert cli.main(["backup-policy"]) == 2
    finally:
        get_settings.cache_clear()
    out, err = capsys.readouterr()
    document = json.loads(out)
    assert (document["result"], document["exit_code"], document["interval_hours"]) == (
        "failed",
        2,
        None,
    )
    assert document["error"]["code"] == "database_unavailable"
    assert "could not reach its database" in err
