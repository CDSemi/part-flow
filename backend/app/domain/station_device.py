"""Scan Station device enrollment values (Phase 14 slice 4; owner decision OD-P6).

An administrator enrolls a station device with a one-time **enrollment
code**: 10 characters of Crockford base32 (about 50 bits), shown once as
``ABCDE-FGHJK``, valid 15 minutes, single use, bound to one Scan
Station. The station browser types it in and exchanges it for a device
token it keeps. A code is matched case-, space- and hyphen-insensitively
with the Crockford decoding aliases (``O`` → ``0``, ``I`` and ``L`` →
``1``), so a code read aloud or retyped at a shop-floor terminal still
matches.

A device carries an administrator-given name (``label``): trimmed, 1–80
characters, no control character.

This module owns those rules and is deliberately framework-independent.
A device authenticates a terminal for one Scan Station — never a person:
Workers are never Users and a badge never authorizes.
"""

import unicodedata
from typing import Final

#: Crockford base32: digits and uppercase letters without I, L, O and U.
ENROLLMENT_CODE_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ENROLLMENT_CODE_LENGTH: Final = 10
DEVICE_LABEL_MAX: Final = 80

INVALID_DEVICE_LABEL_MESSAGE: Final = "Enter a device name of 1 to 80 characters."

_ALIASES: Final = str.maketrans({"O": "0", "I": "1", "L": "1"})
_ALPHABET: Final = frozenset(ENROLLMENT_CODE_ALPHABET)
_GROUP: Final = ENROLLMENT_CODE_LENGTH // 2


class InvalidDeviceLabelError(ValueError):
    """Raised when an input value cannot be a device name."""


def normalize_enrollment_code(raw: object) -> str | None:
    """The canonical enrollment code for ``raw``, or ``None`` when it cannot be one.

    Every whitespace character and ``-`` are removed, the rest is
    uppercased and the Crockford aliases are decoded; the result must be
    exactly 10 characters of the alphabet. A value that is not a string
    is never a code.
    """
    if not isinstance(raw, str):
        return None
    compact = "".join(
        character for character in raw if not character.isspace() and character != "-"
    )
    canonical = compact.upper().translate(_ALIASES)
    if len(canonical) != ENROLLMENT_CODE_LENGTH or not set(canonical) <= _ALPHABET:
        return None
    return canonical


def format_enrollment_code(code: str) -> str:
    """A canonical code as it is shown once: ``ABCDE-FGHJK``."""
    return f"{code[:_GROUP]}-{code[_GROUP:]}"


def normalize_device_label(raw: object) -> str:
    """The trimmed device name, or ``InvalidDeviceLabelError``.

    1–80 characters after trimming, none of them a control character
    (NUL included).
    """
    if not isinstance(raw, str):
        raise InvalidDeviceLabelError(INVALID_DEVICE_LABEL_MESSAGE)
    label = raw.strip()
    if not 1 <= len(label) <= DEVICE_LABEL_MAX or any(
        unicodedata.category(character) == "Cc" for character in label
    ):
        raise InvalidDeviceLabelError(INVALID_DEVICE_LABEL_MESSAGE)
    return label
