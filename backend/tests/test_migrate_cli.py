"""Tests for ``python -m app.cli migrate`` (Phase 16 slice 3: M-1 … M-16).

``migrate`` applies this release's pending Alembic revisions on one
connection in one transaction, records the backup reference (or the
reason there is none) and prints exactly one JSON document on stdout.
Every case runs ``app.cli.main`` in process (M-16 as a subprocess)
against its own temporary database created on the development cluster
and dropped afterwards; ``DATABASE_URL`` points at it.
"""

import json
import os
import re
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from alembic import command
from app import cli
from app.application import migration
from app.core.config import get_settings
from app.infrastructure import schema_revision

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_HEAD = schema_revision.code_head()
_PREVIOUS = "0031_phase14_beyond_demand"
_ALL = schema_revision.pending_revisions(None)
_KEYS = [
    "report_version",
    "command",
    "result",
    "exit_code",
    "started_at",
    "finished_at",
    "duration_ms",
    "release",
    "commit",
    "expected_revision",
    "revision_before",
    "revision_after",
    "applied_revisions",
    "backup",
    "grants",
    "error",
]
_GRANTS = {
    "status": "not_applicable",
    "detail": "Database-role hardening is not installed yet (P16-S4).",
}
_NOTHING_CHANGED = "Nothing was changed."


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    """An empty temporary database; Settings (re-read) point at it."""
    name = f"partflow_test_migrate_{uuid.uuid4().hex[:10]}"
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = make_url(os.environ["DATABASE_URL"]).set(database=name)
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        yield url
    finally:
        get_settings.cache_clear()
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin_engine.dispose()


def _scalar(url: URL, sql: str) -> Any:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(sa.text(sql)).scalar()
    finally:
        engine.dispose()


def _revision(url: URL) -> str | None:
    if _scalar(url, "SELECT to_regclass('public.alembic_version')") is None:
        return None
    return _scalar(url, "SELECT version_num FROM alembic_version")  # type: ignore[no-any-return]


def _migrate(
    capsys: pytest.CaptureFixture[str], *arguments: str
) -> tuple[int, dict[str, Any], str]:
    code = cli.main(["migrate", *arguments])
    out, err = capsys.readouterr()
    # Exactly one document, printed with indent=2: the pinned exit_code line (M-10).
    assert out.splitlines().count(f'  "exit_code": {code},') == 1, out
    assert out.startswith("{\n") and out.endswith("\n}\n")
    return code, json.loads(out), err


