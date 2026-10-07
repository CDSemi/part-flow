"""Signed-in test identities (Phase 14 slice 2; PLAN CD6).

Since slice 2 every Administration read needs a signed-in User and every
Administration write its permission, since slice 3 every Management read
and write too. Tests reach those routes through an identity created
here; public routes stay on the module's anonymous client, and Scan
Station routes (slice 4) on its ``station_device_client`` wrapper.

- ``create_identity(client, *permissions)`` — one transaction on the
  app's engine: a role ``test-role-<hex>`` holding exactly
  ``permissions``, an active User ``test-<hex>`` in it with a password
  and an open session. Direct SQL setup (no audit row), like the slice 1
  tests' helpers; the session token is issued with the application's own
  ``new_session_token``.
- ``client_as(client, *permissions)`` — an :class:`IdentityClient` for a
  new identity: a ``TestClient`` on the same, already started app that
  sends ``Cookie: partflow_session=<token>`` and the CSRF header with
  every request (an explicit Cookie header wins over its own jar).
- ``admin_of(client)`` — the app's administrator identity holding every
  permission, created on first use and reused for the app's lifetime
  (creating it closes first-run setup).
- ``anonymous_client(client)`` — a ``TestClient`` on the same app that
  sends neither;
- ``another_session(client, identity_client)`` — the same User signed in
  a second time (a new session row, as from another browser; Phase 14
  slice 3);
- ``station_device_client(client)`` — since slice 4 every Scan Station
  route needs an enrolled station device: a client on the same app that
  adds the ``X-PartFlow-Station-Device`` header of a device of the
  addressed station (enrolled directly, once per station) to every
  ``STATION`` route request, and leaves every other request untouched;
  ``enroll_station_device`` / ``station_device_headers`` for explicit
  devices.

Harness rows are recognizable by name: role names start with
``test-role-`` and login names with ``test-``; list and count
assertions over roles and users filter them out.
"""

import functools
import hashlib
import secrets
import uuid
import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final, cast
from urllib.parse import unquote, urlsplit

import httpx
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from starlette.routing import BaseRoute, Match

from app.api.route_access import ROUTE_ACCESS, Access
from app.application import authentication, password_hashing
from app.domain.enums import Permission

TEST_PASSWORD: Final = "partflow-test-password"
CSRF_HEADERS: Final = {"X-PartFlow-CSRF": "1"}
ALL_PERMISSIONS: Final = tuple(Permission)
HARNESS_ROLE_PREFIX: Final = "test-role-"
HARNESS_LOGIN_PREFIX: Final = "test-"

_ADMINS: "weakref.WeakKeyDictionary[FastAPI, IdentityClient]" = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class TestIdentity:
    __test__ = False  # not a pytest test class

    user_id: int
    role_id: int
    login_name: str
    token: str
    permissions: frozenset[Permission]


@functools.cache
def _password_hash() -> str:
    """The test password's scrypt hash, computed once per process."""
    return password_hashing.hash_password(TEST_PASSWORD)


