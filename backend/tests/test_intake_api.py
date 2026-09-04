"""Integration tests for Phase 10.5 — Scan Station `Receive Quantity`.

Exercises the full request path — FastAPI routes, the Application
command and read model, and PostgreSQL — against a dedicated temporary
database migrated to head by the real Alembic chain. Covered per
IMPLEMENTATION_ROADMAP Phase 10.5, PROJECT_PROFILE §14 and GUI_DESIGN
§4.7:

- the entry condition: a PN with no ACTIVE Work Order Demand (a demand
  line with `requested_quantity > allocated_quantity`) and no active
  quantity at a production Area opens the workflow — a PN seen for the
  first time included; active demand, active quantity and a terminal
  Area each withhold it, in the read model AND in the command;
- the confirmed receipt as ONE transaction: the PartNumber master on
  first valid use, the internal blank-number Work Order, the
  WorkOrderDemand, the QuantityFlow, the immutable `RECEIVED` Movement
  with the Scan Station identity and the resolved Operation, and the
  current-position projection — with `PLANNED` snapshotting an
  independent AssignedRoute and `FLOATING` none;
- internal Work Order reuse (§14 "must never guess"): one clearly
  applicable blank-number MODIFY Work Order is reused by raising its
  existing demand line for the PN (SLICE1_DATA_MODEL §5 — one
  canonical PN at most once per Work Order), several REQUIRE an
  explicit selection listing the candidates, and a `NEW` receipt never
  reuses;
- the authoritative write-time revalidation: a deactivated or rebound
  station, a deactivated or terminal Area, and a foreign or inactive
  Operation are refused with zero writes;
- the shared command/idempotency model: replay of the same intent,
  conflicting reuse, and cross-command reuse;
- Undo: a receipt is deliberately not reversible from a station;
- quantity conservation and the projection replay.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations; the module database is dropped afterwards.
"""

import datetime
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import allocations, intake, projections, undo, work_orders
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_intake_api"
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


def _resolve(client: TestClient, cell: _Cell, pn: str) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/scans/resolve", json={"part_number": pn}
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _receipt_payload(pn: str, quantity: Any, **kw: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "request_type": "MODIFY",
        "route_mode": "FLOATING",
        # What the PN resolution issued when the wizard opened: the
        # received date follows this instant, never the confirmation.
        "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return payload


def _receive(client: TestClient, cell: _Cell, pn: str, quantity: Any, **kw: Any) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/receipts", json=_receipt_payload(pn, quantity, **kw)
    )


def _work_order(client: TestClient, work_order_id: int) -> dict[str, Any]:
    response = client.get(f"/api/work-orders/{work_order_id}")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _counts(engine: Engine, pn: str) -> dict[str, int]:
    """Everything one PN owns — the zero-write assertion of a refusal."""
    with Session(engine) as session:
        return {
            "flows": int(
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(models.QuantityFlow)
                    .where(models.QuantityFlow.part_number == pn)
                )
                or 0
            ),
            "movements": int(
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(models.PartMovement)
                    .where(models.PartMovement.part_number == pn)
                )
                or 0
            ),
            "demands": int(
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(models.WorkOrderDemand)
                    .where(models.WorkOrderDemand.part_number == pn)
                )
                or 0
            ),
            "masters": int(
                session.scalar(
                    sa.select(sa.func.count())
                    .select_from(models.PartNumber)
                    .where(models.PartNumber.part_number == pn)
                )
                or 0
            ),
            "requested": int(
                session.scalar(
                    sa.select(
                        sa.func.coalesce(sa.func.sum(models.WorkOrderDemand.requested_quantity), 0)
                    ).where(models.WorkOrderDemand.part_number == pn)
                )
                or 0
            ),
        }


