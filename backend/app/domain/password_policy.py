"""Password rule for application Users (Phase 14 slice 1).

A User signs in with a login name and a password (owner decision OD-P1:
local accounts). A new password must hold 12 to 256 characters, counted
on its Unicode NFKC form (PLAN OD-P3 default, slice 1 decision OD-S1-9):
no composition rules, no banned list, and nothing is stripped — a space
is a character like any other. The same NFKC form is what gets hashed,
so a password typed with full-width characters verifies against its
ASCII spelling. A password must also be valid text: a lone UTF-16
surrogate (which JSON can carry but UTF-8 cannot encode) can never be
hashed, so a new password holding one is refused and a sign-in with one
is refused like an over-long password. Sign-in never applies the minimum
(an older password stays valid). This module owns that one rule and is deliberately
framework-independent; hashing lives in
``app.application.password_hashing``.
"""

import unicodedata
from typing import Final

#: Fewest characters a new password may have (after NFKC).
MIN_PASSWORD_LENGTH: Final = 12
#: Most characters any password may have (after NFKC); a longer password
#: given at sign-in can match no stored hash.
MAX_PASSWORD_LENGTH: Final = 256

TOO_SHORT_MESSAGE: Final = f"A password must be at least {MIN_PASSWORD_LENGTH} characters long."
TOO_LONG_MESSAGE: Final = f"A password can be at most {MAX_PASSWORD_LENGTH} characters long."
INVALID_TEXT_MESSAGE: Final = "A password can contain only valid text characters."


class InvalidPasswordError(ValueError):
    """Raised when a new password does not satisfy the length or text rule."""


def normalize_password(raw: str) -> str:
    """The NFKC form of ``raw`` — what is measured and hashed; nothing stripped."""
    return unicodedata.normalize("NFKC", raw)


def is_over_long(raw: str) -> bool:
    """Whether ``raw`` exceeds the maximum length after normalization."""
    return len(normalize_password(raw)) > MAX_PASSWORD_LENGTH


def is_encodable(raw: str) -> bool:
    """Whether the normalized ``raw`` encodes to UTF-8 (no lone surrogate)."""
    try:
        normalize_password(raw).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def validate_new_password(raw: str) -> str:
    """Return the normalized new password or raise ``InvalidPasswordError``."""
    password = normalize_password(raw)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidPasswordError(TOO_SHORT_MESSAGE)
    if len(password) > MAX_PASSWORD_LENGTH:
        raise InvalidPasswordError(TOO_LONG_MESSAGE)
    if not is_encodable(password):
        raise InvalidPasswordError(INVALID_TEXT_MESSAGE)
    return password
