"""Integration tests for Phase 14 slice 7 — the PN audit trail.

``GET /api/tracking/audit-trail`` against a dedicated temporary database
migrated to head by the real Alembic chain (PROJECT_PROFILE §21 Tracking
"correction history", §28; owner decision OD-P15). Set-up data is written
through the real routes as the harness administrator; station commands
go through the enrolled-device harness. Every assertion is a read.

- BT-5: the exact scope of one PN (master, Work Orders, demand lines with
  their Hot rank rows and a station receipt, Management allocation rows,
  route adjustments) — never another PN's lines, a station allocation or
  a configuration row; no idempotency key, fingerprint or digest leaks;
- BT-6: a deleted demand line keeps its recorded history;
- BT-7: order and keyset paging, a late row and a held transaction, a
  line created between the scope read and the page read;
- BT-9 / BT-10: cursor and PN input errors;
- BT-11: the read takes no lock and writes nothing;
- BT-12: actors (deactivated User, station rows, legacy text);
- BT-13: the priority, allocation, route and completion payloads, a
  split after an adjustment;
- BZ-1: authorization;
- BT-14 (last — it seeds bulk rows): the measured plan of a first-page
  and a cursor-page read, printed for the OD-S7-7 trigger (100 ms) and
  bounded only against a gross regression.
"""

import collections
import datetime
import json
import os
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import audit_trail
from app.core.config import get_settings
from app.domain.enums import AuditEntityType, Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    admin_of,
    anonymous_client,
    client_as,
    station_device_client,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_audit_trail_api"
_REASON = "the customer added a deburr operation"
_TR2 = "Give both before_source and before_id, or neither."
_V1 = "Your account does not have permission to view this."
_A3 = "Choose a new password before you continue."
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16
_TRACKING_READ = (
    Permission.VIEW_PRODUCTION_DATA,
    Permission.EDIT_WORK_ORDER_ALLOCATION,
    Permission.ASSIGN_ROUTES,
)


