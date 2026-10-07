"""What an enrolled Scan Station may do (Phase 14 slice 4; owner decisions OD-S4-1, OD-S4-9).

An enrolled, unrevoked device authenticates the terminal for one Scan
Station (``app.application.station_devices``); the permission keys of
the ONE role applied at Scan Stations — ``application_policy.
scan_station_role_id``, set once by migration to the seeded Operator
role — authorize each kind of station command. Workers hold no role and
never authorize; a device never identifies a Worker.

- ``COMMAND_PERMISSION`` — the one key each ``StationCommand`` needs
  (PROFILE §20's ten Scan Station keys; no key is added or renamed);
- ``require_station_capability`` — refuse (403 K-1, nothing recorded)
  unless the station role holds every key the given kinds map to. A
  command calls it immediately after its post-lock idempotency re-check,
  so a committed command always replays and an identical retry racing
  the first attempt replays instead of being refused; a read calls it
  first;
- ``station_permissions`` — the station role's grants among the ten
  Scan Station keys (the station hides what it may not do);
- ``enrollment_permissions`` — what issuing an enrollment code needs
  now: ``MANAGE_SCAN_STATIONS``, plus ``MANAGE_CORRECTION_PERMISSIONS``
  while the station role holds a protected key (enrolling hands the
  station's correction permission to a new terminal; OD-S4-9).

Plain, unlocked reads only: production commands never lock ``roles``,
``role_permissions`` or ``users``; a grant edit committed while a
command runs applies from the statement that reads it. No rule is keyed
to a role name.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import user_access
from app.application.errors import StationPermissionDeniedError
from app.domain.enums import Permission, StationCommand
from app.infrastructure.models import ApplicationPolicy

_POLICY_ID: Final = 1

#: The ten Scan Station keys of PROJECT_PROFILE §20.
STATION_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    (
        Permission.SCAN_PN_BARCODES,
        Permission.SCAN_MACHINE_BARCODES,
        Permission.SCAN_WORKER_BARCODES,
        Permission.RECEIVE_QUANTITY,
        Permission.ASSIGN_QUANTITY_TO_MACHINE,
        Permission.CONFIRM_QUANTITY,
        Permission.COMPLETE_INTO_STOCKROOM,
        Permission.CONFIRM_SUGGESTED_ALLOCATION,
        Permission.ADJUST_SUGGESTED_ALLOCATION,
        Permission.UNDO_RECENT_SCANS,
    )
)

COMMAND_PERMISSION: Final[Mapping[StationCommand, Permission]] = MappingProxyType(
    {
        StationCommand.PN_SCAN: Permission.SCAN_PN_BARCODES,
        StationCommand.MACHINE_SCAN: Permission.SCAN_MACHINE_BARCODES,
        StationCommand.BADGE_SCAN: Permission.SCAN_WORKER_BARCODES,
        StationCommand.RECEIPT: Permission.RECEIVE_QUANTITY,
        StationCommand.MACHINE_ASSIGNMENT: Permission.ASSIGN_QUANTITY_TO_MACHINE,
        StationCommand.MACHINE_RELEASE: Permission.ASSIGN_QUANTITY_TO_MACHINE,
        StationCommand.AREA_COMPLETION: Permission.CONFIRM_QUANTITY,
        StationCommand.TRANSFER: Permission.CONFIRM_QUANTITY,
        StationCommand.MERGE: Permission.CONFIRM_QUANTITY,
        StationCommand.SCRAP: Permission.CONFIRM_QUANTITY,
        StationCommand.QUANTITY_ADDITION: Permission.CONFIRM_QUANTITY,
        StationCommand.STOCKING: Permission.COMPLETE_INTO_STOCKROOM,
        StationCommand.ALLOCATION: Permission.CONFIRM_SUGGESTED_ALLOCATION,
        StationCommand.ALLOCATION_ADJUSTMENT: Permission.ADJUST_SUGGESTED_ALLOCATION,
        StationCommand.UNDO: Permission.UNDO_RECENT_SCANS,
    }
)

#: K-1 ``{action}`` per command kind.
_ACTION: Final[Mapping[StationCommand, str]] = MappingProxyType(
    {
        StationCommand.PN_SCAN: "scan Part Number barcodes",
        StationCommand.MACHINE_SCAN: "scan Machine barcodes",
        StationCommand.BADGE_SCAN: "scan Worker badges",
        StationCommand.RECEIPT: "receive quantity",
        StationCommand.MACHINE_ASSIGNMENT: "assign quantity to Machines",
        StationCommand.MACHINE_RELEASE: "assign quantity to Machines",
        StationCommand.AREA_COMPLETION: "confirm quantity",
        StationCommand.TRANSFER: "confirm quantity",
        StationCommand.MERGE: "confirm quantity",
        StationCommand.SCRAP: "confirm quantity",
        StationCommand.QUANTITY_ADDITION: "confirm quantity",
        StationCommand.STOCKING: "complete production into the Stockroom",
        StationCommand.ALLOCATION: "confirm suggested allocations",
        StationCommand.ALLOCATION_ADJUSTMENT: "adjust suggested allocations",
        StationCommand.UNDO: "undo recent scans",
    }
)


def permission_denied_message(command: StationCommand) -> str:
    """K-1: the refusal of one command kind the station role does not grant."""
    return (
        f"Scan Stations are not allowed to {_ACTION[command]}. An administrator can grant"
        " it to the role applied at Scan Stations. Nothing was recorded."
    )


def station_role_id(session: Session) -> int:
    """The id of the role applied at Scan Stations (a plain read)."""
    return int(
        session.execute(
            select(ApplicationPolicy.scan_station_role_id).where(ApplicationPolicy.id == _POLICY_ID)
        ).scalar_one()
    )


def station_permissions(session: Session) -> frozenset[Permission]:
    """The station role's grants among the ten Scan Station keys."""
    return user_access.role_permissions(session, station_role_id(session)) & STATION_PERMISSIONS


def station_may(session: Session, command: StationCommand) -> bool:
    """Whether the station role grants the key of ``command`` (a plain read)."""
    return COMMAND_PERMISSION[command] in station_permissions(session)


def require_station_capability(session: Session, *commands: StationCommand) -> None:
    """Refuse unless the station role holds the key of every given kind.

    One unlocked read of the station role's grants; the refusal names
    every key the kinds map to (sorted by value) and explains the first
    kind whose key is missing. Writes nothing.
    """
    granted = station_permissions(session)
    missing = [command for command in commands if COMMAND_PERMISSION[command] not in granted]
    if missing:
        raise StationPermissionDeniedError(
            permission_denied_message(missing[0]),
            required=tuple(sorted({COMMAND_PERMISSION[command].value for command in commands})),
        )


def lacks_enrollment_guard(required: frozenset[Permission], held: frozenset[Permission]) -> bool:
    """Whether an enrollment is refused for the guard key alone (G-4)."""
    return (
        Permission.MANAGE_CORRECTION_PERMISSIONS in required
        and Permission.MANAGE_CORRECTION_PERMISSIONS not in held
    )


def enrollment_permissions(session: Session) -> frozenset[Permission]:
    """What issuing an enrollment code needs now (OD-S4-9)."""
    if user_access.role_holds_protected(session, station_role_id(session)):
        return frozenset(
            {Permission.MANAGE_SCAN_STATIONS, Permission.MANAGE_CORRECTION_PERMISSIONS}
        )
    return frozenset({Permission.MANAGE_SCAN_STATIONS})
