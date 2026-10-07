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
without a Machine for an Area without Machines); plus the Phase 8
quantity lineage (the `SPLIT` / `MERGED` types, the QuantityFlow
lifecycle closure, and the append-only `quantity_flow_lineage` edge
table); plus the Phase 10 Stockroom and allocation persistence (the
`STOCKED` type, the `STOCKED` flow closure, `work_orders.completed_at`
and the append-only `work_order_allocations` table); plus the Phase 12
Hot rank constraints (positive, unique `priority_rank`) and the Hot
list idempotency index on `audit_events`; plus the Phase 13 Workers
registry (`workers`, with the `DELETED` audit event and the `Worker`
audit entity); plus the Phase 13 Worker identification
(`areas.worker_identification_mode`, `areas.fixed_worker_id`) and the
production audit identity (`part_movements.worker_id`,
`work_order_allocations.allocated_by_worker_id`); plus the Worker
Session runtime (`worker_sessions`, `part_movements.scan_session_id`)
and the global policy singleton (`application_policy`,
`areas.worker_session_timeout_minutes`). Business rules stay
in the Domain/Application layers; this module owns table shape and the
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
    LargeBinary,
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
    AllocationSource,
    AuditEntityType,
    AuditEventType,
    LineageRelation,
    MachineLifecycleEventType,
    MachineLifecycleState,
    MovementType,
    QuantityFlowStatus,
    RequestType,
    RouteMode,
    ThemePreference,
    WorkerIdentificationMode,
    WorkerSessionEndReason,
)

# Canonical PN form (PROJECT_PROFILE §7): uppercase, non-empty, and free
# of any whitespace. Reused verbatim by every table that keeps a PN by
# value, so the database rejects non-canonical values even if a caller
# bypasses domain normalization. The POSIX class [[:space:]] is used
# instead of the \s shorthand so the expression contains no backslash
# and never depends on string-literal escaping semantics. Both clauses
# run under the "C" collation, so they check ASCII only (no ASCII
# lowercase letter, no ASCII whitespace) and never depend on the OS libc
# case tables, which disagree with Python `str.upper()` on some code
# points (`ɤ`); the full Unicode uppercase and the refusal of every
# Unicode whitespace are owned by `app.domain.part_number`, whose every
# result passes this CHECK. Repeated verbatim by migration
# `0018_phase13_pn_check_collation`.
CANONICAL_PART_NUMBER_SQL = (
    """part_number = upper(part_number COLLATE "C")"""
    """ AND part_number COLLATE "C" !~ '[[:space:]]' AND part_number <> ''"""
)

# Part Number image on the master row (Phase 13 slice 7, CD1; owner
# decision OD-10): the S1 avatar rules repeated — all three image
# columns or none, PNG/JPEG/WebP only, 1 byte to 2 MiB. Repeated
# verbatim by migration `0023_phase13_part_number_master`.
PART_NUMBER_IMAGE_SHAPE_SQL = (
    "(image IS NULL) = (image_type IS NULL) AND (image IS NULL) = (image_updated_at IS NULL)"
)
PART_NUMBER_IMAGE_TYPE_SQL = "image_type IN ('image/png', 'image/jpeg', 'image/webp')"
PART_NUMBER_IMAGE_SIZE_SQL = "image IS NULL OR octet_length(image) BETWEEN 1 AND 2097152"

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

# The Scan Station's own saved Dark/Light preference (Phase 13 slice 10,
# GUI_DESIGN §2.1 station tier) in its full canonical vocabulary; NULL
# (no preference) passes a CHECK. Repeated verbatim by migration
# `0026_phase13_station_theme`.
SCAN_STATION_THEME_PREFERENCE_SQL = (
    "theme_preference IN (" + ", ".join(f"'{theme}'" for theme in ThemePreference) + ")"
)

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

# Canonical Worker badge (PROJECT_PROFILE §10; owner decision OD-3):
# the badge printed on the employee badge, stored trimmed and UPPERCASE
# so the plain UNIQUE is case-insensitive, at most 128 characters
# (`app.domain.worker_badge.MAX_BADGE_BARCODE_LENGTH`), and outside the
# `PF:` namespace — badges are the one non-PF scanned value. The `PF:`
# test needs no upper(): the uppercase clause already holds. The trim
# and uppercase clauses run under the "C" collation, so they check ASCII
# only and never depend on the OS libc case tables, which disagree with
# Python `str.upper()` on some code points (`ɤ`); the full Unicode
# uppercase is owned by `app.domain.worker_badge`, whose every result
# passes this CHECK. Repeated verbatim by migration
# `0015_phase13_badge_check`.
WORKER_BADGE_BARCODE_SQL = (
    r"""badge_barcode <> '' AND badge_barcode COLLATE "C" !~ '^\s|\s$'"""
    """ AND badge_barcode = upper(badge_barcode COLLATE "C")"""
    " AND char_length(badge_barcode) <= 128 AND left(badge_barcode, 3) <> 'PF:'"
)

