"""Integration tests for the Phase 6 Machine-Area processing commands.

Exercises the full request path — FastAPI routes, the Application
commands and read models, and PostgreSQL — against a dedicated temporary
database migrated to head by the real Alembic chain. Covered per
IMPLEMENTATION_ROADMAP Phase 6, PROJECT_PROFILE §7 Area Completion,
§8.6, §8.11, §12 and §15:

- derived processing state in the read models: QUEUED after arrival,
  ON_MACHINE after assignment, READY_TO_TRANSFER after DONE — a NULL
  Machine is never "queued" by itself;
- assign: exactly one immutable ``ASSIGNED_TO_MACHINE`` with the
  canonical shape, the Machine projection set in the same transaction,
  the Machine derived Running with ``state_changed_at`` moved exactly
  when the state changes, several PNs on one Machine, and NO
  auto-assignment in a single-Machine Area;
- assign refusals with ZERO writes: retired, other-Area and maintenance
  Machines, unknown Machine, flow not in the station's Area, inactive
  station, ON_MACHINE and READY_TO_TRANSFER flows, exceeding
  quantity, PN mismatch;
- QUEUE: ``RELEASED_FROM_MACHINE`` clears the Machine and derives
  QUEUED (never completed); DONE: ``AREA_COMPLETED`` clears the Machine,
  keeps the Area and derives READY_TO_TRANSFER; both refuse queued or
  finished quantity and a stale Machine precondition;
- the implicit completion: a transfer of ON_MACHINE quantity appends
  ``AREA_COMPLETED`` + ``TRANSFERRED`` as ONE command under ONE
  ``device_event_id`` (command_sequence 1, 2); finished quantity
  transfers with ``TRANSFERRED`` alone;
- idempotency per command: replay after the state moved on, mismatched
  reuse (also across command kinds), race lost at COMMIT;
- Machine retirement blocked while quantity is assigned, allowed after
  DONE; maintenance may start while assigned, DONE under maintenance
  keeps the Maintenance state age;
- concurrency: two assignments of one flow, assign versus retirement
  (both arrival orders), DONE versus transfer of one flow — each with
  exactly one serial outcome;
- the projection replay rebuilds Area, Machine and state from history;
- Phase 6 read models: Machine barcode resolution (``PF:MACHINE:``,
  manual Asset Tag; unknown, PN, retired, other-Area, maintenance
  refused) into the one-shot Machine-first context with the queued
  flows of the Area and no sticky state; PN-first ``available_actions``
  per derived state and ``requires_selection`` on ambiguity; the Area
  inventory split into queued / per-Machine (ON_MACHINE only) /
  finished with Machine cards reconciling with ``/api/machines``.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations; the module database is dropped afterwards.
"""

import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
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
from app.application import (
    machine_processing,
    machines,
    production_release,
    projections,
    transfers,
)
from app.application.errors import ConflictError, IdempotencyConflictError
from app.core.config import get_settings
from app.domain.enums import MovementType, ProcessingState
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_machine_processing_api"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
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
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    """Machine creation requires the configured Asset Tag format."""
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
    payload = {"department_id": department.json()["id"], "name": _unique("AREA"), **overrides}
    response = client.post("/api/areas", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_operation(client: TestClient, area_id: int) -> dict[str, Any]:
    response = client.post("/api/operations", json={"area_id": area_id, "code": _unique("OP")})
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


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


def _machine(client: TestClient, machine_id: int) -> dict[str, Any]:
    response = client.get(f"/api/machines/{machine_id}")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


class _Cell:
    """An Area with one Operation, one Scan Station and ``machine_count`` Machines."""

    def __init__(self, client: TestClient, *, machine_count: int = 1) -> None:
        self.area = _create_area(client)
        self.area_id = int(self.area["id"])
        self.operation_id = int(_create_operation(client, self.area_id)["id"])
        self.station_id = _create_station(client, self.area_id)
        self.machine_ids = [_create_machine(client, self.area_id) for _ in range(machine_count)]

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


def _release(
    client: TestClient, cell: _Cell, *, quantity: int = 25, part_number: str | None = None
) -> tuple[int, str]:
    """Release one FLOATING flow into the cell's Area: (quantity_flow_id, pn).

    A second release of the same PN confirms the existing active
    quantity (SLICE1 §8.2) and yields a second flow of that PN."""
    pn = part_number or _unique("PN")
    response = client.post(
        "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
    )
    assert response.status_code == 201, response.text
    work_order_id = int(response.json()["id"])
    demand_id = int(response.json()["demands"][0]["id"])
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": quantity,
            "route_mode": "FLOATING",
            "starting_area_id": cell.area_id,
            "operation_id": cell.operation_id,
            "confirm_active_quantity": part_number is not None,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert released.status_code == 201, released.text
    return int(released.json()["quantity_flow_id"]), str(released.json()["part_number"])


_ACTION_PATHS = {
    "ASSIGN": "machine-assignments",
    "QUEUE": "machine-releases",
    "DONE": "area-completions",
}


def _act(
    client: TestClient,
    kind: str,
    station_id: str,
    *,
    part_number: str,
    quantity_flow_id: int,
    machine_id: int,
    quantity: Any,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": part_number,
        "quantity_flow_id": quantity_flow_id,
        "machine_id": machine_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return client.post(f"/api/scan-stations/{station_id}/{_ACTION_PATHS[kind]}", json=payload)


def _assign(
    client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: Any, **kw: Any
) -> Any:
    return _act(
        client,
        "ASSIGN",
        cell.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=kw.pop("machine_id", cell.machine_id),
        quantity=quantity,
        **kw,
    )


def _assigned(client: TestClient, cell: _Cell, quantity: int = 25) -> tuple[int, str]:
    """A released flow already ON the cell's first Machine."""
    flow_id, pn = _release(client, cell, quantity=quantity)
    response = _assign(client, cell, flow_id, pn, quantity)
    assert response.status_code == 201, response.text
    return flow_id, pn


def _transfer(
    client: TestClient,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: int,
    **kw: Any,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return client.post(f"/api/scan-stations/{target.station_id}/transfers", json=payload)


def _flow_row(engine: Engine, flow_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.QuantityFlow.__table__).where(
                models.QuantityFlow.__table__.c.id == flow_id
            )
        ).one()


def _movement_row(engine: Engine, movement_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.PartMovement.__table__).where(
                models.PartMovement.__table__.c.id == movement_id
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


def _movement_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.count()).select_from(models.PartMovement.__table__)
            ).scalar_one()
        )


def _inventory_flow(client: TestClient, area_id: int, flow_id: int) -> dict[str, Any]:
    response = client.get(f"/api/areas/{area_id}/inventory")
    assert response.status_code == 200, response.text
    for line in response.json()["lines"]:
        for flow in line["flows"]:
            if flow["quantity_flow_id"] == flow_id:
                return cast(dict[str, Any], flow)
    raise AssertionError(f"flow {flow_id} not in Area {area_id} inventory")


def _assert_replay_matches(engine: Engine, flow_id: int) -> None:
    with Session(engine) as session:
        position = projections.rebuild_current_positions(session)[flow_id]
    row = _flow_row(engine, flow_id)
    assert (position.area_id, position.machine_id) == (row.current_area_id, row.current_machine_id)


class _Pause:
    """Test seam: wrap a callable so its FIRST call pauses after
    completing — while the caller holds whatever locks it took — until
    released; every later call passes straight through."""

    def __init__(self, real: Callable[..., Any]) -> None:
        self.real = real
        self.first_inside = threading.Event()
        self.let_first_finish = threading.Event()
        self._guard = threading.Lock()
        self._paused_once = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self.real(*args, **kwargs)
        with self._guard:
            should_pause = not self._paused_once
            self._paused_once = True
        if should_pause:
            self.first_inside.set()
            assert self.let_first_finish.wait(timeout=20), "test deadlock: never released"
        return result


