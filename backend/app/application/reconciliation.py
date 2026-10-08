"""Read-only reconciliation checks (Phase 16 slice 1; OPERATIONS_RUNBOOK §7).

One run executes the selected checks (a)–(j) inside ONE read-only
PostgreSQL snapshot and returns a report; it never repairs anything —
every finding is an owner decision (PLAN §4, RUNBOOK §7).

Transaction protocol (order matters): ``SET TRANSACTION ISOLATION
LEVEL REPEATABLE READ, READ ONLY`` and the ``SET LOCAL`` timeouts are
utility statements that take no snapshot; then every table the
selected checks read is locked ``IN ACCESS SHARE MODE`` (the mode a
plain SELECT takes) BEFORE the first SELECT, because PostgreSQL freezes
the snapshot at the first query and a TRUNCATE or table rewrite
committed between that snapshot and a later implicit lock would not be
MVCC-safe. Every production command commits its Movements and
projections in one transaction, so one snapshot sees each command
entirely or not at all. No row lock, no ``FOR UPDATE``/``SHARE``, no
advisory lock (verified at the end of the run): reconciliation never
joins the production lock order.

Each check runs in its own savepoint, so a statement timeout, a lock
timeout or another database error marks that check ``error`` and the
run continues; an unexpected error stops the run and keeps the
completed results. The checks reuse the replay functions of the
modules that own each projection (``projections``, ``allocations``,
``production_release``, ``machines``, ``work_orders``) and add only
comparators and set-based SQL.

Check (j) re-evaluates the canonical identities under the RUNNING
interpreter (Unicode tables) and the RUNNING database server (its libc,
the database collation and ctype) — the platform-upgrade identity
check. Check (g) reports ``not_applicable`` until Movement history
archival exists. Check (h) (Phase 16 slice 4) verifies guard integrity —
guard triggers and functions, database-role privileges and attributes,
``session_replication_role`` — through ``database_roles.guard_integrity``;
it reports ``not_applicable`` on a database without the PartFlow
database roles (development, test, staging).
"""

import datetime
import platform
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any, Final, NamedTuple

import psycopg.errors
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, PendingRollbackError
from sqlalchemy.orm import Session

from app.application import (
    allocations,
    environment,
    machines,
    production_release,
    projections,
    work_orders,
)
from app.application.database_roles import DatabaseRoles, configured_roles, guard_integrity
from app.application.errors import ConflictError
from app.core.config import get_settings
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    LineageRelation,
    MovementType,
    QuantityFlowStatus,
    RouteMode,
    WorkOrderStatus,
)
from app.domain.part_number import InvalidPartNumberError, normalize_part_number
from app.domain.worker_badge import InvalidBadgeBarcodeError, normalize_badge_barcode
from app.infrastructure.database_privileges import GUARD_TABLES
from app.infrastructure.models import (
    ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT,
    AREA_BARCODE_SQL,
    ASSET_TAG_PREFIX_SQL,
    CANONICAL_PART_NUMBER_SQL,
    DEVICE_EVENT_ID_CONSTRAINT,
    MACHINE_ASSET_TAG_SQL,
    WORKER_BADGE_BARCODE_SQL,
)

CHECK_IDS: Final = ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")

#: Seconds the run waits for a table lock before it gives up (read at
#: call time). A migration holding ACCESS EXCLUSIVE fails the run fast
#: instead of queueing production statements behind both.
LOCK_TIMEOUT_SECONDS: Final = 5
DEFAULT_STATEMENT_TIMEOUT_SECONDS: Final = 300
DEFAULT_MAX_FINDINGS: Final = 100

#: The DEPLOYMENT §5 Hot-list query, verbatim (without its ';').
HOT_LIST_CHECK_SQL: Final = (
    "SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w"
    " ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL"
    " AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity)"
)

CHECK_TITLES: Final[Mapping[str, str]] = {
    "a": "Current positions replay from Movement history",
    "b": "Quantity Flow history and conservation",
    "c": "Per-PN quantity balance",
    "d": "Machine assigned quantities",
    "e": "Release evidence and Work Order status",
    "f": "Allocations and Work Order completion",
    "g": "Retained Movements reference no purged row",
    "h": "Append-only guards and database-role privileges intact",
    "i": "Hot list entries are active demand",
    "j": "Canonical identity under the running interpreter and database",
}

_REPLAY_TABLES: Final = (
    "areas",
    "machines",
    "part_movements",
    "quantity_flow_lineage",
    "quantity_flows",
)
_WORK_ORDER_TABLES: Final = (
    "audit_events",
    "part_movements",
    "work_order_allocations",
    "work_order_demands",
    "work_orders",
)

#: The tables each check reads, locked up front (ACCESS SHARE) before
#: the snapshot starts.
CHECK_TABLES: Final[Mapping[str, tuple[str, ...]]] = {
    "a": _REPLAY_TABLES,
    "b": ("assigned_route_steps", "part_movements", "quantity_flow_lineage", "quantity_flows"),
    "c": _REPLAY_TABLES,
    "d": _REPLAY_TABLES,
    "e": _WORK_ORDER_TABLES,
    "f": _WORK_ORDER_TABLES,
    "g": (),
    "h": GUARD_TABLES,
    "i": ("work_order_demands", "work_orders"),
    "j": (
        "areas",
        "machine_asset_tag_config",
        "machines",
        "part_movements",
        "part_numbers",
        "quantity_flows",
        "users",
        "work_order_allocations",
        "work_order_demands",
        "workers",
    ),
}

#: Checks that run whatever the database's schema revision: (j) reads
#: only identity columns stable across revisions; (g) reads nothing. (h)
#: compares the database with the guard state of this code's head.
_REVISION_INDEPENDENT: Final = frozenset({"g", "j"})
_NOT_APPLICABLE: Final[Mapping[str, str]] = {
    "g": "No Movement-history archival exists yet. This check starts with the archival purge.",
}
_REPLAY_CHECKS: Final = frozenset({"a", "c", "d"})

RUN_ERROR_MESSAGES: Final[Mapping[str, str]] = {
    "configuration_invalid": (
        "The database connection is not configured or is invalid (DATABASE_URL, or"
        " DATABASE_HOST, DATABASE_NAME, DATABASE_USER and DATABASE_PASSWORD_FILE)."
        " Nothing was checked."
    ),
    "database_unavailable": "The PartFlow database could not be reached. Nothing was checked.",
    "not_read_only": "The reconciliation transaction is not read-only. Nothing was checked.",
    "schema_mismatch": (
        "A table the checks read does not exist. The database is not at this code's schema."
        " Nothing was checked."
    ),
    "advisory_lock_held": (
        "The run held an advisory lock. The report is kept, but this is a defect to fix before"
        " the next run."
    ),
    "internal_error": (
        "Reconciliation stopped on an unexpected error. Nothing was repaired. See the error output."
    ),
}
_CONNECTION_LOST: Final = (
    "The connection to the PartFlow database was lost. The report is incomplete. Nothing was"
    " repaired."
)


def _lock_timeout_message(seconds: int | None = None) -> str:
    waited = LOCK_TIMEOUT_SECONDS if seconds is None else seconds
    return (
        f"The run waited more than {waited} s for a table lock. A migration or"
        " maintenance may be running. Nothing was checked."
    )


_LOCK_DEADLOCK: Final = (
    "The run's table locks deadlocked with another session. A migration or maintenance may be"
    " running. Nothing was checked."
)


class Finding(NamedTuple):
    code: str
    entity_type: str
    entity_id: int | str
    part_number: str | None
    expected: object
    actual: object
    detail: dict[str, object]


class CheckResult(NamedTuple):
    id: str
    title: str
    status: str
    duration_ms: int
    examined: dict[str, int]
    finding_count: int
    truncated: bool
    findings: list[Finding]
    reason: str | None
    error_code: str | None


class RunError(NamedTuple):
    code: str
    message: str
    #: The unexpected exception behind ``internal_error`` (never
    #: rendered in the JSON report; the CLI prints its traceback).
    exception: BaseException | None = None


class ReconciliationReport(NamedTuple):
    started_at: datetime.datetime
    finished_at: datetime.datetime
    database: dict[str, object] | None
    runtime: dict[str, object]
    options: dict[str, object]
    checks: list[CheckResult]
    error: RunError | None


