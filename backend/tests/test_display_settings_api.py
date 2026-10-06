"""Integration tests for Phase 13 slice 9 — Department display settings and Due Soon.

Exercises the full request path — FastAPI routes, the environment and
policies services and PostgreSQL — against a dedicated temporary
database migrated to head by the real Alembic chain
(IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §21 "configuration per
Department … never global"; GUI_DESIGN §3 rule 12, §5, §9; PLAN CD3;
owner default OD-5):

- Administration → Settings → Due Soon warning: ``GET`` / ``PUT
  /api/policies/due-soon`` (defaults 2 / 15 / 7, a full replace of three
  strict integers, the ranges and the minimum ≤ maximum rule with the
  first failure reported, audited as ``ApplicationPolicy`` /
  ``due-soon``, a no-op writes nothing, the other policy sections never
  touched and vice versa);
- Department display settings: the Production Board rotation timing on
  the Department (create takes the defaults 3 / 6 and refuses them as
  extra fields, the PATCH merges either field, refuses out-of-range,
  ``null`` and non-integer values with nothing written, an inactive
  Department is editable, every effective change is one ``Department``
  ``UPDATED`` row with the four-key snapshot);
- the Production Board feed carries each Department's own values and a
  change from the next read on;
- concurrency: the policy writers serialize on the singleton row lock
  and merge, the Due Soon write locks before it reads its own columns
  and judges the no-op on the locked row, a Department PATCH waits for
  a concurrent rename and audits it as its predecessor, and readers
  never lock;
- a static guard: no other Application module reads the settings, so
  they never feed a production rule.

The API commits real transactions, so tests isolate through unique
Department names; every policy section is restored after each test and
the module database is dropped afterwards. Helpers are copied from the
slice 6 module (each module owns its own).
"""

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
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APPLICATION_DIR = _BACKEND_DIR / "app" / "application"
_TEST_DATABASE = "partflow_test_display_settings_api"
_DB_URL_ENV = "DATABASE_URL"
_POLICY_PATH = "/api/policies/due-soon"
_SESSIONS_POLICY_PATH = "/api/policies/worker-sessions"
_CORRECTION_POLICY_PATH = "/api/policies/correction-permissions"
_SECTION = "due-soon"

_E_B1 = "Seconds per displayed row must be a whole number from 1 to 60."
_E_B2 = "The minimum page dwell must be a whole number of seconds from 1 to 300."
_E_D1 = "Minimum warning days must be a whole number from 0 to 365."
_E_D2 = "Maximum warning days must be a whole number from 0 to 365."
_E_D3 = "The lead-time warning percentage must be a whole number from 1 to 100."
_E_D4 = "Minimum warning days cannot be greater than maximum warning days."

_DUE_SOON_KEYS = ("due_soon_min_days", "due_soon_lead_time_percent", "due_soon_max_days")
_DUE_SOON_DEFAULTS = {
    "due_soon_min_days": 2,
    "due_soon_lead_time_percent": 15,
    "due_soon_max_days": 7,
}
_SESSION_DEFAULTS = {
    "worker_session_timeout_minutes": 15,
    "badge_confirm_done": True,
    "badge_confirm_queue": True,
    "badge_confirm_undo": True,
}
_CORRECTION_DEFAULTS = {"undo_reason_required": False}
_SETTING_DEFAULTS = {"board_seconds_per_row": 3, "board_min_page_seconds": 6}


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
    """Every test leaves every policy section at its approved defaults."""
    yield
    _ok(client.put(_POLICY_PATH, json=_DUE_SOON_DEFAULTS))
    _ok(client.put(_SESSIONS_POLICY_PATH, json=_SESSION_DEFAULTS))
    _ok(client.put(_CORRECTION_POLICY_PATH, json=_CORRECTION_DEFAULTS))


# ---------------------------------------------------------------------------
# Helpers (copied from the slice 6 module)
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _refused(response: Any, detail: str) -> None:
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": detail}


