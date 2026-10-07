"""Integration tests for Phase 14 slice 6 — the AssignedRoute adjustment.

Exercises the full request path against a dedicated temporary database
migrated to head by the real Alembic chain (PROJECT_PROFILE §8.10, §17;
owner decisions OD-P11, OD-S6-6, OD-S6-19, OD-S6-20):

- AD-1 … AD-5: ``POST /api/quantity-flows/{id}/route-adjustments``
  replaces only the future (unreferenced) steps of one ACTIVE PLANNED
  flow's own AssignedRoute — past steps, the Movement history, the Route
  Template and every other flow stay untouched — and the next on-route
  arrival is judged against the adjusted route;
- AD-6: every refusal writes nothing, with the exact copy and absolute
  step numbers;
- AD-7 … AD-12: idempotency (actor-aware replay), concurrency on the
  same flow and on the same ``device_event_id``, serialization with a
  transfer and a partial transfer of the same flow, and the lock order;
- AD-13: authorization precedes every lock;
- AD-14 / AD-15: the editor read ``GET /api/tracking/assigned-routes`` and
  Tracking's ``route_adjustments`` notes and ``route_adjustment_total``;
- AD-16: merge compatibility follows the adjusted route;
- AD-17: a station Undo of a command that created a flow whose route was
  adjusted afterwards is refused (U-RA), zero writes.

Set-up data is created through the harness administrator; station
commands go through the enrolled-device harness. Every refusal asserts
that no audit row, no Movement and no step row changed.
"""

import datetime
import functools
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import reconciliation
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    admin_of,
    anonymous_client,
    another_session,
    client_as,
    station_device_client,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_route_adjustments_api"
_REASON = "the customer added a deburr operation"
AR = Permission.ASSIGN_ROUTES

_R1 = (
    "This request was already recorded by another user. Nothing more was recorded"
    " — reload to see the current state."
)
_A2 = "Your account does not have permission to do this."
_RT6 = (
    "A route adjustment needs a reason. Enter why the route is adjusted — nothing is"
    " changed until then."
)
_RT8 = "The route could not be checked — reload the route and try again."
_RT10 = (
    "This device_event_id was already used for a different route adjustment. Nothing was"
    " changed — a new adjustment needs a new device_event_id."
)


def _rt2(flow_id: int) -> str:
    return (
        f"Quantity Flow {flow_id} follows a Floating Route — it has no assigned route to"
        " change. Nothing was changed."
    )


def _rt3(flow_id: int) -> str:
    return (
        f"Quantity Flow {flow_id} is no longer active, so its route can no longer change."
        " Nothing was changed."
    )


def _rt4(flow_id: int) -> str:
    return (
        f"The route of Quantity Flow {flow_id} changed since you opened it — the quantity"
        " moved on or the route was changed by someone else. Nothing was changed. Review the"
        " current route and make the change again."
    )


def _ura(flow_id: int) -> str:
    return (
        f"Later activity exists for this quantity: the route of Quantity Flow {flow_id} was"
        " adjusted after this action, so it cannot be undone from a station. Correct the"
        " quantity with the production workflows instead."
    )


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
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
    """Anonymous application client (with enrolled station devices)."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield station_device_client(test_client)
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# The shop
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _event() -> str:
    return str(uuid.uuid4())


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.content else None


@dataclass(frozen=True)
class _Cell:
    area_id: int
    operation_id: int
    station_id: str
    name: str


@dataclass(frozen=True)
class _Shop:
    department_id: int
    cells: dict[str, _Cell]

    def __getitem__(self, key: str) -> _Cell:
        return self.cells[key]


def _cell(admin: TestClient, department_id: int, *, is_terminal: bool = False) -> _Cell:
    name = _unique("AREA")
    area = _ok(
        admin.post(
            "/api/areas",
            json={"department_id": department_id, "name": name, "is_terminal": is_terminal},
        ),
        201,
    )
    operation = _ok(
        admin.post("/api/operations", json={"area_id": area["id"], "code": _unique("OP")}), 201
    )
    station = _ok(
        admin.post("/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area["id"]}),
        201,
    )
    return _Cell(int(area["id"]), int(operation["id"]), str(station["station_id"]), name)


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    admin = admin_of(client)
    _ok(
        admin.put(
            "/api/barcode-configuration/machine-asset-tag-format",
            json={"prefix": "RA-", "digits": 4},
        )
    )
    department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    department_id = int(department["id"])
    cells = {key: _cell(admin, department_id) for key in "ABCDEXYWPQ"}
    cells["STOCK"] = _cell(admin, department_id, is_terminal=True)
    return _Shop(department_id, cells)


def _step(cell: _Cell, **extra: Any) -> dict[str, Any]:
    return {"area_id": cell.area_id, "operation_id": cell.operation_id, **extra}


def _template(client: TestClient, *cells: _Cell, first_duration: str | None = None) -> int:
    steps = [_step(cell) for cell in cells]
    if first_duration is not None:
        steps[0]["expected_duration"] = first_duration
    body = _ok(
        admin_of(client).post(
            "/api/route-templates",
            json={"name": _unique("ROUTE"), "description": None, "steps": steps},
        ),
        201,
    )
    return int(body["id"])


def _release(
    client: TestClient,
    start: _Cell,
    template_id: int | None,
    *,
    quantity: int = 10,
    pn: str | None = None,
) -> tuple[str, int]:
    """A PLANNED (template given) or FLOATING release starting in ``start``."""
    admin = admin_of(client)
    part_number = pn or _unique("PN")
    work_order = _ok(
        admin.post(
            "/api/work-orders",
            json={"lines": [{"part_number": part_number, "requested_quantity": 500}]},
        ),
        201,
    )
    payload: dict[str, Any] = {
        "part_number": part_number,
        "quantity": quantity,
        "route_mode": "PLANNED" if template_id is not None else "FLOATING",
        "starting_area_id": start.area_id,
        "operation_id": start.operation_id,
        "confirm_active_quantity": True,
        "device_event_id": _event(),
    }
    if template_id is not None:
        payload["route_template_id"] = template_id
    released = _ok(
        admin.post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json=payload,
        ),
        201,
    )
    return part_number, int(released["quantity_flow_id"])


def _transfer(
    client: TestClient,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: int,
    *,
    action: str = "transfers",
    **extra: Any,
) -> Any:
    return client.post(
        f"/api/scan-stations/{target.station_id}/{action}",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": source.area_id,
            "target_area_id": target.area_id,
            "quantity": quantity,
            "device_event_id": _event(),
            **extra,
        },
    )


def _undo(client: TestClient, cell: _Cell, pn: str, reverses: str) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/undos",
        json={"part_number": pn, "reverses_device_event_id": reverses, "device_event_id": _event()},
    )


def _preview(client: TestClient, cell: _Cell, reverses: str) -> Any:
    return _ok(client.get(f"/api/scan-stations/{cell.station_id}/undo-preview/{reverses}"))


def _merge(client: TestClient, cell: _Cell, pn: str, flow_ids: list[int]) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/merges",
        json={"part_number": pn, "quantity_flow_ids": flow_ids, "device_event_id": _event()},
    )


def _body(
    expected: list[int],
    steps: list[dict[str, Any]],
    *,
    reason: str = _REASON,
    event: str | None = None,
) -> dict[str, Any]:
    return {
        "device_event_id": event or _event(),
        "expected_future_step_ids": expected,
        "steps": steps,
        "reason": reason,
    }


def _adjust(caller: TestClient, flow_id: int, body: dict[str, Any]) -> Any:
    return caller.post(f"/api/quantity-flows/{flow_id}/route-adjustments", json=body)


def _assigned(caller: TestClient, pn: str) -> Any:
    return caller.get("/api/tracking/assigned-routes", params={"part_number": pn})


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar()


def _route_id(engine: Engine, flow_id: int) -> int:
    return int(
        _scalar(engine, "SELECT assigned_route_id FROM quantity_flows WHERE id = :id", id=flow_id)
    )


def _steps(engine: Engine, flow_id: int) -> list[tuple[Any, ...]]:
    """The flow's snapshot rows (every column), in sequence order."""
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text(
                    "SELECT s.id, s.sequence, s.area_id, s.operation_id, s.expected_duration,"
                    " s.preferred_machine_id, s.instructions FROM assigned_route_steps s"
                    " JOIN quantity_flows f ON f.assigned_route_id = s.assigned_route_id"
                    " WHERE f.id = :id ORDER BY s.sequence"
                ),
                {"id": flow_id},
            )
        ]