def _run_collecting(results: dict[str, Any], name: str, action: Callable[[], Any]) -> None:
    try:
        results[name] = action()
    except Exception as exc:  # noqa: BLE001 — collected for assertions
        results[name] = exc


# ---------------------------------------------------------------------------
# Derived processing state in the read models
# ---------------------------------------------------------------------------


def test_released_quantity_is_queued_and_never_auto_assigned(
    client: TestClient, db_engine: Engine
) -> None:
    """One Machine behaves exactly like several: arrival queues, nothing assigns."""
    lathe = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, lathe, quantity=12)

    flow = _inventory_flow(client, lathe.area_id, flow_id)
    assert flow["processing_state"] == "QUEUED"
    assert flow["machine_id"] is None
    assert _flow_row(db_engine, flow_id).current_machine_id is None
    machine = _machine(client, lathe.machine_id)
    assert machine["operational_state"] == "IDLE"
    assert machine["assigned_quantity"] == 0
    resolved = client.post(
        f"/api/scan-stations/{lathe.station_id}/scans/resolve", json={"part_number": pn}
    )
    assert resolved.json()["in_area"][0]["processing_state"] == "QUEUED"
    assert client.get(f"/api/scan-stations/{lathe.station_id}/context").json()["has_machines"]


# ---------------------------------------------------------------------------
# Assign
# ---------------------------------------------------------------------------


def test_assign_records_the_canonical_movement_and_projection(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=8)
    before = _machine(client, lathe.machine_id)
    event_id = str(uuid.uuid4())

    response = _assign(client, lathe, flow_id, pn, 8, device_event_id=event_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_type"] == "ASSIGNED_TO_MACHINE"
    assert body["processing_state"] == "ON_MACHINE"
    assert body["machine_id"] == lathe.machine_id
    assert body["area_id"] == lathe.area_id
    assert body["operation_id"] == lathe.operation_id
    assert body["station_id"] == lathe.station_id
    assert body["device_event_id"] == event_id

    row = _movement_row(db_engine, body["movement_id"])
    assert row.movement_type == "ASSIGNED_TO_MACHINE"
    assert row.from_area_id == lathe.area_id and row.to_area_id == lathe.area_id
    assert row.destination_machine_id == lathe.machine_id and row.source_machine_id is None
    assert row.operation_id == lathe.operation_id  # carried from the RECEIVED
    assert row.assigned_route_step_id is None
    assert row.station_id == lathe.station_id
    assert row.quantity == 8 and row.command_sequence == 1
    assert row.metadata["command"] == {"kind": "ASSIGN", "size": 1}
    assert row.metadata["request_fingerprint"]

    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_machine_id == lathe.machine_id
    assert flow_row.current_area_id == lathe.area_id
    flow = _inventory_flow(client, lathe.area_id, flow_id)
    assert flow["processing_state"] == "ON_MACHINE" and flow["machine_id"] == lathe.machine_id
    _assert_replay_matches(db_engine, flow_id)

    machine = _machine(client, lathe.machine_id)
    assert machine["operational_state"] == "RUNNING"
    assert machine["assigned_quantity"] == 8
    assert machine["state_changed_at"] > before["state_changed_at"]  # Idle → Running


def test_one_machine_holds_several_part_numbers(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client)
    first_id, first_pn = _assigned(client, lathe, quantity=5)
    running_since = _machine(client, lathe.machine_id)["state_changed_at"]
    second_id, second_pn = _release(client, lathe, quantity=7)

    response = _assign(client, lathe, second_id, second_pn, 7)
    assert response.status_code == 201, response.text
    machine = _machine(client, lathe.machine_id)
    assert machine["assigned_quantity"] == 12
    assert machine["operational_state"] == "RUNNING"
    # Already Running: the state age does NOT restart on the second PN.
    assert machine["state_changed_at"] == running_since
    assert _flow_row(db_engine, first_id).current_machine_id == lathe.machine_id
    assert _flow_row(db_engine, second_id).current_machine_id == lathe.machine_id
    assert first_pn != second_pn


def test_assign_refuses_retired_other_area_and_maintenance_machines(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=2)
    other = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=3)
    retired_id = lathe.machine_ids[1]
    assert client.post(f"/api/machines/{retired_id}/retire", json={}).status_code == 200
    maintained_id = _create_machine(client, lathe.area_id)
    assert client.post(f"/api/machines/{maintained_id}/maintenance", json={}).status_code == 201
    count = _movement_count(db_engine)

    retired = _assign(client, lathe, flow_id, pn, 3, machine_id=retired_id)
    assert retired.status_code == 409 and "retired" in retired.json()["detail"]
    elsewhere = _assign(client, lathe, flow_id, pn, 3, machine_id=other.machine_id)
    assert elsewhere.status_code == 409 and "another Area" in elsewhere.json()["detail"]
    maintained = _assign(client, lathe, flow_id, pn, 3, machine_id=maintained_id)
    assert maintained.status_code == 409 and "maintenance" in maintained.json()["detail"]
    unknown = _assign(client, lathe, flow_id, pn, 3, machine_id=-1)
    assert unknown.status_code == 422

    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id is None


def test_assign_refuses_wrong_station_state_quantity_and_pn(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    other = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=10)
    count = _movement_count(db_engine)

    # The flow is not in the other station's Area.
    elsewhere = _act(
        client,
        "ASSIGN",
        other.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=other.machine_id,
        quantity=10,
    )
    assert elsewhere.status_code == 409 and "not in the Area" in elsewhere.json()["detail"]
    # Exceeding quantity: refused, never written (a smaller quantity
    # splits the flow first — Phase 8, test_quantity_split_merge_api).
    exceeding = _assign(client, lathe, flow_id, pn, 11)
    assert exceeding.status_code == 422 and "exceeds" in exceeding.json()["detail"]
    for bad in (0, -1, 2.5, "10", True):
        assert _assign(client, lathe, flow_id, pn, bad).status_code == 422
    # PN mismatch and malformed idempotency key.
    assert _assign(client, lathe, flow_id, "OTHER-PN", 10).status_code == 422
    assert _assign(client, lathe, flow_id, pn, 10, device_event_id="nope").status_code == 422
    unknown_flow = _assign(client, lathe, -1, pn, 10)
    assert unknown_flow.status_code == 422
    # Inactive station.
    inactive_station = _create_station(client, lathe.area_id)
    assert (
        client.patch(
            f"/api/scan-stations/{inactive_station}", json={"is_active": False}
        ).status_code
        == 200
    )
    inactive = _act(
        client,
        "ASSIGN",
        inactive_station,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=10,
    )
    assert inactive.status_code == 409 and "inactive" in inactive.json()["detail"]
    assert (
        _act(
            client,
            "ASSIGN",
            "NO-SUCH-STATION",
            part_number=pn,
            quantity_flow_id=flow_id,
            machine_id=lathe.machine_id,
            quantity=10,
        ).status_code
        == 404
    )

    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id is None
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "QUEUED"


def test_assign_refuses_quantity_already_on_a_machine_or_finished(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=2)
    flow_id, pn = _assigned(client, lathe, quantity=6)
    count = _movement_count(db_engine)

    again = _assign(client, lathe, flow_id, pn, 6, machine_id=lathe.machine_ids[1])
    assert again.status_code == 409 and "already on a Machine" in again.json()["detail"]
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id

    done = _act(
        client,
        "DONE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=6,
    )
    assert done.status_code == 201, done.text
    count = _movement_count(db_engine)
    finished = _assign(client, lathe, flow_id, pn, 6)
    assert finished.status_code == 409 and "DONE" in finished.json()["detail"]
    assert _movement_count(db_engine) == count
    # A NULL Machine here means finished, not queued.
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == (
        "READY_TO_TRANSFER"
    )


# ---------------------------------------------------------------------------
# QUEUE and DONE
# ---------------------------------------------------------------------------


