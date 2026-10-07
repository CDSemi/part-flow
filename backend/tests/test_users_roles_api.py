"""Integration tests for Phase 13 slice 12 — users, roles and permissions.

Exercises the full request path — FastAPI routes, the roles and users
services and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain (IMPLEMENTATION_ROADMAP
Phase 13 "Users and roles / authorization management — configuration is
created here; enforcement is Phase 14"; PROJECT_PROFILE §7 User, §20;
owner decisions OD-8, OD-19):

- roles: the read model equals the stored grants and user counts;
  create (trimmed name, duplicate grants collapse), rename and
  grant/revoke deltas, every refusal with nothing written, the no-op
  (no audit row, no UPDATE), the role lock (a concurrent edit audits its
  committed predecessor) and the lost races of a create and a rename
  (409, never 500, nothing written);
- users: create (canonical login name, one role), the login rule, the
  duplicate refusals naming the holder, the lost races of a create and a
  login change, the explicit-null refusals, partial edits, the User lock
  and the avatar (PUT/GET/DELETE, ETag/304, no-op, refusals);
- no theme surface: the User preference is stored only (S12-OD8);
- Users are never Workers: no foreign key either way, no badge column,
  and a login never resolves as a badge;
- audit coverage: one row per effective write, none for a no-op or a
  refusal; and a static proof that only the configuration, sign-in and
  first-run modules and the checked routes read users, roles,
  credentials or permissions (Phase 14 slice 1 widened it).

The module database is shared and append-only for audit rows, so every
case creates its own uniquely named roles and users, asserts audit rows
by its own ``entity_id`` and counts as deltas; exact seed state is
asserted by the schema test on a freshly migrated database. No case
edits a seeded role. Helpers are copied locally (each module owns its
own); the module database is dropped afterwards.
"""

import ast
import hashlib
import os
import threading
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

from alembic import command
from app.application import roles as roles_service
from app.application import users as users_service
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APP_DIR = _BACKEND_DIR / "app"
_TEST_DATABASE = "partflow_test_users_roles_api"

_ROLE_KEYS = {
    "id",
    "name",
    "permissions",
    "user_count",
    "created_at",
    "updated_at",
    # Phase 14 slice 4.
    "applies_at_scan_stations",
}
_USER_KEYS = {
    "id",
    "login_name",
    "display_name",
    "role_id",
    "role_name",
    "is_active",
    "avatar_updated_at",
    "created_at",
    "updated_at",
}
# What a caller who may manage users and roles (the harness administrator)
# sees: slice 12's shape plus the slice 1 sign-in state.
_ADMIN_USER_KEYS = _USER_KEYS | {"sign_in_state"}

_E_R1 = "Role name must not be empty."
_E_R2 = "A role with this name already exists."
_E_R4 = "A permission cannot be granted and revoked in the same change."
_E_U1 = "Name must not be empty."
_E_U2A = "Login name must not be empty."
_E_U2B = (
    "A login name may contain only letters (a–z), digits and . _ @ + -, with no spaces,"
    " and at most 128 characters."
)
_E_U4 = "This login name is already used by another user."
_E_U8 = "Choose a role for this user."
_E_U9 = "User active status must be true or false."
_E_R1_NUL = "Role name must not contain a NUL character."
_E_U1_NUL = "Name must not contain a NUL character."
# One past the largest id PostgreSQL can bind to an ``integer`` key: it
# names no row and must be answered as missing, never fail in the driver.
_UNBINDABLE_ID = 2**31
_UNSUPPORTED = "The file is not a PNG, JPEG or WebP image."
_TOO_LARGE = "The image is larger than 2 MB. Choose a smaller image."

