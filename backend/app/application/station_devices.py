"""Enrolled Scan Station devices (Phase 14 slice 4; owner decisions OD-P6, OD-S4-9).

Only an enrolled device may act as a Scan Station. An administrator
issues a one-time **enrollment code** for one station (shown once,
stored only as a SHA-256 digest, valid 15 minutes, single use); the
station browser exchanges it for a **device token** (256 bits, returned
once, stored only as a SHA-256 digest) that it sends with every station
request. A device authenticates a terminal for one Scan Station — never
a person: the Worker the station records is decided by the Area's
Worker ID mode, and what the station may do by the keys of the role
applied at Scan Stations (``app.application.station_access``).

- ``resolve_station_device`` — the device of a presented token, on its
  own short transaction on its own connection, committed before the
  request's own session runs anything; also records ``last_seen_at``
  (at most once a minute) for every request with a valid, unrevoked
  token — refused requests included. A revocation committed after the
  resolution applies from the next request;
- ``issue_enrollment`` — a new PENDING device with its code; optionally
  re-enrolling (replacing) an active device of the same station, which
  keeps working until the new one is activated. Judged under the
  User-administration lock (``user_access.acting_user``): issuing needs
  ``MANAGE_SCAN_STATIONS``, plus ``MANAGE_CORRECTION_PERMISSIONS`` while
  the station role holds a protected key (OD-S4-9);
- ``activate_device`` — the code exchanged for the token (the code's
  digest is cleared, so a code works once); the replaced device, if
  still active, is revoked as REPLACED in the same transaction. Not
  idempotent by design: a lost response wastes the code;
- ``revoke_device`` — never guarded beyond ``MANAGE_SCAN_STATIONS``
  (removing capability is the safe direction); idempotent. A revocation
  never ends the station's Worker Session;
- ``list_open_devices`` — PENDING (unexpired) and ACTIVE devices;
- ``require_station_binding`` / ``require_area_binding`` — the station a
  station request addresses must be the device's own (403 D-2); an Area
  the device's station is no longer bound to is a stale station context
  (409 C-1), never a device refusal.

Neither a code, a token nor a digest is ever logged, audited or answered
except the code once at issue and the token once at activation. Every
enrollment, activation, replacement and revocation appends one
``ScanStationDevice`` audit row (``actor_user_id`` = the administrator;
NULL for the station's own activation).
"""

import datetime
import hashlib
import logging
import secrets
from typing import Any, Final, NamedTuple, NoReturn

from sqlalchemy import ColumnElement, Engine, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application import audit, authorization, station_access, user_access
from app.application.common import is_bindable_id
from app.application.errors import (
    STATION_DEVICE_MISMATCH_MESSAGE,
    ConflictError,
    EnrollmentCodeInvalidError,
    InvalidInputError,
    NotFoundError,
    StationContextChangedError,
    StationDeviceMismatchError,
)
from app.application.user_access import Principal
from app.domain.enums import (
    AuditEntityType,
    AuditEventType,
    StationDeviceRevokedReason,
    StationDeviceState,
)
from app.domain.station_device import (
    ENROLLMENT_CODE_ALPHABET,
    ENROLLMENT_CODE_LENGTH,
    INVALID_DEVICE_LABEL_MESSAGE,
    InvalidDeviceLabelError,
    format_enrollment_code,
    normalize_device_label,
    normalize_enrollment_code,
)
from app.infrastructure.models import ScanStation, ScanStationDevice

logger = logging.getLogger("app.station_devices")

ENROLLMENT_CODE_LIFETIME: Final = datetime.timedelta(minutes=15)
_LAST_SEEN_INTERVAL: Final = datetime.timedelta(seconds=60)
_CODE_ATTEMPTS: Final = 3
_CODE_DIGEST_UNIQUE: Final = "uq_scan_station_devices_enrollment_code_digest"
_TOKEN_MAX_LENGTH: Final = 128
# FOR KEY SHARE: the station must stay as judged; a station edit's own
# FOR KEY SHARE and UPDATE (FOR NO KEY UPDATE) never conflict with it.
_STATION_LOCK: Final = {"read": True, "key_share": True}

#: G-4: issuing a code while the station role holds a protected key.
ENROLLMENT_GUARD_MESSAGE: Final = (
    "Enrolling a device gives it the permissions of the role applied at Scan Stations,"
    " which include correction permissions. It needs the Manage correction permissions"
    " permission."
)
#: C-1: the device's station is no longer bound to the Area read.
STATION_CONTEXT_CHANGED_MESSAGE: Final = (
    "This Scan Station's Area changed. The station reloads with its current Area."
)


