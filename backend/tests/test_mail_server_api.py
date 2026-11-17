"""Integration tests for the outgoing mail server of Administration → Settings (Phase 17).

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 8): a
User who loses their other sign-in methods may receive a one-time code by
email, sent through the mail server set here.

- ``GET /api/policies/mail-server`` (signed in): host, port, security,
  user name, whether a password is stored and the sender — never the
  password;
- ``PUT`` (Configure system settings): a full replace; the password is
  kept when left out, cleared by ``null``, replaced by a string, and
  dropped with the user name; a refused value writes nothing, a no-op
  writes nothing, and an effective change is audited once as
  ``ApplicationPolicy`` / ``mail-server`` with whether a password is
  stored and, in the metadata, whether it changed — never the password;
- ``POST /api/policies/mail-server/test``: one test message through the
  stored server (the stand-in of ``tests/fake_smtp.py``); 409 without a
  server or when the server refuses, with the reason; nothing is written.

The API commits real transactions; the section is reset after each test
and the module database is dropped afterwards. The migration itself is
covered by ``tests/test_phase17_outgoing_mail_schema.py``; the access rules
by ``tests/test_route_authorization_api.py``.
"""

import os
import smtplib
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
from app.application.outgoing_mail import MAIL_SERVER_NOT_SET_UP_MESSAGE
from app.core.config import get_settings
from app.main import create_app
from tests import fake_smtp
from tests.auth_harness import admin_of, anonymous_client, client_as

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_mail_server_api"
_DB_URL_ENV = "DATABASE_URL"
_PATH = "/api/policies/mail-server"
_TEST_PATH = "/api/policies/mail-server/test"
_SECTION = "mail-server"
_PASSWORD = "app-password-1"
_NONE = {
    "mail_host": None,
    "mail_port": 587,
    "mail_security": "STARTTLS",
    "mail_username": None,
    "mail_sender": None,
}
_SERVER = {
    "mail_host": " SMTP.Example.com ",
    "mail_port": 587,
    "mail_security": "STARTTLS",
    "mail_username": " partflow@example.com ",
    "mail_sender": " PartFlow@Example.COM ",
}
_STORED = {
    "mail_host": "smtp.example.com",
    "mail_port": 587,
    "mail_security": "STARTTLS",
    "mail_username": "partflow@example.com",
    "mail_sender": "PartFlow@example.com",
}
_E_HOST = (
    "Enter the mail server as a host name or IP address, such as smtp.example.com, without"
    " spaces or a port."
)
_E_PORT = "The port must be a whole number from 1 to 65535."
_E_SECURITY = "Choose STARTTLS, SSL/TLS or None as the connection security."
_E_USERNAME = (
    "The user name must be at most 254 characters, without line breaks or other control characters."
)
_E_PASSWORD = (
    "The password must be 1 to 1024 characters, without line breaks or other control characters."
)
_E_ADDRESS = (
    "Enter an email address such as name@example.com: no spaces, a domain with a dot after"
    " the @, and at most 254 characters."
)
_E_SENDER = "Enter the sender address PartFlow sends its messages from."
_E_NO_HOST = "Without a mail server, leave the sender address and the user name empty."
_E_PASSWORD_NO_USER = "A password needs a user name."
_E_CLEAR_SIGN_IN = (
    "Signing in to the mail server needs STARTTLS or SSL/TLS, so the password never crosses"
    " the network unencrypted."
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


@pytest.fixture(autouse=True)
def no_mail_server(client: TestClient) -> Iterator[None]:
    """Every test leaves no mail server stored."""
    yield
    _ok(admin_of(client).put(_PATH, json={**_NONE, "mail_password": None}))


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> fake_smtp.Recorder:
    return fake_smtp.install(monkeypatch)


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _values(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "updated_at"}


def _audits(engine: Engine) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT before_data, after_data, metadata, actor_user_id FROM audit_events"
                    " WHERE entity_type = 'ApplicationPolicy' AND entity_id = :section"
                    " ORDER BY id"
                ),
                {"section": _SECTION},
            )
        )


def _audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one())


def _stored(engine: Engine) -> dict[str, Any]:
    """The mail columns, the stored password and the policy row version (a changed
    xmin means an UPDATE ran)."""
    with engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "SELECT xmin::text AS row_xmin, mail_host, mail_port, mail_security,"
                " mail_username, mail_sender,"
                " (SELECT password FROM mail_server_credentials) AS mail_password"
                " FROM application_policy"
            )
        ).one()
    return dict(row._mapping)


def _answer(stored: dict[str, Any], password_set: bool) -> dict[str, Any]:
    return {**stored, "mail_password_set": password_set}


# ---------------------------------------------------------------------------
# GET / PUT
# ---------------------------------------------------------------------------


def test_no_mail_server_is_set_up_at_first(client: TestClient) -> None:
    policy = _ok(admin_of(client).get(_PATH))
    assert set(policy) == {*_NONE, "mail_password_set", "updated_at"}
    assert _values(policy) == _answer(_NONE, False)
    # Every signed-in User reads it; nobody else.
    assert _values(_ok(client_as(client).get(_PATH))) == _answer(_NONE, False)
    assert anonymous_client(client).get(_PATH).status_code == 401


