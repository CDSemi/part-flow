"""Integration tests for Phase 13 slice 10 — the Scan Station theme preference.

Exercises the full request path — FastAPI routes, the Application
service and PostgreSQL — against a dedicated temporary database migrated
to head by the real Alembic chain (GUI_DESIGN §2.1 station tier; PLAN
CD2, CD3; owner default OD-13):

- the station context reports the saved preference (null = none);
- ``PUT /api/scan-stations/{id}/theme-preference`` saves an absolute
  ``DARK`` / ``LIGHT`` value and echoes the value this request saved or
  kept; a repeat performs no UPDATE (``xmin`` unchanged);
- the write is never audited and never changes ``updated_at``; invalid
  bodies are 422 and an unknown station 404, each with nothing written;
- any existing station saves it, active or not, on an active or
  inactive Area;
- the service refuses a non-member value (E2) and returns the validated
  member, never a post-COMMIT re-read;
- locking: the write waits behind a production command's station lock,
  never on an FK check's FOR KEY SHARE, and a production command waits
  behind an uncommitted theme write and then succeeds unchanged;
- configuration, production commands, Worker Session events and the
  badge-confirmation gate never change the saved preference (R62).

The API commits real transactions, so tests isolate through unique
Areas, stations, PNs and Workers; the module database is dropped
afterwards. Helpers are copied from the slice 2 and slice 5 modules
(each module owns its own).
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
from sqlalchemy.orm import Session

from alembic import command
from app.application import environment
from app.application.errors import InvalidInputError
from app.core.config import get_settings
from app.domain.enums import ThemePreference
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_station_theme_api"
_DB_URL_ENV = "DATABASE_URL"

_E1 = "Scan Station '{station_id}' does not exist."
_E2 = "Theme preference must be DARK or LIGHT."
_E_S1 = (
    "No Worker is signed in at this Scan Station. Scan your badge to continue."
    " Nothing was recorded."
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
    """Direct database access for assertions and concurrent holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


class _Cell:
    """An Area without Machines, with one Operation and one Scan Station."""

    def __init__(self, client: TestClient) -> None:
        department = _ok(client.post("/api/departments", json={"name": _unique("DEPT")}), 201)
        area = _ok(
            client.post(
                "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
            ),
            201,
        )
        self.area_id = int(area["id"])
        operation = _ok(
            client.post("/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}),
            201,
        )
        self.operation_id = int(operation["id"])
        station = _ok(
            client.post(
                "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
            ),
            201,
        )
        self.station_id = str(station["station_id"])


def _worker(client: TestClient) -> dict[str, Any]:
    return _ok(
        client.post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )


def _release(client: TestClient, cell: _Cell, quantity: int = 10) -> tuple[int, str]:
    """Management releases ``quantity`` of a new PN into ``cell`` (no station)."""
    pn = _unique("PN")
    work_order = _ok(
        client.post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
        ),
        201,
    )
    released = _ok(
        client.post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": pn,
                "quantity": quantity,
                "route_mode": "FLOATING",
                "starting_area_id": cell.area_id,
                "operation_id": cell.operation_id,
                "confirm_active_quantity": False,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )
    return int(released["quantity_flow_id"]), pn


def _transfer(
    client: TestClient, source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int = 10
) -> Any:
    return client.post(
        f"/api/scan-stations/{target.station_id}/transfers",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": source.area_id,
            "target_area_id": target.area_id,
            "quantity": quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _undo(client: TestClient, cell: _Cell, pn: str, reverses: str, **extra: Any) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/undos",
        json={
            "part_number": pn,
            "reverses_device_event_id": reverses,
            "device_event_id": str(uuid.uuid4()),
            **extra,
        },
    )


def _badge_scan(client: TestClient, cell: _Cell, worker: dict[str, Any]) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/scan-stations/{cell.station_id}/badge-scans",
            json={"badge": worker["badge_barcode"]},
        )
    )


