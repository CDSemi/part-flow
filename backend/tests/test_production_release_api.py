"""Integration tests for the Phase 4 explicit production release.

Exercises the full request path — FastAPI routes, the Application
release command, and PostgreSQL — against a dedicated temporary
database migrated to head by the real Alembic chain. Covered per the
Slice 1 acceptance criteria (SLICE1_DATA_MODEL §19; PROJECT_PROFILE
§13; GUI_DESIGN §11.2/§11.4):

- invalid input (PN, quantity, Area, Operation, RouteTemplate, route
  mode shape, device_event_id) is rejected with zero writes;
- a FLOATING release creates exactly one flow + one RECEIVED with the
  exact canonical shape and no AssignedRoute; a PLANNED release adds
  exactly one independent snapshot whose first step the Movement
  references — never a mutable ``route_steps`` row;
- the release transaction is atomic at every failure point and writes
  no generic ``audit_events`` row;
- releasing a PN with active quantity requires the explicit
  confirmation flag (the 409 carries the existing distribution) and a
  confirmed release creates a separate flow — never a merge;
- the same ``device_event_id`` + same normalized request replays the
  original committed result (also when the race is lost at COMMIT);
  the same id with a different request is an explicit 409 conflict;
- RouteTemplate edits never change a committed snapshot; committed
  Movements cannot be updated or deleted;
- ``current_area_id`` rebuilds from Movement history alone, and
  conservation holds per PN;
- a saved demand deletes only while no quantity has ever been
  released for it — afterwards the backend refuses, and removal never
  cascades to production data, the PN master, or audit history.

The API commits real transactions, so tests isolate through unique
PNs/Work Orders; the module database is dropped afterwards.
"""

import datetime
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
from sqlalchemy.orm import Session

from alembic import command
from app.application import environment, production_release, projections
from app.application.common import flush as translated_flush
from app.application.errors import (
    ActiveQuantityConfirmationRequiredError,
    ConflictError,
)
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_production_release_api"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
    """Temporary database migrated to head for the API under test."""
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
    """Application client wired to the temporary database."""
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
    """Direct database access for state verification."""
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


def _create_demand(client: TestClient, part_number: str | None = None) -> tuple[int, int, str]:
    """One saved Work Order with one demand line: (wo_id, demand_id, pn)."""
    pn = part_number or _unique("PN")
    response = client.post(
        "/api/work-orders",
        json={"lines": [{"part_number": pn, "requested_quantity": 50}]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return int(body["id"]), int(body["demands"][0]["id"]), str(body["demands"][0]["part_number"])


def _create_route_template(
    engine: Engine,
    steps: list[dict[str, Any]],
    *,
    archived: bool = False,
) -> int:
    """Seed a RouteTemplate directly (no management API exists yet)."""
    with Session(engine) as session:
        template = models.RouteTemplate(
            name=_unique("ROUTE"),
            archived_at=datetime.datetime.now(datetime.UTC) if archived else None,
        )
        session.add(template)
        session.flush()
        for index, step in enumerate(steps):
            session.add(
                models.RouteStep(
                    route_template_id=template.id,
                    # Non-contiguous on purpose: the snapshot must copy
                    # sequence values verbatim.
                    sequence=(index + 1) * 10,
                    area_id=step["area_id"],
                    operation_id=step.get("operation_id"),
                    expected_duration=step.get("expected_duration"),
                    instructions=step.get("instructions"),
                )
            )
        session.commit()
        return int(template.id)


def _release(
    client: TestClient,
    work_order_id: int,
    demand_id: int,
    **overrides: Any,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": overrides.pop("part_number"),
        "quantity": 25,
        "route_mode": "FLOATING",
        "starting_area_id": overrides.pop("starting_area_id"),
        "operation_id": overrides.pop("operation_id"),
        "device_event_id": str(uuid.uuid4()),
    }
    payload.update(overrides)
    return client.post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release", json=payload
    )


_PRODUCTION_MODELS = (
    models.QuantityFlow,
    models.PartMovement,
    models.AssignedRoute,
    models.AssignedRouteStep,
    models.AuditEvent,
    models.PartNumber,
    models.WorkOrderDemand,
)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            model.__tablename__: connection.execute(
                sa.select(sa.func.count()).select_from(model.__table__)
            ).scalar_one()
            for model in _PRODUCTION_MODELS
        }


def _movement_row(engine: Engine, movement_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.PartMovement.__table__).where(
                models.PartMovement.__table__.c.id == movement_id
            )
        ).one()


def _flow_row(engine: Engine, flow_id: int) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.QuantityFlow.__table__).where(
                models.QuantityFlow.__table__.c.id == flow_id
            )
        ).one()


# ---------------------------------------------------------------------------
# FLOATING release — exact canonical shape
# ---------------------------------------------------------------------------


