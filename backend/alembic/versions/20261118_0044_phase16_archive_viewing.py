"""Phase 16: viewing an archive in PartFlow (design B, step 5; decision D16).

The fifth build step of the approved Movement-history archival design
(``_claude_outputs/phase16/ARCHIVAL_DESIGN.md`` §9.1, §9.3, §10 and §13 step
5; IMPLEMENTATION_ROADMAP Phase 16). A holder of Run history archival and
purge opens the verified archive of a finished run for viewing; the host job
loads its files into viewer tables of their own, never into live history, and
removes the loaded rows again when a person closes the view or its days run
out. No reader of live history, no projection and no reconcile check but (h)
(which pins the guards below) and (j) (the Part Number columns) reads them.

- ``history_archive_views`` — one row per viewing request ("view"): the run,
  who opened it and when, its state (``REQUESTED``, ``LOADING``, ``OPEN``,
  ``CLOSING``; ``CLOSED``, ``FAILED``, ``CANCELLED``), the job's progress (the
  load's start, when it opened and how many rows it loaded, when its closing
  started and who closed it, the end, the failure reason, who cancelled it).
  At most one view of a run is active (``REQUESTED``, ``LOADING`` or ``OPEN``:
  ``uq_history_archive_views_run_active``). Grant class ``GUARDED_UPDATE``;
  the row trigger ``trg_history_archive_views_guard_mutation`` admits an
  insert only of a ``REQUESTED`` view of a finished run whose archive was
  verified, by a person (never the maintenance role), while fewer views are
  active than the setting allows (serialized by a transaction-level advisory
  lock); the application role may only cancel a ``REQUESTED`` view and close
  an ``OPEN`` one (``CLOSING``); every other change is the host job's (a role
  that may write archive records, while ``partflow.archive_view_id`` names
  the view): ``REQUESTED`` to ``LOADING`` or ``FAILED``, ``LOADING`` to
  ``OPEN`` or ``FAILED``, ``OPEN`` to ``CLOSING``, ``CLOSING`` to ``CLOSED``,
  a view ending ``FAILED`` or ``CLOSED`` only once its loaded rows are gone.
  The request never changes, a finished view never changes, and no view is
  deleted.
- The viewer tables, every row keyed by its view: ``archive_view_movements``
  (every column of each archived Movement), ``archive_view_flows`` (each
  archived flow with its carry-forward record and the Work Orders it was
  released for, directly or through the flows it was split or merged from),
  ``archive_view_lineage`` (the lineage edges, ``void`` for an undone
  command), ``archive_view_routes`` (the Assigned Route snapshots with their
  steps) and ``archive_view_names`` (the names of ``context.json``: Areas,
  Operations, Machines, Workers, Scan Stations, Work Orders, demand lines,
  Users and Part Numbers as they were at export). Grant class ``READ_ONLY``.
  Their statement-level guards (function ``partflow_archive_view_rows_guard``)
  judge every row of a statement through its transition table: an insert
  only by a role that may write archive records while the view
  ``partflow.archive_view_id`` names is ``LOADING`` and every new row is that
  view's; a delete only while that view is ``LOADING`` (a failed or stopped
  load is removed) or ``CLOSING`` and every deleted row is that view's; no
  update and no truncate, for every role, the owner included. Their
  ``view_id`` collects no statistics (``SET STATISTICS 0``): a view loaded
  since the last ANALYZE would otherwise read as empty to the planner.
- ``application_policy``: ``archive_view_days`` (the days a loaded archive
  stays open, 7; 1-365) and ``archive_view_max_loaded`` (how many runs may be
  loaded at once, 1; 1-10).
- ``ck_audit_events_entity_type`` gains ``HistoryArchiveView``, and
  ``partflow_audit_events_maintenance_scope`` (replaced) lets the maintenance
  role also append the ``HistoryArchiveView`` rows of the view it works on.

The maintenance role's new privileges (SELECT and UPDATE on the views;
SELECT, INSERT and DELETE on the viewer tables) are grants, derived by
``apply-grants`` in every ``migrate`` from
``app.infrastructure.database_privileges``.

The downgrade REFUSES while a view or a ``HistoryArchiveView`` audit row
exists, or while a new setting differs from its default; otherwise it
restores ``0043`` exactly. ``alembic/env.py`` runs in one transaction. Run it
only against disposable development and test databases.

Revision ID: 0044_phase16_archive_viewing
Revises: 0043_phase16_automatic_runs
Create Date: 2026-11-18

"""