# Movement-shape rule per movement type (SLICE1_DATA_MODEL §11; Phase 5
# transfer; Phase 6 Machine assignment and Area completion; Phase 7
# direct-processing completion; Phase 8 quantity lineage; Phase 9
# corrections and auditable quantity events). Reused
# verbatim by the Phase 9 migration so the stored CHECK and the mapping
# never drift. RECEIVED
# introduces quantity (no source Area, no Machine); TRANSFERRED moves
# between two DIFFERENT Areas at a Station and references no Machine (a
# transfer from actively processing quantity is preceded by its own
# AREA_COMPLETED); the three in-Area Movements stay in ONE Area at a
# Station and carry exactly the Machine reference their meaning
# requires — assignment a destination Machine only, release a source
# Machine only, completion a source Machine when the quantity left a
# Machine (Phase 6) or NO Machine when it was directly processed by an
# Area without Machines (Phase 7); a completion never has a
# destination Machine. The two lineage events SPLIT / MERGED (Phase 8)
# record consumption and descent inside ONE Area at a Station and
# reference no Machine: the Machine and the holding state of the
# quantity they carry are derived by following the lineage to the last
# position-bearing Movement, never re-stated on the lineage row.
# Phase 9: SCRAPPED removes quantity from active production inside ONE
# Area at a Station and may name the source Machine the quantity left
# (never a destination Machine); QUANTITY_ADJUSTED introduces quantity
# (no source Area, like RECEIVED) but is always scan-driven (Station
# NOT NULL) and references no Machine; REVERSED records the
# compensating motion of a command-level Undo at a Station — its
# from/to pair may cross Areas (reversing a TRANSFERRED) or stay in one
# (reversing an in-Area event), and it never re-states a Machine: the
# restored state is derived by EXCLUDING the reversed pair from the
# derivations, so re-stating it here could only drift. Phase 10:
# STOCKED records the scan of quantity into the terminal Stockroom
# Area exactly like a TRANSFERRED — two DIFFERENT Areas at a Station,
# no Machine (actively processing quantity is preceded by its own
# AREA_COMPLETED); the terminal flag of the destination is an Area
# configuration rule the Application layer judges under the Area lock.
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
    " OR (movement_type = 'STOCKED' AND from_area_id IS NOT NULL"
    " AND from_area_id <> to_area_id AND station_id IS NOT NULL"
    " AND source_machine_id IS NULL AND destination_machine_id IS NULL)"
)

# Typed movement intent (PROJECT_PROFILE §8.11, §14): the only Phase 9
# value is REPAIR, and it exists only on a TRANSFERRED Movement.
MOVEMENT_REASON_SQL = (
    "movement_reason IS NULL OR (movement_reason = 'REPAIR' AND movement_type = 'TRANSFERRED')"
)

# The free-text explanation is mandatory exactly where the domain
# requires one (PROJECT_PROFILE §8.11): Scrap, quantity adjustment, and
# every Movement carrying a typed movement_reason (Repair). Other
# Movements may carry none; a `REVERSED` row carries one when the Undo
# command received it, and the command requires it while the Undo
# reason policy is on (`application_policy.undo_reason_required`,
# Phase 13) — configuration-dependent, so never a CHECK.
MOVEMENT_REASON_REQUIRED_SQL = (
    "reason IS NOT NULL"
    " OR (movement_type NOT IN ('SCRAPPED', 'QUANTITY_ADJUSTED')"
    " AND movement_reason IS NULL)"
)

# A REVERSED Movement references exactly the original it compensates
# (PROJECT_PROFILE §16); no other type references one.
MOVEMENT_REVERSES_SQL = "(movement_type = 'REVERSED') = (reverses_movement_id IS NOT NULL)"

# A reversal takes an allocation back and always says why (PROJECT_PROFILE
# §8.12 "every adjustment must be auditable"); an allocation row may
# carry an optional reason.
ALLOCATION_REVERSAL_REASON_SQL = "reverses_allocation_id IS NULL OR allocation_reason IS NOT NULL"

# Worker identification (Phase 13 slice 3; PROJECT_PROFILE §8.4, §8.13,
# §19): the Area's Worker ID mode in its full canonical vocabulary, and
# a Fixed Worker that exists exactly in FIXED mode. Repeated verbatim by
# migration `0019_phase13_worker_identity`.
WORKER_IDENTIFICATION_MODE_SQL = (
    "worker_identification_mode IN ("
    + ", ".join(f"'{mode}'" for mode in WorkerIdentificationMode)
    + ")"
)
AREA_FIXED_WORKER_SQL = "(worker_identification_mode = 'FIXED') = (fixed_worker_id IS NOT NULL)"

# Production audit identity (Phase 13 slice 3, PLAN CD4): a Worker is
# only ever recorded by a Scan Station command — a Management Movement
# or allocation (no station) never carries one. Repeated verbatim by
# migration `0019_phase13_worker_identity`.
MOVEMENT_WORKER_STATION_SQL = "worker_id IS NULL OR station_id IS NOT NULL"
ALLOCATION_WORKER_STATION_SQL = "allocated_by_worker_id IS NULL OR station_id IS NOT NULL"

# Worker Session timeout policy (Phase 13 slice 4; owner decision OD-2):
# whole minutes, 1-720 for the global default and for every per-Area
# override (NULL = use the default), default 15. Repeated verbatim by
# migration `0020_phase13_worker_sessions`.
WORKER_SESSION_TIMEOUT_MIN = 1
WORKER_SESSION_TIMEOUT_MAX = 720
WORKER_SESSION_TIMEOUT_DEFAULT = 15
POLICY_WORKER_SESSION_TIMEOUT_SQL = "worker_session_timeout_minutes BETWEEN 1 AND 720"
AREA_WORKER_SESSION_TIMEOUT_SQL = (
    "worker_session_timeout_minutes IS NULL OR worker_session_timeout_minutes BETWEEN 1 AND 720"
)

