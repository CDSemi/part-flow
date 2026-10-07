"""PartNumber master services (Phase 4 lookup; Phase 13 management).

Application-layer operations behind the PN lookup/create step of the
Add Part flow (GUI_DESIGN §11.2/§11.3), the minimal barcode-label
capability of Phase 4 (IMPLEMENTATION_ROADMAP Phase 4), and Part
Numbers management (Phase 13 slice 7; GUI_DESIGN §14): the optional
details (Name / Description, current revision, ERP id), the PN image
and the hard delete of the master record.

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
  Management creates it explicitly too (:func:`create_part_number`,
  create-only: an existing master is a conflict, never reused).
- The PN itself is never edited: every management service addresses
  the master by its canonical PN and changes only the optional details
  or the image. The details are free text (trimmed, blank → NULL),
  informational and never unique; the ERP id is never looked up (ERP
  stays isolated).
- Hard delete (PROJECT_PROFILE §21, §28) removes ONLY the master row —
  details and image. No production table references it, so demand,
  QuantityFlows, Movements, allocations and history are never read or
  written; the PN gets a master again on its next first use.
- The PN barcode is fully derived (``PF:PN:<canonical-part-number>``)
  from the canonical PN — nothing separate is stored or issued.
- Master creation appends its ``CREATED`` audit row in the SAME
  transaction (SLICE1_DATA_MODEL §16). ``ensure_part_number`` stages
  only — the caller owns the transaction — so the Work Order save that
  first uses a PN commits the master, the demand, and every audit row
  atomically; every management service commits its own. Every
  effective write appends exactly one ``PartNumber`` row: the details
  are snapshotted as :func:`master_snapshot` (never image data), the
  image as ``{"image": digest-or-null}``. Rejected writes, lost races
  and no-ops append nothing. Each row carries ``actor_user_id``, the
  signed-in User of a Management write; a Scan Station receipt's
  first use records none (Phase 14 slice 3).
- Lock order: :func:`create_part_number` takes ONLY the PN advisory
  lock (it serializes with create-on-first-use, which the production
  commands run under that lock); every other management write takes
  ONLY the master's row lock (:func:`_lock_master`) — never the PN
  advisory lock or a production row, so production never waits on it.
- The canonical PN string is also the key of the ONE PN-level
  serialization lock every command shares
  (:func:`acquire_part_number_lock`), and the subject of the PN-level
  state that lock protects — :func:`active_quantity_distribution`, the
  one reading of "what active quantity this PN already has" that the
  release, the Scan Station receipt and the scan resolution all share.
  Both live here because the canonical PN — not the optional,
  hard-deletable master row — is what they are keyed on.
"""

import datetime
from collections.abc import Collection, Iterable
from typing import Any, Final, NamedTuple

from sqlalchemy import ColumnElement, false, func, or_, select
from sqlalchemy.orm import Session, undefer

