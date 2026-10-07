"""Tests for the backend command line (Phase 14 slice 1 — ``reset-password``).

``python -m app.cli reset-password --login-name NAME`` is recovery only
(slice 1 decision OD-S1-6): it gives a User that ALREADY has a password a
new temporary one, clears its lock and ends its sign-ins, only while an
administrator exists — so it can never create an administrator or race
first-run setup. The password comes from the prompt or one stdin line,
never an argument, and is never printed. Runs ``app.cli.main`` in
process against a dedicated temporary database migrated to head.
"""

import ast
import io
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.application import password_hashing
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_cli"
_PASSWORD = "original-password-123"
_RESET = "recovery-password-456"
_NO_ADMINISTRATOR = (
    "PartFlow has no administrator yet. Use Set up PartFlow with the setup token from the"
    " server log."
)
_NO_PASSWORD = (
    "This user has no password yet. An administrator gives it one with Set password… in"
    " Administration → Users."
)


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
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


@pytest.fixture(scope="module")
def db_engine(database_url: URL) -> Iterator[Engine]:
    engine = create_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def administrator(db_engine: Engine) -> int:
    """An active administrator with a password: recovery is available."""
    return _insert_user(db_engine, "Administrator", password=_PASSWORD)


@pytest.fixture(autouse=True)
def cli_database(monkeypatch: pytest.MonkeyPatch, database_url: URL) -> Iterator[None]:
    """Point Settings (re-read) at the module database for each run."""
    monkeypatch.setenv("DATABASE_URL", database_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _insert_user(
    engine: Engine,
    role: str,
    *,
    password: str | None = None,
    active: bool = True,
    locked: bool = False,
) -> int:
    with engine.begin() as connection:
        user_id = int(
            connection.execute(
                sa.text(
                    "INSERT INTO users (login_name, display_name, role_id, is_active)"
                    " SELECT :login, :name, id, :active FROM roles WHERE name = :role RETURNING id"
                ),
                {
                    "login": f"cli-{uuid.uuid4().hex[:10]}",
                    "name": f"Cli {uuid.uuid4().hex[:6]}",
                    "active": active,
                    "role": role,
                },
            ).scalar_one()
        )
        if password is not None:
            connection.execute(
                sa.text(
                    "INSERT INTO user_credentials (user_id, password_hash, password_is_temporary,"
                    " password_changed_at, failed_attempts, locked_until) VALUES (:id, :hash,"
                    " false, now(), :failed,"
                    " CASE WHEN :locked THEN now() + interval '1 hour' END)"
                ),
                {
                    "id": user_id,
                    "hash": password_hashing.hash_password(password),
                    "failed": 3 if locked else 0,
                    "locked": locked,
                },
            )
    return user_id


def _login(engine: Engine, user_id: int) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                sa.text("SELECT login_name FROM users WHERE id = :id"), {"id": user_id}
            ).scalar_one()
        )


def _rows(engine: Engine, sql: str, **params: object) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(sa.text(sql), params)]


