"""Integration tests for Phase 14 slice 2 — the permission-management guard,
the last-holder rule and their serialization (owner decision OD-P19).

Exercises the full request path against a dedicated temporary database
migrated to head by the real Alembic chain:

- PM: changing who holds a correction permission or the permission to
  manage them — a role's grants, a User's role, activity or password —
  needs ``MANAGE_CORRECTION_PERMISSIONS``; correction-key-only role
  changes need only that key; renames and avatars are never guarded;
  every refusal writes nothing;
- LH: no change may leave no active User with a password who may manage
  users and roles (L-1) or correction permissions (L-2); a no-op never
  counts; concurrent attempts leave a holder;
- LK: every role and user write waits on the
  ``partflow:user-administration`` advisory lock and judges the actor's
  keys and the guard's data as committed when it got the lock; an
  audited configuration write waits only behind a login rename of its
  actor;
- RC: the startup warning and the ``restore-correction-permission-management``
  recovery command.

Each case creates its own identities (``tests.auth_harness``); the
holder cases first deactivate every other User of this module's
database by SQL, so their holder counts are their own.
"""

import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    IdentityClient,
    TestIdentity,
    client_as,
    create_identity,
    station_device_client,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_permission_management_api"
_LOCK_KEY = "partflow:user-administration"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16
_NEW_PASSWORD = "a-temporary-password"

MUAR = Permission.MANAGE_USERS_AND_ROLES
MCP = Permission.MANAGE_CORRECTION_PERMISSIONS
UNDO = Permission.UNDO_RECENT_SCANS
MD = Permission.MANAGE_DEPARTMENTS
MWSP = Permission.MANAGE_WORKER_SESSION_POLICIES

_A1 = "You are not signed in, or your sign-in has ended. Sign in to continue."
_A2 = "Your account does not have permission to do this."
_G1 = (
    "Granting or removing correction permissions, or the permission to manage them, needs"
    " the Manage correction permissions permission."
)
_G2 = (
    "This user's role holds correction permissions or the permission to manage them."
    " Setting their password or changing whether they are active needs the Manage"
    " correction permissions permission."
)
_G3 = (
    "Giving a user a role that holds correction permissions or the permission to manage"
    " them, or moving them out of such a role, needs the Manage correction permissions"
    " permission."
)
_L1 = (
    "This change would leave no active user with a password who may manage users and"
    " roles. Give that permission to another active user first."
)
_L2 = (
    "This change would leave no active user with a password who may manage correction"
    " permissions. Give that permission to another active user first."
)
_WARNING = (
    "No active user with a password may manage correction permissions. Restore it with:"
    " python -m app.cli restore-correction-permission-management --role-name <role>"
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
    """Anonymous application client wired to the temporary database; the
    environment stays pointed at it for the module (the CLI reads it)."""
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
# Helpers
# ---------------------------------------------------------------------------

_TABLES = (
    "roles",
    "role_permissions",
    "users",
    "user_credentials",
    "user_sessions",
    "audit_events",
)


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _execute(engine: Engine, sql: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(sql), params)


def _snapshot(engine: Engine, *rows: tuple[str, int]) -> tuple[Any, ...]:
    """Row counts of every role/user table and the xmin of each target row."""
    with engine.connect() as connection:
        counts = tuple(
            connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in _TABLES
        )
        xmins = tuple(
            connection.execute(
                sa.text(f"SELECT xmin::text FROM {table} WHERE id = :id"), {"id": row_id}
            ).scalar_one_or_none()
            for table, row_id in rows
        )
    return counts + xmins


def _refused(
    engine: Engine,
    request: Callable[[], Any],
    status: int,
    detail: str,
    required: list[Permission] | None = None,
    targets: tuple[tuple[str, int], ...] = (),
) -> None:
    """The request is refused with ``detail`` and writes nothing (PM-6)."""
    before = _snapshot(engine, *targets)
    response = request()
    assert response.status_code == status, response.text
    body = response.json()
    assert body["detail"] == detail
    if required is not None:
        assert body["permission_denied"] is True
        assert body["required_permissions"] == sorted(key.value for key in required)
    if status == 409:
        assert body["last_permission_holder"] is True
    assert _snapshot(engine, *targets) == before


def _sql_role(engine: Engine, *permissions: Permission) -> int:
    with engine.begin() as connection:
        role_id = int(
            connection.execute(
                sa.text("INSERT INTO roles (name) VALUES (:name) RETURNING id"),
                {"name": f"PM {_suffix()}"},
            ).scalar_one()
        )
        for permission in permissions:
            connection.execute(
                sa.text("INSERT INTO role_permissions (role_id, permission) VALUES (:r, :p)"),
                {"r": role_id, "p": permission.value},
            )
    return role_id


def _sql_user(engine: Engine, role_id: int, *, active: bool = True) -> int:
    """A User without a password (no credential row)."""
    with engine.begin() as connection:
        return int(
            connection.execute(
                sa.text(
                    "INSERT INTO users (login_name, display_name, role_id, is_active)"
                    " VALUES (:login, :name, :role, :active) RETURNING id"
                ),
                {
                    "login": f"pm-{_suffix()}",
                    "name": f"PM {_suffix()}",
                    "role": role_id,
                    "active": active,
                },
            ).scalar_one()
        )


def _in_role(client: TestClient, engine: Engine, role_id: int) -> IdentityClient:
    """A signed-in User holding ``role_id`` (moved there by SQL)."""
    identity = create_identity(client)
    _execute(engine, "UPDATE users SET role_id = :r WHERE id = :id", r=role_id, id=identity.user_id)
    return IdentityClient(client, identity)


def _identity(acting: IdentityClient) -> TestIdentity:
    assert acting.identity is not None
    return acting.identity


def _seeded_role_id(engine: Engine, name: str) -> int:
    return int(_scalar(engine, "SELECT id FROM roles WHERE name = :name", name=name))


def _isolate(engine: Engine) -> None:
    """Every User of this module's database leaves: holder counts start at 0."""
    _execute(engine, "UPDATE users SET is_active = false WHERE is_active")


def _active(engine: Engine, user_id: int) -> bool:
    return bool(_scalar(engine, "SELECT is_active FROM users WHERE id = :id", id=user_id))


def _open_sessions(engine: Engine, user_id: int) -> int:
    return int(
        _scalar(
            engine,
            "SELECT count(*) FROM user_sessions WHERE user_id = :id AND ended_at IS NULL",
            id=user_id,
        )
    )


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


def _hold_lock(connection: sa.Connection) -> None:
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": _LOCK_KEY}
    )