def _set_unknown_revision(url: URL, revision: str = "zzzz_unknown") -> None:
    """An ``alembic_version`` table at a revision this release does not know."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
            )
            connection.execute(sa.text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
    finally:
        engine.dispose()


def _refused(document: dict[str, Any], code: str) -> None:
    assert document["result"] == "refused"
    assert document["exit_code"] == 1
    assert document["error"]["code"] == code
    assert document["revision_after"] == document["revision_before"]
    assert document["applied_revisions"] == []
    assert document["grants"] is None


def test_first_install_then_rerun(database: URL, capsys: pytest.CaptureFixture[str]) -> None:
    """M-1, M-2, M-10."""
    code, document, err = _migrate(capsys, "--no-backup-reason", "  first install  ")
    assert code == 0, err
    assert list(document) == _KEYS
    assert document["result"] == "upgraded"
    assert document["exit_code"] == 0
    assert document["release"] == "development"
    assert document["commit"] is None
    assert document["expected_revision"] == _HEAD
    assert document["revision_before"] is None
    assert document["revision_after"] == _HEAD
    assert document["applied_revisions"] == _ALL
    assert document["backup"] == {"kind": "none", "reason": "first install"}
    assert document["grants"] == _GRANTS
    assert document["error"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", document["started_at"])
    assert f"migrate: upgraded empty database -> {_HEAD} ({len(_ALL)} revisions) in" in err
    assert f"Running upgrade  -> {_ALL[0]}" in err
    assert _revision(database) == _HEAD

    # M-10: no URL, password or path.
    password = make_url(os.environ["DATABASE_URL"]).password
    for text in (json.dumps(document), err):
        assert "postgresql" not in text
        assert str(_BACKEND_DIR) not in text
        assert password is None or password not in text

    # M-2: rerun — nothing applied, the grants hook still runs.
    code, document, err = _migrate(capsys, "--pre-release-backup", "dump-20261008.sql.gz")
    assert code == 0, err
    assert document["result"] == "already_current"
    assert document["revision_before"] == document["revision_after"] == _HEAD
    assert document["applied_revisions"] == []
    assert document["grants"] == _GRANTS
    assert document["backup"] == {
        "kind": "reference",
        "reference": "dump-20261008.sql.gz",
        "verified": False,
        "verification": "pending: backup-verify arrives with P16-S5",
    }
    assert f"migrate: already at {_HEAD}; nothing to apply (" in err


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--pre-release-backup", "a", "--no-backup-reason", "b"],
        ["--no-backup-reason", ""],
        ["--no-backup-reason", "   "],
        ["--no-backup-reason", "line\nbreak"],
        ["--no-backup-reason", "x" * 501],
        ["--no-backup-reason", "x", "--lock-timeout", "0"],
    ],
)
def test_usage_errors_print_no_document(
    database: URL, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    """M-3."""
    with pytest.raises(SystemExit) as usage:
        cli.main(["migrate", *arguments])
    assert usage.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err
    assert _revision(database) is None


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (RuntimeError("hook failed"), "internal_error"),
        (ProgrammingError("GRANT", {}, Exception("permission denied")), "migration_failed"),
    ],
)
def test_a_failure_after_the_upgrade_rolls_everything_back(
    database: URL,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    code: str,
) -> None:
    """M-4: one transaction — not even alembic_version survives."""

    def failing_hook(connection: Connection) -> dict[str, str]:
        raise failure

    monkeypatch.setattr(migration, "apply_grants", failing_hook)
    exit_code, document, err = _migrate(capsys, "--no-backup-reason", "first install")
    assert exit_code == 2
    assert document["result"] == "failed"
    assert document["error"]["code"] == code
    assert document["error"]["message"] == migration.MIGRATE_MESSAGES[code]
    assert document["applied_revisions"] == []
    assert document["revision_after"] is None
    assert "Traceback" in err
    assert _scalar(database, "SELECT to_regclass('public.alembic_version')") is None
    assert _scalar(database, "SELECT to_regclass('public.part_movements')") is None


def _at_previous(url: URL) -> None:
    command.upgrade(_alembic_config(url), _PREVIOUS)


def test_a_connected_backend_refuses_then_the_migration_applies(
    database: URL, capsys: pytest.CaptureFixture[str]
) -> None:
    """M-5, M-6."""
    _at_previous(database)
    api = create_engine(database, connect_args={"application_name": "partflow-api"})
    try:
        with api.connect():
            code, document, _ = _migrate(capsys, "--no-backup-reason", "test")
        assert code == 1
        _refused(document, "backend_connected")
        assert document["error"]["message"] == migration.MIGRATE_MESSAGES["backend_connected"]
        assert document["revision_before"] == _PREVIOUS
        assert _revision(database) == _PREVIOUS
    finally:
        api.dispose()
    code, document, err = _migrate(capsys, "--no-backup-reason", "test")
    assert code == 0, err
    assert document["result"] == "upgraded"
    assert document["applied_revisions"] == [_HEAD]
    assert f"migrate: upgraded {_PREVIOUS} -> {_HEAD} (1 revisions) in" in err
    assert _revision(database) == _HEAD


def test_a_concurrent_migrate_refuses(database: URL, capsys: pytest.CaptureFixture[str]) -> None:
    """M-7."""
    other = create_engine(database)
    try:
        with other.connect() as connection, connection.begin():
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended('partflow:migrate', 0))")
            )
            code, document, _ = _migrate(capsys, "--no-backup-reason", "test")
    finally:
        other.dispose()
    assert code == 1
    _refused(document, "migrate_running")
    assert document["error"]["message"] == (
        "Another migrate is running on this database. Nothing was changed."
    )
    assert _revision(database) is None


def test_an_unknown_database_revision_refuses(
    database: URL, capsys: pytest.CaptureFixture[str]
) -> None:
    """M-8."""
    _set_unknown_revision(database)
    code, document, _ = _migrate(capsys, "--no-backup-reason", "test")
    assert code == 1
    _refused(document, "revision_unknown")
    assert document["revision_before"] == "zzzz_unknown"
    assert document["error"]["message"] == (
        "The database is at revision zzzz_unknown, which this release does not know (it is"
        " newer, or from another branch). Nothing was changed. Use the release that created"
        " it, or follow the rollback decision tree."
    )
    assert _revision(database) == "zzzz_unknown"


def test_a_non_transactional_revision_refuses(
    database: URL, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-9."""
    real = schema_revision.read_revision_source

    def source(path: str) -> str:
        if Path(path).name.endswith(f"{_HEAD[:4]}_phase14_route_adjusted.py"):
            return 'op.execute("CREATE INDEX CONCURRENTLY ix ON t (c)")'
        return real(path)

    monkeypatch.setattr(schema_revision, "read_revision_source", source)
    code, document, _ = _migrate(capsys, "--no-backup-reason", "test")
    assert code == 1
    _refused(document, "non_transactional_migration")
    assert document["error"]["message"] == (
        f"Revision {_HEAD} contains a statement that cannot run inside the migrate transaction"
        " (autocommit_block or CONCURRENTLY). It needs its own documented procedure. Nothing"
        " was changed."
    )
    assert _revision(database) is None


