"""Phase 16: Movement-history archival runs without deletion (design B, step 1).

The owner approved the Movement-history archival and purge design on
2026-11-17 (``_claude_outputs/phase16/ARCHIVAL_DESIGN.md``; IMPLEMENTATION_ROADMAP
Phase 16). Its first build step records archival runs and exports, verifies
and copies their archives off the host; nothing is deleted.

- ``history_archive_runs`` — one row per run: its state, cutoff, reason,
  requester and request time, the counts its preview found, and the host
  job's progress (archive name, exported counts, manifest SHA-256,
  verification and skips, the off-host copy or its confirmation in
  Administration, the result ``EXPORT_ONLY`` or the failure reason). At most
  one run is open (``uq_history_archive_runs_open``). Grant class
  ``GUARDED_UPDATE``; the row trigger ``trg_history_archive_runs_guard_mutation``
  lets the application role only insert an ``APPROVED`` run, cancel an
  ``APPROVED`` or ``WAITING_FOR_COPY`` run and confirm the copy of a
  ``WAITING_FOR_COPY`` run; the maintenance job (a role that may write
  archive records, while ``partflow.archive_run_id`` names the run) makes the
  other transitions, never confirming a copy or cancelling, and the
  maintenance role changes a run in no other way; the request never
  changes, a finished run never changes, and no run is deleted. Every
  insert is refused (SQLSTATE 23514, constraint
  ``trg_history_archive_runs_guard_mutation``) when its cutoff is newer than
  the shortest allowed retention period before now (read from the policy
  row; compared in UTC with four days' allowance for the site's calendar,
  which only the application knows exactly). A ``COMPLETED`` run was copied
  off the host or its copy confirmed (``ck_history_archive_runs_completed_copy``).
- ``history_archived_flows`` — the carry-forward record of each flow of an
  archived component, written by the job at export (foreign key to
  ``quantity_flows (id, part_number)``). Grant class ``READ_ONLY``; the row
  trigger ``trg_history_archived_flows_guard_mutation`` admits an insert only
  while the run is ``RUNNING`` and ``partflow.archive_run_id`` names it, and no
  change but setting an empty ``skipped_at`` (run ``RUNNING``) or an empty
  ``purged_at`` (run ``PURGING``, a later step); no row is deleted.
- ``trg_audit_events_maintenance_scope`` — the maintenance role (a role that
  may write archive records and does not own ``audit_events``) appends only
  ``HistoryArchiveRun`` audit rows. ``ck_audit_events_entity_type`` gains
  ``HistoryArchiveRun``.
- The permission key ``RUN_HISTORY_ARCHIVAL_AND_PURGE`` (design D7):
  ``ck_role_permissions_permission_known`` admits it, and it is granted to the
  role named Administrator (the exactly cased name first, then any letter
  case, the lowest id) and to every role that holds every other key
  (PROJECT_PROFILE §20, the Super User role). Each role that receives it
  moves ``updated_at`` and gets one ``Role`` ``UPDATED`` audit row without an
  actor, with the metadata ``{"source": "migration", "revision": ...}``, as
  ``0033`` completes a role.
- ``application_policy`` gains ``shortest_retention_months`` (default 12,
  1-1200), ``archive_max_wait_days`` (default 7, 1-365: the days a run may
  wait for its copy confirmation before the host job's status reports it),
  ``archive_start_minute`` (default 20, 0-59) and ``archive_preview_seconds``
  (default 90, 10-600: inside the 100 seconds after which Cloudflare ends a
  request), each with its range CHECK, and
  ``ck_application_policy_retention_period_range`` becomes "NULL or from the
  shortest allowed period to 1200".
- The 24-month default (design §3.1): while no ``ApplicationPolicy`` audit
  row of the ``data-retention`` section shows that the retention period was
  ever saved, it is set to 24 months, ``updated_at`` moves, and one such
  audit row (no actor, the migration metadata) records the change.

The maintenance role's new privileges (SELECT on what the export reads, the
guarded UPDATE on the runs, INSERT and the guarded UPDATE on the records,
INSERT on ``audit_events``) are grants, derived by ``apply-grants`` in every
``migrate`` from ``app.infrastructure.database_privileges``. It may not
insert a run: only a person requests one, in Administration. No delete
privilege and no change to the Movement deletion guard: that is the purge
step.

The downgrade REFUSES while a run, a record or a ``HistoryArchiveRun``
audit row exists or a new policy value differs from its default, so no
archival history or configuration is silently dropped; otherwise it revokes
the permission key (one ``Role`` ``UPDATED`` audit row per role it changes)
and keeps the retention period, which the previous revision can hold.
``alembic/env.py`` runs in one transaction, so a refused downgrade leaves the
database at this revision. Run it only against disposable development and
test databases.

Revision ID: 0040_phase16_history_archive
Revises: 0039_security_settings
Create Date: 2026-11-17

"""

