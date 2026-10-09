"""Tests for the database roles, grants and check (h) (Phase 16 slice 4).

``provision-roles`` creates or repairs the application and maintenance
database roles; ``apply-grants`` (also inside every ``migrate``) derives
every privilege from ``database_privileges``; reconcile check (h)
verifies guard integrity. Every case runs on its own
``partflow_test_*`` database with uniquely named temporary roles
(``tests.role_harness``) that are dropped afterwards; the production
names and the development database are never touched. Ids: DC-1…4,
GR-1…6, AG-1…14, PR-1…11, H-1…24 (S4 SPEC §6.1).

The module needs a superuser cluster role and keeps it in the
application-role test mode (``database_owner``): it creates roles,
corrupts guards and grants, and switches roles itself.
"""

import contextlib
import hashlib
import importlib.util
import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import psycopg
import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from alembic import command
from app import cli
from app.application import database_roles, migration, reconciliation
from app.application.database_roles import DatabaseRoles
from app.core.config import get_settings
from app.infrastructure import schema_revision
from app.infrastructure.database_privileges import (
    APP_PRIVILEGES,
    GUARD_FUNCTION_SHA256,
    GUARD_TABLES,
    GUARD_TRIGGERS,
    MAINTENANCE_SELECT,
    TABLE_CLASSES,
    TableClass,
)
from app.infrastructure.models import Base
from tests.conftest import owner_engine
from tests.role_harness import (
    acl_snapshot,
    cluster_engine,
    drop_roles,
    new_password,
    temporary_name,
    temporary_roles,
    write_password_files,
)

pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DATABASE = "partflow_test_database_roles"
_FRESH_DATABASE = "partflow_test_database_roles_fresh"
_OWNED_DATABASE = "partflow_test_database_roles_owned"
_HEAD = schema_revision.code_head()
_PREVIOUS = "0031_phase14_beyond_demand"
_NOT_SUPERUSER = (
    "The database-role tests need a superuser test database role (the Compose and CI"
    " partflow_user is one)."
)
_APPEND_ONLY = (
    "audit_events",
    "machine_lifecycle_events",
    "part_movements",
    "quantity_flow_lineage",
    "work_order_allocations",
)
_GUARDED = (*_APPEND_ONLY, "assigned_route_steps", "worker_sessions")
_GRANT_KEYS = [
    "report_version",
    "command",
    "result",
    "exit_code",
    "started_at",
    "finished_at",
    "duration_ms",
    "release",
    "expected_revision",
    "database_revision",
    "grants",
    "error",
]
_PROVISION_KEYS = [
    "report_version",
    "command",
    "result",
    "exit_code",
    "started_at",
    "finished_at",
    "duration_ms",
    "owner_role",
    "roles",
    "error",
]


def _cluster_url() -> URL:
    return make_url(os.environ["DATABASE_URL"])


#: The cluster (owner) URL at import, before any case repoints DATABASE_URL.
_CLUSTER_URL = _cluster_url()


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


class Context(NamedTuple):
    url: URL
    engine: Engine
    roles: DatabaseRoles
    passwords: DatabaseRoles
    files: DatabaseRoles
    owner: str
    baseline: dict[str, Any]

    def format(self, statement: str, **extra: str) -> str:
        return statement.format(
            app=self.roles.app,
            maint=self.roles.maintenance,
            db=_DATABASE,
            owner=self.owner,
            **extra,
        )

    def execute(self, *statements: str, **extra: str) -> None:
        with self.engine.begin() as connection:
            for statement in statements:
                connection.execute(sa.text(self.format(statement, **extra)))

    def snapshot(self) -> dict[str, Any]:
        with self.engine.connect() as connection:
            return acl_snapshot(connection)

    def provision(self) -> list[dict[str, object]]:
        with self.engine.begin() as connection:
            return database_roles.provision_roles(
                connection, roles=self.roles, passwords=self.passwords, lock_timeout_seconds=5
            )

    def grant(self) -> dict[str, object]:
        with self.engine.begin() as connection:
            return database_roles.apply_grants(connection, roles=self.roles, required=True)

    def check_h(self, *, required: bool = True, roles: DatabaseRoles | None = None) -> Any:
        report = reconciliation.run_reconciliation(
            self.engine,
            checks=["h"],
            expected_alembic_revision=_HEAD,
            database_roles=roles or self.roles,
            roles_required=required,
        )
        document: dict[str, Any] = reconciliation.report_document(report)
        [check] = [check for check in document["checks"] if check["id"] == "h"]
        return document["exit_code"], check

    def assert_clean(self) -> None:
        exit_code, check = self.check_h()
        assert (exit_code, check["status"]) == (0, "pass"), json.dumps(check, indent=1)

    def restore(self) -> None:
        self.provision()
        self.grant()
        self.assert_clean()


def _create_database(admin: Engine, name: str, *, template: str | None = None) -> None:
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        suffix = f' TEMPLATE "{template}"' if template else ""
        connection.execute(sa.text(f'CREATE DATABASE "{name}"{suffix}'))


def _drop_database(admin: Engine, name: str) -> None:
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def context(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Context]:
    cluster = _cluster_url()
    admin = cluster_engine(cluster)
    with admin.connect() as connection:
        owner, superuser = connection.execute(
            sa.text("SELECT rolname, rolsuper FROM pg_roles WHERE rolname = current_user")
        ).one()
    if not superuser:
        pytest.fail(_NOT_SUPERUSER)
    _create_database(admin, _DATABASE)
    url = cluster.set(database=_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    # A pristine copy at head: the default ACLs a --no-privileges restore leaves.
    _create_database(admin, _FRESH_DATABASE, template=_DATABASE)
    roles = temporary_roles()
    passwords = DatabaseRoles(new_password(), new_password())
    files = write_password_files(tmp_path_factory.mktemp("roles"), passwords)
    engine = owner_engine(url)
    try:
        partial = Context(url, engine, roles, passwords, files, str(owner), {})
        partial.provision()
        partial.grant()
        built = partial._replace(baseline=partial.snapshot())
        built.assert_clean()
        yield built
    finally:
        engine.dispose()
        _drop_database(admin, _FRESH_DATABASE)
        _drop_database(admin, _DATABASE)
        drop_roles(cluster, roles)
        admin.dispose()


@pytest.fixture
def extra_roles() -> Iterator[list[str]]:
    """Further temporary roles a case creates; dropped afterwards."""
    names: list[str] = []
    # Read now: a case may point DATABASE_URL at another role.
    cluster = _cluster_url()
    try:
        yield names
    finally:
        drop_roles(cluster, names)


def _create_role(name: str, attributes: str = "NOLOGIN", password: str | None = None) -> None:
    admin = cluster_engine(_CLUSTER_URL)
    try:
        with admin.connect() as connection:
            clause = ""
            if password is not None:
                literal = connection.execute(sa.text("SELECT quote_literal(:p)"), {"p": password})
                clause = f" PASSWORD {literal.scalar_one()}"
            connection.execute(sa.text(f'CREATE ROLE "{name}" {attributes}{clause}'))
    finally:
        admin.dispose()


# ---------------------------------------------------------------------------
# In-process CLI runs
# ---------------------------------------------------------------------------


class CliRun(NamedTuple):
    exit_code: int
    document: dict[str, Any]
    stdout: str
    stderr: str


@pytest.fixture
def cli_run(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., CliRun]:
    def run(
        *arguments: str, url: URL, roles: DatabaseRoles, environment: dict[str, str] | None = None
    ) -> CliRun:
        monkeypatch.setattr(database_roles, "APP_ROLE", roles.app)
        monkeypatch.setattr(database_roles, "MAINTENANCE_ROLE", roles.maintenance)
        monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
        for name, value in (environment or {}).items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        capsys.readouterr()
        try:
            code = cli.main(list(arguments))
        finally:
            get_settings.cache_clear()
        out, err = capsys.readouterr()
        assert out.splitlines().count(f'  "exit_code": {code},') == 1, out
        assert out.startswith("{\n") and out.endswith("\n}\n")
        return CliRun(code, json.loads(out), out, err)

    return run


def _apply_cli(cli_run: Callable[..., CliRun], context: Context, url: URL | None = None) -> CliRun:
    return cli_run("apply-grants", url=url or context.url, roles=context.roles)


def _provision_cli(
    cli_run: Callable[..., CliRun], url: URL, roles: DatabaseRoles, files: DatabaseRoles
) -> CliRun:
    return cli_run(
        "provision-roles",
        "--app-password-file",
        files.app,
        "--maintenance-password-file",
        files.maintenance,
        url=url,
        roles=roles,
    )


def _login(url: URL, user: str, password: str) -> str:
    """A real LOGIN connection; returns current_user."""
    target = url.set(username=user, password=password)
    with psycopg.connect(
        host=target.host,
        port=target.port or 5432,
        dbname=target.database,
        user=user,
        password=password,
        connect_timeout=10,
    ) as connection:
        row = connection.execute("SELECT current_user").fetchone()
        assert row is not None
        return str(row[0])


def _refused(document: dict[str, Any], code: str) -> None:
    assert document["result"] == "refused", document
    assert document["exit_code"] == 1
    assert document["error"]["code"] == code, document["error"]


# ---------------------------------------------------------------------------
# DC — classification
# ---------------------------------------------------------------------------


def test_every_model_table_is_classified() -> None:
    """DC-1."""
    assert set(Base.metadata.tables) | {"alembic_version"} == set(TABLE_CLASSES)
    assert all(isinstance(value, TableClass) for value in TABLE_CLASSES.values())
    assert set(MAINTENANCE_SELECT) <= set(TABLE_CLASSES)
    assert len(MAINTENANCE_SELECT) == 14
    assert not {"user_credentials", "user_sessions", "scan_station_devices"} & MAINTENANCE_SELECT


def test_the_migrated_relations_are_the_classified_ones(context: Context) -> None:
    """DC-2."""
    with context.engine.connect() as connection:
        relations = set(
            connection.execute(
                sa.text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
                    " ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                    " AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
                )
            ).scalars()
        )
    assert relations == set(TABLE_CLASSES)
    assert len(relations) == 29


