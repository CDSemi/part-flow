"""Sign-in for application Users (Phase 14 slice 1; owner decisions OD-P1–OD-P3).

A User — an application account, never a Worker (PROJECT_PROFILE §7) —
signs in with its login name and a password held in
``user_credentials``. A sign-in creates a server-side session: the
browser receives an opaque random token in an HttpOnly cookie and only
its SHA-256 digest is stored, so a database read never yields a usable
cookie. A presented cookie is never adopted: every sign-in issues a new
token (session fixation).

Rules owned here:

- One generic refusal (``SignInFailedError``) for an unknown login name,
  a User without a password, an inactive or locked User, a wrong,
  over-long or unencodable password — the response never tells which; an unknown login
  name costs the same scrypt work (``password_hashing.dummy_verify``).
- Failed attempts are counted per credential under its row lock; at
  the policy's threshold (``>=``) the account locks for the policy's
  duration and the counter restarts. Attempts while locked are neither
  checked, counted, nor extend the lock; a password set clears it.
- Expiry is absolute from sign-in and derived on every request from the
  CURRENT policy (never recorded), so a policy change applies to open
  sessions at their next request; sign-out, a password change or reset
  and deactivation end sessions explicitly.
- A password set by an administrator or the recovery command is
  temporary; the User must replace it while the policy's
  ``require_password_change`` is on (evaluated per request).
- Permissions are read per request from the User's role, so a role or
  grant change applies at the next request.
- Two-step sign-in (Phase 17; ``app.application.two_step``): a User with
  an authenticator app or a passkey is not signed in by the password alone.
  The password step opens a second step (``SecondStepRequired``: a
  challenge whose token travels in its own HttpOnly cookie);
  ``complete_second_step`` signs the User in with a code from the app, a
  recovery code or an emailed code, and
  ``complete_second_step_with_passkey`` with a passkey.
- A passkey also signs its User in alone (``begin_passkey_sign_in`` then
  ``sign_in_with_passkey``): the authenticator verifies the person, so it
  is a full sign-in. Its refusal is one generic answer, like a password's,
  and an inactive, password-less or locked User is refused the same way.
- Two counters (owner decision of 2026-11-17): a correct
  password restarts the password counter (``failed_attempts``), while
  wrong codes count on ``failed_codes``, which only a full sign-in
  restarts, so knowing the password buys no fresh guesses at a code. At
  the two-step policy's threshold the account locks for the sign-in
  policy's lock duration; the policy's wrong codes per sign-in end the
  second step.
- A signed-in User changing their own sign-in methods or email address
  types the password again (``confirm_own_password``); a wrong one is a
  failed attempt too. The previous address hears of an email change.

Every hashing command first reads the plain values it needs, releases
its database connection (``session.rollback()``), hashes or verifies
outside any transaction and lock, then starts its locked phase, which
re-reads what it relies on (a concurrent password set, deactivation or
lock; for an administrator's password set, the actor's own sign-in and
keys). Every role and user write takes the same advisory lock first
(Phase 14 slice 2), so no concurrent administration write can change
the grants, role assignment or activity that re-read saw before
COMMIT. Lock order: the ``partflow:user-administration`` advisory lock
→ the ``users`` row → the ``user_credentials`` row → the
``user_sign_in_challenges`` row → the ``user_totp_factors`` row → the
``user_passkey_setups`` row → the ``user_passkeys`` rows → the
``user_recovery_codes`` rows → INSERT ``user_sessions`` → UPDATE
``user_sessions`` → audit.

Sign-in, sign-out and failures are logged, never audited, and the log
never carries the typed login name, a password, a hash, a token or its
digest. Password changes and sets are audited on the User without any
secret (``{password_set, password_temporary, password_changed_at}``).
"""

import datetime
import hashlib
import logging
import math
import secrets
from collections.abc import Callable
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.application import (
    audit,
    authorization,
    one_time_codes,
    outgoing_mail,
    passkeys,
    password_hashing,
    policies,
    roles,
    two_step_factors,
    user_access,
    users,
)
from app.application.common import commit, flush
from app.application.errors import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    AccountLockedError,
    AuthenticationRequiredError,
    ConflictError,
    InvalidInputError,
    MailNotSentError,
    MailServerNotSetUpError,
    RecoveryOutcomeUnknownError,
    RecoveryUnavailableError,
    SecondStepExpiredError,
    SecondStepFailedError,
    SignInFailedError,
    UnknownLoginError,
)
from app.application.user_access import Principal as Principal
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    Permission,
    SecondStepMethod,
    SignInChallengeEndReason,
    SignInChallengePurpose,
    ThemePreference,
    UserSessionEndReason,
)
from app.domain.password_policy import (
    InvalidPasswordError,
    is_encodable,
    is_over_long,
    normalize_password,
    validate_new_password,
)
from app.domain.user_login import InvalidLoginNameError, normalize_login_name
from app.infrastructure import mail
from app.infrastructure.models import (
    ApplicationPolicy,
    Role,
    RolePermission,
    User,
    UserCredential,
    UserPasskey,
    UserSession,
    UserSignInChallenge,
)

logger = logging.getLogger(__name__)

#: The session cookie: the raw token, never logged.
SESSION_COOKIE: Final = "partflow_session"
#: Longest cookie value ever looked up (issued tokens have 43 characters).
MAX_TOKEN_LENGTH: Final = 64
#: Cookie lifetime of a session that never expires: the browser cap (400 days).
NEVER_EXPIRES_COOKIE_SECONDS: Final = 34_560_000
_POLICY_ID: Final = 1

