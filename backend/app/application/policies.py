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
period (NULL) is a valid, audited change. Phase 14 slice 1 adds the User
sign-in panel of Administration → Settings (owner decisions OD-P1–OD-P3;
``entity_id`` ``sign-in``, a partial merge like Worker sessions): whether
user sign-ins expire and after how many whole days (1-365, default 30),
the failed sign-ins before a lock (3-100, default 10), the lock duration
(1-1440 minutes, default 15) and whether an administrator-set password
must be replaced at the next sign-in (default on). Every request reads
it, so a change applies to open sign-ins at their next request; its audit
row names the signed-in administrator (``actor_user_id``). Phase 16 adds
Administration → Backups (owner decision of 2026-11-13; ``entity_id``
``backups``, a full replace like Due Soon): the interval of the scheduled
backups (1, 2, 3, 4, 6, 8, 12 or 24 hours, default 1) and the hourly
(0-720, default 48), daily (1-3650, default 30) and weekly (0-520, default
52) backups their rotation keeps. Only the host's hourly backup task
(``app.cli backup-policy``) and ``status`` read it, from their next run.
Phase 17 adds the outgoing mail server of Administration → Settings (owner
decision of 2026-11-13; ``entity_id`` ``mail-server``, a full replace like
Backups except the password, which is kept unless replaced or cleared):
the host (none = no mail server), the port (1-65535, default 587), the
connection security (STARTTLS, TLS or NONE, default STARTTLS), the
optional user name and password of the sign-in to the server (never with
NONE) and the sender address. The password is a credential: it lives in
``mail_server_credentials`` (one row at most; none = no password), never on
the policy row, and is read for sending only — no answer, log or audit row
carries it. PartFlow reads the server when it sends a message
(``app.application.outgoing_mail``). The owner decisions of 2026-11-17 add
the server's timeout (5-120 seconds, default 20) to that section and the
Two-step sign-in panel of Administration → Settings (``entity_id``
``two-step``, a partial merge like User sign-in): every number of two-step
sign-in, each a whole number in its range (``TWO_STEP_POLICY_RANGES``).
Every sign-in step reads it, so a change applies from the next step.

A write follows the configuration protocol: the row is locked first
(``FOR NO KEY UPDATE``) and re-read, the value validated, a no-op
writes nothing, and an effective change appends exactly one
``audit_events`` row in the same transaction (entity
``ApplicationPolicy``, ``entity_id`` the section, ``actor_user_id`` the
signed-in User (Phase 14 slices 1–2; ``actor_reference`` is legacy and
stays NULL), the full section as the before / after snapshot).
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

import datetime
import ipaddress
import re
from typing import Any, Final, NamedTuple, TypeGuard

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application import audit, users
from app.application.common import UNSET, UnsetType, commit
from app.application.errors import InvalidInputError, NotFoundError
from app.domain.enums import AuditEntityType, AuditEventType, MailSecurity
from app.infrastructure.mail import SmtpServer
from app.infrastructure.models import (
    BACKUP_INTERVAL_HOURS,
    BACKUP_KEEP_DAILY_MAX,
    BACKUP_KEEP_DAILY_MIN,
    BACKUP_KEEP_HOURLY_MAX,
    BACKUP_KEEP_HOURLY_MIN,
    BACKUP_KEEP_WEEKLY_MAX,
    BACKUP_KEEP_WEEKLY_MIN,
    DUE_SOON_DAYS_MAX,
    DUE_SOON_DAYS_MIN,
    DUE_SOON_PERCENT_MAX,
    DUE_SOON_PERCENT_MIN,
    MAIL_HOST_MAX_LENGTH,
    MAIL_PASSWORD_MAX_LENGTH,
    MAIL_PORT_MAX,
    MAIL_PORT_MIN,
    MAIL_TIMEOUT_SECONDS_RANGE,
    MAIL_USERNAME_MAX_LENGTH,
    RETENTION_PERIOD_MONTHS_MAX,
    RETENTION_PERIOD_MONTHS_MIN,
    SIGN_IN_LOCKOUT_ATTEMPTS_MAX,
    SIGN_IN_LOCKOUT_ATTEMPTS_MIN,
    SIGN_IN_LOCKOUT_MINUTES_MAX,
    SIGN_IN_LOCKOUT_MINUTES_MIN,
    TWO_STEP_POLICY_RANGES,
    USER_SESSION_DAYS_MAX,
    USER_SESSION_DAYS_MIN,
    WORKER_SESSION_TIMEOUT_MAX,
    WORKER_SESSION_TIMEOUT_MIN,
    ApplicationPolicy,
    MailServerCredential,
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
# The audit entity_id and API path segment of Administration → Settings →
# User sign-in (Phase 14 slice 1; CD3).
SIGN_IN_SECTION: Final = "sign-in"
# The audit entity_id and API path segment of Administration → Backups
# (Phase 16; CD3: the sidebar section id).
BACKUPS_SECTION: Final = "backups"
# The audit entity_id and API path segment of Administration → Settings →
# Outgoing mail server (Phase 17; CD3).
MAIL_SERVER_SECTION: Final = "mail-server"
# The audit entity_id and API path segment of Administration → Settings →
# Two-step sign-in (Phase 17, owner decisions of 2026-11-17; CD3).
TWO_STEP_SECTION: Final = "two-step"
# The scheduled backup intervals Administration → Backups admits: the
# whole hours that divide the day (the backup-due command's choices too).
BACKUP_INTERVALS: Final = BACKUP_INTERVAL_HOURS
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
    actor_user_id: int | None,
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
        actor_user_id=actor_user_id,
    )
    commit(session, {})
    return policy