def _step_ids(engine: Engine, flow_id: int) -> list[int]:
    return [int(row[0]) for row in _steps(engine, flow_id)]


def _counts(engine: Engine) -> list[int]:
    with engine.connect() as connection:
        return [
            int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in ("audit_events", "part_movements", "assigned_route_steps")
        ]


def _refused(
    engine: Engine,
    flow_id: int | None,
    send: Callable[[], Any],
    status: int,
    detail: str | None = None,
) -> Any:
    """``send`` is refused with ``status`` and writes nothing."""
    before = _counts(engine)
    steps = _steps(engine, flow_id) if flow_id is not None else None
    response = send()
    assert response.status_code == status, response.text
    if detail is not None:
        assert response.json()["detail"] == detail
    assert _counts(engine) == before
    if flow_id is not None:
        assert _steps(engine, flow_id) == steps
    return response


def _movements(engine: Engine, pn: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM part_movements WHERE part_number = :pn ORDER BY id"),
                {"pn": pn},
            ).mappings()
        ]


def _flow_row(engine: Engine, flow_id: int) -> dict[str, Any]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                sa.text("SELECT * FROM quantity_flows WHERE id = :id"), {"id": flow_id}
            )
            .mappings()
            .one()
        )


def _adjustment_rows(engine: Engine, route_id: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT * FROM audit_events WHERE entity_type = 'AssignedRoute'"
                    " AND entity_id = :id ORDER BY id"
                ),
                {"id": str(route_id)},
            ).mappings()
        ]


def _assert_reconciled(engine: Engine) -> None:
    report = reconciliation.run_reconciliation(engine)
    assert report.error is None, report.error
    failing = {check.id: check.findings for check in report.checks if check.findings}
    assert failing == {}


def _run(results: dict[str, Any], name: str, call: Callable[[], Any]) -> threading.Thread:
    def target() -> None:
        try:
            results[name] = call()
        except Exception as exc:  # noqa: BLE001 — collected for assertions
            results[name] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread) -> None:
    thread.join(timeout=30)
    assert not thread.is_alive()


def _row_lock(connection: sa.Connection, table: str, row_id: int) -> None:
    connection.execute(sa.text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id})


def _advisory(connection: sa.Connection, pn: str) -> None:
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"partflow:part-number:{pn}"},
    )


def _planned(client: TestClient, shop: _Shop, *keys: str, **kw: Any) -> tuple[str, int]:
    """A PLANNED release on a fresh template through ``keys`` (start = first)."""
    template = _template(client, *(shop[key] for key in keys))
    return _release(client, shop[keys[0]], template, **kw)


# ---------------------------------------------------------------------------
# AD-1 … AD-5 — the edit boundary and the adjusted route
# ---------------------------------------------------------------------------


def test_adjustment_replaces_only_the_future_steps(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-1."""
    adjuster = client_as(client, AR)
    template = _template(client, shop["A"], shop["B"], shop["C"], shop["D"])
    pn, flow_id = _release(client, shop["A"], template)
    _, other_flow = _release(client, shop["A"], template, pn=pn)
    route_id = _route_id(db_engine, flow_id)
    before_steps = _steps(db_engine, flow_id)
    other_steps = _steps(db_engine, other_flow)
    s1, s2, s3, s4 = (row[0] for row in before_steps)
    movements = _movements(db_engine, pn)
    flow_before = _flow_row(db_engine, flow_id)
    template_before = _ok(admin_of(client).get("/api/route-templates/management"))
    audit_before = _counts(db_engine)[0]

    response = _adjust(
        adjuster,
        flow_id,
        _body(
            [s2, s3, s4],
            [
                _step(shop["B"], expected_duration="PT1H30M", instructions="  Deburr  "),
                _step(shop["E"]),
            ],
        ),
    )
    body = _ok(response, 201)
    after = _steps(db_engine, flow_id)
    assert after[0] == before_steps[0]  # the same row, untouched
    assert [row[1] for row in after] == [1, 2, 3]
    assert not {row[0] for row in after[1:]} & {s2, s3, s4}
    assert after[1][2:] == (
        shop["B"].area_id,
        shop["B"].operation_id,
        datetime.timedelta(hours=1, minutes=30),
        None,
        "Deburr",
    )
    assert after[2][2:4] == (shop["E"].area_id, shop["E"].operation_id)
    assert body == {
        "device_event_id": body["device_event_id"],
        "quantity_flow_id": flow_id,
        "part_number": pn,
        "assigned_route_id": route_id,
        "kept_through_sequence": 1,
        "reason": _REASON,
        "steps": [
            {
                "id": row[0],
                "sequence": row[1],
                "area_id": row[2],
                "operation_id": row[3],
                "expected_duration": "PT1H30M" if row[1] == 2 else None,
                "preferred_machine_id": None,
                "instructions": row[6],
            }
            for row in after
        ],
    }

    [audit] = _adjustment_rows(db_engine, route_id)
    assert _counts(db_engine)[0] == audit_before + 1
    assert audit["event_type"] == "ROUTE_ADJUSTED"
    assert audit["entity_type"] == "AssignedRoute"
    assert audit["entity_id"] == str(route_id)
    assert audit["actor_user_id"] == adjuster.user_id
    assert audit["actor_reference"] is None
    assert [step["id"] for step in audit["before_data"]["steps"]] == [s1, s2, s3, s4]
    assert [step["id"] for step in audit["after_data"]["steps"]] == [row[0] for row in after]
    assert audit["after_data"]["steps"][1] == {
        "id": after[1][0],
        "sequence": 2,
        "area_id": shop["B"].area_id,
        "operation_id": shop["B"].operation_id,
        "expected_duration_seconds": 5400.0,
        "preferred_machine_id": None,
        "instructions": "Deburr",
    }
    block = audit["metadata"]["route_adjustment"]
    assert set(block) == {
        "device_event_id",
        "fingerprint",
        "quantity_flow_id",
        "part_number",
        "reason",
        "kept_through_sequence",
    }
    assert (block["quantity_flow_id"], block["part_number"], block["reason"]) == (
        flow_id,
        pn,
        _REASON,
    )
    assert block["kept_through_sequence"] == 1
    assert block["device_event_id"] == body["device_event_id"]
    # Nothing else changed: Movements, the flow row, the template, the other flow.
    assert _movements(db_engine, pn) == movements
    assert _flow_row(db_engine, flow_id) == flow_before
    assert _ok(admin_of(client).get("/api/route-templates/management")) == template_before
    assert _steps(db_engine, other_flow) == other_steps
    _assert_reconciled(db_engine)


def test_boundary_moves_with_progress(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """AD-2."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C", "D")
    s1, s2, s3, s4 = _step_ids(db_engine, flow_id)
    _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 10), 201)
    stale = _refused(
        db_engine,
        flow_id,
        lambda: _adjust(adjuster, flow_id, _body([s2, s3, s4], [_step(shop["E"])])),
        409,
        _rt4(flow_id),
    )
    assert stale.json()["route_changed"] is True
    body = _ok(_adjust(adjuster, flow_id, _body([s3, s4], [_step(shop["E"])])), 201)
    assert body["kept_through_sequence"] == 2
    assert _step_ids(db_engine, flow_id)[:2] == [s1, s2]


