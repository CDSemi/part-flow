"""Phase 10 persistence: Stockroom `STOCKED` and WorkOrderAllocation.

Exactly the additive schema the Stockroom and allocation workflows need
(IMPLEMENTATION_ROADMAP Phase 10; PROJECT_PROFILE §8.2, §8.12, §18;
SLICE1_DATA_MODEL §17/§18):

- the movement-type CHECK widens with `STOCKED` and the per-type shape
  CHECK gains its branch: a `STOCKED` records the scan of quantity into
  the terminal Stockroom Area exactly like a `TRANSFERRED` — from one
  Area into a DIFFERENT Area at a Scan Station, no Machine reference
  (actively processing quantity is preceded by its own
  `AREA_COMPLETED` inside the same command); the terminal flag of the
  destination is an Area configuration rule the Application layer
  judges under the Area row lock, not a shape rule;
- the QuantityFlow lifecycle widens with `STOCKED` (the flow's quantity
  is manufacturing-complete and never active inventory again); the
  `closed_at` agreement CHECK is unchanged and covers it;
- `work_orders.completed_at` (the canonical done date, SLICE1 §17): a
  maintained projection of the allocation records — set to the
  timestamp of the allocation event that fully allocated the last open
  demand line, cleared again by an allocation reversal — with the
  partial index `(completed_at, id)` the keyset-paged completed history
  reads;
- the append-only `work_order_allocations` table: one row per
  allocation event of stocked PN quantity to one demand line —
  canonical PN by value, the demand FK, a positive quantity, the
  source (`STOCKROOM` receiving confirmation / `MANAGEMENT`), the
  manual-override flag, an optional reason (mandatory on a reversal,
  CHECK), `reverses_allocation_id` (FK to this table, set on a
  reversal, UNIQUE — an allocation can be taken back at most once,
  including by a race), the recording Scan Station (NULL from
  Management), a reference-free `actor_reference`, `allocated_at`, and
  the `device_event_id` + `command_sequence` idempotency pair of the
  application-command model (`UNIQUE`). Allocation never references a
  Movement or a QuantityFlow: PROJECT_PROFILE §8.12 keeps allocation
  independent from Movement. A raise-on-write trigger enforces
  append-only in PostgreSQL itself (as for `part_movements`).

The stored `work_order_demands.allocated_quantity` (Slice 1) becomes a
maintained projection of the demand's active allocation rows — no
column change.

Deliberate exclusions (later phases own them): no `allocated_by_worker_id`
(Worker sessions, Phase 13), no allocation permission configuration
(Phase 14 — no authorization is simulated), no read-model tables for
monitoring (Phase 11), no return of stocked quantity to production
(PROJECT_PROFILE §32 open decision 1 — a `STOCKED` command is not
undoable).

Revision ID: 0011_phase10_stock_allocation
Revises: 0010_phase9_undo_corrections
Create Date: 2026-09-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_phase10_stock_allocation"
down_revision: str | None = "0010_phase9_undo_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MOVEMENTS = "part_movements"
_FLOWS = "quantity_flows"
_WORK_ORDERS = "work_orders"
_ALLOCATIONS = "work_order_allocations"
_TYPE_CHECK = "ck_part_movements_movement_type"
_SHAPE_CHECK = "ck_part_movements_movement_shape"
_STATUS_CHECK = "ck_quantity_flows_status"
_COMPLETED_INDEX = "ix_work_orders_completed_at_id"

_PHASE9_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED', 'SPLIT', 'MERGED',"
    " 'SCRAPPED', 'QUANTITY_ADJUSTED', 'REVERSED')"
)
_PHASE10_TYPES = (
    "movement_type IN ('RECEIVED', 'TRANSFERRED', 'ASSIGNED_TO_MACHINE',"
    " 'RELEASED_FROM_MACHINE', 'AREA_COMPLETED', 'SPLIT', 'MERGED',"
    " 'SCRAPPED', 'QUANTITY_ADJUSTED', 'REVERSED', 'STOCKED')"
)

_PHASE9_STATUSES = "status IN ('ACTIVE', 'SPLIT', 'MERGED', 'SCRAPPED', 'REVERSED')"
_PHASE10_STATUSES = "status IN ('ACTIVE', 'SPLIT', 'MERGED', 'SCRAPPED', 'REVERSED', 'STOCKED')"

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts `_PHASE10_SHAPE` equals
# `models.MOVEMENT_SHAPE_SQL` and `_PHASE9_SHAPE` equals the previous
# migration's expression.
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
_PHASE10_SHAPE = (
    _PHASE9_SHAPE + " OR (movement_type = 'STOCKED' AND from_area_id IS NOT NULL"
    " AND from_area_id <> to_area_id AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
)

_CANONICAL_PART_NUMBER_SQL = (
    "part_number = upper(part_number) AND part_number !~ '[[:space:]]' AND part_number <> ''"
)
_ALLOCATION_SOURCES = "source IN ('STOCKROOM', 'MANAGEMENT')"
_REVERSAL_REASON_SQL = "reverses_allocation_id IS NULL OR allocation_reason IS NOT NULL"

_FORBID_MUTATION_FUNCTION = """
CREATE FUNCTION partflow_work_order_allocations_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'work_order_allocations is append-only: % is not permitted', TG_OP;
END;
$$;
"""

# Statement-level so it also covers TRUNCATE and fires even for
# zero-row UPDATE/DELETE statements.
_FORBID_MUTATION_TRIGGER = """
CREATE TRIGGER trg_work_order_allocations_forbid_mutation
BEFORE UPDATE OR DELETE OR TRUNCATE ON work_order_allocations
FOR EACH STATEMENT EXECUTE FUNCTION partflow_work_order_allocations_forbid_mutation();
"""


def upgrade() -> None:
    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE10_TYPES)
    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE10_SHAPE)

    op.drop_constraint(op.f(_STATUS_CHECK), _FLOWS, type_="check")
    op.create_check_constraint(op.f(_STATUS_CHECK), _FLOWS, _PHASE10_STATUSES)

    op.add_column(
        _WORK_ORDERS, sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        _COMPLETED_INDEX,
        _WORK_ORDERS,
        ["completed_at", "id"],
        unique=False,
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )

    op.create_table(
        _ALLOCATIONS,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("work_order_demand_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "is_manual_override", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("allocation_reason", sa.Text(), nullable=True),
        sa.Column("reverses_allocation_id", sa.BigInteger(), nullable=True),
        sa.Column("station_id", sa.Text(), nullable=True),
        sa.Column("actor_reference", sa.Text(), nullable=True),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_event_id", sa.Text(), nullable=False),
        sa.Column("command_sequence", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            _CANONICAL_PART_NUMBER_SQL,
            name=op.f("ck_work_order_allocations_part_number_canonical"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_work_order_allocations_quantity_positive")
        ),
        sa.CheckConstraint(_ALLOCATION_SOURCES, name=op.f("ck_work_order_allocations_source")),
        sa.CheckConstraint(
            _REVERSAL_REASON_SQL,
            name=op.f("ck_work_order_allocations_reversal_reason_required"),
        ),
        sa.CheckConstraint(
            "command_sequence >= 1",
            name=op.f("ck_work_order_allocations_command_sequence_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["work_order_demand_id"],
            ["work_order_demands.id"],
            name="fk_work_order_allocations_demand_id_work_order_demands",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_allocation_id"],
            ["work_order_allocations.id"],
            name="fk_work_order_allocations_reverses_allocation_id",
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["scan_stations.station_id"],
            name="fk_work_order_allocations_station_id_scan_stations",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_order_allocations"),
        sa.UniqueConstraint(
            "reverses_allocation_id", name="uq_work_order_allocations_reverses_allocation_id"
        ),
        sa.UniqueConstraint(
            "device_event_id",
            "command_sequence",
            name="uq_work_order_allocations_device_event_id_command_sequence",
        ),
    )
    op.create_index(
        "ix_work_order_allocations_work_order_demand_id",
        _ALLOCATIONS,
        ["work_order_demand_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_order_allocations_part_number", _ALLOCATIONS, ["part_number"], unique=False
    )
    op.execute(_FORBID_MUTATION_FUNCTION)
    op.execute(_FORBID_MUTATION_TRIGGER)


def downgrade() -> None:
    # Destructive by nature: a database holding Phase 10 history
    # (STOCKED Movements, stocked flows, allocation rows, completed
    # Work Orders) cannot satisfy the re-created Phase 9 checks, which
    # PostgreSQL re-validates against existing rows — so the downgrade
    # fails loudly BEFORE any table or column is dropped instead of
    # dropping history — run it only against disposable development/test
    # databases. The allocation table is dropped last, after the checks
    # proved the database holds no Phase 10 production history.
    op.drop_constraint(op.f(_STATUS_CHECK), _FLOWS, type_="check")
    op.create_check_constraint(op.f(_STATUS_CHECK), _FLOWS, _PHASE9_STATUSES)

    op.drop_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_SHAPE_CHECK), _MOVEMENTS, _PHASE9_SHAPE)
    op.drop_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, type_="check")
    op.create_check_constraint(op.f(_TYPE_CHECK), _MOVEMENTS, _PHASE9_TYPES)

    # A completed Work Order is Phase 10 history too: refuse to forget
    # its done date silently.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM work_orders WHERE completed_at IS NOT NULL)"
        " THEN RAISE EXCEPTION 'work_orders holds completed history; refusing downgrade';"
        " END IF; END $$;"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM work_order_allocations)"
        " THEN RAISE EXCEPTION 'work_order_allocations holds history; refusing downgrade';"
        " END IF; END $$;"
    )
    op.execute("DROP TRIGGER trg_work_order_allocations_forbid_mutation ON work_order_allocations;")
    op.execute("DROP FUNCTION partflow_work_order_allocations_forbid_mutation();")
    op.drop_index("ix_work_order_allocations_part_number", table_name=_ALLOCATIONS)
    op.drop_index("ix_work_order_allocations_work_order_demand_id", table_name=_ALLOCATIONS)
    op.drop_table(_ALLOCATIONS)
    op.drop_index(_COMPLETED_INDEX, table_name=_WORK_ORDERS)
    op.drop_column(_WORK_ORDERS, "completed_at")
