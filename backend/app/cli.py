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
- ``reset-two-step --login-name NAME [--end-sessions]`` (Phase 17,
  recovery only): turn off the two-step sign-in of a User who lost the
  authenticator app, the passkeys and the recovery codes — all of them are
  deleted, so the User signs in with the password alone and can set it up
  again. The User's sign-ins continue unless ``--end-sessions`` ends them
  all. Audited like an administrator's reset with ``source: cli`` and no
  actor; a User whose two-step sign-in is off is answered so, with
  nothing changed.
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
- ``migrate (--pre-release-backup NAME | --no-backup-reason TEXT)
  [--backup-not-before UTC | --max-backup-age-minutes N] [--backup-dir DIR]
  [--lock-timeout SECONDS]`` (Phase 16 slices 3 and 5): apply this
  release's pending Alembic revisions on one connection in one
  transaction, with the verified pre-release backup (or the reason there
  is none) recorded; refused while another migrate runs, the database
  revision is unknown to this release, a pending revision cannot run in
  one transaction or the API is still connected, and for a backup that is
  missing, fails verification, is stale, is of another revision or
  database, or (with a migration pending) has no not-before proof. Prints
  one JSON document on stdout (also on refusal and failure); the Alembic
  log goes to stderr. 0 upgraded or already current, 1 refused, 2 failed
  or its outcome is unknown.
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
- ``backup-manifest --name NAME --operator TEXT --reason TEXT --release-tag
  TAG …`` (Phase 16 slice 5, ``deploy/production/backup.sh``): publish the
  dump files of ``<backup-dir>/.partial/NAME/`` as the backup directory
  ``<backup-dir>/NAME/`` with its manifest and ``SHA256SUMS``. Reads no
  database setting. 0 published, 1 refused, 2 failed.
- ``backup-verify NAME [--backup-dir DIR] [--expect-database NAME]`` (Phase
  16 slice 5, read-only): verify one backup directory. 0 verified, 1
  invalid, 2 could not read it.
- ``backup-rotate [--keep-hourly N] --keep-daily N --keep-weekly N
  [--keep-pre-purge N] [--backup-dir DIR] [--dry-run]`` (Phase 16 slice 5;
  hourly since the backups of Administration → Backups): of the verified
  scheduled backups (and the daily backups of earlier releases), keep the
  newest of each of the newest N hours, days and ISO weeks and delete the
  others; with ``--keep-pre-purge`` (history archival step 2), also keep
  the newest N verified pre-purge backups and delete the older ones; never
  touches other kinds or invalid backups. 0 rotated, 1 rotated but some
  backups failed verification, 2 failed.
- ``backup-due --interval-hours N [--backup-dir DIR]`` (Phase 16,
  read-only): whether the host's hourly task takes a scheduled backup now.
  Reads no database. 0 due, 1 not due, 2 could not read the directory.
- ``backup-policy`` (Phase 16, read-only): the backup policy of
  Administration → Backups — the interval of the scheduled backups, the
  hourly, daily and weekly backups kept and the pre-purge backups kept — as
  one JSON document (``deploy/production/backup.sh``). 0 read, 2 could not
  read it.
- ``status [--backup-dir DIR] [--max-backup-age-hours N] [--statement-timeout
  SECONDS]`` (Phase 16 slice 6, read-only): the database size, connections
  and lock waits, the Movement count, the schema readiness and the age of
  the newest published backup as one JSON document
  (``deploy/production/check.sh``); without ``--max-backup-age-hours`` the
  age limit is the interval of Administration → Backups plus two hours.
  0 ok, 1 a finding, 2 could not run.
- ``delete-permanently (--work-order NUMBER | --part-number PN)... --operator
  TEXT --reason TEXT [--confirm] [--backup-name NAME] [--lock-timeout
  SECONDS]`` (interim test-data tool; OPERATIONS_RUNBOOK §10): preview, or
  with ``--confirm`` delete, Work Orders and Part Numbers entered only for
  testing in one transaction, keeping one audit row per deleted record;
  refused (nothing deleted) when the data is linked to data that stays.
  Runs as the database owner role (``app.application.permanent_deletion``).
  0 preview shown or deleted, 1 refused, 2 failed or its outcome is unknown.
