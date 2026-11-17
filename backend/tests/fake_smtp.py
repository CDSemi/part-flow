"""A stand-in for ``smtplib.SMTP`` and ``smtplib.SMTP_SSL`` (Phase 17 mail tests).

``install(monkeypatch)`` replaces both classes for one test and returns the
recorder: every connection it opened, the calls each received in order and
the messages sent. ``recorder.fail(stage, error)`` makes the next
connection raise ``error`` at ``connect``, ``starttls``, ``login`` or
``send``. Nothing ever leaves the process.
"""

import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

import pytest


@dataclass
class Connection:
    kind: str
    host: str
    port: int
    timeout: float | None
    context: ssl.SSLContext | None
    calls: list[Any] = field(default_factory=list)
    sent: list[EmailMessage] = field(default_factory=list)


@dataclass
class Recorder:
    connections: list[Connection] = field(default_factory=list)
    failures: dict[str, BaseException] = field(default_factory=dict)

    def fail(self, stage: str, error: BaseException) -> None:
        self.failures[stage] = error

    @property
    def sent(self) -> list[EmailMessage]:
        return [message for connection in self.connections for message in connection.sent]


def install(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()

    class _Fake:
        kind = "SMTP"

        def __init__(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            context: ssl.SSLContext | None = None,
        ) -> None:
            self.connection = Connection(self.kind, host, port, timeout, context)
            recorder.connections.append(self.connection)
            self._stage("connect")

        def _stage(self, stage: str) -> None:
            error = recorder.failures.pop(stage, None)
            if error is not None:
                raise error

        def __enter__(self) -> "_Fake":
            return self

        def __exit__(self, *exc: object) -> None:
            self.connection.calls.append("quit")

        def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
            self.connection.calls.append(("starttls", context))
            self._stage("starttls")

        def login(self, user: str, password: str) -> None:
            self.connection.calls.append(("login", user, password))
            self._stage("login")

        def send_message(self, message: EmailMessage) -> None:
            self.connection.calls.append("send")
            self._stage("send")
            self.connection.sent.append(message)

    class _FakeSsl(_Fake):
        kind = "SMTP_SSL"

    monkeypatch.setattr(smtplib, "SMTP", _Fake)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSsl)
    return recorder