def test_an_undone_arrival_keeps_its_step(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-3 (OD-S6-2)."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C", "D")
    s1, s2, s3, s4 = _step_ids(db_engine, flow_id)
    moved = _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 10), 201)
    _ok(_undo(client, shop["B"], pn, moved["device_event_id"]), 201)

    [read] = _ok(_assigned(adjuster, pn))["flows"]
    assert read["kept_through_sequence"] == 2
    assert read["future_step_ids"] == [s3, s4]
    states = {step["id"]: (step["state"], step["locked"]) for step in read["steps"]}
    assert states == {
        s1: ("CURRENT", True),
        s2: ("FUTURE", True),
        s3: ("FUTURE", False),
        s4: ("FUTURE", False),
    }
    _refused(
        db_engine,
        flow_id,
        lambda: _adjust(adjuster, flow_id, _body([s2, s3, s4], [_step(shop["Y"])])),
        409,
        _rt4(flow_id),
    )
    _ok(_adjust(adjuster, flow_id, _body([s3, s4], [_step(shop["Y"])])), 201)
    # The locked, recorded step is still the expected next step — not Y.
    arrival = _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 10), 201)
    assert arrival["route_deviation"] is None
    assert arrival["assigned_route_step_id"] == s2
    # FK backstop: a referenced step can never be deleted.
    with db_engine.connect() as connection, pytest.raises(sa.exc.IntegrityError) as raised:
        connection.execute(sa.text("DELETE FROM assigned_route_steps WHERE id = :id"), {"id": s2})
    assert "fk_part_movements_assigned_route_step_id_assigned_route_steps" in str(raised.value)
    _assert_reconciled(db_engine)


def test_an_off_route_flow_gets_a_new_next_step(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-4."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C", "D")
    _, s2, s3, s4 = _step_ids(db_engine, flow_id)
    _ok(
        _transfer(
            client,
            shop["A"],
            shop["X"],
            flow_id,
            pn,
            10,
            confirm_route_deviation=True,
            route_deviation_reason="B is down",
        ),
        201,
    )
    [read] = _ok(_assigned(adjuster, pn))["flows"]
    assert read["off_route"] is True
    assert read["kept_through_sequence"] == 1
    _ok(_adjust(adjuster, flow_id, _body([s2, s3, s4], [_step(shop["Y"])])), 201)
    new_step = _step_ids(db_engine, flow_id)[1]
    arrival = _ok(_transfer(client, shop["X"], shop["Y"], flow_id, pn, 10), 201)
    assert arrival["route_deviation"] is None
    assert arrival["assigned_route_step_id"] == new_step
    [flow] = _ok(admin_of(client).get("/api/tracking/detail", params={"part_number": pn}))["flows"][
        "flows"
    ]
    assert flow["off_route"] is False
    [deviation] = flow["deviations"]
    assert deviation["expected_area"]["id"] == shop["B"].area_id


def test_extend_after_the_last_step_and_remove_every_future_step(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-5."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B")
    _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 10), 201)
    _ok(_adjust(adjuster, flow_id, _body([], [_step(shop["E"])])), 201)
    arrival = _ok(_transfer(client, shop["B"], shop["E"], flow_id, pn, 10), 201)
    assert arrival["route_deviation"] is None
    assert arrival["assigned_route_step_id"] == _step_ids(db_engine, flow_id)[2]

    pn2, flow2 = _planned(client, shop, "A", "B")
    _, s2 = _step_ids(db_engine, flow2)
    body = _ok(_adjust(adjuster, flow2, _body([s2], [])), 201)
    assert [step["sequence"] for step in body["steps"]] == [1]
    refused = _transfer(client, shop["A"], shop["B"], flow2, pn2, 10)
    assert refused.status_code == 409, refused.text
    assert refused.json()["route_deviation"]["expected_next_area_id"] is None


# ---------------------------------------------------------------------------
# AD-6 — refusals write nothing
# ---------------------------------------------------------------------------


