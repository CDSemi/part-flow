"""Integration tests for Phase 13 slice 11 — the Movement-history retention period.

Exercises the full request path — FastAPI routes, the policies service
and PostgreSQL — against a dedicated temporary database migrated to head
by the real Alembic chain (IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE
§21 "retention settings belong in Administration/configuration", §28 "the
retention period is configuration"; PLAN CD3; owner default OD-18):

- Administration → History archival & purge: ``GET`` / ``PUT
  /api/policies/data-retention`` (24 months since migration 0040 set it
  where it was never saved — Phase 16, history archival design B §3.1 —,
  whole months from the shortest allowed period, 12, to 1200 or ``null``,
  a strict integer in a partial merge of the section, audited as
  ``ApplicationPolicy`` / ``data-retention`` with the whole section as the
  snapshot, a no-op writes nothing — no UPDATE at all — and every refusal
  writes nothing); the section's other settings are
  ``tests/test_history_archive_api.py``;
- section isolation: the retention write never changes another policy
  section's values and vice versa, only the shared row ``updated_at``
  moves;
- concurrency: the write locks the singleton first and audits the
  committed predecessor re-read under the lock;
- no production reader and no side effect: only the policy model, service
  and API and the archival module (``app.application.history_archive``)
  mention the column, and saving it changes no production table.

The API commits real transactions; every policy section is restored
after each test and the module database is dropped afterwards. Helpers
are copied from the slice 9 module (each module owns its own).
"""

import datetime
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import policies
from app.core.config import get_settings
from app.main import create_app
from tests.auth_harness import admin_of

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APP_DIR = _BACKEND_DIR / "app"
_TEST_DATABASE = "partflow_test_retention_policy_api"
_DB_URL_ENV = "DATABASE_URL"
_POLICY_PATH = "/api/policies/data-retention"
_SESSIONS_POLICY_PATH = "/api/policies/worker-sessions"
_CORRECTION_POLICY_PATH = "/api/policies/correction-permissions"
_DUE_SOON_POLICY_PATH = "/api/policies/due-soon"
_OTHER_SECTION_PATHS = (_SESSIONS_POLICY_PATH, _CORRECTION_POLICY_PATH, _DUE_SOON_POLICY_PATH)
_SECTION = "data-retention"
_KEY = "retention_period_months"
#: The section's other settings at their defaults (migrations 0040 and 0041).
_OTHER_SETTINGS = {
    "shortest_retention_months": 12,
    "archive_max_wait_days": 7,
    "archive_start_minute": 20,
    "archive_preview_seconds": 90,
    "archive_purge_batch_movements": 50000,
    "archive_purge_batch_part_numbers": 20,
    # Automatic runs (history archival step 4, migration 0043).
    "archive_automatic_runs": False,
    "archive_measure": "LIVE_MOVEMENT_ROWS",
    "archive_limit_movement_rows": 5000000,
    "archive_limit_history_months": 1,
    "archive_limit_database_gib": 20,
    "archive_approval_required": True,
    "archive_interval_days": 7,
    # Viewing an archive (history archival step 5, migration 0044).
    "archive_view_days": 7,
    "archive_view_max_loaded": 1,
}

_E_T1 = (
    "The retention period must be a whole number of months from 12 to 1200, or no retention period."
)

_SESSION_DEFAULTS = {
    "worker_session_timeout_minutes": 15,
    "badge_confirm_done": True,
    "badge_confirm_queue": True,
    "badge_confirm_undo": True,
}
_CORRECTION_DEFAULTS = {"undo_reason_required": False}
_DUE_SOON_DEFAULTS = {
    "due_soon_min_days": 2,
    "due_soon_lead_time_percent": 15,
    "due_soon_max_days": 7,
}
_COUNTED_TABLES = (
    "part_movements",
    "quantity_flows",
    "worker_sessions",
    "work_order_allocations",
)


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
    admin_engine = create_engine(make_url(os.environ[_DB_URL_ENV]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    url = make_url(os.environ[_DB_URL_ENV]).set(database=_TEST_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    yield url
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    original_url = os.environ[_DB_URL_ENV]
    os.environ[_DB_URL_ENV] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ[_DB_URL_ENV] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def approved_policy(client: TestClient) -> Iterator[None]:
    """Every test leaves no retention period and every other section at its defaults."""
    yield
    _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: None}))
    _ok(admin_of(client).put(_SESSIONS_POLICY_PATH, json=_SESSION_DEFAULTS))
    _ok(admin_of(client).put(_CORRECTION_POLICY_PATH, json=_CORRECTION_DEFAULTS))
    _ok(admin_of(client).put(_DUE_SOON_POLICY_PATH, json=_DUE_SOON_DEFAULTS))


