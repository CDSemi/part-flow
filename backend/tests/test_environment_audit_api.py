"""Integration tests for the Phase 13 configuration audit of the environment writes.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Every effective write of
Departments, Areas, Operations, Scan Stations and the Machine Asset Tag
format appends exactly one ``audit_events`` row in its own transaction
(PROJECT_PROFILE §28 "administrative configuration changes"):

- exact ``CREATED``/``UPDATED`` rows: entity, ``entity_id``, explicit
  before/after snapshots, ``actor_reference`` and ``metadata`` NULL;
- one row per effective request (multi-field PATCH included); no row
  for a no-op, a refusal or a lost race, at flush or at COMMIT;
- lock-first: an edit waits for a concurrent writer and audits its
  committed predecessor, in the lock mode its own UPDATE takes (it never
  waits on FOR KEY SHARE, except a requested Area deactivation and an
  UPDATE of a unique-key column), Asset Tag format edits included;
- atomicity on all ten write paths, the audit chain property, and
  isolation from production commands and from Machine creation's
  ``next_sequence``;
- the slice 2b lost races: a Department rename race and a concurrent
  first Asset Tag format save are 409s with nothing written, never 500s;
- a static guard that every environment setter keeps its audit call.

``tests/test_environment_api.py`` stays unchanged: that it passes is the
proof that the HTTP behavior is identical. The module database never
holds the Asset Tag singleton; the tests that need it get a fresh
database each from ``unconfigured_client``. The development database in
DATABASE_URL is only used as the admin connection for CREATE/DROP
DATABASE.
"""

import ast
import datetime
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_environment_audit_api"
_ENVIRONMENT_SERVICE = _BACKEND_DIR / "app" / "application" / "environment.py"
_ENVIRONMENT_ENTITIES = ("Department", "Area", "Operation", "ScanStation", "MachineAssetTagConfig")
_ASSET_TAG_PATH = "/api/barcode-configuration/machine-asset-tag-format"
# Public environment setters that deliberately write no audit row, each
# with its reason.
_NON_AUDITED_SETTERS: dict[str, str] = {
    "update_scan_station_theme_preference": (
        "The station's Dark/Light display preference is not configuration (PLAN CD2, OD-13)."
    ),
}


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
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


def _start_client(database_url: URL) -> Iterator[TestClient]:
    """Application client on ``database_url`` through the real startup
    path (DATABASE_URL pointed at it, settings re-read)."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    yield from _start_client(api_database_url)


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    """Direct database access for assertions and concurrent holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


class _Deployment(NamedTuple):
    client: TestClient
    engine: Engine


@pytest.fixture
def unconfigured_client(api_database_url: URL) -> Iterator[_Deployment]:
    """A fresh database without the Asset Tag singleton, with its own app.

    The only place the singleton is written, so no test depends on the
    order in which another test configured it.
    """
    name = f"partflow_test_env_audit_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(api_database_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = api_database_url.set(database=name)
    engine = create_engine(url)
    try:
        command.upgrade(_alembic_config(url), "head")
        for test_client in _start_client(url):
            yield _Deployment(test_client, engine)
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _created(response: Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_department(client: TestClient, **overrides: Any) -> dict[str, Any]:
    return _created(
        admin_of(client).post("/api/departments", json={"name": _unique("DEPT"), **overrides})
    )


def _create_area(
    client: TestClient, department_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if department_id is None:
        department_id = int(_create_department(client)["id"])
    payload = {"department_id": department_id, "name": _unique("AREA"), **overrides}
    return _created(admin_of(client).post("/api/areas", json=payload))


def _create_operation(
    client: TestClient, area_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if area_id is None:
        area_id = int(_create_area(client)["id"])
    payload = {"area_id": area_id, "code": _unique("OP"), **overrides}
    return _created(admin_of(client).post("/api/operations", json=payload))


def _create_scan_station(
    client: TestClient, area_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if area_id is None:
        area_id = int(_create_area(client)["id"])
    payload = {"station_id": _unique("ST"), "area_id": area_id, **overrides}
    return _created(admin_of(client).post("/api/scan-stations", json=payload))


def _audit_rows(engine: Engine, entity_type: str, entity_id: object) -> list[sa.Row[Any]]:
    """One entity's audit history in write order."""
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AuditEvent.__table__)
                .where(
                    models.AuditEvent.entity_type == entity_type,
                    models.AuditEvent.entity_id == str(entity_id),
                )
                .order_by(models.AuditEvent.id)
            )
        )


def _audit_count(engine: Engine, entity_types: tuple[str, ...] | None = None) -> int:
    query = sa.select(sa.func.count()).select_from(models.AuditEvent.__table__)
    if entity_types is not None:
        query = query.where(models.AuditEvent.entity_type.in_(entity_types))
    with engine.connect() as connection:
        return int(connection.execute(query).scalar_one())


def _table(model: type[models.Base]) -> sa.Table:
    return cast(sa.Table, model.__table__)


def _stored(engine: Engine, model: type[models.Base], key: object) -> sa.Row[Any]:
    """The whole stored row."""
    table = _table(model)
    primary_key = next(iter(table.primary_key.columns))
    with engine.connect() as connection:
        return connection.execute(sa.select(table).where(primary_key == key)).one()


def _row_count(engine: Engine, model: type[models.Base]) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(sa.select(sa.func.count()).select_from(_table(model))).scalar_one()
        )


