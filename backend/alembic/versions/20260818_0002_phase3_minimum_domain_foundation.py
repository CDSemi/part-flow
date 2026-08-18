"""Phase 3 minimum canonical domain and data foundation.

Creates the canonical Phase 3 schema (PROJECT_PROFILE §8,
SLICE1_DATA_MODEL §17, IMPLEMENTATION_ROADMAP Phase 3): Department,
Area, Operation, the optional PartNumber master (natural PN key),
WorkOrder, WorkOrderDemand, RouteTemplate/RouteStep, the immutable
AssignedRoute/AssignedRouteStep snapshot tables, QuantityFlow with its
maintained `current_area_id` projection, and the append-only
PartMovement event table guarded by a raise-on-write trigger.

Deliberate exclusions (later phases own them): `scan_stations`,
`machines`, `audit_events`, `station_id`, `worker_id`,
`scan_session_id`, `current_machine_id`, `parent_flow_id`,
`movement_reason`, `reverses_movement_id`, `preferred_machine_id`,
`is_terminal`, `worker_identification_mode`, `completed_at`. The
additional application-role UPDATE/DELETE revocation on
`part_movements` requires a distinct application database role, which
does not exist yet — it arrives with deployment hardening; the trigger
below already enforces append-only at the database for every
non-superuser path.

Revision ID: 0002_phase3_domain
Revises: 0001_repo_foundation
Create Date: 2026-08-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_phase3_domain"
down_revision: str | None = "0001_repo_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Canonical PN form (PROJECT_PROFILE §7): uppercase, non-empty, no
# whitespace anywhere in the value. POSIX class [[:space:]] instead of
# the \s shorthand: no backslash, no string-escaping ambiguity.
_CANONICAL_PN = (
    "part_number = upper(part_number) AND part_number !~ '[[:space:]]' AND part_number <> ''"
)

_FORBID_MUTATION_FUNCTION = """
CREATE FUNCTION partflow_part_movements_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'part_movements is append-only: % is not permitted', TG_OP;
END;
$$;
"""

# Statement-level so it also covers TRUNCATE and fires even for
# zero-row UPDATE/DELETE statements.
_FORBID_MUTATION_TRIGGER = """
CREATE TRIGGER trg_part_movements_forbid_mutation
BEFORE UPDATE OR DELETE OR TRUNCATE ON part_movements
FOR EACH STATEMENT EXECUTE FUNCTION partflow_part_movements_forbid_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_departments"),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )

    op.create_table(
        "areas",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("barcode_value", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_areas"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_areas_department_id_departments"
        ),
        # UNIQUE ignores NULLs: many Areas without a barcode stay valid.
        sa.UniqueConstraint("barcode_value", name="uq_areas_barcode_value"),
    )

    op.create_table(
        "operations",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("area_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operations"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_operations_area_id_areas"),
        sa.UniqueConstraint("area_id", "code", name="uq_operations_area_id_code"),
    )

    # Optional current-metadata master: natural PN key, no surrogate id.
    # No production table references it, so a master row can be
    # hard-deleted without touching production data.
    op.create_table(
        "part_numbers",
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("part_number", name="pk_part_numbers"),
        sa.CheckConstraint(_CANONICAL_PN, name=op.f("ck_part_numbers_part_number_canonical")),
    )

    op.create_table(
        "work_orders",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        # Nullable opaque external string; multiple NULLs are valid —
        # uniqueness applies to non-null numbers only (partial index).
        sa.Column("work_order_number", sa.Text(), nullable=True),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'OPEN'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_orders"),
    )
    op.create_index(
        "uq_work_orders_work_order_number",
        "work_orders",
        ["work_order_number"],
        unique=True,
        postgresql_where=sa.text("work_order_number IS NOT NULL"),
    )

    op.create_table(
        "work_order_demands",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        # Canonical PN kept by value — deliberately no FK to the
        # optional part_numbers master.
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("request_type", sa.Text(), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("allocated_quantity", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("priority_rank", sa.Integer(), nullable=True),
        sa.Column(
            "job_numbers",
            ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("requester", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_order_demands"),
        sa.ForeignKeyConstraint(
            ["work_order_id"],
            ["work_orders.id"],
            name="fk_work_order_demands_work_order_id_work_orders",
        ),
        sa.CheckConstraint(_CANONICAL_PN, name=op.f("ck_work_order_demands_part_number_canonical")),
        sa.CheckConstraint(
            "request_type IN ('NEW', 'MODIFY')", name=op.f("ck_work_order_demands_request_type")
        ),
        sa.CheckConstraint(
            "requested_quantity > 0", name=op.f("ck_work_order_demands_requested_quantity_positive")
        ),
        sa.CheckConstraint(
            "allocated_quantity >= 0",
            name=op.f("ck_work_order_demands_allocated_quantity_non_negative"),
        ),
    )
    op.create_index("ix_work_order_demands_work_order_id", "work_order_demands", ["work_order_id"])
    op.create_index("ix_work_order_demands_part_number", "work_order_demands", ["part_number"])

    op.create_table(
        "route_templates",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # NULL = active; ever-used templates archive instead of delete.
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_route_templates"),
    )

    op.create_table(
        "route_steps",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("route_template_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("area_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=True),
        sa.Column("expected_duration", sa.Interval(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_route_steps"),
        sa.ForeignKeyConstraint(
            ["route_template_id"],
            ["route_templates.id"],
            name="fk_route_steps_route_template_id_route_templates",
        ),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_route_steps_area_id_areas"),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["operations.id"], name="fk_route_steps_operation_id_operations"
        ),
        sa.UniqueConstraint(
            "route_template_id", "sequence", name="uq_route_steps_route_template_id_sequence"
        ),
    )

    # Immutable snapshot shell; the owning flow references it through
    # quantity_flows.assigned_route_id — no quantity_flow_id here.
    op.create_table(
        "assigned_routes",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("source_route_template_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assigned_routes"),
        sa.ForeignKeyConstraint(
            ["source_route_template_id"],
            ["route_templates.id"],
            name="fk_assigned_routes_source_route_template_id_route_templates",
        ),
    )

    op.create_table(
        "assigned_route_steps",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("assigned_route_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("area_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=True),
        sa.Column("expected_duration", sa.Interval(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_assigned_route_steps"),
        sa.ForeignKeyConstraint(
            ["assigned_route_id"],
            ["assigned_routes.id"],
            name="fk_assigned_route_steps_assigned_route_id_assigned_routes",
        ),
        sa.ForeignKeyConstraint(
            ["area_id"], ["areas.id"], name="fk_assigned_route_steps_area_id_areas"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
            name="fk_assigned_route_steps_operation_id_operations",
        ),
        sa.UniqueConstraint(
            "assigned_route_id",
            "sequence",
            name="uq_assigned_route_steps_assigned_route_id_sequence",
        ),
    )

    op.create_table(
        "quantity_flows",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("route_mode", sa.Text(), server_default=sa.text("'FLOATING'"), nullable=False),
        sa.Column("assigned_route_id", sa.Integer(), nullable=True),
        # Maintained current-position projection: set by the creating
        # INSERT itself; PartMovement history stays the source of truth.
        sa.Column("current_area_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_quantity_flows"),
        sa.ForeignKeyConstraint(
            ["assigned_route_id"],
            ["assigned_routes.id"],
            name="fk_quantity_flows_assigned_route_id_assigned_routes",
        ),
        sa.ForeignKeyConstraint(
            ["current_area_id"], ["areas.id"], name="fk_quantity_flows_current_area_id_areas"
        ),
        sa.CheckConstraint(_CANONICAL_PN, name=op.f("ck_quantity_flows_part_number_canonical")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_quantity_flows_quantity_positive")),
        sa.CheckConstraint(
            "route_mode IN ('FLOATING', 'PLANNED')", name=op.f("ck_quantity_flows_route_mode")
        ),
        # A PLANNED flow always references its snapshot; a FLOATING flow
        # never does.
        sa.CheckConstraint(
            "(route_mode = 'PLANNED') = (assigned_route_id IS NOT NULL)",
            name=op.f("ck_quantity_flows_route_mode_assigned_route"),
        ),
        # At most one flow per snapshot.
        sa.UniqueConstraint("assigned_route_id", name="uq_quantity_flows_assigned_route_id"),
        # Composite-FK target guaranteeing Movement/flow PN agreement.
        sa.UniqueConstraint("id", "part_number", name="uq_quantity_flows_id_part_number"),
    )
    op.create_index(
        "ix_quantity_flows_part_number_active",
        "quantity_flows",
        ["part_number"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index("ix_quantity_flows_current_area_id", "quantity_flows", ["current_area_id"])

    op.create_table(
        "part_movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("quantity_flow_id", sa.Integer(), nullable=False),
        # Canonical PN kept by the Movement itself: history identifies
        # its PN without any join to the optional master.
        sa.Column("part_number", sa.Text(), nullable=False),
        sa.Column("movement_type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("from_area_id", sa.Integer(), nullable=True),
        sa.Column("to_area_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        # The immutable snapshot step — never the mutable route_steps
        # template row. Set for PLANNED flows, NULL for FLOATING.
        sa.Column("assigned_route_step_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_event_id", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_part_movements"),
        # Structural PN agreement: a Movement of flow A can never carry
        # the PN of flow B.
        sa.ForeignKeyConstraint(
            ["quantity_flow_id", "part_number"],
            ["quantity_flows.id", "quantity_flows.part_number"],
            name="fk_part_movements_quantity_flow_id_part_number_quantity_flows",
        ),
        sa.ForeignKeyConstraint(
            ["from_area_id"], ["areas.id"], name="fk_part_movements_from_area_id_areas"
        ),
        sa.ForeignKeyConstraint(
            ["to_area_id"], ["areas.id"], name="fk_part_movements_to_area_id_areas"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["operations.id"], name="fk_part_movements_operation_id_operations"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_route_step_id"],
            ["assigned_route_steps.id"],
            name="fk_part_movements_assigned_route_step_id_assigned_route_steps",
        ),
        sa.CheckConstraint(
            "movement_type IN ('RECEIVED')", name=op.f("ck_part_movements_movement_type")
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_part_movements_quantity_positive")),
        # RECEIVED introduces quantity: no source Area. Widens per
        # movement type in later phases.
        sa.CheckConstraint(
            "movement_type = 'RECEIVED' AND from_area_id IS NULL",
            name=op.f("ck_part_movements_received_shape"),
        ),
        sa.UniqueConstraint("device_event_id", name="uq_part_movements_device_event_id"),
    )
    op.create_index(
        "ix_part_movements_quantity_flow_id_id", "part_movements", ["quantity_flow_id", "id"]
    )

    # Append-only enforcement in PostgreSQL itself, not application
    # convention: any UPDATE, DELETE, or TRUNCATE statement raises.
    op.execute(_FORBID_MUTATION_FUNCTION)
    op.execute(_FORBID_MUTATION_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_part_movements_forbid_mutation ON part_movements;")
    op.execute("DROP FUNCTION partflow_part_movements_forbid_mutation();")
    op.drop_table("part_movements")
    op.drop_table("quantity_flows")
    op.drop_table("assigned_route_steps")
    op.drop_table("assigned_routes")
    op.drop_table("route_steps")
    op.drop_table("route_templates")
    op.drop_table("work_order_demands")
    op.drop_table("work_orders")
    op.drop_table("part_numbers")
    op.drop_table("operations")
    op.drop_table("areas")
    op.drop_table("departments")
