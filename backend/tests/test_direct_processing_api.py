"""Integration tests for Phase 7 — direct Area processing (Areas without Machines).

Exercises the full request path — FastAPI routes, the Application
commands and read models, and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain. Covered
per IMPLEMENTATION_ROADMAP Phase 7, PROJECT_PROFILE §7 Area Completion,
§8.11, §12 (Area Without Machines, Area Processing States) and §15:

- exactly two Area modes, following from the Area's active Machines:
  quantity arriving in an Area without Machines (a production release
  or a transfer) is PROCESSING — owned directly, no queue, Machine
  NULL, the Operation recorded (an Area with several active Operations
  needs the explicit choice; nothing is picked); an Area with Machines
  keeps QUEUE_AND_ASSIGN unchanged; no assignment-mode configuration
  exists, and adding the first Machine or retiring the last one moves
  the Area — and the quantity it holds — between the two modes;
- the read models: ``has_machines`` on the station context and the
  inventory, the PROCESSING state with ``available_actions`` DONE and
  TRANSFER (never ASSIGN or QUEUE), the inventory split
  ``processing`` / ``finished`` with no Machine cards and structurally
  zero queued/on-Machine figures;
- the manual DONE without a Machine: exactly one immutable
  ``AREA_COMPLETED`` with ``source_machine_id`` NULL, the Operation
  carried forward, the Station recorded, the Area kept, deriving
  READY_TO_TRANSFER; refusals with ZERO writes (exceeding
  quantity, PN mismatch, already finished, a station of another Area,
  an inactive station, quantity in a Machine Area, and the Machine-Area
  commands — DONE with a Machine, QUEUE, ASSIGN — on directly
  processing quantity);
- idempotency: replay after the state moved on, mismatched reuse —
  including a Machine DONE reusing a direct DONE id and vice versa — and
  a race lost at COMMIT;
- the implicit completion: a transfer of PROCESSING quantity appends
  ``AREA_COMPLETED`` (no Machine) + ``TRANSFERRED`` as ONE command under
  ONE ``device_event_id`` (sequence 1, 2), replayed as a whole,
  all-or-nothing at the database; finished quantity transfers with
  ``TRANSFERRED`` alone;
- concurrency: DONE versus transfer of one flow, and two DONEs of one
  flow, each with exactly one serial outcome;
- the projection replay reconstructing PROCESSING → READY_TO_TRANSFER →
  transferred from history alone;
- Machine-Area regressions: the Phase 6 commands and shapes are exactly
  as before for an Area with Machines.

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
    direct_processing,
    machine_processing,
    machines,
    projections,
    transfers,
)
from app.application.errors import ConflictError, IdempotencyConflictError
from app.core.config import get_settings
from app.domain.enums import ProcessingState
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_direct_processing_api"


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
    """An Area with ``operation_count`` Operations, one Scan Station and
    ``machine_count`` Machines — zero Machines is a direct-processing Area."""

    def __init__(
        self, client: TestClient, *, machine_count: int = 0, operation_count: int = 1
    ) -> None:
        self.area = _create_area(client)
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


def _release(
    client: TestClient, cell: _Cell, *, quantity: int = 25, part_number: str | None = None
) -> tuple[int, str]:
    """Release one FLOATING flow into the cell's Area: (quantity_flow_id, pn)."""
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
    quantity: Any,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": part_number,
        "quantity_flow_id": quantity_flow_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return client.post(f"/api/scan-stations/{station_id}/{_ACTION_PATHS[kind]}", json=payload)


def _done(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: Any, **kw: Any) -> Any:
    """The direct-processing DONE: the same endpoint WITHOUT a Machine."""
    return _act(
        client,
        "DONE",
        cell.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        quantity=quantity,
        **kw,
    )


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


def _inventory(client: TestClient, area_id: int) -> dict[str, Any]:
    response = client.get(f"/api/areas/{area_id}/inventory")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _inventory_flow(client: TestClient, area_id: int, flow_id: int) -> dict[str, Any]:
    for line in _inventory(client, area_id)["lines"]:
        for flow in line["flows"]:
            if flow["quantity_flow_id"] == flow_id:
                return cast(dict[str, Any], flow)
    raise AssertionError(f"flow {flow_id} not in Area {area_id} inventory")


def _context(client: TestClient, station_id: str) -> dict[str, Any]:
    response = client.get(f"/api/scan-stations/{station_id}/context")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _resolve_pn(client: TestClient, station_id: str, pn: str) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{station_id}/scans/resolve", json={"part_number": pn}
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _assert_replay_matches(engine: Engine, flow_id: int, state: ProcessingState) -> None:
    with Session(engine) as session:
        position = projections.rebuild_current_positions(session)[flow_id]
    row = _flow_row(engine, flow_id)
    assert (position.area_id, position.machine_id) == (row.current_area_id, row.current_machine_id)
    assert position.processing_state == state


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
# Arrival: an Area without Machines takes direct processing ownership
# ---------------------------------------------------------------------------


