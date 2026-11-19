"""Integration tests for migration ``0040_phase16_history_archive`` (history archival, step 1).

Runs the real Alembic chain against isolated, temporary PostgreSQL databases
(created and dropped by the fixtures) and verifies:

- the cases run against this revision inside the current chain; the head
  boundary and the models parity moved to ``tests/test_history_purge_schema.py``
  with ``0041_phase16_history_purge``; the migration's literals against the
  models, enums and policies of step 1 (as migration 0041 repeats them);
- a fresh install: the 24-month retention period with its migration audit
  row, the defaults of the new settings, the key granted to Administrator and
  Super User with one Role audit row each;
- the upgrade from 0039: a retention period once saved stays as it is; the key
  goes to the role named Administrator in any letter case and to every role
  holding every other key, never to a narrowed role;
- the CHECKs of the settings and the runs, and the open-run index;
- the run guard (the request never changes, the cutoff floor, the
  application's cancel and copy confirmation, the job's transitions only with
  ``partflow.archive_run_id``, finished runs, no delete or truncate) and the
  record guard (only while the run is RUNNING and named, skipped once, never
  purged in step 1, never deleted);
- the database roles (temporary roles granted by ``apply-grants``): the
  application role may not make the job's changes or write records; the
  maintenance role makes them and appends ``HistoryArchiveRun`` audit rows
  (it reads the audit rows and deletes since later steps);
- the downgrade restores 0039 (revoking the key with an audit row per role)
  and refuses while archival data or a changed setting exists.
"""

import datetime
import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.application import database_roles, history_archive, policies
from app.application.database_roles import DatabaseRoles
from app.domain.enums import HistoryArchiveRunResult, HistoryArchiveRunState, Permission
from app.infrastructure import models, schema_revision
from app.infrastructure.database_privileges import GUARD_FUNCTION_SHA256, GUARD_TRIGGERS
from tests.role_harness import drop_roles, temporary_roles

# Schema, trigger and role assertions: owner connections in the
# application-role test mode (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_VERSIONS = _BACKEND_DIR / "alembic" / "versions"
_PREVIOUS_REVISION = "0039_security_settings"
_HEAD_REVISION = "0040_phase16_history_archive"
_CODE_HEAD = schema_revision.code_head()
_MIGRATION_FILE = _VERSIONS / "20261117_0040_phase16_history_archive.py"
_PURGE_MIGRATION_FILE = _VERSIONS / "20261118_0041_phase16_history_purge.py"
_DELETION_MIGRATION_FILE = _VERSIONS / "20261118_0042_phase16_deletion_requests.py"
_USERS_ROLES_FILE = _VERSIONS / "20261006_0028_phase13_users_roles.py"
_ROUTE_ADJUSTED_FILE = _VERSIONS / "20261007_0032_phase14_route_adjusted.py"
_RETENTION_FILE = _VERSIONS / "20261006_0027_phase13_retention_period.py"
_HEAD_TEMPLATE = "partflow_test_history_archive_schema_head"
_PREVIOUS_TEMPLATE = "partflow_test_history_archive_schema_previous"
_CASE = "partflow_test_history_archive_schema_case"
_KEY = Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE.value
_METADATA = {"source": "migration", "revision": _HEAD_REVISION}
_SECTION_DEFAULTS = {
    "retention_period_months": 24,
    "shortest_retention_months": 12,
    "archive_max_wait_days": 7,
    "archive_start_minute": 20,
    "archive_preview_seconds": 90,
}


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _create_database(admin: Engine, name: str, template: str | None = None) -> None:
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        suffix = f' TEMPLATE "{template}"' if template else ""
        connection.execute(sa.text(f'CREATE DATABASE "{name}"{suffix}'))


def _drop_database(admin: Engine, name: str) -> None:
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _url(name: str) -> URL:
    return make_url(os.environ["DATABASE_URL"]).set(database=name)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
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
def head_template(admin_engine: Engine) -> Iterator[str]:
    _create_database(admin_engine, _HEAD_TEMPLATE)
    command.upgrade(_alembic_config(_url(_HEAD_TEMPLATE)), "head")
    yield _HEAD_TEMPLATE
    _drop_database(admin_engine, _HEAD_TEMPLATE)


@pytest.fixture(scope="module")
def previous_template(admin_engine: Engine) -> Iterator[str]:
    _create_database(admin_engine, _PREVIOUS_TEMPLATE)
    command.upgrade(_alembic_config(_url(_PREVIOUS_TEMPLATE)), _PREVIOUS_REVISION)
    yield _PREVIOUS_TEMPLATE
    _drop_database(admin_engine, _PREVIOUS_TEMPLATE)


@pytest.fixture
def head_database(admin_engine: Engine, head_template: str) -> Iterator[URL]:
    _create_database(admin_engine, _CASE, template=head_template)
    yield _url(_CASE)
    _drop_database(admin_engine, _CASE)


@pytest.fixture
def previous_database(admin_engine: Engine, previous_template: str) -> Iterator[URL]:
    _create_database(admin_engine, _CASE, template=previous_template)
    yield _url(_CASE)
    _drop_database(admin_engine, _CASE)


