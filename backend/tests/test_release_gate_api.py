"""Tests for the backend write gate (Phase 16 slice 3: G-1 … G-10).

``ReleaseGateMiddleware`` refuses every unsafe request — every route, and
unknown paths — before routing, before CSRF and before any body read:
409 ``release_mismatch`` when enforcement is on and the page's
``X-PartFlow-Release`` differs from ``RELEASE_TAG``; 503 ``not_ready``
when the schema does not match this release or its readiness cannot be
confirmed. A refusal writes nothing: a row count of every table is
compared before and after. Runs against the development database (at
head); a schema mismatch is simulated by replacing the revision read.
"""

import asyncio
import re
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from starlette.routing import BaseRoute
from starlette.types import Message

from app.api.release_gate import ReleaseGate, ReleaseGateMiddleware
from app.application.readiness import ReadinessMonitor
from app.core.config import get_settings
from app.infrastructure import schema_revision
from app.infrastructure.database import DatabaseUnavailableError
from app.infrastructure.models import Base
from app.main import create_app
from tests.auth_harness import CSRF_HEADERS, admin_of, anonymous_client, engine_of

_RELEASE = "v9.9.9-test"
_RELEASE_MISMATCH = {
    "detail": (
        "PartFlow was updated while this page was open, so this request was refused and"
        " nothing was changed by it. Reload the page to continue. If an earlier attempt had"
        " no answer, check whether it was recorded before repeating it."
    ),
    "release_mismatch": True,
}
_NOT_READY = {
    "detail": (
        "PartFlow is not ready for changes: the database does not match this release."
        " Nothing was changed. An administrator must complete or roll back the release."
    ),
    "not_ready": True,
}
_CANNOT_CONFIRM = {
    "detail": (
        "PartFlow cannot confirm that its database is ready for changes. Nothing was"
        " changed. Try again in a moment."
    ),
    "not_ready": True,
}
_UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
_SIGN_IN = {"login_name": "release-gate-nobody", "password": "not-a-password"}


@pytest.fixture
def enforcing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The next ``create_app`` enforces the client release ``v9.9.9-test``."""
    monkeypatch.setenv("ENFORCE_CLIENT_RELEASE", "true")
    monkeypatch.setenv("RELEASE_TAG", _RELEASE)
    get_settings.cache_clear()
    yield
    # Before monkeypatch restores the environment: the next read sees it.
    get_settings.cache_clear()


@pytest.fixture
def mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The database reads as one revision behind this release."""
    monkeypatch.setattr(
        schema_revision, "read_database_revision", lambda engine: "0031_phase14_beyond_demand"
    )


def _walk(routes: list[BaseRoute]) -> Iterator[BaseRoute]:
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        else:
            yield route


def _unsafe_routes(client: TestClient) -> list[tuple[str, str]]:
    """Every unsafe (method, path) of the app, path parameters filled."""
    found: set[tuple[str, str]] = set()
    for route in _walk(cast(Any, client.app).router.routes):
        if isinstance(route, APIRoute):
            for method in (route.methods or set()) & set(_UNSAFE):
                found.add((method, re.sub(r"\{[^}]+\}", "1", route.path)))
    assert len(found) > 50
    return sorted(found)


def _row_counts(engine: Engine) -> dict[str, int]:
    tables = [table.name for table in Base.metadata.sorted_tables] + ["alembic_version"]
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar_one())
            for table in tables
        }


def _assert_refused(response: Any, status: int, body: dict[str, object], context: object) -> None:
    assert response.status_code == status, (context, response.text)
    assert response.json() == body, context
    assert response.headers["cache-control"] == "no-store", context


@pytest.mark.usefixtures("mismatch")
def test_every_unsafe_route_is_refused_on_a_schema_mismatch() -> None:
    """G-1."""
    with TestClient(create_app()) as client:
        admin = admin_of(client)
        before = _row_counts(engine_of(client))
        for method, path in _unsafe_routes(client):
            response = admin.request(method, path, json={})
            _assert_refused(response, 503, _NOT_READY, (method, path))
        assert _row_counts(engine_of(client)) == before
        assert admin.get("/api/session").status_code == 200
        assert client.get("/api/health/live").status_code == 200


