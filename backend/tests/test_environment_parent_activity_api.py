"""Integration tests for the Phase 13 slice 2c parent-activity locks.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Seven child writes judge
their parent's active flag (Phase 3.5 "new configuration only
references active entities"): Area create and Area activation (parent
Department); Operation create, Scan Station create and rebind, Machine
create and Machine reactivation in place or moved (parent Area). Each
judgment is made on the parent row locked FOR SHARE and re-read under
it, held until COMMIT, so the child write and a concurrent deactivation
of the parent have exactly one serial outcome:

- the deactivation first: the child waits for it, then refuses with the
  existing 409 and writes nothing — or proceeds after its rollback;
- the child first: a Department deactivation waits for it and then
  refuses with the existing "still has active Areas" 409, and an Area
  deactivation waits for it and then proceeds;
- an in-place Machine reactivation waits behind a production
  FOR UPDATE on its Area and then proceeds;
- the parent lock never waits on FOR SHARE or FOR KEY SHARE, so
  concurrent child writes under one parent and FK checks never
  serialize on it.

A deactivation holder is a plain ``UPDATE … SET is_active = false``:
the weakest lock any deactivation takes (FOR NO KEY UPDATE), so passing
against it covers every deactivation path. The development database in
DATABASE_URL is only used as the admin connection for CREATE/DROP
DATABASE.
"""

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
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import audit
from app.core.config import get_settings
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_env_parent_activity"
_ASSET_TAG_PREFIX = "PA-"
_ASSET_TAG_DIGITS = 4
_COUNTED_TABLES = (
    "areas",
    "operations",
    "scan_stations",
    "machines",
    "machine_lifecycle_events",
    "audit_events",
)
# Tables whose names are interpolated into the raw holder statements.
_PARENT_TABLES = frozenset({"departments", "areas"})
_ROW_LOCKS = frozenset({"FOR UPDATE", "FOR SHARE", "FOR KEY SHARE"})
_STILL_HAS_ACTIVE_AREAS = (
    "This Department still has active Areas."
    " Deactivate its Areas first, then deactivate the Department."
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


def _start_client(database_url: URL) -> Iterator[TestClient]:
    """Application client on ``database_url`` through the real startup
    path (DATABASE_URL pointed at it, settings re-read)."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    yield from _start_client(api_database_url)


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
        json={"prefix": _ASSET_TAG_PREFIX, "digits": _ASSET_TAG_DIGITS},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _ok(response: Response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


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


def _join(threads: list[threading.Thread]) -> None:
    """Join every started request thread, so none outlives its test."""
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()


def _create_department(client: TestClient) -> dict[str, Any]:
    return _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)


def _create_area(client: TestClient, department_id: int | None = None) -> dict[str, Any]:
    if department_id is None:
        department_id = int(_create_department(client)["id"])
    payload = {"department_id": department_id, "name": _unique("AREA")}
    return _ok(client.post("/api/areas", json=payload), 201)


def _deactivate_area(client: TestClient, area_id: int) -> None:
    _ok(client.patch(f"/api/areas/{area_id}", json={"is_active": False}))


def _create_retired_machine(client: TestClient, area_id: int) -> dict[str, Any]:
    payload = {"area_id": area_id, "name": _unique("MACHINE")}
    machine = _ok(client.post("/api/machines", json=payload), 201)
    return _ok(client.post(f"/api/machines/{machine['id']}/retire", json={"reason": "Retired"}))


def _deactivate_uncommitted(holder: Connection, table: str, row_id: int) -> None:
    """A plain deactivation UPDATE left uncommitted on ``holder``: the
    implicit FOR NO KEY UPDATE, the weakest lock any deactivation takes."""
    assert table in _PARENT_TABLES
    holder.execute(
        sa.text(f"UPDATE {table} SET is_active = false, updated_at = now() WHERE id = :id"),
        {"id": row_id},
    )


def _hold_row(holder: Connection, table: str, row_id: int, lock: str) -> None:
    """Hold a row lock on ``holder`` without changing the row."""
    assert table in _PARENT_TABLES
    assert lock in _ROW_LOCKS
    holder.execute(sa.text(f"SELECT 1 FROM {table} WHERE id = :id {lock}"), {"id": row_id})


def _hold_for_update(holder: Connection, table: str, row_id: int) -> None:
    """The production lock mode on an Area row (intake, transfers, undo,
    production release), changing nothing."""
    _hold_row(holder, table, row_id, "FOR UPDATE")


def _counts(engine: Engine) -> dict[str, int]:
    """Row counts of every table a child write touches, plus the Asset
    Tag never-reuse counter."""
    with engine.connect() as connection:
        counts = {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED_TABLES
        }
        counts["next_sequence"] = int(
            connection.execute(
                sa.text("SELECT next_sequence FROM machine_asset_tag_config")
            ).scalar_one()
        )
    return counts


def _value(engine: Engine, table: str, key_column: str, key: object, column: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(f"SELECT {column} FROM {table} WHERE {key_column} = :key"), {"key": key}
        ).scalar_one()


def _latest_audit_row(engine: Engine) -> sa.Row[Any]:
    with engine.connect() as connection:
        return connection.execute(
            sa.text(
                "SELECT entity_type, event_type, entity_id, before_data, after_data"
                " FROM audit_events ORDER BY id DESC LIMIT 1"
            )
        ).one()


def _audit_events_of(engine: Engine, entity_type: str, entity_id: object) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, before_data, after_data FROM audit_events"
                    " WHERE entity_type = :entity_type AND entity_id = :entity_id ORDER BY id"
                ),
                {"entity_type": entity_type, "entity_id": str(entity_id)},
            )
        )


def _lifecycle_events(engine: Engine, machine_id: int) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, from_area_id, to_area_id FROM machine_lifecycle_events"
                    " WHERE machine_id = :machine_id ORDER BY id"
                ),
                {"machine_id": machine_id},
            )
        )


def _gate(
    monkeypatch: pytest.MonkeyPatch, entity_type: str, event_type: str
) -> tuple[threading.Event, threading.Event]:
    """Park the request that appends a matching audit row, inside its
    transaction and after its parent judgment, until ``release`` is set.

    A gate that is never released raises instead of delegating, so the
    parked request rolls back and never commits into a later test.
    """
    entered = threading.Event()
    release = threading.Event()
    append = audit.append_audit_event

    def gated(session: Session, **fields: Any) -> None:
        if fields["entity_type"] == entity_type and fields["event_type"] == event_type:
            entered.set()
            if not release.wait(timeout=30):
                raise RuntimeError("The audit gate was never released.")
        append(session, **fields)

    monkeypatch.setattr("app.application.audit.append_audit_event", gated)
    return entered, release


# ---------------------------------------------------------------------------
# The seven child paths (eight cases): explicit per-case data
# ---------------------------------------------------------------------------


class _Child(NamedTuple):
    """One child write prepared against fresh entities."""

    parent_table: str
    parent_id: int
    method: str
    path: str
    body: dict[str, Any]
    # Exact 409 detail once the parent is inactive.
    refusal: str
    # Status and _counts change when the write proceeds.
    status: int
    deltas: dict[str, int]
    # (entity_type, event_type) of the one audit row it appends, if any.
    audit: tuple[str, str] | None
    # Asserts the child state a refusal leaves unchanged.
    refused: Callable[[], None]
    # Asserts the state after the write proceeded (given its response body).
    proceeded: Callable[[dict[str, Any]], None]


def _send(client: TestClient, child: _Child) -> Response:
    response: Response = client.request(child.method, child.path, json=child.body)
    return response


def _area_create(client: TestClient, engine: Engine) -> _Child:
    department = _create_department(client)

    def proceeded(body: dict[str, Any]) -> None:
        assert body["department_id"] == department["id"]
        assert body["is_active"] is True

    return _Child(
        parent_table="departments",
        parent_id=int(department["id"]),
        method="POST",
        path="/api/areas",
        body={"department_id": department["id"], "name": _unique("AREA")},
        refusal=f"Department '{department['name']}' is inactive and cannot receive new Areas.",
        status=201,
        deltas={"areas": 1, "audit_events": 1},
        audit=("Area", "CREATED"),
        refused=lambda: None,
        proceeded=proceeded,
    )


def _area_activation(client: TestClient, engine: Engine) -> _Child:
    department = _create_department(client)
    area = _create_area(client, int(department["id"]))
    _deactivate_area(client, int(area["id"]))

    def refused() -> None:
        assert _value(engine, "areas", "id", area["id"], "is_active") is False

    def proceeded(body: dict[str, Any]) -> None:
        assert _value(engine, "areas", "id", area["id"], "is_active") is True

    return _Child(
        parent_table="departments",
        parent_id=int(department["id"]),
        method="PATCH",
        path=f"/api/areas/{area['id']}",
        body={"is_active": True},
        refusal=(
            f"Department '{department['name']}' is inactive."
            " Activate the Department before activating this Area."
        ),
        status=200,
        deltas={"audit_events": 1},
        audit=("Area", "UPDATED"),
        refused=refused,
        proceeded=proceeded,
    )


def _operation_create(client: TestClient, engine: Engine) -> _Child:
    area = _create_area(client)

    def proceeded(body: dict[str, Any]) -> None:
        assert body["area_id"] == area["id"]

    return _Child(
        parent_table="areas",
        parent_id=int(area["id"]),
        method="POST",
        path="/api/operations",
        body={"area_id": area["id"], "code": _unique("OP")},
        refusal=f"Area '{area['name']}' is inactive and cannot receive new Operations.",
        status=201,
        deltas={"operations": 1, "audit_events": 1},
        audit=("Operation", "CREATED"),
        refused=lambda: None,
        proceeded=proceeded,
    )


def _station_create(client: TestClient, engine: Engine) -> _Child:
    area = _create_area(client)

    def proceeded(body: dict[str, Any]) -> None:
        assert body["area_id"] == area["id"]

    return _Child(
        parent_table="areas",
        parent_id=int(area["id"]),
        method="POST",
        path="/api/scan-stations",
        body={"station_id": _unique("ST"), "area_id": area["id"]},
        refusal=f"Area '{area['name']}' is inactive and cannot receive new Scan Stations.",
        status=201,
        deltas={"scan_stations": 1, "audit_events": 1},
        audit=("ScanStation", "CREATED"),
        refused=lambda: None,
        proceeded=proceeded,
    )


def _station_rebind(client: TestClient, engine: Engine) -> _Child:
    area_a = _create_area(client)
    area_b = _create_area(client)
    station = _ok(
        client.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": area_a["id"]}
        ),
        201,
    )

    def bound_area() -> Any:
        return _value(engine, "scan_stations", "station_id", station["station_id"], "area_id")

    def refused() -> None:
        assert bound_area() == area_a["id"]

    def proceeded(body: dict[str, Any]) -> None:
        assert bound_area() == area_b["id"]

    return _Child(
        parent_table="areas",
        parent_id=int(area_b["id"]),
        method="PATCH",
        path=f"/api/scan-stations/{station['station_id']}",
        body={"area_id": area_b["id"]},
        refusal=f"Area '{area_b['name']}' is inactive and cannot receive Scan Stations.",
        status=200,
        deltas={"audit_events": 1},
        audit=("ScanStation", "UPDATED"),
        refused=refused,
        proceeded=proceeded,
    )


def _machine_create(client: TestClient, engine: Engine) -> _Child:
    area = _create_area(client)
    sequence = int(_value(engine, "machine_asset_tag_config", "id", 1, "next_sequence"))

    def proceeded(body: dict[str, Any]) -> None:
        assert body["area_id"] == area["id"]
        assert body["asset_tag"] == f"{_ASSET_TAG_PREFIX}{sequence:0{_ASSET_TAG_DIGITS}d}"

    return _Child(
        parent_table="areas",
        parent_id=int(area["id"]),
        method="POST",
        path="/api/machines",
        body={"area_id": area["id"], "name": _unique("MACHINE")},
        refusal=f"Area '{area['name']}' is inactive and cannot receive new Machines.",
        status=201,
        deltas={"machines": 1, "audit_events": 1, "next_sequence": 1},
        audit=("Machine", "CREATED"),
        refused=lambda: None,
        proceeded=proceeded,
    )


def _retired_machine_refused(engine: Engine, machine_id: int, area_id: int) -> Callable[[], None]:
    retired_on = _value(engine, "machines", "id", machine_id, "retired_on")
    assert retired_on is not None

    def refused() -> None:
        assert _value(engine, "machines", "id", machine_id, "retired_on") == retired_on
        assert _value(engine, "machines", "id", machine_id, "area_id") == area_id

    return refused


def _machine_reactivate_in_place(client: TestClient, engine: Engine) -> _Child:
    area = _create_area(client)
    machine = _create_retired_machine(client, int(area["id"]))
    machine_id = int(machine["id"])

    def proceeded(body: dict[str, Any]) -> None:
        assert _value(engine, "machines", "id", machine_id, "retired_on") is None
        assert _value(engine, "machines", "id", machine_id, "area_id") == area["id"]
        events = _lifecycle_events(engine, machine_id)
        assert [tuple(event) for event in events] == [
            ("RETIRED", None, None),
            ("REACTIVATED", None, None),
        ]

    return _Child(
        parent_table="areas",
        parent_id=int(area["id"]),
        method="POST",
        path=f"/api/machines/{machine_id}/reactivate",
        body={"reason": "Back in service"},
        refusal=f"Area '{area['name']}' is inactive and cannot receive reactivated Machines.",
        status=200,
        # No rename, no Area move, no maintenance context: no configuration
        # delta, so the lifecycle event is the only record.
        deltas={"machine_lifecycle_events": 1},
        audit=None,
        refused=_retired_machine_refused(engine, machine_id, int(area["id"])),
        proceeded=proceeded,
    )


def _machine_reactivate_moved(client: TestClient, engine: Engine) -> _Child:
    area_a = _create_area(client)
    area_b = _create_area(client)
    machine = _create_retired_machine(client, int(area_a["id"]))
    machine_id = int(machine["id"])

    def proceeded(body: dict[str, Any]) -> None:
        assert _value(engine, "machines", "id", machine_id, "retired_on") is None
        assert _value(engine, "machines", "id", machine_id, "area_id") == area_b["id"]
        events = _lifecycle_events(engine, machine_id)
        assert [tuple(event) for event in events] == [
            ("RETIRED", None, None),
            ("REACTIVATED", area_a["id"], area_b["id"]),
        ]
        row = _latest_audit_row(engine)
        assert row.entity_id == str(machine_id)
        assert row.before_data["area_id"] == area_a["id"]
        assert row.after_data["area_id"] == area_b["id"]

    return _Child(
        parent_table="areas",
        parent_id=int(area_b["id"]),
        method="POST",
        path=f"/api/machines/{machine_id}/reactivate",
        body={"reason": "Moved while retired", "area_id": area_b["id"]},
        refusal=f"Area '{area_b['name']}' is inactive and cannot receive reactivated Machines.",
        status=200,
        deltas={"machine_lifecycle_events": 1, "audit_events": 1},
        audit=("Machine", "UPDATED"),
        refused=_retired_machine_refused(engine, machine_id, int(area_a["id"])),
        proceeded=proceeded,
    )


_CHILDREN: dict[str, Callable[[TestClient, Engine], _Child]] = {
    "area-create": _area_create,
    "area-activation": _area_activation,
    "operation-create": _operation_create,
    "station-create": _station_create,
    "station-rebind": _station_rebind,
    "machine-create": _machine_create,
    "machine-reactivate-in-place": _machine_reactivate_in_place,
    "machine-reactivate-moved": _machine_reactivate_moved,
}


def _assert_proceeded(
    engine: Engine, child: _Child, response: Response, before: dict[str, int]
) -> None:
    assert response.status_code == child.status, response.text
    expected = {key: value + child.deltas.get(key, 0) for key, value in before.items()}
    assert _counts(engine) == expected
    if child.audit is not None:
        row = _latest_audit_row(engine)
        assert (row.entity_type, row.event_type) == child.audit
    child.proceeded(cast(dict[str, Any], response.json()))


# ---------------------------------------------------------------------------
# T-1 / T-2: the parent deactivation is in flight first
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(_CHILDREN))
def test_child_write_waits_for_an_in_flight_parent_deactivation_and_is_refused(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    child = _CHILDREN[case](client, db_engine)
    before = _counts(db_engine)
    with db_engine.connect() as holder:
        _deactivate_uncommitted(holder, child.parent_table, child.parent_id)
        thread, results = _start(lambda: _send(client, child))
        try:
            # Waits on the parent FOR SHARE behind the uncommitted UPDATE.
            _assert_blocked(thread)
        finally:
            holder.commit()
            _join([thread])
    response = _finish(thread, results)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == child.refusal
    assert _counts(db_engine) == before
    child.refused()


@pytest.mark.parametrize("case", list(_CHILDREN))
def test_child_write_proceeds_when_the_parent_edit_rolls_back(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    child = _CHILDREN[case](client, db_engine)
    before = _counts(db_engine)
    with db_engine.connect() as holder:
        _deactivate_uncommitted(holder, child.parent_table, child.parent_id)
        thread, results = _start(lambda: _send(client, child))
        try:
            _assert_blocked(thread)
        finally:
            holder.rollback()
            _join([thread])
    response = _finish(thread, results)

    _assert_proceeded(db_engine, child, response, before)


# ---------------------------------------------------------------------------
# T-3..T-5: the child write is in flight first
# ---------------------------------------------------------------------------


def test_department_deactivation_waits_for_an_in_flight_area_create_and_refuses(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    department = _create_department(client)
    entered, release = _gate(monkeypatch, "Area", "CREATED")
    threads: list[threading.Thread] = []
    create, created = _start(
        lambda: client.post(
            "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
        )
    )
    threads.append(create)
    try:
        assert entered.wait(timeout=20)
        deactivate, deactivated = _start(
            lambda: client.patch(f"/api/departments/{department['id']}", json={"is_active": False})
        )
        threads.append(deactivate)
        # Waits on the in-flight Area create's Department FOR SHARE.
        _assert_blocked(deactivate)
    finally:
        release.set()
        _join(threads)

    assert _finish(create, created).status_code == 201
    response = _finish(deactivate, deactivated)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == _STILL_HAS_ACTIVE_AREAS
    assert _value(db_engine, "departments", "id", department["id"], "is_active") is True
    rows = _audit_events_of(db_engine, "Department", department["id"])
    assert [row.event_type for row in rows] == ["CREATED"]


def test_department_deactivation_waits_for_an_in_flight_area_activation_and_refuses(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    department = _create_department(client)
    area = _create_area(client, int(department["id"]))
    _deactivate_area(client, int(area["id"]))
    entered, release = _gate(monkeypatch, "Area", "UPDATED")
    threads: list[threading.Thread] = []
    activate, activated = _start(
        lambda: client.patch(f"/api/areas/{area['id']}", json={"is_active": True})
    )
    threads.append(activate)
    try:
        assert entered.wait(timeout=20)
        deactivate, deactivated = _start(
            lambda: client.patch(f"/api/departments/{department['id']}", json={"is_active": False})
        )
        threads.append(deactivate)
        # Waits on the in-flight activation's Department FOR SHARE.
        _assert_blocked(deactivate)
    finally:
        release.set()
        _join(threads)

    assert _finish(activate, activated).status_code == 200
    response = _finish(deactivate, deactivated)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == _STILL_HAS_ACTIVE_AREAS
    assert _value(db_engine, "departments", "id", department["id"], "is_active") is True
    assert _value(db_engine, "areas", "id", area["id"], "is_active") is True


def test_area_deactivation_waits_for_an_in_flight_machine_reactivation(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    area = _create_area(client)
    machine = _create_retired_machine(client, int(area["id"]))
    machine_id = int(machine["id"])
    # The rename is a configuration delta, so the reactivation appends a
    # Machine UPDATED row and passes through the gate.
    entered, release = _gate(monkeypatch, "Machine", "UPDATED")
    threads: list[threading.Thread] = []
    reactivate, reactivated = _start(
        lambda: client.post(
            f"/api/machines/{machine_id}/reactivate",
            json={"reason": "Back in service", "name": _unique("MACHINE")},
        )
    )
    threads.append(reactivate)
    try:
        assert entered.wait(timeout=20)
        deactivate, deactivated = _start(
            lambda: client.patch(f"/api/areas/{area['id']}", json={"is_active": False})
        )
        threads.append(deactivate)
        # Waits on the in-flight reactivation's Area FOR SHARE.
        _assert_blocked(deactivate)
    finally:
        release.set()
        _join(threads)

    # One serial outcome: the reactivation, then the deactivation.
    assert _finish(reactivate, reactivated).status_code == 200
    response = _finish(deactivate, deactivated)
    assert response.status_code == 200, response.text
    events = _lifecycle_events(db_engine, machine_id)
    assert [event.event_type for event in events].count("REACTIVATED") == 1
    rows = _audit_events_of(db_engine, "Area", area["id"])
    assert rows[-1].event_type == "UPDATED"
    assert rows[-1].before_data["is_active"] is True
    assert rows[-1].after_data["is_active"] is False


# ---------------------------------------------------------------------------
# T-6: the parent lock never waits on FOR SHARE or FOR KEY SHARE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "lock"),
    [
        pytest.param("area-create", "FOR SHARE", id="department-share"),
        pytest.param("area-create", "FOR KEY SHARE", id="department-key-share"),
        pytest.param("area-activation", "FOR SHARE", id="department-share-activation"),
        pytest.param("area-activation", "FOR KEY SHARE", id="department-key-share-activation"),
        pytest.param("station-create", "FOR SHARE", id="area-share"),
        pytest.param("station-create", "FOR KEY SHARE", id="area-key-share"),
        pytest.param("machine-create", "FOR SHARE", id="area-share-machine-create"),
    ],
)
def test_parent_lock_never_waits_on_share_or_key_share(
    client: TestClient, db_engine: Engine, case: str, lock: str
) -> None:
    child = _CHILDREN[case](client, db_engine)
    before = _counts(db_engine)
    with db_engine.connect() as holder:
        _hold_row(holder, child.parent_table, child.parent_id, lock)
        thread, results = _start(lambda: _send(client, child))
        try:
            # Completes while the holder is still open.
            thread.join(timeout=5)
            assert not thread.is_alive()
        finally:
            holder.commit()
            _join([thread])
    response = _finish(thread, results)

    _assert_proceeded(db_engine, child, response, before)


# ---------------------------------------------------------------------------
# T-7: a production FOR UPDATE on the Area delays the child, never deadlocks it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", ["machine-reactivate-in-place", "station-rebind"])
def test_child_write_waits_behind_a_production_area_lock_then_proceeds(
    client: TestClient, db_engine: Engine, case: str
) -> None:
    child = _CHILDREN[case](client, db_engine)
    before = _counts(db_engine)
    with db_engine.connect() as holder:
        _hold_for_update(holder, child.parent_table, child.parent_id)
        thread, results = _start(lambda: _send(client, child))
        try:
            _assert_blocked(thread)
        finally:
            holder.commit()
            _join([thread])
    response = _finish(thread, results)

    _assert_proceeded(db_engine, child, response, before)
