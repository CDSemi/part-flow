"""Worker registry services (Phase 13 — Administration → Workers).

Application-layer operations behind the Workers registry: the Scan
Station production audit identity of a person operating the stations
(PROJECT_PROFILE §7 Worker, §8.13) — never a User, the application
account.

Rules owned here (PROJECT_PROFILE §8.13, §10; owner decisions OD-3,
OD-10, OD-14):

- The badge barcode is the company's existing employee badge. It goes
  through the one domain rule (``app.domain.worker_badge``): trimmed,
  UPPERCASE, non-empty, at most 128 characters, never ``PF:``. The same
  rule runs on save and on scan (:func:`resolve_badge`), so badges are
  case-insensitive, and a badge is unique among ALL Workers, inactive
  ones included — the UNIQUE constraint over the canonical value is the
  authority; the pre-check only gives the friendly message naming the
  holder.
- Names are required but not unique.
- Workers are deactivated, never deleted (OD-14): no delete service
  exists.
- The optional avatar is stored on the row (CD1/OD-10) after the shared
  image validation (``app.application.images``); its bytes are loaded
  only where they are compared or served.
- Every effective write appends exactly one ``audit_events`` row in the
  SAME transaction (entity ``Worker``, ``actor_reference`` NULL until
  Phase 14). Profile rows snapshot ``{name, badge_barcode, is_active}``;
  avatar rows snapshot ``{"avatar": digest-or-null}`` — never bytes.
  Rejected writes and no-ops append nothing.

Each mutating service commits its own transaction, so a 2xx response
always reflects committed state. A mutation of an existing Worker locks
its row first, so concurrent writes serialize and every audit row's
``before_data`` is the committed predecessor within its facet. Every
field is validated and every pre-check query runs BEFORE the first
attribute assignment, and all flushes go through the conflict-
translating helper, so autoflush never emits a write outside it.
"""

import datetime
from typing import Any, Final, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, undefer

from app.application import audit, images
from app.application.common import UNSET, UnsetType, commit, flush, required_flag, required_text
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.domain.enums import AuditEntityType, AuditEventType
from app.domain.worker_badge import InvalidBadgeBarcodeError, normalize_badge_barcode
from app.infrastructure.models import Worker

_WORKER_CONFLICTS: Final = {
    "uq_workers_badge_barcode": "This badge barcode is already assigned to another Worker.",
}


class WorkerAvatar(NamedTuple):
    """A stored avatar as served: bytes, media type and cache version."""

    data: bytes
    content_type: str
    updated_at: datetime.datetime


def profile_snapshot(worker: Worker) -> dict[str, Any]:
    """The audited profile facet; avatar data never belongs to it."""
    return {
        "name": worker.name,
        "badge_barcode": worker.badge_barcode,
        "is_active": worker.is_active,
    }


def canonical_badge_barcode(value: object) -> str:
    """Normalize input to the canonical badge or raise ``InvalidInputError``.

    Thin translation of the framework-independent domain rule into the
    application error vocabulary — the rule itself lives only in
    ``app.domain.worker_badge``.
    """
    if not isinstance(value, str):
        raise InvalidInputError("Badge barcode must be text.")
    try:
        return normalize_badge_barcode(value)
    except InvalidBadgeBarcodeError as exc:
        raise InvalidInputError(str(exc)) from exc


def _reject_duplicate_badge(session: Session, badge: str, exclude_id: int | None = None) -> None:
    query = select(Worker.name, Worker.is_active).where(Worker.badge_barcode == badge).limit(1)
    if exclude_id is not None:
        query = query.where(Worker.id != exclude_id)
    holder = session.execute(query).first()
    if holder is not None:
        name, is_active = holder
        suffix = "" if is_active else " (inactive)"
        raise ConflictError(f"This badge barcode is already assigned to {name}{suffix}.")


def _lock_worker(session: Session, worker_id: int, *, with_avatar: bool = False) -> Worker:
    """Load one Worker under its row lock (``SELECT … FOR UPDATE``).

    The avatar bytes are loaded only when the caller compares them.
    """
    worker = session.get(
        Worker,
        worker_id,
        options=[undefer(Worker.avatar_image)] if with_avatar else None,
        populate_existing=True,
        with_for_update=True,
    )
    if worker is None:
        raise NotFoundError(f"Worker {worker_id} does not exist.")
    return worker


def _avatar_digest(worker: Worker) -> dict[str, str | int] | None:
    if worker.avatar_image is None or worker.avatar_image_type is None:
        return None
    return images.image_digest(worker.avatar_image, worker.avatar_image_type)


def list_workers(session: Session) -> list[Worker]:
    """All Workers, active and inactive, by name; avatar bytes stay unloaded."""
    return list(session.scalars(select(Worker).order_by(Worker.name, Worker.id)))


