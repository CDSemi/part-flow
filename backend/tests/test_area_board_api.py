"""Integration tests for Phase 11 — the Area Board read model.

Exercises ``GET /api/area-board`` through the full request path
(FastAPI route, the Application read model, PostgreSQL) against a
dedicated temporary database migrated to head by the real Alembic
chain. Covered per IMPLEMENTATION_ROADMAP Phase 11, PROJECT_PROFILE
§21 and GUI_DESIGN §6:

- the Department scope: the Department's ACTIVE Areas in name order,
  each with its active Operations; the single active Department
  resolved without an id, several active Departments refused with
  their names, an unknown id 404;
- **one monitoring model**: the Area content of the board is byte-for
  -byte the answer ``GET /api/areas/{id}/inventory`` gives the Scan
  Station — the Area mode, the state split, the Machine cards holding
  only actively assigned quantity, the totals;
- the per-flow monitoring context the shared PN row presents: the
  entry timestamp (following the effective position-bearing Movement,
  and the OLDEST lineage branch of merged quantity), the completing
  Machine of finished quantity (only where every branch agrees), and
  the demand's Job Numbers, due date, Hot rank and received date;
- the Area context: scrapped quantity per PN (net of reversed scraps),
  and, for the terminal Stockroom, the stocked lines with the PN's
  active allocation — a terminal Area holds no ACTIVE inventory.

The API commits real transactions, so tests isolate through fresh
Departments, PNs and Work Orders; the module database is dropped
afterwards.
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

from alembic import command
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_area_board_api"
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
        json={"prefix": "AB-", "digits": 4},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _create_department(client: TestClient, *, name: str | None = None) -> int:
    response = client.post("/api/departments", json={"name": name or _unique("DEPT")})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


class _Cell:
    """An Area of one Department with an Operation, a station and Machines."""

    def __init__(
        self,
        client: TestClient,
        department_id: int,
        *,
        name: str,
        machine_count: int = 0,
        is_terminal: bool = False,
        external_operation: str | None = None,
    ) -> None:
        area = client.post(
            "/api/areas",
            json={
                "department_id": department_id,
                "name": name,
                "is_terminal": is_terminal,
                "color": "var(--a-lathe)",
                "description": f"{name} cell",
            },
        )
        assert area.status_code == 201, area.text
        self.area_id = int(area.json()["id"])
        self.name = name
        operation: dict[str, Any] = {"area_id": self.area_id, "code": _unique("OP")}
        if external_operation is not None:
            operation.update(name=external_operation, is_external=True)
        created = client.post("/api/operations", json=operation)
        assert created.status_code == 201, created.text
        self.operation_id = int(created.json()["id"])
        self.operation_code = str(created.json()["code"])
        station = client.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
        )
        assert station.status_code == 201, station.text
        self.station_id = str(station.json()["station_id"])
        self.machine_ids: list[int] = []
        self.machine_names: list[str] = []
        for index in range(machine_count):
            machine_name = f"{name} M{index + 1}"
            machine = client.post(
                "/api/machines", json={"area_id": self.area_id, "name": machine_name}
            )
            assert machine.status_code == 201, machine.text
            self.machine_ids.append(int(machine.json()["id"]))
            self.machine_names.append(machine_name)

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


class _Shop:
    """One Department with the Area modes the Area Board distinguishes."""

    def __init__(self, client: TestClient, *, name: str | None = None) -> None:
        self.department_id = _create_department(client, name=name)
        suffix = uuid.uuid4().hex[:6].upper()
        # Deliberately created out of alphabetical order: the board
        # answers in name order.
        self.lathe = _Cell(client, self.department_id, name=f"Lathe {suffix}", machine_count=2)
        self.deburr = _Cell(client, self.department_id, name=f"Deburr {suffix}")
        self.stockroom = _Cell(
            client, self.department_id, name=f"Stockroom {suffix}", is_terminal=True
        )
        self.material = _Cell(client, self.department_id, name=f"Material {suffix}")


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    return _Shop(client)


class _WorkOrder:
    def __init__(self, body: dict[str, Any]) -> None:
        self.id = int(body["id"])
        self.number = body["work_order_number"]
        self.received_date = str(body["received_date"])
        self.demand_ids = [int(line["id"]) for line in body["demands"]]

    @property
    def demand_id(self) -> int:
        return self.demand_ids[0]


def _create_work_order(
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


def _set_priority(engine: Engine, demand_id: int, rank: int | None) -> None:
    """Hot rank is Phase 12's to manage; the board only reads it."""
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
    confirm_active_quantity: bool = False,
) -> int:
    released = client.post(
        f"/api/work-orders/{work_order.id}/demands/{demand_id or work_order.demand_id}/release",
        json={
            "part_number": pn,
            "quantity": quantity,
            "route_mode": "FLOATING",
            "starting_area_id": cell.area_id,
            "operation_id": cell.operation_id,
            "confirm_active_quantity": confirm_active_quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert released.status_code == 201, released.text
    return int(released.json()["quantity_flow_id"])


def _arrival(
    client: TestClient,
    kind: str,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: int,
) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{target.station_id}/{kind}",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": source.area_id,
            "target_area_id": target.area_id,
            "quantity": quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _transfer(
    client: TestClient, source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int
) -> dict[str, Any]:
    return _arrival(client, "transfers", source, target, flow_id, pn, quantity)


def _stock(
    client: TestClient, source: _Cell, stockroom: _Cell, flow_id: int, pn: str, quantity: int
) -> dict[str, Any]:
    return _arrival(client, "stockings", source, stockroom, flow_id, pn, quantity)


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
    event_id = str(uuid.uuid4())
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/scraps",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "quantity": quantity,
            "reason": "damaged",
            "device_event_id": event_id,
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


def _allocate(client: TestClient, pn: str, lines: list[tuple[int, int]]) -> None:
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


def _newest_movement_id(engine: Engine, flow_id: int) -> int:
    with engine.connect() as connection:
        movement_id = connection.scalar(
            sa.select(sa.func.max(models.PartMovement.id)).where(
                models.PartMovement.quantity_flow_id == flow_id
            )
        )
    assert movement_id is not None
    return int(movement_id)


def _set_occurred_at(engine: Engine, movement_id: int, occurred_at: datetime.datetime) -> None:
    """Pin a Movement's ``occurred_at`` on the disposable test database.

    History is append-only to the application (the raise-on-write
    trigger); the seed bypasses the trigger for this one statement as
    the database superuser to author an OLD entry timestamp — the read
    model is then judged on that fixed history.
    """
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL session_replication_role = 'replica'"))
        connection.execute(
            sa.update(models.PartMovement)
            .where(models.PartMovement.id == movement_id)
            .values(occurred_at=occurred_at)
        )


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------


def _board(client: TestClient, department_id: int | None) -> dict[str, Any]:
    params = {"department_id": department_id} if department_id is not None else {}
    response = client.get("/api/area-board", params=params)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _area(board: dict[str, Any], area_id: int) -> dict[str, Any]:
    found = [entry for entry in board["areas"] if entry["inventory"]["area"]["id"] == area_id]
    assert len(found) == 1, f"Area {area_id} appears {len(found)} times"
    return cast(dict[str, Any], found[0])


def _flows(entry: dict[str, Any], pn: str) -> list[dict[str, Any]]:
    return [
        flow
        for line in entry["inventory"]["lines"]
        if line["part_number"] == pn
        for flow in line["flows"]
    ]


def _flow(entry: dict[str, Any], pn: str) -> dict[str, Any]:
    flows = _flows(entry, pn)
    assert len(flows) == 1, f"{pn} has {len(flows)} flows in the Area"
    return flows[0]


def _demands(entry: dict[str, Any], pn: str) -> list[dict[str, Any]]:
    """The PN's OPEN demand context in the Area — the monitoring one."""
    found = [item for item in entry["inventory"]["demand_context"] if item["part_number"] == pn]
    return list(found[0]["demands"]) if found else []


# ---------------------------------------------------------------------------
# Department scope and Area listing
# ---------------------------------------------------------------------------


def test_the_board_names_its_department_and_refuses_an_unknown_one(
    client: TestClient, shop: _Shop
) -> None:
    board = _board(client, shop.department_id)
    assert board["department"]["id"] == shop.department_id

    missing = client.get("/api/area-board", params={"department_id": 999_999})
    assert missing.status_code == 404
    assert "does not exist" in missing.json()["detail"]


def test_without_an_id_an_ambiguous_department_configuration_is_refused(
    client: TestClient, shop: _Shop
) -> None:
    # The Area Board resolves its Department by the SAME rule as the
    # Production Board: a second active Department makes the omitted id
    # ambiguous, and the refusal names both.
    second = _create_department(client, name=_unique("Assembly"))
    try:
        ambiguous = client.get("/api/area-board")
        assert ambiguous.status_code == 409
        detail = ambiguous.json()["detail"]
        assert f"(id {shop.department_id})" in detail
        assert f"(id {second})" in detail
    finally:
        deactivated = client.patch(f"/api/departments/{second}", json={"is_active": False})
        assert deactivated.status_code == 200, deactivated.text

    resolved = client.get("/api/area-board")
    assert resolved.status_code == 200
    assert resolved.json()["department"]["id"] == shop.department_id


def test_the_board_lists_the_departments_active_areas_in_name_order(
    client: TestClient, shop: _Shop
) -> None:
    board = _board(client, shop.department_id)
    names = [entry["inventory"]["area"]["name"] for entry in board["areas"]]
    assert names == sorted(names)
    assert {shop.lathe.name, shop.deburr.name, shop.stockroom.name, shop.material.name} <= set(
        names
    )

    # Each Area carries its active Operations for the column header, its
    # identity color and its description.
    lathe = _area(board, shop.lathe.area_id)
    assert [operation["code"] for operation in lathe["operations"]] == [shop.lathe.operation_code]
    assert lathe["inventory"]["area"]["color"] == "var(--a-lathe)"
    assert lathe["inventory"]["area"]["description"] == f"{shop.lathe.name} cell"


def test_an_area_of_another_department_never_appears(client: TestClient, shop: _Shop) -> None:
    other = _Shop(client)
    board = _board(client, shop.department_id)
    area_ids = {entry["inventory"]["area"]["id"] for entry in board["areas"]}
    assert other.lathe.area_id not in area_ids
    assert shop.lathe.area_id in area_ids


def test_a_deactivated_area_leaves_the_board(client: TestClient) -> None:
    shop = _Shop(client)
    spare = _Cell(client, shop.department_id, name=f"Spare {uuid.uuid4().hex[:6].upper()}")
    assert spare.area_id in {
        entry["inventory"]["area"]["id"] for entry in _board(client, shop.department_id)["areas"]
    }

    # An Area holding active quantity cannot be deactivated at all
    # (app.application.environment), so nothing in production is hidden.
    deactivated = client.patch(f"/api/areas/{spare.area_id}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text
    assert spare.area_id not in {
        entry["inventory"]["area"]["id"] for entry in _board(client, shop.department_id)["areas"]
    }


# ---------------------------------------------------------------------------
# One monitoring model — the Scan Station's own Area inventory
# ---------------------------------------------------------------------------


def test_the_area_content_is_exactly_the_scan_station_inventory(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 9}])
    flow = _release(client, shop.material, work_order, pn, quantity=9)
    _transfer(client, shop.material, shop.lathe, flow, pn, 9)
    assigned = _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        flow,
        pn,
        4,
        machine_id=shop.lathe.machine_id,
    )
    _machine_action(
        client,
        "area-completions",
        shop.lathe,
        int(assigned["quantity_flow_id"]),
        pn,
        1,
        machine_id=shop.lathe.machine_id,
    )

    board = _board(client, shop.department_id)
    station_view = client.get(f"/api/areas/{shop.lathe.area_id}/inventory")
    assert station_view.status_code == 200, station_view.text
    # The two views render the same quantity through the same shared
    # components, so they must receive the SAME answer — byte for byte.
    assert _area(board, shop.lathe.area_id)["inventory"] == station_view.json()