# The badge-confirmation options of the sensitive Scan Station actions
# (Phase 13 slice 5, PROJECT_PROFILE §19): one boolean `application_policy`
# column per action, default on. Repeated verbatim by migration
# `0021_phase13_badge_confirmation`.
BADGE_CONFIRMATION_OPTIONS = ("badge_confirm_done", "badge_confirm_queue", "badge_confirm_undo")

# Department display settings (Phase 13 slice 9, PROJECT_PROFILE §21):
# the Production Board rotation timing per Department — whole seconds per
# displayed row (1-60, default 3) and the minimum page dwell (1-300,
# default 6). Repeated verbatim by migration
# `0025_phase13_display_settings`.
BOARD_SECONDS_PER_ROW_MIN = 1
BOARD_SECONDS_PER_ROW_MAX = 60
BOARD_SECONDS_PER_ROW_DEFAULT = 3
BOARD_MIN_PAGE_SECONDS_MIN = 1
BOARD_MIN_PAGE_SECONDS_MAX = 300
BOARD_MIN_PAGE_SECONDS_DEFAULT = 6
DEPARTMENT_BOARD_SECONDS_PER_ROW_SQL = "board_seconds_per_row BETWEEN 1 AND 60"
DEPARTMENT_BOARD_MIN_PAGE_SECONDS_SQL = "board_min_page_seconds BETWEEN 1 AND 300"

# The Due Soon warning policy (Phase 13 slice 9; GUI_DESIGN §3 rule 12,
# §9; owner default OD-5): whole days 0-365 for both clamps (minimum never
# above maximum) and a whole lead-time percentage 1-100; defaults 2 days,
# 15 %, 7 days. Repeated verbatim by migration
# `0025_phase13_display_settings`.
DUE_SOON_DAYS_MIN = 0
DUE_SOON_DAYS_MAX = 365
DUE_SOON_PERCENT_MIN = 1
DUE_SOON_PERCENT_MAX = 100
DUE_SOON_MIN_DAYS_DEFAULT = 2
DUE_SOON_LEAD_TIME_PERCENT_DEFAULT = 15
DUE_SOON_MAX_DAYS_DEFAULT = 7
POLICY_DUE_SOON_MIN_DAYS_SQL = "due_soon_min_days BETWEEN 0 AND 365"
POLICY_DUE_SOON_MAX_DAYS_SQL = "due_soon_max_days BETWEEN 0 AND 365"
POLICY_DUE_SOON_PERCENT_SQL = "due_soon_lead_time_percent BETWEEN 1 AND 100"
POLICY_DUE_SOON_ORDER_SQL = "due_soon_min_days <= due_soon_max_days"

# Worker Session rows (Phase 13 slice 4, PROJECT_PROFILE §19, §28): the
# closed end-reason vocabulary, an end time exactly with an end reason,
# an expiry after the start, an end inside the session's window, and an
# already-expired session closed EXPIRED at its expiry — never with a
# configuration reason. Repeated verbatim by migration
# `0020_phase13_worker_sessions`.
WORKER_SESSION_END_REASON_SQL = (
    "end_reason IN (" + ", ".join(f"'{reason}'" for reason in WorkerSessionEndReason) + ")"
)
WORKER_SESSION_END_SHAPE_SQL = "(ended_at IS NULL) = (end_reason IS NULL)"
WORKER_SESSION_EXPIRY_AFTER_START_SQL = "expires_at > started_at"
WORKER_SESSION_END_WITHIN_WINDOW_SQL = (
    "ended_at IS NULL OR (ended_at >= started_at AND ended_at <= expires_at)"
)
WORKER_SESSION_EXPIRED_AT_EXPIRY_SQL = (
    "end_reason IS DISTINCT FROM 'EXPIRED' OR ended_at = expires_at"
)

# A Movement names a Worker Session only together with its Worker
# (PLAN CD4). With the Worker ⇒ station CHECK above, all three columns
# of the composite session FK are then set, so MATCH SIMPLE always
# checks it. Repeated verbatim by migration `0020_phase13_worker_sessions`.
MOVEMENT_SESSION_WORKER_SQL = "scan_session_id IS NULL OR worker_id IS NOT NULL"