def _run(monkeypatch: pytest.MonkeyPatch, login: str, password: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    return cli.main(["reset-password", "--login-name", login])


def _write_counts(engine: Engine) -> list[int]:
    """Credentials, sessions and audit rows — a refusal changes none of them."""
    with engine.connect() as connection:
        return [
            int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in ("user_credentials", "user_sessions", "audit_events")
        ]


def test_reset_password_resets_clears_the_lock_and_ends_sign_ins(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    db_engine: Engine,
    administrator: int,
) -> None:
    target = _insert_user(db_engine, "Operator", password=_PASSWORD, locked=True)
    with db_engine.begin() as connection:
        for _ in range(2):
            connection.execute(
                sa.text("INSERT INTO user_sessions (user_id, token_digest) VALUES (:id, :d)"),
                {"id": target, "d": os.urandom(32)},
            )
    assert _run(monkeypatch, _login(db_engine, target).upper(), _RESET) == 0
    out, err = capsys.readouterr()
    assert f"(user {target}) was reset" in out
    assert _RESET not in out + err and _PASSWORD not in out + err
    assert err == ""

    [credential] = _rows(db_engine, "SELECT * FROM user_credentials WHERE user_id = :id", id=target)
    assert credential["password_is_temporary"] is True
    assert (credential["failed_attempts"], credential["locked_until"]) == (0, None)
    assert password_hashing.verify_password(_RESET, credential["password_hash"])
    sessions = _rows(
        db_engine, "SELECT end_reason FROM user_sessions WHERE user_id = :id", id=target
    )
    assert [row["end_reason"] for row in sessions] == ["PASSWORD_RESET", "PASSWORD_RESET"]
    [audit] = _rows(
        db_engine,
        "SELECT * FROM audit_events WHERE entity_type = 'User' AND entity_id = :id",
        id=str(target),
    )
    assert audit["actor_user_id"] is None
    assert audit["metadata"] == {
        "password_change": "RECOVERY_CLI",
        "lock_cleared": True,
        "source": "cli",
    }


def test_reset_password_refusals(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    db_engine: Engine,
    administrator: int,
) -> None:
    target = _insert_user(db_engine, "Operator", password=_PASSWORD)
    before = _write_counts(db_engine)
    unknown = f"nobody-{uuid.uuid4().hex[:8]}"
    assert _run(monkeypatch, unknown, _RESET) == 1
    assert capsys.readouterr().err.strip() == f"No user has the login name {unknown}."
    assert _run(monkeypatch, _login(db_engine, target), "x" * 11) == 1
    assert capsys.readouterr().err.strip() == "A password must be at least 12 characters long."
    assert _write_counts(db_engine) == before

    # A User without a password — even in the Administrator role — is never given one.
    no_password = _insert_user(db_engine, "Administrator")
    before = _write_counts(db_engine)
    assert _run(monkeypatch, _login(db_engine, no_password), _RESET) == 1
    assert capsys.readouterr().err.strip() == _NO_PASSWORD
    assert _write_counts(db_engine) == before
    assert not _rows(
        db_engine, "SELECT 1 FROM user_credentials WHERE user_id = :id", id=no_password
    )


def test_reset_password_of_an_inactive_user(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    db_engine: Engine,
    administrator: int,
) -> None:
    target = _insert_user(db_engine, "Operator", password=_PASSWORD, active=False)
    assert _run(monkeypatch, _login(db_engine, target), _RESET) == 0
    out = capsys.readouterr().out
    assert "This user is inactive and cannot sign in until reactivated." in out


def test_reset_password_is_refused_while_setup_is_open(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    db_engine: Engine,
    administrator: int,
) -> None:
    target = _insert_user(db_engine, "Operator", password=_PASSWORD)
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE users SET is_active = false WHERE id = :id"), {"id": administrator}
        )
    try:
        before = _write_counts(db_engine)
        assert _run(monkeypatch, _login(db_engine, target), _RESET) == 1
        assert capsys.readouterr().err.strip() == _NO_ADMINISTRATOR
        assert _write_counts(db_engine) == before
        with TestClient(create_app()) as client:
            assert client.get("/api/setup").json()["open"] is True
    finally:
        with db_engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE users SET is_active = true WHERE id = :id"), {"id": administrator}
            )


def test_usage_and_unreachable_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as usage:
        cli.main([])
    assert usage.value.code == 2
    assert "required: command" in capsys.readouterr().err
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:secret-pw@127.0.0.1:1/none")
    get_settings.cache_clear()
    assert _run(monkeypatch, "anyone", _RESET) == 2
    out, err = capsys.readouterr()
    assert err.strip() == "PartFlow could not reach its database. Nothing was changed."
    assert "secret-pw" not in out + err and "127.0.0.1" not in out + err


def test_the_cli_reads_no_model() -> None:
    """B-STATIC: the CLI is a presentation adapter over authentication only."""
    tree = ast.parse((_BACKEND_DIR / "app" / "cli.py").read_text(encoding="utf-8"))
    application: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "app.infrastructure.models"
            assert not (
                node.module == "app.domain.enums"
                and "Permission" in {alias.name for alias in node.names}
            )
            if node.module == "app.application":
                application |= {alias.name for alias in node.names}
            elif node.module.startswith("app.application."):
                application.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Attribute):
            assert node.attr not in {"User", "Role", "RolePermission", "Permission"}
            assert node.attr not in {"UserCredential", "UserSession"}
    # `errors` is the shared refusal vocabulary the CLI renders.
    assert "authentication" in application
    assert application <= {"authentication", "errors", "reconciliation"}