# ---------------------------------------------------------------------------
# Helpers (copied from the slice 9 / slice 4 modules)
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _values(body: dict[str, Any]) -> dict[str, Any]:
    """A policy section's value fields, without the shared row timestamp (and, for History
    archival & purge, the standing authorization it reports beside its settings)."""
    return {
        key: value
        for key, value in body.items()
        if key not in ("updated_at", "standing_authorization")
    }


def _stamp(body: dict[str, Any]) -> datetime.datetime:
    return datetime.datetime.fromisoformat(body["updated_at"])


def _policy_audits(engine: Engine, entity_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, entity_id, before_data, after_data, actor_reference"
                    " FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
                    " AND entity_id = :entity_id ORDER BY id"
                ),
                {"entity_id": entity_id},
            )
        )


def _audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one())


def _stored_row(engine: Engine) -> dict[str, Any]:
    """The whole singleton row, with its xmin (a changed xmin means an UPDATE ran)."""
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT xmin::text AS row_xmin, * FROM application_policy")
        ).one()
    return dict(row._mapping)


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED_TABLES
        }


def _start(request: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(request()))
    thread.start()
    return thread, results


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread, results: list[Any]) -> Any:
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(results) == 1
    return results[0]


def _release(client: TestClient) -> None:
    """Management releases a fresh PN into a fresh Area (no station)."""
    department = _ok(admin_of(client).post("/api/departments", json={"name": _unique("DEPT")}), 201)
    area = _ok(
        admin_of(client).post(
            "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
        ),
        201,
    )
    operation = _ok(
        admin_of(client).post(
            "/api/operations", json={"area_id": area["id"], "code": _unique("OP")}
        ),
        201,
    )
    pn = _unique("PN")
    work_order = _ok(
        admin_of(client).post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
        ),
        201,
    )
    _ok(
        admin_of(client).post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": pn,
                "quantity": 10,
                "route_mode": "FLOATING",
                "starting_area_id": area["id"],
                "operation_id": operation["id"],
                "confirm_active_quantity": False,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )


# ---------------------------------------------------------------------------
# T-1 … T-6 — the retention period section
# ---------------------------------------------------------------------------


def test_the_retention_period_starts_at_24_months(client: TestClient, db_engine: Engine) -> None:
    """Migration 0040 set the period that was never saved, and recorded it (design §3.1)."""
    policy = _ok(admin_of(client).get(_POLICY_PATH))
    assert set(policy) == {_KEY, *_OTHER_SETTINGS, "standing_authorization", "updated_at"}
    assert policy["standing_authorization"] is None
    assert {key: policy[key] for key in _OTHER_SETTINGS} == _OTHER_SETTINGS
    assert policy["updated_at"]
    [first, *_] = _policy_audits(db_engine, _SECTION)
    assert (first.before_data[_KEY], first.after_data[_KEY]) == (None, 24)


def _section(months: int | None) -> dict[str, Any]:
    """The audited section with this retention period and the other defaults."""
    return {_KEY: months, **_OTHER_SETTINGS}


def test_an_effective_put_is_stored_and_audited_once(client: TestClient, db_engine: Engine) -> None:
    before = len(_policy_audits(db_engine, _SECTION))
    assert _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 120}))[_KEY] == 120
    assert _ok(admin_of(client).get(_POLICY_PATH))[_KEY] == 120

    rows = _policy_audits(db_engine, _SECTION)
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == ("UPDATED", _SECTION, _section(None), _section(120), None)


def test_an_identical_put_writes_nothing(client: TestClient, db_engine: Engine) -> None:
    stored = _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 120}))
    row = _stored_row(db_engine)
    count = _audit_count(db_engine)

    again = _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 120}))
    assert again == stored
    assert _audit_count(db_engine) == count
    # No UPDATE at all: the row version and its timestamp are unchanged.
    assert _stored_row(db_engine) == row


def test_null_clears_the_period_as_an_audited_change(client: TestClient, db_engine: Engine) -> None:
    _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 120}))
    before = len(_policy_audits(db_engine, _SECTION))

    assert _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: None}))[_KEY] is None
    rows = _policy_audits(db_engine, _SECTION)
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == ("UPDATED", _SECTION, _section(120), _section(None), None)

    # Clearing again is a no-op.
    assert _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: None}))[_KEY] is None
    assert len(_policy_audits(db_engine, _SECTION)) == before + 1


@pytest.mark.parametrize("months", [12, 1200])
def test_the_range_boundaries_are_admitted(
    client: TestClient, db_engine: Engine, months: int
) -> None:
    before = len(_policy_audits(db_engine, _SECTION))
    assert _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: months}))[_KEY] == months
    rows = _policy_audits(db_engine, _SECTION)
    assert len(rows) == before + 1
    assert rows[-1].after_data == _section(months)


