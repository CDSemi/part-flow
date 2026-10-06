"""Raw-body image upload transport (Phase 13, CD1).

Image uploads travel as the raw request body with the image's own
``Content-Type`` — no multipart form, no extra dependency. The body is
read here, bounded by the shared image limit: a declared
``Content-Length`` above the limit is refused before anything is read,
and a streamed (for example chunked) body is refused as soon as it
passes the limit, so an oversized upload is never buffered whole.
Content validation (empty, type, magic bytes) stays in
``app.application.images``.

Serving a stored image is shared too (:func:`stored_image_response`):
the Worker avatar and the Part Number image answer with the same
validator, caching policy and 304 handling.
"""

import datetime
from dataclasses import dataclass

from fastapi import Request, Response

from app.application.errors import PayloadTooLargeError
from app.application.images import IMAGE_TOO_LARGE_MESSAGE, MAX_IMAGE_BYTES


@dataclass(frozen=True)
class UploadedImage:
    """The uploaded bytes and the media type the client declared."""

    data: bytes
    content_type: str | None


async def read_image_body(request: Request) -> UploadedImage:
    """Read a bounded raw image body (``Depends`` target of upload routes)."""
    declared_length = request.headers.get("content-length")
    if (
        declared_length is not None
        and declared_length.isdigit()
        and int(declared_length) > MAX_IMAGE_BYTES
    ):
        raise PayloadTooLargeError(IMAGE_TOO_LARGE_MESSAGE)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_IMAGE_BYTES:
            raise PayloadTooLargeError(IMAGE_TOO_LARGE_MESSAGE)
    return UploadedImage(data=bytes(body), content_type=request.headers.get("content-type"))


def image_etag(updated_at: datetime.datetime) -> str:
    """Strong ETag from a stored image's version, in whole microseconds."""
    return f'"{int(updated_at.timestamp()) * 1_000_000 + updated_at.microsecond}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    candidates = {candidate.strip() for candidate in if_none_match.split(",")}
    # If-None-Match uses the weak comparison (RFC 9110 §13.1.2).
    return "*" in candidates or etag in candidates or f"W/{etag}" in candidates


def stored_image_response(
    data: bytes, content_type: str, updated_at: datetime.datetime, if_none_match: str | None
) -> Response:
    """Serve a database-stored image: 200 with its bytes, or 304.

    The ``ETag`` derives from the image's version and the client must
    revalidate (``Cache-Control: private, no-cache``); a matching
    ``If-None-Match`` answers 304, which repeats the validator and the
    caching policy (RFC 9110 §15.4.5).
    """
    etag = image_etag(updated_at)
    cache_headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=cache_headers)
    return Response(
        content=data,
        media_type=content_type,
        headers={**cache_headers, "X-Content-Type-Options": "nosniff"},
    )