SIGN_IN_FAILED_MESSAGE: Final = (
    "Sign-in failed. Check your login name and password. If it keeps failing, ask an"
    " administrator — the account may be locked or inactive."
)
_WRONG_CURRENT_PASSWORD: Final = "The current password is not correct."
_SAME_PASSWORD: Final = "Choose a new password that is different from the current one."
_OWN_PASSWORD: Final = "Use Change password to change your own password."
_CHANGED_CONCURRENTLY: Final = (
    "Your password was changed at the same time by someone else. Sign in again."
)
_ACCOUNT_LOCKED: Final = (
    "This account is locked after too many failed attempts. Try again later or ask an"
    " administrator."
)
_NO_ADMINISTRATOR: Final = (
    "PartFlow has no administrator yet. Use Set up PartFlow with the setup token from the"
    " server log."
)
_NO_PASSWORD_YET: Final = (
    "This user has no password yet. An administrator gives it one with Set password… in"
    " Administration → Users."
)
SECOND_STEP_EXPIRED_MESSAGE: Final = "This sign-in has ended or expired. Enter your password again."
PASSKEY_SIGN_IN_FAILED_MESSAGE: Final = (
    "Sign-in with a passkey failed. Use a passkey saved for PartFlow at this address, or sign in"
    " with your login name and password. If it keeps failing, ask an administrator — the account"
    " may be locked or inactive."
)
_TOO_MANY_CODES: Final = "Too many wrong codes. Sign in again with your password."
_WRONG_CODE: Final = "That code is not correct. Check the code and try again."
_RECOVERY_OUTCOME_UNKNOWN: Final = (
    "The connection to the database failed while the reset was being saved, so it is"
    " unknown whether the password was reset. Run the same command again: it sets the"
    " password again either way."
)
_EMAIL_CHANGED_SUBJECT: Final = "Your PartFlow email address was changed"
_EMAIL_CHANGED_BODY: Final = (
    "The email address of your PartFlow account {login} was {change} on {when} UTC.\n"
    "\n"
    "PartFlow sends no more messages to this address. If you did not make this change,"
    " ask an administrator to check your account and set a new password.\n"
)


class RoleRestoreOutcome(NamedTuple):
    """What ``restore-correction-permission-management`` reports."""

    role_id: int
    role_name: str
    # Active Users with a password holding the key after the grant.
    holders: int


class SessionGrant(NamedTuple):
    """A new session: the principal and the raw token for the cookie only."""

    principal: Principal
    token: str


class SecondStepRequired(NamedTuple):
    """A correct password of a User with two-step sign-in: the second step to take.

    ``token`` is the raw challenge token for the cookie only.
    """

    token: str
    methods: tuple[SecondStepMethod, ...]
    email_hint: str | None
    expires_at: datetime.datetime


class PasskeySignInStart(NamedTuple):
    """A sign-in with a passkey alone: the raw token for the cookie only, and the options."""

    token: str
    options: dict[str, Any]
    expires_at: datetime.datetime


class RecoveryOutcome(NamedTuple):
    """What the recovery command reports after a reset."""

    user_id: int
    display_name: str
    is_active: bool


_CORRECTION_MANAGER_EXISTS: Final = (
    "A user who may manage correction permissions already exists. Grant it in"
    " Administration instead."
)
_RESTORE_OUTCOME_UNKNOWN: Final = (
    "The connection to the database failed while the grant was being saved, so it is"
    " unknown whether the role may now manage correction permissions. Run the same command"
    " again: it refuses once a user may manage correction permissions."
)


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _usable_token(token: object) -> str | None:
    """A cookie value worth looking up, or None (absent, oversized, non-ASCII)."""
    if isinstance(token, str) and 0 < len(token) <= MAX_TOKEN_LENGTH and token.isascii():
        return token
    return None


def new_session_token() -> tuple[str, bytes]:
    """A fresh opaque token and the SHA-256 digest that is stored."""
    token = secrets.token_urlsafe(32)
    return token, _digest(token)


def resolve_principal(session: Session, token: str | None) -> Principal | None:
    """The signed-in User of ``token``, or None (unknown, ended, expired, inactive).

    No lock and no write; column selects only, so the request's identity
    map holds no User or policy instance afterwards.
    """
    usable = _usable_token(token)
    if usable is None:
        return None
    return user_access.principal_where(session, UserSession.token_digest == _digest(usable))


def cookie_max_age(principal: Principal) -> int:
    """The cookie's Max-Age: the session's remaining lifetime under the current policy."""
    if principal.session_expires_at is None:
        return NEVER_EXPIRES_COOKIE_SECONDS
    remaining = principal.session_expires_at - datetime.datetime.now(datetime.UTC)
    return max(1, min(NEVER_EXPIRES_COOKIE_SECONDS, math.floor(remaining.total_seconds())))


# ---------------------------------------------------------------------------
# Shared steps
# ---------------------------------------------------------------------------


def _login_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return normalize_login_name(value)
    except InvalidLoginNameError:
        return None


def _cannot_match(password: str) -> bool:
    """Whether ``password`` can match no stored hash: over-long or unencodable."""
    return is_over_long(password) or not is_encodable(password)


def _new_password(raw: str) -> None:
    try:
        validate_new_password(raw)
    except InvalidPasswordError as exc:
        raise InvalidInputError(str(exc)) from exc


def _database_now(session: Session) -> datetime.datetime:
    now = session.scalar(select(func.now()))
    assert now is not None  # now() always answers
    return now