def _triggers(engine: Engine) -> dict[str, tuple[str, str, str, str]]:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL search_path = pg_catalog, public"))
        rows = connection.execute(
            sa.text(
                "SELECT t.tgname, c.relname, p.proname, pg_get_triggerdef(t.oid), t.tgenabled"
                " FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_proc p ON p.oid = t.tgfoid"
                " WHERE NOT t.tgisinternal AND n.nspname = 'public'"
            )
        )
        return {str(r[0]): (str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows}


def test_the_guard_map_matches_the_migrations(context: Context) -> None:
    """DC-3: pinned definitions (also under another database search_path) and hashes."""
    first = _triggers(context.engine)
    try:
        context.execute("ALTER DATABASE {db} SET search_path = pg_catalog")
        other = create_engine(context.url, poolclass=sa.pool.NullPool)
        try:
            second = _triggers(other)
        finally:
            other.dispose()
    finally:
        context.execute("ALTER DATABASE {db} RESET search_path")
    assert first == second
    assert set(first) == set(GUARD_TRIGGERS)
    for name, guard in GUARD_TRIGGERS.items():
        table, function, definition, enabled = first[name]
        assert (table, function, definition, enabled) == (
            guard.table,
            guard.function,
            guard.definition,
            "O",
        )
    with context.engine.connect() as connection:
        sources: dict[str, str] = dict(
            connection.execute(  # type: ignore[arg-type]
                sa.text(
                    "SELECT p.proname, p.prosrc FROM pg_proc p"
                    " JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
                )
            ).all()
        )
    assert set(sources) == set(GUARD_FUNCTION_SHA256)
    for name, digest in GUARD_FUNCTION_SHA256.items():
        assert hashlib.sha256(sources[name].encode("utf-8")).hexdigest() == digest, name
    assert {guard.function for guard in GUARD_TRIGGERS.values()} == set(GUARD_FUNCTION_SHA256)
    assert tuple(sorted({guard.table for guard in GUARD_TRIGGERS.values()})) == GUARD_TABLES


def test_guards_and_grants_are_consistent() -> None:
    """DC-4: a whole-table guard never forbids what its table's class grants."""
    for guard in GUARD_TRIGGERS.values():
        match = re.search(r" BEFORE (.+?) ON public\.", guard.definition)
        assert match is not None
        if " OF " in match.group(1) or " WHEN " in guard.definition:
            continue
        events = set(match.group(1).split(" OR "))
        overlap = events & APP_PRIVILEGES[TABLE_CLASSES[guard.table]]
        expected = events & {"UPDATE"} if guard.table == "worker_sessions" else set()
        assert overlap == expected, guard
    guarded = {
        table
        for table, table_class in TABLE_CLASSES.items()
        if table_class in (TableClass.APPEND_ONLY, TableClass.GUARDED_UPDATE, TableClass.NO_UPDATE)
    }
    assert set(GUARD_TABLES) - {"areas", "machines"} == guarded == set(_GUARDED)


# ---------------------------------------------------------------------------
# GR — privileges
# ---------------------------------------------------------------------------


def _as_role(
    context: Context, role: str, statement: str, *, disable_triggers: str | None = None
) -> str | None:
    """SQLSTATE of ``statement`` run as ``role`` (None: it succeeded); always rolled back."""
    with context.engine.connect() as connection:
        transaction = connection.begin()
        try:
            if disable_triggers is not None:
                connection.execute(sa.text(f"ALTER TABLE {disable_triggers} DISABLE TRIGGER USER"))
            connection.execute(sa.text(f'SET LOCAL ROLE "{role}"'))
            connection.execute(sa.text(statement))
            return None
        except DBAPIError as exc:
            return str(getattr(exc.orig, "sqlstate", None))
        finally:
            transaction.rollback()


_MATRIX = (
    "UPDATE {t} SET id = id WHERE false",
    "DELETE FROM {t} WHERE false",
    "TRUNCATE {t}",
)


def _allowed(table: str, statement: str) -> bool:
    return (table == "worker_sessions" and statement.startswith("UPDATE")) or (
        table == "assigned_route_steps" and statement.startswith("DELETE")
    )


@pytest.mark.parametrize("disabled", [False, True])
def test_the_application_role_cannot_change_guarded_history(
    context: Context, disabled: bool
) -> None:
    """GR-1: privilege checks precede triggers (also with the triggers disabled)."""
    for table in _GUARDED:
        for template in _MATRIX:
            statement = template.format(t=table)
            outcome = _as_role(
                context,
                context.roles.app,
                statement,
                disable_triggers=table if disabled else None,
            )
            expected = None if _allowed(table, statement) else "42501"
            assert outcome == expected, (statement, disabled)


def test_a_real_login_of_the_application_role_is_refused_too(context: Context) -> None:
    """GR-1 with a real LOGIN connection (no SET ROLE)."""
    target = context.url.set(username=context.roles.app, password=context.passwords.app)
    with psycopg.connect(
        host=target.host,
        port=target.port or 5432,
        dbname=target.database,
        user=context.roles.app,
        password=context.passwords.app,
    ) as connection:
        assert connection.execute("SELECT current_user").fetchone() == (context.roles.app,)
        for template in _MATRIX:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(template.format(t="part_movements"))
            connection.rollback()


def _first_columns(context: Context) -> dict[str, str]:
    with context.engine.connect() as connection:
        return {
            str(table): str(column)
            for table, column in connection.execute(
                sa.text(
                    "SELECT c.relname, a.attname FROM pg_attribute a"
                    " JOIN pg_class c ON c.oid = a.attrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attnum = 1"
                )
            )
        }


def test_the_maintenance_role_reads_its_tables_only(context: Context) -> None:
    """GR-2."""
    role = context.roles.maintenance
    for table in sorted(MAINTENANCE_SELECT):
        assert _as_role(context, role, f"SELECT * FROM {table} WHERE false") is None, table
    for table in (
        "user_credentials",
        "user_sessions",
        "scan_station_devices",
        "audit_events",
        "work_orders",
    ):
        assert _as_role(context, role, f"SELECT * FROM {table} WHERE false") == "42501", table
    columns = _first_columns(context)
    for table in sorted(TABLE_CLASSES):
        column = columns[table]
        for statement in (
            f"INSERT INTO {table} DEFAULT VALUES",
            f"UPDATE {table} SET {column} = {column} WHERE false",
            f"DELETE FROM {table} WHERE false",
            f"TRUNCATE {table}",
        ):
            assert _as_role(context, role, statement) == "42501", statement


def test_identity_inserts_need_no_sequence_privilege(context: Context) -> None:
    """GR-3."""
    app = context.roles.app
    assert (
        _as_role(context, app, "INSERT INTO departments (name) VALUES ('S4') RETURNING id") is None
    )
    assert (
        _as_role(
            context,
            app,
            "INSERT INTO machine_asset_tag_config (id, prefix, digits) VALUES (1, 'MC', 4)",
        )
        is None
    )
    assert _as_role(context, app, "SELECT nextval('part_movements_id_seq')") == "42501"


def test_alembic_version_is_read_only(context: Context) -> None:
    """GR-4."""
    app = context.roles.app
    assert _as_role(context, app, "SELECT version_num FROM alembic_version") is None
    assert _as_role(context, app, "UPDATE alembic_version SET version_num = version_num") == "42501"


def test_row_locks(context: Context) -> None:
    """GR-5: the worker-session lock the code takes needs the UPDATE it keeps."""
    app = context.roles.app
    for mode in ("FOR NO KEY UPDATE", "FOR KEY SHARE"):
        assert _as_role(context, app, f"SELECT id FROM worker_sessions WHERE false {mode}") is None
        assert (
            _as_role(context, app, f"SELECT id FROM part_movements WHERE false {mode}") == "42501"
        )


def test_no_object_can_be_created(context: Context) -> None:
    """GR-6."""
    app = context.roles.app
    assert _as_role(context, app, "CREATE TABLE s4_probe (i int)") == "42501"
    assert _as_role(context, app, "CREATE SCHEMA s4_probe") == "42501"


# ---------------------------------------------------------------------------
# AG — apply_grants / apply-grants
# ---------------------------------------------------------------------------


def test_apply_grants_on_a_fresh_database_twice(
    context: Context, cli_run: Callable[..., CliRun]
) -> None:
    """AG-1, AG-11."""
    url = context.url.set(database=_FRESH_DATABASE)
    fresh = owner_engine(url)
    try:
        snapshots = []
        for _ in range(2):
            result = _apply_cli(cli_run, context, url)
            assert result.exit_code == 0, result.stderr
            assert list(result.document) == _GRANT_KEYS
            assert result.document["result"] == "applied"
            assert result.document["release"] == "development"
            assert result.document["expected_revision"] == _HEAD
            assert result.document["database_revision"] == _HEAD
            assert result.document["error"] is None
            assert result.document["grants"] == {
                "status": "applied",
                "roles": {
                    "application": context.roles.app,
                    "maintenance": context.roles.maintenance,
                },
                "tables": 29,
                "sequences": 23,
                "default_privileges_removed": 0,
                "foreign_grantees": [],
            }
            assert result.stderr.startswith(
                f"apply-grants: granted 29 tables to {context.roles.app} and"
                f" {context.roles.maintenance} at revision {_HEAD} ("
            )
            # AG-11: no URL, password or path.
            password = _CLUSTER_URL.password
            for text in (result.stdout, result.stderr):
                assert "postgresql" not in text
                assert str(_BACKEND_DIR) not in text
                assert password is None or password not in text
            with fresh.connect() as connection:
                snapshots.append(acl_snapshot(connection))
        assert snapshots[0] == snapshots[1]
        assert snapshots[0]["relacl"] == context.baseline["relacl"]
        report = reconciliation.run_reconciliation(
            fresh,
            checks=["h"],
            expected_alembic_revision=_HEAD,
            database_roles=context.roles,
            roles_required=True,
        )
        assert reconciliation.result_of(report) == ("clean", 0)
    finally:
        fresh.dispose()


def test_apply_grants_repairs_drift(context: Context, cli_run: Callable[..., CliRun]) -> None:
    """AG-2."""
    try:
        context.execute(
            'GRANT UPDATE ON part_movements TO "{app}"',
            "GRANT SELECT ON audit_events TO PUBLIC",
            'GRANT UPDATE (quantity) ON part_movements TO "{app}"',
            'GRANT USAGE ON SEQUENCE part_movements_id_seq TO "{app}"',
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT INSERT ON TABLES TO "{app}"',
            'GRANT CREATE ON SCHEMA public TO "{app}"',
            'GRANT CREATE ON DATABASE "{db}" TO PUBLIC',
        )
        result = _apply_cli(cli_run, context)
        assert result.exit_code == 0, result.stderr
        assert result.document["grants"]["default_privileges_removed"] == 1
        context.assert_clean()
        assert context.snapshot() == context.baseline
    finally:
        context.restore()


def test_an_unclassified_table_refuses(context: Context, cli_run: Callable[..., CliRun]) -> None:
    """AG-3."""
    try:
        context.execute("CREATE TABLE s4_extra (i int)")
        before = context.snapshot()
        result = _apply_cli(cli_run, context)
        assert result.exit_code == 1
        _refused(result.document, "table_unclassified")
        assert result.document["grants"] is None
        assert result.document["error"]["message"] == (
            "Table s4_extra has no privilege class in this release, so it cannot be granted."
            " Nothing was changed."
        )
        assert context.snapshot() == before
    finally:
        context.execute("DROP TABLE IF EXISTS s4_extra")
    context.assert_clean()


@pytest.mark.parametrize(
    ("corruption", "code", "reason", "repair"),
    [
        ('ALTER ROLE "{app}" CREATEDB', "role_unsafe", "has the CREATEDB attribute", None),
        (
            'GRANT pg_write_all_data TO "{app}"',
            "role_unsafe",
            "is a member of pg_write_all_data",
            None,
        ),
        (
            'ALTER TABLE route_steps OWNER TO "{app}"',
            "role_owns_objects",
            None,
            'ALTER TABLE route_steps OWNER TO "{owner}"',
        ),
    ],
)
def test_an_unsafe_role_refuses(
    context: Context,
    cli_run: Callable[..., CliRun],
    corruption: str,
    code: str,
    reason: str | None,
    repair: str | None,
) -> None:
    """AG-4."""
    try:
        context.execute(corruption)
        before = context.snapshot()
        result = _apply_cli(cli_run, context)
        _refused(result.document, code)
        if reason is not None:
            assert result.document["error"]["message"] == (
                f"The database role {context.roles.app} {reason}. Run provision-roles to restore"
                " its safe attributes, then try again. Nothing was changed."
            )
        else:
            # The identity sequence follows its table's owner.
            assert result.document["error"]["message"] == (
                f"The database role {context.roles.app} owns 2 database objects (for example"
                f" sequence route_steps_id_seq). Give them back to the owner role (REASSIGN"
                f" OWNED BY {context.roles.app} TO {context.owner}) after review, then run"
                " apply-grants"
                " (or the release) again. Nothing was changed."
            )
        assert context.snapshot() == before
    finally:
        if repair is not None:
            context.execute(repair)
        context.restore()


def _statements(engine: Engine) -> tuple[list[str], Callable[[], None]]:
    seen: list[str] = []

    def spy(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    return seen, lambda: event.remove(engine, "before_cursor_execute", spy)


def test_roles_absent_or_incomplete(
    context: Context,
    cli_run: Callable[..., CliRun],
    extra_roles: list[str],
    tmp_path: Path,
) -> None:
    """AG-5."""
    fresh = temporary_roles()
    result = _apply_cli(cli_run, context._replace(roles=fresh))
    _refused(result.document, "roles_not_provisioned")
    assert result.document["error"]["message"] == (
        f"The database roles {fresh.app} and {fresh.maintenance} do not exist. Run"
        " provision-roles first. Nothing was changed."
    )
    seen, stop = _statements(context.engine)
    try:
        with context.engine.begin() as connection:
            outcome = database_roles.apply_grants(connection, roles=fresh, required=False)
    finally:
        stop()
    assert outcome == {
        "status": "not_provisioned",
        "detail": (
            f"The database roles {fresh.app} and {fresh.maintenance} do not exist; grants were"
            " not applied (development or test database)."
        ),
    }
    assert seen and all(statement.lstrip().startswith("SELECT") for statement in seen), seen
    with (
        context.engine.begin() as connection,
        pytest.raises(database_roles.DatabaseRolesRefusal) as refused,
    ):
        database_roles.apply_grants(connection, roles=fresh, required=True)
    assert refused.value.code == "roles_not_provisioned"

    # A non-superuser LOGIN role without CREATEDB that owns its database (S3 M-16's shape).
    login = temporary_name("login")
    password = new_password()
    extra_roles.append(login)
    _create_role(login, "LOGIN NOSUPERUSER NOCREATEDB", password)
    admin = cluster_engine(_CLUSTER_URL)
    try:
        _create_database(admin, _OWNED_DATABASE)
        with admin.connect() as connection:
            connection.execute(sa.text(f'ALTER DATABASE "{_OWNED_DATABASE}" OWNER TO "{login}"'))
        owned = create_engine(
            _CLUSTER_URL.set(database=_OWNED_DATABASE, username=login, password=password)
        )
        try:
            with owned.begin() as connection:
                assert database_roles.apply_grants(connection, roles=fresh, required=False)[
                    "status"
                ] == ("not_provisioned")
        finally:
            owned.dispose()
    finally:
        _drop_database(admin, _OWNED_DATABASE)
        admin.dispose()

    # Only the application role present: roles_incomplete in all three.
    half = temporary_roles()
    extra_roles.append(half.app)
    _create_role(half.app)
    message = (
        f"The database role {half.maintenance} does not exist ({half.app} does). Run"
        " provision-roles. Nothing was changed."
    )
    result = _apply_cli(cli_run, context._replace(roles=half))
    _refused(result.document, "roles_incomplete")
    assert result.document["error"]["message"] == message
    for required in (False, True):
        with (
            context.engine.begin() as connection,
            pytest.raises(database_roles.DatabaseRolesRefusal) as refused,
        ):
            database_roles.apply_grants(connection, roles=half, required=required)
        assert (refused.value.code, refused.value.message) == ("roles_incomplete", message)


def test_apply_grants_as_a_non_superuser(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str]
) -> None:
    """AG-6: refused before the revision read (never internal_error)."""
    login = temporary_name("login")
    password = new_password()
    extra_roles.append(login)
    _create_role(login, "LOGIN", password)
    url = context.url.set(username=login, password=password)
    result = _apply_cli(cli_run, context, url)
    _refused(result.document, "not_superuser")
    assert result.document["error"]["message"] == (
        f"apply-grants must run as the database owner role (POSTGRES_USER); the connected"
        f" database role {login} is not a superuser. Nothing was changed."
    )
    assert result.document["database_revision"] is None


def test_apply_grants_needs_the_release_revision(
    context: Context, cli_run: Callable[..., CliRun]
) -> None:
    """AG-7."""
    try:
        context.execute(f"UPDATE alembic_version SET version_num = '{_PREVIOUS}'")
        result = _apply_cli(cli_run, context)
        _refused(result.document, "revision_mismatch")
        assert result.document["database_revision"] == _PREVIOUS
        assert result.document["error"]["message"] == (
            f"The database is at revision {_PREVIOUS}, but this release grants for revision"
            f" {_HEAD}. Run apply-grants with the release that matches the database, or migrate"
            " first. Nothing was changed."
        )
    finally:
        context.execute(f"UPDATE alembic_version SET version_num = '{_HEAD}'")


def test_a_running_migrate_refuses(context: Context, cli_run: Callable[..., CliRun]) -> None:
    """AG-8."""
    with context.engine.connect() as connection, connection.begin():
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended('partflow:migrate', 0))")
        )
        result = _apply_cli(cli_run, context)
    _refused(result.document, "migrate_running")
    assert result.document["error"]["message"] == (
        "A migrate or another apply-grants is running on this database. Nothing was changed."
    )


