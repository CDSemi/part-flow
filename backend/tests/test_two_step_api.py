"""Integration tests for two-step sign-in (Phase 17, owner decisions of 2026-11-13 and -17).

IMPLEMENTATION_ROADMAP Phase 17 item 8. Against a module database migrated
to head, through the real application:

- setting up an authenticator app takes the password again (a wrong one
  is a counted failed sign-in) and the first code within the setup time;
  the first confirmation answers the recovery codes once; the audit rows
  carry no secret, code or digest;
- a password sign-in of such a User answers 202 with the second step and
  its own cookie, never a session; the app code, a recovery code or an
  emailed code (through the stand-in mail server) signs in, each once;
- two counters: a correct password restarts the password counter, wrong
  codes count apart until a full sign-in, the policy's wrong codes per
  sign-in end the step, its threshold locks the account, and an expired,
  replaced or reset step answers ``second_step_expired``;
- every number is Administration → Settings → Two-step sign-in
  (``/api/policies/two-step``): read by every signed-in User, changed and
  audited with Configure system settings, refused out of range, and
  applied from the next step;
- a User changes their own email address with the password; the previous
  address hears of it (best effort);
- turning the app off and creating new codes take the password; a user
  administrator (with the protected-user guard) or the recovery command
  turns another User's two-step sign-in off, ending their open second
  steps and, when asked, every sign-in;
- user administrators see ``two_step`` on every User.

The API commits real transactions; the module database is dropped
afterwards. The schema is covered by ``tests/test_phase17_two_step_schema.py``
and the codes themselves by ``tests/test_one_time_codes.py``.
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
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.application import one_time_codes
from app.application.two_step_factors import SIGN_IN_COOKIE
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests import fake_smtp
from tests.auth_harness import (
    CSRF_HEADERS,
    TEST_PASSWORD,
    IdentityClient,
    admin_of,
    app_of,
    client_as,
    create_identity,
)

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_two_step_api"
_DB_URL_ENV = "DATABASE_URL"
_WRONG_PASSWORD = "The current password is not correct."
_WRONG_CODE = "That code is not correct. Check the code and try again."
_TOO_MANY = "Too many wrong codes. Sign in again with your password."
_ENDED = "This sign-in has ended or expired. Enter your password again."
_POLICY_PATH = "/api/policies/two-step"
_POLICY_DEFAULTS = {
    "two_step_window_minutes": 15,
    "two_step_max_codes": 5,
    "two_step_lockout_codes": 10,
    "two_step_email_codes": 3,
    "two_step_email_interval_seconds": 60,
    "two_step_email_code_digits": 8,
    "two_step_recovery_codes": 10,
    "two_step_recovery_code_length": 12,
    "two_step_setup_minutes": 15,
    "two_step_clock_drift_steps": 1,
}
_MAIL_SERVER = {
    "mail_host": "smtp.example.com",
    "mail_port": 587,
    "mail_security": "STARTTLS",
    "mail_username": None,
    "mail_sender": "partflow@example.com",
    "mail_timeout_seconds": 20,
}
_NO_MAIL_SERVER = {**_MAIL_SERVER, "mail_host": None, "mail_sender": None}


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


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> fake_smtp.Recorder:
    return fake_smtp.install(monkeypatch)


@pytest.fixture
def mail_server(client: TestClient) -> Iterator[None]:
    """An outgoing mail server for one test (the stand-in answers)."""
    _ok(admin_of(client).put("/api/policies/mail-server", json=_MAIL_SERVER))
    yield
    _ok(admin_of(client).put("/api/policies/mail-server", json=_NO_MAIL_SERVER))


@pytest.fixture(autouse=True)
def default_policy(client: TestClient) -> Iterator[None]:
    """Every test leaves the two-step policy at its defaults."""
    yield
    _ok(admin_of(client).put(_POLICY_PATH, json=_POLICY_DEFAULTS))


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


def _list(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


def _with_cookie(client: TestClient, token: str, *, csrf: bool = True) -> TestClient:
    """A browser that presents ``token`` as its second-step cookie."""
    headers = {"Cookie": f"{SIGN_IN_COOKIE}={token}"}
    return TestClient(app_of(client), headers={**(CSRF_HEADERS if csrf else {}), **headers})


def _browser(client: TestClient) -> TestClient:
    """A browser with its own cookie jar: no sign-in yet, the CSRF header always."""
    return TestClient(app_of(client), headers=CSRF_HEADERS)


def _db_step(engine: Engine) -> int:
    with engine.connect() as connection:
        now = connection.execute(sa.text("SELECT extract(epoch FROM now())")).scalar_one()
    return one_time_codes.totp_step(float(now))


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _failed_attempts(engine: Engine, user_id: int) -> int:
    return int(
        _scalar(
            engine, "SELECT failed_attempts FROM user_credentials WHERE user_id = :u", u=user_id
        )
    )


def _failed_codes(engine: Engine, user_id: int) -> int:
    return int(
        _scalar(engine, "SELECT failed_codes FROM user_credentials WHERE user_id = :u", u=user_id)
    )


def _set_counters(engine: Engine, user_id: int, **values: object) -> None:
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    with engine.begin() as connection:
        connection.execute(
            sa.text(f"UPDATE user_credentials SET {assignments} WHERE user_id = :u"),
            {"u": user_id, **values},
        )


def _policy(client: TestClient, **changes: int) -> dict[str, Any]:
    return _ok(admin_of(client).put(_POLICY_PATH, json=changes))


def _emailed_code(message: Any) -> str:
    content = str(message.get_content())
    return next(word.rstrip(".") for word in content.split() if word.rstrip(".").isdigit())


class TwoStepUser:
    """A User with two-step sign-in on: its session client, secret and recovery codes."""

    def __init__(self, signed_in: IdentityClient, secret: bytes, codes: list[str]) -> None:
        self.signed_in = signed_in
        self.secret = secret
        self.codes = codes

    @property
    def user_id(self) -> int:
        return self.signed_in.user_id

    @property
    def login(self) -> str:
        assert self.signed_in.identity is not None
        return self.signed_in.identity.login_name

    def code(self, engine: Engine, offset: int = 0) -> str:
        return one_time_codes.hotp(self.secret, _db_step(engine) + offset)


def _turn_on(client: TestClient, engine: Engine, *permissions: Permission) -> TwoStepUser:
    signed_in = client_as(client, *permissions)
    setup = _ok(
        signed_in.post("/api/session/two-step/totp", json={"current_password": TEST_PASSWORD})
    )
    secret = base64.b32decode(setup["secret"] + "=" * (-len(setup["secret"]) % 8))
    confirmed = _ok(
        signed_in.post(
            "/api/session/two-step/totp/confirm",
            json={"code": one_time_codes.hotp(secret, _db_step(engine))},
        )
    )
    return TwoStepUser(signed_in, secret, confirmed["recovery_codes"])


def _password_step(browser: TestClient, login: str, password: str = TEST_PASSWORD) -> Any:
    return browser.post("/api/session", json={"login_name": login, "password": password})


def _second_step(browser: TestClient, method: str, code: str) -> Any:
    return browser.post("/api/session/second-step", json={"method": method, "code": code})


def _two_step_audits(engine: Engine, user_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT before_data, after_data, metadata, actor_user_id FROM audit_events"
                    " WHERE entity_type = 'User' AND entity_id = :u"
                    " AND metadata ? 'two_step_change' ORDER BY id"
                ),
                {"u": str(user_id)},
            )
        )


# ---------------------------------------------------------------------------
# Setting up
# ---------------------------------------------------------------------------


def test_setting_up_takes_the_password_and_the_first_code(
    client: TestClient, db_engine: Engine
) -> None:
    signed_in = client_as(client)
    status = _ok(signed_in.get("/api/session/two-step"))
    assert status == {
        "enabled": False,
        "authenticator_app": False,
        "passkeys": [],
        "recovery_codes_left": 0,
        "email": None,
        "email_ready": False,
    }
    _refused(
        signed_in.post("/api/session/two-step/totp", json={"current_password": "wrong one"}),
        422,
        _WRONG_PASSWORD,
    )
    assert _failed_attempts(db_engine, signed_in.user_id) == 1

    setup = _ok(
        signed_in.post("/api/session/two-step/totp", json={"current_password": TEST_PASSWORD})
    )
    assert set(setup) == {"secret", "uri", "qr"}
    assert len(setup["secret"]) == 32
    assert setup["uri"].startswith("otpauth://totp/PartFlow:")
    assert setup["qr"] and set("".join(setup["qr"])) <= {"0", "1"}
    assert {len(row) for row in setup["qr"]} == {len(setup["qr"])}
    secret = base64.b32decode(setup["secret"])
    # Not on until the first code.
    assert _ok(signed_in.get("/api/session/two-step"))["enabled"] is False

    _refused(
        signed_in.post("/api/session/two-step/totp/confirm", json={"code": "000000"}),
        422,
        "That code does not match. Type the 6-digit code your authenticator app shows now.",
    )
    confirmed = _ok(
        signed_in.post(
            "/api/session/two-step/totp/confirm",
            json={"code": one_time_codes.hotp(secret, _db_step(db_engine))},
        )
    )
    codes = confirmed["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(one_time_codes.canonical_recovery_code(code) for code in codes)
    assert _ok(signed_in.get("/api/session/two-step")) == {
        "enabled": True,
        "authenticator_app": True,
        "passkeys": [],
        "recovery_codes_left": 10,
        "email": None,
        "email_ready": False,
    }
    # A second setup is refused while one is on.
    _refused(
        signed_in.post("/api/session/two-step/totp", json={"current_password": TEST_PASSWORD}),
        409,
        "An authenticator app is already set up. Remove it first to set up another one.",
    )
    [audit_row] = _two_step_audits(db_engine, signed_in.user_id)
    assert tuple(audit_row) == (
        {"two_step": False, "authenticator_app": False, "passkeys": 0, "recovery_codes": 0},
        {"two_step": True, "authenticator_app": True, "passkeys": 0, "recovery_codes": 10},
        {"two_step_change": "TOTP_ADDED"},
        signed_in.user_id,
    )
    stored = _scalar(
        db_engine, "SELECT secret FROM user_totp_factors WHERE user_id = :u", u=signed_in.user_id
    )
    assert bytes(stored) == secret
    assert setup["secret"] not in str(tuple(audit_row))


# ---------------------------------------------------------------------------
# Signing in with a second step
# ---------------------------------------------------------------------------


def test_the_password_alone_opens_a_second_step_and_the_app_code_signs_in(
    client: TestClient, db_engine: Engine
) -> None:
    user = _turn_on(client, db_engine)
    browser = _browser(client)
    step = _ok(_password_step(browser, user.login), 202)
    assert set(step) == {"methods", "email_hint", "expires_at"}
    assert step["methods"] == ["TOTP", "RECOVERY_CODE"]
    assert step["email_hint"] is None
    assert SIGN_IN_COOKIE in browser.cookies
    assert "partflow_session" not in browser.cookies
    assert _ok(browser.get("/api/session"))["user"] is None

    # The setup used the current step's code: the next one signs in.
    code = user.code(db_engine, 1)
    state = _ok(_second_step(browser, "TOTP", code))
    assert state["user"]["id"] == user.user_id
    assert "partflow_session" in browser.cookies
    assert SIGN_IN_COOKIE not in browser.cookies
    assert _ok(browser.get("/api/session"))["user"]["id"] == user.user_id
    assert (
        _scalar(
            db_engine,
            "SELECT end_reason FROM user_sign_in_challenges WHERE user_id = :u"
            " ORDER BY id DESC LIMIT 1",
            u=user.user_id,
        )
        == "COMPLETED"
    )

    # The same code never works twice.
    again = _browser(client)
    _ok(_password_step(again, user.login), 202)
    _refused(_second_step(again, "TOTP", code), 401, _WRONG_CODE, "second_step_failed")


def test_a_recovery_code_signs_in_once(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    _ok(_second_step(browser, "RECOVERY_CODE", f"  {user.codes[0].upper()}  "))
    assert _ok(user.signed_in.get("/api/session/two-step"))["recovery_codes_left"] == 9

    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    _refused(
        _second_step(browser, "RECOVERY_CODE", user.codes[0]),
        401,
        _WRONG_CODE,
        "second_step_failed",
    )


def test_wrong_codes_count_apart_from_wrong_passwords_and_end_the_step(
    client: TestClient, db_engine: Engine
) -> None:
    user = _turn_on(client, db_engine)
    browser = _browser(client)
    _ok(_password_step(browser, user.login, "a wrong password"), 401)
    assert _failed_attempts(db_engine, user.user_id) == 1
    # A correct password restarts the password counter (owner decision of 2026-11-17).
    _ok(_password_step(browser, user.login), 202)
    assert _failed_attempts(db_engine, user.user_id) == 0
    for attempt in range(1, 5):
        _refused(_second_step(browser, "TOTP", "000000"), 401, _WRONG_CODE, "second_step_failed")
        assert _failed_codes(db_engine, user.user_id) == attempt
    _refused(_second_step(browser, "TOTP", "000000"), 401, _TOO_MANY, "second_step_expired")
    # The step is over: even the right code is refused now.
    _refused(
        _second_step(browser, "TOTP", user.code(db_engine)), 401, _ENDED, "second_step_expired"
    )
    assert _failed_attempts(db_engine, user.user_id) == 0
    assert _failed_codes(db_engine, user.user_id) == 5

    # A correct password never restarts the wrong-code count; a full sign-in does.
    _ok(_password_step(browser, user.login), 202)
    assert _failed_codes(db_engine, user.user_id) == 5
    _ok(_second_step(browser, "TOTP", user.code(db_engine, 1)))
    assert _failed_attempts(db_engine, user.user_id) == 0
    assert _failed_codes(db_engine, user.user_id) == 0


def test_the_wrong_code_threshold_locks_the_account(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    _set_counters(db_engine, user.user_id, failed_attempts=9, failed_codes=8)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    # The password counter restarts; the code counter goes on to its threshold.
    assert _failed_attempts(db_engine, user.user_id) == 0
    _refused(_second_step(browser, "TOTP", "000000"), 401, _WRONG_CODE, "second_step_failed")
    assert _failed_codes(db_engine, user.user_id) == 9
    response = _second_step(browser, "TOTP", "000000")
    assert response.status_code == 409, response.text
    assert response.json()["account_locked"] is True
    assert _failed_codes(db_engine, user.user_id) == 0
    assert _scalar(
        db_engine,
        "SELECT locked_until > now() FROM user_credentials WHERE user_id = :u",
        u=user.user_id,
    )
    _refused(
        _second_step(browser, "TOTP", user.code(db_engine)), 401, _ENDED, "second_step_expired"
    )
    # While locked, even the right password opens no second step.
    _ok(_password_step(_browser(client), user.login), 401)
    _set_counters(db_engine, user.user_id, locked_until=None)


def test_an_expired_or_replaced_step_answers_that_it_ended(
    client: TestClient, db_engine: Engine
) -> None:
    user = _turn_on(client, db_engine)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE user_sign_in_challenges SET created_at = now() - interval '16 minutes'"
                " WHERE user_id = :u AND ended_at IS NULL"
            ),
            {"u": user.user_id},
        )
    _refused(
        _second_step(browser, "TOTP", user.code(db_engine)), 401, _ENDED, "second_step_expired"
    )

    # A new password step in the same browser replaces the open one.
    # The expired one (still presented by the cookie) ends REPLACED too.
    _ok(_password_step(browser, user.login), 202)
    first = browser.cookies[SIGN_IN_COOKIE]
    _ok(_password_step(browser, user.login), 202)
    stale = _with_cookie(client, first)
    _refused(_second_step(stale, "TOTP", user.code(db_engine)), 401, _ENDED, "second_step_expired")
    assert (
        _scalar(
            db_engine,
            "SELECT count(*) FROM user_sign_in_challenges WHERE user_id = :u"
            " AND end_reason = 'REPLACED'",
            u=user.user_id,
        )
        == 2
    )
    # Without any second step at all.
    _refused(_second_step(_browser(client), "TOTP", "123456"), 401, _ENDED, "second_step_expired")


def test_a_second_step_needs_the_csrf_header(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    bare = _with_cookie(client, browser.cookies[SIGN_IN_COOKIE], csrf=False)
    response = bare.post(
        "/api/session/second-step", json={"method": "TOTP", "code": user.code(db_engine)}
    )
    assert response.status_code == 403
    assert response.json()["csrf_rejected"] is True


@pytest.mark.usefixtures("mail_server")
def test_an_emailed_code_signs_in(
    client: TestClient, db_engine: Engine, smtp: fake_smtp.Recorder
) -> None:
    user = _turn_on(client, db_engine)
    _ok(admin_of(client).patch(f"/api/users/{user.user_id}", json={"email": "ann@example.org"}))
    browser = _browser(client)
    step = _ok(_password_step(browser, user.login), 202)
    assert step["methods"] == ["TOTP", "RECOVERY_CODE", "EMAIL_CODE"]
    assert step["email_hint"] == "a•••@example.org"

    sent = _ok(browser.post("/api/session/second-step/email-code"))
    assert sent == {"sent_to": "a•••@example.org", "resend_after_seconds": 60, "sends_left": 2}
    [message] = smtp.sent
    assert message["To"] == "ann@example.org"
    assert message["Subject"] == "Your PartFlow sign-in code"
    code = _emailed_code(message)
    assert len(code) == 8
    # Too soon for another one.
    response = browser.post("/api/session/second-step/email-code")
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Wait ")

    _ok(_second_step(browser, "EMAIL_CODE", code))
    assert _ok(browser.get("/api/session"))["user"]["id"] == user.user_id


def test_without_an_address_no_code_is_emailed(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    response = browser.post("/api/session/second-step/email-code")
    assert response.status_code == 409, response.text
    assert response.json()["detail"].startswith("PartFlow cannot email you a code")


# ---------------------------------------------------------------------------
# Own changes, administrators and the recovery command
# ---------------------------------------------------------------------------


def test_new_codes_and_turning_off_take_the_password(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    _refused(
        user.signed_in.post(
            "/api/session/two-step/recovery-codes", json={"current_password": "nope"}
        ),
        422,
        _WRONG_PASSWORD,
    )
    fresh = _ok(
        user.signed_in.post(
            "/api/session/two-step/recovery-codes", json={"current_password": TEST_PASSWORD}
        )
    )["recovery_codes"]
    assert len(fresh) == 10 and not set(fresh) & set(user.codes)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    _refused(
        _second_step(browser, "RECOVERY_CODE", user.codes[0]),
        401,
        _WRONG_CODE,
        "second_step_failed",
    )

    status = _ok(
        user.signed_in.post(
            "/api/session/two-step/totp/remove", json={"current_password": TEST_PASSWORD}
        )
    )
    assert status["enabled"] is False and status["recovery_codes_left"] == 0
    # The open second step ended with it; the password alone signs in now.
    _refused(
        _second_step(browser, "TOTP", user.code(db_engine)), 401, _ENDED, "second_step_expired"
    )
    assert _ok(_password_step(_browser(client), user.login))["user"]["id"] == user.user_id
    changes = [row.metadata["two_step_change"] for row in _two_step_audits(db_engine, user.user_id)]
    assert changes == ["TOTP_ADDED", "RECOVERY_CODES_CREATED", "TOTP_REMOVED"]
    _refused(
        user.signed_in.post(
            "/api/session/two-step/recovery-codes", json={"current_password": TEST_PASSWORD}
        ),
        409,
        "Two-step sign-in is off. Recovery codes come with an authenticator app or a passkey.",
    )


def test_a_user_administrator_turns_another_users_two_step_off(
    client: TestClient, db_engine: Engine
) -> None:
    user = _turn_on(client, db_engine, Permission.VIEW_PRODUCTION_DATA)
    manager = client_as(client, Permission.MANAGE_USERS_AND_ROLES)
    listed = {row["id"]: row for row in _list(manager.get("/api/users"))}
    assert listed[user.user_id]["two_step"] is True
    assert "two_step" not in _list(user.signed_in.get("/api/users"))[0]

    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    reset = f"/api/users/{user.user_id}/two-step/reset"
    keep = {"end_sessions": False}
    _refused(
        manager.post(f"/api/users/{manager.user_id}/two-step/reset", json=keep),
        409,
        "Use Two-step sign-in… in your account menu to turn off your own two-step sign-in.",
    )
    # The choice is required and a boolean.
    assert manager.post(reset).status_code == 422
    assert manager.post(reset, json={"end_sessions": "yes"}).status_code == 422
    _set_counters(db_engine, user.user_id, failed_codes=4)
    answered = _ok(manager.post(reset, json=keep))
    assert answered["two_step"] is False
    assert _failed_codes(db_engine, user.user_id) == 0
    _refused(
        _second_step(browser, "TOTP", user.code(db_engine)), 401, _ENDED, "second_step_expired"
    )
    # Their sign-ins continue.
    assert _ok(user.signed_in.get("/api/session"))["user"]["id"] == user.user_id
    [*_, reset_row] = _two_step_audits(db_engine, user.user_id)
    assert reset_row.metadata == {
        "two_step_change": "RESET_BY_ADMINISTRATOR",
        "sessions_ended": False,
    }
    assert reset_row.actor_user_id == manager.user_id
    # A repeat changes nothing, signing out included.
    count = len(_two_step_audits(db_engine, user.user_id))
    _ok(manager.post(reset, json={"end_sessions": True}))
    assert len(_two_step_audits(db_engine, user.user_id)) == count
    assert _ok(user.signed_in.get("/api/session"))["user"]["id"] == user.user_id


def test_a_reset_may_sign_the_user_out_everywhere(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    manager = client_as(client, Permission.MANAGE_USERS_AND_ROLES)
    _ok(manager.post(f"/api/users/{user.user_id}/two-step/reset", json={"end_sessions": True}))
    assert _ok(user.signed_in.get("/api/session"))["user"] is None
    open_sessions = _scalar(
        db_engine,
        "SELECT count(*) FROM user_sessions WHERE user_id = :u AND ended_at IS NULL",
        u=user.user_id,
    )
    assert open_sessions == 0
    ended_by_reset = _scalar(
        db_engine,
        "SELECT count(*) FROM user_sessions WHERE user_id = :u AND end_reason = 'TWO_STEP_RESET'",
        u=user.user_id,
    )
    assert ended_by_reset >= 1
    [*_, reset_row] = _two_step_audits(db_engine, user.user_id)
    assert reset_row.metadata == {
        "two_step_change": "RESET_BY_ADMINISTRATOR",
        "sessions_ended": True,
    }
    # The administrator's own sign-in continues.
    assert _ok(manager.get("/api/session"))["user"]["id"] == manager.user_id


def test_the_recovery_command_turns_two_step_off(
    client: TestClient, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    user = _turn_on(client, db_engine)
    assert cli.main(["reset-two-step", "--login-name", user.login.upper()]) == 0
    out = capsys.readouterr().out
    assert (
        "is off: the authenticator app, the passkeys and the recovery codes were deleted. The"
        " user's sign-ins continue." in out
    )
    assert _ok(user.signed_in.get("/api/session/two-step"))["enabled"] is False
    [*_, reset_row] = _two_step_audits(db_engine, user.user_id)
    assert reset_row.metadata == {
        "two_step_change": "RESET_BY_CLI",
        "sessions_ended": False,
        "source": "cli",
    }
    assert reset_row.actor_user_id is None

    assert cli.main(["reset-two-step", "--login-name", user.login]) == 0
    assert "was already off. Nothing was changed." in capsys.readouterr().out
    assert cli.main(["reset-two-step", "--login-name", "nobody-here"]) == 1
    assert "No user has the login name nobody-here." in capsys.readouterr().err


def test_the_recovery_command_may_sign_the_user_out_everywhere(
    client: TestClient, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    user = _turn_on(client, db_engine)
    assert cli.main(["reset-two-step", "--login-name", user.login, "--end-sessions"]) == 0
    assert "Every sign-in of the user ended." in capsys.readouterr().out
    assert _ok(user.signed_in.get("/api/session"))["user"] is None
    [*_, reset_row] = _two_step_audits(db_engine, user.user_id)
    assert reset_row.metadata == {
        "two_step_change": "RESET_BY_CLI",
        "sessions_ended": True,
        "source": "cli",
    }


def test_a_user_without_two_step_signs_in_with_the_password_alone(client: TestClient) -> None:
    identity = create_identity(client)
    state = _ok(_password_step(_browser(client), identity.login_name))
    assert state["user"]["id"] == identity.user_id


# ---------------------------------------------------------------------------
# The two-step policy of Administration → Settings
# ---------------------------------------------------------------------------


def test_the_policy_is_read_by_every_signed_in_user_and_audited(
    client: TestClient, db_engine: Engine
) -> None:
    answered = _ok(client_as(client).get(_POLICY_PATH))
    assert {key: value for key, value in answered.items() if key != "updated_at"} == (
        _POLICY_DEFAULTS
    )
    assert TestClient(app_of(client)).get(_POLICY_PATH).status_code == 401
    # Changing it takes Configure system settings.
    assert client_as(client).put(_POLICY_PATH, json={"two_step_max_codes": 3}).status_code == 403
    settings = client_as(client, Permission.CONFIGURE_SYSTEM_SETTINGS)
    answered = _ok(settings.put(_POLICY_PATH, json={"two_step_max_codes": 3}))
    assert answered["two_step_max_codes"] == 3
    assert answered["two_step_window_minutes"] == 15
    with db_engine.connect() as connection:
        before, after, actor = connection.execute(
            sa.text(
                "SELECT before_data, after_data, actor_user_id FROM audit_events"
                " WHERE entity_type = 'ApplicationPolicy' AND entity_id = 'two-step'"
                " ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert before == _POLICY_DEFAULTS
    assert after == {**_POLICY_DEFAULTS, "two_step_max_codes": 3}
    assert actor == settings.user_id
    # The same value again writes nothing.
    count = _scalar(db_engine, "SELECT count(*) FROM audit_events")
    _ok(settings.put(_POLICY_PATH, json={"two_step_max_codes": 3}))
    assert _scalar(db_engine, "SELECT count(*) FROM audit_events") == count


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {"two_step_window_minutes": 0},
            "The time to finish the second step must be a whole number of minutes from 1 to 60.",
        ),
        (
            {"two_step_max_codes": 21},
            "The wrong codes one sign-in may take must be a whole number from 1 to 20.",
        ),
        (
            {"two_step_lockout_codes": 2},
            "The wrong codes before a lock must be a whole number from 3 to 100.",
        ),
        (
            {"two_step_email_codes": 11},
            "The codes one sign-in may email must be a whole number from 1 to 10.",
        ),
        (
            {"two_step_email_interval_seconds": 3601},
            "The wait between two emailed codes must be a whole number of seconds from 0 to 3600.",
        ),
        (
            {"two_step_email_code_digits": 5},
            "The digits of an emailed code must be a whole number from 6 to 10.",
        ),
        (
            {"two_step_recovery_codes": 4},
            "The number of recovery codes must be a whole number from 5 to 20.",
        ),
        (
            {"two_step_recovery_code_length": 21},
            "The characters of a recovery code must be a whole number from 8 to 20.",
        ),
        (
            {"two_step_setup_minutes": 61},
            "The time to finish setting up an authenticator app or a passkey must be a whole number"
            " of minutes from 1 to 60.",
        ),
        (
            {"two_step_clock_drift_steps": 3},
            "The clock drift allowed must be a whole number of 30-second steps from 0 to 2.",
        ),
        (
            {"two_step_max_codes": None},
            "The wrong codes one sign-in may take must be a whole number from 1 to 20.",
        ),
        ({}, "Change at least one two-step sign-in setting."),
    ],
)
def test_out_of_range_values_are_refused(
    client: TestClient, db_engine: Engine, body: dict[str, Any], detail: str
) -> None:
    count = _scalar(db_engine, "SELECT count(*) FROM audit_events")
    _refused(admin_of(client).put(_POLICY_PATH, json=body), 422, detail)
    assert _scalar(db_engine, "SELECT count(*) FROM audit_events") == count


@pytest.mark.parametrize(
    "body",
    [
        {"two_step_max_codes": "3"},
        {"two_step_max_codes": 3.0},
        {"two_step_max_codes": True},
        {"two_step_codes": 3},
    ],
)
def test_malformed_policy_bodies_are_refused_by_the_schema(
    client: TestClient, body: dict[str, Any]
) -> None:
    assert admin_of(client).put(_POLICY_PATH, json=body).status_code == 422


def test_the_policy_shapes_the_next_second_step(client: TestClient, db_engine: Engine) -> None:
    user = _turn_on(client, db_engine)
    _policy(client, two_step_max_codes=2, two_step_window_minutes=1, two_step_clock_drift_steps=2)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    _refused(_second_step(browser, "TOTP", "000000"), 401, _WRONG_CODE, "second_step_failed")
    _refused(_second_step(browser, "TOTP", "000000"), 401, _TOO_MANY, "second_step_expired")

    # A one-minute window: two minutes old is expired.
    _ok(_password_step(browser, user.login), 202)
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE user_sign_in_challenges SET created_at = now() - interval '2 minutes'"
                " WHERE user_id = :u AND ended_at IS NULL"
            ),
            {"u": user.user_id},
        )
    _refused(
        _second_step(browser, "TOTP", user.code(db_engine, 1)), 401, _ENDED, "second_step_expired"
    )

    # A drift of two steps accepts a code two steps ahead.
    _ok(_password_step(browser, user.login), 202)
    _ok(_second_step(browser, "TOTP", user.code(db_engine, 2)))


def test_the_policy_shapes_recovery_codes_and_the_setup_time(
    client: TestClient, db_engine: Engine
) -> None:
    _policy(client, two_step_recovery_codes=5, two_step_recovery_code_length=8)
    user = _turn_on(client, db_engine)
    assert len(user.codes) == 5
    assert all(len(code) == 9 and code[4] == "-" for code in user.codes)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    _ok(_second_step(browser, "RECOVERY_CODE", user.codes[0]))

    # Longer codes from now on; the shorter ones still work until replaced.
    _policy(client, two_step_recovery_code_length=16)
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    _ok(_second_step(browser, "RECOVERY_CODE", user.codes[1]))
    fresh = _ok(
        user.signed_in.post(
            "/api/session/two-step/recovery-codes", json={"current_password": TEST_PASSWORD}
        )
    )["recovery_codes"]
    assert len(fresh) == 5 and {len(code) for code in fresh} == {19}

    # A one-minute setup time: a setup two minutes old has expired.
    _policy(client, two_step_setup_minutes=1)
    other = client_as(client)
    setup = _ok(other.post("/api/session/two-step/totp", json={"current_password": TEST_PASSWORD}))
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE user_totp_factors SET created_at = now() - interval '2 minutes'"
                " WHERE user_id = :u"
            ),
            {"u": other.user_id},
        )
    secret = base64.b32decode(setup["secret"])
    _refused(
        other.post(
            "/api/session/two-step/totp/confirm",
            json={"code": one_time_codes.hotp(secret, _db_step(db_engine))},
        ),
        409,
        "This setup has expired or was not started. Start again with Set up two-step sign-in.",
    )


@pytest.mark.usefixtures("mail_server")
def test_the_policy_shapes_emailed_codes(
    client: TestClient, db_engine: Engine, smtp: fake_smtp.Recorder
) -> None:
    _policy(
        client,
        two_step_email_codes=2,
        two_step_email_interval_seconds=0,
        two_step_email_code_digits=6,
    )
    user = _turn_on(client, db_engine)
    _ok(admin_of(client).patch(f"/api/users/{user.user_id}", json={"email": "ann@example.org"}))
    browser = _browser(client)
    _ok(_password_step(browser, user.login), 202)
    first = _ok(browser.post("/api/session/second-step/email-code"))
    assert (first["resend_after_seconds"], first["sends_left"]) == (0, 1)
    # No wait between codes; the last one sent is the one that works.
    second = _ok(browser.post("/api/session/second-step/email-code"))
    assert second["sends_left"] == 0
    response = browser.post("/api/session/second-step/email-code")
    assert response.status_code == 409, response.text
    assert response.json()["detail"].startswith("PartFlow already emailed the most codes")
    old, new = (_emailed_code(message) for message in smtp.sent)
    assert len(old) == len(new) == 6
    if old != new:
        _refused(_second_step(browser, "EMAIL_CODE", old), 401, _WRONG_CODE, "second_step_failed")
    _ok(_second_step(browser, "EMAIL_CODE", new))


# ---------------------------------------------------------------------------
# A User's own email address
# ---------------------------------------------------------------------------


def _email_audits(engine: Engine, user_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT before_data, after_data, metadata, actor_user_id FROM audit_events"
                    " WHERE entity_type = 'User' AND entity_id = :u"
                    " AND metadata ? 'email_change' ORDER BY id"
                ),
                {"u": str(user_id)},
            )
        )


def test_a_user_changes_their_own_email_with_the_password(
    client: TestClient, db_engine: Engine, smtp: fake_smtp.Recorder
) -> None:
    signed_in = client_as(client)
    path = "/api/session/email"
    # The address is judged first: a malformed one never costs a password try.
    _refused(
        signed_in.put(path, json={"current_password": TEST_PASSWORD, "email": "ann"}),
        422,
        "Enter an email address such as name@example.com: no spaces, a domain with a dot after"
        " the @, and at most 254 characters.",
    )
    assert _failed_attempts(db_engine, signed_in.user_id) == 0
    _refused(
        signed_in.put(path, json={"current_password": "wrong", "email": "ann@example.org"}),
        422,
        _WRONG_PASSWORD,
    )
    assert _failed_attempts(db_engine, signed_in.user_id) == 1

    answered = _ok(
        signed_in.put(path, json={"current_password": TEST_PASSWORD, "email": " Ann@Example.ORG "})
    )
    assert answered == {"email": "Ann@example.org"}
    assert _ok(signed_in.get("/api/session/two-step"))["email"] == "Ann@example.org"
    [row] = _email_audits(db_engine, signed_in.user_id)
    assert (row.before_data["email"], row.after_data["email"]) == (None, "Ann@example.org")
    assert (row.metadata, row.actor_user_id) == ({"email_change": "BY_USER"}, signed_in.user_id)
    # No mail server: the change stands without a notice.
    assert smtp.connections == []

    # The same address again changes nothing.
    _ok(signed_in.put(path, json={"current_password": TEST_PASSWORD, "email": "Ann@EXAMPLE.org"}))
    assert len(_email_audits(db_engine, signed_in.user_id)) == 1
    # Changing it needs a signed-in User.
    anonymous = TestClient(app_of(client), headers=CSRF_HEADERS)
    response = anonymous.put(path, json={"current_password": TEST_PASSWORD, "email": None})
    assert response.status_code == 401


@pytest.mark.usefixtures("mail_server")
def test_the_previous_address_hears_of_a_change(
    client: TestClient, db_engine: Engine, smtp: fake_smtp.Recorder
) -> None:
    signed_in = client_as(client)
    path = "/api/session/email"
    _ok(signed_in.put(path, json={"current_password": TEST_PASSWORD, "email": "ann@example.org"}))
    # The first address had no predecessor to tell.
    assert smtp.sent == []

    _ok(signed_in.put(path, json={"current_password": TEST_PASSWORD, "email": "bob@example.org"}))
    [notice] = smtp.sent
    assert notice["To"] == "ann@example.org"
    assert notice["Subject"] == "Your PartFlow email address was changed"
    assert "was changed to b•••@example.org on " in notice.get_content()
    assert "bob@example.org" not in notice.get_content()

    # A refusing mail server never undoes the change.
    smtp.fail("send", OSError(113, "No route to host"))
    answered = _ok(signed_in.put(path, json={"current_password": TEST_PASSWORD, "email": None}))
    assert answered == {"email": None}
    assert _ok(signed_in.get("/api/session/two-step"))["email"] is None
    assert len(_email_audits(db_engine, signed_in.user_id)) == 3
