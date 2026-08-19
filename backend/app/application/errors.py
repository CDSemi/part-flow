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