def _insert_expired_session(engine: Engine, cell: _Cell, worker_id: int) -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(
                sa.text(
                    "INSERT INTO worker_sessions (station_id, area_id, worker_id, started_at,"
                    " expires_at) VALUES (:station, :area, :worker, now() - interval '20 minutes',"
                    " now() - interval '5 minutes') RETURNING id"
                ),
                {"station": cell.station_id, "area": cell.area_id, "worker": worker_id},
            ).scalar_one()
        )


# ---------------------------------------------------------------------------
# Theme helpers
# ---------------------------------------------------------------------------


def _path(station_id: str) -> str:
    return f"/api/scan-stations/{station_id}/theme-preference"


def _save(client: TestClient, station_id: str, theme: str) -> dict[str, Any]:
    return _ok(client.put(_path(station_id), json={"theme_preference": theme}))


def _context(client: TestClient, station_id: str) -> dict[str, Any]:
    return _ok(client.get(f"/api/scan-stations/{station_id}/context"))


class _StationRow:
    def __init__(self, row: sa.Row[Any]) -> None:
        self.theme_preference: str | None = row.theme_preference
        self.updated_at: Any = row.updated_at
        self.xmin: str = row.xmin
        self.area_id: int = row.area_id
        self.is_active: bool = row.is_active


def _station(engine: Engine, station_id: str) -> _StationRow:
    with engine.connect() as connection:
        return _StationRow(
            connection.execute(
                sa.text(
                    "SELECT theme_preference, updated_at, xmin::text AS xmin, area_id, is_active"
                    " FROM scan_stations WHERE station_id = :station"
                ),
                {"station": station_id},
            ).one()
        )


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())


def _station_audits(engine: Engine, station_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT before_data, after_data FROM audit_events"
                    " WHERE entity_type = 'ScanStation' AND entity_id = :station ORDER BY id"
                ),
                {"station": station_id},
            )
        )


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
# Context and PUT
# ---------------------------------------------------------------------------


def test_a_new_station_has_no_preference(client: TestClient) -> None:
    cell = _Cell(client)
    assert _context(client, cell.station_id)["theme_preference"] is None


def test_put_saves_each_value_and_the_context_reports_it(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    for theme in ("LIGHT", "DARK"):
        assert _save(client, cell.station_id, theme) == {
            "station_id": cell.station_id,
            "theme_preference": theme,
        }
        assert _context(client, cell.station_id)["theme_preference"] == theme
        assert _station(db_engine, cell.station_id).theme_preference == theme


def test_a_repeat_is_a_no_op_without_an_update(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    first = _save(client, cell.station_id, "LIGHT")
    after_first = _station(db_engine, cell.station_id)
    second = _save(client, cell.station_id, "LIGHT")
    after_second = _station(db_engine, cell.station_id)
    assert first == second == {"station_id": cell.station_id, "theme_preference": "LIGHT"}
    assert after_second.theme_preference == "LIGHT"
    # A row lock leaves xmin alone; any UPDATE would change it.
    assert after_second.xmin == after_first.xmin


def test_the_write_is_not_audited_and_keeps_updated_at(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    before = _station(db_engine, cell.station_id)
    audits = _count(db_engine, "audit_events")
    for theme in ("LIGHT", "DARK", "LIGHT", "LIGHT"):
        _save(client, cell.station_id, theme)
    after = _station(db_engine, cell.station_id)
    assert _count(db_engine, "audit_events") == audits
    assert after.updated_at == before.updated_at
    assert after.theme_preference == "LIGHT"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"theme_preference": None},
        {"theme_preference": "light"},
        {"theme_preference": "Dark"},
        {"theme_preference": "AUTO"},
        {"theme_preference": ""},
        {"theme_preference": 1},
        {"theme_preference": "DARK", "station_id": "S2"},
    ],
    ids=["empty", "null", "lower", "mixed", "unknown", "blank", "number", "extra"],
)
def test_invalid_bodies_are_refused_with_nothing_written(
    client: TestClient, db_engine: Engine, body: dict[str, Any]
) -> None:
    cell = _Cell(client)
    _save(client, cell.station_id, "LIGHT")
    before = _station(db_engine, cell.station_id)
    audits = _count(db_engine, "audit_events")
    response = client.put(_path(cell.station_id), json=body)
    assert response.status_code == 422, response.text
    after = _station(db_engine, cell.station_id)
    assert (after.theme_preference, after.xmin) == ("LIGHT", before.xmin)
    assert _count(db_engine, "audit_events") == audits


