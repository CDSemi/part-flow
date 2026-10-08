"""The backend write gate (Phase 16 slice 3; OD-16-06, OD-16-07).

``ReleaseGateMiddleware`` — a pure ASGI middleware, registered by
``app.main.create_app`` between ``NoStoreMiddleware`` (outside) and
``CsrfMiddleware`` (inside). Every unsafe request (``POST``, ``PUT``,
``PATCH``, ``DELETE``) on every path, unknown paths included, is checked
before routing, before CSRF and before any body read:

1. When ``ENFORCE_CLIENT_RELEASE`` is on, the header
   ``X-PartFlow-Release`` must equal ``RELEASE_TAG`` exactly; otherwise
   409 ``release_mismatch`` (a page of another release). Off in
   development and tests: no release check at all.
2. The schema must be ready (``ReadinessMonitor.current``, a cached
   observation at most 5 s old; a miss reads in the thread pool): a
   ``mismatch`` is 503 ``not_ready``; a readiness that cannot be
   determined is 503 ``not_ready`` too (fail closed).

A refusal writes nothing, takes no lock and is not audited; it logs one
INFO line without header values or query string. Safe methods are never
gated. Refusals carry ``Cache-Control: no-store``.
"""

import logging
from typing import Final

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.application.readiness import ReadinessMonitor

logger = logging.getLogger(__name__)

RELEASE_HEADER: Final = "x-partflow-release"
RELEASE_MISMATCH_MESSAGE: Final = (
    "PartFlow was updated while this page was open, so this request was refused and nothing"
    " was changed by it. Reload the page to continue. If an earlier attempt had no answer,"
    " check whether it was recorded before repeating it."
)
NOT_READY_MESSAGE: Final = (
    "PartFlow is not ready for changes: the database does not match this release. Nothing was"
    " changed. An administrator must complete or roll back the release."
)
READINESS_UNKNOWN_MESSAGE: Final = (
    "PartFlow cannot confirm that its database is ready for changes. Nothing was changed."
    " Try again in a moment."
)
_UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class ReleaseGate:
    """What the gate compares, fixed at startup (``app.state.release_gate``)."""

    def __init__(self, *, enforced_release: str | None, readiness: ReadinessMonitor) -> None:
        # None: enforcement off (no release check).
        self.enforced_release = enforced_release
        self.readiness = readiness


class ReleaseGateMiddleware:
    """Refuse an unsafe request from another release or against an unready schema."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        refusal = await self._refusal(scope)
        if refusal is None:
            await self.app(scope, receive, send)
            return
        status_code, code, content = refusal
        logger.info("release gate refused %s %s: %s", scope["method"], scope["path"], code)
        response = JSONResponse(
            status_code=status_code, content=content, headers={"Cache-Control": "no-store"}
        )
        await response(scope, receive, send)

    async def _refusal(self, scope: Scope) -> tuple[int, str, dict[str, object]] | None:
        gate = getattr(getattr(scope.get("app"), "state", None), "release_gate", None)
        if not isinstance(gate, ReleaseGate):
            # Started without its lifespan state: readiness cannot be confirmed.
            return 503, "not_ready", _not_ready(READINESS_UNKNOWN_MESSAGE)
        if (
            gate.enforced_release is not None
            and Headers(scope=scope).get(RELEASE_HEADER) != gate.enforced_release
        ):
            mismatch: dict[str, object] = {
                "detail": RELEASE_MISMATCH_MESSAGE,
                "release_mismatch": True,
            }
            return 409, "release_mismatch", mismatch
        readiness = gate.readiness.fresh()
        if readiness is None:
            # A cache miss reads the database: off the event loop.
            readiness = await run_in_threadpool(gate.readiness.current)
        if readiness.ready:
            return None
        if readiness.state == "mismatch":
            return 503, "not_ready", _not_ready(NOT_READY_MESSAGE)
        return 503, "not_ready", _not_ready(READINESS_UNKNOWN_MESSAGE)


def _not_ready(message: str) -> dict[str, object]:
    return {"detail": message, "not_ready": True}