def _assert_chain(rows: list[sa.Row[Any]]) -> None:
    """Each row's before_data is its predecessor's after_data on every
    key present in both snapshots."""
    assert [row.id for row in rows] == sorted(row.id for row in rows)
    for previous, current in zip(rows, rows[1:], strict=False):
        shared = previous.after_data.keys() & current.before_data.keys()
        assert shared
        assert {key: current.before_data[key] for key in shared} == {
            key: previous.after_data[key] for key in shared
        }


def _start(request: Callable[[], Response]) -> tuple[threading.Thread, list[Response]]:
    results: list[Response] = []
    thread = threading.Thread(target=lambda: results.append(request()))
    thread.start()
    return thread, results


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread, results: list[Response]) -> Response:
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def test_department_create_is_audited(client: TestClient, db_engine: Engine) -> None:
    name = _unique("DEPT")
    department = _create_department(client, name=f"  {name}  ")

    rows = _audit_rows(db_engine, "Department", department["id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "CREATED"
    assert row.entity_id == str(department["id"])
    assert row.before_data is None
    assert row.after_data == {
        "name": name,
        "is_active": True,
        "board_seconds_per_row": 3,
        "board_min_page_seconds": 6,
    }
    assert row.actor_reference is None
    assert row._mapping["metadata"] is None


def test_department_edits_are_audited_as_a_chain(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    path = f"/api/departments/{department['id']}"
    new_name = _unique("DEPT")
    assert admin_of(client).patch(path, json={"name": new_name}).status_code == 200
    assert admin_of(client).patch(path, json={"is_active": False}).status_code == 200
    assert admin_of(client).patch(path, json={"is_active": True}).status_code == 200

    rows = _audit_rows(db_engine, "Department", department["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED", "UPDATED", "UPDATED"]
    settings = {"board_seconds_per_row": 3, "board_min_page_seconds": 6}
    old = {"name": department["name"], "is_active": True, **settings}
    renamed = {"name": new_name, "is_active": True, **settings}
    inactive = {"name": new_name, "is_active": False, **settings}
    assert (rows[1].before_data, rows[1].after_data) == (old, renamed)
    assert (rows[2].before_data, rows[2].after_data) == (renamed, inactive)
    assert (rows[3].before_data, rows[3].after_data) == (inactive, renamed)
    _assert_chain(rows)


def test_department_no_op_patch_audits_nothing(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    stored = _stored(db_engine, models.Department, department["id"])
    path = f"/api/departments/{department['id']}"
    for body in ({"name": f"  {department['name']}  "}, {"is_active": True}, {}):
        response = admin_of(client).patch(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json() == department
    assert len(_audit_rows(db_engine, "Department", department["id"])) == 1
    assert _stored(db_engine, models.Department, department["id"]) == stored


def test_department_refusals_audit_nothing(client: TestClient, db_engine: Engine) -> None:
    other = _create_department(client)
    department = _create_department(client)
    _create_area(client, department_id=int(department["id"]))
    path = f"/api/departments/{department['id']}"
    stored = _stored(db_engine, models.Department, department["id"])
    count = _audit_count(db_engine)

    refusals: list[tuple[Callable[[], Response], int]] = [
        (lambda: admin_of(client).patch(path, json={"name": other["name"]}), 409),
        (lambda: admin_of(client).patch(path, json={"name": "   "}), 422),
        (lambda: admin_of(client).patch(path, json={"name": None}), 422),
        (lambda: admin_of(client).patch("/api/departments/999999", json={"is_active": False}), 404),
        (lambda: admin_of(client).patch(path, json={"barcode_value": "PF:AREA:1"}), 422),
        (lambda: admin_of(client).patch(path, json={"is_active": False}), 409),
        (lambda: admin_of(client).post("/api/departments", json={"name": other["name"]}), 409),
    ]
    for attempt, status in refusals:
        assert attempt().status_code == status

    assert _audit_count(db_engine) == count
    assert _stored(db_engine, models.Department, department["id"]) == stored


def test_department_create_race_lost_at_flush_audits_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    name = _unique("DEPT")
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        holder.execute(sa.text("INSERT INTO departments (name) VALUES (:name)"), {"name": name})
        thread, results = _start(
            lambda: admin_of(client).post("/api/departments", json={"name": name})
        )
        # The INSERT waits on the holder's uncommitted duplicate.
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409
    assert response.json()["detail"] == "A Department with this name already exists."
    assert _audit_count(db_engine) == count
    with db_engine.connect() as connection:
        same_name = connection.execute(
            sa.select(sa.func.count())
            .select_from(_table(models.Department))
            .where(models.Department.name == name)
        ).scalar_one()
    assert same_name == 1


@pytest.mark.parametrize("holder_outcome", ["commit", "rollback"])
def test_department_rename_race_maps_to_conflict_not_autoflush_500(
    client: TestClient, db_engine: Engine, holder_outcome: str
) -> None:
    """S2-F4: every read runs before the first assignment, so the rename
    UPDATE is emitted inside commit() — never autoflushed by the
    active-Area query — and a uq_departments_name race lost there is the
    duplicate-name 409, with nothing written."""
    department = _create_department(client)
    stored = _stored(db_engine, models.Department, department["id"])
    new_name = _unique("DEPT")
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        holder.execute(sa.text("INSERT INTO departments (name) VALUES (:name)"), {"name": new_name})
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/departments/{department['id']}",
                json={"name": new_name, "is_active": False},
            )
        )
        # Waits on the holder's uncommitted duplicate.
        _assert_blocked(thread)
        if holder_outcome == "commit":
            holder.commit()
        else:
            holder.rollback()
    response = _finish(thread, results)

    if holder_outcome == "commit":
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "A Department with this name already exists."
        assert _stored(db_engine, models.Department, department["id"]) == stored
        assert _audit_count(db_engine) == count
    else:
        assert response.status_code == 200, response.text
        rows = _audit_rows(db_engine, "Department", department["id"])
        assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
        settings = {"board_seconds_per_row": 3, "board_min_page_seconds": 6}
        assert rows[-1].before_data == {
            "name": department["name"],
            "is_active": True,
            **settings,
        }
        assert rows[-1].after_data == {"name": new_name, "is_active": False, **settings}
        assert _audit_count(db_engine) == count + 1


# ---------------------------------------------------------------------------
# Operations: the PLAN S2 risk on the update path
# ---------------------------------------------------------------------------


def test_operation_code_rename_race_lost_at_commit_leaves_no_orphan_audit_row(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    operation = _create_operation(client, area_id=int(area["id"]))
    code = _unique("OP")
    stored = _stored(db_engine, models.Operation, operation["id"])
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("INSERT INTO operations (area_id, code) VALUES (:area_id, :code)"),
            {"area_id": area["id"], "code": code},
        )
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/operations/{operation['id']}", json={"code": code}
            )
        )
        # Waits at COMMIT, its UPDATED row staged, on the unique index.
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409
    assert response.json()["detail"] == "The Area already has an Operation with this code."
    assert _stored(db_engine, models.Operation, operation["id"]) == stored
    assert _audit_count(db_engine) == count


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def test_area_create_is_audited_with_the_derived_barcode(
    client: TestClient, db_engine: Engine
) -> None:
    department = _create_department(client)
    name = _unique("AREA")
    area = _create_area(
        client,
        department_id=int(department["id"]),
        name=name,
        description="  Lathe row  ",
        color="",
        icon_url=None,
    )

    rows = _audit_rows(db_engine, "Area", area["id"])
    assert len(rows) == 1
    assert rows[0].event_type == "CREATED"
    assert rows[0].before_data is None
    assert rows[0].after_data == {
        "department_id": department["id"],
        "name": name,
        "barcode_value": f"PF:AREA:{area['id']}",
        "description": "Lathe row",
        "color": None,
        "icon_url": None,
        "is_terminal": False,
        "is_active": True,
        "worker_identification_mode": "DISABLED",
        "fixed_worker_id": None,
        "worker_session_timeout_minutes": None,
    }
    assert rows[0].actor_reference is None
    assert rows[0]._mapping["metadata"] is None


def test_area_display_edit_and_terminal_toggle_are_audited(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client, description="old")
    path = f"/api/areas/{area['id']}"
    assert (
        admin_of(client).patch(path, json={"description": "new", "color": "var(--a2)"}).status_code
        == 200
    )
    assert admin_of(client).patch(path, json={"is_terminal": True}).status_code == 200

    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED", "UPDATED"]
    assert (rows[1].before_data["description"], rows[1].after_data["description"]) == (
        "old",
        "new",
    )
    assert (rows[1].before_data["color"], rows[1].after_data["color"]) == (None, "var(--a2)")
    assert (rows[2].before_data["is_terminal"], rows[2].after_data["is_terminal"]) == (
        False,
        True,
    )
    for row in rows[1:]:
        assert row.before_data["barcode_value"] == row.after_data["barcode_value"]
        assert row.after_data["barcode_value"] == area["barcode_value"]
    _assert_chain(rows)


def _create_worker(client: TestClient) -> dict[str, Any]:
    return _created(
        admin_of(client).post(
            "/api/workers",
            json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE").upper()},
        )
    )


def test_area_worker_id_mode_changes_are_audited(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client)
    inactive = _create_worker(client)
    deactivated = admin_of(client).patch(
        f"/api/workers/{inactive['id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 200
    area = _create_area(client)
    path = f"/api/areas/{area['id']}"
    fixed = {"worker_identification_mode": "FIXED", "fixed_worker_id": worker["id"]}
    assert admin_of(client).patch(path, json=fixed).status_code == 200
    assert (
        admin_of(client).patch(path, json={"worker_identification_mode": "DISABLED"}).status_code
        == 200
    )

    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED", "UPDATED"]
    disabled = {"worker_identification_mode": "DISABLED", "fixed_worker_id": None}

    def identity(data: dict[str, Any]) -> dict[str, Any]:
        return {key: data[key] for key in disabled}

    assert (identity(rows[1].before_data), identity(rows[1].after_data)) == (disabled, fixed)
    assert (identity(rows[2].before_data), identity(rows[2].after_data)) == (fixed, disabled)
    _assert_chain(rows)

    # Scanned session mode is selectable (Phase 13 slice 5), audited like any mode change.
    assert (
        admin_of(client).patch(path, json={"worker_identification_mode": "SCANNED"}).status_code
        == 200
    )
    assert (
        admin_of(client).patch(path, json={"worker_identification_mode": "DISABLED"}).status_code
        == 200
    )
    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED"] + ["UPDATED"] * 4
    scanned = {"worker_identification_mode": "SCANNED", "fixed_worker_id": None}
    assert (identity(rows[3].before_data), identity(rows[3].after_data)) == (disabled, scanned)
    assert (identity(rows[4].before_data), identity(rows[4].after_data)) == (scanned, disabled)
    _assert_chain(rows)

    # Refusals (E2–E5) append nothing.
    before = _audit_count(db_engine)
    for body in (
        {"worker_identification_mode": "FIXED"},
        {"fixed_worker_id": worker["id"]},
        {"worker_identification_mode": "FIXED", "fixed_worker_id": 999_999_999},
        {"worker_identification_mode": "FIXED", "fixed_worker_id": inactive["id"]},
    ):
        assert admin_of(client).patch(path, json=body).status_code in (409, 422), body
    assert _audit_count(db_engine) == before


def test_area_deactivation_is_audited_only_once_it_succeeds(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    path = f"/api/areas/{area['id']}"
    with db_engine.begin() as connection:
        flow_id = connection.execute(
            sa.insert(models.QuantityFlow)
            .values(
                part_number=f"PN{uuid.uuid4().hex[:8].upper()}",
                quantity=5,
                current_area_id=int(area["id"]),
            )
            .returning(models.QuantityFlow.id)
        ).scalar_one()
    stored = _stored(db_engine, models.Area, area["id"])
    count = _audit_count(db_engine)

    blocked = admin_of(client).patch(path, json={"is_active": False})
    assert blocked.status_code == 409
    assert "holds active quantity" in blocked.json()["detail"]
    assert _audit_count(db_engine) == count
    assert _stored(db_engine, models.Area, area["id"]) == stored

    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.QuantityFlow)
            .where(models.QuantityFlow.id == flow_id)
            .values(status="SCRAPPED", closed_at=sa.func.now())
        )
    assert admin_of(client).patch(path, json={"is_active": False}).status_code == 200

    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert (rows[1].before_data["is_active"], rows[1].after_data["is_active"]) == (True, False)


def test_area_activation_under_an_inactive_department_audits_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    department = _create_department(client)
    area = _create_area(client, department_id=int(department["id"]))
    assert (
        admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": False}).status_code
        == 200
    )
    assert (
        admin_of(client)
        .patch(f"/api/departments/{department['id']}", json={"is_active": False})
        .status_code
        == 200
    )
    stored = _stored(db_engine, models.Area, area["id"])
    count = _audit_count(db_engine)

    response = admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": True})
    assert response.status_code == 409
    assert _audit_count(db_engine) == count
    assert _stored(db_engine, models.Area, area["id"]) == stored


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def test_operation_create_and_edits_are_audited_with_duration_seconds(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    code = _unique("OP")
    operation = _create_operation(
        client,
        area_id=int(area["id"]),
        code=code,
        name="Turning",
        default_expected_duration="PT30M",
    )
    path = f"/api/operations/{operation['id']}"
    for body in (
        {"default_expected_duration": "PT1H"},
        {"default_expected_duration": None},
        {"is_external": True},
        {"is_active": False},
    ):
        assert admin_of(client).patch(path, json=body).status_code == 200

    rows = _audit_rows(db_engine, "Operation", operation["id"])
    assert [row.event_type for row in rows] == ["CREATED"] + ["UPDATED"] * 4
    assert rows[0].before_data is None
    assert rows[0].after_data == {
        "area_id": area["id"],
        "code": code,
        "name": "Turning",
        "description": None,
        "default_expected_duration_seconds": 1800,
        "is_external": False,
        "is_active": True,
    }
    key = "default_expected_duration_seconds"
    durations = [(row.before_data[key], row.after_data[key]) for row in rows[1:3]]
    assert durations == [(1800, 3600), (3600, None)]
    assert (rows[3].before_data["is_external"], rows[3].after_data["is_external"]) == (False, True)
    assert (rows[4].before_data["is_active"], rows[4].after_data["is_active"]) == (True, False)
    _assert_chain(rows)


def test_operation_refusals_audit_nothing(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    inactive_area = _create_area(client)
    assert (
        admin_of(client)
        .patch(f"/api/areas/{inactive_area['id']}", json={"is_active": False})
        .status_code
        == 200
    )
    first = _create_operation(client, area_id=int(area["id"]))
    operation = _create_operation(client, area_id=int(area["id"]))
    path = f"/api/operations/{operation['id']}"
    stored = _stored(db_engine, models.Operation, operation["id"])
    count = _audit_count(db_engine)
    operations = _row_count(db_engine, models.Operation)

    refusals: list[tuple[Callable[[], Response], int]] = [
        (
            lambda: admin_of(client).post(
                "/api/operations",
                json={"area_id": area["id"], "code": _unique("OP"), "default_expected_duration": 0},
            ),
            422,
        ),
        (lambda: admin_of(client).patch(path, json={"default_expected_duration": -60}), 422),
        (
            lambda: admin_of(client).post(
                "/api/operations", json={"area_id": area["id"], "code": first["code"]}
            ),
            409,
        ),
        (lambda: admin_of(client).patch(path, json={"code": first["code"]}), 409),
        (
            lambda: admin_of(client).post("/api/operations", json={"area_id": 999999, "code": "X"}),
            422,
        ),
        (
            lambda: admin_of(client).post(
                "/api/operations", json={"area_id": inactive_area["id"], "code": "X"}
            ),
            409,
        ),
        (lambda: admin_of(client).patch(path, json={"area_id": inactive_area["id"]}), 422),
    ]
    for attempt, status in refusals:
        assert attempt().status_code == status

    assert _audit_count(db_engine) == count
    assert _row_count(db_engine, models.Operation) == operations
    assert _stored(db_engine, models.Operation, operation["id"]) == stored


def test_operation_create_race_lost_at_flush_audits_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    code = _unique("OP")
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("INSERT INTO operations (area_id, code) VALUES (:area_id, :code)"),
            {"area_id": area["id"], "code": code},
        )
        thread, results = _start(
            lambda: admin_of(client).post(
                "/api/operations", json={"area_id": area["id"], "code": code}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409
    assert response.json()["detail"] == "The Area already has an Operation with this code."
    assert _audit_count(db_engine) == count


# ---------------------------------------------------------------------------
# Scan Stations
# ---------------------------------------------------------------------------


def test_scan_station_create_rebind_and_reactivation_are_audited(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    target = _create_area(client)
    station = _create_scan_station(client, area_id=int(area["id"]), is_active=False)
    path = f"/api/scan-stations/{station['station_id']}"
    assert admin_of(client).patch(path, json={"area_id": target["id"]}).status_code == 200
    assert admin_of(client).patch(path, json={"is_active": True}).status_code == 200

    rows = _audit_rows(db_engine, "ScanStation", station["station_id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED", "UPDATED"]
    assert rows[0].entity_id == station["station_id"]
    assert rows[0].before_data is None
    assert rows[0].after_data == {"area_id": area["id"], "is_active": False}
    assert (rows[1].before_data, rows[1].after_data) == (
        {"area_id": area["id"], "is_active": False},
        {"area_id": target["id"], "is_active": False},
    )
    assert (rows[2].before_data, rows[2].after_data) == (
        {"area_id": target["id"], "is_active": False},
        {"area_id": target["id"], "is_active": True},
    )
    _assert_chain(rows)


def test_scan_station_refusals_audit_nothing(client: TestClient, db_engine: Engine) -> None:
    station = _create_scan_station(client)
    inactive_area = _create_area(client)
    assert (
        admin_of(client)
        .patch(f"/api/areas/{inactive_area['id']}", json={"is_active": False})
        .status_code
        == 200
    )
    path = f"/api/scan-stations/{station['station_id']}"
    stored = _stored(db_engine, models.ScanStation, station["station_id"])
    count = _audit_count(db_engine)
    stations = _row_count(db_engine, models.ScanStation)

    refusals: list[tuple[Callable[[], Response], int]] = [
        (
            lambda: admin_of(client).post(
                "/api/scan-stations",
                json={"station_id": station["station_id"], "area_id": station["area_id"]},
            ),
            409,
        ),
        (
            lambda: admin_of(client).post(
                "/api/scan-stations", json={"station_id": "ST 1", "area_id": station["area_id"]}
            ),
            422,
        ),
        (lambda: admin_of(client).patch(path, json={"area_id": inactive_area["id"]}), 409),
        (
            lambda: admin_of(client).patch(
                "/api/scan-stations/does-not-exist", json={"is_active": False}
            ),
            404,
        ),
    ]
    for attempt, status in refusals:
        assert attempt().status_code == status

    assert _audit_count(db_engine) == count
    assert _row_count(db_engine, models.ScanStation) == stations
    assert _stored(db_engine, models.ScanStation, station["station_id"]) == stored


def test_scan_station_create_race_lost_at_commit_leaves_no_orphan_audit_row(
    client: TestClient, db_engine: Engine
) -> None:
    """PLAN S2 risk, create path: the CREATED row is staged when the
    INSERT loses at COMMIT; both roll back together."""
    area = _create_area(client)
    station_id = _unique("ST")
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("INSERT INTO scan_stations (station_id, area_id) VALUES (:id, :area_id)"),
            {"id": station_id, "area_id": area["id"]},
        )
        thread, results = _start(
            lambda: admin_of(client).post(
                "/api/scan-stations", json={"station_id": station_id, "area_id": area["id"]}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409
    assert response.json()["detail"] == "A Scan Station with this Station ID already exists."
    assert _audit_count(db_engine) == count
    assert _audit_rows(db_engine, "ScanStation", station_id) == []


# ---------------------------------------------------------------------------
# Lock-first
# ---------------------------------------------------------------------------


class _LockCase(NamedTuple):
    entity_type: str
    table: str
    key_column: str
    holder_column: str
    holder_value: object
    # The PATCH body, built against the module client (a rebind target).
    patch: Callable[[TestClient], dict[str, Any]]


def _lock_cases() -> list[_LockCase]:
    return [
        _LockCase(
            "Department",
            "departments",
            "id",
            "name",
            _unique("HELD"),
            lambda c: {"is_active": False},
        ),
        _LockCase(
            "Area", "areas", "id", "name", _unique("HELD"), lambda c: {"description": "edited"}
        ),
        _LockCase(
            "Operation", "operations", "id", "name", "held", lambda c: {"description": "edited"}
        ),
        _LockCase(
            "ScanStation",
            "scan_stations",
            "station_id",
            "is_active",
            False,
            lambda c: {"area_id": _create_area(c)["id"]},
        ),
    ]


def _create_entity(client: TestClient, entity_type: str) -> tuple[str, dict[str, Any]]:
    """(PATCH path, created body) of a fresh entity of ``entity_type``."""
    if entity_type == "Department":
        body = _create_department(client)
        return f"/api/departments/{body['id']}", body
    if entity_type == "Area":
        body = _create_area(client)
        return f"/api/areas/{body['id']}", body
    if entity_type == "Operation":
        body = _create_operation(client)
        return f"/api/operations/{body['id']}", body
    body = _create_scan_station(client)
    return f"/api/scan-stations/{body['station_id']}", body


@pytest.mark.parametrize("case", _lock_cases(), ids=lambda case: case.entity_type)
def test_concurrent_edit_waits_and_audits_the_committed_predecessor(
    client: TestClient, db_engine: Engine, case: _LockCase
) -> None:
    path, entity = _create_entity(client, case.entity_type)
    key = entity[case.key_column]
    body = case.patch(client)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(f"SELECT 1 FROM {case.table} WHERE {case.key_column} = :key FOR UPDATE"),
            {"key": key},
        )
        holder.execute(
            sa.text(
                f"UPDATE {case.table} SET {case.holder_column} = :value"
                f" WHERE {case.key_column} = :key"
            ),
            {"value": case.holder_value, "key": key},
        )
        thread, results = _start(lambda: admin_of(client).patch(path, json=body))
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 200, response.text
    assert response.json()[case.holder_column] == case.holder_value
    last = _audit_rows(db_engine, case.entity_type, key)[-1]
    assert last.event_type == "UPDATED"
    assert last.before_data[case.holder_column] == case.holder_value
    assert last.after_data[case.holder_column] == case.holder_value
    for field, value in body.items():
        assert last.after_data[field] == value


class _KeyShareCase(NamedTuple):
    name: str
    entity_type: str
    body: dict[str, Any]
    blocks: bool


@pytest.mark.parametrize(
    "case",
    [
        _KeyShareCase("station-toggle", "ScanStation", {"is_active": False}, blocks=False),
        _KeyShareCase("area-display-edit", "Area", {"description": "edited"}, blocks=False),
        _KeyShareCase("area-deactivation", "Area", {"is_active": False}, blocks=True),
        _KeyShareCase("department-deactivation", "Department", {"is_active": False}, blocks=False),
        _KeyShareCase("department-rename", "Department", {"name": _unique("REN")}, blocks=True),
        _KeyShareCase("operation-display-edit", "Operation", {"description": "e"}, blocks=False),
    ],
    ids=lambda case: case.name,
)
def test_lock_mode_never_waits_on_key_share_except_area_deactivation(
    client: TestClient, db_engine: Engine, case: _KeyShareCase
) -> None:
    """FK checks and the allocation path hold FOR KEY SHARE; the
    lock-first lock never waits for them. Only an Area deactivation
    (today's FOR UPDATE) waits, and an UPDATE that changes a unique-key
    column (a Department name) waits because PostgreSQL itself takes
    FOR UPDATE for it, as before the audit."""
    path, entity = _create_entity(client, case.entity_type)
    table, column = {
        "Department": ("departments", "id"),
        "Area": ("areas", "id"),
        "Operation": ("operations", "id"),
        "ScanStation": ("scan_stations", "station_id"),
    }[case.entity_type]
    key = entity[column]
    count = len(_audit_rows(db_engine, case.entity_type, key))
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE {column} = :key FOR KEY SHARE"), {"key": key}
        )
        thread, results = _start(lambda: admin_of(client).patch(path, json=case.body))
        if case.blocks:
            _assert_blocked(thread)
        else:
            # Completes while the holder is still open.
            thread.join(timeout=5)
            assert not thread.is_alive()
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 200, response.text
    rows = _audit_rows(db_engine, case.entity_type, key)
    assert len(rows) == count + 1
    assert rows[-1].event_type == "UPDATED"


# ---------------------------------------------------------------------------
# Machine Asset Tag format
# ---------------------------------------------------------------------------


def test_asset_tag_format_lifecycle_is_audited_without_next_sequence(
    unconfigured_client: _Deployment,
) -> None:
    client, engine = unconfigured_client
    first = admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 4})
    assert first.status_code == 200, first.text
    assert (
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 5}).status_code
        == 200
    )
    assert (
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 5}).status_code
        == 200
    )
    assert (
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "A B", "digits": 5}).status_code
        == 422
    )
    assert (
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 9}).status_code
        == 422
    )

    rows = _audit_rows(engine, "MachineAssetTagConfig", 1)
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert rows[0].entity_id == "1"
    assert rows[0].before_data is None
    assert rows[0].after_data == {"prefix": "CD-", "digits": 4}
    assert (rows[1].before_data, rows[1].after_data) == (
        {"prefix": "CD-", "digits": 4},
        {"prefix": "CD-", "digits": 5},
    )
    _assert_chain(rows)

    # Machine creation advances the never-reuse counter and appends
    # exactly one Machine CREATED row (slice 2b) — never a format row.
    area = _create_area(client)
    sequence = _stored(engine, models.MachineAssetTagConfig, 1).next_sequence
    count = _audit_count(engine)
    format_count = _audit_count(engine, ("MachineAssetTagConfig",))
    machine = client.post("/api/machines", json={"area_id": area["id"], "name": _unique("M")})
    assert machine.status_code == 201, machine.text
    assert _stored(engine, models.MachineAssetTagConfig, 1).next_sequence == sequence + 1
    assert _audit_count(engine) == count + 1
    assert _audit_count(engine, ("MachineAssetTagConfig",)) == format_count
    assert [row.event_type for row in _audit_rows(engine, "Machine", machine.json()["id"])] == [
        "CREATED"
    ]
    for row in _audit_rows(engine, "MachineAssetTagConfig", 1):
        for snapshot in (row.before_data, row.after_data):
            assert snapshot is None or "next_sequence" not in snapshot


