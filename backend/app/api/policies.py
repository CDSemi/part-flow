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
  correction permissions are not configurable yet.
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
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from app.api.dependencies import SessionDep
from app.application import policies
from app.infrastructure.models import ApplicationPolicy

router = APIRouter(prefix="/api")


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
def get_worker_session_policy(session: SessionDep) -> WorkerSessionPolicyResponse:
    return _response(policies.get_policy(session))


@router.put("/policies/worker-sessions")
def put_worker_session_policy(
    body: WorkerSessionPolicyPutRequest, session: SessionDep
) -> WorkerSessionPolicyResponse:
    fields = body.model_dump(exclude_unset=True)
    if "worker_session_timeout_minutes" in fields:
        fields["timeout_minutes"] = fields.pop("worker_session_timeout_minutes")
    policy = policies.update_worker_session_policy(session, **fields)
    return _response(policy)


@router.get("/policies/correction-permissions")
def get_correction_permissions_policy(session: SessionDep) -> CorrectionPermissionsPolicyResponse:
    return _correction_permissions_response(policies.get_policy(session))


@router.put("/policies/correction-permissions")
def put_correction_permissions_policy(
    body: CorrectionPermissionsPolicyPutRequest, session: SessionDep
) -> CorrectionPermissionsPolicyResponse:
    policy = policies.update_correction_permissions_policy(
        session, undo_reason_required=body.undo_reason_required
    )
    return _correction_permissions_response(policy)


@router.get("/policies/due-soon")
def get_due_soon_policy(session: SessionDep) -> DueSoonPolicyResponse:
    return _due_soon_response(policies.get_policy(session))


@router.put("/policies/due-soon")
def put_due_soon_policy(
    body: DueSoonPolicyPutRequest, session: SessionDep
) -> DueSoonPolicyResponse:
    policy = policies.update_due_soon_policy(
        session,
        min_days=body.due_soon_min_days,
        lead_time_percent=body.due_soon_lead_time_percent,
        max_days=body.due_soon_max_days,
    )
    return _due_soon_response(policy)


@router.get("/policies/data-retention")
def get_retention_policy(session: SessionDep) -> RetentionPolicyResponse:
    return _retention_response(policies.get_policy(session))


@router.put("/policies/data-retention")
def put_retention_policy(
    body: RetentionPolicyPutRequest, session: SessionDep
) -> RetentionPolicyResponse:
    policy = policies.update_retention_policy(
        session, retention_period_months=body.retention_period_months
    )
    return _retention_response(policy)
