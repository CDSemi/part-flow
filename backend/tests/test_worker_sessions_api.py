"""Integration tests for Phase 13 slice 4 — scanned Worker Sessions and the timeout policy.

Exercises the full request path — FastAPI routes, the Application
commands, read models and configuration services, and PostgreSQL —
against a dedicated temporary database migrated to head by the real
Alembic chain (IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §9,
§8.11, §16, §19, §28; PLAN CD3, CD4, CD5, CD10; owner decision OD-2):

- badge sign-in, switch and refresh at a Scanned-session station; an
  unknown or inactive badge records and refreshes nothing; an expired
  session closes lazily as EXPIRED at its expiry;
- every Scan Station command (the 14 entry points and their variants)
  records the valid session and its Worker on every row and refreshes
  it, or is refused with 409 ``worker_session_required`` and nothing
  recorded; a committed command replays with its recorded identity and
  refreshes nothing;
- successful PN and Machine resolves refresh server-side (and commit
  only then); refusals never refresh;
- configuration closes in the same transaction (Area mode change,
  station rebind / deactivation, Worker deactivation) and the
  configuration writes that close nothing;
- the Worker sessions policy API and the per-Area override, audited;
- concurrency: every serialization the lock order promises, the
  closer-versus-closer order and the Movement-time invariant;
- static guards on the session writers and their lock order.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations/Workers; the module database is dropped afterwards.
Scanned session mode is set with SQL (``_force_scanned``): the Area
service refuses a change to it until the badge gates exist.
"""

import copy
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
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APPLICATION_DIR = _BACKEND_DIR / "app" / "application"
_TEST_DATABASE = "partflow_test_worker_sessions_api"
_DB_URL_ENV = "DATABASE_URL"

_E_S1 = (
    "No Worker is signed in at this Scan Station. Scan your badge to continue."
    " Nothing was recorded."
)
_E_S2 = "The Worker session timeout must be a whole number of minutes from 1 to 720."
_E_S3 = (
    "An Area's Worker session timeout must be a whole number of minutes from 1 to 720,"
    " or empty to use the default."
)
_FIFTEEN_MINUTES = datetime.timedelta(minutes=15)
_POLICY_PATH = "/api/policies/worker-sessions"


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
        json={"prefix": "WS-", "digits": 4},
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def default_policy(client: TestClient) -> Iterator[None]:
    """A test that changes the global default restores the approved 15 minutes."""
    yield
    _ok(client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": 15}))


# ---------------------------------------------------------------------------
# Seeding helpers (copied from the slice 3 module — each module owns its own)
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
        self.station_id = _station(client, self.area_id)
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


def _second_station(client: TestClient, cell: _Cell) -> _Cell:
    """Another Scan Station bound to ``cell``'s Area."""
    other = copy.copy(cell)
    other.station_id = _station(client, cell.area_id)
    return other


def _station(client: TestClient, area_id: int) -> str:
    station = _ok(
        client.post("/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area_id}),
        201,
    )
    return str(station["station_id"])


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


def _force_scanned(engine: Engine, area_id: int) -> None:
    """Scanned session mode the Area service refuses until S5 (fixture only)."""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE areas SET worker_identification_mode = 'SCANNED', fixed_worker_id = NULL"
                " WHERE id = :id"
            ),
            {"id": area_id},
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


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


# A station command as a (path, body) pair, so a retry resends the
# identical request.
_Request = tuple[str, dict[str, Any]]


def _transfer_request(
    source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int, **kw: Any
) -> _Request:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": _event(),
    }
    payload.update(kw)
    return f"/api/scan-stations/{target.station_id}/transfers", payload


def _stock_request(
    source: _Cell, stockroom: _Cell, flow_id: int, pn: str, quantity: int
) -> _Request:
    _, payload = _transfer_request(source, stockroom, flow_id, pn, quantity)
    return f"/api/scan-stations/{stockroom.station_id}/stockings", payload


def _in_area(
    cell: _Cell, route: str, flow_id: int, pn: str, quantity: int, machine: bool
) -> _Request:
    payload: dict[str, Any] = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "quantity": quantity,
        "device_event_id": _event(),
    }
    if machine:
        payload["machine_id"] = cell.machine_id
    return f"/api/scan-stations/{cell.station_id}/{route}", payload


def _merge_request(cell: _Cell, pn: str, flow_ids: list[int]) -> _Request:
    return f"/api/scan-stations/{cell.station_id}/merges", {
        "part_number": pn,
        "quantity_flow_ids": flow_ids,
        "device_event_id": _event(),
    }


def _scrap_request(cell: _Cell, flow_id: int, pn: str, quantity: int) -> _Request:
    path, payload = _in_area(cell, "scraps", flow_id, pn, quantity, machine=False)
    return path, {**payload, "reason": "damaged"}


def _add_request(cell: _Cell, pn: str, quantity: int) -> _Request:
    return f"/api/scan-stations/{cell.station_id}/quantity-additions", {
        "part_number": pn,
        "quantity": quantity,
        "reason": "found on the floor",
        "device_event_id": _event(),
    }


def _receive_request(cell: _Cell, pn: str, quantity: int) -> _Request:
    return f"/api/scan-stations/{cell.station_id}/receipts", {
        "part_number": pn,
        "quantity": quantity,
        "request_type": "MODIFY",
        "route_mode": "FLOATING",
        "scanned_at": _now_iso(),
        "device_event_id": _event(),
    }


def _undo_request(cell: _Cell, pn: str, reverses: str) -> _Request:
    return f"/api/scan-stations/{cell.station_id}/undos", {
        "part_number": pn,
        "reverses_device_event_id": reverses,
        "device_event_id": _event(),
    }


def _allocate_request(pn: str, demand_id: int, quantity: int, station_id: str) -> _Request:
    return "/api/allocations", {
        "part_number": pn,
        "allocation_quantity": quantity,
        "lines": [{"work_order_demand_id": demand_id, "quantity": quantity}],
        "station_id": station_id,
        "device_event_id": _event(),
    }


def _reverse_request(allocation_id: int, station_id: str) -> _Request:
    return f"/api/allocations/{allocation_id}/reversals", {
        "reason": "wrong Work Order",
        "station_id": station_id,
        "device_event_id": _event(),
    }


def _send(client: TestClient, request: _Request) -> Any:
    path, payload = request
    return client.post(path, json=payload)


def _created(client: TestClient, request: _Request) -> dict[str, Any]:
    return _ok(_send(client, request), 201)


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
    _created(client, _stock_request(material, stockroom, flow_id, pn, 10))
    return pn, _demand(client, pn, 5)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _badge_path(cell: _Cell) -> str:
    return f"/api/scan-stations/{cell.station_id}/badge-scans"


def _scan_badge(client: TestClient, cell: _Cell, badge: str) -> dict[str, Any]:
    return _ok(client.post(_badge_path(cell), json={"badge": badge}))


def _sign_in(client: TestClient, cell: _Cell, worker: dict[str, Any]) -> dict[str, Any]:
    return _scan_badge(client, cell, str(worker["badge_barcode"]))


def _scanned_cell(
    client: TestClient, engine: Engine, worker: dict[str, Any] | None = None, **kw: Any
) -> _Cell:
    """A Scanned-session cell, with ``worker`` signed in when given."""
    cell = _Cell(client, **kw)
    _force_scanned(engine, cell.area_id)
    if worker is not None:
        assert _sign_in(client, cell, worker)["outcome"] == "SIGNED_IN"
    return cell


class _SessionRow(NamedTuple):
    id: int
    station_id: str
    area_id: int
    worker_id: int
    started_at: datetime.datetime
    expires_at: datetime.datetime
    ended_at: datetime.datetime | None
    end_reason: str | None


