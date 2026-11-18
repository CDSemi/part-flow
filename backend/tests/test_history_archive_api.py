"""API tests of Administration → History archival & purge (Phase 16, design B step 1).

Exercises the full request path — FastAPI routes, the policies and archival
services and PostgreSQL — on a dedicated module database migrated to head:

- the settings (``/api/policies/data-retention``): the 24-month default, the
  partial merge of the retention period, the shortest allowed period (with
  a reason kept in the audit row), the Start minute and the preview time
  limit, every range message, and the new key guarding the change;
- the preview: no retention period, a cutoff newer than allowed, an older
  cutoff, the time limit;
- runs: a request is stored APPROVED with its recomputed preview and a
  CREATED audit row; at most one run is open; nothing eligible refuses;
  cancel (idempotent; a running or finished run refuses); the copy
  confirmation (only while waiting, against the manifest SHA-256, once);
  the list;
- permissions on every new route: anonymous 401, without the key 403 naming
  it (Configure system settings no longer suffices), the run list for any
  signed-in User.

Eligible history is built through the API (a release stocked whole at a
Stockroom station) and moved back in time by the owner's setup transaction.
"""

import datetime
import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import history_archive
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.auth_harness import (
    ALL_PERMISSIONS,
    admin_of,
    anonymous_client,
    client_as,
    station_device_client,
)
from tests.conftest import owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"
_SETTINGS = "/api/policies/data-retention"
_PREVIEW = "/api/history-archive/preview"
_RUNS = "/api/history-archive/runs"
_KEY = Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE
_DEFAULTS = {
    "retention_period_months": 24,
    "shortest_retention_months": 12,
    "archive_max_wait_days": 7,
    "archive_start_minute": 20,
    "archive_preview_seconds": 90,
}
_E_DENIED = "Your account does not have permission to do this."


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="module")
def url() -> Iterator[URL]:
    database = f"partflow_test_history_archive_api_{uuid.uuid4().hex[:8]}"
    cluster = make_url(os.environ[_DB_URL_ENV])
    admin = create_engine(cluster, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    target = cluster.set(database=database)
    try:
        command.upgrade(_alembic_config(target), "head")
        yield target
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def client(url: URL) -> Iterator[TestClient]:
    original = os.environ[_DB_URL_ENV]
    os.environ[_DB_URL_ENV] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield station_device_client(test_client)
    finally:
        os.environ[_DB_URL_ENV] = original
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def owner(url: URL) -> Iterator[Engine]:
    engine = owner_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def stations(client: TestClient) -> dict[str, Any]:
    admin = admin_of(client)
    department = admin.post("/api/departments", json={"name": "Archive API"})
    assert department.status_code == 201, department.text
    found: dict[str, Any] = {}
    for name, terminal in (("MAT", False), ("STOCK", True)):
        area = admin.post(
            "/api/areas",
            json={"department_id": department.json()["id"], "name": name, "is_terminal": terminal},
        )
        assert area.status_code == 201, area.text
        operation = admin.post(
            "/api/operations", json={"area_id": area.json()["id"], "code": f"OP-{name}"}
        )
        assert operation.status_code == 201, operation.text
        station = admin.post(
            "/api/scan-stations", json={"station_id": f"HA-{name}", "area_id": area.json()["id"]}
        )
        assert station.status_code == 201, station.text
        found[name] = {
            "area": int(area.json()["id"]),
            "operation": int(operation.json()["id"]),
            "station": str(station.json()["station_id"]),
        }
    return found


@pytest.fixture(autouse=True)
def restore_settings(owner: Engine) -> Iterator[None]:
    """Every case starts from the defaults and with no open run."""
    yield
    with owner.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE application_policy SET retention_period_months = 24,"
                " shortest_retention_months = 12, archive_max_wait_days = 7,"
                " archive_start_minute = 20, archive_preview_seconds = 90"
            )
        )
        open_ids = list(
            connection.execute(
                sa.text(
                    "SELECT id FROM history_archive_runs"
                    " WHERE state IN ('APPROVED', 'RUNNING', 'WAITING_FOR_COPY')"
                )
            ).scalars()
        )
    for run_id in open_ids:
        _job_change(owner, run_id, state="FAILED", failure_reason="test cleanup")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _settings(client: TestClient) -> dict[str, Any]:
    body = cast(dict[str, Any], _ok(admin_of(client).get(_SETTINGS)))
    return {key: value for key, value in body.items() if key != "updated_at"}


