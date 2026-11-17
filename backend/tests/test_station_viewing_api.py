"""Integration tests for viewing a Scan Station without an enrolled device.

Owner decision 2026-11-17: a signed-in User whose role holds Manage Scan
Stations may open any Scan Station in the standard mode in a browser
that is not an enrolled device and see what the station shows. Viewing
is read-only; every write still needs an enrolled device of that station
(OD-P6). Exercises the full request path — FastAPI routes, the
Application services and PostgreSQL — against a dedicated temporary
database migrated to head by the real Alembic chain:

- the station context and the Area inventory answer an enrolled device
  as before and, without a device token, a signed-in viewer; they refuse
  a signed-in User without the permission, one who must replace an
  administrator-set password first, and an anonymous caller with 401
  ``station_device_required``;
- a device token always decides: a refused token is never widened by a
  viewer's sign-in;
- the context answered to a viewer names the viewer, no device and no
  station permissions; the inventory needs the viewed station, bound to
  the Area now;
- every other station route refuses a viewer with 401
  ``station_device_required`` and writes nothing.
"""

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

from alembic import command
from app.api.route_access import ROUTE_ACCESS, Access
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    IdentityClient,
    admin_of,
    anonymous_client,
    client_as,
    enroll_station_device,
    station_device_client,
    station_device_headers,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_station_viewing_api"
_DB_URL_ENV = "DATABASE_URL"
_HEADER = "X-PartFlow-Station-Device"
_MSS = Permission.MANAGE_SCAN_STATIONS