def _sessions(engine: Engine, station_id: str) -> list[_SessionRow]:
    with engine.connect() as connection:
        return [
            _SessionRow(*row)
            for row in connection.execute(
                sa.text(
                    "SELECT id, station_id, area_id, worker_id, started_at, expires_at,"
                    " ended_at, end_reason FROM worker_sessions WHERE station_id = :station"
                    " ORDER BY id"
                ),
                {"station": station_id},
            )
        ]


def _open(engine: Engine, station_id: str) -> _SessionRow:
    rows = [row for row in _sessions(engine, station_id) if row.ended_at is None]
    assert len(rows) == 1, rows
    return rows[0]


def _session_row(engine: Engine, session_id: int) -> _SessionRow:
    with engine.connect() as connection:
        return _SessionRow(
            *connection.execute(
                sa.text(
                    "SELECT id, station_id, area_id, worker_id, started_at, expires_at,"
                    " ended_at, end_reason FROM worker_sessions WHERE id = :id"
                ),
                {"id": session_id},
            ).one()
        )


def _force_expired(engine: Engine, session_id: int) -> None:
    """An open session past its expiry (the trigger lets an open row slide)."""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE worker_sessions SET expires_at = started_at + interval '1 microsecond'"
                " WHERE id = :id"
            ),
            {"id": session_id},
        )


def _insert_expired_session(engine: Engine, cell: _Cell, worker_id: int) -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(
                sa.text(
                    "INSERT INTO worker_sessions (station_id, area_id, worker_id, started_at,"
                    " expires_at) VALUES (:station, :area, :worker, now() - interval '20 minutes',"
                    " now() - interval '5 minutes') RETURNING id"
                ),
                {"station": cell.station_id, "area": cell.area_id, "worker": worker_id},
            ).scalar_one()
        )


def _worker_ref(worker: dict[str, Any]) -> dict[str, Any]:
    return {"id": worker["id"], "name": worker["name"], "avatar_updated_at": None}


def _parse(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value)


def _movement_identity(engine: Engine, device_event_id: str) -> list[tuple[int | None, ...]]:
    with engine.connect() as connection:
        return [
            (row.worker_id, row.scan_session_id)
            for row in connection.execute(
                sa.text(
                    "SELECT worker_id, scan_session_id FROM part_movements"
                    " WHERE device_event_id = :event ORDER BY command_sequence"
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


_COUNTED_TABLES = (
    "part_movements",
    "quantity_flows",
    "work_order_allocations",
    "audit_events",
    "worker_sessions",
)


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED_TABLES
        }


def _audit_count(engine: Engine, entity_type: str, entity_id: object) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.text(
                    "SELECT count(*) FROM audit_events WHERE entity_type = :type"
                    " AND entity_id = :id"
                ),
                {"type": entity_type, "id": str(entity_id)},
            ).scalar_one()
        )


def _assert_session_required(response: Any) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": _E_S1, "worker_session_required": True}


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
# W-1 … W-5 — sign-in, refresh, switch, lazy expiry, unknown badges
# ---------------------------------------------------------------------------


def test_sign_in_opens_a_session_with_the_effective_timeout(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    answer = _sign_in(client, cell, worker)
    assert answer["outcome"] == "SIGNED_IN"
    assert answer["mode"] == "SCANNED"
    assert answer["previous_worker"] is None
    row = _open(db_engine, cell.station_id)
    assert (row.worker_id, row.area_id) == (worker["id"], cell.area_id)
    assert row.expires_at - row.started_at == _FIFTEEN_MINUTES
    session = answer["worker_session"]
    assert session["worker"] == _worker_ref(worker)
    assert (_parse(session["started_at"]), _parse(session["expires_at"])) == (
        row.started_at,
        row.expires_at,
    )
    assert _parse(session["server_now"]) == row.started_at

    context = _ok(client.get(f"/api/scan-stations/{cell.station_id}/context"))
    identification = context["worker_identification"]
    assert identification["mode"] == "SCANNED"
    assert identification["fixed_worker"] is None
    assert identification["session"]["worker"] == _worker_ref(worker)
    assert _parse(identification["session"]["expires_at"]) == row.expires_at
    assert _parse(identification["session"]["server_now"]) > row.started_at

    overridden = _scanned_cell(client, db_engine)
    _ok(
        client.patch(f"/api/areas/{overridden.area_id}", json={"worker_session_timeout_minutes": 5})
    )
    _sign_in(client, overridden, worker)
    other = _open(db_engine, overridden.station_id)
    assert other.expires_at - other.started_at == datetime.timedelta(minutes=5)
    # One Worker may hold sessions at several stations (S4-OD4).
    assert _open(db_engine, cell.station_id).id == row.id


def test_the_same_badge_refreshes_the_session(client: TestClient, db_engine: Engine) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    first = _open(db_engine, cell.station_id)
    badge = str(worker["badge_barcode"])
    answer = _scan_badge(client, cell, f"  {badge.lower()} ")
    assert answer["outcome"] == "REFRESHED"
    assert answer["previous_worker"] is None
    rows = _sessions(db_engine, cell.station_id)
    assert len(rows) == 1
    assert rows[0].id == first.id and rows[0].started_at == first.started_at
    assert rows[0].expires_at > first.expires_at
    assert _parse(answer["worker_session"]["expires_at"]) == rows[0].expires_at


def test_another_badge_switches_the_session(client: TestClient, db_engine: Engine) -> None:
    first, second = _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine, first)
    answer = _sign_in(client, cell, second)
    assert answer["outcome"] == "SWITCHED"
    assert answer["previous_worker"] == _worker_ref(first)
    assert answer["worker_session"]["worker"] == _worker_ref(second)
    old, new = _sessions(db_engine, cell.station_id)
    assert (old.worker_id, old.end_reason) == (first["id"], "SWITCHED")
    assert old.ended_at == new.started_at
    assert (new.worker_id, new.ended_at) == (second["id"], None)


def test_an_expired_session_refuses_commands_and_closes_lazily(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    flow_id, pn = _release(client, cell)
    expired = _insert_expired_session(db_engine, cell, int(worker["id"]))

    context = _ok(client.get(f"/api/scan-stations/{cell.station_id}/context"))
    assert context["worker_identification"]["session"] is None
    before = _row_counts(db_engine)
    for request in (
        _in_area(cell, "area-completions", flow_id, pn, 10, machine=False),
        _scrap_request(cell, flow_id, pn, 1),
        _add_request(cell, pn, 2),
    ):
        _assert_session_required(_send(client, request))
    assert _row_counts(db_engine) == before
    # The context read and the refusals neither closed nor moved the row.
    row = _session_row(db_engine, expired)
    assert row.ended_at is None

    answer = _sign_in(client, cell, worker)
    assert answer["outcome"] == "SIGNED_IN"
    assert answer["previous_worker"] is None
    closed = _session_row(db_engine, expired)
    assert (closed.end_reason, closed.ended_at) == ("EXPIRED", row.expires_at)
    assert _open(db_engine, cell.station_id).id != expired


def test_unknown_and_inactive_badges_record_and_refresh_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    worker, inactive = _worker(client), _worker(client)
    _ok(client.patch(f"/api/workers/{inactive['id']}", json={"is_active": False}))
    cell = _scanned_cell(client, db_engine, worker)
    row = _open(db_engine, cell.station_id)
    before = _row_counts(db_engine)
    for badge in (
        _unique("NOBODY"),
        str(inactive["badge_barcode"]),
        f"PF:{worker['badge_barcode']}",
        "",
        "X" * 200,
    ):
        answer = _scan_badge(client, cell, badge)
        assert answer["outcome"] == "UNKNOWN", badge
        assert answer["mode"] == "SCANNED"
        assert answer["previous_worker"] is None
        assert answer["worker_session"]["worker"] == _worker_ref(worker)
    assert _row_counts(db_engine) == before
    assert _sessions(db_engine, cell.station_id) == [row]


# ---------------------------------------------------------------------------
# W-6 / W-7 — every station command records the session, or is refused
# ---------------------------------------------------------------------------


class _Command(NamedTuple):
    cell: _Cell  # the station's cell
    rows: str  # "movements" or "allocations"
    request: _Request
    size: int  # rows the command appends


def _scenario_receipt(client: TestClient) -> _Command:
    cell = _Cell(client)
    return _Command(cell, "movements", _receive_request(cell, _unique("PN"), 7), 1)


def _scenario_transfer(client: TestClient) -> _Command:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    return _Command(target, "movements", _transfer_request(source, target, flow_id, pn, 10), 1)


def _scenario_transfer_from_machine(client: TestClient) -> _Command:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    _created(client, _in_area(source, "machine-assignments", flow_id, pn, 10, machine=True))
    # AREA_COMPLETED in the source Area + TRANSFERRED, one command.
    return _Command(target, "movements", _transfer_request(source, target, flow_id, pn, 10), 2)


def _scenario_repair(client: TestClient) -> _Command:
    first, second = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, first)
    _created(client, _transfer_request(first, second, flow_id, pn, 10))
    request = _transfer_request(
        second, first, flow_id, pn, 10, repair=True, repair_reason="burr on edge"
    )
    return _Command(first, "movements", request, 1)


