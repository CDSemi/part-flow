"""Shared pytest configuration.

Provides the development-database default for DATABASE_URL so the suite
runs outside Docker (IDE, plain `uv run pytest` on the host) without
extra environment setup. The default matches `.env.example` and the port
published by the Compose db service. Inside Docker Compose and CI the
variable is already set, so the default is ignored.
"""

import os

# Must run before any test module imports app.core.config.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://partflow_user:partflow_dev_password@localhost:5432/partflow",
)
