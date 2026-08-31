"""Integration tests for Phase 9 — command-level Undo (PROJECT_PROFILE §16).

Exercises the full request path — FastAPI routes, the Application
command and the preview read model, and PostgreSQL — against a
dedicated temporary database migrated to head by the real Alembic
chain. Covered per IMPLEMENTATION_ROADMAP Phase 9:

- Undo reverses the COMPLETE application command, never one arbitrary
  row: a plain transfer, an implicit-completion transfer
  (`AREA_COMPLETED` + `TRANSFERRED`), every in-Area command, a
  SPLIT-prefixed partial command, a merge, a Scrap and a quantity
  addition all reverse as one — compensating `REVERSED` rows in
  reverse order, one per original, the originals preserved;
- the derived state is restored exactly: position, Machine, holding
  state, route position and the Machine's derived operational state;
  flows the command closed reopen, flows it created close as
  `REVERSED`; quantity is conserved through every reversal;
- eligibility (zero writes otherwise): only the most recent operation
  of its quantity, only at the recording station, never a Management
  event, never twice, never an Undo of an Undo, never restoring onto a
  retired Machine or into a deactivated Area;
- the preview read model serves the §16 summary confirmation and the
  honest eligibility verdict;
- idempotency of the Undo's own `device_event_id` (replay, mismatched
  reuse) and the double-undo race (one winner, the DB UNIQUE backstop);
- the projection replay rebuilds the post-undo state from history
  alone and agrees with the stored projection.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations; the module database is dropped afterwards.
"""

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
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import projections
from app.core.config import get_settings
from app.domain.enums import ProcessingState
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_undo_api"
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
    def __init__(
        self, flow_id: int, part_number: str, release_event_id: str, demand_id: int
    ) -> None:
        self.flow_id = flow_id
        self.part_number = part_number
        self.release_event_id = release_event_id
        self.demand_id = demand_id


def _release(
    client: TestClient,
    cell: _Cell,
    *,
    quantity: int = 10,
    part_number: str | None = None,
    route_template_id: int | None = None,
) -> _Released:
    pn = part_number or _unique("PN")
    response = client.post(
        "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
    )
    assert response.status_code == 201, response.text
    work_order_id = int(response.json()["id"])
    demand_id = int(response.json()["demands"][0]["id"])
    release_event_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "route_mode": "PLANNED" if route_template_id is not None else "FLOATING",
        "starting_area_id": cell.area_id,
        "operation_id": cell.operation_id,
        "confirm_active_quantity": part_number is not None,
        "device_event_id": release_event_id,
    }
    if route_template_id is not None:
        payload["route_template_id"] = route_template_id
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release", json=payload
    )
    assert released.status_code == 201, released.text
    return _Released(int(released.json()["quantity_flow_id"]), pn, release_event_id, demand_id)


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


def _undo(client: TestClient, cell: _Cell, pn: str, reverses: str, **kw: Any) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "reverses_device_event_id": reverses,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return client.post(f"/api/scan-stations/{cell.station_id}/undos", json=payload)


def _preview(client: TestClient, cell: _Cell, reverses: str) -> Any:
    return client.get(f"/api/scan-stations/{cell.station_id}/undo-preview/{reverses}")


def _resolve(client: TestClient, cell: _Cell, pn: str) -> dict[str, Any]:
    response = client.post(
        f"/api/scan-stations/{cell.station_id}/scans/resolve", json={"part_number": pn}
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


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


def _command_rows(engine: Engine, device_event_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.PartMovement.__table__)
                .where(models.PartMovement.__table__.c.device_event_id == device_event_id)
                .order_by(models.PartMovement.__table__.c.command_sequence)
            )
        )


def _movement_count(engine: Engine, pn: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.count())
                .select_from(models.PartMovement.__table__)
                .where(models.PartMovement.__table__.c.part_number == pn)
            ).scalar_one()
        )


def _active_total(engine: Engine, pn: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.coalesce(sa.func.sum(models.QuantityFlow.quantity), 0)).where(
                    models.QuantityFlow.part_number == pn,
                    models.QuantityFlow.status == "ACTIVE",
                )
            ).scalar_one()
        )


