"""The production log record format (Phase 16 slice 6: LF-1 … LF-5).

``JsonFormatter`` writes one compact JSON object per record with its keys
in a fixed order; tracebacks never carry a database error's message (the
statement and the PostgreSQL ``DETAIL`` line); ``build_engine`` hides
statement parameters; the health probe's failure ERROR is throttled to
one per minute per process. LF-3/LF-3b use an empty temporary database
(temporary tables only), dropped afterwards.
"""

import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.request_log import RequestLogMiddleware
from app.core.structured_logging import JsonFormatter
from app.infrastructure import schema_revision
from app.infrastructure.database import DatabaseUnavailableError, build_engine
from tests.log_capture import capture_json_logs

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_structured_logging"


def _record(
    message: str,
    *args: object,
    level: int = logging.INFO,
    exc_info: Any = None,
    extra: dict[str, Any] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord("app.test", level, __file__, 1, message, args, exc_info)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    record.created = 1791460800.1234  # 2026-10-08T12:00:00.123Z
    return record


def test_json_formatter_shapes_every_record() -> None:
    """LF-1."""
    formatter = JsonFormatter()

    plain = formatter.format(_record("Hello %s", "world"))
    assert plain == (
        '{"ts":"2026-10-08T12:00:00.123Z","level":"INFO","logger":"app.test",'
        '"message":"Hello world"}'
    )

    payload = json.loads(
        formatter.format(
            _record("x", level=logging.ERROR, extra={"partflow": {"event": "e", "status": 1}})
        )
    )
    assert list(payload) == ["ts", "level", "logger", "message", "event", "status"]
    assert payload["level"] == "ERROR"

    try:
        raise ValueError("boom")
    except ValueError:
        line = formatter.format(_record("failed", exc_info=sys.exc_info()))
    failed = json.loads(line)
    assert list(failed) == ["ts", "level", "logger", "message", "exc_type", "traceback"]
    assert failed["exc_type"] == "ValueError"
    assert failed["traceback"].startswith("Traceback (most recent call last):\n")
    assert failed["traceback"].endswith("ValueError: boom")

    tricky = formatter.format(_record("line one\nline two é"))
    assert "\n" not in tricky
    assert "\\n" in tricky and "\\u00e9" in tricky
    assert '"level":"INFO"' in tricky


_DICT_CONFIG_PROBE = r"""
import io, json, logging, logging.config
from app.core.structured_logging import JsonFormatter
with open("app/core/logging.production.json", encoding="utf-8") as stream:
    logging.config.dictConfig(json.load(stream))
root = logging.getLogger()
uvicorn = logging.getLogger("uvicorn")
access = logging.getLogger("uvicorn.access")
error = logging.getLogger("uvicorn.error")
result = {
    "root": [type(h.formatter).__name__ for h in root.handlers],
    "uvicorn": [type(h.formatter).__name__ for h in uvicorn.handlers],
    "uvicorn_propagate": uvicorn.propagate,
    "error_handlers": len(error.handlers),
    "access_handlers": len(access.handlers),
    "access_propagate": access.propagate,
    "root_level": root.level,
}
result["shared_handler"] = root.handlers[0] is uvicorn.handlers[0]
stream = io.StringIO()
root.handlers[0].setStream(stream)
error.info("uvicorn error line")
access.info("an access line")
logging.getLogger("app.anything").info("app line")
result["lines"] = stream.getvalue().splitlines()
print(json.dumps(result))
"""


def test_production_dict_config() -> None:
    """LF-2: in a separate interpreter, so this process's logging stays untouched."""
    completed = subprocess.run(
        [sys.executable, "-c", _DICT_CONFIG_PROBE],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["root"] == ["JsonFormatter"]
    assert result["uvicorn"] == ["JsonFormatter"]
    assert result["uvicorn_propagate"] is False
    assert result["error_handlers"] == 0
    assert result["access_handlers"] == 0 and result["access_propagate"] is False
    assert result["root_level"] == logging.INFO
    # One stderr handler shared by root and uvicorn: uvicorn.error is formatted once,
    # uvicorn.access emits nothing at INFO.
    assert result["shared_handler"] is True
    lines = [json.loads(line) for line in result["lines"]]
    assert [(line["logger"], line["message"]) for line in lines] == [
        ("uvicorn.error", "uvicorn error line"),
        ("app.anything", "app line"),
    ]


@pytest.fixture(scope="module")
def scratch_url() -> Iterator[URL]:
    """An empty temporary database (temporary tables only)."""
    admin = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    yield make_url(os.environ["DATABASE_URL"]).set(database=_TEST_DATABASE)
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin.dispose()


def test_build_engine_hides_statement_parameters(scratch_url: URL) -> None:
    """LF-3."""
    engine = build_engine(scratch_url.render_as_string(hide_password=False))
    try:
        assert engine.hide_parameters is True
        with engine.connect() as connection:
            connection.execute(sa.text("CREATE TEMP TABLE lf3 (a integer NOT NULL)"))
            with pytest.raises(SQLAlchemyError) as raised:
                connection.execute(
                    sa.text("INSERT INTO lf3 (a) SELECT 1 / 0 WHERE :p IS NOT NULL"),
                    {"p": "secret-param"},
                )
        assert "secret-param" not in str(raised.value)
        assert "hidden due to hide_parameters" in str(raised.value)
    finally:
        engine.dispose()


def test_traceback_never_prints_a_database_message(scratch_url: URL) -> None:
    """LF-3b."""
    secret = "-".join(("secret", "key"))  # never a literal in a traceback source line
    engine = build_engine(scratch_url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text("CREATE TEMP TABLE lf3b (b text CONSTRAINT uq_lf3b_b UNIQUE)")
            )
            statement = sa.text("INSERT INTO lf3b (b) VALUES (:b)")
            parameters = {"b": secret}
            connection.execute(statement, parameters)
            with pytest.raises(IntegrityError) as raised:
                connection.execute(statement, parameters)
        assert secret in str(raised.value)  # the DETAIL line the formatter must drop
        try:
            raise RuntimeError("the command failed") from raised.value
        except RuntimeError:
            line = JsonFormatter().format(_record("failed", exc_info=sys.exc_info()))
    finally:
        engine.dispose()
    document = json.loads(line)
    trace = document["traceback"]
    assert document["exc_type"] == "RuntimeError"
    assert secret not in line and "DETAIL" not in line and "INSERT" not in line
    assert "sqlalchemy.exc.IntegrityError: sqlstate=23505 constraint=uq_lf3b_b table=lf3b" in trace
    assert "psycopg.errors.UniqueViolation: sqlstate=23505 constraint=uq_lf3b_b table=lf3b" in trace
    assert trace.endswith("RuntimeError: the command failed")
    assert "The above exception was the direct cause of the following exception:" in trace
    assert trace.count("Traceback (most recent call last):") == 3


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _failing_engine() -> sa.Engine:
    def refuse() -> Any:
        raise psycopg.OperationalError("connection refused (test)")

    return create_engine("postgresql+psycopg://", creator=refuse)


@pytest.fixture
def throttle_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeClock]:
    clock = _FakeClock()
    throttle = schema_revision.REVISION_READ_FAILURE_THROTTLE
    monkeypatch.setattr(throttle, "clock", clock)
    throttle.reset()
    yield clock
    throttle.reset()


def test_revision_read_failure_is_throttled(throttle_clock: _FakeClock) -> None:
    """LF-4."""
    engine = _failing_engine()
    try:
        with capture_json_logs() as logs:
            for _ in range(5):
                with pytest.raises(DatabaseUnavailableError):
                    schema_revision.read_database_revision(engine)
                throttle_clock.now += 10
            throttle_clock.now += 20  # 70 s after the first failure
            with pytest.raises(DatabaseUnavailableError):
                schema_revision.read_database_revision(engine)
    finally:
        engine.dispose()
    probe = [record for record in logs.records if record["logger"] == schema_revision.__name__]
    assert [record["level"] for record in probe] == [
        "ERROR",
        "DEBUG",
        "DEBUG",
        "DEBUG",
        "DEBUG",
        "ERROR",
    ]
    assert all("traceback" in record for record in probe if record["level"] == "ERROR")
    assert not [record for record in probe if record["level"] == "DEBUG" and "traceback" in record]
    assert {record["message"] for record in probe} == {
        "Database revision read failed: OperationalError"
    }


def test_request_id_reaches_records_of_a_sync_route() -> None:
    """LF-5: a record emitted in the worker thread of a sync route carries the request id."""
    app = FastAPI()
    application_logger = logging.getLogger("app.application.lf5")

    @app.get("/api/lf5")
    def lf5() -> dict[str, str]:
        application_logger.info("imported a Work Order file")
        return {"ok": "yes"}

    app.add_middleware(RequestLogMiddleware)
    with capture_json_logs() as logs, TestClient(app) as client:
        response = client.get("/api/lf5", headers={"X-Request-ID": "lf5-request"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "lf5-request"
    line = next(record for record in logs.records if record["logger"] == application_logger.name)
    assert line["request_id"] == "lf5-request"
    assert line["message"] == "imported a Work Order file"
