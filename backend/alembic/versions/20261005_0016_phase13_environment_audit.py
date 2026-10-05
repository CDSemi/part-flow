"""Phase 13 configuration audit: the environment entities join the audit vocabulary.

PROJECT_PROFILE §28 lists "administrative configuration changes" in the
complete audit trail, and the Phase 3.5 environment writes (Departments,
Areas, Operations, Scan Stations and the Machine Asset Tag format) never
wrote `audit_events`. Phase 13 audits them (PLAN CD2/OD-12): every
effective create or update appends one `CREATED`/`UPDATED` row in its
own transaction. This revision only widens the closed entity vocabulary
the database guards: `ck_audit_events_entity_type` gains `Department`,
`Area`, `Operation`, `ScanStation` and `MachineAssetTagConfig`.

Deliberate non-changes: the event CHECK is unchanged (environment writes
are creations and edits; deactivation is an edit of `is_active`; nothing
is hard-deleted); no index (the existing `(entity_type, entity_id, id)`
index serves per-entity history); no trigger and no table; no backfill —
historical configuration has no truthful creation record, so history
starts at the first audited write, whose `before_data` records the state
found. Machine stays outside `audit_events` (`machine_lifecycle_events`
owns its history).

The downgrade REFUSES — it never deletes audit history: PostgreSQL
re-validates the re-created narrower CHECK against existing rows, so any
environment audit row makes it fail loudly; `alembic/env.py` runs in one
transaction, so the database then stays at this revision.

Revision ID: 0016_phase13_environment_audit
Revises: 0015_phase13_badge_check
Create Date: 2026-10-05

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_phase13_environment_audit"
down_revision: str | None = "0015_phase13_badge_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUDIT = "audit_events"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"

# Self-contained on purpose (a migration never imports the mutable
# model module). The first literal is exactly what 0014 left (0015
# does not touch `audit_events`); the downgrade restores it.
_WORKERS_ENTITY_TYPES = "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker')"
_ENVIRONMENT_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig')"
)


def upgrade() -> None:
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _ENVIRONMENT_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. PostgreSQL re-validates the narrower CHECK against
    # existing rows, so any environment audit row makes it fail loudly
    # and env.py's single transaction keeps the database at this revision.
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _WORKERS_ENTITY_TYPES)