@pytest.fixture
def head(head_database: URL) -> Iterator[Engine]:
    engine = create_engine(head_database)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Small data helpers (direct SQL as the owner)
# ---------------------------------------------------------------------------


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


def _old_cutoff() -> datetime.datetime:
    return datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)


def _insert_run(connection: Connection, user_id: int, **values: Any) -> int:
    row = {
        "state": "APPROVED",
        "cutoff": _old_cutoff(),
        "reason": "test",
        "requested_by_user_id": user_id,
        "preview_components": 1,
        "preview_flows": 1,
        "preview_movements": 1,
        "preview_archive_bytes": 0,
        **values,
    }
    columns = ", ".join(row)
    placeholders = ", ".join(f":{column}" for column in row)
    return int(
        connection.execute(
            sa.text(
                f"INSERT INTO history_archive_runs ({columns}) VALUES ({placeholders}) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


def _mark(connection: Connection, run_id: int) -> None:
    connection.execute(
        sa.text("SELECT set_config('partflow.archive_run_id', :id, true)"), {"id": str(run_id)}
    )


def _flow(connection: Connection) -> int:
    department = connection.execute(
        sa.text("INSERT INTO departments (name) VALUES ('D') RETURNING id")
    ).scalar_one()
    area = connection.execute(
        sa.text("INSERT INTO areas (department_id, name) VALUES (:d, 'A') RETURNING id"),
        {"d": department},
    ).scalar_one()
    return int(
        connection.execute(
            sa.text(
                "INSERT INTO quantity_flows (part_number, quantity, status, current_area_id,"
                " closed_at) VALUES ('PN-1', 1, 'STOCKED', :area, now()) RETURNING id"
            ),
            {"area": area},
        ).scalar_one()
    )


def _record(flow_id: int, run_id: int) -> dict[str, Any]:
    moment = _old_cutoff() - datetime.timedelta(days=30)
    return {
        "run_id": run_id,
        "flow_id": flow_id,
        "part_number": "PN-1",
        "component_id": flow_id,
        "flow_status": "STOCKED",
        "movement_count": 2,
        "first_movement_id": 1,
        "last_movement_id": 2,
        "first_occurred_at": moment,
        "last_occurred_at": moment,
        "content_sha256": "ab" * 32,
    }


def _insert_record(connection: Connection, values: dict[str, Any]) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{column}" for column in values)
    connection.execute(
        sa.text(f"INSERT INTO history_archived_flows ({columns}) VALUES ({placeholders})"), values
    )


def _refused(engine: Engine, statements: list[tuple[str, dict[str, Any]]], match: str) -> None:
    """The last statement of one transaction is refused with ``match``; nothing stays."""
    with pytest.raises(DBAPIError, match=match), engine.begin() as connection:
        for sql, params in statements:
            connection.execute(sa.text(sql), params)


def _running(connection: Connection, run_id: int) -> None:
    _mark(connection, run_id)
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now() WHERE id = :id"
        ),
        {"id": run_id},
    )


# ---------------------------------------------------------------------------
# Head and literals
# ---------------------------------------------------------------------------


def test_the_revision_is_in_the_chain() -> None:
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    revision = ScriptDirectory.from_config(config).get_revision(_HEAD_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS_REVISION
    assert schema_revision.non_transactional_revisions([_HEAD_REVISION]) == []


def _model_checks(model: type[models.Base]) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in cast(sa.Table, model.__table__).constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def test_the_migration_literals_match_the_model() -> None:
    migration = _load(_MIGRATION_FILE, "history_archive_migration")
    users_roles = _load(_USERS_ROLES_FILE, "users_roles_migration")
    route_adjusted = _load(_ROUTE_ADJUSTED_FILE, "route_adjusted_migration")
    retention = _load(_RETENTION_FILE, "retention_migration")
    assert (migration.revision, migration.down_revision) == (_HEAD_REVISION, _PREVIOUS_REVISION)
    assert migration._PREVIOUS_PERMISSIONS == users_roles._PERMISSIONS
    # 0042 (step 3) appends its key and DeletionRequest to the literals this
    # revision wrote, and repeats them as its previous ones.
    deletion = _load(_DELETION_MIGRATION_FILE, "deletion_requests_migration_for_0040")
    assert tuple(permission.value for permission in Permission) == (
        *migration._PERMISSIONS,
        Permission.DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY.value,
    )
    assert migration._PERMISSIONS == deletion._PREVIOUS_PERMISSIONS
    assert migration._KEY == _KEY
    assert migration._PERMISSION_SQL == deletion._PREVIOUS_PERMISSION_SQL
    assert migration._PREVIOUS_PERMISSION_SQL == users_roles._PERMISSION_SQL
    assert migration._PREVIOUS_ENTITY_TYPES == route_adjusted._ROUTE_ENTITY_TYPES
    assert migration._ENTITY_TYPES == deletion._PREVIOUS_ENTITY_TYPES
    assert migration._PREVIOUS_RETENTION_SQL == retention._RETENTION_SQL
    assert migration._RETENTION_SQL == models.POLICY_RETENTION_PERIOD_SQL
    # Step 1's settings; step 2 (0041) adds the purge batch limits after them, step 4
    # (0043) the limits of the automatic runs and the days between them, and step 5
    # (0044) the settings of viewing an archive.
    assert {
        column: (minimum, maximum) for column, _, minimum, maximum in migration._POLICY_COLUMNS
    } == {
        column: bounds
        for column, bounds in models.HISTORY_ARCHIVE_POLICY_RANGES.items()
        if not column.startswith(
            ("archive_purge_", "archive_limit_", "archive_interval_", "archive_view_")
        )
    }
    assert migration._POLICY_COLUMNS[0][1] == models.SHORTEST_RETENTION_MONTHS_DEFAULT
    assert migration._SECTION == policies.DATA_RETENTION_SECTION
    assert policies.HISTORY_ARCHIVE_COLUMNS[:5] == migration._SECTION_COLUMNS
    # The run states, results and CHECKs as 0041 found them (it repeats 0040's).
    purge = _load(_PURGE_MIGRATION_FILE, "history_purge_migration_for_0040")
    assert migration._STATES == purge._PREVIOUS_STATES
    assert migration._RESULTS == purge._PREVIOUS_RESULTS
    assert set(migration._STATES) < {state.value for state in HistoryArchiveRunState}
    assert set(migration._RESULTS) < {result.value for result in HistoryArchiveRunResult}
    assert migration._FINAL_STATES == purge._PREVIOUS_FINAL_STATES
    assert migration._OPEN_STATES == purge._PREVIOUS_OPEN_STATES
    assert migration._CANONICAL_PART_NUMBER_SQL == models.CANONICAL_PART_NUMBER_SQL
    assert purge._in("state", purge._PREVIOUS_OPEN_STATES) == migration._RUN_OPEN_WHERE_SQL
    changed = {suffix for suffix, _old, _new in purge._CHANGED_RUN_CHECKS}
    model_checks = _model_checks(models.HistoryArchiveRun)
    for suffix, sql in migration._RUN_CHECKS:
        if suffix not in changed:
            assert model_checks[f"ck_history_archive_runs_{suffix}"] == sql, suffix
    assert {
        f"ck_history_archived_flows_{suffix}": sql for suffix, sql in migration._FLOW_CHECKS
    } == _model_checks(models.HistoryArchivedFlow)
    for name, _statement, table in migration._TRIGGERS:
        assert GUARD_TRIGGERS[name].table == table
    assert {
        migration._RUNS_FUNCTION,
        migration._FLOWS_FUNCTION,
        migration._AUDIT_FUNCTION,
    } <= set(GUARD_FUNCTION_SHA256)


# ---------------------------------------------------------------------------
# A fresh install
# ---------------------------------------------------------------------------


def _grants(connection: Connection) -> dict[str, set[str]]:
    grants: dict[str, set[str]] = {}
    for name, permission in connection.execute(
        sa.text(
            "SELECT r.name, p.permission FROM roles r"
            " LEFT JOIN role_permissions p ON p.role_id = r.id"
        )
    ):
        keys = grants.setdefault(str(name), set())
        if permission is not None:
            keys.add(str(permission))
    return grants


def _audits(connection: Connection, entity_type: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            sa.text(
                "SELECT event_type, entity_id, actor_user_id, before_data, after_data, metadata"
                " FROM audit_events WHERE entity_type = :type ORDER BY id"
            ),
            {"type": entity_type},
        ).mappings()
    ]


def _section(connection: Connection) -> dict[str, Any]:
    return dict(
        connection.execute(
            sa.text(
                "SELECT retention_period_months, shortest_retention_months, archive_max_wait_days,"
                " archive_start_minute, archive_preview_seconds FROM application_policy"
            )
        )
        .mappings()
        .one()
    )


def test_a_fresh_install_holds_the_defaults_and_the_key(head: Engine) -> None:
    with head.connect() as connection:
        assert _version(connection) == _CODE_HEAD
        assert _section(connection) == _SECTION_DEFAULTS
        [retention] = _audits(connection, "ApplicationPolicy")
        assert retention["entity_id"] == "data-retention"
        assert retention["before_data"] == {**_SECTION_DEFAULTS, "retention_period_months": None}
        assert retention["after_data"] == _SECTION_DEFAULTS
        assert (retention["actor_user_id"], retention["metadata"]) == (None, _METADATA)
        grants = _grants(connection)
        assert _KEY in grants["Administrator"] and _KEY in grants["Super User"]
        assert _KEY not in grants["Manager"] and _KEY not in grants["Operator"]
        assert grants["Super User"] == {permission.value for permission in Permission}
        # This revision's rows (0042 adds its own row for Super User).
        roles = [row for row in _audits(connection, "Role") if row["metadata"] == _METADATA]
        assert [row["after_data"]["name"] for row in roles] == ["Administrator", "Super User"]
        for row in roles:
            assert set(row["after_data"]["permissions"]) - set(
                row["before_data"]["permissions"]
            ) == {_KEY}
            assert (row["event_type"], row["actor_user_id"], row["metadata"]) == (
                "UPDATED",
                None,
                _METADATA,
            )
        assert (
            connection.execute(sa.text("SELECT count(*) FROM history_archive_runs")).scalar() == 0
        )


# ---------------------------------------------------------------------------
# The upgrade from 0039
# ---------------------------------------------------------------------------


def test_a_retention_period_once_saved_is_kept(previous_database: URL) -> None:
    engine = create_engine(previous_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
                    " before_data, after_data) VALUES ('UPDATED', 'ApplicationPolicy',"
                    " 'data-retention', now(), '{\"retention_period_months\": 36}',"
                    " '{\"retention_period_months\": null}')"
                )
            )
        command.upgrade(_alembic_config(previous_database), "head")
        with engine.connect() as connection:
            assert _section(connection)["retention_period_months"] is None
            assert len(_audits(connection, "ApplicationPolicy")) == 1
    finally:
        engine.dispose()


