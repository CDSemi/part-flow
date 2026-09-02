"""Integration tests for Phase 10 — Stockroom `STOCKED` and WorkOrderAllocation.

Exercises the full request path — FastAPI routes, the Application
commands and read models, and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain. Covered
per IMPLEMENTATION_ROADMAP Phase 10, PROJECT_PROFILE §8.2 / §8.12 / §11
/ §18 and GUI_DESIGN §10 / §11.5:

- the `STOCKED` arrival at a Stockroom station: the same command
  mechanism as the transfer (explicit source, confirmed destination,
  implicit `AREA_COMPLETED`, partial quantity through the in-command
  SPLIT — never a parallel quantity mechanism, route assessment and
  deviation confirmation), the flow closing as manufacturing-complete,
  the projection replay agreeing, every refusal with zero writes,
  whole-command idempotency (replay / mismatch / cross-kind reuse) and
  the one-winner race on the flow row lock; a `STOCKED` command is not
  undoable; stocked flows accept no further production command;
- available stocked quantity derived from history (stocked minus
  active allocation) — never a stored counter — and the §11
  reconciliation `introduced = active + stocked + scrapped`;
- the suggestion in the canonical demand ordering (Hot rank, dated
  earliest first, undated by received date, the deterministic
  tie-breaker), proposing only up to each line's remaining shortage
  and never more than what is available;
- the confirmation: the operator's adjusted lines (override flagged),
  the two invariants (a line never beyond its shortage, the total
  never beyond the available stocked quantity) refused with zero
  writes, the `allocated_quantity` projection agreeing with the rows,
  replay / mismatch idempotency, and the concurrency race of two
  confirmations of one PN with exactly one winner;
- the reversal (the auditable adjustment): once per allocation, also
  under a race (the UNIQUE backstop), never a reversal of a reversal,
  reopening a completed Work Order;
- Work Order completion derived from allocation: `completed_at` set by
  the completing allocation and cleared by a reversal, `COMPLETED`
  status, the active list excluding completed Work Orders while the
  exact number resolution still finds them, the read-only history
  (edits, removal, release refused), the demand-edit floor at the
  allocated quantity, and the completed history endpoint (search,
  due outcome, keyset paging, total); the projection replays of
  `allocated_quantity` and `completed_at`.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations; the module database is dropped afterwards.
"""

import datetime
import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import allocations, projections, work_orders
from app.core.config import Settings, get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_stockroom_allocation_api"
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
        json={"prefix": "CD-", "digits": 4},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _create_area(client: TestClient, **overrides: Any) -> dict[str, Any]:
    department = client.post("/api/departments", json={"name": _unique("DEPT")})
    assert department.status_code == 201, department.text
    response = client.post(
        "/api/areas",
        json={"department_id": department.json()["id"], "name": _unique("AREA"), **overrides},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_operation(client: TestClient, area_id: int) -> int:
    response = client.post("/api/operations", json={"area_id": area_id, "code": _unique("OP")})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _create_station(client: TestClient, area_id: int) -> str:
    response = client.post(
        "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area_id}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["station_id"])


def _create_machine(client: TestClient, area_id: int) -> int:
    response = client.post("/api/machines", json={"area_id": area_id, "name": _unique("Lathe")})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


class _Cell:
    """An Area with Operations, one Scan Station and ``machine_count`` Machines."""

    def __init__(
        self,
        client: TestClient,
        *,
        machine_count: int = 0,
        operation_count: int = 1,
        is_terminal: bool = False,
    ) -> None:
        self.area = _create_area(client, is_terminal=is_terminal)
        self.area_id = int(self.area["id"])
        self.operation_ids = [
            _create_operation(client, self.area_id) for _ in range(operation_count)
        ]
        self.station_id = _create_station(client, self.area_id)
        self.machine_ids = [_create_machine(client, self.area_id) for _ in range(machine_count)]

    @property
    def operation_id(self) -> int:
        return self.operation_ids[0]

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


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
    due_date: str | None = None,
) -> _WorkOrder:
    payload: dict[str, Any] = {"lines": lines}
    if number is not None:
        payload["work_order_number"] = number
    if received_date is not None:
        payload["received_date"] = received_date
    if due_date is not None:
        payload["due_date"] = due_date
    response = client.post("/api/work-orders", json=payload)
    assert response.status_code == 201, response.text
    return _WorkOrder(response.json())


def _set_priority(engine: Engine, demand_id: int, rank: int | None) -> None:
    """Hot rank is Phase 12's to manage; the ordering it feeds is Phase 10's."""
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


def _released_flow(
    client: TestClient, cell: _Cell, *, quantity: int = 10, requested: int = 500
) -> tuple[int, str, _WorkOrder]:
    """A fresh PN on a fresh Work Order, ``quantity`` released into ``cell``."""
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": requested}])
    return _release(client, cell, work_order, pn, quantity=quantity), pn, work_order


def _supply(
    client: TestClient, material: _Cell, stockroom: _Cell, pn: str, quantity: int
) -> _WorkOrder:
    """Put ``quantity`` of ``pn`` into stock through its own supply Work
    Order (a release is capped at the demand's remaining quantity, so the
    stocked quantity needs a demand of its own). Received far in the
    future and undated, the supply demand sorts LAST in the canonical
    ordering and never disturbs the ordering assertions."""
    supply = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": quantity}], received_date="2099-12-31"
    )
    flow_id = _release(
        client, material, supply, pn, quantity=quantity, confirm_active_quantity=True
    )
    assert _stock(client, material, stockroom, flow_id, pn, quantity).status_code == 201
    return supply


def _create_route_template(engine: Engine, steps: list[tuple[int, int | None]]) -> int:
    with Session(engine) as session:
        template = models.RouteTemplate(name=_unique("ROUTE"))
        session.add(template)
        session.flush()
        for index, (area_id, operation_id) in enumerate(steps):
            session.add(
                models.RouteStep(
                    route_template_id=template.id,
                    sequence=(index + 1) * 10,
                    area_id=area_id,
                    operation_id=operation_id,
                )
            )
        session.commit()
        return int(template.id)


