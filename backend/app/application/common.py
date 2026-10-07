"""Shared Application-layer helpers.

Input normalization and the commit protocol used by every configuration
service: uniqueness is pre-checked for friendly messages, but the
database constraint stays the authority — a race lost at COMMIT maps
back to the same user-facing ``ConflictError`` through the constraint
name. These helpers carry no business rules of their own.
"""

import uuid
from typing import Final, NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.errors import ConflictError, InvalidInputError


class UnsetType:
    """Sentinel marking a partial-update field that was not provided."""

    __slots__ = ()


UNSET: Final = UnsetType()

# The largest id PostgreSQL can bind to an ``integer`` key. An id
# outside 1..MAX_ROW_ID names no row: answer it as missing before any
# query instead of failing in the driver.
MAX_ROW_ID: Final = 2_147_483_647


def is_bindable_id(value: int) -> bool:
    """Whether ``value`` can name a row of an ``integer``-keyed table."""
    return 0 < value <= MAX_ROW_ID


def required_text(value: object, label: str) -> str:
    """Normalize a required text field: strip and reject empty/None.

    PostgreSQL text cannot hold NUL (U+0000), so a value containing it
    is refused as input before it reaches any query.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{label} must not be empty.")
    if "\x00" in value:
        raise InvalidInputError(f"{label} must not contain a NUL character.")
    return value.strip()


def optional_text(value: str | None, label: str = "The text") -> str | None:
    """Normalize an optional text field: strip; empty becomes NULL.

    Like ``required_text``, a value containing NUL (U+0000) is refused
    as input: PostgreSQL text cannot hold it, and the driver would
    otherwise fail at flush with an unhandled DataError.
    """
    if value is None:
        return None
    if "\x00" in value:
        raise InvalidInputError(f"{label} must not contain a NUL character.")
    stripped = value.strip()
    return stripped or None


def required_flag(value: object, label: str) -> bool:
    """Reject an explicit ``null`` sent for a boolean field."""
    if not isinstance(value, bool):
        raise InvalidInputError(f"{label} must be true or false.")
    return value


def device_event_id_text(value: object) -> str:
    """Normalize the client-generated idempotency key of a production command.

    One UUID per submission, reused on every transport retry (SLICE1
    §14); canonical text form so the same id always compares equal.
    """
    if not isinstance(value, str):
        raise InvalidInputError("device_event_id must be text.")
    try:
        return str(uuid.UUID(value.strip()))
    except ValueError:
        raise InvalidInputError("device_event_id must be a UUID.") from None


def commit(session: Session, conflict_messages: dict[str, str]) -> None:
    """Commit, translating known constraint violations into ConflictError.

    Uniqueness is pre-checked for friendly messages, but a concurrent
    writer can still win the race — the database constraint is the
    authority, and its deterministic name maps back to the same
    user-facing message. Unknown integrity failures are re-raised: they
    indicate a programming error, not an expected outcome.
    """
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _raise_translated(exc, conflict_messages)


def flush(session: Session, conflict_messages: dict[str, str]) -> None:
    """Flush with the same constraint translation as ``commit``.

    A multi-step transaction (for example one demand save staging PN
    masters, demand rows, and audit rows) flushes between steps to
    obtain generated keys; a constraint race lost at such a flush must
    surface as the same user-facing ``ConflictError`` as one lost at
    COMMIT. The rollback discards the whole staged transaction — no
    partial state survives.
    """
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        _raise_translated(exc, conflict_messages)


def _raise_translated(exc: IntegrityError, conflict_messages: dict[str, str]) -> NoReturn:
    diagnostics = getattr(exc.orig, "diag", None)
    constraint = getattr(diagnostics, "constraint_name", None)
    message = conflict_messages.get(constraint or "")
    if message is None:
        raise exc
    raise ConflictError(message) from exc
