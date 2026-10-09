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
  (:class:`WorkerSessionRequiredError`). For a DONE, QUEUE or Undo
  whose badge-confirmation option is on (slice 5) it records instead the
  Worker of the confirming badge, whom the command itself signs in
  (open, switch or refresh — the badge-scan rules) and whose session it
  records.

The badge-confirmation gate (slice 5, PROJECT_PROFILE §16, §19; PLAN
CD6): every DONE, QUEUE and Undo ends in a final gate whose FORM
:func:`final_gate` decides — a required Worker badge scan exactly in a
Scanned-session Area whose option for the action is on, else the final
confirmation question (client-side only, not provable here). The
command enforces the badge form: a missing badge is refused
(:class:`BadgeConfirmationRequiredError`), a badge where none is
expected is refused (:class:`BadgeConfirmationNotExpectedError`), and a
badge that is not an active Worker's is refused
(:class:`BadgeNotRecognizedError`) — each with nothing recorded.

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
from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, NamedTuple

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.application import policies, worker_sessions, workers
from app.application.errors import ConflictError, InvalidInputError, NotFoundError
from app.application.worker_sessions import OpenSession
from app.core import log_context
from app.domain.enums import WorkerIdentificationMode
from app.domain.worker_badge import normalize_badge_barcode
from app.infrastructure.models import (
    ApplicationPolicy,
    Area,
    PartMovement,
    ScanStation,
    Worker,
    WorkerSession,
)

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


class SensitiveAction(StrEnum):
    """The three Scan Station actions that carry the final-confirmation gate (PROFILE §19)."""

    DONE = "DONE"
    QUEUE = "QUEUE"
    UNDO = "UNDO"


class FinalGate(StrEnum):
    """The form of the ALWAYS-present final gate (PROFILE §16, §19; GUI_DESIGN §4.6)."""

    # A Worker badge scan confirms and identifies the confirming Worker.
    BADGE = "BADGE"
    # A final toned confirmation question (no badge).
    QUESTION = "QUESTION"


_ALL_QUESTION: Final[Mapping[SensitiveAction, FinalGate]] = MappingProxyType(
    {action: FinalGate.QUESTION for action in SensitiveAction}
)


def final_gate(
    mode: WorkerIdentificationMode, policy: ApplicationPolicy, action: SensitiveAction
) -> FinalGate:
    """BADGE exactly in a Scanned-session Area whose option for ``action`` is on; else QUESTION.

    The one place the rule lives — the station context and the command
    resolver both call it.
    """
    if mode is not WorkerIdentificationMode.SCANNED:
        return FinalGate.QUESTION
    required = {
        SensitiveAction.DONE: policy.badge_confirm_done,
        SensitiveAction.QUEUE: policy.badge_confirm_queue,
        SensitiveAction.UNDO: policy.badge_confirm_undo,
    }[action]
    return FinalGate.BADGE if required else FinalGate.QUESTION


class BadgeConfirmationRequiredError(ConflictError):
    """The final gate is a badge scan, and none was sent: nothing written.

    The API adds ``{"badge_confirmation_required": true}`` so the station
    switches the gate to the badge scan, keeping the draft.
    """


class BadgeConfirmationNotExpectedError(ConflictError):
    """A badge was sent, but the final gate is the question: nothing written.

    The API adds ``{"badge_confirmation_not_expected": true}`` so the
    station asks the confirmation question again, keeping the draft.
    """


class BadgeNotRecognizedError(InvalidInputError):
    """The confirming badge is no active Worker's badge: nothing written.

    The API adds ``{"badge_not_recognized": true}`` so the station keeps
    the badge gate open with the error in place.
    """


_BADGE_REQUIRED: Final = (
    "This action is now confirmed by a Worker badge scan. Scan your badge to confirm."
    " Nothing was recorded."
)
_BADGE_NOT_EXPECTED: Final = (
    "This action is no longer confirmed by a badge scan. Confirm it again. Nothing was recorded."
)
_BADGE_NOT_RECOGNIZED: Final = (
    "Badge not recognized. Check the badge and scan again — nothing was recorded."
)


def confirming_badge_text(value: object) -> str | None:
    """Shape only, before a gated command's idempotency fast path: None or non-empty text.

    Whether a badge is expected, and whose it is, is judged after the
    fast path under the command's locks (:func:`resolve_station_identity`);
    the badge never joins a request fingerprint.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidInputError("The confirming badge must be text.")
    return value


class WorkerIdentification(NamedTuple):
    """The Area's Worker ID mode as the station context presents it."""

    mode: WorkerIdentificationMode
    # Set exactly in FIXED mode.
    fixed_worker: Worker | None
    # The station's valid Worker Session — only in SCANNED mode.
    session: OpenSession | None = None
    # The final-gate form of each sensitive action (`final_gate`), as read now.
    final_gates: Mapping[SensitiveAction, FinalGate] = _ALL_QUESTION

    @property
    def current_worker(self) -> Worker | None:
        """The Worker a command at the station would record now (a read)."""
        return self.session.worker if self.session is not None else self.fixed_worker


