"""PartNumber master endpoints (Phase 4 — Work Order intake, Add Part).

HTTP surface for the PN lookup/create step of the Add Part flow
(GUI_DESIGN §11.2/§11.3) and the minimal barcode-label capability of
Phase 4: enough data to view/print the derived ``PF:PN:<pn>`` label.

Routes stay thin orchestration: request schemas validate shape only
(``extra="forbid"``), the Application layer owns normalization,
create-on-first-use, the audit protocol and the transaction, and the
central handlers in ``app.api.errors`` translate typed failures.

Deliberate surface decisions:

- ``barcode_value`` appears only in responses — the PN barcode is
  fully derived from the canonical PN and never stored or entered.
- Lookup uses query parameters (``search`` contains-match, ``number``
  exact canonical resolution) instead of a path segment: the PN is an
  opaque arbitrary string, so it never travels as a URL path value.
- ``POST /part-numbers`` is create-or-reuse: an existing canonical PN
  returns the existing master (200), first valid use creates it (201)
  with its ``CREATED`` audit row in the same transaction. There is no
  DELETE and no update — master metadata management is Phase 13.
"""

import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import SessionDep
from app.application import part_numbers
from app.infrastructure.models import PartNumber

router = APIRouter(prefix="/api")


class PartNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # The canonical uppercase PN — the identity and natural key.
    part_number: str
    # Derived label data: PF:PN:<canonical-part-number>.
    barcode_value: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PartNumberCreateRequest(BaseModel):
    """Only the PN itself — the audit ``actor_reference`` is never
    client-writable: it stays NULL from this HTTP surface until an
    authenticated identity exists (Phase 14)."""

    model_config = ConfigDict(extra="forbid")

    part_number: str


@router.get("/part-numbers")
def list_part_numbers(
    session: SessionDep, search: str | None = None, number: str | None = None
) -> list[PartNumberResponse]:
    """PN lookup: ``number`` resolves one exact canonical PN (empty list
    on a miss — the Add Part flow then offers creation), ``search``
    filters by contains-match.

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
    return [PartNumberResponse.model_validate(master) for master in matches]


@router.post("/part-numbers")
def create_part_number(
    body: PartNumberCreateRequest, session: SessionDep, response: Response
) -> PartNumberResponse:
    master, created = part_numbers.create_part_number(session, body.part_number)
    response.status_code = 201 if created else 200
    return PartNumberResponse.model_validate(master)