def _arrival_payload(
    source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: Any, **kw: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return payload


def _transfer(
    client: TestClient,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: Any,
    **kw: Any,
) -> Any:
    return client.post(
        f"/api/scan-stations/{target.station_id}/transfers",
        json=_arrival_payload(source, target, flow_id, pn, quantity, **kw),
    )


def _stock(
    client: TestClient,
    source: _Cell,
    stockroom: _Cell,
    flow_id: int,
    pn: str,
    quantity: Any,
    **kw: Any,
) -> Any:
    return client.post(
        f"/api/scan-stations/{stockroom.station_id}/stockings",
        json=_arrival_payload(source, stockroom, flow_id, pn, quantity, **kw),
    )


def _assign(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/machine-assignments",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "machine_id": cell.machine_id,
            "quantity": quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _scrap(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/scraps",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "quantity": quantity,
            "reason": "damaged",
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _resolve(client: TestClient, cell: _Cell, pn: str) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/scans/resolve", json={"part_number": pn}
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _suggest(client: TestClient, pn: str, quantity: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"part_number": pn}
    if quantity is not None:
        params["quantity"] = quantity
    response = client.get("/api/allocations/suggestion", params=params)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _allocate(
    client: TestClient,
    pn: str,
    lines: list[tuple[int, int]],
    *,
    station_id: str | None = None,
    **kw: Any,
) -> Any:
    """POST an allocation; the explicit allocation quantity defaults to
    the sum of the lines (override or remove it through ``kw``)."""
    payload: dict[str, Any] = {
        "part_number": pn,
        "allocation_quantity": sum(qty for _, qty in lines),
        "lines": [{"work_order_demand_id": demand_id, "quantity": qty} for demand_id, qty in lines],
        "device_event_id": str(uuid.uuid4()),
    }
    if station_id is not None:
        payload["station_id"] = station_id
    payload.update(kw)
    payload = {key: value for key, value in payload.items() if value is not _OMIT}
    return client.post("/api/allocations", json=payload)


#: Sentinel: drop this key from a request payload.
_OMIT = object()


def _reverse(client: TestClient, allocation_id: int, **kw: Any) -> Any:
    payload: dict[str, Any] = {"reason": "wrong Work Order", "device_event_id": str(uuid.uuid4())}
    payload.update(kw)
    return client.post(f"/api/allocations/{allocation_id}/reversals", json=payload)


def _work_order(client: TestClient, work_order_id: int) -> dict[str, Any]:
    response = client.get(f"/api/work-orders/{work_order_id}")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _active_ids(client: TestClient, search: str | None = None) -> set[int]:
    params = {"search": search} if search is not None else {}
    response = client.get("/api/work-orders", params=params)
    assert response.status_code == 200, response.text
    return {int(row["id"]) for row in response.json()}


def _completed(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/work-orders/completed", params=params)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


# ---------------------------------------------------------------------------
# Database-level assertions
# ---------------------------------------------------------------------------


def _flow_row(engine: Engine, flow_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.QuantityFlow.__table__).where(
                models.QuantityFlow.__table__.c.id == flow_id
            )
        ).one()


def _movements(engine: Engine, flow_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.PartMovement.__table__)
                .where(models.PartMovement.__table__.c.quantity_flow_id == flow_id)
                .order_by(models.PartMovement.__table__.c.id)
            )
        )


def _allocation_rows(engine: Engine, pn: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.WorkOrderAllocation.__table__)
                .where(models.WorkOrderAllocation.__table__.c.part_number == pn)
                .order_by(models.WorkOrderAllocation.__table__.c.id)
            )
        )


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            model.__tablename__: connection.execute(
                sa.select(sa.func.count()).select_from(model.__table__)
            ).scalar_one()
            for model in (
                models.QuantityFlow,
                models.PartMovement,
                models.WorkOrderAllocation,
                models.QuantityFlowLineage,
            )
        }


def _demand_row(engine: Engine, demand_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.WorkOrderDemand.__table__).where(
                models.WorkOrderDemand.__table__.c.id == demand_id
            )
        ).one()


def _work_order_row(engine: Engine, work_order_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.WorkOrder.__table__).where(
                models.WorkOrder.__table__.c.id == work_order_id
            )
        ).one()


def _assert_projections_match_replay(engine: Engine) -> None:
    """Current positions, allocated quantities and done dates all rebuild from history."""
    with Session(engine) as session:
        rebuilt = projections.rebuild_current_positions(session)
        stored = {
            int(row.id): (int(row.current_area_id), row.current_machine_id)
            for row in session.execute(
                sa.select(models.QuantityFlow.__table__).where(
                    models.QuantityFlow.__table__.c.status == "ACTIVE"
                )
            )
        }
        assert set(rebuilt) == set(stored)
        for flow_id, position in rebuilt.items():
            assert (position.area_id, position.machine_id) == stored[flow_id], flow_id
        allocated = allocations.rebuild_allocated_quantities(session)
        for demand_id, quantity in session.execute(
            sa.select(models.WorkOrderDemand.id, models.WorkOrderDemand.allocated_quantity)
        ):
            assert allocated.get(int(demand_id), 0) == int(quantity), demand_id
        completed = allocations.rebuild_completed_at(session)
        for work_order_id, completed_at in session.execute(
            sa.select(models.WorkOrder.id, models.WorkOrder.completed_at)
        ):
            assert completed.get(int(work_order_id)) == completed_at, work_order_id


# ---------------------------------------------------------------------------
# STOCKED — the Stockroom arrival
# ---------------------------------------------------------------------------


def test_stocking_closes_the_flow_and_makes_the_quantity_available(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)  # no Machines: directly processing quantity
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, work_order = _released_flow(client, material, quantity=10, requested=10)

    resolved = _resolve(client, stockroom, pn)
    assert resolved["stock_available"] is True
    assert resolved["transfer_blocked_reason"] is not None
    assert resolved["stocked_quantity"] == 0 and resolved["available_stocked_quantity"] == 0

    response = _stock(client, material, stockroom, flow_id, pn, 10)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_type"] == "STOCKED"
    assert body["from_area_id"] == material.area_id and body["to_area_id"] == stockroom.area_id
    assert body["operation_id"] == stockroom.operation_id
    # Directly processing quantity completes implicitly — one command.
    assert body["completed_movement_id"] is not None and body["completed_machine_id"] is None
    rows = _movements(db_engine, flow_id)
    assert [row.movement_type for row in rows] == ["RECEIVED", "AREA_COMPLETED", "STOCKED"]
    assert [row.command_sequence for row in rows[1:]] == [1, 2]
    assert rows[1].device_event_id == rows[2].device_event_id == body["device_event_id"]
    assert rows[2].metadata["command"] == {"kind": "STOCK", "size": 2}

    flow = _flow_row(db_engine, flow_id)
    assert flow.status == "STOCKED" and flow.closed_at is not None
    assert flow.current_area_id == stockroom.area_id and flow.current_machine_id is None
    _assert_projections_match_replay(db_engine)

    # Never inventory again, anywhere; the PN's stock is derived.
    inventory = client.get(f"/api/areas/{stockroom.area_id}/inventory").json()
    assert inventory["lines"] == []
    resolved = _resolve(client, material, pn)
    assert resolved["resolution"] == "NO_TRANSFERABLE_QUANTITY"
    assert resolved["stocked_quantity"] == 10 and resolved["available_stocked_quantity"] == 10
    # The Work Order stays untouched by the stocking itself.
    assert _work_order(client, work_order.id)["status"] == "RELEASED"
    assert _work_order(client, work_order.id)["completed_at"] is None


def test_stocking_from_a_machine_completes_and_releases_the_machine(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, lathe, quantity=8)
    assert _assign(client, lathe, flow_id, pn, 8).status_code == 201
    response = _stock(client, lathe, stockroom, flow_id, pn, 8)
    assert response.status_code == 201, response.text
    assert response.json()["completed_machine_id"] == lathe.machine_id
    machine = client.get(f"/api/machines/{lathe.machine_id}").json()
    assert machine["assigned_quantity"] == 0 and machine["operational_state"] == "IDLE"
    assert _flow_row(db_engine, flow_id).status == "STOCKED"


def test_partial_stocking_uses_the_split_mechanism(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)  # queued: STOCKED alone after the split
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, material, quantity=10)
    response = _stock(client, material, stockroom, flow_id, pn, 4)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_quantity_flow_id"] == flow_id
    assert body["remainder_quantity"] == 6 and body["quantity"] == 4
    selected = body["quantity_flow_id"]
    remainder = body["remainder_quantity_flow_id"]
    assert _flow_row(db_engine, flow_id).status == "SPLIT"
    assert _flow_row(db_engine, selected).status == "STOCKED"
    remainder_row = _flow_row(db_engine, remainder)
    assert remainder_row.status == "ACTIVE" and remainder_row.current_area_id == material.area_id
    # SPLIT ×3 then STOCKED, one device_event_id, sequences 1..4.
    command = [
        row
        for row in _movements(db_engine, flow_id)
        + _movements(db_engine, selected)
        + _movements(db_engine, remainder)
        if row.device_event_id == body["device_event_id"]
    ]
    assert sorted(row.command_sequence for row in command) == [1, 2, 3, 4]
    assert sorted(row.movement_type for row in command) == ["SPLIT", "SPLIT", "SPLIT", "STOCKED"]
    resolved = _resolve(client, material, pn)
    assert resolved["stocked_quantity"] == 4
    assert [line["quantity"] for line in resolved["in_area"]] == [6]
    _assert_projections_match_replay(db_engine)


