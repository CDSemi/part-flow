"""Translation of application-layer errors into HTTP responses.

One central mapping keeps the routes thin: services raise the typed
errors from ``app.application.errors`` and never think in HTTP terms,
while every response body stays in the standard FastAPI
``{"detail": ...}`` shape with the safe, user-facing message only.
"""

from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.application.errors import (
    ApplicationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
)

_STATUS_BY_ERROR: dict[type[ApplicationError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    InvalidInputError: 422,
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register the application-error → HTTP status translation."""

    def _register(error_type: type[ApplicationError], status_code: int) -> None:
        async def handler(request: Request, exc: Exception) -> JSONResponse:
            # FastAPI dispatches by the registered type, so exc is
            # always the matching ApplicationError subclass here.
            message = cast(ApplicationError, exc).message
            return JSONResponse(status_code=status_code, content={"detail": message})

        app.add_exception_handler(error_type, handler)

    for error_type, status_code in _STATUS_BY_ERROR.items():
        _register(error_type, status_code)