def _scenario_stocking(client: TestClient) -> _Command:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    flow_id, pn = _release(client, material)
    return _Command(stockroom, "movements", _stock_request(material, stockroom, flow_id, pn, 10), 1)


def _scenario_assignment(client: TestClient) -> _Command:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    request = _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True)
    return _Command(cell, "movements", request, 1)


def _scenario_partial_assignment(client: TestClient) -> _Command:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    # SPLIT prefix (source + two children) + ASSIGNED_TO_MACHINE.
    request = _in_area(cell, "machine-assignments", flow_id, pn, 4, machine=True)
    return _Command(cell, "movements", request, 4)


def _scenario_queue(client: TestClient) -> _Command:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(client, _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True))
    request = _in_area(cell, "machine-releases", flow_id, pn, 10, machine=True)
    return _Command(cell, "movements", request, 1)


def _scenario_machine_done(client: TestClient) -> _Command:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(client, _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True))
    request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=True)
    return _Command(cell, "movements", request, 1)


def _scenario_direct_done(client: TestClient) -> _Command:
    cell = _Cell(client)
    flow_id, pn = _release(client, cell)
    request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=False)
    return _Command(cell, "movements", request, 1)


def _scenario_merge(client: TestClient) -> _Command:
    cell = _Cell(client)
    first, pn = _release(client, cell)
    second, _ = _release(client, cell, part_number=pn)
    # One MERGED row per source plus the result's.
    return _Command(cell, "movements", _merge_request(cell, pn, [first, second]), 3)


def _scenario_scrap(client: TestClient) -> _Command:
    cell = _Cell(client)
    flow_id, pn = _release(client, cell)
    return _Command(cell, "movements", _scrap_request(cell, flow_id, pn, 10), 1)


def _scenario_addition(client: TestClient) -> _Command:
    cell = _Cell(client)
    _, pn = _release(client, cell)
    return _Command(cell, "movements", _add_request(cell, pn, 3), 1)


def _scenario_undo(client: TestClient) -> _Command:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    transfer = _created(client, _transfer_request(source, target, flow_id, pn, 10))
    request = _undo_request(target, pn, str(transfer["device_event_id"]))
    return _Command(target, "movements", request, 1)


def _scenario_allocation(client: TestClient) -> _Command:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    pn, demand_id = _stocked(client, material, stockroom)
    request = _allocate_request(pn, demand_id, 4, stockroom.station_id)
    return _Command(stockroom, "allocations", request, 1)


def _scenario_allocation_reversal(client: TestClient) -> _Command:
    material, stockroom = _Cell(client, machine_count=1), _Cell(client, is_terminal=True)
    pn, demand_id = _stocked(client, material, stockroom)
    allocated = _created(client, _allocate_request(pn, demand_id, 4, stockroom.station_id))
    request = _reverse_request(int(allocated["rows"][0]["allocation_id"]), stockroom.station_id)
    return _Command(stockroom, "allocations", request, 1)


_SCENARIOS: dict[str, Callable[[TestClient], _Command]] = {
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


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_every_station_command_records_and_refreshes_the_valid_session(
    client: TestClient, db_engine: Engine, scenario: str
) -> None:
    command_ = _SCENARIOS[scenario](client)
    worker = _worker(client)
    _force_scanned(db_engine, command_.cell.area_id)
    _sign_in(client, command_.cell, worker)
    before = _open(db_engine, command_.cell.station_id)

    response = _send(client, command_.request)
    assert response.status_code == 201, response.text
    event_id = str(command_.request[1]["device_event_id"])
    if command_.rows == "movements":
        assert (
            _movement_identity(db_engine, event_id)
            == [(int(worker["id"]), before.id)] * command_.size
        )
    else:
        assert _allocation_workers(db_engine, event_id) == [int(worker["id"])] * command_.size
    after = _open(db_engine, command_.cell.station_id)
    assert after.id == before.id
    assert after.expires_at > before.expires_at


@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_every_station_command_without_a_session_is_refused_with_zero_writes(
    client: TestClient, db_engine: Engine, scenario: str
) -> None:
    command_ = _SCENARIOS[scenario](client)
    _force_scanned(db_engine, command_.cell.area_id)
    before = _row_counts(db_engine)
    _assert_session_required(_send(client, command_.request))
    assert _row_counts(db_engine) == before


# ---------------------------------------------------------------------------
# W-8 — the retry race (PLAN S4)
# ---------------------------------------------------------------------------


def test_a_committed_command_replays_with_its_session_and_refreshes_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    first, second = _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine, first)
    flow_id, pn = _release(client, cell)
    request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=False)
    event_id = str(request[1]["device_event_id"])
    assert _send(client, request).status_code == 201
    original = _open(db_engine, cell.station_id)
    assert _movement_identity(db_engine, event_id) == [(int(first["id"]), original.id)]

    _force_expired(db_engine, original.id)
    assert _sign_in(client, cell, second)["outcome"] == "SIGNED_IN"
    successor = _open(db_engine, cell.station_id)
    counts = _row_counts(db_engine)
    assert _send(client, request).status_code == 200
    assert _row_counts(db_engine) == counts
    assert _movement_identity(db_engine, event_id) == [(int(first["id"]), original.id)]
    assert _open(db_engine, cell.station_id) == successor


def test_a_refused_first_attempt_records_the_session_of_its_successful_retry(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    flow_id, pn = _release(client, cell)
    request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=False)
    event_id = str(request[1]["device_event_id"])
    _assert_session_required(_send(client, request))

    _sign_in(client, cell, worker)
    session = _open(db_engine, cell.station_id)
    assert _send(client, request).status_code == 201
    assert _movement_identity(db_engine, event_id) == [(int(worker["id"]), session.id)]
    counts = _row_counts(db_engine)
    assert _send(client, request).status_code == 200
    assert _row_counts(db_engine) == counts


# ---------------------------------------------------------------------------
# W-9 / W-10 — the sliding timeout and the resolve refresh
# ---------------------------------------------------------------------------


def _resolve(client: TestClient, cell: _Cell, pn: str) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/scans/resolve", json={"barcode": f"PF:PN:{pn}"}
    )


