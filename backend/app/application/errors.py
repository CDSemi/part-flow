"""Application-layer error vocabulary.

Typed outcomes the API layer translates into HTTP responses. Every
message is written for the administrator who sees it: it states what
was rejected and why, and never carries driver errors, SQL, or any
other internal detail.
"""


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
