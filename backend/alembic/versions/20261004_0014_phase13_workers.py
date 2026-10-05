"""Phase 13 Workers registry: the `workers` table and the audit vocabulary.

Exactly the additive schema the Workers registry needs
(IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §7 Worker, §8.13,
§10):

- the `workers` table: a stable identity id, a required (not unique)
  name, the employee badge barcode, the optional avatar stored on the
  row (bytes, sniffed media type, and the timestamp that versions it),
  the active flag and the configuration timestamps.
  `uq_workers_badge_barcode` keeps every badge unique among ALL Workers,
  inactive ones included; `ck_workers_badge_barcode_canonical` admits
  only the canonical form (non-empty, trimmed, UPPERCASE, at most 128
  characters, outside the `PF:` namespace — owner decision OD-3), so the
  plain UNIQUE is case-insensitive. The avatar CHECKs keep the three
  avatar columns all-or-none, the type PNG/JPEG/WebP and the size at
  most 2 MiB (OD-10);
- the audit event CHECK widens with `DELETED` (hard deletes of
  master/configuration records; no writer exists in this revision —
  later Phase 13 slices add them);
- the audit entity CHECK widens with `Worker`.

Deliberate non-changes: no Area column (Worker ID mode, fixed Worker),
no Movement or allocation column, no session table, no index beyond the
UNIQUE, no trigger (Workers are mutable configuration; their history
lives in `audit_events`), and no writer of `DELETED`.

The downgrade REFUSES — it never deletes Worker configuration or
history: `audit_events` is append-only, so the narrower entity CHECK
cannot be re-created over `Worker` rows, and a non-empty `workers`
table refuses explicitly. Run it only against disposable development
and test databases.

Revision ID: 0014_phase13_workers
Revises: 0013_phase12_priority
Create Date: 2026-10-04

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_phase13_workers"
down_revision: str | None = "0013_phase12_priority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKERS = "workers"
_AUDIT = "audit_events"
_EVENT_TYPE_CHECK = "ck_audit_events_event_type"
_ENTITY_TYPE_CHECK = "ck_audit_events_entity_type"

_PHASE4_EVENT_TYPES = "event_type IN ('CREATED', 'UPDATED')"
_PHASE13_EVENT_TYPES = "event_type IN ('CREATED', 'UPDATED', 'DELETED')"
_PHASE4_ENTITY_TYPES = "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber')"
_PHASE13_ENTITY_TYPES = "entity_type IN ('WorkOrder', 'WorkOrderDemand', 'PartNumber', 'Worker')"

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts it equals
# `models.WORKER_BADGE_BARCODE_SQL`.
_WORKER_BADGE_BARCODE_SQL = (
    r"badge_barcode <> '' AND badge_barcode !~ '^\s|\s$'"
    " AND badge_barcode = upper(badge_barcode) AND char_length(badge_barcode) <= 128"
    " AND left(badge_barcode, 3) <> 'PF:'"
)


def upgrade() -> None:
    op.create_table(
        _WORKERS,
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("badge_barcode", sa.Text(), nullable=False),
        sa.Column("avatar_image", sa.LargeBinary(), nullable=True),
        sa.Column("avatar_image_type", sa.Text(), nullable=True),
        sa.Column("avatar_image_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workers"),
        sa.UniqueConstraint("badge_barcode", name="uq_workers_badge_barcode"),
        sa.CheckConstraint(
            _WORKER_BADGE_BARCODE_SQL, name=op.f("ck_workers_badge_barcode_canonical")
        ),
        sa.CheckConstraint(
            "(avatar_image IS NULL) = (avatar_image_type IS NULL)"
            " AND (avatar_image IS NULL) = (avatar_image_updated_at IS NULL)",
            name=op.f("ck_workers_avatar_image_shape"),
        ),
        sa.CheckConstraint(
            "avatar_image_type IN ('image/png', 'image/jpeg', 'image/webp')",
            name=op.f("ck_workers_avatar_image_type"),
        ),
        sa.CheckConstraint(
            "avatar_image IS NULL OR octet_length(avatar_image) BETWEEN 1 AND 2097152",
            name=op.f("ck_workers_avatar_image_size"),
        ),
    )

    op.drop_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, _PHASE13_EVENT_TYPES)
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PHASE13_ENTITY_TYPES)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. PostgreSQL re-validates the re-created Phase 4
    # CHECKs against existing rows, so any `Worker` (or `DELETED`)
    # audit row makes the downgrade fail loudly; `alembic/env.py` runs
    # in one transaction, so the database then stays at this revision.
    # The append-only `audit_events` history is never deleted.
    op.drop_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_ENTITY_TYPE_CHECK), _AUDIT, _PHASE4_ENTITY_TYPES)
    op.drop_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, type_="check")
    op.create_check_constraint(op.f(_EVENT_TYPE_CHECK), _AUDIT, _PHASE4_EVENT_TYPES)

    # Worker configuration is never dropped silently either.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM workers)"
        " THEN RAISE EXCEPTION 'workers holds Worker configuration; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_table(_WORKERS)
