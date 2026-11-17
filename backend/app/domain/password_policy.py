"""Password rules for application Users (Phase 14 slice 1; Security, 2026-11-17).

A User signs in with a login name and a password (owner decision OD-P1:
local accounts). A new password must satisfy the password rules of
Administration → Security (owner request of 2026-11-17), stored on the
application policy and passed in as ``PasswordRules``: a minimum and a
maximum length, each from 4 to 256 characters with the minimum never
above the maximum, and four optional composition rules — a digit, a
special character, an uppercase letter and a lowercase letter. The
defaults are the rule before Administration could change it: 6 to 256
characters (the minimum is the owner's decision of 2026-11-12; slice 1
decision OD-S1-9 for the rest) and no composition rule.

Length counts characters of the Unicode NFKC form, and nothing is
stripped — a space is a character like any other. The same NFKC form is
what gets hashed, so a password typed with full-width characters verifies
against its ASCII spelling. The composition rules judge each character of
that form by its Unicode general category: a digit is a decimal digit
(``Nd``), an uppercase letter ``Lu``, a lowercase letter ``Ll`` and a
special character a punctuation mark or a symbol (``P*`` or ``S*``); a
space is none of them. The frontend mirrors these categories for its
hints; this module decides.

A password must also be valid text: a lone UTF-16 surrogate (which JSON
can carry but UTF-8 cannot encode) can never be hashed, so a new password
holding one is refused and a sign-in with one is refused like an
over-long password. Sign-in never applies the rules, so an older password
stays valid when the rules change; only a password longer than any rule
admits (``MAX_PASSWORD_LENGTH``) can match no stored hash. This module
owns the rules and is deliberately framework-independent; the
Application reads the stored rules (``app.application.policies``) and
hashing lives in ``app.application.password_hashing``.
"""

import unicodedata
from typing import Final, NamedTuple

#: The bounds of both length rules (the CHECKs of the policy columns).
PASSWORD_LENGTH_BOUNDS: Final = (4, 256)
#: The most characters any password may have (after NFKC): the upper bound
#: of the length rules, so a longer password given at sign-in can match no
#: stored hash, whatever the rules were when it was set.
MAX_PASSWORD_LENGTH: Final = PASSWORD_LENGTH_BOUNDS[1]

INVALID_TEXT_MESSAGE: Final = "A password can contain only valid text characters."


class PasswordRules(NamedTuple):
    """The password rules a new password must satisfy (Administration → Security)."""

    min_length: int = 6
    max_length: int = MAX_PASSWORD_LENGTH
    require_digit: bool = False
    require_special: bool = False
    require_uppercase: bool = False
    require_lowercase: bool = False


#: The rules a fresh install starts with: today's rule (6 to 256 characters).
DEFAULT_PASSWORD_RULES: Final = PasswordRules()


class InvalidPasswordError(ValueError):
    """Raised when a new password does not satisfy the password rules.

    ``str()`` names every unmet rule, one sentence each.
    """

    def __init__(self, messages: list[str]) -> None:
        super().__init__(" ".join(messages))
        self.messages = messages


def normalize_password(raw: str) -> str:
    """The NFKC form of ``raw`` — what is measured and hashed; nothing stripped."""
    return unicodedata.normalize("NFKC", raw)


def is_over_long(raw: str) -> bool:
    """Whether ``raw`` exceeds the longest password any rule admits, after normalization."""
    return len(normalize_password(raw)) > MAX_PASSWORD_LENGTH


def is_encodable(raw: str) -> bool:
    """Whether the normalized ``raw`` encodes to UTF-8 (no lone surrogate)."""
    try:
        normalize_password(raw).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _has(password: str, *prefixes: str) -> bool:
    return any(unicodedata.category(character).startswith(prefixes) for character in password)


def unmet_rules(raw: str, rules: PasswordRules) -> list[str]:
    """One sentence per rule the normalized ``raw`` does not satisfy; empty = acceptable."""
    password = normalize_password(raw)
    unmet: list[str] = []
    if len(password) < rules.min_length:
        unmet.append(f"A password must be at least {rules.min_length} characters long.")
    if len(password) > rules.max_length:
        unmet.append(f"A password can be at most {rules.max_length} characters long.")
    if not is_encodable(password):
        unmet.append(INVALID_TEXT_MESSAGE)
    if rules.require_digit and not _has(password, "Nd"):
        unmet.append("A password must contain a digit.")
    if rules.require_special and not _has(password, "P", "S"):
        unmet.append("A password must contain a special character such as ! # % or *.")
    if rules.require_uppercase and not _has(password, "Lu"):
        unmet.append("A password must contain an uppercase letter.")
    if rules.require_lowercase and not _has(password, "Ll"):
        unmet.append("A password must contain a lowercase letter.")
    return unmet


def validate_new_password(raw: str, rules: PasswordRules = DEFAULT_PASSWORD_RULES) -> str:
    """Return the normalized new password or raise ``InvalidPasswordError``.

    Every path that sets a password calls this with the stored rules
    (``app.application.policies.read_password_rules``).
    """
    unmet = unmet_rules(raw, rules)
    if unmet:
        raise InvalidPasswordError(unmet)
    return normalize_password(raw)
