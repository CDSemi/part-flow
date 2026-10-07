"""User endpoints (Phase 13 slice 12 — Administration → Users).

HTTP surface of the application accounts: list, create and edit Users
(login name, display name, role, active flag) and set, remove or read a
User's avatar image. Users sign in through ``/api/session`` (Phase 14
slice 1); only ``PUT /api/users/{id}/password`` checks a permission so
far; credentials are never part of a user response. Users are never
Workers: the Workers registry is ``/api/workers``.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"`` — a client that submits a server-owned field such
as ``id``, a password or an actor is rejected instead of silently
ignored; ``role_id`` is a strict integer), the Application layer
(``app.application.users``) owns the login-name rule, the image
validation, the audit protocol and the transaction, and the central
handlers in ``app.api.errors`` translate typed failures.

Deliberate surface decisions:

- No User DELETE and no single-item GET — Users are deactivated, never
  deleted; the list is the read model.
- ``login_name`` in every response is the stored canonical value
  (trimmed, lowercase); a request may send any letter case.
- No theme-preference endpoint and no theme field: the User preference
  is stored only in Phase 13 (Phase 14 adds its writer).
- No response carries image bytes, type or size: ``avatar_updated_at``
  (``null`` = no avatar) is the cache version the client puts in the
  avatar URL as ``?v=`` (ignored here, cache-busting only).
- The avatar is uploaded as the raw request body with its own
  ``Content-Type`` (``app.api.uploads``); ``PUT`` and ``DELETE`` answer
  with the User, also when nothing changed; ``GET …/avatar`` serves the
  stored bytes through the shared ``stored_image_response`` (ETag, 304).
- ``PUT /users/{id}/password`` (Phase 14 slice 1, ``MANAGE_USERS_AND_ROLES``)
  sets another User's temporary password, ends the User's sign-ins and
  clears a lock; one's own password is changed through
  ``/api/session/password``.
- Every route answering with Users adds ``sign_in_state`` (no password,
  temporary password, password set, locked) only for a signed-in caller
  holding ``MANAGE_USERS_AND_ROLES`` — the key is absent for everyone
  else, whose responses keep the slice 12 shape exactly. A declared
  response model would drop a subclass-only field, so these routes
  render their answer explicitly (``_render``); the OpenAPI schema
  documents both shapes.
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt
from sqlalchemy.orm import Session

from app.api.authorization import OptionalPrincipalDep, RequirePermission, holds
from app.api.dependencies import SessionDep
from app.api.session import Secret
from app.api.uploads import UploadedImage, read_image_body, stored_image_response
from app.application import authentication, user_access, users
from app.application.authentication import Principal
from app.domain.enums import Permission, SignInState

router = APIRouter(prefix="/api")


class UserResponse(BaseModel):
    id: int
    # The stored canonical login name (trimmed, lowercase).
    login_name: str
    display_name: str
    role_id: int
    role_name: str
    is_active: bool
    # The avatar's cache version; null = no avatar.
    avatar_updated_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class UserAdministrationResponse(UserResponse):
    """A User as answered to a caller who may manage users and roles."""

    sign_in_state: SignInState


class UserCreateRequest(BaseModel):
    """Login name, display name and role only — every other field is
    server-owned; the audit actor stays NULL from this HTTP surface until
    Phase 14 slice 2."""

    model_config = ConfigDict(extra="forbid")

    login_name: str
    display_name: str
    role_id: StrictInt


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str | None = None
    display_name: str | None = None
    role_id: StrictInt | None = None
    is_active: bool | None = None


class UserPasswordSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: Secret


def user_response(view: users.UserView) -> UserResponse:
    return UserResponse(
        id=view.id,
        login_name=view.login_name,
        display_name=view.display_name,
        role_id=view.role_id,
        role_name=view.role_name,
        is_active=view.is_active,
        avatar_updated_at=view.avatar_updated_at,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _administration_response(
    view: users.UserView, state: SignInState
) -> UserAdministrationResponse:
    return UserAdministrationResponse(**user_response(view).model_dump(), sign_in_state=state)


def _answers(
    session: Session, principal: Principal | None, views: list[users.UserView]
) -> list[UserResponse]:
    """S12's shape, plus ``sign_in_state`` for a caller who may manage users."""
    if not holds(principal, Permission.MANAGE_USERS_AND_ROLES):
        return [user_response(view) for view in views]
    states = user_access.sign_in_states(session, [view.id for view in views])
    return [_administration_response(view, states[view.id]) for view in views]


# Documents the per-caller shape; the routes return a rendered response,
# which FastAPI passes through without applying this model.
_EitherUser = UserAdministrationResponse | UserResponse


def _render(content: UserResponse | list[UserResponse], status_code: int = 200) -> JSONResponse:
    """Serialize exactly the models built (a declared response model would
    drop the subclass-only ``sign_in_state``)."""
    return JSONResponse(status_code=status_code, content=jsonable_encoder(content))


@router.get("/users", response_model=list[_EitherUser])
def list_users(session: SessionDep, principal: OptionalPrincipalDep) -> JSONResponse:
    return _render(_answers(session, principal, users.list_users(session)))


@router.post("/users", status_code=201, response_model=_EitherUser)
def create_user(
    body: UserCreateRequest, session: SessionDep, principal: OptionalPrincipalDep
) -> JSONResponse:
    view = users.create_user(
        session, login_name=body.login_name, display_name=body.display_name, role_id=body.role_id
    )
    return _render(_answers(session, principal, [view])[0], 201)


@router.patch("/users/{user_id}", response_model=_EitherUser)
def update_user(
    user_id: int, body: UserUpdateRequest, session: SessionDep, principal: OptionalPrincipalDep
) -> JSONResponse:
    view = users.update_user(session, user_id, **body.model_dump(exclude_unset=True))
    return _render(_answers(session, principal, [view])[0])


@router.put("/users/{user_id}/avatar", response_model=_EitherUser)
def set_user_avatar(
    user_id: int,
    image: Annotated[UploadedImage, Depends(read_image_body)],
    session: SessionDep,
    principal: OptionalPrincipalDep,
) -> JSONResponse:
    view = users.set_user_avatar(
        session, user_id, data=image.data, declared_type=image.content_type
    )
    return _render(_answers(session, principal, [view])[0])


@router.delete("/users/{user_id}/avatar", response_model=_EitherUser)
def remove_user_avatar(
    user_id: int, session: SessionDep, principal: OptionalPrincipalDep
) -> JSONResponse:
    view = users.remove_user_avatar(session, user_id)
    return _render(_answers(session, principal, [view])[0])


@router.put("/users/{user_id}/password")
def set_user_password(
    user_id: int,
    body: UserPasswordSetRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(RequirePermission(Permission.MANAGE_USERS_AND_ROLES))],
) -> UserAdministrationResponse:
    view = authentication.set_user_password(
        session, user_id, new_password=body.new_password.get_secret_value(), actor=principal
    )
    return _administration_response(view, user_access.sign_in_states(session, [view.id])[view.id])


@router.get("/users/{user_id}/avatar")
def get_user_avatar(
    user_id: int,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    avatar = users.get_user_avatar(session, user_id)
    return stored_image_response(avatar.data, avatar.content_type, avatar.updated_at, if_none_match)
