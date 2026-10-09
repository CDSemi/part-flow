"""No secret in any structured log record (Phase 16 slice 6: LS-1 … LS-6b).

Extends the intent of the earlier secret tests (which read
``record.getMessage()`` only) to the production output: every record of a
scenario is formatted with ``JsonFormatter`` (``tests.log_capture``),
structured payloads and tracebacks included, and the whole text is
searched. The application runs against a dedicated temporary database
migrated to head; its first-run setup is still open when LS-2 runs first.
Unhandled failures go through a wrapper that logs on ``uvicorn.error`` the
way uvicorn does (same task, so the traceback record carries the request
id). The module database is dropped afterwards.
"""

import hashlib
import logging
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from starlette.types import ASGIApp, Receive, Scope, Send

from alembic import command
from app.api.dependencies import SessionDep
from app.application import transfers
from app.core.config import get_settings
from app.main import create_app
from tests.auth_harness import (
    CSRF_HEADERS,
    TEST_PASSWORD,
    admin_of,
    anonymous_client,
    client_as,
    enroll_station_device,
    station_device_client,
    station_device_headers,
)
from tests.log_capture import JsonLogs, capture_json_logs

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_log_secrets"
_TOKEN = re.compile(r"Setup token: ([A-Z2-7]{4}(?:-[A-Z2-7]{4})+)")
_DUPLICATE_BADGE_PATH = "/api/test-only/duplicate-badge"
_BAD_HASH_PATH = "/api/test-only/bad-hash"


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


class _Violation(BaseModel):
    value: str
    user_id: int | None = None


def _app_with_test_routes() -> FastAPI:
    app = create_app()

    @app.post(_DUPLICATE_BADGE_PATH)
    def duplicate_badge(body: _Violation, session: SessionDep) -> dict[str, str]:
        statement = sa.text("INSERT INTO workers (name, badge_barcode) VALUES ('dup', :badge)")
        values = {"badge": body.value}
        session.execute(statement, values)
        session.commit()
        return {"inserted": "yes"}

    @app.post(_BAD_HASH_PATH)
    def bad_hash(body: _Violation, session: SessionDep) -> dict[str, str]:
        statement = sa.text(
            "UPDATE user_credentials SET password_hash = :hash WHERE user_id = :user"
        )
        values = {"hash": body.value, "user": body.user_id}
        session.execute(statement, values)
        session.commit()
        return {"updated": "yes"}

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


