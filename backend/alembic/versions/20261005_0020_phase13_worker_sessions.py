"""Phase 13: Worker Sessions and the Worker Session timeout policy.

Exactly the additive schema slice 4 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §8.11, §9, §19, §28; owner decision OD-2):

- `application_policy` — the ONE typed singleton for global policies
  (CHECK `id = 1`), seeded here with the approved default so the row
  always exists: `worker_session_timeout_minutes` default 15, CHECK
  1-720. Later slices add their policies as typed columns with server
  defaults; there is no key/value store;
- `areas.worker_session_timeout_minutes` — the optional per-Area
  override (NULL = the default; CHECK 1-720), nullable without default,
  so the ALTER is metadata-only;
- `worker_sessions` — the PROFILE's `ScanSession`: one row per scanned
  Worker Session (station, the station's Area at sign-in, Worker,
  `started_at`, `expires_at`, `ended_at`, `end_reason`), with the
  closed end-reason vocabulary and the shape CHECKs (an end time exactly
  with a reason, an expiry after the start, an end inside the window,
  an expired session ended `EXPIRED` at its expiry); at most one open
  session per station (partial UNIQUE), the open-by-Worker partial
  index, `UNIQUE (id, worker_id, station_id)` as the composite FK
  target, and a mutation guard: DELETE and TRUNCATE raise, and an
  UPDATE may change only an OPEN row's `expires_at`, `ended_at` and
  `end_reason`;
- `part_movements.scan_session_id` — the session a Scan Station command
  recorded, nullable without default (the append-only trigger fires on
  DML only), with a CHECK that a session is only ever recorded with its
  Worker and the composite FK `(scan_session_id, worker_id, station_id)`
  → `worker_sessions (id, worker_id, station_id)`, so a Movement can
  never name another Worker's or another station's session. Validating
  the CHECK and the FK scans `part_movements` once under the ALTER lock;
- `ck_audit_events_entity_type` gains `ApplicationPolicy`.

Deliberate non-changes: no backfill — history recorded before this
revision keeps NULL `scan_session_id` forever; no index on
`scan_session_id` (no reader filters by it, and sessions are never
deleted, so no FK cascade scans); no session id on
`work_order_allocations` (PROJECT_PROFILE §8.12). The FKs keep the
default `NO ACTION`: stations, Areas and Workers are never deleted.

The downgrade REFUSES — it never deletes Worker Session history or
timeout configuration: any `worker_sessions` row, any Area override or a
policy value other than 15 raises, and an `ApplicationPolicy` audit row
makes the re-created narrower entity CHECK fail; `alembic/env.py` runs
in one transaction, so the database then stays at this revision. Run it
only against disposable development and test databases.

Revision ID: 0020_phase13_worker_sessions
Revises: 0019_phase13_worker_identity
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_phase13_worker_sessions"
down_revision: str | None = "0019_phase13_worker_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "application_policy"
_SESSIONS = "worker_sessions"
_AREAS = "areas"
_MOVEMENTS = "part_movements"
_AUDIT = "audit_events"

_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"
_AREA_TIMEOUT_CHECK = "ck_areas_worker_session_timeout_range"
_MOVEMENT_SESSION_CHECK = "ck_part_movements_session_requires_worker"
_MOVEMENT_SESSION_FK = "fk_part_movements_scan_session_worker_sessions"
_OPEN_STATION_INDEX = "uq_worker_sessions_open_station"
_OPEN_WORKER_INDEX = "ix_worker_sessions_open_worker"

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant.
_TIMEOUT_MIN, _TIMEOUT_MAX, _TIMEOUT_DEFAULT = 1, 720, 15  # OD-2
_POLICY_TIMEOUT_SQL = "worker_session_timeout_minutes BETWEEN 1 AND 720"
_AREA_TIMEOUT_SQL = (
    "worker_session_timeout_minutes IS NULL OR worker_session_timeout_minutes BETWEEN 1 AND 720"
)
_END_REASON_SQL = (
    "end_reason IN ('SWITCHED', 'EXPIRED', 'AREA_MODE_CHANGED', 'STATION_CHANGED',"
    " 'WORKER_DEACTIVATED')"
)
_END_SHAPE_SQL = "(ended_at IS NULL) = (end_reason IS NULL)"
_EXPIRY_AFTER_START_SQL = "expires_at > started_at"
_END_WITHIN_WINDOW_SQL = "ended_at IS NULL OR (ended_at >= started_at AND ended_at <= expires_at)"
_EXPIRED_AT_EXPIRY_SQL = "end_reason IS DISTINCT FROM 'EXPIRED' OR ended_at = expires_at"
_MOVEMENT_SESSION_WORKER_SQL = "scan_session_id IS NULL OR worker_id IS NOT NULL"
# Exactly what 0017 left; the downgrade restores it.
_MACHINE_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine')"
)
_POLICY_ENTITY_TYPES = (
    "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker',"
    " 'Department', 'Area', 'Operation', 'ScanStation', 'MachineAssetTagConfig',"
    " 'Machine', 'ApplicationPolicy')"
)

# One function for both triggers: the row trigger guards UPDATE and
# DELETE, the statement trigger TRUNCATE (which row triggers never see).
_GUARD_FUNCTION = """
CREATE FUNCTION partflow_worker_sessions_guard_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'worker_sessions rows are audit history: % is not permitted', TG_OP;
    END IF;
    IF OLD.ended_at IS NOT NULL
        OR NEW.id IS DISTINCT FROM OLD.id
        OR NEW.station_id IS DISTINCT FROM OLD.station_id
        OR NEW.area_id IS DISTINCT FROM OLD.area_id
        OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
        OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION 'worker_sessions: only an open session''s expiry and end may change';
    END IF;
    RETURN NEW;
