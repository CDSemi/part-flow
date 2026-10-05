"""Raw-body image upload transport (Phase 13, CD1).

Image uploads travel as the raw request body with the image's own
``Content-Type`` — no multipart form, no extra dependency. The body is
read here, bounded by the shared image limit: a declared
``Content-Length`` above the limit is refused before anything is read,
and a streamed (for example chunked) body is refused as soon as it
passes the limit, so an oversized upload is never buffered whole.
Content validation (empty, type, magic bytes) stays in
``app.application.images``.
"""

from dataclasses import dataclass

from fastapi import Request

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
