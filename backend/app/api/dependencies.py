"""Shared FastAPI dependencies for the API layer."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    """Provide a request-scoped ORM session on the application engine.

    The Application layer owns the transaction boundary and commits
    explicitly; closing the session here discards anything left
    uncommitted, so a failed request never leaks partial writes.
    """
    engine: Engine = request.app.state.engine
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
