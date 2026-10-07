"""Translation of application-layer errors into HTTP responses.

One central mapping keeps the routes thin: services raise the typed
errors from ``app.application.errors`` and never think in HTTP terms,
while every response body stays in the standard FastAPI
``{"detail": ...}`` shape with the safe, user-facing message only.

The deliberate exceptions are the confirmation-required outcomes: the
active-quantity confirmation carries the PN's existing active
distribution — raised by the release (SLICE1_DATA_MODEL §8.2) and by
the Phase 10.5 Scan Station receipt, which never joins existing
quantity either (PROJECT_PROFILE §14) —, the Phase 5 route-deviation
confirmation (PROJECT_PROFILE §17) carries the deviation itself, and
the receipt also carries the internal blank-number MODIFY Work Orders
it refuses to choose between (§14), because the UI must show them
before the user can confirm the intent — still no internal detail,
only the data the confirmation dialog presents. The Phase 12 stale
Hot list change likewise carries the CURRENT Hot entries, rendered
exactly like a Hot list read, so the Priority view can show the list
the manager must review again, and the refused removal of a Hot demand
line (Phase 12 follow-up, OD3) carries the entry — PN and current rank —
the warning and the typed confirmation present. A Scan Station command
refused for want of a valid Worker Session (Phase 13) carries
``worker_session_required`` so the station raises the badge sign-in and
resends the unchanged request. The Phase 13 badge-confirmation gate
refusals carry ``badge_confirmation_required``,
``badge_confirmation_not_expected`` or ``badge_not_recognized``, so the
Scan Station can switch or re-open the final gate without losing the
operator's draft. The Undo reason refusal carries
``undo_reason_required``, so the Scan Station shows the required reason
field without losing the operator's draft. The Phase 14 sign-in
refusals carry one flag each (``authentication_required`` — which also
clears the session cookie —, ``permission_denied`` with
``required_permissions``, ``password_change_required``,
``sign_in_failed``, ``account_locked``, ``password_check_busy``,
``setup_closed``, ``setup_token_invalid``), so the client opens the
right dialog without parsing the message; a change refused because it
would leave no active User with a password who may manage users and
roles, or correction permissions, carries ``last_permission_holder``
(Phase 14 slice 2). Since slice 3 a refused Management read adds
``any_permission`` (any one of ``required_permissions`` opens it), and a
Management command replayed by another User carries
``recorded_by_another_user``.

Request-validation refusals (422) keep FastAPI's ``detail`` list but
only each error's ``type``, ``loc`` and ``msg``: the default body also
echoes the submitted ``input`` (and ``ctx``), which would return a
password or the setup token in clear text (Phase 14 slice 1).
"""

from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.authorization import clear_session_cookie
from app.api.hot_list import entry_response
from app.application.errors import (
    AccountLockedError,
    ActiveQuantityConfirmationRequiredError,
    ApplicationError,
    AuthenticationRequiredError,
    ConflictError,
    HotDemandRemovalConfirmationRequiredError,
    HotListChangedError,
    InvalidInputError,
    LastPermissionHolderError,
    NotFoundError,
    PasswordChangeRequiredError,
    PasswordCheckBusyError,
    PayloadTooLargeError,
    PermissionDeniedError,
    RecordedByAnotherUserError,
    RouteDeviationConfirmationRequiredError,
    SetupClosedError,
    SetupTokenInvalidError,
    SignInFailedError,
    UnsupportedMediaTypeError,
)
from app.application.intake import WorkOrderSelectionRequiredError
from app.application.station_identity import (
    BadgeConfirmationNotExpectedError,
    BadgeConfirmationRequiredError,
    BadgeNotRecognizedError,
    WorkerSessionRequiredError,
)
from app.application.undo import UndoReasonRequiredError

