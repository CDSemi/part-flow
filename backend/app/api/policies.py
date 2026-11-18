"""Policy endpoints (Phase 13 — Administration → Policies).

HTTP surface of the global ``application_policy`` singleton. Routes
stay thin: request schemas validate shape only (``extra="forbid"``,
strict integers and booleans — a string or float, a bool for the
timeout or a number for an option is refused by the schema), the
Application layer (`app.application.policies`) owns the range rule, the
refusal of an explicit null or an empty change, the audit protocol and
the transaction, and the central
handlers in ``app.api.errors`` translate typed failures.

- ``GET /policies/worker-sessions`` — the default sliding inactivity
  timeout of scanned Worker Sessions, in whole minutes, and (Phase 13
  slice 5) the three badge-confirmation options of DONE, QUEUE return
  and Undo.
- ``PUT /policies/worker-sessions`` — a partial merge: each field is
  optional and a field left out keeps its stored value (1-720 minutes,
  strict booleans; an empty body, a ``null`` or an extra field is 422);
  answers with the full stored policy, also when nothing changed. The
  per-Area overrides are Area fields (``/api/areas``).
- ``GET /policies/correction-permissions`` — Administration → Correction
  permissions (Phase 13 slice 6): the Undo reason policy; role-based
  correction permissions are configured through ``/api/roles`` (slice
  12; changing who holds them needs ``MANAGE_CORRECTION_PERMISSIONS``
  since Phase 14 slice 2).
- ``PUT /policies/correction-permissions`` — exactly
  ``{"undo_reason_required": bool}`` (a missing field, a non-boolean, a
  ``null`` or an extra field is 422); answers with the stored policy,
  also when nothing changed.
- ``GET /policies/due-soon`` — the Due Soon warning window behind every
  derived due countdown — Administration → Settings (Phase 13 slice 9):
  the minimum and maximum warning days and the lead-time warning
  percentage.
- ``PUT /policies/due-soon`` — a full replace: all three fields are
  required strict integers (a missing field, a string, float, bool,
  ``null`` or an extra field is 422); the ranges and the minimum ≤
  maximum rule are the Application's 422; answers with the stored
  policy, also when nothing changed.
- ``GET /policies/data-retention`` — the settings of Administration →
  History archival & purge (Phase 13 slice 11; Phase 16 history archival,
  design B steps 1, 2 and 4): the Movement-history retention period in
  whole months or ``null`` (no retention period), the shortest allowed
  period, the days a run may wait for its copy confirmation or its
  approval, the Start minute of the host job's hourly work, the preview
  time limit, the purge batch limits (Movements and Part Numbers per batch),
  and the automatic runs: on or off, the measure and the limit of each
  measure, the approval step, the days between automatic runs and the
  standing authorization (who switched the approval step off, when, why,
  and whether it is still valid; ``null`` while the step is on).
- ``PUT /policies/data-retention`` — a partial merge like User sign-in
  (strict integers and booleans; ``retention_period_months: null`` clears
  the period; an empty body or an extra field is 422) plus ``reason``,
  required when the shortest allowed period changes or the approval step is
  switched off, and ``confirmation``, the typed confirmation that switching
  the approval step off needs (with the step already off it renews the
  standing authorization); the ranges are the Application's 422; requires
  ``RUN_HISTORY_ARCHIVAL_AND_PURGE``; answers with the stored section, also
  when nothing changed. Saving archives nothing: the runs are
  ``/api/history-archive`` (``app.api.history_archive``).
- ``GET /policies/sign-in`` — Administration → Settings → User sign-in
  (Phase 14 slice 1): whether user sign-ins expire and after how many
  days, the failed sign-ins before a lock, the lock duration and whether
  an administrator-set password must be replaced. Requires a signed-in
  User (no pending forced password change).
- ``PUT /policies/sign-in`` — a partial merge like Worker sessions
  (strict integers and booleans; an empty body, a ``null`` or an extra
  field is 422); requires ``CONFIGURE_SYSTEM_SETTINGS``; the audit row
  names the signed-in administrator.
- ``GET /policies/backups`` — Administration → Backups (Phase 16, owner
  decision of 2026-11-13): the interval of the scheduled backups in whole
  hours, the hourly, daily and weekly backups their rotation keeps, and
  (history archival step 2) the pre-purge backups kept. Requires a
  signed-in User.
- ``PUT /policies/backups`` — a full replace like Due Soon of the four
  fields of the scheduled backups, each a required strict integer (a
  missing field, a string, float, bool, ``null`` or an extra field is 422);
  ``backup_keep_pre_purge`` is optional (left out keeps the stored value);
  the allowed intervals and the ranges are the Application's 422; requires
  ``CONFIGURE_SYSTEM_SETTINGS``; answers with the stored policy, also when
  nothing changed. Saving takes or removes no backup: the host's hourly
  backup task reads it.
- ``GET /policies/mail-server`` — Administration → Settings → Outgoing mail
  server (Phase 17, owner decision of 2026-11-13): host (null = none),
  port, connection security, user name, whether a password is stored and
  the sender address. The password is never answered. Requires a
  signed-in User.
- ``PUT /policies/mail-server`` — a full replace of host, port, security,
  user name, sender and timeout (each key required; null clears host, user
  name and sender); ``mail_password`` left out keeps the stored password,
  ``null`` clears it, a string replaces it. Requires
  ``CONFIGURE_SYSTEM_SETTINGS``; answers with the stored section, also when
  nothing changed. Saving sends nothing.
- ``POST /policies/mail-server/test`` — sends one test message to ``to``
  through the stored server; ``CONFIGURE_SYSTEM_SETTINGS``. 409 when no
  server is set up or the server does not take the message (the detail says
  why); nothing is written either way.
- ``GET /policies/two-step`` — Administration → Settings → Two-step
  sign-in (Phase 17, owner decisions of 2026-11-17): every number of
  two-step sign-in. Requires a signed-in User.
- ``PUT /policies/two-step`` — a partial merge like User sign-in (strict
  integers; an empty body, a ``null`` or an extra field is 422; the ranges
  are the Application's 422); requires ``CONFIGURE_SYSTEM_SETTINGS``.
- ``GET /policies/password-rules`` — Administration → Security → Password
  rules (owner request of 2026-11-17): the minimum and maximum length and
  whether a digit, a special character, an uppercase and a lowercase letter
  are required. Requires a signed-in User.
- ``PUT /policies/password-rules`` — a partial merge like User sign-in
  (strict integers and booleans; an empty body, a ``null`` or an extra
  field is 422; the ranges and the minimum ≤ maximum rule are the
  Application's 422); requires ``CONFIGURE_SYSTEM_SETTINGS``.
- ``GET /policies/remember-me`` — Administration → Security → Remember me
  (owner request of 2026-11-17): whether the sign-in dialog offers it,
  whether a remembered sign-in expires and after how many days. Requires a
  signed-in User.
- ``PUT /policies/remember-me`` — a partial merge like User sign-in;
  requires ``CONFIGURE_SYSTEM_SETTINGS``.

The User sign-in, Two-step sign-in, Password rules and Remember me
sections are shown in Administration → Security; the others in their own
sections or in Administration → Settings.

Access (Phase 14 slice 2): every read needs a signed-in User except
``GET /policies/due-soon``, which stays public (the Production Board and
the Scan Station read it without signing in). Each write needs its
section's permission — Worker sessions
``MANAGE_WORKER_SESSION_POLICIES``, Correction permissions
``MANAGE_CORRECTION_PERMISSIONS``, History archival & purge
``RUN_HISTORY_ARCHIVAL_AND_PURGE``, and Due Soon, user sign-in, backups, the
outgoing mail server, two-step sign-in, the password rules and Remember me
``CONFIGURE_SYSTEM_SETTINGS`` — and its
audit row names the
signed-in User (``actor_user_id``).
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt
from sqlalchemy.orm import Session

from app.api.authorization import RequirePermission, SignedInDep
from app.api.dependencies import SessionDep
from app.api.session import Secret
from app.application import history_archive, outgoing_mail, policies
from app.application.authentication import Principal
from app.application.common import UNSET
from app.domain.enums import MailSecurity, Permission
from app.infrastructure.models import ApplicationPolicy

router = APIRouter(prefix="/api")

WorkerSessionPolicyManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.MANAGE_WORKER_SESSION_POLICIES))
]
CorrectionPermissionsManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.MANAGE_CORRECTION_PERMISSIONS))
]
SystemSettingsManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.CONFIGURE_SYSTEM_SETTINGS))
]
HistoryArchiveManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE))
]


class WorkerSessionPolicyResponse(BaseModel):
    worker_session_timeout_minutes: int
    badge_confirm_done: bool
    badge_confirm_queue: bool
    badge_confirm_undo: bool
    updated_at: datetime.datetime


class WorkerSessionPolicyPutRequest(BaseModel):
    """Any non-empty subset of the section; a field left out keeps its value.

    An explicit ``null`` reaches the service (exclude_unset) and is
    refused there.
    """

    model_config = ConfigDict(extra="forbid")

    worker_session_timeout_minutes: StrictInt | None = None
    badge_confirm_done: StrictBool | None = None
    badge_confirm_queue: StrictBool | None = None
    badge_confirm_undo: StrictBool | None = None


def _response(policy: ApplicationPolicy) -> WorkerSessionPolicyResponse:
    return WorkerSessionPolicyResponse(
        worker_session_timeout_minutes=policy.worker_session_timeout_minutes,
        badge_confirm_done=policy.badge_confirm_done,
        badge_confirm_queue=policy.badge_confirm_queue,
        badge_confirm_undo=policy.badge_confirm_undo,
        updated_at=policy.updated_at,
    )


class CorrectionPermissionsPolicyResponse(BaseModel):
    undo_reason_required: bool
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class CorrectionPermissionsPolicyPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    undo_reason_required: StrictBool


def _correction_permissions_response(
    policy: ApplicationPolicy,
) -> CorrectionPermissionsPolicyResponse:
    return CorrectionPermissionsPolicyResponse(
        undo_reason_required=policy.undo_reason_required, updated_at=policy.updated_at
    )


class DueSoonPolicyResponse(BaseModel):
    due_soon_min_days: int
    due_soon_lead_time_percent: int
    due_soon_max_days: int
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class DueSoonPolicyPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_soon_min_days: StrictInt
    due_soon_lead_time_percent: StrictInt
    due_soon_max_days: StrictInt


def _due_soon_response(policy: ApplicationPolicy) -> DueSoonPolicyResponse:
    return DueSoonPolicyResponse(
        due_soon_min_days=policy.due_soon_min_days,
        due_soon_lead_time_percent=policy.due_soon_lead_time_percent,
        due_soon_max_days=policy.due_soon_max_days,
        updated_at=policy.updated_at,
    )


class StandingAuthorizationUser(BaseModel):
    user_id: int
    display_name: str


class StandingAuthorizationResponse(BaseModel):
    """The audit row that switched the approval step of automatic runs off."""

    audit_event_id: int
    authorized_by: StandingAuthorizationUser | None
    authorized_at: datetime.datetime | None
    reason: str | None
    # Its User is still active and still holds Run history archival and purge,
    # and no run failed under it because its User could no longer run it.
    valid: bool
    # Why it is no longer valid (automatic runs wait until it is saved again).
    problem: str | None


class HistoryArchivePolicyResponse(BaseModel):
    retention_period_months: int | None
    shortest_retention_months: int
    archive_max_wait_days: int
    archive_start_minute: int
    archive_preview_seconds: int
    archive_purge_batch_movements: int
    archive_purge_batch_part_numbers: int
    # Automatic runs (design B step 4).
    archive_automatic_runs: bool
    archive_measure: str
    archive_limit_movement_rows: int
    archive_limit_history_months: int
    archive_limit_database_gib: int
    archive_approval_required: bool
    archive_interval_days: int
    standing_authorization: StandingAuthorizationResponse | None
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class HistoryArchivePolicyPutRequest(BaseModel):
    """Any non-empty subset of the section; a field left out keeps its value.

    ``retention_period_months: null`` clears the retention period; an
    explicit ``null`` for another setting reaches the service (exclude_unset)
    and is refused there. ``reason`` is required when the shortest allowed
    period changes or the approval step is switched off, and recorded with
    that change only; ``confirmation`` is the typed confirmation of switching
    the approval step off (checked, never stored).
    """

    model_config = ConfigDict(extra="forbid")

    retention_period_months: StrictInt | None = None
    shortest_retention_months: StrictInt | None = None
    archive_max_wait_days: StrictInt | None = None
    archive_start_minute: StrictInt | None = None
    archive_preview_seconds: StrictInt | None = None
    archive_purge_batch_movements: StrictInt | None = None
    archive_purge_batch_part_numbers: StrictInt | None = None
    archive_automatic_runs: StrictBool | None = None
    archive_measure: str | None = None
    archive_limit_movement_rows: StrictInt | None = None
    archive_limit_history_months: StrictInt | None = None
    archive_limit_database_gib: StrictInt | None = None
    archive_approval_required: StrictBool | None = None
    archive_interval_days: StrictInt | None = None
    reason: str | None = None
    confirmation: str | None = None


def _history_archive_response(
    session: Session, policy: ApplicationPolicy
) -> HistoryArchivePolicyResponse:
    authorization = history_archive.standing_authorization(session, policy.archive_authorization_id)
    return HistoryArchivePolicyResponse(
        retention_period_months=policy.retention_period_months,
        shortest_retention_months=policy.shortest_retention_months,
        archive_max_wait_days=policy.archive_max_wait_days,
        archive_start_minute=policy.archive_start_minute,
        archive_preview_seconds=policy.archive_preview_seconds,
        archive_purge_batch_movements=policy.archive_purge_batch_movements,
        archive_purge_batch_part_numbers=policy.archive_purge_batch_part_numbers,
        archive_automatic_runs=policy.archive_automatic_runs,
        archive_measure=policy.archive_measure,
        archive_limit_movement_rows=policy.archive_limit_movement_rows,
        archive_limit_history_months=policy.archive_limit_history_months,
        archive_limit_database_gib=policy.archive_limit_database_gib,
        archive_approval_required=policy.archive_approval_required,
        archive_interval_days=policy.archive_interval_days,
        standing_authorization=(
            None
            if authorization is None
            else StandingAuthorizationResponse.model_validate(authorization.document())
        ),
        updated_at=policy.updated_at,
    )


class BackupPolicyResponse(BaseModel):
    backup_interval_hours: int
    backup_keep_hourly: int
    backup_keep_daily: int
    backup_keep_weekly: int
    # The newest verified pre-purge backups kept (history archival step 2).
    backup_keep_pre_purge: int
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class BackupPolicyPutRequest(BaseModel):
    """The four fields of the scheduled backups (each required) and, optionally,
    the pre-purge backups kept (left out: the stored value stays; an explicit
    ``null`` reaches the service and is refused there)."""

    model_config = ConfigDict(extra="forbid")

    backup_interval_hours: StrictInt
    backup_keep_hourly: StrictInt
    backup_keep_daily: StrictInt
    backup_keep_weekly: StrictInt
    backup_keep_pre_purge: StrictInt | None = None


def _backup_response(policy: ApplicationPolicy) -> BackupPolicyResponse:
    return BackupPolicyResponse(
        backup_interval_hours=policy.backup_interval_hours,
        backup_keep_hourly=policy.backup_keep_hourly,
        backup_keep_daily=policy.backup_keep_daily,
        backup_keep_weekly=policy.backup_keep_weekly,
        backup_keep_pre_purge=policy.backup_keep_pre_purge,
        updated_at=policy.updated_at,
    )


@router.get("/policies/worker-sessions")
def get_worker_session_policy(
    principal: SignedInDep, session: SessionDep
) -> WorkerSessionPolicyResponse:
    return _response(policies.get_policy(session))


@router.put("/policies/worker-sessions")
def put_worker_session_policy(
    principal: WorkerSessionPolicyManagerDep,
    body: WorkerSessionPolicyPutRequest,
    session: SessionDep,
) -> WorkerSessionPolicyResponse:
    fields = body.model_dump(exclude_unset=True)
    if "worker_session_timeout_minutes" in fields:
        fields["timeout_minutes"] = fields.pop("worker_session_timeout_minutes")
    policy = policies.update_worker_session_policy(
        session, actor_user_id=principal.user_id, **fields
    )
    return _response(policy)


@router.get("/policies/correction-permissions")
def get_correction_permissions_policy(
    principal: SignedInDep, session: SessionDep
) -> CorrectionPermissionsPolicyResponse:
    return _correction_permissions_response(policies.get_policy(session))


@router.put("/policies/correction-permissions")
def put_correction_permissions_policy(
    principal: CorrectionPermissionsManagerDep,
    body: CorrectionPermissionsPolicyPutRequest,
    session: SessionDep,
) -> CorrectionPermissionsPolicyResponse:
    policy = policies.update_correction_permissions_policy(
        session, undo_reason_required=body.undo_reason_required, actor_user_id=principal.user_id
    )
    return _correction_permissions_response(policy)


@router.get("/policies/due-soon")
def get_due_soon_policy(session: SessionDep) -> DueSoonPolicyResponse:
    return _due_soon_response(policies.get_policy(session))


@router.put("/policies/due-soon")
def put_due_soon_policy(
    principal: SystemSettingsManagerDep, body: DueSoonPolicyPutRequest, session: SessionDep
) -> DueSoonPolicyResponse:
    policy = policies.update_due_soon_policy(
        session,
        min_days=body.due_soon_min_days,
        lead_time_percent=body.due_soon_lead_time_percent,
        max_days=body.due_soon_max_days,
        actor_user_id=principal.user_id,
    )
    return _due_soon_response(policy)


@router.get("/policies/data-retention")
def get_history_archive_policy(
    principal: SignedInDep, session: SessionDep
) -> HistoryArchivePolicyResponse:
    return _history_archive_response(session, policies.get_policy(session))


@router.put("/policies/data-retention")
def put_history_archive_policy(
    principal: HistoryArchiveManagerDep, body: HistoryArchivePolicyPutRequest, session: SessionDep
) -> HistoryArchivePolicyResponse:
    changes = body.model_dump(exclude_unset=True)
    reason: object = changes.pop("reason") if "reason" in changes else UNSET
    confirmation: object = changes.pop("confirmation") if "confirmation" in changes else UNSET
    policy = policies.update_history_archive_policy(
        session,
        changes=changes,
        reason=reason,
        confirmation=confirmation,
        actor_user_id=principal.user_id,
    )
    return _history_archive_response(session, policy)


class SignInPolicyResponse(BaseModel):
    user_session_expires: bool
    user_session_days: int
    sign_in_lockout_attempts: int
    sign_in_lockout_minutes: int
    require_password_change: bool
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class SignInPolicyPutRequest(BaseModel):
    """Any non-empty subset of the section; a field left out keeps its value.

    An explicit ``null`` reaches the service (exclude_unset) and is
    refused there.
    """

    model_config = ConfigDict(extra="forbid")

    user_session_expires: StrictBool | None = None
    user_session_days: StrictInt | None = None
    sign_in_lockout_attempts: StrictInt | None = None
    sign_in_lockout_minutes: StrictInt | None = None
    require_password_change: StrictBool | None = None


def _sign_in_response(policy: ApplicationPolicy) -> SignInPolicyResponse:
    return SignInPolicyResponse(
        user_session_expires=policy.user_session_expires,
        user_session_days=policy.user_session_days,
        sign_in_lockout_attempts=policy.sign_in_lockout_attempts,
        sign_in_lockout_minutes=policy.sign_in_lockout_minutes,
        require_password_change=policy.require_password_change,
        updated_at=policy.updated_at,
    )


@router.get("/policies/sign-in")
def get_sign_in_policy(principal: SignedInDep, session: SessionDep) -> SignInPolicyResponse:
    return _sign_in_response(policies.get_policy(session))


@router.put("/policies/sign-in")
def put_sign_in_policy(
    principal: SystemSettingsManagerDep, body: SignInPolicyPutRequest, session: SessionDep
) -> SignInPolicyResponse:
    policy = policies.update_sign_in_policy(
        session, actor_user_id=principal.user_id, **body.model_dump(exclude_unset=True)
    )
    return _sign_in_response(policy)


@router.get("/policies/backups")
def get_backup_policy(principal: SignedInDep, session: SessionDep) -> BackupPolicyResponse:
    return _backup_response(policies.get_policy(session))


@router.put("/policies/backups")
def put_backup_policy(
    principal: SystemSettingsManagerDep, body: BackupPolicyPutRequest, session: SessionDep
) -> BackupPolicyResponse:
    given = body.model_dump(exclude_unset=True)
    policy = policies.update_backup_policy(
        session,
        interval_hours=body.backup_interval_hours,
        keep_hourly=body.backup_keep_hourly,
        keep_daily=body.backup_keep_daily,
        keep_weekly=body.backup_keep_weekly,
        keep_pre_purge=given.get("backup_keep_pre_purge", UNSET),
        actor_user_id=principal.user_id,
    )
    return _backup_response(policy)


class MailServerPolicyResponse(BaseModel):
    mail_host: str | None
    mail_port: int
    mail_security: MailSecurity
    mail_username: str | None
    # Whether a password is stored; the password itself is never answered.
    mail_password_set: bool
    mail_sender: str | None
    mail_timeout_seconds: int
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class MailServerPolicyPutRequest(BaseModel):
    """The whole section; ``mail_password`` left out keeps the stored one, null clears it."""

    model_config = ConfigDict(extra="forbid")

    mail_host: str | None
    mail_port: StrictInt
    mail_security: str
    mail_username: str | None
    mail_sender: str | None
    mail_timeout_seconds: StrictInt
    mail_password: Secret | None = None


class MailTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str


class MailTestResponse(BaseModel):
    # The canonical address the test message went to.
    sent_to: str


def _mail_server_response(session: Session, policy: ApplicationPolicy) -> MailServerPolicyResponse:
    settings = policies.mail_server_settings(session, policy)
    return MailServerPolicyResponse(
        mail_host=settings.host,
        mail_port=settings.port,
        mail_security=settings.security,
        mail_username=settings.username,
        mail_password_set=settings.password_set,
        mail_sender=settings.sender,
        mail_timeout_seconds=settings.timeout_seconds,
        updated_at=policy.updated_at,
    )


@router.get("/policies/mail-server")
def get_mail_server_policy(principal: SignedInDep, session: SessionDep) -> MailServerPolicyResponse:
    return _mail_server_response(session, policies.get_policy(session))


@router.put("/policies/mail-server")
def put_mail_server_policy(
    principal: SystemSettingsManagerDep, body: MailServerPolicyPutRequest, session: SessionDep
) -> MailServerPolicyResponse:
    password: object = UNSET
    if "mail_password" in body.model_fields_set:
        password = None if body.mail_password is None else body.mail_password.get_secret_value()
    policy = policies.update_mail_server_policy(
        session,
        host=body.mail_host,
        port=body.mail_port,
        security=body.mail_security,
        username=body.mail_username,
        sender=body.mail_sender,
        timeout_seconds=body.mail_timeout_seconds,
        password=password,
        actor_user_id=principal.user_id,
    )
    return _mail_server_response(session, policy)


@router.post("/policies/mail-server/test")
def send_test_mail(
    principal: SystemSettingsManagerDep, body: MailTestRequest, session: SessionDep
) -> MailTestResponse:
    return MailTestResponse(
        sent_to=outgoing_mail.send_test_message(session, to=body.to, actor=principal)
    )


class TwoStepPolicyResponse(BaseModel):
    two_step_window_minutes: int
    two_step_max_codes: int
    two_step_lockout_codes: int
    two_step_email_codes: int
    two_step_email_interval_seconds: int
    two_step_email_code_digits: int
    two_step_recovery_codes: int
    two_step_recovery_code_length: int
    two_step_setup_minutes: int
    two_step_clock_drift_steps: int
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class TwoStepPolicyPutRequest(BaseModel):
    """Any non-empty subset of the section; a field left out keeps its value.

    An explicit ``null`` reaches the service (exclude_unset) and is refused
    there.
    """

    model_config = ConfigDict(extra="forbid")

    two_step_window_minutes: StrictInt | None = None
    two_step_max_codes: StrictInt | None = None
    two_step_lockout_codes: StrictInt | None = None
    two_step_email_codes: StrictInt | None = None
    two_step_email_interval_seconds: StrictInt | None = None
    two_step_email_code_digits: StrictInt | None = None
    two_step_recovery_codes: StrictInt | None = None
    two_step_recovery_code_length: StrictInt | None = None
    two_step_setup_minutes: StrictInt | None = None
    two_step_clock_drift_steps: StrictInt | None = None


def _two_step_response(policy: ApplicationPolicy) -> TwoStepPolicyResponse:
    return TwoStepPolicyResponse.model_validate(
        {
            **{field: getattr(policy, field) for field in TwoStepPolicyPutRequest.model_fields},
            "updated_at": policy.updated_at,
        }
    )


@router.get("/policies/two-step")
def get_two_step_policy(principal: SignedInDep, session: SessionDep) -> TwoStepPolicyResponse:
    return _two_step_response(policies.get_policy(session))


@router.put("/policies/two-step")
def put_two_step_policy(
    principal: SystemSettingsManagerDep, body: TwoStepPolicyPutRequest, session: SessionDep
) -> TwoStepPolicyResponse:
    policy = policies.update_two_step_policy(
        session, changes=body.model_dump(exclude_unset=True), actor_user_id=principal.user_id
    )
    return _two_step_response(policy)


class PasswordRulesPolicyResponse(BaseModel):
    password_min_length: int
    password_max_length: int
    password_require_digit: bool
    password_require_special: bool
    password_require_uppercase: bool
    password_require_lowercase: bool
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class PasswordRulesPolicyPutRequest(BaseModel):
    """Any non-empty subset of the section; a field left out keeps its value.

    An explicit ``null`` reaches the service (exclude_unset) and is refused
    there.
    """

    model_config = ConfigDict(extra="forbid")

    password_min_length: StrictInt | None = None
    password_max_length: StrictInt | None = None
    password_require_digit: StrictBool | None = None
    password_require_special: StrictBool | None = None
    password_require_uppercase: StrictBool | None = None
    password_require_lowercase: StrictBool | None = None


def _password_rules_response(policy: ApplicationPolicy) -> PasswordRulesPolicyResponse:
    return PasswordRulesPolicyResponse.model_validate(
        {
            **{
                field: getattr(policy, field)
                for field in PasswordRulesPolicyPutRequest.model_fields
            },
            "updated_at": policy.updated_at,
        }
    )


@router.get("/policies/password-rules")
def get_password_rules_policy(
    principal: SignedInDep, session: SessionDep
) -> PasswordRulesPolicyResponse:
    return _password_rules_response(policies.get_policy(session))


@router.put("/policies/password-rules")
def put_password_rules_policy(
    principal: SystemSettingsManagerDep, body: PasswordRulesPolicyPutRequest, session: SessionDep
) -> PasswordRulesPolicyResponse:
    policy = policies.update_password_rules(
        session, changes=body.model_dump(exclude_unset=True), actor_user_id=principal.user_id
    )
    return _password_rules_response(policy)


class RememberMePolicyResponse(BaseModel):
    remember_me_offered: bool
    remember_me_expires: bool
    remember_me_days: int
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class RememberMePolicyPutRequest(BaseModel):
    """Any non-empty subset of the section; a field left out keeps its value.

    An explicit ``null`` reaches the service (exclude_unset) and is refused
    there.
    """

    model_config = ConfigDict(extra="forbid")

    remember_me_offered: StrictBool | None = None
    remember_me_expires: StrictBool | None = None
    remember_me_days: StrictInt | None = None


def _remember_me_response(policy: ApplicationPolicy) -> RememberMePolicyResponse:
    return RememberMePolicyResponse(
        remember_me_offered=policy.remember_me_offered,
        remember_me_expires=policy.remember_me_expires,
        remember_me_days=policy.remember_me_days,
        updated_at=policy.updated_at,
    )


@router.get("/policies/remember-me")
def get_remember_me_policy(principal: SignedInDep, session: SessionDep) -> RememberMePolicyResponse:
    return _remember_me_response(policies.get_policy(session))


@router.put("/policies/remember-me")
def put_remember_me_policy(
    principal: SystemSettingsManagerDep, body: RememberMePolicyPutRequest, session: SessionDep
) -> RememberMePolicyResponse:
    policy = policies.update_remember_me(
        session, changes=body.model_dump(exclude_unset=True), actor_user_id=principal.user_id
    )
    return _remember_me_response(policy)