def _new_user(role_id: int) -> dict[str, object]:
    return {"login_name": f"pm-{_suffix()}", "display_name": "PM", "role_id": role_id}


# ---------------------------------------------------------------------------
# PM — the guard
# ---------------------------------------------------------------------------


def test_creating_a_role_with_a_protected_key_needs_the_guard_key(
    client: TestClient, db_engine: Engine
) -> None:
    """PM-1 (and PM-5, PM-6)."""
    users_only = client_as(client, MUAR)
    for permissions in (["MANAGE_CORRECTION_PERMISSIONS"], ["UNDO_RECENT_SCANS"]):
        _refused(
            db_engine,
            partial(
                users_only.post,
                "/api/roles",
                json={"name": f"R {_suffix()}", "permissions": permissions},
            ),
            403,
            _G1,
            [MCP, MUAR],
        )
    _ok(
        users_only.post(
            "/api/roles", json={"name": f"R {_suffix()}", "permissions": ["VIEW_PRODUCTION_DATA"]}
        ),
        201,
    )
    both = client_as(client, MUAR, MCP)
    created = _ok(
        both.post(
            "/api/roles",
            json={
                "name": f"R {_suffix()}",
                "permissions": ["UNDO_RECENT_SCANS", "MANAGE_CORRECTION_PERMISSIONS"],
            },
        ),
        201,
    )
    assert created["permissions"] == ["MANAGE_CORRECTION_PERMISSIONS", "UNDO_RECENT_SCANS"]