def _is_active(session: Session, user_id: int) -> bool:
    """A fresh read (READ COMMITTED): sees every deactivation committed so far."""
    return bool(session.scalar(select(User.is_active).where(User.id == user_id)))


def _is_locked(credential: UserCredential, now: datetime.datetime) -> bool:
    return credential.locked_until is not None and credential.locked_until > now


def _count_failure(
    session: Session, credential: UserCredential, now: datetime.datetime
) -> datetime.datetime | None:
    """Count one failed attempt; lock at the threshold. Returns the lock end, if any."""
    attempts, minutes = session.execute(
        select(
            ApplicationPolicy.sign_in_lockout_attempts, ApplicationPolicy.sign_in_lockout_minutes
        ).where(ApplicationPolicy.id == _POLICY_ID)
    ).one()
    credential.failed_attempts += 1
    if credential.failed_attempts < attempts:
        return None
    locked_until = now + datetime.timedelta(minutes=minutes)
    credential.locked_until = locked_until
    credential.failed_attempts = 0
    return locked_until


def _count_wrong_code(
    session: Session, credential: UserCredential, now: datetime.datetime, lockout_codes: int
) -> datetime.datetime | None:
    """Count one wrong two-step code; lock at the two-step threshold. Returns the lock end."""
    minutes = session.scalar(
        select(ApplicationPolicy.sign_in_lockout_minutes).where(ApplicationPolicy.id == _POLICY_ID)
    )
    assert minutes is not None  # the policy row is seeded by its migration
    credential.failed_codes += 1
    if credential.failed_codes < lockout_codes:
        return None
    locked_until = now + datetime.timedelta(minutes=minutes)
    credential.locked_until = locked_until
    credential.failed_codes = 0
    return locked_until


def _log_lock(user_id: int, locked_until: datetime.datetime | None) -> None:
    if locked_until is not None:
        logger.info("Sign-in locked for user %s until %s", user_id, locked_until.isoformat())


def _refuse_sign_in(user_id: int | None) -> SignInFailedError:
    if user_id is None:
        logger.info("Sign-in refused for an unknown login name")
    else:
        logger.info("Sign-in refused for user %s", user_id)
    return SignInFailedError(SIGN_IN_FAILED_MESSAGE)


def password_snapshot(credential: UserCredential | None) -> dict[str, Any]:
    """The audited password facet — never the hash (first-run setup reuses it)."""
    if credential is None:
        return {"password_set": False, "password_temporary": False, "password_changed_at": None}
    return {
        "password_set": True,
        "password_temporary": credential.password_is_temporary,
        "password_changed_at": credential.password_changed_at.isoformat(),
    }


def _open_session(session: Session, user_id: int) -> tuple[str, int]:
    """INSERT a new session row; returns the raw token and the row id."""
    token, digest = new_session_token()
    row = UserSession(user_id=user_id, token_digest=digest)
    session.add(row)
    session.flush()
    return token, row.id


def _end_session_by_token(
    session: Session, token: str | None, reason: UserSessionEndReason
) -> None:
    usable = _usable_token(token)
    if usable is None:
        return
    session.execute(
        update(UserSession)
        .where(UserSession.token_digest == _digest(usable), UserSession.ended_at.is_(None))
        .values(ended_at=func.now(), end_reason=reason)
        .execution_options(synchronize_session=False)
    )


def _grant(session: Session, token: str) -> SessionGrant:
    """The principal of a session created in this transaction, read before COMMIT."""
    principal = resolve_principal(session, token)
    assert principal is not None  # an active User's fresh session always resolves
    return SessionGrant(principal=principal, token=token)


def open_first_session(session: Session, user_id: int) -> SessionGrant:
    """Create the first session of a User created in this transaction (first-run setup)."""
    token, _session_id = _open_session(session, user_id)
    return _grant(session, token)


