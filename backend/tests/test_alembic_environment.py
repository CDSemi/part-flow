"""Tests for the Alembic migration environment (``alembic/env.py``).

``env.py`` reads DATABASE_URL from the application settings when the
caller has not pre-set ``sqlalchemy.url``. Alembic's Config is a
ConfigParser with interpolation, so a URL whose user or password is
percent-encoded (the Deployment Admin encodes it, e.g. ``%40`` for
``@``) must still migrate: online and offline both read the value back
unescaped, and the engine authenticates with the decoded credentials.

The online run goes through ``alembic upgrade head`` in a subprocess
with only DATABASE_URL set — exactly the deployment migration step —
against a dedicated temporary database that is dropped afterwards.
"""

import io
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.core.config import get_settings

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_alembic_env"


def _percent_encode_all(value: str) -> str:
    """Percent-encode every byte, so the URL is guaranteed to contain '%'."""
    return "".join(f"%{byte:02X}" for byte in value.encode())


def _encoded_url(url: URL) -> str:
    """Render ``url`` with a fully percent-encoded user and password."""
    assert url.username is not None and url.password is not None
    host = f"{url.host}:{url.port}" if url.port else str(url.host)
    return (
        f"{url.drivername}://{_percent_encode_all(url.username)}"
        f":{_percent_encode_all(str(url.password))}@{host}/{url.database}"
    )


def _script_directory() -> ScriptDirectory:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(config)


def _head_revision() -> str:
    head = _script_directory().get_current_head()
    assert head is not None
    return head


@pytest.fixture(scope="module")
def database_url() -> Iterator[URL]:
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    yield make_url(os.environ["DATABASE_URL"]).set(database=_TEST_DATABASE)
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


def test_encoded_url_decodes_to_the_same_credentials(database_url: URL) -> None:
    encoded = _encoded_url(database_url)
    assert "%" in encoded
    decoded = make_url(encoded)
    assert decoded.username == database_url.username
    assert decoded.password == database_url.password
    assert decoded.database == _TEST_DATABASE


def test_upgrade_head_online_accepts_a_percent_encoded_database_url(database_url: URL) -> None:
    encoded = _encoded_url(database_url)
    environment = {**os.environ, "DATABASE_URL": encoded}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr.replace(encoded, "<DATABASE_URL>")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            revision = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()
    assert revision == _head_revision()


def test_upgrade_offline_reads_the_url_back_unescaped(
    monkeypatch: pytest.MonkeyPatch, database_url: URL
) -> None:
    # Offline SQL generation covers only the schema-only revisions: later
    # revisions validate existing rows through a live connection. The
    # first revision runs env.py's offline branch end to end.
    base = _script_directory().get_base()
    assert base is not None
    encoded = _encoded_url(database_url)
    monkeypatch.setenv("DATABASE_URL", encoded)
    get_settings.cache_clear()
    try:
        output = io.StringIO()
        config = Config(str(_BACKEND_DIR / "alembic.ini"), output_buffer=output)
        config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
        assert not config.get_main_option("sqlalchemy.url")

        command.upgrade(config, base, sql=True)

        assert config.get_main_option("sqlalchemy.url") == encoded
        assert base in output.getvalue()
    finally:
        get_settings.cache_clear()