def test_unreachable_database_and_missing_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """M-11."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:secret-pw@127.0.0.1:1/none")
    get_settings.cache_clear()
    try:
        code, document, err = _migrate(capsys, "--no-backup-reason", "test")
        assert code == 2
        assert document["result"] == "failed"
        assert document["error"] == {
            "code": "database_unavailable",
            "message": "PartFlow could not reach its database. Nothing was changed.",
        }
        assert document["expected_revision"] == _HEAD
        assert "secret-pw" not in json.dumps(document) + err
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DATABASE_URL")
        get_settings.cache_clear()
        code, document, err = _migrate(capsys, "--no-backup-reason", "test")
        assert code == 2
        assert document["error"] == {
            "code": "configuration_invalid",
            "message": (
                "PartFlow is not configured: check DATABASE_URL, or DATABASE_HOST,"
                " DATABASE_NAME, DATABASE_USER and DATABASE_PASSWORD_FILE. Nothing was changed."
            ),
        }
        assert document["release"] is None
        assert document["grants"] is None
    finally:
        get_settings.cache_clear()


def test_the_lock_timeout_applies_to_the_whole_transaction(
    database: URL, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-12 (on an empty database the hook runs after the full upgrade)."""
    seen: list[str] = []

    def capture(connection: Connection) -> dict[str, str]:
        seen.append(str(connection.execute(sa.text("SHOW lock_timeout")).scalar_one()))
        return {"status": "not_applicable", "detail": "captured"}

    monkeypatch.setattr(migration, "apply_grants", capture)
    assert _migrate(capsys, "--no-backup-reason", "test")[0] == 0
    assert _migrate(capsys, "--no-backup-reason", "test", "--lock-timeout", "5")[0] == 0
    assert seen == ["30s", "5s"]


def test_a_commit_failure_is_an_unknown_outcome(
    database: URL, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-13: never "Nothing was changed"."""

    def answer_lost(transaction: Any) -> None:
        raise OperationalError("COMMIT", {}, Exception("server closed the connection"))

    monkeypatch.setattr(migration, "commit_transaction", answer_lost)
    code, document, err = _migrate(capsys, "--no-backup-reason", "test")
    assert code == 2
    assert document["result"] == "outcome_unknown"
    assert document["error"] == {
        "code": "outcome_unknown",
        "message": (
            "The database connection failed while committing the migration: it may or may not"
            ' have been applied. Run "python -m app.cli revision" before doing anything else.'
        ),
    }
    assert _NOTHING_CHANGED not in json.dumps(document) + err


def test_a_held_table_lock_times_out(database: URL, capsys: pytest.CaptureFixture[str]) -> None:
    """M-14: 0032 alters audit_events; a reconciliation's ACCESS SHARE lock blocks it."""
    _at_previous(database)
    other = create_engine(database)
    try:
        with other.connect() as connection, connection.begin():
            connection.execute(sa.text("LOCK TABLE audit_events IN ACCESS SHARE MODE"))
            code, document, _ = _migrate(
                capsys, "--no-backup-reason", "test", "--lock-timeout", "1"
            )
    finally:
        other.dispose()
    assert code == 2
    assert document["result"] == "failed"
    assert document["error"]["code"] == "lock_not_available"
    assert document["error"]["message"] == migration.MIGRATE_MESSAGES["lock_not_available"]
    assert _revision(database) == _PREVIOUS


def test_no_existing_revision_is_non_transactional() -> None:
    """M-15."""
    assert schema_revision.non_transactional_revisions(_ALL) == []
    for path in (_BACKEND_DIR / "alembic" / "versions").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\bautocommit_block\b", source), path.name
        assert not re.search(r"\bCONCURRENTLY\b", source, re.IGNORECASE), path.name


def test_migrate_with_the_file_based_configuration(tmp_path: Path) -> None:
    """M-16: the production configuration form as a subprocess."""
    suffix = uuid.uuid4().hex[:10]
    role = f"partflow_test_m16_role_{suffix}"
    name = f"partflow_test_m16_db_{suffix}"
    password = "pa%ss@w:rd/1"
    admin_url = make_url(os.environ["DATABASE_URL"])
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    password_file = tmp_path / "postgres_password"
    password_file.write_text(password + "\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("DATABASE_")
    }
    environment.update(
        DATABASE_HOST=str(admin_url.host),
        DATABASE_PORT=str(admin_url.port or 5432),
        DATABASE_NAME=name,
        DATABASE_USER=role,
        DATABASE_PASSWORD_FILE=str(password_file),
    )
    try:
        with admin_engine.connect() as connection:
            literal = connection.execute(sa.text("SELECT quote_literal(:p)"), {"p": password})
            connection.execute(
                sa.text(f'CREATE ROLE "{role}" LOGIN PASSWORD {literal.scalar_one()}')
            )
            connection.execute(sa.text(f'CREATE DATABASE "{name}" OWNER "{role}"'))
        result = subprocess.run(
            [sys.executable, "-m", "app.cli", "migrate", "--no-backup-reason", "ae"],
            cwd=_BACKEND_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, result.stderr.replace(password, "<PASSWORD>")
        document = json.loads(result.stdout)
        assert document["result"] == "upgraded"
        assert document["revision_after"] == _HEAD
        assert password not in result.stdout + result.stderr
    finally:
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        admin_engine.dispose()