def _tr3(pn: str) -> str:
    return f"That audit trail entry is not part of {pn}'s audit trail — reload the audit trail."


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
    """Anonymous application client (with enrolled station devices)."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield station_device_client(test_client)
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# The shop and the set-up commands
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _event() -> str:
    return str(uuid.uuid4())


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@dataclass(frozen=True)
class _Cell:
    area_id: int
    operation_id: int
    station_id: str
    name: str


@dataclass(frozen=True)
class _Shop:
    cells: dict[str, _Cell]

    def __getitem__(self, key: str) -> _Cell:
        return self.cells[key]


def _cell(admin: TestClient, department_id: int, *, is_terminal: bool = False) -> _Cell:
    name = _unique("AREA")
    area = _ok(
        admin.post(
            "/api/areas",
            json={"department_id": department_id, "name": name, "is_terminal": is_terminal},
        ),
        201,
    )
    operation = _ok(
        admin.post("/api/operations", json={"area_id": area["id"], "code": _unique("OP")}), 201
    )
    station = _ok(
        admin.post("/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area["id"]}),
        201,
    )
    return _Cell(int(area["id"]), int(operation["id"]), str(station["station_id"]), name)


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    admin = admin_of(client)
    _ok(
        admin.put(
            "/api/barcode-configuration/machine-asset-tag-format",
            json={"prefix": "AT-", "digits": 4},
        )
    )
    department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    department_id = int(department["id"])
    # CONFIG is only ever renamed (the excluded configuration rows).
    cells = {key: _cell(admin, department_id) for key in ("MAT", "CUT", "LATHE", "CONFIG")}
    cells["STOCK"] = _cell(admin, department_id, is_terminal=True)
    return _Shop(cells)


def _work_order(admin: TestClient, *lines: dict[str, Any], **header: Any) -> tuple[int, list[int]]:
    body = _ok(admin.post("/api/work-orders", json={"lines": list(lines), **header}), 201)
    return int(body["id"]), [int(line["id"]) for line in body["demands"]]


def _line(pn: str, quantity: int, **extra: Any) -> dict[str, Any]:
    return {"part_number": pn, "requested_quantity": quantity, **extra}


def _release(
    admin: TestClient,
    cell: _Cell,
    wo: int,
    demand: int,
    pn: str,
    quantity: int,
    template_id: int | None = None,
) -> int:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "route_mode": "PLANNED" if template_id is not None else "FLOATING",
        "starting_area_id": cell.area_id,
        "operation_id": cell.operation_id,
        "confirm_active_quantity": True,
        "device_event_id": _event(),
    }
    if template_id is not None:
        payload["route_template_id"] = template_id
    released = _ok(admin.post(f"/api/work-orders/{wo}/demands/{demand}/release", json=payload), 201)
    return int(released["quantity_flow_id"])


def _stock(client: TestClient, shop: _Shop, source: _Cell, flow_id: int, pn: str, qty: int) -> None:
    _ok(
        client.post(
            f"/api/scan-stations/{shop['STOCK'].station_id}/stockings",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "source_area_id": source.area_id,
                "target_area_id": shop["STOCK"].area_id,
                "quantity": qty,
                "device_event_id": _event(),
            },
        ),
        201,
    )


def _supply(client: TestClient, shop: _Shop, pn: str, quantity: int) -> tuple[int, int]:
    """``quantity`` more pcs of ``pn`` in stock on a supply line of its own."""
    admin = admin_of(client)
    wo, [demand] = _work_order(admin, _line(pn, quantity))
    flow_id = _release(admin, shop["MAT"], wo, demand, pn, quantity)
    _stock(client, shop, shop["MAT"], flow_id, pn, quantity)
    return wo, demand


def _allocation_body(pn: str, demand: int, quantity: int, **extra: Any) -> dict[str, Any]:
    return {
        "part_number": pn,
        "allocation_quantity": quantity,
        "lines": [{"work_order_demand_id": demand, "quantity": quantity}],
        "device_event_id": _event(),
        **extra,
    }


def _allocate(admin: TestClient, pn: str, demand: int, quantity: int) -> int:
    body = _ok(
        admin.post("/api/allocations/management", json=_allocation_body(pn, demand, quantity)), 201
    )
    return int(body["rows"][0]["allocation_id"])


def _station_allocate(client: TestClient, shop: _Shop, pn: str, demand: int, qty: int) -> int:
    body = _ok(
        client.post(
            "/api/allocations",
            json=_allocation_body(pn, demand, qty, station_id=shop["STOCK"].station_id),
        ),
        201,
    )
    return int(body["rows"][0]["allocation_id"])


def _reverse(admin: TestClient, allocation_id: int, reason: str = "recount") -> int:
    body = _ok(
        admin.post(
            f"/api/allocations/{allocation_id}/reversals",
            json={"reason": reason, "device_event_id": _event()},
        ),
        201,
    )
    return int(body["rows"][0]["allocation_id"])


def _correct(admin: TestClient, pn: str, demand: int, quantity: int) -> int:
    body = _ok(
        admin.post(
            "/api/allocations/corrections",
            json={
                "part_number": pn,
                "work_order_demand_id": demand,
                "quantity": quantity,
                "reason": "customer accepted overage",
                "device_event_id": _event(),
            },
        ),
        201,
    )
    return int(body["rows"][0]["allocation_id"])


def _hot_order(admin: TestClient) -> list[int]:
    return [
        int(entry["work_order_demand_id"]) for entry in _ok(admin.get("/api/hot-list"))["entries"]
    ]


def _hot_change(admin: TestClient, action: str, new_order: list[int]) -> None:
    _ok(
        admin.post(
            "/api/hot-list/changes",
            json={
                "device_event_id": _event(),
                "action": action,
                "expected_order": _hot_order(admin),
                "new_order": new_order,
            },
        ),
        201,
    )


def _hot_add(admin: TestClient, demand: int) -> None:
    _hot_change(admin, "ADD", [*_hot_order(admin), demand])


def _template(admin: TestClient, *steps: dict[str, Any]) -> int:
    body = _ok(
        admin.post(
            "/api/route-templates",
            json={"name": _unique("ROUTE"), "description": None, "steps": list(steps)},
        ),
        201,
    )
    return int(body["id"])


def _step(cell: _Cell, **extra: Any) -> dict[str, Any]:
    return {"area_id": cell.area_id, "operation_id": cell.operation_id, **extra}


def _future_step_ids(admin: TestClient, pn: str, flow_id: int) -> list[int]:
    body = _ok(admin.get("/api/tracking/assigned-routes", params={"part_number": pn}))
    [flow] = [flow for flow in body["flows"] if flow["quantity_flow_id"] == flow_id]
    return [int(step_id) for step_id in flow["future_step_ids"]]


def _adjust(admin: TestClient, pn: str, flow_id: int, steps: list[dict[str, Any]]) -> None:
    _ok(
        admin.post(
            f"/api/quantity-flows/{flow_id}/route-adjustments",
            json={
                "device_event_id": _event(),
                "expected_future_step_ids": _future_step_ids(admin, pn, flow_id),
                "steps": steps,
                "reason": _REASON,
            },
        ),
        201,
    )


def _receive(client: TestClient, cell: _Cell, pn: str, quantity: int, **extra: Any) -> Any:
    return _ok(
        client.post(
            f"/api/scan-stations/{cell.station_id}/receipts",
            json={
                "part_number": pn,
                "quantity": quantity,
                "request_type": "MODIFY",
                "route_mode": "FLOATING",
                "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "device_event_id": _event(),
                **extra,
            },
        ),
        201,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _trail(caller: TestClient, pn: str, **params: Any) -> Any:
    return caller.get("/api/tracking/audit-trail", params={"part_number": pn, **params})


def _page(caller: TestClient, pn: str, **params: Any) -> dict[str, Any]:
    body: dict[str, Any] = _ok(_trail(caller, pn, **params))
    return body


def _all(caller: TestClient, pn: str) -> dict[str, Any]:
    body = _page(caller, pn, limit=audit_trail.MAX_TRAIL_LIMIT)
    assert body["has_more"] is False
    return body


def _keys(body: dict[str, Any]) -> list[tuple[str, int]]:
    return [(entry["source"], entry["id"]) for entry in body["entries"]]


def _next(body: dict[str, Any]) -> dict[str, Any]:
    return {"before_source": body["next_before_source"], "before_id": body["next_before_id"]}


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar()


def _rows(engine: Engine, sql: str, **params: object) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(sa.text(sql), params).mappings()]


def _audit_ids(engine: Engine, entity_type: str, entity_ids: list[object]) -> set[int]:
    return {
        int(row["id"])
        for row in _rows(
            engine,
            "SELECT id FROM audit_events WHERE entity_type = :t AND entity_id = ANY(:ids)",
            t=entity_type,
            ids=[str(value) for value in entity_ids],
        )
    }


def _max_audit_id(engine: Engine) -> int:
    return int(_scalar(engine, "SELECT coalesce(max(id), 0) FROM audit_events"))


def _route_id(engine: Engine, flow_id: int) -> int:
    return int(
        _scalar(engine, "SELECT assigned_route_id FROM quantity_flows WHERE id = :id", id=flow_id)
    )


def _by_kind(body: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [entry for entry in body["entries"] if entry["kind"] == kind]


# ---------------------------------------------------------------------------
# BT-5 — the exact scope of one PN
# ---------------------------------------------------------------------------


def test_the_trail_lists_exactly_the_pn_scope(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BT-5 (and the "no internals" half of BT-13)."""
    admin = admin_of(client)
    pn_a, pn_b = _unique("PA"), _unique("PB")
    # WO1: an internal MODIFY Work Order requesting A and B; WO2 only B.
    wo1, [a1, b1] = _work_order(
        admin, _line(pn_a, 4, request_type="MODIFY"), _line(pn_b, 5, request_type="MODIFY")
    )
    wo2, [b2] = _work_order(admin, _line(pn_b, 3), work_order_number=_unique("WO"))
    # A's master details and image.
    _ok(admin.patch("/api/part-numbers", params={"number": pn_a}, json={"name": "Shaft"}))
    _ok(
        admin.put(
            "/api/part-numbers/image",
            params={"number": pn_a},
            content=_PNG,
            headers={"Content-Type": "image/png"},
        )
    )
    _ok(admin.delete("/api/part-numbers/image", params={"number": pn_a}))
    # WO1 header edit, then both lines' edits.
    _ok(admin.patch(f"/api/work-orders/{wo1}", json={"due_date": "2031-01-15"}))
    _ok(
        admin.patch(
            f"/api/work-orders/{wo1}",
            json={"line_edits": [{"id": a1, "notes": "first"}, {"id": b1, "notes": "second"}]},
        )
    )
    # Hot add of both lines, then B moves up past A.
    _hot_add(admin, a1)
    _hot_add(admin, b1)
    order = _hot_order(admin)
    swapped = [*order[:-2], b1, a1]
    assert order[-2:] == [a1, b1]
    _hot_change(admin, "MOVE_UP", swapped)
    # A's line released, stocked and filled by a STATION allocation (excluded);
    # it leaves the Hot list automatically.
    flow_a = _release(admin, shop["MAT"], wo1, a1, pn_a, 4)
    _stock(client, shop, shop["MAT"], flow_a, pn_a, 4)
    station_allocation = _station_allocate(client, shop, pn_a, a1, 4)
    # A station receipt raises A's internal line (no actor), PLANNED.
    template = _template(admin, _step(shop["MAT"]), _step(shop["CUT"]))
    receipt = _receive(
        client, shop["MAT"], pn_a, 6, route_mode="PLANNED", route_template_id=template
    )
    assert receipt["work_order_reused"] is True and receipt["work_order_demand_id"] == a1
    flow_f = int(receipt["quantity_flow_id"])
    _adjust(admin, pn_a, flow_f, [_step(shop["CUT"], instructions="Deburr")])
    # Stock for the Management entries; allocate-later, reversal, correction.
    wo3, a3 = _supply(client, shop, pn_a, 10)
    later = _allocate(admin, pn_a, a1, 2)
    reversal = _reverse(admin, later, "entered on the wrong line")
    correction = _correct(admin, pn_a, a1, 7)
    # A configuration edit (excluded).
    before_area = _max_audit_id(db_engine)
    _ok(admin.patch(f"/api/areas/{shop['CONFIG'].area_id}", json={"name": _unique("AREA")}))
    [area_row] = [
        row["id"]
        for row in _rows(
            db_engine, "SELECT id FROM audit_events WHERE id > :since", since=before_area
        )
    ]

    body = _all(admin, pn_a)
    expected_audit = (
        _audit_ids(db_engine, "PartNumber", [pn_a])
        | _audit_ids(db_engine, "WorkOrder", [wo1, wo3])
        | _audit_ids(db_engine, "WorkOrderDemand", [a1, a3])
        | _audit_ids(db_engine, "AssignedRoute", [_route_id(db_engine, flow_f)])
    )
    expected = {("AUDIT", row_id) for row_id in expected_audit} | {
        ("ALLOCATION", row_id) for row_id in (later, reversal, correction)
    }
    assert set(_keys(body)) == expected
    assert len(body["entries"]) == len(expected) == body["total"] == 18
    assert collections.Counter(entry["kind"] for entry in body["entries"]) == {
        "PART_NUMBER_CREATED": 1,
        "PART_NUMBER_UPDATED": 1,
        "PART_NUMBER_IMAGE_CHANGED": 2,
        "WORK_ORDER_CREATED": 2,
        "WORK_ORDER_UPDATED": 1,
        "DEMAND_CREATED": 2,
        "DEMAND_UPDATED": 2,
        "PRIORITY_CHANGED": 3,
        "ROUTE_ADJUSTED": 1,
        "ALLOCATED": 1,
        "ALLOCATION_REVERSED": 1,
        "ALLOCATED_BEYOND_DEMAND": 1,
    }
    excluded = (
        {("ALLOCATION", station_allocation), ("AUDIT", int(area_row))}
        | {("AUDIT", row_id) for row_id in _audit_ids(db_engine, "WorkOrderDemand", [b1, b2])}
        | {("AUDIT", row_id) for row_id in _audit_ids(db_engine, "WorkOrder", [wo2])}
        | {("AUDIT", row_id) for row_id in _audit_ids(db_engine, "PartNumber", [pn_b])}
    )
    assert not excluded & set(_keys(body))
    # The station receipt's raise of A's line: an edit without an actor.
    [raised] = [
        entry
        for entry in _by_kind(body, "DEMAND_UPDATED")
        if any(change["field"] == "requested_quantity" for change in entry["changes"])
    ]
    assert raised["actor_user"] is None and raised["legacy_actor"] is None
    assert raised["subject"] == {
        "work_order_id": wo1,
        "work_order_number": None,
        "work_order_demand_id": a1,
        "demand_exists": True,
        "quantity_flow_id": None,
    }
    assert {"field": "requested_quantity", "before": 4, "after": 10} in raised["changes"]
    # Station-written rows carry no User: the receipt's raise and the Hot
    # removal the station allocation caused; every other row is the admin's.
    actorless = [entry for entry in body["entries"] if entry["actor_user"] is None]
    assert [entry["kind"] for entry in actorless] == ["DEMAND_UPDATED", "PRIORITY_CHANGED"]
    assert actorless[0] == raised
    assert actorless[1]["priority"] == {
        "action": "AUTO_REMOVE",
        "trigger": "ALLOCATION",
        "removal_reason": "FULLY_ALLOCATED",
        "shifted": False,
    }
    for entry in body["entries"]:
        if entry["actor_user"] is not None:
            assert entry["actor_user"]["id"] == admin.user_id, entry

    # B's trail shares WO1's header rows and none of A's line rows.
    b_keys = set(_keys(_all(admin, pn_b)))
    assert {("AUDIT", row_id) for row_id in _audit_ids(db_engine, "WorkOrder", [wo1])} <= b_keys
    assert not {("AUDIT", row_id) for row_id in _audit_ids(db_engine, "WorkOrderDemand", [a1])} & (
        b_keys
    )

    # No idempotency key, fingerprint, command reference or digest leaks.
    secrets: set[str] = set()
    for row in _rows(
        db_engine,
        "SELECT before_data, after_data, metadata FROM audit_events WHERE id = ANY(:ids)",
        ids=sorted(expected_audit),
    ):
        for side in (row["before_data"], row["after_data"]):
            if isinstance(side, dict) and isinstance(side.get("image"), str):
                secrets.add(side["image"])
        for block in (row["metadata"] or {}).values():
            for key in ("device_event_id", "fingerprint"):
                if isinstance(block, dict) and isinstance(block.get(key), str):
                    secrets.add(block[key])
    for row in _rows(
        db_engine,
        "SELECT device_event_id FROM work_order_allocations WHERE part_number = :pn",
        pn=pn_a,
    ):
        secrets.add(row["device_event_id"])
    assert len(secrets) >= 8
    text = json.dumps(body)
    assert not [secret for secret in secrets if secret in text]
    assert '"device_event_id"' not in text and '"fingerprint"' not in text