def _correction_permissions_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {"undo_reason_required": policy.undo_reason_required}


def update_correction_permissions_policy(
    session: Session, *, undo_reason_required: object, actor_user_id: int | None
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
        actor_user_id=actor_user_id,
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
    session: Session,
    *,
    min_days: object,
    lead_time_percent: object,
    max_days: object,
    actor_user_id: int | None,
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
        actor_user_id=actor_user_id,
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
    session: Session, *, retention_period_months: object, actor_user_id: int | None
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
        actor_user_id=actor_user_id,
    )
    commit(session, {})
    return policy


def _is_whole_in(value: object, minimum: int, maximum: int) -> TypeGuard[int]:
    """A whole number in [minimum, maximum] (a bool is never a number)."""
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _sign_in_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {
        "user_session_expires": policy.user_session_expires,
        "user_session_days": policy.user_session_days,
        "sign_in_lockout_attempts": policy.sign_in_lockout_attempts,
        "sign_in_lockout_minutes": policy.sign_in_lockout_minutes,
        "require_password_change": policy.require_password_change,
    }


def update_sign_in_policy(
    session: Session,
    *,
    actor_user_id: int,
    user_session_expires: object = UNSET,
    user_session_days: object = UNSET,
    sign_in_lockout_attempts: object = UNSET,
    sign_in_lockout_minutes: object = UNSET,
    require_password_change: object = UNSET,
) -> ApplicationPolicy:
    """Merge the given user sign-in settings; a no-op writes and audits nothing.

    The Worker sessions protocol: a field not given keeps its stored
    value. The audit row names the signed-in administrator.
    """
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    given = {
        name: value
        for name, value in {
            "user_session_expires": user_session_expires,
            "user_session_days": user_session_days,
            "sign_in_lockout_attempts": sign_in_lockout_attempts,
            "sign_in_lockout_minutes": sign_in_lockout_minutes,
            "require_password_change": require_password_change,
        }.items()
        if not isinstance(value, UnsetType)
    }
    if not given:
        raise InvalidInputError("Change at least one user sign-in setting.")
    if "user_session_days" in given and not _is_whole_in(
        given["user_session_days"], USER_SESSION_DAYS_MIN, USER_SESSION_DAYS_MAX
    ):
        raise InvalidInputError(
            "User sign-ins must expire after a whole number of days from 1 to 365."
        )
    if "sign_in_lockout_attempts" in given and not _is_whole_in(
        given["sign_in_lockout_attempts"],
        SIGN_IN_LOCKOUT_ATTEMPTS_MIN,
        SIGN_IN_LOCKOUT_ATTEMPTS_MAX,
    ):
        raise InvalidInputError(
            "The number of failed sign-ins before a lock must be a whole number from 3 to 100."
        )
    if "sign_in_lockout_minutes" in given and not _is_whole_in(
        given["sign_in_lockout_minutes"], SIGN_IN_LOCKOUT_MINUTES_MIN, SIGN_IN_LOCKOUT_MINUTES_MAX
    ):
        raise InvalidInputError(
            "The lock duration must be a whole number of minutes from 1 to 1440."
        )
    if any(
        not isinstance(given[name], bool)
        for name in ("user_session_expires", "require_password_change")
        if name in given
    ):
        raise InvalidInputError("Each user sign-in option must be On or Off.")
    before = _sign_in_snapshot(policy)
    after = {**before, **given}
    if after == before:
        return policy
    for name, value in given.items():
        setattr(policy, name, value)
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=SIGN_IN_SECTION,
        before_data=before,
        after_data=_sign_in_snapshot(policy),
        actor_user_id=actor_user_id,
    )
    commit(session, {})
    return policy


