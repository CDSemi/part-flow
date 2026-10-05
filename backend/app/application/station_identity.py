"""Worker identity of Scan Station commands (Phase 13 slice 3).

The one place that decides WHO a Scan Station command records
(PROJECT_PROFILE §8.11, §8.12, §8.13, §19; PLAN CD4/CD10), by the Worker
ID mode of the station's Area:

- ``DISABLED`` records no Worker;
- ``FIXED`` records the Area's configured Fixed Worker, who must still
  be active when the command is confirmed — an inactive Fixed Worker
  refuses the command with nothing recorded;
- ``SCANNED`` (the Worker of an open Worker Session) arrives with the
  session slices; until then the Area service refuses the mode, and a
  fixture-built ``SCANNED`` Area refuses every command rather than
  recording a NULL identity.

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

from collections.abc import Iterable
from typing import Final, NamedTuple

from sqlalchemy.orm import Session

from app.application.errors import ConflictError, NotFoundError
from app.domain.enums import WorkerIdentificationMode
from app.infrastructure.models import Area, PartMovement, ScanStation, Worker

# The resolver's lock on the Fixed Worker row: FOR KEY SHARE. It
# conflicts only with FOR UPDATE — which every Worker write takes
# (`workers._lock_worker`) — so a deactivation and a command recording
# that Worker serialize, while FK checks and concurrent commands never
# wait on each other.
_WORKER_KEY_SHARE: Final = {"read": True, "key_share": True}


class StationIdentity(NamedTuple):
    """Who one station command records (PLAN CD4). S4 adds scan_session_id."""

    worker_id: int | None


NO_IDENTITY: Final = StationIdentity(worker_id=None)


class WorkerIdentification(NamedTuple):
    """The Area's Worker ID mode as the station context presents it."""

    mode: WorkerIdentificationMode
    # Set exactly in FIXED mode.
    fixed_worker: Worker | None


def worker_identification(session: Session, area: Area) -> WorkerIdentification:
    """A read, no lock: the mode and, in FIXED mode, the Fixed Worker row."""
    mode = WorkerIdentificationMode(area.worker_identification_mode)
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
    - ``SCANNED`` → refused until the Worker-session slices exist.
    """
    area = session.get(Area, station.area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    mode = WorkerIdentificationMode(area.worker_identification_mode)
    if mode is WorkerIdentificationMode.DISABLED:
        return NO_IDENTITY
    if mode is WorkerIdentificationMode.SCANNED:
        raise ConflictError(
            f"Area '{area.name}' identifies Workers by badge sessions, which are not"
            " available yet. Nothing was recorded."
        )
    if area.fixed_worker_id is None:  # pragma: no cover - ck_areas_fixed_worker_shape
        raise ConflictError(f"Area '{area.name}' has no Fixed Worker. Nothing was recorded.")
    worker = session.get(
        Worker, area.fixed_worker_id, with_for_update=_WORKER_KEY_SHARE, populate_existing=True
    )
    if worker is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Worker {area.fixed_worker_id} does not exist.")
    if not worker.is_active:
        raise ConflictError(
            f"The Fixed Worker '{worker.name}' of Area '{area.name}' is inactive, so this"
            " action cannot record its Worker. Choose an active Fixed Worker in"
            " Administration → Areas. Nothing was recorded."
        )
    return StationIdentity(worker_id=worker.id)


def stamp_movements(rows: Iterable[PartMovement], identity: StationIdentity) -> None:
    """Set ``worker_id`` on every row of one command.

    Called immediately before the command's ``session.add_all`` /
    ``session.add``, so every row — the SPLIT / MERGED lineage prefix,
    an implicit AREA_COMPLETED with its TRANSFERRED, every REVERSED row
    — carries the same identity.
    """
    for row in rows:
        row.worker_id = identity.worker_id