_D1 = (
    "This device is not enrolled for this Scan Station, or its enrollment was revoked or"
    " replaced. Ask an administrator for an enrollment code."
)
_D2 = (
    "This device is enrolled for a different Scan Station. Enroll it for this station to continue."
)
_C1 = "This Scan Station's Area changed. The station reloads with its current Area."
_COUNTED = (
    "part_movements",
    "quantity_flows",
    "worker_sessions",
    "work_order_allocations",
    "scan_station_devices",
    "audit_events",
)


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
    admin_engine = create_engine(make_url(os.environ[_DB_URL_ENV]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    url = make_url(os.environ[_DB_URL_ENV]).set(database=_TEST_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    yield url
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    """The raw client: no station device and no sign-in."""
    original_url = os.environ[_DB_URL_ENV]
    os.environ[_DB_URL_ENV] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ[_DB_URL_ENV] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _refused_d1(response: Any) -> None:
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": _D1, "station_device_required": True}


class _Cell:
    """An Area with one Operation and one Scan Station."""

    def __init__(self, client: TestClient) -> None:
        admin = admin_of(client)
        department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        self.area_id = int(
            _ok(
                admin.post(
                    "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
                ),
                201,
            )["id"]
        )
        _ok(
            admin.post("/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}),
            201,
        )
        self.station_id = str(
            _ok(
                admin.post(
                    "/api/scan-stations",
                    json={"station_id": _unique("ST"), "area_id": self.area_id},
                ),
                201,
            )["station_id"]
        )


@pytest.fixture(scope="module")
def cell(client: TestClient) -> _Cell:
    return _Cell(client)


@pytest.fixture(scope="module")
def other(client: TestClient) -> _Cell:
    return _Cell(client)


@pytest.fixture(scope="module")
def viewer(client: TestClient) -> IdentityClient:
    """A signed-in User holding exactly Manage Scan Stations."""
    return client_as(client, _MSS)


def _counts(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        return {
            table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in _COUNTED
        }


# ---------------------------------------------------------------------------
# The station context
# ---------------------------------------------------------------------------


def test_the_context_answers_a_viewer_what_it_answers_the_device(
    client: TestClient, viewer: IdentityClient, cell: _Cell
) -> None:
    path = f"/api/scan-stations/{cell.station_id}/context"
    as_device = _ok(station_device_client(client).get(path))
    assert as_device["device"] is not None and as_device["viewer"] is None
    assert as_device["station_permissions"] != []

    as_viewer = _ok(viewer.get(path))
    identity = viewer.identity
    assert identity is not None
    assert as_viewer["device"] is None
    assert as_viewer["viewer"] == {
        "id": identity.user_id,
        "name": f"Test {identity.login_name[5:]}",
    }
    assert as_viewer["station_permissions"] == []
    shared = set(as_device) - {"device", "viewer", "station_permissions"}
    assert {key: as_viewer[key] for key in shared} == {key: as_device[key] for key in shared}


def test_the_context_refuses_everyone_else_as_today(client: TestClient, cell: _Cell) -> None:
    path = f"/api/scan-stations/{cell.station_id}/context"
    for caller in (
        anonymous_client(client),
        client_as(client, Permission.VIEW_PRODUCTION_DATA),
        client_as(client, _MSS, temporary_password=True),
        client_as(client, _MSS, active=False),
    ):
        _refused_d1(caller.get(path))


def test_the_context_of_an_unknown_station_is_404_for_a_viewer(viewer: TestClient) -> None:
    response = viewer.get("/api/scan-stations/NO-SUCH-STATION/context")
    assert response.status_code == 404, response.text


def test_a_device_token_always_decides(
    client: TestClient, viewer: TestClient, db_engine: Engine, cell: _Cell, other: _Cell
) -> None:
    """A refused token is never widened by the viewer's sign-in; a blank
    header carries no token."""
    path = f"/api/scan-stations/{cell.station_id}/context"
    _refused_d1(viewer.get(path, headers={_HEADER: "x" * 43}))
    mismatch = viewer.get(
        path, headers=station_device_headers(enroll_station_device(db_engine, other.station_id))
    )
    assert mismatch.status_code == 403, mismatch.text
    assert mismatch.json() == {"detail": _D2, "station_device_mismatch": True}
    assert _ok(viewer.get(path, headers={_HEADER: " "}))["viewer"] is not None
    # A valid device with a viewer's sign-in is answered as the device.
    token = enroll_station_device(db_engine, cell.station_id)
    answered = _ok(viewer.get(path, headers=station_device_headers(token)))
    assert answered["device"] is not None and answered["viewer"] is None


# ---------------------------------------------------------------------------
# The Area inventory
# ---------------------------------------------------------------------------


def test_the_inventory_answers_a_viewer_of_the_bound_station(
    client: TestClient, viewer: TestClient, cell: _Cell, other: _Cell
) -> None:
    path = f"/api/areas/{cell.area_id}/inventory"
    as_device = _ok(station_device_client(client).get(path))
    as_viewer = _ok(viewer.get(path, params={"station_id": cell.station_id}))
    assert as_viewer == as_device

    missing = viewer.get(path)
    assert missing.status_code == 422, missing.text
    for station_id in (other.station_id, "NO-SUCH-STATION"):
        stale = viewer.get(path, params={"station_id": station_id})
        assert stale.status_code == 409, stale.text
        assert stale.json() == {"detail": _C1, "station_context_changed": True}


def test_the_inventory_refuses_everyone_else_as_today(client: TestClient, cell: _Cell) -> None:
    path = f"/api/areas/{cell.area_id}/inventory"
    for caller in (
        anonymous_client(client),
        client_as(client, Permission.VIEW_PRODUCTION_DATA),
        client_as(client, _MSS, temporary_password=True),
    ):
        _refused_d1(caller.get(path, params={"station_id": cell.station_id}))


def test_the_inventory_ignores_station_id_for_a_device(
    client: TestClient, db_engine: Engine, cell: _Cell, other: _Cell
) -> None:
    """A device's own station decides; a named station never widens it."""
    device = station_device_client(client)
    path = f"/api/areas/{other.area_id}/inventory"
    _ok(device.get(path, params={"station_id": cell.station_id}))
    token = station_device_headers(enroll_station_device(db_engine, cell.station_id))
    stale = client.get(path, params={"station_id": other.station_id}, headers=token)
    assert stale.status_code == 409 and stale.json()["station_context_changed"] is True


# ---------------------------------------------------------------------------
# Every other station route stays device-only
# ---------------------------------------------------------------------------


def _device_only_requests(cell: _Cell) -> dict[tuple[str, str], tuple[str, str, dict[str, Any]]]:
    base = "/api/scan-stations/{station_id}"
    at = f"/api/scan-stations/{cell.station_id}"
    event = str(uuid.uuid4())
    commands = (
        "badge-scans",
        "scans/resolve",
        "machine-scans/resolve",
        "transfers",
        "stockings",
        "machine-assignments",
        "machine-releases",
        "area-completions",
        "merges",
        "scraps",
        "quantity-additions",
        "receipts",
        "undos",
    )
    requests: dict[tuple[str, str], tuple[str, str, dict[str, Any]]] = {
        ("POST", f"{base}/{name}"): ("POST", f"{at}/{name}", {"json": {}}) for name in commands
    }
    requests[("PUT", f"{base}/theme-preference")] = (
        "PUT",
        f"{at}/theme-preference",
        {"json": {"theme_preference": "LIGHT"}},
    )
    requests[("GET", f"{base}/undo-preview/{{device_event_id}}")] = (
        "GET",
        f"{at}/undo-preview/{event}",
        {},
    )
    requests[("GET", "/api/allocations/suggestion")] = (
        "GET",
        "/api/allocations/suggestion",
        {"params": {"part_number": "PN-1", "quantity": 1}},
    )
    requests[("POST", "/api/allocations")] = ("POST", "/api/allocations", {"json": {}})
    return requests


def test_every_other_station_route_refuses_a_viewer(
    client: TestClient, db_engine: Engine, cell: _Cell
) -> None:
    """Writes stay device-only (OD-P6): even an all-permission viewer is
    refused with 401 D-1 and nothing is written."""
    requests = _device_only_requests(cell)
    device_only = {
        key
        for key, access in ROUTE_ACCESS.items()
        if access.access is Access.STATION and not access.station_viewer
    }
    assert set(requests) == device_only
    admin = admin_of(client)
    for key, (method, path, kwargs) in requests.items():
        before = _counts(db_engine)
        response = admin.request(method, path, **kwargs)
        assert response.status_code == 401, (key, response.text)
        assert response.json() == {"detail": _D1, "station_device_required": True}, key
        assert _counts(db_engine) == before, key