def test_a_period_never_saved_becomes_24_months_once(previous_database: URL) -> None:
    engine = create_engine(previous_database)
    config = _alembic_config(previous_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE application_policy SET retention_period_months = 60")
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _section(connection)["retention_period_months"] == 24
            [row] = _audits(connection, "ApplicationPolicy")
            assert row["before_data"]["retention_period_months"] == 60
        # Downgrading and upgrading again changes the period no more.
        command.downgrade(config, _PREVIOUS_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _section(connection)["retention_period_months"] == 24
            assert len(_audits(connection, "ApplicationPolicy")) == 1
    finally:
        engine.dispose()


def _insert_role(connection: Connection, name: str, keys: list[str]) -> int:
    role_id = int(
        connection.execute(
            sa.text("INSERT INTO roles (name) VALUES (:name) RETURNING id"), {"name": name}
        ).scalar_one()
    )
    if keys:
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission)"
                " SELECT :role_id, unnest(CAST(:keys AS text[]))"
            ),
            {"role_id": role_id, "keys": keys},
        )
    return role_id


def test_the_key_goes_to_administrator_and_every_full_role_only(previous_database: URL) -> None:
    engine = create_engine(previous_database)
    # The vocabulary before this revision (0042's key comes later still).
    later = {_KEY, Permission.DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY.value}
    every = [permission.value for permission in Permission if permission.value not in later]
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE roles SET name = 'administrator' WHERE name = 'Administrator'")
            )
            full = _insert_role(connection, "Everything", every)
            narrowed = _insert_role(connection, "Almost", every[1:])
        command.upgrade(_alembic_config(previous_database), "head")
        with engine.connect() as connection:
            grants = _grants(connection)
            assert _KEY in grants["administrator"]
            assert _KEY in grants["Everything"] and _KEY in grants["Super User"]
            assert _KEY not in grants["Almost"]
            changed = {row["entity_id"] for row in _audits(connection, "Role")}
            assert str(full) in changed and str(narrowed) not in changed
            assert len(changed) == 3
    finally:
        engine.dispose()


