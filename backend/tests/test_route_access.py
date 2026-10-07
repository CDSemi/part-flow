"""Static tests of the route registry (Phase 14 slices 2–3; owner decision OD-P7).

``app.api.route_access`` classifies every API route; these cases fail
when a route is added without a class, a registry entry goes stale, or a
route's declared dependencies disagree with its class — so no route can
silently stay open or lose its check. No database: the app is built and
its routes inspected only.
"""

import inspect
from collections.abc import Iterator
from typing import Any

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from app.api import route_access
from app.api.authorization import (
    RequireAnyPermission,
    RequirePermission,
    RequireStationDevice,
    current_user,
    optional_principal,
)
from app.api.route_access import (
    FRAMEWORK_ROUTES,
    ROUTE_ACCESS,
    Access,
)
from app.domain.enums import Permission
from app.domain.permissions import (
    CORRECTION_PERMISSIONS,
    INERT_PERMISSIONS,
    PERMISSION_MANAGEMENT,
    PROTECTED_PERMISSIONS,
)
from app.main import create_app


def _walk(routes: list[BaseRoute]) -> Iterator[BaseRoute]:
    """Every route, descending into included routers (FastAPI includes
    them lazily; ``original_router`` holds their own routes)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        else:
            yield route


def _api_routes() -> dict[tuple[str, str], APIRoute]:
    app = create_app()
    found: dict[tuple[str, str], APIRoute] = {}
    for route in _walk(app.router.routes):
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                found[(method, route.path)] = route
    return found


def _other_paths() -> set[str]:
    app = create_app()
    return {
        str(getattr(route, "path", ""))
        for route in _walk(app.router.routes)
        if not isinstance(route, APIRoute)
    }


def _calls(route: APIRoute) -> list[Any]:
    calls: list[Any] = []
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        calls.append(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def _permission_dependencies(route: APIRoute) -> list[RequirePermission]:
    return [call for call in _calls(route) if isinstance(call, RequirePermission)]


def _any_permission_dependencies(route: APIRoute) -> list[RequireAnyPermission]:
    return [call for call in _calls(route) if isinstance(call, RequireAnyPermission)]


def test_every_route_is_classified_exactly_once() -> None:
    """RA-1: no pending list remains (Phase 14 slice 3 classified it)."""
    assert not hasattr(route_access, "PENDING_S3")
    routes = set(_api_routes())
    assert routes - set(ROUTE_ACCESS) == set()
    assert _other_paths() == FRAMEWORK_ROUTES


def test_no_registry_entry_is_stale() -> None:
    """RA-2: every registry key exists in the app."""
    routes = set(_api_routes())
    assert set(ROUTE_ACCESS) - routes == set()


def _station_device_dependencies(route: APIRoute) -> list[RequireStationDevice]:
    return [call for call in _calls(route) if isinstance(call, RequireStationDevice)]


def test_dependencies_match_the_class() -> None:
    """RA-4 (+ SD-27: exactly the STATION routes require an enrolled
    station device, declared first, bound to the path's station iff the
    path names one)."""
    principal_calls = (optional_principal, current_user)
    for key, route in _api_routes().items():
        calls = _calls(route)
        permissions = _permission_dependencies(route)
        any_permissions = _any_permission_dependencies(route)
        devices = _station_device_dependencies(route)
        access = ROUTE_ACCESS[key]
        if access.access is Access.STATION:
            assert not permissions and not any_permissions, key
            assert not any(call in principal_calls for call in calls), key
            assert len(devices) == 1, key
            device = devices[0]
            assert device.bind_path_station == ("{station_id}" in route.path), key
            assert route.dependant.dependencies[0].call is device, key
            continue
        assert not devices, key
        if access.access is Access.PUBLIC:
            assert not permissions and not any_permissions and current_user not in calls, key
        elif access.access is Access.SIGNED_IN:
            assert not any_permissions, key
            if access.password_change_allowed:
                assert not permissions and current_user in calls, key
            else:
                assert len(permissions) == 1 and permissions[0].keys == (), key
        elif access.any_of:
            assert not access.requires and not access.conditional, key
            assert not permissions and len(any_permissions) == 1, key
            assert frozenset(any_permissions[0].keys) == access.any_of, key
        else:
            assert not any_permissions, key
            assert len(permissions) == 1, key
            assert frozenset(permissions[0].keys) == access.requires, key


def test_only_the_content_and_guard_routes_have_conditional_keys() -> None:
    """RA-5."""
    conditional = {key for key, access in ROUTE_ACCESS.items() if access.conditional}
    assert conditional == {
        ("POST", "/api/areas"),
        ("PATCH", "/api/areas/{area_id}"),
        ("POST", "/api/roles"),
        ("PATCH", "/api/roles/{role_id}"),
        ("POST", "/api/users"),
        ("PATCH", "/api/users/{user_id}"),
        ("PUT", "/api/users/{user_id}/password"),
        ("PATCH", "/api/work-orders/{work_order_id}"),
        ("POST", "/api/hot-list/changes"),
        ("POST", "/api/scan-stations/{station_id}/device-enrollments"),
    }
    assert all(
        access.access is Access.PERMISSION
        for key, access in ROUTE_ACCESS.items()
        if key in conditional
    )
    only_password_change = {
        key for key, access in ROUTE_ACCESS.items() if access.password_change_allowed
    }
    assert only_password_change == {("PUT", "/api/session/password")}


def test_no_route_requires_an_inert_permission() -> None:
    """RA-6."""
    for key, access in ROUTE_ACCESS.items():
        assert not (access.requires | access.conditional | access.any_of) & INERT_PERMISSIONS, key


def test_permission_sets_equal_the_spec_literals() -> None:
    """RA-7: the frontend's permissions test holds the same literals."""
    assert CORRECTION_PERMISSIONS == (
        Permission.UNDO_RECENT_SCANS,
        Permission.PERFORM_QUANTITY_CORRECTIONS,
        Permission.EDIT_WORK_ORDER_ALLOCATION,
        Permission.PERFORM_HISTORICAL_CORRECTIONS,
    )
    assert PERMISSION_MANAGEMENT == (
        Permission.MANAGE_USERS_AND_ROLES,
        Permission.MANAGE_CORRECTION_PERMISSIONS,
    )
    assert {
        Permission.UNDO_RECENT_SCANS,
        Permission.PERFORM_QUANTITY_CORRECTIONS,
        Permission.EDIT_WORK_ORDER_ALLOCATION,
        Permission.PERFORM_HISTORICAL_CORRECTIONS,
        Permission.MANAGE_CORRECTION_PERMISSIONS,
    } == PROTECTED_PERMISSIONS
    assert {
        Permission.MANAGE_SCAN_BEHAVIOR,
        Permission.RESOLVE_EXCEPTIONAL_SITUATIONS,
        Permission.EXPORT_REPORTS,
        Permission.PERFORM_QUANTITY_CORRECTIONS,
        Permission.PERFORM_HISTORICAL_CORRECTIONS,
    } == INERT_PERMISSIONS


