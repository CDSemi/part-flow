"""Unit tests for the canonical email address rule (``app.domain.email_address``).

Phase 17 (owner decision of 2026-11-13): a User's optional address and the
sender address of the outgoing mail server share one rule — trimmed, plain
ASCII, a local part of permitted characters, ``@`` and a lowercased domain
with a dot, 254 characters at most. Every canonical result also passes the
database CHECK, whose pattern is repeated here.
"""

import re

import pytest

from app.domain.email_address import (
    INVALID_EMAIL_ADDRESS_MESSAGE,
    MAX_EMAIL_ADDRESS_LENGTH,
    InvalidEmailAddressError,
    normalize_email_address,
)

# The pattern of ck_users_email_canonical / ck_application_policy_mail_sender_canonical.
_CHECK = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}@[a-z0-9-]{1,63}(\.[a-z0-9-]{1,63})+")


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("ann@example.com", "ann@example.com"),
        ("  Ann@Example.COM \t", "Ann@example.com"),
        ("a.b+tag@mail.example.co", "a.b+tag@mail.example.co"),
        ("o'neil@ex-ample.org", "o'neil@ex-ample.org"),
        ("x!#$%&*/=?^_`{|}~-@a.io", "x!#$%&*/=?^_`{|}~-@a.io"),
        ("ann@xn--bcher-kva.example", "ann@xn--bcher-kva.example"),
        ("ann@1.2", "ann@1.2"),
        ("a" * 64 + "@example.com", "a" * 64 + "@example.com"),
    ],
)
def test_canonical_addresses(raw: str, canonical: str) -> None:
    assert normalize_email_address(raw) == canonical
    assert _CHECK.fullmatch(canonical) is not None


def test_the_longest_address_is_admitted() -> None:
    domain = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 60])
    raw = "x@" + domain
    assert len(raw) == MAX_EMAIL_ADDRESS_LENGTH
    assert normalize_email_address(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "ann",
        "ann@",
        "@example.com",
        "ann@example",
        "ann@@example.com",
        "ann@exam@ple.com",
        "an n@example.com",
        "ann@exa mple.com",
        ".ann@example.com",
        "ann.@example.com",
        "an..n@example.com",
        "ann@-example.com",
        "ann@example-.com",
        "ann@example..com",
        "ann@exa_mple.com",
        '"ann"@example.com',
        "ann@[192.168.0.1]",
        "ännchen@example.com",
        "ann@exämple.com",
        "ann@exa\nmple.com",
        "a" * 65 + "@example.com",
        "ann@" + "a" * 64 + ".com",
        "x@" + ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61]),
    ],
)
def test_non_canonical_input_is_refused(raw: str) -> None:
    with pytest.raises(InvalidEmailAddressError, match=re.escape(INVALID_EMAIL_ADDRESS_MESSAGE)):
        normalize_email_address(raw)