@pytest.mark.parametrize("holder", ["format-edit", "machine-counter"])
def test_asset_tag_format_edit_waits_and_audits_the_committed_predecessor(
    unconfigured_client: _Deployment, holder: str
) -> None:
    """Lock-first on the singleton: a format PUT waits for a concurrent
    format edit or Machine creation's counter UPDATE, then audits the
    committed row and never overwrites ``next_sequence``."""
    client, engine = unconfigured_client
    assert (
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 4}).status_code
        == 200
    )
    expected_before = {"prefix": "CD-", "digits": 4}
    sequence = _stored(engine, models.MachineAssetTagConfig, 1).next_sequence
    with engine.connect() as connection:
        connection.execute(
            sa.text("SELECT 1 FROM machine_asset_tag_config WHERE id = 1 FOR UPDATE")
        )
        if holder == "format-edit":
            connection.execute(
                sa.text("UPDATE machine_asset_tag_config SET prefix = 'MS-', digits = 6")
            )
            expected_before = {"prefix": "MS-", "digits": 6}
        else:
            connection.execute(
                sa.text("UPDATE machine_asset_tag_config SET next_sequence = next_sequence + 1")
            )
        thread, results = _start(
            lambda: admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "AB-", "digits": 5})
        )
        _assert_blocked(thread)
        connection.commit()
    response = _finish(thread, results)

    assert response.status_code == 200, response.text
    rows = _audit_rows(engine, "MachineAssetTagConfig", 1)
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert rows[-1].before_data == expected_before
    assert rows[-1].after_data == {"prefix": "AB-", "digits": 5}
    stored = _stored(engine, models.MachineAssetTagConfig, 1)
    assert stored.next_sequence == sequence + (1 if holder == "machine-counter" else 0)


