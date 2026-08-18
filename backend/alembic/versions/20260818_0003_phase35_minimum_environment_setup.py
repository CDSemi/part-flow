"""Phase 3.5 minimum environment setup.

Environment configuration required before any real production workflow
(IMPLEMENTATION_ROADMAP Phase 3.5, PROJECT_PROFILE §8.4–§8.6/§10/§15):
completes the Area display/terminal configuration and the Operation
configuration fields, and creates `scan_stations`, `machines`, the
dedicated append-only `machine_lifecycle_events` history (created
together with `machines` — NOT the Phase 4 `audit_events` mechanism),
and the singleton `machine_asset_tag_config` (prefix + zero-padded
numeric sequence) Machine creation requires.

Database-enforced invariants added here:

- Area barcodes own exactly the `PF:AREA:<stable-id>` namespace, and an
  assigned Area barcode is stable: it may be assigned once (NULL → a
  valid value) but never changed or cleared afterwards (raise-on-change
  trigger).
- Scan Station identity is a stable, whitespace-free natural key bound
  to one Area. No database trigger freezes the binding: rebinding a
  Scan Station is a configuration workflow controlled at the
  Application layer, and Scan Stations carry no barcode namespace.
- Machine Asset Tags are unique forever (retired Machines keep theirs)
  and immutable (raise-on-change trigger); there is no independent
  Machine barcode column — the barcode is always derived.
- The Area of an active Machine is fixed: `machines.area_id` may change
  only inside the same UPDATE that performs the RETIRED → ACTIVE
  reactivation (`OLD.retired_on IS NOT NULL AND NEW.retired_on IS
  NULL`) — the forward-looking move of the same physical machine while
  it was retired. The atomic reactivation transition together with its
  lifecycle event remains an Application-layer transaction protocol.
- Active Machine display names are unique per Area (partial unique
  index over `retired_on IS NULL` only).
- Maintenance note/expected return exist only inside an active
  maintenance override.
- `machine_lifecycle_events` accepts exactly RETIRED/REACTIVATED with
  the matching before/after state pair, records an Area move only on
  reactivation as a complete distinct previous→current pair, and is
  append-only in PostgreSQL itself (statement-level raise trigger).

Deliberate exclusions (later phases own them): no `audit_events`
(Phase 4), no `part_movements.station_id` (Phase 5), no
`quantity_flows.current_machine_id` or Movement machine columns
(Phase 6), no `areas.worker_identification_mode` (Phase 7), no
`route_steps.preferred_machine_id`, no Workers/Users tables
(Phase 13/14), no PartMovement widening of any kind. Retire/reactivate
atomicity with its lifecycle event is a transaction protocol owned by
the Application layer; the actor on lifecycle events stays a nullable,
reference-free value — no Worker or User foreign key.

Revision ID: 0003_phase35_environment
Revises: 0002_phase3_domain
Create Date: 2026-08-18

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_phase35_environment"
down_revision: str | None = "0002_phase3_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Assigned Area barcodes own exactly the PF:AREA namespace with a
# non-empty, whitespace-free stable-id suffix (PROJECT_PROFILE §10);
# NULL (no barcode) passes the CHECK.
_AREA_BARCODE = "barcode_value ~ '^PF:AREA:[^[:space:]]+$'"

# Stable, whitespace-free Station ID (PROJECT_PROFILE §15).
_SCAN_STATION_ID = "station_id ~ '^[^[:space:]]+$'"

# Generated Asset Tags are non-empty and free of whitespace and ':'
# (prefix rules + numeric sequence), keeping PF:MACHINE:<asset-tag>
# deterministic (PROJECT_PROFILE §8.6/§10).
_MACHINE_ASSET_TAG = "asset_tag ~ '^[^[:space:]:]+$'"

# Prefix input rule (GUI_DESIGN §9): whitespace and ':' rejected;
# empty prefix valid.
_ASSET_TAG_PREFIX = "prefix !~ '[[:space:]:]'"

_FORBID_AREA_BARCODE_CHANGE_FUNCTION = """
CREATE FUNCTION partflow_areas_forbid_barcode_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'areas.barcode_value is stable once assigned: it is never changed or cleared';
END;
$$;
"""

# Row-level: assigning a barcode to an Area that has none (NULL → a
# valid value) stays a normal configuration step, and rewriting the
# same value is a no-op — only changing or clearing an assigned
# barcode raises.
_FORBID_AREA_BARCODE_CHANGE_TRIGGER = """
CREATE TRIGGER trg_areas_forbid_barcode_change
BEFORE UPDATE OF barcode_value ON areas
FOR EACH ROW
WHEN (OLD.barcode_value IS NOT NULL AND NEW.barcode_value IS DISTINCT FROM OLD.barcode_value)
EXECUTE FUNCTION partflow_areas_forbid_barcode_change();
"""

_FORBID_MACHINE_AREA_CHANGE_FUNCTION = """
CREATE FUNCTION partflow_machines_forbid_area_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'machines.area_id is fixed: an Area change is allowed only inside the'
        ' RETIRED -> ACTIVE reactivation update of the same physical machine';