def test_without_a_role_named_administrator_only_full_roles_get_the_key(
    previous_database: URL,
) -> None:
    engine = create_engine(previous_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE roles SET name = 'Admins' WHERE name = 'Administrator'")
            )
        command.upgrade(_alembic_config(previous_database), "head")
        with engine.connect() as connection:
            grants = _grants(connection)
            assert _KEY not in grants["Admins"]
            assert _KEY in grants["Super User"]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# CHECKs and the open-run index
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "assignment",
    [
        "retention_period_months = 11",
        "retention_period_months = 1201",
        "shortest_retention_months = 0",
        "shortest_retention_months = 1201",
        "shortest_retention_months = 30",
        "archive_max_wait_days = 0",
        "archive_max_wait_days = 366",
        "archive_start_minute = 60",
        "archive_start_minute = -1",
        "archive_preview_seconds = 9",
        "archive_preview_seconds = 601",
    ],
)
def test_the_setting_checks_refuse_values_outside_their_ranges(
    head: Engine, assignment: str
) -> None:
    with pytest.raises(IntegrityError), head.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignment}"))


@pytest.mark.parametrize(
    "assignment",
    [
        "retention_period_months = NULL",
        "shortest_retention_months = 1, retention_period_months = 1",
        "retention_period_months = 1200, shortest_retention_months = 1200",
        "archive_max_wait_days = 1",
        "archive_max_wait_days = 365",
        "archive_start_minute = 0",
        "archive_start_minute = 59",
        "archive_preview_seconds = 10",
        "archive_preview_seconds = 600",
    ],
)
def test_the_setting_checks_admit_their_bounds(head: Engine, assignment: str) -> None:
    with head.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignment}"))


