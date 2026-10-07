"""The display reference of the User who recorded a history row (Phase 14).

Shared by the Management responses that show who recorded an entry —
the Machine lifecycle history, the allocation context and Tracking's
allocation history — so the wire shape is one: ``{id, display_name,
avatar_updated_at}`` (``user_access.UserRef``).
"""

import datetime

from pydantic import BaseModel

from app.application import user_access


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
