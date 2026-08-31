"""Integration tests for Phase 9 — Repair, Scrap, and quantity additions.

Exercises the full request path — FastAPI routes, the Application
commands and read models, and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain. Covered
per IMPLEMENTATION_ROADMAP Phase 9 and PROJECT_PROFILE §8.11, §11, §14:

- Repair: the explicit intent on the SAME transfer command —
  `movement_reason = REPAIR` with its mandatory reason on the
  `TRANSFERRED` row; full and partial (in-command SPLIT) quantity; a
  destination the quantity never visited refused with zero writes; a
  Planned Route deviation still confirmed separately; never a Request
  Type or a demand; the read model marks candidates
  `repair_available`;
- Scrap: one confirmed action = ONE auditable `SCRAPPED` operation
  with its mandatory reason; the flow (or split-off part) closes and
  leaves active production while history, lineage and the last
  position stay; ON_MACHINE scrap records and releases the Machine;
  the PN's scrapped total appears in the resolution;
- Quantity addition: `QUANTITY_ADJUSTED · INCREASE` introduces a NEW
  FLOATING flow (mandatory reason and quantity, no MAX), enters the
  queue or direct processing by Area mode, never changes a requested
  quantity, and exists only beside existing in-Area quantity;
- quantity conservation and the §11 reconciliation
  `introduced = active + scrapped` (no `STOCKED` yet);
- idempotency (whole-command replay, conflicting reuse), zero-write
  refusals for invalid/stale input, and the projection replay;
- the addition's existing-quantity precondition under concurrency: the
  WITNESS flow row lock makes "the last ACTIVE quantity leaves the
  Area" and "Add more quantity" one serial outcome in both orders — an
  addition never commits on a stale precondition, and the loser writes
  nothing;
- the addition's station precondition under concurrency: the Scan
  Station row lock with its under-lock active/binding re-check makes a
  station rebind/deactivation and an addition one serial outcome in
  both orders — a `QUANTITY_ADJUSTED` never carries the station of an
  Area it no longer belongs to, and a refused addition writes nothing.

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
from app.application import projections
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_phase9_corrections_api"
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
    route_template_id: int | None = None,
) -> _Released:
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
        "operation_id": cell.operation_id,
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


def _scrap(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: Any, **kw: Any) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "quantity": quantity,
        "reason": "damaged during processing",
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return client.post(f"/api/scan-stations/{cell.station_id}/scraps", json=payload)


def _add(client: TestClient, cell: _Cell, pn: str, quantity: Any, **kw: Any) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "reason": "found uncounted pieces at the rack",
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(kw)
    return client.post(f"/api/scan-stations/{cell.station_id}/quantity-additions", json=payload)


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


def _requested_quantity(engine: Engine, demand_id: int) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(models.WorkOrderDemand.requested_quantity).where(
                    models.WorkOrderDemand.id == demand_id
                )
            ).scalar_one()
        )


def _assert_projection_matches_replay(engine: Engine) -> None:
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
# Repair
# ---------------------------------------------------------------------------


def test_full_repair_records_the_intent_and_reason(client: TestClient, db_engine: Engine) -> None:
    # Machine Areas on both sides: arrivals stay QUEUED, so the
    # transfers append TRANSFERRED alone (no implicit completion).
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    assert (
        _transfer(client, material, lathe, released.flow_id, released.part_number, 10).status_code
        == 201
    )

    # The read model marks the previously visited source as repairable.
    resolved = _resolve(client, material, released.part_number)
    assert resolved["candidates"][0]["repair_available"] is True

    response = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        10,
        repair=True,
        repair_reason="surface finish out of spec",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_reason"] == "REPAIR"
    assert body["reason"] == "surface finish out of spec"
    history = _movements(db_engine, released.flow_id)
    assert [m.movement_type for m in history] == ["RECEIVED", "TRANSFERRED", "TRANSFERRED"]
    assert history[-1].movement_reason == "REPAIR"
    assert history[-1].reason == "surface finish out of spec"
    # No demand and no Request Type were created (PROJECT_PROFILE §14).
    assert _requested_quantity(db_engine, released.demand_id) == 500
    assert _flow_row(db_engine, released.flow_id).current_area_id == material.area_id
    _assert_projection_matches_replay(db_engine)


def test_repair_to_an_unvisited_area_is_refused_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    other = _Cell(client)
    released = _release(client, material, quantity=8)
    resolved = _resolve(client, other, released.part_number)
    assert resolved["candidates"][0]["repair_available"] is False
    before = _movement_count(db_engine, released.part_number)
    response = _transfer(
        client,
        material,
        other,
        released.flow_id,
        released.part_number,
        8,
        repair=True,
        repair_reason="rework",
    )
    assert response.status_code == 409
    assert "not an Area this quantity has been in before" in response.json()["detail"]
    assert _movement_count(db_engine, released.part_number) == before
    assert _flow_row(db_engine, released.flow_id).current_area_id == material.area_id


def test_repair_requires_the_reason_and_the_explicit_intent(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    released = _release(client, material, quantity=6)
    assert (
        _transfer(client, material, lathe, released.flow_id, released.part_number, 6).status_code
        == 201
    )
    before = _movement_count(db_engine, released.part_number)
    missing = _transfer(
        client, lathe, material, released.flow_id, released.part_number, 6, repair=True
    )
    assert missing.status_code == 422
    assert "Repair needs a reason" in missing.json()["detail"]
    unflagged = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        6,
        repair_reason="rework",
    )
    assert unflagged.status_code == 422
    assert "without the Repair intent" in unflagged.json()["detail"]
    assert _movement_count(db_engine, released.part_number) == before


def test_partial_repair_splits_and_the_remainder_keeps_its_state(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    released = _release(client, material, quantity=10)
    assert (
        _transfer(client, material, lathe, released.flow_id, released.part_number, 10).status_code
        == 201
    )
    response = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        4,
        repair=True,
        repair_reason="burrs on four pieces",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["movement_reason"] == "REPAIR"
    assert body["source_quantity_flow_id"] == released.flow_id
    assert body["remainder_quantity"] == 6
    repaired = int(body["quantity_flow_id"])
    remainder = int(body["remainder_quantity_flow_id"])
    assert _flow_row(db_engine, released.flow_id).status == "SPLIT"
    assert _flow_row(db_engine, repaired).current_area_id == material.area_id
    remainder_row = _flow_row(db_engine, remainder)
    assert remainder_row.current_area_id == lathe.area_id
    assert remainder_row.status == "ACTIVE"
    # The repaired child carries the REPAIR TRANSFERRED with the reason.
    repaired_history = _movements(db_engine, repaired)
    assert [m.movement_type for m in repaired_history] == ["SPLIT", "TRANSFERRED"]
    assert repaired_history[-1].movement_reason == "REPAIR"
    assert _active_total(db_engine, released.part_number) == 10
    _assert_projection_matches_replay(db_engine)


def test_repair_and_plain_transfer_are_different_intents_under_one_id(
    client: TestClient,
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    released = _release(client, material, quantity=5)
    assert (
        _transfer(client, material, lathe, released.flow_id, released.part_number, 5).status_code
        == 201
    )
    event_id = str(uuid.uuid4())
    first = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        5,
        repair=True,
        repair_reason="rework",
        device_event_id=event_id,
    )
    assert first.status_code == 201, first.text
    replay = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        5,
        repair=True,
        repair_reason="rework",
        device_event_id=event_id,
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    conflicting = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        5,
        device_event_id=event_id,
    )
    assert conflicting.status_code == 409


def test_planned_repair_off_route_still_confirms_the_deviation(
    client: TestClient, db_engine: Engine
) -> None:
    """The Repair intent never absorbs the §17 route-deviation rule: a
    PLANNED flow leaving its expectation confirms the deviation with its
    own reason, exactly like a normal transfer."""
    material = _Cell(client)
    lathe = _Cell(client)
    template = _create_route_template(db_engine, [(material.area_id, None), (lathe.area_id, None)])
    released = _release(client, material, quantity=10, route_template_id=template)
    assert (
        _transfer(client, material, lathe, released.flow_id, released.part_number, 10).status_code
        == 201
    )
    unconfirmed = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        10,
        repair=True,
        repair_reason="rework",
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["confirmation_required"] is True
    confirmed = _transfer(
        client,
        lathe,
        material,
        released.flow_id,
        released.part_number,
        10,
        repair=True,
        repair_reason="rework",
        confirm_route_deviation=True,
        route_deviation_reason="returning off-route for repair",
    )
    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()
    assert body["movement_reason"] == "REPAIR"
    assert body["route_deviation"]["reason"] == "returning off-route for repair"
    assert body["assigned_route_step_id"] is None


# ---------------------------------------------------------------------------
# Scrap
# ---------------------------------------------------------------------------


def test_full_scrap_closes_the_flow_and_stays_auditable(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    released = _release(client, material, quantity=10)
    event_id = str(uuid.uuid4())
    response = _scrap(
        client,
        material,
        released.flow_id,
        released.part_number,
        10,
        device_event_id=event_id,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["quantity"] == 10
    assert body["machine_id"] is None
    assert body["reason"] == "damaged during processing"
    assert body["source_quantity_flow_id"] is None
    row = _flow_row(db_engine, released.flow_id)
    assert row.status == "SCRAPPED"
    assert row.closed_at is not None
    # History and the last position stay; one auditable SCRAPPED row.
    history = _movements(db_engine, released.flow_id)
    assert [m.movement_type for m in history] == ["RECEIVED", "SCRAPPED"]
    assert history[-1].reason == "damaged during processing"
    # The flow left active inventory; the scrapped total is presented.
    inventory = client.get(f"/api/areas/{material.area_id}/inventory").json()
    assert inventory["total_quantity"] == 0
    resolved = _resolve(client, material, released.part_number)
    assert resolved["scrapped_quantity"] == 10
    assert resolved["resolution"] == "NO_TRANSFERABLE_QUANTITY"
    # Scrap never reduces the requested quantity (§11).
    assert _requested_quantity(db_engine, released.demand_id) == 500
    # Whole-command idempotency.
    replay = _scrap(
        client,
        material,
        released.flow_id,
        released.part_number,
        10,
        device_event_id=event_id,
    )
    assert replay.status_code == 200
    assert replay.json() == body
    conflicting = _scrap(
        client,
        material,
        released.flow_id,
        released.part_number,
        9,
        device_event_id=event_id,
    )
    assert conflicting.status_code == 409
    _assert_projection_matches_replay(db_engine)


def test_partial_scrap_on_machine_releases_only_the_scrapped_part(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    assert _assign(client, lathe, released.flow_id, released.part_number, 10).status_code == 201
    response = _scrap(client, lathe, released.flow_id, released.part_number, 4)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["machine_id"] == lathe.machine_id
    assert body["source_quantity_flow_id"] == released.flow_id
    assert body["remainder_quantity"] == 6
    scrapped = int(body["quantity_flow_id"])
    remainder = int(body["remainder_quantity_flow_id"])
    assert _flow_row(db_engine, scrapped).status == "SCRAPPED"
    remainder_row = _flow_row(db_engine, remainder)
    assert remainder_row.status == "ACTIVE"
    # The remainder stays ON its Machine; only the scrapped part left.
    assert remainder_row.current_machine_id == lathe.machine_id
    machines = {m["id"]: m for m in client.get("/api/machines").json()}
    assert machines[lathe.machine_id]["assigned_quantity"] == 6
    assert machines[lathe.machine_id]["operational_state"] == "RUNNING"
    # Conservation and the §11 reconciliation: introduced 10 = active 6
    # + scrapped 4.
    assert _active_total(db_engine, released.part_number) == 6
    assert _resolve(client, lathe, released.part_number)["scrapped_quantity"] == 4
    _assert_projection_matches_replay(db_engine)


def test_scrap_refusals_write_nothing(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client)
    other = _Cell(client)
    released = _release(client, material, quantity=5)
    before = _movement_count(db_engine, released.part_number)
    empty_reason = _scrap(client, material, released.flow_id, released.part_number, 5, reason="   ")
    assert empty_reason.status_code == 422
    too_much = _scrap(client, material, released.flow_id, released.part_number, 6)
    assert too_much.status_code == 422
    zero = _scrap(client, material, released.flow_id, released.part_number, 0)
    assert zero.status_code == 422
    wrong_station = _scrap(client, other, released.flow_id, released.part_number, 5)
    assert wrong_station.status_code == 409
    assert _movement_count(db_engine, released.part_number) == before
    assert _flow_row(db_engine, released.flow_id).status == "ACTIVE"


def test_scrap_of_a_consumed_flow_is_refused(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    # A partial assignment consumes the source into children.
    assert _assign(client, lathe, released.flow_id, released.part_number, 4).status_code == 201
    response = _scrap(client, lathe, released.flow_id, released.part_number, 2)
    assert response.status_code == 409
    assert "split into separate quantities" in response.json()["detail"]
    assert _active_total(db_engine, released.part_number) == 10


# ---------------------------------------------------------------------------
# Quantity addition
# ---------------------------------------------------------------------------


def test_addition_introduces_a_new_queued_flow(client: TestClient, db_engine: Engine) -> None:
    lathe = _Cell(client, machine_count=1)
    released = _release(client, lathe, quantity=10)
    event_id = str(uuid.uuid4())
    response = _add(client, lathe, released.part_number, 5, device_event_id=event_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["processing_state"] == "QUEUED"
    assert body["quantity"] == 5
    new_flow = int(body["quantity_flow_id"])
    assert new_flow != released.flow_id
    row = _flow_row(db_engine, new_flow)
    assert row.status == "ACTIVE"
    assert row.route_mode == "FLOATING"
    assert row.current_area_id == lathe.area_id
    history = _movements(db_engine, new_flow)
    assert [m.movement_type for m in history] == ["QUANTITY_ADJUSTED"]
    assert history[0].from_area_id is None
    assert history[0].station_id == lathe.station_id
    assert history[0].reason == "found uncounted pieces at the rack"
    assert history[0].metadata["adjustment"]["direction"] == "INCREASE"
    # Never a demand change (§8.11): requested quantity untouched, and
    # the new flow carries no Work Order context.
    assert _requested_quantity(db_engine, released.demand_id) == 500
    resolved = _resolve(client, lathe, released.part_number)
    flows = {f["quantity_flow_id"]: f for f in resolved["in_area"]}
    assert flows[new_flow]["work_order"] is None
    assert flows[new_flow]["processing_state"] == "QUEUED"
    # Whole-command idempotency.
    replay = _add(client, lathe, released.part_number, 5, device_event_id=event_id)
    assert replay.status_code == 200
    assert replay.json() == body
    conflicting = _add(client, lathe, released.part_number, 6, device_event_id=event_id)
    assert conflicting.status_code == 409
    assert _active_total(db_engine, released.part_number) == 15
    _assert_projection_matches_replay(db_engine)


def test_addition_in_an_area_without_machines_is_processing(
    client: TestClient, db_engine: Engine
) -> None:
    plating = _Cell(client)
    released = _release(client, plating, quantity=10)
    response = _add(client, plating, released.part_number, 3)
    assert response.status_code == 201, response.text
    assert response.json()["processing_state"] == "PROCESSING"
    inventory = client.get(f"/api/areas/{plating.area_id}/inventory").json()
    assert inventory["processing_quantity"] == 13
    _assert_projection_matches_replay(db_engine)


def test_addition_requires_existing_in_area_quantity(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client)
    elsewhere = _Cell(client)
    released = _release(client, material, quantity=10)
    pn = released.part_number
    # Active quantity exists — but not in THIS station's Area.
    response = _add(client, elsewhere, pn, 5)
    assert response.status_code == 409
    assert "has no active quantity in Area" in response.json()["detail"]
    fresh = _add(client, elsewhere, _unique("PN"), 5)
    assert fresh.status_code == 409
    assert _movement_count(db_engine, pn) == 1


def test_addition_operation_ambiguity_blocks_the_write(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, operation_count=2)
    released = _release(client, cell, quantity=10)
    before = _movement_count(db_engine, released.part_number)
    ambiguous = _add(client, cell, released.part_number, 5)
    assert ambiguous.status_code == 422
    assert "several Operations" in ambiguous.json()["detail"]
    assert _movement_count(db_engine, released.part_number) == before
    chosen = _add(client, cell, released.part_number, 5, operation_id=cell.operation_ids[1])
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["operation_id"] == cell.operation_ids[1]


def test_reconciliation_introduced_equals_active_plus_scrapped(
    client: TestClient, db_engine: Engine
) -> None:
    """PROJECT_PROFILE §11: introduced (release + additions) = active +
    scrapped, across a release, an addition and a scrap."""
    material = _Cell(client)
    released = _release(client, material, quantity=10)
    assert _add(client, material, released.part_number, 5).status_code == 201
    assert _scrap(client, material, released.flow_id, released.part_number, 3).status_code == 201
    with db_engine.connect() as connection:
        movements = models.PartMovement.__table__
        introduced = int(
            connection.execute(
                sa.select(sa.func.coalesce(sa.func.sum(movements.c.quantity), 0)).where(
                    movements.c.part_number == released.part_number,
                    movements.c.movement_type.in_(["RECEIVED", "QUANTITY_ADJUSTED"]),
                )
            ).scalar_one()
        )
    active = _active_total(db_engine, released.part_number)
    scrapped = _resolve(client, material, released.part_number)["scrapped_quantity"]
    assert introduced == 15
    assert introduced == active + scrapped
    _assert_projection_matches_replay(db_engine)


# ---------------------------------------------------------------------------
# Addition versus the last quantity leaving the Area (the witness lock)
# ---------------------------------------------------------------------------


class _Pause:
    """Test seam: the FIRST call pauses after completing — while the
    caller holds whatever locks it acquired so far — until released;
    later calls pass through."""

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


def test_addition_never_commits_on_a_stale_existing_quantity_precondition(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remover wins the race: the last ACTIVE quantity is scrapped
    AFTER the addition read its station context but BEFORE it judged
    the precondition — the authoritative under-lock re-check refuses
    the addition with zero writes (serial order: scrap, then a refused
    addition), and the refused id keeps no idempotency residue."""
    from app.application import transfers

    material = _Cell(client)
    released = _release(client, material, quantity=10)
    addition_event = str(uuid.uuid4())

    # The seam wraps the function quantity_events imported from
    # transfers; the patch replaces the quantity_events attribute.
    pause = _Pause(transfers.require_production_station)
    monkeypatch.setattr("app.application.quantity_events.require_production_station", pause)
    results: dict[str, Any] = {}
    addition = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "addition",
            lambda: _add(client, material, released.part_number, 5, device_event_id=addition_event),
        ),
    )
    addition.start()
    # The addition paused after its unlocked station read, holding no
    # lock: the scrap of the LAST active quantity commits meanwhile.
    assert pause.first_inside.wait(timeout=20)
    scrapped = _scrap(client, material, released.flow_id, released.part_number, 10)
    assert scrapped.status_code == 201, scrapped.text
    pause.let_first_finish.set()
    addition.join(timeout=30)
    assert not addition.is_alive()

    response = results["addition"]
    assert not isinstance(response, Exception), response
    assert response.status_code == 409, response.text
    assert "has no active quantity in Area" in response.json()["detail"]
    # Zero writes: no QUANTITY_ADJUSTED row, no new flow, nothing under
    # the addition's device_event_id.
    with db_engine.connect() as connection:
        movements = models.PartMovement.__table__
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(movements)
                .where(
                    movements.c.part_number == released.part_number,
                    movements.c.movement_type == "QUANTITY_ADJUSTED",
                )
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(movements)
                .where(movements.c.device_event_id == addition_event)
            ).scalar_one()
            == 0
        )
    assert _active_total(db_engine, released.part_number) == 0
    _assert_projection_matches_replay(db_engine)
    # No idempotency residue: once quantity exists again, the SAME id
    # with the SAME payload records a fresh addition.
    _release(client, material, quantity=4, part_number=released.part_number)
    retried = _add(client, material, released.part_number, 5, device_event_id=addition_event)
    assert retried.status_code == 201, retried.text
    assert _active_total(db_engine, released.part_number) == 9
    _assert_projection_matches_replay(db_engine)


