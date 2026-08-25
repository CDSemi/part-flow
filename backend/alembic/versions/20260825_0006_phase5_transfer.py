"""Phase 5 persistence: the Scan Station transfer Movement.

Exactly the additive schema the Scan Station transfer to an Area queue
needs (IMPLEMENTATION_ROADMAP Phase 5; SLICE1_DATA_MODEL §18):

- widen the movement-type vocabulary from `RECEIVED` to
  `RECEIVED` + `TRANSFERRED`;
- add `part_movements.station_id` — the canonical audit column that
  records the stable Scan Station identity of a scan-driven Movement
  (PROJECT_PROFILE §15 Scan Station Persistence), a nullable foreign
  key to `scan_stations.station_id` (Phase 3.5). It is NULL on the
  Management-initiated `RECEIVED` release, so existing rows need no
  backfill;
- replace the single-type `ck_part_movements_received_shape` CHECK by
  the per-type `ck_part_movements_movement_shape`: `RECEIVED` still has
  no source Area, and `TRANSFERRED` moves quantity between two
  DIFFERENT Areas and always carries its Scan Station.

Deliberate exclusions (later phases own them): no
`quantity_flows.current_machine_id`, `parent_flow_id`, or Movement
machine/worker/session columns (Phases 6–8), no `movement_reason` or
`reverses_movement_id` (Phase 9), no `AREA_COMPLETED`, `SPLIT`,
`MERGED`, `REVERSED`, `STOCKED` or any other movement type, no new
table, and no partial-quantity support of any kind (Phase 8 SPLIT).

The immutability trigger of migration 0002 is untouched: `TRANSFERRED`
rows are append-only exactly like `RECEIVED` rows.

Revision ID: 0006_phase5_transfer
Revises: 0005_phase4_release_index
Create Date: 2026-08-25

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_phase5_transfer"
down_revision: str | None = "0005_phase4_release_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "part_movements"
_TYPE_CHECK = "ck_part_movements_movement_type"
_PHASE3_SHAPE_CHECK = "ck_part_movements_received_shape"
_PHASE5_SHAPE_CHECK = "ck_part_movements_movement_shape"
_STATION_FK = "fk_part_movements_station_id_scan_stations"

_PHASE3_TYPES = "movement_type IN ('RECEIVED')"
_PHASE5_TYPES = "movement_type IN ('RECEIVED', 'TRANSFERRED')"
_PHASE3_SHAPE = "movement_type = 'RECEIVED' AND from_area_id IS NULL"
# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts it equals
# `models.MOVEMENT_SHAPE_SQL`.
_PHASE5_SHAPE = (
    "(movement_type = 'RECEIVED' AND from_area_id IS NULL)"
    " OR (movement_type = 'TRANSFERRED' AND from_area_id IS NOT NULL"
    " AND from_area_id <> to_area_id AND station_id IS NOT NULL)"
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("station_id", sa.Text(), nullable=True))
    op.create_foreign_key(_STATION_FK, _TABLE, "scan_stations", ["station_id"], ["station_id"])
    op.drop_constraint(op.f(_TYPE_CHECK), _TABLE, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _TABLE, _PHASE5_TYPES)
    op.drop_constraint(op.f(_PHASE3_SHAPE_CHECK), _TABLE, type_="check")
    op.create_check_constraint(op.f(_PHASE5_SHAPE_CHECK), _TABLE, _PHASE5_SHAPE)


def downgrade() -> None:
    # Destructive by nature: a database holding TRANSFERRED history
    # cannot satisfy the Phase 4 checks. Alembic re-validates the
    # narrowed CHECKs against existing rows, so the downgrade fails
    # loudly instead of dropping history — run it only against
    # disposable development/test databases.
    op.drop_constraint(op.f(_PHASE5_SHAPE_CHECK), _TABLE, type_="check")
    op.create_check_constraint(op.f(_PHASE3_SHAPE_CHECK), _TABLE, _PHASE3_SHAPE)
    op.drop_constraint(op.f(_TYPE_CHECK), _TABLE, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _TABLE, _PHASE3_TYPES)
    op.drop_constraint(_STATION_FK, _TABLE, type_="foreignkey")
    op.drop_column(_TABLE, "station_id")
