"""Integration tests for the Phase 5 Scan Station transfer to an Area queue.

Exercises the full request path — FastAPI routes, the Application read
models and transfer command, and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain. Covered
per IMPLEMENTATION_ROADMAP Phase 5, PROJECT_PROFILE §10/§15/§17 and
GUI_DESIGN §4.7:

- station context and Area inventory read models; unknown/inactive
  stations and Areas are refused, never substituted;
- PN barcode resolution (``PF:PN:`` prefix, canonical form, unknown
  barcodes rejected) and manual entry;
- source resolution by current position, route and station context:
  in-Area quantity, FLOATING/ON_ROUTE/DEVIATION candidates, and several
  candidates returned as-is (never picked or combined);
- the happy path: exactly one immutable ``TRANSFERRED`` Movement with
  the canonical shape (source, destination, Operation, Station,
  matched snapshot step), the projection updated in the same
  transaction and rebuildable from history, and the inventory reflecting
  the move;
- Operation resolution at the destination (single, route-step, explicit
  choice, ambiguity, wrong Area, inactive);
- route deviation: refused until explicitly confirmed, then recorded on
  the Movement with no snapshot step;
- invalid station/source/Operation/route/quantity input rejected with
  ZERO writes — including partial quantity (Phase 5 moves whole flows);
- idempotency: same ``device_event_id`` + same request replays the
  original result (also after the flow moved on and when the race is
  lost at COMMIT); a different request is an explicit conflict;
- concurrency: two transfers of one flow serialize on the flow row
  lock with exactly one winner; transfer versus Area deactivation has
  one serial outcome;
- committed Movements cannot be updated or deleted; no Phase 6+
  behavior (no Machine, no ``AREA_COMPLETED``) leaks in.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations; the module database is dropped afterwards.
"""

import os
import threading
import time
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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.application import environment, projections, transfers
from app.application.errors import ConflictError
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_scan_station_transfer_api"


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


def _create_operation(client: TestClient, area_id: int, **overrides: Any) -> dict[str, Any]:
    payload = {"area_id": area_id, "code": _unique("OP"), **overrides}
    response = client.post("/api/operations", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_station(client: TestClient, area_id: int, **overrides: Any) -> str:
    payload = {"station_id": _unique("ST"), "area_id": area_id, **overrides}
    response = client.post("/api/scan-stations", json=payload)
    assert response.status_code == 201, response.text
    return str(response.json()["station_id"])


class _Cell:
    """An Area with one active Operation and one active Scan Station."""

    def __init__(self, client: TestClient, **area_overrides: Any) -> None:
        self.area = _create_area(client, **area_overrides)
        self.area_id = int(self.area["id"])
        self.operation = _create_operation(client, self.area_id)
        self.operation_id = int(self.operation["id"])
        self.station_id = _create_station(client, self.area_id)


def _release(
    client: TestClient,
    cell: _Cell,
    *,
    part_number: str | None = None,
    quantity: int = 25,
    route_mode: str = "FLOATING",
    route_template_id: int | None = None,
    confirm_active_quantity: bool = False,
) -> tuple[int, str]:
    """Release one flow into the cell's Area: (quantity_flow_id, pn)."""
    pn = part_number or _unique("PN")
    response = client.post(
        "/api/work-orders",
        json={"lines": [{"part_number": pn, "requested_quantity": 500}]},
    )
    assert response.status_code == 201, response.text
    work_order_id = int(response.json()["id"])
    demand_id = int(response.json()["demands"][0]["id"])
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity": quantity,
        "route_mode": route_mode,
        "starting_area_id": cell.area_id,
        "operation_id": cell.operation_id,
        "confirm_active_quantity": confirm_active_quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    if route_template_id is not None:
        payload["route_template_id"] = route_template_id
    released = client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release", json=payload
    )
    assert released.status_code == 201, released.text
    return int(released.json()["quantity_flow_id"]), str(released.json()["part_number"])


def _create_route_template(engine: Engine, steps: list[dict[str, Any]]) -> int:
    with Session(engine) as session:
        template = models.RouteTemplate(name=_unique("ROUTE"))
        session.add(template)
        session.flush()
        for index, step in enumerate(steps):
            session.add(
                models.RouteStep(
                    route_template_id=template.id,
                    sequence=(index + 1) * 10,
                    area_id=step["area_id"],
                    operation_id=step.get("operation_id"),
                )
            )
        session.commit()
        return int(template.id)


def _resolve(client: TestClient, station_id: str, **body: Any) -> Any:
    return client.post(f"/api/scan-stations/{station_id}/scans/resolve", json=body)


def _transfer(
    client: TestClient,
    station_id: str,
    *,
    part_number: str,
    quantity_flow_id: int,
    source_area_id: int,
    quantity: Any,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": part_number,
        "quantity_flow_id": quantity_flow_id,
        "source_area_id": source_area_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return client.post(f"/api/scan-stations/{station_id}/transfers", json=payload)


_PRODUCTION_TABLES = (
    models.QuantityFlow,
    models.PartMovement,
    models.AssignedRoute,
    models.AssignedRouteStep,
    models.AuditEvent,
    models.WorkOrderDemand,
    models.WorkOrder,
)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            model.__tablename__: connection.execute(
                sa.select(sa.func.count()).select_from(model.__table__)
            ).scalar_one()
            for model in _PRODUCTION_TABLES
        }


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


