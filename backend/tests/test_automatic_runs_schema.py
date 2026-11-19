"""Integration tests for migration ``0043_phase16_automatic_runs`` (history archival, step 4).

Runs the real Alembic chain against isolated, temporary PostgreSQL databases
(created and dropped by the fixtures) and verifies:

- the exact head boundary (``0043_phase16_automatic_runs`` is the single head,
  and the models match the schema migrated to it) and the migration's
  literals against the models, the enums, the policies module and migration
  0041 (whose run guard and CHECKs it replaces, and restores on its
  downgrade);
- a fresh install: automatic runs off, the measure, the limits, the approval
  step on, the interval, no standing authorization, and the pinned guard;
- the CHECKs of the settings (the measure, the ranges, the standing
  authorization exactly while the approval step is off) and of the runs (the
  trigger, the requester, the approval, the authority, a failed authority);
- the run guard: a role that may write archive records inserts automatic
  runs only, PROPOSED or APPROVED under the setting's standing authorization,
  never with a requesting User or progress; the maintenance role inserts no
  manual run and the application role no automatic one; the application
  role approves or dismisses a PROPOSED run and changes nothing else; the
  job never moves, approves or dismisses a PROPOSED run; the request columns
  never change; a dismissed run never changes; PROPOSED is open — as the
  owner, the maintenance role and the application role (temporary roles
  granted by ``apply-grants``);
- the downgrade restores 0042 exactly and refuses while step-4 data exists.
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
from app.application import database_roles, policies
from app.application.database_roles import DatabaseRoles
from app.domain.enums import HistoryArchiveMeasure, HistoryArchiveRunState, HistoryArchiveRunTrigger
from app.infrastructure import models, schema_revision
from app.infrastructure.database_privileges import (
    GUARD_FUNCTION_SHA256,
    GUARD_TRIGGERS,
    MAINTENANCE_PRIVILEGES,
)
from tests.role_harness import drop_roles, temporary_roles

# Schema, trigger and role assertions: owner connections in the
# application-role test mode (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_VERSIONS = _BACKEND_DIR / "alembic" / "versions"
_PREVIOUS_REVISION = "0042_phase16_deletion_requests"
_HEAD_REVISION = "0043_phase16_automatic_runs"
_MIGRATION_FILE = _VERSIONS / "20261118_0043_phase16_automatic_runs.py"
_PURGE_FILE = _VERSIONS / "20261118_0041_phase16_history_purge.py"
_HEAD_TEMPLATE = "partflow_test_automatic_runs_schema_head"
_CASE = "partflow_test_automatic_runs_schema_case"
_RUNS_FUNCTION = "partflow_history_archive_runs_guard_mutation"
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


def _attempt(
    engine: Engine, statements: list[tuple[str, dict[str, Any]]], role: str | None = None
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
    statements: list[tuple[str, dict[str, Any]]],
    match: str,
    role: str | None = None,
    sqlstate: str = "P0001",
) -> None:
    state, message = _attempt(engine, statements, role)
    assert state == sqlstate, message
    assert match in message, message


def _admitted(
    engine: Engine, statements: list[tuple[str, dict[str, Any]]], role: str | None = None
) -> None:
    state, message = _attempt(engine, statements, role)
    assert state is None, message


# ---------------------------------------------------------------------------
# Head and literals
# ---------------------------------------------------------------------------


def test_the_revision_is_in_the_chain() -> None:
    # The single head moved to tests/test_archive_viewing_schema.py (step 5, 0044).
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    revision = ScriptDirectory.from_config(config).get_revision(_HEAD_REVISION)
    assert revision is not None and revision.down_revision == _PREVIOUS_REVISION
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


def _server_default(model: type[models.Base], column: str) -> str | None:
    default = cast(sa.Table, model.__table__).c[column].server_default
    if default is None:
        return None
    return str(cast(sa.DefaultClause, default).arg.text)  # type: ignore[union-attr]


def test_the_migration_literals_match_the_models_and_0041() -> None:
    migration = _load(_MIGRATION_FILE, "automatic_runs_migration")
    purge = _load(_PURGE_FILE, "history_purge_migration_for_0043")
    assert (migration.revision, migration.down_revision) == (_HEAD_REVISION, _PREVIOUS_REVISION)
    assert tuple(state.value for state in HistoryArchiveRunState) == migration._STATES
    assert migration._FINAL_STATES == models.HISTORY_ARCHIVE_FINAL_STATES
    assert migration._OPEN_STATES == models.HISTORY_ARCHIVE_OPEN_STATES
    assert migration._in("state", migration._OPEN_STATES) == (
        models.HISTORY_ARCHIVE_RUN_OPEN_WHERE_SQL
    )
    assert tuple(trigger.value for trigger in HistoryArchiveRunTrigger) == migration._RUN_TRIGGERS
    assert tuple(measure.value for measure in HistoryArchiveMeasure) == migration._MEASURES
    # The previous literals are 0041's (the downgrade restores them).
    assert migration._PREVIOUS_STATES == purge._STATES
    assert migration._PREVIOUS_FINAL_STATES == purge._FINAL_STATES
    assert migration._PREVIOUS_OPEN_STATES == purge._OPEN_STATES
    assert migration._PREVIOUS_RUNS_BODY == purge._RUNS_BODY
    changed_by_0041 = {suffix: new for suffix, _old, new in purge._CHANGED_RUN_CHECKS}
    for suffix, previous, _new in migration._CHANGED_RUN_CHECKS:
        assert changed_by_0041[suffix] == previous, suffix
    # The run CHECKs it changes and adds are the models'.
    model_checks = _model_checks(models.HistoryArchiveRun)
    for suffix, _previous, sql in migration._CHANGED_RUN_CHECKS:
        assert model_checks[f"ck_history_archive_runs_{suffix}"] == sql, suffix
    for suffix, sql in migration._NEW_RUN_CHECKS:
        assert model_checks[f"ck_history_archive_runs_{suffix}"] == sql, suffix
    # The run columns, their types' nullability and defaults are the models'.
    runs = cast(sa.Table, models.HistoryArchiveRun.__table__)
    for column, _type, default in migration._RUN_COLUMNS:
        assert runs.c[column].nullable is (default is None), column
        assert _server_default(models.HistoryArchiveRun, column) == default, column
    assert runs.c["requested_by_user_id"].nullable is True
    # The settings: their defaults, ranges and CHECKs.
    policy_checks = _model_checks(models.ApplicationPolicy)
    for column, _type, default in migration._POLICY_COLUMNS:
        assert _server_default(models.ApplicationPolicy, column) == default, column
    assert {
        column: (minimum, maximum)
        for column, _default, minimum, maximum in migration._POLICY_RANGES
    } == {
        column: bounds
        for column, bounds in models.HISTORY_ARCHIVE_POLICY_RANGES.items()
        if column.startswith(("archive_limit_", "archive_interval_"))
    }
    assert [default for _column, default, *_ in migration._POLICY_RANGES] == [
        models.ARCHIVE_LIMIT_MOVEMENT_ROWS_DEFAULT,
        models.ARCHIVE_LIMIT_HISTORY_MONTHS_DEFAULT,
        models.ARCHIVE_LIMIT_DATABASE_GIB_DEFAULT,
        models.ARCHIVE_INTERVAL_DAYS_DEFAULT,
    ]
    for column, _default, minimum, maximum in migration._POLICY_RANGES:
        assert policy_checks[f"ck_application_policy_{column}_range"] == (
            f"{column} BETWEEN {minimum} AND {maximum}"
        )
    for name, sql in migration._POLICY_CHECKS:
        assert policy_checks[name] == sql, name
    assert policy_checks["ck_application_policy_archive_measure_allowed"] == (
        models.POLICY_ARCHIVE_MEASURE_SQL
    )
    # The audited section adds the settings, not the authorization's id.
    assert (
        policies.HISTORY_ARCHIVE_COLUMNS[7:14]
        == tuple(column for column, _type, _default in migration._POLICY_COLUMNS)[:-1]
    )
    # The replaced guard function is pinned by check (h); its triggers are unchanged.
    assert (
        hashlib.sha256(migration._RUNS_BODY.encode("utf-8")).hexdigest()
        == GUARD_FUNCTION_SHA256[migration._RUNS_FUNCTION]
    )
    assert GUARD_TRIGGERS["trg_history_archive_runs_guard_mutation"].function == _RUNS_FUNCTION
    assert migration._RUNS_FUNCTION == _RUNS_FUNCTION
    # The maintenance role inserts runs (automatic ones only: the guard).
    assert MAINTENANCE_PRIVILEGES["history_archive_runs"] == {"SELECT", "INSERT", "UPDATE"}


def test_a_fresh_install_holds_the_new_defaults(head: Engine) -> None:
    with head.connect() as connection:
        policy = dict(
            connection.execute(
                sa.text(
                    "SELECT archive_automatic_runs, archive_measure, archive_limit_movement_rows,"
                    " archive_limit_history_months, archive_limit_database_gib,"
                    " archive_approval_required, archive_interval_days, archive_authorization_id"
                    " FROM application_policy"
                )
            )
            .mappings()
            .one()
        )
        source = _scalar(
            connection, "SELECT prosrc FROM pg_proc WHERE proname = :name", name=_RUNS_FUNCTION
        )
    assert policy == {
        "archive_automatic_runs": False,
        "archive_measure": "LIVE_MOVEMENT_ROWS",
        "archive_limit_movement_rows": 5_000_000,
        "archive_limit_history_months": 1,
        "archive_limit_database_gib": 20,
        "archive_approval_required": True,
        "archive_interval_days": 7,
        "archive_authorization_id": None,
    }
    assert (
        hashlib.sha256(source.encode("utf-8")).hexdigest() == GUARD_FUNCTION_SHA256[_RUNS_FUNCTION]
    )


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


_RUN_INSERT = (
    "INSERT INTO history_archive_runs (state, trigger, cutoff, reason, requested_by_user_id,"
    " authorization_id, preview_components, preview_flows, preview_movements,"
    " preview_archive_bytes) VALUES (:state, :trigger, :cutoff, 'test', :user, :authorization,"
    " 1, 1, 1, 0)"
)


def _run_values(**values: Any) -> dict[str, Any]:
    return {
        "state": "PROPOSED",
        "trigger": "AUTOMATIC",
        "cutoff": _CUTOFF,
        "user": None,
        "authorization": None,
        **values,
    }


def _insert(connection: Connection, **values: Any) -> int:
    return int(_scalar(connection, _RUN_INSERT + " RETURNING id", **_run_values(**values)))


def _authorize(connection: Connection, user_id: int) -> int:
    """Switch the approval step off with a standing authorization (its audit row)."""
    audit_id = int(
        _scalar(
            connection,
            "INSERT INTO audit_events (event_type, entity_type, entity_id, actor_user_id,"
            " occurred_at, before_data, after_data, metadata) VALUES ('UPDATED',"
            " 'ApplicationPolicy', 'data-retention', :user, now(), '{}', '{}',"
            " CAST(:metadata AS jsonb)) RETURNING id",
            user=user_id,
            metadata='{"reason": "test", "standing_authorization": "granted"}',
        )
    )
    connection.execute(
        sa.text(
            "UPDATE application_policy SET archive_approval_required = false,"
            " archive_authorization_id = :id"
        ),
        {"id": audit_id},
    )
    return audit_id


def _marked(run_id: int) -> tuple[str, dict[str, Any]]:
    return (
        "SELECT set_config('partflow.archive_run_id', CAST(:id AS text), true)",
        {"id": run_id},
    )


def _state(engine: Engine, run_id: int) -> str:
    with engine.connect() as connection:
        return str(
            _scalar(connection, "SELECT state FROM history_archive_runs WHERE id = :id", id=run_id)
        )


# ---------------------------------------------------------------------------
# The CHECKs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("assignment", "admitted"),
    [
        ("archive_measure = 'RETAINED_HISTORY_AGE'", True),
        ("archive_measure = 'DATABASE_SIZE'", True),
        ("archive_measure = 'ROWS'", False),
        ("archive_limit_movement_rows = 0", True),
        ("archive_limit_movement_rows = 1000000000", True),
        ("archive_limit_movement_rows = -1", False),
        ("archive_limit_movement_rows = 1000000001", False),
        ("archive_limit_history_months = 0", True),
        ("archive_limit_history_months = 1200", True),
        ("archive_limit_history_months = 1201", False),
        ("archive_limit_database_gib = 0", True),
        ("archive_limit_database_gib = 100000", True),
        ("archive_limit_database_gib = 100001", False),
        ("archive_interval_days = 1", True),
        ("archive_interval_days = 365", True),
        ("archive_interval_days = 0", False),
        ("archive_interval_days = 366", False),
        # The standing authorization exactly while the approval step is off.
        ("archive_approval_required = false", False),
        ("archive_automatic_runs = true", True),
    ],
)
def test_the_setting_checks(head: Engine, assignment: str, admitted: bool) -> None:
    statement: list[tuple[str, dict[str, Any]]] = [
        (f"UPDATE application_policy SET {assignment}", {})
    ]
    if admitted:
        _admitted(head, statement)
    else:
        _refused(head, statement, "ck_application_policy_", sqlstate="23514")


def test_the_standing_authorization_needs_the_approval_step_off(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        audit_id = _authorize(connection, user_id)
    _refused(
        head,
        [("UPDATE application_policy SET archive_approval_required = true", {})],
        "ck_application_policy_archive_authorization_shape",
        sqlstate="23514",
    )
    _admitted(
        head,
        [
            (
                "UPDATE application_policy SET archive_approval_required = true,"
                " archive_authorization_id = NULL",
                {},
            )
        ],
    )
    # The authorization names a real audit row.
    _refused(
        head,
        [("UPDATE application_policy SET archive_authorization_id = :id", {"id": audit_id + 99})],
        "fk_application_policy_archive_authorization_id_audit_events",
        sqlstate="23503",
    )


def test_the_run_checks(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
    # The trigger, and a requester exactly for a manual run.
    _refused(
        head,
        [(_RUN_INSERT, _run_values(state="APPROVED", trigger="TIMER", user=user_id))],
        # The trigger CHECK, or the authority CHECK an unknown trigger fails too.
        'violates check constraint "ck_history_archive_runs_',
        sqlstate="23514",
    )
    _refused(
        head,
        [(_RUN_INSERT, _run_values(state="APPROVED", trigger="MANUAL"))],
        "ck_history_archive_runs_requester_shape",
        sqlstate="23514",
    )
    # An automatic run beyond PROPOSED has an approval or the standing authorization.
    with head.begin() as connection:
        run_id = _insert(connection)
    _refused(
        head,
        [("UPDATE history_archive_runs SET state = 'APPROVED' WHERE id = :id", {"id": run_id})],
        "ck_history_archive_runs_authority_shape",
        sqlstate="23514",
    )
    # An approval is complete: approver, time and a reason of 1-500 characters.
    _refused(
        head,
        [
            (
                "UPDATE history_archive_runs SET state = 'APPROVED', approved_by_user_id = :user,"
                " approved_at = now() WHERE id = :id",
                {"id": run_id, "user": user_id},
            )
        ],
        "ck_history_archive_runs_approval_shape",
        sqlstate="23514",
    )
    _refused(
        head,
        [
            (
                "UPDATE history_archive_runs SET state = 'APPROVED', approved_by_user_id = :user,"
                " approved_at = now(), approval_reason = '' WHERE id = :id",
                {"id": run_id, "user": user_id},
            )
        ],
        "ck_history_archive_runs_approval_shape",
        sqlstate="23514",
    )
    # A failed authority only on a FAILED run.
    _refused(
        head,
        [
            _marked(run_id),
            (
                "UPDATE history_archive_runs SET authorization_failed = true WHERE id = :id",
                {"id": run_id},
            ),
        ],
        "only a person approves or dismisses a proposed run",
    )
    with head.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'DISMISSED', finished_at = now(),"
                " dismissed_by_user_id = :user WHERE id = :id"
            ),
            {"id": run_id, "user": user_id},
        )
        manual = _insert(connection, state="APPROVED", trigger="MANUAL", user=user_id)
    _refused(
        head,
        [
            _marked(manual),
            (
                "UPDATE history_archive_runs SET authorization_failed = true WHERE id = :id",
                {"id": manual},
            ),
        ],
        "ck_history_archive_runs_authorization_failed_shape",
        sqlstate="23514",
    )


def test_a_proposed_run_is_open(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        _insert(connection)
    _refused(
        head,
        [(_RUN_INSERT, _run_values(state="APPROVED", trigger="MANUAL", user=user_id))],
        "uq_history_archive_runs_open",
        sqlstate="23505",
    )


# ---------------------------------------------------------------------------
# The run guard
# ---------------------------------------------------------------------------


def test_an_automatic_run_is_proposed_or_approved_under_the_standing_authorization(
    head: Engine,
) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
    # PROPOSED, with no requester, no authorization and no progress.
    _admitted(head, [(_RUN_INSERT, _run_values())])
    for values, match in (
        ({"user": user_id}, "has no requesting User"),
        ({"state": "RUNNING"}, "created PROPOSED, or APPROVED"),
        ({"state": "APPROVED"}, "created PROPOSED, or APPROVED"),
    ):
        _refused(head, [(_RUN_INSERT, _run_values(**values))], match)
    _refused(
        head,
        [
            (
                "INSERT INTO history_archive_runs (state, trigger, cutoff, reason, started_at,"
                " preview_components, preview_flows, preview_movements, preview_archive_bytes)"
                " VALUES ('PROPOSED', 'AUTOMATIC', :cutoff, 'test', now(), 1, 1, 1, 0)",
                {"cutoff": _CUTOFF},
            )
        ],
        "created with no progress",
    )
    # APPROVED only with the standing authorization the setting holds, while the step is off.
    with head.begin() as connection:
        audit_id = _authorize(connection, user_id)
        other = int(
            _scalar(
                connection,
                "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
                " VALUES ('UPDATED', 'ApplicationPolicy', 'data-retention', now()) RETURNING id",
            )
        )
    _admitted(head, [(_RUN_INSERT, _run_values(state="APPROVED", authorization=audit_id))])
    refused: list[dict[str, Any]] = [
        {"state": "APPROVED", "authorization": other},
        {"state": "APPROVED"},
        {"state": "PROPOSED", "authorization": audit_id},
    ]
    for values in refused:
        _refused(
            head,
            [(_RUN_INSERT, _run_values(**values))],
            "under the standing authorization of the approval setting",
        )
    # The cutoff floor binds an automatic run too.
    _refused(
        head,
        [(_RUN_INSERT, _run_values(cutoff=datetime.datetime.now(datetime.UTC)))],
        "newer than the shortest allowed period",
        sqlstate="23514",
    )
    # A manual run never names an authorization.
    _refused(
        head,
        [
            (
                _RUN_INSERT,
                _run_values(
                    state="APPROVED", trigger="MANUAL", user=user_id, authorization=audit_id
                ),
            )
        ],
        "requested APPROVED, with no progress",
    )


def test_the_roles_insert_their_own_runs_only(head: Engine, roles: DatabaseRoles) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
    manual = (_RUN_INSERT, _run_values(state="APPROVED", trigger="MANUAL", user=user_id))
    proposed = (_RUN_INSERT, _run_values())
    # The maintenance role (history-archive evaluate): automatic runs only.
    _admitted(head, [proposed], roles.maintenance)
    _refused(
        head,
        [manual],
        "the maintenance role creates automatic history archive runs only",
        roles.maintenance,
    )
    _refused(
        head,
        [(_RUN_INSERT, _run_values(user=user_id))],
        "has no requesting User",
        roles.maintenance,
    )
    _refused(
        head,
        [(_RUN_INSERT, _run_values(state="APPROVED"))],
        "under the standing authorization of the approval setting",
        roles.maintenance,
    )
    with head.begin() as connection:
        audit_id = _authorize(connection, user_id)
    _admitted(
        head,
        [(_RUN_INSERT, _run_values(state="APPROVED", authorization=audit_id))],
        roles.maintenance,
    )
    # The application role (a person's request): manual runs only.
    _admitted(head, [manual], roles.app)
    _refused(
        head, [proposed], "only the host job creates an automatic history archive run", roles.app
    )


def test_a_person_approves_or_dismisses_a_proposed_run(head: Engine, roles: DatabaseRoles) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        run_id = _insert(connection)
    approve = (
        "UPDATE history_archive_runs SET state = 'APPROVED', approved_by_user_id = :user,"
        " approved_at = now(), approval_reason = 'Looks right' WHERE id = :id",
        {"id": run_id, "user": user_id},
    )
    dismiss = (
        "UPDATE history_archive_runs SET state = 'DISMISSED', finished_at = now(),"
        " dismissed_by_user_id = :user WHERE id = :id",
        {"id": run_id, "user": user_id},
    )
    cancel = (
        "UPDATE history_archive_runs SET state = 'CANCELLED', finished_at = now(),"
        " cancelled_by_user_id = :user WHERE id = :id",
        {"id": run_id, "user": user_id},
    )
    _admitted(head, [approve], roles.app)
    _admitted(head, [dismiss], roles.app)
    # Nothing else changes with them; a proposed run is dismissed, not cancelled.
    _refused(
        head,
        [
            (
                "UPDATE history_archive_runs SET state = 'APPROVED', approved_by_user_id = :user,"
                " approved_at = now(), approval_reason = 'x', copied_at = now() WHERE id = :id",
                {"id": run_id, "user": user_id},
            )
        ],
        "only the maintenance job makes this change",
        roles.app,
    )
    _refused(head, [cancel], "only the maintenance job makes this change", roles.app)
    # The maintenance role neither approves nor dismisses, with the setting or without it.
    for statements in (
        [approve],
        [dismiss],
        [_marked(run_id), approve],
        [_marked(run_id), dismiss],
    ):
        state, message = _attempt(head, statements, roles.maintenance)
        assert state == "P0001", message
    # Approved once: the approval never changes afterwards.
    with head.begin() as connection:
        connection.execute(sa.text(approve[0]), approve[1])
    _refused(
        head,
        [
            (
                "UPDATE history_archive_runs SET approval_reason = 'Other' WHERE id = :id",
                {"id": run_id},
            )
        ],
        "only the maintenance job makes this change",
        roles.app,
    )


def test_the_job_never_moves_a_proposed_run(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        run_id = _insert(connection)
    for change in (
        "state = 'RUNNING', started_at = now()",
        "state = 'FAILED', failure_reason = 'x', finished_at = now()",
        "state = 'DISMISSED', finished_at = now(), dismissed_by_user_id = :user",
    ):
        _refused(
            head,
            [
                _marked(run_id),
                (
                    f"UPDATE history_archive_runs SET {change} WHERE id = :id",
                    {"id": run_id, "user": user_id},
                ),
            ],
            "only a person approves or dismisses a proposed run",
        )
    # Once approved, the job works on it, and never changes its approval.
    with head.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'APPROVED', approved_by_user_id = :user,"
                " approved_at = now(), approval_reason = 'Go' WHERE id = :id"
            ),
            {"id": run_id, "user": user_id},
        )
    _admitted(
        head,
        [
            _marked(run_id),
            (
                "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now()"
                " WHERE id = :id",
                {"id": run_id},
            ),
        ],
    )
    _refused(
        head,
        [
            _marked(run_id),
            (
                "UPDATE history_archive_runs SET approval_reason = 'Other' WHERE id = :id",
                {"id": run_id},
            ),
        ],
        "only a person approves or dismisses a proposed run",
    )


def test_the_request_and_a_dismissed_run_never_change(head: Engine) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        audit_id = _authorize(connection, user_id)
        approved = _insert(connection, state="APPROVED", authorization=audit_id)
    for change in ("trigger = 'MANUAL', requested_by_user_id = :user", "authorization_id = NULL"):
        _refused(
            head,
            [
                _marked(approved),
                (
                    f"UPDATE history_archive_runs SET {change} WHERE id = :id",
                    {"id": approved, "user": user_id},
                ),
            ],
            "never changes",
        )
    with head.begin() as connection:
        sql, params = _marked(approved)
        connection.execute(sa.text(sql), params)
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'FAILED', finished_at = now(),"
                " failure_reason = 'test' WHERE id = :id"
            ),
            {"id": approved},
        )
        connection.execute(
            sa.text(
                "UPDATE application_policy SET archive_approval_required = true,"
                " archive_authorization_id = NULL"
            )
        )
        dismissed = _insert(connection)
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'DISMISSED', finished_at = now(),"
                " dismissed_by_user_id = :user WHERE id = :id"
            ),
            {"id": dismissed, "user": user_id},
        )
    assert _state(head, dismissed) == "DISMISSED"
    _refused(
        head,
        [
            (
                "UPDATE history_archive_runs SET state = 'PROPOSED', finished_at = NULL,"
                " dismissed_by_user_id = NULL WHERE id = :id",
                {"id": dismissed},
            )
        ],
        "is finished (DISMISSED) and never changes",
    )


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def test_the_downgrade_restores_0042(head_database: URL, head: Engine) -> None:
    config = _alembic_config(head_database)
    purge = _load(_PURGE_FILE, "history_purge_migration_for_0043_downgrade")
    command.downgrade(config, _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert _scalar(connection, "SELECT version_num FROM alembic_version") == _PREVIOUS_REVISION
        run_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("history_archive_runs")
        }
        policy_columns = {
            column["name"] for column in sa.inspect(connection).get_columns("application_policy")
        }
        source = _scalar(
            connection, "SELECT prosrc FROM pg_proc WHERE proname = :name", name=_RUNS_FUNCTION
        )
        nullable = _scalar(
            connection,
            "SELECT is_nullable FROM information_schema.columns WHERE table_name ="
            " 'history_archive_runs' AND column_name = 'requested_by_user_id'",
        )
    assert not {"trigger", "approved_by_user_id", "authorization_id"} & run_columns
    assert not {"archive_automatic_runs", "archive_authorization_id"} & policy_columns
    assert source == purge._RUNS_BODY
    assert nullable == "NO"
    with head.begin() as connection:
        user_id = _user(connection)
    _refused(
        head,
        [
            (
                "INSERT INTO history_archive_runs (state, cutoff, reason, requested_by_user_id,"
                " preview_components, preview_flows, preview_movements, preview_archive_bytes)"
                " VALUES ('PROPOSED', :cutoff, 'test', :user, 1, 1, 1, 0)",
                {"cutoff": _CUTOFF, "user": user_id},
            )
        ],
        "requested APPROVED",
    )
    command.upgrade(config, "head")
    with head.connect() as connection:
        assert (
            _scalar(connection, "SELECT version_num FROM alembic_version")
            == schema_revision.code_head()
        )


_STEP_FOUR_DATA = {
    "proposed-run": "proposed",
    "dismissed-run": "dismissed",
    "automatic-runs-on": "UPDATE application_policy SET archive_automatic_runs = true",
    "measure": "UPDATE application_policy SET archive_measure = 'DATABASE_SIZE'",
    "limit": "UPDATE application_policy SET archive_limit_movement_rows = 1",
    "interval": "UPDATE application_policy SET archive_interval_days = 30",
    "approval-off": "authorized",
}


@pytest.mark.parametrize("data", list(_STEP_FOUR_DATA))
def test_the_downgrade_refuses_while_step_4_data_exists(
    head_database: URL, head: Engine, data: str
) -> None:
    with head.begin() as connection:
        user_id = _user(connection)
        statement = _STEP_FOUR_DATA[data]
        if statement == "proposed":
            _insert(connection)
        elif statement == "dismissed":
            run_id = _insert(connection)
            connection.execute(
                sa.text(
                    "UPDATE history_archive_runs SET state = 'DISMISSED', finished_at = now(),"
                    " dismissed_by_user_id = :user WHERE id = :id"
                ),
                {"id": run_id, "user": user_id},
            )
        elif statement == "authorized":
            _authorize(connection, user_id)
        else:
            connection.execute(sa.text(statement))
    with pytest.raises(DBAPIError, match="Automatic archival run data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert (
            _scalar(connection, "SELECT version_num FROM alembic_version")
            == schema_revision.code_head()
        )
