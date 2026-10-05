"""Scanned Worker Sessions (Phase 13 slice 4 — PROJECT_PROFILE §9, §19, §28).

The runtime of an Area in Scanned session mode: a Worker signs in at a
Scan Station by scanning an active badge, and the station's commands
record that Worker (`station_identity`). Only the session persists
(PROFILE §9 "Only the Worker Session persists") — one
``worker_sessions`` row per session, the audit trail of who was signed
in where, when and until when; never production truth.

Rules owned here (PLAN CD5):

- One open session per station at most (database partial UNIQUE). A
  session is VALID while open and ``expires_at`` is later than the
  session clock. An open row past its expiry is an expired, not yet
  closed session: every reader ignores it, and the next sign-in or
  configuration close at its station closes it ``EXPIRED`` at its
  ``expires_at`` — never with a configuration reason.
- The timeout is sliding: a valid production interaction (a command, a
  successfully resolved PN or Machine scan, a badge scan of the same
  Worker) moves ``expires_at`` to the clock plus the effective timeout —
  the station Area's override, else the ``application_policy`` default.
  A timeout change never rewrites open sessions; it applies from each
  session's next refresh or sign-in.
- The session clock is ``clock_timestamp()``, read AFTER every lock the
  writer takes, the ``worker_sessions`` row lock included — never
  ``now()`` (the transaction start): a writer that waited on a lock must
  not judge expiry or write a time from before its wait.
- Every writer locks the row(s) it writes first (``FOR NO KEY UPDATE``,
  re-evaluated on the latest row version after a wait); configuration
  closers lock their rows ``ORDER BY id``, so two closers never
  deadlock each other.

The writers of ``worker_sessions`` are :func:`sign_in`,
:func:`refresh_on_resolve`, :func:`close_open_sessions` and the
command-time refresh of ``station_identity``. :func:`sign_in` and
:func:`refresh_on_resolve` commit their own transaction (the latter
only when it refreshed a valid session); a close runs inside its
caller's configuration transaction. Nothing here speaks HTTP.
"""

import datetime
from collections.abc import Iterable
from enum import StrEnum
from typing import Final, NamedTuple, cast

from sqlalchemy import Row, case, func, select, update
from sqlalchemy.orm import Session

from app.application import policies
from app.application.common import commit
from app.application.errors import ConflictError, NotFoundError
from app.domain.enums import WorkerIdentificationMode, WorkerSessionEndReason
from app.infrastructure.models import Area, ScanStation, Worker, WorkerSession

# The sign-in's lock on the Worker it signs in: FOR KEY SHARE, which
# conflicts only with the FOR UPDATE of a Worker write (deactivation).
_WORKER_KEY_SHARE: Final = {"read": True, "key_share": True}
# The sign-in's lock on the station's Area: FOR SHARE, so a sign-in and
# an Area save (mode change) have one serial outcome.
_AREA_SHARE: Final = {"read": True}

_SIGN_IN_CONFLICTS: Final = {
    "uq_worker_sessions_open_station": (
        "Another badge was scanned at this Scan Station at the same moment."
        " Scan your badge again. Nothing was recorded."
    ),
}


class OpenSession(NamedTuple):
    """A valid session as the station presents it."""

    worker: Worker
    started_at: datetime.datetime
    expires_at: datetime.datetime
    # The session clock the validity was judged at.
    server_now: datetime.datetime


def session_clock(session: Session) -> datetime.datetime:
    """``clock_timestamp()`` — the wall clock now, not the transaction start."""
    return cast(datetime.datetime, session.scalar(select(func.clock_timestamp())))


def timeout_minutes(session: Session, override: int | None) -> int:
    """The effective timeout: the station Area's override, else the global default.

    Plain reads, no lock: a concurrent policy change applies from the
    next refresh either way.
    """
    if override is not None:
        return override
    return policies.get_policy(session).worker_session_timeout_minutes


def _timeout(session: Session, override: int | None) -> datetime.timedelta:
    return datetime.timedelta(minutes=timeout_minutes(session, override))


def open_session(session: Session, station_id: str) -> OpenSession | None:
    """A read, no lock, no write: the station's valid session, or None."""
    row = session.execute(
        select(WorkerSession.worker_id, WorkerSession.started_at, WorkerSession.expires_at).where(
            WorkerSession.station_id == station_id, WorkerSession.ended_at.is_(None)
        )
    ).one_or_none()
    if row is None:
        return None
    now = session_clock(session)
    if row.expires_at <= now:
        return None
    worker = session.get(Worker, row.worker_id)
    if worker is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Worker {row.worker_id} does not exist.")
    return OpenSession(
        worker=worker, started_at=row.started_at, expires_at=row.expires_at, server_now=now
    )


