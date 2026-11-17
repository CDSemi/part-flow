"""Integration tests for passkeys (Phase 17, owner decision of 2026-11-13).

IMPLEMENTATION_ROADMAP Phase 17 item 8. Against a module database migrated
to head, through the real application, with a software authenticator
(``tests/soft_authenticator.py``) answering as a browser would:

- adding a passkey takes the password again (a wrong one is a counted
  failed sign-in), works only at an HTTPS address, verifies the browser's
  answer against the pending request of the same host within the setup
  time, turns two-step sign-in on and creates the recovery codes with the
  first factor; the audit rows carry no key material;
- a passkey signs its User in alone (the person verified), once per
  request; every refusal — a wrong host, an unverified person, a counter
  that did not grow, an inactive or locked User, an expired request — is
  one generic answer, and expired anonymous requests are pruned;
- a passkey answers the second step after the password (the person need
  not be verified there); a refused answer counts like a wrong code and
  the request works once;
- removing the app keeps two-step sign-in on while a passkey remains, and
  removing the last factor turns it off with its recovery codes; an
  administrator's reset deletes passkeys too.

The API commits real transactions; the module database is dropped
afterwards. The schema is covered by ``tests/test_phase17_passkeys_schema.py``.
"""

import base64
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from fido2.utils import websafe_encode
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import one_time_codes
from app.application.authentication import PASSKEY_SIGN_IN_FAILED_MESSAGE
from app.application.passkeys import NOT_AVAILABLE_MESSAGE
from app.application.two_step_factors import SIGN_IN_COOKIE
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    CSRF_HEADERS,
    TEST_PASSWORD,
    IdentityClient,
    admin_of,
    app_of,
    client_as,
)
from tests.soft_authenticator import SoftAuthenticator

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_passkeys_api"
_DB_URL_ENV = "DATABASE_URL"
_ORIGIN = "https://pf.example.com"
_HOST = "pf.example.com"
_AT = {"Origin": _ORIGIN}
_WRONG_PASSWORD = "The current password is not correct."
_WRONG_CODE = "That code is not correct. Check the code and try again."
_NO_PENDING = "This passkey request has expired or was not started. Start again with Add a passkey."
_REFUSED = (
    "The passkey could not be verified. Try again, and let your device or password manager save"
    " the passkey for this address."
)
_BAD_NAME = (
    "Name the passkey with 1 to 80 characters, without line breaks or other control characters."
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _refused(response: Any, status: int, detail: str, flag: str | None = None) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["detail"] == detail
    if flag is not None:
        assert body[flag] is True


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _execute(engine: Engine, sql: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(sql), params)


def _browser(client: TestClient, origin: str = _ORIGIN) -> TestClient:
    """A browser at ``origin`` with its own cookie jar and the CSRF header."""
    return TestClient(app_of(client), headers={**CSRF_HEADERS, "Origin": origin})


def _status(user: IdentityClient) -> dict[str, Any]:
    return _ok(user.get("/api/session/two-step"))


def _add_passkey(
    user: IdentityClient, soft: SoftAuthenticator, name: str = "Laptop"
) -> list[str] | None:
    options = _ok(
        user.post(
            "/api/session/two-step/passkeys/options",
            json={"current_password": TEST_PASSWORD},
            headers=_AT,
        )
    )["options"]
    answered = _ok(
        user.post(
            "/api/session/two-step/passkeys",
            json={"name": name, "credential": soft.create(options)},
            headers=_AT,
        )
    )
    return cast(list[str] | None, answered["recovery_codes"])


def _with_passkey(
    client: TestClient, *permissions: Permission
) -> tuple[IdentityClient, SoftAuthenticator]:
    user = client_as(client, *permissions)
    soft = SoftAuthenticator(_ORIGIN)
    _add_passkey(user, soft)
    return user, soft


def _login(user: IdentityClient) -> str:
    assert user.identity is not None
    return user.identity.login_name


def _db_step(engine: Engine) -> int:
    with engine.connect() as connection:
        now = connection.execute(sa.text("SELECT extract(epoch FROM now())")).scalar_one()
    return one_time_codes.totp_step(float(now))


def _add_app(user: IdentityClient, engine: Engine) -> bytes:
    setup = _ok(user.post("/api/session/two-step/totp", json={"current_password": TEST_PASSWORD}))
    secret = base64.b32decode(setup["secret"] + "=" * (-len(setup["secret"]) % 8))
    _ok(
        user.post(
            "/api/session/two-step/totp/confirm",
            json={"code": one_time_codes.hotp(secret, _db_step(engine))},
        )
    )
    return secret


def _changes(engine: Engine, user_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT before_data, after_data, metadata FROM audit_events"
                    " WHERE entity_type = 'User' AND entity_id = :u"
                    " AND metadata ? 'two_step_change' ORDER BY id"
                ),
                {"u": str(user_id)},
            )
        )


