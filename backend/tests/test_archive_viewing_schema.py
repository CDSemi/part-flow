"""Integration tests for migration ``0044_phase16_archive_viewing`` (history archival, step 5).

Runs the real Alembic chain against isolated, temporary PostgreSQL databases
(created and dropped by the fixtures) and verifies:

- the exact head boundary (``0044_phase16_archive_viewing`` is the single
  head, and the models match the schema migrated to it) and the migration's
  literals against the models, the enums, the application modules and
  migration 0042 (whose audit scope it replaces, and restores on its
  downgrade);
- a fresh install: the viewing settings at their defaults, the pinned guards;
- the CHECKs of the settings and of the views;
- the views' guard: a person's request only (never the maintenance role),
  REQUESTED with no progress, of a finished run whose archive was verified,
  within the setting of runs loaded at once and one active view per run;
  the application role cancels a REQUESTED view and closes an OPEN one and
  changes nothing else; the job's transitions while the marker names the
  view, a view ending FAILED or CLOSED only without rows; the request and
  the recorded progress never change; nothing is deleted or truncated — as
  the owner, the maintenance role and the application role (temporary roles
  granted by ``apply-grants``);
- the viewer tables' statement guards: every insert and delete by each role,
  the maintenance role touching only the rows of the view it works on
  (LOADING for an insert, LOADING or CLOSING for a delete); no update and no
  truncate for anyone;
- the audit scope: the maintenance role appends the ``HistoryArchiveView``
  rows of the view it works on only;
- the downgrade restores 0043 exactly and refuses while step-5 data exists.
"""

import datetime
import hashlib
import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.application import database_roles, history_archive_views, policies
from app.application.database_roles import DatabaseRoles
from app.domain.enums import AuditEntityType, HistoryArchiveViewState
from app.infrastructure import models, schema_revision
from app.infrastructure.database_privileges import (
    GUARD_FUNCTION_SHA256,
    GUARD_TRIGGERS,
    MAINTENANCE_PRIVILEGES,
    TABLE_CLASSES,
    TableClass,
)
from tests.role_harness import drop_roles, temporary_roles

# Schema, trigger and role assertions: owner connections in the
# application-role test mode (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_VERSIONS = _BACKEND_DIR / "alembic" / "versions"
_PREVIOUS_REVISION = "0043_phase16_automatic_runs"
_HEAD_REVISION = "0044_phase16_archive_viewing"
_MIGRATION_FILE = _VERSIONS / "20261118_0044_phase16_archive_viewing.py"
_DELETION_FILE = _VERSIONS / "20261118_0042_phase16_deletion_requests.py"
_HEAD_TEMPLATE = "partflow_test_archive_viewing_schema_head"
_CASE = "partflow_test_archive_viewing_schema_case"
_VIEWS_FUNCTION = "partflow_history_archive_views_guard_mutation"
_ROWS_FUNCTION = "partflow_archive_view_rows_guard"
_AUDIT_FUNCTION = "partflow_audit_events_maintenance_scope"
_CUTOFF = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _url(name: str) -> URL:
    return make_url(os.environ["DATABASE_URL"]).set(database=name)


def _scalar(connection: Connection, sql: str, **params: object) -> Any:
    return connection.execute(sa.text(sql), params).scalar_one()


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


def _create_database(admin: Engine, name: str, template: str | None = None) -> None:
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        suffix = f' TEMPLATE "{template}"' if template else ""
        connection.execute(sa.text(f'CREATE DATABASE "{name}"{suffix}'))


def _drop_database(admin: Engine, name: str) -> None:
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def head_template(admin_engine: Engine) -> Iterator[str]:
    _create_database(admin_engine, _HEAD_TEMPLATE)
    command.upgrade(_alembic_config(_url(_HEAD_TEMPLATE)), "head")
    yield _HEAD_TEMPLATE
    _drop_database(admin_engine, _HEAD_TEMPLATE)


@pytest.fixture
def head_database(admin_engine: Engine, head_template: str) -> Iterator[URL]:
    _create_database(admin_engine, _CASE, template=head_template)
    yield _url(_CASE)
    _drop_database(admin_engine, _CASE)


@pytest.fixture
def head(head_database: URL) -> Iterator[Engine]:
    engine = create_engine(head_database)
    yield engine
    engine.dispose()


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


Statements = list[tuple[str, dict[str, Any]]]