def _lock_open_row(
    session: Session, station_id: str
) -> Row[tuple[int, int, datetime.datetime, datetime.datetime]] | None:
    """The station's open row under ``FOR NO KEY UPDATE`` — the lock the
    writer's own UPDATE takes anyway, compatible with the FOR KEY SHARE a
    Movement's composite FK check takes — re-evaluated on the latest row
    version after a wait (a row closed meanwhile is skipped)."""
    return session.execute(
        select(
            WorkerSession.id,
            WorkerSession.worker_id,
            WorkerSession.started_at,
            WorkerSession.expires_at,
        )
        .where(WorkerSession.station_id == station_id, WorkerSession.ended_at.is_(None))
        .with_for_update(key_share=True)
    ).one_or_none()


def refresh_on_resolve(session: Session, station: ScanStation, area: Area) -> OpenSession | None:
    """Server-side refresh after a SUCCESSFULLY resolved PN or Machine scan (CD5, GUI §4.12).

    Not Scanned session mode → None, nothing written, no commit. Scanned
    session → the station's open row locked; none (or closed
    concurrently — the lock re-evaluation skips it) or expired → None,
    nothing written, no commit (an expired row stays for the lazy
    close); otherwise ``expires_at`` moves to the clock plus the
    effective timeout and the refresh commits — the ONLY commit a
    resolve ever makes. Never refuses: a resolve answers either way.
    """
    if area.worker_identification_mode != WorkerIdentificationMode.SCANNED:
        return None
    row = _lock_open_row(session, station.station_id)
    if row is None:
        return None
    now = session_clock(session)
    if row.expires_at <= now:
        return None
    expires_at = now + _timeout(session, area.worker_session_timeout_minutes)
    session.execute(
        update(WorkerSession)
        .where(WorkerSession.id == row.id)
        .values(expires_at=expires_at)
        .execution_options(synchronize_session=False)
    )
    worker = session.get(Worker, row.worker_id)
    if worker is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Worker {row.worker_id} does not exist.")
    session.commit()
    return OpenSession(
        worker=worker, started_at=row.started_at, expires_at=expires_at, server_now=now
    )


class SignInOutcome(StrEnum):
    """What a valid badge did to the station's session."""

    # No valid session was open (none, or an expired one now closed).
    SIGNED_IN = "SIGNED_IN"
    # Another Worker's valid session was closed SWITCHED.
    SWITCHED = "SWITCHED"
    # The same Worker's valid session was refreshed (no new row).
    REFRESHED = "REFRESHED"


class SignInResult(NamedTuple):
    outcome: SignInOutcome
    session: OpenSession
    # Set exactly for SWITCHED.
    previous_worker: Worker | None


def _lock_sign_in_station(session: Session, station_id: str) -> tuple[ScanStation, Area]:
    """The station ``FOR UPDATE`` and its Area ``FOR SHARE``, re-judged under the locks.

    The station lock serializes a sign-in with every command at the
    station (FOR UPDATE, or the allocation path's FOR KEY SHARE) and with
    a station rebind or deactivation; the Area lock with an Area save.
    Same refusals as every station read.
    """
    station = session.get(ScanStation, station_id, with_for_update=True, populate_existing=True)
    if station is None:
        raise NotFoundError(f"Scan Station '{station_id}' does not exist.")
    if not station.is_active:
        raise ConflictError(
            f"Scan Station '{station_id}' is inactive and accepts no production use."
        )
    area = session.get(Area, station.area_id, with_for_update=_AREA_SHARE, populate_existing=True)
    if area is None:  # pragma: no cover - FK guarantees the row
        raise NotFoundError(f"Area {station.area_id} does not exist.")
    if not area.is_active:
        raise ConflictError(
            f"Area '{area.name}' bound to Scan Station '{station_id}' is inactive"
            " and accepts no production use."
        )
    return station, area