def test_a_whole_server_is_stored_canonical_and_audited_without_the_password(
    client: TestClient, db_engine: Engine
) -> None:
    before = len(_audits(db_engine))
    answered = _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_password": _PASSWORD}))
    assert _values(answered) == _answer(_STORED, True)
    assert _PASSWORD not in str(answered)
    assert _values(_ok(client_as(client).get(_PATH))) == _answer(_STORED, True)
    stored = _stored(db_engine)
    assert stored["mail_password"] == _PASSWORD
    assert {key: stored[key] for key in _STORED} == _STORED

    rows = _audits(db_engine)
    assert len(rows) == before + 1
    before_data, after_data, metadata, actor = rows[-1]
    assert before_data == {**_NONE, "mail_password_set": False}
    assert after_data == {**_STORED, "mail_password_set": True}
    assert metadata == {"mail_password_changed": True}
    assert actor == admin_of(client).user_id
    assert _PASSWORD not in str(tuple(rows[-1]))


def test_an_identical_put_writes_nothing(client: TestClient, db_engine: Engine) -> None:
    _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_password": _PASSWORD}))
    stored = _stored(db_engine)
    count = _audit_count(db_engine)
    # Left out, the password is kept; the same password again is no change either.
    _ok(admin_of(client).put(_PATH, json=_STORED))
    _ok(admin_of(client).put(_PATH, json={**_STORED, "mail_password": _PASSWORD}))
    assert _stored(db_engine) == stored
    assert _audit_count(db_engine) == count


def test_the_password_is_replaced_cleared_and_dropped_with_the_user_name(
    client: TestClient, db_engine: Engine
) -> None:
    _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_password": _PASSWORD}))

    # A new password with nothing else changed is one audited change.
    _ok(admin_of(client).put(_PATH, json={**_STORED, "mail_password": "app-password-2"}))
    assert _stored(db_engine)["mail_password"] == "app-password-2"
    before_data, after_data, metadata, _ = _audits(db_engine)[-1]
    assert before_data == after_data == {**_STORED, "mail_password_set": True}
    assert metadata == {"mail_password_changed": True}

    # null clears it.
    answered = _ok(admin_of(client).put(_PATH, json={**_STORED, "mail_password": None}))
    assert answered["mail_password_set"] is False
    assert _stored(db_engine)["mail_password"] is None

    # Without a user name, a kept password is dropped.
    _ok(admin_of(client).put(_PATH, json={**_STORED, "mail_password": _PASSWORD}))
    answered = _ok(admin_of(client).put(_PATH, json={**_STORED, "mail_username": "  "}))
    assert (answered["mail_username"], answered["mail_password_set"]) == (None, False)
    assert _stored(db_engine)["mail_password"] is None


def test_removing_the_server_keeps_nothing_but_port_and_security(
    client: TestClient, db_engine: Engine
) -> None:
    _ok(
        admin_of(client).put(
            _PATH,
            json={**_SERVER, "mail_security": "TLS", "mail_port": 465, "mail_password": _PASSWORD},
        )
    )
    answered = _ok(
        admin_of(client).put(
            _PATH,
            json={
                "mail_host": "",
                "mail_port": 465,
                "mail_security": "TLS",
                "mail_username": None,
                "mail_sender": None,
            },
        )
    )
    assert _values(answered) == _answer({**_NONE, "mail_port": 465, "mail_security": "TLS"}, False)
    assert _stored(db_engine)["mail_password"] is None


