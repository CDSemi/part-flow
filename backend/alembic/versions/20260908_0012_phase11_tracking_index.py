"""Phase 11 read-path hardening: index a PN's Movement history by time.

PN Tracking (app/application/tracking.py) reads one Part Number's
immutable Movement history in reverse-chronological order — GUI_DESIGN
§7.2 item 5, `(occurred_at DESC, id DESC)`, with keyset paging on the
same order — and its Scrap history is the same read restricted to
`SCRAPPED` rows. Without an index that is a scan of every Movement of
the PN followed by a sort, and the history is retained for years
(PROJECT_PROFILE §28), so its cost grows with total production history
rather than with the page being read.

This migration adds exactly one composite index matching that access
path and nothing else: `(part_number, occurred_at, id)` serves the
per-PN ordered read in either direction (a backward index scan for
DESC) and the keyset predicate `(occurred_at, id) < (…)`.

Deliberate non-changes: no column, no constraint, no table, no
movement-type widening — a read-path index only.

Revision ID: 0012_phase11_tracking_index
Revises: 0011_phase10_stock_allocation
Create Date: 2026-09-08

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_phase11_tracking_index"
down_revision: str | None = "0011_phase10_stock_allocation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_part_movements_part_number_occurred_at_id"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME, "part_movements", ["part_number", "occurred_at", "id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="part_movements")
