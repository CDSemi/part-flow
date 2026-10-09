"""``python -m app.cli status`` (Phase 16 slice 6: SC-1 … SC-17).

Runs against a dedicated temporary database migrated to head (in the
application-role test mode as the temporary application role) and
temporary backup directories built with the real ``backups.publish_backup``
(``tests.backup_harness``). ``collect_status(..., now=...)`` controls the
clock; the CLI runs in-process. Every lock-holding or lock-waiting helper
session uses ``conftest.owner_engine``. Nothing here writes PartFlow data;
the module database is dropped afterwards.
"""

import datetime
import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from alembic import command
from app import cli
from app.application import migration, system_status
from app.core.config import get_settings
from app.infrastructure import backup_files, schema_revision
from app.infrastructure.database import build_engine
from tests import backup_harness
from tests.conftest import owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_status_cli"
_HEAD = schema_revision.code_head()
_PREVIOUS = "0031_phase14_beyond_demand"
_UNKNOWN = "9999_unknown_to_this_image"
_NOW = datetime.datetime(2026, 10, 8, 12, 0, tzinfo=datetime.UTC)
_TOP_KEYS = [
    "report_version",
    "command",
    "result",
    "exit_code",
    "started_at",
    "finished_at",
    "duration_ms",
    "release",
    "database",
    "movements",
    "schema",
    "backup",
    "archival",
    "findings",
    "error",
]


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="module")
def database_url() -> Iterator[URL]:
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    url = make_url(os.environ["DATABASE_URL"]).set(database=_TEST_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    yield url
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture
def engine(database_url: URL) -> Iterator[Engine]:
    built = build_engine(
        database_url.render_as_string(hide_password=False),
        application_name=schema_revision.CLI_APPLICATION_NAME,
        connect_timeout=system_status.CONNECT_TIMEOUT_SECONDS,
    )
    yield built
    built.dispose()


@pytest.fixture
def cli_database(database_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The in-process CLI reads the module database."""
    monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _collect(engine: Engine | None, backup_dir: Path | None = None, **options: Any) -> Any:
    values: dict[str, Any] = {
        "release_tag": "v1.0.0-rc.1",
        "release_commit": None,
        "accepted_revision": None,
        "backup_dir": backup_dir,
        "now": lambda: _NOW,
    }
    values.update(options)
    return system_status.collect_status(engine, **values)


def _document(report: Any) -> dict[str, Any]:
    return system_status.status_document(report)


def _published(
    backup_dir: Path,
    completed: datetime.datetime,
    kind: str = "daily",
    label: str | None = None,
) -> str:
    """A published backup whose manifest says it completed at ``completed``."""
    name = backup_harness.name_of(completed - datetime.timedelta(minutes=5), kind, label)
    path = backup_harness.publish(backup_dir, name)

    def complete(manifest: dict[str, Any]) -> None:
        manifest["completed_at"] = completed.strftime("%Y-%m-%dT%H:%M:%SZ")

    backup_harness.reseal(path, complete)
    return name


def _run_cli(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, str, str]:
    code = cli.main(["status", *arguments])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _scalar(url: URL, statement: str) -> Any:
    engine = owner_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            return connection.execute(sa.text(statement)).scalar_one()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_status_ok(
    cli_database: None, database_url: URL, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SC-1 (+ SC-13, SC-16)."""
    now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    name = _published(tmp_path, now - datetime.timedelta(hours=3))
    code, out, err = _run_cli(capsys, "--backup-dir", str(tmp_path))
    assert code == 0, out + err
    assert '\n  "exit_code": 0,\n' in out
    document = json.loads(out)
    assert out == json.dumps(document, indent=2, ensure_ascii=True) + "\n"
    assert list(document) == _TOP_KEYS
    assert (document["result"], document["exit_code"], document["error"]) == ("ok", 0, None)
    assert document["command"] == "status" and document["report_version"] == 1
    database = document["database"]
    assert list(database) == [
        "status",
        "name",
        "server_version",
        "connected_role",
        "size_bytes",
        "connections",
        "locks",
    ]
    assert database["status"] == "ok" and database["name"] == _TEST_DATABASE
    assert database["size_bytes"] > 0 and database["connections"] >= 1
    assert list(database["locks"]) == ["waiting", "longest_wait_seconds"]
    rows = _scalar(database_url, "SELECT count(*) FROM part_movements")
    assert document["movements"]["status"] == "ok"
    assert document["movements"]["rows"] == rows
    assert document["movements"]["total_bytes"] > 0
    assert document["schema"] == {
        "status": "ok",
        "readiness": "current",
        "expected_revision": _HEAD,
        "database_revision": _HEAD,
        "accepted_revision": None,
    }
    backup = document["backup"]
    assert backup["status"] == "ok" and backup["directory"] == str(tmp_path)
    assert backup["max_age_hours"] == 26 and backup["candidates"] == 1
    assert backup["latest"]["name"] == name and backup["latest"]["kind"] == "daily"
    assert backup["latest"]["age_hours"] == 3.0
    assert backup["latest"]["release_tag"] == backup_harness.RELEASE
    assert backup["latest"]["alembic_revision"] == _HEAD
    assert backup["latest"]["dump_bytes"] == len(backup_harness.DUMP)
    assert backup["latest_daily"]["name"] == name
    assert document["archival"] == {
        "status": "not_applicable",
        "reason": "Archival proposals arrive with P16-S10.",
        "awaiting_approval": None,
    }
    assert document["findings"] == []
    # SC-13: one JSON document; no DSN, password, path content or exception text.
    assert err.startswith("status: ok (database ") and err.count("\n") == 1
    url = make_url(os.environ["DATABASE_URL"])
    for text in (out, err):
        assert "postgresql" not in text
        if url.password:
            assert str(url.password) not in text
        assert "Traceback" not in text


def test_stale_backup(engine: Engine, tmp_path: Path) -> None:
    """SC-2."""
    name = _published(tmp_path, _NOW - datetime.timedelta(hours=27))
    report = _collect(engine, tmp_path)
    document = _document(report)
    assert (report.exit_code, document["result"]) == (1, "attention")
    assert document["findings"] == [
        {
            "code": "backup_stale",
            "message": (
                f"The newest backup {name} completed 27.0 hours ago; the limit is 26 hours."
            ),
        }
    ]
    assert document["backup"]["status"] == "finding"


def test_no_published_backup(engine: Engine, tmp_path: Path) -> None:
    """SC-3."""
    report = _collect(engine, tmp_path)
    assert [finding.code for finding in report.findings] == ["backup_missing"]
    assert report.findings[0].message == f"No published backup was found in {tmp_path}."

    (tmp_path / ".partial" / "x").mkdir(parents=True)
    (tmp_path / "notes").mkdir()
    lock = tmp_path / ".backup.lock"
    lock.mkdir()
    (lock / "owner").write_text(
        "host=nas01\npid=42\nstarted_at=2026-10-08T02:00:00Z\nby=backup.sh\n"
        "name=20261008T020000Z-daily\n",
        encoding="utf-8",
    )
    document = _document(_collect(engine, tmp_path))
    assert [finding["code"] for finding in document["findings"]] == ["backup_missing"]
    assert document["backup"]["candidates"] == 0
    assert document["backup"]["lock"] == {
        "present": True,
        "by": "backup.sh",
        "started_at": "2026-10-08T02:00:00Z",
        "name": "20261008T020000Z-daily",
    }


def test_without_backup_directory(engine: Engine) -> None:
    """SC-4."""
    report = _collect(engine, None)
    assert report.exit_code == 0
    backup = _document(report)["backup"]
    assert backup["status"] == "not_applicable"
    assert backup["directory"] is None and backup["latest"] is None


def test_unreadable_backup_directory(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-5."""

    def refuse(path: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    # The directory listing (os.scandir) is refused, as for another account's 0700 directory.
    monkeypatch.setattr(backup_files, "entry_names", refuse)
    report = _collect(engine, tmp_path)
    assert report.exit_code == 1
    assert [finding.code for finding in report.findings] == ["backup_dir_unreadable"]
    assert report.findings[0].message == (
        f"The backup directory {tmp_path} cannot be read (Permission denied). Run status as the"
        ' account that owns it (docker compose run --user "$(id -u):$(id -g)").'
    )


def test_corrupt_newest_manifest(engine: Engine, tmp_path: Path) -> None:
    """SC-6."""
    older = _published(tmp_path, _NOW - datetime.timedelta(hours=5))
    newest = _published(tmp_path, _NOW - datetime.timedelta(hours=1))
    (tmp_path / newest / "manifest.json").write_text("{not json", encoding="utf-8")
    document = _document(_collect(engine, tmp_path))
    assert document["findings"] == [
        {
            "code": "backup_manifest_unreadable",
            "message": f"The backup {newest} has no valid manifest; it was not counted.",
        }
    ]
    assert document["backup"]["latest"]["name"] == older
    assert document["backup"]["latest"]["age_hours"] == 5.0


def test_pre_release_backup_is_the_newest(engine: Engine, tmp_path: Path) -> None:
    """SC-7."""
    daily = _published(tmp_path, _NOW - datetime.timedelta(hours=10))
    pre_release = _published(
        tmp_path, _NOW - datetime.timedelta(hours=2), "pre-release", "v1.0.0-rc.2"
    )
    backup = _document(_collect(engine, tmp_path))["backup"]
    assert backup["latest"]["name"] == pre_release
    assert backup["latest"]["kind"] == "pre-release"
    assert backup["latest_daily"] == {
        "name": daily,
        "completed_at": (_NOW - datetime.timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_hours": 10.0,
    }


def test_completion_time_in_the_future(engine: Engine, tmp_path: Path) -> None:
    """SC-8."""
    future = _NOW + datetime.timedelta(minutes=10)
    name = _published(tmp_path, future)
    document = _document(_collect(engine, tmp_path))
    assert document["findings"] == [
        {
            "code": "backup_time_in_future",
            "message": (
                f"The backup {name} records a completion time in the future"
                f" ({future.strftime('%Y-%m-%dT%H:%M:%SZ')}); check the host clock."
            ),
        }
    ]
    assert document["backup"]["latest"]["age_hours"] == 0


@pytest.mark.parametrize(
    ("database_revision", "accepted", "readiness"),
    [
        (_PREVIOUS, None, "mismatch"),
        (_UNKNOWN, _UNKNOWN, "accepted"),
        (_PREVIOUS, _PREVIOUS, "mismatch"),
    ],
)
def test_schema_readiness(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    database_revision: str,
    accepted: str | None,
    readiness: str,
) -> None:
    """SC-9 (+ SC-9b: the same answer as the ``revision`` command)."""
    monkeypatch.setattr(schema_revision, "read_revision", lambda connection: database_revision)
    report = _collect(engine, None, accepted_revision=accepted)
    document = _document(report)
    assert document["schema"]["readiness"] == readiness
    assert document["schema"]["database_revision"] == database_revision
    assert document["schema"]["accepted_revision"] == accepted
    codes = [finding["code"] for finding in document["findings"]]
    if readiness == "mismatch":
        assert codes == ["schema_not_ready"] and report.exit_code == 1
        assert document["schema"]["status"] == "finding"
        assert document["findings"][0]["message"] == (
            f"The database is at revision {database_revision} but release v1.0.0-rc.1 expects"
            f" {_HEAD}: PartFlow refuses changes until the release is completed or rolled back."
        )
    else:
        assert codes == [] and report.exit_code == 0
    revision = migration.revision_report(
        engine, release=None, commit=None, accepted_revision=accepted
    )
    assert revision.readiness == readiness


def test_unreachable_database_still_reports_the_backups(tmp_path: Path) -> None:
    """SC-10."""
    _published(tmp_path, _NOW - datetime.timedelta(hours=1))
    unreachable = build_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/partflow",
        application_name=schema_revision.CLI_APPLICATION_NAME,
        connect_timeout=system_status.CONNECT_TIMEOUT_SECONDS,
    )
    try:
        report = _collect(unreachable, tmp_path)
    finally:
        unreachable.dispose()
    document = _document(report)
    assert (report.exit_code, document["result"]) == (2, "error")
    assert document["error"] == {
        "code": "database_unavailable",
        "message": "The PartFlow database could not be reached.",
    }
    assert document["database"]["status"] == "error" and document["database"]["name"] is None
    assert document["movements"]["status"] == "not_run"
    assert document["schema"]["status"] == "not_run"
    assert document["backup"]["status"] == "ok"
    assert system_status.status_summary(report) == "status: ERROR — database_unavailable"


def test_read_only_transaction_and_statement_order(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-11."""
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    settings: dict[str, str] = {}
    real_read_revision = schema_revision.read_revision

    def spy(connection: Connection) -> str | None:
        for name in ("transaction_read_only", "lock_timeout", "statement_timeout"):
            settings[name] = str(connection.execute(sa.text(f"SHOW {name}")).scalar_one())
        return real_read_revision(connection)

    monkeypatch.setattr(schema_revision, "read_revision", spy)
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert _collect(engine, None).exit_code == 0
        assert settings == {
            "transaction_read_only": "on",
            "lock_timeout": "5s",
            "statement_timeout": "20s",
        }
        _collect(engine, None, statement_timeout_seconds=7)
        assert settings["statement_timeout"] == "7s"
    finally:
        event.remove(engine, "before_cursor_execute", record)
    touching = [index for index, text in enumerate(statements) if "part_movements" in text]
    assert touching
    for index in touching:
        assert statements[index] == system_status.MOVEMENTS_SQL
        following = [text for text in statements[index + 1 :] if not text.startswith("SHOW")]
        assert not following or following[0].startswith("SET TRANSACTION READ ONLY")


def test_the_cli_bounds_connecting(
    cli_database: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SC-11: ``connect_timeout`` 10 s and the ``partflow-cli`` session name."""
    seen: dict[str, Any] = {}
    real_build_engine = build_engine

    def capture(url: str, **options: Any) -> Engine:
        seen.update(options)
        return real_build_engine(url, **options)

    monkeypatch.setattr(cli, "build_engine", capture)
    code, _, _ = _run_cli(capsys)
    assert code == 0
    assert seen == {"application_name": "partflow-cli", "connect_timeout": 10}


def _hold_lock(url: URL, statement: str, ready: threading.Event, release: threading.Event) -> None:
    engine = owner_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(sa.text(statement))
            ready.set()
            release.wait(60)
    finally:
        engine.dispose()


def test_lock_timeout(engine: Engine, database_url: URL) -> None:
    """SC-12."""
    ready, release = threading.Event(), threading.Event()
    holder = threading.Thread(
        target=_hold_lock,
        args=(database_url, "LOCK TABLE part_movements IN ACCESS EXCLUSIVE MODE", ready, release),
    )
    holder.start()
    try:
        assert ready.wait(30)
        started = time.monotonic()
        report = _collect(engine, None)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(60)
    document = _document(report)
    assert report.exit_code == 2
    assert document["error"] == {
        "code": "lock_timeout",
        "message": (
            "The status query waited more than 5 s for a table lock. A migration may be running."
        ),
    }
    assert 4 <= elapsed < 15
    assert document["schema"]["status"] == "ok"
    assert document["movements"]["status"] == "error"


def test_lock_waits_are_reported(engine: Engine, database_url: URL) -> None:
    """SC-12b."""
    ready, release = threading.Event(), threading.Event()
    holder = threading.Thread(
        target=_hold_lock,
        args=(database_url, "LOCK TABLE areas IN ACCESS EXCLUSIVE MODE", ready, release),
    )
    waiter_done = threading.Event()

    def wait_for_areas() -> None:
        waiter = owner_engine(database_url, poolclass=NullPool)
        try:
            with waiter.connect() as connection, connection.begin():
                connection.execute(sa.text("SET LOCAL lock_timeout = '60s'"))
                connection.execute(sa.text("LOCK TABLE areas IN ACCESS SHARE MODE"))
        finally:
            waiter.dispose()
            waiter_done.set()

    holder.start()
    assert ready.wait(30)
    waiter = threading.Thread(target=wait_for_areas)
    waiter.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            waiting = _scalar(database_url, "SELECT count(*) FROM pg_locks WHERE NOT granted")
            if waiting:
                break
            time.sleep(0.1)
        time.sleep(0.5)
        report = _collect(engine, None)
    finally:
        release.set()
        holder.join(60)
        waiter.join(60)
    assert waiter_done.is_set()
    locks = _document(report)["database"]["locks"]
    assert report.exit_code == 0
    assert locks["waiting"] >= 1 and locks["longest_wait_seconds"] > 0


def _status_holds_part_movements(url: URL) -> bool:
    return bool(
        _scalar(
            url,
            "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid"
            " WHERE a.application_name = 'partflow-cli' AND l.granted"
            " AND l.relation = 'part_movements'::regclass",
        )
    )


def _lock_after_status(
    engine: Engine, database_url: URL, statement_timeout: int
) -> tuple[Any, float, float]:
    """Run status (slow last statement) and request ACCESS EXCLUSIVE while it holds the table."""
    results: list[Any] = []
    runner = threading.Thread(
        target=lambda: results.append(
            _collect(engine, None, statement_timeout_seconds=statement_timeout)
        )
    )
    runner.start()
    deadline = time.monotonic() + 30
    while not _status_holds_part_movements(database_url):
        assert time.monotonic() < deadline, "status never locked part_movements"
        time.sleep(0.05)
    locker = owner_engine(database_url, poolclass=NullPool)
    try:
        with locker.connect() as connection, connection.begin():
            connection.execute(sa.text("SET LOCAL lock_timeout = '30s'"))
            asked = time.monotonic()
            connection.execute(sa.text("LOCK TABLE part_movements IN ACCESS EXCLUSIVE MODE"))
            granted = time.monotonic()
    finally:
        locker.dispose()
    runner.join(60)
    return results[0], asked, granted


def test_status_delays_but_never_aborts_a_migration_lock(
    engine: Engine, database_url: URL, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-17."""
    monkeypatch.setattr(
        system_status,
        "MOVEMENTS_SQL",
        "SELECT (SELECT count(*) FROM part_movements),"
        " pg_total_relation_size('part_movements'), pg_sleep(3)",
    )
    report, asked, granted = _lock_after_status(engine, database_url, 20)
    assert report.exit_code == 0
    assert granted - asked < 25

    report, asked, granted = _lock_after_status(engine, database_url, 1)
    assert report.exit_code == 2
    assert _document(report)["error"] == {
        "code": "statement_timeout",
        "message": "The status query exceeded the statement timeout of 1 s.",
    }
    assert granted - asked < 5


def test_application_role(
    cli_database: None, capsys: pytest.CaptureFixture[str], database_url: URL
) -> None:
    """SC-14: the role of the connection, equal to what reconcile reports."""
    code, out, err = _run_cli(capsys)
    assert code == 0, out + err
    role = json.loads(out)["database"]["connected_role"]
    assert cli.main(["reconcile", "--check", "a"]) == 0
    reconciled = json.loads(capsys.readouterr().out)
    assert role == reconciled["database"]["connected_role"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--max-backup-age-hours", "0"],
        ["--max-backup-age-hours", "721"],
        ["--statement-timeout", "0"],
        ["--statement-timeout", "601"],
    ],
)
def test_usage_errors(capsys: pytest.CaptureFixture[str], arguments: list[str]) -> None:
    """SC-15."""
    with pytest.raises(SystemExit) as raised:
        cli.main(["status", *arguments])
    assert raised.value.code == 2
    assert capsys.readouterr().out == ""


def test_configuration_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused configuration still reports the backups; the run cannot complete."""
    monkeypatch.setenv("DATABASE_URL", "not a url")
    get_settings.cache_clear()
    try:
        code, out, err = _run_cli(capsys, "--backup-dir", str(tmp_path))
    finally:
        get_settings.cache_clear()
    document = json.loads(out)
    assert code == 2 and document["error"]["code"] == "configuration_invalid"
    assert document["backup"]["status"] == "finding"
    assert err == "status: ERROR — configuration_invalid\n"


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI-1."""
    with pytest.raises(SystemExit) as raised:
        cli.main(["status", "--help"])
    assert raised.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert (
        "Report database size, Movement count, schema readiness and backup age as JSON. Never"
        " changes data." in out
    )
    assert "keep below 30 so a release's migrate is never aborted by this run" in out