def test_addition_witness_lock_serializes_the_remover_behind_the_commit(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The addition wins the race: it holds the witness flow row lock
    until COMMIT, so a concurrent scrap of that last quantity BLOCKS
    and applies only after the addition committed beside
    still-existing quantity — the outcome equals the serial order
    "addition, then scrap", with quantity conserved and the projection
    replay agreeing."""
    from app.application import transfers

    material = _Cell(client)
    released = _release(client, material, quantity=10)

    # Pause the addition AFTER the Operation resolution: at this point
    # it holds the witness flow lock (and the Area/Operation locks) and
    # has not yet committed.
    pause = _Pause(transfers.resolve_arrival_operation)
    monkeypatch.setattr("app.application.quantity_events.resolve_arrival_operation", pause)
    results: dict[str, Any] = {}
    addition = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "addition",
            lambda: _add(client, material, released.part_number, 5),
        ),
    )
    addition.start()
    assert pause.first_inside.wait(timeout=20)
    remover = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "scrap",
            lambda: _scrap(client, material, released.flow_id, released.part_number, 10),
        ),
    )
    remover.start()
    # The scrap needs the witness's row lock and must wait for the
    # addition's COMMIT — it cannot finish while the addition holds it.
    remover.join(timeout=1.0)
    assert remover.is_alive(), "the scrap should block behind the witness flow lock"
    pause.let_first_finish.set()
    addition.join(timeout=30)
    remover.join(timeout=30)
    assert not addition.is_alive() and not remover.is_alive()

    added = results["addition"]
    scrapped = results["scrap"]
    assert not isinstance(added, Exception), added
    assert not isinstance(scrapped, Exception), scrapped
    # Serial order "addition, then scrap": both commit, the addition
    # beside quantity that still existed, the scrap afterwards.
    assert added.status_code == 201, added.text
    assert scrapped.status_code == 201, scrapped.text
    new_flow = int(added.json()["quantity_flow_id"])
    assert _flow_row(db_engine, new_flow).status == "ACTIVE"
    assert _flow_row(db_engine, released.flow_id).status == "SCRAPPED"
    # Conservation and the §11 reconciliation: introduced 15 = active 5
    # + scrapped 10.
    assert _active_total(db_engine, released.part_number) == 5
    assert _resolve(client, material, released.part_number)["scrapped_quantity"] == 10
    _assert_projection_matches_replay(db_engine)


# ---------------------------------------------------------------------------
# Addition versus a Scan Station reconfiguration (the station lock)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reconfigure", "restore", "refusal"),
    [
        pytest.param(
            lambda other: {"area_id": other.area_id},
            lambda cell: {"area_id": cell.area_id},
            "no longer bound to Area",
            id="rebound",
        ),
        pytest.param(
            lambda other: {"is_active": False},
            lambda cell: {"is_active": True},
            "inactive and accepts no production use",
            id="deactivated",
        ),
    ],
)
def test_addition_rejects_a_station_reconfigured_before_its_authoritative_lock(
    client: TestClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    reconfigure: Callable[[Any], dict[str, Any]],
    restore: Callable[[Any], dict[str, Any]],
    refusal: str,
) -> None:
    """The configuration change wins the race: the station is rebound
    to another Area (or deactivated) AFTER the addition's unlocked
    station read but BEFORE its authoritative station lock — the
    under-lock re-check refuses the addition with zero writes, so a
    `QUANTITY_ADJUSTED` never carries the station of an Area it no
    longer belongs to, and the refused id keeps no idempotency
    residue."""
    from app.application import transfers

    cell = _Cell(client)
    other = _Cell(client)
    released = _release(client, cell, quantity=10)
    addition_event = str(uuid.uuid4())

    pause = _Pause(transfers.require_production_station)
    monkeypatch.setattr("app.application.quantity_events.require_production_station", pause)
    results: dict[str, Any] = {}
    addition = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "addition",
            lambda: _add(client, cell, released.part_number, 5, device_event_id=addition_event),
        ),
    )
    addition.start()
    # The addition paused after its unlocked station read: the
    # configuration change commits meanwhile.
    assert pause.first_inside.wait(timeout=20)
    changed = client.patch(f"/api/scan-stations/{cell.station_id}", json=reconfigure(other))
    assert changed.status_code == 200, changed.text
    pause.let_first_finish.set()
    addition.join(timeout=30)
    assert not addition.is_alive()

    response = results["addition"]
    assert not isinstance(response, Exception), response
    assert response.status_code == 409, response.text
    assert refusal in response.json()["detail"]
    # Zero writes: no QUANTITY_ADJUSTED row, nothing under the
    # addition's device_event_id, the existing quantity untouched.
    with db_engine.connect() as connection:
        movements = models.PartMovement.__table__
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(movements)
                .where(
                    movements.c.part_number == released.part_number,
                    movements.c.movement_type == "QUANTITY_ADJUSTED",
                )
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                sa.select(sa.func.count())
                .select_from(movements)
                .where(movements.c.device_event_id == addition_event)
            ).scalar_one()
            == 0
        )
    assert _active_total(db_engine, released.part_number) == 10
    _assert_projection_matches_replay(db_engine)
    # No idempotency residue: with the configuration restored, the SAME
    # id with the SAME payload records a fresh addition.
    restored = client.patch(f"/api/scan-stations/{cell.station_id}", json=restore(cell))
    assert restored.status_code == 200, restored.text
    retried = _add(client, cell, released.part_number, 5, device_event_id=addition_event)
    assert retried.status_code == 201, retried.text
    assert _active_total(db_engine, released.part_number) == 15
    _assert_projection_matches_replay(db_engine)


def test_addition_station_lock_serializes_a_concurrent_rebind(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The addition wins the race: it holds the Scan Station row lock
    until COMMIT, so a concurrent rebind BLOCKS and applies only after
    the addition committed — the `QUANTITY_ADJUSTED` was recorded into
    the Area the station was still bound to, the outcome equal to the
    serial order "addition, then rebind"."""
    from app.application import transfers

    cell = _Cell(client)
    other = _Cell(client)
    released = _release(client, cell, quantity=10)

    # Pause the addition AFTER the Operation resolution: it now holds
    # the witness flow, station, Area and Operation locks, uncommitted.
    pause = _Pause(transfers.resolve_arrival_operation)
    monkeypatch.setattr("app.application.quantity_events.resolve_arrival_operation", pause)
    results: dict[str, Any] = {}
    addition = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "addition",
            lambda: _add(client, cell, released.part_number, 5),
        ),
    )
    addition.start()
    assert pause.first_inside.wait(timeout=20)
    rebind = threading.Thread(
        target=_run_collecting,
        args=(
            results,
            "rebind",
            lambda: client.patch(
                f"/api/scan-stations/{cell.station_id}", json={"area_id": other.area_id}
            ),
        ),
    )
    rebind.start()
    # The rebinding UPDATE needs the station row lock and must wait for
    # the addition's COMMIT.
    rebind.join(timeout=1.0)
    assert rebind.is_alive(), "the rebind should block behind the station row lock"
    pause.let_first_finish.set()
    addition.join(timeout=30)
    rebind.join(timeout=30)
    assert not addition.is_alive() and not rebind.is_alive()

    added = results["addition"]
    rebound = results["rebind"]
    assert not isinstance(added, Exception), added
    assert not isinstance(rebound, Exception), rebound
    # Serial order "addition, then rebind": the addition recorded into
    # the Area the station was bound to at its COMMIT, the rebind
    # applied afterwards.
    assert added.status_code == 201, added.text
    assert rebound.status_code == 200, rebound.text
    body = added.json()
    assert body["area_id"] == cell.area_id
    assert body["station_id"] == cell.station_id
    new_flow = int(body["quantity_flow_id"])
    row = _flow_row(db_engine, new_flow)
    assert row.status == "ACTIVE"
    assert row.current_area_id == cell.area_id
    with db_engine.connect() as connection:
        movement = connection.execute(
            sa.select(models.PartMovement.__table__).where(
                models.PartMovement.__table__.c.quantity_flow_id == new_flow
            )
        ).one()
        station_row = connection.execute(
            sa.select(models.ScanStation.__table__).where(
                models.ScanStation.__table__.c.station_id == cell.station_id
            )
        ).one()
    assert movement.to_area_id == cell.area_id
    assert movement.station_id == cell.station_id
    assert station_row.area_id == other.area_id
    assert _active_total(db_engine, released.part_number) == 15
    _assert_projection_matches_replay(db_engine)
