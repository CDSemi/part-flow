"""Two-step sign-in of application Users (Phase 17, owner decision of 2026-11-13).

IMPLEMENTATION_ROADMAP Phase 17 item 8: each User may add an
authenticator app (one-time codes, TOTP); it is never required,
Administrators included. With it on, signing in takes the password and then
a second step (``authentication.complete_second_step``): a code from the
app, one of the User's recovery codes, or a code PartFlow emails to the
User's address when an outgoing mail server is set up.

Rules owned here (the stored factors live in ``two_step_factors``; every
number is the two-step policy of Administration → Settings,
``policies.TwoStepPolicy``, read at each step):

- Adding the app: the signed-in User types the password again; PartFlow
  stores a pending secret (open for the policy's setup time) and shows it
  as text, an ``otpauth://`` link and a QR code; the first code from the
  app confirms it. The first confirmation creates the policy's number of
  recovery codes, shown once.
- Removing the app (two-step sign-in off, recovery codes deleted) and
  creating new recovery codes take the password again too. A wrong password
  counts as a failed sign-in (``authentication.confirm_own_password``).
- A user administrator turns another User's two-step sign-in off (the User
  lost the app and the recovery codes) — with ``MANAGE_CORRECTION_PERMISSIONS``
  too when the User's role holds a protected key, as for a password set; the
  recovery command does it from the backend container. Either may also end
  every sign-in of the User (``end_sessions``; owner decision of
  2026-11-17), for a lost device that may be in someone else's hands.
- An emailed code: at most the policy's number per second step, the
  policy's interval apart; the message leaves first, outside any
  transaction, and only then is its digest stored on the challenge.

Every change of the factors appends one ``UPDATED`` audit row on the User
with ``two_step_factors.snapshot`` before and after and the metadata
``{"two_step_change": ...}`` (with ``sessions_ended`` for a reset) — never
a secret, a code or a digest. Using a code at sign-in is logged, never
audited.
"""

import datetime
import logging
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.application import (
    audit,
    authentication,
    authorization,
    one_time_codes,
    outgoing_mail,
    policies,
    two_step_factors,
    user_access,
    users,
)
from app.application.common import commit
from app.application.errors import (
    ConflictError,
    InvalidInputError,
    RecoveryOutcomeUnknownError,
    SecondStepExpiredError,
    UnknownLoginError,
)
from app.application.user_access import Principal
from app.domain.enums import AuditEntityType, AuditEventType, Permission, UserSessionEndReason
from app.infrastructure import mail, qr_code
from app.infrastructure.models import User, UserSignInChallenge, UserTotpFactor

logger = logging.getLogger(__name__)

_ALREADY_ON: Final = (
    "Two-step sign-in is already on. Turn it off first to set up another authenticator app."
)
_NO_PENDING_SETUP: Final = (
    "This setup has expired or was not started. Start again with Set up two-step sign-in."
)
_WRONG_SETUP_CODE: Final = (
    "That code does not match. Type the 6-digit code your authenticator app shows now."
)
_NOT_ON: Final = "Two-step sign-in is off. Recovery codes come with an authenticator app."
_OWN_RESET: Final = (
    "Use Two-step sign-in… in your account menu to turn off your own two-step sign-in."
)
_NO_EMAIL: Final = (
    "PartFlow cannot email you a code: your account has no email address or no outgoing mail"
    " server is set up. Use a recovery code, or ask an administrator."
)
_TOO_MANY_EMAILS: Final = (
    "PartFlow already emailed the most codes one sign-in may send. Use the last code you"
    " received, or sign in again."
)
_RESET_OUTCOME_UNKNOWN: Final = (
    "The connection to the database failed while the change was being saved, so it is"
    " unknown whether two-step sign-in was turned off. Run the same command again."
)
_EMAIL_SUBJECT: Final = "Your PartFlow sign-in code"
_EMAIL_BODY: Final = (
    "Your PartFlow sign-in code is {code}.\n"
    "\n"
    "Type it in the sign-in window. It works once, until {until} UTC.\n"
    "\n"
    "If you did not just sign in to PartFlow, someone else knows your password: change it,"
    " or ask an administrator to set a new one.\n"
)