def test_a_verification_failure_rolls_back(
    context: Context, cli_run: Callable[..., CliRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG-9."""
    real = database_roles._privilege_findings

    def broken(*args: Any, **kwargs: Any) -> list[database_roles.GuardFinding]:
        found = real(*args, **kwargs)
        return [*found, database_roles.GuardFinding("PRIVILEGE_EXCESS", "Table", "x/y", [], [], {})]

    try:
        context.execute('GRANT UPDATE ON part_movements TO "{app}"')
        before = context.snapshot()
        monkeypatch.setattr(database_roles, "_privilege_findings", broken)
        result = _apply_cli(cli_run, context)
        monkeypatch.undo()
        assert result.exit_code == 2
        assert result.document["result"] == "failed"
        assert result.document["error"] == {
            "code": "internal_error",
            "message": "apply-grants failed unexpectedly (internal error). Nothing was changed.",
        }
        assert "Traceback" in result.stderr
        assert context.snapshot() == before
    finally:
        context.restore()


def test_a_commit_failure_is_an_unknown_outcome(
    context: Context, cli_run: Callable[..., CliRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG-10."""

    def lost(transaction: Any) -> None:
        raise OperationalError("COMMIT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(migration, "commit_transaction", lost)
    result = _apply_cli(cli_run, context)
    assert result.exit_code == 2
    assert result.document["result"] == "outcome_unknown"
    assert result.document["error"] == {
        "code": "outcome_unknown",
        "message": (
            "The database connection failed while committing: the grants may or may not have"
            " been applied. Run apply-grants again (it is safe to repeat)."
        ),
    }


def test_a_foreign_grantee_is_left_in_place(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str]
) -> None:
    """AG-12."""
    foreign = temporary_name("foreign")
    extra_roles.append(foreign)
    _create_role(foreign)
    try:
        context.execute(
            'GRANT SELECT ON audit_events TO "{foreign}"',
            'GRANT USAGE ON SEQUENCE audit_events_id_seq TO "{foreign}"',
            foreign=foreign,
        )
        result = _apply_cli(cli_run, context)
        assert result.exit_code == 0, result.stderr
        assert result.document["grants"]["foreign_grantees"] == [
            f"audit_events/{foreign}",
            f"audit_events_id_seq/{foreign}",
        ]
        assert result.stderr.rstrip().endswith(
            "; 2 privileges of other database roles were left in place (see reconcile check h)"
        )
        after = context.snapshot()
        changed = {"audit_events", "audit_events_id_seq"}
        relacl = dict(after["relacl"])
        for name in changed:
            assert f"{foreign}=" in relacl[name]
        assert [entry for entry in after["relacl"] if entry[0] not in changed] == [
            entry for entry in context.baseline["relacl"] if entry[0] not in changed
        ]
        exit_code, check = context.check_h()
        assert exit_code == 1
        assert {(f["code"], f["entity"]["id"]) for f in check["findings"]} == {
            ("PRIVILEGE_EXCESS", f"audit_events/{foreign}"),
            ("PRIVILEGE_EXCESS", f"audit_events_id_seq/{foreign}"),
        }
    finally:
        context.execute(
            'REVOKE ALL ON audit_events FROM "{foreign}"',
            'REVOKE ALL ON SEQUENCE audit_events_id_seq FROM "{foreign}"',
            foreign=foreign,
        )
    context.assert_clean()


def test_a_foreign_grantor_refuses(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str]
) -> None:
    """AG-13."""
    foreign = temporary_name("foreign")
    extra_roles.append(foreign)
    _create_role(foreign)
    try:
        context.execute(
            'GRANT UPDATE ON part_movements TO "{foreign}" WITH GRANT OPTION',
            'SET LOCAL ROLE "{foreign}"',
            'GRANT UPDATE ON part_movements TO "{app}"',
            foreign=foreign,
        )
        before = context.snapshot()
        result = _apply_cli(cli_run, context)
        _refused(result.document, "foreign_grantor")
        app = context.roles.app
        assert result.document["error"]["message"] == (
            f"The database role {app} holds UPDATE on part_movements, granted by {foreign}, a"
            " database role PartFlow does not manage. Review it and revoke it as that role"
            f" (SET ROLE {foreign}; REVOKE ALL ON part_movements FROM {app} CASCADE), then run"
            " apply-grants (or the release) again. Nothing was changed."
        )
        assert context.snapshot() == before
    finally:
        context.execute('REVOKE ALL ON part_movements FROM "{foreign}" CASCADE', foreign=foreign)
    context.restore()


def test_a_grant_option_chain_is_revoked(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str]
) -> None:
    """AG-14."""
    foreign = temporary_name("foreign")
    extra_roles.append(foreign)
    _create_role(foreign)
    context.execute(
        'GRANT SELECT ON audit_events TO "{app}" WITH GRANT OPTION',
        'SET LOCAL ROLE "{app}"',
        'GRANT SELECT ON audit_events TO "{foreign}"',
        foreign=foreign,
    )
    result = _apply_cli(cli_run, context)
    assert result.exit_code == 0, result.stderr
    with context.engine.connect() as connection:
        acl = connection.execute(
            sa.text("SELECT relacl::text FROM pg_class WHERE relname = 'audit_events'")
        ).scalar_one()
    assert foreign not in acl
    assert f"{context.roles.app}=ar/" in acl
    context.assert_clean()