import json
from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0040_phase16_history_archive"
down_revision: str | None = "0039_security_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py, enums or
# policies counterpart.
_RUNS = "history_archive_runs"
_FLOWS = "history_archived_flows"
_POLICY = "application_policy"
_AUDIT = "audit_events"
_SECTION = "data-retention"
_KEY = "RUN_HISTORY_ARCHIVAL_AND_PURGE"
_ADMINISTRATOR = "Administrator"
_AUDIT_METADATA = {"source": "migration", "revision": revision}
_DEFAULT_RETENTION_MONTHS = 24

# Every key the CHECK admitted before this revision (0028), in its order.
_PREVIOUS_PERMISSIONS = (
    "MANAGE_DEPARTMENTS",
    "MANAGE_AREAS",
    "MANAGE_OPERATIONS",
    "MANAGE_WORKERS",
    "MANAGE_USERS_AND_ROLES",
    "MANAGE_SCAN_STATIONS",
    "MANAGE_BARCODE_CONFIGURATION",
    "MANAGE_SCAN_BEHAVIOR",
    "MANAGE_WORKER_SESSION_POLICIES",
    "MANAGE_CORRECTION_PERMISSIONS",
    "CONFIGURE_SYSTEM_SETTINGS",
    "MANAGE_MACHINES",
    "MANAGE_ROUTE_TEMPLATES",
    "MANAGE_PART_NUMBER_MASTER",
    "VIEW_PRODUCTION_DATA",
    "MANAGE_WORK_ORDERS",
    "EDIT_WORK_ORDER_DEMAND",
    "SET_DEMAND_PRIORITY",
    "REORDER_HOT_ITEMS",
    "ASSIGN_ROUTES",
    "RESOLVE_EXCEPTIONAL_SITUATIONS",
    "EXPORT_REPORTS",
    "SCAN_PN_BARCODES",
    "SCAN_MACHINE_BARCODES",
    "SCAN_WORKER_BARCODES",
    "RECEIVE_QUANTITY",
    "ASSIGN_QUANTITY_TO_MACHINE",
    "CONFIRM_QUANTITY",
    "COMPLETE_INTO_STOCKROOM",
    "CONFIRM_SUGGESTED_ALLOCATION",
    "ADJUST_SUGGESTED_ALLOCATION",
    "UNDO_RECENT_SCANS",
    "PERFORM_QUANTITY_CORRECTIONS",
    "EDIT_WORK_ORDER_ALLOCATION",
    "PERFORM_HISTORICAL_CORRECTIONS",
)
_PERMISSIONS = (*_PREVIOUS_PERMISSIONS, _KEY)
_PERMISSION_CHECK = "ck_role_permissions_permission_known"


def _permission_sql(keys: Iterable[str]) -> str:
    return "permission IN (" + ", ".join(f"'{key}'" for key in keys) + ")"


_PREVIOUS_PERMISSION_SQL = _permission_sql(_PREVIOUS_PERMISSIONS)
_PERMISSION_SQL = _permission_sql(_PERMISSIONS)

_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
# Exactly what 0032 left (no later revision touched it; the downgrade restores it).
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute')"
)
_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute', 'HistoryArchiveRun')"
)

_RETENTION_CHECK = "ck_application_policy_retention_period_range"
# Exactly what 0027 created (the downgrade restores it).
_PREVIOUS_RETENTION_SQL = (
    "retention_period_months IS NULL OR retention_period_months BETWEEN 12 AND 1200"
)
_RETENTION_SQL = (
    "retention_period_months IS NULL"
    " OR retention_period_months BETWEEN shortest_retention_months AND 1200"
)
# (column, server default, minimum, maximum); the CHECK of each is
# ck_application_policy_<column>_range: "<column> BETWEEN minimum AND maximum".
_POLICY_COLUMNS = (
    ("shortest_retention_months", 12, 1, 1200),
    ("archive_max_wait_days", 7, 1, 365),
    ("archive_start_minute", 20, 0, 59),
    ("archive_preview_seconds", 90, 10, 600),
)
# The audited facet of the data-retention section (policies.history_archive_snapshot).
_SECTION_COLUMNS = (
    "retention_period_months",
    "shortest_retention_months",
    "archive_max_wait_days",
    "archive_start_minute",
    "archive_preview_seconds",
)

