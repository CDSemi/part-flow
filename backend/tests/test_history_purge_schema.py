"""Integration tests for migration ``0041_phase16_history_purge`` (history archival, step 2).

Runs the real Alembic chain against isolated, temporary PostgreSQL databases
(created and dropped by the fixtures) and verifies:

- the cases run against this revision inside the current chain; the head
  boundary and the models parity moved to
  ``tests/test_deletion_requests_schema.py`` with
  ``0042_phase16_deletion_requests``, which also replaces the Movement guard's
  function (it restores this revision's on its downgrade); the migration's
  literals against the models, the enums and migration 0040;
- the new settings (defaults and range CHECKs);
- the Movement deletion guard: refused for every row unless a run in
  PURGING is named, the row's flow has an unpurged, unskipped record of that
  run, the row is not above the record's last Movement id and both its times
  are before the cutoff — as the owner and as the maintenance role (temporary
  roles granted by ``apply-grants``); the application role holds no DELETE;
  UPDATE and TRUNCATE stay refused;
- the run guard's new transitions (PURGING only with the verified, copied
  archive and its pre-purge backup, recorded once; no EXPORT_ONLY any more;
  the old completions refused), the run CHECKs, and the record guard (skipped
  while PURGING);
- the downgrade restores 0040 and refuses while purge data exists.
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
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from app.application import database_roles
from app.application.database_roles import DatabaseRoles
from app.domain.enums import HistoryArchiveRunResult, HistoryArchiveRunState
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
_PREVIOUS_REVISION = "0040_phase16_history_archive"
_HEAD_REVISION = "0041_phase16_history_purge"
_CODE_HEAD = schema_revision.code_head()
_MIGRATION_FILE = _VERSIONS / "20261118_0041_phase16_history_purge.py"
_PREVIOUS_FILE = _VERSIONS / "20261117_0040_phase16_history_archive.py"
_HEAD_TEMPLATE = "partflow_test_history_purge_schema_head"
_CASE = "partflow_test_history_purge_schema_case"
_CUTOFF = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
_OLD = datetime.datetime(2019, 6, 1, tzinfo=datetime.UTC)
_NEW = datetime.datetime(2021, 6, 1, tzinfo=datetime.UTC)
_ARCHIVE = "run-{run}-20271110T042000Z"


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


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


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


@pytest.fixture(scope="module")
def head_template(admin_engine: Engine) -> Iterator[str]:
    _create_database(admin_engine, _HEAD_TEMPLATE)
    command.upgrade(_alembic_config(_url(_HEAD_TEMPLATE)), "head")
    yield _HEAD_TEMPLATE
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_HEAD_TEMPLATE}" WITH (FORCE)'))


@pytest.fixture
def head_database(admin_engine: Engine, head_template: str) -> Iterator[URL]:
    _create_database(admin_engine, _CASE, template=head_template)
    yield _url(_CASE)
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_CASE}" WITH (FORCE)'))


@pytest.fixture
def head(head_database: URL) -> Iterator[Engine]:
    engine = create_engine(head_database)
    yield engine
    engine.dispose()


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


def _body(create_function: str) -> str:
    """The ``prosrc`` of a ``CREATE FUNCTION … AS $$…$$`` statement."""
    return create_function.split("$$")[1]


def test_the_migration_literals_match_the_models_and_0040() -> None:
    migration = _load(_MIGRATION_FILE, "history_purge_migration")
    previous = _load(_PREVIOUS_FILE, "history_archive_migration_for_purge")
    assert (migration.revision, migration.down_revision) == (_HEAD_REVISION, _PREVIOUS_REVISION)
    assert tuple(state.value for state in HistoryArchiveRunState) == migration._STATES
    assert tuple(result.value for result in HistoryArchiveRunResult) == migration._RESULTS
    assert migration._FINAL_STATES == models.HISTORY_ARCHIVE_FINAL_STATES
    assert migration._OPEN_STATES == models.HISTORY_ARCHIVE_OPEN_STATES
    assert (
        migration._in("state", migration._OPEN_STATES) == models.HISTORY_ARCHIVE_RUN_OPEN_WHERE_SQL
    )
    assert migration._BACKUP_NAME_PATTERN == models.HISTORY_ARCHIVE_BACKUP_NAME_PATTERN
    # The previous literals are 0040's.
    assert migration._PREVIOUS_STATES == previous._STATES
    assert migration._PREVIOUS_RESULTS == previous._RESULTS
    assert migration._PREVIOUS_FINAL_STATES == previous._FINAL_STATES
    assert migration._PREVIOUS_OPEN_STATES == previous._OPEN_STATES
    previous_checks = dict(previous._RUN_CHECKS)
    for suffix, old, _new in migration._CHANGED_RUN_CHECKS:
        assert previous_checks[suffix] == old, suffix
    assert _body(previous._CREATE_RUNS_FUNCTION) == migration._PREVIOUS_RUNS_BODY
    assert _body(previous._CREATE_FLOWS_FUNCTION) == migration._PREVIOUS_FLOWS_BODY
    # The run CHECKs at the head: 0040's unchanged ones, the changed ones, the new ones.
    expected = {
        **{f"ck_history_archive_runs_{suffix}": sql for suffix, sql in previous._RUN_CHECKS},
        **{f"ck_history_archive_runs_{s}": new for s, _old, new in migration._CHANGED_RUN_CHECKS},
        **{f"ck_history_archive_runs_{suffix}": sql for suffix, sql in migration._NEW_RUN_CHECKS},
    }
    assert expected == _model_checks(models.HistoryArchiveRun)
    assert {
        column: (minimum, maximum)
        for column, _default, minimum, maximum in migration._POLICY_COLUMNS
        if column.startswith("archive_")
    } == {
        column: bounds
        for column, bounds in models.HISTORY_ARCHIVE_POLICY_RANGES.items()
        if column.startswith("archive_purge_")
    }
    [backup] = [row for row in migration._POLICY_COLUMNS if row[0] == "backup_keep_pre_purge"]
    assert backup[1:] == (
        models.BACKUP_KEEP_PRE_PURGE_DEFAULT,
        models.BACKUP_KEEP_PRE_PURGE_MIN,
        models.BACKUP_KEEP_PRE_PURGE_MAX,
    )
    assert (
        _model_checks(models.ApplicationPolicy)["ck_application_policy_backup_keep_pre_purge_range"]
        == models.POLICY_BACKUP_KEEP_PRE_PURGE_SQL
    )
    # The guards the migration creates are pinned by check (h); 0042 (history
    # archival step 3) replaces the Movement guard's function, so its pin is 0042's.
    for name, body in (
        (migration._RUNS_FUNCTION, migration._RUNS_BODY),
        (migration._FLOWS_FUNCTION, migration._FLOWS_BODY),
    ):
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == GUARD_FUNCTION_SHA256[name]
    assert GUARD_TRIGGERS[migration._DELETE_TRIGGER].function == migration._DELETE_FUNCTION
    assert GUARD_TRIGGERS[migration._MOVEMENTS_TRIGGER].definition.startswith(
        "CREATE TRIGGER trg_part_movements_forbid_mutation BEFORE UPDATE OR TRUNCATE ON"
    )
    assert MAINTENANCE_PRIVILEGES["part_movements"] == {"SELECT", "DELETE"}


def test_a_fresh_install_holds_the_new_defaults(head: Engine) -> None:
    with head.connect() as connection:
        assert _version(connection) == _CODE_HEAD
        row = (
            connection.execute(
                sa.text(
                    "SELECT archive_purge_batch_movements, archive_purge_batch_part_numbers,"
                    " backup_keep_pre_purge FROM application_policy"
                )
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "archive_purge_batch_movements": 50000,
        "archive_purge_batch_part_numbers": 20,
        "backup_keep_pre_purge": 3,
    }


@pytest.mark.parametrize(
    ("assignment", "admitted"),
    [
        ("archive_purge_batch_movements = 99", False),
        ("archive_purge_batch_movements = 100", True),
        ("archive_purge_batch_movements = 1000000", True),
        ("archive_purge_batch_movements = 1000001", False),
        ("archive_purge_batch_part_numbers = 0", False),
        ("archive_purge_batch_part_numbers = 1", True),
        ("archive_purge_batch_part_numbers = 1000", True),
        ("archive_purge_batch_part_numbers = 1001", False),
        ("backup_keep_pre_purge = 0", False),
        ("backup_keep_pre_purge = 1", True),
        ("backup_keep_pre_purge = 100", True),
        ("backup_keep_pre_purge = 101", False),
    ],
)
def test_the_new_setting_checks(head: Engine, assignment: str, admitted: bool) -> None:
    statement = sa.text(f"UPDATE application_policy SET {assignment}")
    if admitted:
        with head.begin() as connection:
            connection.execute(statement)
    else:
        with pytest.raises(IntegrityError), head.begin() as connection:
            connection.execute(statement)


# ---------------------------------------------------------------------------
# A run with records, built by direct SQL as the owner
# ---------------------------------------------------------------------------


class Scene:
    """Flows, Movements, two runs and their records (owner SQL)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.user = 0
        self.area = 0
        self.operation = 0
        self.flows: dict[str, int] = {}
        self.movements: dict[str, int] = {}
        self.runs: dict[str, int] = {}


