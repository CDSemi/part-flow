"""Alembic glue for release readiness and ``migrate`` (Phase 16 slice 3).

Infrastructure only: where this image's migration scripts are, which
revision is their single head, which revisions they know, which are
pending above a database revision, whether a pending revision cannot run
inside one transaction, the database revision itself, and the Alembic
upgrade on a caller-owned connection. No readiness rule lives here
(``app.application.readiness``).
"""

import logging
import re
from pathlib import Path
from typing import Final

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from alembic import command
from app.infrastructure.database import DatabaseUnavailableError

logger = logging.getLogger(__name__)

# The application_name of the API's and the CLI's database sessions
# (``build_engine``); the API's equals the health answer's ``service``.
API_APPLICATION_NAME: Final = "partflow-api"
CLI_APPLICATION_NAME: Final = "partflow-cli"

_BACKEND_DIR: Final = Path(__file__).resolve().parents[2]
ALEMBIC_DIR: Final = _BACKEND_DIR / "alembic"

# A statement that cannot run inside the migrate transaction.
_NON_TRANSACTIONAL: Final = re.compile(r"\bautocommit_block\b|\b(?i:CONCURRENTLY)\b")


class MigrationScriptsError(Exception):
    """This image's migration scripts cannot be read (or have no single head).

    The message names the cause without any path beyond ``alembic/``.
    """


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc).replace(str(ALEMBIC_DIR), 'alembic')}"


def script_directory() -> ScriptDirectory:
    return ScriptDirectory(str(ALEMBIC_DIR))


def code_head() -> str:
    """The single Alembic head this image ships."""
    try:
        head = script_directory().get_current_head()
    except Exception as exc:
        # Any failure to load the scripts (a missing directory, multiple
        # heads, a broken revision file) makes the image unusable.
        raise MigrationScriptsError(_reason(exc)) from exc
    if head is None:
        raise MigrationScriptsError("CommandError: no revision found in alembic/versions")
    return head


def known_revisions() -> frozenset[str]:
    """Every revision id of this image's script directory."""
    try:
        return frozenset(script.revision for script in script_directory().walk_revisions())
    except Exception as exc:
        raise MigrationScriptsError(_reason(exc)) from exc


def pending_revisions(current: str | None) -> list[str]:
    """Revisions above ``current`` (exclusive) up to the head, in upgrade order.

    ``current`` must be a known revision or None (an empty database).
    """
    scripts = script_directory().iterate_revisions("heads", current or "base")
    return [script.revision for script in reversed(list(scripts))]


def read_revision_source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def non_transactional_revisions(revisions: list[str]) -> list[str]:
    """The given revisions whose source needs autocommit or CONCURRENTLY."""
    scripts = script_directory()
    found: list[str] = []
    for revision in revisions:
        script = scripts.get_revision(revision)
        if script is not None and _NON_TRANSACTIONAL.search(read_revision_source(script.path)):
            found.append(revision)
    return found


def read_revision(connection: Connection) -> str | None:
    """The database revision: None without the table or a row; several rows joined by ','."""
    if connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar() is None:
        return None
    rows = connection.execute(
        text("SELECT version_num FROM alembic_version ORDER BY version_num")
    ).scalars()
    revisions = [str(row) for row in rows]
    return ",".join(revisions) if revisions else None


def read_database_revision(engine: Engine) -> str | None:
    """``read_revision`` on a pooled connection; also the health connectivity proof."""
    try:
        with engine.connect() as connection:
            return read_revision(connection)
    except SQLAlchemyError as exc:
        # The URL is not logged because it contains credentials.
        logger.error("Database revision read failed: %s", type(exc).__name__, exc_info=exc)
        raise DatabaseUnavailableError() from exc


def upgrade_to_head(connection: Connection) -> None:
    """Run every pending revision inside ``connection``'s open transaction.

    ``alembic/env.py`` adopts the connection (``config.attributes``):
    Alembic uses the caller's transaction and never commits it.
    """
    # No alembic.ini: the script location is all env.py needs here (the
    # application package is already importable; no logging configuration).
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