# Row-level idempotency guarantee of the application-command model
# (Phase 6): one `device_event_id` identifies one command, which may
# append several Movements numbered by `command_sequence`. Referenced
# by the commands that translate a race lost at COMMIT into a replay.
DEVICE_EVENT_ID_CONSTRAINT = "uq_part_movements_device_event_id_command_sequence"
# The same guarantee for the allocation command (Phase 10).
ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT = "uq_work_order_allocations_device_event_id_command_sequence"

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
    """Major organizational unit owning Areas (PROJECT_PROFILE §7).

    Department display settings (Phase 13): the Production Board rotation
    timing — whole seconds per displayed row and the minimum page dwell —
    configured per Department, never globally (PROJECT_PROFILE §21).
    """

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    board_seconds_per_row: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3")
    )
    board_min_page_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("6")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_departments_name"),
        CheckConstraint(
            DEPARTMENT_BOARD_SECONDS_PER_ROW_SQL,
            name=conv("ck_departments_board_seconds_per_row_range"),
        ),
        CheckConstraint(
            DEPARTMENT_BOARD_MIN_PAGE_SECONDS_SQL,
            name=conv("ck_departments_board_min_page_seconds_range"),
        ),
    )


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
    # Worker ID mode (Phase 13 slice 3, PROJECT_PROFILE §8.4/§8.13):
    # how the Area's Scan Station commands identify their Worker. The
    # server default makes every insert path, fixtures included, Disabled.
    worker_identification_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'DISABLED'")
    )
    # The configured Fixed Worker — set exactly in FIXED mode (CHECK).
    # Workers are never deleted, so the FK never needs a delete action.
    fixed_worker_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workers.id", name="fk_areas_fixed_worker_id_workers")
    )
    # The Area's Worker Session timeout override in whole minutes
    # (Phase 13 slice 4, PROJECT_PROFILE §19 "optional per-Area
    # overrides"); NULL uses the `application_policy` default.
    worker_session_timeout_minutes: Mapped[int | None] = mapped_column(Integer)
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
        CheckConstraint(
            WORKER_IDENTIFICATION_MODE_SQL, name=conv("ck_areas_worker_identification_mode")
        ),
        CheckConstraint(AREA_FIXED_WORKER_SQL, name=conv("ck_areas_fixed_worker_shape")),
        CheckConstraint(
            AREA_WORKER_SESSION_TIMEOUT_SQL, name=conv("ck_areas_worker_session_timeout_range")
        ),
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
    ``theme_preference`` (Phase 13 slice 10) is the station's own
    Dark/Light display preference — the station tier of GUI_DESIGN §2.1;
    NULL = no preference. Not configuration: never audited (OD-13), never
    part of the audit snapshot, never changes ``updated_at``.
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
    theme_preference: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(SCAN_STATION_ID_SQL, name=conv("ck_scan_stations_station_id_canonical")),
        CheckConstraint(
            SCAN_STATION_THEME_PREFERENCE_SQL, name=conv("ck_scan_stations_theme_preference")
        ),
    )


class Worker(Base):
    """Scan Station production audit identity (PROJECT_PROFILE §7 Worker, §8.13).

    A person operating the Scan Stations — never a User (an application
    account): the two identities are never merged. The badge barcode is
    the company's existing employee badge, stored and matched exactly in
    its canonical form (trimmed, UPPERCASE; owner decision OD-3), unique
    among ALL Workers including inactive ones. Workers are deactivated,
    never deleted (owner-decision default OD-14). The optional avatar is
    stored on the row (CD1): its bytes are mapped deferred, so no list or
    lock query loads them; type and timestamp travel with every read.
    """

    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    badge_barcode: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_image: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    avatar_image_type: Mapped[str | None] = mapped_column(Text)
    # The avatar's cache version: drives the ETag and the `?v=` URL.
    avatar_image_updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Every stored value is canonical (CHECK below), so this plain
        # UNIQUE already gives case-insensitive uniqueness.
        UniqueConstraint("badge_barcode", name="uq_workers_badge_barcode"),
        CheckConstraint(WORKER_BADGE_BARCODE_SQL, name=conv("ck_workers_badge_barcode_canonical")),
        # An avatar is all three columns or none.
        CheckConstraint(
            "(avatar_image IS NULL) = (avatar_image_type IS NULL)"
            " AND (avatar_image IS NULL) = (avatar_image_updated_at IS NULL)",
            name=conv("ck_workers_avatar_image_shape"),
        ),
        CheckConstraint(
            "avatar_image_type IN ('image/png', 'image/jpeg', 'image/webp')",
            name=conv("ck_workers_avatar_image_type"),
        ),
        CheckConstraint(
            "avatar_image IS NULL OR octet_length(avatar_image) BETWEEN 1 AND 2097152",
            name=conv("ck_workers_avatar_image_size"),
        ),
    )


