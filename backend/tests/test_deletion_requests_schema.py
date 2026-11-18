"""Integration tests for migration ``0042_phase16_deletion_requests`` (history archival, step 3).

Runs the real Alembic chain against isolated, temporary PostgreSQL databases
(created and dropped by the fixtures) and verifies:

- the revision in the chain (``0043_phase16_automatic_runs``, history
  archival step 4, is the head since; its own test pins the single head),
  the models matching the schema migrated to head, and the migration's
  literals against the models, the enums, ``permanent_deletion`` and
  migrations 0040 and 0041;
- the permission key: granted to every role that holds every other key
  (the seeded Super User), never to a role by its name, with one ``Role``
  audit row each;
- the request and row guards (shapes, who may insert, cancel and change a
  request, rows written only with their request);
- the row guard of every table a deletion deletes from — as the owner, the
  maintenance role and the application role (temporary roles granted by
  ``apply-grants``): only the stored rows of the RUNNING request named by the
  marker, by a role that may write archive records; without the marker, only
  the application's own deletes (by any role but the maintenance role);
- the statement guards keep refusing UPDATE and TRUNCATE; the maintenance
  role locks Work Order and demand rows but never changes one; its audit
  scope;
- the downgrade restores 0041 exactly and refuses while a request exists.
"""

import hashlib
import importlib.util
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
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
from app.application import backups, database_roles, permanent_deletion
from app.application.database_roles import DatabaseRoles
from app.domain.enums import DeletionRequestState, Permission
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
_PREVIOUS_REVISION = "0041_phase16_history_purge"
_HEAD_REVISION = "0042_phase16_deletion_requests"
_CODE_HEAD = schema_revision.code_head()
_MIGRATION_FILE = _VERSIONS / "20261118_0042_phase16_deletion_requests.py"
_PURGE_FILE = _VERSIONS / "20261118_0041_phase16_history_purge.py"
_ARCHIVE_FILE = _VERSIONS / "20261117_0040_phase16_history_archive.py"
_HEAD_TEMPLATE = "partflow_test_deletion_requests_schema_head"
_CASE = "partflow_test_deletion_requests_schema_case"
_PREVIOUS = "partflow_test_deletion_requests_schema_previous"
_KEY = Permission.DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY.value


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


def _mark(connection: Connection, request_id: int) -> None:
    sql, params = _marked(request_id)
    connection.execute(sa.text(sql), params)


def _marked(request_id: int) -> tuple[str, dict[str, Any]]:
    return (
        "SELECT set_config('partflow.deletion_request_id', :id, true)",
        {"id": str(request_id)},
    )


# ---------------------------------------------------------------------------
# Head and literals
# ---------------------------------------------------------------------------


def test_the_revision_is_in_the_chain() -> None:
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


def _body(create_function: str) -> str:
    """The ``prosrc`` of a ``CREATE FUNCTION … AS $$…$$`` statement."""
    return create_function.split("$$")[1]


