"""Permanent deletion requests in PartFlow (Phase 16, history archival design D17 and §6.3).

The application side of Delete permanently… in Work Order Details and the
Part Number dialog: what a deletion would delete (the preview), the request,
its cancellation and the list of requests. Nothing here deletes anything:
the server records a request with the exact rows its plan deletes
(``permanent_deletion.record_request``), and the host job carries it out as
the maintenance database role (``history_archive_job``: ``start-deletion``,
the pre-purge backup, ``delete``), under row guards that admit exactly those
rows. The application role holds no delete privilege on any append-only
table.

- The scope is one or more Work Orders, named by their Work Order Numbers
  exactly as stored, or one Part Number (canonicalized as everywhere). An
  internal Work Order (no number) is deleted only with the Part Numbers of
  all its lines.
- The preview runs the plan of the interim command (``build_plan``) in its
  own ``REPEATABLE READ, READ ONLY`` transaction and never writes: what
  goes, every refusal, and the open request if one exists.
- A request needs a reason (1 to 500 characters of plain text) and the
  typed confirmation: every Work Order Number of the scope typed again
  exactly, or the Part Number typed again; the confirmation is checked and
  never stored. The plan is computed again in one ``REPEATABLE READ``
  transaction that records the request: a refusal answers 409 with every
  refusal and records nothing; at most one request is open (APPROVED or
  RUNNING), also when two requests race.
- A holder of the key may cancel an APPROVED request; a RUNNING request is
  the host job's, and a finished request never changes.

Every change of state appends one ``DeletionRequest`` audit row in the same
transaction (``actor_user_id`` the signed-in User, ``metadata.source``
``web``).
"""

import datetime
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, NamedTuple

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.application import permanent_deletion, user_access
from app.application.common import commit, flush, is_bindable_id
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.permanent_deletion import Plan
from app.application.policies import maintenance_reason
from app.domain.enums import DeletionRequestState
from app.infrastructure.models import DELETION_REQUEST_OPEN_STATES, DeletionRequest

DEFAULT_REQUEST_LIMIT: Final = 50
#: At most this many Work Orders in one request.
MAX_WORK_ORDERS: Final = 50

_OPEN_INDEX: Final = "uq_deletion_requests_open"
_SCOPE_MESSAGE: Final = (
    "Name what to delete: one or more Work Order Numbers, or one Part Number, not both."
)
_TOO_MANY_MESSAGE: Final = f"Name at most {MAX_WORK_ORDERS} Work Orders in one deletion request."
_BLANK_NUMBER_MESSAGE: Final = "A Work Order Number must not be blank or contain a NUL character."
_REASON_MESSAGE: Final = "Enter the reason for this deletion: 1 to 500 characters of plain text."
_WORK_ORDER_CONFIRMATION_MESSAGE: Final = (
    "Type the Work Order Number of every Work Order to delete, exactly as PartFlow shows it, to"
    " confirm the deletion. Nothing was requested."
)
_PART_NUMBER_CONFIRMATION_MESSAGE: Final = (
    "Type the Part Number to delete again to confirm the deletion. Nothing was requested."
)
_OPEN_MESSAGE: Final = (
    "Another deletion request is still open. Cancel it or wait until it finishes before"
    " requesting another deletion."
)


class Scope(NamedTuple):
    """What a request names: Work Order Numbers exactly as stored, canonical PNs."""

    work_order_numbers: tuple[str, ...]
    part_numbers: tuple[str, ...]


def scope_of(work_order_numbers: Sequence[object] | None, part_number: object | None) -> Scope:
    """The validated scope: one or more Work Order Numbers, or one Part Number."""
    numbers = list(work_order_numbers or [])
    if bool(numbers) == (part_number is not None):
        raise InvalidInputError(_SCOPE_MESSAGE)
    if part_number is not None:
        return Scope((), (permanent_deletion.requested_part_number(part_number),))
    if len(numbers) > MAX_WORK_ORDERS:
        raise InvalidInputError(_TOO_MANY_MESSAGE)
    for number in numbers:
        if not isinstance(number, str) or not number.strip() or "\x00" in number:
            raise InvalidInputError(_BLANK_NUMBER_MESSAGE)
    return Scope(tuple(dict.fromkeys(str(number) for number in numbers)), ())


def _check_confirmation(scope: Scope, confirmation: Sequence[object]) -> None:
    """The typed confirmation: every Work Order Number exactly, or the Part Number again."""
    if scope.part_numbers:
        typed = [value for value in confirmation if isinstance(value, str)]
        try:
            matches = len(typed) == len(confirmation) == 1 and (
                permanent_deletion.requested_part_number(typed[0]) == scope.part_numbers[0]
            )
        except InvalidInputError:
            matches = False
        if not matches:
            raise InvalidInputError(_PART_NUMBER_CONFIRMATION_MESSAGE)
        return
    if not all(isinstance(value, str) for value in confirmation) or set(confirmation) != set(
        scope.work_order_numbers
    ):
        raise InvalidInputError(_WORK_ORDER_CONFIRMATION_MESSAGE)


