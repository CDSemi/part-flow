"""Integration tests for Phase 11 — the Production Board read model.

Exercises ``GET /api/production-board`` through the full request path
(FastAPI route, the Application read model, PostgreSQL) against a
dedicated temporary database migrated to head by the real Alembic
chain. Covered per IMPLEMENTATION_ROADMAP Phase 11, PROJECT_PROFILE
§21 and GUI_DESIGN §5:

- the Department scope: only quantity currently in the Department's
  Areas, the single active Department resolved without an id, several
  active Departments refused with their names, an unknown id 404;
- the distribution per Area / Machine / External activity with the
  derived states (`MACHINE`, `QUEUE`, `PROCESSING`, `DONE`, `STOCKED`),
  quantity grouped per position with the OLDEST entry timestamp, the
  entry timestamp following the effective position-bearing Movement
  (a split child inherits its parent's entry, an undone command
  restores the earlier one), the completing Machine as DONE context;
- the stocked and scrapped quantities derived from history (net of
  reversed scraps), the row totals and the footer totals reconciling;
- the OPEN demand context (Work Order Number, Job Numbers, requested /
  allocated quantity, request type) in the canonical order, the first
  demand defining the row's Hot rank, due and received dates, a
  completed Work Order never supplying metadata even while its
  quantity is still in production, and a quantity without any demand
  context keeping its row with the no-demand fallback;
- the row selection: stocked-only rows stay only while an open demand
  exists, and leave once the Work Order completes;
- the canonical board order and nothing else: Hot rank, dated
  earliest first, undated by received date, the deterministic
  tie-breaker — stocked quantity is not a sorting tier.

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
_TEST_DATABASE = "partflow_test_production_board_api"
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
        json={"prefix": "PB-", "digits": 4},
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


def _deactivate_department(client: TestClient, department_id: int) -> None:
    response = client.patch(f"/api/departments/{department_id}", json={"is_active": False})
    assert response.status_code == 200, response.text


class _Cell:
    """An Area of one Department with an Operation, a station and Machines."""

    def __init__(
        self,
        client: TestClient,
        department_id: int,
        *,
        name: str | None = None,
        machine_count: int = 0,
        is_terminal: bool = False,
        external_operation: str | None = None,
        color: str | None = None,
    ) -> None:
        area = client.post(
            "/api/areas",
            json={
                "department_id": department_id,
                "name": name or _unique("AREA"),
                "is_terminal": is_terminal,
                "color": color,
            },
        )
        assert area.status_code == 201, area.text
        self.area = cast(dict[str, Any], area.json())
        self.area_id = int(self.area["id"])
        operation: dict[str, Any] = {"area_id": self.area_id, "code": _unique("OP")}
        if external_operation is not None:
            operation.update(name=external_operation, is_external=True)
        created = client.post("/api/operations", json=operation)
        assert created.status_code == 201, created.text
        self.operation_id = int(created.json()["id"])
        station = client.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
        )
        assert station.status_code == 201, station.text
        self.station_id = str(station.json()["station_id"])
        self.machine_ids: list[int] = []
        self.machine_names: list[str] = []
        for _ in range(machine_count):
            machine_name = _unique("Lathe")
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
    """One Department with the four Area modes the board distinguishes."""

    def __init__(self, client: TestClient, *, name: str | None = None) -> None:
        self.department_id = _create_department(client, name=name)
        self.material = _Cell(client, self.department_id, name=_unique("Material"))
        self.lathe = _Cell(client, self.department_id, name=_unique("Lathe"), machine_count=2)
        self.external = _Cell(
            client, self.department_id, name=_unique("External"), external_operation="Plating"
        )
        self.stockroom = _Cell(
            client, self.department_id, name=_unique("Stockroom"), is_terminal=True
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


def _set_occurred_at(engine: Engine, movement_id: int, occurred_at: datetime.datetime) -> None:
    """Pin a Movement's ``occurred_at`` on the disposable test database.

    History is append-only to the application (the raise-on-write
    trigger); the seed bypasses the trigger for this one statement as
    the database superuser to author an OLD entry timestamp — the
    read model is then judged on that fixed history.
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
    response = client.get("/api/production-board", params=params)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _row(board: dict[str, Any], pn: str) -> dict[str, Any]:
    rows = [row for row in board["rows"] if row["part_number"] == pn]
    assert len(rows) == 1, f"{pn} appears {len(rows)} times"
    return cast(dict[str, Any], rows[0])


