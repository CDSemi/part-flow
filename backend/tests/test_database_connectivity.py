"""Real PostgreSQL connectivity test.

Runs SELECT 1 against the Compose development database configured through
DATABASE_URL. It performs no writes and touches no domain data.
"""

from app.core.config import get_settings
from app.infrastructure.database import build_engine, ping_database


def test_select_1_against_configured_database() -> None:
    engine = build_engine(get_settings().database_url)
    try:
        ping_database(engine)
    finally:
        engine.dispose()