- ``history-archive (settings | recover | next | export --run N | verify
  --run N | copy --run N | purge --run N [--backup NAME --backup-started-at
  UTC] | status)`` (Phase 16, history archival design B steps 1 and 2;
  ``deploy/production/archive.sh``): the host job of Movement-history
  archival, run as the maintenance database role with the archive folder in
  ``PARTFLOW_ARCHIVE_DIR`` and the optional off-host copy folder in
  ``PARTFLOW_ARCHIVE_COPY_DIR`` (``app.application.history_archive_job``).
  Only ``purge`` deletes: the archived Movements of its run, after the
  pre-purge backup. 0 the step is done (``result`` says what happened), 1
  refused (or, for ``status``, a finding), 2 could not run or its outcome
  is unknown, 3 the run failed and is now FAILED with its reason.

The CLI configures no logging on stdout: stdout carries only the
command's outcome lines (``reconcile``, ``migrate``, ``revision``,
``provision-roles``, ``apply-grants``, ``backup-manifest``, ``backup-verify``,
``backup-rotate``, ``backup-due``, ``backup-policy``, ``status``,
``delete-permanently``, ``history-archive``: their JSON document); refusals
and errors go to stderr.
"""

import argparse
import datetime
import getpass
import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import ArgumentError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application import (
    authentication,
    backups,
    database_roles,
    history_archive_job,
    migration,
    permanent_deletion,
    policies,
    reconciliation,
    system_status,
    two_step,
)
from app.application.errors import ApplicationError, RecoveryOutcomeUnknownError
from app.core.config import DEVELOPMENT_RELEASE, get_settings
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


def _add_reset_two_step_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "reset-two-step",
        help="Turn off a user's two-step sign-in (recovery).",
        description=(
            "Delete the authenticator app, the passkeys and the recovery codes of a user who lost"
            " them, so the user signs in with the password alone and can set up two-step sign-in"
            " again."
            " The user's sign-ins continue unless --end-sessions is given; reset the password"
            " too if the password may be known to someone else."
        ),
    )
    parser.add_argument("--login-name", required=True, help="The user's login name.")
    parser.add_argument(
        "--end-sessions",
        action="store_true",
        help="Also sign the user out everywhere (every open sign-in ends).",
    )
    parser.set_defaults(handler=_run_reset_two_step)


def _run_reset_two_step(args: argparse.Namespace) -> int:
    try:
        engine = _engine()
    except ValidationError:
        print(_CONFIGURATION_INVALID, file=sys.stderr)
        return 2
    try:
        with Session(engine) as session:
            outcome = two_step.reset_for_login(
                session, args.login_name, end_sessions=args.end_sessions
            )
    except RecoveryOutcomeUnknownError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    except ApplicationError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except (OperationalError, InterfaceError):
        print(_DATABASE_UNAVAILABLE, file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print(_DATABASE_ERROR, file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    if outcome.was_on:
        signed_out = (
            " Every sign-in of the user ended."
            if outcome.sessions_ended
            else " The user's sign-ins continue."
        )
        print(
            f"Two-step sign-in of {outcome.display_name} (user {outcome.user_id}) is off: the"
            f" authenticator app, the passkeys and the recovery codes were deleted.{signed_out}"
        )
    else:
        print(
            f"Two-step sign-in of {outcome.display_name} (user {outcome.user_id}) was already"
            " off. Nothing was changed."
        )
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


def _utc_instant(value: str) -> datetime.datetime:
    parsed = backups.parse_utc(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"must be a UTC time YYYY-MM-DDTHH:MM:SSZ, got {value!r}")
    return parsed


def _add_migrate_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("migrate", help=_MIGRATE_HELP, description=_MIGRATE_HELP)
    backup = parser.add_mutually_exclusive_group(required=True)
    backup.add_argument(
        "--pre-release-backup",
        type=_operator_text,
        metavar="NAME",
        help=(
            "The backup directory created by deploy/production/backup.sh that this migration can"
            " be restored from; it is verified before the migration and recorded."
        ),
    )
    backup.add_argument(
        "--no-backup-reason",
        type=_operator_text,
        metavar="TEXT",
        help="Why there is no pre-release backup, e.g. a first install (recorded).",
    )
    freshness = parser.add_mutually_exclusive_group()
    freshness.add_argument(
        "--backup-not-before",
        type=_utc_instant,
        metavar="UTC",
        help=(
            "The backup must have started at or after this instant (YYYY-MM-DDTHH:MM:SSZ): the"
            " write-freeze time. Required when a migration is pending."
        ),
    )
    freshness.add_argument(
        "--max-backup-age-minutes",
        type=_bounded_int(1, 1440),
        metavar="N",
        help=(
            "The backup may be at most N minutes old (1-1440, default"
            f" {backups.DEFAULT_MAX_AGE_MINUTES}); only when no migration is pending."
        ),
    )
    _backup_dir_argument(parser)
    parser.add_argument(
        "--lock-timeout",
        type=_bounded_int(1, 600),
        default=migration.DEFAULT_LOCK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="How long a statement may wait for a table lock (1-600, default %(default)s).",
    )

    def handler(args: argparse.Namespace) -> int:
        if args.pre_release_backup is None and (
            args.backup_not_before is not None or args.max_backup_age_minutes is not None
        ):
            parser.error(
                "--backup-not-before and --max-backup-age-minutes need --pre-release-backup"
            )
        return _run_migrate(args)

    parser.set_defaults(handler=handler)


def _migrate_report(args: argparse.Namespace) -> migration.MigrateReport:
    started_at = datetime.datetime.now(datetime.UTC)
    backup = migration.Backup(
        reference=args.pre_release_backup,
        no_backup_reason=args.no_backup_reason,
        backup_dir=args.backup_dir,
        not_before=args.backup_not_before,
        max_age_minutes=args.max_backup_age_minutes or backups.DEFAULT_MAX_AGE_MINUTES,
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


def _pattern(pattern: re.Pattern[str], rule: str, *, empty_is_none: bool) -> Callable[[str], Any]:
    def parse(value: str) -> str | None:
        if empty_is_none and value == "":
            return None
        if pattern.fullmatch(value) is None:
            raise argparse.ArgumentTypeError(f"must be {rule}, got {value!r}")
        return value

    return parse


def _backup_name(value: str) -> backups.BackupName:
    parsed = backups.parse_name(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            "must be <YYYYMMDDTHHMMSSZ>-scheduled, -daily, -manual,"
            " -pre-release-<release tag> or -pre-purge-<archive name>,"
            f" got {value!r}"
        )
    return parsed


def _backup_text(value: str) -> str:
    try:
        return backups.backup_text(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _backup_dir_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=backups.DEFAULT_BACKUP_DIR,
        metavar="DIR",
        help="The backup directory (default %(default)s).",
    )


_BACKUP_MANIFEST_HELP = (
    "Publish the dump files of <backup-dir>/.partial/NAME/ as the backup <backup-dir>/NAME/"
    " with its manifest and SHA256SUMS (deploy/production/backup.sh). Needs no database."
)


def _add_backup_manifest_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "backup-manifest", help=_BACKUP_MANIFEST_HELP, description=_BACKUP_MANIFEST_HELP
    )
    image = _pattern(backups.IMAGE_ID_PATTERN, "sha256:<64 hex> or empty", empty_is_none=True)
    parser.add_argument("--name", type=_backup_name, required=True, metavar="NAME")
    parser.add_argument("--operator", type=_backup_text, required=True, metavar="TEXT")
    parser.add_argument("--reason", type=_backup_text, required=True, metavar="TEXT")
    parser.add_argument(
        "--release-tag",
        type=_pattern(backups.RELEASE_TAG_PATTERN, "a release tag", empty_is_none=False),
        required=True,
        metavar="TAG",
        help="The release running when the backup was taken.",
    )
    parser.add_argument(
        "--release-commit",
        type=_pattern(backups.COMMIT_PATTERN, "40 lowercase hex or empty", empty_is_none=True),
        metavar="SHA",
    )
    parser.add_argument(
        "--expected-revision",
        type=_pattern(backups.REVISION_PATTERN, "an Alembic revision or empty", empty_is_none=True),
        metavar="REV",
        help="The revision the running release expects (empty when it could not be read).",
    )
    parser.add_argument(
        "--environment",
        type=_pattern(backups.ENVIRONMENT_PATTERN, "a lowercase name", empty_is_none=False),
        default="production",
        metavar="NAME",
        help="Default %(default)s.",
    )
    parser.add_argument(
        "--host",
        type=_pattern(backups.HOST_PATTERN, "1-100 of A-Z a-z 0-9 . _ -", empty_is_none=False),
        default="unknown",
        metavar="TEXT",
        help="Default %(default)s.",
    )
    parser.add_argument("--image-backend", type=image, metavar="ID")
    parser.add_argument("--image-web", type=image, metavar="ID")
    parser.add_argument("--image-db", type=image, metavar="ID")
    _backup_dir_argument(parser)
    parser.set_defaults(handler=_run_backup_manifest)


def _tool_identity() -> tuple[str | None, str | None]:
    """The RELEASE_TAG / RELEASE_COMMIT of this image (no Settings: no database configuration)."""
    tag = os.environ.get("RELEASE_TAG") or DEVELOPMENT_RELEASE
    commit = os.environ.get("RELEASE_COMMIT") or None
    return (
        tag if backups.RELEASE_TAG_PATTERN.fullmatch(tag) else None,
        commit if commit is not None and backups.COMMIT_PATTERN.fullmatch(commit) else None,
    )


def _run_backup_manifest(args: argparse.Namespace) -> int:
    tool_tag, tool_commit = _tool_identity()
    request = backups.ManifestRequest(
        name=args.name,
        operator=args.operator,
        reason=args.reason,
        release_tag=args.release_tag,
        release_commit=args.release_commit,
        expected_revision=args.expected_revision,
        environment=args.environment,
        host=args.host,
        image_backend=args.image_backend,
        image_web=args.image_web,
        image_db=args.image_db,
        tool_tag=tool_tag,
        tool_commit=tool_commit,
    )
    report = backups.publish_backup(args.backup_dir, request)
    _print_document(backups.publish_document(report))
    _print_backup_traceback(report.error)
    print(backups.publish_summary(report), file=sys.stderr)
    return report.exit_code


def _print_backup_traceback(error: backups.BackupError | None) -> None:
    if error is not None and error.code == "internal_error" and error.exception is not None:
        traceback.print_exception(error.exception, file=sys.stderr)


_BACKUP_VERIFY_HELP = (
    "Verify one backup directory (checksums, manifest, dump header, table of contents) and"
    " print a JSON report. Never changes anything."
)


def _add_backup_verify_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "backup-verify", help=_BACKUP_VERIFY_HELP, description=_BACKUP_VERIFY_HELP
    )
    parser.add_argument("name", metavar="NAME", help="The backup directory name.")
    _backup_dir_argument(parser)
    parser.add_argument(
        "--expect-database",
        metavar="NAME",
        help="Also require the backup to be of this database.",
    )
    parser.set_defaults(handler=_run_backup_verify)


def _run_backup_verify(args: argparse.Namespace) -> int:
    result = backups.verify_backup(args.backup_dir, args.name, expect_database=args.expect_database)
    _print_document(backups.verify_document(result))
    _print_backup_traceback(result.error)
    print(backups.verify_summary(result), file=sys.stderr)
    return result.exit_code


_BACKUP_ROTATE_HELP = (
    "Of the verified scheduled backups (and the daily backups of earlier releases), keep the"
    " newest of each of the newest N hours, days and ISO weeks (UTC); delete the others. With"
    " --keep-pre-purge, also keep the newest N verified pre-purge backups and delete the older"
    " ones. Never touches pre-release, manual or invalid backups."
)


def _add_backup_rotate_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "backup-rotate", help=_BACKUP_ROTATE_HELP, description=_BACKUP_ROTATE_HELP
    )
    parser.add_argument(
        "--keep-hourly",
        type=_bounded_int(0, 720),
        default=0,
        metavar="N",
        help="The newest backup of each of the newest N hours is kept (0-720, default 0).",
    )
    parser.add_argument(
        "--keep-daily",
        type=_bounded_int(1, 3650),
        required=True,
        metavar="N",
        help="The newest backup of each of the newest N days is kept (1-3650).",
    )
    parser.add_argument(
        "--keep-weekly",
        type=_bounded_int(0, 520),
        required=True,
        metavar="N",
        help="The newest backup of each of the newest N ISO weeks is kept (0-520).",
    )
    low, high = policies.BACKUP_KEEP_PRE_PURGE_RANGE
    parser.add_argument(
        "--keep-pre-purge",
        type=_bounded_int(low, high),
        default=None,
        metavar="N",
        help=(
            "The newest N verified pre-purge backups are kept and older ones deleted"
            f" ({low}-{high}; default: none is touched)."
        ),
    )
    _backup_dir_argument(parser)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be removed; change nothing."
    )
    parser.set_defaults(handler=_run_backup_rotate)


def _run_backup_rotate(args: argparse.Namespace) -> int:
    report = backups.rotate_backups(
        args.backup_dir,
        keep_hourly=args.keep_hourly,
        keep_daily=args.keep_daily,
        keep_weekly=args.keep_weekly,
        keep_pre_purge=args.keep_pre_purge,
        dry_run=args.dry_run,
    )
    _print_document(backups.rotate_document(report))
    _print_backup_traceback(report.error)
    print(backups.rotate_summary(report), file=sys.stderr)
    return report.exit_code


_BACKUP_DUE_HELP = (
    "Report whether a scheduled backup is due: none was published yet, or the newest started"
    " at least the interval ago (less ten minutes). Never changes anything; reads no database."
)


def _add_backup_due_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "backup-due", help=_BACKUP_DUE_HELP, description=_BACKUP_DUE_HELP
    )
    parser.add_argument(
        "--interval-hours",
        type=int,
        choices=policies.BACKUP_INTERVALS,
        required=True,
        metavar="N",
        help="The interval of Administration → Backups: 1, 2, 3, 4, 6, 8, 12 or 24 hours.",
    )
    _backup_dir_argument(parser)
    parser.set_defaults(handler=_run_backup_due)


def _run_backup_due(args: argparse.Namespace) -> int:
    report = backups.check_due(args.backup_dir, interval_hours=args.interval_hours)
    _print_document(backups.due_document(report))
    _print_backup_traceback(report.error)
    print(backups.due_summary(report), file=sys.stderr)
    return report.exit_code


_BACKUP_POLICY_HELP = (
    "Print the backup policy of Administration → Backups — the interval of the scheduled"
    " backups, the hourly, daily and weekly backups kept and the pre-purge backups kept — as a"
    " JSON report. Never changes data."
)
_BACKUP_POLICY_UNREACHABLE = (
    "The backup policy could not be read: PartFlow could not reach its database."
)
_BACKUP_POLICY_REFUSED = (
    "The backup policy could not be read (database error). Check that the database is at this"
    " release's revision (python -m app.cli revision)."
)


def _add_backup_policy_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "backup-policy", help=_BACKUP_POLICY_HELP, description=_BACKUP_POLICY_HELP
    )
    parser.set_defaults(handler=_run_backup_policy)


def _backup_policy_document(
    policy: policies.BackupPolicy | None, error: tuple[str, str] | None
) -> dict[str, object]:
    return {
        "report_version": 1,
        "command": "backup-policy",
        "result": "failed" if policy is None else "read",
        "exit_code": 2 if policy is None else 0,
        "interval_hours": None if policy is None else policy.interval_hours,
        "keep_hourly": None if policy is None else policy.keep_hourly,
        "keep_daily": None if policy is None else policy.keep_daily,
        "keep_weekly": None if policy is None else policy.keep_weekly,
        "keep_pre_purge": None if policy is None else policy.keep_pre_purge,
        "error": None if error is None else {"code": error[0], "message": error[1]},
    }


def _read_backup_policy() -> tuple[policies.BackupPolicy | None, tuple[str, str] | None]:
    try:
        engine = _engine()
    except (ValidationError, ArgumentError, ValueError):
        return None, ("configuration_invalid", _CONFIGURATION_INVALID)
    try:
        with Session(engine) as session:
            return policies.read_backup_policy(session), None
    except (OperationalError, InterfaceError):
        return None, ("database_unavailable", _BACKUP_POLICY_UNREACHABLE)
    except SQLAlchemyError:
        return None, ("database_error", _BACKUP_POLICY_REFUSED)
    finally:
        engine.dispose()


def _run_backup_policy(args: argparse.Namespace) -> int:
    policy, error = _read_backup_policy()
    _print_document(_backup_policy_document(policy, error))
    if policy is None:
        message = error[1] if error is not None else "The backup policy could not be read."
        print(f"backup-policy: {message}", file=sys.stderr)
        return 2
    every = "hour" if policy.interval_hours == 1 else f"{policy.interval_hours} hours"
    print(
        f"backup-policy: a scheduled backup every {every}; keep {policy.keep_hourly} hourly,"
        f" {policy.keep_daily} daily and {policy.keep_weekly} weekly, and"
        f" {policy.keep_pre_purge} pre-purge backups",
        file=sys.stderr,
    )
    return 0


_STATUS_HELP = (
    "Report database size, Movement count, schema readiness and backup age as JSON. Never"
    " changes data."
)


def _add_status_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("status", help=_STATUS_HELP, description=_STATUS_HELP)
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="The backup directory whose newest backup is reported (default: none).",
    )
    parser.add_argument(
        "--max-backup-age-hours",
        type=_bounded_int(1, 720),
        default=None,
        metavar="N",
        help=(
            "A newest backup older than N hours is a finding (1-720; default: the interval of"
            " Administration → Backups plus 2 hours)."
        ),
    )
    parser.add_argument(
        "--statement-timeout",
        type=_bounded_int(1, 600),
        default=system_status.DEFAULT_STATEMENT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "Per-statement timeout in seconds (1-600, default %(default)s); keep below 30 so a"
            " release's migrate is never aborted by this run."
        ),
    )
    parser.set_defaults(handler=_run_status)


def _status_report(args: argparse.Namespace) -> system_status.StatusReport:
    options: dict[str, Any] = {
        "backup_dir": args.backup_dir,
        "max_backup_age_hours": args.max_backup_age_hours,
        "statement_timeout_seconds": args.statement_timeout,
    }
    try:
        settings = get_settings()
        engine = build_engine(
            settings.database_url,
            application_name=schema_revision.CLI_APPLICATION_NAME,
            connect_timeout=system_status.CONNECT_TIMEOUT_SECONDS,
        )
    except (ValidationError, ArgumentError, ValueError):
        # No valid configuration: the backups are still reported.
        tag, commit = _tool_identity()
        return system_status.collect_status(
            None, release_tag=tag, release_commit=commit, accepted_revision=None, **options
        )
    try:
        return system_status.collect_status(
            engine,
            release_tag=settings.release_tag,
            release_commit=settings.release_commit,
            accepted_revision=settings.accept_schema_revision,
            **options,
        )
    finally:
        engine.dispose()


def _run_status(args: argparse.Namespace) -> int:
    report = _status_report(args)
    _print_document(system_status.status_document(report))
    if (
        report.error is not None
        and report.error.code == "internal_error"
        and report.error.exception is not None
    ):
        traceback.print_exception(report.error.exception, file=sys.stderr)
    print(system_status.status_summary(report), file=sys.stderr)
    return report.exit_code


_DELETE_PERMANENTLY_HELP = (
    "Preview, or with --confirm delete, Work Orders and Part Numbers entered only for testing,"
    " as if they had never been entered (one audit row per deleted record). Runs as the"
    " database owner role; refused when the data is linked to data that stays."
)


def _work_order_number(value: str) -> str:
    """A Work Order Number as PartFlow stores it: matched verbatim, never trimmed."""
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    if "\x00" in value:
        raise argparse.ArgumentTypeError("must not contain a NUL character")
    return value


def _part_number(value: str) -> str:
    """The canonical Part Number, by PartFlow's own normalization."""
    try:
        return permanent_deletion.requested_part_number(value)
    except ApplicationError as exc:
        raise argparse.ArgumentTypeError(exc.message) from None


