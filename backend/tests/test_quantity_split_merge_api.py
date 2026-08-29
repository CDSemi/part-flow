"""Integration tests for Phase 8 — quantity SPLIT and MERGED workflows.

Exercises the full request path — FastAPI routes, the Application
commands and read models, and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain. Covered
per IMPLEMENTATION_ROADMAP Phase 8 and PROJECT_PROFILE §8.7, §8.11, §11
(Quantity Splitting, Quantity Merging, Quantity Integrity):

- partial quantity on every existing workflow — Assign, QUEUE, Machine
  DONE, direct DONE, Transfer (from QUEUED, ON_MACHINE, PROCESSING and
  READY_TO_TRANSFER): the source splits atomically inside the same
  command (three ``SPLIT`` rows, then the action rows, one
  ``device_event_id``), the action applies to the selected child only,
  the remainder keeps the source's state and context, the source closes
  and leaves the inventory, the PN and the total quantity are
  conserved, and the Work Order Demand is untouched;
- full-quantity regression: no SPLIT, no lineage edge, no closed flow;
- split lineage (1 → N through a tree of splits) and merge lineage
  (N → 1) reconstructed from the edge table and the Movements;
- PLANNED routes: every child carries its own snapshot copy at the
  source's route position, the transfer records the CHILD's step (or
  deviation), the remainder keeps the route expectation; FLOATING
  children carry none;
- the explicit merge: same PN, same Area, same state, same Machine,
  same Operation, same route context → one resulting flow with the
  summed quantity, every source closed, ancestry kept; every
  incompatibility refused explicitly with zero writes; never automatic;
- idempotency: whole-command replay (split + action), conflicting reuse
  (a different quantity, another kind), a race lost at COMMIT;
- concurrency and stale commands: two partial commands on one source
  have exactly one winner; a command naming a consumed flow is refused;
- read models: consumed flows never appear as active inventory; the
  Work Order context follows the lineage;
- the projection replay rebuilds every ACTIVE descendant / merge
  result from history alone and agrees with the stored projection.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations; the module database is dropped afterwards.
"""

import os
import threading
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
from app.application import machine_processing, machines, projections
from app.core.config import get_settings
from app.domain.enums import ProcessingState
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_quantity_split_merge_api"
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


def _create_area(client: TestClient) -> dict[str, Any]:
    department = client.post("/api/departments", json={"name": _unique("DEPT")})
    assert department.status_code == 201, department.text
    response = client.post(
        "/api/areas", json={"department_id": department.json()["id"], "name": _unique("AREA")}
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


class _Released:
    def __init__(self, flow_id: int, part_number: str, work_order_id: int, demand_id: int) -> None:
        self.flow_id = flow_id
        self.part_number = part_number
        self.work_order_id = work_order_id
        self.demand_id = demand_id


def _release(
    client: TestClient,
    cell: _Cell,
    *,
    quantity: int = 10,
    part_number: str | None = None,
    operation_id: int | None = None,
    route_template_id: int | None = None,
) -> _Released:
    """Release one flow (FLOATING, or PLANNED with a template) into the cell's Area."""
    pn = part_number or _unique("PN")
    response = client.post(
        "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
    )
    assert response.status_code == 201, response.text
    work_order_id = int(response.json()["id"])
    demand_id = int(response.json()["demands"][0]["id"])
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "route_mode": "PLANNED" if route_template_id is not None else "FLOATING",
        "starting_area_id": cell.area_id,
        "operation_id": operation_id or cell.operation_id,
        "confirm_active_quantity": part_number is not None,
        "device_event_id": str(uuid.uuid4()),
    }
    if route_template_id is not None:
        payload["route_template_id"] = route_template_id
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release", json=payload
    )
    assert released.status_code == 201, released.text
    return _Released(int(released.json()["quantity_flow_id"]), pn, work_order_id, demand_id)


_ACTION_PATHS = {
    "ASSIGN": "machine-assignments",
    "QUEUE": "machine-releases",
    "DONE": "area-completions",
}


def _act(
    client: TestClient,
    kind: str,
    cell: _Cell,
    flow_id: int,
    pn: str,
    quantity: Any,
    *,
    machine_id: int | None = None,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    if machine_id is not None:
        payload["machine_id"] = machine_id
    payload.update(overrides)
    return client.post(f"/api/scan-stations/{cell.station_id}/{_ACTION_PATHS[kind]}", json=payload)


def _transfer(
    client: TestClient,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: Any,
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


def _merge(client: TestClient, cell: _Cell, pn: str, flow_ids: Any, **kw: Any) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_ids": flow_ids,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return client.post(f"/api/scan-stations/{cell.station_id}/merges", json=payload)


def _assigned(client: TestClient, lathe: _Cell, *, quantity: int = 10) -> _Released:
    released = _release(client, lathe, quantity=quantity)
    response = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        quantity,
        machine_id=lathe.machine_id,
    )
    assert response.status_code == 201, response.text
    return released


# ---------------------------------------------------------------------------
# Reading helpers
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


def _command(engine: Engine, device_event_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.PartMovement.__table__)
                .where(models.PartMovement.__table__.c.device_event_id == device_event_id)
                .order_by(models.PartMovement.__table__.c.command_sequence)
            )
        )


def _edges(engine: Engine, **where: Any) -> list[Any]:
    table = models.QuantityFlowLineage.__table__
    query = sa.select(table).order_by(table.c.id)
    for column, value in where.items():
        query = query.where(getattr(table.c, column) == value)
    with engine.connect() as connection:
        return list(connection.execute(query))


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            model.__tablename__: connection.execute(
                sa.select(sa.func.count()).select_from(model.__table__)
            ).scalar_one()
            for model in (
                models.QuantityFlow,
                models.PartMovement,
                models.QuantityFlowLineage,
                models.AssignedRoute,
                models.AssignedRouteStep,
                models.WorkOrderDemand,
            )
        }


def _active_quantity(engine: Engine, pn: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.coalesce(sa.func.sum(models.QuantityFlow.quantity), 0)).where(
                    models.QuantityFlow.part_number == pn,
                    models.QuantityFlow.status == "ACTIVE",
                )
            ).scalar_one()
        )


def _demand_requested(client: TestClient, released: _Released) -> int:
    detail = client.get(f"/api/work-orders/{released.work_order_id}")
    assert detail.status_code == 200, detail.text
    for demand in detail.json()["demands"]:
        if demand["id"] == released.demand_id:
            return int(demand["requested_quantity"])
    raise AssertionError(f"demand {released.demand_id} not listed")


def _inventory(client: TestClient, area_id: int) -> dict[str, Any]:
    response = client.get(f"/api/areas/{area_id}/inventory")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _inventory_flows(client: TestClient, area_id: int) -> dict[int, dict[str, Any]]:
    return {
        flow["quantity_flow_id"]: flow
        for line in _inventory(client, area_id)["lines"]
        for flow in line["flows"]
    }


