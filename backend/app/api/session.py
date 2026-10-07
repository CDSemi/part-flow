"""User sign-in endpoints (Phase 14 slice 1; owner decisions OD-P1–OD-P3).

HTTP surface of ``app.application.authentication``:

- ``GET /session`` — the signed-in User (with the role's effective
  permissions, whether a new password is required and when the sign-in
  expires; ``null`` when not signed in) and whether first-run setup is
  open. A resolved session's cookie is re-issued with its remaining
  lifetime under the current policy, so a lengthened expiry or "never"
  reaches the browser; an unusable cookie is cleared.
- ``POST /session`` — sign in with a login name and password; a new
  session token is always issued (a presented cookie is never adopted,
  and its session ends REPLACED). One generic 401 for every refusal.
- ``DELETE /session`` — sign out; 204 also without a session.
- ``PUT /session/password`` — change the signed-in User's own password
  (allowed while a change is required); every other sign-in of the User
  ends and the cookie rotates.

Every response carries ``Cache-Control: no-store`` and the CSRF header
rule applies (``app.api.csrf``). Passwords travel as ``SecretStr`` and
every password field is capped at 1024 raw characters; nothing here
logs a request body.
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.authorization import (
    CurrentUserDep,
    clear_session_cookie,
    set_session_cookie,
)
from app.api.dependencies import SessionDep, SetupGateDep
from app.application import authentication, first_run
from app.application.authentication import SESSION_COOKIE, Principal, SessionGrant
from app.domain.enums import Permission

router = APIRouter(prefix="/api")

# Every password or token field: at most 1024 raw characters (a longer
# body fails validation before any normalization or hashing).
Secret = Annotated[SecretStr, Field(max_length=1024)]


class SessionUserResponse(BaseModel):
    id: int
    login_name: str
    display_name: str
    role_id: int
    role_name: str
    avatar_updated_at: datetime.datetime | None
    # The role's effective permission keys, sorted.
    permissions: list[Permission]
    must_change_password: bool
    # null = the sign-in never expires.
    session_expires_at: datetime.datetime | None


class SessionStateResponse(BaseModel):
    user: SessionUserResponse | None
    setup_open: bool


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(max_length=1024)
    password: Secret


class OwnPasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Secret
    new_password: Secret


def session_user_response(principal: Principal) -> SessionUserResponse:
    return SessionUserResponse(
        id=principal.user_id,
        login_name=principal.login_name,
        display_name=principal.display_name,
        role_id=principal.role_id,
        role_name=principal.role_name,
        avatar_updated_at=principal.avatar_updated_at,
        permissions=sorted(principal.permissions),
        must_change_password=principal.must_change_password,
        session_expires_at=principal.session_expires_at,
    )


def session_state(principal: Principal | None, setup_open: bool) -> SessionStateResponse:
    return SessionStateResponse(
        user=None if principal is None else session_user_response(principal),
        setup_open=setup_open,
    )


def grant_session(response: Response, grant: SessionGrant) -> None:
    """Put a new session's token into the cookie (its only destination)."""
    set_session_cookie(response, grant.token, authentication.cookie_max_age(grant.principal))


@router.get("/session")
def get_session_state(
    request: Request, response: Response, session: SessionDep, gate: SetupGateDep
) -> SessionStateResponse:
    token = request.cookies.get(SESSION_COOKIE)
    principal = authentication.resolve_principal(session, token)
    if principal is not None and token is not None:
        set_session_cookie(response, token, authentication.cookie_max_age(principal))
    elif token is not None:
        clear_session_cookie(response)
    return session_state(principal, first_run.is_setup_open(session, gate))


@router.post("/session")
def sign_in(
    body: SignInRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse:
    grant = authentication.sign_in(
        session,
        login_name=body.login_name,
        password=body.password.get_secret_value(),
        replaced_token=request.cookies.get(SESSION_COOKIE),
    )
    grant_session(response, grant)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))


@router.delete("/session", status_code=204)
def sign_out(request: Request, session: SessionDep) -> Response:
    authentication.sign_out(session, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.put("/session/password")
def change_own_password(
    body: OwnPasswordChangeRequest,
    principal: CurrentUserDep,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse:
    grant = authentication.change_own_password(
        session,
        principal,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    grant_session(response, grant)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))
