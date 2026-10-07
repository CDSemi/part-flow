"""Integration tests for the Phase 3.5 environment configuration API.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Covered per the Phase 3.5
scope (Departments, Areas, Operations, Scan Stations, Machine Asset
Tag format):

- shape validation (unknown/server-owned request fields rejected) and
  value validation (blank names, non-URL-safe Station IDs, invalid
  durations, invalid Asset Tag prefix/digits);
- uniqueness (Department name, Operation code per Area, Station ID)
  reported as conflicts;
- active-state constraints: creating/rebinding only against active
  parents, Department deactivation blocked by active Areas, Area
  deactivation blocked by held active quantity;
- relationships: Area → Department, Operation → Area (binding
  immutable), Scan Station → Area (rebind allowed);
- the derived PF:AREA barcode and the singleton Asset Tag format whose
  ``next_sequence`` counter is never client-writable.

The API commits real transactions, so tests isolate through unique
names instead of rollbacks; the module database is dropped afterwards.
The development database in DATABASE_URL is only used as the admin
connection for CREATE/DROP DATABASE.
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
from pydantic import TypeAdapter
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_environment_api"


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


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    """Application client wired to the temporary database.

    Runs the real startup path: DATABASE_URL is pointed at the test
    database and the cached settings are cleared, so the lifespan
    builds the engine exactly as production startup does.
    """
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
    """Direct database access for seeding state the API cannot create."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _create_department(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = admin_of(client).post(
        "/api/departments", json={"name": _unique("DEPT"), **overrides}
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_area(
    client: TestClient, department_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if department_id is None:
        department_id = int(_create_department(client)["id"])
    payload = {"department_id": department_id, "name": _unique("AREA"), **overrides}
    response = admin_of(client).post("/api/areas", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_operation(
    client: TestClient, area_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if area_id is None:
        area_id = int(_create_area(client)["id"])
    payload = {"area_id": area_id, "code": _unique("OP"), **overrides}
    response = admin_of(client).post("/api/operations", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_scan_station(
    client: TestClient, area_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if area_id is None:
        area_id = int(_create_area(client)["id"])
    payload = {"station_id": _unique("ST"), "area_id": area_id, **overrides}
    response = admin_of(client).post("/api/scan-stations", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _seed_active_quantity_flow(db_engine: Engine, area_id: int) -> int:
    """Insert an ACTIVE QuantityFlow located in the Area (Phase 3 shape)."""
    part_number = f"PN{uuid.uuid4().hex[:8].upper()}"
    with db_engine.begin() as connection:
        return int(
            connection.execute(
                sa.insert(models.QuantityFlow)
                .values(part_number=part_number, quantity=5, current_area_id=area_id)
                .returning(models.QuantityFlow.id)
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------


def test_department_create_and_list(client: TestClient) -> None:
    created = _create_department(client)

    assert created["is_active"] is True
    assert created["created_at"] and created["updated_at"]

    listed = client.get("/api/departments")
    assert listed.status_code == 200
    assert created["id"] in [row["id"] for row in listed.json()]


def test_department_name_is_trimmed_and_required(client: TestClient) -> None:
    name = _unique("DEPT")
    created = _create_department(client, name=f"  {name}  ")
    assert created["name"] == name

    blank = admin_of(client).post("/api/departments", json={"name": "   "})
    assert blank.status_code == 422
    assert "must not be empty" in blank.json()["detail"]


def test_department_duplicate_name_conflict(client: TestClient) -> None:
    created = _create_department(client)

    duplicate = admin_of(client).post("/api/departments", json={"name": created["name"]})
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_department_rename_and_rename_conflict(client: TestClient) -> None:
    first = _create_department(client)
    second = _create_department(client)

    new_name = _unique("DEPT")
    renamed = admin_of(client).patch(f"/api/departments/{second['id']}", json={"name": new_name})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == new_name

    collision = admin_of(client).patch(
        f"/api/departments/{second['id']}", json={"name": first["name"]}
    )
    assert collision.status_code == 409


def test_department_unknown_id_not_found(client: TestClient) -> None:
    response = admin_of(client).patch("/api/departments/999999", json={"is_active": False})
    assert response.status_code == 404


def test_department_unknown_field_rejected(client: TestClient) -> None:
    response = admin_of(client).post(
        "/api/departments", json={"name": _unique("DEPT"), "barcode_value": "PF:AREA:1"}
    )
    assert response.status_code == 422


def test_department_deactivation_blocked_by_active_area(client: TestClient) -> None:
    department = _create_department(client)
    area = _create_area(client, department_id=int(department["id"]))

    blocked = admin_of(client).patch(
        f"/api/departments/{department['id']}", json={"is_active": False}
    )
    assert blocked.status_code == 409
    assert "active Areas" in blocked.json()["detail"]

    deactivated_area = admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": False})
    assert deactivated_area.status_code == 200

    deactivated = admin_of(client).patch(
        f"/api/departments/{department['id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    # The tree never re-activates bottom-up: the Area cannot come back
    # while its Department is inactive.
    reactivate_area = admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": True})
    assert reactivate_area.status_code == 409
    assert "Department" in reactivate_area.json()["detail"]


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------


def test_area_create_derives_stable_pf_area_barcode(client: TestClient) -> None:
    first = _create_area(client)
    second = _create_area(client)

    assert first["barcode_value"] == f"PF:AREA:{first['id']}"
    assert second["barcode_value"] == f"PF:AREA:{second['id']}"
    assert first["barcode_value"] != second["barcode_value"]
    assert first["is_terminal"] is False
    assert first["is_active"] is True


def test_area_create_requires_existing_active_department(client: TestClient) -> None:
    missing = admin_of(client).post(
        "/api/areas", json={"department_id": 999999, "name": _unique("AREA")}
    )
    assert missing.status_code == 422

    department = _create_department(client)
    deactivated = admin_of(client).patch(
        f"/api/departments/{department['id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 200

    inactive = admin_of(client).post(
        "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
    )
    assert inactive.status_code == 409
    assert "inactive" in inactive.json()["detail"]


def test_area_barcode_is_never_client_writable(client: TestClient) -> None:
    department = _create_department(client)
    create = admin_of(client).post(
        "/api/areas",
        json={
            "department_id": department["id"],
            "name": _unique("AREA"),
            "barcode_value": "PF:AREA:HIJACK",
        },
    )
    assert create.status_code == 422

    area = _create_area(client, department_id=int(department["id"]))
    patch = admin_of(client).patch(
        f"/api/areas/{area['id']}", json={"barcode_value": "PF:AREA:HIJACK"}
    )
    assert patch.status_code == 422


def test_area_display_properties_update_keeps_identity(client: TestClient) -> None:
    area = _create_area(client, description="old", color="var(--a1)")

    updated = admin_of(client).patch(
        f"/api/areas/{area['id']}",
        json={
            "name": _unique("AREA"),
            "description": "Lathe row, north wall",
            "color": "var(--a2)",
            "icon_url": None,
            "is_terminal": True,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    # Identity and barcode stay stable while display properties change.
    assert body["id"] == area["id"]
    assert body["barcode_value"] == area["barcode_value"]
    assert body["description"] == "Lathe row, north wall"
    assert body["color"] == "var(--a2)"
    assert body["icon_url"] is None
    assert body["is_terminal"] is True
    assert body["updated_at"] > area["updated_at"]


def test_area_deactivation_blocked_while_holding_quantity(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    other_area = _create_area(client)
    flow_id = _seed_active_quantity_flow(db_engine, int(area["id"]))

    blocked = admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": False})
    assert blocked.status_code == 409
    assert "holds active quantity" in blocked.json()["detail"]

    # Once the quantity is located elsewhere the Area can deactivate.
    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.QuantityFlow)
            .where(models.QuantityFlow.id == flow_id)
            .values(current_area_id=int(other_area["id"]))
        )
    deactivated = admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": False})
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Areas — Worker ID mode and Fixed Worker (Phase 13 S3)
# ---------------------------------------------------------------------------

_E2 = "Choose the Fixed Worker for Fixed Worker mode."
_E3 = "A Fixed Worker can be set only in Fixed Worker mode."
_E7 = "Worker ID mode must be DISABLED, FIXED or SCANNED."


def _create_worker(client: TestClient) -> dict[str, Any]:
    response = admin_of(client).post(
        "/api/workers",
        json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE").upper()},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _force_worker_inactive(db_engine: Engine, worker_id: int) -> None:
    """A state the API refuses for a Fixed Worker (fixture only)."""
    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.Worker).where(models.Worker.id == worker_id).values(is_active=False)
        )


def _area_count(db_engine: Engine) -> int:
    with db_engine.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM areas")).scalar_one())


def _identity(body: dict[str, Any]) -> tuple[Any, Any]:
    return body["worker_identification_mode"], body["fixed_worker_id"]


def test_area_create_sets_the_worker_identification_mode(
    client: TestClient, db_engine: Engine
) -> None:
    assert _identity(_create_area(client)) == ("DISABLED", None)
    worker = _create_worker(client)
    fixed = _create_area(client, worker_identification_mode="FIXED", fixed_worker_id=worker["id"])
    assert _identity(fixed) == ("FIXED", worker["id"])
    listed = {area["id"]: area for area in client.get("/api/areas").json()}
    assert _identity(listed[fixed["id"]]) == ("FIXED", worker["id"])

    inactive = _create_worker(client)
    deactivated = admin_of(client).patch(
        f"/api/workers/{inactive['id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 200
    department_id = int(_create_department(client)["id"])
    refusals = [
        ({"worker_identification_mode": "FIXED"}, 422, _E2),
        ({"worker_identification_mode": "DISABLED", "fixed_worker_id": worker["id"]}, 422, _E3),
        ({"fixed_worker_id": worker["id"]}, 422, _E3),
        (
            {"worker_identification_mode": "FIXED", "fixed_worker_id": 999_999_999},
            422,
            "Worker 999999999 does not exist.",
        ),
        (
            {"worker_identification_mode": "FIXED", "fixed_worker_id": inactive["id"]},
            409,
            f"Worker '{inactive['name']}' is inactive and cannot be the Fixed Worker of an"
            " Area. Choose an active Worker.",
        ),
    ]
    before = _area_count(db_engine)
    for fields, status, detail in refusals:
        payload = {"department_id": department_id, "name": _unique("AREA"), **fields}
        response = admin_of(client).post("/api/areas", json=payload)
        assert response.status_code == status, (fields, response.text)
        assert response.json()["detail"] == detail
    unknown = admin_of(client).post(
        "/api/areas",
        json={
            "department_id": department_id,
            "name": _unique("AREA"),
            "worker_identification_mode": "MANUAL",
        },
    )
    assert unknown.status_code == 422
    assert _area_count(db_engine) == before


def test_area_update_changes_and_clears_the_fixed_worker(client: TestClient) -> None:
    first, second = _create_worker(client), _create_worker(client)
    area = _create_area(client)
    path = f"/api/areas/{area['id']}"

    def patch(**fields: Any) -> Any:
        return admin_of(client).patch(path, json=fields)

    def fix(worker: dict[str, Any]) -> tuple[Any, Any]:
        response = patch(worker_identification_mode="FIXED", fixed_worker_id=worker["id"])
        assert response.status_code == 200, response.text
        return _identity(response.json())

    assert fix(first) == ("FIXED", first["id"])
    assert fix(second) == ("FIXED", second["id"])
    # The Fixed Worker alone on a Fixed Area changes it.
    assert _identity(patch(fixed_worker_id=first["id"]).json()) == ("FIXED", first["id"])
    # Leaving Fixed clears the Worker without the client sending it.
    assert _identity(patch(worker_identification_mode="DISABLED").json()) == ("DISABLED", None)
    # A Fixed Worker alone on a Disabled Area is refused.
    refused = patch(fixed_worker_id=first["id"])
    assert (refused.status_code, refused.json()["detail"]) == (422, _E3)
    explicit_null = patch(worker_identification_mode=None)
    assert (explicit_null.status_code, explicit_null.json()["detail"]) == (422, _E7)
    fixed_without_worker = patch(worker_identification_mode="FIXED")
    assert (fixed_without_worker.status_code, fixed_without_worker.json()["detail"]) == (422, _E2)

    # Scanned session mode is selectable (Phase 13 slice 5) and saves unchanged.
    assert _identity(patch(worker_identification_mode="SCANNED").json()) == ("SCANNED", None)
    assert patch(name=_unique("AREA")).status_code == 200
    assert patch(worker_identification_mode="SCANNED").status_code == 200


def test_area_activation_rejudges_an_inactive_fixed_worker(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _create_worker(client)
    area = _create_area(client, worker_identification_mode="FIXED", fixed_worker_id=worker["id"])
    path = f"/api/areas/{area['id']}"
    assert admin_of(client).patch(path, json={"is_active": False}).status_code == 200
    _force_worker_inactive(db_engine, int(worker["id"]))

    refused = admin_of(client).patch(path, json={"is_active": True})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"The Fixed Worker '{worker['name']}' is inactive. Choose an active Fixed Worker"
        " before activating this Area."
    )
    # An unrelated edit never re-judges the Fixed Worker.
    renamed = admin_of(client).patch(path, json={"name": _unique("AREA")})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["is_active"] is False


def test_area_identity_patch_with_identical_values_is_a_no_op(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _create_worker(client)
    area = _create_area(client, worker_identification_mode="FIXED", fixed_worker_id=worker["id"])

    def audit_count() -> int:
        with db_engine.connect() as connection:
            return int(
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_events WHERE entity_type = 'Area'"
                        " AND entity_id = :id"
                    ),
                    {"id": str(area["id"])},
                ).scalar_one()
            )

    before = audit_count()
    response = admin_of(client).patch(
        f"/api/areas/{area['id']}",
        json={"worker_identification_mode": "FIXED", "fixed_worker_id": worker["id"]},
    )
    assert response.status_code == 200
    assert response.json()["updated_at"] == area["updated_at"]
    assert audit_count() == before


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def test_operation_create_with_duration(client: TestClient) -> None:
    operation = _create_operation(
        client,
        name="Turning",
        description="Lathe work",
        default_expected_duration="PT30M",
        is_external=False,
    )

    expected = TypeAdapter(datetime.timedelta).dump_python(
        datetime.timedelta(minutes=30), mode="json"
    )
    assert operation["default_expected_duration"] == expected
    assert operation["name"] == "Turning"
    assert operation["is_external"] is False
    assert operation["is_active"] is True


def test_operation_code_unique_per_area_only(client: TestClient) -> None:
    operation = _create_operation(client)

    duplicate = admin_of(client).post(
        "/api/operations", json={"area_id": operation["area_id"], "code": operation["code"]}
    )
    assert duplicate.status_code == 409
    assert "already has an Operation" in duplicate.json()["detail"]

    other_area = _create_area(client)
    elsewhere = admin_of(client).post(
        "/api/operations", json={"area_id": other_area["id"], "code": operation["code"]}
    )
    assert elsewhere.status_code == 201


def test_operation_requires_existing_active_area(client: TestClient) -> None:
    missing = admin_of(client).post(
        "/api/operations", json={"area_id": 999999, "code": _unique("OP")}
    )
    assert missing.status_code == 422

    area = _create_area(client)
    assert (
        admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": False}).status_code
        == 200
    )
    inactive = admin_of(client).post(
        "/api/operations", json={"area_id": area["id"], "code": _unique("OP")}
    )
    assert inactive.status_code == 409


def test_operation_rejects_non_positive_duration(client: TestClient) -> None:
    area = _create_area(client)
    for seconds in (0, -60):
        response = admin_of(client).post(
            "/api/operations",
            json={
                "area_id": area["id"],
                "code": _unique("OP"),
                "default_expected_duration": seconds,
            },
        )
        assert response.status_code == 422
        assert "must be positive" in response.json()["detail"]


def test_operation_area_binding_is_immutable(client: TestClient) -> None:
    operation = _create_operation(client)
    other_area = _create_area(client)

    response = admin_of(client).patch(
        f"/api/operations/{operation['id']}", json={"area_id": other_area["id"]}
    )
    assert response.status_code == 422


def test_operation_update_code_conflict_and_lifecycle(client: TestClient) -> None:
    area = _create_area(client)
    first = _create_operation(client, area_id=int(area["id"]))
    second = _create_operation(client, area_id=int(area["id"]))

    collision = admin_of(client).patch(
        f"/api/operations/{second['id']}", json={"code": first["code"]}
    )
    assert collision.status_code == 409

    updated = admin_of(client).patch(
        f"/api/operations/{second['id']}",
        json={"code": _unique("OP"), "is_external": True, "is_active": False},
    )
    assert updated.status_code == 200
    assert updated.json()["is_external"] is True
    assert updated.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Scan Stations
# ---------------------------------------------------------------------------


def test_scan_station_create_and_resolve_by_station_id(client: TestClient) -> None:
    station = _create_scan_station(client)

    resolved = client.get(f"/api/scan-stations/{station['station_id']}")
    assert resolved.status_code == 200
    assert resolved.json() == station

    listed = client.get("/api/scan-stations")
    assert listed.status_code == 200
    assert station["station_id"] in [row["station_id"] for row in listed.json()]

    unknown = client.get("/api/scan-stations/does-not-exist")
    assert unknown.status_code == 404


def test_scan_station_id_canonical_url_safe_form(client: TestClient) -> None:
    area = _create_area(client)

    trimmed = admin_of(client).post(
        "/api/scan-stations", json={"station_id": "  ST-TRIM-1  ", "area_id": area["id"]}
    )
    assert trimmed.status_code == 201
    assert trimmed.json()["station_id"] == "ST-TRIM-1"

    # The full URL-safe identifier charset is accepted — including
    # the intended floor naming style.
    for good_station_id in ("LATHE-ST-01", "st_0.9-A"):
        response = admin_of(client).post(
            "/api/scan-stations", json={"station_id": good_station_id, "area_id": area["id"]}
        )
        assert response.status_code == 201, response.text
        assert response.json()["station_id"] == good_station_id

    # Anything outside [A-Za-z0-9._-] is rejected and never
    # persisted: the Station ID is one URL path segment.
    for bad_station_id in ("ST 1", "   ", "ST/1", "ST?1", "ST#1", "ST:1", "ST%1", "STÄ-1"):
        response = admin_of(client).post(
            "/api/scan-stations", json={"station_id": bad_station_id, "area_id": area["id"]}
        )
        assert response.status_code == 422, bad_station_id
        assert (
            "letters, digits" in response.json()["detail"]
            or "must not be empty" in (response.json()["detail"])
        )

    listed = client.get("/api/scan-stations")
    station_ids = [row["station_id"] for row in listed.json()]
    assert "ST/1" not in station_ids and "ST 1" not in station_ids


def test_scan_station_duplicate_station_id_conflict(client: TestClient) -> None:
    station = _create_scan_station(client)
    other_area = _create_area(client)

    duplicate = admin_of(client).post(
        "/api/scan-stations",
        json={"station_id": station["station_id"], "area_id": other_area["id"]},
    )
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_scan_station_requires_existing_active_area(client: TestClient) -> None:
    missing = admin_of(client).post(
        "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": 999999}
    )
    assert missing.status_code == 422

    area = _create_area(client)
    assert (
        admin_of(client).patch(f"/api/areas/{area['id']}", json={"is_active": False}).status_code
        == 200
    )
    inactive = admin_of(client).post(
        "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area["id"]}
    )
    assert inactive.status_code == 409


def test_scan_station_rebind_and_active_toggle(client: TestClient) -> None:
    station = _create_scan_station(client)
    target_area = _create_area(client)
    inactive_area = _create_area(client)
    assert (
        admin_of(client)
        .patch(f"/api/areas/{inactive_area['id']}", json={"is_active": False})
        .status_code
        == 200
    )

    # Rebinding is an allowed configuration workflow — to an active
    # Area only. The Station ID itself is never renamed (no such
    # field), and the binding freeze is deliberately not a DB trigger.
    rebound = admin_of(client).patch(
        f"/api/scan-stations/{station['station_id']}", json={"area_id": target_area["id"]}
    )
    assert rebound.status_code == 200
    assert rebound.json()["area_id"] == target_area["id"]

    to_inactive = admin_of(client).patch(
        f"/api/scan-stations/{station['station_id']}", json={"area_id": inactive_area["id"]}
    )
    assert to_inactive.status_code == 409

    to_missing = admin_of(client).patch(
        f"/api/scan-stations/{station['station_id']}", json={"area_id": 999999}
    )
    assert to_missing.status_code == 422

    deactivated = admin_of(client).patch(
        f"/api/scan-stations/{station['station_id']}", json={"is_active": False}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    reactivated = admin_of(client).patch(
        f"/api/scan-stations/{station['station_id']}", json={"is_active": True}
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


# ---------------------------------------------------------------------------
# Barcode configuration — Machine Asset Tag format
# ---------------------------------------------------------------------------


def test_machine_asset_tag_format_singleton_lifecycle(
    client: TestClient, db_engine: Engine
) -> None:
    # Unconfigured deployment: nothing is seeded, and Machine creation
    # will require this to exist.
    unconfigured = client.get("/api/barcode-configuration/machine-asset-tag-format")
    assert unconfigured.status_code == 404
    assert "not configured" in unconfigured.json()["detail"]

    created = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "CD-", "digits": 4},
    )
    assert created.status_code == 200
    assert created.json()["prefix"] == "CD-"
    assert created.json()["digits"] == 4
    assert created.json()["next_sequence"] == 1

    fetched = client.get("/api/barcode-configuration/machine-asset-tag-format")
    assert fetched.status_code == 200
    assert fetched.json() == created.json()

    # Simulate allocations by the future Machine-creation workflow,
    # then verify a format change never resets the never-reuse counter.
    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.MachineAssetTagConfig)
            .where(models.MachineAssetTagConfig.id == 1)
            .values(next_sequence=7)
        )
    reformatted = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "MS-", "digits": 6},
    )
    assert reformatted.status_code == 200
    assert reformatted.json()["prefix"] == "MS-"
    assert reformatted.json()["digits"] == 6
    assert reformatted.json()["next_sequence"] == 7

    # Idempotent PUT: same values, still one configuration row.
    unchanged = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "MS-", "digits": 6},
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["next_sequence"] == 7


def test_machine_asset_tag_format_validation(client: TestClient) -> None:
    for invalid in (
        {"prefix": "CD ", "digits": 4},
        {"prefix": "CD:", "digits": 4},
        {"prefix": "CD-", "digits": 0},
        {"prefix": "CD-", "digits": 9},
    ):
        response = admin_of(client).put(
            "/api/barcode-configuration/machine-asset-tag-format", json=invalid
        )
        assert response.status_code == 422, invalid

    # The counter is server-owned: submitting it is a shape error, not
    # a silent ignore.
    counter_write = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "CD-", "digits": 4, "next_sequence": 99},
    )
    assert counter_write.status_code == 422

    # An empty prefix stays valid (numeric-only Asset Tags).
    empty_prefix = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format", json={"prefix": "", "digits": 4}
    )
    assert empty_prefix.status_code == 200
    assert empty_prefix.json()["prefix"] == ""
