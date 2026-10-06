"""Integration tests for Phase 13 slice 5 — the badge-confirmation gates.

Exercises the full request path — FastAPI routes, the Application
commands and read models, the Worker sessions policy and PostgreSQL —
against a dedicated temporary database migrated to head by the real
Alembic chain (IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §16,
§19; GUI_DESIGN §4.6, §4.12; PLAN CD3, CD4, CD5, CD6, CD10; owner
decision OD-3):

- the Worker sessions policy carries the three badge-confirmation
  options (default on), written as an audited partial merge under the
  row lock;
- the station context reports the server-computed final-gate form per
  action (BADGE exactly in a Scanned-session Area whose option is on);
- the Area editor accepts Scanned session mode;
- DONE (both variants), QUEUE and Undo: the badge gate requires the
  confirming badge, signs its active Worker in (open, switch, refresh)
  and records that Worker and session on every row, in the command's
  transaction; the typed refusals (badge required, not expected, not
  recognized) write nothing; a committed command replays whatever the
  badge or the configuration says now;
- concurrency: the gate against an Area mode change in both orders, a
  badge Worker write, a badge scan and a Worker deactivation;
- static guards: the badge never joins a fingerprint, and the badge
  path's lock order.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations/Workers; the policy is restored after every test and
the module database is dropped afterwards. Helpers are copied from the
slice 4 module (each module owns its own). Command setups run while the
Area is Disabled, then the Area switches to Scanned session through the
Area editor.
"""

import ast
import os
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

from alembic import command
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APPLICATION_DIR = _BACKEND_DIR / "app" / "application"
_TEST_DATABASE = "partflow_test_badge_confirmation_api"
_DB_URL_ENV = "DATABASE_URL"
_POLICY_PATH = "/api/policies/worker-sessions"

_E_G1 = (
    "This action is now confirmed by a Worker badge scan. Scan your badge to confirm."
    " Nothing was recorded."
)
_E_G2 = (
    "This action is no longer confirmed by a badge scan. Confirm it again. Nothing was recorded."
)
_E_G3 = "Badge not recognized. Check the badge and scan again — nothing was recorded."
_E_G5 = "Change at least one Worker session setting."
_E_S1 = (
    "No Worker is signed in at this Scan Station. Scan your badge to continue."
    " Nothing was recorded."
)
_OLD_E1 = "Scanned session mode is not available yet"
_QUESTION_GATES = {"done": "QUESTION", "queue": "QUESTION", "undo": "QUESTION"}
_ALL_ON = {"badge_confirm_done": True, "badge_confirm_queue": True, "badge_confirm_undo": True}


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
        json={"prefix": "BC-", "digits": 4},
    )
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def approved_policy(client: TestClient) -> Iterator[None]:
    """Every test leaves the approved defaults: 15 minutes, every option on."""
    yield
    _ok(client.put(_POLICY_PATH, json={"worker_session_timeout_minutes": 15, **_ALL_ON}))


# ---------------------------------------------------------------------------
# Seeding helpers (copied from the slice 4 module — each module owns its own)
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


class _Cell:
    """An Area with one Operation, one Scan Station and optional Machines."""

    def __init__(self, client: TestClient, *, machine_count: int = 0) -> None:
        department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        self.area = _ok(
            client.post(
                "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
            ),
            201,
        )
        self.area_id = int(self.area["id"])
        operation = _ok(
            client.post("/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}),
            201,
        )
        self.operation_id = int(operation["id"])
        station = _ok(
            client.post(
                "/api/scan-stations",
                json={"station_id": _unique("ST"), "area_id": self.area_id},
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


def _worker(client: TestClient) -> dict[str, Any]:
    return _ok(
        client.post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )


def _set_mode(
    client: TestClient, area_id: int, mode: str, worker_id: int | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"worker_identification_mode": mode}
    if worker_id is not None:
        body["fixed_worker_id"] = worker_id
    return _ok(client.patch(f"/api/areas/{area_id}", json=body))


def _set_policy(client: TestClient, **fields: Any) -> dict[str, Any]:
    return _ok(client.put(_POLICY_PATH, json=fields))


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


# A station command as a (path, body) pair, so a retry resends the
# identical request.
_Request = tuple[str, dict[str, Any]]


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


def _transfer_request(
    source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int
) -> _Request:
    return f"/api/scan-stations/{target.station_id}/transfers", {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": _event(),
    }


def _undo_request(cell: _Cell, pn: str, reverses: str) -> _Request:
    return f"/api/scan-stations/{cell.station_id}/undos", {
        "part_number": pn,
        "reverses_device_event_id": reverses,
        "device_event_id": _event(),
    }


def _badged(request: _Request, badge: object) -> _Request:
    """The same request (same device_event_id) carrying ``badge``."""
    path, payload = request
    return path, {**payload, "confirming_badge": badge}


def _send(client: TestClient, request: _Request) -> Any:
    path, payload = request
    return client.post(path, json=payload)


def _created(client: TestClient, request: _Request) -> dict[str, Any]:
    return _ok(_send(client, request), 201)


def _event_of(request: _Request) -> str:
    return str(request[1]["device_event_id"])


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _sign_in(client: TestClient, cell: _Cell, worker: dict[str, Any]) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/scan-stations/{cell.station_id}/badge-scans",
            json={"badge": worker["badge_barcode"]},
        )
    )


def _scanned(client: TestClient, cell: _Cell, worker: dict[str, Any] | None = None) -> None:
    """Scanned session mode through the Area editor, ``worker`` signed in when given."""
    _set_mode(client, cell.area_id, "SCANNED")
    if worker is not None:
        assert _sign_in(client, cell, worker)["outcome"] == "SIGNED_IN"


class _SessionRow(NamedTuple):
    id: int
    station_id: str
    area_id: int
    worker_id: int
    started_at: Any
    expires_at: Any
    ended_at: Any
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


def _open_rows(engine: Engine, station_id: str) -> list[_SessionRow]:
    return [row for row in _sessions(engine, station_id) if row.ended_at is None]


def _open(engine: Engine, station_id: str) -> _SessionRow:
    rows = _open_rows(engine, station_id)
    assert len(rows) == 1, rows
    return rows[0]


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


def _policy_audits(engine: Engine) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, entity_id, before_data, after_data, actor_reference"
                    " FROM audit_events WHERE entity_type = 'ApplicationPolicy' ORDER BY id"
                )
            )
        )