def test_editing_a_role_names_the_keys_it_touches(client: TestClient, db_engine: Engine) -> None:
    """PM-2 (and PM-5, PM-6)."""
    users_only = client_as(client, MUAR)
    own_role = _identity(users_only).role_id
    protected = _sql_role(db_engine, UNDO)
    for role_id in (protected, own_role):
        path = f"/api/roles/{role_id}"
        target = (("roles", role_id),)
        _refused(
            db_engine,
            partial(
                users_only.patch,
                path,
                json={"grant_permissions": ["MANAGE_CORRECTION_PERMISSIONS"]},
            ),
            403,
            _G1,
            [MCP, MUAR],
            target,
        )
        _refused(
            db_engine,
            partial(users_only.patch, path, json={"grant_permissions": ["UNDO_RECENT_SCANS"]}),
            403,
            _G1,
            [MCP],
            target,
        )
        _refused(
            db_engine,
            partial(users_only.patch, path, json={"revoke_permissions": ["UNDO_RECENT_SCANS"]}),
            403,
            _G1,
            [MCP],
            target,
        )
        _refused(
            db_engine,
            partial(
                users_only.patch,
                path,
                json={"name": f"R {_suffix()}", "grant_permissions": ["UNDO_RECENT_SCANS"]},
            ),
            403,
            _G1,
            [MCP, MUAR],
            target,
        )
    # A no-op grant of a held protected key is judged on the request: refused.
    _refused(
        db_engine,
        lambda: users_only.patch(
            f"/api/roles/{protected}", json={"grant_permissions": ["UNDO_RECENT_SCANS"]}
        ),
        403,
        _G1,
        [MCP],
        (("roles", protected),),
    )
    renamed = _ok(users_only.patch(f"/api/roles/{protected}", json={"name": f"R {_suffix()}"}))
    assert renamed["permissions"] == ["UNDO_RECENT_SCANS"]
    # PM-5: the same changes succeed with both keys.
    both = client_as(client, MUAR, MCP)
    _ok(both.patch(f"/api/roles/{protected}", json={"revoke_permissions": ["UNDO_RECENT_SCANS"]}))
    _ok(
        both.patch(
            f"/api/roles/{protected}",
            json={"name": f"R {_suffix()}", "grant_permissions": ["UNDO_RECENT_SCANS"]},
        )
    )


def test_a_correction_permission_manager_edits_only_correction_keys(
    client: TestClient, db_engine: Engine
) -> None:
    """PM-3."""
    corrections_only = client_as(client, MCP)
    role_id = _sql_role(db_engine)
    path = f"/api/roles/{role_id}"
    granted = _ok(corrections_only.patch(path, json={"grant_permissions": ["UNDO_RECENT_SCANS"]}))
    assert granted["permissions"] == ["UNDO_RECENT_SCANS"]
    actor = _scalar(
        db_engine,
        "SELECT actor_user_id FROM audit_events WHERE entity_type = 'Role' AND entity_id = :id"
        " ORDER BY id DESC LIMIT 1",
        id=str(role_id),
    )
    assert actor == corrections_only.user_id
    target = (("roles", role_id),)
    _refused(
        db_engine,
        lambda: corrections_only.patch(
            path, json={"grant_permissions": ["MANAGE_CORRECTION_PERMISSIONS"]}
        ),
        403,
        _A2,
        [MCP, MUAR],
        target,
    )
    _refused(
        db_engine,
        lambda: corrections_only.patch(path, json={"name": f"R {_suffix()}"}),
        403,
        _A2,
        [MUAR],
        target,
    )
    _refused(db_engine, lambda: corrections_only.patch(path, json={}), 403, _A2, [MUAR], target)


