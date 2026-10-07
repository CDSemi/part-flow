"""Integration tests for Phase 14 slice 5 — the Management allocation
workflow and the authorized beyond-demand correction.

Exercises the full request path against a dedicated temporary database
migrated to head by the real Alembic chain (PROJECT_PROFILE §8.12, §18;
owner decisions OD-P10, OD-P12/P13, OD-S5-3):

- BC-1 … BC-5: ``POST /api/allocations/corrections`` records one
  ``exceeds_demand`` row (Management, reasoned, the signed-in User) on
  an open or a completed Work Order, completes an open one, keeps a
  completed one's done date, refuses within the remaining demand
  (C5-1) and beyond the available stock (C5-2);
- BC-6 / BC-7 / BC-21: input and authorization refusals write nothing;
  NUL in any allocation reason is a typed 422;
- BC-8: idempotency, actor-aware replay and two concurrent races;
- BC-9: the automatic Hot removal; BC-10: reversal of a correction;
  BC-11: routine allocation still never exceeds the remaining demand;
- BC-12: ``rebuild_completed_at`` equals every ``completed_at`` after
  each step of the slice's sequences and reconcile checks (e)/(f) stay
  clean for the Work Order;
- BC-13: the demand-edit floor judges only a changed Qty;
- BC-15 … BC-20: concurrency, lock order and the lock takers without
  advisory locks;
- CX-1 … CX-3: ``GET /api/allocations/management/context``;
- TR-1: Tracking's allocation history and the allocation responses carry
  ``exceeds_demand`` and the recording User.

The database CHECK of a correction row (BC-14) is exercised in
``test_phase14_schema.py``. Set-up data is created through the harness
administrator; identities come from ``tests.auth_harness``.
"""

import contextlib
import datetime
import functools
import json
import os
import re
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
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app import cli
from app.application import allocations, authentication
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    admin_of,
    anonymous_client,
    another_session,
    client_as,
    station_device_client,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_allocation_corrections_api"
_REASON = "customer accepted overage"
_R1 = (
    "This request was already recorded by another user. Nothing more was recorded"
    " — reload to see the current state."
)
_REUSED = (
    "This device_event_id was already used for a different allocation request. Nothing was"
    " recorded — a new intent needs a new device_event_id."
)
_A2 = "Your account does not have permission to do this."

EWOA = Permission.EDIT_WORK_ORDER_ALLOCATION
VPD = Permission.VIEW_PRODUCTION_DATA
SDP = Permission.SET_DEMAND_PRIORITY

_COUNTED = ("work_order_allocations", "audit_events", "part_movements")


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
# The shop: ONE Department (the Hot list resolves it)
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
    department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    material = _cell(admin, int(department["id"]), is_terminal=False)
    stockroom = _cell(admin, int(department["id"]), is_terminal=True)
    return _Shop(material, stockroom)


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


def _supply(client: TestClient, shop: _Shop, pn: str, quantity: int) -> int:
    """``quantity`` more pcs of ``pn`` in stock, released and stocked
    against a supply line of its own (left unallocated, so open)."""
    admin = admin_of(client)
    wo, [demand] = _work_order(admin, (pn, quantity))
    released = _ok(
        admin.post(
            f"/api/work-orders/{wo}/demands/{demand}/release",
            json={
                "part_number": pn,
                "quantity": quantity,
                "route_mode": "FLOATING",
                "starting_area_id": shop.material.area_id,
                "operation_id": shop.material.operation_id,
                "confirm_active_quantity": True,
                "device_event_id": _event(),
            },
        ),
        201,
    )
    _ok(
        client.post(
            f"/api/scan-stations/{shop.stockroom.station_id}/stockings",
            json={
                "part_number": pn,
                "quantity_flow_id": released["quantity_flow_id"],
                "source_area_id": shop.material.area_id,
                "target_area_id": shop.stockroom.area_id,
                "quantity": quantity,
                "device_event_id": _event(),
            },
        ),
        201,
    )
    return demand


def _line(client: TestClient, shop: _Shop, requested: int, stocked: int) -> tuple[str, int, int]:
    """A fresh PN on a fresh one-line Work Order with ``stocked`` pcs in stock
    (the target line is created first, so it leads the canonical order)."""
    pn = _unique("PN")
    wo, [demand] = _work_order(admin_of(client), (pn, requested))
    if stocked:
        _supply(client, shop, pn, stocked)
    return pn, wo, demand


def _allocation_body(pn: str, demand: int, quantity: int, **extra: Any) -> dict[str, Any]:
    return {
        "part_number": pn,
        "allocation_quantity": quantity,
        "lines": [{"work_order_demand_id": demand, "quantity": quantity}],
        "device_event_id": _event(),
        **extra,
    }


def _allocate(caller: TestClient, pn: str, demand: int, quantity: int) -> dict[str, Any]:
    result: dict[str, Any] = _ok(
        caller.post("/api/allocations/management", json=_allocation_body(pn, demand, quantity)),
        201,
    )
    return result


def _station_allocate(
    client: TestClient, shop: _Shop, pn: str, demand: int, quantity: int
) -> dict[str, Any]:
    result: dict[str, Any] = _ok(
        client.post(
            "/api/allocations",
            json=_allocation_body(pn, demand, quantity, station_id=shop.stockroom.station_id),
        ),
        201,
    )
    return result


def _correction_body(pn: str, demand: int, quantity: object, **extra: Any) -> dict[str, Any]:
    return {
        "part_number": pn,
        "work_order_demand_id": demand,
        "quantity": quantity,
        "reason": _REASON,
        "device_event_id": _event(),
        **extra,
    }


def _correct(caller: TestClient, pn: str, demand: int, quantity: int, **extra: Any) -> Any:
    return caller.post(
        "/api/allocations/corrections", json=_correction_body(pn, demand, quantity, **extra)
    )


def _reverse(caller: TestClient, allocation_id: int, reason: str = "recount") -> Any:
    return caller.post(
        f"/api/allocations/{allocation_id}/reversals",
        json={"reason": reason, "device_event_id": _event()},
    )


def _context(caller: TestClient, **query: Any) -> Any:
    return caller.get("/api/allocations/management/context", params=query)


def _hot_order(client: TestClient) -> list[int]:
    hot = _ok(admin_of(client).get("/api/hot-list"))
    return [int(entry["work_order_demand_id"]) for entry in hot["entries"]]


def _hot_add(client: TestClient, demand: int) -> None:
    order = _hot_order(client)
    _ok(
        admin_of(client).post(
            "/api/hot-list/changes",
            json={
                "device_event_id": _event(),
                "action": "ADD",
                "expected_order": order,
                "new_order": [*order, demand],
            },
        ),
        201,
    )


