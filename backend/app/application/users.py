"""User services (Phase 13 slice 12 — Administration → Users).

Application-layer operations behind the application accounts: a User is
an account for Management, Administration and the other
non-Scan-Station views (PROJECT_PROFILE §7) — never a Worker, the Scan
Station production audit identity. The two are never merged: nothing
here reads the Worker registry, and no Scan Station service reads Users.

Users sign in with a password held in `user_credentials` (Phase 14
slice 1, `app.application.authentication`); deactivating a User ends
every open sign-in.

Who may change Users (Phase 14 slice 2; owner decision OD-P19): every
write takes the ``partflow:user-administration`` advisory lock first,
re-reads the acting User (``app.application.user_access``) and needs
``MANAGE_USERS_AND_ROLES`` on those fresh keys. Creating a User in a
role that holds a protected key (``app.domain.permissions``), moving a
User into or out of such a role, or changing whether such a User is
active also needs ``MANAGE_CORRECTION_PERMISSIONS`` (renames and avatars
do not). A role change or deactivation that would leave no active User
with a password holding ``MANAGE_USERS_AND_ROLES`` or
``MANAGE_CORRECTION_PERMISSIONS`` is refused (409).

Rules owned here (owner decision OD-8; slice 12 decisions):

- The login name goes through the one domain rule
  (``app.domain.user_login``): trimmed, non-ASCII refused, lowercase,
  ``[a-z0-9._@+-]{1,128}``; unique among ALL Users, inactive ones
  included — the ``uq_users_login_name`` UNIQUE is the authority, the
  pre-check only gives the friendly message naming the holder. It is an
  identifier, not a credential.
- The display name is required, not unique.
- Each User holds exactly one existing role.
- Users are deactivated, never deleted: no delete service exists.
- The optional avatar is stored on the row (CD1/OD-10) after the shared
  image validation (``app.application.images``) — the Worker protocol.
- ``users.theme_preference`` (OD-19) is never written or read here: the
  signed-in User's own toggle writes it
  (``authentication.set_own_theme_preference``, Phase 14 slice 8) and
  the session principal reads it.
- Every effective write appends exactly one ``audit_events`` row in the
  SAME transaction (entity ``User``; ``actor_user_id`` is the signed-in
  User since Phase 14 slice 2; ``actor_reference`` is legacy and stays
  NULL). Profile rows snapshot ``{login_name, display_name, role_id,
  is_active}``; avatar rows snapshot ``{"avatar": digest-or-null}`` —
  never bytes. Rejected writes and no-ops append nothing.

Each mutating service commits its own transaction and returns a
plain-value :class:`UserView` built before COMMIT, so no ORM object is
read after it. A mutation of an existing User locks its row first (``FOR
NO KEY UPDATE``), so concurrent writes serialize and every audit row's
``before_data`` is the committed predecessor within its facet. Every
field is validated and every pre-check query runs BEFORE the first
attribute assignment, and all flushes go through the conflict-
translating helper, so autoflush never emits a write outside it.
"""

import datetime
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from app.application import audit, authorization, images, user_access
from app.application.common import (
    UNSET,
    UnsetType,
    commit,
    flush,
    is_bindable_id,
    required_flag,
    required_text,
)
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.user_access import Principal
from app.domain.enums import AuditEntityType, AuditEventType, Permission, UserSessionEndReason
from app.domain.user_login import (
    EMPTY_LOGIN_NAME_MESSAGE,
    InvalidLoginNameError,
    normalize_login_name,
)
from app.infrastructure.models import Role, User

USER_CONFLICTS: Final = {
    "uq_users_login_name": "This login name is already used by another user.",
}

# Lock-first mode of every User write: FOR NO KEY UPDATE, the lock the
# write's own UPDATE takes anyway (a login change still upgrades to FOR
# UPDATE at its UPDATE — the login name has a UNIQUE index).
_EDIT_LOCK: Final = {"key_share": True}


class UserAvatar(NamedTuple):
    """A stored avatar as served: bytes, media type and cache version."""

    data: bytes
    content_type: str
    updated_at: datetime.datetime


class UserView(NamedTuple):
    """A User as answered — plain values only, read before COMMIT.

    No theme preference: it is stored only in Phase 13.
    """

    id: int
    login_name: str
    display_name: str
    role_id: int
    role_name: str
    is_active: bool
    # The avatar's cache version; None = no avatar.
    avatar_updated_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def profile_snapshot(user: User) -> dict[str, Any]:
    """The audited profile facet; avatar data never belongs to it."""
    return {
        "login_name": user.login_name,
        "display_name": user.display_name,
        "role_id": user.role_id,
        "is_active": user.is_active,
    }


def canonical_login_name(value: object) -> str:
    """Normalize input to the canonical login name or raise ``InvalidInputError``.

    Thin translation of the framework-independent domain rule into the
    application error vocabulary — the rule itself lives only in
    ``app.domain.user_login``.
    """
    if not isinstance(value, str):
        raise InvalidInputError(EMPTY_LOGIN_NAME_MESSAGE)
    try:
        return normalize_login_name(value)
    except InvalidLoginNameError as exc:
        raise InvalidInputError(str(exc)) from exc