def _scalar(connection: Connection, sql: str, **params: Any) -> int:
    return int(connection.execute(sa.text(sql), params).scalar_one())


def _mark(connection: Connection, run_id: int) -> None:
    connection.execute(
        sa.text("SELECT set_config('partflow.archive_run_id', :id, true)"), {"id": str(run_id)}
    )


def _movement(
    connection: Connection,
    scene: Scene,
    flow: str,
    occurred: datetime.datetime,
    received: datetime.datetime,
) -> int:
    return _scalar(
        connection,
        "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type, quantity,"
        " to_area_id, operation_id, occurred_at, server_received_at, device_event_id,"
        " command_sequence) VALUES (:flow, 'PN-1', 'RECEIVED', 1, :area, :operation,"
        " :occurred, :received, gen_random_uuid()::text, 1) RETURNING id",
        flow=scene.flows[flow],
        area=scene.area,
        operation=scene.operation,
        occurred=occurred,
        received=received,
    )


def _run(connection: Connection, scene: Scene, name: str) -> int:
    run_id = _scalar(
        connection,
        "INSERT INTO history_archive_runs (state, cutoff, reason, requested_by_user_id,"
        " preview_components, preview_flows, preview_movements, preview_archive_bytes)"
        " VALUES ('APPROVED', :cutoff, 'test', :user, 9, 9, 99, 0) RETURNING id",
        cutoff=_CUTOFF,
        user=scene.user,
    )
    scene.runs[name] = run_id
    _mark(connection, run_id)
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now() WHERE id = :id"
        ),
        {"id": run_id},
    )
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET archive_name = :name, exported_at = now(),"
            " exported_components = 1, exported_flows = 1, exported_movements = 1,"
            " exported_lineage_edges = 0, exported_assigned_routes = 0, archive_bytes = 1,"
            " manifest_sha256 = :sha WHERE id = :id"
        ),
        {"id": run_id, "name": _ARCHIVE.format(run=run_id), "sha": "ab" * 32},
    )
    return run_id