def _create_department(client: TestClient) -> dict[str, Any]:
    return _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)


def _due_soon(values: tuple[int, int, int]) -> dict[str, int]:
    return dict(zip(_DUE_SOON_KEYS, values, strict=True))


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


def _department_audits(engine: Engine, department_id: int) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, before_data, after_data FROM audit_events"
                    " WHERE entity_type = 'Department' AND entity_id = :id ORDER BY id"
                ),
                {"id": str(department_id)},
            )
        )


def _audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one())


def _stored_policy(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(sa.text("SELECT * FROM application_policy")).one()
    stored = dict(row._mapping)
    stored.pop("updated_at")
    return stored


def _stored_department(engine: Engine, department_id: int) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            sa.text("SELECT * FROM departments WHERE id = :id"), {"id": department_id}
        ).one()
    return dict(row._mapping)


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


# ---------------------------------------------------------------------------
# DS-1 … DS-5 — the Due Soon policy section
# ---------------------------------------------------------------------------


def test_the_due_soon_policy_reads_its_defaults(client: TestClient) -> None:
    policy = _ok(client.get(_POLICY_PATH))
    assert set(policy) == {*_DUE_SOON_KEYS, "updated_at"}
    assert {key: policy[key] for key in _DUE_SOON_KEYS} == _DUE_SOON_DEFAULTS
    assert policy["updated_at"]


def test_an_effective_put_is_stored_and_audited_once(client: TestClient, db_engine: Engine) -> None:
    before = len(_policy_audits(db_engine, _SECTION))
    new = _due_soon((1, 20, 5))
    stored = _ok(client.put(_POLICY_PATH, json=new))
    assert {key: stored[key] for key in _DUE_SOON_KEYS} == new
    assert {key: _ok(client.get(_POLICY_PATH))[key] for key in _DUE_SOON_KEYS} == new

    rows = _policy_audits(db_engine, _SECTION)
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == ("UPDATED", _SECTION, _DUE_SOON_DEFAULTS, new, None)

    # The same PUT again: 200 with the stored policy, nothing written.
    again = _ok(client.put(_POLICY_PATH, json=new))
    assert again == stored
    assert len(_policy_audits(db_engine, _SECTION)) == before + 1


