"""Phase 16: permanent deletion requests (design B, step 3; decision D17).

The third build step of the approved Movement-history archival design
(``_claude_outputs/phase16/ARCHIVAL_DESIGN.md`` §6.3, §8 and §13 step 3;
IMPLEMENTATION_ROADMAP Phase 16). A holder of the new key records a request
to delete Work Orders or a Part Number permanently; the host job carries it
out as the maintenance database role, after a ``pre-purge`` backup, under
row guards that admit exactly the rows the request stored. What a deletion
deletes is unchanged (``app.application.permanent_deletion.build_plan``).

- The permission key ``DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY``:
  ``ck_role_permissions_permission_known`` admits it, and it is granted to
  every role that holds every other key (PROJECT_PROFILE §20: the Super User
  role), never to a role by its name. Each role that receives it moves
  ``updated_at`` and gets one ``Role`` ``UPDATED`` audit row without an
  actor, with the metadata ``{"source": "migration", "revision": ...}``, as
  ``0040`` does.
- ``deletion_requests`` — one row per request: its state (``APPROVED``,
  ``RUNNING``; ``COMPLETED``, ``FAILED``, ``CANCELLED``), the scope as
  requested (Work Order Numbers, canonical Part Numbers), the reason, the
  requesting User or — for ``pf delete-permanently`` — the operator's name,
  the request time, the plan's row counts per table and the records it
  names, and the job's progress (start, the label and the evidence of the
  pre-purge backup, the end, the failure reason, who cancelled). At most one
  request is open (``uq_deletion_requests_open``). Grant class
  ``GUARDED_UPDATE``; the row trigger ``trg_deletion_requests_guard_mutation``
  lets the application role only insert an ``APPROVED`` request and cancel
  an ``APPROVED`` one, lets only the table owner record an operator's
  request, and leaves the other transitions to the host job (a role that
  may write archive records, while ``partflow.deletion_request_id`` names
  the request): ``APPROVED`` → ``RUNNING`` or ``FAILED``, ``RUNNING`` →
  ``COMPLETED`` or ``FAILED``. The request never changes, a finished request
  never changes, and no request is deleted.
- ``deletion_request_rows`` — every row a request deletes: the table and
  the row's primary key as text. Grant class ``APPEND_ONLY``; the row
  trigger ``trg_deletion_request_rows_guard_mutation`` admits an insert only
  in the transaction that recorded the request, while it is ``APPROVED``;
  no row is changed or deleted.
- The row guards (design §8.2, extended to every table a deletion deletes
  from): a row-level ``BEFORE DELETE`` trigger ``trg_<table>_guard_delete``
  on ``audit_events``, ``quantity_flow_lineage``, ``quantity_flows``,
  ``assigned_route_steps``, ``assigned_routes``, ``work_order_allocations``,
  ``work_order_demands``, ``work_orders`` and ``part_numbers`` (function
  ``partflow_guard_delete``), and the replaced function of
  ``trg_part_movements_guard_delete``. While ``partflow.deletion_request_id``
  is set, every row must be a stored row of that request, the request must
  be ``RUNNING`` and the role one that may write archive records (the
  maintenance role or the owner); otherwise the delete raises (``P0001``).
  Without the marker, the deletes these tables allowed before stay as they
  were: the application's own deletes of a route adjustment's future steps,
  of a demand line and of a Part Number master (by any role but the
  maintenance role), and the purge of an archival run on ``part_movements``;
  every other delete raises, for every role.
- The append-only tables keep refusing ``UPDATE`` and ``TRUNCATE`` for
  everyone: the statement triggers ``trg_audit_events_forbid_mutation``,
  ``trg_quantity_flow_lineage_forbid_mutation`` and
  ``trg_work_order_allocations_forbid_mutation`` now cover ``UPDATE OR
  TRUNCATE`` (their functions unchanged), as ``part_movements``' does since
  ``0041``; the row guard judges each delete. ``machine_lifecycle_events``
  keeps refusing every change.
- ``trg_work_orders_maintenance_forbid_update`` and
  ``trg_work_order_demands_maintenance_forbid_update`` (statement-level,
  function ``partflow_maintenance_forbid_update``): the maintenance role
  holds UPDATE on these two tables only for the row locks of a deletion
  (``SELECT … FOR UPDATE`` needs it) and never changes a row.
- ``partflow_audit_events_maintenance_scope`` (replaced): the maintenance
  role appends ``HistoryArchiveRun`` rows, the ``DeletionRequest`` rows of
  the request it works on, and, while that request is ``RUNNING``, one
  ``DELETED`` row per Work Order it stored and per Part Number of its scope.
  ``ck_audit_events_entity_type`` gains ``DeletionRequest``.

The maintenance role's new privileges (SELECT, INSERT and DELETE on
``audit_events``; DELETE on the other tables a deletion deletes from;
SELECT and DELETE on ``work_order_allocations``; UPDATE on ``work_orders``
and ``work_order_demands`` for the row locks; SELECT and UPDATE on the
requests, SELECT on their rows) are grants, derived by ``apply-grants`` in
every ``migrate`` from ``app.infrastructure.database_privileges``.

The downgrade REFUSES while a deletion request or a ``DeletionRequest``
audit row exists; otherwise it revokes the key (one ``Role`` ``UPDATED``
audit row per role it changes) and restores ``0041`` exactly.
``alembic/env.py`` runs in one transaction. Run it only against disposable
development and test databases.

Revision ID: 0042_phase16_deletion_requests
Revises: 0041_phase16_history_purge
Create Date: 2026-11-18

"""

