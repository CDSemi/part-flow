"""Who is signed in, and what they may do (Phase 14 slice 1).

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
  ``SignedInDep`` is the key-less form.

Checks run before the route body, so before any lock or write. In slice
1 they gate only the new sign-in routes; no existing route changes its
gate. The principal is always derived on the server from the session —
never from a request body.

Also the cookie helpers: the raw token goes only into ``Set-Cookie``
(``HttpOnly``, ``SameSite=Strict``, ``Path=/api``, ``Secure`` when
``SESSION_COOKIE_SECURE`` is on, ``Max-Age`` the session's remaining
lifetime).
"""

from typing import Annotated, Final

from fastapi import Depends, Request, Response

from app.api.dependencies import SessionDep
from app.application import authentication
from app.application.authentication import SESSION_COOKIE, Principal
from app.application.errors import (
    AuthenticationRequiredError,
    PasswordChangeRequiredError,
    PermissionDeniedError,
)
from app.core.config import get_settings
from app.domain.enums import Permission

_COOKIE_PATH: Final = "/api"


def optional_principal(request: Request, session: SessionDep) -> Principal | None:
    return authentication.resolve_principal(session, request.cookies.get(SESSION_COOKIE))


OptionalPrincipalDep = Annotated[Principal | None, Depends(optional_principal)]


def current_user(principal: OptionalPrincipalDep) -> Principal:
    if principal is None:
        raise AuthenticationRequiredError(authentication.AUTHENTICATION_REQUIRED_MESSAGE)
    return principal


CurrentUserDep = Annotated[Principal, Depends(current_user)]


class RequirePermission:
    """Dependency factory: a signed-in User holding every given key."""

    def __init__(self, *keys: Permission) -> None:
        self.keys = keys

    def __call__(self, principal: CurrentUserDep) -> Principal:
        if principal.must_change_password:
            raise PasswordChangeRequiredError(authentication.PASSWORD_CHANGE_REQUIRED_MESSAGE)
        if any(key not in principal.permissions for key in self.keys):
            raise PermissionDeniedError(
                authentication.PERMISSION_DENIED_MESSAGE,
                required=tuple(str(key) for key in self.keys),
            )
        return principal


SignedInDep = Annotated[Principal, Depends(RequirePermission())]


def holds(principal: Principal | None, key: Permission) -> bool:
    """Whether a usable principal (no pending forced change) holds ``key``."""
    return (
        principal is not None
        and not principal.must_change_password
        and key in principal.permissions
    )


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
