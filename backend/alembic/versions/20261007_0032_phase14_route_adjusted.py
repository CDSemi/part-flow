"""Phase 14: the authorized AssignedRoute adjustment (ROUTE_ADJUSTED).

Exactly the additive schema slice 6 needs (IMPLEMENTATION_ROADMAP
Phase 14; PROJECT_PROFILE §8.10, §17; owner decision OD-P11):

- the audit vocabulary: event type `ROUTE_ADJUSTED` and entity type
  `AssignedRoute` — an authorized, reasoned change of the future steps
  of one PLANNED flow's own AssignedRoute, audited with the complete
  previous and new step lists and the signed-in User; never a
  PartMovement (it moves no quantity). Both CHECKs are re-created —
  metadata-only on the append-only table; its trigger is untouched;
- `uq_audit_events_route_adjustment_device_event_id` — the adjustment's
  single audit row is its idempotency record, so the index is UNIQUE:
  a `device_event_id` names one adjustment even when two flows race
  with the same id. The expression is the JSONB SUBSCRIPT form the
  application emits (`models.ROUTE_ADJUSTMENT_DEVICE_EVENT_ID`), written
  as raw SQL (the 0013 precedent); partial on AssignedRoute rows;
- `ix_part_movements_assigned_route_step_id` — the FK's RI check when
  an adjustment deletes an unreferenced future step, and the "is this
  step referenced" boundary of the edit. DDL does not fire the
  append-only statement trigger (UPDATE/DELETE/TRUNCATE only);
- `trg_assigned_route_steps_forbid_update` — past steps are immutable:
  no UPDATE of `assigned_route_steps` ever (statement-level, so it also
  fires for zero-row statements); a step a Movement references is never
  deleted (FK NO ACTION). DELETE stays allowed for the unreferenced
  future steps an adjustment replaces.

The downgrade REFUSES while any AssignedRoute adjustment is recorded:
the audit rows hold the only copy of the steps an adjustment replaced,
and it never drops them; `alembic/env.py` runs in one transaction, so
the database then stays at this revision. Run it only against
disposable development and test databases.

Revision ID: 0032_phase14_route_adjusted
Revises: 0031_phase14_beyond_demand
Create Date: 2026-10-07

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_phase14_route_adjusted"
down_revision: str | None = "0031_phase14_beyond_demand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py counterpart.
_AUDIT = "audit_events"
_EVENT_TYPE_CHECK = "ck_audit_events_event_type"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
# Exactly what 0014 left (no later revision touched it; the downgrade
# restores it).
_PREVIOUS_EVENT_TYPES = "event_type IN ('CREATED', 'UPDATED', 'DELETED')"
_ROUTE_EVENT_TYPES = "event_type IN ('CREATED', 'UPDATED', 'DELETED', 'ROUTE_ADJUSTED')"
# Exactly what 0030 left (0031 did not touch it; the downgrade restores it).
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice')"
)
_ROUTE_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate', 'User', 'Role',"
    " 'ScanStationDevice', 'AssignedRoute')"
)
_ADJUSTMENT_INDEX = "uq_audit_events_route_adjustment_device_event_id"
_ADJUSTMENT_WHERE_SQL = "entity_type = 'AssignedRoute'"
# Raw SQL so the stored expression is byte-identical to the one the
# application emits — the JSONB SUBSCRIPT form (0013 precedent);
# op.create_index() would re-render it, and the `->` operator form is a
# different expression node to the planner.
_CREATE_ADJUSTMENT_INDEX = f"""
CREATE UNIQUE INDEX {_ADJUSTMENT_INDEX}
ON audit_events ((metadata['route_adjustment'] ->> 'device_event_id'))
WHERE {_ADJUSTMENT_WHERE_SQL}
"""
_STEP_REFERENCE_INDEX = "ix_part_movements_assigned_route_step_id"
_FORBID_UPDATE_FUNCTION = "partflow_assigned_route_steps_forbid_update"
_FORBID_UPDATE_TRIGGER = "trg_assigned_route_steps_forbid_update"
_FORBID_UPDATE_MESSAGE = (
    "assigned_route_steps rows are never updated: past steps are immutable, and an"
    " adjustment deletes unreferenced future steps and inserts new ones"
)
_CREATE_FORBID_UPDATE_FUNCTION = f"""
CREATE FUNCTION {_FORBID_UPDATE_FUNCTION}() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '{_FORBID_UPDATE_MESSAGE}';
END;
$$;
"""
# Statement-level (the part_movements precedent): it fires even for a
# zero-row UPDATE.
_CREATE_FORBID_UPDATE_TRIGGER = f"""
CREATE TRIGGER {_FORBID_UPDATE_TRIGGER}
BEFORE UPDATE ON assigned_route_steps
FOR EACH STATEMENT EXECUTE FUNCTION {_FORBID_UPDATE_FUNCTION}();
"""


def upgrade() -> None:
    op.drop_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, _ROUTE_EVENT_TYPES)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ROUTE_ENTITY_TYPES)
    op.execute(_CREATE_ADJUSTMENT_INDEX)
    op.create_index(_STEP_REFERENCE_INDEX, "part_movements", ["assigned_route_step_id"])
    op.execute(_CREATE_FORBID_UPDATE_FUNCTION)
    op.execute(_CREATE_FORBID_UPDATE_TRIGGER)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Recorded adjustments (the only copy of the steps
    # they replaced) are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_AUDIT} WHERE entity_type = 'AssignedRoute'"
        "  OR event_type = 'ROUTE_ADJUSTED')"
        " THEN RAISE EXCEPTION 'Assigned Route adjustments are recorded; refusing downgrade';"
        " END IF; END $$;"
    )
    op.execute(f"DROP TRIGGER {_FORBID_UPDATE_TRIGGER} ON assigned_route_steps;")
    op.execute(f"DROP FUNCTION {_FORBID_UPDATE_FUNCTION}();")
    op.drop_index(_STEP_REFERENCE_INDEX, table_name="part_movements")
    op.execute(f"DROP INDEX {_ADJUSTMENT_INDEX}")
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)
    op.drop_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, _PREVIOUS_EVENT_TYPES)
