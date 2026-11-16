"""Integration tests for the Phase 17 migrations.

Runs the real Alembic migration chain against isolated, temporary
PostgreSQL databases (created and dropped by the fixtures), then
verifies what `0033_phase17_super_user_role` (IMPLEMENTATION_ROADMAP
Phase 17 item 3; PROJECT_PROFILE §20) and `0034_phase17_unique_names`
(item 4; PROJECT_PROFILE §8.4, §8.8) do:

- exact head boundary: `0034_phase17_unique_names` is the single head
  (the earlier cases run against their own revision), and the models
  match the schema migrated to it;
- 0033: the migration's permission literal is exactly the 0028
  vocabulary;
- a fresh install seeds `Super User` with every key of
  `app.domain.enums.Permission` and no audit row, and still at head: a
  later revision that adds a key must grant it to every role that holds
  every other key, and this case fails until it does;
- a role already named Super User in any letter case is completed, not
  duplicated: one role, same id and name, every key, one `Role`
  `UPDATED` audit row without an actor and with the migration's
  metadata; the exactly cased name is chosen over another case, and a
  role that already holds every key is left untouched;
- 0033: the downgrade changes nothing, and upgrading again adds nothing;
- 0034: an Area name is unique within its Department (inactive Areas
  included) and a name among the active Planned Routes (an archived
  route keeps its name); the duplicates already stored are listed by
  `UPGRADE_BLOCKERS_SQL`, block the upgrade (the revision stays at 0033)
  and appear as `blocked_by_data` in `revision` and as a `migrate`
  refusal; the downgrade drops the constraint and the index.
"""

import datetime
import importlib.util
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.application import migration
from app.domain.enums import Permission
from app.infrastructure import models, schema_revision

# Schema and seed assertions: owner connections in the application-role
# test mode (Phase 16 slice 4).
pytestmark = pytest.mark.database_owner

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_PREVIOUS_REVISION = "0032_phase14_route_adjusted"
_SUPER_USER_REVISION = "0033_phase17_super_user_role"
_UNIQUE_NAMES_REVISION = "0034_phase17_unique_names"
_HEAD_REVISION = _UNIQUE_NAMES_REVISION
_VERSIONS_DIR = _BACKEND_DIR / "alembic" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "20261114_0033_phase17_super_user_role.py"
_UNIQUE_NAMES_MIGRATION_FILE = _VERSIONS_DIR / "20261114_0034_phase17_unique_names.py"
_USERS_ROLES_MIGRATION_FILE = _VERSIONS_DIR / "20261006_0028_phase13_users_roles.py"
_TEMPLATE_DATABASE = "partflow_test_phase17_template"
_SUPER_USER_TEMPLATE_DATABASE = "partflow_test_phase17_super_user_template"
_ROLE_NAME = "Super User"
_EVERY_KEY = {permission.value for permission in Permission}
_METADATA = {"source": "migration", "revision": _SUPER_USER_REVISION}


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _create_temp_database(admin_engine: Engine, name: str, template: str | None = None) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        suffix = f' TEMPLATE "{template}"' if template else ""
        connection.execute(sa.text(f'CREATE DATABASE "{name}"{suffix}'))


def _drop_temp_database(admin_engine: Engine, name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _url(name: str) -> URL:
    return make_url(os.environ["DATABASE_URL"]).set(database=name)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version(connection: Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _grants(connection: Connection) -> dict[str, set[str]]:
    """Every role by name with its granted keys."""
    grants: dict[str, set[str]] = {}
    for name, permission in connection.execute(
        sa.text(
            "SELECT r.name, p.permission FROM roles r"
            " LEFT JOIN role_permissions p ON p.role_id = r.id"
        )
    ):
        keys = grants.setdefault(str(name), set())
        if permission is not None:
            keys.add(str(permission))
    return grants


def _role_audit(connection: Connection) -> list[dict[str, Any]]:
    return [
        dict(row._mapping)
        for row in connection.execute(
            sa.text(
                "SELECT event_type, entity_id, actor_user_id, before_data, after_data, metadata"
                " FROM audit_events WHERE entity_type = 'Role' ORDER BY id"
            )
        )
    ]


def _insert_role(connection: Connection, name: str, keys: list[str]) -> int:
    role_id = int(
        connection.execute(
            sa.text("INSERT INTO roles (name) VALUES (:name) RETURNING id"), {"name": name}
        ).scalar_one()
    )
    connection.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id, permission)"
            " SELECT :role_id, unnest(CAST(:keys AS text[]))"
        ),
        {"role_id": role_id, "keys": keys},
    )
    return role_id