def app_of(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def engine_of(client: TestClient) -> Engine:
    return cast(Engine, app_of(client).state.engine)


def create_identity(
    client: TestClient,
    *permissions: Permission,
    active: bool = True,
    temporary_password: bool = False,
) -> TestIdentity:
    suffix = uuid.uuid4().hex[:8]
    token, digest = authentication.new_session_token()
    with engine_of(client).begin() as connection:
        role_id = connection.execute(
            sa.text("INSERT INTO roles (name) VALUES (:name) RETURNING id"),
            {"name": f"{HARNESS_ROLE_PREFIX}{suffix}"},
        ).scalar_one()
        for permission in set(permissions):
            connection.execute(
                sa.text("INSERT INTO role_permissions (role_id, permission) VALUES (:r, :p)"),
                {"r": role_id, "p": permission.value},
            )
        login = f"{HARNESS_LOGIN_PREFIX}{suffix}"
        user_id = connection.execute(
            sa.text(
                "INSERT INTO users (login_name, display_name, role_id, is_active)"
                " VALUES (:login, :name, :role, :active) RETURNING id"
            ),
            {"login": login, "name": f"Test {suffix}", "role": role_id, "active": active},
        ).scalar_one()
        connection.execute(
            sa.text(
                "INSERT INTO user_credentials (user_id, password_hash, password_is_temporary,"
                " password_changed_at) VALUES (:user_id, :hash, :temporary, now())"
            ),
            {"user_id": user_id, "hash": _password_hash(), "temporary": temporary_password},
        )
        connection.execute(
            sa.text("INSERT INTO user_sessions (user_id, token_digest) VALUES (:user_id, :digest)"),
            {"user_id": user_id, "digest": digest},
        )
    return TestIdentity(
        user_id=int(user_id),
        role_id=int(role_id),
        login_name=login,
        token=token,
        permissions=frozenset(permissions),
    )


class IdentityClient(TestClient):
    """A client on an already started app that acts as ``identity``
    (``None`` = anonymous). Its lifespan is the module client's."""

    __test__ = False  # not a pytest test class

    def __init__(self, client: TestClient, identity: TestIdentity | None) -> None:
        headers = (
            {}
            if identity is None
            else {"Cookie": f"{authentication.SESSION_COOKIE}={identity.token}", **CSRF_HEADERS}
        )
        super().__init__(client.app, headers=headers)
        self.identity = identity

    @property
    def user_id(self) -> int:
        assert self.identity is not None
        return self.identity.user_id


def client_as(client: TestClient, *permissions: Permission, **kwargs: bool) -> IdentityClient:
    return IdentityClient(client, create_identity(client, *permissions, **kwargs))


def anonymous_client(client: TestClient) -> IdentityClient:
    return IdentityClient(client, None)


def admin_of(client: TestClient) -> IdentityClient:
    """The app's all-permission identity, created on first use."""
    app = app_of(client)
    admin = _ADMINS.get(app)
    if admin is None:
        admin = client_as(client, *ALL_PERMISSIONS)
        _ADMINS[app] = admin
    return admin


# ---------------------------------------------------------------------------
# Enrolled Scan Station devices (Phase 14 slice 4)
# ---------------------------------------------------------------------------

STATION_DEVICE_HEADER: Final = "X-PartFlow-Station-Device"

# The wrapper's lazily enrolled device token per app and station.
_STATION_TOKENS: "weakref.WeakKeyDictionary[FastAPI, dict[str, str]]" = weakref.WeakKeyDictionary()


def enroll_station_device(engine: Engine, station_id: str, *, label: str = "test device") -> str:
    """An activated device of ``station_id``, inserted directly (no audit
    row, no enrollment code); returns its fresh token."""
    token = secrets.token_urlsafe(32)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO scan_station_devices"
                " (station_id, label, enrollment_expires_at, token_digest, activated_at)"
                " VALUES (:station_id, :label, now(), :digest, now())"
            ),
            {
                "station_id": station_id,
                "label": label,
                "digest": hashlib.sha256(token.encode("ascii")).digest(),
            },
        )
    return token


def station_device_headers(token: str) -> dict[str, str]:
    return {STATION_DEVICE_HEADER: token}