def test_released_quantity_in_an_area_without_machines_is_processing(
    client: TestClient, db_engine: Engine
) -> None:
    """No queue: the release hands the quantity straight to processing —
    Machine NULL, the Operation recorded, only DONE and TRANSFER valid."""
    plating = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=12)

    assert _context(client, plating.station_id)["has_machines"] is False
    flow = _inventory_flow(client, plating.area_id, flow_id)
    assert flow["processing_state"] == "PROCESSING"
    assert flow["machine_id"] is None
    assert flow["operation"]["id"] == plating.operation_id
    assert flow["operation"]["is_active"] is True
    assert flow["available_actions"] == ["DONE", "TRANSFER", "SCRAP"]
    history = _movements(db_engine, flow_id)
    assert [m.movement_type for m in history] == ["RECEIVED"]
    assert history[0].operation_id == plating.operation_id
    assert _flow_row(db_engine, flow_id).current_machine_id is None

    resolved = _resolve_pn(client, plating.station_id, pn)
    assert resolved["resolution"] == "ALREADY_IN_AREA"
    assert resolved["in_area"][0]["processing_state"] == "PROCESSING"
    assert resolved["in_area"][0]["available_actions"] == ["DONE", "TRANSFER", "SCRAP"]
    assert resolved["requires_selection"] is False

    inventory = _inventory(client, plating.area_id)
    assert inventory["has_machines"] is False
    assert inventory["machines"] == []
    assert inventory["queued"] == [] and inventory["queued_quantity"] == 0
    assert inventory["on_machine_quantity"] == 0
    assert inventory["processing_quantity"] == 12
    assert [line["part_number"] for line in inventory["processing"]] == [pn]
    assert inventory["finished"] == [] and inventory["finished_quantity"] == 0
    assert inventory["total_quantity"] == 12
    _assert_replay_matches(db_engine, flow_id, ProcessingState.PROCESSING)


