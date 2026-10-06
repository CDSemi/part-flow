"""Phase 13: Planned Routes management — preferred Machines, usage index, audit.

Exactly the additive schema slice 8 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §8.8–§8.10, §21, §28; owner decision OD-11):

- `route_steps.preferred_machine_id` — the canonical advisory step
  attribute (PROJECT_PROFILE §8.9), referencing the stable Machine id
  (FK `fk_route_steps_preferred_machine_id_machines`, NO ACTION;
  Machines are retired, never deleted). No composite FK with the step's
  Area: a Machine may move Areas on reactivation, so a stored preference
  may legitimately become stale — the Area match is an Application rule
  at save time only;
- `assigned_route_steps.preferred_machine_id` — the snapshot copy
  (OD-11), a plain nullable integer with NO foreign key (S8-OD20): the
  snapshot INSERT runs after release / receipt lock their starting Area
  and after split / merge lock a Machine, so an FK check there would
  take FOR KEY SHARE on Machine rows in an order production commands
  never use. The value is always copied from the FK-checked template
  column (or another snapshot), so it cannot dangle;
- `ix_assigned_routes_source_route_template_id` — the usage list, the
  per-template usage counts, the ever-used check and the FK check of a
  template delete are all per template;
- `ck_audit_events_entity_type` gains `RouteTemplate`.

Deliberate non-changes: no index on either `preferred_machine_id` (no
reader filters by it; Machines are never deleted), no CHECK on
`expected_duration` (positivity is an Application rule, like the
Operation default), no backfill — legacy steps without an Operation stay
as they are and must be completed on their next save (OD-11).

The downgrade REFUSES — it never deletes data: any stored preferred
Machine raises, and PostgreSQL re-validates the re-created narrower
entity CHECK, so any `RouteTemplate` audit row makes it fail loudly;
`alembic/env.py` runs in one transaction, so the database then stays at
this revision. Run it only against disposable development and test
databases.

Revision ID: 0024_phase13_planned_routes
Revises: 0023_phase13_part_number_master
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024_phase13_planned_routes"
down_revision: str | None = "0023_phase13_part_number_master"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROUTE_STEPS = "route_steps"
_ASSIGNED_ROUTE_STEPS = "assigned_route_steps"
_ASSIGNED_ROUTES = "assigned_routes"
_MACHINES = "machines"
_AUDIT = "audit_events"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
_PREFERRED_MACHINE_FK = "fk_route_steps_preferred_machine_id_machines"
_SOURCE_TEMPLATE_INDEX = "ix_assigned_routes_source_route_template_id"

# Self-contained literals (a migration never imports the mutable model
# module). The first is exactly what 0020 left (the downgrade restores
# it); the second appends RouteTemplate.
_PREVIOUS_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy')"
)
_ROUTE_TEMPLATE_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy', 'RouteTemplate')"
)


def upgrade() -> None:
    op.add_column(_ROUTE_STEPS, sa.Column("preferred_machine_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        _PREFERRED_MACHINE_FK, _ROUTE_STEPS, _MACHINES, ["preferred_machine_id"], ["id"]
    )
    op.add_column(
        _ASSIGNED_ROUTE_STEPS, sa.Column("preferred_machine_id", sa.Integer(), nullable=True)
    )
    op.create_index(_SOURCE_TEMPLATE_INDEX, _ASSIGNED_ROUTES, ["source_route_template_id"])

    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ROUTE_TEMPLATE_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Preferred Machines are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM route_steps WHERE preferred_machine_id IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM assigned_route_steps WHERE preferred_machine_id IS NOT NULL)"
        " THEN RAISE EXCEPTION 'Preferred Machines are recorded on Planned Route steps or"
        " Assigned Route snapshots; refusing to drop them';"
        " END IF; END $$;"
    )
    # PostgreSQL re-validates the narrower CHECK against existing rows,
    # so a RouteTemplate audit row makes it fail loudly.
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PREVIOUS_ENTITY_TYPES)

    op.drop_index(_SOURCE_TEMPLATE_INDEX, table_name=_ASSIGNED_ROUTES)
    op.drop_column(_ASSIGNED_ROUTE_STEPS, "preferred_machine_id")
    op.drop_constraint(_PREFERRED_MACHINE_FK, _ROUTE_STEPS, type_="foreignkey")
    op.drop_column(_ROUTE_STEPS, "preferred_machine_id")
