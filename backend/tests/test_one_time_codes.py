"""Unit tests for the one-time codes of two-step sign-in (``app.application.one_time_codes``).

Phase 17 (owner decision of 2026-11-13):

- TOTP is RFC 6238 over RFC 4226: the RFC 4226 Appendix D values and the
  RFC 6238 Appendix B times (SHA-1, truncated to 6 digits) are reproduced;
  a code is accepted within the policy's clock drift around now (owner
  decisions of 2026-11-17: 0, 1 or 2 steps), never for a step already used;
- the otpauth link carries the issuer, the account and the parameters
  every authenticator app expects;
- recovery codes have the policy's length in the look-alike-free alphabet,
  shown in groups of four, typed in any case with spaces or hyphens; a
  code of any length the policy ever allowed is still read;
- emailed codes have the policy's digits; digests are SHA-256; an address
  is masked to its first character and its domain.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from app.application import one_time_codes as codes

# RFC 4226 Appendix D / RFC 6238 Appendix B (SHA-1): the ASCII secret.
_RFC_SECRET = b"12345678901234567890"


@pytest.mark.parametrize(
    ("counter", "expected"),
    list(
        enumerate(
            [
                "755224",
                "287082",
                "359152",
                "969429",
                "338314",
                "254676",
                "287922",
                "162583",
                "399871",
                "520489",
            ]
        )
    ),
)
def test_hotp_matches_rfc_4226(counter: int, expected: str) -> None:
    assert codes.hotp(_RFC_SECRET, counter) == expected


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_totp_matches_rfc_6238(timestamp: int, expected: str) -> None:
    step = codes.totp_step(timestamp)
    assert codes.hotp(_RFC_SECRET, step, digits=8) == expected
    assert codes.hotp(_RFC_SECRET, step) == expected[-6:]


def test_a_code_is_accepted_one_step_around_now_and_once() -> None:
    now = codes.totp_step(1234567890)
    for offset in (-1, 0, 1):
        code = codes.hotp(_RFC_SECRET, now + offset)
        assert codes.matching_totp_step(_RFC_SECRET, code, now, None, 1) == now + offset
        assert codes.matching_totp_step(_RFC_SECRET, f" {code[:3]} {code[3:]} ", now, None, 1) == (
            now + offset
        )
    for offset in (-2, 2):
        code = codes.hotp(_RFC_SECRET, now + offset)
        assert codes.matching_totp_step(_RFC_SECRET, code, now, None, 1) is None
    current = codes.hotp(_RFC_SECRET, now)
    # Used once: the same step, or an older one, never works again.
    assert codes.matching_totp_step(_RFC_SECRET, current, now, now, 1) is None
    assert codes.matching_totp_step(_RFC_SECRET, current, now, now - 1, 1) == now
    for typed in ("", "12345", "1234567", "12345a", "١٢٣٤٥٦"):
        assert codes.matching_totp_step(_RFC_SECRET, typed, now, None, 1) is None


@pytest.mark.parametrize("drift", [0, 1, 2])
def test_the_clock_drift_is_the_policy_s(drift: int) -> None:
    now = codes.totp_step(1234567890)
    for offset in range(-3, 4):
        code = codes.hotp(_RFC_SECRET, now + offset)
        expected = now + offset if abs(offset) <= drift else None
        assert codes.matching_totp_step(_RFC_SECRET, code, now, None, drift) == expected


def test_a_new_secret_is_160_bits_in_unpadded_base32() -> None:
    secret = codes.new_totp_secret()
    assert len(secret) == codes.TOTP_SECRET_BYTES == 20
    text = codes.totp_secret_text(secret)
    assert len(text) == 32 and "=" not in text and text == text.upper()
    assert base64.b32decode(text) == secret
    assert codes.new_totp_secret() != secret


def test_the_otpauth_link_names_partflow_and_the_account() -> None:
    secret = bytes(range(20))
    uri = codes.totp_uri(secret, "j.doe@plant")
    parts = urlsplit(uri)
    assert (parts.scheme, parts.netloc, parts.path) == ("otpauth", "totp", "/PartFlow:j.doe@plant")
    assert parse_qs(parts.query) == {
        "secret": [codes.totp_secret_text(secret)],
        "issuer": ["PartFlow"],
        "algorithm": ["SHA1"],
        "digits": ["6"],
        "period": ["30"],
    }


def test_recovery_codes_are_typed_in_any_form() -> None:
    code = codes.new_recovery_code(12)
    assert len(code) == 12 and set(code) <= set(codes.RECOVERY_ALPHABET)
    shown = codes.format_recovery_code(code)
    assert shown == f"{code[:4]}-{code[4:8]}-{code[8:]}"
    assert codes.canonical_recovery_code(shown) == code
    assert codes.canonical_recovery_code(f"  {shown.upper()} ") == code
    assert codes.canonical_recovery_code(shown.replace("-", " ")) == code
    for typed in ("", " - ", "abcd-efgh-jk1n", "abcd-efgh-jkon", "a" * 33):
        assert codes.canonical_recovery_code(typed) is None
    assert not set("01ilo") & set(codes.RECOVERY_ALPHABET)


@pytest.mark.parametrize(("length", "shown"), [(8, "4-4"), (10, "4-4-2"), (20, "4-4-4-4-4")])
def test_recovery_codes_have_the_policy_s_length(length: int, shown: str) -> None:
    code = codes.new_recovery_code(length)
    assert len(code) == length and set(code) <= set(codes.RECOVERY_ALPHABET)
    formatted = codes.format_recovery_code(code)
    assert "-".join(str(len(group)) for group in formatted.split("-")) == shown
    # Read whatever length the policy had when the code was made.
    assert codes.canonical_recovery_code(formatted) == code


@pytest.mark.parametrize("digits", [6, 8, 10])
def test_email_codes_have_the_policy_s_digits(digits: int) -> None:
    code = codes.new_email_code(digits)
    assert len(code) == digits and code.isdigit()
    assert codes.digits_only(f"{code[:4]} {code[4:]}") == code


def test_email_codes_digests_and_masks() -> None:
    code = codes.new_email_code(8)
    assert codes.digits_only(f" {code[:4]} {code[4:]} ") == code
    for typed in ("", "12a4", "١٢٣٤", "1" * 33):
        assert codes.digits_only(typed) is None
    assert codes.code_digest(code) == hashlib.sha256(code.encode()).digest()
    assert len(codes.code_digest(code)) == codes.DIGEST_BYTES
    assert codes.mask_email_address("ann.lee@example.com") == "a•••@example.com"
    assert codes.mask_email_address("x@mail.example.org") == "x•••@mail.example.org"
