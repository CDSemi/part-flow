"""Phase 13: Department display settings and the Due Soon warning policy.

Exactly the additive schema slice 9 needs (IMPLEMENTATION_ROADMAP
Phase 13; PROJECT_PROFILE §21 "configuration per Department … never
global, never hard-coded UI constants"; GUI_DESIGN §3 rule 12, §5, §9;
PLAN CD3; owner default OD-5):

- `departments.board_seconds_per_row` (integer, NOT NULL, server default
  3, CHECK 1-60) and `departments.board_min_page_seconds` (integer, NOT
  NULL, server default 6, CHECK 1-300) — the Production Board rotation
  timing as a per-record setting on its owner, in whole seconds;
- `application_policy.due_soon_min_days` (default 2),
  `due_soon_lead_time_percent` (default 15) and `due_soon_max_days`
  (default 7) — the one global Due Soon warning policy: whole days
  0-365 for both clamps, a whole percent 1-100 (never a float ratio),
  and the minimum never above the maximum.

Every existing Department and the seeded policy singleton receive the
canonical defaults from the column defaults — no data statement, no
behavior change at upgrade; a constant-default ADD COLUMN is
metadata-only. No index (no reader filters on them) and no audit CHECK
change (`Department` and `ApplicationPolicy` are already admitted).

The downgrade REFUSES — it never silently loses configuration or
strands its history: a value other than its default, an
`ApplicationPolicy` audit row of the `due-soon` section, or a
`Department` UPDATED audit row that changed a rotation setting raises;
`alembic/env.py` runs in one transaction, so the database then stays at
this revision. Department CREATED rows and name-only UPDATED rows that
merely carry the keys never block it. Run it only against disposable
development and test databases.

Revision ID: 0025_phase13_display_settings
Revises: 0024_phase13_planned_routes
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025_phase13_display_settings"
down_revision: str | None = "0024_phase13_planned_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts each equals its models.py constant and
# _SECTION equals policies.DUE_SOON_SECTION.
_DEPARTMENTS = "departments"
_POLICY = "application_policy"
_SECTION = "due-soon"
_SECONDS_PER_ROW_MIN, _SECONDS_PER_ROW_MAX, _SECONDS_PER_ROW_DEFAULT = 1, 60, 3
_MIN_PAGE_SECONDS_MIN, _MIN_PAGE_SECONDS_MAX, _MIN_PAGE_SECONDS_DEFAULT = 1, 300, 6
_DUE_SOON_MIN_DAYS_DEFAULT, _DUE_SOON_PERCENT_DEFAULT, _DUE_SOON_MAX_DAYS_DEFAULT = 2, 15, 7
_SECONDS_PER_ROW_SQL = "board_seconds_per_row BETWEEN 1 AND 60"
_MIN_PAGE_SECONDS_SQL = "board_min_page_seconds BETWEEN 1 AND 300"
_DUE_SOON_MIN_DAYS_SQL = "due_soon_min_days BETWEEN 0 AND 365"
_DUE_SOON_MAX_DAYS_SQL = "due_soon_max_days BETWEEN 0 AND 365"
_DUE_SOON_PERCENT_SQL = "due_soon_lead_time_percent BETWEEN 1 AND 100"
_DUE_SOON_ORDER_SQL = "due_soon_min_days <= due_soon_max_days"

_SECONDS_PER_ROW_CHECK = "ck_departments_board_seconds_per_row_range"
_MIN_PAGE_SECONDS_CHECK = "ck_departments_board_min_page_seconds_range"
_DUE_SOON_MIN_DAYS_CHECK = "ck_application_policy_due_soon_min_days_range"
_DUE_SOON_MAX_DAYS_CHECK = "ck_application_policy_due_soon_max_days_range"
_DUE_SOON_PERCENT_CHECK = "ck_application_policy_due_soon_lead_time_percent_range"
_DUE_SOON_ORDER_CHECK = "ck_application_policy_due_soon_window_order"

_DEPARTMENT_COLUMNS = (
    ("board_seconds_per_row", _SECONDS_PER_ROW_DEFAULT),
    ("board_min_page_seconds", _MIN_PAGE_SECONDS_DEFAULT),
)
_DEPARTMENT_CHECKS = (
    (_SECONDS_PER_ROW_CHECK, _SECONDS_PER_ROW_SQL),
    (_MIN_PAGE_SECONDS_CHECK, _MIN_PAGE_SECONDS_SQL),
)
_POLICY_COLUMNS = (
    ("due_soon_min_days", _DUE_SOON_MIN_DAYS_DEFAULT),
    ("due_soon_lead_time_percent", _DUE_SOON_PERCENT_DEFAULT),
    ("due_soon_max_days", _DUE_SOON_MAX_DAYS_DEFAULT),
)
_POLICY_CHECKS = (
    (_DUE_SOON_MIN_DAYS_CHECK, _DUE_SOON_MIN_DAYS_SQL),
    (_DUE_SOON_MAX_DAYS_CHECK, _DUE_SOON_MAX_DAYS_SQL),
    (_DUE_SOON_PERCENT_CHECK, _DUE_SOON_PERCENT_SQL),
    (_DUE_SOON_ORDER_CHECK, _DUE_SOON_ORDER_SQL),
)


def upgrade() -> None:
    for column, default in _DEPARTMENT_COLUMNS:
        op.add_column(
            _DEPARTMENTS,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(str(default))),
        )
    for name, sql in _DEPARTMENT_CHECKS:
        op.create_check_constraint(op.f(name), _DEPARTMENTS, sql)

    for column, default in _POLICY_COLUMNS:
        op.add_column(
            _POLICY,
            sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text(str(default))),
        )
    for name, sql in _POLICY_CHECKS:
        op.create_check_constraint(op.f(name), _POLICY, sql)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Once a setting was written, the schema cannot
    # return to one that cannot hold it.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM departments"
        "            WHERE board_seconds_per_row <> 3 OR board_min_page_seconds <> 6)"
        "  OR EXISTS (SELECT 1 FROM application_policy"
        "             WHERE due_soon_min_days <> 2 OR due_soon_lead_time_percent <> 15"
        "                OR due_soon_max_days <> 7)"
        " THEN RAISE EXCEPTION 'Display settings configuration exists; refusing downgrade';"
        " END IF;"
        " IF EXISTS (SELECT 1 FROM audit_events"
        "            WHERE entity_type = 'ApplicationPolicy' AND entity_id = 'due-soon')"
        "  OR EXISTS (SELECT 1 FROM audit_events"
        "             WHERE entity_type = 'Department' AND event_type = 'UPDATED'"
        "               AND (before_data -> 'board_seconds_per_row'"
        "                      IS DISTINCT FROM after_data -> 'board_seconds_per_row'"
        "                    OR before_data -> 'board_min_page_seconds'"
        "                      IS DISTINCT FROM after_data -> 'board_min_page_seconds'))"
        " THEN RAISE EXCEPTION 'Display settings history exists; refusing downgrade';"
        " END IF; END $$;"
    )
    for name, _sql in reversed(_POLICY_CHECKS):
        op.drop_constraint(op.f(name), _POLICY, type_="check")
    for column, _default in reversed(_POLICY_COLUMNS):
        op.drop_column(_POLICY, column)
    for name, _sql in reversed(_DEPARTMENT_CHECKS):
        op.drop_constraint(op.f(name), _DEPARTMENTS, type_="check")
    for column, _default in reversed(_DEPARTMENT_COLUMNS):
        op.drop_column(_DEPARTMENTS, column)