# ---------------------------------------------------------------------------
# Database reads
# ---------------------------------------------------------------------------


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar()


def _completed_at(engine: Engine, wo: int) -> datetime.datetime | None:
    value: datetime.datetime | None = _scalar(
        engine, "SELECT completed_at FROM work_orders WHERE id = :id", id=wo
    )
    return value


def _allocated_quantity(engine: Engine, demand: int) -> int:
    return int(
        _scalar(
            engine, "SELECT allocated_quantity FROM work_order_demands WHERE id = :id", id=demand
        )
    )


def _allocation(engine: Engine, allocation_id: int) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                sa.text("SELECT * FROM work_order_allocations WHERE id = :id"),
                {"id": allocation_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED
        }


def _xmin(engine: Engine, demand: int) -> str:
    return str(
        _scalar(engine, "SELECT xmin::text FROM work_order_demands WHERE id = :id", id=demand)
    )


def _refused_without_writes(engine: Engine, demand: int, send: Callable[[], Any]) -> Any:
    before, xmin = _counts(engine), _xmin(engine, demand)
    response = send()
    assert _counts(engine) == before, response.text
    assert _xmin(engine, demand) == xmin, response.text
    return response


def _rebuilt_completed_at(engine: Engine) -> dict[int, datetime.datetime | None]:
    with Session(engine) as session:
        return allocations.rebuild_completed_at(session)


def _assert_projections_match_replay(engine: Engine) -> None:
    """Every stored projection equals its replay from the rows."""
    with Session(engine) as session:
        allocated = allocations.rebuild_allocated_quantities(session)
        completed = allocations.rebuild_completed_at(session)
    with engine.connect() as connection:
        for demand_id, stored in connection.execute(
            sa.text("SELECT id, allocated_quantity FROM work_order_demands")
        ):
            assert stored == allocated.get(int(demand_id), 0), demand_id
        for work_order_id, stored in connection.execute(
            sa.text("SELECT id, completed_at FROM work_orders")
        ):
            assert stored == completed.get(int(work_order_id)), work_order_id


def _reconcile_findings(
    engine: Engine, capsys: pytest.CaptureFixture[str], wo: int
) -> list[dict[str, Any]]:
    """Checks (e) and (f) of ``python -m app.cli reconcile``, in process
    against the module database (``DATABASE_URL`` names it while the
    module client runs): the findings naming the Work Order or one of its
    demand lines."""
    with engine.connect() as connection:
        demands = {
            int(demand_id)
            for demand_id in connection.execute(
                sa.text("SELECT id FROM work_order_demands WHERE work_order_id = :wo"),
                {"wo": wo},
            ).scalars()
        }
    get_settings.cache_clear()
    capsys.readouterr()
    try:
        exit_code = cli.main(["reconcile", "--check", "e", "--check", "f"])
    finally:
        get_settings.cache_clear()
    report = json.loads(capsys.readouterr().out)
    assert exit_code in (0, 1), report
    named = {("WorkOrder", wo)} | {("WorkOrderDemand", demand) for demand in demands}
    return [
        finding
        for check in report["checks"]
        for finding in check["findings"]
        if (finding["entity"]["type"], finding["entity"]["id"]) in named
    ]


def _assert_done_date(
    engine: Engine,
    capsys: pytest.CaptureFixture[str],
    wo: int,
    expected: datetime.datetime | None,
) -> None:
    """BC-12: the stored done date, its replay and reconcile agree."""
    assert _completed_at(engine, wo) == expected
    assert _rebuilt_completed_at(engine)[wo] == expected
    assert _reconcile_findings(engine, capsys, wo) == []


# ---------------------------------------------------------------------------
# BC-1 … BC-5 — the correction
# ---------------------------------------------------------------------------


def test_correction_on_a_completed_work_order(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-1."""
    admin = admin_of(client)
    pn, wo, demand = _line(client, shop, 10, 12)
    _station_allocate(client, shop, pn, demand, 10)
    done = _completed_at(db_engine, wo)
    assert done is not None
    body = _ok(
        _correct(admin, pn, demand, 2, reason="  customer accepted overage  "),
        201,
    )
    assert body["kind"] == "ALLOCATE_BEYOND_DEMAND"
    assert body["allocation_quantity"] == 2
    assert body["completed_work_order_ids"] == [] and body["reopened_work_order_ids"] == []
    [row] = body["rows"]
    assert row["exceeds_demand"] is True and row["is_manual_override"] is True
    assert row["source"] == "MANAGEMENT" and row["allocation_reason"] == _REASON
    assert row["actor_user_id"] == admin.user_id
    stored = _allocation(db_engine, int(row["allocation_id"]))
    assert stored["exceeds_demand"] is True
    assert stored["station_id"] is None and stored["allocated_by_worker_id"] is None
    assert stored["actor_user_id"] == admin.user_id and stored["actor_reference"] is None
    command_block = stored["metadata"]["command"]
    assert command_block["kind"] == "ALLOCATE_BEYOND_DEMAND"
    assert command_block["requested_quantity"] == 10
    assert command_block["allocated_before"] == 10
    assert _allocated_quantity(db_engine, demand) == 12
    assert _completed_at(db_engine, wo) == done
    assert _rebuilt_completed_at(db_engine)[wo] == done
    assert _ok(admin.get(f"/api/work-orders/{wo}"))["status"] == "COMPLETED"


def test_correction_on_an_open_work_order_keeps_it_open_until_its_last_line(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-2."""
    admin = admin_of(client)
    pn_a, pn_b = _unique("PN"), _unique("PN")
    wo, [line_a, line_b] = _work_order(admin, (pn_a, 10), (pn_b, 5))
    _supply(client, shop, pn_a, 11)
    _supply(client, shop, pn_b, 5)
    _allocate(admin, pn_a, line_a, 8)
    corrected = _ok(_correct(admin, pn_a, line_a, 3), 201)
    assert corrected["completed_work_order_ids"] == []
    assert _completed_at(db_engine, wo) is None
    filled = _allocate(admin, pn_b, line_b, 5)
    assert filled["completed_work_order_ids"] == [wo]
    assert _completed_at(db_engine, wo) is not None
    _assert_projections_match_replay(db_engine)


def test_correction_completes_the_work_order(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-3 (BC-12 c): a two-line Work Order completed by a correction."""
    admin = admin_of(client)
    pn_a, pn_b = _unique("PN"), _unique("PN")
    wo, [line_a, line_b] = _work_order(admin, (pn_a, 10), (pn_b, 5))
    _supply(client, shop, pn_a, 11)
    _supply(client, shop, pn_b, 5)
    _allocate(admin, pn_a, line_a, 8)
    _allocate(admin, pn_b, line_b, 5)
    corrected = _ok(_correct(admin, pn_a, line_a, 3), 201)
    assert corrected["completed_work_order_ids"] == [wo]
    allocated_at = datetime.datetime.fromisoformat(corrected["rows"][0]["allocated_at"])
    assert _completed_at(db_engine, wo) == allocated_at
    assert _rebuilt_completed_at(db_engine)[wo] == allocated_at


@pytest.mark.parametrize("quantity", [4, 2], ids=["equal", "below"])
def test_a_correction_within_the_remaining_demand_is_refused(
    client: TestClient, shop: _Shop, db_engine: Engine, quantity: int
) -> None:
    """BC-4 (C5-1)."""
    admin = admin_of(client)
    pn, wo, demand = _line(client, shop, 10, 12)
    _allocate(admin, pn, demand, 6)
    response = _refused_without_writes(
        db_engine, demand, lambda: _correct(admin, pn, demand, quantity)
    )
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": (
            f"Demand line {demand} (Work Order {wo}) still needs 4 pcs, so {quantity} pcs fit"
            " within its remaining demand. Use Allocate from stock instead — a beyond-demand"
            " correction must allocate more than the remaining demand. Nothing was allocated."
        )
    }


def test_a_correction_never_exceeds_the_available_stock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-5 (C5-2)."""
    admin = admin_of(client)
    pn, _, demand = _line(client, shop, 10, 13)
    _allocate(admin, pn, demand, 10)
    response = _refused_without_writes(db_engine, demand, lambda: _correct(admin, pn, demand, 4))
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": (
            f"Only 3 pcs of Part Number '{pn}' are available in stock (13 stocked, 10 already"
            " allocated); 4 pcs cannot be allocated. A correction never exceeds the available"
            " stocked quantity. Nothing was allocated."
        )
    }
    _ok(_correct(admin, pn, demand, 3), 201)
    assert _allocated_quantity(db_engine, demand) == 13