def _record(
    connection: Connection, scene: Scene, run_id: int, flow: str, last: int, count: int
) -> None:
    _mark(connection, run_id)
    connection.execute(
        sa.text(
            "INSERT INTO history_archived_flows (run_id, flow_id, part_number, component_id,"
            " flow_status, movement_count, first_movement_id, last_movement_id,"
            " first_occurred_at, last_occurred_at, content_sha256) VALUES (:run, :flow, 'PN-1',"
            " :flow, 'STOCKED', :count, 1, :last, :old, :old, :sha)"
        ),
        {
            "run": run_id,
            "flow": scene.flows[flow],
            "count": count,
            "last": last,
            "old": _OLD,
            "sha": "cd" * 32,
        },
    )


def _verified(connection: Connection, run_id: int) -> None:
    _mark(connection, run_id)
    connection.execute(
        sa.text(
            "UPDATE history_archive_runs SET verified_at = now() - interval '1 hour',"
            " skipped_components = 0, skipped_flows = 0 WHERE id = :id"
        ),
        {"id": run_id},
    )


def _backup_name(run_id: int) -> str:
    return "20271110T050102Z-pre-purge-" + _ARCHIVE.format(run=run_id)


_START_PURGE = (
    "UPDATE history_archive_runs SET state = 'PURGING', pre_purge_backup_name = :backup,"
    " pre_purge_backup_started_at = now() - interval '10 minutes', purge_started_at = now(),"
    " purged_components = 0, purged_flows = 0, purged_movements = 0 WHERE id = :id"
)


