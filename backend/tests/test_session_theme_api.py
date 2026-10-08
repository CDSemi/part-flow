"""Integration tests for Phase 14 slice 8 — the signed-in User's theme preference.

Exercises the full request path — FastAPI routes, the Application
service and PostgreSQL — against a dedicated temporary database migrated
to head by the real Alembic chain (GUI_DESIGN §2.1 User tier; owner
decision OD-P16):

- ``GET /api/session`` and every session-creating response (sign in,
  own password change, first-run setup) report the stored preference
  (null = none), converted on every path;
- ``PUT /api/session/theme-preference`` saves an absolute ``DARK`` /
  ``LIGHT`` value on the caller's own row only and echoes the value this
  request saved or kept; a repeat performs no UPDATE (``xmin``
  unchanged);
- the write is never audited and touches nothing else (``updated_at``,
  sessions, credentials); refusals (A1, A3, A4, 422) write nothing;
- locking: one User row lock FOR NO KEY UPDATE, never the
  User-administration advisory lock; a deactivation, a session end or a
  forced password change committed while the write waited refuses it;
- the User tier and the Scan Station tier are stored independently.

The API commits real transactions, so tests isolate through fresh
harness identities and unique Scan Stations; the module database is
dropped afterwards. Helpers are copied from the station theme and
first-run setup modules (each module owns its own).
"""

