"""Translation of application-layer errors into HTTP responses.

One central mapping keeps the routes thin: services raise the typed
errors from ``app.application.errors`` and never think in HTTP terms,
while every response body stays in the standard FastAPI
``{"detail": ...}`` shape with the safe, user-facing message only.

The one deliberate exception is the release confirmation-required
outcome (SLICE1_DATA_MODEL §8.2): its 409 body additionally carries
the existing active distribution, because the UI must show it before
the user can confirm the intent — still no internal detail, only the
data the confirmation dialog presents.
"""

from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.application.errors import (
    ActiveQuantityConfirmationRequiredError,
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

    async def confirmation_required_handler(request: Request, exc: Exception) -> JSONResponse:
        # Starlette resolves handlers along the exception's MRO, so the
        # exact class registered here wins over its ConflictError base.
        error = cast(ActiveQuantityConfirmationRequiredError, exc)
        return JSONResponse(
            status_code=409,
            content={
                "detail": error.message,
                "confirmation_required": True,
                "existing_active_quantity": error.existing_active_quantity,
            },
        )

    app.add_exception_handler(
        ActiveQuantityConfirmationRequiredError, confirmation_required_handler
    )