def _scene(engine: Engine) -> Scene:
    """Run "other" (FAILED) recorded flow "foreign"; run "main" (PURGING) recorded
    "flow" (its last Movement "above" came after the record), "skipped" (skipped)
    and "purged" (purged though a Movement stayed)."""
    scene = Scene(engine)
    with engine.begin() as connection:
        role = _scalar(connection, "SELECT min(id) FROM roles")
        scene.user = _scalar(
            connection,
            "INSERT INTO users (login_name, display_name, role_id) VALUES ('ann', 'Ann', :role)"
            " RETURNING id",
            role=role,
        )
        department = _scalar(connection, "INSERT INTO departments (name) VALUES ('D') RETURNING id")
        scene.area = _scalar(
            connection,
            "INSERT INTO areas (department_id, name) VALUES (:d, 'A') RETURNING id",
            d=department,
        )
        scene.operation = _scalar(
            connection,
            "INSERT INTO operations (area_id, code) VALUES (:a, 'OP') RETURNING id",
            a=scene.area,
        )
        for name in ("flow", "foreign", "skipped", "purged"):
            scene.flows[name] = _scalar(
                connection,
                "INSERT INTO quantity_flows (part_number, quantity, status, current_area_id,"
                " closed_at) VALUES ('PN-1', 1, 'STOCKED', :area, now()) RETURNING id",
                area=scene.area,
            )
        movements = scene.movements
        movements["old"] = _movement(connection, scene, "flow", _OLD, _OLD)
        movements["received_late"] = _movement(connection, scene, "flow", _OLD, _NEW)
        movements["occurred_late"] = _movement(connection, scene, "flow", _NEW, _OLD)
        movements["foreign"] = _movement(connection, scene, "foreign", _OLD, _OLD)
        movements["skipped"] = _movement(connection, scene, "skipped", _OLD, _OLD)
        movements["purged"] = _movement(connection, scene, "purged", _OLD, _OLD)
        other = _run(connection, scene, "other")
        _record(connection, scene, other, "foreign", movements["foreign"], 1)
        _mark(connection, other)
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'FAILED', failure_reason = 'test',"
                " finished_at = now() WHERE id = :id"
            ),
            {"id": other},
        )
        main = _run(connection, scene, "main")
        last = movements["occurred_late"]
        _record(connection, scene, main, "flow", last, 3)
        _record(connection, scene, main, "skipped", movements["skipped"], 1)
        _record(connection, scene, main, "purged", movements["purged"], 1)
        _mark(connection, main)
        connection.execute(
            sa.text("UPDATE history_archived_flows SET skipped_at = now() WHERE flow_id = :f"),
            {"f": scene.flows["skipped"]},
        )
        _verified(connection, main)
        connection.execute(
            sa.text("UPDATE history_archive_runs SET copied_at = now() WHERE id = :id"),
            {"id": main},
        )
        connection.execute(sa.text(_START_PURGE), {"id": main, "backup": _backup_name(main)})
        connection.execute(
            sa.text("UPDATE history_archived_flows SET purged_at = now() WHERE flow_id = :f"),
            {"f": scene.flows["purged"]},
        )
        # A Movement written after the record (its id above the record's last id).
        movements["above"] = _movement(connection, scene, "flow", _OLD, _OLD)
    return scene


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


def _marked(run_id: int) -> tuple[str, dict[str, Any]]:
    return ("SELECT set_config('partflow.archive_run_id', :id, true)", {"id": str(run_id)})


def _delete(movement_id: int) -> tuple[str, dict[str, Any]]:
    return ("DELETE FROM part_movements WHERE id = :id", {"id": movement_id})


# ---------------------------------------------------------------------------
# The Movement deletion guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("as_maintenance", [False, True], ids=["owner", "maintenance"])
def test_the_delete_guard_admits_only_the_archived_rows_of_the_purging_run(
    head: Engine, roles: DatabaseRoles, as_maintenance: bool
) -> None:
    scene = _scene(head)
    role = roles.maintenance if as_maintenance else None
    main, other = scene.runs["main"], scene.runs["other"]
    movements = scene.movements
    refused = "may be deleted only by the purge"
    cases = {
        "no marker": [_delete(movements["old"])],
        "a run not PURGING": [_marked(other), _delete(movements["foreign"])],
        "a row of another run": [_marked(main), _delete(movements["foreign"])],
        "received after the cutoff": [_marked(main), _delete(movements["received_late"])],
        "occurred after the cutoff": [_marked(main), _delete(movements["occurred_late"])],
        "above the last id": [_marked(main), _delete(movements["above"])],
        "a skipped record": [_marked(main), _delete(movements["skipped"])],
        "a purged record": [_marked(main), _delete(movements["purged"])],
        "a malformed marker": [
            ("SELECT set_config('partflow.archive_run_id', 'x', true)", {}),
            _delete(movements["old"]),
        ],
        "a whole statement with one bad row": [
            _marked(main),
            (
                "DELETE FROM part_movements WHERE id = ANY(:ids)",
                {"ids": [movements["old"], movements["above"]]},
            ),
        ],
    }
    for name, statements in cases.items():
        state, message = _attempt(head, statements, role)
        assert (state, refused in message) == ("P0001", True), name
    # The one admitted row: archived by the PURGING run, old on both times.
    assert _attempt(head, [_marked(main), _delete(movements["old"])], role) == (None, "")
    # Updates and truncation stay refused: by the statement guard, or by a missing privilege.
    for statement in (
        "UPDATE part_movements SET quantity = quantity WHERE id = :id",
        "TRUNCATE part_movements CASCADE",
    ):
        state, _ = _attempt(head, [_marked(main), (statement, {"id": movements["old"]})], role)
        assert state == ("42501" if as_maintenance else "P0001"), statement


