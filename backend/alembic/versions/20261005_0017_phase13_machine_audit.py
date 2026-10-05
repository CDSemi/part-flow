"""Phase 13 Machine configuration audit: Machine joins the audit vocabulary.

PROJECT_PROFILE §28 lists "administrative configuration changes" in the
complete audit trail, and the Phase 3.5 Machine configuration writes
never wrote `audit_events`. Owner decision S2-F6 (2026-10-05) audits
them with the slice 2 mechanism: every effective Machine creation,
metadata or maintenance-context edit, maintenance start or clear
appends one `CREATED`/`UPDATED` row in its own transaction. This
revision only widens the closed entity vocabulary the database guards:
`ck_audit_events_entity_type` gains `Machine`.

Retirement and reactivation stay recorded in `machine_lifecycle_events`.
An audit row written inside them carries only the configuration delta
they apply (a recorded Save draft; a reactivation's rename, Area move
or cleared maintenance context) and links to the lifecycle event
through `metadata.machine_lifecycle_event_id`, so no lifecycle
transition is ever recorded twice.

Deliberate non-changes: the event CHECK is unchanged (Machines are
created and edited, never hard-deleted); no index (the existing
`(entity_type, entity_id, id)` index serves per-Machine history); no
trigger and no table; no backfill — historical configuration has no
truthful creation record, so history starts at the first audited
write, whose `before_data` records the state found;
`machine_lifecycle_events` is untouched.

The downgrade REFUSES — it never deletes audit history: PostgreSQL
re-validates the re-created narrower CHECK against existing rows, so any
Machine audit row makes it fail loudly; `alembic/env.py` runs in one
transaction, so the database then stays at this revision.

Revision ID: 0017_phase13_machine_audit
Revises: 0016_phase13_environment_audit
Create Date: 2026-10-05

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_phase13_machine_audit"
down_revision: str | None = "0016_phase13_environment_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT = "audit_events"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"

# Self-contained on purpose (a migration never imports the mutable
# model module). The first literal is exactly what 0016 left; the
# downgrade restores it.
_ENVIRONMENT_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig')"
)
_MACHINE_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _MACHINE_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. PostgreSQL re-validates the narrower CHECK against
    # existing rows, so any Machine audit row makes it fail loudly and
    # env.py's single transaction keeps the database at this revision.
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ENVIRONMENT_ENTITY_TYPES)
