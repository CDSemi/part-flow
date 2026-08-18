"""SQLAlchemy mappings for the Phase 3 data foundation and the
Phase 3.5 minimum environment setup.

Infrastructure-only persistence mappings for the canonical domain shape
defined by PROJECT_PROFILE §8 and SLICE1_DATA_MODEL §17, plus the
Phase 3.5 environment configuration (IMPLEMENTATION_ROADMAP Phase 3.5):
completed Area/Operation configuration fields, `scan_stations`,
`machines`, the append-only `machine_lifecycle_events` history, and the
Machine Asset Tag format configuration. Business rules stay in the
Domain/Application layers; this module owns table shape and the
invariants PostgreSQL can enforce declaratively (CHECK, UNIQUE, FK).

Deliberate canonical decisions encoded here:

- The canonical PN string is the domain identity: `part_numbers` uses it
  as its natural primary key, and the production tables
  (`work_order_demands`, `quantity_flows`, `part_movements`) keep their
  own canonical PN value with **no foreign key to `part_numbers`** — the
  optional master may be hard-deleted without touching production data.
- PN consistency between a Movement and its flow is structural: the
  composite FK `(quantity_flow_id, part_number)` →
  `quantity_flows (id, part_number)`.
- `quantity_flows.assigned_route_id` is the single canonical link to an
  AssignedRoute snapshot: nullable, unique (at most one flow per
  snapshot), present exactly when `route_mode = 'PLANNED'`. There is no
  reverse `assigned_routes.quantity_flow_id`.
- `part_movements` and `machine_lifecycle_events` append-only
  enforcement (raise-on-write triggers) and the `machines.asset_tag`
  immutability trigger are database DDL owned by the Alembic
  migrations, not by this metadata.

Every constraint and index carries an explicit deterministic name so
database errors are debuggable and migrations stay reviewable.
"""

import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Interval,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from app.domain.enums import (
    MachineLifecycleEventType,
    MachineLifecycleState,
    MovementType,
    QuantityFlowStatus,
    RequestType,
    RouteMode,
)

# Canonical PN form (PROJECT_PROFILE §7): uppercase, non-empty, and free
# of any whitespace. Reused verbatim by every table that keeps a PN by
# value, so the database rejects non-canonical values even if a caller
# bypasses domain normalization. The POSIX class [[:space:]] is used
# instead of the \s shorthand so the expression contains no backslash
# and never depends on string-literal escaping semantics.
CANONICAL_PART_NUMBER_SQL = (
    "part_number = upper(part_number) AND part_number !~ '[[:space:]]' AND part_number <> ''"
)

# Area barcode ownership (PROJECT_PROFILE §10): an assigned Area
# barcode is always `PF:AREA:<stable-id>` with a non-empty,
# whitespace-free stable-id suffix. NULL (no barcode assigned) passes a
# CHECK, so the expression needs no explicit NULL branch.
AREA_BARCODE_SQL = "barcode_value ~ '^PF:AREA:[^[:space:]]+$'"

# Stable Scan Station identity (PROJECT_PROFILE §15): the Station ID is
# a non-empty, whitespace-free opaque value — it addresses the station
# route (`/scan-station/<station-id>`) and is recorded on Movements
# from Phase 5 on.
SCAN_STATION_ID_SQL = "station_id ~ '^[^[:space:]]+$'"

# Machine Asset Tag shape (PROJECT_PROFILE §8.6/§10): generated from a
# configured prefix (whitespace and ':' rejected) plus a zero-padded
# numeric sequence, so a stored tag is always non-empty and free of
# whitespace and ':' — keeping `PF:MACHINE:<asset-tag>` deterministic.
MACHINE_ASSET_TAG_SQL = "asset_tag ~ '^[^[:space:]:]+$'"

# Asset Tag format prefix rule (GUI_DESIGN §9 Barcode configuration):
# whitespace and ':' are rejected; an empty prefix stays valid.
ASSET_TAG_PREFIX_SQL = "prefix !~ '[[:space:]:]'"

