"""The HTTP request id and the backend's access record (Phase 16 slice 6; PLAN CD10).

``RequestLogMiddleware`` — a pure ASGI middleware, the outermost one
``app.main.create_app`` adds (Starlette's ``ServerErrorMiddleware`` stays
outside it): RequestLog → NoStore → ReleaseGate → Csrf → routes.

1. The request id is the request's ``X-Request-ID`` when it is 1-64
   characters of ``[A-Za-z0-9._-]``, otherwise a new ``uuid4().hex``; a
   non-conforming value is never logged, echoed or stored. Every response
   the middleware sends carries exactly one ``X-Request-ID`` header.
2. One ``app.core.log_context`` context per request; routes, dependencies
   and handlers add the domain context and the refusal to it.
3. Exactly one record on logger ``app.access`` per request, also for
   middleware refusals, 404s, 422s, failures and client disconnects.

Level (the first rule that matches): a health path → DEBUG whatever the
status; a designed refusal (recorded by a registered handler or the CSRF
and release-gate middlewares) → INFO; a failure (``internal_error``, or a
5xx nobody recorded) → ERROR; a ``GET``/``HEAD``/``OPTIONS`` below 400
answered in under one second → DEBUG; anything else → INFO (a read of one
second or more carries ``"slow": true``).

Never logged: request or response bodies, query strings, any header but
the conforming request id, cookies, form fields, uploaded files. ``path``
is logged only when no route matched; otherwise the route template.

Also ``bind_command``/``bind_result``, which the production command routes
call to put their PN, flow, quantity, Area, Operation, Machine and
``device_event_id`` into the record — never a badge, a scanned value or a
free-text reason.
"""

import logging
import re
import time
import uuid
from typing import Final

from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import log_context
from app.core.structured_logging import PAYLOAD_ATTRIBUTE
from app.domain.part_number import InvalidPartNumberError, normalize_part_number

REQUEST_ID_HEADER: Final = "x-request-id"
REQUEST_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,64}")
ACCESS_LOGGER: Final = "app.access"

#: The body fields a production command puts into its access record.
COMMAND_BODY_FIELDS: Final = (
    "part_number",
    "quantity_flow_id",
    "quantity_flow_ids",
    "quantity",
    "allocation_quantity",
    "source_area_id",
    "target_area_id",
    "starting_area_id",
    "operation_id",
    "machine_id",
    "work_order_id",
    "work_order_demand_id",
    "route_template_id",
    "device_event_id",
    "reverses_device_event_id",
)
#: The committed context a command result adds (when the result has it).
RESULT_FIELDS: Final = ("area_id", "operation_id", "machine_id")

_HEALTH_PATHS: Final = frozenset({"/api/health", "/api/health/live"})
_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})
_SLOW_MILLISECONDS: Final = 1000.0
_MAX_PATH_LENGTH: Final = 200
_CLIENT_CLOSED_REQUEST: Final = 499

logger = logging.getLogger(ACCESS_LOGGER)


def request_id_of(scope: Scope) -> str:
    """The client's conforming ``X-Request-ID``, else a new 32-hex id."""
    offered = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if offered is not None and REQUEST_ID_PATTERN.fullmatch(offered) is not None:
        return offered
    return uuid.uuid4().hex


def _route_template(scope: Scope) -> str | None:
    route = scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template if isinstance(template, str) else None


def _client(scope: Scope) -> str | None:
    client = scope.get("client")
    if client is None:
        return None
    return str(client[0])


def _outcome(
    context: log_context.RequestLogContext, status: int, refusal: log_context.Refusal | None
) -> str:
    if context.command and status == 201:
        return "created"
    if context.command and status == 200:
        return "replayed"
    if status < 400:
        return "ok"
    if refusal is not None and refusal.designed:
        return "refused"
    if status == _CLIENT_CLOSED_REQUEST:
        return "error"
    if status < 500:
        return "refused"
    return "error"