def test_queue_returns_unfinished_quantity_to_the_queue(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=9)
    running_since = _machine(client, lathe.machine_id)["state_changed_at"]

    response = _act(
        client,
        "QUEUE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=9,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_type"] == "RELEASED_FROM_MACHINE"
    assert body["processing_state"] == "QUEUED"
    row = _movement_row(db_engine, body["movement_id"])
    assert row.source_machine_id == lathe.machine_id and row.destination_machine_id is None
    assert row.from_area_id == lathe.area_id and row.to_area_id == lathe.area_id
    assert row.metadata["command"] == {"kind": "QUEUE", "size": 1}

    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_machine_id is None and flow_row.current_area_id == lathe.area_id
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "QUEUED"
    _assert_replay_matches(db_engine, flow_id)
    machine = _machine(client, lathe.machine_id)
    assert machine["operational_state"] == "IDLE" and machine["assigned_quantity"] == 0
    assert machine["state_changed_at"] > running_since  # Running → Idle

    # Queued again means assignable again — never completed.
    assert _assign(client, lathe, flow_id, pn, 9).status_code == 201
    types = [movement.movement_type for movement in _movements(db_engine, flow_id)]
    assert types == [
        "RECEIVED",
        "ASSIGNED_TO_MACHINE",
        "RELEASED_FROM_MACHINE",
        "ASSIGNED_TO_MACHINE",
    ]


def test_done_completes_processing_and_keeps_the_area(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=4)

    response = _act(
        client,
        "DONE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=4,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_type"] == "AREA_COMPLETED"
    assert body["processing_state"] == "READY_TO_TRANSFER"
    row = _movement_row(db_engine, body["movement_id"])
    assert row.source_machine_id == lathe.machine_id and row.destination_machine_id is None
    assert row.from_area_id == lathe.area_id and row.to_area_id == lathe.area_id
    assert row.operation_id == lathe.operation_id and row.command_sequence == 1
    assert row.metadata["command"] == {"kind": "DONE", "size": 1}

    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_machine_id is None
    assert flow_row.current_area_id == lathe.area_id  # the Area stays the location
    assert flow_row.status == "ACTIVE"  # DONE is never PN/flow completion
    flow = _inventory_flow(client, lathe.area_id, flow_id)
    assert flow["processing_state"] == "READY_TO_TRANSFER" and flow["machine_id"] is None
    _assert_replay_matches(db_engine, flow_id)
    machine = _machine(client, lathe.machine_id)
    assert machine["operational_state"] == "IDLE" and machine["assigned_quantity"] == 0

    # Finished quantity is neither on a Machine to QUEUE nor assignable.
    count = _movement_count(db_engine)
    queue = _act(
        client,
        "QUEUE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=4,
    )
    assert queue.status_code == 409 and "DONE" in queue.json()["detail"]
    assert _movement_count(db_engine) == count


@pytest.mark.parametrize("kind", ["QUEUE", "DONE"])
def test_queue_and_done_refuse_queued_quantity_and_a_stale_machine(
    client: TestClient, db_engine: Engine, kind: str
) -> None:
    lathe = _Cell(client, machine_count=2)
    flow_id, pn = _release(client, lathe, quantity=2)
    count = _movement_count(db_engine)

    queued = _act(
        client,
        kind,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=2,
    )
    assert queued.status_code == 409 and "queued" in queued.json()["detail"]

    assert _assign(client, lathe, flow_id, pn, 2).status_code == 201
    count = _movement_count(db_engine)
    stale = _act(
        client,
        kind,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_ids[1],
        quantity=2,
    )
    assert stale.status_code == 409 and "not on the selected Machine" in stale.json()["detail"]
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id


def test_done_and_queue_stay_allowed_under_maintenance(
    client: TestClient, db_engine: Engine
) -> None:
    """Maintenance may start while quantity is assigned and never moves
    it; leaving the Machine under maintenance is allowed and keeps the
    Maintenance state age (the derived state does not change)."""
    lathe = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=3)
    started = client.post(f"/api/machines/{lathe.machine_id}/maintenance", json={"note": "belt"})
    assert started.status_code == 201, started.text
    assert started.json()["operational_state"] == "MAINTENANCE"
    assert started.json()["assigned_quantity"] == 3
    maintenance_since = started.json()["state_changed_at"]
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id

    done = _act(
        client,
        "DONE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=3,
    )
    assert done.status_code == 201, done.text
    machine = _machine(client, lathe.machine_id)
    assert machine["operational_state"] == "MAINTENANCE"
    assert machine["assigned_quantity"] == 0
    assert machine["state_changed_at"] == maintenance_since

    cleared = client.delete(f"/api/machines/{lathe.machine_id}/maintenance")
    assert cleared.status_code == 200 and cleared.json()["operational_state"] == "IDLE"


def test_clearing_maintenance_returns_to_running_when_quantity_is_assigned(
    client: TestClient,
) -> None:
    lathe = _Cell(client)
    _assigned(client, lathe, quantity=3)
    assert client.post(f"/api/machines/{lathe.machine_id}/maintenance", json={}).status_code == 201
    cleared = client.delete(f"/api/machines/{lathe.machine_id}/maintenance")
    assert cleared.status_code == 200
    assert cleared.json()["operational_state"] == "RUNNING"
    assert cleared.json()["assigned_quantity"] == 3


# ---------------------------------------------------------------------------
# Transfer from ON_MACHINE: one command, two Movements
# ---------------------------------------------------------------------------


