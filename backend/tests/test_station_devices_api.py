"""Integration tests for Phase 14 slice 4 — enrolled Scan Station devices.

Exercises the full request path — FastAPI routes, the Application
services and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain (owner decisions OD-P6,
OD-S4-1, OD-S4-9):

- every ``STATION`` route needs an enrolled, unrevoked device of the
  addressed station (401 ``station_device_required`` / 403
  ``station_device_mismatch``; the inventory 409 ``station_context_changed``
  for an Area the station left), with nothing written but the device's
  last-seen time — and never resolves the User principal (SD-1, SD-2,
  SD-9, SD-29, SD-31);
- enrollment: issue → activate → list, the refusals, the secrets shown
  once and stored only as digests, revocation, re-enrollment, the audit
  rows, the enrollment guard judged under the User-administration lock,
  last seen and per-request revocation (SD-3–SD-8, SD-20–SD-24);
- the keys of the role applied at Scan Stations gate each command kind
  after the idempotency re-check (replays survive a revocation and a
  device replacement; an in-flight retry replays, never K-1) and each
  read first; the station allocation's adjustment check (SD-10–SD-16,
  SD-30);
- the Roles answer marks the role applied at Scan Stations (SD-25);
- concurrent activations and revocations serialize on the device rows
  (SD-17–SD-19).

"Zero writes" means unchanged counts of Movements, flows, Worker
Sessions (rows and expiry), allocations, devices and audit rows — the
device's ``last_seen_at`` is the one exception, asserted where a case
says so. Grants of the station role are revoked only inside a case and
restored in ``finally``. The module database is dropped afterwards.
"""

import contextlib
import datetime
import hashlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.api.route_access import ROUTE_ACCESS, Access
from app.application import (
    allocations,
    direct_processing,
    intake,
    machine_processing,
    merges,
    quantity_events,
    station_access,
    station_devices,
    station_identity,
    transfers,
    undo,
)
from app.application.errors import (
    ConflictError,
    EnrollmentCodeInvalidError,
    StationPermissionDeniedError,
)
from app.core.config import get_settings
from app.domain.enums import Permission, StationCommand
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    admin_of,
    anonymous_client,
    client_as,
    enroll_station_device,
    station_device_client,
    station_device_headers,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_station_devices_api"
_DB_URL_ENV = "DATABASE_URL"
_HEADER = "X-PartFlow-Station-Device"

_D1 = (
    "This device is not enrolled for this Scan Station, or its enrollment was revoked or"
    " replaced. Ask an administrator for an enrollment code."
)
_D2 = (
    "This device is enrolled for a different Scan Station. Enroll it for this station to continue."
)
_C1 = "This Scan Station's Area changed. The station reloads with its current Area."
_C2 = (
    "The suggested allocation changed since it was shown, and Scan Stations are not allowed"
    " to adjust suggested allocations. Nothing was allocated."
)
_E2 = "Enter a device name of 1 to 80 characters."
_G4 = (
    "Enrolling a device gives it the permissions of the role applied at Scan Stations, which"
    " include correction permissions. It needs the Manage correction permissions permission."
)
_SNAPSHOT_KEYS = {
    "station_id",
    "label",
    "state",
    "enrollment_expires_at",
    "activated_at",
    "revoked_at",
    "revoked_reason",
    "replaces_device_id",
}
_MSS = Permission.MANAGE_SCAN_STATIONS
_MCP = Permission.MANAGE_CORRECTION_PERMISSIONS
_MUAR = Permission.MANAGE_USERS_AND_ROLES


def _e1(station_id: str) -> str:
    return (
        f"This enrollment code is not valid for Scan Station {station_id}. It may have"
        " expired (codes last 15 minutes), been used already, or been issued for another"
        " station. Ask an administrator for a new code."
    )


def _k1(action: str) -> str:
    return (
        f"Scan Stations are not allowed to {action}. An administrator can grant it to the"
        " role applied at Scan Stations. Nothing was recorded."
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
    """The raw client: no station device unless a case adds one."""
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
def station(client: TestClient) -> TestClient:
    """The station client: an enrolled device of every addressed station."""
    return station_device_client(client)


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _event() -> str:
    return str(uuid.uuid4())


class _Cell:
    """An Area with one Operation, one Scan Station and ``machine_count`` Machines."""

    def __init__(
        self, client: TestClient, *, machine_count: int = 0, is_terminal: bool = False
    ) -> None:
        admin = admin_of(client)
        department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        area_body: dict[str, Any] = {"department_id": department["id"], "name": _unique("AREA")}
        if is_terminal:
            area_body["is_terminal"] = True
        self.area_id = int(_ok(admin.post("/api/areas", json=area_body), 201)["id"])
        self.operation_id = int(
            _ok(
                admin.post(
                    "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
                ),
                201,
            )["id"]
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
        self.machine_ids = [
            int(
                _ok(
                    admin.post(
                        "/api/machines", json={"area_id": self.area_id, "name": _unique("M")}
                    ),
                    201,
                )["id"]
            )
            for _ in range(machine_count)
        ]

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    _ok(
        admin_of(client).put(
            "/api/barcode-configuration/machine-asset-tag-format",
            json={"prefix": "SD-", "digits": 4},
        )
    )


def _work_order(
    client: TestClient, pn: str, requested: int, received_date: str | None = None
) -> tuple[int, int]:
    payload: dict[str, Any] = {"lines": [{"part_number": pn, "requested_quantity": requested}]}
    if received_date is not None:
        payload["received_date"] = received_date
    body = _ok(admin_of(client).post("/api/work-orders", json=payload), 201)
    return int(body["id"]), int(body["demands"][0]["id"])


def _release(
    client: TestClient,
    cell: _Cell,
    *,
    quantity: int = 10,
    pn: str | None = None,
    received_date: str | None = None,
) -> tuple[int, str]:
    part_number = pn or _unique("PN")
    work_order_id, demand_id = _work_order(client, part_number, 500, received_date)
    released = _ok(
        admin_of(client).post(
            f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
            json={
                "part_number": part_number,
                "quantity": quantity,
                "route_mode": "FLOATING",
                "starting_area_id": cell.area_id,
                "operation_id": cell.operation_id,
                "confirm_active_quantity": pn is not None,
                "device_event_id": _event(),
            },
        ),
        201,
    )
    return int(released["quantity_flow_id"]), part_number


def _transfer_body(
    source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int, event: str | None = None
) -> dict[str, Any]:
    return {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": event or _event(),
    }


def _in_area_body(
    flow_id: int, pn: str, quantity: int, machine_id: int | None = None, event: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "quantity": quantity,
        "device_event_id": event or _event(),
    }
    if machine_id is not None:
        body["machine_id"] = machine_id
    return body


def _receipt_body(pn: str, quantity: int, event: str | None = None) -> dict[str, Any]:
    return {
        "part_number": pn,
        "quantity": quantity,
        "request_type": "MODIFY",
        "route_mode": "FLOATING",
        "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "device_event_id": event or _event(),
    }


class _Stock(NamedTuple):
    material: _Cell
    stockroom: _Cell
    pn: str
    demand_id: int
    supply_demand_id: int


def _stocked(client: TestClient, station: TestClient, *, requested: int = 5) -> _Stock:
    """``requested`` demanded on an early Work Order; 8 pcs stocked through a
    late supply Work Order (whose own demand sorts last)."""
    material = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    _, demand_id = _work_order(client, pn, requested, "2000-01-01")
    supply_id, supply_demand_id = _work_order(client, pn, 8, "2099-12-31")
    released = _ok(
        admin_of(client).post(
            f"/api/work-orders/{supply_id}/demands/{supply_demand_id}/release",
            json={
                "part_number": pn,
                "quantity": 8,
                "route_mode": "FLOATING",
                "starting_area_id": material.area_id,
                "operation_id": material.operation_id,
                "confirm_active_quantity": True,
                "device_event_id": _event(),
            },
        ),
        201,
    )
    _ok(
        station.post(
            f"/api/scan-stations/{stockroom.station_id}/stockings",
            json=_transfer_body(material, stockroom, int(released["quantity_flow_id"]), pn, 8),
        ),
        201,
    )
    return _Stock(material, stockroom, pn, demand_id, supply_demand_id)


def _allocation_body(
    stock: _Stock, lines: list[tuple[int, int]], event: str | None = None, **kw: Any
) -> dict[str, Any]:
    return {
        "part_number": stock.pn,
        "allocation_quantity": sum(quantity for _, quantity in lines),
        "lines": [{"work_order_demand_id": d, "quantity": q} for d, q in lines],
        "station_id": stock.stockroom.station_id,
        "device_event_id": event or _event(),
        **kw,
    }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _scalar(engine: Engine, statement: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(statement), params).scalar()


def _station_role_id(engine: Engine) -> int:
    return int(_scalar(engine, "SELECT scan_station_role_id FROM application_policy"))


@contextlib.contextmanager
def _revoked(engine: Engine, *keys: Permission) -> Iterator[None]:
    """The station role without ``keys`` for the block (direct SQL), restored after."""
    role_id = _station_role_id(engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :r AND permission = ANY(:p)"),
            {"r": role_id, "p": [key.value for key in keys]},
        )
    try:
        yield
    finally:
        _grant(engine, *keys)


def _grant(engine: Engine, *keys: Permission) -> None:
    with engine.begin() as connection:
        for key in keys:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission) VALUES (:r, :p)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"r": _station_role_id(engine), "p": key.value},
            )