def test_users_in_protected_roles_need_the_guard_key(client: TestClient, db_engine: Engine) -> None:
    """PM-4 (and PM-5, PM-6)."""
    users_only = client_as(client, MUAR)
    both = client_as(client, MUAR, MCP)
    operator = _seeded_role_id(db_engine, "Operator")
    plain_role = _sql_role(db_engine, Permission.VIEW_PRODUCTION_DATA)

    _refused(
        db_engine,
        lambda: users_only.post("/api/users", json=_new_user(operator)),
        403,
        _G3,
        [MCP, MUAR],
    )
    plain = _ok(users_only.post("/api/users", json=_new_user(plain_role)), 201)
    protected = _ok(both.post("/api/users", json=_new_user(operator)), 201)
    plain_target = (("users", int(plain["id"])),)
    protected_target = (("users", int(protected["id"])),)

    _refused(
        db_engine,
        lambda: users_only.patch(f"/api/users/{plain['id']}", json={"role_id": operator}),
        403,
        _G3,
        [MCP, MUAR],
        plain_target,
    )
    _refused(
        db_engine,
        lambda: users_only.patch(f"/api/users/{protected['id']}", json={"role_id": plain_role}),
        403,
        _G3,
        [MCP, MUAR],
        protected_target,
    )
    _refused(
        db_engine,
        lambda: users_only.patch(f"/api/users/{protected['id']}", json={"is_active": False}),
        403,
        _G2,
        [MCP, MUAR],
        protected_target,
    )
    renamed = _ok(
        users_only.patch(f"/api/users/{protected['id']}", json={"display_name": "Renamed"})
    )
    assert renamed["display_name"] == "Renamed"
    _ok(
        users_only.put(
            f"/api/users/{protected['id']}/avatar",
            content=_PNG,
            headers={"Content-Type": "image/png"},
        )
    )
    _refused(
        db_engine,
        lambda: users_only.patch(f"/api/users/{users_only.user_id}", json={"role_id": operator}),
        403,
        _G3,
        [MCP, MUAR],
        (("users", users_only.user_id),),
    )
    _refused(
        db_engine,
        lambda: users_only.put(
            f"/api/users/{protected['id']}/password", json={"new_password": _NEW_PASSWORD}
        ),
        403,
        _G2,
        [MCP, MUAR],
        protected_target,
    )
    # The reactivation of an inactive protected-role User is guarded too.
    _ok(both.patch(f"/api/users/{protected['id']}", json={"is_active": False}))
    _refused(
        db_engine,
        lambda: users_only.patch(f"/api/users/{protected['id']}", json={"is_active": True}),
        403,
        _G2,
        [MCP, MUAR],
        protected_target,
    )

    # PM-5: everything refused above succeeds with both keys.
    _ok(both.patch(f"/api/users/{protected['id']}", json={"is_active": True}))
    _ok(both.patch(f"/api/users/{plain['id']}", json={"role_id": operator}))
    _ok(both.patch(f"/api/users/{protected['id']}", json={"role_id": plain_role}))
    _ok(both.put(f"/api/users/{plain['id']}/password", json={"new_password": _NEW_PASSWORD}))


# ---------------------------------------------------------------------------
# LH — the last-holder rule
# ---------------------------------------------------------------------------


def test_the_last_user_manager_keeps_the_permission(client: TestClient, db_engine: Engine) -> None:
    """LH-1."""
    _isolate(db_engine)
    sole = client_as(client, MUAR)
    path = f"/api/roles/{_identity(sole).role_id}"
    _refused(
        db_engine,
        lambda: sole.patch(path, json={"revoke_permissions": ["MANAGE_USERS_AND_ROLES"]}),
        409,
        _L1,
        targets=(("roles", _identity(sole).role_id),),
    )
    client_as(client, MUAR)
    revoked = _ok(sole.patch(path, json={"revoke_permissions": ["MANAGE_USERS_AND_ROLES"]}))
    assert revoked["permissions"] == []


def test_the_last_user_manager_may_not_leave(client: TestClient, db_engine: Engine) -> None:
    """LH-2: refusals keep the refusing actor's sessions open."""
    _isolate(db_engine)
    sole = client_as(client, MUAR)
    empty_role = _sql_role(db_engine)
    path = f"/api/users/{sole.user_id}"
    target = (("users", sole.user_id),)
    _refused(
        db_engine, lambda: sole.patch(path, json={"is_active": False}), 409, _L1, targets=target
    )
    assert _open_sessions(db_engine, sole.user_id) == 1
    assert _ok(sole.get("/api/session"))["user"]["id"] == sole.user_id
    _refused(
        db_engine, lambda: sole.patch(path, json={"role_id": empty_role}), 409, _L1, targets=target
    )
    assert _open_sessions(db_engine, sole.user_id) == 1

    client_as(client, MUAR)
    assert _ok(sole.patch(path, json={"is_active": False}))["is_active"] is False
    assert _open_sessions(db_engine, sole.user_id) == 0
    reasons = _scalar(
        db_engine,
        "SELECT array_agg(DISTINCT end_reason) FROM user_sessions WHERE user_id = :id",
        id=sole.user_id,
    )
    assert reasons == ["USER_DEACTIVATED"]