def _policy_audits(owner: Engine) -> list[dict[str, Any]]:
    with owner.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT actor_user_id, before_data, after_data, metadata FROM audit_events"
                    " WHERE entity_type = 'ApplicationPolicy' AND entity_id = 'data-retention'"
                    " ORDER BY id"
                )
            ).mappings()
        ]


def _audit_count(owner: Engine) -> int:
    with owner.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one())


def _eligible_flow(client: TestClient, owner: Engine, stations: dict[str, Any]) -> int:
    """A released flow stocked whole, with its Movements moved 36 months back."""
    admin = admin_of(client)
    part_number = f"HAAPI-{uuid.uuid4().hex[:6].upper()}"
    work_order = _ok(
        admin.post(
            "/api/work-orders",
            json={"lines": [{"part_number": part_number, "requested_quantity": 10}]},
        ),
        201,
    )
    flow = _ok(
        admin.post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": part_number,
                "quantity": 5,
                "route_mode": "FLOATING",
                "starting_area_id": stations["MAT"]["area"],
                "operation_id": stations["MAT"]["operation"],
                "confirm_active_quantity": True,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )["quantity_flow_id"]
    _ok(
        client.post(
            f"/api/scan-stations/{stations['STOCK']['station']}/stockings",
            json={
                "part_number": part_number,
                "quantity_flow_id": flow,
                "source_area_id": stations["MAT"]["area"],
                "target_area_id": stations["STOCK"]["area"],
                "quantity": 5,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )
    with owner.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE part_movements DISABLE TRIGGER trg_part_movements_forbid_mutation")
        )
        connection.execute(
            sa.text(
                "UPDATE part_movements SET occurred_at = occurred_at - interval '36 months',"
                " server_received_at = server_received_at - interval '36 months'"
                " WHERE quantity_flow_id = :flow"
            ),
            {"flow": flow},
        )
        connection.execute(
            sa.text("ALTER TABLE part_movements ENABLE TRIGGER trg_part_movements_forbid_mutation")
        )
    return int(flow)


def _job_change(owner: Engine, run_id: int, **values: object) -> None:
    """A change only the maintenance job may make, made as the owner with the job's setting."""
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    with owner.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('partflow.archive_run_id', :id, true)"), {"id": str(run_id)}
        )
        connection.execute(
            sa.text(
                f"UPDATE history_archive_runs SET {assignments}, finished_at = CASE WHEN"
                " :final THEN now() ELSE finished_at END WHERE id = :run_id"
            ),
            {**values, "run_id": run_id, "final": values.get("state") == "FAILED"},
        )


def _waiting(owner: Engine, run_id: int) -> str:
    """Move a run to WAITING_FOR_COPY as the job would; returns its manifest SHA-256."""
    manifest = "ab" * 32
    with owner.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('partflow.archive_run_id', :id, true)"), {"id": str(run_id)}
        )
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now(),"
                " archive_name = 'run-' || id || '-20271110T042000Z', exported_at = now(),"
                " exported_components = 1, exported_flows = 1, exported_movements = 2,"
                " exported_lineage_edges = 0, exported_assigned_routes = 0, archive_bytes = 100,"
                " manifest_sha256 = :manifest WHERE id = :id"
            ),
            {"manifest": manifest, "id": run_id},
        )
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET verified_at = now(), skipped_components = 0,"
                " skipped_flows = 0 WHERE id = :id"
            ),
            {"id": run_id},
        )
        connection.execute(
            sa.text("UPDATE history_archive_runs SET state = 'WAITING_FOR_COPY' WHERE id = :id"),
            {"id": run_id},
        )
    return manifest


def _run_audits(owner: Engine, run_id: int) -> list[dict[str, Any]]:
    with owner.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT event_type, actor_user_id, before_data, after_data, metadata"
                    " FROM audit_events WHERE entity_type = 'HistoryArchiveRun'"
                    " AND entity_id = :id ORDER BY id"
                ),
                {"id": str(run_id)},
            ).mappings()
        ]


