"""Phase 4 persistence foundation: the generic audit_events table.

The only schema Phase 4 adds (SLICE1_DATA_MODEL §16–§17,
IMPLEMENTATION_ROADMAP Phase 4): the append-only `audit_events` table
recording master-data and business-demand changes — WorkOrder,
WorkOrderDemand, and PartNumber. Every other table and column the
Phase 4 slice uses (work_orders, work_order_demands, part_numbers,
route templates and snapshots, quantity_flows, part_movements) already
exists from the Phase 3 migration `0002_phase3_domain`, and the
environment configuration from `0003_phase35_environment`.

Deliberate shape decisions (SLICE1_DATA_MODEL §16):

- `entity_id` is polymorphic text with **no foreign key**: for
  WorkOrder/WorkOrderDemand it is the internal PK, for PartNumber the
  canonical PN string (the master's natural key). Integrity is
  guaranteed by writing the audit row in the same transaction as the
  audited change — a transaction protocol owned by the Application
  layer (Phase 4 workflows), never by this schema.
- `actor_reference` stays a nullable, reference-free value until
  authentication exists (Phase 14) — no user table is invented here.
- Rows are descriptive history for display and accountability only —
  never replayed to build state. This is deliberately not an
  event-sourcing framework, and production release writes no row here:
  the `RECEIVED` PartMovement is the production audit record.

Deliberate exclusions (later phases own them): no
`part_movements.station_id` (Phase 5), no
`quantity_flows.current_machine_id`, `parent_flow_id`, or Movement
machine/worker/session columns (Phases 6–8), no `movement_reason` or
`reverses_movement_id` (Phase 9), no `work_orders.completed_at`
(Phase 10), no `areas.worker_identification_mode` (Phase 7), no
`route_steps.preferred_machine_id`, no Worker/User/ScanSession tables
(Phases 13/14), and no movement-type widening of any kind.

Revision ID: 0004_phase4_audit
Revises: 0003_phase35_environment
Create Date: 2026-08-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_phase4_audit"
down_revision: str | None = "0003_phase35_environment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORBID_MUTATION_FUNCTION = """
CREATE FUNCTION partflow_audit_events_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP;
END;
$$;
"""

# Statement-level so it also covers TRUNCATE and fires even for
# zero-row UPDATE/DELETE statements (same pattern as part_movements and
# machine_lifecycle_events).
_FORBID_MUTATION_TRIGGER = """
CREATE TRIGGER trg_audit_events_forbid_mutation
BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION partflow_audit_events_forbid_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        # Polymorphic by design — no FK (SLICE1_DATA_MODEL §16).
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("actor_reference", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        # NULL before_data for creation events; snapshots of the
        # audited fields, never replayed to build state.
        sa.Column("before_data", JSONB(), nullable=True),
        sa.Column("after_data", JSONB(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        # Both vocabularies widen additively in later phases; Slice 1
        # records exactly creation and edit of exactly the three
        # master-data/business-demand entities — never production
        # activity (PartMovement is the production audit record).
        sa.CheckConstraint(
            "event_type IN ('CREATED', 'UPDATED')", name=op.f("ck_audit_events_event_type")
        ),
        sa.CheckConstraint(
            "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber')",
            name=op.f("ck_audit_events_entity_type"),
        ),
    )
    # Per-entity history in write order (SLICE1_DATA_MODEL §17).
    op.create_index(
        "ix_audit_events_entity_type_entity_id_id",
        "audit_events",
        ["entity_type", "entity_id", "id"],
    )

    # Append-only enforcement in PostgreSQL itself, not application
    # convention: any UPDATE, DELETE, or TRUNCATE statement raises.
    op.execute(_FORBID_MUTATION_FUNCTION)
    op.execute(_FORBID_MUTATION_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_audit_events_forbid_mutation ON audit_events;")
    op.execute("DROP FUNCTION partflow_audit_events_forbid_mutation();")
    op.drop_table("audit_events")
