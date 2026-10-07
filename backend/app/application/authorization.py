"""Permission checks of the Administration and Management surfaces (Phase 14 slices 2–3).

Plain rules over an :class:`Actor` — the acting User's id and the
permission keys its role grants — with no model, no framework and no
read of its own:

- ``require`` — every required key held, else ``PermissionDeniedError``
  naming every required key (sorted by value); writes nothing;
- the content rules — the keys one request needs beyond its route's
  static keys, judged on the request AS SENT (never on the effective
  delta), so the answer depends only on the request and the actor's
  keys and a request naming a protected key fails closed:

  - an Area's Worker Session timeout override is a Worker session
    policy edit (``MANAGE_WORKER_SESSION_POLICIES``); every other Area
    field needs ``MANAGE_AREAS``;
  - a role change naming a protected key (``app.domain.permissions``)
    needs ``MANAGE_CORRECTION_PERMISSIONS``; a rename, any other key, or
    an empty change needs ``MANAGE_USERS_AND_ROLES`` — so a change of
    correction keys only needs ``MANAGE_CORRECTION_PERMISSIONS`` alone;
  - a Work Order edit needs ``MANAGE_WORK_ORDERS`` for its header and
    ``EDIT_WORK_ORDER_DEMAND`` for its demand lines (slice 3, OD-P10);
  - a Hot list change that adds or removes an entry needs
    ``SET_DEMAND_PRIORITY``, one that only reorders needs
    ``REORDER_HOT_ITEMS`` — whatever action the request names, Undo and
    Redo included (slice 3, OD-P10).

Where the actor's keys come from is the caller's business: a route
checks the request's principal (a fast fail), and the role, user and
password services check again on the actor re-read under the
User-administration lock (``app.application.user_access``).
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from app.application.errors import (
    PERMISSION_DENIED_MESSAGE,
    LastPermissionHolderError,
    PermissionDeniedError,
)
from app.domain import hot_list
from app.domain.enums import Permission
from app.domain.permissions import CORRECTION_PERMISSIONS, holds_protected

_TIMEOUT_OVERRIDE = "worker_session_timeout_minutes"
_WORK_ORDER_HEADER_FIELDS = ("work_order_number", "due_date")
_WORK_ORDER_LINE_FIELDS = ("line_edits", "new_lines")

#: G-1: a role change naming a protected key, by an actor who may not manage them.
CORRECTION_GRANT_MESSAGE = (
    "Granting or removing correction permissions, or the permission to manage them, needs"
    " the Manage correction permissions permission."
)


class Actor(NamedTuple):
    """The acting User: its id and the keys its role grants (plain values)."""

    user_id: int
    permissions: frozenset[Permission]


def require(actor: Actor, required: Iterable[Permission], *, detail: str | None = None) -> None:
    """Refuse unless ``actor`` holds every key of ``required``.

    The refusal names every required key, held or not, sorted by value;
    ``detail`` replaces the generic message.
    """
    keys = frozenset(required)
    if keys <= actor.permissions:
        return
    raise PermissionDeniedError(
        detail or PERMISSION_DENIED_MESSAGE,
        required=tuple(sorted(key.value for key in keys)),
    )


def area_create_permissions(worker_session_timeout_minutes: object) -> frozenset[Permission]:
    """A new Area needs ``MANAGE_AREAS``; giving it a timeout override also
    needs ``MANAGE_WORKER_SESSION_POLICIES``."""
    if worker_session_timeout_minutes is None:
        return frozenset({Permission.MANAGE_AREAS})
    return frozenset({Permission.MANAGE_AREAS, Permission.MANAGE_WORKER_SESSION_POLICIES})


def area_update_permissions(fields: Mapping[str, object]) -> frozenset[Permission]:
    """The keys an Area edit needs, by the fields it sends.

    The timeout override (a null clears it — also a policy edit) needs
    ``MANAGE_WORKER_SESSION_POLICIES``; any other field, or no field at
    all, needs ``MANAGE_AREAS``.
    """
    required: set[Permission] = set()
    if _TIMEOUT_OVERRIDE in fields:
        required.add(Permission.MANAGE_WORKER_SESSION_POLICIES)
    if not fields or set(fields) - {_TIMEOUT_OVERRIDE}:
        required.add(Permission.MANAGE_AREAS)
    return frozenset(required)


def role_create_permissions(permissions: Iterable[Permission]) -> frozenset[Permission]:
    """A new role needs ``MANAGE_USERS_AND_ROLES``; one granted a protected
    key also needs ``MANAGE_CORRECTION_PERMISSIONS``."""
    if holds_protected(permissions):
        return frozenset(
            {Permission.MANAGE_USERS_AND_ROLES, Permission.MANAGE_CORRECTION_PERMISSIONS}
        )
    return frozenset({Permission.MANAGE_USERS_AND_ROLES})


def role_update_permissions(
    *, name_given: bool, grant: Iterable[Permission], revoke: Iterable[Permission]
) -> frozenset[Permission]:
    """The keys a role edit needs, by what it names.

    A protected key in either list needs ``MANAGE_CORRECTION_PERMISSIONS``;
    a name, a key outside the correction keys, or nothing at all needs
    ``MANAGE_USERS_AND_ROLES``.
    """
    keys = frozenset(grant) | frozenset(revoke)
    required: set[Permission] = set()
    if holds_protected(keys):
        required.add(Permission.MANAGE_CORRECTION_PERMISSIONS)
    if name_given or keys - frozenset(CORRECTION_PERMISSIONS) or not keys:
        required.add(Permission.MANAGE_USERS_AND_ROLES)
    return frozenset(required)


def require_role_change(actor: Actor, required: frozenset[Permission]) -> None:
    """``require`` for a role change: G-1 when the change needs
    ``MANAGE_CORRECTION_PERMISSIONS`` and the actor lacks it, else A2."""
    lacks_guard_key = (
        Permission.MANAGE_CORRECTION_PERMISSIONS in required
        and Permission.MANAGE_CORRECTION_PERMISSIONS not in actor.permissions
    )
    require(actor, required, detail=CORRECTION_GRANT_MESSAGE if lacks_guard_key else None)


def work_order_update_permissions(fields: Mapping[str, object]) -> frozenset[Permission]:
    """The keys a Work Order edit needs, by what it sends.

    A header field (``work_order_number`` / ``due_date``, a null
    included) needs ``MANAGE_WORK_ORDERS``; non-empty ``line_edits`` or
    ``new_lines`` need ``EDIT_WORK_ORDER_DEMAND``; a request asking for
    nothing at all needs ``MANAGE_WORK_ORDERS``.
    """
    required: set[Permission] = set()
    if any(name in fields for name in _WORK_ORDER_HEADER_FIELDS):
        required.add(Permission.MANAGE_WORK_ORDERS)
    if any(fields.get(name) for name in _WORK_ORDER_LINE_FIELDS):
        required.add(Permission.EDIT_WORK_ORDER_DEMAND)
    if not required:
        required.add(Permission.MANAGE_WORK_ORDERS)
    return frozenset(required)


def hot_list_change_permissions(
    expected: Sequence[int], new: Sequence[int]
) -> frozenset[Permission]:
    """The key a Hot list change needs: ``SET_DEMAND_PRIORITY`` when it
    changes the list's members, ``REORDER_HOT_ITEMS`` when it only
    reorders them. The action label never selects the key."""
    if hot_list.changes_membership(expected, new):
        return frozenset({Permission.SET_DEMAND_PRIORITY})
    return frozenset({Permission.REORDER_HOT_ITEMS})


# ---------------------------------------------------------------------------
# The permission-management guard and the last-holder rule (OD-P19)
# ---------------------------------------------------------------------------

#: G-2: the target User's role is protected; their password or activity changes.
PROTECTED_USER_MESSAGE = (
    "This user's role holds correction permissions or the permission to manage them."
    " Setting their password or changing whether they are active needs the Manage"
    " correction permissions permission."
)
#: G-3: a User moves into or out of a protected role (or is created in one).
PROTECTED_ROLE_MESSAGE = (
    "Giving a user a role that holds correction permissions or the permission to manage"
    " them, or moving them out of such a role, needs the Manage correction permissions"
    " permission."
)
_LAST_HOLDER_MESSAGES = (
    (
        Permission.MANAGE_USERS_AND_ROLES,
        "This change would leave no active user with a password who may manage users and"
        " roles. Give that permission to another active user first.",
    ),
    (
        Permission.MANAGE_CORRECTION_PERMISSIONS,
        "This change would leave no active user with a password who may manage correction"
        " permissions. Give that permission to another active user first.",
    ),
)


def require_guard(actor: Actor, detail: str) -> None:
    """The escalation guard: a write that changes who holds a protected key
    also needs ``MANAGE_CORRECTION_PERMISSIONS``. The refusal names both
    keys the write requires (it is reached only behind
    ``MANAGE_USERS_AND_ROLES``)."""
    require(
        actor,
        (Permission.MANAGE_USERS_AND_ROLES, Permission.MANAGE_CORRECTION_PERMISSIONS),
        detail=detail,
    )


def reject_holder_loss(before: Mapping[Permission, int], after: Mapping[Permission, int]) -> None:
    """Refuse a change that takes the last holder of a management key.

    Judged per key in ``PERMISSION_MANAGEMENT`` order (users and roles
    first): ``before >= 1`` and ``after == 0`` refuses; a state that
    already had no holder is never blamed on this change.
    """
    for key, message in _LAST_HOLDER_MESSAGES:
        if before.get(key, 0) >= 1 and after.get(key, 0) == 0:
            raise LastPermissionHolderError(message)