def test_at_most_one_run_is_open(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        _insert_run(connection, user_id)
    with (
        pytest.raises(IntegrityError, match="uq_history_archive_runs_open"),
        head.begin() as connection,
    ):
        _insert_run(connection, user_id)


# ---------------------------------------------------------------------------
# The run guard
# ---------------------------------------------------------------------------


def _cancel(connection: Connection, run_id: int, user_id: int) -> None:
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET state = 'CANCELLED', finished_at = now(),"
            " cancelled_by_user_id = :user WHERE id = :id"
        ),
        {"user": user_id, "id": run_id},
    )


def test_the_cutoff_floor_of_a_new_run(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        floor = connection.execute(
            sa.text("SELECT (now() AT TIME ZONE 'UTC' - interval '12 months') AT TIME ZONE 'UTC'")
        ).scalar_one()
    now = datetime.datetime.now(datetime.UTC)
    refused = "newer than the shortest allowed period"
    for cutoff in (now, floor + datetime.timedelta(days=4, hours=1)):
        with pytest.raises(IntegrityError, match=refused) as caught, head.begin() as connection:
            _insert_run(connection, user_id, cutoff=cutoff)
        # A check violation naming the trigger, which the request maps to a 409.
        assert getattr(caught.value.orig, "sqlstate", None) == "23514"
        diagnostics = getattr(caught.value.orig, "diag", None)
        assert diagnostics is not None
        assert diagnostics.constraint_name == "trg_history_archive_runs_guard_mutation"
    # The allowance of four days for the site's calendar.
    with head.begin() as connection:
        run_id = _insert_run(
            connection, user_id, cutoff=floor + datetime.timedelta(days=3, hours=23)
        )
        _cancel(connection, run_id, user_id)
    # A longer shortest period moves the floor.
    with head.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE application_policy SET retention_period_months = 1200,"
                " shortest_retention_months = 1200"
            )
        )
    with pytest.raises(DBAPIError, match=r"\(1200 months\)"), head.begin() as connection:
        _insert_run(connection, user_id)


