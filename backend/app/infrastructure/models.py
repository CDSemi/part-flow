"""SQLAlchemy mappings for the Phase 3 data foundation, the Phase 3.5
minimum environment setup, and the Phase 4 audit persistence.

Infrastructure-only persistence mappings for the canonical domain shape
defined by PROJECT_PROFILE §8 and SLICE1_DATA_MODEL §17, plus the
Phase 3.5 environment configuration (IMPLEMENTATION_ROADMAP Phase 3.5):
completed Area/Operation configuration fields, `scan_stations`,
`machines`, the append-only `machine_lifecycle_events` history, and the
Machine Asset Tag format configuration; plus the Phase 4 generic
append-only `audit_events` table for master-data and business-demand
changes (SLICE1_DATA_MODEL §16); plus the Phase 5 Movement widening
(`TRANSFERRED`, `part_movements.station_id`); plus the Phase 6 Machine
assignment and Area completion widening (`quantity_flows.current_machine_id`,
the Movement Machine references, the application-command sequence, and
the `ASSIGNED_TO_MACHINE` / `RELEASED_FROM_MACHINE` / `AREA_COMPLETED`
types); plus the Phase 7 direct-processing widening (an `AREA_COMPLETED`
without a Machine for an Area without Machines). Business rules stay in the
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
- `part_movements`, `machine_lifecycle_events`, and `audit_events`
  append-only enforcement (raise-on-write triggers) and the
  `machines.asset_tag` immutability trigger are database DDL owned by
  the Alembic migrations, not by this metadata.

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
    AuditEntityType,
    AuditEventType,
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

# Stable Scan Station identity (PROJECT_PROFILE §15): the Station ID
# addresses the station route (`/scan-station/<station-id>`) and the
# configuration API as one URL path segment and is recorded on
# Movements from Phase 5 on — so it is a simple URL-safe identifier:
# ASCII letters, digits, '.', '_' and '-' only.
SCAN_STATION_ID_SQL = "station_id ~ '^[A-Za-z0-9._-]+$'"

# Machine Asset Tag shape (PROJECT_PROFILE §8.6/§10): generated from a
# configured prefix (whitespace and ':' rejected) plus a zero-padded
# numeric sequence, so a stored tag is always non-empty and free of
# whitespace and ':' — keeping `PF:MACHINE:<asset-tag>` deterministic.
MACHINE_ASSET_TAG_SQL = "asset_tag ~ '^[^[:space:]:]+$'"

# Asset Tag format prefix rule (GUI_DESIGN §9 Barcode configuration):
# whitespace and ':' are rejected; an empty prefix stays valid.
ASSET_TAG_PREFIX_SQL = "prefix !~ '[[:space:]:]'"

# Machine barcode namespace (PROJECT_PROFILE §10): the barcode is
# always the Asset Tag in the PF:MACHINE namespace — derived, never
# stored and never entered.
MACHINE_BARCODE_PREFIX = "PF:MACHINE:"

# PN barcode namespace (PROJECT_PROFILE §10): the reusable folder
# barcode carries the canonical uppercase PN itself — fully derived,
# never stored and never separately unique.
PART_NUMBER_BARCODE_PREFIX = "PF:PN:"

# Movement-shape rule per movement type (SLICE1_DATA_MODEL §11; Phase 5
# transfer; Phase 6 Machine assignment and Area completion; Phase 7
# direct-processing completion). Reused verbatim by the Phase 7
# migration so the stored CHECK and the mapping never drift. RECEIVED
# introduces quantity (no source Area, no Machine); TRANSFERRED moves
# between two DIFFERENT Areas at a Station and references no Machine (a
# transfer from actively processing quantity is preceded by its own
# AREA_COMPLETED); the three in-Area Movements stay in ONE Area at a
# Station and carry exactly the Machine reference their meaning
# requires — assignment a destination Machine only, release a source
# Machine only, completion a source Machine when the quantity left a
# Machine (Phase 6) or NO Machine when it was directly processed by an
# Area without Machines (Phase 7); a completion never has a
# destination Machine.
MOVEMENT_SHAPE_SQL = (
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

# Row-level idempotency guarantee of the application-command model
# (Phase 6): one `device_event_id` identifies one command, which may
# append several Movements numbered by `command_sequence`. Referenced
# by the commands that translate a race lost at COMMIT into a replay.
DEVICE_EVENT_ID_CONSTRAINT = "uq_part_movements_device_event_id_command_sequence"

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
        # Once assigned, the barcode is stable — it may be assigned
        # from NULL but never changed or cleared afterwards
        # (raise-on-change trigger owned by the Phase 3.5 migration).
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
    No database trigger freezes the binding: rebinding a Scan Station
    is a configuration workflow controlled at the Application layer.
    Scan Stations carry no barcode namespace (PROJECT_PROFILE §10).
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
    declarative constraint. The Area of an active Machine is fixed:
    `area_id` may change only inside the same UPDATE that performs the
    RETIRED → ACTIVE reactivation (raise-on-change trigger owned by the
    Phase 3.5 migration) — every other capacity move is a replacement
    (retire + new record). The operational Running/Idle state is
    derived (Running = ACTIVE quantity whose projection
    `quantity_flows.current_machine_id` references the Machine, Phase 6)
    and never stored; only the explicit maintenance override and
    `state_changed_at` persist.
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

    @property
    def barcode_value(self) -> str:
        """Derived Machine barcode: the Asset Tag in the PF:MACHINE namespace.

        PROJECT_PROFILE §8.6/§10: ``barcode_value`` is always equal to
        the Asset Tag — there is no independent barcode identifier and
        no stored column, so the derivation lives with the mapping.
        """
        return f"{MACHINE_BARCODE_PREFIX}{self.asset_tag}"

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

    @property
    def barcode_value(self) -> str:
        """Derived PN barcode: the canonical PN in the PF:PN namespace.

        PROJECT_PROFILE §8.1/§10: the folder barcode identifies only the
        PN and carries the canonical uppercase PN itself — there is no
        stored barcode column and no separate barcode key, so the
        derivation lives with the mapping (same pattern as Machine).
        """
        return f"{PART_NUMBER_BARCODE_PREFIX}{self.part_number}"

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

    `current_area_id` and `current_machine_id` are the maintained
    current-position projection: the Area is set by the creating INSERT
    itself and both are updated inside Movement transactions, while
    PartMovement history remains the source of truth they must stay
    rebuildable from (the Machine is the destination Machine of the
    flow's latest Movement — NULL unless that Movement is an
    `ASSIGNED_TO_MACHINE`). `parent_flow_id` is a canonical later-phase
    column and deliberately does not exist yet.
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
    # The current executor while the quantity is ON_MACHINE (Phase 6);
    # NULL while queued or finished (READY_TO_TRANSFER) in the Area —
    # the two are told apart by the latest Movement, never by this
    # column alone.
    current_machine_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machines.id", name="fk_quantity_flows_current_machine_id_machines"),
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
        Index("ix_quantity_flows_current_machine_id", "current_machine_id"),
    )


class PartMovement(Base):
    """Immutable append-only production event (PROJECT_PROFILE §8.11).

    Append-only enforcement lives in PostgreSQL (raise-on-write trigger
    created by the Phase 3 migration), never only in application
    convention. `station_id` (Phase 5) records the stable Scan Station
    identity of a scan-driven Movement for audit (PROJECT_PROFILE §15);
    `source_machine_id` / `destination_machine_id` (Phase 6) are the
    Machine references of the assignment, release and completion
    Movements; `command_sequence` (Phase 6) numbers the Movements of
    one application command — one `device_event_id` per command. The
    remaining canonical later-phase columns (`worker_id`,
    `scan_session_id`, `movement_reason`, `reverses_movement_id`)
    deliberately do not exist yet.
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
    # Stable Scan Station identity of a scan-driven Movement (Phase 5,
    # PROJECT_PROFILE §15 Scan Station Persistence): audit context
    # only — never production state. NULL for Management-initiated
    # Movements such as the Phase 4 RECEIVED release.
    station_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("scan_stations.station_id", name="fk_part_movements_station_id_scan_stations"),
    )
    # Machine references (PROJECT_PROFILE §8.11): the Machine the
    # quantity left (release, completion) and the Machine it was
    # assigned to (assignment). Which one a type carries is fixed by
    # the shape CHECK.
    source_machine_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machines.id", name="fk_part_movements_source_machine_id_machines"),
    )
    destination_machine_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machines.id", name="fk_part_movements_destination_machine_id_machines"),
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Idempotency key: one id per submission, reused on transport
    # retries; uniqueness guarantees at-most-once recording.
    device_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Position inside the application command identified by
    # `device_event_id` (Phase 6): 1 for every single-Movement command;
    # 1, 2 for the atomic AREA_COMPLETED + TRANSFERRED transfer. All
    # rows of one command share the id, so `WHERE device_event_id = …`
    # yields the complete command — what Undo (Phase 9) reverses.
    command_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
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
            "movement_type IN ("
            + ", ".join(f"'{movement_type}'" for movement_type in MovementType)
            + ")",
            name=conv("ck_part_movements_movement_type"),
        ),
        CheckConstraint("quantity > 0", name=conv("ck_part_movements_quantity_positive")),
        # Movement-shape rule per type (widens per movement type in the
        # phase that adds it) — see MOVEMENT_SHAPE_SQL.
        CheckConstraint(MOVEMENT_SHAPE_SQL, name=conv("ck_part_movements_movement_shape")),
        CheckConstraint(
            "command_sequence >= 1", name=conv("ck_part_movements_command_sequence_positive")
        ),
        UniqueConstraint("device_event_id", "command_sequence", name=DEVICE_EVENT_ID_CONSTRAINT),
        Index("ix_part_movements_quantity_flow_id_id", "quantity_flow_id", "id"),
    )


