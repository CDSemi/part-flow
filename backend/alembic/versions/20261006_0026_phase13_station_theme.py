"""Phase 13: the Scan Station theme preference (station tier).

Exactly the additive schema slice 10 needs (IMPLEMENTATION_ROADMAP
Phase 13; GUI_DESIGN §2.1 theme persistence ① User → ② Scan Station →
③ Dark; PLAN CD3 "per-record settings are typed columns on their owner",
owner default OD-13):

- `scan_stations.theme_preference` (text, NULL) — the station's own
  saved Dark/Light display preference, the station tier of GUI_DESIGN
  §2.1; NULL means no preference (the Dark default applies);
- CHECK `ck_scan_stations_theme_preference` admitting exactly `DARK`
  and `LIGHT` (NULL passes a CHECK by SQL semantics).

Deliberately unchanged: no server default and no backfill — every
existing station has no preference, the truthful state, and the Dark
default stays a GUI rule rather than frozen station data; no index — the
value is only read by primary key; no audit CHECK change — the
preference is a display preference, not configuration, and is never
audited (OD-13); no `users` column — the User tier is slice 12 storage
and Phase 14 resolution.

The downgrade REFUSES while any station holds a saved preference — it
never silently deletes a recorded choice; `alembic/env.py` runs in one
transaction, so the database then stays at this revision. Run it only
against disposable development and test databases.

Revision ID: 0026_phase13_station_theme
Revises: 0025_phase13_display_settings
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026_phase13_station_theme"
down_revision: str | None = "0025_phase13_display_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained literals (a migration never imports the mutable model
# module); the schema test asserts the CHECK literal equals
# models.SCAN_STATION_THEME_PREFERENCE_SQL.
_SCAN_STATIONS = "scan_stations"
_THEME_CHECK = "ck_scan_stations_theme_preference"
_THEME_PREFERENCE_SQL = "theme_preference IN ('DARK', 'LIGHT')"


def upgrade() -> None:
    op.add_column(_SCAN_STATIONS, sa.Column("theme_preference", sa.Text(), nullable=True))
    op.create_check_constraint(op.f(_THEME_CHECK), _SCAN_STATIONS, _THEME_PREFERENCE_SQL)


def downgrade() -> None:
    # Refusing, never destructive — for disposable development and test
    # databases only. Once a station saved its theme, the schema cannot
    # return to one that cannot hold it.
    op.execute(
        "DO $$ BEGIN"
        " IF EXISTS (SELECT 1 FROM scan_stations WHERE theme_preference IS NOT NULL)"
        " THEN RAISE EXCEPTION"
        " 'scan stations hold a saved theme preference; refusing downgrade';"
        " END IF; END $$;"
    )
    op.drop_constraint(op.f(_THEME_CHECK), _SCAN_STATIONS, type_="check")
    op.drop_column(_SCAN_STATIONS, "theme_preference")
