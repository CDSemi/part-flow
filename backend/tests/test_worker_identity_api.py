"""Integration tests for Phase 13 slice 3 — Worker identity on production records.

Exercises the full request path — FastAPI routes, the Application
commands and read models, and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain
(IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §8.11, §8.12, §8.13,
§19; PLAN CD4/CD10):

- every Scan Station command (the 14 entry points) records the Worker
  its station's Area mode identifies on EVERY row it appends — the
  Fixed Worker in Fixed Worker mode, none in Disabled mode — including
  the SPLIT prefix, the implicit AREA_COMPLETED, the REVERSED rows of
  an Undo and the station allocation rows; the station's Area decides
  even when the command writes rows about another Area;
- Management rows stay NULL; identity never changes the quantity
  effect of a command;
- idempotency: a committed command replays with its recorded identity
  after any configuration change; a refused first attempt retried
  under the same key records the identity judged at its first
  successful recording; identity never causes a fingerprint conflict;
- refusals with zero writes: an inactive Fixed Worker (409) — the
  Scanned session mode (Phase 13 slice 4) is covered by
  `test_worker_sessions_api.py`;
- concurrency: the resolver's FOR KEY SHARE serialises with a Worker
  deactivation's FOR UPDATE, Area saves (update and create) and
  deactivations have one serial outcome, and the Area saves take the
  Fixed Worker lock in the S2c-F1 addendum order;
- the station context, the read-only badge scan, the Undo preview and
  PN Tracking expose the identity;
- no request carries identity; a static guard keeps identity out of
  every business rule.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations/Workers; the module database is dropped afterwards.
"""

import datetime
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import audit
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APPLICATION_DIR = _BACKEND_DIR / "app" / "application"
_TEST_DATABASE = "partflow_test_worker_identity_api"
_DB_URL_ENV = "DATABASE_URL"

_E9_FRAGMENT = "is inactive, so this action cannot record its Worker"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
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
        json={"prefix": "WI-", "digits": 4},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


class _Cell:
    """An Area with one Operation, one Scan Station and optional Machines."""

    def __init__(
        self, client: TestClient, *, machine_count: int = 0, is_terminal: bool = False
    ) -> None:
        department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        self.area = _ok(
            client.post(
                "/api/areas",
                json={
                    "department_id": department["id"],
                    "name": _unique("AREA"),
                    "is_terminal": is_terminal,
                },
            ),
            201,
        )
        self.area_id = int(self.area["id"])
        self.area_name = str(self.area["name"])
        operation = _ok(
            client.post("/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}),
            201,
        )
        self.operation_id = int(operation["id"])
        station = _ok(
            client.post(
                "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
            ),
            201,
        )
        self.station_id = str(station["station_id"])
        self.machine_ids = [
            int(
                _ok(
                    client.post(
                        "/api/machines", json={"area_id": self.area_id, "name": _unique("Lathe")}
                    ),
                    201,
                )["id"]
            )
            for _ in range(machine_count)
        ]

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


def _worker(client: TestClient, name: str | None = None) -> dict[str, Any]:
    return _ok(
        client.post(
            "/api/workers",
            json={"name": name or _unique("Worker"), "badge_barcode": _unique("BADGE")},
        ),
        201,
    )


def _set_mode(
    client: TestClient, area_id: int, mode: str, worker_id: int | None = None
) -> dict[str, Any]:
    return _ok(
        client.patch(
            f"/api/areas/{area_id}",
            json={"worker_identification_mode": mode, "fixed_worker_id": worker_id},
        )
    )


def _force_inactive(engine: Engine, worker_id: int) -> None:
    """A state the API refuses while the Worker is fixed (fixture only)."""
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE workers SET is_active = false WHERE id = :id"), {"id": worker_id}
        )


def _release(
    client: TestClient, cell: _Cell, *, quantity: int = 10, part_number: str | None = None
) -> tuple[int, str]:
    """Management releases ``quantity`` of a PN into ``cell`` (no station)."""
    pn = part_number or _unique("PN")
    work_order = _ok(
        client.post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
        ),
        201,
    )
    released = _ok(
        client.post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": pn,
                "quantity": quantity,
                "route_mode": "FLOATING",
                "starting_area_id": cell.area_id,
                "operation_id": cell.operation_id,
                "confirm_active_quantity": part_number is not None,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )
    return int(released["quantity_flow_id"]), pn


def _event() -> str:
    return str(uuid.uuid4())


def _transfer_payload(
    source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int, **kw: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": _event(),
    }
    payload.update(kw)
    return payload


def _transfer(
    client: TestClient,
    source: _Cell,
    target: _Cell,
    flow_id: int,
    pn: str,
    quantity: int,
    **kw: Any,
) -> Any:
    return client.post(
        f"/api/scan-stations/{target.station_id}/transfers",
        json=_transfer_payload(source, target, flow_id, pn, quantity, **kw),
    )


def _stock(
    client: TestClient, source: _Cell, stockroom: _Cell, flow_id: int, pn: str, quantity: int
) -> Any:
    return client.post(
        f"/api/scan-stations/{stockroom.station_id}/stockings",
        json=_transfer_payload(source, stockroom, flow_id, pn, quantity),
    )


def _in_area_payload(
    flow_id: int, pn: str, quantity: int, machine_id: int | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "quantity": quantity,
        "device_event_id": _event(),
    }
    if machine_id is not None:
        payload["machine_id"] = machine_id
    return payload


def _assign(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/machine-assignments",
        json=_in_area_payload(flow_id, pn, quantity, cell.machine_id),
    )


def _queue(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/machine-releases",
        json=_in_area_payload(flow_id, pn, quantity, cell.machine_id),
    )


def _done(
    client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int, *, machine: bool
) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/area-completions",
        json=_in_area_payload(flow_id, pn, quantity, cell.machine_id if machine else None),
    )


def _merge(client: TestClient, cell: _Cell, pn: str, flow_ids: list[int]) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/merges",
        json={"part_number": pn, "quantity_flow_ids": flow_ids, "device_event_id": _event()},
    )


def _scrap(client: TestClient, cell: _Cell, flow_id: int, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/scraps",
        json={**_in_area_payload(flow_id, pn, quantity), "reason": "damaged"},
    )


def _add(client: TestClient, cell: _Cell, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/quantity-additions",
        json={
            "part_number": pn,
            "quantity": quantity,
            "reason": "found on the floor",
            "device_event_id": _event(),
        },
    )