# ---------------------------------------------------------------------------
# BT-6 — a deleted demand line
# ---------------------------------------------------------------------------


def test_a_deleted_line_keeps_its_recorded_history(client: TestClient, db_engine: Engine) -> None:
    """BT-6."""
    admin = admin_of(client)
    pn = _unique("PD")
    wo, [line, _other] = _work_order(admin, _line(pn, 5), _line(_unique("PC"), 5))
    _hot_add(admin, line)
    _ok(
        admin.delete(f"/api/work-orders/{wo}/demands/{line}", params={"confirm_hot_removal": True}),
        204,
    )
    assert (
        _scalar(db_engine, "SELECT count(*) FROM work_order_demands WHERE id = :id", id=line) == 0
    )

    body = _all(admin, pn)
    line_entries = [
        entry for entry in body["entries"] if entry["subject"]["work_order_demand_id"] == line
    ]
    assert [entry["kind"] for entry in line_entries] == [
        "PRIORITY_CHANGED",
        "PRIORITY_CHANGED",
        "DEMAND_CREATED",
    ]
    for entry in line_entries:
        assert entry["subject"]["demand_exists"] is False
        assert entry["subject"]["work_order_id"] == wo
    removal, added, created = line_entries
    assert removal["priority"] == {
        "action": "LINE_DELETE",
        "trigger": "DEMAND_LINE_REMOVAL",
        "removal_reason": "LINE_DELETED",
        "shifted": False,
    }
    [rank] = removal["changes"]
    assert (rank["field"], rank["after"]) == ("priority_rank", None)
    assert isinstance(rank["before"], int)
    assert added["priority"] == {
        "action": "ADD",
        "trigger": None,
        "removal_reason": None,
        "shifted": False,
    }
    assert {"field": "requested_quantity", "before": None, "after": 5} in created["changes"]
    # The Work Order's own creation row stays in the PN's trail too.
    assert [
        entry["subject"]["work_order_id"] for entry in _by_kind(body, "WORK_ORDER_CREATED")
    ] == [wo]


