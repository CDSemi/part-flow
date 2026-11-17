"""Messages PartFlow sends by email (Phase 17, owner decision of 2026-11-13).

PartFlow sends a message only through the outgoing mail server set in
Administration → Settings (``policies.read_mail_server``) and only for its
own purposes; no production command sends mail. Each send first releases
the request's database connection (``session.rollback()``), so no
transaction or lock is held while the mail server answers, then hands the
message to ``app.infrastructure.mail``. A message that does not leave
raises ``MailNotSentError`` with the server's reason; a missing server
raises ``MailServerNotSetUpError``. Sending writes nothing and is never
audited; the log names the User who sent it, never the address, the
content or a password.

- ``send_test_message`` — Administration → Settings → Send test email
  (``CONFIGURE_SYSTEM_SETTINGS``): one short message to an address the
  administrator types, which proves the stored server works.
"""

import logging
from typing import Final

from sqlalchemy.orm import Session

from app.application import policies, users
from app.application.errors import InvalidInputError, MailNotSentError, MailServerNotSetUpError
from app.application.user_access import Principal
from app.infrastructure import mail

logger = logging.getLogger(__name__)

MAIL_SERVER_NOT_SET_UP_MESSAGE: Final = (
    "No outgoing mail server is set up. An administrator sets one up in Administration →"
    " Settings → Outgoing mail server."
)
_TEST_SUBJECT: Final = "PartFlow test message"
_TEST_BODY: Final = (
    "This test message was sent from PartFlow by {name}.\n"
    "\n"
    "It shows that the outgoing mail server set in Administration → Settings works: PartFlow"
    " can send one-time sign-in codes to users' email addresses.\n"
)


def send(session: Session, message: mail.OutgoingMessage) -> None:
    """Send ``message`` through the stored mail server, outside any transaction."""
    server = policies.read_mail_server(session)
    session.rollback()
    if server is None:
        raise MailServerNotSetUpError(MAIL_SERVER_NOT_SET_UP_MESSAGE)
    try:
        mail.send_message(server, message)
    except mail.MailDeliveryError as error:
        raise MailNotSentError(str(error)) from error


def send_test_message(session: Session, *, to: object, actor: Principal) -> str:
    """Send the test message to ``to``; returns the canonical address it went to."""
    address = users.canonical_email(to)
    if address is None:
        raise InvalidInputError("Enter the email address to send the test message to.")
    message = mail.OutgoingMessage(
        to=address, subject=_TEST_SUBJECT, body=_TEST_BODY.format(name=actor.display_name)
    )
    try:
        send(session, message)
    except (MailServerNotSetUpError, MailNotSentError):
        logger.info("Test message from user %s was not sent", actor.user_id)
        raise
    logger.info("Test message sent by user %s", actor.user_id)
    return address
