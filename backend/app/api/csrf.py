"""Transport rules of the User session cookie (Phase 14 slice 1).

Two pure ASGI middlewares, registered by ``app.main.create_app``:

- ``CsrfMiddleware`` — a state-changing request (``POST``, ``PUT``,
  ``PATCH``, ``DELETE``) that carries the ``partflow_session`` cookie,
  and every such request to ``/api/session`` or
  ``/api/setup/administrator``, must carry the header
  ``X-PartFlow-CSRF: 1``; otherwise it is answered 403 at once — no
  handler runs and nothing is written. A browser sends a custom header
  cross-origin only after a CORS preflight, and PartFlow installs no CORS
  middleware, so no other site can obtain one. The body is never read.
  Safe methods and anonymous requests without the cookie are untouched,
  so every existing caller keeps working unchanged. A later station
  device token travels in its own header and is unaffected. The refusal
  is recorded as ``csrf_rejected`` in the request's access record
  (Phase 16 slice 6, ``app.api.request_log``).
- ``NoStoreMiddleware`` — every ``/api/session*`` and ``/api/setup*``
  response, refusals included, carries ``Cache-Control: no-store``.
"""

from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.application.authentication import SESSION_COOKIE
from app.core import log_context

CSRF_HEADER: Final = "x-partflow-csrf"
CSRF_REJECTED_MESSAGE: Final = (
    "This request was refused because it did not come from the PartFlow application."
    " Reload the page and try again."
)
_UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Sign-in and first-run setup create a session without presenting one.
_ALWAYS_CHECKED_PATHS: Final = frozenset({"/api/session", "/api/setup/administrator"})
_NO_STORE_PREFIXES: Final = ("/api/session", "/api/setup")


class CsrfMiddleware:
    """Refuse a state-changing cookie request without the PartFlow header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in _UNSAFE_METHODS:
            headers = Headers(scope=scope)
            carries_cookie = SESSION_COOKIE in cookie_parser(headers.get("cookie", ""))
            if (carries_cookie or scope["path"] in _ALWAYS_CHECKED_PATHS) and headers.get(
                CSRF_HEADER
            ) != "1":
                log_context.record_refusal("CsrfRejected", "csrf_rejected", None)
                response = JSONResponse(
                    status_code=403,
                    content={"detail": CSRF_REJECTED_MESSAGE, "csrf_rejected": True},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class NoStoreMiddleware:
    """Mark every session and setup response as never cacheable."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope["path"]).startswith(_NO_STORE_PREFIXES):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_no_store)