def _request(client: TestClient, **body: Any) -> Any:
    return admin_of(client).post(_RUNS, json={"reason": "Archive old history", **body})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_settings_start_at_their_defaults(client: TestClient, owner: Engine) -> None:
    assert _settings(client) == _DEFAULTS
    # The migration set the retention period to 24 months and recorded it.
    first = _policy_audits(owner)[0]
    assert first["after_data"]["retention_period_months"] == 24
    assert first["before_data"]["retention_period_months"] is None
    assert first["actor_user_id"] is None
    assert first["metadata"] == {"source": "migration", "revision": "0040_phase16_history_archive"}


def test_a_partial_merge_changes_only_the_given_settings(client: TestClient, owner: Engine) -> None:
    before = len(_policy_audits(owner))
    body = _ok(admin_of(client).put(_SETTINGS, json={"archive_start_minute": 35}))
    assert body["archive_start_minute"] == 35
    assert {key: body[key] for key in _DEFAULTS if key != "archive_start_minute"} == {
        key: value for key, value in _DEFAULTS.items() if key != "archive_start_minute"
    }
    rows = _policy_audits(owner)
    assert len(rows) == before + 1
    assert rows[-1]["before_data"] == _DEFAULTS
    assert rows[-1]["after_data"] == {**_DEFAULTS, "archive_start_minute": 35}
    assert rows[-1]["actor_user_id"] == admin_of(client).user_id
    assert rows[-1]["metadata"] is None
    # The same values again write nothing.
    _ok(admin_of(client).put(_SETTINGS, json={"archive_start_minute": 35}))
    assert len(_policy_audits(owner)) == before + 1
    # The waiting limit of a copy confirmation merges the same way.
    body = _ok(admin_of(client).put(_SETTINGS, json={"archive_max_wait_days": 30}))
    assert (body["archive_max_wait_days"], body["archive_start_minute"]) == (30, 35)
    assert _policy_audits(owner)[-1]["after_data"]["archive_max_wait_days"] == 30
    # null clears the retention period.
    assert (
        _ok(admin_of(client).put(_SETTINGS, json={"retention_period_months": None}))[
            "retention_period_months"
        ]
        is None
    )


def test_the_shortest_period_changes_only_with_a_reason(client: TestClient, owner: Engine) -> None:
    response = admin_of(client).put(_SETTINGS, json={"shortest_retention_months": 6})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "Enter the reason for changing the shortest allowed period: 1 to 500 characters of"
        " plain text."
    )
    count = _audit_count(owner)
    blank = admin_of(client).put(_SETTINGS, json={"shortest_retention_months": 6, "reason": "  "})
    assert blank.status_code == 422
    assert _audit_count(owner) == count
    body = _ok(
        admin_of(client).put(
            _SETTINGS,
            json={
                "shortest_retention_months": 6,
                "retention_period_months": 6,
                "reason": " Audit ",
            },
        )
    )
    assert (body["shortest_retention_months"], body["retention_period_months"]) == (6, 6)
    last = _policy_audits(owner)[-1]
    assert last["metadata"] == {"reason": "Audit"}
    # A reason is not needed while the shortest period stays.
    _ok(
        admin_of(client).put(
            _SETTINGS, json={"shortest_retention_months": 6, "archive_start_minute": 1}
        )
    )


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {"retention_period_months": 11},
            "The retention period must be a whole number of months from 12 to 1200, or no"
            " retention period.",
        ),
        (
            {"retention_period_months": 1201},
            "The retention period must be a whole number of months from 12 to 1200, or no"
            " retention period.",
        ),
        (
            {"shortest_retention_months": 0, "reason": "x"},
            "The shortest allowed period must be a whole number of months from 1 to 1200.",
        ),
        (
            {"shortest_retention_months": 30, "reason": "x"},
            "The shortest allowed period cannot be longer than the retention period (24 months)."
            " Lengthen the retention period first, or in the same change.",
        ),
        (
            {"shortest_retention_months": 30, "retention_period_months": 24, "reason": "x"},
            "The retention period must be a whole number of months from 30 to 1200, or no"
            " retention period.",
        ),
        (
            {"archive_max_wait_days": 0},
            "The waiting limit must be a whole number of days from 1 to 365.",
        ),
        (
            {"archive_max_wait_days": 366},
            "The waiting limit must be a whole number of days from 1 to 365.",
        ),
        ({"archive_start_minute": 60}, "The Start minute must be a whole number from 0 to 59."),
        (
            {"archive_preview_seconds": 9},
            "The preview time limit must be a whole number of seconds from 10 to 600.",
        ),
        (
            {"archive_preview_seconds": None},
            "The preview time limit must be a whole number of seconds from 10 to 600.",
        ),
        ({}, "Change at least one setting of History archival & purge."),
    ],
)
def test_invalid_settings_are_refused_with_nothing_written(
    client: TestClient, owner: Engine, body: dict[str, Any], detail: str
) -> None:
    count = _audit_count(owner)
    response = admin_of(client).put(_SETTINGS, json=body)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": detail}
    assert _audit_count(owner) == count
    assert _settings(client) == _DEFAULTS