@pytest.mark.parametrize(
    ("values", "detail"),
    [
        ((-1, 15, 7), _E_D1),
        ((366, 15, 366), _E_D1),
        ((2, 15, 366), _E_D2),
        ((2, 15, -1), _E_D2),
        ((2, 0, 7), _E_D3),
        ((2, 101, 7), _E_D3),
        ((5, 15, 3), _E_D4),
        # The first failure wins.
        ((-1, 0, 7), _E_D1),
        ((2, 0, 400), _E_D2),
        ((9, 0, 3), _E_D3),
    ],
)
def test_out_of_range_policies_are_refused_with_nothing_written(
    client: TestClient, db_engine: Engine, values: tuple[int, int, int], detail: str
) -> None:
    stored = _stored_policy(db_engine)
    count = _audit_count(db_engine)
    _refused(client.put(_POLICY_PATH, json=_due_soon(values)), detail)
    assert _stored_policy(db_engine) == stored
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize(
    "body",
    [
        {"due_soon_min_days": 2, "due_soon_lead_time_percent": 15},
        {},
        {**_DUE_SOON_DEFAULTS, "due_soon_min_days": "2"},
        {**_DUE_SOON_DEFAULTS, "due_soon_lead_time_percent": 2.0},
        {**_DUE_SOON_DEFAULTS, "due_soon_max_days": True},
        {**_DUE_SOON_DEFAULTS, "due_soon_min_days": None},
        {**_DUE_SOON_DEFAULTS, "extra": 1},
    ],
    ids=["missing-field", "empty", "string", "float", "bool", "null", "extra-field"],
)
def test_malformed_policy_bodies_are_refused_by_the_schema(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    stored = _stored_policy(db_engine)
    count = _audit_count(db_engine)
    assert client.put(_POLICY_PATH, json=body).status_code == 422
    assert _stored_policy(db_engine) == stored
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize("values", [(0, 1, 0), (365, 100, 365), (3, 15, 3)])
def test_the_policy_boundaries_are_admitted(
    client: TestClient, values: tuple[int, int, int]
) -> None:
    stored = _ok(client.put(_POLICY_PATH, json=_due_soon(values)))
    assert tuple(stored[key] for key in _DUE_SOON_KEYS) == values


def test_policy_sections_never_touch_each_other(client: TestClient, db_engine: Engine) -> None:
    sessions = _ok(client.get(_SESSIONS_POLICY_PATH))
    correction = _ok(client.get(_CORRECTION_POLICY_PATH))
    _ok(client.put(_POLICY_PATH, json=_due_soon((1, 20, 5))))
    after_sessions = _ok(client.get(_SESSIONS_POLICY_PATH))
    after_correction = _ok(client.get(_CORRECTION_POLICY_PATH))
    assert {key: after_sessions[key] for key in _SESSION_DEFAULTS} == {
        key: sessions[key] for key in _SESSION_DEFAULTS
    }
    assert after_correction["undo_reason_required"] == correction["undo_reason_required"]

    _ok(
        client.put(
            _SESSIONS_POLICY_PATH,
            json={"worker_session_timeout_minutes": 30, "badge_confirm_queue": False},
        )
    )
    _ok(client.put(_CORRECTION_POLICY_PATH, json={"undo_reason_required": True}))
    due_soon = _ok(client.get(_POLICY_PATH))
    assert tuple(due_soon[key] for key in _DUE_SOON_KEYS) == (1, 20, 5)

    # Each audit row carries only its own section's keys and entity_id.
    assert set(_policy_audits(db_engine, _SECTION)[-1].after_data) == set(_DUE_SOON_KEYS)
    assert set(_policy_audits(db_engine, "worker-sessions")[-1].after_data) == set(
        _SESSION_DEFAULTS
    )
    assert set(_policy_audits(db_engine, "correction-permissions")[-1].after_data) == {
        "undo_reason_required"
    }


# ---------------------------------------------------------------------------
# DS-6 … DS-10 — Department display settings
# ---------------------------------------------------------------------------


def test_a_new_department_takes_the_default_settings(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    assert {key: department[key] for key in _SETTING_DEFAULTS} == _SETTING_DEFAULTS
    rows = _department_audits(db_engine, int(department["id"]))
    assert len(rows) == 1
    assert rows[0].event_type == "CREATED"
    assert rows[0].after_data == {
        "name": department["name"],
        "is_active": True,
        **_SETTING_DEFAULTS,
    }
    assert {key: _listed(client, department)[key] for key in _SETTING_DEFAULTS} == _SETTING_DEFAULTS


def _listed(client: TestClient, department: dict[str, Any]) -> dict[str, Any]:
    response = client.get("/api/departments")
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in cast(list[dict[str, Any]], response.json())}
    return rows[department["id"]]


def _settings(body: dict[str, Any]) -> tuple[int, int]:
    return body["board_seconds_per_row"], body["board_min_page_seconds"]


def test_settings_patches_merge_and_are_audited(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    department_id = int(department["id"])
    path = f"/api/departments/{department_id}"
    name = department["name"]

    both = _ok(client.patch(path, json={"board_seconds_per_row": 2, "board_min_page_seconds": 10}))
    assert _settings(both) == (2, 10)
    assert both["name"] == name and both["is_active"] is True
    rows = _department_audits(db_engine, department_id)
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    first = {"name": name, "is_active": True, **_SETTING_DEFAULTS}
    second = {
        "name": name,
        "is_active": True,
        "board_seconds_per_row": 2,
        "board_min_page_seconds": 10,
    }
    # One row for a multi-field PATCH, with the four-key snapshots.
    assert (rows[1].before_data, rows[1].after_data) == (first, second)

    # A name-only PATCH keeps the settings; a settings-only PATCH keeps the name.
    new_name = _unique("DEPT")
    renamed = _ok(client.patch(path, json={"name": new_name}))
    assert _settings(renamed) == (2, 10)
    kept_name = _ok(client.patch(path, json={"board_seconds_per_row": 5}))
    assert kept_name["name"] == new_name and _settings(kept_name) == (5, 10)

    # An identical PATCH writes nothing.
    count = len(_department_audits(db_engine, department_id))
    same = _ok(client.patch(path, json={"board_seconds_per_row": 5, "board_min_page_seconds": 10}))
    assert same == kept_name
    assert len(_department_audits(db_engine, department_id)) == count

    # Partial merge: each field keeps the other's stored value.
    _ok(client.patch(path, json={"board_seconds_per_row": 4}))
    merged = _ok(client.patch(path, json={"board_min_page_seconds": 20}))
    assert _settings(merged) == (4, 20)
    assert _settings(_listed(client, department)) == (4, 20)
    rows = _department_audits(db_engine, department_id)
    assert len(rows) == count + 2
    assert rows[-1].before_data["board_seconds_per_row"] == 4
    assert rows[-1].after_data == {
        "name": new_name,
        "is_active": True,
        "board_seconds_per_row": 4,
        "board_min_page_seconds": 20,
    }
    # The chain: each before is the previous after.
    for previous, current in zip(rows[1:], rows[2:], strict=False):
        assert current.before_data == previous.after_data


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"board_seconds_per_row": 0}, _E_B1),
        ({"board_seconds_per_row": 61}, _E_B1),
        ({"board_seconds_per_row": None}, _E_B1),
        ({"board_min_page_seconds": 0}, _E_B2),
        ({"board_min_page_seconds": 301}, _E_B2),
        ({"board_min_page_seconds": None}, _E_B2),
        # The rotation settings are validated in order; nothing is written.
        ({"board_seconds_per_row": 0, "board_min_page_seconds": 0}, _E_B1),
        ({"board_seconds_per_row": 2, "board_min_page_seconds": 0}, _E_B2),
    ],
)
def test_out_of_range_settings_are_refused_with_nothing_written(
    client: TestClient, db_engine: Engine, body: dict[str, Any], detail: str
) -> None:
    department = _create_department(client)
    stored = _stored_department(db_engine, int(department["id"]))
    count = _audit_count(db_engine)
    _refused(client.patch(f"/api/departments/{department['id']}", json=body), detail)
    assert _stored_department(db_engine, int(department["id"])) == stored
    assert _audit_count(db_engine) == count


