"""Integration tests for the Phase 13 slice 2b Machine configuration audit.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Every effective Machine
configuration write appends exactly one ``audit_events`` row (entity
``Machine``) in its own transaction (PROJECT_PROFILE §28
"administrative configuration changes"; owner decision S2-F6):

- exact ``CREATED``/``UPDATED`` rows: ``entity_id`` the internal id, the
  twelve-key snapshot (never ``retired_on``, ``state_changed_at`` or a
  timestamp), ``maintenance_since`` as UTC ISO-8601 text equal to the
  stored start and to the row's ``occurred_at``, ``actor_reference``
  NULL and ``metadata`` NULL;
- retirement and reactivation: no audit row for the lifecycle
  transition itself (``machine_lifecycle_events`` records it); only the
  configuration delta they carry (a Save draft; a rename, Area move or
  cleared maintenance context) is audited, linked to its lifecycle
  event through ``metadata.machine_lifecycle_event_id``;
- one row per effective request; no row for a no-op, a refusal, a lost
  race or a production command moving ``state_changed_at``;
- lock-first: every admin write waits for a concurrent row holder,
  audits the committed predecessor and judges the committed row with
  the existing checks (lost races are 409s), in the lock mode its own
  UPDATE takes (FOR KEY SHARE never blocks it); a uniqueness race lost
  at the new flush points is the existing 409, never a 500;
- atomicity on every audited path, the audit chain property, and a
  static guard over every Machine writer.

``tests/test_machines_api.py`` stays unchanged: that it passes is the
proof that the HTTP behavior is identical outside the listed races. The
development database in DATABASE_URL is only used as the admin
connection for CREATE/DROP DATABASE.
"""

import ast
import datetime
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_APP_DIR = _BACKEND_DIR / "app"
_MACHINE_SERVICE = _APP_DIR / "application" / "machines.py"
_TEST_DATABASE = "partflow_test_machine_audit_api"
_SNAPSHOT_KEYS = {
    "area_id",
    "name",
    "asset_tag",
    "description",
    "manufacturer",
    "model",
    "serial_number",
    "installed_on",
    "notes",
    "maintenance_since",
    "maintenance_note",
    "maintenance_expected_return",
}
_MAINTENANCE_KEYS = {"maintenance_since", "maintenance_note", "maintenance_expected_return"}
_DUPLICATE_ACTIVE_NAME = (
    "The Area already has an active Machine with this name."
    " Display names must be unique among the active Machines of one Area."
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
    """Temporary database migrated to head for the API under test."""
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
def client(api_database_url: URL) -> Iterator[TestClient]:
    """Application client wired to the temporary database."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    """Direct database access for assertions and concurrent holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    """Machine creation requires the configured Asset Tag format."""
    response = client.put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "CD-", "digits": 4},
    )
    assert response.status_code == 200, response.text


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _ok(response: Response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


def _create_area(client: TestClient, **overrides: Any) -> dict[str, Any]:
    department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
    payload = {"department_id": department["id"], "name": _unique("AREA"), **overrides}
    return _ok(client.post("/api/areas", json=payload), 201)


def _create_machine(
    client: TestClient, area_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if area_id is None:
        area_id = int(_create_area(client)["id"])
    payload = {"area_id": area_id, "name": _unique("MACHINE"), **overrides}
    return _ok(client.post("/api/machines", json=payload), 201)


def _retire(client: TestClient, machine_id: int, **overrides: Any) -> dict[str, Any]:
    return _ok(client.post(f"/api/machines/{machine_id}/retire", json={**overrides}))


def _start_maintenance(client: TestClient, machine_id: int, **body: Any) -> dict[str, Any]:
    return _ok(client.post(f"/api/machines/{machine_id}/maintenance", json=body), 201)


def _audit_rows(engine: Engine, machine_id: object) -> list[sa.Row[Any]]:
    """One Machine's audit history in write order."""
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AuditEvent.__table__)
                .where(
                    models.AuditEvent.entity_type == "Machine",
                    models.AuditEvent.entity_id == str(machine_id),
                )
                .order_by(models.AuditEvent.id)
            )
        )


def _audit_count(engine: Engine, entity_types: tuple[str, ...] | None = None) -> int:
    query = sa.select(sa.func.count()).select_from(models.AuditEvent.__table__)
    if entity_types is not None:
        query = query.where(models.AuditEvent.entity_type.in_(entity_types))
    with engine.connect() as connection:
        return int(connection.execute(query).scalar_one())


def _table(model: type[models.Base]) -> sa.Table:
    return cast(sa.Table, model.__table__)


def _stored(engine: Engine, model: type[models.Base], key: object) -> sa.Row[Any]:
    """The whole stored row."""
    table = _table(model)
    primary_key = next(iter(table.primary_key.columns))
    with engine.connect() as connection:
        return connection.execute(sa.select(table).where(primary_key == key)).one()