def test_transfer_of_on_machine_quantity_completes_implicitly_in_one_command(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=15)
    running_since = _machine(client, lathe.machine_id)["state_changed_at"]
    event_id = str(uuid.uuid4())
    resolved = client.post(
        f"/api/scan-stations/{deburr.station_id}/scans/resolve", json={"part_number": pn}
    )
    candidate = resolved.json()["candidates"][0]
    assert candidate["processing_state"] == "ON_MACHINE"
    assert candidate["machine_id"] == lathe.machine_id

    response = _transfer(client, lathe, deburr, flow_id, pn, 15, device_event_id=event_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["completed_movement_id"] is not None
    assert body["completed_machine_id"] == lathe.machine_id
    assert body["from_area_id"] == lathe.area_id and body["to_area_id"] == deburr.area_id

    history = _movements(db_engine, flow_id)
    assert [m.movement_type for m in history] == [
        "RECEIVED",
        "ASSIGNED_TO_MACHINE",
        "AREA_COMPLETED",
        "TRANSFERRED",
    ]
    completed, transferred = history[2], history[3]
    assert completed.id == body["completed_movement_id"]
    assert transferred.id == body["movement_id"]
    assert completed.id < transferred.id
    # One command: one device_event_id, sequence 1 then 2, same fingerprint.
    assert completed.device_event_id == transferred.device_event_id == event_id
    assert (completed.command_sequence, transferred.command_sequence) == (1, 2)
    assert completed.metadata["command"] == {"kind": "TRANSFER", "size": 2}
    assert transferred.metadata["command"] == {"kind": "TRANSFER", "size": 2}
    assert completed.metadata["request_fingerprint"] == transferred.metadata["request_fingerprint"]
    # The completion happens inside the source Area at the source Machine.
    assert completed.from_area_id == completed.to_area_id == lathe.area_id
    assert completed.source_machine_id == lathe.machine_id
    assert completed.operation_id == lathe.operation_id
    assert completed.station_id == deburr.station_id
    assert transferred.source_machine_id is None and transferred.destination_machine_id is None
    assert transferred.operation_id == deburr.operation_id

    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_area_id == deburr.area_id and flow_row.current_machine_id is None
    assert _inventory_flow(client, deburr.area_id, flow_id)["processing_state"] == "QUEUED"
    _assert_replay_matches(db_engine, flow_id)
    machine = _machine(client, lathe.machine_id)
    assert machine["operational_state"] == "IDLE" and machine["assigned_quantity"] == 0
    assert machine["state_changed_at"] > running_since

    # The whole command replays as one: identical result, nothing added.
    count = _movement_count(db_engine)
    replay = _transfer(client, lathe, deburr, flow_id, pn, 15, device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json() == body
    assert _movement_count(db_engine) == count
    # A different intent under the same id is refused.
    mismatch = _transfer(client, lathe, deburr, flow_id, pn, 14, device_event_id=event_id)
    assert mismatch.status_code == 409
    assert _movement_count(db_engine) == count


def test_finished_quantity_transfers_with_transferred_alone(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=5)
    done = _act(
        client,
        "DONE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=5,
    )
    assert done.status_code == 201, done.text

    response = _transfer(client, lathe, deburr, flow_id, pn, 5)
    assert response.status_code == 201, response.text
    assert response.json()["completed_movement_id"] is None
    assert response.json()["completed_machine_id"] is None
    history = _movements(db_engine, flow_id)
    assert [m.movement_type for m in history] == [
        "RECEIVED",
        "ASSIGNED_TO_MACHINE",
        "AREA_COMPLETED",
        "TRANSFERRED",
    ]
    assert history[-1].command_sequence == 1
    assert history[-1].device_event_id != history[-2].device_event_id
    _assert_replay_matches(db_engine, flow_id)


def test_partial_transfer_of_on_machine_quantity_splits_and_leaves_the_rest_on_the_machine(
    client: TestClient, db_engine: Engine
) -> None:
    """Phase 8: only the selected part completes and moves; the remainder
    stays ON_MACHINE (full lineage coverage in test_quantity_split_merge_api)."""
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=20)
    count = _movement_count(db_engine)
    response = _transfer(client, lathe, deburr, flow_id, pn, 10)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["completed_machine_id"] == lathe.machine_id
    assert body["source_quantity_flow_id"] == flow_id and body["remainder_quantity"] == 10
    assert _movement_count(db_engine) == count + 5
    assert _flow_row(db_engine, flow_id).status == "SPLIT"
    remainder = _flow_row(db_engine, body["remainder_quantity_flow_id"])
    assert remainder.current_area_id == lathe.area_id
    assert remainder.current_machine_id == lathe.machine_id
    moved = _flow_row(db_engine, body["quantity_flow_id"])
    assert moved.current_area_id == deburr.area_id and moved.current_machine_id is None
    assert _machine(client, lathe.machine_id)["operational_state"] == "RUNNING"


def test_implicit_completion_is_all_or_nothing(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after both Movements are staged leaves neither behind."""
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=2)
    count = _movement_count(db_engine)

    def explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated failure before COMMIT")

    monkeypatch.setattr(transfers, "note_assignment_change", explode)
    with pytest.raises(RuntimeError), Session(db_engine) as session:
        transfers.transfer_to_station_area(
            session,
            station_id=deburr.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            source_area_id=lathe.area_id,
            target_area_id=deburr.area_id,
            quantity=2,
            operation_id=None,
            confirm_route_deviation=False,
            route_deviation_reason=None,
            device_event_id=str(uuid.uuid4()),
        )
    assert _movement_count(db_engine) == count
    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_area_id == lathe.area_id
    assert flow_row.current_machine_id == lathe.machine_id


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["ASSIGN", "QUEUE", "DONE"])
def test_replay_returns_the_original_result_after_the_state_moved_on(
    client: TestClient, db_engine: Engine, kind: str
) -> None:
    lathe = _Cell(client)
    if kind == "ASSIGN":
        flow_id, pn = _release(client, lathe, quantity=6)
    else:
        flow_id, pn = _assigned(client, lathe, quantity=6)
    event_id = str(uuid.uuid4())
    original = _act(
        client,
        kind,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=6,
        device_event_id=event_id,
    )
    assert original.status_code == 201, original.text

    # Move the flow on with a NEW intent, then retry the old submission.
    follow_up = "QUEUE" if kind == "ASSIGN" else "ASSIGN"
    if kind == "DONE":
        deburr = _Cell(client)
        assert _transfer(client, lathe, deburr, flow_id, pn, 6).status_code == 201
    else:
        moved = _act(
            client,
            follow_up,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            machine_id=lathe.machine_id,
            quantity=6,
        )
        assert moved.status_code == 201, moved.text
    count = _movement_count(db_engine)

    replay = _act(
        client,
        kind,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=6,
        device_event_id=event_id,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == original.json()
    assert _movement_count(db_engine) == count


def test_mismatched_reuse_is_refused_within_and_across_command_kinds(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=2)
    deburr = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=6)
    event_id = str(uuid.uuid4())
    assert _assign(client, lathe, flow_id, pn, 6, device_event_id=event_id).status_code == 201
    count = _movement_count(db_engine)

    # Same kind, different Machine.
    other_machine = _assign(
        client, lathe, flow_id, pn, 6, machine_id=lathe.machine_ids[1], device_event_id=event_id
    )
    assert other_machine.status_code == 409 and "different" in other_machine.json()["detail"]
    # Another kind reusing the assignment's id.
    for kind in ("QUEUE", "DONE"):
        reused = _act(
            client,
            kind,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            machine_id=lathe.machine_id,
            quantity=6,
            device_event_id=event_id,
        )
        assert reused.status_code == 409, reused.text
    # A transfer reusing the assignment's id.
    transfer = _transfer(client, lathe, deburr, flow_id, pn, 6, device_event_id=event_id)
    assert transfer.status_code == 409
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id

    # And an assignment reusing a two-Movement transfer command's id.
    transfer_id = str(uuid.uuid4())
    assert (
        _transfer(client, lathe, deburr, flow_id, pn, 6, device_event_id=transfer_id).status_code
        == 201
    )
    count = _movement_count(db_engine)
    reused_transfer = _act(
        client,
        "ASSIGN",
        deburr.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=deburr.machine_id,
        quantity=6,
        device_event_id=transfer_id,
    )
    assert reused_transfer.status_code == 409
    assert _movement_count(db_engine) == count


def test_release_ids_and_machine_command_ids_never_replay_each_other(
    client: TestClient, db_engine: Engine
) -> None:
    """A ``device_event_id`` first used by the Phase 4 release (``RECEIVED``)
    is a conflicting reuse for every Machine-Area command, and a Machine
    command's id is a conflicting reuse for a release — the command kind
    is compared explicitly, never inferred from the fingerprint."""
    lathe = _Cell(client)
    pn = _unique("PN")
    response = client.post(
        "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
    )
    assert response.status_code == 201, response.text
    work_order_id = int(response.json()["id"])
    demand_id = int(response.json()["demands"][0]["id"])
    release_url = f"/api/work-orders/{work_order_id}/demands/{demand_id}/release"
    release_id = str(uuid.uuid4())
    release_payload = {
        "part_number": pn,
        "quantity": 4,
        "route_mode": "FLOATING",
        "starting_area_id": lathe.area_id,
        "operation_id": lathe.operation_id,
        "confirm_active_quantity": False,
        "device_event_id": release_id,
    }
    released = client.post(release_url, json=release_payload)
    assert released.status_code == 201, released.text
    flow_id = int(released.json()["quantity_flow_id"])
    count = _movement_count(db_engine)

    reused = _assign(client, lathe, flow_id, pn, 4, device_event_id=release_id)
    assert reused.status_code == 409, reused.text
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id is None

    assign_id = str(uuid.uuid4())
    assert _assign(client, lathe, flow_id, pn, 4, device_event_id=assign_id).status_code == 201
    count = _movement_count(db_engine)
    for kind in ("QUEUE", "DONE"):
        reused = _act(
            client,
            kind,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            machine_id=lathe.machine_id,
            quantity=4,
            device_event_id=release_id,
        )
        assert reused.status_code == 409, reused.text
    # The release side: the assignment's id (a different fingerprint) and,
    # explicitly, a Machine command whose id is replayed as a release.
    replayed_release = client.post(
        release_url, json={**release_payload, "confirm_active_quantity": True}
    )
    assert replayed_release.status_code == 200, replayed_release.text
    reused_release = client.post(
        release_url,
        json={**release_payload, "confirm_active_quantity": True, "device_event_id": assign_id},
    )
    assert reused_release.status_code == 409, reused_release.text
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id


def test_release_replay_checks_the_command_kind_explicitly() -> None:
    """Unit seam: a committed row of another command kind whose stored
    fingerprint text happens to equal the release fingerprint is still a
    conflicting reuse — the guard is the Movement type, not the
    fingerprint's key set."""
    fingerprint = "same-text"
    foreign = models.PartMovement(
        movement_type=MovementType.ASSIGNED_TO_MACHINE,
        metadata_={production_release._FINGERPRINT_KEY: fingerprint},
    )
    with pytest.raises(IdempotencyConflictError):
        production_release._replay_or_conflict(cast(Session, None), foreign, fingerprint)


def test_leaving_quantity_keeps_running_while_another_pn_remains(
    client: TestClient, db_engine: Engine
) -> None:
    """``state_changed_at`` moves only when the derived state changes:
    QUEUE / DONE of one PN while another PN stays on the Machine leaves
    the Machine Running with the same state age; the last quantity
    leaving turns it Idle and moves the timestamp."""
    lathe = _Cell(client)
    first_id, first_pn = _assigned(client, lathe, quantity=3)
    running_since = _machine(client, lathe.machine_id)["state_changed_at"]
    second_id, second_pn = _release(client, lathe, quantity=8)
    assert _assign(client, lathe, second_id, second_pn, 8).status_code == 201
    machine = _machine(client, lathe.machine_id)
    assert (machine["operational_state"], machine["assigned_quantity"]) == ("RUNNING", 11)
    assert machine["state_changed_at"] == running_since

    queued = _act(
        client,
        "QUEUE",
        lathe.station_id,
        part_number=first_pn,
        quantity_flow_id=first_id,
        machine_id=lathe.machine_id,
        quantity=3,
    )
    assert queued.status_code == 201, queued.text
    machine = _machine(client, lathe.machine_id)
    assert (machine["operational_state"], machine["assigned_quantity"]) == ("RUNNING", 8)
    assert machine["state_changed_at"] == running_since

    done = _act(
        client,
        "DONE",
        lathe.station_id,
        part_number=second_pn,
        quantity_flow_id=second_id,
        machine_id=lathe.machine_id,
        quantity=8,
    )
    assert done.status_code == 201, done.text
    machine = _machine(client, lathe.machine_id)
    assert (machine["operational_state"], machine["assigned_quantity"]) == ("IDLE", 0)
    assert machine["state_changed_at"] > running_since
    assert _flow_row(db_engine, first_id).current_machine_id is None
    assert _flow_row(db_engine, second_id).current_machine_id is None


def test_done_race_lost_at_commit_replays(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The COMMIT-time duplicate resolution covers the leaving commands
    too: a blinded retry of a committed DONE replays it (200, same body)
    and leaves no second Movement and no projection change behind."""
    lathe = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=5)
    event_id = str(uuid.uuid4())
    real_lookup = machine_processing.committed_command
    misses = {"remaining": 2}

    def blind_then_real(session: Session, device_event_id: str) -> Any:
        if misses["remaining"] > 0:
            misses["remaining"] -= 1
            return []
        return real_lookup(session, device_event_id)

    def done() -> Any:
        return _act(
            client,
            "DONE",
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            machine_id=lathe.machine_id,
            quantity=5,
            device_event_id=event_id,
        )

    original = done()
    assert original.status_code == 201, original.text
    # Put the flow back on the Machine with NEW intents so the blinded
    # retry passes validation and reaches COMMIT.
    deburr = _Cell(client)
    assert _transfer(client, lathe, deburr, flow_id, pn, 5).status_code == 201
    assert _transfer(client, deburr, lathe, flow_id, pn, 5).status_code == 201
    assert _assign(client, lathe, flow_id, pn, 5).status_code == 201
    count = _movement_count(db_engine)
    monkeypatch.setattr(machine_processing, "committed_command", blind_then_real)
    try:
        loser = done()
    finally:
        monkeypatch.undo()
    assert loser.status_code == 200, loser.text
    assert loser.json() == original.json()
    assert misses["remaining"] == 0
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "ON_MACHINE"


def test_assignment_race_lost_at_commit_replays(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both pre-checks miss, the UNIQUE constraint decides at COMMIT: the
    loser resolves exactly like a pre-checked duplicate."""
    lathe = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=5)
    event_id = str(uuid.uuid4())
    real_lookup = machine_processing.committed_command
    misses = {"remaining": 2}

    def blind_then_real(session: Session, device_event_id: str) -> Any:
        if misses["remaining"] > 0:
            misses["remaining"] -= 1
            return []
        return real_lookup(session, device_event_id)

    original = _assign(client, lathe, flow_id, pn, 5, device_event_id=event_id)
    assert original.status_code == 201, original.text
    # Queue it again with a NEW intent so the blinded retry of the
    # original submission passes validation and reaches COMMIT.
    queued = _act(
        client,
        "QUEUE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=5,
    )
    assert queued.status_code == 201, queued.text
    count = _movement_count(db_engine)
    monkeypatch.setattr(machine_processing, "committed_command", blind_then_real)
    try:
        loser = _assign(client, lathe, flow_id, pn, 5, device_event_id=event_id)
    finally:
        monkeypatch.undo()
    assert loser.status_code == 200, loser.text
    assert loser.json() == original.json()
    assert misses["remaining"] == 0  # both pre-checks were really blinded
    # The rolled-back attempt left neither a Movement nor a projection
    # change behind: the flow is still queued.
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_machine_id is None
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "QUEUED"


# ---------------------------------------------------------------------------
# Machine retirement
# ---------------------------------------------------------------------------


def test_retirement_is_blocked_while_quantity_is_assigned(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=7)

    refused = client.post(f"/api/machines/{lathe.machine_id}/retire", json={"reason": "old"})
    assert refused.status_code == 409, refused.text
    assert "7 pcs" in refused.json()["detail"]
    machine = _machine(client, lathe.machine_id)
    assert machine["retired_on"] is None and machine["operational_state"] == "RUNNING"
    events = client.get(f"/api/machines/{lathe.machine_id}/lifecycle-events").json()
    assert events == []

    done = _act(
        client,
        "DONE",
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        machine_id=lathe.machine_id,
        quantity=7,
    )
    assert done.status_code == 201
    retired = client.post(f"/api/machines/{lathe.machine_id}/retire", json={"reason": "old"})
    assert retired.status_code == 200, retired.text
    assert retired.json()["retired_on"] is not None
    # Retired: no new assignment, history untouched.
    queued_id, queued_pn = _release(client, lathe, quantity=1)
    assert _assign(client, lathe, queued_id, queued_pn, 1).status_code == 409
    assert (
        _movement_row(db_engine, done.json()["movement_id"]).source_machine_id == lathe.machine_id
    )


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def _assign_in_session(
    engine: Engine, cell: _Cell, flow_id: int, pn: str, quantity: int, machine_id: int
) -> Any:
    with Session(engine) as session:
        return machine_processing.assign_to_machine(
            session,
            station_id=cell.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            machine_id=machine_id,
            quantity=quantity,
            device_event_id=str(uuid.uuid4()),
        )


def test_concurrent_assignments_of_one_flow_serialize_with_one_winner(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    lathe = _Cell(client, machine_count=2)
    flow_id, pn = _release(client, lathe, quantity=11)
    # Pause the first command right after it locked the Machine — it
    # holds the flow, station and Machine locks.
    pause = _Pause(machines.lock_machine)
    monkeypatch.setattr(machine_processing, "lock_machine", pause)
    results: dict[str, Any] = {}
    first = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "first",
            lambda: _assign_in_session(db_engine, lathe, flow_id, pn, 11, lathe.machine_ids[0]),
        ),
        daemon=True,
    )
    second = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "second",
            lambda: _assign_in_session(db_engine, lathe, flow_id, pn, 11, lathe.machine_ids[1]),
        ),
        daemon=True,
    )
    try:
        first.start()
        assert pause.first_inside.wait(timeout=20)
        second.start()
        time.sleep(1.0)
        assert "second" not in results  # blocked on the flow row lock
    finally:
        pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)
    assert not first.is_alive() and not second.is_alive()

    winner, loser = results["first"], results["second"]
    assert isinstance(winner, machine_processing.MachineProcessingResult) and winner.created
    assert winner.machine_id == lathe.machine_ids[0]
    assert isinstance(loser, ConflictError)
    assert "already on a Machine" in loser.message
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_ids[0]
    assigned = [
        m for m in _movements(db_engine, flow_id) if m.movement_type == "ASSIGNED_TO_MACHINE"
    ]
    assert len(assigned) == 1
    assert _machine(client, lathe.machine_ids[1])["operational_state"] == "IDLE"


