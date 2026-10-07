"""Integration tests for Phase 14 slice 3 — Management authorization.

Exercises the full request path against a dedicated temporary database
migrated to head by the real Alembic chain (owner decisions OD-P7,
OD-P10, OD-P17):

- MA-1 / MA-5: every Management write succeeds with exactly its keys and
  records the signed-in User (``actor_user_id``) on every row it writes;
  without a key, anonymous, or with a pending password change it is
  refused and writes nothing;
- MA-2: the Management reads open with View production data or a key
  whose action a view reading them hosts (403 ``any_permission``);
- MA-3 / MA-4: the Work Order Save and Hot list content rules;
- MA-6: the allocation route split; MA-7 / MA-11: a replay by another
  User is refused (409 ``recorded_by_another_user``); MA-9: allocation
  fingerprints keep their pre-slice-3 shape;
- MA-8: the Machine lifecycle actor; MA-10: refusals never wait on a
  production lock; LK-S3-1 / LK-S3-2: the actor-row wait during a login
  rename is a bounded stall, never a deadlock; MA-12: public and station
  routes stay anonymous (station routes behind an enrolled station device
  since Phase 14 slice 4).

Set-up data is created through the harness administrator; identities
come from ``tests.auth_harness``.
"""

import functools
import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.api.route_access import ROUTE_ACCESS, Access
from app.application import allocations, production_release
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    IdentityClient,
    admin_of,
    anonymous_client,
    another_session,
    client_as,
    enroll_station_device,
    station_device_client,
    station_device_headers,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_management_authorization_api"
_A2 = "Your account does not have permission to do this."
_V1 = "Your account does not have permission to view this."
_R1 = (
    "This request was already recorded by another user. Nothing more was recorded"
    " — reload to see the current state."
)
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16

VPD = Permission.VIEW_PRODUCTION_DATA
MWO = Permission.MANAGE_WORK_ORDERS
EWOD = Permission.EDIT_WORK_ORDER_DEMAND
EWOA = Permission.EDIT_WORK_ORDER_ALLOCATION
SDP = Permission.SET_DEMAND_PRIORITY
RHI = Permission.REORDER_HOT_ITEMS
MM = Permission.MANAGE_MACHINES
MRT = Permission.MANAGE_ROUTE_TEMPLATES
MPNM = Permission.MANAGE_PART_NUMBER_MASTER

_COUNTED = ("audit_events", "machine_lifecycle_events", "work_order_allocations", "part_movements")


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
    """Anonymous application client wired to the temporary database."""
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
# The shop: ONE Department (the Hot list and the Area Board resolve it)
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


@dataclass(frozen=True)
class _Shop:
    material: _Cell
    stockroom: _Cell
    machine_id: int
    template_id: int


def _cell(admin: TestClient, department_id: int, *, is_terminal: bool) -> _Cell:
    area = _ok(
        admin.post(
            "/api/areas",
            json={
                "department_id": department_id,
                "name": _unique("AREA"),
                "is_terminal": is_terminal,
            },
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
    return _Cell(int(area["id"]), int(operation["id"]), str(station["station_id"]))


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    admin = admin_of(client)
    _ok(
        admin.put(
            "/api/barcode-configuration/machine-asset-tag-format",
            json={"prefix": "MA-", "digits": 4},
        )
    )
    department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    material = _cell(admin, int(department["id"]), is_terminal=False)
    stockroom = _cell(admin, int(department["id"]), is_terminal=True)
    machine = _ok(
        admin.post("/api/machines", json={"area_id": material.area_id, "name": _unique("M")}),
        201,
    )
    template = _ok(admin.post("/api/route-templates", json=_route_body(material)), 201)
    return _Shop(material, stockroom, int(machine["id"]), int(template["id"]))


def _route_body(cell: _Cell) -> dict[str, Any]:
    return {
        "name": _unique("ROUTE"),
        "description": None,
        "steps": [{"area_id": cell.area_id, "operation_id": cell.operation_id}],
    }


def _work_order(admin: TestClient, *lines: tuple[str, int]) -> tuple[int, list[int]]:
    body = _ok(
        admin.post(
            "/api/work-orders",
            json={
                "lines": [
                    {"part_number": pn, "requested_quantity": quantity} for pn, quantity in lines
                ]
            },
        ),
        201,
    )
    return int(body["id"]), [int(line["id"]) for line in body["demands"]]


def _release_body(shop: _Shop, pn: str, quantity: int, **extra: Any) -> dict[str, Any]:
    return {
        "part_number": pn,
        "quantity": quantity,
        "route_mode": "FLOATING",
        "starting_area_id": shop.material.area_id,
        "operation_id": shop.material.operation_id,
        "confirm_active_quantity": True,
        "device_event_id": _event(),
        **extra,
    }


def _release(admin: TestClient, shop: _Shop, wo: int, demand: int, pn: str, quantity: int) -> int:
    released = _ok(
        admin.post(
            f"/api/work-orders/{wo}/demands/{demand}/release",
            json=_release_body(shop, pn, quantity),
        ),
        201,
    )
    return int(released["quantity_flow_id"])


def _stock(client: TestClient, shop: _Shop, flow_id: int, pn: str, quantity: int) -> None:
    _ok(
        client.post(
            f"/api/scan-stations/{shop.stockroom.station_id}/stockings",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "source_area_id": shop.material.area_id,
                "target_area_id": shop.stockroom.area_id,
                "quantity": quantity,
                "device_event_id": _event(),
            },
        ),
        201,
    )


def _stocked(client: TestClient, shop: _Shop, requested: int, stocked: int) -> tuple[str, int, int]:
    """A fresh PN on a fresh one-line Work Order, ``stocked`` pcs in stock."""
    admin = admin_of(client)
    pn = _unique("PN")
    wo, demands = _work_order(admin, (pn, requested))
    flow_id = _release(admin, shop, wo, demands[0], pn, stocked)
    _stock(client, shop, flow_id, pn, stocked)
    return pn, wo, demands[0]


def _allocation_body(pn: str, demand: int, quantity: int, **extra: Any) -> dict[str, Any]:
    return {
        "part_number": pn,
        "allocation_quantity": quantity,
        "lines": [{"work_order_demand_id": demand, "quantity": quantity}],
        "device_event_id": _event(),
        **extra,
    }


def _hot_order(client: TestClient) -> list[int]:
    hot = _ok(admin_of(client).get("/api/hot-list"))
    return [int(entry["work_order_demand_id"]) for entry in hot["entries"]]


def _hot_body(action: str, expected: list[int], new: list[int]) -> dict[str, Any]:
    return {
        "device_event_id": _event(),
        "action": action,
        "expected_order": expected,
        "new_order": new,
    }


def _hot_add(client: TestClient, demand: int) -> None:
    order = _hot_order(client)
    _ok(
        admin_of(client).post(
            "/api/hot-list/changes", json=_hot_body("ADD", order, [*order, demand])
        ),
        201,
    )


def _active_demand(client: TestClient) -> int:
    _, demands = _work_order(admin_of(client), (_unique("PN"), 10))
    return demands[0]


# ---------------------------------------------------------------------------
# Row accounting
# ---------------------------------------------------------------------------


def _max_ids(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(
                connection.execute(
                    sa.text(f"SELECT coalesce(max(id), 0) FROM {table}")
                ).scalar_one()
            )
            for table in _COUNTED
        }


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED
        }


