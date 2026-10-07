"""Integration tests for Phase 14 slice 1 — first-run setup of the first administrator.

Exercises the full request path against a dedicated temporary database
migrated to head by the real Alembic chain (owner decisions OD-P4/OD-P5):
the setup token is announced once per token in the server log while no
administrator exists, the creation requires it, is atomic (one
administrator out of two concurrent attempts), re-checks the chosen
role's eligibility under its lock, rotates the token afterwards and
writes nothing on any refusal; startup never fails over it.

The setup token is read the way an operator reads it — from the
``app.first_run`` log. The module attaches its own list handler to that
logger BEFORE the module client starts (a function-scoped ``caplog``
cannot see the lifespan of a module-scoped client). ``_current_token``
re-opens setup (every credentialed administrator deactivated), observes
it and parses the last announced token, so cases are order-independent.
Production code has no test-only accessor.
"""

import http.cookiejar
import logging
import os
import re
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import first_run
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_first_run_setup_api"
_CSRF = {"X-PartFlow-CSRF": "1"}
_PASSWORD = "first-administrator-password"
_TOKEN = re.compile(r"Setup token: ([A-Z2-7]{4}(?:-[A-Z2-7]{4}){7}) ")
_ANNOUNCEMENT = (
    "PartFlow first-run setup is open: no administrator exists. Setup token: {token}"
    ' — enter it in PartFlow under "Set up PartFlow". It stops working once the first'
    " administrator is created or the server restarts."
)
_S1 = "PartFlow already has an administrator. Sign in instead."
_S2 = "The setup token is not correct. Copy the current token from the PartFlow server log."
_A4 = (
    "This request was refused because it did not come from the PartFlow application."
    " Reload the page and try again."
)
_P1 = "A password must be at least 12 characters long."
_FALLBACK = (
    "First-run state could not be determined at startup; the setup token is announced when"
    " the setup screen is first requested."
)


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    """The test client keeps no cookie: a created administrator's session
    never leaks into the next request."""

    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False


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
def announcements() -> Iterator[list[str]]:
    """Every WARNING of ``app.first_run``, captured from before startup."""
    handler = _ListHandler()
    logger = logging.getLogger("app.first_run")
    logger.addHandler(handler)
    yield handler.messages
    logger.removeHandler(handler)


@pytest.fixture(scope="module")
def client(api_database_url: URL, announcements: list[str]) -> Iterator[TestClient]:
    """Application client wired to the temporary database through the
    real startup path (DATABASE_URL pointed at it, settings re-read)."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            test_client.cookies.jar.set_policy(_NoCookies())
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
    return uuid.uuid4().hex[:10]


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _refused(response: Any, status: int, detail: str | None = None) -> None:
    assert response.status_code == status, response.text
    if detail is not None:
        assert response.json()["detail"] == detail


def _execute(engine: Engine, sql: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(sql), params)


def _counts(engine: Engine) -> tuple[int, ...]:
    """Users, credentials, sessions and audit rows — a refusal changes none."""
    with engine.connect() as connection:
        return tuple(
            int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in ("users", "user_credentials", "user_sessions", "audit_events")
        )


def _administrators(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.text(
                    "SELECT count(*) FROM users u JOIN user_credentials c ON c.user_id = u.id"
                    " JOIN role_permissions rp ON rp.role_id = u.role_id"
                    " AND rp.permission = 'MANAGE_USERS_AND_ROLES' WHERE u.is_active"
                )
            ).scalar_one()
        )


def _open_setup(engine: Engine) -> None:
    """Deactivate every credentialed administrator: setup re-opens."""
    _execute(
        engine,
        "UPDATE users SET is_active = false WHERE is_active AND id IN ("
        " SELECT u.id FROM users u JOIN user_credentials c ON c.user_id = u.id"
        " JOIN role_permissions rp ON rp.role_id = u.role_id"
        " AND rp.permission = 'MANAGE_USERS_AND_ROLES')",
    )


def _last_token(messages: list[str]) -> str:
    found = [match.group(1) for message in messages if (match := _TOKEN.search(message))]
    assert found, messages
    return found[-1]


def _current_token(client: TestClient, engine: Engine, messages: list[str]) -> str:
    _open_setup(engine)
    assert _ok(client.get("/api/setup"))["open"] is True
    return _last_token(messages)


def _role_id(client: TestClient, name: str) -> int:
    roles = _ok(client.get("/api/roles"))
    return int(
        next(role["id"] for role in cast(list[dict[str, Any]], roles) if role["name"] == name)
    )


def _body(token: str, role_id: object, **overrides: object) -> dict[str, object]:
    suffix = _suffix()
    body: dict[str, object] = {
        "setup_token": token,
        "login_name": f"admin-{suffix}",
        "display_name": f"Admin {suffix}",
        "role_id": role_id,
        "password": _PASSWORD,
    }
    body.update(overrides)
    return body


def _create(client: TestClient, body: dict[str, object], headers: dict[str, str] = _CSRF) -> Any:
    return client.post("/api/setup/administrator", json=body, headers=headers)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def test_startup_announces_the_token_once(
    client: TestClient,
    db_engine: Engine,
    announcements: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F-1: a fresh process (its own gate) announces exactly once."""
    _open_setup(db_engine)
    caplog.set_level(logging.DEBUG)
    mark = len(announcements)
    own = _ListHandler()
    logger = logging.getLogger("app.first_run")
    logger.addHandler(own)
    try:
        with TestClient(create_app()) as fresh:
            assert len(own.messages) == 1
            token = _last_token(own.messages)
            assert own.messages[0] == _ANNOUNCEMENT.format(token=token)
            status = _ok(fresh.get("/api/setup"))
            assert status["open"] is True
            assert "Administrator" in {role["name"] for role in status["eligible_roles"]}
            assert _ok(fresh.get("/api/session"))["setup_open"] is True
            _ok(fresh.get("/api/setup"))
            assert len(own.messages) == 1
    finally:
        logger.removeHandler(own)
        # The fresh process's announcement is not the module client's token.
        del announcements[mark:]
    carrying = [record for record in caplog.records if token in record.getMessage()]
    assert len(carrying) == 1 and carrying[0].name == "app.first_run"
    assert carrying[0].levelno == logging.WARNING