from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044_phase16_archive_viewing"
down_revision: str | None = "0043_phase16_automatic_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py or enums
# counterpart, and each _PREVIOUS_ value equals migration 0042's.
_VIEWS = "history_archive_views"
_RUNS = "history_archive_runs"
_POLICY = "application_policy"
_AUDIT = "audit_events"

_STATES = ("REQUESTED", "LOADING", "OPEN", "CLOSING", "CLOSED", "FAILED", "CANCELLED")
_FINAL_STATES = ("CLOSED", "FAILED", "CANCELLED")
#: The views that count against the setting of loaded runs, one per run.
_ACTIVE_STATES = ("REQUESTED", "LOADING", "OPEN")
#: The run states a viewable run may be in (finished), with ``verified_at`` set.
_RUN_FINAL_STATES = (
    "COMPLETED",
    "COMPLETED_WITH_SKIPS",
    "NOTHING_ELIGIBLE",
    "DISMISSED",
    "CANCELLED",
    "FAILED",
)
#: The kinds of names a view keeps (the sections of the archive's context.json).
_NAME_KINDS = (
    "areas",
    "machines",
    "operations",
    "part_numbers",
    "scan_stations",
    "users",
    "work_order_demands",
    "work_orders",
    "workers",
)
#: The viewer tables, in the order the job removes a view's rows.
_VIEWER_TABLES = (
    "archive_view_movements",
    "archive_view_flows",
    "archive_view_lineage",
    "archive_view_routes",
    "archive_view_names",
)
#: The canonical form of a Part Number (models.CANONICAL_PART_NUMBER_SQL, 0018).
_CANONICAL_PART_NUMBER_SQL = (
    """part_number = upper(part_number COLLATE "C")"""
    """ AND part_number COLLATE "C" !~ '[[:space:]]' AND part_number <> ''"""
)
#: The same rule for the Part Number names of a view (their key).
_CANONICAL_NAME_KEY_SQL = (
    """kind <> 'part_numbers' OR (key = upper(key COLLATE "C")"""
    """ AND key COLLATE "C" !~ '[[:space:]]' AND key <> '')"""
)


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_VIEW_CHECKS = (
    ("state", _in("state", _STATES)),
    (
        "progress_shape",
        "(state NOT IN ('REQUESTED', 'CANCELLED') OR started_at IS NULL)"
        " AND (state NOT IN ('LOADING', 'OPEN', 'CLOSING', 'CLOSED') OR started_at IS NOT NULL)"
        " AND (started_at IS NULL OR started_at >= requested_at)",
    ),
    (
        "loaded_shape",
        "(opened_at IS NULL) = (state NOT IN ('OPEN', 'CLOSING', 'CLOSED'))"
        " AND (opened_at IS NULL) = (loaded_movements IS NULL)"
        " AND (opened_at IS NULL) = (loaded_flows IS NULL)"
        " AND (opened_at IS NULL) = (loaded_lineage_edges IS NULL)"
        " AND (opened_at IS NULL) = (loaded_assigned_routes IS NULL)"
        " AND (opened_at IS NULL) = (loaded_names IS NULL)"
        " AND (opened_at IS NULL OR (opened_at >= started_at AND loaded_movements >= 0"
        " AND loaded_flows >= 0 AND loaded_lineage_edges >= 0 AND loaded_assigned_routes >= 0"
        " AND loaded_names >= 0))",
    ),
    (
        "closing_shape",
        "(closing_started_at IS NULL) = (state NOT IN ('CLOSING', 'CLOSED'))"
        " AND (closed_by_user_id IS NULL OR closing_started_at IS NOT NULL)"
        " AND (closing_started_at IS NULL OR closing_started_at >= opened_at)",
    ),
    (
        "failure_shape",
        "(state = 'FAILED') = (failure_reason IS NOT NULL)"
        " AND (failure_reason IS NULL OR char_length(failure_reason) BETWEEN 1 AND 2000)",
    ),
    (
        "finish_shape",
        "(" + _in("state", _FINAL_STATES) + ") = (finished_at IS NOT NULL)"
        " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)"
        " AND (finished_at IS NULL OR finished_at >= requested_at)",
    ),
)
_ACTIVE_INDEX = "uq_history_archive_views_run_active"

