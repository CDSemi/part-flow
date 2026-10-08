"""Backend half of the production web-tier contract (Phase 16 slice 2).

The in-stack ``web`` (nginx) lists API routes by hand in
``frontend/nginx/templates/default.conf.template``: the five raw-body
upload/import routes get a 4 MiB body limit (every other route 1 MiB),
``POST /api/session`` the sign-in rate limit and the password routes the
password rate limit. These cases fail when a route that needs one of
those rules is added, moved or renamed, so the nginx lists can never go
stale silently. No database: the app is built and its routes inspected.
"""

import typing
from collections.abc import Iterator
from typing import Any

from fastapi.routing import APIRoute
from pydantic import BaseModel, SecretStr
from starlette.routing import BaseRoute

from app.api import uploads, work_order_import
from app.api.work_order_import_files import MAX_IMPORT_BYTES
from app.application.images import MAX_IMAGE_BYTES
from app.main import create_app

# `client_max_body_size 4m` of the upload/import locations in
# frontend/nginx/templates/default.conf.template.
WEB_UPLOAD_LIMIT_BYTES = 4 * 1024 * 1024

# The nginx 4 MiB location set.
_RAW_BODY_ROUTES = {
    ("PUT", "/api/workers/{worker_id}/avatar"),
    ("PUT", "/api/users/{user_id}/avatar"),
    ("PUT", "/api/part-numbers/image"),
    ("POST", "/api/work-orders/import/preview"),
    ("POST", "/api/work-orders/import"),
}
_SIGN_IN_ZONE = {("POST", "/api/session")}
_PASSWORD_ZONE = {
    ("PUT", "/api/session/password"),
    ("POST", "/api/setup/administrator"),
    ("PUT", "/api/users/{user_id}/password"),
}
# Not rate limited (OD-S2-6): a 10-character code of a 32-symbol alphabet
# (50 bits) that expires after 15 minutes cannot be guessed in its lifetime.
_UNLIMITED_SECRET_ROUTES = {("POST", "/api/scan-stations/{station_id}/device-activations")}


def _walk(routes: list[BaseRoute]) -> Iterator[BaseRoute]:
    """Every route, descending into included routers (FastAPI includes
    them lazily; ``original_router`` holds their own routes)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        else:
            yield route


def _api_routes() -> Iterator[tuple[str, APIRoute]]:
    for route in _walk(create_app().router.routes):
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                yield method, route


def _calls(route: APIRoute) -> list[Any]:
    calls: list[Any] = []
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        calls.append(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def _holds_secret(annotation: Any) -> bool:
    """Whether a type is, contains or is a model with a ``SecretStr`` field."""
    if annotation is SecretStr:
        return True
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return any(_holds_secret(field.annotation) for field in annotation.model_fields.values())
    # Optional/union members, Annotated metadata and container items.
    return any(_holds_secret(argument) for argument in typing.get_args(annotation))


def test_raw_body_routes_are_the_web_upload_set() -> None:
    """B-W1."""
    readers = {uploads.read_image_body, work_order_import.read_import_body}
    found = {
        (method, route.path)
        for method, route in _api_routes()
        if readers.intersection(_calls(route))
    }
    assert found == _RAW_BODY_ROUTES


def test_secret_request_bodies_are_the_rate_limited_set() -> None:
    """B-W2."""
    found = {
        (method, route.path)
        for method, route in _api_routes()
        if any(_holds_secret(param.field_info.annotation) for param in route.dependant.body_params)
    }
    assert found == _SIGN_IN_ZONE | _PASSWORD_ZONE | _UNLIMITED_SECRET_ROUTES


def test_app_body_limits_stay_below_the_web_limit() -> None:
    """B-W3: the app's own JSON 413 wins over web's on the upload routes."""
    assert MAX_IMAGE_BYTES < WEB_UPLOAD_LIMIT_BYTES
    assert MAX_IMPORT_BYTES < WEB_UPLOAD_LIMIT_BYTES