def test_the_application_role_never_deletes_a_movement(head: Engine, roles: DatabaseRoles) -> None:
    scene = _scene(head)
    main = scene.runs["main"]
    for statement in (
        _delete(scene.movements["old"]),
        ("UPDATE part_movements SET quantity = quantity WHERE false", {}),
        ("TRUNCATE part_movements CASCADE", {}),
    ):
        assert _attempt(head, [_marked(main), statement], roles.app)[0] == "42501", statement


# ---------------------------------------------------------------------------
# The run guard's new transitions and the run CHECKs
# ---------------------------------------------------------------------------


def _waiting_run(head: Engine) -> tuple[int, int]:
    """An exported, verified RUNNING run (not yet copied); (run id, user id)."""
    scene = Scene(head)
    with head.begin() as connection:
        role = _scalar(connection, "SELECT min(id) FROM roles")
        scene.user = _scalar(
            connection,
            "INSERT INTO users (login_name, display_name, role_id) VALUES ('bob', 'Bob', :role)"
            " RETURNING id",
            role=role,
        )
        run_id = _run(connection, scene, "run")
        _verified(connection, run_id)
    return run_id, scene.user


def _job(run_id: int, sql: str, **params: Any) -> list[tuple[str, dict[str, Any]]]:
    return [_marked(run_id), (sql, {"id": run_id, **params})]


def test_purging_needs_the_copy_and_the_backup_recorded_once(head: Engine) -> None:
    run_id, user_id = _waiting_run(head)
    backup = _backup_name(run_id)
    no_copy = _job(run_id, _START_PURGE, backup=backup)
    state, message = _attempt(head, no_copy)
    assert state == "P0001" and "needs the verified archive" in message
    copied = _job(run_id, "UPDATE history_archive_runs SET copied_at = now() WHERE id = :id")
    without_backup = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'PURGING', purge_started_at = now(),"
        " purged_components = 0, purged_flows = 0, purged_movements = 0 WHERE id = :id",
    )
    assert _attempt(head, [*copied, *without_backup])[0] in ("P0001", "23514")
    wrong_label = _job(
        run_id, _START_PURGE, backup="20271110T050102Z-pre-purge-run-999-20271110T042000Z"
    )
    assert _attempt(head, [*copied, *wrong_label])[0] == "23514"
    before_verification = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'PURGING', pre_purge_backup_name = :backup,"
        " pre_purge_backup_started_at = verified_at - interval '1 second',"
        " purge_started_at = now(), purged_components = 0, purged_flows = 0,"
        " purged_movements = 0 WHERE id = :id",
        backup=backup,
    )
    assert _attempt(head, [*copied, *before_verification])[0] == "23514"
    purging = [*copied, *_job(run_id, _START_PURGE, backup=backup)]
    assert _attempt(head, purging) == (None, "")
    changed = _job(
        run_id,
        "UPDATE history_archive_runs SET pre_purge_backup_name = :backup WHERE id = :id",
        backup="20271111T050102Z-pre-purge-" + _ARCHIVE.format(run=run_id),
    )
    state, message = _attempt(head, [*purging, *changed])
    assert state == "P0001" and "recorded when the purge starts" in message
    recopied = _job(
        run_id,
        "UPDATE history_archive_runs SET copied_at = now() + interval '1 second' WHERE id = :id",
    )
    state, message = _attempt(head, [*purging, *recopied])
    assert state == "P0001" and "recorded once" in message
    # The application never cancels a purging run.
    cancel = (
        "UPDATE history_archive_runs SET state = 'CANCELLED', finished_at = now(),"
        " cancelled_by_user_id = :user WHERE id = :id",
        {"id": run_id, "user": user_id},
    )
    assert (
        _attempt(
            head, [*purging, ("SELECT set_config('partflow.archive_run_id', '', true)", {}), cancel]
        )[0]
        == "P0001"
    )
    # PURGING finishes with a result and its receipt.
    finish = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'COMPLETED', result = 'PURGED',"
        " finished_at = now() WHERE id = :id",
    )
    assert _attempt(head, [*purging, *finish])[0] == "23514"
    purged = _job(
        run_id,
        "UPDATE history_archive_runs SET purged_components = 1, purged_flows = 1,"
        " purged_movements = 1 WHERE id = :id",
    )
    receipt = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'COMPLETED', result = 'PURGED',"
        " receipt_sha256 = :sha, finished_at = now() WHERE id = :id",
        sha="ef" * 32,
    )
    assert _attempt(head, [*purging, *purged, *receipt]) == (None, "")
    nothing = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'COMPLETED', result = 'NOTHING_PURGED',"
        " receipt_sha256 = :sha, finished_at = now() WHERE id = :id",
        sha="ef" * 32,
    )
    assert _attempt(head, [*purging, *purged, *nothing])[0] == "23514"


