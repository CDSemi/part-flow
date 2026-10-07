"""Global application policies (Phase 13 — Administration → Policies).

Application-layer read/write of the ``application_policy`` singleton
(PLAN CD3): one typed row, seeded by its migration, so it always exists;
each Administration section owns its typed columns. Slice 4 owns the
Worker sessions section's sliding inactivity timeout (PROJECT_PROFILE
§19; owner decision OD-2: whole minutes, 1-720, default 15). The
per-Area override lives on the Area and is written through the Area
service (`environment`), audited as the Area. Slice 5 adds the same
section's three badge-confirmation options (`badge_confirm_done`,
`badge_confirm_queue`, `badge_confirm_undo`; PROFILE §19, default on),
read by `station_identity.final_gate`. Slice 6 adds the Correction
permissions section's Undo reason policy (`undo_reason_required`;
PROFILE §16 "require a reason when configured", owner default OD-6: one
global switch, default off), read by the Undo command and its preview
through the column-only `is_undo_reason_required`. Slice 9 adds the
Due Soon warning panel of Administration → Settings (GUI_DESIGN §3 rule
12, §9; owner default OD-5: one global policy, 2 days / 15 % / 7 days):
the minimum and maximum warning days (0-365, minimum never above
maximum) and the whole lead-time warning percentage (1-100), audited
under its own ``entity_id`` ``due-soon`` and written as a full replace
of the three fields (one form with a cross-field rule). It is display
configuration behind every derived due countdown; no production command
reads it. History archival & purge (slice 11): the Movement-history
retention period — whole months 12-1200 or none — stored as configuration
for the Phase 16 archival maintenance; nothing in Phase 13 reads it,
archives or purges (PROJECT_PROFILE §28 "retention settings live in
Administration/configuration, not in production workflow logic").
Audited under its own ``entity_id`` ``data-retention``; clearing the
period (NULL) is a valid, audited change.

A write follows the configuration protocol: the row is locked first
(``FOR NO KEY UPDATE``) and re-read, the value validated, a no-op
writes nothing, and an effective change appends exactly one
``audit_events`` row in the same transaction (entity
``ApplicationPolicy``, ``entity_id`` the section, ``actor_reference``
NULL until Phase 14, the full section as the before / after snapshot).
A section write assigns only its own section's columns, so writes of
two sections serialize on the row lock and never overwrite each other;
``updated_at`` is the ONE row-level timestamp every section's effective
write moves. The Worker sessions write is a PARTIAL merge: a field not
given keeps its stored value, and the given fields merge onto the
locked row, so two administrators changing different fields both keep
their change. A policy change never touches open Worker Sessions, open
dialogs or Movements and never rewrites history: it applies from each
session's next refresh or sign-in, and from each command's own read.
"""

from typing import Any, Final, TypeGuard

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application import audit
from app.application.common import UNSET, UnsetType, commit
from app.application.errors import InvalidInputError, NotFoundError
from app.domain.enums import AuditEntityType, AuditEventType
from app.infrastructure.models import (
    DUE_SOON_DAYS_MAX,
    DUE_SOON_DAYS_MIN,
    DUE_SOON_PERCENT_MAX,
    DUE_SOON_PERCENT_MIN,
    RETENTION_PERIOD_MONTHS_MAX,
    RETENTION_PERIOD_MONTHS_MIN,
    WORKER_SESSION_TIMEOUT_MAX,
    WORKER_SESSION_TIMEOUT_MIN,
    ApplicationPolicy,
)

_POLICY_ID: Final = 1
# The audit entity_id of the Worker sessions section (CD3).
WORKER_SESSIONS_SECTION: Final = "worker-sessions"
# The audit entity_id of the Correction permissions section (CD3).
CORRECTION_PERMISSIONS_SECTION: Final = "correction-permissions"
# The audit entity_id and API path segment of the Due Soon warning panel
# (Administration → Settings; CD3, S9-OD1).
DUE_SOON_SECTION: Final = "due-soon"
# The audit entity_id and API path segment of Administration →
# History archival & purge (CD3: the sidebar section id, S11-OD2).
DATA_RETENTION_SECTION: Final = "data-retention"
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
    return {
        "worker_session_timeout_minutes": policy.worker_session_timeout_minutes,
        "badge_confirm_done": policy.badge_confirm_done,
        "badge_confirm_queue": policy.badge_confirm_queue,
        "badge_confirm_undo": policy.badge_confirm_undo,
    }


def update_worker_session_policy(
    session: Session,
    *,
    timeout_minutes: object = UNSET,
    badge_confirm_done: object = UNSET,
    badge_confirm_queue: object = UNSET,
    badge_confirm_undo: object = UNSET,
) -> ApplicationPolicy:
    """Merge the given Worker sessions settings; a no-op writes and audits nothing.

    The default Worker Session timeout and the three badge-confirmation
    options; a field not given keeps its stored value.
    """
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    options = {
        "badge_confirm_done": badge_confirm_done,
        "badge_confirm_queue": badge_confirm_queue,
        "badge_confirm_undo": badge_confirm_undo,
    }
    given_options = {
        name: value for name, value in options.items() if not isinstance(value, UnsetType)
    }
    if isinstance(timeout_minutes, UnsetType) and not given_options:
        raise InvalidInputError("Change at least one Worker session setting.")
    if not isinstance(timeout_minutes, UnsetType) and not is_timeout_minutes(timeout_minutes):
        raise InvalidInputError(
            "The Worker session timeout must be a whole number of minutes from 1 to 720."
        )
    if any(not isinstance(value, bool) for value in given_options.values()):
        raise InvalidInputError("Each badge-confirmation option must be On or Off.")
    before = _worker_sessions_snapshot(policy)
    after = dict(before)
    if not isinstance(timeout_minutes, UnsetType):
        after["worker_session_timeout_minutes"] = timeout_minutes
    after.update(given_options)
    if after == before:
        return policy
    policy.worker_session_timeout_minutes = after["worker_session_timeout_minutes"]
    policy.badge_confirm_done = after["badge_confirm_done"]
    policy.badge_confirm_queue = after["badge_confirm_queue"]
    policy.badge_confirm_undo = after["badge_confirm_undo"]
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


