"""Signed-in test identities (Phase 14 slice 2; PLAN CD6).

Since slice 2 every Administration read needs a signed-in User and every
Administration write its permission, since slice 3 every Management read
and write too. Tests reach those routes through an identity created
here; Scan Station and public routes stay on the module's anonymous
client.

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
  slice 3).

Harness rows are recognizable by name: role names start with
``test-role-`` and login names with ``test-``; list and count
assertions over roles and users filter them out.
"""

import functools
import uuid
import weakref
from dataclasses import dataclass
from typing import Final, cast

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

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