def _apply_password_set(
    session: Session,
    user: User,
    credential: UserCredential | None,
    new_hash: str,
    *,
    actor_user_id: int | None,
    metadata: dict[str, Any],
) -> None:
    """Store a temporary password, clear the lock, end every sign-in, audit."""
    now = _database_now(session)
    before = password_snapshot(credential)
    lock_cleared = credential is not None and _is_locked(credential, now)
    if credential is None:
        credential = UserCredential(user_id=user.id, failed_attempts=0)
        session.add(credential)
    credential.password_hash = new_hash
    credential.password_is_temporary = True
    credential.password_changed_at = now
    credential.failed_attempts = 0
    credential.failed_codes = 0
    credential.locked_until = None
    session.flush()
    user_access.end_user_sessions(session, user.id, UserSessionEndReason.PASSWORD_RESET)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data=before,
        after_data=password_snapshot(credential),
        actor_user_id=actor_user_id,
        metadata={**metadata, "lock_cleared": lock_cleared},
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def sign_in(
    session: Session,
    *,
    login_name: object,
    password: str,
    replaced_token: str | None,
    replaced_challenge_token: str | None = None,
    origin: str | None = None,
) -> SessionGrant | SecondStepRequired:
    """Sign a User in; ends the session ``replaced_token`` names (REPLACED).

    A correct password restarts the password counter. A User with two-step
    sign-in gets a second step instead of a session: an open one
    ``replaced_challenge_token`` names ends REPLACED, and the wrong-code
    counter stays until the second step succeeds. ``origin`` is the page
    the password was typed at: a passkey made at its host is offered.
    """
    login = _login_or_none(login_name)
    unusable = _cannot_match(password)
    user_id: int | None = None
    stored: str | None = None
    if login is not None:
        found = session.execute(
            select(User.id, UserCredential.password_hash)
            .outerjoin(UserCredential, UserCredential.user_id == User.id)
            .where(User.login_name == login)
        ).one_or_none()
        if found is not None:
            user_id, stored = found
    # Release the connection before any scrypt work.
    session.rollback()
    if user_id is None or stored is None or unusable:
        password_hashing.dummy_verify(password)
        raise _refuse_sign_in(user_id)
    ok = password_hashing.verify_password(password, stored)
    new_hash = (
        password_hashing.hash_password(password)
        if ok and password_hashing.needs_rehash(stored)
        else None
    )

    credential = user_access.lock_credential(session, user_id)
    active = _is_active(session, user_id)
    now = _database_now(session)
    if credential is None or credential.password_hash != stored:
        # A concurrent password set: the verification is stale.
        session.rollback()
        raise _refuse_sign_in(user_id)
    if _is_locked(credential, now):
        session.rollback()
        raise _refuse_sign_in(user_id)
    if not ok:
        locked_until = _count_failure(session, credential, now)
        commit(session, {})
        _log_lock(user_id, locked_until)
        raise _refuse_sign_in(user_id)
    if not active:
        session.rollback()
        raise _refuse_sign_in(user_id)

    if new_hash is not None:
        credential.password_hash = new_hash
    credential.failed_attempts = 0
    if two_step_factors.is_enabled(session, user_id):
        # A known password never buys fresh guesses at a code: the wrong
        # codes keep counting on ``failed_codes`` until a full sign-in.
        window = policies.read_two_step_policy(session).window
        opened = two_step_factors.open_challenge(
            session,
            user_id,
            now,
            replaced_challenge_token,
            window,
            passkeys.relying_party_or_none(origin),
        )
        commit(session, {})
        logger.info("Sign-in password accepted for user %s; second step required", user_id)
        return SecondStepRequired(
            token=opened.token,
            methods=opened.methods,
            email_hint=opened.email_hint,
            expires_at=opened.expires_at,
        )
    credential.failed_codes = 0
    credential.locked_until = None
    token, _session_id = _open_session(session, user_id)
    _end_session_by_token(session, replaced_token, UserSessionEndReason.REPLACED)
    grant = _grant(session, token)
    commit(session, {})
    logger.info("Sign-in succeeded for user %s", user_id)
    return grant


# Accepts one answer of a second step: (User id, the locked challenge, now,
# the policy) → whether it is right. It runs under the credential and the
# challenge locks and takes the later locks of the order itself.
_Accept = Callable[[int, UserSignInChallenge, datetime.datetime, policies.TwoStepPolicy], bool]


def complete_second_step(
    session: Session,
    *,
    token: str | None,
    method: SecondStepMethod,
    code: str,
    replaced_token: str | None,
) -> SessionGrant:
    """Finish the open second step ``token`` names with ``code``: sign the User in.

    A recovery code and an emailed code work once; an app code's step is
    remembered so it works once too. A passkey answers through
    ``complete_second_step_with_passkey``.
    """
    if method is SecondStepMethod.PASSKEY:
        raise InvalidInputError("A passkey answers the second step with its own request.")

    def accept(
        user_id: int,
        challenge: UserSignInChallenge,
        now: datetime.datetime,
        policy: policies.TwoStepPolicy,
    ) -> bool:
        factor = two_step_factors.lock_totp(session, user_id)
        if method is SecondStepMethod.TOTP:
            return two_step_factors.accept_totp(factor, code, now, policy.clock_drift_steps)
        if method is SecondStepMethod.RECOVERY_CODE:
            return two_step_factors.use_recovery_code(session, user_id, code)
        return two_step_factors.accept_email_code(challenge, code)

    return _finish_second_step(
        session, token=token, method=method, accept=accept, replaced_token=replaced_token
    )


def complete_second_step_with_passkey(
    session: Session,
    *,
    token: str | None,
    credential: dict[str, Any],
    origin: str | None,
    replaced_token: str | None,
) -> SessionGrant:
    """Finish the open second step ``token`` names with a passkey's answer.

    The answer must sign the WebAuthn challenge of this second step's last
    passkey request (``two_step.passkey_step_options``), made at the same
    host, with one of the User's passkeys made there; the challenge works
    once. A refused answer counts like a wrong code.
    """
    rp_id = passkeys.relying_party(origin)

    def accept(
        user_id: int,
        challenge: UserSignInChallenge,
        now: datetime.datetime,
        policy: policies.TwoStepPolicy,
    ) -> bool:
        two_step_factors.lock_totp(session, user_id)
        asked, asked_at = challenge.passkey_challenge, challenge.passkey_rp_id
        challenge.passkey_challenge = None
        challenge.passkey_rp_id = None
        if asked is None or asked_at != rp_id:
            return False
        return _use_passkey(session, user_id, rp_id, asked, credential, now, second_step=True)

    return _finish_second_step(
        session,
        token=token,
        method=SecondStepMethod.PASSKEY,
        accept=accept,
        replaced_token=replaced_token,
    )


