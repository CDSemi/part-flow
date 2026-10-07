"""Worker registry endpoints (Phase 13 — Administration → Workers).

HTTP surface for the Workers registry: list, create and edit Workers
(name, badge barcode, active flag) and set, remove or read a Worker's
avatar image.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"`` — a client that submits a server-owned field such
as ``id``, an avatar field or an actor is rejected instead of silently
ignored), the Application layer owns the badge rule, the image
validation, the audit protocol and the transaction, and the central
handlers in ``app.api.errors`` translate typed failures.

Access (Phase 14 slice 2): the list needs a signed-in User, every
write ``MANAGE_WORKERS``, and the avatar image stays public (the Scan
Station shows it). Badge values are a credential-like secret of the
Scan Station sign-in: the list carries ``badge_barcode`` only for a
caller holding ``MANAGE_WORKERS`` — for everyone else the key is
absent, never ``null`` — so the list renders its answer explicitly; the
OpenAPI schema documents both shapes. Write responses always carry it
(only a ``MANAGE_WORKERS`` holder reaches them). Every audit row names
the signed-in User (``actor_user_id``).

Deliberate surface decisions:

- No Worker DELETE exists — Workers are deactivated, never deleted.
- ``badge_barcode`` in every response is the stored canonical value
  (trimmed, UPPERCASE); a request may send any letter case.
- No response carries image bytes, type or size: ``avatar_updated_at``
  (``null`` = no avatar) is the cache version the client puts in the
  avatar URL as ``?v=`` (ignored here, cache-busting only).
- The avatar is uploaded as the raw request body with its own
  ``Content-Type`` (``app.api.uploads``); ``PUT`` and ``DELETE`` answer
  with the Worker, also when nothing changed.
- ``GET …/avatar`` serves the stored bytes with an ``ETag`` derived from
  ``avatar_updated_at`` and ``Cache-Control: private, no-cache``, and
  answers a matching ``If-None-Match`` with 304.
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.authorization import RequirePermission, SignedInDep, holds
from app.api.dependencies import SessionDep
from app.api.uploads import UploadedImage, read_image_body, stored_image_response
from app.application import workers
from app.application.authentication import Principal
from app.domain.enums import Permission
from app.infrastructure.models import Worker

router = APIRouter(prefix="/api")

WorkerManagerDep = Annotated[Principal, Depends(RequirePermission(Permission.MANAGE_WORKERS))]


class WorkerProfileResponse(BaseModel):
    """A Worker as listed to a caller who may not manage Workers: no badge."""

    id: int
    name: str
    is_active: bool
    # The avatar's cache version; null = no avatar.
    avatar_updated_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WorkerResponse(BaseModel):
    id: int
    name: str
    # The stored canonical badge (trimmed, UPPERCASE).
    badge_barcode: str
    is_active: bool
    # The avatar's cache version; null = no avatar.
    avatar_updated_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WorkerCreateRequest(BaseModel):
    """Name and badge only — every other field is server-owned; the
    audit actor (``actor_user_id``) is the signed-in User (Phase 14 slice
    2); ``actor_reference`` is legacy and stays NULL."""

    model_config = ConfigDict(extra="forbid")

    name: str
    badge_barcode: str


class WorkerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    badge_barcode: str | None = None
    is_active: bool | None = None


def worker_response(worker: Worker) -> WorkerResponse:
    return WorkerResponse(
        id=worker.id,
        name=worker.name,
        badge_barcode=worker.badge_barcode,
        is_active=worker.is_active,
        avatar_updated_at=worker.avatar_image_updated_at,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )


def _profile_response(worker: Worker) -> WorkerProfileResponse:
    return WorkerProfileResponse(
        id=worker.id,
        name=worker.name,
        is_active=worker.is_active,
        avatar_updated_at=worker.avatar_image_updated_at,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )


# Documents the per-caller shape; the list returns a rendered response,
# which FastAPI passes through without applying this model.
_EitherWorker = WorkerResponse | WorkerProfileResponse


@router.get("/workers", response_model=list[_EitherWorker])
def list_workers(principal: SignedInDep, session: SessionDep) -> JSONResponse:
    listed = workers.list_workers(session)
    answers: list[WorkerResponse] | list[WorkerProfileResponse] = (
        [worker_response(worker) for worker in listed]
        if holds(principal, Permission.MANAGE_WORKERS)
        else [_profile_response(worker) for worker in listed]
    )
    # Serialize exactly the models built: the badge-less model has no key.
    return JSONResponse(content=jsonable_encoder(answers))


@router.post("/workers", status_code=201)
def create_worker(
    principal: WorkerManagerDep, body: WorkerCreateRequest, session: SessionDep
) -> WorkerResponse:
    worker = workers.create_worker(
        session, name=body.name, badge_barcode=body.badge_barcode, actor_user_id=principal.user_id
    )
    return worker_response(worker)


@router.patch("/workers/{worker_id}")
def update_worker(
    principal: WorkerManagerDep, worker_id: int, body: WorkerUpdateRequest, session: SessionDep
) -> WorkerResponse:
    worker = workers.update_worker(
        session, worker_id, actor_user_id=principal.user_id, **body.model_dump(exclude_unset=True)
    )
    return worker_response(worker)


@router.put("/workers/{worker_id}/avatar")
def set_worker_avatar(
    principal: WorkerManagerDep,
    worker_id: int,
    image: Annotated[UploadedImage, Depends(read_image_body)],
    session: SessionDep,
) -> WorkerResponse:
    worker = workers.set_worker_avatar(
        session,
        worker_id,
        data=image.data,
        declared_type=image.content_type,
        actor_user_id=principal.user_id,
    )
    return worker_response(worker)


@router.delete("/workers/{worker_id}/avatar")
def remove_worker_avatar(
    principal: WorkerManagerDep, worker_id: int, session: SessionDep
) -> WorkerResponse:
    return worker_response(
        workers.remove_worker_avatar(session, worker_id, actor_user_id=principal.user_id)
    )


@router.get("/workers/{worker_id}/avatar")
def get_worker_avatar(
    worker_id: int,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    avatar = workers.get_worker_avatar(session, worker_id)
    return stored_image_response(avatar.data, avatar.content_type, avatar.updated_at, if_none_match)
