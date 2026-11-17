"""Integration tests for Administration → Security (owner request of 2026-11-17).

Exercises the full request path — the CSRF middleware, FastAPI routes, the
policy, authentication and first-run services and PostgreSQL — against a
dedicated temporary database migrated to head by the real Alembic chain:

- the Password rules and Remember me sections: every signed-in User reads
  them, ``CONFIGURE_SYSTEM_SETTINGS`` edits them (a partial merge), each
  effective change appends one audit row naming the administrator, a no-op
  writes nothing, and every value outside its range, a minimum above the
  maximum, a ``null``, a wrong type or an unknown field is refused;
- ``GET /api/session/sign-in-options`` is public and answers what the
  dialogs show before anyone types;
- the stored password rules apply to Change password, an administrator's
  Set password and the recovery command, naming every unmet rule, while a
  stored password keeps signing in when the rules tighten (first-run setup
  is covered in ``tests/test_first_run_setup_api.py``);
- Remember me: not offered (the choice is ignored, today's persistent
  cookie and User sign-in lifetime), offered and not chosen (a
  browser-session cookie, the User sign-in lifetime), chosen for some days,
  chosen until signed out (the 400-day cookie renewed by ``GET
  /api/session``, never expiring however old), an offer turned off (back to
  the User sign-in lifetime), the second step and a passkey alone carrying
  the choice, Change password keeping it, and every way of ending sessions
  ending remembered ones too.

Every case restores the policy values it changed.
"""

import base64
import datetime
import http.cookiejar
import os
from collections.abc import Iterator
from contextlib import contextmanager
from http.cookies import Morsel, SimpleCookie
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
from app import cli
from app.application import authentication, one_time_codes
from app.application.errors import InvalidInputError
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
_TEST_DATABASE = "partflow_test_security_settings_api"
_COOKIE = authentication.SESSION_COOKIE
_DAY = 86_400
_NEVER = 34_560_000
_ORIGIN = "https://pf.example.com"
_RULES_PATH = "/api/policies/password-rules"
_REMEMBER_PATH = "/api/policies/remember-me"
_RULE_DEFAULTS = {
    "password_min_length": 6,
    "password_max_length": 256,
    "password_require_digit": False,
    "password_require_special": False,
    "password_require_uppercase": False,
    "password_require_lowercase": False,
}
_REMEMBER_DEFAULTS = {
    "remember_me_offered": False,
    "remember_me_expires": True,
    "remember_me_days": 30,
}
_A1 = "You are not signed in, or your sign-in has ended. Sign in to continue."
_A2 = "Your account does not have permission to do this."
_DIGIT = "A password must contain a digit."
_SPECIAL = "A password must contain a special character such as ! # % or *."
_UPPER = "A password must contain an uppercase letter."
_LOWER = "A password must contain a lowercase letter."
_STRICT = {
    "password_min_length": 10,
    "password_require_digit": True,
    "password_require_special": True,
    "password_require_uppercase": True,
}
_GOOD = "Strong-enough-2026"


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    """The module client keeps no cookie: every case sends its own explicitly."""

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
    """Application client wired to the temporary database through the real
    startup path (DATABASE_URL pointed at it, settings re-read)."""
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


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _refused(response: Any, status: int, detail: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = cast(dict[str, Any], response.json())
    assert body["detail"] == detail
    return body


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar_one()


def _execute(engine: Engine, sql: str, **params: object) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(sql), params)


@contextmanager
def _policy(engine: Engine, **values: object) -> Iterator[None]:
    """Set Security policy values directly; restore every default afterwards."""
    assignments = ", ".join(f"{name} = :{name}" for name in values)
    _execute(engine, f"UPDATE application_policy SET {assignments}", **values)
    try:
        yield
    finally:
        defaults = {**_RULE_DEFAULTS, **_REMEMBER_DEFAULTS, "user_session_expires": True}
        restore = ", ".join(f"{name} = :{name}" for name in defaults)
        _execute(engine, f"UPDATE application_policy SET {restore}", **defaults)