def _next_sequence(engine: Engine) -> int:
    return int(_stored(engine, models.MachineAssetTagConfig, 1).next_sequence)


def _lifecycle_events(engine: Engine, machine_id: object) -> list[sa.Row[Any]]:
    table = _table(models.MachineLifecycleEvent)
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(table).where(table.c.machine_id == machine_id).order_by(table.c.id)
            )
        )


def _assert_chain(rows: list[sa.Row[Any]]) -> None:
    """Each row's before_data is its predecessor's after_data on every
    key present in both snapshots."""
    assert [row.id for row in rows] == sorted(row.id for row in rows)
    for previous, current in zip(rows, rows[1:], strict=False):
        shared = previous.after_data.keys() & current.before_data.keys()
        assert shared
        assert {key: current.before_data[key] for key in shared} == {
            key: previous.after_data[key] for key in shared
        }


def _changed_keys(row: sa.Row[Any]) -> set[str]:
    return {key for key in row.after_data if row.after_data[key] != row.before_data[key]}


def _utc(value: datetime.datetime) -> str:
    return value.astimezone(datetime.UTC).isoformat()


def _start(request: Callable[[], Response]) -> tuple[threading.Thread, list[Response]]:
    results: list[Response] = []
    thread = threading.Thread(target=lambda: results.append(request()))
    thread.start()
    return thread, results


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread, results: list[Response]) -> Response:
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(results) == 1
    return results[0]


def _lock_and_update(holder: sa.Connection, machine_id: int, assignments: str) -> None:
    """Hold the Machine row FOR UPDATE with an uncommitted change."""
    holder.execute(sa.text("SELECT 1 FROM machines WHERE id = :id FOR UPDATE"), {"id": machine_id})
    holder.execute(sa.text(f"UPDATE machines SET {assignments} WHERE id = :id"), {"id": machine_id})


# ---------------------------------------------------------------------------
# M-1 — creation
# ---------------------------------------------------------------------------