import logging
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
from app.application import authentication
from app.application.errors import InvalidInputError
from app.core.config import get_settings
from app.domain.enums import ThemePreference
from app.main import create_app
from tests.auth_harness import (
    CSRF_HEADERS,
    TEST_PASSWORD,
    IdentityClient,
    admin_of,
    anonymous_client,
    another_session,
    client_as,
    station_device_client,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_session_theme_api"
_DB_URL_ENV = "DATABASE_URL"
_PATH = "/api/session/theme-preference"
_USER_ADMINISTRATION_LOCK = "partflow:user-administration"
_NEW_PASSWORD = "a-new-theme-test-password"
_ADMIN_PASSWORD = "first-administrator-password"
_TOKEN = re.compile(r"Setup token: ([A-Z2-7]{4}(?:-[A-Z2-7]{4}){7}) ")

_A1 = "You are not signed in, or your sign-in has ended. Sign in to continue."
_A3 = "Choose a new password before you continue."
_E2 = "Theme preference must be DARK or LIGHT."


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


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
def announcements() -> Iterator[list[str]]:
    """Every WARNING of ``app.first_run`` (the setup token), captured from before startup."""
    handler = _ListHandler()
    logger = logging.getLogger("app.first_run")
    logger.addHandler(handler)
    yield handler.messages
    logger.removeHandler(handler)


@pytest.fixture(scope="module")
def client(api_database_url: URL, announcements: list[str]) -> Iterator[TestClient]:
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
    """Direct database access for assertions and concurrent holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _execute(engine: Engine, sql: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(sql), params)


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


@contextmanager
def _policy(engine: Engine, **values: object) -> Iterator[None]:
    """Set sign-in policy values directly; restore the previous ones afterwards."""
    names = ", ".join(values)
    with engine.connect() as connection:
        previous = dict(
            connection.execute(sa.text(f"SELECT {names} FROM application_policy")).mappings().one()
        )
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    _execute(engine, f"UPDATE application_policy SET {assignments}", **values)
    try:
        yield
    finally:
        _execute(engine, f"UPDATE application_policy SET {assignments}", **previous)


class _UserRow:
    def __init__(self, row: sa.Row[Any]) -> None:
        self.theme_preference: str | None = row.theme_preference
        self.updated_at: Any = row.updated_at
        self.xmin: str = row.xmin


def _user(engine: Engine, user_id: int) -> _UserRow:
    with engine.connect() as connection:
        return _UserRow(
            connection.execute(
                sa.text(
                    "SELECT theme_preference, updated_at, xmin::text AS xmin"
                    " FROM users WHERE id = :id"
                ),
                {"id": user_id},
            ).one()
        )


def _set_stored(engine: Engine, user_id: int, value: str | None) -> None:
    _execute(
        engine, "UPDATE users SET theme_preference = :value WHERE id = :id", value=value, id=user_id
    )


def _sessions(engine: Engine, user_id: int) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id, ended_at, end_reason FROM user_sessions"
                    " WHERE user_id = :id ORDER BY id"
                ),
                {"id": user_id},
            )
        ]


def _credential(engine: Engine, user_id: int) -> tuple[Any, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                sa.text(
                    "SELECT password_hash, password_is_temporary, password_changed_at,"
                    " failed_attempts, locked_until FROM user_credentials WHERE user_id = :id"
                ),
                {"id": user_id},
            ).one()
        )


def _put(client: TestClient, theme: object) -> Any:
    return client.put(_PATH, json={"theme_preference": theme})


def _session_user(client: TestClient) -> dict[str, Any] | None:
    return cast(dict[str, Any] | None, _ok(client.get("/api/session"))["user"])


def _refused_a1(response: Any) -> None:
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == _A1
    assert body["authentication_required"] is True


def _refused_a3(response: Any) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["detail"] == _A3
    assert body["password_change_required"] is True


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


def _station(client: TestClient) -> str:
    """A new Scan Station on a new Area (the station theme module's cell, without production)."""
    admin = admin_of(client)
    department = _ok(admin.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    area = _ok(
        admin.post("/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}),
        201,
    )
    station = _ok(
        admin.post("/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area["id"]}),
        201,
    )
    return str(station["station_id"])


# ---------------------------------------------------------------------------
# Read and write
# ---------------------------------------------------------------------------


def test_the_session_reports_the_stored_preference(client: TestClient, db_engine: Engine) -> None:
    """T-1."""
    user = client_as(client)
    session_user = _session_user(user)
    assert session_user is not None
    assert session_user["theme_preference"] is None
    assert _ok(anonymous_client(client).get("/api/session"))["user"] is None

    _set_stored(db_engine, user.user_id, "LIGHT")
    assert user.identity is not None
    signed_in = _ok(
        anonymous_client(client).post(
            "/api/session",
            json={"login_name": user.identity.login_name, "password": TEST_PASSWORD},
            headers=CSRF_HEADERS,
        )
    )
    assert signed_in["user"]["theme_preference"] == "LIGHT"
    reported = _session_user(user)
    assert reported is not None
    assert reported["theme_preference"] == "LIGHT"


def test_put_saves_each_value_and_the_session_reports_it(
    client: TestClient, db_engine: Engine
) -> None:
    """T-2."""
    user = client_as(client)
    for theme in ("LIGHT", "DARK"):
        response = _put(user, theme)
        assert _ok(response) == {"theme_preference": theme}
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        reported = _session_user(user)
        assert reported is not None
        assert reported["theme_preference"] == theme
        assert _user(db_engine, user.user_id).theme_preference == theme


def test_a_repeat_is_a_no_op_without_an_update(client: TestClient, db_engine: Engine) -> None:
    """T-3."""
    user = client_as(client)
    first = _ok(_put(user, "LIGHT"))
    after_first = _user(db_engine, user.user_id)
    second = _ok(_put(user, "LIGHT"))
    after_second = _user(db_engine, user.user_id)
    assert first == second == {"theme_preference": "LIGHT"}
    assert after_second.theme_preference == "LIGHT"
    # A row lock leaves xmin alone; any UPDATE would change it.
    assert after_second.xmin == after_first.xmin


def test_the_write_is_not_audited_and_touches_nothing_else(
    client: TestClient, db_engine: Engine
) -> None:
    """T-4."""
    user = client_as(client)
    before = _user(db_engine, user.user_id)
    sessions = _sessions(db_engine, user.user_id)
    credential = _credential(db_engine, user.user_id)
    audits = _count(db_engine, "audit_events")
    all_sessions = _count(db_engine, "user_sessions")
    for theme in ("LIGHT", "DARK", "LIGHT", "LIGHT"):
        _ok(_put(user, theme))
    after = _user(db_engine, user.user_id)
    assert after.theme_preference == "LIGHT"
    assert after.updated_at == before.updated_at
    assert _count(db_engine, "audit_events") == audits
    assert _count(db_engine, "user_sessions") == all_sessions
    assert _sessions(db_engine, user.user_id) == sessions
    assert sessions[0][1] is None  # the caller's session is still open
    assert _credential(db_engine, user.user_id) == credential


def test_only_the_callers_own_row_changes(client: TestClient, db_engine: Engine) -> None:
    """T-5."""
    user_a, user_b = client_as(client), client_as(client)
    _ok(_put(user_a, "LIGHT"))
    assert _user(db_engine, user_a.user_id).theme_preference == "LIGHT"
    assert _user(db_engine, user_b.user_id).theme_preference is None

    before_b = _user(db_engine, user_b.user_id)
    response = user_a.put(_PATH, json={"theme_preference": "DARK", "user_id": user_b.user_id})
    assert response.status_code == 422, response.text
    after_b = _user(db_engine, user_b.user_id)
    assert (after_b.theme_preference, after_b.xmin) == (None, before_b.xmin)
    assert _user(db_engine, user_a.user_id).theme_preference == "LIGHT"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def _assert_unchanged(engine: Engine, user_id: int, value: str | None, audits: int) -> None:
    assert _user(engine, user_id).theme_preference == value
    assert _count(engine, "audit_events") == audits


def test_anonymous_and_csrf_less_requests_are_refused(
    client: TestClient, db_engine: Engine
) -> None:
    """T-6 (a), (b)."""
    user = client_as(client)
    assert user.identity is not None
    audits = _count(db_engine, "audit_events")
    _refused_a1(
        anonymous_client(client).put(
            _PATH, json={"theme_preference": "LIGHT"}, headers=CSRF_HEADERS
        )
    )

    without_header = TestClient(
        client.app, headers={"Cookie": f"{authentication.SESSION_COOKIE}={user.identity.token}"}
    )
    response = without_header.put(_PATH, json={"theme_preference": "LIGHT"})
    assert response.status_code == 403, response.text
    assert response.json()["csrf_rejected"] is True
    _assert_unchanged(db_engine, user.user_id, None, audits)


def test_an_ended_or_expired_sign_in_is_refused(client: TestClient, db_engine: Engine) -> None:
    """T-6 (c), (d)."""
    signed_out = client_as(client)
    _ok(_put(signed_out, "LIGHT"))
    assert signed_out.delete("/api/session").status_code == 204
    audits = _count(db_engine, "audit_events")
    response = _put(signed_out, "DARK")
    _refused_a1(response)
    _assert_unchanged(db_engine, signed_out.user_id, "LIGHT", audits)

    expired = client_as(client)
    with _policy(db_engine, user_session_expires=True, user_session_days=30):
        _execute(
            db_engine,
            "UPDATE user_sessions SET created_at = now() - interval '31 days' WHERE user_id = :id",
            id=expired.user_id,
        )
        _refused_a1(_put(expired, "LIGHT"))
    _assert_unchanged(db_engine, expired.user_id, None, audits)


def test_a_pending_password_change_is_refused_while_the_policy_requires_it(
    client: TestClient, db_engine: Engine
) -> None:
    """T-6 (e)."""
    user = client_as(client, temporary_password=True)
    audits = _count(db_engine, "audit_events")
    with _policy(db_engine, require_password_change=True):
        _refused_a3(_put(user, "LIGHT"))
        _assert_unchanged(db_engine, user.user_id, None, audits)
    with _policy(db_engine, require_password_change=False):
        assert _ok(_put(user, "LIGHT")) == {"theme_preference": "LIGHT"}
    _assert_unchanged(db_engine, user.user_id, "LIGHT", audits)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"theme_preference": None},
        {"theme_preference": "light"},
        {"theme_preference": "Dark"},
        {"theme_preference": "AUTO"},
        {"theme_preference": ""},
        {"theme_preference": 1},
        {"theme_preference": "DARK", "display_name": "x"},
    ],
    ids=["empty", "null", "lower", "mixed", "unknown", "blank", "number", "extra"],
)
def test_invalid_bodies_are_refused_with_nothing_written(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    """T-7 (HTTP)."""
    user = client_as(client)
    _ok(_put(user, "LIGHT"))
    before = _user(db_engine, user.user_id)
    audits = _count(db_engine, "audit_events")
    response = user.put(_PATH, json=body)
    assert response.status_code == 422, response.text
    after = _user(db_engine, user.user_id)
    assert (after.theme_preference, after.xmin) == ("LIGHT", before.xmin)
    assert _count(db_engine, "audit_events") == audits


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["BLUE", None, "light", 1])
def test_the_service_refuses_a_non_member(
    client: TestClient, db_engine: Engine, value: object
) -> None:
    """T-7 (service)."""
    user = client_as(client)
    assert user.identity is not None
    before = _user(db_engine, user.user_id)
    with Session(db_engine) as session:
        principal = authentication.resolve_principal(session, user.identity.token)
        assert principal is not None
        with pytest.raises(InvalidInputError) as raised:
            authentication.set_own_theme_preference(session, principal, theme_preference=value)
    assert str(raised.value) == _E2
    after = _user(db_engine, user.user_id)
    assert (after.theme_preference, after.xmin) == (None, before.xmin)


def test_the_service_returns_the_validated_member(client: TestClient, db_engine: Engine) -> None:
    """T-10."""
    user = client_as(client)
    assert user.identity is not None
    for _attempt in range(2):  # a write, then a repeat
        with Session(db_engine) as session:
            principal = authentication.resolve_principal(session, user.identity.token)
            assert principal is not None
            saved = authentication.set_own_theme_preference(
                session, principal, theme_preference="LIGHT"
            )
        assert type(saved) is ThemePreference
        assert saved is ThemePreference.LIGHT
    assert _user(db_engine, user.user_id).theme_preference == "LIGHT"


def test_the_principal_carries_the_converted_preference(
    client: TestClient, db_engine: Engine
) -> None:
    """The column reads as ``str``; the principal carries the enum member."""
    user = client_as(client)
    assert user.identity is not None
    _set_stored(db_engine, user.user_id, "DARK")
    with Session(db_engine) as session:
        principal = authentication.resolve_principal(session, user.identity.token)
    assert principal is not None
    assert type(principal.theme_preference) is ThemePreference
    assert principal.theme_preference is ThemePreference.DARK


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "waits"),
    [("FOR UPDATE", True), ("FOR NO KEY UPDATE", True), ("FOR KEY SHARE", False)],
    ids=["rename-style", "set-password-style", "fk-check"],
)
def test_the_write_locks_only_the_user_row(
    client: TestClient, db_engine: Engine, mode: str, waits: bool
) -> None:
    """T-8 (a), (b), (c)."""
    user = client_as(client)
    with db_engine.connect() as holder:
        holder.execute(sa.text(f"SELECT 1 FROM users WHERE id = :id {mode}"), {"id": user.user_id})
        if waits:
            thread, results = _start(lambda: _put(user, "LIGHT"))
            _assert_blocked(thread)
            holder.commit()
            response = _finish(thread, results)
        else:
            response = _put(user, "LIGHT")
            holder.rollback()
    assert _ok(response) == {"theme_preference": "LIGHT"}
    assert _user(db_engine, user.user_id).theme_preference == "LIGHT"