def _walk(routes: list[BaseRoute]) -> Iterator[BaseRoute]:
    """Every route, descending into included routers (FastAPI includes
    them lazily; ``original_router`` holds their own routes)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        else:
            yield route


def _station_route(app: FastAPI, method: str, path: str) -> tuple[str, dict[str, Any]] | None:
    """The registry path and path parameters of the STATION route a request
    reaches, or ``None`` for every other route."""
    scope = {"type": "http", "path": path, "root_path": "", "method": method}
    for route in _walk(app.router.routes):
        if not isinstance(route, APIRoute):
            continue
        match, child = route.matches(scope)
        if match is Match.FULL:
            access = ROUTE_ACCESS.get((method, route.path))
            if access is None or access.access is not Access.STATION:
                return None
            return route.path, dict(child.get("path_params", {}))
    return None


class StationDeviceClient(TestClient):
    """A client on an already started app that adds an enrolled device of
    the addressed Scan Station to every ``STATION`` route request.

    The station is the path's ``station_id``, the body's ``station_id`` of
    ``POST /api/allocations``, the lowest Station ID bound to the Area of
    an inventory read (none → the test fails loudly) and the lowest
    Station ID at all for the allocation suggestion. An unknown station
    gets no header (the request answers 401). A request that already
    names the header, and every non-station route, passes untouched. The
    base client's headers (an identity's cookie and CSRF header) are
    kept."""

    __test__ = False  # not a pytest test class

    def __init__(self, client: TestClient) -> None:
        super().__init__(client.app, headers=dict(client.headers))
        self.base = client

    def request(self, method: str, url: httpx._types.URLTypes, **kwargs: Any) -> httpx.Response:
        headers = httpx.Headers(kwargs.pop("headers", None))
        if STATION_DEVICE_HEADER not in headers:
            token = self._token_for(method.upper(), str(url), kwargs.get("json"))
            if token is not None:
                headers[STATION_DEVICE_HEADER] = token
        response: httpx.Response = super().request(method, url, headers=headers, **kwargs)
        return response

    def _token_for(self, method: str, url: str, body: object) -> str | None:
        app = app_of(self)
        found = _station_route(app, method, unquote(urlsplit(url).path))
        if found is None:
            return None
        route_path, params = found
        engine = engine_of(self)
        station_id = self._station_of(engine, route_path, params, body)
        if station_id is None:
            return None
        with engine.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM scan_stations WHERE station_id = :s"), {"s": station_id}
            ).first()
        if exists is None:
            return None
        tokens = _STATION_TOKENS.setdefault(app, {})
        if station_id not in tokens:
            tokens[station_id] = enroll_station_device(engine, station_id)
        return tokens[station_id]

    @staticmethod
    def _station_of(
        engine: Engine, route_path: str, params: dict[str, Any], body: object
    ) -> str | None:
        if "station_id" in params:
            return str(params["station_id"])
        if route_path == "/api/allocations":
            station = body.get("station_id") if isinstance(body, dict) else None
            return station if isinstance(station, str) else None
        with engine.connect() as connection:
            if route_path == "/api/areas/{area_id}/inventory":
                station = connection.execute(
                    sa.text(
                        "SELECT station_id FROM scan_stations WHERE area_id = :a"
                        " ORDER BY station_id LIMIT 1"
                    ),
                    {"a": params["area_id"]},
                ).scalar()
                assert station is not None, (
                    f"No Scan Station is bound to Area {params['area_id']}: add one in the"
                    " test setup to read its inventory."
                )
                return str(station)
            station = connection.execute(
                sa.text("SELECT station_id FROM scan_stations ORDER BY station_id LIMIT 1")
            ).scalar()
            assert station is not None, "No Scan Station exists to read the suggestion."
            return str(station)


def station_device_client(client: TestClient) -> StationDeviceClient:
    return StationDeviceClient(client)


def another_session(client: TestClient, identity_client: IdentityClient) -> IdentityClient:
    """The same User in a second, independent session (another browser)."""
    identity = identity_client.identity
    assert identity is not None
    token, digest = authentication.new_session_token()
    with engine_of(client).begin() as connection:
        connection.execute(
            sa.text("INSERT INTO user_sessions (user_id, token_digest) VALUES (:user_id, :digest)"),
            {"user_id": identity.user_id, "digest": digest},
        )
    return IdentityClient(
        client,
        TestIdentity(
            user_id=identity.user_id,
            role_id=identity.role_id,
            login_name=identity.login_name,
            token=token,
            permissions=identity.permissions,
        ),
    )
