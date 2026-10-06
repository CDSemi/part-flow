"""Phase 13: Part Number details (name, revision, ERP id) and the PN image.

Exactly the additive schema slice 7 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §8.1, §21, §28; owner decision OD-10):

- `part_numbers.name` — the GUI "Name / Description" (one free-text
  field), `current_revision` (informational only) and `erp_id` (free
  text, never looked up — ERP stays isolated). All nullable, no
  default, not unique, no length limit and no index: nothing is looked
  up by them;
- the PN image on the row (CD1): `image` (bytes), `image_type` and
  `image_updated_at` (the cache version), with the S1 avatar rules
  repeated as `ck_part_numbers_image_shape` (all three columns or
  none), `ck_part_numbers_image_type` (PNG/JPEG/WebP) and
  `ck_part_numbers_image_size` (1 byte to 2 MiB).

Every ADD COLUMN is nullable without a default, so the ALTER is
metadata-only; validating the three CHECKs scans `part_numbers` once.

Deliberate non-changes: no FK (production tables keep the PN by value
and never reference `part_numbers`, so a hard delete of the details can
never cascade), no index, no trigger, no backfill and no audit CHECK
change — `DELETED`, `UPDATED` and the `PartNumber` entity already
exist.

The downgrade REFUSES — it never deletes Part Number details: any row
carrying a name, revision, ERP id or image raises, and `alembic/env.py`
runs in one transaction, so the database then stays at this revision.
`PartNumber` audit rows (`UPDATED` / `DELETED`) stay valid at the older
revision and never block it. Run it only against disposable development
and test databases.

Revision ID: 0023_phase13_part_number_master
Revises: 0022_phase13_undo_reason_policy
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023_phase13_part_number_master"
down_revision: str | None = "0022_phase13_undo_reason_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PART_NUMBERS = "part_numbers"

_IMAGE_SHAPE_CHECK = "ck_part_numbers_image_shape"
_IMAGE_TYPE_CHECK = "ck_part_numbers_image_type"
_IMAGE_SIZE_CHECK = "ck_part_numbers_image_size"

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant.
_PART_NUMBER_IMAGE_SHAPE_SQL = (
    "(image IS NULL) = (image_type IS NULL) AND (image IS NULL) = (image_updated_at IS NULL)"
)
_PART_NUMBER_IMAGE_TYPE_SQL = "image_type IN ('image/png', 'image/jpeg', 'image/webp')"
_PART_NUMBER_IMAGE_SIZE_SQL = "image IS NULL OR octet_length(image) BETWEEN 1 AND 2097152"


def upgrade() -> None:
    op.add_column(_PART_NUMBERS, sa.Column("name", sa.Text(), nullable=True))
    op.add_column(_PART_NUMBERS, sa.Column("current_revision", sa.Text(), nullable=True))
    op.add_column(_PART_NUMBERS, sa.Column("erp_id", sa.Text(), nullable=True))
    op.add_column(_PART_NUMBERS, sa.Column("image", sa.LargeBinary(), nullable=True))
    op.add_column(_PART_NUMBERS, sa.Column("image_type", sa.Text(), nullable=True))
    op.add_column(
        _PART_NUMBERS, sa.Column("image_updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f(_IMAGE_SHAPE_CHECK), _PART_NUMBERS, _PART_NUMBER_IMAGE_SHAPE_SQL
    )
    op.create_check_constraint(op.f(_IMAGE_TYPE_CHECK), _PART_NUMBERS, _PART_NUMBER_IMAGE_TYPE_SQL)
    op.create_check_constraint(op.f(_IMAGE_SIZE_CHECK), _PART_NUMBERS, _PART_NUMBER_IMAGE_SIZE_SQL)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Part Number details are never dropped silently.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM part_numbers WHERE name IS NOT NULL"
        "  OR current_revision IS NOT NULL OR erp_id IS NOT NULL OR image IS NOT NULL)"
        " THEN RAISE EXCEPTION 'Part Number details exist; refusing to drop them';"
        " END IF; END $$;"
    )
    op.drop_constraint(op.f(_IMAGE_SIZE_CHECK), _PART_NUMBERS, type_="check")
    op.drop_constraint(op.f(_IMAGE_TYPE_CHECK), _PART_NUMBERS, type_="check")
    op.drop_constraint(op.f(_IMAGE_SHAPE_CHECK), _PART_NUMBERS, type_="check")
    op.drop_column(_PART_NUMBERS, "image_updated_at")
    op.drop_column(_PART_NUMBERS, "image_type")
    op.drop_column(_PART_NUMBERS, "image")
    op.drop_column(_PART_NUMBERS, "erp_id")
    op.drop_column(_PART_NUMBERS, "current_revision")
    op.drop_column(_PART_NUMBERS, "name")