@pytest.mark.parametrize(
    "body",
    [
        {"board_seconds_per_row": "3"},
        {"board_seconds_per_row": 3.5},
        {"board_seconds_per_row": True},
        {"board_min_page_seconds": "6"},
        {"board_min_page_seconds": 6.5},
        {"board_min_page_seconds": False},
    ],
)
def test_non_integer_settings_are_refused_by_the_schema(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    department = _create_department(client)
    stored = _stored_department(db_engine, int(department["id"]))
    count = _audit_count(db_engine)
    assert client.patch(f"/api/departments/{department['id']}", json=body).status_code == 422
    assert _stored_department(db_engine, int(department["id"])) == stored
    assert _audit_count(db_engine) == count


def test_an_unknown_department_and_a_combined_refusal_write_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    count = _audit_count(db_engine)
    missing = client.patch("/api/departments/999999", json={"board_seconds_per_row": 2})
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Department 999999 does not exist."}

    department = _create_department(client)
    stored = _stored_department(db_engine, int(department["id"]))
    combined = client.patch(
        f"/api/departments/{department['id']}",
        json={"name": "  ", "board_seconds_per_row": 2},
    )
    assert combined.status_code == 422, combined.text
    assert _stored_department(db_engine, int(department["id"])) == stored
    assert _audit_count(db_engine) == count + 1  # only the create


def test_an_inactive_department_accepts_settings(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    path = f"/api/departments/{department['id']}"
    _ok(client.patch(path, json={"is_active": False}))
    updated = _ok(client.patch(path, json={"board_seconds_per_row": 7}))
    assert updated["is_active"] is False
    assert _settings(updated) == (7, 6)
    last = _department_audits(db_engine, int(department["id"]))[-1]
    assert last.event_type == "UPDATED"
    assert (
        last.before_data["board_seconds_per_row"],
        last.after_data["board_seconds_per_row"],
    ) == (
        3,
        7,
    )


def test_the_create_body_refuses_the_settings(client: TestClient, db_engine: Engine) -> None:
    name = _unique("DEPT")
    count = _audit_count(db_engine)
    response = client.post("/api/departments", json={"name": name, "board_seconds_per_row": 2})
    assert response.status_code == 422
    assert name not in {row["name"] for row in client.get("/api/departments").json()}
    assert _audit_count(db_engine) == count


# ---------------------------------------------------------------------------
# DS-11 — the Production Board feed
# ---------------------------------------------------------------------------


def _board_department(client: TestClient, department_id: int) -> dict[str, Any]:
    board = _ok(client.get("/api/production-board", params={"department_id": department_id}))
    return cast(dict[str, Any], board["department"])


def test_the_board_feed_carries_its_departments_own_settings(client: TestClient) -> None:
    first = _create_department(client)
    second = _create_department(client)
    _ok(client.patch(f"/api/departments/{second['id']}", json={"board_seconds_per_row": 2}))

    assert _board_department(client, int(first["id"])) == {
        "id": first["id"],
        "name": first["name"],
        **_SETTING_DEFAULTS,
    }
    assert _settings(_board_department(client, int(second["id"]))) == (2, 6)

    _ok(
        client.patch(
            f"/api/departments/{first['id']}",
            json={"board_seconds_per_row": 1, "board_min_page_seconds": 4},
        )
    )
    assert _settings(_board_department(client, int(first["id"]))) == (1, 4)
    assert _settings(_board_department(client, int(second["id"]))) == (2, 6)


# ---------------------------------------------------------------------------
# DS-C1 … DS-C3 — concurrency
# ---------------------------------------------------------------------------


def test_policy_writers_serialize_and_merge(client: TestClient, db_engine: Engine) -> None:
    new = _due_soon((1, 20, 5))
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(sa.text("UPDATE application_policy SET worker_session_timeout_minutes = 30"))
        thread, results = _start(lambda: client.put(_POLICY_PATH, json=new))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert {key: _ok(response)[key] for key in _DUE_SOON_KEYS} == new
    with db_engine.connect() as connection:
        stored = connection.execute(
            sa.text(
                "SELECT worker_session_timeout_minutes, due_soon_min_days,"
                " due_soon_lead_time_percent, due_soon_max_days FROM application_policy"
            )
        ).one()
    assert tuple(stored) == (30, 1, 20, 5)
    last = _policy_audits(db_engine, _SECTION)[-1]
    assert (last.before_data, last.after_data) == (_DUE_SOON_DEFAULTS, new)


def test_the_due_soon_write_locks_before_it_reads(client: TestClient, db_engine: Engine) -> None:
    before = len(_policy_audits(db_engine, _SECTION))
    new = _due_soon((1, 20, 5))
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(sa.text("UPDATE application_policy SET due_soon_max_days = 9"))
        thread, results = _start(lambda: client.put(_POLICY_PATH, json=new))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert {key: _ok(response)[key] for key in _DUE_SOON_KEYS} == new
    rows = _policy_audits(db_engine, _SECTION)
    assert len(rows) == before + 1
    # The committed predecessor, re-read under the lock.
    assert (rows[-1].before_data, rows[-1].after_data) == (_due_soon((2, 15, 9)), new)


def test_the_no_op_is_judged_on_the_locked_row(client: TestClient, db_engine: Engine) -> None:
    before = len(_policy_audits(db_engine, _SECTION))
    new = _due_soon((1, 20, 5))
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(
            sa.text(
                "UPDATE application_policy SET due_soon_min_days = 1,"
                " due_soon_lead_time_percent = 20, due_soon_max_days = 5"
            )
        )
        thread, results = _start(lambda: client.put(_POLICY_PATH, json=new))
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert {key: _ok(response)[key] for key in _DUE_SOON_KEYS} == new
    assert {key: _ok(client.get(_POLICY_PATH))[key] for key in _DUE_SOON_KEYS} == new
    assert len(_policy_audits(db_engine, _SECTION)) == before


def test_a_settings_patch_waits_for_a_concurrent_rename(
    client: TestClient, db_engine: Engine
) -> None:
    department = _create_department(client)
    department_id = int(department["id"])
    held_name = _unique("HELD")
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM departments WHERE id = :id FOR NO KEY UPDATE"),
            {"id": department_id},
        )
        holder.execute(
            sa.text("UPDATE departments SET name = :name WHERE id = :id"),
            {"name": held_name, "id": department_id},
        )
        thread, results = _start(
            lambda: client.patch(
                f"/api/departments/{department_id}",
                json={"board_seconds_per_row": 2, "board_min_page_seconds": 10},
            )
        )
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    body = _ok(response)
    assert body["name"] == held_name and _settings(body) == (2, 10)
    stored = _stored_department(db_engine, department_id)
    assert (stored["name"], _settings(stored)) == (held_name, (2, 10))
    last = _department_audits(db_engine, department_id)[-1]
    assert last.before_data == {"name": held_name, "is_active": True, **_SETTING_DEFAULTS}
    assert last.after_data == {
        "name": held_name,
        "is_active": True,
        "board_seconds_per_row": 2,
        "board_min_page_seconds": 10,
    }


def test_readers_never_lock(client: TestClient, db_engine: Engine) -> None:
    department = _create_department(client)
    department_id = int(department["id"])
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM departments WHERE id = :id FOR UPDATE"), {"id": department_id}
        )
        holder.execute(
            sa.text(
                "UPDATE departments SET board_seconds_per_row = 9, board_min_page_seconds = 99"
                " WHERE id = :id"
            ),
            {"id": department_id},
        )
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR UPDATE"))
        holder.execute(sa.text("UPDATE application_policy SET due_soon_min_days = 0"))
        try:
            for read in (
                lambda: _board_department(client, department_id),
                lambda: _listed(client, department),
                lambda: _ok(client.get(_POLICY_PATH)),
            ):
                thread, results = _start(read)
                # Completes while the holder is still open.
                thread.join(timeout=5)
                assert not thread.is_alive()
                assert len(results) == 1
            board, listed, policy = (
                _board_department(client, department_id),
                _listed(client, department),
                _ok(client.get(_POLICY_PATH)),
            )
        finally:
            holder.rollback()
    assert _settings(board) == (3, 6)
    assert _settings(listed) == (3, 6)
    assert policy["due_soon_min_days"] == 2


# ---------------------------------------------------------------------------
# DS-12 — static guard
# ---------------------------------------------------------------------------

_SETTING_READERS = {"environment.py", "policies.py"}
_SETTING_TOKENS = ("board_seconds_per_row", "board_min_page_seconds", "due_soon_", "DUE_SOON_")


def test_no_production_rule_reads_the_display_settings() -> None:
    """Only the Department service and the policies service touch the
    settings; the board read model passes the Department through and the
    API copies the two values — no production write path reads them."""
    offenders = sorted(
        (path.name, token)
        for path in _APPLICATION_DIR.rglob("*.py")
        if path.name not in _SETTING_READERS
        for token in _SETTING_TOKENS
        if token in path.read_text(encoding="utf-8")
    )
    assert offenders == []
    # The guard is not vacuous: both readers do reference them.
    for name in _SETTING_READERS:
        source = (_APPLICATION_DIR / name).read_text(encoding="utf-8")
        assert any(token in source for token in _SETTING_TOKENS), name