def test_floating_release_exact_shape_and_no_generic_audit(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    before = _counts(db_engine)

    event_id = str(uuid.uuid4())
    response = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
        device_event_id=event_id,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["part_number"] == pn
    assert body["quantity"] == 25
    assert body["route_mode"] == "FLOATING"
    assert body["assigned_route_id"] is None
    assert body["starting_area_id"] == area["id"]
    assert body["operation_id"] == operation["id"]
    assert body["device_event_id"] == event_id

    flow = _flow_row(db_engine, body["quantity_flow_id"])
    assert flow.part_number == pn
    assert flow.quantity == 25
    assert flow.status == "ACTIVE"
    assert flow.route_mode == "FLOATING"
    assert flow.assigned_route_id is None
    assert flow.current_area_id == area["id"]
    assert flow.closed_at is None

    movement = _movement_row(db_engine, body["movement_id"])
    assert movement.quantity_flow_id == body["quantity_flow_id"]
    assert movement.part_number == pn
    assert movement.movement_type == "RECEIVED"
    assert movement.quantity == 25
    assert movement.from_area_id is None
    assert movement.to_area_id == area["id"]
    assert movement.operation_id == operation["id"]
    assert movement.assigned_route_step_id is None
    assert movement.device_event_id == event_id
    # Synchronous online semantics: both server-assigned and equal.
    assert movement.occurred_at == movement.server_received_at
    # The immutable metadata carries the fingerprint and the
    # informational demand context (SLICE1 §11/§14).
    assert movement.metadata["request_fingerprint"]
    assert movement.metadata["context"]["work_order_demand_id"] == demand_id

    after = _counts(db_engine)
    assert after["quantity_flows"] == before["quantity_flows"] + 1
    assert after["part_movements"] == before["part_movements"] + 1
    assert after["assigned_routes"] == before["assigned_routes"]
    assert after["assigned_route_steps"] == before["assigned_route_steps"]
    # Production release writes NO generic audit row: the RECEIVED
    # Movement is the production audit record (SLICE1 §13/§16).
    assert after["audit_events"] == before["audit_events"]


# ---------------------------------------------------------------------------
# PLANNED release — exact snapshot shape and independence
# ---------------------------------------------------------------------------


def test_planned_release_exact_snapshot_shape(client: TestClient, db_engine: Engine) -> None:
    start_area = _create_area(client)
    start_operation = _create_operation(client, int(start_area["id"]))
    next_area = _create_area(client)
    next_operation = _create_operation(client, int(next_area["id"]))
    template_id = _create_route_template(
        db_engine,
        [
            {
                "area_id": start_area["id"],
                "operation_id": start_operation["id"],
                "expected_duration": datetime.timedelta(minutes=90),
                "instructions": "Deburr first",
            },
            {"area_id": next_area["id"], "operation_id": next_operation["id"]},
            {"area_id": start_area["id"]},
        ],
    )
    work_order_id, demand_id, pn = _create_demand(client)
    before = _counts(db_engine)

    response = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        route_mode="PLANNED",
        route_template_id=template_id,
        starting_area_id=start_area["id"],
        operation_id=start_operation["id"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["route_mode"] == "PLANNED"
    assert body["assigned_route_id"] is not None

    with db_engine.connect() as connection:
        snapshot = connection.execute(
            sa.select(models.AssignedRoute.__table__).where(
                models.AssignedRoute.__table__.c.id == body["assigned_route_id"]
            )
        ).one()
        snapshot_steps = connection.execute(
            sa.select(models.AssignedRouteStep.__table__)
            .where(
                models.AssignedRouteStep.__table__.c.assigned_route_id == body["assigned_route_id"]
            )
            .order_by(models.AssignedRouteStep.__table__.c.sequence)
        ).all()
    assert snapshot.source_route_template_id == template_id
    assert [step.sequence for step in snapshot_steps] == [10, 20, 30]
    assert [step.area_id for step in snapshot_steps] == [
        start_area["id"],
        next_area["id"],
        start_area["id"],
    ]
    assert [step.operation_id for step in snapshot_steps] == [
        start_operation["id"],
        next_operation["id"],
        None,
    ]
    assert snapshot_steps[0].expected_duration == datetime.timedelta(minutes=90)
    assert snapshot_steps[0].instructions == "Deburr first"

    flow = _flow_row(db_engine, body["quantity_flow_id"])
    assert flow.route_mode == "PLANNED"
    assert flow.assigned_route_id == body["assigned_route_id"]
    assert flow.current_area_id == start_area["id"]

    # The RECEIVED references the snapshot's first step — an
    # assigned_route_steps row, never the mutable route_steps template.
    movement = _movement_row(db_engine, body["movement_id"])
    assert movement.assigned_route_step_id == snapshot_steps[0].id

    after = _counts(db_engine)
    assert after["assigned_routes"] == before["assigned_routes"] + 1
    assert after["assigned_route_steps"] == before["assigned_route_steps"] + 3
    assert after["audit_events"] == before["audit_events"]


def test_route_template_edit_never_changes_snapshot(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    template_id = _create_route_template(
        db_engine, [{"area_id": area["id"], "operation_id": operation["id"]}]
    )
    work_order_id, demand_id, pn = _create_demand(client)
    response = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        route_mode="PLANNED",
        route_template_id=template_id,
        starting_area_id=area["id"],
        operation_id=operation["id"],
    )
    assert response.status_code == 201, response.text
    body = response.json()

    def snapshot_state() -> list[tuple[Any, ...]]:
        with db_engine.connect() as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    sa.select(models.AssignedRouteStep.__table__)
                    .where(
                        models.AssignedRouteStep.__table__.c.assigned_route_id
                        == body["assigned_route_id"]
                    )
                    .order_by(models.AssignedRouteStep.__table__.c.sequence)
                ).all()
            ]

    before_steps = snapshot_state()
    other_area = _create_area(client)
    with Session(db_engine) as session:
        step = session.scalars(
            sa.select(models.RouteStep).where(models.RouteStep.route_template_id == template_id)
        ).one()
        step.area_id = other_area["id"]
        step.instructions = "rewritten after release"
        session.add(
            models.RouteStep(route_template_id=template_id, sequence=999, area_id=other_area["id"])
        )
        session.commit()

    assert snapshot_state() == before_steps
    assert (
        _movement_row(db_engine, body["movement_id"]).assigned_route_step_id == before_steps[0][0]
    )