class TwoStepStatus(NamedTuple):
    """The signed-in User's own two-step sign-in, as answered."""

    enabled: bool
    recovery_codes_left: int
    # The User's own address; None = none.
    email: str | None
    # An emailed code can be sent now (an address and an outgoing mail server).
    email_ready: bool


class TotpSetup(NamedTuple):
    """A pending authenticator app: what the User adds it from."""

    secret: str
    uri: str
    # The QR code of ``uri``: rows of "1" (dark) and "0" (light), no quiet zone.
    qr: list[str]


class EmailCodeSent(NamedTuple):
    sent_to: str
    resend_after_seconds: int
    sends_left: int


class ResetOutcome(NamedTuple):
    """What the recovery command reports after turning two-step sign-in off."""

    user_id: int
    display_name: str
    was_on: bool
    # Every sign-in of the User ended (asked for, and two-step sign-in was on).
    sessions_ended: bool


def _database_now(session: Session) -> datetime.datetime:
    now = session.scalar(select(func.now()))
    assert now is not None  # now() always answers
    return now


def _audit(
    session: Session,
    user_id: int,
    before: dict[str, object],
    change: str,
    *,
    actor_user_id: int | None,
    extra: dict[str, Any] | None = None,
) -> None:
    session.flush()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user_id),
        before_data=before,
        after_data=two_step_factors.snapshot(session, user_id),
        actor_user_id=actor_user_id,
        metadata={"two_step_change": change, **(extra or {})},
    )


def _turn_off(
    session: Session, user_id: int, *, end_sessions: bool, change: str, actor_user_id: int | None
) -> bool:
    """Delete the factors of a User whose credential the caller locked; audit when it was on.

    With ``end_sessions``, every sign-in of the User ends too
    (``TWO_STEP_RESET``) — only when two-step sign-in was on, so the audit
    row records it. Returns whether two-step sign-in was on.
    """
    factor = two_step_factors.lock_totp(session, user_id)
    if factor is None:
        return False
    was_on = factor.confirmed_at is not None
    before = two_step_factors.snapshot(session, user_id)
    two_step_factors.delete_factors(session, user_id)
    if not was_on:
        return False
    if end_sessions:
        user_access.end_user_sessions(session, user_id, UserSessionEndReason.TWO_STEP_RESET)
    extra: dict[str, Any] = {"sessions_ended": end_sessions}
    if actor_user_id is None:
        extra["source"] = "cli"
    _audit(session, user_id, before, change, actor_user_id=actor_user_id, extra=extra)
    return True


def status(session: Session, principal: Principal) -> TwoStepStatus:
    user_id = principal.user_id
    email = session.scalar(select(User.email).where(User.id == user_id))
    return TwoStepStatus(
        enabled=two_step_factors.is_enabled(session, user_id),
        recovery_codes_left=two_step_factors.unused_recovery_codes(session, user_id),
        email=email,
        email_ready=two_step_factors.email_target(session, user_id) is not None,
    )


def begin_totp(session: Session, principal: Principal, *, current_password: str) -> TotpSetup:
    """Store a new pending authenticator app secret; answered once, for setup only."""
    authentication.confirm_own_password(session, principal, current_password)
    factor = two_step_factors.lock_totp(session, principal.user_id)
    if factor is not None and factor.confirmed_at is not None:
        session.rollback()
        raise ConflictError(_ALREADY_ON)
    secret = one_time_codes.new_totp_secret()
    now = _database_now(session)
    if factor is None:
        session.add(UserTotpFactor(user_id=principal.user_id, secret=secret, created_at=now))
    else:
        factor.secret = secret
        factor.created_at = now
        factor.last_used_step = None
    commit(session, {})
    uri = one_time_codes.totp_uri(secret, principal.login_name)
    logger.info("Two-step setup started for user %s", principal.user_id)
    return TotpSetup(secret=one_time_codes.totp_secret_text(secret), uri=uri, qr=qr_code.rows(uri))