def test_the_write_never_waits_on_the_user_administration_lock(
    client: TestClient, db_engine: Engine
) -> None:
    """T-8 (d)."""
    user = client_as(client)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": _USER_ADMINISTRATION_LOCK},
        )
        response = _put(user, "LIGHT")
        assert _ok(response) == {"theme_preference": "LIGHT"}
        holder.rollback()
    assert _user(db_engine, user.user_id).theme_preference == "LIGHT"


def _refused_after_waiting(
    client: TestClient,
    db_engine: Engine,
    user: IdentityClient,
    change: str,
    params: dict[str, object],
) -> Any:
    """Hold the User row (FOR NO KEY UPDATE) with ``change`` uncommitted, start
    the PUT (its principal resolves before the commit), commit, and return
    its response."""
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM users WHERE id = :id FOR NO KEY UPDATE"), {"id": user.user_id}
        )
        holder.execute(sa.text(change), params)
        thread, results = _start(lambda: _put(user, "LIGHT"))
        _assert_blocked(thread)
        holder.commit()
    return _finish(thread, results)


def test_a_deactivation_committed_while_waiting_refuses(
    client: TestClient, db_engine: Engine
) -> None:
    """T-9."""
    user = client_as(client)
    audits = _count(db_engine, "audit_events")
    response = _refused_after_waiting(
        client,
        db_engine,
        user,
        "UPDATE users SET is_active = false WHERE id = :id",
        {"id": user.user_id},
    )
    _refused_a1(response)
    _assert_unchanged(db_engine, user.user_id, None, audits)