def _assert_refused(response: Any, status: int, detail: str, flag: str) -> None:
    assert response.status_code == status, response.text
    assert response.json() == {"detail": detail, flag: True}


def _assert_badge_required(response: Any) -> None:
    _assert_refused(response, 409, _E_G1, "badge_confirmation_required")


def _assert_badge_not_expected(response: Any) -> None:
    _assert_refused(response, 409, _E_G2, "badge_confirmation_not_expected")


def _assert_badge_not_recognized(response: Any) -> None:
    _assert_refused(response, 422, _E_G3, "badge_not_recognized")


def _assert_session_required(response: Any) -> None:
    _assert_refused(response, 409, _E_S1, "worker_session_required")


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
# The gated commands (built while the Area is Disabled)
# ---------------------------------------------------------------------------


class _Gated(NamedTuple):
    cell: _Cell  # the station's cell
    request: _Request
    size: int  # Movements the command appends


def _gated_machine_done(client: TestClient) -> _Gated:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(client, _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True))
    return _Gated(cell, _in_area(cell, "area-completions", flow_id, pn, 10, machine=True), 1)


def _gated_direct_done(client: TestClient) -> _Gated:
    cell = _Cell(client)
    flow_id, pn = _release(client, cell)
    return _Gated(cell, _in_area(cell, "area-completions", flow_id, pn, 10, machine=False), 1)


def _gated_queue(client: TestClient) -> _Gated:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(client, _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True))
    return _Gated(cell, _in_area(cell, "machine-releases", flow_id, pn, 10, machine=True), 1)


def _gated_undo(client: TestClient) -> _Gated:
    """An Undo of a prior transfer at the station: the station Area is not a restored Area."""
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    transfer = _created(client, _transfer_request(source, target, flow_id, pn, 10))
    return _Gated(target, _undo_request(target, pn, str(transfer["device_event_id"])), 1)


_GATED: dict[str, Callable[[TestClient], _Gated]] = {
    "machine_done": _gated_machine_done,
    "direct_done": _gated_direct_done,
    "queue": _gated_queue,
    "undo": _gated_undo,
}
# The badge-confirmation option each gated command reads.
_OPTION = {
    "machine_done": "badge_confirm_done",
    "direct_done": "badge_confirm_done",
    "queue": "badge_confirm_queue",
    "undo": "badge_confirm_undo",
}


# ---------------------------------------------------------------------------
# G-1 / G-1b — the policy
# ---------------------------------------------------------------------------


def test_policy_carries_the_options_as_an_audited_partial_merge(
    client: TestClient, db_engine: Engine
) -> None:
    initial = _ok(client.get(_POLICY_PATH))
    assert {key: initial[key] for key in ("worker_session_timeout_minutes", *_ALL_ON)} == {
        "worker_session_timeout_minutes": 15,
        **_ALL_ON,
    }
    before = len(_policy_audits(db_engine))
    stored = _set_policy(
        client,
        worker_session_timeout_minutes=30,
        badge_confirm_done=False,
        badge_confirm_queue=True,
        badge_confirm_undo=False,
    )
    first = {
        "worker_session_timeout_minutes": 30,
        "badge_confirm_done": False,
        "badge_confirm_queue": True,
        "badge_confirm_undo": False,
    }
    assert {key: stored[key] for key in first} == first
    rows = _policy_audits(db_engine)
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == (
        "UPDATED",
        "worker-sessions",
        {"worker_session_timeout_minutes": 15, **_ALL_ON},
        first,
        None,
    )
    # The same values again write nothing.
    _set_policy(client, **first)
    assert len(_policy_audits(db_engine)) == before + 1

    # One option alone: the timeout and the other options are kept.
    second = {**first, "badge_confirm_undo": True}
    assert {
        key: value
        for key, value in _set_policy(client, badge_confirm_undo=True).items()
        if key in second
    } == second
    rows = _policy_audits(db_engine)
    assert len(rows) == before + 2
    assert (rows[-1].before_data, rows[-1].after_data) == (first, second)
    # The slice 4 body (the timeout alone) keeps every option.
    third = {**second, "worker_session_timeout_minutes": 20}
    stored = _set_policy(client, worker_session_timeout_minutes=20)
    assert {key: stored[key] for key in third} == third

    counts = len(_policy_audits(db_engine))
    empty = client.put(_POLICY_PATH, json={})
    assert (empty.status_code, empty.json()["detail"]) == (422, _E_G5)
    for body in (
        {"badge_confirm_done": "true"},
        {"badge_confirm_done": 1},
        {"badge_confirm_queue": None},
        {"worker_session_timeout_minutes": None},
        {"badge_confirm_undo": False, "extra": 1},
    ):
        assert client.put(_POLICY_PATH, json=body).status_code == 422, body
    final = _ok(client.get(_POLICY_PATH))
    assert {key: final[key] for key in third} == third
    assert len(_policy_audits(db_engine)) == counts


