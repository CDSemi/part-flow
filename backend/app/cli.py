"""PartFlow backend command line (`python -m app.cli <command>`).

Presentation adapter: parses arguments, builds the engine from Settings,
calls one Application function, renders its result. No business rule here.
Exit codes shared by every command: 0 success/clean, 1 refusal or mismatch,
2 could not run (usage error, configuration, database unreachable, internal error)
or its outcome is unknown (the database connection failed during COMMIT).

Commands:

- ``reset-password --login-name NAME`` (Phase 14 slice 1, recovery
  only): give a User that already has a password a new temporary one,
  clear its lock and end its sign-ins — for example when the only
  administrator forgot the password. Refused while no administrator
  exists (first-run setup creates the first one) and for a User without
  a password (an administrator gives it one in Administration → Users),
  so it can never create an administrator. The password is typed at the
  prompt (twice) or read as one line from a non-interactive stdin —
  never an argument, never printed. Access to the backend container is
  the authority; the audit row records ``source: cli`` and no actor.
- ``restore-correction-permission-management --role-name NAME`` (Phase
  14 slice 2, recovery only): grant ``MANAGE_CORRECTION_PERMISSIONS`` to
  the named role while no active user with a password holds it — a state
  only data from before slice 2 can be in, which the Administration
  screens can no longer repair (granting it needs it). Refused while such
  a user exists and for a role no active user with a password holds; it
  grants that one key only. Audited like a role edit (``source: cli``, no
  actor).

The CLI configures no logging on stdout: stdout carries only the
command's outcome lines; refusals and errors go to stderr.
"""

import argparse
import getpass
import sys
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application import authentication
from app.application.errors import ApplicationError, RecoveryOutcomeUnknownError
from app.core.config import get_settings
from app.infrastructure.database import build_engine

_DATABASE_UNAVAILABLE = "PartFlow could not reach its database. Nothing was changed."
_DATABASE_ERROR = "The database refused the reset (internal error). Nothing was changed."
_RESTORE_DATABASE_ERROR = "The database refused the grant (internal error). Nothing was changed."
_CONFIGURATION_INVALID = "PartFlow is not configured: check DATABASE_URL. Nothing was changed."
_PASSWORDS_DIFFER = "The passwords do not match. Nothing was changed."


def _engine() -> Engine:
    return build_engine(get_settings().database_url)


def _read_new_password() -> str | None:
    """The new password from the prompt (asked twice) or one stdin line; None = mismatch."""
    if sys.stdin.isatty():
        first = getpass.getpass("New temporary password: ")
        second = getpass.getpass("Repeat the new temporary password: ")
        return first if first == second else None
    return sys.stdin.readline().rstrip("\r\n")


def _add_reset_password_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "reset-password",
        help="Reset the password of a user that already has one (recovery).",
        description=(
            "Give a user that already has a password a new temporary password, clear its"
            " lock and end its sign-ins. Only while an administrator exists. The password is"
            " read from the prompt or from one line of standard input."
        ),
    )
    parser.add_argument("--login-name", required=True, help="The user's login name.")
    parser.set_defaults(handler=_run_reset_password)


def _run_reset_password(args: argparse.Namespace) -> int:
    password = _read_new_password()
    if password is None:
        print(_PASSWORDS_DIFFER, file=sys.stderr)
        return 1
    try:
        engine = _engine()
    except ValidationError:
        print(_CONFIGURATION_INVALID, file=sys.stderr)
        return 2
    try:
        with Session(engine) as session:
            outcome = authentication.reset_password_for_login(
                session, args.login_name, new_password=password
            )
    except RecoveryOutcomeUnknownError as exc:
        # Raised only when COMMIT itself failed: never "nothing was changed".
        print(exc.message, file=sys.stderr)
        return 2
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (OperationalError, InterfaceError):
        # Before COMMIT (connect or a statement): the transaction is discarded.
        print(_DATABASE_UNAVAILABLE, file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(_DATABASE_ERROR, file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(
        f"The password of {outcome.display_name} (user {outcome.user_id}) was reset. It is"
        " temporary: the user chooses a new one at the next sign-in when that option is on."
        " Every sign-in of this user has ended."
    )
    if not outcome.is_active:
        print("This user is inactive and cannot sign in until reactivated.")
    return 0


def _add_restore_correction_permission_management_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "restore-correction-permission-management",
        help="Let a role manage correction permissions again (recovery).",
        description=(
            "Grant the Manage correction permissions permission to a role while no active"
            " user with a password holds it. The role must be held by an active user with a"
            " password."
        ),
    )
    parser.add_argument("--role-name", required=True, help="The exact name of the role.")
    parser.set_defaults(handler=_run_restore_correction_permission_management)


def _run_restore_correction_permission_management(args: argparse.Namespace) -> int:
    try:
        engine = _engine()
    except ValidationError:
        print(_CONFIGURATION_INVALID, file=sys.stderr)
        return 2
    try:
        with Session(engine) as session:
            outcome = authentication.restore_correction_permission_management(
                session, args.role_name
            )
    except RecoveryOutcomeUnknownError as exc:
        # Raised only when COMMIT itself failed: never "nothing was changed".
        print(exc.message, file=sys.stderr)
        return 2
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (OperationalError, InterfaceError):
        # Before COMMIT (connect or a statement): the transaction is discarded.
        print(_DATABASE_UNAVAILABLE, file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(_RESTORE_DATABASE_ERROR, file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    print(
        f"Role {outcome.role_name} may now manage correction permissions"
        f" ({outcome.holders} active users with a password hold it)."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_reset_password_parser(subparsers)
    _add_restore_correction_permission_management_parser(subparsers)
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