def _resolve_machine(client: TestClient, cell: _Cell, asset_tag: str) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/machine-scans/resolve",
        json={"asset_tag": asset_tag},
    )


def _asset_tag(client: TestClient, machine_id: int) -> str:
    return str(_ok(client.get(f"/api/machines/{machine_id}"))["asset_tag"])


def test_the_timeout_follows_the_override_else_the_default(
    client: TestClient, db_engine: Engine, default_policy: None
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    _, pn = _release(client, cell)
    area_path = f"/api/areas/{cell.area_id}"

    opened = _open(db_engine, cell.station_id)
    _ok(client.patch(area_path, json={"worker_session_timeout_minutes": 1}))
    # A configuration change never moves an open session.
    assert _open(db_engine, cell.station_id) == opened
    session = _ok(_resolve(client, cell, pn))["worker_session"]
    assert _parse(session["expires_at"]) - _parse(session["server_now"]) == datetime.timedelta(
        minutes=1
    )

    refreshed = _open(db_engine, cell.station_id)
    _ok(client.patch(area_path, json={"worker_session_timeout_minutes": None}))
    _ok(client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": 30}))
    assert _open(db_engine, cell.station_id) == refreshed
    session = _ok(_resolve(client, cell, pn))["worker_session"]
    assert _parse(session["expires_at"]) - _parse(session["server_now"]) == datetime.timedelta(
        minutes=30
    )


def _counting_commits() -> tuple[list[int], Callable[[Session], None]]:
    commits: list[int] = []

    def listener(session: Session) -> None:
        commits.append(1)

    return commits, listener


def _without_volatile(body: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in body.items() if key not in {"worker_session", "scanned_at"}
    }


def test_successful_resolves_refresh_and_refusals_never_do(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker, machine_count=1)
    other = _Cell(client, machine_count=1)
    _, pn = _release(client, cell)
    tag = _asset_tag(client, cell.machine_id)
    commits, listener = _counting_commits()
    event.listen(Session, "after_commit", listener)
    try:
        refreshing: list[Callable[[], Any]] = [
            lambda: _resolve(client, cell, pn),
            lambda: _resolve(client, cell, _unique("NOPN")),  # NO_TRANSFERABLE_QUANTITY
            lambda: _resolve_machine(client, cell, tag),
        ]
        for send in refreshing:
            before = _open(db_engine, cell.station_id)
            commits.clear()
            body = _ok(send())
            after = _open(db_engine, cell.station_id)
            assert after.expires_at > before.expires_at
            assert _parse(body["worker_session"]["expires_at"]) == after.expires_at
            assert body["worker_session"]["worker"] == _worker_ref(worker)
            assert len(commits) == 1

        unchanged = _open(db_engine, cell.station_id)
        for send, status in (
            (
                lambda: client.post(
                    f"/api/scan-stations/{cell.station_id}/scans/resolve",
                    json={"barcode": "NOT-A-PN-BARCODE"},
                ),
                422,
            ),
            (lambda: _resolve_machine(client, cell, _unique("NO-TAG")), 404),
            (lambda: _resolve_machine(client, cell, _asset_tag(client, other.machine_id)), 409),
        ):
            commits.clear()
            assert send().status_code == status
            assert commits == []
        assert _open(db_engine, cell.station_id) == unchanged

        # A Disabled / Fixed Worker station never refreshes or commits.
        disabled = _Cell(client)
        _, disabled_pn = _release(client, disabled)
        fixed = _Cell(client)
        _set_mode(client, fixed.area_id, "FIXED", int(worker["id"]))
        for plain in (disabled, fixed):
            commits.clear()
            body = _ok(_resolve(client, plain, disabled_pn))
            assert body["worker_session"] is None
            assert commits == []

        # The same resolves without a valid session (expired): 200, no write.
        with_session = _ok(_resolve(client, cell, pn))
        with_session_machine = _ok(_resolve_machine(client, cell, tag))
        _force_expired(db_engine, unchanged.id)
        expired = _session_row(db_engine, unchanged.id)
        commits.clear()
        without_session = _ok(_resolve(client, cell, pn))
        without_session_machine = _ok(_resolve_machine(client, cell, tag))
        assert commits == []
        assert without_session["worker_session"] is None
        assert without_session_machine["worker_session"] is None
        assert _session_row(db_engine, unchanged.id) == expired
    finally:
        event.remove(Session, "after_commit", listener)
    # The refresh commit never changes the answer itself.
    assert _without_volatile(with_session) == _without_volatile(without_session)
    assert _without_volatile(with_session_machine) == _without_volatile(without_session_machine)


# ---------------------------------------------------------------------------
# W-11 / W-12 — configuration closes and non-closes
# ---------------------------------------------------------------------------


def test_leaving_scanned_mode_closes_the_area_sessions(
    client: TestClient, db_engine: Engine
) -> None:
    first, second, third = _worker(client), _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine, first)
    second_station = _second_station(client, cell)
    third_station = _second_station(client, cell)
    _sign_in(client, second_station, second)
    expired = _insert_expired_session(db_engine, third_station, int(third["id"]))
    expired_row = _session_row(db_engine, expired)
    audits = _audit_count(db_engine, "Area", cell.area_id)

    _set_mode(client, cell.area_id, "DISABLED")
    for station in (cell, second_station):
        (row,) = _sessions(db_engine, station.station_id)
        assert row.end_reason == "AREA_MODE_CHANGED"
        assert row.ended_at is not None and row.ended_at < row.expires_at
    closed = _session_row(db_engine, expired)
    assert (closed.end_reason, closed.ended_at) == ("EXPIRED", expired_row.expires_at)
    assert _audit_count(db_engine, "Area", cell.area_id) == audits + 1


def test_a_station_rebind_or_deactivation_closes_its_session(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    rebound = _scanned_cell(client, db_engine, worker)
    elsewhere = _Cell(client)
    audits = _audit_count(db_engine, "ScanStation", rebound.station_id)
    _ok(
        client.patch(
            f"/api/scan-stations/{rebound.station_id}", json={"area_id": elsewhere.area_id}
        )
    )
    (row,) = _sessions(db_engine, rebound.station_id)
    assert row.end_reason == "STATION_CHANGED"
    assert _audit_count(db_engine, "ScanStation", rebound.station_id) == audits + 1

    deactivated = _scanned_cell(client, db_engine, worker)
    _ok(client.patch(f"/api/scan-stations/{deactivated.station_id}", json={"is_active": False}))
    (row,) = _sessions(db_engine, deactivated.station_id)
    assert row.end_reason == "STATION_CHANGED"


def test_a_worker_deactivation_closes_the_worker_sessions(
    client: TestClient, db_engine: Engine
) -> None:
    worker, bystander = _worker(client), _worker(client)
    first = _scanned_cell(client, db_engine, worker)
    second = _scanned_cell(client, db_engine, worker)
    third = _scanned_cell(client, db_engine, bystander)
    untouched = _open(db_engine, third.station_id)
    audits = _audit_count(db_engine, "Worker", worker["id"])
    _ok(client.patch(f"/api/workers/{worker['id']}", json={"is_active": False}))
    for cell in (first, second):
        (row,) = _sessions(db_engine, cell.station_id)
        assert row.end_reason == "WORKER_DEACTIVATED"
    assert _open(db_engine, third.station_id) == untouched
    assert _audit_count(db_engine, "Worker", worker["id"]) == audits + 1


def test_other_configuration_writes_close_nothing(
    client: TestClient, db_engine: Engine, default_policy: None
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    other = _scanned_cell(client, db_engine, worker)
    _ok(client.patch(f"/api/scan-stations/{other.station_id}", json={"is_active": False}))
    rows = {
        station: _sessions(db_engine, station) for station in (cell.station_id, other.station_id)
    }

    area_path = f"/api/areas/{cell.area_id}"
    _ok(client.patch(area_path, json={"name": _unique("RENAMED")}))
    _ok(client.patch(area_path, json={"worker_session_timeout_minutes": 7}))
    _ok(client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": 20}))
    _ok(client.patch(f"/api/scan-stations/{other.station_id}", json={"is_active": True}))
    _ok(client.patch(f"/api/workers/{worker['id']}", json={"name": _unique("Renamed")}))
    # Refused configuration writes close nothing either: the Worker is the
    # Fixed Worker of another Area (S3), and a rebind into an inactive Area (S2c).
    fixed_area = _Cell(client)
    _set_mode(client, fixed_area.area_id, "FIXED", int(worker["id"]))
    deactivate = client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
    assert deactivate.status_code == 409
    inactive_area = _Cell(client)
    _ok(client.patch(f"/api/areas/{inactive_area.area_id}", json={"is_active": False}))
    rebind = client.patch(
        f"/api/scan-stations/{cell.station_id}", json={"area_id": inactive_area.area_id}
    )
    assert rebind.status_code == 409
    # An Area deactivation closes nothing (S4-OD5).
    _ok(client.patch(area_path, json={"is_active": False}))
    assert {
        station: _sessions(db_engine, station) for station in (cell.station_id, other.station_id)
    } == rows


# ---------------------------------------------------------------------------
# W-13 / W-14 — Undo preview and the quantity effect
# ---------------------------------------------------------------------------


def test_undo_preview_names_the_valid_session_worker(client: TestClient, db_engine: Engine) -> None:
    original, reversing = _worker(client), _worker(client)
    source = _Cell(client, machine_count=1)
    target = _scanned_cell(client, db_engine, original, machine_count=1)
    flow_id, pn = _release(client, source)
    transfer = _created(client, _transfer_request(source, target, flow_id, pn, 10))
    reverses = str(transfer["device_event_id"])
    preview_path = f"/api/scan-stations/{target.station_id}/undo-preview/{reverses}"

    _sign_in(client, target, reversing)
    preview = _ok(client.get(preview_path))
    assert preview["worker"] == _worker_ref(original)
    assert preview["reversed_by"] == _worker_ref(reversing)

    session = _open(db_engine, target.station_id)
    _force_expired(db_engine, session.id)
    preview = _ok(client.get(preview_path))
    assert preview["worker"] == _worker_ref(original)
    assert preview["reversed_by"] is None
    # A read: the preview neither closed nor refreshed the expired row.
    assert _session_row(db_engine, session.id).ended_at is None

    _sign_in(client, target, reversing)
    confirmed = _open(db_engine, target.station_id)
    undo = _created(client, _undo_request(target, pn, reverses))
    assert _movement_identity(db_engine, str(undo["device_event_id"])) == [
        (int(reversing["id"]), confirmed.id)
    ]


def _role_map(roles: dict[int, str]) -> Callable[[int | None], str | None]:
    return lambda value: None if value is None else roles[value]


def _quantity_story(
    client: TestClient, engine: Engine, identify: Callable[[_Cell], None]
) -> dict[str, Any]:
    """receipt → partial transfer → assign → DONE → addition + merge →
    partial scrap → Undo of the scrap, under one identity; returns the
    normalized flows, Movements and Machine projection."""
    receiving, machining = _Cell(client), _Cell(client, machine_count=1)
    for cell in (receiving, machining):
        identify(cell)
    pn = _unique("PN")
    received = _created(client, _receive_request(receiving, pn, 10))
    moved = _created(
        client, _transfer_request(receiving, machining, received["quantity_flow_id"], pn, 6)
    )
    child = int(moved["quantity_flow_id"])
    remainder = int(moved["remainder_quantity_flow_id"])
    _created(client, _in_area(machining, "machine-assignments", child, pn, 6, machine=True))
    _created(client, _in_area(machining, "area-completions", child, pn, 6, machine=True))
    added = _created(client, _add_request(receiving, pn, 2))
    merged = _created(
        client, _merge_request(receiving, pn, [remainder, int(added["quantity_flow_id"])])
    )
    scrapped = _created(client, _scrap_request(receiving, int(merged["quantity_flow_id"]), pn, 1))
    _created(client, _undo_request(receiving, pn, str(scrapped["device_event_id"])))

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
    recorded = {
        (movement["worker_id"], movement["scan_session_id"] is not None)
        for movement in movements
        if movement["station_id"]
    }
    return {
        "movements": normalized_movements,
        "flows": normalized_flows,
        "machine": (machine["operational_state"], machine["assigned_quantity"]),
        "identity": recorded,
    }


def test_a_session_never_changes_the_quantity_effect(client: TestClient, db_engine: Engine) -> None:
    fixed_worker, scanned_worker = _worker(client), _worker(client)

    def disabled(cell: _Cell) -> None:
        return None

    def fixed(cell: _Cell) -> None:
        _set_mode(client, cell.area_id, "FIXED", int(fixed_worker["id"]))

    def scanned(cell: _Cell) -> None:
        _force_scanned(db_engine, cell.area_id)
        _sign_in(client, cell, scanned_worker)

    stories = [
        _quantity_story(client, db_engine, identify) for identify in (disabled, fixed, scanned)
    ]
    assert [story["identity"] for story in stories] == [
        {(None, False)},
        {(int(fixed_worker["id"]), False)},
        {(int(scanned_worker["id"]), True)},
    ]
    for key in ("movements", "flows", "machine"):
        assert stories[0][key] == stories[1][key] == stories[2][key], key


# ---------------------------------------------------------------------------
# W-15 … W-21, W-31 … W-35 — concurrency
# ---------------------------------------------------------------------------


def test_a_command_waiting_on_a_session_close_is_refused(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    flow_id, pn = _release(client, cell)
    session = _open(db_engine, cell.station_id)
    before = _row_counts(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(
                "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                " end_reason = 'STATION_CHANGED' WHERE id = :id"
            ),
            {"id": session.id},
        )
        request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=False)
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    _assert_session_required(response)
    assert _row_counts(db_engine) == before


def test_a_sign_in_waits_for_a_command_holding_the_station(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    with db_engine.connect() as holder:
        # The allocation path's station lock.
        holder.execute(
            sa.text("SELECT 1 FROM scan_stations WHERE station_id = :id FOR KEY SHARE"),
            {"id": cell.station_id},
        )
        thread, results = _start(lambda: _sign_in(client, cell, worker))
        try:
            _assert_blocked(thread)
            holder.rollback()
        finally:
            answer = _finish(thread, results)
    assert answer["outcome"] == "SIGNED_IN"


def test_a_command_waiting_on_a_worker_deactivation_is_refused(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    flow_id, pn = _release(client, cell)
    session = _open(db_engine, cell.station_id)
    before = _row_counts(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": worker["id"]}
        )
        holder.execute(
            sa.text("UPDATE workers SET is_active = false WHERE id = :id"), {"id": worker["id"]}
        )
        holder.execute(
            sa.text(
                "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                " end_reason = 'WORKER_DEACTIVATED' WHERE id = :id"
            ),
            {"id": session.id},
        )
        request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=False)
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    _assert_session_required(response)
    assert _row_counts(db_engine) == before


def test_a_sign_in_waiting_on_an_area_mode_change_answers_under_the_new_mode(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM areas WHERE id = :id FOR NO KEY UPDATE"), {"id": cell.area_id}
        )
        thread, results = _start(lambda: _sign_in(client, cell, worker))
        try:
            _assert_blocked(thread)
            holder.execute(
                sa.text("UPDATE areas SET worker_identification_mode = 'DISABLED' WHERE id = :id"),
                {"id": cell.area_id},
            )
            holder.commit()
        finally:
            answer = _finish(thread, results)
    assert answer == {
        "outcome": "NOT_USED_IN_AREA",
        "mode": "DISABLED",
        "worker_session": None,
        "previous_worker": None,
    }
    assert _sessions(db_engine, cell.station_id) == []


def _held(
    client: TestClient, engine: Engine, mode: str, area_id: int, request: _Request
) -> tuple[bool, Any]:
    """Send ``request`` while a raw transaction holds the Area ``FOR {mode}``;
    returns whether it was blocked, and its response after the rollback."""
    with engine.connect() as holder:
        holder.execute(sa.text(f"SELECT 1 FROM areas WHERE id = :id FOR {mode}"), {"id": area_id})
        thread, results = _start(lambda: _send(client, request))
        try:
            thread.join(timeout=0.5)
            blocked = thread.is_alive()
            holder.rollback()
        finally:
            response = _finish(thread, results)
    return blocked, response


def test_command_area_lock_modes_per_route_group(client: TestClient, db_engine: Engine) -> None:
    worker = _worker(client)
    # (a) Group B: the station Area is only KEY SHARE-locked by the command.
    cell = _scanned_cell(client, db_engine, worker, machine_count=1)
    direct = _scanned_cell(client, db_engine, worker)
    for mode, expect_blocked in (("NO KEY UPDATE", False), ("UPDATE", True)):
        flow_id, pn = _release(client, direct)
        done = _in_area(direct, "area-completions", flow_id, pn, 10, machine=False)
        blocked, response = _held(client, db_engine, mode, direct.area_id, done)
        assert (blocked, response.status_code) == (expect_blocked, 201), mode
        queued, queued_pn = _release(client, cell)
        _created(client, _in_area(cell, "machine-assignments", queued, queued_pn, 10, True))
        queue = _in_area(cell, "machine-releases", queued, queued_pn, 10, machine=True)
        blocked, response = _held(client, db_engine, mode, cell.area_id, queue)
        assert (blocked, response.status_code) == (expect_blocked, 201), mode

    # (b) Group A: the command holds the station Area FOR UPDATE.
    source = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    for request in (
        _transfer_request(source, cell, flow_id, pn, 10),
        _receive_request(cell, _unique("PN"), 3),
    ):
        blocked, response = _held(client, db_engine, "NO KEY UPDATE", cell.area_id, request)
        assert (blocked, response.status_code) == (True, 201)

    # (c) Allocation: a new KEY SHARE wait on the Stockroom Area.
    material = _Cell(client, machine_count=1)
    stockroom = _scanned_cell(client, db_engine, worker, is_terminal=True)
    for mode, expect_blocked in (("UPDATE", True), ("NO KEY UPDATE", False)):
        stock_pn, demand_id = _stocked(client, material, stockroom)
        request = _allocate_request(stock_pn, demand_id, 2, stockroom.station_id)
        blocked, response = _held(client, db_engine, mode, stockroom.area_id, request)
        assert (blocked, response.status_code) == (expect_blocked, 201), mode


def test_concurrent_sign_ins_leave_one_open_session(client: TestClient, db_engine: Engine) -> None:
    first, second = _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine)
    barrier = threading.Barrier(2)

    def sign_in(worker: dict[str, Any]) -> Callable[[], Any]:
        def send() -> Any:
            barrier.wait()
            return client.post(_badge_path(cell), json={"badge": worker["badge_barcode"]})

        return send

    threads = [_start(sign_in(first)), _start(sign_in(second))]
    responses = [_finish(thread, results) for thread, results in threads]
    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()["outcome"] for response in responses) == [
        "SIGNED_IN",
        "SWITCHED",
    ]
    rows = _sessions(db_engine, cell.station_id)
    assert [row.end_reason for row in rows] == ["SWITCHED", None]


def test_an_area_deactivation_and_mode_change_waits_for_a_command_without_deadlock(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    session = _open(db_engine, cell.station_id)
    with db_engine.connect() as holder:
        # A group-B command between its refresh and its flush.
        holder.execute(
            sa.text("SELECT 1 FROM areas WHERE id = :id FOR KEY SHARE"), {"id": cell.area_id}
        )
        holder.execute(
            sa.text("SELECT 1 FROM worker_sessions WHERE id = :id FOR NO KEY UPDATE"),
            {"id": session.id},
        )
        thread, results = _start(
            lambda: client.patch(
                f"/api/areas/{cell.area_id}",
                json={"is_active": False, "worker_identification_mode": "DISABLED"},
            )
        )
        try:
            _assert_blocked(thread)
            with db_engine.connect() as probe:
                waiting = list(
                    probe.execute(
                        sa.text(
                            "SELECT query FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                            " AND datname = current_database()"
                        )
                    ).scalars()
                )
            assert len(waiting) == 1
            assert "FROM areas" in waiting[0]
            assert "worker_sessions" not in waiting[0]
            holder.rollback()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 200, response.text
    assert _session_row(db_engine, session.id).end_reason == "AREA_MODE_CHANGED"


def test_closers_of_the_same_sessions_never_deadlock(client: TestClient, db_engine: Engine) -> None:
    for _ in range(20):
        worker = _worker(client)
        cell = _scanned_cell(client, db_engine, worker)
        second = _second_station(client, cell)
        _sign_in(client, second, worker)
        rows = [_open(db_engine, cell.station_id), _open(db_engine, second.station_id)]
        audits = (
            _audit_count(db_engine, "Area", cell.area_id),
            _audit_count(db_engine, "Worker", worker["id"]),
        )
        barrier = threading.Barrier(2)

        def area_patch(area_id: int = cell.area_id, barrier: Any = barrier) -> Any:
            barrier.wait()
            return client.patch(
                f"/api/areas/{area_id}", json={"worker_identification_mode": "DISABLED"}
            )

        def worker_patch(worker_id: int = int(worker["id"]), barrier: Any = barrier) -> Any:
            barrier.wait()
            return client.patch(f"/api/workers/{worker_id}", json={"is_active": False})

        threads = [_start(area_patch), _start(worker_patch)]
        responses = [_finish(thread, results) for thread, results in threads]
        assert [response.status_code for response in responses] == [200, 200], [
            response.text for response in responses
        ]
        for row in rows:
            closed = _session_row(db_engine, row.id)
            assert closed.end_reason in {"AREA_MODE_CHANGED", "WORKER_DEACTIVATED"}
        assert (
            _audit_count(db_engine, "Area", cell.area_id),
            _audit_count(db_engine, "Worker", worker["id"]),
        ) == (audits[0] + 1, audits[1] + 1)


def test_closers_wait_in_id_order_behind_a_held_session(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    second = _second_station(client, cell)
    _sign_in(client, second, worker)
    higher = max(_open(db_engine, cell.station_id).id, _open(db_engine, second.station_id).id)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM worker_sessions WHERE id = :id FOR NO KEY UPDATE"),
            {"id": higher},
        )
        threads = [
            _start(
                lambda: client.patch(
                    f"/api/areas/{cell.area_id}", json={"worker_identification_mode": "DISABLED"}
                )
            ),
            _start(lambda: client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})),
        ]
        try:
            for thread, _ in threads:
                _assert_blocked(thread)
            holder.rollback()
        finally:
            responses = [_finish(thread, results) for thread, results in threads]
    assert [response.status_code for response in responses] == [200, 200]
    assert _sessions(db_engine, cell.station_id)[0].ended_at is not None
    assert _sessions(db_engine, second.station_id)[0].ended_at is not None


def test_a_movement_may_precede_its_session_start(client: TestClient, db_engine: Engine) -> None:
    """``scan_session_id`` is the link — never the time window (§3.1)."""
    first, second = _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine, first)
    flow_id, pn = _release(client, cell)
    request = _in_area(cell, "area-completions", flow_id, pn, 10, machine=False)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM scan_stations WHERE station_id = :id FOR UPDATE"),
            {"id": cell.station_id},
        )
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            # A sign-in of the second Worker, emulated under the station lock.
            holder.execute(
                sa.text(
                    "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                    " end_reason = 'SWITCHED' WHERE station_id = :station AND ended_at IS NULL"
                ),
                {"station": cell.station_id},
            )
            holder.execute(
                sa.text(
                    "INSERT INTO worker_sessions (station_id, area_id, worker_id, started_at,"
                    " expires_at) VALUES (:station, :area, :worker, clock_timestamp(),"
                    " clock_timestamp() + interval '15 minutes')"
                ),
                {"station": cell.station_id, "area": cell.area_id, "worker": second["id"]},
            )
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 201, response.text
    successor = _open(db_engine, cell.station_id)
    event_id = str(request[1]["device_event_id"])
    assert _movement_identity(db_engine, event_id) == [(int(second["id"]), successor.id)]
    with db_engine.connect() as connection:
        occurred_at = connection.execute(
            sa.text("SELECT occurred_at FROM part_movements WHERE device_event_id = :event"),
            {"event": event_id},
        ).scalar_one()
    assert occurred_at < successor.started_at