def _snapshot_step_ids(engine: Engine, flow_id: int) -> list[int]:
    with engine.connect() as connection:
        return list(
            connection.scalars(
                sa.select(models.AssignedRouteStep.id)
                .join(
                    models.QuantityFlow,
                    models.QuantityFlow.assigned_route_id
                    == models.AssignedRouteStep.assigned_route_id,
                )
                .where(models.QuantityFlow.id == flow_id)
                .order_by(models.AssignedRouteStep.sequence)
            )
        )


# ---------------------------------------------------------------------------
# Station context
# ---------------------------------------------------------------------------


def test_station_context_reports_the_bound_area_environment(
    client: TestClient, db_engine: Engine
) -> None:
    lathe = _Cell(client, color="#3366ff", description="Turning cell")
    second_operation = _create_operation(client, lathe.area_id)
    inactive_operation = _create_operation(client, lathe.area_id)
    assert (
        client.patch(
            f"/api/operations/{inactive_operation['id']}", json={"is_active": False}
        ).status_code
        == 200
    )

    response = client.get(f"/api/scan-stations/{lathe.station_id}/context")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["station_id"] == lathe.station_id
    assert body["area"] == {
        "id": lathe.area_id,
        "name": lathe.area["name"],
        "color": "#3366ff",
        "description": "Turning cell",
        "is_terminal": False,
    }
    assert body["department"]["id"] == lathe.area["department_id"]
    # Active Operations only, in stable code order.
    assert {operation["id"] for operation in body["operations"]} == {
        lathe.operation_id,
        int(second_operation["id"]),
    }
    assert body["has_machines"] is False

    # An Area with an active Machine queues quantity (QUEUE_AND_ASSIGN).
    with Session(db_engine) as session:
        session.add(
            models.Machine(area_id=lathe.area_id, name=_unique("Lathe"), asset_tag=_unique("CD"))
        )
        session.commit()
    assert client.get(f"/api/scan-stations/{lathe.station_id}/context").json()["has_machines"]


def test_unknown_and_inactive_stations_are_refused_never_substituted(
    client: TestClient,
) -> None:
    cell = _Cell(client)
    assert client.get("/api/scan-stations/NOPE-01/context").status_code == 404

    inactive_station = _create_station(client, cell.area_id, is_active=False)
    response = client.get(f"/api/scan-stations/{inactive_station}/context")
    assert response.status_code == 409
    assert "inactive" in response.json()["detail"]
    assert _resolve(client, inactive_station, part_number="X").status_code == 409

    # A station bound to an inactive Area accepts no production use either.
    idle = _Cell(client)
    assert client.patch(f"/api/areas/{idle.area_id}", json={"is_active": False}).status_code == 200
    response = client.get(f"/api/scan-stations/{idle.station_id}/context")
    assert response.status_code == 409
    assert "inactive" in response.json()["detail"]


# ---------------------------------------------------------------------------
# PN scan resolution
# ---------------------------------------------------------------------------


def test_barcode_and_manual_entry_resolve_the_canonical_pn(client: TestClient) -> None:
    lathe = _Cell(client)
    material = _Cell(client)
    flow_id, pn = _release(client, material, part_number=_unique("pn").lower())
    canonical = pn  # the release already canonicalized it

    # Scanned barcode: exact PF:PN: prefix, terminators trimmed, suffix canonicalized.
    scanned = _resolve(client, lathe.station_id, barcode=f"  PF:PN:{canonical.lower()}\r\n")
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["part_number"] == canonical
    assert scanned.json()["resolution"] == "TRANSFER_SOURCE_AVAILABLE"
    assert [c["quantity_flow_id"] for c in scanned.json()["candidates"]] == [flow_id]

    # Manual entry: lowercase accepted and canonicalized.
    manual = _resolve(client, lathe.station_id, part_number=f" {canonical.lower()} ")
    assert manual.status_code == 200, manual.text
    assert manual.json()["part_number"] == canonical

    # A resolution is a read: nothing moved.
    assert (
        _resolve(client, lathe.station_id, part_number=canonical).json()["candidates"][0][
            "current_area"
        ]["id"]
        == material.area_id
    )


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"barcode": "PF:MACHINE:CD-0001"}, "Unknown barcode"),
        ({"barcode": "PF:AREA:1"}, "Unknown barcode"),
        ({"barcode": "PF:SCRAP"}, "Unknown barcode"),
        ({"barcode": "2027-60-8114-00"}, "Unknown barcode"),  # raw PN text is not a barcode
        ({"barcode": "PF:PN:"}, "must not be empty"),
        ({"barcode": "PF:PN:AB 12"}, "internal whitespace"),
        ({"part_number": "AB 12"}, "internal whitespace"),
        ({"part_number": "   "}, "must not be empty"),
        ({}, "exactly one"),
        ({"barcode": "PF:PN:A", "part_number": "A"}, "exactly one"),
    ],
)
def test_invalid_scans_are_rejected(
    client: TestClient, body: dict[str, Any], fragment: str
) -> None:
    cell = _Cell(client)
    response = _resolve(client, cell.station_id, **body)
    assert response.status_code == 422, response.text
    assert fragment in response.json()["detail"]