def test_concurrent_first_asset_tag_format_save_is_a_conflict(
    unconfigured_client: _Deployment,
) -> None:
    """S2-F5: the loser of two concurrent first configurations is a 409
    on the singleton primary key, writes nothing and audits nothing."""
    client, engine = unconfigured_client
    with engine.connect() as holder:
        holder.execute(
            sa.text(
                "INSERT INTO machine_asset_tag_config (id, prefix, digits) VALUES (1, 'HD-', 4)"
            )
        )
        thread, results = _start(
            lambda: admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 5})
        )
        # The INSERT waits on the holder's uncommitted singleton row.
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "The Machine Asset Tag format was just saved by someone else."
        " Refresh the page and open Barcode configuration again to see the saved format,"
        " then apply your change again."
    )
    stored = _stored(engine, models.MachineAssetTagConfig, 1)
    assert (stored.prefix, stored.digits) == ("HD-", 4)
    assert _audit_count(engine, ("MachineAssetTagConfig",)) == 0


# ---------------------------------------------------------------------------
# One row per request, and the concurrent deactivation no-op
# ---------------------------------------------------------------------------


def test_multi_field_patch_is_one_audit_row(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    new_name = _unique("DEPT")
    response = admin_of(client).patch(
        f"/api/departments/{department['id']}", json={"name": new_name, "is_active": False}
    )
    assert response.status_code == 200, response.text
    rows = _audit_rows(db_engine, "Department", department["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    settings = {"board_seconds_per_row": 3, "board_min_page_seconds": 6}
    assert rows[1].before_data == {"name": department["name"], "is_active": True, **settings}
    assert rows[1].after_data == {"name": new_name, "is_active": False, **settings}

    area = _create_area(client, description="old")
    response = admin_of(client).patch(
        f"/api/areas/{area['id']}",
        json={"description": "new", "color": "var(--a3)", "is_active": False},
    )
    assert response.status_code == 200, response.text
    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    changed = {
        key for key in rows[1].after_data if rows[1].after_data[key] != rows[1].before_data[key]
    }
    assert changed == {"description", "color", "is_active"}


def test_deactivation_already_committed_by_a_concurrent_writer(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client, description="old")
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("UPDATE areas SET is_active = false WHERE id = :id"), {"id": area["id"]}
        )
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/areas/{area['id']}", json={"description": "new", "is_active": False}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 200, response.text
    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert rows[1].before_data["is_active"] is False
    assert rows[1].after_data["is_active"] is False
    changed = {
        key for key in rows[1].after_data if rows[1].after_data[key] != rows[1].before_data[key]
    }
    assert changed == {"description"}


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def _boom(*args: object, **kwargs: object) -> None:
    raise RuntimeError("audit persistence failed")


_ENVIRONMENT_MODELS: tuple[type[models.Base], ...] = (
    models.Department,
    models.Area,
    models.Operation,
    models.ScanStation,
    models.MachineAssetTagConfig,
)


def _snapshot_tables(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    """Every stored row of the five configuration tables and the audit table."""
    with engine.connect() as connection:
        return {
            table.name: [
                tuple(row)
                for row in connection.execute(sa.select(table).order_by(*table.primary_key.columns))
            ]
            for table in (*map(_table, _ENVIRONMENT_MODELS), _table(models.AuditEvent))
        }


def test_failed_audit_write_rolls_back_every_department_area_operation_station_path(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    department = _create_department(client)
    area = _create_area(client, department_id=int(department["id"]))
    operation = _create_operation(client, area_id=int(area["id"]))
    station = _create_scan_station(client, area_id=int(area["id"]))
    target = _create_area(client, department_id=int(department["id"]))
    before = _snapshot_tables(db_engine)

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    attempts: list[Callable[[], Response]] = [
        lambda: admin_of(client).post("/api/departments", json={"name": _unique("DEPT")}),
        lambda: admin_of(client).patch(
            f"/api/departments/{department['id']}", json={"name": _unique("DEPT")}
        ),
        lambda: admin_of(client).patch(
            f"/api/departments/{department['id']}",
            json={"board_seconds_per_row": 2, "board_min_page_seconds": 10},
        ),
        lambda: admin_of(client).post(
            "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
        ),
        lambda: admin_of(client).patch(
            f"/api/areas/{area['id']}", json={"description": "changed", "is_terminal": True}
        ),
        lambda: admin_of(client).post(
            "/api/operations", json={"area_id": area["id"], "code": _unique("OP")}
        ),
        lambda: admin_of(client).patch(
            f"/api/operations/{operation['id']}",
            json={"code": _unique("OP"), "default_expected_duration": "PT5M"},
        ),
        lambda: admin_of(client).post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area["id"]}
        ),
        lambda: admin_of(client).patch(
            f"/api/scan-stations/{station['station_id']}",
            json={"area_id": target["id"], "is_active": False},
        ),
    ]
    for attempt in attempts:
        with pytest.raises(RuntimeError, match="audit persistence failed"):
            attempt()
    monkeypatch.undo()

    assert _snapshot_tables(db_engine) == before


def test_failed_audit_write_rolls_back_both_asset_tag_paths(
    unconfigured_client: _Deployment, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = unconfigured_client
    empty = _snapshot_tables(engine)

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 4})
    monkeypatch.undo()
    assert _snapshot_tables(engine) == empty
    assert _row_count(engine, models.MachineAssetTagConfig) == 0

    assert (
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "CD-", "digits": 4}).status_code
        == 200
    )
    seeded = _snapshot_tables(engine)
    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        admin_of(client).put(_ASSET_TAG_PATH, json={"prefix": "MS-", "digits": 6})
    monkeypatch.undo()
    assert _snapshot_tables(engine) == seeded
    assert _row_count(engine, models.MachineAssetTagConfig) == 1