def _new_rows(engine: Engine, since: dict[str, int], table: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(f"SELECT * FROM {table} WHERE id > :since ORDER BY id"),
                {"since": since[table]},
            ).mappings()
        )


def _assert_recorded_by(engine: Engine, since: dict[str, int], user_id: int | None) -> int:
    """Every row written since ``since`` carries ``user_id``; returns how many."""
    written = 0
    for row in _new_rows(engine, since, "audit_events"):
        assert row["actor_user_id"] == user_id, dict(row)
        assert row["actor_reference"] is None, dict(row)
        written += 1
    for row in _new_rows(engine, since, "machine_lifecycle_events"):
        assert row["actor_user_id"] == user_id, dict(row)
        assert row["actor"] is None, dict(row)
        written += 1
    for row in _new_rows(engine, since, "work_order_allocations"):
        assert row["actor_user_id"] == user_id, dict(row)
        assert row["actor_reference"] is None, dict(row)
        written += 1
    for row in _new_rows(engine, since, "part_movements"):
        if row["movement_type"] == "RECEIVED":
            context = row["metadata"]["context"]
            assert context.get("actor_user_id") == user_id, context
            assert "actor" not in context, context
        written += 1
    return written


def _xmin(engine: Engine, target: tuple[str, str, object] | None) -> str | None:
    if target is None:
        return None
    table, column, key = target
    with engine.connect() as connection:
        value = connection.execute(
            sa.text(f"SELECT xmin::text FROM {table} WHERE {column}::text = :key"),
            {"key": str(key)},
        ).scalar_one_or_none()
    return None if value is None else str(value)


def _denied(response: Any, required: frozenset[Permission] | set[Permission]) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["permission_denied"] is True
    assert body["detail"] == _A2
    assert body["required_permissions"] == sorted(key.value for key in required)
    assert "any_permission" not in body


def _view_denied(response: Any, read_set: frozenset[Permission]) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body == {
        "detail": _V1,
        "permission_denied": True,
        "required_permissions": sorted(key.value for key in read_set),
        "any_permission": True,
    }


def _recorded_by_another(response: Any) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": _R1, "recorded_by_another_user": True}


# ---------------------------------------------------------------------------
# MA-1 / MA-5 — the allowed triple of every Management write
# ---------------------------------------------------------------------------

_Prepared = tuple[str, dict[str, Any], tuple[str, str, object] | None]


@dataclass(frozen=True)
class _Write:
    name: str
    method: str
    route: str
    required: frozenset[Permission]
    # Builds (path, request kwargs, target row) against fresh set-up data.
    prepare: Callable[[TestClient, _Shop], _Prepared]
    status: int = 200
    # False only for the demand line delete: no audit row of its own (F13).
    records: bool = True


def _machine(client: TestClient, shop: _Shop) -> int:
    body = _ok(
        admin_of(client).post(
            "/api/machines", json={"area_id": shop.material.area_id, "name": _unique("M")}
        ),
        201,
    )
    return int(body["id"])


def _retired_machine(client: TestClient, shop: _Shop) -> int:
    machine_id = _machine(client, shop)
    _ok(admin_of(client).post(f"/api/machines/{machine_id}/retire", json={}))
    return machine_id


def _part_number(client: TestClient, *, image: bool = False) -> str:
    pn = _unique("PN")
    _ok(admin_of(client).post("/api/part-numbers", json={"part_number": pn}), 201)
    if image:
        _ok(
            admin_of(client).put(
                f"/api/part-numbers/image?number={pn}",
                content=_PNG,
                headers={"Content-Type": "image/png"},
            )
        )
    return pn


def _template(client: TestClient, shop: _Shop, *, used: bool = False) -> int:
    admin = admin_of(client)
    template = _ok(admin.post("/api/route-templates", json=_route_body(shop.material)), 201)
    if used:
        pn = _unique("PN")
        wo, demands = _work_order(admin, (pn, 5))
        _ok(
            admin.post(
                f"/api/work-orders/{wo}/demands/{demands[0]}/release",
                json=_release_body(
                    shop, pn, 5, route_mode="PLANNED", route_template_id=template["id"]
                ),
            ),
            201,
        )
    return int(template["id"])


def _p_machine_create(client: TestClient, shop: _Shop) -> _Prepared:
    return "/api/machines", {"json": {"area_id": shop.material.area_id, "name": _unique("M")}}, None


def _p_machine_patch(client: TestClient, shop: _Shop) -> _Prepared:
    machine_id = _machine(client, shop)
    return (
        f"/api/machines/{machine_id}",
        {"json": {"name": _unique("M")}},
        ("machines", "id", machine_id),
    )


def _p_maintenance_start(client: TestClient, shop: _Shop) -> _Prepared:
    machine_id = _machine(client, shop)
    return f"/api/machines/{machine_id}/maintenance", {"json": {}}, ("machines", "id", machine_id)


def _p_maintenance_clear(client: TestClient, shop: _Shop) -> _Prepared:
    machine_id = _machine(client, shop)
    _ok(admin_of(client).post(f"/api/machines/{machine_id}/maintenance", json={}), 201)
    return f"/api/machines/{machine_id}/maintenance", {}, ("machines", "id", machine_id)


def _p_retire(client: TestClient, shop: _Shop) -> _Prepared:
    machine_id = _machine(client, shop)
    return f"/api/machines/{machine_id}/retire", {"json": {}}, ("machines", "id", machine_id)


def _p_reactivate(client: TestClient, shop: _Shop) -> _Prepared:
    machine_id = _retired_machine(client, shop)
    return (
        f"/api/machines/{machine_id}/reactivate",
        {"json": {"reason": "back in service"}},
        ("machines", "id", machine_id),
    )


def _p_pn_create(client: TestClient, shop: _Shop) -> _Prepared:
    return "/api/part-numbers", {"json": {"part_number": _unique("PN")}}, None


def _p_pn_patch(client: TestClient, shop: _Shop) -> _Prepared:
    pn = _part_number(client)
    return (
        f"/api/part-numbers?number={pn}",
        {"json": {"name": "Bracket"}},
        ("part_numbers", "part_number", pn),
    )


def _p_pn_delete(client: TestClient, shop: _Shop) -> _Prepared:
    pn = _part_number(client)
    return f"/api/part-numbers?number={pn}", {}, ("part_numbers", "part_number", pn)


def _p_pn_image_put(client: TestClient, shop: _Shop) -> _Prepared:
    pn = _part_number(client)
    return (
        f"/api/part-numbers/image?number={pn}",
        {"content": _PNG, "headers": {"Content-Type": "image/png"}},
        ("part_numbers", "part_number", pn),
    )


def _p_pn_image_delete(client: TestClient, shop: _Shop) -> _Prepared:
    pn = _part_number(client, image=True)
    return f"/api/part-numbers/image?number={pn}", {}, ("part_numbers", "part_number", pn)


def _p_template_create(client: TestClient, shop: _Shop) -> _Prepared:
    return "/api/route-templates", {"json": _route_body(shop.material)}, None


def _p_template_put(client: TestClient, shop: _Shop) -> _Prepared:
    template_id = _template(client, shop)
    return (
        f"/api/route-templates/{template_id}",
        {"json": _route_body(shop.material)},
        ("route_templates", "id", template_id),
    )


