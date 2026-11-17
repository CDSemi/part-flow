"""User sign-in endpoints (Phase 14 slice 1; owner decisions OD-P1–OD-P3).

HTTP surface of ``app.application.authentication``:

- ``GET /session`` — the signed-in User (with the role's effective
  permissions, whether a new password is required and when the sign-in
  expires; ``null`` when not signed in) and whether first-run setup is
  open. A resolved session's cookie is re-issued with its remaining
  lifetime under the current policy, so a lengthened expiry or "never"
  reaches the browser; an unusable cookie is cleared.
- ``POST /session`` — sign in with a login name and password; a new
  session token is always issued (a presented cookie is never adopted,
  and its session ends REPLACED). One generic 401 for every refusal. A
  User with two-step sign-in (Phase 17) is answered 202 with the second
  step instead — the ways to finish, the masked email address a code can
  go to and when it ends — and its token in the ``partflow_sign_in``
  cookie (path ``/api/session``), never a session.
- ``POST /session/second-step`` — finish the second step with
  ``{method, code}``; signs in like ``POST /session`` and clears the
  second-step cookie. 401 ``second_step_failed`` for a wrong code (the
  step stays open), 401 ``second_step_expired`` when it is over (the
  cookie is cleared), 409 ``account_locked``.
- ``POST /session/second-step/email-code`` — email a sign-in code for
  the open second step; 409 when no address or mail server is available,
  when the server refuses, or too soon after the previous code.
- ``POST /session/second-step/passkey-options`` and
  ``POST /session/second-step/passkey`` — finish the second step with a
  passkey: WebAuthn request options for the User's passkeys at this host,
  then the browser's answer ``{credential}``; signs in like
  ``…/second-step``.
- ``POST /session/passkey/options`` and ``POST /session/passkey`` — sign
  in with a passkey alone: WebAuthn request options (with the
  ``partflow_sign_in`` cookie of this request), then the browser's answer
  ``{credential}``; one generic 401 ``sign_in_failed`` for every refusal.
- ``GET /session/two-step`` and ``POST /session/two-step/totp``,
  ``…/totp/confirm``, ``…/totp/remove``, ``…/recovery-codes``,
  ``…/passkeys/options``, ``…/passkeys`` and ``…/passkeys/{id}/remove`` —
  the signed-in User's own two-step sign-in (``app.application.two_step``):
  setting up an authenticator app, confirming it with its first code,
  removing it, adding and removing passkeys and replacing the recovery
  codes. Every change but a confirmation takes ``current_password``. The
  status carries the User's own email address and passkeys.

Passkeys work only at an HTTPS address (or ``http://localhost``): the
request's ``Origin`` names the host they are made and used at; elsewhere
their routes answer 422.
- ``DELETE /session`` — sign out; 204 also without a session.
- ``PUT /session/password`` — change the signed-in User's own password
  (allowed while a change is required); every other sign-in of the User
  ends and the cookie rotates.
- ``PUT /session/email`` — change the signed-in User's own email address
  (owner decision of 2026-11-17) with ``{current_password, email}``;
  ``null`` or blank removes it. Audited on the User; the previous address
  receives a notice when a mail server is set up.
- ``PUT /session/theme-preference`` — save the signed-in User's own
  Dark/Light preference (the GUI_DESIGN §2.1 User tier; Phase 14 slice
  8); not audited (OD-P16), only the caller's own row.

Every response carries ``Cache-Control: no-store`` and the CSRF header
rule applies (``app.api.csrf``). Passwords travel as ``SecretStr`` and
every password field is capped at 1024 raw characters; nothing here
logs a request body.
"""

import datetime
import math
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.authorization import (
    CurrentUserDep,
    SignedInDep,
    clear_session_cookie,
    set_session_cookie,
)
from app.api.dependencies import SessionDep, SetupGateDep
from app.api.scan_station import ThemePreferenceLiteral
from app.application import authentication, first_run, two_step, two_step_factors
from app.application.authentication import (
    SESSION_COOKIE,
    Principal,
    SecondStepRequired,
    SessionGrant,
)
from app.core.config import get_settings
from app.domain.enums import Permission, SecondStepMethod

router = APIRouter(prefix="/api")

# Every password or token field: at most 1024 raw characters (a longer
# body fails validation before any normalization or hashing).
Secret = Annotated[SecretStr, Field(max_length=1024)]


