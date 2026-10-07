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
- ``GET /policies/data-retention`` — the Movement-history retention
  period of Administration → History archival & purge (Phase 13 slice
  11), in whole months or ``null`` (no retention period) — stored only;
  archival and purge are Phase 16.
- ``PUT /policies/data-retention`` — exactly
  ``{"retention_period_months": int | null}``: the key is required and
  ``null`` clears the period (a missing key, a string, float or bool, or
  an extra field is 422); the 12-1200 range is the Application's 422;
  answers with the stored policy, also when nothing changed.
- ``GET /policies/sign-in`` — Administration → Settings → User sign-in
  (Phase 14 slice 1): whether user sign-ins expire and after how many
  days, the failed sign-ins before a lock, the lock duration and whether
  an administrator-set password must be replaced. Requires a signed-in
  User (no pending forced password change).
- ``PUT /policies/sign-in`` — a partial merge like Worker sessions
  (strict integers and booleans; an empty body, a ``null`` or an extra
  field is 422); requires ``CONFIGURE_SYSTEM_SETTINGS``; the audit row
  names the signed-in administrator.

Access (Phase 14 slice 2): every read needs a signed-in User except
``GET /policies/due-soon``, which stays public (the Production Board and
the Scan Station read it without signing in). Each write needs its
section's permission — Worker sessions
``MANAGE_WORKER_SESSION_POLICIES``, Correction permissions
``MANAGE_CORRECTION_PERMISSIONS``, Due Soon, data retention and user
sign-in ``CONFIGURE_SYSTEM_SETTINGS`` — and its audit row names the
signed-in User (``actor_user_id``).
"""

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.authorization import RequirePermission, SignedInDep
from app.api.dependencies import SessionDep
from app.application import policies
from app.application.authentication import Principal
from app.domain.enums import Permission
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


class RetentionPolicyResponse(BaseModel):
    retention_period_months: int | None
    # The singleton row's timestamp, shared by every policy section.
    updated_at: datetime.datetime


class RetentionPolicyPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Required key; null clears the retention period.
    retention_period_months: StrictInt | None


def _retention_response(policy: ApplicationPolicy) -> RetentionPolicyResponse:
    return RetentionPolicyResponse(
        retention_period_months=policy.retention_period_months, updated_at=policy.updated_at
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
def get_retention_policy(principal: SignedInDep, session: SessionDep) -> RetentionPolicyResponse:
    return _retention_response(policies.get_policy(session))


@router.put("/policies/data-retention")
def put_retention_policy(
    principal: SystemSettingsManagerDep, body: RetentionPolicyPutRequest, session: SessionDep
) -> RetentionPolicyResponse:
    policy = policies.update_retention_policy(
        session,
        retention_period_months=body.retention_period_months,
        actor_user_id=principal.user_id,
    )
    return _retention_response(policy)


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
