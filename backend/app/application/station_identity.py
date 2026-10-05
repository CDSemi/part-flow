"""Worker identity of Scan Station commands (Phase 13 slice 3).

The one place that decides WHO a Scan Station command records
(PROJECT_PROFILE §8.11, §8.12, §8.13, §19; PLAN CD4/CD10), by the Worker
ID mode of the station's Area:

- ``DISABLED`` records no Worker;
- ``FIXED`` records the Area's configured Fixed Worker, who must still
  be active when the command is confirmed — an inactive Fixed Worker
  refuses the command with nothing recorded;
- ``SCANNED`` (Phase 13 slice 4) records the Worker of the station's
  valid Worker Session and that session (``scan_session_id``), and
  refreshes the session in the command's own transaction; without a
  valid session the command is refused with nothing recorded
  (:class:`WorkerSessionRequiredError`). The Area service still refuses
  a change to the mode until the badge gates exist.

Identity is accountability metadata only: no request carries it, it
never joins an idempotency fingerprint, and no eligibility, quantity,
route or allocation rule ever reads it. A command resolves it once,
after its idempotency fast path, its post-lock re-check and every
existing lock and refusal, immediately before its first staged write,
and stamps the same identity on every row it appends — so a committed
command replays with the identity it recorded, whatever the
configuration says since.

Nothing here commits or speaks HTTP.
"""

import datetime
from collections.abc import Iterable
from typing import Final, NamedTuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.application import worker_sessions
from app.application.errors import ConflictError, NotFoundError
from app.application.worker_sessions import OpenSession
from app.domain.enums import WorkerIdentificationMode
from app.infrastructure.models import Area, PartMovement, ScanStation, Worker, WorkerSession

# The resolver's lock on the Fixed Worker row: FOR KEY SHARE. It
# conflicts only with FOR UPDATE — which every Worker write takes
# (`workers._lock_worker`) — so a deactivation and a command recording
# that Worker serialize, while FK checks and concurrent commands never
# wait on each other.
_WORKER_KEY_SHARE: Final = {"read": True, "key_share": True}


class StationIdentity(NamedTuple):
    """Who one station command records (PLAN CD4).

    ``scan_session_id`` is the Scanned-session Worker Session the command
    records (None in Disabled / Fixed Worker mode).
    """

    worker_id: int | None
    scan_session_id: int | None = None


NO_IDENTITY: Final = StationIdentity(worker_id=None)


class WorkerSessionRequiredError(ConflictError):
    """No valid Worker Session at a Scanned-session station (CD5): nothing written.

    The API adds ``{"worker_session_required": true}`` so the station
    raises the badge sign-in and the operator confirms the unchanged
    request again.
    """


_SESSION_REQUIRED: Final = (
    "No Worker is signed in at this Scan Station. Scan your badge to continue."
    " Nothing was recorded."
)


class WorkerIdentification(NamedTuple):
    """The Area's Worker ID mode as the station context presents it."""

    mode: WorkerIdentificationMode
    # Set exactly in FIXED mode.
    fixed_worker: Worker | None
    # The station's valid Worker Session — only in SCANNED mode.
    session: OpenSession | None = None

    @property
    def current_worker(self) -> Worker | None:
        """The Worker a command at the station would record now (a read)."""
        return self.session.worker if self.session is not None else self.fixed_worker


def worker_identification(session: Session, area: Area, station_id: str) -> WorkerIdentification:
    """A read, no lock, no write: the mode and, in FIXED mode, the Fixed
    Worker row; in SCANNED mode the station's valid session (an expired
    one is neither refreshed nor closed here)."""
    mode = WorkerIdentificationMode(area.worker_identification_mode)
    if mode is WorkerIdentificationMode.SCANNED:
        return WorkerIdentification(
            mode=mode,
            fixed_worker=None,
            session=worker_sessions.open_session(session, station_id),
        )
    if mode is not WorkerIdentificationMode.FIXED or area.fixed_worker_id is None:
        return WorkerIdentification(mode=mode, fixed_worker=None)
    return WorkerIdentification(mode=mode, fixed_worker=session.get(Worker, area.fixed_worker_id))


