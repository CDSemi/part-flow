"""The typed-confirmation phrase allowlist (SPEC 6.2), derived from the pf-admin.py / pf_install.py constructors.

Each entry: the constructor's leading literal (its "verb") -> the full-phrase regex. A step allows only the entries
it names (and may narrow them to one exact phrase); tests/test_integration_harness.py proves that every
``confirm(...)`` / ``Type exactly`` constructor of the shipped code has an entry and that an unknown phrase is
refused.
"""
import re

PHRASES = {
    # pf_install.py confirmation_phrase / resume
    "INSTALL CONTROL": r"INSTALL CONTROL r-[0-9a-f]{16}",
    "SELECT CONTROL": r"SELECT CONTROL r-[0-9a-f]{16}",
    "REGISTER": r"REGISTER [a-z0-9][a-z0-9-]{0,62}",
    "MIGRATE": r"MIGRATE [a-z0-9][a-z0-9-]{0,62}",
    # pf-admin.py lifecycle routes
    "DEPLOY": r"DEPLOY [0-9a-f]{12}",
    "UPDATE": r"UPDATE [0-9a-f]{12}",
    "ROLLBACK": r"ROLLBACK [0-9A-Za-z._-]+",
    "RESTORE": r"RESTORE [a-z0-9_]+ [0-9A-Za-z._-]+",
    "RESTORE INSTANCE": r"RESTORE INSTANCE [a-z0-9][a-z0-9_-]*",
    "RESTORE COPY": r"RESTORE COPY purge-[0-9A-Za-z._-]+",
    "RESET": r"RESET [a-z0-9_]+",
    "PURGE": r"PURGE [a-z0-9][a-z0-9_-]*",
    "DELETE": r"DELETE [a-z0-9_]+",
    "DELETE BACKUPS": r"DELETE BACKUPS [a-z0-9][a-z0-9_-]*",
    "RESET ADMIN CONFIG": r"RESET ADMIN CONFIG [a-z0-9][a-z0-9_-]*",
    "ERASE": r"ERASE [a-z0-9][a-z0-9_-]* [0-9A-F]{6}",
    "CLEANUP": r"CLEANUP [a-z0-9][a-z0-9_-]* [0-9a-f]{8}",
    "DELETE CHECKPOINT HISTORY": r"DELETE CHECKPOINT HISTORY \S+",
    "REMOVE RECOVERY TARGET": r"REMOVE RECOVERY TARGET pfrecover-[0-9a-f]{12}",
    "ABORT DEPLOY": r"ABORT DEPLOY [a-z0-9][a-z0-9_-]*",
    "EMERGENCY BACKUP": r"EMERGENCY BACKUP [a-z0-9][a-z0-9_-]*",
    "ACKNOWLEDGE": r"ACKNOWLEDGE [0-9a-f]{8}",
    # resume_phrase
    "RESUME": r"RESUME [0-9a-f]{8}",
    "RESUME PURGE": r"RESUME PURGE [a-z0-9][a-z0-9_-]* purge-[0-9A-Za-z._-]+",
    "RESUME ABORT DEPLOY": r"RESUME ABORT DEPLOY [a-z0-9][a-z0-9_-]*",
    "ABANDON": r"ABANDON [0-9a-f]{8}",
    "ABANDON RESTORE": r"ABANDON RESTORE [a-z0-9][a-z0-9_-]* [0-9a-f]{8}",
    "ABANDON RECOVERY TARGET": r"ABANDON RECOVERY TARGET (?:pfrecover-[0-9a-f]{12}|[0-9a-f]{8})",
    "KEEP WORKSPACE": r"KEEP WORKSPACE [0-9a-f]{8}",
    # permissions
    "APPLY PERMISSIONS": r"APPLY PERMISSIONS [a-z0-9][a-z0-9-]*",
    "RESUME PERMISSIONS": r"RESUME PERMISSIONS [a-z0-9][a-z0-9-]*",
    "ABANDON PERMISSIONS": r"ABANDON PERMISSIONS [a-z0-9][a-z0-9-]*",
}


def allow(*names):
    """The regexes of the named entries (KeyError for an unknown name: a step can only allow listed phrases)."""
    return tuple(PHRASES[name] for name in names)


def exact(*phrases):
    """Narrow to exact phrases; each must also match some allowlisted entry."""
    result = []
    for phrase in phrases:
        if not any(re.fullmatch(pattern, phrase) for pattern in PHRASES.values()):
            raise ValueError(f"phrase {phrase!r} is not in the allowlist")
        result.append(re.escape(phrase))
    return tuple(result)


def allowed(phrase, patterns):
    return any(re.fullmatch(pattern, phrase) for pattern in patterns)


def entry_for(phrase):
    """The longest allowlist verb whose regex matches ``phrase``, else None."""
    matches = [name for name, pattern in PHRASES.items() if re.fullmatch(pattern, phrase)]
    return max(matches, key=len) if matches else None
