"""Real PostgreSQL integration test.

Exercises GET /api/health through the full application wiring -- lifespan
engine creation from the configured DATABASE_URL, route dispatch, and a
real SELECT 1 -- without mocking ping_database. It requires the
PostgreSQL service from Docker Compose or CI to be reachable, performs no
writes, and touches no domain data.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_connected_against_real_database() -> None:
    # The context manager runs the lifespan, which builds the real engine
    # from DATABASE_URL; nothing on the connectivity path is mocked.
    with TestClient(app) as client:
        response = client.get("/api/health")

    # The 503 body is the safe generic message, so echoing it on failure
    # leaks no connection details.
    assert response.status_code == 200, (
        f"Health endpoint reported an unhealthy database (HTTP {response.status_code}: "
        f"{response.text}). Is PostgreSQL running and DATABASE_URL correct?"
    )
    assert response.json() == {
        "status": "ok",
        "service": "partflow-api",
        "database": "connected",
    }