def _use_passkey(
    session: Session,
    user_id: int,
    rp_id: str,
    asked: bytes,
    credential: dict[str, Any],
    now: datetime.datetime,
    *,
    second_step: bool,
) -> bool:
    """Verify an answer against the User's passkeys at ``rp_id``; record the one used."""
    stored = two_step_factors.lock_passkeys(session, user_id, rp_id)
    candidates = [
        passkeys.StoredPasskey(id=p.id, credential_data=p.credential_data, sign_count=p.sign_count)
        for p in stored
    ]
    verify = passkeys.verify_second_step if second_step else passkeys.verify_sign_in
    asserted = verify(rp_id, asked, candidates, credential)
    if asserted is None:
        return False
    used = next(p for p in stored if p.id == asserted.passkey_id)
    used.sign_count = asserted.sign_count
    used.last_used_at = now
    return True


def begin_passkey_sign_in(
    session: Session, *, origin: str | None, replaced_challenge_token: str | None
) -> PasskeySignInStart:
    """Open a sign-in with a passkey alone; answers its WebAuthn request options.

    Any passkey made at this host may answer (the browser lets the person
    choose); the authenticator must verify the person. An open challenge
    ``replaced_challenge_token`` names ends REPLACED.
    """
    rp_id = passkeys.relying_party(origin)
    options, challenge = passkeys.sign_in_options(rp_id)
    now = _database_now(session)
    window = policies.read_two_step_policy(session).window
    token = two_step_factors.open_passkey_sign_in(
        session, now, replaced_challenge_token, window, rp_id, challenge
    )
    commit(session, {})
    return PasskeySignInStart(token=token, options=options, expires_at=now + window)


def sign_in_with_passkey(
    session: Session,
    *,
    token: str | None,
    credential: dict[str, Any],
    origin: str | None,
    replaced_token: str | None,
) -> SessionGrant:
    """Sign in the User whose passkey answered the open passkey sign-in ``token`` names.

    The answer names its passkey and User; both must match a stored passkey
    made at this host, and its signature must verify. The request works
    once: any refusal ends it. An inactive User, one without a password or
    a locked one is refused with the same generic answer. A sign-in
    restarts both failure counters and clears a lock, as a full sign-in
    does; the session ``replaced_token`` names ends REPLACED.
    """
    rp_id = passkeys.relying_party(origin)
    found = two_step_factors.find_challenge(session, token)
    if found is None or found.purpose != SignInChallengePurpose.PASSKEY_SIGN_IN.value:
        session.rollback()
        raise SignInFailedError(PASSKEY_SIGN_IN_FAILED_MESSAGE)
    challenge_id = found.id
    answered = passkeys.answered_credential(credential)
    user_id = (
        None
        if answered is None
        else session.scalar(
            select(UserPasskey.user_id).where(UserPasskey.credential_id == answered[0])
        )
    )
    # Lock order: the credential first, then the challenge.
    session.rollback()
    credential_row = None if user_id is None else user_access.lock_credential(session, user_id)
    challenge = two_step_factors.lock_challenge(session, challenge_id)
    now = _database_now(session)
    policy = policies.read_two_step_policy(session)
    if not two_step_factors.is_open(challenge, now, policy.window):
        session.rollback()
        raise SignInFailedError(PASSKEY_SIGN_IN_FAILED_MESSAGE)
    asked, asked_at = challenge.passkey_challenge, challenge.passkey_rp_id
    accepted = (
        user_id is not None
        and answered is not None
        and credential_row is not None
        and asked is not None
        and asked_at == rp_id
        and answered[1] == passkeys.user_handle(user_id)
        and _is_active(session, user_id)
        and not _is_locked(credential_row, now)
        and _use_passkey(session, user_id, rp_id, asked, credential, now, second_step=False)
    )
    if not accepted or user_id is None or credential_row is None:
        two_step_factors.end_challenge(challenge, SignInChallengeEndReason.FAILED)
        commit(session, {})
        logger.info("Passkey sign-in refused")
        raise SignInFailedError(PASSKEY_SIGN_IN_FAILED_MESSAGE)
    credential_row.failed_attempts = 0
    credential_row.failed_codes = 0
    credential_row.locked_until = None
    challenge.user_id = user_id
    two_step_factors.end_challenge(challenge, SignInChallengeEndReason.COMPLETED)
    session_token, _session_id = _open_session(session, user_id)
    _end_session_by_token(session, replaced_token, UserSessionEndReason.REPLACED)
    grant = _grant(session, session_token)
    commit(session, {})
    logger.info("Sign-in succeeded for user %s (passkey)", user_id)
    return grant


