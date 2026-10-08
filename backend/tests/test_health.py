"""Tests for the operational health endpoints (Phase 16 slice 3: H-1 … H-11).

``GET /api/health`` is readiness: it reads the database revision on every
call and compares it with this image's own Alembic head (OD-16-06).
``GET /api/health/live`` is liveness and never touches the database. The
revision read (``schema_revision.read_database_revision``) is replaced
where a case needs another database state; H-1, H-7 and H-10 use real
databases (H-7 an isolated temporary one, dropped afterwards).
"""

import logging
import os
from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.util.exc import CommandError
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from app import cli
from app.application.readiness import CACHE_SECONDS, ReadinessMonitor
from app.core.config import get_settings
from app.infrastructure import schema_revision
from app.infrastructure.database import DatabaseUnavailableError
from app.main import create_app
from tests.conftest import owner_engine

_HEAD = schema_revision.code_head()
_PREVIOUS = "0031_phase14_beyond_demand"
_UNKNOWN = "9999_unknown_to_this_image"
_OTHER_UNKNOWN = "9998_unknown_to_this_image"
_NOT_READY_DETAIL = (
    "The database schema does not match this release. Changes are refused until an"
    " administrator completes or rolls back the release."
)
_UNAVAILABLE_DETAIL = "Database is unreachable. Verify that PostgreSQL is running and retry."
_TEST_DATABASE = "partflow_test_health_revision"


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Set backend environment values for the next ``create_app``; settings re-read."""

    def apply(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield apply
    # Before monkeypatch restores the environment: the next read sees it.
    get_settings.cache_clear()


@pytest.fixture
def database_revision(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], list[int]]:
    """Replace the revision read: a revision string, or an exception to raise."""

    def replace(answer: object) -> list[int]:
        calls = [0]

        def read(engine: Engine) -> str | None:
            calls[0] += 1
            if isinstance(answer, BaseException):
                raise answer
            return cast(str | None, answer)

        monkeypatch.setattr(schema_revision, "read_database_revision", read)
        return calls

    return replace


def _client() -> TestClient:
    # The context manager runs the lifespan: engine creation and disposal.
    return TestClient(create_app())


def _identity() -> dict[str, Any]:
    return {"service": "partflow-api", "release": "development", "commit": None}


def test_health_ready_against_the_real_database() -> None:
    """H-1: the development database is at head."""
    with _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "ok",
        "service": "partflow-api",
        "database": "connected",
        "release": "development",
        "commit": None,
        "schema": "current",
        "expected_revision": _HEAD,
        "database_revision": _HEAD,
        "accepted_revision": None,
    }
    assert list(response.json()) == [
        "status",
        "service",
        "database",
        "release",
        "commit",
        "schema",
        "expected_revision",
        "database_revision",
        "accepted_revision",
    ]


def test_health_schema_mismatch(database_revision: Callable[[object], list[int]]) -> None:
    """H-2."""
    database_revision(_PREVIOUS)
    with _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        **_identity(),
        "database": "connected",
        "schema": "mismatch",
        "expected_revision": _HEAD,
        "database_revision": _PREVIOUS,
        "accepted_revision": None,
        "detail": _NOT_READY_DETAIL,
        "not_ready": True,
    }


def test_health_accepted_override(
    configure: Callable[..., None], database_revision: Callable[[object], list[int]]
) -> None:
    """H-3: rollback path 2 — the database is newer than the code, the revision accepted."""
    configure(ACCEPT_SCHEMA_REVISION=_UNKNOWN)
    database_revision(_UNKNOWN)
    with _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "accepted"
    assert body["status"] == "ok"
    assert body["accepted_revision"] == _UNKNOWN
    assert body["database_revision"] == _UNKNOWN


def test_health_override_never_matches_another_revision(
    configure: Callable[..., None], database_revision: Callable[[object], list[int]]
) -> None:
    """H-4."""
    configure(ACCEPT_SCHEMA_REVISION=_OTHER_UNKNOWN)
    database_revision(_UNKNOWN)
    with _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["schema"] == "mismatch"
    assert response.json()["accepted_revision"] == _OTHER_UNKNOWN


def test_health_database_unavailable_returns_503_without_leaking_internals(
    database_revision: Callable[[object], list[int]],
) -> None:
    """H-5."""
    internal_error = "connection refused at db:5432 (password=secret)"
    failure = DatabaseUnavailableError()
    failure.__cause__ = RuntimeError(internal_error)
    database_revision(failure)
    with _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        **_identity(),
        "database": "unreachable",
        "schema": "unknown",
        "expected_revision": _HEAD,
        "database_revision": None,
        "accepted_revision": None,
        "detail": _UNAVAILABLE_DETAIL,
    }
    # The internal exception text must never reach the API response.
    assert "secret" not in response.text
    assert "connection refused" not in response.text


@pytest.mark.parametrize(
    "answer", [_HEAD, _PREVIOUS, _UNKNOWN, DatabaseUnavailableError()], ids=str
)
def test_liveness_never_reads_the_database(
    database_revision: Callable[[object], list[int]], answer: object
) -> None:
    """H-6."""
    calls = database_revision(answer)
    with _client() as client:
        response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live", **_identity()}
    assert calls == [0]


def test_read_database_revision_on_a_temporary_database() -> None:
    """H-7: no table → None; one row → it; two rows → joined in ascending order."""
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    owner: Engine | None = None
    try:
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
        url = make_url(os.environ["DATABASE_URL"]).set(database=_TEST_DATABASE)
        engine = create_engine(url)
        # Setup DDL and writes as the owner (application-role test mode);
        # a new pooled connection then sees the table's grant.
        owner = owner_engine(url)
        assert schema_revision.read_database_revision(engine) is None
        with owner.begin() as connection:
            connection.execute(sa.text("CREATE TABLE alembic_version (version_num varchar(32))"))
        engine.dispose()
        assert schema_revision.read_database_revision(engine) is None
        with owner.begin() as connection:
            connection.execute(sa.text("INSERT INTO alembic_version VALUES ('b_second')"))
        assert schema_revision.read_database_revision(engine) == "b_second"
        with owner.begin() as connection:
            connection.execute(sa.text("INSERT INTO alembic_version VALUES ('a_first')"))
        assert schema_revision.read_database_revision(engine) == "a_first,b_second"
    finally:
        if engine is not None:
            engine.dispose()
        if owner is not None:
            owner.dispose()
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        admin_engine.dispose()


def test_readiness_monitor_cache(database_revision: Callable[[object], list[int]]) -> None:
    """H-8: current() reuses an observation at most 5 s old; observe() always reads."""
    calls = database_revision(_HEAD)
    now = [100.0]
    monitor = ReadinessMonitor(
        cast(Engine, None),
        expected_revision=_HEAD,
        known_revisions={_HEAD},
        accepted_revision=None,
        clock=lambda: now[0],
    )
    assert monitor.fresh() is None
    assert monitor.current().state == "current"
    assert calls == [1]
    now[0] += CACHE_SECONDS
    assert monitor.current().state == "current"
    assert calls == [1]
    now[0] += 0.001
    assert monitor.current().state == "current"
    assert calls == [2]
    monitor.observe()
    monitor.observe()
    assert calls == [4]
    # A failed read is never cached.
    database_revision(DatabaseUnavailableError())
    now[0] += CACHE_SECONDS + 1
    assert monitor.current().state == "unknown"
    assert monitor.fresh() is None


def test_unreadable_migration_scripts_fail_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-9: no path beyond alembic/ in the message."""

    def unreadable() -> object:
        raise CommandError(f"Path doesn't exist: {schema_revision.ALEMBIC_DIR}")

    monkeypatch.setattr(schema_revision, "script_directory", unreadable)
    with pytest.raises(RuntimeError) as raised, _client():
        pass
    assert str(raised.value) == (
        "PartFlow cannot read its migration scripts (alembic/): CommandError: Path doesn't"
        " exist: alembic. The backend image is incomplete."
    )


