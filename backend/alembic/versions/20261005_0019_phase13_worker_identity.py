"""Phase 13: Area Worker ID modes and Worker identity on production records.

Exactly the additive schema slice 3 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §8.4, §8.11, §8.12, §8.13, §19):

- `areas.worker_identification_mode` — the Area's Worker ID mode in its
  full canonical vocabulary (`DISABLED` / `FIXED` / `SCANNED`), NOT NULL
  with the constant default `DISABLED`: today nothing is recorded, so
  every existing Area becomes Disabled, the truthful state (a constant
  default is metadata-only in PostgreSQL ≥ 11). `SCANNED` is admitted by
  the CHECK so the session slices enable it without a migration; the
  Area service refuses it until the badge gates exist;
- `areas.fixed_worker_id` — the configured Fixed Worker (FK to
  `workers`), present exactly in `FIXED` mode
  (`ck_areas_fixed_worker_shape`);
- `part_movements.worker_id` and
  `work_order_allocations.allocated_by_worker_id` — the Worker a Scan
  Station command recorded (nullable FKs to `workers`, no default, so
  the ALTER is metadata-only and the append-only triggers, which fire on
  DML only, are unaffected), each with a CHECK that a Worker is only
  ever recorded together with a Scan Station (PLAN CD4). Validating the
  two CHECKs and the FKs scans `part_movements` and
  `work_order_allocations` once under the ALTER locks.

Deliberate non-changes: no `scan_session_id` (Worker Sessions, a later
slice), no index (no reader filters by Worker, and Workers are never
deleted, so no FK cascade scans), no backfill of any existing row —
history recorded before this revision keeps NULL identity forever — no
trigger and no audit CHECK change (`Area` and `Worker` are already
audit entities). The FKs keep the default `NO ACTION` on delete:
Workers are deactivated, never deleted (OD-14).

The downgrade REFUSES — it never deletes production identity or Area
configuration: any recorded `worker_id` / `allocated_by_worker_id` or
any Area whose mode is not `DISABLED` raises, and `alembic/env.py` runs
in one transaction, so the database then stays at this revision. Run it
only against disposable development and test databases.

Revision ID: 0019_phase13_worker_identity
Revises: 0018_phase13_pn_check_collation
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_phase13_worker_identity"
down_revision: str | None = "0018_phase13_pn_check_collation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AREAS = "areas"
_MOVEMENTS = "part_movements"
_ALLOCATIONS = "work_order_allocations"
_WORKERS = "workers"

_AREA_MODE_CHECK = "ck_areas_worker_identification_mode"
_AREA_FIXED_WORKER_CHECK = "ck_areas_fixed_worker_shape"
_AREA_FIXED_WORKER_FK = "fk_areas_fixed_worker_id_workers"
_MOVEMENT_WORKER_FK = "fk_part_movements_worker_id_workers"
_MOVEMENT_WORKER_CHECK = "ck_part_movements_worker_requires_station"
_ALLOCATION_WORKER_FK = "fk_work_order_allocations_allocated_by_worker_id_workers"
_ALLOCATION_WORKER_CHECK = "ck_work_order_allocations_worker_requires_station"

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant.
_WORKER_IDENTIFICATION_MODE_SQL = "worker_identification_mode IN ('DISABLED', 'FIXED', 'SCANNED')"
_AREA_FIXED_WORKER_SQL = "(worker_identification_mode = 'FIXED') = (fixed_worker_id IS NOT NULL)"
_MOVEMENT_WORKER_STATION_SQL = "worker_id IS NULL OR station_id IS NOT NULL"
_ALLOCATION_WORKER_STATION_SQL = "allocated_by_worker_id IS NULL OR station_id IS NOT NULL"


def upgrade() -> None:
    op.add_column(
        _AREAS,
        sa.Column(
            "worker_identification_mode",
            sa.Text(),
            server_default=sa.text("'DISABLED'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(op.f(_AREA_MODE_CHECK), _AREAS, _WORKER_IDENTIFICATION_MODE_SQL)
    op.add_column(_AREAS, sa.Column("fixed_worker_id", sa.Integer(), nullable=True))
    op.create_foreign_key(_AREA_FIXED_WORKER_FK, _AREAS, _WORKERS, ["fixed_worker_id"], ["id"])
    op.create_check_constraint(op.f(_AREA_FIXED_WORKER_CHECK), _AREAS, _AREA_FIXED_WORKER_SQL)

    op.add_column(_MOVEMENTS, sa.Column("worker_id", sa.Integer(), nullable=True))
    op.create_foreign_key(_MOVEMENT_WORKER_FK, _MOVEMENTS, _WORKERS, ["worker_id"], ["id"])
    op.create_check_constraint(
        op.f(_MOVEMENT_WORKER_CHECK), _MOVEMENTS, _MOVEMENT_WORKER_STATION_SQL
    )

    op.add_column(_ALLOCATIONS, sa.Column("allocated_by_worker_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        _ALLOCATION_WORKER_FK, _ALLOCATIONS, _WORKERS, ["allocated_by_worker_id"], ["id"]
    )
    op.create_check_constraint(
        op.f(_ALLOCATION_WORKER_CHECK), _ALLOCATIONS, _ALLOCATION_WORKER_STATION_SQL
    )


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Recorded production identity and Area mode
    # configuration are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM part_movements WHERE worker_id IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM work_order_allocations"
        " WHERE allocated_by_worker_id IS NOT NULL)"
        " THEN RAISE EXCEPTION 'production records carry Worker identity; refusing downgrade';"
        " END IF;"
        " IF EXISTS (SELECT 1 FROM areas WHERE worker_identification_mode <> 'DISABLED')"
        " THEN RAISE EXCEPTION 'areas hold Worker ID mode configuration; refusing downgrade';"
        " END IF; END $$;"
    )

    op.drop_constraint(op.f(_ALLOCATION_WORKER_CHECK), _ALLOCATIONS, type_="check")
    op.drop_constraint(_ALLOCATION_WORKER_FK, _ALLOCATIONS, type_="foreignkey")
    op.drop_column(_ALLOCATIONS, "allocated_by_worker_id")

    op.drop_constraint(op.f(_MOVEMENT_WORKER_CHECK), _MOVEMENTS, type_="check")
    op.drop_constraint(_MOVEMENT_WORKER_FK, _MOVEMENTS, type_="foreignkey")
    op.drop_column(_MOVEMENTS, "worker_id")

    op.drop_constraint(op.f(_AREA_FIXED_WORKER_CHECK), _AREAS, type_="check")
    op.drop_constraint(op.f(_AREA_MODE_CHECK), _AREAS, type_="check")
    op.drop_constraint(_AREA_FIXED_WORKER_FK, _AREAS, type_="foreignkey")
    op.drop_column(_AREAS, "fixed_worker_id")
    op.drop_column(_AREAS, "worker_identification_mode")