_VPD = Permission.VIEW_PRODUCTION_DATA
_WO_VIEW = {
    _VPD,
    Permission.MANAGE_WORK_ORDERS,
    Permission.EDIT_WORK_ORDER_DEMAND,
    Permission.EDIT_WORK_ORDER_ALLOCATION,
}
_PRIORITY_VIEW = {_VPD, Permission.SET_DEMAND_PRIORITY, Permission.REORDER_HOT_ITEMS}
_TRACKING_VIEW = {_VPD, Permission.EDIT_WORK_ORDER_ALLOCATION, Permission.ASSIGN_ROUTES}
_MACHINES_VIEW = {_VPD, Permission.MANAGE_MACHINES}
_ROUTES_VIEW = {_VPD, Permission.MANAGE_ROUTE_TEMPLATES}
_PN_VIEW = {_VPD, Permission.MANAGE_PART_NUMBER_MASTER}


def test_management_read_sets_equal_the_spec_literals() -> None:
    """RA-8: every any-of read opens with View production data, and each
    read set is the union of the sets of the views reading the route (the
    frontend's management-access test holds the same view literals)."""
    expected = {
        ("GET", "/api/work-orders"): _WO_VIEW,
        ("GET", "/api/work-orders/completed"): _WO_VIEW,
        ("GET", "/api/work-orders/{work_order_id}"): _WO_VIEW,
        ("GET", "/api/part-numbers"): _WO_VIEW | _PN_VIEW,
        ("GET", "/api/part-numbers/page"): _PN_VIEW,
        ("GET", "/api/hot-list"): _PRIORITY_VIEW,
        ("GET", "/api/hot-list/candidates"): _PRIORITY_VIEW,
        ("GET", "/api/tracking"): _TRACKING_VIEW,
        ("GET", "/api/tracking/detail"): _TRACKING_VIEW,
        ("GET", "/api/tracking/movements"): _TRACKING_VIEW,
        ("GET", "/api/tracking/flows"): _TRACKING_VIEW,
        ("GET", "/api/tracking/allocations"): _TRACKING_VIEW,
        ("GET", "/api/tracking/audit-trail"): _TRACKING_VIEW,
        ("GET", "/api/area-board"): {_VPD},
        ("GET", "/api/machines/{machine_id}"): _MACHINES_VIEW,
        ("GET", "/api/machines/{machine_id}/lifecycle-events"): _MACHINES_VIEW,
        ("GET", "/api/route-templates/management"): _ROUTES_VIEW,
        ("GET", "/api/route-templates/{template_id}/usage"): _ROUTES_VIEW,
        ("GET", "/api/allocations"): {_VPD, Permission.EDIT_WORK_ORDER_ALLOCATION},
    }
    actual = {key: set(access.any_of) for key, access in ROUTE_ACCESS.items() if access.any_of}
    assert actual == expected
    assert all(_VPD in keys for keys in actual.values())


def test_station_device_routes_and_bindings() -> None:
    """SD-27: the four device routes are classified as the slice 4 spec
    states, and the two station routes without a path station check their
    binding in the route body."""
    mss = Permission.MANAGE_SCAN_STATIONS
    assert ROUTE_ACCESS[("GET", "/api/scan-station-devices")].access is Access.SIGNED_IN
    issue = ROUTE_ACCESS[("POST", "/api/scan-stations/{station_id}/device-enrollments")]
    assert issue.access is Access.PERMISSION
    assert issue.requires == {mss}
    assert issue.conditional == {Permission.MANAGE_CORRECTION_PERMISSIONS}
    revoke = ROUTE_ACCESS[("POST", "/api/scan-station-devices/{device_id}/revocation")]
    assert revoke.access is Access.PERMISSION
    assert revoke.requires == {mss} and not revoke.conditional
    activation = ROUTE_ACCESS[("POST", "/api/scan-stations/{station_id}/device-activations")]
    assert activation.access is Access.PUBLIC
    routes = _api_routes()
    allocation = inspect.getsource(routes[("POST", "/api/allocations")].endpoint)
    assert "station_devices.require_station_binding(device, body.station_id)" in allocation
    inventory = inspect.getsource(routes[("GET", "/api/areas/{area_id}/inventory")].endpoint)
    assert "station_devices.require_area_binding(session, device, area_id)" in inventory