# ---------------------------------------------------------------------------
# BT-7 — order and paging
# ---------------------------------------------------------------------------


def _order_key(entry: dict[str, Any]) -> tuple[datetime.datetime, int, int]:
    return (
        datetime.datetime.fromisoformat(entry["occurred_at"]),
        1 if entry["source"] == "AUDIT" else 0,
        entry["id"],
    )


def _page_through(caller: TestClient, pn: str, limit: int) -> tuple[list[tuple[str, int]], int]:
    keys: list[tuple[str, int]] = []
    body = _page(caller, pn, limit=limit)
    keys += _keys(body)
    while body["has_more"]:
        assert len(body["entries"]) == limit
        body = _page(caller, pn, limit=limit, **_next(body))
        keys += _keys(body)
    assert body["next_before_source"] is None and body["next_before_id"] is None
    return keys, int(body["total"])


def test_order_and_paging(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """BT-7: rows of one instant come ``source_rank DESC, id DESC``; paging
    yields every entry exactly once in the full-read order; (a) a newer row
    written while paging appears only on a fresh read."""
    admin = admin_of(client)
    pn = _unique("PO")
    other_pn = _unique("PX")
    wo, [line, _x] = _work_order(admin, _line(pn, 5), _line(other_pn, 5))
    _hot_add(admin, line)
    _supply(client, shop, pn, 5)
    # One command: the allocation row and the Hot rank row it causes.
    allocation = _allocate(admin, pn, line, 5)

    full = _all(admin, pn)
    entries = full["entries"]
    assert [_order_key(entry) for entry in entries] == sorted(
        (_order_key(entry) for entry in entries), reverse=True
    )
    # The Work Order save: PN master, Work Order and line rows share one
    # instant and come id DESC.
    created = [entry for entry in entries if entry["kind"] in ("PART_NUMBER_CREATED",)]
    instant = created[0]["occurred_at"]
    same = [entry for entry in entries if entry["occurred_at"] == instant]
    assert len(same) == 3
    assert [entry["id"] for entry in same] == sorted((entry["id"] for entry in same), reverse=True)
    # The allocation command: its audit (Hot) row ranks before its own row.
    first, second = entries[0], entries[1]
    assert (first["kind"], first["priority"]["action"]) == ("PRIORITY_CHANGED", "AUTO_REMOVE")
    assert (second["source"], second["id"], second["kind"]) == (
        "ALLOCATION",
        allocation,
        "ALLOCATED",
    )
    assert first["occurred_at"] == second["occurred_at"]

    keys, total = _page_through(admin, pn, 2)
    assert keys == _keys(full)
    assert total == len(keys) == full["total"]

    # (a) A newer row written after page 1 is not reached by this opening.
    page1 = _page(admin, pn, limit=2)
    _ok(admin.patch("/api/part-numbers", params={"number": pn}, json={"name": "Later"}))
    keys = _keys(page1)
    body = page1
    while body["has_more"]:
        body = _page(admin, pn, limit=2, **_next(body))
        keys += _keys(body)
    assert keys == _keys(full)
    assert body["total"] == full["total"] + 1
    fresh = _all(admin, pn)
    assert fresh["entries"][0]["kind"] == "PART_NUMBER_UPDATED"
    assert _keys(fresh)[1:] == _keys(full)


def test_a_row_committed_late_by_an_earlier_transaction_is_reached_once(
    client: TestClient, db_engine: Engine
) -> None:
    """BT-7 (b): X is stamped with its transaction's start (between R3 and
    R4) but committed after page 1 was read — it is delivered exactly once."""
    admin = admin_of(client)
    pn = _unique("PH")
    _work_order(admin, _line(pn, 5))  # R1–R3: PN master, Work Order, line
    with db_engine.connect() as held:
        transaction = held.begin()
        held.execute(sa.text("SELECT 1"))
        x_id = int(
            held.execute(
                sa.text(
                    "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
                    " before_data, after_data) VALUES ('UPDATED', 'PartNumber', :pn, now(),"
                    " CAST(:before AS jsonb), CAST(:after AS jsonb)) RETURNING id"
                ),
                {
                    "pn": pn,
                    "before": json.dumps({"name": None}),
                    "after": json.dumps({"name": "held"}),
                },
            ).scalar_one()
        )
        for name in ("R4", "R5", "R6"):  # R4–R6, committed by other requests
            _ok(admin.patch("/api/part-numbers", params={"number": pn}, json={"name": name}))
        page1 = _page(admin, pn, limit=2)
        assert [entry["changes"][0]["after"] for entry in page1["entries"]] == ["R6", "R5"]
        assert page1["total"] == 6
        transaction.commit()
    keys = _keys(page1)
    body = page1
    while body["has_more"]:
        body = _page(admin, pn, limit=2, **_next(body))
        keys += _keys(body)
    assert keys.count(("AUDIT", x_id)) == 1
    assert len(keys) == len(set(keys)) == 7
    assert body["total"] == 7
    r4 = keys.index(("AUDIT", x_id)) - 1
    full = _all(admin, pn)
    assert full["entries"][r4]["changes"][0]["after"] == "R4"
    assert _keys(full) == keys


def test_an_allocation_on_a_line_created_after_the_scope_read_waits_for_a_later_read(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BT-7 (c): under READ COMMITTED every statement reads its own
    snapshot. A new demand line and a Management allocation on it,
    committed between the scope read and the page read, are left to a
    later read — never a 500 for a line the scope does not know."""
    admin = admin_of(client)
    pn = _unique("PL")
    _supply(client, shop, pn, 5)
    late: dict[str, int] = {}
    scope_of = audit_trail._scope

    def scope_then_commit(session: Session, part_number: str) -> audit_trail.TrailScope:
        scope = scope_of(session, part_number)
        if not late:
            _, [line] = _work_order(admin, _line(pn, 5))
            late["line"] = line
            late["allocation"] = _allocate(admin, pn, line, 2)
        return scope

    monkeypatch.setattr(audit_trail, "_scope", scope_then_commit)
    with Session(db_engine) as session:
        page = audit_trail.audit_trail_of(session, pn, limit=audit_trail.MAX_TRAIL_LIMIT)
    assert set(late) == {"line", "allocation"}
    assert all(entry.subject.work_order_demand_id != late["line"] for entry in page.entries)
    assert page.total == len(page.entries) and not page.has_more

    monkeypatch.undo()
    later = _all(admin, pn)
    assert ("ALLOCATION", late["allocation"]) in _keys(later)
    assert later["total"] == len(later["entries"]) > page.total


# ---------------------------------------------------------------------------
# BT-9 / BT-10 — cursor and PN input
# ---------------------------------------------------------------------------


def test_cursor_errors(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """BT-9."""
    admin = admin_of(client)
    pn, other = _unique("PE"), _unique("PF")
    _, [line, _other_line] = _work_order(admin, _line(pn, 5), _line(other, 5))
    _supply(client, shop, pn, 5)
    station_allocation = _station_allocate(client, shop, pn, line, 5)
    since = _max_audit_id(db_engine)
    _ok(admin.patch(f"/api/areas/{shop['CONFIG'].area_id}", json={"name": _unique("AREA")}))
    configuration_row = int(
        _scalar(db_engine, "SELECT id FROM audit_events WHERE id > :since", since=since)
    )
    [other_master] = _audit_ids(db_engine, "PartNumber", [other])
    assert _all(admin, pn)["total"] > 0

    for params in ({"before_source": "AUDIT"}, {"before_id": 5}):
        response = _trail(admin, pn, **params)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == _TR2
    for source, row_id in (
        ("AUDIT", configuration_row),
        ("AUDIT", other_master),
        ("ALLOCATION", station_allocation),
    ):
        response = _trail(admin, pn, before_source=source, before_id=row_id)
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": _tr3(pn)}
    invalid: list[dict[str, Any]] = [
        {"before_source": "AUDIT", "before_id": 0},
        {"before_source": "AUDIT", "before_id": 2**63},
        {"limit": 0},
        {"limit": 201},
        {"before_source": "MOVEMENT", "before_id": 1},
    ]
    for params in invalid:
        response = _trail(admin, pn, **params)
        assert response.status_code == 422, (params, response.text)


def test_part_number_input(client: TestClient) -> None:
    """BT-10."""
    admin = admin_of(client)
    pn = _unique("PI")
    _work_order(admin, _line(pn, 5))
    body = _page(admin, f"  {pn.lower()} ")
    assert body["part_number"] == pn and body["total"] == 3
    spaced = _trail(admin, "HAS SPACE")
    assert spaced.status_code == 422, spaced.text
    unknown = _unique("PU")
    missing = _trail(admin, unknown)
    assert missing.status_code == 404, missing.text
    assert missing.json() == {"detail": f"Part Number {unknown} is not known to PartFlow."}


# ---------------------------------------------------------------------------
# BT-11 — no lock, no write
# ---------------------------------------------------------------------------

_COUNTED = ("audit_events", "work_order_allocations", "part_movements", "user_sessions")


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED
        }


def test_the_read_never_waits_on_a_lock_and_writes_nothing(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BT-11."""
    admin = admin_of(client)
    reader = client_as(client, Permission.VIEW_PRODUCTION_DATA)
    pn = _unique("PL")
    wo, [line] = _work_order(admin, _line(pn, 5))
    flow_id = _release(admin, shop["MAT"], wo, line, pn, 5)
    _hot_add(admin, line)
    expected = _all(reader, pn)
    before = _counts(db_engine)
    with db_engine.connect() as holder:
        transaction = holder.begin()
        for key in (f"partflow:part-number:{pn}", "partflow:hot-list"):
            holder.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
            )
        for table, row_id in (
            ("work_order_demands", line),
            ("work_orders", wo),
            ("quantity_flows", flow_id),
            ("users", admin.user_id),
        ):
            holder.execute(
                sa.text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id}
            )
        # The service itself, on a session that would fail fast on any wait.
        with Session(db_engine) as session:
            session.execute(sa.text("SET lock_timeout = '1s'"))
            page = audit_trail.audit_trail_of(session, pn, limit=audit_trail.MAX_TRAIL_LIMIT)
            assert [(entry.source, entry.id) for entry in page.entries] == _keys(expected)
            session.rollback()
        results: dict[str, Any] = {}
        thread = threading.Thread(
            target=lambda: results.update(response=_trail(reader, pn, limit=200)), daemon=True
        )
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive()
        assert results["response"].status_code == 200, results["response"].text
        assert results["response"].json() == expected
        transaction.rollback()
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# BT-12 — actors
# ---------------------------------------------------------------------------


