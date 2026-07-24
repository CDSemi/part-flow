"""Repository foundation baseline.

Intentional no-op revision. Phase 1 creates no domain tables; this
baseline only validates Alembic wiring against the configured database.
The only metadata created is Alembic's own version table.

Revision ID: 0001_repo_foundation
Revises:
Create Date: 2026-07-23

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0001_repo_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
