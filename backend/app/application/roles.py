"""Role services (Phase 13 slice 12 — Administration → Roles & permissions).

Application-layer operations behind the role configuration: named,
editable roles and the permissions each one grants (owner decision OD-8;
PROJECT_PROFILE §20). The three seeded roles (Administrator, Manager,
Operator) carry exactly the grants PROJECT_PROFILE §20 states as their
initial grants; afterwards they are ordinary rows. Permission keys are
the only authority: no rule here is keyed to a role name, and any role
may be granted or revoked any key.

Configuration only — nothing reads a role or a permission to allow or
refuse an action until Phase 14, and no endpoint checks or simulates an
identity before then.

Rules owned here:

- A role name is required, trimmed and unique (case-sensitive; the
  ``uq_roles_name`` UNIQUE is the authority, the pre-check only gives
  the friendly message).
- Permissions are the closed ``Permission`` vocabulary. Edits are deltas
  (``grant_permissions`` / ``revoke_permissions``): granting a held key
  or revoking a missing one is a no-op, so a retry is safe and two
  editors changing different keys never revert each other. The same key
  in both lists is refused.
- Roles are renamed, never deleted or deactivated: no such service
  exists.
- Every effective write appends exactly one ``audit_events`` row in the
  SAME transaction (entity ``Role``, ``actor_reference`` NULL until
  Phase 14) snapshotting ``{name, permissions}`` (sorted). Rejected
  writes and no-ops append nothing.

Each mutating service commits its own transaction and returns a
plain-value :class:`RoleView` built before COMMIT, so no ORM object is
read after it. An update locks the role row first (``FOR NO KEY
UPDATE``), so concurrent edits serialize and every audit row's
``before_data`` is the committed predecessor. Every statement that can
violate a constraint is emitted only inside the conflict-translating
``flush``: the revoke DELETE runs before any assignment, so its
autoflush has nothing pending.
"""

import datetime
from collections.abc import Iterable
from typing import Any, Final, NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import (
    UNSET,
    UnsetType,
    commit,
    flush,
    is_bindable_id,
    required_text,
)
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.enums import AuditEntityType, AuditEventType, Permission
from app.infrastructure.models import Role, RolePermission, User

_ROLE_CONFLICTS: Final = {"uq_roles_name": "A role with this name already exists."}

# Lock-first mode of a role update: FOR NO KEY UPDATE — the lock the
# edit's own UPDATE takes anyway — so a user write's FK check (FOR KEY
# SHARE) never waits on a grant edit. A rename still upgrades to FOR
# UPDATE at its UPDATE (the name has a UNIQUE index).
_EDIT_LOCK: Final = {"key_share": True}


class RoleView(NamedTuple):
    """A role as answered — plain values only, read before COMMIT."""

    id: int
    name: str
    # Sorted by value.
    permissions: tuple[Permission, ...]
    # Users holding the role, active and inactive.
    user_count: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


def role_snapshot(name: str, permissions: Iterable[Permission]) -> dict[str, Any]:
    """The audited role facet: the name and the sorted permission keys."""
    return {"name": name, "permissions": sorted(p.value for p in permissions)}


def _permission_set(value: object) -> frozenset[Permission]:
    """A list or tuple of ``Permission`` members; duplicates collapse."""
    if not isinstance(value, list | tuple) or not all(isinstance(p, Permission) for p in value):
        raise InvalidInputError("Unknown permission.")
    return frozenset(value)


def _sorted(permissions: Iterable[Permission]) -> tuple[Permission, ...]:
    return tuple(sorted(permissions, key=lambda permission: permission.value))


def _held_permissions(session: Session, role_id: int) -> frozenset[Permission]:
    return frozenset(
        Permission(value)
        for value in session.scalars(
            select(RolePermission.permission).where(RolePermission.role_id == role_id)
        )
    )


def _user_count(session: Session, role_id: int) -> int:
    return int(
        session.scalar(select(func.count()).select_from(User).where(User.role_id == role_id)) or 0
    )