def test_machine_cards_hold_only_assigned_quantity_and_finished_names_its_machine(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 6}])
    flow = _release(client, shop.material, work_order, pn, quantity=6)
    _transfer(client, shop.material, shop.lathe, flow, pn, 6)
    # A partial action splits inside the command: the acted-on part and
    # the remainder are separate quantities from here on.
    assigned = _machine_action(
        client, "machine-assignments", shop.lathe, flow, pn, 5, machine_id=shop.lathe.machine_id
    )
    queued_flow = int(assigned["remainder_quantity_flow_id"])
    completed = _machine_action(
        client,
        "area-completions",
        shop.lathe,
        int(assigned["quantity_flow_id"]),
        pn,
        2,
        machine_id=shop.lathe.machine_id,
    )
    on_machine = int(completed["remainder_quantity_flow_id"])
    finished_flow = int(completed["quantity_flow_id"])

    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    inventory = entry["inventory"]
    assert inventory["has_machines"] is True
    # 1 queued + 3 still on the Machine + 2 finished = the whole 6.
    assert sum(item["quantity"] for item in _flows(entry, pn)) == 6
    states = {item["quantity_flow_id"]: item["processing_state"] for item in _flows(entry, pn)}
    assert states[queued_flow] == "QUEUED"
    assert states[on_machine] == "ON_MACHINE"
    assert states[finished_flow] == "READY_TO_TRANSFER"

    # The Machine card carries the assigned quantity only — the finished
    # portion left it for the Area summary.
    card = next(
        card for card in inventory["machines"] if card["machine"]["id"] == shop.lathe.machine_id
    )
    held = next(line for line in card["lines"] if line["part_number"] == pn)
    assert held["total_quantity"] == 3
    assert card["machine"]["operational_state"] == "RUNNING"
    assert finished_flow in {
        item["quantity_flow_id"] for line in inventory["finished"] for item in line["flows"]
    }

    # Finished quantity names the Machine that COMPLETED it as context;
    # quantity in any other state never carries one.
    finished = next(item for item in _flows(entry, pn) if item["quantity_flow_id"] == finished_flow)
    assert finished["completed_machine"] == {
        "id": shop.lathe.machine_id,
        "name": shop.lathe.machine_names[0],
    }
    assert finished["machine_id"] is None
    queued = next(item for item in _flows(entry, pn) if item["quantity_flow_id"] == queued_flow)
    assert queued["completed_machine"] is None

    # Quantity RETURNED from a Machine to the queue is the sharp case:
    # its position-bearing Movement names the Machine it left, and it
    # must still not read as finished there.
    returned = _machine_action(
        client,
        "machine-releases",
        shop.lathe,
        on_machine,
        pn,
        3,
        machine_id=shop.lathe.machine_id,
    )
    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    released = next(
        item
        for item in _flows(entry, pn)
        if item["quantity_flow_id"] == int(returned["quantity_flow_id"])
    )
    assert released["processing_state"] == "QUEUED"
    assert released["machine_id"] is None
    assert released["completed_machine"] is None


