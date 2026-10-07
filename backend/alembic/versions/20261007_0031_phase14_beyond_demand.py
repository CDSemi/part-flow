"""Phase 14: the authorized beyond-demand allocation correction.

Exactly the additive schema slice 5 needs (IMPLEMENTATION_ROADMAP
Phase 14; PROJECT_PROFILE §8.12; owner decisions OD-P12/P13):

- `work_order_allocations.exceeds_demand` (boolean, NOT NULL, default
  false) — true only on a row recorded by the authorized beyond-demand
  correction (`allocations.allocate_beyond_demand`). Neither
  `is_manual_override` (a difference from the suggestion) nor
  `source = MANAGEMENT` (routine Management allocation) can carry that
  fact, and the command kind in the JSONB metadata is not CHECK-able.
  Rows are append-only; the flag records the intent at command time and
  is never recomputed. Every existing row reads false — truthfully, none
  was a correction. A constant-default ADD COLUMN is metadata-only; the
  row-level raise-on-write trigger does not fire on DDL and stays
  untouched;
- `ck_work_order_allocations_exceeds_demand_shape` — a correction is
  Management-only, reasoned, never a reversal, never attributed to a
  Scan Station or a Worker, and always recorded with a signed-in User.

No index (no reader filters on the flag) and no data statement.

The downgrade REFUSES while any correction row exists — it never drops
recorded corrections; `alembic/env.py` runs in one transaction, so the
database then stays at this revision. Run it only against disposable
development and test databases.

Revision ID: 0031_phase14_beyond_demand
Revises: 0030_phase14_station_devices
Create Date: 2026-10-07

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031_phase14_beyond_demand"
down_revision: str | None = "0030_phase14_station_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts _EXCEEDS_DEMAND_SQL equals
# models.ALLOCATION_EXCEEDS_DEMAND_SQL.
_TABLE = "work_order_allocations"
_COLUMN = "exceeds_demand"
_CHECK = "ck_work_order_allocations_exceeds_demand_shape"
_EXCEEDS_DEMAND_SQL = (
    "NOT exceeds_demand OR (source = 'MANAGEMENT' AND allocation_reason IS NOT NULL"
    " AND reverses_allocation_id IS NULL AND station_id IS NULL"
    " AND allocated_by_worker_id IS NULL AND actor_user_id IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(op.f(_CHECK), _TABLE, _EXCEEDS_DEMAND_SQL)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Recorded corrections are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_TABLE} WHERE {_COLUMN})"
        " THEN RAISE EXCEPTION 'Beyond-demand allocation corrections exist; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_constraint(op.f(_CHECK), _TABLE, type_="check")
    op.drop_column(_TABLE, _COLUMN)