def test_planned_route_ending_at_the_stockroom_is_on_route(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    other = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    template = _create_route_template(
        db_engine, [(material.area_id, None), (stockroom.area_id, stockroom.operation_id)]
    )
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    flow_id = _release(client, material, work_order, pn, quantity=5, route_template_id=template)
    # A different terminal Area than the planned step is a deviation:
    # refused until confirmed with a reason.
    other_stockroom = _Cell(client, is_terminal=True)
    refused = _stock(client, material, other_stockroom, flow_id, pn, 5)
    assert refused.status_code == 409 and refused.json()["confirmation_required"] is True
    assert _flow_row(db_engine, flow_id).status == "ACTIVE"
    on_route = _stock(client, material, stockroom, flow_id, pn, 5)
    assert on_route.status_code == 201, on_route.text
    assert on_route.json()["assigned_route_step_id"] is not None
    assert on_route.json()["route_deviation"] is None
    assert _flow_row(db_engine, flow_id).status == "STOCKED"
    assert other.area_id != stockroom.area_id


@pytest.mark.parametrize(
    "case",
    ["not_terminal", "transfer_into_terminal", "repair", "exceeds", "stale_source", "inactive"],
)
def test_refusals_write_nothing(client: TestClient, db_engine: Engine, case: str) -> None:
    material = _Cell(client, machine_count=1)
    other = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, material, quantity=10)
    before = _counts(db_engine)
    if case == "not_terminal":
        response = _stock(client, material, other, flow_id, pn, 10)
        assert response.status_code == 409 and "not a terminal Area" in response.json()["detail"]
    elif case == "transfer_into_terminal":
        response = _transfer(client, material, stockroom, flow_id, pn, 10)
        assert response.status_code == 409 and "terminal" in response.json()["detail"]
    elif case == "repair":
        response = client.post(
            f"/api/scan-stations/{stockroom.station_id}/stockings",
            json=_arrival_payload(material, stockroom, flow_id, pn, 10, repair=True),
        )
        assert response.status_code == 422
    elif case == "exceeds":
        response = _stock(client, material, stockroom, flow_id, pn, 11)
        assert response.status_code == 422 and "exceeds" in response.json()["detail"]
    elif case == "stale_source":
        response = _stock(client, other, stockroom, flow_id, pn, 10)
        assert (
            response.status_code == 409
            and "no longer in the selected source" in (response.json()["detail"])
        )
    else:
        assert (
            client.patch(f"/api/areas/{stockroom.area_id}", json={"is_active": False}).status_code
            == 200
        )
        response = _stock(client, material, stockroom, flow_id, pn, 10)
        assert response.status_code == 409
    assert _counts(db_engine) == before
    assert _flow_row(db_engine, flow_id).status == "ACTIVE"


def test_stocking_is_idempotent_per_command(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, material, quantity=10)
    payload = _arrival_payload(material, stockroom, flow_id, pn, 10)
    first = client.post(f"/api/scan-stations/{stockroom.station_id}/stockings", json=payload)
    assert first.status_code == 201, first.text
    other = _Cell(client, machine_count=1)
    flow2, pn2, _ = _released_flow(client, material, quantity=5)
    before = _counts(db_engine)
    replay = client.post(f"/api/scan-stations/{stockroom.station_id}/stockings", json=payload)
    assert replay.status_code == 200 and replay.json() == first.json()
    # A different intent under the same id: conflict, nothing written.
    mismatch = client.post(
        f"/api/scan-stations/{stockroom.station_id}/stockings", json={**payload, "quantity": 9}
    )
    assert mismatch.status_code == 409 and "different" in mismatch.json()["detail"]
    # The transfer id reused for a stocking (and vice versa) is another
    # kind of intent: a transfer into a normal Area cannot replay it.
    reuse = _transfer(
        client, material, other, flow2, pn2, 5, device_event_id=payload["device_event_id"]
    )
    assert reuse.status_code == 409
    assert _counts(db_engine) == before