def _backup_name_text(value: str) -> str:
    _backup_name(value)
    return value


def _add_delete_permanently_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "delete-permanently", help=_DELETE_PERMANENTLY_HELP, description=_DELETE_PERMANENTLY_HELP
    )
    parser.add_argument(
        "--work-order",
        dest="work_orders",
        action="append",
        default=[],
        type=_work_order_number,
        metavar="NUMBER",
        help="A Work Order Number, exactly as stored (repeatable).",
    )
    parser.add_argument(
        "--part-number",
        dest="part_numbers",
        action="append",
        default=[],
        type=_part_number,
        metavar="PN",
        help="A Part Number (repeatable); every Work Order with a line of it must be deleted too.",
    )
    parser.add_argument(
        "--operator", type=_operator_text, required=True, metavar="TEXT", help="Who runs it."
    )
    parser.add_argument(
        "--reason", type=_operator_text, required=True, metavar="TEXT", help="Why (recorded)."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Delete. Without it the command only shows what it would delete.",
    )
    parser.add_argument(
        "--backup-name",
        type=_backup_name_text,
        metavar="NAME",
        help="The backup taken before the deletion (recorded in the audit rows).",
    )
    parser.add_argument(
        "--lock-timeout",
        type=_bounded_int(1, 600),
        default=migration.DEFAULT_LOCK_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="How long it may wait for a table lock (1-600, default %(default)s).",
    )

    def handler(args: argparse.Namespace) -> int:
        if not args.work_orders and not args.part_numbers:
            parser.error("give at least one --work-order or --part-number")
        return _run_delete_permanently(args)

    parser.set_defaults(handler=handler)


