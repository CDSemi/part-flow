"""User endpoints (Phase 13 slice 12 — Administration → Users).

HTTP surface of the application accounts: list, create and edit Users
(login name, display name, role, active flag) and set, remove or read a
User's avatar image. Configuration only — no endpoint checks or
simulates an identity until Phase 14, and no credential field exists.
Users are never Workers: the Workers registry is ``/api/workers``.

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
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.dependencies import SessionDep
from app.api.uploads import UploadedImage, read_image_body, stored_image_response
from app.application import users

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


class UserCreateRequest(BaseModel):
    """Login name, display name and role only — every other field is
    server-owned; the audit actor stays NULL from this HTTP surface until
    Phase 14."""

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


@router.get("/users")
def list_users(session: SessionDep) -> list[UserResponse]:
    return [user_response(view) for view in users.list_users(session)]


@router.post("/users", status_code=201)
def create_user(body: UserCreateRequest, session: SessionDep) -> UserResponse:
    view = users.create_user(
        session, login_name=body.login_name, display_name=body.display_name, role_id=body.role_id
    )
    return user_response(view)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserUpdateRequest, session: SessionDep) -> UserResponse:
    view = users.update_user(session, user_id, **body.model_dump(exclude_unset=True))
    return user_response(view)


@router.put("/users/{user_id}/avatar")
def set_user_avatar(
    user_id: int,
    image: Annotated[UploadedImage, Depends(read_image_body)],
    session: SessionDep,
) -> UserResponse:
    view = users.set_user_avatar(
        session, user_id, data=image.data, declared_type=image.content_type
    )
    return user_response(view)


@router.delete("/users/{user_id}/avatar")
def remove_user_avatar(user_id: int, session: SessionDep) -> UserResponse:
    return user_response(users.remove_user_avatar(session, user_id))


@router.get("/users/{user_id}/avatar")
def get_user_avatar(
    user_id: int,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    avatar = users.get_user_avatar(session, user_id)
    return stored_image_response(avatar.data, avatar.content_type, avatar.updated_at, if_none_match)