def test_flow_refusals(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """AD-6: Floating, closed and missing flows."""
    adjuster = client_as(client, AR)
    _, floating = _release(client, shop["A"], None)
    _refused(
        db_engine, None, lambda: _adjust(adjuster, floating, _body([], [])), 409, _rt2(floating)
    )

    closed: list[int] = []
    # SPLIT (a partial transfer's source); REVERSED (an undone one's child).
    pn, split_source = _planned(client, shop, "A", "B")
    _ok(_transfer(client, shop["A"], shop["B"], split_source, pn, 4), 201)
    closed.append(split_source)
    pn, undone_source = _planned(client, shop, "A", "B")
    partial = _ok(_transfer(client, shop["A"], shop["B"], undone_source, pn, 4), 201)
    _ok(_undo(client, shop["B"], pn, partial["device_event_id"]), 201)
    closed.append(int(partial["quantity_flow_id"]))
    # MERGED sources.
    template = _template(client, shop["A"], shop["B"])
    pn, first = _release(client, shop["A"], template)
    _, second = _release(client, shop["A"], template, pn=pn)
    _ok(_merge(client, shop["A"], pn, [first, second]), 201)
    closed.append(first)
    # SCRAPPED.
    pn, scrapped = _planned(client, shop, "A", "B")
    _ok(
        client.post(
            f"/api/scan-stations/{shop['A'].station_id}/scraps",
            json={
                "part_number": pn,
                "quantity_flow_id": scrapped,
                "quantity": 10,
                "reason": "damaged",
                "device_event_id": _event(),
            },
        ),
        201,
    )
    closed.append(scrapped)
    # STOCKED (a Floating flow — the status is judged first).
    pn, stocked = _release(client, shop["A"], None)
    _ok(_transfer(client, shop["A"], shop["STOCK"], stocked, pn, 10, action="stockings"), 201)
    closed.append(stocked)
    statuses = {_flow_row(db_engine, flow_id)["status"] for flow_id in closed}
    assert statuses == {"SPLIT", "REVERSED", "MERGED", "SCRAPPED", "STOCKED"}
    for flow_id in closed:
        _refused(db_engine, None, _sender(adjuster, flow_id, _body([], [])), 409, _rt3(flow_id))

    for missing in (999_999_999, 2_147_483_648):
        _refused(
            db_engine,
            None,
            _sender(adjuster, missing, _body([], [])),
            404,
            f"Quantity Flow {missing} does not exist.",
        )


def test_input_refusals(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """AD-6: identical tail, reason, expected ids, request shape."""
    adjuster = client_as(client, AR)
    _, flow_id = _planned(client, shop, "A", "B", "C")
    _, s2, s3 = _step_ids(db_engine, flow_id)
    tail = [_step(shop["B"]), _step(shop["C"])]
    _refused(
        db_engine,
        flow_id,
        lambda: _adjust(adjuster, flow_id, _body([s2, s3], tail)),
        409,
        "These steps match the current route. Nothing was changed.",
    )
    for reason in ("", "   "):
        _refused(
            db_engine,
            flow_id,
            _sender(adjuster, flow_id, _body([s2, s3], [], reason=reason)),
            422,
            _RT6,
        )
    missing_reason = _body([s2, s3], [])
    del missing_reason["reason"]
    _refused(db_engine, flow_id, lambda: _adjust(adjuster, flow_id, missing_reason), 422)
    _refused(
        db_engine,
        flow_id,
        lambda: _adjust(adjuster, flow_id, _body([s2, s3], [], reason="re\x00work")),
        422,
        "The reason must be plain text.",
    )
    _refused(
        db_engine,
        flow_id,
        lambda: _adjust(adjuster, flow_id, _body([s2, s2], [])),
        422,
        _RT8,
    )
    for extra in ({"actor": "someone"}, {"actor_user_id": 1}, {"station_id": "ST"}):
        _refused(
            db_engine, flow_id, _sender(adjuster, flow_id, {**_body([s2, s3], []), **extra}), 422
        )


def test_step_refusals_name_the_absolute_step(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-6: every step check, numbered from the kept route (Step 2 = the
    first future step)."""
    admin = admin_of(client)
    adjuster = client_as(client, AR)
    _, flow_id = _planned(client, shop, "A", "B")
    _, s2 = _step_ids(db_engine, flow_id)
    inactive_name = _unique("AREA")
    inactive_area = _ok(
        admin.post("/api/areas", json={"department_id": shop.department_id, "name": inactive_name}),
        201,
    )
    inactive_operation = _ok(
        admin.post("/api/operations", json={"area_id": inactive_area["id"], "code": _unique("OP")}),
        201,
    )
    _ok(admin.patch(f"/api/areas/{inactive_area['id']}", json={"is_active": False}))
    inactive = {"area_id": inactive_area["id"], "operation_id": inactive_operation["id"]}
    stopped = _ok(
        admin.post("/api/operations", json={"area_id": shop["C"].area_id, "code": _unique("OP")}),
        201,
    )
    _ok(admin.patch(f"/api/operations/{stopped['id']}", json={"is_active": False}))
    retired = _ok(
        admin.post("/api/machines", json={"area_id": shop["B"].area_id, "name": "R"}), 201
    )
    _ok(admin.post(f"/api/machines/{retired['id']}/retire", json={"reason": "Worn out"}))
    foreign = _ok(
        admin.post("/api/machines", json={"area_id": shop["C"].area_id, "name": "F"}), 201
    )
    b_name = shop["B"].name
    cases: list[tuple[list[dict[str, Any]], int, str]] = [
        (
            [_step(shop["B"]), inactive],
            409,
            f"Step 3: Area '{inactive_name}' is inactive. Choose an active Area.",
        ),
        (
            [{"area_id": shop["B"].area_id, "operation_id": shop["C"].operation_id}],
            422,
            f"Step 2: Operation '{_op_code(db_engine, shop['C'])}' does not belong to"
            f" Area '{b_name}'.",
        ),
        (
            [{"area_id": shop["C"].area_id, "operation_id": stopped["id"]}],
            409,
            f"Step 2: Operation '{stopped['code']}' is inactive. Choose an Operation the Area"
            " still offers.",
        ),
        (
            [_step(shop["B"], preferred_machine_id=retired["id"])],
            409,
            f"Step 2: Machine '{retired['name']}' is retired. Choose an active Machine or no"
            " preferred Machine.",
        ),
        (
            [_step(shop["B"], preferred_machine_id=foreign["id"])],
            422,
            f"Step 2: Machine '{foreign['name']}' is not in Area '{b_name}'. Choose one of the"
            " Area's active Machines or no preferred Machine.",
        ),
        (
            [_step(shop["B"]), {"area_id": shop["C"].area_id, "operation_id": None}],
            422,
            "Step 3 needs an Operation.",
        ),
        (
            [_step(shop["B"], expected_duration="PT0S")],
            422,
            "Step 2: the estimated time must be longer than zero.",
        ),
        (
            [_step(shop["B"], instructions="bad\x00text")],
            422,
            "Step 2: the instructions must be text.",
        ),
    ]
    for steps, status, detail in cases:
        _refused(db_engine, flow_id, _sender(adjuster, flow_id, _body([s2], steps)), status, detail)


def _sender(caller: TestClient, flow_id: int, body: dict[str, Any]) -> Callable[[], Any]:
    return lambda: _adjust(caller, flow_id, body)


def _op_code(engine: Engine, cell: _Cell) -> str:
    return str(_scalar(engine, "SELECT code FROM operations WHERE id = :id", id=cell.operation_id))


# ---------------------------------------------------------------------------
# AD-7 … AD-9 — idempotency and races
# ---------------------------------------------------------------------------


def test_idempotency_and_actor_aware_replay(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-7."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C")
    _, s2, s3 = _step_ids(db_engine, flow_id)
    body = _body([s2, s3], [_step(shop["E"])])
    first = _ok(_adjust(adjuster, flow_id, body), 201)
    route_id = _route_id(db_engine, flow_id)

    def replayed(caller: TestClient) -> None:
        response = _refused(db_engine, None, lambda: _adjust(caller, flow_id, body), 200)
        assert response.json() == first

    replayed(adjuster)
    replayed(another_session(client, adjuster))
    # A later adjustment changes the route; the replay still answers the original.
    [new_step] = _ok(_assigned(adjuster, pn))["flows"][0]["future_step_ids"]
    _ok(_adjust(adjuster, flow_id, _body([new_step], [_step(shop["D"])])), 201)
    replayed(adjuster)
    # Mismatched reuse: another body, or the same id against another flow.
    _refused(
        db_engine,
        None,
        lambda: _adjust(adjuster, flow_id, {**body, "reason": "something else"}),
        409,
        _RT10,
    )
    _, other_flow = _planned(client, shop, "A", "B")
    _refused(
        db_engine,
        None,
        lambda: _adjust(adjuster, other_flow, {**body, "expected_future_step_ids": []}),
        409,
        _RT10,
    )
    other = _refused(
        db_engine, None, lambda: _adjust(client_as(client, AR), flow_id, body), 409, _R1
    )
    assert other.json() == {"detail": _R1, "recorded_by_another_user": True}
    # The flow closes: the replay still answers the original.
    moved = _transfer(client, shop["A"], shop["STOCK"], flow_id, pn, 10, action="stockings")
    assert moved.status_code in (201, 409), moved.text
    if moved.status_code == 409:
        _ok(
            _transfer(
                client,
                shop["A"],
                shop["STOCK"],
                flow_id,
                pn,
                10,
                action="stockings",
                confirm_route_deviation=True,
                route_deviation_reason="done early",
            ),
            201,
        )
    assert _flow_row(db_engine, flow_id)["status"] == "STOCKED"
    replayed(adjuster)
    assert len(_adjustment_rows(db_engine, route_id)) == 2


def _race(callers: list[tuple[TestClient, int, dict[str, Any]]]) -> list[Any]:
    barrier = threading.Barrier(len(callers))
    results: dict[str, Any] = {}

    def send(caller: TestClient, flow_id: int, body: dict[str, Any]) -> Callable[[], Any]:
        def call() -> Any:
            barrier.wait(timeout=10)
            return _adjust(caller, flow_id, body)

        return call

    threads = [_run(results, str(index), send(*caller)) for index, caller in enumerate(callers)]
    for thread in threads:
        _finish(thread)
    return [results[str(index)] for index in range(len(callers))]


def test_two_adjustments_of_one_flow_race(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-8."""
    adjuster = client_as(client, AR)
    _, flow_id = _planned(client, shop, "A", "B", "C")
    _, s2, s3 = _step_ids(db_engine, flow_id)
    responses = _race(
        [
            (adjuster, flow_id, _body([s2, s3], [_step(shop["E"])])),
            (adjuster, flow_id, _body([s2, s3], [_step(shop["Y"])])),
        ]
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    [loser] = [response for response in responses if response.status_code == 409]
    assert loser.json() == {"detail": _rt4(flow_id), "route_changed": True}
    assert len(_adjustment_rows(db_engine, _route_id(db_engine, flow_id))) == 1


def test_one_device_event_id_on_two_flows_races(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-9: two PNs (two PN locks) — the UNIQUE index decides."""
    adjuster = client_as(client, AR)
    _, first = _planned(client, shop, "A", "B")
    _, second = _planned(client, shop, "A", "B")
    event = _event()
    responses = _race(
        [
            (adjuster, first, _body([_step_ids(db_engine, first)[1]], [], event=event)),
            (adjuster, second, _body([_step_ids(db_engine, second)[1]], [], event=event)),
        ]
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    [loser] = [response for response in responses if response.status_code == 409]
    assert loser.json()["detail"] == _RT10
    assert (
        _scalar(
            db_engine,
            "SELECT count(*) FROM audit_events WHERE metadata['route_adjustment']"
            " ->> 'device_event_id' = :event",
            event=event,
        )
        == 1
    )


# ---------------------------------------------------------------------------
# AD-10 … AD-12 — serialization with production commands and lock order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("first", ["transfer", "adjustment"])
def test_adjustment_and_transfer_of_one_flow_serialize(
    client: TestClient, shop: _Shop, db_engine: Engine, first: str
) -> None:
    """AD-10 (a), (b)."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C", "D")
    _, s2, s3, s4 = _step_ids(db_engine, flow_id)
    calls: dict[str, Callable[[], Any]] = {
        "transfer": lambda: _transfer(client, shop["A"], shop["B"], flow_id, pn, 10),
        "adjustment": lambda: _adjust(
            adjuster,
            flow_id,
            _body([s2, s3, s4], [_step(shop["Y"]), _step(shop["C"]), _step(shop["D"])]),
        ),
    }
    second = "adjustment" if first == "transfer" else "transfer"
    results: dict[str, Any] = {}
    with db_engine.connect() as holder:
        transaction = holder.begin()
        _row_lock(holder, "quantity_flows", flow_id)
        leading = _run(results, first, calls[first])
        _assert_blocked(leading)
        trailing = _run(results, second, calls[second])
        _assert_blocked(trailing)
        transaction.commit()
    _finish(leading)
    _finish(trailing)
    if first == "transfer":
        assert results["transfer"].status_code == 201, results["transfer"].text
        assert results["transfer"].json()["assigned_route_step_id"] == s2
        assert results["adjustment"].status_code == 409
        assert results["adjustment"].json()["route_changed"] is True
    else:
        assert results["adjustment"].status_code == 201, results["adjustment"].text
        refused = results["transfer"]
        assert refused.status_code == 409, refused.text
        assert refused.json()["confirmation_required"] is True
        assert refused.json()["route_deviation"]["expected_next_area_id"] == shop["Y"].area_id


@pytest.mark.parametrize("new_area", ["X", "Y"])
def test_a_confirmed_deviation_is_judged_at_commit(
    client: TestClient, shop: _Shop, db_engine: Engine, new_area: str
) -> None:
    """AD-10 (c), OD-S6-20: (c1) the adjustment now expects the Area → on
    route, the reason recorded nowhere; (c2) still a deviation → recorded
    against the route at commit."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C")
    _, s2, s3 = _step_ids(db_engine, flow_id)
    prompt = _transfer(client, shop["A"], shop["X"], flow_id, pn, 10)
    assert prompt.status_code == 409, prompt.text
    assert prompt.json()["route_deviation"]["expected_next_step_id"] == s2
    _ok(_adjust(adjuster, flow_id, _body([s2, s3], [_step(shop[new_area]), _step(shop["C"])])), 201)
    new_step = _step_ids(db_engine, flow_id)[1]
    confirmed = _ok(
        _transfer(
            client,
            shop["A"],
            shop["X"],
            flow_id,
            pn,
            10,
            confirm_route_deviation=True,
            route_deviation_reason="B is down",
        ),
        201,
    )
    [movement] = [row for row in _movements(db_engine, pn) if row["movement_type"] == "TRANSFERRED"]
    if new_area == "X":
        assert confirmed["route_deviation"] is None
        assert confirmed["assigned_route_step_id"] == new_step
        assert "route_deviation" not in (movement["metadata"] or {})
        assert "B is down" not in str(movement)
    else:
        assert confirmed["assigned_route_step_id"] is None
        recorded = movement["metadata"]["route_deviation"]
        assert recorded["expected_next_step_id"] == new_step
        assert recorded["expected_next_area_id"] == shop["Y"].area_id
        assert recorded["reason"] == "B is down"


@pytest.mark.parametrize("first", ["split", "adjustment"])
def test_a_partial_transfer_copies_one_whole_route(
    client: TestClient, shop: _Shop, db_engine: Engine, first: str
) -> None:
    """AD-11: the children copy the parent's route as committed by the winner."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C", "D")
    _, s2, s3, s4 = _step_ids(db_engine, flow_id)
    old_route = [row[1:] for row in _steps(db_engine, flow_id)]
    calls: dict[str, Callable[[], Any]] = {
        "split": lambda: _transfer(client, shop["A"], shop["B"], flow_id, pn, 4),
        "adjustment": lambda: _adjust(
            adjuster, flow_id, _body([s2, s3, s4], [_step(shop["B"]), _step(shop["E"])])
        ),
    }
    second = "adjustment" if first == "split" else "split"
    results: dict[str, Any] = {}
    with db_engine.connect() as holder:
        transaction = holder.begin()
        _row_lock(holder, "quantity_flows", flow_id)
        leading = _run(results, first, calls[first])
        _assert_blocked(leading)
        trailing = _run(results, second, calls[second])
        _assert_blocked(trailing)
        transaction.commit()
    _finish(leading)
    _finish(trailing)
    split = results["split"]
    assert split.status_code == 201, split.text
    children = [
        int(split.json()["quantity_flow_id"]),
        int(split.json()["remainder_quantity_flow_id"]),
    ]
    if first == "split":
        assert results["adjustment"].status_code == 409
        assert results["adjustment"].json()["detail"] == _rt3(flow_id)
        expected = old_route
    else:
        assert results["adjustment"].status_code == 201
        expected = [row[1:] for row in _steps(db_engine, flow_id)]
        assert expected != old_route
    for child in children:
        assert [row[1:] for row in _steps(db_engine, child)] == expected
    # The moved child's recorded step maps by sequence (step 2 in Area B).
    moved = _steps(db_engine, children[0])
    assert split.json()["assigned_route_step_id"] == moved[1][0]


def test_lock_order_never_deadlocks(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """AD-12: the adjustment waits on a held Area holding its PN and flow
    locks; a transfer of the flow queues; another PN's release avoiding
    the Area completes; nothing deadlocks."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C")
    _, s2, s3 = _step_ids(db_engine, flow_id)
    other_template = _template(client, shop["A"], shop["B"])
    results: dict[str, Any] = {}
    with db_engine.connect() as holder:
        transaction = holder.begin()
        _row_lock(holder, "areas", shop["W"].area_id)
        adjustment = _run(
            results,
            "adjustment",
            lambda: _adjust(
                adjuster, flow_id, _body([s2, s3], [_step(shop["B"]), _step(shop["W"])])
            ),
        )
        _assert_blocked(adjustment)
        transfer = _run(
            results, "transfer", lambda: _transfer(client, shop["A"], shop["B"], flow_id, pn, 10)
        )
        _assert_blocked(transfer)
        released = _release(client, shop["A"], other_template)
        assert released[1] > 0
        transaction.commit()
    _finish(adjustment)
    _finish(transfer)
    assert results["adjustment"].status_code == 201, results["adjustment"].text
    assert results["transfer"].status_code == 201, results["transfer"].text
    assert results["transfer"].json()["assigned_route_step_id"] == _step_ids(db_engine, flow_id)[1]


def test_crossing_lock_orders_never_deadlock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-12 crossing case, 20 rounds: an adjustment adding Areas [Q, P]
    (descending ids) ‖ a release of another PN starting in Q through P ‖
    an Undo restoring quantity into P."""
    adjuster = client_as(client, AR)
    low, high = sorted((shop["P"], shop["Q"]), key=lambda cell: cell.area_id)
    crossing_template = _template(client, high, low)
    for _ in range(20):
        _crossing_round(client, shop, db_engine, adjuster, low, high, crossing_template)


def _crossing_round(
    client: TestClient,
    shop: _Shop,
    db_engine: Engine,
    adjuster: TestClient,
    low: _Cell,
    high: _Cell,
    crossing_template: int,
) -> None:
    _, flow_id = _planned(client, shop, "A", "B")
    _, s2 = _step_ids(db_engine, flow_id)
    undo_pn, undo_flow = _release(client, low, None)
    moved = _ok(_transfer(client, low, shop["E"], undo_flow, undo_pn, 10), 201)
    release_pn = _unique("PN")
    work_order = _ok(
        admin_of(client).post(
            "/api/work-orders",
            json={"lines": [{"part_number": release_pn, "requested_quantity": 5}]},
        ),
        201,
    )
    calls: list[Callable[[], Any]] = [
        lambda: _adjust(adjuster, flow_id, _body([s2], [_step(high), _step(low)])),
        lambda: admin_of(client).post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": release_pn,
                "quantity": 5,
                "route_mode": "PLANNED",
                "route_template_id": crossing_template,
                "starting_area_id": high.area_id,
                "operation_id": high.operation_id,
                "confirm_active_quantity": True,
                "device_event_id": _event(),
            },
        ),
        lambda: _undo(client, shop["E"], undo_pn, moved["device_event_id"]),
    ]
    barrier = threading.Barrier(len(calls))
    results: dict[str, Any] = {}

    def gated(call: Callable[[], Any]) -> Callable[[], Any]:
        def run() -> Any:
            barrier.wait(timeout=10)
            return call()

        return run

    threads = [_run(results, str(index), gated(call)) for index, call in enumerate(calls)]
    for thread in threads:
        _finish(thread)
    for index in range(len(calls)):
        response = results[str(index)]
        assert not isinstance(response, Exception), response
        assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# AD-13 — authorization precedes every lock
# ---------------------------------------------------------------------------


def test_authorization_precedes_every_lock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-13."""
    pn, flow_id = _planned(client, shop, "A", "B")
    _, s2 = _step_ids(db_engine, flow_id)
    body = _body([s2], [])
    outsider = client_as(client, *(set(ALL_PERMISSIONS) - {AR}))
    pending = client_as(client, *ALL_PERMISSIONS, temporary_password=True)
    calls: list[Callable[[TestClient], Any]] = [
        lambda caller: _adjust(caller, flow_id, body),
        lambda caller: _assigned(caller, pn),
    ]
    with db_engine.connect() as holder:
        transaction = holder.begin()
        _advisory(holder, pn)
        _row_lock(holder, "quantity_flows", flow_id)
        for call in calls:
            started = time.monotonic()
            anonymous = _refused(
                db_engine, flow_id, functools.partial(call, anonymous_client(client)), 401
            )
            assert anonymous.json()["authentication_required"] is True
            denied = _refused(db_engine, flow_id, functools.partial(call, outsider), 403)
            assert denied.json() == {
                "detail": _A2,
                "permission_denied": True,
                "required_permissions": ["ASSIGN_ROUTES"],
            }
            changing = _refused(db_engine, flow_id, functools.partial(call, pending), 403)
            assert changing.json()["password_change_required"] is True
            assert time.monotonic() - started < 2
        # Anonymous with an invalid body: 401, never 422.
        assert (
            anonymous_client(client)
            .post(f"/api/quantity-flows/{flow_id}/route-adjustments", json={"x": 1})
            .status_code
            == 401
        )
        transaction.rollback()


# ---------------------------------------------------------------------------
# AD-14 / AD-15 — the editor read and Tracking
# ---------------------------------------------------------------------------


def test_editor_read(client: TestClient, shop: _Shop, db_engine: Engine) -> None:
    """AD-14."""
    admin = admin_of(client)
    reader = client_as(client, AR)
    machine = _ok(
        admin.post("/api/machines", json={"area_id": shop["B"].area_id, "name": "M"}), 201
    )
    template = _ok(
        admin.post(
            "/api/route-templates",
            json={
                "name": _unique("ROUTE"),
                "description": None,
                "steps": [
                    _step(shop["A"]),
                    _step(
                        shop["B"],
                        preferred_machine_id=machine["id"],
                        instructions="Use the soft jaws",
                        expected_duration="PT45M",
                    ),
                ],
            },
        ),
        201,
    )
    pn, older = _release(client, shop["A"], int(template["id"]))
    _, newer = _release(client, shop["A"], int(template["id"]), pn=pn)
    _release(client, shop["A"], None, pn=pn)  # FLOATING: never listed
    _ok(admin.post(f"/api/machines/{machine['id']}/retire", json={"reason": "Worn out"}))

    body = _ok(_assigned(reader, pn.lower()))
    assert body["part_number"] == pn
    assert [flow["quantity_flow_id"] for flow in body["flows"]] == [newer, older]
    flow = body["flows"][0]
    s1, s2 = _step_ids(db_engine, newer)
    assert flow["quantity"] == 10
    assert flow["kept_through_sequence"] == 1
    assert flow["future_step_ids"] == [s2]
    assert flow["off_route"] is False
    assert flow["source_template"] == {"id": template["id"], "name": template["name"]}
    assert flow["position"]["area"]["id"] == shop["A"].area_id
    assert flow["steps"][0]["id"] == s1
    assert (flow["steps"][0]["state"], flow["steps"][0]["locked"]) == ("CURRENT", True)
    assert flow["steps"][1] == {
        "id": s2,
        "sequence": 2,
        "area": {
            "id": shop["B"].area_id,
            "name": shop["B"].name,
            "color": flow["steps"][1]["area"]["color"],
            "is_terminal": False,
        },
        "operation": flow["steps"][1]["operation"],
        "expected_duration": "PT45M",
        "preferred_machine": {"id": machine["id"], "name": machine["name"]},
        "instructions": "Use the soft jaws",
        "state": "FUTURE",
        "locked": False,
    }
    assert flow["steps"][1]["operation"]["id"] == shop["B"].operation_id

    floating_pn, _ = _release(client, shop["A"], None)
    assert _ok(_assigned(reader, floating_pn)) == {"part_number": floating_pn, "flows": []}
    assert _assigned(reader, _unique("PN")).status_code == 404
    assert _assigned(reader, "has space").status_code == 422


def _detail(client: TestClient, pn: str) -> dict[str, Any]:
    body: dict[str, Any] = _ok(
        admin_of(client).get("/api/tracking/detail", params={"part_number": pn})
    )
    return body


def _board_row(client: TestClient, shop: _Shop, pn: str) -> Any:
    board = _ok(client.get("/api/production-board", params={"department_id": shop.department_id}))
    return next(row for row in board["rows"] if row["part_number"] == pn)


def test_tracking_shows_every_adjustment(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-15 (and OD-S6-6)."""
    adjuster = client_as(client, AR)
    template = _template(client, shop["A"], shop["B"], shop["C"], first_duration="PT2H")
    pn, flow_id = _release(client, shop["A"], template)
    _, sibling = _release(client, shop["A"], template, pn=pn)
    other_pn, other_flow = _release(client, shop["A"], template)
    before = _detail(client, pn)
    board_before = _board_row(client, shop, pn)
    area_board_before = _ok(
        admin_of(client).get("/api/area-board", params={"department_id": shop.department_id})
    )
    assert before["route_adjustment_total"] == 0

    for round_ in range(7):
        future = _ok(_assigned(adjuster, pn))["flows"]
        [current] = [flow for flow in future if flow["quantity_flow_id"] == flow_id]
        # The last round (6) leaves step 2 in Area E.
        target = shop["E"] if round_ % 2 == 0 else shop["D"]
        _ok(
            _adjust(
                adjuster,
                flow_id,
                _body(current["future_step_ids"], [_step(target)], reason=f"round {round_}"),
            ),
            201,
        )
    _ok(_adjust(adjuster, sibling, _body(_step_ids(db_engine, sibling)[1:], [])), 201)
    _ok(_adjust(adjuster, other_flow, _body(_step_ids(db_engine, other_flow)[1:], [])), 201)

    after = _detail(client, pn)
    assert after["route_adjustment_total"] == 8
    assert _detail(client, other_pn)["route_adjustment_total"] == 1
    flows = {flow["id"]: flow for flow in after["flows"]["flows"]}
    notes = flows[flow_id]["route_adjustments"]
    assert [note["reason"] for note in notes] == [f"round {index}" for index in range(7)]
    ids = [note["audit_event_id"] for note in notes]
    assert ids == sorted(ids)
    assert notes[0]["kept_through_sequence"] == 1
    assert notes[0]["actor_user"]["id"] == adjuster.user_id
    assert set(notes[0]["actor_user"]) == {"id", "display_name", "avatar_updated_at"}
    assert [step["state"] for step in flows[flow_id]["route_steps"]] == ["CURRENT", "FUTURE"]
    assert flows[flow_id]["route_steps"][1]["area"]["id"] == shop["E"].area_id
    assert len(flows[sibling]["route_adjustments"]) == 1
    # The current step is untouched: monitoring is identical.
    position_before = next(f for f in before["flows"]["flows"] if f["id"] == flow_id)["position"]
    assert flows[flow_id]["position"] == position_before
    assert position_before["expected_by"] is not None
    assert after["locations"] == before["locations"]
    assert _board_row(client, shop, pn) == board_before
    assert (
        _ok(admin_of(client).get("/api/area-board", params={"department_id": shop.department_id}))
        == area_board_before
    )

    # A split child created after the adjustment carries no notes of its
    # own; the closed parent keeps its notes.
    partial = _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 4), 409)
    assert partial["route_deviation"]["expected_next_area_id"] == shop["E"].area_id
    split = _ok(_transfer(client, shop["A"], shop["E"], flow_id, pn, 4), 201)
    detail = _detail(client, pn)
    flows = {flow["id"]: flow for flow in detail["flows"]["flows"]}
    assert flows[flow_id]["status"] == "SPLIT"
    assert len(flows[flow_id]["route_adjustments"]) == 7
    assert flows[int(split["quantity_flow_id"])]["route_adjustments"] == []
    assert flows[int(split["remainder_quantity_flow_id"])]["route_adjustments"] == []


# ---------------------------------------------------------------------------
# AD-16 — merge compatibility
# ---------------------------------------------------------------------------


def test_merge_compatibility_follows_the_adjusted_route(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-16: split siblings in one Area merge only with one route."""
    adjuster = client_as(client, AR)
    pn, flow_id = _planned(client, shop, "A", "B", "C", "D")
    split = _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 4), 201)
    moved, remainder = int(split["quantity_flow_id"]), int(split["remainder_quantity_flow_id"])
    _ok(_transfer(client, shop["A"], shop["B"], remainder, pn, 6), 201)
    tail = [_step(shop["Y"])]
    _ok(_adjust(adjuster, moved, _body(_step_ids(db_engine, moved)[2:], tail)), 201)
    refused = _merge(client, shop["B"], pn, [moved, remainder])
    assert refused.status_code in (409, 422), refused.text
    assert "route context differs" in refused.json()["detail"]
    _ok(_adjust(adjuster, remainder, _body(_step_ids(db_engine, remainder)[2:], tail)), 201)
    _ok(_merge(client, shop["B"], pn, [moved, remainder]), 201)


# ---------------------------------------------------------------------------
# AD-17 — station Undo after an adjustment (OD-S6-19)
# ---------------------------------------------------------------------------


def _undo_refused(
    client: TestClient, engine: Engine, cell: _Cell, pn: str, event: str, adjusted: int
) -> None:
    preview = _preview(client, cell, event)
    assert preview["eligible"] is False
    assert preview["ineligible_reason"] == _ura(adjusted)
    flows = _scalar(
        engine,
        "SELECT string_agg(id || ':' || status || ':' || current_area_id, ',' ORDER BY id)"
        " FROM quantity_flows WHERE part_number = :pn",
        pn=pn,
    )
    steps = _steps(engine, adjusted)
    _refused(
        engine,
        None,
        lambda: _undo(client, cell, pn, event),
        409,
        f"{_ura(adjusted)} Nothing was reversed.",
    )
    assert (
        _scalar(
            engine,
            "SELECT string_agg(id || ':' || status || ':' || current_area_id, ',' ORDER BY id)"
            " FROM quantity_flows WHERE part_number = :pn",
            pn=pn,
        )
        == flows
    )
    assert _steps(engine, adjusted) == steps


def test_undo_of_a_command_that_created_an_adjusted_flow_is_refused(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """AD-17 (a) partial transfer, (b) partial scrap, (c) merge; controls."""
    adjuster = client_as(client, AR)

    # (a) A partial transfer creates the moved child and the remainder.
    pn, flow_id = _planned(client, shop, "A", "B", "C")
    partial = _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 4), 201)
    moved = int(partial["quantity_flow_id"])
    assert _preview(client, shop["B"], partial["device_event_id"])["eligible"] is True
    _ok(_adjust(adjuster, moved, _body(_step_ids(db_engine, moved)[2:], [_step(shop["E"])])), 201)
    _undo_refused(client, db_engine, shop["B"], pn, partial["device_event_id"], moved)

    # (b) A partial scrap splits; the remainder is created by the command.
    pn, flow_id = _planned(client, shop, "A", "B")
    scrap = _ok(
        client.post(
            f"/api/scan-stations/{shop['A'].station_id}/scraps",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "quantity": 3,
                "reason": "damaged",
                "device_event_id": _event(),
            },
        ),
        201,
    )
    remainder = int(scrap["remainder_quantity_flow_id"])
    assert _preview(client, shop["A"], scrap["device_event_id"])["eligible"] is True
    _ok(_adjust(adjuster, remainder, _body(_step_ids(db_engine, remainder)[1:], [])), 201)
    _undo_refused(client, db_engine, shop["A"], pn, scrap["device_event_id"], remainder)

    # (c) A merge creates its result.
    template = _template(client, shop["A"], shop["B"])
    pn, first = _release(client, shop["A"], template)
    _, second = _release(client, shop["A"], template, pn=pn)
    merged = _ok(_merge(client, shop["A"], pn, [first, second]), 201)
    result = int(merged["quantity_flow_id"])
    assert _preview(client, shop["A"], merged["device_event_id"])["eligible"] is True
    _ok(_adjust(adjuster, result, _body(_step_ids(db_engine, result)[1:], [])), 201)
    _undo_refused(client, db_engine, shop["A"], pn, merged["device_event_id"], result)

    # Control: an adjusted flow the command did NOT create — the Undo
    # succeeds, the adjusted tail stays and the undone step stays locked.
    pn, flow_id = _planned(client, shop, "A", "B", "C")
    s1, s2, s3 = _step_ids(db_engine, flow_id)
    moved_whole = _ok(_transfer(client, shop["A"], shop["B"], flow_id, pn, 10), 201)
    _ok(_adjust(adjuster, flow_id, _body([s3], [_step(shop["E"])])), 201)
    adjusted = _steps(db_engine, flow_id)
    assert _preview(client, shop["B"], moved_whole["device_event_id"])["eligible"] is True
    _ok(_undo(client, shop["B"], pn, moved_whole["device_event_id"]), 201)
    assert _steps(db_engine, flow_id) == adjusted
    [read] = _ok(_assigned(adjuster, pn))["flows"]
    states = {step["id"]: (step["state"], step["locked"]) for step in read["steps"]}
    assert states[s1] == ("CURRENT", True)
    assert states[s2] == ("FUTURE", True)
    _assert_reconciled(db_engine)