def _audit_rows(engine: Engine, section: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT * FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
                " AND entity_id = :section ORDER BY id"
            ),
            {"section": section},
        )
        return [dict(row._mapping) for row in rows]


def _browser(client: TestClient) -> TestClient:
    """A browser at the HTTPS origin with its own cookie jar and the CSRF header."""
    return TestClient(app_of(client), headers={**CSRF_HEADERS, "Origin": _ORIGIN})


def _morsel(response: Any) -> Morsel[str]:
    for header in response.headers.get_list("set-cookie"):
        jar: SimpleCookie = SimpleCookie()
        jar.load(header)
        if _COOKIE in jar:
            return jar[_COOKIE]
    raise AssertionError(f"no {_COOKIE} cookie in {response.headers}")


def _max_age(response: Any) -> int | None:
    """The session cookie's Max-Age; None = a browser-session cookie."""
    morsel = _morsel(response)
    assert morsel.value
    if morsel["max-age"] == "":
        assert morsel["expires"] == ""
        return None
    return int(morsel["max-age"])


def _login(user: IdentityClient) -> str:
    assert user.identity is not None
    return user.identity.login_name


def _sign_in(client: TestClient, user: IdentityClient, **extra: object) -> Any:
    return client.post(
        "/api/session",
        json={"login_name": _login(user), "password": TEST_PASSWORD, **extra},
        headers=CSRF_HEADERS,
    )


def _token(response: Any) -> str:
    return _morsel(response).value


def _session_get(client: TestClient, token: str) -> Any:
    return client.get("/api/session", headers={"Cookie": f"{_COOKIE}={token}"})


def _age_session(engine: Engine, token: str, days: int) -> None:
    _execute(
        engine,
        "UPDATE user_sessions SET created_at = now() - make_interval(days => :days)"
        " WHERE token_digest = :digest",
        days=days,
        digest=authentication._digest(token),
    )


def _remembered(engine: Engine, token: str) -> bool:
    return bool(
        _scalar(
            engine,
            "SELECT remembered FROM user_sessions WHERE token_digest = :digest",
            digest=authentication._digest(token),
        )
    )


def _expires_in_days(state: dict[str, Any]) -> float | None:
    expires = state["user"]["session_expires_at"]
    if expires is None:
        return None
    remaining = datetime.datetime.fromisoformat(expires) - datetime.datetime.now(datetime.UTC)
    return remaining.total_seconds() / _DAY


# ---------------------------------------------------------------------------
# The two sections
# ---------------------------------------------------------------------------


def test_the_password_rules_section(client: TestClient, db_engine: Engine) -> None:
    plain = client_as(client)
    manager = client_as(client, Permission.CONFIGURE_SYSTEM_SETTINGS)

    _refused(client.get(_RULES_PATH), 401, _A1)
    stored = _ok(plain.get(_RULES_PATH))
    assert {name: stored[name] for name in _RULE_DEFAULTS} == _RULE_DEFAULTS
    denied = _refused(plain.put(_RULES_PATH, json={"password_min_length": 8}), 403, _A2)
    assert denied["required_permissions"] == ["CONFIGURE_SYSTEM_SETTINGS"]

    def put(body: object) -> Any:
        return manager.put(_RULES_PATH, json=body)

    start = len(_audit_rows(db_engine, "password-rules"))
    try:
        changed = _ok(put({"password_min_length": 12, "password_require_digit": True}))
        assert (changed["password_min_length"], changed["password_require_digit"]) == (12, True)
        assert changed["password_max_length"] == 256
        rows = _audit_rows(db_engine, "password-rules")
        assert len(rows) == start + 1
        assert rows[-1]["actor_user_id"] == manager.user_id
        assert rows[-1]["before_data"] == _RULE_DEFAULTS
        assert rows[-1]["after_data"] == {
            **_RULE_DEFAULTS,
            "password_min_length": 12,
            "password_require_digit": True,
        }
        # A no-op writes nothing.
        _ok(put({"password_min_length": 12}))
        assert len(_audit_rows(db_engine, "password-rules")) == start + 1

        _refused(put({}), 422, "Change at least one password rule.")
        for value in (3, 257):
            _refused(
                put({"password_min_length": value}),
                422,
                "The minimum password length must be a whole number of characters from 4 to 256.",
            )
            _refused(
                put({"password_max_length": value}),
                422,
                "The maximum password length must be a whole number of characters from 4 to 256.",
            )
        order = "The minimum password length cannot be greater than the maximum password length."
        # Judged after the merge: the stored minimum (12) against a new maximum.
        _refused(put({"password_max_length": 11}), 422, order)
        _refused(put({"password_min_length": 30, "password_max_length": 20}), 422, order)
        _refused(
            put({"password_require_special": None}),
            422,
            "Each password requirement must be On or Off.",
        )
        for body in (
            {"password_min_length": "8"},
            {"password_min_length": 8.0},
            {"password_min_length": True},
            {"password_require_digit": 1},
            {"password_require_digit": "true"},
            {"unknown": 1},
        ):
            assert put(body).status_code == 422, body
        assert len(_audit_rows(db_engine, "password-rules")) == start + 1
        _ok(put({"password_min_length": 12, "password_max_length": 12}))
    finally:
        _ok(put(_RULE_DEFAULTS))