def test_the_migration_literals_match_the_models_and_earlier_revisions() -> None:
    migration = _load(_MIGRATION_FILE, "deletion_requests_migration")
    purge = _load(_PURGE_FILE, "history_purge_migration_for_0042")
    archive = _load(_ARCHIVE_FILE, "history_archive_migration_for_0042")
    assert (migration.revision, migration.down_revision) == (_HEAD_REVISION, _PREVIOUS_REVISION)
    assert tuple(state.value for state in DeletionRequestState) == migration._STATES
    assert migration._FINAL_STATES == models.DELETION_REQUEST_FINAL_STATES
    assert migration._OPEN_STATES == models.DELETION_REQUEST_OPEN_STATES
    assert migration._TABLES == models.DELETION_TABLES == permanent_deletion.DELETE_ORDER
    assert migration._BACKUP_NAME_PATTERN == models.DELETION_REQUEST_BACKUP_NAME_PATTERN
    assert {
        f"ck_deletion_requests_{suffix}": sql for suffix, sql in migration._REQUEST_CHECKS
    } == _model_checks(models.DeletionRequest)
    assert {
        f"ck_deletion_request_rows_{suffix}": sql for suffix, sql in migration._ROW_CHECKS
    } == _model_checks(models.DeletionRequestRow)
    assert migration._in("state", migration._OPEN_STATES) == models.DELETION_REQUEST_OPEN_WHERE_SQL
    # The key and the audit vocabulary extend 0040's.
    assert migration._PREVIOUS_PERMISSIONS == archive._PERMISSIONS
    assert tuple(permission.value for permission in Permission) == migration._PERMISSIONS
    assert migration._PERMISSION_SQL == models.ROLE_PERMISSION_SQL
    assert migration._PREVIOUS_ENTITY_TYPES == archive._ENTITY_TYPES
    assert (
        _model_checks(models.AuditEvent)["ck_audit_events_entity_type"] == migration._ENTITY_TYPES
    )
    # The bodies the downgrade restores are 0041's Movement guard and 0040's audit scope.
    assert migration._PREVIOUS_MOVEMENTS_BODY == purge._DELETE_BODY
    assert _body(archive._CREATE_AUDIT_FUNCTION) == migration._PREVIOUS_AUDIT_BODY
    # Every function it creates or replaces, and every trigger, is pinned by check (h).
    for name, body in (
        (migration._REQUESTS_FUNCTION, migration._REQUESTS_BODY),
        (migration._ROWS_FUNCTION, migration._ROWS_BODY),
        (migration._GUARD_FUNCTION, migration._GUARD_BODY),
        (migration._LOCK_FUNCTION, migration._LOCK_BODY),
        (migration._MOVEMENTS_FUNCTION, migration._MOVEMENTS_BODY),
        (migration._AUDIT_FUNCTION, migration._AUDIT_BODY),
    ):
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == GUARD_FUNCTION_SHA256[name]
    for name, _statement, table in migration._TRIGGERS:
        assert GUARD_TRIGGERS[name].table == table, name
    for table in migration._STATEMENT_TABLES:
        assert GUARD_TRIGGERS[f"trg_{table}_forbid_mutation"].definition.startswith(
            f"CREATE TRIGGER trg_{table}_forbid_mutation BEFORE UPDATE OR TRUNCATE ON"
        )
    assert GUARD_TRIGGERS["trg_machine_lifecycle_events_forbid_mutation"].definition.startswith(
        "CREATE TRIGGER trg_machine_lifecycle_events_forbid_mutation BEFORE DELETE OR UPDATE OR"
        " TRUNCATE ON"
    )
    # The maintenance role deletes from exactly the tables a deletion deletes from.
    assert {
        table for table, privileges in MAINTENANCE_PRIVILEGES.items() if "DELETE" in privileges
    } == set(migration._TABLES)
    assert {
        table for table, privileges in MAINTENANCE_PRIVILEGES.items() if "UPDATE" in privileges
    } == {
        *migration._LOCKED_TABLES,
        "history_archive_runs",
        "history_archived_flows",
        migration._REQUESTS,
    }
    # The pre-purge backup of a request is labelled with it.
    assert backups.PRE_PURGE_LABEL_PATTERN.fullmatch("deletion-7-20271110T050102Z")
    assert backups.PRE_PURGE_LABEL_PATTERN.fullmatch("run-3-20271110T042000Z")
    assert not backups.PRE_PURGE_LABEL_PATTERN.fullmatch("delete-7-20271110T050102Z")


# ---------------------------------------------------------------------------
# The permission key
# ---------------------------------------------------------------------------


def _grants(connection: Connection) -> dict[str, set[str]]:
    rows = connection.execute(
        sa.text(
            "SELECT r.name, coalesce(array_agg(p.permission) FILTER (WHERE p.permission IS NOT"
            " NULL), '{}') AS keys FROM roles r LEFT JOIN role_permissions p ON p.role_id = r.id"
            " GROUP BY r.name"
        )
    )
    return {str(row.name): set(row.keys) for row in rows}


def _key_audits(connection: Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            sa.text(
                "SELECT entity_id, actor_user_id, before_data, after_data, metadata FROM"
                " audit_events WHERE entity_type = 'Role'"
                " AND metadata ->> 'revision' = :revision ORDER BY id"
            ),
            {"revision": _HEAD_REVISION},
        ).mappings()
    ]


def test_a_fresh_install_grants_the_key_to_super_user_only(head: Engine) -> None:
    with head.connect() as connection:
        grants = _grants(connection)
        holders = sorted(name for name, keys in grants.items() if _KEY in keys)
        assert holders == ["Super User"]
        assert grants["Super User"] == {permission.value for permission in Permission}
        assert _KEY not in grants["Administrator"]
        [audit] = _key_audits(connection)
        assert audit["actor_user_id"] is None
        assert audit["metadata"] == {"source": "migration", "revision": _HEAD_REVISION}
        assert set(audit["after_data"]["permissions"]) - set(
            audit["before_data"]["permissions"]
        ) == {_KEY}


