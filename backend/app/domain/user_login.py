"""Canonical User login name rule (Phase 13 slice 12, PROJECT_PROFILE §7 User).

A User is an application account — never a Worker. Its login name is the
account's unique name: an identifier, not a credential (no credential
exists before Phase 14). The value is trimmed of surrounding whitespace,
refused when it holds any non-ASCII character, lowercased, and must then
consist of 1-128 characters from ``a-z``, ``0-9`` and ``. _ @ + -``.
ASCII is checked BEFORE lowering, so a non-ASCII letter that lowers to
ASCII (the Kelvin sign U+212A lowers to ``k``) is refused, and the
stored set never depends on a Unicode version. This module owns that one
rule and is deliberately framework-independent; the database CHECK
repeats the canonical shape under the "C" collation.
"""

import re
from typing import Final

#: Most characters a canonical login name may have; the database CHECK
#: repeats the bound.
MAX_LOGIN_NAME_LENGTH: Final = 128

EMPTY_LOGIN_NAME_MESSAGE: Final = "Login name must not be empty."
INVALID_LOGIN_NAME_MESSAGE: Final = (
    "A login name may contain only letters (a–z), digits and . _ @ + -, with no spaces,"
    f" and at most {MAX_LOGIN_NAME_LENGTH} characters."
)

_CANONICAL_LOGIN_NAME: Final = re.compile(rf"[a-z0-9._@+-]{{1,{MAX_LOGIN_NAME_LENGTH}}}")


class InvalidLoginNameError(ValueError):
    """Raised when an input value cannot be a canonical login name."""


def normalize_login_name(raw: str) -> str:
    """Return the canonical login name for ``raw`` or raise ``InvalidLoginNameError``.

    Surrounding whitespace is trimmed; an empty result, any non-ASCII
    character, or a lowercased value outside ``[a-z0-9._@+-]{1,128}`` is
    refused. Letter case never matters: ``JDoe`` is stored as ``jdoe``.
    """
    stripped = raw.strip()
    if not stripped:
        raise InvalidLoginNameError(EMPTY_LOGIN_NAME_MESSAGE)
    if not stripped.isascii():
        raise InvalidLoginNameError(INVALID_LOGIN_NAME_MESSAGE)
    canonical = stripped.lower()
    if _CANONICAL_LOGIN_NAME.fullmatch(canonical) is None:
        raise InvalidLoginNameError(INVALID_LOGIN_NAME_MESSAGE)
    return canonical