# ---------------------------------------------------------------------------
# PR — provision_roles / provision-roles
# ---------------------------------------------------------------------------


def _role_row(context: Context, name: str) -> Any:
    with context.engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT a.rolcanlogin, a.rolsuper, a.rolcreatedb, a.rolcreaterole,"
                " a.rolreplication, a.rolbypassrls, a.rolinherit, a.rolconnlimit,"
                " a.rolvaliduntil = 'infinity', a.rolpassword,"
                " (SELECT count(*) FROM pg_auth_members WHERE member = a.oid),"
                " (SELECT count(*) FROM pg_db_role_setting WHERE setrole = a.oid)"
                " FROM pg_authid a WHERE a.rolname = :name"
            ),
            {"name": name},
        ).one()


def test_provision_create_rerun_and_rotate(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str], tmp_path: Path
) -> None:
    """PR-1, PR-2, PR-3."""
    roles = temporary_roles()
    extra_roles.extend(roles)
    passwords = DatabaseRoles(new_password(), new_password())
    files = write_password_files(tmp_path / "secrets", passwords)
    result = _provision_cli(cli_run, context.url, roles, files)
    assert result.exit_code == 0, result.stderr
    assert list(result.document) == _PROVISION_KEYS
    assert result.document["result"] == "provisioned"
    assert result.document["owner_role"] == context.owner
    assert result.document["roles"] == [
        {
            "name": roles.app,
            "purpose": "application",
            "action": "created",
            "attributes_fixed": [],
            "memberships_revoked": [],
            "settings_reset": False,
            "password": "set",
        },
        {
            "name": roles.maintenance,
            "purpose": "maintenance",
            "action": "created",
            "attributes_fixed": [],
            "memberships_revoked": [],
            "settings_reset": False,
            "password": "set",
        },
    ]
    assert result.stderr.startswith(
        f"provision-roles: {roles.app} created, {roles.maintenance} created; passwords set from"
        " the secret files ("
    )
    for name, password in zip(roles, passwords, strict=True):
        row = _role_row(context, name)
        assert tuple(row[:9]) == (True, False, False, False, False, False, True, -1, True)
        assert str(row[9]).startswith("SCRAM-SHA-256$4096:")
        assert (row[10], row[11]) == (0, 0)
        assert _login(context.url, name, password) == name

    # PR-2: rerun.
    result = _provision_cli(cli_run, context.url, roles, files)
    assert result.exit_code == 0, result.stderr
    assert [role["action"] for role in result.document["roles"]] == ["unchanged", "unchanged"]
    assert {role["password"] for role in result.document["roles"]} == {"set"}
    assert _login(context.url, roles.app, passwords.app) == roles.app

    # PR-3: rotation of the application password.
    rotated = new_password()
    Path(files.app).write_text(rotated + "\n", encoding="utf-8")
    assert _provision_cli(cli_run, context.url, roles, files).exit_code == 0
    with pytest.raises(psycopg.OperationalError):
        _login(context.url, roles.app, passwords.app)
    assert _login(context.url, roles.app, rotated) == roles.app
    assert _login(context.url, roles.maintenance, passwords.maintenance) == roles.maintenance


