"""Integration tests for Phase 14 slice 1 — sign-in for application Users.

Exercises the full request path — the CSRF middleware, FastAPI routes,
the authentication services and PostgreSQL — against a dedicated
temporary database migrated to head by the real Alembic chain
(IMPLEMENTATION_ROADMAP Phase 14; owner decisions OD-P1–OD-P3, OD-P17):

- sign-in: the strict cookie, only the token digest stored, one generic
  refusal for every failure, the lockout (threshold, duration, a lowered
  threshold, concurrent failures), the derived expiry and the re-issued
  cookie lifetime, sign-out, session fixation and replacement;
- passwords: the own change (wrong, same, short, locked, success, the
  lost-response retry), the administrator set (ends every sign-in,
  clears the lock, audited with the actor), the forced change and its
  policy switch, the rehash of older parameters;
- deactivation ends every sign-in and reactivation revives none; a role
  or grant change applies at the next request;
- the CSRF header rule, the sign-in policy section, the per-caller
  ``sign_in_state`` shape, the hashing load bound and that no secret is
  ever logged;
- lock order and races: a concurrent password set, a login rename and a
  concurrent deactivation, never a deadlock.

The client never stores cookies: every case passes its session cookie
explicitly, so a sign-in never leaks into another request. Every case
creates its own roles and users; policy values changed by a case are
restored in ``finally``. Helpers are copied locally (each module owns its
own); the module database is dropped afterwards.
"""

import base64
import datetime
import hashlib
import http.cookiejar
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.cookies import Morsel, SimpleCookie
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import password_hashing
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_authentication_api"
_COOKIE = "partflow_session"
_CSRF = {"X-PartFlow-CSRF": "1"}
_PASSWORD = "correct-horse-battery-staple"
_NEW_PASSWORD = "another-long-password-42"
_WRONG = "definitely-not-the-password"
_DAY = 86_400
_NEVER = 34_560_000

_A1 = "You are not signed in, or your sign-in has ended. Sign in to continue."
_A2 = "Your account does not have permission to do this."
_A3 = "Choose a new password before you continue."
_A4 = (
    "This request was refused because it did not come from the PartFlow application."
    " Reload the page and try again."
)
_A5 = (
    "Sign-in failed. Check your login name and password. If it keeps failing, ask an"
    " administrator — the account may be locked or inactive."
)
_P1 = "A password must be at least 12 characters long."
_P3 = "The current password is not correct."
_P4 = "Choose a new password that is different from the current one."
_P5 = "Use Change password to change your own password."
_P8 = "A password can contain only valid text characters."
_LONE_SURROGATE = "\ud800" + "x" * 13
_P7 = (
    "This account is locked after too many failed attempts. Try again later or ask an"
    " administrator."
)
_B1 = "PartFlow is busy checking other passwords. Try again in a moment."
_G0 = "Change at least one user sign-in setting."
_G1 = "User sign-ins must expire after a whole number of days from 1 to 365."
_G2 = "The number of failed sign-ins before a lock must be a whole number from 3 to 100."
_G3 = "The lock duration must be a whole number of minutes from 1 to 1440."
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
_POLICY_FIELDS = (
    "user_session_expires",
    "user_session_days",
    "sign_in_lockout_attempts",
    "sign_in_lockout_minutes",
    "require_password_change",
)


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    """The test client keeps no cookie: every case sends its own explicitly."""

    def set_ok(self, cookie: http.cookiejar.Cookie, request: Any) -> bool:
        return False


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
            test_client.cookies.jar.set_policy(_NoCookies())
            yield test_client
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


class Account(NamedTuple):
    user_id: int
    login: str
    role_id: int


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _listed(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


def _refused(response: Any, status: int, detail: str, flag: str | None = None) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = cast(dict[str, Any], response.json())
    assert body["detail"] == detail
    if flag is not None:
        assert body[flag] is True
    return body


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _execute(engine: Engine, sql: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(sql), params)


def _store_credential(
    engine: Engine,
    user_id: int,
    password: str,
    *,
    temporary: bool = False,
    stored_hash: str | None = None,
) -> None:
    _execute(
        engine,
        "INSERT INTO user_credentials (user_id, password_hash, password_is_temporary,"
        " password_changed_at) VALUES (:user_id, :hash, :temporary, now())",
        user_id=user_id,
        hash=stored_hash or password_hashing.hash_password(password),
        temporary=temporary,
    )


def _make_user(
    client: TestClient,
    engine: Engine,
    role_keys: list[str],
    password: str | None = None,
    temporary: bool = False,
) -> Account:
    """A role (unique name) and a User through the S12 routes; a credential
    row directly when asked."""
    suffix = _suffix()
    role = _ok(
        client.post("/api/roles", json={"name": f"Role {suffix}", "permissions": role_keys}), 201
    )
    user = _ok(
        client.post(
            "/api/users",
            json={
                "login_name": f"user-{suffix}",
                "display_name": f"User {suffix}",
                "role_id": role["id"],
            },
        ),
        201,
    )
    if password is not None:
        _store_credential(engine, int(user["id"]), password, temporary=temporary)
    return Account(user_id=int(user["id"]), login=str(user["login_name"]), role_id=int(role["id"]))


def _cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"{_COOKIE}={token}"}


def _auth(token: str) -> dict[str, str]:
    return {**_cookie_header(token), **_CSRF}


def _morsel(response: Any) -> Morsel[str] | None:
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        if _COOKIE in jar:
            return jar[_COOKIE]
    return None


def _token(response: Any) -> str:
    morsel = _morsel(response)
    assert morsel is not None and morsel.value
    return morsel.value


def _sign_in(client: TestClient, login: object, password: str, cookie: str | None = None) -> Any:
    headers = dict(_CSRF)
    if cookie is not None:
        headers.update(_cookie_header(cookie))
    return client.post(
        "/api/session", json={"login_name": login, "password": password}, headers=headers
    )


def _signed_in(client: TestClient, account: Account, password: str = _PASSWORD) -> str:
    response = _sign_in(client, account.login, password)
    assert response.status_code == 200, response.text
    return _token(response)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _credential(engine: Engine, user_id: int) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT xmin::text AS row_xmin, * FROM user_credentials WHERE user_id = :id"),
            {"id": user_id},
        ).one()
    return dict(row._mapping)