class WorkerSession(Base):
    """One scanned Worker Session at one Scan Station (PROJECT_PROFILE §9, §19, §28).

    The PROFILE's `ScanSession`, stored as `worker_sessions`. A row is
    the audit trail of one session: who signed in at which station (and
    the station's Area at sign-in), when, until when it is valid
    (`expires_at`, the sliding inactivity deadline), and when and why it
    ended. It is accountability metadata — never production truth.

    A mutation-guard trigger owned by migration
    `0020_phase13_worker_sessions` forbids DELETE and TRUNCATE and lets
    only an OPEN session's `expires_at`, `ended_at` and `end_reason`
    change. At most one session per station is open (partial UNIQUE).
    An open row past its expiry is an expired, not yet closed session
    every reader ignores; the next sign-in or configuration close at the
    station closes it EXPIRED at its expiry.

    `part_movements.scan_session_id` is the authoritative link from a
    Movement to its session — a Movement's `occurred_at` (transaction
    start) may precede its session's `started_at` by the command's lock
    wait, so time windows must never be used to place a Movement in a
    session.
    """

    __tablename__ = "worker_sessions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("scan_stations.station_id", name="fk_worker_sessions_station_id_scan_stations"),
        nullable=False,
    )
    # The station's Area at sign-in (reporting); a rebind closes the session.
    area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("areas.id", name="fk_worker_sessions_area_id_areas"), nullable=False
    )
    worker_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workers.id", name="fk_worker_sessions_worker_id_workers"),
        nullable=False,
    )
    # Written by the Application from the session clock — no server default.
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(WORKER_SESSION_END_REASON_SQL, name=conv("ck_worker_sessions_end_reason")),
        CheckConstraint(WORKER_SESSION_END_SHAPE_SQL, name=conv("ck_worker_sessions_end_shape")),
        CheckConstraint(
            WORKER_SESSION_EXPIRY_AFTER_START_SQL,
            name=conv("ck_worker_sessions_expiry_after_start"),
        ),
        CheckConstraint(
            WORKER_SESSION_END_WITHIN_WINDOW_SQL,
            name=conv("ck_worker_sessions_end_within_window"),
        ),
        CheckConstraint(
            WORKER_SESSION_EXPIRED_AT_EXPIRY_SQL,
            name=conv("ck_worker_sessions_expired_at_expiry"),
        ),
        # Target of the Movement's composite session FK (PLAN CD4).
        UniqueConstraint(
            "id", "worker_id", "station_id", name="uq_worker_sessions_id_worker_id_station_id"
        ),
        # At most one open session per station (CD5).
        Index(
            "uq_worker_sessions_open_station",
            "station_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
        # A Worker deactivation closes that Worker's open sessions.
        Index(
            "ix_worker_sessions_open_worker",
            "worker_id",
            postgresql_where=text("ended_at IS NULL"),
        ),
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
    never associated with these events. Machine configuration writes are
    audited in `audit_events` (Phase 13); lifecycle transitions are
    recorded only here.
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


class ApplicationPolicy(Base):
    """The global application policy singleton (Phase 13 slice 4, PLAN CD3).

    One row (CHECK id = 1), seeded by migration
    `0020_phase13_worker_sessions` with the approved defaults, so it
    always exists. Each global policy is a typed column with a server
    default — a later slice adds its policy the same way; there is no
    key/value store. Per-record overrides live on their owner (the
    Area's `worker_session_timeout_minutes`).

    Slice 5 adds the three badge-confirmation options of the sensitive
    Scan Station actions (PROJECT_PROFILE §19; default on) — they decide
    only the FORM of the always-present final gate in Scanned-session
    Areas (`station_identity.final_gate`).

    Slice 6 adds the Undo reason policy of Administration → Correction
    permissions (PROJECT_PROFILE §16 "require a reason when configured";
    owner default OD-6: one global switch, default off).

    Slice 9 adds the Due Soon warning policy (Administration → Settings;
    GUI_DESIGN §3 rule 12, §9; owner default OD-5) behind every derived
    due countdown: the minimum and maximum warning days and the lead-time
    warning percentage. No production command reads it.
    """

    __tablename__ = "application_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # The sliding Worker Session inactivity timeout (OD-2), whole minutes.
    worker_session_timeout_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("15")
    )
    # Badge confirmation of DONE, QUEUE return and Undo (BADGE_CONFIRMATION_OPTIONS).
    badge_confirm_done: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    badge_confirm_queue: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    badge_confirm_undo: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    # The Undo reason policy: while on, the Undo command refuses a reversal
    # without a reason. Enforced by the command — never by a CHECK, which
    # cannot depend on configuration.
    undo_reason_required: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    # The Due Soon warning window: the lead-time percentage (whole percent,
    # never a float ratio) of the received → due lead time, clamped into
    # [min days, max days].
    due_soon_min_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2")
    )
    due_soon_lead_time_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("15")
    )
    due_soon_max_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("7")
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("id = 1", name=conv("ck_application_policy_singleton")),
        CheckConstraint(
            POLICY_WORKER_SESSION_TIMEOUT_SQL,
            name=conv("ck_application_policy_worker_session_timeout_range"),
        ),
        CheckConstraint(
            POLICY_DUE_SOON_MIN_DAYS_SQL,
            name=conv("ck_application_policy_due_soon_min_days_range"),
        ),
        CheckConstraint(
            POLICY_DUE_SOON_MAX_DAYS_SQL,
            name=conv("ck_application_policy_due_soon_max_days_range"),
        ),
        CheckConstraint(
            POLICY_DUE_SOON_PERCENT_SQL,
            name=conv("ck_application_policy_due_soon_lead_time_percent_range"),
        ),
        CheckConstraint(
            POLICY_DUE_SOON_ORDER_SQL,
            name=conv("ck_application_policy_due_soon_window_order"),
        ),
    )


class PartNumber(Base):
    """Optional current-metadata master for a canonical PN (PROJECT_PROFILE §8.1).

    The canonical PN string is the natural primary key — no surrogate
    `part_number_id` exists anywhere. Production tables never reference
    this table, so a master row can be hard-deleted (and later recreated
    for the same canonical PN) without touching production data.

    Optional metadata (Phase 13 slice 7): the free-text Name /
    Description, the informational current revision and the ERP id —
    none unique, none looked up — plus the PN image stored on the row
    (CD1). The image bytes are mapped deferred, so no list, lock or
    read-model query loads them; type and timestamp travel with every
    read.
    """

    __tablename__ = "part_numbers"

    part_number: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    current_revision: Mapped[str | None] = mapped_column(Text)
    erp_id: Mapped[str | None] = mapped_column(Text)
    image: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    image_type: Mapped[str | None] = mapped_column(Text)
    # The image's cache version: drives the ETag and the `?v=` URL.
    image_updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
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
        CheckConstraint(PART_NUMBER_IMAGE_SHAPE_SQL, name=conv("ck_part_numbers_image_shape")),
        CheckConstraint(PART_NUMBER_IMAGE_TYPE_SQL, name=conv("ck_part_numbers_image_type")),
        CheckConstraint(PART_NUMBER_IMAGE_SIZE_SQL, name=conv("ck_part_numbers_image_size")),
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
    # The done date (Phase 10, PROJECT_PROFILE §8.2): the timestamp of
    # the allocation event that fully allocated the last open demand
    # line — a maintained projection of the allocation records, set and
    # cleared inside the allocation transaction, never entered by hand,
    # and rebuildable from `work_order_allocations` alone. NULL while
    # the Work Order is active. Indexed for the keyset-paged completed
    # history ordered by (completed_at, id).
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index(
            "ix_work_orders_completed_at_id",
            "completed_at",
            "id",
            postgresql_where=text("completed_at IS NOT NULL"),
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
    # Maintained projection of the demand's ACTIVE allocation (Phase 10):
    # the sum of its effective `work_order_allocations` rows, updated
    # inside the allocation transaction under the demand row lock and
    # rebuildable from those rows alone — the rows stay the source of
    # truth (PROJECT_PROFILE §8.2 "the allocation records remain the
    # source of truth").
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
        # Phase 12 Hot list (invariant H1): ranks are positive and
        # unique — NULL (unranked) any number of times. The writers are
        # the Hot list command and hot_ranks.remove_from_hot_list; both
        # keep exactly 1..N.
        CheckConstraint(
            "priority_rank IS NULL OR priority_rank >= 1",
            name=conv("ck_work_order_demands_priority_rank_positive"),
        ),
        UniqueConstraint("priority_rank", name="uq_work_order_demands_priority_rank"),
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
    """Ordered expected step of a RouteTemplate (PROJECT_PROFILE §8.9).

    The preferred Machine is referenced by stable id (advisory,
    PROJECT_PROFILE §8.9); it is validated against the step's Area at
    save time only — a Machine may later move Areas or retire, and the
    stale preference is then displayed, never silently cleared.
    """

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
    preferred_machine_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machines.id", name="fk_route_steps_preferred_machine_id_machines"),
    )
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

    __table_args__ = (
        # Usage lists, per-template usage counts, the ever-used check and
        # the FK check of a template delete are all per template.
        Index("ix_assigned_routes_source_route_template_id", "source_route_template_id"),
    )


