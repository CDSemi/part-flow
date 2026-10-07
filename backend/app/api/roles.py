"""Role endpoints (Phase 13 slice 12 — Administration → Roles & permissions).

HTTP surface of the role configuration: list, create, and rename or
grant/revoke permissions of named roles. Roles are assigned to Users
(application accounts), never to Workers.

Access (Phase 14 slice 2): the list needs a signed-in User. A create
needs ``MANAGE_USERS_AND_ROLES``; a change naming a correction
permission or ``MANAGE_CORRECTION_PERMISSIONS`` also needs
``MANAGE_CORRECTION_PERMISSIONS`` — and an edit of correction keys only
needs only that one. The route checks the request's principal first (a
fast fail); the service checks again on the acting User re-read under
the User-administration lock and refuses a revocation that would leave
no active User with a password holding a management key (409
``last_permission_holder``).

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
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.authorization import RequirePermission, SignedInDep, actor_of
from app.api.dependencies import SessionDep
from app.application import authorization, roles
from app.application.authentication import Principal
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
    # The role applied at Scan Stations (Phase 14 slice 4): every enrolled
    # station device has its Scan Station permissions.
    applies_at_scan_stations: bool


class RoleCreateRequest(BaseModel):
    """Name and initial grants only; the audit actor (``actor_user_id``)
    is the signed-in User (Phase 14 slice 2); ``actor_reference`` is
    legacy and stays NULL."""

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
        applies_at_scan_stations=view.applies_at_scan_stations,
    )


@router.get("/roles")
def list_roles(principal: SignedInDep, session: SessionDep) -> list[RoleResponse]:
    return [role_response(view) for view in roles.list_roles(session)]


@router.post("/roles", status_code=201)
def create_role(
    principal: Annotated[Principal, Depends(RequirePermission(Permission.MANAGE_USERS_AND_ROLES))],
    body: RoleCreateRequest,
    session: SessionDep,
) -> RoleResponse:
    authorization.require_role_change(
        actor_of(principal), authorization.role_create_permissions(body.permissions)
    )
    view = roles.create_role(session, name=body.name, permissions=body.permissions, actor=principal)
    return role_response(view)


@router.patch("/roles/{role_id}")
def update_role(
    principal: SignedInDep, role_id: int, body: RoleUpdateRequest, session: SessionDep
) -> RoleResponse:
    fields = body.model_dump(exclude_unset=True)
    authorization.require_role_change(
        actor_of(principal),
        authorization.role_update_permissions(
            name_given="name" in fields,
            grant=body.grant_permissions,
            revoke=body.revoke_permissions,
        ),
    )
    view = roles.update_role(session, role_id, actor=principal, **fields)
    return role_response(view)