def confirm_totp(session: Session, principal: Principal, *, code: str) -> list[str] | None:
    """Confirm the pending app with its first code; returns new recovery codes on the first."""
    user_access.lock_credential(session, principal.user_id)
    user_access.recheck_actor(session, principal)
    factor = two_step_factors.lock_totp(session, principal.user_id)
    now = _database_now(session)
    policy = policies.read_two_step_policy(session)
    if (
        factor is None
        or factor.confirmed_at is not None
        or factor.created_at + policy.setup_time <= now
    ):
        session.rollback()
        raise ConflictError(_NO_PENDING_SETUP)
    step = one_time_codes.matching_totp_step(
        factor.secret,
        code,
        one_time_codes.totp_step(now.timestamp()),
        None,
        policy.clock_drift_steps,
    )
    if step is None:
        session.rollback()
        raise InvalidInputError(_WRONG_SETUP_CODE)
    before = two_step_factors.snapshot(session, principal.user_id)
    factor.confirmed_at = now
    factor.last_used_step = step
    codes = (
        None
        if two_step_factors.unused_recovery_codes(session, principal.user_id)
        else two_step_factors.replace_recovery_codes(session, principal.user_id, policy)
    )
    _audit(session, principal.user_id, before, "TOTP_ADDED", actor_user_id=principal.user_id)
    commit(session, {})
    logger.info("Two-step sign-in turned on for user %s", principal.user_id)
    return codes


def remove_totp(session: Session, principal: Principal, *, current_password: str) -> TwoStepStatus:
    """Turn the signed-in User's two-step sign-in off; a no-op when it is off."""
    authentication.confirm_own_password(session, principal, current_password)
    factor = two_step_factors.lock_totp(session, principal.user_id)
    if factor is None:
        session.rollback()
        return status(session, principal)
    was_on = factor.confirmed_at is not None
    before = two_step_factors.snapshot(session, principal.user_id)
    two_step_factors.delete_factors(session, principal.user_id)
    if was_on:
        _audit(session, principal.user_id, before, "TOTP_REMOVED", actor_user_id=principal.user_id)
    commit(session, {})
    if was_on:
        logger.info("Two-step sign-in turned off by user %s", principal.user_id)
    return status(session, principal)


def create_recovery_codes(
    session: Session, principal: Principal, *, current_password: str
) -> list[str]:
    """Replace the signed-in User's recovery codes; the old ones stop working."""
    authentication.confirm_own_password(session, principal, current_password)
    two_step_factors.lock_totp(session, principal.user_id)
    if not two_step_factors.is_enabled(session, principal.user_id):
        session.rollback()
        raise ConflictError(_NOT_ON)
    before = two_step_factors.snapshot(session, principal.user_id)
    codes = two_step_factors.replace_recovery_codes(
        session, principal.user_id, policies.read_two_step_policy(session)
    )
    _audit(
        session,
        principal.user_id,
        before,
        "RECOVERY_CODES_CREATED",
        actor_user_id=principal.user_id,
    )
    commit(session, {})
    return codes


def reset_for_user(
    session: Session, user_id: int, *, end_sessions: bool, actor: Principal
) -> users.UserView:
    """A user administrator turns another User's two-step sign-in off.

    With ``end_sessions`` every sign-in of the User ends too. A User whose
    two-step sign-in is already off is answered unchanged.
    """
    if actor.user_id == user_id:
        raise ConflictError(_OWN_RESET)
    acting = user_access.acting_user(session, actor)
    user_access.judge(
        session, lambda: authorization.require(acting, (Permission.MANAGE_USERS_AND_ROLES,))
    )
    user = users.lock_user(session, user_id)
    if user_access.role_holds_protected(session, user.role_id):
        user_access.judge(
            session,
            lambda: authorization.require_guard(acting, authorization.PROTECTED_USER_MESSAGE),
        )
    user_access.lock_credential(session, user.id)
    was_on = _turn_off(
        session,
        user.id,
        end_sessions=end_sessions,
        change="RESET_BY_ADMINISTRATOR",
        actor_user_id=actor.user_id,
    )
    view = users.user_view(session, user)
    commit(session, users.USER_CONFLICTS)
    if was_on:
        logger.info(
            "Two-step sign-in of user %s turned off by user %s (signed out: %s)",
            user.id,
            actor.user_id,
            end_sessions,
        )
    return view