def _sessions(engine: Engine, user_id: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("SELECT * FROM user_sessions WHERE user_id = :id ORDER BY id"),
            {"id": user_id},
        )
        return [dict(row._mapping) for row in rows]


def _session_of(engine: Engine, token: str) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT * FROM user_sessions WHERE token_digest = :digest"),
            {"digest": _digest(token)},
        ).one()
    return dict(row._mapping)


def _user_audit(engine: Engine, user_id: int) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT * FROM audit_events WHERE entity_type = 'User' AND entity_id = :id"
                " ORDER BY id"
            ),
            {"id": str(user_id)},
        )
        return [dict(row._mapping) for row in rows]


def _audit_count(engine: Engine) -> int:
    return int(_scalar(engine, "SELECT count(*) FROM audit_events"))


@contextmanager
def _policy(engine: Engine, **values: object) -> Iterator[None]:
    """Set sign-in policy values directly; restore the previous ones afterwards."""
    with engine.connect() as connection:
        previous = dict(
            connection.execute(
                sa.text(f"SELECT {', '.join(_POLICY_FIELDS)} FROM application_policy")
            )
            .mappings()
            .one()
        )
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    _execute(engine, f"UPDATE application_policy SET {assignments}", **values)
    try:
        yield
    finally:
        restore = ", ".join(f"{name} = :{name}" for name in _POLICY_FIELDS)
        _execute(engine, f"UPDATE application_policy SET {restore}", **previous)


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


def _change_own(client: TestClient, token: str, current: str, new: str) -> Any:
    return client.put(
        "/api/session/password",
        json={"current_password": current, "new_password": new},
        headers=_auth(token),
    )


def _set_password(client: TestClient, token: str | None, user_id: int, new: str) -> Any:
    headers = _auth(token) if token is not None else dict(_CSRF)
    return client.put(f"/api/users/{user_id}/password", json={"new_password": new}, headers=headers)


# ---------------------------------------------------------------------------
# Sign in
# ---------------------------------------------------------------------------


def test_sign_in_sets_a_strict_cookie_and_stores_only_the_digest(
    client: TestClient, db_engine: Engine
) -> None:
    """A-1."""
    account = _make_user(client, db_engine, ["MANAGE_WORKERS", "MANAGE_AREAS"], password=_PASSWORD)
    response = _sign_in(client, account.login.upper(), _PASSWORD)
    body = _ok(response)
    assert body["setup_open"] in (True, False)
    user = body["user"]
    assert (user["id"], user["login_name"], user["role_id"]) == (
        account.user_id,
        account.login,
        account.role_id,
    )
    assert user["permissions"] == ["MANAGE_AREAS", "MANAGE_WORKERS"]
    assert user["must_change_password"] is False
    assert response.headers["cache-control"] == "no-store"

    morsel = _morsel(response)
    assert morsel is not None
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/api"
    assert not morsel["secure"]
    assert abs(int(morsel["max-age"]) - 30 * _DAY) <= 5
    token = morsel.value

    sessions = _sessions(db_engine, account.user_id)
    assert len(sessions) == 1 and sessions[0]["ended_at"] is None
    assert bytes(sessions[0]["token_digest"]) == _digest(token)
    expires = datetime.datetime.fromisoformat(user["session_expires_at"])
    assert abs(expires - (sessions[0]["created_at"] + datetime.timedelta(days=30))) < (
        datetime.timedelta(seconds=1)
    )
    assert (
        _scalar(
            db_engine,
            "SELECT count(*) FROM (SELECT t::text AS v FROM user_sessions t"
            " UNION ALL SELECT t::text FROM user_credentials t"
            " UNION ALL SELECT t::text FROM audit_events t"
            " UNION ALL SELECT t::text FROM users t) dump WHERE position(:token in v) > 0",
            token=token,
        )
        == 0
    )

    again = client.get("/api/session", headers=_cookie_header(token))
    state = _ok(again)
    assert state["user"] == user
    assert again.headers["cache-control"] == "no-store"
    reissued = _morsel(again)
    assert reissued is not None and reissued.value == token
    assert abs(int(reissued["max-age"]) - 30 * _DAY) <= 5