def test_provision_repairs_drift(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str]
) -> None:
    """PR-4."""
    second = temporary_name("owner")
    extra_roles.append(second)
    _create_role(second, "SUPERUSER NOLOGIN")
    try:
        context.execute(
            'ALTER ROLE "{app}" CREATEDB CREATEROLE BYPASSRLS',
            'GRANT pg_write_all_data TO "{app}"',
            # PG16: the recorded grantor must hold ADMIN on the granted role.
            'GRANT pg_read_all_data TO "{second}" WITH ADMIN OPTION',
            'GRANT pg_read_all_data TO "{app}" GRANTED BY "{second}"',
            'ALTER ROLE "{app}" IN DATABASE "{db}" SET session_replication_role = \'replica\'',
            second=second,
        )
        result = _provision_cli(cli_run, context.url, context.roles, context.files)
        assert result.exit_code == 0, result.stderr
        app, maintenance = result.document["roles"]
        assert app["action"] == "updated"
        assert app["attributes_fixed"] == ["BYPASSRLS", "CREATEDB", "CREATEROLE"]
        assert app["memberships_revoked"] == ["pg_read_all_data", "pg_write_all_data"]
        assert app["settings_reset"] is True
        assert maintenance["action"] == "unchanged"
        context.assert_clean()

        context.execute(
            "ALTER ROLE \"{maint}\" NOLOGIN CONNECTION LIMIT 0 VALID UNTIL '2000-01-01'"
        )
        result = _provision_cli(cli_run, context.url, context.roles, context.files)
        assert result.exit_code == 0, result.stderr
        app, maintenance = result.document["roles"]
        assert app["action"] == "unchanged"
        assert maintenance["action"] == "updated"
        assert maintenance["attributes_fixed"] == ["CONNECTION LIMIT", "LOGIN", "VALID UNTIL"]
        assert _login(context.url, context.roles.maintenance, context.passwords.maintenance)
        context.assert_clean()
    finally:
        context.restore()


def test_provision_revokes_a_membership_the_role_granted_on(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str]
) -> None:
    """PR-4 variant: the role used an ADMIN OPTION to grant the membership on (PG16)."""
    third = temporary_name("member")
    extra_roles.append(third)
    _create_role(third)
    try:
        context.execute(
            'GRANT pg_read_all_data TO "{app}" WITH ADMIN OPTION',
            'GRANT pg_read_all_data TO "{third}" GRANTED BY "{app}"',
            third=third,
        )
        result = _provision_cli(cli_run, context.url, context.roles, context.files)
        assert result.exit_code == 0, result.stderr
        app, maintenance = result.document["roles"]
        assert app["action"] == "updated"
        assert app["memberships_revoked"] == ["pg_read_all_data"]
        assert maintenance["action"] == "unchanged"
        with context.engine.connect() as connection:
            granted = connection.execute(
                sa.text(
                    "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member"
                    " WHERE r.rolname = ANY(:names)"
                ),
                {"names": [context.roles.app, third]},
            ).scalar_one()
        assert granted == 0
        context.assert_clean()
    finally:
        context.restore()


def test_the_owner_name_may_not_be_a_partflow_role(
    context: Context, cli_run: Callable[..., CliRun], tmp_path: Path
) -> None:
    """PR-5."""
    roles = DatabaseRoles(context.owner, temporary_roles().maintenance)
    files = write_password_files(tmp_path, DatabaseRoles(new_password(), new_password()))
    result = _provision_cli(cli_run, context.url, roles, files)
    _refused(result.document, "role_name_conflict")
    assert result.document["error"]["message"] == (
        f"The database owner role is named {context.owner}, which is reserved for PartFlow. Use"
        " another POSTGRES_USER for this installation. Nothing was changed."
    )
    assert result.document["roles"] == []
    with context.engine.connect() as connection:
        assert not connection.execute(
            sa.text("SELECT count(*) FROM pg_roles WHERE rolname = :n"), {"n": roles.maintenance}
        ).scalar_one()