def test_actors(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """BT-12."""
    admin = admin_of(client)
    pn = _unique("PR")
    _receive(client, shop["CUT"], pn, 3)  # station rows: master, Work Order, line
    editor = client_as(client, Permission.MANAGE_PART_NUMBER_MASTER)
    _ok(editor.patch("/api/part-numbers", params={"number": pn}, json={"name": "Edited"}))
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE users SET is_active = false WHERE id = :id"), {"id": editor.user_id}
        )
        legacy_id = connection.execute(
            sa.text(
                "INSERT INTO audit_events (event_type, entity_type, entity_id, actor_reference,"
                " occurred_at, before_data, after_data) VALUES ('UPDATED', 'PartNumber', :pn,"
                " 'legacy-x', now(), CAST(:before AS jsonb), CAST(:after AS jsonb)) RETURNING id"
            ),
            {
                "pn": pn,
                "before": json.dumps({"erp_id": None}),
                "after": json.dumps({"erp_id": "E-9"}),
            },
        ).scalar_one()
    display_name = _scalar(
        db_engine, "SELECT display_name FROM users WHERE id = :id", id=editor.user_id
    )

    body = _all(admin, pn)
    assert [entry["kind"] for entry in body["entries"]] == [
        "PART_NUMBER_UPDATED",
        "PART_NUMBER_UPDATED",
        "DEMAND_CREATED",
        "WORK_ORDER_CREATED",
        "PART_NUMBER_CREATED",
    ]
    legacy, edited, *station = body["entries"]
    assert legacy["id"] == legacy_id
    assert (legacy["actor_user"], legacy["legacy_actor"]) == (None, "legacy-x")
    assert legacy["changes"] == [{"field": "erp_id", "before": None, "after": "E-9"}]
    assert edited["actor_user"] == {
        "id": editor.user_id,
        "display_name": display_name,
        "avatar_updated_at": None,
    }
    assert edited["legacy_actor"] is None
    for entry in station:
        assert (entry["actor_user"], entry["legacy_actor"]) == (None, None)