# ---------------------------------------------------------------------------
# Validation — every invalid input creates nothing
# ---------------------------------------------------------------------------


def test_invalid_inputs_create_nothing(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    other_area = _create_area(client)
    other_operation = _create_operation(client, int(other_area["id"]))
    inactive_area = _create_area(client)
    assert (
        client.patch(f"/api/areas/{inactive_area['id']}", json={"is_active": False}).status_code
        == 200
    )
    inactive_operation = _create_operation(client, int(area["id"]))
    assert (
        client.patch(
            f"/api/operations/{inactive_operation['id']}", json={"is_active": False}
        ).status_code
        == 200
    )
    archived_template = _create_route_template(db_engine, [{"area_id": area["id"]}], archived=True)
    empty_template = _create_route_template(db_engine, [])
    mismatched_template = _create_route_template(db_engine, [{"area_id": other_area["id"]}])
    wrong_operation_template = _create_route_template(
        db_engine, [{"area_id": area["id"], "operation_id": inactive_operation["id"]}]
    )
    work_order_id, demand_id, pn = _create_demand(client)
    before = _counts(db_engine)

    def rejected(expected_status: int, **overrides: Any) -> None:
        response = _release(
            client,
            overrides.pop("work_order_id", work_order_id),
            overrides.pop("demand_id", demand_id),
            part_number=overrides.pop("part_number", pn),
            starting_area_id=overrides.pop("starting_area_id", area["id"]),
            operation_id=overrides.pop("operation_id", operation["id"]),
            **overrides,
        )
        assert response.status_code == expected_status, response.text

    rejected(422, part_number="BAD PN")  # internal whitespace is never a PN
    rejected(422, part_number=_unique("OTHER"))  # PN does not match the demand
    rejected(422, quantity=0)
    rejected(422, quantity=-5)
    rejected(422, quantity=True)  # a bool is never a quantity (StrictInt)
    rejected(422, quantity="25")
    rejected(404, demand_id=999999)  # demand must exist
    rejected(404, work_order_id=999999)  # ... on the addressed Work Order
    rejected(422, starting_area_id=999999)
    rejected(409, starting_area_id=inactive_area["id"])
    rejected(422, operation_id=999999)
    rejected(422, operation_id=other_operation["id"])  # Operation of another Area
    rejected(409, operation_id=inactive_operation["id"])
    rejected(422, route_mode="PLANNED")  # PLANNED requires a template
    rejected(422, route_template_id=archived_template)  # FLOATING never takes one
    rejected(409, route_mode="PLANNED", route_template_id=archived_template)
    rejected(422, route_mode="PLANNED", route_template_id=999999)
    rejected(422, route_mode="PLANNED", route_template_id=empty_template)
    rejected(422, route_mode="PLANNED", route_template_id=mismatched_template)
    rejected(422, route_mode="PLANNED", route_template_id=wrong_operation_template)
    rejected(422, route_mode="REPAIR")
    rejected(422, device_event_id="not-a-uuid")
    rejected(422, actor="someone")  # extra="forbid": no client actor
    rejected(422, confirm_active_quantity="yes")  # StrictBool

    assert _counts(db_engine) == before  # zero writes, including audit rows


# ---------------------------------------------------------------------------
# Active-quantity safety — confirmation required, never a merge
# ---------------------------------------------------------------------------


def test_active_quantity_requires_confirmation_and_never_merges(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    first = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
        quantity=30,
    )
    assert first.status_code == 201, first.text
    first_flow_id = first.json()["quantity_flow_id"]
    before = _counts(db_engine)

    unconfirmed = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
        quantity=20,
    )
    assert unconfirmed.status_code == 409, unconfirmed.text
    body = unconfirmed.json()
    assert body["confirmation_required"] is True
    assert body["existing_active_quantity"] == [
        {
            "quantity_flow_id": first_flow_id,
            "quantity": 30,
            "route_mode": "FLOATING",
            "current_area_id": area["id"],
            "current_area_name": area["name"],
        }
    ]
    assert _counts(db_engine) == before  # rejected with no write

    confirmed = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
        quantity=20,
        confirm_active_quantity=True,
    )
    assert confirmed.status_code == 201, confirmed.text
    second_flow_id = confirmed.json()["quantity_flow_id"]
    assert second_flow_id != first_flow_id

    # A separate flow, NEVER a merge: both keep their own quantities.
    assert _flow_row(db_engine, first_flow_id).quantity == 30
    assert _flow_row(db_engine, second_flow_id).quantity == 20
    assert _counts(db_engine)["quantity_flows"] == before["quantity_flows"] + 1


# ---------------------------------------------------------------------------
# Idempotency — replay and mismatch (SLICE1 §14)
# ---------------------------------------------------------------------------