def test_the_last_correction_permission_manager_keeps_the_permission(
    client: TestClient, db_engine: Engine
) -> None:
    """LH-3."""
    _isolate(db_engine)
    manager = client_as(client, MUAR, MCP)
    other = client_as(client, MUAR)
    role_id = _identity(manager).role_id
    _refused(
        db_engine,
        lambda: manager.patch(
            f"/api/roles/{role_id}", json={"revoke_permissions": ["MANAGE_CORRECTION_PERMISSIONS"]}
        ),
        409,
        _L2,
        targets=(("roles", role_id),),
    )
    assert _active(db_engine, other.user_id)
    assert _ok(other.get("/api/session"))["user"]["permissions"] == ["MANAGE_USERS_AND_ROLES"]


def test_only_active_users_with_a_password_hold(client: TestClient, db_engine: Engine) -> None:
    """LH-4."""
    _isolate(db_engine)
    sole = client_as(client, MUAR)
    holder_role = _identity(sole).role_id
    _sql_user(db_engine, holder_role)  # no password
    create_identity(client, MUAR, active=False)  # inactive with a password
    _refused(
        db_engine,
        lambda: sole.patch(f"/api/users/{sole.user_id}", json={"is_active": False}),
        409,
        _L1,
        targets=(("users", sole.user_id),),
    )


