"""Unit tests for the password rules (``app.domain.password_policy``).

Phase 14 slice 1 and Administration → Security (owner request of
2026-11-17): length counts characters of the NFKC form, nothing is
stripped, and by default (6-256 characters) no composition rule applies
(PLAN OD-P3 default, OD-S1-9). The stored rules may set the lengths (4-256)
and require a digit, a special character, an uppercase and a lowercase
letter, judged by Unicode general category; every unmet rule is named.
"""

import ast
from pathlib import Path

import pytest

from app.domain.password_policy import (
    DEFAULT_PASSWORD_RULES,
    MAX_PASSWORD_LENGTH,
    PASSWORD_LENGTH_BOUNDS,
    InvalidPasswordError,
    PasswordRules,
    is_encodable,
    is_over_long,
    normalize_password,
    unmet_rules,
    validate_new_password,
)

_P1 = "A password must be at least 6 characters long."
_P2 = "A password can be at most 256 characters long."
_P8 = "A password can contain only valid text characters."
_DOMAIN_DIR = Path(__file__).resolve().parent.parent / "app" / "domain"


def test_bounds() -> None:
    assert PASSWORD_LENGTH_BOUNDS == (4, 256)
    assert MAX_PASSWORD_LENGTH == 256
    assert PasswordRules(6, 256, False, False, False, False) == DEFAULT_PASSWORD_RULES


@pytest.mark.parametrize(("length", "message"), [(5, _P1), (257, _P2)])
def test_lengths_outside_the_rule_are_refused(length: int, message: str) -> None:
    with pytest.raises(InvalidPasswordError) as raised:
        validate_new_password("x" * length)
    assert str(raised.value) == message


@pytest.mark.parametrize("length", [6, 256])
def test_lengths_inside_the_rule_are_admitted(length: int) -> None:
    assert validate_new_password("x" * length) == "x" * length


def test_length_counts_the_nfkc_form() -> None:
    # U+FB03 (ﬃ) is one character that NFKC expands to three.
    assert len(normalize_password("ﬃ")) == 3
    assert validate_new_password("ﬃ" * 2) == "ffi" * 2  # 2 raw, 6 after NFKC
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
    for name in ("password_policy.py", "user_login.py", "assigned_route.py"):
        assert not _imported_roots(_DOMAIN_DIR / name) & {"hashlib", "secrets", "fastapi"}


def test_the_assigned_route_rules_stay_framework_free() -> None:
    """Phase 14 slice 6: the AssignedRoute adjustment rules import no
    application, infrastructure or SQLAlchemy module (the fingerprint, the
    locks and the persistence live in ``app.application.route_adjustments``)."""
    tree = ast.parse((_DOMAIN_DIR / "assigned_route.py").read_text(encoding="utf-8"))
    modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {
        module
        for module in modules
        if module.startswith(("app.application", "app.infrastructure", "sqlalchemy", "fastapi"))
    }


_DIGIT = "A password must contain a digit."
_SPECIAL = "A password must contain a special character such as ! # % or *."
_UPPER = "A password must contain an uppercase letter."
_LOWER = "A password must contain a lowercase letter."
_ALL = PasswordRules(8, 20, True, True, True, True)


@pytest.mark.parametrize(
    ("password", "unmet"),
    [
        ("Abcdef1!", []),
        ("abcdef1!", [_UPPER]),
        ("ABCDEF1!", [_LOWER]),
        ("Abcdefg!", [_DIGIT]),
        ("Abcdefg1", [_SPECIAL]),
        ("Abcdefg 1", [_SPECIAL]),  # a space is not a special character
        ("abc", ["A password must be at least 8 characters long.", _DIGIT, _SPECIAL, _UPPER]),
        ("Abcdef1!" * 3, ["A password can be at most 20 characters long."]),
        # Unicode letters and digits count: Vietnamese capitals, Arabic-Indic digits.
        ("Mật-khẩu٣x", []),
        ("ĐƯỜNG-đi-٣", []),
        # NFKC first: the full-width digit is a digit, the full-width "！" is "!".
        ("Abcdefg１！", []),
        ("€uro-Price1a", []),  # a currency symbol is a special character
    ],
)
def test_every_unmet_rule_is_named(password: str, unmet: list[str]) -> None:
    assert unmet_rules(password, _ALL) == unmet
    if unmet:
        with pytest.raises(InvalidPasswordError) as raised:
            validate_new_password(password, _ALL)
        assert raised.value.messages == unmet
        assert str(raised.value) == " ".join(unmet)
    else:
        assert validate_new_password(password, _ALL) == normalize_password(password)


def test_the_default_rules_ask_only_for_the_length() -> None:
    assert unmet_rules("aaaaaa", DEFAULT_PASSWORD_RULES) == []
    assert unmet_rules("aaaaa", DEFAULT_PASSWORD_RULES) == [_P1]
    lowered = PasswordRules(min_length=4)
    assert validate_new_password("abcd", lowered) == "abcd"
