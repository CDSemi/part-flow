"""Phase 8 persistence: quantity SPLIT and MERGED lineage.

Exactly the additive schema the quantity-lineage workflows need
(IMPLEMENTATION_ROADMAP Phase 8; PROJECT_PROFILE §8.7, §8.11, §11
Quantity Splitting / Quantity Merging / Quantity Integrity;
SLICE1_DATA_MODEL §18):

- the movement-type CHECK widens with `SPLIT` and `MERGED`, and the
  per-type shape CHECK gains their one shared branch: a lineage event
  stays in ONE Area at a Scan Station and references NO Machine — the
  Machine and the holding state of the quantity it carries are derived
  by following the lineage to the last position-bearing Movement, never
  re-stated on the lineage row;
- the QuantityFlow lifecycle closure: `quantity_flows.status` is now
  CHECK-constrained to `ACTIVE` / `SPLIT` / `MERGED`, and `closed_at`
  is set exactly for a non-ACTIVE status. A consumed source flow keeps
  its history and last position but is never active inventory again;
- `quantity_flow_lineage` — the append-only descent graph, one row per
  (parent, child) edge with the relation and the `device_event_id` of
  the command that recorded it. A SPLIT fans one parent out to several
  children (one edge per child), a MERGED fans several parents into one
  child (one edge per source): the same edge shape reconstructs 1 → N
  and N → 1 alike, which is why there is no single
  `quantity_flows.parent_flow_id` column — it could not express a merge.

Movement rows stay append-only (trigger of migration 0002); the edge
table gets the same raise-on-write trigger.

Deliberate exclusions (later phases own them): no `worker_id` /
`scan_session_id`, no `movement_reason` / `reverses_movement_id` /
`REVERSED` (Phase 9 — Undo of a lineage command reverses it as a whole
through `device_event_id`), no `STOCKED` (Phase 10), no
`QUANTITY_ADJUSTED` / `SCRAPPED`.

Revision ID: 0009_phase8_split_merge
Revises: 0008_phase7_direct_processing
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_phase8_split_merge"
down_revision: str | None = "0008_phase7_direct_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVEMENTS = "part_movements"
_FLOWS = "quantity_flows"
_LINEAGE = "quantity_flow_lineage"
_TYPE_CHECK = "ck_part_movements_movement_type"
_SHAPE_CHECK = "ck_part_movements_movement_shape"
_STATUS_CHECK = "ck_quantity_flows_status"
_CLOSED_AT_CHECK = "ck_quantity_flows_status_closed_at"

_PHASE7_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED')"
)
_PHASE8_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED', 'SPLIT', 'MERGED')"
)

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts `_PHASE8_SHAPE` equals
# `models.MOVEMENT_SHAPE_SQL` and `_PHASE7_SHAPE` equals the previous
# migration's expression.
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

_FORBID_MUTATION_FUNCTION = """
CREATE FUNCTION partflow_quantity_flow_lineage_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'quantity_flow_lineage is append-only: % is not permitted', TG_OP;
END;
$$;
"""

# Statement-level so it also covers TRUNCATE and fires even for
# zero-row UPDATE/DELETE statements.
_FORBID_MUTATION_TRIGGER = """
CREATE TRIGGER trg_quantity_flow_lineage_forbid_mutation
BEFORE UPDATE OR DELETE OR TRUNCATE ON quantity_flow_lineage
FOR EACH STATEMENT EXECUTE FUNCTION partflow_quantity_flow_lineage_forbid_mutation();
"""


def upgrade() -> None:
    # QuantityFlow lifecycle closure (every existing flow is ACTIVE with
    # a NULL closed_at, so both checks validate against existing rows).
    op.create_check_constraint(
        op.f(_STATUS_CHECK), _FLOWS, "status IN ('ACTIVE', 'SPLIT', 'MERGED')"
    )
    op.create_check_constraint(
        op.f(_CLOSED_AT_CHECK), _FLOWS, "(status = 'ACTIVE') = (closed_at IS NULL)"
    )

    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE8_TYPES)
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE8_SHAPE)

    op.create_table(
        _LINEAGE,
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("parent_flow_id", sa.Integer(), nullable=False),
        sa.Column("child_flow_id", sa.Integer(), nullable=False),
        sa.Column("device_event_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["parent_flow_id"],
            ["quantity_flows.id"],
            name="fk_quantity_flow_lineage_parent_flow_id_quantity_flows",
        ),
        sa.ForeignKeyConstraint(
            ["child_flow_id"],
            ["quantity_flows.id"],
            name="fk_quantity_flow_lineage_child_flow_id_quantity_flows",
        ),
        sa.CheckConstraint(
            "relation IN ('SPLIT', 'MERGED')", name=op.f("ck_quantity_flow_lineage_relation")
        ),
        sa.CheckConstraint(
            "parent_flow_id <> child_flow_id",
            name=op.f("ck_quantity_flow_lineage_parent_child_distinct"),
        ),
        sa.UniqueConstraint(
            "parent_flow_id",
            "child_flow_id",
            name="uq_quantity_flow_lineage_parent_flow_id_child_flow_id",
        ),
    )
    op.create_index("ix_quantity_flow_lineage_parent_flow_id", _LINEAGE, ["parent_flow_id"])
    op.create_index("ix_quantity_flow_lineage_child_flow_id", _LINEAGE, ["child_flow_id"])
    op.execute(_FORBID_MUTATION_FUNCTION)
    op.execute(_FORBID_MUTATION_TRIGGER)


def downgrade() -> None:
    # Destructive by nature: a database holding lineage history (SPLIT
    # or MERGED Movements, closed flows, lineage edges) cannot satisfy
    # the Phase 7 checks (re-validated against existing rows), so the
    # downgrade fails loudly BEFORE the edge table is dropped instead
    # of dropping history — run it only against disposable
    # development/test databases.
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE7_SHAPE)
    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE7_TYPES)
    # A closed flow never exists without its lineage Movements, which
    # the re-created checks above already refuse — so no closure is
    # silently un-checked here.
    op.drop_constraint(op.f(_CLOSED_AT_CHECK), _FLOWS, type_="check")
    op.drop_constraint(op.f(_STATUS_CHECK), _FLOWS, type_="check")

    op.execute("DROP TRIGGER trg_quantity_flow_lineage_forbid_mutation ON quantity_flow_lineage;")
    op.execute("DROP FUNCTION partflow_quantity_flow_lineage_forbid_mutation();")
    op.drop_index("ix_quantity_flow_lineage_child_flow_id", table_name=_LINEAGE)
    op.drop_index("ix_quantity_flow_lineage_parent_flow_id", table_name=_LINEAGE)
    op.drop_table(_LINEAGE)