def _finish_second_step(
    session: Session,
    *,
    token: str | None,
    method: SecondStepMethod,
    accept: _Accept,
    replaced_token: str | None,
) -> SessionGrant:
    """Judge one answer of the open second step ``token`` names; sign the User in when right.

    A wrong answer counts on the challenge and on the account's wrong-code
    counter (``failed_codes``); the challenge ends at the two-step policy's
    wrong codes per sign-in, and the account locks at its threshold. A full
    sign-in restarts both counters and clears a lock.
    """
    found = two_step_factors.find_challenge(session, token)
    if (
        found is None
        or found.purpose != SignInChallengePurpose.SECOND_STEP.value
        or found.user_id is None
    ):
        session.rollback()
        raise SecondStepExpiredError(SECOND_STEP_EXPIRED_MESSAGE)
    challenge_id, user_id = found.id, found.user_id
    # Lock order: the credential first, then the challenge.
    session.rollback()
    credential = user_access.lock_credential(session, user_id)
    challenge = two_step_factors.lock_challenge(session, challenge_id)
    now = _database_now(session)
    policy = policies.read_two_step_policy(session)
    if not two_step_factors.is_open(challenge, now, policy.window):
        session.rollback()
        raise SecondStepExpiredError(SECOND_STEP_EXPIRED_MESSAGE)
    if credential is None or not _is_active(session, user_id):
        two_step_factors.end_challenge(challenge, SignInChallengeEndReason.FAILED)
        commit(session, {})
        raise SecondStepExpiredError(SECOND_STEP_EXPIRED_MESSAGE)
    if _is_locked(credential, now):
        two_step_factors.end_challenge(challenge, SignInChallengeEndReason.FAILED)
        commit(session, {})
        raise AccountLockedError(_ACCOUNT_LOCKED)
    if not accept(user_id, challenge, now, policy):
        challenge.failed_attempts += 1
        locked_until = _count_wrong_code(session, credential, now, policy.lockout_codes)
        ended = locked_until is not None or challenge.failed_attempts >= policy.max_codes
        if ended:
            two_step_factors.end_challenge(challenge, SignInChallengeEndReason.FAILED)
        commit(session, {})
        _log_lock(user_id, locked_until)
        logger.info("Second step refused for user %s", user_id)
        if locked_until is not None:
            raise AccountLockedError(_ACCOUNT_LOCKED)
        if ended:
            raise SecondStepExpiredError(_TOO_MANY_CODES)
        raise SecondStepFailedError(_WRONG_CODE)

    credential.failed_attempts = 0
    credential.failed_codes = 0
    credential.locked_until = None
    session_token, _session_id = _open_session(session, user_id)
    _end_session_by_token(session, replaced_token, UserSessionEndReason.REPLACED)
    two_step_factors.end_challenge(challenge, SignInChallengeEndReason.COMPLETED)
    grant = _grant(session, session_token)
    commit(session, {})
    logger.info("Sign-in succeeded for user %s (second step %s)", user_id, method.value)
    return grant


class _PasswordCheck(NamedTuple):
    """The scrypt check of a typed password: its result and the hash it was checked against."""

    ok: bool
    stored: str | None


def _check_own_password(session: Session, user_id: int, password: str) -> _PasswordCheck:
    """Verify ``password`` against the User's stored hash, outside any transaction."""
    stored = session.scalar(
        select(UserCredential.password_hash).where(UserCredential.user_id == user_id)
    )
    session.rollback()
    if stored is None or _cannot_match(password):
        password_hashing.dummy_verify(password)
        return _PasswordCheck(ok=False, stored=stored)
    return _PasswordCheck(ok=password_hashing.verify_password(password, stored), stored=stored)


def _judge_own_password(session: Session, user_id: int, check: _PasswordCheck) -> None:
    """Lock and re-read the credential; refuse a stale, locked or wrong password.

    A wrong password counts as a failed attempt (it cannot be guessed past
    the lockout) and raises; on return the transaction holds the credential
    lock.
    """
    credential = user_access.lock_credential(session, user_id)
    if not _is_active(session, user_id):
        session.rollback()
        raise AuthenticationRequiredError(AUTHENTICATION_REQUIRED_MESSAGE)
    if credential is None or credential.password_hash != check.stored:
        session.rollback()
        raise ConflictError(_CHANGED_CONCURRENTLY)
    now = _database_now(session)
    if _is_locked(credential, now):
        session.rollback()
        raise AccountLockedError(_ACCOUNT_LOCKED)
    if not check.ok:
        locked_until = _count_failure(session, credential, now)
        commit(session, {})
        _log_lock(user_id, locked_until)
        raise InvalidInputError(_WRONG_CURRENT_PASSWORD)


def confirm_own_password(session: Session, principal: Principal, password: str) -> None:
    """Check the signed-in User's password again before their sign-in methods change.

    The scrypt check runs outside any transaction; then the credential row
    is locked and re-read (``_judge_own_password``). On return the caller's
    transaction holds the credential lock.
    """
    check = _check_own_password(session, principal.user_id, password)
    _judge_own_password(session, principal.user_id, check)


def change_own_email(
    session: Session, principal: Principal, *, current_password: str, email: object
) -> str | None:
    """Replace the signed-in User's own email address; returns the stored address.

    The address goes through the one domain rule (blank = none) before the
    password is checked again. Lock order: the ``users`` row (the mode
    every User write uses), then the ``user_credentials`` row. An
    effective change is audited on the User like an administrator's edit,
    with the metadata ``{"email_change": "BY_USER"}``; after COMMIT the
    previous address, if any, receives a notice (best effort: a missing or
    failing mail server never undoes the change). Needs no permission: the
    account is the User's own, so no escalation guard applies.
    """
    new_email = users.canonical_email(email)
    check = _check_own_password(session, principal.user_id, current_password)
    user = users.lock_user(session, principal.user_id)
    user_access.recheck_actor(session, principal)
    _judge_own_password(session, principal.user_id, check)
    previous = user.email
    if new_email == previous:
        session.rollback()
        return previous
    before = users.profile_snapshot(user)
    user.email = new_email
    user.updated_at = func.now()
    flush(session, users.USER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data=before,
        after_data=users.profile_snapshot(user),
        actor_user_id=principal.user_id,
        metadata={"email_change": "BY_USER"},
    )
    changed_at = _database_now(session)
    commit(session, users.USER_CONFLICTS)
    logger.info("Email address changed by user %s", principal.user_id)
    if previous is not None:
        _notify_email_changed(session, principal, previous, new_email, changed_at)
    return new_email