def _delete_permanently_report(args: argparse.Namespace) -> permanent_deletion.DeletionReport:
    request = permanent_deletion.DeletionRequest(
        work_order_numbers=tuple(args.work_orders),
        part_numbers=tuple(args.part_numbers),
        operator=args.operator,
        reason=args.reason,
        confirm=args.confirm,
        backup_name=args.backup_name,
    )
    try:
        get_settings()
        engine = _engine()
    except (ValidationError, ArgumentError, ValueError):
        report = permanent_deletion.DeletionReport(
            started_at=datetime.datetime.now(datetime.UTC), request=request
        )
        return permanent_deletion.deletion_failure(
            report, "configuration_invalid", _CONFIGURATION_INVALID
        )
    try:
        return permanent_deletion.run_deletion(
            engine, request, lock_timeout_seconds=args.lock_timeout
        )
    finally:
        engine.dispose()


def _run_delete_permanently(args: argparse.Namespace) -> int:
    report = _delete_permanently_report(args)
    _print_document(permanent_deletion.deletion_document(report))
    _print_error_traceback(report.error)
    print(permanent_deletion.deletion_summary(report), file=sys.stderr)
    return report.exit_code


_HISTORY_ARCHIVE_HELP = (
    "The host job of Movement-history archival (deploy/production/archive.sh), run as the"
    " maintenance database role with the archive folder in PARTFLOW_ARCHIVE_DIR and the"
    " optional off-host copy folder in PARTFLOW_ARCHIVE_COPY_DIR. Only purge deletes, and only"
    " the archived Movements of its run."
)
_HISTORY_ARCHIVE_COMMANDS: dict[str, str] = {
    "settings": "Print what the host script needs (the Start minute of the hourly work).",
    "recover": (
        "Right after the host script took its lock: make a stale RUNNING run FAILED, remove the"
        " unfinished entries of finished runs and copy missing purge receipts. A PURGING run is"
        " left for purge to resume."
    ),
    "next": "Print the run the job must work on and what to run for it.",
    "export": "Export an APPROVED run's archive into .partial and record what it holds.",
    "verify": "Verify the exported archive and publish its folder.",
    "copy": "Copy the archive off the host, or accept a confirmation, or wait for one.",
    "purge": (
        "Purge the archived Movements of a copied run after its pre-purge backup (--backup and"
        " --backup-started-at), or resume a PURGING run (no backup)."
    ),
    "status": "Report the open run, the last finished run and the archive folder's space.",
}