END;
$$;
"""

_GUARD_ROW_TRIGGER = """
CREATE TRIGGER trg_worker_sessions_guard_mutation
BEFORE UPDATE OR DELETE ON worker_sessions
FOR EACH ROW EXECUTE FUNCTION partflow_worker_sessions_guard_mutation();
"""

_GUARD_TRUNCATE_TRIGGER = """
CREATE TRIGGER trg_worker_sessions_forbid_truncate
BEFORE TRUNCATE ON worker_sessions
FOR EACH STATEMENT EXECUTE FUNCTION partflow_worker_sessions_guard_mutation();
"""


def upgrade() -> None:
    op.create_table(
        _POLICY,
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column(
            "worker_session_timeout_minutes",
            sa.Integer(),
            server_default=sa.text(str(_TIMEOUT_DEFAULT)),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_policy"),
        sa.CheckConstraint("id = 1", name=op.f("ck_application_policy_singleton")),
        sa.CheckConstraint(
            _POLICY_TIMEOUT_SQL, name=op.f("ck_application_policy_worker_session_timeout_range")
        ),
    )
    # The row always exists (CD3), seeded with the approved default.
    op.execute("INSERT INTO application_policy (id) VALUES (1)")

    op.add_column(_AREAS, sa.Column("worker_session_timeout_minutes", sa.Integer(), nullable=True))
    op.create_check_constraint(op.f(_AREA_TIMEOUT_CHECK), _AREAS, _AREA_TIMEOUT_SQL)

    op.create_table(
        _SESSIONS,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("station_id", sa.Text(), nullable=False),
        sa.Column("area_id", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(_END_REASON_SQL, name=op.f("ck_worker_sessions_end_reason")),
        sa.CheckConstraint(_END_SHAPE_SQL, name=op.f("ck_worker_sessions_end_shape")),
        sa.CheckConstraint(
            _EXPIRY_AFTER_START_SQL, name=op.f("ck_worker_sessions_expiry_after_start")
        ),
        sa.CheckConstraint(
            _END_WITHIN_WINDOW_SQL, name=op.f("ck_worker_sessions_end_within_window")
        ),
        sa.CheckConstraint(
            _EXPIRED_AT_EXPIRY_SQL, name=op.f("ck_worker_sessions_expired_at_expiry")
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["scan_stations.station_id"],
            name="fk_worker_sessions_station_id_scan_stations",
        ),
        sa.ForeignKeyConstraint(["area_id"], ["areas.id"], name="fk_worker_sessions_area_id_areas"),
        sa.ForeignKeyConstraint(
            ["worker_id"], ["workers.id"], name="fk_worker_sessions_worker_id_workers"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_worker_sessions"),
        sa.UniqueConstraint(
            "id", "worker_id", "station_id", name="uq_worker_sessions_id_worker_id_station_id"
        ),
    )
    op.create_index(
        _OPEN_STATION_INDEX,
        _SESSIONS,
        ["station_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        _OPEN_WORKER_INDEX,
        _SESSIONS,
        ["worker_id"],
        unique=False,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.execute(_GUARD_FUNCTION)
    op.execute(_GUARD_ROW_TRIGGER)
    op.execute(_GUARD_TRUNCATE_TRIGGER)

    op.add_column(_MOVEMENTS, sa.Column("scan_session_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f(_MOVEMENT_SESSION_CHECK), _MOVEMENTS, _MOVEMENT_SESSION_WORKER_SQL
    )
    op.create_foreign_key(
        _MOVEMENT_SESSION_FK,
        _MOVEMENTS,
        _SESSIONS,
        ["scan_session_id", "worker_id", "station_id"],
        ["id", "worker_id", "station_id"],
    )

    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _POLICY_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Worker Session history and timeout configuration
    # are never dropped silently; no `scan_session_id` value can exist
    # without a session row, so the first guard covers Movements too.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM worker_sessions)"
        " THEN RAISE EXCEPTION 'worker_sessions holds Worker Session history; refusing downgrade';"
        " END IF;"
        " IF EXISTS (SELECT 1 FROM areas WHERE worker_session_timeout_minutes IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM application_policy WHERE worker_session_timeout_minutes <> 15)"
        " THEN RAISE EXCEPTION 'Worker session timeout configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    # PostgreSQL re-validates the narrower CHECK against existing rows,
    # so an ApplicationPolicy audit row makes it fail loudly.
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _MACHINE_ENTITY_TYPES)

    op.drop_constraint(_MOVEMENT_SESSION_FK, _MOVEMENTS, type_="foreignkey")
    op.drop_constraint(op.f(_MOVEMENT_SESSION_CHECK), _MOVEMENTS, type_="check")
    op.drop_column(_MOVEMENTS, "scan_session_id")

    op.execute("DROP TRIGGER trg_worker_sessions_forbid_truncate ON worker_sessions;")
    op.execute("DROP TRIGGER trg_worker_sessions_guard_mutation ON worker_sessions;")
    op.execute("DROP FUNCTION partflow_worker_sessions_guard_mutation();")
    op.drop_index(_OPEN_WORKER_INDEX, table_name=_SESSIONS)
    op.drop_index(_OPEN_STATION_INDEX, table_name=_SESSIONS)
    op.drop_table(_SESSIONS)

    op.drop_constraint(op.f(_AREA_TIMEOUT_CHECK), _AREAS, type_="check")
    op.drop_column(_AREAS, "worker_session_timeout_minutes")

    op.drop_table(_POLICY)
