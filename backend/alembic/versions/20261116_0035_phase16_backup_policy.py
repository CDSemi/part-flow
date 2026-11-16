"""Phase 16: the backup policy of Administration → Backups.

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 16, "backups
set in Administration"): the host's backup task runs every hour and reads
from the ``application_policy`` singleton how often it takes a scheduled
backup and how many scheduled backups its rotation keeps.

- ``backup_interval_hours`` (integer, NOT NULL, default 1) — a scheduled
  backup is due this many whole hours after the previous one; CHECK
  ``ck_application_policy_backup_interval_hours_allowed`` admits only the
  hours that divide the day (1, 2, 3, 4, 6, 8, 12, 24);
- ``backup_keep_hourly`` (default 48, 0-720), ``backup_keep_daily``
  (default 30, 1-3650) and ``backup_keep_weekly`` (default 52, 0-520) —
  the rotation keeps the newest scheduled backup of each of the newest N
  hours, days and ISO weeks (UTC) that hold one; at least one daily
  backup is kept, so the newest is never removed.

Each column is added with its constant server default, so the seeded
singleton holds the owner's defaults at once and the ADD COLUMN is
metadata-only. No index, no data statement and no audit CHECK change
(``ApplicationPolicy`` is already admitted; the section's audit rows use
``entity_id`` ``backups``).

The downgrade REFUSES once the section was written — a stored value other
than the defaults or an ``ApplicationPolicy`` audit row of the ``backups``
section raises — so configuration and its history are never silently
lost; ``alembic/env.py`` runs in one transaction, so the database then
stays at this revision. Run it only against disposable development and
test databases.

Revision ID: 0035_phase16_backup_policy
Revises: 0034_phase17_unique_names
Create Date: 2026-11-16

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035_phase16_backup_policy"
down_revision: str | None = "0034_phase17_unique_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant and
# _SECTION equals policies.BACKUPS_SECTION.
_POLICY = "application_policy"
_SECTION = "backups"
# (column, server default, check name, check SQL)
_COLUMNS = (
    (
        "backup_interval_hours",
        "1",
        "ck_application_policy_backup_interval_hours_allowed",
        "backup_interval_hours IN (1, 2, 3, 4, 6, 8, 12, 24)",
    ),
    (
        "backup_keep_hourly",
        "48",
        "ck_application_policy_backup_keep_hourly_range",
        "backup_keep_hourly BETWEEN 0 AND 720",
    ),
    (
        "backup_keep_daily",
        "30",
        "ck_application_policy_backup_keep_daily_range",
        "backup_keep_daily BETWEEN 1 AND 3650",
    ),
    (
        "backup_keep_weekly",
        "52",
        "ck_application_policy_backup_keep_weekly_range",
        "backup_keep_weekly BETWEEN 0 AND 520",
    ),
)


def upgrade() -> None:
    for column, default, check, sql in _COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(default)),
        )
        op.create_check_constraint(op.f(check), _POLICY, sql)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Once the section was written, the schema cannot
    # return to one that cannot hold it.
    changed = " OR ".join(f"{column} <> {default}" for column, default, _, _ in _COLUMNS)
    op.execute(
        "DO $$ BEGIN"
        f" IF EXISTS (SELECT 1 FROM {_POLICY} WHERE {changed})"
        "  OR EXISTS (SELECT 1 FROM audit_events"
        f"             WHERE entity_type = 'ApplicationPolicy' AND entity_id = '{_SECTION}')"
        " THEN RAISE EXCEPTION 'Backup policy configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for column, _, check, _ in reversed(_COLUMNS):
        op.drop_constraint(op.f(check), _POLICY, type_="check")
        op.drop_column(_POLICY, column)