def test_every_sign_in_failure_answers_the_same(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A-2."""
    known = _make_user(client, db_engine, [], password=_PASSWORD)
    inactive = _make_user(client, db_engine, [], password=_PASSWORD)
    _ok(client.patch(f"/api/users/{inactive.user_id}", json={"is_active": False}))
    locked = _make_user(client, db_engine, [], password=_PASSWORD)
    _execute(
        db_engine,
        "UPDATE user_credentials SET locked_until = now() + interval '1 hour' WHERE user_id = :id",
        id=locked.user_id,
    )
    calls = {"dummy": 0, "verify": 0}
    dummy, verify = password_hashing.dummy_verify, password_hashing.verify_password

    def counting_dummy(password: str) -> None:
        calls["dummy"] += 1
        dummy(password)

    def counting_verify(password: str, stored: str) -> bool:
        calls["verify"] += 1
        return verify(password, stored)

    monkeypatch.setattr(password_hashing, "dummy_verify", counting_dummy)
    monkeypatch.setattr(password_hashing, "verify_password", counting_verify)

    expected = {"detail": _A5, "sign_in_failed": True}
    over_long = "x" * 257
    cases: list[tuple[str, object, str, bool]] = [
        ("wrong password", known.login, _WRONG, False),
        ("unknown login", f"nobody-{_suffix()}", _PASSWORD, True),
        ("invalid login", "a b", _PASSWORD, True),
        ("inactive user", inactive.login, _PASSWORD, False),
        ("locked user", locked.login, _PASSWORD, False),
        ("over-long, known", known.login, over_long, True),
        ("over-long, unknown", f"nobody-{_suffix()}", over_long, True),
    ]
    for label, login, password, dummy_only in cases:
        calls.update(dummy=0, verify=0)
        before = _credential(db_engine, known.user_id)
        response = _sign_in(client, login, password)
        assert response.status_code == 401, label
        assert response.json() == expected, label
        if dummy_only:
            assert calls == {"dummy": 1, "verify": 0}, label
        if label == "over-long, known":
            assert _credential(db_engine, known.user_id) == before
    assert _sessions(db_engine, known.user_id) == []
    assert _sessions(db_engine, locked.user_id) == []

    before = _credential(db_engine, known.user_id)
    response = _sign_in(client, known.login, "x" * 1025)
    assert response.status_code == 422, response.text
    assert _credential(db_engine, known.user_id) == before
    assert _sessions(db_engine, known.user_id) == []


def test_lockout_threshold_duration_and_reset(client: TestClient, db_engine: Engine) -> None:
    """A-3."""
    with _policy(db_engine, sign_in_lockout_attempts=3, sign_in_lockout_minutes=1):
        account = _make_user(client, db_engine, [], password=_PASSWORD)
        for _ in range(2):
            _refused(_sign_in(client, account.login, _WRONG), 401, _A5, "sign_in_failed")
        assert _credential(db_engine, account.user_id)["failed_attempts"] == 2
        _refused(_sign_in(client, account.login, _WRONG), 401, _A5)
        locked = _credential(db_engine, account.user_id)
        assert locked["failed_attempts"] == 0
        assert locked["locked_until"] is not None
        lock_span = locked["locked_until"] - _scalar(db_engine, "SELECT now()")
        assert datetime.timedelta(seconds=30) < lock_span <= datetime.timedelta(minutes=1)

        # Right password while locked: refused, not counted, lock not extended.
        _refused(_sign_in(client, account.login, _PASSWORD), 401, _A5)
        still = _credential(db_engine, account.user_id)
        assert (still["failed_attempts"], still["locked_until"]) == (0, locked["locked_until"])
        assert _sessions(db_engine, account.user_id) == []

        _execute(
            db_engine,
            "UPDATE user_credentials SET locked_until = now() - interval '1 minute'"
            " WHERE user_id = :id",
            id=account.user_id,
        )
        _ok(_sign_in(client, account.login, _PASSWORD))
        cleared = _credential(db_engine, account.user_id)
        assert (cleared["failed_attempts"], cleared["locked_until"]) == (0, None)

        # A success resets the counter.
        for _ in range(2):
            _sign_in(client, account.login, _WRONG)
        _ok(_sign_in(client, account.login, _PASSWORD))
        for _ in range(2):
            _sign_in(client, account.login, _WRONG)
        after = _credential(db_engine, account.user_id)
        assert (after["failed_attempts"], after["locked_until"]) == (2, None)

    # A lowered threshold locks at the next failure (>=).
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    with _policy(db_engine, sign_in_lockout_attempts=10):
        for _ in range(4):
            _sign_in(client, account.login, _WRONG)
        assert _credential(db_engine, account.user_id)["failed_attempts"] == 4
        with _policy(db_engine, sign_in_lockout_attempts=3):
            _sign_in(client, account.login, _WRONG)
            lowered = _credential(db_engine, account.user_id)
            assert lowered["failed_attempts"] == 0
            assert lowered["locked_until"] is not None


def test_concurrent_failures_are_counted_one_at_a_time(
    client: TestClient, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """A-4."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    barrier = threading.Barrier(6)
    responses: list[Any] = []

    def attempt() -> None:
        barrier.wait()
        responses.append(_sign_in(client, account.login, _WRONG))

    caplog.set_level(logging.INFO, logger="app.application.authentication")
    with _policy(db_engine, sign_in_lockout_attempts=3):
        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    assert [response.status_code for response in responses] == [401] * 6
    credential = _credential(db_engine, account.user_id)
    assert credential["failed_attempts"] == 0
    assert credential["locked_until"] is not None
    locks = [
        record
        for record in caplog.records
        if record.getMessage().startswith(f"Sign-in locked for user {account.user_id} ")
    ]
    assert len(locks) == 1


def test_expiry_is_derived_from_the_current_policy(client: TestClient, db_engine: Engine) -> None:
    """A-5."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    token = _signed_in(client, account)
    _execute(
        db_engine,
        "UPDATE user_sessions SET created_at = now() - interval '31 days'"
        " WHERE token_digest = :digest",
        digest=_digest(token),
    )
    expired = client.get("/api/session", headers=_cookie_header(token))
    assert _ok(expired)["user"] is None
    cleared = _morsel(expired)
    assert cleared is not None and cleared.value == "" and int(cleared["max-age"]) == 0
    _refused(
        _change_own(client, token, _PASSWORD, _NEW_PASSWORD), 401, _A1, "authentication_required"
    )

    with _policy(db_engine, user_session_days=60):
        valid = client.get("/api/session", headers=_cookie_header(token))
        assert _ok(valid)["user"]["id"] == account.user_id
        morsel = _morsel(valid)
        assert morsel is not None and morsel.value == token
        assert abs(int(morsel["max-age"]) - 29 * _DAY) <= 60
    with _policy(db_engine, user_session_expires=False):
        never = client.get("/api/session", headers=_cookie_header(token))
        assert _ok(never)["user"]["session_expires_at"] is None
        morsel = _morsel(never)
        assert morsel is not None and int(morsel["max-age"]) == _NEVER
    assert _session_of(db_engine, token)["ended_at"] is None


def test_sign_out_ends_the_session_and_is_idempotent(client: TestClient, db_engine: Engine) -> None:
    """A-6."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    token = _signed_in(client, account)
    response = client.delete("/api/session", headers=_auth(token))
    assert response.status_code == 204, response.text
    cleared = _morsel(response)
    assert cleared is not None and int(cleared["max-age"]) == 0
    assert _session_of(db_engine, token)["end_reason"] == "SIGNED_OUT"
    again = client.delete("/api/session", headers=_auth(token))
    assert again.status_code == 204
    assert client.delete("/api/session", headers=_CSRF).status_code == 204
    assert _ok(client.get("/api/session", headers=_cookie_header(token)))["user"] is None


def test_a_presented_cookie_is_never_adopted(client: TestClient, db_engine: Engine) -> None:
    """A-7."""
    first = _make_user(client, db_engine, [], password=_PASSWORD)
    second = _make_user(client, db_engine, [], password=_PASSWORD)
    first_token = _signed_in(client, first)
    replacing = _sign_in(client, second.login, _PASSWORD, cookie=first_token)
    second_token = _token(replacing)
    assert second_token != first_token
    assert _session_of(db_engine, first_token)["end_reason"] == "REPLACED"
    assert _session_of(db_engine, second_token)["ended_at"] is None

    forged = "forged-" + _suffix()
    signed = _sign_in(client, second.login, _PASSWORD, cookie=forged)
    assert _token(signed) != forged
    assert (
        _scalar(
            db_engine,
            "SELECT count(*) FROM user_sessions WHERE token_digest = :digest",
            digest=_digest(forged),
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def test_own_password_change_refusals(client: TestClient, db_engine: Engine) -> None:
    """A-8: wrong (counted), same, short."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    token = _signed_in(client, account)
    _refused(_change_own(client, token, _WRONG, _NEW_PASSWORD), 422, _P3)
    assert _credential(db_engine, account.user_id)["failed_attempts"] == 1
    _refused(_change_own(client, token, _PASSWORD, _PASSWORD), 422, _P4)
    _refused(_change_own(client, token, _PASSWORD, "x" * 11), 422, _P1)
    assert _credential(db_engine, account.user_id)["failed_attempts"] == 1


def test_own_password_change_while_locked(client: TestClient, db_engine: Engine) -> None:
    """A-8: the lock refuses even the right password, revealing nothing."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    token = _signed_in(client, account)
    with _policy(db_engine, sign_in_lockout_attempts=3):
        for _ in range(3):
            _refused(_change_own(client, token, _WRONG, _NEW_PASSWORD), 422, _P3)
        locked = _credential(db_engine, account.user_id)
        assert locked["locked_until"] is not None
        audits = len(_user_audit(db_engine, account.user_id))
        _refused(_change_own(client, token, _PASSWORD, _NEW_PASSWORD), 409, _P7, "account_locked")
        assert _credential(db_engine, account.user_id) == locked
        assert len(_user_audit(db_engine, account.user_id)) == audits
    _execute(
        db_engine,
        "UPDATE user_credentials SET locked_until = now() - interval '1 minute'"
        " WHERE user_id = :id",
        id=account.user_id,
    )
    _ok(_change_own(client, token, _PASSWORD, _NEW_PASSWORD))


def test_own_password_change_rotates_every_session(client: TestClient, db_engine: Engine) -> None:
    """A-8: success, then the retry after a lost response."""
    account = _make_user(client, db_engine, [], password=_PASSWORD, temporary=True)
    token = _signed_in(client, account)
    others = [_signed_in(client, account), _signed_in(client, account)]
    response = _change_own(client, token, _PASSWORD, _NEW_PASSWORD)
    state = _ok(response)
    assert state["user"]["must_change_password"] is False
    new_token = _token(response)
    assert new_token not in (token, *others)
    for old in (token, *others):
        assert _session_of(db_engine, old)["end_reason"] == "PASSWORD_CHANGED"
    assert _session_of(db_engine, new_token)["ended_at"] is None
    assert _ok(client.get("/api/session", headers=_cookie_header(new_token)))["user"]["id"] == (
        account.user_id
    )
    credential = _credential(db_engine, account.user_id)
    assert credential["password_is_temporary"] is False
    assert password_hashing.verify_password(_NEW_PASSWORD, credential["password_hash"])

    audits = _user_audit(db_engine, account.user_id)
    assert audits[-1]["event_type"] == "UPDATED"
    assert audits[-1]["actor_user_id"] == account.user_id
    assert audits[-1]["metadata"] == {"password_change": "CHANGED_BY_USER"}
    assert audits[-1]["before_data"]["password_temporary"] is True
    assert audits[-1]["after_data"]["password_temporary"] is False
    rendered = str((audits[-1]["before_data"], audits[-1]["after_data"], audits[-1]["metadata"]))
    assert credential["password_hash"] not in rendered and "scrypt" not in rendered

    # The response was lost: the retry with the pre-change cookie writes nothing.
    count, before = _audit_count(db_engine), _credential(db_engine, account.user_id)
    _refused(_change_own(client, token, _PASSWORD, _NEW_PASSWORD), 401, _A1)
    assert _audit_count(db_engine) == count
    assert _credential(db_engine, account.user_id) == before


def test_administrator_sets_a_temporary_password(client: TestClient, db_engine: Engine) -> None:
    """A-9: success ends every sign-in (expired ones too) and clears the lock."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    target_token = _signed_in(client, target)
    old_token = "old-" + _suffix()
    _execute(
        db_engine,
        "INSERT INTO user_sessions (user_id, token_digest, created_at)"
        " VALUES (:user_id, :digest, now() - interval '31 days')",
        user_id=target.user_id,
        digest=_digest(old_token),
    )
    _execute(
        db_engine,
        "UPDATE user_credentials SET locked_until = now() + interval '1 hour', failed_attempts = 2"
        " WHERE user_id = :id",
        id=target.user_id,
    )
    body = _ok(_set_password(client, admin_token, target.user_id, _NEW_PASSWORD))
    assert set(body) == _USER_KEYS | {"sign_in_state"}
    assert body["sign_in_state"] == "TEMPORARY_PASSWORD"
    reasons = {row["end_reason"] for row in _sessions(db_engine, target.user_id)}
    assert reasons == {"PASSWORD_RESET"}
    credential = _credential(db_engine, target.user_id)
    assert (credential["failed_attempts"], credential["locked_until"]) == (0, None)
    assert credential["password_is_temporary"] is True
    with _policy(db_engine, user_session_expires=False):
        for token in (old_token, target_token):
            assert _ok(client.get("/api/session", headers=_cookie_header(token)))["user"] is None
    audit = _user_audit(db_engine, target.user_id)[-1]
    assert audit["actor_user_id"] == admin.user_id
    assert audit["metadata"] == {"password_change": "SET_BY_ADMINISTRATOR", "lock_cleared": True}
    assert audit["before_data"]["password_set"] is True
    assert audit["after_data"]["password_temporary"] is True

    # A first password for a User without one.
    fresh = _make_user(client, db_engine, [])
    body = _ok(_set_password(client, admin_token, fresh.user_id, _NEW_PASSWORD))
    assert body["sign_in_state"] == "TEMPORARY_PASSWORD"
    first = _user_audit(db_engine, fresh.user_id)[-1]
    assert first["before_data"] == {
        "password_set": False,
        "password_temporary": False,
        "password_changed_at": None,
    }
    assert first["metadata"]["lock_cleared"] is False


def test_administrator_password_set_refusals_write_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    """A-9: anonymous, without the key, on oneself, unknown id."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)
    plain = _make_user(client, db_engine, ["MANAGE_WORKERS"], password=_PASSWORD)
    plain_token = _signed_in(client, plain)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    before, count = _credential(db_engine, target.user_id), _audit_count(db_engine)

    _refused(
        _set_password(client, None, target.user_id, _NEW_PASSWORD),
        401,
        _A1,
        "authentication_required",
    )
    denied = _refused(
        _set_password(client, plain_token, target.user_id, _NEW_PASSWORD),
        403,
        _A2,
        "permission_denied",
    )
    assert denied["required_permissions"] == ["MANAGE_USERS_AND_ROLES"]
    _refused(_set_password(client, admin_token, admin.user_id, _NEW_PASSWORD), 409, _P5)
    missing = 2**31 - 1
    _refused(
        _set_password(client, admin_token, missing, _NEW_PASSWORD),
        404,
        f"User {missing} does not exist.",
    )
    _refused(_set_password(client, admin_token, target.user_id, "x" * 11), 422, _P1)
    assert _credential(db_engine, target.user_id) == before
    assert _audit_count(db_engine) == count


def test_sign_in_state_is_shown_only_to_user_administrators(
    client: TestClient, db_engine: Engine
) -> None:
    """A-9: response shapes."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)
    plain = _make_user(client, db_engine, ["MANAGE_WORKERS"], password=_PASSWORD)
    plain_token = _signed_in(client, plain)
    no_password = _make_user(client, db_engine, [])
    temporary = _make_user(client, db_engine, [], password=_PASSWORD, temporary=True)
    locked = _make_user(client, db_engine, [], password=_PASSWORD)
    _execute(
        db_engine,
        "UPDATE user_credentials SET locked_until = now() + interval '1 hour' WHERE user_id = :id",
        id=locked.user_id,
    )

    for headers in ({}, _cookie_header(plain_token)):
        listed = _listed(client.get("/api/users", headers=headers))
        assert listed and all(set(item) == _USER_KEYS for item in listed)
        assert all(item["avatar_updated_at"] is None for item in listed)
    listed = _listed(client.get("/api/users", headers=_cookie_header(admin_token)))
    assert all(set(item) == _USER_KEYS | {"sign_in_state"} for item in listed)
    states = {item["id"]: item["sign_in_state"] for item in listed}
    assert states[no_password.user_id] == "NO_PASSWORD"
    assert states[temporary.user_id] == "TEMPORARY_PASSWORD"
    assert states[plain.user_id] == "PASSWORD_SET"
    assert states[locked.user_id] == "LOCKED"
    assert all(item["avatar_updated_at"] is None for item in listed if item["id"] == locked.user_id)

    # The S12 writes answer in the caller's shape too.
    patched = _ok(
        client.patch(
            f"/api/users/{no_password.user_id}",
            json={"display_name": "Renamed " + _suffix()},
            headers=_auth(admin_token),
        )
    )
    assert patched["sign_in_state"] == "NO_PASSWORD"
    anonymous = _ok(
        client.patch(f"/api/users/{no_password.user_id}", json={"display_name": "Anon"})
    )
    assert set(anonymous) == _USER_KEYS


def test_forced_change_follows_the_policy(client: TestClient, db_engine: Engine) -> None:
    """A-10."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    target = _make_user(
        client,
        db_engine,
        ["MANAGE_USERS_AND_ROLES", "CONFIGURE_SYSTEM_SETTINGS"],
        password=_PASSWORD,
        temporary=True,
    )
    response = _sign_in(client, target.login, _PASSWORD)
    assert _ok(response)["user"]["must_change_password"] is True
    token = _token(response)
    _refused(
        _set_password(client, token, admin.user_id, _NEW_PASSWORD),
        403,
        _A3,
        "password_change_required",
    )
    _refused(
        client.get("/api/policies/sign-in", headers=_cookie_header(token)),
        403,
        _A3,
        "password_change_required",
    )
    _refused(
        client.put("/api/policies/sign-in", json={"user_session_days": 7}, headers=_auth(token)),
        403,
        _A3,
    )
    assert _ok(client.get("/api/session", headers=_cookie_header(token)))["user"]["id"] == (
        target.user_id
    )
    changed = _change_own(client, token, _PASSWORD, _NEW_PASSWORD)
    assert _ok(changed)["user"]["must_change_password"] is False
    assert client.delete("/api/session", headers=_auth(_token(changed))).status_code == 204

    other = _make_user(client, db_engine, [], password=_PASSWORD, temporary=True)
    with _policy(db_engine, require_password_change=False):
        assert _ok(_sign_in(client, other.login, _PASSWORD))["user"]["must_change_password"] is (
            False
        )


def test_older_parameters_are_rehashed_at_sign_in(client: TestClient, db_engine: Engine) -> None:
    """A-17."""
    account = _make_user(client, db_engine, [])
    salt = os.urandom(16)
    key = hashlib.scrypt(_PASSWORD.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    older = f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"
    _store_credential(db_engine, account.user_id, _PASSWORD, stored_hash=older)
    _refused(_sign_in(client, account.login, _WRONG), 401, _A5)
    assert _credential(db_engine, account.user_id)["password_hash"] == older
    _ok(_sign_in(client, account.login, _PASSWORD))
    upgraded = _credential(db_engine, account.user_id)["password_hash"]
    assert upgraded.startswith("scrypt$32768$8$1$")
    assert password_hashing.verify_password(_PASSWORD, upgraded)


# ---------------------------------------------------------------------------
# Deactivation, roles, CSRF, policy
# ---------------------------------------------------------------------------


def test_deactivation_ends_every_sign_in(client: TestClient, db_engine: Engine) -> None:
    """A-11."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    tokens = [_signed_in(client, account), _signed_in(client, account)]
    _ok(client.patch(f"/api/users/{account.user_id}", json={"is_active": False}))
    assert {row["end_reason"] for row in _sessions(db_engine, account.user_id)} == {
        "USER_DEACTIVATED"
    }
    _ok(client.patch(f"/api/users/{account.user_id}", json={"is_active": True}))
    for token in tokens:
        assert _ok(client.get("/api/session", headers=_cookie_header(token)))["user"] is None

    inactive = _make_user(client, db_engine, [], password=_PASSWORD)
    _ok(client.patch(f"/api/users/{inactive.user_id}", json={"is_active": False}))
    lingering = "lingering-" + _suffix()
    _execute(
        db_engine,
        "INSERT INTO user_sessions (user_id, token_digest) VALUES (:user_id, :digest)",
        user_id=inactive.user_id,
        digest=_digest(lingering),
    )
    assert _ok(client.get("/api/session", headers=_cookie_header(lingering)))["user"] is None


def test_role_and_grant_changes_apply_at_the_next_request(
    client: TestClient, db_engine: Engine
) -> None:
    """A-12."""
    account = _make_user(client, db_engine, ["MANAGE_WORKERS"], password=_PASSWORD)
    token = _signed_in(client, account)
    _ok(
        client.patch(
            f"/api/roles/{account.role_id}",
            json={
                "grant_permissions": ["EXPORT_REPORTS"],
                "revoke_permissions": ["MANAGE_WORKERS"],
            },
        )
    )
    state = _ok(client.get("/api/session", headers=_cookie_header(token)))
    assert state["user"]["permissions"] == ["EXPORT_REPORTS"]


def test_cookie_requests_need_the_csrf_header(client: TestClient, db_engine: Engine) -> None:
    """A-13."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    token = _signed_in(client, admin)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    before = _credential(db_engine, target.user_id)
    departments = int(_scalar(db_engine, "SELECT count(*) FROM departments"))
    cookie_only = _cookie_header(token)

    _refused(
        client.put(
            f"/api/users/{target.user_id}/password",
            json={"new_password": _NEW_PASSWORD},
            headers=cookie_only,
        ),
        403,
        _A4,
        "csrf_rejected",
    )
    _refused(client.delete("/api/session", headers=cookie_only), 403, _A4)
    _refused(
        client.post("/api/departments", json={"name": "Csrf " + _suffix()}, headers=cookie_only),
        403,
        _A4,
    )
    _refused(
        client.post("/api/departments", content=b'{"name": "Bare body"}', headers=cookie_only),
        403,
        _A4,
    )
    assert _credential(db_engine, target.user_id) == before
    assert _session_of(db_engine, token)["ended_at"] is None
    assert int(_scalar(db_engine, "SELECT count(*) FROM departments")) == departments

    _ok(
        client.post("/api/departments", json={"name": "Csrf " + _suffix()}, headers=_auth(token)),
        201,
    )
    _refused(
        client.post("/api/session", json={"login_name": admin.login, "password": _PASSWORD}),
        403,
        _A4,
    )
    # Anonymous requests without the cookie keep working unchanged.
    _ok(client.post("/api/departments", json={"name": "Anonymous " + _suffix()}), 201)


def test_sign_in_policy_section(client: TestClient, db_engine: Engine) -> None:
    """A-16."""
    plain = _make_user(client, db_engine, [], password=_PASSWORD)
    plain_token = _signed_in(client, plain)
    admin = _make_user(client, db_engine, ["CONFIGURE_SYSTEM_SETTINGS"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)

    _refused(client.get("/api/policies/sign-in"), 401, _A1)
    policy = _ok(client.get("/api/policies/sign-in", headers=_cookie_header(plain_token)))
    assert {name: policy[name] for name in _POLICY_FIELDS} == {
        "user_session_expires": True,
        "user_session_days": 30,
        "sign_in_lockout_attempts": 10,
        "sign_in_lockout_minutes": 15,
        "require_password_change": True,
    }
    denied = _refused(
        client.put(
            "/api/policies/sign-in", json={"user_session_days": 7}, headers=_auth(plain_token)
        ),
        403,
        _A2,
    )
    assert denied["required_permissions"] == ["CONFIGURE_SYSTEM_SETTINGS"]

    def audit_rows() -> list[dict[str, Any]]:
        with db_engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT * FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
                    " AND entity_id = 'sign-in' ORDER BY id"
                )
            )
            return [dict(row._mapping) for row in rows]

    def put(body: object) -> Any:
        return client.put("/api/policies/sign-in", json=body, headers=_auth(admin_token))

    try:
        start = len(audit_rows())
        changed = _ok(put({"user_session_days": 7}))
        assert changed["user_session_days"] == 7
        rows = audit_rows()
        assert len(rows) == start + 1
        assert rows[-1]["actor_user_id"] == admin.user_id
        assert rows[-1]["before_data"]["user_session_days"] == 30
        assert rows[-1]["after_data"]["user_session_days"] == 7
        assert set(rows[-1]["after_data"]) == set(_POLICY_FIELDS)
        _ok(put({"user_session_days": 7}))
        assert len(audit_rows()) == start + 1

        _refused(put({}), 422, _G0)
        for value in (0, 366):
            _refused(put({"user_session_days": value}), 422, _G1)
        for value in (2, 101):
            _refused(put({"sign_in_lockout_attempts": value}), 422, _G2)
        for value in (0, 1441):
            _refused(put({"sign_in_lockout_minutes": value}), 422, _G3)
        for body in (
            {"user_session_days": "7"},
            {"user_session_days": True},
            {"user_session_days": None},
            {"user_session_expires": None},
            {"user_session_expires": 1},
            {"unknown": 1},
        ):
            assert put(body).status_code == 422, body
        assert len(audit_rows()) == start + 1
    finally:
        _execute(db_engine, "UPDATE application_policy SET user_session_days = 30")


def test_no_secret_is_ever_logged_or_answered(
    client: TestClient, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """A-18."""
    caplog.set_level(logging.DEBUG)
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    signed = _sign_in(client, admin.login, _PASSWORD)
    admin_token = _token(signed)
    target_token = _signed_in(client, target)
    changed = _change_own(client, target_token, _PASSWORD, _NEW_PASSWORD)
    new_target_token = _token(changed)
    reset_password = "administrator-chosen-9"
    _ok(_set_password(client, admin_token, target.user_id, reset_password))
    password_like = "Correct-Horse-Battery-9"
    _refused(_sign_in(client, password_like, _PASSWORD), 401, _A5)
    _refused(_sign_in(client, target.login, _WRONG), 401, _A5)

    secrets_ = [
        _PASSWORD,
        _NEW_PASSWORD,
        reset_password,
        _WRONG,
        password_like,
        password_like.lower(),
        admin_token,
        target_token,
        new_target_token,
        _digest(admin_token).hex(),
        _digest(new_target_token).hex(),
        _credential(db_engine, admin.user_id)["password_hash"],
        _credential(db_engine, target.user_id)["password_hash"],
    ]
    logged = [f"{record.getMessage()} {record.args!r}" for record in caplog.records]
    for secret in secrets_:
        assert not [line for line in logged if secret in line], secret
    assert any(line.startswith("Sign-in refused for an unknown login name") for line in logged)
    assert any(line.startswith(f"Sign-in refused for user {target.user_id}") for line in logged)
    assert not [line for line in logged if target.login in line]

    for headers in ({}, _cookie_header(admin_token)):
        assert "password" not in client.get("/api/users", headers=headers).text
    columns = {
        str(name)
        for name in _scalar_list(
            db_engine,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'users'",
        )
    }
    assert not {name for name in columns if "password" in name or "credential" in name}


def _raw(
    client: TestClient, method: str, url: str, body: dict[str, Any], token: str | None = None
) -> Any:
    """Send ``body`` as ASCII-escaped JSON, so a lone surrogate travels as ``\\ud800``."""
    headers = {"Content-Type": "application/json", **(_auth(token) if token else _CSRF)}
    return client.request(method, url, content=json.dumps(body), headers=headers)


def test_a_lone_surrogate_password_is_refused_cleanly(
    client: TestClient, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Audit F1: refused as invalid input — never a 500, a count or a corrupt-hash alarm."""
    caplog.set_level(logging.DEBUG)
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    target_token = _signed_in(client, target)
    before, count = _credential(db_engine, target.user_id), _audit_count(db_engine)

    for login in (target.login, f"nobody-{_suffix()}"):
        body = {"login_name": login, "password": _LONE_SURROGATE}
        _refused(_raw(client, "POST", "/api/session", body), 401, _A5, "sign_in_failed")
    own = {"current_password": _PASSWORD, "new_password": _LONE_SURROGATE}
    _refused(_raw(client, "PUT", "/api/session/password", own, target_token), 422, _P8)
    url = f"/api/users/{target.user_id}/password"
    _refused(_raw(client, "PUT", url, {"new_password": _LONE_SURROGATE}, admin_token), 422, _P8)

    assert _credential(db_engine, target.user_id) == before
    assert _audit_count(db_engine) == count
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_validation_refusals_never_echo_a_secret(client: TestClient, db_engine: Engine) -> None:
    """Audit F2: a 422 body keeps type, loc and msg only — never the submitted value."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    target_token = _signed_in(client, target)
    over_long = "over-long-secret-" + "y" * 1100
    responses = [
        _sign_in(client, target.login, over_long),
        client.post("/api/session", json={"password": _PASSWORD}, headers=_CSRF),
        _change_own(client, target_token, _PASSWORD, over_long),
        client.put(
            "/api/session/password",
            json={"current_password": _PASSWORD},
            headers=_auth(target_token),
        ),
        _set_password(client, admin_token, target.user_id, over_long),
        client.put(
            f"/api/users/{target.user_id}/password",
            json={"new_password": _NEW_PASSWORD, "extra": _PASSWORD},
            headers=_auth(admin_token),
        ),
    ]
    for response in responses:
        assert response.status_code == 422, response.text
        assert "over-long-secret" not in response.text and _PASSWORD not in response.text
        detail = response.json()["detail"]
        assert detail and all(set(error) == {"type", "loc", "msg"} for error in detail)


def _scalar_list(engine: Engine, sql: str) -> list[Any]:
    with engine.connect() as connection:
        return list(connection.execute(sa.text(sql)).scalars())


def test_password_checks_are_bounded(
    client: TestClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A-19: a sign-in flood answers B1 but never stalls other requests."""
    accounts = [_make_user(client, db_engine, [], password=_PASSWORD) for _ in range(6)]

    def slow_derive(password: bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
        time.sleep(1)
        return b"\x00" * 32

    monkeypatch.setattr(password_hashing, "_derive", slow_derive)
    caplog.set_level(logging.INFO)
    logins = [account.login for account in accounts] + [f"nobody-{_suffix()}" for _ in range(6)]
    barrier = threading.Barrier(len(logins) + 1)
    results: dict[str, Any] = {}

    def attempt(login: str) -> None:
        barrier.wait()
        results[login] = _sign_in(client, login, _WRONG)

    threads = [threading.Thread(target=attempt, args=(login,)) for login in logins]
    for thread in threads:
        thread.start()
    barrier.wait()
    time.sleep(0.2)
    for path in ("/api/health", "/api/departments"):
        started = time.monotonic()
        assert client.get(path).status_code == 200, path
        assert time.monotonic() - started < 1.5, path
    for thread in threads:
        thread.join(timeout=60)

    statuses = [results[login].status_code for login in logins]
    assert set(statuses) <= {401, 503}, statuses
    busy = [login for login in logins if results[login].status_code == 503]
    assert len(busy) >= 4, statuses
    for login in busy:
        assert results[login].json() == {"detail": _B1, "password_check_busy": True}
    for account in accounts:
        credential = _credential(db_engine, account.user_id)
        expected = 0 if account.login in busy else 1
        assert credential["failed_attempts"] == expected, account.login
        assert _sessions(db_engine, account.user_id) == []
    assert not [record for record in caplog.records if "QueuePool" in record.getMessage()]


# ---------------------------------------------------------------------------
# Races and lock order
# ---------------------------------------------------------------------------


def test_a_concurrent_password_set_invalidates_the_verification(
    client: TestClient, db_engine: Engine
) -> None:
    """A-14."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    replacement = password_hashing.hash_password(_NEW_PASSWORD)
    with db_engine.connect() as holder:
        holder.begin()
        holder.execute(
            sa.text("SELECT 1 FROM user_credentials WHERE user_id = :id FOR NO KEY UPDATE"),
            {"id": account.user_id},
        )
        holder.execute(
            sa.text("UPDATE user_credentials SET password_hash = :hash WHERE user_id = :id"),
            {"hash": replacement, "id": account.user_id},
        )
        # The sign-in reads and verifies the committed (old) hash, then waits.
        thread, results = _start(lambda: _sign_in(client, account.login, _PASSWORD))
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)
    _refused(response, 401, _A5)
    assert _sessions(db_engine, account.user_id) == []
    assert _credential(db_engine, account.user_id)["failed_attempts"] == 0


def test_sign_in_waits_for_a_login_rename_without_deadlock(
    client: TestClient, db_engine: Engine
) -> None:
    """A-15."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    with db_engine.connect() as holder:
        holder.begin()
        holder.execute(
            sa.text("UPDATE users SET login_name = :login WHERE id = :id"),
            {"login": f"renamed-{_suffix()}", "id": account.user_id},
        )
        thread, results = _start(lambda: _sign_in(client, account.login, _PASSWORD))
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)
    assert _ok(response)["user"]["id"] == account.user_id


def _hold_deactivation(holder: sa.Connection, user_id: int) -> None:
    holder.execute(sa.text("SELECT 1 FROM users WHERE id = :id FOR NO KEY UPDATE"), {"id": user_id})
    holder.execute(
        sa.text("SELECT 1 FROM user_credentials WHERE user_id = :id FOR NO KEY UPDATE"),
        {"id": user_id},
    )
    holder.execute(sa.text("UPDATE users SET is_active = false WHERE id = :id"), {"id": user_id})
    holder.execute(
        sa.text(
            "UPDATE user_sessions SET ended_at = now(), end_reason = 'USER_DEACTIVATED'"
            " WHERE user_id = :id AND ended_at IS NULL"
        ),
        {"id": user_id},
    )


def test_a_concurrent_deactivation_refuses_the_sign_in(
    client: TestClient, db_engine: Engine
) -> None:
    """A-20 (a)."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    earlier = _signed_in(client, account)
    with db_engine.connect() as holder:
        holder.begin()
        _hold_deactivation(holder, account.user_id)
        thread, results = _start(lambda: _sign_in(client, account.login, _PASSWORD))
        _assert_blocked(thread)
        holder.commit()
    _refused(_finish(thread, results), 401, _A5)
    assert not [row for row in _sessions(db_engine, account.user_id) if row["ended_at"] is None]
    _ok(client.patch(f"/api/users/{account.user_id}", json={"is_active": True}))
    assert _ok(client.get("/api/session", headers=_cookie_header(earlier)))["user"] is None


def test_a_concurrent_deactivation_refuses_the_own_change(
    client: TestClient, db_engine: Engine
) -> None:
    """A-20 (b)."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    token = _signed_in(client, account)
    before = _credential(db_engine, account.user_id)["password_hash"]
    with db_engine.connect() as holder:
        holder.begin()
        _hold_deactivation(holder, account.user_id)
        thread, results = _start(lambda: _change_own(client, token, _PASSWORD, _NEW_PASSWORD))
        _assert_blocked(thread)
        holder.commit()
    _refused(_finish(thread, results), 401, _A1)
    assert _credential(db_engine, account.user_id)["password_hash"] == before


@pytest.mark.parametrize("revocation", ["deactivation", "grant_removal"])
def test_a_revocation_during_the_hashing_wait_refuses_the_password_set(
    client: TestClient, db_engine: Engine, revocation: str
) -> None:
    """Audit F4: the locked phase re-reads the actor the route checked before hashing."""
    admin = _make_user(client, db_engine, ["MANAGE_USERS_AND_ROLES"], password=_PASSWORD)
    admin_token = _signed_in(client, admin)
    target = _make_user(client, db_engine, [], password=_PASSWORD)
    target_token = _signed_in(client, target)
    before, count = _credential(db_engine, target.user_id), _audit_count(db_engine)
    with db_engine.connect() as holder:
        holder.begin()
        holder.execute(
            sa.text(
                "SELECT pg_advisory_xact_lock(hashtextextended('partflow:user-administration', 0))"
            )
        )
        thread, results = _start(
            lambda: _set_password(client, admin_token, target.user_id, _NEW_PASSWORD)
        )
        _assert_blocked(thread)
        if revocation == "deactivation":
            _hold_deactivation(holder, admin.user_id)
        else:
            holder.execute(
                sa.text("DELETE FROM role_permissions WHERE role_id = :id"), {"id": admin.role_id}
            )
        holder.commit()
    response = _finish(thread, results)
    if revocation == "deactivation":
        _refused(response, 401, _A1, "authentication_required")
    else:
        denied = _refused(response, 403, _A2, "permission_denied")
        assert denied["required_permissions"] == ["MANAGE_USERS_AND_ROLES"]
    assert _credential(db_engine, target.user_id) == before
    assert _audit_count(db_engine) == count
    assert _session_of(db_engine, target_token)["ended_at"] is None


@pytest.mark.parametrize("rename", [False, True])
def test_deactivation_waits_for_a_sign_in_in_progress(
    client: TestClient, db_engine: Engine, rename: bool
) -> None:
    """A-20 (c): the sign-in commits first; its session is then ended."""
    account = _make_user(client, db_engine, [], password=_PASSWORD)
    pending = "pending-" + _suffix()
    body: dict[str, Any] = {"is_active": False}
    if rename:
        body["login_name"] = f"moved-{_suffix()}"
    with db_engine.connect() as holder:
        holder.begin()
        holder.execute(
            sa.text("SELECT 1 FROM user_credentials WHERE user_id = :id FOR NO KEY UPDATE"),
            {"id": account.user_id},
        )
        holder.execute(
            sa.text("INSERT INTO user_sessions (user_id, token_digest) VALUES (:id, :digest)"),
            {"id": account.user_id, "digest": _digest(pending)},
        )
        thread, results = _start(lambda: client.patch(f"/api/users/{account.user_id}", json=body))
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)
    assert _ok(response)["is_active"] is False
    assert _session_of(db_engine, pending)["end_reason"] == "USER_DEACTIVATED"