def test_machine_create_is_audited_with_the_exact_snapshot(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    format_count = _audit_count(db_engine, ("MachineAssetTagConfig",))
    machine = _create_machine(
        client,
        int(area["id"]),
        description="Five-axis mill",
        manufacturer="Haas",
        model="UMC-750",
        serial_number=_unique("SN"),
        installed_on="2026-01-15",
        notes="Bay 3",
    )

    rows = _audit_rows(db_engine, machine["id"])
    assert len(rows) == 1
    row = rows[0]
    assert row.event_type == "CREATED"
    assert row.entity_id == str(machine["id"])
    assert row.before_data is None
    assert row.after_data == {
        "area_id": area["id"],
        "name": machine["name"],
        "asset_tag": machine["asset_tag"],
        "description": "Five-axis mill",
        "manufacturer": "Haas",
        "model": "UMC-750",
        "serial_number": machine["serial_number"],
        "installed_on": "2026-01-15",
        "notes": "Bay 3",
        "maintenance_since": None,
        "maintenance_note": None,
        "maintenance_expected_return": None,
    }
    assert row.metadata is None
    assert row.actor_reference is None
    assert _audit_count(db_engine, ("MachineAssetTagConfig",)) == format_count


# ---------------------------------------------------------------------------
# M-2 / M-3 — metadata edits, no-ops and refusals
# ---------------------------------------------------------------------------


def test_metadata_edits_are_audited_one_row_per_effective_request(
    client: TestClient, db_engine: Engine
) -> None:
    machine = _create_machine(client)
    path = f"/api/machines/{machine['id']}"
    expected = dict(_audit_rows(db_engine, machine["id"])[0].after_data)
    edits: list[dict[str, Any]] = [
        {"name": _unique("MACHINE")},
        {"description": "desc"},
        {"manufacturer": "DMG"},
        {"model": "DMU 50"},
        {"serial_number": _unique("SN")},
        {"installed_on": "2025-12-01"},
        {"notes": "first"},
    ]
    for edit in edits:
        _ok(client.patch(path, json=edit))
        row = _audit_rows(db_engine, machine["id"])[-1]
        assert row.event_type == "UPDATED"
        assert row.before_data == expected
        expected = {**expected, **edit}
        assert row.after_data == expected
        assert row.metadata is None
        assert row.actor_reference is None

    multi = {"description": "multi", "notes": "second", "model": "DMU 65"}
    _ok(client.patch(path, json=multi))
    rows = _audit_rows(db_engine, machine["id"])
    assert len(rows) == 1 + len(edits) + 1
    assert _changed_keys(rows[-1]) == set(multi)
    expected = {**expected, **multi}
    assert rows[-1].after_data == expected

    count = _audit_count(db_engine)
    _ok(client.patch(path, json={"description": "  multi  ", "notes": " second "}))
    _ok(client.patch(path, json={}))
    assert _audit_count(db_engine) == count
    _assert_chain(_audit_rows(db_engine, machine["id"]))


def test_machine_refusals_audit_nothing(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    machine = _create_machine(client, int(area["id"]))
    other = _create_machine(client, int(area["id"]))
    retired = _create_machine(client, int(area["id"]))
    _retire(client, int(retired["id"]))
    inactive = _create_area(client)
    _ok(client.patch(f"/api/areas/{inactive['id']}", json={"is_active": False}))
    path = f"/api/machines/{machine['id']}"
    stored = _stored(db_engine, models.Machine, machine["id"])
    stored_retired = _stored(db_engine, models.Machine, retired["id"])
    count = _audit_count(db_engine)
    sequence = _next_sequence(db_engine)

    refusals: list[tuple[Callable[[], Response], int]] = [
        (lambda: client.patch(path, json={"name": "   "}), 422),
        (lambda: client.patch(path, json={"name": other["name"]}), 409),
        (lambda: client.patch(path, json={"asset_tag": "CD-9999"}), 422),
        (lambda: client.patch(path, json={"area_id": inactive["id"]}), 422),
        (lambda: client.patch(path, json={"maintenance_note": "x"}), 409),
        (lambda: client.patch(f"/api/machines/{retired['id']}", json={"notes": "x"}), 409),
        (lambda: client.patch("/api/machines/999999", json={"notes": "x"}), 404),
        (
            lambda: client.post(
                "/api/machines", json={"area_id": inactive["id"], "name": _unique("M")}
            ),
            409,
        ),
        (
            lambda: client.post(
                "/api/machines",
                json={"area_id": area["id"], "name": _unique("M"), "expected_asset_tag": "X-1"},
            ),
            409,
        ),
    ]
    for attempt, status in refusals:
        response = attempt()
        assert response.status_code == status, response.text

    assert _audit_count(db_engine) == count
    assert _stored(db_engine, models.Machine, machine["id"]) == stored
    assert _stored(db_engine, models.Machine, retired["id"]) == stored_retired
    assert _next_sequence(db_engine) == sequence


# ---------------------------------------------------------------------------
# M-4 — maintenance override
# ---------------------------------------------------------------------------


def test_maintenance_start_edit_and_clear_are_audited(
    client: TestClient, db_engine: Engine
) -> None:
    machine = _create_machine(client)
    started = _start_maintenance(
        client, int(machine["id"]), note="Spindle", expected_return="2026-11-01"
    )

    rows = _audit_rows(db_engine, machine["id"])
    start = rows[-1]
    assert start.event_type == "UPDATED"
    assert {key: start.before_data[key] for key in _MAINTENANCE_KEYS} == dict.fromkeys(
        _MAINTENANCE_KEYS
    )
    assert _changed_keys(start) == _MAINTENANCE_KEYS
    stored_since = _stored(db_engine, models.Machine, machine["id"]).maintenance_since
    assert start.after_data["maintenance_since"] == _utc(stored_since)
    assert start.after_data["maintenance_since"] == _utc(start.occurred_at)
    assert start.after_data["maintenance_note"] == "Spindle"
    assert start.after_data["maintenance_expected_return"] == "2026-11-01"
    assert started["maintenance_since"] is not None

    _ok(
        client.patch(
            f"/api/machines/{machine['id']}",
            json={"maintenance_note": "Waiting for parts", "maintenance_expected_return": None},
        )
    )
    in_place = _audit_rows(db_engine, machine["id"])[-1]
    assert _changed_keys(in_place) == {"maintenance_note", "maintenance_expected_return"}
    assert in_place.after_data["maintenance_since"] == start.after_data["maintenance_since"]

    count = _audit_count(db_engine)
    again = client.post(f"/api/machines/{machine['id']}/maintenance", json={})
    assert again.status_code == 409
    assert "already under maintenance" in again.json()["detail"]
    assert _audit_count(db_engine) == count

    _ok(client.delete(f"/api/machines/{machine['id']}/maintenance"))
    cleared = _audit_rows(db_engine, machine["id"])[-1]
    assert _changed_keys(cleared) == {"maintenance_since", "maintenance_note"}
    assert {key: cleared.after_data[key] for key in _MAINTENANCE_KEYS} == dict.fromkeys(
        _MAINTENANCE_KEYS
    )
    _assert_chain(_audit_rows(db_engine, machine["id"]))


# ---------------------------------------------------------------------------
# M-5 / M-6 — retirement and reactivation
# ---------------------------------------------------------------------------


def test_retirement_audits_only_the_save_draft_it_applies(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    plain = _create_machine(client, int(area["id"]))
    _retire(client, int(plain["id"]), reason="worn out")
    assert [event.event_type for event in _lifecycle_events(db_engine, plain["id"])] == ["RETIRED"]
    assert [row.event_type for row in _audit_rows(db_engine, plain["id"])] == ["CREATED"]

    drafted = _create_machine(client, int(area["id"]))
    new_name = _unique("MACHINE")
    _retire(client, int(drafted["id"]), edits={"name": new_name, "notes": "scrapped"})
    events = _lifecycle_events(db_engine, drafted["id"])
    assert [event.event_type for event in events] == ["RETIRED"]
    rows = _audit_rows(db_engine, drafted["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]
    assert _changed_keys(rows[1]) == {"name", "notes"}
    assert rows[1].after_data["name"] == new_name
    assert rows[1].after_data["notes"] == "scrapped"
    assert rows[1].metadata == {"machine_lifecycle_event_id": events[0].id}
    for row in rows:
        for snapshot in (row.before_data, row.after_data):
            assert snapshot is None or "retired_on" not in snapshot
    _assert_chain(rows)

    taken = _create_machine(client, int(area["id"]))
    failing = _create_machine(client, int(area["id"]))
    stored = _stored(db_engine, models.Machine, failing["id"])
    count = _audit_count(db_engine)
    response = client.post(
        f"/api/machines/{failing['id']}/retire", json={"edits": {"name": taken["name"]}}
    )
    assert response.status_code == 409, response.text
    assert _lifecycle_events(db_engine, failing["id"]) == []
    assert _audit_count(db_engine) == count
    assert _stored(db_engine, models.Machine, failing["id"]) == stored


def test_reactivation_audits_only_its_configuration_delta(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    plain = _create_machine(client, int(area["id"]))
    _retire(client, int(plain["id"]))
    _ok(client.post(f"/api/machines/{plain['id']}/reactivate", json={"reason": "back"}))
    assert [event.event_type for event in _lifecycle_events(db_engine, plain["id"])] == [
        "RETIRED",
        "REACTIVATED",
    ]
    assert [row.event_type for row in _audit_rows(db_engine, plain["id"])] == ["CREATED"]

    moved = _create_machine(client, int(area["id"]))
    _start_maintenance(client, int(moved["id"]), note="long repair", expected_return="2026-12-01")
    _retire(client, int(moved["id"]))
    target = _create_area(client)
    new_name = _unique("MACHINE")
    _ok(
        client.post(
            f"/api/machines/{moved['id']}/reactivate",
            json={"reason": "moved", "name": new_name, "area_id": target["id"]},
        )
    )

    events = _lifecycle_events(db_engine, moved["id"])
    assert [event.event_type for event in events] == ["RETIRED", "REACTIVATED"]
    rows = _audit_rows(db_engine, moved["id"])
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED", "UPDATED"]
    reactivation = rows[-1]
    assert _changed_keys(reactivation) == {"name", "area_id"} | _MAINTENANCE_KEYS
    assert reactivation.after_data["name"] == new_name
    assert {key: reactivation.after_data[key] for key in _MAINTENANCE_KEYS} == dict.fromkeys(
        _MAINTENANCE_KEYS
    )
    assert reactivation.metadata == {"machine_lifecycle_event_id": events[1].id}
    assert reactivation.before_data["area_id"] == events[1].from_area_id == area["id"]
    assert reactivation.after_data["area_id"] == events[1].to_area_id == target["id"]
    _assert_chain(rows)


# ---------------------------------------------------------------------------
# M-7 — production commands never audit
# ---------------------------------------------------------------------------


def test_production_assignment_moves_state_age_but_appends_no_machine_row(
    client: TestClient, db_engine: Engine
) -> None:
    area = _create_area(client)
    operation = _ok(
        client.post("/api/operations", json={"area_id": area["id"], "code": _unique("OP")}), 201
    )
    station = _ok(
        client.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area["id"]}
        ),
        201,
    )
    machine = _create_machine(client, int(area["id"]))
    pn = _unique("PN").upper()
    work_order = _ok(
        client.post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 50}]}
        ),
        201,
    )
    released = _ok(
        client.post(
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
    state_changed_at = _stored(db_engine, models.Machine, machine["id"]).state_changed_at
    machine_rows = _audit_count(db_engine, ("Machine",))

    _ok(
        client.post(
            f"/api/scan-stations/{station['station_id']}/machine-assignments",
            json={
                "part_number": pn,
                "quantity_flow_id": released["quantity_flow_id"],
                "machine_id": machine["id"],
                "quantity": 10,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )

    assert _stored(db_engine, models.Machine, machine["id"]).state_changed_at != state_changed_at
    assert _audit_count(db_engine, ("Machine",)) == machine_rows


# ---------------------------------------------------------------------------
# M-8 … M-12 — lock-first and lost races
# ---------------------------------------------------------------------------


class _LockCase(NamedTuple):
    action: str
    under_maintenance: bool
    request: Callable[[TestClient, int], Response]


_LOCK_CASES = [
    _LockCase(
        "patch",
        False,
        lambda client, machine_id: client.patch(
            f"/api/machines/{machine_id}", json={"description": "edited"}
        ),
    ),
    _LockCase(
        "start-maintenance",
        False,
        lambda client, machine_id: client.post(
            f"/api/machines/{machine_id}/maintenance", json={"note": "edited"}
        ),
    ),
    _LockCase(
        "clear-maintenance",
        True,
        lambda client, machine_id: client.delete(f"/api/machines/{machine_id}/maintenance"),
    ),
]


@pytest.mark.parametrize("case", _LOCK_CASES, ids=lambda case: case.action)
def test_admin_write_waits_and_audits_the_committed_predecessor(
    client: TestClient, db_engine: Engine, case: _LockCase
) -> None:
    machine = _create_machine(client)
    machine_id = int(machine["id"])
    if case.under_maintenance:
        _start_maintenance(client, machine_id, note="before")
    with db_engine.connect() as holder:
        _lock_and_update(holder, machine_id, "notes = 'holder'")
        thread, results = _start(lambda: case.request(client, machine_id))
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.is_success, response.text
    rows = _audit_rows(db_engine, machine_id)
    assert rows[-1].event_type == "UPDATED"
    assert rows[-1].before_data["notes"] == "holder"
    assert rows[-1].after_data["notes"] == "holder"


def test_concurrent_maintenance_start_loses_with_conflict(
    client: TestClient, db_engine: Engine
) -> None:
    machine = _create_machine(client)
    machine_id = int(machine["id"])
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        _lock_and_update(
            holder, machine_id, "maintenance_since = now(), maintenance_note = 'holder'"
        )
        thread, results = _start(
            lambda: client.post(f"/api/machines/{machine_id}/maintenance", json={"note": "loser"})
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409, response.text
    assert "already under maintenance" in response.json()["detail"]
    assert _stored(db_engine, models.Machine, machine_id).maintenance_note == "holder"
    assert _audit_count(db_engine) == count


def test_edit_losing_to_a_retirement_is_refused(client: TestClient, db_engine: Engine) -> None:
    machine = _create_machine(client, notes="original")
    machine_id = int(machine["id"])
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        _lock_and_update(holder, machine_id, "retired_on = current_date")
        thread, results = _start(
            lambda: client.patch(f"/api/machines/{machine_id}", json={"notes": "loser"})
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409, response.text
    assert "is retired and cannot be edited" in response.json()["detail"]
    assert _stored(db_engine, models.Machine, machine_id).notes == "original"
    assert _audit_count(db_engine) == count


def test_concurrent_reactivation_loses_with_conflict(client: TestClient, db_engine: Engine) -> None:
    machine = _create_machine(client)
    machine_id = int(machine["id"])
    _retire(client, machine_id)
    count = _audit_count(db_engine)
    with db_engine.connect() as holder:
        _lock_and_update(holder, machine_id, "retired_on = NULL")
        thread, results = _start(
            lambda: client.post(f"/api/machines/{machine_id}/reactivate", json={"reason": "loser"})
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409, response.text
    assert "is not retired" in response.json()["detail"]
    assert [event.event_type for event in _lifecycle_events(db_engine, machine_id)] == ["RETIRED"]
    assert _audit_count(db_engine) == count


def test_admin_edit_never_waits_on_key_share(client: TestClient, db_engine: Engine) -> None:
    machine = _create_machine(client)
    machine_id = int(machine["id"])
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM machines WHERE id = :id FOR KEY SHARE"), {"id": machine_id}
        )
        # Completes while the holder is still open.
        response = client.patch(f"/api/machines/{machine_id}", json={"notes": "key share"})
        holder.rollback()

    assert response.status_code == 200, response.text
    rows = _audit_rows(db_engine, machine_id)
    assert [row.event_type for row in rows] == ["CREATED", "UPDATED"]


# ---------------------------------------------------------------------------
# M-13 — atomicity
# ---------------------------------------------------------------------------


def _boom(*args: object, **kwargs: object) -> None:
    raise RuntimeError("audit persistence failed")


def _snapshot_tables(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    """Every stored Machine, lifecycle event, audit row and the counter."""
    tables = (
        _table(models.Machine),
        _table(models.MachineLifecycleEvent),
        _table(models.AuditEvent),
        _table(models.MachineAssetTagConfig),
    )
    with engine.connect() as connection:
        return {
            table.name: [
                tuple(row)
                for row in connection.execute(sa.select(table).order_by(*table.primary_key.columns))
            ]
            for table in tables
        }


def test_failed_audit_write_rolls_back_every_machine_path(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = _create_area(client)
    target = _create_area(client)
    edited = _create_machine(client, int(area["id"]))
    idle = _create_machine(client, int(area["id"]))
    serviced = _create_machine(client, int(area["id"]))
    _start_maintenance(client, int(serviced["id"]), note="x")
    retiring = _create_machine(client, int(area["id"]))
    retired = _create_machine(client, int(area["id"]))
    _retire(client, int(retired["id"]))
    before = _snapshot_tables(db_engine)

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    attempts: list[Callable[[], Response]] = [
        lambda: client.post("/api/machines", json={"area_id": area["id"], "name": _unique("M")}),
        lambda: client.patch(f"/api/machines/{edited['id']}", json={"notes": "changed"}),
        lambda: client.post(f"/api/machines/{idle['id']}/maintenance", json={"note": "n"}),
        lambda: client.delete(f"/api/machines/{serviced['id']}/maintenance"),
        lambda: client.post(
            f"/api/machines/{retiring['id']}/retire",
            json={"edits": {"name": _unique("M"), "notes": "draft"}},
        ),
        lambda: client.post(
            f"/api/machines/{retired['id']}/reactivate",
            json={"reason": "moved", "area_id": target["id"]},
        ),
    ]
    for attempt in attempts:
        with pytest.raises(RuntimeError, match="audit persistence failed"):
            attempt()
    monkeypatch.undo()

    assert _snapshot_tables(db_engine) == before


# ---------------------------------------------------------------------------
# M-14 — the audit chain over one Machine
# ---------------------------------------------------------------------------


def test_audit_chain_over_a_full_machine_history(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    machine = _create_machine(client, int(area["id"]))
    machine_id = int(machine["id"])
    path = f"/api/machines/{machine_id}"
    steps: list[tuple[Callable[[], Response], int]] = [
        (lambda: client.patch(path, json={"description": "first"}), 200),
        (lambda: client.patch(path, json={"description": "first"}), 200),  # no-op
        (lambda: client.patch(path, json={"model": "M-2"}), 200),
        (lambda: client.patch(path, json={"name": "   "}), 422),  # refusal
        (lambda: client.patch(path, json={"notes": "third"}), 200),
        (lambda: client.post(f"{path}/maintenance", json={"note": "start"}), 201),
        (lambda: client.patch(path, json={"maintenance_note": "in place"}), 200),
        (lambda: client.delete(f"{path}/maintenance"), 200),
        (lambda: client.delete(f"{path}/maintenance"), 409),  # refusal
        (lambda: client.patch(path, json={}), 200),  # no-op
        (lambda: client.post(f"{path}/retire", json={"edits": {"notes": "draft"}}), 200),
        (
            lambda: client.post(
                f"{path}/reactivate", json={"reason": "back", "name": _unique("MACHINE")}
            ),
            200,
        ),
    ]
    for attempt, status in steps:
        response = attempt()
        assert response.status_code == status, response.text

    rows = _audit_rows(db_engine, machine_id)
    assert [row.event_type for row in rows] == ["CREATED"] + ["UPDATED"] * 8
    _assert_chain(rows)


# ---------------------------------------------------------------------------
# M-16 — uniqueness races lost at the new flush points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["create", "reactivate"])
def test_name_race_lost_at_flush_is_a_conflict(
    client: TestClient, db_engine: Engine, path: str
) -> None:
    area = _create_area(client)
    name = _unique("MACHINE")
    retired: dict[str, Any] | None = None
    stored: sa.Row[Any] | None = None
    if path == "reactivate":
        retired = _create_machine(client)
        _retire(client, int(retired["id"]))
        stored = _stored(db_engine, models.Machine, retired["id"])
    sequence = _next_sequence(db_engine)
    count = _audit_count(db_engine, ("Machine",))
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(
                "INSERT INTO machines (area_id, name, asset_tag) VALUES (:area_id, :name, :tag)"
            ),
            {"area_id": area["id"], "name": name, "tag": _unique("ZZ")},
        )
        if retired is None:
            thread, results = _start(
                lambda: client.post("/api/machines", json={"area_id": area["id"], "name": name})
            )
        else:
            machine_id = int(retired["id"])
            thread, results = _start(
                lambda: client.post(
                    f"/api/machines/{machine_id}/reactivate",
                    json={"reason": "moved", "name": name, "area_id": area["id"]},
                )
            )
        # The flush waits on the holder's uncommitted duplicate.
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == _DUPLICATE_ACTIVE_NAME
    assert _audit_count(db_engine, ("Machine",)) == count
    with db_engine.connect() as connection:
        same_name = connection.execute(
            sa.select(sa.func.count())
            .select_from(_table(models.Machine))
            .where(models.Machine.area_id == area["id"], models.Machine.name == name)
        ).scalar_one()
    assert same_name == 1
    if retired is None:
        assert _next_sequence(db_engine) == sequence
    else:
        assert [event.event_type for event in _lifecycle_events(db_engine, retired["id"])] == [
            "RETIRED"
        ]
        assert _stored(db_engine, models.Machine, retired["id"]) == stored


# ---------------------------------------------------------------------------
# M-15 — static guard over every Machine writer
# ---------------------------------------------------------------------------

_AUDITED = {
    "create_machine",
    "update_machine",
    "start_maintenance",
    "clear_maintenance",
    "retire_machine",
    "reactivate_machine",
}
# Functions that write a Machine without an audit row, each with its reason.
_NON_AUDITED_MACHINE_WRITERS = {
    "note_assignment_change": "derived state age only, production commands",
}
_MACHINE_LOADERS = {"lock_machine", "get_machine", "_lock_machine_for_edit", "list_machines"}
# Calls whose first argument ``Machine`` yields Machine rows: a session
# lookup, or a query any session call (scalar, scalars, execute) runs.
_MACHINE_ROW_CALLS = {"get", "get_one", "select"}
_MACHINE_BULK_WRITES = {"update", "insert", "delete"}


def _functions(tree: ast.Module) -> list[ast.FunctionDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _first_arg_is_machine(call: ast.Call) -> bool:
    return bool(call.args) and isinstance(call.args[0], ast.Name) and call.args[0].id == "Machine"


def _is_machine_load(value: ast.expr | None) -> bool:
    """An expression that yields Machine rows: it contains a loader call
    (``lock_machine``, ``list_machines`` ...), ``Machine(...)``,
    ``<session>.get(Machine, ...)`` / ``get_one(Machine, ...)``, or a
    ``select(Machine)`` query, however the session runs it
    (``session.scalar(select(Machine)...)``,
    ``session.execute(select(Machine)).scalar_one()``). ``refresh`` binds
    no new name, so the row it re-reads is tracked by its own binding."""
    if value is None:
        return False
    for node in ast.walk(value):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in _MACHINE_LOADERS or name == "Machine":
            return True
        if name in _MACHINE_ROW_CALLS and _first_arg_is_machine(node):
            return True
    return False


def _bound_names(target: ast.expr) -> set[str]:
    return {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}


def _machine_names(function: ast.FunctionDef) -> set[str]:
    """Parameters annotated as a Machine, locals bound from a Machine load,
    and loop or comprehension targets iterating Machine rows."""
    names = {
        arg.arg
        for arg in (*function.args.args, *function.args.kwonlyargs)
        if arg.annotation is not None and "Machine" in ast.unparse(arg.annotation).split(" | ")
    }

    def yields_machines(value: ast.expr | None) -> bool:
        return _is_machine_load(value) or (isinstance(value, ast.Name) and value.id in names)

    # Repeat until stable: a loop over ``listed`` is a Machine loop only
    # once ``listed = list_machines(...)`` has been seen.
    while True:
        found = set(names)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and yields_machines(node.value):
                found |= {target.id for target in node.targets if isinstance(target, ast.Name)}
            elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and yields_machines(node.value):
                if isinstance(node.target, ast.Name):
                    found.add(node.target.id)
            elif isinstance(node, ast.For | ast.comprehension) and yields_machines(node.iter):
                found |= _bound_names(node.target)
        if found == names:
            return names
        names = found


def _writes_machine(function: ast.FunctionDef) -> bool:
    """Constructs a Machine, bulk-writes the Machine table
    (``update``/``insert``/``delete(Machine)``) or assigns an attribute of
    a Machine row ``_machine_names`` tracks."""
    names = _machine_names(function)
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and (
            _call_name(node) == "Machine"
            or (_call_name(node) in _MACHINE_BULK_WRITES and _first_arg_is_machine(node))
        ):
            return True
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in names
            ):
                return True
    return False


def _calls_append_audit_event(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append_audit_event"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "audit"
        for node in ast.walk(function)
    )


def _callers(functions: list[ast.FunctionDef], callee: str) -> set[str]:
    return {
        function.name
        for function in functions
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == callee
    }


def _other_modules() -> list[Path]:
    return [
        path
        for path in sorted(_APP_DIR.rglob("*.py"))
        if path != _MACHINE_SERVICE and path != _APP_DIR / "infrastructure" / "models.py"
    ]


# Function bodies, one statement per item, for the detector tests below.
_WRITER_SHAPES: dict[str, tuple[str, ...]] = {
    "get_one": ("m = session.get_one(Machine, 1)", "m.notes = 'x'"),
    "get": ("m = session.get(Machine, 1)", "m.notes = 'x'"),
    "scalar_select": (
        "m = session.scalar(select(Machine).where(Machine.id == 1))",
        "m.notes = 'x'",
    ),
    "execute_scalar_one": (
        "m = session.execute(select(Machine).where(Machine.id == 1)).scalar_one()",
        "m.notes = 'x'",
    ),
    "annotated_local": ("m: Machine = session.get_one(Machine, 1)", "m.notes = 'x'"),
    "loop_over_loader": ("for m in list_machines(session):", "    m.notes = 'x'"),
    "loop_over_bound_rows": (
        "listed = machines.list_machines(session)",
        "for m in listed:",
        "    m.notes = 'x'",
    ),
    "loop_over_scalars": ("for m in session.scalars(select(Machine)):", "    m.notes += 'x'"),
    "bulk_update": ("session.execute(update(Machine).where(Machine.id == 1).values(notes='x'))",),
    "bulk_insert": ("session.execute(insert(Machine).values(name='x'))",),
    "bulk_delete": ("session.execute(delete(Machine).where(Machine.id == 1))",),
    "construction": ("session.add(Machine(name='x'))",),
}
_READ_ONLY_SHAPES: dict[str, tuple[str, ...]] = {
    "read_attribute": ("m = session.get_one(Machine, 1)", "return m.notes"),
    "other_entity": ("area = session.get_one(Area, 1)", "area.name = 'x'"),
    "column_query": ("ids = session.scalars(select(Machine.id))", "total = len(list(ids))"),
    "session_delete_of_other": ("session.delete(area)",),
}


def _function_of(body: tuple[str, ...]) -> ast.FunctionDef:
    source = "\n".join(("def writer(session):", *(f"    {line}" for line in body)))
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    return function


@pytest.mark.parametrize("shape", sorted(_WRITER_SHAPES))
def test_machine_writer_detection_flags_every_load_and_bulk_write_shape(shape: str) -> None:
    """The M-15 guard's detector recognizes each way a function can load
    and write a Machine, so an unaudited writer cannot pass it by using a
    session lookup, a ``select(Machine)`` query, a loop or a bulk write."""
    assert _writes_machine(_function_of(_WRITER_SHAPES[shape]))


@pytest.mark.parametrize("shape", sorted(_READ_ONLY_SHAPES))
def test_machine_writer_detection_ignores_reads_and_other_entities(shape: str) -> None:
    assert not _writes_machine(_function_of(_READ_ONLY_SHAPES[shape]))


def test_every_machine_writer_appends_an_audit_row() -> None:
    """(a) Every audited service calls ``audit.append_audit_event``; any
    other function that writes a Machine is either named with a reason
    or a private helper called only from audited services. (b) No other
    module constructs, bulk-writes or assigns to a Machine. (c) The
    snapshot keys are exactly the audited twelve. "Writes" is what
    ``_writes_machine`` detects: construction, ``update``/``insert``/
    ``delete(Machine)``, and attribute assignment on a row bound from a
    Machine parameter, loader, session lookup, ``select(Machine)`` query
    or a loop over those (the ``test_machine_writer_detection_*`` tests pin each)."""
    tree = ast.parse(_MACHINE_SERVICE.read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    by_name = {function.name: function for function in functions}
    other_sources = {path: path.read_text(encoding="utf-8") for path in _other_modules()}

    # (a)
    assert set(by_name) >= _AUDITED
    assert {name for name in _AUDITED if not _calls_append_audit_event(by_name[name])} == set()
    offenders = []
    for function in functions:
        if not _writes_machine(function) or function.name in _AUDITED:
            continue
        if function.name in _NON_AUDITED_MACHINE_WRITERS:
            continue
        callers = _callers(functions, function.name)
        referenced_elsewhere = any(function.name in source for source in other_sources.values())
        if not (
            function.name.startswith("_")
            and callers
            and callers <= _AUDITED
            and not referenced_elsewhere
        ):
            offenders.append(function.name)
    assert offenders == []
    assert _callers(functions, "_apply_edits") == {"update_machine", "retire_machine"}
    assert _writes_machine(by_name["note_assignment_change"])

    # (b)
    foreign = []
    for path, source in other_sources.items():
        for function in _functions(ast.parse(source)):
            if _writes_machine(function):
                foreign.append(f"{path.name}:{function.name}")
    assert foreign == []

    # (c)
    snapshot = by_name["_machine_snapshot"]
    keys = {
        key.value
        for node in ast.walk(snapshot)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant)
    }
    assert keys == _SNAPSHOT_KEYS
    assert not keys & {"retired_on", "state_changed_at", "created_at", "updated_at"}