def test_an_unknown_station_is_404_with_nothing_created(
    client: TestClient, db_engine: Engine
) -> None:
    stations = _count(db_engine, "scan_stations")
    response = client.put(_path("NOPE"), json={"theme_preference": "LIGHT"})
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": _E1.format(station_id="NOPE")}
    assert _count(db_engine, "scan_stations") == stations


def test_an_inactive_station_or_area_still_saves(client: TestClient, db_engine: Engine) -> None:
    inactive_station = _Cell(client)
    _ok(
        client.patch(f"/api/scan-stations/{inactive_station.station_id}", json={"is_active": False})
    )
    assert _save(client, inactive_station.station_id, "LIGHT")["theme_preference"] == "LIGHT"
    assert _station(db_engine, inactive_station.station_id).theme_preference == "LIGHT"
    response = client.get(f"/api/scan-stations/{inactive_station.station_id}/context")
    assert response.status_code == 409, response.text

    inactive_area = _Cell(client)
    _ok(client.patch(f"/api/areas/{inactive_area.area_id}", json={"is_active": False}))
    assert _save(client, inactive_area.station_id, "DARK")["theme_preference"] == "DARK"
    assert _station(db_engine, inactive_area.station_id).theme_preference == "DARK"
    response = client.get(f"/api/scan-stations/{inactive_area.station_id}/context")
    assert response.status_code == 409, response.text


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["BLUE", None, "light", 1])
def test_the_service_refuses_a_non_member(
    client: TestClient, db_engine: Engine, value: object
) -> None:
    cell = _Cell(client)
    before = _station(db_engine, cell.station_id)
    with Session(db_engine) as session, pytest.raises(InvalidInputError) as raised:
        environment.update_scan_station_theme_preference(
            session, cell.station_id, theme_preference=value
        )
    assert str(raised.value) == _E2
    after = _station(db_engine, cell.station_id)
    assert (after.theme_preference, after.xmin) == (None, before.xmin)


def test_the_service_returns_the_validated_member(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    for _attempt in range(2):  # a write, then a repeat
        with Session(db_engine) as session:
            saved = environment.update_scan_station_theme_preference(
                session, cell.station_id, theme_preference="LIGHT"
            )
        assert type(saved) is ThemePreference
        assert saved is ThemePreference.LIGHT
    assert _station(db_engine, cell.station_id).theme_preference == "LIGHT"


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


def test_the_write_waits_behind_a_production_command_lock(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM scan_stations WHERE station_id = :station FOR UPDATE"),
            {"station": cell.station_id},
        )
        thread, results = _start(
            lambda: client.put(_path(cell.station_id), json={"theme_preference": "LIGHT"})
        )
        _assert_blocked(thread)
        holder.commit()
    response = _finish(thread, results)
    assert response.status_code == 200, response.text
    assert _station(db_engine, cell.station_id).theme_preference == "LIGHT"


def test_the_write_never_waits_on_an_fk_check(client: TestClient, db_engine: Engine) -> None:
    cell = _Cell(client)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM scan_stations WHERE station_id = :station FOR KEY SHARE"),
            {"station": cell.station_id},
        )
        response = client.put(_path(cell.station_id), json={"theme_preference": "LIGHT"})
        assert response.status_code == 200, response.text
        holder.rollback()
    assert _station(db_engine, cell.station_id).theme_preference == "LIGHT"