@pytest.mark.parametrize("months", [11, 1201, 0, -12])
def test_out_of_range_periods_are_refused_with_nothing_written(
    client: TestClient, db_engine: Engine, months: int
) -> None:
    _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 120}))
    row = _stored_row(db_engine)
    count = _audit_count(db_engine)

    response = admin_of(client).put(_POLICY_PATH, json={_KEY: months})
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": _E_T1}
    assert _stored_row(db_engine) == row
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize(
    "body",
    [{}, {_KEY: "120"}, {_KEY: 120.0}, {_KEY: True}, {_KEY: 120, "extra": 1}],
    ids=["missing-key", "string", "float", "bool", "extra-field"],
)
def test_malformed_bodies_are_refused_by_the_schema(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    row = _stored_row(db_engine)
    count = _audit_count(db_engine)
    assert admin_of(client).put(_POLICY_PATH, json=body).status_code == 422
    assert _stored_row(db_engine) == row
    assert _audit_count(db_engine) == count


# ---------------------------------------------------------------------------
# T-7 — section isolation
# ---------------------------------------------------------------------------


def test_policy_sections_never_touch_each_other(client: TestClient, db_engine: Engine) -> None:
    # (1) The retention write leaves every other section's values unchanged.
    others = {path: _values(_ok(admin_of(client).get(path))) for path in _OTHER_SECTION_PATHS}
    initial_stamp = _stamp(_ok(admin_of(client).get(_POLICY_PATH)))
    stored = _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 240}))
    assert {
        path: _values(_ok(admin_of(client).get(path))) for path in _OTHER_SECTION_PATHS
    } == others
    # The shared row timestamp moved, seen from both sections.
    assert _stamp(stored) > initial_stamp
    assert _stamp(_ok(admin_of(client).get(_SESSIONS_POLICY_PATH))) == _stamp(stored)

    # (2) Another section's write leaves the retention period unchanged.
    retention_audits = len(_policy_audits(db_engine, _SECTION))
    _ok(admin_of(client).put(_SESSIONS_POLICY_PATH, json={"worker_session_timeout_minutes": 30}))
    after = _ok(admin_of(client).get(_POLICY_PATH))
    assert _values(after) == _section(240)
    assert _stamp(after) > _stamp(stored)
    assert len(_policy_audits(db_engine, _SECTION)) == retention_audits
    # Each audit row carries only its own section's keys.
    assert set(_policy_audits(db_engine, _SECTION)[-1].after_data) == set(
        policies.HISTORY_ARCHIVE_COLUMNS
    )
    assert set(_policy_audits(db_engine, "worker-sessions")[-1].after_data) == set(
        _SESSION_DEFAULTS
    )


# ---------------------------------------------------------------------------
# T-8 — concurrency
# ---------------------------------------------------------------------------


def test_the_retention_write_locks_before_it_reads(client: TestClient, db_engine: Engine) -> None:
    before = len(_policy_audits(db_engine, _SECTION))
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(
            sa.text(
                "UPDATE application_policy"
                " SET worker_session_timeout_minutes = 30, retention_period_months = 24"
            )
        )
        thread, results = _start(lambda: admin_of(client).put(_POLICY_PATH, json={_KEY: 36}))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert _ok(response)[_KEY] == 36
    row = _stored_row(db_engine)
    assert (row["worker_session_timeout_minutes"], row[_KEY]) == (30, 36)
    rows = _policy_audits(db_engine, _SECTION)
    assert len(rows) == before + 1
    # The committed predecessor, re-read under the lock; only its own section.
    assert (rows[-1].before_data, rows[-1].after_data) == (_section(24), _section(36))


# ---------------------------------------------------------------------------
# T-9, T-10 — no reader, no side effect
# ---------------------------------------------------------------------------

#: The policy model, service and API that store the setting, and the one
#: module that reads it: the archival module (history archival design §9.2).
_RETENTION_READERS = {
    "app/infrastructure/models.py",
    "app/application/policies.py",
    "app/api/policies.py",
    "app/application/history_archive.py",
}


def test_no_production_path_reads_the_retention_period() -> None:
    """Only the policy model, service and API, and the archival module, mention the column."""
    mentions = {
        path.relative_to(_BACKEND_DIR).as_posix()
        for path in _APP_DIR.rglob("*.py")
        if _KEY in path.read_text(encoding="utf-8")
    }
    assert mentions == _RETENTION_READERS


def test_saving_the_period_changes_no_production_table(
    client: TestClient, db_engine: Engine
) -> None:
    _release(client)
    counts = _row_counts(db_engine)
    assert counts["part_movements"] > 0 and counts["quantity_flows"] > 0
    _ok(admin_of(client).put(_POLICY_PATH, json={_KEY: 12}))
    assert _row_counts(db_engine) == counts