@pytest.fixture
def previous_database(admin_engine: Engine) -> Iterator[URL]:
    _create_database(admin_engine, _PREVIOUS)
    url = _url(_PREVIOUS)
    command.upgrade(_alembic_config(url), _PREVIOUS_REVISION)
    yield url
    _drop_database(admin_engine, _PREVIOUS)


def test_the_key_goes_to_every_role_that_holds_every_other_key(previous_database: URL) -> None:
    previous_keys = [permission.value for permission in Permission if permission.value != _KEY]
    engine = create_engine(previous_database)
    try:
        with engine.begin() as connection:
            for name, keys in (
                ("Everything", previous_keys),
                ("Narrowed", previous_keys[1:]),
                ("ADMINISTRATOR", previous_keys[:5]),
            ):
                role_id = _scalar(
                    connection,
                    "INSERT INTO roles (name) VALUES (:name) RETURNING id",
                    name=name,
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO role_permissions (role_id, permission)"
                        " SELECT :role, unnest(CAST(:keys AS text[]))"
                    ),
                    {"role": role_id, "keys": keys},
                )
        command.upgrade(_alembic_config(previous_database), "head")
        with engine.connect() as connection:
            grants = _grants(connection)
            assert {name for name, keys in grants.items() if _KEY in keys} == {
                "Everything",
                "Super User",
            }
            assert len(_key_audits(connection)) == 2
        command.downgrade(_alembic_config(previous_database), _PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert not any(_KEY in keys for keys in _grants(connection).values())
            downgraded = connection.execute(
                sa.text(
                    "SELECT count(*) FROM audit_events WHERE entity_type = 'Role'"
                    " AND metadata ->> 'revision' = :revision AND metadata ? 'downgrade'"
                ),
                {"revision": _HEAD_REVISION},
            ).scalar_one()
            assert downgraded == 2
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# A scene: one row of every table a deletion deletes from, three times
# ---------------------------------------------------------------------------

_LABELS = ("A", "B", "C")


@dataclass
class Scene:
    user: int = 0
    area: int = 0
    operation: int = 0
    #: label → table → the row's primary key as text.
    rows: dict[str, dict[str, str]] = field(default_factory=dict)
    #: "running" (stores the A rows, RUNNING), "cancelled" (the B rows), "approved".
    requests: dict[str, int] = field(default_factory=dict)


def _rows_of(connection: Connection, scene: Scene, label: str) -> dict[str, str]:
    def one(sql: str, **params: object) -> str:
        return str(_scalar(connection, sql, **params))

    pn = f"PN-{label}"
    rows: dict[str, str] = {}
    rows["part_numbers"] = one(
        "INSERT INTO part_numbers (part_number) VALUES (:pn) RETURNING part_number", pn=pn
    )
    rows["work_orders"] = one(
        "INSERT INTO work_orders (work_order_number, received_date) VALUES (:n, current_date)"
        " RETURNING id",
        n=f"WO-{label}",
    )
    support = one(
        "INSERT INTO work_orders (work_order_number, received_date) VALUES (:n, current_date)"
        " RETURNING id",
        n=f"SUP-{label}",
    )
    rows["work_order_demands"] = one(
        "INSERT INTO work_order_demands (work_order_id, part_number, request_type,"
        " requested_quantity) VALUES (:wo, :pn, 'NEW', 1) RETURNING id",
        wo=int(support),
        pn=f"{pn}-1",
    )
    allocated = one(
        "INSERT INTO work_order_demands (work_order_id, part_number, request_type,"
        " requested_quantity) VALUES (:wo, :pn, 'NEW', 1) RETURNING id",
        wo=int(support),
        pn=f"{pn}-2",
    )
    rows["work_order_allocations"] = one(
        "INSERT INTO work_order_allocations (part_number, work_order_demand_id, quantity, source,"
        " allocated_at, device_event_id) VALUES (:pn, :demand, 1, 'MANAGEMENT', now(),"
        " gen_random_uuid()::text) RETURNING id",
        pn=f"{pn}-2",
        demand=int(allocated),
    )
    flows = [
        one(
            "INSERT INTO quantity_flows (part_number, quantity, current_area_id) VALUES"
            " (:pn, 1, :area) RETURNING id",
            pn=pn,
            area=scene.area,
        )
        for _ in range(4)
    ]
    rows["quantity_flows"] = flows[0]
    rows["part_movements"] = one(
        "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type, quantity,"
        " to_area_id, operation_id, occurred_at, server_received_at, device_event_id,"
        " command_sequence) VALUES (:flow, :pn, 'RECEIVED', 1, :area, :operation, now(), now(),"
        " gen_random_uuid()::text, 1) RETURNING id",
        flow=int(flows[1]),
        pn=pn,
        area=scene.area,
        operation=scene.operation,
    )
    rows["quantity_flow_lineage"] = one(
        "INSERT INTO quantity_flow_lineage (relation, parent_flow_id, child_flow_id,"
        " device_event_id) VALUES ('SPLIT', :parent, :child, gen_random_uuid()::text) RETURNING id",
        parent=int(flows[2]),
        child=int(flows[3]),
    )
    rows["assigned_routes"] = one("INSERT INTO assigned_routes DEFAULT VALUES RETURNING id")
    route = one("INSERT INTO assigned_routes DEFAULT VALUES RETURNING id")
    rows["assigned_route_steps"] = one(
        "INSERT INTO assigned_route_steps (assigned_route_id, sequence, area_id, operation_id)"
        " VALUES (:route, 1, :area, :operation) RETURNING id",
        route=int(route),
        area=scene.area,
        operation=scene.operation,
    )
    rows["audit_events"] = one(
        "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
        " VALUES ('CREATED', 'WorkOrder', :id, now()) RETURNING id",
        id=rows["work_orders"],
    )
    return rows


def _request(connection: Connection, scene: Scene, rows: dict[str, str]) -> int:
    """An operator's APPROVED request (recorded by the owner) storing ``rows``."""
    request_id = int(
        _scalar(
            connection,
            "INSERT INTO deletion_requests (state, work_order_numbers, part_numbers, reason,"
            " requested_by_operator, row_counts, records) VALUES ('APPROVED', :numbers, :pns,"
            " 'test', 'Ops', '{}', '{}') RETURNING id",
            numbers=["WO-X"],
            pns=[rows["part_numbers"]],
        )
    )
    for table, key in rows.items():
        connection.execute(
            sa.text(
                "INSERT INTO deletion_request_rows (request_id, table_name, row_key)"
                " VALUES (:id, :table, :key)"
            ),
            {"id": request_id, "table": table, "key": key},
        )
    return request_id


_START = (
    "UPDATE deletion_requests SET state = 'RUNNING', started_at = date_trunc('second', now()),"
    " backup_label = 'deletion-' || id || '-' || to_char(date_trunc('second', now())"
    " AT TIME ZONE 'UTC', 'YYYYMMDD\"T\"HH24MISS\"Z\"') WHERE id = :id"
)


def _scene(engine: Engine) -> Scene:
    scene = Scene()
    with engine.begin() as connection:
        role = _scalar(connection, "SELECT min(id) FROM roles")
        scene.user = int(
            _scalar(
                connection,
                "INSERT INTO users (login_name, display_name, role_id) VALUES ('ann', 'Ann', :r)"
                " RETURNING id",
                r=role,
            )
        )
        department = _scalar(connection, "INSERT INTO departments (name) VALUES ('D') RETURNING id")
        scene.area = int(
            _scalar(
                connection,
                "INSERT INTO areas (department_id, name) VALUES (:d, 'A') RETURNING id",
                d=department,
            )
        )
        scene.operation = int(
            _scalar(
                connection,
                "INSERT INTO operations (area_id, code) VALUES (:a, 'OP') RETURNING id",
                a=scene.area,
            )
        )
        for label in _LABELS:
            scene.rows[label] = _rows_of(connection, scene, label)
    with engine.begin() as connection:
        scene.requests["cancelled"] = _request(connection, scene, scene.rows["B"])
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE deletion_requests SET state = 'CANCELLED', finished_at = now(),"
                " cancelled_by_user_id = :user WHERE id = :id"
            ),
            {"user": scene.user, "id": scene.requests["cancelled"]},
        )
    with engine.begin() as connection:
        scene.requests["running"] = _request(connection, scene, scene.rows["A"])
    with engine.begin() as connection:
        _mark(connection, scene.requests["running"])
        connection.execute(sa.text(_START), {"id": scene.requests["running"]})
    return scene