def _p_template_archive(client: TestClient, shop: _Shop) -> _Prepared:
    template_id = _template(client, shop, used=True)
    return (
        f"/api/route-templates/{template_id}/archive",
        {},
        ("route_templates", "id", template_id),
    )


def _p_template_delete(client: TestClient, shop: _Shop) -> _Prepared:
    template_id = _template(client, shop)
    return f"/api/route-templates/{template_id}", {}, ("route_templates", "id", template_id)


def _p_work_order_create(client: TestClient, shop: _Shop) -> _Prepared:
    return (
        "/api/work-orders",
        {"json": {"lines": [{"part_number": _unique("PN"), "requested_quantity": 5}]}},
        None,
    )


def _p_work_order_header(client: TestClient, shop: _Shop) -> _Prepared:
    wo, _ = _work_order(admin_of(client), (_unique("PN"), 5))
    return (
        f"/api/work-orders/{wo}",
        {"json": {"work_order_number": _unique("WO")}},
        ("work_orders", "id", wo),
    )


def _p_work_order_lines(client: TestClient, shop: _Shop) -> _Prepared:
    wo, demands = _work_order(admin_of(client), (_unique("PN"), 5))
    return (
        f"/api/work-orders/{wo}",
        {"json": {"line_edits": [{"id": demands[0], "requested_quantity": 7}]}},
        ("work_order_demands", "id", demands[0]),
    )


def _p_work_order_mixed(client: TestClient, shop: _Shop) -> _Prepared:
    wo, demands = _work_order(admin_of(client), (_unique("PN"), 5))
    return (
        f"/api/work-orders/{wo}",
        {
            "json": {
                "due_date": "2031-01-31",
                "new_lines": [{"part_number": _unique("PN"), "requested_quantity": 2}],
            }
        },
        ("work_orders", "id", wo),
    )


def _p_line_delete(client: TestClient, shop: _Shop) -> _Prepared:
    wo, demands = _work_order(admin_of(client), (_unique("PN"), 5), (_unique("PN"), 3))
    return (
        f"/api/work-orders/{wo}/demands/{demands[1]}",
        {},
        ("work_order_demands", "id", demands[1]),
    )


def _p_release(client: TestClient, shop: _Shop) -> _Prepared:
    pn = _unique("PN")
    wo, demands = _work_order(admin_of(client), (pn, 5))
    return (
        f"/api/work-orders/{wo}/demands/{demands[0]}/release",
        {"json": _release_body(shop, pn, 5)},
        ("work_order_demands", "id", demands[0]),
    )


def _p_hot_add(client: TestClient, shop: _Shop) -> _Prepared:
    demand = _active_demand(client)
    order = _hot_order(client)
    return (
        "/api/hot-list/changes",
        {"json": _hot_body("ADD", order, [*order, demand])},
        ("work_order_demands", "id", demand),
    )


def _p_hot_move(client: TestClient, shop: _Shop) -> _Prepared:
    while len(_hot_order(client)) < 2:
        _hot_add(client, _active_demand(client))
    order = _hot_order(client)
    moved = [*order[:-2], order[-1], order[-2]]
    return (
        "/api/hot-list/changes",
        {"json": _hot_body("MOVE_UP", order, moved)},
        ("work_order_demands", "id", order[-1]),
    )


def _p_allocate(client: TestClient, shop: _Shop) -> _Prepared:
    pn, _, demand = _stocked(client, shop, 10, 6)
    return (
        "/api/allocations/management",
        {"json": _allocation_body(pn, demand, 4)},
        ("work_order_demands", "id", demand),
    )


def _p_reverse(client: TestClient, shop: _Shop) -> _Prepared:
    pn, _, demand = _stocked(client, shop, 10, 6)
    allocated = _ok(
        admin_of(client).post("/api/allocations/management", json=_allocation_body(pn, demand, 4)),
        201,
    )
    allocation_id = int(allocated["rows"][0]["allocation_id"])
    return (
        f"/api/allocations/{allocation_id}/reversals",
        {"json": {"reason": "wrong Work Order", "device_event_id": _event()}},
        ("work_order_demands", "id", demand),
    )


def _keys(*keys: Permission) -> frozenset[Permission]:
    return frozenset(keys)


_WRITES: list[_Write] = [
    _Write("machine-create", "POST", "/api/machines", _keys(MM), _p_machine_create, 201),
    _Write("machine-patch", "PATCH", "/api/machines/{machine_id}", _keys(MM), _p_machine_patch),
    _Write(
        "maintenance-start",
        "POST",
        "/api/machines/{machine_id}/maintenance",
        _keys(MM),
        _p_maintenance_start,
        201,
    ),
    _Write(
        "maintenance-clear",
        "DELETE",
        "/api/machines/{machine_id}/maintenance",
        _keys(MM),
        _p_maintenance_clear,
    ),
    _Write("retire", "POST", "/api/machines/{machine_id}/retire", _keys(MM), _p_retire),
    _Write("reactivate", "POST", "/api/machines/{machine_id}/reactivate", _keys(MM), _p_reactivate),
    _Write("pn-create", "POST", "/api/part-numbers", _keys(MPNM), _p_pn_create, 201),
    _Write("pn-patch", "PATCH", "/api/part-numbers", _keys(MPNM), _p_pn_patch),
    _Write("pn-delete", "DELETE", "/api/part-numbers", _keys(MPNM), _p_pn_delete, 204),
    _Write("pn-image-put", "PUT", "/api/part-numbers/image", _keys(MPNM), _p_pn_image_put),
    _Write("pn-image-delete", "DELETE", "/api/part-numbers/image", _keys(MPNM), _p_pn_image_delete),
    _Write("route-create", "POST", "/api/route-templates", _keys(MRT), _p_template_create, 201),
    _Write("route-put", "PUT", "/api/route-templates/{template_id}", _keys(MRT), _p_template_put),
    _Write(
        "route-archive",
        "POST",
        "/api/route-templates/{template_id}/archive",
        _keys(MRT),
        _p_template_archive,
    ),
    _Write(
        "route-delete",
        "DELETE",
        "/api/route-templates/{template_id}",
        _keys(MRT),
        _p_template_delete,
        204,
    ),
    _Write("wo-create", "POST", "/api/work-orders", _keys(MWO), _p_work_order_create, 201),
    _Write(
        "wo-header", "PATCH", "/api/work-orders/{work_order_id}", _keys(MWO), _p_work_order_header
    ),
    _Write(
        "wo-lines", "PATCH", "/api/work-orders/{work_order_id}", _keys(EWOD), _p_work_order_lines
    ),
    _Write(
        "wo-mixed",
        "PATCH",
        "/api/work-orders/{work_order_id}",
        _keys(MWO, EWOD),
        _p_work_order_mixed,
    ),
    _Write(
        "line-delete",
        "DELETE",
        "/api/work-orders/{work_order_id}/demands/{demand_id}",
        _keys(EWOD),
        _p_line_delete,
        204,
        records=False,
    ),
    _Write(
        "release",
        "POST",
        "/api/work-orders/{work_order_id}/demands/{demand_id}/release",
        _keys(MWO),
        _p_release,
        201,
    ),
    _Write("hot-add", "POST", "/api/hot-list/changes", _keys(SDP), _p_hot_add, 201),
    _Write("hot-move", "POST", "/api/hot-list/changes", _keys(RHI), _p_hot_move, 201),
    _Write("allocate", "POST", "/api/allocations/management", _keys(EWOA), _p_allocate, 201),
    _Write(
        "reverse",
        "POST",
        "/api/allocations/{allocation_id}/reversals",
        _keys(EWOA),
        _p_reverse,
        201,
    ),
]