def test_provision_refuses_a_role_owning_objects(
    context: Context, cli_run: Callable[..., CliRun]
) -> None:
    """PR-6."""
    try:
        context.execute('ALTER TABLE route_steps OWNER TO "{app}"')
        result = _provision_cli(cli_run, context.url, context.roles, context.files)
        _refused(result.document, "role_owns_objects")
        assert result.document["error"]["message"] == (
            f"The database role {context.roles.app} owns 2 database objects (for example"
            f" sequence route_steps_id_seq). Give them back to the owner role (REASSIGN OWNED BY"
            f" {context.roles.app} TO {context.owner}) after review, then run provision-roles"
            " again. Nothing was changed."
        )
    finally:
        context.execute('ALTER TABLE route_steps OWNER TO "{owner}"')
        context.restore()


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (None, "cannot be read (No such file or directory)"),
        ("<directory>", "cannot be read (Is a directory)"),
        ("", "is empty"),
        ("a\nb", "must hold exactly one line"),
        ("x" * 15, "must hold 16 to 128 printable ASCII characters without spaces"),
        ("abcdefgh ijklmnopq", "must hold 16 to 128 printable ASCII characters without spaces"),
        ("abcdefghijklmnopé", "must hold 16 to 128 printable ASCII characters without spaces"),
        ("<equal>", None),
    ],
)
def test_password_file_problems(
    context: Context,
    cli_run: Callable[..., CliRun],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str | None,
    problem: str | None,
) -> None:
    """PR-7: checked before any setting or connection."""
    roles = temporary_roles()
    good = new_password()
    files = write_password_files(tmp_path, DatabaseRoles(good, new_password()))
    app_file = Path(files.app)
    if content is None:
        app_file.unlink()
    elif content == "<directory>":
        app_file.unlink()
        app_file.mkdir()
    elif content == "<equal>":
        Path(files.maintenance).write_text(good + "\n", encoding="utf-8")
    else:
        app_file.write_bytes(content.encode("utf-8"))
    opened: list[bool] = []

    def no_engine() -> Engine:
        opened.append(True)
        raise AssertionError("no connection may be opened")

    monkeypatch.setattr(cli, "_engine", no_engine)
    result = _provision_cli(cli_run, context.url, roles, files)
    assert result.exit_code == 2
    assert result.document["result"] == "failed"
    if problem is None:
        assert result.document["error"] == {
            "code": "passwords_equal",
            "message": (
                f"{roles.app} and {roles.maintenance} must have different passwords. Nothing"
                " was changed."
            ),
        }
    else:
        assert result.document["error"] == {
            "code": "password_file_invalid",
            "message": (
                f"The password file of {roles.app} ({app_file}) {problem}. Nothing was changed."
            ),
        }
    assert opened == []
    assert good not in result.stdout + result.stderr


def test_provision_as_a_non_superuser(
    context: Context, cli_run: Callable[..., CliRun], extra_roles: list[str], tmp_path: Path
) -> None:
    """PR-8."""
    login = temporary_name("login")
    password = new_password()
    extra_roles.append(login)
    _create_role(login, "LOGIN", password)
    files = write_password_files(tmp_path, DatabaseRoles(new_password(), new_password()))
    result = _provision_cli(
        cli_run, context.url.set(username=login, password=password), temporary_roles(), files
    )
    _refused(result.document, "not_superuser")
    assert result.document["owner_role"] == login


def test_a_running_provision_refuses(context: Context, cli_run: Callable[..., CliRun]) -> None:
    """PR-9."""
    with context.engine.connect() as connection, connection.begin():
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended('partflow:provision-roles', 0))")
        )
        result = _provision_cli(cli_run, context.url, context.roles, context.files)
    _refused(result.document, "provision_running")
    assert result.document["error"]["message"] == (
        "Another provision-roles is running on this database. Nothing was changed."
    )


