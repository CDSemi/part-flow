"""Integration tests for migration ``0035_phase16_backup_policy`` (Phase 16).

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then verifies
what the backup policy of Administration → Backups adds (owner decision of
2026-11-13; IMPLEMENTATION_ROADMAP Phase 16 "backups set in
Administration"):

- the cases run against this revision inside the current chain; the head
  boundary and the models parity moved to
  ``tests/test_phase17_passkeys_schema.py`` with
  ``0038_phase17_passkeys``;
- the migration's literals equal the model constants and the section id;
- a fresh install holds the owner's defaults (every hour; 48 hourly, 30
  daily and 52 weekly backups kept) and the CHECKs refuse every value
  outside the rules;
- an upgrade from 0034 keeps every other section of the singleton;
- the downgrade drops the columns while the policy is untouched, and
  refuses once the section was written (a value other than the defaults,
  or an audit row of the ``backups`` section), the database staying at
  the head the downgrade started from (one transaction).
"""

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.application import policies
from app.infrastructure import models, schema_revision

# Schema assertions: owner connections in the application-role test mode
# (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0034_phase17_unique_names"
_REVISION = "0035_phase16_backup_policy"
# The newest revision of the code; this revision's cases run inside its chain.
_CODE_HEAD = schema_revision.code_head()
_MIGRATION_FILE = _BACKEND_DIR / "alembic" / "versions" / "20261116_0035_phase16_backup_policy.py"
_HEAD_TEMPLATE_DATABASE = "partflow_test_phase16_backup_template"
_DEFAULTS = {
    "backup_interval_hours": 1,
    "backup_keep_hourly": 48,
    "backup_keep_daily": 30,
    "backup_keep_weekly": 52,
}


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
    spec = importlib.util.spec_from_file_location("phase16_backup_policy", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _policy_row(connection: Connection) -> dict[str, Any]:
    return dict(connection.execute(sa.text("SELECT * FROM application_policy")).one()._mapping)


def _backup_columns(connection: Connection) -> list[str]:
    return [
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'application_policy' AND column_name LIKE 'backup\\_%'"
                " ORDER BY column_name"
            )
        ).scalars()
    ]


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
    name = "partflow_test_phase16_backup_case"
    _create_temp_database(admin_engine, name, template=head_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


@pytest.fixture
def head_engine(head_database: URL) -> Iterator[Engine]:
    engine = create_engine(head_database)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------


def test_the_migration_literals_match_the_model() -> None:
    migration = _load_migration()
    assert migration.revision == _REVISION
    assert migration.down_revision == _PREVIOUS_REVISION
    assert migration._SECTION == policies.BACKUPS_SECTION
    assert [(column, default) for column, default, _, _ in migration._COLUMNS] == [
        (column, str(value)) for column, value in _DEFAULTS.items()
    ]
    assert [sql for _, _, _, sql in migration._COLUMNS] == [
        models.POLICY_BACKUP_INTERVAL_SQL,
        models.POLICY_BACKUP_KEEP_HOURLY_SQL,
        models.POLICY_BACKUP_KEEP_DAILY_SQL,
        models.POLICY_BACKUP_KEEP_WEEKLY_SQL,
    ]
    assert (
        "backup_interval_hours IN ("
        + ", ".join(str(hours) for hours in models.BACKUP_INTERVAL_HOURS)
        + ")"
    ) == models.POLICY_BACKUP_INTERVAL_SQL
    assert (
        f"backup_keep_hourly BETWEEN {models.BACKUP_KEEP_HOURLY_MIN}"
        f" AND {models.BACKUP_KEEP_HOURLY_MAX}"
    ) == models.POLICY_BACKUP_KEEP_HOURLY_SQL
    assert (
        f"backup_keep_daily BETWEEN {models.BACKUP_KEEP_DAILY_MIN}"
        f" AND {models.BACKUP_KEEP_DAILY_MAX}"
    ) == models.POLICY_BACKUP_KEEP_DAILY_SQL
    assert (
        f"backup_keep_weekly BETWEEN {models.BACKUP_KEEP_WEEKLY_MIN}"
        f" AND {models.BACKUP_KEEP_WEEKLY_MAX}"
    ) == models.POLICY_BACKUP_KEEP_WEEKLY_SQL


# ---------------------------------------------------------------------------
# A fresh install and the checks
# ---------------------------------------------------------------------------


def test_a_fresh_install_holds_the_owner_defaults(head_engine: Engine) -> None:
    with head_engine.connect() as connection:
        row = _policy_row(connection)
    assert {key: row[key] for key in _DEFAULTS} == _DEFAULTS


@pytest.mark.parametrize(
    "assignment",
    [
        "backup_interval_hours = 5",
        "backup_interval_hours = 0",
        "backup_interval_hours = 48",
        "backup_interval_hours = NULL",
        "backup_keep_hourly = -1",
        "backup_keep_hourly = 721",
        "backup_keep_daily = 0",
        "backup_keep_daily = 3651",
        "backup_keep_weekly = -1",
        "backup_keep_weekly = 521",
    ],
)
def test_the_checks_refuse_values_outside_the_rules(head_engine: Engine, assignment: str) -> None:
    with pytest.raises(IntegrityError), head_engine.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignment}"))
    with head_engine.connect() as connection:
        row = _policy_row(connection)
    assert {key: row[key] for key in _DEFAULTS} == _DEFAULTS


# ---------------------------------------------------------------------------
# Upgrade from 0034 and the downgrade
# ---------------------------------------------------------------------------


def test_the_upgrade_keeps_every_other_section(admin_engine: Engine) -> None:
    name = "partflow_test_phase16_backup_upgrade"
    _create_temp_database(admin_engine, name)
    engine = create_engine(_url(name))
    try:
        command.upgrade(_alembic_config(_url(name)), _PREVIOUS_REVISION)
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE application_policy SET due_soon_min_days = 3,"
                    " worker_session_timeout_minutes = 30, retention_period_months = 24"
                )
            )
            before = _policy_row(connection)
        command.upgrade(_alembic_config(_url(name)), "head")
        with engine.connect() as connection:
            assert _version(connection) == _CODE_HEAD
            after = _policy_row(connection)
        assert {key: after[key] for key in _DEFAULTS} == _DEFAULTS
        assert {key: after[key] for key in before} == before
    finally:
        engine.dispose()
        _drop_temp_database(admin_engine, name)


def test_the_downgrade_drops_an_untouched_policy(head_database: URL, head_engine: Engine) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
        assert _backup_columns(connection) == []
    command.upgrade(_alembic_config(head_database), "head")
    with head_engine.connect() as connection:
        row = _policy_row(connection)
    assert {key: row[key] for key in _DEFAULTS} == _DEFAULTS


@pytest.mark.parametrize(
    "written",
    [
        ("UPDATE application_policy SET backup_keep_daily = 14",),
        (
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy', 'backups', now(),"
            " '{}'::jsonb, '{}'::jsonb)",
        ),
    ],
    ids=["stored-value", "audit-row"],
)
def test_the_downgrade_refuses_a_written_policy(
    head_database: URL, head_engine: Engine, written: tuple[str]
) -> None:
    with head_engine.begin() as connection:
        connection.execute(sa.text(written[0]))
    with pytest.raises(DBAPIError, match="Backup policy configuration exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head_engine.connect() as connection:
        assert _version(connection) == _CODE_HEAD
        assert len(_backup_columns(connection)) == len(_DEFAULTS)
