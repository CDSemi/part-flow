"""Who is signed in, and what they may do (Phase 14 slices 1–4).

FastAPI dependencies over ``app.application.authentication``:

- ``OptionalPrincipalDep`` — the signed-in User of the request's
  ``partflow_session`` cookie, or ``None`` (absent, unknown, ended,
  expired or inactive);
- ``CurrentUserDep`` — the same, refusing ``None`` with 401
  (``authentication_required``; the handler also clears the cookie).
  Allowed while a password change is required;
- ``RequirePermission(*keys)`` — a signed-in User who need not replace
  an administrator-set password first (else 403
  ``password_change_required``) and whose role holds every key (else
  403 ``permission_denied`` with ``required_permissions``);
  ``SignedInDep`` is the key-less form;
- ``RequireAnyPermission(*keys)`` — the same, but any ONE key opens it
  (Management reads, slice 3; else 403 ``permission_denied`` with
  ``any_permission``);
- ``RequireStationDevice`` (slice 4) — an enrolled Scan Station device
  from the ``X-PartFlow-Station-Device`` header, never the User
  principal (``StationDeviceForPathDep`` also binds it to the path's
  station; ``StationDeviceDep`` does not). What the station may do is
  checked per command by the Application (``station_access``).

Checks run before the route body, so before any lock or write. Since
slice 2 every Administration read and write declares one, since slice 3
every Management read and write (the class of every route is listed in
``app.api.route_access``); a route whose keys
depend on its request also checks ``authorization.require`` on
``actor_of(principal)`` first thing in its body. The principal is always
derived on the server from the session — never from a request body.

Also the cookie helpers: the raw token goes only into ``Set-Cookie``
(``HttpOnly``, ``SameSite=Strict``, ``Path=/api``, ``Secure`` when
``SESSION_COOKIE_SECURE`` is on, ``Max-Age`` the session's remaining
lifetime).
"""

from typing import Annotated, Final

from fastapi import Depends, Header, Request, Response

from app.api.dependencies import SessionDep
from app.application import authentication, station_devices
from app.application.authentication import SESSION_COOKIE, Principal
from app.application.authorization import Actor
from app.application.errors import (
    AUTHENTICATION_REQUIRED_MESSAGE,
    PASSWORD_CHANGE_REQUIRED_MESSAGE,
    PERMISSION_DENIED_MESSAGE,
    STATION_DEVICE_REQUIRED_MESSAGE,
    VIEW_PERMISSION_DENIED_MESSAGE,
    AuthenticationRequiredError,
    PasswordChangeRequiredError,
    PermissionDeniedError,
    StationDeviceRequiredError,
)
from app.application.station_devices import StationDevice
from app.core import log_context
from app.core.config import get_settings
from app.domain.enums import Permission

_COOKIE_PATH: Final = "/api"


def optional_principal(request: Request, session: SessionDep) -> Principal | None:
    principal = authentication.resolve_principal(session, request.cookies.get(SESSION_COOKIE))
    if principal is not None:
        # The access record names the User by id only (Phase 16 slice 6).
        log_context.bind(user_id=principal.user_id)
    return principal


OptionalPrincipalDep = Annotated[Principal | None, Depends(optional_principal)]


def current_user(principal: OptionalPrincipalDep) -> Principal:
    if principal is None:
        raise AuthenticationRequiredError(AUTHENTICATION_REQUIRED_MESSAGE)
    return principal


CurrentUserDep = Annotated[Principal, Depends(current_user)]


class RequirePermission:
    """Dependency factory: a signed-in User holding every given key."""

    def __init__(self, *keys: Permission) -> None:
        self.keys = keys

    def __call__(self, principal: CurrentUserDep) -> Principal:
        if principal.must_change_password:
            raise PasswordChangeRequiredError(PASSWORD_CHANGE_REQUIRED_MESSAGE)
        if any(key not in principal.permissions for key in self.keys):
            raise PermissionDeniedError(
                PERMISSION_DENIED_MESSAGE,
                required=tuple(sorted(key.value for key in self.keys)),
            )
        return principal


SignedInDep = Annotated[Principal, Depends(RequirePermission())]

