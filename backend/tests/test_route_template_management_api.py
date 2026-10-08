"""Integration tests for Planned Routes management (Phase 13 slice 8).

`/api/route-templates` management surface (GUI_DESIGN §13; PROJECT_PROFILE
§8.8–§8.10, §21, §28; owner decision OD-11). Covered:

- create, full replacement, archive, delete and usage with the exact
  wire shape, refusal copy and audit snapshots (`RouteTemplate`);
- the identical-PUT no-op judged before any reference check, and stale
  references refused on the next effective save;
- the legacy step without an Operation (OD-11);
- the snapshot copy of the preferred Machine in release, receipt and
  split, merge compatibility over it, and the independence of every
  snapshot from later template edits, Machine retirement and archive;
- serialization of release / receipt with edit, archive and delete
  (template FOR SHARE before the Area), the writer's reference lock
  order (Machines → Areas → Operations, FOR KEY SHARE, ascending), no
  Machine lock through the snapshot, independence from the PN lock,
  and atomicity with the audit row.

The API commits real transactions, so tests isolate through unique
names, PNs and Areas; the module database is dropped afterwards.
"""

import datetime
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
    audit,
    intake,
    part_numbers,
    production_release,
    route_templates,
    transfers,
    work_orders,
)
from app.application.errors import ConflictError, InvalidInputError
from app.application.route_templates import RouteStepInput
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client
from tests.conftest import owner_connection

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_route_template_management_api"
_DB_URL_ENV = "DATABASE_URL"
_MISSING_ID = 99_999_999


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
    """Application client wired to the temporary database."""
    original_url = os.environ[_DB_URL_ENV]
    os.environ[_DB_URL_ENV] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield station_device_client(test_client)
    finally:
        os.environ[_DB_URL_ENV] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    """Direct database access for state verification and lock holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    response = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "RT-", "digits": 4},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _create_area(client: TestClient, **overrides: Any) -> dict[str, Any]:
    department = admin_of(client).post("/api/departments", json={"name": _unique("DEPT")})
    assert department.status_code == 201, department.text
    response = admin_of(client).post(
        "/api/areas",
        json={"department_id": department.json()["id"], "name": _unique("AREA"), **overrides},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_operation(client: TestClient, area_id: int) -> dict[str, Any]:
    response = admin_of(client).post(
        "/api/operations", json={"area_id": area_id, "code": _unique("OP")}
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_machine(client: TestClient, area_id: int) -> dict[str, Any]:
    response = admin_of(client).post(
        "/api/machines", json={"area_id": area_id, "name": _unique("Lathe")}
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


class _Cell:
    """An Area with one Operation, optionally a Scan Station and Machines."""

    def __init__(
        self,
        client: TestClient,
        *,
        machine_count: int = 0,
        station: bool = True,
        is_terminal: bool = False,
    ) -> None:
        self.area = _create_area(client, is_terminal=is_terminal)
        self.area_id = int(self.area["id"])
        self.name = str(self.area["name"])
        self.operation = _create_operation(client, self.area_id)
        self.operation_id = int(self.operation["id"])
        self.station_id: str | None = None
        if station:
            response = admin_of(client).post(
                "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
            )
            assert response.status_code == 201, response.text
            self.station_id = str(response.json()["station_id"])
        self.machines = [_create_machine(client, self.area_id) for _ in range(machine_count)]
        self.machine_ids = [int(machine["id"]) for machine in self.machines]


def _step(
    cell: _Cell,
    *,
    machine_id: int | None = None,
    duration: str | None = None,
    instructions: str | None = None,
    operation_id: int | None = None,
) -> dict[str, Any]:
    return {
        "area_id": cell.area_id,
        "operation_id": operation_id if operation_id is not None else cell.operation_id,
        "expected_duration": duration,
        "preferred_machine_id": machine_id,
        "instructions": instructions,
    }


def _create(
    client: TestClient,
    steps: list[dict[str, Any]],
    *,
    name: str | None = None,
    description: str | None = None,
) -> Any:
    return admin_of(client).post(
        "/api/route-templates",
        json={"name": name or _unique("ROUTE"), "description": description, "steps": steps},
    )


def _created(client: TestClient, steps: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    response = _create(client, steps, **kw)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _write_body(record: dict[str, Any]) -> dict[str, Any]:
    """The write request that reproduces a management record."""
    return {
        "name": record["name"],
        "description": record["description"],
        "steps": [
            {
                "area_id": step["area_id"],
                "operation_id": step["operation_id"],
                "expected_duration": step["expected_duration"],
                "preferred_machine_id": step["preferred_machine_id"],
                "instructions": step["instructions"],
            }
            for step in record["steps"]
        ],
    }


def _put(client: TestClient, template_id: int, body: dict[str, Any]) -> Any:
    return admin_of(client).put(f"/api/route-templates/{template_id}", json=body)


def _records(client: TestClient) -> dict[int, dict[str, Any]]:
    response = admin_of(client).get("/api/route-templates/management")
    assert response.status_code == 200, response.text
    return {int(entry["id"]): entry for entry in response.json()}


def _record(client: TestClient, template_id: int) -> dict[str, Any]:
    return _records(client)[template_id]


def _demand(client: TestClient, part_number: str) -> tuple[int, int]:
    response = admin_of(client).post(
        "/api/work-orders",
        json={"lines": [{"part_number": part_number, "requested_quantity": 500}]},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"]), int(response.json()["demands"][0]["id"])


def _release_response(
    client: TestClient,
    cell: _Cell,
    template_id: int,
    *,
    part_number: str | None = None,
    quantity: int = 10,
) -> tuple[Any, str]:
    pn = part_number or _unique("PN")
    work_order_id, demand_id = _demand(client, pn)
    response = admin_of(client).post(
        f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": quantity,
            "route_mode": "PLANNED",
            "route_template_id": template_id,
            "starting_area_id": cell.area_id,
            "operation_id": cell.operation_id,
            "confirm_active_quantity": part_number is not None,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    return response, pn


def _release(
    client: TestClient,
    cell: _Cell,
    template_id: int,
    *,
    part_number: str | None = None,
    quantity: int = 10,
) -> tuple[int, str]:
    response, pn = _release_response(
        client, cell, template_id, part_number=part_number, quantity=quantity
    )
    assert response.status_code == 201, response.text
    return int(response.json()["quantity_flow_id"]), pn


def _receive(client: TestClient, cell: _Cell, template_id: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/receipts",
        json={
            "part_number": _unique("PN"),
            "quantity": 5,
            "request_type": "MODIFY",
            "route_mode": "PLANNED",
            "route_template_id": template_id,
            "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _transfer(
    client: TestClient, source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int
) -> Any:
    return client.post(
        f"/api/scan-stations/{target.station_id}/transfers",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": source.area_id,
            "target_area_id": target.area_id,
            "quantity": quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _merge(client: TestClient, cell: _Cell, pn: str, flow_ids: list[int]) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/merges",
        json={
            "part_number": pn,
            "quantity_flow_ids": flow_ids,
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _retire(client: TestClient, machine_id: int) -> None:
    response = admin_of(client).post(
        f"/api/machines/{machine_id}/retire", json={"reason": "Worn out"}
    )
    assert response.status_code == 200, response.text


def _deactivate(client: TestClient, path: str) -> None:
    response = admin_of(client).patch(path, json={"is_active": False})
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------

_COUNTED = {
    "route_templates": "SELECT count(*) FROM route_templates",
    "route_steps": "SELECT count(*) FROM route_steps",
    "route_template_audit": "SELECT count(*) FROM audit_events WHERE entity_type = 'RouteTemplate'",
    "assigned_routes": "SELECT count(*) FROM assigned_routes",
    "assigned_route_steps": "SELECT count(*) FROM assigned_route_steps",
    "quantity_flows": "SELECT count(*) FROM quantity_flows",
    "part_movements": "SELECT count(*) FROM part_movements",
    "quantity_flow_lineage": "SELECT count(*) FROM quantity_flow_lineage",
}


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            name: int(connection.execute(sa.text(query)).scalar_one())
            for name, query in _COUNTED.items()
        }


def _audit_rows(engine: Engine, template_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, before_data, after_data, actor_reference, metadata"
                    " FROM audit_events WHERE entity_type = 'RouteTemplate'"
                    " AND entity_id = :entity_id ORDER BY id"
                ),
                {"entity_id": str(template_id)},
            )
        )


def _template_rows(engine: Engine, template_id: int) -> tuple[Any, list[Any]]:
    with engine.connect() as connection:
        template = connection.execute(
            sa.text("SELECT * FROM route_templates WHERE id = :id"), {"id": template_id}
        ).one_or_none()
        steps = list(
            connection.execute(
                sa.text(
                    "SELECT * FROM route_steps WHERE route_template_id = :id ORDER BY sequence"
                ),
                {"id": template_id},
            )
        )
    return template, steps


def _flow_route(engine: Engine, flow_id: int) -> int:
    with engine.connect() as connection:
        route = connection.execute(
            sa.text("SELECT assigned_route_id FROM quantity_flows WHERE id = :id"), {"id": flow_id}
        ).scalar_one()
    assert route is not None
    return int(route)


_STEP_FIELDS = (
    "sequence",
    "area_id",
    "operation_id",
    "expected_duration",
    "preferred_machine_id",
    "instructions",
)


def _snapshot_steps(engine: Engine, assigned_route_id: int) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                f"SELECT {', '.join(_STEP_FIELDS)} FROM assigned_route_steps"
                " WHERE assigned_route_id = :id ORDER BY sequence"
            ),
            {"id": assigned_route_id},
        )
        return [tuple(row) for row in rows]


def _template_steps(engine: Engine, template_id: int) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                f"SELECT {', '.join(_STEP_FIELDS)} FROM route_steps"
                " WHERE route_template_id = :id ORDER BY sequence"
            ),
            {"id": template_id},
        )
        return [tuple(row) for row in rows]


def _all_rows(engine: Engine, table: str) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row) for row in connection.execute(sa.text(f"SELECT * FROM {table} ORDER BY id"))
        ]


def _snapshot(record: dict[str, Any], *, archived_at: str | None = None) -> dict[str, Any]:
    """The expected §3.5 audit snapshot of a management record."""
    return {
        "name": record["name"],
        "description": record["description"],
        "archived_at": archived_at,
        "steps": [
            {
                "sequence": step["sequence"],
                "area_id": step["area_id"],
                "operation_id": step["operation_id"],
                "expected_duration_seconds": (
                    _seconds(step["expected_duration"])
                    if step["expected_duration"] is not None
                    else None
                ),
                "preferred_machine_id": step["preferred_machine_id"],
                "instructions": step["instructions"],
            }
            for step in record["steps"]
        ],
    }


def _seconds(iso_duration: str) -> float:
    durations = {"PT4H": 14400.0, "PT30M": 1800.0, "PT1H30M": 5400.0, "P1DT2H": 93600.0}
    return durations[iso_duration]


def _site_date(instant: str) -> str:
    return work_orders.site_date_of(datetime.datetime.fromisoformat(instant)).isoformat()


# ---------------------------------------------------------------------------
# Concurrency helpers
# ---------------------------------------------------------------------------


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


def _lock_waiters(engine: Engine) -> int:
    # As the owner: an application-role session sees no other session's wait.
    with owner_connection(engine.url) as connection:
        return int(
            connection.execute(
                sa.text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        )


def _await_lock_waiters(engine: Engine, expected: int, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _lock_waiters(engine) >= expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {expected} lock waiter(s)")


class _Runner:
    """Run application calls in threads, each in its own Session, and
    collect the result or the exception."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.results: dict[str, Any] = {}
        self.pids: dict[str, int] = {}
        self.threads: list[threading.Thread] = []

    def start(self, name: str, action: Callable[[Session], Any]) -> None:
        def run() -> None:
            with Session(self.engine) as session:
                try:
                    self.pids[name] = int(session.scalar(sa.select(sa.func.pg_backend_pid())) or 0)
                    self.results[name] = action(session)
                except Exception as exc:  # noqa: BLE001 — collected for assertions
                    self.results[name] = exc

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()

    def join(self) -> None:
        for thread in self.threads:
            thread.join(timeout=30)
        assert not any(thread.is_alive() for thread in self.threads), "a thread never finished"