def _row_or_none(board: dict[str, Any], pn: str) -> dict[str, Any] | None:
    rows = [row for row in board["rows"] if row["part_number"] == pn]
    return cast(dict[str, Any], rows[0]) if rows else None


def _locations(row: dict[str, Any]) -> list[tuple[str, str, str | None, int]]:
    return [
        (
            location["area"]["name"],
            location["state"],
            location["machine"]["name"] if location["machine"] else None,
            location["quantity"],
        )
        for location in row["locations"]
    ]


# ---------------------------------------------------------------------------
# Department scope
# ---------------------------------------------------------------------------


def test_the_board_names_its_department_and_refuses_an_unknown_one(
    client: TestClient, shop: _Shop
) -> None:
    board = _board(client, shop.department_id)
    assert board["department"]["id"] == shop.department_id
    assert board["department"]["name"].startswith("DEPT-")

    missing = client.get("/api/production-board", params={"department_id": 999_999})
    assert missing.status_code == 404
    assert "does not exist" in missing.json()["detail"]


def test_without_an_id_only_a_single_active_department_resolves(
    client: TestClient, shop: _Shop
) -> None:
    # Runs while the module holds exactly one active Department (the
    # shop fixture; later tests create their own shops, whose Areas
    # keep them active) — a second, Area-less Department is added and
    # deactivated again here.
    second = _create_department(client, name=_unique("Assembly"))
    try:
        # Two active Departments: the omitted id is refused with their names.
        ambiguous = client.get("/api/production-board")
        assert ambiguous.status_code == 409, ambiguous.text
        detail = ambiguous.json()["detail"]
        assert "Several active Departments" in detail
        assert f"(id {shop.department_id})" in detail
        assert f"(id {second})" in detail
    finally:
        _deactivate_department(client, second)

    # One active Department: the board resolves it without an id.
    resolved = client.get("/api/production-board")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["department"]["id"] == shop.department_id


def test_only_quantity_in_the_departments_areas_is_shown(client: TestClient, shop: _Shop) -> None:
    other = _Shop(client)
    pn = _unique("PN")
    mine = _create_work_order(client, [{"part_number": pn, "requested_quantity": 20}])
    _release(client, shop.material, mine, pn, quantity=6)
    theirs = _create_work_order(client, [{"part_number": pn, "requested_quantity": 20}])
    _release(client, other.material, theirs, pn, quantity=4, confirm_active_quantity=True)

    row = _row(_board(client, shop.department_id), pn)
    assert row["active_quantity"] == 6
    assert _locations(row) == [(shop.material.area["name"], "PROCESSING", None, 6)]
    other_row = _row(_board(client, other.department_id), pn)
    assert other_row["active_quantity"] == 4


# ---------------------------------------------------------------------------
# Distribution and states
# ---------------------------------------------------------------------------