def test_two_stockings_of_one_flow_have_exactly_one_winner(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, material, quantity=10)
    results: dict[int, Any] = {}

    def _attempt(slot: int) -> None:
        results[slot] = _stock(client, material, stockroom, flow_id, pn, 10)

    threads = [threading.Thread(target=_attempt, args=(slot,)) for slot in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(response.status_code for response in results.values()) == [201, 409]
    stocked = [row for row in _movements(db_engine, flow_id) if row.movement_type == "STOCKED"]
    assert len(stocked) == 1
    assert _resolve(client, material, pn)["stocked_quantity"] == 10
    _assert_projections_match_replay(db_engine)


def test_stocked_quantity_is_final(client: TestClient, db_engine: Engine) -> None:
    """No Undo of a STOCKED command (PROJECT_PROFILE §32 open decision 1)
    and no further production command on a stocked flow."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, material, quantity=10)
    stocked = _stock(client, material, stockroom, flow_id, pn, 10)
    assert stocked.status_code == 201
    event_id = stocked.json()["device_event_id"]
    preview = client.get(f"/api/scan-stations/{stockroom.station_id}/undo-preview/{event_id}")
    assert preview.status_code == 200 and preview.json()["eligible"] is False
    assert "allocat" in preview.json()["ineligible_reason"]
    before = _counts(db_engine)
    undo = client.post(
        f"/api/scan-stations/{stockroom.station_id}/undos",
        json={
            "part_number": pn,
            "reverses_device_event_id": event_id,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert undo.status_code == 409 and "Returning stocked quantity" in undo.json()["detail"]
    # Transfer / scrap / a second stocking of the stocked flow: stale.
    assert _transfer(client, stockroom, material, flow_id, pn, 10).status_code == 409
    assert _scrap(client, stockroom, flow_id, pn, 10).status_code == 409
    assert _stock(client, material, stockroom, flow_id, pn, 10).status_code == 409
    assert _counts(db_engine) == before


def test_reconciliation_introduced_equals_active_plus_stocked_plus_scrapped(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    flow_id, pn, _ = _released_flow(client, material, quantity=20)
    assert _scrap(client, material, flow_id, pn, 3).status_code == 201
    remainder = next(line["quantity_flow_id"] for line in _resolve(client, material, pn)["in_area"])
    assert _stock(client, material, stockroom, remainder, pn, 7).status_code == 201
    resolved = _resolve(client, material, pn)
    active = sum(line["quantity"] for line in resolved["in_area"])
    assert active == 10
    assert resolved["stocked_quantity"] == 7 and resolved["scrapped_quantity"] == 3
    assert active + resolved["stocked_quantity"] + resolved["scrapped_quantity"] == 20


# ---------------------------------------------------------------------------
# The suggestion — canonical demand ordering
# ---------------------------------------------------------------------------


def test_suggestion_follows_the_canonical_demand_ordering(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    # Six demand lines of one PN across Work Orders, deliberately created
    # in an order different from the canonical one.
    undated_old = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 5}], received_date="2026-01-05"
    )
    dated_late = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 5, "due_date": "2026-12-01"}],
        received_date="2026-02-01",
    )
    hot_two = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 5}], received_date="2026-03-01"
    )
    undated_new = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 5}], received_date="2026-01-06"
    )
    dated_early = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 5, "due_date": "2026-10-01"}],
        received_date="2026-02-02",
    )
    hot_one = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 5}], received_date="2026-04-01"
    )
    _set_priority(db_engine, hot_two.demand_id, 2)
    _set_priority(db_engine, hot_one.demand_id, 1)
    # Stock 12 pcs through a supply Work Order (sorting last).
    supply = _supply(client, material, stockroom, pn, 12)

    suggestion = _suggest(client, pn, 12)
    assert suggestion["available_stocked_quantity"] == 12 and suggestion["quantity"] == 12
    order = [line["work_order_demand_id"] for line in suggestion["lines"]]
    assert order == [
        hot_one.demand_id,  # Hot rank #1
        hot_two.demand_id,  # Hot rank #2
        dated_early.demand_id,  # dated, earliest due date first
        dated_late.demand_id,
        undated_old.demand_id,  # undated: oldest received date first
        undated_new.demand_id,
        supply.demand_id,
    ]
    assert [line["proposed_quantity"] for line in suggestion["lines"]] == [5, 5, 2, 0, 0, 0, 0]
    assert suggestion["proposed_total"] == 12 and suggestion["unallocated_quantity"] == 0
    line = suggestion["lines"][0]
    assert line["requested_quantity"] == 5 and line["previously_allocated_quantity"] == 0
    assert line["remaining_shortage"] == 5 and line["work_order_id"] == hot_one.id

    # Quantity is capped at what is available; a smaller quantity fills
    # in order; the shortage shrinks as allocation is recorded.
    assert _suggest(client, pn, 99)["quantity"] == 12
    assert [line["proposed_quantity"] for line in _suggest(client, pn, 3)["lines"]][:2] == [3, 0]
    # Nothing outstanding beyond the demand: the surplus stays in stock.
    assert _suggest(client, pn, 12)["unallocated_quantity"] == 0
    assert _allocate(client, pn, [(hot_one.demand_id, 5)]).status_code == 201
    after = _suggest(client, pn)
    assert after["available_stocked_quantity"] == 7 and after["quantity"] == 7
    assert [line["work_order_demand_id"] for line in after["lines"]][0] == hot_two.demand_id
    assert all(line["work_order_demand_id"] != hot_one.demand_id for line in after["lines"])


def test_suggestion_tie_breaker_is_deterministic(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    first = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 4, "due_date": "2026-10-01"}],
        received_date="2026-01-01",
    )
    second = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 4, "due_date": "2026-10-01"}],
        received_date="2026-01-01",
    )
    supply = _supply(client, material, stockroom, pn, 6)
    for _ in range(3):
        suggestion = _suggest(client, pn)
        assert [line["work_order_demand_id"] for line in suggestion["lines"]] == [
            first.demand_id,
            second.demand_id,
            supply.demand_id,
        ]
        assert [line["proposed_quantity"] for line in suggestion["lines"]] == [4, 2, 0]


def test_received_date_orders_undated_demand_only(client: TestClient, db_engine: Engine) -> None:
    """PROJECT_PROFILE §18: the parent Work Order's received date orders
    UNDATED demand. Two dated lines of one priority sharing a due date
    resolve by the deterministic tie-breaker (creation order), never by
    which Work Order was received first — here the later-created line
    belongs to the EARLIER-received Work Order and still sorts second."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    dated_late_receipt = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 3, "due_date": "2026-10-01"}],
        received_date="2026-05-01",
    )
    dated_early_receipt = _create_work_order(
        client,
        [{"part_number": pn, "requested_quantity": 3, "due_date": "2026-10-01"}],
        received_date="2026-01-01",
    )
    # Undated lines DO order by received date, oldest first — created in
    # the opposite order to prove the date decides, not the id.
    undated_new = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 3}], received_date="2026-03-01"
    )
    undated_old = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 3}], received_date="2026-02-01"
    )
    supply = _supply(client, material, stockroom, pn, 4)
    suggestion = _suggest(client, pn)
    assert [line["work_order_demand_id"] for line in suggestion["lines"]] == [
        dated_late_receipt.demand_id,  # same due date: tie-breaker, not received date
        dated_early_receipt.demand_id,
        undated_old.demand_id,  # undated: received date, oldest first
        undated_new.demand_id,
        supply.demand_id,
    ]
    assert [line["proposed_quantity"] for line in suggestion["lines"]] == [3, 1, 0, 0, 0]
    # A Hot rank on the later-created dated line still wins outright.
    _set_priority(db_engine, dated_early_receipt.demand_id, 1)
    assert [line["work_order_demand_id"] for line in _suggest(client, pn)["lines"]][:2] == [
        dated_early_receipt.demand_id,
        dated_late_receipt.demand_id,
    ]


# ---------------------------------------------------------------------------
# The confirmation
# ---------------------------------------------------------------------------


def test_confirmation_records_the_allocation_and_derives_completion(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    other_pn = _unique("PN")
    work_order = _create_work_order(
        client,
        [
            {"part_number": pn, "requested_quantity": 6},
            {"part_number": other_pn, "requested_quantity": 2},
        ],
        number=_unique("WO"),
    )
    flow_id = _release(client, material, work_order, pn, quantity=6)
    assert _stock(client, material, stockroom, flow_id, pn, 6).status_code == 201

    response = _allocate(client, pn, [(work_order.demand_id, 6)], station_id=stockroom.station_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "ALLOCATE" and body["completed_work_order_ids"] == []
    (row,) = body["rows"]
    assert row["quantity"] == 6 and row["source"] == "STOCKROOM"
    assert row["station_id"] == stockroom.station_id and row["is_manual_override"] is False
    assert row["work_order_id"] == work_order.id and row["reverses_allocation_id"] is None
    detail = _work_order(client, work_order.id)
    assert detail["status"] == "OPEN" and detail["completed_at"] is None
    assert detail["demands"][0]["allocated_quantity"] == 6
    assert _demand_row(db_engine, work_order.demand_id).allocated_quantity == 6
    assert _suggest(client, pn)["available_stocked_quantity"] == 0
    # Allocation never touches Movement history.
    assert [row.movement_type for row in _movements(db_engine, flow_id)] == ["RECEIVED", "STOCKED"]

    # The second line completes the Work Order: derived, with the done
    # date set by the completing allocation.
    other_flow = _release(
        client, material, work_order, other_pn, demand_id=work_order.demand_ids[1], quantity=2
    )
    assert _stock(client, material, stockroom, other_flow, other_pn, 2).status_code == 201
    completing = _allocate(client, other_pn, [(work_order.demand_ids[1], 2)])
    assert completing.status_code == 201, completing.text
    assert completing.json()["completed_work_order_ids"] == [work_order.id]
    assert completing.json()["rows"][0]["source"] == "MANAGEMENT"
    detail = _work_order(client, work_order.id)
    assert detail["status"] == "COMPLETED" and detail["completed_at"] is not None
    assert work_order.id not in _active_ids(client)
    # The exact number resolution still finds it — never duplicated.
    by_number = client.get("/api/work-orders", params={"number": work_order.number}).json()
    assert [row["id"] for row in by_number] == [work_order.id]
    assert by_number[0]["status"] == "COMPLETED"
    listed = _completed(client, search=work_order.number)
    assert [row["id"] for row in listed["work_orders"]] == [work_order.id]
    _assert_projections_match_replay(db_engine)


def test_operator_adjustment_is_flagged_as_an_override(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    first = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 5}], received_date="2026-01-01"
    )
    second = _create_work_order(
        client, [{"part_number": pn, "requested_quantity": 5}], received_date="2026-02-01"
    )
    _supply(client, material, stockroom, pn, 6)
    # Suggested: 5 to the older, 1 to the newer. The operator decides 3 / 3.
    response = _allocate(
        client, pn, [(first.demand_id, 3), (second.demand_id, 3)], station_id=stockroom.station_id
    )
    assert response.status_code == 201, response.text
    flags = {
        row["work_order_demand_id"]: row["is_manual_override"] for row in response.json()["rows"]
    }
    assert flags == {first.demand_id: True, second.demand_id: True}
    assert [row["command_sequence"] for row in response.json()["rows"]] == [1, 2]
    # Confirming exactly the suggestion carries no override flag.
    _supply(client, material, stockroom, pn, 4)
    suggestion = _suggest(client, pn, 4)
    assert [line["proposed_quantity"] for line in suggestion["lines"]][:2] == [2, 2]
    exact = _allocate(client, pn, [(first.demand_id, 2), (second.demand_id, 2)])
    assert exact.status_code == 201
    assert all(row["is_manual_override"] is False for row in exact.json()["rows"])
    _assert_projections_match_replay(db_engine)


