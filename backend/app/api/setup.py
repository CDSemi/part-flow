"""First-run setup endpoints (Phase 14 slice 1; owner decisions OD-P4/OD-P5).

HTTP surface of ``app.application.first_run``:

- ``GET /setup`` — whether setup is open (no administrator exists) and,
  while it is, the roles the first administrator may hold. Observing an
  open setup announces the process's setup token in the server log
  (once per token).
- ``POST /setup/administrator`` — create the first administrator with
  the one-time setup token from the server log, then sign it in (201 and
  the session cookie). A wrong token is 403 while setup is open; once an
  administrator exists every attempt is 409, whatever the token.

The token is never returned, stored or logged by these routes. Every
response carries ``Cache-Control: no-store`` and the creation always
requires the CSRF header (``app.api.csrf``).
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from app.api.dependencies import SessionDep, SetupGateDep
from app.api.session import Secret, SessionStateResponse, grant_session, session_state
from app.application import first_run

router = APIRouter(prefix="/api")


class SetupRoleResponse(BaseModel):
    id: int
    name: str


class SetupStatusResponse(BaseModel):
    open: bool
    # Empty when setup is closed.
    eligible_roles: list[SetupRoleResponse]


class FirstAdministratorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup_token: Secret
    login_name: str
    display_name: str
    role_id: StrictInt
    password: Secret


@router.get("/setup")
def get_setup_status(session: SessionDep, gate: SetupGateDep) -> SetupStatusResponse:
    status = first_run.setup_status(session, gate)
    return SetupStatusResponse(
        open=status.open,
        eligible_roles=[SetupRoleResponse(id=role.id, name=role.name) for role in status.roles],
    )


@router.post("/setup/administrator", status_code=201)
def create_first_administrator(
    body: FirstAdministratorRequest, response: Response, session: SessionDep, gate: SetupGateDep
) -> SessionStateResponse:
    grant = first_run.create_first_administrator(
        session,
        gate,
        setup_token=body.setup_token.get_secret_value(),
        login_name=body.login_name,
        display_name=body.display_name,
        role_id=body.role_id,
        password=body.password.get_secret_value(),
    )
    grant_session(response, grant)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))
