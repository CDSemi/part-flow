"""Structured log capture for the Phase 16 slice 6 tests.

``capture_json_logs()`` attaches one handler formatted by the production
``JsonFormatter`` to the root logger at DEBUG and yields a
:class:`JsonLogs` view of everything formatted while it is active: the
formatted lines and their parsed objects. The previous root level (and the
handler list) is restored in ``finally``. Records are formatted when they
are emitted — inside the request, so ``request_id`` is the request's.
"""

import contextlib
import json
import logging
import threading
from collections.abc import Iterator
from typing import Any

from app.core.structured_logging import JsonFormatter


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        with self._guard:
            self.lines.append(line)


class JsonLogs:
    """The formatted lines and parsed records captured so far."""

    def __init__(self, handler: _ListHandler) -> None:
        self._handler = handler

    @property
    def lines(self) -> list[str]:
        return list(self._handler.lines)

    @property
    def text(self) -> str:
        return "\n".join(self._handler.lines)

    @property
    def records(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self._handler.lines]

    def access(self) -> list[dict[str, Any]]:
        """The ``app.access`` records, in emission order."""
        return [record for record in self.records if record["logger"] == "app.access"]

    def clear(self) -> None:
        self._handler.lines.clear()


@contextlib.contextmanager
def capture_json_logs() -> Iterator[JsonLogs]:
    root = logging.getLogger()
    handler = _ListHandler()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield JsonLogs(handler)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