def _counters(engine: Engine, user_id: int) -> tuple[int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT failed_attempts, failed_codes FROM user_credentials WHERE user_id = :u"
            ),
            {"u": user_id},
        ).one()
    return int(row[0]), int(row[1])


# ---------------------------------------------------------------------------
# Adding a passkey
# ---------------------------------------------------------------------------


def test_adding_a_passkey_takes_the_password_and_turns_two_step_on(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    soft = SoftAuthenticator(_ORIGIN)
    assert _status(user) == {
        "enabled": False,
        "authenticator_app": False,
        "passkeys": [],
        "recovery_codes_left": 0,
        "email": None,
        "email_ready": False,
    }
    path = "/api/session/two-step/passkeys/options"
    _refused(
        user.post(path, json={"current_password": "wrong one"}, headers=_AT),
        422,
        _WRONG_PASSWORD,
    )
    assert _counters(db_engine, user.user_id) == (1, 0)
    # Only at an HTTPS address: never asked over plain HTTP or without an origin.
    for headers in ({"Origin": "http://pf.example.com"}, {}):
        _refused(
            user.post(path, json={"current_password": TEST_PASSWORD}, headers=headers),
            422,
            NOT_AVAILABLE_MESSAGE,
        )

    options = _ok(user.post(path, json={"current_password": TEST_PASSWORD}, headers=_AT))[
        "options"
    ]["publicKey"]
    assert options["rp"] == {"name": "PartFlow", "id": _HOST}
    assert options["user"]["id"] == websafe_encode(str(user.user_id).encode())
    assert options["user"]["name"] == _login(user)
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["excludeCredentials"] == []
    codes = _ok(
        user.post(
            "/api/session/two-step/passkeys",
            json={"name": "  Laptop  ", "credential": soft.create({"publicKey": options})},
            headers=_AT,
        )
    )["recovery_codes"]
    assert len(codes) == 10
    status = _status(user)
    assert (status["enabled"], status["authenticator_app"], status["recovery_codes_left"]) == (
        True,
        False,
        10,
    )
    [passkey] = status["passkeys"]
    assert (passkey["name"], passkey["rp_id"], passkey["last_used_at"]) == ("Laptop", _HOST, None)
    assert set(passkey) == {"id", "name", "rp_id", "created_at", "last_used_at"}

    # A second passkey: the first is excluded, and the codes are kept.
    second = _ok(user.post(path, json={"current_password": TEST_PASSWORD}, headers=_AT))["options"]
    assert len(second["publicKey"]["excludeCredentials"]) == 1
    assert _add_passkey(user, soft, "Phone") is None
    assert [p["name"] for p in _status(user)["passkeys"]] == ["Laptop", "Phone"]

    [first, _second] = _changes(db_engine, user.user_id)
    assert tuple(first) == (
        {"two_step": False, "authenticator_app": False, "passkeys": 0, "recovery_codes": 0},
        {"two_step": True, "authenticator_app": False, "passkeys": 1, "recovery_codes": 10},
        {"two_step_change": "PASSKEY_ADDED"},
    )
    stored = _scalar(
        db_engine,
        "SELECT encode(credential_data, 'hex') FROM user_passkeys WHERE user_id = :u"
        " ORDER BY id LIMIT 1",
        u=user.user_id,
    )
    assert stored not in str(tuple(first))
    # User administrators see the passkey-only User with two-step sign-in on.
    listed = {row["id"]: row for row in _ok_list(admin_of(client).get("/api/users"))}
    assert listed[user.user_id]["two_step"] is True


def _ok_list(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


def test_a_registration_is_refused_when_wrong_late_or_elsewhere(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    soft = SoftAuthenticator(_ORIGIN)
    add = "/api/session/two-step/passkeys"
    for name in ("", "  ", "x" * 81, "Lap\ntop"):
        _refused(user.post(add, json={"name": name, "credential": {}}, headers=_AT), 422, _BAD_NAME)
    _refused(
        user.post(add, json={"name": "Laptop", "credential": {}}, headers=_AT), 409, _NO_PENDING
    )

    def ask() -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _ok(
                user.post(
                    "/api/session/two-step/passkeys/options",
                    json={"current_password": TEST_PASSWORD},
                    headers=_AT,
                )
            )["options"],
        )

    # An answer that signs another challenge, or none at all, is refused; the
    # request stays open.
    options = ask()
    for credential in (soft.create(options, challenge="AAAA"), {"id": "x"}):
        _refused(
            user.post(add, json={"name": "Laptop", "credential": credential}, headers=_AT),
            422,
            _REFUSED,
        )
    # Asked at one host, answered at another.
    elsewhere = {"Origin": "https://other.example.com"}
    _refused(
        user.post(
            add, json={"name": "Laptop", "credential": soft.create(options)}, headers=elsewhere
        ),
        409,
        _NO_PENDING,
    )
    # Too late: the setup time is over.
    options = ask()
    _execute(
        db_engine,
        "UPDATE user_passkey_setups SET created_at = now() - interval '16 minutes'"
        " WHERE user_id = :u",
        u=user.user_id,
    )
    _refused(
        user.post(add, json={"name": "Laptop", "credential": soft.create(options)}, headers=_AT),
        409,
        _NO_PENDING,
    )
    assert _status(user)["passkeys"] == []
    assert (
        _scalar(db_engine, "SELECT count(*) FROM user_passkeys WHERE user_id = :u", u=user.user_id)
        == 0
    )


# ---------------------------------------------------------------------------
# Signing in with a passkey alone
# ---------------------------------------------------------------------------


def _passkey_options(browser: TestClient) -> dict[str, Any]:
    return cast(dict[str, Any], _ok(browser.post("/api/session/passkey/options"))["options"])


def test_a_passkey_signs_in_alone_once(client: TestClient, db_engine: Engine) -> None:
    user, soft = _with_passkey(client)
    _execute(
        db_engine,
        "UPDATE user_credentials SET failed_attempts = 3, failed_codes = 2 WHERE user_id = :u",
        u=user.user_id,
    )
    browser = _browser(client)
    options = _passkey_options(browser)
    assert SIGN_IN_COOKIE in browser.cookies
    public = options["publicKey"]
    # Any passkey at this host may answer: none is named.
    assert (public["rpId"], public.get("allowCredentials"), public["userVerification"]) == (
        _HOST,
        None,
        "required",
    )
    answer = soft.get(options)
    state = _ok(browser.post("/api/session/passkey", json={"credential": answer}))
    assert state["user"]["id"] == user.user_id
    assert "partflow_session" in browser.cookies
    assert SIGN_IN_COOKIE not in browser.cookies
    assert _ok(browser.get("/api/session"))["user"]["id"] == user.user_id
    # A full sign-in: both counters restart.
    assert _counters(db_engine, user.user_id) == (0, 0)
    with db_engine.connect() as connection:
        purpose, owner, reason = connection.execute(
            sa.text(
                "SELECT purpose, user_id, end_reason FROM user_sign_in_challenges"
                " WHERE purpose = 'PASSKEY_SIGN_IN' ORDER BY id DESC LIMIT 1"
            )
        ).one()
        used, count = connection.execute(
            sa.text("SELECT last_used_at, sign_count FROM user_passkeys WHERE user_id = :u"),
            {"u": user.user_id},
        ).one()
    assert (purpose, owner, reason) == ("PASSKEY_SIGN_IN", user.user_id, "COMPLETED")
    assert used is not None and count == 1

    # The same answer never works again.
    again = _browser(client)
    _passkey_options(again)
    _refused(
        again.post("/api/session/passkey", json={"credential": answer}),
        401,
        PASSKEY_SIGN_IN_FAILED_MESSAGE,
        "sign_in_failed",
    )


def test_a_passkey_sign_in_is_refused_generically(client: TestClient, db_engine: Engine) -> None:
    user, soft = _with_passkey(client)
    sign_in = "/api/session/passkey"

    def attempt(credential_of: Any, browser: TestClient | None = None) -> None:
        browser = browser or _browser(client)
        options = _passkey_options(browser)
        _refused(
            browser.post(sign_in, json={"credential": credential_of(options)}),
            401,
            PASSKEY_SIGN_IN_FAILED_MESSAGE,
            "sign_in_failed",
        )

    # The person must be verified, and a counter must grow.
    attempt(lambda options: soft.get(options, verified=False))
    _execute(
        db_engine, "UPDATE user_passkeys SET sign_count = 5 WHERE user_id = :u", u=user.user_id
    )
    attempt(lambda options: soft.get(options, counter=5))
    attempt(lambda options: {"id": "garbage"})
    # Answered for another request, or at another host.
    stale = soft.get(_passkey_options(_browser(client)))
    attempt(lambda options: stale)
    browser = _browser(client)
    answer = soft.get(_passkey_options(browser))
    elsewhere = _browser(client, "https://other.example.com")
    _passkey_options(elsewhere)
    _refused(
        elsewhere.post(sign_in, json={"credential": answer}),
        401,
        PASSKEY_SIGN_IN_FAILED_MESSAGE,
    )
    # An inactive or a locked User is refused the same way.
    _execute(db_engine, "UPDATE users SET is_active = false WHERE id = :u", u=user.user_id)
    attempt(lambda options: soft.get(options))
    _execute(db_engine, "UPDATE users SET is_active = true WHERE id = :u", u=user.user_id)
    _execute(
        db_engine,
        "UPDATE user_credentials SET locked_until = now() + interval '1 hour' WHERE user_id = :u",
        u=user.user_id,
    )
    attempt(lambda options: soft.get(options))
    _execute(
        db_engine,
        "UPDATE user_credentials SET locked_until = NULL WHERE user_id = :u",
        u=user.user_id,
    )
    # An expired request.
    browser = _browser(client)
    options = _passkey_options(browser)
    _execute(
        db_engine,
        "UPDATE user_sign_in_challenges SET created_at = now() - interval '16 minutes'"
        " WHERE purpose = 'PASSKEY_SIGN_IN' AND ended_at IS NULL",
    )
    _refused(
        browser.post(sign_in, json={"credential": soft.get(options)}),
        401,
        PASSKEY_SIGN_IN_FAILED_MESSAGE,
    )
    # Expired requests nobody answered are pruned by the next one.
    _passkey_options(_browser(client))
    assert (
        _scalar(
            db_engine,
            "SELECT count(*) FROM user_sign_in_challenges WHERE purpose = 'PASSKEY_SIGN_IN'"
            " AND user_id IS NULL AND created_at < now() - interval '15 minutes'",
        )
        == 0
    )
    # The request and the answer need the CSRF header, and HTTPS.
    bare = TestClient(app_of(client), headers={"Origin": _ORIGIN})
    assert bare.post("/api/session/passkey/options").status_code == 403
    _refused(
        _browser(client, "http://pf.example.com").post("/api/session/passkey/options"),
        422,
        NOT_AVAILABLE_MESSAGE,
    )
    # Still usable afterwards.
    browser = _browser(client)
    _ok(browser.post(sign_in, json={"credential": soft.get(_passkey_options(browser))}))


# ---------------------------------------------------------------------------
# The second step
# ---------------------------------------------------------------------------


def _password_step(browser: TestClient, login: str) -> dict[str, Any]:
    return _ok(
        browser.post("/api/session", json={"login_name": login, "password": TEST_PASSWORD}), 202
    )


def test_a_passkey_answers_the_second_step(client: TestClient, db_engine: Engine) -> None:
    user, soft = _with_passkey(client)
    login = _login(user)
    # Typed at another host (or with no origin): no passkey is offered.
    plain = TestClient(app_of(client), headers=CSRF_HEADERS)
    assert _password_step(plain, login)["methods"] == ["RECOVERY_CODE"]
    browser = _browser(client)
    assert _password_step(browser, login)["methods"] == ["PASSKEY", "RECOVERY_CODE"]
    # A passkey answers through its own route, never as a typed code.
    assert (
        browser.post(
            "/api/session/second-step", json={"method": "PASSKEY", "code": "x"}
        ).status_code
        == 422
    )

    options = _ok(browser.post("/api/session/second-step/passkey-options"))["options"]
    public = options["publicKey"]
    assert (public["rpId"], len(public["allowCredentials"]), public["userVerification"]) == (
        _HOST,
        1,
        "preferred",
    )
    # A refused answer counts like a wrong code, and the request works once.
    _refused(
        browser.post(
            "/api/session/second-step/passkey",
            json={"credential": soft.get(options, challenge="AAAA")},
        ),
        401,
        _WRONG_CODE,
        "second_step_failed",
    )
    assert _counters(db_engine, user.user_id) == (0, 1)
    _refused(
        browser.post("/api/session/second-step/passkey", json={"credential": soft.get(options)}),
        401,
        _WRONG_CODE,
        "second_step_failed",
    )
    options = _ok(browser.post("/api/session/second-step/passkey-options"))["options"]
    state = _ok(
        browser.post(
            "/api/session/second-step/passkey",
            json={"credential": soft.get(options, verified=False)},
        )
    )
    assert state["user"]["id"] == user.user_id
    assert SIGN_IN_COOKIE not in browser.cookies
    assert _counters(db_engine, user.user_id) == (0, 0)

    # Without a passkey made at this host, none is asked for.
    other = _browser(client, "https://other.example.com")
    assert _password_step(other, login)["methods"] == ["RECOVERY_CODE"]
    response = other.post("/api/session/second-step/passkey-options")
    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Removing factors
# ---------------------------------------------------------------------------


def test_two_step_stays_on_while_a_factor_is_left(client: TestClient, db_engine: Engine) -> None:
    user = client_as(client)
    soft = SoftAuthenticator(_ORIGIN)
    _add_app(user, db_engine)
    assert _add_passkey(user, soft) is None
    status = _status(user)
    assert (
        status["authenticator_app"],
        len(status["passkeys"]),
        status["recovery_codes_left"],
    ) == (
        True,
        1,
        10,
    )

    status = _ok(
        user.post("/api/session/two-step/totp/remove", json={"current_password": TEST_PASSWORD})
    )
    assert (status["enabled"], status["authenticator_app"], status["recovery_codes_left"]) == (
        True,
        False,
        10,
    )
    passkey_id = status["passkeys"][0]["id"]
    remove = f"/api/session/two-step/passkeys/{passkey_id}/remove"
    _refused(user.post(remove, json={"current_password": "nope"}), 422, _WRONG_PASSWORD)
    other = client_as(client)
    _refused(
        other.post(remove, json={"current_password": TEST_PASSWORD}),
        409,
        "That passkey does not exist any more.",
    )
    status = _ok(user.post(remove, json={"current_password": TEST_PASSWORD}))
    assert (status["enabled"], status["passkeys"], status["recovery_codes_left"]) == (False, [], 0)
    changes = [row.metadata["two_step_change"] for row in _changes(db_engine, user.user_id)]
    assert changes == ["TOTP_ADDED", "PASSKEY_ADDED", "TOTP_REMOVED", "PASSKEY_REMOVED"]
    [*_, last] = _changes(db_engine, user.user_id)
    assert (last.before_data, last.after_data) == (
        {"two_step": True, "authenticator_app": False, "passkeys": 1, "recovery_codes": 10},
        {"two_step": False, "authenticator_app": False, "passkeys": 0, "recovery_codes": 0},
    )


def test_an_administrator_reset_removes_passkeys_too(client: TestClient, db_engine: Engine) -> None:
    user, _soft = _with_passkey(client, Permission.VIEW_PRODUCTION_DATA)
    manager = client_as(client, Permission.MANAGE_USERS_AND_ROLES)
    answered = _ok(
        manager.post(f"/api/users/{user.user_id}/two-step/reset", json={"end_sessions": False})
    )
    assert answered["two_step"] is False
    assert (
        _scalar(db_engine, "SELECT count(*) FROM user_passkeys WHERE user_id = :u", u=user.user_id)
        == 0
    )
    [*_, reset] = _changes(db_engine, user.user_id)
    assert reset.before_data["passkeys"] == 1
    assert reset.metadata == {"two_step_change": "RESET_BY_ADMINISTRATOR", "sessions_ended": False}