class BackupPolicy(NamedTuple):
    """The backup policy of Administration → Backups, as stored."""

    interval_hours: int
    keep_hourly: int
    keep_daily: int
    keep_weekly: int


def backup_policy(policy: ApplicationPolicy) -> BackupPolicy:
    return BackupPolicy(
        interval_hours=policy.backup_interval_hours,
        keep_hourly=policy.backup_keep_hourly,
        keep_daily=policy.backup_keep_daily,
        keep_weekly=policy.backup_keep_weekly,
    )


def read_backup_policy(session: Session) -> BackupPolicy:
    """The stored backup policy (unlocked; the host task's read)."""
    return backup_policy(get_policy(session))


def _backup_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {
        "backup_interval_hours": policy.backup_interval_hours,
        "backup_keep_hourly": policy.backup_keep_hourly,
        "backup_keep_daily": policy.backup_keep_daily,
        "backup_keep_weekly": policy.backup_keep_weekly,
    }


def update_backup_policy(
    session: Session,
    *,
    interval_hours: object,
    keep_hourly: object,
    keep_daily: object,
    keep_weekly: object,
    actor_user_id: int | None,
) -> ApplicationPolicy:
    """Replace the backup policy (all four fields); a no-op writes and audits nothing.

    Saving it takes, removes or schedules no backup: the host's hourly
    backup task reads it at its next run.
    """
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    if not (_is_whole_in(interval_hours, 1, 24) and interval_hours in BACKUP_INTERVALS):
        raise InvalidInputError("Back up every 1, 2, 3, 4, 6, 8, 12 or 24 hours.")
    if not _is_whole_in(keep_hourly, BACKUP_KEEP_HOURLY_MIN, BACKUP_KEEP_HOURLY_MAX):
        raise InvalidInputError("Hourly backups kept must be a whole number from 0 to 720.")
    if not _is_whole_in(keep_daily, BACKUP_KEEP_DAILY_MIN, BACKUP_KEEP_DAILY_MAX):
        raise InvalidInputError("Daily backups kept must be a whole number from 1 to 3650.")
    if not _is_whole_in(keep_weekly, BACKUP_KEEP_WEEKLY_MIN, BACKUP_KEEP_WEEKLY_MAX):
        raise InvalidInputError("Weekly backups kept must be a whole number from 0 to 520.")
    before = _backup_snapshot(policy)
    after = {
        "backup_interval_hours": interval_hours,
        "backup_keep_hourly": keep_hourly,
        "backup_keep_daily": keep_daily,
        "backup_keep_weekly": keep_weekly,
    }
    if after == before:
        return policy
    policy.backup_interval_hours = interval_hours
    policy.backup_keep_hourly = keep_hourly
    policy.backup_keep_daily = keep_daily
    policy.backup_keep_weekly = keep_weekly
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=BACKUPS_SECTION,
        before_data=before,
        after_data=_backup_snapshot(policy),
        actor_user_id=actor_user_id,
    )
    commit(session, {})
    return policy