def test_every_security_and_an_address_host_are_admitted(client: TestClient) -> None:
    for security, port, username in (("TLS", 465, "u"), ("NONE", 25, None), ("STARTTLS", 1, "u")):
        answered = _ok(
            admin_of(client).put(
                _PATH,
                json={
                    **_SERVER,
                    "mail_host": "192.168.10.25",
                    "mail_security": security,
                    "mail_port": port,
                    "mail_username": username,
                },
            )
        )
        assert (answered["mail_security"], answered["mail_port"]) == (security, port)
    answered = _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_host": "FD00::25"}))
    assert answered["mail_host"] == "fd00::25"


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        pytest.param({"mail_host": "smtp example.com"}, _E_HOST, id="host-space"),
        pytest.param({"mail_host": "smtp.example.com:587"}, _E_HOST, id="host-with-port"),
        pytest.param({"mail_host": "fd00::25::1"}, _E_HOST, id="host-bad-ipv6"),
        pytest.param({"mail_host": "smtp/example"}, _E_HOST, id="host-slash"),
        pytest.param({"mail_host": "smtp.exämple.com"}, _E_HOST, id="host-non-ascii"),
        pytest.param({"mail_host": "a" * 254}, _E_HOST, id="host-too-long"),
        pytest.param({"mail_port": 0}, _E_PORT, id="port-zero"),
        pytest.param({"mail_port": 65536}, _E_PORT, id="port-too-big"),
        pytest.param({"mail_security": "SSL"}, _E_SECURITY, id="security-unknown"),
        pytest.param({"mail_security": "starttls"}, _E_SECURITY, id="security-lowercase"),
        pytest.param({"mail_username": "a\nb"}, _E_USERNAME, id="username-newline"),
        pytest.param({"mail_username": "u" * 255}, _E_USERNAME, id="username-too-long"),
        pytest.param({"mail_sender": "partflow"}, _E_ADDRESS, id="sender-not-an-address"),
        pytest.param({"mail_sender": None}, _E_SENDER, id="sender-missing"),
        pytest.param({"mail_sender": "  "}, _E_SENDER, id="sender-blank"),
        pytest.param({"mail_host": None}, _E_NO_HOST, id="fields-without-host"),
        pytest.param(
            {"mail_username": None, "mail_password": "x"}, _E_PASSWORD_NO_USER, id="password-alone"
        ),
        pytest.param({"mail_password": ""}, _E_PASSWORD, id="password-empty"),
        pytest.param({"mail_password": "a\tb"}, _E_PASSWORD, id="password-tab"),
        pytest.param({"mail_security": "NONE"}, _E_CLEAR_SIGN_IN, id="sign-in-in-the-clear"),
    ],
)
def test_refused_values_write_nothing(
    client: TestClient, db_engine: Engine, change: dict[str, Any], detail: str
) -> None:
    stored = _stored(db_engine)
    count = _audit_count(db_engine)
    response = admin_of(client).put(_PATH, json={**_SERVER, **change})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == detail
    assert _stored(db_engine) == stored
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({**_SERVER, "mail_port": "587"}, id="port-string"),
        pytest.param({**_SERVER, "mail_port": 587.0}, id="port-float"),
        pytest.param({**_SERVER, "mail_port": True}, id="port-bool"),
        pytest.param({k: v for k, v in _SERVER.items() if k != "mail_sender"}, id="missing-key"),
        pytest.param({**_SERVER, "mail_password_set": True}, id="extra-key"),
        pytest.param({**_SERVER, "mail_password": "p" * 1025}, id="password-too-long"),
    ],
)
def test_malformed_bodies_are_refused_by_the_schema(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    stored = _stored(db_engine)
    assert admin_of(client).put(_PATH, json=body).status_code == 422
    assert _stored(db_engine) == stored


# ---------------------------------------------------------------------------
# POST /api/policies/mail-server/test
# ---------------------------------------------------------------------------


def test_the_test_message_goes_through_the_stored_server(
    client: TestClient, db_engine: Engine, smtp: fake_smtp.Recorder
) -> None:
    _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_password": _PASSWORD}))
    count = _audit_count(db_engine)
    stored = _stored(db_engine)

    answered = _ok(admin_of(client).post(_TEST_PATH, json={"to": "  Ann@Example.ORG "}))
    assert answered == {"sent_to": "Ann@example.org"}
    [connection] = smtp.connections
    assert (connection.host, connection.port) == ("smtp.example.com", 587)
    assert ("login", "partflow@example.com", _PASSWORD) in connection.calls
    [message] = connection.sent
    assert message["To"] == "Ann@example.org"
    assert message["From"] == "PartFlow <PartFlow@example.com>"
    assert message["Subject"] == "PartFlow test message"
    assert "This test message was sent from PartFlow by Test " in message.get_content()
    # Sending writes nothing.
    assert _audit_count(db_engine) == count
    assert _stored(db_engine) == stored


def test_without_a_server_nothing_is_sent(client: TestClient, smtp: fake_smtp.Recorder) -> None:
    response = admin_of(client).post(_TEST_PATH, json={"to": "ann@example.org"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == MAIL_SERVER_NOT_SET_UP_MESSAGE
    assert smtp.connections == []


def test_a_refusing_server_answers_with_its_reason(
    client: TestClient, db_engine: Engine, smtp: fake_smtp.Recorder
) -> None:
    _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_password": _PASSWORD}))
    count = _audit_count(db_engine)
    smtp.fail("login", smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"))
    response = admin_of(client).post(_TEST_PATH, json={"to": "ann@example.org"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "The mail server refused the user name or password."
    assert _PASSWORD not in response.text
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize(
    ("body", "status"),
    [
        pytest.param({"to": "not an address"}, 422, id="invalid"),
        pytest.param({"to": "  "}, 422, id="blank"),
        pytest.param({}, 422, id="missing"),
        pytest.param({"to": "ann@example.org", "cc": "x@example.org"}, 422, id="extra"),
    ],
)
def test_the_test_address_is_validated_before_anything_is_sent(
    client: TestClient, smtp: fake_smtp.Recorder, body: dict[str, Any], status: int
) -> None:
    _ok(admin_of(client).put(_PATH, json={**_SERVER, "mail_password": _PASSWORD}))
    assert admin_of(client).post(_TEST_PATH, json=body).status_code == status
    assert smtp.connections == []
