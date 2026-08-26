"""Phase 6 persistence: one-shot Machine assignment and Area completion.

Exactly the additive schema the Machine-Area processing workflows need
(IMPLEMENTATION_ROADMAP Phase 6; PROJECT_PROFILE §7 Area Completion,
§8.6, §8.11, §12; SLICE1_DATA_MODEL §15/§18):

- `quantity_flows.current_machine_id` — the canonical Machine
  projection column (nullable FK to `machines.id`, indexed): set by
  `ASSIGNED_TO_MACHINE`, cleared by `RELEASED_FROM_MACHINE`,
  `AREA_COMPLETED` and `TRANSFERRED`, always inside the Movement
  transaction and always rebuildable from Movement history alone;
- `part_movements.source_machine_id` / `destination_machine_id` — the
  canonical Machine references of a Movement (nullable FKs to
  `machines.id`);
- `part_movements.command_sequence` — the position of a Movement
  inside its **application command**. One `device_event_id` now
  identifies one command, and a command may append several Movements
  (a transfer from `ON_MACHINE` quantity appends `AREA_COMPLETED` then
  `TRANSFERRED` atomically, PROJECT_PROFILE §8.11). The row-level
  idempotency guarantee moves from `UNIQUE (device_event_id)` to
  `UNIQUE (device_event_id, command_sequence)`; every existing row is a
  one-Movement command and keeps sequence 1. `WHERE device_event_id =
  …` therefore yields the complete command — the relationship Undo
  (Phase 9) needs to reverse a multi-Movement command as one;
- the movement-type CHECK widens to `RECEIVED`, `TRANSFERRED`,
  `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`;
- the per-type shape CHECK gains one branch per new type: the three
  in-Area Movements stay in ONE Area (`from_area_id = to_area_id`),
  are scan-driven (Station recorded), and carry exactly the Machine
  reference their meaning requires — assignment a destination Machine
  only, release and completion a source Machine only (Phase 6 completes
  Machine-assigned quantity only; direct-processing completion without
  a Machine is Phase 7 and widens this branch then). `RECEIVED` and
  `TRANSFERRED` never reference a Machine.

Deliberate exclusions (later phases own them): no `parent_flow_id`
(Phase 8), no `worker_id`/`scan_session_id`, no `movement_reason` or
`reverses_movement_id` (Phase 9), no `SPLIT`, `MERGED`, `REVERSED`,
`STOCKED` or any other movement type, no new table, and no
partial-quantity support of any kind (Phase 8 SPLIT).

The immutability trigger of migration 0002 is untouched: every new
movement type is append-only exactly like `RECEIVED` rows.

Revision ID: 0007_phase6_machine_assignment
Revises: 0006_phase5_transfer
Create Date: 2026-08-26

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_phase6_machine_assignment"
down_revision: str | None = "0006_phase5_transfer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVEMENTS = "part_movements"
_FLOWS = "quantity_flows"
_TYPE_CHECK = "ck_part_movements_movement_type"
_SHAPE_CHECK = "ck_part_movements_movement_shape"
_SEQUENCE_CHECK = "ck_part_movements_command_sequence_positive"
_PHASE5_EVENT_UNIQUE = "uq_part_movements_device_event_id"
_PHASE6_EVENT_UNIQUE = "uq_part_movements_device_event_id_command_sequence"
_FLOW_MACHINE_FK = "fk_quantity_flows_current_machine_id_machines"
_FLOW_MACHINE_INDEX = "ix_quantity_flows_current_machine_id"
_SOURCE_MACHINE_FK = "fk_part_movements_source_machine_id_machines"
_DESTINATION_MACHINE_FK = "fk_part_movements_destination_machine_id_machines"

_PHASE5_TYPES = "movement_type IN ('RECEIVED', 'TRANSFERRED')"
_PHASE6_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED')"
)
_PHASE5_SHAPE = (
    "(movement_type = 'RECEIVED' AND from_area_id IS NULL)"
    " OR (movement_type = 'TRANSFERRED' AND from_area_id IS NOT NULL"
    " AND from_area_id <> to_area_id AND station_id IS NOT NULL)"
)
# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts it equals
# `models.MOVEMENT_SHAPE_SQL`.
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


def upgrade() -> None:
    # Machine projection on the flow (SLICE1 §15).
    op.add_column(_FLOWS, sa.Column("current_machine_id", sa.Integer(), nullable=True))
    op.create_foreign_key(_FLOW_MACHINE_FK, _FLOWS, "machines", ["current_machine_id"], ["id"])
    op.create_index(_FLOW_MACHINE_INDEX, _FLOWS, ["current_machine_id"])

    # Machine references of a Movement (PROJECT_PROFILE §8.11).
    op.add_column(_MOVEMENTS, sa.Column("source_machine_id", sa.Integer(), nullable=True))
    op.add_column(_MOVEMENTS, sa.Column("destination_machine_id", sa.Integer(), nullable=True))
    op.create_foreign_key(_SOURCE_MACHINE_FK, _MOVEMENTS, "machines", ["source_machine_id"], ["id"])
    op.create_foreign_key(
        _DESTINATION_MACHINE_FK, _MOVEMENTS, "machines", ["destination_machine_id"], ["id"]
    )

    # Application-command relationship: every existing row is a
    # one-Movement command (sequence 1); the server default keeps that
    # true for every later single-Movement command.
    op.add_column(
        _MOVEMENTS,
        sa.Column("command_sequence", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint(op.f(_SEQUENCE_CHECK), _MOVEMENTS, "command_sequence >= 1")
    op.drop_constraint(_PHASE5_EVENT_UNIQUE, _MOVEMENTS, type_="unique")
    op.create_unique_constraint(
        _PHASE6_EVENT_UNIQUE, _MOVEMENTS, ["device_event_id", "command_sequence"]
    )

    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE6_TYPES)
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE6_SHAPE)


def downgrade() -> None:
    # Destructive by nature: a database holding Phase 6 Movement
    # history cannot satisfy the Phase 5 checks (re-validated against
    # existing rows) and a multi-Movement command cannot satisfy the
    # Phase 5 uniqueness, so the downgrade fails loudly instead of
    # dropping history — run it only against disposable
    # development/test databases.
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE5_SHAPE)
    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE5_TYPES)

    op.drop_constraint(_PHASE6_EVENT_UNIQUE, _MOVEMENTS, type_="unique")
    op.create_unique_constraint(_PHASE5_EVENT_UNIQUE, _MOVEMENTS, ["device_event_id"])
    op.drop_constraint(op.f(_SEQUENCE_CHECK), _MOVEMENTS, type_="check")
    op.drop_column(_MOVEMENTS, "command_sequence")

    op.drop_constraint(_DESTINATION_MACHINE_FK, _MOVEMENTS, type_="foreignkey")
    op.drop_constraint(_SOURCE_MACHINE_FK, _MOVEMENTS, type_="foreignkey")
    op.drop_column(_MOVEMENTS, "destination_machine_id")
    op.drop_column(_MOVEMENTS, "source_machine_id")

    op.drop_index(_FLOW_MACHINE_INDEX, table_name=_FLOWS)
    op.drop_constraint(_FLOW_MACHINE_FK, _FLOWS, type_="foreignkey")
    op.drop_column(_FLOWS, "current_machine_id")