def test_an_allocation_and_a_station_deactivation_serialize_on_the_session(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    material = _Cell(client, machine_count=1)
    # (a) The deactivation holds the session first: the allocation is refused.
    stockroom = _scanned_cell(client, db_engine, worker, is_terminal=True)
    pn, demand_id = _stocked(client, material, stockroom)
    session = _open(db_engine, stockroom.station_id)
    before = _row_counts(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM scan_stations WHERE station_id = :id FOR NO KEY UPDATE"),
            {"id": stockroom.station_id},
        )
        holder.execute(
            sa.text("UPDATE scan_stations SET is_active = false WHERE station_id = :id"),
            {"id": stockroom.station_id},
        )
        holder.execute(
            sa.text(
                "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                " end_reason = 'STATION_CHANGED' WHERE id = :id"
            ),
            {"id": session.id},
        )
        request = _allocate_request(pn, demand_id, 2, stockroom.station_id)
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    _assert_session_required(response)
    assert _row_counts(db_engine) == before

    # (b) The allocation holds the session first: the deactivation closes after it.
    stockroom = _scanned_cell(client, db_engine, worker, is_terminal=True)
    session = _open(db_engine, stockroom.station_id)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM scan_stations WHERE station_id = :id FOR KEY SHARE"),
            {"id": stockroom.station_id},
        )
        holder.execute(
            sa.text("SELECT 1 FROM areas WHERE id = :id FOR KEY SHARE"), {"id": stockroom.area_id}
        )
        holder.execute(
            sa.text("SELECT 1 FROM worker_sessions WHERE id = :id FOR NO KEY UPDATE"),
            {"id": session.id},
        )
        thread, results = _start(
            lambda: client.patch(
                f"/api/scan-stations/{stockroom.station_id}", json={"is_active": False}
            )
        )
        try:
            _assert_blocked(thread)
            refreshed_at = holder.execute(
                sa.text(
                    "UPDATE worker_sessions SET expires_at = clock_timestamp()"
                    " + interval '15 minutes' WHERE id = :id RETURNING clock_timestamp()"
                ),
                {"id": session.id},
            ).scalar_one()
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 200, response.text
    (row,) = _sessions(db_engine, stockroom.station_id)
    assert row.end_reason == "STATION_CHANGED"
    assert row.ended_at is not None and row.ended_at >= refreshed_at