_STATES = (
    "APPROVED",
    "RUNNING",
    "WAITING_FOR_COPY",
    "COMPLETED",
    "NOTHING_ELIGIBLE",
    "CANCELLED",
    "FAILED",
)
_RESULTS = ("EXPORT_ONLY",)
_FINAL_STATES = ("COMPLETED", "NOTHING_ELIGIBLE", "CANCELLED", "FAILED")
_OPEN_STATES = ("APPROVED", "RUNNING", "WAITING_FOR_COPY")
_CLOSED_FLOW_STATUSES = ("SPLIT", "MERGED", "SCRAPPED", "REVERSED", "STOCKED")


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_CANONICAL_PART_NUMBER_SQL = (
    """part_number = upper(part_number COLLATE "C")"""
    """ AND part_number COLLATE "C" !~ '[[:space:]]' AND part_number <> ''"""
)
_RUN_STATE_SQL = _in("state", _STATES)
_RUN_REASON_SQL = "char_length(reason) BETWEEN 1 AND 500"
_RUN_PREVIEW_SQL = (
    "preview_components >= 0 AND preview_flows >= preview_components"
    " AND preview_movements >= preview_flows AND preview_archive_bytes >= 0"
    " AND (preview_first_occurred_at IS NULL) = (preview_last_occurred_at IS NULL)"
    " AND (preview_first_occurred_at IS NULL OR"
    " preview_first_occurred_at <= preview_last_occurred_at)"
)
_RUN_EXPORT_SQL = (
    "(exported_at IS NULL) = (archive_name IS NULL)"
    " AND (exported_at IS NULL) = (manifest_sha256 IS NULL)"
    " AND (exported_at IS NULL) = (archive_bytes IS NULL)"
    " AND (exported_at IS NULL) = (exported_components IS NULL)"
    " AND (exported_at IS NULL) = (exported_flows IS NULL)"
    " AND (exported_at IS NULL) = (exported_movements IS NULL)"
    " AND (exported_at IS NULL) = (exported_lineage_edges IS NULL)"
    " AND (exported_at IS NULL) = (exported_assigned_routes IS NULL)"
)
_RUN_NAME_SQL = "archive_name IS NULL OR archive_name ~ '^run-[0-9]+-[0-9]{8}T[0-9]{6}Z$'"
_RUN_MANIFEST_SQL = "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'"
_RUN_VERIFY_SQL = (
    "(verified_at IS NULL OR exported_at IS NOT NULL)"
    " AND (verified_at IS NULL) = (skipped_components IS NULL)"
    " AND (verified_at IS NULL) = (skipped_flows IS NULL)"
    " AND (copied_at IS NULL OR verified_at IS NOT NULL)"
)
_RUN_PROGRESS_SQL = (
    "(state <> 'APPROVED' OR started_at IS NULL)"
    " AND (state NOT IN ('RUNNING', 'WAITING_FOR_COPY', 'COMPLETED', 'NOTHING_ELIGIBLE')"
    " OR started_at IS NOT NULL)"
    " AND (state NOT IN ('WAITING_FOR_COPY', 'COMPLETED') OR verified_at IS NOT NULL)"
)
_RUN_COPY_CONFIRMATION_SQL = "(copy_confirmed_at IS NULL) = (copy_confirmed_by_user_id IS NULL)"
_RUN_COMPLETED_COPY_SQL = (
    "state <> 'COMPLETED' OR copied_at IS NOT NULL OR copy_confirmed_at IS NOT NULL"
)
_RUN_RESULT_SQL = (
    "(result IS NULL OR " + _in("result", _RESULTS) + ")"
    " AND (state = 'COMPLETED') = (result IS NOT NULL)"
)
_RUN_FAILURE_SQL = (
    "(state = 'FAILED') = (failure_reason IS NOT NULL)"
    " AND (failure_reason IS NULL OR char_length(failure_reason) BETWEEN 1 AND 2000)"
)
_RUN_FINISH_SQL = (
    "(" + _in("state", _FINAL_STATES) + ") = (finished_at IS NOT NULL)"
    " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)"
)
_RUN_OPEN_WHERE_SQL = _in("state", _OPEN_STATES)
# (check name suffix, SQL), in the model's order.
_RUN_CHECKS = (
    ("state", _RUN_STATE_SQL),
    ("reason_length", _RUN_REASON_SQL),
    ("preview_shape", _RUN_PREVIEW_SQL),
    ("export_shape", _RUN_EXPORT_SQL),
    ("archive_name_shape", _RUN_NAME_SQL),
    ("manifest_sha256_shape", _RUN_MANIFEST_SQL),
    ("verify_shape", _RUN_VERIFY_SQL),
    ("progress_shape", _RUN_PROGRESS_SQL),
    ("copy_confirmation_shape", _RUN_COPY_CONFIRMATION_SQL),
    ("completed_copy", _RUN_COMPLETED_COPY_SQL),
    ("result_shape", _RUN_RESULT_SQL),
    ("failure_shape", _RUN_FAILURE_SQL),
    ("finish_shape", _RUN_FINISH_SQL),
)