def test_finished_quantity_keeps_its_completing_machine_after_retirement(
    client: TestClient, shop: _Shop
) -> None:
    """The Machine that finished the work is completion CONTEXT, not a
    card lookup: retiring it removes the card, never the answer to
    where the quantity was completed."""
    cell = _Cell(
        client,
        shop.department_id,
        name=f"Mill {uuid.uuid4().hex[:6].upper()}",
        machine_count=2,
    )
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    flow = _release(client, shop.material, work_order, pn, quantity=5)
    _transfer(client, shop.material, cell, flow, pn, 5)
    assigned = _machine_action(
        client, "machine-assignments", cell, flow, pn, 5, machine_id=cell.machine_ids[0]
    )
    _machine_action(
        client,
        "area-completions",
        cell,
        int(assigned["quantity_flow_id"]),
        pn,
        5,
        machine_id=cell.machine_ids[0],
    )

    entry = _area(_board(client, shop.department_id), cell.area_id)
    assert _flow(entry, pn)["completed_machine"] == {
        "id": cell.machine_ids[0],
        "name": cell.machine_names[0],
    }

    # Retirement is allowed now: the Machine holds no assigned quantity
    # any more — the finished quantity waits in the Area.
    retired = client.post(
        f"/api/machines/{cell.machine_ids[0]}/retire",
        json={"reason": "replaced", "actor": "tester"},
    )
    assert retired.status_code == 200, retired.text

    entry = _area(_board(client, shop.department_id), cell.area_id)
    # The card is gone…
    assert cell.machine_ids[0] not in {
        card["machine"]["id"] for card in entry["inventory"]["machines"]
    }
    # …and the finished quantity still names where it was completed.
    assert _flow(entry, pn)["completed_machine"] == {
        "id": cell.machine_ids[0],
        "name": cell.machine_names[0],
    }
    assert _flow(entry, pn)["processing_state"] == "READY_TO_TRANSFER"


