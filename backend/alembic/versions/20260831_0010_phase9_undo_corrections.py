"""Phase 9 persistence: Undo, corrections, and auditable quantity events.

Exactly the additive schema the correction workflows need
(IMPLEMENTATION_ROADMAP Phase 9; PROJECT_PROFILE §8.11, §11, §14, §16;
SLICE1_DATA_MODEL §18):

- the movement-type CHECK widens with `SCRAPPED`, `QUANTITY_ADJUSTED`
  and `REVERSED`, and the per-type shape CHECK gains their branches:
  a `SCRAPPED` stays in ONE Area at a Scan Station and may name the
  source Machine the quantity left (never a destination Machine); a
  `QUANTITY_ADJUSTED` introduces quantity like a `RECEIVED` (no source
  Area, no Machine) but is always scan-driven (Station NOT NULL); a
  `REVERSED` records the compensating motion of a command-level Undo
  at a Station (its from/to pair may cross Areas — reversing a
  `TRANSFERRED` — or stay in one) and never re-states a Machine — the
  restored state is derived by EXCLUDING the reversed pair from every
  derivation, never read off the compensating row;
- three new `part_movements` columns: `movement_reason` (the typed
  movement intent — only `REPAIR`, only on a `TRANSFERRED`), `reason`
  (the free-text explanation — mandatory for Scrap, quantity
  adjustments and every Repair, CHECK-enforced) and
  `reverses_movement_id` (the original Movement a `REVERSED` row
  compensates — set exactly on a `REVERSED`, FK to `part_movements`,
  and UNIQUE: at most one reversal per original, so a command can
  never be undone twice, including by a race between two concurrent
  Undo submissions);
- the QuantityFlow lifecycle widens with `SCRAPPED` (the flow's
  quantity was removed from active production by a confirmed Scrap)
  and `REVERSED` (the command that created the flow was undone); the
  `closed_at` agreement CHECK is unchanged and covers them.

Movement rows stay append-only (trigger of migration 0002): an Undo
never deletes or edits the original rows — it appends compensating
`REVERSED` rows under its own `device_event_id`.

Deliberate exclusions (later phases own them): no `worker_id` /
`scan_session_id` (Worker sessions, Phase 13), no Undo-reason or
permission configuration (Phase 13/14 — no authorization is simulated),
no `STOCKED` (Phase 10), no `ROUTE_ADJUSTED` /
`ROUTE_DEVIATION_CONFIRMED` (route editing).

Revision ID: 0010_phase9_undo_corrections
Revises: 0009_phase8_split_merge
Create Date: 2026-08-31

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_phase9_undo_corrections"
down_revision: str | None = "0009_phase8_split_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVEMENTS = "part_movements"
_FLOWS = "quantity_flows"
_TYPE_CHECK = "ck_part_movements_movement_type"
_SHAPE_CHECK = "ck_part_movements_movement_shape"
_STATUS_CHECK = "ck_quantity_flows_status"
_MOVEMENT_REASON_CHECK = "ck_part_movements_movement_reason"
_REASON_REQUIRED_CHECK = "ck_part_movements_reason_required"
_REVERSES_CHECK = "ck_part_movements_reverses_shape"
_REVERSES_UNIQUE = "uq_part_movements_reverses_movement_id"
_REVERSES_FK = "fk_part_movements_reverses_movement_id_part_movements"

_PHASE8_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED', 'SPLIT', 'MERGED')"
)
_PHASE9_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED', 'SPLIT', 'MERGED',"
    " 'SCRAPPED', 'QUANTITY_ADJUSTED', 'REVERSED')"
)

_PHASE8_STATUSES = "status IN ('ACTIVE', 'SPLIT', 'MERGED')"
_PHASE9_STATUSES = "status IN ('ACTIVE', 'SPLIT', 'MERGED', 'SCRAPPED', 'REVERSED')"

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts `_PHASE9_SHAPE` equals
# `models.MOVEMENT_SHAPE_SQL` and `_PHASE8_SHAPE` equals the previous
# migration's expression.
_PHASE8_SHAPE = (
    "(movement_type = 'RECEIVED' AND from_area_id IS NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'TRANSFERRED' AND from_area_id IS NOT NULL"
    " AND from_area_id <> to_area_id AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'ASSIGNED_TO_MACHINE'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NOT NULL)"
    " OR (movement_type = 'RELEASED_FROM_MACHINE'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NOT NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'AREA_COMPLETED'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL AND destination_machine_id IS NULL)"
    " OR (movement_type IN ('SPLIT', 'MERGED')"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
)
_PHASE9_SHAPE = (
    "(movement_type = 'RECEIVED' AND from_area_id IS NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'TRANSFERRED' AND from_area_id IS NOT NULL"
    " AND from_area_id <> to_area_id AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'ASSIGNED_TO_MACHINE'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NOT NULL)"
    " OR (movement_type = 'RELEASED_FROM_MACHINE'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NOT NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'AREA_COMPLETED'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL AND destination_machine_id IS NULL)"
    " OR (movement_type IN ('SPLIT', 'MERGED')"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'SCRAPPED'"
    " AND from_area_id IS NOT NULL AND from_area_id = to_area_id"
    " AND station_id IS NOT NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'QUANTITY_ADJUSTED' AND from_area_id IS NULL"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
    " OR (movement_type = 'REVERSED' AND from_area_id IS NOT NULL"
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
)

_MOVEMENT_REASON_SQL = (
    "movement_reason IS NULL OR (movement_reason = 'REPAIR' AND movement_type = 'TRANSFERRED')"
)
_REASON_REQUIRED_SQL = (
    "reason IS NOT NULL"
    " OR (movement_type NOT IN ('SCRAPPED', 'QUANTITY_ADJUSTED')"
    " AND movement_reason IS NULL)"
)
_REVERSES_SQL = "(movement_type = 'REVERSED') = (reverses_movement_id IS NOT NULL)"


def upgrade() -> None:
    op.add_column(_MOVEMENTS, sa.Column("movement_reason", sa.Text(), nullable=True))
    op.add_column(_MOVEMENTS, sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(_MOVEMENTS, sa.Column("reverses_movement_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(_REVERSES_FK, _MOVEMENTS, _MOVEMENTS, ["reverses_movement_id"], ["id"])
    # PostgreSQL UNIQUE ignores NULLs: every non-REVERSED row stays
    # valid, and each original Movement can be reversed at most once.
    op.create_unique_constraint(_REVERSES_UNIQUE, _MOVEMENTS, ["reverses_movement_id"])

    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE9_TYPES)
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE9_SHAPE)
    op.create_check_constraint(op.f(_MOVEMENT_REASON_CHECK), _MOVEMENTS, _MOVEMENT_REASON_SQL)
    op.create_check_constraint(op.f(_REASON_REQUIRED_CHECK), _MOVEMENTS, _REASON_REQUIRED_SQL)
    op.create_check_constraint(op.f(_REVERSES_CHECK), _MOVEMENTS, _REVERSES_SQL)

    op.drop_constraint(op.f(_STATUS_CHECK), _FLOWS, type_="check")
    op.create_check_constraint(op.f(_STATUS_CHECK), _FLOWS, _PHASE9_STATUSES)


def downgrade() -> None:
    # Destructive by nature: a database holding Phase 9 history
    # (SCRAPPED / QUANTITY_ADJUSTED / REVERSED Movements, scrapped or
    # reversed flows, Repair intents, recorded reasons) cannot satisfy
    # the re-created Phase 8 checks, which PostgreSQL re-validates
    # against existing rows — so the downgrade fails loudly BEFORE any
    # column is dropped instead of dropping history — run it only
    # against disposable development/test databases.
    op.drop_constraint(op.f(_STATUS_CHECK), _FLOWS, type_="check")
    op.create_check_constraint(op.f(_STATUS_CHECK), _FLOWS, _PHASE8_STATUSES)

    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE8_SHAPE)
    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE8_TYPES)

    op.drop_constraint(op.f(_REVERSES_CHECK), _MOVEMENTS, type_="check")
    op.drop_constraint(op.f(_REASON_REQUIRED_CHECK), _MOVEMENTS, type_="check")
    op.drop_constraint(op.f(_MOVEMENT_REASON_CHECK), _MOVEMENTS, type_="check")
    op.drop_constraint(_REVERSES_UNIQUE, _MOVEMENTS, type_="unique")
    op.drop_constraint(_REVERSES_FK, _MOVEMENTS, type_="foreignkey")
    op.drop_column(_MOVEMENTS, "reverses_movement_id")
    op.drop_column(_MOVEMENTS, "reason")
    op.drop_column(_MOVEMENTS, "movement_reason")
