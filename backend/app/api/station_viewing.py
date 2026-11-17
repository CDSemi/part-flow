"""Scan Station reads that a signed-in viewer may make (owner decision 2026-11-17).

``RequireStationReader`` — the dependency of the Scan Station READS the
standard-mode station loads (the station context and the Area
inventory; ``app.api.route_access`` marks them ``station_viewer``):

- a request that carries a device token (the ``X-PartFlow-Station-Device``
  header, not blank) is judged exactly as ``RequireStationDevice`` judges
  it — an enrolled device of the station, else 401
  ``station_device_required`` / 403 ``station_device_mismatch``; a
  signed-in User never widens a refused device;
- a request without a device token is answered to a signed-in User whose
  role holds Manage Scan Stations (``station_viewing.VIEW_PERMISSION``)
  and who need not replace an administrator-set password first — a
  VIEWER; anyone else gets the 401 ``station_device_required`` of today.

Viewing is read-only. Every production command, the badge scan, the PN
and Machine resolves (they refresh a Worker Session), the Undo preview,
the theme save and both allocation routes keep ``RequireStationDevice``:
a viewer is refused there with 401 ``station_device_required`` and
nothing is recorded (OD-P6 — each Movement records the device that made
it).
"""

from typing import Annotated, NamedTuple

from fastapi import Depends, Header, Request

from app.api.authorization import STATION_DEVICE_HEADER, RequireStationDevice, holds
from app.api.dependencies import SessionDep
from app.application import authentication, station_viewing
from app.application.authentication import SESSION_COOKIE, Principal
from app.application.errors import STATION_DEVICE_REQUIRED_MESSAGE, StationDeviceRequiredError
from app.application.station_devices import StationDevice
from app.core import log_context


class StationReader(NamedTuple):
    """Who a station read is answered to: exactly one is set."""

    device: StationDevice | None
    viewer: Principal | None


class RequireStationReader:
    """Dependency factory: the enrolled device of the station, or — without a
    device token — a signed-in viewer (see the module docstring).

    ``bind_path_station`` binds a device to the path's ``station_id`` as
    ``RequireStationDevice`` does; a viewer may read any station.
    """

    def __init__(self, *, bind_path_station: bool) -> None:
        self.bind_path_station = bind_path_station
        self._device = RequireStationDevice(bind_path_station=bind_path_station)

    def __call__(
        self,
        request: Request,
        session: SessionDep,
        header: Annotated[str | None, Header(alias=STATION_DEVICE_HEADER)] = None,
    ) -> StationReader:
        if header is not None and header.strip():
            return StationReader(device=self._device(request, header), viewer=None)
        principal = authentication.resolve_principal(session, request.cookies.get(SESSION_COOKIE))
        if principal is None or not holds(principal, station_viewing.VIEW_PERMISSION):
            raise StationDeviceRequiredError(STATION_DEVICE_REQUIRED_MESSAGE)
        log_context.bind(user_id=principal.user_id)
        if self.bind_path_station:
            log_context.bind(station_id=request.path_params["station_id"])
        return StationReader(device=None, viewer=principal)


StationReaderForPathDep = Annotated[
    StationReader, Depends(RequireStationReader(bind_path_station=True))
]
StationReaderDep = Annotated[StationReader, Depends(RequireStationReader(bind_path_station=False))]
