"""The HTTP request id and the backend access record (Phase 16 slice 6: RL-1 … RL-19, LC-3).

Exercises the real application (``create_app``) against a dedicated
temporary database migrated to head: every request gets one ``app.access``
record (``app.api.request_log``) with the request id echoed in exactly one
``X-Request-ID`` header; levels follow DV-1 (health always DEBUG, designed
refusals INFO, ERROR only for real failures); production commands carry
their PN, flow, quantity, Area, Operation, Machine, Worker, Scan Station
and ``device_event_id`` context and never a body field outside the
allowlist. Records are captured with the production ``JsonFormatter``
(``tests.log_capture``). Three test-only routes are added to the module's
app: an unhandled failure, a slow read and a ``password_check_busy``
refusal. The module database is dropped afterwards.
"""

import ast
import asyncio
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message, Receive, Scope, Send

from alembic import command
from app.api import request_log
from app.application.errors import PasswordCheckBusyError
from app.core import log_context
from app.core.config import get_settings
from app.infrastructure import schema_revision
from app.main import create_app
from tests.auth_harness import (
    CSRF_HEADERS,
    admin_of,
    anonymous_client,
    enroll_station_device,
    station_device_client,
    station_device_headers,
)
from tests.log_capture import JsonLogs, capture_json_logs

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_request_log"
_HEX32 = re.compile(r"[0-9a-f]{32}")
_ACCESS_KEYS = [
    "ts",
    "level",
    "logger",
    "message",
    "request_id",
    "event",
    "method",
    "route",
    "path",
    "status",
    "duration_ms",
    "client",
    "outcome",
    "slow",
    "refusal",
    "context",
]
_FAILURE_PATH = "/api/test-only/failure"
_SLOW_PATH = "/api/test-only/slow"
_BUSY_PATH = "/api/test-only/busy"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
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
def module_environment(api_database_url: URL) -> Iterator[None]:
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


def _app_with_test_routes() -> FastAPI:
    app = create_app()

    @app.get(_FAILURE_PATH)
    def failure() -> dict[str, str]:
        raise RuntimeError("secret-xyz")

    @app.get(_SLOW_PATH)
    def slow() -> dict[str, str]:
        time.sleep(1.1)
        return {"slow": "yes"}

    @app.post(_BUSY_PATH)
    def busy() -> dict[str, str]:
        raise PasswordCheckBusyError("PartFlow is busy checking passwords. Try again.")

    return app


@pytest.fixture(scope="module")
def client(module_environment: None) -> Iterator[TestClient]:
    with TestClient(_app_with_test_routes()) as test_client:
        yield station_device_client(test_client)


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _rid() -> str:
    return f"rl-{uuid.uuid4().hex}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _access(logs: JsonLogs, request_id: str) -> dict[str, Any]:
    """The one access record of the request ``request_id``."""
    found = [record for record in logs.access() if record.get("request_id") == request_id]
    assert len(found) == 1, found
    return found[0]


class _Cell:
    """An Area with one Operation, one Scan Station and optional Machines."""

    def __init__(
        self, client: TestClient, *, machine_count: int = 0, is_terminal: bool = False
    ) -> None:
        admin = admin_of(client)
        department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        self.area_id = int(
            _ok(
                admin.post(
                    "/api/areas",
                    json={
                        "department_id": department["id"],
                        "name": _unique("AREA"),
                        "is_terminal": is_terminal,
                    },
                ),
                201,
            )["id"]
        )
        self.operation_id = int(
            _ok(
                admin.post(
                    "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
                ),
                201,
            )["id"]
        )
        self.station_id = str(
            _ok(
                admin.post(
                    "/api/scan-stations",
                    json={"station_id": _unique("ST"), "area_id": self.area_id},
                ),
                201,
            )["station_id"]
        )
        if machine_count:
            _ok(
                admin.put(
                    "/api/barcode-configuration/machine-asset-tag-format",
                    json={"prefix": "CD-", "digits": 4},
                )
            )
        self.machine_ids = [
            int(
                _ok(
                    admin.post(
                        "/api/machines", json={"area_id": self.area_id, "name": _unique("Lathe")}
                    ),
                    201,
                )["id"]
            )
            for _ in range(machine_count)
        ]