def test_a_resolve_behind_a_session_close_answers_without_a_session(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    _, pn = _release(client, cell)
    session = _open(db_engine, cell.station_id)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(
                "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                " end_reason = 'STATION_CHANGED' WHERE id = :id"
            ),
            {"id": session.id},
        )
        thread, results = _start(lambda: _resolve(client, cell, pn))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 200, response.text
    assert response.json()["worker_session"] is None
    closed = _session_row(db_engine, session.id)
    assert (closed.expires_at, closed.end_reason) == (session.expires_at, "STATION_CHANGED")


def test_a_resolve_behind_a_badge_switch_refreshes_the_replacement_session(
    client: TestClient, db_engine: Engine
) -> None:
    worker, successor = _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine, worker)
    _, pn = _release(client, cell)
    session = _open(db_engine, cell.station_id)
    with db_engine.connect() as holder:
        # A concurrent switch, uncommitted: the old row closed SWITCHED and
        # its replacement inserted in the same transaction.
        holder.execute(
            sa.text(
                "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                " end_reason = 'SWITCHED' WHERE id = :id"
            ),
            {"id": session.id},
        )
        replacement_id, inserted_expiry = holder.execute(
            sa.text(
                "INSERT INTO worker_sessions (station_id, area_id, worker_id, started_at,"
                " expires_at) VALUES (:station, :area, :worker, clock_timestamp(),"
                " clock_timestamp() + interval '15 minutes') RETURNING id, expires_at"
            ),
            {"station": cell.station_id, "area": cell.area_id, "worker": successor["id"]},
        ).one()
        thread, results = _start(lambda: _resolve(client, cell, pn))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 200, response.text
    reported = response.json()["worker_session"]
    assert reported is not None
    assert reported["worker"] == _worker_ref(successor)
    replacement = _open(db_engine, cell.station_id)
    assert replacement.id == replacement_id
    assert _parse(reported["expires_at"]) == replacement.expires_at
    assert replacement.expires_at > inserted_expiry
    assert _session_row(db_engine, session.id).end_reason == "SWITCHED"