def test_the_remember_me_section(client: TestClient, db_engine: Engine) -> None:
    plain = client_as(client)
    manager = client_as(client, Permission.CONFIGURE_SYSTEM_SETTINGS)

    _refused(client.get(_REMEMBER_PATH), 401, _A1)
    stored = _ok(plain.get(_REMEMBER_PATH))
    assert {name: stored[name] for name in _REMEMBER_DEFAULTS} == _REMEMBER_DEFAULTS
    _refused(plain.put(_REMEMBER_PATH, json={"remember_me_offered": True}), 403, _A2)

    def put(body: object) -> Any:
        return manager.put(_REMEMBER_PATH, json=body)

    start = len(_audit_rows(db_engine, "remember-me"))
    try:
        changed = _ok(put({"remember_me_offered": True, "remember_me_expires": False}))
        assert changed["remember_me_days"] == 30  # kept while switched off
        rows = _audit_rows(db_engine, "remember-me")
        assert len(rows) == start + 1
        assert rows[-1]["actor_user_id"] == manager.user_id
        assert rows[-1]["after_data"] == {
            "remember_me_offered": True,
            "remember_me_expires": False,
            "remember_me_days": 30,
        }
        _ok(put({"remember_me_offered": True}))
        assert len(_audit_rows(db_engine, "remember-me")) == start + 1

        _refused(put({}), 422, "Change at least one Remember me setting.")
        for value in (0, 366):
            _refused(
                put({"remember_me_days": value}),
                422,
                "A remembered sign-in must last a whole number of days from 1 to 365.",
            )
        _refused(
            put({"remember_me_offered": None}), 422, "Each Remember me option must be On or Off."
        )
        for body in (
            {"remember_me_days": "30"},
            {"remember_me_days": True},
            {"remember_me_offered": 1},
            {"unknown": True},
        ):
            assert put(body).status_code == 422, body
        assert len(_audit_rows(db_engine, "remember-me")) == start + 1
        _ok(put({"remember_me_days": 1}))
        _ok(put({"remember_me_days": 365}))
    finally:
        _ok(put(_REMEMBER_DEFAULTS))


def test_the_sign_in_options_are_public(client: TestClient, db_engine: Engine) -> None:
    default = _ok(client.get("/api/session/sign-in-options"))
    assert default == {
        "remember_me": None,
        "password_rules": {
            "min_length": 6,
            "max_length": 256,
            "require_digit": False,
            "require_special": False,
            "require_uppercase": False,
            "require_lowercase": False,
        },
    }
    with _policy(db_engine, remember_me_offered=True, remember_me_days=90, **_STRICT):
        options = _ok(client.get("/api/session/sign-in-options"))
        assert options["remember_me"] == {"days": 90}
        assert options["password_rules"] == {
            "min_length": 10,
            "max_length": 256,
            "require_digit": True,
            "require_special": True,
            "require_uppercase": True,
            "require_lowercase": False,
        }
    with _policy(db_engine, remember_me_offered=True, remember_me_expires=False):
        assert _ok(client.get("/api/session/sign-in-options"))["remember_me"] == {"days": None}