def _code_invalid_message(station_id: str) -> str:
    """E-1 — one answer for every reason, so a guess learns nothing."""
    return (
        f"This enrollment code is not valid for Scan Station {station_id}. It may have"
        " expired (codes last 15 minutes), been used already, or been issued for another"
        " station. Ask an administrator for a new code."
    )


class StationDevice(NamedTuple):
    """The device of one station request — plain values."""

    device_id: int
    station_id: str
    label: str


class StationDeviceView(NamedTuple):
    """A device as answered — plain values, read before COMMIT."""

    id: int
    station_id: str
    label: str
    state: StationDeviceState
    enrollment_expires_at: datetime.datetime
    activated_at: datetime.datetime | None
    last_seen_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    revoked_reason: StationDeviceRevokedReason | None
    replaces_device_id: int | None


class EnrollmentIssued(NamedTuple):
    device: StationDeviceView
    # Formatted ``ABCDE-FGHJK`` — answered once, never stored in clear.
    enrollment_code: str


class DeviceActivated(NamedTuple):
    device: StationDeviceView
    # Answered once, never stored in clear.
    device_token: str


def _digest(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("ascii")).digest()


def _new_code() -> str:
    return "".join(secrets.choice(ENROLLMENT_CODE_ALPHABET) for _ in range(ENROLLMENT_CODE_LENGTH))


def _is_token_shaped(token: str) -> bool:
    """1-128 printable ASCII characters; anything else is no token at all."""
    return 1 <= len(token) <= _TOKEN_MAX_LENGTH and token.isascii() and token.isprintable()


# ---------------------------------------------------------------------------
# Device resolution (every station request)
# ---------------------------------------------------------------------------


def _last_seen_stale() -> ColumnElement[bool]:
    return or_(
        ScanStationDevice.last_seen_at.is_(None),
        ScanStationDevice.last_seen_at < func.now() - _LAST_SEEN_INTERVAL,
    )