def _reject_duplicate_name(session: Session, name: str, exclude_id: int | None = None) -> None:
    query = select(Role.id).where(Role.name == name).limit(1)
    if exclude_id is not None:
        query = query.where(Role.id != exclude_id)
    if session.scalar(query) is not None:
        raise ConflictError(_ROLE_CONFLICTS["uq_roles_name"])


def _view(session: Session, role: Role, permissions: Iterable[Permission]) -> RoleView:
    return RoleView(
        id=role.id,
        name=role.name,
        permissions=_sorted(permissions),
        user_count=_user_count(session, role.id),
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def list_roles(session: Session) -> list[RoleView]:
    """Every role by name, id, with its grants and its user count."""
    roles = list(session.scalars(select(Role).order_by(Role.name, Role.id)))
    grants: dict[int, list[Permission]] = {}
    for role_id, permission in session.execute(
        select(RolePermission.role_id, RolePermission.permission)
    ):
        grants.setdefault(role_id, []).append(Permission(permission))
    counts = {
        role_id: int(count)
        for role_id, count in session.execute(
            select(User.role_id, func.count()).group_by(User.role_id)
        )
    }
    return [
        RoleView(
            id=role.id,
            name=role.name,
            permissions=_sorted(grants.get(role.id, ())),
            user_count=counts.get(role.id, 0),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
        for role in roles
    ]


def create_role(session: Session, *, name: object, permissions: object) -> RoleView:
    clean_name = required_text(name, "Role name")
    granted = _permission_set(permissions)
    _reject_duplicate_name(session, clean_name)
    role = Role(name=clean_name)
    session.add(role)
    # The id is the audit entity id; a name race lost here surfaces as
    # the same conflict as one lost at COMMIT.
    flush(session, _ROLE_CONFLICTS)
    for permission in granted:
        session.add(RolePermission(role_id=role.id, permission=permission.value))
    flush(session, _ROLE_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.ROLE,
        entity_id=str(role.id),
        before_data=None,
        after_data=role_snapshot(clean_name, granted),
    )
    view = _view(session, role, granted)
    commit(session, _ROLE_CONFLICTS)
    return view


def update_role(
    session: Session,
    role_id: int,
    *,
    name: object = UNSET,
    grant_permissions: object = (),
    revoke_permissions: object = (),
) -> RoleView:
    """Rename and grant/revoke as deltas; a no-op writes and audits nothing."""
    if not is_bindable_id(role_id):
        raise NotFoundError(f"Role {role_id} does not exist.")
    role = session.get(Role, role_id, with_for_update=_EDIT_LOCK, populate_existing=True)
    if role is None:
        raise NotFoundError(f"Role {role_id} does not exist.")
    held = _held_permissions(session, role.id)

    new_name: str | None = None
    if not isinstance(name, UnsetType):
        clean_name = required_text(name, "Role name")
        if clean_name != role.name:
            new_name = clean_name
    grant = _permission_set(grant_permissions)
    revoke = _permission_set(revoke_permissions)
    if grant & revoke:
        raise InvalidInputError("A permission cannot be granted and revoked in the same change.")
    to_add = grant - held
    to_remove = revoke & held
    if new_name is None and not to_add and not to_remove:
        return _view(session, role, held)
    if new_name is not None:
        _reject_duplicate_name(session, new_name, exclude_id=role.id)

    before = role_snapshot(role.name, held)
    after_permissions = (held | to_add) - to_remove
    # The DELETE runs before any assignment or add, so its autoflush has
    # nothing pending; it touches only this locked role's rows.
    if to_remove:
        session.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission.in_([permission.value for permission in to_remove]),
            )
        )
    # Read-before-assign: no query runs from here to the explicit flush.
    if new_name is not None:
        role.name = new_name
    role.updated_at = func.now()
    for permission in to_add:
        session.add(RolePermission(role_id=role.id, permission=permission.value))
    flush(session, _ROLE_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.ROLE,
        entity_id=str(role.id),
        before_data=before,
        after_data=role_snapshot(role.name, after_permissions),
    )
    view = _view(session, role, after_permissions)
    commit(session, _ROLE_CONFLICTS)
    return view
