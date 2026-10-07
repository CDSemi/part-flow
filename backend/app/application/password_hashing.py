"""Password hashing for application Users (Phase 14 slice 1, owner decision OD-P1).

Standard library only: ``hashlib.scrypt`` over the UTF-8 bytes of the
NFKC form of the password (``app.domain.password_policy``), a fresh
16-byte random salt per hash, compared with ``hmac.compare_digest``.
The stored value carries its own parameters —
``scrypt$N$r$p$<base64 salt>$<base64 key>`` — so a later parameter
change verifies old hashes and upgrades them at the next successful
sign-in (``needs_rehash``).

scrypt deliberately costs CPU and memory (``N = 2**15``: about 50 ms and
32 MiB per call), and every route runs on the threadpool the health
probe and the Scan Station writes share. The work is therefore bounded,
never an unbounded wait:

- admission: at most ``HASHING_ADMISSION`` callers are inside the
  hashing section at once; the next one is refused immediately;
- compute: inside admission at most ``HASHING_CONCURRENCY`` derive at
  once; a caller waits at most ``HASHING_WAIT_SECONDS`` for a slot.

A refusal raises ``PasswordCheckBusyError`` (HTTP 503) — a definite
refusal: the callers run hashing before any write, so nothing was
written or counted. Callers also release their database connection
before hashing (``session.rollback()``), so no pooled connection and no
row lock is held during the work.

Nothing here logs a password, a hash or a salt.
"""

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final, NamedTuple

from app.application.errors import PasswordCheckBusyError
from app.domain.password_policy import is_encodable, normalize_password

logger = logging.getLogger(__name__)

#: Current scrypt parameters (slice 1 decision OD-S1-13; never below 2**14).
SCRYPT_N: Final = 2**15
SCRYPT_R: Final = 8
SCRYPT_P: Final = 1
_KEY_BYTES: Final = 32
_SALT_BYTES: Final = 16
_MAXMEM: Final = 64 * 1024 * 1024
_PREFIX: Final = "scrypt"

#: Callers admitted into the hashing section at once.
HASHING_ADMISSION: Final = 8
#: Callers deriving at once (bounds memory to 4 x 32 MiB).
HASHING_CONCURRENCY: Final = 4
#: Longest wait for a compute slot inside admission.
HASHING_WAIT_SECONDS: Final = 2.0

BUSY_MESSAGE: Final = "PartFlow is busy checking other passwords. Try again in a moment."

_admission_lock = threading.Lock()
_admitted = 0
_compute_slots = threading.BoundedSemaphore(HASHING_CONCURRENCY)


class _Parameters(NamedTuple):
    n: int
    r: int
    p: int
    salt: bytes
    key: bytes


def _busy() -> PasswordCheckBusyError:
    logger.info("Password check refused: the server is busy")
    return PasswordCheckBusyError(BUSY_MESSAGE)


@contextmanager
def _hashing_slot() -> Iterator[None]:
    """Admit the caller and hold one compute slot, or refuse it (B1)."""
    global _admitted
    with _admission_lock:
        if _admitted >= HASHING_ADMISSION:
            raise _busy()
        _admitted += 1
    try:
        if not _compute_slots.acquire(timeout=HASHING_WAIT_SECONDS):
            raise _busy()
        try:
            yield
        finally:
            _compute_slots.release()
    finally:
        with _admission_lock:
            _admitted -= 1


def _derive(password: bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
    """The scrypt derivation itself (one seam, so tests can slow it down)."""
    return hashlib.scrypt(password, salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_KEY_BYTES)


def _encode(n: int, r: int, p: int, salt: bytes, key: bytes) -> str:
    salt_text = base64.b64encode(salt).decode("ascii")
    key_text = base64.b64encode(key).decode("ascii")
    return f"{_PREFIX}${n}${r}${p}${salt_text}${key_text}"


def _decode(stored: str) -> _Parameters | None:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        key = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):
        return None
    if n < 2 or n & (n - 1) or not 1 <= r <= 32 or not 1 <= p <= 16 or not salt or not key:
        return None
    return _Parameters(n, r, p, salt, key)


def _password_bytes(password: str) -> bytes:
    return normalize_password(password).encode("utf-8")


def hash_password(password: str) -> str:
    """A new stored value for ``password`` with the current parameters."""
    salt = secrets.token_bytes(_SALT_BYTES)
    with _hashing_slot():
        key = _derive(_password_bytes(password), salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return _encode(SCRYPT_N, SCRYPT_R, SCRYPT_P, salt, key)


def _verify(password: str, stored: str) -> bool:
    parameters = _decode(stored)
    if parameters is None:
        logger.error("A stored password hash is malformed; the password check failed")
        return False
    # Outside the guard below: an unencodable password is the caller's
    # error (callers refuse it first), never a malformed stored hash.
    password_bytes = _password_bytes(password)
    with _hashing_slot():
        try:
            key = _derive(
                password_bytes,
                parameters.salt,
                parameters.n,
                parameters.r,
                parameters.p,
            )
        except ValueError:
            # Parameters the runtime refuses (for example beyond maxmem).
            logger.error("A stored password hash has unusable parameters; the check failed")
            return False
    return hmac.compare_digest(key, parameters.key)


def verify_password(password: str, stored: str) -> bool:
    """Whether ``password`` matches ``stored``; never raises on a mismatch."""
    return _verify(password, stored)


def needs_rehash(stored: str) -> bool:
    """Whether ``stored`` was made with parameters other than the current ones."""
    parameters = _decode(stored)
    return parameters is None or (parameters.n, parameters.r, parameters.p) != (
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
    )


# A well-formed value with the current parameters whose key is random
# bytes: no password matches it, and checking one against it costs the
# same scrypt work as a real verification. Built once, at import, with
# no derivation (so building it can never be refused or race).
_DUMMY_HASH: Final = _encode(
    SCRYPT_N,
    SCRYPT_R,
    SCRYPT_P,
    secrets.token_bytes(_SALT_BYTES),
    secrets.token_bytes(_KEY_BYTES),
)


def dummy_verify(password: str) -> None:
    """Spend one verification's work and discard the result.

    Used for an unknown login name, a User without a password or an
    over-long or unencodable password, so the response timing never
    tells them apart from a wrong password. An unencodable password
    (a lone surrogate) is replaced by a stand-in of the same work.
    """
    _verify(password if is_encodable(password) else "", _DUMMY_HASH)
