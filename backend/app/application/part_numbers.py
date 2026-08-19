"""PartNumber master services (Phase 4 — Work Order intake, Add Part).

Application-layer operations behind the PN lookup/create step of the
Add Part flow (GUI_DESIGN §11.2/§11.3) and the minimal barcode-label
capability of Phase 4 (IMPLEMENTATION_ROADMAP Phase 4).

Rules owned here (PROJECT_PROFILE §7 Part Number, §8.1, §10;
SLICE1_DATA_MODEL §6, §16):

- Every PN entering the system goes through the one canonical domain
  normalization (``app.domain.part_number``): surrounding whitespace
  trimmed, internal whitespace rejected (never silently removed),
  canonical UPPERCASE stored and compared. No second normalization
  exists anywhere.
- The master record is **created on first valid use** and an existing
  canonical PN is always reused — one master row per canonical PN
  (natural primary key). Production data never references the master,
  so it stays hard-deletable; the master has no active/inactive state.
- The PN barcode is fully derived (``PF:PN:<canonical-part-number>``)
  from the canonical PN — nothing separate is stored or issued.
- Master creation appends its ``CREATED`` audit row in the SAME
  transaction (SLICE1_DATA_MODEL §16). ``ensure_part_number`` stages
  only — the caller owns the transaction — so the Work Order save that
  first uses a PN commits the master, the demand, and every audit row
  atomically; the standalone create endpoint commits its own.
"""

from typing import Final, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import commit, flush
from app.application.errors import InvalidInputError
from app.domain.enums import AuditEntityType, AuditEventType
from app.domain.part_number import InvalidPartNumberError, normalize_part_number
from app.infrastructure.models import PartNumber

PART_NUMBER_CONFLICTS: Final = {
    "pk_part_numbers": (
        "This Part Number was just created by another user."
        " Look it up again to reuse the existing record."
    ),
}


def canonical_part_number(value: object) -> str:
    """Normalize input to the canonical PN or raise ``InvalidInputError``.

    Thin translation of the framework-independent domain rule into the
    application error vocabulary — the normalization itself lives only
    in ``app.domain.part_number``.
    """
    if not isinstance(value, str):
        raise InvalidInputError("Part Number must be text.")
    try:
        return normalize_part_number(value)
    except InvalidPartNumberError as exc:
        raise InvalidInputError(str(exc)) from exc


class EnsuredPartNumber(NamedTuple):
    """A resolved master record and whether this call created it."""

    master: PartNumber
    created: bool


def ensure_part_number(
    session: Session, value: object, *, actor: str | None = None
) -> EnsuredPartNumber:
    """Reuse the existing master for the canonical PN or stage a new one.

    Stages only — no commit. The new master and its ``CREATED`` audit
    row join the caller's transaction, so first use inside a Work Order
    save commits atomically with the demand it belongs to.
    """
    canonical = canonical_part_number(value)
    existing = session.get(PartNumber, canonical)
    if existing is not None:
        return EnsuredPartNumber(existing, created=False)
    master = PartNumber(part_number=canonical)
    session.add(master)
    # Surface a creation race at the INSERT instead of the caller's
    # later commit, translated to the same friendly conflict either
    # way; the PN string is the natural key, so no generated id is
    # needed for the audit row.
    flush(session, PART_NUMBER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.PART_NUMBER,
        entity_id=canonical,
        before_data=None,
        after_data={"part_number": canonical},
        actor_reference=actor,
    )
    return EnsuredPartNumber(master, created=True)


def create_part_number(
    session: Session, value: object, *, actor: str | None = None
) -> EnsuredPartNumber:
    """Create-or-reuse a PN master as its own transaction (Add Part).

    A concurrent creation race lost at COMMIT maps to the same
    friendly conflict as the pre-checked reuse path.
    """
    ensured = ensure_part_number(session, value, actor=actor)
    if ensured.created:
        commit(session, PART_NUMBER_CONFLICTS)
    return ensured


def resolve_part_number(session: Session, value: object) -> PartNumber | None:
    """Exact lookup by canonical PN; ``None`` on a miss.

    The Add Part flow treats a miss as "offer creation", so absence is
    a normal outcome here — invalid input (internal whitespace, empty)
    still raises, because it can never be a PN.
    """
    return session.get(PartNumber, canonical_part_number(value))


def list_part_numbers(session: Session, *, search: str | None = None) -> list[PartNumber]:
    """List masters for the Add Part lookup, optionally filtered.

    ``search`` is a case-insensitive contains-match over the canonical
    PN with LIKE wildcards escaped — a lookup convenience only, never a
    normalization of the stored value.
    """
    query = select(PartNumber).order_by(PartNumber.part_number)
    if search is not None and search.strip():
        query = query.where(PartNumber.part_number.ilike(_contains_pattern(search), escape="\\"))
    return list(session.scalars(query))


def _contains_pattern(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
