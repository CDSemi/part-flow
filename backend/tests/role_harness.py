"""Temporary database roles and ACL snapshots for the database-role tests (Phase 16 slice 4).

Every case uses uniquely named temporary roles (``partflow_app_t<hex>``,
``partflow_maint_t<hex>``, ``partflow_owner_t…``/``partflow_login_t…``/
``partflow_foreign_t…``) on the test cluster, only on ``partflow_test_*``
databases, and drops them afterwards (R16-85). The production names are
never created.
"""

import secrets
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.application.database_roles import DatabaseRoles
from tests.conftest import drop_temporary_roles, owner_engine


def temporary_roles() -> DatabaseRoles:
    suffix = uuid.uuid4().hex[:8]
    return DatabaseRoles(f"partflow_app_t{suffix}", f"partflow_maint_t{suffix}")


def temporary_name(kind: str) -> str:
    """``partflow_<kind>_t<hex>`` (kind: owner, login, foreign)."""
    return f"partflow_{kind}_t{uuid.uuid4().hex[:8]}"


def new_password() -> str:
    """32 URL-safe characters: within the 16-128 printable ASCII rule."""
    return secrets.token_urlsafe(24)


def write_password_files(directory: Path, passwords: DatabaseRoles) -> DatabaseRoles:
    """The two password files (as paths, in a DatabaseRoles pair)."""
    directory.mkdir(parents=True, exist_ok=True)
    app_file = directory / "partflow_app_password"
    maintenance_file = directory / "partflow_maintenance_password"
    app_file.write_text(passwords.app + "\n", encoding="utf-8")
    maintenance_file.write_text(passwords.maintenance + "\n", encoding="utf-8")
    return DatabaseRoles(str(app_file), str(maintenance_file))


def cluster_engine(url: URL) -> Engine:
    """AUTOCOMMIT owner connections (role DDL, CREATE/DROP DATABASE)."""
    return owner_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)


def drop_roles(cluster_url: URL, names: Iterable[str]) -> None:
    """``DROP OWNED BY`` wherever the roles have entries, then ``DROP ROLE``."""
    drop_temporary_roles(cluster_url, tuple(names))


def acl_snapshot(connection: Connection) -> dict[str, Any]:
    """Every ACL ``apply-grants`` touches, comparable across runs."""
    return {
        "relacl": sorted(
            (str(name), str(acl))
            for name, acl in connection.execute(
                sa.text(
                    "SELECT c.relname, coalesce(c.relacl::text, '') FROM pg_class c"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                    " AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')"
                )
            )
        ),
        "attacl": sorted(
            (str(name), str(column), str(acl))
            for name, column, acl in connection.execute(
                sa.text(
                    "SELECT c.relname, a.attname, a.attacl::text FROM pg_attribute a"
                    " JOIN pg_class c ON c.oid = a.attrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
                    " AND a.attacl IS NOT NULL"
                )
            )
        ),
        "nspacl": connection.execute(
            sa.text("SELECT nspacl::text FROM pg_namespace WHERE nspname = 'public'")
        ).scalar(),
        "datacl": connection.execute(
            sa.text("SELECT datacl::text FROM pg_database WHERE datname = current_database()")
        ).scalar(),
        "default_acl": sorted(
            (str(row[0]), str(row[1]), str(row[2]), str(row[3]))
            for row in connection.execute(
                sa.text(
                    "SELECT defaclrole::regrole::text, defaclnamespace, defaclobjtype,"
                    " defaclacl::text FROM pg_default_acl"
                )
            )
        ),
    }