# ---------------------------------------------------------------------------
# The password rules on every path
# ---------------------------------------------------------------------------


def test_every_password_path_applies_the_stored_rules(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    target = client_as(client)
    manager = client_as(client, Permission.MANAGE_USERS_AND_ROLES)
    unmet = f"A password must be at least 10 characters long. {_DIGIT} {_SPECIAL} {_UPPER}"
    with _policy(db_engine, **_STRICT):
        # A stored password keeps signing in when the rules tighten.
        _ok(_sign_in(client, user))

        # Change password.
        _refused(
            user.put(
                "/api/session/password",
                json={"current_password": TEST_PASSWORD, "new_password": "short"},
            ),
            422,
            unmet,
        )
        _refused(
            user.put(
                "/api/session/password",
                json={"current_password": TEST_PASSWORD, "new_password": "strong-enough-2026"},
            ),
            422,
            _UPPER,
        )

        # An administrator's Set password.
        path = f"/api/users/{target.user_id}/password"
        _refused(manager.put(path, json={"new_password": "short"}), 422, unmet)
        _ok(manager.put(path, json={"new_password": _GOOD}))

        # The recovery command's application call (``reset-password``).
        with Session(db_engine) as session, pytest.raises(InvalidInputError) as raised:
            authentication.reset_password_for_login(
                session, _login(target), new_password="Strong-but-no-digit"
            )
        assert raised.value.message == _DIGIT
        with Session(db_engine) as session:
            authentication.reset_password_for_login(
                session, _login(target), new_password="Another-strong-1"
            )

        changed = user.put(
            "/api/session/password",
            json={"current_password": TEST_PASSWORD, "new_password": _GOOD},
        )
        _ok(changed)
    with _policy(db_engine, password_require_lowercase=True, password_max_length=12):
        _refused(
            manager.put(path, json={"new_password": "ALL-CAPS-PASSWORD"}),
            422,
            f"A password can be at most 12 characters long. {_LOWER}",
        )


def test_the_recovery_command_names_the_unmet_rules(
    client: TestClient,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = client_as(client)
    monkeypatch.setattr(cli, "_read_new_password", lambda: "lowercase-only")
    with _policy(db_engine, password_require_uppercase=True):
        assert cli.main(["reset-password", "--login-name", _login(target)]) == 1
    assert capsys.readouterr().err.strip() == _UPPER


# ---------------------------------------------------------------------------
# Remember me
# ---------------------------------------------------------------------------


def test_without_the_offer_the_choice_is_ignored(client: TestClient, db_engine: Engine) -> None:
    user = client_as(client)
    response = _sign_in(client, user, remember_me=True)
    state = _ok(response)
    token = _token(response)
    assert not _remembered(db_engine, token)
    # Today's behavior: a persistent cookie for the User sign-in lifetime (30 days).
    max_age = _max_age(response)
    assert max_age is not None and abs(max_age - 30 * _DAY) <= 60
    days = _expires_in_days(state)
    assert days is not None and abs(days - 30) < 0.01
    # A later offer never turns it into a remembered sign-in.
    with _policy(db_engine, remember_me_offered=True, remember_me_expires=False):
        renewed = _session_get(client, token)
        assert _expires_in_days(_ok(renewed)) is not None
        assert _max_age(renewed) is None
    assert _session_get(client, token).json()["user"]["id"] == user.user_id
    # The choice must be a boolean.
    assert _sign_in(client, user, remember_me="yes").status_code == 422


def test_an_unremembered_sign_in_keeps_a_browser_session_cookie(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    with _policy(db_engine, remember_me_offered=True, remember_me_days=90):
        response = _sign_in(client, user)
        token = _token(response)
        assert not _remembered(db_engine, token)
        assert _max_age(response) is None
        days = _expires_in_days(_ok(response))
        assert days is not None and abs(days - 30) < 0.01
        renewed = _session_get(client, token)
        assert _max_age(renewed) is None
        _age_session(db_engine, token, 31)
        assert _ok(_session_get(client, token))["user"] is None


def test_a_remembered_sign_in_lasts_the_remember_me_days(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    with _policy(db_engine, remember_me_offered=True, remember_me_days=90):
        response = _sign_in(client, user, remember_me=True)
        token = _token(response)
        assert _remembered(db_engine, token)
        max_age = _max_age(response)
        assert max_age is not None and abs(max_age - 90 * _DAY) <= 60
        _age_session(db_engine, token, 60)
        renewed = _session_get(client, token)
        assert _ok(renewed)["user"]["id"] == user.user_id
        max_age = _max_age(renewed)
        assert max_age is not None and abs(max_age - 30 * _DAY) <= 60
        _age_session(db_engine, token, 91)
        assert _ok(_session_get(client, token))["user"] is None


def test_a_sign_in_remembered_until_signed_out_is_renewed_on_use(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    with _policy(db_engine, remember_me_offered=True, remember_me_expires=False):
        response = _sign_in(client, user, remember_me=True)
        token = _token(response)
        assert _expires_in_days(_ok(response)) is None
        assert _max_age(response) == _NEVER
        # Older than the browser's 400-day cap: each visit renews the cookie.
        _age_session(db_engine, token, 500)
        renewed = _session_get(client, token)
        assert _ok(renewed)["user"]["id"] == user.user_id
        assert _max_age(renewed) == _NEVER
        assert _morsel(renewed).value == token
        # Turning the offer off returns it to the User sign-in lifetime.
        with _policy(db_engine, remember_me_offered=False):
            ended = _session_get(client, token)
            assert _ok(ended)["user"] is None
            assert int(_morsel(ended)["max-age"]) == 0


def test_the_offer_turned_off_returns_a_session_to_the_sign_in_lifetime(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    with _policy(db_engine, remember_me_offered=True, remember_me_days=365):
        token = _token(_ok_response(_sign_in(client, user, remember_me=True)))
        _age_session(db_engine, token, 10)
    renewed = _session_get(client, token)
    days = _expires_in_days(_ok(renewed))
    assert days is not None and abs(days - 20) < 0.01
    max_age = _max_age(renewed)
    assert max_age is not None and abs(max_age - 20 * _DAY) <= 60


def _ok_response(response: Any) -> Any:
    assert response.status_code == 200, response.text
    return response


def test_the_second_step_and_a_passkey_carry_the_choice(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    soft = SoftAuthenticator(_ORIGIN)
    options = _ok(
        user.post(
            "/api/session/two-step/passkeys/options",
            json={"current_password": TEST_PASSWORD},
            headers={"Origin": _ORIGIN},
        )
    )["options"]
    codes = _ok(
        user.post(
            "/api/session/two-step/passkeys",
            json={"name": "Laptop", "credential": soft.create(options)},
            headers={"Origin": _ORIGIN},
        )
    )["recovery_codes"]
    assert codes
    with _policy(db_engine, remember_me_offered=True, remember_me_expires=False):
        # The password, then a recovery code with Remember me.
        browser = _browser(client)
        _ok(
            browser.post(
                "/api/session", json={"login_name": _login(user), "password": TEST_PASSWORD}
            ),
            202,
        )
        finished = browser.post(
            "/api/session/second-step",
            json={"method": "RECOVERY_CODE", "code": codes[0], "remember_me": True},
        )
        _ok(finished)
        assert _remembered(db_engine, _token(finished))
        assert _max_age(finished) == _NEVER

        # The password, then a passkey without Remember me.
        browser = _browser(client)
        _ok(
            browser.post(
                "/api/session", json={"login_name": _login(user), "password": TEST_PASSWORD}
            ),
            202,
        )
        step = _ok(browser.post("/api/session/second-step/passkey-options"))["options"]
        finished = browser.post(
            "/api/session/second-step/passkey", json={"credential": soft.get(step)}
        )
        _ok(finished)
        assert not _remembered(db_engine, _token(finished))
        assert _max_age(finished) is None

        # A passkey alone, with Remember me.
        browser = _browser(client)
        alone = _ok(browser.post("/api/session/passkey/options"))["options"]
        finished = browser.post(
            "/api/session/passkey", json={"credential": soft.get(alone), "remember_me": True}
        )
        _ok(finished)
        assert _remembered(db_engine, _token(finished))
        assert _max_age(finished) == _NEVER


def test_change_password_keeps_the_choice_and_ends_the_other_remembered_sessions(
    client: TestClient, db_engine: Engine
) -> None:
    user = client_as(client)
    with _policy(db_engine, remember_me_offered=True, remember_me_days=90):
        mine = _token(_ok_response(_sign_in(client, user, remember_me=True)))
        other = _token(_ok_response(_sign_in(client, user, remember_me=True)))
        changed = client.put(
            "/api/session/password",
            json={"current_password": TEST_PASSWORD, "new_password": _GOOD},
            headers={**CSRF_HEADERS, "Cookie": f"{_COOKIE}={mine}"},
        )
        _ok(changed)
        rotated = _token(changed)
        assert _remembered(db_engine, rotated)
        max_age = _max_age(changed)
        assert max_age is not None and abs(max_age - 90 * _DAY) <= 60
        for ended in (mine, other):
            assert _ok(_session_get(client, ended))["user"] is None
            assert (
                _scalar(
                    db_engine,
                    "SELECT end_reason FROM user_sessions WHERE token_digest = :d",
                    d=authentication._digest(ended),
                )
                == "PASSWORD_CHANGED"
            )


@pytest.mark.parametrize("ending", ["sign-out", "password-set", "two-step-reset", "deactivation"])
def test_every_way_of_ending_sessions_ends_remembered_ones(
    client: TestClient, db_engine: Engine, ending: str
) -> None:
    user = client_as(client)
    manager = admin_of(client)
    with _policy(db_engine, remember_me_offered=True, remember_me_expires=False):
        token = _token(_ok_response(_sign_in(client, user, remember_me=True)))
        assert _remembered(db_engine, token)
        if ending == "sign-out":
            response = client.delete(
                "/api/session", headers={**CSRF_HEADERS, "Cookie": f"{_COOKIE}={token}"}
            )
            assert response.status_code == 204
            reason = "SIGNED_OUT"
        elif ending == "password-set":
            _ok(manager.put(f"/api/users/{user.user_id}/password", json={"new_password": _GOOD}))
            reason = "PASSWORD_RESET"
        elif ending == "two-step-reset":
            secret_setup = _ok(
                user.post("/api/session/two-step/totp", json={"current_password": TEST_PASSWORD})
            )
            assert secret_setup["secret"]
            _ok(
                user.post(
                    "/api/session/two-step/totp/confirm",
                    json={"code": _app_code(db_engine, secret_setup["secret"])},
                )
            )
            _ok(
                manager.post(
                    f"/api/users/{user.user_id}/two-step/reset", json={"end_sessions": True}
                )
            )
            reason = "TWO_STEP_RESET"
        else:
            _ok(manager.patch(f"/api/users/{user.user_id}", json={"is_active": False}))
            reason = "USER_DEACTIVATED"
        assert _ok(_session_get(client, token))["user"] is None
        assert (
            _scalar(
                db_engine,
                "SELECT end_reason FROM user_sessions WHERE token_digest = :d",
                d=authentication._digest(token),
            )
            == reason
        )


def _app_code(engine: Engine, secret_text: str) -> str:
    secret = base64.b32decode(secret_text + "=" * (-len(secret_text) % 8))
    now = float(_scalar(engine, "SELECT extract(epoch FROM now())"))
    return one_time_codes.hotp(secret, one_time_codes.totp_step(now))
