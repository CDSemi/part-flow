"""Unit tests for the SMTP client of the outgoing mail server (``app.infrastructure.mail``).

Phase 17 (owner decision of 2026-11-13). Against the stand-in of
``tests/fake_smtp.py`` — nothing leaves the process:

- each connection security opens the right connection: STARTTLS a plain
  one upgraded before the sign-in, TLS an encrypted one from the start,
  NONE a plain one and never a sign-in; the timeout set in Administration
  (owner decisions of 2026-11-17) and a verifying TLS context are always
  passed;
- the message carries PartFlow as the sender name, the recipient, the
  subject, a date, a message id in the sender's domain and the body;
- every failure becomes one actionable sentence (``MailDeliveryError``)
  that never contains the password.
"""

import smtplib
import socket
import ssl

import pytest

from app.domain.enums import MailSecurity
from app.infrastructure import mail
from tests import fake_smtp

_PASSWORD = "s3cret-mail-password"
_SERVER = mail.SmtpServer(
    host="smtp.example.com",
    port=587,
    security=MailSecurity.STARTTLS,
    username="partflow@example.com",
    password=_PASSWORD,
    sender="partflow@example.com",
    timeout_seconds=20,
)
_MESSAGE = mail.OutgoingMessage(to="ann@example.org", subject="Hello", body="Body text\n")


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> fake_smtp.Recorder:
    return fake_smtp.install(monkeypatch)


def test_starttls_upgrades_signs_in_and_sends(smtp: fake_smtp.Recorder) -> None:
    mail.send_message(_SERVER, _MESSAGE)
    [connection] = smtp.connections
    assert (connection.kind, connection.host, connection.port) == ("SMTP", "smtp.example.com", 587)
    assert connection.timeout == 20
    [starttls, login, send, quit_] = connection.calls
    assert starttls[0] == "starttls"
    assert isinstance(starttls[1], ssl.SSLContext)
    assert starttls[1].verify_mode is ssl.CERT_REQUIRED
    assert starttls[1].check_hostname is True
    assert login == ("login", "partflow@example.com", _PASSWORD)
    assert (send, quit_) == ("send", "quit")
    [message] = connection.sent
    assert message["From"] == "PartFlow <partflow@example.com>"
    assert message["To"] == "ann@example.org"
    assert message["Subject"] == "Hello"
    assert message["Date"]
    assert str(message["Message-ID"]).endswith("@example.com>")
    assert message.get_content() == "Body text\n"


def test_tls_encrypts_from_the_start(smtp: fake_smtp.Recorder) -> None:
    mail.send_message(
        _SERVER._replace(security=MailSecurity.TLS, port=465, timeout_seconds=45), _MESSAGE
    )
    [connection] = smtp.connections
    assert (connection.kind, connection.port, connection.timeout) == ("SMTP_SSL", 465, 45)
    assert isinstance(connection.context, ssl.SSLContext)
    assert connection.context.verify_mode is ssl.CERT_REQUIRED
    assert [call[0] if isinstance(call, tuple) else call for call in connection.calls] == [
        "login",
        "send",
        "quit",
    ]


def test_none_sends_in_the_clear_without_signing_in(smtp: fake_smtp.Recorder) -> None:
    server = _SERVER._replace(security=MailSecurity.NONE, port=25, username=None, password=None)
    mail.send_message(server, _MESSAGE)
    [connection] = smtp.connections
    assert (connection.kind, connection.context) == ("SMTP", None)
    assert connection.calls == ["send", "quit"]


def test_no_user_name_means_no_sign_in(smtp: fake_smtp.Recorder) -> None:
    mail.send_message(_SERVER._replace(username=None, password=None), _MESSAGE)
    [connection] = smtp.connections
    assert [call[0] if isinstance(call, tuple) else call for call in connection.calls] == [
        "starttls",
        "send",
        "quit",
    ]