def _machine(client: TestClient, machine_id: int) -> dict[str, Any]:
    response = client.get(f"/api/machines/{machine_id}")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _state(engine: Engine, flow_id: int) -> ProcessingState:
    with Session(engine) as session:
        return projections.rebuild_current_positions(session)[flow_id].processing_state


def _assert_projection_matches_replay(engine: Engine) -> None:
    """Every stored ACTIVE projection equals the pure history replay."""
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


# ---------------------------------------------------------------------------
# Undo of the single-Movement commands
# ---------------------------------------------------------------------------


def test_undo_transfer_restores_the_previous_position(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    transfer = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    transfer_event = str(transfer.json()["device_event_id"])

    response = _undo(client, lathe, released.part_number, transfer_event)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reverses_device_event_id"] == transfer_event
    assert body["reversed_kind"] == "TRANSFER"
    assert [m["original_movement_type"] for m in body["movements"]] == ["TRANSFERRED"]
    assert body["flows"] == [
        {
            "quantity_flow_id": released.flow_id,
            "quantity": 10,
            "status": "ACTIVE",
            "current_area_id": material.area_id,
            "current_machine_id": None,
        }
    ]
    # The original row is preserved; the compensating row references it.
    history = _movements(db_engine, released.flow_id)
    assert [m.movement_type for m in history] == ["RECEIVED", "TRANSFERRED", "REVERSED"]
    assert history[2].reverses_movement_id == history[1].id
    assert history[2].from_area_id == lathe.area_id
    assert history[2].to_area_id == material.area_id
    # Position and state restored; the flow is back in the source queue.
    row = _flow_row(db_engine, released.flow_id)
    assert row.status == "ACTIVE"
    assert row.current_area_id == material.area_id
    assert _state(db_engine, released.flow_id) == ProcessingState.QUEUED
    inventory = client.get(f"/api/areas/{material.area_id}/inventory").json()
    assert inventory["queued_quantity"] == 10
    _assert_projection_matches_replay(db_engine)

    # Idempotency of the Undo's own id; a second Undo (fresh id) is a
    # clean 409 — the command was already reversed.
    replay = _undo(
        client,
        lathe,
        released.part_number,
        transfer_event,
        device_event_id=body["device_event_id"],
    )
    assert replay.status_code == 200
    assert replay.json() == body
    again = _undo(client, lathe, released.part_number, transfer_event)
    assert again.status_code == 409
    assert "already been reversed" in again.json()["detail"]


def test_undo_reverses_the_complete_implicit_completion_transfer(
    client: TestClient, db_engine: Engine
) -> None:
    """An AREA_COMPLETED + TRANSFERRED command reverses as ONE: the
    quantity returns to its Machine, never to some intermediate state."""
    lathe = _Cell(client, machine_count=1)
    mill = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assert (
        _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    transfer = _transfer(client, lathe, mill, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    assert transfer.json()["completed_movement_id"] is not None
    transfer_event = str(transfer.json()["device_event_id"])
    assert _machine(client, lathe.machine_id)["assigned_quantity"] == 0

    response = _undo(client, mill, released.part_number, transfer_event)
    assert response.status_code == 201, response.text
    body = response.json()
    # Reverse order: the TRANSFERRED is compensated first, then the
    # implicit AREA_COMPLETED.
    assert [m["original_movement_type"] for m in body["movements"]] == [
        "TRANSFERRED",
        "AREA_COMPLETED",
    ]
    assert body["flows"][0]["current_area_id"] == lathe.area_id
    assert body["flows"][0]["current_machine_id"] == lathe.machine_id
    row = _flow_row(db_engine, released.flow_id)
    assert row.current_area_id == lathe.area_id
    assert row.current_machine_id == lathe.machine_id
    assert _state(db_engine, released.flow_id) == ProcessingState.ON_MACHINE
    # The Machine's derived state is restored with its assigned total.
    machine = _machine(client, lathe.machine_id)
    assert machine["assigned_quantity"] == 10
    assert machine["operational_state"] == "RUNNING"
    _assert_projection_matches_replay(db_engine)


@pytest.mark.parametrize(
    ("kind", "restored_state", "restored_machine"),
    [
        ("ASSIGN", ProcessingState.QUEUED, False),
        ("QUEUE", ProcessingState.ON_MACHINE, True),
        ("DONE", ProcessingState.ON_MACHINE, True),
    ],
)
def test_undo_in_area_commands_restore_the_prior_state(
    client: TestClient,
    db_engine: Engine,
    kind: str,
    restored_state: ProcessingState,
    restored_machine: bool,
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assert (
        _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    if kind == "ASSIGN":
        # Undo the assignment itself.
        target = _movements(db_engine, released.flow_id)[-1].device_event_id
    else:
        acted = _act(
            client,
            kind,
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        )
        assert acted.status_code == 201, acted.text
        target = str(acted.json()["device_event_id"])

    response = _undo(client, lathe, released.part_number, target)
    assert response.status_code == 201, response.text
    assert _state(db_engine, released.flow_id) == restored_state
    row = _flow_row(db_engine, released.flow_id)
    assert row.current_machine_id == (lathe.machine_id if restored_machine else None)
    assert _machine(client, lathe.machine_id)["assigned_quantity"] == (
        10 if restored_machine else 0
    )
    _assert_projection_matches_replay(db_engine)


def test_undo_direct_processing_done_restores_processing(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    released = _release(client, plating, quantity=10)
    done = _act(client, "DONE", plating, released.flow_id, released.part_number, 10)
    assert done.status_code == 201
    response = _undo(client, plating, released.part_number, str(done.json()["device_event_id"]))
    assert response.status_code == 201, response.text
    assert _state(db_engine, released.flow_id) == ProcessingState.PROCESSING
    inventory = client.get(f"/api/areas/{plating.area_id}/inventory").json()
    assert inventory["processing_quantity"] == 10
    assert inventory["finished_quantity"] == 0
    _assert_projection_matches_replay(db_engine)


# ---------------------------------------------------------------------------
# Undo of lineage commands — SPLIT prefix, MERGE
# ---------------------------------------------------------------------------


def test_undo_partial_command_reopens_the_source_and_conserves_quantity(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    partial = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        4,
        machine_id=lathe.machine_id,
    )
    assert partial.status_code == 201
    selected = int(partial.json()["quantity_flow_id"])
    remainder = int(partial.json()["remainder_quantity_flow_id"])
    partial_event = str(partial.json()["device_event_id"])
    assert _flow_row(db_engine, released.flow_id).status == "SPLIT"

    response = _undo(client, lathe, released.part_number, partial_event)
    assert response.status_code == 201, response.text
    body = response.json()
    # Four originals reversed: SPLIT ×3 + the ASSIGNED_TO_MACHINE.
    assert len(body["movements"]) == 4
    flows = {f["quantity_flow_id"]: f for f in body["flows"]}
    assert flows[released.flow_id]["status"] == "ACTIVE"
    assert flows[released.flow_id]["quantity"] == 10
    assert flows[selected]["status"] == "REVERSED"
    assert flows[remainder]["status"] == "REVERSED"
    # The source is whole and queued again; the children never count as
    # quantity again; nothing was lost or duplicated.
    source = _flow_row(db_engine, released.flow_id)
    assert source.status == "ACTIVE"
    assert source.quantity == 10
    assert source.closed_at is None
    assert _flow_row(db_engine, selected).status == "REVERSED"
    assert _flow_row(db_engine, remainder).status == "REVERSED"
    assert _active_total(db_engine, released.part_number) == 10
    assert _state(db_engine, released.flow_id) == ProcessingState.QUEUED
    assert _machine(client, lathe.machine_id)["assigned_quantity"] == 0
    resolved = _resolve(client, lathe, released.part_number)
    assert [f["quantity_flow_id"] for f in resolved["in_area"]] == [released.flow_id]
    _assert_projection_matches_replay(db_engine)


def test_undo_merge_reopens_the_sources(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    first = _release(client, lathe, quantity=6)
    second = _release(client, lathe, quantity=4, part_number=first.part_number)
    merge = client.post(
        f"/api/scan-stations/{lathe.station_id}/merges",
        json={
            "part_number": first.part_number,
            "quantity_flow_ids": [first.flow_id, second.flow_id],
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert merge.status_code == 201, merge.text
    result = int(merge.json()["quantity_flow_id"])
    merge_event = str(merge.json()["device_event_id"])

    response = _undo(client, lathe, first.part_number, merge_event)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reversed_kind"] == "MERGE"
    flows = {f["quantity_flow_id"]: f for f in body["flows"]}
    assert flows[first.flow_id]["status"] == "ACTIVE"
    assert flows[second.flow_id]["status"] == "ACTIVE"
    assert flows[result]["status"] == "REVERSED"
    assert _flow_row(db_engine, result).status == "REVERSED"
    assert _active_total(db_engine, first.part_number) == 10
    resolved = _resolve(client, lathe, first.part_number)
    assert sorted(f["quantity_flow_id"] for f in resolved["in_area"]) == sorted(
        [first.flow_id, second.flow_id]
    )
    # The reopened sources are combinable again — the read model applies
    # the same context rule to the restored state.
    assert resolved["combine_groups"] == [[first.flow_id, second.flow_id]]
    _assert_projection_matches_replay(db_engine)


# ---------------------------------------------------------------------------
# Undo of the Phase 9 quantity events
# ---------------------------------------------------------------------------


def test_undo_scrap_returns_the_quantity_to_its_machine(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assert (
        _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    scrap = client.post(
        f"/api/scan-stations/{lathe.station_id}/scraps",
        json={
            "part_number": released.part_number,
            "quantity_flow_id": released.flow_id,
            "quantity": 10,
            "reason": "damaged",
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert scrap.status_code == 201, scrap.text
    assert _resolve(client, lathe, released.part_number)["scrapped_quantity"] == 10

    response = _undo(client, lathe, released.part_number, str(scrap.json()["device_event_id"]))
    assert response.status_code == 201, response.text
    assert response.json()["reversed_kind"] == "SCRAP"
    row = _flow_row(db_engine, released.flow_id)
    assert row.status == "ACTIVE"
    assert row.current_machine_id == lathe.machine_id
    assert _state(db_engine, released.flow_id) == ProcessingState.ON_MACHINE
    # The scrapped total no longer counts the reversed scrap.
    assert _resolve(client, lathe, released.part_number)["scrapped_quantity"] == 0
    assert _machine(client, lathe.machine_id)["assigned_quantity"] == 10
    _assert_projection_matches_replay(db_engine)


def test_undo_addition_closes_the_introduced_flow(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    addition = client.post(
        f"/api/scan-stations/{lathe.station_id}/quantity-additions",
        json={
            "part_number": released.part_number,
            "quantity": 5,
            "reason": "found at the rack",
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert addition.status_code == 201, addition.text
    added_flow = int(addition.json()["quantity_flow_id"])
    assert _active_total(db_engine, released.part_number) == 15

    response = _undo(client, lathe, released.part_number, str(addition.json()["device_event_id"]))
    assert response.status_code == 201, response.text
    assert response.json()["reversed_kind"] == "ADD"
    row = _flow_row(db_engine, added_flow)
    assert row.status == "REVERSED"
    assert row.closed_at is not None
    assert _active_total(db_engine, released.part_number) == 10
    resolved = _resolve(client, lathe, released.part_number)
    assert [f["quantity_flow_id"] for f in resolved["in_area"]] == [released.flow_id]
    _assert_projection_matches_replay(db_engine)


def test_undo_repair_is_a_transfer_undo(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    assert (
        _transfer(client, material, lathe, released.flow_id, released.part_number, 10).status_code
        == 201
    )
    repair = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        10,
        repair=True,
        repair_reason="rework",
    )
    assert repair.status_code == 201
    response = _undo(client, material, released.part_number, str(repair.json()["device_event_id"]))
    assert response.status_code == 201, response.text
    assert _flow_row(db_engine, released.flow_id).current_area_id == lathe.area_id
    _assert_projection_matches_replay(db_engine)


# ---------------------------------------------------------------------------
# Route position restoration
# ---------------------------------------------------------------------------


def test_undo_restores_the_route_position(client: TestClient, db_engine: Engine) -> None:
    """An undone ON_ROUTE transfer no longer counts as a route visit:
    the next expectation is the SAME step again, and re-recording it is
    ON_ROUTE — never a deviation."""
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    template = _create_route_template(db_engine, [(material.area_id, None), (lathe.area_id, None)])
    released = _release(client, material, quantity=10, route_template_id=template)
    transfer = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    assert transfer.json()["assigned_route_step_id"] is not None
    assert (
        _undo(
            client, lathe, released.part_number, str(transfer.json()["device_event_id"])
        ).status_code
        == 201
    )

    resolved = _resolve(client, lathe, released.part_number)
    candidate = resolved["candidates"][0]
    assert candidate["route_status"] == "ON_ROUTE"
    assert candidate["expected_next_area"]["id"] == lathe.area_id
    repeated = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["assigned_route_step_id"] is not None
    assert repeated.json()["route_deviation"] is None


# ---------------------------------------------------------------------------
# Eligibility — zero writes on refusal
# ---------------------------------------------------------------------------


def test_undo_refusals_write_nothing(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    other = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    transfer = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    transfer_event = str(transfer.json()["device_event_id"])
    assign = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        10,
        machine_id=lathe.machine_id,
    )
    assert assign.status_code == 201
    before = _movement_count(db_engine, released.part_number)

    # Not the most recent operation of its quantity anymore.
    stale = _undo(client, lathe, released.part_number, transfer_event)
    assert stale.status_code == 409
    assert "no longer the most recent" in stale.json()["detail"]
    # A Management-recorded event is never undone from a station.
    management = _undo(client, material, released.part_number, released.release_event_id)
    assert management.status_code == 409
    assert "recorded by Management" in management.json()["detail"]
    # A different station may not undo it.
    elsewhere = _undo(client, other, released.part_number, str(assign.json()["device_event_id"]))
    assert elsewhere.status_code == 409
    assert "different Scan Station" in elsewhere.json()["detail"]
    # An unknown command and a PN mismatch are invalid input.
    unknown = _undo(client, lathe, released.part_number, str(uuid.uuid4()))
    assert unknown.status_code == 422
    mismatch = _undo(
        client,
        lathe,
        "OTHER-PN",
        str(assign.json()["device_event_id"]),
    )
    assert mismatch.status_code == 422
    # The Undo needs its own id.
    reused = _undo(
        client,
        lathe,
        released.part_number,
        str(assign.json()["device_event_id"]),
        device_event_id=str(assign.json()["device_event_id"]),
    )
    assert reused.status_code == 422
    assert _movement_count(db_engine, released.part_number) == before
    assert _flow_row(db_engine, released.flow_id).current_machine_id == lathe.machine_id


def test_undo_of_an_undo_is_refused(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    transfer = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    undone = _undo(client, lathe, released.part_number, str(transfer.json()["device_event_id"]))
    assert undone.status_code == 201
    # The reversal itself is permanent — corrected forward, never
    # un-reversed. (The station bound to the restored Area records it.)
    response = _undo(client, material, released.part_number, str(undone.json()["device_event_id"]))
    assert response.status_code == 409
    assert "itself a reversal" in response.json()["detail"]
    assert _flow_row(db_engine, released.flow_id).current_area_id == material.area_id


def test_undo_refuses_to_restore_onto_a_retired_machine(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assert (
        _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    done = _act(
        client,
        "DONE",
        lathe,
        released.flow_id,
        released.part_number,
        10,
        machine_id=lathe.machine_id,
    )
    assert done.status_code == 201
    retired = client.post(f"/api/machines/{lathe.machine_id}/retire", json={})
    assert retired.status_code == 200, retired.text
    response = _undo(client, lathe, released.part_number, str(done.json()["device_event_id"]))
    assert response.status_code == 409
    assert "retired" in response.json()["detail"]
    assert _state(db_engine, released.flow_id) == ProcessingState.READY_TO_TRANSFER


def test_undo_refuses_to_restore_into_a_deactivated_area(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    transfer = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    deactivated = client.patch(f"/api/areas/{material.area_id}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text
    response = _undo(client, lathe, released.part_number, str(transfer.json()["device_event_id"]))
    assert response.status_code == 409
    assert "deactivated" in response.json()["detail"]
    assert _flow_row(db_engine, released.flow_id).current_area_id == lathe.area_id


# ---------------------------------------------------------------------------
# The preview read model
# ---------------------------------------------------------------------------


def test_undo_preview_serves_the_summary_and_the_honest_verdict(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    mill = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assert (
        _act(
            client,
            "ASSIGN",
            lathe,
            released.flow_id,
            released.part_number,
            10,
            machine_id=lathe.machine_id,
        ).status_code
        == 201
    )
    transfer = _transfer(client, lathe, mill, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    transfer_event = str(transfer.json()["device_event_id"])

    preview = _preview(client, mill, transfer_event)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["eligible"] is True
    assert body["ineligible_reason"] is None
    assert body["kind"] == "TRANSFER"
    assert body["part_number"] == released.part_number
    assert body["quantity"] == 10
    assert [m["movement_type"] for m in body["movements"]] == [
        "AREA_COMPLETED",
        "TRANSFERRED",
    ]
    assert body["movements"][1]["from_area"]["id"] == lathe.area_id
    assert body["movements"][1]["to_area"]["id"] == mill.area_id
    # The effect: back ON its Machine at the source Area.
    restored = body["restored"][0]
    assert restored["status"] == "ACTIVE"
    assert restored["area"]["id"] == lathe.area_id
    assert restored["machine_id"] == lathe.machine_id
    assert restored["processing_state"] == "ON_MACHINE"
    # The preview is a read: nothing was recorded.
    assert _flow_row(db_engine, released.flow_id).current_area_id == mill.area_id

    # After later activity the verdict flips, with the reason.
    assert (
        _act(
            client,
            "ASSIGN",
            mill,
            released.flow_id,
            released.part_number,
            10,
            machine_id=mill.machine_id,
        ).status_code
        == 201
    )
    stale = _preview(client, mill, transfer_event)
    assert stale.status_code == 200
    assert stale.json()["eligible"] is False
    assert "no longer the most recent" in stale.json()["ineligible_reason"]
    unknown = _preview(client, mill, str(uuid.uuid4()))
    assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# Concurrency — the double-undo race has exactly one winner
# ---------------------------------------------------------------------------


def test_double_undo_race_has_one_winner(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    transfer = _transfer(client, material, lathe, released.flow_id, released.part_number, 10)
    assert transfer.status_code == 201
    transfer_event = str(transfer.json()["device_event_id"])

    results: dict[int, Any] = {}

    def _attempt(slot: int) -> None:
        results[slot] = _undo(client, lathe, released.part_number, transfer_event)

    threads = [threading.Thread(target=_attempt, args=(slot,)) for slot in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    statuses = sorted(response.status_code for response in results.values())
    assert statuses == [201, 409]
    # Exactly one compensating row exists per original.
    reversed_rows = [
        row for row in _movements(db_engine, released.flow_id) if row.movement_type == "REVERSED"
    ]
    assert len(reversed_rows) == 1
    assert _flow_row(db_engine, released.flow_id).current_area_id == material.area_id
    _assert_projection_matches_replay(db_engine)


# ---------------------------------------------------------------------------
# Undo after Undo — the context advances to the previous operation
# ---------------------------------------------------------------------------


def test_consecutive_undos_walk_back_through_eligible_operations(
    client: TestClient, db_engine: Engine
) -> None:
    """§16: after a confirmed Undo the next eligible previous operation
    can be undone in turn — each reversal leaves a state in which the
    previous command IS the most recent effective operation."""
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assign = _act(
        client,
        "ASSIGN",
        lathe,
        released.flow_id,
        released.part_number,
        10,
        machine_id=lathe.machine_id,
    )
    assert assign.status_code == 201
    done = _act(
        client,
        "DONE",
        lathe,
        released.flow_id,
        released.part_number,
        10,
        machine_id=lathe.machine_id,
    )
    assert done.status_code == 201

    # DONE reversed → ON_MACHINE again; then the assignment is the most
    # recent effective operation and reverses too → QUEUED.
    assert (
        _undo(client, lathe, released.part_number, str(done.json()["device_event_id"])).status_code
        == 201
    )
    assert _state(db_engine, released.flow_id) == ProcessingState.ON_MACHINE
    assert (
        _undo(
            client, lathe, released.part_number, str(assign.json()["device_event_id"])
        ).status_code
        == 201
    )
    assert _state(db_engine, released.flow_id) == ProcessingState.QUEUED
    assert _flow_row(db_engine, released.flow_id).current_machine_id is None
    history = _movements(db_engine, released.flow_id)
    assert [m.movement_type for m in history] == [
        "RECEIVED",
        "ASSIGNED_TO_MACHINE",
        "AREA_COMPLETED",
        "REVERSED",
        "REVERSED",
    ]
    _assert_projection_matches_replay(db_engine)