def _application_name(engine: Engine) -> str:
    with engine.connect() as connection:
        pid = connection.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
        # Read from another session: the row of this connection's backend.
        observer = create_engine(make_url(os.environ["DATABASE_URL"]))
        try:
            with observer.connect() as other:
                name = other.execute(
                    sa.text(
                        "SELECT application_name FROM pg_stat_activity"
                        " WHERE pid = :pid AND datname = current_database()"
                    ),
                    {"pid": pid},
                ).scalar_one()
        finally:
            observer.dispose()
    return str(name)


def test_sessions_carry_their_application_name() -> None:
    """H-10."""
    with _client() as client:
        assert client.get("/api/health").status_code == 200
        engine = cast(Engine, cast(Any, client.app).state.engine)
        assert _application_name(engine) == "partflow-api"
    engine = cli._engine()
    try:
        assert _application_name(engine) == "partflow-cli"
    finally:
        engine.dispose()


def test_override_of_a_known_revision_is_ignored(
    configure: Callable[..., None],
    database_revision: Callable[[object], list[int]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H-11: an older schema the code already knows is never accepted."""
    configure(ACCEPT_SCHEMA_REVISION=_PREVIOUS)
    database_revision(_PREVIOUS)
    with caplog.at_level(logging.WARNING, logger="app.main"), _client() as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["schema"] == "mismatch"
    assert response.json()["accepted_revision"] == _PREVIOUS
    assert (
        f"ACCEPT_SCHEMA_REVISION names {_PREVIOUS}, a revision this release already knows;"
        " the override is ignored."
    ) in caplog.messages