def test_assignment_versus_retirement_assignment_first(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The assignment holds the Machine lock until COMMIT; retirement
    blocks on it and then sees the assigned quantity."""
    lathe = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=3)
    pause = _Pause(machines.lock_machine)
    monkeypatch.setattr(machine_processing, "lock_machine", pause)
    results: dict[str, Any] = {}

    def retire() -> Any:
        with Session(db_engine) as session:
            return machines.retire_machine(session, lathe.machine_id, reason="race")

    assign_thread = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "assign",
            lambda: _assign_in_session(db_engine, lathe, flow_id, pn, 3, lathe.machine_id),
        ),
        daemon=True,
    )
    retire_thread = threading.Thread(
        target=_run_collecting, args=(results, "retire", retire), daemon=True
    )
    try:
        assign_thread.start()
        assert pause.first_inside.wait(timeout=20)  # holds the Machine lock
        retire_thread.start()
        time.sleep(1.0)
        assert "retire" not in results  # blocked on the Machine lock
    finally:
        pause.let_first_finish.set()
    assign_thread.join(timeout=20)
    retire_thread.join(timeout=20)

    assert isinstance(results["assign"], machine_processing.MachineProcessingResult)
    assert isinstance(results["retire"], ConflictError)
    assert "still holds" in results["retire"].message
    assert _machine(client, lathe.machine_id)["retired_on"] is None
    assert _flow_row(db_engine, flow_id).current_machine_id == lathe.machine_id


def test_assignment_versus_retirement_retirement_first(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retirement holds the Machine lock; the assignment blocks on it and
    re-reads the retired row under the lock — refused, nothing written."""
    lathe = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=3)
    pause = _Pause(machines.lock_machine)
    monkeypatch.setattr(machines, "lock_machine", pause)
    results: dict[str, Any] = {}

    def retire() -> Any:
        with Session(db_engine) as session:
            return machines.retire_machine(session, lathe.machine_id, reason="race")

    retire_thread = threading.Thread(
        target=_run_collecting, args=(results, "retire", retire), daemon=True
    )
    assign_thread = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "assign",
            lambda: _assign_in_session(db_engine, lathe, flow_id, pn, 3, lathe.machine_id),
        ),
        daemon=True,
    )
    try:
        retire_thread.start()
        assert pause.first_inside.wait(timeout=20)  # holds the Machine lock
        assign_thread.start()
        time.sleep(1.0)
        assert "assign" not in results  # blocked on the Machine lock
    finally:
        pause.let_first_finish.set()
    retire_thread.join(timeout=20)
    assign_thread.join(timeout=20)

    assert isinstance(results["retire"], models.Machine)
    assert isinstance(results["assign"], ConflictError)
    assert "retired" in results["assign"].message
    assert _machine(client, lathe.machine_id)["retired_on"] is not None
    assert _flow_row(db_engine, flow_id).current_machine_id is None
    # The Area mode follows from its ACTIVE Machines (PROJECT_PROFILE
    # §12): its only Machine retired, the Area now processes directly
    # and the never-assigned quantity reads PROCESSING (Phase 7).
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "PROCESSING"


