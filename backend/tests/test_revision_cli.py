"""Tests for ``python -m app.cli revision`` (Phase 16 slice 3: REV-1 … REV-9).

``revision`` is read-only: one READ ONLY transaction reports the release
identity, the expected (code head) and database revisions, the pending
and non-transactional revisions and the readiness the backend would
report under the configured override. Exit 0 ``current``, 1 any other
state, 2 could not run. Runs ``app.cli.main`` in process against
temporary databases (module-scoped: the command never writes), dropped
afterwards.
"""

import json
import os
import re
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.core.config import get_settings
from app.infrastructure import schema_revision

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_HEAD = schema_revision.code_head()
_PREVIOUS = "0031_phase14_beyond_demand"
_ALL = schema_revision.pending_revisions(None)


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _temporary_database(prepare: Callable[[URL], None]) -> Iterator[URL]:
    name = f"partflow_test_revision_{uuid.uuid4().hex[:10]}"
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    try:
        prepare(url)
        yield url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


def _unknown(url: URL) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE alembic_version (version_num varchar(32))"))
            connection.execute(sa.text("INSERT INTO alembic_version VALUES ('9999_unknown')"))
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def at_head() -> Iterator[URL]:
    yield from _temporary_database(lambda url: command.upgrade(_alembic_config(url), "head"))


@pytest.fixture(scope="module")
def at_previous() -> Iterator[URL]:
    yield from _temporary_database(lambda url: command.upgrade(_alembic_config(url), _PREVIOUS))


@pytest.fixture(scope="module")
def empty() -> Iterator[URL]:
    yield from _temporary_database(lambda url: None)


@pytest.fixture(scope="module")
def unknown() -> Iterator[URL]:
    yield from _temporary_database(_unknown)


@pytest.fixture
def run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[Callable[..., tuple[int, dict[str, Any], str]]]:
    def invoke(url: URL, **environment: str) -> tuple[int, dict[str, Any], str]:
        monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        code = cli.main(["revision"])
        out, err = capsys.readouterr()
        return code, json.loads(out), out + err

    yield invoke
    get_settings.cache_clear()


def test_at_head(at_head: URL, run: Callable[..., tuple[int, dict[str, Any], str]]) -> None:
    """REV-1, REV-8."""
    code, document, output = run(at_head)
    assert code == 0
    assert document == {
        "report_version": 1,
        "command": "revision",
        "state": "current",
        "exit_code": 0,
        "release": "development",
        "commit": None,
        "expected_revision": _HEAD,
        "database_revision": _HEAD,
        "accepted_revision": None,
        "override_ignored": False,
        "readiness": "current",
        "pending_revisions": [],
        "non_transactional_revisions": [],
        "error": None,
    }
    # REV-8: the lines the host scripts read with sed/grep.
    assert re.search(r'^  "state": "current",$', output, re.MULTILINE)
    assert re.search(r'^  "exit_code": 0,$', output, re.MULTILINE)


def test_one_revision_behind(
    at_previous: URL, run: Callable[..., tuple[int, dict[str, Any], str]]
) -> None:
    """REV-2."""
    code, document, _ = run(at_previous)
    assert code == 1
    assert document["state"] == "upgrade_available"
    assert document["pending_revisions"] == [_HEAD]
    assert document["non_transactional_revisions"] == []
    assert document["readiness"] == "mismatch"


def test_an_override_of_a_known_revision_is_ignored(
    at_previous: URL, run: Callable[..., tuple[int, dict[str, Any], str]]
) -> None:
    """REV-9."""
    code, document, _ = run(at_previous, ACCEPT_SCHEMA_REVISION=_PREVIOUS)
    assert code == 1
    assert document["accepted_revision"] == _PREVIOUS
    assert document["override_ignored"] is True
    assert document["readiness"] == "mismatch"


def test_an_accepted_unknown_revision(
    unknown: URL, run: Callable[..., tuple[int, dict[str, Any], str]]
) -> None:
    """REV-3, REV-5."""
    code, document, _ = run(unknown, ACCEPT_SCHEMA_REVISION="9999_unknown")
    assert code == 1
    assert document["state"] == "unknown_revision"
    assert document["readiness"] == "accepted"
    assert document["override_ignored"] is False
    code, document, _ = run(unknown, ACCEPT_SCHEMA_REVISION="")
    assert code == 1
    assert document["state"] == "unknown_revision"
    assert document["readiness"] == "mismatch"
    assert document["pending_revisions"] == []


def test_an_empty_database(empty: URL, run: Callable[..., tuple[int, dict[str, Any], str]]) -> None:
    """REV-4."""
    code, document, _ = run(empty)
    assert code == 1
    assert document["state"] == "upgrade_available"
    assert document["database_revision"] is None
    assert document["pending_revisions"] == _ALL
    assert document["readiness"] == "mismatch"


def test_a_non_transactional_pending_revision(
    at_previous: URL,
    run: Callable[..., tuple[int, dict[str, Any], str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REV-6."""
    monkeypatch.setattr(
        schema_revision,
        "read_revision_source",
        lambda path: "with op.get_context().autocommit_block(): pass",
    )
    code, document, _ = run(at_previous)
    assert code == 1
    assert document["state"] == "blocked_non_transactional"
    assert document["non_transactional_revisions"] == [_HEAD]


def test_the_transaction_is_read_only(
    at_head: URL,
    run: Callable[..., tuple[int, dict[str, Any], str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REV-7."""
    seen: list[tuple[str, ...]] = []
    real = schema_revision.read_revision

    def spy(connection: Connection) -> str | None:
        settings = ("transaction_read_only", "lock_timeout", "statement_timeout")
        seen.append(
            tuple(
                str(connection.execute(sa.text(f"SHOW {setting}")).scalar_one())
                for setting in settings
            )
        )
        return real(connection)

    monkeypatch.setattr(schema_revision, "read_revision", spy)
    assert run(at_head)[0] == 0
    assert seen == [("on", "5s", "30s")]


def test_an_unreachable_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 with ``state: null`` and the error."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:secret-pw@127.0.0.1:1/none")
    get_settings.cache_clear()
    try:
        assert cli.main(["revision"]) == 2
    finally:
        get_settings.cache_clear()
    out, err = capsys.readouterr()
    document = json.loads(out)
    assert document["state"] is None
    assert document["readiness"] is None
    assert document["exit_code"] == 2
    assert document["error"] == {
        "code": "database_unavailable",
        "message": "PartFlow could not reach its database.",
    }
    assert "secret-pw" not in out + err