def test_concurrent_partial_policy_writers_both_keep_their_change(
    client: TestClient, db_engine: Engine
) -> None:
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(sa.text("UPDATE application_policy SET badge_confirm_done = false"))
        thread, results = _start(
            lambda: client.put(_POLICY_PATH, json={"badge_confirm_queue": False})
        )
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    stored = _ok(response)
    assert (stored["badge_confirm_done"], stored["badge_confirm_queue"]) == (False, False)
    last = _policy_audits(db_engine)[-1]
    # The PUT merged onto the locked, committed row.
    assert last.before_data["badge_confirm_done"] is False
    assert last.after_data == {
        "worker_session_timeout_minutes": 15,
        "badge_confirm_done": False,
        "badge_confirm_queue": False,
        "badge_confirm_undo": True,
    }


# ---------------------------------------------------------------------------
# G-2 / G-3 — the context and the Area editor
# ---------------------------------------------------------------------------


def test_context_reports_the_final_gate_of_each_action(client: TestClient) -> None:
    disabled, fixed, scanned = _Cell(client), _Cell(client), _Cell(client)
    _set_mode(client, fixed.area_id, "FIXED", int(_worker(client)["id"]))
    _scanned(client, scanned)

    def gates(cell: _Cell) -> dict[str, str]:
        context = _ok(client.get(f"/api/scan-stations/{cell.station_id}/context"))
        return cast(dict[str, str], context["worker_identification"]["final_gates"])

    for done in (True, False):
        for queue in (True, False):
            for undo in (True, False):
                _set_policy(
                    client,
                    badge_confirm_done=done,
                    badge_confirm_queue=queue,
                    badge_confirm_undo=undo,
                )
                assert gates(disabled) == gates(fixed) == _QUESTION_GATES
                assert gates(scanned) == {
                    action: "BADGE" if on else "QUESTION"
                    for action, on in (("done", done), ("queue", queue), ("undo", undo))
                }


def _area_audits(engine: Engine, area_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, before_data, after_data FROM audit_events"
                    " WHERE entity_type = 'Area' AND entity_id = :id ORDER BY id"
                ),
                {"id": str(area_id)},
            )
        )


def test_the_area_editor_accepts_scanned_session_mode(
    client: TestClient, db_engine: Engine
) -> None:
    department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    created = _ok(
        client.post(
            "/api/areas",
            json={
                "department_id": department["id"],
                "name": _unique("AREA"),
                "worker_identification_mode": "SCANNED",
            },
        ),
        201,
    )
    assert (created["worker_identification_mode"], created["fixed_worker_id"]) == ("SCANNED", None)

    worker = _worker(client)
    disabled, fixed = _Cell(client), _Cell(client)
    _set_mode(client, fixed.area_id, "FIXED", int(worker["id"]))
    for cell, before in (
        (disabled, {"worker_identification_mode": "DISABLED", "fixed_worker_id": None}),
        (fixed, {"worker_identification_mode": "FIXED", "fixed_worker_id": worker["id"]}),
    ):
        count = len(_area_audits(db_engine, cell.area_id))
        saved = _set_mode(client, cell.area_id, "SCANNED")
        assert (saved["worker_identification_mode"], saved["fixed_worker_id"]) == ("SCANNED", None)
        rows = _area_audits(db_engine, cell.area_id)
        assert len(rows) == count + 1
        identity = ("worker_identification_mode", "fixed_worker_id")
        assert rows[-1].event_type == "UPDATED"
        assert {key: rows[-1].before_data[key] for key in identity} == before
        assert {key: rows[-1].after_data[key] for key in identity} == {
            "worker_identification_mode": "SCANNED",
            "fixed_worker_id": None,
        }

    refused = client.patch(
        f"/api/areas/{disabled.area_id}",
        json={"worker_identification_mode": "SCANNED", "fixed_worker_id": worker["id"]},
    )
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "A Fixed Worker can be set only in Fixed Worker mode.",
    )
    for path in (_BACKEND_DIR / "app").rglob("*.py"):
        assert _OLD_E1 not in path.read_text(encoding="utf-8"), path