def test_same_device_event_id_replays_original_result(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    event_id = str(uuid.uuid4())
    payload = {
        "part_number": pn,
        "starting_area_id": area["id"],
        "operation_id": operation["id"],
        "device_event_id": event_id,
    }
    first = _release(client, work_order_id, demand_id, **payload)
    assert first.status_code == 201, first.text
    before = _counts(db_engine)

    retry = _release(client, work_order_id, demand_id, **payload)
    assert retry.status_code == 200, retry.text  # replay, not a new release
    assert retry.json() == first.json()
    assert _counts(db_engine) == before

    # A textual UUID variant is the same id (normalized), and the
    # replay works even after the confirmed Operation went inactive —
    # the idempotency check runs before entity validation (SLICE1 §13).
    # (The Area itself cannot be deactivated while it holds active
    # quantity — that guard is part of the environment rules.)
    deactivated = client.patch(f"/api/operations/{operation['id']}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text
    variant = _release(
        client,
        work_order_id,
        demand_id,
        **{**payload, "device_event_id": event_id.upper()},
    )
    assert variant.status_code == 200, variant.text
    assert variant.json() == first.json()
    assert _counts(db_engine) == before


def test_device_event_id_reuse_with_different_request_conflicts(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    event_id = str(uuid.uuid4())
    first = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
        device_event_id=event_id,
    )
    assert first.status_code == 201, first.text
    before = _counts(db_engine)

    mismatch = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
        device_event_id=event_id,
        quantity=26,  # different normalized request
        confirm_active_quantity=True,
    )
    assert mismatch.status_code == 409, mismatch.text
    assert "device_event_id" in mismatch.json()["detail"]
    assert _counts(db_engine) == before  # nothing created


def test_duplicate_device_event_id_race_lost_at_commit(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent duplicate that wins at COMMIT resolves like a replay.

    The pre-lock check AND the post-lock re-check are blinded (two
    calls) so the request reaches the unique ``device_event_id``
    constraint at COMMIT — the whole staged release (flow + Movement)
    rolls back and the committed original is returned unchanged; a
    mismatched fingerprint is the explicit conflict instead.
    """
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    event_id = str(uuid.uuid4())
    payload = {
        "part_number": pn,
        "starting_area_id": area["id"],
        "operation_id": operation["id"],
        "device_event_id": event_id,
        "confirm_active_quantity": True,
    }
    first = _release(client, work_order_id, demand_id, **payload)
    assert first.status_code == 201, first.text
    before = _counts(db_engine)

    real = production_release._committed_release
    blind = {"remaining": 2}

    def blinded(session: Session, device_event_id: str) -> Any:
        if blind["remaining"]:
            blind["remaining"] -= 1
            return None
        return real(session, device_event_id)

    monkeypatch.setattr(production_release, "_committed_release", blinded)
    race_replay = _release(client, work_order_id, demand_id, **payload)
    assert race_replay.status_code == 200, race_replay.text
    assert race_replay.json() == first.json()
    assert _counts(db_engine) == before

    blind["remaining"] = 2
    race_mismatch = _release(client, work_order_id, demand_id, **{**payload, "quantity": 99})
    assert race_mismatch.status_code == 409, race_mismatch.text
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# Atomicity — any failure point leaves zero writes
# ---------------------------------------------------------------------------


def test_failure_after_partial_staging_rolls_back_everything(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    template_id = _create_route_template(
        db_engine, [{"area_id": area["id"]}, {"area_id": area["id"]}]
    )
    work_order_id, demand_id, pn = _create_demand(client)
    before = _counts(db_engine)

    real_flush = translated_flush

    def _failing_flush_at(call_number: int) -> Any:
        state = {"calls": 0}

        def failing(session: Session, conflicts: dict[str, str]) -> None:
            state["calls"] += 1
            if state["calls"] == call_number:
                raise RuntimeError("injected failure inside the release transaction")
            real_flush(session, conflicts)

        return failing

    # PLANNED flush order: snapshot (1), snapshot steps (2), flow (3).
    # Failing at each point must leave zero committed writes.
    for call_number in (1, 2, 3):
        monkeypatch.setattr(production_release, "flush", _failing_flush_at(call_number))
        with pytest.raises(RuntimeError, match="injected failure"):
            _release(
                client,
                work_order_id,
                demand_id,
                part_number=pn,
                route_mode="PLANNED",
                route_template_id=template_id,
                starting_area_id=area["id"],
                operation_id=operation["id"],
            )
        assert _counts(db_engine) == before

    monkeypatch.setattr(production_release, "flush", real_flush)


# ---------------------------------------------------------------------------
# Immutability, projection rebuild, conservation
# ---------------------------------------------------------------------------


def test_part_movements_cannot_be_updated_or_deleted(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    response = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
    )
    assert response.status_code == 201, response.text
    movement_id = response.json()["movement_id"]

    with db_engine.connect() as connection:
        with pytest.raises(sa.exc.DBAPIError):
            connection.execute(
                sa.update(models.PartMovement)
                .where(models.PartMovement.id == movement_id)
                .values(quantity=999)
            )
        connection.rollback()
        with pytest.raises(sa.exc.DBAPIError):
            connection.execute(
                sa.delete(models.PartMovement).where(models.PartMovement.id == movement_id)
            )
        connection.rollback()
    assert _movement_row(db_engine, movement_id).quantity == 25


def test_projection_rebuilds_from_movement_history_alone(
    client: TestClient, db_engine: Engine
) -> None:
    area_a = _create_area(client)
    operation_a = _create_operation(client, int(area_a["id"]))
    area_b = _create_area(client)
    operation_b = _create_operation(client, int(area_b["id"]))
    template_id = _create_route_template(
        db_engine, [{"area_id": area_b["id"]}, {"area_id": area_a["id"]}]
    )
    for starting, operation, mode, template in (
        (area_a, operation_a, "FLOATING", None),
        (area_b, operation_b, "PLANNED", template_id),
    ):
        work_order_id, demand_id, pn = _create_demand(client)
        response = _release(
            client,
            work_order_id,
            demand_id,
            part_number=pn,
            route_mode=mode,
            route_template_id=template,
            starting_area_id=starting["id"],
            operation_id=operation["id"],
        )
        assert response.status_code == 201, response.text

    with Session(db_engine) as session:
        rebuilt = projections.rebuild_current_area_ids(session)
        stored = {
            flow_id: current_area_id
            for flow_id, current_area_id in session.execute(
                sa.select(models.QuantityFlow.id, models.QuantityFlow.current_area_id)
            )
        }
    # Every flow appears (its first Movement is its RECEIVED) and the
    # stored projection matches the history-derived value exactly.
    assert rebuilt == stored
    assert stored  # the check is not vacuous


def test_conservation_of_released_quantities(client: TestClient, db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        flows: dict[str, int] = {
            part_number: int(total)
            for part_number, total in connection.execute(
                sa.select(
                    models.QuantityFlow.part_number,
                    sa.func.sum(models.QuantityFlow.quantity),
                )
                .where(models.QuantityFlow.status == "ACTIVE")
                .group_by(models.QuantityFlow.part_number)
            )
        }
        received: dict[str, int] = {
            part_number: int(total)
            for part_number, total in connection.execute(
                sa.select(
                    models.PartMovement.part_number,
                    sa.func.sum(models.PartMovement.quantity),
                )
                .where(models.PartMovement.movement_type == "RECEIVED")
                .group_by(models.PartMovement.part_number)
            )
        }
    # Σ(active flow quantities per PN) = Σ(RECEIVED quantities per PN).
    assert flows == received
    assert flows  # earlier tests released quantity, so never vacuous


# ---------------------------------------------------------------------------
# Work Order Demand removal (PROJECT_PROFILE §13)
# ---------------------------------------------------------------------------


def test_demand_without_release_deletes_and_cascades_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    pn = _unique("PN")
    response = client.post(
        "/api/work-orders",
        json={
            "lines": [
                {"part_number": pn, "requested_quantity": 5},
                {"part_number": _unique("PN"), "requested_quantity": 7},
            ]
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    work_order_id = body["id"]
    demand_id = body["demands"][0]["id"]
    sibling_id = body["demands"][1]["id"]

    # Wrong addressing removes nothing.
    assert client.delete(f"/api/work-orders/{work_order_id}/demands/999999").status_code == 404
    assert client.delete(f"/api/work-orders/999999/demands/{demand_id}").status_code == 404

    before = _counts(db_engine)
    deleted = client.delete(f"/api/work-orders/{work_order_id}/demands/{demand_id}")
    assert deleted.status_code == 204, deleted.text

    remaining = client.get(f"/api/work-orders/{work_order_id}").json()
    assert [demand["id"] for demand in remaining["demands"]] == [sibling_id]
    after = _counts(db_engine)
    assert after["work_order_demands"] == before["work_order_demands"] - 1
    # The PN master survives, and the demand's audit history remains —
    # historical records never disappear.
    assert after["part_numbers"] == before["part_numbers"]
    assert after["audit_events"] == before["audit_events"]
    with db_engine.connect() as connection:
        audit_rows = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.AuditEvent.__table__)
            .where(
                models.AuditEvent.__table__.c.entity_type == "WorkOrderDemand",
                models.AuditEvent.__table__.c.entity_id == str(demand_id),
            )
        ).scalar_one()
    assert audit_rows == 1  # the CREATED row of the removed demand


def test_demand_deletion_blocked_after_release(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    pn = _unique("PN")
    work_order_id, demand_id, _ = _create_demand(client, part_number=pn)
    released = _release(
        client,
        work_order_id,
        demand_id,
        part_number=pn,
        starting_area_id=area["id"],
        operation_id=operation["id"],
    )
    assert released.status_code == 201, released.text

    # Another demand for the SAME PN that never released stays
    # removable: the rule is per demand, not per PN. The other Work
    # Order carries a second line so the removal is not the last-line
    # case.
    other = client.post(
        "/api/work-orders",
        json={
            "lines": [
                {"part_number": pn, "requested_quantity": 5},
                {"part_number": _unique("PN"), "requested_quantity": 5},
            ]
        },
    )
    assert other.status_code == 201, other.text
    other_work_order_id = other.json()["id"]
    other_demand_id = other.json()["demands"][0]["id"]

    before = _counts(db_engine)
    blocked = client.delete(f"/api/work-orders/{work_order_id}/demands/{demand_id}")
    assert blocked.status_code == 409, blocked.text
    assert (
        blocked.json()["detail"] == "Cannot remove: production quantity has already been released."
    )
    assert _counts(db_engine) == before  # nothing removed, nothing cascaded

    removable = client.delete(f"/api/work-orders/{other_work_order_id}/demands/{other_demand_id}")
    assert removable.status_code == 204, removable.text
    after = _counts(db_engine)
    assert after["work_order_demands"] == before["work_order_demands"] - 1
    # Production data and the PN master are untouched by the removal.
    assert after["quantity_flows"] == before["quantity_flows"]
    assert after["part_movements"] == before["part_movements"]
    assert after["part_numbers"] == before["part_numbers"]


# ---------------------------------------------------------------------------
# Round 2 — replay immutability, last-line removal, concurrency
# ---------------------------------------------------------------------------


def test_replay_returns_original_result_after_projection_change(
    client: TestClient, db_engine: Engine
) -> None:
    """The replay body is the immutable ORIGINAL release result.

    A later change of ``QuantityFlow.current_area_id`` (the mutable
    projection) must not leak into the response of a retried old
    release: everything is read from the immutable ``RECEIVED``
    Movement, and the response's ``starting_area_id`` stays the
    release-time starting Area.
    """
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    other_area = _create_area(client)
    work_order_id, demand_id, pn = _create_demand(client)
    payload = {
        "part_number": pn,
        "starting_area_id": area["id"],
        "operation_id": operation["id"],
        "device_event_id": str(uuid.uuid4()),
    }
    first = _release(client, work_order_id, demand_id, **payload)
    assert first.status_code == 201, first.text
    original = first.json()
    assert original["starting_area_id"] == area["id"]

    # Simulate the flow moving on (a later phase's transfer would do
    # this inside a Movement transaction): the projection changes, the
    # RECEIVED Movement does not.
    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.QuantityFlow)
            .where(models.QuantityFlow.id == original["quantity_flow_id"])
            .values(current_area_id=other_area["id"])
        )

    replay = _release(client, work_order_id, demand_id, **payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == original
    assert replay.json()["starting_area_id"] == area["id"]


def test_last_demand_line_cannot_be_removed(client: TestClient, db_engine: Engine) -> None:
    """A Work Order always keeps at least one demand line (PP §8.2)."""
    work_order_id, demand_id, _ = _create_demand(client)
    before = _counts(db_engine)

    blocked = client.delete(f"/api/work-orders/{work_order_id}/demands/{demand_id}")
    assert blocked.status_code == 409, blocked.text
    assert "last demand line" in blocked.json()["detail"]
    assert _counts(db_engine) == before  # zero writes
    remaining = client.get(f"/api/work-orders/{work_order_id}").json()
    assert [demand["id"] for demand in remaining["demands"]] == [demand_id]

    # With a sibling present the same unreleased line deletes fine —
    # until it IS the last one.
    two_line = client.post(
        "/api/work-orders",
        json={
            "lines": [
                {"part_number": _unique("PN"), "requested_quantity": 5},
                {"part_number": _unique("PN"), "requested_quantity": 5},
            ]
        },
    )
    assert two_line.status_code == 201, two_line.text
    wo_id = two_line.json()["id"]
    first_id, second_id = (demand["id"] for demand in two_line.json()["demands"])
    assert client.delete(f"/api/work-orders/{wo_id}/demands/{first_id}").status_code == 204
    assert client.delete(f"/api/work-orders/{wo_id}/demands/{second_id}").status_code == 409


class _PauseFirstActiveCheck:
    """Test seam: pause the FIRST release inside its active-quantity
    check — after it holds the PN advisory lock (and the starting-Area
    row lock) — so a competing transaction can be started and observed
    blocking, then released deterministically."""

    def __init__(self) -> None:
        self.real = production_release._existing_active_distribution
        self.first_inside = threading.Event()
        self.let_first_finish = threading.Event()
        self._pause_taken = threading.Lock()
        self._paused_once = False

    def __call__(self, session: Session, part_number: str) -> list[dict[str, Any]]:
        result = self.real(session, part_number)
        with self._pause_taken:
            should_pause = not self._paused_once
            self._paused_once = True
        if should_pause:
            self.first_inside.set()
            assert self.let_first_finish.wait(timeout=20), "test deadlock: never released"
        return result


def test_concurrent_same_pn_releases_cannot_both_pass_active_check(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two unconfirmed concurrent releases of one PN — two demands, two
    event ids — serialize on the PN lock: exactly one creates, the
    other must see the active quantity and require confirmation."""
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    pn = _unique("PN")
    wo_1, demand_1, _ = _create_demand(client, part_number=pn)
    wo_2, demand_2, _ = _create_demand(client, part_number=pn)

    pause = _PauseFirstActiveCheck()
    monkeypatch.setattr(production_release, "_existing_active_distribution", pause)

    results: dict[str, Any] = {}

    def run(name: str, work_order_id: int, demand_id: int) -> None:
        with Session(db_engine) as session:
            try:
                results[name] = production_release.release_to_production(
                    session,
                    work_order_id=work_order_id,
                    work_order_demand_id=demand_id,
                    part_number=pn,
                    quantity=10,
                    route_mode="FLOATING",
                    route_template_id=None,
                    starting_area_id=int(area["id"]),
                    operation_id=int(operation["id"]),
                    confirm_active_quantity=False,
                    device_event_id=str(uuid.uuid4()),
                )
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results[name] = exc

    first = threading.Thread(target=run, args=("first", wo_1, demand_1), daemon=True)
    second = threading.Thread(target=run, args=("second", wo_2, demand_2), daemon=True)
    try:
        first.start()
        assert pause.first_inside.wait(timeout=20)  # holds the PN lock, paused
        second.start()
        time.sleep(1.0)  # the second release is now blocked on the PN lock
        assert "second" not in results  # ...really blocked, not failed
    finally:
        pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)
    assert not first.is_alive() and not second.is_alive()

    winner = results["first"]
    loser = results["second"]
    assert isinstance(winner, production_release.ProductionRelease) and winner.created
    assert isinstance(loser, ActiveQuantityConfirmationRequiredError)
    assert [entry["quantity_flow_id"] for entry in loser.existing_active_quantity] == [
        winner.quantity_flow_id
    ]
    with db_engine.connect() as connection:
        flow_count = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.QuantityFlow.__table__)
            .where(models.QuantityFlow.__table__.c.part_number == pn)
        ).scalar_one()
    assert flow_count == 1  # exactly one release passed unconfirmed