def _delete(table: str, key: str) -> tuple[str, dict[str, Any]]:
    if table == "part_numbers":
        return ("DELETE FROM part_numbers WHERE part_number = :key", {"key": key})
    return (f"DELETE FROM {table} WHERE id = :key", {"key": int(key)})


_APPEND_ONLY = ("audit_events", "part_movements", "quantity_flow_lineage", "work_order_allocations")
_APPLICATION_DELETES = ("assigned_route_steps", "work_order_demands", "part_numbers")


# ---------------------------------------------------------------------------
# The row guards on every table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("as_maintenance", [False, True], ids=["owner", "maintenance"])
def test_the_job_deletes_only_the_stored_rows_of_the_running_request(
    head: Engine, roles: DatabaseRoles, as_maintenance: bool
) -> None:
    scene = _scene(head)
    role = roles.maintenance if as_maintenance else None
    running, cancelled = scene.requests["running"], scene.requests["cancelled"]
    for table in permanent_deletion.DELETE_ORDER:
        stored = _delete(table, scene.rows["A"][table])
        other = _delete(table, scene.rows["B"][table])
        outside = _delete(table, scene.rows["C"][table])
        refused = {
            "a row the request did not store": [_marked(running), outside],
            "a row of another request": [_marked(running), other],
            "the marker of a finished request": [_marked(cancelled), other],
            "an unknown request": [_marked(999999), stored],
            "a malformed marker": [
                ("SELECT set_config('partflow.deletion_request_id', 'x', true)", {}),
                stored,
            ],
            "one bad row in a statement": [
                _marked(running),
                stored,
                outside,
            ],
        }
        for case, statements in refused.items():
            state, message = _attempt(head, statements, role)
            assert state == "P0001", (table, case, message)
        assert _attempt(head, [_marked(running), stored], role) == (None, ""), table
        # Without the marker: refused, except the application's own deletes by a
        # role other than the maintenance role.
        state, _message = _attempt(head, [outside], role)
        if table in _APPLICATION_DELETES and not as_maintenance:
            assert state is None, table
        else:
            assert state == "P0001", table