# ---------------------------------------------------------------------------
# BC-6 / BC-7 / BC-21 — refusals
# ---------------------------------------------------------------------------


def test_invalid_corrections_are_refused_without_writes(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-6."""
    admin = admin_of(client)
    pn, _, demand = _line(client, shop, 10, 12)
    _allocate(admin, pn, demand, 10)
    other_pn, _, other_demand = _line(client, shop, 5, 0)
    missing = int(_scalar(db_engine, "SELECT max(id) FROM work_order_demands")) + 1000
    typed: list[tuple[dict[str, Any], str]] = [
        (
            _correction_body(other_pn, demand, 2),
            f"Demand line {demand} is for Part Number '{pn}', not '{other_pn}'. Stocked quantity"
            " is allocated to its own PN's demand only. Nothing was allocated.",
        ),
        (_correction_body(pn, missing, 2), f"Demand line {missing} does not exist."),
        (_correction_body(pn, 2**31, 2), "Demand line 2147483648 does not exist."),
        (_correction_body(pn, 0, 2), "Demand line 0 does not exist."),
        (
            _correction_body(pn, demand, 0),
            "The correction quantity must be a positive whole number.",
        ),
        (
            _correction_body(pn, demand, -1),
            "The correction quantity must be a positive whole number.",
        ),
        (_correction_body(pn, demand, 2, reason=""), "The correction reason must not be empty."),
        (_correction_body(pn, demand, 2, reason="   "), "The correction reason must not be empty."),
        (
            _correction_body(pn, demand, 2, reason="over\x00age"),
            "The correction reason must not contain a NUL character.",
        ),
    ]
    for body, detail in typed:
        response = _refused_without_writes(
            db_engine,
            demand,
            functools.partial(admin.post, "/api/allocations/corrections", json=body),
        )
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": detail}
    invalid_pn = _refused_without_writes(
        db_engine,
        demand,
        lambda: _correct(admin, "BAD PN", demand, 2),
    )
    assert invalid_pn.status_code == 422, invalid_pn.text
    without_reason = _correction_body(pn, demand, 2)
    del without_reason["reason"]
    shapes: list[dict[str, Any]] = [
        _correction_body(pn, demand, True),
        _correction_body(pn, demand, "2"),
        without_reason,
        _correction_body(pn, demand, 2, station_id=shop.stockroom.station_id),
        _correction_body(pn, demand, 2, actor="someone"),
        _correction_body(pn, demand, 2, actor_user_id=admin.user_id),
        _correction_body(pn, demand, 2, exceeds_demand=True),
    ]
    for body in shapes:
        response = _refused_without_writes(
            db_engine,
            demand,
            functools.partial(admin.post, "/api/allocations/corrections", json=body),
        )
        assert response.status_code == 422, response.text
        assert isinstance(response.json()["detail"], list)


def test_corrections_need_edit_work_order_allocation(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-7."""
    pn, _, demand = _line(client, shop, 2, 5)
    body = _correction_body(pn, demand, 3)

    def send(caller: TestClient) -> Callable[[], Any]:
        return functools.partial(caller.post, "/api/allocations/corrections", json=body)

    anonymous = _refused_without_writes(db_engine, demand, send(anonymous_client(client)))
    assert anonymous.status_code == 401 and anonymous.json()["authentication_required"] is True
    others = client_as(client, *(set(ALL_PERMISSIONS) - {EWOA}))
    denied = _refused_without_writes(db_engine, demand, send(others))
    assert denied.status_code == 403, denied.text
    assert denied.json()["detail"] == _A2
    assert denied.json()["permission_denied"] is True
    assert denied.json()["required_permissions"] == ["EDIT_WORK_ORDER_ALLOCATION"]
    pending = client_as(client, EWOA, temporary_password=True)
    refused = _refused_without_writes(db_engine, demand, send(pending))
    assert refused.status_code == 403 and refused.json()["password_change_required"] is True
    holder = client_as(client, EWOA)
    assert holder.identity is not None
    no_csrf = TestClient(
        client.app,
        headers={"Cookie": f"{authentication.SESSION_COOKIE}={holder.identity.token}"},
    )
    rejected = _refused_without_writes(db_engine, demand, send(no_csrf))
    assert rejected.status_code == 403 and rejected.json()["csrf_rejected"] is True
    _ok(send(holder)(), 201)


def test_nul_in_any_allocation_reason_is_a_typed_refusal(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-21 (and BC-10's reversal reason): regression assertions only."""
    admin = admin_of(client)
    pn, _, demand = _line(client, shop, 10, 10)
    allocation = _allocation_body(pn, demand, 2, reason="re\x00count")
    response = _refused_without_writes(
        db_engine,
        demand,
        functools.partial(admin.post, "/api/allocations/management", json=allocation),
    )
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "The allocation reason must not contain a NUL character."}
    allocated = _allocate(admin, pn, demand, 2)
    reversal = _refused_without_writes(
        db_engine,
        demand,
        lambda: _reverse(admin, int(allocated["rows"][0]["allocation_id"]), "re\x00count"),
    )
    assert reversal.status_code == 422, reversal.text
    assert reversal.json() == {"detail": "The adjustment reason must not contain a NUL character."}


# ---------------------------------------------------------------------------
# BC-8 — idempotency
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


def _barrier_race(callers: list[TestClient], path: str, body: dict[str, Any]) -> list[Any]:
    barrier = threading.Barrier(len(callers))
    results: dict[str, Any] = {}

    def send(caller: TestClient) -> Callable[[], Any]:
        def call() -> Any:
            barrier.wait(timeout=10)
            return caller.post(path, json=body)

        return call

    threads = [_run(results, str(index), send(caller)) for index, caller in enumerate(callers)]
    for thread in threads:
        _finish(thread)
    return [results[str(index)] for index in range(len(callers))]


def _rows_of(engine: Engine, event_id: str) -> int:
    return int(
        _scalar(
            engine,
            "SELECT count(*) FROM work_order_allocations WHERE device_event_id = :e",
            e=event_id,
        )
    )


def test_correction_idempotency_and_actor_aware_replay(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-8 (sequential cases)."""
    editor = client_as(client, EWOA)
    pn, _, demand = _line(client, shop, 2, 10)
    body = _correction_body(pn, demand, 3)
    first = editor.post("/api/allocations/corrections", json=body)
    assert first.status_code == 201, first.text
    replay = editor.post("/api/allocations/corrections", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert replay.json()["kind"] == "ALLOCATE_BEYOND_DEMAND"
    assert _rows_of(db_engine, body["device_event_id"]) == 1
    second_session = another_session(client, editor)
    assert second_session.post("/api/allocations/corrections", json=body).json() == first.json()
    other_quantity = _refused_without_writes(
        db_engine,
        demand,
        lambda: editor.post("/api/allocations/corrections", json={**body, "quantity": 4}),
    )
    assert other_quantity.status_code == 409 and other_quantity.json() == {"detail": _REUSED}
    other_user = _refused_without_writes(
        db_engine,
        demand,
        lambda: client_as(client, EWOA).post("/api/allocations/corrections", json=body),
    )
    assert other_user.status_code == 409, other_user.text
    assert other_user.json() == {"detail": _R1, "recorded_by_another_user": True}
    # A device_event_id already used by a Management allocation.
    used = _event()
    other_pn, _, other_demand = _line(client, shop, 5, 5)
    _ok(
        editor.post(
            "/api/allocations/management",
            json=_allocation_body(other_pn, other_demand, 1, device_event_id=used),
        ),
        201,
    )
    reused = _refused_without_writes(
        db_engine, demand, lambda: _correct(editor, pn, demand, 3, device_event_id=used)
    )
    assert reused.status_code == 409 and reused.json() == {"detail": _REUSED}


def test_concurrent_identical_corrections_record_once(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-8 barriers: (a) one identity → 201 + identical 200; (b) two
    identities → 201 + 409 recorded_by_another_user; one row each."""
    editor = client_as(client, EWOA)
    pn, _, demand = _line(client, shop, 2, 10)
    body = _correction_body(pn, demand, 3)
    same = _barrier_race(
        [editor, another_session(client, editor)], "/api/allocations/corrections", body
    )
    assert sorted(response.status_code for response in same) == [200, 201], [
        response.text for response in same
    ]
    assert same[0].json() == same[1].json()
    assert _rows_of(db_engine, body["device_event_id"]) == 1

    pn, _, demand = _line(client, shop, 2, 10)
    body = _correction_body(pn, demand, 3)
    different = _barrier_race(
        [client_as(client, EWOA), client_as(client, EWOA)], "/api/allocations/corrections", body
    )
    assert sorted(response.status_code for response in different) == [201, 409], [
        response.text for response in different
    ]
    loser = next(response for response in different if response.status_code == 409)
    assert loser.json() == {"detail": _R1, "recorded_by_another_user": True}
    assert _rows_of(db_engine, body["device_event_id"]) == 1


# ---------------------------------------------------------------------------
# BC-9 … BC-11 — Hot list, reversal, routine paths
# ---------------------------------------------------------------------------


def _hot_audit_rows(engine: Engine, since: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT * FROM audit_events WHERE id > :since AND entity_type ="
                    " 'WorkOrderDemand' AND metadata ? 'hot_list_change' ORDER BY id"
                ),
                {"since": since},
            ).mappings()
        ]


def _max_audit_id(engine: Engine) -> int:
    return int(_scalar(engine, "SELECT coalesce(max(id), 0) FROM audit_events"))


@pytest.mark.parametrize("completes", [False, True], ids=["fully-allocated", "completed"])
def test_a_correction_removes_its_ranked_line_from_the_hot_list(
    client: TestClient, shop: _Shop, db_engine: Engine, completes: bool
) -> None:
    """BC-9."""
    admin = admin_of(client)
    pn = _unique("PN")
    lines = [(pn, 10)] if completes else [(pn, 10), (_unique("PN"), 5)]
    wo, demands = _work_order(admin, *lines)
    target = demands[0]
    _supply(client, shop, pn, 12)
    _hot_add(client, target)
    trailing = _work_order(admin, (_unique("PN"), 3))[1][0]
    _hot_add(client, trailing)
    before = _hot_order(client)
    since = _max_audit_id(db_engine)
    body = _correction_body(pn, target, 12)
    _ok(admin.post("/api/allocations/corrections", json=body), 201)
    after = _hot_order(client)
    assert after == [demand for demand in before if demand != target]
    ranks = [entry["rank"] for entry in _ok(admin.get("/api/hot-list"))["entries"]]
    assert ranks == list(range(1, len(after) + 1))
    rows = _hot_audit_rows(db_engine, since)
    assert rows
    reason = "WORK_ORDER_COMPLETED" if completes else "FULLY_ALLOCATED"
    for row in rows:
        assert row["actor_user_id"] == admin.user_id
        cause = row["metadata"]["hot_list_change"]["cause"]
        assert cause["trigger"] == "ALLOCATION"
        assert cause["removed"] == [{"work_order_demand_id": target, "reason": reason}]
        assert cause["reference"]["source"] == "MANAGEMENT"
    assert (_completed_at(db_engine, wo) is not None) is completes
    since = _max_audit_id(db_engine)
    assert admin.post("/api/allocations/corrections", json=body).status_code == 200
    assert _hot_audit_rows(db_engine, since) == []


def test_reversing_a_correction_and_the_routine_allocation(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-10."""
    admin = admin_of(client)
    pn, wo, demand = _line(client, shop, 10, 12)
    routine = _allocate(admin, pn, demand, 10)
    correction = _ok(_correct(admin, pn, demand, 2), 201)
    correction_id = int(correction["rows"][0]["allocation_id"])
    reversal = _ok(_reverse(admin, correction_id), 201)
    [row] = reversal["rows"]
    assert row["exceeds_demand"] is False and row["source"] == "MANAGEMENT"
    assert row["actor_user_id"] == admin.user_id
    assert reversal["reopened_work_order_ids"] == []
    assert _allocated_quantity(db_engine, demand) == 10
    assert _completed_at(db_engine, wo) is not None
    again = _refused_without_writes(db_engine, demand, lambda: _reverse(admin, correction_id))
    assert again.status_code == 409, again.text

    # Reversing the routine allocation while a correction stays active.
    second = _ok(_correct(admin, pn, demand, 2), 201)
    reopened = _ok(_reverse(admin, int(routine["rows"][0]["allocation_id"])), 201)
    assert reopened["reopened_work_order_ids"] == [wo]
    assert _completed_at(db_engine, wo) is None
    assert _allocation(db_engine, int(second["rows"][0]["allocation_id"]))["exceeds_demand"] is True
    _assert_projections_match_replay(db_engine)


def test_routine_allocation_still_never_exceeds_the_remaining_demand(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-11."""
    admin = admin_of(client)
    pn, _, demand = _line(client, shop, 10, 20)
    station = client.post(
        "/api/allocations",
        json=_allocation_body(pn, demand, 11, station_id=shop.stockroom.station_id),
    )
    assert station.status_code == 409, station.text
    assert "Allocation never exceeds the requested quantity" in station.json()["detail"]
    management = admin.post("/api/allocations/management", json=_allocation_body(pn, demand, 11))
    assert management.status_code == 409, management.text
    assert "Allocation never exceeds the requested quantity" in management.json()["detail"]
    _allocate(admin, pn, demand, 10)
    _ok(_correct(admin, pn, demand, 2), 201)
    for caller, extra in ((client, {"station_id": shop.stockroom.station_id}), (admin, {})):
        path = "/api/allocations" if extra else "/api/allocations/management"
        response = _refused_without_writes(
            db_engine,
            demand,
            functools.partial(caller.post, path, json=_allocation_body(pn, demand, 1, **extra)),
        )
        assert response.status_code == 409, response.text
        assert "can take 0 pcs more (12 of 10 pcs already allocated)" in response.json()["detail"]
    suggestion = _ok(client.get("/api/allocations/suggestion", params={"part_number": pn}))
    assert demand not in {line["work_order_demand_id"] for line in suggestion["lines"]}
    # The line's single-line Work Order is complete: the demand-line scope.
    [line] = _ok(_context(admin, work_order_demand_id=demand))["lines"]
    assert (line["remaining_shortage"], line["beyond_demand_quantity"]) == (0, 2)


# ---------------------------------------------------------------------------
# BC-12 — the done-date replay
# ---------------------------------------------------------------------------


def _stamp(result: dict[str, Any]) -> datetime.datetime:
    return datetime.datetime.fromisoformat(result["rows"][0]["allocated_at"])


def test_done_date_replay_station_then_correction(
    client: TestClient, shop: _Shop, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """BC-12 (a)."""
    admin = admin_of(client)
    pn, wo, demand = _line(client, shop, 10, 12)
    station = _station_allocate(client, shop, pn, demand, 10)
    t1, routine_id = _stamp(station), int(station["rows"][0]["allocation_id"])
    _assert_done_date(db_engine, capsys, wo, t1)
    correction = _ok(_correct(admin, pn, demand, 2), 201)
    _assert_done_date(db_engine, capsys, wo, t1)
    _ok(_reverse(admin, int(correction["rows"][0]["allocation_id"])), 201)
    _assert_done_date(db_engine, capsys, wo, t1)
    _ok(_reverse(admin, routine_id), 201)
    _assert_done_date(db_engine, capsys, wo, None)


def test_done_date_replay_survives_a_reversal_that_leaves_it_complete(
    client: TestClient, shop: _Shop, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """BC-12 (b): the pre-slice-5 newest-row rule would answer the correction's time."""
    admin = admin_of(client)
    pn, wo, demand = _line(client, shop, 10, 15)
    six = _allocate(admin, pn, demand, 6)
    _assert_done_date(db_engine, capsys, wo, None)
    four = _allocate(admin, pn, demand, 4)
    t2 = _stamp(four)
    _assert_done_date(db_engine, capsys, wo, t2)
    _ok(_correct(admin, pn, demand, 5), 201)
    _assert_done_date(db_engine, capsys, wo, t2)
    _ok(_reverse(admin, int(four["rows"][0]["allocation_id"])), 201)
    _assert_done_date(db_engine, capsys, wo, t2)
    _ok(_reverse(admin, int(six["rows"][0]["allocation_id"])), 201)
    _assert_done_date(db_engine, capsys, wo, None)
    again = _allocate(admin, pn, demand, 5)
    _assert_done_date(db_engine, capsys, wo, _stamp(again))


def test_done_date_replay_keeps_a_demand_change_completion(
    client: TestClient, shop: _Shop, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """BC-12 (d): a Save lowering the last short line completes the Work
    Order (9a7db92, audited); later corrections and reversals replay."""
    admin = admin_of(client)
    pn_a, pn_b = _unique("PN"), _unique("PN")
    wo, [line_a, line_b] = _work_order(admin, (pn_a, 10), (pn_b, 10))
    _supply(client, shop, pn_a, 12)
    _supply(client, shop, pn_b, 6)
    _allocate(admin, pn_a, line_a, 10)
    b_six = _allocate(admin, pn_b, line_b, 6)
    saved = _ok(
        admin.patch(
            f"/api/work-orders/{wo}",
            json={"line_edits": [{"id": line_b, "requested_quantity": 6}]},
        )
    )
    assert saved["status"] == "COMPLETED"
    t_s = _completed_at(db_engine, wo)
    assert t_s is not None and t_s > _stamp(b_six)
    _assert_done_date(db_engine, capsys, wo, t_s)
    correction = _ok(_correct(admin, pn_a, line_a, 2), 201)
    _assert_done_date(db_engine, capsys, wo, t_s)
    _ok(_reverse(admin, int(correction["rows"][0]["allocation_id"])), 201)
    _assert_done_date(db_engine, capsys, wo, t_s)
    _ok(_reverse(admin, int(b_six["rows"][0]["allocation_id"])), 201)
    _assert_done_date(db_engine, capsys, wo, None)
    again = _allocate(admin, pn_b, line_b, 6)
    _assert_done_date(db_engine, capsys, wo, _stamp(again))


# ---------------------------------------------------------------------------
# BC-13 — the demand-edit floor
# ---------------------------------------------------------------------------


def test_an_over_allocated_line_stays_editable_except_below_its_allocation(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-13."""
    admin = admin_of(client)
    pn, other_pn = _unique("PN"), _unique("PN")
    wo, [demand, _] = _work_order(admin, (pn, 10), (other_pn, 5))
    _supply(client, shop, pn, 12)
    _allocate(admin, pn, demand, 10)
    _ok(_correct(admin, pn, demand, 2), 201)

    def save(**edit: Any) -> Any:
        return admin.patch(f"/api/work-orders/{wo}", json={"line_edits": [{"id": demand, **edit}]})

    _ok(save(due_date="2030-01-15"))
    _ok(save(requested_quantity=10))
    for quantity, verb in ((11, "set"), (9, "lower")):
        response = _refused_without_writes(
            db_engine, demand, functools.partial(save, requested_quantity=quantity)
        )
        assert response.status_code == 409, response.text
        assert response.json() == {
            "detail": (
                f"Cannot {verb} Qty to {quantity} pcs for Part Number '{pn}': 12 pcs are"
                " already allocated. Enter 12 pcs or more."
            )
        }
    raised = _ok(save(requested_quantity=12))
    [line] = [line for line in raised["demands"] if line["id"] == demand]
    assert line["requested_quantity"] == 12


# ---------------------------------------------------------------------------
# BC-15 … BC-20 — concurrency and lock order
# ---------------------------------------------------------------------------


def test_two_corrections_never_jointly_exceed_the_stock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-15."""
    admin = admin_of(client)
    pn = _unique("PN")
    _, [line_a] = _work_order(admin, (pn, 5))
    _, [line_b] = _work_order(admin, (pn, 5))
    _supply(client, shop, pn, 12)
    _allocate(admin, pn, line_a, 5)
    _allocate(admin, pn, line_b, 5)
    first, second = client_as(client, EWOA), client_as(client, EWOA)
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def send(caller: TestClient, demand: int) -> Callable[[], Any]:
        def call() -> Any:
            barrier.wait(timeout=10)
            return _correct(caller, pn, demand, 2)

        return call

    threads = [
        _run(results, "a", send(first, line_a)),
        _run(results, "b", send(second, line_b)),
    ]
    for thread in threads:
        _finish(thread)
    statuses = sorted(response.status_code for response in results.values())
    assert statuses == [201, 409], [response.text for response in results.values()]
    loser = next(response for response in results.values() if response.status_code == 409)
    assert "A correction never exceeds the available stocked quantity" in loser.json()["detail"]
    with Session(db_engine) as session:
        position = allocations.stock_position_of(session, pn)
    assert position.active_allocated_quantity <= position.stocked_quantity


def test_a_correction_and_a_station_allocation_race_for_the_last_stock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-16: both queue on the paused PN lock; the later one is refused."""
    admin = admin_of(client)
    pn = _unique("PN")
    _, [full] = _work_order(admin, (pn, 2))
    _, [short] = _work_order(admin, (pn, 5))
    _supply(client, shop, pn, 3)
    _allocate(admin, pn, full, 2)
    before = _counts(db_engine)["work_order_allocations"]
    results: dict[str, Any] = {}
    with db_engine.connect() as holder:
        transaction = holder.begin()
        _advisory(holder, f"partflow:part-number:{pn}")
        correction = _run(results, "correction", lambda: _correct(admin, pn, full, 1))
        _assert_blocked(correction)
        station = _run(
            results,
            "station",
            lambda: client.post(
                "/api/allocations",
                json=_allocation_body(pn, short, 1, station_id=shop.stockroom.station_id),
            ),
        )
        _assert_blocked(station)
        transaction.rollback()
    _finish(correction)
    _finish(station)
    statuses = sorted(response.status_code for response in results.values())
    assert statuses == [201, 409], [response.text for response in results.values()]
    assert _counts(db_engine)["work_order_allocations"] == before + 1
    _assert_projections_match_replay(db_engine)


def test_a_correction_and_a_quantity_lowering_save_stay_consistent(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-17: whichever runs first, no unflagged row ends beyond demand and
    the save is refused by the floor when it runs second."""
    admin = admin_of(client)
    for first in ("correction", "save"):
        pn, other_pn = _unique("PN"), _unique("PN")
        wo, [demand, _] = _work_order(admin, (pn, 10), (other_pn, 5))
        _supply(client, shop, pn, 12)
        _allocate(admin, pn, demand, 6)
        calls: dict[str, Callable[[], Any]] = {
            "correction": functools.partial(_correct, admin, pn, demand, 5),
            "save": functools.partial(
                admin.patch,
                f"/api/work-orders/{wo}",
                json={"line_edits": [{"id": demand, "requested_quantity": 8}]},
            ),
        }
        second = "save" if first == "correction" else "correction"
        results: dict[str, Any] = {}
        with db_engine.connect() as holder:
            transaction = holder.begin()
            _advisory(holder, f"partflow:part-number:{pn}")
            leading = _run(results, first, calls[first])
            _assert_blocked(leading)
            trailing = _run(results, second, calls[second])
            _assert_blocked(trailing)
            transaction.rollback()
        _finish(leading)
        _finish(trailing)
        assert results["correction"].status_code == 201, results["correction"].text
        save = results["save"]
        requested = int(
            _scalar(
                db_engine,
                "SELECT requested_quantity FROM work_order_demands WHERE id = :id",
                id=demand,
            )
        )
        if save.status_code == 200:
            assert requested == 8
        else:
            assert save.status_code == 409, save.text
            assert save.json()["detail"].startswith(
                f"Cannot lower Qty to 8 pcs for Part Number '{pn}'"
            )
            assert requested == 10
        unflagged = int(
            _scalar(
                db_engine,
                "SELECT coalesce(sum(a.quantity), 0) FROM work_order_allocations a"
                " WHERE a.work_order_demand_id = :id AND NOT a.exceeds_demand"
                " AND a.reverses_allocation_id IS NULL AND NOT EXISTS (SELECT 1 FROM"
                " work_order_allocations r WHERE r.reverses_allocation_id = a.id)",
                id=demand,
            )
        )
        assert unflagged <= requested
        _assert_projections_match_replay(db_engine)


def test_authorization_refusals_never_wait_on_a_production_lock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """BC-18 (and CX-3's lock-free read)."""
    pn, _, demand = _line(client, shop, 2, 5)
    callers = (anonymous_client(client), client_as(client, VPD))
    reader = client_as(client, EWOA)
    for key in (f"partflow:part-number:{pn}", "partflow:hot-list"):
        before = _counts(db_engine)
        with db_engine.connect() as holder:
            transaction = holder.begin()
            _advisory(holder, key)
            results: dict[str, Any] = {}
            threads = [
                _run(results, str(index), functools.partial(_correct, caller, pn, demand, 3))
                for index, caller in enumerate(callers)
            ]
            threads.append(
                _run(results, "context", functools.partial(_context, reader, part_number=pn))
            )
            for thread in threads:
                thread.join(timeout=2)
                assert not thread.is_alive(), key
            transaction.rollback()
        assert results["0"].status_code == 401
        assert results["1"].status_code == 403
        assert results["context"].status_code == 200
        assert _counts(db_engine) == before


_LOCK_ORDER: dict[str, int] = {"A1": 1, "A2": 2, "R1": 3, "R2": 4, "R3": 5}
_FOR_UPDATE = re.compile(r"\bFOR (UPDATE|KEY SHARE)\b")


@contextlib.contextmanager
def _recording() -> Iterator[list[tuple[str, int | None]]]:
    """Every advisory lock and row lock the API takes, in execution order
    (the ``test_hot_list_api`` recorder)."""
    locks: list[tuple[str, int | None]] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        text = " ".join(statement.split())
        values = list(parameters.values()) if isinstance(parameters, dict) else []
        if "pg_advisory_xact_lock" in text:
            for value in values:
                if isinstance(value, str) and value.startswith("partflow:part-number:"):
                    locks.append(("A1", None))
                elif value == "partflow:hot-list":
                    locks.append(("A2", None))
            return
        if not _FOR_UPDATE.search(text):
            return
        ids = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
        if re.search(r"\bFROM scan_stations\b", text):
            locks.append(("R1", None))
        elif re.search(r"\bFROM work_order_demands\b", text):
            locks.append(("R2", ids[0] if ids else None))
        elif re.search(r"\bFROM work_orders\b", text):
            locks.append(("R3", None))

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        yield locks
    finally:
        event.remove(Engine, "before_cursor_execute", listener)


def test_the_correction_follows_the_global_lock_order(client: TestClient, shop: _Shop) -> None:
    """BC-19: PN → Hot → ONE ascending demand pass (its line and the Hot
    shift scope) → Work Order; never a Scan Station row."""
    admin = admin_of(client)
    pn = _unique("PN")
    _, [target] = _work_order(admin, (pn, 2))
    _, [trailing] = _work_order(admin, (_unique("PN"), 2))
    _supply(client, shop, pn, 5)
    _hot_add(client, target)
    _hot_add(client, trailing)
    with _recording() as locks:
        response = _correct(admin, pn, target, 3)
    assert response.status_code == 201, response.text
    classes = [kind for kind, _ in locks]
    ranks = [_LOCK_ORDER[kind] for kind in classes]
    assert ranks == sorted(ranks), classes
    assert classes[:2] == ["A1", "A2"] and classes[-1] == "R3", classes
    assert classes.count("A1") == 1 and classes.count("A2") == 1, classes
    assert "R1" not in classes
    demand_ids = [demand_id for kind, demand_id in locks if kind == "R2" and demand_id is not None]
    assert len(demand_ids) == classes.count("R2")
    assert demand_ids == sorted(set(demand_ids))
    assert {target, trailing} <= set(demand_ids)


def _row_lock(connection: sa.Connection, table: str, row_id: int) -> None:
    connection.execute(sa.text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id})


@pytest.mark.parametrize("first", ["correction", "other"])
def test_takers_without_advisory_locks_never_deadlock_with_a_correction(
    client: TestClient, shop: _Shop, db_engine: Engine, first: str
) -> None:
    """BC-20: (a) an unconfirmed delete of a Hot-ranked shift-scope line and
    (b) a header-only save of the correction's own Work Order, each in both
    arrival orders (a raw connection holds the row they meet on)."""
    admin = admin_of(client)
    pn = _unique("PN")
    # Two lines: the correction never completes the Work Order it saves.
    target_wo, [target, _] = _work_order(admin, (pn, 2), (_unique("PN"), 2))
    ranked_wo, [ranked, _] = _work_order(admin, (_unique("PN"), 2), (_unique("PN"), 2))
    _supply(client, shop, pn, 10)
    _hot_add(client, target)
    _hot_add(client, ranked)
    scenarios: list[tuple[str, int, Callable[[], Any]]] = [
        (
            "work_order_demands",
            ranked,
            lambda: admin.delete(f"/api/work-orders/{ranked_wo}/demands/{ranked}"),
        ),
        (
            "work_orders",
            target_wo,
            lambda: admin.patch(
                f"/api/work-orders/{target_wo}", json={"work_order_number": _unique("WO")}
            ),
        ),
    ]
    # The first correction must exceed the line's demand (2); the line is
    # then fully allocated, so the second needs only 1.
    for (table, row_id, other), quantity in zip(scenarios, (3, 1), strict=True):
        calls: dict[str, Callable[[], Any]] = {
            "correction": functools.partial(_correct, admin, pn, target, quantity),
            "other": other,
        }
        second = "other" if first == "correction" else "correction"
        results: dict[str, Any] = {}
        with db_engine.connect() as holder:
            transaction = holder.begin()
            _row_lock(holder, table, row_id)
            leading = _run(results, first, calls[first])
            _assert_blocked(leading)
            trailing = _run(results, second, calls[second])
            _assert_blocked(trailing)
            transaction.rollback()
        _finish(leading)
        _finish(trailing)
        assert results["correction"].status_code == 201, results["correction"].text
        response = results["other"]
        if table == "work_order_demands":
            assert response.status_code == 409, response.text
            assert response.json()["confirmation_required"] is True
        else:
            assert response.status_code == 200, response.text
        _assert_projections_match_replay(db_engine)


# ---------------------------------------------------------------------------
# CX-1 … CX-3 — the Management context read
# ---------------------------------------------------------------------------


def test_context_of_a_part_number(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """CX-1."""
    admin = admin_of(client)
    editor = client_as(client, EWOA)
    pn = _unique("PN")
    # Two lines: fully allocating the first leaves its Work Order open.
    first_wo, [first, _] = _work_order(admin, (pn, 4), (_unique("PN"), 1))
    _, [over, _] = _work_order(admin, (pn, 3), (_unique("PN"), 1))
    done_wo, [done] = _work_order(admin, (pn, 2))
    supply = _supply(client, shop, pn, 20)
    station = _station_allocate(client, shop, pn, first, 4)
    _allocate(admin, pn, done, 2)
    routine = _allocate(editor, pn, over, 3)
    correction = _ok(_correct(editor, pn, over, 2), 201)
    extra = _ok(_correct(editor, pn, over, 1), 201)
    _ok(_reverse(admin, int(extra["rows"][0]["allocation_id"])), 201)
    assert _completed_at(db_engine, done_wo) is not None
    body = _ok(_context(editor, part_number=pn.lower()))
    with Session(db_engine) as session:
        position = allocations.stock_position_of(session, pn)
    assert body["part_number"] == pn
    assert (
        body["stocked_quantity"],
        body["active_allocated_quantity"],
        body["available_stocked_quantity"],
    ) == (
        position.stocked_quantity,
        position.active_allocated_quantity,
        position.available_stocked_quantity,
    )
    assert [line["work_order_demand_id"] for line in body["lines"]] == [first, over, supply]
    by_id = {line["work_order_demand_id"]: line for line in body["lines"]}
    assert by_id[first]["work_order_id"] == first_wo
    assert by_id[first]["work_order_completed"] is False
    assert (by_id[first]["remaining_shortage"], by_id[first]["beyond_demand_quantity"]) == (0, 0)
    [station_entry] = by_id[first]["active_allocations"]
    assert station_entry["allocation_id"] == station["rows"][0]["allocation_id"]
    assert station_entry["source"] == "STOCKROOM" and station_entry["actor_user"] is None
    over_line = by_id[over]
    assert over_line["request_type"] == "NEW"
    assert (over_line["requested_quantity"], over_line["allocated_quantity"]) == (3, 5)
    assert (over_line["remaining_shortage"], over_line["beyond_demand_quantity"]) == (0, 2)
    assert [entry["allocation_id"] for entry in over_line["active_allocations"]] == [
        routine["rows"][0]["allocation_id"],
        correction["rows"][0]["allocation_id"],
    ]
    beyond = over_line["active_allocations"][1]
    assert beyond["exceeds_demand"] is True and beyond["allocation_reason"] == _REASON
    assert beyond["actor_user"]["id"] == editor.user_id
    assert set(beyond["actor_user"]) == {"id", "display_name", "avatar_updated_at"}
    assert by_id[supply]["remaining_shortage"] == 20


def test_context_of_one_demand_line(client: TestClient, shop: _Shop) -> None:
    """CX-2."""
    admin = admin_of(client)
    editor = client_as(client, EWOA)
    pn, wo, demand = _line(client, shop, 4, 4)
    _allocate(admin, pn, demand, 4)
    body = _ok(_context(editor, work_order_demand_id=demand))
    assert body["part_number"] == pn
    [line] = body["lines"]
    assert line["work_order_demand_id"] == demand and line["work_order_id"] == wo
    assert line["work_order_completed"] is True
    for missing in (
        int(demand) + 100_000,
        2**31,
    ):
        response = _context(editor, work_order_demand_id=missing)
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": f"Demand line {missing} does not exist."}
    for query in ({}, {"part_number": pn, "work_order_demand_id": demand}):
        response = _context(editor, **query)
        assert response.status_code == 422, response.text
        assert response.json() == {
            "detail": "Give exactly one of part_number or work_order_demand_id."
        }
    assert _context(editor, part_number="BAD PN").status_code == 422
    empty = _ok(_context(editor, part_number=_unique("PN")))
    assert empty["lines"] == [] and empty["stocked_quantity"] == 0


def test_context_needs_edit_work_order_allocation(client: TestClient, shop: _Shop) -> None:
    """CX-3 (the lock-free read is BC-18)."""
    pn, _, _ = _line(client, shop, 2, 0)
    anonymous = _context(anonymous_client(client), part_number=pn)
    assert anonymous.status_code == 401
    viewer = _context(client_as(client, VPD), part_number=pn)
    assert viewer.status_code == 403
    assert viewer.json()["required_permissions"] == ["EDIT_WORK_ORDER_ALLOCATION"]
    assert _context(client_as(client, EWOA), part_number=pn).status_code == 200


# ---------------------------------------------------------------------------
# TR-1 — Tracking and the allocation responses
# ---------------------------------------------------------------------------


def test_tracking_and_allocation_reads_carry_the_flag_and_the_user(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """TR-1."""
    admin = admin_of(client)
    editor = client_as(client, EWOA)
    pn, _, demand = _line(client, shop, 4, 8)
    station = _station_allocate(client, shop, pn, demand, 2)
    management = _allocate(editor, pn, demand, 2)
    assert management["rows"][0]["exceeds_demand"] is False
    correction = _ok(_correct(editor, pn, demand, 3), 201)
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE users SET is_active = false WHERE id = :id"), {"id": editor.user_id}
        )
    expected = {
        station["rows"][0]["allocation_id"]: (False, None),
        management["rows"][0]["allocation_id"]: (False, editor.user_id),
        correction["rows"][0]["allocation_id"]: (True, editor.user_id),
    }
    detail = _ok(admin.get("/api/tracking/detail", params={"part_number": pn}))
    page = _ok(admin.get("/api/tracking/allocations", params={"part_number": pn}))
    for entries in (detail["allocations"]["allocations"], page["allocations"]):
        observed = {
            entry["id"]: (
                entry["exceeds_demand"],
                entry["actor_user"]["id"] if entry["actor_user"] else None,
            )
            for entry in entries
        }
        assert observed == expected
        for entry in entries:
            if entry["actor_user"] is not None:
                assert set(entry["actor_user"]) == {"id", "display_name", "avatar_updated_at"}
    records = _ok(admin.get("/api/allocations", params={"part_number": pn}))
    assert {record["id"]: record["exceeds_demand"] for record in records} == {
        allocation_id: flag for allocation_id, (flag, _) in expected.items()
    }