END;
$$;
"""

# Row-level: moving production capacity is a replacement (retire + new
# record), never an edit. The single exception is reactivation of the
# same physical machine that moved while retired — the Area change is
# permitted only inside the same UPDATE that clears retired_on
# (PROJECT_PROFILE §8.6). The atomic reactivation + lifecycle-event
# transaction remains an Application-layer protocol.
_FORBID_MACHINE_AREA_CHANGE_TRIGGER = """
CREATE TRIGGER trg_machines_forbid_area_change
BEFORE UPDATE OF area_id ON machines
FOR EACH ROW
WHEN (
    NEW.area_id IS DISTINCT FROM OLD.area_id
    AND NOT (OLD.retired_on IS NOT NULL AND NEW.retired_on IS NULL)
)
EXECUTE FUNCTION partflow_machines_forbid_area_change();
"""

_FORBID_ASSET_TAG_CHANGE_FUNCTION = """
CREATE FUNCTION partflow_machines_forbid_asset_tag_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'machines.asset_tag is immutable: Asset Tags are never renamed or regenerated';
END;
$$;
"""

# Row-level, fired only when the value actually changes: an UPDATE that
# rewrites the same tag value stays valid.
_FORBID_ASSET_TAG_CHANGE_TRIGGER = """
CREATE TRIGGER trg_machines_forbid_asset_tag_change
BEFORE UPDATE OF asset_tag ON machines
FOR EACH ROW
WHEN (NEW.asset_tag IS DISTINCT FROM OLD.asset_tag)
EXECUTE FUNCTION partflow_machines_forbid_asset_tag_change();
"""

_FORBID_LIFECYCLE_MUTATION_FUNCTION = """
CREATE FUNCTION partflow_machine_lifecycle_events_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'machine_lifecycle_events is append-only: % is not permitted', TG_OP;
END;
$$;
"""

# Statement-level so it also covers TRUNCATE and fires even for
# zero-row UPDATE/DELETE statements (same pattern as part_movements).
_FORBID_LIFECYCLE_MUTATION_TRIGGER = """
CREATE TRIGGER trg_machine_lifecycle_events_forbid_mutation
BEFORE UPDATE OR DELETE OR TRUNCATE ON machine_lifecycle_events
FOR EACH STATEMENT EXECUTE FUNCTION partflow_machine_lifecycle_events_forbid_mutation();
"""


def upgrade() -> None:
    # --- Areas: display properties, terminal flag, barcode namespace.
    op.add_column("areas", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("areas", sa.Column("color", sa.Text(), nullable=True))
    op.add_column("areas", sa.Column("icon_url", sa.Text(), nullable=True))
    op.add_column(
        "areas",
        sa.Column("is_terminal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_areas_barcode_value_namespace"), "areas", sa.text(_AREA_BARCODE)
    )
    op.execute(_FORBID_AREA_BARCODE_CHANGE_FUNCTION)
    op.execute(_FORBID_AREA_BARCODE_CHANGE_TRIGGER)

    # --- Operations: description, planning default, external flag.
    op.add_column("operations", sa.Column("description", sa.Text(), nullable=True))
    op.add_column(
        "operations", sa.Column("default_expected_duration", sa.Interval(), nullable=True)
    )
    op.add_column(
        "operations",
        sa.Column("is_external", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    # --- Scan Stations: stable Station ID bound to one Area.
    op.create_table(
        "scan_stations",
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("area_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("station_id", name="pk_scan_stations"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_scan_stations_area_id_areas"),
        sa.CheckConstraint(_SCAN_STATION_ID, name=op.f("ck_scan_stations_station_id_canonical")),
    )

    # --- Machines: identity, asset metadata, maintenance override,
    # lifecycle. No barcode column — the barcode is always derived from
    # the immutable Asset Tag (PF:MACHINE:<asset-tag>).
    op.create_table(
        "machines",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("area_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("asset_tag", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("serial_number", sa.Text(), nullable=True),
        sa.Column("installed_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("maintenance_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("maintenance_note", sa.Text(), nullable=True),
        sa.Column("maintenance_expected_return", sa.Date(), nullable=True),
        sa.Column(
            "state_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # NULL = active; set by retirement, cleared by reactivation of
        # the same physical machine on the same record.
        sa.Column("retired_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_machines"),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_machines_area_id_areas"),
        # Never reused — uniqueness spans retired Machines forever.
        sa.UniqueConstraint("asset_tag", name="uq_machines_asset_tag"),
        sa.CheckConstraint(_MACHINE_ASSET_TAG, name=op.f("ck_machines_asset_tag_canonical")),
        sa.CheckConstraint(
            "maintenance_since IS NOT NULL"
            " OR (maintenance_note IS NULL AND maintenance_expected_return IS NULL)",
            name=op.f("ck_machines_maintenance_shape"),
        ),
    )
    # Display-name uniqueness constrains only simultaneously active
    # Machines of the same Area; retired records keep their names.
    op.create_index(
        "uq_machines_area_id_name_active",
        "machines",
        ["area_id", "name"],
        unique=True,
        postgresql_where=sa.text("retired_on IS NULL"),
    )
    op.execute(_FORBID_ASSET_TAG_CHANGE_FUNCTION)
    op.execute(_FORBID_ASSET_TAG_CHANGE_TRIGGER)
    op.execute(_FORBID_MACHINE_AREA_CHANGE_FUNCTION)
    op.execute(_FORBID_MACHINE_AREA_CHANGE_TRIGGER)

    # --- Machine lifecycle history: dedicated append-only persistence
    # of RETIRED/REACTIVATED, created together with machines.
    op.create_table(
        "machine_lifecycle_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("machine_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        # Nullable, reference-free actor (no Worker/User FK): future
        # authenticated actor linkage belongs to Phase 14.
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("before_state", sa.Text(), nullable=False),
        sa.Column("after_state", sa.Text(), nullable=False),
        sa.Column("from_area_id", sa.Integer(), nullable=True),
        sa.Column("to_area_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_machine_lifecycle_events"),
        sa.ForeignKeyConstraint(
            ["machine_id"], ["machines.id"], name="fk_machine_lifecycle_events_machine_id_machines"
        ),
        sa.ForeignKeyConstraint(
            ["from_area_id"], ["areas.id"], name="fk_machine_lifecycle_events_from_area_id_areas"
        ),
        sa.ForeignKeyConstraint(
            ["to_area_id"], ["areas.id"], name="fk_machine_lifecycle_events_to_area_id_areas"
        ),
        sa.CheckConstraint(
            "event_type IN ('RETIRED', 'REACTIVATED')",
            name=op.f("ck_machine_lifecycle_events_event_type"),
        ),
        # The before/after pair is fully determined by the event type —
        # this also pins the state vocabulary itself.
        sa.CheckConstraint(
            "(event_type = 'RETIRED' AND before_state = 'ACTIVE' AND after_state = 'RETIRED')"
            " OR (event_type = 'REACTIVATED'"
            " AND before_state = 'RETIRED' AND after_state = 'ACTIVE')",
            name=op.f("ck_machine_lifecycle_events_state_shape"),
        ),
        # An Area move is a complete previous→current pair of distinct
        # Areas, and only a reactivation can carry one.
        sa.CheckConstraint(
            "(from_area_id IS NULL) = (to_area_id IS NULL)"
            " AND (event_type = 'REACTIVATED' OR from_area_id IS NULL)"
            " AND (from_area_id IS NULL OR from_area_id <> to_area_id)",
            name=op.f("ck_machine_lifecycle_events_area_move_shape"),
        ),
    )
    op.create_index(
        "ix_machine_lifecycle_events_machine_id_id",
        "machine_lifecycle_events",
        ["machine_id", "id"],
    )
    op.execute(_FORBID_LIFECYCLE_MUTATION_FUNCTION)
    op.execute(_FORBID_LIFECYCLE_MUTATION_TRIGGER)

    # --- Machine Asset Tag format: singleton prefix + zero-padded
    # sequence configuration. No row is seeded — the format is explicit
    # deployment configuration and Machine creation requires it.
    op.create_table(
        "machine_asset_tag_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("digits", sa.Integer(), nullable=False),
        # Persisted monotonic counter: allocation (atomic
        # UPDATE … RETURNING) guarantees tags are never reused, even
        # across format changes.
        sa.Column("next_sequence", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_machine_asset_tag_config"),
        sa.CheckConstraint("id = 1", name=op.f("ck_machine_asset_tag_config_singleton")),
        sa.CheckConstraint(_ASSET_TAG_PREFIX, name=op.f("ck_machine_asset_tag_config_prefix")),
        sa.CheckConstraint(
            "digits BETWEEN 1 AND 8", name=op.f("ck_machine_asset_tag_config_digits_range")
        ),
        sa.CheckConstraint(
            "next_sequence >= 1", name=op.f("ck_machine_asset_tag_config_next_sequence_positive")
        ),
    )


def downgrade() -> None:
    op.drop_table("machine_asset_tag_config")

    op.execute(
        "DROP TRIGGER trg_machine_lifecycle_events_forbid_mutation ON machine_lifecycle_events;"
    )
    op.execute("DROP FUNCTION partflow_machine_lifecycle_events_forbid_mutation();")
    op.drop_table("machine_lifecycle_events")

    op.execute("DROP TRIGGER trg_machines_forbid_area_change ON machines;")
    op.execute("DROP FUNCTION partflow_machines_forbid_area_change();")
    op.execute("DROP TRIGGER trg_machines_forbid_asset_tag_change ON machines;")
    op.execute("DROP FUNCTION partflow_machines_forbid_asset_tag_change();")
    op.drop_table("machines")

    op.drop_table("scan_stations")

    op.drop_column("operations", "is_external")
    op.drop_column("operations", "default_expected_duration")
    op.drop_column("operations", "description")

    op.execute("DROP TRIGGER trg_areas_forbid_barcode_change ON areas;")
    op.execute("DROP FUNCTION partflow_areas_forbid_barcode_change();")
    op.drop_constraint(op.f("ck_areas_barcode_value_namespace"), "areas")
    op.drop_column("areas", "is_terminal")
    op.drop_column("areas", "icon_url")
    op.drop_column("areas", "color")
    op.drop_column("areas", "description")
