"""Phase 7 persistence: direct Area processing (Areas without Machines).

Exactly the schema change the direct-processing workflows need
(IMPLEMENTATION_ROADMAP Phase 7; PROJECT_PROFILE §7 Area Completion,
§8.11, §12 Area Without Machines; SLICE1_DATA_MODEL §18): the
`AREA_COMPLETED` branch of the per-type Movement shape CHECK widens so
a completion may carry NO source Machine. An Area without Machines
directly owns and processes the quantity it receives (Machine NULL,
derived holding state `PROCESSING`), and its manual DONE — or the
implicit completion when directly processing quantity transfers to
another Area (`AREA_COMPLETED` + `TRANSFERRED`, one atomic command
under one `device_event_id`, exactly like the `ON_MACHINE` case) —
records an `AREA_COMPLETED` with `source_machine_id IS NULL`. A
completion of Machine-assigned quantity keeps recording its source
Machine; a completion never carries a destination Machine.

Nothing else changes: no column, no table, no new movement type, no
`areas` configuration column — exactly two Area modes exist and the
mode follows from the Area's Machines (no `machine_assignment_mode`),
and `areas.worker_identification_mode` stays with Worker identity.
The holding state is derived (never stored), so `PROCESSING` needs no
column. Every row remains append-only (trigger of migration 0002).

Deliberate exclusions (later phases own them): no `parent_flow_id`,
`SPLIT`, `MERGED` or partial-quantity support (Phase 8), no
`worker_id` / `scan_session_id`, no `movement_reason` /
`reverses_movement_id` / `REVERSED` (Phase 9), no `STOCKED` (Phase 10).

Revision ID: 0008_phase7_direct_processing
Revises: 0007_phase6_machine_assignment
Create Date: 2026-08-26

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_phase7_direct_processing"
down_revision: str | None = "0007_phase6_machine_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVEMENTS = "part_movements"
_SHAPE_CHECK = "ck_part_movements_movement_shape"

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts `_PHASE7_SHAPE` equals
# `models.MOVEMENT_SHAPE_SQL` and `_PHASE6_SHAPE` equals the previous
# migration's expression.
_PHASE6_SHAPE = (
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
    " AND station_id IS NOT NULL"
    " AND source_machine_id IS NOT NULL AND destination_machine_id IS NULL)"
)
_PHASE7_SHAPE = (
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
)


def upgrade() -> None:
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE7_SHAPE)


def downgrade() -> None:
    # Destructive by nature: a database holding a direct-processing
    # completion (an AREA_COMPLETED without a Machine) cannot satisfy
    # the Phase 6 shape (re-validated against existing rows), so the
    # downgrade fails loudly instead of dropping history — run it only
    # against disposable development/test databases.
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE6_SHAPE)