# Declared after the class so the index expression is literally the one
# `production_release.released_quantities` emits — the JSONB SUBSCRIPT
# form, which is what PostgreSQL must match to use the index (the `->`
# operator form is a different expression node to the planner). Partial
# on RECEIVED, because only a RECEIVED Movement is release evidence.
# Created by migration `0005_phase4_release_index`.
Index(
    "ix_part_movements_received_demand_context",
    PartMovement.metadata_["context"]["work_order_demand_id"].as_integer(),
    postgresql_where=PartMovement.movement_type == MovementType.RECEIVED,
)


class AuditEvent(Base):
    """Generic append-only audit row (SLICE1_DATA_MODEL §16).

    Records master-data and business-demand changes only — WorkOrder,
    WorkOrderDemand, and PartNumber. Rows are descriptive history for
    display and accountability: never replayed to build state, never
    describing production actions (the `RECEIVED` PartMovement is the
    production audit record), and deliberately not an event-sourcing
    framework. `entity_id` is polymorphic text with no FK — the
    internal PK for WorkOrder/WorkOrderDemand, the canonical PN string
    for PartNumber; integrity is guaranteed by writing the audit row in
    the same transaction as the audited change (an Application-layer
    transaction protocol, Phase 4 workflows). `actor_reference` stays a
    nullable, reference-free value until authentication exists
    (Phase 14). Append-only enforcement is the raise-on-write trigger
    owned by the Phase 4 migration.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor_reference: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # jsonb snapshots of the audited fields; before_data is NULL for
    # creation events. Edits append a new UPDATED row — prior rows are
    # never rewritten.
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    __table_args__ = (
        # Both vocabularies widen additively in later phases.
        CheckConstraint(
            f"event_type IN ('{AuditEventType.CREATED}', '{AuditEventType.UPDATED}')",
            name=conv("ck_audit_events_event_type"),
        ),
        CheckConstraint(
            f"entity_type IN ('{AuditEntityType.WORK_ORDER}',"
            f" '{AuditEntityType.WORK_ORDER_DEMAND}', '{AuditEntityType.PART_NUMBER}')",
            name=conv("ck_audit_events_entity_type"),
        ),
        # Per-entity history in write order.
        Index("ix_audit_events_entity_type_entity_id_id", "entity_type", "entity_id", "id"),
    )