def test_the_distribution_reports_every_state_with_its_machine_and_activity(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 40, "job_numbers": ["18112", "18113"]}],
        number="007001",
    )
    material_flow = _release(client, shop.material, work_order, pn, quantity=40)
    # 12 pcs go to the Lathe: 5 on a Machine, 3 queued, 4 finished on
    # the second Machine; 20 pcs go out for plating; 8 pcs stay in
    # Material (direct processing). Every partial command splits the
    # source, so the remainder ids are followed explicitly.
    to_lathe = _transfer(client, shop.material, shop.lathe, material_flow, pn, 12)
    lathe_flow = int(to_lathe["quantity_flow_id"])
    material_rest = int(to_lathe["remainder_quantity_flow_id"])
    on_machine = _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        lathe_flow,
        pn,
        5,
        machine_id=shop.lathe.machine_ids[0],
    )
    lathe_rest = int(on_machine["remainder_quantity_flow_id"])
    finished = _machine_action(
        client,
        "machine-assignments",
        shop.lathe,
        lathe_rest,
        pn,
        4,
        machine_id=shop.lathe.machine_ids[1],
    )
    _machine_action(
        client,
        "area-completions",
        shop.lathe,
        int(finished["quantity_flow_id"]),
        pn,
        4,
        machine_id=shop.lathe.machine_ids[1],
    )
    _transfer(client, shop.material, shop.external, material_rest, pn, 20)

    row = _row(_board(client, shop.department_id), pn)
    assert row["active_quantity"] == 40
    assert row["total_quantity"] == 40
    assert row["stocked_quantity"] == 0
    assert row["scrapped_quantity"] == 0
    assert _locations(row) == [
        (shop.external.area["name"], "PROCESSING", None, 20),
        (shop.lathe.area["name"], "MACHINE", shop.lathe.machine_names[0], 5),
        (shop.lathe.area["name"], "QUEUE", None, 3),
        (shop.lathe.area["name"], "DONE", shop.lathe.machine_names[1], 4),
        (shop.material.area["name"], "PROCESSING", None, 8),
    ]
    by_state = {
        (location["area"]["name"], location["state"]): location for location in row["locations"]
    }
    external = by_state[(shop.external.area["name"], "PROCESSING")]
    assert external["activity"] == "Plating"
    assert by_state[(shop.material.area["name"], "PROCESSING")]["activity"] is None
    assert (
        by_state[(shop.lathe.area["name"], "MACHINE")]["machine"]["id"]
        == (shop.lathe.machine_ids[0])
    )
    # Every active position carries its fixed entry timestamp.
    assert all(location["since"] is not None for location in row["locations"])
    # The demand context: the Work Order Number, the Job Numbers, the
    # requested quantity, nothing allocated yet, and the row's dates.
    assert row["demands"] == [
        {
            "work_order_id": work_order.id,
            "work_order_number": "007001",
            "work_order_demand_id": work_order.demand_id,
            "request_type": "NEW",
            "requested_quantity": 40,
            "allocated_quantity": 0,
            "job_numbers": ["18112", "18113"],
            "due_date": None,
            "priority_rank": None,
        }
    ]
    assert row["hot_rank"] is None
    assert row["due_date"] is None
    assert row["received_date"] == datetime.date.today().isoformat()


def test_quantity_in_one_position_is_grouped_with_its_oldest_entry(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 30}])
    first = _release(client, shop.material, work_order, pn, quantity=10)
    second = _release(
        client, shop.material, work_order, pn, quantity=20, confirm_active_quantity=True
    )
    old = datetime.datetime(2026, 8, 1, 6, 30, tzinfo=datetime.UTC)
    with db_engine.connect() as connection:
        first_movement = connection.scalar(
            sa.select(sa.func.min(models.PartMovement.id)).where(
                models.PartMovement.quantity_flow_id == first
            )
        )
    assert first_movement is not None
    _set_occurred_at(db_engine, int(first_movement), old)

    row = _row(_board(client, shop.department_id), pn)
    assert _locations(row) == [(shop.material.area["name"], "PROCESSING", None, 30)]
    assert datetime.datetime.fromisoformat(row["locations"][0]["since"]) == old
    assert second != first


