"""Unit tests of the Scan Station device enrollment values (Phase 14 slice 4).

``app.domain.station_device``: the enrollment code (Crockford base32,
10 characters, shown as ``ABCDE-FGHJK``, matched case-, space- and
hyphen-insensitively with the ``O``/``I``/``L`` aliases) and the device
name rule. No database.
"""

import pytest

from app.domain.station_device import (
    DEVICE_LABEL_MAX,
    ENROLLMENT_CODE_ALPHABET,
    ENROLLMENT_CODE_LENGTH,
    InvalidDeviceLabelError,
    format_enrollment_code,
    normalize_device_label,
    normalize_enrollment_code,
)


def test_the_alphabet_is_crockford_base32() -> None:
    assert len(ENROLLMENT_CODE_ALPHABET) == 32
    assert len(set(ENROLLMENT_CODE_ALPHABET)) == 32
    assert not set("ILOU") & set(ENROLLMENT_CODE_ALPHABET)
    assert ENROLLMENT_CODE_LENGTH == 10
    assert DEVICE_LABEL_MAX == 80


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("ABCDE-FGHJK", "ABCDEFGHJK"),
        ("abcde-fghjk", "ABCDEFGHJK"),
        ("  abc de\tfg-hj k \n", "ABCDEFGHJK"),
        ("ABCDEFGHJK", "ABCDEFGHJK"),
        ("0123456789", "0123456789"),
        ("O1I2L3o4i5", "0112130415"),
        ("--ZZZZZ--ZZZZZ--", "ZZZZZZZZZZ"),
    ],
)
def test_codes_normalize(raw: str, canonical: str) -> None:
    assert normalize_enrollment_code(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ABCDE-FGHJ",
        "ABCDE-FGHJKM",
        "ABCDE-FGHJU",
        "ABCDE_FGHJK",
        "ÄBCDE-FGHJK",
        "ABCDE-FGHJ!",
        None,
        1234567890,
        b"ABCDEFGHJK",
    ],
)
def test_anything_else_is_no_code(raw: object) -> None:
    assert normalize_enrollment_code(raw) is None


def test_codes_format_in_two_groups() -> None:
    assert format_enrollment_code("ABCDEFGHJK") == "ABCDE-FGHJK"
    assert normalize_enrollment_code(format_enrollment_code("0123456789")) == "0123456789"


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("Line 3 terminal", "Line 3 terminal"),
        ("  Desk  ", "Desk"),
        ("x" * 80, "x" * 80),
        ("Bàn trạm 1", "Bàn trạm 1"),
    ],
)
def test_device_names_are_trimmed(raw: str, label: str) -> None:
    assert normalize_device_label(raw) == label


@pytest.mark.parametrize(
    "raw", ["", "   ", "x" * 81, "bad\x00name", "tab\tname", "line\nbreak", None, 5]
)
def test_device_names_are_refused(raw: object) -> None:
    with pytest.raises(InvalidDeviceLabelError):
        normalize_device_label(raw)