def _resolve_pn(client: TestClient, station_id: str, pn: str) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{station_id}/scans/resolve", json={"part_number": pn}
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _assert_projection_matches_history(engine: Engine, *flow_ids: int) -> None:
    """The stored projection of every named flow equals the replay from
    history, closed flows are exactly the consumed ones, and no closed
    flow keeps a Machine."""
    with Session(engine) as session:
        positions = projections.rebuild_current_positions(session)
        consumed = projections.consumed_flow_ids(session, flow_ids)
    for flow_id in flow_ids:
        row = _flow_row(engine, flow_id)
        if row.status == "ACTIVE":
            assert flow_id not in consumed and row.closed_at is None
            position = positions[flow_id]
            assert (position.area_id, position.machine_id) == (
                row.current_area_id,
                row.current_machine_id,
            ), flow_id
        else:
            assert flow_id in consumed and flow_id not in positions
            assert row.closed_at is not None and row.current_machine_id is None


def _state(engine: Engine, flow_id: int) -> ProcessingState:
    with Session(engine) as session:
        return projections.rebuild_current_positions(session)[flow_id].processing_state


class _Pause:
    """Test seam: the FIRST call pauses after completing — while the
    caller holds its locks — until released; later calls pass through."""

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


def _assert_split_command(
    engine: Engine, event_id: str, *, source: int, selected: int, remainder: int, action: list[str]
) -> None:
    rows = _command(engine, event_id)
    assert [row.movement_type for row in rows] == ["SPLIT", "SPLIT", "SPLIT", *action]
    assert [row.command_sequence for row in rows] == list(range(1, len(rows) + 1))
    assert [row.quantity_flow_id for row in rows[:3]] == [source, selected, remainder]
    assert all(row.quantity_flow_id == selected for row in rows[3:])
    assert rows[0].quantity == rows[1].quantity + rows[2].quantity
    assert len({row.metadata["request_fingerprint"] for row in rows}) == 1
    assert all(row.metadata["command"]["size"] == len(rows) for row in rows)
    for row in rows[:3]:
        assert row.from_area_id == row.to_area_id
        assert row.station_id is not None
        assert row.source_machine_id is None and row.destination_machine_id is None
    edges = _edges(engine, parent_flow_id=source)
    assert [(edge.child_flow_id, edge.relation, edge.device_event_id) for edge in edges] == [
        (selected, "SPLIT", event_id),
        (remainder, "SPLIT", event_id),
    ]
    assert _flow_row(engine, source).status == "SPLIT"


# ---------------------------------------------------------------------------
# Partial quantity on the existing workflows
# ---------------------------------------------------------------------------