def test_concurrent_identical_retries_one_creates_one_replays(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent identical submissions — same demand, same
    device_event_id, confirm_active_quantity=false — resolve as one
    created release and one replay of the original result. The replay
    must NOT trip the active-quantity confirmation over the original's
    own flow: the idempotency re-check runs after the PN lock."""
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)
    event_id = str(uuid.uuid4())

    pause = _PauseFirstActiveCheck()
    monkeypatch.setattr(production_release, "_existing_active_distribution", pause)

    results: dict[str, Any] = {}

    def run(name: str) -> None:
        with Session(db_engine) as session:
            try:
                results[name] = production_release.release_to_production(
                    session,
                    work_order_id=work_order_id,
                    work_order_demand_id=demand_id,
                    part_number=pn,
                    quantity=25,
                    route_mode="FLOATING",
                    route_template_id=None,
                    starting_area_id=int(area["id"]),
                    operation_id=int(operation["id"]),
                    confirm_active_quantity=False,
                    device_event_id=event_id,
                )
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results[name] = exc

    first = threading.Thread(target=run, args=("first",), daemon=True)
    second = threading.Thread(target=run, args=("second",), daemon=True)
    try:
        first.start()
        assert pause.first_inside.wait(timeout=20)
        second.start()  # pre-lock check sees nothing yet, blocks on the PN lock
        time.sleep(1.0)
        assert "second" not in results
    finally:
        pause.let_first_finish.set()
    first.join(timeout=20)
    second.join(timeout=20)
    assert not first.is_alive() and not second.is_alive()

    outcomes = [results["first"], results["second"]]
    for outcome in outcomes:
        assert isinstance(outcome, production_release.ProductionRelease), outcome
    created = [outcome for outcome in outcomes if outcome.created]
    replayed = [outcome for outcome in outcomes if not outcome.created]
    assert len(created) == 1 and len(replayed) == 1
    # The replay carries the ORIGINAL committed result, field by field.
    assert replayed[0] == created[0]._replace(created=False)
    with db_engine.connect() as connection:
        movement_count = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartMovement.__table__)
            .where(models.PartMovement.__table__.c.part_number == pn)
        ).scalar_one()
    assert movement_count == 1  # at-most-once recording held


def test_concurrent_release_vs_area_deactivation_single_serial_outcome(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release and Area deactivation serialize on the Area row lock.

    Release wins → the deactivation sees the fresh active quantity and
    is blocked. (Deactivation wins → the release sees the inactive
    Area — asserted sequentially below.) Either way no inactive Area
    ever holds an ACTIVE flow.
    """
    area = _create_area(client)
    operation = _create_operation(client, int(area["id"]))
    work_order_id, demand_id, pn = _create_demand(client)

    pause = _PauseFirstActiveCheck()
    monkeypatch.setattr(production_release, "_existing_active_distribution", pause)

    results: dict[str, Any] = {}

    def run_release() -> None:
        with Session(db_engine) as session:
            try:
                results["release"] = production_release.release_to_production(
                    session,
                    work_order_id=work_order_id,
                    work_order_demand_id=demand_id,
                    part_number=pn,
                    quantity=10,
                    route_mode="FLOATING",
                    route_template_id=None,
                    starting_area_id=int(area["id"]),
                    operation_id=int(operation["id"]),
                    confirm_active_quantity=False,
                    device_event_id=str(uuid.uuid4()),
                )
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results["release"] = exc

    def run_deactivation() -> None:
        with Session(db_engine) as session:
            try:
                results["deactivation"] = environment.update_area(
                    session, int(area["id"]), is_active=False
                )
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results["deactivation"] = exc

    release_thread = threading.Thread(target=run_release, daemon=True)
    deactivation_thread = threading.Thread(target=run_deactivation, daemon=True)
    try:
        release_thread.start()
        # Paused inside the active check: the release transaction now
        # holds the starting-Area row lock until COMMIT.
        assert pause.first_inside.wait(timeout=20)
        deactivation_thread.start()
        time.sleep(1.0)  # deactivation is blocked on the Area row lock
        assert "deactivation" not in results
    finally:
        pause.let_first_finish.set()
    release_thread.join(timeout=20)
    deactivation_thread.join(timeout=20)
    assert not release_thread.is_alive() and not deactivation_thread.is_alive()

    released = results["release"]
    blocked = results["deactivation"]
    assert isinstance(released, production_release.ProductionRelease) and released.created
    assert isinstance(blocked, ConflictError)
    assert "still holds active quantity" in blocked.message

    # The other serial order: a committed deactivation blocks release.
    empty_area = _create_area(client)
    empty_operation = _create_operation(client, int(empty_area["id"]))
    assert (
        client.patch(f"/api/areas/{empty_area['id']}", json={"is_active": False}).status_code == 200
    )
    wo_2, demand_2, pn_2 = _create_demand(client)
    late_release = _release(
        client,
        wo_2,
        demand_2,
        part_number=pn_2,
        starting_area_id=empty_area["id"],
        operation_id=empty_operation["id"],
    )
    assert late_release.status_code == 409, late_release.text

    # Invariant: no inactive Area holds ACTIVE quantity — ever.
    with db_engine.connect() as connection:
        violations = connection.execute(
            sa.select(sa.func.count())
            .select_from(
                models.QuantityFlow.__table__.join(
                    models.Area.__table__,
                    models.Area.__table__.c.id == models.QuantityFlow.__table__.c.current_area_id,
                )
            )
            .where(
                models.QuantityFlow.__table__.c.status == "ACTIVE",
                sa.not_(models.Area.__table__.c.is_active),
            )
        ).scalar_one()
    assert violations == 0


def test_area_deactivation_patch_applies_metadata_edits_atomically(
    client: TestClient, db_engine: Engine
) -> None:
    """One PATCH with metadata edits AND is_active=false persists all.

    Regression: the deactivation branch locks the Area row via
    ``Session.refresh(with_for_update=True)``, which reloads every
    attribute — taken after the field mutations it would silently
    discard them. The lock must come first, so the combined request
    commits the new name/description/color/is_terminal together with
    the inactive state in one transaction.
    """
    area = _create_area(client)  # active, holds no quantity
    new_name = _unique("AREA-RENAMED")
    response = client.patch(
        f"/api/areas/{area['id']}",
        json={
            "name": new_name,
            "description": "combined-edit description",
            "color": "#123456",
            "is_terminal": True,
            "is_active": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == new_name
    assert body["description"] == "combined-edit description"
    assert body["color"] == "#123456"
    assert body["is_terminal"] is True
    assert body["is_active"] is False

    # Committed state, not just the response echo.
    with db_engine.connect() as connection:
        row = connection.execute(
            sa.select(models.Area.__table__).where(models.Area.__table__.c.id == area["id"])
        ).one()
    assert row.name == new_name
    assert row.description == "combined-edit description"
    assert row.color == "#123456"
    assert row.is_terminal is True
    assert row.is_active is False


# ---------------------------------------------------------------------------
# Server-derived release evidence in the Work Order read models
# ---------------------------------------------------------------------------


def test_release_evidence_and_derived_status_in_work_order_reads(
    client: TestClient, db_engine: Engine
) -> None:
    """`has_released_quantity` and the derived OPEN/RELEASED status come
    from the immutable RECEIVED Movement context — server state that
    survives any reload, never a client-session flag. A mixed Work
    Order (released and unreleased demand) stays OPEN; only a Work
    Order whose EVERY current demand has release evidence reads
    RELEASED. The stored status column keeps OPEN (derived read model,
    no migration)."""
    area = _create_area(client)
    operation = _create_operation(client, area["id"])
    pn_a, pn_b = _unique("PN"), _unique("PN")
    response = client.post(
        "/api/work-orders",
        json={
            "lines": [
                {"part_number": pn_a, "requested_quantity": 10},
                {"part_number": pn_b, "requested_quantity": 5},
            ]
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    wo_id = int(body["id"])
    demand_a, demand_b = (int(line["id"]) for line in body["demands"])

    # Before any release: no evidence, status OPEN everywhere.
    assert all(line["has_released_quantity"] is False for line in body["demands"])
    detail = client.get(f"/api/work-orders/{wo_id}").json()
    assert detail["status"] == "OPEN"
    assert all(line["has_released_quantity"] is False for line in detail["demands"])

    # Release ONE of two demands: evidence on that demand only, and the
    # mixed Work Order stays OPEN (never "any released => RELEASED").
    released = _release(
        client,
        wo_id,
        demand_a,
        part_number=pn_a,
        quantity=10,
        starting_area_id=area["id"],
        operation_id=operation["id"],
    )
    assert released.status_code == 201, released.text
    detail = client.get(f"/api/work-orders/{wo_id}").json()
    flags = {int(line["id"]): line["has_released_quantity"] for line in detail["demands"]}
    assert flags == {demand_a: True, demand_b: False}
    assert detail["status"] == "OPEN"
    listed = client.get("/api/work-orders").json()
    assert {entry["id"]: entry["status"] for entry in listed}[wo_id] == "OPEN"

    # Release the second demand: every current demand carries evidence
    # — the read status derives RELEASED while the stored column keeps
    # OPEN.
    released = _release(
        client,
        wo_id,
        demand_b,
        part_number=pn_b,
        quantity=5,
        starting_area_id=area["id"],
        operation_id=operation["id"],
    )
    assert released.status_code == 201, released.text
    detail = client.get(f"/api/work-orders/{wo_id}").json()
    assert detail["status"] == "RELEASED"
    assert all(line["has_released_quantity"] is True for line in detail["demands"])
    listed = client.get("/api/work-orders").json()
    assert {entry["id"]: entry["status"] for entry in listed}[wo_id] == "RELEASED"
    with db_engine.connect() as connection:
        stored = connection.execute(
            sa.select(models.WorkOrder.__table__.c.status).where(
                models.WorkOrder.__table__.c.id == wo_id
            )
        ).scalar_one()
    assert stored == "OPEN"


def test_external_number_edit_stays_available_after_full_release(
    client: TestClient, db_engine: Engine
) -> None:
    """Release evidence never blocks the audited Work Order Number edit
    (PROJECT_PROFILE §7): an internal Work Order that fully released
    still receives its real external number later — stored VERBATIM,
    surrounding whitespace included."""
    area = _create_area(client)
    operation = _create_operation(client, area["id"])
    pn = _unique("PN")
    body = client.post(
        "/api/work-orders",
        json={"lines": [{"part_number": pn, "requested_quantity": 4}]},
    ).json()
    wo_id, demand_id = int(body["id"]), int(body["demands"][0]["id"])
    assert body["work_order_number"] is None

    released = _release(
        client,
        wo_id,
        demand_id,
        part_number=pn,
        quantity=4,
        starting_area_id=area["id"],
        operation_id=operation["id"],
    )
    assert released.status_code == 201, released.text
    assert client.get(f"/api/work-orders/{wo_id}").json()["status"] == "RELEASED"

    number = f"  {_unique('WO')}  "
    edited = client.patch(f"/api/work-orders/{wo_id}", json={"work_order_number": number})
    assert edited.status_code == 200, edited.text
    assert edited.json()["work_order_number"] == number
    with db_engine.connect() as connection:
        stored = connection.execute(
            sa.select(models.WorkOrder.__table__.c.work_order_number).where(
                models.WorkOrder.__table__.c.id == wo_id
            )
        ).scalar_one()
    assert stored == number