def test_a_request_that_is_not_running_admits_nothing(head: Engine, roles: DatabaseRoles) -> None:
    scene = _scene(head)
    with head.begin() as connection:
        _mark(connection, scene.requests["running"])
        connection.execute(
            sa.text(
                "UPDATE deletion_requests SET state = 'FAILED', failure_reason = 'test',"
                " finished_at = now() WHERE id = :id"
            ),
            {"id": scene.requests["running"]},
        )
    for table in permanent_deletion.DELETE_ORDER:
        statements = [_marked(scene.requests["running"]), _delete(table, scene.rows["A"][table])]
        assert _attempt(head, statements, roles.maintenance)[0] == "P0001", table


def test_the_application_role_never_deletes_with_the_marker(
    head: Engine, roles: DatabaseRoles
) -> None:
    scene = _scene(head)
    running = scene.requests["running"]
    for table in permanent_deletion.DELETE_ORDER:
        stored = _delete(table, scene.rows["A"][table])
        state, _message = _attempt(head, [_marked(running), stored], roles.app)
        # No DELETE on an append-only table; elsewhere the guard: not a role that
        # may write archive records.
        assert state == ("42501" if table in _APPEND_ONLY else "P0001"), table
        # Its own deletes stay: a demand line, a future route step, a Part Number master.
        state, _message = _attempt(head, [_delete(table, scene.rows["C"][table])], roles.app)
        if table in _APPLICATION_DELETES:
            assert state is None, table
        else:
            assert state == ("42501" if table in _APPEND_ONLY else "P0001"), table


def test_updates_and_truncation_stay_refused(head: Engine, roles: DatabaseRoles) -> None:
    scene = _scene(head)
    for table in _APPEND_ONLY:
        key = int(scene.rows["A"][table])
        update: tuple[str, dict[str, Any]] = (
            f"UPDATE {table} SET id = id WHERE id = :key",
            {"key": key},
        )
        truncate: tuple[str, dict[str, Any]] = (f"TRUNCATE {table} CASCADE", {})
        for statement in (update, truncate):
            assert _attempt(head, [_marked(scene.requests["running"]), statement])[0] == "P0001"
            assert _attempt(head, [statement], roles.maintenance)[0] == "42501"
    # Machine lifecycle events keep refusing deletes too.
    assert _attempt(head, [("DELETE FROM machine_lifecycle_events", {})])[0] == "P0001"