# (column, type, server default) appended to application_policy, in the
# model's order; NOT NULL.
_POLICY_COLUMNS: tuple[tuple[str, Any, str], ...] = (
    ("archive_view_days", sa.Integer(), "7"),
    ("archive_view_max_loaded", sa.Integer(), "1"),
)
# (column, default, minimum, maximum); the CHECK of each is
# ck_application_policy_<column>_range: "<column> BETWEEN minimum AND maximum".
_POLICY_RANGES = (
    ("archive_view_days", 7, 1, 365),
    ("archive_view_max_loaded", 1, 1, 10),
)

_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
# Exactly what 0042 left (the downgrade restores it).
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute', 'HistoryArchiveRun', 'DeletionRequest')"
)
_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute', 'HistoryArchiveRun', 'DeletionRequest',"
    " 'HistoryArchiveView')"
)

# The guard functions and triggers (pinned by app.infrastructure.database_privileges:
# GUARD_TRIGGERS and the SHA-256 of each function's source).
_VIEWS_FUNCTION = "partflow_history_archive_views_guard_mutation"
_ROWS_FUNCTION = "partflow_archive_view_rows_guard"
_AUDIT_FUNCTION = "partflow_audit_events_maintenance_scope"
# The "constraint" the refusal of a request beyond the setting of loaded
# runs names (SQLSTATE 23514), so the request answers 409.
_LIMIT_GUARD = "trg_history_archive_views_guard_mutation"
#: The advisory lock that serializes the requests of views (the application
#: takes it too, before it counts the active views).
_LIMIT_LOCK = "partflow:history-archive-views"