def sign_in(session: Session, station_id: str, worker_id: int) -> SignInResult | None:
    """Open, switch or refresh the station's session for an ACTIVE Worker (PROFILE §19).

    Locks, in order: the station ``FOR UPDATE`` and its Area ``FOR
    SHARE`` (both re-judged: inactive → the existing 409; the Area no
    longer in Scanned session mode → None, nothing written), the Worker
    ``FOR KEY SHARE`` (inactive meanwhile → None, nothing written), then
    the open row ``FOR NO KEY UPDATE``; the clock is read after them.

    - no open row → a new session → SIGNED_IN;
    - an expired open row → closed EXPIRED at its expiry, a new session
      → SIGNED_IN;
    - a valid row of the same Worker → its expiry refreshed → REFRESHED;
    - a valid row of another Worker → closed SWITCHED now, a new session
      → SWITCHED.

    One transaction, committed here.
    """
    station, area = _lock_sign_in_station(session, station_id)
    if area.worker_identification_mode != WorkerIdentificationMode.SCANNED:
        return None
    worker = session.get(
        Worker, worker_id, with_for_update=_WORKER_KEY_SHARE, populate_existing=True
    )
    if worker is None or not worker.is_active:
        return None
    row = _lock_open_row(session, station.station_id)
    now = session_clock(session)
    expires_at = now + _timeout(session, area.worker_session_timeout_minutes)

    previous_worker: Worker | None = None
    if row is not None and row.expires_at > now and row.worker_id == worker.id:
        session.execute(
            update(WorkerSession)
            .where(WorkerSession.id == row.id)
            .values(expires_at=expires_at)
            .execution_options(synchronize_session=False)
        )
        commit(session, _SIGN_IN_CONFLICTS)
        return SignInResult(
            outcome=SignInOutcome.REFRESHED,
            session=OpenSession(
                worker=worker, started_at=row.started_at, expires_at=expires_at, server_now=now
            ),
            previous_worker=None,
        )

    outcome = SignInOutcome.SIGNED_IN
    if row is not None:
        if row.expires_at <= now:
            ended_at, reason = row.expires_at, WorkerSessionEndReason.EXPIRED
        else:
            ended_at, reason = now, WorkerSessionEndReason.SWITCHED
            outcome = SignInOutcome.SWITCHED
            previous_worker = session.get(Worker, row.worker_id)
        session.execute(
            update(WorkerSession)
            .where(WorkerSession.id == row.id)
            .values(ended_at=ended_at, end_reason=reason.value)
            .execution_options(synchronize_session=False)
        )
    session.add(
        WorkerSession(
            station_id=station.station_id,
            area_id=area.id,
            worker_id=worker.id,
            started_at=now,
            expires_at=expires_at,
        )
    )
    commit(session, _SIGN_IN_CONFLICTS)
    return SignInResult(
        outcome=outcome,
        session=OpenSession(worker=worker, started_at=now, expires_at=expires_at, server_now=now),
        previous_worker=previous_worker,
    )


def close_open_sessions(
    session: Session,
    *,
    reason: WorkerSessionEndReason,
    station_ids: Iterable[str] | None = None,
    worker_id: int | None = None,
) -> int:
    """Configuration close inside the CALLER's transaction (no commit).

    Exactly one of ``station_ids`` / ``worker_id``. The open rows are
    locked ``ORDER BY id`` first (closers never deadlock each other), the
    clock is read after the locks, and every row closes at the clock
    with ``reason`` — or ``EXPIRED`` at its own expiry when it already
    expired. Session closes append no ``audit_events`` row: the session
    row is its own audit record. Returns the number of rows closed.
    """
    if (station_ids is None) == (worker_id is None):
        raise ValueError("Pass exactly one of station_ids or worker_id.")
    query = select(WorkerSession.id).where(WorkerSession.ended_at.is_(None))
    if station_ids is not None:
        query = query.where(WorkerSession.station_id.in_(list(station_ids)))
    else:
        query = query.where(WorkerSession.worker_id == worker_id)
    ids = list(session.scalars(query.order_by(WorkerSession.id).with_for_update(key_share=True)))
    if not ids:
        return 0
    now = session_clock(session)
    session.execute(
        update(WorkerSession)
        .where(WorkerSession.id.in_(ids))
        .values(
            ended_at=func.least(now, WorkerSession.expires_at),
            end_reason=case(
                (WorkerSession.expires_at <= now, WorkerSessionEndReason.EXPIRED.value),
                else_=reason.value,
            ),
        )
        .execution_options(synchronize_session=False)
    )
    return len(ids)