def test_concurrent_mutual_deactivation_leaves_a_holder(
    client: TestClient, db_engine: Engine
) -> None:
    """LH-5."""
    _isolate(db_engine)
    first = client_as(client, MUAR, MCP)
    role_id = _identity(first).role_id
    second = _in_role(client, db_engine, role_id)
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def deactivate(name: str, actor: IdentityClient, target: int) -> None:
        barrier.wait()
        results[name] = actor.patch(f"/api/users/{target}", json={"is_active": False})

    threads = [
        threading.Thread(target=deactivate, args=("first", first, second.user_id)),
        threading.Thread(target=deactivate, args=("second", second, first.user_id)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    statuses = sorted(response.status_code for response in results.values())
    assert statuses[0] == 200 and statuses[1] in (401, 409), [r.text for r in results.values()]
    refused = next(response for response in results.values() if response.status_code != 200)
    if refused.status_code == 409:
        assert refused.json()["detail"] == _L1
    else:
        assert refused.json()["detail"] == _A1
    assert _active(db_engine, first.user_id) != _active(db_engine, second.user_id)


def test_concurrent_revocation_and_deactivation_leave_a_holder(
    client: TestClient, db_engine: Engine
) -> None:
    """LH-6."""
    _isolate(db_engine)
    first = client_as(client, MUAR, MCP)
    second = client_as(client, MUAR, MCP)
    barrier = threading.Barrier(2)
    results: dict[str, Any] = {}

    def revoke() -> None:
        barrier.wait()
        results["revoke"] = first.patch(
            f"/api/roles/{_identity(second).role_id}",
            json={"revoke_permissions": ["MANAGE_USERS_AND_ROLES"]},
        )

    def deactivate() -> None:
        barrier.wait()
        results["deactivate"] = second.patch(
            f"/api/users/{first.user_id}", json={"is_active": False}
        )

    threads = [threading.Thread(target=revoke), threading.Thread(target=deactivate)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    statuses = sorted(response.status_code for response in results.values())
    assert statuses[0] == 200 and statuses[1] in (401, 403, 409), statuses
    holders = _scalar(
        db_engine,
        "SELECT count(*) FROM users u JOIN user_credentials c ON c.user_id = u.id"
        " JOIN role_permissions rp ON rp.role_id = u.role_id"
        " AND rp.permission = 'MANAGE_USERS_AND_ROLES' WHERE u.is_active",
    )
    assert holders >= 1


def test_a_no_op_is_never_refused(client: TestClient, db_engine: Engine) -> None:
    """LH-7: a retry of a deactivation after an unknown outcome."""
    _isolate(db_engine)
    sole = client_as(client, MUAR, MCP)
    inactive = _sql_user(db_engine, _sql_role(db_engine), active=False)
    before = _snapshot(db_engine, ("users", inactive))
    response = _ok(sole.patch(f"/api/users/{inactive}", json={"is_active": False}))
    assert response["is_active"] is False
    assert _snapshot(db_engine, ("users", inactive)) == before


def test_the_last_correction_permission_manager_may_not_move_or_leave(
    client: TestClient, db_engine: Engine
) -> None:
    """LH-8: L-2 through a user edit while a user manager remains."""
    _isolate(db_engine)
    manager = client_as(client, MUAR, MCP)
    client_as(client, MUAR)
    users_only_role = _sql_role(db_engine, MUAR)
    path = f"/api/users/{manager.user_id}"
    target = (("users", manager.user_id),)
    _refused(
        db_engine,
        lambda: manager.patch(path, json={"role_id": users_only_role}),
        409,
        _L2,
        targets=target,
    )
    _refused(
        db_engine, lambda: manager.patch(path, json={"is_active": False}), 409, _L2, targets=target
    )


# ---------------------------------------------------------------------------
# LK — serialization
# ---------------------------------------------------------------------------


def test_every_role_and_user_write_waits_on_the_advisory_lock(
    client: TestClient, db_engine: Engine
) -> None:
    """LK-1."""
    actor = client_as(client, *ALL_PERMISSIONS)
    role_id = _sql_role(db_engine)
    target = create_identity(client)
    avatar_target = create_identity(client)
    _ok(
        actor.put(
            f"/api/users/{avatar_target.user_id}/avatar",
            content=_PNG,
            headers={"Content-Type": "image/png"},
        )
    )
    station = _ok(
        actor.post(
            "/api/scan-stations",
            json={
                "station_id": f"ST-{_suffix()}",
                "area_id": _ok(
                    actor.post(
                        "/api/areas",
                        json={
                            "department_id": _ok(
                                actor.post("/api/departments", json={"name": f"D {_suffix()}"}),
                                201,
                            )["id"],
                            "name": f"A {_suffix()}",
                        },
                    ),
                    201,
                )["id"],
            },
        ),
        201,
    )
    requests: list[Callable[[], Any]] = [
        lambda: actor.post("/api/roles", json={"name": f"R {_suffix()}"}),
        lambda: actor.patch(f"/api/roles/{role_id}", json={"name": f"R {_suffix()}"}),
        lambda: actor.post("/api/users", json=_new_user(role_id)),
        lambda: actor.patch(f"/api/users/{target.user_id}", json={"display_name": "Waited"}),
        lambda: actor.put(
            f"/api/users/{target.user_id}/avatar",
            content=_PNG,
            headers={"Content-Type": "image/png"},
        ),
        lambda: actor.delete(f"/api/users/{avatar_target.user_id}/avatar"),
        lambda: actor.put(
            f"/api/users/{target.user_id}/password", json={"new_password": _NEW_PASSWORD}
        ),
    ]
    with db_engine.connect() as holder:
        holder.begin()
        _hold_lock(holder)
        started = [_start(request) for request in requests]
        for thread, _ in started:
            _assert_blocked(thread)
        # Neither a Scan Station write nor a policy write waits on it.
        theme = client.put(
            f"/api/scan-stations/{station['station_id']}/theme-preference",
            json={"theme_preference": "DARK"},
        )
        assert theme.status_code == 200, theme.text
        _ok(actor.put("/api/policies/worker-sessions", json={"worker_session_timeout_minutes": 33}))
        holder.commit()
    for thread, results in started:
        response = _finish(thread, results)
        assert response.status_code in (200, 201), response.text

    # The audit row's FK takes FOR KEY SHARE on the actor's users row: it
    # waits only behind a FOR UPDATE (a login rename) of that row.
    department = _ok(actor.post("/api/departments", json={"name": f"D {_suffix()}"}), 201)
    with db_engine.connect() as holder:
        holder.begin()
        holder.execute(
            sa.text("SELECT 1 FROM users WHERE id = :id FOR UPDATE"), {"id": actor.user_id}
        )
        thread, results = _start(
            lambda: actor.patch(
                f"/api/departments/{department['id']}", json={"name": f"D {_suffix()}"}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    _ok(_finish(thread, results))


def test_the_actor_is_judged_as_committed_when_the_lock_is_granted(
    client: TestClient, db_engine: Engine
) -> None:
    """LK-2."""
    role_id = _sql_role(db_engine)
    path = f"/api/roles/{role_id}"
    body = {"grant_permissions": ["UNDO_RECENT_SCANS"]}

    def blocked_then(actor: IdentityClient, change: str, **params: object) -> Any:
        """The actor's grant waits on the lock while ``change`` commits."""
        audits = _scalar(db_engine, "SELECT count(*) FROM audit_events")
        with db_engine.connect() as holder:
            holder.begin()
            _hold_lock(holder)
            thread, results = _start(lambda: actor.patch(path, json=body))
            _assert_blocked(thread)
            holder.execute(sa.text(change), params)
            holder.commit()
        response = _finish(thread, results)
        assert _scalar(db_engine, "SELECT count(*) FROM audit_events") == audits
        assert (
            _scalar(
                db_engine, "SELECT count(*) FROM role_permissions WHERE role_id = :r", r=role_id
            )
            == 0
        )
        return response

    actor = client_as(client, MUAR, MCP)
    revoked = blocked_then(
        actor,
        "DELETE FROM role_permissions WHERE role_id = :r"
        " AND permission = 'MANAGE_CORRECTION_PERMISSIONS'",
        r=_identity(actor).role_id,
    )
    assert revoked.status_code == 403, revoked.text
    assert revoked.json()["detail"] == _G1
    assert revoked.json()["required_permissions"] == ["MANAGE_CORRECTION_PERMISSIONS"]

    actor = client_as(client, MUAR, MCP)
    deactivated = blocked_then(
        actor, "UPDATE users SET is_active = false WHERE id = :id", id=actor.user_id
    )
    assert deactivated.status_code == 401, deactivated.text
    assert deactivated.json()["authentication_required"] is True

    actor = client_as(client, MUAR, MCP)
    ended = blocked_then(
        actor,
        "UPDATE user_sessions SET ended_at = now(), end_reason = 'SIGNED_OUT' WHERE user_id = :id",
        id=actor.user_id,
    )
    assert ended.status_code == 401, ended.text

    # The same revocation against a password set for a protected-role User.
    actor = client_as(client, MUAR, MCP)
    protected = create_identity(client, UNDO)
    before = _snapshot(db_engine, ("users", protected.user_id))
    with db_engine.connect() as holder:
        holder.begin()
        _hold_lock(holder)
        thread, results = _start(
            lambda: actor.put(
                f"/api/users/{protected.user_id}/password", json={"new_password": _NEW_PASSWORD}
            )
        )
        _assert_blocked(thread)
        holder.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE role_id = :r"
                " AND permission = 'MANAGE_CORRECTION_PERMISSIONS'"
            ),
            {"r": _identity(actor).role_id},
        )
        holder.commit()
    refused = _finish(thread, results)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == _G2
    after = _snapshot(db_engine, ("users", protected.user_id))
    # Only the holder's DELETE changed a table (role_permissions).
    assert after[0] == before[0] and after[2:] == before[2:]


def test_the_guard_reads_the_target_as_committed_when_the_lock_is_granted(
    client: TestClient, db_engine: Engine
) -> None:
    """LK-3."""
    users_only = client_as(client, MUAR)
    target = create_identity(client)
    with db_engine.connect() as holder:
        holder.begin()
        _hold_lock(holder)
        thread, results = _start(
            lambda: users_only.patch(f"/api/users/{target.user_id}", json={"is_active": False})
        )
        _assert_blocked(thread)
        holder.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission)"
                " VALUES (:r, 'UNDO_RECENT_SCANS')"
            ),
            {"r": target.role_id},
        )
        holder.commit()
    before = _snapshot(db_engine, ("users", target.user_id))
    refused = _finish(thread, results)
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == _G2
    assert _snapshot(db_engine, ("users", target.user_id)) == before
    assert _active(db_engine, target.user_id)
    assert _open_sessions(db_engine, target.user_id) == 1