# Fallback naming convention for anything created without an explicit
# name. All constraints below are still named explicitly.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base carrying the metadata Alembic migrates."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Department(Base):
    """Major organizational unit owning Areas (PROJECT_PROFILE §7)."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("name", name="uq_departments_name"),)


class Area(Base):
    """Stable physical shop-floor location identity (PROJECT_PROFILE §8.4)."""

    __tablename__ = "areas"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    department_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("departments.id", name="fk_areas_department_id_departments"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    barcode_value: Mapped[str | None] = mapped_column(Text)
    # Display properties (Phase 3.5): they may change freely — Area
    # identity and barcode stay stable and history is unaffected.
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(Text)
    icon_url: Mapped[str | None] = mapped_column(Text)
    # Terminal Areas (Stockroom) end the normal flow; the Stockroom
    # workflow itself arrives with Phase 10.
    is_terminal: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Unique where assigned; PostgreSQL UNIQUE ignores NULLs, so
        # many rows without a barcode stay valid.
        UniqueConstraint("barcode_value", name="uq_areas_barcode_value"),
        # PF:AREA namespace ownership (Phase 3.5, PROJECT_PROFILE §10).
        CheckConstraint(AREA_BARCODE_SQL, name=conv("ck_areas_barcode_value_namespace")),
    )


class Operation(Base):
    """Type of work supported by an Area (PROJECT_PROFILE §8.5)."""

    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    area_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_operations_area_id_areas"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    # Informational planning default (PROJECT_PROFILE §8.5); duration
    # semantics stay with the routing phases.
    default_expected_duration: Mapped[datetime.timedelta | None] = mapped_column(Interval)
    # External processing (plating, painting, testing) performed
    # outside the shop; no barcode field — an Operation is resolved
    # from Area configuration (PROJECT_PROFILE §8.5, §10).
    is_external: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("area_id", "code", name="uq_operations_area_id_code"),)


class ScanStation(Base):
    """Stable Scan Station configuration (PROJECT_PROFILE §15).

    Application/infrastructure configuration, not a domain aggregate:
    the stable Station ID is the natural key (`/scan-station/<id>` and,
    from Phase 5 on, the Movement audit column `station_id` reference
    it), bound to exactly one Area. An inactive station accepts no
    production use; the Station Selector never substitutes another.
    """

    __tablename__ = "scan_stations"

    station_id: Mapped[str] = mapped_column(Text, primary_key=True)
    area_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_scan_stations_area_id_areas"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(SCAN_STATION_ID_SQL, name=conv("ck_scan_stations_station_id_canonical")),
    )


class Machine(Base):
    """Physical production resource inside one Area (PROJECT_PROFILE §8.6).

    The immutable, never-reused Asset Tag is the human-readable
    identity of the physical asset and fully determines the Machine
    barcode (`PF:MACHINE:<asset-tag>`, PROJECT_PROFILE §10) — no
    independent barcode column exists. Immutability is enforced by a
    raise-on-change trigger owned by the Phase 3.5 migration.

    Lifecycle: active until `retired_on` is set; reactivation of the
    same physical machine clears it again. Retire/reactivate and their
    `machine_lifecycle_events` rows commit atomically — a transaction
    protocol owned by the Application layer, not expressible as a
    declarative constraint. The operational Running/Idle state is
    derived (assignment arrives with Phase 6) and never stored; only
    the explicit maintenance override and `state_changed_at` persist.
    """

    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    area_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_machines_area_id_areas"),
        nullable=False,
    )
    # Operator-facing display name: reusable across time and
    # replacements, unique among the ACTIVE Machines of one Area only
    # (partial unique index below).
    name: Mapped[str] = mapped_column(Text, nullable=False)
    asset_tag: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Optional asset metadata — production tracking never depends on it.
    manufacturer: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    serial_number: Mapped[str | None] = mapped_column(Text)
    installed_on: Mapped[datetime.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    # Explicit maintenance override: active while maintenance_since is
    # set; note and expected return exist only inside an override.
    maintenance_since: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_note: Mapped[str | None] = mapped_column(Text)
    maintenance_expected_return: Mapped[datetime.date | None] = mapped_column(Date)
    # When the derived operational state last changed; every surface
    # derives elapsed time in state from it — no duration is stored.
    state_changed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = active. Set by retirement, cleared by reactivation of the
    # same physical machine on the same record.
    retired_on: Mapped[datetime.date | None] = mapped_column(Date)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Never reused — uniqueness spans retired Machines forever.
        UniqueConstraint("asset_tag", name="uq_machines_asset_tag"),
        CheckConstraint(MACHINE_ASSET_TAG_SQL, name=conv("ck_machines_asset_tag_canonical")),
        # Maintenance note/expected return never exist outside an
        # active maintenance override.
        CheckConstraint(
            "maintenance_since IS NOT NULL"
            " OR (maintenance_note IS NULL AND maintenance_expected_return IS NULL)",
            name=conv("ck_machines_maintenance_shape"),
        ),
        # Display-name uniqueness constrains only simultaneously active
        # Machines of the same Area — retired records keep their names.
        Index(
            "uq_machines_area_id_name_active",
            "area_id",
            "name",
            unique=True,
            postgresql_where=text("retired_on IS NULL"),
        ),
    )


class MachineLifecycleEvent(Base):
    """Append-only Machine lifecycle history (PROJECT_PROFILE §8.6).

    Dedicated RETIRED/REACTIVATED persistence created with `machines`
    (IMPLEMENTATION_ROADMAP Phase 3.5) — deliberately NOT the Phase 4
    `audit_events` mechanism and never a generic audit framework.
    Events are immutable (raise-on-write trigger owned by the
    migration) and commit atomically with the lifecycle change they
    record. `actor` stays a nullable, reference-free value: Machine
    lifecycle is a Management action, future authenticated actor
    linkage belongs to Users/authentication (Phase 14), and Workers are
    never associated with these events.
    """

    __tablename__ = "machine_lifecycle_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    machine_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("machines.id", name="fk_machine_lifecycle_events_machine_id_machines"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    before_state: Mapped[str] = mapped_column(Text, nullable=False)
    after_state: Mapped[str] = mapped_column(Text, nullable=False)
    # Set only when the physical machine moved while retired
    # (reactivation with a forward-only Area change): previous and
    # current Area — historical Movements keep their recorded Areas.
    from_area_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_machine_lifecycle_events_from_area_id_areas"),
    )
    to_area_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_machine_lifecycle_events_to_area_id_areas"),
    )

    __table_args__ = (
        CheckConstraint(
            f"event_type IN ('{MachineLifecycleEventType.RETIRED}',"
            f" '{MachineLifecycleEventType.REACTIVATED}')",
            name=conv("ck_machine_lifecycle_events_event_type"),
        ),
        # The before/after pair is fully determined by the event type —
        # this also pins the state vocabulary itself.
        CheckConstraint(
            f"(event_type = '{MachineLifecycleEventType.RETIRED}'"
            f" AND before_state = '{MachineLifecycleState.ACTIVE}'"
            f" AND after_state = '{MachineLifecycleState.RETIRED}')"
            f" OR (event_type = '{MachineLifecycleEventType.REACTIVATED}'"
            f" AND before_state = '{MachineLifecycleState.RETIRED}'"
            f" AND after_state = '{MachineLifecycleState.ACTIVE}')",
            name=conv("ck_machine_lifecycle_events_state_shape"),
        ),
        # An Area move is recorded as a complete previous→current pair
        # of distinct Areas, and only a reactivation can carry one.
        CheckConstraint(
            "(from_area_id IS NULL) = (to_area_id IS NULL)"
            f" AND (event_type = '{MachineLifecycleEventType.REACTIVATED}'"
            " OR from_area_id IS NULL)"
            " AND (from_area_id IS NULL OR from_area_id <> to_area_id)",
            name=conv("ck_machine_lifecycle_events_area_move_shape"),
        ),
        Index("ix_machine_lifecycle_events_machine_id_id", "machine_id", "id"),
    )


class MachineAssetTagConfig(Base):
    """Machine Asset Tag format configuration (PROJECT_PROFILE §8.6).

    Administration → Barcode configuration: a prefix plus a zero-padded
    numeric sequence (`CD-` + 4 digits → `CD-0001`) — deliberately no
    template engine. Single row (CHECK id = 1); no row is seeded — the
    format is explicit deployment configuration and Machine creation
    requires it to exist. `next_sequence` is the persisted monotonic
    counter: allocating from it (atomic UPDATE … RETURNING) guarantees
    Asset Tags are never reused even across format changes, and a
    format change applies to Machines created afterwards only —
    existing tags are never renamed or regenerated. `digits` is a
    minimum width: a sequence that outgrows it renders unpadded, never
    truncated.
    """

    __tablename__ = "machine_asset_tag_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prefix: Mapped[str] = mapped_column(Text, nullable=False)
    digits: Mapped[int] = mapped_column(Integer, nullable=False)
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("id = 1", name=conv("ck_machine_asset_tag_config_singleton")),
        CheckConstraint(ASSET_TAG_PREFIX_SQL, name=conv("ck_machine_asset_tag_config_prefix")),
        CheckConstraint(
            "digits BETWEEN 1 AND 8", name=conv("ck_machine_asset_tag_config_digits_range")
        ),
        CheckConstraint(
            "next_sequence >= 1", name=conv("ck_machine_asset_tag_config_next_sequence_positive")
        ),
    )


class PartNumber(Base):
    """Optional current-metadata master for a canonical PN (PROJECT_PROFILE §8.1).

    The canonical PN string is the natural primary key — no surrogate
    `part_number_id` exists anywhere. Production tables never reference
    this table, so a master row can be hard-deleted (and later recreated
    for the same canonical PN) without touching production data.
    """

    __tablename__ = "part_numbers"

    part_number: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            CANONICAL_PART_NUMBER_SQL, name=conv("ck_part_numbers_part_number_canonical")
        ),
    )


class WorkOrder(Base):
    """Business order shell with a nullable external number (PROJECT_PROFILE §8.2)."""

    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Opaque arbitrary external string; NULL is valid data for an
    # internal Work Order and multiple NULLs may coexist — uniqueness is
    # a partial unique index over non-null numbers only (below).
    work_order_number: Mapped[str | None] = mapped_column(Text)
    received_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    due_date: Mapped[datetime.date | None] = mapped_column(Date)
    # Value vocabulary belongs to the Phase 4 intake workflow; 'OPEN' is
    # the established initial state of an accepting Work Order.
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'OPEN'"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "uq_work_orders_work_order_number",
            "work_order_number",
            unique=True,
            postgresql_where=text("work_order_number IS NOT NULL"),
        ),
    )


class WorkOrderDemand(Base):
    """Requested quantity of one PN for one Work Order (PROJECT_PROFILE §8.3)."""

    __tablename__ = "work_order_demands"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    work_order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("work_orders.id", name="fk_work_order_demands_work_order_id_work_orders"),
        nullable=False,
    )
    # Canonical PN kept by the demand itself — deliberately no FK to the
    # optional part_numbers master.
    part_number: Mapped[str] = mapped_column(Text, nullable=False)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    due_date: Mapped[datetime.date | None] = mapped_column(Date)
    priority_rank: Mapped[int | None] = mapped_column(Integer)
    job_numbers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    requester: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            CANONICAL_PART_NUMBER_SQL, name=conv("ck_work_order_demands_part_number_canonical")
        ),
        CheckConstraint(
            f"request_type IN ('{RequestType.NEW}', '{RequestType.MODIFY}')",
            name=conv("ck_work_order_demands_request_type"),
        ),
        CheckConstraint(
            "requested_quantity > 0", name=conv("ck_work_order_demands_requested_quantity_positive")
        ),
        CheckConstraint(
            "allocated_quantity >= 0",
            name=conv("ck_work_order_demands_allocated_quantity_non_negative"),
        ),
        Index("ix_work_order_demands_work_order_id", "work_order_id"),
        Index("ix_work_order_demands_part_number", "part_number"),
    )


class RouteTemplate(Base):
    """Reusable route definition — user-facing Planned Routes (PROJECT_PROFILE §8.8)."""

    __tablename__ = "route_templates"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # NULL = active. An ever-used template is archived instead of
    # deleted; there is no template versioning — AssignedRoute snapshots
    # preserve historical definitions.
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RouteStep(Base):
    """Ordered expected step of a RouteTemplate (PROJECT_PROFILE §8.9)."""

    __tablename__ = "route_steps"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    route_template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("route_templates.id", name="fk_route_steps_route_template_id_route_templates"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("areas.id", name="fk_route_steps_area_id_areas"), nullable=False
    )
    operation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("operations.id", name="fk_route_steps_operation_id_operations")
    )
    expected_duration: Mapped[datetime.timedelta | None] = mapped_column(Interval)
    instructions: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "route_template_id", "sequence", name="uq_route_steps_route_template_id_sequence"
        ),
    )


class AssignedRoute(Base):
    """Immutable route snapshot of one PLANNED QuantityFlow (PROJECT_PROFILE §8.10).

    Carries no `quantity_flow_id` back-reference: the owning flow points
    here through `quantity_flows.assigned_route_id` — the single FK
    between the two tables.
    """

    __tablename__ = "assigned_routes"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Informational provenance only; the snapshot stays valid and
    # independent whatever happens to the template.
    source_route_template_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "route_templates.id",
            name="fk_assigned_routes_source_route_template_id_route_templates",
        ),
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AssignedRouteStep(Base):
    """Snapshot copy of a route step, independent of the mutable template."""

    __tablename__ = "assigned_route_steps"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    assigned_route_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "assigned_routes.id", name="fk_assigned_route_steps_assigned_route_id_assigned_routes"
        ),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    area_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_assigned_route_steps_area_id_areas"),
        nullable=False,
    )
    operation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("operations.id", name="fk_assigned_route_steps_operation_id_operations"),
    )
    expected_duration: Mapped[datetime.timedelta | None] = mapped_column(Interval)
    instructions: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "assigned_route_id",
            "sequence",
            name="uq_assigned_route_steps_assigned_route_id_sequence",
        ),
    )


class QuantityFlow(Base):
    """Traceable production portion of PN quantity (PROJECT_PROFILE §8.7).

    `current_area_id` is the maintained current-position projection: set
    by the creating INSERT itself and updated inside Movement
    transactions, while PartMovement history remains the source of truth
    it must stay rebuildable from. `current_machine_id` and
    `parent_flow_id` are canonical later-phase columns and deliberately
    do not exist yet.
    """

    __tablename__ = "quantity_flows"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    part_number: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{QuantityFlowStatus.ACTIVE}'")
    )
    route_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text(f"'{RouteMode.FLOATING}'")
    )
    assigned_route_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "assigned_routes.id", name="fk_quantity_flows_assigned_route_id_assigned_routes"
        ),
    )
    current_area_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_quantity_flows_current_area_id_areas"),
        nullable=False,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            CANONICAL_PART_NUMBER_SQL, name=conv("ck_quantity_flows_part_number_canonical")
        ),
        CheckConstraint("quantity > 0", name=conv("ck_quantity_flows_quantity_positive")),
        CheckConstraint(
            f"route_mode IN ('{RouteMode.FLOATING}', '{RouteMode.PLANNED}')",
            name=conv("ck_quantity_flows_route_mode"),
        ),
        # A PLANNED flow always references its snapshot; a FLOATING flow
        # never does (PROJECT_PROFILE §8.7).
        CheckConstraint(
            f"(route_mode = '{RouteMode.PLANNED}') = (assigned_route_id IS NOT NULL)",
            name=conv("ck_quantity_flows_route_mode_assigned_route"),
        ),
        # At most one flow per snapshot (one-to-one ownership).
        UniqueConstraint("assigned_route_id", name="uq_quantity_flows_assigned_route_id"),
        # Composite-FK target guaranteeing Movement/flow PN agreement.
        UniqueConstraint("id", "part_number", name="uq_quantity_flows_id_part_number"),
        Index(
            "ix_quantity_flows_part_number_active",
            "part_number",
            postgresql_where=text(f"status = '{QuantityFlowStatus.ACTIVE}'"),
        ),
        Index("ix_quantity_flows_current_area_id", "current_area_id"),
    )


class PartMovement(Base):
    """Immutable append-only production event (PROJECT_PROFILE §8.11).

    Append-only enforcement lives in PostgreSQL (raise-on-write trigger
    created by the Phase 3 migration), never only in application
    convention. Canonical later-phase columns (`station_id`,
    `worker_id`, `scan_session_id`, `movement_reason`,
    `reverses_movement_id`, Machine columns) deliberately do not exist
    yet.
    """

    __tablename__ = "part_movements"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    quantity_flow_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Canonical PN kept by the Movement itself: history identifies its
    # PN without any join to the optional master.
    part_number: Mapped[str] = mapped_column(Text, nullable=False)
    movement_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    from_area_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("areas.id", name="fk_part_movements_from_area_id_areas")
    )
    to_area_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("areas.id", name="fk_part_movements_to_area_id_areas"),
        nullable=False,
    )
    operation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("operations.id", name="fk_part_movements_operation_id_operations"),
        nullable=False,
    )
    # References the immutable snapshot step (never the mutable
    # route_steps template row): set for a PLANNED flow's Movement, NULL
    # for FLOATING. Cross-table agreement with the flow's own
    # AssignedRoute is a transaction-protocol invariant (Phase 4).
    assigned_route_step_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "assigned_route_steps.id",
            name="fk_part_movements_assigned_route_step_id_assigned_route_steps",
        ),
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Idempotency key: one id per submission, reused on transport
    # retries; uniqueness guarantees at-most-once recording.
    device_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    __table_args__ = (
        # PN agreement with the owning flow is structural: a Movement of
        # flow A can never carry the PN of flow B.
        ForeignKeyConstraint(
            ["quantity_flow_id", "part_number"],
            ["quantity_flows.id", "quantity_flows.part_number"],
            name="fk_part_movements_quantity_flow_id_part_number_quantity_flows",
        ),
        CheckConstraint(
            f"movement_type IN ('{MovementType.RECEIVED}')",
            name=conv("ck_part_movements_movement_type"),
        ),
        CheckConstraint("quantity > 0", name=conv("ck_part_movements_quantity_positive")),
        # Movement-shape rule: RECEIVED introduces quantity, so it has
        # no source Area. Widens per movement type in later phases.
        CheckConstraint(
            f"movement_type = '{MovementType.RECEIVED}' AND from_area_id IS NULL",
            name=conv("ck_part_movements_received_shape"),
        ),
        UniqueConstraint("device_event_id", name="uq_part_movements_device_event_id"),
        Index("ix_part_movements_quantity_flow_id_id", "quantity_flow_id", "id"),
    )