def test_transfer_into_an_area_without_machines_records_the_operation_and_no_machine(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    plating = _Cell(client)
    flow_id, pn = _release(client, lathe, quantity=9)
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "QUEUED"

    resolved = _resolve_pn(client, plating.station_id, pn)
    assert resolved["resolution"] == "TRANSFER_SOURCE_AVAILABLE"
    candidate = resolved["candidates"][0]
    assert candidate["processing_state"] == "QUEUED"
    assert candidate["suggested_operation_id"] == plating.operation_id

    response = _transfer(client, lathe, plating, flow_id, pn, 9)
    assert response.status_code == 201, response.text
    body = response.json()
    # Queued quantity leaves unprocessed: TRANSFERRED alone.
    assert body["completed_movement_id"] is None and body["completed_machine_id"] is None
    assert body["operation_id"] == plating.operation_id
    transferred = _movements(db_engine, flow_id)[-1]
    assert transferred.movement_type == "TRANSFERRED"
    assert transferred.operation_id == plating.operation_id
    assert transferred.source_machine_id is None and transferred.destination_machine_id is None

    flow = _inventory_flow(client, plating.area_id, flow_id)
    assert flow["processing_state"] == "PROCESSING" and flow["machine_id"] is None
    assert _flow_row(db_engine, flow_id).current_machine_id is None
    _assert_replay_matches(db_engine, flow_id, ProcessingState.PROCESSING)


def test_several_operations_need_an_explicit_choice_before_direct_processing(
    client: TestClient, db_engine: Engine
) -> None:
    """The applicable Operation is resolved or confirmed — never picked."""
    material = _Cell(client)
    external = _Cell(client, operation_count=2)
    flow_id, pn = _release(client, material, quantity=6)
    count = _movement_count(db_engine)

    resolved = _resolve_pn(client, external.station_id, pn)
    assert resolved["candidates"][0]["suggested_operation_id"] is None
    assert {op["id"] for op in resolved["operations"]} == set(external.operation_ids)

    refused = _transfer(client, material, external, flow_id, pn, 6)
    assert refused.status_code == 422, refused.text
    assert "Choose the Operation" in refused.json()["detail"]
    assert _movement_count(db_engine) == count
    assert _flow_row(db_engine, flow_id).current_area_id == material.area_id

    chosen = external.operation_ids[1]
    accepted = _transfer(client, material, external, flow_id, pn, 6, operation_id=chosen)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["operation_id"] == chosen
    assert _movements(db_engine, flow_id)[-1].operation_id == chosen
    arrived = _inventory_flow(client, external.area_id, flow_id)
    assert arrived["processing_state"] == "PROCESSING"
    assert arrived["operation"]["id"] == chosen  # the explicit choice, on the read model too


def test_existing_quantity_keeps_its_recorded_operation_after_deactivation(
    client: TestClient,
) -> None:
    """The read models present the Operation RECORDED on the quantity —
    active or not — independent of the active Operations the station
    offers for new arrivals (which no longer include it)."""
    external = _Cell(client, operation_count=2)
    recorded_id, other_id = external.operation_ids
    flow_id, pn = _release(client, external, quantity=3)
    assert _inventory_flow(client, external.area_id, flow_id)["operation"]["id"] == recorded_id

    marked = client.patch(f"/api/operations/{recorded_id}", json={"is_external": True})
    assert marked.status_code == 200, marked.text
    deactivated = client.patch(f"/api/operations/{recorded_id}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text

    context = _context(client, external.station_id)
    assert [op["id"] for op in context["operations"]] == [other_id]
    for flow in (
        _inventory_flow(client, external.area_id, flow_id),
        _resolve_pn(client, external.station_id, pn)["in_area"][0],
    ):
        assert flow["operation"]["id"] == recorded_id
        assert flow["operation"]["is_external"] is True
        assert flow["operation"]["is_active"] is False
        assert flow["processing_state"] == "PROCESSING"
    resolved = _resolve_pn(client, external.station_id, pn)
    assert [op["id"] for op in resolved["operations"]] == [other_id]
    # The direct DONE still carries the recorded Operation forward.
    done = _done(client, external, flow_id, pn, 3)
    assert done.status_code == 201, done.text
    assert done.json()["operation_id"] == recorded_id


# ---------------------------------------------------------------------------
# Manual DONE without a Machine
# ---------------------------------------------------------------------------


def test_direct_done_records_one_machine_less_completion(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=7)
    event_id = str(uuid.uuid4())
    count = _movement_count(db_engine)

    response = _done(client, plating, flow_id, pn, 7, device_event_id=event_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_type"] == "AREA_COMPLETED"
    assert body["machine_id"] is None
    assert body["area_id"] == plating.area_id
    assert body["operation_id"] == plating.operation_id
    assert body["station_id"] == plating.station_id
    assert body["processing_state"] == "READY_TO_TRANSFER"
    assert body["quantity"] == 7 and body["device_event_id"] == event_id
    assert _movement_count(db_engine) == count + 1

    history = _movements(db_engine, flow_id)
    assert [m.movement_type for m in history] == ["RECEIVED", "AREA_COMPLETED"]
    completed = history[-1]
    assert completed.id == body["movement_id"]
    # The canonical Machine-less completion shape: inside the Area, at
    # the station, Operation carried forward, no Machine either side.
    assert completed.from_area_id == completed.to_area_id == plating.area_id
    assert completed.source_machine_id is None and completed.destination_machine_id is None
    assert completed.operation_id == plating.operation_id
    assert completed.station_id == plating.station_id
    assert completed.assigned_route_step_id is None
    assert completed.command_sequence == 1
    assert completed.metadata["command"] == {"kind": "DONE", "size": 1}
    assert completed.metadata["request_fingerprint"]

    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_area_id == plating.area_id
    assert flow_row.current_machine_id is None
    flow = _inventory_flow(client, plating.area_id, flow_id)
    assert flow["processing_state"] == "READY_TO_TRANSFER"
    assert flow["available_actions"] == ["TRANSFER", "SCRAP"]
    inventory = _inventory(client, plating.area_id)
    assert inventory["processing_quantity"] == 0 and inventory["finished_quantity"] == 7
    assert inventory["machines"] == [] and inventory["queued_quantity"] == 0
    _assert_replay_matches(db_engine, flow_id, ProcessingState.READY_TO_TRANSFER)

    # Idempotent replay: identical body, nothing added; a mismatched
    # reuse — a different quantity, or the Machine-Area DONE naming a
    # Machine under the same id — is an explicit conflict.
    replay = _done(client, plating, flow_id, pn, 7, device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json() == body
    mismatch = _done(client, plating, flow_id, pn, 6, device_event_id=event_id)
    assert mismatch.status_code == 409, mismatch.text
    lathe = _Cell(client, machine_count=1)
    with_machine = _done(
        client, plating, flow_id, pn, 7, machine_id=lathe.machine_id, device_event_id=event_id
    )
    assert with_machine.status_code == 409, with_machine.text
    assert _movement_count(db_engine) == count + 1
    # Finished quantity is not completed twice.
    again = _done(client, plating, flow_id, pn, 7)
    assert again.status_code == 409 and "already completed" in again.json()["detail"]
    assert _movement_count(db_engine) == count + 1


def test_direct_done_refusals_write_nothing(client: TestClient, db_engine: Engine) -> None:
    plating = _Cell(client)
    other = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=10)
    count = _movement_count(db_engine)

    exceeding = _done(client, plating, flow_id, pn, 11)
    assert exceeding.status_code == 422 and "exceeds" in exceeding.json()["detail"]
    wrong_pn = _done(client, plating, flow_id, _unique("PN"), 10)
    assert wrong_pn.status_code == 422 and "does not match" in wrong_pn.json()["detail"]
    for bad_quantity in (0, -1, 2.5, "10", True):
        assert _done(client, plating, flow_id, pn, bad_quantity).status_code == 422
    elsewhere = _done(client, other, flow_id, pn, 10)
    assert elsewhere.status_code == 409 and "not in the Area" in elsewhere.json()["detail"]
    unknown_flow = _done(client, plating, flow_id + 1_000_000, pn, 10)
    assert unknown_flow.status_code == 422
    unknown_station = _act(
        client, "DONE", "NO-SUCH-STATION", part_number=pn, quantity_flow_id=flow_id, quantity=10
    )
    assert unknown_station.status_code == 404

    deactivated = client.patch(
        f"/api/scan-stations/{plating.station_id}", json={"is_active": False}
    )
    assert deactivated.status_code == 200, deactivated.text
    inactive = _done(client, plating, flow_id, pn, 10)
    assert inactive.status_code == 409 and "inactive" in inactive.json()["detail"]
    assert (
        client.patch(
            f"/api/scan-stations/{plating.station_id}", json={"is_active": True}
        ).status_code
        == 200
    )

    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, plating.area_id, flow_id)["processing_state"] == "PROCESSING"


def test_machine_area_commands_never_apply_to_directly_processing_quantity(
    client: TestClient, db_engine: Engine
) -> None:
    """DONE with a Machine, QUEUE and ASSIGN are Machine-Area actions:
    on directly processing quantity each is refused with zero writes."""
    plating = _Cell(client)
    lathe = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, plating, quantity=5)
    count = _movement_count(db_engine)

    machine_done = _done(client, plating, flow_id, pn, 5, machine_id=lathe.machine_id)
    assert machine_done.status_code == 409, machine_done.text
    assert "not on a Machine" in machine_done.json()["detail"]
    queue = _act(
        client,
        "QUEUE",
        plating.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        quantity=5,
        machine_id=lathe.machine_id,
    )
    assert queue.status_code == 409 and "not on a Machine" in queue.json()["detail"]
    assign = _act(
        client,
        "ASSIGN",
        plating.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        quantity=5,
        machine_id=lathe.machine_id,
    )
    assert assign.status_code == 409 and "nothing to assign" in assign.json()["detail"]
    # machine_id is required for assignment and QUEUE (shape, not a rule).
    for kind in ("ASSIGN", "QUEUE"):
        missing = _act(
            client, kind, plating.station_id, part_number=pn, quantity_flow_id=flow_id, quantity=5
        )
        assert missing.status_code == 422, missing.text

    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, plating.area_id, flow_id)["processing_state"] == "PROCESSING"


