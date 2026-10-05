"""Canonical Worker badge barcode rule (PROJECT_PROFILE §8.13, §10).

A Worker's badge barcode is the barcode already printed on the
company's employee badge — the one non-``PF:`` value the Scan Station
resolves. Badges are case-insensitive (owner decision OD-3,
2026-10-04): the value is canonicalized by trimming surrounding
whitespace and converting to UPPERCASE, both when it is stored and when
a scan is matched, so a scan matches exactly after canonicalization.
This module owns that one rule and is deliberately
framework-independent; no second badge normalization exists anywhere.
"""

from typing import Final

#: Most characters a canonical badge may have. Bounds the UNIQUE btree
#: key far below PostgreSQL's tuple limit (128 characters are at most
#: 512 UTF-8 bytes); the database CHECK repeats the bound.
MAX_BADGE_BARCODE_LENGTH: Final = 128

#: The PartFlow barcode namespace (PROJECT_PROFILE §10). A badge is the
#: one scanned value outside it, so a badge can never start with it.
_PARTFLOW_PREFIX: Final = "PF:"


class InvalidBadgeBarcodeError(ValueError):
    """Raised when an input value cannot be a canonical badge barcode."""


def normalize_badge_barcode(raw: str) -> str:
    """Return the canonical badge for ``raw`` or raise ``InvalidBadgeBarcodeError``.

    Surrounding whitespace (scanner CR/LF, padding) is trimmed, then the
    value is converted to UPPERCASE (``str.upper()``, the full Unicode
    mapping — ``ß`` becomes ``SS``). Internal characters are kept. The
    canonical value is rejected when it is empty, longer than
    :data:`MAX_BADGE_BARCODE_LENGTH` characters, or starts with ``PF:``
    (the PartFlow namespace). The same rule runs on save and on scan,
    so badges are case-insensitive (OD-3).
    """
    canonical = raw.strip().upper()
    if not canonical:
        raise InvalidBadgeBarcodeError("Badge barcode must not be empty.")
    if len(canonical) > MAX_BADGE_BARCODE_LENGTH:
        raise InvalidBadgeBarcodeError(
            f"A badge barcode must be at most {MAX_BADGE_BARCODE_LENGTH} characters."
        )
    if canonical.startswith(_PARTFLOW_PREFIX):
        raise InvalidBadgeBarcodeError(
            "A badge barcode cannot start with PF: — that prefix belongs to PartFlow"
            " barcodes. Use the barcode printed on the employee badge."
        )
    return canonical
