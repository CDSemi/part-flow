"""One-time sign-in codes of two-step sign-in (Phase 17, owner decision of 2026-11-13).

Framework-independent rules of the codes a User types at the second step.
Every number a rule takes as an argument is the two-step policy of
Administration → Settings (owner decisions of 2026-11-17); the callers read
it.

- Authenticator app codes (TOTP, RFC 6238 over RFC 4226): HMAC-SHA1 of the
  30-second time step under a 160-bit secret, truncated to 6 digits — the
  parameters every authenticator app (1Password included) assumes, and
  some apps ignore any other value, so they stay fixed. The secret is
  shown as unpadded base32 and in an ``otpauth://`` link. A code is
  accepted for the current step and up to ``drift`` steps before or after
  (clock drift), and never for a step at or before the last step accepted,
  so a code works once.
- Recovery codes: characters from an alphabet without look-alike
  characters (no 0, 1, i, l or o), shown in groups of four
  (``abcd-efgh-jkmn``); typing ignores letter case, spaces and hyphens.
  Only the SHA-256 digest of the canonical form is stored, so a code of any
  length the policy ever allowed keeps working.
- Email codes: digits, stored as a SHA-256 digest.

Every comparison of a typed code with an expected one is constant-time
(``hmac.compare_digest``).
"""

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Final
from urllib.parse import quote, urlencode

TOTP_DIGITS: Final = 6
TOTP_PERIOD_SECONDS: Final = 30
TOTP_SECRET_BYTES: Final = 20
TOTP_ISSUER: Final = "PartFlow"

RECOVERY_ALPHABET: Final = "23456789abcdefghjkmnpqrstuvwxyz"
_RECOVERY_GROUP: Final = 4
#: Longest typed code (digits, or recovery code characters) ever compared.
_MAX_TYPED_LENGTH: Final = 32

#: Length of every stored code digest (SHA-256).
DIGEST_BYTES: Final = 32


def new_totp_secret() -> bytes:
    return secrets.token_bytes(TOTP_SECRET_BYTES)


def totp_secret_text(secret: bytes) -> str:
    """The secret as authenticator apps take it: unpadded base32, capital letters."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def totp_step(timestamp: float) -> int:
    return int(timestamp // TOTP_PERIOD_SECONDS)


def hotp(secret: bytes, counter: int, digits: int = TOTP_DIGITS) -> str:
    """RFC 4226 §5.3: the dynamically truncated HMAC-SHA1 of ``counter``."""
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def digits_only(text: str) -> str | None:
    """``text`` without spaces when it is 1 to 32 ASCII digits, else None."""
    compact = "".join(text.split())
    if 0 < len(compact) <= _MAX_TYPED_LENGTH and compact.isascii() and compact.isdigit():
        return compact
    return None


def matching_totp_step(
    secret: bytes, code: str, now_step: int, last_used_step: int | None, drift: int
) -> int | None:
    """The step ``code`` belongs to within ``drift`` steps, never one already used; else None."""
    typed = digits_only(code)
    if typed is None or len(typed) != TOTP_DIGITS:
        return None
    found: int | None = None
    for step in range(now_step - drift, now_step + drift + 1):
        # Every step is computed and compared, so the time taken does not
        # depend on which one matched.
        if hmac.compare_digest(hotp(secret, step), typed) and (
            last_used_step is None or step > last_used_step
        ):
            found = step
    return found


def totp_uri(secret: bytes, account: str) -> str:
    """The ``otpauth://`` link an authenticator app adds the account from."""
    label = quote(f"{TOTP_ISSUER}:{account}", safe=":@")
    query = urlencode(
        {
            "secret": totp_secret_text(secret),
            "issuer": TOTP_ISSUER,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"


def new_recovery_code(length: int) -> str:
    return "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(length))


def format_recovery_code(code: str) -> str:
    return "-".join(
        code[start : start + _RECOVERY_GROUP] for start in range(0, len(code), _RECOVERY_GROUP)
    )


def canonical_recovery_code(text: str) -> str | None:
    """The canonical form of a typed recovery code, or None when it cannot be one."""
    compact = "".join(text.split()).replace("-", "").lower()
    if 0 < len(compact) <= _MAX_TYPED_LENGTH and all(ch in RECOVERY_ALPHABET for ch in compact):
        return compact
    return None


def new_email_code(digits: int) -> str:
    return str(secrets.randbelow(10**digits)).zfill(digits)


def code_digest(code: str) -> bytes:
    return hashlib.sha256(code.encode("ascii")).digest()


def mask_email_address(address: str) -> str:
    """``ann.lee@example.com`` → ``a•••@example.com``: enough to recognize, not to read."""
    local, _, domain = address.rpartition("@")
    return f"{local[:1]}•••@{domain}"