def _internal_modify_candidate(
    client: TestClient, production: _Cell, stockroom: _Cell, pn: str
) -> tuple[int, int]:
    """A blank-number MODIFY Work Order whose line for ``pn`` is inactive.

    Built through the real workflows: the internal Work Order carries a
    second line (another PN) that keeps it open, while the line for
    ``pn`` is released, stocked and fully allocated — so the PN has no
    remaining business shortage and no active quantity, exactly the
    state in which `Receive Quantity` opens and §14 reuse applies.
    """
    other_pn = _unique("PN")
    created = client.post(
        "/api/work-orders",
        json={
            "lines": [
                {"part_number": pn, "requested_quantity": 4, "request_type": "MODIFY"},
                {"part_number": other_pn, "requested_quantity": 5, "request_type": "MODIFY"},
            ]
        },
    )
    assert created.status_code == 201, created.text
    work_order_id = int(created.json()["id"])
    assert created.json()["work_order_number"] is None
    demand_id = int(
        next(line for line in created.json()["demands"] if line["part_number"] == pn)["id"]
    )
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": 4,
            "route_mode": "FLOATING",
            "starting_area_id": production.area_id,
            "operation_id": production.operation_id,
            "confirm_active_quantity": False,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert released.status_code == 201, released.text
    stocked = client.post(
        f"/api/scan-stations/{stockroom.station_id}/stockings",
        json={
            "part_number": pn,
            "quantity_flow_id": int(released.json()["quantity_flow_id"]),
            "source_area_id": production.area_id,
            "target_area_id": stockroom.area_id,
            "quantity": 4,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert stocked.status_code == 201, stocked.text
    allocated = client.post(
        "/api/allocations",
        json={
            "part_number": pn,
            "allocation_quantity": 4,
            "lines": [{"work_order_demand_id": demand_id, "quantity": 4}],
            "station_id": stockroom.station_id,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert allocated.status_code == 201, allocated.text
    return work_order_id, demand_id


def _scrapped_beside_a_settled_demand(
    client: TestClient, production: _Cell, stockroom: _Cell, pn: str
) -> str:
    """A PN whose only remaining history is ONE scrapped (closed) flow.

    Built through the real workflows: the demand line is released,
    stocked and fully allocated (no business shortage left), and a
    second quantity found at the Area was scrapped — so the PN has no
    active demand and no active quantity and `Receive Quantity` opens,
    while undoing that Scrap would reopen the closed flow and give the
    PN active quantity again. Returns the Scrap's ``device_event_id``.
    """
    created = client.post(
        "/api/work-orders",
        json={"lines": [{"part_number": pn, "requested_quantity": 4, "request_type": "MODIFY"}]},
    )
    assert created.status_code == 201, created.text
    work_order_id = int(created.json()["id"])
    demand_id = int(created.json()["demands"][0]["id"])
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": 4,
            "route_mode": "FLOATING",
            "starting_area_id": production.area_id,
            "operation_id": production.operation_id,
            "confirm_active_quantity": False,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert released.status_code == 201, released.text
    released_flow = int(released.json()["quantity_flow_id"])
    # Found quantity beside it — the Phase 9 addition, its own flow.
    added = client.post(
        f"/api/scan-stations/{production.station_id}/quantity-additions",
        json={
            "part_number": pn,
            "quantity": 3,
            "reason": "Found at the Area",
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert added.status_code == 201, added.text
    scrap_event = str(uuid.uuid4())
    scrapped = client.post(
        f"/api/scan-stations/{production.station_id}/scraps",
        json={
            "part_number": pn,
            "quantity_flow_id": int(added.json()["quantity_flow_id"]),
            "quantity": 3,
            "reason": "Damaged",
            "device_event_id": scrap_event,
        },
    )
    assert scrapped.status_code == 201, scrapped.text
    stocked = client.post(
        f"/api/scan-stations/{stockroom.station_id}/stockings",
        json={
            "part_number": pn,
            "quantity_flow_id": released_flow,
            "source_area_id": production.area_id,
            "target_area_id": stockroom.area_id,
            "quantity": 4,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert stocked.status_code == 201, stocked.text
    allocated = client.post(
        "/api/allocations",
        json={
            "part_number": pn,
            "allocation_quantity": 4,
            "lines": [{"work_order_demand_id": demand_id, "quantity": 4}],
            "station_id": stockroom.station_id,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert allocated.status_code == 201, allocated.text
    return scrap_event


# ---------------------------------------------------------------------------
# The entry condition (GUI_DESIGN §4.7 item 1)
# ---------------------------------------------------------------------------


def test_unknown_part_number_opens_receive_quantity(client: TestClient) -> None:
    cell = _Cell(client)
    pn = _unique("PN")

    resolution = _resolve(client, cell, pn)

    assert resolution["resolution"] == "NO_TRANSFERABLE_QUANTITY"
    assert resolution["has_active_demand"] is False
    assert resolution["intake_available"] is True
    assert resolution["part_number_known"] is False
    assert resolution["internal_work_orders"] == []


def test_active_demand_withholds_receive_quantity(client: TestClient) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    created = client.post(
        "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 12}]}
    )
    assert created.status_code == 201, created.text

    resolution = _resolve(client, cell, pn)

    # Demand that can still be satisfied belongs to a production
    # release from Management, never to a station receipt.
    assert resolution["has_active_demand"] is True
    assert resolution["intake_available"] is False
    assert resolution["part_number_known"] is True


def test_terminal_area_never_opens_receive_quantity(client: TestClient) -> None:
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")

    resolution = _resolve(client, stockroom, pn)

    assert resolution["intake_available"] is False


def test_active_quantity_withholds_receive_quantity(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    assert _receive(client, cell, pn, 6).status_code == 201

    resolution = _resolve(client, cell, pn)

    # The PN now has quantity in the Area: the applicable workflows are
    # the in-Area actions (`Add more quantity` among them), not a
    # second receipt.
    assert resolution["resolution"] == "ALREADY_IN_AREA"
    assert resolution["intake_available"] is False


# ---------------------------------------------------------------------------
# The confirmed receipt
# ---------------------------------------------------------------------------


def test_receipt_creates_internal_work_order_demand_flow_and_movement(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, machine_count=1)
    pn = _unique("PN")

    response = _receive(client, cell, pn, 7, reason="returned from customer for rework")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["part_number"] == pn
    assert body["quantity"] == 7
    assert body["request_type"] == "MODIFY"
    assert body["route_mode"] == "FLOATING"
    assert body["assigned_route_id"] is None
    assert body["area_id"] == cell.area_id
    assert body["operation_id"] == cell.operation_id
    assert body["processing_state"] == "QUEUED"
    assert body["station_id"] == cell.station_id
    assert body["work_order_reused"] is False
    assert body["reason"] == "returned from customer for rework"

    work_order = _work_order(client, int(body["work_order_id"]))
    assert work_order["work_order_number"] is None
    assert len(work_order["demands"]) == 1
    demand = work_order["demands"][0]
    assert demand["id"] == body["work_order_demand_id"]
    assert demand["part_number"] == pn
    assert demand["request_type"] == "MODIFY"
    assert demand["requested_quantity"] == 7
    assert demand["released_quantity"] == 7
    assert demand["remaining_quantity"] == 0

    with Session(db_engine) as session:
        movement = session.scalars(
            sa.select(models.PartMovement).where(models.PartMovement.part_number == pn)
        ).one()
        assert movement.movement_type == "RECEIVED"
        assert movement.from_area_id is None
        assert movement.to_area_id == cell.area_id
        assert movement.station_id == cell.station_id
        assert movement.operation_id == cell.operation_id
        assert movement.assigned_route_step_id is None
        assert movement.command_sequence == 1
        assert (movement.metadata_ or {})["command"]["kind"] == "INTAKE"
        assert (movement.metadata_ or {})["context"]["work_order_demand_id"] == demand["id"]
        flow = session.get(models.QuantityFlow, int(body["quantity_flow_id"]))
        assert flow is not None
        assert flow.status == "ACTIVE"
        assert flow.route_mode == "FLOATING"
        assert flow.current_area_id == cell.area_id
        assert flow.current_machine_id is None
        # The master is created on first valid use.
        assert session.get(models.PartNumber, pn) is not None


def test_receipt_into_area_without_machines_enters_direct_processing(client: TestClient) -> None:
    cell = _Cell(client)
    pn = _unique("PN")

    response = _receive(client, cell, pn, 3)

    assert response.status_code == 201, response.text
    assert response.json()["processing_state"] == "PROCESSING"
    inventory = client.get(f"/api/areas/{cell.area_id}/inventory")
    assert inventory.status_code == 200, inventory.text
    line = next(item for item in inventory.json()["processing"] if item["part_number"] == pn)
    assert line["total_quantity"] == 3


def test_receipt_records_the_due_date_on_the_demand(client: TestClient) -> None:
    cell = _Cell(client)
    pn = _unique("PN")

    response = _receive(client, cell, pn, 2, due_date="2026-10-15")

    assert response.status_code == 201, response.text
    work_order = _work_order(client, int(response.json()["work_order_id"]))
    # The due date is owned by the WorkOrderDemand; the PN never owns one.
    assert work_order["demands"][0]["due_date"] == "2026-10-15"
    assert work_order["due_date"] is None


def test_planned_receipt_snapshots_its_own_assigned_route(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    next_cell = _Cell(client)
    template_id = _create_route_template(
        db_engine, [(cell.area_id, cell.operation_id), (next_cell.area_id, None)]
    )
    pn = _unique("PN")

    response = _receive(client, cell, pn, 5, route_mode="PLANNED", route_template_id=template_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["route_mode"] == "PLANNED"
    assert body["assigned_route_id"] is not None
    with Session(db_engine) as session:
        snapshot_steps = session.scalars(
            sa.select(models.AssignedRouteStep)
            .where(models.AssignedRouteStep.assigned_route_id == body["assigned_route_id"])
            .order_by(models.AssignedRouteStep.sequence)
        ).all()
        assert [step.area_id for step in snapshot_steps] == [cell.area_id, next_cell.area_id]
        movement = session.scalars(
            sa.select(models.PartMovement).where(models.PartMovement.part_number == pn)
        ).one()
        assert movement.assigned_route_step_id == snapshot_steps[0].id


def test_planned_receipt_requires_a_route_starting_here(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    elsewhere = _Cell(client)
    template_id = _create_route_template(db_engine, [(elsewhere.area_id, None)])
    pn = _unique("PN")

    response = _receive(client, cell, pn, 5, route_mode="PLANNED", route_template_id=template_id)

    assert response.status_code == 422, response.text
    assert _counts(db_engine, pn)["movements"] == 0


def test_floating_receipt_refuses_a_planned_route(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    template_id = _create_route_template(db_engine, [(cell.area_id, None)])
    pn = _unique("PN")

    response = _receive(client, cell, pn, 5, route_template_id=template_id)

    assert response.status_code == 422, response.text
    assert _counts(db_engine, pn) == {
        "flows": 0,
        "movements": 0,
        "demands": 0,
        "masters": 0,
        "requested": 0,
    }


# ---------------------------------------------------------------------------
# Internal Work Order reuse (PROJECT_PROFILE §14 — never a guess)
# ---------------------------------------------------------------------------


def test_one_applicable_internal_work_order_is_reused_by_raising_its_line(
    client: TestClient, db_engine: Engine
) -> None:
    production = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    work_order_id, demand_id = _internal_modify_candidate(client, production, stockroom, pn)

    resolution = _resolve(client, production, pn)
    assert resolution["intake_available"] is True
    assert [item["work_order_id"] for item in resolution["internal_work_orders"]] == [work_order_id]
    assert resolution["internal_work_orders"][0]["requested_quantity"] == 4

    response = _receive(client, production, pn, 6)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["work_order_reused"] is True
    assert body["work_order_id"] == work_order_id
    assert body["work_order_demand_id"] == demand_id
    work_order = _work_order(client, work_order_id)
    lines = [line for line in work_order["demands"] if line["part_number"] == pn]
    # SLICE1_DATA_MODEL §5: one canonical PN at most once per Work
    # Order — the existing line is raised, never duplicated.
    assert len(lines) == 1
    assert lines[0]["requested_quantity"] == 10
    # The released quantity of the line is derived from every RECEIVED
    # of its context: the original 4-piece release plus this receipt.
    assert lines[0]["released_quantity"] == 10
    assert lines[0]["allocated_quantity"] == 4
    with Session(db_engine) as session:
        events = session.scalars(
            sa.select(models.AuditEvent)
            .where(
                models.AuditEvent.entity_type == "WorkOrderDemand",
                models.AuditEvent.entity_id == str(demand_id),
                models.AuditEvent.event_type == "UPDATED",
            )
            .order_by(models.AuditEvent.id)
        ).all()
        assert [event.after_data["requested_quantity"] for event in events if event.after_data] == [
            10
        ]


def test_several_applicable_internal_work_orders_require_explicit_selection(
    client: TestClient, db_engine: Engine
) -> None:
    production = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    first, _ = _internal_modify_candidate(client, production, stockroom, pn)
    second, _ = _internal_modify_candidate(client, production, stockroom, pn)
    before = _counts(db_engine, pn)

    response = _receive(client, production, pn, 3)

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["selection_required"] is True
    assert sorted(item["work_order_id"] for item in body["work_orders"]) == sorted([first, second])
    assert _counts(db_engine, pn) == before

    chosen = _receive(client, production, pn, 3, work_order_id=second)
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["work_order_id"] == second
    assert chosen.json()["work_order_reused"] is True


def test_selecting_an_inapplicable_work_order_is_refused(
    client: TestClient, db_engine: Engine
) -> None:
    production = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    other_pn = _unique("PN")
    foreign, _ = _internal_modify_candidate(client, production, stockroom, other_pn)
    before = _counts(db_engine, pn)

    response = _receive(client, production, pn, 3, work_order_id=foreign)

    assert response.status_code == 409, response.text
    assert _counts(db_engine, pn) == before


def test_new_request_type_never_reuses_an_internal_work_order(
    client: TestClient, db_engine: Engine
) -> None:
    production = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    existing, _ = _internal_modify_candidate(client, production, stockroom, pn)

    refused = _receive(client, production, pn, 2, request_type="NEW", work_order_id=existing)
    assert refused.status_code == 422, refused.text

    response = _receive(client, production, pn, 2, request_type="NEW")

    assert response.status_code == 201, response.text
    assert response.json()["work_order_reused"] is False
    assert response.json()["work_order_id"] != existing
    assert _work_order(client, int(response.json()["work_order_id"]))["work_order_number"] is None


def test_a_completed_internal_work_order_is_never_a_reuse_candidate(
    client: TestClient, db_engine: Engine
) -> None:
    production = _Cell(client)
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")
    created = client.post(
        "/api/work-orders",
        json={"lines": [{"part_number": pn, "requested_quantity": 4, "request_type": "MODIFY"}]},
    )
    assert created.status_code == 201, created.text
    work_order_id = int(created.json()["id"])
    demand_id = int(created.json()["demands"][0]["id"])
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": 4,
            "route_mode": "FLOATING",
            "starting_area_id": production.area_id,
            "operation_id": production.operation_id,
            "confirm_active_quantity": False,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert released.status_code == 201, released.text
    stocked = client.post(
        f"/api/scan-stations/{stockroom.station_id}/stockings",
        json={
            "part_number": pn,
            "quantity_flow_id": int(released.json()["quantity_flow_id"]),
            "source_area_id": production.area_id,
            "target_area_id": stockroom.area_id,
            "quantity": 4,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert stocked.status_code == 201, stocked.text
    allocated = client.post(
        "/api/allocations",
        json={
            "part_number": pn,
            "allocation_quantity": 4,
            "lines": [{"work_order_demand_id": demand_id, "quantity": 4}],
            "station_id": stockroom.station_id,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert allocated.status_code == 201, allocated.text

    resolution = _resolve(client, production, pn)
    assert resolution["intake_available"] is True
    # The Work Order is completed history: it is never reused, and the
    # receipt creates its own internal Work Order instead.
    assert resolution["internal_work_orders"] == []
    response = _receive(client, production, pn, 5)
    assert response.status_code == 201, response.text
    assert response.json()["work_order_reused"] is False
    assert response.json()["work_order_id"] != work_order_id


# ---------------------------------------------------------------------------
# Write-time revalidation — zero writes on stale or invalid input
# ---------------------------------------------------------------------------


def test_receipt_is_refused_at_a_terminal_area(client: TestClient, db_engine: Engine) -> None:
    stockroom = _Cell(client, is_terminal=True)
    pn = _unique("PN")

    response = _receive(client, stockroom, pn, 4)

    assert response.status_code == 409, response.text
    assert _counts(db_engine, pn)["movements"] == 0


def test_receipt_is_refused_on_the_area_context_of_a_rebound_station(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    elsewhere = _Cell(client)
    pn = _unique("PN")
    # The wizard was prepared against the Area the station was bound to
    # when the PN resolved — its Operation travels with the confirmed
    # intent.
    resolution = _resolve(client, cell, pn)
    assert resolution["intake_available"] is True
    payload = _receipt_payload(pn, 4, operation_id=resolution["operations"][0]["id"])
    rebound = client.patch(
        f"/api/scan-stations/{cell.station_id}", json={"area_id": elsewhere.area_id}
    )
    assert rebound.status_code == 200, rebound.text

    response = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)

    # The station belongs to another Area now, so the confirmed
    # Operation is not one of its Operations: nothing is recorded, and
    # the quantity never lands in an Area the operator never confirmed.
    assert response.status_code in {409, 422}, response.text
    assert _counts(db_engine, pn)["movements"] == 0


def test_receipt_is_refused_at_an_inactive_station(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    deactivated = client.patch(f"/api/scan-stations/{cell.station_id}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text

    response = _receive(client, cell, pn, 4)

    assert response.status_code == 409, response.text
    assert _counts(db_engine, pn)["movements"] == 0


def test_receipt_requires_an_explicit_operation_when_several_are_configured(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, operation_count=2)
    pn = _unique("PN")

    ambiguous = _receive(client, cell, pn, 4)
    assert ambiguous.status_code in {409, 422}, ambiguous.text
    assert _counts(db_engine, pn)["movements"] == 0

    chosen = _receive(client, cell, pn, 4, operation_id=cell.operation_ids[1])
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["operation_id"] == cell.operation_ids[1]


def test_receipt_refuses_an_operation_of_another_area(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    elsewhere = _Cell(client)
    pn = _unique("PN")

    response = _receive(client, cell, pn, 4, operation_id=elsewhere.operation_id)

    assert response.status_code in {409, 422}, response.text
    assert _counts(db_engine, pn)["movements"] == 0


def test_receipt_refuses_active_demand_that_appeared_meanwhile(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    payload = _receipt_payload(pn, 4)
    created = client.post(
        "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 9}]}
    )
    assert created.status_code == 201, created.text
    before = _counts(db_engine, pn)

    response = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)

    assert response.status_code == 409, response.text
    assert _counts(db_engine, pn) == before


def test_receipt_refuses_active_quantity_that_appeared_meanwhile(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    payload = _receipt_payload(pn, 4)
    assert _receive(client, cell, pn, 2).status_code == 201
    before = _counts(db_engine, pn)

    response = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)

    assert response.status_code == 409, response.text
    assert _counts(db_engine, pn) == before


@pytest.mark.parametrize(
    "override",
    [
        {"quantity": 0},
        {"quantity": -3},
        {"quantity": "4"},
        {"part_number": "AB CD"},
        {"part_number": ""},
        {"request_type": "REPAIR"},
        {"route_mode": "FIXED"},
        {"route_mode": "PLANNED"},
    ],
)
def test_invalid_input_records_nothing(
    client: TestClient, db_engine: Engine, override: dict[str, Any]
) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    payload = {**_receipt_payload(pn, 4), **override}

    response = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)

    assert response.status_code == 422, response.text
    pn = str(payload["part_number"]) or pn
    assert _counts(db_engine, pn) == {
        "flows": 0,
        "movements": 0,
        "demands": 0,
        "masters": 0,
        "requested": 0,
    }


# ---------------------------------------------------------------------------
# Idempotency (SLICE1_DATA_MODEL §14)
# ---------------------------------------------------------------------------


def test_replay_of_the_same_receipt_records_nothing_twice(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    payload = _receipt_payload(pn, 5)

    first = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)
    assert first.status_code == 201, first.text
    second = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)

    assert second.status_code == 200, second.text
    assert second.json()["movement_id"] == first.json()["movement_id"]
    assert second.json()["quantity_flow_id"] == first.json()["quantity_flow_id"]
    assert second.json()["work_order_demand_id"] == first.json()["work_order_demand_id"]
    counts = _counts(db_engine, pn)
    assert counts["movements"] == 1
    assert counts["flows"] == 1
    assert counts["demands"] == 1
    assert counts["requested"] == 5


def test_mismatched_reuse_of_a_receipt_id_is_refused(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    payload = _receipt_payload(pn, 5)
    assert (
        client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload).status_code
        == 201
    )

    response = client.post(
        f"/api/scan-stations/{cell.station_id}/receipts", json={**payload, "quantity": 6}
    )

    assert response.status_code == 409, response.text
    assert _counts(db_engine, pn)["movements"] == 1


def test_a_receipt_id_reused_by_another_command_is_refused(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    event_id = str(uuid.uuid4())
    first = _receive(client, cell, pn, 5, device_event_id=event_id)
    assert first.status_code == 201, first.text

    addition = client.post(
        f"/api/scan-stations/{cell.station_id}/quantity-additions",
        json={
            "part_number": pn,
            "quantity": 2,
            "reason": "found extra pieces",
            "device_event_id": event_id,
        },
    )

    assert addition.status_code == 409, addition.text
    assert _counts(db_engine, pn)["movements"] == 1


# ---------------------------------------------------------------------------
# Undo boundary and the derived state
# ---------------------------------------------------------------------------


def test_a_receipt_is_not_undoable_from_a_station(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    pn = _unique("PN")
    event_id = str(uuid.uuid4())
    assert _receive(client, cell, pn, 5, device_event_id=event_id).status_code == 201

    preview = client.get(f"/api/scan-stations/{cell.station_id}/undo-preview/{event_id}")
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"] is False
    assert "Work Order Demand" in preview.json()["ineligible_reason"]

    undone = client.post(
        f"/api/scan-stations/{cell.station_id}/undos",
        json={
            "part_number": pn,
            "reverses_device_event_id": event_id,
            "device_event_id": str(uuid.uuid4()),
        },
    )

    assert undone.status_code == 409, undone.text
    assert _counts(db_engine, pn)["movements"] == 1


def test_received_quantity_replays_from_movement_history(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, machine_count=1)
    pn = _unique("PN")
    response = _receive(client, cell, pn, 8)
    assert response.status_code == 201, response.text
    flow_id = int(response.json()["quantity_flow_id"])

    with Session(db_engine) as session:
        replayed = projections.rebuild_current_positions(session)
    assert replayed[flow_id].area_id == cell.area_id
    assert replayed[flow_id].machine_id is None
    assert replayed[flow_id].processing_state.value == "QUEUED"


def test_two_concurrent_receipts_of_one_part_number_have_one_serial_outcome(
    client: TestClient, db_engine: Engine
) -> None:
    """The per-PN advisory lock the release also takes serializes intents.

    Two receipts of the same PN, each its own confirmed intent, can
    never both introduce quantity: the second waits for the first and
    then sees the active quantity its own precondition forbids.
    """
    cell = _Cell(client)
    pn = _unique("PN")
    results: list[int] = []
    lock = threading.Lock()

    def submit() -> None:
        response = client.post(
            f"/api/scan-stations/{cell.station_id}/receipts", json=_receipt_payload(pn, 4)
        )
        with lock:
            results.append(response.status_code)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [201, 409]
    counts = _counts(db_engine, pn)
    assert counts["movements"] == 1
    assert counts["flows"] == 1
    assert counts["demands"] == 1


# ---------------------------------------------------------------------------
# `received_date` follows the SCAN (PROJECT_PROFILE §14)
# ---------------------------------------------------------------------------


def test_the_resolution_issues_the_scan_timestamp(client: TestClient) -> None:
    """The PN resolution carries the instant `Receive Quantity` records
    its received date from — the station never invents one."""
    cell = _Cell(client)
    before = datetime.datetime.now(datetime.UTC)
    resolution = _resolve(client, cell, _unique("PN"))
    after = datetime.datetime.now(datetime.UTC)
    scanned_at = datetime.datetime.fromisoformat(resolution["scanned_at"])
    assert scanned_at.tzinfo is not None
    assert before <= scanned_at <= after


def test_received_date_is_the_scan_day_even_when_the_confirmation_crosses_midnight(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PN scanned at 23:50 and confirmed at 00:10 the next day is
    received on the day it was SCANNED (§14 — `received_date` defaults
    to the scan timestamp), read on the site calendar."""
    cell = _Cell(client)
    pn = _unique("PN")
    zone = ZoneInfo("America/Los_Angeles")
    scanned_at = datetime.datetime(2026, 3, 9, 23, 50, tzinfo=zone)
    confirmed_at = datetime.datetime(2026, 3, 10, 0, 10, tzinfo=zone)
    monkeypatch.setattr(work_orders, "site_timezone", lambda: "America/Los_Angeles")
    monkeypatch.setattr(work_orders, "now", lambda: confirmed_at)

    response = _receive(client, cell, pn, 3, scanned_at=scanned_at.isoformat())
    assert response.status_code == 201, response.text
    detail = _work_order(client, int(response.json()["work_order_id"]))
    assert detail["received_date"] == "2026-03-09"
    # The server derives the date itself; the confirmation instant is
    # only the clock the validation compares against.
    assert work_orders.site_today() == datetime.date(2026, 3, 10)


def test_a_receipt_refuses_an_unusable_scan_timestamp(
    client: TestClient, db_engine: Engine
) -> None:
    """The instant travels through the station, so the server validates
    it: naive, in the future, or older than the intake scan window are
    each refused with zero writes."""
    cell = _Cell(client)
    now = datetime.datetime.now(datetime.UTC)
    cases = {
        "naive": now.replace(tzinfo=None).isoformat(),
        "future": (now + datetime.timedelta(minutes=5)).isoformat(),
        "stale": (now - intake.MAX_SCAN_AGE - datetime.timedelta(minutes=1)).isoformat(),
        "missing": None,
    }
    for label, value in cases.items():
        pn = _unique("PN")
        payload = _receipt_payload(pn, 4)
        if value is None:
            del payload["scanned_at"]
        else:
            payload["scanned_at"] = value
        response = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)
        assert response.status_code == 422, f"{label}: {response.text}"
        assert _counts(db_engine, pn) == {
            "flows": 0,
            "movements": 0,
            "demands": 0,
            "masters": 0,
            "requested": 0,
        }


def test_the_scan_timestamp_is_part_of_the_receipt_intent(
    client: TestClient, db_engine: Engine
) -> None:
    """The retry of a lost response replays under the SAME scan instant;
    the same id with a different scan is a different intent and is
    refused, so a retry can never silently move the received date."""
    cell = _Cell(client)
    pn = _unique("PN")
    payload = _receipt_payload(pn, 5)
    first = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)
    assert first.status_code == 201, first.text

    replay = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["movement_id"] == first.json()["movement_id"]

    moved = dict(payload)
    moved["scanned_at"] = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)
    ).isoformat()
    conflicting = client.post(f"/api/scan-stations/{cell.station_id}/receipts", json=moved)
    assert conflicting.status_code == 409, conflicting.text
    assert _counts(db_engine, pn)["movements"] == 1


# ---------------------------------------------------------------------------
# PN-level serialization — the shared lock, judged against real writers
# ---------------------------------------------------------------------------


def _pause_after_part_number_lock(
    monkeypatch: pytest.MonkeyPatch, module: Any, attribute: str
) -> tuple[threading.Event, threading.Event]:
    """Hold the shared PN-level lock inside ``module.attribute``.

    The seam is the shared lock helper itself: the real lock is taken,
    then the caller's transaction stops there and keeps holding it —
    exactly the window in which a concurrent `Receive Quantity` must
    NOT be able to judge its preconditions.
    """
    real = getattr(module, attribute)
    inside = threading.Event()
    release = threading.Event()

    def paused(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        if not inside.is_set():
            inside.set()
            assert release.wait(timeout=30), "test deadlock: never released"

    monkeypatch.setattr(module, attribute, paused)
    return inside, release


def _receipt_blocked_by(
    client: TestClient,
    cell: _Cell,
    pn: str,
    writer: "Callable[[], Any]",
    inside: threading.Event,
    release: threading.Event,
) -> tuple[Any, Any]:
    """Run ``writer`` up to the held PN lock, prove the receipt WAITS,
    then let both finish and return (writer result, receipt response)."""
    outcome: dict[str, Any] = {}
    writing = threading.Thread(target=lambda: outcome.update(writer=writer()))
    writing.start()
    assert inside.wait(timeout=30), "the writer never reached the PN lock"

    receiving = threading.Thread(
        target=lambda: outcome.update(receipt=_receive(client, cell, pn, 6))
    )
    receiving.start()
    receiving.join(timeout=1.5)
    # The receipt has not judged its preconditions: it is queued behind
    # the same PN-level lock the writer holds.
    assert receiving.is_alive(), "the receipt did not wait for the PN lock"

    release.set()
    writing.join(timeout=30)
    receiving.join(timeout=30)
    return outcome["writer"], outcome["receipt"]


def test_a_receipt_cannot_commit_beside_demand_created_meanwhile(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Work Order save that gives the PN its first demand line holds
    the shared PN lock: the receipt queues behind it and then sees the
    active demand its own entry condition forbids — the demand is never
    created beside a receipt that judged the PN as free."""
    cell = _Cell(client)
    pn = _unique("PN")
    inside, release = _pause_after_part_number_lock(
        monkeypatch, work_orders, "acquire_part_number_locks"
    )
    created, receipt = _receipt_blocked_by(
        client,
        cell,
        pn,
        lambda: client.post(
            "/api/work-orders",
            json={"lines": [{"part_number": pn, "requested_quantity": 7}]},
        ),
        inside,
        release,
    )
    assert created.status_code == 201, created.text
    assert receipt.status_code == 409, receipt.text
    assert "active Work Order Demand" in receipt.json()["detail"]
    counts = _counts(db_engine, pn)
    assert counts["movements"] == 0 and counts["flows"] == 0
    assert counts["demands"] == 1 and counts["requested"] == 7


def test_a_receipt_cannot_commit_beside_a_demand_line_raised_meanwhile(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raising a fully allocated line returns the PN's business shortage
    — the same threshold the receipt judges. The save holds the shared
    PN lock, the receipt waits, and is then refused."""
    production = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    cell = _Cell(client)
    pn = _unique("PN")
    work_order_id, demand_id = _internal_modify_candidate(client, production, stockroom, pn)
    before = _counts(db_engine, pn)
    inside, release = _pause_after_part_number_lock(
        monkeypatch, work_orders, "acquire_part_number_locks"
    )
    raised, receipt = _receipt_blocked_by(
        client,
        cell,
        pn,
        lambda: client.patch(
            f"/api/work-orders/{work_order_id}",
            json={"line_edits": [{"id": demand_id, "requested_quantity": 9}]},
        ),
        inside,
        release,
    )
    assert raised.status_code == 200, raised.text
    assert receipt.status_code == 409, receipt.text
    assert "active Work Order Demand" in receipt.json()["detail"]
    after = _counts(db_engine, pn)
    # The refused receipt introduced no production quantity at all.
    assert (after["movements"], after["flows"], after["demands"]) == (
        before["movements"],
        before["flows"],
        before["demands"],
    )


def test_a_receipt_cannot_commit_beside_an_allocation_reversed_meanwhile(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An allocation reversal gives the demand line its shortage back.
    It holds the shared PN lock, so the receipt cannot judge the PN as
    free while the reversal is in flight."""
    production = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    cell = _Cell(client)
    pn = _unique("PN")
    _internal_modify_candidate(client, production, stockroom, pn)
    before = _counts(db_engine, pn)
    listed = client.get("/api/allocations", params={"part_number": pn})
    assert listed.status_code == 200, listed.text
    allocation_id = int(listed.json()[0]["id"])

    inside, release = _pause_after_part_number_lock(
        monkeypatch, allocations, "acquire_part_number_lock"
    )
    reversed_row, receipt = _receipt_blocked_by(
        client,
        cell,
        pn,
        lambda: client.post(
            f"/api/allocations/{allocation_id}/reversals",
            json={
                "reason": "Wrong Work Order",
                "station_id": stockroom.station_id,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        inside,
        release,
    )
    assert reversed_row.status_code == 201, reversed_row.text
    assert receipt.status_code == 409, receipt.text
    assert "active Work Order Demand" in receipt.json()["detail"]
    after = _counts(db_engine, pn)
    # The refused receipt introduced no production quantity at all.
    assert (after["movements"], after["flows"], after["demands"]) == (
        before["movements"],
        before["flows"],
        before["demands"],
    )


def test_a_receipt_cannot_commit_beside_an_undo_reopening_quantity(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Undoing a Scrap reopens the flow it closed — the PN has active
    quantity again. The Undo holds the shared PN lock, the receipt
    waits, and is then refused against the reopened quantity."""
    production = _Cell(client, machine_count=1)
    stockroom = _Cell(client, is_terminal=True)
    cell = _Cell(client)
    pn = _unique("PN")
    scrapped_event = _scrapped_beside_a_settled_demand(client, production, stockroom, pn)

    inside, release = _pause_after_part_number_lock(monkeypatch, undo, "acquire_part_number_lock")
    reversal, receipt = _receipt_blocked_by(
        client,
        cell,
        pn,
        lambda: client.post(
            f"/api/scan-stations/{production.station_id}/undos",
            json={
                "part_number": pn,
                "reverses_device_event_id": scrapped_event,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        inside,
        release,
    )
    assert reversal.status_code == 201, reversal.text
    assert receipt.status_code == 409, receipt.text
    assert "active production quantity" in receipt.json()["detail"]
    # Only the setup's own flows exist: the refused receipt created none.
    with Session(db_engine) as session:
        active = session.scalars(
            sa.select(models.QuantityFlow.quantity).where(
                models.QuantityFlow.part_number == pn,
                models.QuantityFlow.status == "ACTIVE",
            )
        ).all()
    assert sorted(active) == [3]
