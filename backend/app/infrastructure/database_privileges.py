"""Database-role privilege constants (Phase 16 slice 4; CD5, OD-16-08).

One classification of every relation in schema ``public`` at the code
head, the exact table privileges of the application database role per
class, the exact table privileges of the maintenance database role, and
the guard triggers and guard functions the migrations create.
``apply-grants`` derives every grant from these constants and reconcile
check (h) compares the database with them. Tests pin them to the migrated
schema: a migration that adds a table classifies it in the same commit,
and one that adds or changes a guard updates ``GUARD_TRIGGERS`` /
``GUARD_FUNCTION_SHA256`` in the same commit.

"Role" here always means a PostgreSQL database role, never an
application Role.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Final, NamedTuple


class TableClass(StrEnum):
    ORDINARY = "ORDINARY"
    APPEND_ONLY = "APPEND_ONLY"
    GUARDED_UPDATE = "GUARDED_UPDATE"
    NO_UPDATE = "NO_UPDATE"
    READ_ONLY = "READ_ONLY"


#: The exact table privileges of the application database role per class
#: (nothing else, no grant option, is ever granted).
APP_PRIVILEGES: Final[Mapping[TableClass, frozenset[str]]] = {
    TableClass.ORDINARY: frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    TableClass.APPEND_ONLY: frozenset({"SELECT", "INSERT"}),
    # worker_sessions: open-session expiry and end; its FOR NO KEY UPDATE
    # row lock needs UPDATE too. history_archive_runs: a run request, its
    # cancellation and the confirmation of its off-host copy.
    # deletion_requests: a permanent deletion request and its cancellation.
    # The row trigger of each refuses every other change.
    TableClass.GUARDED_UPDATE: frozenset({"SELECT", "INSERT", "UPDATE"}),
    # assigned_route_steps: past steps are immutable; an AssignedRoute
    # adjustment deletes unreferenced future steps.
    TableClass.NO_UPDATE: frozenset({"SELECT", "INSERT", "DELETE"}),
    TableClass.READ_ONLY: frozenset({"SELECT"}),
}

TABLE_CLASSES: Final[Mapping[str, TableClass]] = {
    "alembic_version": TableClass.READ_ONLY,
    "application_policy": TableClass.ORDINARY,
    "areas": TableClass.ORDINARY,
    "assigned_route_steps": TableClass.NO_UPDATE,
    "assigned_routes": TableClass.ORDINARY,
    "audit_events": TableClass.APPEND_ONLY,
    # Permanent deletion requests (history archival step 3): the application
    # records a request with its rows and cancels it; the job does the rest.
    "deletion_request_rows": TableClass.APPEND_ONLY,
    "deletion_requests": TableClass.GUARDED_UPDATE,
    "departments": TableClass.ORDINARY,
    "history_archive_runs": TableClass.GUARDED_UPDATE,
    "history_archived_flows": TableClass.READ_ONLY,
    "machine_asset_tag_config": TableClass.ORDINARY,
    "machine_lifecycle_events": TableClass.APPEND_ONLY,
    "machines": TableClass.ORDINARY,
    "mail_server_credentials": TableClass.ORDINARY,
    "operations": TableClass.ORDINARY,
    "part_movements": TableClass.APPEND_ONLY,
    "part_numbers": TableClass.ORDINARY,
    "quantity_flow_lineage": TableClass.APPEND_ONLY,
    "quantity_flows": TableClass.ORDINARY,
    "role_permissions": TableClass.ORDINARY,
    "roles": TableClass.ORDINARY,
    "route_steps": TableClass.ORDINARY,
    "route_templates": TableClass.ORDINARY,
    "scan_station_devices": TableClass.ORDINARY,
    "scan_stations": TableClass.ORDINARY,
    "user_credentials": TableClass.ORDINARY,
    "user_passkey_setups": TableClass.ORDINARY,
    "user_passkeys": TableClass.ORDINARY,
    "user_recovery_codes": TableClass.ORDINARY,
    "user_sessions": TableClass.ORDINARY,
    "user_sign_in_challenges": TableClass.ORDINARY,
    "user_totp_factors": TableClass.ORDINARY,
    "users": TableClass.ORDINARY,
    "work_order_allocations": TableClass.APPEND_ONLY,
    "work_order_demands": TableClass.ORDINARY,
    "work_orders": TableClass.ORDINARY,
    "worker_sessions": TableClass.GUARDED_UPDATE,
    "workers": TableClass.ORDINARY,
}

_READ: Final = frozenset({"SELECT"})
_READ_DELETE: Final = frozenset({"SELECT", "DELETE"})
#: UPDATE only for ``SELECT … FOR UPDATE``: a statement guard refuses every change.
_READ_LOCK_DELETE: Final = frozenset({"SELECT", "UPDATE", "DELETE"})

#: The exact table privileges of the maintenance database role (CD5;
#: history archival design §8.3, build steps 1 to 3): SELECT on what the
#: archive export reads — Movements, flows, lineage, Assigned Routes and
#: their steps, Work Orders and demand lines, the Part Number master, the
#: context tables, Users/roles/role permissions (also the re-check of the
#: requester of a run or deletion request), the application policy and
#: ``alembic_version`` for the archive manifest —; on the two archival
#: tables SELECT and the UPDATE their guard triggers allow, and INSERT on the
#: records only (a run is requested by a person in Administration, never by
#: the job); DELETE on ``part_movements``, which
#: ``trg_part_movements_guard_delete`` limits to the archived rows of a run
#: in PURGING (build step 2) and to the stored rows of a RUNNING deletion
#: request (build step 3). For permanent deletion requests (build step 3):
#: SELECT and the guarded UPDATE on the requests, SELECT on their rows;
#: SELECT, INSERT and DELETE on ``audit_events`` (the deletion's plan reads
#: the audit rows it deletes; ``trg_audit_events_maintenance_scope`` limits
#: what it appends, ``trg_audit_events_guard_delete`` what it deletes);
#: DELETE on every other table a deletion deletes from, each limited by its
#: row guard to the stored rows of the RUNNING request; SELECT on the
#: allocations the plan reads; and UPDATE on ``work_orders`` and
#: ``work_order_demands`` only for the row locks of a deletion (``SELECT …
#: FOR UPDATE`` needs it), which ``trg_<table>_maintenance_forbid_update``
#: keeps from ever changing a row. Never credentials, sign-in sessions, mail
#: or Scan Station devices.
MAINTENANCE_PRIVILEGES: Final[Mapping[str, frozenset[str]]] = {
    "alembic_version": _READ,
    "application_policy": _READ,
    "areas": _READ,
    "assigned_route_steps": _READ_DELETE,
    "assigned_routes": _READ_DELETE,
    "audit_events": frozenset({"SELECT", "INSERT", "DELETE"}),
    "deletion_request_rows": _READ,
    "deletion_requests": frozenset({"SELECT", "UPDATE"}),
    "history_archive_runs": frozenset({"SELECT", "UPDATE"}),
    "history_archived_flows": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "machines": _READ,
    "operations": _READ,
    "part_movements": _READ_DELETE,
    "part_numbers": _READ_DELETE,
    "quantity_flow_lineage": _READ_DELETE,
    "quantity_flows": _READ_DELETE,
    "role_permissions": _READ,
    "roles": _READ,
    "scan_stations": _READ,
    "users": _READ,
    "work_order_allocations": _READ_DELETE,
    "work_order_demands": _READ_LOCK_DELETE,
    "work_orders": _READ_LOCK_DELETE,
    "worker_sessions": _READ,
    "workers": _READ,
}


class GuardTrigger(NamedTuple):
    table: str
    function: str
    #: ``pg_get_triggerdef`` text with ``search_path = pg_catalog, public``
    #: (PostgreSQL 16).
    definition: str


def _statement_guard(table: str, events: str) -> GuardTrigger:
    function = f"partflow_{table}_forbid_mutation"
    return GuardTrigger(
        table,
        function,
        f"CREATE TRIGGER trg_{table}_forbid_mutation BEFORE {events} ON public.{table}"
        f" FOR EACH STATEMENT EXECUTE FUNCTION {function}()",
    )


_ALL_EVENTS: Final = "DELETE OR UPDATE OR TRUNCATE"
#: The append-only tables a permanent deletion deletes from (build step 3):
#: their statement guard refuses UPDATE and TRUNCATE, their row guard judges
#: each DELETE.
_NO_UPDATE_EVENTS: Final = "UPDATE OR TRUNCATE"


def _delete_guard(table: str) -> GuardTrigger:
    """The row guard of a table a permanent deletion deletes from (0042)."""
    return GuardTrigger(
        table,
        "partflow_guard_delete",
        f"CREATE TRIGGER trg_{table}_guard_delete BEFORE DELETE ON public.{table}"
        " FOR EACH ROW EXECUTE FUNCTION partflow_guard_delete()",
    )


def _lock_guard(table: str) -> GuardTrigger:
    """The maintenance role locks rows of ``table`` for a deletion, never changes one (0042)."""
    return GuardTrigger(
        table,
        "partflow_maintenance_forbid_update",
        f"CREATE TRIGGER trg_{table}_maintenance_forbid_update BEFORE UPDATE ON public.{table}"
        " FOR EACH STATEMENT EXECUTE FUNCTION partflow_maintenance_forbid_update()",
    )


#: The tables a permanent deletion deletes from with the row guard
#: ``partflow_guard_delete`` (``part_movements`` has its own, 0041/0042).
_DELETION_GUARDED: Final = (
    "assigned_route_steps",
    "assigned_routes",
    "audit_events",
    "part_numbers",
    "quantity_flow_lineage",
    "quantity_flows",
    "work_order_allocations",
    "work_order_demands",
    "work_orders",
)

GUARD_TRIGGERS: Final[Mapping[str, GuardTrigger]] = {
    "trg_areas_forbid_barcode_change": GuardTrigger(
        "areas",
        "partflow_areas_forbid_barcode_change",
        "CREATE TRIGGER trg_areas_forbid_barcode_change BEFORE UPDATE OF barcode_value ON"
        " public.areas FOR EACH ROW WHEN (((old.barcode_value IS NOT NULL) AND"
        " (new.barcode_value IS DISTINCT FROM old.barcode_value))) EXECUTE FUNCTION"
        " partflow_areas_forbid_barcode_change()",
    ),
    "trg_assigned_route_steps_forbid_update": GuardTrigger(
        "assigned_route_steps",
        "partflow_assigned_route_steps_forbid_update",
        "CREATE TRIGGER trg_assigned_route_steps_forbid_update BEFORE UPDATE ON"
        " public.assigned_route_steps FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_assigned_route_steps_forbid_update()",
    ),
    "trg_audit_events_forbid_mutation": _statement_guard("audit_events", _NO_UPDATE_EVENTS),
    # The maintenance role appends HistoryArchiveRun rows (0040), and the
    # DeletionRequest rows and DELETED rows of the request it works on (0042).
    "trg_audit_events_maintenance_scope": GuardTrigger(
        "audit_events",
        "partflow_audit_events_maintenance_scope",
        "CREATE TRIGGER trg_audit_events_maintenance_scope BEFORE INSERT ON public.audit_events"
        " FOR EACH ROW WHEN ((new.entity_type <> 'HistoryArchiveRun'::text)) EXECUTE FUNCTION"
        " partflow_audit_events_maintenance_scope()",
    ),
    # The request and row guards of permanent deletion requests (0042): the
    # row trigger judges every insert and change; nothing is deleted or
    # truncated.
    "trg_deletion_request_rows_forbid_truncate": GuardTrigger(
        "deletion_request_rows",
        "partflow_deletion_request_rows_guard_mutation",
        "CREATE TRIGGER trg_deletion_request_rows_forbid_truncate BEFORE TRUNCATE ON"
        " public.deletion_request_rows FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_deletion_request_rows_guard_mutation()",
    ),
    "trg_deletion_request_rows_guard_mutation": GuardTrigger(
        "deletion_request_rows",
        "partflow_deletion_request_rows_guard_mutation",
        "CREATE TRIGGER trg_deletion_request_rows_guard_mutation BEFORE INSERT OR DELETE OR"
        " UPDATE ON public.deletion_request_rows FOR EACH ROW EXECUTE FUNCTION"
        " partflow_deletion_request_rows_guard_mutation()",
    ),
    "trg_deletion_requests_forbid_truncate": GuardTrigger(
        "deletion_requests",
        "partflow_deletion_requests_guard_mutation",
        "CREATE TRIGGER trg_deletion_requests_forbid_truncate BEFORE TRUNCATE ON"
        " public.deletion_requests FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_deletion_requests_guard_mutation()",
    ),
    "trg_deletion_requests_guard_mutation": GuardTrigger(
        "deletion_requests",
        "partflow_deletion_requests_guard_mutation",
        "CREATE TRIGGER trg_deletion_requests_guard_mutation BEFORE INSERT OR DELETE OR UPDATE"
        " ON public.deletion_requests FOR EACH ROW EXECUTE FUNCTION"
        " partflow_deletion_requests_guard_mutation()",
    ),
    # The run and record guards of history archival (0040): the row trigger
    # judges every insert and change; no row is ever deleted or truncated.
    "trg_history_archive_runs_forbid_truncate": GuardTrigger(
        "history_archive_runs",
        "partflow_history_archive_runs_guard_mutation",
        "CREATE TRIGGER trg_history_archive_runs_forbid_truncate BEFORE TRUNCATE ON"
        " public.history_archive_runs FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_history_archive_runs_guard_mutation()",
    ),
    "trg_history_archive_runs_guard_mutation": GuardTrigger(
        "history_archive_runs",
        "partflow_history_archive_runs_guard_mutation",
        "CREATE TRIGGER trg_history_archive_runs_guard_mutation BEFORE INSERT OR DELETE OR UPDATE"
        " ON public.history_archive_runs FOR EACH ROW EXECUTE FUNCTION"
        " partflow_history_archive_runs_guard_mutation()",
    ),
    "trg_history_archived_flows_forbid_truncate": GuardTrigger(
        "history_archived_flows",
        "partflow_history_archived_flows_guard_mutation",
        "CREATE TRIGGER trg_history_archived_flows_forbid_truncate BEFORE TRUNCATE ON"
        " public.history_archived_flows FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_history_archived_flows_guard_mutation()",
    ),
    "trg_history_archived_flows_guard_mutation": GuardTrigger(
        "history_archived_flows",
        "partflow_history_archived_flows_guard_mutation",
        "CREATE TRIGGER trg_history_archived_flows_guard_mutation BEFORE INSERT OR DELETE OR"
        " UPDATE ON public.history_archived_flows FOR EACH ROW EXECUTE FUNCTION"
        " partflow_history_archived_flows_guard_mutation()",
    ),
    "trg_machine_lifecycle_events_forbid_mutation": _statement_guard(
        "machine_lifecycle_events", _ALL_EVENTS
    ),
    "trg_machines_forbid_area_change": GuardTrigger(
        "machines",
        "partflow_machines_forbid_area_change",
        "CREATE TRIGGER trg_machines_forbid_area_change BEFORE UPDATE OF area_id ON"
        " public.machines FOR EACH ROW WHEN (((new.area_id IS DISTINCT FROM old.area_id) AND"
        " (NOT ((old.retired_on IS NOT NULL) AND (new.retired_on IS NULL))))) EXECUTE FUNCTION"
        " partflow_machines_forbid_area_change()",
    ),
    "trg_machines_forbid_asset_tag_change": GuardTrigger(
        "machines",
        "partflow_machines_forbid_asset_tag_change",
        "CREATE TRIGGER trg_machines_forbid_asset_tag_change BEFORE UPDATE OF asset_tag ON"
        " public.machines FOR EACH ROW WHEN ((new.asset_tag IS DISTINCT FROM old.asset_tag))"
        " EXECUTE FUNCTION partflow_machines_forbid_asset_tag_change()",
    ),
    # Movements: never updated or truncated; a delete is judged row by row
    # and allowed only to the purge of an archival run (0041) and to the
    # permanent deletion request that stored the row (0042).
    "trg_part_movements_forbid_mutation": _statement_guard("part_movements", "UPDATE OR TRUNCATE"),
    "trg_part_movements_guard_delete": GuardTrigger(
        "part_movements",
        "partflow_part_movements_guard_delete",
        "CREATE TRIGGER trg_part_movements_guard_delete BEFORE DELETE ON public.part_movements"
        " FOR EACH ROW EXECUTE FUNCTION partflow_part_movements_guard_delete()",
    ),
    "trg_quantity_flow_lineage_forbid_mutation": _statement_guard(
        "quantity_flow_lineage", _NO_UPDATE_EVENTS
    ),
    "trg_work_order_allocations_forbid_mutation": _statement_guard(
        "work_order_allocations", _NO_UPDATE_EVENTS
    ),
    # Every table a permanent deletion deletes from: a delete is judged row
    # by row (0042); the deletes the application made before stay allowed.
    **{f"trg_{table}_guard_delete": _delete_guard(table) for table in _DELETION_GUARDED},
    # The maintenance role's UPDATE serves only the row locks of a deletion (0042).
    "trg_work_order_demands_maintenance_forbid_update": _lock_guard("work_order_demands"),
    "trg_work_orders_maintenance_forbid_update": _lock_guard("work_orders"),
    "trg_worker_sessions_forbid_truncate": GuardTrigger(
        "worker_sessions",
        "partflow_worker_sessions_guard_mutation",
        "CREATE TRIGGER trg_worker_sessions_forbid_truncate BEFORE TRUNCATE ON"
        " public.worker_sessions FOR EACH STATEMENT EXECUTE FUNCTION"
        " partflow_worker_sessions_guard_mutation()",
    ),
    "trg_worker_sessions_guard_mutation": GuardTrigger(
        "worker_sessions",
        "partflow_worker_sessions_guard_mutation",
        "CREATE TRIGGER trg_worker_sessions_guard_mutation BEFORE DELETE OR UPDATE ON"
        " public.worker_sessions FOR EACH ROW EXECUTE FUNCTION"
        " partflow_worker_sessions_guard_mutation()",
    ),
}

#: SHA-256 (hex) of each guard function's ``pg_proc.prosrc`` (UTF-8).
GUARD_FUNCTION_SHA256: Final[Mapping[str, str]] = {
    "partflow_areas_forbid_barcode_change": (
        "1d1c8591bc8d4526f58d13d2e5bd4982ff430fb5128f868026e845e26aebf3bb"
    ),
    "partflow_assigned_route_steps_forbid_update": (
        "d92ed7d241178127c4a1a9c88a4786b4f539660cebfad60360dcc5a6c5f860a8"
    ),
    "partflow_audit_events_forbid_mutation": (
        "b366828e03acbf84260a4fb8efef0a9e0175fd6a24c18bcfb360143ee903f85c"
    ),
    "partflow_audit_events_maintenance_scope": (
        "271f29aac4a994e6a3026f817b3fd6d9df3a4f744368179de95aa38079f5f1a0"
    ),
    "partflow_deletion_request_rows_guard_mutation": (
        "ac2b65388b5d9ecb41812a3fee77e8b0959001340cf3ed5e43b15e2bd87952f3"
    ),
    "partflow_deletion_requests_guard_mutation": (
        "9646ef80a2a99f88afa5f73128660bd07626b5829199bb93ef8fca28fbc81a88"
    ),
    "partflow_guard_delete": ("ac78c72998a7e5262309da70c18a8138583b22b33fe7da629b7596a24e51ae63"),
    "partflow_history_archive_runs_guard_mutation": (
        "40c21ea2f2c577c385c682377470476f218c1dc37c747f38e2aad0fb302f52af"
    ),
    "partflow_history_archived_flows_guard_mutation": (
        "bd48a533b98c9b6d7571c9888fe50b64e7d1c81aa16ec46e115018b93e3504fd"
    ),
    "partflow_machine_lifecycle_events_forbid_mutation": (
        "6a29a65f2e3ddee67b0f3bbda429d72b7fed6ff72564bc6bd7466724109f52b1"
    ),
    "partflow_machines_forbid_area_change": (
        "acb7c8a5fd5551f2e8e9b0d80d19b6ad0405355caa323cb391808ea3e66e73ee"
    ),
    "partflow_machines_forbid_asset_tag_change": (
        "2cf004df5fb36f07933ea264bfef2dfbc1da993d25d70a49dbed5c79bde6a6fe"
    ),
    "partflow_maintenance_forbid_update": (
        "da2894a1cf118461576f7a0652f8b62808c3a0ba748438f2912243a0e612ab36"
    ),
    "partflow_part_movements_forbid_mutation": (
        "e66962957036fd21cd17cc9cc002450969508d29165811845626d87317dd6764"
    ),
    "partflow_part_movements_guard_delete": (
        "19f2dc5aad885d04f2dd583061d49fbc3247b0ad4b845a0131c30633915093b0"
    ),
    "partflow_quantity_flow_lineage_forbid_mutation": (
        "de7c37d36a72a5c7286e7d4c2293a404d546a7ed2a8aaddfbbbaaa0818a5614a"
    ),
    "partflow_work_order_allocations_forbid_mutation": (
        "f8bbe9dfef93108b075e4e94bfd130fb2fe28820fd8faa1a8dfc72a37945bf59"
    ),
    "partflow_worker_sessions_guard_mutation": (
        "82aef253638f9937413741a87f86061d65965f376a986b94aa1ae41eab7e99e3"
    ),
}

GUARD_TABLES: Final[tuple[str, ...]] = tuple(
    sorted({guard.table for guard in GUARD_TRIGGERS.values()})
)