_MANAGEMENT_PREFIXES = (
    "/api/machines",
    "/api/part-numbers",
    "/api/route-templates",
    "/api/work-orders",
    "/api/hot-list",
    "/api/allocations",
)


def test_the_write_table_covers_every_management_write() -> None:
    """MA-1: one row per Management write route (static or content), keys
    matching the registry."""
    management_writes = {
        key
        for key, access in ROUTE_ACCESS.items()
        if access.access is Access.PERMISSION
        and not access.any_of
        and key[1].startswith(_MANAGEMENT_PREFIXES)
    }
    assert {(write.method, write.route) for write in _WRITES} == management_writes
    for write in _WRITES:
        access = ROUTE_ACCESS[(write.method, write.route)]
        if access.requires:
            assert access.requires == write.required, write.name
        else:
            assert write.required <= access.conditional, write.name


def _refused_without_writes(
    engine: Engine, target: tuple[str, str, object] | None, send: Callable[[], Any]
) -> Any:
    before, xmin = _counts(engine), _xmin(engine, target)
    response = send()
    assert _counts(engine) == before
    assert _xmin(engine, target) == xmin
    return response


@pytest.mark.parametrize("write", _WRITES, ids=lambda write: write.name)
def test_each_management_write_needs_exactly_its_keys(
    client: TestClient, db_engine: Engine, shop: _Shop, write: _Write
) -> None:
    """MA-1 and MA-5."""
    path, kwargs, target = write.prepare(client, shop)

    def send(caller: TestClient) -> Callable[[], Any]:
        return lambda: caller.request(write.method, path, **kwargs)

    anonymous = _refused_without_writes(db_engine, target, send(anonymous_client(client)))
    assert anonymous.status_code == 401, anonymous.text
    assert anonymous.json()["authentication_required"] is True
    pending = client_as(client, *ALL_PERMISSIONS, temporary_password=True)
    refused = _refused_without_writes(db_engine, target, send(pending))
    assert refused.status_code == 403 and refused.json()["password_change_required"] is True
    for missing in sorted(write.required):
        lacking = client_as(client, *(write.required - {missing}))
        _denied(_refused_without_writes(db_engine, target, send(lacking)), write.required)

    caller = client_as(client, *write.required)
    since = _max_ids(db_engine)
    response = send(caller)()
    assert response.status_code == write.status, response.text
    recorded = _assert_recorded_by(db_engine, since, caller.user_id)
    assert recorded > 0 if write.records else recorded == 0
    if not write.records:
        assert _xmin(db_engine, target) is None  # the line is gone


# ---------------------------------------------------------------------------
# MA-2 — any-of reads
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def read_urls(client: TestClient, shop: _Shop) -> dict[tuple[str, str], str]:
    admin = admin_of(client)
    pn, wo, demand = _stocked(client, shop, 10, 6)
    _ok(admin.post("/api/allocations/management", json=_allocation_body(pn, demand, 2)), 201)
    return {
        ("GET", "/api/work-orders"): "/api/work-orders",
        ("GET", "/api/work-orders/completed"): "/api/work-orders/completed",
        ("GET", "/api/work-orders/{work_order_id}"): f"/api/work-orders/{wo}",
        ("GET", "/api/part-numbers"): f"/api/part-numbers?number={pn}",
        ("GET", "/api/part-numbers/page"): "/api/part-numbers/page",
        ("GET", "/api/hot-list"): "/api/hot-list",
        ("GET", "/api/hot-list/candidates"): "/api/hot-list/candidates",
        ("GET", "/api/tracking"): "/api/tracking",
        ("GET", "/api/tracking/detail"): f"/api/tracking/detail?part_number={pn}",
        ("GET", "/api/tracking/movements"): f"/api/tracking/movements?part_number={pn}",
        ("GET", "/api/tracking/flows"): f"/api/tracking/flows?part_number={pn}",
        ("GET", "/api/tracking/allocations"): f"/api/tracking/allocations?part_number={pn}",
        ("GET", "/api/area-board"): "/api/area-board",
        ("GET", "/api/machines/{machine_id}"): f"/api/machines/{shop.machine_id}",
        ("GET", "/api/machines/{machine_id}/lifecycle-events"): (
            f"/api/machines/{shop.machine_id}/lifecycle-events"
        ),
        ("GET", "/api/route-templates/management"): "/api/route-templates/management",
        ("GET", "/api/route-templates/{template_id}/usage"): (
            f"/api/route-templates/{shop.template_id}/usage"
        ),
        ("GET", "/api/allocations"): f"/api/allocations?part_number={pn}",
    }


_IDENTITIES: dict[tuple[int, frozenset[Permission]], IdentityClient] = {}


def _holding(client: TestClient, keys: frozenset[Permission]) -> IdentityClient:
    """One identity per key set (and app), reused across the read cases."""
    memo = (id(client.app), keys)
    if memo not in _IDENTITIES:
        _IDENTITIES[memo] = client_as(client, *keys)
    return _IDENTITIES[memo]


def test_every_any_of_read_opens_with_any_one_key_of_its_set(
    client: TestClient, read_urls: dict[tuple[str, str], str]
) -> None:
    """MA-2."""
    any_of = {key: access.any_of for key, access in ROUTE_ACCESS.items() if access.any_of}
    assert set(read_urls) == set(any_of)
    anonymous = anonymous_client(client)
    for key, url in read_urls.items():
        read_set = any_of[key]
        for holder_key in sorted(read_set):
            response = _holding(client, frozenset({holder_key})).get(url)
            assert response.status_code == 200, (key, holder_key, response.text)
        outsider = _holding(client, frozenset(ALL_PERMISSIONS) - read_set)
        _view_denied(outsider.get(url), read_set)
        assert anonymous.get(url).status_code == 401, key


def test_the_asset_tag_format_read_needs_only_a_sign_in(client: TestClient, shop: _Shop) -> None:
    """MA-2: Administration and Machines both read it."""
    path = "/api/barcode-configuration/machine-asset-tag-format"
    assert anonymous_client(client).get(path).status_code == 401
    assert _ok(client_as(client).get(path))["prefix"] == "MA-"


# ---------------------------------------------------------------------------
# MA-3 — the Work Order Save by content
# ---------------------------------------------------------------------------