# ---------------------------------------------------------------------------
# G-4 … G-11 — the gate on every gated command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_a_missing_badge_is_refused_when_the_gate_is_the_badge(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    worker = _worker(client)
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell, worker)
    session = _open(db_engine, command_.cell.station_id)
    before = _row_counts(db_engine)
    _assert_badge_required(_send(client, command_.request))
    assert _row_counts(db_engine) == before
    assert _open(db_engine, command_.cell.station_id) == session


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_badge_required_takes_precedence_over_a_missing_session(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    worker = _worker(client)
    # (a) No session row at all.
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell)
    before = _row_counts(db_engine)
    _assert_badge_required(_send(client, command_.request))
    assert _row_counts(db_engine) == before
    # (b) An expired, not yet closed row: left untouched.
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell)
    expired = _insert_expired_session(db_engine, command_.cell, int(worker["id"]))
    rows = _sessions(db_engine, command_.cell.station_id)
    before = _row_counts(db_engine)
    _assert_badge_required(_send(client, command_.request))
    assert _row_counts(db_engine) == before
    assert _sessions(db_engine, command_.cell.station_id) == rows
    assert rows[0].id == expired and rows[0].ended_at is None


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_the_signed_in_workers_badge_refreshes_and_records_the_session(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    worker = _worker(client)
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell, worker)
    session = _open(db_engine, command_.cell.station_id)
    badge = f"  {str(worker['badge_barcode']).lower()} "
    _created(client, _badged(command_.request, badge))
    assert (
        _movement_identity(db_engine, _event_of(command_.request))
        == [(int(worker["id"]), session.id)] * command_.size
    )
    rows = _sessions(db_engine, command_.cell.station_id)
    assert [row.id for row in rows] == [session.id]
    assert rows[0].ended_at is None and rows[0].expires_at > session.expires_at


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_another_workers_badge_switches_the_session(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    first, second = _worker(client), _worker(client)
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell, first)
    _created(client, _badged(command_.request, second["badge_barcode"]))
    old, new = _sessions(db_engine, command_.cell.station_id)
    assert (old.worker_id, old.end_reason) == (first["id"], "SWITCHED")
    assert old.ended_at == new.started_at
    assert (new.worker_id, new.area_id, new.ended_at) == (
        second["id"],
        command_.cell.area_id,
        None,
    )
    assert (
        _movement_identity(db_engine, _event_of(command_.request))
        == [(int(second["id"]), new.id)] * command_.size
    )
    assert _open(db_engine, command_.cell.station_id) == new


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_a_valid_badge_signs_in_without_a_valid_session(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    worker = _worker(client)
    # No session at all.
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell)
    _created(client, _badged(command_.request, worker["badge_barcode"]))
    opened = _open(db_engine, command_.cell.station_id)
    assert opened.worker_id == worker["id"]
    assert (
        _movement_identity(db_engine, _event_of(command_.request))
        == [(int(worker["id"]), opened.id)] * command_.size
    )
    # Right after expiry: the expired row closes EXPIRED at its expiry.
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell)
    expired = _insert_expired_session(db_engine, command_.cell, int(worker["id"]))
    _created(client, _badged(command_.request, worker["badge_barcode"]))
    old, new = _sessions(db_engine, command_.cell.station_id)
    assert old.id == expired
    assert (old.end_reason, old.ended_at) == ("EXPIRED", old.expires_at)
    assert (new.worker_id, new.ended_at) == (worker["id"], None)
    assert (
        _movement_identity(db_engine, _event_of(command_.request))
        == [(int(worker["id"]), new.id)] * command_.size
    )


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_an_unrecognized_badge_is_refused_with_zero_writes(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    signed_in, inactive = _worker(client), _worker(client)
    _ok(client.patch(f"/api/workers/{inactive['id']}", json={"is_active": False}))
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell, signed_in)
    rows = _sessions(db_engine, command_.cell.station_id)
    before = _row_counts(db_engine)
    for badge in (
        _unique("UNKNOWN"),
        inactive["badge_barcode"],
        f"PF:{signed_in['badge_barcode']}",
        "   ",
        "A" * 200,
        f"{signed_in['badge_barcode']}\x00",
    ):
        _assert_badge_not_recognized(_send(client, _badged(command_.request, badge)))
    assert _row_counts(db_engine) == before
    assert _sessions(db_engine, command_.cell.station_id) == rows


@pytest.mark.parametrize("gated", sorted(_GATED))
def test_the_question_form_in_a_scanned_area(
    client: TestClient, db_engine: Engine, gated: str
) -> None:
    worker = _worker(client)
    _set_policy(client, **{_OPTION[gated]: False})
    # A badge where the gate is the question.
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell, worker)
    before = _row_counts(db_engine)
    _assert_badge_not_expected(_send(client, _badged(command_.request, worker["badge_barcode"])))
    assert _row_counts(db_engine) == before
    # No badge with a valid session: the slice 4 path.
    session = _open(db_engine, command_.cell.station_id)
    _created(client, command_.request)
    assert (
        _movement_identity(db_engine, _event_of(command_.request))
        == [(int(worker["id"]), session.id)] * command_.size
    )
    # No badge without a session.
    command_ = _GATED[gated](client)
    _scanned(client, command_.cell)
    before = _row_counts(db_engine)
    _assert_session_required(_send(client, command_.request))
    assert _row_counts(db_engine) == before


