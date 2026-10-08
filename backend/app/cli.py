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
- ``reconcile [--check ID]... [--statement-timeout S] [--max-findings N]``
  (Phase 16 slice 1, read-only): run the reconciliation checks in one
  read-only snapshot and print one JSON report on stdout (also when it
  could not run); 0 clean, 1 mismatch, 2 could not run. Never repairs.

The CLI configures no logging on stdout: stdout carries only the
command's outcome lines (``reconcile``: its JSON report); refusals and
errors go to stderr.
"""

import argparse
import datetime
import getpass
import json
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import ArgumentError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application import authentication, reconciliation
from app.application.errors import ApplicationError, RecoveryOutcomeUnknownError
from app.core.config import get_settings
from app.infrastructure.database import build_engine

_DATABASE_UNAVAILABLE = "PartFlow could not reach its database. Nothing was changed."
_DATABASE_ERROR = "The database refused the reset (internal error). Nothing was changed."
_RESTORE_DATABASE_ERROR = "The database refused the grant (internal error). Nothing was changed."
_CONFIGURATION_INVALID = (
    "PartFlow is not configured: check DATABASE_URL, or DATABASE_HOST, DATABASE_NAME,"
    " DATABASE_USER and DATABASE_PASSWORD_FILE. Nothing was changed."
)
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


_RECONCILE_HELP = (
    "Run the read-only reconciliation checks and print a JSON report. Never changes data."
)


def _bounded_int(low: int, high: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"must be a whole number, got {value!r}") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}, got {number}")
        return number

    return parse


def _add_reconcile_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("reconcile", help=_RECONCILE_HELP, description=_RECONCILE_HELP)
    parser.add_argument(
        "--check",
        action="append",
        choices=reconciliation.CHECK_IDS,
        help="Run only this check (repeatable); the others are reported as skipped.",
    )
    parser.add_argument(
        "--statement-timeout",
        type=_bounded_int(1, 3600),
        default=reconciliation.DEFAULT_STATEMENT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="Per-statement timeout in seconds (1-3600, default %(default)s).",
    )
    parser.add_argument(
        "--max-findings",
        type=_bounded_int(1, 10000),
        default=reconciliation.DEFAULT_MAX_FINDINGS,
        metavar="N",
        help="Findings listed per check (1-10000, default %(default)s); the count stays full.",
    )
    parser.set_defaults(handler=_run_reconcile)


def _code_alembic_head() -> str | None:
    """The Alembic head this code ships (None when it cannot be read)."""
    try:
        return ScriptDirectory(
            str(Path(__file__).resolve().parents[1] / "alembic")
        ).get_current_head()
    except (CommandError, OSError):
        return None


def _reconcile_summary(report: reconciliation.ReconciliationReport) -> str:
    if report.error is not None:
        return f"reconcile: {report.error.message}"
    document = reconciliation.report_document(report)
    findings = sum(check.finding_count for check in report.checks)
    milliseconds = document["duration_ms"]
    errors = [check.id for check in report.checks if check.status == "error"]
    if errors:
        return (
            f"reconcile: ERROR in check(s) {', '.join(errors)}; the report is incomplete."
            f" ({findings} findings) in {milliseconds} ms"
        )
    failed = [check.id for check in report.checks if check.status == "fail"]
    if failed:
        return (
            f"reconcile: MISMATCH in check(s) {', '.join(failed)} ({findings} findings)"
            f" in {milliseconds} ms"
        )
    run = sum(1 for check in report.checks if check.status != "skipped")
    return (
        f"reconcile: clean ({run} of {len(reconciliation.CHECK_IDS)} checks run, 0 findings)"
        f" in {milliseconds} ms"
    )


def _reconcile_report(
    started_at: datetime.datetime, options: dict[str, Any]
) -> reconciliation.ReconciliationReport:
    """Always a complete report: a run that cannot start is an error report (exit 2)."""
    try:
        options["expected_alembic_revision"] = _code_alembic_head()
        try:
            engine = _engine()
        except (ValidationError, ArgumentError, ValueError):
            # No valid database configuration (DATABASE_URL missing or
            # malformed — an unknown dialect, an unparseable URL or port — or
            # an invalid file-based form). The report never repeats the URL.
            return reconciliation.error_report(
                "configuration_invalid", started_at=started_at, **options
            )
        try:
            return reconciliation.run_reconciliation(engine, **options)
        finally:
            engine.dispose()
    except Exception as exc:
        # Any escaping error still yields a complete report (exit 2).
        return reconciliation.error_report(
            "internal_error", started_at=started_at, exception=exc, **options
        )


def _run_reconcile(args: argparse.Namespace) -> int:
    started_at = datetime.datetime.now(datetime.UTC)
    options: dict[str, Any] = {
        "checks": args.check or reconciliation.CHECK_IDS,
        "statement_timeout_seconds": args.statement_timeout,
        "max_findings": args.max_findings,
        "expected_alembic_revision": None,
    }
    report = _reconcile_report(started_at, options)
    sys.stdout.write(
        json.dumps(reconciliation.report_document(report), indent=2, ensure_ascii=True) + "\n"
    )
    if (
        report.error is not None
        and report.error.code == "internal_error"
        and report.error.exception is not None
    ):
        traceback.print_exception(report.error.exception, file=sys.stderr)
    print(_reconcile_summary(report), file=sys.stderr)
    return reconciliation.result_of(report)[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_reset_password_parser(subparsers)
    _add_restore_correction_permission_management_parser(subparsers)
    _add_reconcile_parser(subparsers)
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
