"""Viewing a Scan Station without an enrolled device (owner decision 2026-11-17).

A signed-in User whose role holds Manage Scan Stations may open any Scan
Station in the standard mode in a browser that is not an enrolled device
and see what the station shows. Viewing is read-only: every production
command still needs an enrolled device of that station, because each
Movement records the device that made it (owner decision OD-P6). Only
the station context and the Area inventory answer a viewer
(``app.api.station_viewing``).
"""

from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.errors import InvalidInputError, StationContextChangedError
from app.application.station_devices import STATION_CONTEXT_CHANGED_MESSAGE
from app.domain.enums import Permission
from app.infrastructure.models import ScanStation

#: The key that lets a signed-in User view a Scan Station without an
#: enrolled device.
VIEW_PERMISSION: Final = Permission.MANAGE_SCAN_STATIONS

VIEWED_STATION_REQUIRED_MESSAGE: Final = (
    "Name the Scan Station whose Area this is (station_id): without an enrolled device,"
    " an Area inventory is read only for the station being viewed."
)


def require_viewed_station_area(session: Session, station_id: str | None, area_id: int) -> None:
    """A viewer's Area inventory read: 409 C-1 unless the viewed station is
    currently bound to ``area_id`` — the same rule as an enrolled device's
    (``station_devices.require_area_binding``), so nothing of an Area the
    station left is answered. An unknown station is answered the same way;
    the station context read reports it as unknown first. An unlocked read.
    """
    if station_id is None:
        raise InvalidInputError(VIEWED_STATION_REQUIRED_MESSAGE)
    bound = session.scalar(select(ScanStation.area_id).where(ScanStation.station_id == station_id))
    if bound != area_id:
        raise StationContextChangedError(STATION_CONTEXT_CHANGED_MESSAGE)
