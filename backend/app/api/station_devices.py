"""Scan Station device endpoints (Phase 14 slice 4; owner decisions OD-P6, OD-S4-9).

Only an enrolled device may act as a Scan Station. An administrator
issues a one-time enrollment code for a station (Administration → Scan
Stations); the station browser exchanges it for a device token it sends
in ``X-PartFlow-Station-Device`` with every station request
(``app.api.authorization.RequireStationDevice``).

- ``GET  /scan-station-devices`` — the PENDING (unexpired) and ACTIVE
  devices with their last contact, and ``enrollment_permissions``: what
  issuing a code needs now. Any signed-in User;
- ``POST /scan-stations/{station_id}/device-enrollments`` — issue a code
  (201, the code answered ONCE, ``Cache-Control: no-store``), optionally
  re-enrolling (``replaces_device_id``) an active device of the station,
  which keeps working until the new one is activated. Needs Manage Scan
  Stations, plus Manage correction permissions while the role applied
  at Scan Stations holds a correction permission (judged under the
  User-administration lock);
- ``POST /scan-station-devices/{device_id}/revocation`` — revoke a
  pending or active device (200, also when already revoked). Needs
  Manage Scan Stations only;
- ``POST /scan-stations/{station_id}/device-activations`` — the station
  browser exchanges the code for its token (201, the token answered
  ONCE, ``Cache-Control: no-store``); public, gated by the code: 403
  ``enrollment_code_invalid`` for an unknown, used, expired, revoked or
  other-station code, one answer for every reason.

Routes stay thin: request schemas validate shape only
(``extra="forbid"``); ``app.application.station_devices`` owns the
rules, the audit protocol and the transactions; the central handlers in
``app.api.errors`` translate typed failures.
"""

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt

from app.api.authorization import RequirePermission, SignedInDep
from app.api.dependencies import SessionDep
from app.application import station_access, station_devices
from app.application.authentication import Principal
from app.domain.enums import Permission

router = APIRouter(prefix="/api")

StationManagerDep = Annotated[
    Principal, Depends(RequirePermission(Permission.MANAGE_SCAN_STATIONS))
]

_NO_STORE = "no-store"


class StationDeviceResponse(BaseModel):
    id: int
    station_id: str
    label: str
    state: Literal["PENDING", "ACTIVE", "EXPIRED", "REVOKED"]
    enrollment_expires_at: datetime.datetime
    activated_at: datetime.datetime | None
    last_seen_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    revoked_reason: Literal["REVOKED", "REPLACED"] | None
    replaces_device_id: int | None


class StationDeviceListResponse(BaseModel):
    # Only PENDING (unexpired) and ACTIVE devices.
    devices: list[StationDeviceResponse]
    # Sorted; what issuing an enrollment code needs now.
    enrollment_permissions: list[Permission]


class EnrollmentIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(max_length=1024)
    replaces_device_id: StrictInt | None = None


class EnrollmentIssuedResponse(BaseModel):
    device: StationDeviceResponse
    # "ABCDE-FGHJK" — shown once.
    enrollment_code: str


class DeviceActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enrollment_code: Annotated[SecretStr, Field(max_length=64)]


class ActivatedDeviceResponse(BaseModel):
    id: int
    station_id: str
    label: str
    activated_at: datetime.datetime


class DeviceActivationResponse(BaseModel):
    # Returned once; never again.
    device_token: str
    device: ActivatedDeviceResponse


def device_response(view: station_devices.StationDeviceView) -> StationDeviceResponse:
    return StationDeviceResponse(
        id=view.id,
        station_id=view.station_id,
        label=view.label,
        state=view.state.value,
        enrollment_expires_at=view.enrollment_expires_at,
        activated_at=view.activated_at,
        last_seen_at=view.last_seen_at,
        revoked_at=view.revoked_at,
        revoked_reason=None if view.revoked_reason is None else view.revoked_reason.value,
        replaces_device_id=view.replaces_device_id,
    )


@router.get("/scan-station-devices")
def list_station_devices(principal: SignedInDep, session: SessionDep) -> StationDeviceListResponse:
    devices = station_devices.list_open_devices(session)
    required = station_access.enrollment_permissions(session)
    return StationDeviceListResponse(
        devices=[device_response(view) for view in devices],
        enrollment_permissions=sorted(required, key=lambda key: key.value),
    )


@router.post("/scan-stations/{station_id}/device-enrollments", status_code=201)
def issue_device_enrollment(
    principal: StationManagerDep,
    station_id: str,
    body: EnrollmentIssueRequest,
    session: SessionDep,
    response: Response,
) -> EnrollmentIssuedResponse:
    issued = station_devices.issue_enrollment(
        session,
        station_id,
        label=body.label,
        replaces_device_id=body.replaces_device_id,
        actor=principal,
    )
    response.headers["Cache-Control"] = _NO_STORE
    return EnrollmentIssuedResponse(
        device=device_response(issued.device), enrollment_code=issued.enrollment_code
    )


@router.post("/scan-station-devices/{device_id}/revocation")
def revoke_station_device(
    principal: StationManagerDep, device_id: int, session: SessionDep
) -> StationDeviceResponse:
    view = station_devices.revoke_device(session, device_id, actor_user_id=principal.user_id)
    return device_response(view)


@router.post("/scan-stations/{station_id}/device-activations", status_code=201)
def activate_station_device(
    station_id: str, body: DeviceActivationRequest, session: SessionDep, response: Response
) -> DeviceActivationResponse:
    activated = station_devices.activate_device(
        session, station_id, enrollment_code=body.enrollment_code.get_secret_value()
    )
    response.headers["Cache-Control"] = _NO_STORE
    device = activated.device
    assert device.activated_at is not None  # an activated device
    return DeviceActivationResponse(
        device_token=activated.device_token,
        device=ActivatedDeviceResponse(
            id=device.id,
            station_id=device.station_id,
            label=device.label,
            activated_at=device.activated_at,
        ),
    )
