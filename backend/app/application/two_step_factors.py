"""Stored factors and open challenges of two-step sign-in (Phase 17).

The data rules behind ``app.application.authentication`` (the second step
of a sign-in) and ``app.application.two_step`` (a User managing their own
two-step sign-in, an administrator or the recovery command turning it off).
This module never commits; its callers own the transaction and the lock
order: the ``user_credentials`` row, then the User's open challenge, then
the authenticator app row, then the recovery codes. Every number comes
from the two-step policy of Administration → Settings
(``policies.TwoStepPolicy``), which the callers read.

- Two-step sign-in is on exactly while the User has a confirmed
  authenticator app (``user_totp_factors.confirmed_at``); recovery codes
  exist only while it is on.
- A challenge is the open second step of one sign-in: the browser holds an
  opaque token in an HttpOnly cookie and only its SHA-256 digest is stored.
  It is open for the policy's window from the password step (judged with
  the policy current at each step), until completed, failed (the policy's
  wrong codes per sign-in, or a lock), replaced by a newer password step in
  the same browser, or reset (two-step sign-in turned off). Rows are never
  deleted at runtime.
"""

import datetime
import hashlib
import hmac
import secrets
from collections.abc import Iterable
from typing import Any, Final, NamedTuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.application import one_time_codes, policies, user_access
from app.domain.enums import SecondStepMethod, SignInChallengeEndReason
from app.infrastructure.models import (
    User,
    UserRecoveryCode,
    UserSignInChallenge,
    UserTotpFactor,
)

#: The challenge cookie: the raw token, never logged.
SIGN_IN_COOKIE: Final = "partflow_sign_in"
#: Longest cookie value ever looked up (issued tokens have 43 characters).
MAX_TOKEN_LENGTH: Final = 64


class OpenedChallenge(NamedTuple):
    """A new second step: the raw token for the cookie only, and what it offers."""

    token: str
    methods: tuple[SecondStepMethod, ...]
    email_hint: str | None
    expires_at: datetime.datetime


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def usable_token(token: object) -> str | None:
    """A cookie value worth looking up, or None (absent, oversized, non-ASCII)."""
    if isinstance(token, str) and 0 < len(token) <= MAX_TOKEN_LENGTH and token.isascii():
        return token
    return None


def is_enabled(session: Session, user_id: int) -> bool:
    """Whether the User signs in with a second step (a confirmed authenticator app)."""
    return bool(
        session.scalar(
            select(func.count())
            .select_from(UserTotpFactor)
            .where(UserTotpFactor.user_id == user_id, UserTotpFactor.confirmed_at.is_not(None))
        )
    )


def enabled_users(session: Session, user_ids: Iterable[int]) -> set[int]:
    """Which of ``user_ids`` have two-step sign-in on (one query)."""
    ids = list(user_ids)
    if not ids:
        return set()
    return set(
        session.scalars(
            select(UserTotpFactor.user_id).where(
                UserTotpFactor.user_id.in_(ids), UserTotpFactor.confirmed_at.is_not(None)
            )
        )
    )


def unused_recovery_codes(session: Session, user_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        )
        or 0
    )


def email_target(session: Session, user_id: int) -> str | None:
    """The User's address when a code can be sent there now (an outgoing mail server is set up)."""
    email = session.scalar(select(User.email).where(User.id == user_id))
    if email is None or policies.read_mail_server(session) is None:
        return None
    return email


def available_methods(session: Session, user_id: int) -> tuple[SecondStepMethod, ...]:
    methods = [SecondStepMethod.TOTP]
    if unused_recovery_codes(session, user_id):
        methods.append(SecondStepMethod.RECOVERY_CODE)
    if email_target(session, user_id) is not None:
        methods.append(SecondStepMethod.EMAIL_CODE)
    return tuple(methods)


def snapshot(session: Session, user_id: int) -> dict[str, Any]:
    """The audited two-step facet of a User: never a secret, a code or a digest."""
    return {
        "two_step": is_enabled(session, user_id),
        "recovery_codes": unused_recovery_codes(session, user_id),
    }


def is_open(
    challenge: UserSignInChallenge, now: datetime.datetime, window: datetime.timedelta
) -> bool:
    return challenge.ended_at is None and challenge.created_at + window > now


def find_challenge(session: Session, token: str | None) -> UserSignInChallenge | None:
    """The challenge ``token`` names, unlocked (it may be ended or expired)."""
    usable = usable_token(token)
    if usable is None:
        return None
    return session.scalars(
        select(UserSignInChallenge).where(UserSignInChallenge.token_digest == _digest(usable))
    ).one_or_none()


