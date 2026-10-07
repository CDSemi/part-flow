"""Who may call each route (Phase 14 slice 2; owner decisions OD-P7, OD-P10, OD-P19).

One registry classifies every API route by ``(METHOD, path)``; a test
(``tests/test_route_access.py``) fails on any route that is missing,
stale, or whose declared dependencies disagree with its class. A new
route is added here in the same change.

- ``PUBLIC`` — no sign-in: the Production Board, the Station Selector
  lists, the active Planned Route list, the Due Soon policy the board and
  the Scan Station read, image reads, health, sign-in and first-run
  setup. May read the optional principal only.
- ``STATION`` — a Scan Station route: never resolves the User principal
  (the station authorization of a later Phase 14 slice is not built yet;
  until then station writes stay callable by any client on the network).
- ``SIGNED_IN`` — any signed-in User without a pending forced password
  change (``RequirePermission()``): the Administration reads.
- ``PERMISSION`` — ``RequirePermission(*requires)``; ``conditional``
  names the keys a content or data rule may add for a given request
  (``app.application.authorization``; the guard of
  ``app.application.users`` / ``roles`` / ``authentication``).

``PENDING_S3`` lists the Management, master-data and monitoring routes
whose class Phase 14 slice 3 decides; they stay open until then.
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


def _keys(*keys: Permission) -> frozenset[Permission]:
    return frozenset(keys)


_PUBLIC: Final = RouteAccess(Access.PUBLIC)
_STATION: Final = RouteAccess(Access.STATION)
_SIGNED_IN: Final = RouteAccess(Access.SIGNED_IN)


def _permission(
    *requires: Permission, conditional: frozenset[Permission] = frozenset()
) -> RouteAccess:
    return RouteAccess(Access.PERMISSION, _keys(*requires), conditional)


_MUAR: Final = Permission.MANAGE_USERS_AND_ROLES
_MCP: Final = Permission.MANAGE_CORRECTION_PERMISSIONS
_CSS: Final = Permission.CONFIGURE_SYSTEM_SETTINGS
_MWSP: Final = Permission.MANAGE_WORKER_SESSION_POLICIES

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
    # --- SIGNED_IN ----------------------------------------------------------
    ("GET", "/api/workers"): _SIGNED_IN,
    ("GET", "/api/users"): _SIGNED_IN,
    ("GET", "/api/roles"): _SIGNED_IN,
    ("GET", "/api/policies/worker-sessions"): _SIGNED_IN,
    ("GET", "/api/policies/correction-permissions"): _SIGNED_IN,
    ("GET", "/api/policies/data-retention"): _SIGNED_IN,
    ("GET", "/api/policies/sign-in"): _SIGNED_IN,
    ("PUT", "/api/session/password"): RouteAccess(Access.SIGNED_IN, password_change_allowed=True),
    # --- PERMISSION (static) ------------------------------------------------
    ("POST", "/api/departments"): _permission(Permission.MANAGE_DEPARTMENTS),
    ("PATCH", "/api/departments/{department_id}"): _permission(Permission.MANAGE_DEPARTMENTS),
    ("POST", "/api/operations"): _permission(Permission.MANAGE_OPERATIONS),
    ("PATCH", "/api/operations/{operation_id}"): _permission(Permission.MANAGE_OPERATIONS),
    ("POST", "/api/scan-stations"): _permission(Permission.MANAGE_SCAN_STATIONS),
    ("PATCH", "/api/scan-stations/{station_id}"): _permission(Permission.MANAGE_SCAN_STATIONS),
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
    # --- PERMISSION (static + conditional) ----------------------------------
    ("POST", "/api/areas"): _permission(Permission.MANAGE_AREAS, conditional=_keys(_MWSP)),
    ("POST", "/api/roles"): _permission(_MUAR, conditional=_keys(_MCP)),
    ("POST", "/api/users"): _permission(_MUAR, conditional=_keys(_MCP)),
    ("PATCH", "/api/users/{user_id}"): _permission(_MUAR, conditional=_keys(_MCP)),
    ("PUT", "/api/users/{user_id}/password"): _permission(_MUAR, conditional=_keys(_MCP)),
    # --- PERMISSION (content only) ------------------------------------------
    ("PATCH", "/api/areas/{area_id}"): _permission(
        conditional=_keys(Permission.MANAGE_AREAS, _MWSP)
    ),
    ("PATCH", "/api/roles/{role_id}"): _permission(conditional=_keys(_MUAR, _MCP)),
}

PENDING_S3: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # Machines
        ("GET", "/api/machines/{machine_id}"),
        ("POST", "/api/machines"),
        ("PATCH", "/api/machines/{machine_id}"),
        ("POST", "/api/machines/{machine_id}/maintenance"),
        ("DELETE", "/api/machines/{machine_id}/maintenance"),
        ("POST", "/api/machines/{machine_id}/retire"),
        ("POST", "/api/machines/{machine_id}/reactivate"),
        ("GET", "/api/machines/{machine_id}/lifecycle-events"),
        # Hosted by Management → Machines too (OD-S2-2).
        ("GET", "/api/barcode-configuration/machine-asset-tag-format"),
        # Part Numbers
        ("GET", "/api/part-numbers"),
        ("GET", "/api/part-numbers/page"),
        ("POST", "/api/part-numbers"),
        ("PATCH", "/api/part-numbers"),
        ("DELETE", "/api/part-numbers"),
        ("PUT", "/api/part-numbers/image"),
        ("DELETE", "/api/part-numbers/image"),
        # Planned Routes
        ("GET", "/api/route-templates/management"),
        ("POST", "/api/route-templates"),
        ("PUT", "/api/route-templates/{template_id}"),
        ("POST", "/api/route-templates/{template_id}/archive"),
        ("DELETE", "/api/route-templates/{template_id}"),
        ("GET", "/api/route-templates/{template_id}/usage"),
        # Work Orders and release
        ("GET", "/api/work-orders"),
        ("GET", "/api/work-orders/completed"),
        ("GET", "/api/work-orders/{work_order_id}"),
        ("POST", "/api/work-orders"),
        ("PATCH", "/api/work-orders/{work_order_id}"),
        ("DELETE", "/api/work-orders/{work_order_id}/demands/{demand_id}"),
        ("POST", "/api/work-orders/{work_order_id}/demands/{demand_id}/release"),
        # Hot list
        ("GET", "/api/hot-list"),
        ("GET", "/api/hot-list/candidates"),
        ("POST", "/api/hot-list/changes"),
        # Allocations
        ("POST", "/api/allocations"),
        ("POST", "/api/allocations/{allocation_id}/reversals"),
        ("GET", "/api/allocations"),
        # Monitoring
        ("GET", "/api/area-board"),
        ("GET", "/api/tracking"),
        ("GET", "/api/tracking/detail"),
        ("GET", "/api/tracking/movements"),
        ("GET", "/api/tracking/flows"),
        ("GET", "/api/tracking/allocations"),
    }
)

FRAMEWORK_ROUTES: Final = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