def _release_call(
    client: TestClient, cell: _Cell, template_id: int, *, part_number: str | None = None
) -> tuple[str, Callable[[Session], Any]]:
    pn = part_number or _unique("PN")
    work_order_id, demand_id = _demand(client, pn)
    actor_user_id = admin_of(client).user_id

    def action(session: Session) -> Any:
        return production_release.release_to_production(
            session,
            work_order_id=work_order_id,
            work_order_demand_id=demand_id,
            part_number=pn,
            quantity=5,
            route_mode="PLANNED",
            route_template_id=template_id,
            starting_area_id=cell.area_id,
            operation_id=cell.operation_id,
            confirm_active_quantity=part_number is not None,
            device_event_id=str(uuid.uuid4()),
            actor_user_id=actor_user_id,
        )

    return pn, action


def _receipt_call(cell: _Cell, template_id: int) -> Callable[[Session], Any]:
    assert cell.station_id is not None
    station_id = cell.station_id

    def action(session: Session) -> Any:
        return intake.receive_quantity(
            session,
            station_id=station_id,
            part_number=_unique("PN"),
            quantity=5,
            request_type="MODIFY",
            route_mode="PLANNED",
            route_template_id=template_id,
            scanned_at=datetime.datetime.now(datetime.UTC),
            device_event_id=str(uuid.uuid4()),
        )

    return action


def _inputs(steps: list[dict[str, Any]]) -> list[RouteStepInput]:
    return [
        RouteStepInput(
            area_id=step["area_id"],
            operation_id=step["operation_id"],
            expected_duration=None,
            preferred_machine_id=step["preferred_machine_id"],
            instructions=step["instructions"],
        )
        for step in steps
    ]


def _replace_call(
    template_id: int, steps: list[dict[str, Any]], name: str, actor_user_id: int
) -> Callable[[Session], Any]:
    def action(session: Session) -> Any:
        return route_templates.replace_route_template(
            session,
            template_id,
            name=name,
            description=None,
            steps=_inputs(steps),
            actor_user_id=actor_user_id,
        )

    return action


