"""Sign-in primitives shared by Users, roles, authentication and first-run
setup (Phase 14 slices 1–3).

The small reads and writes that ``app.application.users``,
``app.application.roles``, ``app.application.authentication`` and
``app.application.first_run`` all need. This module imports none of
them, so the dependency graph stays acyclic: ``users, roles →
user_access``; ``authentication → users, user_access``; ``first_run →
users, user_access, authentication``.

- ``Principal`` / ``principal_where`` — the signed-in User of one usable
  session (not ended, not expired under the current policy, the User
  active with a password), with its role's permissions;
- ``recheck_actor`` — the acting User's own session re-read under the
  User-administration lock, so every role, user and password write
  judges the actor's CURRENT sign-in and permissions (slice 2);
- ``USER_ADMINISTRATION_LOCK`` — the transaction-scoped advisory lock
  taken first by every role and user write, the password set, first-run
  setup and the recovery commands, so grants, role assignments,
  activity and credentials — every input of the permission-management
  guard and of the holder counts, the actor's own included — change one
  transaction at a time;
- ``lock_credential`` — a User's credential row under ``FOR NO KEY
  UPDATE``, always taken after the User row and before any session row
  (the lock order of every sign-in path);
- ``end_user_sessions`` — ends every not-ended session of a User,
  expired or not, so a later switch to "never expires" can never revive
  a session a sign-out, password change or reset, or deactivation ended;
- ``permission_holder_count`` / ``administrator_exists`` — how many
  active Users with a password hold a key (permission keys, never role
  names); an administrator exists while one may manage users and roles;
- ``role_permissions`` / ``role_holds_protected`` — a role's grants and
  whether one of them is protected (``app.domain.permissions``);
- ``warn_if_correction_management_lost`` — the startup warning while no
  active User with a password may manage correction permissions but
  Users may be managed (logger ``app.user_access``);
- ``sign_in_states`` — how each credential presents to administrators;
- ``UserRef`` / ``user_refs`` — who recorded a history row, for display
  (slice 3: the Machine lifecycle timeline).
"""

import datetime
import logging
from collections.abc import Callable, Collection, Iterable
from typing import Final, NamedTuple

from sqlalchemy import ColumnElement, Interval, case, func, or_, select, update
from sqlalchemy.orm import Session

from app.application.authorization import Actor
from app.application.errors import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    PASSWORD_CHANGE_REQUIRED_MESSAGE,
    AuthenticationRequiredError,
    LastPermissionHolderError,
    PasswordChangeRequiredError,
    PermissionDeniedError,
)
from app.domain.enums import Permission, SignInState, UserSessionEndReason
from app.domain.permissions import PERMISSION_MANAGEMENT, holds_protected
from app.infrastructure.models import (
    ApplicationPolicy,
    Role,
    RolePermission,
    User,
    UserCredential,
    UserSession,
)

#: The advisory lock key of every User-administration write: role and
#: user writes, password sets, first-run setup and the recovery commands.
USER_ADMINISTRATION_LOCK: Final = "partflow:user-administration"
_POLICY_ID: Final = 1

logger = logging.getLogger("app.user_access")

_CORRECTION_MANAGEMENT_LOST: Final = (
    "No active user with a password may manage correction permissions. Restore it with:"
    " python -m app.cli restore-correction-permission-management --role-name <role>"
)

# Lock-first mode of a credential write: the FOR NO KEY UPDATE its own
# UPDATE takes anyway (no key column of the row ever changes).
_CREDENTIAL_LOCK: Final = {"key_share": True}


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


def acquire_user_administration_lock(session: Session) -> None:
    """Serialize this transaction against every other User-administration write.

    ``pg_advisory_xact_lock`` releases with the transaction.
    """
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(USER_ADMINISTRATION_LOCK, 0)))
    )


def principal_where(session: Session, which: ColumnElement[bool]) -> Principal | None:
    """The principal of the one usable session ``which`` selects, or None.

    No lock and no write; column selects only, so the identity map holds
    no User or policy instance afterwards.
    """
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
            which,
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
    return Principal(
        session_id=session_id,
        user_id=user_id,
        login_name=login_name,
        display_name=display_name,
        role_id=role_id,
        role_name=role_name,
        avatar_updated_at=avatar_updated_at,
        permissions=role_permissions(session, role_id),
        must_change_password=bool(temporary and require_change),
        session_expires_at=session_expires_at,
    )