def _level(
    path: str,
    method: str,
    status: int,
    duration_ms: float,
    refusal: log_context.Refusal | None,
) -> int:
    if path in _HEALTH_PATHS:
        return logging.DEBUG
    if refusal is not None and refusal.designed:
        return logging.INFO
    if status >= 500 or (refusal is not None and refusal.code == "internal_error"):
        return logging.ERROR
    if method in _SAFE_METHODS and status < 400 and duration_ms < _SLOW_MILLISECONDS:
        return logging.DEBUG
    return logging.INFO


def _refusal_document(refusal: log_context.Refusal | None) -> dict[str, object] | None:
    if refusal is None:
        return None
    document: dict[str, object] = {
        "type": refusal.type,
        "code": refusal.code,
        "message": refusal.message,
    }
    if refusal.errors is not None:
        document["errors"] = refusal.errors
    return document


def _emit(
    scope: Scope, context: log_context.RequestLogContext, status: int, duration_ms: float
) -> None:
    method = str(scope["method"])
    path = str(scope["path"])
    refusal = context.refusal
    level = _level(path, method, status, duration_ms, refusal)
    if not logger.isEnabledFor(level):
        return
    route = _route_template(scope)
    logged_path = None if route is not None else log_context.clean_text(path, _MAX_PATH_LENGTH)
    duration = round(duration_ms, 1)
    payload: dict[str, object] = {
        "event": "request",
        "method": method,
        "route": route,
        "path": logged_path,
        "status": status,
        "duration_ms": duration,
        "client": _client(scope),
        "outcome": _outcome(context, status, refusal),
        "slow": method in _SAFE_METHODS and duration_ms >= _SLOW_MILLISECONDS,
        "refusal": _refusal_document(refusal),
        "context": dict(context.fields),
    }
    message = f"{method} {route or logged_path} {status} {duration:.1f} ms"
    logger.log(level, message, extra={PAYLOAD_ATTRIBUTE: payload})


class RequestLogMiddleware:
    """Give every request an id, echo it, and write its one access record."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_of(scope)
        context = log_context.start(request_id)
        started = time.perf_counter()
        status: int | None = None
        disconnected = False

        async def receive_tracked() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect" and status is None:
                disconnected = True
            return message

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive_tracked, send_with_id)
        except Exception as exc:
            if disconnected and status is None:
                status = _CLIENT_CLOSED_REQUEST
                log_context.record_refusal(
                    type(exc).__name__, "client_disconnected", None, designed=False
                )
            else:
                status = status or 500
                log_context.record_refusal(
                    type(exc).__name__, "internal_error", None, designed=False
                )
            _emit(scope, context, status, (time.perf_counter() - started) * 1000)
            # Starlette answers its unchanged 500 and uvicorn logs the traceback.
            raise
        if disconnected and (status is None or status >= 400):
            # The client left before any answer reached it.
            status = _CLIENT_CLOSED_REQUEST
            log_context.record_refusal(
                "ClientDisconnect", "client_disconnected", None, designed=False
            )
        elif status is None:
            status = 500
            log_context.record_refusal("NoResponse", "internal_error", None, designed=False)
        _emit(scope, context, status, (time.perf_counter() - started) * 1000)


def _bind_part_number(raw: object) -> None:
    if not isinstance(raw, str):
        return
    try:
        canonical = normalize_part_number(raw)
    except InvalidPartNumberError:
        log_context.bind_invalid_part_number()
        return
    log_context.bind(part_number=canonical)


def bind_command(body: BaseModel | None = None, **path_values: int | str) -> None:
    """Mark the request as a production command and bind its context.

    The path values given, and from ``body`` exactly the attributes in
    :data:`COMMAND_BODY_FIELDS` that its model has; ``part_number`` in its
    canonical form (``part_number_invalid`` when it has none — the raw
    value is never logged). Called first in each production command route.
    """
    log_context.mark_command()
    log_context.bind(**path_values)
    if body is None:
        return
    fields = type(body).model_fields
    for name in COMMAND_BODY_FIELDS:
        if name not in fields:
            continue
        value = getattr(body, name)
        if name == "part_number":
            _bind_part_number(value)
        else:
            log_context.bind(**{name: value})


def bind_result(result: object) -> None:
    """Bind the int-valued :data:`RESULT_FIELDS` the command's result has."""
    for name in RESULT_FIELDS:
        value = getattr(result, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            log_context.bind(**{name: value})
