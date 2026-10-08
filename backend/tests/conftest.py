"""Shared pytest configuration.

Provides the development-database default for DATABASE_URL so the suite
runs outside Docker (IDE, plain `uv run pytest` on the host) without
extra environment setup. The default matches `.env.example` and the port
published by the Compose db service. Inside Docker Compose and CI the
variable is already set, so the default is ignored.

Phase 16 slice 4 adds the application-role test mode
(``PARTFLOW_TEST_DATABASE_ROLE=app``; the default ``owner`` is the
behaviour before it). In app mode every new connection to a database
whose name starts with ``partflow_test_`` runs ``SET SESSION ROLE`` to a
temporary application database role holding exactly the grants
``apply-grants`` derives, so every API call and every direct
Application-service call runs with production's privileges. Owner
connections are exactly:

1. connections of ``owner_engine(url)`` (``application_name``
   ``partflow-test-owner``) — test setup that needs owner rights;
2. connections opened inside ``alembic.command.upgrade``/``downgrade``/
   ``stamp`` (wrapped here);
3. connections opened by a test of a module marked
   ``pytest.mark.database_owner`` (schema, DDL, trigger and owner-only
   command tests only; never an API or service module).

The temporary roles ``partflow_app_t<hex>``/``partflow_maint_t<hex>`` are
created at session start (the cluster user must be a superuser) and
dropped at session end. The development database is never touched.
"""

import contextlib
import os
import threading
import uuid
from collections.abc import Callable, Generator, Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from psycopg import sql
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool, Pool

# Must run before any test module imports app.core.config.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://partflow_user:partflow_dev_password@localhost:5432/partflow",
)

from alembic import command  # noqa: E402
from app.application import database_roles  # noqa: E402
from app.infrastructure.database_privileges import APP_PRIVILEGES, TABLE_CLASSES  # noqa: E402

TEST_DATABASE_ROLE_ENV = "PARTFLOW_TEST_DATABASE_ROLE"
OWNER_APPLICATION_NAME = "partflow-test-owner"
TEST_DATABASE_PREFIX = "partflow_test_"
_NOT_SUPERUSER = (
    "PARTFLOW_TEST_DATABASE_ROLE=app needs a superuser test database role (the Compose and CI"
    " partflow_user is one)."
)


def owner_engine(url: URL | str, **options: Any) -> Engine:
    """An engine whose connections keep the cluster (owner) user in app mode.

    For test setup that needs owner rights only: trigger disabling,
    ``session_replication_role``, corruption statements, ``setval``, DDL,
    ``CREATE DATABASE`` through a test database. Never for an
    Application-service call under test. ``options`` go to
    ``create_engine`` (for example ``isolation_level``).
    """
    return create_engine(url, connect_args={"application_name": OWNER_APPLICATION_NAME}, **options)


@contextlib.contextmanager
def owner_connection(url: URL | str) -> Iterator[sa.Connection]:
    """One owner connection (see ``owner_engine``), disposed afterwards.

    Also for observing other sessions in ``pg_stat_activity``: an
    application-role session sees no wait event of another session user.
    """
    engine = owner_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def app_role_mode() -> bool:
    return os.environ.get(TEST_DATABASE_ROLE_ENV, "owner") == "app"


class _AppRoleMode:
    """The session state of app mode (one instance while it is active)."""

    def __init__(self, cluster_url: URL, roles: database_roles.DatabaseRoles) -> None:
        self.cluster_url = cluster_url
        self.roles = roles
        self.owner_scope = 0
        self.scope_lock = threading.Lock()
        self.grant_lock = threading.RLock()
        self.granted: dict[str, tuple[int, int, int]] = {}
        self.wrapped: dict[str, Callable[..., Any]] = {}

    # -- owner scope -------------------------------------------------------

    def enter_owner_scope(self) -> None:
        with self.scope_lock:
            self.owner_scope += 1

    def exit_owner_scope(self) -> None:
        with self.scope_lock:
            self.owner_scope -= 1

    def wrap_alembic(self) -> None:
        for name in ("upgrade", "downgrade", "stamp"):
            original = getattr(command, name)
            self.wrapped[name] = original

            def wrapper(*args: Any, _original: Callable[..., Any] = original, **kwargs: Any) -> Any:
                self.enter_owner_scope()
                try:
                    return _original(*args, **kwargs)
                finally:
                    self.exit_owner_scope()

            setattr(command, name, wrapper)

    def unwrap_alembic(self) -> None:
        for name, original in self.wrapped.items():
            setattr(command, name, original)
        self.wrapped.clear()

    # -- the connect listener ----------------------------------------------

    def on_connect(self, dbapi_connection: Any, connection_record: Any) -> None:
        info = dbapi_connection.info
        database = str(info.dbname)
        if not database.startswith(TEST_DATABASE_PREFIX):
            return
        if info.parameter_status("application_name") == OWNER_APPLICATION_NAME:
            return
        if self.owner_scope > 0:
            return
        with self.grant_lock:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(
                    "SELECT (SELECT oid FROM pg_database WHERE datname = current_database()),"
                    " count(*), coalesce(max(c.oid::bigint), 0) FROM pg_class c"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                    " AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')"
                )
                row = cursor.fetchone()
                fingerprint = (int(row[0]), int(row[1]), int(row[2]))
                dbapi_connection.commit()
                if self.granted.get(database) != fingerprint:
                    self._grant(database)
                    self.granted[database] = fingerprint
                cursor.execute(_set_role_statement(self.roles.app))
                dbapi_connection.commit()
            finally:
                cursor.close()

    def _grant(self, database: str) -> None:
        engine = owner_engine(self.cluster_url.set(database=database), poolclass=NullPool)
        try:
            with engine.begin() as connection:
                relations = {
                    str(name): str(kind)
                    for name, kind in connection.execute(
                        sa.text(
                            "SELECT c.relname, c.relkind FROM pg_class c"
                            " JOIN pg_namespace n ON n.oid = c.relnamespace"
                            " WHERE n.nspname = 'public'"
                            " AND c.relkind IN ('r', 'p', 'v', 'm', 'f')"
                        )
                    )
                }
                if set(relations) == set(TABLE_CLASSES):
                    try:
                        database_roles.apply_grants(connection, roles=self.roles, required=True)
                    except database_roles.DatabaseRolesRefusal as refusal:
                        raise RuntimeError(
                            f"App-role test mode could not grant {database}: {refusal.code}"
                            f" ({refusal.message})"
                        ) from refusal
                    return
                _grant_existing(connection, database, relations, self.roles)
        finally:
            engine.dispose()


