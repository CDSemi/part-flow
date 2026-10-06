"""Policy endpoints (Phase 13 slice 4 — Administration → Worker sessions).

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
