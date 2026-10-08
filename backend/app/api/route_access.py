"""Who may call each route (Phase 14 slices 2–5; owner decisions OD-P6, OD-P7, OD-P10, OD-P19).

One registry classifies every API route by ``(METHOD, path)``; a test
(``tests/test_route_access.py``) fails on any route that is missing,
stale, or whose declared dependencies disagree with its class. A new
route is added here in the same change.

- ``PUBLIC`` — no sign-in: the Production Board, the Station Selector
  lists, the active Planned Route list, the Due Soon policy the board and
  the Scan Station read, image reads, health, sign-in and first-run
  setup. May read the optional principal only.
- ``STATION`` — a Scan Station route: requires an enrolled station
  device (``RequireStationDevice``); never resolves the User principal;
  keys of the role applied at Scan Stations are checked by the
  Application per command (``app.application.station_access``).
- ``SIGNED_IN`` — any signed-in User without a pending forced password
  change (``RequirePermission()``): the Administration reads and the
  Asset Tag format read.
- ``PERMISSION`` — ``RequirePermission(*requires)``; ``conditional``
  names the keys a content or data rule may add for a given request
  (``app.application.authorization``; the guard of
  ``app.application.users`` / ``roles`` / ``authentication``). A
  Management read instead names ``any_of``: ``RequireAnyPermission`` —
  View production data or a key whose action a view reading the route
  hosts (OD-P7). A route has static/conditional keys or ``any_of``,
  never both.

``FRAMEWORK_ROUTES`` are FastAPI's own documentation routes (public).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.domain.enums import Permission


class Access(StrEnum):
    PUBLIC = "PUBLIC"
    STATION = "STATION"
    SIGNED_IN = "SIGNED_IN"
    PERMISSION = "PERMISSION"


@dataclass(frozen=True)
class RouteAccess:
    access: Access
    # Static keys: the route's RequirePermission.
    requires: frozenset[Permission] = field(default_factory=frozenset)
    # Keys a content or data rule may add for a given request.
    conditional: frozenset[Permission] = field(default_factory=frozenset)
    # Only PUT /api/session/password: allowed while a password change is pending.
    password_change_allowed: bool = False
    # A Management read: RequireAnyPermission — any one of these keys opens it.
    any_of: frozenset[Permission] = field(default_factory=frozenset)


def _keys(*keys: Permission) -> frozenset[Permission]:
    return frozenset(keys)


_PUBLIC: Final = RouteAccess(Access.PUBLIC)
_STATION: Final = RouteAccess(Access.STATION)
_SIGNED_IN: Final = RouteAccess(Access.SIGNED_IN)


def _permission(
    *requires: Permission, conditional: frozenset[Permission] = frozenset()
) -> RouteAccess:
    return RouteAccess(Access.PERMISSION, _keys(*requires), conditional)


def _any_of(*keys: frozenset[Permission]) -> RouteAccess:
    """A Management read: the union of the read sets of the views reading it."""
    return RouteAccess(Access.PERMISSION, any_of=frozenset[Permission]().union(*keys))


_MUAR: Final = Permission.MANAGE_USERS_AND_ROLES
_MCP: Final = Permission.MANAGE_CORRECTION_PERMISSIONS
_CSS: Final = Permission.CONFIGURE_SYSTEM_SETTINGS
_MWSP: Final = Permission.MANAGE_WORKER_SESSION_POLICIES
_MWO: Final = Permission.MANAGE_WORK_ORDERS
_EWOD: Final = Permission.EDIT_WORK_ORDER_DEMAND
_EWOA: Final = Permission.EDIT_WORK_ORDER_ALLOCATION
_MM: Final = Permission.MANAGE_MACHINES
_MRT: Final = Permission.MANAGE_ROUTE_TEMPLATES
_MPNM: Final = Permission.MANAGE_PART_NUMBER_MASTER
_VPD: Final = Permission.VIEW_PRODUCTION_DATA

# The Management views' read sets (OD-P7).
_WO_VIEW: Final = _keys(_VPD, _MWO, _EWOD, _EWOA)
_PRIORITY_VIEW: Final = _keys(_VPD, Permission.SET_DEMAND_PRIORITY, Permission.REORDER_HOT_ITEMS)
_TRACKING_VIEW: Final = _keys(_VPD, _EWOA, Permission.ASSIGN_ROUTES)
_MACHINES_VIEW: Final = _keys(_VPD, _MM)
_ROUTES_VIEW: Final = _keys(_VPD, _MRT)
_PN_VIEW: Final = _keys(_VPD, _MPNM)
_AREA_BOARD_VIEW: Final = _keys(_VPD)

_STATION_COMMANDS: Final = (
    "badge-scans",
    "scans/resolve",
    "machine-scans/resolve",
    "transfers",
    "stockings",
    "machine-assignments",
    "machine-releases",
    "area-completions",
    "merges",
    "scraps",
    "quantity-additions",
    "receipts",
    "undos",
)

ROUTE_ACCESS: Final[Mapping[tuple[str, str], RouteAccess]] = {
    # --- PUBLIC -------------------------------------------------------------
    ("GET", "/api/health"): _PUBLIC,
    ("GET", "/api/session"): _PUBLIC,
    ("POST", "/api/session"): _PUBLIC,
    ("DELETE", "/api/session"): _PUBLIC,
    ("GET", "/api/setup"): _PUBLIC,
    ("POST", "/api/setup/administrator"): _PUBLIC,
    ("GET", "/api/production-board"): _PUBLIC,
    ("GET", "/api/departments"): _PUBLIC,
    ("GET", "/api/areas"): _PUBLIC,
    ("GET", "/api/operations"): _PUBLIC,
    ("GET", "/api/scan-stations"): _PUBLIC,
    ("GET", "/api/scan-stations/{station_id}"): _PUBLIC,
    ("GET", "/api/machines"): _PUBLIC,
    ("GET", "/api/route-templates"): _PUBLIC,
    ("GET", "/api/policies/due-soon"): _PUBLIC,
    ("GET", "/api/workers/{worker_id}/avatar"): _PUBLIC,
    ("GET", "/api/users/{user_id}/avatar"): _PUBLIC,
    ("GET", "/api/part-numbers/image"): _PUBLIC,
    # Gated by the one-time enrollment code (Phase 14 slice 4).
    ("POST", "/api/scan-stations/{station_id}/device-activations"): _PUBLIC,
    # --- STATION ------------------------------------------------------------
    ("GET", "/api/scan-stations/{station_id}/context"): _STATION,
    **{
        ("POST", f"/api/scan-stations/{{station_id}}/{command}"): _STATION
        for command in _STATION_COMMANDS
    },
    ("GET", "/api/scan-stations/{station_id}/undo-preview/{device_event_id}"): _STATION,
    ("PUT", "/api/scan-stations/{station_id}/theme-preference"): _STATION,
    ("GET", "/api/areas/{area_id}/inventory"): _STATION,
    ("GET", "/api/allocations/suggestion"): _STATION,
    # The Stockroom station's receiving confirmation (station_id required).
    ("POST", "/api/allocations"): _STATION,
    # --- SIGNED_IN ----------------------------------------------------------
    ("GET", "/api/workers"): _SIGNED_IN,
    ("GET", "/api/users"): _SIGNED_IN,
    ("GET", "/api/roles"): _SIGNED_IN,
    ("GET", "/api/policies/worker-sessions"): _SIGNED_IN,
    ("GET", "/api/policies/correction-permissions"): _SIGNED_IN,
    ("GET", "/api/policies/data-retention"): _SIGNED_IN,
    ("GET", "/api/policies/sign-in"): _SIGNED_IN,
    ("GET", "/api/scan-station-devices"): _SIGNED_IN,
    ("PUT", "/api/session/password"): RouteAccess(Access.SIGNED_IN, password_change_allowed=True),
    ("PUT", "/api/session/theme-preference"): _SIGNED_IN,
    # Read by Administration and by Management → Machines (OD-S2-2).
    ("GET", "/api/barcode-configuration/machine-asset-tag-format"): _SIGNED_IN,
    # --- PERMISSION (static) ------------------------------------------------
    ("POST", "/api/departments"): _permission(Permission.MANAGE_DEPARTMENTS),
    ("PATCH", "/api/departments/{department_id}"): _permission(Permission.MANAGE_DEPARTMENTS),
    ("POST", "/api/operations"): _permission(Permission.MANAGE_OPERATIONS),
    ("PATCH", "/api/operations/{operation_id}"): _permission(Permission.MANAGE_OPERATIONS),
    ("POST", "/api/scan-stations"): _permission(Permission.MANAGE_SCAN_STATIONS),
    ("PATCH", "/api/scan-stations/{station_id}"): _permission(Permission.MANAGE_SCAN_STATIONS),
    ("POST", "/api/scan-station-devices/{device_id}/revocation"): _permission(
        Permission.MANAGE_SCAN_STATIONS
    ),
    ("PUT", "/api/barcode-configuration/machine-asset-tag-format"): _permission(
        Permission.MANAGE_BARCODE_CONFIGURATION
    ),
    ("POST", "/api/workers"): _permission(Permission.MANAGE_WORKERS),
    ("PATCH", "/api/workers/{worker_id}"): _permission(Permission.MANAGE_WORKERS),
    ("PUT", "/api/workers/{worker_id}/avatar"): _permission(Permission.MANAGE_WORKERS),
    ("DELETE", "/api/workers/{worker_id}/avatar"): _permission(Permission.MANAGE_WORKERS),
    ("PUT", "/api/policies/worker-sessions"): _permission(_MWSP),
    ("PUT", "/api/policies/correction-permissions"): _permission(_MCP),
    ("PUT", "/api/policies/due-soon"): _permission(_CSS),
    ("PUT", "/api/policies/data-retention"): _permission(_CSS),
    ("PUT", "/api/policies/sign-in"): _permission(_CSS),
    ("PUT", "/api/users/{user_id}/avatar"): _permission(_MUAR),
    ("DELETE", "/api/users/{user_id}/avatar"): _permission(_MUAR),
    # Management (slice 3)
    ("POST", "/api/machines"): _permission(_MM),
    ("PATCH", "/api/machines/{machine_id}"): _permission(_MM),
    ("POST", "/api/machines/{machine_id}/maintenance"): _permission(_MM),
    ("DELETE", "/api/machines/{machine_id}/maintenance"): _permission(_MM),
    ("POST", "/api/machines/{machine_id}/retire"): _permission(_MM),
    ("POST", "/api/machines/{machine_id}/reactivate"): _permission(_MM),
    ("POST", "/api/part-numbers"): _permission(_MPNM),
    ("PATCH", "/api/part-numbers"): _permission(_MPNM),
    ("DELETE", "/api/part-numbers"): _permission(_MPNM),
    ("PUT", "/api/part-numbers/image"): _permission(_MPNM),
    ("DELETE", "/api/part-numbers/image"): _permission(_MPNM),
    ("POST", "/api/route-templates"): _permission(_MRT),
    ("PUT", "/api/route-templates/{template_id}"): _permission(_MRT),
    ("POST", "/api/route-templates/{template_id}/archive"): _permission(_MRT),
    ("DELETE", "/api/route-templates/{template_id}"): _permission(_MRT),
    ("POST", "/api/work-orders"): _permission(_MWO),
    ("POST", "/api/work-orders/{work_order_id}/demands/{demand_id}/release"): _permission(_MWO),
    ("DELETE", "/api/work-orders/{work_order_id}/demands/{demand_id}"): _permission(_EWOD),
    ("POST", "/api/allocations/management"): _permission(_EWOA),
    ("POST", "/api/allocations/{allocation_id}/reversals"): _permission(_EWOA),
    # Slice 5: the beyond-demand correction and the context read only its
    # write workflow uses (OD-P7, OD-P10).
    ("POST", "/api/allocations/corrections"): _permission(_EWOA),
    ("GET", "/api/allocations/management/context"): _permission(_EWOA),
    # Slice 6: the AssignedRoute adjustment and the editor read only its
    # write workflow uses (OD-P11, OD-P10).
    ("POST", "/api/quantity-flows/{quantity_flow_id}/route-adjustments"): _permission(
        Permission.ASSIGN_ROUTES
    ),
    ("GET", "/api/tracking/assigned-routes"): _permission(Permission.ASSIGN_ROUTES),
    # --- PERMISSION (static + conditional) ----------------------------------
    ("POST", "/api/areas"): _permission(Permission.MANAGE_AREAS, conditional=_keys(_MWSP)),
    ("POST", "/api/roles"): _permission(_MUAR, conditional=_keys(_MCP)),
    ("POST", "/api/users"): _permission(_MUAR, conditional=_keys(_MCP)),
    ("PATCH", "/api/users/{user_id}"): _permission(_MUAR, conditional=_keys(_MCP)),
    ("PUT", "/api/users/{user_id}/password"): _permission(_MUAR, conditional=_keys(_MCP)),
    # Phase 14 slice 4 (OD-S4-9): while the role applied at Scan Stations
    # holds a protected key.
    ("POST", "/api/scan-stations/{station_id}/device-enrollments"): _permission(
        Permission.MANAGE_SCAN_STATIONS, conditional=_keys(_MCP)
    ),
    # --- PERMISSION (content only) ------------------------------------------
    ("PATCH", "/api/areas/{area_id}"): _permission(
        conditional=_keys(Permission.MANAGE_AREAS, _MWSP)
    ),
    ("PATCH", "/api/roles/{role_id}"): _permission(conditional=_keys(_MUAR, _MCP)),
    # Management (slice 3): a Work Order Save by its header and lines, a
    # Hot list change by whether it changes the list's members.
    ("PATCH", "/api/work-orders/{work_order_id}"): _permission(conditional=_keys(_MWO, _EWOD)),
    # Phase 15 slice 2: the Work Order file import — creating needs
    # Create and edit Work Orders, changing existing ones Edit Work Order
    # Demand; either key passes the gate before the body is read.
    ("POST", "/api/work-orders/import"): _permission(conditional=_keys(_MWO, _EWOD)),
    ("POST", "/api/hot-list/changes"): _permission(
        conditional=_keys(Permission.SET_DEMAND_PRIORITY, Permission.REORDER_HOT_ITEMS)
    ),
    # --- PERMISSION (any-of reads, slice 3) ---------------------------------
    ("GET", "/api/work-orders"): _any_of(_WO_VIEW),
    ("GET", "/api/work-orders/completed"): _any_of(_WO_VIEW),
    ("GET", "/api/work-orders/{work_order_id}"): _any_of(_WO_VIEW),
    ("GET", "/api/part-numbers"): _any_of(_WO_VIEW, _PN_VIEW),
    ("GET", "/api/part-numbers/page"): _any_of(_PN_VIEW),
    ("GET", "/api/hot-list"): _any_of(_PRIORITY_VIEW),
    ("GET", "/api/hot-list/candidates"): _any_of(_PRIORITY_VIEW),
    ("GET", "/api/tracking"): _any_of(_TRACKING_VIEW),
    ("GET", "/api/tracking/detail"): _any_of(_TRACKING_VIEW),
    ("GET", "/api/tracking/movements"): _any_of(_TRACKING_VIEW),
    ("GET", "/api/tracking/flows"): _any_of(_TRACKING_VIEW),
    ("GET", "/api/tracking/allocations"): _any_of(_TRACKING_VIEW),
    ("GET", "/api/tracking/audit-trail"): _any_of(_TRACKING_VIEW),
    ("GET", "/api/area-board"): _any_of(_AREA_BOARD_VIEW),
    ("GET", "/api/machines/{machine_id}"): _any_of(_MACHINES_VIEW),
    ("GET", "/api/machines/{machine_id}/lifecycle-events"): _any_of(_MACHINES_VIEW),
    ("GET", "/api/route-templates/management"): _any_of(_ROUTES_VIEW),
    ("GET", "/api/route-templates/{template_id}/usage"): _any_of(_ROUTES_VIEW),
    # No view reads it yet: View production data ∪ its write surface's key.
    ("GET", "/api/allocations"): _any_of(_keys(_VPD, _EWOA)),
    # Phase 15 slice 2: the import write workflow's own surfaces — the
    # dry run and the header-only templates — open for either write key
    # (OD-P10 precedent; not a Management view read, so no View
    # production data).
    ("POST", "/api/work-orders/import/preview"): _any_of(_keys(_MWO, _EWOD)),
    ("GET", "/api/work-orders/import/template.csv"): _any_of(_keys(_MWO, _EWOD)),
    ("GET", "/api/work-orders/import/template.xlsx"): _any_of(_keys(_MWO, _EWOD)),
}

FRAMEWORK_ROUTES: Final = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