@pytest.mark.parametrize(
    "case", ["beyond_shortage", "beyond_available", "wrong_pn", "duplicate", "zero", "station"]
)
def test_confirmation_refusals_write_nothing(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    other = _create_work_order(client, [{"part_number": _unique("PN"), "requested_quantity": 5}])
    _supply(client, material, stockroom, pn, 8)
    before = _counts(db_engine)
    if case == "beyond_shortage":
        response = _allocate(client, pn, [(work_order.demand_id, 6)])
        assert (
            response.status_code == 409
            and "never exceeds the requested" in response.json()["detail"]
        )
    elif case == "beyond_available":
        # 8 stocked: 4 to this demand, 4 to another — nothing is left for
        # the remaining 1 pc of shortage.
        assert _allocate(client, pn, [(work_order.demand_id, 4)]).status_code == 201
        second = _create_work_order(client, [{"part_number": pn, "requested_quantity": 9}])
        assert _allocate(client, pn, [(second.demand_id, 4)]).status_code == 201
        before = _counts(db_engine)
        response = _allocate(client, pn, [(work_order.demand_id, 1)])
        assert response.status_code == 409 and "available in stock" in response.json()["detail"]
    elif case == "wrong_pn":
        response = _allocate(client, pn, [(other.demand_id, 1)])
        assert response.status_code == 422 and "own PN" in response.json()["detail"]
    elif case == "duplicate":
        response = _allocate(client, pn, [(work_order.demand_id, 1), (work_order.demand_id, 1)])
        assert response.status_code == 422
    elif case == "zero":
        response = _allocate(client, pn, [(work_order.demand_id, 0)])
        assert response.status_code == 422
        assert _allocate(client, pn, []).status_code == 422
    else:
        response = _allocate(
            client, pn, [(work_order.demand_id, 1)], station_id=material.station_id
        )
        assert response.status_code == 409 and "terminal" in response.json()["detail"]
        assert (
            _allocate(client, pn, [(work_order.demand_id, 1)], station_id="NOPE").status_code == 404
        )
    assert _counts(db_engine) == before
    _assert_projections_match_replay(db_engine)


@pytest.mark.parametrize("case", ["missing", "too_high", "too_low", "zero", "negative", "bool"])
def test_confirmation_requires_the_explicit_allocation_quantity(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    """§18: the total active allocation must equal the quantity being
    allocated — the command names it explicitly and the lines must add
    up to exactly it; a missing or disagreeing quantity is refused with
    nothing written."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 8}])
    _supply(client, material, stockroom, pn, 8)
    before = _counts(db_engine)
    quantity: Any = {
        "missing": _OMIT,
        "too_high": 6,
        "too_low": 4,
        "zero": 0,
        "negative": -5,
        "bool": True,
    }[case]
    response = _allocate(client, pn, [(work_order.demand_id, 5)], allocation_quantity=quantity)
    assert response.status_code == 422, response.text
    if case in ("too_high", "too_low"):
        assert "must equal the quantity being allocated" in response.json()["detail"]
    assert _counts(db_engine) == before
    assert _demand_row(db_engine, work_order.demand_id).allocated_quantity == 0
    # The same lines with the agreeing quantity are accepted.
    assert _allocate(client, pn, [(work_order.demand_id, 5)]).status_code == 201


def test_stale_available_stock_is_refused_at_confirmation(
    client: TestClient, db_engine: Engine
) -> None:
    """The dialog was opened on 10 pcs available; another confirmation
    took 4 meanwhile. Confirming the stale 10 — even with lines that add
    up to it — is refused with nothing written; the current figure
    (6) confirms."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    first = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    second = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    _supply(client, material, stockroom, pn, 10)
    stale = _suggest(client, pn, 10)
    assert stale["available_stocked_quantity"] == 10
    assert _allocate(client, pn, [(second.demand_id, 4)]).status_code == 201
    before = _counts(db_engine)
    response = _allocate(client, pn, [(first.demand_id, 10)], station_id=stockroom.station_id)
    assert response.status_code == 409, response.text
    assert "changed since the allocation was prepared" in response.json()["detail"]
    assert _counts(db_engine) == before
    assert _demand_row(db_engine, first.demand_id).allocated_quantity == 0
    fresh = _suggest(client, pn)
    assert fresh["available_stocked_quantity"] == 6 and fresh["quantity"] == 6
    accepted = _allocate(client, pn, [(first.demand_id, 6)], station_id=stockroom.station_id)
    assert accepted.status_code == 201 and accepted.json()["allocation_quantity"] == 6
    _assert_projections_match_replay(db_engine)


def test_confirmation_is_idempotent_per_command(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    flow_id = _release(client, material, work_order, pn, quantity=5)
    assert _stock(client, material, stockroom, flow_id, pn, 5).status_code == 201
    event_id = str(uuid.uuid4())
    first = _allocate(client, pn, [(work_order.demand_id, 5)], device_event_id=event_id)
    assert first.status_code == 201, first.text
    before = _counts(db_engine)
    replay = _allocate(client, pn, [(work_order.demand_id, 5)], device_event_id=event_id)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert replay.json()["completed_work_order_ids"] == [work_order.id]
    mismatch = _allocate(client, pn, [(work_order.demand_id, 4)], device_event_id=event_id)
    assert mismatch.status_code == 409 and "different allocation" in mismatch.json()["detail"]
    assert replay.json()["allocation_quantity"] == 5
    # The reversal command never replays under an allocation's id.
    assert (
        _reverse(
            client, first.json()["rows"][0]["allocation_id"], device_event_id=event_id
        ).status_code
        == 409
    )
    assert _counts(db_engine) == before
    assert _demand_row(db_engine, work_order.demand_id).allocated_quantity == 5


def test_two_confirmations_of_one_pn_never_exceed_the_available_quantity(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two Stockroom stations confirm the same 10 stocked pcs at once,
    each for its own Work Order: the per-PN lock serializes them — the
    first holds the lock while it judges the available quantity, the
    second BLOCKS on that lock (never reading the same stale snapshot),
    then is refused against the then-available quantity — and the total
    active allocation never exceeds what was stocked."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    first = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    second = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    _supply(client, material, stockroom, pn, 10)

    real_position = allocations.stock_position_of
    inside = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def paused_position(session: Session, part_number: str) -> Any:
        result = real_position(session, part_number)
        if part_number == pn:
            calls.append(result.available_stocked_quantity)
            if len(calls) == 1:
                inside.set()
                assert release.wait(timeout=20), "test deadlock: never released"
        return result

    monkeypatch.setattr(allocations, "stock_position_of", paused_position)
    results: dict[str, Any] = {}
    winner = threading.Thread(
        target=lambda: results.update(first=_allocate(client, pn, [(first.demand_id, 10)]))
    )
    winner.start()
    assert inside.wait(timeout=20)
    loser = threading.Thread(
        target=lambda: results.update(second=_allocate(client, pn, [(second.demand_id, 10)]))
    )
    loser.start()
    loser.join(timeout=1.0)
    # The second confirmation is still waiting on the PN lock — it has
    # not judged the quantity while the first holds its snapshot.
    assert loser.is_alive() and calls == [10]
    release.set()
    winner.join(timeout=30)
    loser.join(timeout=30)
    assert results["first"].status_code == 201, results["first"].text
    assert results["second"].status_code == 409, results["second"].text
    assert "available in stock" in results["second"].json()["detail"]
    # The loser judged AFTER the winner committed: 0 available.
    assert calls == [10, 0]
    with Session(db_engine) as session:
        assert allocations.active_allocated_quantity_of(session, pn) == 10
        assert allocations.stock_position_of(session, pn).available_stocked_quantity == 0
    assert len(_allocation_rows(db_engine, pn)) == 1
    _assert_projections_match_replay(db_engine)


def test_allocation_and_stocking_of_one_pn_interleave_safely(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An allocation judged while a second stocking of the same PN is in
    flight sees at most the committed stock — never more — so it can
    never allocate quantity that is not (yet) stocked; the stocking
    commits independently and the quantity becomes available after."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 20}])
    first_flow = _release(client, material, work_order, pn, quantity=10)
    second_flow = _release(
        client, material, work_order, pn, quantity=10, confirm_active_quantity=True
    )
    assert _stock(client, material, stockroom, first_flow, pn, 10).status_code == 201

    real_position = allocations.stock_position_of
    inside = threading.Event()
    release = threading.Event()

    def paused_position(session: Session, part_number: str) -> Any:
        result = real_position(session, part_number)
        if part_number == pn and not inside.is_set():
            inside.set()
            assert release.wait(timeout=20)
        return result

    monkeypatch.setattr(allocations, "stock_position_of", paused_position)
    results: dict[str, Any] = {}
    allocating = threading.Thread(
        target=lambda: results.update(over=_allocate(client, pn, [(work_order.demand_id, 20)]))
    )
    allocating.start()
    assert inside.wait(timeout=20)
    # The second stocking commits while the allocation holds its snapshot.
    results["stock"] = _stock(client, material, stockroom, second_flow, pn, 10)
    release.set()
    allocating.join(timeout=30)
    assert results["stock"].status_code == 201
    # 20 were asked against the 10 the allocation saw: refused, zero writes.
    assert results["over"].status_code == 409
    monkeypatch.setattr(allocations, "stock_position_of", real_position)
    assert _suggest(client, pn)["available_stocked_quantity"] == 20
    assert _allocate(client, pn, [(work_order.demand_id, 20)]).status_code == 201
    _assert_projections_match_replay(db_engine)


# ---------------------------------------------------------------------------
# The reversal — the auditable adjustment
# ---------------------------------------------------------------------------


def test_reversal_reopens_the_work_order_and_returns_the_quantity_to_stock(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    flow_id = _release(client, material, work_order, pn, quantity=5)
    assert _stock(client, material, stockroom, flow_id, pn, 5).status_code == 201
    allocated = _allocate(client, pn, [(work_order.demand_id, 5)], station_id=stockroom.station_id)
    assert allocated.status_code == 201
    allocation_id = allocated.json()["rows"][0]["allocation_id"]
    assert _work_order(client, work_order.id)["status"] == "COMPLETED"

    response = _reverse(client, allocation_id, reason="allocated to the wrong Work Order")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "REVERSE_ALLOCATION"
    assert body["reopened_work_order_ids"] == [work_order.id]
    (row,) = body["rows"]
    assert row["reverses_allocation_id"] == allocation_id and row["quantity"] == 5
    assert row["allocation_reason"] == "allocated to the wrong Work Order"
    detail = _work_order(client, work_order.id)
    assert detail["status"] == "RELEASED" and detail["completed_at"] is None
    assert detail["demands"][0]["allocated_quantity"] == 0
    assert work_order.id in _active_ids(client)
    assert _completed(client, search=str(work_order.id))["total"] == 0
    assert _suggest(client, pn)["available_stocked_quantity"] == 5
    # The original row is untouched; both rows are listed for audit.
    listed = client.get("/api/allocations", params={"part_number": pn}).json()
    assert [(row["id"], row["reverses_allocation_id"]) for row in listed] == [
        (allocation_id, None),
        (body["rows"][0]["allocation_id"], allocation_id),
    ]
    # Once only; never a reversal of a reversal; replay of the reversal.
    before = _counts(db_engine)
    assert _reverse(client, allocation_id).status_code == 409
    assert _reverse(client, body["rows"][0]["allocation_id"]).status_code == 409
    assert _reverse(client, 999_999_999).status_code == 404
    assert (
        client.post(
            f"/api/allocations/{allocation_id}/reversals",
            json={"reason": "", "device_event_id": str(uuid.uuid4())},
        ).status_code
        == 422
    )
    assert _counts(db_engine) == before
    # Re-allocation completes the Work Order again with a new done date.
    again = _allocate(client, pn, [(work_order.demand_id, 5)])
    assert again.status_code == 201 and again.json()["completed_work_order_ids"] == [work_order.id]
    assert _work_order(client, work_order.id)["status"] == "COMPLETED"
    _assert_projections_match_replay(db_engine)


def test_double_reversal_race_has_one_winner(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    flow_id = _release(client, material, work_order, pn, quantity=5)
    assert _stock(client, material, stockroom, flow_id, pn, 5).status_code == 201
    allocated = _allocate(client, pn, [(work_order.demand_id, 5)])
    allocation_id = allocated.json()["rows"][0]["allocation_id"]
    results: dict[int, Any] = {}

    def _attempt(slot: int) -> None:
        results[slot] = _reverse(client, allocation_id)

    threads = [threading.Thread(target=_attempt, args=(slot,)) for slot in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(response.status_code for response in results.values()) == [201, 409]
    reversals = [row for row in _allocation_rows(db_engine, pn) if row.reverses_allocation_id]
    assert len(reversals) == 1
    assert _demand_row(db_engine, work_order.demand_id).allocated_quantity == 0
    _assert_projections_match_replay(db_engine)


# ---------------------------------------------------------------------------
# Completed Work Orders — read-only history, floors, the history endpoint
# ---------------------------------------------------------------------------


def test_completed_work_order_is_read_only_history(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    other_pn = _unique("PN")
    work_order = _create_work_order(
        client,
        [
            {"part_number": pn, "requested_quantity": 5},
            {"part_number": other_pn, "requested_quantity": 3},
        ],
    )
    # Stock is PN-level: the line is satisfied from a supply Work Order's
    # stock without any release of its own.
    _supply(client, material, stockroom, pn, 5)
    assert _allocate(client, pn, [(work_order.demand_id, 5)]).status_code == 201
    # Not yet complete: lowering Qty below the allocated quantity is
    # refused (the committed-quantity floor), a raise is fine.
    lowered = client.patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "requested_quantity": 4}]},
    )
    assert lowered.status_code == 409 and "already allocated" in lowered.json()["detail"]
    raised = client.patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "requested_quantity": 6}]},
    )
    assert raised.status_code == 200
    assert (
        client.patch(
            f"/api/work-orders/{work_order.id}",
            json={"line_edits": [{"id": work_order.demand_id, "requested_quantity": 5}]},
        ).status_code
        == 200
    )
    # Removing an allocated line is refused; the fully allocated line
    # needs no production release.
    assert (
        client.delete(
            f"/api/work-orders/{work_order.id}/demands/{work_order.demand_id}"
        ).status_code
        == 409
    )
    refused_release = client.post(
        f"/api/work-orders/{work_order.id}/demands/{work_order.demand_id}/release",
        json={
            "part_number": pn,
            "quantity": 1,
            "route_mode": "FLOATING",
            "starting_area_id": material.area_id,
            "operation_id": material.operation_id,
            "confirm_active_quantity": False,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert (
        refused_release.status_code == 409 and "fully allocated" in refused_release.json()["detail"]
    )

    # Complete it through the second line from Management-side stock
    # of the same PN on another Work Order (stock is PN-level).
    other = _create_work_order(client, [{"part_number": other_pn, "requested_quantity": 3}])
    other_flow = _release(client, material, other, other_pn, quantity=3)
    assert _stock(client, material, stockroom, other_flow, other_pn, 3).status_code == 201
    completing = _allocate(client, other_pn, [(work_order.demand_ids[1], 3)])
    assert completing.status_code == 201 and completing.json()["completed_work_order_ids"] == [
        work_order.id
    ]
    assert other.id in _active_ids(client) and work_order.id not in _active_ids(client)

    before = _counts(db_engine)
    for payload in (
        {"work_order_number": _unique("WO")},
        {"due_date": "2026-12-31"},
        {"line_edits": [{"id": work_order.demand_id, "due_date": "2026-12-31"}]},
        {"new_lines": [{"part_number": _unique("PN"), "requested_quantity": 1}]},
    ):
        edit = client.patch(f"/api/work-orders/{work_order.id}", json=payload)
        assert edit.status_code == 409 and "completed" in edit.json()["detail"], payload
    assert (
        client.delete(
            f"/api/work-orders/{work_order.id}/demands/{work_order.demand_id}"
        ).status_code
        == 409
    )
    assert _counts(db_engine) == before
    assert _work_order(client, work_order.id)["status"] == "COMPLETED"
    _assert_projections_match_replay(db_engine)


def test_demand_save_judges_the_allocation_floor_on_the_locked_re_read(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Save that read the demand BEFORE a concurrent allocation committed
    must judge the committed-quantity floor on the row RE-READ under its
    lock — never on the stale identity-map copy — so a lowered Qty can
    never leave ``allocated_quantity > requested_quantity``."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 10}])
    _supply(client, material, stockroom, pn, 10)

    real_get = work_orders.get_work_order
    allocated_meanwhile: list[Any] = []

    def get_then_allocate(session: Session, work_order_id: int) -> Any:
        detail = real_get(session, work_order_id)
        if work_order_id == work_order.id and not allocated_meanwhile:
            # The allocation commits (its own request, its own session)
            # after this save loaded the demand and before it locks it.
            allocated_meanwhile.append(_allocate(client, pn, [(work_order.demand_id, 6)]))
        return detail

    monkeypatch.setattr(work_orders, "get_work_order", get_then_allocate)
    lowered = client.patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "requested_quantity": 4}]},
    )
    assert allocated_meanwhile and allocated_meanwhile[0].status_code == 201
    assert lowered.status_code == 409 and "6 pcs are already allocated" in lowered.json()["detail"]
    demand = _demand_row(db_engine, work_order.demand_id)
    assert (demand.requested_quantity, demand.allocated_quantity) == (10, 6)