@pytest.mark.parametrize(
    ("change", "allowed"),
    [
        ("purged_components = 1, purged_flows = 2, purged_movements = 5", True),
        ("skipped_components = skipped_components + 1, skipped_flows = skipped_flows + 2", True),
        ("verified_at = verified_at + interval '1 second'", False),
        ("manifest_sha256 = repeat('cd', 32)", False),
        ("exported_movements = exported_movements + 1", False),
        ("copied_at = now()", False),
        ("failure_reason = 'x'", False),
    ],
)
def test_a_purging_run_changes_only_its_counts(head: Engine, change: str, allowed: bool) -> None:
    """Review finding 6: while PURGING the job changes only what its batches raise."""
    run_id, user_id = _waiting_run(head)
    # Confirmed, not copied: a first copied_at would still be possible otherwise.
    purging = [
        *_job(run_id, "UPDATE history_archive_runs SET state = 'WAITING_FOR_COPY' WHERE id = :id"),
        ("SELECT set_config('partflow.archive_run_id', '', true)", {}),
        (
            "UPDATE history_archive_runs SET copy_confirmed_at = now(),"
            " copy_confirmed_by_user_id = :user WHERE id = :id",
            {"id": run_id, "user": user_id},
        ),
        *_job(run_id, "UPDATE history_archive_runs SET state = 'RUNNING' WHERE id = :id"),
        *_job(run_id, _START_PURGE, backup=_backup_name(run_id)),
    ]
    assert _attempt(head, purging) == (None, "")
    state, message = _attempt(
        head, [*purging, *_job(run_id, f"UPDATE history_archive_runs SET {change} WHERE id = :id")]
    )
    if allowed:
        assert (state, message) == (None, "")
    else:
        assert state == "P0001" and "changes only its purged and skipped counts" in message


def test_a_purging_run_never_lowers_its_counts(head: Engine) -> None:
    run_id, _user = _waiting_run(head)
    copied = _job(run_id, "UPDATE history_archive_runs SET copied_at = now() WHERE id = :id")
    purging = [*copied, *_job(run_id, _START_PURGE, backup=_backup_name(run_id))]
    raised = _job(
        run_id,
        "UPDATE history_archive_runs SET purged_components = 2, purged_flows = 2,"
        " purged_movements = 2 WHERE id = :id",
    )
    assert _attempt(head, [*purging, *raised]) == (None, "")
    lowered = _job(
        run_id,
        "UPDATE history_archive_runs SET purged_components = 1, purged_flows = 1,"
        " purged_movements = 1 WHERE id = :id",
    )
    state, message = _attempt(head, [*purging, *raised, *lowered])
    assert state == "P0001" and "only upwards" in message