def test_a_split_child_inherits_its_parents_entry_and_an_undo_restores_the_earlier_one(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    flow_id = _release(client, shop.material, work_order, pn, quantity=10)
    old = datetime.datetime(2026, 8, 2, 8, 0, tzinfo=datetime.UTC)
    with db_engine.connect() as connection:
        received_id = connection.scalar(
            sa.select(sa.func.min(models.PartMovement.id)).where(
                models.PartMovement.quantity_flow_id == flow_id
            )
        )
    assert received_id is not None
    _set_occurred_at(db_engine, int(received_id), old)

    # A partial transfer splits the flow: the remainder (a split child)
    # keeps the entry time of the quantity it came from.
    transferred = _transfer(client, shop.material, shop.lathe, flow_id, pn, 4)
    assert transferred["remainder_quantity"] == 6
    row = _row(_board(client, shop.department_id), pn)
    by_area = {location["area"]["name"]: location for location in row["locations"]}
    assert datetime.datetime.fromisoformat(by_area[shop.material.area["name"]]["since"]) == old
    assert by_area[shop.material.area["name"]]["quantity"] == 6
    assert datetime.datetime.fromisoformat(by_area[shop.lathe.area["name"]]["since"]) > old

    # Undoing the transfer restores the whole quantity in Material with
    # its original entry time — the undone Movements never count.
    _undo(client, shop.lathe, pn, str(transferred["device_event_id"]))
    row = _row(_board(client, shop.department_id), pn)
    assert _locations(row) == [(shop.material.area["name"], "PROCESSING", None, 10)]
    assert datetime.datetime.fromisoformat(row["locations"][0]["since"]) == old


# ---------------------------------------------------------------------------
# Stocked, scrapped, totals
# ---------------------------------------------------------------------------


def test_stocked_and_scrapped_quantities_derive_from_history(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 20}], number="007005"
    )
    flow_id = _release(client, shop.material, work_order, pn, quantity=20)
    # Partial commands split the source: the remainder is followed.
    rest = int(
        _stock(client, shop.material, shop.stockroom, flow_id, pn, 8)["remainder_quantity_flow_id"]
    )
    rest = int(_scrap(client, shop.material, rest, pn, 2)["remainder_quantity_flow_id"])
    undone_scrap = _scrap(client, shop.material, rest, pn, 3)
    _undo(client, shop.material, pn, str(undone_scrap["device_event_id"]))

    row = _row(_board(client, shop.department_id), pn)
    assert row["active_quantity"] == 10
    assert row["stocked_quantity"] == 8
    assert row["scrapped_quantity"] == 2
    assert row["total_quantity"] == 18
    assert _locations(row) == [
        (shop.material.area["name"], "PROCESSING", None, 10),
        (shop.stockroom.area["name"], "STOCKED", None, 8),
    ]
    stocked = row["locations"][1]
    assert stocked["since"] is None
    assert stocked["area"]["is_terminal"] is True
    # Leaving the stocked quantity unallocated keeps the row: the demand
    # is still open (allocated 0 of 20).
    assert row["demands"][0]["allocated_quantity"] == 0


def test_the_footer_totals_reconcile_with_the_rows(client: TestClient) -> None:
    shop = _Shop(client)
    pn_a, pn_b = _unique("PN"), _unique("PN")
    wo_a = _create_work_order(client, [{"part_number": pn_a, "requested_quantity": 10}])
    wo_b = _create_work_order(client, [{"part_number": pn_b, "requested_quantity": 10}])
    flow_a = _release(client, shop.material, wo_a, pn_a, quantity=10)
    flow_b = _release(client, shop.material, wo_b, pn_b, quantity=10)
    _stock(client, shop.material, shop.stockroom, flow_a, pn_a, 3)
    _scrap(client, shop.material, flow_b, pn_b, 1)
    # A third PN entirely stocked with its demand still open.
    pn_c = _unique("PN")
    wo_c = _create_work_order(client, [{"part_number": pn_c, "requested_quantity": 10}])
    flow_c = _release(client, shop.material, wo_c, pn_c, quantity=5)
    _stock(client, shop.material, shop.stockroom, flow_c, pn_c, 5)

    board = _board(client, shop.department_id)
    # Three undated demands received today: the demand id breaks the tie
    # — the entirely stocked PN is no sorting tier of its own.
    assert [row["part_number"] for row in board["rows"]] == [pn_a, pn_b, pn_c]
    assert board["active_part_numbers"] == 2
    assert board["active_quantity"] == 7 + 9
    assert board["stocked_quantity"] == 3 + 5
    assert board["scrapped_quantity"] == 1
    assert board["active_quantity"] == sum(row["active_quantity"] for row in board["rows"])
    assert board["stocked_quantity"] == sum(row["stocked_quantity"] for row in board["rows"])