@pytest.mark.parametrize("gated", sorted(_GATED))
@pytest.mark.parametrize("mode", ["DISABLED", "FIXED"])
def test_a_badge_is_never_expected_outside_scanned_mode(
    client: TestClient, db_engine: Engine, gated: str, mode: str
) -> None:
    worker = _worker(client)
    command_ = _GATED[gated](client)
    if mode == "FIXED":
        _set_mode(client, command_.cell.area_id, "FIXED", int(worker["id"]))
    before = _row_counts(db_engine)
    _assert_badge_not_expected(_send(client, _badged(command_.request, worker["badge_barcode"])))
    assert _row_counts(db_engine) == before
    _created(client, command_.request)
    expected = int(worker["id"]) if mode == "FIXED" else None
    assert (
        _movement_identity(db_engine, _event_of(command_.request))
        == [(expected, None)] * command_.size
    )


def test_each_option_decides_only_its_own_action(client: TestClient, db_engine: Engine) -> None:
    first, second = _worker(client), _worker(client)
    cell = _Cell(client, machine_count=1)
    done_flow, done_pn = _release(client, cell)
    queue_flow, queue_pn = _release(client, cell)
    for flow_id, pn in ((done_flow, done_pn), (queue_flow, queue_pn)):
        _created(client, _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True))
    _scanned(client, cell, first)
    _set_policy(client, badge_confirm_done=False)

    done = _created(
        client, _in_area(cell, "area-completions", done_flow, done_pn, 10, machine=True)
    )
    _assert_badge_required(
        _send(client, _in_area(cell, "machine-releases", queue_flow, queue_pn, 10, machine=True))
    )
    undo = _undo_request(cell, done_pn, str(done["device_event_id"]))
    _created(client, _badged(undo, second["badge_barcode"]))
    assert _movement_identity(db_engine, _event_of(undo)) == [
        (int(second["id"]), _open(db_engine, cell.station_id).id)
    ]


# ---------------------------------------------------------------------------
# G-12 / G-13 — replay and retry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["mode", "option"])
def test_a_committed_gate_command_replays_whatever_the_badge_or_configuration(
    client: TestClient, db_engine: Engine, change: str
) -> None:
    worker = _worker(client)
    command_ = _gated_direct_done(client)
    _scanned(client, command_.cell)
    request = _badged(command_.request, worker["badge_barcode"])
    _created(client, request)
    session = _open(db_engine, command_.cell.station_id)
    recorded = [(int(worker["id"]), session.id)]
    assert _movement_identity(db_engine, _event_of(request)) == recorded

    if change == "mode":
        _set_mode(client, command_.cell.area_id, "DISABLED")
    else:
        _set_policy(client, badge_confirm_done=False)
    rows = _sessions(db_engine, command_.cell.station_id)
    counts = _row_counts(db_engine)
    for replay in (request, command_.request, _badged(command_.request, _unique("UNKNOWN"))):
        _ok(_send(client, replay), 200)
    assert _row_counts(db_engine) == counts
    assert _sessions(db_engine, command_.cell.station_id) == rows
    assert _movement_identity(db_engine, _event_of(request)) == recorded


def test_a_refused_gate_attempt_is_retried_under_the_same_device_event_id(
    client: TestClient, db_engine: Engine
) -> None:
    first, second = _worker(client), _worker(client)
    command_ = _gated_direct_done(client)
    _scanned(client, command_.cell, first)
    _assert_badge_required(_send(client, command_.request))
    badged = _badged(command_.request, second["badge_barcode"])
    _created(client, badged)
    switched = _open(db_engine, command_.cell.station_id)
    assert switched.worker_id == second["id"]
    assert _movement_identity(db_engine, _event_of(badged)) == [(int(second["id"]), switched.id)]
    counts = _row_counts(db_engine)
    _ok(_send(client, badged), 200)
    assert _row_counts(db_engine) == counts

    command_ = _gated_direct_done(client)
    _scanned(client, command_.cell)
    _assert_badge_not_recognized(_send(client, _badged(command_.request, _unique("UNKNOWN"))))
    _created(client, _badged(command_.request, first["badge_barcode"]))
    assert _movement_identity(db_engine, _event_of(command_.request)) == [
        (int(first["id"]), _open(db_engine, command_.cell.station_id).id)
    ]


# ---------------------------------------------------------------------------
# G-14 — request shape
# ---------------------------------------------------------------------------


def test_the_confirming_badge_shape_is_checked_before_the_fast_path(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _worker(client)
    commands = [factory(client) for factory in (_gated_direct_done, _gated_queue, _gated_undo)]
    before = _row_counts(db_engine)
    for command_ in commands:
        for badge in (5, "", True):
            response = _send(client, _badged(command_.request, badge))
            assert response.status_code == 422, (command_.request[0], badge, response.text)
    assert _row_counts(db_engine) == before

    committed = commands[0]
    _scanned(client, committed.cell)
    _created(client, _badged(committed.request, worker["badge_barcode"]))
    assert _send(client, _badged(committed.request, 5)).status_code == 422

    # The routes outside the canonical gate list take no badge at all.
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    source = _Cell(client)
    other_flow, other_pn = _release(client, source)
    scrap_path, scrap = _in_area(cell, "scraps", flow_id, pn, 1, machine=False)
    counts = _row_counts(db_engine)
    for request in (
        _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True),
        _transfer_request(source, cell, other_flow, other_pn, 10),
        (scrap_path, {**scrap, "reason": "damaged"}),
    ):
        response = _send(client, _badged(request, worker["badge_barcode"]))
        assert response.status_code == 422, (request[0], response.text)
    assert _row_counts(db_engine) == counts


