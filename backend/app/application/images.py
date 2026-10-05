"""Database-stored image validation (Phase 13, CD1; owner decision OD-10).

The one validation every stored image passes — Worker avatars now, and
later the Part Number image and the User avatar: PNG, JPEG or WebP, at
most 2 MiB, and the declared media type must equal the type the bytes
themselves carry (magic-byte sniffing), so a stored type always
describes its bytes. Images are stored in PostgreSQL by the owning
service; this module holds no persistence and no business flow.

Audit rows never carry image bytes: :func:`image_digest` is the
snapshot an audit row records instead.
"""

import hashlib
from typing import Final

from app.application.errors import (
    InvalidInputError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)

#: Largest stored image (2 MiB); the database CHECKs repeat the bound.
MAX_IMAGE_BYTES: Final = 2 * 1024 * 1024

#: Accepted media types, each identified by its magic bytes.
ALLOWED_IMAGE_TYPES: Final = ("image/png", "image/jpeg", "image/webp")

IMAGE_TOO_LARGE_MESSAGE: Final = "The image is larger than 2 MB. Choose a smaller image."
UNSUPPORTED_IMAGE_MESSAGE: Final = "The file is not a PNG, JPEG or WebP image."

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE: Final = b"\xff\xd8\xff"


def sniff_image_type(data: bytes) -> str | None:
    """Return the accepted media type the bytes carry, or ``None``."""
    if data.startswith(_PNG_SIGNATURE):
        return "image/png"
    if data.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image(data: bytes, declared_type: str | None) -> str:
    """Validate an uploaded image and return its canonical media type.

    Checks run in this order: empty (422), larger than
    :data:`MAX_IMAGE_BYTES` (413), then the declared type — parameters
    dropped, lowercased — must be an accepted type AND equal the
    sniffed type (415). Nothing is written by this function.
    """
    if not data:
        raise InvalidInputError("The image is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise PayloadTooLargeError(IMAGE_TOO_LARGE_MESSAGE)
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_IMAGE_TYPES or declared != sniff_image_type(data):
        raise UnsupportedMediaTypeError(UNSUPPORTED_IMAGE_MESSAGE)
    return declared


def image_digest(data: bytes, content_type: str) -> dict[str, str | int]:
    """The audit snapshot of an image: type, size and SHA-256 — never bytes."""
    return {
        "content_type": content_type,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