def _receive(client: TestClient, cell: _Cell, pn: str, quantity: int) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/receipts",
        json={
            "part_number": pn,
            "quantity": quantity,
            "request_type": "MODIFY",
            "route_mode": "FLOATING",
            "scanned_at": _now_iso(),
            "device_event_id": _event(),
        },
    )


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _undo(client: TestClient, cell: _Cell, pn: str, reverses: str) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/undos",
        json={"part_number": pn, "reverses_device_event_id": reverses, "device_event_id": _event()},
    )


def _preview(client: TestClient, cell: _Cell, reverses: str) -> dict[str, Any]:
    return _ok(client.get(f"/api/scan-stations/{cell.station_id}/undo-preview/{reverses}"))


def _demand(client: TestClient, pn: str, requested: int) -> int:
    work_order = _ok(
        client.post(
            "/api/work-orders",
            json={"lines": [{"part_number": pn, "requested_quantity": requested}]},
        ),
        201,
    )
    return int(work_order["demands"][0]["id"])


def _stocked(client: TestClient, material: _Cell, stockroom: _Cell) -> tuple[str, int]:
    """A PN with 10 pcs in stock and an open demand line of its own."""
    flow_id, pn = _release(client, material)
    _ok(_stock(client, material, stockroom, flow_id, pn, 10), 201)
    return pn, _demand(client, pn, 5)


def _allocate(
    client: TestClient, pn: str, demand_id: int, quantity: int, station_id: str | None
) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "allocation_quantity": quantity,
        "lines": [{"work_order_demand_id": demand_id, "quantity": quantity}],
        "device_event_id": _event(),
    }
    if station_id is not None:
        payload["station_id"] = station_id
    return client.post("/api/allocations", json=payload)


def _reverse(client: TestClient, allocation_id: int, station_id: str | None) -> Any:
    payload: dict[str, Any] = {"reason": "wrong Work Order", "device_event_id": _event()}
    if station_id is not None:
        payload["station_id"] = station_id
    return client.post(f"/api/allocations/{allocation_id}/reversals", json=payload)


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------


def _movement_workers(engine: Engine, device_event_id: str) -> list[int | None]:
    with engine.connect() as connection:
        return [
            row.worker_id
            for row in connection.execute(
                sa.text(
                    "SELECT worker_id FROM part_movements WHERE device_event_id = :event"
                    " ORDER BY command_sequence"
                ),
                {"event": device_event_id},
            )
        ]


def _allocation_workers(engine: Engine, device_event_id: str) -> list[int | None]:
    with engine.connect() as connection:
        return [
            row.allocated_by_worker_id
            for row in connection.execute(
                sa.text(
                    "SELECT allocated_by_worker_id FROM work_order_allocations"
                    " WHERE device_event_id = :event ORDER BY command_sequence"
                ),
                {"event": device_event_id},
            )
        ]


def _command_movement_types(engine: Engine, device_event_id: str) -> list[str]:
    with engine.connect() as connection:
        return [
            str(row.movement_type)
            for row in connection.execute(
                sa.text(
                    "SELECT movement_type FROM part_movements WHERE device_event_id = :event"
                    " ORDER BY command_sequence"
                ),
                {"event": device_event_id},
            )
        ]


_COUNTED_TABLES = (
    "part_movements",
    "quantity_flows",
    "quantity_flow_lineage",
    "work_order_allocations",
    "work_order_demands",
    "work_orders",
    "audit_events",
    "workers",
    "areas",
)


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED_TABLES
        }


def _flow_projection(engine: Engine, flow_id: int) -> tuple[Any, ...]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT status, quantity, current_area_id, current_machine_id, closed_at"
                " FROM quantity_flows WHERE id = :id"
            ),
            {"id": flow_id},
        ).one()
    return tuple(row)


def _start(request: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(request()))
    thread.start()
    return thread, results


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread, results: list[Any]) -> Any:
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# T-1 — every station command records the station Area's identity
# ---------------------------------------------------------------------------

# A scenario builds its own cells and history, calls ``apply`` on the
# station's cell right before the command under test, and returns which
# rows to inspect ("movements" / "allocations"), the command's
# device_event_id and the number of rows the command appends.
_Apply = Callable[[_Cell], None]
_Scenario = Callable[[TestClient, _Apply], tuple[str, str, int]]


def _created(response: Any) -> str:
    assert response.status_code == 201, response.text
    return str(response.json()["device_event_id"])


def _scenario_receipt(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client)
    apply(cell)
    return "movements", _created(_receive(client, cell, _unique("PN"), 7)), 1