# ---------------------------------------------------------------------------
# G-15 / G-15b — Undo specifics
# ---------------------------------------------------------------------------


def test_undo_records_the_confirming_worker_and_keeps_the_original_identity(
    client: TestClient, db_engine: Engine
) -> None:
    first, second = _worker(client), _worker(client)
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    _scanned(client, target, first)
    original_session = _open(db_engine, target.station_id)
    transfer = _created(client, _transfer_request(source, target, flow_id, pn, 10))
    reverses = str(transfer["device_event_id"])
    preview = _ok(client.get(f"/api/scan-stations/{target.station_id}/undo-preview/{reverses}"))
    assert preview["reversed_by"]["id"] == first["id"]

    undo = _badged(_undo_request(target, pn, reverses), second["badge_barcode"])
    _created(client, undo)
    confirming = _open(db_engine, target.station_id)
    assert confirming.worker_id == second["id"]
    assert _movement_identity(db_engine, _event_of(undo)) == [(int(second["id"]), confirming.id)]
    assert _movement_identity(db_engine, reverses) == [(int(first["id"]), original_session.id)]


def test_undo_into_the_station_area_takes_the_badge_path_without_deadlock(
    client: TestClient, db_engine: Engine
) -> None:
    first, second = _worker(client), _worker(client)
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _created(client, _in_area(cell, "machine-assignments", flow_id, pn, 10, machine=True))
    _scanned(client, cell, first)
    _set_policy(client, badge_confirm_done=False)
    done = _created(client, _in_area(cell, "area-completions", flow_id, pn, 10, machine=True))

    undo = _badged(_undo_request(cell, pn, str(done["device_event_id"])), second["badge_barcode"])
    result = _created(client, undo)
    confirming = _open(db_engine, cell.station_id)
    assert confirming.worker_id == second["id"]
    assert _movement_identity(db_engine, _event_of(undo)) == [(int(second["id"]), confirming.id)]
    [restored] = result["flows"]
    assert (restored["current_area_id"], restored["current_machine_id"]) == (
        cell.area_id,
        cell.machine_id,
    )


# ---------------------------------------------------------------------------
# G-16 — the gate never changes the quantity effect
# ---------------------------------------------------------------------------


def _gate_story(
    client: TestClient,
    engine: Engine,
    configure: Callable[[list[_Cell]], None],
    badge: str | None,
) -> dict[str, Any]:
    """Partial DONE at a Machine, QUEUE, Undo of the QUEUE and a direct DONE
    under one configuration; returns the normalized flows and Movements and
    the identity the gated commands recorded."""
    machining, direct = _Cell(client, machine_count=1), _Cell(client)
    done_flow, done_pn = _release(client, machining)
    queue_flow, queue_pn = _release(client, machining)
    direct_flow, direct_pn = _release(client, direct)
    for flow_id, pn in ((done_flow, done_pn), (queue_flow, queue_pn)):
        _created(client, _in_area(machining, "machine-assignments", flow_id, pn, 10, machine=True))
    configure([machining, direct])

    def run(request: _Request) -> dict[str, Any]:
        return _created(client, request if badge is None else _badged(request, badge))

    done = run(_in_area(machining, "area-completions", done_flow, done_pn, 4, machine=True))
    queued = run(_in_area(machining, "machine-releases", queue_flow, queue_pn, 10, machine=True))
    undone = run(_undo_request(machining, queue_pn, str(queued["device_event_id"])))
    finished = run(_in_area(direct, "area-completions", direct_flow, direct_pn, 10, machine=False))
    events = {str(result["device_event_id"]) for result in (done, queued, undone, finished)}
    pns = [done_pn, queue_pn, direct_pn]
    areas = {machining.area_id: "M", direct.area_id: "D"}
    with engine.connect() as connection:
        movements = list(
            connection.execute(
                sa.text("SELECT * FROM part_movements WHERE part_number = ANY(:pns) ORDER BY id"),
                {"pns": pns},
            ).mappings()
        )
        flows = list(
            connection.execute(
                sa.text("SELECT * FROM quantity_flows WHERE part_number = ANY(:pns) ORDER BY id"),
                {"pns": pns},
            ).mappings()
        )
    order: dict[int, int] = {}
    for movement in movements:
        order.setdefault(int(movement["quantity_flow_id"]), len(order))
    index = {int(movement["id"]): position for position, movement in enumerate(movements)}
    return {
        "movements": [
            (
                order[int(movement["quantity_flow_id"])],
                pns.index(movement["part_number"]),
                movement["movement_type"],
                movement["quantity"],
                areas.get(movement["from_area_id"]),
                areas.get(movement["to_area_id"]),
                movement["source_machine_id"] is not None,
                movement["destination_machine_id"] is not None,
                movement["command_sequence"],
                (
                    index[int(movement["reverses_movement_id"])]
                    if movement["reverses_movement_id"] is not None
                    else None
                ),
            )
            for movement in movements
        ],
        "flows": sorted(
            (
                order[int(flow["id"])],
                flow["quantity"],
                flow["status"],
                areas.get(flow["current_area_id"]),
                flow["current_machine_id"] is not None,
            )
            for flow in flows
        ),
        "identity": {
            (movement["worker_id"], movement["scan_session_id"] is not None)
            for movement in movements
            if movement["device_event_id"] in events
        },
    }


