"""Role endpoints (Phase 13 slice 12 — Administration → Roles & permissions).

HTTP surface of the role configuration: list, create, and rename or
grant/revoke permissions of named roles. Configuration only — no
endpoint checks or simulates an identity, and nothing reads a role to
allow or refuse an action, until Phase 14. Roles are assigned to Users
(application accounts), never to Workers.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"``; a permission outside the closed vocabulary is
refused by the schema), the Application layer (``app.application.roles``)
owns the name rule, the delta semantics, the audit protocol and the
transaction, and the central handlers in ``app.api.errors`` translate
typed failures.

Deliberate surface decisions:

- No role DELETE and no single-item GET: roles are renamed, never
  deleted; the list is the read model.
- ``PATCH`` carries deltas — ``grant_permissions`` and
  ``revoke_permissions`` — so two editors changing different keys never
  revert each other; it answers with the role, also when nothing
  changed.
- ``permissions`` in every response are the permission keys sorted by
  value; ``user_count`` counts active and inactive Users.
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import SessionDep
from app.application import roles
from app.domain.enums import Permission

router = APIRouter(prefix="/api")


class RoleResponse(BaseModel):
    id: int
    name: str
    # Sorted by value.
    permissions: list[Permission]
    # Users holding the role, active and inactive.
    user_count: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class RoleCreateRequest(BaseModel):
    """Name and initial grants only; the audit actor stays NULL from this
    HTTP surface until Phase 14."""

    model_config = ConfigDict(extra="forbid")

    name: str
    permissions: list[Permission] = []


class RoleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    grant_permissions: list[Permission] = []
    revoke_permissions: list[Permission] = []


def role_response(view: roles.RoleView) -> RoleResponse:
    return RoleResponse(
        id=view.id,
        name=view.name,
        permissions=list(view.permissions),
        user_count=view.user_count,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.get("/roles")
def list_roles(session: SessionDep) -> list[RoleResponse]:
    return [role_response(view) for view in roles.list_roles(session)]


@router.post("/roles", status_code=201)
def create_role(body: RoleCreateRequest, session: SessionDep) -> RoleResponse:
    view = roles.create_role(session, name=body.name, permissions=body.permissions)
    return role_response(view)


@router.patch("/roles/{role_id}")
def update_role(role_id: int, body: RoleUpdateRequest, session: SessionDep) -> RoleResponse:
    view = roles.update_role(session, role_id, **body.model_dump(exclude_unset=True))
    return role_response(view)