_VIEWS_BODY = f"""
DECLARE
    job_role boolean := has_table_privilege('public.history_archived_flows', 'INSERT');
    table_owner boolean := pg_has_role(
        (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE');
    max_loaded integer;
    active integer;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archive_views rows are never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'REQUESTED' OR NEW.started_at IS NOT NULL OR NEW.opened_at IS NOT NULL
            OR NEW.closing_started_at IS NOT NULL OR NEW.closed_by_user_id IS NOT NULL
            OR NEW.finished_at IS NOT NULL OR NEW.failure_reason IS NOT NULL
            OR NEW.cancelled_by_user_id IS NOT NULL THEN
            RAISE EXCEPTION 'a view of an archive is requested REQUESTED, with no progress';
        END IF;
        IF NEW.requested_at IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'a view of an archive is requested at the start of its transaction';
        END IF;
        -- Only a person opens an archive for viewing, never the maintenance role (it
        -- may write archive records and does not own this table).
        IF job_role AND NOT table_owner THEN
            RAISE EXCEPTION 'only a person opens an archive for viewing';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM public.history_archive_runs r WHERE r.id = NEW.run_id
            AND r.state IN ('COMPLETED', 'COMPLETED_WITH_SKIPS', 'NOTHING_ELIGIBLE', 'DISMISSED',
                'CANCELLED', 'FAILED')
            AND r.verified_at IS NOT NULL) THEN
            RAISE EXCEPTION 'run % is not a finished run whose archive was verified', NEW.run_id;
        END IF;
        -- At most the set number of runs loaded at once: the requests are serialized.
        PERFORM pg_advisory_xact_lock(hashtextextended('{_LIMIT_LOCK}', 0));
        SELECT p.archive_view_max_loaded INTO max_loaded
        FROM public.application_policy p WHERE p.id = 1;
        SELECT count(*) INTO active FROM public.history_archive_views v
        WHERE v.state IN ('REQUESTED', 'LOADING', 'OPEN');
        IF max_loaded IS NULL OR active >= max_loaded THEN
            RAISE EXCEPTION 'at most % runs may be loaded for viewing at once, and % are',
                max_loaded, active
                USING ERRCODE = 'check_violation', CONSTRAINT = '{_LIMIT_GUARD}';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('CLOSED', 'FAILED', 'CANCELLED') THEN
        RAISE EXCEPTION 'view % is finished (%) and never changes', OLD.id, OLD.state;
    END IF;
    IF (NEW.id, NEW.run_id, NEW.requested_by_user_id, NEW.requested_at)
        IS DISTINCT FROM (OLD.id, OLD.run_id, OLD.requested_by_user_id, OLD.requested_at) THEN
        RAISE EXCEPTION 'the request of view % never changes', OLD.id;
    END IF;
    -- The progress is recorded once: the load's start, the opening with its counts,
    -- the start of its closing.
    IF (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
        OR (OLD.opened_at IS NOT NULL AND (NEW.opened_at, NEW.loaded_movements,
            NEW.loaded_flows, NEW.loaded_lineage_edges, NEW.loaded_assigned_routes,
            NEW.loaded_names) IS DISTINCT FROM (OLD.opened_at, OLD.loaded_movements,
            OLD.loaded_flows, OLD.loaded_lineage_edges, OLD.loaded_assigned_routes,
            OLD.loaded_names))
        OR (OLD.closing_started_at IS NOT NULL
            AND (NEW.closing_started_at, NEW.closed_by_user_id)
                IS DISTINCT FROM (OLD.closing_started_at, OLD.closed_by_user_id)) THEN
        RAISE EXCEPTION 'view %: its progress is recorded once', OLD.id;
    END IF;
    -- The host job: a role that may write archive records, working on this view. Only
    -- a person cancels a view or closes it.
    IF job_role AND current_setting('partflow.archive_view_id', true) = OLD.id::text THEN
        IF (NEW.cancelled_by_user_id, NEW.closed_by_user_id)
            IS DISTINCT FROM (OLD.cancelled_by_user_id, OLD.closed_by_user_id) THEN
            RAISE EXCEPTION 'view %: the host job never cancels a view or closes it for a person',
                OLD.id;
        END IF;
        IF (OLD.state = 'REQUESTED' AND NEW.state IN ('LOADING', 'FAILED'))
            OR (OLD.state = 'LOADING' AND NEW.state IN ('OPEN', 'FAILED'))
            OR (OLD.state = 'OPEN' AND NEW.state = 'CLOSING')
            OR (OLD.state = 'CLOSING' AND NEW.state = 'CLOSED') THEN
            -- A view ends only once its loaded rows are gone.
            IF NEW.state IN ('FAILED', 'CLOSED') AND (
                EXISTS (SELECT 1 FROM public.archive_view_movements t WHERE t.view_id = OLD.id)
                OR EXISTS (SELECT 1 FROM public.archive_view_flows t WHERE t.view_id = OLD.id)
                OR EXISTS (SELECT 1 FROM public.archive_view_lineage t WHERE t.view_id = OLD.id)
                OR EXISTS (SELECT 1 FROM public.archive_view_routes t WHERE t.view_id = OLD.id)
                OR EXISTS (SELECT 1 FROM public.archive_view_names t WHERE t.view_id = OLD.id))
            THEN
                RAISE EXCEPTION 'view %: its loaded rows are removed before it ends %',
                    OLD.id, NEW.state;
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'view %: the host job cannot change % to %', OLD.id, OLD.state, NEW.state;
    END IF;
    -- The maintenance role (it may write archive records and does not own this table)
    -- changes a view only as its job.
    IF job_role AND NOT table_owner THEN
        RAISE EXCEPTION 'view %: the maintenance role changes a view only as its job', OLD.id;
    END IF;
    -- The application: cancel a requested view.
    IF NEW.state = 'CANCELLED' AND OLD.state = 'REQUESTED'
        AND to_jsonb(NEW) - ARRAY['state', 'finished_at', 'cancelled_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'finished_at', 'cancelled_by_user_id'] THEN
        RETURN NEW;
    END IF;
    -- The application: close an open view (Close viewing); the host job removes its rows.
    IF NEW.state = 'CLOSING' AND OLD.state = 'OPEN' AND NEW.closed_by_user_id IS NOT NULL
        AND to_jsonb(NEW) - ARRAY['state', 'closing_started_at', 'closed_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'closing_started_at', 'closed_by_user_id'] THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'view %: only the host job makes this change', OLD.id;
END;
"""
_ROWS_BODY = """
DECLARE
    marker text := current_setting('partflow.archive_view_id', true);
    view_state text;
BEGIN
    IF TG_OP = 'UPDATE' OR TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '% rows never change: the host job loads and removes them', TG_TABLE_NAME;
    END IF;
    -- The host job (a role that may write archive records) works on the view the
    -- marker names; every row of the statement must be that view's.
    IF coalesce(marker, '') ~ '^[1-9][0-9]{0,8}$'
        AND has_table_privilege('public.history_archived_flows', 'INSERT') THEN
        SELECT v.state INTO view_state FROM public.history_archive_views v
        WHERE v.id = marker::integer;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF view_state IS DISTINCT FROM 'LOADING' THEN
            RAISE EXCEPTION '% rows are inserted only by the host job loading the view they belong'
                ' to (LOADING)', TG_TABLE_NAME;
        END IF;
        IF EXISTS (SELECT 1 FROM new_rows r WHERE r.view_id IS DISTINCT FROM marker::integer) THEN
            RAISE EXCEPTION 'view %: % rows of another view are not inserted',
                marker, TG_TABLE_NAME;
        END IF;
        RETURN NULL;
    END IF;
    IF view_state IS NULL OR view_state NOT IN ('LOADING', 'CLOSING') THEN
        RAISE EXCEPTION '% rows are deleted only by the host job removing the rows of a failed'
            ' load (LOADING) or closing a view (CLOSING)', TG_TABLE_NAME;
    END IF;
    IF EXISTS (SELECT 1 FROM old_rows r WHERE r.view_id IS DISTINCT FROM marker::integer) THEN
        RAISE EXCEPTION 'view %: % rows of another view are not deleted', marker, TG_TABLE_NAME;
    END IF;
    RETURN NULL;
END;
"""
_AUDIT_BODY = """
DECLARE
    request text := current_setting('partflow.deletion_request_id', true);
    archive_view text := current_setting('partflow.archive_view_id', true);
BEGIN
    -- The maintenance role: it may write archive records and does not own this table.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        -- The view of an archive it loads or closes: that view's own rows.
        IF NEW.entity_type = 'HistoryArchiveView' AND archive_view ~ '^[1-9][0-9]{0,8}$'
            AND NEW.entity_id = archive_view THEN
            RETURN NEW;
        END IF;
        -- The deletion request it works on: the request's own rows, and while it is
        -- RUNNING one DELETED row per Work Order it stored and Part Number of its scope.
        IF request ~ '^[1-9][0-9]{0,8}$' AND (
            (NEW.entity_type = 'DeletionRequest' AND NEW.entity_id = request)
            OR (NEW.event_type = 'DELETED' AND EXISTS (
                SELECT 1 FROM public.deletion_requests q
                WHERE q.id = request::integer AND q.state = 'RUNNING' AND (
                    (NEW.entity_type = 'WorkOrder' AND EXISTS (
                        SELECT 1 FROM public.deletion_request_rows r
                        WHERE r.request_id = q.id AND r.table_name = 'work_orders'
                            AND r.row_key = NEW.entity_id))
                    OR (NEW.entity_type = 'PartNumber' AND NEW.entity_id = ANY (q.part_numbers))))))
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'the maintenance role appends only HistoryArchiveRun, DeletionRequest and'
            ' HistoryArchiveView audit rows and the DELETED rows of the deletion request it'
            ' carries out, not %', NEW.entity_type;
    END IF;
    RETURN NEW;
END;
"""


