"""Phase 4 read-path hardening: index the RECEIVED demand context.

`released_quantities` (app/application/production_release.py) derives a
demand's released quantity from the immutable `RECEIVED` metadata
context — no stored counter, no second source of truth, no
Movement → WorkOrderDemand foreign key (SLICE1_DATA_MODEL §3/§8a/§11).
That derivation is the hot read path of Phase 4: it runs on every Work
Order list read and every Work Order Details read. Without an index it
is a sequential scan of the whole append-only `part_movements` history,
which is retained for years (PROJECT_PROFILE §28), so its cost grows
with total production history rather than with the Work Order being
read.

This migration adds exactly one partial expression index matching that
query and nothing else:

- the expression is the one the application actually emits — the JSONB
  **subscript** form SQLAlchemy renders for
  `PartMovement.metadata_["context"]["work_order_demand_id"].as_integer()`,
  i.e. `CAST(metadata['context'] ->> 'work_order_demand_id' AS integer)`.
  The `->` operator form is a different expression node to the planner
  and would NOT match, so the subscript form is deliberate;
- the predicate is `movement_type = 'RECEIVED'`, the same restriction
  the query carries: only a `RECEIVED` Movement is release evidence,
  and later movement types may carry demand context for other reasons.

Deliberate non-changes: no `work_order_demand_id` column, no foreign
key from Movement to WorkOrderDemand, no stored released counter, no
table, no movement-type widening, and no Phase 5+ column of any kind.

Revision ID: 0005_phase4_release_index
Revises: 0004_phase4_audit
Create Date: 2026-08-24

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_phase4_release_index"
down_revision: str | None = "0004_phase4_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_part_movements_received_demand_context"

# Written as raw SQL so the stored expression is byte-identical to the
# one the application emits; op.create_index() would re-render it.
_CREATE_INDEX = f"""
CREATE INDEX {_INDEX_NAME}
ON part_movements ((CAST(metadata['context'] ->> 'work_order_demand_id' AS INTEGER)))
WHERE movement_type = 'RECEIVED'
"""


def upgrade() -> None:
    op.execute(_CREATE_INDEX)


def downgrade() -> None:
    op.execute(f"DROP INDEX {_INDEX_NAME}")
