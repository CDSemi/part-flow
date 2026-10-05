"""Policy endpoints (Phase 13 slice 4 — Administration → Worker sessions).

HTTP surface of the global ``application_policy`` singleton. Routes
stay thin: request schemas validate shape only (``extra="forbid"``,
strict integers — a string, float, bool or null is refused by the
schema), the Application layer (`app.application.policies`) owns the
range rule, the audit protocol and the transaction, and the central
handlers in ``app.api.errors`` translate typed failures.

- ``GET /policies/worker-sessions`` — the default sliding inactivity
  timeout of scanned Worker Sessions, in whole minutes.
- ``PUT /policies/worker-sessions`` — replace it (1-720 minutes, else
  422); answers with the stored policy, also when nothing changed. The
  per-Area overrides are Area fields (``/api/areas``).
"""

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.dependencies import SessionDep
from app.application import policies
from app.infrastructure.models import ApplicationPolicy

router = APIRouter(prefix="/api")


class WorkerSessionPolicyResponse(BaseModel):
    worker_session_timeout_minutes: int
    updated_at: datetime.datetime


class WorkerSessionPolicyPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_session_timeout_minutes: StrictInt


def _response(policy: ApplicationPolicy) -> WorkerSessionPolicyResponse:
    return WorkerSessionPolicyResponse(
        worker_session_timeout_minutes=policy.worker_session_timeout_minutes,
        updated_at=policy.updated_at,
    )


@router.get("/policies/worker-sessions")
def get_worker_session_policy(session: SessionDep) -> WorkerSessionPolicyResponse:
    return _response(policies.get_policy(session))


@router.put("/policies/worker-sessions")
def put_worker_session_policy(
    body: WorkerSessionPolicyPutRequest, session: SessionDep
) -> WorkerSessionPolicyResponse:
    policy = policies.update_worker_session_policy(
        session, timeout_minutes=body.worker_session_timeout_minutes
    )
    return _response(policy)