class SessionUserResponse(BaseModel):
    id: int
    login_name: str
    display_name: str
    role_id: int
    role_name: str
    avatar_updated_at: datetime.datetime | None
    # The role's effective permission keys, sorted.
    permissions: list[Permission]
    must_change_password: bool
    # null = the sign-in never expires.
    session_expires_at: datetime.datetime | None
    # The signed-in User's saved theme (GUI_DESIGN §2.1 User tier); null = no preference.
    theme_preference: ThemePreferenceLiteral | None


class SessionStateResponse(BaseModel):
    user: SessionUserResponse | None
    setup_open: bool


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_name: str = Field(max_length=1024)
    password: Secret


class OwnPasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Secret
    new_password: Secret


class SecondStepResponse(BaseModel):
    """A correct password of a User with two-step sign-in (202): finish with a code."""

    methods: list[SecondStepMethod]
    # The masked address an emailed code goes to; null when none can be sent.
    email_hint: str | None
    expires_at: datetime.datetime


class SecondStepRequest(BaseModel):
    """The code is a credential: never echoed in a refusal or a log."""

    model_config = ConfigDict(extra="forbid")

    method: SecondStepMethod
    code: Secret


class EmailCodeResponse(BaseModel):
    sent_to: str
    resend_after_seconds: int
    sends_left: int


class PasskeyResponse(BaseModel):
    id: int
    name: str
    # The host it was made at, the only host it works at.
    rp_id: str
    created_at: datetime.datetime
    last_used_at: datetime.datetime | None


class PasskeyOptionsResponse(BaseModel):
    """WebAuthn options in their JSON form (binary values as unpadded base64url)."""

    options: dict[str, Any]


class PasskeyCredentialRequest(BaseModel):
    """The browser's ``PublicKeyCredential`` in its JSON form."""

    model_config = ConfigDict(extra="forbid")

    credential: dict[str, Any]


class PasskeyAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=200)
    credential: dict[str, Any]


class TwoStepStatusResponse(BaseModel):
    enabled: bool
    authenticator_app: bool
    passkeys: list[PasskeyResponse]
    recovery_codes_left: int
    # The User's own address; null = none.
    email: str | None
    email_ready: bool


class OwnEmailChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Secret
    # null or blank removes the address.
    email: str | None


class OwnEmailResponse(BaseModel):
    # The stored canonical address; null = none.
    email: str | None


class CurrentPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Secret


class TotpSetupResponse(BaseModel):
    """Answered once, to add the app: the key, its otpauth link and QR code."""

    secret: str
    uri: str
    qr: list[str]


class TotpConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=64)


class RecoveryCodesResponse(BaseModel):
    # Shown once; null when confirming kept the existing codes.
    recovery_codes: list[str] | None


class OwnThemePreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme_preference: ThemePreferenceLiteral


class OwnThemePreferenceResponse(BaseModel):
    theme_preference: ThemePreferenceLiteral


def session_user_response(principal: Principal) -> SessionUserResponse:
    return SessionUserResponse(
        id=principal.user_id,
        login_name=principal.login_name,
        display_name=principal.display_name,
        role_id=principal.role_id,
        role_name=principal.role_name,
        avatar_updated_at=principal.avatar_updated_at,
        permissions=sorted(principal.permissions),
        must_change_password=principal.must_change_password,
        session_expires_at=principal.session_expires_at,
        theme_preference=(
            None if principal.theme_preference is None else principal.theme_preference.value
        ),
    )


def session_state(principal: Principal | None, setup_open: bool) -> SessionStateResponse:
    return SessionStateResponse(
        user=None if principal is None else session_user_response(principal),
        setup_open=setup_open,
    )


def grant_session(response: Response, grant: SessionGrant) -> None:
    """Put a new session's token into the cookie (its only destination)."""
    set_session_cookie(response, grant.token, authentication.cookie_max_age(grant.principal))


_SIGN_IN_COOKIE_PATH = "/api/session"


def _set_sign_in_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        two_step_factors.SIGN_IN_COOKIE,
        token,
        max_age=max_age,
        path=_SIGN_IN_COOKIE_PATH,
        secure=get_settings().session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_sign_in_cookie(response: Response) -> None:
    _set_sign_in_cookie(response, "", 0)


def _origin(request: Request) -> str | None:
    """The page a request came from: the host passkeys are made and used at."""
    return request.headers.get("origin")


def _cookie_seconds(expires_at: datetime.datetime) -> int:
    remaining = expires_at - datetime.datetime.now(datetime.UTC)
    return max(1, math.ceil(remaining.total_seconds()))