import json
from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042_phase16_deletion_requests"
down_revision: str | None = "0041_phase16_history_purge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py or enums
# counterpart, and each _PREVIOUS_ value equals migrations 0040 and 0041.
_REQUESTS = "deletion_requests"
_ROWS = "deletion_request_rows"
_AUDIT = "audit_events"
_KEY = "DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY"
_AUDIT_METADATA = {"source": "migration", "revision": revision}

# Every key the CHECK admitted before this revision (0040), in its order.
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
    "RUN_HISTORY_ARCHIVAL_AND_PURGE",
)
_PERMISSIONS = (*_PREVIOUS_PERMISSIONS, _KEY)
_PERMISSION_CHECK = "ck_role_permissions_permission_known"


def _permission_sql(keys: Iterable[str]) -> str:
    return "permission IN (" + ", ".join(f"'{key}'" for key in keys) + ")"


_PREVIOUS_PERMISSION_SQL = _permission_sql(_PREVIOUS_PERMISSIONS)
_PERMISSION_SQL = _permission_sql(_PERMISSIONS)

_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
# Exactly what 0040 left (the downgrade restores it).
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute', 'HistoryArchiveRun')"
)
_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute', 'HistoryArchiveRun', 'DeletionRequest')"
)

_STATES = ("APPROVED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
_FINAL_STATES = ("COMPLETED", "FAILED", "CANCELLED")
_OPEN_STATES = ("APPROVED", "RUNNING")
#: The tables a deletion deletes from, in delete (dependency) order.
_TABLES = (
    "audit_events",
    "part_movements",
    "quantity_flow_lineage",
    "quantity_flows",
    "assigned_route_steps",
    "assigned_routes",
    "work_order_allocations",
    "work_order_demands",
    "work_orders",
    "part_numbers",
)
#: The tables whose new row guard is ``partflow_guard_delete`` (part_movements
#: keeps its own guard, whose function this revision replaces).
_GUARDED_TABLES = tuple(table for table in _TABLES if table != "part_movements")
#: The append-only tables whose statement guard now covers UPDATE and TRUNCATE only.
_STATEMENT_TABLES = ("audit_events", "quantity_flow_lineage", "work_order_allocations")
#: The tables the maintenance role may lock rows of (UPDATE), never change.
_LOCKED_TABLES = ("work_order_demands", "work_orders")
_BACKUP_NAME_PATTERN = "^[0-9]{8}T[0-9]{6}Z-pre-purge-deletion-[0-9]+-[0-9]{8}T[0-9]{6}Z$"


def _in(column: str, values: Iterable[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


_REQUEST_CHECKS = (
    ("state", _in("state", _STATES)),
    (
        "scope_shape",
        "cardinality(work_order_numbers) + cardinality(part_numbers) >= 1"
        " AND array_position(work_order_numbers, NULL) IS NULL"
        " AND array_position(part_numbers, NULL) IS NULL",
    ),
    ("reason_length", "char_length(reason) BETWEEN 1 AND 500"),
    (
        "requester_shape",
        "(requested_by_user_id IS NULL) <> (requested_by_operator IS NULL)"
        " AND (requested_by_operator IS NULL OR char_length(requested_by_operator)"
        " BETWEEN 1 AND 500)",
    ),
    ("plan_shape", "jsonb_typeof(row_counts) = 'object' AND jsonb_typeof(records) = 'object'"),
    (
        "progress_shape",
        "(started_at IS NULL) = (backup_label IS NULL)"
        " AND (state NOT IN ('APPROVED', 'CANCELLED') OR started_at IS NULL)"
        " AND (state NOT IN ('RUNNING', 'COMPLETED') OR started_at IS NOT NULL)"
        " AND (started_at IS NULL OR (started_at = date_trunc('second', started_at)"
        " AND backup_label = 'deletion-' || id || '-'"
        " || to_char(started_at AT TIME ZONE 'UTC', 'YYYYMMDD\"T\"HH24MISS\"Z\"')))",
    ),
    (
        "backup_shape",
        "(pre_purge_backup_name IS NULL) = (pre_purge_backup_started_at IS NULL)"
        " AND (pre_purge_backup_name IS NULL OR (pre_purge_backup_name ~ '"
        + _BACKUP_NAME_PATTERN
        + "' AND substr(pre_purge_backup_name, 28) = backup_label"
        " AND pre_purge_backup_started_at >= started_at))"
        " AND (state <> 'COMPLETED' OR pre_purge_backup_name IS NOT NULL)"
        " AND (pre_purge_backup_name IS NULL OR state IN ('COMPLETED', 'FAILED'))",
    ),
    (
        "failure_shape",
        "(state = 'FAILED') = (failure_reason IS NOT NULL)"
        " AND (failure_reason IS NULL OR char_length(failure_reason) BETWEEN 1 AND 2000)",
    ),
    (
        "finish_shape",
        "(" + _in("state", _FINAL_STATES) + ") = (finished_at IS NOT NULL)"
        " AND (state = 'CANCELLED') = (cancelled_by_user_id IS NOT NULL)",
    ),
)
_ROW_CHECKS = (
    ("table_name", _in("table_name", _TABLES)),
    (
        "row_key_shape",
        "(table_name = 'part_numbers' AND row_key = upper(row_key COLLATE \"C\")"
        " AND row_key COLLATE \"C\" !~ '[[:space:]]' AND row_key <> '')"
        " OR (table_name <> 'part_numbers' AND row_key ~ '^[1-9][0-9]{0,18}$')",
    ),
)
_OPEN_INDEX = "uq_deletion_requests_open"

# The guard functions and triggers (pinned by app.infrastructure.database_privileges:
# GUARD_TRIGGERS and the SHA-256 of each function's source).
_REQUESTS_FUNCTION = "partflow_deletion_requests_guard_mutation"
_ROWS_FUNCTION = "partflow_deletion_request_rows_guard_mutation"
_GUARD_FUNCTION = "partflow_guard_delete"
_MOVEMENTS_FUNCTION = "partflow_part_movements_guard_delete"
_AUDIT_FUNCTION = "partflow_audit_events_maintenance_scope"
_LOCK_FUNCTION = "partflow_maintenance_forbid_update"

_REQUESTS_BODY = """
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'deletion_requests rows are never deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'APPROVED' OR NEW.started_at IS NOT NULL OR NEW.backup_label IS NOT NULL
            OR NEW.pre_purge_backup_name IS NOT NULL OR NEW.pre_purge_backup_started_at IS NOT NULL
            OR NEW.finished_at IS NOT NULL OR NEW.failure_reason IS NOT NULL
            OR NEW.cancelled_by_user_id IS NOT NULL THEN
            RAISE EXCEPTION 'a deletion request is recorded APPROVED, with no progress';
        END IF;
        IF NEW.requested_at IS DISTINCT FROM now() THEN
            RAISE EXCEPTION 'a deletion request is recorded at the start of its transaction';
        END IF;
        -- An operator's request (pf delete-permanently) only by the owner of this table.
        IF NEW.requested_by_user_id IS NULL AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
            RAISE EXCEPTION
                'only the database owner role records the deletion request of an operator';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.state IN ('COMPLETED', 'FAILED', 'CANCELLED') THEN
        RAISE EXCEPTION 'deletion request % is finished (%) and never changes', OLD.id, OLD.state;
    END IF;
    IF (NEW.id, NEW.work_order_numbers, NEW.part_numbers, NEW.reason, NEW.requested_by_user_id,
        NEW.requested_by_operator, NEW.requested_at, NEW.row_counts, NEW.records)
        IS DISTINCT FROM
        (OLD.id, OLD.work_order_numbers, OLD.part_numbers, OLD.reason, OLD.requested_by_user_id,
        OLD.requested_by_operator, OLD.requested_at, OLD.row_counts, OLD.records)
    THEN
        RAISE EXCEPTION 'the request of deletion request % never changes', OLD.id;
    END IF;
    -- The host job: a role that may write archive records, working on this request.
    -- Only a person cancels a request.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND current_setting('partflow.deletion_request_id', true) = OLD.id::text THEN
        IF NEW.cancelled_by_user_id IS NOT NULL THEN
            RAISE EXCEPTION 'deletion request %: the host job never cancels a request', OLD.id;
        END IF;
        IF OLD.state = 'APPROVED' AND NEW.state = 'RUNNING'
            AND NEW.pre_purge_backup_name IS NULL AND NEW.failure_reason IS NULL THEN
            RETURN NEW;
        END IF;
        IF OLD.state = 'APPROVED' AND NEW.state = 'FAILED' AND NEW.started_at IS NULL THEN
            RETURN NEW;
        END IF;
        IF OLD.state = 'RUNNING' AND NEW.state IN ('COMPLETED', 'FAILED')
            AND (NEW.started_at, NEW.backup_label)
                IS NOT DISTINCT FROM (OLD.started_at, OLD.backup_label) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'deletion request %: the host job cannot change % to %',
            OLD.id, OLD.state, NEW.state;
    END IF;
    -- The maintenance role (it may write archive records and does not own this table)
    -- changes a request only as its job.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RAISE EXCEPTION
            'deletion request %: the maintenance role changes a request only as its job', OLD.id;
    END IF;
    -- The application: cancel an approved request.
    IF NEW.state = 'CANCELLED' AND OLD.state = 'APPROVED'
        AND to_jsonb(NEW) - ARRAY['state', 'finished_at', 'cancelled_by_user_id']
            = to_jsonb(OLD) - ARRAY['state', 'finished_at', 'cancelled_by_user_id'] THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'deletion request %: only the host job makes this change', OLD.id;
END;
"""
_ROWS_BODY = """
BEGIN
    IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'deletion_request_rows rows are never deleted';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'deletion_request_rows rows never change';
    END IF;
    -- Written only with the request: by the transaction that recorded it, while APPROVED.
    IF NOT EXISTS (SELECT 1 FROM public.deletion_requests q
        WHERE q.id = NEW.request_id AND q.state = 'APPROVED' AND q.requested_at = now()) THEN
        RAISE EXCEPTION 'the rows of deletion request % are written only with the request',
            NEW.request_id;
    END IF;
    RETURN NEW;
END;
"""
_GUARD_BODY = """
DECLARE
    marker text := current_setting('partflow.deletion_request_id', true);
    key_text text;
    allowed boolean;
BEGIN
    IF TG_TABLE_NAME = 'part_numbers' THEN
        key_text := OLD.part_number;
    ELSE
        key_text := OLD.id::text;
    END IF;
    -- A permanent deletion: the host job (a role that may write archive records)
    -- deletes only the stored rows of the RUNNING request the marker names.
    IF coalesce(marker, '') <> '' THEN
        IF marker ~ '^[1-9][0-9]{0,8}$'
            AND has_table_privilege('public.history_archived_flows', 'INSERT') THEN
            SELECT true INTO allowed
            FROM public.deletion_requests q
            JOIN public.deletion_request_rows r ON r.request_id = q.id
            WHERE q.id = marker::integer AND q.state = 'RUNNING'
                AND r.table_name = TG_TABLE_NAME AND r.row_key = key_text;
        END IF;
        IF allowed IS NULL THEN
            RAISE EXCEPTION 'deletion request %: % row % is not one of its stored rows,'
                ' or the request is not RUNNING', marker, TG_TABLE_NAME, key_text;
        END IF;
        RETURN OLD;
    END IF;
    -- The deletes these tables allowed before stay: a route adjustment's future
    -- steps, a demand line removal and a Part Number master deletion, by any role but
    -- the maintenance role (it may write archive records and does not own the table).
    IF TG_TABLE_NAME IN ('assigned_route_steps', 'work_order_demands', 'part_numbers')
        AND NOT (has_table_privilege('public.history_archived_flows', 'INSERT')
            AND NOT pg_has_role(
                (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE'))
    THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% row % may be deleted only by the host job carrying out a permanent'
        ' deletion request that lists it', TG_TABLE_NAME, key_text;
END;
"""
_MOVEMENTS_BODY = """
DECLARE
    marker text := current_setting('partflow.archive_run_id', true);
    request text := current_setting('partflow.deletion_request_id', true);
    allowed boolean;
BEGIN
    -- A permanent deletion: only the stored Movements of the RUNNING request the
    -- marker names, by a role that may write archive records.
    IF coalesce(request, '') <> '' THEN
        IF request ~ '^[1-9][0-9]{0,8}$'
            AND has_table_privilege('public.history_archived_flows', 'INSERT') THEN
            SELECT true INTO allowed
            FROM public.deletion_requests q
            JOIN public.deletion_request_rows r ON r.request_id = q.id
            WHERE q.id = request::integer AND q.state = 'RUNNING'
                AND r.table_name = 'part_movements' AND r.row_key = OLD.id::text;
        END IF;
        IF allowed IS NULL THEN
            RAISE EXCEPTION 'deletion request %: part_movements row % is not one of its stored'
                ' rows, or the request is not RUNNING', request, OLD.id;
        END IF;
        RETURN OLD;
    END IF;
    -- Only the purge of the archival run that recorded the row's flow deletes it:
    -- every condition is checked for every row, and a row that fails one raises.
    IF marker ~ '^[1-9][0-9]{0,8}$' THEN
        SELECT true INTO allowed
        FROM public.history_archive_runs r
        JOIN public.history_archived_flows f ON f.run_id = r.id
        WHERE r.id = marker::integer AND r.state = 'PURGING'
            AND f.flow_id = OLD.quantity_flow_id
            AND f.purged_at IS NULL AND f.skipped_at IS NULL
            AND OLD.id <= f.last_movement_id
            AND OLD.occurred_at < r.cutoff AND OLD.server_received_at < r.cutoff;
    END IF;
    IF allowed IS NULL THEN
        RAISE EXCEPTION
            'part_movements is append-only: Movement % may be deleted only by the purge of'
            ' the archival run that archived its flow, or by the permanent deletion request'
            ' that lists it', OLD.id;
    END IF;
    RETURN OLD;
END;
"""
_AUDIT_BODY = """
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
_LOCK_BODY = """
BEGIN
    -- The maintenance role (it may write archive records and does not own this table)
    -- holds UPDATE here only for the row locks of a permanent deletion.
    IF has_table_privilege('public.history_archived_flows', 'INSERT')
        AND NOT pg_has_role(
            (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid = TG_RELID), 'USAGE') THEN
        RAISE EXCEPTION 'the maintenance role never changes %: it locks its rows only, for a'
            ' permanent deletion', TG_TABLE_NAME;
    END IF;
    RETURN NULL;
END;
"""


def _function(name: str, body: str, *, replace: bool) -> str:
    verb = "CREATE OR REPLACE" if replace else "CREATE"
    return f"{verb} FUNCTION {name}() RETURNS trigger\nLANGUAGE plpgsql AS $${body}$$;\n"


def _statement_trigger(table: str, events: str) -> str:
    return (
        f"CREATE TRIGGER trg_{table}_forbid_mutation BEFORE {events} ON {table}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION partflow_{table}_forbid_mutation()"
    )


_PREVIOUS_EVENTS = "UPDATE OR DELETE OR TRUNCATE"
_EVENTS = "UPDATE OR TRUNCATE"

#: (name, CREATE TRIGGER statement, table) of every trigger this revision creates.
_TRIGGERS = (
    *(
        (
            f"trg_{table}_guard_delete",
            f"CREATE TRIGGER trg_{table}_guard_delete BEFORE DELETE ON {table}"
            f" FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()",
            table,
        )
        for table in _GUARDED_TABLES
    ),
    *(
        (
            f"trg_{table}_maintenance_forbid_update",
            f"CREATE TRIGGER trg_{table}_maintenance_forbid_update BEFORE UPDATE ON {table}"
            f" FOR EACH STATEMENT EXECUTE FUNCTION {_LOCK_FUNCTION}()",
            table,
        )
        for table in _LOCKED_TABLES
    ),
    (
        "trg_deletion_requests_guard_mutation",
        f"CREATE TRIGGER trg_deletion_requests_guard_mutation"
        f" BEFORE INSERT OR UPDATE OR DELETE ON {_REQUESTS}"
        f" FOR EACH ROW EXECUTE FUNCTION {_REQUESTS_FUNCTION}()",
        _REQUESTS,
    ),
    (
        "trg_deletion_requests_forbid_truncate",
        f"CREATE TRIGGER trg_deletion_requests_forbid_truncate BEFORE TRUNCATE ON {_REQUESTS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {_REQUESTS_FUNCTION}()",
        _REQUESTS,
    ),
    (
        "trg_deletion_request_rows_guard_mutation",
        f"CREATE TRIGGER trg_deletion_request_rows_guard_mutation"
        f" BEFORE INSERT OR UPDATE OR DELETE ON {_ROWS}"
        f" FOR EACH ROW EXECUTE FUNCTION {_ROWS_FUNCTION}()",
        _ROWS,
    ),
    (
        "trg_deletion_request_rows_forbid_truncate",
        f"CREATE TRIGGER trg_deletion_request_rows_forbid_truncate BEFORE TRUNCATE ON {_ROWS}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {_ROWS_FUNCTION}()",
        _ROWS,
    ),
)


def _create_tables() -> None:
    op.create_table(
        _REQUESTS,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column(
            "work_order_numbers",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "part_numbers",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("requested_by_operator", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("records", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_label", sa.Text(), nullable=True),
        sa.Column("pre_purge_backup_name", sa.Text(), nullable=True),
        sa.Column("pre_purge_backup_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_deletion_requests"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_deletion_requests_requested_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name="fk_deletion_requests_cancelled_by_user_id_users",
        ),
        *(
            sa.CheckConstraint(sql, name=op.f(f"ck_deletion_requests_{suffix}"))
            for suffix, sql in _REQUEST_CHECKS
        ),
    )
    op.create_index(
        _OPEN_INDEX,
        _REQUESTS,
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text(_in("state", _OPEN_STATES)),
    )
    op.create_table(
        _ROWS,
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "request_id", "table_name", "row_key", name="pk_deletion_request_rows"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            [f"{_REQUESTS}.id"],
            name="fk_deletion_request_rows_request_id_deletion_requests",
        ),
        *(
            sa.CheckConstraint(sql, name=op.f(f"ck_deletion_request_rows_{suffix}"))
            for suffix, sql in _ROW_CHECKS
        ),
    )


def _create_guards() -> None:
    op.execute(_function(_REQUESTS_FUNCTION, _REQUESTS_BODY, replace=False))
    op.execute(_function(_ROWS_FUNCTION, _ROWS_BODY, replace=False))
    op.execute(_function(_GUARD_FUNCTION, _GUARD_BODY, replace=False))
    op.execute(_function(_LOCK_FUNCTION, _LOCK_BODY, replace=False))
    op.execute(_function(_MOVEMENTS_FUNCTION, _MOVEMENTS_BODY, replace=True))
    op.execute(_function(_AUDIT_FUNCTION, _AUDIT_BODY, replace=True))
    # One transaction: no table is ever without its guard.
    for table in _STATEMENT_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_forbid_mutation ON {table}")
        op.execute(_statement_trigger(table, _EVENTS))
    for _name, statement, _table in _TRIGGERS:
        op.execute(statement)


def _audit(
    connection: sa.Connection,
    *,
    entity_id: str,
    before: dict[str, object],
    after: dict[str, object],
    metadata: dict[str, object],
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at,"
            " before_data, after_data, metadata) VALUES ('UPDATED', 'Role', :entity_id,"
            " now(), CAST(:before AS jsonb), CAST(:after AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "entity_id": entity_id,
            "before": json.dumps(before),
            "after": json.dumps(after),
            "metadata": json.dumps(metadata),
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
        entity_id=str(role_id),
        before=_role_snapshot(name, held),
        after=_role_snapshot(name, after),
        metadata=metadata,
    )


def _grant_the_key(connection: sa.Connection) -> None:
    """Every role that holds every other key receives it; no role by its name."""
    every_other_key = set(_PREVIOUS_PERMISSIONS)
    for role_id, name, held in _roles(connection):
        if _KEY not in held and every_other_key <= held:
            _change_role(connection, role_id, name, held, held | {_KEY}, _AUDIT_METADATA)


def upgrade() -> None:
    connection = op.get_bind()
    op.drop_constraint(op.f(_PERMISSION_CHECK), "role_permissions", type_="check")
    op.create_check_constraint(op.f(_PERMISSION_CHECK), "role_permissions", _PERMISSION_SQL)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ENTITY_TYPES)
    _create_tables()
    _create_guards()
    _grant_the_key(connection)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_REQUESTS})"
        f"  OR EXISTS (SELECT 1 FROM {_AUDIT} WHERE entity_type = 'DeletionRequest')"
        " THEN RAISE EXCEPTION 'Permanent deletion request data exists; refusing downgrade';"
        " END IF; END $$;"
    )
    connection = op.get_bind()
    metadata = {**_AUDIT_METADATA, "downgrade": True}
    for role_id, name, held in _roles(connection):
        if _KEY in held:
            _change_role(connection, role_id, name, held, held - {_KEY}, metadata)
    for name, _statement, table in reversed(_TRIGGERS):
        op.execute(f"DROP TRIGGER {name} ON {table}")
    for table in reversed(_STATEMENT_TABLES):
        op.execute(f"DROP TRIGGER trg_{table}_forbid_mutation ON {table}")
        op.execute(_statement_trigger(table, _PREVIOUS_EVENTS))
    op.execute(_function(_AUDIT_FUNCTION, _PREVIOUS_AUDIT_BODY, replace=True))
    op.execute(_function(_MOVEMENTS_FUNCTION, _PREVIOUS_MOVEMENTS_BODY, replace=True))
    for function in (_LOCK_FUNCTION, _GUARD_FUNCTION, _ROWS_FUNCTION, _REQUESTS_FUNCTION):
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_table(_ROWS)
    op.drop_index(_OPEN_INDEX, table_name=_REQUESTS)
    op.drop_table(_REQUESTS)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)
    op.drop_constraint(op.f(_PERMISSION_CHECK), "role_permissions", type_="check")
    op.create_check_constraint(
        op.f(_PERMISSION_CHECK), "role_permissions", _PREVIOUS_PERMISSION_SQL
    )


# The function bodies the downgrade restores (the schema test asserts that
# they are exactly 0041's Movement guard and 0040's audit scope).
_PREVIOUS_MOVEMENTS_BODY = """
DECLARE
    marker text := current_setting('partflow.archive_run_id', true);
    allowed boolean;
BEGIN
    -- Only the purge of the archival run that recorded the row's flow deletes it:
    -- every condition is checked for every row, and a row that fails one raises.
    IF marker ~ '^[1-9][0-9]{0,8}$' THEN
        SELECT true INTO allowed
        FROM public.history_archive_runs r
        JOIN public.history_archived_flows f ON f.run_id = r.id
        WHERE r.id = marker::integer AND r.state = 'PURGING'
            AND f.flow_id = OLD.quantity_flow_id
            AND f.purged_at IS NULL AND f.skipped_at IS NULL
            AND OLD.id <= f.last_movement_id
            AND OLD.occurred_at < r.cutoff AND OLD.server_received_at < r.cutoff;
    END IF;
    IF allowed IS NULL THEN
        RAISE EXCEPTION
            'part_movements is append-only: Movement % may be deleted only by the purge of'
            ' the archival run that archived its flow', OLD.id;
    END IF;
    RETURN OLD;
END;
"""
_PREVIOUS_AUDIT_BODY = """
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
"""
