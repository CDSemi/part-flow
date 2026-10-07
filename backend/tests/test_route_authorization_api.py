"""Integration tests for Phase 14 slices 2–3 — route authorization.

Exercises the full request path against a dedicated temporary database
migrated to head by the real Alembic chain (owner decisions OD-P7,
OD-P10, OD-P19):

- every signed-in or permission route refuses an anonymous caller (401),
  a caller without the keys (403 naming the keys) and a caller who must
  first replace a temporary password (403) — writing nothing;
- every Administration write succeeds with exactly its keys, names the
  missing key(s) otherwise (the route's static keys when a static key
  is missing, else every key the request needed), and audits the
  signed-in User as ``actor_user_id``;
- the Area content rule (the timeout override is a Worker session policy
  edit);
- Worker badge values only for callers who may manage Workers;
- public and Scan Station routes stay anonymous; since slice 3 every
  Management route is checked too (its writes' allowed triples are in
  ``test_management_authorization_api``).

Identities come from ``tests.auth_harness``; set-up data is created
through the harness administrator.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.api.route_access import ROUTE_ACCESS, Access
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    IdentityClient,
    admin_of,
    anonymous_client,
    client_as,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_route_authorization_api"
_A2 = "Your account does not have permission to do this."
_V1 = "Your account does not have permission to view this."
_G1 = (
    "Granting or removing correction permissions, or the permission to manage them, needs"
    " the Manage correction permissions permission."
)
_G3 = (
    "Giving a user a role that holds correction permissions or the permission to manage"
    " them, or moving them out of such a role, needs the Manage correction permissions"
    " permission."
)
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16
_STATION_ID = "S1"
_FIXED_UUID = "00000000-0000-4000-8000-000000000001"

MD = Permission.MANAGE_DEPARTMENTS
MA = Permission.MANAGE_AREAS
MO = Permission.MANAGE_OPERATIONS
MW = Permission.MANAGE_WORKERS
MSS = Permission.MANAGE_SCAN_STATIONS
MBC = Permission.MANAGE_BARCODE_CONFIGURATION
MWSP = Permission.MANAGE_WORKER_SESSION_POLICIES
MCP = Permission.MANAGE_CORRECTION_PERMISSIONS
CSS = Permission.CONFIGURE_SYSTEM_SETTINGS
MUAR = Permission.MANAGE_USERS_AND_ROLES


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
    """Anonymous application client wired to the temporary database."""
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
# Helpers
# ---------------------------------------------------------------------------


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _count(engine: Engine, sql: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text(sql), params).scalar_one())


def _audit_count(engine: Engine) -> int:
    return _count(engine, "SELECT count(*) FROM audit_events")


def _denied(response: Any, required: list[Permission], detail: str = _A2) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["permission_denied"] is True
    assert body["detail"] == detail
    assert body["required_permissions"] == sorted(key.value for key in required)


def _concrete(path: str) -> str:
    return (
        path.replace("{station_id}", _STATION_ID)
        .replace("{device_event_id}", _FIXED_UUID)
        .replace("{department_id}", "1")
        .replace("{area_id}", "1")
        .replace("{operation_id}", "1")
        .replace("{worker_id}", "1")
        .replace("{user_id}", "1")
        .replace("{role_id}", "1")
        .replace("{machine_id}", "1")
        .replace("{template_id}", "1")
        .replace("{work_order_id}", "1")
        .replace("{demand_id}", "1")
        .replace("{allocation_id}", "1")
    )


_CHECKED = sorted(
    key
    for key, access in ROUTE_ACCESS.items()
    if access.access in (Access.SIGNED_IN, Access.PERMISSION)
)


# ---------------------------------------------------------------------------
# RZ-1 … RZ-3: every checked route, with no body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), _CHECKED, ids=lambda value: str(value))
def test_anonymous_callers_are_refused_everywhere(
    client: TestClient, db_engine: Engine, method: str, path: str
) -> None:
    """RZ-1."""
    before = _audit_count(db_engine)
    response = client.request(method, _concrete(path))
    assert response.status_code == 401, response.text
    assert response.json()["authentication_required"] is True
    assert _audit_count(db_engine) == before


@pytest.mark.parametrize(("method", "path"), _CHECKED, ids=lambda value: str(value))
def test_callers_without_the_keys_are_refused(
    client: TestClient, db_engine: Engine, method: str, path: str
) -> None:
    """RZ-2."""
    access = ROUTE_ACCESS[(method, path)]
    caller = client_as(client)
    before = _audit_count(db_engine)
    if access.access is Access.SIGNED_IN:
        if path == "/api/barcode-configuration/machine-asset-tag-format":
            # Read by Administration and Management → Machines (slice 3):
            # answered once a format exists.
            _ok(admin_of(client).put(path, json={"prefix": "RZ-", "digits": 4}))
        if method == "GET":
            _ok(caller.get(_concrete(path)))
        return
    if access.any_of:
        _denied(caller.request(method, _concrete(path)), list(access.any_of), _V1)
        assert caller.request(method, _concrete(path)).json()["any_permission"] is True
    elif access.requires:
        _denied(caller.request(method, _concrete(path)), list(access.requires))
    elif path == "/api/areas/{area_id}":
        _denied(caller.patch(_concrete(path), json={}), [MA])
    elif path == "/api/work-orders/{work_order_id}":
        _denied(caller.patch(_concrete(path), json={}), [Permission.MANAGE_WORK_ORDERS])
    elif path == "/api/hot-list/changes":
        # A valid Move body (same id set) — the content rule names Reorder.
        move = {
            "device_event_id": _FIXED_UUID,
            "action": "MOVE_DOWN",
            "expected_order": [1, 2],
            "new_order": [2, 1],
        }
        _denied(caller.post(path, json=move), [Permission.REORDER_HOT_ITEMS])
    else:
        assert path == "/api/roles/{role_id}"
        _denied(caller.patch(_concrete(path), json={}), [MUAR])
    assert _audit_count(db_engine) == before


@pytest.mark.parametrize(("method", "path"), _CHECKED, ids=lambda value: str(value))
def test_a_pending_password_change_refuses_everything_but_the_change(
    client: TestClient, db_engine: Engine, method: str, path: str
) -> None:
    """RZ-3: the policy requires the change (the default)."""
    access = ROUTE_ACCESS[(method, path)]
    caller = client_as(client, *ALL_PERMISSIONS, temporary_password=True)
    before = _audit_count(db_engine)
    response = caller.request(method, _concrete(path))
    if access.password_change_allowed:
        assert response.status_code == 422, response.text
    else:
        assert response.status_code == 403, response.text
        assert response.json()["password_change_required"] is True
    assert _audit_count(db_engine) == before


# ---------------------------------------------------------------------------
# RZ-4 / RZ-6: the allowed triple of every Administration write
# ---------------------------------------------------------------------------


@dataclass
class _Write:
    """One Administration write: the request and the keys it needs."""

    name: str
    method: str
    # The registry path of the route.
    route: str
    static: frozenset[Permission]
    required: frozenset[Permission]
    # Builds (path, request kwargs) against fresh set-up data.
    prepare: Callable[[TestClient], tuple[str, dict[str, Any]]]
    # (table, key column) of the row a refusal must leave untouched.
    table: str
    key_column: str = "id"
    detail: dict[Permission, str] = field(default_factory=dict)


def _department(client: TestClient) -> dict[str, Any]:
    return _ok(admin_of(client).post("/api/departments", json={"name": f"D {_suffix()}"}), 201)


def _area(client: TestClient) -> dict[str, Any]:
    department = _department(client)
    return _ok(
        admin_of(client).post(
            "/api/areas", json={"department_id": department["id"], "name": f"A {_suffix()}"}
        ),
        201,
    )


def _station(client: TestClient) -> dict[str, Any]:
    return _ok(
        admin_of(client).post(
            "/api/scan-stations",
            json={"station_id": f"ST-{_suffix()}", "area_id": _area(client)["id"]},
        ),
        201,
    )


def _worker(client: TestClient) -> dict[str, Any]:
    return _ok(
        admin_of(client).post(
            "/api/workers", json={"name": f"W {_suffix()}", "badge_barcode": f"B{_suffix()}"}
        ),
        201,
    )


def _role(client: TestClient, permissions: list[str] | None = None) -> dict[str, Any]:
    return _ok(
        admin_of(client).post(
            "/api/roles", json={"name": f"R {_suffix()}", "permissions": permissions or []}
        ),
        201,
    )


def _user(client: TestClient, role_id: int | None = None) -> dict[str, Any]:
    return _ok(
        admin_of(client).post(
            "/api/users",
            json={
                "login_name": f"u-{_suffix()}",
                "display_name": f"U {_suffix()}",
                "role_id": role_id if role_id is not None else _role(client)["id"],
            },
        ),
        201,
    )


def _image() -> dict[str, Any]:
    return {"content": _PNG + uuid.uuid4().bytes, "headers": {"Content-Type": "image/png"}}


def _with_worker_avatar(client: TestClient) -> tuple[str, dict[str, Any]]:
    worker = _worker(client)
    _ok(admin_of(client).put(f"/api/workers/{worker['id']}/avatar", **_image()))
    return f"/api/workers/{worker['id']}/avatar", {}


def _with_user_avatar(client: TestClient) -> tuple[str, dict[str, Any]]:
    user = _user(client)
    _ok(admin_of(client).put(f"/api/users/{user['id']}/avatar", **_image()))
    return f"/api/users/{user['id']}/avatar", {}


def _key(*keys: Permission) -> frozenset[Permission]:
    return frozenset(keys)


_WRITES: list[_Write] = [
    _Write(
        "department-create",
        "POST",
        "/api/departments",
        _key(MD),
        _key(MD),
        lambda c: ("/api/departments", {"json": {"name": f"D {_suffix()}"}}),
        "departments",
    ),
    _Write(
        "department-edit",
        "PATCH",
        "/api/departments/{department_id}",
        _key(MD),
        _key(MD),
        lambda c: (
            f"/api/departments/{_department(c)['id']}",
            {"json": {"board_seconds_per_row": 7}},
        ),
        "departments",
    ),
    _Write(
        "area-create",
        "POST",
        "/api/areas",
        _key(MA),
        _key(MA),
        lambda c: (
            "/api/areas",
            {"json": {"department_id": _department(c)["id"], "name": f"A {_suffix()}"}},
        ),
        "areas",
    ),
    _Write(
        "area-create-with-override",
        "POST",
        "/api/areas",
        _key(MA),
        _key(MA, MWSP),
        lambda c: (
            "/api/areas",
            {
                "json": {
                    "department_id": _department(c)["id"],
                    "name": f"A {_suffix()}",
                    "worker_session_timeout_minutes": 30,
                }
            },
        ),
        "areas",
    ),
    _Write(
        "area-edit",
        "PATCH",
        "/api/areas/{area_id}",
        _key(),
        _key(MA),
        lambda c: (f"/api/areas/{_area(c)['id']}", {"json": {"description": "edited"}}),
        "areas",
    ),
    _Write(
        "area-override-edit",
        "PATCH",
        "/api/areas/{area_id}",
        _key(),
        _key(MWSP),
        lambda c: (
            f"/api/areas/{_area(c)['id']}",
            {"json": {"worker_session_timeout_minutes": 45}},
        ),
        "areas",
    ),
    _Write(
        "operation-create",
        "POST",
        "/api/operations",
        _key(MO),
        _key(MO),
        lambda c: ("/api/operations", {"json": {"area_id": _area(c)["id"], "code": _suffix()}}),
        "operations",
    ),
    _Write(
        "operation-edit",
        "PATCH",
        "/api/operations/{operation_id}",
        _key(MO),
        _key(MO),
        lambda c: (
            "/api/operations/"
            + str(
                _ok(
                    admin_of(c).post(
                        "/api/operations", json={"area_id": _area(c)["id"], "code": _suffix()}
                    ),
                    201,
                )["id"]
            ),
            {"json": {"description": "edited"}},
        ),
        "operations",
    ),
    _Write(
        "scan-station-create",
        "POST",
        "/api/scan-stations",
        _key(MSS),
        _key(MSS),
        lambda c: (
            "/api/scan-stations",
            {"json": {"station_id": f"ST-{_suffix()}", "area_id": _area(c)["id"]}},
        ),
        "scan_stations",
        "station_id",
    ),
    _Write(
        "scan-station-edit",
        "PATCH",
        "/api/scan-stations/{station_id}",
        _key(MSS),
        _key(MSS),
        lambda c: (
            f"/api/scan-stations/{_station(c)['station_id']}",
            {"json": {"is_active": False}},
        ),
        "scan_stations",
        "station_id",
    ),
    _Write(
        "asset-tag-format",
        "PUT",
        "/api/barcode-configuration/machine-asset-tag-format",
        _key(MBC),
        _key(MBC),
        lambda c: (
            "/api/barcode-configuration/machine-asset-tag-format",
            {"json": {"prefix": f"T{_suffix()[:4].upper()}-", "digits": 5}},
        ),
        "machine_asset_tag_config",
    ),
    _Write(
        "worker-create",
        "POST",
        "/api/workers",
        _key(MW),
        _key(MW),
        lambda c: ("/api/workers", {"json": {"name": "W", "badge_barcode": f"B{_suffix()}"}}),
        "workers",
    ),
    _Write(
        "worker-edit",
        "PATCH",
        "/api/workers/{worker_id}",
        _key(MW),
        _key(MW),
        lambda c: (f"/api/workers/{_worker(c)['id']}", {"json": {"name": f"W {_suffix()}"}}),
        "workers",
    ),
    _Write(
        "worker-avatar-set",
        "PUT",
        "/api/workers/{worker_id}/avatar",
        _key(MW),
        _key(MW),
        lambda c: (f"/api/workers/{_worker(c)['id']}/avatar", _image()),
        "workers",
    ),
    _Write(
        "worker-avatar-remove",
        "DELETE",
        "/api/workers/{worker_id}/avatar",
        _key(MW),
        _key(MW),
        _with_worker_avatar,
        "workers",
    ),
    _Write(
        "worker-session-policy",
        "PUT",
        "/api/policies/worker-sessions",
        _key(MWSP),
        _key(MWSP),
        lambda c: (
            "/api/policies/worker-sessions",
            {"json": {"worker_session_timeout_minutes": int(uuid.uuid4().int % 700) + 10}},
        ),
        "application_policy",
    ),
    _Write(
        "correction-permissions-policy",
        "PUT",
        "/api/policies/correction-permissions",
        _key(MCP),
        _key(MCP),
        lambda c: (
            "/api/policies/correction-permissions",
            {
                "json": {
                    "undo_reason_required": not _ok(
                        admin_of(c).get("/api/policies/correction-permissions")
                    )["undo_reason_required"]
                }
            },
        ),
        "application_policy",
    ),
    _Write(
        "due-soon-policy",
        "PUT",
        "/api/policies/due-soon",
        _key(CSS),
        _key(CSS),
        lambda c: (
            "/api/policies/due-soon",
            {
                "json": {
                    "due_soon_min_days": 1 + int(uuid.uuid4().int % 5),
                    "due_soon_lead_time_percent": 20 + int(uuid.uuid4().int % 50),
                    "due_soon_max_days": 30,
                }
            },
        ),
        "application_policy",
    ),
    _Write(
        "retention-policy",
        "PUT",
        "/api/policies/data-retention",
        _key(CSS),
        _key(CSS),
        lambda c: (
            "/api/policies/data-retention",
            {"json": {"retention_period_months": 12 + int(uuid.uuid4().int % 1000)}},
        ),
        "application_policy",
    ),
    _Write(
        "sign-in-policy",
        "PUT",
        "/api/policies/sign-in",
        _key(CSS),
        _key(CSS),
        lambda c: (
            "/api/policies/sign-in",
            {"json": {"sign_in_lockout_attempts": 3 + int(uuid.uuid4().int % 90)}},
        ),
        "application_policy",
    ),
    _Write(
        "role-create",
        "POST",
        "/api/roles",
        _key(MUAR),
        _key(MUAR),
        lambda c: ("/api/roles", {"json": {"name": f"R {_suffix()}", "permissions": []}}),
        "roles",
    ),
    _Write(
        "role-create-protected",
        "POST",
        "/api/roles",
        _key(MUAR),
        _key(MUAR, MCP),
        lambda c: (
            "/api/roles",
            {"json": {"name": f"R {_suffix()}", "permissions": ["UNDO_RECENT_SCANS"]}},
        ),
        "roles",
        detail={MCP: _G1},
    ),
    _Write(
        "role-rename",
        "PATCH",
        "/api/roles/{role_id}",
        _key(),
        _key(MUAR),
        lambda c: (f"/api/roles/{_role(c)['id']}", {"json": {"name": f"R {_suffix()}"}}),
        "roles",
    ),
    _Write(
        "role-correction-grant",
        "PATCH",
        "/api/roles/{role_id}",
        _key(),
        _key(MCP),
        lambda c: (
            f"/api/roles/{_role(c)['id']}",
            {"json": {"grant_permissions": ["UNDO_RECENT_SCANS"]}},
        ),
        "roles",
        detail={MCP: _G1},
    ),
    _Write(
        "user-create",
        "POST",
        "/api/users",
        _key(MUAR),
        _key(MUAR),
        lambda c: (
            "/api/users",
            {
                "json": {
                    "login_name": f"u-{_suffix()}",
                    "display_name": "U",
                    "role_id": _role(c)["id"],
                }
            },
        ),
        "users",
    ),
    _Write(
        "user-create-protected",
        "POST",
        "/api/users",
        _key(MUAR),
        _key(MUAR, MCP),
        lambda c: (
            "/api/users",
            {
                "json": {
                    "login_name": f"u-{_suffix()}",
                    "display_name": "U",
                    "role_id": _role(c, ["UNDO_RECENT_SCANS"])["id"],
                }
            },
        ),
        "users",
        detail={MCP: _G3},
    ),
    _Write(
        "user-edit",
        "PATCH",
        "/api/users/{user_id}",
        _key(MUAR),
        _key(MUAR),
        lambda c: (f"/api/users/{_user(c)['id']}", {"json": {"display_name": f"U {_suffix()}"}}),
        "users",
    ),
    _Write(
        "user-avatar-set",
        "PUT",
        "/api/users/{user_id}/avatar",
        _key(MUAR),
        _key(MUAR),
        lambda c: (f"/api/users/{_user(c)['id']}/avatar", _image()),
        "users",
    ),
    _Write(
        "user-avatar-remove",
        "DELETE",
        "/api/users/{user_id}/avatar",
        _key(MUAR),
        _key(MUAR),
        _with_user_avatar,
        "users",
    ),
    _Write(
        "user-password",
        "PUT",
        "/api/users/{user_id}/password",
        _key(MUAR),
        _key(MUAR),
        lambda c: (
            f"/api/users/{_user(c)['id']}/password",
            {"json": {"new_password": "a-temporary-password"}},
        ),
        "user_credentials",
        "user_id",
    ),
]


def _state(engine: Engine, table: str, key_column: str, path: str) -> tuple[int, int, str | None]:
    """(table rows, audit rows, xmin of the row the path addresses, if any)."""
    rows = _count(engine, f"SELECT count(*) FROM {table}")
    audits = _audit_count(engine)
    target: str | None = None
    parts = path.rstrip("/").split("/")
    candidate = parts[3] if len(parts) > 3 else None
    if candidate is not None and table != "application_policy":
        with engine.connect() as connection:
            target = connection.execute(
                sa.text(f"SELECT xmin::text FROM {table} WHERE {key_column}::text = :key"),
                {"key": candidate},
            ).scalar_one_or_none()
    return rows, audits, target


#: The Management writes of Phase 14 slice 3 — their allowed triples are
#: MA-1 of ``test_management_authorization_api``.
_MANAGEMENT_WRITES = {
    ("POST", "/api/machines"),
    ("PATCH", "/api/machines/{machine_id}"),
    ("POST", "/api/machines/{machine_id}/maintenance"),
    ("DELETE", "/api/machines/{machine_id}/maintenance"),
    ("POST", "/api/machines/{machine_id}/retire"),
    ("POST", "/api/machines/{machine_id}/reactivate"),
    ("POST", "/api/part-numbers"),
    ("PATCH", "/api/part-numbers"),
    ("DELETE", "/api/part-numbers"),
    ("PUT", "/api/part-numbers/image"),
    ("DELETE", "/api/part-numbers/image"),
    ("POST", "/api/route-templates"),
    ("PUT", "/api/route-templates/{template_id}"),
    ("POST", "/api/route-templates/{template_id}/archive"),
    ("DELETE", "/api/route-templates/{template_id}"),
    ("POST", "/api/work-orders"),
    ("PATCH", "/api/work-orders/{work_order_id}"),
    ("DELETE", "/api/work-orders/{work_order_id}/demands/{demand_id}"),
    ("POST", "/api/work-orders/{work_order_id}/demands/{demand_id}/release"),
    ("POST", "/api/hot-list/changes"),
    ("POST", "/api/allocations/management"),
    ("POST", "/api/allocations/{allocation_id}/reversals"),
}


def test_the_write_table_covers_every_permission_route() -> None:
    """Every Administration write route has a row whose keys match the
    registry; together with the Management writes (MA-1) every write
    route with static or conditional keys is covered exactly once (the
    any-of reads are RZ-1 … RZ-3 and MA-2)."""
    permission_routes = {
        key
        for key, access in ROUTE_ACCESS.items()
        if access.access is Access.PERMISSION and not access.any_of
    }
    administration = {(write.method, write.route) for write in _WRITES}
    assert not administration & _MANAGEMENT_WRITES
    assert administration | _MANAGEMENT_WRITES == permission_routes
    for write in _WRITES:
        access = ROUTE_ACCESS[(write.method, write.route)]
        assert access.requires == write.static, write.name
        assert write.required - write.static <= access.conditional | access.requires, write.name


@pytest.mark.parametrize("write", _WRITES, ids=lambda write: write.name)
def test_each_write_needs_exactly_its_keys(
    client: TestClient, db_engine: Engine, write: _Write
) -> None:
    """RZ-4 and RZ-6."""
    path, kwargs = write.prepare(client)
    # Identities first: creating one adds role and user rows.
    missing_keys = sorted(write.required, key=lambda key: key.value)
    lacking = {key: client_as(client, *(write.required - {key})) for key in missing_keys}
    caller = client_as(client, *write.required)

    state = _state(db_engine, write.table, write.key_column, path)
    response = anonymous_client(client).request(write.method, path, **kwargs)
    assert response.status_code == 401, response.text
    assert _state(db_engine, write.table, write.key_column, path) == state

    for missing in missing_keys:
        expected = write.static if missing in write.static else write.required
        detail = write.detail.get(missing, _A2) if missing not in write.static else _A2
        _denied(lacking[missing].request(write.method, path, **kwargs), list(expected), detail)
        assert _state(db_engine, write.table, write.key_column, path) == state, missing

    response = caller.request(write.method, path, **kwargs)
    assert response.status_code in (200, 201), response.text
    with db_engine.connect() as connection:
        last = connection.execute(
            sa.text(
                "SELECT actor_user_id, actor_reference FROM audit_events ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert _audit_count(db_engine) > state[1]
    assert last.actor_user_id == caller.user_id
    assert last.actor_reference is None


def test_a_static_refusal_names_only_the_static_keys(client: TestClient) -> None:
    """RZ-4 explicit rows."""
    department = _department(client)
    override = {
        "department_id": department["id"],
        "name": f"A {_suffix()}",
        "worker_session_timeout_minutes": 30,
    }
    _denied(client_as(client, MWSP).post("/api/areas", json=override), [MA])
    _denied(client_as(client, MA).post("/api/areas", json=override), [MA, MWSP])
    roles = cast(list[dict[str, Any]], admin_of(client).get("/api/roles").json())
    operator_id = next(role["id"] for role in roles if role["name"] == "Operator")
    _denied(
        client_as(client, MCP).post(
            "/api/users",
            json={"login_name": f"u-{_suffix()}", "display_name": "U", "role_id": operator_id},
        ),
        [MUAR],
    )
    _denied(
        client_as(client).post(
            "/api/roles", json={"name": f"R {_suffix()}", "permissions": ["UNDO_RECENT_SCANS"]}
        ),
        [MUAR],
    )
    _denied(
        client_as(client, MUAR).post(
            "/api/users",
            json={"login_name": f"u-{_suffix()}", "display_name": "U", "role_id": operator_id},
        ),
        [MUAR, MCP],
        _G3,
    )


# ---------------------------------------------------------------------------
# RZ-5: the Area content rule
# ---------------------------------------------------------------------------


def test_the_area_timeout_override_is_a_worker_session_policy_edit(client: TestClient) -> None:
    """RZ-5."""
    path = f"/api/areas/{_area(client)['id']}"
    areas_only = client_as(client, MA)
    policy_only = client_as(client, MWSP)
    both = client_as(client, MA, MWSP)

    _ok(areas_only.patch(path, json={"name": f"A {_suffix()}"}))
    _denied(policy_only.patch(path, json={"name": f"A {_suffix()}"}), [MA])
    _ok(policy_only.patch(path, json={"worker_session_timeout_minutes": 30}))
    _ok(policy_only.patch(path, json={"worker_session_timeout_minutes": None}))
    _denied(areas_only.patch(path, json={"worker_session_timeout_minutes": 30}), [MWSP])
    _denied(areas_only.patch(path, json={"worker_session_timeout_minutes": None}), [MWSP])
    combined = {"description": "both", "worker_session_timeout_minutes": 20}
    _denied(areas_only.patch(path, json=combined), [MA, MWSP])
    _denied(policy_only.patch(path, json=combined), [MA, MWSP])
    assert _ok(both.patch(path, json=combined))["worker_session_timeout_minutes"] == 20

    department = _department(client)
    plain = {"department_id": department["id"], "name": f"A {_suffix()}"}
    _ok(areas_only.post("/api/areas", json=plain), 201)
    with_override = {**plain, "name": f"A {_suffix()}", "worker_session_timeout_minutes": 15}
    _denied(areas_only.post("/api/areas", json=with_override), [MA, MWSP])
    _ok(both.post("/api/areas", json=with_override), 201)


# ---------------------------------------------------------------------------
# RZ-7: Worker badge values
# ---------------------------------------------------------------------------


def test_worker_badges_are_listed_only_to_worker_managers(client: TestClient) -> None:
    """RZ-7."""
    worker = _worker(client)
    with_key = cast(list[dict[str, Any]], client_as(client, MW).get("/api/workers").json())
    without = client_as(client, MD).get("/api/workers")
    assert without.status_code == 200, without.text
    full = next(item for item in with_key if item["id"] == worker["id"])
    profile = next(item for item in without.json() if item["id"] == worker["id"])
    assert full["badge_barcode"] == worker["badge_barcode"]
    assert set(profile) == set(full) - {"badge_barcode"}
    assert all("badge_barcode" not in item for item in without.json())
    assert anonymous_client(client).get("/api/workers").status_code == 401


# ---------------------------------------------------------------------------
# RZ-8: public and Scan Station routes stay anonymous
# ---------------------------------------------------------------------------


def test_public_and_station_routes_need_no_sign_in(client: TestClient) -> None:
    """RZ-8: no cookie and no CSRF header."""
    area = _area(client)
    station = _ok(
        admin_of(client).post(
            "/api/scan-stations", json={"station_id": f"ST-{_suffix()}", "area_id": area["id"]}
        ),
        201,
    )
    worker = _worker(client)
    user = _user(client)
    anonymous: IdentityClient = anonymous_client(client)
    for path in (
        "/api/health",
        "/api/departments",
        "/api/areas",
        "/api/operations",
        "/api/scan-stations",
        f"/api/scan-stations/{station['station_id']}",
        "/api/machines",
        "/api/route-templates",
        "/api/policies/due-soon",
        f"/api/production-board?department_id={area['department_id']}",
        f"/api/scan-stations/{station['station_id']}/context",
    ):
        response = anonymous.get(path)
        assert response.status_code == 200, (path, response.text)
    # Image reads answer for a missing image themselves, never 401 / 403.
    for path in (
        f"/api/workers/{worker['id']}/avatar",
        f"/api/users/{user['id']}/avatar",
        "/api/part-numbers/image?number=NOPE",
    ):
        response = anonymous.get(path)
        assert response.status_code == 404, (path, response.text)
    # A station command is answered by the station itself (no sign-in asked).
    resolved = anonymous.post(
        f"/api/scan-stations/{station['station_id']}/scans/resolve",
        json={"part_number": f"PN-{_suffix()}"},
    )
    assert resolved.status_code not in (401, 403), resolved.text
    # A Management write needs a signed-in User since slice 3.
    machine = anonymous.post(
        "/api/machines",
        json={"name": f"M {_suffix()}", "area_id": station["area_id"]},
    )
    assert machine.status_code == 401, machine.text