def test_a_session_end_committed_while_waiting_refuses(
    client: TestClient, db_engine: Engine
) -> None:
    """T-9b: the User stays active; only the caller's session ends."""
    user = client_as(client)
    audits = _count(db_engine, "audit_events")
    response = _refused_after_waiting(
        client,
        db_engine,
        user,
        "UPDATE user_sessions SET ended_at = now(), end_reason = 'PASSWORD_RESET'"
        " WHERE user_id = :id AND ended_at IS NULL",
        {"id": user.user_id},
    )
    _refused_a1(response)
    _assert_unchanged(db_engine, user.user_id, None, audits)


def test_a_forced_change_committed_while_waiting_refuses(
    client: TestClient, db_engine: Engine
) -> None:
    """T-9c: the re-check judges the whole sign-in, not the session alone."""
    user = client_as(client)
    audits = _count(db_engine, "audit_events")
    with _policy(db_engine, require_password_change=True):
        response = _refused_after_waiting(
            client,
            db_engine,
            user,
            "UPDATE user_credentials SET password_is_temporary = true WHERE user_id = :id",
            {"id": user.user_id},
        )
    _refused_a3(response)
    _assert_unchanged(db_engine, user.user_id, None, audits)


# ---------------------------------------------------------------------------
# Tier independence and session-creating responses
# ---------------------------------------------------------------------------