class HeldLocks(NamedTuple):
    """The locks this backend holds: advisory count and locked public tables."""

    advisory: int
    tables: frozenset[str]


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _iso(value: datetime.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _selected(checks: Collection[str]) -> list[str]:
    wanted = set(checks)
    unknown = wanted - set(CHECK_IDS)
    if unknown:
        raise ValueError(f"Unknown reconciliation check id(s): {sorted(unknown)}.")
    return [check_id for check_id in CHECK_IDS if check_id in wanted]


def _options(
    selected: list[str], statement_timeout_seconds: int, max_findings: int
) -> dict[str, object]:
    return {
        "checks": selected,
        "statement_timeout_seconds": statement_timeout_seconds,
        "lock_timeout_seconds": LOCK_TIMEOUT_SECONDS,
        "max_findings": max_findings,
    }


def _runtime(expected_alembic_revision: str | None) -> dict[str, object]:
    return {
        "python_version": platform.python_version(),
        "unicode_version": unicodedata.unidata_version,
        "alembic_head": expected_alembic_revision,
    }


def error_report(
    code: str,
    *,
    started_at: datetime.datetime,
    checks: Collection[str] = CHECK_IDS,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    max_findings: int = DEFAULT_MAX_FINDINGS,
    expected_alembic_revision: str | None = None,
    exception: BaseException | None = None,
) -> ReconciliationReport:
    """A report of a run that could not start (no check ran)."""
    return ReconciliationReport(
        started_at=started_at,
        finished_at=_now(),
        database=None,
        runtime=_runtime(expected_alembic_revision),
        options=_options(_selected(checks), statement_timeout_seconds, max_findings),
        checks=[],
        error=RunError(code, RUN_ERROR_MESSAGES[code], exception),
    )


def result_of(report: ReconciliationReport) -> tuple[str, int]:
    """``("error", 2)``, ``("mismatch", 1)`` or ``("clean", 0)`` — an
    incomplete run never reads as a mere mismatch."""
    if report.error is not None or any(check.status == "error" for check in report.checks):
        return "error", 2
    if any(check.status == "fail" for check in report.checks):
        return "mismatch", 1
    return "clean", 0


def report_document(report: ReconciliationReport) -> dict[str, object]:
    """The JSON report (``report_version`` 1), keys in contract order."""
    result, exit_code = result_of(report)
    duration = report.finished_at - report.started_at
    return {
        "report_version": 1,
        "command": "reconcile",
        "result": result,
        "exit_code": exit_code,
        "started_at": _iso(report.started_at),
        "finished_at": _iso(report.finished_at),
        "duration_ms": int(duration.total_seconds() * 1000),
        "runtime": report.runtime,
        "database": report.database,
        "options": report.options,
        "error": (
            None
            if report.error is None
            else {"code": report.error.code, "message": report.error.message}
        ),
        "checks": [
            {
                "id": check.id,
                "title": check.title,
                "status": check.status,
                "duration_ms": check.duration_ms,
                "examined": check.examined,
                "finding_count": check.finding_count,
                "truncated": check.truncated,
                "reason": check.reason,
                "error_code": check.error_code,
                "findings": [
                    {
                        "code": finding.code,
                        "entity": {"type": finding.entity_type, "id": finding.entity_id},
                        "part_number": finding.part_number,
                        "expected": finding.expected,
                        "actual": finding.actual,
                        "detail": finding.detail,
                    }
                    for finding in check.findings
                ],
            }
            for check in report.checks
        ],
    }


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


class _Outcome(NamedTuple):
    examined: dict[str, int]
    findings: list[Finding]
    #: Set when the runner decided the check does not apply here (h).
    not_applicable_reason: str | None = None


class _ReplayFailedError(Exception):
    """The Movement-history replay raised (`effective_latest_movement`)."""


class _Flow(NamedTuple):
    id: int
    part_number: str
    quantity: int
    status: str
    route_mode: str
    assigned_route_id: int | None
    current_area_id: int
    current_machine_id: int | None


class _Demand(NamedTuple):
    id: int
    work_order_id: int
    part_number: str
    requested_quantity: int
    allocated_quantity: int


class _WorkOrder(NamedTuple):
    id: int
    status: str
    completed_at: datetime.datetime | None


class _Context:
    """The run's session and the shared inputs, each loaded once when needed."""

    def __init__(
        self,
        session: Session,
        database: Mapping[str, object],
        roles: DatabaseRoles,
        roles_required: bool,
    ) -> None:
        self.session = session
        self.database = database
        self.roles = roles
        self.roles_required = roles_required
        self._flows: dict[int, _Flow] | None = None
        self._latest_types: dict[int, str] | None = None
        self._consumed: set[int] | None = None
        self._positions: dict[int, projections.CurrentPosition] | None = None
        self._replay_error: str | None = None
        self._demands: dict[int, _Demand] | None = None
        self._work_orders: dict[int, _WorkOrder] | None = None
        self._released: dict[int, int] | None = None
        self._allocated: dict[int, int] | None = None
        self._completed: dict[int, datetime.datetime | None] | None = None

    def rows(self, sql: str, **params: object) -> list[Any]:
        return list(self.session.execute(text(sql), params))

    def scalar_int(self, sql: str, **params: object) -> int:
        return int(self.session.execute(text(sql), params).scalar_one())

    def flows(self) -> dict[int, _Flow]:
        if self._flows is None:
            self._flows = {
                int(row.id): _Flow(*row)
                for row in self.rows(
                    "SELECT id, part_number, quantity, status, route_mode, assigned_route_id,"
                    " current_area_id, current_machine_id FROM quantity_flows ORDER BY id"
                )
            }
        return self._flows

    def latest_types(self) -> dict[int, str]:
        if self._latest_types is None:
            self._latest_types = {
                flow_id: movement.movement_type
                for flow_id, movement in projections.latest_movements(self.session, None).items()
            }
        return self._latest_types

    def consumed(self) -> set[int]:
        if self._consumed is None:
            self._consumed = projections.consumed_flow_ids(self.session)
        return self._consumed

    def positions(self) -> dict[int, projections.CurrentPosition]:
        if self._replay_error is not None:
            raise _ReplayFailedError(self._replay_error)
        if self._positions is None:
            try:
                self._positions = projections.rebuild_current_positions(self.session)
            except ConflictError as exc:
                self._replay_error = exc.message
                raise _ReplayFailedError(exc.message) from exc
        return self._positions

    def demands(self) -> dict[int, _Demand]:
        if self._demands is None:
            self._demands = {
                int(row.id): _Demand(*row)
                for row in self.rows(
                    "SELECT id, work_order_id, part_number, requested_quantity,"
                    " allocated_quantity FROM work_order_demands ORDER BY id"
                )
            }
        return self._demands

    def work_orders(self) -> dict[int, _WorkOrder]:
        if self._work_orders is None:
            self._work_orders = {
                int(row.id): _WorkOrder(*row)
                for row in self.rows("SELECT id, status, completed_at FROM work_orders ORDER BY id")
            }
        return self._work_orders

    def released(self) -> dict[int, int]:
        if self._released is None:
            self._released = production_release.released_quantities(
                self.session, list(self.demands())
            )
        return self._released

    def allocated(self) -> dict[int, int]:
        if self._allocated is None:
            self._allocated = allocations.rebuild_allocated_quantities(self.session)
        return self._allocated

    def completed(self) -> dict[int, datetime.datetime | None]:
        if self._completed is None:
            self._completed = allocations.rebuild_completed_at(self.session)
        return self._completed


def _findings_sort_key(finding: Finding) -> tuple[str, str, str]:
    return finding.code, finding.entity_type, str(finding.entity_id)


def _check_result(
    check_id: str,
    status: str,
    *,
    duration_ms: int = 0,
    examined: dict[str, int] | None = None,
    findings: list[Finding] | None = None,
    max_findings: int = DEFAULT_MAX_FINDINGS,
    reason: str | None = None,
    error_code: str | None = None,
) -> CheckResult:
    ordered = sorted(findings or [], key=_findings_sort_key)
    return CheckResult(
        id=check_id,
        title=CHECK_TITLES[check_id],
        status=status,
        duration_ms=duration_ms,
        examined=examined or {},
        finding_count=len(ordered),
        truncated=len(ordered) > max_findings,
        findings=ordered[:max_findings],
        reason=reason,
        error_code=error_code,
    )


def _check_error(check_id: str, error_code: str, reason: str, duration_ms: int) -> CheckResult:
    return _check_result(
        check_id, "error", duration_ms=duration_ms, reason=reason, error_code=error_code
    )


def _caught_error(
    exc: DBAPIError, statement_timeout_seconds: int
) -> tuple[str, str]:  # (error_code, reason)
    original = exc.orig
    if isinstance(original, psycopg.errors.QueryCanceled):
        return (
            "statement_timeout",
            f"The check exceeded the statement timeout of {statement_timeout_seconds} s.",
        )
    if isinstance(original, psycopg.errors.LockNotAvailable):
        return (
            "lock_timeout",
            f"The check waited more than {LOCK_TIMEOUT_SECONDS} s for a table lock. A migration"
            " or maintenance may be running.",
        )
    if isinstance(original, psycopg.errors.ReadOnlySqlTransaction):
        return (
            "read_only_violation",
            "The check attempted a write and the read-only transaction refused it.",
        )
    return (
        "database_error",
        f"The check failed on a database error ({type(original).__name__}).",
    )


def _held_locks(session: Session) -> HeldLocks:
    advisory = int(
        session.execute(
            text(
                "SELECT count(*) FROM pg_locks"
                " WHERE pid = pg_backend_pid() AND locktype = 'advisory'"
            )
        ).scalar_one()
    )
    tables = frozenset(
        str(name)
        for name in session.scalars(
            text(
                "SELECT DISTINCT c.relname FROM pg_locks l"
                " JOIN pg_class c ON c.oid = l.relation"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE l.pid = pg_backend_pid() AND l.locktype = 'relation'"
                " AND n.nspname = 'public' AND c.relkind IN ('r', 'p')"
            )
        )
    )
    return HeldLocks(advisory=advisory, tables=tables)


def _database_block(session: Session) -> dict[str, object]:
    row = session.execute(
        text(
            "SELECT current_database() AS name, current_setting('server_version') AS version,"
            " d.datcollate, d.datctype, d.datcollversion, d.oid, current_user AS role,"
            " (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS superuser"
            " FROM pg_database d WHERE d.datname = current_database()"
        )
    ).one()
    revision: str | None = None
    if session.execute(text("SELECT to_regclass('public.alembic_version')")).scalar() is not None:
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    actual_version: str | None
    try:
        with session.begin_nested():
            actual_version = session.execute(
                text("SELECT pg_database_collation_actual_version(:oid)"), {"oid": row.oid}
            ).scalar()
    except DBAPIError:
        # The libc/ICU version is unreadable on this server: reported as
        # unknown (null); the collation-version comparison is skipped.
        actual_version = None
    return {
        "name": row.name,
        "server_version": row.version,
        "alembic_revision": revision,
        "transaction_isolation": session.execute(text("SHOW transaction_isolation")).scalar_one(),
        "transaction_read_only": session.execute(text("SHOW transaction_read_only")).scalar_one()
        == "on",
        "collation": row.datcollate,
        "ctype": row.datctype,
        "collation_version_recorded": row.datcollversion,
        "collation_version_actual": actual_version,
        "connected_role": row.role,
        "connected_role_superuser": bool(row.superuser),
    }


#: The SQLSTATEs (besides class 08, connection exception) psycopg raises
#: as ``OperationalError`` that mean the connection is gone or was refused:
#: admin/crash shutdown, cannot connect now, too many connections. Every
#: other ``OperationalError`` SQLSTATE (a statement refusal such as a lock
#: or statement timeout or a deadlock, or a statement failure such as a
#: full disk or a program limit) leaves the database reachable.
_CONNECTION_SQLSTATES: Final = frozenset({"57P01", "57P02", "57P03", "53300"})


def _is_connection_failure(exc: BaseException) -> bool:
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    if isinstance(exc, InterfaceError):
        return True
    if not isinstance(exc, OperationalError):
        return False
    # A client-side failure (refused, closed by the server) carries no SQLSTATE.
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return sqlstate is None or sqlstate.startswith("08") or sqlstate in _CONNECTION_SQLSTATES


def _lock_failure_message(exc: DBAPIError, statement_timeout_seconds: int) -> str | None:
    """The run-level ``lock_timeout`` message when step 4 could not take
    its table locks (None: another failure)."""
    original = exc.orig
    if isinstance(original, psycopg.errors.LockNotAvailable):
        return _lock_timeout_message()
    if isinstance(original, psycopg.errors.QueryCanceled):
        # A statement timeout shorter than the lock timeout fired first
        # while LOCK TABLE waited.
        return _lock_timeout_message(statement_timeout_seconds)
    if isinstance(original, psycopg.errors.DeadlockDetected):
        return _LOCK_DEADLOCK
    return None


def _advisory_guard(session: Session) -> RunError | None:
    """Step 8: the run must hold no advisory lock. A database failure
    here is a run-level error that keeps the completed results."""
    try:
        advisory = _held_locks(session).advisory
    except DBAPIError as exc:
        if _is_connection_failure(exc):
            return RunError("database_unavailable", _CONNECTION_LOST, exc)
        return RunError("internal_error", RUN_ERROR_MESSAGES["internal_error"], exc)
    if advisory > 0:
        return RunError("advisory_lock_held", RUN_ERROR_MESSAGES["advisory_lock_held"])
    return None


def run_reconciliation(
    engine: Engine,
    *,
    checks: Collection[str] = CHECK_IDS,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    max_findings: int = DEFAULT_MAX_FINDINGS,
    expected_alembic_revision: str | None = None,
    database_roles: DatabaseRoles | None = None,
    roles_required: bool | None = None,
) -> ReconciliationReport:
    """Run the selected checks in one read-only snapshot; never writes.

    ``expected_alembic_revision`` is the code's own Alembic head (None
    skips the comparison): when the database is at another revision,
    every selected check except (j) reports ``schema_mismatch`` without
    running. Run-level failures before the first check return a report
    with no checks and ``error`` set. ``database_roles`` (default: the
    configured PartFlow names) and ``roles_required`` (default: the
    ``DATABASE_ROLES_REQUIRED`` setting) decide check (h).
    """
    selected = _selected(checks)
    roles = configured_roles() if database_roles is None else database_roles
    required = get_settings().database_roles_required if roles_required is None else roles_required
    statement_timeout = int(statement_timeout_seconds)
    lock_timeout = int(LOCK_TIMEOUT_SECONDS)
    started_at = _now()
    options = _options(selected, statement_timeout, max_findings)
    runtime = _runtime(expected_alembic_revision)

    def finish(
        database: dict[str, object] | None,
        results: list[CheckResult],
        error: RunError | None,
    ) -> ReconciliationReport:
        return ReconciliationReport(
            started_at=started_at,
            finished_at=_now(),
            database=database,
            runtime=runtime,
            options=options,
            checks=results,
            error=error,
        )

    def run_error(code: str, message: str | None = None) -> ReconciliationReport:
        return finish(None, [], RunError(code, message or RUN_ERROR_MESSAGES[code]))

    with Session(engine) as session:
        try:
            # 1–2. Utility statements only: none takes a snapshot.
            try:
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            except DBAPIError as exc:
                if _is_connection_failure(exc):
                    return run_error("database_unavailable")
                raise
            session.execute(text(f"SET LOCAL statement_timeout = '{statement_timeout}s'"))
            session.execute(text(f"SET LOCAL lock_timeout = '{lock_timeout}s'"))
            session.execute(text("SET LOCAL application_name = 'partflow-reconcile'"))
            # 3. The transaction really is a read-only snapshot.
            read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
            isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
            if read_only != "on" or isolation != "repeatable read":
                return run_error("not_read_only")
            # 4. Every table the selected checks read, locked before the snapshot.
            tables = sorted({table for check_id in selected for table in CHECK_TABLES[check_id]})
            if tables:
                try:
                    session.execute(text(f"LOCK TABLE {', '.join(tables)} IN ACCESS SHARE MODE"))
                except DBAPIError as exc:
                    lock_failure = _lock_failure_message(exc, statement_timeout)
                    if lock_failure is not None:
                        return run_error("lock_timeout", lock_failure)
                    if isinstance(exc.orig, psycopg.errors.UndefinedTable):
                        return run_error("schema_mismatch")
                    if _is_connection_failure(exc):
                        return run_error("database_unavailable")
                    raise
            # 5. First SELECT: the snapshot starts here.
            try:
                database = _database_block(session)
            except DBAPIError as exc:
                if _is_connection_failure(exc):
                    return run_error("database_unavailable")
                raise
            results, error = _run_checks(
                session,
                selected,
                database=database,
                statement_timeout_seconds=statement_timeout,
                max_findings=max_findings,
                expected_alembic_revision=expected_alembic_revision,
                roles=roles,
                roles_required=required,
            )
            # 8. No advisory lock may ever be taken by a reconciliation run
            # (skipped once the run already stopped on an error).
            if error is None:
                error = _advisory_guard(session)
            return finish(database, results, error)
        finally:
            # 9. Never commit. Session.rollback() on a lost connection
            # discards the transaction state without raising.
            session.rollback()


def _run_checks(
    session: Session,
    selected: list[str],
    *,
    database: dict[str, object],
    statement_timeout_seconds: int,
    max_findings: int,
    expected_alembic_revision: str | None,
    roles: DatabaseRoles,
    roles_required: bool,
) -> tuple[list[CheckResult], RunError | None]:
    runners: dict[str, Callable[[_Context], _Outcome]] = {
        "a": _check_a,
        "b": _check_b,
        "c": _check_c,
        "d": _check_d,
        "e": _check_e,
        "f": _check_f,
        "h": _check_h,
        "i": _check_i,
        "j": _check_j,
    }
    context = _Context(session, database, roles, roles_required)
    revision = database["alembic_revision"]
    schema_differs = expected_alembic_revision is not None and revision != expected_alembic_revision
    results: list[CheckResult] = []
    error: RunError | None = None
    for check_id in CHECK_IDS:
        if check_id not in selected:
            results.append(_check_result(check_id, "skipped"))
            continue
        if check_id in _NOT_APPLICABLE:
            results.append(
                _check_result(check_id, "not_applicable", reason=_NOT_APPLICABLE[check_id])
            )
            continue
        if error is not None:
            results.append(
                _check_error(check_id, "not_run", "The run stopped before this check.", 0)
            )
            continue
        if schema_differs and check_id not in _REVISION_INDEPENDENT:
            results.append(
                _check_error(
                    check_id,
                    "schema_mismatch",
                    f"The database is at revision {revision or 'none'} but this code expects"
                    f" {expected_alembic_revision}. Only check (j) runs across revisions.",
                    0,
                )
            )
            continue
        result, error = _run_one_check(
            context,
            check_id,
            runners[check_id],
            statement_timeout_seconds=statement_timeout_seconds,
            max_findings=max_findings,
        )
        results.append(result)
    return results, error


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _run_one_check(
    context: _Context,
    check_id: str,
    runner: Callable[[_Context], _Outcome],
    *,
    statement_timeout_seconds: int,
    max_findings: int,
) -> tuple[CheckResult, RunError | None]:
    """Run one check in its own savepoint; a caught failure rolls back to
    it and the run continues, any other failure stops the run (the
    returned RunError)."""
    started = time.monotonic()
    try:
        with context.session.begin_nested():
            outcome = runner(context)
    except _ReplayFailedError as exc:
        reason = f"The Movement-history replay failed: {exc}"
        return _check_error(check_id, "replay_failed", reason, _elapsed_ms(started)), None
    except DBAPIError as exc:
        if not _is_connection_lost(exc):
            code, reason = _caught_error(exc, statement_timeout_seconds)
            return _check_error(check_id, code, reason, _elapsed_ms(started)), None
        reason = f"The check failed on a database error ({type(exc.orig).__name__})."
        return (
            _check_error(check_id, "database_error", reason, _elapsed_ms(started)),
            RunError("database_unavailable", _CONNECTION_LOST, exc),
        )
    except PendingRollbackError as exc:
        # The savepoint could not be rolled back: the connection is gone.
        reason = f"The check failed on a database error ({type(exc).__name__})."
        return (
            _check_error(check_id, "database_error", reason, _elapsed_ms(started)),
            RunError("database_unavailable", _CONNECTION_LOST, exc),
        )
    except Exception as exc:
        # Unexpected: stop the run, keep the finished results; the CLI
        # prints the traceback.
        reason = "The check stopped on an unexpected error."
        return (
            _check_error(check_id, "internal_error", reason, _elapsed_ms(started)),
            RunError("internal_error", RUN_ERROR_MESSAGES["internal_error"], exc),
        )
    if outcome.not_applicable_reason is not None:
        reason = outcome.not_applicable_reason
        return _check_result(check_id, "not_applicable", reason=reason), None
    status = "fail" if outcome.findings else "pass"
    result = _check_result(
        check_id,
        status,
        duration_ms=_elapsed_ms(started),
        examined=outcome.examined,
        findings=outcome.findings,
        max_findings=max_findings,
    )
    return result, None


def _is_connection_lost(exc: DBAPIError) -> bool:
    """A lost connection (not a statement-level refusal): the run stops."""
    return bool(exc.connection_invalidated) or isinstance(exc, InterfaceError)


# ---------------------------------------------------------------------------
# (a) Current positions replay from Movement history
# ---------------------------------------------------------------------------

_LINEAGE_TYPES: Final = (MovementType.SPLIT, MovementType.MERGED)
_CLOSING_TYPES: Final = (MovementType.SCRAPPED, MovementType.STOCKED)


def _expected_status(
    flow: _Flow, latest: Mapping[int, str], consumed: set[int]
) -> tuple[str | None, Finding | None]:
    """The lifecycle status history implies for one flow (None = undefined)."""
    if flow.id not in latest:
        return QuantityFlowStatus.REVERSED, None
    latest_type = latest[flow.id]
    if flow.id in consumed:
        if latest_type in _LINEAGE_TYPES:
            return latest_type, None
        return None, Finding(
            "LINEAGE_CONSUMPTION_MISMATCH",
            "QuantityFlow",
            flow.id,
            flow.part_number,
            [MovementType.SPLIT.value, MovementType.MERGED.value],
            latest_type,
            {},
        )
    if latest_type in _CLOSING_TYPES:
        return latest_type, None
    return QuantityFlowStatus.ACTIVE, None


def _check_a(context: _Context) -> _Outcome:
    flows = context.flows()
    latest = context.latest_types()
    consumed = context.consumed()
    positions = context.positions()
    findings: list[Finding] = []
    for flow in flows.values():
        expected, mismatch = _expected_status(flow, latest, consumed)
        if mismatch is not None:
            findings.append(mismatch)
        elif expected != flow.status:
            findings.append(
                Finding(
                    "FLOW_STATUS_MISMATCH",
                    "QuantityFlow",
                    flow.id,
                    flow.part_number,
                    expected,
                    flow.status,
                    {},
                )
            )
        replayed_active = flow.id in positions
        if (expected == QuantityFlowStatus.ACTIVE) != replayed_active:
            findings.append(
                Finding(
                    "REPLAY_SET_MISMATCH",
                    "QuantityFlow",
                    flow.id,
                    flow.part_number,
                    "not ACTIVE" if replayed_active else "ACTIVE",
                    "ACTIVE" if replayed_active else "not ACTIVE",
                    {},
                )
            )
        if flow.status != QuantityFlowStatus.ACTIVE and flow.current_machine_id is not None:
            findings.append(
                Finding(
                    "CLOSED_FLOW_ON_MACHINE",
                    "QuantityFlow",
                    flow.id,
                    flow.part_number,
                    None,
                    flow.current_machine_id,
                    {"status": flow.status},
                )
            )
        position = positions.get(flow.id)
        if position is None or flow.status != QuantityFlowStatus.ACTIVE:
            continue
        if flow.current_area_id != position.area_id:
            findings.append(
                Finding(
                    "PROJECTION_AREA_MISMATCH",
                    "QuantityFlow",
                    flow.id,
                    flow.part_number,
                    position.area_id,
                    flow.current_area_id,
                    {},
                )
            )
        if flow.current_machine_id != position.machine_id:
            findings.append(
                Finding(
                    "PROJECTION_MACHINE_MISMATCH",
                    "QuantityFlow",
                    flow.id,
                    flow.part_number,
                    position.machine_id,
                    flow.current_machine_id,
                    {},
                )
            )
    return _Outcome({"flows": len(flows), "active_flows": len(positions)}, findings)


# ---------------------------------------------------------------------------
# (b) Quantity Flow history and conservation
# ---------------------------------------------------------------------------


def _check_b(context: _Context) -> _Outcome:
    findings: list[Finding] = []
    for row in context.rows(
        "SELECT f.id, f.part_number FROM quantity_flows f WHERE NOT EXISTS"
        " (SELECT 1 FROM part_movements m WHERE m.quantity_flow_id = f.id)"
    ):
        findings.append(
            Finding("FLOW_WITHOUT_MOVEMENT", "QuantityFlow", int(row.id), row.part_number, 1, 0, {})
        )
    for row in context.rows(
        "WITH first AS (SELECT DISTINCT ON (quantity_flow_id) quantity_flow_id, id,"
        " movement_type, device_event_id, part_number FROM part_movements"
        " ORDER BY quantity_flow_id, id)"
        " SELECT first.quantity_flow_id, first.part_number, first.id, first.movement_type"
        " FROM first WHERE"
        " (first.movement_type IN (:received, :adjusted) AND EXISTS (SELECT 1 FROM"
        " quantity_flow_lineage l WHERE l.child_flow_id = first.quantity_flow_id))"
        " OR (first.movement_type IN (:split, :merged) AND NOT EXISTS (SELECT 1 FROM"
        " quantity_flow_lineage l WHERE l.child_flow_id = first.quantity_flow_id"
        " AND l.device_event_id = first.device_event_id))"
        " OR first.movement_type NOT IN (:received, :adjusted, :split, :merged)",
        received=MovementType.RECEIVED,
        adjusted=MovementType.QUANTITY_ADJUSTED,
        split=MovementType.SPLIT,
        merged=MovementType.MERGED,
    ):
        findings.append(
            Finding(
                "FIRST_MOVEMENT_INVALID",
                "QuantityFlow",
                int(row.quantity_flow_id),
                row.part_number,
                None,
                row.movement_type,
                {"movement_id": int(row.id), "movement_type": row.movement_type},
            )
        )
    for row in context.rows(
        "SELECT m.id, m.part_number, m.movement_type, m.quantity_flow_id FROM part_movements m"
        " WHERE m.movement_type IN (:received, :adjusted) AND m.id <> (SELECT min(o.id)"
        " FROM part_movements o WHERE o.quantity_flow_id = m.quantity_flow_id)",
        received=MovementType.RECEIVED,
        adjusted=MovementType.QUANTITY_ADJUSTED,
    ):
        findings.append(
            Finding(
                "INTRODUCTION_NOT_FIRST",
                "PartMovement",
                int(row.id),
                row.part_number,
                None,
                row.movement_type,
                {"quantity_flow_id": int(row.quantity_flow_id)},
            )
        )
    for row in context.rows(
        "SELECT f.id, f.part_number, f.quantity, count(*) AS count,"
        " (array_agg(m.id ORDER BY m.id))[1:10] AS movement_ids"
        " FROM quantity_flows f JOIN part_movements m ON m.quantity_flow_id = f.id"
        " WHERE m.quantity <> f.quantity GROUP BY f.id, f.part_number, f.quantity"
    ):
        findings.append(
            Finding(
                "MOVEMENT_QUANTITY_MISMATCH",
                "QuantityFlow",
                int(row.id),
                row.part_number,
                int(row.quantity),
                None,
                {"movement_ids": [int(value) for value in row.movement_ids], "count": row.count},
            )
        )
    for row in context.rows(
        "SELECT l.device_event_id, l.parent_flow_id, p.part_number, p.quantity AS parent_quantity,"
        " sum(c.quantity) AS children_quantity FROM quantity_flow_lineage l"
        " JOIN quantity_flows p ON p.id = l.parent_flow_id"
        " JOIN quantity_flows c ON c.id = l.child_flow_id WHERE l.relation = :split"
        " GROUP BY l.device_event_id, l.parent_flow_id, p.part_number, p.quantity"
        " HAVING p.quantity <> sum(c.quantity)",
        split=LineageRelation.SPLIT,
    ):
        findings.append(
            Finding(
                "SPLIT_NOT_CONSERVED",
                "QuantityFlowLineage",
                str(row.device_event_id),
                row.part_number,
                int(row.parent_quantity),
                int(row.children_quantity),
                {"parent_flow_id": int(row.parent_flow_id)},
            )
        )
    for row in context.rows(
        "SELECT l.device_event_id, l.child_flow_id, c.part_number, c.quantity AS child_quantity,"
        " sum(p.quantity) AS parents_quantity FROM quantity_flow_lineage l"
        " JOIN quantity_flows p ON p.id = l.parent_flow_id"
        " JOIN quantity_flows c ON c.id = l.child_flow_id WHERE l.relation = :merged"
        " GROUP BY l.device_event_id, l.child_flow_id, c.part_number, c.quantity"
        " HAVING c.quantity <> sum(p.quantity)",
        merged=LineageRelation.MERGED,
    ):
        findings.append(
            Finding(
                "MERGE_NOT_CONSERVED",
                "QuantityFlowLineage",
                str(row.device_event_id),
                row.part_number,
                int(row.parents_quantity),
                int(row.child_quantity),
                {"child_flow_id": int(row.child_flow_id)},
            )
        )
    for row in context.rows(
        "SELECT l.id, l.parent_flow_id, l.child_flow_id, p.part_number AS parent_part_number,"
        " c.part_number AS child_part_number FROM quantity_flow_lineage l"
        " JOIN quantity_flows p ON p.id = l.parent_flow_id"
        " JOIN quantity_flows c ON c.id = l.child_flow_id"
        " WHERE p.part_number <> c.part_number"
    ):
        findings.append(
            Finding(
                "LINEAGE_PN_MISMATCH",
                "QuantityFlowLineage",
                int(row.id),
                row.child_part_number,
                row.parent_part_number,
                row.child_part_number,
                {
                    "parent_flow_id": int(row.parent_flow_id),
                    "child_flow_id": int(row.child_flow_id),
                },
            )
        )
    for row in context.rows(
        "SELECT r.id, r.part_number, r.quantity_flow_id, r.quantity, o.id AS original_id,"
        " o.quantity_flow_id AS original_flow_id, o.quantity AS original_quantity,"
        " o.part_number AS original_part_number, o.movement_type AS original_type"
        " FROM part_movements r JOIN part_movements o ON o.id = r.reverses_movement_id"
        " WHERE r.movement_type = :reversed AND (o.quantity_flow_id <> r.quantity_flow_id"
        " OR o.quantity <> r.quantity OR o.part_number <> r.part_number"
        " OR o.movement_type = :reversed)",
        reversed=MovementType.REVERSED,
    ):
        findings.append(
            Finding(
                "REVERSAL_MISMATCH",
                "PartMovement",
                int(row.id),
                row.part_number,
                {
                    "quantity_flow_id": int(row.original_flow_id),
                    "quantity": int(row.original_quantity),
                    "part_number": row.original_part_number,
                    "movement_type": "not REVERSED",
                },
                {
                    "quantity_flow_id": int(row.quantity_flow_id),
                    "quantity": int(row.quantity),
                    "part_number": row.part_number,
                    "movement_type": row.original_type,
                },
                {"reverses_movement_id": int(row.original_id)},
            )
        )
    for row in context.rows(
        "SELECT m.device_event_id, min(m.part_number) AS part_number, count(*) AS movements,"
        " count(r.id) AS reversed FROM part_movements m"
        " LEFT JOIN part_movements r ON r.reverses_movement_id = m.id"
        " WHERE m.movement_type <> :reversed GROUP BY m.device_event_id"
        " HAVING count(r.id) > 0 AND count(r.id) < count(*)",
        reversed=MovementType.REVERSED,
    ):
        findings.append(
            Finding(
                "PARTIAL_COMMAND_REVERSAL",
                "PartMovement",
                str(row.device_event_id),
                row.part_number,
                int(row.movements),
                int(row.reversed),
                {},
            )
        )
    for row in context.rows(
        "SELECT m.id, m.part_number, m.assigned_route_step_id, f.id AS flow_id, f.route_mode,"
        " f.assigned_route_id, s.assigned_route_id AS step_route_id FROM part_movements m"
        " JOIN quantity_flows f ON f.id = m.quantity_flow_id"
        " JOIN assigned_route_steps s ON s.id = m.assigned_route_step_id"
        " WHERE f.route_mode = :floating OR f.assigned_route_id IS NULL"
        " OR s.assigned_route_id <> f.assigned_route_id",
        floating=RouteMode.FLOATING,
    ):
        findings.append(
            Finding(
                "ROUTE_STEP_MISMATCH",
                "PartMovement",
                int(row.id),
                row.part_number,
                row.assigned_route_id,
                int(row.step_route_id),
                {
                    "quantity_flow_id": int(row.flow_id),
                    "route_mode": row.route_mode,
                    "assigned_route_step_id": int(row.assigned_route_step_id),
                },
            )
        )
    counts = context.rows(
        "SELECT (SELECT count(*) FROM quantity_flows) AS flows,"
        " (SELECT count(*) FROM part_movements) AS movements,"
        " (SELECT count(*) FROM quantity_flow_lineage) AS edges"
    )[0]
    return _Outcome(
        {
            "flows": int(counts.flows),
            "movements": int(counts.movements),
            "lineage_edges": int(counts.edges),
        },
        findings,
    )


# ---------------------------------------------------------------------------
# (c) Per-PN quantity balance
# ---------------------------------------------------------------------------


def _check_c(context: _Context) -> _Outcome:
    flows = context.flows()
    positions = context.positions()
    terms: dict[str, dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(
            ("received", "added", "added_reversed", "scrapped", "stocked", "active"), 0
        )
    )
    for row in context.rows(
        "SELECT m.part_number,"
        " coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = :received), 0) AS received,"
        " coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = :adjusted), 0) AS added,"
        " coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = :adjusted"
        " AND r.id IS NOT NULL), 0) AS added_reversed,"
        " coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = :scrapped"
        " AND r.id IS NULL), 0) AS scrapped,"
        " coalesce(sum(m.quantity) FILTER (WHERE m.movement_type = :stocked"
        " AND r.id IS NULL), 0) AS stocked"
        " FROM part_movements m LEFT JOIN part_movements r ON r.reverses_movement_id = m.id"
        " WHERE m.movement_type IN (:received, :adjusted, :scrapped, :stocked)"
        " GROUP BY m.part_number",
        received=MovementType.RECEIVED,
        adjusted=MovementType.QUANTITY_ADJUSTED,
        scrapped=MovementType.SCRAPPED,
        stocked=MovementType.STOCKED,
    ):
        values = terms[row.part_number]
        for name in ("received", "added", "added_reversed", "scrapped", "stocked"):
            values[name] = int(getattr(row, name))
    for flow_id in positions:
        flow = flows.get(flow_id)
        if flow is not None:
            terms[flow.part_number]["active"] += flow.quantity
    stored_active: dict[str, int] = defaultdict(int)
    for flow in flows.values():
        if flow.status == QuantityFlowStatus.ACTIVE:
            stored_active[flow.part_number] += flow.quantity
    findings: list[Finding] = []
    for part_number, values in terms.items():
        introduced = values["received"] + values["added"] - values["added_reversed"]
        accounted = values["active"] + values["stocked"] + values["scrapped"]
        if introduced != accounted:
            findings.append(
                Finding(
                    "PN_QUANTITY_IMBALANCE",
                    "PN",
                    part_number,
                    part_number,
                    introduced,
                    accounted,
                    {**values, "stored_active": stored_active.get(part_number, 0)},
                )
            )
    return _Outcome({"part_numbers": len(terms)}, findings)


# ---------------------------------------------------------------------------
# (d) Machine assigned quantities
# ---------------------------------------------------------------------------


def _check_d(context: _Context) -> _Outcome:
    flows = context.flows()
    positions = context.positions()
    machine_rows = context.rows(
        "SELECT id, area_id, name, asset_tag, retired_on FROM machines ORDER BY id"
    )
    stored = machines.assigned_quantities(context.session, [int(row.id) for row in machine_rows])
    derived: dict[int, int] = defaultdict(int)
    assigned_flows: list[tuple[int, int]] = []  # (flow id, replayed Machine id)
    for flow_id, position in positions.items():
        if position.machine_id is not None:
            derived[position.machine_id] += flows[flow_id].quantity
            assigned_flows.append((flow_id, position.machine_id))
    by_id = {int(row.id): row for row in machine_rows}
    findings: list[Finding] = []
    for machine in machine_rows:
        machine_id = int(machine.id)
        detail: dict[str, object] = {"name": machine.name, "asset_tag": machine.asset_tag}
        if derived.get(machine_id, 0) != stored.get(machine_id, 0):
            findings.append(
                Finding(
                    "MACHINE_ASSIGNED_MISMATCH",
                    "Machine",
                    machine_id,
                    None,
                    derived.get(machine_id, 0),
                    stored.get(machine_id, 0),
                    detail,
                )
            )
        if machine.retired_on is not None and derived.get(machine_id, 0) > 0:
            findings.append(
                Finding(
                    "RETIRED_MACHINE_HOLDS_QUANTITY",
                    "Machine",
                    machine_id,
                    None,
                    0,
                    derived[machine_id],
                    {**detail, "retired_on": machine.retired_on.isoformat()},
                )
            )
    for flow_id, machine_id in assigned_flows:
        area_id = positions[flow_id].area_id
        machine = by_id.get(machine_id)
        if machine is not None and int(machine.area_id) != area_id:
            findings.append(
                Finding(
                    "MACHINE_AREA_MISMATCH",
                    "QuantityFlow",
                    flow_id,
                    flows[flow_id].part_number,
                    int(machine.area_id),
                    area_id,
                    {"machine_id": machine_id},
                )
            )
    return _Outcome(
        {"machines": len(machine_rows), "assigned_flows": len(assigned_flows)}, findings
    )


# ---------------------------------------------------------------------------
# (e) Release evidence and Work Order status
# ---------------------------------------------------------------------------


def _lines_by_work_order(demands: Iterable[_Demand]) -> dict[int, list[_Demand]]:
    lines: dict[int, list[_Demand]] = defaultdict(list)
    for demand in demands:
        lines[demand.work_order_id].append(demand)
    return lines


def _check_e(context: _Context) -> _Outcome:
    demands = context.demands()
    orders = context.work_orders()
    released = context.released()
    replay_completed = context.completed()
    findings: list[Finding] = []
    for row in context.rows(
        "SELECT m.id, m.quantity_flow_id, m.part_number, m.quantity,"
        " m.metadata -> 'context' ->> 'work_order_demand_id' AS demand_ref,"
        " d.id AS demand_id, d.part_number AS demand_part_number"
        " FROM part_movements m LEFT JOIN work_order_demands d"
        " ON d.id = (m.metadata -> 'context' ->> 'work_order_demand_id')::bigint"
        " WHERE m.movement_type = :received AND (d.id IS NULL OR d.part_number <> m.part_number)",
        received=MovementType.RECEIVED,
    ):
        detail: dict[str, object] = {
            "quantity_flow_id": int(row.quantity_flow_id),
            "quantity": int(row.quantity),
        }
        if row.demand_ref is None:
            findings.append(
                Finding(
                    "RELEASE_WITHOUT_DEMAND",
                    "PartMovement",
                    int(row.id),
                    row.part_number,
                    None,
                    None,
                    detail,
                )
            )
        elif row.demand_id is None:
            findings.append(
                Finding(
                    "RELEASE_DEMAND_MISSING",
                    "PartMovement",
                    int(row.id),
                    row.part_number,
                    None,
                    int(row.demand_ref),
                    detail,
                )
            )
        else:
            findings.append(
                Finding(
                    "RELEASE_PN_MISMATCH",
                    "PartMovement",
                    int(row.id),
                    row.part_number,
                    row.demand_part_number,
                    row.part_number,
                    {**detail, "work_order_demand_id": int(row.demand_id)},
                )
            )
    for demand in demands.values():
        if released.get(demand.id, 0) > demand.requested_quantity:
            findings.append(
                Finding(
                    "RELEASED_EXCEEDS_REQUESTED",
                    "WorkOrderDemand",
                    demand.id,
                    demand.part_number,
                    demand.requested_quantity,
                    released[demand.id],
                    {"work_order_id": demand.work_order_id},
                )
            )
    lines = _lines_by_work_order(demands.values())
    for work_order_id, work_order_lines in lines.items():
        by_part_number: dict[str, list[int]] = defaultdict(list)
        for demand in work_order_lines:
            by_part_number[demand.part_number].append(demand.id)
        for part_number, demand_ids in by_part_number.items():
            if len(demand_ids) > 1:
                findings.append(
                    Finding(
                        "DUPLICATE_PN_ON_WORK_ORDER",
                        "WorkOrder",
                        work_order_id,
                        part_number,
                        1,
                        len(demand_ids),
                        {"part_number": part_number, "demand_ids": sorted(demand_ids)},
                    )
                )
    for work_order in orders.values():
        if work_order.status != WorkOrderStatus.OPEN:
            findings.append(
                Finding(
                    "WORK_ORDER_STATUS_STORED",
                    "WorkOrder",
                    work_order.id,
                    None,
                    WorkOrderStatus.OPEN.value,
                    work_order.status,
                    {},
                )
            )
        pairs = [(demand.id, demand.requested_quantity) for demand in lines.get(work_order.id, [])]
        expected = work_orders.derived_status(
            replay_completed.get(work_order.id), work_order.status, pairs, released
        )
        actual = work_orders.derived_status(
            work_order.completed_at, work_order.status, pairs, released
        )
        if expected != actual:
            findings.append(
                Finding(
                    "WORK_ORDER_STATUS_MISMATCH",
                    "WorkOrder",
                    work_order.id,
                    None,
                    expected,
                    actual,
                    {
                        "completed_at": _iso(work_order.completed_at),
                        "replayed_completed_at": _iso(replay_completed.get(work_order.id)),
                    },
                )
            )
    release_movements = context.scalar_int(
        "SELECT count(*) FROM part_movements WHERE movement_type = :received",
        received=MovementType.RECEIVED,
    )
    return _Outcome(
        {
            "work_orders": len(orders),
            "demands": len(demands),
            "release_movements": release_movements,
        },
        findings,
    )


# ---------------------------------------------------------------------------
# (f) Allocations and Work Order completion
# ---------------------------------------------------------------------------


def _completion_kind(stored: datetime.datetime | None, replayed: datetime.datetime | None) -> str:
    if stored is None:
        return "not_completed_but_fully_allocated"
    if replayed is None:
        return "completed_but_not_fully_allocated"
    return "different_done_date"


def _latest_demand_changes(
    context: _Context, work_order_ids: list[int]
) -> dict[int, datetime.datetime]:
    """Newest ``UPDATED`` audit row per Work Order on it or its demand lines."""
    if not work_order_ids:
        return {}
    keys = [str(work_order_id) for work_order_id in work_order_ids]
    rows = context.rows(
        "SELECT owner, max(occurred_at) AS changed_at FROM ("
        " SELECT entity_id AS owner, occurred_at FROM audit_events"
        " WHERE event_type = :updated AND entity_type = :work_order AND entity_id = ANY(:keys)"
        " UNION ALL"
        " SELECT coalesce(after_data, before_data) ->> 'work_order_id', occurred_at"
        " FROM audit_events WHERE event_type = :updated AND entity_type = :demand"
        " AND coalesce(after_data, before_data) ->> 'work_order_id' = ANY(:keys)"
        ") changes GROUP BY owner",
        updated=AuditEventType.UPDATED,
        work_order=AuditEntityType.WORK_ORDER,
        demand=AuditEntityType.WORK_ORDER_DEMAND,
        keys=keys,
    )
    return {int(row.owner): row.changed_at for row in rows}


def _check_f(context: _Context) -> _Outcome:
    demands = context.demands()
    orders = context.work_orders()
    allocated = context.allocated()
    replay_completed = context.completed()
    # The authorized beyond-demand correction (Phase 14 slice 5): the
    # active quantity of rows recorded `exceeds_demand` is allowed beyond
    # the requested quantity — only an excess it does not cover is a
    # finding (the CHECK makes such a row Management, reasoned and
    # User-recorded, so the flag is trusted).
    authorized = {
        int(row.work_order_demand_id): int(row.quantity)
        for row in context.rows(
            "SELECT a.work_order_demand_id, sum(a.quantity) AS quantity"
            " FROM work_order_allocations a WHERE a.exceeds_demand"
            " AND a.reverses_allocation_id IS NULL AND NOT EXISTS"
            " (SELECT 1 FROM work_order_allocations r WHERE r.reverses_allocation_id = a.id)"
            " GROUP BY a.work_order_demand_id"
        )
    }
    findings: list[Finding] = []
    for demand in demands.values():
        derived = allocated.get(demand.id, 0)
        if demand.allocated_quantity != derived:
            findings.append(
                Finding(
                    "ALLOCATED_QUANTITY_MISMATCH",
                    "WorkOrderDemand",
                    demand.id,
                    demand.part_number,
                    derived,
                    demand.allocated_quantity,
                    {"work_order_id": demand.work_order_id},
                )
            )
        if derived - authorized.get(demand.id, 0) > demand.requested_quantity:
            findings.append(
                Finding(
                    "ALLOCATED_EXCEEDS_REQUESTED",
                    "WorkOrderDemand",
                    demand.id,
                    demand.part_number,
                    demand.requested_quantity,
                    derived,
                    {"work_order_id": demand.work_order_id},
                )
            )
    mismatched = [
        work_order
        for work_order in orders.values()
        if work_order.completed_at != replay_completed.get(work_order.id)
    ]
    changes = _latest_demand_changes(
        context,
        [
            work_order.id
            for work_order in mismatched
            if _completion_kind(work_order.completed_at, replay_completed.get(work_order.id))
            == "not_completed_but_fully_allocated"
        ],
    )
    for work_order in mismatched:
        replayed = replay_completed.get(work_order.id)
        kind = _completion_kind(work_order.completed_at, replayed)
        detail: dict[str, object] = {"kind": kind}
        if kind == "not_completed_but_fully_allocated":
            detail["latest_demand_change_at"] = _iso(changes.get(work_order.id))
        findings.append(
            Finding(
                "COMPLETED_AT_MISMATCH",
                "WorkOrder",
                work_order.id,
                None,
                _iso(replayed),
                _iso(work_order.completed_at),
                detail,
            )
        )
    for row in context.rows(
        "WITH allocated AS (SELECT a.part_number, sum(a.quantity) AS quantity"
        " FROM work_order_allocations a WHERE a.reverses_allocation_id IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM work_order_allocations r"
        " WHERE r.reverses_allocation_id = a.id) GROUP BY a.part_number),"
        " stocked AS (SELECT m.part_number, sum(m.quantity) AS quantity FROM part_movements m"
        " WHERE m.movement_type = :stocked AND NOT EXISTS (SELECT 1 FROM part_movements r"
        " WHERE r.reverses_movement_id = m.id) GROUP BY m.part_number)"
        " SELECT allocated.part_number, allocated.quantity AS allocated,"
        " coalesce(stocked.quantity, 0) AS stocked"
        " FROM allocated LEFT JOIN stocked ON stocked.part_number = allocated.part_number"
        " WHERE allocated.quantity > coalesce(stocked.quantity, 0)",
        stocked=MovementType.STOCKED,
    ):
        findings.append(
            Finding(
                "PN_OVER_ALLOCATED",
                "PN",
                row.part_number,
                row.part_number,
                int(row.stocked),
                int(row.allocated),
                {},
            )
        )
    for row in context.rows(
        "SELECT a.id, a.part_number, a.work_order_demand_id, d.part_number AS demand_part_number"
        " FROM work_order_allocations a JOIN work_order_demands d"
        " ON d.id = a.work_order_demand_id WHERE a.part_number <> d.part_number"
    ):
        findings.append(
            Finding(
                "ALLOCATION_PN_MISMATCH",
                "WorkOrderAllocation",
                int(row.id),
                row.part_number,
                row.demand_part_number,
                row.part_number,
                {"work_order_demand_id": int(row.work_order_demand_id)},
            )
        )
    for row in context.rows(
        "SELECT r.id, r.part_number, r.work_order_demand_id, r.quantity,"
        " o.id AS original_id, o.part_number AS original_part_number,"
        " o.work_order_demand_id AS original_demand_id, o.quantity AS original_quantity,"
        " o.reverses_allocation_id AS original_reverses"
        " FROM work_order_allocations r JOIN work_order_allocations o"
        " ON o.id = r.reverses_allocation_id"
        " WHERE r.work_order_demand_id <> o.work_order_demand_id"
        " OR r.part_number <> o.part_number OR r.quantity <> o.quantity"
        " OR o.reverses_allocation_id IS NOT NULL"
    ):
        findings.append(
            Finding(
                "ALLOCATION_REVERSAL_MISMATCH",
                "WorkOrderAllocation",
                int(row.id),
                row.part_number,
                {
                    "work_order_demand_id": int(row.original_demand_id),
                    "part_number": row.original_part_number,
                    "quantity": int(row.original_quantity),
                    "reverses_allocation_id": None,
                },
                {
                    "work_order_demand_id": int(row.work_order_demand_id),
                    "part_number": row.part_number,
                    "quantity": int(row.quantity),
                    "reverses_allocation_id": (
                        None if row.original_reverses is None else int(row.original_reverses)
                    ),
                },
                {"reverses_allocation_id": int(row.original_id)},
            )
        )
    allocation_rows = context.scalar_int("SELECT count(*) FROM work_order_allocations")
    return _Outcome(
        {"demands": len(demands), "work_orders": len(orders), "allocation_rows": allocation_rows},
        findings,
    )


# ---------------------------------------------------------------------------
# (i) Hot list entries are active demand
# ---------------------------------------------------------------------------


def _check_i(context: _Context) -> _Outcome:
    inactive = [int(row.id) for row in context.rows(HOT_LIST_CHECK_SQL)]
    findings: list[Finding] = []
    if inactive:
        for row in context.rows(
            "SELECT d.id, d.part_number, d.priority_rank, d.work_order_id, w.completed_at,"
            " d.requested_quantity, d.allocated_quantity FROM work_order_demands d"
            " JOIN work_orders w ON w.id = d.work_order_id WHERE d.id = ANY(:ids)",
            ids=inactive,
        ):
            findings.append(
                Finding(
                    "HOT_ENTRY_INACTIVE",
                    "WorkOrderDemand",
                    int(row.id),
                    row.part_number,
                    None,
                    int(row.priority_rank),
                    {
                        "priority_rank": int(row.priority_rank),
                        "work_order_id": int(row.work_order_id),
                        "completed_at": _iso(row.completed_at),
                        "requested_quantity": int(row.requested_quantity),
                        "allocated_quantity": int(row.allocated_quantity),
                    },
                )
            )
    ranked = context.scalar_int(
        "SELECT count(*) FROM work_order_demands WHERE priority_rank IS NOT NULL"
    )
    return _Outcome({"ranked_demands": ranked}, findings)


# ---------------------------------------------------------------------------
# (j) Canonical identity under the running interpreter and database
# ---------------------------------------------------------------------------


def changed_code_points(value: str) -> list[str]:
    """The code points canonicalization changes or refuses (``U+XXXX``)."""
    return sorted(
        {f"U+{ord(character):04X}" for character in value if character.upper() != character}
        | {f"U+{ord(character):04X}" for character in value if character.isspace()}
    )


def part_number_identity_findings(values: Mapping[str, Collection[str]]) -> list[Finding]:
    """(j)1 over the stored PN values (value → source tables), pure."""
    findings: list[Finding] = []
    by_canonical: dict[str, list[str]] = defaultdict(list)
    for value, tables in values.items():
        try:
            canonical = normalize_part_number(value)
        except InvalidPartNumberError as exc:
            findings.append(
                Finding(
                    "PN_NOT_CANONICAL",
                    "PN",
                    value,
                    value,
                    None,
                    value,
                    {"tables": sorted(tables), "error": str(exc)},
                )
            )
            continue
        by_canonical[canonical].append(value)
        if canonical != value:
            findings.append(
                Finding(
                    "PN_NOT_CANONICAL",
                    "PN",
                    value,
                    value,
                    canonical,
                    value,
                    {"tables": sorted(tables), "changed_code_points": changed_code_points(value)},
                )
            )
    for canonical, stored in by_canonical.items():
        if len(stored) > 1:
            findings.append(
                Finding(
                    "PN_CANONICAL_COLLISION",
                    "PN",
                    canonical,
                    canonical,
                    canonical,
                    sorted(stored),
                    {},
                )
            )
    return findings


def badge_identity_findings(workers: Iterable[tuple[int, str, bool]]) -> list[Finding]:
    """(j)2 over the stored Worker badges (id, badge, is_active), pure.

    A collision is two DIFFERENT stored badges with one canonical form;
    the same stored badge on two Workers is the identity-key probe's
    ``UNIQUE_KEY_DUPLICATED``.
    """
    findings: list[Finding] = []
    by_canonical: dict[str, list[int]] = defaultdict(list)
    stored_forms: dict[str, set[str]] = defaultdict(set)
    for entity_id, badge, is_active in workers:
        try:
            canonical = normalize_badge_barcode(badge)
        except InvalidBadgeBarcodeError as exc:
            findings.append(
                Finding(
                    "BADGE_NOT_CANONICAL",
                    "Worker",
                    entity_id,
                    None,
                    None,
                    badge,
                    {"value": badge, "is_active": is_active, "error": str(exc)},
                )
            )
            continue
        by_canonical[canonical].append(entity_id)
        stored_forms[canonical] |= {badge}
        if canonical != badge:
            findings.append(
                Finding(
                    "BADGE_NOT_CANONICAL",
                    "Worker",
                    entity_id,
                    None,
                    canonical,
                    badge,
                    {
                        "value": badge,
                        "is_active": is_active,
                        "changed_code_points": changed_code_points(badge),
                    },
                )
            )
    for canonical, worker_ids in by_canonical.items():
        if len(stored_forms[canonical]) > 1:
            findings.append(
                Finding(
                    "BADGE_CANONICAL_COLLISION",
                    "Worker",
                    canonical,
                    None,
                    canonical,
                    sorted(worker_ids),
                    {},
                )
            )
    return findings


def collation_version_findings(
    database_name: str, recorded: str | None, actual: str | None
) -> list[Finding]:
    """(j)5: PostgreSQL's own signal that default-collation indexes may be stale."""
    if recorded is None or actual is None or recorded == actual:
        return []
    return [
        Finding(
            "COLLATION_VERSION_MISMATCH",
            "Database",
            database_name,
            None,
            recorded,
            actual,
            {},
        )
    ]


class _CanonicalCheck(NamedTuple):
    table: str
    key: str
    column: str
    entity_type: str
    constraint: str
    expression: str
    holds_part_number: bool


_CANONICAL_CHECKS: Final = (
    _CanonicalCheck(
        "part_numbers",
        "part_number",
        "part_number",
        "PartNumber",
        "ck_part_numbers_part_number_canonical",
        CANONICAL_PART_NUMBER_SQL,
        True,
    ),
    _CanonicalCheck(
        "work_order_demands",
        "id",
        "part_number",
        "WorkOrderDemand",
        "ck_work_order_demands_part_number_canonical",
        CANONICAL_PART_NUMBER_SQL,
        True,
    ),
    _CanonicalCheck(
        "quantity_flows",
        "id",
        "part_number",
        "QuantityFlow",
        "ck_quantity_flows_part_number_canonical",
        CANONICAL_PART_NUMBER_SQL,
        True,
    ),
    _CanonicalCheck(
        "work_order_allocations",
        "id",
        "part_number",
        "WorkOrderAllocation",
        "ck_work_order_allocations_part_number_canonical",
        CANONICAL_PART_NUMBER_SQL,
        True,
    ),
    _CanonicalCheck(
        "workers",
        "id",
        "badge_barcode",
        "Worker",
        "ck_workers_badge_barcode_canonical",
        WORKER_BADGE_BARCODE_SQL,
        False,
    ),
    _CanonicalCheck(
        "machines",
        "id",
        "asset_tag",
        "Machine",
        "ck_machines_asset_tag_canonical",
        MACHINE_ASSET_TAG_SQL,
        False,
    ),
    _CanonicalCheck(
        "machine_asset_tag_config",
        "id",
        "prefix",
        "MachineAssetTagConfig",
        "ck_machine_asset_tag_config_prefix",
        ASSET_TAG_PREFIX_SQL,
        False,
    ),
    _CanonicalCheck(
        "areas",
        "id",
        "barcode_value",
        "Area",
        "ck_areas_barcode_value_namespace",
        AREA_BARCODE_SQL,
        False,
    ),
)


class _IdentityKey(NamedTuple):
    table: str
    columns: tuple[str, ...]
    row_id: str
    entity_type: str
    constraint: str


_IDENTITY_KEYS: Final = (
    _IdentityKey("part_numbers", ("part_number",), "ctid::text", "PartNumber", "pk_part_numbers"),
    _IdentityKey("workers", ("badge_barcode",), "id", "Worker", "uq_workers_badge_barcode"),
    _IdentityKey("machines", ("asset_tag",), "id", "Machine", "uq_machines_asset_tag"),
    _IdentityKey("areas", ("barcode_value",), "id", "Area", "uq_areas_barcode_value"),
    _IdentityKey("users", ("login_name",), "id", "User", "uq_users_login_name"),
    _IdentityKey(
        "part_movements",
        ("device_event_id", "command_sequence"),
        "id",
        "PartMovement",
        DEVICE_EVENT_ID_CONSTRAINT,
    ),
    _IdentityKey(
        "work_order_allocations",
        ("device_event_id", "command_sequence"),
        "id",
        "WorkOrderAllocation",
        ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT,
    ),
)


def _identity_key_findings(context: _Context) -> list[Finding]:
    """(j)6: duplicate identity keys found without trusting their indexes.

    The text column is grouped under ``COLLATE "C"`` (byte order), so
    the planner cannot answer from the default-collation unique index
    a libc change may have corrupted.
    """
    findings: list[Finding] = []
    for key in _IDENTITY_KEYS:
        grouped = ", ".join(
            f'{column} COLLATE "C"' if column != "command_sequence" else column
            for column in key.columns
        )
        not_null = " AND ".join(f"{column} IS NOT NULL" for column in key.columns)
        for row in context.rows(
            f"SELECT {grouped}, count(*) AS count,"
            f" (array_agg({key.row_id} ORDER BY {key.row_id}))[1:10] AS row_ids"
            f" FROM {key.table} WHERE {not_null} GROUP BY {grouped} HAVING count(*) > 1"
        ):
            values = [str(value) for value in row[: len(key.columns)]]
            findings.append(
                Finding(
                    "UNIQUE_KEY_DUPLICATED",
                    key.entity_type,
                    "/".join(values),
                    values[0] if key.table == "part_numbers" else None,
                    1,
                    [value if isinstance(value, str) else int(value) for value in row.row_ids],
                    {"table": key.table, "constraint": key.constraint, "count": int(row.count)},
                )
            )
    return findings


# ---------------------------------------------------------------------------
# (h) Append-only guards and database-role privileges intact
# ---------------------------------------------------------------------------


def _check_h(context: _Context) -> _Outcome:
    """Guard integrity (OD-16-09), read from the catalogs inside the snapshot."""
    integrity = guard_integrity(
        context.session.connection(), roles=context.roles, required=context.roles_required
    )
    findings = [
        Finding(
            finding.code,
            finding.entity_type,
            finding.entity_id,
            None,
            finding.expected,
            finding.actual,
            finding.detail,
        )
        for finding in integrity.findings
    ]
    return _Outcome(integrity.examined, findings, integrity.not_applicable_reason)


def _check_j(context: _Context) -> _Outcome:
    findings: list[Finding] = []
    # 1. Canonical PNs, wherever production stores one by value.
    part_numbers: dict[str, list[str]] = {
        row.value: list(row.tables)
        for row in context.rows(
            'SELECT value COLLATE "C" AS value, array_agg(DISTINCT source ORDER BY source)'
            " AS tables FROM ("
            " SELECT part_number AS value, 'part_numbers' AS source FROM part_numbers"
            " UNION ALL SELECT DISTINCT part_number, 'work_order_demands' FROM work_order_demands"
            " UNION ALL SELECT DISTINCT part_number, 'quantity_flows' FROM quantity_flows"
            " UNION ALL SELECT DISTINCT part_number, 'work_order_allocations'"
            " FROM work_order_allocations"
            ') pn GROUP BY value COLLATE "C"'
        )
    }
    findings.extend(part_number_identity_findings(part_numbers))
    # 2. Case-insensitive Worker badges (OD-3).
    workers = [
        (int(row.id), str(row.badge_barcode), bool(row.is_active))
        for row in context.rows("SELECT id, badge_barcode, is_active FROM workers ORDER BY id")
    ]
    findings.extend(badge_identity_findings(workers))
    # 3. The Asset Tag prefix rule under the running interpreter.
    for row in context.rows("SELECT id, prefix FROM machine_asset_tag_config ORDER BY id"):
        refused = environment.ASSET_TAG_PREFIX_FORBIDDEN.findall(row.prefix)
        if refused:
            findings.append(
                Finding(
                    "ASSET_TAG_PREFIX_REFUSED",
                    "MachineAssetTagConfig",
                    int(row.id),
                    None,
                    None,
                    row.prefix,
                    {"refused_code_points": sorted({f"U+{ord(c):04X}" for c in refused})},
                )
            )
    # 4. Every canonical-form CHECK re-evaluated by the running server.
    check_rows = 0
    for check in _CANONICAL_CHECKS:
        not_null = f"{check.column} IS NOT NULL"
        check_rows += context.scalar_int(f"SELECT count(*) FROM {check.table} WHERE {not_null}")
        for row in context.rows(
            f"SELECT {check.key} AS key, {check.column} AS value FROM {check.table}"
            f" WHERE {not_null} AND NOT ({check.expression})"
        ):
            findings.append(
                Finding(
                    "CHECK_OUTCOME_CHANGED",
                    check.entity_type,
                    row.key if isinstance(row.key, str) else int(row.key),
                    row.value if check.holds_part_number else None,
                    True,
                    False,
                    {"constraint": check.constraint, "table": check.table, "value": row.value},
                )
            )
    # 5. The collation version PostgreSQL recorded vs. the running libc's
    # (read once by the database block, inside the snapshot).
    database = context.database
    findings.extend(
        collation_version_findings(
            str(database["name"]),
            _optional_text(database["collation_version_recorded"]),
            _optional_text(database["collation_version_actual"]),
        )
    )
    # 6. Identity keys without trusting their indexes.
    findings.extend(_identity_key_findings(context))
    return _Outcome(
        {
            "part_numbers": len(part_numbers),
            "badges": len(workers),
            "check_rows": check_rows,
            "identity_keys": len(_IDENTITY_KEYS),
        },
        findings,
    )