def test_a_non_ascii_body_is_sent(smtp: fake_smtp.Recorder) -> None:
    mail.send_message(_SERVER, _MESSAGE._replace(body="Administration → Settings\n"))
    assert smtp.sent[0].get_content() == "Administration → Settings\n"


_SENDER_REFUSED = smtplib.SMTPSenderRefused(553, b"5.7.1 Sender not allowed", "x@example.com")


@pytest.mark.parametrize(
    ("stage", "error", "expected"),
    [
        (
            "login",
            smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed"),
            "The mail server refused the user name or password.",
        ),
        (
            "starttls",
            smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server."),
            "The mail server does not offer STARTTLS. Choose SSL/TLS or None as the connection"
            " security, with the port that goes with it.",
        ),
        (
            "login",
            smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported by server."),
            "The mail server does not accept a sign-in. Leave the user name and password empty.",
        ),
        (
            "login",
            smtplib.SMTPException("No suitable authentication method found."),
            "The mail server offers no sign-in method PartFlow supports.",
        ),
        (
            "send",
            smtplib.SMTPRecipientsRefused({"ann@example.org": (550, b"No such user")}),
            "The mail server refused to deliver to ann@example.org.",
        ),
        (
            "send",
            _SENDER_REFUSED,
            "The mail server refused the sender address partflow@example.com:"
            " 553 5.7.1 Sender not allowed",
        ),
        (
            "send",
            smtplib.SMTPDataError(554, b"5.6.0 Message\r\nrejected\x00 for policy"),
            "The mail server refused the message: 554 5.6.0 Message rejected for policy",
        ),
        (
            "send",
            smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
            "The mail server closed the connection unexpectedly. Check the connection security"
            " and the port.",
        ),
        (
            "connect",
            ssl.SSLCertVerificationError("certificate verify failed"),
            "The mail server's certificate could not be verified (certificate verify failed).",
        ),
        (
            "starttls",
            ssl.SSLError("wrong version number"),
            "The secure connection to the mail server failed. Check that the connection security"
            " matches the port (SSL/TLS usually 465, STARTTLS usually 587).",
        ),
        (
            "connect",
            socket.gaierror(-2, "Name or service not known"),
            "PartFlow could not find the mail server smtp.example.com. Check the host name.",
        ),
        (
            "connect",
            TimeoutError("timed out"),
            "The mail server smtp.example.com on port 587 did not answer within 20 seconds."
            " Check the host name, the port and that the server accepts connections from"
            " PartFlow.",
        ),
        (
            "connect",
            ConnectionRefusedError(111, "Connection refused"),
            "The mail server smtp.example.com on port 587 refused the connection. Check the port.",
        ),
        (
            "connect",
            OSError(113, "No route to host"),
            "The connection to the mail server smtp.example.com on port 587 failed: No route to"
            " host.",
        ),
    ],
)
def test_every_failure_is_one_actionable_sentence(
    smtp: fake_smtp.Recorder, stage: str, error: BaseException, expected: str
) -> None:
    smtp.fail(stage, error)
    with pytest.raises(mail.MailDeliveryError) as raised:
        mail.send_message(_SERVER, _MESSAGE)
    assert str(raised.value) == expected
    assert _PASSWORD not in str(raised.value)
    assert raised.value.__cause__ is error


def test_a_certificate_error_names_the_reason_the_server_gave(smtp: fake_smtp.Recorder) -> None:
    error = ssl.SSLCertVerificationError("certificate verify failed")
    error.verify_message = "self-signed certificate"
    smtp.fail("connect", error)
    with pytest.raises(mail.MailDeliveryError, match=r"\(self-signed certificate\)\.$"):
        mail.send_message(_SERVER, _MESSAGE)


def test_a_long_server_answer_is_cut_short(smtp: fake_smtp.Recorder) -> None:
    smtp.fail("send", smtplib.SMTPDataError(554, b"x" * 1000))
    with pytest.raises(mail.MailDeliveryError) as raised:
        mail.send_message(_SERVER, _MESSAGE)
    assert str(raised.value) == "The mail server refused the message: 554 " + "x" * 200