def test_the_maintenance_role_locks_but_never_changes_work_orders(
    head: Engine, roles: DatabaseRoles
) -> None:
    scene = _scene(head)
    for table in ("work_orders", "work_order_demands"):
        key = int(scene.rows["C"][table])
        lock = (f"SELECT id FROM {table} WHERE id = :key FOR UPDATE", {"key": key})
        change = (f"UPDATE {table} SET updated_at = now() WHERE id = :key", {"key": key})
        nothing: tuple[str, dict[str, Any]] = (
            f"UPDATE {table} SET updated_at = now() WHERE false",
            {},
        )
        assert _attempt(head, [lock], roles.maintenance) == (None, ""), table
        for statement in (change, nothing):
            state, message = _attempt(
                head, [_marked(scene.requests["running"]), statement], roles.maintenance
            )
            assert state == "P0001" and "never changes" in message, table
        assert _attempt(head, [change], roles.app) == (None, ""), table
        assert _attempt(head, [change]) == (None, ""), table


_AUDIT = (
    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
    " VALUES (:event, :type, :id, now())"
)


def test_the_maintenance_role_appends_only_its_audit_rows(
    head: Engine, roles: DatabaseRoles
) -> None:
    scene = _scene(head)
    running = scene.requests["running"]
    stored_work_order = scene.rows["A"]["work_orders"]

    def audit(event: str, entity_type: str, entity_id: str, marker: int | None) -> str | None:
        statements = [] if marker is None else [_marked(marker)]
        statements.append((_AUDIT, {"event": event, "type": entity_type, "id": entity_id}))
        return _attempt(head, statements, roles.maintenance)[0]

    assert audit("UPDATED", "HistoryArchiveRun", "1", None) is None
    assert audit("UPDATED", "DeletionRequest", str(running), running) is None
    assert audit("UPDATED", "DeletionRequest", str(running), None) == "P0001"
    assert audit("UPDATED", "DeletionRequest", "999", running) == "P0001"
    assert audit("DELETED", "WorkOrder", stored_work_order, running) is None
    assert audit("DELETED", "WorkOrder", scene.rows["C"]["work_orders"], running) == "P0001"
    assert audit("UPDATED", "WorkOrder", stored_work_order, running) == "P0001"
    assert audit("DELETED", "PartNumber", "PN-A", running) is None
    assert audit("DELETED", "PartNumber", "PN-C", running) == "P0001"
    assert audit("DELETED", "WorkOrderDemand", "1", running) == "P0001"
    assert audit("DELETED", "WorkOrder", stored_work_order, scene.requests["cancelled"]) == "P0001"


# ---------------------------------------------------------------------------
# The request and row guards
# ---------------------------------------------------------------------------

_INSERT = (
    "INSERT INTO deletion_requests (state, work_order_numbers, reason, requested_by_user_id,"
    " requested_by_operator, row_counts, records) VALUES ('APPROVED', ARRAY['W'], 'test', :user,"
    " :operator, '{}', '{}')"
)


def test_the_request_guard(head: Engine, roles: DatabaseRoles) -> None:
    scene = _scene(head)
    insert = (_INSERT, {"user": scene.user, "operator": None})
    operator = (_INSERT, {"user": None, "operator": "Ops"})
    # Only one request is open at a time; the scene has one.
    assert _attempt(head, [insert])[0] == "23505"
    with head.begin() as connection:
        _mark(connection, scene.requests["running"])
        connection.execute(
            sa.text(
                "UPDATE deletion_requests SET state = 'FAILED', failure_reason = 'test',"
                " finished_at = now() WHERE id = :id"
            ),
            {"id": scene.requests["running"]},
        )
    assert _attempt(head, [insert], roles.app) == (None, "")
    assert _attempt(head, [operator])[0] is None
    # An operator's request only by the owner; never by the maintenance role.
    state, message = _attempt(head, [operator], roles.app)
    assert state == "P0001" and "only the database owner role" in message
    assert _attempt(head, [insert], roles.maintenance)[0] == "42501"
    # Recorded APPROVED, without progress, at the time of its transaction.
    for statement in (
        _INSERT.replace("'APPROVED'", "'RUNNING'"),
        _INSERT.replace("row_counts, records)", "row_counts, records, failure_reason)").replace(
            "'{}', '{}')", "'{}', '{}', 'x')"
        ),
        _INSERT.replace("row_counts, records)", "row_counts, records, requested_at)").replace(
            "'{}', '{}')", "'{}', '{}', now() - interval '1 day')"
        ),
    ):
        state, _message = _attempt(head, [(statement, {"user": scene.user, "operator": None})])
        assert state in ("P0001", "23514"), statement
    # The scope, the reason and the requester are checked.
    for statement, params in (
        (_INSERT.replace("ARRAY['W']", "'{}'"), {"user": scene.user, "operator": None}),
        (_INSERT, {"user": scene.user, "operator": "Ops"}),
        (_INSERT, {"user": None, "operator": None}),
        (_INSERT.replace("'test'", "''"), {"user": scene.user, "operator": None}),
    ):
        assert _attempt(head, [(statement, params)])[0] == "23514", (statement, params)