def test_a_work_order_save_needs_the_keys_of_what_it_sends(
    client: TestClient, db_engine: Engine
) -> None:
    """MA-3."""
    admin = admin_of(client)
    manager, editor = client_as(client, MWO), client_as(client, EWOD)
    wo, demands = _work_order(admin, (_unique("PN"), 5))
    path = f"/api/work-orders/{wo}"
    target = ("work_orders", "id", wo)

    header_cases: list[dict[str, Any]] = [
        {"work_order_number": _unique("WO")},
        {"due_date": "2030-05-01"},
        {"due_date": None},
        {"work_order_number": None},
        {},
        {"line_edits": [], "new_lines": []},
    ]
    for body in header_cases:
        _denied(
            _refused_without_writes(
                db_engine, target, functools.partial(editor.patch, path, json=body)
            ),
            {MWO},
        )
        _ok(manager.patch(path, json=body))

    line_edit = {"line_edits": [{"id": demands[0], "requested_quantity": 6}]}
    _denied(
        _refused_without_writes(db_engine, target, lambda: manager.patch(path, json=line_edit)),
        {EWOD},
    )
    _ok(editor.patch(path, json=line_edit))

    mixed = {"due_date": "2030-06-01", **line_edit}
    for caller in (manager, editor):
        _denied(
            _refused_without_writes(
                db_engine, target, functools.partial(caller.patch, path, json=mixed)
            ),
            {MWO, EWOD},
        )

    first_use = _unique("PN")
    since = _max_ids(db_engine)
    _ok(
        editor.patch(
            path, json={"new_lines": [{"part_number": first_use, "requested_quantity": 1}]}
        )
    )
    created = [
        row
        for row in _new_rows(db_engine, since, "audit_events")
        if row["entity_type"] == "PartNumber"
    ]
    assert [(row["entity_id"], row["event_type"]) for row in created] == [(first_use, "CREATED")]
    assert created[0]["actor_user_id"] == editor.user_id
    _assert_recorded_by(db_engine, since, editor.user_id)


# ---------------------------------------------------------------------------
# MA-4 — the Hot list by membership
# ---------------------------------------------------------------------------


def test_a_hot_list_change_needs_the_key_of_its_membership_change(
    client: TestClient, db_engine: Engine
) -> None:
    """MA-4: whatever the action label says."""
    setter, reorderer = client_as(client, SDP), client_as(client, RHI)
    path = "/api/hot-list/changes"

    def refused(caller: TestClient, body: dict[str, Any], key: Permission) -> None:
        _denied(
            _refused_without_writes(db_engine, None, lambda: caller.post(path, json=body)), {key}
        )

    # ADD / REMOVE need Set priority.
    demand = _active_demand(client)
    order = _hot_order(client)
    add = _hot_body("ADD", order, [*order, demand])
    refused(reorderer, add, SDP)
    _ok(setter.post(path, json=add), 201)
    order = _hot_order(client)
    remove = _hot_body("REMOVE", order, [entry for entry in order if entry != demand])
    refused(reorderer, remove, SDP)
    _ok(setter.post(path, json=remove), 201)

    # MOVE_UP / MOVE_DOWN / DRAG need Reorder.
    while len(_hot_order(client)) < 3:
        _hot_add(client, _active_demand(client))

    def swap_last(o: list[int]) -> list[int]:
        return [*o[:-2], o[-1], o[-2]]

    def last_to_top(o: list[int]) -> list[int]:
        return [o[-1], *o[:-1]]

    moves: list[tuple[str, Callable[[list[int]], list[int]]]] = [
        ("MOVE_UP", swap_last),
        ("MOVE_DOWN", swap_last),
        ("DRAG", last_to_top),
    ]
    for action, build in moves:
        order = _hot_order(client)
        body = _hot_body(action, order, build(order))
        refused(setter, body, RHI)
        _ok(reorderer.post(path, json=body), 201)

    # UNDO / REDO: by the delta — an insert or removal needs Set priority,
    # a move needs Reorder.
    demand = _active_demand(client)
    order = _hot_order(client)
    undo_insert = _hot_body("UNDO", order, [*order, demand])
    refused(reorderer, undo_insert, SDP)
    _ok(setter.post(path, json=undo_insert), 201)
    order = _hot_order(client)
    undo_remove = _hot_body("UNDO", order, order[:-1])
    refused(reorderer, undo_remove, SDP)
    _ok(setter.post(path, json=undo_remove), 201)
    for action in ("UNDO", "REDO"):
        order = _hot_order(client)
        body = _hot_body(action, order, [order[-1], *order[:-1]])
        refused(setter, body, RHI)
        _ok(reorderer.post(path, json=body), 201)

    # The label never selects the key: an insert labelled MOVE_UP.
    demand = _active_demand(client)
    order = _hot_order(client)
    mislabelled = _hot_body("MOVE_UP", order, [*order, demand])
    refused(reorderer, mislabelled, SDP)
    rejected = _refused_without_writes(db_engine, None, lambda: setter.post(path, json=mislabelled))
    assert rejected.status_code == 422, rejected.text


# ---------------------------------------------------------------------------
# MA-5 — actors on the consequences of a Management command
# ---------------------------------------------------------------------------