def test_a_command_waits_behind_an_uncommitted_theme_write(
    client: TestClient, db_engine: Engine
) -> None:
    source, target = _Cell(client), _Cell(client)
    flow_id, pn = _release(client, source)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text(
                "UPDATE scan_stations SET theme_preference = 'LIGHT' WHERE station_id = :station"
            ),
            {"station": target.station_id},
        )
        thread, results = _start(lambda: _transfer(client, source, target, flow_id, pn))
        _assert_blocked(thread)
        holder.commit()
    transfer = _ok(_finish(thread, results), 201)
    with db_engine.connect() as connection:
        transferred = connection.execute(
            sa.text(
                "SELECT quantity, to_area_id, station_id FROM part_movements"
                " WHERE device_event_id = :event AND movement_type = 'TRANSFERRED'"
            ),
            {"event": transfer["device_event_id"]},
        ).one()
        flow = connection.execute(
            sa.text("SELECT quantity, current_area_id, status FROM quantity_flows WHERE id = :id"),
            {"id": flow_id},
        ).one()
    assert tuple(transferred) == (10, target.area_id, target.station_id)
    assert tuple(flow) == (10, target.area_id, "ACTIVE")
    assert _station(db_engine, target.station_id).theme_preference == "LIGHT"


# ---------------------------------------------------------------------------
# Nothing else writes the preference (R62)
# ---------------------------------------------------------------------------


def test_configuration_production_and_session_events_leave_the_theme_alone(
    client: TestClient, db_engine: Engine
) -> None:
    source, cell, other = _Cell(client), _Cell(client), _Cell(client)
    station = cell.station_id
    _save(client, station, "LIGHT")

    def assert_light() -> None:
        assert _station(db_engine, station).theme_preference == "LIGHT"
        assert _context(client, station)["theme_preference"] == "LIGHT"

    # Configuration: rebind away and back, deactivate and reactivate.
    for body in (
        {"area_id": other.area_id},
        {"area_id": cell.area_id},
        {"is_active": False},
        {"is_active": True},
    ):
        _ok(client.patch(f"/api/scan-stations/{station}", json=body))
        assert _station(db_engine, station).theme_preference == "LIGHT"
    audits = _station_audits(db_engine, station)
    assert len(audits) == 5  # CREATED + four UPDATED
    for row in audits:
        for snapshot in (row.before_data, row.after_data):
            assert snapshot is None or "theme_preference" not in snapshot
    assert_light()

    # Production: a transfer into the station's Area and its Undo.
    flow_id, pn = _release(client, source)
    transfer = _ok(_transfer(client, source, cell, flow_id, pn), 201)
    assert_light()
    _ok(_undo(client, cell, pn, str(transfer["device_event_id"])), 201)
    assert_light()

    # Worker Sessions: the Area switches to Scanned session mode through
    # the real Area API (slice 5).
    _ok(client.patch(f"/api/areas/{cell.area_id}", json={"worker_identification_mode": "SCANNED"}))
    assert_light()
    first, second = _worker(client), _worker(client)

    # An expired session: the command is refused with nothing recorded.
    _insert_expired_session(db_engine, cell, int(first["id"]))
    flow_id, pn = _release(client, source)
    refused = _transfer(client, source, cell, flow_id, pn)
    assert refused.status_code == 409, refused.text
    assert refused.json() == {"detail": _E_S1, "worker_session_required": True}
    assert_light()

    # Badge sign-in, then a switch to another Worker.
    assert _badge_scan(client, cell, first)["outcome"] == "SIGNED_IN"
    assert_light()
    assert _badge_scan(client, cell, second)["outcome"] == "SWITCHED"
    assert_light()

    # A transfer under the session, then its Undo confirmed by a badge
    # (the slice 5 gate; the badge-confirmation options default on).
    transfer = _ok(_transfer(client, source, cell, flow_id, pn), 201)
    assert_light()
    gates = _context(client, station)["worker_identification"]["final_gates"]
    assert gates["undo"] == "BADGE"
    _ok(
        _undo(
            client,
            cell,
            pn,
            str(transfer["device_event_id"]),
            confirming_badge=first["badge_barcode"],
        ),
        201,
    )
    assert_light()
