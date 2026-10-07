"""Application-layer error vocabulary.

Typed outcomes the API layer translates into HTTP responses. Every
message is written for the administrator who sees it: it states what
was rejected and why, and never carries driver errors, SQL, or any
other internal detail.
"""

from typing import Any


class ApplicationError(Exception):
    """Base class for expected, user-reportable application failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(ApplicationError):
    """The addressed resource does not exist."""


class ConflictError(ApplicationError):
    """The change conflicts with the current configuration state.

    Covers uniqueness violations, references to inactive entities, and
    lifecycle changes blocked by dependent state.
    """


class InvalidInputError(ApplicationError):
    """A submitted value is invalid beyond what schema validation covers.

    Also covers references to entities that do not exist: the request
    shape is fine, but the content cannot be processed.
    """


class PayloadTooLargeError(ApplicationError):
    """An uploaded payload exceeds its limit (413)."""


class UnsupportedMediaTypeError(ApplicationError):
    """An upload is not an accepted media type (415)."""


class IdempotencyConflictError(ConflictError):
    """A ``device_event_id`` was reused for a different normalized request.

    SLICE1_DATA_MODEL §14: the mismatch signals a client defect (an id
    wrongly reused for a new intent) and is never silently honored —
    nothing is created.
    """


class ActiveQuantityConfirmationRequiredError(ConflictError):
    """A release needs explicit confirmation of intent (SLICE1 §8.2).

    The PN already has ACTIVE QuantityFlows: the response carries the
    existing distribution so the UI can show it and resubmit with the
    explicit confirmation flag. Nothing is written until confirmed —
    release never auto-creates or auto-merges quantity.
    """

    def __init__(self, message: str, existing_active_quantity: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.existing_active_quantity = existing_active_quantity


class RouteDeviationConfirmationRequiredError(ConflictError):
    """A PLANNED transfer leaves its route and needs explicit confirmation.

    PROJECT_PROFILE §17 Route Deviation: the operator is warned and must
    confirm before the actual Movement is recorded. The response carries
    the deviation the confirmation dialog presents (the expected next
    step versus the station's Area); nothing is written until the
    request is resubmitted with the explicit confirmation flag.
    """

    def __init__(self, message: str, route_deviation: dict[str, Any]) -> None:
        super().__init__(message)
        self.route_deviation = route_deviation


class HotDemandRemovalConfirmationRequiredError(ConflictError):
    """Removing a Hot demand line needs explicit confirmation (OD3).

    The line passes every other removal rule but is on the Hot list:
    nothing is written, and the response carries the entry as it stands
    under the demand row lock (``work_order_demand_id``, ``part_number``,
    the CURRENT ``rank``) so the UI can warn and ask for the typed
    confirmation before resubmitting with the flag.
    """

    def __init__(self, message: str, hot_list_entry: dict[str, Any]) -> None:
        super().__init__(message)
        self.hot_list_entry = hot_list_entry


class HotListChangedError(ConflictError):
    """The Hot list changed since the manager confirmed (Phase 12).

    Every Hot list change carries the full ranked order the manager
    confirmed against as an optimistic precondition; when it no longer
    equals the current order nothing is written, and the response
    carries the CURRENT entries (``app.application.hot_list.HotEntry``,
    rendered exactly like a Hot list read) so the view can show them
    and the manager can decide again.
    """

    def __init__(self, message: str, entries: list[Any]) -> None:
        super().__init__(message)
        self.entries = entries


class AuthenticationRequiredError(ApplicationError):
    """No signed-in User, or the sign-in has ended (Phase 14, 401).

    The API adds ``{"authentication_required": true}`` and clears the
    session cookie, so the client opens the sign-in dialog.
    """


class PermissionDeniedError(ApplicationError):
    """The signed-in User's role lacks a required permission (403).

    ``required`` names the permission keys the route requires, as plain
    strings, so this vocabulary never depends on the permission enum.
    """

    def __init__(self, message: str, required: tuple[str, ...]) -> None:
        super().__init__(message)
        self.required = required


class PasswordChangeRequiredError(ApplicationError):
    """The signed-in User must first replace an administrator-set password (403)."""


class SignInFailedError(ApplicationError):
    """A sign-in was refused (401). One message for every reason, so the
    response never tells which part was wrong or whether the account
    exists, is inactive or is locked."""


class AccountLockedError(ConflictError):
    """The account is locked after too many failed attempts (409); the
    attempt is neither counted nor checked."""


class PasswordCheckBusyError(ApplicationError):
    """Too many password checks are running (503). A definite refusal:
    nothing was written or counted, and the request may be retried."""


class SetupClosedError(ConflictError):
    """First-run setup is closed: an administrator already exists (409)."""


class SetupTokenInvalidError(ApplicationError):
    """The first-run setup token is not the current one (403)."""


class UnknownLoginError(ApplicationError):
    """No User has the given login name (recovery command only; never HTTP)."""


class RecoveryUnavailableError(ApplicationError):
    """The recovery reset may not run for this User or in this state
    (recovery command only; never HTTP)."""