_COUNTED = (
    "part_movements",
    "quantity_flows",
    "worker_sessions",
    "work_order_allocations",
    "scan_station_devices",
    "audit_events",
)


def _counts(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        counts: dict[str, Any] = {
            table: connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in _COUNTED
        }
        # The station client enrolls its harness devices lazily (label
        # "test device", inserted directly): only the other rows count.
        counts["scan_station_devices"] = connection.execute(
            sa.text("SELECT count(*) FROM scan_station_devices WHERE label <> 'test device'")
        ).scalar_one()
        counts["worker_session_expiry"] = connection.execute(
            sa.text("SELECT array_agg(expires_at ORDER BY id) FROM worker_sessions")
        ).scalar()
        counts["hot_ranks"] = connection.execute(
            sa.text(
                "SELECT array_agg(priority_rank ORDER BY id) FROM work_order_demands"
                " WHERE priority_rank IS NOT NULL"
            )
        ).scalar()
    return counts


def _device_rows(engine: Engine, *, with_last_seen: bool = True) -> list[tuple[Any, ...]]:
    columns = (
        "id, station_id, label, enrollment_code_digest, enrollment_expires_at, token_digest,"
        " replaces_device_id, issued_at, activated_at, revoked_at, revoked_reason"
    )
    if with_last_seen:
        columns += ", last_seen_at"
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text(f"SELECT {columns} FROM scan_station_devices ORDER BY id")
            )
        ]


def _device(engine: Engine, device_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT * FROM scan_station_devices WHERE id = :id"), {"id": device_id}
        ).one()


def _device_id_of(engine: Engine, token: str) -> int:
    digest = hashlib.sha256(token.encode("ascii")).digest()
    return int(
        _scalar(engine, "SELECT id FROM scan_station_devices WHERE token_digest = :d", d=digest)
    )


def _headers(engine: Engine, station_id: str) -> dict[str, str]:
    return station_device_headers(enroll_station_device(engine, station_id))


def _audits(engine: Engine, device_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT * FROM audit_events WHERE entity_type = 'ScanStationDevice'"
                    " AND entity_id = :id ORDER BY id"
                ),
                {"id": str(device_id)},
            ).mappings()
        )


# ---------------------------------------------------------------------------
# Enrollment helpers
# ---------------------------------------------------------------------------


def _enroller(client: TestClient) -> TestClient:
    return client_as(client, _MSS, _MCP)


def _issue(caller: TestClient, station_id: str, *, label: str = "Line terminal", **kw: Any) -> Any:
    return caller.post(
        f"/api/scan-stations/{station_id}/device-enrollments", json={"label": label, **kw}
    )


def _activate(client: TestClient, station_id: str, code: str) -> Any:
    return client.post(
        f"/api/scan-stations/{station_id}/device-activations", json={"enrollment_code": code}
    )


def _enroll(client: TestClient, station_id: str, **kw: Any) -> tuple[int, str]:
    """Issue and activate through the API: the device id and its token."""
    issued = _ok(_issue(_enroller(client), station_id, **kw), 201)
    activated = _ok(_activate(client, station_id, issued["enrollment_code"]), 201)
    return int(activated["device"]["id"]), str(activated["device_token"])


# ---------------------------------------------------------------------------
# Every STATION route with a valid request (SD-1, SD-2, SD-9)
# ---------------------------------------------------------------------------


class _Request(NamedTuple):
    key: tuple[str, str]
    method: str
    path: str
    kwargs: dict[str, Any]


class _Shop(NamedTuple):
    cell: _Cell
    machines: _Cell
    stock: _Stock
    flow_id: int
    pn: str
    transfer_event: str


@pytest.fixture(scope="module")
def shop(client: TestClient, station: TestClient) -> _Shop:
    cell = _Cell(client)
    machines = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, machines, quantity=6)
    transfer_event = _event()
    moved = station.post(
        f"/api/scan-stations/{cell.station_id}/transfers",
        json=_transfer_body(machines, cell, flow_id, pn, 1, transfer_event),
    )
    _ok(moved, 201)
    return _Shop(cell, machines, _stocked(client, station), flow_id, pn, transfer_event)


def _station_requests(shop: _Shop) -> list[_Request]:
    cell, machines, stock = shop.cell, shop.machines, shop.stock
    base = "/api/scan-stations/{station_id}"
    at_cell = f"/api/scan-stations/{cell.station_id}"
    at_machines = f"/api/scan-stations/{machines.station_id}"
    flow_body = _in_area_body(shop.flow_id, shop.pn, 1, machines.machine_id)
    requests = [
        _Request(("GET", f"{base}/context"), "GET", f"{at_cell}/context", {}),
        _Request(
            ("PUT", f"{base}/theme-preference"),
            "PUT",
            f"{at_cell}/theme-preference",
            {"json": {"theme_preference": "LIGHT"}},
        ),
        _Request(
            ("POST", f"{base}/badge-scans"),
            "POST",
            f"{at_cell}/badge-scans",
            {"json": {"badge": "NOBODY"}},
        ),
        _Request(
            ("POST", f"{base}/scans/resolve"),
            "POST",
            f"{at_cell}/scans/resolve",
            {"json": {"part_number": shop.pn}},
        ),
        _Request(
            ("POST", f"{base}/machine-scans/resolve"),
            "POST",
            f"{at_machines}/machine-scans/resolve",
            {"json": {"asset_tag": "SD-0001"}},
        ),
        _Request(
            ("POST", f"{base}/transfers"),
            "POST",
            f"{at_cell}/transfers",
            {"json": _transfer_body(machines, cell, shop.flow_id, shop.pn, 1)},
        ),
        _Request(
            ("POST", f"{base}/stockings"),
            "POST",
            f"/api/scan-stations/{stock.stockroom.station_id}/stockings",
            {"json": _transfer_body(machines, stock.stockroom, shop.flow_id, shop.pn, 1)},
        ),
        _Request(
            ("POST", f"{base}/machine-assignments"),
            "POST",
            f"{at_machines}/machine-assignments",
            {"json": flow_body},
        ),
        _Request(
            ("POST", f"{base}/machine-releases"),
            "POST",
            f"{at_machines}/machine-releases",
            {"json": flow_body},
        ),
        _Request(
            ("POST", f"{base}/area-completions"),
            "POST",
            f"{at_machines}/area-completions",
            {"json": flow_body},
        ),
        _Request(
            ("POST", f"{base}/merges"),
            "POST",
            f"{at_machines}/merges",
            {
                "json": {
                    "part_number": shop.pn,
                    "quantity_flow_ids": [shop.flow_id, shop.flow_id + 1],
                    "device_event_id": _event(),
                }
            },
        ),
        _Request(
            ("POST", f"{base}/scraps"),
            "POST",
            f"{at_machines}/scraps",
            {
                "json": {
                    "part_number": shop.pn,
                    "quantity_flow_id": shop.flow_id,
                    "quantity": 1,
                    "reason": "Damaged",
                    "device_event_id": _event(),
                }
            },
        ),
        _Request(
            ("POST", f"{base}/quantity-additions"),
            "POST",
            f"{at_machines}/quantity-additions",
            {
                "json": {
                    "part_number": shop.pn,
                    "quantity": 1,
                    "reason": "Found",
                    "device_event_id": _event(),
                }
            },
        ),
        _Request(
            ("POST", f"{base}/receipts"),
            "POST",
            f"{at_cell}/receipts",
            {"json": _receipt_body(_unique("PN"), 2)},
        ),
        _Request(
            ("GET", f"{base}/undo-preview/{{device_event_id}}"),
            "GET",
            f"{at_cell}/undo-preview/{shop.transfer_event}",
            {},
        ),
        _Request(
            ("POST", f"{base}/undos"),
            "POST",
            f"{at_cell}/undos",
            {
                "json": {
                    "part_number": shop.pn,
                    "reverses_device_event_id": shop.transfer_event,
                    "device_event_id": _event(),
                }
            },
        ),
        _Request(
            ("GET", "/api/areas/{area_id}/inventory"),
            "GET",
            f"/api/areas/{cell.area_id}/inventory",
            {},
        ),
        _Request(
            ("GET", "/api/allocations/suggestion"),
            "GET",
            "/api/allocations/suggestion",
            {"params": {"part_number": stock.pn, "quantity": 1}},
        ),
        _Request(
            ("POST", "/api/allocations"),
            "POST",
            "/api/allocations",
            {"json": _allocation_body(stock, [(stock.demand_id, 1)])},
        ),
    ]
    return requests


