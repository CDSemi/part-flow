"""Unit tests for the password rule (Phase 14 slice 1, ``app.domain.password_policy``).

Length counts characters of the NFKC form (12-256), nothing is stripped,
and no composition rule applies (PLAN OD-P3 default, OD-S1-9).
"""

import ast
from pathlib import Path

import pytest

from app.domain.password_policy import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    InvalidPasswordError,
    is_encodable,
    is_over_long,
    normalize_password,
    validate_new_password,
)

_P1 = "A password must be at least 12 characters long."
_P2 = "A password can be at most 256 characters long."
_P8 = "A password can contain only valid text characters."
_DOMAIN_DIR = Path(__file__).resolve().parent.parent / "app" / "domain"


def test_bounds() -> None:
    assert (MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH) == (12, 256)


@pytest.mark.parametrize(("length", "message"), [(11, _P1), (257, _P2)])
def test_lengths_outside_the_rule_are_refused(length: int, message: str) -> None:
    with pytest.raises(InvalidPasswordError) as raised:
        validate_new_password("x" * length)
    assert str(raised.value) == message


@pytest.mark.parametrize("length", [12, 256])
def test_lengths_inside_the_rule_are_admitted(length: int) -> None:
    assert validate_new_password("x" * length) == "x" * length


def test_length_counts_the_nfkc_form() -> None:
    # U+FB03 (ﬃ) is one character that NFKC expands to three.
    assert len(normalize_password("ﬃ")) == 3
    assert validate_new_password("ﬃ" * 4) == "ffi" * 4  # 4 raw, 12 after NFKC
    with pytest.raises(InvalidPasswordError):
        validate_new_password("ﬃ" * 86)  # 86 raw, 258 after NFKC
    assert is_over_long("ﬃ" * 86)
    assert not is_over_long("x" * 256)


def test_a_lone_surrogate_is_refused() -> None:
    # JSON can carry "\ud800"; UTF-8 cannot encode it, so it could never be hashed.
    lone = "\ud800" + "x" * 13
    assert not is_encodable(lone)
    assert is_encodable("x" * 12) and is_encodable("mật-khẩu-đủ-dài-\U0001f600")
    with pytest.raises(InvalidPasswordError) as raised:
        validate_new_password(lone)
    assert str(raised.value) == _P8


def test_nothing_is_stripped_and_no_composition_rule_applies() -> None:
    assert validate_new_password("  spaced out  ") == "  spaced out  "
    assert validate_new_password("aaaaaaaaaaaa") == "aaaaaaaaaaaa"


def test_full_width_characters_normalize_to_ascii() -> None:
    assert normalize_password("ｐａｓｓｗｏｒｄ１２３４") == "password1234"


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    return roots


def test_no_domain_module_imports_hashing_secrets_or_fastapi() -> None:
    """B-STATIC: password hashing, token generation and the web framework
    stay out of the domain. The one pre-existing ``hashlib`` user is the
    Phase 12 Hot list fingerprint (a content digest, not a credential)."""
    hashlib_users = {
        path.name for path in _DOMAIN_DIR.rglob("*.py") if "hashlib" in _imported_roots(path)
    }
    assert hashlib_users <= {"hot_list.py"}
    for path in sorted(_DOMAIN_DIR.rglob("*.py")):
        assert not _imported_roots(path) & {"secrets", "fastapi"}, path.name
    for name in ("password_policy.py", "user_login.py"):
        assert not _imported_roots(_DOMAIN_DIR / name) & {"hashlib", "secrets", "fastapi"}