def _updated_at(connection: Connection, role_id: int) -> datetime.datetime:
    value = connection.execute(
        sa.text("SELECT updated_at FROM roles WHERE id = :id"), {"id": role_id}
    ).scalar_one()
    assert isinstance(value, datetime.datetime)
    return value


@pytest.fixture(scope="module")
def admin_engine() -> Iterator[Engine]:
    engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def head_engine(admin_engine: Engine) -> Iterator[Engine]:
    """A fresh install: a new database migrated to head."""
    name = "partflow_test_phase17_schema"
    _create_temp_database(admin_engine, name)
    command.upgrade(_alembic_config(_url(name)), "head")
    engine = create_engine(_url(name))
    yield engine
    engine.dispose()
    _drop_temp_database(admin_engine, name)


@pytest.fixture(scope="module")
def previous_template(admin_engine: Engine) -> Iterator[str]:
    """A clean database at 0032, cloned (CREATE DATABASE … TEMPLATE) per case."""
    _create_temp_database(admin_engine, _TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_TEMPLATE_DATABASE)), _PREVIOUS_REVISION)
    yield _TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _TEMPLATE_DATABASE)


@pytest.fixture
def previous_database(admin_engine: Engine, previous_template: str) -> Iterator[URL]:
    name = "partflow_test_phase17_case"
    _create_temp_database(admin_engine, name, template=previous_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


@pytest.fixture(scope="module")
def super_user_template(admin_engine: Engine) -> Iterator[str]:
    """A clean database at 0033, cloned per case."""
    _create_temp_database(admin_engine, _SUPER_USER_TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(_url(_SUPER_USER_TEMPLATE_DATABASE)), _SUPER_USER_REVISION)
    yield _SUPER_USER_TEMPLATE_DATABASE
    _drop_temp_database(admin_engine, _SUPER_USER_TEMPLATE_DATABASE)


@pytest.fixture
def super_user_database(admin_engine: Engine, super_user_template: str) -> Iterator[URL]:
    name = "partflow_test_phase17_names_case"
    _create_temp_database(admin_engine, name, template=super_user_template)
    yield _url(name)
    _drop_temp_database(admin_engine, name)


# ---------------------------------------------------------------------------
# Head and literals
# ---------------------------------------------------------------------------


def test_alembic_has_a_single_head() -> None:
    config = _alembic_config(make_url(os.environ["DATABASE_URL"]))
    assert ScriptDirectory.from_config(config).get_heads() == [_HEAD_REVISION]


def test_models_metadata_matches_the_migrated_schema(head_engine: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with head_engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diffs = compare_metadata(context, models.Base.metadata)
    assert diffs == []


def test_the_permission_literal_is_the_0028_vocabulary() -> None:
    super_user = _load(_MIGRATION_FILE, "phase17_super_user_migration")
    users_roles = _load(_USERS_ROLES_MIGRATION_FILE, "phase13_users_roles_migration")
    assert super_user._PERMISSIONS == users_roles._PERMISSIONS
    assert super_user._ROLE_NAME == _ROLE_NAME
    assert super_user._AUDIT_METADATA == _METADATA


# ---------------------------------------------------------------------------
# A fresh install
# ---------------------------------------------------------------------------


def test_a_fresh_install_seeds_super_user_with_every_key(head_engine: Engine) -> None:
    """The fourth seeded role holds every permission key, without an audit
    row — and still does at head. A later revision that adds a key must
    grant it, in the same transaction, to every role that holds every
    other key (PROJECT_PROFILE §20); until it does, this case fails."""
    with head_engine.connect() as connection:
        assert _version(connection) == _HEAD_REVISION
        grants = _grants(connection)
        assert set(grants) == {"Administrator", "Manager", "Operator", _ROLE_NAME}
        assert grants[_ROLE_NAME] == _EVERY_KEY
        assert _role_audit(connection) == []


# ---------------------------------------------------------------------------
# An existing role is completed, not duplicated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [_ROLE_NAME, "super user"])
def test_an_existing_super_user_role_is_completed_not_duplicated(
    previous_database: URL, name: str
) -> None:
    held = ["MANAGE_WORK_ORDERS", "VIEW_PRODUCTION_DATA"]
    engine = create_engine(previous_database)
    try:
        with engine.begin() as connection:
            role_id = _insert_role(connection, name, held)
            before = _updated_at(connection, role_id)
        command.upgrade(_alembic_config(previous_database), _SUPER_USER_REVISION)
        with engine.connect() as connection:
            grants = _grants(connection)
            assert [role for role in grants if role.lower() == "super user"] == [name]
            assert grants[name] == _EVERY_KEY
            assert (
                connection.execute(
                    sa.text("SELECT id FROM roles WHERE name = :name"), {"name": name}
                ).scalar_one()
                == role_id
            )
            assert _updated_at(connection, role_id) > before
            assert _role_audit(connection) == [
                {
                    "event_type": "UPDATED",
                    "entity_id": str(role_id),
                    "actor_user_id": None,
                    "before_data": {"name": name, "permissions": sorted(held)},
                    "after_data": {"name": name, "permissions": sorted(_EVERY_KEY)},
                    "metadata": _METADATA,
                }
            ]
    finally:
        engine.dispose()


def test_the_exactly_cased_name_is_chosen_over_another_case(previous_database: URL) -> None:
    engine = create_engine(previous_database)
    try:
        with engine.begin() as connection:
            other_id = _insert_role(connection, "SUPER USER", ["EXPORT_REPORTS"])
            role_id = _insert_role(connection, _ROLE_NAME, ["EXPORT_REPORTS"])
        assert other_id < role_id
        command.upgrade(_alembic_config(previous_database), _SUPER_USER_REVISION)
        with engine.connect() as connection:
            grants = _grants(connection)
            assert grants[_ROLE_NAME] == _EVERY_KEY
            assert grants["SUPER USER"] == {"EXPORT_REPORTS"}
            assert [row["entity_id"] for row in _role_audit(connection)] == [str(role_id)]
    finally:
        engine.dispose()


def test_a_role_that_holds_every_key_is_left_untouched(previous_database: URL) -> None:
    engine = create_engine(previous_database)
    try:
        with engine.begin() as connection:
            role_id = _insert_role(connection, _ROLE_NAME, sorted(_EVERY_KEY))
            before = _updated_at(connection, role_id)
        command.upgrade(_alembic_config(previous_database), _SUPER_USER_REVISION)
        with engine.connect() as connection:
            assert _grants(connection)[_ROLE_NAME] == _EVERY_KEY
            assert _updated_at(connection, role_id) == before
            assert _role_audit(connection) == []
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Downgrade and upgrade again
# ---------------------------------------------------------------------------


def test_the_downgrade_keeps_the_role_and_upgrading_again_adds_nothing(
    previous_database: URL,
) -> None:
    config = _alembic_config(previous_database)
    engine = create_engine(previous_database)
    try:
        command.upgrade(config, _SUPER_USER_REVISION)
        command.downgrade(config, _PREVIOUS_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _PREVIOUS_REVISION
            assert _grants(connection)[_ROLE_NAME] == _EVERY_KEY
        command.upgrade(config, _SUPER_USER_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _SUPER_USER_REVISION
            grants = _grants(connection)
            assert [role for role in grants if role.lower() == "super user"] == [_ROLE_NAME]
            assert grants[_ROLE_NAME] == _EVERY_KEY
            assert _role_audit(connection) == []
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 0034 — unique Area names per Department, unique active Planned Route names
# ---------------------------------------------------------------------------


def _insert_department(connection: Connection, name: str) -> int:
    return int(
        connection.execute(
            sa.text("INSERT INTO departments (name) VALUES (:name) RETURNING id"), {"name": name}
        ).scalar_one()
    )


def _insert_area(
    connection: Connection, department_id: int, name: str, *, active: bool = True
) -> int:
    return int(
        connection.execute(
            sa.text(
                "INSERT INTO areas (department_id, name, is_active)"
                " VALUES (:department_id, :name, :active) RETURNING id"
            ),
            {"department_id": department_id, "name": name, "active": active},
        ).scalar_one()
    )


def _insert_route(connection: Connection, name: str, *, archived: bool = False) -> int:
    return int(
        connection.execute(
            sa.text(
                "INSERT INTO route_templates (name, archived_at)"
                " VALUES (:name, CASE WHEN :archived THEN now() END) RETURNING id"
            ),
            {"name": name, "archived": archived},
        ).scalar_one()
    )


def _seed_duplicates(url: URL) -> dict[str, int]:
    """Two Areas named Booth 1 in Plating (one inactive) and one in Paint;
    two active Planned Routes named Standard and an archived third."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            plating = _insert_department(connection, "Plating")
            paint = _insert_department(connection, "Paint")
            return {
                "plating": plating,
                "paint": paint,
                "booth": _insert_area(connection, plating, "Booth 1"),
                "booth_inactive": _insert_area(connection, plating, "Booth 1", active=False),
                "booth_paint": _insert_area(connection, paint, "Booth 1"),
                "standard": _insert_route(connection, "Standard"),
                "standard_again": _insert_route(connection, "Standard"),
                "standard_archived": _insert_route(connection, "Standard", archived=True),
            }
    finally:
        engine.dispose()


def _expected_blockers(ids: dict[str, int]) -> list[str]:
    return [
        f"{_UNIQUE_NAMES_REVISION}: 2 active Planned Routes are named 'Standard'"
        f" (ids {ids['standard']}, {ids['standard_again']}); rename all but one.",
        f"{_UNIQUE_NAMES_REVISION}: Department 'Plating' has 2 Areas named 'Booth 1'"
        f" (Area ids {ids['booth']}, {ids['booth_inactive']}); rename all but one.",
    ]


def _refused_by(connection: Connection, constraint: str, statement: Callable[[], object]) -> None:
    """Run ``statement`` inside a savepoint; it must fail on ``constraint``."""
    savepoint = connection.begin_nested()
    with pytest.raises(IntegrityError) as raised:
        statement()
    savepoint.rollback()
    assert constraint in str(raised.value.orig)


def test_the_constraint_and_the_partial_index_at_head(head_engine: Engine) -> None:
    inspector = inspect(head_engine)
    constraints = {
        str(constraint["name"]): constraint["column_names"]
        for constraint in inspector.get_unique_constraints("areas")
    }
    assert constraints["uq_areas_department_id_name"] == ["department_id", "name"]
    indexes = {str(index["name"]): index for index in inspector.get_indexes("route_templates")}
    index = indexes["uq_route_templates_name_active"]
    assert index["unique"] is True
    assert index["column_names"] == ["name"]
    assert index["dialect_options"]["postgresql_where"] == "(archived_at IS NULL)"


def test_stored_duplicates_are_listed_and_block_the_upgrade(super_user_database: URL) -> None:
    """An inactive Area counts, an Area of another Department does not, and
    an archived Planned Route does not count. The upgrade refuses and the
    database stays at 0033."""
    expected = _expected_blockers(_seed_duplicates(super_user_database))
    engine = create_engine(super_user_database)
    try:
        with engine.connect() as connection:
            assert schema_revision.data_blockers(connection, [_UNIQUE_NAMES_REVISION]) == expected
            assert schema_revision.data_blockers(connection, [_SUPER_USER_REVISION]) == []
        with pytest.raises(RuntimeError, match="Rename the duplicate names in PartFlow first"):
            command.upgrade(_alembic_config(super_user_database), _UNIQUE_NAMES_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _SUPER_USER_REVISION
    finally:
        engine.dispose()


def test_revision_reports_the_duplicates_and_migrate_refuses(super_user_database: URL) -> None:
    expected = _expected_blockers(_seed_duplicates(super_user_database))
    engine = create_engine(super_user_database)
    try:
        report = migration.revision_report(
            engine, release=None, commit=None, accepted_revision=None
        )
        assert (report.state, report.exit_code) == ("blocked_by_data", 1)
        assert report.pending_revisions == [_UNIQUE_NAMES_REVISION]
        assert report.blocking_data == expected
        assert migration.revision_document(report)["blocking_data"] == expected
        assert migration.revision_summary(report).endswith(
            "; fix in PartFlow first: " + "; ".join(expected)
        )
        refused = migration.run_migrate(
            engine,
            backup=migration.Backup(no_backup_reason="test"),
            lock_timeout_seconds=5,
            release=None,
            commit=None,
        )
        assert (refused.result, refused.exit_code) == ("refused", 1)
        assert refused.error is not None
        assert refused.error.code == "blocked_by_data"
        assert refused.error.message == migration.MIGRATE_MESSAGES["blocked_by_data"].format(
            blockers="; ".join(expected)
        )
        with engine.connect() as connection:
            assert _version(connection) == _SUPER_USER_REVISION
    finally:
        engine.dispose()


def test_after_renaming_the_upgrade_enforces_both_names(super_user_database: URL) -> None:
    ids = _seed_duplicates(super_user_database)
    engine = create_engine(super_user_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE areas SET name = 'Booth 1 (old)' WHERE id = :id"),
                {"id": ids["booth_inactive"]},
            )
            connection.execute(
                sa.text("UPDATE route_templates SET name = 'Standard 2' WHERE id = :id"),
                {"id": ids["standard_again"]},
            )
        command.upgrade(_alembic_config(super_user_database), _UNIQUE_NAMES_REVISION)
        area_constraint = "uq_areas_department_id_name"
        route_index = "uq_route_templates_name_active"
        with engine.connect() as connection:
            assert _version(connection) == _UNIQUE_NAMES_REVISION
            _refused_by(
                connection,
                area_constraint,
                lambda: _insert_area(connection, ids["plating"], "Booth 1"),
            )
            _refused_by(
                connection,
                area_constraint,
                lambda: _insert_area(connection, ids["plating"], "Booth 1 (old)", active=False),
            )
            _refused_by(connection, route_index, lambda: _insert_route(connection, "Standard"))
            # Still allowed: another Department, and another archived route.
            _insert_area(connection, ids["paint"], "Booth 2")
            _insert_route(connection, "Standard", archived=True)
            connection.rollback()
    finally:
        engine.dispose()


def test_the_downgrade_drops_the_constraint_and_the_index(super_user_database: URL) -> None:
    config = _alembic_config(super_user_database)
    engine = create_engine(super_user_database)
    try:
        command.upgrade(config, _UNIQUE_NAMES_REVISION)
        command.downgrade(config, _SUPER_USER_REVISION)
        inspector = inspect(engine)
        assert "uq_areas_department_id_name" not in {
            str(constraint["name"]) for constraint in inspector.get_unique_constraints("areas")
        }
        assert "uq_route_templates_name_active" not in {
            str(index["name"]) for index in inspector.get_indexes("route_templates")
        }
        command.upgrade(config, _UNIQUE_NAMES_REVISION)
        with engine.connect() as connection:
            assert _version(connection) == _UNIQUE_NAMES_REVISION
    finally:
        engine.dispose()