def test_partial_assign_splits_and_assigns_only_the_selected_part(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    before = _counts(db_engine)
    event_id = str(uuid.uuid4())

    response = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        4,
        machine_id=lathe.machine_id,
        device_event_id=event_id,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    selected, remainder = body["quantity_flow_id"], body["remainder_quantity_flow_id"]
    assert body["source_quantity_flow_id"] == released.flow_id
    assert body["quantity"] == 4 and body["remainder_quantity"] == 6
    assert body["movement_type"] == "ASSIGNED_TO_MACHINE" and body["machine_id"] == lathe.machine_id
    assert selected != released.flow_id and remainder not in (selected, released.flow_id)

    _assert_split_command(
        db_engine,
        event_id,
        source=released.flow_id,
        selected=selected,
        remainder=remainder,
        action=["ASSIGNED_TO_MACHINE"],
    )
    after = _counts(db_engine)
    assert after["quantity_flows"] == before["quantity_flows"] + 2
    assert after["part_movements"] == before["part_movements"] + 4
    assert after["quantity_flow_lineage"] == before["quantity_flow_lineage"] + 2
    assert after["assigned_routes"] == before["assigned_routes"]  # FLOATING: no snapshot
    assert after["work_order_demands"] == before["work_order_demands"]
    assert _demand_requested(client, released) == 500

    # Conservation and the PN: the children carry the source's PN and sum to it.
    for flow_id in (selected, remainder):
        row = _flow_row(db_engine, flow_id)
        assert row.part_number == released.part_number and row.current_area_id == lathe.area_id
    assert _active_quantity(db_engine, released.part_number) == 10
    assert _flow_row(db_engine, selected).current_machine_id == lathe.machine_id
    assert _flow_row(db_engine, remainder).current_machine_id is None
    source_row = _flow_row(db_engine, released.flow_id)
    assert source_row.status == "SPLIT" and source_row.closed_at is not None
    assert source_row.quantity == 10  # history: the consumed quantity is never rewritten

    # Read models: the source is gone, the children carry state, actions and WO context.
    flows = _inventory_flows(client, lathe.area_id)
    assert released.flow_id not in flows
    assert flows[selected]["processing_state"] == "ON_MACHINE"
    assert flows[selected]["available_actions"] == ["DONE", "QUEUE", "TRANSFER"]
    assert flows[remainder]["processing_state"] == "QUEUED"
    assert flows[remainder]["available_actions"] == ["ASSIGN", "TRANSFER"]
    for flow_id in (selected, remainder):
        assert flows[flow_id]["work_order"]["work_order_demand_id"] == released.demand_id
        assert flows[flow_id]["operation"]["id"] == lathe.operation_id
    inventory = _inventory(client, lathe.area_id)
    assert inventory["total_quantity"] == 10
    assert inventory["queued_quantity"] == 6 and inventory["on_machine_quantity"] == 4
    assert inventory["machines"][0]["total_quantity"] == 4
    machine = client.get(f"/api/machines/{lathe.machine_id}").json()
    assert machine["assigned_quantity"] == 4 and machine["operational_state"] == "RUNNING"
    _assert_projection_matches_history(db_engine, released.flow_id, selected, remainder)
    assert _state(db_engine, selected) == ProcessingState.ON_MACHINE
    assert _state(db_engine, remainder) == ProcessingState.QUEUED


@pytest.mark.parametrize(
    ("kind", "selected_state", "selected_actions"),
    [("QUEUE", "QUEUED", ["ASSIGN", "TRANSFER"]), ("DONE", "READY_TO_TRANSFER", ["TRANSFER"])],
)
def test_partial_queue_and_machine_done_leave_the_remainder_on_the_machine(
    client: TestClient,
    db_engine: Engine,
    kind: str,
    selected_state: str,
    selected_actions: list[str],
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _assigned(client, lathe, quantity=10)
    event_id = str(uuid.uuid4())
    response = _act(
        client,
        kind,
        lathe,
        released.flow_id,
        released.part_number,
        3,
        machine_id=lathe.machine_id,
        device_event_id=event_id,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    selected, remainder = body["quantity_flow_id"], body["remainder_quantity_flow_id"]
    assert body["quantity"] == 3 and body["remainder_quantity"] == 7
    _assert_split_command(
        db_engine,
        event_id,
        source=released.flow_id,
        selected=selected,
        remainder=remainder,
        action=["RELEASED_FROM_MACHINE" if kind == "QUEUE" else "AREA_COMPLETED"],
    )
    assert _command(db_engine, event_id)[3].source_machine_id == lathe.machine_id
    flows = _inventory_flows(client, lathe.area_id)
    assert flows[selected]["processing_state"] == selected_state
    assert flows[selected]["available_actions"] == selected_actions
    assert flows[selected]["machine_id"] is None
    assert flows[remainder]["processing_state"] == "ON_MACHINE"
    assert flows[remainder]["machine_id"] == lathe.machine_id
    assert _inventory(client, lathe.area_id)["machines"][0]["total_quantity"] == 7
    assert client.get(f"/api/machines/{lathe.machine_id}").json()["assigned_quantity"] == 7
    assert _active_quantity(db_engine, released.part_number) == 10
    _assert_projection_matches_history(db_engine, released.flow_id, selected, remainder)
    assert _state(db_engine, remainder) == ProcessingState.ON_MACHINE


def test_partial_direct_done_leaves_the_remainder_processing(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    released = _release(client, plating, quantity=9)
    event_id = str(uuid.uuid4())
    response = _act(
        client, "DONE", plating, released.flow_id, released.part_number, 5, device_event_id=event_id
    )
    assert response.status_code == 201, response.text
    body = response.json()
    selected, remainder = body["quantity_flow_id"], body["remainder_quantity_flow_id"]
    assert body["machine_id"] is None and body["remainder_quantity"] == 4
    _assert_split_command(
        db_engine,
        event_id,
        source=released.flow_id,
        selected=selected,
        remainder=remainder,
        action=["AREA_COMPLETED"],
    )
    flows = _inventory_flows(client, plating.area_id)
    assert flows[selected]["processing_state"] == "READY_TO_TRANSFER"
    assert flows[remainder]["processing_state"] == "PROCESSING"
    assert flows[remainder]["available_actions"] == ["DONE", "TRANSFER"]
    inventory = _inventory(client, plating.area_id)
    assert inventory["processing_quantity"] == 4 and inventory["finished_quantity"] == 5
    _assert_projection_matches_history(db_engine, released.flow_id, selected, remainder)
    assert _state(db_engine, remainder) == ProcessingState.PROCESSING
    # The remainder can be split again: lineage depth 2 keeps the state.
    again = _act(client, "DONE", plating, remainder, released.part_number, 1)
    assert again.status_code == 201, again.text
    rest = again.json()["remainder_quantity_flow_id"]
    assert _inventory_flows(client, plating.area_id)[rest]["processing_state"] == "PROCESSING"
    assert _active_quantity(db_engine, released.part_number) == 9
    _assert_projection_matches_history(db_engine, remainder, again.json()["quantity_flow_id"], rest)
    with Session(db_engine) as session:
        assert projections.origin_flow_ids(session, rest) == {released.flow_id}


def test_partial_transfer_from_every_source_state(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    pn = _unique("PN")
    queued = _release(client, lathe, quantity=10, part_number=pn)
    on_machine = _assigned(client, lathe, quantity=10)
    finished = _assigned(client, lathe, quantity=10)
    assert (
        _act(
            client,
            "DONE",
            lathe,
            finished.flow_id,
            finished.part_number,
            10,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    cases = [
        (queued, ["TRANSFERRED"], "QUEUED", None),
        (on_machine, ["AREA_COMPLETED", "TRANSFERRED"], "ON_MACHINE", lathe.machine_id),
        (finished, ["TRANSFERRED"], "READY_TO_TRANSFER", None),
    ]
    for released, action, remainder_state, machine_id in cases:
        event_id = str(uuid.uuid4())
        response = _transfer(
            client,
            lathe,
            deburr,
            released.flow_id,
            released.part_number,
            4,
            device_event_id=event_id,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        selected, remainder = body["quantity_flow_id"], body["remainder_quantity_flow_id"]
        assert body["source_quantity_flow_id"] == released.flow_id
        assert body["quantity"] == 4 and body["remainder_quantity"] == 6
        assert body["completed_machine_id"] == machine_id
        assert (body["completed_movement_id"] is not None) == (len(action) == 2)
        _assert_split_command(
            db_engine,
            event_id,
            source=released.flow_id,
            selected=selected,
            remainder=remainder,
            action=action,
        )
        moved = _flow_row(db_engine, selected)
        assert moved.current_area_id == deburr.area_id and moved.current_machine_id is None
        rest = _flow_row(db_engine, remainder)
        assert rest.current_area_id == lathe.area_id and rest.current_machine_id == machine_id
        assert _inventory_flows(client, lathe.area_id)[remainder]["processing_state"] == (
            remainder_state
        )
        assert _inventory_flows(client, deburr.area_id)[selected]["processing_state"] == (
            "PROCESSING"
        )
        assert _active_quantity(db_engine, released.part_number) == 10
        _assert_projection_matches_history(db_engine, released.flow_id, selected, remainder)
    assert client.get(f"/api/machines/{lathe.machine_id}").json()["assigned_quantity"] == 6


def test_full_quantity_commands_never_split(client: TestClient, db_engine: Engine) -> None:
    """Regression: the whole quantity is handled as before — one action
    row, no SPLIT, no lineage edge, no closed flow, null split fields."""
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    released = _release(client, lathe, quantity=10)
    before = _counts(db_engine)
    steps: list[Callable[[], Any]] = [
        lambda: _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ),
        lambda: _act(
            client,
            "QUEUE",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ),
        lambda: _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ),
        lambda: _act(
            client,
            "DONE",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ),
        lambda: _transfer(client, lathe, deburr, released.flow_id, released.part_number, 10),
        lambda: _act(client, "DONE", deburr, released.flow_id, released.part_number, 10),
    ]
    for step in steps:
        response = step()
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["quantity_flow_id"] == released.flow_id
        assert body["source_quantity_flow_id"] is None
        assert body["remainder_quantity_flow_id"] is None and body["remainder_quantity"] is None
        rows = _command(db_engine, body["device_event_id"])
        assert "SPLIT" not in {row.movement_type for row in rows}
    after = _counts(db_engine)
    assert after["quantity_flows"] == before["quantity_flows"]
    assert after["quantity_flow_lineage"] == before["quantity_flow_lineage"]
    assert after["part_movements"] == before["part_movements"] + 6
    assert _flow_row(db_engine, released.flow_id).status == "ACTIVE"
    _assert_projection_matches_history(db_engine, released.flow_id)


def test_exceeding_and_zero_quantity_still_write_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    released = _release(client, lathe, quantity=10)
    before = _counts(db_engine)
    for quantity in (11, 0, -1):
        assign = _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            quantity,
            machine_id=lathe.machine_id,
        )
        assert assign.status_code == 422, assign.text
        transfer = _transfer(
            client, lathe, deburr, released.flow_id, released.part_number, quantity
        )
        assert transfer.status_code == 422, transfer.text
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# PLANNED and FLOATING route behavior
# ---------------------------------------------------------------------------


def _snapshot_steps(engine: Engine, assigned_route_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AssignedRouteStep.__table__)
                .where(models.AssignedRouteStep.__table__.c.assigned_route_id == assigned_route_id)
                .order_by(models.AssignedRouteStep.__table__.c.sequence)
            )
        )


def test_planned_split_gives_every_child_its_own_snapshot_at_the_route_position(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client, machine_count=1)
    mill = _Cell(client)
    template = _create_route_template(
        db_engine,
        [(material.area_id, material.operation_id), (lathe.area_id, None), (mill.area_id, None)],
    )
    released = _release(client, material, quantity=10, route_template_id=template)
    source_route = _flow_row(db_engine, released.flow_id).assigned_route_id
    assert source_route is not None
    before = _counts(db_engine)

    # ON_ROUTE partial transfer Material → Lathe.
    response = _transfer(client, material, lathe, released.flow_id, released.part_number, 4)
    assert response.status_code == 201, response.text
    body = response.json()
    selected, remainder = body["quantity_flow_id"], body["remainder_quantity_flow_id"]
    assert body["route_deviation"] is None

    rows = {flow_id: _flow_row(db_engine, flow_id) for flow_id in (selected, remainder)}
    routes = {rows[selected].assigned_route_id, rows[remainder].assigned_route_id, source_route}
    assert len(routes) == 3 and None not in routes  # three distinct snapshots, never shared
    assert all(rows[flow_id].route_mode == "PLANNED" for flow_id in rows)
    after = _counts(db_engine)
    assert after["assigned_routes"] == before["assigned_routes"] + 2
    assert after["assigned_route_steps"] == before["assigned_route_steps"] + 6
    for flow_id in (selected, remainder):
        copied = _snapshot_steps(db_engine, rows[flow_id].assigned_route_id)
        original = _snapshot_steps(db_engine, source_route)
        assert [(s.sequence, s.area_id, s.operation_id) for s in copied] == [
            (s.sequence, s.area_id, s.operation_id) for s in original
        ]
    # The child's SPLIT row positions it at the source's last known step
    # (its own copy); the TRANSFERRED records the CHILD's matched step.
    selected_steps = _snapshot_steps(db_engine, rows[selected].assigned_route_id)
    command = _command(db_engine, body["device_event_id"])
    assert command[1].assigned_route_step_id == selected_steps[0].id
    assert command[2].assigned_route_step_id == (
        _snapshot_steps(db_engine, rows[remainder].assigned_route_id)[0].id
    )
    assert command[0].assigned_route_step_id is None
    assert body["assigned_route_step_id"] == selected_steps[1].id
    assert command[-1].assigned_route_step_id == selected_steps[1].id

    # The remainder keeps the route expectation: Lathe is still its next step.
    resolved = _resolve_pn(client, lathe.station_id, released.part_number)
    candidates = {c["quantity_flow_id"]: c for c in resolved["candidates"]}
    assert candidates[remainder]["route_status"] == "ON_ROUTE"
    assert candidates[remainder]["expected_next_area"]["id"] == lathe.area_id
    # The moved child expects Mill next.
    resolved_mill = _resolve_pn(client, mill.station_id, released.part_number)
    mill_candidates = {c["quantity_flow_id"]: c for c in resolved_mill["candidates"]}
    assert mill_candidates[selected]["route_status"] == "ON_ROUTE"
    assert mill_candidates[remainder]["route_status"] == "DEVIATION"
    _assert_projection_matches_history(db_engine, released.flow_id, selected, remainder)


def test_planned_partial_deviation_is_recorded_against_the_child_snapshot(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    elsewhere = _Cell(client)
    template = _create_route_template(db_engine, [(material.area_id, None), (lathe.area_id, None)])
    released = _release(client, material, quantity=10, route_template_id=template)
    before = _counts(db_engine)
    refused = _transfer(client, material, elsewhere, released.flow_id, released.part_number, 3)
    assert refused.status_code == 409 and refused.json().get("confirmation_required") is True
    assert _counts(db_engine) == before  # the confirmation request writes nothing, splits nothing
    confirmed = _transfer(
        client,
        material,
        elsewhere,
        released.flow_id,
        released.part_number,
        3,
        confirm_route_deviation=True,
        route_deviation_reason="Rework at another cell",
    )
    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()
    child_route = _flow_row(db_engine, body["quantity_flow_id"]).assigned_route_id
    child_steps = _snapshot_steps(db_engine, child_route)
    deviation = body["route_deviation"]
    assert deviation["kind"] == "AREA" and deviation["confirmed"] is True
    assert deviation["expected_next_step_id"] == child_steps[1].id
    assert deviation["last_known_step_id"] == child_steps[0].id
    assert body["assigned_route_step_id"] is None
    remainder = body["remainder_quantity_flow_id"]
    resolved = _resolve_pn(client, lathe.station_id, released.part_number)
    assert {c["quantity_flow_id"]: c["route_status"] for c in resolved["candidates"]}[
        remainder
    ] == ("ON_ROUTE")


def test_floating_children_carry_no_snapshot(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=6)
    before = _counts(db_engine)
    response = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        2,
        machine_id=lathe.machine_id,
    )
    assert response.status_code == 201, response.text
    for flow_id in (
        response.json()["quantity_flow_id"],
        response.json()["remainder_quantity_flow_id"],
    ):
        row = _flow_row(db_engine, flow_id)
        assert row.route_mode == "FLOATING" and row.assigned_route_id is None
    assert _counts(db_engine)["assigned_routes"] == before["assigned_routes"]
    assert all(
        row.assigned_route_step_id is None
        for row in _command(db_engine, response.json()["device_event_id"])
    )


# ---------------------------------------------------------------------------
# The explicit merge
# ---------------------------------------------------------------------------


def test_merge_combines_compatible_flows_and_keeps_ancestry(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    pn = _unique("PN")
    first = _release(client, lathe, quantity=2, part_number=pn)
    second = _release(client, lathe, quantity=4, part_number=pn)
    # A split child is a valid merge source too (1 → N then N → 1).
    split = _act(client, "ASSIGN", lathe, second.flow_id, pn, 1, machine_id=lathe.machine_id)
    assert split.status_code == 201, split.text
    queued_child = split.json()["remainder_quantity_flow_id"]  # 3 pcs QUEUED
    before = _counts(db_engine)
    event_id = str(uuid.uuid4())

    # Never automatic: two flows of one PN in one Area stay separate until merged.
    assert len(_inventory_flows(client, lathe.area_id)) == 3
    response = _merge(client, lathe, pn, [queued_child, first.flow_id], device_event_id=event_id)
    assert response.status_code == 201, response.text
    body = response.json()
    result = body["quantity_flow_id"]
    assert body["quantity"] == 5 and body["part_number"] == pn
    assert body["processing_state"] == "QUEUED" and body["machine_id"] is None
    assert body["source_quantity_flow_ids"] == sorted([first.flow_id, queued_child])
    assert body["area_id"] == lathe.area_id and body["operation_id"] == lathe.operation_id

    rows = _command(db_engine, event_id)
    assert [row.movement_type for row in rows] == ["MERGED", "MERGED", "MERGED"]
    assert [row.command_sequence for row in rows] == [1, 2, 3]
    assert [row.quantity_flow_id for row in rows] == [
        *sorted([first.flow_id, queued_child]),
        result,
    ]
    assert rows[0].quantity + rows[1].quantity == rows[2].quantity == 5
    assert all(row.metadata["command"] == {"kind": "MERGE", "size": 3} for row in rows)
    edges = _edges(db_engine, child_flow_id=result)
    assert [(e.parent_flow_id, e.relation) for e in edges] == [
        (flow_id, "MERGED") for flow_id in sorted([first.flow_id, queued_child])
    ]
    for source in (first.flow_id, queued_child):
        row = _flow_row(db_engine, source)
        assert row.status == "MERGED" and row.closed_at is not None
    after = _counts(db_engine)
    assert after["quantity_flows"] == before["quantity_flows"] + 1
    assert after["part_movements"] == before["part_movements"] + 3
    assert after["quantity_flow_lineage"] == before["quantity_flow_lineage"] + 2
    assert _active_quantity(db_engine, pn) == 6

    flows = _inventory_flows(client, lathe.area_id)
    assert set(flows) == {result, split.json()["quantity_flow_id"]}
    assert flows[result]["processing_state"] == "QUEUED"
    assert flows[result]["available_actions"] == ["ASSIGN", "TRANSFER"]
    # Two demands feed the result: no single Work Order context is guessed.
    assert flows[result]["work_order"] is None
    with Session(db_engine) as session:
        assert projections.origin_flow_ids(session, result) == {first.flow_id, second.flow_id}
    _assert_projection_matches_history(
        db_engine, first.flow_id, second.flow_id, queued_child, result
    )
    # The result is an ordinary flow: it can be assigned, and the source is refused.
    assigned = _act(client, "ASSIGN", lathe, result, pn, 5, machine_id=lathe.machine_id)
    assert assigned.status_code == 201, assigned.text
    stale = _act(client, "ASSIGN", lathe, first.flow_id, pn, 2, machine_id=lathe.machine_id)
    assert stale.status_code == 409 and "merged" in stale.json()["detail"]


def test_merge_of_on_machine_flows_keeps_the_machine_and_the_work_order(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    split = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        4,
        machine_id=lathe.machine_id,
    )
    assert split.status_code == 201
    rest = _act(
        client,
        "ASSIGN",
        lathe,
        split.json()["remainder_quantity_flow_id"],
        released.part_number,
        6,
        machine_id=lathe.machine_id,
    )
    assert rest.status_code == 201
    sources = [split.json()["quantity_flow_id"], rest.json()["quantity_flow_id"]]
    response = _merge(client, lathe, released.part_number, sources)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["processing_state"] == "ON_MACHINE" and body["machine_id"] == lathe.machine_id
    result = body["quantity_flow_id"]
    assert _flow_row(db_engine, result).current_machine_id == lathe.machine_id
    assert client.get(f"/api/machines/{lathe.machine_id}").json()["assigned_quantity"] == 10
    flows = _inventory_flows(client, lathe.area_id)
    assert set(flows) == {result}
    assert flows[result]["work_order"]["work_order_demand_id"] == released.demand_id
    _assert_projection_matches_history(db_engine, released.flow_id, *sources, result)
    assert _state(db_engine, result) == ProcessingState.ON_MACHINE
    for source in sources:
        assert _flow_row(db_engine, source).current_machine_id is None


def test_merge_of_planned_flows_needs_an_equal_snapshot_and_position(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    template = _create_route_template(db_engine, [(material.area_id, None), (lathe.area_id, None)])
    other_template = _create_route_template(
        db_engine, [(material.area_id, None), (lathe.area_id, None)]
    )
    pn = _unique("PN")
    a = _release(client, material, quantity=3, part_number=pn, route_template_id=template)
    b = _release(client, material, quantity=4, part_number=pn, route_template_id=template)
    c = _release(client, material, quantity=5, part_number=pn, route_template_id=other_template)
    before = _counts(db_engine)
    # Same structure from another template: structurally equal snapshots merge.
    response = _merge(client, material, pn, [a.flow_id, b.flow_id, c.flow_id])
    assert response.status_code == 201, response.text
    result = response.json()["quantity_flow_id"]
    row = _flow_row(db_engine, result)
    assert row.route_mode == "PLANNED" and row.assigned_route_id is not None
    assert _counts(db_engine)["assigned_routes"] == before["assigned_routes"] + 1
    result_steps = _snapshot_steps(db_engine, row.assigned_route_id)
    assert _command(db_engine, response.json()["device_event_id"])[-1].assigned_route_step_id == (
        result_steps[0].id
    )
    assert response.json()["quantity"] == 12
    # Mixed provenance (two Route Templates): the result records none.
    assert _route_provenance(db_engine, row.assigned_route_id) is None
    # The result is on its route: Lathe is its next step.
    resolved = _resolve_pn(client, lathe.station_id, pn)
    assert [c["quantity_flow_id"] for c in resolved["candidates"]] == [result]
    assert resolved["candidates"][0]["route_status"] == "ON_ROUTE"

    # A different route position (one flow already moved on) never merges.
    d = _release(client, material, quantity=1, part_number=pn, route_template_id=template)
    moved = _transfer(client, material, lathe, result, pn, 12)
    assert moved.status_code == 201, moved.text
    back = _transfer(
        client,
        lathe,
        material,
        result,
        pn,
        12,
        confirm_route_deviation=True,
        route_deviation_reason="back",
    )
    assert back.status_code == 201, back.text
    before = _counts(db_engine)
    refused = _merge(client, material, pn, [d.flow_id, result])
    assert refused.status_code == 409 and "route context" in refused.json()["detail"]
    assert _counts(db_engine) == before


def test_incompatible_merges_are_refused_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=2)
    plating = _Cell(client, operation_count=2)
    pn = _unique("PN")
    queued = _release(client, lathe, quantity=1, part_number=pn)
    on_first = _release(client, lathe, quantity=2, part_number=pn)
    on_second = _release(client, lathe, quantity=3, part_number=pn)
    for released, quantity, machine_id in (
        (on_first, 2, lathe.machine_ids[0]),
        (on_second, 3, lathe.machine_ids[1]),
    ):
        assigned = _act(
            client, "ASSIGN", lathe, released.flow_id, pn, quantity, machine_id=machine_id
        )
        assert assigned.status_code == 201, assigned.text
    elsewhere = _release(client, plating, quantity=4, part_number=pn)
    other_op = _release(
        client, plating, quantity=5, part_number=pn, operation_id=plating.operation_ids[1]
    )
    other_pn = _release(client, lathe, quantity=6)
    template = _create_route_template(db_engine, [(lathe.area_id, None)])
    planned = _release(client, lathe, quantity=7, part_number=pn, route_template_id=template)
    consumed = _release(client, lathe, quantity=8, part_number=pn)
    split = _act(client, "ASSIGN", lathe, consumed.flow_id, pn, 1, machine_id=lathe.machine_ids[0])
    assert split.status_code == 201
    queued_child = split.json()["remainder_quantity_flow_id"]
    before = _counts(db_engine)

    cases: list[tuple[_Cell, str, Any, int, str]] = [
        (lathe, pn, [queued.flow_id, on_first.flow_id], 409, "processing states"),
        (lathe, pn, [on_first.flow_id, on_second.flow_id], 409, "same Machine"),
        (plating, pn, [elsewhere.flow_id, other_op.flow_id], 409, "different Operations"),
        (lathe, pn, [queued.flow_id, planned.flow_id], 409, "route context"),
        (lathe, pn, [queued.flow_id, elsewhere.flow_id], 409, "not in Area"),
        (plating, pn, [queued.flow_id, elsewhere.flow_id], 409, "not in Area"),
        (lathe, pn, [queued.flow_id, other_pn.flow_id], 422, "does not match"),
        (lathe, pn, [queued.flow_id, consumed.flow_id], 409, "split"),
        (lathe, pn, [queued.flow_id], 422, "at least two"),
        (lathe, pn, [queued.flow_id, queued.flow_id], 422, "once"),
        (lathe, pn, [queued.flow_id, queued.flow_id + 1_000_000], 422, "does not exist"),
        (lathe, pn, [queued.flow_id, "x"], 422, ""),
        (lathe, pn, [queued.flow_id, 1.5], 422, ""),
    ]
    for cell, part_number, ids, status, fragment in cases:
        response = _merge(client, cell, part_number, ids)
        assert response.status_code == status, (ids, response.text)
        assert fragment in str(response.json().get("detail", "")), (ids, response.text)
    assert _counts(db_engine) == before
    # The compatible pair still merges afterwards (nothing was corrupted).
    good = _merge(client, lathe, pn, [queued.flow_id, queued_child])
    assert good.status_code == 201, good.text
    _assert_projection_matches_history(
        db_engine, queued.flow_id, queued_child, good.json()["quantity_flow_id"]
    )


def test_merge_station_preconditions(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    pn = _unique("PN")
    a = _release(client, lathe, quantity=1, part_number=pn)
    b = _release(client, lathe, quantity=2, part_number=pn)
    before = _counts(db_engine)
    unknown = client.post(
        "/api/scan-stations/NO-SUCH-STATION/merges",
        json={
            "part_number": pn,
            "quantity_flow_ids": [a.flow_id, b.flow_id],
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert unknown.status_code == 404
    deactivated = client.patch(f"/api/scan-stations/{lathe.station_id}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text
    inactive = _merge(client, lathe, pn, [a.flow_id, b.flow_id])
    assert inactive.status_code == 409 and "inactive" in inactive.json()["detail"]
    extra = _merge(client, lathe, pn, [a.flow_id, b.flow_id], quantity=3)
    assert extra.status_code == 422
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_partial_command_replays_as_a_whole_and_rejects_a_different_payload(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    released = _assigned(client, lathe, quantity=10)
    event_id = str(uuid.uuid4())
    first = _transfer(
        client, lathe, deburr, released.flow_id, released.part_number, 4, device_event_id=event_id
    )
    assert first.status_code == 201, first.text
    after = _counts(db_engine)
    # Move the child on, so a replay must come from the immutable command, not current state.
    moved = _act(client, "DONE", deburr, first.json()["quantity_flow_id"], released.part_number, 4)
    assert moved.status_code == 201, moved.text
    after_move = _counts(db_engine)

    replay = _transfer(
        client, lathe, deburr, released.flow_id, released.part_number, 4, device_event_id=event_id
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert _counts(db_engine) == after_move

    for payload in (
        {"quantity": 5},  # a different part
        {"quantity": 10},  # the whole flow
    ):
        conflict = _transfer(
            client,
            lathe,
            deburr,
            released.flow_id,
            released.part_number,
            payload["quantity"],
            device_event_id=event_id,
        )
        assert conflict.status_code == 409 and "different" in conflict.json()["detail"]
    # The same id reused for another command kind on the child.
    other_kind = _act(
        client,
        "DONE",
        deburr,
        first.json()["remainder_quantity_flow_id"],
        released.part_number,
        1,
        device_event_id=event_id,
    )
    assert other_kind.status_code == 409
    assert _counts(db_engine) == after_move
    assert after["quantity_flows"] == after_move["quantity_flows"]

    # Whole-flow idempotency of the other commands with a split prefix.
    assign_id = str(uuid.uuid4())
    remainder = first.json()["remainder_quantity_flow_id"]
    queued = _act(
        client,
        "QUEUE",
        lathe,
        remainder,
        released.part_number,
        2,
        machine_id=lathe.machine_id,
        device_event_id=assign_id,
    )
    assert queued.status_code == 201, queued.text
    again = _act(
        client,
        "QUEUE",
        lathe,
        remainder,
        released.part_number,
        2,
        machine_id=lathe.machine_id,
        device_event_id=assign_id,
    )
    assert again.status_code == 200 and again.json() == queued.json()
    mismatch = _act(
        client,
        "DONE",
        lathe,
        remainder,
        released.part_number,
        2,
        machine_id=lathe.machine_id,
        device_event_id=assign_id,
    )
    assert mismatch.status_code == 409


def _route_provenance(engine: Engine, assigned_route_id: int) -> int | None:
    with engine.connect() as connection:
        value = connection.execute(
            sa.select(models.AssignedRoute.source_route_template_id).where(
                models.AssignedRoute.id == assigned_route_id
            )
        ).scalar_one()
        return None if value is None else int(value)


def test_planned_merge_keeps_the_provenance_only_when_every_source_shares_it(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    template = _create_route_template(db_engine, [(material.area_id, None), (lathe.area_id, None)])
    twin = _create_route_template(db_engine, [(material.area_id, None), (lathe.area_id, None)])
    pn = _unique("PN")
    a = _release(client, material, quantity=1, part_number=pn, route_template_id=template)
    b = _release(client, material, quantity=2, part_number=pn, route_template_id=template)
    # A split child inherits its source's provenance.
    split = _act(client, "DONE", material, b.flow_id, pn, 1)
    assert split.status_code == 201, split.text
    remainder = split.json()["remainder_quantity_flow_id"]
    assert _route_provenance(db_engine, _flow_row(db_engine, remainder).assigned_route_id) == (
        template
    )
    # Same template everywhere: the result keeps it.
    same = _merge(client, material, pn, [a.flow_id, remainder])
    assert same.status_code == 201, same.text
    same_result = same.json()["quantity_flow_id"]
    assert _route_provenance(db_engine, _flow_row(db_engine, same_result).assigned_route_id) == (
        template
    )
    # A structurally identical snapshot from another template: still
    # mergeable, but the result records NO provenance — never the first
    # source's.
    c = _release(client, material, quantity=3, part_number=pn, route_template_id=twin)
    mixed = _merge(client, material, pn, [same_result, c.flow_id])
    assert mixed.status_code == 201, mixed.text
    mixed_route = _flow_row(db_engine, mixed.json()["quantity_flow_id"]).assigned_route_id
    assert mixed_route is not None
    assert _route_provenance(db_engine, mixed_route) is None
    assert [(s.sequence, s.area_id) for s in _snapshot_steps(db_engine, mixed_route)] == [
        (10, material.area_id),
        (20, lathe.area_id),
    ]
    # And the reverse order of sources gives the same answer.
    d = _release(client, material, quantity=4, part_number=pn, route_template_id=template)
    e = _release(client, material, quantity=5, part_number=pn, route_template_id=twin)
    reverse = _merge(client, material, pn, [e.flow_id, d.flow_id])
    assert reverse.status_code == 201, reverse.text
    assert (
        _route_provenance(
            db_engine, _flow_row(db_engine, reverse.json()["quantity_flow_id"]).assigned_route_id
        )
        is None
    )


def test_merge_replay_is_the_original_response_whatever_happened_since(
    client: TestClient, db_engine: Engine
) -> None:
    """The replay is rebuilt from the immutable MERGED command alone: the
    resulting flow moving on, changing state, or the Area changing mode
    never alters it, and nothing is written."""
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    pn = _unique("PN")
    flows = [_release(client, lathe, quantity=q, part_number=pn).flow_id for q in (3, 4)]
    event_id = str(uuid.uuid4())
    original = _merge(client, lathe, pn, flows, device_event_id=event_id)
    assert original.status_code == 201, original.text
    assert original.json()["processing_state"] == "QUEUED"
    assert original.json()["machine_id"] is None
    result = original.json()["quantity_flow_id"]

    # The result continues: Assign → DONE → Transfer (and a partial one).
    assert (
        _act(client, "ASSIGN", lathe, result, pn, 7, machine_id=lathe.machine_id).status_code == 201
    )
    assert (
        _act(client, "DONE", lathe, result, pn, 7, machine_id=lathe.machine_id).status_code == 201
    )
    assert _transfer(client, lathe, deburr, result, pn, 2).status_code == 201
    after = _counts(db_engine)
    replay = _merge(client, lathe, pn, flows, device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original.json()
    assert _counts(db_engine) == after

    # ON_MACHINE merge: the recorded Machine survives the result leaving it.
    on = [_assigned(client, lathe, quantity=q).flow_id for q in (1, 2)]
    on_event = str(uuid.uuid4())
    on_pn = _flow_row(db_engine, on[0]).part_number
    # Both sources share one PN only when released as such: re-release.
    second = _release(client, lathe, quantity=2, part_number=on_pn)
    assert (
        _act(
            client, "ASSIGN", lathe, second.flow_id, on_pn, 2, machine_id=lathe.machine_id
        ).status_code
        == 201
    )
    on_original = _merge(client, lathe, on_pn, [on[0], second.flow_id], device_event_id=on_event)
    assert on_original.status_code == 201, on_original.text
    assert on_original.json()["machine_id"] == lathe.machine_id
    assert on_original.json()["processing_state"] == "ON_MACHINE"
    on_result = on_original.json()["quantity_flow_id"]
    assert (
        _act(client, "QUEUE", lathe, on_result, on_pn, 3, machine_id=lathe.machine_id).status_code
        == 201
    )
    after = _counts(db_engine)
    on_replay = _merge(client, lathe, on_pn, [on[0], second.flow_id], device_event_id=on_event)
    assert on_replay.status_code == 200 and on_replay.json() == on_original.json()
    assert _counts(db_engine) == after


def test_merge_replay_ignores_an_area_mode_change(client: TestClient, db_engine: Engine) -> None:
    plating = _Cell(client)
    pn = _unique("PN")
    flows = [_release(client, plating, quantity=q, part_number=pn).flow_id for q in (5, 6)]
    event_id = str(uuid.uuid4())
    original = _merge(client, plating, pn, flows, device_event_id=event_id)
    assert original.status_code == 201, original.text
    assert original.json()["processing_state"] == "PROCESSING"
    # The first Machine turns the Area into a QUEUE_AND_ASSIGN Area: the
    # result now READS as QUEUED, the original merge response does not change.
    machine_id = _create_machine(client, plating.area_id)
    result = original.json()["quantity_flow_id"]
    assert _inventory_flows(client, plating.area_id)[result]["processing_state"] == "QUEUED"
    after = _counts(db_engine)
    replay = _merge(client, plating, pn, flows, device_event_id=event_id)
    assert replay.status_code == 200 and replay.json() == original.json()
    assert replay.json()["processing_state"] == "PROCESSING"
    assert _counts(db_engine) == after
    # And back: retire the Machine, the replay is still the original.
    retired = client.post(f"/api/machines/{machine_id}/retire", json={"reason": "audit"})
    assert retired.status_code == 200, retired.text
    assert _inventory_flows(client, plating.area_id)[result]["processing_state"] == "PROCESSING"
    again = _merge(client, plating, pn, flows, device_event_id=event_id)
    assert again.status_code == 200 and again.json() == original.json()
    assert _counts(db_engine) == after


def test_merge_replays_and_rejects_a_different_set(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    pn = _unique("PN")
    flows = [_release(client, lathe, quantity=q, part_number=pn).flow_id for q in (1, 2, 3)]
    event_id = str(uuid.uuid4())
    first = _merge(client, lathe, pn, flows[:2], device_event_id=event_id)
    assert first.status_code == 201, first.text
    after = _counts(db_engine)
    replay = _merge(client, lathe, pn, list(reversed(flows[:2])), device_event_id=event_id)
    assert replay.status_code == 200 and replay.json() == first.json()
    conflict = _merge(
        client, lathe, pn, [first.json()["quantity_flow_id"], flows[2]], device_event_id=event_id
    )
    assert conflict.status_code == 409
    cross = _act(
        client,
        "ASSIGN",
        lathe,
        flows[2],
        pn,
        3,
        machine_id=lathe.machine_id,
        device_event_id=event_id,
    )
    assert cross.status_code == 409
    assert _counts(db_engine) == after


# ---------------------------------------------------------------------------
# Concurrency and stale commands
# ---------------------------------------------------------------------------


def test_two_partial_commands_on_one_source_have_one_winner(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    lathe = _Cell(client, machine_count=2)
    released = _release(client, lathe, quantity=10)
    pause = _Pause(machines.lock_machine)
    monkeypatch.setattr("app.application.machine_processing.lock_machine", pause)
    results: dict[str, Any] = {}

    def run(name: str, machine_id: int, quantity: int) -> Callable[[], Any]:
        def action() -> Any:
            return _act(
                client,
                "ASSIGN",
                lathe,
                released.flow_id,
                released.part_number,
                quantity,
                machine_id=machine_id,
            )

        return action

    first = threading.Thread(
        target=_run_collecting, args=(results, "first", run("first", lathe.machine_ids[0], 4))
    )
    first.start()
    assert pause.first_inside.wait(timeout=20)
    second = threading.Thread(
        target=_run_collecting, args=(results, "second", run("second", lathe.machine_ids[1], 7))
    )
    second.start()
    second.join(timeout=2)
    assert second.is_alive(), "the second command must block on the source flow lock"
    pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)
    assert results["first"].status_code == 201, results["first"].text
    assert results["second"].status_code == 409, results["second"].text
    assert "split" in results["second"].json()["detail"]
    assert _active_quantity(db_engine, released.part_number) == 10
    assert client.get(f"/api/machines/{lathe.machine_ids[1]}").json()["assigned_quantity"] == 0
    body = results["first"].json()
    _assert_projection_matches_history(
        db_engine, released.flow_id, body["quantity_flow_id"], body["remainder_quantity_flow_id"]
    )


def test_stale_commands_naming_a_consumed_flow_are_refused(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    pn = _unique("PN")
    a = _release(client, lathe, quantity=4, part_number=pn)
    b = _release(client, lathe, quantity=6, part_number=pn)
    merged = _merge(client, lathe, pn, [a.flow_id, b.flow_id])
    assert merged.status_code == 201
    before = _counts(db_engine)
    for response in (
        _act(client, "ASSIGN", lathe, a.flow_id, pn, 4, machine_id=lathe.machine_id),
        _transfer(client, lathe, deburr, b.flow_id, pn, 6),
        _transfer(client, lathe, deburr, b.flow_id, pn, 2),
        _merge(client, lathe, pn, [a.flow_id, merged.json()["quantity_flow_id"]]),
    ):
        assert response.status_code == 409, response.text
        assert "merged" in response.json()["detail"]
    assert _counts(db_engine) == before
    # Split sources are refused the same way.
    split = _act(
        client,
        "ASSIGN",
        lathe,
        merged.json()["quantity_flow_id"],
        pn,
        3,
        machine_id=lathe.machine_id,
    )
    assert split.status_code == 201
    before = _counts(db_engine)
    stale = _transfer(client, lathe, deburr, merged.json()["quantity_flow_id"], pn, 10)
    assert stale.status_code == 409 and "split" in stale.json()["detail"]
    assert _counts(db_engine) == before


def test_blinded_retry_of_a_partial_command_still_serializes_on_the_flow_lock(
    db_engine: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when both idempotency checks are blind, the retry locks the
    (now closed) source and fails its state precondition before any
    write — the flow lock is the serialization, the UNIQUE key the net."""
    from app.application import direct_processing

    plating = _Cell(client)
    released = _release(client, plating, quantity=8)
    event_id = str(uuid.uuid4())
    winner = _act(
        client, "DONE", plating, released.flow_id, released.part_number, 3, device_event_id=event_id
    )
    assert winner.status_code == 201, winner.text
    after = _counts(db_engine)
    real_lookup = machine_processing.committed_command
    misses = {"remaining": 2}

    def blind_then_real(session: Session, device_event_id: str) -> Any:
        if misses["remaining"] > 0:
            misses["remaining"] -= 1
            return []
        return real_lookup(session, device_event_id)

    monkeypatch.setattr(direct_processing, "committed_command", blind_then_real)
    try:
        loser = _act(
            client,
            "DONE",
            plating,
            released.flow_id,
            released.part_number,
            3,
            device_event_id=event_id,
        )
    finally:
        monkeypatch.undo()
    assert loser.status_code == 409 and "split" in loser.json()["detail"]
    # Refused under the flow lock, before the second (blind) re-check
    # was even reached.
    assert misses["remaining"] == 1
    assert _counts(db_engine) == after


def test_partial_command_lost_at_commit_persists_nothing_of_its_split(
    db_engine: Engine, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blinded retry that reuses the id on ANOTHER active flow reaches
    COMMIT with its split already staged (child flows, edges, rows),
    loses on the (device_event_id, command_sequence) key, and ends as
    an explicit conflict: the rollback discards the whole staged split."""
    from app.application import direct_processing

    plating = _Cell(client)
    pn = _unique("PN")
    first = _release(client, plating, quantity=8, part_number=pn)
    second = _release(client, plating, quantity=8, part_number=pn)
    event_id = str(uuid.uuid4())
    winner = _act(client, "DONE", plating, first.flow_id, pn, 3, device_event_id=event_id)
    assert winner.status_code == 201, winner.text
    after = _counts(db_engine)
    real_lookup = machine_processing.committed_command
    misses = {"remaining": 2}

    def blind_then_real(session: Session, device_event_id: str) -> Any:
        if misses["remaining"] > 0:
            misses["remaining"] -= 1
            return []
        return real_lookup(session, device_event_id)

    monkeypatch.setattr(direct_processing, "committed_command", blind_then_real)
    try:
        loser = _act(client, "DONE", plating, second.flow_id, pn, 3, device_event_id=event_id)
    finally:
        monkeypatch.undo()
    assert loser.status_code == 409 and "different" in loser.json()["detail"]
    assert misses["remaining"] == 0
    assert _counts(db_engine) == after
    assert _flow_row(db_engine, second.flow_id).status == "ACTIVE"
    assert _edges(db_engine, parent_flow_id=second.flow_id) == []
    assert _inventory_flows(client, plating.area_id)[second.flow_id]["quantity"] == 8


def test_race_lost_at_commit_on_a_fresh_source_replays_the_winner(
    db_engine: Engine, client: TestClient
) -> None:
    """The database-level guarantee itself: a loser whose SPLIT rows
    collide on (device_event_id, command_sequence) at COMMIT persists
    nothing and replays the winner's whole command."""
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=8)
    event_id = str(uuid.uuid4())
    winner = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        3,
        machine_id=lathe.machine_id,
        device_event_id=event_id,
    )
    assert winner.status_code == 201
    after = _counts(db_engine)
    with Session(db_engine) as session:
        replay = machine_processing.committed_command(session, event_id)
        assert len(replay) == 4
    # Any later use of the id replays exactly the committed command.
    again = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        3,
        machine_id=lathe.machine_id,
        device_event_id=event_id,
    )
    assert again.status_code == 200 and again.json() == winner.json()
    assert _counts(db_engine) == after


# ---------------------------------------------------------------------------
# Projection rebuild across a lineage tree
# ---------------------------------------------------------------------------


def test_projection_replay_rebuilds_active_descendants_and_merge_results(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    deburr = _Cell(client)
    pn = _unique("PN")
    root = _release(client, lathe, quantity=12, part_number=pn)
    # 12 → 5 (assigned) + 7; 7 → 2 (transferred) + 5; merge the two 5s? no —
    # one is ON_MACHINE, the other QUEUED: queue the assigned one first.
    a = _act(client, "ASSIGN", lathe, root.flow_id, pn, 5, machine_id=lathe.machine_id)
    assert a.status_code == 201
    on_machine, rest7 = a.json()["quantity_flow_id"], a.json()["remainder_quantity_flow_id"]
    t = _transfer(client, lathe, deburr, rest7, pn, 2)
    assert t.status_code == 201
    moved2, rest5 = t.json()["quantity_flow_id"], t.json()["remainder_quantity_flow_id"]
    q = _act(client, "QUEUE", lathe, on_machine, pn, 5, machine_id=lathe.machine_id)
    assert q.status_code == 201
    m = _merge(client, lathe, pn, [on_machine, rest5])
    assert m.status_code == 201, m.text
    merged10 = m.json()["quantity_flow_id"]
    d = _act(client, "DONE", deburr, moved2, pn, 1)
    assert d.status_code == 201
    done1, processing1 = d.json()["quantity_flow_id"], d.json()["remainder_quantity_flow_id"]

    all_flows = [root.flow_id, on_machine, rest7, moved2, rest5, merged10, done1, processing1]
    _assert_projection_matches_history(db_engine, *all_flows)
    with Session(db_engine) as session:
        positions = projections.rebuild_current_positions(session)
        consumed = projections.consumed_flow_ids(session)
    assert {f for f in all_flows if f in consumed} == {
        root.flow_id,
        rest7,
        moved2,
        on_machine,
        rest5,
    }
    assert positions[merged10].processing_state == ProcessingState.QUEUED
    assert positions[merged10].area_id == lathe.area_id
    assert positions[done1].processing_state == ProcessingState.READY_TO_TRANSFER
    assert positions[processing1].processing_state == ProcessingState.PROCESSING
    assert positions[processing1].area_id == deburr.area_id
    assert _active_quantity(db_engine, pn) == 12
    inventory_lathe = _inventory(client, lathe.area_id)
    inventory_deburr = _inventory(client, deburr.area_id)
    assert inventory_lathe["total_quantity"] == 10 and inventory_deburr["total_quantity"] == 2
    assert set(_inventory_flows(client, lathe.area_id)) == {merged10}
    assert set(_inventory_flows(client, deburr.area_id)) == {done1, processing1}
    resolved = _resolve_pn(client, deburr.station_id, pn)
    assert {f["quantity_flow_id"] for f in resolved["in_area"]} == {done1, processing1}
    assert {c["quantity_flow_id"] for c in resolved["candidates"]} == {merged10}
    with Session(db_engine) as session:
        for flow_id in (merged10, done1, processing1):
            assert projections.origin_flow_ids(session, flow_id) == {root.flow_id}
