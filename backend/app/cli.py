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
- ``migrate (--pre-release-backup REF | --no-backup-reason TEXT)
  [--lock-timeout SECONDS]`` (Phase 16 slice 3): apply this release's
  pending Alembic revisions on one connection in one transaction, with
  the backup reference (or the reason there is none) recorded; refused
  while another migrate runs, the database revision is unknown to this
  release, a pending revision cannot run in one transaction or the API is
  still connected. Prints one JSON document on stdout (also on refusal
  and failure); the Alembic log goes to stderr. 0 upgraded or already
  current, 1 refused, 2 failed or its outcome is unknown.
- ``revision`` (Phase 16 slice 3, read-only): the release identity, the
  expected and database revisions, the pending revisions and the
  readiness the backend would report, as one JSON document; 0 current,
  1 any other state, 2 could not run.
- ``provision-roles [--app-password-file PATH] [--maintenance-password-file
  PATH] [--lock-timeout SECONDS]`` (Phase 16 slice 4): create or repair the
  ``partflow_app`` and ``partflow_maintenance`` database roles and set their
  passwords from the secret files (read and checked before connecting);
  runs as the database owner role. 0 provisioned, 1 refused, 2 failed or
  its outcome is unknown.
- ``apply-grants [--lock-timeout SECONDS]`` (Phase 16 slice 4): derive every
  grant of the two database roles at this release's revision (also part of
  every ``migrate``); runs as the database owner role. 0 applied, 1
  refused, 2 failed or its outcome is unknown.