@pytest.mark.parametrize(
    "body",
    [{"archive_start_minute": "20"}, {"archive_start_minute": 2.0}, {"extra": 1}],
    ids=["string", "float", "extra-field"],
)
def test_malformed_settings_are_refused_by_the_schema(client: TestClient, body: Any) -> None:
    assert admin_of(client).put(_SETTINGS, json=body).status_code == 422


def test_the_settings_need_the_archival_key(client: TestClient, owner: Engine) -> None:
    count = _audit_count(owner)
    settings_only = client_as(client, Permission.CONFIGURE_SYSTEM_SETTINGS)
    response = settings_only.put(_SETTINGS, json={"archive_start_minute": 5})
    assert response.status_code == 403
    assert response.json()["required_permissions"] == [_KEY.value]
    assert (
        anonymous_client(client).put(_SETTINGS, json={"archive_start_minute": 5}).status_code == 401
    )
    assert _audit_count(owner) == count
    # Reading needs only a signed-in User.
    assert _ok(client_as(client).get(_SETTINGS))["archive_start_minute"] == 20
    _ok(client_as(client, _KEY).put(_SETTINGS, json={"archive_start_minute": 5}))


# ---------------------------------------------------------------------------
# Preview and runs
# ---------------------------------------------------------------------------


def test_the_preview_refuses_without_a_retention_period_and_a_newer_cutoff(
    client: TestClient,
) -> None:
    latest = cast(dict[str, Any], _ok(admin_of(client).get(_PREVIEW)))["latest_cutoff_date"]
    newer = (datetime.date.fromisoformat(latest) + datetime.timedelta(days=1)).isoformat()
    response = admin_of(client).get(f"{_PREVIEW}?cutoff_date={newer}")
    assert response.status_code == 422
    assert response.json()["detail"].startswith(f"The cutoff cannot be newer than {latest}")
    assert admin_of(client).get(f"{_PREVIEW}?cutoff_date=1999-12-31").status_code == 422
    _ok(admin_of(client).put(_SETTINGS, json={"retention_period_months": None}))
    response = admin_of(client).get(_PREVIEW)
    assert response.status_code == 409
    assert response.json()["detail"].startswith("No retention period is set")
    request = _request(client)
    assert request.status_code == 409


def test_the_preview_reports_its_time_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def expired(self: Any, seconds: int) -> None:
        self.seconds = seconds
        self.ends = time.monotonic() - 1

    monkeypatch.setattr(history_archive._Deadline, "__init__", expired)
    response = admin_of(client).get(_PREVIEW)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "The preview did not finish within its time limit of 90 seconds, so nothing was"
        " requested. Try again at a quieter time, or raise the preview time limit."
    )


def test_a_statement_over_the_time_limit_is_cancelled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, owner: Engine, stations: dict[str, Any]
) -> None:
    _eligible_flow(client, owner, stations)

    def arm(self: Any, session: Any) -> None:
        session.execute(sa.text("SELECT set_config('statement_timeout', '1ms', true)"))
        session.execute(sa.text("SELECT pg_sleep(0.01)"))

    monkeypatch.setattr(history_archive._Deadline, "arm", arm)
    response = admin_of(client).get(_PREVIEW)
    assert response.status_code == 409, response.text
    assert "did not finish within its time limit" in response.json()["detail"]