def test_a_save_completing_a_work_order_records_its_user(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-5: the lowered last short line completes the Work Order and
    leaves the Hot list — every row carries the User who saved."""
    admin = admin_of(client)
    pn, wo, demand = _stocked(client, shop, 10, 4)
    _ok(admin.post("/api/allocations/management", json=_allocation_body(pn, demand, 4)), 201)
    _hot_add(client, demand)
    editor = client_as(client, EWOD)
    since = _max_ids(db_engine)
    saved = _ok(
        editor.patch(
            f"/api/work-orders/{wo}",
            json={"line_edits": [{"id": demand, "requested_quantity": 4}]},
        )
    )
    assert saved["status"] == "COMPLETED"
    rows = _new_rows(db_engine, since, "audit_events")
    completion = [row for row in rows if "completion" in (row["metadata"] or {})]
    hot = [row for row in rows if "hot_list_change" in (row["metadata"] or {})]
    assert len(completion) == 1 and hot
    _assert_recorded_by(db_engine, since, editor.user_id)


def test_a_line_delete_completing_a_work_order_records_its_user(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-5: the confirmed removal of a short Hot line leaves only fully
    allocated lines — its Hot removal and the completion carry the User."""
    admin = admin_of(client)
    allocated_pn, short_pn = _unique("PN"), _unique("PN")
    wo, demands = _work_order(admin, (allocated_pn, 4), (short_pn, 5))
    flow_id = _release(admin, shop, wo, demands[0], allocated_pn, 4)
    _stock(client, shop, flow_id, allocated_pn, 4)
    _ok(
        admin.post(
            "/api/allocations/management", json=_allocation_body(allocated_pn, demands[0], 4)
        ),
        201,
    )
    _hot_add(client, demands[1])
    editor = client_as(client, EWOD)
    since = _max_ids(db_engine)
    _ok(
        editor.delete(f"/api/work-orders/{wo}/demands/{demands[1]}?confirm_hot_removal=true"),
        204,
    )
    rows = _new_rows(db_engine, since, "audit_events")
    assert [row for row in rows if "completion" in (row["metadata"] or {})]
    assert [row for row in rows if "hot_list_change" in (row["metadata"] or {})]
    _assert_recorded_by(db_engine, since, editor.user_id)


def test_a_station_allocation_and_its_hot_removal_record_no_user(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-5: Scan Station rows keep actor_user_id NULL."""
    pn, _, demand = _stocked(client, shop, 6, 6)
    _hot_add(client, demand)
    since = _max_ids(db_engine)
    allocated = _ok(
        client.post(
            "/api/allocations",
            json=_allocation_body(pn, demand, 6, station_id=shop.stockroom.station_id),
        ),
        201,
    )
    assert allocated["rows"][0]["source"] == "STOCKROOM"
    assert allocated["rows"][0]["actor_user_id"] is None
    rows = _new_rows(db_engine, since, "audit_events")
    assert [row for row in rows if "hot_list_change" in (row["metadata"] or {})]
    assert _assert_recorded_by(db_engine, since, None) > 0


# ---------------------------------------------------------------------------
# MA-6 — the allocation route split
# ---------------------------------------------------------------------------


def test_the_station_and_management_allocation_routes_are_split(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-6."""
    pn, _, demand = _stocked(client, shop, 10, 10)
    anonymous = anonymous_client(client)
    # Phase 14 slice 4: the station route needs an enrolled device first —
    # with the Stockroom's device a body without a station is still 422.
    stockroom_device = station_device_headers(
        enroll_station_device(db_engine, shop.stockroom.station_id)
    )
    for body in (
        _allocation_body(pn, demand, 1),
        _allocation_body(pn, demand, 1, station_id=None),
    ):
        refused = _refused_without_writes(
            db_engine,
            None,
            functools.partial(
                anonymous.post, "/api/allocations", json=body, headers=stockroom_device
            ),
        )
        assert refused.status_code == 422, refused.text
        refused = _refused_without_writes(
            db_engine, None, functools.partial(anonymous.post, "/api/allocations", json=body)
        )
        assert refused.status_code == 401, refused.text
        assert refused.json()["station_device_required"] is True
    station_body = _allocation_body(pn, demand, 1, station_id=shop.stockroom.station_id)
    refused = _refused_without_writes(
        db_engine, None, functools.partial(anonymous.post, "/api/allocations", json=station_body)
    )
    assert refused.status_code == 401 and refused.json()["station_device_required"] is True
    station = _ok(station_device_client(anonymous).post("/api/allocations", json=station_body), 201)
    assert station["rows"][0]["source"] == "STOCKROOM"

    path = "/api/allocations/management"
    editor = client_as(client, EWOA)
    with_station = _allocation_body(pn, demand, 1, station_id=shop.stockroom.station_id)
    assert (
        _refused_without_writes(
            db_engine, None, lambda: editor.post(path, json=with_station)
        ).status_code
        == 422
    )
    unsigned = _refused_without_writes(
        db_engine, None, lambda: anonymous.post(path, json=_allocation_body(pn, demand, 1))
    )
    assert unsigned.status_code == 401
    _denied(
        _refused_without_writes(
            db_engine,
            None,
            lambda: client_as(client, VPD, MWO, EWOD).post(
                path, json=_allocation_body(pn, demand, 1)
            ),
        ),
        {EWOA},
    )
    allocated = _ok(editor.post(path, json=_allocation_body(pn, demand, 2)), 201)
    row = allocated["rows"][0]
    assert (row["source"], row["station_id"], row["actor_user_id"]) == (
        "MANAGEMENT",
        None,
        editor.user_id,
    )
    with db_engine.connect() as connection:
        worker = connection.execute(
            sa.text("SELECT allocated_by_worker_id FROM work_order_allocations WHERE id = :id"),
            {"id": row["allocation_id"]},
        ).scalar_one()
    assert worker is None
    # The routine limit: never beyond the remaining shortage (7 left).
    beyond = _refused_without_writes(
        db_engine, None, lambda: editor.post(path, json=_allocation_body(pn, demand, 8))
    )
    assert beyond.status_code == 409 and "can take" in beyond.json()["detail"]

    reversal = f"/api/allocations/{row['allocation_id']}/reversals"
    station_reversal = {
        "reason": "x",
        "station_id": shop.stockroom.station_id,
        "device_event_id": _event(),
    }
    assert (
        _refused_without_writes(
            db_engine, None, lambda: editor.post(reversal, json=station_reversal)
        ).status_code
        == 422
    )
    plain = {"reason": "x", "device_event_id": _event()}
    assert (
        _refused_without_writes(
            db_engine, None, lambda: anonymous.post(reversal, json=plain)
        ).status_code
        == 401
    )
    reversed_ = _ok(editor.post(reversal, json=plain), 201)
    assert (reversed_["rows"][0]["source"], reversed_["rows"][0]["station_id"]) == (
        "MANAGEMENT",
        None,
    )


# ---------------------------------------------------------------------------
# MA-7 — a replay by another User
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Replay:
    name: str
    keys: frozenset[Permission]
    # (path, body, a different body under the same device_event_id)
    prepare: Callable[[TestClient, _Shop], tuple[str, dict[str, Any], dict[str, Any]]]


def _r_allocate(client: TestClient, shop: _Shop) -> tuple[str, dict[str, Any], dict[str, Any]]:
    pn, _, demand = _stocked(client, shop, 10, 10)
    body = _allocation_body(pn, demand, 3)
    return (
        "/api/allocations/management",
        body,
        {
            **body,
            "allocation_quantity": 4,
            "lines": [{"work_order_demand_id": demand, "quantity": 4}],
        },
    )


def _r_reverse(client: TestClient, shop: _Shop) -> tuple[str, dict[str, Any], dict[str, Any]]:
    pn, _, demand = _stocked(client, shop, 10, 10)
    allocated = _ok(
        admin_of(client).post("/api/allocations/management", json=_allocation_body(pn, demand, 3)),
        201,
    )
    body = {"reason": "wrong Work Order", "device_event_id": _event()}
    return (
        f"/api/allocations/{allocated['rows'][0]['allocation_id']}/reversals",
        body,
        {**body, "reason": "another reason"},
    )


def _r_release(client: TestClient, shop: _Shop) -> tuple[str, dict[str, Any], dict[str, Any]]:
    pn = _unique("PN")
    wo, demands = _work_order(admin_of(client), (pn, 10))
    body = _release_body(shop, pn, 3)
    return f"/api/work-orders/{wo}/demands/{demands[0]}/release", body, {**body, "quantity": 4}


def _r_hot(client: TestClient, shop: _Shop) -> tuple[str, dict[str, Any], dict[str, Any]]:
    demand = _active_demand(client)
    order = _hot_order(client)
    body = _hot_body("ADD", order, [*order, demand])
    # The same delta under another action label: a different fingerprint.
    return "/api/hot-list/changes", body, {**body, "action": "UNDO"}


_REPLAYS = [
    _Replay("allocate", _keys(EWOA), _r_allocate),
    _Replay("reverse", _keys(EWOA), _r_reverse),
    _Replay("release", _keys(MWO), _r_release),
    _Replay("hot-list", _keys(SDP), _r_hot),
]


@pytest.mark.parametrize("replay", _REPLAYS, ids=lambda replay: replay.name)
def test_a_replay_by_another_user_is_refused(
    client: TestClient, db_engine: Engine, shop: _Shop, replay: _Replay
) -> None:
    """MA-7: the same User from another session replays; another User
    holding the same keys is refused; a different body is the plain
    idempotency conflict (the fingerprint is checked first)."""
    path, body, different = replay.prepare(client, shop)
    first = client_as(client, *replay.keys)
    _ok(first.post(path, json=body), 201)
    replayed = _refused_without_writes(
        db_engine, None, lambda: another_session(client, first).post(path, json=body)
    )
    assert replayed.status_code == 200, replayed.text
    other = client_as(client, *replay.keys)
    _recorded_by_another(
        _refused_without_writes(db_engine, None, lambda: other.post(path, json=body))
    )
    conflict = _refused_without_writes(db_engine, None, lambda: first.post(path, json=different))
    assert conflict.status_code == 409, conflict.text
    assert "recorded_by_another_user" not in conflict.json()


def test_a_command_recorded_before_sign_in_is_not_replayed_to_a_user(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-7 / OD-S3-4: a stored NULL actor differs from every User."""
    pn, _, demand = _stocked(client, shop, 10, 10)
    editor = client_as(client, EWOA)
    body = _allocation_body(pn, demand, 2)
    _ok(editor.post("/api/allocations/management", json=body), 201)
    # Simulate a pre-slice-3 row: the append-only trigger is lifted for
    # this one test UPDATE only.
    with db_engine.begin() as connection:
        connection.execute(sa.text("ALTER TABLE work_order_allocations DISABLE TRIGGER USER"))
        connection.execute(
            sa.text(
                "UPDATE work_order_allocations SET actor_user_id = NULL"
                " WHERE device_event_id = :event"
            ),
            {"event": body["device_event_id"]},
        )
        connection.execute(sa.text("ALTER TABLE work_order_allocations ENABLE TRIGGER USER"))
    _recorded_by_another(
        _refused_without_writes(
            db_engine, None, lambda: editor.post("/api/allocations/management", json=body)
        )
    )


# ---------------------------------------------------------------------------
# MA-8 — the Machine lifecycle actor
# ---------------------------------------------------------------------------


def test_the_machine_lifecycle_records_the_signed_in_user(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-8."""
    machine_id = _machine(client, shop)
    manager = client_as(client, MM)
    path = f"/api/machines/{machine_id}"
    for action, body in (
        ("retire", {"actor": "Peter"}),
        ("reactivate", {"reason": "x", "actor": "Mai"}),
    ):
        refused = _refused_without_writes(
            db_engine,
            ("machines", "id", machine_id),
            functools.partial(manager.post, f"{path}/{action}", json=body),
        )
        assert refused.status_code == 422, refused.text
    _ok(manager.post(f"{path}/retire", json={"reason": "worn out"}))
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO machine_lifecycle_events (machine_id, event_type, occurred_at,"
                " actor, reason, before_state, after_state) VALUES (:id, 'REACTIVATED', now(),"
                " 'legacy', 'recorded before sign-in', 'RETIRED', 'ACTIVE')"
            ),
            {"id": machine_id},
        )
        connection.execute(
            sa.text("UPDATE users SET is_active = false WHERE id = :id"), {"id": manager.user_id}
        )
    events = _ok(client_as(client, VPD).get(f"{path}/lifecycle-events"))
    retired, legacy = events
    assert retired["actor"] is None
    assert retired["actor_user"]["id"] == manager.user_id
    assert retired["actor_user"]["display_name"].startswith("Test ")
    assert set(retired["actor_user"]) == {"id", "display_name", "avatar_updated_at"}
    assert (legacy["actor"], legacy["actor_user"]) == ("legacy", None)


# ---------------------------------------------------------------------------
# MA-9 — allocation fingerprints keep their pre-slice-3 shape
# ---------------------------------------------------------------------------


def _fingerprint(normalized: dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_fingerprints(engine: Engine, device_event_id: str) -> set[str]:
    with engine.connect() as connection:
        return {
            str(value)
            for value in connection.execute(
                sa.text(
                    "SELECT metadata ->> 'request_fingerprint' FROM work_order_allocations"
                    " WHERE device_event_id = :event"
                ),
                {"event": device_event_id},
            ).scalars()
        }


def test_allocation_fingerprints_keep_the_pre_sign_in_key_set(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-9: identity is never part of a fingerprint."""
    pn, _, demand = _stocked(client, shop, 10, 10)
    editor = client_as(client, EWOA)
    management = _allocation_body(pn, demand, 2)
    allocated = _ok(editor.post("/api/allocations/management", json=management), 201)
    station = _allocation_body(pn, demand, 1, station_id=shop.stockroom.station_id)
    _ok(client.post("/api/allocations", json=station), 201)
    reversal = {"reason": "recount", "device_event_id": _event()}
    allocation_id = allocated["rows"][0]["allocation_id"]
    _ok(editor.post(f"/api/allocations/{allocation_id}/reversals", json=reversal), 201)

    def allocate(station_id: str | None, quantity: int) -> str:
        return _fingerprint(
            {
                "command": "ALLOCATE",
                "part_number": pn,
                "allocation_quantity": quantity,
                "lines": [[demand, quantity]],
                "station_id": station_id,
                "actor": None,
                "reason": None,
            }
        )

    assert _stored_fingerprints(db_engine, management["device_event_id"]) == {allocate(None, 2)}
    assert _stored_fingerprints(db_engine, station["device_event_id"]) == {
        allocate(shop.stockroom.station_id, 1)
    }
    assert _stored_fingerprints(db_engine, reversal["device_event_id"]) == {
        _fingerprint(
            {
                "command": "REVERSE_ALLOCATION",
                "allocation_id": allocation_id,
                "reason": "recount",
                "station_id": None,
                "actor": None,
            }
        )
    }


# ---------------------------------------------------------------------------
# MA-10 / MA-11 / LK-S3 — refusals, races and the actor-row wait
# ---------------------------------------------------------------------------


def _run(results: dict[str, Any], name: str, call: Callable[[], Any]) -> threading.Thread:
    def target() -> None:
        try:
            results[name] = call()
        except Exception as exc:  # noqa: BLE001 — collected for assertions
            results[name] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive()


def _advisory(connection: sa.Connection, key: str) -> None:
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )


def test_authorization_refusals_never_wait_on_a_production_lock(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """MA-10."""
    pn, wo, demand = _stocked(client, shop, 10, 10)
    allocated = _ok(
        admin_of(client).post("/api/allocations/management", json=_allocation_body(pn, demand, 2)),
        201,
    )
    reversal = f"/api/allocations/{allocated['rows'][0]['allocation_id']}/reversals"
    order = _hot_order(client)
    requests: list[tuple[str, dict[str, Any]]] = [
        ("/api/allocations/management", _allocation_body(pn, demand, 1)),
        (reversal, {"reason": "x", "device_event_id": _event()}),
        (f"/api/work-orders/{wo}/demands/{demand}/release", _release_body(shop, pn, 1)),
        ("/api/hot-list/changes", _hot_body("ADD", order, [*order, _active_demand(client)])),
    ]
    callers = (anonymous_client(client), client_as(client, VPD))
    for key in (f"partflow:part-number:{pn}", "partflow:hot-list"):
        before = _counts(db_engine)
        with db_engine.connect() as holder:
            transaction = holder.begin()
            _advisory(holder, key)
            results: dict[str, Any] = {}
            threads = [
                _run(results, f"{index}-{path}", functools.partial(caller.post, path, json=body))
                for index, caller in enumerate(callers)
                for path, body in requests
            ]
            for thread in threads:
                thread.join(timeout=2)
                assert not thread.is_alive(), key
            transaction.rollback()
        assert sorted({response.status_code for response in results.values()}) == [401, 403]
        assert _counts(db_engine) == before


@pytest.mark.parametrize("command_", ["allocate", "hot-list"])
def test_two_users_racing_one_device_event_id_get_one_winner(
    client: TestClient, db_engine: Engine, shop: _Shop, command_: str
) -> None:
    """MA-11: the loser re-checks after the lock and is refused."""
    if command_ == "allocate":
        pn, _, demand = _stocked(client, shop, 10, 10)
        path, body, keys = "/api/allocations/management", _allocation_body(pn, demand, 3), {EWOA}
    else:
        order = _hot_order(client)
        body = _hot_body("ADD", order, [*order, _active_demand(client)])
        path, keys = "/api/hot-list/changes", {SDP}
    callers = [client_as(client, *keys), client_as(client, *keys)]
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def send(caller: TestClient) -> Callable[[], Any]:
        def call() -> Any:
            barrier.wait(timeout=10)
            return caller.post(path, json=body)

        return call

    threads = [_run(results, str(index), send(caller)) for index, caller in enumerate(callers)]
    for thread in threads:
        _finish(thread)
    statuses = sorted(response.status_code for response in results.values())
    assert statuses == [201, 409], [response.text for response in results.values()]
    loser = next(response for response in results.values() if response.status_code == 409)
    _recorded_by_another(loser)


def _blind_committed_lookup(monkeypatch: pytest.MonkeyPatch, replay: str, calls: int = 2) -> None:
    """Blind the committed-command lookup of ``replay`` for its first
    ``calls`` calls — the pre-lock check and the re-check after the locks
    — so the request reaches the ``device_event_id`` UNIQUE constraint at
    COMMIT; the lookup after the rollback reads the committed winner."""
    module: Any
    missing: list[Any] | None
    if replay == "release":
        module, name, missing = production_release, "_committed_release", None
    else:
        module, name, missing = allocations, "committed_allocation_command", []
    real = getattr(module, name)
    remaining = {"calls": calls}

    def blinded(session: Any, device_event_id: str) -> Any:
        if remaining["calls"]:
            remaining["calls"] -= 1
            return missing
        return real(session, device_event_id)

    monkeypatch.setattr(module, name, blinded)


@pytest.mark.parametrize("replay", ["release", "allocate"])
def test_another_users_replay_lost_at_commit_is_refused(
    client: TestClient,
    db_engine: Engine,
    shop: _Shop,
    monkeypatch: pytest.MonkeyPatch,
    replay: str,
) -> None:
    """MA-7 / MA-11 on the COMMIT path: another User's identical request
    that loses the race at the ``device_event_id`` UNIQUE constraint is
    refused as recorded by another user — never the winner's result as
    a replay — and nothing of it is written. (A reversal racing itself
    violates ``uq_work_order_allocations_reverses_allocation_id`` first
    — the index PostgreSQL checks first — so its loser is the
    "already reversed" conflict, covered by the reversal race tests.)"""
    spec = next(candidate for candidate in _REPLAYS if candidate.name == replay)
    path, body, _ = spec.prepare(client, shop)
    winner = client_as(client, *spec.keys)
    _ok(winner.post(path, json=body), 201)
    loser = client_as(client, *spec.keys)
    _blind_committed_lookup(monkeypatch, replay)
    _recorded_by_another(
        _refused_without_writes(db_engine, None, lambda: loser.post(path, json=body))
    )
    # The same User's retry on the same path is still the replay.
    _blind_committed_lookup(monkeypatch, replay)
    replayed = _refused_without_writes(
        db_engine, None, lambda: another_session(client, winner).post(path, json=body)
    )
    assert replayed.status_code == 200, replayed.text


def test_a_login_rename_of_the_actor_is_a_bounded_stall(
    client: TestClient, db_engine: Engine, shop: _Shop
) -> None:
    """LK-S3-1 / LK-S3-2: the actor's Management allocation waits at its
    INSERT while holding the PN and Hot locks; a station allocation of
    another PN and a Hot list change queue on the Hot lock; a station
    transfer of a third PN does not; the rename's COMMIT releases all."""
    x_pn, _, x_demand = _stocked(client, shop, 10, 10)
    y_pn, _, y_demand = _stocked(client, shop, 10, 10)
    admin = admin_of(client)
    z_pn = _unique("PN")
    z_wo, z_demands = _work_order(admin, (z_pn, 10))
    z_flow = _release(admin, shop, z_wo, z_demands[0], z_pn, 5)
    actor, setter = client_as(client, EWOA), client_as(client, SDP)
    order = _hot_order(client)
    hot_change = _hot_body("ADD", order, [*order, _active_demand(client)])
    results: dict[str, Any] = {}
    with db_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            sa.text("SELECT id FROM users WHERE id = :id FOR UPDATE"), {"id": actor.user_id}
        )
        allocation = _run(
            results,
            "actor",
            lambda: actor.post(
                "/api/allocations/management", json=_allocation_body(x_pn, x_demand, 2)
            ),
        )
        _assert_blocked(allocation)
        station = _run(
            results,
            "station",
            lambda: client.post(
                "/api/allocations",
                json=_allocation_body(y_pn, y_demand, 2, station_id=shop.stockroom.station_id),
            ),
        )
        hot = _run(results, "hot", lambda: setter.post("/api/hot-list/changes", json=hot_change))
        _assert_blocked(station)
        _assert_blocked(hot)
        transfer = _run(
            results,
            "transfer",
            lambda: client.post(
                f"/api/scan-stations/{shop.stockroom.station_id}/stockings",
                json={
                    "part_number": z_pn,
                    "quantity_flow_id": z_flow,
                    "source_area_id": shop.material.area_id,
                    "target_area_id": shop.stockroom.area_id,
                    "quantity": 5,
                    "device_event_id": _event(),
                },
            ),
        )
        transfer.join(timeout=2)
        assert not transfer.is_alive()
        transaction.commit()
    for thread in (allocation, station, hot):
        _finish(thread)
    assert [results[name].status_code for name in ("actor", "station", "hot", "transfer")] == [
        201,
        201,
        201,
        201,
    ], {name: getattr(response, "text", response) for name, response in results.items()}


# ---------------------------------------------------------------------------
# MA-12 — public and station routes stay anonymous
# ---------------------------------------------------------------------------


def test_public_and_station_routes_stay_anonymous(client: TestClient, shop: _Shop) -> None:
    """MA-12: no cookie and no CSRF header. Since Phase 14 slice 4 the
    station routes need an enrolled station device (still no sign-in)."""
    anonymous = anonymous_client(client)
    station_device = station_device_client(anonymous)
    pn, _, demand = _stocked(client, shop, 10, 4)
    for path in (
        f"/api/scan-stations/{shop.material.station_id}",
        "/api/machines",
        "/api/route-templates",
    ):
        assert anonymous.get(path).status_code == 200, path
    for path in (
        f"/api/allocations/suggestion?part_number={pn}",
        f"/api/scan-stations/{shop.stockroom.station_id}/context",
    ):
        assert station_device.get(path).status_code == 200, path
        refused = anonymous.get(path)
        assert refused.status_code == 401 and refused.json()["station_device_required"], path
    board = anonymous.get("/api/production-board")
    assert board.status_code == 200, board.text
    image = anonymous.get(f"/api/part-numbers/image?number={pn}")
    assert image.status_code == 404, image.text
    body = _allocation_body(pn, demand, 4, station_id=shop.stockroom.station_id)
    refused = anonymous.post("/api/allocations", json=body)
    assert refused.status_code == 401 and refused.json()["station_device_required"] is True
    allocated = station_device.post("/api/allocations", json=body)
    assert allocated.status_code == 201, allocated.text
