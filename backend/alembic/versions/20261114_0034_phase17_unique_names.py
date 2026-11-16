"""Phase 17: unique Area names per Department and unique active Planned Route names.

Owner decision of 2026-11-13 (IMPLEMENTATION_ROADMAP Phase 17 item 4;
PROJECT_PROFILE §8.4 and §8.8):

- `uq_areas_department_id_name` — an Area name is unique within its
  Department, inactive Areas included: an inactive Area can still be
  renamed. A create retried after a timeout can no longer add a second
  Area with the same name.
- `uq_route_templates_name_active` — a Planned Route name is unique among
  the active Planned Routes. An archived route can never be edited, so it
  keeps its name without blocking a new route.

``upgrade_blockers`` lists every existing duplicate, one line each
(``app.infrastructure.schema_revision.data_blockers``): ``revision`` run
with this release's image reports them as ``blocked_by_data`` before the
write freeze, and ``migrate`` refuses with the same list, so the owner
renames them in PartFlow first. The upgrade itself refuses with the list
too, whatever runs it.

The downgrade drops the constraint and the index and keeps every row.

Revision ID: 0034_phase17_unique_names
Revises: 0033_phase17_super_user_role
Create Date: 2026-11-14

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034_phase17_unique_names"
down_revision: str | None = "0033_phase17_super_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AREA_NAME_CONSTRAINT = "uq_areas_department_id_name"
_ROUTE_NAME_INDEX = "uq_route_templates_name_active"

# One row per duplicate the constraint or the index would refuse.
_DUPLICATES_SQL = """
SELECT 'Department ' || quote_literal(d.name) || ' has ' || count(*) || ' Areas named '
       || quote_literal(a.name) || ' (Area ids ' || string_agg(a.id::text, ', ' ORDER BY a.id)
       || '); rename all but one.'
FROM areas a JOIN departments d ON d.id = a.department_id
GROUP BY d.id, a.name
HAVING count(*) > 1
UNION ALL
SELECT count(*) || ' active Planned Routes are named ' || quote_literal(name) || ' (ids '
       || string_agg(id::text, ', ' ORDER BY id) || '); rename all but one.'
FROM route_templates
WHERE archived_at IS NULL
GROUP BY name
HAVING count(*) > 1
ORDER BY 1
"""


def upgrade_blockers(connection: sa.Connection) -> list[str]:
    """One line per stored duplicate (read-only); none before 0002 created the tables."""
    tables = connection.execute(
        sa.text(
            "SELECT to_regclass('public.areas') IS NOT NULL"
            " AND to_regclass('public.departments') IS NOT NULL"
            " AND to_regclass('public.route_templates') IS NOT NULL"
        )
    ).scalar_one()
    if not tables:
        return []
    return [str(line) for line in connection.execute(sa.text(_DUPLICATES_SQL)).scalars()]


def upgrade() -> None:
    blockers = upgrade_blockers(op.get_bind())
    if blockers:
        raise RuntimeError(
            "Rename the duplicate names in PartFlow first; nothing was changed. "
            + " ".join(blockers)
        )
    op.create_unique_constraint(op.f(_AREA_NAME_CONSTRAINT), "areas", ["department_id", "name"])
    op.create_index(
        _ROUTE_NAME_INDEX,
        "route_templates",
        ["name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(_ROUTE_NAME_INDEX, table_name="route_templates")
    op.drop_constraint(op.f(_AREA_NAME_CONSTRAINT), "areas", type_="unique")