def _station_routes() -> set[tuple[str, str]]:
    return {key for key, access in ROUTE_ACCESS.items() if access.access is Access.STATION}


def test_the_request_list_covers_every_station_route(shop: _Shop) -> None:
    assert {request.key for request in _station_requests(shop)} == _station_routes()


_MALFORMED: dict[str, str | bytes | None] = {
    "absent": None,
    "empty": "",
    "too long": "x" * 200,
    "non-ascii": "tokén".encode(),
}


@pytest.mark.parametrize("header", sorted(_MALFORMED))
def test_every_station_route_needs_a_device(
    client: TestClient, db_engine: Engine, shop: _Shop, header: str
) -> None:
    """SD-1: no (or a malformed) device header → 401 D-1, nothing written —
    every device row identical, ``last_seen_at`` included."""
    value = _MALFORMED[header]
    for request in _station_requests(shop):
        before, devices = _counts(db_engine), _device_rows(db_engine)
        headers = {} if value is None else {_HEADER: value}
        response = client.request(request.method, request.path, headers=headers, **request.kwargs)
        assert response.status_code == 401, (request.key, response.text)
        assert response.json() == {"detail": _D1, "station_device_required": True}
        assert "set-cookie" not in response.headers
        assert _counts(db_engine) == before, request.key
        assert _device_rows(db_engine) == devices, request.key