def test_direct_done_never_applies_in_a_machine_area(client: TestClient, db_engine: Engine) -> None:
    """Regression for Machine Areas: quantity there is queued, on a
    Machine or finished — a DONE without a Machine is refused for each,
    and the Phase 6 DONE with the Machine still works exactly as before."""
    lathe = _Cell(client, machine_count=1)
    queued_id, queued_pn = _release(client, lathe, quantity=3)
    on_machine_id, on_machine_pn = _release(client, lathe, quantity=4)
    assigned = _act(
        client,
        "ASSIGN",
        lathe.station_id,
        part_number=on_machine_pn,
        quantity_flow_id=on_machine_id,
        quantity=4,
        machine_id=lathe.machine_id,
    )
    assert assigned.status_code == 201, assigned.text
    count = _movement_count(db_engine)

    for flow_id, pn, quantity in ((queued_id, queued_pn, 3), (on_machine_id, on_machine_pn, 4)):
        refused = _done(client, lathe, flow_id, pn, quantity)
        assert refused.status_code == 409, refused.text
        assert "has Machines" in refused.json()["detail"]
    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, lathe.area_id, queued_id)["processing_state"] == "QUEUED"
    assert _inventory_flow(client, lathe.area_id, on_machine_id)["processing_state"] == (
        "ON_MACHINE"
    )

    # The Machine-Area DONE, unchanged.
    machine_done = _done(
        client, lathe, on_machine_id, on_machine_pn, 4, machine_id=lathe.machine_id
    )
    assert machine_done.status_code == 201, machine_done.text
    assert machine_done.json()["machine_id"] == lathe.machine_id
    completed = _movements(db_engine, on_machine_id)[-1]
    assert completed.movement_type == "AREA_COMPLETED"
    assert completed.source_machine_id == lathe.machine_id
    assert _inventory_flow(client, lathe.area_id, on_machine_id)["processing_state"] == (
        "READY_TO_TRANSFER"
    )
    # Finished quantity in a Machine Area refuses the direct DONE too.
    finished = _done(client, lathe, on_machine_id, on_machine_pn, 4)
    assert finished.status_code == 409 and "already completed" in finished.json()["detail"]
    inventory = _inventory(client, lathe.area_id)
    assert inventory["has_machines"] is True
    assert inventory["processing"] == [] and inventory["processing_quantity"] == 0
    assert inventory["queued_quantity"] == 3 and inventory["finished_quantity"] == 4