_FLOW_STATUS_SQL = _in("flow_status", _CLOSED_FLOW_STATUSES)
_FLOW_MOVEMENTS_SQL = (
    "movement_count > 0 AND first_movement_id <= last_movement_id"
    " AND first_occurred_at <= last_occurred_at AND component_id BETWEEN 1 AND flow_id"
)
_FLOW_CONTENT_SQL = "content_sha256 ~ '^[0-9a-f]{64}$'"
_FLOW_RECEIVED_SQL = (
    "(received_quantity IS NULL) = (received_at IS NULL)"
    " AND (received_quantity IS NOT NULL OR received_demand_id IS NULL)"
    " AND (received_quantity IS NULL OR received_quantity > 0)"
)
_FLOW_ADJUSTED_SQL = "adjusted_quantity IS NULL OR adjusted_quantity > 0"
_FLOW_STOCKED_SQL = (
    "(stocked_quantity IS NULL) = (stocked_area_id IS NULL)"
    " AND (stocked_quantity IS NULL OR stocked_quantity > 0)"
)
_FLOW_SCRAPPED_SQL = (
    "(scrapped_quantity IS NULL) = (scrapped_area_id IS NULL)"
    " AND (scrapped_quantity IS NULL OR scrapped_quantity > 0)"
)
_FLOW_SKIP_OR_PURGE_SQL = "skipped_at IS NULL OR purged_at IS NULL"
_FLOW_CHECKS = (
    ("part_number_canonical", _CANONICAL_PART_NUMBER_SQL),
    ("flow_status", _FLOW_STATUS_SQL),
    ("movement_shape", _FLOW_MOVEMENTS_SQL),
    ("content_sha256_shape", _FLOW_CONTENT_SQL),
    ("received_shape", _FLOW_RECEIVED_SQL),
    ("adjusted_positive", _FLOW_ADJUSTED_SQL),
    ("stocked_shape", _FLOW_STOCKED_SQL),
    ("scrapped_shape", _FLOW_SCRAPPED_SQL),
    ("skipped_or_purged", _FLOW_SKIP_OR_PURGE_SQL),
)