def lock_challenge(session: Session, challenge_id: int) -> UserSignInChallenge:
    challenge = session.get(
        UserSignInChallenge, challenge_id, with_for_update=True, populate_existing=True
    )
    assert challenge is not None  # rows are never deleted at runtime
    return challenge


def end_challenge(challenge: UserSignInChallenge, reason: SignInChallengeEndReason) -> None:
    challenge.ended_at = func.now()
    challenge.end_reason = reason.value


def open_challenge(
    session: Session,
    user_id: int,
    now: datetime.datetime,
    replaced_token: str | None,
    window: datetime.timedelta,
) -> OpenedChallenge:
    """INSERT a new challenge; an open one ``replaced_token`` names ends REPLACED."""
    replaced = find_challenge(session, replaced_token)
    if replaced is not None and replaced.ended_at is None:
        session.execute(
            update(UserSignInChallenge)
            .where(UserSignInChallenge.id == replaced.id, UserSignInChallenge.ended_at.is_(None))
            .values(ended_at=func.now(), end_reason=SignInChallengeEndReason.REPLACED.value)
            .execution_options(synchronize_session=False)
        )
    token = secrets.token_urlsafe(32)
    session.add(UserSignInChallenge(user_id=user_id, token_digest=_digest(token), created_at=now))
    session.flush()
    target = email_target(session, user_id)
    return OpenedChallenge(
        token=token,
        methods=available_methods(session, user_id),
        email_hint=None if target is None else one_time_codes.mask_email_address(target),
        expires_at=now + window,
    )


def end_open_challenges(session: Session, user_id: int, reason: SignInChallengeEndReason) -> None:
    session.execute(
        update(UserSignInChallenge)
        .where(UserSignInChallenge.user_id == user_id, UserSignInChallenge.ended_at.is_(None))
        .values(ended_at=func.now(), end_reason=reason.value)
        .execution_options(synchronize_session=False)
    )


def lock_totp(session: Session, user_id: int) -> UserTotpFactor | None:
    return session.get(UserTotpFactor, user_id, with_for_update=True, populate_existing=True)


def accept_totp(
    factor: UserTotpFactor | None, code: str, now: datetime.datetime, drift: int
) -> bool:
    """Accept ``code`` from the confirmed app once (its step is remembered)."""
    if factor is None or factor.confirmed_at is None:
        return False
    step = one_time_codes.matching_totp_step(
        factor.secret,
        code,
        one_time_codes.totp_step(now.timestamp()),
        factor.last_used_step,
        drift,
    )
    if step is None:
        return False
    factor.last_used_step = step
    return True


def use_recovery_code(session: Session, user_id: int, code: str) -> bool:
    """Mark the User's unused recovery code ``code`` used; False when there is none."""
    canonical = one_time_codes.canonical_recovery_code(code)
    if canonical is None:
        return False
    row = session.scalars(
        select(UserRecoveryCode)
        .where(
            UserRecoveryCode.user_id == user_id,
            UserRecoveryCode.code_digest == one_time_codes.code_digest(canonical),
            UserRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        return False
    row.used_at = func.now()
    return True


def accept_email_code(challenge: UserSignInChallenge, code: str) -> bool:
    """Accept the code last sent for this challenge, once (whatever its length)."""
    typed = one_time_codes.digits_only(code)
    stored = challenge.email_code_digest
    if typed is None or stored is None:
        return False
    if not hmac.compare_digest(stored, one_time_codes.code_digest(typed)):
        return False
    challenge.email_code_digest = None
    challenge.email_code_sent_at = None
    return True


def replace_recovery_codes(
    session: Session, user_id: int, policy: policies.TwoStepPolicy
) -> list[str]:
    """Delete the User's recovery codes and store the policy's number of new ones.

    Returns them formatted for display, once.
    """
    session.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    codes: set[str] = set()
    while len(codes) < policy.recovery_codes:
        codes.add(one_time_codes.new_recovery_code(policy.recovery_code_length))
    ordered = sorted(codes)
    session.add_all(
        UserRecoveryCode(user_id=user_id, code_digest=one_time_codes.code_digest(code))
        for code in ordered
    )
    session.flush()
    return [one_time_codes.format_recovery_code(code) for code in ordered]


def delete_factors(session: Session, user_id: int) -> None:
    """Turn two-step sign-in off: the app (confirmed or pending) and every recovery code.

    Open second steps end RESET and the wrong-code count restarts. The
    caller holds the credential lock (a User without a password has none).
    """
    session.execute(delete(UserTotpFactor).where(UserTotpFactor.user_id == user_id))
    session.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    end_open_challenges(session, user_id, SignInChallengeEndReason.RESET)
    credential = user_access.lock_credential(session, user_id)
    if credential is not None:
        credential.failed_codes = 0