def test_an_area_without_machines_processes_directly(client: TestClient, shop: _Shop) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 4}])
    flow = _release(client, shop.material, work_order, pn, quantity=4)
    _transfer(client, shop.material, shop.deburr, flow, pn, 4)

    entry = _area(_board(client, shop.department_id), shop.deburr.area_id)
    assert entry["inventory"]["has_machines"] is False
    assert entry["inventory"]["machines"] == []
    assert _flow(entry, pn)["processing_state"] == "PROCESSING"
    assert entry["inventory"]["processing_quantity"] >= 4
    assert entry["inventory"]["queued_quantity"] == 0


# ---------------------------------------------------------------------------
# The per-flow monitoring context of the shared PN row
# ---------------------------------------------------------------------------


def test_a_pn_carries_its_open_demand_context_job_numbers_due_date_and_hot_rank(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(
        client,
        [
            {
                "part_number": pn,
                "requested_quantity": 5,
                "due_date": "2026-09-30",
                "job_numbers": ["18112", "18113"],
            }
        ],
        number="WO-AB-1",
        received_date="2026-07-12",
    )
    _set_priority(db_engine, work_order.demand_id, 1)
    flow = _release(client, shop.material, work_order, pn, quantity=5)

    entry = _area(_board(client, shop.department_id), shop.material.area_id)
    demands = _demands(entry, pn)
    assert len(demands) == 1
    assert demands[0]["work_order_number"] == "WO-AB-1"
    assert demands[0]["work_order_demand_id"] == work_order.demand_id
    assert demands[0]["request_type"] == "NEW"
    assert demands[0]["job_numbers"] == ["18112", "18113"]
    assert demands[0]["due_date"] == "2026-09-30"
    assert demands[0]["priority_rank"] == 1
    assert demands[0]["received_date"] == "2026-07-12"
    assert demands[0]["requested_quantity"] == 5

    # The flow keeps the demand it ORIGINATED from as provenance —
    # identity and request type only, never the monitoring values.
    provenance = _flow(entry, pn)["work_order"]
    assert provenance == {
        "work_order_id": work_order.id,
        "work_order_number": "WO-AB-1",
        "work_order_demand_id": work_order.demand_id,
        "request_type": "NEW",
    }

    # The same Area, read by the Scan Station: one model, one answer.
    station = client.get(f"/api/areas/{shop.material.area_id}/inventory").json()
    station_flow = next(
        item
        for line in station["lines"]
        if line["part_number"] == pn
        for item in line["flows"]
        if item["quantity_flow_id"] == flow
    )
    assert station_flow["work_order"] == provenance
    assert station["demand_context"] == entry["inventory"]["demand_context"]


def test_the_monitoring_context_is_the_open_demands_never_the_completed_origin(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A completed Work Order is history: quantity it released keeps
    moving, but the row's Hot rank, dates and Job Numbers come from the
    PN's OPEN demand — never from the demand the quantity originated
    from, which stays on the flow as provenance."""
    pn = _unique("PN")
    origin = _create_work_order(
        client,
        [
            {
                "part_number": pn,
                "requested_quantity": 4,
                "due_date": "2026-08-01",
                "job_numbers": ["ORIGIN-JOB"],
            }
        ],
        number="WO-AB-ORIGIN",
    )
    _set_priority(db_engine, origin.demand_id, 2)
    flow = _release(client, shop.material, origin, pn, quantity=4)
    _transfer(client, shop.material, shop.lathe, flow, pn, 4)

    # While the origin Work Order is open it IS the PN's open demand.
    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    assert [item["work_order_number"] for item in _demands(entry, pn)] == ["WO-AB-ORIGIN"]

    # A second Work Order supplies the stocked quantity that completes
    # the origin demand (allocation is PN-level), so the origin Work
    # Order completes while the quantity it released is still in the
    # Lathe.
    current = _create_work_order(
        client,
        [
            {
                "part_number": pn,
                "requested_quantity": 6,
                "due_date": "2026-12-24",
                "job_numbers": ["CURRENT-JOB"],
            }
        ],
        number="WO-AB-CURRENT",
    )
    _set_priority(db_engine, current.demand_id, 1)
    supply = _release(client, shop.material, current, pn, quantity=4, confirm_active_quantity=True)
    _stock(client, shop.material, shop.stockroom, supply, pn, 4)
    _allocate(client, pn, [(origin.demand_id, 4)])

    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    demands = _demands(entry, pn)
    # The completed origin is gone from the monitoring context; the open
    # Work Order is what the row is worked for now.
    assert [item["work_order_number"] for item in demands] == ["WO-AB-CURRENT"]
    assert demands[0]["priority_rank"] == 1
    assert demands[0]["job_numbers"] == ["CURRENT-JOB"]
    # …while the quantity still names where it came from, and is still
    # on the board.
    provenance = _flow(entry, pn)["work_order"]
    assert provenance["work_order_number"] == "WO-AB-ORIGIN"
    assert provenance["work_order_demand_id"] == origin.demand_id
    assert _flow(entry, pn)["quantity"] == 4


def test_quantity_without_any_open_demand_keeps_its_row_and_no_context(
    client: TestClient, shop: _Shop
) -> None:
    """Found quantity added at the station (Phase 9) belongs to no
    demand: it is shown, with an empty monitoring context rather than a
    borrowed one."""
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 3}])
    flow = _release(client, shop.material, work_order, pn, quantity=3)
    _transfer(client, shop.material, shop.lathe, flow, pn, 3)
    added = client.post(
        f"/api/scan-stations/{shop.lathe.station_id}/quantity-additions",
        json={
            "part_number": pn,
            "quantity": 2,
            "reason": "found on the rack",
            "operation_id": shop.lathe.operation_id,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert added.status_code == 201, added.text
    addition = int(added.json()["quantity_flow_id"])

    # Its Work Order is still open, so the PN has a context…
    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    assert len(_demands(entry, pn)) == 1
    added_flow = next(item for item in _flows(entry, pn) if item["quantity_flow_id"] == addition)
    # …but the added quantity itself never borrowed a provenance.
    assert added_flow["work_order"] is None
    assert added_flow["quantity"] == 2


def test_several_open_demands_stay_explicit_in_the_canonical_order(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    hot = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 2, "due_date": "2026-11-30"}],
        number="WO-AB-HOT",
    )
    dated = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 3, "due_date": "2026-10-01"}],
        number="WO-AB-DATED",
    )
    undated = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 4}],
        number="WO-AB-UNDATED",
    )
    _set_priority(db_engine, hot.demand_id, 3)
    _release(client, shop.material, dated, pn, quantity=3)

    entry = _area(_board(client, shop.department_id), shop.material.area_id)
    # All three are reported — nothing is picked or summed — in the
    # canonical demand order: Hot rank first, then the earliest due
    # date, then the undated demand.
    assert [item["work_order_number"] for item in _demands(entry, pn)] == [
        "WO-AB-HOT",
        "WO-AB-DATED",
        "WO-AB-UNDATED",
    ]
    assert [item["priority_rank"] for item in _demands(entry, pn)] == [3, None, None]
    assert undated.number == "WO-AB-UNDATED"


def test_the_entry_timestamp_follows_the_effective_position_bearing_movement(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 8}])
    flow = _release(client, shop.material, work_order, pn, quantity=8)
    _transfer(client, shop.material, shop.lathe, flow, pn, 8)
    arrival = _newest_movement_id(db_engine, flow)
    entered = datetime.datetime(2026, 8, 1, 6, 30, tzinfo=datetime.UTC)
    _set_occurred_at(db_engine, arrival, entered)

    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    assert _flow(entry, pn)["entered_at"].startswith("2026-08-01T06:30")

    # A partial assignment SPLITs the quantity: the remainder keeps the
    # entry it already had, and the child inherits it through the
    # lineage — neither restarts its dwell time at the split.
    assigned = _machine_action(
        client, "machine-assignments", shop.lathe, flow, pn, 3, machine_id=shop.lathe.machine_id
    )
    remainder = int(assigned["remainder_quantity_flow_id"])
    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    entered_by_flow = {item["quantity_flow_id"]: item["entered_at"] for item in _flows(entry, pn)}
    assert entered_by_flow[remainder].startswith("2026-08-01T06:30")
    # The assignment itself IS a position change, so the assigned child
    # is dated by it — not by the arrival it descends from.
    assigned_flow = int(assigned["quantity_flow_id"])
    assert not entered_by_flow[assigned_flow].startswith("2026-08-01T06:30")


def test_merged_quantity_is_dated_from_the_oldest_branch_and_needs_one_machine(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    # One PN appears at most once per Work Order, so the two portions
    # come from two Work Orders — which is also why the merge result has
    # no single demand context.
    first_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 3}])
    second_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 3}])
    first = _release(client, shop.material, first_order, pn, quantity=3)
    second = _release(
        client, shop.material, second_order, pn, quantity=3, confirm_active_quantity=True
    )
    _transfer(client, shop.material, shop.lathe, first, pn, 3)
    old = _newest_movement_id(db_engine, first)
    _transfer(client, shop.material, shop.lathe, second, pn, 3)

    # Each portion is completed on a DIFFERENT Machine, which the merge
    # command allows: an AREA_COMPLETED cleared the current Machine, so
    # the two portions share their position.
    first_done = _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        first,
        pn,
        3,
        machine_id=shop.lathe.machine_ids[0],
    )
    _machine_action(
        client,
        "area-completions",
        shop.lathe,
        int(first_done["quantity_flow_id"]),
        pn,
        3,
        machine_id=shop.lathe.machine_ids[0],
    )
    second_done = _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        second,
        pn,
        3,
        machine_id=shop.lathe.machine_ids[1],
    )
    _machine_action(
        client,
        "area-completions",
        shop.lathe,
        int(second_done["quantity_flow_id"]),
        pn,
        3,
        machine_id=shop.lathe.machine_ids[1],
    )
    # The OLDEST branch is authored old, so the merge result must be
    # dated by it — the newer branch is offered first on purpose.
    completed_first = _newest_movement_id(db_engine, int(first_done["quantity_flow_id"]))
    _set_occurred_at(db_engine, old, datetime.datetime(2026, 7, 2, 5, 0, tzinfo=datetime.UTC))
    _set_occurred_at(
        db_engine, completed_first, datetime.datetime(2026, 7, 3, 5, 0, tzinfo=datetime.UTC)
    )
    merged = _merge(
        client,
        shop.lathe,
        pn,
        [int(second_done["quantity_flow_id"]), int(first_done["quantity_flow_id"])],
    )

    entry = _area(_board(client, shop.department_id), shop.lathe.area_id)
    result = next(
        item
        for item in _flows(entry, pn)
        if item["quantity_flow_id"] == int(merged["quantity_flow_id"])
    )
    assert result["quantity"] == 6
    assert result["processing_state"] == "READY_TO_TRANSFER"
    assert result["entered_at"].startswith("2026-07-03T05:00")
    # The branches were completed on different Machines: the merged
    # quantity is shown with NO completing Machine rather than crediting
    # one of them with all of it.
    assert result["completed_machine"] is None


# ---------------------------------------------------------------------------
# Area context: scrap and the terminal Stockroom
# ---------------------------------------------------------------------------


def test_scrapped_quantity_is_reported_per_area_and_net_of_reversed_scraps(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    flow = _release(client, shop.material, work_order, pn, quantity=10)
    # A partial scrap splits the quantity: the remainder carries on.
    first_scrap = _scrap(client, shop.material, flow, pn, 2)
    remainder = int(first_scrap["remainder_quantity_flow_id"])
    _transfer(client, shop.material, shop.lathe, remainder, pn, 8)
    reversed_scrap = _scrap(client, shop.lathe, remainder, pn, 3)

    board = _board(client, shop.department_id)
    scrapped: dict[int, dict[str, int]] = {
        int(entry["inventory"]["area"]["id"]): {
            str(line["part_number"]): int(line["quantity"]) for line in entry["scrapped"]
        }
        for entry in board["areas"]
    }
    assert scrapped[shop.material.area_id][pn] == 2
    assert scrapped[shop.lathe.area_id][pn] == 3
    assert pn not in scrapped[shop.deburr.area_id]

    _undo(client, shop.lathe, pn, str(reversed_scrap["device_event_id"]))
    board = _board(client, shop.department_id)
    lathe = {
        line["part_number"]: line["quantity"]
        for line in _area(board, shop.lathe.area_id)["scrapped"]
    }
    assert pn not in lathe
    material = {
        line["part_number"]: line["quantity"]
        for line in _area(board, shop.material.area_id)["scrapped"]
    }
    assert material[pn] == 2


def test_a_terminal_area_reports_stocked_lines_with_their_allocation(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 7}])
    flow = _release(client, shop.material, work_order, pn, quantity=7)
    _transfer(client, shop.material, shop.lathe, flow, pn, 7)
    _stock(client, shop.lathe, shop.stockroom, flow, pn, 7)

    entry = _area(_board(client, shop.department_id), shop.stockroom.area_id)
    # Stocked quantity is manufacturing-complete: its flow is closed, so
    # a terminal Area holds no ACTIVE inventory at all.
    assert entry["inventory"]["total_quantity"] == 0
    assert entry["inventory"]["lines"] == []
    stocked = {line["part_number"]: line for line in entry["stocked"]}
    assert stocked[pn]["quantity"] == 7
    assert stocked[pn]["allocated_quantity"] == 0

    _allocate(client, pn, [(work_order.demand_id, 5)])
    entry = _area(_board(client, shop.department_id), shop.stockroom.area_id)
    stocked = {line["part_number"]: line for line in entry["stocked"]}
    assert stocked[pn]["quantity"] == 7
    assert stocked[pn]["allocated_quantity"] == 5


def test_a_non_terminal_area_never_reports_stocked_lines(client: TestClient, shop: _Shop) -> None:
    board = _board(client, shop.department_id)
    for entry in board["areas"]:
        if not entry["inventory"]["area"]["is_terminal"]:
            assert entry["stocked"] == []