# The outgoing mail server (Phase 17, Administration → Settings).
_MAIL_HOST: Final = re.compile(r"[a-z0-9.:-]+")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_MAIL_HOST_MESSAGE: Final = (
    "Enter the mail server as a host name or IP address, such as smtp.example.com, without"
    " spaces or a port."
)
_MAIL_PORT_MESSAGE: Final = "The port must be a whole number from 1 to 65535."
_MAIL_TIMEOUT_MESSAGE: Final = (
    "The mail server timeout must be a whole number of seconds from"
    f" {MAIL_TIMEOUT_SECONDS_RANGE[0]} to {MAIL_TIMEOUT_SECONDS_RANGE[1]}."
)
_MAIL_SECURITY_MESSAGE: Final = "Choose STARTTLS, SSL/TLS or None as the connection security."
_MAIL_USERNAME_MESSAGE: Final = (
    "The user name must be at most 254 characters, without line breaks or other control characters."
)
_MAIL_PASSWORD_MESSAGE: Final = (
    "The password must be 1 to 1024 characters, without line breaks or other control characters."
)
_MAIL_SENDER_MISSING: Final = "Enter the sender address PartFlow sends its messages from."
_MAIL_FIELDS_WITHOUT_HOST: Final = (
    "Without a mail server, leave the sender address and the user name empty."
)
_MAIL_PASSWORD_WITHOUT_USERNAME: Final = "A password needs a user name."
_MAIL_SIGN_IN_NEEDS_TLS: Final = (
    "Signing in to the mail server needs STARTTLS or SSL/TLS, so the password never crosses"
    " the network unencrypted."
)


class MailServerSettings(NamedTuple):
    """The outgoing mail server as answered: the password only as whether one is stored."""

    host: str | None
    port: int
    security: MailSecurity
    username: str | None
    password_set: bool
    sender: str | None
    timeout_seconds: int


# The one row a mail server password may have (CHECK id = 1).
_CREDENTIAL_ID: Final = 1


def mail_server_settings(session: Session, policy: ApplicationPolicy) -> MailServerSettings:
    password_set = session.scalar(
        select(func.count())
        .select_from(MailServerCredential)
        .where(MailServerCredential.id == _CREDENTIAL_ID)
    )
    return MailServerSettings(
        host=policy.mail_host,
        port=policy.mail_port,
        security=MailSecurity(policy.mail_security),
        username=policy.mail_username,
        password_set=bool(password_set),
        sender=policy.mail_sender,
        timeout_seconds=policy.mail_timeout_seconds,
    )


def read_mail_server(session: Session) -> SmtpServer | None:
    """The stored mail server for sending, or None when none is set up (unlocked)."""
    host, port, security, username, password, sender, timeout = session.execute(
        select(
            ApplicationPolicy.mail_host,
            ApplicationPolicy.mail_port,
            ApplicationPolicy.mail_security,
            ApplicationPolicy.mail_username,
            MailServerCredential.password,
            ApplicationPolicy.mail_sender,
            ApplicationPolicy.mail_timeout_seconds,
        )
        .select_from(ApplicationPolicy)
        .outerjoin(MailServerCredential, MailServerCredential.id == _CREDENTIAL_ID)
        .where(ApplicationPolicy.id == _POLICY_ID)
    ).one()
    if host is None or sender is None:
        return None
    return SmtpServer(
        host=host,
        port=port,
        security=MailSecurity(security),
        username=username,
        password=password,
        sender=sender,
        timeout_seconds=timeout,
    )


def _mail_host(value: object) -> str | None:
    """A lowercase host name or IP address; None or blank = no mail server."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError(_MAIL_HOST_MESSAGE)
    stripped = value.strip()
    if not stripped:
        return None
    # ASCII before lowering, as the login-name rule does.
    if not stripped.isascii():
        raise InvalidInputError(_MAIL_HOST_MESSAGE)
    host = stripped.lower()
    if len(host) > MAIL_HOST_MAX_LENGTH or _MAIL_HOST.fullmatch(host) is None:
        raise InvalidInputError(_MAIL_HOST_MESSAGE)
    if ":" in host:
        # Only an IPv6 address holds a colon: a typed "host:port" is refused.
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise InvalidInputError(_MAIL_HOST_MESSAGE) from exc
    return host


def _mail_username(value: object) -> str | None:
    """The trimmed user name of the sign-in to the server; None or blank = no sign-in."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError(_MAIL_USERNAME_MESSAGE)
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > MAIL_USERNAME_MAX_LENGTH or _CONTROL.search(stripped):
        raise InvalidInputError(_MAIL_USERNAME_MESSAGE)
    return stripped