def _notify_email_changed(
    session: Session,
    principal: Principal,
    previous: str,
    new_email: str | None,
    changed_at: datetime.datetime,
) -> None:
    """Tell the previous address of a User's email change (best effort, after COMMIT)."""
    change = (
        "removed"
        if new_email is None
        else f"changed to {one_time_codes.mask_email_address(new_email)}"
    )
    message = mail.OutgoingMessage(
        to=previous,
        subject=_EMAIL_CHANGED_SUBJECT,
        body=_EMAIL_CHANGED_BODY.format(
            login=principal.login_name,
            change=change,
            when=f"{changed_at.astimezone(datetime.UTC):%Y-%m-%d %H:%M}",
        ),
    )
    try:
        outgoing_mail.send(session, message)
    except MailServerNotSetUpError:
        logger.info(
            "No notice of the email change of user %s: no outgoing mail server", principal.user_id
        )
    except MailNotSentError:
        # The change is committed; the notice is a courtesy, so the mail
        # server's refusal is logged for the administrator, never raised.
        logger.warning("The notice of the email change of user %s was not sent", principal.user_id)


def sign_out(session: Session, token: str | None) -> None:
    """End the session ``token`` names; no session is a no-op (idempotent)."""
    _end_session_by_token(session, token, UserSessionEndReason.SIGNED_OUT)
    commit(session, {})


def change_own_password(
    session: Session, principal: Principal, *, current_password: str, new_password: str
) -> SessionGrant:
    """Replace the signed-in User's password; every other sign-in of the User ends.

    Allowed while a change is required. The caller's session ends too and
    a new one is granted (the API rotates the cookie).
    """
    _new_password(new_password)
    if normalize_password(new_password) == normalize_password(current_password):
        raise InvalidInputError(_SAME_PASSWORD)
    user_id = principal.user_id
    stored = session.scalar(
        select(UserCredential.password_hash).where(UserCredential.user_id == user_id)
    )
    session.rollback()
    ok = False
    new_hash: str | None = None
    if stored is None or _cannot_match(current_password):
        password_hashing.dummy_verify(current_password)
    else:
        ok = password_hashing.verify_password(current_password, stored)
        if ok:
            new_hash = password_hashing.hash_password(new_password)

    credential = user_access.lock_credential(session, user_id)
    if not _is_active(session, user_id):
        session.rollback()
        raise AuthenticationRequiredError(AUTHENTICATION_REQUIRED_MESSAGE)
    if credential is None or credential.password_hash != stored:
        session.rollback()
        raise ConflictError(_CHANGED_CONCURRENTLY)
    now = _database_now(session)
    if _is_locked(credential, now):
        # Neither counted nor revealed: a session holder cannot keep
        # guessing the current password past the lockout.
        session.rollback()
        raise AccountLockedError(_ACCOUNT_LOCKED)
    if not ok or new_hash is None:
        locked_until = _count_failure(session, credential, now)
        commit(session, {})
        _log_lock(user_id, locked_until)
        raise InvalidInputError(_WRONG_CURRENT_PASSWORD)

    before = password_snapshot(credential)
    credential.password_hash = new_hash
    credential.password_is_temporary = False
    credential.password_changed_at = now
    credential.failed_attempts = 0
    credential.locked_until = None
    token, session_id = _open_session(session, user_id)
    user_access.end_user_sessions(
        session, user_id, UserSessionEndReason.PASSWORD_CHANGED, keep_session_id=session_id
    )
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user_id),
        before_data=before,
        after_data=password_snapshot(credential),
        actor_user_id=user_id,
        metadata={"password_change": "CHANGED_BY_USER"},
    )
    grant = _grant(session, token)
    commit(session, {})
    return grant


def set_own_theme_preference(
    session: Session, principal: Principal, *, theme_preference: object
) -> ThemePreference:
    """Save the signed-in User's own Dark/Light preference (GUI_DESIGN §2.1, User tier).

    A display preference, not configuration (OD-P16): no audit row, ``users.updated_at``
    unchanged, never part of the User audit snapshot. Only the caller's own row: the id
    comes from the session principal, never from the request. Absolute value: a repeat
    performs no UPDATE, so a retry after an unknown outcome is safe. Returns the value
    written or kept, never re-read after COMMIT (the station theme precedent). Worker
    Sessions never call it.

    One lock, taken first and held to COMMIT: the User row FOR NO KEY UPDATE (the mode
    every User write uses). Not under the User-administration advisory lock: the write
    changes no grant, role, credential or activity. As a transaction's only lock it
    cannot be part of a deadlock.
    """
    # Wire vocabulary, case-sensitive; the shape is judged before any lock
    # (the Scan Station theme rule and copy).
    if not isinstance(theme_preference, str) or theme_preference not in ThemePreference:
        raise InvalidInputError("Theme preference must be DARK or LIGHT.")
    value = ThemePreference(theme_preference)
    user = users.lock_user(session, principal.user_id)
    # After the lock: a deactivation, a session end or a forced change committed
    # by a writer that held this row while the request waited refuses (A1/A3).
    user_access.recheck_actor(session, principal)
    if user.theme_preference != value.value:
        user.theme_preference = value.value
        commit(session, {})
    return value