def resolve_station_device(engine: Engine, token: str | None) -> StationDevice | None:
    """The activated, unrevoked device of ``token``, or ``None``.

    Its own short transaction on its own connection, committed before
    the request's session executes anything, so a request never holds
    two connections and never holds a lock from this one. ``last_seen_at``
    is updated at most once a minute (one statement on the device row).
    A database error propagates.
    """
    if token is None or not _is_token_shaped(token):
        return None
    digest = _digest(token)
    with engine.begin() as connection:
        row = connection.execute(
            select(
                ScanStationDevice.id,
                ScanStationDevice.station_id,
                ScanStationDevice.label,
                _last_seen_stale(),
            ).where(
                ScanStationDevice.token_digest == digest,
                ScanStationDevice.revoked_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            return None
        device_id, station_id, label, stale = row
        if stale:
            connection.execute(
                update(ScanStationDevice)
                .where(
                    ScanStationDevice.id == device_id,
                    ScanStationDevice.revoked_at.is_(None),
                    _last_seen_stale(),
                )
                .values(last_seen_at=func.now())
            )
    return StationDevice(device_id=int(device_id), station_id=str(station_id), label=str(label))


def require_station_binding(device: StationDevice, station_id: str) -> None:
    """403 D-2 unless the request addresses the device's own station."""
    if device.station_id != station_id:
        raise StationDeviceMismatchError(STATION_DEVICE_MISMATCH_MESSAGE)


def require_area_binding(session: Session, device: StationDevice, area_id: int) -> None:
    """409 C-1 unless the device's station is currently bound to ``area_id``.

    An unlocked read: the token is valid, only the station's context may
    be stale (an administrator rebound the station) — nothing of the
    other Area is answered either way.
    """
    bound = session.scalar(
        select(ScanStation.area_id).where(ScanStation.station_id == device.station_id)
    )
    if bound != area_id:
        raise StationContextChangedError(STATION_CONTEXT_CHANGED_MESSAGE)


# ---------------------------------------------------------------------------
# Views and audit snapshots
# ---------------------------------------------------------------------------

_STATE: Final = case(
    (ScanStationDevice.revoked_at.is_not(None), StationDeviceState.REVOKED.value),
    (ScanStationDevice.activated_at.is_not(None), StationDeviceState.ACTIVE.value),
    (func.now() >= ScanStationDevice.enrollment_expires_at, StationDeviceState.EXPIRED.value),
    else_=StationDeviceState.PENDING.value,
)


def _views(session: Session, *where: ColumnElement[bool]) -> list[StationDeviceView]:
    rows = session.execute(
        select(
            ScanStationDevice.id,
            ScanStationDevice.station_id,
            ScanStationDevice.label,
            _STATE,
            ScanStationDevice.enrollment_expires_at,
            ScanStationDevice.activated_at,
            ScanStationDevice.last_seen_at,
            ScanStationDevice.revoked_at,
            ScanStationDevice.revoked_reason,
            ScanStationDevice.replaces_device_id,
        )
        .where(*where)
        .order_by(ScanStationDevice.station_id, ScanStationDevice.id)
    )
    return [
        StationDeviceView(
            id=int(device_id),
            station_id=station_id,
            label=label,
            state=StationDeviceState(state),
            enrollment_expires_at=expires_at,
            activated_at=activated_at,
            last_seen_at=last_seen_at,
            revoked_at=revoked_at,
            revoked_reason=None if reason is None else StationDeviceRevokedReason(reason),
            replaces_device_id=replaces,
        )
        for (
            device_id,
            station_id,
            label,
            state,
            expires_at,
            activated_at,
            last_seen_at,
            revoked_at,
            reason,
            replaces,
        ) in rows
    ]


def _view(session: Session, device_id: int) -> StationDeviceView:
    return _views(session, ScanStationDevice.id == device_id)[0]


def _iso(value: datetime.datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def device_snapshot(view: StationDeviceView) -> dict[str, Any]:
    """The audited device facet — never a code, token, digest or last seen."""
    return {
        "station_id": view.station_id,
        "label": view.label,
        "state": view.state.value,
        "enrollment_expires_at": _iso(view.enrollment_expires_at),
        "activated_at": _iso(view.activated_at),
        "revoked_at": _iso(view.revoked_at),
        "revoked_reason": None if view.revoked_reason is None else view.revoked_reason.value,
        "replaces_device_id": view.replaces_device_id,
    }


def _audit_update(
    session: Session,
    device_id: int,
    before: StationDeviceView,
    *,
    actor_user_id: int | None,
    metadata: dict[str, Any] | None = None,
) -> StationDeviceView:
    """Flush, read the device back and stage its UPDATED audit row."""
    session.flush()
    after = _view(session, device_id)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type=AuditEntityType.SCAN_STATION_DEVICE,
        entity_id=str(device_id),
        before_data=device_snapshot(before),
        after_data=device_snapshot(after),
        actor_user_id=actor_user_id,
        metadata=metadata,
    )
    return after


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def list_open_devices(session: Session) -> list[StationDeviceView]:
    """PENDING (unexpired) and ACTIVE devices by station, then id (a plain read)."""
    return _views(
        session,
        ScanStationDevice.revoked_at.is_(None),
        or_(
            ScanStationDevice.activated_at.is_not(None),
            func.now() < ScanStationDevice.enrollment_expires_at,
        ),
    )


def issue_enrollment(
    session: Session,
    station_id: str,
    *,
    label: object,
    replaces_device_id: object,
    actor: Principal,
) -> EnrollmentIssued:
    """Issue a one-time enrollment code for ``station_id`` (a new PENDING device).

    Order: the name rule (422) → the User-administration lock and the
    actor re-read → the enrollment guard on the station role's CURRENT
    grants (403) → the station FOR KEY SHARE (404 unknown, 409 inactive)
    → the replaced device FOR UPDATE (409 unless an active device of
    this station) → INSERT → audit → COMMIT. Under the lock no role
    write can change the guard's inputs before COMMIT.
    """
    try:
        clean_label = normalize_device_label(label)
    except InvalidDeviceLabelError as exc:
        raise InvalidInputError(INVALID_DEVICE_LABEL_MESSAGE) from exc
    acting = user_access.acting_user(session, actor)
    required = station_access.enrollment_permissions(session)
    guarded = station_access.lacks_enrollment_guard(required, acting.permissions)
    user_access.judge(
        session,
        lambda: authorization.require(
            acting, required, detail=ENROLLMENT_GUARD_MESSAGE if guarded else None
        ),
    )
    station = session.get(
        ScanStation, station_id, with_for_update=_STATION_LOCK, populate_existing=True
    )
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive. Reactivate it before enrolling a device."
        )
    replaced_id: int | None = None
    if replaces_device_id is not None:
        replaced = (
            session.get(
                ScanStationDevice, replaces_device_id, with_for_update=True, populate_existing=True
            )
            if isinstance(replaces_device_id, int) and is_bindable_id(replaces_device_id)
            else None
        )
        if (
            replaced is None
            or replaced.station_id != station_id
            or replaced.activated_at is None
            or replaced.revoked_at is not None
        ):
            raise ConflictError(
                f"Only an enrolled device of Scan Station {station_id} can be re-enrolled."
                " Reload the device list and try again."
            )
        replaced_id = replaced.id

    code, device_id = _insert_pending_device(session, station_id, clean_label, replaced_id)
    view = _view(session, device_id)
    audit.append_audit_event(
        session,
        event_type=AuditEventType.CREATED,
        entity_type=AuditEntityType.SCAN_STATION_DEVICE,
        entity_id=str(device_id),
        before_data=None,
        after_data=device_snapshot(view),
        actor_user_id=acting.user_id,
    )
    session.commit()
    logger.info("Scan Station device %s enrollment issued for station %s", device_id, station_id)
    return EnrollmentIssued(device=view, enrollment_code=format_enrollment_code(code))


def _insert_pending_device(
    session: Session, station_id: str, label: str, replaces_device_id: int | None
) -> tuple[str, int]:
    """INSERT the PENDING row; a code digest collision regenerates the code
    inside a savepoint (at most ``_CODE_ATTEMPTS``), then the error propagates."""
    for attempt in range(1, _CODE_ATTEMPTS + 1):
        code = _new_code()
        device = ScanStationDevice(
            station_id=station_id,
            label=label,
            enrollment_code_digest=_digest(code),
            enrollment_expires_at=func.now() + ENROLLMENT_CODE_LIFETIME,
            replaces_device_id=replaces_device_id,
        )
        try:
            with session.begin_nested():
                session.add(device)
                session.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint != _CODE_DIGEST_UNIQUE or attempt == _CODE_ATTEMPTS:
                raise
            continue
        return code, device.id
    raise AssertionError("unreachable")  # pragma: no cover


def activate_device(
    session: Session, station_id: str, *, enrollment_code: object
) -> DeviceActivated:
    """Exchange an enrollment code for the device token (403 E-1 otherwise).

    One generic refusal for an unknown, used, expired, revoked or
    other-station code; a code that cannot be one is refused without a
    lookup. The station's activity is not re-checked (its commands
    refuse on their own). The replaced device, still active, is revoked
    as REPLACED first, in the same transaction.
    """
    code = normalize_enrollment_code(enrollment_code)
    if code is None:
        _refuse_activation(session, station_id)
    found = session.execute(
        select(ScanStationDevice, func.now() >= ScanStationDevice.enrollment_expires_at)
        .where(ScanStationDevice.enrollment_code_digest == _digest(code))
        .with_for_update(of=ScanStationDevice)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if found is None:
        _refuse_activation(session, station_id)
    device, expired = found
    if device.station_id != station_id or device.revoked_at is not None or expired:
        _refuse_activation(session, station_id)
    device_id = device.id
    before = _view(session, device_id)

    if device.replaces_device_id is not None:
        replaced = session.get(
            ScanStationDevice,
            device.replaces_device_id,
            with_for_update=True,
            populate_existing=True,
        )
        if (
            replaced is not None
            and replaced.activated_at is not None
            and replaced.revoked_at is None
        ):
            replaced_before = _view(session, replaced.id)
            replaced.revoked_at = func.now()
            replaced.revoked_reason = StationDeviceRevokedReason.REPLACED
            _audit_update(
                session,
                replaced_before.id,
                replaced_before,
                actor_user_id=None,
                metadata={"source": "station-activation", "replaced_by_device_id": device_id},
            )

    token = secrets.token_urlsafe(32)
    device.token_digest = _digest(token)
    device.activated_at = func.now()
    device.enrollment_code_digest = None
    view = _audit_update(
        session,
        device_id,
        before,
        actor_user_id=None,
        metadata={"source": "station-activation"},
    )
    session.commit()
    logger.info("Scan Station device %s enrolled for station %s", device_id, station_id)
    return DeviceActivated(device=view, device_token=token)


def _refuse_activation(session: Session, station_id: str) -> NoReturn:
    session.rollback()
    logger.info("Scan Station device enrollment refused for station %s", station_id)
    raise EnrollmentCodeInvalidError(_code_invalid_message(station_id))


def revoke_device(session: Session, device_id: int, *, actor_user_id: int) -> StationDeviceView:
    """Revoke a pending or active device; an already revoked one is answered
    as it is, with nothing written (idempotent)."""
    device = (
        session.get(ScanStationDevice, device_id, with_for_update=True, populate_existing=True)
        if is_bindable_id(device_id)
        else None
    )
    if device is None:
        raise NotFoundError(f"Scan Station device {device_id} does not exist.")
    before = _view(session, device.id)
    if device.revoked_at is not None:
        session.rollback()
        return before
    device.revoked_at = func.now()
    device.revoked_reason = StationDeviceRevokedReason.REVOKED
    view = _audit_update(session, device_id, before, actor_user_id=actor_user_id)
    session.commit()
    logger.info("Scan Station device %s of station %s revoked", device_id, view.station_id)
    return view