def test_setup_creates_the_first_administrator_and_closes(
    client: TestClient, db_engine: Engine, announcements: list[str]
) -> None:
    """F-2."""
    token = _current_token(client, db_engine, announcements)
    role_id = _role_id(client, "Administrator")
    before = _counts(db_engine)
    _refused(_create(client, _body("AAAA-" + token[5:], role_id)), 403, _S2)
    assert _create(client, _body("AAAA-" + token[5:], role_id)).json()["setup_token_invalid"]
    assert _counts(db_engine) == before

    body = _body(token.lower().replace("-", " "), role_id)
    response = _create(client, body)
    state = _ok(response, 201)
    assert response.headers["cache-control"] == "no-store"
    assert "partflow_session=" in response.headers["set-cookie"]
    user = state["user"]
    assert {"MANAGE_USERS_AND_ROLES", "MANAGE_CORRECTION_PERMISSIONS"} <= set(user["permissions"])
    assert user["must_change_password"] is False
    assert state["setup_open"] is False
    with db_engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT event_type, metadata, actor_user_id, before_data, after_data"
                " FROM audit_events WHERE entity_type = 'User' AND entity_id = :id ORDER BY id"
            ),
            {"id": str(user["id"])},
        ).all()
        temporary = connection.execute(
            sa.text("SELECT password_is_temporary FROM user_credentials WHERE user_id = :id"),
            {"id": user["id"]},
        ).scalar_one()
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert all(row.metadata == {"source": "first-run-setup"} for row in rows)
    assert all(row.actor_user_id is None for row in rows)
    assert rows[1].after_data["password_set"] is True
    assert temporary is False
    assert _ok(client.get("/api/setup")) == {"open": False, "eligible_roles": []}

    # A retry after an unknown outcome reads "already set up".
    _refused(_create(client, body), 409, _S1)
    assert _create(client, body).json()["setup_closed"] is True

    _open_setup(db_engine)
    _refused(_create(client, _body(token, role_id)), 403, _S2)
    _ok(client.get("/api/setup"))
    rotated = _last_token(announcements)
    assert rotated != token
    _ok(_create(client, _body(rotated, role_id)), 201)


def test_two_concurrent_setups_create_one_administrator(
    client: TestClient, db_engine: Engine, announcements: list[str]
) -> None:
    """F-3."""
    token = _current_token(client, db_engine, announcements)
    role_id = _role_id(client, "Administrator")
    barrier = threading.Barrier(2)
    responses: list[Any] = []

    def attempt() -> None:
        body = _body(token, role_id)
        barrier.wait()
        responses.append(_create(client, body))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(response.status_code for response in responses) == [201, 409]
    refused = next(response for response in responses if response.status_code == 409)
    assert refused.json()["detail"] == _S1
    assert _administrators(db_engine) == 1


