"""The display references of who recorded a history row (Phase 14, Phase 17).

Shared by the Management responses that show who recorded an entry —
the Machine lifecycle history, the allocation context and Tracking's
allocation history and audit trail — so each wire shape is one: a User
is ``{id, display_name, avatar_updated_at}`` (``user_access.UserRef``),
and the Worker a Stockroom station identified for an allocation is
``{id, name}`` with the Worker's current name.
"""

import datetime

from pydantic import BaseModel

from app.application import user_access
from app.application.station_identity import WorkerRef


class UserRefResponse(BaseModel):
    id: int
    display_name: str
    avatar_updated_at: datetime.datetime | None


def user_ref_response(ref: user_access.UserRef | None) -> UserRefResponse | None:
    """The wire form of a display reference (``None`` stays ``None``)."""
    if ref is None:
        return None
    return UserRefResponse(
        id=ref.id, display_name=ref.display_name, avatar_updated_at=ref.avatar_updated_at
    )


class WorkerRefResponse(BaseModel):
    id: int
    name: str


def worker_ref_response(worker: WorkerRef | None) -> WorkerRefResponse | None:
    """The wire form of the Worker who recorded a station row (``None`` stays ``None``)."""
    if worker is None:
        return None
    return WorkerRefResponse(id=worker.id, name=worker.name)