def _approved(head: Engine, scene: Scene) -> int:
    """Finish the scene's running request, then record a new APPROVED one by a User."""
    with head.begin() as connection:
        _mark(connection, scene.requests["running"])
        connection.execute(
            sa.text(
                "UPDATE deletion_requests SET state = 'FAILED', failure_reason = 'test',"
                " finished_at = now() WHERE id = :id"
            ),
            {"id": scene.requests["running"]},
        )
    with head.begin() as connection:
        return int(_scalar(connection, _INSERT + " RETURNING id", user=scene.user, operator=None))


def test_who_changes_a_request(head: Engine, roles: DatabaseRoles) -> None:
    scene = _scene(head)
    request_id = _approved(head, scene)
    by_id = {"id": request_id, "user": scene.user}
    cancel = (
        "UPDATE deletion_requests SET state = 'CANCELLED', finished_at = now(),"
        " cancelled_by_user_id = :user WHERE id = :id",
        by_id,
    )
    start = (_START, by_id)
    fail = (
        "UPDATE deletion_requests SET state = 'FAILED', failure_reason = 'x', finished_at = now()"
        " WHERE id = :id",
        by_id,
    )
    reason = ("UPDATE deletion_requests SET reason = 'other' WHERE id = :id", by_id)
    # The application cancels an APPROVED request, and does nothing else.
    assert _attempt(head, [cancel], roles.app) == (None, "")
    for statement in (start, fail, reason):
        assert _attempt(head, [_marked(request_id), statement], roles.app)[0] == "P0001"
    # The maintenance role starts or fails it only as the job (the marker), never cancels.
    assert _attempt(head, [start], roles.maintenance)[0] == "P0001"
    assert _attempt(head, [_marked(request_id), start], roles.maintenance) == (None, "")
    assert _attempt(head, [_marked(request_id), fail], roles.maintenance) == (None, "")
    assert _attempt(head, [_marked(request_id), cancel], roles.maintenance)[0] == "P0001"
    assert _attempt(head, [_marked(request_id), reason], roles.maintenance)[0] == "P0001"
    # A RUNNING request is never cancelled; COMPLETED needs its backup evidence.
    with head.begin() as connection:
        _mark(connection, request_id)
        connection.execute(sa.text(_START), by_id)
    assert _attempt(head, [cancel], roles.app)[0] == "P0001"
    complete = (
        "UPDATE deletion_requests SET state = 'COMPLETED', finished_at = now() WHERE id = :id",
        by_id,
    )
    assert _attempt(head, [_marked(request_id), complete], roles.maintenance)[0] == "23514"
    evidence = (
        "UPDATE deletion_requests SET state = 'COMPLETED', finished_at = now(),"
        " pre_purge_backup_name = to_char(started_at AT TIME ZONE 'UTC',"
        " 'YYYYMMDD\"T\"HH24MISS\"Z\"') || '-pre-purge-' || backup_label,"
        " pre_purge_backup_started_at = started_at WHERE id = :id",
        by_id,
    )
    wrong_label = (
        "UPDATE deletion_requests SET state = 'COMPLETED', finished_at = now(),"
        " pre_purge_backup_name = '20271110T050102Z-pre-purge-deletion-1-20271110T050102Z',"
        " pre_purge_backup_started_at = started_at WHERE id = :id",
        by_id,
    )
    assert _attempt(head, [_marked(request_id), wrong_label], roles.maintenance)[0] == "23514"
    assert _attempt(head, [_marked(request_id), evidence], roles.maintenance) == (None, "")
    with head.begin() as connection:
        _mark(connection, request_id)
        connection.execute(sa.text(evidence[0]), by_id)
    # Finished: it never changes, is never deleted or truncated.
    for statement in (
        fail,
        reason,
        ("DELETE FROM deletion_requests WHERE id = :id", by_id),
        ("TRUNCATE deletion_requests CASCADE", {}),
    ):
        assert _attempt(head, [_marked(request_id), statement])[0] == "P0001", statement