def test_resolution_distinguishes_in_area_from_transfer_sources(
    client: TestClient,
) -> None:
    lathe = _Cell(client)
    material = _Cell(client)
    pn = _unique("PN")
    in_lathe, _ = _release(client, lathe, part_number=pn, quantity=5)
    elsewhere, _ = _release(
        client, material, part_number=pn, quantity=7, confirm_active_quantity=True
    )

    body = _resolve(client, lathe.station_id, part_number=pn).json()
    assert body["resolution"] == "ALREADY_IN_AREA"
    assert [item["quantity_flow_id"] for item in body["in_area"]] == [in_lathe]
    assert body["in_area"][0]["work_order"]["request_type"] == "NEW"
    assert body["in_area"][0]["work_order"]["work_order_number"] is None
    # "Receive more quantity from another Area" stays offered explicitly.
    assert [item["quantity_flow_id"] for item in body["candidates"]] == [elsewhere]
    assert body["has_active_demand"] is True
    assert body["transfer_blocked_reason"] is None

    # A PN never released anywhere: nothing to transfer, no demand.
    fresh = _resolve(client, lathe.station_id, part_number=_unique("PN")).json()
    assert fresh["resolution"] == "NO_TRANSFERABLE_QUANTITY"
    assert fresh["in_area"] == [] and fresh["candidates"] == []
    assert fresh["has_active_demand"] is False


def test_several_valid_sources_are_all_returned_never_picked_or_combined(
    client: TestClient,
) -> None:
    lathe = _Cell(client)
    material = _Cell(client)
    cut = _Cell(client)
    pn = _unique("PN")
    first, _ = _release(client, material, part_number=pn, quantity=10)
    second, _ = _release(
        client, material, part_number=pn, quantity=20, confirm_active_quantity=True
    )
    third, _ = _release(client, cut, part_number=pn, quantity=30, confirm_active_quantity=True)

    body = _resolve(client, lathe.station_id, part_number=pn).json()
    assert body["resolution"] == "TRANSFER_SOURCE_AVAILABLE"
    candidates = body["candidates"]
    assert [c["quantity_flow_id"] for c in candidates] == [first, second, third]
    assert [c["quantity"] for c in candidates] == [10, 20, 30]  # never summed
    assert [c["current_area"]["id"] for c in candidates] == [
        material.area_id,
        material.area_id,
        cut.area_id,
    ]
    assert all(c["route_status"] == "FLOATING" for c in candidates)
    # The single active Operation of the destination resolves without a choice.
    assert all(c["suggested_operation_id"] == lathe.operation_id for c in candidates)


def test_candidates_respect_route_and_position_not_every_active_flow(
    client: TestClient, db_engine: Engine
) -> None:
    """A PLANNED flow's candidate status follows its next expected
    snapshot step; a flow whose whole quantity is already in the Area is
    no candidate; other PNs are never involved."""
    material = _Cell(client)
    cut = _Cell(client)
    lathe = _Cell(client)
    template_id = _create_route_template(
        db_engine,
        [
            {"area_id": material.area_id, "operation_id": material.operation_id},
            {"area_id": cut.area_id},
            {"area_id": lathe.area_id, "operation_id": lathe.operation_id},
        ],
    )
    pn = _unique("PN")
    planned, _ = _release(
        client, material, part_number=pn, route_mode="PLANNED", route_template_id=template_id
    )
    _release(client, material, part_number=_unique("PN"))  # another PN — irrelevant

    # Next expected step is Cut: at Cut the flow is ON_ROUTE, at Lathe a DEVIATION.
    at_cut = _resolve(client, cut.station_id, part_number=pn).json()["candidates"]
    assert [(c["quantity_flow_id"], c["route_status"]) for c in at_cut] == [(planned, "ON_ROUTE")]
    assert at_cut[0]["expected_next_area"]["id"] == cut.area_id

    at_lathe = _resolve(client, lathe.station_id, part_number=pn).json()["candidates"]
    assert [(c["quantity_flow_id"], c["route_status"]) for c in at_lathe] == [
        (planned, "DEVIATION")
    ]
    assert at_lathe[0]["expected_next_area"]["id"] == cut.area_id
    # The route-step Operation is NOT suggested for a deviation (no
    # matched step); the destination's single Operation still is.
    assert at_lathe[0]["suggested_operation_id"] == lathe.operation_id

    # At the station of its own Area the flow is in-Area, not a candidate.
    at_material = _resolve(client, material.station_id, part_number=pn).json()
    assert at_material["resolution"] == "ALREADY_IN_AREA"
    assert at_material["candidates"] == []


# ---------------------------------------------------------------------------
# Happy path — exact TRANSFERRED shape, projection, inventory
# ---------------------------------------------------------------------------