def test_demand_line_with_allocation_history_is_not_removable(
    client: TestClient, db_engine: Engine
) -> None:
    """A reversed allocation leaves the line's active allocation at 0, but
    its append-only allocation rows still reference the line: removal is
    refused with a clear reason — never a database error."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(
        client,
        [
            {"part_number": pn, "requested_quantity": 4},
            {"part_number": _unique("PN"), "requested_quantity": 1},
        ],
    )
    _supply(client, material, stockroom, pn, 4)
    allocated = _allocate(client, pn, [(work_order.demand_id, 4)])
    assert allocated.status_code == 201
    assert _reverse(client, allocated.json()["rows"][0]["allocation_id"]).status_code == 201
    assert _demand_row(db_engine, work_order.demand_id).allocated_quantity == 0

    before = _counts(db_engine)
    removed = client.delete(f"/api/work-orders/{work_order.id}/demands/{work_order.demand_id}")
    assert removed.status_code == 409 and "allocat" in removed.json()["detail"]
    assert _counts(db_engine) == before
    assert len(_allocation_rows(db_engine, pn)) == 2


def test_completed_history_search_filters_and_pages(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    marker = uuid.uuid4().hex[:8].upper()
    completed: list[_WorkOrder] = []
    for index in range(3):
        pn = _unique(f"PN{marker}")
        job = f"JOB-{marker}-{index}"
        due = "2026-01-01" if index == 0 else ("2099-01-01" if index == 1 else None)
        work_order = _create_work_order(
            client,
            [{"part_number": pn, "requested_quantity": 2, "job_numbers": [job]}],
            number=f"WO-{marker}-{index}",
            due_date=due,
        )
        flow_id = _release(client, material, work_order, pn, quantity=2)
        assert _stock(client, material, stockroom, flow_id, pn, 2).status_code == 201
        assert _allocate(client, pn, [(work_order.demand_id, 2)]).status_code == 201
        completed.append(work_order)

    page = _completed(client, search=marker)
    assert page["total"] == 3
    assert [row["id"] for row in page["work_orders"]] == [w.id for w in reversed(completed)]
    assert all(row["status"] == "COMPLETED" and row["completed_at"] for row in page["work_orders"])
    # Search by PN and by Job Number, not only by number.
    assert _completed(client, search=f"JOB-{marker}-1")["total"] == 1
    assert _completed(client, search=f"pn{marker}".lower())["total"] == 3
    # Due outcome: index 0 is late (due 2026-01-01), 1 on time, 2 undated.
    assert [
        r["id"] for r in _completed(client, search=marker, due_outcome="LATE")["work_orders"]
    ] == [completed[0].id]
    assert [
        r["id"] for r in _completed(client, search=marker, due_outcome="ON_TIME")["work_orders"]
    ] == [completed[1].id]
    assert [
        r["id"] for r in _completed(client, search=marker, due_outcome="NO_DUE_DATE")["work_orders"]
    ] == [completed[2].id]
    # Keyset paging, one row per page, newest done date first — the
    # cursor is opaque and issued by the server.
    first = _completed(client, search=marker, limit=1)
    assert [r["id"] for r in first["work_orders"]] == [completed[2].id]
    assert isinstance(first["next_cursor"], str)
    second = _completed(client, search=marker, limit=1, cursor=first["next_cursor"])
    assert [r["id"] for r in second["work_orders"]] == [completed[1].id]
    third = _completed(client, search=marker, limit=2, cursor=second["next_cursor"])
    assert [r["id"] for r in third["work_orders"]] == [completed[0].id]
    assert third["next_cursor"] is None
    # A done-date range that excludes everything, and a broken cursor.
    # `history_total` ignores the filters: "none in this range" is not
    # "none ever" (GUI_DESIGN §11.5).
    empty_range = _completed(client, search=marker, done_to="2000-01-01")
    assert empty_range["total"] == 0 and empty_range["history_total"] >= 3
    assert page["history_total"] >= page["total"]
    assert (
        client.get("/api/work-orders/completed", params={"cursor": "nonsense"}).status_code == 422
    )
    assert client.get("/api/work-orders/completed", params={"limit": 0}).status_code == 422
    assert client.get("/api/work-orders/completed", params={"sort": "PN"}).status_code == 422


def _complete(
    client: TestClient, material: _Cell, stockroom: _Cell, work_order: _WorkOrder
) -> None:
    """Stock and allocate every line of ``work_order`` (all lines 1 pc)."""
    detail = _work_order(client, work_order.id)
    for line in detail["demands"]:
        pn = str(line["part_number"])
        flow_id = _release(client, material, work_order, pn, demand_id=int(line["id"]), quantity=1)
        assert _stock(client, material, stockroom, flow_id, pn, 1).status_code == 201
        assert _allocate(client, pn, [(int(line["id"]), 1)]).status_code == 201


def _walk(client: TestClient, **params: Any) -> list[int]:
    """Every id of the history for ``params``, one keyset page at a time."""
    ids: list[int] = []
    cursor: str | None = None
    for _ in range(50):
        page = _completed(client, **params, **({"cursor": cursor} if cursor else {}))
        ids.extend(int(r["id"]) for r in page["work_orders"])
        cursor = page["next_cursor"]
        if cursor is None:
            return ids
    raise AssertionError("the keyset walk never ended")


def test_completed_history_sorts_on_the_server_with_keyset_paging_per_sort(
    client: TestClient, db_engine: Engine
) -> None:
    """GUI_DESIGN §11.5: the date and identity columns sort on the SERVER
    — Done descending by default, NULL values (an internal number, no
    due date) last in either direction, the id as the stable
    tie-breaker — and a page continues exactly that order: walking one
    row at a time yields the same sequence as one page, with no row
    twice or missing, also across the trailing NULL block; a cursor of
    another sort is refused."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    marker = uuid.uuid4().hex[:8].upper()
    spec: list[tuple[str | None, str, str | None]] = [
        # (number, received, due)
        (f"WO-{marker}-B", "2026-02-01", "2026-06-01"),
        (None, "2026-01-01", None),
        (f"WO-{marker}-A", "2026-03-01", None),
        (None, "2026-02-01", "2026-05-01"),
        (f"WO-{marker}-C", "2026-01-01", "2026-06-01"),
    ]
    orders: list[_WorkOrder] = []
    for number, received, due in spec:
        work_order = _create_work_order(
            client,
            [{"part_number": _unique(f"PN{marker}"), "requested_quantity": 1}],
            number=number,
            received_date=received,
            due_date=due,
        )
        _complete(client, material, stockroom, work_order)
        orders.append(work_order)
    ids = [w.id for w in orders]
    search = f"PN{marker}"

    def order(sort: str, direction: str) -> list[int]:
        page = _completed(client, search=search, sort=sort, direction=direction)
        assert page["total"] == 5
        return [int(r["id"]) for r in page["work_orders"]]

    # Default: Done descending = completion order reversed (ids as the
    # tie-breaker share the direction).
    assert order("DONE", "DESC") == list(reversed(ids))
    assert [int(r["id"]) for r in _completed(client, search=search)["work_orders"]] == list(
        reversed(ids)
    )
    assert order("DONE", "ASC") == ids
    # Identity: A < B < C, internal (NULL) numbers last both ways, by id.
    assert order("NUMBER", "ASC") == [ids[2], ids[0], ids[4], ids[1], ids[3]]
    assert order("NUMBER", "DESC") == [ids[4], ids[0], ids[2], ids[3], ids[1]]
    # Due date: equal dates by id in the direction, undated last both ways.
    assert order("DUE", "ASC") == [ids[3], ids[0], ids[4], ids[1], ids[2]]
    assert order("DUE", "DESC") == [ids[4], ids[0], ids[3], ids[2], ids[1]]
    assert order("RECEIVED", "ASC") == [ids[1], ids[4], ids[0], ids[3], ids[2]]
    assert order("RECEIVED", "DESC") == [ids[2], ids[3], ids[0], ids[4], ids[1]]
    # Keyset continuation reproduces every order one row at a time.
    for sort in ("DONE", "NUMBER", "DUE", "RECEIVED"):
        for direction in ("ASC", "DESC"):
            assert _walk(client, search=search, sort=sort, direction=direction, limit=1) == order(
                sort, direction
            ), (sort, direction)
            assert _walk(client, search=search, sort=sort, direction=direction, limit=2) == order(
                sort, direction
            ), (sort, direction)
    # A cursor is bound to the sort it was issued for.
    issued = _completed(client, search=search, sort="DUE", direction="ASC", limit=1)
    mismatch = client.get(
        "/api/work-orders/completed",
        params={
            "search": search,
            "sort": "NUMBER",
            "direction": "ASC",
            "cursor": issued["next_cursor"],
        },
    )
    assert mismatch.status_code == 422 and "another sort" in mismatch.json()["detail"]


