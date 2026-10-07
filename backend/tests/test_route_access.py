"""Static tests of the route registry (Phase 14 slice 2; owner decision OD-P7).

``app.api.route_access`` classifies every API route; these cases fail
when a route is added without a class, a registry entry goes stale, or a
route's declared dependencies disagree with its class — so no route can
silently stay open or lose its check. No database: the app is built and
its routes inspected only.
"""

from collections.abc import Iterator
from typing import Any

from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from app.api.authorization import RequirePermission, current_user, optional_principal
from app.api.route_access import (
    FRAMEWORK_ROUTES,
    PENDING_S3,
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


def test_every_route_is_classified_exactly_once() -> None:
    """RA-1."""
    routes = set(_api_routes())
    assert routes - set(ROUTE_ACCESS) - PENDING_S3 == set()
    assert not set(ROUTE_ACCESS) & PENDING_S3
    assert _other_paths() == FRAMEWORK_ROUTES


def test_no_registry_entry_is_stale() -> None:
    """RA-2."""
    routes = set(_api_routes())
    assert set(ROUTE_ACCESS) - routes == set()
    assert PENDING_S3 - routes == set()
    assert not set(ROUTE_ACCESS) & PENDING_S3


def test_pending_routes_are_management_and_monitoring_only() -> None:
    """RA-3: only the Management, master-data and monitoring surfaces
    wait for slice 3."""
    for method, path in PENDING_S3:
        if path == "/api/barcode-configuration/machine-asset-tag-format":
            assert method == "GET"
        elif path == "/api/machines":
            assert method != "GET"
        elif path.startswith("/api/part-numbers"):
            assert (method, path) != ("GET", "/api/part-numbers/image")
        elif path.startswith("/api/route-templates"):
            assert (method, path) != ("GET", "/api/route-templates")
        elif path.startswith("/api/allocations"):
            assert path != "/api/allocations/suggestion"
        else:
            assert path.startswith(
                (
                    "/api/machines/",
                    "/api/work-orders",
                    "/api/hot-list",
                    "/api/area-board",
                    "/api/tracking",
                )
            ), (method, path)


def test_dependencies_match_the_class() -> None:
    """RA-4."""
    principal_calls = (optional_principal, current_user)
    for key, route in _api_routes().items():
        calls = _calls(route)
        permissions = _permission_dependencies(route)
        if key in PENDING_S3:
            assert not permissions and not any(call in principal_calls for call in calls), key
            continue
        access = ROUTE_ACCESS[key]
        if access.access is Access.STATION:
            assert not permissions and not any(call in principal_calls for call in calls), key
        elif access.access is Access.PUBLIC:
            assert not permissions and current_user not in calls, key
        elif access.access is Access.SIGNED_IN:
            if access.password_change_allowed:
                assert not permissions and current_user in calls, key
            else:
                assert len(permissions) == 1 and permissions[0].keys == (), key
        else:
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
        assert not (access.requires | access.conditional) & INERT_PERMISSIONS, key


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
