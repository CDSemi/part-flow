"""Schema readiness of a release (Phase 16 slice 3; OD-16-06 strict readiness).

A release is ready for changes only when the database is at the single
Alembic head of its own image (``current``), or when the database is at
the one revision the operator accepted for rollback path 2 and that
revision is unknown to this image (``accepted``: a database newer than
the code). Anything else is a ``mismatch``; a database that cannot be
read is ``unknown``. The override never matches another revision, and a
revision this image knows (its head or an ancestor: an older schema that
lacks constraints the code relies on) is never accepted.

``ReadinessMonitor`` is the per-process view used by ``GET /api/health``
(``observe``: always reads, it is also the connectivity ping) and by the
release gate (``current``: the last successful observation while at most
``CACHE_SECONDS`` old).
"""

import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import Engine

from app.infrastructure import schema_revision
from app.infrastructure.database import DatabaseUnavailableError

SchemaState = Literal["current", "accepted", "mismatch", "unknown"]
CACHE_SECONDS: Final = 5.0


@dataclass(frozen=True)
class SchemaReadiness:
    state: SchemaState
    expected_revision: str
    database_revision: str | None
    accepted_revision: str | None

    @property
    def ready(self) -> bool:
        return self.state in ("current", "accepted")


def override_ignored(accepted_revision: str | None, known_revisions: Collection[str]) -> bool:
    """The override names a revision this image knows, so it is never honoured."""
    return accepted_revision is not None and accepted_revision in known_revisions


def evaluate(
    *,
    expected_revision: str,
    database_revision: str | None,
    accepted_revision: str | None,
    known_revisions: Collection[str],
) -> SchemaState:
    """``current``, ``accepted`` or ``mismatch`` for a database that could be read."""
    if database_revision == expected_revision:
        return "current"
    if (
        accepted_revision is not None
        and database_revision == accepted_revision
        and not override_ignored(accepted_revision, known_revisions)
    ):
        return "accepted"
    return "mismatch"


class ReadinessMonitor:
    """Observes the database revision; caches the last successful observation."""

    def __init__(
        self,
        engine: Engine,
        *,
        expected_revision: str,
        known_revisions: Collection[str],
        accepted_revision: str | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._expected = expected_revision
        self._known = frozenset(known_revisions)
        self._accepted = accepted_revision
        self._clock = clock
        self._lock = threading.Lock()
        self._last: tuple[float, SchemaReadiness] | None = None

    def observe(self) -> SchemaReadiness:
        """Read the database revision now (``unknown`` when it cannot be read)."""
        try:
            database = schema_revision.read_database_revision(self._engine)
        except DatabaseUnavailableError:
            # A failed read is never cached: the next caller reads again.
            return self._readiness("unknown", None)
        state = evaluate(
            expected_revision=self._expected,
            database_revision=database,
            accepted_revision=self._accepted,
            known_revisions=self._known,
        )
        readiness = self._readiness(state, database)
        with self._lock:
            self._last = (self._clock(), readiness)
        return readiness

    def fresh(self) -> SchemaReadiness | None:
        """The last successful observation while at most ``CACHE_SECONDS`` old."""
        with self._lock:
            last = self._last
        if last is None or self._clock() - last[0] > CACHE_SECONDS:
            return None
        return last[1]

    def current(self) -> SchemaReadiness:
        """``fresh()``, else a new observation (two concurrent misses may both read)."""
        return self.fresh() or self.observe()

    def _readiness(self, state: SchemaState, database: str | None) -> SchemaReadiness:
        return SchemaReadiness(
            state=state,
            expected_revision=self._expected,
            database_revision=database,
            accepted_revision=self._accepted,
        )
