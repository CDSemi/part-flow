"""Phase 13 Workers: a canonical badge CHECK that never depends on the OS case tables.

`0014_phase13_workers` created `ck_workers_badge_barcode_canonical` with
`badge_barcode = upper(badge_barcode)` and `badge_barcode !~ '^\\s|\\s$'`
under the database collation (`en_US.utf8`, libc provider). The badge
rule itself (`app.domain.worker_badge`, owner decision OD-3) uppercases
with Python `str.upper()`, and the two Unicode case tables disagree:
glibc maps `ɤ` (U+0264) to `Ɤ` (U+A7CB) while Python 3.12 leaves it
unchanged. A badge the domain accepted could therefore fail the CHECK
and surface as an untranslated 500, and a glibc change in the
PostgreSQL image could make stored rows violate the CHECK on restore.

This migration re-creates the same CHECK with both clauses evaluated
under the `"C"` collation: PostgreSQL then uppercases and matches `\\s`
on ASCII only, independently of the OS libc. The CHECK keeps its role
as the database backstop — non-empty, no surrounding ASCII whitespace,
no ASCII lowercase letter (so `pf:` is still refused and the plain
UNIQUE stays case-insensitive for ASCII badges), at most 128
characters, outside the `PF:` namespace — while the full Unicode
uppercase stays owned by the one domain rule. Every value the domain
rule produces passes it: no Python uppercase mapping yields an ASCII
lowercase letter, and `str.strip()` removes all ASCII whitespace.

No data rewrite and no pre-check: every row the 0014 CHECK admitted
already holds no ASCII lowercase letter and no surrounding ASCII
whitespace, so PostgreSQL's re-validation of existing rows cannot fail.

The downgrade re-creates the 0014 CHECK. PostgreSQL re-validates it
against existing rows, so a badge only this revision admits (such as
`ɤ1`) makes the downgrade fail loudly; `alembic/env.py` runs in one
transaction, so the database then stays at this revision. No Worker
row is ever rewritten or deleted.

Revision ID: 0015_phase13_badge_check
Revises: 0014_phase13_workers
Create Date: 2026-10-04

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015_phase13_badge_check"
down_revision: str | None = "0014_phase13_workers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKERS = "workers"
_BADGE_CHECK = "ck_workers_badge_barcode_canonical"

# Self-contained on purpose (a migration never imports the mutable
# model module); the schema test asserts the new literal equals
# `models.WORKER_BADGE_BARCODE_SQL`.
_WORKER_BADGE_BARCODE_SQL = (
    r"""badge_barcode <> '' AND badge_barcode COLLATE "C" !~ '^\s|\s$'"""
    """ AND badge_barcode = upper(badge_barcode COLLATE "C")"""
    " AND char_length(badge_barcode) <= 128 AND left(badge_barcode, 3) <> 'PF:'"
)
# The 0014 literal, restored by the downgrade.
_PHASE13_S1_BADGE_BARCODE_SQL = (
    r"badge_barcode <> '' AND badge_barcode !~ '^\s|\s$'"
    " AND badge_barcode = upper(badge_barcode) AND char_length(badge_barcode) <= 128"
    " AND left(badge_barcode, 3) <> 'PF:'"
)


def upgrade() -> None:
    op.drop_constraint(op.f(_BADGE_CHECK), _WORKERS, type_="check")
    op.create_check_constraint(op.f(_BADGE_CHECK), _WORKERS, _WORKER_BADGE_BARCODE_SQL)


def downgrade() -> None:
    op.drop_constraint(op.f(_BADGE_CHECK), _WORKERS, type_="check")
    op.create_check_constraint(op.f(_BADGE_CHECK), _WORKERS, _PHASE13_S1_BADGE_BARCODE_SQL)
