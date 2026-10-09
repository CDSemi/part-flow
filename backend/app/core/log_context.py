"""The log context of one HTTP request (Phase 16 slice 6; PLAN CD10).

Framework-free (no FastAPI, Starlette or SQLAlchemy import), so the API
layer and Application code may both add to it. ``app.api.request_log``
starts one mutable :class:`RequestLogContext` per request and writes it
into the request's one access record; ``app.core.structured_logging``
adds its ``request_id`` to every record emitted inside the request.

Sync routes and dependencies run in a worker thread with a *copy* of the
request's ``contextvars`` context, so they mutate the same object (a
``ContextVar.set`` there would be lost). The variable is set once per
request and never reset: every request runs in its own task with its own
context copy, and uvicorn's traceback record — written in that task after
the application returned — still carries the request id.

- ``bind(**fields)`` — only the keys of :data:`ALLOWED_FIELDS`; ``int``
  kept, ``str`` trimmed, control characters replaced by ``?`` and cut to
  128 characters, ``list[int]`` kept up to 20 items (``<key>_truncated``
  beyond), ``None`` removes the key; any other value or key raises
  ``ValueError`` (a programming error). A no-op outside a request.
- ``record_refusal(...)`` — the first refusal recorded wins.
- ``suppress_refusal_message()`` — the request's refusal message is never
  stored (routes whose refusal may echo a raw scanned value).
- ``mark_command()`` — the request is a production command.
"""

import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

#: Every key a request's log context may hold (the access record's ``context``).
ALLOWED_FIELDS: Final = (
    "station_id",
    "station_device_id",
    "user_id",
    "worker_id",
    "area_id",
    "part_number",
    "part_number_invalid",
    "quantity_flow_id",
    "quantity_flow_ids",
    "quantity_flow_ids_truncated",
    "quantity",
    "allocation_quantity",
    "source_area_id",
    "target_area_id",
    "starting_area_id",
    "operation_id",
    "machine_id",
    "work_order_id",
    "work_order_demand_id",
    "demand_id",
    "allocation_id",
    "route_template_id",
    "device_event_id",
    "reverses_device_event_id",
)
# Set only by the helpers below, never by ``bind``.
_FLAG_FIELDS: Final = frozenset({"part_number_invalid", "quantity_flow_ids_truncated"})
_ALLOWED: Final = frozenset(ALLOWED_FIELDS)

MAX_TEXT_LENGTH: Final = 128
MAX_LIST_ITEMS: Final = 20
MAX_MESSAGE_LENGTH: Final = 300

LogValue = int | str | bool | list[int] | None


def clean_text(value: str, limit: int) -> str:
    """Trimmed, every control character replaced by ``?``, cut to ``limit`` characters."""
    cleaned = "".join(
        "?" if unicodedata.category(character) == "Cc" else character for character in value.strip()
    )
    return cleaned[:limit]


@dataclass
class Refusal:
    """Why the request was refused (or failed), as the access record shows it."""

    type: str
    code: str | None
    message: str | None
    designed: bool
    errors: list[dict[str, object]] | None = None


class RequestLogContext:
    """The mutable log state of one request."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.fields: dict[str, LogValue] = {}
        self.refusal: Refusal | None = None
        self.command = False
        self.suppress_message = False


_current: ContextVar[RequestLogContext | None] = ContextVar(
    "partflow_request_log_context", default=None
)


def start(request_id: str) -> RequestLogContext:
    """A new context for the current request task (never reset, see the module docstring)."""
    context = RequestLogContext(request_id)
    _current.set(context)
    return context


def current() -> RequestLogContext | None:
    return _current.get()


def _value(key: str, value: object) -> LogValue:
    if isinstance(value, bool):
        raise ValueError(f"log context field {key} takes no boolean value")
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, str):
        return clean_text(value, MAX_TEXT_LENGTH)
    if isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        return list(value)
    raise ValueError(f"log context field {key} takes no {type(value).__name__} value")


def bind(**fields: object) -> None:
    """Add allowlisted fields to the request's context (a no-op outside a request)."""
    values: dict[str, LogValue] = {}
    for key, value in fields.items():
        if key not in _ALLOWED or key in _FLAG_FIELDS:
            raise ValueError(f"{key} is not a log context field")
        values[key] = _value(key, value)
    context = _current.get()
    if context is None:
        return
    for key, value in values.items():
        if value is None:
            context.fields.pop(key, None)
        elif isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
            context.fields[key] = value[:MAX_LIST_ITEMS]
            context.fields[f"{key}_truncated"] = True
        else:
            context.fields[key] = value


def bind_invalid_part_number() -> None:
    """The request's PN could not be normalized: ``part_number`` null, never the raw value."""
    context = _current.get()
    if context is None:
        return
    context.fields["part_number"] = None
    context.fields["part_number_invalid"] = True


def record_refusal(
    error_type: str,
    code: str | None,
    message: str | None,
    *,
    designed: bool = True,
    errors: list[dict[str, object]] | None = None,
) -> None:
    """Record why the request was refused; the first refusal recorded wins."""
    context = _current.get()
    if context is None or context.refusal is not None:
        return
    stored = None
    if message is not None and not context.suppress_message:
        stored = clean_text(message, MAX_MESSAGE_LENGTH)
    context.refusal = Refusal(
        type=clean_text(error_type, MAX_TEXT_LENGTH),
        code=code,
        message=stored,
        designed=designed,
        errors=errors,
    )


def suppress_refusal_message() -> None:
    """Never store this request's refusal message (it may echo a raw scanned value)."""
    context = _current.get()
    if context is not None:
        context.suppress_message = True
        if context.refusal is not None:
            context.refusal.message = None


def mark_command() -> None:
    """The request is a production command (outcome ``created``/``replayed``)."""
    context = _current.get()
    if context is not None:
        context.command = True