def recheck_actor(session: Session, actor: Principal) -> Principal:
    """Under the User-administration lock: the actor's own sign-in, re-read.

    A fresh read (READ COMMITTED) of what the route checked at request
    start: the session is not ended or expired under the current policy,
    the User is still active with a password and no forced change is
    pending. Returns the fresh principal, whose permissions are the
    role's CURRENT grants. A refusal rolls back; nothing was written.
    """
    current = principal_where(session, UserSession.id == actor.session_id)
    if current is None or current.user_id != actor.user_id:
        session.rollback()
        raise AuthenticationRequiredError(AUTHENTICATION_REQUIRED_MESSAGE)
    if current.must_change_password:
        session.rollback()
        raise PasswordChangeRequiredError(PASSWORD_CHANGE_REQUIRED_MESSAGE)
    return current


def acting_user(session: Session, actor: Principal) -> Actor:
    """Take the User-administration lock, then re-read the actor (``recheck_actor``).

    The first statement of every role, user and password write: the
    returned keys are the actor's role's grants as committed when the
    lock was granted, and no other such write can change them (or any
    other grant, role assignment, activity or credential) before COMMIT.
    """
    acquire_user_administration_lock(session)
    fresh = recheck_actor(session, actor)
    return Actor(fresh.user_id, fresh.permissions)


def judge(session: Session, rule: Callable[[], None]) -> None:
    """Apply one permission rule under the lock; a refusal first rolls back,
    so nothing this transaction staged is written and the lock is released."""
    try:
        rule()
    except (PermissionDeniedError, LastPermissionHolderError):
        session.rollback()
        raise


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


def permission_holder_count(session: Session, permission: Permission) -> int:
    """How many active Users with a password hold ``permission`` through their role.

    Locked accounts and temporary passwords count: a lock expires or is
    cleared by a password set, and a temporary password still signs in.
    """
    query = (
        select(func.count())
        .select_from(User)
        .join(UserCredential, UserCredential.user_id == User.id)
        .join(
            RolePermission,
            (RolePermission.role_id == User.role_id) & (RolePermission.permission == permission),
        )
        .where(User.is_active)
    )
    return int(session.scalar(query) or 0)


def management_holder_counts(session: Session) -> dict[Permission, int]:
    """The holder count of each permission-management key (last-holder rule)."""
    return {key: permission_holder_count(session, key) for key in PERMISSION_MANAGEMENT}


def warn_if_correction_management_lost(session: Session) -> None:
    """Startup: log a WARNING when Users may be managed but correction
    permissions may not — a state only data from before Phase 14 slice 2
    can be in, which only the recovery command repairs."""
    counts = management_holder_counts(session)
    if (
        counts[Permission.MANAGE_USERS_AND_ROLES] > 0
        and counts[Permission.MANAGE_CORRECTION_PERMISSIONS] == 0
    ):
        logger.warning(_CORRECTION_MANAGEMENT_LOST)


def administrator_exists(session: Session) -> bool:
    """Whether an active User with a password may manage users and roles."""
    return permission_holder_count(session, Permission.MANAGE_USERS_AND_ROLES) > 0


def role_permissions(session: Session, role_id: int) -> frozenset[Permission]:
    """The keys ``role_id`` grants (a plain read; empty for an unknown role)."""
    return frozenset(
        Permission(permission)
        for permission in session.scalars(
            select(RolePermission.permission).where(RolePermission.role_id == role_id)
        )
    )


def role_holds_protected(session: Session, role_id: int) -> bool:
    """Whether ``role_id`` grants a protected key (the escalation guard's subject)."""
    return holds_protected(role_permissions(session, role_id))


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


class UserRef(NamedTuple):
    """Who recorded a history row: display values only, no ORM object."""

    id: int
    display_name: str
    avatar_updated_at: datetime.datetime | None


def user_refs(session: Session, user_ids: Collection[int]) -> dict[int, UserRef]:
    """The display reference of each given User — active or not (history).

    One plain SELECT, no lock; an id with no row is simply absent.
    """
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(User.id, User.display_name, User.avatar_image_updated_at).where(User.id.in_(ids))
    )
    return {
        int(user_id): UserRef(int(user_id), display_name, avatar_updated_at)
        for user_id, display_name, avatar_updated_at in rows
    }