def _correction_permissions_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {"undo_reason_required": policy.undo_reason_required}


def update_correction_permissions_policy(
    session: Session, *, undo_reason_required: object
) -> ApplicationPolicy:
    """Turn the Undo reason requirement on or off; a no-op writes and audits nothing."""
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    if not isinstance(undo_reason_required, bool):
        raise InvalidInputError("The Undo reason setting must be On or Off.")
    if policy.undo_reason_required == undo_reason_required:
        return policy
    before = _correction_permissions_snapshot(policy)
    policy.undo_reason_required = undo_reason_required
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=CORRECTION_PERMISSIONS_SECTION,
        before_data=before,
        after_data=_correction_permissions_snapshot(policy),
    )
    commit(session, {})
    return policy


def _is_due_soon_days(value: object) -> TypeGuard[int]:
    """Whole warning days, 0-365 (a bool is never a number)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and DUE_SOON_DAYS_MIN <= value <= DUE_SOON_DAYS_MAX
    )


def _is_due_soon_percent(value: object) -> TypeGuard[int]:
    """A whole lead-time warning percentage, 1-100 (a bool is never a number)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and DUE_SOON_PERCENT_MIN <= value <= DUE_SOON_PERCENT_MAX
    )


def _due_soon_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {
        "due_soon_min_days": policy.due_soon_min_days,
        "due_soon_lead_time_percent": policy.due_soon_lead_time_percent,
        "due_soon_max_days": policy.due_soon_max_days,
    }


def update_due_soon_policy(
    session: Session, *, min_days: object, lead_time_percent: object, max_days: object
) -> ApplicationPolicy:
    """Replace the Due Soon warning policy; a no-op writes and audits nothing."""
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    if not _is_due_soon_days(min_days):
        raise InvalidInputError("Minimum warning days must be a whole number from 0 to 365.")
    if not _is_due_soon_days(max_days):
        raise InvalidInputError("Maximum warning days must be a whole number from 0 to 365.")
    if not _is_due_soon_percent(lead_time_percent):
        raise InvalidInputError(
            "The lead-time warning percentage must be a whole number from 1 to 100."
        )
    if min_days > max_days:
        raise InvalidInputError("Minimum warning days cannot be greater than maximum warning days.")
    before = _due_soon_snapshot(policy)
    after = {
        "due_soon_min_days": min_days,
        "due_soon_lead_time_percent": lead_time_percent,
        "due_soon_max_days": max_days,
    }
    if after == before:
        return policy
    policy.due_soon_min_days = min_days
    policy.due_soon_lead_time_percent = lead_time_percent
    policy.due_soon_max_days = max_days
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=DUE_SOON_SECTION,
        before_data=before,
        after_data=_due_soon_snapshot(policy),
    )
    commit(session, {})
    return policy


def is_retention_period_months(value: object) -> TypeGuard[int]:
    """A whole number of months from 12 to 1200 (a bool is never a number)."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and RETENTION_PERIOD_MONTHS_MIN <= value <= RETENTION_PERIOD_MONTHS_MAX
    )


def _retention_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {"retention_period_months": policy.retention_period_months}


def update_retention_policy(
    session: Session, *, retention_period_months: object
) -> ApplicationPolicy:
    """Set or clear the Movement-history retention period; a no-op writes and audits nothing.

    ``None`` clears the period (no retention period). Saving it never
    archives, deletes, schedules or previews anything.
    """
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    period: int | None
    if retention_period_months is None:
        period = None
    elif is_retention_period_months(retention_period_months):
        period = retention_period_months
    else:
        raise InvalidInputError(
            "The retention period must be a whole number of months from 12 to 1200,"
            " or no retention period."
        )
    if policy.retention_period_months == period:
        return policy
    before = _retention_snapshot(policy)
    policy.retention_period_months = period
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=DATA_RETENTION_SECTION,
        before_data=before,
        after_data=_retention_snapshot(policy),
    )
    commit(session, {})
    return policy


def is_undo_reason_required(session: Session) -> bool:
    """The Undo reason policy now, read as one column (unlocked).

    A column select loads no ApplicationPolicy instance into the Session,
    so the policy reads of the identity resolver later in the same Undo
    (worker-session timeout, badge-confirmation options — judged after
    their own locks) still query the committed row themselves.
    """
    value = session.scalar(
        select(ApplicationPolicy.undo_reason_required).where(ApplicationPolicy.id == _POLICY_ID)
    )
    if value is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    return value