def test_floating_transfer_exact_shape_projection_and_inventory(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=40)
    before = _counts(db_engine)

    event_id = str(uuid.uuid4())
    response = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=40,
        device_event_id=event_id,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["quantity_flow_id"] == flow_id
    assert body["part_number"] == pn
    assert body["quantity"] == 40
    assert body["from_area_id"] == material.area_id
    assert body["to_area_id"] == lathe.area_id
    assert body["operation_id"] == lathe.operation_id  # resolved, not sent
    assert body["station_id"] == lathe.station_id
    assert body["assigned_route_step_id"] is None
    assert body["route_deviation"] is None
    assert body["device_event_id"] == event_id

    movement = _movement_row(db_engine, body["movement_id"])
    assert movement.movement_type == "TRANSFERRED"
    assert movement.quantity == 40
    assert movement.from_area_id == material.area_id
    assert movement.to_area_id == lathe.area_id
    assert movement.operation_id == lathe.operation_id
    assert movement.station_id == lathe.station_id
    assert movement.assigned_route_step_id is None
    assert movement.occurred_at == movement.server_received_at
    assert movement.metadata["request_fingerprint"]
    assert "route_deviation" not in movement.metadata

    # Projection updated in the same transaction; quantity unchanged.
    flow = _flow_row(db_engine, flow_id)
    assert flow.current_area_id == lathe.area_id
    assert flow.quantity == 40 and flow.status == "ACTIVE"

    after = _counts(db_engine)
    assert after["part_movements"] == before["part_movements"] + 1
    assert after["quantity_flows"] == before["quantity_flows"]  # moved, not created
    assert after["assigned_routes"] == before["assigned_routes"]
    assert after["audit_events"] == before["audit_events"]  # Movement IS the audit record

    # Inventory: gone from Material, present at Lathe.
    source_inventory = client.get(f"/api/areas/{material.area_id}/inventory").json()
    assert source_inventory["lines"] == [] and source_inventory["total_quantity"] == 0
    target_inventory = client.get(f"/api/areas/{lathe.area_id}/inventory").json()
    assert target_inventory["total_part_numbers"] == 1
    assert target_inventory["total_quantity"] == 40
    assert target_inventory["lines"][0]["part_number"] == pn
    assert [f["quantity_flow_id"] for f in target_inventory["lines"][0]["flows"]] == [flow_id]
    assert target_inventory["lines"][0]["flows"][0]["work_order"]["work_order_demand_id"] > 0

    # Resolution after the move: in-Area at Lathe, a candidate from Lathe elsewhere.
    assert _resolve(client, lathe.station_id, part_number=pn).json()["resolution"] == (
        "ALREADY_IN_AREA"
    )
    back = _resolve(client, material.station_id, part_number=pn).json()["candidates"]
    assert [c["current_area"]["id"] for c in back] == [lathe.area_id]

    # Projection rebuilds from Movement history alone (SLICE1 §15).
    with Session(db_engine) as session:
        assert projections.rebuild_current_area_ids(session)[flow_id] == lathe.area_id


