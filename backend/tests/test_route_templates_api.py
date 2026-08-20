"""Integration tests for the Phase 4 read-only RouteTemplate listing.

`GET /api/route-templates` exists only so the release flow (GUI_DESIGN
§11.4) can offer an existing **active** RouteTemplate for a PLANNED
release. Covered:

- active templates are listed with their steps in sequence order;
- archived templates never appear;
- the surface is read-only — no create/update/archive route exists
  (Planned Routes management is a later phase).
"""

import datetime
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_route_templates_api"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
    """Temporary database migrated to head for the API under test."""
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    url = make_url(os.environ["DATABASE_URL"]).set(database=_TEST_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    yield url
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    """Application client wired to the temporary database."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    """Direct database access for seeding (no management API exists)."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _create_area(client: TestClient) -> dict[str, Any]:
    department = client.post("/api/departments", json={"name": _unique("DEPT")})
    assert department.status_code == 201, department.text
    payload = {"department_id": department.json()["id"], "name": _unique("AREA")}
    response = client.post("/api/areas", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_operation(client: TestClient, area_id: int) -> dict[str, Any]:
    response = client.post("/api/operations", json={"area_id": area_id, "code": _unique("OP")})
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_route_template(
    engine: Engine,
    name: str,
    steps: list[dict[str, Any]],
    *,
    archived: bool = False,
    description: str | None = None,
) -> int:
    """Seed a RouteTemplate directly (no management API exists yet)."""
    with Session(engine) as session:
        template = models.RouteTemplate(
            name=name,
            description=description,
            archived_at=datetime.datetime.now(datetime.UTC) if archived else None,
        )
        session.add(template)
        session.flush()
        for index, step in enumerate(steps):
            session.add(
                models.RouteStep(
                    route_template_id=template.id,
                    # Non-contiguous on purpose: the listing must return
                    # sequence values verbatim, ordered by sequence.
                    sequence=(index + 1) * 10,
                    area_id=step["area_id"],
                    operation_id=step.get("operation_id"),
                    instructions=step.get("instructions"),
                )
            )
        session.commit()
        return int(template.id)


def test_active_templates_list_with_ordered_steps(client: TestClient, db_engine: Engine) -> None:
    area_a = _create_area(client)
    area_b = _create_area(client)
    operation = _create_operation(client, area_a["id"])
    name = _unique("ROUTE")
    template_id = _create_route_template(
        db_engine,
        name,
        [
            {"area_id": area_a["id"], "operation_id": operation["id"], "instructions": "Start"},
            {"area_id": area_b["id"]},
        ],
        description="Two-step route",
    )

    response = client.get("/api/route-templates")
    assert response.status_code == 200, response.text
    listed = {entry["id"]: entry for entry in response.json()}
    assert template_id in listed
    entry = listed[template_id]
    assert entry["name"] == name
    assert entry["description"] == "Two-step route"
    assert [step["sequence"] for step in entry["steps"]] == [10, 20]
    assert entry["steps"][0]["area_id"] == area_a["id"]
    assert entry["steps"][0]["operation_id"] == operation["id"]
    assert entry["steps"][0]["instructions"] == "Start"
    assert entry["steps"][1]["area_id"] == area_b["id"]
    assert entry["steps"][1]["operation_id"] is None


def test_archived_templates_never_appear(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    archived_id = _create_route_template(
        db_engine, _unique("ROUTE"), [{"area_id": area["id"]}], archived=True
    )

    response = client.get("/api/route-templates")
    assert response.status_code == 200, response.text
    assert archived_id not in {entry["id"] for entry in response.json()}


def test_the_surface_is_read_only(client: TestClient) -> None:
    # No management surface exists: templates cannot be created,
    # changed, or archived through the API (Planned Routes is a later
    # phase). FastAPI answers 405 for the defined path with an
    # unsupported method.
    assert client.post("/api/route-templates", json={"name": "X"}).status_code == 405
    assert client.patch("/api/route-templates/1", json={}).status_code in (404, 405)
    assert client.delete("/api/route-templates/1").status_code in (404, 405)