def _role_id(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError("Choose a role for this user.")
    return value


def _require_role(session: Session, role_id: int) -> Role:
    role = session.get(Role, role_id) if is_bindable_id(role_id) else None
    if role is None:
        raise InvalidInputError(f"Role {role_id} does not exist.")
    return role


def require_role(session: Session, value: object) -> Role:
    """The existing role ``value`` names, or ``InvalidInputError`` (no write)."""
    return _require_role(session, _role_id(value))


def reject_duplicate_login(session: Session, login: str, exclude_id: int | None = None) -> None:
    query = select(User.display_name, User.is_active).where(User.login_name == login).limit(1)
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    holder = session.execute(query).first()
    if holder is not None:
        display_name, is_active = holder
        suffix = "" if is_active else " (inactive)"
        raise ConflictError(f"This login name is already used by {display_name}{suffix}.")


def lock_user(session: Session, user_id: int, *, with_avatar: bool = False) -> User:
    """Load one User under its row lock; avatar bytes only when compared."""
    if not is_bindable_id(user_id):
        raise NotFoundError(f"User {user_id} does not exist.")
    user = session.get(
        User,
        user_id,
        options=[undefer(User.avatar_image)] if with_avatar else None,
        populate_existing=True,
        with_for_update=_EDIT_LOCK,
    )
    if user is None:
        raise NotFoundError(f"User {user_id} does not exist.")
    return user


def _avatar_digest(user: User) -> dict[str, str | int] | None:
    if user.avatar_image is None or user.avatar_image_type is None:
        return None
    return images.image_digest(user.avatar_image, user.avatar_image_type)


def _act(session: Session, actor: Principal) -> authorization.Actor:
    """Lock, re-read the actor and require ``MANAGE_USERS_AND_ROLES`` on its fresh keys."""
    acting = user_access.acting_user(session, actor)
    user_access.judge(
        session,
        lambda: authorization.require(acting, (Permission.MANAGE_USERS_AND_ROLES,)),
    )
    return acting


def _guard(session: Session, acting: authorization.Actor, detail: str) -> None:
    user_access.judge(session, lambda: authorization.require_guard(acting, detail))


def user_view(session: Session, user: User) -> UserView:
    """The answer, read from this transaction (role name included)."""
    role = session.get(Role, user.role_id)
    assert role is not None  # the FK guarantees it
    return UserView(
        id=user.id,
        login_name=user.login_name,
        display_name=user.display_name,
        role_id=user.role_id,
        role_name=role.name,
        is_active=user.is_active,
        avatar_updated_at=user.avatar_image_updated_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def list_users(session: Session) -> list[UserView]:
    """All Users, active and inactive, by display name, id; no image bytes."""
    rows = session.execute(
        select(User, Role.name)
        .join(Role, Role.id == User.role_id)
        .order_by(User.display_name, User.id)
    )
    return [
        UserView(
            id=user.id,
            login_name=user.login_name,
            display_name=user.display_name,
            role_id=user.role_id,
            role_name=role_name,
            is_active=user.is_active,
            avatar_updated_at=user.avatar_image_updated_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
        for user, role_name in rows
    ]


def create_user(
    session: Session,
    *,
    login_name: object,
    display_name: object,
    role_id: object,
    actor: Principal,
) -> UserView:
    acting = _act(session, actor)
    clean_name = required_text(display_name, "Name")
    login = canonical_login_name(login_name)
    role = require_role(session, role_id)
    if user_access.role_holds_protected(session, role.id):
        _guard(session, acting, authorization.PROTECTED_ROLE_MESSAGE)
    reject_duplicate_login(session, login)
    user = User(login_name=login, display_name=clean_name, role_id=role.id, is_active=True)
    session.add(user)
    # The id is the audit entity id; a login race lost here surfaces as
    # the same conflict as one lost at COMMIT.
    flush(session, USER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data=None,
        after_data=profile_snapshot(user),
        actor_user_id=actor.user_id,
    )
    view = user_view(session, user)
    commit(session, USER_CONFLICTS)
    return view


def update_user(
    session: Session,
    user_id: int,
    *,
    login_name: object = UNSET,
    display_name: object = UNSET,
    role_id: object = UNSET,
    is_active: object = UNSET,
    actor: Principal,
) -> UserView:
    """Apply the provided profile fields; a no-op writes and audits nothing.

    A role change touching a protected role, or an activity change of a
    User in one, also needs ``MANAGE_CORRECTION_PERMISSIONS`` (checked
    after every read the change needs, before any write). A role change
    or deactivation counts the management keys' holders before the first
    assignment and again after the flush; losing the last holder of one
    refuses everything (409). Deactivation ends every
    open sign-in of the User (Phase 14 slice 1): the credential row is
    locked after the User row and before any assignment — before a login
    rename upgrades the User lock at its UPDATE — so a concurrent sign-in
    or own password change either committed first (its session is ended
    here) or waits and then reads the User inactive. Reactivation never
    revives an ended session.
    """
    acting = _act(session, actor)
    user = lock_user(session, user_id)
    before = profile_snapshot(user)

    changes: dict[str, Any] = {}
    if not isinstance(display_name, UnsetType):
        clean_name = required_text(display_name, "Name")
        if clean_name != user.display_name:
            changes["display_name"] = clean_name
    if not isinstance(login_name, UnsetType):
        # A case or whitespace variant of the stored login is no change.
        login = canonical_login_name(login_name)
        if login != user.login_name:
            changes["login_name"] = login
    if not isinstance(role_id, UnsetType):
        new_role_id = _role_id(role_id)
        if new_role_id != user.role_id:
            changes["role_id"] = new_role_id
    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "User active status")
        if active != user.is_active:
            changes["is_active"] = active
    if not changes:
        return user_view(session, user)
    if "role_id" in changes:
        require_role(session, changes["role_id"])
    if ("role_id" in changes or "is_active" in changes) and (
        user_access.role_holds_protected(session, user.role_id)
        or ("role_id" in changes and user_access.role_holds_protected(session, changes["role_id"]))
    ):
        _guard(
            session,
            acting,
            authorization.PROTECTED_ROLE_MESSAGE
            if "role_id" in changes
            else authorization.PROTECTED_USER_MESSAGE,
        )
    if "login_name" in changes:
        reject_duplicate_login(session, changes["login_name"], exclude_id=user.id)
    deactivating = changes.get("is_active") is False
    if deactivating:
        user_access.lock_credential(session, user.id)
    # Last-holder rule: only a role change or a deactivation can remove a holder.
    holders_before = (
        user_access.management_holder_counts(session)
        if deactivating or "role_id" in changes
        else None
    )

    # Read-before-assign: no query runs from here to the explicit flush.
    for field, value in changes.items():
        setattr(user, field, value)
    user.updated_at = func.now()
    flush(session, USER_CONFLICTS)
    if holders_before is not None:
        holders_after = user_access.management_holder_counts(session)
        user_access.judge(
            session, lambda: authorization.reject_holder_loss(holders_before, holders_after)
        )
    if deactivating:
        user_access.end_user_sessions(session, user.id, UserSessionEndReason.USER_DEACTIVATED)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data=before,
        after_data=profile_snapshot(user),
        actor_user_id=actor.user_id,
    )
    view = user_view(session, user)
    commit(session, USER_CONFLICTS)
    return view


def set_user_avatar(
    session: Session, user_id: int, *, data: bytes, declared_type: str | None, actor: Principal
) -> UserView:
    """Store or replace the avatar; identical bytes and type are a no-op.

    The image is validated before the lock — nothing is written on a
    refusal. The no-op makes an upload safely retryable after an
    unknown outcome.
    """
    content_type = images.validate_image(data, declared_type)
    _act(session, actor)
    user = lock_user(session, user_id, with_avatar=True)
    before = _avatar_digest(user)
    after = images.image_digest(data, content_type)
    if before == after:
        return user_view(session, user)

    user.avatar_image = data
    user.avatar_image_type = content_type
    user.avatar_image_updated_at = func.now()
    user.updated_at = func.now()
    flush(session, USER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data={"avatar": before},
        after_data={"avatar": after},
        actor_user_id=actor.user_id,
    )
    view = user_view(session, user)
    commit(session, USER_CONFLICTS)
    return view


def remove_user_avatar(session: Session, user_id: int, *, actor: Principal) -> UserView:
    """Remove the avatar; a User without one is a no-op."""
    _act(session, actor)
    user = lock_user(session, user_id, with_avatar=True)
    before = _avatar_digest(user)
    if before is None:
        return user_view(session, user)

    user.avatar_image = None
    user.avatar_image_type = None
    user.avatar_image_updated_at = None
    user.updated_at = func.now()
    flush(session, USER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.USER,
        entity_id=str(user.id),
        before_data={"avatar": before},
        after_data={"avatar": None},
        actor_user_id=actor.user_id,
    )
    view = user_view(session, user)
    commit(session, USER_CONFLICTS)
    return view


def get_user_avatar(session: Session, user_id: int) -> UserAvatar:
    if not is_bindable_id(user_id):
        raise NotFoundError(f"User {user_id} does not exist.")
    user = session.get(User, user_id, options=[undefer(User.avatar_image)])
    if user is None:
        raise NotFoundError(f"User {user_id} does not exist.")
    if (
        user.avatar_image is None
        or user.avatar_image_type is None
        or user.avatar_image_updated_at is None
    ):
        raise NotFoundError("This user has no avatar.")
    return UserAvatar(user.avatar_image, user.avatar_image_type, user.avatar_image_updated_at)
