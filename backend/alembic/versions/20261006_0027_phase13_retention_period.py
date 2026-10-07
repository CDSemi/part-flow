"""Phase 13: the Movement-history retention period setting.

Exactly the additive schema slice 11 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §21 "retention settings belong in
Administration/configuration", §28 "the retention period is
configuration, never a hard-coded number of years in domain rules";
PLAN CD3; owner default OD-18):

- `application_policy.retention_period_months` (integer, NULL) — the
  Movement-history retention period of Administration → History
  archival & purge, in whole months; NULL means no retention period;
- CHECK `ck_application_policy_retention_period_range` admitting NULL
  or 12-1200.

Unlike the earlier policy columns, this one is nullable with NO server
default: PROJECT_PROFILE §28 forbids a hard-coded retention number and
no canonical default exists, so the seeded singleton holds NULL and
nothing is implied; a nullable ADD COLUMN without a default is
metadata-only. The value is stored for the Phase 16 archival
maintenance only — nothing in Phase 13 reads it, archives or purges. No
index (no reader filters on it), no data statement and no audit CHECK
change (`ApplicationPolicy` is already admitted).

The downgrade REFUSES — it never silently loses configuration or
strands its history: a stored period or an `ApplicationPolicy` audit
row of the `data-retention` section raises; `alembic/env.py` runs in
one transaction, so the database then stays at this revision. Run it
only against disposable development and test databases.

Revision ID: 0027_phase13_retention_period
Revises: 0026_phase13_station_theme
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_phase13_retention_period"
down_revision: str | None = "0026_phase13_station_theme"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant and
# _SECTION equals policies.DATA_RETENTION_SECTION.
_POLICY = "application_policy"
_COLUMN = "retention_period_months"
_CHECK = "ck_application_policy_retention_period_range"
_SECTION = "data-retention"
_RETENTION_MIN, _RETENTION_MAX = 12, 1200
_RETENTION_SQL = "retention_period_months IS NULL OR retention_period_months BETWEEN 12 AND 1200"


def upgrade() -> None:
    op.add_column(_POLICY, sa.Column(_COLUMN, sa.Integer(), nullable=True))
    op.create_check_constraint(op.f(_CHECK), _POLICY, _RETENTION_SQL)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Once the section was written, the schema cannot
    # return to one that cannot hold it.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM application_policy WHERE retention_period_months IS NOT NULL)"
        "  OR EXISTS (SELECT 1 FROM audit_events"
        "             WHERE entity_type = 'ApplicationPolicy' AND entity_id = 'data-retention')"
        " THEN RAISE EXCEPTION 'Retention period configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_constraint(op.f(_CHECK), _POLICY, type_="check")
    op.drop_column(_POLICY, _COLUMN)
