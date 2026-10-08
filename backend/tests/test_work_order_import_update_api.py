"""Integration tests of changing existing Work Orders by file import (Phase 15 slice 2).

Exercises ``POST /api/work-orders/import/preview`` and ``POST
/api/work-orders/import`` against a dedicated temporary database
migrated to head (S2 SPEC §6.1: UP, WP, CC-U, CR-U, AZ-U, CH-8):

- the check lists every change per Open/Released Work Order and digests
  it into ``update_token``; the import applies the changes only under
  that confirmation (``X-PartFlow-Import-Confirm``) — otherwise every
  update is refused per Work Order while the creates commit;
- each update is ONE ``update_work_order`` transaction with its usual
  rules and consequences (quantity floor, Hot-list removal, completion)
  and a state token compared under its locks: a Work Order changed
  after the commit planned it is refused and never overwritten;
- creating needs Create and edit Work Orders, changing Edit Work Order
  Demand; either key opens the check;
- audit rows carry the User and, on the demand rows, the intake
  channel; reconciliation stays clean.

The API commits real transactions, so tests isolate through unique
numbers/PNs; the module database is dropped afterwards.
"""

import csv
import datetime
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app import cli
from app.application import part_numbers, work_order_import, work_orders
from app.application.errors import ConflictError
from app.application.work_order_import import IMPORT_COLUMNS
from app.core.config import get_settings
from app.domain.enums import Permission
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import (
    CSRF_HEADERS,
    IdentityClient,
    admin_of,
    anonymous_client,
    client_as,
    station_device_client,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_work_order_import_update_api"
_CSV = "text/csv"
_CHECK = "X-PartFlow-Import-Check"
_CONFIRM = "X-PartFlow-Import-Confirm"
_PREVIEW = "/api/work-orders/import/preview"
_IMPORT = "/api/work-orders/import"
_TEMPLATES = ("/api/work-orders/import/template.csv", "/api/work-orders/import/template.xlsx")
_INTAKE = {"intake": {"channel": "FILE_IMPORT"}}
_BOTH_KEYS = ["EDIT_WORK_ORDER_DEMAND", "MANAGE_WORK_ORDERS"]

_U3 = (
    "This Work Order changed after the file was checked, so nothing was changed on it."
    " Check the file again."
)
_U4 = (
    "Work Orders in this file changed after the changes were confirmed, so this Work Order"
    " was not changed. Check the file again and confirm the new changes."
)
_C5 = "The import confirmation is not valid. Check the file again and confirm the changes."
_A2 = "Your account does not have permission to do this."
_V1 = "Your account does not have permission to view this."

Row = Sequence[object]


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
    """Direct database access for state verification."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def mwo(client: TestClient) -> IdentityClient:
    return client_as(client, Permission.MANAGE_WORK_ORDERS)


@pytest.fixture(scope="module")
def ewod(client: TestClient) -> IdentityClient:
    return client_as(client, Permission.EDIT_WORK_ORDER_DEMAND)


@pytest.fixture(scope="module")
def both(client: TestClient) -> IdentityClient:
    return client_as(client, Permission.MANAGE_WORK_ORDERS, Permission.EDIT_WORK_ORDER_DEMAND)


@pytest.fixture(scope="module")
def vpd(client: TestClient) -> IdentityClient:
    return client_as(client, Permission.VIEW_PRODUCTION_DATA)


# ---------------------------------------------------------------------------
# Shop and Work Order seeding
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class _Cell:
    """An Area with one Operation and one Scan Station."""

    def __init__(self, client: TestClient, department_id: int, *, is_terminal: bool) -> None:
        admin = admin_of(client)
        area = admin.post(
            "/api/areas",
            json={
                "department_id": department_id,
                "name": _unique("AREA"),
                "is_terminal": is_terminal,
            },
        )
        assert area.status_code == 201, area.text
        self.area_id = int(area.json()["id"])
        operation = admin.post(
            "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
        )
        assert operation.status_code == 201, operation.text
        self.operation_id = int(operation.json()["id"])
        station = admin.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
        )
        assert station.status_code == 201, station.text
        self.station_id = str(station.json()["station_id"])


class _Shop:
    """A release Area and the Stockroom of one Department."""

    def __init__(self, client: TestClient) -> None:
        department = admin_of(client).post("/api/departments", json={"name": _unique("DEPT")})
        assert department.status_code == 201, department.text
        department_id = int(department.json()["id"])
        self.material = _Cell(client, department_id, is_terminal=False)
        self.stockroom = _Cell(client, department_id, is_terminal=True)


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    return _Shop(client)


class _WorkOrder:
    def __init__(self, body: dict[str, Any]) -> None:
        self.id = int(body["id"])
        self.number = str(body["work_order_number"])
        self.demand_ids = [int(line["id"]) for line in body["demands"]]


def _seed(client: TestClient, *lines: tuple[str, int] | dict[str, Any]) -> _WorkOrder:
    """A manual Work Order with a unique number; a line is (PN, quantity)
    or a full line payload."""
    response = admin_of(client).post(
        "/api/work-orders",
        json={
            "work_order_number": _unique("WO"),
            "lines": [
                line
                if isinstance(line, dict)
                else {"part_number": line[0], "requested_quantity": line[1]}
                for line in lines
            ],
        },
    )
    assert response.status_code == 201, response.text
    return _WorkOrder(response.json())


def _release_response(
    client: TestClient, shop: _Shop, work_order: _WorkOrder, demand_id: int, pn: str, quantity: int
) -> httpx.Response:
    response: httpx.Response = admin_of(client).post(
        f"/api/work-orders/{work_order.id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": quantity,
            "route_mode": "FLOATING",
            "starting_area_id": shop.material.area_id,
            "operation_id": shop.material.operation_id,
            "confirm_active_quantity": True,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    return response


def _release(
    client: TestClient, shop: _Shop, work_order: _WorkOrder, demand_id: int, pn: str, quantity: int
) -> int:
    released = _release_response(client, shop, work_order, demand_id, pn, quantity)
    assert released.status_code == 201, released.text
    return int(released.json()["quantity_flow_id"])


def _stocked(
    client: TestClient, shop: _Shop, work_order: _WorkOrder, demand_id: int, pn: str, quantity: int
) -> None:
    """Release ``quantity`` for the demand and stock it — not allocated."""
    flow_id = _release(client, shop, work_order, demand_id, pn, quantity)
    stocked = station_device_client(client).post(
        f"/api/scan-stations/{shop.stockroom.station_id}/stockings",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": shop.material.area_id,
            "target_area_id": shop.stockroom.area_id,
            "quantity": quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert stocked.status_code == 201, stocked.text


def _allocate_response(
    client: TestClient, pn: str, demand_id: int, quantity: int
) -> httpx.Response:
    response: httpx.Response = admin_of(client).post(
        "/api/allocations/management",
        json={
            "part_number": pn,
            "allocation_quantity": quantity,
            "lines": [{"work_order_demand_id": demand_id, "quantity": quantity}],
            "device_event_id": str(uuid.uuid4()),
        },
    )
    return response


def _allocated(
    client: TestClient, shop: _Shop, work_order: _WorkOrder, demand_id: int, pn: str, quantity: int
) -> None:
    """Release, stock and allocate ``quantity`` back to the demand."""
    _stocked(client, shop, work_order, demand_id, pn, quantity)
    allocated = _allocate_response(client, pn, demand_id, quantity)
    assert allocated.status_code == 201, allocated.text


def _hot_order(client: TestClient) -> list[int]:
    response = admin_of(client).get("/api/hot-list")
    assert response.status_code == 200, response.text
    return [int(entry["work_order_demand_id"]) for entry in response.json()["entries"]]


def _hot_change(client: TestClient, action: str, new: list[int]) -> httpx.Response:
    response: httpx.Response = admin_of(client).post(
        "/api/hot-list/changes",
        json={
            "device_event_id": str(uuid.uuid4()),
            "action": action,
            "expected_order": _hot_order(client),
            "new_order": new,
        },
    )
    return response


def _hot_add(client: TestClient, *demand_ids: int) -> None:
    for demand_id in demand_ids:
        added = _hot_change(client, "ADD", [*_hot_order(client), demand_id])
        assert added.status_code == 201, added.text


# ---------------------------------------------------------------------------
# File and request helpers
# ---------------------------------------------------------------------------


def _csv(*rows: Row) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(IMPORT_COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _token(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _preview(caller: TestClient, body: bytes) -> httpx.Response:
    response: httpx.Response = caller.post(_PREVIEW, content=body, headers={"Content-Type": _CSV})
    return response


def _commit(caller: TestClient, body: bytes, *, confirm: str | None = None) -> httpx.Response:
    headers = {"Content-Type": _CSV, _CHECK: _token(body)}
    if confirm is not None:
        headers[_CONFIRM] = confirm
    response: httpx.Response = caller.post(_IMPORT, content=body, headers=headers)
    return response


def _ok(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _checked(caller: TestClient, body: bytes) -> dict[str, Any]:
    """Check file, then Import the same bytes confirming what it found."""
    preview = _ok(_preview(caller, body))
    return _ok(_commit(caller, body, confirm=preview["update_token"]))


def _entry(report: dict[str, Any], number: str) -> dict[str, Any]:
    return next(entry for entry in report["work_orders"] if entry["work_order_number"] == number)


def _outcomes(report: dict[str, Any]) -> dict[str, str]:
    return {entry["work_order_number"]: entry["outcome"] for entry in report["work_orders"]}


def _count(engine: Engine, table: sa.FromClause) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def _write_counts(engine: Engine) -> dict[str, int]:
    return {
        "work_orders": _count(engine, models.WorkOrder.__table__),
        "work_order_demands": _count(engine, models.WorkOrderDemand.__table__),
        "part_numbers": _count(engine, models.PartNumber.__table__),
        "audit_events": _count(engine, models.AuditEvent.__table__),
        "quantity_flows": _count(engine, models.QuantityFlow.__table__),
        "part_movements": _count(engine, models.PartMovement.__table__),
    }


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar()


def _rows(engine: Engine, sql: str, **params: object) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(sa.text(sql), params)]


def _demand(engine: Engine, demand_id: int) -> dict[str, Any]:
    (row,) = _rows(
        engine,
        "SELECT requested_quantity, due_date, job_numbers, allocated_quantity, priority_rank"
        " FROM work_order_demands WHERE id = :id",
        id=demand_id,
    )
    return row


def _quantity(engine: Engine, demand_id: int) -> int:
    return int(_demand(engine, demand_id)["requested_quantity"])


def _line_count(engine: Engine, work_order: _WorkOrder) -> int:
    return int(
        _scalar(
            engine,
            "SELECT count(*) FROM work_order_demands WHERE work_order_id = :id",
            id=work_order.id,
        )
    )


def _audit_mark(engine: Engine) -> int:
    return int(_scalar(engine, "SELECT coalesce(max(id), 0) FROM audit_events"))


def _audit_since(engine: Engine, mark: int) -> list[dict[str, Any]]:
    return _rows(
        engine,
        "SELECT entity_type, entity_id, event_type, actor_user_id, metadata FROM audit_events"
        " WHERE id > :mark ORDER BY id",
        mark=mark,
    )


def _intake_rows(engine: Engine, demand_id: int) -> int:
    return int(
        _scalar(
            engine,
            "SELECT count(*) FROM audit_events WHERE entity_type = 'WorkOrderDemand'"
            " AND entity_id = :id AND metadata ? 'intake'",
            id=str(demand_id),
        )
    )


class _Seam:
    """Test seam: wrap ``real`` and hold its ``on_call``-th call (counted
    over every thread) until released — before the real call, or after
    it with ``after=True``."""

    def __init__(self, real: Callable[..., Any], *, on_call: int = 1, after: bool = False) -> None:
        self.real = real
        self.on_call = on_call
        self.after = after
        self.inside = threading.Event()
        self.let_go = threading.Event()
        self._guard = threading.Lock()
        self.calls = 0

    def _hold(self) -> None:
        self.inside.set()
        assert self.let_go.wait(timeout=30), "test deadlock: never released"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with self._guard:
            self.calls += 1
            hold = self.calls == self.on_call
        if hold and not self.after:
            self._hold()
        result = self.real(*args, **kwargs)
        if hold and self.after:
            self._hold()
        return result


def _in_thread(target: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["value"] = target()
        except Exception as exc:  # noqa: BLE001 — collected for assertions
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def _identity_headers(identity_client: IdentityClient) -> dict[str, str]:
    assert identity_client.identity is not None
    return {"Cookie": f"partflow_session={identity_client.identity.token}", **CSRF_HEADERS}


def _seamed_commit(
    monkeypatch: pytest.MonkeyPatch,
    caller: IdentityClient,
    body: bytes,
    confirm: str | None,
    concurrent: Callable[[], None],
) -> dict[str, Any]:
    """Commit with the first ``update_work_order`` call held before it
    runs while ``concurrent`` changes the data and commits."""
    seam = _Seam(work_orders.update_work_order)
    monkeypatch.setattr(work_orders, "update_work_order", seam)
    thread, result = _in_thread(lambda: _commit(caller, body, confirm=confirm))
    try:
        assert seam.inside.wait(timeout=20)
        concurrent()
    finally:
        seam.let_go.set()
    thread.join(timeout=60)
    monkeypatch.setattr(work_orders, "update_work_order", seam.real)
    assert "error" not in result, result
    return _ok(result["value"])


def _refusal(report: dict[str, Any], number: str) -> list[dict[str, Any]]:
    entry = _entry(report, number)
    assert entry["outcome"] == "REFUSED", entry
    assert entry["lines"] == [] and entry["changes"] is None
    return cast(list[dict[str, Any]], entry["errors"])


def _general(message: str) -> list[dict[str, Any]]:
    return [{"row": None, "column": None, "message": message}]


# ---------------------------------------------------------------------------
# UP — what an import changes on an existing Work Order
# ---------------------------------------------------------------------------


def test_an_identical_file_changes_nothing(
    client: TestClient, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-1."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 5))
    body = _csv((work_order.number, pn, "5", "", ""))
    before = _write_counts(db_engine)
    preview = _ok(_preview(ewod, body))
    entry = _entry(preview, work_order.number)
    assert (entry["outcome"], entry["existing_status"], entry["work_order_id"]) == (
        "EXISTS",
        "OPEN",
        work_order.id,
    )
    assert (entry["changes"], entry["completes_work_order"], entry["differs_from_file"]) == (
        None,
        None,
        False,
    )
    assert entry["lines_not_in_file"] == [] and entry["lines_without_due_date"] == 0
    assert preview["update_token"] is None and preview["required_permissions"] == []
    result = _ok(_commit(ewod, body))
    assert result["summary"] == {"created": 0, "updated": 0, "existing": 1, "refused": 0}
    assert _write_counts(db_engine) == before


def test_a_raised_quantity_is_applied_only_under_the_confirmation(
    both: IdentityClient, client: TestClient, db_engine: Engine
) -> None:
    """UP-2."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 5))
    (demand_id,) = work_order.demand_ids
    created = _unique("NEW")
    body = _csv((work_order.number, pn, "8", "", ""), (created, _unique("PN"), "1", "", ""))
    preview = _ok(_preview(both, body))
    assert _outcomes(preview) == {work_order.number: "WILL_UPDATE", created: "WILL_CREATE"}
    assert preview["summary"] == {"will_create": 1, "will_update": 1, "existing": 0, "refused": 0}
    assert preview["required_permissions"] == _BOTH_KEYS
    update = _entry(preview, work_order.number)
    assert update["changes"] == [
        {
            "kind": "EDIT_LINE",
            "row": 2,
            "part_number": pn,
            "demand_id": demand_id,
            "new_part_number": False,
            "requested_quantity": {"before": 5, "after": 8},
            "due_date": None,
            "job_numbers": None,
            "leaves_hot_list": False,
        }
    ]
    assert (update["completes_work_order"], update["lines_not_in_file"]) == (False, [])
    assert (update["work_order_id"], update["existing_status"]) == (work_order.id, "OPEN")
    assert (update["differs_from_file"], update["lines_without_due_date"]) == (None, 0)
    token = preview["update_token"]
    assert re.fullmatch(r"[0-9a-f]{64}", token)

    before = _write_counts(db_engine)
    for malformed in ("abc", token.upper(), token + "0", ""):
        response = _commit(both, body, confirm=malformed)
        assert (response.status_code, response.json()) == (422, {"detail": _C5}), malformed
    assert _write_counts(db_engine) == before

    unconfirmed = _ok(_commit(both, body))
    assert _outcomes(unconfirmed) == {work_order.number: "REFUSED", created: "CREATED"}
    assert _refusal(unconfirmed, work_order.number) == _general(_U4)
    refused = _entry(unconfirmed, work_order.number)
    assert (refused["work_order_id"], refused["existing_status"]) == (work_order.id, "OPEN")
    assert unconfirmed["update_token"] is None
    assert unconfirmed["required_permissions"] == _BOTH_KEYS
    assert _quantity(db_engine, demand_id) == 5

    second = _unique("NEW")
    body = _csv((work_order.number, pn, "8", "", ""), (second, _unique("PN"), "1", "", ""))
    wrong = _ok(_commit(both, body, confirm="0" * 64))
    assert _outcomes(wrong) == {work_order.number: "REFUSED", second: "CREATED"}
    assert _refusal(wrong, work_order.number) == _general(_U4)
    assert _quantity(db_engine, demand_id) == 5

    recheck = _ok(_preview(both, body))
    assert _outcomes(recheck) == {work_order.number: "WILL_UPDATE", second: "EXISTS"}
    assert recheck["required_permissions"] == ["EDIT_WORK_ORDER_DEMAND"]
    mark = _audit_mark(db_engine)
    applied = _ok(_commit(both, body, confirm=recheck["update_token"]))
    assert _outcomes(applied) == {work_order.number: "UPDATED", second: "EXISTS"}
    assert applied["summary"] == {"created": 0, "updated": 1, "existing": 1, "refused": 0}
    entry = _entry(applied, work_order.number)
    assert (entry["existing_status"], entry["changes"]) == ("OPEN", update["changes"])
    assert _quantity(db_engine, demand_id) == 8
    assert _audit_since(db_engine, mark) == [
        {
            "entity_type": "WorkOrderDemand",
            "entity_id": str(demand_id),
            "event_type": "UPDATED",
            "actor_user_id": both.user_id,
            "metadata": _INTAKE,
        }
    ]

    replay_counts = _write_counts(db_engine)
    replay = _checked(both, body)
    assert _outcomes(replay) == {work_order.number: "EXISTS", second: "EXISTS"}
    assert _write_counts(db_engine) == replay_counts


def test_a_new_part_number_adds_a_line_to_an_open_work_order(
    client: TestClient, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-3 (and AZ-U1: an Edit Work Order Demand holder creates the
    first-use master as the save's actor)."""
    pn, first_use = _unique("PN"), _unique("FIRST")
    work_order = _seed(client, (pn, 5))
    body = _csv(
        (work_order.number, pn, "5", "", ""),
        (work_order.number, first_use, "3", "JOB-3", "2026-12-01"),
    )
    preview = _ok(_preview(ewod, body))
    entry = _entry(preview, work_order.number)
    assert entry["outcome"] == "WILL_UPDATE"
    assert entry["new_part_numbers"] == [first_use]
    assert entry["changes"] == [
        {
            "kind": "ADD_LINE",
            "row": 3,
            "part_number": first_use,
            "demand_id": None,
            "new_part_number": True,
            "requested_quantity": {"before": None, "after": 3},
            "due_date": {"before": None, "after": "2026-12-01"},
            "job_numbers": {"before": [], "after": ["JOB-3"]},
            "leaves_hot_list": False,
        }
    ]
    mark = _audit_mark(db_engine)
    result = _ok(_commit(ewod, body, confirm=preview["update_token"]))
    assert _entry(result, work_order.number)["outcome"] == "UPDATED"
    assert _entry(result, work_order.number)["existing_status"] == "OPEN"
    detail = admin_of(client).get(f"/api/work-orders/{work_order.id}").json()
    assert detail["status"] == "OPEN"
    added = detail["demands"][1]
    assert (
        added["part_number"],
        added["request_type"],
        added["requested_quantity"],
        added["due_date"],
        added["job_numbers"],
    ) == (first_use, "NEW", 3, "2026-12-01", ["JOB-3"])
    assert _audit_since(db_engine, mark) == [
        {
            "entity_type": "PartNumber",
            "entity_id": first_use,
            "event_type": "CREATED",
            "actor_user_id": ewod.user_id,
            "metadata": None,
        },
        {
            "entity_type": "WorkOrderDemand",
            "entity_id": str(added["id"]),
            "event_type": "CREATED",
            "actor_user_id": ewod.user_id,
            "metadata": _INTAKE,
        },
    ]


def test_a_released_work_order_accepts_no_new_line(
    client: TestClient, shop: _Shop, both: IdentityClient, db_engine: Engine
) -> None:
    """UP-4."""
    pn, other_pn = _unique("PN"), _unique("PN")
    released = _seed(client, (pn, 4))
    _release(client, shop, released, released.demand_ids[0], pn, 4)
    other = _seed(client, (other_pn, 2))
    new_pn = _unique("NEW")
    body = _csv(
        (released.number, pn, "4", "", ""),
        (released.number, new_pn, "1", "", ""),
        (other.number, other_pn, "3", "", ""),
    )
    u1 = [
        {
            "row": 3,
            "column": "Part Number",
            "message": f"Part Number {new_pn} is not on this Work Order, and the Work Order is"
            " Released: lines can be added only while it is Open. Remove this row from the file.",
        }
    ]
    preview = _ok(_preview(both, body))
    assert _refusal(preview, released.number) == u1
    entry = _entry(preview, released.number)
    assert (entry["work_order_id"], entry["existing_status"]) == (released.id, "RELEASED")
    assert entry["new_part_numbers"] == [] and entry["lines_not_in_file"] is None
    assert _entry(preview, other.number)["outcome"] == "WILL_UPDATE"
    result = _ok(_commit(both, body, confirm=preview["update_token"]))
    assert _outcomes(result) == {released.number: "REFUSED", other.number: "UPDATED"}
    assert _refusal(result, released.number) == u1
    assert _line_count(db_engine, released) == 1
    assert (
        _scalar(db_engine, "SELECT count(*) FROM part_numbers WHERE part_number = :pn", pn=new_pn)
        == 0
    )


def test_lowering_below_the_released_quantity_is_refused(
    client: TestClient, shop: _Shop, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-5."""
    pn, other_pn = _unique("PN"), _unique("PN")
    lowered = _seed(client, (pn, 10))
    _release(client, shop, lowered, lowered.demand_ids[0], pn, 6)
    other = _seed(client, (other_pn, 2))
    body = _csv((lowered.number, pn, "4", "", ""), (other.number, other_pn, "3", "", ""))
    u2 = [
        {
            "row": 2,
            "column": "Requested Quantity",
            "message": f"Cannot lower Qty to 4 pcs for Part Number '{pn}': 6 pcs are already"
            " released. Enter 6 pcs or more.",
        }
    ]
    preview = _ok(_preview(ewod, body))
    assert _refusal(preview, lowered.number) == u2
    assert _entry(preview, lowered.number)["existing_status"] == "OPEN"
    mark = _audit_mark(db_engine)
    result = _ok(_commit(ewod, body, confirm=preview["update_token"]))
    assert _outcomes(result) == {lowered.number: "REFUSED", other.number: "UPDATED"}
    assert _quantity(db_engine, lowered.demand_ids[0]) == 10
    assert [row["entity_id"] for row in _audit_since(db_engine, mark)] == [str(other.demand_ids[0])]


def test_a_ranked_line_lowered_to_its_allocated_quantity_leaves_the_hot_list(
    client: TestClient, shop: _Shop, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-6."""
    pn, short_pn = _unique("PN"), _unique("PN")
    work_order = _seed(client, (pn, 10), (short_pn, 5))
    hot = work_order.demand_ids[0]
    last = _seed(client, (_unique("PN"), 3)).demand_ids[0]
    _allocated(client, shop, work_order, hot, pn, 4)
    _hot_add(client, hot, last)
    order = _hot_order(client)
    body = _csv((work_order.number, pn, "4", "", ""), (work_order.number, short_pn, "5", "", ""))
    preview = _ok(_preview(ewod, body))
    entry = _entry(preview, work_order.number)
    assert [change["leaves_hot_list"] for change in entry["changes"]] == [True]
    assert entry["completes_work_order"] is False
    mark = _audit_mark(db_engine)
    result = _ok(_commit(ewod, body, confirm=preview["update_token"]))
    assert _entry(result, work_order.number)["outcome"] == "UPDATED"
    assert _hot_order(client) == [demand for demand in order if demand != hot]
    assert _demand(db_engine, hot)["priority_rank"] is None
    hot_rows = [
        row
        for row in _audit_since(db_engine, mark)
        if row["metadata"] is not None and "hot_list_change" in row["metadata"]
    ]
    assert hot_rows and {row["actor_user_id"] for row in hot_rows} == {ewod.user_id}
    for row in hot_rows:
        block = row["metadata"]["hot_list_change"]
        assert block["action"] == "AUTO_REMOVE"
        assert block["cause"]["trigger"] == "WORK_ORDER_SAVE"
        assert block["cause"]["removed"] == [
            {"work_order_demand_id": hot, "reason": "FULLY_ALLOCATED"}
        ]
        assert "intake" not in row["metadata"]
    assert _rows(
        db_engine,
        "SELECT metadata FROM audit_events WHERE id > :mark AND entity_id = :id"
        " AND entity_type = 'WorkOrderDemand' AND metadata ? 'intake'",
        mark=mark,
        id=str(hot),
    ) == [{"metadata": _INTAKE}]


def test_a_completing_update_completes_the_work_order(
    client: TestClient, shop: _Shop, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-7."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 10))
    (demand_id,) = work_order.demand_ids
    _allocated(client, shop, work_order, demand_id, pn, 4)
    body = _csv((work_order.number, pn, "4", "", ""))
    preview = _ok(_preview(ewod, body))
    assert _entry(preview, work_order.number)["completes_work_order"] is True
    mark = _audit_mark(db_engine)
    result = _ok(_commit(ewod, body, confirm=preview["update_token"]))
    entry = _entry(result, work_order.number)
    assert (entry["outcome"], entry["existing_status"]) == ("UPDATED", "COMPLETED")
    assert (
        _scalar(db_engine, "SELECT completed_at FROM work_orders WHERE id = :id", id=work_order.id)
        is not None
    )
    work_order_rows = [
        row for row in _audit_since(db_engine, mark) if row["entity_type"] == "WorkOrder"
    ]
    assert work_order_rows == [
        {
            "entity_type": "WorkOrder",
            "entity_id": str(work_order.id),
            "event_type": "UPDATED",
            "actor_user_id": ewod.user_id,
            "metadata": {"completion": {"trigger": "WORK_ORDER_SAVE"}},
        }
    ]
    assert _intake_rows(db_engine, demand_id) == 1

    counts = _write_counts(db_engine)
    replay = _checked(ewod, body)
    entry = _entry(replay, work_order.number)
    assert (entry["outcome"], entry["existing_status"]) == ("EXISTS", "COMPLETED")
    assert _write_counts(db_engine) == counts


def test_due_dates_and_job_numbers_are_set_and_blank_cells_keep(
    client: TestClient, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-8."""
    pn, kept_pn = _unique("PN"), _unique("PN")
    work_order = _seed(
        client,
        {
            "part_number": pn,
            "requested_quantity": 5,
            "due_date": "2026-10-01",
            "job_numbers": ["J1"],
        },
        {
            "part_number": kept_pn,
            "requested_quantity": 6,
            "due_date": "2026-10-02",
            "job_numbers": ["J2"],
        },
    )
    edited, kept = work_order.demand_ids
    body = _csv(
        (work_order.number, pn, "5", "J2", "2026-11-11"), (work_order.number, kept_pn, "6", "", "")
    )
    preview = _ok(_preview(ewod, body))
    (change,) = _entry(preview, work_order.number)["changes"]
    assert (change["requested_quantity"], change["due_date"], change["job_numbers"]) == (
        None,
        {"before": "2026-10-01", "after": "2026-11-11"},
        {"before": ["J1"], "after": ["J1", "J2"]},
    )
    _ok(_commit(ewod, body, confirm=preview["update_token"]))
    assert (_demand(db_engine, edited)["due_date"], _demand(db_engine, edited)["job_numbers"]) == (
        datetime.date(2026, 11, 11),
        ["J1", "J2"],
    )
    assert (_demand(db_engine, kept)["due_date"], _demand(db_engine, kept)["job_numbers"]) == (
        datetime.date(2026, 10, 2),
        ["J2"],
    )
    counts = _write_counts(db_engine)
    assert _outcomes(_checked(ewod, body)) == {work_order.number: "EXISTS"}
    assert _write_counts(db_engine) == counts


def test_a_saved_line_missing_from_the_file_is_kept(
    client: TestClient, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-9."""
    pns = [_unique("PN") for _ in range(3)]
    work_order = _seed(client, (pns[0], 1), (pns[1], 2), (pns[2], 3))
    body = _csv((work_order.number, pns[1], "5", "", ""))
    preview = _ok(_preview(ewod, body))
    assert _entry(preview, work_order.number)["lines_not_in_file"] == [pns[0], pns[2]]
    result = _ok(_commit(ewod, body, confirm=preview["update_token"]))
    assert _entry(result, work_order.number)["lines_not_in_file"] == [pns[0], pns[2]]
    assert _line_count(db_engine, work_order) == 3
    kept = _ok(_preview(ewod, body))
    entry = _entry(kept, work_order.number)
    assert (entry["outcome"], entry["differs_from_file"]) == ("EXISTS", True)
    assert entry["lines_not_in_file"] == [pns[0], pns[2]]


def test_a_completed_work_order_is_never_changed(
    client: TestClient, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-10."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 2))
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE work_orders SET completed_at = now() WHERE id = :id"),
            {"id": work_order.id},
        )
    body = _csv(
        (work_order.number, pn, "9", "", ""), (work_order.number, _unique("PN"), "1", "", "")
    )
    before = _write_counts(db_engine)
    preview = _ok(_preview(ewod, body))
    entry = _entry(preview, work_order.number)
    assert (entry["outcome"], entry["existing_status"], entry["differs_from_file"]) == (
        "EXISTS",
        "COMPLETED",
        True,
    )
    assert (entry["changes"], entry["lines_not_in_file"], entry["lines_without_due_date"]) == (
        None,
        None,
        0,
    )
    assert preview["update_token"] is None and preview["required_permissions"] == []
    assert _outcomes(_ok(_commit(ewod, body))) == {work_order.number: "EXISTS"}
    assert _write_counts(db_engine) == before


def test_a_mixed_file(client: TestClient, both: IdentityClient, db_engine: Engine) -> None:
    """UP-11."""
    raised_pn, added_to_pn, existing_pn = _unique("PN"), _unique("PN"), _unique("PN")
    raised = _seed(client, (raised_pn, 1))
    added_to = _seed(client, (added_to_pn, 1))
    existing = _seed(client, (existing_pn, 1))
    shared, dated, other_new = _unique("SHARED"), _unique("DATED"), _unique("OTHER")
    create_one, create_two, refused = _unique("C1"), _unique("C2"), _unique("R")
    body = _csv(
        (raised.number, raised_pn, "2", "", ""),
        (added_to.number, added_to_pn, "1", "", ""),
        (added_to.number, shared, "1", "", ""),
        (added_to.number, dated, "1", "", "2026-12-01"),
        (create_one, shared, "1", "", ""),
        (create_one, other_new, "1", "", "2026-12-01"),
        (existing.number, existing_pn, "1", "", ""),
        (refused, _unique("PN"), "0", "", ""),
        (create_two, _unique("TWO"), "1", "", ""),
    )
    numbers = [raised.number, added_to.number, create_one, existing.number, refused, create_two]
    preview = _ok(_preview(both, body))
    assert [entry["work_order_number"] for entry in preview["work_orders"]] == numbers
    assert preview["summary"] == {"will_create": 2, "will_update": 2, "existing": 1, "refused": 1}
    assert _entry(preview, added_to.number)["new_part_numbers"] == [shared, dated]
    assert _entry(preview, create_one)["new_part_numbers"] == [other_new]
    assert [entry["lines_without_due_date"] for entry in preview["work_orders"]] == [
        0,
        1,
        1,
        0,
        0,
        1,
    ]
    assert preview["lines_without_due_date"] == 3
    result = _ok(_commit(both, body, confirm=preview["update_token"]))
    assert [entry["work_order_number"] for entry in result["work_orders"]] == numbers
    assert result["summary"] == {"created": 2, "updated": 2, "existing": 1, "refused": 1}
    assert result["lines_without_due_date"] == 3
    assert _line_count(db_engine, added_to) == 3


def test_unassigned_rows_block_before_the_content_permission(
    client: TestClient, ewod: IdentityClient, db_engine: Engine
) -> None:
    """UP-12."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 1))
    body = _csv(
        (work_order.number, pn, "9", "", ""),
        (_unique("NEW"), _unique("PN"), "1", "", ""),
        ("", _unique("PN"), "1", "", ""),
    )
    before = _write_counts(db_engine)
    response = _commit(ewod, body)
    assert (response.status_code, response.json()) == (
        422,
        {
            "detail": "1 row has no usable Work Order Number. Add or fix it, or delete those"
            " rows, then check the file again."
        },
    )
    assert _write_counts(db_engine) == before


def test_reconciliation_stays_clean_after_update_imports(
    client: TestClient,
    shop: _Shop,
    ewod: IdentityClient,
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """UP-13."""
    raised_pn, added_pn, first_use = _unique("PN"), _unique("PN"), _unique("FIRST")
    hot_pn, hot_short_pn, done_pn = _unique("PN"), _unique("PN"), _unique("PN")
    raised = _seed(client, (raised_pn, 3))
    added = _seed(client, (added_pn, 3))
    hot_work_order = _seed(client, (hot_pn, 10), (hot_short_pn, 5))
    hot = hot_work_order.demand_ids[0]
    _allocated(client, shop, hot_work_order, hot, hot_pn, 4)
    _hot_add(client, hot)
    done = _seed(client, (done_pn, 10))
    _allocated(client, shop, done, done.demand_ids[0], done_pn, 6)
    body = _csv(
        (raised.number, raised_pn, "7", "", ""),
        (added.number, added_pn, "3", "", ""),
        (added.number, first_use, "2", "", ""),
        (hot_work_order.number, hot_pn, "4", "", ""),
        (done.number, done_pn, "6", "", ""),
    )
    result = _checked(ewod, body)
    assert set(_outcomes(result).values()) == {"UPDATED"}
    assert _entry(result, done.number)["existing_status"] == "COMPLETED"
    work_orders_ = [raised, added, hot_work_order, done]
    work_order_ids = {work_order.id for work_order in work_orders_}
    demand_ids = {
        int(row["id"])
        for row in _rows(
            db_engine,
            "SELECT id FROM work_order_demands WHERE work_order_id = ANY(:ids)",
            ids=list(work_order_ids),
        )
    }
    pns = {raised_pn, added_pn, first_use, hot_pn, hot_short_pn, done_pn}
    get_settings.cache_clear()
    capsys.readouterr()
    try:
        exit_code = cli.main(
            ["reconcile", "--check", "e", "--check", "f", "--check", "i", "--check", "j"]
        )
    finally:
        get_settings.cache_clear()
    report = json.loads(capsys.readouterr().out)
    assert exit_code in (0, 1), report
    named = {("WorkOrder", wo) for wo in work_order_ids} | {
        ("WorkOrderDemand", demand) for demand in demand_ids
    }
    findings = [
        finding
        for check in report["checks"]
        for finding in check["findings"]
        if (finding["entity"]["type"], finding["entity"]["id"]) in named
        or finding["part_number"] in pns
    ]
    assert findings == []


# ---------------------------------------------------------------------------
# WP — the write path's two new keywords
# ---------------------------------------------------------------------------


def _lockable(engine: Engine, work_order_id: int) -> bool:
    with engine.connect() as connection:
        try:
            connection.execute(
                sa.text("SELECT id FROM work_orders WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": work_order_id},
            )
        except sa.exc.OperationalError:
            return False
        finally:
            connection.rollback()
    return True


def test_the_manual_save_is_unchanged_and_the_state_token_guards_the_import(
    client: TestClient, db_engine: Engine
) -> None:
    """WP-1."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 5))
    (demand_id,) = work_order.demand_ids
    mark = _audit_mark(db_engine)
    saved = admin_of(client).patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": demand_id, "requested_quantity": 6}]},
    )
    assert saved.status_code == 200, saved.text
    assert [(row["entity_type"], row["metadata"]) for row in _audit_since(db_engine, mark)] == [
        ("WorkOrderDemand", None)
    ]

    actor = admin_of(client).user_id
    with Session(db_engine) as session:
        state = work_orders.read_work_order_state(session, work_order.id)
        session.rollback()
        right = work_orders.work_order_state_token(state, {demand_id})
        before = _write_counts(db_engine)
        with pytest.raises(ConflictError) as stale:
            work_orders.update_work_order(
                session,
                work_order.id,
                line_edits=[{"id": demand_id, "requested_quantity": 7}],
                actor_user_id=actor,
                expected_state_token="0" * 64,
            )
        assert stale.value.message == _U3
        session.rollback()
        assert _lockable(db_engine, work_order.id)
        with pytest.raises(RuntimeError):
            work_orders.update_work_order(
                session,
                work_order.id,
                line_edits=[{"id": demand_id, "requested_quantity": 6}],
                actor_user_id=actor,
                expected_state_token=right,
            )
        assert _lockable(db_engine, work_order.id)
        assert _write_counts(db_engine) == before
    assert _quantity(db_engine, demand_id) == 6


# ---------------------------------------------------------------------------
# CC-U — concurrency
# ---------------------------------------------------------------------------


def _two_raised(client: TestClient) -> tuple[_WorkOrder, _WorkOrder, str, str, bytes]:
    first_pn, second_pn = _unique("PN"), _unique("PN")
    first, second = _seed(client, (first_pn, 5)), _seed(client, (second_pn, 5))
    body = _csv((first.number, first_pn, "8", "", ""), (second.number, second_pn, "8", "", ""))
    return first, second, first_pn, second_pn, body


def test_a_manual_change_after_the_check_refuses_every_update(
    client: TestClient, both: IdentityClient, db_engine: Engine
) -> None:
    """CC-U1."""
    first_pn, second_pn = _unique("PN"), _unique("PN")
    first, second = _seed(client, (first_pn, 5)), _seed(client, (second_pn, 5))
    created = _unique("NEW")
    body = _csv(
        (first.number, first_pn, "8", "", ""),
        (second.number, second_pn, "8", "", ""),
        (created, _unique("PN"), "1", "", ""),
    )
    token = _ok(_preview(both, body))["update_token"]
    patched = admin_of(client).patch(
        f"/api/work-orders/{first.id}",
        json={"line_edits": [{"id": first.demand_ids[0], "requested_quantity": 6}]},
    )
    assert patched.status_code == 200, patched.text
    result = _ok(_commit(both, body, confirm=token))
    assert _outcomes(result) == {
        first.number: "REFUSED",
        second.number: "REFUSED",
        created: "CREATED",
    }
    for number in (first.number, second.number):
        assert _refusal(result, number) == _general(_U4)
    assert _quantity(db_engine, first.demand_ids[0]) == 6
    assert _quantity(db_engine, second.demand_ids[0]) == 5
    assert _intake_rows(db_engine, first.demand_ids[0]) == 0


def test_a_change_after_the_commit_plan_refuses_that_work_order(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CC-U2."""
    first, second, _, _, body = _two_raised(client)
    token = _ok(_preview(both, body))["update_token"]

    def manual_job_number() -> None:
        patched = admin_of(client).patch(
            f"/api/work-orders/{first.id}",
            json={"line_edits": [{"id": first.demand_ids[0], "job_numbers": ["MANUAL"]}]},
        )
        assert patched.status_code == 200, patched.text

    result = _seamed_commit(monkeypatch, both, body, token, manual_job_number)
    assert _outcomes(result) == {first.number: "REFUSED", second.number: "UPDATED"}
    assert _refusal(result, first.number) == _general(_U3)
    assert _entry(result, first.number)["work_order_id"] == first.id
    line = _demand(db_engine, first.demand_ids[0])
    assert (line["requested_quantity"], line["job_numbers"]) == (5, ["MANUAL"])
    assert _quantity(db_engine, second.demand_ids[0]) == 8


def test_a_release_raising_the_floor_after_the_plan_refuses_the_lowering(
    client: TestClient,
    shop: _Shop,
    both: IdentityClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-U3."""
    pn, other_pn = _unique("PN"), _unique("PN")
    lowered, other = _seed(client, (pn, 10)), _seed(client, (other_pn, 5))
    body = _csv((lowered.number, pn, "4", "", ""), (other.number, other_pn, "8", "", ""))
    token = _ok(_preview(both, body))["update_token"]

    def release() -> None:
        _release(client, shop, lowered, lowered.demand_ids[0], pn, 6)

    result = _seamed_commit(monkeypatch, both, body, token, release)
    assert _outcomes(result) == {lowered.number: "REFUSED", other.number: "UPDATED"}
    assert _refusal(result, lowered.number) == _general(
        f"Cannot lower Qty to 4 pcs for Part Number '{pn}': 6 pcs are already released."
        " Enter 6 pcs or more."
    )
    assert _quantity(db_engine, lowered.demand_ids[0]) == 10


def test_a_deleted_line_after_the_plan_refuses_that_work_order(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CC-U4."""
    pn, deleted_pn, other_pn = _unique("PN"), _unique("PN"), _unique("PN")
    edited = _seed(client, (pn, 5), (deleted_pn, 5))
    other = _seed(client, (other_pn, 5))
    deleted = edited.demand_ids[1]
    body = _csv(
        (edited.number, pn, "5", "", ""),
        (edited.number, deleted_pn, "6", "", ""),
        (other.number, other_pn, "8", "", ""),
    )
    token = _ok(_preview(both, body))["update_token"]

    def delete_line() -> None:
        response = admin_of(client).delete(f"/api/work-orders/{edited.id}/demands/{deleted}")
        assert response.status_code == 204, response.text

    result = _seamed_commit(monkeypatch, both, body, token, delete_line)
    assert _outcomes(result) == {edited.number: "REFUSED", other.number: "UPDATED"}
    assert _refusal(result, edited.number) == _general(
        f"Demand line {deleted} does not exist on Work Order {edited.id}."
    )
    assert _line_count(db_engine, edited) == 1


def test_a_refused_update_releases_its_locks_during_the_import(
    client: TestClient,
    shop: _Shop,
    both: IdentityClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-U5: group 1 is forced stale (U3); while group 2 is held inside
    its PN locks, a receipt of group 1's PN and a Hot list move answer."""
    first, second, first_pn, _, body = _two_raised(client)
    ranked = [_seed(client, (_unique("PN"), 2)).demand_ids[0] for _ in range(2)]
    _hot_add(client, *ranked)
    token = _ok(_preview(both, body))["update_token"]

    real_update = work_orders.update_work_order
    forced: list[bool] = []

    def stale_first(session: Session, work_order_id: int, **kwargs: Any) -> Any:
        if not forced:
            forced.append(True)
            kwargs["expected_state_token"] = "0" * 64
        return real_update(session, work_order_id, **kwargs)

    monkeypatch.setattr(work_orders, "update_work_order", stale_first)
    pause = _Seam(part_numbers.acquire_part_number_locks, on_call=2, after=True)
    monkeypatch.setattr(work_orders, "acquire_part_number_locks", pause)
    headers = {
        **_identity_headers(both),
        "Content-Type": _CSV,
        _CHECK: _token(body),
        _CONFIRM: token,
    }
    thread, result = _in_thread(lambda: client.post(_IMPORT, content=body, headers=headers))
    try:
        assert pause.inside.wait(timeout=20)
        started = time.monotonic()
        receipt = station_device_client(client).post(
            f"/api/scan-stations/{shop.material.station_id}/receipts",
            json={
                "part_number": first_pn,
                "quantity": 2,
                "request_type": "MODIFY",
                "route_mode": "FLOATING",
                "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "device_event_id": str(uuid.uuid4()),
            },
        )
        assert time.monotonic() - started < 2
        # Judged under the PN lock group 1 held: the PN has active demand.
        assert receipt.status_code == 409, receipt.text
        assert "now has active Work Order Demand" in receipt.json()["detail"]
        order = _hot_order(client)
        moved = [*order]
        index = moved.index(ranked[1])
        moved[index - 1], moved[index] = moved[index], moved[index - 1]
        started = time.monotonic()
        move = _hot_change(client, "DRAG", moved)
        assert time.monotonic() - started < 2
        assert move.status_code == 201, move.text
        assert "value" not in result  # the import is still held
    finally:
        pause.let_go.set()
    thread.join(timeout=60)
    report = _ok(result["value"])
    assert _outcomes(report) == {first.number: "REFUSED", second.number: "UPDATED"}
    assert _refusal(report, first.number) == _general(_U3)
    assert _quantity(db_engine, first.demand_ids[0]) == 5


def test_two_importers_update_each_work_order_once(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CC-U6."""
    first, second, _, _, body = _two_raised(client)
    token = _ok(_preview(both, body))["update_token"]

    def other_importer() -> None:
        report = _ok(_commit(both, body, confirm=token))
        assert _outcomes(report) == {first.number: "UPDATED", second.number: "UPDATED"}

    held = _seamed_commit(monkeypatch, both, body, token, other_importer)
    assert _outcomes(held) == {first.number: "REFUSED", second.number: "REFUSED"}
    for number in (first.number, second.number):
        assert _refusal(held, number) == _general(_U3)
    for work_order in (first, second):
        assert _quantity(db_engine, work_order.demand_ids[0]) == 8
        assert _intake_rows(db_engine, work_order.demand_ids[0]) == 1
    recheck = _ok(_preview(both, body))
    assert set(_outcomes(recheck).values()) == {"EXISTS"}

    # The other importer finished before this commit's plan: nothing to
    # update, the stale confirmation is ignored.
    first, second, _, _, body = _two_raised(client)
    token = _ok(_preview(both, body))["update_token"]
    _checked(both, body)
    counts = _write_counts(db_engine)
    late = _ok(_commit(both, body, confirm=token))
    assert set(_outcomes(late).values()) == {"EXISTS"}
    assert _write_counts(db_engine) == counts


def test_an_allocation_of_an_unedited_line_after_the_plan_refuses_the_update(
    client: TestClient,
    shop: _Shop,
    both: IdentityClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-U7 (stale)."""
    edited_pn, unedited_pn = _unique("PN"), _unique("PN")
    work_order = _seed(client, (edited_pn, 10), (unedited_pn, 5))
    unedited = work_order.demand_ids[1]
    _stocked(client, shop, work_order, unedited, unedited_pn, 2)
    body = _csv(
        (work_order.number, edited_pn, "12", "", ""), (work_order.number, unedited_pn, "5", "", "")
    )
    token = _ok(_preview(both, body))["update_token"]

    def allocate() -> None:
        response = _allocate_response(client, unedited_pn, unedited, 2)
        assert response.status_code == 201, response.text

    result = _seamed_commit(monkeypatch, both, body, token, allocate)
    assert _refusal(result, work_order.number) == _general(_U3)
    assert _quantity(db_engine, work_order.demand_ids[0]) == 10


def test_an_allocation_waits_for_the_import_and_neither_deadlocks(
    client: TestClient,
    shop: _Shop,
    both: IdentityClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-U7 (lock wait)."""
    edited_pn, unedited_pn = _unique("PN"), _unique("PN")
    work_order = _seed(client, (edited_pn, 10), (unedited_pn, 5))
    edited, unedited = work_order.demand_ids
    _stocked(client, shop, work_order, unedited, unedited_pn, 2)
    body = _csv(
        (work_order.number, edited_pn, "12", "", ""), (work_order.number, unedited_pn, "5", "", "")
    )
    token = _ok(_preview(both, body))["update_token"]
    held = _Seam(work_orders._require_active, after=True)
    monkeypatch.setattr(work_orders, "_require_active", held)
    importer, imported = _in_thread(lambda: _commit(both, body, confirm=token))
    try:
        assert held.inside.wait(timeout=20)
        allocator, allocated = _in_thread(
            lambda: _allocate_response(client, unedited_pn, unedited, 2)
        )
        allocator.join(timeout=1)
        assert allocator.is_alive(), allocated  # waits on the import's locks
    finally:
        held.let_go.set()
    importer.join(timeout=60)
    allocator.join(timeout=60)
    assert _outcomes(_ok(imported["value"])) == {work_order.number: "UPDATED"}
    assert allocated["value"].status_code == 201, allocated["value"].text
    assert _quantity(db_engine, edited) == 12
    assert _demand(db_engine, unedited)["allocated_quantity"] == 2


def test_a_change_while_the_import_waits_for_its_locks_refuses_that_work_order(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CC-U2 (lock wait): the state token is compared under the save's
    locks. The import is held inside ``update_work_order`` after its
    unlocked read and before its first lock is requested; a manual save
    of the very Job Numbers it plans to append commits there. A token
    compared before the locks would pass and overwrite the manual value."""
    pn, other_pn = _unique("PN"), _unique("PN")
    edited = _seed(client, {"part_number": pn, "requested_quantity": 5, "job_numbers": ["J1"]})
    other = _seed(client, (other_pn, 5))
    (demand_id,) = edited.demand_ids
    body = _csv((edited.number, pn, "5", "J2", ""), (other.number, other_pn, "8", "", ""))
    token = _ok(_preview(both, body))["update_token"]
    pause = _Seam(part_numbers.acquire_part_number_locks)
    monkeypatch.setattr(work_orders, "acquire_part_number_locks", pause)
    thread, result = _in_thread(lambda: _commit(both, body, confirm=token))
    try:
        assert pause.inside.wait(timeout=20)
        # The manual save runs the same seam (call 2: not held).
        patched = admin_of(client).patch(
            f"/api/work-orders/{edited.id}",
            json={"line_edits": [{"id": demand_id, "job_numbers": ["J1", "MANUAL"]}]},
        )
        assert patched.status_code == 200, patched.text
        assert "value" not in result  # the import is still held
    finally:
        pause.let_go.set()
    thread.join(timeout=60)
    assert "error" not in result, result
    report = _ok(result["value"])
    assert _outcomes(report) == {edited.number: "REFUSED", other.number: "UPDATED"}
    assert _refusal(report, edited.number) == _general(_U3)
    assert _demand(db_engine, demand_id)["job_numbers"] == ["J1", "MANUAL"]
    assert _intake_rows(db_engine, demand_id) == 0
    assert _quantity(db_engine, other.demand_ids[0]) == 8


def test_an_allocation_waits_on_the_work_order_lock_of_an_import_without_the_hot_lock(
    client: TestClient,
    shop: _Shop,
    both: IdentityClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-U7 (Work Order lock): the import edits only a Job Number, so it
    takes no Hot lock, and the allocation of an UNEDITED line of the same
    Work Order needs none of the import's PN or demand locks — it can
    only wait on the import's Work Order row lock."""
    edited_pn, unedited_pn = _unique("PN"), _unique("PN")
    work_order = _seed(client, (edited_pn, 10), (unedited_pn, 5))
    edited, unedited = work_order.demand_ids
    _stocked(client, shop, work_order, unedited, unedited_pn, 2)
    body = _csv(
        (work_order.number, edited_pn, "10", "J9", ""),
        (work_order.number, unedited_pn, "5", "", ""),
    )
    token = _ok(_preview(both, body))["update_token"]
    held = _Seam(work_orders._require_active, after=True)
    monkeypatch.setattr(work_orders, "_require_active", held)
    importer, imported = _in_thread(lambda: _commit(both, body, confirm=token))
    try:
        assert held.inside.wait(timeout=20)
        allocator, allocated = _in_thread(
            lambda: _allocate_response(client, unedited_pn, unedited, 2)
        )
        allocator.join(timeout=1)
        assert allocator.is_alive(), allocated  # waits on the Work Order row lock
    finally:
        held.let_go.set()
    importer.join(timeout=60)
    allocator.join(timeout=60)
    assert "error" not in imported and "error" not in allocated, (imported, allocated)
    assert _outcomes(_ok(imported["value"])) == {work_order.number: "UPDATED"}
    assert allocated["value"].status_code == 201, allocated["value"].text
    assert _demand(db_engine, edited)["job_numbers"] == ["J9"]
    assert _demand(db_engine, unedited)["allocated_quantity"] == 2


def test_hot_list_changes_after_the_plan(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CC-U8: only the rank of a quantity-edited line is relied on."""

    def planned() -> tuple[_WorkOrder, bytes, str]:
        edited_pn, unedited_pn = _unique("PN"), _unique("PN")
        work_order = _seed(client, (edited_pn, 10), (unedited_pn, 5))
        body = _csv(
            (work_order.number, edited_pn, "12", "", ""),
            (work_order.number, unedited_pn, "5", "", ""),
        )
        return work_order, body, _ok(_preview(both, body))["update_token"]

    def insert(demand_id: int) -> Callable[[], None]:
        def run() -> None:
            response = _hot_change(client, "ADD", [*_hot_order(client), demand_id])
            assert response.status_code == 201, response.text

        return run

    work_order, body, token = planned()
    result = _seamed_commit(monkeypatch, both, body, token, insert(work_order.demand_ids[1]))
    assert _outcomes(result) == {work_order.number: "UPDATED"}

    work_order, body, _ = planned()
    other = _seed(client, (_unique("PN"), 2)).demand_ids[0]
    _hot_add(client, work_order.demand_ids[0], other)
    token = _ok(_preview(both, body))["update_token"]

    def move() -> None:
        order = _hot_order(client)
        moved = [demand for demand in order if demand != other]
        moved.insert(moved.index(work_order.demand_ids[0]), other)
        response = _hot_change(client, "DRAG", moved)
        assert response.status_code == 201, response.text

    result = _seamed_commit(monkeypatch, both, body, token, move)
    assert _outcomes(result) == {work_order.number: "UPDATED"}

    work_order, body, token = planned()
    result = _seamed_commit(monkeypatch, both, body, token, insert(work_order.demand_ids[0]))
    assert _refusal(result, work_order.number) == _general(_U3)
    assert _quantity(db_engine, work_order.demand_ids[0]) == 10


def test_a_renamed_work_order_is_never_changed_by_the_file(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CC-U9."""
    first, second, _, _, body = _two_raised(client)
    token = _ok(_preview(both, body))["update_token"]
    renamed = _unique("RENAMED")

    def rename() -> None:
        response = admin_of(client).patch(
            f"/api/work-orders/{first.id}", json={"work_order_number": renamed}
        )
        assert response.status_code == 200, response.text

    result = _seamed_commit(monkeypatch, both, body, token, rename)
    assert _refusal(result, first.number) == _general(_U3)
    assert _quantity(db_engine, first.demand_ids[0]) == 5
    assert (
        _scalar(db_engine, "SELECT work_order_number FROM work_orders WHERE id = :id", id=first.id)
        == renamed
    )


def test_a_release_completing_the_release_before_an_added_line_refuses_it(
    client: TestClient,
    shop: _Shop,
    both: IdentityClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CC-U10."""
    released_pn, open_pn = _unique("PN"), _unique("PN")
    work_order = _seed(client, (released_pn, 3), (open_pn, 4))
    _release(client, shop, work_order, work_order.demand_ids[0], released_pn, 3)
    body = _csv(
        (work_order.number, released_pn, "3", "", ""),
        (work_order.number, open_pn, "4", "", ""),
        (work_order.number, _unique("ADD"), "1", "", ""),
    )
    preview = _ok(_preview(both, body))
    assert _entry(preview, work_order.number)["existing_status"] == "OPEN"

    def release_the_last_line() -> None:
        _release(client, shop, work_order, work_order.demand_ids[1], open_pn, 4)

    result = _seamed_commit(monkeypatch, both, body, preview["update_token"], release_the_last_line)
    assert _refusal(result, work_order.number) == _general(_U3)
    assert _line_count(db_engine, work_order) == 2


# ---------------------------------------------------------------------------
# CR-U — a crash in the middle of an import
# ---------------------------------------------------------------------------


def test_an_import_interrupted_by_a_crash_completes_after_a_new_check(
    client: TestClient, both: IdentityClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-U1."""
    pns = [_unique("PN") for _ in range(3)]
    seeded = [_seed(client, (pn, 5)) for pn in pns]
    body = _csv(
        *((work_order.number, pn, "9", "", "") for work_order, pn in zip(seeded, pns, strict=True))
    )
    token = _ok(_preview(both, body))["update_token"]
    real = work_orders.update_work_order
    calls: list[int] = []

    def crash_on_the_second(session: Session, work_order_id: int, **kwargs: Any) -> Any:
        calls.append(work_order_id)
        if len(calls) == 2:
            raise RuntimeError("simulated crash")
        return real(session, work_order_id, **kwargs)

    monkeypatch.setattr(work_orders, "update_work_order", crash_on_the_second)
    raw = TestClient(client.app, headers=_identity_headers(both), raise_server_exceptions=False)
    crashed = raw.post(
        _IMPORT, content=body, headers={"Content-Type": _CSV, _CHECK: _token(body), _CONFIRM: token}
    )
    assert crashed.status_code == 500
    assert [_quantity(db_engine, work_order.demand_ids[0]) for work_order in seeded] == [9, 5, 5]
    monkeypatch.setattr(work_orders, "update_work_order", real)

    recheck = _ok(_preview(both, body))
    assert [entry["outcome"] for entry in recheck["work_orders"]] == [
        "EXISTS",
        "WILL_UPDATE",
        "WILL_UPDATE",
    ]
    assert recheck["update_token"] != token
    result = _ok(_commit(both, body, confirm=recheck["update_token"]))
    assert [entry["outcome"] for entry in result["work_orders"]] == ["EXISTS", "UPDATED", "UPDATED"]
    for work_order in seeded:
        assert _quantity(db_engine, work_order.demand_ids[0]) == 9
        assert _intake_rows(db_engine, work_order.demand_ids[0]) == 1


# ---------------------------------------------------------------------------
# AZ-U — who may check, and what the content needs
# ---------------------------------------------------------------------------


def test_the_content_decides_which_key_the_import_needs(
    client: TestClient,
    mwo: IdentityClient,
    ewod: IdentityClient,
    both: IdentityClient,
    vpd: IdentityClient,
    db_engine: Engine,
) -> None:
    """AZ-U1."""
    pn = _unique("PN")
    work_order = _seed(client, (pn, 1))
    update_only = _csv((work_order.number, pn, "2", "", ""))
    create_only = _csv((_unique("NEW"), _unique("PN"), "1", "", ""))
    mixed = _csv((work_order.number, pn, "3", "", ""), (_unique("NEW"), _unique("PN"), "1", "", ""))

    def refused(response: httpx.Response, required: list[str]) -> None:
        assert response.status_code == 403, response.text
        assert response.json() == {
            "detail": _A2,
            "permission_denied": True,
            "required_permissions": required,
        }

    before = _write_counts(db_engine)
    refused(_commit(ewod, create_only), ["MANAGE_WORK_ORDERS"])
    mixed_token = _ok(_preview(ewod, mixed))["update_token"]
    refused(_commit(ewod, mixed, confirm=mixed_token), _BOTH_KEYS)
    update_token = _ok(_preview(mwo, update_only))["update_token"]
    refused(_commit(mwo, update_only, confirm=update_token), ["EDIT_WORK_ORDER_DEMAND"])
    assert _write_counts(db_engine) == before

    preview = _ok(_preview(ewod, update_only))
    assert preview["required_permissions"] == ["EDIT_WORK_ORDER_DEMAND"]
    applied = _ok(_commit(ewod, update_only, confirm=preview["update_token"]))
    assert _outcomes(applied) == {work_order.number: "UPDATED"}
    assert applied["required_permissions"] == ["EDIT_WORK_ORDER_DEMAND"]

    preview = _ok(_preview(both, mixed))
    result = _ok(_commit(both, mixed, confirm=preview["update_token"]))
    assert sorted(_outcomes(result).values()) == ["CREATED", "UPDATED"]

    before = _write_counts(db_engine)
    for response in (_preview(vpd, mixed), *(vpd.get(path) for path in _TEMPLATES)):
        assert response.status_code == 403, response.text
        assert response.json() == {
            "detail": _V1,
            "permission_denied": True,
            "required_permissions": _BOTH_KEYS,
            "any_permission": True,
        }
    commit_refusal = {
        "detail": _A2,
        "permission_denied": True,
        "required_permissions": _BOTH_KEYS,
        "any_permission": True,
    }
    for content in (mixed, b"x" * (2 * 1_048_576)):
        response = _commit(vpd, content)
        assert (response.status_code, response.json()) == (403, commit_refusal)
    anonymous = anonymous_client(client)
    for response in (
        _preview(anonymous, mixed),
        _commit(anonymous, mixed),
        *(anonymous.get(path) for path in _TEMPLATES),
    ):
        assert response.status_code == 401, response.text
    assert _write_counts(db_engine) == before


# ---------------------------------------------------------------------------
# CH-8 — one state read feeds the change list and the state token
# ---------------------------------------------------------------------------


def test_one_state_read_feeds_the_change_list_and_the_state_token(
    client: TestClient, ewod: IdentityClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CH-8."""
    changed_pn, same_pn = _unique("PN"), _unique("PN")
    changed, same = _seed(client, (changed_pn, 1)), _seed(client, (same_pn, 1))
    body = _csv(
        (changed.number, changed_pn, "2", "", ""),
        (same.number, same_pn, "1", "", ""),
        (_unique("NEW"), _unique("PN"), "1", "", ""),
    )
    reads: list[work_orders.WorkOrderState] = []
    planned: list[work_orders.WorkOrderState] = []
    hashed: list[work_orders.WorkOrderState] = []
    real_read = work_orders.read_work_order_state
    real_plan = work_order_import.plan_changes
    real_token = work_orders.work_order_state_token

    def read(session: Session, work_order_id: int) -> work_orders.WorkOrderState:
        state = real_read(session, work_order_id)
        reads.append(state)
        return state

    def plan(state: work_orders.WorkOrderState, *args: Any) -> Any:
        planned.append(state)
        return real_plan(state, *args)

    def token(state: work_orders.WorkOrderState, *args: Any) -> str:
        hashed.append(state)
        return real_token(state, *args)

    monkeypatch.setattr(work_orders, "read_work_order_state", read)
    monkeypatch.setattr(work_order_import, "plan_changes", plan)
    monkeypatch.setattr(work_orders, "work_order_state_token", token)
    preview = _ok(_preview(ewod, body))
    assert _outcomes(preview)[changed.number] == "WILL_UPDATE"
    assert [state.work_order_id for state in reads] == [changed.id, same.id]
    assert len(planned) == 2 and all(p is r for p, r in zip(planned, reads, strict=True))
    assert len(hashed) == 1 and hashed[0] is reads[0]
