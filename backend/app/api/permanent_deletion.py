"""Permanent deletion endpoints (Phase 16, history archival design D17; build step 3).

HTTP surface of Delete permanently… in Work Order Details and the Part
Number dialog. Routes stay thin: request schemas validate shape only
(``extra="forbid"``), the Application layer
(``app.application.deletion_requests``) owns every rule, audit row and
transaction, and the central handlers in ``app.api.errors`` translate typed
failures (404 unknown request, 409 conflict — a refused deletion with its
``refusals`` —, 422 invalid). Nothing here deletes: a request is recorded,
and the host job carries it out within minutes.

- ``GET /permanent-deletion/preview?work_order_number=…&work_order_number=…``
  or ``?part_number=…`` — what a deletion would delete (the Work Orders and
  Part Numbers, the rows per table, the audit rows kept) and every refusal,
  plus the open request if one exists. Reads only.
- ``GET /permanent-deletion/requests?limit=N`` — the newest requests first
  (default 50, 1-500).
- ``POST /permanent-deletion/requests`` — ``{"work_order_numbers": [text] |
  null, "part_number": text | null, "reason": text, "confirmation":
  [text]}``: record a request (APPROVED) with every row its plan deletes;
  201 with the request. The confirmation is every Work Order Number typed
  again, or the Part Number typed again; it is not stored.
- ``POST /permanent-deletion/requests/{request_id}/cancel`` — cancel an
  APPROVED request (a cancelled request answers unchanged).

Access: every route needs ``DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY``,
and each audit row names the signed-in User.
"""

import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.authorization import RequirePermission
from app.api.dependencies import SessionDep
from app.application import deletion_requests
from app.application.authentication import Principal
from app.application.deletion_requests import RequestUser, RequestView
from app.domain.enums import Permission
from app.infrastructure.models import DeletionRequest

router = APIRouter(prefix="/api")

PermanentDeletionDep = Annotated[
    Principal,
    Depends(RequirePermission(Permission.DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY)),
]


class UserRef(BaseModel):
    user_id: int
    display_name: str


class Scope(BaseModel):
    work_order_numbers: list[str]
    part_numbers: list[str]


class Refusal(BaseModel):
    code: str
    message: str


class Backup(BaseModel):
    """The pre-purge backup the host job took before the deletion."""

    name: str
    dump_started_at: datetime.datetime


class RequestResponse(BaseModel):
    id: int
    state: str
    scope: Scope
    reason: str
    requested_by: UserRef | None
    requested_by_operator: str | None
    requested_at: datetime.datetime
    # What the plan of the request deletes: the records it names and the rows per table.
    work_orders: list[dict[str, Any]]
    part_numbers: list[dict[str, Any]]
    rows: dict[str, int]
    started_at: datetime.datetime | None
    backup_label: str | None
    pre_purge_backup: Backup | None
    finished_at: datetime.datetime | None
    cancelled_by: UserRef | None
    failure_reason: str | None


class PreviewResponse(BaseModel):
    scope: Scope
    work_orders: list[dict[str, Any]]
    part_numbers: list[dict[str, Any]]
    # Null when the deletion is refused (as the interim command's report).
    rows: dict[str, int] | None
    kept_audit_rows: list[dict[str, Any]]
    refusals: list[Refusal]
    open_request: RequestResponse | None


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_order_numbers: list[str] | None = None
    part_number: str | None = None
    reason: str
    # Every Work Order Number typed again, or the Part Number typed again.
    confirmation: list[str]


def _user(value: RequestUser | None) -> UserRef | None:
    return (
        None if value is None else UserRef(user_id=value.user_id, display_name=value.display_name)
    )


def _request_response(view: RequestView) -> RequestResponse:
    request = view.request
    backup = (
        None
        if request.pre_purge_backup_name is None or request.pre_purge_backup_started_at is None
        else Backup(
            name=request.pre_purge_backup_name,
            dump_started_at=request.pre_purge_backup_started_at,
        )
    )
    records = request.records or {}
    return RequestResponse(
        id=request.id,
        state=request.state,
        scope=Scope(
            work_order_numbers=list(request.work_order_numbers),
            part_numbers=list(request.part_numbers),
        ),
        reason=request.reason,
        requested_by=_user(view.requested_by),
        requested_by_operator=request.requested_by_operator,
        requested_at=request.requested_at,
        work_orders=list(records.get("work_orders", [])),
        part_numbers=list(records.get("part_numbers", [])),
        rows={str(table): int(count) for table, count in request.row_counts.items()},
        started_at=request.started_at,
        backup_label=request.backup_label,
        pre_purge_backup=backup,
        finished_at=request.finished_at,
        cancelled_by=_user(view.cancelled_by),
        failure_reason=request.failure_reason,
    )


def _answer(session: Session, request: DeletionRequest) -> RequestResponse:
    return _request_response(deletion_requests.request_view(session, request))


@router.get("/permanent-deletion/preview")
def get_preview(
    principal: PermanentDeletionDep,
    session: SessionDep,
    work_order_number: Annotated[list[str] | None, Query()] = None,
    part_number: Annotated[str | None, Query()] = None,
) -> PreviewResponse:
    result = deletion_requests.preview(
        session, work_order_numbers=work_order_number, part_number=part_number
    )
    plan = result.plan
    return PreviewResponse(
        scope=Scope(
            work_order_numbers=list(result.scope.work_order_numbers),
            part_numbers=list(result.scope.part_numbers),
        ),
        work_orders=plan.work_orders,
        part_numbers=plan.part_numbers,
        rows=None if plan.refusals else plan.counts(),
        kept_audit_rows=[] if plan.refusals else plan.kept_audit_rows,
        refusals=[Refusal(code=item.code, message=item.message) for item in plan.refusals],
        open_request=(
            None if result.open_request is None else _request_response(result.open_request)
        ),
    )


@router.get("/permanent-deletion/requests")
def get_requests(
    principal: PermanentDeletionDep,
    session: SessionDep,
    limit: Annotated[int, Query()] = deletion_requests.DEFAULT_REQUEST_LIMIT,
) -> list[RequestResponse]:
    return [
        _request_response(view) for view in deletion_requests.list_requests(session, limit=limit)
    ]


@router.post("/permanent-deletion/requests", status_code=201)
def post_request(
    principal: PermanentDeletionDep, body: RequestBody, session: SessionDep
) -> RequestResponse:
    request = deletion_requests.request_deletion(
        session,
        work_order_numbers=body.work_order_numbers,
        part_number=body.part_number,
        reason=body.reason,
        confirmation=body.confirmation,
        actor_user_id=principal.user_id,
    )
    return _answer(session, request)


@router.post("/permanent-deletion/requests/{request_id}/cancel")
def post_cancel(
    principal: PermanentDeletionDep, request_id: int, session: SessionDep
) -> RequestResponse:
    request = deletion_requests.cancel_request(session, request_id, actor_user_id=principal.user_id)
    return _answer(session, request)