def _history_archive_engine() -> Engine | None:
    """The CLI's engine, or None without a valid database configuration."""
    try:
        return _engine()
    except (ValidationError, ArgumentError, ValueError):
        return None


def _run_number(value: str) -> int:
    return _bounded_int(1, 2_147_483_647)(value)


def _add_history_archive_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "history-archive", help=_HISTORY_ARCHIVE_HELP, description=_HISTORY_ARCHIVE_HELP
    )
    steps = parser.add_subparsers(dest="step", required=True)
    for step, text in _HISTORY_ARCHIVE_COMMANDS.items():
        step_parser = steps.add_parser(step, help=text, description=text)
        if step in ("export", "verify", "copy", "purge"):
            step_parser.add_argument(
                "--run", type=_run_number, required=True, metavar="N", help="The run number."
            )
        if step == "purge":
            # Plain text: the job judges the backup and answers a refusal as JSON.
            step_parser.add_argument(
                "--backup",
                metavar="NAME",
                help="The pre-purge backup: <YYYYMMDDTHHMMSSZ>-pre-purge-<archive name>.",
            )
            step_parser.add_argument(
                "--backup-started-at",
                metavar="UTC",
                help="Its dump start as backup-verify reports it: YYYY-MM-DDTHH:MM:SSZ.",
            )
            step_parser.set_defaults(purge_parser=step_parser)
    parser.set_defaults(handler=_run_history_archive)