def test_the_gate_never_changes_the_quantity_effect(client: TestClient, db_engine: Engine) -> None:
    worker = _worker(client)
    worker_id = int(worker["id"])

    def disabled(cells: list[_Cell]) -> None:
        return None

    def fixed(cells: list[_Cell]) -> None:
        for cell in cells:
            _set_mode(client, cell.area_id, "FIXED", worker_id)

    def scanned_question(cells: list[_Cell]) -> None:
        _set_policy(
            client, badge_confirm_done=False, badge_confirm_queue=False, badge_confirm_undo=False
        )
        for cell in cells:
            _scanned(client, cell, worker)

    def scanned_badge(cells: list[_Cell]) -> None:
        _set_policy(client, **_ALL_ON)
        for cell in cells:
            _scanned(client, cell)

    stories = [
        _gate_story(client, db_engine, disabled, None),
        _gate_story(client, db_engine, fixed, None),
        _gate_story(client, db_engine, scanned_question, None),
        _gate_story(client, db_engine, scanned_badge, str(worker["badge_barcode"])),
    ]
    assert [story["identity"] for story in stories] == [
        {(None, False)},
        {(worker_id, False)},
        {(worker_id, True)},
        {(worker_id, True)},
    ]
    for key in ("movements", "flows"):
        assert stories[0][key] == stories[1][key] == stories[2][key] == stories[3][key], key


# ---------------------------------------------------------------------------
# G-17 … G-21 — concurrency
# ---------------------------------------------------------------------------


_CLOSE_STATION_SESSIONS = (
    "UPDATE worker_sessions SET ended_at = clock_timestamp(), end_reason = 'AREA_MODE_CHANGED'"
    " WHERE station_id = :station AND ended_at IS NULL"
)


@pytest.mark.parametrize("factory", [_gated_direct_done, _gated_undo], ids=["done", "undo"])
def test_a_mode_change_first_refuses_the_gate(
    client: TestClient, db_engine: Engine, factory: Callable[[TestClient], _Gated]
) -> None:
    first, second = _worker(client), _worker(client)
    command_ = factory(client)
    _scanned(client, command_.cell, first)
    rows = _sessions(db_engine, command_.cell.station_id)
    before = _row_counts(db_engine)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM areas WHERE id = :id FOR NO KEY UPDATE"),
            {"id": command_.cell.area_id},
        )
        holder.execute(
            sa.text("UPDATE areas SET worker_identification_mode = 'DISABLED' WHERE id = :id"),
            {"id": command_.cell.area_id},
        )
        holder.execute(sa.text(_CLOSE_STATION_SESSIONS), {"station": command_.cell.station_id})
        request = _badged(command_.request, second["badge_barcode"])
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    _assert_badge_not_expected(response)
    after = _sessions(db_engine, command_.cell.station_id)
    assert [row.id for row in after] == [row.id for row in rows]
    assert _open_rows(db_engine, command_.cell.station_id) == []
    assert _row_counts(db_engine) == before


@pytest.mark.parametrize("factory", [_gated_direct_done, _gated_undo], ids=["done", "undo"])
def test_a_gate_first_is_closed_by_the_following_mode_change(
    client: TestClient, db_engine: Engine, factory: Callable[[TestClient], _Gated]
) -> None:
    first, second = _worker(client), _worker(client)
    command_ = factory(client)
    _scanned(client, command_.cell, first)
    held = _open(db_engine, command_.cell.station_id)
    request = _badged(command_.request, second["badge_barcode"])
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM worker_sessions WHERE id = :id FOR NO KEY UPDATE"),
            {"id": held.id},
        )
        # The gate takes the Area FOR SHARE, then waits on the held row.
        gate, gate_results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(gate)
            # The mode change waits on the gate's Area lock.
            patch, patch_results = _start(
                lambda: client.patch(
                    f"/api/areas/{command_.cell.area_id}",
                    json={"worker_identification_mode": "DISABLED"},
                )
            )
            _assert_blocked(patch)
        finally:
            holder.rollback()
    gated_response = _finish(gate, gate_results)
    patch_response = _finish(patch, patch_results)
    assert gated_response.status_code == 201, gated_response.text
    assert patch_response.status_code == 200, patch_response.text
    old, new = _sessions(db_engine, command_.cell.station_id)
    assert (old.id, old.end_reason) == (held.id, "SWITCHED")
    assert (new.worker_id, new.end_reason) == (second["id"], "AREA_MODE_CHANGED")
    assert _open_rows(db_engine, command_.cell.station_id) == []
    assert _movement_identity(db_engine, _event_of(request)) == [(int(second["id"]), new.id)]


@pytest.mark.parametrize("write", ["deactivation", "badge_change"])
def test_a_badge_worker_write_first_refuses_the_gate(
    client: TestClient, db_engine: Engine, write: str
) -> None:
    first, second = _worker(client), _worker(client)
    command_ = _gated_direct_done(client)
    _scanned(client, command_.cell, first)
    before = _row_counts(db_engine)
    rows = _sessions(db_engine, command_.cell.station_id)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": second["id"]}
        )
        if write == "deactivation":
            holder.execute(
                sa.text("UPDATE workers SET is_active = false WHERE id = :id"),
                {"id": second["id"]},
            )
        else:
            holder.execute(
                sa.text("UPDATE workers SET badge_barcode = :badge WHERE id = :id"),
                {"badge": _unique("MOVED"), "id": second["id"]},
            )
        request = _badged(command_.request, second["badge_barcode"])
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    _assert_badge_not_recognized(response)
    assert _row_counts(db_engine) == before
    assert _sessions(db_engine, command_.cell.station_id) == rows