def _mail_password(value: object) -> str:
    """A new password exactly as typed (never trimmed)."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAIL_PASSWORD_MAX_LENGTH
        or _CONTROL.search(value)
    ):
        raise InvalidInputError(_MAIL_PASSWORD_MESSAGE)
    return value


def _mail_server_snapshot(policy: ApplicationPolicy, password_set: bool) -> dict[str, Any]:
    """The audited section — whether a password is stored, never the password."""
    return {
        "mail_host": policy.mail_host,
        "mail_port": policy.mail_port,
        "mail_security": policy.mail_security,
        "mail_username": policy.mail_username,
        "mail_password_set": password_set,
        "mail_sender": policy.mail_sender,
        "mail_timeout_seconds": policy.mail_timeout_seconds,
    }


def update_mail_server_policy(
    session: Session,
    *,
    host: object,
    port: object,
    security: object,
    username: object,
    sender: object,
    timeout_seconds: object,
    password: object = UNSET,
    actor_user_id: int,
) -> ApplicationPolicy:
    """Replace the outgoing mail server; a no-op writes and audits nothing.

    A full replace like Backups, except the password: left out, the
    stored one is kept (and dropped when no user name remains); ``None``
    clears it; a string replaces it. A blank host removes the server,
    which then keeps no sender, user name or password; the port, the
    security and the timeout are kept either way. Saving sends
    nothing. The audit row records whether a password is stored and, in
    its metadata, whether it changed — never the password.

    Lock order: the policy row, then the password row. Every writer of the
    password takes the policy row first, so a password row that does not
    exist yet cannot be inserted twice.
    """
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    credential = session.get(
        MailServerCredential, _CREDENTIAL_ID, with_for_update=True, populate_existing=True
    )
    stored_password = None if credential is None else credential.password
    clean_host = _mail_host(host)
    if not _is_whole_in(port, MAIL_PORT_MIN, MAIL_PORT_MAX):
        raise InvalidInputError(_MAIL_PORT_MESSAGE)
    if not _is_whole_in(timeout_seconds, *MAIL_TIMEOUT_SECONDS_RANGE):
        raise InvalidInputError(_MAIL_TIMEOUT_MESSAGE)
    if not isinstance(security, str) or security not in MailSecurity.__members__:
        raise InvalidInputError(_MAIL_SECURITY_MESSAGE)
    clean_security = MailSecurity(security)
    clean_username = _mail_username(username)
    clean_sender = users.canonical_email(sender)
    if clean_host is None and (clean_sender is not None or clean_username is not None):
        raise InvalidInputError(_MAIL_FIELDS_WITHOUT_HOST)
    if clean_host is not None and clean_sender is None:
        raise InvalidInputError(_MAIL_SENDER_MISSING)
    if isinstance(password, UnsetType):
        new_password = stored_password if clean_username is not None else None
    elif password is None:
        new_password = None
    else:
        new_password = _mail_password(password)
        if clean_username is None:
            raise InvalidInputError(_MAIL_PASSWORD_WITHOUT_USERNAME)
    if clean_username is not None and clean_security is MailSecurity.NONE:
        raise InvalidInputError(_MAIL_SIGN_IN_NEEDS_TLS)
    before = _mail_server_snapshot(policy, stored_password is not None)
    password_changed = new_password != stored_password
    after = {
        "mail_host": clean_host,
        "mail_port": port,
        "mail_security": clean_security.value,
        "mail_username": clean_username,
        "mail_password_set": new_password is not None,
        "mail_sender": clean_sender,
        "mail_timeout_seconds": timeout_seconds,
    }
    if after == before and not password_changed:
        return policy
    policy.mail_host = clean_host
    policy.mail_port = port
    policy.mail_security = clean_security.value
    policy.mail_username = clean_username
    policy.mail_sender = clean_sender
    policy.mail_timeout_seconds = timeout_seconds
    policy.updated_at = func.now()
    if new_password is None:
        if credential is not None:
            session.delete(credential)
    elif credential is None:
        session.add(MailServerCredential(id=_CREDENTIAL_ID, password=new_password))
    elif password_changed:
        credential.password = new_password
        credential.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=MAIL_SERVER_SECTION,
        before_data=before,
        after_data=_mail_server_snapshot(policy, new_password is not None),
        actor_user_id=actor_user_id,
        metadata={"mail_password_changed": password_changed},
    )
    commit(session, {})
    return policy


class TwoStepPolicy(NamedTuple):
    """The two-step sign-in policy of Administration → Settings, as stored."""

    window_minutes: int
    max_codes: int
    lockout_codes: int
    email_codes: int
    email_interval_seconds: int
    email_code_digits: int
    recovery_codes: int
    recovery_code_length: int
    setup_minutes: int
    clock_drift_steps: int

    @property
    def window(self) -> datetime.timedelta:
        """How long a second step stays open from the password step."""
        return datetime.timedelta(minutes=self.window_minutes)

    @property
    def email_interval(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=self.email_interval_seconds)

    @property
    def setup_time(self) -> datetime.timedelta:
        """How long a new authenticator app waits for its first code."""
        return datetime.timedelta(minutes=self.setup_minutes)


# What each field must be, completed with its range below.
_TWO_STEP_RULES: Final = {
    "two_step_window_minutes": "The time to finish the second step must be a whole number of"
    " minutes",
    "two_step_max_codes": "The wrong codes one sign-in may take must be a whole number",
    "two_step_lockout_codes": "The wrong codes before a lock must be a whole number",
    "two_step_email_codes": "The codes one sign-in may email must be a whole number",
    "two_step_email_interval_seconds": "The wait between two emailed codes must be a whole number"
    " of seconds",
    "two_step_email_code_digits": "The digits of an emailed code must be a whole number",
    "two_step_recovery_codes": "The number of recovery codes must be a whole number",
    "two_step_recovery_code_length": "The characters of a recovery code must be a whole number",
    "two_step_setup_minutes": "The time to finish setting up an authenticator app must be a whole"
    " number of minutes",
    "two_step_clock_drift_steps": "The clock drift allowed must be a whole number of 30-second"
    " steps",
}
_TWO_STEP_MESSAGES: Final = {
    column: f"{_TWO_STEP_RULES[column]} from {low} to {high}."
    for column, (low, high) in TWO_STEP_POLICY_RANGES.items()
}


def read_two_step_policy(session: Session) -> TwoStepPolicy:
    """The stored two-step policy (unlocked; a column select, no policy instance)."""
    row = session.execute(
        select(
            ApplicationPolicy.two_step_window_minutes,
            ApplicationPolicy.two_step_max_codes,
            ApplicationPolicy.two_step_lockout_codes,
            ApplicationPolicy.two_step_email_codes,
            ApplicationPolicy.two_step_email_interval_seconds,
            ApplicationPolicy.two_step_email_code_digits,
            ApplicationPolicy.two_step_recovery_codes,
            ApplicationPolicy.two_step_recovery_code_length,
            ApplicationPolicy.two_step_setup_minutes,
            ApplicationPolicy.two_step_clock_drift_steps,
        ).where(ApplicationPolicy.id == _POLICY_ID)
    ).one()
    return TwoStepPolicy(*row)


def _two_step_snapshot(policy: ApplicationPolicy) -> dict[str, Any]:
    return {column: getattr(policy, column) for column in TWO_STEP_POLICY_RANGES}


def update_two_step_policy(
    session: Session, *, changes: dict[str, object], actor_user_id: int
) -> ApplicationPolicy:
    """Merge the given two-step settings; a no-op writes and audits nothing.

    The User sign-in protocol: a field not given keeps its stored value;
    each given one is a whole number in its range. Every sign-in step reads
    the policy, so a change applies from the next step — an open second
    step included.
    """
    policy = session.get(
        ApplicationPolicy, _POLICY_ID, with_for_update=_EDIT_LOCK, populate_existing=True
    )
    if policy is None:  # pragma: no cover - seeded by its migration
        raise NotFoundError("The application policy is not configured.")
    if not changes:
        raise InvalidInputError("Change at least one two-step sign-in setting.")
    for column, value in changes.items():
        if not _is_whole_in(value, *TWO_STEP_POLICY_RANGES[column]):
            raise InvalidInputError(_TWO_STEP_MESSAGES[column])
    before = _two_step_snapshot(policy)
    if {**before, **changes} == before:
        return policy
    for column, value in changes.items():
        setattr(policy, column, value)
    policy.updated_at = func.now()
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.APPLICATION_POLICY,
        entity_id=TWO_STEP_SECTION,
        before_data=before,
        after_data=_two_step_snapshot(policy),
        actor_user_id=actor_user_id,
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
