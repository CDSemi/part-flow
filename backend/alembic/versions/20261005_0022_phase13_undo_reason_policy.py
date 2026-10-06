"""Phase 13: the Undo reason policy of Administration → Correction permissions.

Exactly the additive schema slice 6 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §16 "require a reason when configured"; PLAN
CD3; owner default OD-6: one global switch, default off):

- `application_policy` gains `undo_reason_required` — boolean, NOT NULL,
  server default `false`. While on, the Undo command refuses a reversal
  without a reason; the requirement depends on configuration, so it is
  enforced by the command and never by a CHECK. The seeded singleton
  receives `false` from the default — no data statement, no production
  behavior change at upgrade; on a one-row table the constant-default
  ADD COLUMN is metadata-only. `part_movements.reason` and its CHECK
  predate this revision and already admit a `REVERSED` row with or
  without a reason.

The downgrade REFUSES — it never silently loses configuration or
strands its history: the policy switched on, or an `ApplicationPolicy`
audit row of the `correction-permissions` section, raises;
`alembic/env.py` runs in one transaction, so the database then stays at
this revision. `REVERSED` rows carrying a reason never block it. Run it
only against disposable development and test databases.

Revision ID: 0022_phase13_undo_reason_policy
Revises: 0021_phase13_badge_confirmation
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022_phase13_undo_reason_policy"
down_revision: str | None = "0021_phase13_badge_confirmation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable
# application modules); the schema test asserts _SECTION equals
# policies.CORRECTION_PERMISSIONS_SECTION and that the downgrade guard
# names the same section.
_POLICY = "application_policy"
_COLUMN = "undo_reason_required"
_SECTION = "correction-permissions"


def upgrade() -> None:
    op.add_column(
        _POLICY,
        sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM application_policy WHERE undo_reason_required)"
        "  OR EXISTS (SELECT 1 FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
        "   AND entity_id = 'correction-permissions')"
        " THEN RAISE EXCEPTION 'Undo reason policy configuration exists; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_column(_POLICY, _COLUMN)
