"""Self-check of the application-role test mode (Phase 16 slice 4: AR-2).

Runs only with ``PARTFLOW_TEST_DATABASE_ROLE=app`` (skipped in the
default owner mode). Every new connection to a ``partflow_test_*``
database runs as the temporary application database role, except
``owner_engine`` connections, connections opened inside Alembic commands
and ``database_owner`` modules (``tests/conftest.py``).
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import Pool

from alembic import command
from app import cli
from app.core.config import get_settings
from app.main import create_app
from tests.conftest import app_mode_roles, owner_engine
from tests.role_harness import cluster_engine

pytestmark = pytest.mark.skipif(
    os.environ.get("PARTFLOW_TEST_DATABASE_ROLE", "owner") != "app",
    reason="needs PARTFLOW_TEST_DATABASE_ROLE=app",
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_HEAD_DATABASE = "partflow_test_app_role_mode"
_PREVIOUS_DATABASE = "partflow_test_app_role_mode_previous"
_EMPTY_DATABASE = "partflow_test_app_role_mode_empty"
_PREVIOUS = "0031_phase14_beyond_demand"
#: The cluster (owner) URL at import, before a case repoints DATABASE_URL.
_CLUSTER_URL = make_url(os.environ["DATABASE_URL"])


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="module")
def databases() -> Iterator[dict[str, URL]]:
    admin = cluster_engine(_CLUSTER_URL)
    names = (_HEAD_DATABASE, _PREVIOUS_DATABASE, _EMPTY_DATABASE)
    with admin.connect() as connection:
        for name in names:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    urls = {name: _CLUSTER_URL.set(database=name) for name in names}
    command.upgrade(_alembic_config(urls[_HEAD_DATABASE]), "head")
    command.upgrade(_alembic_config(urls[_PREVIOUS_DATABASE]), _PREVIOUS)
    try:
        yield urls
    finally:
        with admin.connect() as connection:
            for name in names:
                connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _identity(engine: Engine) -> tuple[str, str, bool]:
    with engine.connect() as connection:
        user, session_user, superuser = connection.execute(
            sa.text(
                "SELECT current_user, session_user,"
                " (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"
            )
        ).one()
    return str(user), str(session_user), bool(superuser)


def _roles() -> tuple[str, str]:
    roles = app_mode_roles()
    assert roles is not None
    return roles.app, str(_CLUSTER_URL.username)


def test_every_application_path_runs_as_the_application_role(
    databases: dict[str, URL], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AR-2: the API, a direct service session, the CLI engine; owner exceptions."""
    app, cluster = _roles()
    url = databases[_HEAD_DATABASE]
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            api_engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
            assert _identity(api_engine) == (app, cluster, False)
        db_engine = create_engine(url)
        try:
            with Session(db_engine) as session:
                assert session.execute(sa.text("SELECT current_user")).scalar_one() == app
        finally:
            db_engine.dispose()
        cli_engine = cli._engine()
        try:
            assert _identity(cli_engine) == (app, cluster, False)
        finally:
            cli_engine.dispose()
    finally:
        get_settings.cache_clear()
    owner = owner_engine(url)
    try:
        assert _identity(owner)[0] == cluster
    finally:
        owner.dispose()

    seen: list[str] = []

    def record(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT current_user")
            seen.append(str(cursor.fetchone()[0]))
        finally:
            cursor.close()
            dbapi_connection.rollback()

    event.listen(Pool, "connect", record)
    try:
        command.upgrade(_alembic_config(url), "head")
    finally:
        event.remove(Pool, "connect", record)
    assert seen and set(seen) == {cluster}


def test_databases_at_another_revision_are_granted_too(databases: dict[str, URL]) -> None:
    """AR-2: a database left at 0031, an empty one, then a table created later."""
    app, _ = _roles()
    previous = create_engine(databases[_PREVIOUS_DATABASE])
    try:
        with previous.connect() as connection:
            assert connection.execute(sa.text("SELECT current_user")).scalar_one() == app
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _PREVIOUS
            )
    finally:
        previous.dispose()

    empty_url = databases[_EMPTY_DATABASE]
    empty = create_engine(empty_url)
    owner = owner_engine(empty_url)
    try:
        with empty.connect() as connection:
            assert connection.execute(sa.text("SELECT current_user")).scalar_one() == app
        with owner.begin() as connection:
            connection.execute(
                sa.text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
            )
            connection.execute(sa.text("INSERT INTO alembic_version VALUES ('x')"))
        empty.dispose()
        with empty.connect() as connection:
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == "x"
            )
        with owner.begin() as connection:
            connection.execute(sa.text("CREATE TABLE s4_unclassified (i int)"))
        empty.dispose()
        with pytest.raises(RuntimeError, match="table_unclassified"), empty.connect():
            pass
    finally:
        with owner.begin() as connection:
            connection.execute(sa.text("DROP TABLE IF EXISTS s4_unclassified"))
        empty.dispose()
        owner.dispose()
