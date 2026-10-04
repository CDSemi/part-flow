"""Phase 12 Priority Management: Hot rank constraints and idempotency index.

The Hot list (PROJECT_PROFILE §21 Priority Management; GUI_DESIGN §8)
stores priority in the existing `work_order_demands.priority_rank`
column only — "Hot" means a rank is set, rank 1 is the highest
priority — and keeps the ranked demands at exactly the ranks 1..N
(invariant H1). The Hot list command (app/application/hot_list.py) is
the only writer and always renumbers densely; this migration adds what
PostgreSQL can enforce of that invariant and the index the command's
idempotency lookup needs:

- `ck_work_order_demands_priority_rank_positive`:
  `priority_rank IS NULL OR priority_rank >= 1`;
- `uq_work_order_demands_priority_rank`: one demand per rank, not
  deferrable (the command clears every changed rank before assigning
  the new ones, so it never produces a transient duplicate); NULLs —
  unranked demand — stay distinct;
- `ix_audit_events_hot_list_device_event_id`: the audit rows of a Hot
  list change are its idempotency record, found by the
  `device_event_id` in their `hot_list_change` metadata block. The
  expression is the JSONB **subscript** form SQLAlchemy renders for
  `AuditEvent.metadata_["hot_list_change"]["device_event_id"].astext`
  (the `->` operator form is a different expression node to the
  planner), partial on `entity_type = 'WorkOrderDemand'` like the
  query.

Existing ranks are CHECKED, never rewritten. Ranks written before
Phase 12 that are not exactly 1..N (a rank below 1, a duplicated rank,
or a gap) refuse the upgrade with every offending row named, and
nothing is changed — `alembic/env.py` runs every pending revision in
one transaction, so PostgreSQL's transactional DDL leaves the database
at the revision the upgrade started from (0012, or an earlier one when
several revisions were pending). Normalizing them here would be an
unaudited priority decision: ties are ordered by business dates
(PROJECT_PROFILE §18), and every priority change must be audited
(§28). The owner corrects the ranks explicitly, then upgrades.

Deliberate non-changes: no `is_hot` flag, no priority table, no
Department column on demand, and no audit vocabulary widening (a
priority change is an `UPDATED` `WorkOrderDemand` row).

Revision ID: 0013_phase12_priority
Revises: 0012_phase11_tracking_index
Create Date: 2026-10-04

"""

from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_phase12_priority"
down_revision: str | None = "0012_phase11_tracking_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "work_order_demands"
_CHECK_NAME = "ck_work_order_demands_priority_rank_positive"
_UNIQUE_NAME = "uq_work_order_demands_priority_rank"
_INDEX_NAME = "ix_audit_events_hot_list_device_event_id"

# Written as raw SQL so the stored expression is byte-identical to the
# one the application emits; op.create_index() would re-render it.
_CREATE_INDEX = f"""
CREATE INDEX {_INDEX_NAME}
ON audit_events ((metadata['hot_list_change'] ->> 'device_event_id'))
WHERE entity_type = 'WorkOrderDemand'
"""


def _refuse_invalid_ranks() -> None:
    """Refuse — never rewrite — existing ranks that are not exactly 1..N."""
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT id, priority_rank FROM work_order_demands"
                " WHERE priority_rank IS NOT NULL ORDER BY priority_rank, id"
            )
        )
        .all()
    )
    count = len(rows)
    occurrences = Counter(rank for _, rank in rows)
    # With no rank below 1, no duplicate and none above N, the N ranks
    # are exactly 1..N — so these rows name every violation, gaps
    # included (a gap pushes some rank above N).
    offending = [
        (demand_id, rank)
        for demand_id, rank in rows
        if rank < 1 or occurrences[rank] > 1 or rank > count
    ]
    if not offending:
        return
    listed = ", ".join(
        f"work_order_demands.id {demand_id} (rank {rank})" for demand_id, rank in offending
    )
    raise RuntimeError(
        "Cannot upgrade to 0013_phase12_priority: Hot ranks (work_order_demands.priority_rank)"
        f" must be unique whole numbers 1..N with no gaps, N being the number of ranked"
        f" demands ({count}). Offending rows: {listed}. Nothing was changed — the upgrade"
        " runs in one transaction, so the database stays at the revision it started from."
        " Ranks are priority decisions that must be"
        " audited (PROJECT_PROFILE §28), so they are never renumbered automatically: the"
        " owner must correct these ranks explicitly before upgrading."
    )


def upgrade() -> None:
    _refuse_invalid_ranks()
    op.create_check_constraint(
        op.f(_CHECK_NAME), _TABLE, "priority_rank IS NULL OR priority_rank >= 1"
    )
    op.create_unique_constraint(_UNIQUE_NAME, _TABLE, ["priority_rank"])
    op.execute(_CREATE_INDEX)


def downgrade() -> None:
    op.execute(f"DROP INDEX {_INDEX_NAME}")
    op.drop_constraint(_UNIQUE_NAME, _TABLE, type_="unique")
    op.drop_constraint(op.f(_CHECK_NAME), _TABLE, type_="check")