def _function(name: str, body: str, *, replace: bool) -> str:
    verb = "CREATE OR REPLACE" if replace else "CREATE"
    return f"{verb} FUNCTION {name}() RETURNS trigger\nLANGUAGE plpgsql AS $${body}$$;\n"


def _rows_triggers(table: str) -> tuple[tuple[str, str], ...]:
    """(name, CREATE TRIGGER statement) of the three guards of one viewer table."""
    return (
        (
            f"trg_{table}_guard_insert",
            f"CREATE TRIGGER trg_{table}_guard_insert AFTER INSERT ON {table}"
            f" REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT"
            f" EXECUTE FUNCTION {_ROWS_FUNCTION}()",
        ),
        (
            f"trg_{table}_guard_delete",
            f"CREATE TRIGGER trg_{table}_guard_delete AFTER DELETE ON {table}"
            f" REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT"
            f" EXECUTE FUNCTION {_ROWS_FUNCTION}()",
        ),
        (
            f"trg_{table}_forbid_mutation",
            f"CREATE TRIGGER trg_{table}_forbid_mutation BEFORE UPDATE OR TRUNCATE ON {table}"
            f" FOR EACH STATEMENT EXECUTE FUNCTION {_ROWS_FUNCTION}()",
        ),
    )


#: (name, CREATE TRIGGER statement, table) of every trigger this revision creates.
_TRIGGERS = (
    (
        "trg_history_archive_views_guard_mutation",
        f"CREATE TRIGGER trg_history_archive_views_guard_mutation"
        f" BEFORE INSERT OR UPDATE OR DELETE ON {_VIEWS}"
        f" FOR EACH ROW EXECUTE FUNCTION {_VIEWS_FUNCTION}()",
        _VIEWS,
    ),
    (
        "trg_history_archive_views_forbid_truncate",
        f"CREATE TRIGGER trg_history_archive_views_forbid_truncate BEFORE TRUNCATE ON {_VIEWS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {_VIEWS_FUNCTION}()",
        _VIEWS,
    ),
    *(
        (name, statement, table)
        for table in _VIEWER_TABLES
        for name, statement in _rows_triggers(table)
    ),
)


