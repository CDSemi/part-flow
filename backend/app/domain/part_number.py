"""Canonical Part Number identity rules (PROJECT_PROFILE §7 Part Number).

The canonical PN string itself is the stable domain identity: production
records carry it by value and never reference the optional PartNumber
master through a surrogate key. This module owns the normalization that
produces that canonical form and is deliberately framework-independent.
"""


class InvalidPartNumberError(ValueError):
    """Raised when an input value cannot be a canonical Part Number."""


def normalize_part_number(raw: str) -> str:
    """Return the canonical PN for ``raw`` or raise ``InvalidPartNumberError``.

    Canonical form rules (PROJECT_PROFILE §7):

    - leading/trailing whitespace (input chrome) is trimmed;
    - after trimming the value must be non-empty;
    - internal whitespace of any kind is rejected — never silently
      removed to turn invalid input into a valid PN;
    - the result is normalized to UPPERCASE for storage and comparison.

    Beyond these rules the PN stays an opaque arbitrary string: no format
    is assumed and PN segments are never parsed for business meaning.
    """
    trimmed = raw.strip()
    if not trimmed:
        raise InvalidPartNumberError("Part Number must not be empty.")
    if any(character.isspace() for character in trimmed):
        raise InvalidPartNumberError("Part Number must not contain internal whitespace.")
    return trimmed.upper()