def _two_step_status(status: two_step.TwoStepStatus) -> TwoStepStatusResponse:
    return TwoStepStatusResponse(
        enabled=status.enabled,
        authenticator_app=status.authenticator_app,
        passkeys=[PasskeyResponse(**passkey._asdict()) for passkey in status.passkeys],
        recovery_codes_left=status.recovery_codes_left,
        email=status.email,
        email_ready=status.email_ready,
    )


@router.get("/session")
def get_session_state(
    request: Request, response: Response, session: SessionDep, gate: SetupGateDep
) -> SessionStateResponse:
    token = request.cookies.get(SESSION_COOKIE)
    principal = authentication.resolve_principal(session, token)
    if principal is not None and token is not None:
        set_session_cookie(response, token, authentication.cookie_max_age(principal))
    elif token is not None:
        clear_session_cookie(response)
    return session_state(principal, first_run.is_setup_open(session, gate))


@router.post("/session", response_model=SessionStateResponse | SecondStepResponse)
def sign_in(
    body: SignInRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse | SecondStepResponse:
    result = authentication.sign_in(
        session,
        login_name=body.login_name,
        password=body.password.get_secret_value(),
        replaced_token=request.cookies.get(SESSION_COOKIE),
        replaced_challenge_token=request.cookies.get(two_step_factors.SIGN_IN_COOKIE),
        origin=_origin(request),
    )
    if isinstance(result, SecondStepRequired):
        response.status_code = 202
        _set_sign_in_cookie(response, result.token, _cookie_seconds(result.expires_at))
        return SecondStepResponse(
            methods=list(result.methods),
            email_hint=result.email_hint,
            expires_at=result.expires_at,
        )
    grant_session(response, result)
    return session_state(result.principal, first_run.is_setup_open(session, gate))


@router.post("/session/second-step")
def complete_second_step(
    body: SecondStepRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse:
    # A refusal leaves the cookie as it is: an ended second step's cookie
    # names nothing open, expires with it, and a new password step replaces it.
    grant = authentication.complete_second_step(
        session,
        token=request.cookies.get(two_step_factors.SIGN_IN_COOKIE),
        method=body.method,
        code=body.code.get_secret_value(),
        replaced_token=request.cookies.get(SESSION_COOKIE),
    )
    grant_session(response, grant)
    _clear_sign_in_cookie(response)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))


@router.post("/session/second-step/passkey-options")
def passkey_step_options(request: Request, session: SessionDep) -> PasskeyOptionsResponse:
    return PasskeyOptionsResponse(
        options=two_step.passkey_step_options(
            session, request.cookies.get(two_step_factors.SIGN_IN_COOKIE), _origin(request)
        )
    )


@router.post("/session/second-step/passkey")
def complete_second_step_with_passkey(
    body: PasskeyCredentialRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse:
    grant = authentication.complete_second_step_with_passkey(
        session,
        token=request.cookies.get(two_step_factors.SIGN_IN_COOKIE),
        credential=body.credential,
        origin=_origin(request),
        replaced_token=request.cookies.get(SESSION_COOKIE),
    )
    grant_session(response, grant)
    _clear_sign_in_cookie(response)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))


@router.post("/session/passkey/options")
def begin_passkey_sign_in(
    request: Request, response: Response, session: SessionDep
) -> PasskeyOptionsResponse:
    start = authentication.begin_passkey_sign_in(
        session,
        origin=_origin(request),
        replaced_challenge_token=request.cookies.get(two_step_factors.SIGN_IN_COOKIE),
    )
    _set_sign_in_cookie(response, start.token, _cookie_seconds(start.expires_at))
    return PasskeyOptionsResponse(options=start.options)


@router.post("/session/passkey")
def sign_in_with_passkey(
    body: PasskeyCredentialRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse:
    grant = authentication.sign_in_with_passkey(
        session,
        token=request.cookies.get(two_step_factors.SIGN_IN_COOKIE),
        credential=body.credential,
        origin=_origin(request),
        replaced_token=request.cookies.get(SESSION_COOKIE),
    )
    grant_session(response, grant)
    _clear_sign_in_cookie(response)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))


@router.post("/session/second-step/email-code")
def send_sign_in_code(request: Request, session: SessionDep) -> EmailCodeResponse:
    sent = two_step.send_email_code(session, request.cookies.get(two_step_factors.SIGN_IN_COOKIE))
    return EmailCodeResponse(
        sent_to=sent.sent_to,
        resend_after_seconds=sent.resend_after_seconds,
        sends_left=sent.sends_left,
    )