def test_planned_transfer_on_route_records_the_snapshot_step_and_its_operation(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    other_lathe_operation = _create_operation(client, lathe.area_id)  # Lathe now has two
    template_id = _create_route_template(
        db_engine,
        [
            {"area_id": material.area_id, "operation_id": material.operation_id},
            {"area_id": lathe.area_id, "operation_id": lathe.operation_id},
        ],
    )
    flow_id, pn = _release(
        client, material, route_mode="PLANNED", route_template_id=template_id, quantity=12
    )
    first_step, second_step = _snapshot_step_ids(db_engine, flow_id)

    # The step-defined Operation resolves even though Lathe has two.
    resolved = _resolve(client, lathe.station_id, part_number=pn).json()["candidates"][0]
    assert resolved["route_status"] == "ON_ROUTE"
    assert resolved["suggested_operation_id"] == lathe.operation_id

    response = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=12,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["assigned_route_step_id"] == second_step != first_step
    assert body["operation_id"] == lathe.operation_id
    assert body["route_deviation"] is None

    # A different Operation than the step's is a validation failure.
    other_flow, other_pn = _release(
        client, material, route_mode="PLANNED", route_template_id=template_id, quantity=3
    )
    mismatch = _transfer(
        client,
        lathe.station_id,
        part_number=other_pn,
        quantity_flow_id=other_flow,
        source_area_id=material.area_id,
        quantity=3,
        operation_id=int(other_lathe_operation["id"]),
    )
    assert mismatch.status_code == 422, mismatch.text
    assert "route step" in mismatch.json()["detail"]
    assert _flow_row(db_engine, other_flow).current_area_id == material.area_id


def test_route_visiting_the_same_area_twice_follows_the_sequence(
    client: TestClient, db_engine: Engine
) -> None:
    """Material → Mill → External → Mill: the second Mill visit is
    ON_ROUTE only after External, and each transfer records ITS step."""
    material = _Cell(client)
    mill = _Cell(client)
    external = _Cell(client)
    template_id = _create_route_template(
        db_engine,
        [
            {"area_id": material.area_id},
            {"area_id": mill.area_id},
            {"area_id": external.area_id},
            {"area_id": mill.area_id},
        ],
    )
    flow_id, pn = _release(
        client, material, route_mode="PLANNED", route_template_id=template_id, quantity=9
    )
    steps = _snapshot_step_ids(db_engine, flow_id)

    def move(cell: _Cell, source: _Cell) -> Any:
        response = _transfer(
            client,
            cell.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            source_area_id=source.area_id,
            quantity=9,
        )
        assert response.status_code == 201, response.text
        return response.json()

    assert move(mill, material)["assigned_route_step_id"] == steps[1]
    # From Mill, going straight back to Material is a deviation, External is next.
    assert (
        _resolve(client, material.station_id, part_number=pn).json()["candidates"][0][
            "route_status"
        ]
        == "DEVIATION"
    )
    assert move(external, mill)["assigned_route_step_id"] == steps[2]
    assert move(mill, external)["assigned_route_step_id"] == steps[3]
    # The route is complete: any further transfer is a deviation with no next step.
    end = _resolve(client, material.station_id, part_number=pn).json()["candidates"][0]
    assert end["route_status"] == "DEVIATION" and end["expected_next_area"] is None


# ---------------------------------------------------------------------------
# Route deviation — explicit confirmation, recorded on the Movement
# ---------------------------------------------------------------------------


def test_route_deviation_is_refused_until_confirmed_then_recorded(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    cut = _Cell(client)
    lathe = _Cell(client)
    template_id = _create_route_template(
        db_engine,
        [{"area_id": material.area_id}, {"area_id": cut.area_id}, {"area_id": lathe.area_id}],
    )
    flow_id, pn = _release(
        client, material, route_mode="PLANNED", route_template_id=template_id, quantity=6
    )
    steps = _snapshot_step_ids(db_engine, flow_id)
    before = _counts(db_engine)
    event_id = str(uuid.uuid4())

    # Skipping Cut: warned, nothing recorded.
    refused = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=6,
        device_event_id=event_id,
    )
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["confirmation_required"] is True
    assert body["route_deviation"] == {
        "expected_next_area_id": cut.area_id,
        "expected_next_step_id": steps[1],
        "expected_next_sequence": 20,
        "last_known_step_id": steps[0],
        "actual_area_id": lathe.area_id,
    }
    assert cut.area["name"] in body["detail"]
    assert _counts(db_engine) == before
    assert _flow_row(db_engine, flow_id).current_area_id == material.area_id

    # Confirmed: recorded as the actual TRANSFERRED with the deviation
    # in its metadata, no snapshot step, previous route untouched.
    confirmed = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=6,
        device_event_id=event_id,  # same intent, same id — still fresh
        confirm_route_deviation=True,
    )
    assert confirmed.status_code == 201, confirmed.text
    result = confirmed.json()
    assert result["assigned_route_step_id"] is None
    assert result["route_deviation"]["confirmed"] is True
    assert result["route_deviation"]["expected_next_area_id"] == cut.area_id
    movement = _movement_row(db_engine, result["movement_id"])
    assert movement.movement_type == "TRANSFERRED"
    assert movement.metadata["route_deviation"]["actual_area_id"] == lathe.area_id
    assert _snapshot_step_ids(db_engine, flow_id) == steps
    assert _flow_row(db_engine, flow_id).current_area_id == lathe.area_id

    # After the deviation the last KNOWN position is still Material's
    # step, so Cut (its next step) is ON_ROUTE again from Lathe.
    from_lathe = _resolve(client, cut.station_id, part_number=pn).json()["candidates"][0]
    assert from_lathe["route_status"] == "ON_ROUTE"
    assert from_lathe["current_area"]["id"] == lathe.area_id

    # A FLOATING flow never needs the flag; sending it is harmless.
    floating, floating_pn = _release(client, material, quantity=2)
    ok = _transfer(
        client,
        lathe.station_id,
        part_number=floating_pn,
        quantity_flow_id=floating,
        source_area_id=material.area_id,
        quantity=2,
        confirm_route_deviation=True,
    )
    assert ok.status_code == 201 and ok.json()["route_deviation"] is None


# ---------------------------------------------------------------------------
# Operation resolution at the destination
# ---------------------------------------------------------------------------