def test_a_device_of_another_station_is_refused(
    client: TestClient, station: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """SD-2 (+ SD-31): another station's device → 403 D-2 with nothing
    written but its last-seen time; the inventory of an Area its station is
    not bound to → 409 C-1 until the station is rebound there."""
    other = _Cell(client)
    token = enroll_station_device(db_engine, other.station_id)
    device_id = _device_id_of(db_engine, token)
    headers = station_device_headers(token)
    for request in _station_requests(shop):
        if request.key[1].startswith("/api/scan-stations/") or request.key == (
            "POST",
            "/api/allocations",
        ):
            before = _counts(db_engine)
            response = client.request(
                request.method, request.path, headers=headers, **request.kwargs
            )
            assert response.status_code == 403, (request.key, response.text)
            assert response.json() == {"detail": _D2, "station_device_mismatch": True}
            assert _counts(db_engine) == before, request.key
    assert _device(db_engine, device_id).last_seen_at is not None
    assert _device(db_engine, device_id).revoked_at is None

    inventory = client.get(f"/api/areas/{shop.cell.area_id}/inventory", headers=headers)
    assert inventory.status_code == 409, inventory.text
    assert inventory.json() == {"detail": _C1, "station_context_changed": True}
    _ok(
        admin_of(client).patch(
            f"/api/scan-stations/{other.station_id}", json={"area_id": shop.cell.area_id}
        )
    )
    assert _device(db_engine, device_id).revoked_at is None
    rebound = _ok(client.get(f"/api/areas/{shop.cell.area_id}/inventory", headers=headers))
    assert rebound["area"]["id"] == shop.cell.area_id
    stale = client.get(f"/api/areas/{other.area_id}/inventory", headers=headers)
    assert stale.status_code == 409 and stale.json()["station_context_changed"] is True


def test_an_unknown_station_answers_the_device_check_first(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """SD-29."""
    headers = _headers(db_engine, shop.cell.station_id)
    path = "/api/scan-stations/NO-SUCH-STATION/context"
    mismatch = client.get(path, headers=headers)
    assert mismatch.status_code == 403 and mismatch.json()["station_device_mismatch"] is True
    missing = client.get(path)
    assert missing.status_code == 401 and missing.json()["station_device_required"] is True


def test_a_user_sign_in_neither_widens_nor_narrows_a_station(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """SD-9 (PLAN CD8): a User holding every key without a device → 401 on
    every station route; a device with a User holding no key → answered."""
    admin = admin_of(client)
    for request in _station_requests(shop):
        response = admin.request(request.method, request.path, **request.kwargs)
        assert response.status_code == 401, (request.key, response.text)
        assert response.json()["station_device_required"] is True
        assert "authentication_required" not in response.json()
    keyless = station_device_client(client_as(client))
    context = _ok(keyless.get(f"/api/scan-stations/{shop.cell.station_id}/context"))
    assert context["station_id"] == shop.cell.station_id
    resolved = keyless.post(
        f"/api/scan-stations/{shop.cell.station_id}/scans/resolve",
        json={"part_number": shop.pn},
    )
    assert resolved.status_code == 200, resolved.text


# ---------------------------------------------------------------------------
# Enrollment (SD-3 – SD-8, SD-21 – SD-24)
# ---------------------------------------------------------------------------


def test_issue_activate_and_list(client: TestClient, db_engine: Engine) -> None:
    """SD-3."""
    cell = _Cell(client)
    issued = _ok(_issue(_enroller(client), cell.station_id, label="  Line 3 desk  "), 201)
    code = issued["enrollment_code"]
    assert len(code) == 11 and code[5] == "-"
    device = issued["device"]
    assert (device["station_id"], device["label"], device["state"]) == (
        cell.station_id,
        "Line 3 desk",
        "PENDING",
    )
    assert device["activated_at"] is None and device["replaces_device_id"] is None
    typed = f" {code[:5].lower()} {code[6:].lower()} "
    activated = _ok(_activate(client, cell.station_id, typed), 201)
    token = activated["device_token"]
    assert len(token) == 43
    assert activated["device"]["id"] == device["id"]
    assert activated["device"]["label"] == "Line 3 desk"
    context = _ok(
        client.get(
            f"/api/scan-stations/{cell.station_id}/context", headers=station_device_headers(token)
        )
    )
    assert context["device"] == {"id": device["id"], "label": "Line 3 desk"}
    assert context["station_permissions"] == sorted(
        key.value for key in station_access.STATION_PERMISSIONS
    )
    listed = _ok(admin_of(client).get("/api/scan-station-devices"))
    mine = [row for row in listed["devices"] if row["id"] == device["id"]]
    assert len(mine) == 1 and mine[0]["state"] == "ACTIVE"
    assert listed["enrollment_permissions"] == [_MCP.value, _MSS.value]


def test_activation_refusals_change_nothing(client: TestClient, db_engine: Engine) -> None:
    """SD-4: one generic E-1 for every reason, the device row unchanged."""
    cell, other = _Cell(client), _Cell(client)
    enroller = _enroller(client)
    pending = _ok(_issue(enroller, cell.station_id), 201)
    code = pending["enrollment_code"]

    def refused(station_id: str, value: str) -> None:
        rows = _device_rows(db_engine)
        response = _activate(client, station_id, value)
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": _e1(station_id), "enrollment_code_invalid": True}
        assert _device_rows(db_engine) == rows

    refused(cell.station_id, "ZZZZZ-ZZZZZ")
    refused(other.station_id, code)
    refused(cell.station_id, code[:9])
    expired = _ok(_issue(enroller, cell.station_id), 201)
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE scan_station_devices SET enrollment_expires_at = now() - interval '1 s'"
                " WHERE id = :id"
            ),
            {"id": expired["device"]["id"]},
        )
    refused(cell.station_id, expired["enrollment_code"])
    revoked = _ok(_issue(enroller, cell.station_id), 201)
    _ok(admin_of(client).post(f"/api/scan-station-devices/{revoked['device']['id']}/revocation"))
    refused(cell.station_id, revoked["enrollment_code"])
    _ok(_activate(client, cell.station_id, code), 201)
    refused(cell.station_id, code)


def test_codes_and_tokens_leave_the_server_once(
    client: TestClient, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """SD-5 (+ SD-6: digests only; the code's digest cleared on activation)."""
    cell = _Cell(client)
    caplog.set_level(logging.DEBUG)
    issued = _issue(_enroller(client), cell.station_id)
    assert issued.status_code == 201 and issued.headers["cache-control"] == "no-store"
    code = issued.json()["enrollment_code"]
    activated = _activate(client, cell.station_id, code)
    assert activated.status_code == 201 and activated.headers["cache-control"] == "no-store"
    token = activated.json()["device_token"]
    device_id = activated.json()["device"]["id"]
    headers = station_device_headers(token)
    context = client.get(f"/api/scan-stations/{cell.station_id}/context", headers=headers)
    listed = admin_of(client).get("/api/scan-station-devices")
    canonical = code.replace("-", "")
    code_digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    secrets_ = (code, canonical, token, code_digest, token_digest)
    with db_engine.connect() as connection:
        audit_text = " ".join(
            str(row)
            for row in connection.execute(
                sa.text(
                    "SELECT before_data::text, after_data::text, metadata::text FROM audit_events"
                    " WHERE entity_type = 'ScanStationDevice'"
                )
            )
        )
    for secret in secrets_:
        assert secret not in context.text and secret not in listed.text
        assert secret not in audit_text
        assert secret not in caplog.text
    assert token not in issued.text and code not in activated.text
    row = _device(db_engine, device_id)
    assert bytes(row.token_digest) == hashlib.sha256(token.encode("ascii")).digest()
    assert row.enrollment_code_digest is None


def test_the_device_checks_refuse_malformed_rows(client: TestClient, db_engine: Engine) -> None:
    """SD-6: both secrets, or neither, are refused by the database."""
    cell = _Cell(client)
    with db_engine.connect() as connection:
        transaction = connection.begin()
        try:
            for code, token, activated in (
                (b"\x01" * 32, b"\x02" * 32, "now()"),
                ("NULL", "NULL", "NULL"),
            ):
                savepoint = connection.begin_nested()
                with pytest.raises(sa.exc.IntegrityError) as raised:
                    connection.execute(
                        sa.text(
                            "INSERT INTO scan_station_devices (station_id, label,"
                            " enrollment_expires_at, enrollment_code_digest, token_digest,"
                            f" activated_at) VALUES (:s, 'x', now(), :c, :t, {activated})"
                        ),
                        {
                            "s": cell.station_id,
                            "c": None if code == "NULL" else code,
                            "t": None if token == "NULL" else token,
                        },
                    )
                savepoint.rollback()
                assert "ck_scan_station_devices_code_or_token" in str(raised.value.orig)
        finally:
            transaction.rollback()


def test_revocation(client: TestClient, db_engine: Engine) -> None:
    """SD-7 (+ SD-23: a revocation applies from the next request)."""
    cell = _Cell(client)
    device_id, token = _enroll(client, cell.station_id)
    headers = station_device_headers(token)
    path = f"/api/scan-stations/{cell.station_id}/context"
    _ok(client.get(path, headers=headers))
    revoked = _ok(admin_of(client).post(f"/api/scan-station-devices/{device_id}/revocation"))
    assert (revoked["state"], revoked["revoked_reason"]) == ("REVOKED", "REVOKED")
    refused = client.get(path, headers=headers)
    assert refused.status_code == 401 and refused.json()["station_device_required"] is True
    audits = len(_audits(db_engine, device_id))
    again = _ok(admin_of(client).post(f"/api/scan-station-devices/{device_id}/revocation"))
    assert again == revoked
    assert len(_audits(db_engine, device_id)) == audits
    pending = _ok(_issue(_enroller(client), cell.station_id), 201)
    _ok(admin_of(client).post(f"/api/scan-station-devices/{pending['device']['id']}/revocation"))
    late = _activate(client, cell.station_id, pending["enrollment_code"])
    assert late.status_code == 403 and late.json()["enrollment_code_invalid"] is True
    unknown = admin_of(client).post("/api/scan-station-devices/999999999/revocation")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Scan Station device 999999999 does not exist."}


def test_re_enrollment_replaces_the_device_at_activation(
    client: TestClient, db_engine: Engine
) -> None:
    """SD-8."""
    cell, other = _Cell(client), _Cell(client)
    old_id, old_token = _enroll(client, cell.station_id)
    path = f"/api/scan-stations/{cell.station_id}/context"
    enroller = _enroller(client)
    issued = _ok(_issue(enroller, cell.station_id, replaces_device_id=old_id), 201)
    assert issued["device"]["replaces_device_id"] == old_id
    _ok(client.get(path, headers=station_device_headers(old_token)))
    activated = _ok(_activate(client, cell.station_id, issued["enrollment_code"]), 201)
    old = _device(db_engine, old_id)
    assert (old.revoked_reason, old.revoked_at is not None) == ("REPLACED", True)
    refused = client.get(path, headers=station_device_headers(old_token))
    assert refused.status_code == 401
    _ok(client.get(path, headers=station_device_headers(activated["device_token"])))
    replaced_audit = _audits(db_engine, old_id)[-1]
    assert replaced_audit["actor_user_id"] is None
    assert replaced_audit["metadata"] == {
        "source": "station-activation",
        "replaced_by_device_id": activated["device"]["id"],
    }
    assert replaced_audit["after_data"]["state"] == "REVOKED"

    other_id, _ = _enroll(client, other.station_id)
    pending = _ok(_issue(enroller, cell.station_id), 201)
    for replaces in (other_id, pending["device"]["id"], old_id, 999_999_999):
        before = _counts(db_engine)
        response = _issue(enroller, cell.station_id, replaces_device_id=replaces)
        assert response.status_code == 409, response.text
        assert response.json() == {
            "detail": f"Only an enrolled device of Scan Station {cell.station_id} can be"
            " re-enrolled. Reload the device list and try again."
        }
        assert _counts(db_engine) == before


@pytest.mark.parametrize("label", ["   ", "x" * 81, "bad\x00name"])
def test_issue_refuses_a_bad_name(client: TestClient, db_engine: Engine, label: str) -> None:
    """SD-21 (name)."""
    cell = _Cell(client)
    before = _counts(db_engine)
    response = _issue(_enroller(client), cell.station_id, label=label)
    assert response.status_code == 422 and response.json() == {"detail": _E2}
    assert _counts(db_engine) == before


def test_issue_refuses_an_unknown_or_inactive_station(
    client: TestClient, db_engine: Engine
) -> None:
    """SD-21 (station)."""
    before = _counts(db_engine)
    unknown = _issue(_enroller(client), "NO-SUCH-STATION")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Scan Station 'NO-SUCH-STATION' does not exist."}
    cell = _Cell(client)
    _ok(admin_of(client).patch(f"/api/scan-stations/{cell.station_id}", json={"is_active": False}))
    before = _counts(db_engine)
    inactive = _issue(_enroller(client), cell.station_id)
    assert inactive.status_code == 409
    assert inactive.json() == {
        "detail": f"Scan Station '{cell.station_id}' is inactive. Reactivate it before"
        " enrolling a device."
    }
    assert _counts(db_engine) == before


def test_last_seen(client: TestClient, db_engine: Engine) -> None:
    """SD-22: at most once a minute, for every request with a valid token
    — a refused one included — and never for a revoked device."""
    cell = _Cell(client)
    device_id, token = _enroll(client, cell.station_id)
    headers = station_device_headers(token)
    path = f"/api/scan-stations/{cell.station_id}/context"
    assert _device(db_engine, device_id).last_seen_at is None
    _ok(client.get(path, headers=headers))
    first = _device(db_engine, device_id).last_seen_at
    assert first is not None
    _ok(client.get(path, headers=headers))
    assert _device(db_engine, device_id).last_seen_at == first

    def back_date() -> datetime.datetime:
        with db_engine.begin() as connection:
            return cast(
                datetime.datetime,
                connection.execute(
                    sa.text(
                        "UPDATE scan_station_devices"
                        " SET last_seen_at = now() - interval '61 seconds' WHERE id = :id"
                        " RETURNING last_seen_at"
                    ),
                    {"id": device_id},
                ).scalar_one(),
            )

    old = back_date()
    _ok(client.get(path, headers=headers))
    assert _device(db_engine, device_id).last_seen_at > old
    old = back_date()
    with _revoked(db_engine, Permission.SCAN_PN_BARCODES):
        refused = client.post(
            f"/api/scan-stations/{cell.station_id}/scans/resolve",
            json={"part_number": _unique("PN")},
            headers=headers,
        )
    assert refused.status_code == 403 and refused.json()["station_permission_denied"] is True
    assert _device(db_engine, device_id).last_seen_at > old
    old = back_date()
    _ok(admin_of(client).post(f"/api/scan-station-devices/{device_id}/revocation"))
    assert client.get(path, headers=headers).status_code == 401
    assert _device(db_engine, device_id).last_seen_at == old


def test_the_audit_rows(client: TestClient, db_engine: Engine) -> None:
    """SD-24."""
    cell = _Cell(client)
    enroller = _enroller(client)
    issued = _ok(_issue(enroller, cell.station_id), 201)
    device_id = int(issued["device"]["id"])
    _ok(_activate(client, cell.station_id, issued["enrollment_code"]), 201)
    revoker = client_as(client, _MSS)
    _ok(revoker.post(f"/api/scan-station-devices/{device_id}/revocation"))
    created, activated, revoked = _audits(db_engine, device_id)
    assert (created["event_type"], created["actor_user_id"]) == ("CREATED", enroller.user_id)
    assert created["before_data"] is None
    assert set(created["after_data"]) == _SNAPSHOT_KEYS
    assert created["after_data"]["state"] == "PENDING"
    assert (activated["event_type"], activated["actor_user_id"]) == ("UPDATED", None)
    assert activated["metadata"] == {"source": "station-activation"}
    assert (activated["before_data"]["state"], activated["after_data"]["state"]) == (
        "PENDING",
        "ACTIVE",
    )
    assert (revoked["event_type"], revoked["actor_user_id"]) == ("UPDATED", revoker.user_id)
    assert revoked["after_data"]["state"] == "REVOKED"
    for row in (activated, revoked):
        assert set(row["before_data"]) == set(row["after_data"]) == _SNAPSHOT_KEYS


def test_the_roles_answer_marks_the_role_applied_at_scan_stations(
    client: TestClient, db_engine: Engine
) -> None:
    """SD-25."""
    station_role = _station_role_id(db_engine)
    response = admin_of(client).get("/api/roles")
    assert response.status_code == 200, response.text
    roles = cast(list[dict[str, Any]], response.json())
    flagged = [role["id"] for role in roles if role["applies_at_scan_stations"]]
    assert flagged == [station_role]
    assert next(role for role in roles if role["id"] == station_role)["name"] == "Operator"


# ---------------------------------------------------------------------------
# The enrollment guard (SD-20)
# ---------------------------------------------------------------------------


def test_the_enrollment_guard(client: TestClient, db_engine: Engine) -> None:
    """SD-20 (OD-S4-9): issuing needs Manage correction permissions while
    the station role holds a protected key; revoking never does."""
    cell = _Cell(client)
    manager = client_as(client, _MSS)
    before = _counts(db_engine)
    refused = _issue(manager, cell.station_id)
    assert refused.status_code == 403, refused.text
    assert refused.json() == {
        "detail": _G4,
        "permission_denied": True,
        "required_permissions": [_MCP.value, _MSS.value],
    }
    assert _counts(db_engine) == before
    assert _issue(anonymous_client(client), cell.station_id).status_code == 401
    without = _issue(client_as(client, _MCP), cell.station_id)
    assert without.status_code == 403 and without.json()["required_permissions"] == [_MSS.value]
    station_role = _station_role_id(db_engine)
    correction_manager = client_as(client, _MUAR, _MCP)
    revoke_undo = {"revoke_permissions": [Permission.UNDO_RECENT_SCANS.value]}
    try:
        _ok(correction_manager.patch(f"/api/roles/{station_role}", json=revoke_undo))
        listed = _ok(manager.get("/api/scan-station-devices"))
        assert listed["enrollment_permissions"] == [_MSS.value]
        issued = _ok(_issue(manager, cell.station_id), 201)
        _ok(manager.post(f"/api/scan-station-devices/{issued['device']['id']}/revocation"))
    finally:
        _grant(db_engine, Permission.UNDO_RECENT_SCANS)
    listed = _ok(manager.get("/api/scan-station-devices"))
    assert listed["enrollment_permissions"] == [_MCP.value, _MSS.value]


def test_the_enrollment_guard_is_judged_under_the_user_administration_lock(
    client: TestClient, db_engine: Engine
) -> None:
    """SD-20 (lock): a grant of a protected key to the station role held open
    under the User-administration lock makes a concurrent issue wait, then
    refuse on the committed grants."""
    cell = _Cell(client)
    manager = client_as(client, _MSS)
    station_role = _station_role_id(db_engine)
    with _revoked(db_engine, Permission.UNDO_RECENT_SCANS):
        before = _counts(db_engine)
        results: dict[str, Any] = {}
        with db_engine.connect() as holder:
            transaction = holder.begin()
            holder.execute(sa.text("SET LOCAL statement_timeout = '20s'"))
            holder.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                {"k": "partflow:user-administration"},
            )
            holder.execute(
                sa.text("INSERT INTO role_permissions (role_id, permission) VALUES (:r, :p)"),
                {"r": station_role, "p": Permission.UNDO_RECENT_SCANS.value},
            )
            thread = threading.Thread(
                target=lambda: results.update(issue=_issue(manager, cell.station_id)),
                daemon=True,
            )
            thread.start()
            thread.join(timeout=1.0)
            assert thread.is_alive(), "the issue did not wait for the lock"
            transaction.commit()
        thread.join(timeout=20)
        assert not thread.is_alive()
        response = results["issue"]
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == _G4
        assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# Station role keys (SD-10 – SD-15)
# ---------------------------------------------------------------------------


class _Command(NamedTuple):
    """One station command: what its key is and how to send it."""

    command: StationCommand
    send: Callable[[str], Any]
    created: int


def _command(client: TestClient, station: TestClient, name: str) -> _Command:
    """Set up one command's scenario; ``send(device_event_id)`` posts it."""
    if name == "receipts":
        cell = _Cell(client)
        pn = _unique("PN")
        body = _receipt_body(pn, 3)
        return _Command(
            StationCommand.RECEIPT,
            lambda event: station.post(
                f"/api/scan-stations/{cell.station_id}/receipts",
                json={**body, "device_event_id": event},
            ),
            201,
        )
    if name in ("machine-assignments", "machine-releases", "area-completions (Machine)"):
        cell = _Cell(client, machine_count=1)
        flow_id, pn = _release(client, cell, quantity=4)
        path = f"/api/scan-stations/{cell.station_id}/machine-assignments"
        if name != "machine-assignments":
            _ok(station.post(path, json=_in_area_body(flow_id, pn, 4, cell.machine_id)), 201)
            path = path.replace(
                "machine-assignments",
                "machine-releases" if name == "machine-releases" else "area-completions",
            )
        command = {
            "machine-assignments": StationCommand.MACHINE_ASSIGNMENT,
            "machine-releases": StationCommand.MACHINE_RELEASE,
        }.get(name, StationCommand.AREA_COMPLETION)
        return _Command(
            command,
            lambda event: station.post(
                path, json=_in_area_body(flow_id, pn, 4, cell.machine_id, event)
            ),
            201,
        )
    if name == "area-completions (direct)":
        cell = _Cell(client)
        flow_id, pn = _release(client, cell, quantity=4)
        return _Command(
            StationCommand.AREA_COMPLETION,
            lambda event: station.post(
                f"/api/scan-stations/{cell.station_id}/area-completions",
                json=_in_area_body(flow_id, pn, 4, event=event),
            ),
            201,
        )
    if name in ("transfers", "stockings"):
        source = _Cell(client)
        target = _Cell(client, is_terminal=name == "stockings")
        flow_id, pn = _release(client, source, quantity=4)
        return _Command(
            StationCommand.TRANSFER if name == "transfers" else StationCommand.STOCKING,
            lambda event: station.post(
                f"/api/scan-stations/{target.station_id}/{name}",
                json=_transfer_body(source, target, flow_id, pn, 4, event),
            ),
            201,
        )
    if name == "merges":
        cell = _Cell(client)
        first, pn = _release(client, cell, quantity=2)
        second, _ = _release(client, cell, quantity=3, pn=pn)
        return _Command(
            StationCommand.MERGE,
            lambda event: station.post(
                f"/api/scan-stations/{cell.station_id}/merges",
                json={
                    "part_number": pn,
                    "quantity_flow_ids": [first, second],
                    "device_event_id": event,
                },
            ),
            201,
        )
    if name == "scraps":
        cell = _Cell(client)
        flow_id, pn = _release(client, cell, quantity=4)
        return _Command(
            StationCommand.SCRAP,
            lambda event: station.post(
                f"/api/scan-stations/{cell.station_id}/scraps",
                json={
                    "part_number": pn,
                    "quantity_flow_id": flow_id,
                    "quantity": 1,
                    "reason": "Damaged",
                    "device_event_id": event,
                },
            ),
            201,
        )
    if name == "quantity-additions":
        cell = _Cell(client)
        _, pn = _release(client, cell, quantity=4)
        return _Command(
            StationCommand.QUANTITY_ADDITION,
            lambda event: station.post(
                f"/api/scan-stations/{cell.station_id}/quantity-additions",
                json={
                    "part_number": pn,
                    "quantity": 2,
                    "reason": "Found",
                    "device_event_id": event,
                },
            ),
            201,
        )
    if name == "undos":
        source, target = _Cell(client), _Cell(client)
        flow_id, pn = _release(client, source, quantity=4)
        moved = _event()
        _ok(
            station.post(
                f"/api/scan-stations/{target.station_id}/transfers",
                json=_transfer_body(source, target, flow_id, pn, 4, moved),
            ),
            201,
        )
        return _Command(
            StationCommand.UNDO,
            lambda event: station.post(
                f"/api/scan-stations/{target.station_id}/undos",
                json={
                    "part_number": pn,
                    "reverses_device_event_id": moved,
                    "device_event_id": event,
                },
            ),
            201,
        )
    assert name == "allocations"
    stock = _stocked(client, station)
    return _Command(
        StationCommand.ALLOCATION,
        lambda event: station.post(
            "/api/allocations", json=_allocation_body(stock, [(stock.demand_id, 2)], event)
        ),
        201,
    )


_COMMANDS = (
    "receipts",
    "machine-assignments",
    "machine-releases",
    "area-completions (Machine)",
    "area-completions (direct)",
    "transfers",
    "stockings",
    "merges",
    "scraps",
    "quantity-additions",
    "undos",
    "allocations",
)


@pytest.mark.parametrize("name", _COMMANDS)
def test_each_command_needs_its_station_key(
    client: TestClient, station: TestClient, db_engine: Engine, name: str
) -> None:
    """SD-10: without the mapped key → 403 K-1 naming it, nothing recorded;
    with it again → recorded."""
    case = _command(client, station, name)
    key = station_access.COMMAND_PERMISSION[case.command]
    before = _counts(db_engine)
    with _revoked(db_engine, key):
        refused = case.send(_event())
        assert refused.status_code == 403, refused.text
        assert refused.json() == {
            "detail": station_access.permission_denied_message(case.command),
            "station_permission_denied": True,
            "required_permissions": [key.value],
        }
        assert _counts(db_engine) == before
    assert case.send(_event()).status_code == case.created


def test_a_committed_command_replays_after_its_key_is_revoked(
    client: TestClient, station: TestClient, db_engine: Engine
) -> None:
    """SD-11."""
    case = _command(client, station, "transfers")
    event = _event()
    committed = case.send(event)
    assert committed.status_code == 201, committed.text
    with _revoked(db_engine, Permission.CONFIRM_QUANTITY):
        replay = case.send(event)
        assert replay.status_code == 200, replay.text
        assert replay.json() == committed.json()
        fresh = case.send(_event())
        assert fresh.status_code == 403 and fresh.json()["station_permission_denied"] is True


def test_a_committed_command_replays_from_any_device_of_its_station(
    client: TestClient, db_engine: Engine
) -> None:
    """SD-12: per station, never per device; a replaced device cannot."""
    source, target = _Cell(client), _Cell(client)
    flow_id, pn = _release(client, source, quantity=4)
    body = _transfer_body(source, target, flow_id, pn, 4)
    path = f"/api/scan-stations/{target.station_id}/transfers"
    first_id, first = _enroll(client, target.station_id)
    _, second = _enroll(client, target.station_id)
    committed = client.post(path, json=body, headers=station_device_headers(first))
    assert committed.status_code == 201, committed.text
    replay = client.post(path, json=body, headers=station_device_headers(second))
    assert (replay.status_code, replay.json()) == (200, committed.json())
    issued = _ok(_issue(_enroller(client), target.station_id, replaces_device_id=first_id), 201)
    third = _ok(_activate(client, target.station_id, issued["enrollment_code"]), 201)
    replay = client.post(path, json=body, headers=station_device_headers(third["device_token"]))
    assert (replay.status_code, replay.json()) == (200, committed.json())
    refused = client.post(path, json=body, headers=station_device_headers(first))
    assert refused.status_code == 401 and refused.json()["station_device_required"] is True
    with db_engine.connect() as connection:
        for table in ("part_movements", "work_order_allocations"):
            columns = sa.inspect(connection).get_columns(table)
            assert not [c for c in columns if "station_device" in str(c["name"])], table


def test_each_read_needs_its_station_key_first(
    client: TestClient, station: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """SD-13: PN and Machine resolve, badge scan, Undo preview and the
    allocation suggestion answer K-1 first; the badge scan touches no
    Worker Session."""
    cell = _Cell(client)
    worker = _ok(
        admin_of(client).post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )
    _ok(
        admin_of(client).patch(
            f"/api/areas/{cell.area_id}", json={"worker_identification_mode": "SCANNED"}
        )
    )
    signed = _ok(
        station.post(
            f"/api/scan-stations/{cell.station_id}/badge-scans",
            json={"badge": worker["badge_barcode"]},
        )
    )
    assert signed["outcome"] == "SIGNED_IN"
    reads: list[tuple[Permission, StationCommand, Callable[[], Any]]] = [
        (
            Permission.SCAN_PN_BARCODES,
            StationCommand.PN_SCAN,
            lambda: station.post(
                f"/api/scan-stations/{cell.station_id}/scans/resolve",
                json={"part_number": shop.pn},
            ),
        ),
        (
            Permission.SCAN_MACHINE_BARCODES,
            StationCommand.MACHINE_SCAN,
            lambda: station.post(
                f"/api/scan-stations/{shop.machines.station_id}/machine-scans/resolve",
                json={"asset_tag": "SD-0001"},
            ),
        ),
        (
            Permission.SCAN_WORKER_BARCODES,
            StationCommand.BADGE_SCAN,
            lambda: station.post(
                f"/api/scan-stations/{cell.station_id}/badge-scans",
                json={"badge": worker["badge_barcode"]},
            ),
        ),
        (
            Permission.UNDO_RECENT_SCANS,
            StationCommand.UNDO,
            lambda: station.get(
                f"/api/scan-stations/{shop.cell.station_id}/undo-preview/{shop.transfer_event}"
            ),
        ),
        (
            Permission.CONFIRM_SUGGESTED_ALLOCATION,
            StationCommand.ALLOCATION,
            lambda: station.get(
                "/api/allocations/suggestion", params={"part_number": shop.stock.pn}
            ),
        ),
    ]
    for key, kind, send in reads:
        before = _counts(db_engine)
        with _revoked(db_engine, key):
            refused = send()
            assert refused.status_code == 403, (key, refused.text)
            assert refused.json() == {
                "detail": station_access.permission_denied_message(kind),
                "station_permission_denied": True,
                "required_permissions": [key.value],
            }
        assert _counts(db_engine) == before, key
        assert send().status_code == 200, key


def test_the_k1_copy_names_each_action() -> None:
    """§4.3 K-1 actions, exactly."""
    actions = {
        StationCommand.PN_SCAN: "scan Part Number barcodes",
        StationCommand.MACHINE_SCAN: "scan Machine barcodes",
        StationCommand.BADGE_SCAN: "scan Worker badges",
        StationCommand.RECEIPT: "receive quantity",
        StationCommand.MACHINE_ASSIGNMENT: "assign quantity to Machines",
        StationCommand.MACHINE_RELEASE: "assign quantity to Machines",
        StationCommand.AREA_COMPLETION: "confirm quantity",
        StationCommand.TRANSFER: "confirm quantity",
        StationCommand.MERGE: "confirm quantity",
        StationCommand.SCRAP: "confirm quantity",
        StationCommand.QUANTITY_ADDITION: "confirm quantity",
        StationCommand.STOCKING: "complete production into the Stockroom",
        StationCommand.ALLOCATION: "confirm suggested allocations",
        StationCommand.ALLOCATION_ADJUSTMENT: "adjust suggested allocations",
        StationCommand.UNDO: "undo recent scans",
    }
    assert set(actions) == set(StationCommand)
    for kind, action in actions.items():
        assert station_access.permission_denied_message(kind) == _k1(action)
    assert set(station_access.COMMAND_PERMISSION.values()) == station_access.STATION_PERMISSIONS


def test_the_allocation_adjustment_check(
    client: TestClient, station: TestClient, db_engine: Engine
) -> None:
    """SD-14: without Adjust suggested allocations, lines equal to the
    suggestion are recorded, differing lines are K-1, a stale suggestion
    sent unchanged is C-2; ``suggestion_unchanged`` is outside the
    fingerprint; with the key a stale suggestion is a manual override."""
    stock = _stocked(client, station, requested=5)
    adjust = Permission.ADJUST_SUGGESTED_ALLOCATION
    with _revoked(db_engine, adjust):
        suggestion = _ok(
            station.get(
                "/api/allocations/suggestion", params={"part_number": stock.pn, "quantity": 1}
            )
        )
        proposed = [
            (line["work_order_demand_id"], line["proposed_quantity"])
            for line in suggestion["lines"]
            if line["proposed_quantity"] > 0
        ]
        assert proposed == [(stock.demand_id, 1)]
        event = _event()
        body = _allocation_body(stock, proposed, event, suggestion_unchanged=True)
        recorded = station.post("/api/allocations", json=body)
        assert recorded.status_code == 201, recorded.text
        assert not any(row["is_manual_override"] for row in recorded.json()["rows"])
        replay = station.post("/api/allocations", json={**body, "suggestion_unchanged": False})
        assert (replay.status_code, replay.json()) == (200, recorded.json())

        before = _counts(db_engine)
        for unchanged in (False, None):
            extra: dict[str, Any] = {} if unchanged is None else {"suggestion_unchanged": unchanged}
            differing = station.post(
                "/api/allocations",
                json=_allocation_body(stock, [(stock.supply_demand_id, 1)], **extra),
            )
            assert differing.status_code == 403, differing.text
            assert differing.json() == {
                "detail": station_access.permission_denied_message(
                    StationCommand.ALLOCATION_ADJUSTMENT
                ),
                "station_permission_denied": True,
                "required_permissions": [
                    "ADJUST_SUGGESTED_ALLOCATION",
                    "CONFIRM_SUGGESTED_ALLOCATION",
                ],
            }
            assert _counts(db_engine) == before

        # The suggestion goes stale: the supply line is ranked Hot meanwhile.
        _set_rank(db_engine, stock.supply_demand_id, 1)
        try:
            before = _counts(db_engine)
            stale = station.post(
                "/api/allocations",
                json=_allocation_body(stock, proposed, suggestion_unchanged=True),
            )
            assert stale.status_code == 409, stale.text
            assert stale.json() == {"detail": _C2}
            assert _counts(db_engine) == before
        finally:
            _set_rank(db_engine, stock.supply_demand_id, None)
    _set_rank(db_engine, stock.supply_demand_id, 1)
    try:
        override = station.post(
            "/api/allocations", json=_allocation_body(stock, proposed, suggestion_unchanged=True)
        )
        assert override.status_code == 201, override.text
        assert override.json()["rows"][0]["is_manual_override"] is True
    finally:
        _set_rank(db_engine, stock.supply_demand_id, None)


def _set_rank(engine: Engine, demand_id: int, rank: int | None) -> None:
    with engine.begin() as connection:
        if rank is not None:
            connection.execute(
                sa.text(
                    "UPDATE work_order_demands SET priority_rank = NULL"
                    " WHERE priority_rank = :rank AND id <> :id"
                ),
                {"rank": rank, "id": demand_id},
            )
        connection.execute(
            sa.text("UPDATE work_order_demands SET priority_rank = :rank WHERE id = :id"),
            {"rank": rank, "id": demand_id},
        )


def test_undo_is_refused_for_its_key_before_the_reason_policy(
    client: TestClient, station: TestClient, db_engine: Engine
) -> None:
    """SD-15."""
    case = _command(client, station, "undos")
    policy = "/api/policies/correction-permissions"
    _ok(admin_of(client).put(policy, json={"undo_reason_required": True}))
    try:
        before = _counts(db_engine)
        with _revoked(db_engine, Permission.UNDO_RECENT_SCANS):
            refused = case.send(_event())
        assert refused.status_code == 403, refused.text
        assert refused.json()["station_permission_denied"] is True
        assert "undo_reason_required" not in refused.json()
        assert _counts(db_engine) == before
    finally:
        _ok(admin_of(client).put(policy, json={"undo_reason_required": False}))


def test_the_worker_identity_is_the_stations_not_the_devices(
    client: TestClient, db_engine: Engine
) -> None:
    """SD-16: Fixed and Scanned modes record the same Worker through two
    devices of one station; a device revocation leaves the session open."""
    admin = admin_of(client)
    worker = _ok(
        admin.post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )
    fixed = _Cell(client)
    _ok(
        admin.patch(
            f"/api/areas/{fixed.area_id}",
            json={"worker_identification_mode": "FIXED", "fixed_worker_id": worker["id"]},
        )
    )
    first_id, first = _enroll(client, fixed.station_id)
    _, second = _enroll(client, fixed.station_id)
    for token in (first, second):
        flow_id, pn = _release(client, fixed, quantity=2)
        done = client.post(
            f"/api/scan-stations/{fixed.station_id}/area-completions",
            json=_in_area_body(flow_id, pn, 2),
            headers=station_device_headers(token),
        )
        assert done.status_code == 201, done.text
        recorded = _scalar(
            db_engine,
            "SELECT worker_id FROM part_movements WHERE device_event_id = :e",
            e=done.json()["device_event_id"],
        )
        assert recorded == worker["id"]

    scanned = _Cell(client)
    _ok(
        admin.patch(f"/api/areas/{scanned.area_id}", json={"worker_identification_mode": "SCANNED"})
    )
    signer_id, signer = _enroll(client, scanned.station_id)
    _, other = _enroll(client, scanned.station_id)
    signed = _ok(
        client.post(
            f"/api/scan-stations/{scanned.station_id}/badge-scans",
            json={"badge": worker["badge_barcode"]},
            headers=station_device_headers(signer),
        )
    )
    assert signed["outcome"] == "SIGNED_IN"
    _ok(admin.post(f"/api/scan-station-devices/{signer_id}/revocation"))
    context = _ok(
        client.get(
            f"/api/scan-stations/{scanned.station_id}/context",
            headers=station_device_headers(other),
        )
    )
    session = context["worker_identification"]["session"]
    assert session is not None and session["worker"]["id"] == worker["id"]
    assert first_id != signer_id


# ---------------------------------------------------------------------------
# Concurrency (SD-17 – SD-19, SD-30)
# ---------------------------------------------------------------------------


def _activate_service(engine: Engine, station_id: str, code: str) -> Any:
    with Session(engine) as session:
        try:
            return station_devices.activate_device(session, station_id, enrollment_code=code)
        except EnrollmentCodeInvalidError as exc:
            return exc


def _run_threads(*calls: Callable[[], Any]) -> list[Any]:
    results: list[Any] = [None] * len(calls)

    def run(index: int) -> None:
        try:
            results[index] = calls[index]()
        except Exception as exc:  # noqa: BLE001 — collected for assertions
            results[index] = exc

    threads = [
        threading.Thread(target=run, args=(index,), daemon=True) for index in range(len(calls))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a concurrent call never finished"
    return results


def test_two_activations_replacing_one_device(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SD-17: both activate; the replaced device is revoked exactly once."""
    cell = _Cell(client)
    replaced_id, _ = _enroll(client, cell.station_id)
    enroller = _enroller(client)
    codes = [
        _ok(_issue(enroller, cell.station_id, replaces_device_id=replaced_id), 201)[
            "enrollment_code"
        ]
        for _ in range(2)
    ]
    barrier = threading.Barrier(2, timeout=20)
    real_view = station_devices._view
    first_view: set[int] = set()

    def view_after_code_lock(session: Session, device_id: int) -> Any:
        view = real_view(session, device_id)
        thread = threading.get_ident()
        if thread not in first_view:
            first_view.add(thread)
            barrier.wait()  # both hold their own code row now
        return view

    monkeypatch.setattr(station_devices, "_view", view_after_code_lock)
    results = _run_threads(
        *(lambda code=code: _activate_service(db_engine, cell.station_id, code) for code in codes)
    )
    assert all(isinstance(result, station_devices.DeviceActivated) for result in results)
    assert _device(db_engine, replaced_id).revoked_reason == "REPLACED"
    replaced_audits = [
        row
        for row in _audits(db_engine, replaced_id)
        if (row["metadata"] or {}).get("replaced_by_device_id")
    ]
    assert len(replaced_audits) == 1


def test_an_activation_and_a_revocation_of_one_code(client: TestClient, db_engine: Engine) -> None:
    """SD-18: one serial outcome either way, consistent with the audit rows."""
    cell = _Cell(client)
    issued = _ok(_issue(_enroller(client), cell.station_id), 201)
    device_id = int(issued["device"]["id"])
    revoker = client_as(client, _MSS)
    activation, revocation = _run_threads(
        lambda: _activate_service(db_engine, cell.station_id, issued["enrollment_code"]),
        lambda: revoker.post(f"/api/scan-station-devices/{device_id}/revocation"),
    )
    assert revocation.status_code == 200, revocation.text
    row = _device(db_engine, device_id)
    assert row.revoked_reason == "REVOKED"
    events = [audit["after_data"]["state"] for audit in _audits(db_engine, device_id)]
    if isinstance(activation, station_devices.DeviceActivated):
        assert row.activated_at is not None
        assert events == ["PENDING", "ACTIVE", "REVOKED"]
    else:
        assert isinstance(activation, EnrollmentCodeInvalidError)
        assert row.activated_at is None
        assert events == ["PENDING", "REVOKED"]


def test_two_activations_of_one_code(client: TestClient, db_engine: Engine) -> None:
    """SD-19: exactly one wins; one token digest is stored."""
    cell = _Cell(client)
    issued = _ok(_issue(_enroller(client), cell.station_id), 201)
    code = issued["enrollment_code"]
    results = _run_threads(
        lambda: _activate_service(db_engine, cell.station_id, code),
        lambda: _activate_service(db_engine, cell.station_id, code),
    )
    winners = [r for r in results if isinstance(r, station_devices.DeviceActivated)]
    losers = [r for r in results if isinstance(r, EnrollmentCodeInvalidError)]
    assert (len(winners), len(losers)) == (1, 1)
    row = _device(db_engine, int(issued["device"]["id"]))
    assert row.token_digest is not None and row.enrollment_code_digest is None


class _Inflight(NamedTuple):
    key: Permission
    call: Callable[[Session, str], Any]
    records: Callable[[str], int]


def _movement_records(engine: Engine) -> Callable[[str], int]:
    return lambda event: int(
        _scalar(engine, "SELECT count(*) FROM part_movements WHERE device_event_id = :e", e=event)
    )


def _inflight(client: TestClient, station: TestClient, engine: Engine, body: str) -> _Inflight:
    """One service body of §3.6 called directly, after a setup through the API."""
    movements = _movement_records(engine)
    confirm = Permission.CONFIRM_QUANTITY
    if body == "receipt":
        cell = _Cell(client)
        pn = _unique("PN")
        scanned = datetime.datetime.now(datetime.UTC)
        return _Inflight(
            Permission.RECEIVE_QUANTITY,
            lambda session, event: intake.receive_quantity(
                session,
                station_id=cell.station_id,
                part_number=pn,
                quantity=3,
                request_type="MODIFY",
                route_mode="FLOATING",
                scanned_at=scanned,
                device_event_id=event,
            ),
            movements,
        )
    if body in ("assignment", "leave machine"):
        cell = _Cell(client, machine_count=1)
        flow_id, pn = _release(client, cell, quantity=4)
        if body == "assignment":
            return _Inflight(
                Permission.ASSIGN_QUANTITY_TO_MACHINE,
                lambda session, event: machine_processing.assign_to_machine(
                    session,
                    station_id=cell.station_id,
                    part_number=pn,
                    quantity_flow_id=flow_id,
                    machine_id=cell.machine_id,
                    quantity=4,
                    device_event_id=event,
                ),
                movements,
            )
        _ok(
            station.post(
                f"/api/scan-stations/{cell.station_id}/machine-assignments",
                json=_in_area_body(flow_id, pn, 4, cell.machine_id),
            ),
            201,
        )
        return _Inflight(
            Permission.ASSIGN_QUANTITY_TO_MACHINE,
            lambda session, event: machine_processing.release_to_queue(
                session,
                station_id=cell.station_id,
                part_number=pn,
                quantity_flow_id=flow_id,
                machine_id=cell.machine_id,
                quantity=4,
                device_event_id=event,
                confirming_badge=None,
            ),
            movements,
        )
    if body == "direct DONE":
        cell = _Cell(client)
        flow_id, pn = _release(client, cell, quantity=4)
        return _Inflight(
            confirm,
            lambda session, event: direct_processing.complete_direct_processing(
                session,
                station_id=cell.station_id,
                part_number=pn,
                quantity_flow_id=flow_id,
                quantity=4,
                device_event_id=event,
                confirming_badge=None,
            ),
            movements,
        )
    if body == "arrival":
        source, target = _Cell(client), _Cell(client)
        flow_id, pn = _release(client, source, quantity=4)
        return _Inflight(
            confirm,
            lambda session, event: transfers.transfer_to_station_area(
                session,
                station_id=target.station_id,
                part_number=pn,
                quantity_flow_id=flow_id,
                source_area_id=source.area_id,
                target_area_id=target.area_id,
                quantity=4,
                operation_id=None,
                confirm_route_deviation=False,
                route_deviation_reason=None,
                device_event_id=event,
            ),
            movements,
        )
    if body == "merge":
        cell = _Cell(client)
        first, pn = _release(client, cell, quantity=2)
        second, _ = _release(client, cell, quantity=3, pn=pn)
        return _Inflight(
            confirm,
            lambda session, event: merges.merge_flows(
                session,
                station_id=cell.station_id,
                part_number=pn,
                quantity_flow_ids=[first, second],
                device_event_id=event,
            ),
            movements,
        )
    if body in ("scrap", "addition"):
        cell = _Cell(client)
        flow_id, pn = _release(client, cell, quantity=4)
        if body == "scrap":
            return _Inflight(
                confirm,
                lambda session, event: quantity_events.scrap_flow(
                    session,
                    station_id=cell.station_id,
                    part_number=pn,
                    quantity_flow_id=flow_id,
                    quantity=1,
                    reason="Damaged",
                    device_event_id=event,
                ),
                movements,
            )
        return _Inflight(
            confirm,
            lambda session, event: quantity_events.add_quantity(
                session,
                station_id=cell.station_id,
                part_number=pn,
                quantity=2,
                reason="Found",
                operation_id=None,
                device_event_id=event,
            ),
            movements,
        )
    if body == "undo":
        source, target = _Cell(client), _Cell(client)
        flow_id, pn = _release(client, source, quantity=4)
        moved = _event()
        _ok(
            station.post(
                f"/api/scan-stations/{target.station_id}/transfers",
                json=_transfer_body(source, target, flow_id, pn, 4, moved),
            ),
            201,
        )
        return _Inflight(
            Permission.UNDO_RECENT_SCANS,
            lambda session, event: undo.undo_command(
                session,
                station_id=target.station_id,
                part_number=pn,
                reverses_device_event_id=moved,
                device_event_id=event,
            ),
            movements,
        )
    assert body == "allocation"
    stock = _stocked(client, station)
    return _Inflight(
        Permission.CONFIRM_SUGGESTED_ALLOCATION,
        lambda session, event: allocations.confirm_station_allocation(
            session,
            station_id=stock.stockroom.station_id,
            part_number=stock.pn,
            allocation_quantity=2,
            lines=[{"work_order_demand_id": stock.demand_id, "quantity": 2}],
            device_event_id=event,
        ),
        lambda event: int(
            _scalar(
                engine,
                "SELECT count(*) FROM work_order_allocations WHERE device_event_id = :e",
                e=event,
            )
        ),
    )


_BODIES = (
    "receipt",
    "assignment",
    "leave machine",
    "direct DONE",
    "arrival",
    "merge",
    "scrap",
    "addition",
    "undo",
    "allocation",
)


def _waiting_on_a_lock(engine: Engine) -> bool:
    return bool(
        _scalar(
            engine,
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            " AND wait_event_type = 'Lock'",
        )
    )


@pytest.mark.parametrize("body", _BODIES)
def test_an_inflight_retry_replays_and_is_never_refused_for_a_key(
    client: TestClient,
    station: TestClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    """SD-30: attempt A holds its blocking locks (paused in the identity
    resolver, past its key check); the key is revoked; the identical
    retry B waits on A's lock and then replays — never K-1; one record."""
    case = _inflight(client, station, db_engine, body)
    event = _event()
    inside, release = threading.Event(), threading.Event()
    real_resolve = station_identity.resolve_station_identity
    paused_thread: list[int] = []

    def paused_resolve(*args: Any, **kwargs: Any) -> Any:
        if not paused_thread:
            paused_thread.append(threading.get_ident())
            inside.set()
            assert release.wait(timeout=20), "test deadlock: A never released"
        return real_resolve(*args, **kwargs)

    monkeypatch.setattr(station_identity, "resolve_station_identity", paused_resolve)

    def attempt() -> Any:
        with Session(db_engine) as session:
            return case.call(session, event)

    results: dict[str, Any] = {}

    def run(name: str) -> None:
        try:
            results[name] = attempt()
        except Exception as exc:  # noqa: BLE001 — collected for assertions
            results[name] = exc

    first = threading.Thread(target=run, args=("A",), daemon=True)
    second = threading.Thread(target=run, args=("B",), daemon=True)
    try:
        first.start()
        assert inside.wait(timeout=20), "A never reached its identity resolution"
        with _revoked(db_engine, case.key):
            second.start()
            deadline = time.monotonic() + 10
            while not _waiting_on_a_lock(db_engine) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert _waiting_on_a_lock(db_engine), "B never waited on A's lock"
            release.set()
            first.join(timeout=30)
            second.join(timeout=30)
    finally:
        release.set()
    assert not first.is_alive() and not second.is_alive()
    a, b = results["A"], results["B"]
    assert not isinstance(a, Exception), repr(a)
    assert a.created is True
    assert not isinstance(b, StationPermissionDeniedError), repr(b)
    if body == "scrap" and isinstance(b, ConflictError):
        # Pre-existing (Phase 9): the scrap closes or splits its flow, and
        # the flow precondition of `lock_flow_and_station` is judged before
        # the re-check — the retry is refused as stale, never as K-1.
        assert "no longer active" in b.message, repr(b)
    else:
        assert not isinstance(b, Exception), repr(b)
        assert b.created is False
    assert case.records(event) >= 1
    assert case.records(event) == case.records(event)  # stable, one command


def test_an_inflight_command_records_once(
    client: TestClient, station: TestClient, db_engine: Engine
) -> None:
    """SD-30 (count): the replayed retry recorded nothing more."""
    case = _inflight(client, station, db_engine, "arrival")
    event = _event()
    with Session(db_engine) as session:
        first = case.call(session, event)
    recorded = case.records(event)
    with _revoked(db_engine, case.key), Session(db_engine) as session:
        again = case.call(session, event)
    assert (first.created, again.created) == (True, False)
    assert case.records(event) == recorded


def test_every_station_command_kind_maps_to_a_station_key() -> None:
    """The key table (§3.6) is total and closed over the ten station keys."""
    assert set(station_access.COMMAND_PERMISSION) == set(StationCommand)
    assert set(Permission) >= station_access.STATION_PERMISSIONS
    assert len(station_access.STATION_PERMISSIONS) == 10
    assert set(ALL_PERMISSIONS) >= station_access.STATION_PERMISSIONS
