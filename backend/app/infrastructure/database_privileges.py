"""Database-role privilege constants (Phase 16 slice 4; CD5, OD-16-08).

One classification of every relation in schema ``public`` at the code
head, the exact table privileges of the application database role per
class, the tables the maintenance database role may read, and the guard
triggers and guard functions the migrations create. ``apply-grants``
derives every grant from these constants and reconcile check (h)
compares the database with them. Tests pin them to the migrated schema:
a migration that adds a table classifies it in the same commit, and one
that adds or changes a guard updates ``GUARD_TRIGGERS`` /
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
    # row lock needs UPDATE too. The row trigger refuses every other change.
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
    "departments": TableClass.ORDINARY,
    "machine_asset_tag_config": TableClass.ORDINARY,
    "machine_lifecycle_events": TableClass.APPEND_ONLY,
    "machines": TableClass.ORDINARY,
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
    "user_sessions": TableClass.ORDINARY,
    "users": TableClass.ORDINARY,
    "work_order_allocations": TableClass.APPEND_ONLY,
    "work_order_demands": TableClass.ORDINARY,
    "work_orders": TableClass.ORDINARY,
    "worker_sessions": TableClass.GUARDED_UPDATE,
    "workers": TableClass.ORDINARY,
}

#: SELECT only for the maintenance database role (CD5): Movements, flows,
#: lineage, the CD9 context tables, Users/roles/role permissions, the
#: application policy and ``alembic_version`` for the archive manifest.
#: Never credentials, sign-in sessions or Scan Station devices.
MAINTENANCE_SELECT: Final[frozenset[str]] = frozenset(
    {
        "alembic_version",
        "application_policy",
        "areas",
        "machines",
        "operations",
        "part_movements",
        "quantity_flow_lineage",
        "quantity_flows",
        "role_permissions",
        "roles",
        "scan_stations",
        "users",
        "worker_sessions",
        "workers",
    }
)


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
    "trg_audit_events_forbid_mutation": _statement_guard("audit_events", _ALL_EVENTS),
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
    "trg_part_movements_forbid_mutation": _statement_guard("part_movements", _ALL_EVENTS),
    "trg_quantity_flow_lineage_forbid_mutation": _statement_guard(
        "quantity_flow_lineage", _ALL_EVENTS
    ),
    "trg_work_order_allocations_forbid_mutation": _statement_guard(
        "work_order_allocations", _ALL_EVENTS
    ),
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
    "partflow_machine_lifecycle_events_forbid_mutation": (
        "6a29a65f2e3ddee67b0f3bbda429d72b7fed6ff72564bc6bd7466724109f52b1"
    ),
    "partflow_machines_forbid_area_change": (
        "acb7c8a5fd5551f2e8e9b0d80d19b6ad0405355caa323cb391808ea3e66e73ee"
    ),
    "partflow_machines_forbid_asset_tag_change": (
        "2cf004df5fb36f07933ea264bfef2dfbc1da993d25d70a49dbed5c79bde6a6fe"
    ),
    "partflow_part_movements_forbid_mutation": (
        "e66962957036fd21cd17cc9cc002450969508d29165811845626d87317dd6764"
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
