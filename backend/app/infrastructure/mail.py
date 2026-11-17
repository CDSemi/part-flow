"""Sending one message through the outgoing mail server (Phase 17).

PartFlow's SMTP client: ``smtplib`` from the standard library, one
connection per message, the certificate verified against the system's
trusted authorities (``ssl.create_default_context``) and the timeout set in
Administration → Settings (``SmtpServer.timeout_seconds``) per network
operation. The connection security is the one
configured there too (``MailSecurity``): STARTTLS
upgrades a plain connection before anything is sent, TLS encrypts from the
first byte, NONE sends in the clear and never signs in (the policy refuses
a user name with it).

Every failure surfaces as ``MailDeliveryError`` carrying one sentence an
administrator can act on; the password never appears in it, and the
server's own answer is quoted only after removing control characters and
cutting it short.
"""

import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Final, NamedTuple

from app.domain.enums import MailSecurity

#: The display name of the sender address.
SENDER_NAME: Final = "PartFlow"
_MAX_QUOTED: Final = 200


class SmtpServer(NamedTuple):
    """The outgoing mail server as stored (the password included: for sending only)."""

    host: str
    port: int
    security: MailSecurity
    username: str | None
    password: str | None
    sender: str
    # Seconds each connect, read or write may take.
    timeout_seconds: int


class OutgoingMessage(NamedTuple):
    """One plain-text message to one recipient."""

    to: str
    subject: str
    body: str


class MailDeliveryError(Exception):
    """The mail server did not take the message; the text says why."""


def _quoted(answer: object) -> str:
    text = answer.decode("utf-8", "replace") if isinstance(answer, bytes) else str(answer)
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return clean[:_MAX_QUOTED]


def _build(server: SmtpServer, message: OutgoingMessage) -> EmailMessage:
    mail = EmailMessage()
    mail["From"] = formataddr((SENDER_NAME, server.sender))
    mail["To"] = message.to
    mail["Subject"] = message.subject
    mail["Date"] = formatdate(usegmt=True)
    # An explicit domain: never a DNS lookup of the container's own name.
    mail["Message-ID"] = make_msgid(domain=server.sender.rpartition("@")[2])
    mail.set_content(message.body)
    return mail


def _connection_failure(server: SmtpServer, error: OSError) -> MailDeliveryError:
    where = f"{server.host} on port {server.port}"
    if isinstance(error, socket.gaierror):
        return MailDeliveryError(
            f"PartFlow could not find the mail server {server.host}. Check the host name."
        )
    if isinstance(error, TimeoutError):
        return MailDeliveryError(
            f"The mail server {where} did not answer within {server.timeout_seconds} seconds."
            " Check the host name, the port and that the server accepts connections from"
            " PartFlow."
        )
    if isinstance(error, ConnectionRefusedError):
        return MailDeliveryError(f"The mail server {where} refused the connection. Check the port.")
    reason = error.strerror or type(error).__name__
    return MailDeliveryError(f"The connection to the mail server {where} failed: {reason}.")


def send_message(server: SmtpServer, message: OutgoingMessage) -> None:
    """Send ``message``; raise ``MailDeliveryError`` when the server does not take it."""
    mail = _build(server, message)
    context = ssl.create_default_context()
    stage = "connect"
    try:
        client: smtplib.SMTP
        if server.security is MailSecurity.TLS:
            client = smtplib.SMTP_SSL(
                server.host, server.port, timeout=server.timeout_seconds, context=context
            )
        else:
            client = smtplib.SMTP(server.host, server.port, timeout=server.timeout_seconds)
        with client:
            if server.security is MailSecurity.STARTTLS:
                stage = "starttls"
                client.starttls(context=context)
            if server.username is not None:
                stage = "login"
                client.login(server.username, server.password or "")
            stage = "send"
            client.send_message(mail)
    except smtplib.SMTPAuthenticationError as error:
        raise MailDeliveryError("The mail server refused the user name or password.") from error
    except smtplib.SMTPNotSupportedError as error:
        if stage == "starttls":
            raise MailDeliveryError(
                "The mail server does not offer STARTTLS. Choose SSL/TLS or None as the"
                " connection security, with the port that goes with it."
            ) from error
        raise MailDeliveryError(
            "The mail server does not accept a sign-in. Leave the user name and password empty."
        ) from error
    except smtplib.SMTPRecipientsRefused as error:
        raise MailDeliveryError(f"The mail server refused to deliver to {message.to}.") from error
    except smtplib.SMTPSenderRefused as error:
        raise MailDeliveryError(
            f"The mail server refused the sender address {server.sender}:"
            f" {error.smtp_code} {_quoted(error.smtp_error)}"
        ) from error
    except smtplib.SMTPResponseException as error:
        raise MailDeliveryError(
            f"The mail server refused the message: {error.smtp_code} {_quoted(error.smtp_error)}"
        ) from error
    except smtplib.SMTPServerDisconnected as error:
        raise MailDeliveryError(
            "The mail server closed the connection unexpectedly. Check the connection security"
            " and the port."
        ) from error
    except smtplib.SMTPException as error:
        if stage == "login":
            raise MailDeliveryError(
                "The mail server offers no sign-in method PartFlow supports."
            ) from error
        raise MailDeliveryError(f"The mail server failed: {_quoted(error)}") from error
    except ssl.SSLCertVerificationError as error:
        reason = getattr(error, "verify_message", None) or "certificate verify failed"
        raise MailDeliveryError(
            f"The mail server's certificate could not be verified ({_quoted(reason)})."
        ) from error
    except ssl.SSLError as error:
        raise MailDeliveryError(
            "The secure connection to the mail server failed. Check that the connection"
            " security matches the port (SSL/TLS usually 465, STARTTLS usually 587)."
        ) from error
    except OSError as error:
        raise _connection_failure(server, error) from error