def set_user_password(
    session: Session, user_id: int, *, new_password: str, actor: Principal
) -> users.UserView:
    """An administrator sets another User's temporary password.

    Gives a first password to a User without one, ends every sign-in of
    the User and clears a lock. Setting an inactive User's password is
    allowed (it cannot sign in until reactivated). The actor's sign-in
    and keys are re-read after the advisory lock, so a revocation
    committed during the hashing wait refuses the set; a User whose role
    holds a protected key also needs ``MANAGE_CORRECTION_PERMISSIONS``
    (the escalation guard, Phase 14 slice 2).
    """
    if actor.user_id == user_id:
        raise ConflictError(_OWN_PASSWORD)
    _new_password(new_password)
    # Release the connection principal resolution used before scrypt.
    session.rollback()
    new_hash = password_hashing.hash_password(new_password)

    # The route checked the actor before the hashing wait; check again.
    acting = user_access.acting_user(session, actor)
    user_access.judge(
        session,
        lambda: authorization.require(acting, (Permission.MANAGE_USERS_AND_ROLES,)),
    )
    user = users.lock_user(session, user_id)
    if user_access.role_holds_protected(session, user.role_id):
        user_access.judge(
            session,
            lambda: authorization.require_guard(acting, authorization.PROTECTED_USER_MESSAGE),
        )
    credential = user_access.lock_credential(session, user.id)
    _apply_password_set(
        session,
        user,
        credential,
        new_hash,
        actor_user_id=actor.user_id,
        metadata={"password_change": "SET_BY_ADMINISTRATOR"},
    )
    view = users.user_view(session, user)
    commit(session, {})
    return view


def reset_password_for_login(
    session: Session, login_name: str, *, new_password: str
) -> RecoveryOutcome:
    """Recovery command: reset the password of a User that already has one.

    Only while an administrator exists, and never for a User without a
    password — so it can never create an administrator or race first-run
    setup (both checked under the User-administration lock). The
    password becomes temporary, the lock is cleared and every sign-in of
    the User ends; the audit row carries no actor (``source: cli``).
    """
    _new_password(new_password)
    try:
        login = users.canonical_login_name(login_name)
    except InvalidInputError as exc:
        raise UnknownLoginError(f"No user has the login name {login_name.strip()}.") from exc
    session.rollback()
    new_hash = password_hashing.hash_password(new_password)

    user_access.acquire_user_administration_lock(session)
    if not user_access.administrator_exists(session):
        session.rollback()
        raise RecoveryUnavailableError(_NO_ADMINISTRATOR)
    user = session.scalars(
        select(User)
        .where(User.login_name == login)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if user is None:
        session.rollback()
        raise UnknownLoginError(f"No user has the login name {login}.")
    credential = user_access.lock_credential(session, user.id)
    if credential is None:
        session.rollback()
        raise RecoveryUnavailableError(_NO_PASSWORD_YET)
    _apply_password_set(
        session,
        user,
        credential,
        new_hash,
        actor_user_id=None,
        metadata={"password_change": "RECOVERY_CLI", "source": "cli"},
    )
    outcome = RecoveryOutcome(
        user_id=user.id, display_name=user.display_name, is_active=user.is_active
    )
    try:
        commit(session, {})
    except IntegrityError:
        raise  # rolled back by ``commit``: a definite failure
    except DBAPIError as exc:
        # COMMIT was sent; its answer was lost (the server may have
        # stored the reset). Never reported as "nothing was changed".
        raise RecoveryOutcomeUnknownError(_RECOVERY_OUTCOME_UNKNOWN) from exc
    return outcome


def restore_correction_permission_management(
    session: Session, role_name: str
) -> RoleRestoreOutcome:
    """Recovery command: grant ``MANAGE_CORRECTION_PERMISSIONS`` back to a role.

    Only while no active User with a password holds it (a database that
    lost every holder before Phase 14 slice 2's last-holder rule existed)
    and only to a role an active User with a password holds, so the grant
    restores a holder. It grants that one key, never creates a User or a
    password, and is outside the escalation guard by construction: access
    to the backend container is the authority. Audited like a role edit
    (``source: cli``, no actor).
    """
    user_access.acquire_user_administration_lock(session)
    if user_access.permission_holder_count(session, Permission.MANAGE_CORRECTION_PERMISSIONS):
        session.rollback()
        raise RecoveryUnavailableError(_CORRECTION_MANAGER_EXISTS)
    role = session.scalars(
        select(Role)
        .where(Role.name == role_name)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if role is None:
        session.rollback()
        raise RecoveryUnavailableError(f"No role is named {role_name}.")
    members = session.scalar(
        select(func.count())
        .select_from(User)
        .join(UserCredential, UserCredential.user_id == User.id)
        .where(User.role_id == role.id, User.is_active)
    )
    if not members:
        session.rollback()
        raise RecoveryUnavailableError(
            f"No active user with a password holds the role {role_name}. Name a role that"
            " one holds."
        )
    held = user_access.role_permissions(session, role.id)
    session.add(
        RolePermission(role_id=role.id, permission=Permission.MANAGE_CORRECTION_PERMISSIONS.value)
    )
    role.updated_at = func.now()
    session.flush()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.ROLE,
        entity_id=str(role.id),
        before_data=roles.role_snapshot(role.name, held),
        after_data=roles.role_snapshot(
            role.name, held | {Permission.MANAGE_CORRECTION_PERMISSIONS}
        ),
        metadata={"source": "cli"},
    )
    outcome = RoleRestoreOutcome(
        role_id=role.id,
        role_name=role.name,
        holders=user_access.permission_holder_count(
            session, Permission.MANAGE_CORRECTION_PERMISSIONS
        ),
    )
    try:
        commit(session, {})
    except IntegrityError:
        raise  # rolled back by ``commit``: a definite failure
    except DBAPIError as exc:
        # COMMIT was sent; its answer was lost (the server may have
        # stored the grant). Never reported as "nothing was changed".
        raise RecoveryOutcomeUnknownError(_RESTORE_OUTCOME_UNKNOWN) from exc
    return outcome
