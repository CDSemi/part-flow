"""PN Tracking read model (Phase 11 — PROJECT_PROFILE §21 Tracking, GUI_DESIGN §7).

The management list and the per-PN detail, judged on real production
history written through the public commands: the derived status and
the universe of tracked PNs, the server-side search and filters, the
canonical order with offset paging, and the detail's demand figures,
current quantity by Area / Machine, stocked and allocation history,
the §11 reconciliation, the Quantity Flows with their lineage, the
PLANNED snapshot with a confirmed deviation, the FLOATING trace with a
repeated Area, a Repair and an inherited split prefix, and the paged
immutable Movement history in which a reversed original stays visible
beside its `REVERSED` row.
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
from app.application.work_orders import site_today
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_tracking_api"
_DB_URL_ENV = "DATABASE_URL"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
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


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    response = client.put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "TK-", "digits": 4},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class _Cell:
    """An Area with an Operation, a station and optional Machines."""

    def __init__(
        self,
        client: TestClient,
        department_id: int,
        *,
        name: str,
        machine_count: int = 0,
        is_terminal: bool = False,
    ) -> None:
        area = client.post(
            "/api/areas",
            json={
                "department_id": department_id,
                "name": name,
                "is_terminal": is_terminal,
                "color": "var(--a-lathe)",
            },
        )
        assert area.status_code == 201, area.text
        self.area_id = int(area.json()["id"])
        self.name = name
        created = client.post(
            "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
        )
        assert created.status_code == 201, created.text
        self.operation_id = int(created.json()["id"])
        station = client.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
        )
        assert station.status_code == 201, station.text
        self.station_id = str(station.json()["station_id"])
        self.machine_ids: list[int] = []
        for index in range(machine_count):
            machine = client.post(
                "/api/machines", json={"area_id": self.area_id, "name": f"{name} M{index + 1}"}
            )
            assert machine.status_code == 201, machine.text
            self.machine_ids.append(int(machine.json()["id"]))

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


class _Shop:
    def __init__(self, client: TestClient) -> None:
        response = client.post("/api/departments", json={"name": _unique("DEPT")})
        assert response.status_code == 201, response.text
        self.department_id = int(response.json()["id"])
        suffix = uuid.uuid4().hex[:6].upper()
        self.material = _Cell(client, self.department_id, name=f"Material {suffix}")
        self.cut = _Cell(client, self.department_id, name=f"Cut {suffix}")
        self.lathe = _Cell(client, self.department_id, name=f"Lathe {suffix}", machine_count=2)
        self.stockroom = _Cell(
            client, self.department_id, name=f"Stockroom {suffix}", is_terminal=True
        )


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    return _Shop(client)


class _WorkOrder:
    def __init__(self, body: dict[str, Any]) -> None:
        self.id = int(body["id"])
        self.number = body["work_order_number"]
        self.demand_ids = [int(line["id"]) for line in body["demands"]]

    @property
    def demand_id(self) -> int:
        return self.demand_ids[0]


def _work_order(
    client: TestClient,
    lines: list[dict[str, Any]],
    *,
    number: str | None = None,
    received_date: str | None = None,
) -> _WorkOrder:
    payload: dict[str, Any] = {"lines": lines}
    if number is not None:
        payload["work_order_number"] = number
    if received_date is not None:
        payload["received_date"] = received_date
    response = client.post("/api/work-orders", json=payload)
    assert response.status_code == 201, response.text
    return _WorkOrder(response.json())


def _line(pn: str, quantity: int, **extra: Any) -> dict[str, Any]:
    line: dict[str, Any] = {"part_number": pn, "requested_quantity": quantity}
    line.update(extra)
    return line


def _set_priority(engine: Engine, demand_id: int, rank: int | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.update(models.WorkOrderDemand)
            .where(models.WorkOrderDemand.id == demand_id)
            .values(priority_rank=rank)
        )


def _release(
    client: TestClient,
    cell: _Cell,
    work_order: _WorkOrder,
    pn: str,
    *,
    demand_id: int | None = None,
    quantity: int = 10,
    route_template_id: int | None = None,
    confirm_active_quantity: bool = False,
) -> int:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "route_mode": "PLANNED" if route_template_id is not None else "FLOATING",
        "starting_area_id": cell.area_id,
        "operation_id": cell.operation_id,
        "confirm_active_quantity": confirm_active_quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    if route_template_id is not None:
        payload["route_template_id"] = route_template_id
    released = client.post(
        f"/api/work-orders/{work_order.id}/demands/{demand_id or work_order.demand_id}/release",
        json=payload,
    )
    assert released.status_code == 201, released.text
    return int(released.json()["quantity_flow_id"])


def _route_template(engine: Engine, cells: list[_Cell]) -> int:
    with Session(engine) as session:
        template = models.RouteTemplate(name=_unique("ROUTE"))
        session.add(template)
        session.flush()
        for index, cell in enumerate(cells):
            session.add(
                models.RouteStep(
                    route_template_id=template.id,
                    sequence=(index + 1) * 10,
                    area_id=cell.area_id,
                    operation_id=cell.operation_id,
                )
            )
        session.commit()
        return int(template.id)


def _arrival(
    client: TestClient,
    kind: str,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: int,
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(extra)
    response = client.post(f"/api/scan-stations/{target.station_id}/{kind}", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _transfer(
    client: TestClient, source: _Cell, target: _Cell, flow_id: int, pn: str, qty: int, **kw: Any
) -> dict[str, Any]:
    return _arrival(client, "transfers", source, target, flow_id, pn, qty, **kw)


def _stock(
    client: TestClient, source: _Cell, stockroom: _Cell, flow_id: int, pn: str, qty: int
) -> dict[str, Any]:
    return _arrival(client, "stockings", source, stockroom, flow_id, pn, qty)


def _machine_action(
    client: TestClient, action: str, cell: _Cell, flow_id: int, pn: str, quantity: int, **kw: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    response = client.post(f"/api/scan-stations/{cell.station_id}/{action}", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _merge(client: TestClient, cell: _Cell, pn: str, flow_ids: list[int]) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/merges",
        json={
            "part_number": pn,
            "quantity_flow_ids": flow_ids,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _scrap(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/scraps",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "quantity": quantity,
            "reason": "damaged",
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _undo(client: TestClient, cell: _Cell, pn: str, reverses: str) -> None:
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/undos",
        json={
            "part_number": pn,
            "reverses_device_event_id": reverses,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text


def _allocate(client: TestClient, pn: str, lines: list[tuple[int, int]]) -> int:
    response = client.post(
        "/api/allocations",
        json={
            "part_number": pn,
            "allocation_quantity": sum(qty for _, qty in lines),
            "lines": [
                {"work_order_demand_id": demand_id, "quantity": qty} for demand_id, qty in lines
            ],
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["rows"][0]["allocation_id"])


def _delete_master(engine: Engine, pn: str) -> None:
    """The master is hard-deletable (PROJECT_PROFILE §8.1); full
    management arrives with Phase 13, so the seed removes it directly."""
    with engine.begin() as connection:
        connection.execute(sa.delete(models.PartNumber).where(models.PartNumber.part_number == pn))


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------


def _rows(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    params.setdefault("status", "ALL")
    response = client.get("/api/tracking", params=params)
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json()["rows"])


def _row(client: TestClient, pn: str, **params: Any) -> dict[str, Any]:
    params.setdefault("search", pn)
    found = [row for row in _rows(client, **params) if row["part_number"] == pn]
    assert len(found) == 1, f"{pn} appears {len(found)} times"
    return found[0]


def _detail(client: TestClient, pn: str, **params: Any) -> dict[str, Any]:
    params["part_number"] = pn
    response = client.get("/api/tracking/detail", params=params)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _flow(detail: dict[str, Any], flow_id: int) -> dict[str, Any]:
    found = [flow for flow in detail["flows"] if flow["id"] == flow_id]
    assert len(found) == 1
    return cast(dict[str, Any], found[0])


def _types(movements: list[dict[str, Any]]) -> list[str]:
    return [movement["movement_type"] for movement in movements]


# ---------------------------------------------------------------------------
# The list: universe and derived status
# ---------------------------------------------------------------------------


def test_status_follows_active_quantity_open_demand_and_stock(
    client: TestClient, shop: _Shop
) -> None:
    active_pn = _unique("PN-ACT")
    stocked_pn = _unique("PN-STK")
    completed_pn = _unique("PN-CMP")
    open_pn = _unique("PN-OPN")
    scrapped_pn = _unique("PN-SCR")

    wo = _work_order(client, [_line(active_pn, 10)])
    _release(client, shop.material, wo, active_pn, quantity=10)

    wo = _work_order(client, [_line(stocked_pn, 10)])
    flow = _release(client, shop.material, wo, stocked_pn, quantity=4)
    _stock(client, shop.material, shop.stockroom, flow, stocked_pn, 4)

    wo = _work_order(client, [_line(completed_pn, 3)])
    flow = _release(client, shop.material, wo, completed_pn, quantity=3)
    _stock(client, shop.material, shop.stockroom, flow, completed_pn, 3)
    _allocate(client, completed_pn, [(wo.demand_id, 3)])

    _work_order(client, [_line(open_pn, 5)])

    wo = _work_order(client, [_line(scrapped_pn, 2)])
    flow = _release(client, shop.material, wo, scrapped_pn, quantity=2)
    _scrap(client, shop.material, flow, scrapped_pn, 2)

    assert _row(client, active_pn)["status"] == "ACTIVE"
    assert _row(client, stocked_pn)["status"] == "STOCKED"
    assert _row(client, completed_pn)["status"] == "COMPLETED"
    # Demand with nothing released is tracked (findable by its Work
    # Order) as OPEN; so is a demand whose whole release was scrapped.
    assert _row(client, open_pn)["status"] == "OPEN"
    assert _row(client, scrapped_pn)["status"] == "OPEN"

    # The default status filter is ACTIVE; every other status hides.
    default = client.get("/api/tracking", params={"search": "PN-"}).json()
    assert active_pn in {row["part_number"] for row in default["rows"]}
    assert stocked_pn not in {row["part_number"] for row in default["rows"]}
    assert [row["part_number"] for row in _rows(client, status="STOCKED", search=stocked_pn)] == [
        stocked_pn
    ]
    assert [
        row["part_number"] for row in _rows(client, status="COMPLETED", search=completed_pn)
    ] == [completed_pn]


def test_search_reaches_the_pn_and_any_demands_work_order_or_job_number(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-SRCH")
    number = _unique("WO")
    job = _unique("JOB")
    wo = _work_order(client, [_line(pn, 6, job_numbers=[job])], number=number)
    _release(client, shop.material, wo, pn, quantity=6)
    other_pn = _unique("PN-OTHER")
    _work_order(client, [_line(other_pn, 1)])

    for text in (pn.lower(), pn[3:12], number, number.lower(), job, job[:6]):
        found = {row["part_number"] for row in _rows(client, search=text)}
        assert pn in found, text
        assert other_pn not in found, text
    # LIKE metacharacters are literal search text.
    assert _rows(client, search="%" + pn) == []


def test_a_completed_work_order_number_still_finds_its_pn(client: TestClient, shop: _Shop) -> None:
    pn = _unique("PN-HIST")
    number = _unique("WO")
    wo = _work_order(client, [_line(pn, 2)], number=number)
    flow = _release(client, shop.material, wo, pn, quantity=2)
    _stock(client, shop.material, shop.stockroom, flow, pn, 2)
    _allocate(client, pn, [(wo.demand_id, 2)])
    row = _row(client, pn, search=number)
    assert row["status"] == "COMPLETED"
    # History only: no open demand context, no Hot rank, no due date.
    assert row["demands"] == []
    assert row["hot_rank"] is None
    assert row["next_due_date"] is None


def test_row_figures_distribution_master_and_next_due_date(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN-FIG")
    hot = _work_order(
        client, [_line(pn, 10, due_date="2030-03-01", job_numbers=["J1"])], number=_unique("WO")
    )
    _set_priority(db_engine, hot.demand_id, 2)
    soon = _work_order(client, [_line(pn, 4, due_date="2030-01-15", request_type="MODIFY")])
    flow = _release(client, shop.material, hot, pn, quantity=10)
    _transfer(client, shop.material, shop.lathe, flow, pn, 6)
    lathe_flow = int(_row_flow_ids(client, shop, pn)[shop.lathe.area_id][0])
    _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        lathe_flow,
        pn,
        2,
        machine_id=shop.lathe.machine_id,
    )
    [material_flow] = _row_flow_ids(client, shop, pn)[shop.material.area_id]
    _scrap(client, shop.material, material_flow, pn, 1)
    stock_flow = _release(client, shop.material, soon, pn, quantity=2, confirm_active_quantity=True)
    _stock(client, shop.material, shop.stockroom, stock_flow, pn, 2)

    row = _row(client, pn)
    assert row["status"] == "ACTIVE"
    assert row["has_master"] is True
    assert row["barcode_value"] == f"PF:PN:{pn}"
    assert row["active_quantity"] == 9
    assert row["stocked_quantity"] == 2
    assert row["scrapped_quantity"] == 1
    # Hot rank from the first demand in canonical order; the next due
    # date is the EARLIEST due date among the open demands — not the
    # Hot demand's.
    assert row["hot_rank"] == 2
    assert row["next_due_date"] == "2030-01-15"
    assert [d["work_order_demand_id"] for d in row["demands"]] == [hot.demand_id, soon.demand_id]
    assert row["demands"][0]["job_numbers"] == ["J1"]
    assert [(d["area"]["id"], d["quantity"], d["stocked"]) for d in row["distribution"]] == [
        (shop.lathe.area_id, 6, False),
        (shop.material.area_id, 3, False),
        (shop.stockroom.area_id, 2, True),
    ]

    # A hard-deleted master never hides the PN or its history.
    _delete_master(db_engine, pn)
    row = _row(client, pn)
    assert row["has_master"] is False
    assert row["active_quantity"] == 9
    detail = _detail(client, pn)
    assert detail["master"] is None
    assert detail["barcode_value"] == f"PF:PN:{pn}"
    assert detail["active_quantity"] == 9


def _row_flow_ids(client: TestClient, shop: _Shop, pn: str) -> dict[int, list[int]]:
    """ACTIVE flow ids of the PN per Area (from the detail)."""
    detail = _detail(client, pn)
    found: dict[int, list[int]] = {}
    for flow in detail["flows"]:
        if flow["status"] == "ACTIVE":
            found.setdefault(flow["position"]["area"]["id"], []).append(flow["id"])
    return found


def test_area_operation_machine_request_type_and_hot_filters(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN-FLT")
    other = _unique("PN-FLT")
    wo = _work_order(client, [_line(pn, 5, request_type="MODIFY"), _line(other, 5)])
    flow = _release(client, shop.material, wo, pn, quantity=5)
    _release(client, shop.cut, wo, other, demand_id=wo.demand_ids[1], quantity=5)
    _transfer(client, shop.material, shop.lathe, flow, pn, 5)
    _machine_action(
        client, "machine-assignments", shop.lathe, flow, pn, 5, machine_id=shop.lathe.machine_id
    )
    _set_priority(db_engine, wo.demand_id, 1)

    def names(**params: Any) -> set[str]:
        return {row["part_number"] for row in _rows(client, search="PN-FLT", **params)}

    assert names(area_id=shop.lathe.area_id) == {pn}
    assert names(area_id=shop.cut.area_id) == {other}
    assert names(operation_id=shop.lathe.operation_id) == {pn}
    assert names(machine_id=shop.lathe.machine_id) == {pn}
    assert names(machine_id=shop.lathe.machine_ids[1]) == set()
    assert names(request_type="MODIFY") == {pn}
    assert names(request_type="NEW") == {other}
    assert names(hot_only="true") == {pn}


def test_due_window_filters_are_judged_on_the_site_calendar(
    client: TestClient, shop: _Shop
) -> None:
    today = site_today()
    overdue_pn, week_pn, month_pn, undated_pn = (_unique("PN-DUE") for _ in range(4))
    _work_order(client, [_line(overdue_pn, 1, due_date=str(today - datetime.timedelta(days=1)))])
    _work_order(client, [_line(week_pn, 1, due_date=str(today))])
    # The last day of the month may fall inside this week; the two
    # windows are judged independently.
    last_day = today.replace(day=28)
    _work_order(client, [_line(month_pn, 1, due_date=str(last_day))])
    _work_order(client, [_line(undated_pn, 1)])

    def names(**params: Any) -> set[str]:
        return {row["part_number"] for row in _rows(client, search="PN-DUE", **params)}

    assert overdue_pn in names(due="OVERDUE")
    assert week_pn not in names(due="OVERDUE")
    week = names(due="THIS_WEEK")
    assert week_pn in week
    assert overdue_pn not in week
    assert undated_pn not in week
    month = names(due="THIS_MONTH")
    assert week_pn in month
    assert overdue_pn not in month
    if last_day >= today:
        assert month_pn in month
    assert undated_pn in names(due="ANY")


def test_rows_come_in_the_canonical_demand_order_and_page(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    prefix = _unique("PN-ORD")
    hot_pn, dated_pn, undated_pn, none_pn = (f"{prefix}-{n}" for n in ("H", "D", "U", "N"))
    dated = _work_order(client, [_line(dated_pn, 1, due_date="2031-01-01")])
    hot = _work_order(client, [_line(hot_pn, 1, due_date="2031-12-31")])
    _set_priority(db_engine, hot.demand_id, 1)
    undated = _work_order(client, [_line(undated_pn, 1)])
    none = _work_order(client, [_line(none_pn, 1)])
    for wo, pn in ((dated, dated_pn), (hot, hot_pn), (undated, undated_pn)):
        _release(client, shop.material, wo, pn, quantity=1)
    flow = _release(client, shop.material, none, none_pn, quantity=1)
    _stock(client, shop.material, shop.stockroom, flow, none_pn, 1)
    _allocate(client, none_pn, [(none.demand_id, 1)])

    assert [row["part_number"] for row in _rows(client, search=prefix)] == [
        hot_pn,
        dated_pn,
        undated_pn,
        none_pn,
    ]
    page = client.get(
        "/api/tracking", params={"search": prefix, "status": "ALL", "offset": 1, "limit": 2}
    ).json()
    assert [row["part_number"] for row in page["rows"]] == [dated_pn, undated_pn]
    assert (page["total"], page["offset"], page["limit"], page["has_more"]) == (4, 1, 2, True)
    last = client.get(
        "/api/tracking", params={"search": prefix, "status": "ALL", "offset": 3, "limit": 2}
    ).json()
    assert [row["part_number"] for row in last["rows"]] == [none_pn]
    assert last["has_more"] is False
    assert client.get("/api/tracking", params={"limit": 0}).status_code == 422


# ---------------------------------------------------------------------------
# The detail
# ---------------------------------------------------------------------------


def test_an_unknown_pn_is_404_and_the_input_is_canonicalized(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-CANON")
    wo = _work_order(client, [_line(pn, 1)])
    _release(client, shop.material, wo, pn, quantity=1)
    assert _detail(client, f"  {pn.lower()} ")["part_number"] == pn
    unknown = client.get("/api/tracking/detail", params={"part_number": _unique("PN-NO")})
    assert unknown.status_code == 404
    assert client.get("/api/tracking/detail", params={"part_number": "A B"}).status_code == 422
    assert (
        client.get("/api/tracking/movements", params={"part_number": _unique("PN-NO")}).status_code
        == 404
    )


def test_detail_demand_positions_stock_allocation_and_reconciliation(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-DET")
    wo = _work_order(client, [_line(pn, 12, due_date="2032-05-05")], number=_unique("WO"))
    flow = _release(client, shop.material, wo, pn, quantity=10)
    _transfer(client, shop.material, shop.lathe, flow, pn, 7)
    by_area = _row_flow_ids(client, shop, pn)
    lathe_flow = by_area[shop.lathe.area_id][0]
    _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        lathe_flow,
        pn,
        3,
        machine_id=shop.lathe.machine_id,
    )
    on_machine = next(
        flow_id
        for flow_id in _row_flow_ids(client, shop, pn)[shop.lathe.area_id]
        if _flow(_detail(client, pn), flow_id)["position"]["state"] == "MACHINE"
    )
    _machine_action(
        client, "area-completions", shop.lathe, on_machine, pn, 1, machine_id=shop.lathe.machine_id
    )
    done_flow = next(
        flow_id
        for flow_id in _row_flow_ids(client, shop, pn)[shop.lathe.area_id]
        if _flow(_detail(client, pn), flow_id)["position"]["state"] == "DONE"
    )
    _stock(client, shop.lathe, shop.stockroom, done_flow, pn, 1)
    _scrap(client, shop.material, by_area[shop.material.area_id][0], pn, 1)
    allocation_id = _allocate(client, pn, [(wo.demand_id, 1)])

    detail = _detail(client, pn)
    assert detail["status"] == "ACTIVE"
    assert detail["master"]["part_number"] == pn
    [demand] = detail["demands"]
    assert demand["work_order_number"] == wo.number
    assert (demand["requested_quantity"], demand["released_quantity"]) == (12, 10)
    assert (demand["allocated_quantity"], demand["shortage"]) == (1, 11)
    # Current quantity by Area / Machine — the shared derivation:
    # 2 on Lathe M1, 4 queued at Lathe, 2 processing at Material.
    assert [
        (
            loc["area"]["id"],
            loc["state"],
            loc["machine"]["id"] if loc["machine"] else None,
            loc["quantity"],
        )
        for loc in detail["locations"]
    ] == [
        (shop.lathe.area_id, "MACHINE", shop.lathe.machine_id, 2),
        (shop.lathe.area_id, "QUEUE", None, 4),
        (shop.material.area_id, "PROCESSING", None, 2),
    ]
    assert all(loc["since"] is not None for loc in detail["locations"])
    assert [(s["area"]["id"], s["quantity"]) for s in detail["stocked"]] == [
        (shop.stockroom.area_id, 1)
    ]
    assert (detail["active_quantity"], detail["stocked_quantity"]) == (8, 1)
    assert (detail["allocated_quantity"], detail["available_stocked_quantity"]) == (1, 0)
    assert detail["scrapped_quantity"] == 1
    # introduced = active + stocked + scrapped (PROJECT_PROFILE §11).
    assert detail["introduced_quantity"] == 10 == 8 + 1 + 1
    [allocation] = detail["allocations"]
    assert allocation["id"] == allocation_id
    assert allocation["quantity"] == 1
    assert allocation["work_order"]["work_order_number"] == wo.number
    assert allocation["reversed_by_allocation_id"] is None
    assert detail["allocation_total"] == 1
    # Every flow of the PN stays listed with its status and lineage.
    assert detail["flow_total"] == len(detail["flows"])
    statuses = {flow["status"] for flow in detail["flows"]}
    assert {"ACTIVE", "SPLIT", "STOCKED", "SCRAPPED"} <= statuses
    source = _flow(detail, flow)
    assert source["status"] == "SPLIT"
    assert source["position"] is None
    assert {link["relation"] for link in source["children"]} == {"SPLIT"}
    assert len(source["children"]) == 2

    # An allocation reversal is history beside the allocation it undoes.
    reversed_ = client.post(
        f"/api/allocations/{allocation_id}/reversals",
        json={"reason": "wrong line", "device_event_id": str(uuid.uuid4())},
    )
    assert reversed_.status_code == 201, reversed_.text
    detail = _detail(client, pn)
    assert [a["reverses_allocation_id"] for a in detail["allocations"]] == [allocation_id, None]
    assert detail["allocations"][1]["reversed_by_allocation_id"] == detail["allocations"][0]["id"]
    assert detail["allocated_quantity"] == 0


def test_floating_trace_keeps_repeated_areas_repair_and_the_inherited_split_prefix(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-TRC")
    wo = _work_order(client, [_line(pn, 10)])
    flow = _release(client, shop.material, wo, pn, quantity=10)
    _transfer(client, shop.material, shop.cut, flow, pn, 10)
    _transfer(client, shop.cut, shop.lathe, flow, pn, 10)
    # A partial transfer back to Cut as a Repair splits the flow: the
    # repaired part goes back, the remainder stays at Lathe.
    _transfer(
        client, shop.lathe, shop.cut, flow, pn, 4, repair=True, repair_reason="chamfer missing"
    )
    detail = _detail(client, pn)
    by_area = _row_flow_ids(client, shop, pn)
    [repaired] = by_area[shop.cut.area_id]
    [remainder] = by_area[shop.lathe.area_id]

    def trace(flow_id: int) -> list[tuple[str, bool, bool]]:
        return [
            (step["area"]["name"], step["repair"], step["inherited"])
            for step in _flow(detail, flow_id)["trace"]
        ]

    # The repaired child: the inherited Material → Cut → Lathe prefix,
    # then its own Repair return to Cut — the repeated Area preserved.
    assert trace(repaired) == [
        (shop.material.name, False, True),
        (shop.cut.name, False, True),
        (shop.lathe.name, False, True),
        (shop.cut.name, True, False),
    ]
    # The remainder keeps the inherited trace and never moved since.
    assert trace(remainder) == [
        (shop.material.name, False, True),
        (shop.cut.name, False, True),
        (shop.lathe.name, False, True),
    ]
    assert _flow(detail, repaired)["parents"] == [{"quantity_flow_id": flow, "relation": "SPLIT"}]
    assert _flow(detail, repaired)["route_mode"] == "FLOATING"
    assert _flow(detail, repaired)["route_steps"] == []
    # The consumed source keeps its own trace (nothing inherited).
    assert trace(flow) == [
        (shop.material.name, False, False),
        (shop.cut.name, False, False),
        (shop.lathe.name, False, False),
    ]


def test_a_merge_result_starts_its_trace_at_the_merge_and_names_every_source(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-MRG")
    wo = _work_order(client, [_line(pn, 10)])
    first = _release(client, shop.material, wo, pn, quantity=4)
    second = _release(client, shop.material, wo, pn, quantity=6, confirm_active_quantity=True)
    _transfer(client, shop.material, shop.cut, first, pn, 4)
    _transfer(client, shop.material, shop.cut, second, pn, 6)
    merged = int(_merge(client, shop.cut, pn, [first, second])["quantity_flow_id"])
    detail = _detail(client, pn)
    result = _flow(detail, merged)
    assert result["status"] == "ACTIVE"
    assert result["quantity"] == 10
    assert sorted(link["quantity_flow_id"] for link in result["parents"]) == sorted([first, second])
    assert {link["relation"] for link in result["parents"]} == {"MERGED"}
    # Several parents: no single history to inherit — the sources keep
    # their traces, the result's own trace has no arrival yet.
    assert result["trace"] == []
    assert result["position"]["area"]["id"] == shop.cut.area_id
    assert [step["area"]["name"] for step in _flow(detail, first)["trace"]] == [
        shop.material.name,
        shop.cut.name,
    ]
    assert _flow(detail, first)["children"] == [{"quantity_flow_id": merged, "relation": "MERGED"}]


def test_planned_snapshot_states_and_a_confirmed_deviation(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN-PLN")
    template_id = _route_template(db_engine, [shop.material, shop.cut, shop.lathe, shop.stockroom])
    wo = _work_order(client, [_line(pn, 5)])
    flow = _release(client, shop.material, wo, pn, quantity=5, route_template_id=template_id)
    detail = _detail(client, pn)
    planned = _flow(detail, flow)
    assert planned["route_mode"] == "PLANNED"
    assert planned["source_template"]["id"] == template_id
    assert [step["state"] for step in planned["route_steps"]] == [
        "CURRENT",
        "FUTURE",
        "FUTURE",
        "FUTURE",
    ]
    assert planned["off_route"] is False
    assert planned["deviations"] == []

    # Skipping Cut is a deviation, confirmed with its reason.
    _transfer(
        client,
        shop.material,
        shop.lathe,
        flow,
        pn,
        5,
        confirm_route_deviation=True,
        route_deviation_reason="Cut backlog",
    )
    planned = _flow(_detail(client, pn), flow)
    assert [step["state"] for step in planned["route_steps"]] == [
        "CURRENT",
        "FUTURE",
        "FUTURE",
        "FUTURE",
    ]
    assert planned["off_route"] is True
    [deviation] = planned["deviations"]
    assert deviation["kind"] == "AREA"
    assert deviation["expected_area"]["id"] == shop.cut.area_id
    assert deviation["actual_area"]["id"] == shop.lathe.area_id
    assert deviation["reason"] == "Cut backlog"
    assert deviation["station_id"] == shop.lathe.station_id
    # The actual trace still records where the quantity went.
    assert [step["area"]["id"] for step in planned["trace"]] == [
        shop.material.area_id,
        shop.lathe.area_id,
    ]

    # Back on the route: Lathe → Stockroom fulfils the final step only
    # after Lathe itself is fulfilled; stocking straight from Lathe is
    # another deviation, and a STOCKED flow has no current step.
    _arrival(
        client,
        "stockings",
        shop.lathe,
        shop.stockroom,
        flow,
        pn,
        5,
        confirm_route_deviation=True,
        route_deviation_reason="finished early",
    )
    planned = _flow(_detail(client, pn), flow)
    assert planned["status"] == "STOCKED"
    assert planned["position"] is None
    assert [step["state"] for step in planned["route_steps"]] == [
        "DONE",
        "FUTURE",
        "FUTURE",
        "FUTURE",
    ]
    assert len(planned["deviations"]) == 2


def test_history_pages_newest_first_and_keeps_a_reversed_original_visible(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-HST")
    wo = _work_order(client, [_line(pn, 6)], number=_unique("WO"))
    flow = _release(client, shop.material, wo, pn, quantity=6)
    _transfer(client, shop.material, shop.cut, flow, pn, 6)
    undone = _transfer(client, shop.cut, shop.lathe, flow, pn, 6)
    _undo(client, shop.lathe, pn, str(undone["device_event_id"]))

    # Material and Cut process directly, so each transfer out of them
    # is the atomic AREA_COMPLETED + TRANSFERRED command; the Undo of
    # the last one appended two REVERSED rows: 7 Movements in all.
    detail = _detail(client, pn, movements_limit=4)
    page = detail["movements"]
    assert page["total"] == 7
    assert page["has_more"] is True
    assert _types(page["movements"]) == ["REVERSED", "REVERSED", "TRANSFERRED", "AREA_COMPLETED"]
    reversal_of_completion, reversal_of_transfer, original, completion = page["movements"]
    assert reversal_of_transfer["reverses_movement_id"] == original["id"]
    assert original["reversed_by_movement_id"] == reversal_of_transfer["id"]
    assert reversal_of_completion["reverses_movement_id"] == completion["id"]
    assert completion["reversed_by_movement_id"] == reversal_of_completion["id"]
    assert original["from_area"]["id"] == shop.cut.area_id
    assert original["to_area"]["id"] == shop.lathe.area_id
    assert original["station_id"] == shop.lathe.station_id
    assert original["device_event_id"] == undone["device_event_id"]
    assert (completion["command_sequence"], original["command_sequence"]) == (1, 2)
    assert page["next_before_movement_id"] == completion["id"]

    more = client.get(
        "/api/tracking/movements",
        params={"part_number": pn, "before": page["next_before_movement_id"], "limit": 4},
    ).json()
    assert _types(more["movements"]) == ["TRANSFERRED", "AREA_COMPLETED", "RECEIVED"]
    assert more["has_more"] is False
    assert more["next_before_movement_id"] is None
    received = more["movements"][2]
    assert received["demand"]["work_order_number"] == wo.number
    assert received["demand"]["work_order_demand_id"] == wo.demand_id
    assert received["from_area"] is None
    assert more["movements"][0]["reversed_by_movement_id"] is None

    # The current state excludes the undone transfer while the audit
    # trail keeps it: the flow is back at Cut, the trace ends at Cut.
    tracked = _flow(detail, flow)
    assert tracked["position"]["area"]["id"] == shop.cut.area_id
    assert [step["area"]["id"] for step in tracked["trace"]] == [
        shop.material.area_id,
        shop.cut.area_id,
    ]


def test_lineage_scrap_and_machine_movements_carry_their_audit_context(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN-AUD")
    wo = _work_order(client, [_line(pn, 8)])
    flow = _release(client, shop.lathe, wo, pn, quantity=8)
    _machine_action(
        client, "machine-assignments", shop.lathe, flow, pn, 3, machine_id=shop.lathe.machine_id
    )
    page = _detail(client, pn)["movements"]
    assert _types(page["movements"]) == [
        "ASSIGNED_TO_MACHINE",
        "SPLIT",
        "SPLIT",
        "SPLIT",
        "RECEIVED",
    ]
    assigned = page["movements"][0]
    assert assigned["destination_machine"]["id"] == shop.lathe.machine_id
    assert assigned["quantity"] == 3
    source_split = next(m for m in page["movements"] if m["quantity_flow_id"] == flow)
    assert source_split["movement_type"] == "SPLIT"
    assert {edge["child_flow_id"] for edge in source_split["lineage"]} == {
        m["quantity_flow_id"] for m in page["movements"] if m["movement_type"] == "SPLIT"
    } - {flow}
    child_split = next(
        m
        for m in page["movements"]
        if m["movement_type"] == "SPLIT" and m["quantity_flow_id"] != flow
    )
    assert child_split["lineage"] == [
        {
            "parent_flow_id": flow,
            "child_flow_id": child_split["quantity_flow_id"],
            "relation": "SPLIT",
        }
    ]
    _scrap(client, shop.lathe, assigned["quantity_flow_id"], pn, 3)
    newest = _detail(client, pn)["movements"]["movements"][0]
    assert newest["movement_type"] == "SCRAPPED"
    assert newest["reason"] == "damaged"
    assert newest["station_id"] == shop.lathe.station_id