_STATUS_BY_ERROR: dict[type[ApplicationError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    InvalidInputError: 422,
    PayloadTooLargeError: 413,
    UnsupportedMediaTypeError: 415,
}


def _validation_detail(error: dict[str, Any]) -> dict[str, Any]:
    """One validation error without the submitted value or its context."""
    return {key: error[key] for key in ("type", "loc", "msg") if key in error}


def register_exception_handlers(app: FastAPI) -> None:
    """Register the application-error → HTTP status translation."""

    async def request_validation_handler(request: Request, exc: Exception) -> JSONResponse:
        errors = cast(RequestValidationError, exc).errors()
        return JSONResponse(
            status_code=422,
            content={"detail": [_validation_detail(dict(error)) for error in errors]},
        )

    app.add_exception_handler(RequestValidationError, request_validation_handler)

    def _register(error_type: type[ApplicationError], status_code: int) -> None:
        async def handler(request: Request, exc: Exception) -> JSONResponse:
            # FastAPI dispatches by the registered type, so exc is
            # always the matching ApplicationError subclass here.
            message = cast(ApplicationError, exc).message
            return JSONResponse(status_code=status_code, content={"detail": message})

        app.add_exception_handler(error_type, handler)

    for error_type, status_code in _STATUS_BY_ERROR.items():
        _register(error_type, status_code)

    async def confirmation_required_handler(request: Request, exc: Exception) -> JSONResponse:
        # Starlette resolves handlers along the exception's MRO, so the
        # exact class registered here wins over its ConflictError base.
        error = cast(ActiveQuantityConfirmationRequiredError, exc)
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.message,
                "confirmation_required": True,
                "existing_active_quantity": error.existing_active_quantity,
            },
        )

    app.add_exception_handler(
        ActiveQuantityConfirmationRequiredError, confirmation_required_handler
    )

    async def route_deviation_handler(request: Request, exc: Exception) -> JSONResponse:
        # Same shape family as the release confirmation: the body carries
        # only what the deviation confirmation dialog presents.
        error = cast(RouteDeviationConfirmationRequiredError, exc)
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.message,
                "confirmation_required": True,
                "route_deviation": error.route_deviation,
            },
        )

    app.add_exception_handler(RouteDeviationConfirmationRequiredError, route_deviation_handler)

    async def work_order_selection_handler(request: Request, exc: Exception) -> JSONResponse:
        # Phase 10.5 (PROJECT_PROFILE §14): several internal
        # blank-number MODIFY Work Orders could take a receipt, so the
        # station must present them for an explicit choice. The body
        # carries only what that selection dialog shows.
        error = cast(WorkOrderSelectionRequiredError, exc)
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.message,
                "selection_required": True,
                "work_orders": error.work_orders,
            },
        )

    app.add_exception_handler(WorkOrderSelectionRequiredError, work_order_selection_handler)

    async def hot_list_changed_handler(request: Request, exc: Exception) -> JSONResponse:
        # Phase 12: the optimistic precondition failed — nothing was
        # written; the body carries the current list to review.
        error = cast(HotListChangedError, exc)
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.message,
                "hot_list_changed": True,
                "entries": [
                    entry_response(entry).model_dump(mode="json") for entry in error.entries
                ],
            },
        )

    app.add_exception_handler(HotListChangedError, hot_list_changed_handler)

    async def hot_demand_removal_handler(request: Request, exc: Exception) -> JSONResponse:
        # Phase 12 follow-up (OD3): the demand line is on the Hot list and
        # otherwise removable — nothing was removed; the body carries the
        # entry the warning and the typed confirmation present.
        error = cast(HotDemandRemovalConfirmationRequiredError, exc)
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.message,
                "confirmation_required": True,
                "hot_list_entry": error.hot_list_entry,
            },
        )

    app.add_exception_handler(HotDemandRemovalConfirmationRequiredError, hot_demand_removal_handler)

    async def worker_session_required_handler(request: Request, exc: Exception) -> JSONResponse:
        # Phase 13: no valid Worker Session at a Scanned-session station —
        # nothing was recorded; the station asks for a badge sign-in.
        error = cast(WorkerSessionRequiredError, exc)
        return JSONResponse(
            status_code=409,
            content={"detail": error.message, "worker_session_required": True},
        )

    app.add_exception_handler(WorkerSessionRequiredError, worker_session_required_handler)

    # Phase 13 slice 5: the final-gate refusals — nothing was recorded;
    # the flag tells the station which gate form to present next. Each
    # exact class registered here wins over its base along the MRO.
    _gate_refusals: tuple[tuple[type[ApplicationError], int, str], ...] = (
        (BadgeConfirmationRequiredError, 409, "badge_confirmation_required"),
        (BadgeConfirmationNotExpectedError, 409, "badge_confirmation_not_expected"),
        (BadgeNotRecognizedError, 422, "badge_not_recognized"),
        # Phase 13 slice 6: the Undo reason policy is on and the reversal
        # carries no reason — nothing was reversed.
        (UndoReasonRequiredError, 409, "undo_reason_required"),
    )

    def _register_gate_refusal(
        error_type: type[ApplicationError], status_code: int, flag: str
    ) -> None:
        async def handler(request: Request, exc: Exception) -> JSONResponse:
            message = cast(ApplicationError, exc).message
            return JSONResponse(status_code=status_code, content={"detail": message, flag: True})

        app.add_exception_handler(error_type, handler)

    for error_type, status_code, flag in _gate_refusals:
        _register_gate_refusal(error_type, status_code, flag)

    # Phase 14 slice 1: the sign-in refusals — nothing was written unless
    # the refusal is a counted failed attempt; the flag tells the client
    # which dialog to present (sign in, change password, retry later).
    _sign_in_refusals: tuple[tuple[type[ApplicationError], int, str], ...] = (
        (PasswordChangeRequiredError, 403, "password_change_required"),
        (SignInFailedError, 401, "sign_in_failed"),
        (AccountLockedError, 409, "account_locked"),
        (PasswordCheckBusyError, 503, "password_check_busy"),
        (SetupClosedError, 409, "setup_closed"),
        (SetupTokenInvalidError, 403, "setup_token_invalid"),
        # Phase 14 slice 2: the last-holder rule — nothing was written.
        (LastPermissionHolderError, 409, "last_permission_holder"),
        # Phase 14 slice 3: another User recorded this device_event_id.
        (RecordedByAnotherUserError, 409, "recorded_by_another_user"),
    )
    for error_type, status_code, flag in _sign_in_refusals:
        _register_gate_refusal(error_type, status_code, flag)

    async def authentication_required_handler(request: Request, exc: Exception) -> JSONResponse:
        # No usable sign-in: the stale cookie (if any) is cleared too.
        response = JSONResponse(
            status_code=401,
            content={
                "detail": cast(ApplicationError, exc).message,
                "authentication_required": True,
            },
        )
        clear_session_cookie(response)
        return response

    app.add_exception_handler(AuthenticationRequiredError, authentication_required_handler)

    async def permission_denied_handler(request: Request, exc: Exception) -> JSONResponse:
        error = cast(PermissionDeniedError, exc)
        content: dict[str, Any] = {
            "detail": error.message,
            "permission_denied": True,
            "required_permissions": list(error.required),
        }
        if error.any_of:
            content["any_permission"] = True
        return JSONResponse(status_code=403, content=content)

    app.add_exception_handler(PermissionDeniedError, permission_denied_handler)