def _flows_of(engine: Engine, pn: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.text("SELECT count(*) FROM quantity_flows WHERE part_number = :pn"), {"pn": pn}
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# T-1 / T-14 Create and listings
# ---------------------------------------------------------------------------


def test_create_stores_the_route_and_audits_it(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    name = _unique("ROUTE")
    response = _create(
        client,
        [
            _step(material, duration="PT30M", instructions="  Cut to length  "),
            _step(lathe, machine_id=lathe.machine_ids[0], duration="P1DT2H", instructions="   "),
            _step(material, duration=None),
        ],
        name=f"  {name}  ",
        description="   ",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "id",
        "name",
        "description",
        "archived_at",
        "archived_on",
        "created_at",
        "updated_at",
        "updated_on",
        "ever_used",
        "usage_count",
        "steps",
    }
    assert (body["name"], body["description"]) == (name, None)
    assert (body["archived_at"], body["archived_on"]) == (None, None)
    assert (body["ever_used"], body["usage_count"]) == (False, 0)
    assert body["updated_on"] == _site_date(body["updated_at"])
    assert [step["sequence"] for step in body["steps"]] == [1, 2, 3]
    assert [step["area_id"] for step in body["steps"]] == [
        material.area_id,
        lathe.area_id,
        material.area_id,
    ]
    assert [step["expected_duration"] for step in body["steps"]] == ["PT30M", "P1DT2H", None]
    assert [step["preferred_machine_id"] for step in body["steps"]] == [
        None,
        lathe.machine_ids[0],
        None,
    ]
    assert [step["instructions"] for step in body["steps"]] == ["Cut to length", None, None]

    events = _audit_rows(db_engine, body["id"])
    assert [event.event_type for event in events] == ["CREATED"]
    assert events[0].before_data is None
    assert events[0].after_data == _snapshot(body)
    assert events[0].actor_reference is None and events[0].metadata is None

    assert _record(client, body["id"]) == body
    listed = {entry["id"]: entry for entry in client.get("/api/route-templates").json()}
    # T-14: the release listing carries the preferred Machine.
    assert [step["preferred_machine_id"] for step in listed[body["id"]]["steps"]] == [
        None,
        lathe.machine_ids[0],
        None,
    ]


def test_management_list_orders_active_first_then_name(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    prefix = _unique("ORDER")
    archived = _created(client, [_step(cell)], name=f"{prefix} A")
    _release(client, cell, archived["id"])
    assert (
        admin_of(client).post(f"/api/route-templates/{archived['id']}/archive").status_code == 200
    )
    second = _created(client, [_step(cell)], name=f"{prefix} C")
    first = _created(client, [_step(cell)], name=f"{prefix} B")
    ids = [
        entry["id"]
        for entry in admin_of(client).get("/api/route-templates/management").json()
        if entry["name"].startswith(prefix)
    ]
    assert ids == [first["id"], second["id"], archived["id"]]


# ---------------------------------------------------------------------------
# T-2 Refusals
# ---------------------------------------------------------------------------


def test_every_refusal_has_its_copy_and_writes_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    good = _Cell(client, machine_count=1, station=False)
    other = _Cell(client, machine_count=1, station=False)
    terminal = _Cell(client, station=False, is_terminal=True)
    inactive_area = _Cell(client, station=False)
    _deactivate(client, f"/api/areas/{inactive_area.area_id}")
    inactive_operation = _create_operation(client, good.area_id)
    _deactivate(client, f"/api/operations/{inactive_operation['id']}")
    retired = _create_machine(client, good.area_id)
    _retire(client, int(retired["id"]))
    template = _created(client, [_step(good)])
    before = _counts(db_engine)
    stored = _template_rows(db_engine, template["id"])

    cases: list[tuple[dict[str, Any], int, str]] = [
        ({"name": "   ", "steps": [_step(good)]}, 422, "A route name is required."),
        ({"name": "R", "steps": []}, 422, "A Planned Route needs at least one step."),
        (
            {"name": "R", "steps": [_step(good), {**_step(other), "operation_id": None}]},
            422,
            "Step 2 needs an Operation.",
        ),
        (
            {"name": "R", "steps": [_step(good), {**_step(other), "area_id": _MISSING_ID}]},
            422,
            f"Step 2: Area {_MISSING_ID} does not exist.",
        ),
        (
            {"name": "R", "steps": [_step(good), _step(inactive_area)]},
            409,
            f"Step 2: Area '{inactive_area.name}' is inactive. Choose an active Area.",
        ),
        (
            {"name": "R", "steps": [{**_step(good), "operation_id": _MISSING_ID}]},
            422,
            f"Step 1: Operation {_MISSING_ID} does not exist.",
        ),
        (
            {"name": "R", "steps": [_step(good, operation_id=other.operation_id)]},
            422,
            f"Step 1: Operation '{other.operation['code']}' does not belong to Area '{good.name}'.",
        ),
        (
            {"name": "R", "steps": [_step(good, operation_id=int(inactive_operation["id"]))]},
            409,
            f"Step 1: Operation '{inactive_operation['code']}' is inactive."
            " Choose an Operation the Area still offers.",
        ),
        (
            {"name": "R", "steps": [_step(good), _step(other, machine_id=_MISSING_ID)]},
            422,
            f"Step 2: Machine {_MISSING_ID} does not exist.",
        ),
        (
            {"name": "R", "steps": [_step(good, machine_id=other.machine_ids[0])]},
            422,
            f"Step 1: Machine '{other.machines[0]['name']}' is not in Area '{good.name}'."
            " Choose one of the Area's active Machines or no preferred Machine.",
        ),
        (
            {"name": "R", "steps": [_step(good, machine_id=int(retired["id"]))]},
            409,
            f"Step 1: Machine '{retired['name']}' is retired."
            " Choose an active Machine or no preferred Machine.",
        ),
        (
            {"name": "R", "steps": [_step(good), _step(other, duration="PT0S")]},
            422,
            "Step 2: the estimated time must be longer than zero.",
        ),
        (
            {"name": "R", "steps": [_step(terminal), _step(good)]},
            409,
            f"Step 1: Area '{terminal.name}' is a terminal Area and never starts production."
            " Choose a starting Area for the first step.",
        ),
        # The first failing step in order is reported (step 1's Machine
        # before step 2's Area).
        (
            {
                "name": "R",
                "steps": [
                    _step(good, machine_id=_MISSING_ID),
                    {**_step(other), "area_id": _MISSING_ID},
                ],
            },
            422,
            f"Step 1: Machine {_MISSING_ID} does not exist.",
        ),
        (
            {"name": "R\x00", "steps": [_step(good)]},
            422,
            "The route name must be text.",
        ),
        (
            {"name": "R", "steps": [_step(good, instructions="a\x00b")]},
            422,
            "Step 1: the instructions must be text.",
        ),
    ]
    for body, status, detail in cases:
        for response in (
            admin_of(client).post("/api/route-templates", json=body),
            _put(client, template["id"], body),
        ):
            assert (response.status_code, response.json()) == (status, {"detail": detail}), body

    framework_cases: list[dict[str, Any]] = [
        {"name": "R", "steps": [_step(good)], "unknown": 1},
        {"name": "R", "steps": [{**_step(good), "id": 1}]},
        {"name": "R", "steps": [{**_step(good), "sequence": 1}]},
        {"name": "R", "steps": [{**_step(good), "area_id": "abc"}]},
        {"name": "R", "steps": [{**_step(good), "operation_id": 1.5}]},
        {"name": "R", "steps": [_step(good, duration="4 hours")]},
        {"name": "R"},
    ]
    for body in framework_cases:
        assert admin_of(client).post("/api/route-templates", json=body).status_code == 422, body
        assert _put(client, template["id"], body).status_code == 422, body

    assert _counts(db_engine) == before
    assert _template_rows(db_engine, template["id"]) == stored


def test_a_terminal_area_is_accepted_after_the_first_step(client: TestClient) -> None:
    start = _Cell(client, station=False)
    stockroom = _Cell(client, station=False, is_terminal=True)
    created = _created(client, [_step(start), _step(stockroom)])
    assert [step["area_id"] for step in created["steps"]] == [start.area_id, stockroom.area_id]


# ---------------------------------------------------------------------------
# T-3 Replace
# ---------------------------------------------------------------------------


def test_put_replaces_the_whole_route(client: TestClient, db_engine: Engine) -> None:
    a = _Cell(client, machine_count=1, station=False)
    b = _Cell(client, station=False)
    c = _Cell(client, station=False)
    created = _created(
        client,
        [_step(a, machine_id=a.machine_ids[0], duration="PT4H"), _step(b), _step(c)],
        description="First",
    )
    time.sleep(0.01)
    body = {
        "name": "  Renamed  ",
        "description": "Second",
        "steps": [
            _step(a, instructions="Start"),
            _step(c, duration="PT1H30M"),
            _step(b, machine_id=None),
            _step(a, machine_id=a.machine_ids[0]),
        ],
    }
    response = _put(client, created["id"], body)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert (updated["name"], updated["description"]) == ("Renamed", "Second")
    assert [step["sequence"] for step in updated["steps"]] == [1, 2, 3, 4]
    assert [step["area_id"] for step in updated["steps"]] == [
        a.area_id,
        c.area_id,
        b.area_id,
        a.area_id,
    ]
    old_ids = {step["id"] for step in created["steps"]}
    assert not old_ids & {step["id"] for step in updated["steps"]}
    assert updated["updated_at"] > created["updated_at"]
    assert updated["created_at"] == created["created_at"]
    events = _audit_rows(db_engine, created["id"])
    assert [event.event_type for event in events] == ["CREATED", "UPDATED"]
    assert events[1].before_data == _snapshot(created)
    assert events[1].after_data == _snapshot(updated)
    assert len(_template_steps(db_engine, created["id"])) == 4

    # Identical PUT: a no-op — no write, no audit row, updated_at kept.
    again = _put(client, created["id"], _write_body(updated))
    assert again.status_code == 200, again.text
    assert again.json() == updated
    assert len(_audit_rows(db_engine, created["id"])) == 2


def test_retried_identical_put_stays_a_no_op_after_a_reference_change(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, machine_count=1, station=False)
    operation = _create_operation(client, cell.area_id)
    created = _created(client, [_step(cell)])
    body = {
        "name": created["name"],
        "description": None,
        "steps": [_step(cell, operation_id=int(operation["id"]), machine_id=cell.machine_ids[0])],
    }
    committed = _put(client, created["id"], body)
    assert committed.status_code == 200, committed.text
    stored = _template_rows(db_engine, created["id"])
    audits = len(_audit_rows(db_engine, created["id"]))

    _deactivate(client, f"/api/operations/{operation['id']}")
    retried = _put(client, created["id"], body)
    assert retried.status_code == 200, retried.text
    assert retried.json() == committed.json()
    _retire(client, cell.machine_ids[0])
    retried = _put(client, created["id"], body)
    assert retried.status_code == 200, retried.text
    assert _template_rows(db_engine, created["id"]) == stored
    assert len(_audit_rows(db_engine, created["id"])) == audits


def test_put_refuses_an_absent_or_archived_route(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    absent = _put(client, _MISSING_ID, {"name": "R", "steps": [_step(cell)]})
    assert (absent.status_code, absent.json()) == (
        404,
        {"detail": f"Planned Route {_MISSING_ID} does not exist."},
    )
    created = _created(client, [_step(cell)])
    _release(client, cell, created["id"])
    assert admin_of(client).post(f"/api/route-templates/{created['id']}/archive").status_code == 200
    before = _counts(db_engine)
    refused = _put(client, created["id"], {**_write_body(created), "name": "New"})
    assert (refused.status_code, refused.json()) == (
        409,
        {
            "detail": f"Planned Route '{created['name']}' is archived and cannot be edited."
            " Duplicate it to create an editable copy."
        },
    )
    assert _counts(db_engine) == before


_UNBINDABLE_ID = 2**31


def test_an_id_beyond_the_integer_range_is_answered_as_missing(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, machine_count=1, station=False)
    before = _counts(db_engine)
    missing = {"detail": f"Planned Route {_UNBINDABLE_ID} does not exist."}
    path = f"/api/route-templates/{_UNBINDABLE_ID}"
    responses = {
        "put": _put(client, _UNBINDABLE_ID, {"name": "R", "steps": [_step(cell)]}),
        "archive": admin_of(client).post(f"{path}/archive"),
        "delete": admin_of(client).delete(path),
        "usage": admin_of(client).get(f"{path}/usage"),
    }
    for name, response in responses.items():
        assert (response.status_code, response.json()) == (404, missing), name
    assert _counts(db_engine) == before

    bodies: list[tuple[dict[str, Any], str]] = [
        ({**_step(cell), "area_id": _UNBINDABLE_ID}, f"Area {_UNBINDABLE_ID}"),
        ({**_step(cell), "operation_id": _UNBINDABLE_ID}, f"Operation {_UNBINDABLE_ID}"),
        (_step(cell, machine_id=_UNBINDABLE_ID), f"Machine {_UNBINDABLE_ID}"),
    ]
    existing = _created(client, [_step(cell)])
    before = _counts(db_engine)
    for step, reference in bodies:
        expected = (422, {"detail": f"Step 1: {reference} does not exist."})
        created = _create(client, [step])
        assert (created.status_code, created.json()) == expected, reference
        replaced = _put(client, existing["id"], {"name": "R", "steps": [step]})
        assert (replaced.status_code, replaced.json()) == expected, reference
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# T-4 Legacy step without an Operation (OD-11); T-5 stale references
# ---------------------------------------------------------------------------


def test_a_legacy_step_without_an_operation_must_be_completed(
    client: TestClient, db_engine: Engine
) -> None:
    a = _Cell(client, station=False)
    b = _Cell(client, station=False)
    with Session(db_engine) as session:
        template = models.RouteTemplate(name=_unique("LEGACY"))
        session.add(template)
        session.flush()
        session.add_all(
            [
                models.RouteStep(
                    route_template_id=template.id,
                    sequence=10,
                    area_id=a.area_id,
                    operation_id=a.operation_id,
                ),
                models.RouteStep(route_template_id=template.id, sequence=20, area_id=b.area_id),
            ]
        )
        session.commit()
        template_id = int(template.id)

    record = _record(client, template_id)
    assert [step["operation_id"] for step in record["steps"]] == [a.operation_id, None]
    assert [step["sequence"] for step in record["steps"]] == [10, 20]
    unchanged = _put(client, template_id, _write_body(record))
    assert (unchanged.status_code, unchanged.json()) == (
        422,
        {"detail": "Step 2 needs an Operation."},
    )
    completed = _write_body(record)
    completed["steps"][1]["operation_id"] = b.operation_id
    response = _put(client, template_id, completed)
    assert response.status_code == 200, response.text
    assert [step["sequence"] for step in response.json()["steps"]] == [1, 2]
    assert response.json()["steps"][1]["operation_id"] == b.operation_id


def test_stale_references_are_kept_and_refused_on_the_next_save(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client, machine_count=3, station=False)
    elsewhere = _Cell(client, station=False)
    operation = _create_operation(client, cell.area_id)
    by_operation = _created(client, [_step(cell, operation_id=int(operation["id"]))])
    by_retired = _created(client, [_step(cell, machine_id=cell.machine_ids[0])])
    by_moved = _created(client, [_step(cell, machine_id=cell.machine_ids[1])])

    _deactivate(client, f"/api/operations/{operation['id']}")
    _retire(client, cell.machine_ids[0])
    _retire(client, cell.machine_ids[1])
    reactivated = admin_of(client).post(
        f"/api/machines/{cell.machine_ids[1]}/reactivate",
        json={"reason": "Moved", "area_id": elsewhere.area_id},
    )
    assert reactivated.status_code == 200, reactivated.text

    records = _records(client)
    expected = [
        (by_operation, f"Step 1: Operation '{operation['code']}' is inactive.", 409),
        (by_retired, f"Step 1: Machine '{cell.machines[0]['name']}' is retired.", 409),
        (
            by_moved,
            f"Step 1: Machine '{cell.machines[1]['name']}' is not in Area '{cell.name}'.",
            422,
        ),
    ]
    for created, detail, status in expected:
        # The stored ids are still listed, never cleared.
        assert records[created["id"]]["steps"] == created["steps"]
        stored = _template_rows(db_engine, created["id"])
        audits = len(_audit_rows(db_engine, created["id"]))
        # An effective save (a new name) re-validates every step.
        response = _put(client, created["id"], {**_write_body(created), "name": "Renamed"})
        assert response.status_code == status, response.text
        assert response.json()["detail"].startswith(detail)
        assert _template_rows(db_engine, created["id"]) == stored
        assert len(_audit_rows(db_engine, created["id"])) == audits


# ---------------------------------------------------------------------------
# T-6 Snapshot copy, merge compatibility and independence
# ---------------------------------------------------------------------------


def test_release_receipt_and_split_copy_the_preferred_machine(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=1)
    lathe = _Cell(client, machine_count=1)
    created = _created(
        client,
        [
            _step(material, machine_id=material.machine_ids[0], duration="PT4H", instructions="Go"),
            _step(lathe, machine_id=lathe.machine_ids[0], duration="PT30M"),
        ],
    )
    template_steps = _template_steps(db_engine, created["id"])

    flow_id, pn = _release(client, material, created["id"])
    assert _snapshot_steps(db_engine, _flow_route(db_engine, flow_id)) == template_steps
    received = _receive(client, material, created["id"])
    assert received.status_code == 201, received.text
    assert _snapshot_steps(db_engine, received.json()["assigned_route_id"]) == template_steps

    split = _transfer(client, material, lathe, flow_id, pn, 4)
    assert split.status_code == 201, split.text
    for child in (split.json()["quantity_flow_id"], split.json()["remainder_quantity_flow_id"]):
        assert _snapshot_steps(db_engine, _flow_route(db_engine, child)) == template_steps


def test_merge_compares_the_preferred_machine_and_snapshots_stay_independent(
    client: TestClient, db_engine: Engine
) -> None:
    material = _Cell(client, machine_count=2)
    lathe = _Cell(client)
    first, second = material.machine_ids
    created = _created(client, [_step(material, machine_id=first), _step(lathe)])
    flow_a, pn = _release(client, material, created["id"])
    flow_b, _ = _release(client, material, created["id"], part_number=pn)
    edited = _write_body(created)
    edited["steps"][0]["preferred_machine_id"] = second
    assert _put(client, created["id"], edited).status_code == 200
    flow_c, _ = _release(client, material, created["id"], part_number=pn)

    before = _counts(db_engine)
    refused = _merge(client, material, pn, [flow_a, flow_c])
    assert refused.status_code == 409, refused.text
    assert "route context" in refused.json()["detail"]
    assert _counts(db_engine) == before

    merged = _merge(client, material, pn, [flow_a, flow_b])
    assert merged.status_code == 201, merged.text
    result_route = _flow_route(db_engine, merged.json()["quantity_flow_id"])
    assert [step[4] for step in _snapshot_steps(db_engine, result_route)] == [first, None]

    production = {
        table: _all_rows(db_engine, table)
        for table in ("assigned_routes", "assigned_route_steps", "part_movements", "quantity_flows")
    }
    with db_engine.connect() as connection:
        other_audits = connection.execute(
            sa.text(
                "SELECT count(*) FROM audit_events"
                " WHERE entity_type NOT IN ('RouteTemplate', 'Machine')"
            )
        ).scalar_one()
    rewritten = {
        "name": "Everything changed",
        "description": "New",
        "steps": [
            _step(material, machine_id=None, duration="PT30M", instructions="New"),
            _step(lathe, duration="PT4H"),
            _step(lathe),
        ],
    }
    assert _put(client, created["id"], rewritten).status_code == 200
    _retire(client, first)
    assert admin_of(client).post(f"/api/route-templates/{created['id']}/archive").status_code == 200
    assert {
        table: _all_rows(db_engine, table)
        for table in ("assigned_routes", "assigned_route_steps", "part_movements", "quantity_flows")
    } == production
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                sa.text(
                    "SELECT count(*) FROM audit_events"
                    " WHERE entity_type NOT IN ('RouteTemplate', 'Machine')"
                )
            ).scalar_one()
            == other_audits
        )


# ---------------------------------------------------------------------------
# T-7 Archive; T-8 Delete
# ---------------------------------------------------------------------------


def test_archive_retires_a_used_route_from_new_assignments(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell)])
    never_used = admin_of(client).post(f"/api/route-templates/{created['id']}/archive")
    assert (never_used.status_code, never_used.json()) == (
        409,
        {
            "detail": f"Planned Route '{created['name']}' has never been used by a released"
            " Quantity Flow. Delete it instead of archiving it."
        },
    )
    _release(client, cell, created["id"])

    archived = admin_of(client).post(f"/api/route-templates/{created['id']}/archive")
    assert archived.status_code == 200, archived.text
    body = archived.json()
    assert body["archived_at"] is not None
    assert body["archived_on"] == _site_date(body["archived_at"])
    assert (body["ever_used"], body["usage_count"]) == (True, 1)
    events = _audit_rows(db_engine, created["id"])
    assert [event.event_type for event in events] == ["CREATED", "UPDATED"]
    assert events[1].before_data == _snapshot(created)
    stored_archived_at = events[1].after_data["archived_at"]
    assert datetime.datetime.fromisoformat(stored_archived_at) == datetime.datetime.fromisoformat(
        body["archived_at"]
    )
    assert events[1].after_data == _snapshot(created, archived_at=stored_archived_at)

    assert created["id"] not in {entry["id"] for entry in client.get("/api/route-templates").json()}
    assert _record(client, created["id"])["archived_at"] == body["archived_at"]

    before = _counts(db_engine)
    release, _ = _release_response(client, cell, created["id"])
    assert release.status_code == 409 and "is archived" in release.json()["detail"]
    receipt = _receive(client, cell, created["id"])
    assert receipt.status_code == 409 and "is archived" in receipt.json()["detail"]
    assert _counts(db_engine) == before

    again = admin_of(client).post(f"/api/route-templates/{created['id']}/archive")
    assert again.status_code == 200 and again.json() == body
    assert len(_audit_rows(db_engine, created["id"])) == 2

    absent = admin_of(client).post(f"/api/route-templates/{_MISSING_ID}/archive")
    assert (absent.status_code, absent.json()) == (
        404,
        {"detail": f"Planned Route {_MISSING_ID} does not exist."},
    )