def _scenario_transfer(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    apply(target)
    return "movements", _created(_transfer(client, source, target, flow_id, pn, 10)), 1


def _scenario_transfer_from_machine(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    _created(_assign(client, source, flow_id, pn, 10))
    apply(target)
    # AREA_COMPLETED in the source Area + TRANSFERRED, one command.
    return "movements", _created(_transfer(client, source, target, flow_id, pn, 10)), 2


def _scenario_repair(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    first, second = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, first)
    _created(_transfer(client, first, second, flow_id, pn, 10))
    apply(first)
    response = _transfer(
        client, second, first, flow_id, pn, 10, repair=True, repair_reason="burr on edge"
    )
    return "movements", _created(response), 1


def _scenario_stocking(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    flow_id, pn = _release(client, material)
    apply(stockroom)
    return "movements", _created(_stock(client, material, stockroom, flow_id, pn, 10)), 1


def _scenario_assignment(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    apply(cell)
    return "movements", _created(_assign(client, cell, flow_id, pn, 10)), 1


def _scenario_partial_assignment(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    apply(cell)
    # SPLIT prefix (source + two children) + ASSIGNED_TO_MACHINE.
    return "movements", _created(_assign(client, cell, flow_id, pn, 4)), 4


def _scenario_queue(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(_assign(client, cell, flow_id, pn, 10))
    apply(cell)
    return "movements", _created(_queue(client, cell, flow_id, pn, 10)), 1


def _scenario_machine_done(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(_assign(client, cell, flow_id, pn, 10))
    apply(cell)
    return "movements", _created(_done(client, cell, flow_id, pn, 10, machine=True)), 1


def _scenario_direct_done(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client)
    flow_id, pn = _release(client, cell)
    apply(cell)
    return "movements", _created(_done(client, cell, flow_id, pn, 10, machine=False)), 1


def _scenario_merge(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client)
    first, pn = _release(client, cell)
    second, _ = _release(client, cell, part_number=pn)
    apply(cell)
    # One MERGED row per source plus the result's.
    return "movements", _created(_merge(client, cell, pn, [first, second])), 3


def _scenario_scrap(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client)
    flow_id, pn = _release(client, cell)
    apply(cell)
    return "movements", _created(_scrap(client, cell, flow_id, pn, 10)), 1


def _scenario_addition(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    cell = _Cell(client)
    _, pn = _release(client, cell)
    apply(cell)
    return "movements", _created(_add(client, cell, pn, 3)), 1


def _scenario_undo(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    transfer = _created(_transfer(client, source, target, flow_id, pn, 10))
    apply(target)
    return "movements", _created(_undo(client, target, pn, transfer)), 1


def _scenario_allocation(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    pn, demand_id = _stocked(client, material, stockroom)
    apply(stockroom)
    response = _allocate(client, pn, demand_id, 4, stockroom.station_id)
    assert response.status_code == 201, response.text
    return "allocations", str(response.json()["device_event_id"]), 1


def _scenario_allocation_reversal(client: TestClient, apply: _Apply) -> tuple[str, str, int]:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    pn, demand_id = _stocked(client, material, stockroom)
    allocated = _ok(_allocate(client, pn, demand_id, 4, stockroom.station_id), 201)
    apply(stockroom)
    response = _reverse(client, int(allocated["rows"][0]["allocation_id"]), stockroom.station_id)
    assert response.status_code == 201, response.text
    return "allocations", str(response.json()["device_event_id"]), 1


_SCENARIOS: dict[str, _Scenario] = {
    "receipt": _scenario_receipt,
    "transfer": _scenario_transfer,
    "transfer_from_machine": _scenario_transfer_from_machine,
    "repair": _scenario_repair,
    "stocking": _scenario_stocking,
    "assignment": _scenario_assignment,
    "partial_assignment": _scenario_partial_assignment,
    "queue": _scenario_queue,
    "machine_done": _scenario_machine_done,
    "direct_done": _scenario_direct_done,
    "merge": _scenario_merge,
    "scrap": _scenario_scrap,
    "addition": _scenario_addition,
    "undo": _scenario_undo,
    "allocation": _scenario_allocation,
    "allocation_reversal": _scenario_allocation_reversal,
}


@pytest.mark.parametrize("mode", ["FIXED", "DISABLED"])
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_every_station_command_records_the_station_area_identity(
    client: TestClient, db_engine: Engine, scenario: str, mode: str
) -> None:
    worker = _worker(client)
    expected = int(worker["id"]) if mode == "FIXED" else None

    def apply(cell: _Cell) -> None:
        _set_mode(client, cell.area_id, mode, expected)

    table, event_id, size = _SCENARIOS[scenario](client, apply)
    recorded = (
        _movement_workers(db_engine, event_id)
        if table == "movements"
        else _allocation_workers(db_engine, event_id)
    )
    assert recorded == [expected] * size


def test_cross_area_transfer_and_its_undo_record_the_station_area_worker(
    client: TestClient, db_engine: Engine
) -> None:
    """The station's Area decides — never the Area the rows are about."""
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    source_worker, target_worker = _worker(client), _worker(client)
    _set_mode(client, source.area_id, "FIXED", int(source_worker["id"]))
    _set_mode(client, target.area_id, "FIXED", int(target_worker["id"]))
    flow_id, pn = _release(client, source)
    _created(_assign(client, source, flow_id, pn, 10))

    # Partial transfer from ON_MACHINE quantity: SPLIT prefix, the
    # implicit AREA_COMPLETED in the SOURCE Area, then TRANSFERRED.
    transfer = _created(_transfer(client, source, target, flow_id, pn, 4))
    assert _command_movement_types(db_engine, transfer) == [
        "SPLIT",
        "SPLIT",
        "SPLIT",
        "AREA_COMPLETED",
        "TRANSFERRED",
    ]
    assert _movement_workers(db_engine, transfer) == [int(target_worker["id"])] * 5

    # Undo at the target station restores quantity into the source Area:
    # every REVERSED row still records the station Area's Worker.
    undo = _created(_undo(client, target, pn, transfer))
    assert _movement_workers(db_engine, undo) == [int(target_worker["id"])] * 5


# ---------------------------------------------------------------------------
# T-2 / T-3 — Management stays NULL; identity never changes quantity
# ---------------------------------------------------------------------------


def test_management_rows_stay_without_identity_in_a_fixed_area(
    client: TestClient, db_engine: Engine
) -> None:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    worker = _worker(client)
    _set_mode(client, material.area_id, "FIXED", int(worker["id"]))
    _set_mode(client, stockroom.area_id, "FIXED", int(worker["id"]))

    flow_id, pn = _release(client, material)
    with db_engine.connect() as connection:
        received = connection.execute(
            sa.text(
                "SELECT worker_id, station_id FROM part_movements"
                " WHERE quantity_flow_id = :flow AND movement_type = 'RECEIVED'"
            ),
            {"flow": flow_id},
        ).one()
    assert tuple(received) == (None, None)

    _created(_stock(client, material, stockroom, flow_id, pn, 10))
    demand_id = _demand(client, pn, 5)
    allocated = _ok(_allocate(client, pn, demand_id, 3, None), 201)
    assert _allocation_workers(db_engine, str(allocated["device_event_id"])) == [None]
    reversed_ = _ok(_reverse(client, int(allocated["rows"][0]["allocation_id"]), None), 201)
    assert _allocation_workers(db_engine, str(reversed_["device_event_id"])) == [None]


def _role_map(roles: dict[int, str]) -> Callable[[int | None], str | None]:
    return lambda value: None if value is None else roles[value]


def _quantity_story(client: TestClient, engine: Engine, worker_id: int | None) -> dict[str, Any]:
    """receipt → partial transfer → assign → DONE → addition + merge →
    partial scrap → Undo of the scrap, under one identity; returns the
    normalized flows, Movements and Machine projection."""
    receiving, machining = _Cell(client), _Cell(client, machine_count=1)
    mode = "FIXED" if worker_id is not None else "DISABLED"
    for cell in (receiving, machining):
        _set_mode(client, cell.area_id, mode, worker_id)
    pn = _unique("PN")
    received = _ok(_receive(client, receiving, pn, 10), 201)
    moved = _ok(_transfer(client, receiving, machining, received["quantity_flow_id"], pn, 6), 201)
    child = int(moved["quantity_flow_id"])
    remainder = int(moved["remainder_quantity_flow_id"])
    _ok(_assign(client, machining, child, pn, 6), 201)
    _ok(_done(client, machining, child, pn, 6, machine=True), 201)
    added = _ok(_add(client, receiving, pn, 2), 201)
    merged = _ok(_merge(client, receiving, pn, [remainder, int(added["quantity_flow_id"])]), 201)
    scrapped = _ok(_scrap(client, receiving, int(merged["quantity_flow_id"]), pn, 1), 201)
    _ok(_undo(client, receiving, pn, str(scrapped["device_event_id"])), 201)

    areas = _role_map({receiving.area_id: "R", machining.area_id: "M"})
    machines = _role_map({machining.machine_id: "M1"})
    operations = _role_map({receiving.operation_id: "R-OP", machining.operation_id: "M-OP"})
    stations = {receiving.station_id: "R", machining.station_id: "M"}
    with engine.connect() as connection:
        movements = list(
            connection.execute(
                sa.text("SELECT * FROM part_movements WHERE part_number = :pn ORDER BY id"),
                {"pn": pn},
            ).mappings()
        )
        flow_rows = {
            int(row["id"]): row
            for row in connection.execute(
                sa.text("SELECT * FROM quantity_flows WHERE part_number = :pn"), {"pn": pn}
            ).mappings()
        }
    flow_order: dict[int, int] = {}
    for movement in movements:
        flow_order.setdefault(int(movement["quantity_flow_id"]), len(flow_order))
    movement_index = {int(movement["id"]): index for index, movement in enumerate(movements)}
    normalized_movements = [
        (
            flow_order[int(movement["quantity_flow_id"])],
            movement["movement_type"],
            movement["quantity"],
            areas(movement["from_area_id"]),
            areas(movement["to_area_id"]),
            operations(movement["operation_id"]),
            stations.get(movement["station_id"]),
            machines(movement["source_machine_id"]),
            machines(movement["destination_machine_id"]),
            movement["command_sequence"],
            movement["movement_reason"],
            movement["reason"],
            (
                movement_index[int(movement["reverses_movement_id"])]
                if movement["reverses_movement_id"] is not None
                else None
            ),
            movement["assigned_route_step_id"],
        )
        for movement in movements
    ]
    normalized_flows = sorted(
        (
            flow_order[flow_id],
            row["quantity"],
            row["status"],
            row["route_mode"],
            areas(row["current_area_id"]),
            machines(row["current_machine_id"]),
            row["closed_at"] is None,
        )
        for flow_id, row in flow_rows.items()
    )
    machine = _ok(client.get(f"/api/machines/{machining.machine_id}"))
    recorded = {movement["worker_id"] for movement in movements if movement["station_id"]}
    return {
        "movements": normalized_movements,
        "flows": normalized_flows,
        "machine": (machine["operational_state"], machine["assigned_quantity"]),
        "workers": recorded,
    }


def test_identity_never_changes_the_quantity_effect(client: TestClient, db_engine: Engine) -> None:
    first, second = _worker(client), _worker(client)
    disabled = _quantity_story(client, db_engine, None)
    fixed_first = _quantity_story(client, db_engine, int(first["id"]))
    fixed_second = _quantity_story(client, db_engine, int(second["id"]))
    assert disabled["workers"] == {None}
    assert fixed_first["workers"] == {int(first["id"])}
    assert fixed_second["workers"] == {int(second["id"])}
    for key in ("movements", "flows", "machine"):
        assert disabled[key] == fixed_first[key] == fixed_second[key], key


# ---------------------------------------------------------------------------
# T-4 / T-5 / T-6 — idempotency
# ---------------------------------------------------------------------------


def test_committed_command_replays_with_its_recorded_identity(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    original, successor = _worker(client), _worker(client)
    _set_mode(client, cell.area_id, "FIXED", int(original["id"]))
    flow_id, pn = _release(client, cell)
    payload = _in_area_payload(flow_id, pn, 10)
    path = f"/api/scan-stations/{cell.station_id}/area-completions"
    assert client.post(path, json=payload).status_code == 201
    event_id = str(payload["device_event_id"])

    _set_mode(client, cell.area_id, "FIXED", int(successor["id"]))
    before = _counts(db_engine)
    assert client.post(path, json=payload).status_code == 200
    assert _counts(db_engine) == before
    assert _movement_workers(db_engine, event_id) == [int(original["id"])]

    # Even after the recorded Worker is no longer fixed and deactivated.
    _set_mode(client, cell.area_id, "DISABLED")
    _ok(client.patch(f"/api/workers/{original['id']}", json={"is_active": False}))
    before = _counts(db_engine)
    assert client.post(path, json=payload).status_code == 200
    assert _counts(db_engine) == before
    assert _movement_workers(db_engine, event_id) == [int(original["id"])]


def test_refused_first_attempt_records_the_identity_of_its_successful_retry(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    inactive, replacement = _worker(client), _worker(client)
    _set_mode(client, cell.area_id, "FIXED", int(inactive["id"]))
    _force_inactive(db_engine, int(inactive["id"]))
    flow_id, pn = _release(client, cell)
    payload = _in_area_payload(flow_id, pn, 10)
    path = f"/api/scan-stations/{cell.station_id}/area-completions"

    before = _counts(db_engine)
    refused = client.post(path, json=payload)
    assert refused.status_code == 409
    assert _E9_FRAGMENT in refused.json()["detail"]
    assert _counts(db_engine) == before

    _set_mode(client, cell.area_id, "FIXED", int(replacement["id"]))
    assert client.post(path, json=payload).status_code == 201
    event_id = str(payload["device_event_id"])
    assert _movement_workers(db_engine, event_id) == [int(replacement["id"])]
    after = _counts(db_engine)
    assert client.post(path, json=payload).status_code == 200
    assert _counts(db_engine) == after


def test_identity_never_causes_a_fingerprint_conflict(
    client: TestClient, db_engine: Engine
) -> None:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    payload = _transfer_payload(source, target, flow_id, pn, 10)
    path = f"/api/scan-stations/{target.station_id}/transfers"
    assert client.post(path, json=payload).status_code == 201
    _set_mode(client, target.area_id, "FIXED", int(_worker(client)["id"]))
    replay = client.post(path, json=payload)
    assert replay.status_code == 200, replay.text
    assert _movement_workers(db_engine, str(payload["device_event_id"])) == [None]


# ---------------------------------------------------------------------------
# T-7 — refusals with zero writes
# ---------------------------------------------------------------------------


def test_an_inactive_fixed_worker_refuses_every_command_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    moved, pn = _release(client, source)
    waiting, _ = _release(client, source, part_number=pn)
    queued, queued_pn = _release(client, target)
    transfer = _created(_transfer(client, source, target, moved, pn, 10))
    stock_pn, demand_id = _stocked(client, material, stockroom)
    for cell in (target, stockroom):
        _set_mode(client, cell.area_id, "FIXED", int(worker["id"]))
    _force_inactive(db_engine, int(worker["id"]))

    projections = {flow: _flow_projection(db_engine, flow) for flow in (moved, waiting, queued)}
    before = _counts(db_engine)
    attempts = {
        "assignment": _assign(client, target, queued, queued_pn, 10),
        "transfer": _transfer(client, source, target, waiting, pn, 6),
        "undo": _undo(client, target, pn, transfer),
        "allocation": _allocate(client, stock_pn, demand_id, 2, stockroom.station_id),
    }
    for name, response in attempts.items():
        assert response.status_code == 409, (name, response.text)
        detail = response.json()["detail"]
        assert detail == (
            f"The Fixed Worker '{worker['name']}' of Area"
            f" '{target.area_name if name != 'allocation' else stockroom.area_name}' is"
            " inactive, so this action cannot record its Worker. Choose an active Fixed"
            " Worker in Administration → Areas. Nothing was recorded."
        ), name
    assert _counts(db_engine) == before
    assert {flow: _flow_projection(db_engine, flow) for flow in projections} == projections


# ---------------------------------------------------------------------------
# T-9 … T-12, T-24 — concurrency
# ---------------------------------------------------------------------------


def test_a_command_waiting_on_a_deactivation_is_refused(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    worker = _worker(client)
    _set_mode(client, cell.area_id, "FIXED", int(worker["id"]))
    flow_id, pn = _release(client, cell)
    before = _counts(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": worker["id"]}
        )
        thread, results = _start(lambda: _done(client, cell, flow_id, pn, 10, machine=False))
        try:
            _assert_blocked(thread)
            holder.execute(
                sa.text("UPDATE workers SET is_active = false WHERE id = :id"),
                {"id": worker["id"]},
            )
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 409
    assert _E9_FRAGMENT in response.json()["detail"]
    assert _counts(db_engine) == before


def test_area_save_first_refuses_the_concurrent_deactivation(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    worker = _worker(client)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR SHARE"), {"id": worker["id"]}
        )
        holder.execute(
            sa.text(
                "UPDATE areas SET worker_identification_mode = 'FIXED', fixed_worker_id = :worker"
                " WHERE id = :area"
            ),
            {"worker": worker["id"], "area": cell.area_id},
        )
        thread, results = _start(
            lambda: client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
        )
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Worker '{worker['name']}' is the Fixed Worker of Area '{cell.area_name}'. Choose"
        " another Fixed Worker or Worker ID mode for that Area in Administration → Areas"
        " before deactivating this Worker."
    )
    with db_engine.connect() as connection:
        active = connection.execute(
            sa.text("SELECT is_active FROM workers WHERE id = :id"), {"id": worker["id"]}
        ).scalar_one()
    assert active is True


def test_deactivation_first_refuses_the_concurrent_area_save(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    worker = _worker(client)
    before_audit = _counts(db_engine)["audit_events"]
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": worker["id"]}
        )
        holder.execute(
            sa.text("UPDATE workers SET is_active = false WHERE id = :id"), {"id": worker["id"]}
        )
        thread, results = _start(
            lambda: client.patch(
                f"/api/areas/{cell.area_id}",
                json={"worker_identification_mode": "FIXED", "fixed_worker_id": worker["id"]},
            )
        )
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Worker '{worker['name']}' is inactive and cannot be the Fixed Worker of an Area."
        " Choose an active Worker."
    )
    areas = cast(list[dict[str, Any]], client.get("/api/areas").json())
    area = next(a for a in areas if a["id"] == cell.area_id)
    assert (area["worker_identification_mode"], area["fixed_worker_id"]) == ("DISABLED", None)
    assert _counts(db_engine)["audit_events"] == before_audit


def test_the_resolver_lock_conflicts_only_with_a_worker_write(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    worker = _worker(client)
    _set_mode(client, cell.area_id, "FIXED", int(worker["id"]))
    first, pn = _release(client, cell)
    second, _ = _release(client, cell, part_number=pn)

    # Held FOR UPDATE (a Worker write): the command waits on exactly it.
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": worker["id"]}
        )
        holder_pid = int(holder.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
        thread, results = _start(lambda: _done(client, cell, first, pn, 10, machine=False))
        try:
            _assert_blocked(thread)
            with db_engine.connect() as observer:
                waiting = observer.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity"
                        " WHERE datname = current_database()"
                        " AND :holder = ANY(pg_blocking_pids(pid))"
                        " AND wait_event_type = 'Lock'"
                    ),
                    {"holder": holder_pid},
                ).scalar_one()
            assert waiting == 1
            holder.rollback()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 201, response.text

    # Held FOR KEY SHARE (an FK check, another command): never waits.
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR KEY SHARE"), {"id": worker["id"]}
        )
        thread, results = _start(lambda: _done(client, cell, second, pn, 10, machine=False))
        try:
            response = _finish(thread, results)
        finally:
            holder.rollback()
    assert response.status_code == 201, response.text
    assert _movement_workers(db_engine, str(response.json()["device_event_id"])) == [
        int(worker["id"])
    ]


def test_deactivation_waits_for_a_command_recording_the_worker(
    client: TestClient, db_engine: Engine
) -> None:
    """The Worker write keeps FOR UPDATE: it conflicts with the resolver's
    FOR KEY SHARE (fails if `_lock_worker` is weakened)."""
    worker = _worker(client)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR KEY SHARE"), {"id": worker["id"]}
        )
        thread, results = _start(
            lambda: client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
        )
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 200, response.text
    with db_engine.connect() as connection:
        audits = connection.execute(
            sa.text(
                "SELECT count(*) FROM audit_events WHERE entity_type = 'Worker'"
                " AND entity_id = :id AND event_type = 'UPDATED'"
            ),
            {"id": str(worker["id"])},
        ).scalar_one()
    assert audits == 1


def _gate(
    monkeypatch: pytest.MonkeyPatch, entity_type: str, event_type: str
) -> tuple[threading.Event, threading.Event]:
    """Park the request that appends a matching audit row — inside its
    transaction, after its judgments and with its locks held — until
    ``release`` is set. A gate never released raises instead, so the
    parked request rolls back and never commits into a later test."""
    entered = threading.Event()
    release = threading.Event()
    append = audit.append_audit_event

    def gated(session: Session, **fields: Any) -> None:
        if fields["entity_type"] == entity_type and fields["event_type"] == event_type:
            entered.set()
            if not release.wait(timeout=30):
                raise RuntimeError("The audit gate was never released.")
        append(session, **fields)

    monkeypatch.setattr("app.application.audit.append_audit_event", gated)
    return entered, release


def _join(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=30)


def _fixed_area_body(department_id: int, worker_id: int, name: str) -> dict[str, Any]:
    return {
        "department_id": department_id,
        "name": name,
        "worker_identification_mode": "FIXED",
        "fixed_worker_id": worker_id,
    }


def test_area_create_waiting_on_a_deactivation_is_refused(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create twin of T-11: the real deactivation is in flight with its
    Worker FOR UPDATE; the real Fixed Worker create waits on its own
    Worker FOR SHARE, re-reads the Worker and is refused. Fails if the
    create path stops locking and re-reading the Worker before its
    INSERT (the INSERT's FK check alone passes on the inactive row)."""
    department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    worker = _worker(client)
    name = _unique("AREA")
    before = _counts(db_engine)
    entered, release = _gate(monkeypatch, "Worker", "UPDATED")
    threads: list[threading.Thread] = []
    deactivate, deactivated = _start(
        lambda: client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
    )
    threads.append(deactivate)
    try:
        assert entered.wait(timeout=20)
        create, created = _start(
            lambda: client.post(
                "/api/areas", json=_fixed_area_body(int(department["id"]), worker["id"], name)
            )
        )
        threads.append(create)
        _assert_blocked(create)
    finally:
        release.set()
        _join(threads)

    assert _finish(deactivate, deactivated).status_code == 200
    response = _finish(create, created)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        f"Worker '{worker['name']}' is inactive and cannot be the Fixed Worker of an Area."
        " Choose an active Worker."
    )
    with db_engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT count(*) FROM areas WHERE name = :name"), {"name": name}
        ).scalar_one()
    assert rows == 0
    after = _counts(db_engine)
    assert after["areas"] == before["areas"]
    # Only the deactivation's Worker UPDATED row: no Area CREATED row.
    assert after["audit_events"] == before["audit_events"] + 1


def test_deactivation_waiting_on_an_area_create_is_refused(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create twin of T-10 on the real route: the Fixed Worker create is
    in flight (Department, then Worker FOR SHARE, then its INSERT); the
    deactivation waits on it and then names the new Area."""
    department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    worker = _worker(client)
    name = _unique("AREA")
    entered, release = _gate(monkeypatch, "Area", "CREATED")
    threads: list[threading.Thread] = []
    create, created = _start(
        lambda: client.post(
            "/api/areas", json=_fixed_area_body(int(department["id"]), worker["id"], name)
        )
    )
    threads.append(create)
    try:
        assert entered.wait(timeout=20)
        deactivate, deactivated = _start(
            lambda: client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
        )
        threads.append(deactivate)
        _assert_blocked(deactivate)
    finally:
        release.set()
        _join(threads)

    assert _finish(create, created).status_code == 201
    response = _finish(deactivate, deactivated)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        f"Worker '{worker['name']}' is the Fixed Worker of Area '{name}'. Choose"
        " another Fixed Worker or Worker ID mode for that Area in Administration → Areas"
        " before deactivating this Worker."
    )
    with db_engine.connect() as connection:
        active = connection.execute(
            sa.text("SELECT is_active FROM workers WHERE id = :id"), {"id": worker["id"]}
        ).scalar_one()
    assert active is True


_LOCK_PROBE_TABLES = frozenset({"areas", "departments", "workers"})


def _row_unlocked(engine: Engine, table: str, row_id: int) -> bool:
    """Whether no other transaction holds any lock on the row (a
    FOR UPDATE NOWAIT probe, rolled back at once)."""
    assert table in _LOCK_PROBE_TABLES
    with engine.connect() as observer:
        try:
            observer.execute(
                sa.text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE NOWAIT"), {"id": row_id}
            )
        except sa.exc.OperationalError as error:
            if getattr(error.orig, "sqlstate", None) != "55P03":
                raise
            return False
        finally:
            observer.rollback()
    return True


class _LockCase(NamedTuple):
    """An Area save that judges a Fixed Worker, with its expected lock order."""

    send: Callable[[], Any]
    status: int
    worker_id: int
    # The row the save locks first.
    first: tuple[str, int]
    # Rows already locked while the save waits on the Worker.
    locked_before_worker: tuple[tuple[str, int], ...]
    # Rows the save locks only after the Worker.
    locked_after_worker: tuple[tuple[str, int], ...]


def _lock_case(client: TestClient, case: str) -> _LockCase:
    worker_id = int(_worker(client)["id"])
    if case == "create":
        department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        body = _fixed_area_body(int(department["id"]), worker_id, _unique("AREA"))
        department_row = ("departments", int(department["id"]))
        return _LockCase(
            send=lambda: client.post("/api/areas", json=body),
            status=201,
            worker_id=worker_id,
            first=department_row,
            locked_before_worker=(department_row,),
            locked_after_worker=(),
        )
    cell = _Cell(client)
    area_row = ("areas", cell.area_id)
    if case == "update":
        return _LockCase(
            send=lambda: client.patch(
                f"/api/areas/{cell.area_id}",
                json={"worker_identification_mode": "FIXED", "fixed_worker_id": worker_id},
            ),
            status=200,
            worker_id=worker_id,
            first=area_row,
            locked_before_worker=(area_row,),
            locked_after_worker=(),
        )
    assert case == "activation"
    _set_mode(client, cell.area_id, "FIXED", worker_id)
    _ok(client.patch(f"/api/areas/{cell.area_id}", json={"is_active": False}))
    return _LockCase(
        send=lambda: client.patch(f"/api/areas/{cell.area_id}", json={"is_active": True}),
        status=200,
        worker_id=worker_id,
        first=area_row,
        locked_before_worker=(area_row,),
        locked_after_worker=(("departments", int(cell.area["department_id"])),),
    )


_LOCK_CASES = ("create", "update", "activation")


@pytest.mark.parametrize("case", _LOCK_CASES)
def test_the_fixed_worker_is_not_locked_before_the_first_row(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    """S2c-F1 addendum lock order, first step: an Area create waits on the
    Department row, an Area update or activation on the Area row, while
    the Worker (and, for an activation, the Department) is still unlocked."""
    probe = _lock_case(client, case)
    table, row_id = probe.first
    assert table in _LOCK_PROBE_TABLES
    with db_engine.connect() as holder:
        holder.execute(sa.text(f"SELECT 1 FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id})
        thread, results = _start(probe.send)
        try:
            _assert_blocked(thread)
            assert _row_unlocked(db_engine, "workers", probe.worker_id)
            for later in probe.locked_after_worker:
                assert _row_unlocked(db_engine, *later), later
        finally:
            holder.rollback()
            _join([thread])
    response = _finish(thread, results)
    assert response.status_code == probe.status, response.text


@pytest.mark.parametrize("case", _LOCK_CASES)
def test_the_fixed_worker_is_locked_in_order(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    """S2c-F1 addendum lock order, Worker step: while the save waits on the
    Worker, a create already holds the Department and an update or
    activation the Area; an activation locks its Department only after
    the Worker (the Department lock itself is pinned by the S2c suite)."""
    probe = _lock_case(client, case)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": probe.worker_id}
        )
        thread, results = _start(probe.send)
        try:
            _assert_blocked(thread)
            for earlier in probe.locked_before_worker:
                assert not _row_unlocked(db_engine, *earlier), earlier
            for later in probe.locked_after_worker:
                assert _row_unlocked(db_engine, *later), later
        finally:
            holder.rollback()
            _join([thread])
    response = _finish(thread, results)
    assert response.status_code == probe.status, response.text


# ---------------------------------------------------------------------------
# T-13 / T-14 — station context and badge scans
# ---------------------------------------------------------------------------


def test_station_context_reports_the_worker_identification(client: TestClient) -> None:
    cell = _Cell(client)
    path = f"/api/scan-stations/{cell.station_id}/context"
    assert _ok(client.get(path))["worker_identification"] == {
        "mode": "DISABLED",
        "fixed_worker": None,
        "session": None,
    }
    worker = _worker(client)
    _set_mode(client, cell.area_id, "FIXED", int(worker["id"]))
    assert _ok(client.get(path))["worker_identification"] == {
        "mode": "FIXED",
        "fixed_worker": {"id": worker["id"], "name": worker["name"], "avatar_updated_at": None},
        "session": None,
    }


def test_badge_scans_are_a_read_that_answers_not_used_or_unknown(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    worker = _worker(client)
    inactive = _worker(client)
    _ok(client.patch(f"/api/workers/{inactive['id']}", json={"is_active": False}))
    path = f"/api/scan-stations/{cell.station_id}/badge-scans"
    badge = str(worker["badge_barcode"])

    for mode in ("DISABLED", "FIXED"):
        _set_mode(client, cell.area_id, mode, int(worker["id"]) if mode == "FIXED" else None)
        before = _counts(db_engine)
        for variant in (badge, badge.lower(), f"  {badge.lower()} "):
            assert _ok(client.post(path, json={"badge": variant})) == {
                "outcome": "NOT_USED_IN_AREA",
                "mode": mode,
                "worker_session": None,
                "previous_worker": None,
            }
        for unknown in (
            _unique("NOBODY"),
            f"PF:{badge}",
            "",
            "X" * 200,
            str(inactive["badge_barcode"]),
        ):
            assert _ok(client.post(path, json={"badge": unknown})) == {
                "outcome": "UNKNOWN",
                "mode": mode,
                "worker_session": None,
                "previous_worker": None,
            }
        assert _counts(db_engine) == before

    unknown_station = "/api/scan-stations/NO-SUCH-STATION/badge-scans"
    assert client.post(unknown_station, json={"badge": badge}).status_code == 404
    assert client.post(path, json={"badge": 5}).status_code == 422
    assert client.post(path, json={"badge": badge, "worker_id": worker["id"]}).status_code == 422
    assert client.post(path, json={}).status_code == 422
    assert _counts(db_engine) == before
    _ok(client.patch(f"/api/scan-stations/{cell.station_id}", json={"is_active": False}))
    before = _counts(db_engine)
    assert client.post(path, json={"badge": badge}).status_code == 409
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# T-15 — Undo preview
# ---------------------------------------------------------------------------


def _worker_ref(worker: dict[str, Any]) -> dict[str, Any]:
    return {"id": worker["id"], "name": worker["name"], "avatar_updated_at": None}


def test_undo_preview_names_the_original_and_the_reversing_worker(
    client: TestClient, db_engine: Engine
) -> None:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    original, reversing = _worker(client), _worker(client)
    flow_id, pn = _release(client, source)
    _set_mode(client, target.area_id, "FIXED", int(original["id"]))
    transfer = _created(_transfer(client, source, target, flow_id, pn, 10))

    _set_mode(client, target.area_id, "FIXED", int(reversing["id"]))
    preview = _preview(client, target, transfer)
    assert preview["worker"] == _worker_ref(original)
    assert preview["reversed_by"] == _worker_ref(reversing)

    _set_mode(client, target.area_id, "DISABLED")
    _ok(client.patch(f"/api/workers/{original['id']}", json={"is_active": False}))
    preview = _preview(client, target, transfer)
    # Inactive since, the original Worker is still named: it is history.
    assert preview["worker"] == _worker_ref(original)
    assert preview["reversed_by"] is None

    _set_mode(client, target.area_id, "FIXED", int(reversing["id"]))
    undo = _created(_undo(client, target, pn, transfer))
    assert _movement_workers(db_engine, undo) == [int(reversing["id"])]

    # A Disabled original names no Worker.
    other_flow, other_pn = _release(client, source)
    _set_mode(client, target.area_id, "DISABLED")
    plain = _created(_transfer(client, source, target, other_flow, other_pn, 10))
    preview = _preview(client, target, plain)
    assert preview["worker"] is None
    assert preview["reversed_by"] is None


# ---------------------------------------------------------------------------
# T-16 — PN Tracking
# ---------------------------------------------------------------------------


def _route_template(engine: Engine, area_ids: list[int]) -> int:
    with Session(engine) as session:
        template = models.RouteTemplate(name=_unique("ROUTE"))
        session.add(template)
        session.flush()
        for index, area_id in enumerate(area_ids):
            session.add(
                models.RouteStep(
                    route_template_id=template.id, sequence=(index + 1) * 10, area_id=area_id
                )
            )
        session.commit()
        return int(template.id)


def _planned_release(client: TestClient, cell: _Cell, template_id: int) -> tuple[int, str]:
    pn = _unique("PN")
    work_order = _ok(
        client.post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 50}]}
        ),
        201,
    )
    released = _ok(
        client.post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": pn,
                "quantity": 5,
                "route_mode": "PLANNED",
                "route_template_id": template_id,
                "starting_area_id": cell.area_id,
                "operation_id": cell.operation_id,
                "confirm_active_quantity": False,
                "device_event_id": _event(),
            },
        ),
        201,
    )
    return int(released["quantity_flow_id"]), pn


def _deviation_of(client: TestClient, pn: str, flow_id: int) -> dict[str, Any]:
    detail = _ok(client.get("/api/tracking/detail", params={"part_number": pn}))
    [flow] = [flow for flow in detail["flows"]["flows"] if flow["id"] == flow_id]
    [deviation] = flow["deviations"]
    return cast(dict[str, Any], deviation)


def test_tracking_names_the_recorded_worker(client: TestClient, db_engine: Engine) -> None:
    start, planned_next, fixed, disabled = (_Cell(client, machine_count=1) for _ in range(4))
    worker = _worker(client)
    _set_mode(client, fixed.area_id, "FIXED", int(worker["id"]))
    template_id = _route_template(db_engine, [start.area_id, planned_next.area_id])

    flow_id, pn = _planned_release(client, start, template_id)
    _created(
        _transfer(
            client,
            start,
            fixed,
            flow_id,
            pn,
            5,
            confirm_route_deviation=True,
            route_deviation_reason="Lathe backlog",
        )
    )
    expected = {"id": worker["id"], "name": worker["name"]}
    for response in (
        _ok(client.get("/api/tracking/detail", params={"part_number": pn}))["movements"],
        _ok(client.get("/api/tracking/movements", params={"part_number": pn})),
    ):
        by_type = {movement["movement_type"]: movement for movement in response["movements"]}
        assert by_type["TRANSFERRED"]["worker"] == expected
        # The Management release records nobody.
        assert by_type["RECEIVED"]["worker"] is None
    assert _deviation_of(client, pn, flow_id)["worker"] == expected

    other_flow, other_pn = _planned_release(client, start, template_id)
    _created(
        _transfer(
            client,
            start,
            disabled,
            other_flow,
            other_pn,
            5,
            confirm_route_deviation=True,
            route_deviation_reason="Lathe backlog",
        )
    )
    movements = _ok(client.get("/api/tracking/movements", params={"part_number": other_pn}))
    assert {movement["worker"] is None for movement in movements["movements"]} == {True}
    assert _deviation_of(client, other_pn, other_flow)["worker"] is None


# ---------------------------------------------------------------------------
# T-26 — no request carries identity
# ---------------------------------------------------------------------------

_IDENTITY_FIELDS = ("worker_id", "allocated_by_worker_id", "scan_session_id")


def _identity_free_requests(client: TestClient) -> dict[str, tuple[str, dict[str, Any]]]:
    """An otherwise valid body for each of the 12 identity-recording routes."""
    machining, receiving = _Cell(client, machine_count=1), _Cell(client)
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    flow_id, pn = _release(client, machining)
    direct_flow, direct_pn = _release(client, receiving)
    second_flow, _ = _release(client, receiving, part_number=direct_pn)
    stock_flow, stock_pn = _release(client, material)
    stocked_pn, demand_id = _stocked(client, material, stockroom)
    allocated = _ok(_allocate(client, stocked_pn, demand_id, 1, stockroom.station_id), 201)
    transfer = _created(_transfer(client, receiving, machining, direct_flow, direct_pn, 1))
    machine_station = f"/api/scan-stations/{machining.station_id}"
    receiving_station = f"/api/scan-stations/{receiving.station_id}"
    return {
        "receipts": (
            f"{receiving_station}/receipts",
            {
                "part_number": _unique("PN"),
                "quantity": 1,
                "request_type": "MODIFY",
                "route_mode": "FLOATING",
                "scanned_at": _now_iso(),
                "device_event_id": _event(),
            },
        ),
        "transfers": (
            f"{receiving_station}/transfers",
            _transfer_payload(machining, receiving, flow_id, pn, 1),
        ),
        "stockings": (
            f"/api/scan-stations/{stockroom.station_id}/stockings",
            _transfer_payload(material, stockroom, stock_flow, stock_pn, 1),
        ),
        "machine-assignments": (
            f"{machine_station}/machine-assignments",
            _in_area_payload(flow_id, pn, 1, machining.machine_id),
        ),
        "machine-releases": (
            f"{machine_station}/machine-releases",
            _in_area_payload(flow_id, pn, 1, machining.machine_id),
        ),
        "area-completions": (
            f"{receiving_station}/area-completions",
            _in_area_payload(second_flow, direct_pn, 1),
        ),
        "merges": (
            f"{receiving_station}/merges",
            {
                "part_number": direct_pn,
                "quantity_flow_ids": [direct_flow, second_flow],
                "device_event_id": _event(),
            },
        ),
        "scraps": (
            f"{receiving_station}/scraps",
            {**_in_area_payload(second_flow, direct_pn, 1), "reason": "damaged"},
        ),
        "quantity-additions": (
            f"{receiving_station}/quantity-additions",
            {
                "part_number": direct_pn,
                "quantity": 1,
                "reason": "found",
                "device_event_id": _event(),
            },
        ),
        "undos": (
            f"{machine_station}/undos",
            {
                "part_number": direct_pn,
                "reverses_device_event_id": transfer,
                "device_event_id": _event(),
            },
        ),
        "allocations": (
            "/api/allocations",
            {
                "part_number": stocked_pn,
                "allocation_quantity": 1,
                "lines": [{"work_order_demand_id": demand_id, "quantity": 1}],
                "station_id": stockroom.station_id,
                "device_event_id": _event(),
            },
        ),
        "allocation-reversals": (
            f"/api/allocations/{allocated['rows'][0]['allocation_id']}/reversals",
            {
                "reason": "wrong Work Order",
                "station_id": stockroom.station_id,
                "device_event_id": _event(),
            },
        ),
    }


def test_no_request_carries_identity(client: TestClient, db_engine: Engine) -> None:
    requests = _identity_free_requests(client)
    assert len(requests) == 12
    before = _counts(db_engine)
    for name, (path, body) in requests.items():
        for field in _IDENTITY_FIELDS:
            response = client.post(path, json={**body, field: 1})
            assert response.status_code == 422, (name, field, response.text)
    assert _counts(db_engine) == before


# ---------------------------------------------------------------------------
# T-25 — identity never feeds a business rule (static guard)
# ---------------------------------------------------------------------------

_IDENTITY_TOKENS = re.compile(
    r"\b(worker_id|allocated_by_worker_id|fixed_worker_id|worker_identification_mode)\b"
)
_IDENTITY_MODULES = {
    "station_identity.py",
    "worker_sessions.py",
    "environment.py",
    "workers.py",
    "scan_station.py",
    "undo.py",
    "tracking.py",
    "allocations.py",
}
_STAMPS = {
    "intake.py": 1,
    "transfers.py": 1,
    "direct_processing.py": 1,
    "merges.py": 1,
    "undo.py": 1,
    "machine_processing.py": 2,
    "quantity_events.py": 2,
}


def test_identity_stays_out_of_every_business_rule() -> None:
    for path in sorted(_APPLICATION_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name not in _IDENTITY_MODULES:
            assert not _IDENTITY_TOKENS.search(source), path.name
        assert source.count("stamp_movements(") == _STAMPS.get(path.name, 0) + (
            1 if path.name == "station_identity.py" else 0
        ), path.name
    allocations = (_APPLICATION_DIR / "allocations.py").read_text(encoding="utf-8")
    # Only the two row constructors name the allocation identity column.
    assert allocations.count("allocated_by_worker_id=identity.worker_id") == 2
    assert len(re.findall(r"\ballocated_by_worker_id\b", allocations)) == 3  # + docstring
    undo = (_APPLICATION_DIR / "undo.py").read_text(encoding="utf-8")
    # The Undo command never reads the original's Worker: only the preview does.
    assert len(re.findall(r"\.worker_id\b", undo)) == 1
