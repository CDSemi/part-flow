"""Sign-in primitives shared by Users, authentication and first-run setup
(Phase 14 slice 1).

The small reads and writes that both ``app.application.users`` (S12:
deactivation ends every open sign-in) and
``app.application.authentication`` / ``app.application.first_run`` need.
This module imports neither of them, so the dependency graph stays
acyclic: ``users → user_access``; ``authentication → users,
user_access``; ``first_run → users, user_access, authentication``.

- ``USER_ADMINISTRATION_LOCK`` — the transaction-scoped advisory lock
  that serializes first-run setup, an administrator's password set and
  the recovery reset (and that slice 2's escalation guard reuses);
- ``lock_credential`` — a User's credential row under ``FOR NO KEY
  UPDATE``, always taken after the User row and before any session row
  (the lock order of every sign-in path);
- ``end_user_sessions`` — ends every not-ended session of a User,
  expired or not, so a later switch to "never expires" can never revive
  a session a sign-out, password change or reset, or deactivation ended;
- ``administrator_exists`` — whether an active User with a password
  whose role holds ``MANAGE_USERS_AND_ROLES`` exists (permission keys,
  never role names);
- ``sign_in_states`` — how each credential presents to administrators.
"""

from collections.abc import Iterable
from typing import Final

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app.domain.enums import Permission, SignInState, UserSessionEndReason
from app.infrastructure.models import RolePermission, User, UserCredential, UserSession

#: The advisory lock key of every User-administration write that decides
#: who is an administrator (first-run setup, password set, recovery).
USER_ADMINISTRATION_LOCK: Final = "partflow:user-administration"

# Lock-first mode of a credential write: the FOR NO KEY UPDATE its own
# UPDATE takes anyway (no key column of the row ever changes).
_CREDENTIAL_LOCK: Final = {"key_share": True}


def acquire_user_administration_lock(session: Session) -> None:
    """Serialize this transaction against every other User-administration write.

    ``pg_advisory_xact_lock`` releases with the transaction.
    """
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(USER_ADMINISTRATION_LOCK, 0)))
    )


def lock_credential(session: Session, user_id: int) -> UserCredential | None:
    """The User's credential under its row lock, re-read; ``None`` = no password."""
    return session.get(
        UserCredential, user_id, populate_existing=True, with_for_update=_CREDENTIAL_LOCK
    )


def end_user_sessions(
    session: Session,
    user_id: int,
    reason: UserSessionEndReason,
    *,
    keep_session_id: int | None = None,
) -> None:
    """End every not-ended session of the User, except ``keep_session_id``."""
    statement = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.ended_at.is_(None))
        .values(ended_at=func.now(), end_reason=reason)
        .execution_options(synchronize_session=False)
    )
    if keep_session_id is not None:
        statement = statement.where(UserSession.id != keep_session_id)
    session.execute(statement)


def administrator_exists(session: Session) -> bool:
    """Whether an active User with a password may manage users and roles."""
    query = select(
        exists()
        .where(User.is_active)
        .where(UserCredential.user_id == User.id)
        .where(RolePermission.role_id == User.role_id)
        .where(RolePermission.permission == Permission.MANAGE_USERS_AND_ROLES)
    )
    return bool(session.scalar(query))


def sign_in_states(session: Session, user_ids: Iterable[int]) -> dict[int, SignInState]:
    """The sign-in state of each given User (presentation only, never stored)."""
    ids = list(user_ids)
    states = dict.fromkeys(ids, SignInState.NO_PASSWORD)
    if not ids:
        return states
    rows = session.execute(
        select(
            UserCredential.user_id,
            UserCredential.password_is_temporary,
            func.coalesce(UserCredential.locked_until > func.now(), False),
        ).where(UserCredential.user_id.in_(ids))
    )
    for user_id, temporary, locked in rows:
        if locked:
            states[user_id] = SignInState.LOCKED
        elif temporary:
            states[user_id] = SignInState.TEMPORARY_PASSWORD
        else:
            states[user_id] = SignInState.PASSWORD_SET
    return states
