"""PartNumber master endpoints (Phase 4 lookup; Phase 13 management).

HTTP surface for the PN lookup/create step of the Add Part flow
(GUI_DESIGN §11.2/§11.3), the minimal barcode-label capability of
Phase 4 (enough data to view/print the derived ``PF:PN:<pn>`` label),
and Part Numbers management (Phase 13 slice 7, GUI_DESIGN §14): the
bounded management list, create, edit of the optional details, the PN
image and the hard delete of the master record.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"`` — a client that submits the PN in an edit, the
barcode, an image field or an actor is rejected instead of silently
ignored), the Application layer owns normalization, create-on-first-use,
the image validation, the locks, the audit protocol and the
transaction, and the central handlers in ``app.api.errors`` translate
typed failures.

Deliberate surface decisions:

- ``barcode_value`` appears only in responses — the PN barcode is
  fully derived from the canonical PN and never stored or entered.
- The PN never travels in a path segment: it is an opaque arbitrary
  string, so every route addresses it by a query parameter (``number``
  exact canonical resolution, ``search`` contains-match); ``page`` and
  ``image`` are literal path segments.
- ``POST /part-numbers`` is create-only (Phase 13): first valid use
  creates the master (201) with its optional details and its
  ``CREATED`` audit row; an existing master answers 409. Create-on-
  first-use at the Work Order save and the Scan Station receipt is
  unchanged.
- ``PATCH`` changes only the details the body names (an omitted key
  stays, ``null`` clears, ``{}`` is a no-op); the PN itself is never
  edited. ``DELETE`` hard-deletes the master — details and image —
  and never touches production data.
- No response carries image bytes, type or size: ``image_updated_at``
  (``null`` = the default image) is the cache version the client puts
  in the image URL as ``?v=`` (ignored here, cache-busting only). The
  image is uploaded as the raw request body with its own
  ``Content-Type`` (``app.api.uploads``) and served with an ``ETag``
  and ``Cache-Control: private, no-cache``.
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import SessionDep
from app.api.uploads import UploadedImage, read_image_body, stored_image_response
from app.application import part_numbers
from app.infrastructure.models import PartNumber

router = APIRouter(prefix="/api")


class PartNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # The canonical uppercase PN — the identity and natural key.
    part_number: str
    # Derived label data: PF:PN:<canonical-part-number>.
    barcode_value: str
    # Optional free-text details (Phase 13); null = not saved.
    name: str | None
    current_revision: str | None
    erp_id: str | None
    # The image's cache version; null = the default image.
    image_updated_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PartNumberPageResponse(BaseModel):
    rows: list[PartNumberResponse]
    # Masters matching the search; `rows` is the page at `offset`.
    total: int
    offset: int
    limit: int
    has_more: bool


class PartNumberCreateRequest(BaseModel):
    """The PN and its optional details — the audit ``actor_reference``
    is never client-writable: it stays NULL from this HTTP surface
    until an authenticated identity exists (Phase 14)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str
    name: str | None = None
    current_revision: str | None = None
    erp_id: str | None = None


class PartNumberUpdateRequest(BaseModel):
    """The details to change; the PN itself is never editable."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    current_revision: str | None = None
    erp_id: str | None = None


def part_number_response(master: PartNumber) -> PartNumberResponse:
    return PartNumberResponse.model_validate(master)


@router.get("/part-numbers")
def list_part_numbers(
    session: SessionDep, search: str | None = None, number: str | None = None
) -> list[PartNumberResponse]:
    """PN lookup: ``number`` resolves one exact canonical PN (empty list
    on a miss — the Add Part flow then offers creation), ``search``
    filters by contains-match over the PN or the saved Name /
    Description.

    A ``search`` (or unfiltered) listing is bounded server-side at
    ``part_numbers.SEARCH_RESULT_LIMIT`` masters; an exact ``number``
    resolution is never bounded away, so a short but valid canonical PN
    still resolves.
    """
    matches: list[PartNumber]
    if number is not None:
        master = part_numbers.resolve_part_number(session, number)
        matches = [master] if master is not None else []
    else:
        matches = part_numbers.list_part_numbers(session, search=search)
    return [part_number_response(master) for master in matches]


@router.get("/part-numbers/page")
def part_number_page(
    session: SessionDep,
    search: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(part_numbers.DEFAULT_PAGE_LIMIT, ge=1, le=part_numbers.MAX_PAGE_LIMIT),
) -> PartNumberPageResponse:
    """The management list: a bounded page and the total of matches."""
    page = part_numbers.part_number_page(session, search=search, offset=offset, limit=limit)
    return PartNumberPageResponse(
        rows=[part_number_response(master) for master in page.rows],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
        has_more=page.has_more,
    )


@router.post("/part-numbers", status_code=201)
def create_part_number(body: PartNumberCreateRequest, session: SessionDep) -> PartNumberResponse:
    master = part_numbers.create_part_number(
        session,
        body.part_number,
        name=body.name,
        current_revision=body.current_revision,
        erp_id=body.erp_id,
    )
    return part_number_response(master)


@router.patch("/part-numbers")
def update_part_number(
    number: str, body: PartNumberUpdateRequest, session: SessionDep
) -> PartNumberResponse:
    master = part_numbers.update_part_number(session, number, **body.model_dump(exclude_unset=True))
    return part_number_response(master)


@router.delete("/part-numbers", status_code=204)
def delete_part_number(number: str, session: SessionDep) -> None:
    part_numbers.delete_part_number(session, number)


@router.put("/part-numbers/image")
def set_part_number_image(
    number: str,
    image: Annotated[UploadedImage, Depends(read_image_body)],
    session: SessionDep,
) -> PartNumberResponse:
    master = part_numbers.set_part_number_image(
        session, number, data=image.data, declared_type=image.content_type
    )
    return part_number_response(master)


@router.delete("/part-numbers/image")
def remove_part_number_image(number: str, session: SessionDep) -> PartNumberResponse:
    return part_number_response(part_numbers.remove_part_number_image(session, number))


@router.get("/part-numbers/image")
def get_part_number_image(
    number: str,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    image = part_numbers.get_part_number_image(session, number)
    return stored_image_response(image.data, image.content_type, image.updated_at, if_none_match)