def _release(client: TestClient, cell: _Cell, quantity: int = 10) -> tuple[int, str, int]:
    """Release ``quantity`` of a new PN into ``cell``: (flow id, PN, demand id)."""
    pn = _unique("PN")
    admin = admin_of(client)
    work_order = _ok(
        admin.post(
            "/api/work-orders",
            json={"lines": [{"part_number": pn, "requested_quantity": quantity}]},
        ),
        201,
    )
    demand_id = int(work_order["demands"][0]["id"])
    released = _ok(
        admin.post(
            f"/api/work-orders/{work_order['id']}/demands/{demand_id}/release",
            json={
                "part_number": pn,
                "quantity": quantity,
                "route_mode": "FLOATING",
                "starting_area_id": cell.area_id,
                "operation_id": cell.operation_id,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )
    return int(released["quantity_flow_id"]), pn, demand_id


def _worker(client: TestClient) -> dict[str, Any]:
    return _ok(
        admin_of(client).post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )


def _set_mode(client: TestClient, cell: _Cell, mode: str, worker_id: int | None = None) -> None:
    body: dict[str, Any] = {"worker_identification_mode": mode}
    if worker_id is not None:
        body["fixed_worker_id"] = worker_id
    _ok(admin_of(client).patch(f"/api/areas/{cell.area_id}", json=body))


def _transfer_body(source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int = 10) -> Any:
    return {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "source_area_id": source.area_id,
        "target_area_id": target.area_id,
        "quantity": quantity,
        "device_event_id": str(uuid.uuid4()),
    }


def _post(client: TestClient, path: str, body: Any, request_id: str) -> Any:
    return client.post(path, json=body, headers={"X-Request-ID": request_id})


# ---------------------------------------------------------------------------
# Request id and record shape
# ---------------------------------------------------------------------------


def test_routine_read_record(client: TestClient) -> None:
    """RL-1."""
    admin = admin_of(client)
    with capture_json_logs() as logs:
        response = admin.get("/api/areas")
    assert response.status_code == 200
    assert len(response.headers.get_list("x-request-id")) == 1
    request_id = response.headers["x-request-id"]
    assert _HEX32.fullmatch(request_id)
    record = _access(logs, request_id)
    assert list(record) == _ACCESS_KEYS
    assert record["level"] == "DEBUG"
    assert record["route"] == "/api/areas" and record["path"] is None
    assert record["status"] == 200 and record["outcome"] == "ok"
    assert record["duration_ms"] >= 0 and record["slow"] is False
    assert record["refusal"] is None
    # A public read resolves no principal.
    assert record["context"] == {}
    assert record["message"].startswith("GET /api/areas 200 ")
    # A signed-in read names the User by id only.
    with capture_json_logs() as logs:
        users = admin.get("/api/users", headers={"X-Request-ID": "rl1-users"})
    assert users.status_code == 200
    assert _access(logs, "rl1-users")["context"] == {"user_id": admin.user_id}


@pytest.mark.parametrize("offered", ["abc-123._X", "A" * 64])
def test_conforming_request_id_is_used(client: TestClient, offered: str) -> None:
    """RL-2."""
    with capture_json_logs() as logs:
        response = client.get("/api/health/live", headers={"X-Request-ID": offered})
    assert response.headers.get_list("x-request-id") == [offered]
    assert _access(logs, offered)["route"] == "/api/health/live"


@pytest.mark.parametrize("offered", ["B" * 65, "a b", "x<y", "ü", ""])
def test_non_conforming_request_id_is_replaced(client: TestClient, offered: str) -> None:
    """RL-3."""
    with capture_json_logs() as logs:
        response = client.get("/api/health/live", headers={"X-Request-ID": offered.encode("utf-8")})
    echoed = response.headers.get_list("x-request-id")
    assert len(echoed) == 1 and _HEX32.fullmatch(echoed[0])
    _access(logs, echoed[0])
    if offered:
        assert offered not in logs.text
        assert offered not in echoed[0]


def test_health_records_are_debug(client: TestClient) -> None:
    """RL-4."""
    with capture_json_logs() as logs:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health/live").status_code == 200
    records = logs.access()
    assert {record["route"] for record in records} == {"/api/health", "/api/health/live"}
    assert {record["level"] for record in records} == {"DEBUG"}


class _FakeClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


def test_failing_health_polls_stay_below_info(
    module_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RL-4b: a schema mismatch and an unreachable database, 100 polls each."""
    clock = _FakeClock()
    throttle = schema_revision.REVISION_READ_FAILURE_THROTTLE
    monkeypatch.setattr(throttle, "clock", clock)
    throttle.reset()
    real_read_revision = schema_revision.read_revision
    try:
        with TestClient(create_app()) as fresh, capture_json_logs() as logs:
            monkeypatch.setattr(
                schema_revision, "read_revision", lambda connection: "0031_phase14_beyond_demand"
            )
            assert {fresh.get("/api/health").status_code for _ in range(100)} == {503}
            mismatch_records = logs.records
            logs.clear()

            monkeypatch.setattr(schema_revision, "read_revision", real_read_revision)
            engine = cast(Any, fresh.app).state.engine
            engine.dispose()

            def refuse(*args: Any, **kwargs: Any) -> Any:
                raise psycopg.OperationalError("connection refused (test)")

            event.listen(engine, "do_connect", refuse)
            try:
                assert {fresh.get("/api/health").status_code for _ in range(100)} == {503}
                first_minute = logs.records
                logs.clear()
                clock.now += 60
                assert {fresh.get("/api/health").status_code for _ in range(10)} == {503}
                second_minute = logs.records
            finally:
                event.remove(engine, "do_connect", refuse)
    finally:
        throttle.reset()

    for records in (mismatch_records, first_minute, second_minute):
        access = [record for record in records if record["logger"] == "app.access"]
        assert access and {record["level"] for record in access} == {"DEBUG"}
    assert not [record for record in mismatch_records if record["level"] == "ERROR"]
    for records in (first_minute, second_minute):
        errors = [record for record in records if record["level"] in ("ERROR", "CRITICAL")]
        assert [record["logger"] for record in errors] == [schema_revision.__name__]
        assert "traceback" in errors[0]


def test_unmatched_path_record(client: TestClient) -> None:
    """RL-5."""
    long_path = "/api/" + "x" * 295
    with capture_json_logs() as logs:
        first = client.get("/api/no-such-route", headers={"X-Request-ID": "rl5-a"})
        second = client.get(long_path, headers={"X-Request-ID": "rl5-b"})
    assert first.status_code == second.status_code == 404
    record = _access(logs, "rl5-a")
    assert record["level"] == "INFO" and record["outcome"] == "refused"
    assert record["route"] is None and record["path"] == "/api/no-such-route"
    assert _access(logs, "rl5-b")["path"] == long_path[:200]


def test_unhandled_failure_record(client: TestClient) -> None:
    """RL-6."""
    failing = TestClient(client.app, raise_server_exceptions=False)
    with capture_json_logs() as logs:
        response = failing.get(_FAILURE_PATH, headers={"X-Request-ID": "rl6"})
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    record = _access(logs, "rl6")
    assert record["level"] == "ERROR" and record["status"] == 500
    assert record["outcome"] == "error"
    assert record["refusal"] == {"type": "RuntimeError", "code": "internal_error", "message": None}
    assert "secret-xyz" not in " ".join(
        line for line in logs.lines if '"logger":"app.access"' in line
    )


# ---------------------------------------------------------------------------
# Command context
# ---------------------------------------------------------------------------


def test_refused_transfer_names_its_context(client: TestClient) -> None:
    """RL-7."""
    source, target = _Cell(client), _Cell(client)
    flow_id, pn, _ = _release(client, source)
    path = f"/api/scan-stations/{target.station_id}/transfers"

    missing = _transfer_body(source, target, 987654321, pn.lower())
    wrong_pn = _transfer_body(source, target, flow_id, _unique("OTHER"))
    with capture_json_logs() as logs:
        refused = _post(client, path, missing, "rl7-missing")
        late = _post(client, path, wrong_pn, "rl7-late")
    assert refused.status_code == 422, refused.text
    assert late.status_code in (409, 422), late.text

    record = _access(logs, "rl7-missing")
    assert record["level"] == "INFO" and record["outcome"] == "refused"
    assert record["refusal"]["type"] == "InvalidInputError"
    context = record["context"]
    assert set(context) == {
        "station_device_id",
        "station_id",
        "part_number",
        "quantity_flow_id",
        "quantity",
        "source_area_id",
        "target_area_id",
        "device_event_id",
    }
    assert isinstance(context["station_device_id"], int)
    assert context["station_id"] == target.station_id
    assert context["part_number"] == pn
    assert context["quantity_flow_id"] == 987654321 and context["quantity"] == 10
    assert context["source_area_id"] == source.area_id
    assert context["target_area_id"] == target.area_id
    assert context["device_event_id"] == missing["device_event_id"]

    after_station = _access(logs, "rl7-late")["context"]
    assert after_station["area_id"] == target.area_id
    assert "worker_id" not in after_station


def test_committed_context_comes_from_the_result(client: TestClient) -> None:
    """RL-7b (+ RL-9 FIXED): machine assignment and area completion bodies carry no Area."""
    cell = _Cell(client, machine_count=1)
    worker = _worker(client)
    _set_mode(client, cell, "FIXED", int(worker["id"]))
    flow_id, pn, _ = _release(client, cell)
    base = f"/api/scan-stations/{cell.station_id}"
    body = {
        "part_number": pn,
        "quantity_flow_id": flow_id,
        "machine_id": cell.machine_ids[0],
        "quantity": 10,
    }
    with capture_json_logs() as logs:
        assigned = _ok(
            _post(
                client,
                f"{base}/machine-assignments",
                {**body, "device_event_id": str(uuid.uuid4())},
                "rl7b-assign",
            ),
            201,
        )
        done = _ok(
            _post(
                client,
                f"{base}/area-completions",
                {**body, "device_event_id": str(uuid.uuid4())},
                "rl7b-done",
            ),
            201,
        )
    for request_id, result in (("rl7b-assign", assigned), ("rl7b-done", done)):
        record = _access(logs, request_id)
        assert record["outcome"] == "created" and record["level"] == "INFO"
        context = record["context"]
        assert context["area_id"] == cell.area_id == result["area_id"]
        assert context["operation_id"] == result["operation_id"]
        assert context["machine_id"] == cell.machine_ids[0]
        assert context["worker_id"] == worker["id"]


def test_allocation_records_name_the_device_station(client: TestClient, db_engine: Engine) -> None:
    """RL-7c."""
    source, stockroom, other = _Cell(client), _Cell(client, is_terminal=True), _Cell(client)
    flow_id, pn, demand_id = _release(client, source)
    stocked = _post(
        client,
        f"/api/scan-stations/{stockroom.station_id}/stockings",
        {
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": source.area_id,
            "target_area_id": stockroom.area_id,
            "quantity": 10,
            "device_event_id": str(uuid.uuid4()),
        },
        _rid(),
    )
    _ok(stocked, 201)
    token = enroll_station_device(db_engine, stockroom.station_id)
    device = station_device_headers(token)

    def allocation(station_id: str) -> dict[str, Any]:
        return {
            "part_number": pn,
            "allocation_quantity": 10,
            "lines": [{"work_order_demand_id": demand_id, "quantity": 10}],
            "station_id": station_id,
            "device_event_id": str(uuid.uuid4()),
        }

    anonymous = anonymous_client(client)
    with capture_json_logs() as logs:
        created = anonymous.post(
            "/api/allocations",
            json=allocation(stockroom.station_id),
            headers={**device, "X-Request-ID": "rl7c-created"},
        )
        mismatch = anonymous.post(
            "/api/allocations",
            json=allocation(other.station_id),
            headers={**device, "X-Request-ID": "rl7c-mismatch"},
        )
        foreign = anonymous.get(
            f"/api/scan-stations/{other.station_id}/context",
            headers={**device, "X-Request-ID": "rl7c-foreign"},
        )
        missing = anonymous.post(
            "/api/allocations",
            json=allocation(stockroom.station_id),
            headers={"X-Request-ID": "rl7c-missing"},
        )
    assert created.status_code == 201, created.text
    assert mismatch.status_code == foreign.status_code == 403
    assert missing.status_code == 401
    for request_id in ("rl7c-created", "rl7c-mismatch", "rl7c-foreign"):
        context = _access(logs, request_id)["context"]
        assert context["station_id"] == stockroom.station_id, request_id
        assert isinstance(context["station_device_id"], int), request_id
    assert _access(logs, "rl7c-mismatch")["refusal"]["code"] == "station_device_mismatch"
    assert _access(logs, "rl7c-created")["outcome"] == "created"
    absent = _access(logs, "rl7c-missing")
    assert absent["refusal"]["code"] == "station_device_required"
    assert "station_id" not in absent["context"] and "station_device_id" not in absent["context"]


def test_replay_context_equals_the_original_without_the_worker(client: TestClient) -> None:
    """RL-8."""
    source, target = _Cell(client), _Cell(client)
    worker = _worker(client)
    _set_mode(client, target, "FIXED", int(worker["id"]))
    flow_id, pn, _ = _release(client, source)
    path = f"/api/scan-stations/{target.station_id}/transfers"
    body = _transfer_body(source, target, flow_id, pn)
    with capture_json_logs() as logs:
        _ok(_post(client, path, body, "rl8-first"), 201)
        _ok(_post(client, path, body, "rl8-replay"), 200)
    first, replay = _access(logs, "rl8-first"), _access(logs, "rl8-replay")
    assert (first["outcome"], replay["outcome"]) == ("created", "replayed")
    assert first["context"]["worker_id"] == worker["id"]
    expected = {key: value for key, value in first["context"].items() if key != "worker_id"}
    assert replay["context"] == expected
    for record in (first, replay):
        assert record["context"]["area_id"] == target.area_id
        assert isinstance(record["context"]["operation_id"], int)


def test_worker_context_by_identification_mode(client: TestClient) -> None:
    """RL-9."""
    worker = _worker(client)
    scanned_source, scanned = _Cell(client), _Cell(client)
    _set_mode(client, scanned, "SCANNED")
    _ok(
        client.post(
            f"/api/scan-stations/{scanned.station_id}/badge-scans",
            json={"badge": worker["badge_barcode"]},
        )
    )
    disabled_source, disabled = _Cell(client), _Cell(client)
    flow_id, pn, _ = _release(client, scanned_source)
    other_flow, other_pn, _ = _release(client, disabled_source)
    with capture_json_logs() as logs:
        moved = _post(
            client,
            f"/api/scan-stations/{scanned.station_id}/transfers",
            _transfer_body(scanned_source, scanned, flow_id, pn),
            "rl9-scanned-transfer",
        )
        _ok(moved, 201)
        _ok(
            _post(
                client,
                f"/api/scan-stations/{scanned.station_id}/area-completions",
                {
                    "part_number": pn,
                    "quantity_flow_id": flow_id,
                    "quantity": 10,
                    "device_event_id": str(uuid.uuid4()),
                    # The default final gate of a DONE in Scanned session mode.
                    "confirming_badge": worker["badge_barcode"],
                },
                "rl9-scanned-done",
            ),
            201,
        )
        _ok(
            _post(
                client,
                f"/api/scan-stations/{disabled.station_id}/transfers",
                _transfer_body(disabled_source, disabled, other_flow, other_pn),
                "rl9-disabled",
            ),
            201,
        )
    for request_id in ("rl9-scanned-transfer", "rl9-scanned-done"):
        context = _access(logs, request_id)["context"]
        assert context["worker_id"] == worker["id"], request_id
        assert context["area_id"] == scanned.area_id, request_id
        assert isinstance(context["operation_id"], int), request_id
    assert "worker_id" not in _access(logs, "rl9-disabled")["context"]
    assert worker["badge_barcode"] not in logs.text


def test_middleware_and_designed_refusals_are_info(
    client: TestClient, module_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RL-10."""
    with capture_json_logs() as logs:
        csrf = client.post(
            "/api/session",
            json={"login_name": "nobody", "password": "x"},
            headers={"X-Request-ID": "rl10-csrf"},
        )
        busy = client.post(_BUSY_PATH, headers={"X-Request-ID": "rl10-busy"})
    assert csrf.status_code == 403 and busy.status_code == 503

    monkeypatch.setenv("ENFORCE_CLIENT_RELEASE", "true")
    monkeypatch.setenv("RELEASE_TAG", "v9.9.9-test")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as enforcing, capture_json_logs() as gate_logs:
            mismatch = enforcing.post(
                "/api/session",
                json={"login_name": "nobody", "password": "x"},
                headers={**CSRF_HEADERS, "X-Request-ID": "rl10-release"},
            )
    finally:
        monkeypatch.delenv("ENFORCE_CLIENT_RELEASE")
        monkeypatch.delenv("RELEASE_TAG")
        get_settings.cache_clear()
    assert mismatch.status_code == 409

    monkeypatch.setattr(
        schema_revision, "read_database_revision", lambda engine: "0031_phase14_beyond_demand"
    )
    with TestClient(create_app()) as not_ready, capture_json_logs() as ready_logs:
        refused = not_ready.post(
            "/api/session",
            json={"login_name": "nobody", "password": "x"},
            headers={**CSRF_HEADERS, "X-Request-ID": "rl10-not-ready"},
        )
    assert refused.status_code == 503

    for records, request_id, code in (
        (logs, "rl10-csrf", "csrf_rejected"),
        (logs, "rl10-busy", "password_check_busy"),
        (gate_logs, "rl10-release", "release_mismatch"),
        (ready_logs, "rl10-not-ready", "not_ready"),
    ):
        record = _access(records, request_id)
        assert record["refusal"]["code"] == code
        assert record["level"] == "INFO" and record["outcome"] == "refused"
        assert "release gate refused" not in records.text
        assert not [line for line in records.records if line["level"] == "ERROR"]


def test_validation_refusal_keeps_type_and_location(client: TestClient) -> None:
    """RL-11."""
    cell = _Cell(client)
    body = {
        "part_number": "PN-1",
        "quantity_flow_id": 1,
        "source_area_id": cell.area_id,
        "target_area_id": cell.area_id,
        "quantity": "s3cr3t-value",
        "device_event_id": str(uuid.uuid4()),
    }
    with capture_json_logs() as logs:
        response = _post(client, f"/api/scan-stations/{cell.station_id}/transfers", body, "rl11")
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "int_type",
                "loc": ["body", "quantity"],
                "msg": "Input should be a valid integer",
            }
        ]
    }
    record = _access(logs, "rl11")
    assert record["refusal"] == {
        "type": "RequestValidationError",
        "code": "request_invalid",
        "message": None,
        "errors": [{"type": "int_type", "loc": ["body", "quantity"]}],
    }
    assert "s3cr3t-value" not in logs.text


