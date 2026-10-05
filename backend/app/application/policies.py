"""Global application policies (Phase 13 slice 4 — Administration → Worker sessions).

Application-layer read/write of the ``application_policy`` singleton
(PLAN CD3): one typed row, seeded by its migration, so it always exists;
each Administration section owns its typed columns. Slice 4 owns the
Worker sessions section's sliding inactivity timeout (PROJECT_PROFILE
§19; owner decision OD-2: whole minutes, 1-720, default 15). The
per-Area override lives on the Area and is written through the Area
service (`environment`), audited as the Area.

A write follows the configuration protocol: the row is locked first
(``FOR NO KEY UPDATE``) and re-read, the value validated, a no-op
writes nothing, and an effective change appends exactly one
``audit_events`` row in the same transaction (entity
``ApplicationPolicy``, ``entity_id`` the section, ``actor_reference``
NULL until Phase 14). A policy change never touches open Worker
Sessions: it applies from each session's next refresh or sign-in.
"""

from typing import Any, Final, TypeGuard

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import commit
from app.application.errors import InvalidInputError, NotFoundError
from app.domain.enums import AuditEntityType, AuditEventType
from app.infrastructure.models import (
    WORKER_SESSION_TIMEOUT_MAX,
    WORKER_SESSION_TIMEOUT_MIN,
    ApplicationPolicy,
)

_POLICY_ID: Final = 1
# The audit entity_id of the Worker sessions section (CD3).
WORKER_SESSIONS_SECTION: Final = "worker-sessions"
# Lock-first mode of a policy write: the FOR NO KEY UPDATE its own UPDATE takes.
_EDIT_LOCK: Final = {"key_share": True}


def is_timeout_minutes(value: object) -> TypeGuard[int]:
    """A whole number of minutes in the OD-2 range (a bool is never a number)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and WORKER_SESSION_TIMEOUT_MIN <= value <= WORKER_SESSION_TIMEOUT_MAX
    )


def get_policy(session: Session) -> ApplicationPolicy:
    """The singleton (always seeded by migration 0020)."""
    policy = session.get(ApplicationPolicy, _POLICY_ID)
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    return policy


def _worker_sessions_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {"worker_session_timeout_minutes": policy.worker_session_timeout_minutes}


def update_worker_session_policy(session: Session, *, timeout_minutes: object) -> ApplicationPolicy:
    """Set the default Worker Session timeout; a no-op writes and audits nothing."""
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    if not is_timeout_minutes(timeout_minutes):
        raise InvalidInputError(
            "The Worker session timeout must be a whole number of minutes from 1 to 720."
        )
    if timeout_minutes == policy.worker_session_timeout_minutes:
        return policy
    before = _worker_sessions_snapshot(policy)
    policy.worker_session_timeout_minutes = timeout_minutes
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=WORKER_SESSIONS_SECTION,
        before_data=before,
        after_data=_worker_sessions_snapshot(policy),
    )
    commit(session, {})
    return policy