def test_a_sign_in_waiting_on_a_worker_deactivation_answers_unknown(
    client: TestClient, db_engine: Engine
) -> None:
    worker, bystander = _worker(client), _worker(client)
    cell = _scanned_cell(client, db_engine)
    other = _scanned_cell(client, db_engine, bystander)
    untouched = _open(db_engine, other.station_id)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": worker["id"]}
        )
        holder.execute(
            sa.text("UPDATE workers SET is_active = false WHERE id = :id"), {"id": worker["id"]}
        )
        thread, results = _start(lambda: _sign_in(client, cell, worker))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            answer = _finish(thread, results)
    assert answer["outcome"] == "UNKNOWN"
    assert answer["worker_session"] is None
    assert _sessions(db_engine, cell.station_id) == []
    assert _open(db_engine, other.station_id) == untouched


# ---------------------------------------------------------------------------
# W-22 … W-26, W-28 — the API surface
# ---------------------------------------------------------------------------


def test_badge_scan_refusals(client: TestClient, db_engine: Engine) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    path = _badge_path(cell)
    badge = str(worker["badge_barcode"])
    before = _row_counts(db_engine)
    assert (
        client.post("/api/scan-stations/NO-SUCH-STATION/badge-scans", json={"badge": badge})
    ).status_code == 404
    for body in (
        {"badge": 5},
        {},
        {"badge": badge, "worker_id": worker["id"]},
        {"badge": badge, "scan_session_id": 1},
    ):
        assert client.post(path, json=body).status_code == 422, body
    assert _row_counts(db_engine) == before

    _ok(client.patch(f"/api/scan-stations/{cell.station_id}", json={"is_active": False}))
    refused = client.post(path, json={"badge": badge})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"Scan Station '{cell.station_id}' is inactive and accepts no production use."
    )
    inactive_area = _scanned_cell(client, db_engine)
    _ok(client.patch(f"/api/areas/{inactive_area.area_id}", json={"is_active": False}))
    refused = client.post(_badge_path(inactive_area), json={"badge": badge})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"Area '{inactive_area.area_name}' bound to Scan Station '{inactive_area.station_id}'"
        " is inactive and accepts no production use."
    )
    assert _sessions(db_engine, cell.station_id) == []
    assert _sessions(db_engine, inactive_area.station_id) == []