def worker_identification(session: Session, area: Area, station_id: str) -> WorkerIdentification:
    """A read, no lock, no write: the mode and, in FIXED mode, the Fixed
    Worker row; in SCANNED mode the station's valid session (an expired
    one is neither refreshed nor closed here) and the final-gate form of
    each sensitive action (QUESTION for all three outside SCANNED)."""
    mode = WorkerIdentificationMode(area.worker_identification_mode)
    if mode is WorkerIdentificationMode.SCANNED:
        policy = policies.get_policy(session)
        return WorkerIdentification(
            mode=mode,
            fixed_worker=None,
            session=worker_sessions.open_session(session, station_id),
            final_gates=MappingProxyType(
                {action: final_gate(mode, policy, action) for action in SensitiveAction}
            ),
        )
    if mode is not WorkerIdentificationMode.FIXED or area.fixed_worker_id is None:
        return WorkerIdentification(mode=mode, fixed_worker=None)
    return WorkerIdentification(mode=mode, fixed_worker=session.get(Worker, area.fixed_worker_id))


def resolve_station_identity(
    session: Session,
    station: ScanStation,
    *,
    gate: SensitiveAction | None = None,
    confirming_badge: str | None = None,
) -> StationIdentity:
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

    A DONE, QUEUE or Undo passes its ``gate`` (slice 5) and the
    ``confirming_badge`` it carries, if any (shape-checked before its
    fast path):

    1. the unlocked mode read above is not SCANNED → a badge is refused
       (:class:`BadgeConfirmationNotExpectedError`), else the Disabled /
       Fixed Worker identity;
    2. SCANNED without a badge → :func:`_session_identity`, which first
       refuses when the action's final gate is the badge scan
       (:class:`BadgeConfirmationRequiredError`, before any session
       work, so it takes precedence over a missing session);
    3. SCANNED with a badge → :func:`_badge_identity`: the gate form is
       re-judged under the station Area's ``FOR SHARE`` lock, the badge
       resolved to its active Worker and that Worker signed in by the
       command itself.
    """
    if gate is None and confirming_badge is not None:  # pragma: no cover - programming error
        raise AssertionError("A confirming badge is only ever passed with its gate.")
    area = session.get(Area, station.area_id)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    mode = WorkerIdentificationMode(area.worker_identification_mode)
    if mode is WorkerIdentificationMode.SCANNED:
        if gate is not None and confirming_badge is not None:
            return _logged(_badge_identity(session, station, gate, confirming_badge))
        return _logged(_session_identity(session, station, gate=gate))
    if confirming_badge is not None:
        raise BadgeConfirmationNotExpectedError(_BADGE_NOT_EXPECTED)
    return _logged(_identity_for(session, mode, area.fixed_worker_id, area.name))


def _logged(identity: StationIdentity) -> StationIdentity:
    """The access record names the command's Worker by id, never the badge (Phase 16 slice 6)."""
    if identity.worker_id is not None:
        log_context.bind(worker_id=identity.worker_id)
    return identity


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


def _session_identity(
    session: Session, station: ScanStation, *, gate: SensitiveAction | None = None
) -> StationIdentity:
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
       the Area at flush, taken earlier. A mode still SCANNED whose
       final gate for the command's ``gate`` is the badge scan refuses
       here (slice 5), before any session work;
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
    if gate is not None and final_gate(mode, policies.get_policy(session), gate) is FinalGate.BADGE:
        raise BadgeConfirmationRequiredError(_BADGE_REQUIRED)
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


def _badge_identity(
    session: Session, station: ScanStation, gate: SensitiveAction, confirming_badge: str
) -> StationIdentity:
    """The badge-gate identity: the confirming badge's Worker, signed in by the command.

    Lock order (after every lock of the command):

    1. the station's Area ``FOR SHARE`` — not KEY SHARE: it conflicts
       with an Area save's ``FOR NO KEY UPDATE``, so a mode change and
       this sign-in have one serial outcome and no session can open in an
       Area that left Scanned session mode. Where the command already
       holds the Area ``FOR UPDATE`` (an Undo restoring into it) the lock
       is a no-op. The gate form is re-judged on this locked read: no
       longer the badge scan → refused, nothing written;
    2. the badge resolved to its active Worker, then that Worker locked
       ``FOR KEY SHARE`` and re-judged — a deactivation or badge edit
       committed meanwhile refuses;
    3. :func:`worker_sessions.sign_in_locked` — the open row ``FOR NO KEY
       UPDATE``, the clock, then the open / switch / refresh.

    Every refusal comes before the first staged write; the sign-in's
    write commits with the command's rows, or not at all.
    """
    locked = session.execute(
        select(Area.worker_identification_mode, Area.worker_session_timeout_minutes)
        .where(Area.id == station.area_id)
        .with_for_update(read=True)
    ).one()
    mode = WorkerIdentificationMode(locked.worker_identification_mode)
    if final_gate(mode, policies.get_policy(session), gate) is not FinalGate.BADGE:
        raise BadgeConfirmationNotExpectedError(_BADGE_NOT_EXPECTED)
    found = workers.resolve_badge(session, confirming_badge)
    if found is None:
        raise BadgeNotRecognizedError(_BADGE_NOT_RECOGNIZED)
    worker = session.get(
        Worker, found.id, with_for_update=_WORKER_KEY_SHARE, populate_existing=True
    )
    if (
        worker is None
        or not worker.is_active
        or worker.badge_barcode != normalize_badge_barcode(confirming_badge)
    ):
        raise BadgeNotRecognizedError(_BADGE_NOT_RECOGNIZED)
    _, session_id = worker_sessions.sign_in_locked(
        session,
        station_id=station.station_id,
        area_id=station.area_id,
        worker=worker,
        timeout_override=locked.worker_session_timeout_minutes,
    )
    return StationIdentity(worker_id=worker.id, scan_session_id=session_id)


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