# ---------------------------------------------------------------------------
# RC — rollout recovery
# ---------------------------------------------------------------------------


def _lose_correction_management(client: TestClient, engine: Engine) -> TestIdentity:
    """Users may be managed, correction permissions may not: only pre-slice-2
    data can be in this state (set by SQL)."""
    _isolate(engine)
    _execute(
        engine, "DELETE FROM role_permissions WHERE permission = 'MANAGE_CORRECTION_PERMISSIONS'"
    )
    return create_identity(client, MUAR)


def test_startup_warns_while_no_one_may_manage_correction_permissions(
    client: TestClient, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """RC-1."""
    _lose_correction_management(client, db_engine)
    caplog.set_level(logging.WARNING, logger="app.user_access")
    with TestClient(create_app()):
        pass
    warnings = [record for record in caplog.records if record.name == "app.user_access"]
    assert [record.getMessage() for record in warnings] == [_WARNING]
    assert warnings[0].levelno == logging.WARNING

    caplog.clear()
    create_identity(client, MCP)
    with TestClient(create_app()):
        pass
    assert not [record for record in caplog.records if record.name == "app.user_access"]


def test_the_recovery_command_restores_correction_permission_management(
    client: TestClient, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """RC-2."""
    member = _lose_correction_management(client, db_engine)
    role_name = str(_scalar(db_engine, "SELECT name FROM roles WHERE id = :id", id=member.role_id))
    grants = _scalar(db_engine, "SELECT count(*) FROM role_permissions")
    capsys.readouterr()
    assert cli.main(["restore-correction-permission-management", "--role-name", role_name]) == 0
    out = capsys.readouterr().out
    assert out.strip() == (
        f"Role {role_name} may now manage correction permissions (1 active users with a"
        " password hold it)."
    )
    assert _scalar(db_engine, "SELECT count(*) FROM role_permissions") == grants + 1
    with db_engine.connect() as connection:
        audit = connection.execute(
            sa.text(
                "SELECT actor_user_id, metadata, before_data, after_data FROM audit_events"
                " WHERE entity_type = 'Role' AND entity_id = :id ORDER BY id DESC LIMIT 1"
            ),
            {"id": str(member.role_id)},
        ).one()
    assert audit.actor_user_id is None
    assert audit.metadata == {"source": "cli"}
    assert audit.before_data["permissions"] == ["MANAGE_USERS_AND_ROLES"]
    assert audit.after_data["permissions"] == [
        "MANAGE_CORRECTION_PERMISSIONS",
        "MANAGE_USERS_AND_ROLES",
    ]
    restored = IdentityClient(client, member)
    role_id = _sql_role(db_engine)
    _ok(restored.patch(f"/api/roles/{role_id}", json={"grant_permissions": ["UNDO_RECENT_SCANS"]}))


def test_the_recovery_command_refusals_write_nothing(
    client: TestClient, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """RC-3."""
    member = _lose_correction_management(client, db_engine)
    role_name = str(_scalar(db_engine, "SELECT name FROM roles WHERE id = :id", id=member.role_id))
    memberless = f"Memberless {_suffix()}"
    _execute(db_engine, "INSERT INTO roles (name) VALUES (:name)", name=memberless)
    unknown = f"Nobody {_suffix()}"
    before = _snapshot(db_engine)
    capsys.readouterr()
    assert cli.main(["restore-correction-permission-management", "--role-name", unknown]) == 1
    assert capsys.readouterr().err.strip() == f"No role is named {unknown}."
    assert cli.main(["restore-correction-permission-management", "--role-name", memberless]) == 1
    assert capsys.readouterr().err.strip() == (
        f"No active user with a password holds the role {memberless}. Name a role that one holds."
    )
    assert _snapshot(db_engine) == before

    create_identity(client, MCP)
    before = _snapshot(db_engine)
    assert cli.main(["restore-correction-permission-management", "--role-name", role_name]) == 1
    assert capsys.readouterr().err.strip() == (
        "A user who may manage correction permissions already exists. Grant it in"
        " Administration instead."
    )
    assert _snapshot(db_engine) == before


def test_the_recovery_command_waits_on_the_advisory_lock(
    client: TestClient, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """RC-4."""
    member = _lose_correction_management(client, db_engine)
    role_name = str(_scalar(db_engine, "SELECT name FROM roles WHERE id = :id", id=member.role_id))
    with db_engine.connect() as holder:
        holder.begin()
        _hold_lock(holder)
        thread, results = _start(
            lambda: cli.main(["restore-correction-permission-management", "--role-name", role_name])
        )
        _assert_blocked(thread)
        holder.commit()
    assert _finish(thread, results) == 0
    capsys.readouterr()