def test_delete_removes_only_a_never_used_route(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell), _step(cell, instructions="Again")])
    deleted = admin_of(client).delete(f"/api/route-templates/{created['id']}")
    assert deleted.status_code == 204, deleted.text
    assert _template_rows(db_engine, created["id"]) == (None, [])
    events = _audit_rows(db_engine, created["id"])
    assert [event.event_type for event in events] == ["CREATED", "DELETED"]
    assert (events[1].before_data, events[1].after_data) == (_snapshot(created), None)
    again = admin_of(client).delete(f"/api/route-templates/{created['id']}")
    assert (again.status_code, again.json()) == (
        404,
        {"detail": f"Planned Route {created['id']} does not exist."},
    )

    used = _created(client, [_step(cell)])
    _release(client, cell, used["id"])
    archived = _created(client, [_step(cell)])
    _release(client, cell, archived["id"])
    assert (
        admin_of(client).post(f"/api/route-templates/{archived['id']}/archive").status_code == 200
    )
    for template in (used, archived):
        before = _counts(db_engine)
        refused = admin_of(client).delete(f"/api/route-templates/{template['id']}")
        assert (refused.status_code, refused.json()) == (
            409,
            {
                "detail": f"Planned Route '{template['name']}' has been used by released"
                " Quantity Flows, so it cannot be deleted. Archive it instead."
            },
        )
        assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# T-9 Usage