def _set_role_statement(role: str) -> sql.Composed:
    return sql.SQL("SET SESSION ROLE {}").format(sql.Identifier(role))


def _grant_existing(
    connection: sa.Connection,
    database: str,
    relations: dict[str, str],
    roles: database_roles.DatabaseRoles,
) -> None:
    """A database kept at another revision: grant every existing classified relation."""
    unclassified = sorted(set(relations) - set(TABLE_CLASSES))
    if unclassified:
        raise RuntimeError(
            f"App-role test mode found unclassified relations in {database}:"
            f" {', '.join(unclassified)} (table_unclassified)"
        )
    raw: Any = connection.connection.driver_connection
    statements = [sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(roles.app))]
    for name in sorted(relations):
        privileges = sql.SQL(", ").join(
            sql.SQL(privilege) for privilege in sorted(APP_PRIVILEGES[TABLE_CLASSES[name]])
        )
        statements.append(
            sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                privileges, sql.Identifier("public", name), sql.Identifier(roles.app)
            )
        )
    for statement in statements:
        connection.exec_driver_sql(statement.as_string(raw))


_MODE: _AppRoleMode | None = None


def drop_temporary_roles(cluster_url: URL, names: tuple[str, ...]) -> None:
    """``DROP OWNED BY`` in every database the roles have entries in, then ``DROP ROLE``."""
    engine = create_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            databases = list(
                connection.execute(
                    sa.text(
                        "SELECT DISTINCT d.datname FROM pg_shdepend s"
                        " JOIN pg_database d ON d.oid = s.dbid JOIN pg_roles r"
                        " ON r.oid = s.refobjid WHERE r.rolname = ANY(:names)"
                    ),
                    {"names": list(names)},
                ).scalars()
            )
        existing = _existing_roles(engine, names)
        for database in databases:
            if existing:
                drop_owned(cluster_url.set(database=str(database)), existing)
        with engine.connect() as connection:
            for name in existing:
                connection.execute(sa.text(f'DROP ROLE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


def _existing_roles(engine: Engine, names: tuple[str, ...]) -> tuple[str, ...]:
    with engine.connect() as connection:
        found = set(
            connection.execute(
                sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
                {"names": list(names)},
            ).scalars()
        )
    return tuple(name for name in names if name in found)


def drop_owned(url: URL, names: tuple[str, ...]) -> None:
    engine = owner_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            quoted = ", ".join(f'"{name}"' for name in names)
            connection.execute(sa.text(f"DROP OWNED BY {quoted}"))
    finally:
        engine.dispose()


def pytest_sessionstart(session: pytest.Session) -> None:
    global _MODE
    if not app_role_mode():
        return
    cluster_url = make_url(os.environ["DATABASE_URL"])
    suffix = uuid.uuid4().hex[:8]
    roles = database_roles.DatabaseRoles(f"partflow_app_t{suffix}", f"partflow_maint_t{suffix}")
    engine = create_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        with engine.connect() as connection:
            superuser = connection.execute(
                sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            if not superuser:
                pytest.exit(_NOT_SUPERUSER, returncode=pytest.ExitCode.USAGE_ERROR)
            for name in roles:
                connection.execute(sa.text(f'CREATE ROLE "{name}" NOLOGIN'))
    finally:
        engine.dispose()
    mode = _AppRoleMode(cluster_url, roles)
    mode.wrap_alembic()
    event.listen(Pool, "connect", mode.on_connect)
    _MODE = mode


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _MODE
    mode = _MODE
    if mode is None:
        return
    _MODE = None
    event.remove(Pool, "connect", mode.on_connect)
    mode.unwrap_alembic()
    # A failure here fails the session loudly.
    drop_temporary_roles(mode.cluster_url, tuple(mode.roles))


def app_mode_roles() -> database_roles.DatabaseRoles | None:
    """The temporary roles of app mode (None in owner mode)."""
    return None if _MODE is None else _MODE.roles


_OWNER_SCOPE = pytest.StashKey[bool]()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    if _MODE is not None and item.get_closest_marker("database_owner") is not None:
        _MODE.enter_owner_scope()
        item.stash[_OWNER_SCOPE] = True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, None, None]:
    yield
    if _MODE is not None and item.stash.get(_OWNER_SCOPE, False):
        _MODE.exit_owner_scope()