def test_operation_resolution_at_the_destination(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    second = _create_operation(client, lathe.area_id)
    foreign = _create_operation(client, material.area_id)
    inactive = _create_operation(client, lathe.area_id)
    assert (
        client.patch(f"/api/operations/{inactive['id']}", json={"is_active": False}).status_code
        == 200
    )

    flow_id, pn = _release(client, material, quantity=8)
    resolved = _resolve(client, lathe.station_id, part_number=pn).json()
    assert {o["id"] for o in resolved["operations"]} == {lathe.operation_id, int(second["id"])}
    assert resolved["candidates"][0]["suggested_operation_id"] is None  # ambiguous

    def attempt(**overrides: Any) -> Any:
        return _transfer(
            client,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            source_area_id=material.area_id,
            quantity=8,
            **overrides,
        )

    ambiguous = attempt()
    assert ambiguous.status_code == 422 and "Choose the Operation" in ambiguous.json()["detail"]
    wrong_area = attempt(operation_id=int(foreign["id"]))
    assert wrong_area.status_code == 422 and "does not belong" in wrong_area.json()["detail"]
    off = attempt(operation_id=int(inactive["id"]))
    assert off.status_code == 409 and "inactive" in off.json()["detail"]
    missing = attempt(operation_id=999_999_999)
    assert missing.status_code == 422
    assert _flow_row(db_engine, flow_id).current_area_id == material.area_id

    chosen = attempt(operation_id=int(second["id"]))
    assert chosen.status_code == 201, chosen.text
    assert chosen.json()["operation_id"] == int(second["id"])

    # An Area without any active Operation cannot receive quantity.
    bare = _Cell(client)
    assert (
        client.patch(f"/api/operations/{bare.operation_id}", json={"is_active": False}).status_code
        == 200
    )
    other_flow, other_pn = _release(client, material, quantity=1)
    unconfigured = _transfer(
        client,
        bare.station_id,
        part_number=other_pn,
        quantity_flow_id=other_flow,
        source_area_id=material.area_id,
        quantity=1,
    )
    assert unconfigured.status_code == 409
    assert "no active Operation" in unconfigured.json()["detail"]


# ---------------------------------------------------------------------------
# Rejections — zero writes
# ---------------------------------------------------------------------------


def test_partial_quantity_is_refused_clearly_with_no_write(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=30)
    before = _counts(db_engine)

    for quantity, fragment in ((29, "Partial transfer is not supported yet"), (31, "exceeds")):
        response = _transfer(
            client,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            source_area_id=material.area_id,
            quantity=quantity,
        )
        assert response.status_code == 422, response.text
        assert fragment in response.json()["detail"]
        assert "30 pcs" in response.json()["detail"]
    for invalid in (0, -5, True, 1.5, "30"):
        response = _transfer(
            client,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            source_area_id=material.area_id,
            quantity=invalid,
        )
        assert response.status_code == 422, (invalid, response.text)
    assert _counts(db_engine) == before
    assert _flow_row(db_engine, flow_id).current_area_id == material.area_id


def test_invalid_station_source_and_target_create_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    cut = _Cell(client)
    flow_id, pn = _release(client, material, quantity=10)
    other_flow, other_pn = _release(client, cut, quantity=4)
    before = _counts(db_engine)

    def attempt(station_id: str, **overrides: Any) -> Any:
        payload: dict[str, Any] = {
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": material.area_id,
            "quantity": 10,
        }
        payload.update(overrides)
        return _transfer(client, station_id, **payload)

    assert attempt("NOPE-99").status_code == 404
    inactive_station = _create_station(client, lathe.area_id, is_active=False)
    assert attempt(inactive_station).status_code == 409
    # Unknown flow / PN mismatch / wrong claimed source.
    assert attempt(lathe.station_id, quantity_flow_id=999_999_999).status_code == 422
    mismatch = attempt(lathe.station_id, quantity_flow_id=other_flow, quantity=4)
    assert mismatch.status_code == 422 and "does not match" in mismatch.json()["detail"]
    stale = attempt(lathe.station_id, source_area_id=cut.area_id)
    assert stale.status_code == 409 and "no longer in the selected source" in stale.json()["detail"]
    # Already in the target Area.
    same = attempt(material.station_id)
    assert same.status_code == 409 and "already in Area" in same.json()["detail"]
    # Terminal Area (Stockroom) is never a transfer target.
    stockroom = _Cell(client, is_terminal=True)
    terminal = attempt(stockroom.station_id)
    assert terminal.status_code == 409 and "terminal" in terminal.json()["detail"]
    resolved = _resolve(client, stockroom.station_id, part_number=pn).json()
    assert "terminal" in resolved["transfer_blocked_reason"]
    # Bad idempotency key and unknown fields.
    assert attempt(lathe.station_id, device_event_id="not-a-uuid").status_code == 422
    assert attempt(lathe.station_id, worker_id=1).status_code == 422
    assert attempt(lathe.station_id, confirm_route_deviation="yes").status_code == 422
    # Inactive target Area — the station stays configured but refuses.
    idle = _Cell(client)
    assert client.patch(f"/api/areas/{idle.area_id}", json={"is_active": False}).status_code == 200
    assert attempt(idle.station_id).status_code == 409

    assert _counts(db_engine) == before
    assert _flow_row(db_engine, flow_id).current_area_id == material.area_id
    assert _flow_row(db_engine, other_flow).current_area_id == cut.area_id
    assert other_pn != pn


def test_committed_transfers_cannot_be_updated_or_deleted(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=3)
    response = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=3,
    )
    assert response.status_code == 201
    movement_id = response.json()["movement_id"]
    for statement in (
        sa.update(models.PartMovement)
        .where(models.PartMovement.id == movement_id)
        .values(quantity=99),
        sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id),
    ):
        with db_engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(statement)
            connection.commit()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_same_device_event_id_replays_the_original_after_the_flow_moved_on(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, material, quantity=15)
    event_id = str(uuid.uuid4())
    original = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=15,
        device_event_id=event_id,
    )
    assert original.status_code == 201, original.text
    before = _counts(db_engine)

    # The flow moves on; the retry of the FIRST transfer still replays
    # the ORIGINAL committed result and records nothing.
    onward = _transfer(
        client,
        deburr.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=lathe.area_id,
        quantity=15,
    )
    assert onward.status_code == 201, onward.text
    replay = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=15,
        device_event_id=event_id,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == original.json()
    after = _counts(db_engine)
    assert after["part_movements"] == before["part_movements"] + 1  # onward only
    assert _flow_row(db_engine, flow_id).current_area_id == deburr.area_id

    # Same id, different request: explicit conflict, nothing recorded.
    changes: list[dict[str, Any]] = [
        {"quantity": 14},
        {"source_area_id": deburr.area_id},
        {"operation_id": lathe.operation_id + 1_000_000},
        {"station_id": deburr.station_id},
    ]
    for change in changes:
        station = str(change.pop("station_id", lathe.station_id))
        request: dict[str, Any] = {
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": material.area_id,
            "quantity": 15,
            "device_event_id": event_id,
            **change,
        }
        conflict = _transfer(client, station, **request)
        assert conflict.status_code == 409, (change, conflict.text)
        assert "different production request" in conflict.json()["detail"]
    # A release id reused for a transfer is a conflict too (other event kind).
    with db_engine.connect() as connection:
        release_event_id = connection.execute(
            sa.select(models.PartMovement.device_event_id).where(
                models.PartMovement.quantity_flow_id == flow_id,
                models.PartMovement.movement_type == "RECEIVED",
            )
        ).scalar_one()
    reused = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=15,
        device_event_id=release_event_id,
    )
    assert reused.status_code == 409
    assert _counts(db_engine) == after