def create_worker(session: Session, *, name: object, badge_barcode: object) -> Worker:
    clean_name = required_text(name, "Worker name")
    badge = canonical_badge_barcode(badge_barcode)
    _reject_duplicate_badge(session, badge)
    worker = Worker(name=clean_name, badge_barcode=badge, is_active=True)
    session.add(worker)
    # The id is the audit entity id; a badge race lost here surfaces as
    # the same conflict as one lost at COMMIT.
    flush(session, _WORKER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.WORKER,
        entity_id=str(worker.id),
        before_data=None,
        after_data=profile_snapshot(worker),
    )
    commit(session, _WORKER_CONFLICTS)
    return worker


def update_worker(
    session: Session,
    worker_id: int,
    *,
    name: object = UNSET,
    badge_barcode: object = UNSET,
    is_active: object = UNSET,
) -> Worker:
    """Apply the provided profile fields; a no-op writes and audits nothing.

    Deactivation and reactivation carry no extra guard yet; the slices
    that make Workers operational add theirs here, before any
    assignment.
    """
    worker = _lock_worker(session, worker_id)
    before = profile_snapshot(worker)

    changes: dict[str, Any] = {}
    if not isinstance(name, UnsetType):
        clean_name = required_text(name, "Worker name")
        if clean_name != worker.name:
            changes["name"] = clean_name
    if not isinstance(badge_barcode, UnsetType):
        # A case or whitespace variant of the stored badge is no change.
        badge = canonical_badge_barcode(badge_barcode)
        if badge != worker.badge_barcode:
            changes["badge_barcode"] = badge
    if not isinstance(is_active, UnsetType):
        active = required_flag(is_active, "Worker active status")
        if active != worker.is_active:
            changes["is_active"] = active
    if not changes:
        return worker
    if "badge_barcode" in changes:
        _reject_duplicate_badge(session, changes["badge_barcode"], exclude_id=worker.id)

    # Read-before-assign: no query runs from here to the explicit flush.
    for field, value in changes.items():
        setattr(worker, field, value)
    worker.updated_at = func.now()
    flush(session, _WORKER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.WORKER,
        entity_id=str(worker.id),
        before_data=before,
        after_data=profile_snapshot(worker),
    )
    commit(session, _WORKER_CONFLICTS)
    return worker


def set_worker_avatar(
    session: Session, worker_id: int, *, data: bytes, declared_type: str | None
) -> Worker:
    """Store or replace the avatar; identical bytes and type are a no-op.

    The image is validated before the lock — nothing is written on a
    refusal. The no-op makes an upload safely retryable after an
    unknown outcome.
    """
    content_type = images.validate_image(data, declared_type)
    worker = _lock_worker(session, worker_id, with_avatar=True)
    before = _avatar_digest(worker)
    after = images.image_digest(data, content_type)
    if before == after:
        return worker

    worker.avatar_image = data
    worker.avatar_image_type = content_type
    worker.avatar_image_updated_at = func.now()
    worker.updated_at = func.now()
    flush(session, _WORKER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.WORKER,
        entity_id=str(worker.id),
        before_data={"avatar": before},
        after_data={"avatar": after},
    )
    commit(session, _WORKER_CONFLICTS)
    return worker


def remove_worker_avatar(session: Session, worker_id: int) -> Worker:
    """Remove the avatar; a Worker without one is a no-op."""
    worker = _lock_worker(session, worker_id, with_avatar=True)
    before = _avatar_digest(worker)
    if before is None:
        return worker

    worker.avatar_image = None
    worker.avatar_image_type = None
    worker.avatar_image_updated_at = None
    worker.updated_at = func.now()
    flush(session, _WORKER_CONFLICTS)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.WORKER,
        entity_id=str(worker.id),
        before_data={"avatar": before},
        after_data={"avatar": None},
    )
    commit(session, _WORKER_CONFLICTS)
    return worker


def get_worker_avatar(session: Session, worker_id: int) -> WorkerAvatar:
    worker = session.get(Worker, worker_id, options=[undefer(Worker.avatar_image)])
    if worker is None:
        raise NotFoundError(f"Worker {worker_id} does not exist.")
    if (
        worker.avatar_image is None
        or worker.avatar_image_type is None
        or worker.avatar_image_updated_at is None
    ):
        raise NotFoundError("This Worker has no avatar.")
    return WorkerAvatar(
        worker.avatar_image, worker.avatar_image_type, worker.avatar_image_updated_at
    )


def resolve_badge(session: Session, raw: object) -> Worker | None:
    """The ACTIVE Worker a scanned badge identifies, or ``None``.

    Pure read. The scan goes through the same canonicalization as a
    save, so any letter-case or surrounding-whitespace variant of a
    stored badge resolves to its Worker (OD-3). Non-text input, a value
    the badge rule rejects, an unknown badge and an inactive Worker's
    badge (PROJECT_PROFILE §10) all resolve to ``None``. At most one
    Worker matches: the badge is UNIQUE.
    """
    if not isinstance(raw, str):
        return None
    try:
        badge = normalize_badge_barcode(raw)
    except InvalidBadgeBarcodeError:
        return None
    return session.scalar(
        select(Worker).where(Worker.badge_barcode == badge, Worker.is_active.is_(True))
    )