The CLI configures no logging on stdout: stdout carries only the
command's outcome lines (``reconcile``, ``migrate``, ``revision``,
``provision-roles``, ``apply-grants``: their JSON document); refusals and errors go to stderr.
"""

import argparse
import datetime
import getpass
import json
import logging
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import ArgumentError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application import authentication, database_roles, migration, reconciliation
from app.application.errors import ApplicationError, RecoveryOutcomeUnknownError
from app.core.config import get_settings
from app.infrastructure import schema_revision
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
    return build_engine(
        get_settings().database_url, application_name=schema_revision.CLI_APPLICATION_NAME
    )


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
        return schema_revision.code_head()
    except schema_revision.MigrationScriptsError:
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


def _print_document(document: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=True) + "\n")


def _print_error_traceback(error: migration.RunError | None) -> None:
    if (
        error is not None
        and error.code in ("internal_error", "migration_failed")
        and error.exception is not None
    ):
        traceback.print_exception(error.exception, file=sys.stderr)


def _operator_text(value: str) -> str:
    try:
        return migration.operator_text(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


_MIGRATE_HELP = (
    "Apply this release's pending database migrations in one transaction and print a JSON"
    " report. Run it only during the write freeze (backend stopped)."
)


def _add_migrate_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("migrate", help=_MIGRATE_HELP, description=_MIGRATE_HELP)
    backup = parser.add_mutually_exclusive_group(required=True)
    backup.add_argument(
        "--pre-release-backup",
        type=_operator_text,
        metavar="REF",
        help="The pre-release backup this migration can be restored from (recorded).",
    )
    backup.add_argument(
        "--no-backup-reason",
        type=_operator_text,
        metavar="TEXT",
        help="Why there is no pre-release backup, e.g. a first install (recorded).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=_bounded_int(1, 600),
        default=migration.DEFAULT_LOCK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="How long a statement may wait for a table lock (1-600, default %(default)s).",
    )
    parser.set_defaults(handler=_run_migrate)


def _migrate_report(args: argparse.Namespace) -> migration.MigrateReport:
    started_at = datetime.datetime.now(datetime.UTC)
    backup = migration.Backup(
        reference=args.pre_release_backup, no_backup_reason=args.no_backup_reason
    )
    try:
        settings = get_settings()
        engine = _engine()
    except (ValidationError, ArgumentError, ValueError):
        # No valid configuration; the report never repeats a setting.
        report = migration.MigrateReport(
            started_at=started_at, release=None, commit=None, backup=backup
        )
        report.expected_revision = _code_alembic_head()
        return migration.migrate_failure(
            report, "configuration_invalid", message=_CONFIGURATION_INVALID
        )
    try:
        return migration.run_migrate(
            engine,
            backup=backup,
            lock_timeout_seconds=args.lock_timeout,
            release=settings.release_tag,
            commit=settings.release_commit,
            started_at=started_at,
        )
    finally:
        engine.dispose()


def _run_migrate(args: argparse.Namespace) -> int:
    # The Alembic log ("Running upgrade ...") goes to stderr for this command only.
    alembic_logger = logging.getLogger("alembic")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)-5.5s [%(name)s] %(message)s"))
    saved_level, saved_propagate = alembic_logger.level, alembic_logger.propagate
    alembic_logger.addHandler(handler)
    alembic_logger.setLevel(logging.INFO)
    alembic_logger.propagate = False
    try:
        report = _migrate_report(args)
    finally:
        alembic_logger.removeHandler(handler)
        alembic_logger.setLevel(saved_level)
        alembic_logger.propagate = saved_propagate
    _print_document(migration.migrate_document(report))
    _print_error_traceback(report.error)
    print(migration.migrate_summary(report), file=sys.stderr)
    return report.exit_code


_REVISION_HELP = (
    "Print this release's expected and the database's Alembic revision, the pending"
    " revisions and the readiness as a JSON report. Never changes data."
)


def _add_revision_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("revision", help=_REVISION_HELP, description=_REVISION_HELP)
    parser.set_defaults(handler=_run_revision)


def _revision_report() -> migration.RevisionReport:
    try:
        settings = get_settings()
        engine = _engine()
    except (ValidationError, ArgumentError, ValueError):
        report = migration.RevisionReport(release=None, commit=None, accepted_revision=None)
        report.expected_revision = _code_alembic_head()
        report.error = migration.RunError("configuration_invalid", _CONFIGURATION_INVALID)
        return report
    try:
        return migration.revision_report(
            engine,
            release=settings.release_tag,
            commit=settings.release_commit,
            accepted_revision=settings.accept_schema_revision,
        )
    finally:
        engine.dispose()


def _run_revision(args: argparse.Namespace) -> int:
    report = _revision_report()
    _print_document(migration.revision_document(report))
    _print_error_traceback(report.error)
    print(migration.revision_summary(report), file=sys.stderr)
    return report.exit_code


_PROVISION_ROLES_HELP = (
    "Create or repair the partflow_app and partflow_maintenance database roles and set their"
    " passwords from the secret files; print a JSON report. Runs as the database owner role."
)
_APP_PASSWORD_FILE = Path("/run/secrets/partflow_app_password")
_MAINTENANCE_PASSWORD_FILE = Path("/run/secrets/partflow_maintenance_password")


def _add_provision_roles_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "provision-roles", help=_PROVISION_ROLES_HELP, description=_PROVISION_ROLES_HELP
    )
    parser.add_argument(
        "--app-password-file",
        type=Path,
        default=_APP_PASSWORD_FILE,
        metavar="PATH",
        help="The partflow_app password file (default %(default)s).",
    )
    parser.add_argument(
        "--maintenance-password-file",
        type=Path,
        default=_MAINTENANCE_PASSWORD_FILE,
        metavar="PATH",
        help="The partflow_maintenance password file (default %(default)s).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=_bounded_int(1, 600),
        default=migration.DEFAULT_LOCK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="How long a statement may wait for a lock (1-600, default %(default)s).",
    )
    parser.set_defaults(handler=_run_provision_roles)


def _provision_roles_report(args: argparse.Namespace) -> migration.ProvisionReport:
    report = migration.ProvisionReport(started_at=datetime.datetime.now(datetime.UTC))
    roles = database_roles.configured_roles()
    try:
        # Both files before any setting or connection.
        passwords = migration.read_role_passwords(
            roles, args.app_password_file, args.maintenance_password_file
        )
    except migration.PasswordFileError as exc:
        return migration.provision_failure(report, exc.code, exc.message)
    try:
        get_settings()
        engine = _engine()
    except (ValidationError, ArgumentError, ValueError):
        return migration.provision_failure(report, "configuration_invalid", _CONFIGURATION_INVALID)
    try:
        return migration.run_provision_roles(
            engine,
            report,
            roles=roles,
            passwords=passwords,
            lock_timeout_seconds=args.lock_timeout,
        )
    finally:
        engine.dispose()


def _run_provision_roles(args: argparse.Namespace) -> int:
    report = _provision_roles_report(args)
    _print_document(migration.provision_document(report))
    _print_error_traceback(report.error)
    print(migration.provision_summary(report), file=sys.stderr)
    return report.exit_code


_APPLY_GRANTS_HELP = (
    "Apply the database-role grants of this release (also part of every migrate) and print a"
    " JSON report. Runs as the database owner role; safe while the backend runs."
)


def _add_apply_grants_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "apply-grants", help=_APPLY_GRANTS_HELP, description=_APPLY_GRANTS_HELP
    )
    parser.add_argument(
        "--lock-timeout",
        type=_bounded_int(1, 600),
        default=migration.DEFAULT_LOCK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="How long a statement may wait for a lock (1-600, default %(default)s).",
    )
    parser.set_defaults(handler=_run_apply_grants)


def _apply_grants_report(args: argparse.Namespace) -> migration.GrantsReport:
    report = migration.GrantsReport(started_at=datetime.datetime.now(datetime.UTC), release=None)
    try:
        settings = get_settings()
        engine = _engine()
    except (ValidationError, ArgumentError, ValueError):
        report.expected_revision = _code_alembic_head()
        return migration.grants_failure(report, "configuration_invalid", _CONFIGURATION_INVALID)
    report.release = settings.release_tag
    try:
        return migration.run_apply_grants(
            engine,
            report,
            roles=database_roles.configured_roles(),
            lock_timeout_seconds=args.lock_timeout,
        )
    finally:
        engine.dispose()


def _run_apply_grants(args: argparse.Namespace) -> int:
    report = _apply_grants_report(args)
    _print_document(migration.grants_document(report))
    _print_error_traceback(report.error)
    print(migration.grants_summary(report), file=sys.stderr)
    return report.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_reset_password_parser(subparsers)
    _add_restore_correction_permission_management_parser(subparsers)
    _add_reconcile_parser(subparsers)
    _add_migrate_parser(subparsers)
    _add_revision_parser(subparsers)
    _add_provision_roles_parser(subparsers)
    _add_apply_grants_parser(subparsers)
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