from app.application import audit, images
from app.application.common import UNSET, UnsetType, commit, flush, optional_text
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.enums import AuditEntityType, AuditEventType, QuantityFlowStatus
from app.domain.part_number import InvalidPartNumberError, normalize_part_number
from app.infrastructure.models import Area, PartNumber, QuantityFlow

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
    in ``app.domain.part_number``. A value holding NUL (U+0000) is
    refused here: PostgreSQL text cannot hold it, so it can never be a
    stored PN, and the driver would otherwise fail the first query with
    an unhandled DataError.
    """
    if not isinstance(value, str):
        raise InvalidInputError("Part Number must be text.")
    if "\x00" in value:
        raise InvalidInputError("Part Number must not contain a NUL character.")
    try:
        return normalize_part_number(value)
    except InvalidPartNumberError as exc:
        raise InvalidInputError(str(exc)) from exc


# ---------------------------------------------------------------------------
# PN-level serialization — ONE lock for every PN-level invariant
# ---------------------------------------------------------------------------

#: Namespace hashed together with the canonical PN into the advisory
#: lock key. ONE namespace on purpose: the PN-level preconditions of
#: the different commands are not independent invariants that each
#: deserve their own lock, they are statements about the SAME PN-level
#: state — whether the PN currently has active production quantity,
#: whether it currently has active business demand, and how much of its
#: stocked quantity is still unallocated. Separate namespaces would let
#: two commands each hold "their" lock and both change the state the
#: other just judged.
_PART_NUMBER_LOCK_NAMESPACE: Final = "partflow:part-number:"


def acquire_part_number_lock(session: Session, part_number: str) -> None:
    """Serialize this transaction against every other write of one PN.

    ``pg_advisory_xact_lock`` blocks until the concurrent holder's
    transaction commits or rolls back and releases automatically with
    this one — no new table, and no dependency on the optional
    PartNumber master (the key is derived from the canonical PN string
    itself). A hash collision between two different PNs merely
    serializes them too — harmless.

    Every command that can move a PN across one of the PN-level
    thresholds takes this lock, and takes it BEFORE any row lock:

    - *no active quantity → active quantity*: the production release,
      the Scan Station receipt, and an Undo that reopens a flow its
      command had closed;
    - *no active demand → active demand*: a Work Order save that adds
      or raises a demand line, an allocation reversal (which returns a
      business shortage to a line), and the Scan Station receipt;
    - *available stocked quantity*: allocation confirmation and
      reversal.

    The Part Numbers management create (:func:`create_part_number`)
    takes it too, so every creator of the optional master — the Work
    Order save and the Scan Station receipt (create-on-first-use) and
    the explicit create — serializes per PN, and a production command
    never meets a creation conflict caused by management. The create
    holds no row lock when it takes it.

    Commands that can only move a PN the permissive way — a demand-line
    removal, an allocation confirmation's effect on demand, closing a
    flow — need no lock: they can never make a precondition that was
    judged under the lock become true again.

    After the lock is granted, the state it protects must be RE-READ
    from the database: a snapshot taken while waiting is not authority.
    """
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"{_PART_NUMBER_LOCK_NAMESPACE}{part_number}", 0)
            )
        )
    )


def acquire_part_number_locks(session: Session, part_numbers: Iterable[str]) -> None:
    """Lock several canonical PNs in ONE deterministic order.

    Ascending canonical-PN order, so two transactions that need an
    overlapping set queue behind each other instead of deadlocking. A
    command that needs a single PN takes one lock and never waits on a
    second while holding the first, so the whole advisory-lock graph
    stays acyclic.
    """
    for part_number in sorted(set(part_numbers)):
        acquire_part_number_lock(session, part_number)


# ---------------------------------------------------------------------------
# PN-level active quantity — the state the lock protects
# ---------------------------------------------------------------------------


class ActiveQuantityEntry(NamedTuple):
    """One ACTIVE QuantityFlow of a PN, as a confirmation presents it.

    Business facts only: the quantity, where it currently is and how
    it is routed. The Area name travels with the id so a station or a
    dialog can name the location without a second lookup.
    """

    quantity_flow_id: int
    quantity: int
    route_mode: str
    current_area_id: int
    current_area_name: str


def active_quantity_distribution(session: Session, part_number: str) -> list[ActiveQuantityEntry]:
    """The PN's current ACTIVE distribution, oldest flow first.

    ONE reading of the PN-level state :func:`acquire_part_number_lock`
    protects, shared by every consumer: the production release and the
    Scan Station receipt judge their explicit-confirmation rule on it
    under the lock, and the scan resolution shows exactly the same
    distribution to the operator BEFORE the confirmation. A separate
    reading per consumer could show one thing and judge another.
    """
    rows = session.execute(
        select(QuantityFlow, Area.name)
        .join(Area, Area.id == QuantityFlow.current_area_id)
        .where(
            QuantityFlow.part_number == part_number,
            QuantityFlow.status == QuantityFlowStatus.ACTIVE,
        )
        .order_by(QuantityFlow.id)
    ).all()
    return [
        ActiveQuantityEntry(
            quantity_flow_id=flow.id,
            quantity=flow.quantity,
            route_mode=flow.route_mode,
            current_area_id=flow.current_area_id,
            current_area_name=area_name,
        )
        for flow, area_name in rows
    ]


def active_quantity_payload(entries: Iterable[ActiveQuantityEntry]) -> list[dict[str, Any]]:
    """The distribution as the confirmation-required response body carries it."""
    return [dict(entry._asdict()) for entry in entries]


class EnsuredPartNumber(NamedTuple):
    """A resolved master record and whether this call created it."""

    master: PartNumber
    created: bool


def ensure_part_number(
    session: Session, value: object, *, actor_user_id: int | None
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
        after_data=master_snapshot(master),
        actor_user_id=actor_user_id,
    )
    return EnsuredPartNumber(master, created=True)


def resolve_part_number(session: Session, value: object) -> PartNumber | None:
    """Exact lookup by canonical PN; ``None`` on a miss.

    The Add Part flow treats a miss as "offer creation", so absence is
    a normal outcome here — invalid input (internal whitespace, empty)
    still raises, because it can never be a PN.
    """
    return session.get(PartNumber, canonical_part_number(value))


#: Most masters one Add Part lookup returns. The PN master is an
#: unbounded catalog, so the lookup is bounded HERE — not by slicing an
#: already-transferred list in the browser. This is a result bound, not
#: a pagination contract: the Add Part lookup needs no more than a
#: screenful, and Part Numbers management pages through
#: :func:`part_number_page` instead. ``resolve_part_number`` is
#: unaffected — it resolves one canonical PN by equality, so a valid
#: one-character PN still resolves exactly.
SEARCH_RESULT_LIMIT: Final = 50


def list_part_numbers(session: Session, *, search: str | None = None) -> list[PartNumber]:
    """List masters for the Add Part lookup, optionally filtered.

    ``search`` is a case-insensitive contains-match over the canonical
    PN **or** the saved Name / Description (the Add Part search covers
    both — GUI_DESIGN §11.3) with LIKE wildcards escaped — a lookup
    convenience only, never a normalization of the stored value. At
    most :data:`SEARCH_RESULT_LIMIT` masters come back, in the unchanged
    canonical-PN order, so neither a broad search term nor an absent
    one can stream the whole catalog to a client.
    """
    query = select(PartNumber).order_by(PartNumber.part_number).limit(SEARCH_RESULT_LIMIT)
    if search is not None and search.strip():
        query = query.where(_matches_any(search, PartNumber.part_number, PartNumber.name))
    return list(session.scalars(query))


def _contains_pattern(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _matches_any(term: str, *columns: Any) -> ColumnElement[bool]:
    """Escaped case-insensitive contains-match of ``term`` over any column.

    A term holding NUL (U+0000) matches nothing: PostgreSQL text cannot
    hold it, so no stored value contains it — and the pattern is never
    sent, because the driver would fail the query.
    """
    if "\x00" in term:
        return false()
    pattern = _contains_pattern(term)
    return or_(*(column.ilike(pattern, escape="\\") for column in columns))


def masters_by_part_number(
    session: Session, part_numbers: Collection[str]
) -> dict[str, PartNumber]:
    """The masters of the given canonical PNs, keyed by PN — one query.

    The shared read-model lookup (Production Board, PN Tracking): a PN
    without a master is simply absent from the result, and the image
    bytes stay unloaded.
    """
    if not part_numbers:
        return {}
    return {
        master.part_number: master
        for master in session.scalars(
            select(PartNumber).where(PartNumber.part_number.in_(part_numbers))
        )
    }


# ---------------------------------------------------------------------------
# Part Numbers management (Phase 13 slice 7 — GUI_DESIGN §14)
# ---------------------------------------------------------------------------

#: Default and largest page of the management list.
DEFAULT_PAGE_LIMIT: Final = 100
MAX_PAGE_LIMIT: Final = 200

_DETAIL_LABELS: Final = {
    "name": "Name / Description",
    "current_revision": "Revision",
    "erp_id": "ERP ID",
}


class PartNumberImage(NamedTuple):
    """A stored PN image as served: bytes, media type and cache version."""

    data: bytes
    content_type: str
    updated_at: datetime.datetime


class PartNumberPage(NamedTuple):
    """One bounded page of the management list and the matching total."""

    rows: list[PartNumber]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total


def master_snapshot(master: PartNumber) -> dict[str, str | None]:
    """The audited details of a master — never image data."""
    return {
        "part_number": master.part_number,
        "name": master.name,
        "current_revision": master.current_revision,
        "erp_id": master.erp_id,
    }


def _already_saved(part_number: str) -> str:
    return f"Part Number “{part_number}” already has saved details."


def _not_found(part_number: str) -> NotFoundError:
    return NotFoundError(f"Part Number {part_number} has no saved details.")


def _detail_text(value: object, field: str) -> str | None:
    """Normalize one optional detail: text (trimmed, blank → NULL) or None.

    NUL (U+0000) is refused as not text: PostgreSQL text cannot hold it,
    and the driver would otherwise fail at flush with an unhandled
    DataError.
    """
    if value is not None and (not isinstance(value, str) or "\x00" in value):
        raise InvalidInputError(f"{_DETAIL_LABELS[field]} must be text.")
    return optional_text(value)


def _image_digest(master: PartNumber) -> dict[str, str | int] | None:
    if master.image is None or master.image_type is None:
        return None
    return images.image_digest(master.image, master.image_type)


def _lock_master(session: Session, part_number: str, *, with_image: bool = False) -> PartNumber:
    """Load one master under its row lock (``SELECT … FOR UPDATE``).

    The ONLY lock an edit, image write or delete takes: never the PN
    advisory lock and never a production row, so these writes and the
    production commands never wait on each other. ``populate_existing``
    re-reads the row under the lock, so a write that lost a race to a
    delete finds no row and answers 404 with nothing written. The image
    bytes are loaded only when the caller compares or audits them.
    """
    master = session.get(
        PartNumber,
        part_number,
        options=[undefer(PartNumber.image)] if with_image else None,
        populate_existing=True,
        with_for_update=True,
    )
    if master is None:
        raise _not_found(part_number)
    return master


def _commit_detached(
    session: Session, master: PartNumber, conflict_messages: dict[str, str]
) -> PartNumber:
    """Commit a management write and return the master as committed.

    The route builds its response after the commit. An attached master
    would be expired by the commit and reloaded in a NEW transaction —
    and a delete of the same PN committed in between would fail that
    reload although this write and its audit row are committed. So
    every reported column is re-read under the lock this transaction
    holds and the master is detached before the commit: the response
    carries exactly what this transaction committed. The image bytes
    stay unloaded (deferred).
    """
    session.refresh(master)
    session.expunge(master)
    commit(session, conflict_messages)
    return master


def part_number_page(
    session: Session, *, search: str | None, offset: int, limit: int
) -> PartNumberPage:
    """One page of the management list in canonical-PN order.

    A non-blank ``search`` is an escaped case-insensitive contains-match
    over the PN, the Name / Description, the revision and the ERP id
    (GUI_DESIGN §14.1); ``total`` counts every match. The API bounds
    ``offset`` and ``limit``.
    """
    rows_query = select(PartNumber).order_by(PartNumber.part_number).offset(offset).limit(limit)
    count_query = select(func.count()).select_from(PartNumber)
    if search is not None and search.strip():
        condition = _matches_any(
            search,
            PartNumber.part_number,
            PartNumber.name,
            PartNumber.current_revision,
            PartNumber.erp_id,
        )
        rows_query = rows_query.where(condition)
        count_query = count_query.where(condition)
    total = session.scalar(count_query) or 0
    return PartNumberPage(
        rows=list(session.scalars(rows_query)), total=total, offset=offset, limit=limit
    )


def create_part_number(
    session: Session,
    value: object,
    *,
    name: object = None,
    current_revision: object = None,
    erp_id: object = None,
    actor_user_id: int,
) -> PartNumber:
    """Create a master with its details as its own transaction (create-only).

    An existing master for the canonical PN is a conflict, never reused.
    The PN advisory lock is taken before the pre-check, so this create
    serializes with create-on-first-use (the Work Order save and the
    Scan Station receipt run it under the same lock): whichever commits
    first wins — a production command that comes second reuses the
    master, a create that comes second answers the conflict. The
    primary key stays the backstop at the INSERT and at COMMIT. No
    image here: the image is its own raw-body write.
    """
    canonical = canonical_part_number(value)
    details = {
        "name": _detail_text(name, "name"),
        "current_revision": _detail_text(current_revision, "current_revision"),
        "erp_id": _detail_text(erp_id, "erp_id"),
    }
    conflicts = {"pk_part_numbers": _already_saved(canonical)}
    acquire_part_number_lock(session, canonical)
    if session.get(PartNumber, canonical) is not None:
        raise ConflictError(_already_saved(canonical))
    master = PartNumber(part_number=canonical, **details)
    session.add(master)
    flush(session, conflicts)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.PART_NUMBER,
        entity_id=canonical,
        before_data=None,
        after_data=master_snapshot(master),
        actor_user_id=actor_user_id,
    )
    return _commit_detached(session, master, conflicts)


def update_part_number(
    session: Session,
    value: object,
    *,
    name: object = UNSET,
    current_revision: object = UNSET,
    erp_id: object = UNSET,
    actor_user_id: int,
) -> PartNumber:
    """Apply the provided details; omitted fields stay, ``None`` clears.

    A request that changes nothing writes and audits nothing, so a
    retry after an unknown outcome is safe.
    """
    canonical = canonical_part_number(value)
    provided = {
        field: _detail_text(raw, field)
        for field, raw in (
            ("name", name),
            ("current_revision", current_revision),
            ("erp_id", erp_id),
        )
        if not isinstance(raw, UnsetType)
    }
    master = _lock_master(session, canonical)
    before = master_snapshot(master)
    changes = {field: text for field, text in provided.items() if text != before[field]}
    if not changes:
        return master

    # Read-before-assign: no query runs from here to the explicit flush.
    for field, text in changes.items():
        setattr(master, field, text)
    master.updated_at = func.now()
    flush(session, PART_NUMBER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.PART_NUMBER,
        entity_id=canonical,
        before_data=before,
        after_data=master_snapshot(master),
        actor_user_id=actor_user_id,
    )
    return _commit_detached(session, master, PART_NUMBER_CONFLICTS)


def delete_part_number(session: Session, value: object, *, actor_user_id: int) -> None:
    """Hard-delete the master — details and image — and nothing else.

    No production table references the master (PROJECT_PROFILE §8.1),
    so demand, QuantityFlows, Movements, allocations and history are
    neither read nor written, and no PN advisory lock is taken. The one
    ``DELETED`` audit row keeps the details and the image digest.
    """
    canonical = canonical_part_number(value)
    master = _lock_master(session, canonical, with_image=True)
    before: dict[str, Any] = {**master_snapshot(master), "image": _image_digest(master)}
    session.delete(master)
    flush(session, PART_NUMBER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.DELETED,
        entity_type=AuditEntityType.PART_NUMBER,
        entity_id=canonical,
        before_data=before,
        after_data=None,
        actor_user_id=actor_user_id,
    )
    commit(session, PART_NUMBER_CONFLICTS)


def set_part_number_image(
    session: Session,
    value: object,
    *,
    data: bytes,
    declared_type: str | None,
    actor_user_id: int,
) -> PartNumber:
    """Store or replace the PN image; identical bytes and type are a no-op.

    The image is validated before the lock — nothing is written on a
    refusal. The no-op makes an upload safely retryable after an
    unknown outcome.
    """
    canonical = canonical_part_number(value)
    content_type = images.validate_image(data, declared_type)
    master = _lock_master(session, canonical, with_image=True)
    before = _image_digest(master)
    after = images.image_digest(data, content_type)
    if before == after:
        return master

    master.image = data
    master.image_type = content_type
    master.image_updated_at = func.now()
    master.updated_at = func.now()
    flush(session, PART_NUMBER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.PART_NUMBER,
        entity_id=canonical,
        before_data={"image": before},
        after_data={"image": after},
        actor_user_id=actor_user_id,
    )
    return _commit_detached(session, master, PART_NUMBER_CONFLICTS)


def remove_part_number_image(session: Session, value: object, *, actor_user_id: int) -> PartNumber:
    """Remove the PN image; a master without one is a no-op."""
    canonical = canonical_part_number(value)
    master = _lock_master(session, canonical, with_image=True)
    before = _image_digest(master)
    if before is None:
        return master

    master.image = None
    master.image_type = None
    master.image_updated_at = None
    master.updated_at = func.now()
    flush(session, PART_NUMBER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.PART_NUMBER,
        entity_id=canonical,
        before_data={"image": before},
        after_data={"image": None},
        actor_user_id=actor_user_id,
    )
    return _commit_detached(session, master, PART_NUMBER_CONFLICTS)


def get_part_number_image(session: Session, value: object) -> PartNumberImage:
    """The stored PN image; no master or no image → ``NotFoundError``."""
    canonical = canonical_part_number(value)
    master = session.get(PartNumber, canonical, options=[undefer(PartNumber.image)])
    if master is None:
        raise _not_found(canonical)
    if master.image is None or master.image_type is None or master.image_updated_at is None:
        raise NotFoundError(f"Part Number {canonical} has no image.")
    return PartNumberImage(master.image, master.image_type, master.image_updated_at)