class AssignedRouteStep(Base):
    """Snapshot copy of a route step, independent of the mutable template.

    Copies every template step field, including `preferred_machine_id`
    (a plain integer, no FK — lock order, S8-OD20).
    """

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
    # Advisory copy of the template step's preferred Machine (OD-11).
    # Deliberately NO foreign key (S8-OD20): the snapshot INSERT runs
    # after the release/receipt starting Area or a split/merge Machine is
    # locked FOR UPDATE, and an FK check would take FOR KEY SHARE on
    # Machine rows in an order production commands never use. The value
    # is copied from the FK-checked `route_steps` column (or another
    # snapshot) and Machines are never deleted, so it cannot dangle.
    preferred_machine_id: Mapped[int | None] = mapped_column(Integer)
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
    `ASSIGNED_TO_MACHINE`). Lineage (Phase 8) is not a column here: a
    consumed flow closes (`status` SPLIT / MERGED with `closed_at`) and
    the `quantity_flow_lineage` edges name its children — one parent to
    several children for a SPLIT, several parents to one child for a
    MERGED — so both directions reconstruct without a single
    `parent_flow_id` that could not express N → 1.
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
            "status IN (" + ", ".join(f"'{status}'" for status in QuantityFlowStatus) + ")",
            name=conv("ck_quantity_flows_status"),
        ),
        # A flow is closed exactly when it left ACTIVE (Phase 8): the
        # closure timestamp and the status can never disagree.
        CheckConstraint(
            f"(status = '{QuantityFlowStatus.ACTIVE}') = (closed_at IS NULL)",
            name=conv("ck_quantity_flows_status_closed_at"),
        ),
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
    one application command — one `device_event_id` per command;
    `movement_reason` / `reason` / `reverses_movement_id` (Phase 9) are
    the typed movement intent (REPAIR), the mandatory free-text
    explanation of Repair/Scrap/quantity adjustments, and the original
    Movement a compensating `REVERSED` row undoes — at most one
    reversal per original (UNIQUE), so a command can never be undone
    twice. `worker_id` (Phase 13) is the Worker the station's Area mode
    identified when the command was recorded — accountability metadata
    only, NULL for Management Movements and for history recorded before
    Phase 13 (never backfilled); `scan_session_id` (Phase 13) is the
    scanned Worker Session the command was recorded under (table
    `worker_sessions`; NULL outside Scanned session mode and for history
    before it — never backfilled); the composite FK pins it to the same
    Worker and station. It is the only link to the session:
    `occurred_at` may precede the session's `started_at`.
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
    # The Worker the station's Area mode identified (Phase 13 slice 3,
    # PROJECT_PROFILE §8.11): accountability metadata only — never read
    # by any production rule. Recorded only with a station (CHECK).
    worker_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workers.id", name="fk_part_movements_worker_id_workers")
    )
    # The scanned Worker Session the command was recorded under (Phase 13
    # slice 4) — pinned to the same Worker and station by the composite FK.
    scan_session_id: Mapped[int | None] = mapped_column(BigInteger)
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
    # Typed movement intent (Phase 9, PROJECT_PROFILE §8.11/§14): REPAIR
    # on a TRANSFERRED marks the explicit return of quantity to a
    # previously visited Area — never inferred, never a Request Type.
    movement_reason: Mapped[str | None] = mapped_column(Text)
    # Free-text explanation (Phase 9): mandatory for Repair, Scrap and
    # quantity adjustments (CHECK); optional elsewhere.
    reason: Mapped[str | None] = mapped_column(Text)
    # The original Movement this compensating REVERSED row undoes
    # (Phase 9, PROJECT_PROFILE §16): set exactly on a REVERSED row, at
    # most one reversal per original — the original itself is never
    # touched.
    reverses_movement_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "part_movements.id", name="fk_part_movements_reverses_movement_id_part_movements"
        ),
    )
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
        CheckConstraint(MOVEMENT_REASON_SQL, name=conv("ck_part_movements_movement_reason")),
        CheckConstraint(
            MOVEMENT_REASON_REQUIRED_SQL, name=conv("ck_part_movements_reason_required")
        ),
        CheckConstraint(MOVEMENT_REVERSES_SQL, name=conv("ck_part_movements_reverses_shape")),
        CheckConstraint(
            MOVEMENT_WORKER_STATION_SQL, name=conv("ck_part_movements_worker_requires_station")
        ),
        CheckConstraint(
            MOVEMENT_SESSION_WORKER_SQL, name=conv("ck_part_movements_session_requires_worker")
        ),
        # A Movement can never name another Worker's or another
        # station's session (PLAN CD4).
        ForeignKeyConstraint(
            ["scan_session_id", "worker_id", "station_id"],
            ["worker_sessions.id", "worker_sessions.worker_id", "worker_sessions.station_id"],
            name="fk_part_movements_scan_session_worker_sessions",
        ),
        UniqueConstraint("device_event_id", "command_sequence", name=DEVICE_EVENT_ID_CONSTRAINT),
        # At most one reversal per original Movement (PROJECT_PROFILE
        # §16): the database, not only the eligibility check, refuses a
        # second Undo of the same command — including a race between
        # two concurrent Undo submissions.
        UniqueConstraint("reverses_movement_id", name="uq_part_movements_reverses_movement_id"),
        Index("ix_part_movements_quantity_flow_id_id", "quantity_flow_id", "id"),
        # The per-PN reverse-chronological history read of PN Tracking
        # (Phase 11): `(occurred_at DESC, id DESC)` with keyset paging.
        Index(
            "ix_part_movements_part_number_occurred_at_id",
            "part_number",
            "occurred_at",
            "id",
        ),
    )


