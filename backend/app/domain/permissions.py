"""Permission sets with a rule attached (Phase 14 slice 2; owner decisions OD-P10, OD-P19).

Framework-independent groupings of the ``Permission`` vocabulary:

- ``CORRECTION_PERMISSIONS`` — the four correction keys, in the order
  Administration → Correction permissions lists them (PROJECT_PROFILE
  §20); a role change touching only these needs only
  ``MANAGE_CORRECTION_PERMISSIONS``;
- ``PERMISSION_MANAGEMENT`` — the two keys that manage permissions; the
  last active User with a password holding either may not lose it;
- ``PROTECTED_PERMISSIONS`` — the correction keys plus the permission to
  manage them: granting or revoking one, or changing who holds one
  through a User's role, activity or password, also needs
  ``MANAGE_CORRECTION_PERMISSIONS`` (the escalation guard);
- ``INERT_PERMISSIONS`` — keys no route requires yet (each is recorded
  with its owner under the roadmap's Deferred list).

Permission keys are the only authority: nothing here names a role.
"""

from collections.abc import Iterable
from typing import Final

from app.domain.enums import Permission

CORRECTION_PERMISSIONS: Final = (
    Permission.UNDO_RECENT_SCANS,
    Permission.PERFORM_QUANTITY_CORRECTIONS,
    Permission.EDIT_WORK_ORDER_ALLOCATION,
    Permission.PERFORM_HISTORICAL_CORRECTIONS,
)
PERMISSION_MANAGEMENT: Final = (
    Permission.MANAGE_USERS_AND_ROLES,
    Permission.MANAGE_CORRECTION_PERMISSIONS,
)
PROTECTED_PERMISSIONS: Final = frozenset(
    (*CORRECTION_PERMISSIONS, Permission.MANAGE_CORRECTION_PERMISSIONS)
)
INERT_PERMISSIONS: Final = frozenset(
    (
        Permission.MANAGE_SCAN_BEHAVIOR,
        Permission.RESOLVE_EXCEPTIONAL_SITUATIONS,
        Permission.EXPORT_REPORTS,
        Permission.PERFORM_QUANTITY_CORRECTIONS,
        Permission.PERFORM_HISTORICAL_CORRECTIONS,
    )
)


def holds_protected(permissions: Iterable[Permission]) -> bool:
    """Whether any of ``permissions`` is a protected key."""
    return any(permission in PROTECTED_PERMISSIONS for permission in permissions)