def resolve_station_identity(session: Session, station: ScanStation) -> StationIdentity:
    """The identity a command at ``station`` records, judged NOW.

    Called once per command, after the idempotency fast path, the
    post-lock re-check and every existing lock and refusal, and before
    the first staged write (CD10). Reads the station's Area exactly as
    ``session.get(Area, station.area_id)`` — no lock, no
    ``populate_existing``: it returns the identity-map instance the
    command already loaded (the locked re-read of a transfer, stocking,
    Repair, receipt or quantity addition; the unlocked read of an
    in-Area command, a merge or an allocation), never a refresh of a
    locked instance. Where the command never loaded the station's Area
    (an Undo loads only the restored Areas), this is the one unlocked
    read, and the command is ordered before any later mode change.

    - ``DISABLED`` → :data:`NO_IDENTITY`.
    - ``FIXED`` → the Fixed Worker locked ``FOR KEY SHARE`` and re-read
      under the lock (it waits for a concurrent Worker write, then sees
      its committed result); an inactive Worker refuses the command.
    - ``SCANNED`` → the station's valid Worker Session, locked and
      refreshed (:func:`_session_identity`); without one the command is
      refused (:class:`WorkerSessionRequiredError`).
    """
    area = session.get(Area, station.area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    mode = WorkerIdentificationMode(area.worker_identification_mode)
    if mode is WorkerIdentificationMode.SCANNED:
        return _session_identity(session, station)
    return _identity_for(session, mode, area.fixed_worker_id, area.name)


def _identity_for(
    session: Session, mode: WorkerIdentificationMode, fixed_worker_id: int | None, area_name: str
) -> StationIdentity:
    """The Disabled / Fixed Worker identity (the slice 3 rule)."""
    if mode is WorkerIdentificationMode.DISABLED:
        return NO_IDENTITY
    if fixed_worker_id is None:  # pragma: no cover - ck_areas_fixed_worker_shape
        raise ConflictError(f"Area '{area_name}' has no Fixed Worker. Nothing was recorded.")
    worker = session.get(
        Worker, fixed_worker_id, with_for_update=_WORKER_KEY_SHARE, populate_existing=True
    )
    if worker is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Worker {fixed_worker_id} does not exist.")
    if not worker.is_active:
        raise ConflictError(
            f"The Fixed Worker '{worker.name}' of Area '{area_name}' is inactive, so this"
            " action cannot record its Worker. Choose an active Fixed Worker in"
            " Administration → Areas. Nothing was recorded."
        )
    return StationIdentity(worker_id=worker.id)


def _session_identity(session: Session, station: ScanStation) -> StationIdentity:
    """The Scanned-session identity: the station's valid session, refreshed.

    Lock order (PLAN CD5; deadlock-free against every configuration
    close, which holds the Area or the Worker before it locks session
    rows):

    1. the station's Area ``FOR KEY SHARE``, re-read without touching
       the identity-map instance — a mode that left SCANNED meanwhile
       resolves under the re-read mode. Where the command already holds
       the Area ``FOR UPDATE`` (transfers, stocking, Repair, receipts,
       quantity additions) the lock is redundant and the mode read
       exact; elsewhere it is the lock the Movement FK check takes on
       the Area at flush, taken earlier;
    2. the open row's Worker ``FOR KEY SHARE`` (a Worker deactivation
       holds the Worker, then locks session rows) — inactive refuses;
    3. the open row ``FOR NO KEY UPDATE``, held to COMMIT so no closer
       ends the session under the command — closed meanwhile refuses;
    4. the session clock, read after the locks — expired refuses;
    5. ``expires_at`` moves to the clock plus the effective timeout, in
       the command's own transaction.

    Every refusal comes before the command's first staged write.
    """
    locked = session.execute(
        select(
            Area.worker_identification_mode,
            Area.fixed_worker_id,
            Area.worker_session_timeout_minutes,
            Area.name,
        )
        .where(Area.id == station.area_id)
        .with_for_update(read=True, key_share=True)
    ).one()
    mode = WorkerIdentificationMode(locked.worker_identification_mode)
    if mode is not WorkerIdentificationMode.SCANNED:
        return _identity_for(session, mode, locked.fixed_worker_id, locked.name)
    open_row = session.execute(
        select(WorkerSession.id, WorkerSession.worker_id).where(
            WorkerSession.station_id == station.station_id, WorkerSession.ended_at.is_(None)
        )
    ).one_or_none()
    if open_row is None:
        raise WorkerSessionRequiredError(_SESSION_REQUIRED)
    worker = session.get(
        Worker, open_row.worker_id, with_for_update=_WORKER_KEY_SHARE, populate_existing=True
    )
    if worker is None or not worker.is_active:
        raise WorkerSessionRequiredError(_SESSION_REQUIRED)
    row = session.execute(
        select(WorkerSession.id, WorkerSession.expires_at)
        .where(WorkerSession.id == open_row.id, WorkerSession.ended_at.is_(None))
        .with_for_update(key_share=True)
    ).one_or_none()
    if row is None:
        raise WorkerSessionRequiredError(_SESSION_REQUIRED)
    now = worker_sessions.session_clock(session)
    if row.expires_at <= now:
        raise WorkerSessionRequiredError(_SESSION_REQUIRED)
    minutes = worker_sessions.timeout_minutes(session, locked.worker_session_timeout_minutes)
    session.execute(
        update(WorkerSession)
        .where(WorkerSession.id == row.id)
        .values(expires_at=now + datetime.timedelta(minutes=minutes))
        .execution_options(synchronize_session=False)
    )
    return StationIdentity(worker_id=worker.id, scan_session_id=row.id)


def stamp_movements(rows: Iterable[PartMovement], identity: StationIdentity) -> None:
    """Set ``worker_id`` and ``scan_session_id`` on every row of one command.

    Called immediately before the command's ``session.add_all`` /
    ``session.add``, so every row — the SPLIT / MERGED lineage prefix,
    an implicit AREA_COMPLETED with its TRANSFERRED, every REVERSED row
    — carries the same identity.
    """
    for row in rows:
        row.worker_id = identity.worker_id
        row.scan_session_id = identity.scan_session_id