# ---------------------------------------------------------------------------
# Chain property and isolation
# ---------------------------------------------------------------------------


def test_audit_chain_over_a_mixed_sequence(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    path = f"/api/areas/{area['id']}"
    steps: list[tuple[dict[str, Any], int]] = [
        ({"name": _unique("AREA")}, 200),
        ({"description": "first"}, 200),
        ({"description": "first"}, 200),  # no-op
        ({"name": "   "}, 422),  # refusal
        ({"color": "var(--a4)"}, 200),
        ({}, 200),  # no-op
        ({"is_terminal": None}, 422),  # refusal
        ({"is_terminal": True}, 200),
        ({"is_active": False}, 200),
    ]
    for body, status in steps:
        assert admin_of(client).patch(path, json=body).status_code == status, body

    rows = _audit_rows(db_engine, "Area", area["id"])
    assert [row.event_type for row in rows] == ["CREATED"] + ["UPDATED"] * 5
    _assert_chain(rows)
    assert rows[-1].after_data["is_active"] is False


def test_production_commands_write_no_environment_audit_row(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    _create_operation(client, area_id=int(area["id"]))
    station = _create_scan_station(client, area_id=int(area["id"]))
    count = _audit_count(db_engine, _ENVIRONMENT_ENTITIES)

    receipt = client.post(
        f"/api/scan-stations/{station['station_id']}/receipts",
        json={
            "part_number": _unique("PN"),
            "quantity": 3,
            "request_type": "MODIFY",
            "route_mode": "FLOATING",
            "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert receipt.status_code == 201, receipt.text
    assert _audit_count(db_engine, _ENVIRONMENT_ENTITIES) == count


# ---------------------------------------------------------------------------
# Static guard
# ---------------------------------------------------------------------------


def _calls_append_audit_event(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append_audit_event"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "audit"
        for node in ast.walk(function)
    )


def test_every_environment_setter_appends_an_audit_row() -> None:
    """A later slice adding a setter to these services audits it, or
    names it in ``_NON_AUDITED_SETTERS`` with a reason; no snapshot ever
    carries the Asset Tag ``next_sequence`` counter."""
    tree = ast.parse(_ENVIRONMENT_SERVICE.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    setters = [
        function
        for function in functions
        if function.name.startswith(("create_", "update_", "upsert_"))
        and function.name not in _NON_AUDITED_SETTERS
    ]
    assert len(setters) >= 9
    assert {function.name for function in setters if not _calls_append_audit_event(function)} == (
        set()
    )

    snapshots = [function for function in functions if function.name.endswith("_snapshot")]
    assert len(snapshots) == 5
    keys = {
        key.value
        for function in snapshots
        for node in ast.walk(function)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant)
    }
    assert "next_sequence" not in keys
    assert {"name", "is_active", "barcode_value", "prefix", "digits"} <= keys