def reset_for_login(session: Session, login_name: str, *, end_sessions: bool) -> ResetOutcome:
    """Recovery command: turn off the two-step sign-in of the User ``login_name``.

    With ``end_sessions`` every sign-in of the User ends too.
    """
    try:
        login = users.canonical_login_name(login_name)
    except InvalidInputError as exc:
        raise UnknownLoginError(f"No user has the login name {login_name.strip()}.") from exc
    user_access.acquire_user_administration_lock(session)
    user = session.scalars(
        select(User)
        .where(User.login_name == login)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if user is None:
        session.rollback()
        raise UnknownLoginError(f"No user has the login name {login}.")
    user_access.lock_credential(session, user.id)
    was_on = _turn_off(
        session, user.id, end_sessions=end_sessions, change="RESET_BY_CLI", actor_user_id=None
    )
    outcome = ResetOutcome(
        user_id=user.id,
        display_name=user.display_name,
        was_on=was_on,
        sessions_ended=was_on and end_sessions,
    )
    try:
        commit(session, {})
    except IntegrityError:
        raise
    except DBAPIError as exc:
        raise RecoveryOutcomeUnknownError(_RESET_OUTCOME_UNKNOWN) from exc
    return outcome


def send_email_code(session: Session, token: str | None) -> EmailCodeSent:
    """Email a sign-in code for the open second step ``token`` names.

    The code works until the second step expires. Whether another may be
    sent is judged again, with the policy current then, after the message
    left and the challenge is locked.
    """
    challenge = two_step_factors.find_challenge(session, token)
    now = _database_now(session)
    policy = policies.read_two_step_policy(session)
    if challenge is None or not two_step_factors.is_open(challenge, now, policy.window):
        session.rollback()
        raise SecondStepExpiredError(authentication.SECOND_STEP_EXPIRED_MESSAGE)
    challenge_id, user_id = challenge.id, challenge.user_id
    until = challenge.created_at + policy.window
    refusal = _resend_refusal(challenge, now, policy)
    if refusal is not None:
        session.rollback()
        raise ConflictError(refusal)
    address = two_step_factors.email_target(session, user_id)
    if address is None:
        session.rollback()
        raise ConflictError(_NO_EMAIL)
    code = one_time_codes.new_email_code(policy.email_code_digits)
    outgoing_mail.send(
        session,
        mail.OutgoingMessage(
            to=address,
            subject=_EMAIL_SUBJECT,
            body=_EMAIL_BODY.format(
                code=code, until=f"{until.astimezone(datetime.UTC):%Y-%m-%d %H:%M}"
            ),
        ),
    )
    # Stored only once the message left; the lock order of a sign-in.
    user_access.lock_credential(session, user_id)
    challenge = two_step_factors.lock_challenge(session, challenge_id)
    now = _database_now(session)
    policy = policies.read_two_step_policy(session)
    if not two_step_factors.is_open(challenge, now, policy.window):
        session.rollback()
        raise SecondStepExpiredError(authentication.SECOND_STEP_EXPIRED_MESSAGE)
    if challenge.email_codes_sent >= policy.email_codes:
        # A concurrent send took the last one while this message travelled.
        session.rollback()
        raise ConflictError(_TOO_MANY_EMAILS)
    challenge.email_code_digest = one_time_codes.code_digest(code)
    challenge.email_code_sent_at = now
    challenge.email_codes_sent += 1
    sends_left = max(0, policy.email_codes - challenge.email_codes_sent)
    commit(session, {})
    logger.info("Sign-in code emailed for user %s", user_id)
    return EmailCodeSent(
        sent_to=one_time_codes.mask_email_address(address),
        resend_after_seconds=policy.email_interval_seconds,
        sends_left=sends_left,
    )


def _resend_refusal(
    challenge: UserSignInChallenge, now: datetime.datetime, policy: policies.TwoStepPolicy
) -> str | None:
    if challenge.email_codes_sent >= policy.email_codes:
        return _TOO_MANY_EMAILS
    last_sent_at = challenge.email_code_sent_at
    if last_sent_at is not None and now - last_sent_at < policy.email_interval:
        wait = int((policy.email_interval - (now - last_sent_at)).total_seconds()) + 1
        return f"Wait {wait} seconds before asking for another code."
    return None
