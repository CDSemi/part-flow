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
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import SessionDep
from app.api.uploads import UploadedImage, read_image_body
from app.application import workers
from app.infrastructure.models import Worker

router = APIRouter(prefix="/api")


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
    audit actor stays NULL from this HTTP surface until Phase 14."""

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


@router.get("/workers")
def list_workers(session: SessionDep) -> list[WorkerResponse]:
    return [worker_response(worker) for worker in workers.list_workers(session)]


@router.post("/workers", status_code=201)
def create_worker(body: WorkerCreateRequest, session: SessionDep) -> WorkerResponse:
    worker = workers.create_worker(session, name=body.name, badge_barcode=body.badge_barcode)
    return worker_response(worker)


@router.patch("/workers/{worker_id}")
def update_worker(worker_id: int, body: WorkerUpdateRequest, session: SessionDep) -> WorkerResponse:
    worker = workers.update_worker(session, worker_id, **body.model_dump(exclude_unset=True))
    return worker_response(worker)


@router.put("/workers/{worker_id}/avatar")
def set_worker_avatar(
    worker_id: int,
    image: Annotated[UploadedImage, Depends(read_image_body)],
    session: SessionDep,
) -> WorkerResponse:
    worker = workers.set_worker_avatar(
        session, worker_id, data=image.data, declared_type=image.content_type
    )
    return worker_response(worker)


@router.delete("/workers/{worker_id}/avatar")
def remove_worker_avatar(worker_id: int, session: SessionDep) -> WorkerResponse:
    return worker_response(workers.remove_worker_avatar(session, worker_id))


def _avatar_etag(updated_at: datetime.datetime) -> str:
    """Strong ETag from the avatar version, in whole microseconds."""
    return f'"{int(updated_at.timestamp()) * 1_000_000 + updated_at.microsecond}"'


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    candidates = {candidate.strip() for candidate in if_none_match.split(",")}
    # If-None-Match uses the weak comparison (RFC 9110 §13.1.2).
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates


@router.get("/workers/{worker_id}/avatar")
def get_worker_avatar(
    worker_id: int,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    avatar = workers.get_worker_avatar(session, worker_id)
    etag = _avatar_etag(avatar.updated_at)
    # A 304 repeats the validator and the caching policy (RFC 9110 §15.4.5).
    cache_headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=cache_headers)
    return Response(
        content=avatar.data,
        media_type=avatar.content_type,
        headers={**cache_headers, "X-Content-Type-Options": "nosniff"},
    )