# ---------------------------------------------------------------------------
# BT-13 — payloads
# ---------------------------------------------------------------------------


def test_priority_and_allocation_payloads(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BT-13: manual and automatic Hot rank rows; allocation facts."""
    admin = admin_of(client)
    pn = _unique("PP")
    # The first line shares its Work Order with another PN's open line, so
    # filling it removes it as FULLY_ALLOCATED (not WORK_ORDER_COMPLETED).
    wo_a, [first, _keep_open] = _work_order(admin, _line(pn, 5), _line(_unique("PK"), 5))
    wo_b, [second] = _work_order(admin, _line(pn, 5))
    _hot_add(admin, first)
    _hot_add(admin, second)
    _supply(client, shop, pn, 5)
    allocation = _allocate(admin, pn, first, 5)
    reversal = _reverse(admin, allocation, "recount")

    body = _all(admin, pn)
    added = [
        entry
        for entry in _by_kind(body, "PRIORITY_CHANGED")
        if entry["priority"]["action"] == "ADD"
    ]
    assert {entry["subject"]["work_order_demand_id"] for entry in added} == {first, second}
    for entry in added:
        assert entry["priority"] == {
            "action": "ADD",
            "trigger": None,
            "removal_reason": None,
            "shifted": False,
        }
        assert entry["actor_user"]["id"] == admin.user_id
    automatic = {
        entry["subject"]["work_order_demand_id"]: entry
        for entry in _by_kind(body, "PRIORITY_CHANGED")
        if entry["priority"]["action"] == "AUTO_REMOVE"
    }
    assert automatic[first]["priority"] == {
        "action": "AUTO_REMOVE",
        "trigger": "ALLOCATION",
        "removal_reason": "FULLY_ALLOCATED",
        "shifted": False,
    }
    assert automatic[first]["changes"][0]["after"] is None
    assert automatic[second]["priority"] == {
        "action": "AUTO_REMOVE",
        "trigger": "ALLOCATION",
        "removal_reason": None,
        "shifted": True,
    }
    assert automatic[second]["subject"]["work_order_id"] == wo_b

    rows = {
        row["id"]: row
        for row in _rows(
            db_engine,
            "SELECT * FROM work_order_allocations WHERE id = ANY(:ids)",
            ids=[allocation, reversal],
        )
    }
    entries = {entry["id"]: entry for entry in body["entries"] if entry["source"] == "ALLOCATION"}
    assert set(entries) == {allocation, reversal}
    for row_id, row in rows.items():
        entry = entries[row_id]
        assert entry["allocation"] == {
            "quantity": row["quantity"],
            "source": row["source"],
            "is_manual_override": row["is_manual_override"],
            "exceeds_demand": row["exceeds_demand"],
            "reverses_allocation_id": row["reverses_allocation_id"],
            "station_id": row["station_id"],
        }
        assert entry["reason"] == row["allocation_reason"]
        assert entry["changes"] == [] and entry["priority"] is None and entry["route"] is None
        assert entry["subject"]["work_order_demand_id"] == first
        assert entry["subject"]["work_order_id"] == wo_a
        assert entry["actor_user"]["id"] == admin.user_id
    assert entries[reversal]["kind"] == "ALLOCATION_REVERSED"
    assert entries[reversal]["reason"] == "recount"
    assert entries[reversal]["allocation"]["reverses_allocation_id"] == allocation
    assert entries[allocation]["kind"] == "ALLOCATED"


def _priority_rows(caller: TestClient, pn: str, demand: int) -> list[tuple[str, Any, Any, bool]]:
    """``(action, before, after, shifted)`` of the line's rank rows, oldest first."""
    return [
        (
            entry["priority"]["action"],
            entry["changes"][0]["before"],
            entry["changes"][0]["after"],
            entry["priority"]["shifted"],
        )
        for entry in reversed(_by_kind(_all(caller, pn), "PRIORITY_CHANGED"))
        if entry["subject"]["work_order_demand_id"] == demand
    ]


def test_a_displaced_line_is_marked_shifted(client: TestClient) -> None:
    """BT-13: Move Up and Remove of B write rows for A with B's action;
    A's trail marks them shifted, B's own rows are not."""
    admin = admin_of(client)
    pn_a, pn_b = _unique("PA"), _unique("PB")
    _, [line_a] = _work_order(admin, _line(pn_a, 5))
    _, [line_b] = _work_order(admin, _line(pn_b, 5))
    _hot_add(admin, line_a)
    _hot_add(admin, line_b)
    order = _hot_order(admin)
    rank_a = order.index(line_a) + 1
    assert order.index(line_b) == rank_a  # B directly below A
    swapped = [*order[: rank_a - 1], line_b, line_a, *order[rank_a + 1 :]]
    _hot_change(admin, "MOVE_UP", swapped)
    _hot_change(admin, "REMOVE", [demand for demand in swapped if demand != line_b])

    assert _priority_rows(admin, pn_a, line_a) == [
        ("ADD", None, rank_a, False),
        ("MOVE_UP", rank_a, rank_a + 1, True),
        ("REMOVE", rank_a + 1, rank_a, True),
    ]
    assert _priority_rows(admin, pn_b, line_b) == [
        ("ADD", None, rank_a + 1, False),
        ("MOVE_UP", rank_a + 1, rank_a, False),
        ("REMOVE", rank_a, None, False),
    ]


def test_route_payloads(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """BT-13: the replaced and new tails after the kept-through sequence,
    a since-deactivated Area and a since-retired Machine still named, the
    stored float seconds passed through, and a duration-only adjustment."""
    admin = admin_of(client)
    department = int(
        _scalar(db_engine, "SELECT department_id FROM areas WHERE id = :id", id=shop["MAT"].area_id)
    )
    temporary = _cell(admin, department)
    machine = _ok(
        admin.post("/api/machines", json={"area_id": shop["CUT"].area_id, "name": _unique("M")}),
        201,
    )
    template = _template(
        admin,
        _step(shop["MAT"]),
        _step(
            shop["CUT"],
            preferred_machine_id=machine["id"],
            instructions="Use the soft jaws",
            expected_duration="PT45M",
        ),
        _step(shop["LATHE"]),
    )
    pn = _unique("PT")
    wo, [line] = _work_order(admin, _line(pn, 10))
    flow_id = _release(admin, shop["MAT"], wo, line, pn, 10, template)
    cut = {"preferred_machine_id": machine["id"], "instructions": "Deburr"}
    _adjust(
        admin,
        pn,
        flow_id,
        [_step(shop["CUT"], expected_duration="PT1M30.5S", **cut), _step(temporary)],
    )
    # Duration-only: step 2 changes only its expected duration.
    _adjust(
        admin,
        pn,
        flow_id,
        [_step(shop["CUT"], expected_duration="PT2M", **cut), _step(temporary)],
    )
    _ok(admin.patch(f"/api/areas/{temporary.area_id}", json={"is_active": False}))
    _ok(admin.post(f"/api/machines/{machine['id']}/retire", json={"reason": "Worn out"}))

    body = _all(admin, pn)
    duration_only, first = _by_kind(body, "ROUTE_ADJUSTED")
    for entry in (duration_only, first):
        assert entry["subject"] == {
            "work_order_id": None,
            "work_order_number": None,
            "work_order_demand_id": None,
            "demand_exists": None,
            "quantity_flow_id": flow_id,
        }
        assert entry["reason"] == _REASON
        assert entry["changes"] == []
        assert entry["actor_user"]["id"] == admin.user_id
        assert entry["route"]["kept_through_sequence"] == 1
    machine_ref = {"id": machine["id"], "name": machine["name"]}

    def area(cell: _Cell) -> dict[str, Any]:
        return {"id": cell.area_id, "name": cell.name, "color": None, "is_terminal": False}

    def operation(cell: _Cell, code: str) -> dict[str, Any]:
        return {"id": cell.operation_id, "code": code, "name": None, "is_external": False}

    codes = {
        int(row["id"]): row["code"] for row in _rows(db_engine, "SELECT id, code FROM operations")
    }
    cut_step = {
        "sequence": 2,
        "area": area(shop["CUT"]),
        "operation": operation(shop["CUT"], codes[shop["CUT"].operation_id]),
        "preferred_machine": machine_ref,
        "instructions": "Deburr",
    }
    temporary_step = {
        "sequence": 3,
        "area": area(temporary),
        "operation": operation(temporary, codes[temporary.operation_id]),
        "expected_duration": None,
        "preferred_machine": None,
        "instructions": None,
    }
    assert first["route"]["before_steps"] == [
        {**cut_step, "expected_duration": "PT45M", "instructions": "Use the soft jaws"},
        {
            "sequence": 3,
            "area": area(shop["LATHE"]),
            "operation": operation(shop["LATHE"], codes[shop["LATHE"].operation_id]),
            "expected_duration": None,
            "preferred_machine": None,
            "instructions": None,
        },
    ]
    assert first["route"]["after_steps"] == [
        {**cut_step, "expected_duration": "PT1M30.5S"},
        temporary_step,
    ]
    before, after = duration_only["route"]["before_steps"], duration_only["route"]["after_steps"]
    assert before[1] == after[1] == temporary_step
    assert {key for key in before[0] if before[0][key] != after[0][key]} == {"expected_duration"}
    assert (before[0]["expected_duration"], after[0]["expected_duration"]) == ("PT1M30.5S", "PT2M")


def test_split_after_an_adjustment(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """BT-13: each flow's own AssignedRoute adjustments name that flow."""
    admin = admin_of(client)
    template = _template(admin, _step(shop["MAT"]), _step(shop["CUT"]), _step(shop["LATHE"]))
    pn = _unique("PS")
    wo, [line] = _work_order(admin, _line(pn, 10))
    parent = _release(admin, shop["MAT"], wo, line, pn, 10, template)
    _adjust(admin, pn, parent, [_step(shop["CUT"], instructions="first"), _step(shop["LATHE"])])
    split = _ok(
        client.post(
            f"/api/scan-stations/{shop['CUT'].station_id}/transfers",
            json={
                "part_number": pn,
                "quantity_flow_id": parent,
                "source_area_id": shop["MAT"].area_id,
                "target_area_id": shop["CUT"].area_id,
                "quantity": 4,
                "device_event_id": _event(),
            },
        ),
        201,
    )
    child = int(split["remainder_quantity_flow_id"])
    _adjust(admin, pn, child, [_step(shop["CUT"], instructions="second")])
    assert _scalar(db_engine, "SELECT status FROM quantity_flows WHERE id = :id", id=parent) == (
        "SPLIT"
    )

    body = _all(admin, pn)
    adjusted = _by_kind(body, "ROUTE_ADJUSTED")
    assert [entry["subject"]["quantity_flow_id"] for entry in adjusted] == [child, parent]
    assert body["total"] == len(body["entries"])


def test_work_order_completion_payloads(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """BT-13 (F17): a completion caused by a demand save and by a line
    deletion, as the real writer recorded it."""
    admin = admin_of(client)
    pn = _unique("PW")
    wo, [line] = _work_order(admin, _line(pn, 10))
    _supply(client, shop, pn, 6)
    _allocate(admin, pn, line, 6)
    saved = _ok(
        admin.patch(
            f"/api/work-orders/{wo}", json={"line_edits": [{"id": line, "requested_quantity": 6}]}
        )
    )
    assert saved["status"] == "COMPLETED"
    body = _all(admin, pn)
    [completed] = _by_kind(body, "WORK_ORDER_COMPLETED")
    assert completed["completion_trigger"] == "WORK_ORDER_SAVE"
    assert completed["changes"] == [] and completed["priority"] is None
    assert completed["subject"]["work_order_id"] == wo
    assert completed["subject"]["work_order_demand_id"] is None
    assert completed["actor_user"]["id"] == admin.user_id
    [row] = _rows(
        db_engine,
        "SELECT before_data, after_data FROM audit_events WHERE id = :id",
        id=completed["id"],
    )
    assert set(row["before_data"]) == set(row["after_data"]) == {"completed_at"}
    assert set(row["after_data"]) <= set(audit_trail.KIND_ONLY_FIELDS[AuditEntityType.WORK_ORDER])

    other_pn, removed_pn = _unique("PW"), _unique("PZ")
    wo2, [kept, removed] = _work_order(admin, _line(other_pn, 5), _line(removed_pn, 5))
    _supply(client, shop, other_pn, 5)
    _allocate(admin, other_pn, kept, 5)
    _ok(admin.delete(f"/api/work-orders/{wo2}/demands/{removed}"), 204)
    [completed] = _by_kind(_all(admin, other_pn), "WORK_ORDER_COMPLETED")
    assert completed["completion_trigger"] == "DEMAND_LINE_REMOVAL"
    assert completed["subject"]["work_order_id"] == wo2
    # The Work Order's completion is in the trail of every PN it requests.
    [shared] = _by_kind(_all(admin, removed_pn), "WORK_ORDER_COMPLETED")
    assert shared["id"] == completed["id"]


# ---------------------------------------------------------------------------
# BZ-1 — authorization
# ---------------------------------------------------------------------------


def test_authorization(client: TestClient) -> None:
    """BZ-1."""
    admin = admin_of(client)
    pn = _unique("PZ")
    _work_order(admin, _line(pn, 5))
    anonymous = _trail(anonymous_client(client), pn)
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["authentication_required"] is True
    invalid = anonymous_client(client).get(
        "/api/tracking/audit-trail", params={"part_number": pn, "limit": 0}
    )
    assert invalid.status_code == 401, invalid.text
    denied = _trail(client_as(client, Permission.MANAGE_MACHINES), pn)
    assert denied.status_code == 403, denied.text
    assert denied.json() == {
        "detail": _V1,
        "permission_denied": True,
        "required_permissions": sorted(key.value for key in _TRACKING_READ),
        "any_permission": True,
    }
    pending = _trail(client_as(client, *ALL_PERMISSIONS, temporary_password=True), pn)
    assert pending.status_code == 403, pending.text
    assert pending.json()["password_change_required"] is True
    assert pending.json()["detail"] == _A3
    for key in _TRACKING_READ:
        allowed = _trail(client_as(client, key), pn)
        assert allowed.status_code == 200, (key, allowed.text)
        assert allowed.json()["total"] == 3


# ---------------------------------------------------------------------------
# BT-14 — the measured plan (LAST: it seeds bulk rows into this database)
# ---------------------------------------------------------------------------

# Ten times the OD-S7-7 trigger: only a lost index or a plan that scans
# far more than the deleted-line lookup does crosses it on a loaded host.
_GROSS_REGRESSION_MS = 1000

_SEED = """
INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, after_data)
SELECT 'CREATED', 'WorkOrderDemand', (90000000 + n)::text, now() - n * interval '1 second',
       jsonb_build_object('part_number', 'SEED-' || (n % 5000), 'work_order_id', 80000000 + n,
                          'requested_quantity', 5)
FROM generate_series(1, 20000) AS n;
INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, before_data, after_data)
SELECT 'UPDATED', 'WorkOrderDemand', (90000000 + n % 20000)::text, now(),
       jsonb_build_object('part_number', 'SEED-' || (n % 5000), 'requested_quantity', 5),
       jsonb_build_object('part_number', 'SEED-' || (n % 5000), 'requested_quantity', 6)
FROM generate_series(1, 50000) AS n;
INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, before_data, after_data,
                          metadata)
SELECT 'UPDATED', 'WorkOrderDemand', (90000000 + n % 20000)::text, now(),
       jsonb_build_object('priority_rank', n % 50), jsonb_build_object('priority_rank', n % 50 + 1),
       jsonb_build_object('hot_list_change', jsonb_build_object('action', 'DRAG', 'sequence', 1,
                          'device_event_id', 'seed-' || n, 'fingerprint', 'f'))
FROM generate_series(1, 200000) AS n;
"""

_SEED_PN = """
INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at, before_data, after_data,
                          metadata)
SELECT kinds.event_type, 'WorkOrderDemand', (95000000 + n)::text,
       now() - (n * 3 + kinds.ord) * interval '1 second',
       CASE WHEN kinds.ord = 0 THEN NULL ELSE kinds.before END,
       CASE WHEN kinds.ord = 0
            THEN jsonb_build_object('part_number', CAST(:pn AS text), 'work_order_id',
                                    85000000 + n, 'requested_quantity', 5)
            ELSE kinds.after END,
       kinds.metadata
FROM generate_series(1, 200) AS n,
     (VALUES (0, 'CREATED', NULL::jsonb, NULL::jsonb, NULL::jsonb),
             (1, 'UPDATED', '{"requested_quantity": 5}'::jsonb, '{"requested_quantity": 7}'::jsonb,
              NULL::jsonb),
             (2, 'UPDATED', '{"priority_rank": null}'::jsonb, '{"priority_rank": 3}'::jsonb,
              '{"hot_list_change": {"action": "ADD", "sequence": 1}}'::jsonb))
       AS kinds(ord, event_type, before, after, metadata);
"""


def _explained(engine: Engine, call: Any) -> tuple[float, list[tuple[float, str]]]:
    """Run ``call`` while recording its SELECTs, then EXPLAIN (ANALYZE,
    BUFFERS) each one: the summed execution time and the per-statement
    figures."""
    statements: list[tuple[str, Any]] = []

    def record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as session:
            call(session)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    figures: list[tuple[float, str]] = []
    with engine.connect() as connection:
        for statement, parameters in statements:
            plan = connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
            ).scalar_one()
            figures.append((float(plan[0]["Execution Time"]), " ".join(statement.split())[:160]))
    return sum(time for time, _ in figures), figures


def test_measured_plan_of_a_seeded_trail(client: TestClient, db_engine: Engine) -> None:
    """BT-14: 20 000 CREATED + 50 000 edit + 200 000 Hot-rank WorkOrderDemand
    rows of other PNs, and 200 recorded lines of the PN (CREATED, edit and
    Hot-rank rows); the summed execution time of a first-page and a
    cursor-page read is printed for the OD-S7-7 trigger (100 ms), which the
    owner checks from the printed figures. The trigger is reported, not
    enforced: a wall-clock bound that tight would fail on a loaded host
    with no code change, so the test fails only above a gross-regression
    bound of ten times the trigger."""
    admin = admin_of(client)
    pn = _unique("PM")
    _work_order(admin, _line(pn, 5))
    with db_engine.begin() as connection:
        for statement in _SEED.split(";"):
            if statement.strip():
                connection.execute(sa.text(statement))
        connection.execute(sa.text(_SEED_PN), {"pn": pn})
    with db_engine.connect() as connection:
        connection.execute(sa.text("ANALYZE audit_events"))
        connection.commit()
    with Session(db_engine) as session:
        first_page = audit_trail.audit_trail_of(session, pn)
    assert first_page.total == 603 and first_page.has_more
    cursor = first_page.next_before
    assert cursor is not None

    first_ms, first_figures = _explained(
        db_engine, lambda session: audit_trail.audit_trail_of(session, pn)
    )
    cursor_ms, cursor_figures = _explained(
        db_engine, lambda session: audit_trail.audit_trail_of(session, pn, before=cursor)
    )
    for label, total, figures in (
        ("first page", first_ms, first_figures),
        ("cursor page", cursor_ms, cursor_figures),
    ):
        print(f"\nBT-14 {label}: {total:.2f} ms over {len(figures)} statements")
        for time, statement in figures:
            print(f"  {time:8.2f} ms  {statement}")
    # A gross-regression guard, not the OD-S7-7 trigger.
    assert first_ms < _GROSS_REGRESSION_MS, first_figures
    assert cursor_ms < _GROSS_REGRESSION_MS, cursor_figures