def test_provision_commit_failure_then_rerun(
    context: Context, cli_run: Callable[..., CliRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-10."""

    def lost(transaction: Any) -> None:
        raise OperationalError("COMMIT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(migration, "commit_transaction", lost)
    result = _provision_cli(cli_run, context.url, context.roles, context.files)
    assert result.exit_code == 2
    assert result.document["result"] == "outcome_unknown"
    assert result.document["error"]["message"] == (
        "The database connection failed while committing: the database roles may or may not"
        " have been changed. Run provision-roles again (it is safe to repeat)."
    )
    monkeypatch.setattr(migration, "commit_transaction", lambda transaction: transaction.commit())
    assert _provision_cli(cli_run, context.url, context.roles, context.files).exit_code == 0


def test_no_password_ever_appears(
    context: Context, cli_run: Callable[..., CliRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR-11."""
    seen: list[str] = []

    def spy(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", spy)
    try:
        result = _provision_cli(cli_run, context.url, context.roles, context.files)
    finally:
        event.remove(Engine, "before_cursor_execute", spy)
    assert result.exit_code == 0, result.stderr
    for password in context.passwords:
        for text in (*seen, result.stdout, result.stderr):
            assert password not in text
    literals = [statement for statement in seen if " PASSWORD " in statement]
    assert len(literals) == 2
    for statement in literals:
        assert re.search(r" PASSWORD 'SCRAM-SHA-256\$", statement), statement


# ---------------------------------------------------------------------------
# H — check (h)
# ---------------------------------------------------------------------------


def test_check_h_passes_through_the_cli(
    context: Context, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-1."""
    monkeypatch.setattr(database_roles, "APP_ROLE", context.roles.app)
    monkeypatch.setattr(database_roles, "MAINTENANCE_ROLE", context.roles.maintenance)
    monkeypatch.setenv("DATABASE_URL", context.url.render_as_string(hide_password=False))
    monkeypatch.setenv("DATABASE_ROLES_REQUIRED", "true")
    get_settings.cache_clear()
    try:
        code = cli.main(["reconcile", "--check", "h"])
    finally:
        get_settings.cache_clear()
    document = json.loads(capsys.readouterr().out)
    assert code == 0
    [check] = [check for check in document["checks"] if check["id"] == "h"]
    assert check["status"] == "pass"
    assert check["title"] == "Append-only guards and database-role privileges intact"
    assert check["examined"] == {
        "roles": 2,
        "tables": 29,
        "sequences": 23,
        "triggers": 11,
        "guard_functions": 10,
    }


def _ids(check: dict[str, Any]) -> set[tuple[str, str]]:
    return {(finding["code"], str(finding["entity"]["id"])) for finding in check["findings"]}


class HCase(NamedTuple):
    id: str
    corruption: tuple[str, ...]
    expected: set[tuple[str, str]]
    repair: str  # "grants" | "provision" | "incident"
    restore: tuple[str, ...] = ()


_H_CASES = [
    HCase(
        "H-2",
        ('GRANT UPDATE ON part_movements TO "{app}"',),
        {("PRIVILEGE_EXCESS", "part_movements/{app}")},
        "grants",
    ),
    HCase(
        "H-3",
        ('GRANT SELECT ON user_credentials TO "{maint}"',),
        {("PRIVILEGE_EXCESS", "user_credentials/{maint}")},
        "grants",
    ),
    HCase(
        "H-4",
        ('REVOKE INSERT ON audit_events FROM "{app}"',),
        {("PRIVILEGE_MISSING", "audit_events/{app}")},
        "grants",
    ),
    HCase(
        "H-5",
        ('GRANT SELECT ON audit_events TO "{app}" WITH GRANT OPTION',),
        {("PRIVILEGE_EXCESS", "audit_events/{app}")},
        "grants",
    ),
    HCase(
        "H-6",
        ('GRANT UPDATE (quantity) ON part_movements TO "{app}"',),
        {("COLUMN_PRIVILEGE", "part_movements/{app}")},
        "grants",
    ),
    HCase(
        "H-7",
        (
            "GRANT SELECT ON audit_events TO PUBLIC",
            "GRANT USAGE ON SEQUENCE audit_events_id_seq TO PUBLIC",
        ),
        {
            ("PUBLIC_PRIVILEGE", "audit_events/PUBLIC"),
            ("PUBLIC_PRIVILEGE", "audit_events_id_seq/PUBLIC"),
        },
        "grants",
    ),
    HCase(
        "H-9",
        ("DROP TRIGGER trg_worker_sessions_forbid_truncate ON worker_sessions",),
        {("TRIGGER_MISSING", "trg_worker_sessions_forbid_truncate")},
        "incident",
        ("<0020:_GUARD_TRUNCATE_TRIGGER>",),
    ),
    HCase(
        "H-10",
        (
            "CREATE OR REPLACE FUNCTION partflow_audit_events_forbid_mutation() RETURNS trigger"
            " LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$",
        ),
        {("GUARD_FUNCTION_CHANGED", "partflow_audit_events_forbid_mutation")},
        "incident",
        ("<0004:_FORBID_MUTATION_FUNCTION>",),
    ),
    HCase(
        "H-11",
        (
            "DROP TRIGGER trg_areas_forbid_barcode_change ON areas",
            "CREATE TRIGGER trg_areas_forbid_barcode_change BEFORE UPDATE OF barcode_value"
            " ON areas FOR EACH ROW WHEN (false)"
            " EXECUTE FUNCTION partflow_areas_forbid_barcode_change()",
        ),
        {("TRIGGER_CHANGED", "trg_areas_forbid_barcode_change")},
        "incident",
        (
            "DROP TRIGGER trg_areas_forbid_barcode_change ON areas",
            "<0003:_FORBID_AREA_BARCODE_CHANGE_TRIGGER>",
        ),
    ),
    HCase(
        "H-12a",
        ('ALTER ROLE "{app}" CREATEDB', 'ALTER ROLE "{maint}" SUPERUSER'),
        {("ROLE_ATTRIBUTE", "{app}"), ("ROLE_ATTRIBUTE", "{maint}")},
        "provision",
    ),
    HCase(
        "H-12b",
        (
            "ALTER ROLE \"{app}\" VALID UNTIL '2000-01-01' CONNECTION LIMIT 0",
            'ALTER ROLE "{maint}" NOLOGIN',
        ),
        {("ROLE_ATTRIBUTE", "{app}"), ("ROLE_ATTRIBUTE", "{maint}")},
        "provision",
    ),
    HCase(
        "H-13",
        ('GRANT "{owner}" TO "{app}"', 'GRANT pg_write_all_data TO "{maint}"'),
        {("ROLE_MEMBERSHIP", "{app}"), ("ROLE_MEMBERSHIP", "{maint}")},
        "provision",
    ),
    HCase(
        "H-14a",
        ('ALTER FUNCTION partflow_part_movements_forbid_mutation() OWNER TO "{app}"',),
        {("ROLE_OWNS_OBJECTS", "{app}")},
        "incident",
        ('ALTER FUNCTION partflow_part_movements_forbid_mutation() OWNER TO "{owner}"',),
    ),
    HCase(
        "H-14b",
        (
            "CREATE SCHEMA s4_other",
            "CREATE TABLE s4_other.t (i int)",
            'ALTER TABLE s4_other.t OWNER TO "{maint}"',
        ),
        {("ROLE_OWNS_OBJECTS", "{maint}")},
        "incident",
        ("DROP SCHEMA s4_other CASCADE",),
    ),
    HCase(
        "H-15",
        (
            'GRANT CREATE ON SCHEMA public TO "{app}"',
            'GRANT CREATE ON DATABASE "{db}" TO "{maint}"',
        ),
        {("SCHEMA_PRIVILEGE", "public/{app}"), ("DATABASE_PRIVILEGE", "{db}/{maint}")},
        "grants",
    ),
    HCase(
        "H-16",
        ("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC",),
        {("DEFAULT_PRIVILEGE", "{owner}/public/TABLES/PUBLIC")},
        "grants",
    ),
    HCase(
        "H-17a",
        ('ALTER ROLE "{app}" IN DATABASE "{db}" SET session_replication_role = \'replica\'',),
        {("REPLICATION_ROLE_SETTING", "{db}/{app}")},
        "provision",
    ),
    HCase(
        "H-17b",
        ("ALTER DATABASE \"{db}\" SET session_replication_role = 'replica'",),
        {
            ("REPLICATION_ROLE_SETTING", "{db}/*"),
            ("REPLICATION_ROLE_ACTIVE", "session_replication_role"),
        },
        "incident",
        ('ALTER DATABASE "{db}" RESET session_replication_role',),
    ),
    HCase(
        "H-18",
        ("CREATE TABLE s4_extra (i int)",),
        {("TABLE_UNCLASSIFIED", "s4_extra")},
        "incident",
        ("DROP TABLE s4_extra",),
    ),
]


def _migration_module(revision: str) -> ModuleType:
    [path] = (_BACKEND_DIR / "alembic" / "versions").glob(f"*_{revision}_*.py")
    spec = importlib.util.spec_from_file_location(f"s4_guard_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _restore_statement(context: Context, statement: str) -> str:
    """``<rev:CONSTANT>`` → the migration's own text (CREATE FUNCTION made OR REPLACE)."""
    match = re.fullmatch(r"<(\d{4}):(\w+)>", statement)
    if match is None:
        return context.format(statement)
    text = str(getattr(_migration_module(match.group(1)), match.group(2)))
    return text.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")


def _run_restore(context: Context, statements: tuple[str, ...]) -> None:
    with context.engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(_restore_statement(context, statement).replace("%", "%%"))


def _expected(context: Context, ids: set[tuple[str, str]]) -> set[tuple[str, str]]:
    return {(code, context.format(entity)) for code, entity in ids}


@pytest.mark.parametrize("case", _H_CASES, ids=[case.id for case in _H_CASES])
def test_check_h_finds_and_maps_the_repair(context: Context, case: HCase) -> None:
    """H-2 … H-18 and H-22 (the repair mapping)."""
    restored = False
    try:
        context.execute(*case.corruption)
        # A database-wide setting reaches new sessions only.
        context.engine.dispose()
        exit_code, check = context.check_h()
        assert exit_code == 1
        assert check["status"] == "fail"
        assert _ids(check) == _expected(context, case.expected), json.dumps(check, indent=1)
        if case.repair == "grants":
            context.grant()
        elif case.repair == "provision":
            context.provision()
            context.grant()
        else:
            # H-22: neither command clears an incident finding.
            with contextlib.suppress(database_roles.DatabaseRolesRefusal):
                context.provision()
            with contextlib.suppress(database_roles.DatabaseRolesRefusal):
                context.grant()
            context.engine.dispose()
            assert _ids(context.check_h()[1]) >= _expected(context, case.expected)
            _run_restore(context, case.restore)
            restored = True
            context.engine.dispose()
        context.assert_clean()
    finally:
        if not restored:
            with contextlib.suppress(DBAPIError):
                _run_restore(context, case.restore)
        context.engine.dispose()
        context.restore()


def test_check_h_details(context: Context) -> None:
    """H-2, H-5, H-6, H-10, H-12 and H-14 detail values."""
    app, maint = context.roles
    try:
        context.execute('GRANT UPDATE ON part_movements TO "{app}"')
        [finding] = context.check_h()[1]["findings"]
        assert finding["expected"] == ["INSERT", "SELECT"]
        assert finding["actual"] == ["INSERT", "SELECT", "UPDATE"]
        assert finding["detail"] == {"grantee": app, "grantors": [context.owner]}
        assert finding["entity"]["type"] == "Table"
        assert finding["part_number"] is None
        context.grant()

        context.execute('GRANT SELECT ON audit_events TO "{app}" WITH GRANT OPTION')
        [finding] = context.check_h()[1]["findings"]
        assert finding["actual"] == ["INSERT", "SELECT*"]
        context.grant()

        context.execute('GRANT UPDATE (quantity) ON part_movements TO "{app}"')
        [finding] = context.check_h()[1]["findings"]
        assert finding["actual"] == ["UPDATE(quantity)"]
        context.grant()

        context.execute(
            "ALTER ROLE \"{app}\" VALID UNTIL '2000-01-01 00:00:00+00' CONNECTION LIMIT 0",
            'ALTER ROLE "{maint}" NOLOGIN',
        )
        findings = {f["entity"]["id"]: f for f in context.check_h()[1]["findings"]}
        assert findings[app]["actual"]["valid_until"] == "2000-01-01T00:00:00Z"
        assert findings[app]["actual"]["connection_limit"] == 0
        assert findings[maint]["actual"]["login"] is False
        assert findings[app]["expected"] == {
            "login": True,
            "superuser": False,
            "createdb": False,
            "createrole": False,
            "replication": False,
            "bypassrls": False,
            "connection_limit": -1,
            "valid_until": "infinity",
        }
        context.provision()

        context.execute(
            "CREATE SCHEMA s4_other",
            "CREATE TABLE s4_other.t (i int)",
            'ALTER TABLE s4_other.t OWNER TO "{maint}"',
        )
        [finding] = context.check_h()[1]["findings"]
        assert finding["detail"] == {"objects": ["table s4_other.t"]}
        for action in (context.provision, context.grant):
            with pytest.raises(database_roles.DatabaseRolesRefusal) as refused:
                action()
            assert refused.value.code == "role_owns_objects"
        context.execute("DROP SCHEMA s4_other CASCADE")

        context.execute(
            "CREATE OR REPLACE FUNCTION partflow_audit_events_forbid_mutation() RETURNS trigger"
            " LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$"
        )
        [finding] = context.check_h()[1]["findings"]
        assert finding["expected"] == GUARD_FUNCTION_SHA256["partflow_audit_events_forbid_mutation"]
        assert re.fullmatch(r"[0-9a-f]{64}", finding["actual"])
        assert finding["actual"] != finding["expected"]
    finally:
        with contextlib.suppress(DBAPIError):
            context.execute("DROP SCHEMA IF EXISTS s4_other CASCADE")
        _run_restore(context, ("<0004:_FORBID_MUTATION_FUNCTION>",))
        context.restore()


def test_trigger_enable_states(context: Context) -> None:
    """H-8: disabled, replica-only and always-enabled guards."""
    trigger = "trg_quantity_flow_lineage_forbid_mutation"
    try:
        for statement, code, state in (
            (
                f"ALTER TABLE quantity_flow_lineage DISABLE TRIGGER {trigger}",
                "TRIGGER_DISABLED",
                "D",
            ),
            (
                f"ALTER TABLE quantity_flow_lineage ENABLE REPLICA TRIGGER {trigger}",
                "TRIGGER_DISABLED",
                "R",
            ),
            (
                f"ALTER TABLE quantity_flow_lineage ENABLE ALWAYS TRIGGER {trigger}",
                "TRIGGER_ENABLE_MODE",
                "A",
            ),
        ):
            context.execute(statement)
            exit_code, check = context.check_h()
            assert exit_code == 1
            [finding] = check["findings"]
            assert (finding["code"], finding["entity"]["id"]) == (code, trigger)
            assert (finding["expected"], finding["actual"]) == ("O", state)
            assert finding["detail"] == {"table": "quantity_flow_lineage"}
            # H-22: neither command clears it.
            context.provision()
            context.grant()
            assert _ids(context.check_h()[1]) == {(code, trigger)}
    finally:
        context.execute(f"ALTER TABLE quantity_flow_lineage ENABLE TRIGGER {trigger}")
    context.assert_clean()


def test_check_h_without_the_roles(context: Context) -> None:
    """H-19."""
    fresh = temporary_roles()
    exit_code, check = context.check_h(required=False, roles=fresh)
    assert (exit_code, check["status"]) == (0, "not_applicable")
    assert check["reason"] == database_roles.NOT_APPLICABLE_REASON.format(
        app=fresh.app, maintenance=fresh.maintenance
    )
    assert database_roles.NOT_APPLICABLE_REASON.format(
        app="partflow_app", maintenance="partflow_maintenance"
    ) == (
        "The database roles partflow_app and partflow_maintenance do not exist here (a"
        " development or test database with one owner role). Database-role hardening applies"
        " to the production stack."
    )
    exit_code, check = context.check_h(required=True, roles=fresh)
    assert exit_code == 1
    assert {("ROLE_MISSING", fresh.app), ("ROLE_MISSING", fresh.maintenance)} <= _ids(check)
    missing = [f for f in check["findings"] if f["code"] == "ROLE_MISSING"]
    assert all((f["expected"], f["actual"]) == ("exists", None) for f in missing)


def _catalog_state(context: Context) -> Any:
    with context.engine.connect() as connection:
        return (
            acl_snapshot(connection),
            connection.execute(
                sa.text("SELECT tgname, tgenabled, tgfoid FROM pg_trigger ORDER BY 1")
            ).all(),
            connection.execute(
                sa.text("SELECT rolname, rolsuper, rolcanlogin FROM pg_roles ORDER BY 1")
            ).all(),
            connection.execute(
                sa.text("SELECT setdatabase, setrole, setconfig::text FROM pg_db_role_setting")
            ).all(),
        )


def test_check_h_reads_only(context: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """H-20."""
    held: list[reconciliation.HeldLocks] = []
    original = reconciliation._held_locks

    def recording(session: Any) -> reconciliation.HeldLocks:
        locks = original(session)
        held.append(locks)
        return locks

    monkeypatch.setattr(reconciliation, "_held_locks", recording)
    before = _catalog_state(context)
    context.assert_clean()
    assert _catalog_state(context) == before
    [locks] = held
    assert locks.advisory == 0
    assert locks.tables <= set(GUARD_TABLES) | {"alembic_version"}


def test_check_h_as_the_application_role(
    context: Context, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-21: the production configuration form, connected as the application role."""
    monkeypatch.setattr(database_roles, "APP_ROLE", context.roles.app)
    monkeypatch.setattr(database_roles, "MAINTENANCE_ROLE", context.roles.maintenance)
    monkeypatch.delenv("DATABASE_URL")
    url = context.url
    monkeypatch.setenv("DATABASE_HOST", str(url.host))
    monkeypatch.setenv("DATABASE_PORT", str(url.port or 5432))
    monkeypatch.setenv("DATABASE_NAME", _DATABASE)
    monkeypatch.setenv("DATABASE_USER", context.roles.app)
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", context.files.app)
    monkeypatch.setenv("DATABASE_ROLES_REQUIRED", "true")
    get_settings.cache_clear()
    try:
        code = cli.main(["reconcile", "--check", "h"])
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 0, captured.out
    [check] = [check for check in document["checks"] if check["id"] == "h"]
    assert check["status"] == "pass"
    assert document["database"]["connected_role"] == context.roles.app
    assert document["database"]["connected_role_superuser"] is False


def test_check_h_at_another_revision(context: Context) -> None:
    """H-23."""
    try:
        context.execute(f"UPDATE alembic_version SET version_num = '{_PREVIOUS}'")
        report = reconciliation.run_reconciliation(
            context.engine,
            checks=["h", "j"],
            expected_alembic_revision=_HEAD,
            database_roles=context.roles,
            roles_required=True,
        )
        checks = {check.id: check for check in report.checks}
        assert (checks["h"].status, checks["h"].error_code) == ("error", "schema_mismatch")
        assert checks["j"].status == "pass"
    finally:
        context.execute(f"UPDATE alembic_version SET version_num = '{_HEAD}'")


def test_check_h_under_another_search_path(context: Context) -> None:
    """H-24: (h) pins its own search_path and restores the session's.

    Observed: with a database search_path that hides ``public``, S1's
    reconcile protocol already stops at its unqualified table locks
    (run-level ``schema_mismatch``), so (h) is proven on the catalog
    readers directly in such a session.
    """
    try:
        context.execute("ALTER DATABASE {db} SET search_path = pg_catalog")
        context.engine.dispose()
        with context.engine.begin() as connection:
            assert connection.execute(sa.text("SHOW search_path")).scalar_one() == "pg_catalog"
            integrity = database_roles.guard_integrity(
                connection, roles=context.roles, required=True
            )
            assert integrity.findings == []
            assert integrity.examined["triggers"] == 11
            assert connection.execute(sa.text("SHOW search_path")).scalar_one() == "pg_catalog"
        report = reconciliation.run_reconciliation(
            context.engine,
            checks=["h"],
            expected_alembic_revision=_HEAD,
            database_roles=context.roles,
            roles_required=True,
        )
        assert report.error is not None and report.error.code == "schema_mismatch"
    finally:
        context.execute("ALTER DATABASE {db} RESET search_path")
        context.engine.dispose()
    context.assert_clean()


def test_migrate_hook_is_the_same_rule(context: Context) -> None:
    """The S3 hook delegates with the configured names and setting."""
    with context.engine.begin() as connection:
        outcome = migration.apply_grants(connection)
    assert outcome["status"] == "not_provisioned"