def test_the_rows_are_written_only_with_their_request(head: Engine, roles: DatabaseRoles) -> None:
    scene = _scene(head)
    request_id = _approved(head, scene)
    add = (
        "INSERT INTO deletion_request_rows (request_id, table_name, row_key)"
        " VALUES (:id, 'work_orders', '424242')",
        {"id": request_id},
    )
    # A later transaction cannot add a row to a recorded request.
    state, message = _attempt(head, [add])
    assert state == "P0001" and "written only with the request" in message
    assert _attempt(head, [add], roles.app)[0] == "P0001"
    assert _attempt(head, [add], roles.maintenance)[0] == "42501"
    stored = scene.requests["cancelled"]
    for statement in (
        (
            "UPDATE deletion_request_rows SET row_key = row_key WHERE request_id = :id",
            {"id": stored},
        ),
        ("DELETE FROM deletion_request_rows WHERE request_id = :id", {"id": stored}),
        ("TRUNCATE deletion_request_rows", {}),
    ):
        assert _attempt(head, [statement])[0] == "P0001", statement
        assert _attempt(head, [statement], roles.maintenance)[0] == "42501", statement


def test_a_request_and_its_rows_are_one_transaction(head: Engine) -> None:
    scene = _scene(head)
    _approved(head, scene)
    with head.connect() as connection:
        open_id = connection.execute(
            sa.text("SELECT id FROM deletion_requests WHERE state = 'APPROVED'")
        ).scalar_one()
    with head.begin() as connection:
        _mark(connection, open_id)
        connection.execute(
            sa.text(
                "UPDATE deletion_requests SET state = 'FAILED', failure_reason = 'x',"
                " finished_at = now() WHERE id = :id"
            ),
            {"id": open_id},
        )
    for table, key, expected in (
        ("users", "1", "23514"),
        ("work_orders", "x", "23514"),
        ("part_numbers", "lower", "23514"),
        ("part_numbers", "PN-OK", None),
        ("work_orders", "12", None),
    ):
        state, _message = _attempt(
            head,
            [
                (
                    "WITH request AS (" + _INSERT + " RETURNING id)"
                    " INSERT INTO deletion_request_rows (request_id, table_name, row_key)"
                    " SELECT id, :table, :key FROM request",
                    {"user": scene.user, "operator": None, "table": table, "key": key},
                )
            ],
        )
        assert state == expected, (table, key)


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------


def _trigger_events(engine: Engine, name: str) -> str:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL search_path = pg_catalog, public"))
        return str(
            connection.execute(
                sa.text("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = :name"),
                {"name": name},
            ).scalar_one()
        )


def test_the_downgrade_restores_0041(head_database: URL, head: Engine) -> None:
    config = _alembic_config(head_database)
    command.downgrade(config, _PREVIOUS_REVISION)
    purge = _load(_PURGE_FILE, "history_purge_migration_for_0042_downgrade")
    with head.connect() as connection:
        tables = set(sa.inspect(connection).get_table_names())
        assert not {"deletion_requests", "deletion_request_rows"} & tables
        sources = {
            str(name): str(source)
            for name, source in connection.execute(
                sa.text(
                    "SELECT p.proname, p.prosrc FROM pg_proc p JOIN pg_namespace n"
                    " ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
                )
            )
        }
        assert sources["partflow_part_movements_guard_delete"] == purge._DELETE_BODY
        assert "partflow_guard_delete" not in sources
        assert not any(_KEY in keys for keys in _grants(connection).values())
    for table in ("audit_events", "quantity_flow_lineage", "work_order_allocations"):
        assert f"BEFORE DELETE OR UPDATE OR TRUNCATE ON public.{table}" in _trigger_events(
            head, f"trg_{table}_forbid_mutation"
        )
    command.upgrade(config, "head")
    for table in ("audit_events", "quantity_flow_lineage", "work_order_allocations"):
        assert f"BEFORE UPDATE OR TRUNCATE ON public.{table}" in _trigger_events(
            head, f"trg_{table}_forbid_mutation"
        )


def test_the_downgrade_refuses_while_a_request_exists(head_database: URL, head: Engine) -> None:
    _scene(head)
    with pytest.raises(DBAPIError, match="Permanent deletion request data exists"):
        command.downgrade(_alembic_config(head_database), _PREVIOUS_REVISION)
    with head.connect() as connection:
        assert (
            connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            == _CODE_HEAD
        )