def test_a_request_stores_the_run_with_its_preview(
    client: TestClient, owner: Engine, stations: dict[str, Any]
) -> None:
    _eligible_flow(client, owner, stations)
    preview = cast(dict[str, Any], _ok(admin_of(client).get(_PREVIEW)))
    run = cast(dict[str, Any], _ok(_request(client, reason="  First run  "), 201))
    assert run["state"] == "APPROVED"
    assert run["reason"] == "First run"
    assert run["cutoff_date"] == preview["cutoff_date"]
    assert run["cutoff"].startswith(preview["cutoff"][:19])
    assert run["groups"] == ["MOVEMENT_HISTORY"]
    assert run["requested_by"]["user_id"] == admin_of(client).user_id
    assert {key: run["preview"][key] for key in ("components", "flows", "movements")} == {
        key: preview[key] for key in ("components", "flows", "movements")
    }
    assert run["exported"] is None and run["skipped"] is None and run["result"] is None
    audits = _run_audits(owner, int(run["id"]))
    assert [row["event_type"] for row in audits] == ["CREATED"]
    assert audits[0]["before_data"] is None
    assert audits[0]["after_data"]["state"] == "APPROVED"
    assert audits[0]["after_data"]["reason"] == "First run"
    assert audits[0]["actor_user_id"] == admin_of(client).user_id
    assert audits[0]["metadata"] == {"source": "web", "command": "request"}
    # One open run at a time.
    second = _request(client)
    assert second.status_code == 409
    assert second.json()["detail"] == (
        f"Run {run['id']} is still open (APPROVED). Cancel it or wait until it finishes before"
        " requesting a new run."
    )
    listed = cast(list[dict[str, Any]], _ok(client_as(client).get(_RUNS)))
    assert listed[0]["id"] == run["id"]
    assert len(cast(list[Any], _ok(admin_of(client).get(f"{_RUNS}?limit=1")))) == 1
    assert admin_of(client).get(f"{_RUNS}?limit=0").status_code == 422


def test_a_request_that_loses_the_race_names_the_open_run(
    client: TestClient, owner: Engine, stations: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unique index decides a race; the refusal names the run that won while it is open."""
    _eligible_flow(client, owner, stations)
    winner = cast(dict[str, Any], _ok(_request(client), 201))
    count = _audit_count(owner)
    original = history_archive._open_run
    looked: list[int] = []

    def unseen_first(session: Any) -> Any:
        # The first look (before the insert) misses the winner, as in a race.
        looked.append(1)
        return None if len(looked) == 1 else original(session)

    monkeypatch.setattr(history_archive, "_open_run", unseen_first)
    response = _request(client)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        f"Run {winner['id']} is still open (APPROVED). Cancel it or wait until it finishes before"
        " requesting a new run."
    )
    # When the winner has closed meanwhile, the refusal cannot name it.
    monkeypatch.setattr(history_archive, "_open_run", lambda session: None)
    response = _request(client)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Another archival run is still open. Cancel it or wait until it finishes before requesting"
        " a new run."
    )
    assert _audit_count(owner) == count


def test_a_cutoff_the_database_refuses_is_a_conflict(
    client: TestClient, owner: Engine, stations: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run guard's cutoff floor answers 409, never 500 (here the application's clock is
    400 days ahead of the database's, so its cutoff is newer than the floor allows)."""
    _eligible_flow(client, owner, stations)
    later = history_archive.now() + datetime.timedelta(days=400)
    monkeypatch.setattr(history_archive, "now", lambda: later)
    count = _audit_count(owner)
    response = _request(client)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "The database refused the cutoff as newer than the shortest allowed period allows now, so"
        " no run was requested. Reload the settings and choose an older day."
    )
    assert _audit_count(owner) == count


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501, "line\nbreak"])
def test_a_request_needs_a_plain_reason(client: TestClient, owner: Engine, reason: str) -> None:
    count = _audit_count(owner)
    response = _request(client, reason=reason)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "Enter the reason for this run: 1 to 500 characters of plain text."
    )
    assert _audit_count(owner) == count


def test_a_request_with_nothing_eligible_is_refused(client: TestClient, owner: Engine) -> None:
    count = _audit_count(owner)
    response = _request(client, cutoff_date="2001-01-01")
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "Nothing is eligible before 2001-01-01, so no run was requested."
    )
    assert _audit_count(owner) == count