# The guard functions and triggers (pinned by app.infrastructure.database_privileges:
# GUARD_TRIGGERS and the SHA-256 of each function's source).
_RUNS_FUNCTION = "partflow_history_archive_runs_guard_mutation"
_FLOWS_FUNCTION = "partflow_history_archived_flows_guard_mutation"
_AUDIT_FUNCTION = "partflow_audit_events_maintenance_scope"
# The constraint name the cutoff refusal reports (SQLSTATE 23514), which the
# application maps to a 409 like a constraint it names.
_CUTOFF_FLOOR = "trg_history_archive_runs_guard_mutation"
_CREATE_RUNS_FUNCTION = f"""
CREATE FUNCTION {_RUNS_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    shortest integer;
    newest_cutoff timestamptz;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archive_runs rows are never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT p.shortest_retention_months INTO shortest
        FROM public.application_policy p WHERE p.id = 1;
        -- Four days' allowance: the exact rule counts the months back from the site's
        -- date, which can be a day ahead of UTC at the end of a month.
        newest_cutoff := ((now() AT TIME ZONE 'UTC') - make_interval(months => shortest)
            + interval '4 days') AT TIME ZONE 'UTC';
        IF shortest IS NULL OR NEW.cutoff > newest_cutoff THEN
            RAISE EXCEPTION
                'archive run cutoff % is newer than the shortest allowed period (% months)',
                NEW.cutoff, shortest
                USING ERRCODE = 'check_violation', CONSTRAINT = '{_CUTOFF_FLOOR}';
        END IF;
        IF NEW.state <> 'APPROVED' OR NEW.started_at IS NOT NULL OR NEW.exported_at IS NOT NULL
            OR NEW.verified_at IS NOT NULL OR NEW.copied_at IS NOT NULL
            OR NEW.copy_confirmed_at IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
            RAISE EXCEPTION 'a history archive run is requested APPROVED, with no progress';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('COMPLETED', 'NOTHING_ELIGIBLE', 'CANCELLED', 'FAILED') THEN
        RAISE EXCEPTION 'history archive run % is finished (%) and never changes',
            OLD.id, OLD.state;
    END IF;
    IF (NEW.id, NEW.cutoff, NEW.reason, NEW.requested_by_user_id, NEW.requested_at,
        NEW.preview_components, NEW.preview_flows, NEW.preview_movements,
        NEW.preview_first_occurred_at, NEW.preview_last_occurred_at, NEW.preview_archive_bytes)
        IS DISTINCT FROM
        (OLD.id, OLD.cutoff, OLD.reason, OLD.requested_by_user_id, OLD.requested_at,
        OLD.preview_components, OLD.preview_flows, OLD.preview_movements,
        OLD.preview_first_occurred_at, OLD.preview_last_occurred_at, OLD.preview_archive_bytes)
    THEN
        RAISE EXCEPTION 'the request of history archive run % never changes', OLD.id;
    END IF;
    -- The maintenance job: a role that may write archive records, working on this run.
    -- Only a person confirms the off-host copy or cancels a run.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND current_setting('partflow.archive_run_id', true) = OLD.id::text THEN
        IF (NEW.copy_confirmed_at, NEW.copy_confirmed_by_user_id, NEW.cancelled_by_user_id)
            IS DISTINCT FROM
            (OLD.copy_confirmed_at, OLD.copy_confirmed_by_user_id, OLD.cancelled_by_user_id)
        THEN
            RAISE EXCEPTION
                'history archive run %: the maintenance job never confirms a copy or cancels',
                OLD.id;
        END IF;
        IF NEW.state = OLD.state OR (OLD.state, NEW.state) IN (
            ('APPROVED', 'RUNNING'), ('APPROVED', 'FAILED'),
            ('RUNNING', 'WAITING_FOR_COPY'), ('RUNNING', 'COMPLETED'),
            ('RUNNING', 'NOTHING_ELIGIBLE'), ('RUNNING', 'FAILED'),
            ('WAITING_FOR_COPY', 'COMPLETED'), ('WAITING_FOR_COPY', 'FAILED')) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'history archive run %: the maintenance job cannot change % to %',
            OLD.id, OLD.state, NEW.state;
    END IF;
    -- The maintenance role (it may write archive records and does not own this table)
    -- changes a run only as its job.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RAISE EXCEPTION 'history archive run %: the maintenance role changes a run only as its job',
            OLD.id;
    END IF;
    -- The application: cancel an approved or waiting run.
    IF NEW.state = 'CANCELLED' AND OLD.state IN ('APPROVED', 'WAITING_FOR_COPY')
        AND to_jsonb(NEW) - ARRAY['state', 'finished_at', 'cancelled_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'finished_at', 'cancelled_by_user_id'] THEN
        RETURN NEW;
    END IF;
    -- The application: confirm the off-host copy of a waiting run, once.
    IF NEW.state = 'WAITING_FOR_COPY' AND OLD.state = 'WAITING_FOR_COPY'
        AND OLD.copy_confirmed_at IS NULL
        AND to_jsonb(NEW) - ARRAY['copy_confirmed_at', 'copy_confirmed_by_user_id']
            = to_jsonb(OLD) - ARRAY['copy_confirmed_at', 'copy_confirmed_by_user_id'] THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'history archive run %: only the maintenance job makes this change', OLD.id;
END;
$$;
"""
_CREATE_FLOWS_FUNCTION = f"""
CREATE FUNCTION {_FLOWS_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    run_state text;
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'history_archived_flows rows are never deleted';
    END IF;
    IF current_setting('partflow.archive_run_id', true) IS DISTINCT FROM NEW.run_id::text THEN
        RAISE EXCEPTION
            'archive records of run % are written only by the maintenance job of that run',
            NEW.run_id;
    END IF;
    SELECT r.state INTO run_state FROM public.history_archive_runs r WHERE r.id = NEW.run_id;
    IF TG_OP = 'INSERT' THEN
        IF run_state IS DISTINCT FROM 'RUNNING' OR NEW.skipped_at IS NOT NULL
            OR NEW.purged_at IS NOT NULL THEN
            RAISE EXCEPTION
                'an archive record is written only while its run is RUNNING, unskipped, unpurged';
        END IF;
        RETURN NEW;
    END IF;
    IF to_jsonb(NEW) - ARRAY['skipped_at', 'purged_at']
        <> to_jsonb(OLD) - ARRAY['skipped_at', 'purged_at'] THEN
        RAISE EXCEPTION 'an archive record never changes except when it is skipped or purged';
    END IF;
    IF NEW.skipped_at IS DISTINCT FROM OLD.skipped_at
        AND (OLD.skipped_at IS NOT NULL OR run_state IS DISTINCT FROM 'RUNNING') THEN
        RAISE EXCEPTION 'an archive record is marked skipped once, while its run is RUNNING';
    END IF;
    IF NEW.purged_at IS DISTINCT FROM OLD.purged_at
        AND (OLD.purged_at IS NOT NULL OR run_state IS DISTINCT FROM 'PURGING') THEN
        RAISE EXCEPTION 'an archive record is marked purged once, while its run is PURGING';
    END IF;
    RETURN NEW;
END;
$$;
"""
_CREATE_AUDIT_FUNCTION = f"""
CREATE FUNCTION {_AUDIT_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- The maintenance role: it may write archive records and does not own this table.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RAISE EXCEPTION 'the maintenance role appends only HistoryArchiveRun audit rows, not %',
            NEW.entity_type;
    END IF;
    RETURN NEW;
END;
$$;
"""
_TRIGGERS = (
    (
        "trg_history_archive_runs_guard_mutation",
        f"CREATE TRIGGER trg_history_archive_runs_guard_mutation"
        f" BEFORE INSERT OR UPDATE OR DELETE ON {_RUNS}"
        f" FOR EACH ROW EXECUTE FUNCTION {_RUNS_FUNCTION}()",
        _RUNS,
    ),
    (
        "trg_history_archive_runs_forbid_truncate",
        f"CREATE TRIGGER trg_history_archive_runs_forbid_truncate BEFORE TRUNCATE ON {_RUNS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {_RUNS_FUNCTION}()",
        _RUNS,
    ),
    (
        "trg_history_archived_flows_guard_mutation",
        f"CREATE TRIGGER trg_history_archived_flows_guard_mutation"
        f" BEFORE INSERT OR UPDATE OR DELETE ON {_FLOWS}"
        f" FOR EACH ROW EXECUTE FUNCTION {_FLOWS_FUNCTION}()",
        _FLOWS,
    ),
    (
        "trg_history_archived_flows_forbid_truncate",
        f"CREATE TRIGGER trg_history_archived_flows_forbid_truncate BEFORE TRUNCATE ON {_FLOWS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {_FLOWS_FUNCTION}()",
        _FLOWS,
    ),
    (
        "trg_audit_events_maintenance_scope",
        f"CREATE TRIGGER trg_audit_events_maintenance_scope BEFORE INSERT ON {_AUDIT}"
        f" FOR EACH ROW WHEN (NEW.entity_type <> 'HistoryArchiveRun')"
        f" EXECUTE FUNCTION {_AUDIT_FUNCTION}()",
        _AUDIT,
    ),
)