def test_the_old_completions_and_export_only_are_refused(head: Engine) -> None:
    run_id, _user = _waiting_run(head)
    copied = _job(run_id, "UPDATE history_archive_runs SET copied_at = now() WHERE id = :id")
    complete = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'COMPLETED', result = 'EXPORT_ONLY',"
        " finished_at = now() WHERE id = :id",
    )
    state, message = _attempt(head, [*copied, *complete])
    assert state == "P0001" and "no longer ends as EXPORT_ONLY" in message
    to_completed = _job(
        run_id,
        "UPDATE history_archive_runs SET state = 'COMPLETED', result = 'PURGED',"
        " finished_at = now() WHERE id = :id",
    )
    state, message = _attempt(head, [*copied, *to_completed])
    assert state == "P0001" and "cannot change RUNNING to COMPLETED" in message
    waiting = _job(
        run_id, "UPDATE history_archive_runs SET state = 'WAITING_FOR_COPY' WHERE id = :id"
    )
    back = _job(run_id, "UPDATE history_archive_runs SET state = 'RUNNING' WHERE id = :id")
    state, message = _attempt(head, [*waiting, *back])
    assert state == "P0001" and "copied or confirmed" in message
    assert _attempt(head, [*waiting, *copied, *back]) == (None, "")


def test_a_record_is_skipped_while_its_run_purges(head: Engine) -> None:
    scene = _scene(head)
    main = scene.runs["main"]
    skip = (
        "UPDATE history_archived_flows SET skipped_at = now() WHERE run_id = :run AND flow_id = :f",
        {"run": main, "f": scene.flows["flow"]},
    )
    assert _attempt(head, [_marked(main), skip]) == (None, "")
    # Never both: a purged record is not skipped (the CHECK).
    skip_purged = (
        "UPDATE history_archived_flows SET skipped_at = now() WHERE run_id = :run AND flow_id = :f",
        {"run": main, "f": scene.flows["purged"]},
    )
    assert _attempt(head, [_marked(main), skip_purged])[0] == "23514"


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def _trigger_definition(engine: Engine, name: str) -> str | None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL search_path = pg_catalog, public"))
        return connection.execute(
            sa.text("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = :name"),
            {"name": name},
        ).scalar()


def test_the_downgrade_restores_0040(head_database: URL, head: Engine) -> None:
    command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert _version(connection) == _PREVIOUS_REVISION
        sources: dict[str, str] = {
            str(name): str(source)
            for name, source in connection.execute(
                sa.text(
                    "SELECT proname, prosrc FROM pg_proc WHERE proname IN"
                    " ('partflow_history_archive_runs_guard_mutation',"
                    " 'partflow_history_archived_flows_guard_mutation',"
                    " 'partflow_part_movements_guard_delete')"
                )
            )
        }
    assert set(sources) == {
        "partflow_history_archive_runs_guard_mutation",
        "partflow_history_archived_flows_guard_mutation",
    }
    previous = _load(_PREVIOUS_FILE, "history_archive_migration_downgrade")
    assert sources["partflow_history_archive_runs_guard_mutation"] == _body(
        previous._CREATE_RUNS_FUNCTION
    )
    assert _trigger_definition(head, "trg_part_movements_forbid_mutation") == (
        "CREATE TRIGGER trg_part_movements_forbid_mutation BEFORE DELETE OR UPDATE OR TRUNCATE"
        " ON public.part_movements FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_part_movements_forbid_mutation()"
    )
    assert _trigger_definition(head, "trg_part_movements_guard_delete") is None
    assert not {
        "pre_purge_backup_name",
        "purge_started_at",
        "purged_movements",
        "receipt_sha256",
    } & {str(column["name"]) for column in inspect(head).get_columns("history_archive_runs")}
    assert "backup_keep_pre_purge" not in {
        str(column["name"]) for column in inspect(head).get_columns("application_policy")
    }
    command.upgrade(_alembic_config(head_database), "head")
    with head.connect() as connection:
        assert _version(connection) == _CODE_HEAD


@pytest.mark.parametrize(
    "trigger",
    ["purging-run", "batch-setting", "pre-purge-setting"],
)
def test_the_downgrade_refuses_while_purge_data_exists(
    head_database: URL, head: Engine, trigger: str
) -> None:
    if trigger == "purging-run":
        _scene(head)
    else:
        column = (
            "archive_purge_batch_movements = 1000"
            if trigger == "batch-setting"
            else "backup_keep_pre_purge = 5"
        )
        with head.begin() as connection:
            connection.execute(sa.text(f"UPDATE application_policy SET {column}"))
    with pytest.raises(DBAPIError, match="History purge data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert _version(connection) == _CODE_HEAD
