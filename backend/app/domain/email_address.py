"""Canonical email address rule (Phase 17, owner decision of 2026-11-13).

An email address is optional on a User — where PartFlow sends a one-time
sign-in code when the User's other sign-in methods are lost
(IMPLEMENTATION_ROADMAP Phase 17 item 8) — and required as the sender
address of the outgoing mail server (Administration → Settings). The
value is trimmed of surrounding whitespace and must then be a plain ASCII
address ``local@domain``:

- at most 254 characters in all (the SMTP path limit of RFC 5321);
- the local part: 1-64 characters from letters, digits and
  ``! # $ % & ' * + / = ? ^ _ ` { | } ~ . -``, never starting or ending
  with a dot and never holding two dots in a row (no quoted local part);
  its letter case is kept;
- the domain: two or more dot-separated labels of 1-63 letters, digits
  and hyphens, a label never starting or ending with a hyphen; it is
  lowercased, since a domain is case-insensitive.

ASCII is checked before anything else, so the stored value never depends
on a Unicode version; internationalized addresses are refused. This
module owns that one rule and is deliberately framework-independent; the
database CHECKs repeat the canonical shape under the "C" collation.
"""

import re
from typing import Final

#: Most characters a canonical address may have; the database CHECKs repeat it.
MAX_EMAIL_ADDRESS_LENGTH: Final = 254
_MAX_LOCAL_PART_LENGTH: Final = 64

INVALID_EMAIL_ADDRESS_MESSAGE: Final = (
    "Enter an email address such as name@example.com: no spaces, a domain with a dot after"
    f" the @, and at most {MAX_EMAIL_ADDRESS_LENGTH} characters."
)

_ATOM: Final = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LOCAL_PART: Final = re.compile(rf"{_ATOM}(?:\.{_ATOM})*")
_DOMAIN_LABEL: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class InvalidEmailAddressError(ValueError):
    """Raised when an input value cannot be a canonical email address."""


def normalize_email_address(raw: str) -> str:
    """Return the canonical address for ``raw`` or raise ``InvalidEmailAddressError``.

    Surrounding whitespace is trimmed and the domain lowercased:
    `` Ann@Example.COM `` is stored as ``Ann@example.com``.
    """
    stripped = raw.strip()
    if not stripped or not stripped.isascii() or len(stripped) > MAX_EMAIL_ADDRESS_LENGTH:
        raise InvalidEmailAddressError(INVALID_EMAIL_ADDRESS_MESSAGE)
    local, at, domain = stripped.rpartition("@")
    if not at or len(local) > _MAX_LOCAL_PART_LENGTH or _LOCAL_PART.fullmatch(local) is None:
        raise InvalidEmailAddressError(INVALID_EMAIL_ADDRESS_MESSAGE)
    labels = domain.lower().split(".")
    if len(labels) < 2 or any(_DOMAIN_LABEL.fullmatch(label) is None for label in labels):
        raise InvalidEmailAddressError(INVALID_EMAIL_ADDRESS_MESSAGE)
    return f"{local}@{'.'.join(labels)}"