def test_machine_done_id_and_direct_done_id_never_replay_each_other(
    client: TestClient, db_engine: Engine
) -> None:
    """One device_event_id, two intents: a committed Machine DONE reused
    without the Machine — on any flow — is a conflict, never a replay."""
    lathe = _Cell(client, machine_count=1)
    plating = _Cell(client)
    machine_flow, machine_pn = _release(client, lathe, quantity=2)
    assert (
        _act(
            client,
            "ASSIGN",
            lathe.station_id,
            part_number=machine_pn,
            quantity_flow_id=machine_flow,
            quantity=2,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    direct_flow, direct_pn = _release(client, plating, quantity=2)
    event_id = str(uuid.uuid4())
    original = _done(
        client,
        lathe,
        machine_flow,
        machine_pn,
        2,
        machine_id=lathe.machine_id,
        device_event_id=event_id,
    )
    assert original.status_code == 201, original.text
    count = _movement_count(db_engine)

    reused = _done(client, plating, direct_flow, direct_pn, 2, device_event_id=event_id)
    assert reused.status_code == 409, reused.text
    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, plating.area_id, direct_flow)["processing_state"] == "PROCESSING"
    # The unit-level guard: the replay is judged on the stored fingerprint.
    with Session(db_engine) as session:
        committed = machine_processing.committed_command(session, event_id)
        with pytest.raises(IdempotencyConflictError):
            machine_processing.replay_or_conflict(
                committed,
                "DONE",
                machine_processing.request_fingerprint(
                    kind="DONE",
                    station_id=plating.station_id,
                    quantity_flow_id=direct_flow,
                    part_number=direct_pn,
                    machine_id=None,
                    quantity=2,
                ),
            )


def test_direct_done_replays_after_the_state_moved_on(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=8)
    event_id = str(uuid.uuid4())
    original = _done(client, plating, flow_id, pn, 8, device_event_id=event_id)
    assert original.status_code == 201, original.text
    # The finished quantity moves on and comes back into processing.
    assert _transfer(client, plating, deburr, flow_id, pn, 8).status_code == 201
    assert _transfer(client, deburr, plating, flow_id, pn, 8).status_code == 201
    assert _inventory_flow(client, plating.area_id, flow_id)["processing_state"] == "PROCESSING"
    count = _movement_count(db_engine)

    replay = _done(client, plating, flow_id, pn, 8, device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original.json()
    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, plating.area_id, flow_id)["processing_state"] == "PROCESSING"


def test_direct_done_race_lost_at_commit_replays(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blinded retry of a committed direct DONE reaches COMMIT, loses on
    the (device_event_id, command_sequence) key and replays the winner —
    no second Movement, no projection change."""
    plating = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=5)
    event_id = str(uuid.uuid4())
    real_lookup = machine_processing.committed_command
    misses = {"remaining": 2}

    def blind_then_real(session: Session, device_event_id: str) -> Any:
        if misses["remaining"] > 0:
            misses["remaining"] -= 1
            return []
        return real_lookup(session, device_event_id)

    original = _done(client, plating, flow_id, pn, 5, device_event_id=event_id)
    assert original.status_code == 201, original.text
    # Back into processing with NEW intents so the blinded retry passes
    # validation and reaches COMMIT.
    assert _transfer(client, plating, deburr, flow_id, pn, 5).status_code == 201
    assert _transfer(client, deburr, plating, flow_id, pn, 5).status_code == 201
    count = _movement_count(db_engine)
    monkeypatch.setattr(direct_processing, "committed_command", blind_then_real)
    try:
        loser = _done(client, plating, flow_id, pn, 5, device_event_id=event_id)
    finally:
        monkeypatch.undo()
    assert loser.status_code == 200, loser.text
    assert loser.json() == original.json()
    assert misses["remaining"] == 0
    assert _movement_count(db_engine) == count
    assert _inventory_flow(client, plating.area_id, flow_id)["processing_state"] == "PROCESSING"


# ---------------------------------------------------------------------------
# Implicit completion on transfer
# ---------------------------------------------------------------------------


def test_transfer_of_processing_quantity_completes_implicitly_in_one_command(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    lathe = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, plating, quantity=15)
    event_id = str(uuid.uuid4())
    candidate = _resolve_pn(client, lathe.station_id, pn)["candidates"][0]
    assert candidate["processing_state"] == "PROCESSING" and candidate["machine_id"] is None

    response = _transfer(client, plating, lathe, flow_id, pn, 15, device_event_id=event_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["completed_movement_id"] is not None
    assert body["completed_machine_id"] is None
    assert body["from_area_id"] == plating.area_id and body["to_area_id"] == lathe.area_id

    history = _movements(db_engine, flow_id)
    assert [m.movement_type for m in history] == ["RECEIVED", "AREA_COMPLETED", "TRANSFERRED"]
    completed, transferred = history[1], history[2]
    assert completed.id == body["completed_movement_id"]
    assert transferred.id == body["movement_id"]
    assert completed.id < transferred.id
    # One command: one device_event_id, sequence 1 then 2, same fingerprint.
    assert completed.device_event_id == transferred.device_event_id == event_id
    assert (completed.command_sequence, transferred.command_sequence) == (1, 2)
    assert completed.metadata["command"] == {"kind": "TRANSFER", "size": 2}
    assert transferred.metadata["command"] == {"kind": "TRANSFER", "size": 2}
    assert completed.metadata["request_fingerprint"] == transferred.metadata["request_fingerprint"]
    # The completion happens inside the source Area, without a Machine.
    assert completed.from_area_id == completed.to_area_id == plating.area_id
    assert completed.source_machine_id is None and completed.destination_machine_id is None
    assert completed.operation_id == plating.operation_id
    assert completed.station_id == lathe.station_id
    assert transferred.source_machine_id is None and transferred.destination_machine_id is None
    assert transferred.operation_id == lathe.operation_id

    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_area_id == lathe.area_id and flow_row.current_machine_id is None
    assert _inventory_flow(client, lathe.area_id, flow_id)["processing_state"] == "QUEUED"
    assert _inventory(client, plating.area_id)["total_quantity"] == 0
    _assert_replay_matches(db_engine, flow_id, ProcessingState.QUEUED)

    # The whole command replays as one: identical result, nothing added.
    count = _movement_count(db_engine)
    replay = _transfer(client, plating, lathe, flow_id, pn, 15, device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json() == body
    assert _movement_count(db_engine) == count
    mismatch = _transfer(client, plating, lathe, flow_id, pn, 14, device_event_id=event_id)
    assert mismatch.status_code == 409
    assert _movement_count(db_engine) == count


def test_finished_direct_quantity_transfers_with_transferred_alone(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=3)
    assert _done(client, plating, flow_id, pn, 3).status_code == 201
    count = _movement_count(db_engine)

    response = _transfer(client, plating, deburr, flow_id, pn, 3)
    assert response.status_code == 201, response.text
    assert response.json()["completed_movement_id"] is None
    assert response.json()["completed_machine_id"] is None
    assert _movement_count(db_engine) == count + 1
    assert [m.movement_type for m in _movements(db_engine, flow_id)] == [
        "RECEIVED",
        "AREA_COMPLETED",
        "TRANSFERRED",
    ]
    assert _movements(db_engine, flow_id)[-1].command_sequence == 1
    # Arrived in another Area without Machines: processing again.
    assert _inventory_flow(client, deburr.area_id, flow_id)["processing_state"] == "PROCESSING"


def test_partial_transfer_of_processing_quantity_splits_and_completes_the_part(
    client: TestClient, db_engine: Engine
) -> None:
    """Phase 8: the selected part is completed and transferred, the
    remainder keeps processing directly (the full lineage coverage
    lives in test_quantity_split_merge_api)."""
    plating = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=6)
    count = _movement_count(db_engine)
    response = _transfer(client, plating, deburr, flow_id, pn, 2)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_quantity_flow_id"] == flow_id
    assert body["remainder_quantity"] == 4
    assert body["completed_movement_id"] is not None and body["completed_machine_id"] is None
    # 3 SPLIT + AREA_COMPLETED + TRANSFERRED, one command.
    assert _movement_count(db_engine) == count + 5
    assert _flow_row(db_engine, flow_id).status == "SPLIT"
    remainder = _inventory_flow(client, plating.area_id, body["remainder_quantity_flow_id"])
    assert remainder["processing_state"] == "PROCESSING" and remainder["quantity"] == 4
    moved = _inventory_flow(client, deburr.area_id, body["quantity_flow_id"])
    assert moved["processing_state"] == "PROCESSING" and moved["quantity"] == 2


def test_implicit_direct_completion_is_all_or_nothing(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two Movements commit together or not at all — at the database:
    while the transfer holds its locks (after both idempotency checks),
    another row takes (device_event_id, 2), so the TRANSFERRED insert
    fails at COMMIT. The AREA_COMPLETED staged before it never persists,
    the flow stays exactly where it was, and the reused id is reported
    as a conflict, not a success."""
    plating = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=2)
    other_id, other_pn = _release(client, plating, quantity=1)
    event_id = str(uuid.uuid4())
    count = _movement_count(db_engine)
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
                source_area_id=plating.area_id,
                target_area_id=deburr.area_id,
                quantity=2,
                operation_id=None,
                confirm_route_deviation=False,
                route_deviation_reason=None,
                device_event_id=event_id,
            )

    thread = threading.Thread(target=_run_collecting, args=(results, "transfer", run_transfer))
    thread.start()
    try:
        assert pause.first_inside.wait(timeout=20)
        # A foreign row takes sequence 2 under the same id (a second
        # completion of the other flow, written directly).
        assert _done(client, plating, other_id, other_pn, 1).status_code == 201
        with db_engine.begin() as connection:
            connection.execute(
                sa.insert(models.PartMovement).values(
                    quantity_flow_id=other_id,
                    part_number=other_pn,
                    movement_type="AREA_COMPLETED",
                    quantity=1,
                    from_area_id=plating.area_id,
                    to_area_id=plating.area_id,
                    operation_id=plating.operation_id,
                    station_id=plating.station_id,
                    occurred_at=sa.func.now(),
                    server_received_at=sa.func.now(),
                    device_event_id=event_id,
                    command_sequence=2,
                    metadata_={"request_fingerprint": "foreign"},
                )
            )
    finally:
        pause.let_first_finish.set()
    thread.join(timeout=20)

    assert isinstance(results["transfer"], IdempotencyConflictError)
    # Only the DONE and the foreign row were added: no AREA_COMPLETED of
    # the paused transfer, no TRANSFERRED.
    assert _movement_count(db_engine) == count + 2
    assert [m.movement_type for m in _movements(db_engine, flow_id)] == ["RECEIVED"]
    flow_row = _flow_row(db_engine, flow_id)
    assert flow_row.current_area_id == plating.area_id and flow_row.current_machine_id is None
    assert _inventory_flow(client, plating.area_id, flow_id)["processing_state"] == "PROCESSING"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_direct_done_versus_transfer_of_one_flow_has_one_serial_outcome(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transfer (which completes implicitly) holds the flow lock; the
    DONE blocks behind it, re-reads the moved flow and is refused — the
    quantity is completed exactly once."""
    plating = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=4)
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
                source_area_id=plating.area_id,
                target_area_id=deburr.area_id,
                quantity=4,
                operation_id=None,
                confirm_route_deviation=False,
                route_deviation_reason=None,
                device_event_id=str(uuid.uuid4()),
            )

    def run_done() -> Any:
        with Session(db_engine) as session:
            return direct_processing.complete_direct_processing(
                session,
                station_id=plating.station_id,
                part_number=pn,
                quantity_flow_id=flow_id,
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
    assert results["transfer"].completed_movement_id is not None
    assert results["transfer"].completed_machine_id is None
    assert isinstance(results["done"], ConflictError)
    assert "not in the Area" in results["done"].message
    types = [m.movement_type for m in _movements(db_engine, flow_id)]
    assert types.count("AREA_COMPLETED") == 1
    assert types[-1] == "TRANSFERRED"
    assert _flow_row(db_engine, flow_id).current_area_id == deburr.area_id


def test_two_direct_dones_of_one_flow_serialize_with_one_winner(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    plating = _Cell(client)
    flow_id, pn = _release(client, plating, quantity=3)
    pause = _Pause(machines.area_has_machines)
    # The first DONE pauses after judging the Area mode — under the
    # flow lock, before its write; the second blocks on the flow lock.
    monkeypatch.setattr(machine_processing, "area_has_machines", pause)
    results: dict[str, Any] = {}

    def run_done(name: str) -> Callable[[], Any]:
        def action() -> Any:
            with Session(db_engine) as session:
                return direct_processing.complete_direct_processing(
                    session,
                    station_id=plating.station_id,
                    part_number=pn,
                    quantity_flow_id=flow_id,
                    quantity=3,
                    device_event_id=str(uuid.uuid4()),
                )

        return action

    first = threading.Thread(
        target=_run_collecting, args=(results, "first", run_done("first")), daemon=True
    )
    second = threading.Thread(
        target=_run_collecting, args=(results, "second", run_done("second")), daemon=True
    )
    try:
        first.start()
        assert pause.first_inside.wait(timeout=20)
        second.start()
        time.sleep(1.0)
        assert "second" not in results
    finally:
        pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)

    assert isinstance(results["first"], machine_processing.MachineProcessingResult)
    assert results["first"].created is True and results["first"].machine_id is None
    assert isinstance(results["second"], ConflictError)
    assert "already completed" in results["second"].message
    types = [m.movement_type for m in _movements(db_engine, flow_id)]
    assert types == ["RECEIVED", "AREA_COMPLETED"]


# ---------------------------------------------------------------------------
# Projection replay and the Area mode
# ---------------------------------------------------------------------------


def test_projection_replay_reconstructs_processing_ready_and_transferred(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    lathe = _Cell(client, machine_count=1)
    external = _Cell(client)
    processing_id, _ = _release(client, plating, quantity=1)
    finished_id, finished_pn = _release(client, plating, quantity=2)
    assert _done(client, plating, finished_id, finished_pn, 2).status_code == 201
    to_machine_area_id, to_machine_area_pn = _release(client, plating, quantity=3)
    assert (
        _transfer(client, plating, lathe, to_machine_area_id, to_machine_area_pn, 3).status_code
        == 201
    )
    to_direct_area_id, to_direct_area_pn = _release(client, plating, quantity=4)
    assert _done(client, plating, to_direct_area_id, to_direct_area_pn, 4).status_code == 201
    assert (
        _transfer(client, plating, external, to_direct_area_id, to_direct_area_pn, 4).status_code
        == 201
    )

    with Session(db_engine) as session:
        rebuilt = projections.rebuild_current_positions(session)
    assert rebuilt[processing_id] == (plating.area_id, None, ProcessingState.PROCESSING)
    assert rebuilt[finished_id] == (plating.area_id, None, ProcessingState.READY_TO_TRANSFER)
    assert rebuilt[to_machine_area_id] == (lathe.area_id, None, ProcessingState.QUEUED)
    assert rebuilt[to_direct_area_id] == (external.area_id, None, ProcessingState.PROCESSING)
    for flow_id, state in (
        (processing_id, ProcessingState.PROCESSING),
        (finished_id, ProcessingState.READY_TO_TRANSFER),
        (to_machine_area_id, ProcessingState.QUEUED),
        (to_direct_area_id, ProcessingState.PROCESSING),
    ):
        _assert_replay_matches(db_engine, flow_id, state)
        assert _inventory_flow(client, rebuilt[flow_id].area_id, flow_id)["processing_state"] == (
            state.value
        )


def test_the_area_mode_follows_from_its_active_machines(
    client: TestClient, db_engine: Engine
) -> None:
    """No assignment-mode configuration exists: the first active Machine
    turns an Area into QUEUE_AND_ASSIGN (its held quantity reads QUEUED,
    ASSIGN becomes valid, the direct DONE is refused); retiring the last
    one returns it to direct processing. The history never changes."""
    cell = _Cell(client)
    flow_id, pn = _release(client, cell, quantity=5)
    assert _inventory_flow(client, cell.area_id, flow_id)["processing_state"] == "PROCESSING"
    assert _context(client, cell.station_id)["has_machines"] is False

    machine_id = _create_machine(client, cell.area_id)
    assert _context(client, cell.station_id)["has_machines"] is True
    flow = _inventory_flow(client, cell.area_id, flow_id)
    assert flow["processing_state"] == "QUEUED"
    assert flow["available_actions"] == ["ASSIGN", "TRANSFER", "SCRAP"]
    inventory = _inventory(client, cell.area_id)
    assert inventory["queued_quantity"] == 5 and inventory["processing_quantity"] == 0
    assert [card["machine"]["id"] for card in inventory["machines"]] == [machine_id]
    refused = _done(client, cell, flow_id, pn, 5)
    assert refused.status_code == 409 and "has Machines" in refused.json()["detail"]
    _assert_replay_matches(db_engine, flow_id, ProcessingState.QUEUED)

    retired = client.post(f"/api/machines/{machine_id}/retire", json={"reason": "end of life"})
    assert retired.status_code == 200, retired.text
    assert _context(client, cell.station_id)["has_machines"] is False
    flow = _inventory_flow(client, cell.area_id, flow_id)
    assert flow["processing_state"] == "PROCESSING"
    assert flow["available_actions"] == ["DONE", "TRANSFER", "SCRAP"]
    assert _inventory(client, cell.area_id)["machines"] == []
    _assert_replay_matches(db_engine, flow_id, ProcessingState.PROCESSING)
    assert [m.movement_type for m in _movements(db_engine, flow_id)] == ["RECEIVED"]
    assert _done(client, cell, flow_id, pn, 5).status_code == 201


def test_processing_quantity_of_a_pn_in_several_areas_requires_selection(
    client: TestClient,
) -> None:
    """Several flows of one PN are returned as they are — nothing picked."""
    plating = _Cell(client)
    deburr = _Cell(client)
    first_id, pn = _release(client, plating, quantity=1)
    second_id, _ = _release(client, plating, quantity=2, part_number=pn)
    resolved = _resolve_pn(client, plating.station_id, pn)
    assert resolved["requires_selection"] is True
    assert {f["quantity_flow_id"] for f in resolved["in_area"]} == {first_id, second_id}
    assert {f["processing_state"] for f in resolved["in_area"]} == {"PROCESSING"}
    elsewhere = _resolve_pn(client, deburr.station_id, pn)
    assert elsewhere["requires_selection"] is True
    assert {c["processing_state"] for c in elsewhere["candidates"]} == {"PROCESSING"}