def _timestamp(name: str, *, nullable: bool = True) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _create_views() -> None:
    op.create_table(
        _VIEWS,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        _timestamp("started_at"),
        _timestamp("opened_at"),
        sa.Column("loaded_movements", sa.BigInteger(), nullable=True),
        sa.Column("loaded_flows", sa.Integer(), nullable=True),
        sa.Column("loaded_lineage_edges", sa.Integer(), nullable=True),
        sa.Column("loaded_assigned_routes", sa.Integer(), nullable=True),
        sa.Column("loaded_names", sa.Integer(), nullable=True),
        _timestamp("closing_started_at"),
        sa.Column("closed_by_user_id", sa.Integer(), nullable=True),
        _timestamp("finished_at"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_history_archive_views"),
        sa.ForeignKeyConstraint(
            ["run_id"], [f"{_RUNS}.id"], name="fk_history_archive_views_run_id_history_archive_runs"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_history_archive_views_requested_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_user_id"],
            ["users.id"],
            name="fk_history_archive_views_closed_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name="fk_history_archive_views_cancelled_by_user_id_users",
        ),
        *(
            sa.CheckConstraint(sql, name=op.f(f"ck_history_archive_views_{suffix}"))
            for suffix, sql in _VIEW_CHECKS
        ),
    )
    op.create_index(
        _ACTIVE_INDEX,
        _VIEWS,
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(_in("state", _ACTIVE_STATES)),
    )


def _view_id() -> sa.Column[Any]:
    return sa.Column("view_id", sa.Integer(), nullable=False)


def _create_viewer_tables() -> None:
    op.create_table(
        "archive_view_movements",
        _view_id(),
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("quantity_flow_id", sa.Integer(), nullable=False),
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("movement_type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("from_area_id", sa.Integer(), nullable=True),
        sa.Column("to_area_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("assigned_route_step_id", sa.Integer(), nullable=True),
        _timestamp("occurred_at", nullable=False),
        _timestamp("server_received_at", nullable=False),
        sa.Column("device_event_id", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("station_id", sa.Text(), nullable=True),
        sa.Column("source_machine_id", sa.Integer(), nullable=True),
        sa.Column("destination_machine_id", sa.Integer(), nullable=True),
        sa.Column("command_sequence", sa.Integer(), nullable=False),
        sa.Column("movement_reason", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reverses_movement_id", sa.BigInteger(), nullable=True),
        sa.Column("worker_id", sa.Integer(), nullable=True),
        sa.Column("scan_session_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("view_id", "id", name="pk_archive_view_movements"),
        sa.CheckConstraint(
            _CANONICAL_PART_NUMBER_SQL,
            name=op.f("ck_archive_view_movements_part_number_canonical"),
        ),
    )
    for name, columns, where in _MOVEMENT_INDEXES:
        op.create_index(
            name,
            "archive_view_movements",
            list(columns),
            postgresql_where=None if where is None else sa.text(where),
        )
    op.create_table(
        "archive_view_flows",
        _view_id(),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("component_id", sa.Integer(), nullable=False),
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("route_mode", sa.Text(), nullable=False),
        sa.Column("assigned_route_id", sa.Integer(), nullable=True),
        _timestamp("created_at", nullable=False),
        _timestamp("closed_at"),
        sa.Column("received_demand_id", sa.Integer(), nullable=True),
        sa.Column(
            "work_order_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'::integer[]"),
            nullable=False,
        ),
        sa.Column("flow", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("view_id", "flow_id", name="pk_archive_view_flows"),
        sa.CheckConstraint(
            _CANONICAL_PART_NUMBER_SQL, name=op.f("ck_archive_view_flows_part_number_canonical")
        ),
    )
    op.create_index(
        "ix_archive_view_flows_view_id_part_number",
        "archive_view_flows",
        ["view_id", "part_number"],
    )
    op.create_index(
        "ix_archive_view_flows_work_order_ids",
        "archive_view_flows",
        ["work_order_ids"],
        postgresql_using="gin",
    )
    op.create_table(
        "archive_view_lineage",
        _view_id(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("parent_flow_id", sa.Integer(), nullable=False),
        sa.Column("child_flow_id", sa.Integer(), nullable=False),
        sa.Column("device_event_id", sa.Text(), nullable=False),
        _timestamp("created_at", nullable=False),
        sa.Column("void", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("view_id", "id", name="pk_archive_view_lineage"),
    )
    for column in ("parent_flow_id", "child_flow_id", "device_event_id"):
        op.create_index(
            f"ix_archive_view_lineage_view_id_{column}",
            "archive_view_lineage",
            ["view_id", column],
        )
    op.create_table(
        "archive_view_routes",
        _view_id(),
        sa.Column("assigned_route_id", sa.Integer(), nullable=False),
        sa.Column("source_route_template_id", sa.Integer(), nullable=True),
        _timestamp("created_at", nullable=False),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("view_id", "assigned_route_id", name="pk_archive_view_routes"),
    )
    op.create_table(
        "archive_view_names",
        _view_id(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("view_id", "kind", "key", name="pk_archive_view_names"),
        sa.CheckConstraint(_in("kind", _NAME_KINDS), name=op.f("ck_archive_view_names_kind")),
        sa.CheckConstraint(
            _CANONICAL_NAME_KEY_SQL, name=op.f("ck_archive_view_names_part_number_canonical")
        ),
    )
    # No statistics on view_id: they would describe the views loaded when ANALYZE
    # last ran, and a view loaded since would read as empty to the planner (every
    # index then looks equally cheap). Without them each view counts as a fixed
    # share of the table, and the other columns choose the index.
    for table in _VIEWER_TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN view_id SET STATISTICS 0")


#: (name, columns, partial WHERE or None) of the indexes of archive_view_movements:
#: the history in time order, by Part Number, by flow, and the reversals.
_MOVEMENT_INDEXES: tuple[tuple[str, tuple[str, ...], str | None], ...] = (
    (
        "ix_archive_view_movements_view_id_occurred_at_id",
        ("view_id", "occurred_at", "id"),
        None,
    ),
    (
        "ix_archive_view_movements_view_id_part_number_occurred_at_id",
        ("view_id", "part_number", "occurred_at", "id"),
        None,
    ),
    (
        "ix_archive_view_movements_view_id_quantity_flow_id_id",
        ("view_id", "quantity_flow_id", "id"),
        None,
    ),
    (
        "ix_archive_view_movements_view_id_reverses_movement_id",
        ("view_id", "reverses_movement_id"),
        "reverses_movement_id IS NOT NULL",
    ),
)


def _create_guards() -> None:
    op.execute(_function(_VIEWS_FUNCTION, _VIEWS_BODY, replace=False))
    op.execute(_function(_ROWS_FUNCTION, _ROWS_BODY, replace=False))
    op.execute(_function(_AUDIT_FUNCTION, _AUDIT_BODY, replace=True))
    for _name, statement, _table in _TRIGGERS:
        op.execute(statement)


def upgrade() -> None:
    for name, column_type, default in _POLICY_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(name, column_type, nullable=False, server_default=sa.text(default)),
        )
    for column, _default, minimum, maximum in _POLICY_RANGES:
        op.create_check_constraint(
            op.f(f"ck_application_policy_{column}_range"),
            _POLICY,
            f"{column} BETWEEN {minimum} AND {maximum}",
        )
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ENTITY_TYPES)
    _create_views()
    _create_viewer_tables()
    _create_guards()


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. A viewer row needs a LOADING view (its guard), so no
    # view means no viewer row.
    changed = " OR ".join(
        f"{column} <> {default}" for column, default, _minimum, _maximum in _POLICY_RANGES
    )
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_VIEWS})"
        f"  OR EXISTS (SELECT 1 FROM {_AUDIT} WHERE entity_type = 'HistoryArchiveView')"
        f"  OR EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        " THEN RAISE EXCEPTION 'Archive viewing data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for name, _statement, table in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER {name} ON {table}")
    op.execute(_function(_AUDIT_FUNCTION, _PREVIOUS_AUDIT_BODY, replace=True))
    for function in (_ROWS_FUNCTION, _VIEWS_FUNCTION):
        op.execute(f"DROP FUNCTION {function}()")
    for table in reversed(_VIEWER_TABLES):
        op.drop_table(table)
    op.drop_index(_ACTIVE_INDEX, table_name=_VIEWS)
    op.drop_table(_VIEWS)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)
    for column, *_ in reversed(_POLICY_RANGES):
        op.drop_constraint(op.f(f"ck_application_policy_{column}_range"), _POLICY, type_="check")
    for name, *_ in reversed(_POLICY_COLUMNS):
        op.drop_column(_POLICY, name)


# The function body the downgrade restores (the schema test asserts that it is
# exactly 0042's audit scope).
_PREVIOUS_AUDIT_BODY = """
DECLARE
    request text := current_setting('partflow.deletion_request_id', true);
BEGIN
    -- The maintenance role: it may write archive records and does not own this table.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        -- The deletion request it works on: the request's own rows, and while it is
        -- RUNNING one DELETED row per Work Order it stored and Part Number of its scope.
        IF request ~ '^[1-9][0-9]{0,8}$' AND (
            (NEW.entity_type = 'DeletionRequest' AND NEW.entity_id = request)
            OR (NEW.event_type = 'DELETED' AND EXISTS (
                SELECT 1 FROM public.deletion_requests q
                WHERE q.id = request::integer AND q.state = 'RUNNING' AND (
                    (NEW.entity_type = 'WorkOrder' AND EXISTS (
                        SELECT 1 FROM public.deletion_request_rows r
                        WHERE r.request_id = q.id AND r.table_name = 'work_orders'
                            AND r.row_key = NEW.entity_id))
                    OR (NEW.entity_type = 'PartNumber' AND NEW.entity_id = ANY (q.part_numbers))))))
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'the maintenance role appends only HistoryArchiveRun and DeletionRequest'
            ' audit rows and the DELETED rows of the deletion request it carries out, not %',
            NEW.entity_type;
    END IF;
    RETURN NEW;
END;
"""