def _range_check(column: str) -> str:
    return f"ck_application_policy_{column}_range"


def _timestamp(name: str, *, nullable: bool = True) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _create_tables() -> None:
    op.create_table(
        _RUNS,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        _timestamp("cutoff", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("preview_components", sa.Integer(), nullable=False),
        sa.Column("preview_flows", sa.Integer(), nullable=False),
        sa.Column("preview_movements", sa.BigInteger(), nullable=False),
        _timestamp("preview_first_occurred_at"),
        _timestamp("preview_last_occurred_at"),
        sa.Column("preview_archive_bytes", sa.BigInteger(), nullable=False),
        _timestamp("started_at"),
        sa.Column("archive_name", sa.Text(), nullable=True),
        _timestamp("exported_at"),
        sa.Column("exported_components", sa.Integer(), nullable=True),
        sa.Column("exported_flows", sa.Integer(), nullable=True),
        sa.Column("exported_movements", sa.BigInteger(), nullable=True),
        sa.Column("exported_lineage_edges", sa.Integer(), nullable=True),
        sa.Column("exported_assigned_routes", sa.Integer(), nullable=True),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=True),
        sa.Column("manifest_sha256", sa.Text(), nullable=True),
        _timestamp("verified_at"),
        sa.Column("skipped_components", sa.Integer(), nullable=True),
        sa.Column("skipped_flows", sa.Integer(), nullable=True),
        _timestamp("copied_at"),
        _timestamp("copy_confirmed_at"),
        sa.Column("copy_confirmed_by_user_id", sa.Integer(), nullable=True),
        _timestamp("finished_at"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_history_archive_runs"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_history_archive_runs_requested_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["copy_confirmed_by_user_id"],
            ["users.id"],
            name="fk_history_archive_runs_copy_confirmed_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name="fk_history_archive_runs_cancelled_by_user_id_users",
        ),
        *(
            sa.CheckConstraint(sql, name=op.f(f"ck_history_archive_runs_{suffix}"))
            for suffix, sql in _RUN_CHECKS
        ),
    )
    op.create_index(
        "uq_history_archive_runs_open",
        _RUNS,
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text(_RUN_OPEN_WHERE_SQL),
    )
    op.create_table(
        _FLOWS,
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("flow_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("component_id", sa.Integer(), nullable=False),
        sa.Column("flow_status", sa.Text(), nullable=False),
        sa.Column("movement_count", sa.Integer(), nullable=False),
        sa.Column("first_movement_id", sa.BigInteger(), nullable=False),
        sa.Column("last_movement_id", sa.BigInteger(), nullable=False),
        _timestamp("first_occurred_at", nullable=False),
        _timestamp("last_occurred_at", nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("received_quantity", sa.Integer(), nullable=True),
        sa.Column("received_demand_id", sa.Integer(), nullable=True),
        _timestamp("received_at"),
        sa.Column("adjusted_quantity", sa.Integer(), nullable=True),
        sa.Column("stocked_quantity", sa.Integer(), nullable=True),
        sa.Column("stocked_area_id", sa.Integer(), nullable=True),
        sa.Column("scrapped_quantity", sa.Integer(), nullable=True),
        sa.Column("scrapped_area_id", sa.Integer(), nullable=True),
        sa.Column(
            "void_command_ids",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        _timestamp("skipped_at"),
        _timestamp("purged_at"),
        sa.PrimaryKeyConstraint("run_id", "flow_id", name="pk_history_archived_flows"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{_RUNS}.id"],
            name="fk_history_archived_flows_run_id_history_archive_runs",
        ),
        sa.ForeignKeyConstraint(
            ["flow_id", "part_number"],
            ["quantity_flows.id", "quantity_flows.part_number"],
            name="fk_history_archived_flows_flow_id_part_number_quantity_flows",
        ),
        sa.ForeignKeyConstraint(
            ["stocked_area_id"],
            ["areas.id"],
            name="fk_history_archived_flows_stocked_area_id_areas",
        ),
        sa.ForeignKeyConstraint(
            ["scrapped_area_id"],
            ["areas.id"],
            name="fk_history_archived_flows_scrapped_area_id_areas",
        ),
        *(
            sa.CheckConstraint(sql, name=op.f(f"ck_history_archived_flows_{suffix}"))
            for suffix, sql in _FLOW_CHECKS
        ),
    )
    op.create_index("ix_history_archived_flows_flow_id", _FLOWS, ["flow_id"])
    op.create_index(
        "uq_history_archived_flows_flow_id_purged",
        _FLOWS,
        ["flow_id"],
        unique=True,
        postgresql_where=sa.text("purged_at IS NOT NULL"),
    )


def _create_guards() -> None:
    op.execute(_CREATE_RUNS_FUNCTION)
    op.execute(_CREATE_FLOWS_FUNCTION)
    op.execute(_CREATE_AUDIT_FUNCTION)
    for _name, statement, _table in _TRIGGERS:
        op.execute(statement)


def _section_snapshot(row: sa.Row[tuple[object, ...]]) -> dict[str, object]:
    """The audited data-retention facet (``policies.history_archive_snapshot``)."""
    return dict(zip(_SECTION_COLUMNS, row, strict=True))


def _default_retention_period(connection: sa.Connection) -> None:
    """24 months while no audit row shows that the period was ever saved."""
    saved = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM audit_events"
            " WHERE entity_type = 'ApplicationPolicy' AND entity_id = :section)"
        ),
        {"section": _SECTION},
    ).scalar_one()
    if saved:
        return
    columns = ", ".join(_SECTION_COLUMNS)
    before = connection.execute(
        sa.text(f"SELECT {columns} FROM {_POLICY} WHERE id = 1")
    ).one_or_none()
    if before is None or before.retention_period_months == _DEFAULT_RETENTION_MONTHS:
        return
    after = connection.execute(
        sa.text(
            f"UPDATE {_POLICY} SET retention_period_months = :months, updated_at = now()"
            f" WHERE id = 1 RETURNING {columns}"
        ),
        {"months": _DEFAULT_RETENTION_MONTHS},
    ).one()
    _audit(
        connection,
        entity_type="ApplicationPolicy",
        entity_id=_SECTION,
        before=_section_snapshot(before),
        after=_section_snapshot(after),
    )


def _audit(
    connection: sa.Connection,
    *,
    entity_type: str,
    entity_id: str,
    before: dict[str, object],
    after: dict[str, object],
    metadata: dict[str, object] | None = None,
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data, metadata) VALUES ('UPDATED', :entity_type, :entity_id,"
            " now(), CAST(:before AS jsonb), CAST(:after AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": json.dumps(before),
            "after": json.dumps(after),
            "metadata": json.dumps(metadata or _AUDIT_METADATA),
        },
    )