@router.get("/session/two-step")
def get_two_step(principal: SignedInDep, session: SessionDep) -> TwoStepStatusResponse:
    return _two_step_status(two_step.status(session, principal))


@router.post("/session/two-step/totp")
def begin_totp(
    body: CurrentPasswordRequest, principal: SignedInDep, session: SessionDep
) -> TotpSetupResponse:
    setup = two_step.begin_totp(
        session, principal, current_password=body.current_password.get_secret_value()
    )
    return TotpSetupResponse(secret=setup.secret, uri=setup.uri, qr=setup.qr)


@router.post("/session/two-step/totp/confirm")
def confirm_totp(
    body: TotpConfirmRequest, principal: SignedInDep, session: SessionDep
) -> RecoveryCodesResponse:
    return RecoveryCodesResponse(
        recovery_codes=two_step.confirm_totp(session, principal, code=body.code)
    )


@router.post("/session/two-step/totp/remove")
def remove_totp(
    body: CurrentPasswordRequest, principal: SignedInDep, session: SessionDep
) -> TwoStepStatusResponse:
    return _two_step_status(
        two_step.remove_totp(
            session, principal, current_password=body.current_password.get_secret_value()
        )
    )


@router.post("/session/two-step/recovery-codes")
def create_recovery_codes(
    body: CurrentPasswordRequest, principal: SignedInDep, session: SessionDep
) -> RecoveryCodesResponse:
    return RecoveryCodesResponse(
        recovery_codes=two_step.create_recovery_codes(
            session, principal, current_password=body.current_password.get_secret_value()
        )
    )


@router.post("/session/two-step/passkeys/options")
def begin_passkey(
    body: CurrentPasswordRequest, request: Request, principal: SignedInDep, session: SessionDep
) -> PasskeyOptionsResponse:
    return PasskeyOptionsResponse(
        options=two_step.begin_passkey(
            session,
            principal,
            current_password=body.current_password.get_secret_value(),
            origin=_origin(request),
        )
    )


@router.post("/session/two-step/passkeys")
def add_passkey(
    body: PasskeyAddRequest, request: Request, principal: SignedInDep, session: SessionDep
) -> RecoveryCodesResponse:
    return RecoveryCodesResponse(
        recovery_codes=two_step.add_passkey(
            session,
            principal,
            name=body.name,
            credential=body.credential,
            origin=_origin(request),
        )
    )


@router.post("/session/two-step/passkeys/{passkey_id}/remove")
def remove_passkey(
    passkey_id: int, body: CurrentPasswordRequest, principal: SignedInDep, session: SessionDep
) -> TwoStepStatusResponse:
    return _two_step_status(
        two_step.remove_passkey(
            session,
            principal,
            passkey_id,
            current_password=body.current_password.get_secret_value(),
        )
    )


@router.delete("/session", status_code=204)
def sign_out(request: Request, session: SessionDep) -> Response:
    authentication.sign_out(session, request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.put("/session/password")
def change_own_password(
    body: OwnPasswordChangeRequest,
    principal: CurrentUserDep,
    response: Response,
    session: SessionDep,
    gate: SetupGateDep,
) -> SessionStateResponse:
    grant = authentication.change_own_password(
        session,
        principal,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    grant_session(response, grant)
    return session_state(grant.principal, first_run.is_setup_open(session, gate))


@router.put("/session/email")
def change_own_email(
    body: OwnEmailChangeRequest, principal: SignedInDep, session: SessionDep
) -> OwnEmailResponse:
    return OwnEmailResponse(
        email=authentication.change_own_email(
            session,
            principal,
            current_password=body.current_password.get_secret_value(),
            email=body.email,
        )
    )


@router.put("/session/theme-preference")
def set_own_theme_preference(
    body: OwnThemePreferenceRequest, principal: SignedInDep, session: SessionDep
) -> OwnThemePreferenceResponse:
    """The signed-in User's own Dark/Light preference: the User tier of GUI_DESIGN §2.1.

    A display preference, not configuration, so it is not audited (OD-P16). Only the
    caller's own row. Absolute value: a repeat is a no-op. The response echoes the value
    this request saved or kept.
    """
    value = authentication.set_own_theme_preference(
        session, principal, theme_preference=body.theme_preference
    )
    return OwnThemePreferenceResponse(theme_preference=value.value)