# ---------------------------------------------------------------------------
# Row selection
# ---------------------------------------------------------------------------


def test_a_stocked_only_row_stays_while_demand_is_open_and_leaves_on_completion(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 6}])
    flow_id = _release(client, shop.material, work_order, pn, quantity=6)
    _stock(client, shop.material, shop.stockroom, flow_id, pn, 6)

    row = _row(_board(client, shop.department_id), pn)
    assert row["active_quantity"] == 0
    assert row["stocked_quantity"] == 6
    assert _locations(row) == [(shop.stockroom.area["name"], "STOCKED", None, 6)]

    # A partial allocation keeps the demand open: still a row.
    _allocate(client, pn, [(work_order.demand_id, 4)])
    row = _row(_board(client, shop.department_id), pn)
    assert row["demands"][0]["allocated_quantity"] == 4

    # Full allocation completes the Work Order: the finished PN leaves.
    _allocate(client, pn, [(work_order.demand_id, 2)])
    assert _row_or_none(_board(client, shop.department_id), pn) is None


def test_a_completed_work_order_never_supplies_the_rows_context(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 5, "job_numbers": ["18900"]}],
        number="007009",
    )
    flow_id = _release(client, shop.material, work_order, pn, quantity=5)
    # 4 pcs stocked and allocated; the fifth piece comes from a supply
    # Work Order of its own (an allocation never exceeds the available
    # stock), completing 007009 while 1 pc of it is still in Material.
    _stock(client, shop.material, shop.stockroom, flow_id, pn, 4)
    _allocate(client, pn, [(work_order.demand_id, 4)])
    supply = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 1}], received_date="2099-12-31"
    )
    supply_flow = _release(
        client, shop.material, supply, pn, quantity=1, confirm_active_quantity=True
    )
    _stock(client, shop.material, shop.stockroom, supply_flow, pn, 1)
    _allocate(client, pn, [(work_order.demand_id, 1)])

    # The completed 007009 is history: only the open supply demand is
    # the row's context, and it defines the row's dates.
    row = _row(_board(client, shop.department_id), pn)
    assert row["active_quantity"] == 1
    assert [demand["work_order_id"] for demand in row["demands"]] == [supply.id]
    assert all(demand["work_order_number"] != "007009" for demand in row["demands"])
    assert row["due_date"] is None
    assert row["received_date"] == "2099-12-31"