# Minimal byte fixtures carrying the real magic bytes of each type.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x02" * 16


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
    """Application client wired to the temporary database through the
    real startup path (DATABASE_URL pointed at it, settings re-read)."""
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
    """Direct database access for assertions and concurrent holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _refused(response: Any, status: int, detail: str | None = None) -> None:
    assert response.status_code == status, response.text
    if detail is not None:
        assert response.json()["detail"] == detail


def _roles(client: TestClient) -> dict[str, dict[str, Any]]:
    response = admin_of(client).get("/api/roles")
    assert response.status_code == 200, response.text
    return {role["name"]: role for role in response.json()}


def _users(client: TestClient) -> list[dict[str, Any]]:
    response = admin_of(client).get("/api/users")
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


def _seeded_role_id(client: TestClient, name: str) -> int:
    return int(_roles(client)[name]["id"])


def _create_role(
    client: TestClient, name: str | None = None, permissions: list[str] | None = None
) -> dict[str, Any]:
    payload = {"name": name or f"Role {_suffix()}", "permissions": permissions or []}
    return _ok(admin_of(client).post("/api/roles", json=payload), 201)


def _create_user(
    client: TestClient,
    role_id: int,
    login_name: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    payload = {
        "login_name": login_name or f"user-{_suffix()}",
        "display_name": display_name or f"User {_suffix()}",
        "role_id": role_id,
    }
    return _ok(admin_of(client).post("/api/users", json=payload), 201)


def _audit_rows(engine: Engine, entity_type: str, entity_id: int) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, before_data, after_data, actor_reference"
                    " FROM audit_events WHERE entity_type = :entity_type"
                    " AND entity_id = :entity_id ORDER BY id"
                ),
                {"entity_type": entity_type, "entity_id": str(entity_id)},
            )
        )


def _count(engine: Engine, sql: str, **params: object) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text(sql), params).scalar_one())


def _write_counts(engine: Engine) -> tuple[int, int, int, int]:
    """Roles, grants, users and audit rows — a refusal changes none of them."""
    return (
        _count(engine, "SELECT count(*) FROM roles"),
        _count(engine, "SELECT count(*) FROM role_permissions"),
        _count(engine, "SELECT count(*) FROM users"),
        _count(engine, "SELECT count(*) FROM audit_events"),
    )


def _xmin(engine: Engine, table: str, row_id: int) -> str:
    """A changed xmin means an UPDATE ran; a row lock leaves it alone."""
    with engine.connect() as connection:
        return str(
            connection.execute(
                sa.text(f"SELECT xmin::text FROM {table} WHERE id = :id"), {"id": row_id}
            ).scalar_one()
        )


def _stored_grants(engine: Engine, role_id: int) -> list[str]:
    with engine.connect() as connection:
        return [
            str(permission)
            for permission in connection.execute(
                sa.text(
                    "SELECT permission FROM role_permissions WHERE role_id = :id"
                    " ORDER BY permission"
                ),
                {"id": role_id},
            ).scalars()
        ]


def _stored_user(engine: Engine, user_id: int) -> dict[str, Any]:
    """The whole stored row, avatar bytes and xmin included."""
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT xmin::text AS row_xmin, * FROM users WHERE id = :id"), {"id": user_id}
        ).one()
    return dict(row._mapping)


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


def _put_avatar(client: TestClient, user_id: int, data: bytes, content_type: str) -> Any:
    return admin_of(client).put(
        f"/api/users/{user_id}/avatar", content=data, headers={"Content-Type": content_type}
    )


def _digest(data: bytes, content_type: str) -> dict[str, Any]:
    return {
        "content_type": content_type,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def test_role_list_reflects_the_stored_grants_and_user_counts(
    client: TestClient, db_engine: Engine
) -> None:
    """R-1: read-model fidelity."""
    _create_user(client, _seeded_role_id(client, "Operator"))
    roles = _roles(client)
    assert {"Administrator", "Manager", "Operator"} <= roles.keys()
    for role in roles.values():
        assert set(role) == _ROLE_KEYS
        assert role["permissions"] == _stored_grants(db_engine, role["id"])
        assert role["user_count"] == _count(
            db_engine, "SELECT count(*) FROM users WHERE role_id = :id", id=role["id"]
        )
    assert roles["Operator"]["user_count"] >= 1


def test_create_role_trims_the_name_and_collapses_duplicate_grants(
    client: TestClient, db_engine: Engine
) -> None:
    """R-2."""
    name = f"Process Engineer {_suffix()}"
    created = _ok(
        admin_of(client).post(
            "/api/roles",
            json={
                "name": f"  {name} ",
                "permissions": ["MANAGE_MACHINES", "MANAGE_MACHINES", "MANAGE_ROUTE_TEMPLATES"],
            },
        ),
        201,
    )
    assert set(created) == _ROLE_KEYS
    assert created["name"] == name
    assert created["permissions"] == ["MANAGE_MACHINES", "MANAGE_ROUTE_TEMPLATES"]
    assert created["user_count"] == 0
    assert _stored_grants(db_engine, created["id"]) == created["permissions"]
    rows = _audit_rows(db_engine, "Role", created["id"])
    assert len(rows) == 1
    assert rows[0].event_type == "CREATED"
    assert rows[0].before_data is None
    assert rows[0].after_data == {
        "name": name,
        "permissions": ["MANAGE_MACHINES", "MANAGE_ROUTE_TEMPLATES"],
    }
    assert rows[0].actor_reference is None


@pytest.mark.parametrize(
    ("payload", "status", "detail"),
    [
        pytest.param({"name": ""}, 422, _E_R1, id="empty"),
        pytest.param({"name": "   "}, 422, _E_R1, id="blank"),
        pytest.param({"name": "Manager"}, 409, _E_R2, id="duplicate"),
        pytest.param({"name": "X", "permissions": ["NOPE"]}, 422, None, id="unknown-permission"),
        pytest.param({"name": "X", "id": 1}, 422, None, id="extra-field"),
        pytest.param({"name": 5}, 422, None, id="non-string-name"),
        pytest.param({"name": "A\x00B"}, 422, _E_R1_NUL, id="nul-name"),
    ],
)
def test_create_role_refusals_write_nothing(
    client: TestClient, db_engine: Engine, payload: dict[str, Any], status: int, detail: str | None
) -> None:
    """R-3."""
    counts = _write_counts(db_engine)
    _refused(admin_of(client).post("/api/roles", json=payload), status, detail)
    assert _write_counts(db_engine) == counts


def test_role_names_are_unique_case_sensitively(client: TestClient) -> None:
    """R-3 / S12-OD10: a case variant of a role name is another name."""
    name = f"Case Role {_suffix()}"
    _create_role(client, name)
    _refused(admin_of(client).post("/api/roles", json={"name": name}), 409, _E_R2)
    assert _create_role(client, name.lower())["name"] == name.lower()


def test_grant_and_revoke_are_audited_deltas(client: TestClient, db_engine: Engine) -> None:
    """R-4."""
    role = _create_role(client, permissions=["MANAGE_MACHINES"])
    path = f"/api/roles/{role['id']}"
    changed = _ok(
        admin_of(client).patch(
            path,
            json={
                "grant_permissions": ["VIEW_PRODUCTION_DATA"],
                "revoke_permissions": ["MANAGE_MACHINES"],
            },
        )
    )
    assert changed["permissions"] == ["VIEW_PRODUCTION_DATA"]
    assert _stored_grants(db_engine, role["id"]) == ["VIEW_PRODUCTION_DATA"]
    rows = _audit_rows(db_engine, "Role", role["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert rows[1].before_data == {"name": role["name"], "permissions": ["MANAGE_MACHINES"]}
    assert rows[1].after_data == {"name": role["name"], "permissions": ["VIEW_PRODUCTION_DATA"]}

    xmin = _xmin(db_engine, "roles", role["id"])
    for no_op in (
        {"grant_permissions": ["VIEW_PRODUCTION_DATA"], "revoke_permissions": ["MANAGE_MACHINES"]},
        {"grant_permissions": ["VIEW_PRODUCTION_DATA"]},
        {"revoke_permissions": ["MANAGE_AREAS"]},
        {"name": role["name"]},
        {},
    ):
        assert _ok(admin_of(client).patch(path, json=no_op))["permissions"] == [
            "VIEW_PRODUCTION_DATA"
        ]
    assert len(_audit_rows(db_engine, "Role", role["id"])) == 2
    assert _xmin(db_engine, "roles", role["id"]) == xmin

    counts = _write_counts(db_engine)
    _refused(
        admin_of(client).patch(
            path,
            json={
                "grant_permissions": ["EXPORT_REPORTS"],
                "revoke_permissions": ["EXPORT_REPORTS"],
            },
        ),
        422,
        _E_R4,
    )
    _refused(admin_of(client).patch(path, json={"grant_permissions": ["NOPE"]}), 422)
    _refused(admin_of(client).patch(path, json={"permissions": []}), 422)
    assert _write_counts(db_engine) == counts


def test_role_rename(client: TestClient, db_engine: Engine) -> None:
    """R-5."""
    role = _create_role(client, permissions=["EXPORT_REPORTS"])
    path = f"/api/roles/{role['id']}"
    counts = _write_counts(db_engine)
    _refused(admin_of(client).patch(path, json={"name": "Administrator"}), 409, _E_R2)
    _refused(admin_of(client).patch(path, json={"name": ""}), 422, _E_R1)
    _refused(admin_of(client).patch(path, json={"name": None}), 422, _E_R1)
    _refused(admin_of(client).patch(path, json={"name": "A\x00B"}), 422, _E_R1_NUL)
    _refused(
        admin_of(client).patch("/api/roles/999999", json={"name": "X"}),
        404,
        "Role 999999 does not exist.",
    )
    _refused(
        admin_of(client).patch(f"/api/roles/{_UNBINDABLE_ID}", json={"name": "X"}),
        404,
        f"Role {_UNBINDABLE_ID} does not exist.",
    )
    assert _write_counts(db_engine) == counts

    new_name = f"Renamed {_suffix()}"
    renamed = _ok(admin_of(client).patch(path, json={"name": f" {new_name} "}))
    assert renamed["name"] == new_name
    assert renamed["permissions"] == ["EXPORT_REPORTS"]
    rows = _audit_rows(db_engine, "Role", role["id"])
    assert len(rows) == 2
    assert rows[1].before_data == {"name": role["name"], "permissions": ["EXPORT_REPORTS"]}
    assert rows[1].after_data == {"name": new_name, "permissions": ["EXPORT_REPORTS"]}


def test_concurrent_role_edit_waits_and_audits_the_committed_predecessor(
    client: TestClient, db_engine: Engine
) -> None:
    """R-6: lock-first."""
    role = _create_role(client, permissions=["MANAGE_MACHINES"])
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT id FROM roles WHERE id = :id FOR NO KEY UPDATE"), {"id": role["id"]}
        )
        holder.execute(
            sa.text("INSERT INTO role_permissions VALUES (:id, 'EXPORT_REPORTS')"),
            {"id": role["id"]},
        )
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/roles/{role['id']}", json={"grant_permissions": ["MANAGE_AREAS"]}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)
    body = _ok(response)
    assert body["permissions"] == ["EXPORT_REPORTS", "MANAGE_AREAS", "MANAGE_MACHINES"]
    update = _audit_rows(db_engine, "Role", role["id"])[-1]
    assert update.before_data["permissions"] == ["EXPORT_REPORTS", "MANAGE_MACHINES"]
    assert update.after_data["permissions"] == body["permissions"]


def test_create_role_race_lost_at_flush_is_a_conflict(
    client: TestClient, db_engine: Engine
) -> None:
    """R-7: the UNIQUE is the race authority."""
    name = f"Race {_suffix()}"
    with db_engine.connect() as holder:
        holder.execute(sa.text("INSERT INTO roles (name) VALUES (:name)"), {"name": name})
        thread, results = _start(lambda: admin_of(client).post("/api/roles", json={"name": name}))
        _assert_blocked(thread)
        holder.commit()
    _refused(_finish(thread, results), 409, _E_R2)
    assert _count(db_engine, "SELECT count(*) FROM roles WHERE name = :n", n=name) == 1


def test_rename_race_lost_at_flush_is_a_conflict_never_500(
    client: TestClient, db_engine: Engine
) -> None:
    """R-7b: the revoke DELETE runs before the rename is staged, so the
    rename loses only inside the conflict-translating flush."""
    role = _create_role(client, permissions=["MANAGE_AREAS"])
    xmin = _xmin(db_engine, "roles", role["id"])
    race_name = f"Race2 {_suffix()}"
    with db_engine.connect() as holder:
        holder.execute(sa.text("INSERT INTO roles (name) VALUES (:name)"), {"name": race_name})
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/roles/{role['id']}",
                json={
                    "name": race_name,
                    "grant_permissions": ["MANAGE_OPERATIONS"],
                    "revoke_permissions": ["MANAGE_AREAS"],
                },
            )
        )
        _assert_blocked(thread)
        holder.commit()
    _refused(_finish(thread, results), 409, _E_R2)
    assert _roles(client)[role["name"]]["id"] == role["id"]
    assert _stored_grants(db_engine, role["id"]) == ["MANAGE_AREAS"]
    assert len(_audit_rows(db_engine, "Role", role["id"])) == 1
    assert _xmin(db_engine, "roles", role["id"]) == xmin


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def test_create_user_canonicalizes_and_audits(client: TestClient, db_engine: Engine) -> None:
    """U-1."""
    manager = _roles(client)["Manager"]
    suffix = _suffix()
    created = _ok(
        admin_of(client).post(
            "/api/users",
            json={
                "login_name": f"  JDoe-{suffix} ",
                "display_name": f" Jane Doe {suffix} ",
                "role_id": manager["id"],
            },
        ),
        201,
    )
    assert set(created) == _ADMIN_USER_KEYS
    assert created["login_name"] == f"jdoe-{suffix}"
    assert created["display_name"] == f"Jane Doe {suffix}"
    assert created["role_id"] == manager["id"]
    assert created["role_name"] == "Manager"
    assert created["is_active"] is True
    assert created["avatar_updated_at"] is None
    assert _stored_user(db_engine, created["id"])["theme_preference"] is None
    rows = _audit_rows(db_engine, "User", created["id"])
    assert len(rows) == 1
    assert rows[0].event_type == "CREATED"
    assert rows[0].before_data is None
    assert rows[0].after_data == {
        "login_name": f"jdoe-{suffix}",
        "display_name": f"Jane Doe {suffix}",
        "role_id": manager["id"],
        "is_active": True,
    }
    assert rows[0].actor_reference is None
    assert _roles(client)["Manager"]["user_count"] == manager["user_count"] + 1
    listed = {user["id"]: user for user in _users(client)}
    assert listed[created["id"]] == created


def test_users_are_listed_by_display_name_then_id(client: TestClient) -> None:
    role_id = _seeded_role_id(client, "Operator")
    name = f"Same Name {_suffix()}"
    first = _create_user(client, role_id, display_name=name)
    second = _create_user(client, role_id, display_name=name)
    listed = _users(client)
    keys = [(user["display_name"], user["id"]) for user in listed]
    assert keys == sorted(keys)
    ids = [user["id"] for user in listed]
    assert ids.index(first["id"]) < ids.index(second["id"])


@pytest.mark.parametrize(
    ("login_name", "detail"),
    [
        pytest.param("", _E_U2A, id="empty"),
        pytest.param("   ", _E_U2A, id="blank"),
        pytest.param("j doe", _E_U2B, id="space"),
        pytest.param("jdoé", _E_U2B, id="non-ascii"),
        pytest.param("Kelvin", _E_U2B, id="kelvin-sign"),
        pytest.param("a/b", _E_U2B, id="slash"),
        pytest.param("a" * 129, _E_U2B, id="129-characters"),
    ],
)
def test_login_name_rule_refusals_write_nothing(
    client: TestClient, db_engine: Engine, login_name: str, detail: str
) -> None:
    """U-2: refusals."""
    role_id = _seeded_role_id(client, "Operator")
    counts = _write_counts(db_engine)
    _refused(
        admin_of(client).post(
            "/api/users",
            json={"login_name": login_name, "display_name": "Jane", "role_id": role_id},
        ),
        422,
        detail,
    )
    assert _write_counts(db_engine) == counts


def test_login_name_rule_admissions(client: TestClient) -> None:
    """U-2: admissions — 128 characters, every punctuation mark, any case."""
    role_id = _seeded_role_id(client, "Operator")
    suffix = _suffix()
    longest = "a" * (128 - len(suffix)) + suffix
    assert _create_user(client, role_id, login_name=longest)["login_name"] == longest
    punctuated = f"a.b_c@d+e-f{suffix}"
    assert _create_user(client, role_id, login_name=punctuated)["login_name"] == punctuated
    upper = f"ABC{suffix.upper()}"
    assert _create_user(client, role_id, login_name=upper)["login_name"] == upper.lower()


def test_duplicate_login_names_name_the_holder(client: TestClient, db_engine: Engine) -> None:
    """U-3: case-insensitive duplicates, active and inactive holders, retries."""
    role_id = _seeded_role_id(client, "Operator")
    suffix = _suffix()
    holder = _create_user(
        client, role_id, login_name=f"jdoe-{suffix}", display_name=f"Jane Doe {suffix}"
    )
    payload = {"login_name": f"JDOE-{suffix}", "display_name": "Other", "role_id": role_id}
    counts = _write_counts(db_engine)
    _refused(
        admin_of(client).post("/api/users", json=payload),
        409,
        f"This login name is already used by Jane Doe {suffix}.",
    )
    assert _write_counts(db_engine) == counts
    _ok(admin_of(client).patch(f"/api/users/{holder['id']}", json={"is_active": False}))
    _refused(
        admin_of(client).post("/api/users", json=payload),
        409,
        f"This login name is already used by Jane Doe {suffix} (inactive).",
    )
    other = _create_user(client, role_id)
    _refused(
        admin_of(client).patch(f"/api/users/{other['id']}", json={"login_name": f"jdoe-{suffix}"}),
        409,
        f"This login name is already used by Jane Doe {suffix} (inactive).",
    )
    # A retried identical POST after an unknown outcome never duplicates.
    retried = {"login_name": f"retry-{suffix}", "display_name": "Retry", "role_id": role_id}
    _ok(admin_of(client).post("/api/users", json=retried), 201)
    _refused(admin_of(client).post("/api/users", json=retried), 409)
    assert (
        _count(db_engine, "SELECT count(*) FROM users WHERE login_name = :l", l=f"retry-{suffix}")
        == 1
    )


def test_create_user_race_lost_at_flush_is_a_conflict(
    client: TestClient, db_engine: Engine
) -> None:
    """U-3: the UNIQUE is the race authority."""
    role_id = _seeded_role_id(client, "Operator")
    login = f"race-{_suffix()}"
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(
                "INSERT INTO users (login_name, display_name, role_id) VALUES (:l, 'Holder', :r)"
            ),
            {"l": login, "r": role_id},
        )
        thread, results = _start(
            lambda: admin_of(client).post(
                "/api/users", json={"login_name": login, "display_name": "Late", "role_id": role_id}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    _refused(_finish(thread, results), 409, _E_U4)
    assert _count(db_engine, "SELECT count(*) FROM users WHERE login_name = :l", l=login) == 1


def test_login_change_race_lost_at_flush_is_a_conflict_never_500(
    client: TestClient, db_engine: Engine
) -> None:
    """U-3b."""
    role_id = _seeded_role_id(client, "Operator")
    user = _create_user(client, role_id)
    stored = _stored_user(db_engine, user["id"])
    login = f"race-login-{_suffix()}"
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(
                "INSERT INTO users (login_name, display_name, role_id) VALUES (:l, 'Holder', :r)"
            ),
            {"l": login, "r": role_id},
        )
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/users/{user['id']}", json={"login_name": login.upper()}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    _refused(_finish(thread, results), 409, _E_U4)
    assert _stored_user(db_engine, user["id"]) == stored
    assert len(_audit_rows(db_engine, "User", user["id"])) == 1


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        pytest.param({"role_id": 999999}, "Role 999999 does not exist.", id="unknown-role"),
        pytest.param({"role_id": "1"}, None, id="string-role"),
        pytest.param({"role_id": True}, None, id="bool-role"),
        pytest.param({"role_id": 1.5}, None, id="float-role"),
        pytest.param({"display_name": "  "}, _E_U1, id="blank-name"),
        pytest.param({"display_name": "A\x00B"}, _E_U1_NUL, id="nul-name"),
        pytest.param(
            {"role_id": _UNBINDABLE_ID},
            f"Role {_UNBINDABLE_ID} does not exist.",
            id="unbindable-role",
        ),
        pytest.param({"password": "secret"}, None, id="password-field"),
        pytest.param({"theme_preference": "DARK"}, None, id="theme-field"),
    ],
)
def test_create_user_refusals_write_nothing(
    client: TestClient, db_engine: Engine, payload: dict[str, Any], detail: str | None
) -> None:
    """U-4."""
    body = {
        "login_name": f"refused-{_suffix()}",
        "display_name": "Refused",
        "role_id": _seeded_role_id(client, "Operator"),
        **payload,
    }
    counts = _write_counts(db_engine)
    _refused(admin_of(client).post("/api/users", json=body), 422, detail)
    assert _write_counts(db_engine) == counts


def test_create_user_requires_every_field(client: TestClient, db_engine: Engine) -> None:
    role_id = _seeded_role_id(client, "Operator")
    counts = _write_counts(db_engine)
    for missing in ("login_name", "display_name", "role_id"):
        body = {"login_name": f"m-{_suffix()}", "display_name": "M", "role_id": role_id}
        del body[missing]
        _refused(admin_of(client).post("/api/users", json=body), 422)
    assert _write_counts(db_engine) == counts


def test_user_edits_are_audited_with_exact_snapshots(client: TestClient, db_engine: Engine) -> None:
    """U-5: one row per effective edit; partial bodies change only their field."""
    roles = _roles(client)
    manager, operator = roles["Manager"]["id"], roles["Operator"]["id"]
    suffix = _suffix()
    user = _create_user(client, manager, login_name=f"jdoe-{suffix}", display_name=f"Jane {suffix}")
    path = f"/api/users/{user['id']}"
    profile = {
        "login_name": f"jdoe-{suffix}",
        "display_name": f"Jane {suffix}",
        "role_id": manager,
        "is_active": True,
    }
    steps: list[tuple[dict[str, Any], dict[str, Any]]] = [
        ({"display_name": f" Janet {suffix} "}, {"display_name": f"Janet {suffix}"}),
        ({"login_name": f"JSMITH-{suffix}"}, {"login_name": f"jsmith-{suffix}"}),
        ({"role_id": operator}, {"role_id": operator}),
        ({"is_active": False}, {"is_active": False}),
        ({"is_active": True}, {"is_active": True}),
    ]
    for body, change in steps:
        before = dict(profile)
        profile.update(change)
        answered = _ok(admin_of(client).patch(path, json=body))
        assert {key: answered[key] for key in profile} == profile
        assert answered["role_name"] == (
            "Operator" if profile["role_id"] == operator else "Manager"
        )
        row = _audit_rows(db_engine, "User", user["id"])[-1]
        assert row.event_type == "UPDATED"
        assert row.before_data == before
        assert row.after_data == profile
    assert len(_audit_rows(db_engine, "User", user["id"])) == 1 + len(steps)


def test_user_no_ops_and_explicit_nulls_write_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    """U-5: a case/whitespace login variant is no change; nulls are refused."""
    role_id = _seeded_role_id(client, "Operator")
    suffix = _suffix()
    user = _create_user(client, role_id, login_name=f"jdoe-{suffix}")
    path = f"/api/users/{user['id']}"
    stored = _stored_user(db_engine, user["id"])
    counts = _write_counts(db_engine)
    for no_op in (
        {"login_name": f" JDOE-{suffix} "},
        {"display_name": f" {user['display_name']} "},
        {"role_id": role_id, "is_active": True},
        {},
    ):
        assert _ok(admin_of(client).patch(path, json=no_op))["login_name"] == f"jdoe-{suffix}"
    for body, detail in (
        ({"is_active": None}, _E_U9),
        ({"role_id": None}, _E_U8),
        ({"login_name": None}, _E_U2A),
        ({"display_name": None}, _E_U1),
        ({"login_name": "j doe"}, _E_U2B),
        ({"role_id": 999999}, "Role 999999 does not exist."),
        ({"display_name": "A\x00B"}, _E_U1_NUL),
        ({"role_id": _UNBINDABLE_ID}, f"Role {_UNBINDABLE_ID} does not exist."),
    ):
        _refused(admin_of(client).patch(path, json=body), 422, detail)
    _refused(admin_of(client).patch(path, json={"role_id": "1"}), 422)
    _refused(admin_of(client).patch(path, json={"id": 5}), 422)
    for missing_id in (999999, _UNBINDABLE_ID):
        _refused(
            admin_of(client).patch(f"/api/users/{missing_id}", json={"display_name": "X"}),
            404,
            f"User {missing_id} does not exist.",
        )
    assert _stored_user(db_engine, user["id"]) == stored
    assert _write_counts(db_engine) == counts


def test_concurrent_user_edit_waits_and_audits_the_committed_predecessor(
    client: TestClient, db_engine: Engine
) -> None:
    """U-5b: lock-first for Users."""
    roles = _roles(client)
    user = _create_user(client, roles["Manager"]["id"])
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT id FROM users WHERE id = :id FOR NO KEY UPDATE"), {"id": user["id"]}
        )
        holder.execute(
            sa.text("UPDATE users SET display_name = 'Committed Name' WHERE id = :id"),
            {"id": user["id"]},
        )
        thread, results = _start(
            lambda: admin_of(client).patch(
                f"/api/users/{user['id']}", json={"role_id": roles["Operator"]["id"]}
            )
        )
        _assert_blocked(thread)
        holder.commit()
    body = _ok(_finish(thread, results))
    assert body["display_name"] == "Committed Name"
    assert body["role_id"] == roles["Operator"]["id"]
    update = _audit_rows(db_engine, "User", user["id"])[-1]
    assert update.before_data["display_name"] == "Committed Name"
    assert update.before_data["role_id"] == roles["Manager"]["id"]
    assert update.after_data["role_id"] == roles["Operator"]["id"]


def test_user_avatar_round_trip(client: TestClient, db_engine: Engine) -> None:
    """U-6."""
    user = _create_user(client, _seeded_role_id(client, "Operator"))
    path = f"/api/users/{user['id']}/avatar"
    uploaded = _ok(_put_avatar(client, user["id"], _PNG, "image/png"))
    assert uploaded["avatar_updated_at"] is not None
    rows = _audit_rows(db_engine, "User", user["id"])
    assert len(rows) == 2
    assert rows[1].before_data == {"avatar": None}
    assert rows[1].after_data == {"avatar": _digest(_PNG, "image/png")}

    # Identical bytes: no write, same version.
    again = _ok(_put_avatar(client, user["id"], _PNG, "image/png"))
    assert again["avatar_updated_at"] == uploaded["avatar_updated_at"]
    assert len(_audit_rows(db_engine, "User", user["id"])) == 2

    served = client.get(path)
    assert served.status_code == 200
    assert served.content == _PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "private, no-cache"
    etag = served.headers["etag"]
    cached = client.get(path, headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.headers["etag"] == etag

    # The list carries no bytes: only the cache version.
    listed = {item["id"]: item for item in _users(client)}
    assert set(listed[user["id"]]) == _ADMIN_USER_KEYS
    assert listed[user["id"]]["avatar_updated_at"] == uploaded["avatar_updated_at"]

    removed = _ok(admin_of(client).delete(path))
    assert removed["avatar_updated_at"] is None
    rows = _audit_rows(db_engine, "User", user["id"])
    assert len(rows) == 3
    assert rows[2].before_data == {"avatar": _digest(_PNG, "image/png")}
    assert rows[2].after_data == {"avatar": None}
    assert _ok(admin_of(client).delete(path))["avatar_updated_at"] is None
    assert len(_audit_rows(db_engine, "User", user["id"])) == 3
    _refused(client.get(path), 404, "This user has no avatar.")


@pytest.mark.parametrize(
    ("content", "content_type", "status", "detail"),
    [
        pytest.param(_PNG + b"\x00" * (2 * 1024 * 1024), "image/png", 413, _TOO_LARGE, id="large"),
        pytest.param(b"plain text", "image/png", 415, _UNSUPPORTED, id="text-as-png"),
        pytest.param(_PNG, "image/jpeg", 415, _UNSUPPORTED, id="png-as-jpeg"),
        pytest.param(b"", "image/png", 422, "The image is empty.", id="empty"),
    ],
)
def test_user_avatar_refusals_store_nothing(
    client: TestClient,
    db_engine: Engine,
    content: bytes,
    content_type: str,
    status: int,
    detail: str,
) -> None:
    user = _create_user(client, _seeded_role_id(client, "Operator"))
    stored = _stored_user(db_engine, user["id"])
    counts = _write_counts(db_engine)
    _refused(_put_avatar(client, user["id"], content, content_type), status, detail)
    assert _stored_user(db_engine, user["id"]) == stored
    assert _write_counts(db_engine) == counts


@pytest.mark.parametrize("missing_id", [999999, _UNBINDABLE_ID])
def test_avatar_routes_of_an_unknown_user_are_404(client: TestClient, missing_id: int) -> None:
    for response in (
        _put_avatar(client, missing_id, _JPEG, "image/jpeg"),
        admin_of(client).delete(f"/api/users/{missing_id}/avatar"),
        client.get(f"/api/users/{missing_id}/avatar"),
    ):
        _refused(response, 404, f"User {missing_id} does not exist.")


def test_user_avatar_bytes_are_never_loaded_by_default() -> None:
    assert sa.inspect(models.User).attrs["avatar_image"].deferred is True


def test_the_user_theme_preference_has_no_surface(client: TestClient, db_engine: Engine) -> None:
    """U-7 (S12-OD8): stored only — no route, no response field."""
    user = _create_user(client, _seeded_role_id(client, "Operator"))
    counts = _write_counts(db_engine)
    for method in ("PUT", "PATCH", "GET"):
        response = client.request(
            method, f"/api/users/{user['id']}/theme-preference", json={"theme_preference": "DARK"}
        )
        assert response.status_code in (404, 405), response.text
    _refused(
        admin_of(client).patch(f"/api/users/{user['id']}", json={"theme_preference": "DARK"}), 422
    )
    assert "theme_preference" not in user
    assert all("theme_preference" not in item for item in _users(client))
    assert _stored_user(db_engine, user["id"])["theme_preference"] is None
    assert _write_counts(db_engine) == counts


def test_users_are_never_workers(client: TestClient, db_engine: Engine) -> None:
    """U-8: no foreign key either way, no badge column, no badge resolution.

    Phase 14 slice 1 adds the only references to ``users``: the
    credential, the sign-in sessions and the server-derived
    ``actor_user_id`` of audit, Machine lifecycle and allocation rows —
    the allocation's Management actor, never its Worker
    (``allocated_by_worker_id``)."""
    worker_tables = ("workers", "worker_sessions", "part_movements", "work_order_allocations")
    with db_engine.connect() as connection:
        links = set(
            connection.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE contype = 'f' AND ((conrelid = 'users'::regclass"
                    "  AND confrelid::regclass::text = ANY(:tables))"
                    " OR (confrelid = 'users'::regclass"
                    "  AND conrelid::regclass::text = ANY(:tables)))"
                ),
                {"tables": list(worker_tables)},
            ).scalars()
        )
        user_references = set(
            connection.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE contype = 'f' AND confrelid = 'users'::regclass"
                )
            ).scalars()
        )
    assert links == {"fk_work_order_allocations_actor_user_id_users"}
    assert user_references == {
        "fk_user_credentials_user_id_users",
        "fk_user_sessions_user_id_users",
        "fk_audit_events_actor_user_id_users",
        "fk_machine_lifecycle_events_actor_user_id_users",
        "fk_work_order_allocations_actor_user_id_users",
    }
    assert "badge_barcode" not in {
        str(column["name"]) for column in sa.inspect(db_engine).get_columns("users")
    }

    department = _ok(
        admin_of(client).post("/api/departments", json={"name": f"DEPT-{_suffix()}"}), 201
    )
    area = _ok(
        admin_of(client).post(
            "/api/areas", json={"department_id": department["id"], "name": _suffix()}
        ),
        201,
    )
    station = _ok(
        admin_of(client).post(
            "/api/scan-stations", json={"station_id": f"ST-{_suffix()}", "area_id": area["id"]}
        ),
        201,
    )
    scan_path = f"/api/scan-stations/{station['station_id']}/badge-scans"
    worker = _ok(
        admin_of(client).post(
            "/api/workers", json={"name": "Badge Holder", "badge_barcode": _suffix()}
        ),
        201,
    )
    assert _ok(client.post(scan_path, json={"badge": worker["badge_barcode"]}))["outcome"] == (
        "NOT_USED_IN_AREA"
    )
    user = _create_user(client, _seeded_role_id(client, "Operator"))
    for scanned in (user["login_name"], user["login_name"].upper()):
        assert _ok(client.post(scan_path, json={"badge": scanned}))["outcome"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Audit coverage
# ---------------------------------------------------------------------------


def _user_cases(client: TestClient) -> list[tuple[str, Callable[[], Any], int]]:
    """(label, request, audit rows it appends) for every User mutator."""
    role_id = _seeded_role_id(client, "Operator")
    user = _create_user(client, role_id)
    path = f"/api/users/{user['id']}"
    login = f"cov-{_suffix()}"
    body = {"login_name": login, "display_name": "Coverage", "role_id": role_id}
    return [
        ("create", lambda: admin_of(client).post("/api/users", json=body), 1),
        ("create-duplicate", lambda: admin_of(client).post("/api/users", json=body), 0),
        ("update", lambda: admin_of(client).patch(path, json={"display_name": "Covered"}), 1),
        ("update-no-op", lambda: admin_of(client).patch(path, json={"display_name": "Covered"}), 0),
        ("update-refused", lambda: admin_of(client).patch(path, json={"display_name": ""}), 0),
        ("avatar-set", lambda: _put_avatar(client, user["id"], _PNG, "image/png"), 1),
        ("avatar-set-no-op", lambda: _put_avatar(client, user["id"], _PNG, "image/png"), 0),
        ("avatar-refused", lambda: _put_avatar(client, user["id"], b"x", "image/png"), 0),
        ("avatar-remove", lambda: admin_of(client).delete(f"{path}/avatar"), 1),
        ("avatar-remove-no-op", lambda: admin_of(client).delete(f"{path}/avatar"), 0),
    ]


def _role_cases(client: TestClient) -> list[tuple[str, Callable[[], Any], int]]:
    """(label, request, audit rows it appends) for every Role mutator."""
    role = _create_role(client)
    path = f"/api/roles/{role['id']}"
    name = f"Coverage {_suffix()}"
    return [
        ("create", lambda: admin_of(client).post("/api/roles", json={"name": name}), 1),
        ("create-duplicate", lambda: admin_of(client).post("/api/roles", json={"name": name}), 0),
        (
            "grant",
            lambda: admin_of(client).patch(path, json={"grant_permissions": ["EXPORT_REPORTS"]}),
            1,
        ),
        (
            "grant-no-op",
            lambda: admin_of(client).patch(path, json={"grant_permissions": ["EXPORT_REPORTS"]}),
            0,
        ),
        (
            "revoke",
            lambda: admin_of(client).patch(path, json={"revoke_permissions": ["EXPORT_REPORTS"]}),
            1,
        ),
        ("rename-refused", lambda: admin_of(client).patch(path, json={"name": ""}), 0),
    ]


def test_every_effective_write_appends_exactly_one_audit_row(
    client: TestClient, db_engine: Engine
) -> None:
    """U-10."""
    for entity_type, cases in (("User", _user_cases(client)), ("Role", _role_cases(client))):
        for label, request, expected in cases:
            before = _count(
                db_engine, "SELECT count(*) FROM audit_events WHERE entity_type = :t", t=entity_type
            )
            response = request()
            assert response.status_code < 500, (label, response.text)
            after = _count(
                db_engine, "SELECT count(*) FROM audit_events WHERE entity_type = :t", t=entity_type
            )
            assert after - before == expected, label


def test_every_public_mutator_is_audited() -> None:
    """U-10: no mutator exists beyond the audited ones covered above; the
    extra public names are non-mutating builders and checks shared with
    sign-in and first-run setup (Phase 14 slice 1)."""
    public = {
        module.__name__.rsplit(".", 1)[-1]: {
            name
            for name, value in vars(module).items()
            if callable(value)
            and not name.startswith("_")
            and getattr(value, "__module__", None) == module.__name__
        }
        for module in (users_service, roles_service)
    }
    assert public["users"] == {
        "canonical_login_name",
        "profile_snapshot",
        "list_users",
        "create_user",
        "update_user",
        "set_user_avatar",
        "remove_user_avatar",
        "get_user_avatar",
        "UserAvatar",
        "UserView",
        "user_view",
        "lock_user",
        "require_role",
        "reject_duplicate_login",
    }
    assert public["roles"] == {
        "role_snapshot",
        "list_roles",
        "create_role",
        "update_role",
        "RoleView",
    }


# ---------------------------------------------------------------------------
# Isolation: who reads users, roles, permissions and credentials
# ---------------------------------------------------------------------------

# Definers, not readers: the mapped classes and the permission vocabulary.
_DEFINERS = {"app/infrastructure/models.py", "app/domain/enums.py"}
_USER_ROLE_READERS = {
    "app/application/users.py",
    "app/application/roles.py",
    "app/application/user_access.py",
    "app/application/authentication.py",
    "app/application/first_run.py",
    "app/api/users.py",
    "app/api/roles.py",
}
_CREDENTIAL_READERS = {
    "app/application/user_access.py",
    "app/application/authentication.py",
    "app/application/first_run.py",
}
_PERMISSION_READERS = _USER_ROLE_READERS | {
    "app/api/authorization.py",
    "app/api/policies.py",
    "app/api/session.py",
    "app/api/setup.py",
    # Phase 14 slice 2: the permission sets, the rules over them, the
    # route registry and the routes that require a key.
    "app/domain/permissions.py",
    "app/application/authorization.py",
    "app/api/route_access.py",
    "app/api/environment.py",
    "app/api/workers.py",
    # Phase 14 slice 3: the Management routes that require a key.
    "app/api/allocations.py",
    "app/api/machines.py",
    "app/api/part_numbers.py",
    "app/api/production_release.py",
    "app/api/route_templates.py",
    "app/api/work_orders.py",
    # Phase 14 slice 4: what the role applied at Scan Stations grants, the
    # device list's enrollment keys and the station context's keys.
    "app/application/station_access.py",
    "app/api/station_devices.py",
    "app/api/scan_station.py",
    # Phase 14 slice 6: the AssignedRoute adjustment routes.
    "app/api/route_adjustments.py",
}


def _trees() -> dict[str, ast.Module]:
    return {
        path.relative_to(_BACKEND_DIR).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(_APP_DIR.rglob("*.py"))
    }


def _reads(tree: ast.Module, module: str, names: set[str]) -> bool:
    """An ``ImportFrom`` of one of ``names`` from ``module``, or any
    attribute access to one of them (S12's detector shape)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == module and {alias.name for alias in node.names} & names:
                return True
        elif isinstance(node, ast.Attribute) and node.attr in names:
            return True
    return False


def _imported_application_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app.application":
                modules |= {alias.name for alias in node.names}
            elif node.module.startswith("app.application."):
                modules.add(node.module.removeprefix("app.application.").split(".")[0])
        elif isinstance(node, ast.Import):
            modules |= {
                alias.name.removeprefix("app.application.").split(".")[0]
                for alias in node.names
                if alias.name.startswith("app.application.")
            }
    return modules


def _flagged(module: str, names: set[str]) -> set[str]:
    return {
        path
        for path, tree in _trees().items()
        if path not in _DEFINERS and _reads(tree, module, names)
    }


def test_only_the_sign_in_and_configuration_modules_read_users_roles_or_permissions() -> None:
    """B-STATIC (replaces S12 U-11): Users, roles, grants, credentials,
    sessions and permission keys are read only where sign-in, first-run
    setup, the checked routes and the configuration live. Each "⊆" is
    paired with required members, so the rule cannot pass vacuously."""
    models = "app.infrastructure.models"
    users_roles = _flagged(models, {"User", "Role", "RolePermission"})
    assert users_roles <= _USER_ROLE_READERS
    assert {
        "app/application/users.py",
        "app/application/roles.py",
        "app/application/user_access.py",
    } <= users_roles
    credentials = _flagged(models, {"UserCredential", "UserSession"})
    assert credentials <= _CREDENTIAL_READERS
    assert {"app/application/user_access.py", "app/application/authentication.py"} <= credentials
    permissions = _flagged("app.domain.enums", {"Permission"})
    assert permissions <= _PERMISSION_READERS
    assert {
        "app/application/roles.py",
        "app/api/roles.py",
        "app/api/authorization.py",
        "app/domain/permissions.py",
        "app/application/authorization.py",
    } <= permissions
    for owner in _PERMISSION_READERS | _DEFINERS:
        assert (_BACKEND_DIR / owner).is_file(), owner


def test_password_hashing_and_the_sign_in_import_graph() -> None:
    """B-STATIC: who may hash, and an acyclic users / sign-in graph."""
    imports = {path: _imported_application_modules(tree) for path, tree in _trees().items()}
    hashing = {path for path, modules in imports.items() if "password_hashing" in modules}
    assert hashing <= {"app/application/authentication.py", "app/application/first_run.py"}
    assert "app/application/authentication.py" in hashing
    assert not {"users", "authentication"} & imports["app/application/user_access.py"]
    assert not {"authentication", "first_run"} & imports["app/application/users.py"]
    assert not {"users", "authentication", "first_run"} & imports["app/application/roles.py"]
    assert "first_run" not in imports["app/application/authentication.py"]


def test_the_permission_rules_stay_plain_and_out_of_the_configuration_services() -> None:
    """B-STATIC (Phase 14 slices 2–3): ``application/authorization.py`` reads
    no model, no framework and no application module but ``errors`` (the
    Hot list membership rule is a domain import); the environment, Worker
    and policy services and the Management services know nothing about
    permissions (their routes check them) and never read the User model
    (Machines, allocations, Tracking and the audit trail read display
    references through ``user_access``)."""
    trees = _trees()
    rules = trees["app/application/authorization.py"]
    imported = {
        node.module
        for node in ast.walk(rules)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(rules)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {module for module in imported if module.startswith(("fastapi", "sqlalchemy"))}
    assert "app.infrastructure.models" not in imported
    assert _imported_application_modules(rules) == {"errors"}
    assert not _reads(trees["app/domain/hot_list.py"], "app.domain.enums", {"Permission"})
    for service in ("environment", "workers", "policies"):
        tree = trees[f"app/application/{service}.py"]
        assert not _reads(tree, "app.domain.enums", {"Permission"}), service
        assert "authorization" not in _imported_application_modules(tree), service
    for service in (
        "machines",
        "part_numbers",
        "work_orders",
        "production_release",
        "hot_list",
        "hot_ranks",
        "allocations",
        "route_templates",
        "tracking",
        "route_adjustments",
        "audit_trail",
    ):
        tree = trees[f"app/application/{service}.py"]
        assert not _reads(tree, "app.domain.enums", {"Permission"}), service
        assert "authorization" not in _imported_application_modules(tree), service
        assert not _reads(tree, "app.infrastructure.models", {"User"}), service
    for service in ("machines", "allocations", "tracking", "route_adjustments", "audit_trail"):
        tree = trees[f"app/application/{service}.py"]
        assert "user_access" in _imported_application_modules(tree), service


_STATION_COMMAND_MODULES = (
    "scan_station",
    "intake",
    "machine_processing",
    "direct_processing",
    "transfers",
    "stockroom",
    "merges",
    "quantity_events",
    "undo",
    "allocations",
)
# One station key check per service body (Phase 14 slice 4, §3.6): the
# shared bodies ``_leave_machine`` and ``record_arrival`` each map two
# command kinds; the station allocation checks twice (confirmation and
# adjustment), both only for a station.
_STATION_KEY_CHECKS = {
    ("scan_station", "badge_scan"): 1,
    ("scan_station", "resolve_part_number_scan"): 1,
    ("scan_station", "resolve_machine_scan"): 1,
    ("intake", "receive_quantity"): 1,
    ("machine_processing", "assign_to_machine"): 1,
    ("machine_processing", "_leave_machine"): 1,
    ("direct_processing", "complete_direct_processing"): 1,
    ("transfers", "record_arrival"): 1,
    ("merges", "merge_flows"): 1,
    ("quantity_events", "scrap_flow"): 1,
    ("quantity_events", "add_quantity"): 1,
    ("allocations", "suggest_station_allocation"): 1,
    ("allocations", "_confirm_allocation"): 2,
    ("undo", "undo_preview"): 1,
    ("undo", "undo_command"): 1,
}


def _is_key_check(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "require_station_capability"
    )


def _guards_station(test: ast.expr) -> bool:
    """``station_id is not None`` (alone or in an ``and``)."""
    return any(
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "station_id"
        and isinstance(node.ops[0], ast.IsNot)
        for node in ast.walk(test)
    )


def test_station_commands_check_the_station_role_once_per_service_body() -> None:
    """SD-26 (Phase 14 slice 4): the station command modules know no
    permission, role or User and no device; the station role's keys are
    checked through ``station_access`` exactly once per service body (twice
    in the station allocation, each only for a station); the device
    service is judged like the other guard writes, without a permission
    key of its own."""
    trees = _trees()
    counts: dict[tuple[str, str], int] = {}
    for module in _STATION_COMMAND_MODULES:
        tree = trees[f"app/application/{module}.py"]
        assert not _reads(tree, "app.domain.enums", {"Permission"}), module
        assert not _reads(tree, "app.infrastructure.models", {"Role", "RolePermission", "User"}), (
            module
        )
        assert not {"station_devices", "authorization"} & _imported_application_modules(tree)
        for function in tree.body:
            if isinstance(function, ast.FunctionDef):
                calls = sum(1 for node in ast.walk(function) if _is_key_check(node))
                if calls:
                    counts[(module, function.name)] = calls
    assert counts == _STATION_KEY_CHECKS
    confirm = next(
        node
        for node in trees["app/application/allocations.py"].body
        if isinstance(node, ast.FunctionDef) and node.name == "_confirm_allocation"
    )
    guarded = sum(
        1
        for branch in ast.walk(confirm)
        if isinstance(branch, ast.If) and _guards_station(branch.test)
        for statement in branch.body
        for node in ast.walk(statement)
        if _is_key_check(node)
    )
    assert guarded == 2
    devices = trees["app/application/station_devices.py"]
    assert {"station_access", "authorization", "user_access", "audit"} <= (
        _imported_application_modules(devices)
    )
    assert not _reads(devices, "app.domain.enums", {"Permission"})