def test_gates_and_badge_scans_at_one_station_serialize(
    client: TestClient, db_engine: Engine
) -> None:
    first, second = _worker(client), _worker(client)
    cell = _Cell(client)
    flows = [_release(client, cell) for _ in range(20)]
    _scanned(client, cell)
    for flow_id, pn in flows:
        barrier = threading.Barrier(2)
        request = _badged(
            _in_area(cell, "area-completions", flow_id, pn, 10, machine=False),
            second["badge_barcode"],
        )

        def sign_in(barrier: threading.Barrier = barrier) -> Any:
            barrier.wait()
            return client.post(
                f"/api/scan-stations/{cell.station_id}/badge-scans",
                json={"badge": first["badge_barcode"]},
            )

        def gate(barrier: threading.Barrier = barrier, request: _Request = request) -> Any:
            barrier.wait()
            return _send(client, request)

        threads = [_start(sign_in), _start(gate)]
        responses = [_finish(thread, results) for thread, results in threads]
        assert [response.status_code for response in responses] == [200, 201], [
            response.text for response in responses
        ]
        assert len(_open_rows(db_engine, cell.station_id)) == 1


def test_a_gate_behind_a_deactivation_of_the_previous_worker_opens_a_new_session(
    client: TestClient, db_engine: Engine
) -> None:
    first, second = _worker(client), _worker(client)
    command_ = _gated_direct_done(client)
    _scanned(client, command_.cell, first)
    held = _open(db_engine, command_.cell.station_id)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM workers WHERE id = :id FOR UPDATE"), {"id": first["id"]}
        )
        holder.execute(
            sa.text("UPDATE workers SET is_active = false WHERE id = :id"), {"id": first["id"]}
        )
        holder.execute(
            sa.text(
                "UPDATE worker_sessions SET ended_at = clock_timestamp(),"
                " end_reason = 'WORKER_DEACTIVATED' WHERE id = :id"
            ),
            {"id": held.id},
        )
        request = _badged(command_.request, second["badge_barcode"])
        thread, results = _start(lambda: _send(client, request))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert response.status_code == 201, response.text
    old, new = _sessions(db_engine, command_.cell.station_id)
    assert (old.id, old.end_reason) == (held.id, "WORKER_DEACTIVATED")
    assert (new.worker_id, new.ended_at) == (second["id"], None)
    assert _movement_identity(db_engine, _event_of(request)) == [(int(second["id"]), new.id)]


# ---------------------------------------------------------------------------
# G-22 / G-23 — static guards and the database backstop
# ---------------------------------------------------------------------------


def _module(name: str) -> tuple[str, ast.Module]:
    source = (_APPLICATION_DIR / name).read_text(encoding="utf-8")
    return source, ast.parse(source)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    [function] = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    return function


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_the_badge_never_joins_a_fingerprint() -> None:
    for name in ("machine_processing.py", "direct_processing.py", "undo.py"):
        source, tree = _module(name)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and "fingerprint" in node.name:
                segment = ast.get_source_segment(source, node) or ""
                assert "badge" not in segment, (name, node.name)
            if isinstance(node, ast.Call) and "fingerprint" in (_called_name(node) or ""):
                names = {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}
                assert not {"badge", "confirming_badge"} & names, (name, ast.unparse(node))


def test_only_the_three_gated_commands_pass_a_confirming_badge() -> None:
    counts: dict[str, int] = {}
    for path in sorted(_APPLICATION_DIR.glob("*.py")):
        _, tree = _module(path.name)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _called_name(node) == "resolve_station_identity"
                and any(keyword.arg == "confirming_badge" for keyword in node.keywords)
            ):
                counts[path.name] = counts.get(path.name, 0) + 1
    assert counts == {"direct_processing.py": 1, "machine_processing.py": 1, "undo.py": 1}


def test_the_badge_path_lock_order() -> None:
    source, tree = _module("station_identity.py")
    body = ast.get_source_segment(source, _function(tree, "_badge_identity")) or ""
    assert "key_share" not in body
    order = [
        body.index(".with_for_update(read=True)"),
        body.index("resolve_badge("),
        body.index("with_for_update=_WORKER_KEY_SHARE"),
        body.index("sign_in_locked("),
    ]
    assert order == sorted(order)
    callers = [
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _called_name(node) == "_badge_identity"
    ]
    assert callers == ["resolve_station_identity"]

    source, tree = _module("worker_sessions.py")
    core = ast.get_source_segment(source, _function(tree, "sign_in_locked")) or ""
    assert core.index("_lock_open_row(") < core.index("session_clock(")
    sign_in = ast.get_source_segment(source, _function(tree, "sign_in")) or ""
    assert "sign_in_locked(" in sign_in and "session_clock(" not in sign_in


def test_the_session_foreign_key_stays_validated(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        validated = connection.execute(
            sa.text(
                "SELECT convalidated FROM pg_constraint"
                " WHERE conname = 'fk_part_movements_scan_session_worker_sessions'"
            )
        ).scalar_one()
    assert validated is True