def test_duplicate_device_event_id_race_lost_at_commit_replays(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both pre-checks miss, the UNIQUE constraint decides at COMMIT: the
    loser resolves exactly like a pre-checked duplicate."""
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=5)
    event_id = str(uuid.uuid4())
    real_lookup = transfers._committed_transfer
    misses = {"remaining": 2}

    def blind_then_real(session: Session, device_event_id: str) -> Any:
        if misses["remaining"] > 0:
            misses["remaining"] -= 1
            return None
        return real_lookup(session, device_event_id)

    original = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=5,
        device_event_id=event_id,
    )
    assert original.status_code == 201, original.text
    # Reset the projection so the "loser" passes validation and reaches COMMIT.
    with Session(db_engine) as session:
        session.execute(
            sa.update(models.QuantityFlow)
            .where(models.QuantityFlow.id == flow_id)
            .values(current_area_id=material.area_id)
        )
        session.commit()
    monkeypatch.setattr(transfers, "_committed_transfer", blind_then_real)
    try:
        loser = _transfer(
            client,
            lathe.station_id,
            part_number=pn,
            quantity_flow_id=flow_id,
            source_area_id=material.area_id,
            quantity=5,
            device_event_id=event_id,
        )
    finally:
        monkeypatch.undo()
    assert loser.status_code == 200, loser.text
    assert loser.json() == original.json()
    assert misses["remaining"] == 0  # both pre-checks were really blinded
    # The rolled-back attempt left no projection change behind either.
    assert _flow_row(db_engine, flow_id).current_area_id == material.area_id
    # Restore the true projection for the module's other assertions.
    with Session(db_engine) as session:
        session.execute(
            sa.update(models.QuantityFlow)
            .where(models.QuantityFlow.id == flow_id)
            .values(current_area_id=lathe.area_id)
        )
        session.commit()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class _PauseFirstAssessment:
    """Test seam: pause the FIRST transfer inside its route assessment —
    after it holds the flow row lock — so a competitor can be observed
    blocking, then released deterministically."""

    def __init__(self) -> None:
        self.real = transfers.assess_route
        self.first_inside = threading.Event()
        self.let_first_finish = threading.Event()
        self._guard = threading.Lock()
        self._paused_once = False

    def __call__(
        self, session: Session, flow: models.QuantityFlow, target_area_id: int
    ) -> transfers.RouteAssessment:
        result = self.real(session, flow, target_area_id)
        with self._guard:
            should_pause = not self._paused_once
            self._paused_once = True
        if should_pause:
            self.first_inside.set()
            assert self.let_first_finish.wait(timeout=20), "test deadlock: never released"
        return result


def test_concurrent_transfers_of_one_flow_serialize_with_one_winner(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    deburr = _Cell(client)
    flow_id, pn = _release(client, material, quantity=11)
    pause = _PauseFirstAssessment()
    monkeypatch.setattr(transfers, "assess_route", pause)
    results: dict[str, Any] = {}

    def run(name: str, station_id: str) -> None:
        with Session(db_engine) as session:
            try:
                results[name] = transfers.transfer_to_station_area(
                    session,
                    station_id=station_id,
                    part_number=pn,
                    quantity_flow_id=flow_id,
                    source_area_id=material.area_id,
                    quantity=11,
                    operation_id=None,
                    confirm_route_deviation=False,
                    device_event_id=str(uuid.uuid4()),
                )
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results[name] = exc

    first = threading.Thread(target=run, args=("first", lathe.station_id), daemon=True)
    second = threading.Thread(target=run, args=("second", deburr.station_id), daemon=True)
    try:
        first.start()
        assert pause.first_inside.wait(timeout=20)  # holds the flow lock, paused
        second.start()
        time.sleep(1.0)
        assert "second" not in results  # really blocked on the row lock
    finally:
        pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)
    assert not first.is_alive() and not second.is_alive()

    winner, loser = results["first"], results["second"]
    assert isinstance(winner, transfers.AreaTransfer) and winner.created
    assert winner.to_area_id == lathe.area_id
    assert isinstance(loser, ConflictError)
    assert "no longer in the selected source" in loser.message
    with db_engine.connect() as connection:
        transferred = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartMovement.__table__)
            .where(
                models.PartMovement.__table__.c.quantity_flow_id == flow_id,
                models.PartMovement.__table__.c.movement_type == "TRANSFERRED",
            )
        ).scalar_one()
    assert transferred == 1
    assert _flow_row(db_engine, flow_id).current_area_id == lathe.area_id


def test_concurrent_identical_retries_one_creates_one_replays(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=7)
    event_id = str(uuid.uuid4())
    pause = _PauseFirstAssessment()
    monkeypatch.setattr(transfers, "assess_route", pause)
    results: dict[str, Any] = {}

    def run(name: str) -> None:
        with Session(db_engine) as session:
            try:
                results[name] = transfers.transfer_to_station_area(
                    session,
                    station_id=lathe.station_id,
                    part_number=pn,
                    quantity_flow_id=flow_id,
                    source_area_id=material.area_id,
                    quantity=7,
                    operation_id=None,
                    confirm_route_deviation=False,
                    device_event_id=event_id,
                )
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results[name] = exc

    first = threading.Thread(target=run, args=("first",), daemon=True)
    second = threading.Thread(target=run, args=("second",), daemon=True)
    try:
        first.start()
        assert pause.first_inside.wait(timeout=20)
        second.start()  # pre-lock check sees nothing yet, blocks on the flow lock
        time.sleep(1.0)
        assert "second" not in results
    finally:
        pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)

    outcomes = [results["first"], results["second"]]
    for outcome in outcomes:
        assert isinstance(outcome, transfers.AreaTransfer), outcome
    created = [o for o in outcomes if o.created]
    replayed = [o for o in outcomes if not o.created]
    assert len(created) == 1 and len(replayed) == 1
    assert replayed[0] == created[0]._replace(created=False)
    with db_engine.connect() as connection:
        transferred = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartMovement.__table__)
            .where(
                models.PartMovement.__table__.c.quantity_flow_id == flow_id,
                models.PartMovement.__table__.c.movement_type == "TRANSFERRED",
            )
        ).scalar_one()
    assert transferred == 1


def test_transfer_versus_area_deactivation_has_one_serial_outcome(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transfer holds the target Area row lock from its active check
    to COMMIT; deactivation blocks on it and then sees the quantity."""
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=2)
    pause = _PauseFirstAssessment()
    monkeypatch.setattr(transfers, "assess_route", pause)
    results: dict[str, Any] = {}

    def run_transfer() -> None:
        with Session(db_engine) as session:
            try:
                results["transfer"] = transfers.transfer_to_station_area(
                    session,
                    station_id=lathe.station_id,
                    part_number=pn,
                    quantity_flow_id=flow_id,
                    source_area_id=material.area_id,
                    quantity=2,
                    operation_id=None,
                    confirm_route_deviation=False,
                    device_event_id=str(uuid.uuid4()),
                )
            except Exception as exc:  # noqa: BLE001
                results["transfer"] = exc

    def run_deactivation() -> None:
        with Session(db_engine) as session:
            try:
                results["deactivation"] = environment.update_area(
                    session, lathe.area_id, is_active=False
                )
            except Exception as exc:  # noqa: BLE001
                results["deactivation"] = exc

    transfer_thread = threading.Thread(target=run_transfer, daemon=True)
    deactivation_thread = threading.Thread(target=run_deactivation, daemon=True)
    try:
        transfer_thread.start()
        assert pause.first_inside.wait(timeout=20)  # holds the Area row lock
        deactivation_thread.start()
        time.sleep(1.0)
        assert "deactivation" not in results  # blocked on the Area lock
    finally:
        pause.let_first_finish.set()
    transfer_thread.join(timeout=20)
    deactivation_thread.join(timeout=20)

    assert isinstance(results["transfer"], transfers.AreaTransfer)
    assert isinstance(results["deactivation"], ConflictError)
    assert "still holds active quantity" in results["deactivation"].message
    with db_engine.connect() as connection:
        still_active = connection.execute(
            sa.select(models.Area.__table__.c.is_active).where(
                models.Area.__table__.c.id == lathe.area_id
            )
        ).scalar_one()
    assert still_active is True


# ---------------------------------------------------------------------------
# Phase boundary
# ---------------------------------------------------------------------------


def test_no_phase6_plus_behavior_leaks_in(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    flow_id, pn = _release(client, material, quantity=1)
    response = _transfer(
        client,
        lathe.station_id,
        part_number=pn,
        quantity_flow_id=flow_id,
        source_area_id=material.area_id,
        quantity=1,
    )
    assert response.status_code == 201
    with db_engine.connect() as connection:
        types = set(
            connection.scalars(
                sa.select(models.PartMovement.__table__.c.movement_type).where(
                    models.PartMovement.__table__.c.quantity_flow_id == flow_id
                )
            )
        )
    # RECEIVED then TRANSFERRED alone — no AREA_COMPLETED, no Machine events.
    assert types == {"RECEIVED", "TRANSFERRED"}
    columns = {column.name for column in models.PartMovement.__table__.columns}
    assert columns.isdisjoint({"worker_id", "scan_session_id", "movement_reason", "machine_id"})
    assert "current_machine_id" not in {c.name for c in models.QuantityFlow.__table__.columns}