@pytest.mark.usefixtures("enforcing")
def test_every_unsafe_route_is_refused_from_another_release() -> None:
    """G-2."""
    with TestClient(create_app()) as client:
        admin = admin_of(client)
        before = _row_counts(engine_of(client))
        for method, path in _unsafe_routes(client):
            for headers in ({}, {"X-PartFlow-Release": "v9.9.8"}):
                response = admin.request(method, path, json={}, headers=headers)
                _assert_refused(response, 409, _RELEASE_MISMATCH, (method, path, headers))
        assert _row_counts(engine_of(client)) == before


@pytest.mark.usefixtures("enforcing")
def test_the_matching_release_passes_the_gate() -> None:
    """G-3, G-4."""
    with TestClient(create_app()) as client:
        anonymous = anonymous_client(client)
        response = anonymous.post(
            "/api/session",
            json=_SIGN_IN,
            headers={**CSRF_HEADERS, "X-PartFlow-Release": _RELEASE},
        )
        assert response.status_code == 401, response.text
        assert response.json()["sign_in_failed"] is True
        # G-4: the release refusal comes before the CSRF refusal.
        response = anonymous.post(
            "/api/session", json=_SIGN_IN, headers={"X-PartFlow-Release": "v9.9.8"}
        )
        _assert_refused(response, 409, _RELEASE_MISMATCH, "no CSRF")


@pytest.mark.usefixtures("enforcing", "mismatch")
def test_the_release_check_comes_before_readiness() -> None:
    """G-5."""
    with TestClient(create_app()) as client:
        response = anonymous_client(client).post(
            "/api/session",
            json=_SIGN_IN,
            headers={**CSRF_HEADERS, "X-PartFlow-Release": "v9.9.8"},
        )
        _assert_refused(response, 409, _RELEASE_MISMATCH, "mismatch")


def test_without_enforcement_no_release_is_checked() -> None:
    """G-6."""
    with TestClient(create_app()) as client:
        anonymous = anonymous_client(client)
        for headers in ({}, {"X-PartFlow-Release": "v9.9.8"}):
            response = anonymous.post(
                "/api/session", json=_SIGN_IN, headers={**CSRF_HEADERS, **headers}
            )
            assert response.status_code == 401, response.text


def test_an_unconfirmed_readiness_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """G-7."""

    def unreachable(engine: Engine) -> str | None:
        raise DatabaseUnavailableError()

    monkeypatch.setattr(schema_revision, "read_database_revision", unreachable)
    with TestClient(create_app()) as client:
        response = anonymous_client(client).post(
            "/api/session", json=_SIGN_IN, headers=CSRF_HEADERS
        )
        _assert_refused(response, 503, _CANNOT_CONFIRM, "unreachable")


@pytest.mark.usefixtures("enforcing", "mismatch")
def test_safe_methods_are_never_gated() -> None:
    """G-8."""
    with TestClient(create_app()) as client:
        admin = admin_of(client)
        for method in ("GET", "HEAD", "OPTIONS"):
            for path in ("/api/session", "/api/departments", "/api/health/live"):
                response = admin.request(method, path, headers={"X-PartFlow-Release": "v9.9.8"})
                assert response.status_code not in (409, 503), (method, path, response.text)
        assert admin.get("/api/session").status_code == 200


def test_the_gate_never_reads_the_body() -> None:
    """G-9: pure ASGI; neither ``receive`` nor the application is called."""

    async def application(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("the request reached the application")

    async def receive() -> Message:
        raise AssertionError("the gate read the request body")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    readiness = ReadinessMonitor(
        cast(Engine, None), expected_revision="x", known_revisions={"x"}, accepted_revision=None
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            release_gate=ReleaseGate(enforced_release=_RELEASE, readiness=readiness)
        )
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/work-orders",
        "headers": [(b"content-type", b"application/json")],
        "app": app,
    }
    asyncio.run(ReleaseGateMiddleware(application)(scope, receive, send))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 409


@pytest.mark.usefixtures("mismatch")
def test_unknown_paths_are_gated_before_routing() -> None:
    """G-10."""
    with TestClient(create_app()) as client:
        response = anonymous_client(client).post("/api/no-such-path", json={})
        _assert_refused(response, 503, _NOT_READY, "unknown path")


def test_a_gate_without_its_lifespan_state_fails_closed() -> None:
    """The middleware without ``app.state.release_gate`` refuses (fail closed)."""
    sent: list[Message] = []

    async def application(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("the request reached the application")

    async def receive() -> Message:
        raise AssertionError("the gate read the request body")

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "DELETE",
        "path": "/api/session",
        "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace()),
    }
    asyncio.run(ReleaseGateMiddleware(application)(scope, receive, send))
    assert sent[0]["status"] == 503
