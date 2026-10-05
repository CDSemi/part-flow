"""Phase 13: a canonical PN CHECK that never depends on the OS case tables.

`0002_phase3_minimum_domain_foundation` (part_numbers,
work_order_demands, quantity_flows) and `0011_phase10_stock_allocation`
(work_order_allocations) created the canonical PN CHECK
`part_number = upper(part_number) AND part_number !~ '[[:space:]]'
AND part_number <> ''` under the database collation (`en_US.utf8`,
libc provider). The PN rule itself
(`app.domain.part_number.normalize_part_number`, PROJECT_PROFILE §7)
uppercases with Python `str.upper()`, and the two Unicode case tables
disagree on 27 code points: glibc maps `ɤ` (U+0264) to `Ɤ` (U+A7CB)
while Python 3.12 leaves it unchanged. A PN the domain accepted could
therefore fail the CHECK and surface as an untranslated 500 on intake,
Work Order save, PN master creation or allocation, and a glibc change in
the PostgreSQL image could make stored rows violate the CHECK on
restore.

This migration re-creates the four CHECKs with both clauses evaluated
under the `"C"` collation (exactly as `0015_phase13_badge_check` did for
the Worker badge): PostgreSQL then uppercases and matches `[[:space:]]`
on ASCII only, independently of the OS libc. The CHECK keeps its role as
the database backstop — non-empty, no ASCII lowercase letter, no ASCII
whitespace — while the full Unicode uppercase and the refusal of every
Unicode whitespace stay owned by the one domain rule, through which
every PN entering a write passes
(`app.application.part_numbers.canonical_part_number`). Every value the
domain rule produces passes the new CHECK: no Python uppercase mapping
yields an ASCII lowercase letter, and `str.isspace()` covers ASCII
whitespace. `part_movements.part_number` has no CHECK of its own; its
composite FK to `quantity_flows (id, part_number)` inherits the
canonical form.

Consequence (owner-visible decision S2b-OD8): the canonical form of a PN
with a non-ASCII letter is defined by the Unicode version of the
backend's Python (UCD `15.0.0`, pinned by
`tests/test_part_number_normalization.py`). The 27 code points become
storable in their Python 3.12 form (`pnɤ1` → `PNɤ1`), and a change of
that Unicode version requires the S2b-F3 pre-upgrade check of the stored
PNs and Worker badges first.

No data rewrite and no pre-check: every row the 0002/0011 CHECK
admitted already holds no ASCII lowercase letter (libc `upper()` maps
them) and no ASCII whitespace (libc `[[:space:]]` includes it), so
PostgreSQL's re-validation of existing rows cannot fail. It scans the
four tables under `ACCESS EXCLUSIVE` locks inside the migration
transaction.

The downgrade re-creates the 0002/0011 CHECK. PostgreSQL re-validates it
against existing rows, so a PN only this revision admits (such as
`PNɤ1`) makes the downgrade fail loudly; `alembic/env.py` runs in one
transaction, so the database then stays at this revision. No row is
ever rewritten or deleted.

Revision ID: 0018_phase13_pn_check_collation
Revises: 0017_phase13_machine_audit
Create Date: 2026-10-05

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_phase13_pn_check_collation"
down_revision: str | None = "0017_phase13_machine_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKS = (  # (table, constraint), creation order
    ("part_numbers", "ck_part_numbers_part_number_canonical"),
    ("work_order_demands", "ck_work_order_demands_part_number_canonical"),
    ("quantity_flows", "ck_quantity_flows_part_number_canonical"),
    ("work_order_allocations", "ck_work_order_allocations_part_number_canonical"),
)

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts the new literal equals
# `models.CANONICAL_PART_NUMBER_SQL`.
_CANONICAL_PART_NUMBER_SQL = (
    """part_number = upper(part_number COLLATE "C")"""
    """ AND part_number COLLATE "C" !~ '[[:space:]]' AND part_number <> ''"""
)
# The 0002/0011 literal, restored by the downgrade.
_PHASE3_CANONICAL_PART_NUMBER_SQL = (
    "part_number = upper(part_number) AND part_number !~ '[[:space:]]' AND part_number <> ''"
)


def upgrade() -> None:
    for table, name in _CHECKS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, _CANONICAL_PART_NUMBER_SQL)


def downgrade() -> None:
    for table, name in reversed(_CHECKS):
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, _PHASE3_CANONICAL_PART_NUMBER_SQL)
