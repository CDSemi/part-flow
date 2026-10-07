"""Unit tests for password hashing (Phase 14 slice 1, ``app.application.password_hashing``).

scrypt from the standard library with a per-hash random salt, a
self-describing stored value, constant-time comparison, a dummy
verification for unknown accounts, and the admission / compute bounds
that keep hashing from stalling the shared threadpool (OD-S1-13).
"""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.application import password_hashing
from app.application.errors import PasswordCheckBusyError


def test_hash_format_carries_the_current_parameters() -> None:
    stored = password_hashing.hash_password("correct horse battery")
    parts = stored.split("$")
    assert parts[:4] == ["scrypt", "32768", "8", "1"]
    assert len(parts) == 6
    assert not password_hashing.needs_rehash(stored)


def test_verify_accepts_the_password_and_refuses_others() -> None:
    stored = password_hashing.hash_password("correct horse battery")
    assert password_hashing.verify_password("correct horse battery", stored)
    assert not password_hashing.verify_password("correct horse batterz", stored)
    assert not password_hashing.verify_password("", stored)


def test_every_hash_has_its_own_salt() -> None:
    first = password_hashing.hash_password("same password here")
    second = password_hashing.hash_password("same password here")
    assert first != second
    assert password_hashing.verify_password("same password here", first)
    assert password_hashing.verify_password("same password here", second)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "scrypt",
        "bcrypt$32768$8$1$c2FsdA==$a2V5",
        "scrypt$x$8$1$c2FsdA==$a2V5",
        "scrypt$1000$8$1$c2FsdA==$a2V5",  # not a power of two
        "scrypt$32768$8$1$not base64!$a2V5",
        "scrypt$32768$8$1$c2FsdA==",
    ],
)
def test_a_malformed_stored_value_never_verifies(stored: str) -> None:
    assert not password_hashing.verify_password("anything at all", stored)
    assert password_hashing.needs_rehash(stored)


def test_older_parameters_need_a_rehash() -> None:
    salt, key = "c2FsdA==", "a2V5"
    assert password_hashing.needs_rehash(f"scrypt$16384$8$1${salt}${key}")
    assert not password_hashing.needs_rehash(f"scrypt$32768$8$1${salt}${key}")


def test_the_nfkc_form_is_what_is_hashed() -> None:
    stored = password_hashing.hash_password("password1234")
    assert password_hashing.verify_password("ｐａｓｓｗｏｒｄ１２３４", stored)


def test_dummy_verify_spends_a_verification_and_never_raises() -> None:
    password_hashing.dummy_verify("whatever the password")


def test_secret_fields_are_capped_at_1024_raw_characters() -> None:
    class Body(BaseModel):
        password: Annotated[SecretStr, Field(max_length=1024)]

    assert Body.model_validate({"password": "x" * 1024}).password.get_secret_value() == "x" * 1024
    with pytest.raises(ValidationError):
        Body.model_validate({"password": "x" * 1025})


# ---------------------------------------------------------------------------
# Bounds (A-19 unit part)
# ---------------------------------------------------------------------------


@pytest.fixture
def slow_derive(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Make every derivation block until the returned event is set."""
    release = threading.Event()

    def derive(password: bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
        release.wait(timeout=30)
        return b"\x00" * 32

    monkeypatch.setattr(password_hashing, "_derive", derive)
    yield release
    release.set()


def _hold(count: int) -> list[threading.Thread]:
    threads = [
        threading.Thread(target=password_hashing.dummy_verify, args=("held",)) for _ in range(count)
    ]
    for thread in threads:
        thread.start()
    return threads


def _wait_for(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_a_fifth_caller_waits_for_a_compute_slot_then_is_refused(
    slow_derive: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The wait is read per call; shortened so the case stays fast.
    monkeypatch.setattr(password_hashing, "HASHING_WAIT_SECONDS", 0.3)
    held = _hold(password_hashing.HASHING_CONCURRENCY)
    _wait_for(lambda: password_hashing._admitted == password_hashing.HASHING_CONCURRENCY)
    started = time.monotonic()
    with pytest.raises(PasswordCheckBusyError) as raised:
        password_hashing.dummy_verify("fifth")
    waited = time.monotonic() - started
    assert raised.value.message == (
        "PartFlow is busy checking other passwords. Try again in a moment."
    )
    assert 0.25 <= waited < 2.5
    slow_derive.set()
    for thread in held:
        thread.join(timeout=5)
    assert password_hashing._admitted == 0


def test_a_ninth_caller_is_refused_immediately(slow_derive: threading.Event) -> None:
    held = _hold(password_hashing.HASHING_ADMISSION)
    _wait_for(lambda: password_hashing._admitted == password_hashing.HASHING_ADMISSION)
    started = time.monotonic()
    with pytest.raises(PasswordCheckBusyError):
        password_hashing.hash_password("ninth caller here")
    assert time.monotonic() - started < 0.2
    slow_derive.set()
    for thread in held:
        thread.join(timeout=10)
    assert password_hashing._admitted == 0