def test_cancel_and_its_refusals(
    client: TestClient, owner: Engine, stations: dict[str, Any]
) -> None:
    _eligible_flow(client, owner, stations)
    run_id = int(_ok(_request(client), 201)["id"])
    cancelled = cast(dict[str, Any], _ok(admin_of(client).post(f"{_RUNS}/{run_id}/cancel")))
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["cancelled_by"]["user_id"] == admin_of(client).user_id
    assert cancelled["finished_at"] is not None
    # Cancelling again changes nothing.
    count = len(_run_audits(owner, run_id))
    _ok(admin_of(client).post(f"{_RUNS}/{run_id}/cancel"))
    assert len(_run_audits(owner, run_id)) == count
    assert [row["after_data"]["state"] for row in _run_audits(owner, run_id)] == [
        "APPROVED",
        "CANCELLED",
    ]
    # A running run and a finished one cannot be cancelled.
    running = int(_ok(_request(client), 201)["id"])
    _job_change(owner, running, state="RUNNING", started_at=datetime.datetime.now(datetime.UTC))
    response = admin_of(client).post(f"{_RUNS}/{running}/cancel")
    assert response.status_code == 409
    assert "is running on the server now" in response.json()["detail"]
    _job_change(owner, running, state="FAILED", failure_reason="stopped for the test")
    response = admin_of(client).post(f"{_RUNS}/{running}/cancel")
    assert response.status_code == 409
    assert (
        response.json()["detail"] == f"Run {running} is finished (FAILED) and cannot be cancelled."
    )
    missing = admin_of(client).post(f"{_RUNS}/2000000/cancel")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "There is no archival run 2000000."


def test_the_copy_confirmation(client: TestClient, owner: Engine, stations: dict[str, Any]) -> None:
    _eligible_flow(client, owner, stations)
    run_id = int(_ok(_request(client), 201)["id"])
    path = f"{_RUNS}/{run_id}/copy-confirmation"
    early = admin_of(client).post(path, json={"manifest_sha256": "ab" * 32})
    assert early.status_code == 409
    assert early.json()["detail"] == (
        f"Run {run_id} is APPROVED; only a run waiting for its off-host copy can be confirmed."
    )
    manifest = _waiting(owner, run_id)
    wrong = admin_of(client).post(path, json={"manifest_sha256": "cd" * 32})
    assert wrong.status_code == 409
    assert "Reload the run and check the copy again." in wrong.json()["detail"]
    confirmed = cast(
        dict[str, Any], _ok(admin_of(client).post(path, json={"manifest_sha256": manifest}))
    )
    assert confirmed["state"] == "WAITING_FOR_COPY"
    assert confirmed["copy_confirmed_at"] is not None
    assert confirmed["copy_confirmed_by"]["user_id"] == admin_of(client).user_id
    count = len(_run_audits(owner, run_id))
    _ok(admin_of(client).post(path, json={"manifest_sha256": manifest}))
    assert len(_run_audits(owner, run_id)) == count
    last = _run_audits(owner, run_id)[-1]
    assert last["metadata"] == {"source": "web", "command": "copy_confirmation"}
    assert last["after_data"]["copy_confirmed_by_user_id"] == admin_of(client).user_id
    # A waiting run can still be cancelled.
    _ok(admin_of(client).post(f"{_RUNS}/{run_id}/cancel"))


# ---------------------------------------------------------------------------
# Permissions on every new route
# ---------------------------------------------------------------------------


def test_every_archival_action_needs_the_key(client: TestClient, owner: Engine) -> None:
    lacking = client_as(client, *(key for key in ALL_PERMISSIONS if key is not _KEY))
    anonymous = anonymous_client(client)
    requests = [
        ("GET", _PREVIEW, None),
        ("POST", _RUNS, {"reason": "x"}),
        ("POST", f"{_RUNS}/1/cancel", None),
        ("POST", f"{_RUNS}/1/copy-confirmation", {"manifest_sha256": "ab" * 32}),
        ("PUT", _SETTINGS, {"archive_start_minute": 3}),
    ]
    count = _audit_count(owner)
    for method, path, body in requests:
        kwargs: dict[str, Any] = {} if body is None else {"json": body}
        assert anonymous.request(method, path, **kwargs).status_code == 401, path
        response = lacking.request(method, path, **kwargs)
        assert response.status_code == 403, path
        assert response.json()["detail"] == _E_DENIED
        assert response.json()["required_permissions"] == [_KEY.value]
    assert _audit_count(owner) == count
    assert anonymous.get(_RUNS).status_code == 401
    _ok(client_as(client).get(_RUNS))