# ---------------------------------------------------------------------------


def test_usage_lists_the_released_flows_newest_first(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    material = _Cell(client)
    lathe = _Cell(client)
    created = _created(client, [_step(material), _step(lathe)])
    first, first_pn = _release(client, material, created["id"])
    second, second_pn = _release(client, material, created["id"])
    received = _receive(client, material, created["id"])
    assert received.status_code == 201, received.text
    third, third_pn = received.json()["quantity_flow_id"], received.json()["part_number"]
    # A SPLIT child carries the provenance but was never released with it.
    split = _transfer(client, material, lathe, first, first_pn, 4)
    assert split.status_code == 201, split.text

    response = admin_of(client).get(f"/api/route-templates/{created['id']}/usage")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["template_id"] == created["id"] and body["total"] == 3
    assert [(flow["quantity_flow_id"], flow["part_number"]) for flow in body["flows"]] == [
        (third, third_pn),
        (second, second_pn),
        (first, first_pn),
    ]
    with db_engine.connect() as connection:
        received_at = connection.execute(
            sa.text(
                "SELECT occurred_at FROM part_movements"
                " WHERE quantity_flow_id = :flow AND movement_type = 'RECEIVED'"
            ),
            {"flow": first},
        ).scalar_one()
    assert body["flows"][2]["released_on"] == work_orders.site_date_of(received_at).isoformat()
    record = _record(client, created["id"])
    assert (record["ever_used"], record["usage_count"]) == (True, 3)

    monkeypatch.setattr(route_templates, "USAGE_LIST_LIMIT", 2)
    limited = admin_of(client).get(f"/api/route-templates/{created['id']}/usage").json()
    assert limited["total"] == 3
    assert [flow["quantity_flow_id"] for flow in limited["flows"]] == [third, second]
    monkeypatch.undo()

    assert admin_of(client).post(f"/api/route-templates/{created['id']}/archive").status_code == 200
    assert admin_of(client).get(f"/api/route-templates/{created['id']}/usage").json()["total"] == 3
    absent = admin_of(client).get(f"/api/route-templates/{_MISSING_ID}/usage")
    assert (absent.status_code, absent.json()) == (
        404,
        {"detail": f"Planned Route {_MISSING_ID} does not exist."},
    )


# ---------------------------------------------------------------------------
# T-10 Release / receipt vs template writes
# ---------------------------------------------------------------------------


def test_release_holding_the_template_makes_a_delete_conflict(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell)])
    pause = _Pause(route_templates.lock_template_for_assignment)
    monkeypatch.setattr(route_templates, "lock_template_for_assignment", pause)
    _, release = _release_call(client, cell, created["id"])
    runner = _Runner(db_engine)
    try:
        runner.start("release", release)
        assert pause.first_inside.wait(timeout=20)
        runner.start(
            "delete",
            lambda s: route_templates.delete_route_template(
                s, created["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    assert isinstance(runner.results["release"], production_release.ProductionRelease)
    refused = runner.results["delete"]
    assert isinstance(refused, ConflictError) and "has been used" in refused.message
    assert _template_rows(db_engine, created["id"])[0] is not None


def test_delete_holding_the_template_makes_the_release_find_nothing(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell)])
    pause = _Pause(route_templates._is_ever_used)
    monkeypatch.setattr(route_templates, "_is_ever_used", pause)
    pn, release = _release_call(client, cell, created["id"])
    runner = _Runner(db_engine)
    try:
        runner.start(
            "delete",
            lambda s: route_templates.delete_route_template(
                s, created["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        assert pause.first_inside.wait(timeout=20)
        runner.start("release", release)
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    assert runner.results["delete"] is None
    refused = runner.results["release"]
    assert isinstance(refused, InvalidInputError)
    assert refused.message == f"Route Template {created['id']} does not exist."
    assert _flows_of(db_engine, pn) == 0


def test_edit_holding_the_template_is_what_the_release_snapshots(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    other = _Cell(client, station=False)
    created = _created(client, [_step(cell, instructions="Old")])
    new_steps = [_step(cell, instructions="New"), _step(other)]
    pause = _Pause(route_templates._add_steps)
    monkeypatch.setattr(route_templates, "_add_steps", pause)
    pn, release = _release_call(client, cell, created["id"])
    runner = _Runner(db_engine)
    try:
        runner.start(
            "put", _replace_call(created["id"], new_steps, "Edited", admin_of(client).user_id)
        )
        assert pause.first_inside.wait(timeout=20)
        runner.start("release", release)
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    assert not isinstance(runner.results["put"], Exception), runner.results["put"]
    released = runner.results["release"]
    assert isinstance(released, production_release.ProductionRelease)
    snapshot = _snapshot_steps(db_engine, _flow_route(db_engine, released.quantity_flow_id))
    assert snapshot == _template_steps(db_engine, created["id"])
    assert [step[5] for step in snapshot] == ["New", None]


def test_release_holding_the_template_snapshots_the_old_steps(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell, instructions="Old")])
    old_steps = _template_steps(db_engine, created["id"])
    pause = _Pause(route_templates.lock_template_for_assignment)
    monkeypatch.setattr(route_templates, "lock_template_for_assignment", pause)
    _, release = _release_call(client, cell, created["id"])
    runner = _Runner(db_engine)
    try:
        runner.start("release", release)
        assert pause.first_inside.wait(timeout=20)
        runner.start(
            "put",
            _replace_call(
                created["id"], [_step(cell, instructions="New")], "E", admin_of(client).user_id
            ),
        )
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    released = runner.results["release"]
    assert isinstance(released, production_release.ProductionRelease)
    assert not isinstance(runner.results["put"], Exception), runner.results["put"]
    route = _flow_route(db_engine, released.quantity_flow_id)
    assert _snapshot_steps(db_engine, route) == old_steps
    assert [step[5] for step in _template_steps(db_engine, created["id"])] == ["New"]


def test_archive_and_release_serialize_in_both_orders(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    # Release first: the archive waits, then finds the route used.
    unused = _created(client, [_step(cell)])
    pause = _Pause(route_templates.lock_template_for_assignment)
    monkeypatch.setattr(route_templates, "lock_template_for_assignment", pause)
    _, release = _release_call(client, cell, unused["id"])
    runner = _Runner(db_engine)
    try:
        runner.start("release", release)
        assert pause.first_inside.wait(timeout=20)
        runner.start(
            "archive",
            lambda s: route_templates.archive_route_template(
                s, unused["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    assert isinstance(runner.results["release"], production_release.ProductionRelease)
    archived = runner.results["archive"]
    assert isinstance(archived, route_templates.RouteTemplateRecord)
    assert archived.template.archived_at is not None
    monkeypatch.undo()

    # Archive first: the release waits, then finds the route archived.
    used = _created(client, [_step(cell)])
    _release(client, cell, used["id"])
    pause = _Pause(route_templates._is_ever_used)
    monkeypatch.setattr(route_templates, "_is_ever_used", pause)
    pn, release = _release_call(client, cell, used["id"])
    runner = _Runner(db_engine)
    try:
        runner.start(
            "archive",
            lambda s: route_templates.archive_route_template(
                s, used["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        assert pause.first_inside.wait(timeout=20)
        runner.start("release", release)
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    assert isinstance(runner.results["archive"], route_templates.RouteTemplateRecord)
    refused = runner.results["release"]
    assert isinstance(refused, ConflictError) and "is archived" in refused.message
    assert _flows_of(db_engine, pn) == 0


def test_receipt_holding_the_template_makes_a_delete_conflict(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell)])
    pause = _Pause(route_templates.lock_template_for_assignment)
    monkeypatch.setattr(route_templates, "lock_template_for_assignment", pause)
    runner = _Runner(db_engine)
    try:
        runner.start("receipt", _receipt_call(cell, created["id"]))
        assert pause.first_inside.wait(timeout=20)
        runner.start(
            "delete",
            lambda s: route_templates.delete_route_template(
                s, created["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        _await_lock_waiters(db_engine, 1)
    finally:
        pause.let_first_finish.set()
    runner.join()
    assert isinstance(runner.results["receipt"], intake.IntakeReceipt)
    refused = runner.results["delete"]
    assert isinstance(refused, ConflictError) and "has been used" in refused.message


def test_release_never_locks_the_preferred_machine(client: TestClient, db_engine: Engine) -> None:
    material = _Cell(client)
    lathe = _Cell(client, machine_count=1)
    machine_id = lathe.machine_ids[0]
    created = _created(client, [_step(material), _step(lathe, machine_id=machine_id)])
    _, release = _release_call(client, material, created["id"])
    with db_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            sa.text("SELECT id FROM machines WHERE id = :id FOR UPDATE"), {"id": machine_id}
        )
        runner = _Runner(db_engine)
        # The snapshot INSERT has no FK on the preferred Machine: the
        # release completes while the Machine row is held FOR UPDATE.
        runner.start("release", release)
        runner.join()
        holder.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        holder.execute(
            sa.text("SELECT id FROM areas WHERE id = :id FOR UPDATE"), {"id": material.area_id}
        )
        transaction.commit()
    released = runner.results["release"]
    assert isinstance(released, production_release.ProductionRelease), released
    snapshot = _snapshot_steps(db_engine, _flow_route(db_engine, released.quantity_flow_id))
    assert [step[4] for step in snapshot] == [None, machine_id]


# ---------------------------------------------------------------------------
# T-11 Lock order (no deadlock)
# ---------------------------------------------------------------------------


def test_writer_and_release_waiting_on_one_area_never_deadlock(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    created = _created(client, [_step(cell, instructions="Old")])
    edited = [_step(cell, instructions="Edited")]
    _, release = _release_call(client, cell, created["id"])
    runner = _Runner(db_engine)
    with db_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            sa.text("SELECT id FROM areas WHERE id = :id FOR UPDATE"), {"id": cell.area_id}
        )
        # The writer holds the template and waits on the Area.
        runner.start(
            "put", _replace_call(created["id"], edited, "Edited", admin_of(client).user_id)
        )
        _await_lock_waiters(db_engine, 1)
        # The release waits on the template, holding no Area lock.
        runner.start("release", release)
        _await_lock_waiters(db_engine, 2)
        transaction.commit()
    runner.join()
    assert not isinstance(runner.results["put"], Exception), runner.results["put"]
    released = runner.results["release"]
    assert isinstance(released, production_release.ProductionRelease), released
    snapshot = _snapshot_steps(db_engine, _flow_route(db_engine, released.quantity_flow_id))
    assert [step[5] for step in snapshot] == ["Edited"]


def _area_relation_locks(engine: Engine, pid: int) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.text(
                    "SELECT count(*) FROM pg_locks"
                    " WHERE pid = :pid AND relation = 'areas'::regclass AND granted"
                ),
                {"pid": pid},
            ).scalar_one()
        )


@pytest.mark.parametrize("write", ["create", "replace"])
def test_writer_locks_the_machine_before_any_area(
    client: TestClient, db_engine: Engine, write: str
) -> None:
    source = _Cell(client, machine_count=1, station=False)
    target = _Cell(client, station=False)
    machine_id = source.machine_ids[0]
    steps = [_step(target), _step(source, machine_id=machine_id)]
    existing = _created(client, [_step(target)])

    def action(session: Session) -> Any:
        if write == "create":
            return route_templates.create_route_template(
                session,
                name="Locked",
                description=None,
                steps=_inputs(steps),
                actor_user_id=admin_of(client).user_id,
            )
        return _replace_call(existing["id"], steps, "Locked", admin_of(client).user_id)(session)

    runner = _Runner(db_engine)
    with db_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            sa.text("SELECT id FROM machines WHERE id = :id FOR UPDATE"), {"id": machine_id}
        )
        runner.start("writer", action)
        _await_lock_waiters(db_engine, 1)
        assert _area_relation_locks(db_engine, runner.pids["writer"]) == 0
        # A transfer off the Machine now locks its target Area: granted.
        holder.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        holder.execute(
            sa.text("SELECT id FROM areas WHERE id = :id FOR UPDATE"), {"id": target.area_id}
        )
        transaction.commit()
    runner.join()
    record = runner.results["writer"]
    assert isinstance(record, route_templates.RouteTemplateRecord), record
    assert [step.preferred_machine_id for step in record.steps] == [None, machine_id]


def test_writer_locks_areas_in_ascending_order(client: TestClient, db_engine: Engine) -> None:
    low = _Cell(client, station=False)
    high = _Cell(client, station=False)
    assert low.area_id < high.area_id
    steps = [_step(high), _step(low)]
    runner = _Runner(db_engine)
    with db_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            sa.text("SELECT id FROM areas WHERE id = :id FOR UPDATE"), {"id": low.area_id}
        )
        runner.start(
            "writer",
            lambda s: route_templates.create_route_template(
                s,
                name="Ascending",
                description=None,
                steps=_inputs(steps),
                actor_user_id=admin_of(client).user_id,
            ),
        )
        _await_lock_waiters(db_engine, 1)
        # An Undo restoring both Areas locks the higher one next: granted.
        holder.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        holder.execute(
            sa.text("SELECT id FROM areas WHERE id = :id FOR UPDATE"), {"id": high.area_id}
        )
        transaction.commit()
    runner.join()
    record = runner.results["writer"]
    assert isinstance(record, route_templates.RouteTemplateRecord), record
    assert [step.area_id for step in record.steps] == [high.area_id, low.area_id]


class _Gate:
    """Test seam: EVERY wrapped call stops after completing — while the
    caller holds its locks — until the gate opens; arrivals are counted."""

    def __init__(self) -> None:
        self.opened = threading.Event()
        self._guard = threading.Lock()
        self.arrivals = 0

    def wrap(self, real: Callable[..., Any]) -> Callable[..., Any]:
        def gated(*args: Any, **kwargs: Any) -> Any:
            result = real(*args, **kwargs)
            with self._guard:
                self.arrivals += 1
            assert self.opened.wait(timeout=20), "test deadlock: never opened"
            return result

        return gated


def _crossing_assignments(
    engine: Engine, gate: _Gate, first: Callable[[Session], Any], second: Callable[[Session], Any]
) -> dict[str, Any]:
    """Run two assignments whose routes cross Areas in opposite order.

    ``first`` reaches the gate holding its Area locks; ``second`` then
    either blocks on an Area lock (the ascending protocol) or reaches the
    gate too, holding its own starting Area — the state in which
    step-order snapshot FK locks deadlock once the gate opens.
    """
    runner = _Runner(engine)
    try:
        runner.start("first", first)
        deadline = time.monotonic() + 10
        while gate.arrivals < 1:
            assert time.monotonic() < deadline, "the first assignment never reached the gate"
            time.sleep(0.05)
        runner.start("second", second)
        deadline = time.monotonic() + 10
        while gate.arrivals < 2 and _lock_waiters(engine) < 1:
            assert time.monotonic() < deadline, "the second assignment neither waited nor arrived"
            time.sleep(0.05)
    finally:
        gate.opened.set()
    runner.join()
    return runner.results


def test_releases_over_crossing_routes_never_deadlock(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    low = _Cell(client, station=False)
    high = _Cell(client, station=False)
    forward = _created(client, [_step(low), _step(high)])
    backward = _created(client, [_step(high), _step(low)])
    gate = _Gate()
    monkeypatch.setattr(
        production_release,
        "active_quantity_distribution",
        gate.wrap(part_numbers.active_quantity_distribution),
    )
    first_pn, first = _release_call(client, low, forward["id"])
    second_pn, second = _release_call(client, high, backward["id"])
    results = _crossing_assignments(db_engine, gate, first, second)
    for name in ("first", "second"):
        assert isinstance(results[name], production_release.ProductionRelease), results[name]
    assert _flows_of(db_engine, first_pn) == 1
    assert _flows_of(db_engine, second_pn) == 1


def test_receipt_and_release_over_crossing_routes_never_deadlock(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    low = _Cell(client)
    high = _Cell(client)
    forward = _created(client, [_step(low), _step(high)])
    backward = _created(client, [_step(high), _step(low)])
    gate = _Gate()
    monkeypatch.setattr(
        intake, "resolve_arrival_operation", gate.wrap(transfers.resolve_arrival_operation)
    )
    monkeypatch.setattr(
        production_release,
        "active_quantity_distribution",
        gate.wrap(part_numbers.active_quantity_distribution),
    )
    _, release = _release_call(client, high, backward["id"])
    results = _crossing_assignments(db_engine, gate, _receipt_call(low, forward["id"]), release)
    assert isinstance(results["first"], intake.IntakeReceipt), results["first"]
    assert isinstance(results["second"], production_release.ProductionRelease), results["second"]


# ---------------------------------------------------------------------------
# T-12 Independence from the PN lock; T-13 Atomicity
# ---------------------------------------------------------------------------


def test_template_writes_never_take_the_part_number_lock(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    used = _created(client, [_step(cell)])
    _, pn = _release(client, cell, used["id"])
    unused = _created(client, [_step(cell)])
    runner = _Runner(db_engine)
    with db_engine.connect() as holder:
        transaction = holder.begin()
        holder.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"partflow:part-number:{pn}"},
        )
        runner.start(
            "create",
            lambda s: route_templates.create_route_template(
                s,
                name="Free",
                description=None,
                steps=_inputs([_step(cell)]),
                actor_user_id=admin_of(client).user_id,
            ),
        )
        runner.start(
            "replace",
            _replace_call(
                used["id"], [_step(cell, instructions="x")], "N", admin_of(client).user_id
            ),
        )
        runner.join()
        runner.start(
            "archive",
            lambda s: route_templates.archive_route_template(
                s, used["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        runner.start(
            "delete",
            lambda s: route_templates.delete_route_template(
                s, unused["id"], actor_user_id=admin_of(client).user_id
            ),
        )
        runner.join()
        transaction.rollback()
    for name in ("create", "replace", "archive", "delete"):
        assert not isinstance(runner.results[name], Exception), (name, runner.results[name])


def test_a_failing_audit_row_leaves_nothing_written(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cell = _Cell(client)
    used = _created(client, [_step(cell)])
    _release(client, cell, used["id"])
    unused = _created(client, [_step(cell)])
    before = _counts(db_engine)
    stored = {
        template["id"]: _template_rows(db_engine, template["id"]) for template in (used, unused)
    }

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(audit, "append_audit_event", broken)
    calls: list[Callable[[Session], Any]] = [
        lambda s: route_templates.create_route_template(
            s,
            name="Never",
            description=None,
            steps=_inputs([_step(cell)]),
            actor_user_id=admin_of(client).user_id,
        ),
        _replace_call(
            used["id"], [_step(cell, instructions="Never")], "Never", admin_of(client).user_id
        ),
        lambda s: route_templates.archive_route_template(
            s, used["id"], actor_user_id=admin_of(client).user_id
        ),
        lambda s: route_templates.delete_route_template(
            s, unused["id"], actor_user_id=admin_of(client).user_id
        ),
    ]
    for call in calls:
        with Session(db_engine) as session, pytest.raises(RuntimeError):
            call(session)
    assert _counts(db_engine) == before
    for template_id, rows in stored.items():
        assert _template_rows(db_engine, template_id) == rows