class QuantityFlowLineage(Base):
    """One descent edge between QuantityFlows (Phase 8; PROJECT_PROFILE §8.7, §11).

    Append-only (raise-on-write trigger owned by migration 0009). A
    SPLIT command writes one edge per child (1 → N), a MERGED command
    one edge per consumed source (N → 1); `device_event_id` names the
    application command that recorded the edge — the same id its
    `SPLIT` / `MERGED` Movements carry — so the command, its Movements
    and its edges are one auditable unit. The parent's consumption and
    the child's descent are the immutable Movements; this table is the
    queryable graph that rebuilds ancestry and active descendants.
    """

    __tablename__ = "quantity_flow_lineage"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    parent_flow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "quantity_flows.id", name="fk_quantity_flow_lineage_parent_flow_id_quantity_flows"
        ),
        nullable=False,
    )
    child_flow_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "quantity_flows.id", name="fk_quantity_flow_lineage_child_flow_id_quantity_flows"
        ),
        nullable=False,
    )
    device_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "relation IN (" + ", ".join(f"'{relation}'" for relation in LineageRelation) + ")",
            name=conv("ck_quantity_flow_lineage_relation"),
        ),
        CheckConstraint(
            "parent_flow_id <> child_flow_id",
            name=conv("ck_quantity_flow_lineage_parent_child_distinct"),
        ),
        UniqueConstraint(
            "parent_flow_id",
            "child_flow_id",
            name="uq_quantity_flow_lineage_parent_flow_id_child_flow_id",
        ),
        Index("ix_quantity_flow_lineage_parent_flow_id", "parent_flow_id"),
        Index("ix_quantity_flow_lineage_child_flow_id", "child_flow_id"),
    )