def _uvicorn_like(app: ASGIApp) -> ASGIApp:
    """Log an escaping exception on ``uvicorn.error`` in the request's task, as uvicorn does."""

    async def wrapper(scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await app(scope, receive, send)
        except Exception:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application\n")
            raise

    return wrapper


def _failing_client(client: TestClient) -> TestClient:
    return TestClient(_uvicorn_like(client.app), raise_server_exceptions=False)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _assert_absent(logs: JsonLogs, *secrets: str) -> None:
    text = logs.text
    assert logs.lines, "nothing was captured"
    for secret in secrets:
        assert secret, "an empty secret proves nothing"
        assert secret not in text, secret


# ---------------------------------------------------------------------------
# LS-2 runs first: the module's database has no administrator yet.
# ---------------------------------------------------------------------------


def test_setup_token_is_announced_exactly_once(module_environment: None) -> None:
    """LS-2."""
    password = "first-administrator-password-ls2"
    with capture_json_logs() as logs, TestClient(create_app()) as fresh:
        announced = [match.group(1) for match in _TOKEN.finditer(logs.text)]
        assert len(announced) == 1
        token = announced[0]
        status = _ok(fresh.get("/api/setup"))
        role_id = next(role["id"] for role in status["eligible_roles"])
        _ok(
            fresh.post(
                "/api/setup/administrator",
                json={
                    "setup_token": token,
                    "login_name": f"admin-{uuid.uuid4().hex[:8]}",
                    "display_name": "LS-2 Administrator",
                    "role_id": role_id,
                    "password": password,
                },
                headers=CSRF_HEADERS,
            ),
            201,
        )
    carrying = [line for line in logs.lines if token in line]
    assert len(carrying) == 1
    assert '"logger":"app.first_run"' in carrying[0]
    assert '"level":"WARNING"' in carrying[0]
    _assert_absent(logs, password)


# ---------------------------------------------------------------------------
# Credentials, sessions, devices, badges
# ---------------------------------------------------------------------------


def test_sign_in_and_password_changes_log_no_secret(client: TestClient) -> None:
    """LS-1."""
    identity_client = client_as(client)
    identity = identity_client.identity
    assert identity is not None
    anonymous = anonymous_client(client)
    own_password = "Correct-Horse-Battery-77"
    admin_password = "Administrator-Chosen-88"
    wrong = "Not-The-Password-99"
    with capture_json_logs() as logs:
        refused = anonymous.post(
            "/api/session",
            json={"login_name": identity.login_name, "password": wrong},
            headers=CSRF_HEADERS,
        )
        signed = anonymous.post(
            "/api/session",
            json={"login_name": identity.login_name, "password": TEST_PASSWORD},
            headers=CSRF_HEADERS,
        )
        cookie = signed.cookies["partflow_session"]
        changed = anonymous.put(
            "/api/session/password",
            json={"current_password": TEST_PASSWORD, "new_password": own_password},
            headers={**CSRF_HEADERS, "Cookie": f"partflow_session={cookie}"},
        )
        new_cookie = changed.cookies["partflow_session"]
        reset = admin_of(client).put(
            f"/api/users/{identity.user_id}/password", json={"new_password": admin_password}
        )
    assert refused.status_code == 401
    assert signed.status_code == 200 and changed.status_code == 200, changed.text
    assert reset.status_code == 200, reset.text
    _assert_absent(
        logs,
        TEST_PASSWORD,
        own_password,
        admin_password,
        wrong,
        cookie,
        new_cookie,
        identity.token,
        identity.login_name,
    )
    assert "set-cookie" not in logs.text.lower()


class _Cell:
    def __init__(self, client: TestClient, *, machine: bool = False) -> None:
        admin = admin_of(client)
        department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        self.area_id = int(
            _ok(
                admin.post(
                    "/api/areas",
                    json={"department_id": department["id"], "name": _unique("AREA")},
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
        self.machine_id: int | None = None
        if machine:
            _ok(
                admin.put(
                    "/api/barcode-configuration/machine-asset-tag-format",
                    json={"prefix": "CD-", "digits": 4},
                )
            )
            self.machine_id = int(
                _ok(
                    admin.post(
                        "/api/machines", json={"area_id": self.area_id, "name": _unique("Lathe")}
                    ),
                    201,
                )["id"]
            )


def _release(client: TestClient, cell: _Cell, quantity: int = 10) -> tuple[int, str]:
    pn = _unique("PN")
    admin = admin_of(client)
    work_order = _ok(
        admin.post(
            "/api/work-orders",
            json={"lines": [{"part_number": pn, "requested_quantity": quantity}]},
        ),
        201,
    )
    released = _ok(
        admin.post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
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
    return int(released["quantity_flow_id"]), pn


def test_enrollment_codes_and_device_tokens_are_never_logged(client: TestClient) -> None:
    """LS-3."""
    cell = _Cell(client)
    _, pn = _release(client, cell)
    anonymous = anonymous_client(client)
    with capture_json_logs() as logs:
        issued = _ok(
            admin_of(client).post(
                f"/api/scan-stations/{cell.station_id}/device-enrollments",
                json={"label": "LS-3 terminal"},
            ),
            201,
        )
        code = str(issued["enrollment_code"])
        activated = _ok(
            anonymous.post(
                f"/api/scan-stations/{cell.station_id}/device-activations",
                json={"enrollment_code": code},
            ),
            201,
        )
        token = str(activated["device_token"])
        headers = station_device_headers(token)
        _ok(anonymous.get(f"/api/scan-stations/{cell.station_id}/context", headers=headers))
        anonymous.post(
            f"/api/scan-stations/{cell.station_id}/scraps",
            json={
                "part_number": pn,
                "quantity_flow_id": 1,
                "quantity": 1,
                "reason": "damaged",
                "device_event_id": str(uuid.uuid4()),
            },
            headers=headers,
        )
    canonical = code.replace("-", "")
    _assert_absent(
        logs,
        code,
        canonical,
        token,
        hashlib.sha256(token.encode("ascii")).hexdigest(),
    )


def test_badges_are_never_logged(client: TestClient) -> None:
    """LS-4."""
    cell = _Cell(client, machine=True)
    assert cell.machine_id is not None
    worker = _ok(
        admin_of(client).post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )
    badge = str(worker["badge_barcode"])
    unknown_badge = _unique("NOBADGE")
    _ok(
        admin_of(client).patch(
            f"/api/areas/{cell.area_id}", json={"worker_identification_mode": "SCANNED"}
        )
    )
    _ok(
        admin_of(client).put(
            "/api/policies/worker-sessions",
            json={
                "badge_confirm_done": True,
                "badge_confirm_queue": True,
                "badge_confirm_undo": True,
            },
        )
    )
    flow_id, pn = _release(client, cell)
    base = f"/api/scan-stations/{cell.station_id}"

    def in_area(extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "machine_id": cell.machine_id,
            "quantity": 10,
            "device_event_id": str(uuid.uuid4()),
            **extra,
        }

    with capture_json_logs() as logs:
        assert _ok(client.post(f"{base}/badge-scans", json={"badge": badge}))["outcome"] in (
            "SIGNED_IN",
            "REFRESHED",
        )
        client.post(f"{base}/scans/resolve", json={"barcode": badge})
        _ok(client.post(f"{base}/machine-assignments", json=in_area({})), 201)
        refused = client.post(
            f"{base}/machine-releases", json=in_area({"confirming_badge": unknown_badge})
        )
        assert refused.json().get("badge_not_recognized") is True, refused.text
        _ok(client.post(f"{base}/machine-releases", json=in_area({"confirming_badge": badge})), 201)
        _ok(client.post(f"{base}/machine-assignments", json=in_area({})), 201)
        done = in_area({"confirming_badge": badge})
        _ok(client.post(f"{base}/area-completions", json=done), 201)
        undone = client.post(
            f"{base}/undos",
            json={
                "part_number": pn,
                "reverses_device_event_id": done["device_event_id"],
                "device_event_id": str(uuid.uuid4()),
                "confirming_badge": badge,
            },
        )
        assert undone.status_code == 201, undone.text
        bad_undo = client.post(
            f"{base}/undos",
            json={
                "part_number": pn,
                "reverses_device_event_id": str(uuid.uuid4()),
                "device_event_id": str(uuid.uuid4()),
                "confirming_badge": unknown_badge,
            },
        )
        assert bad_undo.status_code >= 400
    _assert_absent(logs, badge, unknown_badge)


def test_badge_typed_as_an_asset_tag_is_never_logged(client: TestClient) -> None:
    """LS-4b."""
    cell = _Cell(client, machine=True)
    badge = _unique("BADGE")
    base = f"/api/scan-stations/{cell.station_id}/machine-scans/resolve"
    with capture_json_logs() as logs:
        manual = client.post(base, json={"asset_tag": badge}, headers={"X-Request-ID": "ls4b-a"})
        scanned = client.post(
            base, json={"barcode": f"PF:MACHINE:{badge}"}, headers={"X-Request-ID": "ls4b-b"}
        )
    assert manual.status_code == scanned.status_code == 404
    assert badge in manual.json()["detail"]  # the operator copy is unchanged
    _assert_absent(logs, badge)
    for request_id in ("ls4b-a", "ls4b-b"):
        record = next(line for line in logs.access() if line.get("request_id") == request_id)
        assert record["refusal"] == {"type": "NotFoundError", "code": None, "message": None}
        assert record["context"]["station_id"] == cell.station_id


# ---------------------------------------------------------------------------
# Configuration and database errors
# ---------------------------------------------------------------------------


def test_file_based_settings_never_reach_the_log(
    api_database_url: URL, module_environment: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LS-5."""
    password = str(api_database_url.password)
    dsn = api_database_url.render_as_string(hide_password=False)
    password_file = tmp_path / "database_password"
    password_file.write_text(password + "\n", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("DATABASE_HOST", str(api_database_url.host))
    monkeypatch.setenv("DATABASE_PORT", str(api_database_url.port or 5432))
    monkeypatch.setenv("DATABASE_NAME", str(api_database_url.database))
    monkeypatch.setenv("DATABASE_USER", str(api_database_url.username))
    monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(password_file))
    get_settings.cache_clear()
    try:
        with capture_json_logs() as logs:
            with TestClient(create_app()) as started:
                assert started.get("/api/health").status_code == 200
            monkeypatch.setenv("DATABASE_PASSWORD_FILE", str(tmp_path / "missing"))
            get_settings.cache_clear()
            with pytest.raises(ValidationError), TestClient(create_app()):
                pass
    finally:
        get_settings.cache_clear()
    _assert_absent(logs, password, dsn)


def test_database_error_during_a_command_hides_its_parameters(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LS-6."""
    secret = "-".join(("secret", "param"))
    cell = _Cell(client)

    def failing(session: Any, **kwargs: Any) -> Any:
        statement = sa.text("SELECT 1 / 0 WHERE CAST(:p AS text) IS NOT NULL")
        values = {"p": secret}
        return session.execute(statement, values)

    monkeypatch.setattr(transfers, "transfer_to_station_area", failing)
    device = station_device_headers(enroll_station_device(db_engine, cell.station_id))
    with capture_json_logs() as logs:
        response = _failing_client(client).post(
            f"/api/scan-stations/{cell.station_id}/transfers",
            json={
                "part_number": "PN-LS6",
                "quantity_flow_id": 1,
                "source_area_id": cell.area_id,
                "target_area_id": cell.area_id,
                "quantity": 1,
                "device_event_id": str(uuid.uuid4()),
            },
            headers={**device, "X-Request-ID": "ls6"},
        )
    assert response.status_code == 500
    _assert_absent(logs, secret)
    traceback_record = next(line for line in logs.records if "traceback" in line)
    assert traceback_record["request_id"] == "ls6"
    assert "sqlalchemy.exc.DataError: sqlstate=22012" in traceback_record["traceback"]
    access = next(line for line in logs.access() if line.get("request_id") == "ls6")
    assert access["level"] == "ERROR" and access["refusal"]["code"] == "internal_error"


def test_constraint_violations_never_print_the_row(client: TestClient, db_engine: Engine) -> None:
    """LS-6b: a duplicate badge and a CHECK violation on ``user_credentials``."""
    worker = _ok(
        admin_of(client).post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )
    badge = str(worker["badge_barcode"])
    identity = client_as(client).identity
    assert identity is not None
    bad_hash = "bcrypt$" + uuid.uuid4().hex
    failing = _failing_client(client)
    with capture_json_logs() as logs:
        duplicate = failing.post(
            _DUPLICATE_BADGE_PATH, json={"value": badge}, headers={"X-Request-ID": "ls6b-badge"}
        )
        rejected = failing.post(
            _BAD_HASH_PATH,
            json={"value": bad_hash, "user_id": identity.user_id},
            headers={"X-Request-ID": "ls6b-hash"},
        )
    assert duplicate.status_code == rejected.status_code == 500
    _assert_absent(logs, badge, identity.login_name, bad_hash)
    traces = {line["request_id"]: line["traceback"] for line in logs.records if "traceback" in line}
    assert (
        "sqlstate=23505 constraint=uq_workers_badge_barcode table=workers" in traces["ls6b-badge"]
    )
    assert (
        "sqlstate=23514 constraint=ck_user_credentials_password_hash_format"
        " table=user_credentials" in traces["ls6b-hash"]
    )
    with db_engine.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT password_hash FROM user_credentials WHERE user_id = :u"),
            {"u": identity.user_id},
        ).scalar_one()
    assert stored != bad_hash