def test_done_versus_transfer_of_one_flow_has_one_serial_outcome(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transfer (which completes implicitly) holds the flow lock; the
    DONE blocks behind it, re-reads the moved flow and is refused — the
    quantity is completed exactly once."""
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _assigned(client, lathe, quantity=4)
    pause = _Pause(transfers.assess_route)
    monkeypatch.setattr(transfers, "assess_route", pause)
    results: dict[str, Any] = {}

    def run_transfer() -> Any:
        with Session(db_engine) as session:
            return transfers.transfer_to_station_area(
                session,
                station_id=deburr.station_id,
                part_number=pn,
                quantity_flow_id=flow_id,
                source_area_id=lathe.area_id,
                target_area_id=deburr.area_id,
                quantity=4,
                operation_id=None,
                confirm_route_deviation=False,
                route_deviation_reason=None,
                device_event_id=str(uuid.uuid4()),
            )

    def run_done() -> Any:
        with Session(db_engine) as session:
            return machine_processing.complete_at_machine(
                session,
                station_id=lathe.station_id,
                part_number=pn,
                quantity_flow_id=flow_id,
                machine_id=lathe.machine_id,
                quantity=4,
                device_event_id=str(uuid.uuid4()),
            )

    transfer_thread = threading.Thread(
        target=_run_collecting, args=(results, "transfer", run_transfer), daemon=True
    )
    done_thread = threading.Thread(
        target=_run_collecting, args=(results, "done", run_done), daemon=True
    )
    try:
        transfer_thread.start()
        assert pause.first_inside.wait(timeout=20)  # holds the flow lock
        done_thread.start()
        time.sleep(1.0)
        assert "done" not in results
    finally:
        pause.let_first_finish.set()
    transfer_thread.join(timeout=20)
    done_thread.join(timeout=20)

    assert isinstance(results["transfer"], transfers.AreaTransfer)
    assert results["transfer"].completed_machine_id == lathe.machine_id
    assert isinstance(results["done"], ConflictError)
    assert "not in the Area" in results["done"].message
    types = [m.movement_type for m in _movements(db_engine, flow_id)]
    assert types.count("AREA_COMPLETED") == 1
    assert types[-1] == "TRANSFERRED"
    assert _flow_row(db_engine, flow_id).current_area_id == deburr.area_id


# ---------------------------------------------------------------------------
# Projection replay
# ---------------------------------------------------------------------------


def test_projection_replay_rebuilds_area_machine_and_state_from_history(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    deburr = _Cell(client)
    queued_id, _ = _release(client, lathe, quantity=1)
    on_machine_id, _ = _assigned(client, lathe, quantity=2)
    finished_id, finished_pn = _assigned(client, lathe, quantity=3)
    assert (
        _act(
            client,
            "DONE",
            lathe.station_id,
            part_number=finished_pn,
            quantity_flow_id=finished_id,
            machine_id=lathe.machine_id,
            quantity=3,
        ).status_code
        == 201
    )
    moved_id, moved_pn = _assigned(client, lathe, quantity=4)
    assert _transfer(client, lathe, deburr, moved_id, moved_pn, 4).status_code == 201

    with Session(db_engine) as session:
        rebuilt = projections.rebuild_current_positions(session)
    assert rebuilt[queued_id] == (lathe.area_id, None, ProcessingState.QUEUED)
    assert rebuilt[on_machine_id] == (lathe.area_id, lathe.machine_id, ProcessingState.ON_MACHINE)
    assert rebuilt[finished_id] == (lathe.area_id, None, ProcessingState.READY_TO_TRANSFER)
    assert rebuilt[moved_id] == (deburr.area_id, None, ProcessingState.QUEUED)
    for flow_id in (queued_id, on_machine_id, finished_id, moved_id):
        _assert_replay_matches(db_engine, flow_id)


# ---------------------------------------------------------------------------
# Phase 6 read models — Machine-first, PN-first, inventory separation
# ---------------------------------------------------------------------------


def _resolve_machine(client: TestClient, station_id: str, **body: Any) -> Any:
    return client.post(f"/api/scan-stations/{station_id}/machine-scans/resolve", json=body)


def _resolve_pn(client: TestClient, station_id: str, **body: Any) -> Any:
    return client.post(f"/api/scan-stations/{station_id}/scans/resolve", json=body)


def _inventory(client: TestClient, area_id: int) -> dict[str, Any]:
    response = client.get(f"/api/areas/{area_id}/inventory")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_machine_scan_resolves_the_one_shot_assignment_context(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=2)
    machine = _machine(client, lathe.machine_id)
    queued_a, pn_a = _release(client, lathe, quantity=5)
    queued_b, pn_b = _release(client, lathe, quantity=7)
    on_machine_id, on_pn = _assigned(client, lathe, quantity=3)
    finished_id, finished_pn = _assigned(client, lathe, quantity=2)
    assert (
        _act(
            client,
            "DONE",
            lathe.station_id,
            part_number=finished_pn,
            quantity_flow_id=finished_id,
            machine_id=lathe.machine_id,
            quantity=2,
        ).status_code
        == 201
    )
    count = _movement_count(db_engine)

    response = _resolve_machine(client, lathe.station_id, barcode=machine["barcode_value"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["station_id"] == lathe.station_id
    assert body["area"]["id"] == lathe.area_id
    assert body["machine"]["id"] == lathe.machine_id
    assert body["machine"]["asset_tag"] == machine["asset_tag"]
    assert body["machine"]["barcode_value"] == f"PF:MACHINE:{machine['asset_tag']}"
    assert body["machine"]["operational_state"] == "RUNNING"
    assert body["assigned_quantity"] == 3
    # Only QUEUED flows are offered — every PN of the Area, never the
    # ON_MACHINE or the finished quantity; several → explicit selection.
    assert [(f["quantity_flow_id"], f["part_number"]) for f in body["queued"]] == sorted(
        [(queued_a, pn_a), (queued_b, pn_b)], key=lambda item: item[1]
    )
    assert all(f["processing_state"] == "QUEUED" for f in body["queued"])
    assert all(f["available_actions"] == ["ASSIGN", "TRANSFER"] for f in body["queued"])
    assert {f["quantity_flow_id"] for f in body["queued"]}.isdisjoint({on_machine_id, finished_id})
    assert body["requires_selection"] is True
    # The manual Asset Tag entry resolves identically; nothing was written
    # and nothing sticks — a second resolution is the same fresh context.
    manual = _resolve_machine(client, lathe.station_id, asset_tag=f"  {machine['asset_tag']} ")
    assert manual.status_code == 200 and manual.json() == body
    assert _movement_count(db_engine) == count
    assert on_pn != pn_a


def test_machine_first_assignment_uses_the_resolved_context(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=4)
    machine = _machine(client, lathe.machine_id)
    context = _resolve_machine(client, lathe.station_id, barcode=machine["barcode_value"]).json()
    assert context["requires_selection"] is False
    assert context["machine"]["operational_state"] == "IDLE"
    selected = context["queued"][0]
    assert (selected["quantity_flow_id"], selected["part_number"]) == (flow_id, pn)

    response = _assign(
        client, lathe, flow_id, pn, selected["quantity"], machine_id=context["machine"]["id"]
    )
    assert response.status_code == 201, response.text
    after = _resolve_machine(client, lathe.station_id, barcode=machine["barcode_value"]).json()
    assert after["queued"] == []
    assert after["assigned_quantity"] == 4
    assert after["machine"]["operational_state"] == "RUNNING"
    assert after["machine"]["state_changed_at"] > context["machine"]["state_changed_at"]


def test_machine_scan_refusals_resolve_nothing(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=2)
    other = _Cell(client)
    _release(client, lathe, quantity=1)
    retired_id = lathe.machine_ids[1]
    retired_tag = _machine(client, retired_id)["asset_tag"]
    assert client.post(f"/api/machines/{retired_id}/retire", json={}).status_code == 200
    maintained_id = _create_machine(client, lathe.area_id)
    maintained_tag = _machine(client, maintained_id)["asset_tag"]
    assert client.post(f"/api/machines/{maintained_id}/maintenance", json={}).status_code == 201
    other_tag = _machine(client, other.machine_id)["asset_tag"]
    count = _movement_count(db_engine)

    unknown = _resolve_machine(client, lathe.station_id, barcode="PF:MACHINE:NOPE-9999")
    assert unknown.status_code == 404 and "NOPE-9999" in unknown.json()["detail"]
    retired = _resolve_machine(client, lathe.station_id, asset_tag=retired_tag)
    assert retired.status_code == 409 and "retired" in retired.json()["detail"]
    elsewhere = _resolve_machine(client, lathe.station_id, asset_tag=other_tag)
    assert elsewhere.status_code == 409 and "another Area" in elsewhere.json()["detail"]
    maintained = _resolve_machine(client, lathe.station_id, asset_tag=maintained_tag)
    assert maintained.status_code == 409 and "maintenance" in maintained.json()["detail"]
    for body, fragment in (
        ({"barcode": "PF:PN:2027-60-8114-00"}, "Part Number barcode"),
        ({"barcode": "PF:AREA:1"}, "Unknown barcode"),
        ({"barcode": "CD-0001"}, "Unknown barcode"),
        ({"barcode": "PF:MACHINE:"}, "Unknown barcode"),
        ({"asset_tag": "   "}, "must not be empty"),
        ({}, "exactly one"),
        ({"barcode": "PF:MACHINE:X", "asset_tag": "X"}, "exactly one"),
    ):
        rejected = _resolve_machine(client, lathe.station_id, **body)
        assert rejected.status_code == 422, rejected.text
        assert fragment in rejected.json()["detail"]
    # A Machine barcode on the PN scan is named as such, never a PN.
    pn_scan = _resolve_pn(client, lathe.station_id, barcode=f"PF:MACHINE:{other_tag}")
    assert pn_scan.status_code == 422 and "Machine barcode" in pn_scan.json()["detail"]
    # Station context is judged first.
    inactive_station = _create_station(client, lathe.area_id)
    client.patch(f"/api/scan-stations/{inactive_station}", json={"is_active": False})
    assert _resolve_machine(client, inactive_station, asset_tag=other_tag).status_code == 409
    assert _resolve_machine(client, "NO-SUCH", asset_tag=other_tag).status_code == 404
    assert _movement_count(db_engine) == count


def test_resolved_machine_context_is_re_validated_by_the_command(
    client: TestClient, db_engine: Engine
) -> None:
    """The context is one-shot and never a session: a Machine retired or
    put under maintenance after the scan refuses the assignment."""
    lathe = _Cell(client, machine_count=2)
    flow_id, pn = _release(client, lathe, quantity=6)
    tags = [_machine(client, machine_id)["asset_tag"] for machine_id in lathe.machine_ids]
    for tag in tags:
        assert _resolve_machine(client, lathe.station_id, asset_tag=tag).status_code == 200
    count = _movement_count(db_engine)

    assert client.post(f"/api/machines/{lathe.machine_ids[0]}/retire", json={}).status_code == 200
    stale_retired = _assign(client, lathe, flow_id, pn, 6, machine_id=lathe.machine_ids[0])
    assert stale_retired.status_code == 409 and "retired" in stale_retired.json()["detail"]
    assert (
        client.post(f"/api/machines/{lathe.machine_ids[1]}/maintenance", json={}).status_code == 201
    )
    stale_maintenance = _assign(client, lathe, flow_id, pn, 6, machine_id=lathe.machine_ids[1])
    assert stale_maintenance.status_code == 409
    assert "maintenance" in stale_maintenance.json()["detail"]
    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "QUEUED"


def test_pn_first_reports_the_valid_actions_per_processing_state(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client)
    deburr = _Cell(client)
    queued_id, pn = _release(client, lathe, quantity=5)
    on_machine_id, _ = _release(client, lathe, quantity=6, part_number=pn)
    finished_id, _ = _release(client, lathe, quantity=7, part_number=pn)
    for flow_id, quantity in ((on_machine_id, 6), (finished_id, 7)):
        assert _assign(client, lathe, flow_id, pn, quantity).status_code == 201
    assert (
        _act(
            client,
            "DONE",
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=finished_id,
            machine_id=lathe.machine_id,
            quantity=7,
        ).status_code
        == 201
    )

    resolved = _resolve_pn(client, lathe.station_id, barcode=f"PF:PN:{pn}")
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["resolution"] == "ALREADY_IN_AREA"
    # Three flows of one PN in the Area: always an explicit selection.
    assert body["requires_selection"] is True
    by_id = {flow["quantity_flow_id"]: flow for flow in body["in_area"]}
    assert set(by_id) == {queued_id, on_machine_id, finished_id}
    assert by_id[queued_id]["processing_state"] == "QUEUED"
    assert by_id[queued_id]["machine_id"] is None
    assert by_id[queued_id]["available_actions"] == ["ASSIGN", "TRANSFER"]
    assert by_id[on_machine_id]["processing_state"] == "ON_MACHINE"
    assert by_id[on_machine_id]["machine_id"] == lathe.machine_id
    assert by_id[on_machine_id]["available_actions"] == ["DONE", "QUEUE", "TRANSFER"]
    assert by_id[finished_id]["processing_state"] == "READY_TO_TRANSFER"
    assert by_id[finished_id]["machine_id"] is None
    assert by_id[finished_id]["available_actions"] == ["TRANSFER"]
    assert all(flow["part_number"] == pn for flow in body["in_area"])

    # Seen from another station, the same three flows are transfer
    # candidates carrying their state — several → explicit selection.
    elsewhere = _resolve_pn(client, deburr.station_id, part_number=pn).json()
    assert elsewhere["resolution"] == "TRANSFER_SOURCE_AVAILABLE"
    assert elsewhere["requires_selection"] is True
    states = {c["quantity_flow_id"]: c["processing_state"] for c in elsewhere["candidates"]}
    assert states == {
        queued_id: "QUEUED",
        on_machine_id: "ON_MACHINE",
        finished_id: "READY_TO_TRANSFER",
    }


def test_single_flow_needs_no_selection(client: TestClient) -> None:
    lathe = _Cell(client)
    deburr = _Cell(client)
    _, pn = _release(client, lathe, quantity=5)
    here = _resolve_pn(client, lathe.station_id, part_number=pn).json()
    assert len(here["in_area"]) == 1 and here["requires_selection"] is False
    elsewhere = _resolve_pn(client, deburr.station_id, part_number=pn).json()
    assert len(elsewhere["candidates"]) == 1 and elsewhere["requires_selection"] is False
    nothing = _resolve_pn(client, deburr.station_id, part_number=_unique("PN")).json()
    assert nothing["resolution"] == "NO_TRANSFERABLE_QUANTITY"
    assert nothing["requires_selection"] is False


def test_inventory_separates_queued_on_machine_and_finished_quantity(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=3)
    machine_a, machine_b, machine_c = lathe.machine_ids
    queued_id, queued_pn = _release(client, lathe, quantity=10)
    a1_id, a1_pn = _release(client, lathe, quantity=20)
    a2_id, a2_pn = _release(client, lathe, quantity=30)
    b_id, b_pn = _release(client, lathe, quantity=40)
    finished_id, finished_pn = _release(client, lathe, quantity=50)
    for flow_id, pn, quantity, machine_id in (
        (a1_id, a1_pn, 20, machine_a),
        (a2_id, a2_pn, 30, machine_a),
        (b_id, b_pn, 40, machine_b),
        (finished_id, finished_pn, 50, machine_c),
    ):
        assert (
            _assign(client, lathe, flow_id, pn, quantity, machine_id=machine_id).status_code == 201
        )
    assert (
        _act(
            client,
            "DONE",
            lathe.station_id,
            part_number=finished_pn,
            quantity_flow_id=finished_id,
            machine_id=machine_c,
            quantity=50,
        ).status_code
        == 201
    )

    inventory = _inventory(client, lathe.area_id)
    # Area summary: queued and finished; Machine cards: ON_MACHINE only.
    assert [(line["part_number"], line["total_quantity"]) for line in inventory["queued"]] == [
        (queued_pn, 10)
    ]
    assert inventory["queued_quantity"] == 10
    assert [(line["part_number"], line["total_quantity"]) for line in inventory["finished"]] == [
        (finished_pn, 50)
    ]
    assert inventory["finished_quantity"] == 50
    assert inventory["on_machine_quantity"] == 90
    assert inventory["total_quantity"] == 10 + 90 + 50
    assert inventory["total_part_numbers"] == 5
    cards = {card["machine"]["id"]: card for card in inventory["machines"]}
    assert set(cards) == {machine_a, machine_b, machine_c}  # every active Machine
    assert cards[machine_a]["total_quantity"] == 50
    assert {line["part_number"] for line in cards[machine_a]["lines"]} == {a1_pn, a2_pn}
    assert cards[machine_a]["machine"]["operational_state"] == "RUNNING"
    assert cards[machine_b]["total_quantity"] == 40
    assert cards[machine_b]["machine"]["operational_state"] == "RUNNING"
    # The finished quantity left Machine C's card: idle and empty.
    assert cards[machine_c]["total_quantity"] == 0 and cards[machine_c]["lines"] == []
    assert cards[machine_c]["machine"]["operational_state"] == "IDLE"
    on_cards = {
        flow["quantity_flow_id"]
        for card in inventory["machines"]
        for line in card["lines"]
        for flow in line["flows"]
    }
    assert on_cards == {a1_id, a2_id, b_id}
    assert all(
        flow["processing_state"] == "ON_MACHINE" and flow["machine_id"] == card["machine"]["id"]
        for card in inventory["machines"]
        for line in card["lines"]
        for flow in line["flows"]
    )

    # Reconciliation with the Machines management read model.
    for machine_id, card in cards.items():
        managed = _machine(client, machine_id)
        assert managed["operational_state"] == card["machine"]["operational_state"]
        assert managed["assigned_quantity"] == card["total_quantity"]
        assert managed["state_changed_at"] == card["machine"]["state_changed_at"]
    listed = {m["id"]: m for m in client.get("/api/machines").json()}
    assert listed[machine_a]["assigned_quantity"] == 50
    assert listed[machine_c]["operational_state"] == "IDLE"

    # A retired Machine is no card; a maintained one keeps its quantity
    # on its card with the Maintenance state.
    assert client.post(f"/api/machines/{machine_c}/retire", json={}).status_code == 200
    assert client.post(f"/api/machines/{machine_b}/maintenance", json={}).status_code == 201
    inventory = _inventory(client, lathe.area_id)
    cards = {card["machine"]["id"]: card for card in inventory["machines"]}
    assert set(cards) == {machine_a, machine_b}
    assert cards[machine_b]["machine"]["operational_state"] == "MAINTENANCE"
    assert cards[machine_b]["machine"]["maintenance_since"] is not None
    assert cards[machine_b]["total_quantity"] == 40
    assert inventory["on_machine_quantity"] == 90
    _assert_replay_matches(db_engine, finished_id)


def test_inventory_of_an_area_without_machines_has_no_cards(client: TestClient) -> None:
    """An Area without Machines has no queue: its quantity is directly
    processing (Phase 7), never queued, and no placeholder card exists."""
    material = _Cell(client, machine_count=0)
    _release(client, material, quantity=3)
    inventory = _inventory(client, material.area_id)
    assert inventory["has_machines"] is False
    assert inventory["machines"] == []
    assert inventory["queued_quantity"] == 0 and inventory["on_machine_quantity"] == 0
    assert inventory["processing_quantity"] == 3 and inventory["finished_quantity"] == 0
    assert (
        client.get(f"/api/scan-stations/{material.station_id}/context").json()["has_machines"]
        is False
    )