def test_worker_session_policy_api(
    client: TestClient, db_engine: Engine, default_policy: None
) -> None:
    initial = _ok(client.get(_POLICY_PATH))
    assert initial["worker_session_timeout_minutes"] == 15
    with db_engine.connect() as connection:
        before = int(
            connection.execute(
                sa.text("SELECT count(*) FROM audit_events WHERE entity_type = 'ApplicationPolicy'")
            ).scalar_one()
        )
    stored = _ok(client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": 30}))
    assert stored["worker_session_timeout_minutes"] == 30
    assert _ok(client.get(_POLICY_PATH))["worker_session_timeout_minutes"] == 30
    with db_engine.connect() as connection:
        rows = list(
            connection.execute(
                sa.text(
                    "SELECT event_type, entity_id, before_data, after_data, actor_reference"
                    " FROM audit_events WHERE entity_type = 'ApplicationPolicy' ORDER BY id"
                )
            )
        )
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == (
        "UPDATED",
        "worker-sessions",
        {"worker_session_timeout_minutes": 15},
        {"worker_session_timeout_minutes": 30},
        None,
    )
    # An identical PUT is a no-op.
    _ok(client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": 30}))
    assert _audit_count(db_engine, "ApplicationPolicy", "worker-sessions") == before + 1

    for value in (0, 721, -5):
        refused = client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": value})
        assert refused.status_code == 422
        assert refused.json() == {"detail": _E_S2}
    for body in (
        {"worker_session_timeout_minutes": "15"},
        {"worker_session_timeout_minutes": 1.5},
        {"worker_session_timeout_minutes": True},
        {"worker_session_timeout_minutes": None},
        {"worker_session_timeout_minutes": 15, "extra": 1},
        {},
    ):
        assert client.put(_POLICY_PATH, json=body).status_code == 422, body
    assert _ok(client.get(_POLICY_PATH))["worker_session_timeout_minutes"] == 30
    assert _audit_count(db_engine, "ApplicationPolicy", "worker-sessions") == before + 1


def test_area_override_api(client: TestClient, db_engine: Engine) -> None:
    department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    plain = _ok(
        client.post(
            "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
        ),
        201,
    )
    assert plain["worker_session_timeout_minutes"] is None
    overridden = _ok(
        client.post(
            "/api/areas",
            json={
                "department_id": department["id"],
                "name": _unique("AREA"),
                "worker_session_timeout_minutes": 45,
            },
        ),
        201,
    )
    assert overridden["worker_session_timeout_minutes"] == 45
    for refused_value in (0, 721):
        refused = client.post(
            "/api/areas",
            json={
                "department_id": department["id"],
                "name": _unique("AREA"),
                "worker_session_timeout_minutes": refused_value,
            },
        )
        assert refused.status_code == 422
        assert refused.json() == {"detail": _E_S3}

    path = f"/api/areas/{plain['id']}"
    audits = _audit_count(db_engine, "Area", plain["id"])
    assert (
        _ok(client.patch(path, json={"worker_session_timeout_minutes": 5}))[
            "worker_session_timeout_minutes"
        ]
        == 5
    )
    with db_engine.connect() as connection:
        before_data, after_data = connection.execute(
            sa.text(
                "SELECT before_data, after_data FROM audit_events WHERE entity_type = 'Area'"
                " AND entity_id = :id ORDER BY id DESC LIMIT 1"
            ),
            {"id": str(plain["id"])},
        ).one()
    assert before_data["worker_session_timeout_minutes"] is None
    assert after_data["worker_session_timeout_minutes"] == 5
    assert _audit_count(db_engine, "Area", plain["id"]) == audits + 1
    for value in (0, 721):
        refused = client.patch(path, json={"worker_session_timeout_minutes": value})
        assert refused.status_code == 422
        assert refused.json() == {"detail": _E_S3}
    for malformed in (True, "5", 1.5):
        body = {"worker_session_timeout_minutes": malformed}
        assert client.patch(path, json=body).status_code == 422
    assert _audit_count(db_engine, "Area", plain["id"]) == audits + 1
    cleared = _ok(client.patch(path, json={"worker_session_timeout_minutes": None}))
    assert cleared["worker_session_timeout_minutes"] is None
    assert _audit_count(db_engine, "Area", plain["id"]) == audits + 2


def test_the_server_clock_drives_every_session(client: TestClient, db_engine: Engine) -> None:
    worker = _worker(client)
    cell = _scanned_cell(client, db_engine)
    answer = _sign_in(client, cell, worker)
    with db_engine.connect() as connection:
        clock = connection.execute(sa.text("SELECT clock_timestamp()")).scalar_one()
        invalid = connection.execute(
            sa.text("SELECT count(*) FROM worker_sessions WHERE expires_at <= started_at")
        ).scalar_one()
    assert abs(_parse(answer["worker_session"]["server_now"]) - clock) < datetime.timedelta(
        seconds=5
    )
    assert invalid == 0


def test_the_area_editor_still_refuses_scanned_mode(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    refused = client.patch(
        f"/api/areas/{cell.area_id}", json={"worker_identification_mode": "SCANNED"}
    )
    assert refused.status_code == 422
    scanned = _scanned_cell(client, db_engine)
    saved = _ok(client.patch(f"/api/areas/{scanned.area_id}", json={"name": _unique("AREA")}))
    assert saved["worker_identification_mode"] == "SCANNED"


# ---------------------------------------------------------------------------
# W-27 — static guards: the session writers and their lock order
# ---------------------------------------------------------------------------

_SESSION_WRITE = re.compile(r"\binsert\(WorkerSession|\bupdate\(WorkerSession|\bWorkerSession\(")


def _functions(source: str) -> dict[str, str]:
    """Top-level function bodies of a module, by name."""
    parts = re.split(r"(?m)^def ", source)
    return {part.split("(", 1)[0]: part for part in parts[1:]}


def test_worker_sessions_are_written_only_by_their_owners() -> None:
    for path in sorted(_APPLICATION_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if path.name not in {"worker_sessions.py", "station_identity.py"}:
            assert not _SESSION_WRITE.search(source), path.name
            assert "scan_session_id" not in source, path.name
    station_identity = _functions(
        (_APPLICATION_DIR / "station_identity.py").read_text(encoding="utf-8")
    )
    branch = station_identity["_session_identity"]
    order = [
        branch.index("with_for_update(read=True, key_share=True)"),
        branch.index("with_for_update=_WORKER_KEY_SHARE"),
        branch.index(".with_for_update(key_share=True)"),
        branch.index("session_clock("),
        branch.index("update(WorkerSession)"),
    ]
    assert order == sorted(order)
    for name, body in _functions(
        (_APPLICATION_DIR / "worker_sessions.py").read_text(encoding="utf-8")
    ).items():
        if not _SESSION_WRITE.search(body):
            continue
        assert "session_clock(" in body, name
        if name == "sign_in" or name == "refresh_on_resolve":
            assert body.index("_lock_open_row(") < body.index("session_clock("), name
        else:
            assert name == "close_open_sessions", name
            assert body.index(".order_by(WorkerSession.id).with_for_update(") < body.index(
                "session_clock("
            )