def test_site_timezone_must_be_a_known_zone() -> None:
    """`SITE_TIMEZONE` is validated at startup: a typo can never silently
    shift every done date."""
    assert Settings(database_url="postgresql+psycopg://x", site_timezone="UTC").site_timezone
    assert (
        Settings(
            database_url="postgresql+psycopg://x", site_timezone="America/Los_Angeles"
        ).site_timezone
        == "America/Los_Angeles"
    )
    with pytest.raises(ValidationError, match="not a known IANA time zone"):
        Settings(database_url="postgresql+psycopg://x", site_timezone="Mars/Olympus_Mons")


def test_done_date_and_due_outcome_follow_the_site_calendar(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ONE calendar rule (`SITE_TIMEZONE`) turns the completion instant
    into the done date — for the due-outcome FILTER, the Done range and
    the row's displayed `done_date` / `due_outcome` alike — so a Work
    Order completed at 06:30 UTC on the day after its due date is LATE
    in a UTC site and ON_TIME in a Los Angeles site (22:30 the day
    before), and the filter never contradicts the row it returns."""
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    marker = uuid.uuid4().hex[:8].upper()
    work_order = _create_work_order(
        client,
        [{"part_number": _unique(f"PN{marker}"), "requested_quantity": 1}],
        number=f"WO-{marker}",
        due_date="2026-03-09",
    )
    _complete(client, material, stockroom, work_order)
    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.WorkOrder)
            .where(models.WorkOrder.id == work_order.id)
            .values(completed_at=datetime.datetime(2026, 3, 10, 6, 30, tzinfo=datetime.UTC))
        )
    search = f"PN{marker}"

    def rows(**params: Any) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]], _completed(client, search=search, **params)["work_orders"]
        )

    for zone, done, outcome, days_late in (
        ("UTC", "2026-03-10", "LATE", 1),
        ("America/Los_Angeles", "2026-03-09", "ON_TIME", None),
    ):
        monkeypatch.setattr(work_orders, "site_timezone", lambda zone=zone: zone)
        row = rows()[0]
        assert (row["done_date"], row["due_outcome"], row["days_late"]) == (
            done,
            outcome,
            days_late,
        )
        # The filter judges the same date: exactly the matching outcome
        # returns the row, and the row it returns says the same.
        matching = rows(due_outcome=outcome)
        assert [r["id"] for r in matching] == [work_order.id]
        assert matching[0]["due_outcome"] == outcome
        other = "ON_TIME" if outcome == "LATE" else "LATE"
        assert rows(due_outcome=other) == []
        # The Done range is judged on the same calendar date.
        assert [r["id"] for r in rows(done_from=done, done_to=done)] == [work_order.id]
        before = (datetime.date.fromisoformat(done) - datetime.timedelta(days=1)).isoformat()
        assert rows(done_from=before, done_to=before) == []
        # Work Order Details carry the same derived calendar.
        detail = _work_order(client, work_order.id)
        assert (detail["done_date"], detail["due_outcome"]) == (done, outcome)
    # An active Work Order has no done date and no outcome.
    active = _create_work_order(client, [{"part_number": _unique("PN"), "requested_quantity": 1}])
    detail = _work_order(client, active.id)
    assert (detail["done_date"], detail["due_outcome"], detail["days_late"]) == (None, None, None)


def test_allocation_rows_are_immutable_and_never_reference_movement(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order = _create_work_order(client, [{"part_number": pn, "requested_quantity": 5}])
    flow_id = _release(client, material, work_order, pn, quantity=5)
    assert _stock(client, material, stockroom, flow_id, pn, 5).status_code == 201
    assert _allocate(client, pn, [(work_order.demand_id, 5)]).status_code == 201
    (row,) = _allocation_rows(db_engine, pn)
    assert set(row._mapping).isdisjoint({"quantity_flow_id", "part_movement_id"})
    table = models.WorkOrderAllocation
    for statement in (
        sa.update(table).where(table.id == row.id).values(quantity=1),
        sa.delete(table).where(table.id == row.id),
    ):
        with pytest.raises(sa.exc.DBAPIError), db_engine.begin() as connection:
            connection.execute(statement)
    assert _demand_row(db_engine, work_order.demand_id).allocated_quantity == 5