def _purge_backup(args: argparse.Namespace) -> history_archive_job.PurgeBackup | None:
    if (args.backup is None) != (args.backup_started_at is None):
        args.purge_parser.error("give --backup and --backup-started-at together, or neither")
    if args.backup is None:
        return None
    return history_archive_job.PurgeBackup(args.backup, args.backup_started_at)


def _run_history_archive(args: argparse.Namespace) -> int:
    folders = history_archive_job.environment_folders()
    backup = _purge_backup(args) if args.step == "purge" else None
    engine = _history_archive_engine()
    try:
        if args.step == "settings":
            report = history_archive_job.settings(engine, folders)
        elif args.step == "recover":
            report = history_archive_job.recover(engine, folders)
        elif args.step == "next":
            report = history_archive_job.next_run(engine, folders)
        elif args.step == "export":
            report = history_archive_job.export(engine, folders, args.run)
        elif args.step == "verify":
            report = history_archive_job.verify(engine, folders, args.run)
        elif args.step == "copy":
            report = history_archive_job.copy(engine, folders, args.run)
        elif args.step == "purge":
            report = history_archive_job.purge(engine, folders, args.run, backup)
        else:
            report = history_archive_job.status(engine, folders)
    finally:
        if engine is not None:
            engine.dispose()
    _print_document(history_archive_job.job_document(report))
    if report.error is not None and report.error.exception is not None:
        traceback.print_exception(report.error.exception, file=sys.stderr)
    print(history_archive_job.job_summary(report), file=sys.stderr)
    return report.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_reset_password_parser(subparsers)
    _add_reset_two_step_parser(subparsers)
    _add_restore_correction_permission_management_parser(subparsers)
    _add_reconcile_parser(subparsers)
    _add_migrate_parser(subparsers)
    _add_revision_parser(subparsers)
    _add_provision_roles_parser(subparsers)
    _add_apply_grants_parser(subparsers)
    _add_backup_manifest_parser(subparsers)
    _add_backup_verify_parser(subparsers)
    _add_backup_rotate_parser(subparsers)
    _add_backup_due_parser(subparsers)
    _add_backup_policy_parser(subparsers)
    _add_status_parser(subparsers)
    _add_delete_permanently_parser(subparsers)
    _add_history_archive_parser(subparsers)
    args = parser.parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
