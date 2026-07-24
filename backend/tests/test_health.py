"""Tests for the operational health endpoint."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api import health as health_api
from app.infrastructure.database import DatabaseUnavailableError
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Context manager runs the lifespan: engine creation and disposal.
    with TestClient(app) as test_client:
        yield test_client


def test_health_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health_api, "ping_database", lambda engine: None)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "partflow-api",
        "database": "connected",
    }


def test_health_database_unavailable_returns_503_without_leaking_internals(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    internal_error = "connection refused at db:5432 (password=secret)"

    def failing_ping(engine: Engine) -> None:
        raise DatabaseUnavailableError() from RuntimeError(internal_error)

    monkeypatch.setattr(health_api, "ping_database", failing_ping)

    response = client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["database"] == "unreachable"
    # The internal exception text must never reach the API response.
    assert internal_error not in response.text
    assert "secret" not in response.text