def _deletion(scope: Scope, reason: str = "") -> permanent_deletion.DeletionRequest:
    return permanent_deletion.DeletionRequest(
        work_order_numbers=scope.work_order_numbers,
        part_numbers=scope.part_numbers,
        operator="",
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class RequestUser(NamedTuple):
    user_id: int
    display_name: str


@dataclass(frozen=True)
class RequestView:
    request: DeletionRequest
    requested_by: RequestUser | None
    cancelled_by: RequestUser | None


def views(session: Session, requests: Sequence[DeletionRequest]) -> list[RequestView]:
    ids = {
        user_id
        for request in requests
        for user_id in (request.requested_by_user_id, request.cancelled_by_user_id)
        if user_id is not None
    }
    users = {
        user_id: RequestUser(ref.id, ref.display_name)
        for user_id, ref in user_access.user_refs(session, ids).items()
    }

    def user(user_id: int | None) -> RequestUser | None:
        if user_id is None:
            return None
        return users.get(user_id, RequestUser(user_id, f"user {user_id}"))

    return [
        RequestView(request, user(request.requested_by_user_id), user(request.cancelled_by_user_id))
        for request in requests
    ]


def request_view(session: Session, request: DeletionRequest) -> RequestView:
    return views(session, [request])[0]


def list_requests(session: Session, *, limit: int = DEFAULT_REQUEST_LIMIT) -> list[RequestView]:
    """The newest requests first."""
    if not 1 <= limit <= 500:
        raise InvalidInputError("limit must be a whole number from 1 to 500.")
    requests = list(
        session.scalars(select(DeletionRequest).order_by(DeletionRequest.id.desc()).limit(limit))
    )
    return views(session, requests)


def _open_request(session: Session) -> DeletionRequest | None:
    return session.scalar(
        select(DeletionRequest)
        .where(DeletionRequest.state.in_(DELETION_REQUEST_OPEN_STATES))
        .order_by(DeletionRequest.id)
        .limit(1)
    )


# ---------------------------------------------------------------------------
# Preview, request, cancel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Preview:
    scope: Scope
    plan: Plan
    #: The open request, if one exists: a request now would be refused.
    open_request: RequestView | None


def preview(
    session: Session, *, work_order_numbers: Sequence[object] | None, part_number: object | None
) -> Preview:
    """What a deletion of this scope would delete, and every refusal (reads only)."""
    scope = scope_of(work_order_numbers, part_number)
    with Session(session.get_bind()) as reader:
        reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        try:
            plan = permanent_deletion.build_plan(reader, _deletion(scope))
        finally:
            reader.rollback()
    existing = _open_request(session)
    return Preview(scope, plan, None if existing is None else request_view(session, existing))


def request_deletion(
    session: Session,
    *,
    work_order_numbers: Sequence[object] | None,
    part_number: object | None,
    reason: object,
    confirmation: Sequence[object],
    actor_user_id: int,
) -> DeletionRequest:
    """Record a deletion request (APPROVED) with every row its plan deletes.

    Refused while another request is open (also in a race), with every
    refusal of the plan (``DeletionRefusedError``), for a missing or wrong
    typed confirmation and for a reason that is not 1 to 500 characters of
    plain text; nothing is recorded then. The host job carries it out.
    """
    scope = scope_of(work_order_numbers, part_number)
    clean_reason = maintenance_reason(reason, _REASON_MESSAGE)
    _check_confirmation(scope, confirmation)
    with Session(session.get_bind()) as writer:
        writer.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        existing = permanent_deletion.open_request(writer)
        if existing is not None:
            raise ConflictError(permanent_deletion.open_request_message(existing))
        plan = permanent_deletion.build_plan(writer, _deletion(scope, clean_reason))
        permanent_deletion.refuse_if_refused(plan)
        recorded = permanent_deletion.record_request(
            writer,
            plan,
            work_order_numbers=scope.work_order_numbers,
            part_numbers=scope.part_numbers,
            reason=clean_reason,
            requested_by_user_id=actor_user_id,
            requested_by_operator=None,
            source="web",
        )
        request_id = recorded.id
        commit(writer, {_OPEN_INDEX: _OPEN_MESSAGE})
    return session.get_one(DeletionRequest, request_id, populate_existing=True)


def _locked_request(session: Session, request_id: int) -> DeletionRequest:
    request = (
        session.get(DeletionRequest, request_id, with_for_update=True, populate_existing=True)
        if is_bindable_id(request_id)
        else None
    )
    if request is None:
        raise NotFoundError(f"There is no deletion request {request_id}.")
    return request


def cancel_request(session: Session, request_id: int, *, actor_user_id: int) -> DeletionRequest:
    """Cancel an APPROVED request; cancelling a cancelled request changes nothing.

    A RUNNING request is the host job's, and a finished request never changes.
    """
    request = _locked_request(session, request_id)
    if request.state == DeletionRequestState.CANCELLED:
        return request
    if request.state == DeletionRequestState.RUNNING:
        raise ConflictError(
            f"Deletion request {request.id} is being carried out on the server now and cannot be"
            " cancelled. Wait until it finishes."
        )
    if request.state != DeletionRequestState.APPROVED:
        raise ConflictError(
            f"Deletion request {request.id} is finished ({request.state}) and cannot be cancelled."
        )
    before = permanent_deletion.request_values(request)
    request.state = DeletionRequestState.CANCELLED
    request.finished_at = datetime.datetime.now(datetime.UTC)
    request.cancelled_by_user_id = actor_user_id
    flush(session, {})
    permanent_deletion.audit_request(
        session,
        before,
        permanent_deletion.request_values(request),
        command="cancel",
        source="web",
        actor_user_id=actor_user_id,
    )
    commit(session, {})
    return request