def _attempt(
    engine: Engine, statements: Statements, role: str | None = None
) -> tuple[str | None, str]:
    """(SQLSTATE of the first refused statement or None, its message); always rolled back."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if role is not None:
                connection.execute(sa.text(f'SET LOCAL ROLE "{role}"'))
            for sql, params in statements:
                connection.execute(sa.text(sql), params)
            return None, ""
        except DBAPIError as exc:
            return str(getattr(exc.orig, "sqlstate", None)), str(exc.orig)
        finally:
            transaction.rollback()


def _refused(
    engine: Engine,
    statements: Statements,
    match: str,
    role: str | None = None,
    sqlstate: str = "P0001",
) -> None:
    state, message = _attempt(engine, statements, role)
    assert state == sqlstate, message
    assert match in message, message


def _admitted(engine: Engine, statements: Statements, role: str | None = None) -> None:
    state, message = _attempt(engine, statements, role)
    assert state is None, message


# ---------------------------------------------------------------------------
# Head and literals
# ---------------------------------------------------------------------------


def test_alembic_has_a_single_head() -> None:
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    assert ScriptDirectory.from_config(config).get_heads() == [_HEAD_REVISION]
    assert schema_revision.code_head() == _HEAD_REVISION
    assert schema_revision.non_transactional_revisions([_HEAD_REVISION]) == []


def test_models_metadata_matches_the_migrated_schema(head: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with head.connect() as connection:
        context = MigrationContext.configure(connection)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def _model_checks(model: type[models.Base]) -> dict[str, str]:
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in cast(sa.Table, model.__table__).constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def test_the_migration_literals_match_the_models_and_0042() -> None:
    migration = _load(_MIGRATION_FILE, "archive_viewing_migration")
    deletion = _load(_DELETION_FILE, "deletion_requests_migration_for_0044")
    assert (migration.revision, migration.down_revision) == (_HEAD_REVISION, _PREVIOUS_REVISION)
    assert tuple(state.value for state in HistoryArchiveViewState) == migration._STATES
    assert migration._FINAL_STATES == models.HISTORY_ARCHIVE_VIEW_FINAL_STATES
    assert migration._ACTIVE_STATES == models.HISTORY_ARCHIVE_VIEW_ACTIVE_STATES
    assert migration._RUN_FINAL_STATES == models.HISTORY_ARCHIVE_FINAL_STATES
    assert migration._NAME_KINDS == models.ARCHIVE_VIEW_NAME_KINDS
    assert migration._VIEWER_TABLES == models.ARCHIVE_VIEWER_TABLES
    assert migration._CANONICAL_PART_NUMBER_SQL == models.CANONICAL_PART_NUMBER_SQL
    assert migration._CANONICAL_NAME_KEY_SQL == models.ARCHIVE_VIEW_NAME_KEY_SQL
    assert migration._in("state", migration._ACTIVE_STATES) == (
        models.HISTORY_ARCHIVE_VIEW_ACTIVE_WHERE_SQL
    )
    view_checks = _model_checks(models.HistoryArchiveView)
    for suffix, sql in migration._VIEW_CHECKS:
        assert view_checks[f"ck_history_archive_views_{suffix}"] == sql, suffix
    assert _model_checks(models.ArchiveViewName) == {
        "ck_archive_view_names_kind": migration._in("kind", migration._NAME_KINDS),
        "ck_archive_view_names_part_number_canonical": migration._CANONICAL_NAME_KEY_SQL,
    }
    for model in (models.ArchiveViewMovement, models.ArchiveViewFlow):
        assert list(_model_checks(model).values()) == [migration._CANONICAL_PART_NUMBER_SQL]
    # The settings: their defaults, ranges and CHECKs.
    policy_checks = _model_checks(models.ApplicationPolicy)
    policy_table = cast(sa.Table, models.ApplicationPolicy.__table__)
    assert {name: default for name, _type, default in migration._POLICY_COLUMNS} == {
        "archive_view_days": str(models.ARCHIVE_VIEW_DAYS_DEFAULT),
        "archive_view_max_loaded": str(models.ARCHIVE_VIEW_MAX_LOADED_DEFAULT),
    }
    for column, default, minimum, maximum in migration._POLICY_RANGES:
        assert models.HISTORY_ARCHIVE_POLICY_RANGES[column] == (minimum, maximum)
        assert policy_checks[f"ck_application_policy_{column}_range"] == (
            f"{column} BETWEEN {minimum} AND {maximum}"
        )
        server_default = cast(sa.DefaultClause, policy_table.c[column].server_default)
        assert str(server_default.arg.text) == str(default)  # type: ignore[union-attr]
    # The audited section ends with the two viewing settings.
    assert policies.HISTORY_ARCHIVE_COLUMNS[-2:] == ("archive_view_days", "archive_view_max_loaded")
    # The audit vocabulary and the replaced audit scope (0042 restored on downgrade).
    assert migration._PREVIOUS_ENTITY_TYPES == deletion._ENTITY_TYPES
    assert deletion._ENTITY_TYPES[:-1] + ", 'HistoryArchiveView')" == migration._ENTITY_TYPES
    assert AuditEntityType.HISTORY_ARCHIVE_VIEW.value == "HistoryArchiveView"
    assert migration._PREVIOUS_AUDIT_BODY == deletion._AUDIT_BODY
    # The lock and the refusal the application shares with the guard.
    assert migration._LIMIT_LOCK == history_archive_views.LIMIT_LOCK
    assert migration._LIMIT_GUARD == history_archive_views.LIMIT_GUARD
    assert migration._ACTIVE_INDEX == history_archive_views.ACTIVE_INDEX
    # The guard functions are pinned by check (h), and so is every trigger.
    for function, body in (
        (_VIEWS_FUNCTION, migration._VIEWS_BODY),
        (_ROWS_FUNCTION, migration._ROWS_BODY),
        (_AUDIT_FUNCTION, migration._AUDIT_BODY),
    ):
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == GUARD_FUNCTION_SHA256[function]
    for name, _statement, table in migration._TRIGGERS:
        assert GUARD_TRIGGERS[name].table == table, name
    # The grant classes and the maintenance role's privileges.
    assert TABLE_CLASSES["history_archive_views"] is TableClass.GUARDED_UPDATE
    assert MAINTENANCE_PRIVILEGES["history_archive_views"] == {"SELECT", "UPDATE"}
    for table in models.ARCHIVE_VIEWER_TABLES:
        assert TABLE_CLASSES[table] is TableClass.READ_ONLY, table
        assert MAINTENANCE_PRIVILEGES[table] == {"SELECT", "INSERT", "DELETE"}, table


def test_a_fresh_install_holds_the_defaults_and_the_pinned_guards(head: Engine) -> None:
    with head.connect() as connection:
        days, loaded = connection.execute(
            sa.text("SELECT archive_view_days, archive_view_max_loaded FROM application_policy")
        ).one()
        sources = {
            str(name): str(source)
            for name, source in connection.execute(
                sa.text("SELECT proname, prosrc FROM pg_proc WHERE proname = ANY(:names)"),
                {"names": [_VIEWS_FUNCTION, _ROWS_FUNCTION, _AUDIT_FUNCTION]},
            )
        }
    assert (days, loaded) == (7, 1)
    for name, source in sources.items():
        assert hashlib.sha256(source.encode("utf-8")).hexdigest() == GUARD_FUNCTION_SHA256[name]
    assert len(sources) == 3


def test_the_viewer_tables_keep_no_statistics_of_their_views(head: Engine) -> None:
    """The planner never sees a freshly loaded view as empty (the step 5 rehearsal)."""
    with head.connect() as connection:
        targets = {
            str(table): int(target)
            for table, target in connection.execute(
                sa.text(
                    "SELECT c.relname, a.attstattarget FROM pg_attribute a"
                    " JOIN pg_class c ON c.oid = a.attrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relname = ANY(:tables)"
                    " AND a.attname = 'view_id'"
                ),
                {"tables": list(models.ARCHIVE_VIEWER_TABLES)},
            )
        }
    assert targets == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)


# ---------------------------------------------------------------------------
# Data helpers (direct SQL as the owner)
# ---------------------------------------------------------------------------


def _user(connection: Connection, login: str = "ann") -> int:
    role_id = _scalar(connection, "SELECT min(id) FROM roles")
    return int(
        _scalar(
            connection,
            "INSERT INTO users (login_name, display_name, role_id) VALUES (:login, :login, :role)"
            " RETURNING id",
            login=login,
            role=role_id,
        )
    )


def _run_marker(run_id: int) -> str:
    return f"SELECT set_config('partflow.archive_run_id', '{run_id}', true)"


def _viewable_run(connection: Connection, user_id: int, *, verified: bool = True) -> int:
    """A finished run (FAILED after its verification, or before it): owner SQL as the job."""
    run_id = int(
        _scalar(
            connection,
            "INSERT INTO history_archive_runs (state, trigger, cutoff, reason,"
            " requested_by_user_id, preview_components, preview_flows, preview_movements,"
            " preview_archive_bytes) VALUES ('APPROVED', 'MANUAL', :cutoff, 'test', :user,"
            " 1, 1, 1, 0) RETURNING id",
            cutoff=_CUTOFF,
            user=user_id,
        )
    )
    connection.execute(sa.text(_run_marker(run_id)))
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now() WHERE id = :id"
        ),
        {"id": run_id},
    )
    if verified:
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET archive_name ="
                " 'run-' || id || '-20271110T042000Z',"
                " exported_at = now(), exported_components = 1, exported_flows = 1,"
                " exported_movements = 1, exported_lineage_edges = 0, exported_assigned_routes = 0,"
                " archive_bytes = 1, manifest_sha256 = repeat('ab', 32), verified_at = now(),"
                " skipped_components = 0, skipped_flows = 0 WHERE id = :id"
            ),
            {"id": run_id},
        )
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET state = 'FAILED', failure_reason = 'test',"
            " finished_at = now() WHERE id = :id"
        ),
        {"id": run_id},
    )
    return run_id


_VIEW_INSERT = (
    "INSERT INTO history_archive_views (run_id, state, requested_by_user_id)"
    " VALUES (:run, 'REQUESTED', :user)"
)


def _view(connection: Connection, run_id: int, user_id: int) -> int:
    return int(_scalar(connection, _VIEW_INSERT + " RETURNING id", run=run_id, user=user_id))


def _marker(view_id: int) -> tuple[str, dict[str, Any]]:
    return (
        "SELECT set_config('partflow.archive_view_id', CAST(:id AS text), true)",
        {"id": view_id},
    )


def _move(connection: Connection, view_id: int, sql: str) -> None:
    """A job change of the view (owner, marked)."""
    connection.execute(sa.text(_marker(view_id)[0]), _marker(view_id)[1])
    connection.execute(
        sa.text(f"UPDATE history_archive_views SET {sql} WHERE id = :id"), {"id": view_id}
    )


_OPEN = (
    "state = 'OPEN', opened_at = now(), loaded_movements = 0, loaded_flows = 0,"
    " loaded_lineage_edges = 0, loaded_assigned_routes = 0, loaded_names = 0"
)


def _name_row(view_id: int, key: str = "1") -> tuple[str, dict[str, Any]]:
    return (
        "INSERT INTO archive_view_names (view_id, kind, key, value)"
        ' VALUES (:view, \'areas\', :key, \'{"id": 1, "name": "MAT"}\')',
        {"view": view_id, "key": key},
    )


@pytest.fixture
def setup(head: Engine) -> dict[str, int]:
    """Two finished, verified runs and a User; the setting allows two loaded runs."""
    with head.begin() as connection:
        user_id = _user(connection)
        first = _viewable_run(connection, user_id)
        second = _viewable_run(connection, user_id)
        connection.execute(sa.text("UPDATE application_policy SET archive_view_max_loaded = 2"))
    return {"user": user_id, "first": first, "second": second}


# ---------------------------------------------------------------------------
# The CHECKs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("assignment", "admitted"),
    [
        ("archive_view_days = 1", True),
        ("archive_view_days = 365", True),
        ("archive_view_days = 0", False),
        ("archive_view_days = 366", False),
        ("archive_view_max_loaded = 1", True),
        ("archive_view_max_loaded = 10", True),
        ("archive_view_max_loaded = 0", False),
        ("archive_view_max_loaded = 11", False),
    ],
)
def test_the_setting_checks(head: Engine, assignment: str, admitted: bool) -> None:
    statement: Statements = [(f"UPDATE application_policy SET {assignment}", {})]
    if admitted:
        _admitted(head, statement)
    else:
        _refused(head, statement, "ck_application_policy_archive_view_", sqlstate="23514")


def test_the_view_checks(head: Engine, setup: dict[str, int]) -> None:
    with head.begin() as connection:
        view_id = _view(connection, setup["first"], setup["user"])
    marker = _marker(view_id)
    # OPEN needs the loaded counts; FAILED a reason; CANCELLED who cancelled it.
    _refused(
        head,
        [
            marker,
            ("UPDATE history_archive_views SET state = 'LOADING', started_at = now()", {}),
            ("UPDATE history_archive_views SET state = 'OPEN', opened_at = now()", {}),
        ],
        "ck_history_archive_views_loaded_shape",
        sqlstate="23514",
    )
    _refused(
        head,
        [marker, ("UPDATE history_archive_views SET state = 'FAILED', finished_at = now()", {})],
        "ck_history_archive_views_failure_shape",
        sqlstate="23514",
    )
    _refused(
        head,
        [("UPDATE history_archive_views SET state = 'CANCELLED', finished_at = now()", {})],
        "ck_history_archive_views_finish_shape",
        sqlstate="23514",
    )
    _refused(
        head,
        [
            (
                "INSERT INTO archive_view_names (view_id, kind, key, value)"
                " VALUES (1, 'runs', '1', '{}')",
                {},
            )
        ],
        "ck_archive_view_names_kind",
        sqlstate="23514",
    )


# ---------------------------------------------------------------------------
# The views' guard
# ---------------------------------------------------------------------------


def test_a_view_is_requested_by_a_person_only(
    head: Engine, setup: dict[str, int], roles: DatabaseRoles
) -> None:
    insert = [(_VIEW_INSERT, {"run": setup["first"], "user": setup["user"]})]
    _admitted(head, insert)
    _admitted(head, insert, role=roles.app)
    # The maintenance role holds no INSERT on the views; were it granted one, the
    # guard would still refuse it (the grant rolls back with the attempt).
    _refused(head, insert, "permission denied", role=roles.maintenance, sqlstate="42501")
    with head.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                sa.text(f'GRANT INSERT ON history_archive_views TO "{roles.maintenance}"')
            )
            connection.execute(sa.text(f'SET LOCAL ROLE "{roles.maintenance}"'))
            with pytest.raises(DBAPIError, match="only a person opens an archive for viewing"):
                connection.execute(sa.text(_VIEW_INSERT), insert[0][1])
        finally:
            transaction.rollback()
    # REQUESTED, with no progress, at the start of its transaction.
    _refused(
        head,
        [
            (
                "INSERT INTO history_archive_views (run_id, state, requested_by_user_id,"
                " started_at) VALUES (:run, 'LOADING', :user, now())",
                {"run": setup["first"], "user": setup["user"]},
            )
        ],
        "requested REQUESTED, with no progress",
        role=roles.app,
    )
    _refused(
        head,
        [
            (
                "INSERT INTO history_archive_views (run_id, state, requested_by_user_id,"
                " requested_at) VALUES (:run, 'REQUESTED', :user, now() - interval '1 day')",
                {"run": setup["first"], "user": setup["user"]},
            )
        ],
        "at the start of its transaction",
        role=roles.app,
    )


def test_only_a_finished_verified_run_is_viewable(head: Engine, setup: dict[str, int]) -> None:
    with head.begin() as connection:
        unverified = _viewable_run(connection, setup["user"], verified=False)
        open_run = int(
            _scalar(
                connection,
                "INSERT INTO history_archive_runs (state, trigger, cutoff, reason,"
                " requested_by_user_id, preview_components, preview_flows, preview_movements,"
                " preview_archive_bytes) VALUES ('APPROVED', 'MANUAL', :cutoff, 'test', :user,"
                " 1, 1, 1, 0) RETURNING id",
                cutoff=_CUTOFF,
                user=setup["user"],
            )
        )
    for run_id in (unverified, open_run):
        _refused(
            head,
            [(_VIEW_INSERT, {"run": run_id, "user": setup["user"]})],
            f"run {run_id} is not a finished run whose archive was verified",
        )


def test_one_active_view_per_run_and_the_setting_of_loaded_runs(
    head: Engine, setup: dict[str, int], roles: DatabaseRoles
) -> None:
    with head.begin() as connection:
        third = _viewable_run(connection, setup["user"])
        _view(connection, setup["first"], setup["user"])
    # A second active view of the same run: the unique index.
    _refused(
        head,
        [(_VIEW_INSERT, {"run": setup["first"], "user": setup["user"]})],
        "uq_history_archive_views_run_active",
        role=roles.app,
        sqlstate="23505",
    )
    with head.begin() as connection:
        _view(connection, setup["second"], setup["user"])
    # The setting allows two: a third is refused by the guard (23514, its name).
    _refused(
        head,
        [(_VIEW_INSERT, {"run": third, "user": setup["user"]})],
        "at most 2 runs may be loaded for viewing at once, and 2 are",
        role=roles.app,
        sqlstate="23514",
    )
    with head.begin() as connection:
        connection.execute(sa.text("UPDATE application_policy SET archive_view_max_loaded = 3"))
    _admitted(head, [(_VIEW_INSERT, {"run": third, "user": setup["user"]})], role=roles.app)


def test_the_job_moves_a_view_while_the_marker_names_it(
    head: Engine, setup: dict[str, int], roles: DatabaseRoles
) -> None:
    with head.begin() as connection:
        view_id = _view(connection, setup["first"], setup["user"])
    marker = _marker(view_id)
    loading: tuple[str, dict[str, Any]] = (
        "UPDATE history_archive_views SET state = 'LOADING', started_at = now()",
        {},
    )
    # Without the marker, the maintenance role changes nothing; with it, the job's steps.
    _refused(head, [loading], "changes a view only as its job", role=roles.maintenance)
    _refused(head, [loading], "only the host job makes this change", role=roles.app)
    _refused(head, [marker, loading], "only the host job makes this change", role=roles.app)
    _admitted(head, [marker, loading], role=roles.maintenance)
    for role in (None, roles.maintenance):
        _admitted(
            head,
            [
                marker,
                loading,
                (f"UPDATE history_archive_views SET {_OPEN}", {}),
                (
                    "UPDATE history_archive_views SET state = 'CLOSING',"
                    " closing_started_at = now()",
                    {},
                ),
                ("UPDATE history_archive_views SET state = 'CLOSED', finished_at = now()", {}),
            ],
            role=role,
        )
    # A job never skips a step, never cancels, never closes for a person.
    _refused(
        head,
        [marker, (f"UPDATE history_archive_views SET {_OPEN}, started_at = now()", {})],
        "cannot change REQUESTED to OPEN",
        role=roles.maintenance,
    )
    _refused(
        head,
        [
            marker,
            (
                "UPDATE history_archive_views SET state = 'CANCELLED', finished_at = now(),"
                " cancelled_by_user_id = :user",
                {"user": setup["user"]},
            ),
        ],
        "never cancels a view or closes it for a person",
        role=roles.maintenance,
    )
    # A view ends FAILED only once its rows are gone.
    _refused(
        head,
        [
            marker,
            loading,
            _name_row(view_id),
            (
                "UPDATE history_archive_views SET state = 'FAILED', failure_reason = 'x',"
                " finished_at = now()",
                {},
            ),
        ],
        f"view {view_id}: its loaded rows are removed before it ends FAILED",
        role=roles.maintenance,
    )
    _admitted(
        head,
        [
            marker,
            loading,
            _name_row(view_id),
            ("DELETE FROM archive_view_names WHERE view_id = :view", {"view": view_id}),
            (
                "UPDATE history_archive_views SET state = 'FAILED', failure_reason = 'x',"
                " finished_at = now()",
                {},
            ),
        ],
        role=roles.maintenance,
    )


def test_the_application_cancels_and_closes_only(
    head: Engine, setup: dict[str, int], roles: DatabaseRoles
) -> None:
    with head.begin() as connection:
        requested = _view(connection, setup["first"], setup["user"])
        opened = _view(connection, setup["second"], setup["user"])
        _move(connection, opened, "state = 'LOADING', started_at = now()")
        _move(connection, opened, _OPEN)
    cancel = (
        "UPDATE history_archive_views SET state = 'CANCELLED', finished_at = now(),"
        " cancelled_by_user_id = :user WHERE id = :id"
    )
    close = (
        "UPDATE history_archive_views SET state = 'CLOSING', closing_started_at = now(),"
        " closed_by_user_id = :user WHERE id = :id"
    )
    _admitted(head, [(cancel, {"user": setup["user"], "id": requested})], role=roles.app)
    _admitted(head, [(close, {"user": setup["user"], "id": opened})], role=roles.app)
    _refused(
        head,
        [(close, {"user": setup["user"], "id": requested})],
        "only the host job makes this change",
        role=roles.app,
    )
    _refused(
        head,
        [(cancel, {"user": setup["user"], "id": opened})],
        "only the host job makes this change",
        role=roles.app,
    )
    # Closing names the person; a close by the job (the days) names nobody.
    _refused(
        head,
        [
            (
                "UPDATE history_archive_views SET state = 'CLOSING', closing_started_at = now()"
                " WHERE id = :id",
                {"id": opened},
            )
        ],
        "only the host job makes this change",
        role=roles.app,
    )
    # The maintenance role cancels nothing, even with the marker.
    _refused(
        head,
        [_marker(requested), (cancel, {"user": setup["user"], "id": requested})],
        "never cancels a view or closes it for a person",
        role=roles.maintenance,
    )


def test_the_request_and_the_progress_never_change_and_nothing_is_deleted(
    head: Engine, setup: dict[str, int], roles: DatabaseRoles
) -> None:
    with head.begin() as connection:
        view_id = _view(connection, setup["first"], setup["user"])
        _move(connection, view_id, "state = 'LOADING', started_at = now()")
        _move(connection, view_id, _OPEN)
    marker = _marker(view_id)
    _refused(
        head,
        [marker, ("UPDATE history_archive_views SET run_id = :run", {"run": setup["second"]})],
        "the request of view",
    )
    _refused(
        head,
        [
            marker,
            (
                "UPDATE history_archive_views SET state = 'CLOSING', closing_started_at = now(),"
                " loaded_movements = 5",
                {},
            ),
        ],
        "its progress is recorded once",
    )
    _refused(
        head,
        [("DELETE FROM history_archive_views WHERE id = :id", {"id": view_id})],
        "never deleted",
    )
    _refused(head, [("TRUNCATE history_archive_views", {})], "never deleted")
    for role in (roles.app, roles.maintenance):
        _refused(
            head,
            [("DELETE FROM history_archive_views", {})],
            "permission denied",
            role=role,
            sqlstate="42501",
        )
    with head.begin() as connection:
        _move(connection, view_id, "state = 'CLOSING', closing_started_at = now()")
        _move(connection, view_id, "state = 'CLOSED', finished_at = now()")
    _refused(
        head,
        [marker, ("UPDATE history_archive_views SET failure_reason = 'x'", {})],
        "is finished (CLOSED) and never changes",
    )


# ---------------------------------------------------------------------------
# The viewer tables' guards
# ---------------------------------------------------------------------------

_ROWS: dict[str, tuple[str, dict[str, Any]]] = {
    "archive_view_movements": (
        "INSERT INTO archive_view_movements (view_id, id, quantity_flow_id, part_number,"
        " movement_type, quantity, to_area_id, operation_id, occurred_at, server_received_at,"
        " device_event_id, command_sequence) VALUES (:view, :key, 1, 'PN-1', 'RECEIVED', 1, 1,"
        " 1, now(), now(), 'e', 1)",
        {},
    ),
    "archive_view_flows": (
        "INSERT INTO archive_view_flows (view_id, flow_id, component_id, part_number, status,"
        " quantity, route_mode, created_at, flow, record) VALUES (:view, :key, 1, 'PN-1',"
        " 'STOCKED', 1, 'FLOATING', now(), '{}', '{}')",
        {},
    ),
    "archive_view_lineage": (
        "INSERT INTO archive_view_lineage (view_id, id, relation, parent_flow_id, child_flow_id,"
        " device_event_id, created_at, void) VALUES (:view, :key, 'SPLIT', 1, 2, 'e', now(),"
        " false)",
        {},
    ),
    "archive_view_routes": (
        "INSERT INTO archive_view_routes (view_id, assigned_route_id, created_at, steps)"
        " VALUES (:view, :key, now(), '[]')",
        {},
    ),
    "archive_view_names": (
        "INSERT INTO archive_view_names (view_id, kind, key, value) VALUES (:view, 'areas',"
        " CAST(:key AS text), '{}')",
        {},
    ),
}


def _row(table: str, view_id: int, key: int = 1) -> tuple[str, dict[str, Any]]:
    sql, _params = _ROWS[table]
    return sql, {"view": view_id, "key": key}


@pytest.fixture
def two_views(head: Engine, setup: dict[str, int]) -> tuple[int, int]:
    """View A LOADING and view B LOADING, each with one row in every viewer table."""
    with head.begin() as connection:
        first = _view(connection, setup["first"], setup["user"])
        second = _view(connection, setup["second"], setup["user"])
        for view_id in (first, second):
            _move(connection, view_id, "state = 'LOADING', started_at = now()")
            for table in models.ARCHIVE_VIEWER_TABLES:
                sql, params = _row(table, view_id)
                connection.execute(sa.text(sql), params)
    return first, second


@pytest.mark.parametrize("table", models.ARCHIVE_VIEWER_TABLES)
def test_viewer_rows_are_inserted_only_for_the_loading_view_the_marker_names(
    head: Engine, roles: DatabaseRoles, two_views: tuple[int, int], table: str
) -> None:
    first, second = two_views
    own, other = _row(table, first, 2), _row(table, second, 2)
    for role in (None, roles.maintenance):
        _admitted(head, [_marker(first), own], role=role)
        _refused(head, [own], "inserted only by the host job loading", role=role)
        _refused(head, [_marker(first), other], "rows of another view are not inserted", role=role)
    _refused(head, [_marker(first), own], "permission denied", role=roles.app, sqlstate="42501")
    # The view must be LOADING.
    with head.begin() as connection:
        _move(connection, first, _OPEN)
    _refused(
        head, [_marker(first), own], "inserted only by the host job loading", role=roles.maintenance
    )


@pytest.mark.parametrize("table", models.ARCHIVE_VIEWER_TABLES)
def test_viewer_rows_are_deleted_only_for_the_view_the_marker_names(
    head: Engine, roles: DatabaseRoles, two_views: tuple[int, int], table: str
) -> None:
    first, second = two_views
    delete = (f"DELETE FROM {table} WHERE view_id = :view", {"view": first})
    delete_all: tuple[str, dict[str, Any]] = (f"DELETE FROM {table}", {})
    for role in (None, roles.maintenance):
        # LOADING: the rows of a failed or stopped load.
        _admitted(head, [_marker(first), delete], role=role)
        _refused(head, [delete], "deleted only by the host job", role=role)
        _refused(
            head, [_marker(first), delete_all], "rows of another view are not deleted", role=role
        )
    _refused(head, [_marker(first), delete], "permission denied", role=roles.app, sqlstate="42501")
    # OPEN: never; CLOSING: its own rows.
    with head.begin() as connection:
        _move(connection, first, _OPEN)
    _refused(head, [_marker(first), delete], "deleted only by the host job", role=roles.maintenance)
    with head.begin() as connection:
        _move(connection, first, "state = 'CLOSING', closing_started_at = now()")
    _admitted(head, [_marker(first), delete], role=roles.maintenance)
    _refused(
        head,
        [_marker(first), (f"DELETE FROM {table} WHERE view_id = :view", {"view": second})],
        "rows of another view are not deleted",
        role=roles.maintenance,
    )


@pytest.mark.parametrize("table", models.ARCHIVE_VIEWER_TABLES)
def test_viewer_rows_never_change(
    head: Engine, roles: DatabaseRoles, two_views: tuple[int, int], table: str
) -> None:
    first, _second = two_views
    update = (f"UPDATE {table} SET view_id = view_id WHERE view_id = :view", {"view": first})
    truncate: tuple[str, dict[str, Any]] = (f"TRUNCATE {table}", {})
    # The owner, the marker set: the statement guard refuses before any row.
    _refused(head, [_marker(first), update], "rows never change")
    _refused(head, [truncate], "rows never change")
    # Neither role holds UPDATE or TRUNCATE on a viewer table.
    for role in (roles.app, roles.maintenance):
        for statement in (update, truncate):
            _refused(
                head, [_marker(first), statement], "permission denied", role=role, sqlstate="42501"
            )
    _admitted(head, [(f"SELECT count(*) FROM {table}", {})], role=roles.app)


# ---------------------------------------------------------------------------
# The audit scope
# ---------------------------------------------------------------------------

_AUDIT_INSERT = (
    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, before_data,"
    " after_data) VALUES ('UPDATED', :type, :id, now(), '{}', '{}')"
)


def test_the_maintenance_role_audits_the_view_it_works_on_only(
    head: Engine, setup: dict[str, int], roles: DatabaseRoles
) -> None:
    with head.begin() as connection:
        view_id = _view(connection, setup["first"], setup["user"])
    own = (_AUDIT_INSERT, {"type": "HistoryArchiveView", "id": str(view_id)})
    other = (_AUDIT_INSERT, {"type": "HistoryArchiveView", "id": str(view_id + 1)})
    _admitted(head, [_marker(view_id), own], role=roles.maintenance)
    _refused(
        head, [own], "appends only HistoryArchiveRun, DeletionRequest and", role=roles.maintenance
    )
    _refused(
        head,
        [_marker(view_id), other],
        "appends only HistoryArchiveRun, DeletionRequest and",
        role=roles.maintenance,
    )
    _refused(
        head,
        [_marker(view_id), (_AUDIT_INSERT, {"type": "WorkOrder", "id": "1"})],
        "not WorkOrder",
        role=roles.maintenance,
    )
    # The application role appends a view's rows as before (no scope for it).
    _admitted(head, [own], role=roles.app)


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def test_the_downgrade_restores_0043_and_upgrades_again(head_database: URL, head: Engine) -> None:
    config = _alembic_config(head_database)
    command.downgrade(config, _PREVIOUS_REVISION)
    with head.connect() as connection:
        tables = connection.execute(
            sa.text("SELECT count(*) FROM pg_class WHERE relname = ANY(:names)"),
            {"names": ["history_archive_views", *models.ARCHIVE_VIEWER_TABLES]},
        ).scalar_one()
        columns = connection.execute(
            sa.text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name ="
                " 'application_policy' AND column_name LIKE 'archive_view_%'"
            )
        ).scalar_one()
        audit_scope = connection.execute(
            sa.text("SELECT prosrc FROM pg_proc WHERE proname = :name"), {"name": _AUDIT_FUNCTION}
        ).scalar_one()
        entity_check = connection.execute(
            sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_audit_events_entity_type'"
            )
        ).scalar_one()
    deletion = _load(_DELETION_FILE, "deletion_requests_migration_for_0044_downgrade")
    assert (tables, columns) == (0, 0)
    assert audit_scope == deletion._AUDIT_BODY
    assert "HistoryArchiveView" not in entity_check
    command.upgrade(config, "head")


_STEP_FIVE_DATA = {
    "view": "view",
    "audit-row": (
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, before_data,"
        " after_data) VALUES ('UPDATED', 'HistoryArchiveView', '1', now(), '{}', '{}')"
    ),
    "days": "UPDATE application_policy SET archive_view_days = 8",
    "max-loaded": "UPDATE application_policy SET archive_view_max_loaded = 3",
}


@pytest.mark.parametrize("data", list(_STEP_FIVE_DATA))
def test_the_downgrade_refuses_while_step_5_data_exists(
    head_database: URL, head: Engine, data: str
) -> None:
    with head.begin() as connection:
        statement = _STEP_FIVE_DATA[data]
        if statement == "view":
            user_id = _user(connection)
            _view(connection, _viewable_run(connection, user_id), user_id)
        else:
            connection.execute(sa.text(statement))
    with pytest.raises(DBAPIError, match="Archive viewing data exists; refusing downgrade"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert _scalar(connection, "SELECT version_num FROM alembic_version") == _HEAD_REVISION