def _roles(connection: sa.Connection) -> list[tuple[int, str, set[str]]]:
    rows = connection.execute(
        sa.text(
            "SELECT r.id, r.name, coalesce(array_agg(p.permission ORDER BY p.permission)"
            " FILTER (WHERE p.permission IS NOT NULL), '{}') AS keys"
            " FROM roles r LEFT JOIN role_permissions p ON p.role_id = r.id"
            " GROUP BY r.id, r.name ORDER BY r.id"
        )
    )
    return [(int(row.id), str(row.name), set(row.keys)) for row in rows]


def _role_snapshot(name: str, permissions: Iterable[str]) -> dict[str, object]:
    """The audited role facet (``app.application.roles.role_snapshot``)."""
    return {"name": name, "permissions": sorted(permissions)}


def _administrator_id(roles: list[tuple[int, str, set[str]]]) -> int | None:
    """The role named Administrator: the exactly cased name first, then the lowest id."""
    exact = [role_id for role_id, name, _ in roles if name == _ADMINISTRATOR]
    if exact:
        return exact[0]
    folded = [role_id for role_id, name, _ in roles if name.lower() == _ADMINISTRATOR.lower()]
    return folded[0] if folded else None


def _change_role(
    connection: sa.Connection,
    role_id: int,
    name: str,
    held: set[str],
    after: set[str],
    metadata: dict[str, object],
) -> None:
    if after - held:
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission)"
                " SELECT :role_id, unnest(CAST(:keys AS text[]))"
            ),
            {"role_id": role_id, "keys": sorted(after - held)},
        )
    if held - after:
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE role_id = :role_id"
                " AND permission = ANY(CAST(:keys AS text[]))"
            ),
            {"role_id": role_id, "keys": sorted(held - after)},
        )
    connection.execute(
        sa.text("UPDATE roles SET updated_at = now() WHERE id = :id"), {"id": role_id}
    )
    _audit(
        connection,
        entity_type="Role",
        entity_id=str(role_id),
        before=_role_snapshot(name, held),
        after=_role_snapshot(name, after),
        metadata=metadata,
    )