def test_the_user_and_station_tiers_are_independent(client: TestClient, db_engine: Engine) -> None:
    """T-11."""
    station_id = _station(client)
    user = client_as(client)
    station = station_device_client(user)
    station_path = f"/api/scan-stations/{station_id}/theme-preference"

    _ok(station.put(station_path, json={"theme_preference": "LIGHT"}))
    _ok(_put(user, "DARK"))
    assert _ok(station.get(f"/api/scan-stations/{station_id}/context"))["theme_preference"] == (
        "LIGHT"
    )
    reported = _session_user(user)
    assert reported is not None
    assert reported["theme_preference"] == "DARK"

    _ok(station.put(station_path, json={"theme_preference": "DARK"}))
    _ok(_put(user, "LIGHT"))
    assert _user(db_engine, user.user_id).theme_preference == "LIGHT"
    assert _ok(station.get(f"/api/scan-stations/{station_id}/context"))["theme_preference"] == (
        "DARK"
    )
    reported = _session_user(user)
    assert reported is not None
    assert reported["theme_preference"] == "LIGHT"


def test_the_own_password_change_response_carries_the_preference(
    client: TestClient, db_engine: Engine
) -> None:
    """T-12 (own password change)."""
    user = client_as(client)
    _set_stored(db_engine, user.user_id, "LIGHT")
    state = _ok(
        user.put(
            "/api/session/password",
            json={"current_password": TEST_PASSWORD, "new_password": _NEW_PASSWORD},
        )
    )
    assert state["user"]["theme_preference"] == "LIGHT"


def test_the_first_administrator_response_carries_the_preference(
    client: TestClient, db_engine: Engine, announcements: list[str]
) -> None:
    """T-12 (first-run setup): setup is re-opened by deactivating every
    credentialed administrator (the first-run module's ``_open_setup``),
    and those Users are re-activated afterwards so later cases keep them."""
    admin_of(client)  # exists before the re-open, whatever ran first
    with db_engine.begin() as connection:
        deactivated = [
            int(row[0])
            for row in connection.execute(
                sa.text(
                    "UPDATE users SET is_active = false WHERE is_active AND id IN ("
                    " SELECT u.id FROM users u JOIN user_credentials c ON c.user_id = u.id"
                    " JOIN role_permissions rp ON rp.role_id = u.role_id"
                    " AND rp.permission = 'MANAGE_USERS_AND_ROLES') RETURNING id"
                )
            )
        ]
    try:
        anonymous = anonymous_client(client)
        assert _ok(anonymous.get("/api/setup"))["open"] is True
        tokens = [match.group(1) for message in announcements if (match := _TOKEN.search(message))]
        assert tokens, announcements
        with db_engine.connect() as connection:
            role_id = connection.execute(
                sa.text("SELECT id FROM roles WHERE name = 'Administrator'")
            ).scalar_one()
        suffix = uuid.uuid4().hex[:10]
        state = _ok(
            anonymous.post(
                "/api/setup/administrator",
                json={
                    "setup_token": tokens[-1],
                    "login_name": f"admin-{suffix}",
                    "display_name": f"Admin {suffix}",
                    "role_id": role_id,
                    "password": _ADMIN_PASSWORD,
                },
                headers=CSRF_HEADERS,
            ),
            201,
        )
        assert "theme_preference" in state["user"]
        assert state["user"]["theme_preference"] is None
    finally:
        _execute(
            db_engine,
            "UPDATE users SET is_active = true WHERE id = ANY(:ids)",
            ids=deactivated,
        )


def test_a_second_session_of_the_same_user_sees_the_saved_value(client: TestClient) -> None:
    """Last writer wins; another browser reads the value at its next session read."""
    user = client_as(client)
    other = another_session(client, user)
    _ok(_put(user, "LIGHT"))
    _ok(_put(other, "DARK"))
    for session_client in (user, other):
        reported = _session_user(session_client)
        assert reported is not None
        assert reported["theme_preference"] == "DARK"
