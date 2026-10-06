"""Phase 13: the badge-confirmation options of the sensitive Scan Station actions.

Exactly the additive schema slice 5 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §16, §19; PLAN CD3, CD6):

- `application_policy` gains `badge_confirm_done`, `badge_confirm_queue`
  and `badge_confirm_undo` — boolean, NOT NULL, server default `true`
  (PROFILE §19: each option is enabled by default). They decide only
  the FORM of the always-present final gate of DONE, QUEUE and Undo in
  Scanned-session Areas: a required Worker badge scan when on, the
  final confirmation question when off. The seeded singleton receives
  `true` from the default — no data statement; on a one-row table the
  constant-default ADD COLUMN is metadata-only.

The downgrade REFUSES — it never silently loses configuration or
strands its history: an option that is off, or an `ApplicationPolicy`
audit row that records an option, raises; `alembic/env.py` runs in one
transaction, so the database then stays at this revision. Run it only
against disposable development and test databases.

Revision ID: 0021_phase13_badge_confirmation
Revises: 0020_phase13_worker_sessions
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_phase13_badge_confirmation"
down_revision: str | None = "0020_phase13_worker_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY = "application_policy"
# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts they equal models.BADGE_CONFIRMATION_OPTIONS.
_BADGE_OPTIONS = ("badge_confirm_done", "badge_confirm_queue", "badge_confirm_undo")


def upgrade() -> None:
    for name in _BADGE_OPTIONS:
        op.add_column(
            _POLICY,
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("true")),
        )


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. `before_data` / `after_data` are JSONB, so `?` tests
    # for the key directly.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM application_policy"
        "   WHERE NOT (badge_confirm_done AND badge_confirm_queue AND badge_confirm_undo))"
        "  OR EXISTS (SELECT 1 FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
        "   AND (before_data ? 'badge_confirm_done' OR after_data ? 'badge_confirm_done'))"
        " THEN RAISE EXCEPTION 'Badge-confirmation configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for name in reversed(_BADGE_OPTIONS):
        op.drop_column(_POLICY, name)