def _grant_the_key(connection: sa.Connection) -> None:
    roles = _roles(connection)
    administrator = _administrator_id(roles)
    every_other_key = set(_PREVIOUS_PERMISSIONS)
    for role_id, name, held in roles:
        if _KEY in held:
            continue
        if role_id == administrator or every_other_key <= held:
            _change_role(connection, role_id, name, held, held | {_KEY}, _AUDIT_METADATA)


def upgrade() -> None:
    connection = op.get_bind()
    op.drop_constraint(op.f(_PERMISSION_CHECK), "role_permissions", type_="check")
    op.create_check_constraint(op.f(_PERMISSION_CHECK), "role_permissions", _PERMISSION_SQL)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ENTITY_TYPES)
    for column, default, minimum, maximum in _POLICY_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(str(default))),
        )
        op.create_check_constraint(
            op.f(_range_check(column)), _POLICY, f"{column} BETWEEN {minimum} AND {maximum}"
        )
    op.drop_constraint(op.f(_RETENTION_CHECK), _POLICY, type_="check")
    op.create_check_constraint(op.f(_RETENTION_CHECK), _POLICY, _RETENTION_SQL)
    _create_tables()
    _create_guards()
    _grant_the_key(connection)
    _default_retention_period(connection)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    changed = " OR ".join(f"{column} <> {default}" for column, default, *_ in _POLICY_COLUMNS)
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_RUNS})"
        f"  OR EXISTS (SELECT 1 FROM {_FLOWS})"
        f"  OR EXISTS (SELECT 1 FROM {_AUDIT} WHERE entity_type = 'HistoryArchiveRun')"
        f"  OR EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        " THEN RAISE EXCEPTION 'History archival data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    connection = op.get_bind()
    metadata = {**_AUDIT_METADATA, "downgrade": True}
    for role_id, name, held in _roles(connection):
        if _KEY in held:
            _change_role(connection, role_id, name, held, held - {_KEY}, metadata)
    for name, _statement, table in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER {name} ON {table}")
    op.execute(f"DROP FUNCTION {_AUDIT_FUNCTION}()")
    op.execute(f"DROP FUNCTION {_FLOWS_FUNCTION}()")
    op.execute(f"DROP FUNCTION {_RUNS_FUNCTION}()")
    op.drop_index("uq_history_archived_flows_flow_id_purged", table_name=_FLOWS)
    op.drop_index("ix_history_archived_flows_flow_id", table_name=_FLOWS)
    op.drop_table(_FLOWS)
    op.drop_index("uq_history_archive_runs_open", table_name=_RUNS)
    op.drop_table(_RUNS)
    op.drop_constraint(op.f(_RETENTION_CHECK), _POLICY, type_="check")
    op.create_check_constraint(op.f(_RETENTION_CHECK), _POLICY, _PREVIOUS_RETENTION_SQL)
    for column, *_ in reversed(_POLICY_COLUMNS):
        op.drop_constraint(op.f(_range_check(column)), _POLICY, type_="check")
        op.drop_column(_POLICY, column)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)
    op.drop_constraint(op.f(_PERMISSION_CHECK), "role_permissions", type_="check")
    op.create_check_constraint(
        op.f(_PERMISSION_CHECK), "role_permissions", _PREVIOUS_PERMISSION_SQL
    )