class WorkOrderAllocation(Base):
    """One append-only allocation event of stocked PN quantity to a demand
    (Phase 10; PROJECT_PROFILE §8.12, §18).

    Allocation is independent from PartMovement by design: a row never
    references a Movement or a QuantityFlow, only the canonical PN
    (kept by value, like every production record) and the demand it
    serves. Rows are immutable (raise-on-write trigger owned by
    migration 0011) and come in two kinds: an ALLOCATION row adds
    `quantity` to the demand's active allocation; a REVERSAL row
    (`reverses_allocation_id` set — FK, UNIQUE: at most one reversal
    per allocation, so a correction can never be applied twice, even
    by a race) takes exactly the referenced allocation's quantity back
    out, with a mandatory reason (CHECK). The ACTIVE allocation of a
    demand — and of a PN — is therefore the sum of its allocation rows
    that no reversal references, derived, never a stored counter that
    could drift (`work_order_demands.allocated_quantity` is a
    maintained projection of exactly that sum). Available stocked
    quantity is `effective STOCKED quantity − active allocation`, both
    derived from history.

    `device_event_id` + `command_sequence` identify the application
    command that recorded the rows — the same idempotency model as
    `part_movements` (SLICE1 §14): one confirmation writes several
    rows under one id, replayed as a whole on a transport retry.
    `station_id` names the Stockroom Scan Station of a receiving
    confirmation (NULL for a Management allocation or adjustment);
    `actor_reference` stays a nullable, reference-free value until
    authentication exists (Phase 14); `allocated_by_worker_id` (Phase 13)
    is the Worker identified at the Stockroom station; NULL for
    Management rows.
    """

    __tablename__ = "work_order_allocations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    part_number: Mapped[str] = mapped_column(Text, nullable=False)
    work_order_demand_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "work_order_demands.id",
            name="fk_work_order_allocations_demand_id_work_order_demands",
        ),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # True when the confirmed quantity differs from the canonical
    # suggestion the server computed at confirmation time (an Operator
    # adjustment, PROJECT_PROFILE §18) — audit context only.
    is_manual_override: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    allocation_reason: Mapped[str | None] = mapped_column(Text)
    reverses_allocation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "work_order_allocations.id",
            name="fk_work_order_allocations_reverses_allocation_id",
        ),
    )
    station_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "scan_stations.station_id", name="fk_work_order_allocations_station_id_scan_stations"
        ),
    )
    actor_reference: Mapped[str | None] = mapped_column(Text)
    # The Worker the Stockroom station's Area mode identified (Phase 13
    # slice 3, PROJECT_PROFILE §8.12): recorded only with a station (CHECK).
    allocated_by_worker_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workers.id", name="fk_work_order_allocations_allocated_by_worker_id_workers"),
    )
    allocated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    device_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    command_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    __table_args__ = (
        CheckConstraint(
            CANONICAL_PART_NUMBER_SQL,
            name=conv("ck_work_order_allocations_part_number_canonical"),
        ),
        CheckConstraint("quantity > 0", name=conv("ck_work_order_allocations_quantity_positive")),
        CheckConstraint(
            "source IN (" + ", ".join(f"'{source}'" for source in AllocationSource) + ")",
            name=conv("ck_work_order_allocations_source"),
        ),
        CheckConstraint(
            ALLOCATION_REVERSAL_REASON_SQL,
            name=conv("ck_work_order_allocations_reversal_reason_required"),
        ),
        CheckConstraint(
            "command_sequence >= 1",
            name=conv("ck_work_order_allocations_command_sequence_positive"),
        ),
        CheckConstraint(
            ALLOCATION_WORKER_STATION_SQL,
            name=conv("ck_work_order_allocations_worker_requires_station"),
        ),
        UniqueConstraint(
            "reverses_allocation_id", name="uq_work_order_allocations_reverses_allocation_id"
        ),
        UniqueConstraint(
            "device_event_id",
            "command_sequence",
            name=ALLOCATION_DEVICE_EVENT_ID_CONSTRAINT,
        ),
        Index("ix_work_order_allocations_work_order_demand_id", "work_order_demand_id"),
        Index("ix_work_order_allocations_part_number", "part_number"),
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

    Records master-data, business-demand and configuration changes
    only — WorkOrder, WorkOrderDemand, PartNumber, and (Phase 13)
    Worker, the environment configuration entities Department, Area,
    Operation, ScanStation and MachineAssetTagConfig (the Asset Tag
    format), Machine configuration (lifecycle transitions stay in
    `machine_lifecycle_events`), the global ApplicationPolicy and
    (slice 8) RouteTemplate — Planned Routes configuration, never the
    Assigned Route snapshots. Rows are descriptive history for
    display and accountability: never replayed to build state, never
    describing production actions (the `RECEIVED` PartMovement is the
    production audit record), and deliberately not an event-sourcing
    framework. `entity_id` is polymorphic text with no FK — the
    internal PK for WorkOrder/WorkOrderDemand/Worker/Department/Area/
    Operation/Machine/RouteTemplate, the canonical PN string for PartNumber, the
    stable Station ID for ScanStation, `"1"` for the singleton
    MachineAssetTagConfig and the Administration section
    (`worker-sessions`) for ApplicationPolicy;
    integrity is guaranteed by writing the audit row in
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
            f"event_type IN ('{AuditEventType.CREATED}', '{AuditEventType.UPDATED}',"
            f" '{AuditEventType.DELETED}')",
            name=conv("ck_audit_events_event_type"),
        ),
        CheckConstraint(
            f"entity_type IN ('{AuditEntityType.WORK_ORDER}',"
            f" '{AuditEntityType.WORK_ORDER_DEMAND}', '{AuditEntityType.PART_NUMBER}',"
            f" '{AuditEntityType.WORKER}', '{AuditEntityType.DEPARTMENT}',"
            f" '{AuditEntityType.AREA}', '{AuditEntityType.OPERATION}',"
            f" '{AuditEntityType.SCAN_STATION}', '{AuditEntityType.MACHINE_ASSET_TAG_CONFIG}',"
            f" '{AuditEntityType.MACHINE}', '{AuditEntityType.APPLICATION_POLICY}',"
            f" '{AuditEntityType.ROUTE_TEMPLATE}')",
            name=conv("ck_audit_events_entity_type"),
        ),
        # Per-entity history in write order.
        Index("ix_audit_events_entity_type_entity_id_id", "entity_type", "entity_id", "id"),
    )


# The idempotency lookup of the Hot list command (Phase 12): its audit
# rows ARE the idempotency record, found by the `device_event_id` in
# their `hot_list_change` metadata block. Declared after the class so
# the lookup (`app.application.hot_list`) emits literally this expression
# — the JSONB SUBSCRIPT form the planner matches, written with an
# explicit `->>` so it renders exactly as PostgreSQL stores it (the
# `.astext` spelling adds parentheses Alembic's compare reads as a
# different expression; the planner sees the same tree). Partial on
# WorkOrderDemand rows, the only entity the command audits. Created by
# migration `0013_phase12_priority`.
HOT_LIST_DEVICE_EVENT_ID = AuditEvent.metadata_["hot_list_change"].op("->>", return_type=Text)(
    "device_event_id"
)
Index(
    "ix_audit_events_hot_list_device_event_id",
    HOT_LIST_DEVICE_EVENT_ID,
    postgresql_where=AuditEvent.entity_type == AuditEntityType.WORK_ORDER_DEMAND,
)
