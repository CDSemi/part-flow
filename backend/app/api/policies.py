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
