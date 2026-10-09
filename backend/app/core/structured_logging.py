"""JSON log records for the production backend (Phase 16 slice 6; PLAN CD10).

``JsonFormatter`` is selected by ``app/core/logging.production.json``
(uvicorn ``--log-config`` in the production image); development and tests
keep the text output of ``logging.basicConfig``.

Every record is one JSON object on one physical line, keys in a fixed
order: ``ts``, ``level``, ``logger``, ``message``, then ``request_id``
inside a request (``app.core.log_context``), then the keys of an
``extra={"partflow": {...}}`` payload in their order, then ``exc_type``
and ``traceback`` for a record with ``exc_info``.

Tracebacks are rendered here, never with an exception's database text: a
``sqlalchemy.exc.DBAPIError`` or ``psycopg.Error`` in the chain is shown as
``{module}.{class}: sqlstate=… constraint=… table=…``, because its message
holds the statement and the PostgreSQL ``DETAIL`` line, which prints the
conflicting key or the failing row (a badge, a login name, a token digest).
"""

import datetime
import json
import logging
import traceback
from typing import Final

import psycopg
from sqlalchemy.exc import DBAPIError

from app.core import log_context

#: The record attribute of the structured payload (``extra={"partflow": {...}}``).
PAYLOAD_ATTRIBUTE: Final = "partflow"

_CAUSE: Final = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT: Final = "\nDuring handling of the above exception, another exception occurred:\n\n"


def _timestamp(created: float) -> str:
    moment = datetime.datetime.fromtimestamp(created, datetime.UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _diagnostic(error: BaseException) -> tuple[str, str, str]:
    """``(sqlstate, constraint, table)`` of a database error; ``-`` when absent."""
    original = error.orig if isinstance(error, DBAPIError) else error
    diag = getattr(original, "diag", None)
    sqlstate = getattr(original, "sqlstate", None)
    constraint = getattr(diag, "constraint_name", None)
    table = getattr(diag, "table_name", None)
    return (
        str(sqlstate) if sqlstate else "-",
        str(constraint) if constraint else "-",
        str(table) if table else "-",
    )


def _safe_exception_line(error: BaseException) -> str:
    """The exception line of one chain link; never a database error's message."""
    if isinstance(error, DBAPIError | psycopg.Error):
        sqlstate, constraint, table = _diagnostic(error)
        kind = type(error)
        return (
            f"{kind.__module__}.{kind.__qualname__}: sqlstate={sqlstate}"
            f" constraint={constraint} table={table}\n"
        )
    return "".join(traceback.format_exception_only(type(error), error))


def _chain(error: BaseException) -> list[tuple[BaseException, str]]:
    """The exception chain, oldest first, each with the separator printed after it."""
    links: list[tuple[BaseException, str]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    separator = ""
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        links.append((current, separator))
        if current.__cause__ is not None:
            current, separator = current.__cause__, _CAUSE
        elif current.__context__ is not None and not current.__suppress_context__:
            current, separator = current.__context__, _CONTEXT
        else:
            current = None
    links.reverse()
    return links


def format_exception_safely(error: BaseException) -> str:
    """The traceback text of ``error``'s chain with database messages replaced."""
    parts: list[str] = []
    for link, separator in _chain(error):
        stack = traceback.TracebackException.from_exception(link).stack
        if stack:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(stack.format())
        parts.append(_safe_exception_line(link))
        parts.append(separator)
    return "".join(parts).rstrip("\n")


class JsonFormatter(logging.Formatter):
    """One JSON object per record, one physical line, keys in fixed order."""

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, object] = {
            "ts": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = log_context.current()
        if context is not None:
            document["request_id"] = context.request_id
        payload = getattr(record, PAYLOAD_ATTRIBUTE, None)
        if isinstance(payload, dict):
            for key, value in payload.items():
                document.setdefault(str(key), value)
        if record.exc_info and record.exc_info[1] is not None:
            document["exc_type"] = type(record.exc_info[1]).__name__
            document["traceback"] = self.formatException(record.exc_info)
        return json.dumps(document, ensure_ascii=True, separators=(",", ":"), default=str)

    def formatException(self, ei: object) -> str:
        error = ei[1] if isinstance(ei, tuple) and len(ei) == 3 else None
        if not isinstance(error, BaseException):
            return ""
        return format_exception_safely(error)