def _orphan_quantity(client: TestClient, shop: _Shop, pn: str, quantity: int) -> None:
    """Active quantity of ``pn`` with NO demand context: a Phase 9
    quantity addition (a FLOATING flow without a `RECEIVED`) recorded
    while the PN still had active quantity in the Area."""
    added = client.post(
        f"/api/scan-stations/{shop.material.station_id}/quantity-additions",
        json={
            "part_number": pn,
            "quantity": quantity,
            "operation_id": shop.material.operation_id,
            "reason": "found on the bench",
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert added.status_code == 201, added.text


def test_quantity_without_demand_context_keeps_a_row(client: TestClient, shop: _Shop) -> None:
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 4}])
    flow_id = _release(client, shop.material, work_order, pn, quantity=4)
    _orphan_quantity(client, shop, pn, 2)
    _stock(client, shop.material, shop.stockroom, flow_id, pn, 4)
    _allocate(client, pn, [(work_order.demand_id, 4)])

    # The Work Order is complete and the remaining active quantity has
    # no demand of its own: the row keeps a null due date and takes its
    # received date from the day the quantity entered.
    row = _row(_board(client, shop.department_id), pn)
    assert row["active_quantity"] == 2
    assert row["stocked_quantity"] == 4
    assert row["demands"] == []
    assert row["due_date"] is None
    assert row["hot_rank"] is None
    assert row["received_date"] == datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_rows_follow_the_canonical_board_order(client: TestClient, db_engine: Engine) -> None:
    shop = _Shop(client)

    def seed(
        *, due: str | None, received: str, rank: int | None = None, stocked: bool = False
    ) -> str:
        pn = _unique("PN")
        line: dict[str, Any] = {"part_number": pn, "requested_quantity": 10}
        if due is not None:
            line["due_date"] = due
        work_order = _create_work_order(client, [line], received_date=received)
        _set_priority(db_engine, work_order.demand_id, rank)
        flow_id = _release(client, shop.material, work_order, pn, quantity=5)
        if stocked:
            _stock(client, shop.material, shop.stockroom, flow_id, pn, 5)
        return pn

    undated_old = seed(due=None, received="2026-01-05")
    dated_late = seed(due="2026-12-01", received="2026-01-01")
    stocked_hot = seed(due="2026-01-02", received="2026-01-01", rank=1, stocked=True)
    hot_2 = seed(due="2026-12-31", received="2026-01-01", rank=2)
    dated_early = seed(due="2026-06-01", received="2026-03-01")
    undated_new = seed(due=None, received="2026-02-01")
    hot_1 = seed(due=None, received="2026-01-01", rank=1)
    dated_first = seed(due="2026-01-01", received="2026-01-01")
    # A row without any demand context: its Work Order completed (no
    # longer context), only found quantity remains active.
    orphan = _unique("PN")
    orphan_wo = _create_work_order(client, [{"part_number": orphan, "requested_quantity": 1}])
    orphan_flow = _release(client, shop.material, orphan_wo, orphan, quantity=1)
    _orphan_quantity(client, shop, orphan, 1)
    _stock(client, shop.material, shop.stockroom, orphan_flow, orphan, 1)
    _allocate(client, orphan, [(orphan_wo.demand_id, 1)])

    # Exactly the canonical demand ordering: within rank 1 the dated
    # demand precedes the undated one — the entirely stocked PN is no
    # tier of its own — and the row without demand context is an
    # unranked, undated row on its fallback received date (today).
    board = _board(client, shop.department_id)
    assert [row["part_number"] for row in board["rows"]] == [
        stocked_hot,
        hot_1,
        hot_2,
        dated_first,
        dated_early,
        dated_late,
        undated_old,
        undated_new,
        orphan,
    ]
    assert _row(board, hot_1)["hot_rank"] == 1
    assert _row(board, stocked_hot)["hot_rank"] == 1
    assert _row(board, dated_early)["due_date"] == "2026-06-01"
    assert _row(board, undated_old)["received_date"] == "2026-01-05"


def test_the_first_demand_in_canonical_order_defines_the_rows_dates(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    pn = _unique("PN")
    later = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 10, "due_date": "2026-12-24"}],
        received_date="2026-02-01",
    )
    earlier = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 10, "due_date": "2026-11-11"}],
        received_date="2026-03-01",
    )
    hot = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 10}], received_date="2026-04-01"
    )
    _release(client, shop.material, later, pn, quantity=3)
    _release(client, shop.material, earlier, pn, quantity=3, confirm_active_quantity=True)

    row = _row(_board(client, shop.department_id), pn)
    # Unreleased open demand is context too; the earliest due date leads.
    assert [demand["work_order_id"] for demand in row["demands"]] == [
        earlier.id,
        later.id,
        hot.id,
    ]
    assert row["due_date"] == "2026-11-11"
    assert row["received_date"] == "2026-03-01"
    assert row["hot_rank"] is None

    # A Hot rank on the undated demand moves it first and re-defines
    # the row: no due date, its own received date, rank 1.
    _set_priority(db_engine, hot.demand_id, 1)
    row = _row(_board(client, shop.department_id), pn)
    assert [demand["work_order_id"] for demand in row["demands"]][0] == hot.id
    assert row["hot_rank"] == 1
    assert row["due_date"] is None
    assert row["received_date"] == "2026-04-01"
