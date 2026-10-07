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
  a User without a password, an inactive or locked User, a wrong or
  over-long password — the response never tells which; an unknown login
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

Every hashing command first reads the plain values it needs, releases
its database connection (``session.rollback()``), hashes or verifies
outside any transaction and lock, then starts its locked phase, which
re-checks everything it relies on (a concurrent password set,
deactivation or lock). Lock order: the ``partflow:user-administration``
advisory lock → the ``users`` row → the ``user_credentials`` row →
INSERT ``user_sessions`` → UPDATE ``user_sessions`` → audit.

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
from typing import Any, Final, NamedTuple

from sqlalchemy import Interval, case, func, or_, select, update
from sqlalchemy.orm import Session

from app.application import audit, password_hashing, user_access, users
from app.application.common import commit
from app.application.errors import (
    AccountLockedError,
    AuthenticationRequiredError,
    ConflictError,
    InvalidInputError,
    RecoveryUnavailableError,
    SignInFailedError,
    UnknownLoginError,
)
from app.domain.enums import AuditEntityType, AuditEventType, Permission, UserSessionEndReason
from app.domain.password_policy import (
    InvalidPasswordError,
    is_over_long,
    normalize_password,
    validate_new_password,
)
from app.domain.user_login import InvalidLoginNameError, normalize_login_name
from app.infrastructure.models import (
    ApplicationPolicy,
    Role,
    RolePermission,
    User,
    UserCredential,
    UserSession,
)

logger = logging.getLogger(__name__)

#: The session cookie: the raw token, never logged.
SESSION_COOKIE: Final = "partflow_session"
#: Longest cookie value ever looked up (issued tokens have 43 characters).
MAX_TOKEN_LENGTH: Final = 64
#: Cookie lifetime of a session that never expires: the browser cap (400 days).
NEVER_EXPIRES_COOKIE_SECONDS: Final = 34_560_000
_POLICY_ID: Final = 1

AUTHENTICATION_REQUIRED_MESSAGE: Final = (
    "You are not signed in, or your sign-in has ended. Sign in to continue."
)
PERMISSION_DENIED_MESSAGE: Final = "Your account does not have permission to do this."
PASSWORD_CHANGE_REQUIRED_MESSAGE: Final = "Choose a new password before you continue."
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


class Principal(NamedTuple):
    """The signed-in User of one request — plain values; no ORM object crosses COMMIT."""

    session_id: int
    user_id: int
    login_name: str
    display_name: str
    role_id: int
    role_name: str
    avatar_updated_at: datetime.datetime | None
    permissions: frozenset[Permission]
    must_change_password: bool
    # None = the session never expires.
    session_expires_at: datetime.datetime | None


class SessionGrant(NamedTuple):
    """A new session: the principal and the raw token for the cookie only."""

    principal: Principal
    token: str


class RecoveryOutcome(NamedTuple):
    """What the recovery command reports after a reset."""

    user_id: int
    display_name: str
    is_active: bool


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
    expires_at = case(
        (
            ApplicationPolicy.user_session_expires,
            UserSession.created_at
            + func.make_interval(0, 0, 0, ApplicationPolicy.user_session_days, type_=Interval),
        ),
        else_=None,
    )
    row = session.execute(
        select(
            UserSession.id,
            User.id,
            User.login_name,
            User.display_name,
            User.role_id,
            Role.name,
            User.avatar_image_updated_at,
            UserCredential.password_is_temporary,
            ApplicationPolicy.require_password_change,
            expires_at,
        )
        .select_from(UserSession)
        .join(User, User.id == UserSession.user_id)
        .join(Role, Role.id == User.role_id)
        .join(UserCredential, UserCredential.user_id == User.id)
        .join(ApplicationPolicy, ApplicationPolicy.id == _POLICY_ID)
        .where(
            UserSession.token_digest == _digest(usable),
            UserSession.ended_at.is_(None),
            User.is_active,
            or_(expires_at.is_(None), func.now() < expires_at),
        )
    ).one_or_none()
    if row is None:
        return None
    (
        session_id,
        user_id,
        login_name,
        display_name,
        role_id,
        role_name,
        avatar_updated_at,
        temporary,
        require_change,
        session_expires_at,
    ) = row
    permissions = frozenset(
        Permission(permission)
        for permission in session.scalars(
            select(RolePermission.permission).where(RolePermission.role_id == role_id)
        )
    )
    return Principal(
        session_id=session_id,
        user_id=user_id,
        login_name=login_name,
        display_name=display_name,
        role_id=role_id,
        role_name=role_name,
        avatar_updated_at=avatar_updated_at,
        permissions=permissions,
        must_change_password=bool(temporary and require_change),
        session_expires_at=session_expires_at,
    )


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
    session: Session, *, login_name: object, password: str, replaced_token: str | None
) -> SessionGrant:
    """Sign a User in; ends the session ``replaced_token`` names (REPLACED)."""
    login = _login_or_none(login_name)
    over_long = is_over_long(password)
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
    if user_id is None or stored is None or over_long:
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

    credential.failed_attempts = 0
    credential.locked_until = None
    if new_hash is not None:
        credential.password_hash = new_hash
    token, _session_id = _open_session(session, user_id)
    _end_session_by_token(session, replaced_token, UserSessionEndReason.REPLACED)
    grant = _grant(session, token)
    commit(session, {})
    logger.info("Sign-in succeeded for user %s", user_id)
    return grant


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
    if stored is None or is_over_long(current_password):
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


def set_user_password(
    session: Session, user_id: int, *, new_password: str, actor: Principal
) -> users.UserView:
    """An administrator sets another User's temporary password.

    Gives a first password to a User without one, ends every sign-in of
    the User and clears a lock. Setting an inactive User's password is
    allowed (it cannot sign in until reactivated).
    """
    if actor.user_id == user_id:
        raise ConflictError(_OWN_PASSWORD)
    _new_password(new_password)
    # Release the connection principal resolution used before scrypt.
    session.rollback()
    new_hash = password_hashing.hash_password(new_password)

    user_access.acquire_user_administration_lock(session)
    user = users.lock_user(session, user_id)
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
    commit(session, {})
    return outcome