# The Management views' read sets (owner decision OD-P7): View production
# data, or a key whose action the view hosts. A route's read set is the
# union of the sets of the views that read it (``app.api.route_access``).
_VPD: Final = Permission.VIEW_PRODUCTION_DATA
WORK_ORDERS_READ: Final = (
    _VPD,
    Permission.MANAGE_WORK_ORDERS,
    Permission.EDIT_WORK_ORDER_DEMAND,
    Permission.EDIT_WORK_ORDER_ALLOCATION,
)
PRIORITY_READ: Final = (
    _VPD,
    Permission.SET_DEMAND_PRIORITY,
    Permission.REORDER_HOT_ITEMS,
)
TRACKING_READ: Final = (
    _VPD,
    Permission.EDIT_WORK_ORDER_ALLOCATION,
    Permission.ASSIGN_ROUTES,
)
MACHINES_READ: Final = (_VPD, Permission.MANAGE_MACHINES)
PLANNED_ROUTES_READ: Final = (_VPD, Permission.MANAGE_ROUTE_TEMPLATES)
PART_NUMBERS_READ: Final = (_VPD, Permission.MANAGE_PART_NUMBER_MASTER)
AREA_BOARD_READ: Final = (_VPD,)


class RequireAnyPermission:
    """Dependency factory: a signed-in User holding at least one given key.

    A separate class (never a ``RequirePermission`` subclass), so the
    route registry test tells all-of and any-of routes apart.
    """

    def __init__(self, *keys: Permission) -> None:
        assert keys, "RequireAnyPermission needs at least one key"
        self.keys = keys

    def __call__(self, principal: CurrentUserDep) -> Principal:
        if principal.must_change_password:
            raise PasswordChangeRequiredError(PASSWORD_CHANGE_REQUIRED_MESSAGE)
        if not any(key in principal.permissions for key in self.keys):
            raise PermissionDeniedError(
                VIEW_PERMISSION_DENIED_MESSAGE,
                required=tuple(sorted(key.value for key in self.keys)),
                any_of=True,
            )
        return principal


STATION_DEVICE_HEADER: Final = "X-PartFlow-Station-Device"


class RequireStationDevice:
    """Dependency factory: an enrolled, unrevoked Scan Station device
    (Phase 14 slice 4; owner decision OD-P6).

    Reads the ``X-PartFlow-Station-Device`` header and resolves it on its
    own short transaction (``station_devices.resolve_station_device``),
    never on the request session and never on the User principal: no
    token, a malformed, unknown or revoked one → 401
    ``station_device_required``; with ``bind_path_station`` a device of
    another station than the path's ``station_id`` → 403
    ``station_device_mismatch``. A header, not a cookie: nothing ambient,
    so it needs no CSRF defence.
    """

    def __init__(self, *, bind_path_station: bool) -> None:
        self.bind_path_station = bind_path_station

    def __call__(
        self,
        request: Request,
        header: Annotated[str | None, Header(alias=STATION_DEVICE_HEADER)] = None,
    ) -> StationDevice:
        device = station_devices.resolve_station_device(request.app.state.engine, header)
        if device is None:
            raise StationDeviceRequiredError(STATION_DEVICE_REQUIRED_MESSAGE)
        # Before the binding check, so its refusal names the device's station too.
        log_context.bind(station_device_id=device.device_id, station_id=device.station_id)
        if self.bind_path_station:
            station_devices.require_station_binding(device, request.path_params["station_id"])
        return device


StationDeviceForPathDep = Annotated[
    StationDevice, Depends(RequireStationDevice(bind_path_station=True))
]
StationDeviceDep = Annotated[StationDevice, Depends(RequireStationDevice(bind_path_station=False))]


def holds(principal: Principal | None, key: Permission) -> bool:
    """Whether a usable principal (no pending forced change) holds ``key``."""
    return (
        principal is not None
        and not principal.must_change_password
        and key in principal.permissions
    )


def actor_of(principal: Principal) -> Actor:
    """The request's principal as an ``authorization.Actor`` (id and keys)."""
    return Actor(principal.user_id, principal.permissions)


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        path=_COOKIE_PATH,
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        "",
        max_age=0,
        path=_COOKIE_PATH,
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