def test_the_cutoff_floor_admits_the_site_cutoff_at_the_end_of_february(
    head: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review case: retention and shortest period both 13 months, a site at UTC+7, and
    2027-02-28 18:00 UTC, already March 1 on site. The application's cutoff is the start of
    2026-02-01 on site (2026-01-31 17:00 UTC); the trigger counts 13 months back from the
    UTC date, February 28, to 2026-01-28 18:00 UTC, and admits it only with four days'
    allowance (one day refused it, and the request answered 500)."""
    moment = datetime.datetime(2027, 2, 28, 18, 0, tzinfo=datetime.UTC)
    monkeypatch.setattr(history_archive, "now", lambda: moment)
    monkeypatch.setattr(history_archive, "site_zone", lambda: ZoneInfo("Asia/Bangkok"))
    settings = history_archive.ArchivalSettings(13, 13, 7, 20, 90, 50000, 20)
    cutoff = history_archive.resolve_cutoff(settings, None)
    assert cutoff == history_archive.Cutoff(
        datetime.date(2026, 2, 1), datetime.datetime(2026, 1, 31, 17, 0, tzinfo=datetime.UTC)
    )
    assert history_archive.shortest_period_allows(cutoff.instant, 13)
    with head.begin() as connection:
        user_id = _user(connection)
        connection.execute(
            sa.text(
                "UPDATE application_policy SET shortest_retention_months = 13,"
                " retention_period_months = 13"
            )
        )
        # The database's clock at the same moment: a now() searched before pg_catalog.
        connection.execute(sa.text("CREATE SCHEMA review_clock"))
        connection.execute(
            sa.text(
                "CREATE FUNCTION review_clock.now() RETURNS timestamptz LANGUAGE sql STABLE"
                " AS $$ SELECT timestamptz '2027-02-28 18:00:00+00' $$"
            )
        )
    with head.begin() as connection:
        connection.execute(sa.text("SET LOCAL search_path = review_clock, pg_catalog, public"))
        assert connection.execute(sa.text("SELECT now()")).scalar_one() == moment
        run_id = _insert_run(connection, user_id, cutoff=cutoff.instant)
        _cancel(connection, run_id, user_id)
    # The trigger's newest cutoff at that moment is 2026-02-01 18:00 UTC: one hour later is refused.
    with (
        pytest.raises(IntegrityError, match="newer than the shortest allowed period"),
        head.begin() as connection,
    ):
        connection.execute(sa.text("SET LOCAL search_path = review_clock, pg_catalog, public"))
        _insert_run(
            connection,
            user_id,
            cutoff=datetime.datetime(2026, 2, 1, 19, 0, tzinfo=datetime.UTC),
        )


@pytest.mark.parametrize(
    "values",
    [
        {"state": "RUNNING", "started_at": datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)},
        {"started_at": datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)},
    ],
    ids=["running", "started"],
)
def test_a_run_is_requested_approved_with_no_progress(head: Engine, values: dict[str, Any]) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
    with pytest.raises(DBAPIError), head.begin() as connection:
        _insert_run(connection, user_id, **values)


def test_the_application_cancels_and_confirms_only(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        run_id = _insert_run(connection, user_id)
    by_id = {"id": run_id, "user": user_id}
    # The request never changes, even for the job.
    _refused(
        head,
        [("UPDATE history_archive_runs SET reason = 'other' WHERE id = :id", by_id)],
        "never changes",
    )
    _refused(
        head,
        [
            ("SELECT set_config('partflow.archive_run_id', CAST(:id AS text), true)", by_id),
            ("UPDATE history_archive_runs SET preview_flows = 9 WHERE id = :id", by_id),
        ],
        "never changes",
    )
    # A job transition without the run named in partflow.archive_run_id.
    _refused(
        head,
        [
            (
                "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now()"
                " WHERE id = :id",
                by_id,
            )
        ],
        "only the maintenance job",
    )
    # An impossible transition, even for the job.
    _refused(
        head,
        [
            ("SELECT set_config('partflow.archive_run_id', CAST(:id AS text), true)", by_id),
            (
                "UPDATE history_archive_runs SET state = 'NOTHING_ELIGIBLE', finished_at = now()"
                " WHERE id = :id",
                by_id,
            ),
        ],
        "cannot change APPROVED to NOTHING_ELIGIBLE",
    )
    for statement in ("DELETE FROM history_archive_runs", "TRUNCATE history_archive_runs CASCADE"):
        _refused(head, [(statement, {})], "never deleted")
    # The job moves it on; the application confirms the copy once.
    with head.begin() as connection:
        _running(connection, run_id)
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET archive_name = 'run-1-20271110T042000Z',"
                " exported_at = now(), exported_components = 1, exported_flows = 1,"
                " exported_movements = 1, exported_lineage_edges = 0,"
                " exported_assigned_routes = 0, archive_bytes = 1, manifest_sha256 = :sha"
                " WHERE id = :id"
            ),
            {"sha": "ab" * 32, "id": run_id},
        )
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET verified_at = now(), skipped_components = 0,"
                " skipped_flows = 0, state = 'WAITING_FOR_COPY' WHERE id = :id"
            ),
            {"id": run_id},
        )
    confirm = (
        "UPDATE history_archive_runs SET copy_confirmed_at = now(),"
        " copy_confirmed_by_user_id = :user WHERE id = :id"
    )
    with head.begin() as connection:
        connection.execute(sa.text(confirm), by_id)
    _refused(head, [(confirm, by_id)], "only the maintenance job")
    # Cancelling a waiting run is the application's; afterwards nothing changes.
    with head.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'CANCELLED', finished_at = now(),"
                " cancelled_by_user_id = :user WHERE id = :id"
            ),
            by_id,
        )
    _refused(
        head,
        [
            ("SELECT set_config('partflow.archive_run_id', CAST(:id AS text), true)", by_id),
            ("UPDATE history_archive_runs SET failure_reason = 'x' WHERE id = :id", by_id),
        ],
        "is finished",
    )


# ---------------------------------------------------------------------------
# The record guard
# ---------------------------------------------------------------------------


def test_the_record_guard(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        run_id = _insert_run(connection, user_id)
        flow_id = _flow(connection)
    record = _record(flow_id, run_id)
    # Not while APPROVED, and never without the job's setting.
    with (
        pytest.raises(DBAPIError, match="only while its run is RUNNING"),
        head.begin() as connection,
    ):
        _mark(connection, run_id)
        _insert_record(connection, record)
    with head.begin() as connection:
        _running(connection, run_id)
    with pytest.raises(DBAPIError, match="maintenance job of that run"), head.begin() as connection:
        _insert_record(connection, record)
    with head.begin() as connection:
        _mark(connection, run_id)
        _insert_record(connection, record)
    key = {"run": run_id, "flow": flow_id}
    marked = ("SELECT set_config('partflow.archive_run_id', CAST(:run AS text), true)", key)
    _refused(
        head,
        [marked, ("UPDATE history_archived_flows SET movement_count = 3 WHERE run_id = :run", key)],
        "never changes except",
    )
    _refused(
        head,
        [marked, ("UPDATE history_archived_flows SET purged_at = now() WHERE run_id = :run", key)],
        "while its run is PURGING",
    )
    with head.begin() as connection:
        _mark(connection, run_id)
        connection.execute(
            sa.text("UPDATE history_archived_flows SET skipped_at = now() WHERE run_id = :run"),
            key,
        )
    _refused(
        head,
        [marked, ("UPDATE history_archived_flows SET skipped_at = now() WHERE run_id = :run", key)],
        "marked skipped once",
    )
    for statement in ("DELETE FROM history_archived_flows", "TRUNCATE history_archived_flows"):
        _refused(head, [marked, (statement, {})], "never deleted")
    # The record keeps its flow row: the row guard of 0042 refuses the delete
    # first, and beneath it the record's foreign key refuses it too (the guard
    # is disabled only inside this rolled-back transaction).
    with (
        pytest.raises(DBAPIError, match="may be deleted only by the host job"),
        head.begin() as connection,
    ):
        connection.execute(sa.text("DELETE FROM quantity_flows WHERE id = :flow"), key)
    with pytest.raises(IntegrityError), head.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE quantity_flows DISABLE TRIGGER trg_quantity_flows_guard_delete")
        )
        connection.execute(sa.text("DELETE FROM quantity_flows WHERE id = :flow"), key)


# ---------------------------------------------------------------------------
# The database roles
# ---------------------------------------------------------------------------


@pytest.fixture
def roles(admin_engine: Engine, head_database: URL) -> Iterator[DatabaseRoles]:
    created = temporary_roles()
    with admin_engine.connect() as connection:
        for name in created:
            connection.execute(sa.text(f'CREATE ROLE "{name}" NOLOGIN'))
    try:
        engine = create_engine(head_database)
        try:
            with engine.begin() as connection:
                database_roles.apply_grants(connection, roles=created, required=True)
        finally:
            engine.dispose()
        yield created
    finally:
        drop_roles(make_url(os.environ["DATABASE_URL"]), tuple(created))


def _as(engine: Engine, role: str, statements: list[tuple[str, dict[str, Any]]]) -> str | None:
    """The SQLSTATE of the first refused statement run as ``role`` (None: all ran); rolled back."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text(f'SET LOCAL ROLE "{role}"'))
            for sql, params in statements:
                connection.execute(sa.text(sql), params)
            return None
        except DBAPIError as exc:
            return str(getattr(exc.orig, "sqlstate", None))
        finally:
            transaction.rollback()


_AUDIT_ROW = (
    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, before_data,"
    " after_data) VALUES ('UPDATED', :type, '1', now(), '{}', '{}')"
)


def test_the_roles_split_the_runs_between_the_application_and_the_job(
    head: Engine, roles: DatabaseRoles
) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        run_id = _insert_run(connection, user_id)
        flow_id = _flow(connection)
    by_id = {"id": run_id, "user": user_id}
    marked = ("SELECT set_config('partflow.archive_run_id', CAST(:id AS text), true)", by_id)
    start = (
        "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now() WHERE id = :id",
        by_id,
    )
    record = _record(flow_id, run_id)
    insert_record = (
        "INSERT INTO history_archived_flows ("
        + ", ".join(record)
        + ") VALUES ("
        + ", ".join(f":{column}" for column in record)
        + ")",
        record,
    )
    # The application role: no job transition even with the setting, no record.
    assert _as(head, roles.app, [marked, start]) == "P0001"
    assert _as(head, roles.app, [insert_record]) == "42501"
    assert (
        _as(
            head,
            roles.app,
            [
                (
                    "UPDATE history_archive_runs SET state = 'CANCELLED', finished_at = now(),"
                    " cancelled_by_user_id = :user WHERE id = :id",
                    by_id,
                )
            ],
        )
        is None
    )
    assert _as(head, roles.app, [(_AUDIT_ROW, {"type": "ApplicationPolicy"})]) is None
    # The maintenance role: the job's changes with the setting, records, its own audit rows.
    assert _as(head, roles.maintenance, [marked, start, insert_record]) is None
    assert _as(head, roles.maintenance, [start]) == "P0001"
    # It never requests a run, confirms a copy or cancels, with the setting or without it.
    # Since step 4 (0043) it holds INSERT for automatic runs only: the run guard refuses a
    # requested (manual) run from it.
    request = (
        "INSERT INTO history_archive_runs (state, cutoff, reason, requested_by_user_id,"
        " preview_components, preview_flows, preview_movements, preview_archive_bytes)"
        " VALUES ('APPROVED', :cutoff, 'job', :user, 1, 1, 1, 0)",
        {"cutoff": _old_cutoff(), "user": user_id},
    )
    assert _as(head, roles.maintenance, [request]) == "P0001"
    cancel = (
        "UPDATE history_archive_runs SET state = 'CANCELLED', finished_at = now(),"
        " cancelled_by_user_id = :user WHERE id = :id",
        by_id,
    )
    assert _as(head, roles.maintenance, [cancel]) == "P0001"
    assert _as(head, roles.maintenance, [marked, cancel]) == "P0001"
    exported: dict[str, Any] = {**by_id, "sha": "ab" * 32}
    waiting: list[tuple[str, dict[str, Any]]] = [
        marked,
        start,
        (
            "UPDATE history_archive_runs SET archive_name = 'run-1-20271110T042000Z',"
            " exported_at = now(), exported_components = 1, exported_flows = 1,"
            " exported_movements = 1, exported_lineage_edges = 0, exported_assigned_routes = 0,"
            " archive_bytes = 1, manifest_sha256 = :sha WHERE id = :id",
            exported,
        ),
        (
            "UPDATE history_archive_runs SET verified_at = now(), skipped_components = 0,"
            " skipped_flows = 0, state = 'WAITING_FOR_COPY' WHERE id = :id",
            by_id,
        ),
    ]
    assert _as(head, roles.maintenance, waiting) is None
    confirm = (
        "UPDATE history_archive_runs SET copy_confirmed_at = now(),"
        " copy_confirmed_by_user_id = :user WHERE id = :id",
        by_id,
    )
    unmarked: tuple[str, dict[str, Any]] = (
        "SELECT set_config('partflow.archive_run_id', '', true)",
        {},
    )
    assert _as(head, roles.maintenance, [*waiting, confirm]) == "P0001"
    assert _as(head, roles.maintenance, [*waiting, unmarked, confirm]) == "P0001"
    # Nor completes a run that was neither copied nor confirmed — since step 2 (0041) no
    # run ends EXPORT_ONLY any more, copied or not (the run guard).
    complete = (
        "UPDATE history_archive_runs SET state = 'COMPLETED', result = 'EXPORT_ONLY',"
        " finished_at = now() WHERE id = :id",
        by_id,
    )
    assert _as(head, roles.maintenance, [*waiting, complete]) == "P0001"
    copied = ("UPDATE history_archive_runs SET copied_at = now() WHERE id = :id", by_id)
    assert _as(head, roles.maintenance, [*waiting, copied, complete]) == "P0001"
    assert _as(head, roles.maintenance, [(_AUDIT_ROW, {"type": "HistoryArchiveRun"})]) is None
    assert _as(head, roles.maintenance, [(_AUDIT_ROW, {"type": "ApplicationPolicy"})]) == "P0001"
    # Since step 3 it reads the audit rows (the plan of a permanent deletion does).
    assert _as(head, roles.maintenance, [("SELECT 1 FROM audit_events LIMIT 1", {})]) is None
    # Since step 2 it may delete Movements, as the purge's row guard allows (none here).
    assert _as(head, roles.maintenance, [("DELETE FROM part_movements WHERE false", {})]) is None
    assert _as(
        head, roles.maintenance, [("UPDATE quantity_flows SET id = id WHERE false", {})]
    ) == ("42501")
    assert _as(head, roles.maintenance, [("SELECT 1 FROM user_credentials", {})]) == "42501"
    assert (
        _as(head, roles.maintenance, [("SELECT 1 FROM work_orders, assigned_route_steps", {})])
        is None
    )


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def _columns(engine: Engine, table: str) -> set[str]:
    return {str(column["name"]) for column in inspect(engine).get_columns(table)}


def test_the_downgrade_restores_the_previous_schema(head_database: URL, head: Engine) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
        assert all(_KEY not in keys for keys in _grants(connection).values())
        # This revision's revocations (0042's downgrade revokes its own key first).
        revoked = [
            row
            for row in _audits(connection, "Role")
            if row["metadata"].get("downgrade") and row["metadata"]["revision"] == _HEAD_REVISION
        ]
        assert len(revoked) == 2
        assert _section_previous(connection) == 24
    tables = set(inspect(head).get_table_names())
    assert not {"history_archive_runs", "history_archived_flows"} & tables
    assert not {
        "shortest_retention_months",
        "archive_max_wait_days",
        "archive_start_minute",
        "archive_preview_seconds",
    } & _columns(head, "application_policy")
    with pytest.raises(IntegrityError), head.begin() as connection:
        connection.execute(sa.text("UPDATE application_policy SET retention_period_months = 11"))
    command.upgrade(_alembic_config(head_database), "head")
    with head.connect() as connection:
        assert _version(connection) == _CODE_HEAD
        assert _KEY in _grants(connection)["Administrator"]


def _section_previous(connection: Connection) -> int | None:
    value = connection.execute(
        sa.text("SELECT retention_period_months FROM application_policy")
    ).scalar_one()
    return None if value is None else int(value)


_DOWNGRADE_TRIGGERS = {
    "run": "run",
    "archive-audit-row": (
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, after_data)"
        " VALUES ('CREATED', 'HistoryArchiveRun', '1', now(), '{}')"
    ),
    "waiting-limit": "UPDATE application_policy SET archive_max_wait_days = 30",
    "start-minute": "UPDATE application_policy SET archive_start_minute = 5",
    "preview-limit": "UPDATE application_policy SET archive_preview_seconds = 300",
    "shortest-period": (
        "UPDATE application_policy SET shortest_retention_months = 24, retention_period_months = 24"
    ),
}


@pytest.mark.parametrize("trigger", list(_DOWNGRADE_TRIGGERS))
def test_the_downgrade_refuses_while_archival_data_exists(
    head_database: URL, head: Engine, trigger: str
) -> None:
    with head.begin() as connection:
        if trigger == "run":
            _insert_run(connection, _user(connection))
        else:
            connection.execute(sa.text(_DOWNGRADE_TRIGGERS[trigger]))
    with pytest.raises(DBAPIError, match="History archival data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert _version(connection) == _CODE_HEAD


def test_the_role_snapshot_matches_the_roles_module() -> None:
    """The migration's audited role facet is the application's (``roles.role_snapshot``)."""
    from app.application import roles as roles_module

    migration = _load(_MIGRATION_FILE, "history_archive_migration_snapshot")
    keys = [Permission.UNDO_RECENT_SCANS, Permission.MANAGE_AREAS]
    assert migration._role_snapshot("R", [key.value for key in keys]) == roles_module.role_snapshot(
        "R", keys
    )