def test_concurrent_requests_keep_their_own_context(client: TestClient) -> None:
    """RL-12."""
    source, target = _Cell(client), _Cell(client)
    _, pn, _ = _release(client, source)
    path = f"/api/scan-stations/{target.station_id}/transfers"
    flows = {f"rl12-{index:02d}": 900000 + index for index in range(20)}
    barrier = threading.Barrier(len(flows))
    statuses: dict[str, int] = {}

    def send(request_id: str, flow_id: int) -> None:
        barrier.wait()
        statuses[request_id] = _post(
            client, path, _transfer_body(source, target, flow_id, pn), request_id
        ).status_code

    # Enrolls the station's device before the concurrent requests.
    _post(client, path, _transfer_body(source, target, 899999, pn), _rid())
    with capture_json_logs() as logs:
        threads = [
            threading.Thread(target=send, args=(request_id, flow_id))
            for request_id, flow_id in flows.items()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    assert set(statuses.values()) == {422}
    for request_id, flow_id in flows.items():
        assert _access(logs, request_id)["context"]["quantity_flow_id"] == flow_id


def test_bind_rules() -> None:
    """RL-13."""
    log_context.bind(quantity=1)  # outside a request: a no-op
    with pytest.raises(ValueError):
        log_context.bind(foo=1)
    with pytest.raises(ValueError):
        log_context.bind(quantity=1.5)
    with pytest.raises(ValueError):
        log_context.bind(quantity=True)
    with pytest.raises(ValueError):
        log_context.bind(part_number_invalid=True)

    async def run() -> log_context.RequestLogContext:
        context = log_context.start("rl13")
        log_context.bind(part_number="a\nb" * 100, quantity_flow_ids=list(range(25)))
        log_context.bind(quantity=3)
        log_context.bind(quantity=None)
        return context

    context = asyncio.run(run())
    stored = context.fields["part_number"]
    assert isinstance(stored, str)
    assert len(stored) == 128 and stored.startswith("a?ba?b") and "\n" not in stored
    assert context.fields["quantity_flow_ids"] == list(range(20))
    assert context.fields["quantity_flow_ids_truncated"] is True
    assert "quantity" not in context.fields


def test_invalid_part_number_is_never_logged(client: TestClient) -> None:
    """RL-15."""
    source, target = _Cell(client), _Cell(client)
    body = _transfer_body(source, target, 1, "PART-NUMBER WITH SPACE")
    with capture_json_logs() as logs:
        response = _post(client, f"/api/scan-stations/{target.station_id}/transfers", body, "rl15")
    assert response.status_code in (409, 422)
    context = _access(logs, "rl15")["context"]
    assert context["part_number"] is None and context["part_number_invalid"] is True
    assert "PART-NUMBER WITH SPACE" not in logs.text


def test_slow_read_is_info(client: TestClient) -> None:
    """RL-16."""
    with capture_json_logs() as logs:
        assert client.get(_SLOW_PATH, headers={"X-Request-ID": "rl16"}).status_code == 200
    record = _access(logs, "rl16")
    assert record["level"] == "INFO" and record["slow"] is True and record["outcome"] == "ok"


def test_client_disconnect_record() -> None:
    """RL-17: the client disconnects while the body is read."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await Request(scope, receive).body()

    middleware = request_log.RequestLogMiddleware(app)
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/rl17",
        "headers": [(b"x-request-id", b"rl17")],
        "client": ("10.0.0.9", 50000),
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    async def run() -> None:
        await middleware(scope, receive, send)

    with capture_json_logs() as logs, pytest.raises(ClientDisconnect):
        asyncio.run(run())
    assert sent == []
    record = _access(logs, "rl17")
    assert record["status"] == 499 and record["outcome"] == "error"
    assert record["refusal"]["code"] == "client_disconnected"
    assert record["client"] == "10.0.0.9"


# ---------------------------------------------------------------------------
# Static guards
# ---------------------------------------------------------------------------

_COMMAND_ROUTES = {
    "scan_station.py": {
        "transfer_to_station_area": "bind_command(body, station_id=station_id)",
        "stock_at_station_area": "bind_command(body, station_id=station_id)",
        "assign_to_machine": "bind_command(body, station_id=station_id)",
        "release_to_queue": "bind_command(body, station_id=station_id)",
        "complete_area_processing": "bind_command(body, station_id=station_id)",
        "merge_flows": "bind_command(body, station_id=station_id)",
        "scrap_quantity": "bind_command(body, station_id=station_id)",
        "add_quantity": "bind_command(body, station_id=station_id)",
        "receive_quantity": "bind_command(body, station_id=station_id)",
        "undo_production_command": "bind_command(body, station_id=station_id)",
    },
    "production_release.py": {
        "release_to_production": (
            "bind_command(body, work_order_id=work_order_id, demand_id=demand_id)"
        ),
    },
    "allocations.py": {
        "confirm_allocation": "bind_command(body)",
        "allocate_from_stock": "bind_command(body)",
        "reverse_allocation": "bind_command(body, allocation_id=allocation_id)",
        "allocate_beyond_demand": "bind_command(body)",
    },
    "route_adjustments.py": {
        "adjust_assigned_route": "bind_command(body, quantity_flow_id=quantity_flow_id)",
    },
}
_STATION_ONLY = ("scan_badge", "resolve_scan", "resolve_machine_scan")
_NEVER_BOUND = {"confirming_badge", "badge", "barcode", "asset_tag", "reason"}


def _api_module(name: str) -> tuple[str, ast.Module]:
    source = (_BACKEND_DIR / "app" / "api" / name).read_text(encoding="utf-8")
    return source, ast.parse(source)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _statements(function: ast.FunctionDef) -> list[ast.stmt]:
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def test_command_routes_bind_their_context_first() -> None:
    """RL-14 (+ RL-19 for ``bind_result``)."""
    routes = 0
    for module, functions in _COMMAND_ROUTES.items():
        source, tree = _api_module(module)
        for name, expected in functions.items():
            statements = _statements(_function(tree, name))
            assert ast.get_source_segment(source, statements[0]) == expected, name
            calls = [ast.get_source_segment(source, statement) for statement in statements]
            assert "bind_result(result)" in calls, name
            last_call = max(
                index
                for index, statement in enumerate(statements)
                if isinstance(statement, ast.If)
                or (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "result"
                        for target in statement.targets
                    )
                )
            )
            assert calls.index("bind_result(result)") == last_call + 1, name
            routes += 1
    assert routes == 16
    source, tree = _api_module("scan_station.py")
    for name in _STATION_ONLY:
        statements = _statements(_function(tree, name))
        assert [ast.get_source_segment(source, statement) for statement in statements[:2]] == [
            "log_context.bind(station_id=station_id)",
            "log_context.suppress_refusal_message()",
        ], name


def test_no_endpoint_binds_a_secret_or_free_text() -> None:
    """RL-14."""
    for path in sorted((_BACKEND_DIR / "app" / "api").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = ast.get_source_segment(source, node.func) or ""
            if name.split(".")[-1] not in ("bind", "bind_command"):
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            assert not keywords & _NEVER_BOUND, (path.name, keywords)
            for argument in ast.walk(node):
                if isinstance(argument, ast.Attribute):
                    assert argument.attr not in _NEVER_BOUND, (path.name, argument.attr)
    assert not set(request_log.COMMAND_BODY_FIELDS) & _NEVER_BOUND
    assert "station_id" not in request_log.COMMAND_BODY_FIELDS


def test_station_device_binds_before_the_binding_check() -> None:
    """RL-19."""
    source, tree = _api_module("authorization.py")
    call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "RequireStationDevice"
    )
    method = next(
        node for node in call.body if isinstance(node, ast.FunctionDef) and node.name == "__call__"
    )
    text = ast.get_source_segment(source, method) or ""
    bind = text.index("log_context.bind(station_device_id=device.device_id")
    check = text.index("station_devices.require_station_binding(")
    assert bind < check


#: The interpolated expressions of every ``raise <Name>Error(f"…")`` message
#: under ``app/application`` (RL-18). A change here is a reviewed decision:
#: an expression that can hold raw request input reaches the access record's
#: refusal message. ``scan_station``'s ``tag`` is a raw scan (F19) — its
#: routes suppress the refusal message.
_FROZEN_INTERPOLATIONS: dict[str, list[str]] = {
    "allocations": [
        "allocated.get(demand.id, 0)",
        "allocation_id",
        "allocation_quantity",
        "demand.id",
        "demand.part_number",
        "demand.requested_quantity",
        "demand.work_order_id",
        "demand_id",
        "line.quantity",
        "max(position.available_stocked_quantity, 0)",
        "max(shortage, 0)",
        "pn",
        "position.active_allocated_quantity",
        "position.stocked_quantity",
        "quantity",
        "shortage",
        "station_id",
        "total",
        "work_order_id",
    ],
    "audit_trail": ["area_id", "row.id", "scope.part_number"],
    "authentication": ["login", "login_name.strip()", "role_name"],
    "backups": ["MAX_TEXT_LENGTH", "detail"],
    "common": ["label"],
    "database_roles": ["', '.join(codes)", "name"],
    "direct_processing": ["_ACTION", "context.area.name", "context.flow.id"],
    "environment": [
        "_ASSET_TAG_DIGITS_MAX",
        "_ASSET_TAG_DIGITS_MIN",
        "area.name",
        "area_id",
        "clean_station_id",
        "code",
        "department.name",
        "department_id",
        "name",
        "operation_id",
        "purpose",
        "station_id",
        "target_fixed",
        "worker.name",
    ],
    "first_run": ["chosen_role_id"],
    "hot_list": [
        "demand.allocated_quantity",
        "demand.part_number",
        "demand.requested_quantity",
        "label",
        "names",
    ],
    "hot_ranks": ["drifted", "stray", "unlocked"],
    "intake": [
        "area.id",
        "area.name",
        "len(candidates)",
        "part_number",
        "pn",
        "route_template_id",
        "station_id",
        "template.name",
    ],
    "lineage": ["assigned_route_id"],
    "machine_processing": [
        "action",
        "area.name",
        "context.area.name",
        "context.flow.id",
        "flow.current_area_id",
        "flow.id",
        "flow.part_number",
        "flow.quantity",
        "machine.name",
        "machine_id",
        "part_number",
        "quantity",
        "quantity_flow_id",
        "station_id",
    ],
    "machines": [
        "action",
        "area.name",
        "asset_tag",
        "assigned",
        "expected_asset_tag",
        "machine.name",
        "machine_id",
        "name",
    ],
    "merges": [
        "area.name",
        "flow.id",
        "flow.part_number",
        "flow_id",
        "machine_id",
        "pn",
        "station.area_id",
        "station_id",
    ],
    "migration": ["MAX_TEXT_LENGTH", "after", "report.expected_revision"],
    "part_numbers": ["_DETAIL_LABELS[field]", "canonical"],
    "production_board": ["department_id", "names"],
    "production_release": [
        "allocated",
        "already_released",
        "area.name",
        "demand.id",
        "demand.part_number",
        "demand.requested_quantity",
        "operation.code",
        "operation_id",
        "pn",
        "remaining",
        "route_template_id",
        "starting_area_id",
        "template.name",
        "work_order_demand_id",
        "work_order_id",
    ],
    "projections": ["flow_id", "movement_type"],
    "quantity_events": [
        "area.name",
        "context.flow.current_machine_id",
        "context.flow.id",
        "part_number",
        "pn",
        "station_id",
    ],
    "reconciliation": ["sorted(unknown)"],
    "roles": ["role_id"],
    "route_adjustments": ["flow.id"],
    "route_templates": [
        "area.name",
        "machine.name",
        "number",
        "operation.code",
        "step.area_id",
        "step.operation_id",
        "step.preferred_machine_id",
        "template.name",
        "template_id",
    ],
    "scan_station": [
        "area.department_id",
        "area.name",
        "area_id",
        "machine.asset_tag",
        "machine.name",
        "station_id",
        "tag",  # raw scan — route suppresses the message
    ],
    "station_devices": ["device_id", "station_id"],
    "station_identity": ["area_name", "fixed_worker_id", "station.area_id", "worker.name"],
    "tracking": ["before_allocation_id", "before_flow_id", "before_movement_id", "pn"],
    "transfers": [
        "'Stock' if stocking else 'Transfer'",
        "'stock' if stocking else 'transfer'",
        "'stocked' if stocking else 'transferred'",
        "area.name",
        "confirmed_quantity",
        "flow.current_machine_id",
        "flow.id",
        "flow.part_number",
        "flow.quantity",
        "nothing",
        "operation.code",
        "pn",
        "quantity_flow_id",
        "requested_operation_id",
        "source_area_id",
        "station.area_id",
        "station_id",
        "target.name",
    ],
    "undo": [
        "area.name",
        "area_id",
        "event_id",
        "flow_id",
        "ineligible",
        "machine.name",
        "machine_id",
        "pn",
        "reverses_id",
        "rows[0].part_number",
        "station_id",
    ],
    "users": ["display_name", "role_id", "suffix", "user_id"],
    "work_order_import": ["', '.join(bom)", "column"],
    "work_orders": [
        "action",
        "committed",
        "demand.part_number",
        "demand.priority_rank",
        "demand_id",
        "edit_id",
        "locked",
        "locked_id",
        "master.part_number",
        "number",
        "part_number",
        "quantity",
        "reason",
        "verb",
        "work_order.id",
        "work_order_id",
    ],
    "worker_sessions": ["area.name", "row.worker_id", "station.area_id", "station_id"],
    "workers": ["areas", "name", "suffix", "those", "worker.name", "worker_id"],
}


def _interpolations(source: str) -> list[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else ""
        )
        if not name.endswith("Error"):
            continue
        for argument in node.exc.args[:1]:
            for sub in ast.walk(argument):
                if isinstance(sub, ast.FormattedValue):
                    found.add(ast.get_source_segment(source, sub.value) or "")
    return sorted(found)


def test_error_message_interpolations_are_frozen() -> None:
    """RL-18."""
    actual: dict[str, list[str]] = {}
    for path in sorted((_BACKEND_DIR / "app" / "application").glob("*.py")):
        found = _interpolations(path.read_text(encoding="utf-8"))
        if found:
            actual[path.stem] = found
    assert actual == _FROZEN_INTERPOLATIONS


def test_log_context_is_framework_free() -> None:
    """LC-3."""
    tree = ast.parse((_BACKEND_DIR / "app" / "core" / "log_context.py").read_text("utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert not modules & {"fastapi", "starlette", "sqlalchemy", "pydantic", "psycopg"}