def test_role_eligibility_is_rechecked_under_the_role_lock(
    client: TestClient, db_engine: Engine, announcements: list[str]
) -> None:
    """F-3b."""
    token = _current_token(client, db_engine, announcements)
    role = _ok(
        client.post(
            "/api/roles",
            json={
                "name": f"Setup {_suffix()}",
                "permissions": ["MANAGE_USERS_AND_ROLES", "MANAGE_CORRECTION_PERMISSIONS"],
            },
        ),
        201,
    )
    before = _counts(db_engine)
    results: list[Any] = []
    with db_engine.connect() as holder:
        holder.begin()
        holder.execute(
            sa.text("SELECT 1 FROM roles WHERE id = :id FOR NO KEY UPDATE"), {"id": role["id"]}
        )
        holder.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE role_id = :id"
                " AND permission = 'MANAGE_CORRECTION_PERMISSIONS'"
            ),
            {"id": role["id"]},
        )
        thread = threading.Thread(
            target=lambda: results.append(_create(client, _body(token, role["id"])))
        )
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()
        holder.commit()
    thread.join(timeout=30)
    assert len(results) == 1
    _refused(
        results[0],
        422,
        f"Role {role['id']} cannot administer PartFlow. Choose a role that may manage users"
        " and roles and correction permissions.",
    )
    assert _counts(db_engine) == before
    assert _ok(client.get("/api/setup"))["open"] is True
    _ok(
        client.patch(
            f"/api/roles/{role['id']}",
            json={"grant_permissions": ["MANAGE_CORRECTION_PERMISSIONS"]},
        )
    )
    _ok(_create(client, _body(token, role["id"])), 201)


def test_setup_refusals_write_nothing_and_keep_the_token(
    client: TestClient, db_engine: Engine, announcements: list[str]
) -> None:
    """F-4."""
    token = _current_token(client, db_engine, announcements)
    role_id = _role_id(client, "Administrator")
    lacking = _ok(
        client.post(
            "/api/roles",
            json={"name": f"Lacking {_suffix()}", "permissions": ["MANAGE_USERS_AND_ROLES"]},
        ),
        201,
    )
    holder = _ok(
        client.post(
            "/api/users",
            json={
                "login_name": f"taken-{_suffix()}",
                "display_name": "Taken Name",
                "role_id": lacking["id"],
            },
        ),
        201,
    )
    before = _counts(db_engine)
    cases: list[tuple[dict[str, object], dict[str, str], int, str | None]] = [
        (
            _body(token, lacking["id"]),
            _CSRF,
            422,
            f"Role {lacking['id']} cannot administer PartFlow. Choose a role that may manage"
            " users and roles and correction permissions.",
        ),
        (_body(token, 2**31 - 1), _CSRF, 422, f"Role {2**31 - 1} does not exist."),
        (_body(token, str(role_id)), _CSRF, 422, None),
        (_body(token, role_id, password="x" * 11), _CSRF, 422, _P1),
        (
            _body(token, role_id, login_name=holder["login_name"]),
            _CSRF,
            409,
            "This login name is already used by Taken Name.",
        ),
        (_body(token, role_id, display_name="  "), _CSRF, 422, "Name must not be empty."),
        (_body(token, role_id, extra=True), _CSRF, 422, None),
        (_body(token, role_id), {}, 403, _A4),
    ]
    for body, headers, status, detail in cases:
        _refused(_create(client, body, headers), status, detail)
    assert _counts(db_engine) == before
    assert _ok(client.get("/api/setup"))["open"] is True
    # The gate was never rotated: the same token still works.
    _ok(_create(client, _body(token, role_id)), 201)


def test_setup_reopens_when_no_administrator_remains(
    client: TestClient, db_engine: Engine, announcements: list[str]
) -> None:
    """F-5."""
    token = _current_token(client, db_engine, announcements)
    created = _ok(_create(client, _body(token, _role_id(client, "Administrator"))), 201)
    assert _ok(client.get("/api/setup"))["open"] is False
    mark = len(announcements)
    _ok(client.patch(f"/api/users/{created['user']['id']}", json={"is_active": False}))
    assert _ok(client.get("/api/setup"))["open"] is True
    assert _ok(client.get("/api/session"))["setup_open"] is True
    assert len(announcements) == mark + 1
    rotated = _last_token(announcements)
    assert rotated != token
    _ok(_create(client, _body(rotated, _role_id(client, "Administrator"))), 201)


def test_startup_never_fails_over_the_setup_check(caplog: pytest.LogCaptureFixture) -> None:
    """F-6."""
    caplog.set_level(logging.WARNING, logger="app.first_run")
    unreachable = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    try:
        first_run.announce_if_open(unreachable, first_run.SetupGate())
    finally:
        unreachable.dispose()
    assert [record.getMessage() for record in caplog.records] == [_FALLBACK]
