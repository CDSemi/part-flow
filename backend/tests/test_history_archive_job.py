"""Tests of the Movement-history archival job (Phase 16, design B step 1: runs without deletion).

Every scenario is built through the real API in one module database —
releases, a stocking, a partial transfer (a split), a merge, an undone
split, a full Scrap, a quantity addition, a flow on a Planned Route, a
still-active component and a recent flow —; the Movements of the old
components are then moved back in time (``occurred_at`` and
``server_received_at``, the append-only guard disabled inside the owner's
setup transaction). The job commands run as the maintenance database role
in the application-role test mode (as the owner otherwise); the API calls
run as the application role.

Covered: the selection of whole eligible components over splits, merges,
Undo and partial transfers, and its cutoff boundaries (``occurred_at`` and
``server_received_at`` strictly before the cutoff); the export's files, its
manifest, ``SHA256SUMS`` (checked with ``sha256sum -c`` alone) and the
carry-forward records, re-derived from the whole archived rows (the export
keeps only a few fields per flow); the verification (a changed file fails the run, a
component that changed after the export is skipped, the folder is published
by renaming it); the off-host copy with a copy folder, and the wait for a
confirmation without one — a copied or confirmed run stays RUNNING for the
pre-purge backup and the purge (step 2, ``tests/test_history_purge.py``), so
this module ends such runs through ``recover`` —; ``recover``, ``next``,
``status``, the job lock, the failures (more than the preview, a cutoff now
newer than the shortest allowed period, an existing archive name) and the
CLI's JSON and exit codes. Nothing is deleted here: the Movement count stays
the same throughout.
"""

import datetime
import gzip
import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from alembic import command
from app import cli
from app.application import history_archive, history_archive_job
from app.application.history_archive_job import JobFolders
from app.core.config import get_settings
from app.infrastructure import archive_files, schema_revision
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client
from tests.conftest import app_mode_roles, owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"
_BACKDATE_MONTHS = 36
#: The scenarios whose whole component is closed and old: eligible at the default cutoff.
_ELIGIBLE = (
    "stocked",
    "split",
    "merge",
    "undone_split",
    "scrapped",
    "added",
    "beside",
    "undone",
    "planned",
)


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _event() -> str:
    return str(uuid.uuid4())


def _pn(label: str) -> str:
    return f"HA-{label}-{uuid.uuid4().hex[:6].upper()}"


# ---------------------------------------------------------------------------
# The module database and the API builder
# ---------------------------------------------------------------------------


class Builder:
    """Drives the API of one running app; every id it returns is real."""

    def __init__(self, client: TestClient, url: URL) -> None:
        self.client = client
        self.url = url
        self.admin = admin_of(client)
        self.engine = cast(Engine, cast(Any, client.app).state.engine)
        self.areas: dict[str, int] = {}
        self.operations: dict[str, int] = {}
        self.stations: dict[str, str] = {}
        self.last_event = ""

    def environment(self) -> None:
        department = self.admin.post("/api/departments", json={"name": "Archive"})
        assert department.status_code == 201, department.text
        for name, terminal in (("MAT", False), ("DEBURR", False), ("STOCK", True)):
            area = self.admin.post(
                "/api/areas",
                json={
                    "department_id": department.json()["id"],
                    "name": name,
                    "is_terminal": terminal,
                },
            )
            assert area.status_code == 201, area.text
            self.areas[name] = int(area.json()["id"])
            operation = self.admin.post(
                "/api/operations", json={"area_id": self.areas[name], "code": f"OP-{name}"}
            )
            assert operation.status_code == 201, operation.text
            self.operations[name] = int(operation.json()["id"])
            station = self.admin.post(
                "/api/scan-stations",
                json={"station_id": f"ST-{name}", "area_id": self.areas[name]},
            )
            assert station.status_code == 201, station.text
            self.stations[name] = str(station.json()["station_id"])

    def route_template(self, areas: list[str]) -> int:
        response = self.admin.post(
            "/api/route-templates",
            json={
                "name": f"ROUTE-{uuid.uuid4().hex[:6]}",
                "steps": [
                    {"area_id": self.areas[name], "operation_id": self.operations[name]}
                    for name in areas
                ],
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    def release(self, part_number: str, quantity: int, route_template_id: int | None = None) -> int:
        work_order = self.admin.post(
            "/api/work-orders",
            json={"lines": [{"part_number": part_number, "requested_quantity": 100}]},
        )
        assert work_order.status_code == 201, work_order.text
        body = work_order.json()
        response = self.admin.post(
            f"/api/work-orders/{body['id']}/demands/{body['demands'][0]['id']}/release",
            json={
                "part_number": part_number,
                "quantity": quantity,
                "route_mode": "FLOATING" if route_template_id is None else "PLANNED",
                "route_template_id": route_template_id,
                "starting_area_id": self.areas["MAT"],
                "operation_id": self.operations["MAT"],
                "confirm_active_quantity": True,
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def arrival(
        self, action: str, source: str, target: str, flow_id: int, part_number: str, quantity: int
    ) -> int:
        event_id = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[target]}/{action}",
            json={
                "part_number": part_number,
                "quantity_flow_id": flow_id,
                "source_area_id": self.areas[source],
                "target_area_id": self.areas[target],
                "quantity": quantity,
                "device_event_id": event_id,
            },
        )
        assert response.status_code == 201, response.text
        self.last_event = event_id
        return int(response.json()["quantity_flow_id"])

    def scrap(self, area: str, flow_id: int, part_number: str, quantity: int) -> None:
        event_id = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/scraps",
            json={
                "part_number": part_number,
                "quantity_flow_id": flow_id,
                "quantity": quantity,
                "reason": "damaged during processing",
                "device_event_id": event_id,
            },
        )
        assert response.status_code == 201, response.text
        self.last_event = event_id

    def undo(self, area: str, part_number: str, command_id: str) -> None:
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/undos",
            json={
                "part_number": part_number,
                "reverses_device_event_id": command_id,
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text

    def merge(self, part_number: str, flow_ids: list[int]) -> int:
        response = self.client.post(
            f"/api/scan-stations/{self.stations['MAT']}/merges",
            json={
                "part_number": part_number,
                "quantity_flow_ids": flow_ids,
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def add_quantity(self, part_number: str, quantity: int) -> int:
        response = self.client.post(
            f"/api/scan-stations/{self.stations['MAT']}/quantity-additions",
            json={
                "part_number": part_number,
                "quantity": quantity,
                "reason": "found on the rack",
                "operation_id": self.operations["MAT"],
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def column(self, sql: str, **params: object) -> list[Any]:
        with self.engine.connect() as connection:
            return list(connection.execute(sa.text(sql), params).scalars())

    def scalar(self, sql: str, **params: object) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(sa.text(sql), params).scalar()

    def component(self, flow_id: int) -> set[int]:
        """Every flow joined to ``flow_id`` by lineage edges (void ones too)."""
        found = {flow_id}
        frontier = {flow_id}
        while frontier:
            edges = self.column(
                "SELECT parent_flow_id || ',' || child_flow_id FROM quantity_flow_lineage"
                " WHERE parent_flow_id = ANY(:ids) OR child_flow_id = ANY(:ids)",
                ids=sorted(frontier),
            )
            frontier = set()
            for edge in edges:
                for value in (int(part) for part in str(edge).split(",")):
                    if value not in found:
                        found.add(value)
                        frontier.add(value)
        return found


@dataclass
class World:
    builder: Builder
    url: URL
    owner: Engine
    #: Every flow of each scenario's component.
    components: dict[str, set[int]] = field(default_factory=dict)
    part_numbers: dict[str, str] = field(default_factory=dict)
    #: The scrap command of scenario "undone" (undone after the first export).
    scrap_event: str = ""


def _backdate(owner: Engine, flow_ids: set[int], months: int) -> None:
    """Move every Movement of the flows ``months`` back (owner setup only)."""
    with owner.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE part_movements DISABLE TRIGGER trg_part_movements_forbid_mutation")
        )
        connection.execute(
            sa.text(
                "UPDATE part_movements SET occurred_at = occurred_at - make_interval(months => :m),"
                " server_received_at = server_received_at - make_interval(months => :m)"
                " WHERE quantity_flow_id = ANY(:ids)"
            ),
            {"m": months, "ids": sorted(flow_ids)},
        )
        connection.execute(
            sa.text("ALTER TABLE part_movements ENABLE TRIGGER trg_part_movements_forbid_mutation")
        )


def _build(builder: Builder, owner: Engine) -> World:
    world = World(builder=builder, url=builder.url, owner=owner)

    def remember(name: str, part_number: str, flow_id: int) -> None:
        world.part_numbers[name] = part_number
        world.components[name] = builder.component(flow_id)

    # stocked: one flow, released and stocked whole.
    pn = _pn("STOCKED")
    flow = builder.release(pn, 5)
    builder.arrival("stockings", "MAT", "STOCK", flow, pn, 5)
    remember("stocked", pn, flow)
    # split: a partial transfer, both parts stocked.
    pn = _pn("SPLIT")
    flow = builder.release(pn, 10)
    moved = builder.arrival("transfers", "MAT", "DEBURR", flow, pn, 4)
    builder.arrival("stockings", "DEBURR", "STOCK", moved, pn, 4)
    remaining = next(iter(builder.component(flow) - {flow, moved}))
    builder.arrival("stockings", "MAT", "STOCK", remaining, pn, 6)
    remember("split", pn, flow)
    # merge: two releases merged, the result stocked.
    pn = _pn("MERGE")
    first = builder.release(pn, 3)
    second = builder.release(pn, 2)
    merged = builder.merge(pn, [first, second])
    builder.arrival("stockings", "MAT", "STOCK", merged, pn, 5)
    remember("merge", pn, first)
    # undone_split: a partial transfer undone, then the reopened flow stocked.
    pn = _pn("UNDO")
    flow = builder.release(pn, 8)
    builder.arrival("transfers", "MAT", "DEBURR", flow, pn, 3)
    builder.undo("DEBURR", pn, builder.last_event)
    builder.arrival("stockings", "MAT", "STOCK", flow, pn, 8)
    remember("undone_split", pn, flow)
    # scrapped: released and scrapped whole.
    pn = _pn("SCRAP")
    flow = builder.release(pn, 4)
    builder.scrap("MAT", flow, pn, 4)
    remember("scrapped", pn, flow)
    # added: a quantity addition (beside a released flow, "beside"), both stocked.
    pn = _pn("ADD")
    beside = builder.release(pn, 3)
    flow = builder.add_quantity(pn, 7)
    builder.arrival("stockings", "MAT", "STOCK", flow, pn, 7)
    builder.arrival("stockings", "MAT", "STOCK", beside, pn, 3)
    remember("added", pn, flow)
    remember("beside", pn, beside)
    # undone: a full Scrap undone only after the first export.
    pn = _pn("LATE")
    flow = builder.release(pn, 2)
    builder.scrap("MAT", flow, pn, 2)
    world.scrap_event = builder.last_event
    remember("undone", pn, flow)
    # planned: released on a Planned Route of three steps and moved along it.
    pn = _pn("PLANNED")
    flow = builder.release(pn, 6, builder.route_template(["MAT", "DEBURR", "STOCK"]))
    builder.arrival("transfers", "MAT", "DEBURR", flow, pn, 6)
    builder.arrival("stockings", "DEBURR", "STOCK", flow, pn, 6)
    remember("planned", pn, flow)
    # active: a partial transfer whose remaining part is still active.
    pn = _pn("ACTIVE")
    flow = builder.release(pn, 9)
    moved = builder.arrival("transfers", "MAT", "DEBURR", flow, pn, 2)
    builder.arrival("stockings", "DEBURR", "STOCK", moved, pn, 2)
    remember("active", pn, flow)
    for name in (*_ELIGIBLE, "active"):
        _backdate(owner, world.components[name], _BACKDATE_MONTHS)
    # recent: a closed flow of today.
    pn = _pn("RECENT")
    flow = builder.release(pn, 1)
    builder.arrival("stockings", "MAT", "STOCK", flow, pn, 1)
    remember("recent", pn, flow)
    return world


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    database = f"partflow_test_history_archive_job_{uuid.uuid4().hex[:8]}"
    cluster = make_url(os.environ[_DB_URL_ENV])
    admin = create_engine(cluster, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    url = cluster.set(database=database)
    original = os.environ[_DB_URL_ENV]
    owner = owner_engine(url)
    try:
        command.upgrade(_alembic_config(url), "head")
        os.environ[_DB_URL_ENV] = url.render_as_string(hide_password=False)
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            builder = Builder(station_device_client(client), url)
            builder.environment()
            yield _build(builder, owner)
    finally:
        owner.dispose()
        os.environ[_DB_URL_ENV] = original
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def job(world: World) -> Iterator[Engine]:
    """The job's engine: the maintenance role in app mode, the owner otherwise."""
    engine = owner_engine(world.url, poolclass=NullPool)
    roles = app_mode_roles()
    if roles is not None:

        @event.listens_for(engine, "connect")
        def _maintenance(dbapi_connection: Any, record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET SESSION ROLE "{roles.maintenance}"')
            cursor.close()
            dbapi_connection.commit()

    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def folders(tmp_path_factory: pytest.TempPathFactory) -> JobFolders:
    archive = tmp_path_factory.mktemp("archive")
    return JobFolders(archive, None)


@pytest.fixture(scope="module")
def copy_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("archive_copy")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all(world: World, names: tuple[str, ...]) -> set[int]:
    return set().union(*(world.components[name] for name in names))


def _movement_count(world: World) -> int:
    return int(world.builder.scalar("SELECT count(*) FROM part_movements"))


def _default_cutoff() -> datetime.datetime:
    zone = history_archive.site_zone()
    today = history_archive.site_date_of(history_archive.now(), zone)
    return history_archive.start_of_day(history_archive.months_before(today, 24), zone)


def _selected(job: Engine, cutoff: datetime.datetime) -> set[int]:
    with job.connect() as connection:
        return {facts.flow_id for facts in history_archive_job.eligible_flows(connection, cutoff)}


def _request(world: World, reason: str = "Archive old history") -> dict[str, Any]:
    response = world.builder.admin.post("/api/history-archive/runs", json={"reason": reason})
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _run(world: World, run_id: int) -> dict[str, Any]:
    with world.owner.connect() as connection:
        return dict(
            connection.execute(
                sa.text("SELECT * FROM history_archive_runs WHERE id = :id"), {"id": run_id}
            )
            .mappings()
            .one()
        )


def _records(world: World, run_id: int) -> dict[int, dict[str, Any]]:
    with world.owner.connect() as connection:
        return {
            int(row["flow_id"]): dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM history_archived_flows WHERE run_id = :id"), {"id": run_id}
            ).mappings()
        }


def _run_audits(world: World, run_id: int) -> list[dict[str, Any]]:
    with world.owner.connect() as connection:
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


def _states(audits: list[dict[str, Any]]) -> list[str]:
    return [str(row["after_data"]["state"]) for row in audits]


def _close_open_runs(world: World) -> None:
    """Cancel an open APPROVED or WAITING_FOR_COPY run through the API."""
    with world.owner.connect() as connection:
        open_ids = list(
            connection.execute(
                sa.text(
                    "SELECT id FROM history_archive_runs"
                    " WHERE state IN ('APPROVED', 'WAITING_FOR_COPY')"
                )
            ).scalars()
        )
    for run_id in open_ids:
        response = world.builder.admin.post(f"/api/history-archive/runs/{run_id}/cancel")
        assert response.status_code == 200, response.text


def _insert_run(
    world: World,
    *,
    cutoff: datetime.datetime,
    components: int = 1000,
    flows: int = 1000,
    movements: int = 100000,
) -> int:
    """An APPROVED run stored directly (owner setup), with the given preview counts."""
    _close_open_runs(world)
    user_id = world.builder.admin.user_id
    with world.owner.begin() as connection:
        return int(
            connection.execute(
                sa.text(
                    "INSERT INTO history_archive_runs (state, cutoff, reason,"
                    " requested_by_user_id, preview_components, preview_flows,"
                    " preview_movements, preview_archive_bytes)"
                    " VALUES ('APPROVED', :cutoff, 'direct test run', :user, :c, :f, :m, 0)"
                    " RETURNING id"
                ),
                {"cutoff": cutoff, "user": user_id, "c": components, "f": flows, "m": movements},
            ).scalar_one()
        )


def _sha256sum_check(folder: Path) -> str:
    completed = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def _lines(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def _done(report: history_archive_job.JobReport, result: str) -> None:
    assert report.exit_code == history_archive_job.EXIT_DONE, history_archive_job.job_document(
        report
    )
    assert report.result == result, history_archive_job.job_document(report)


# ---------------------------------------------------------------------------
# The selection
# ---------------------------------------------------------------------------


def test_the_job_runs_as_the_maintenance_role(job: Engine) -> None:
    """In the application-role test mode the job has only the maintenance role's grants."""
    roles = app_mode_roles()
    if roles is None:
        pytest.skip("only the application-role test mode has a maintenance role")
    with job.connect() as connection:
        assert connection.execute(sa.text("SELECT current_user")).scalar_one() == roles.maintenance


def test_the_selection_takes_whole_closed_components(world: World, job: Engine) -> None:
    selected = _selected(job, _default_cutoff())
    assert selected == _all(world, _ELIGIBLE)
    # Whole components: the split, the merge (2 sources and the result) and the
    # undone split (the reopened flow and its 2 reversed children).
    assert len(world.components["split"]) == 3
    assert len(world.components["merge"]) == 3
    assert len(world.components["undone_split"]) == 3
    assert not selected & world.components["active"]
    assert not selected & world.components["recent"]


def test_the_component_id_is_its_smallest_flow(world: World, job: Engine) -> None:
    with job.connect() as connection:
        facts = history_archive_job.eligible_flows(connection, _default_cutoff())
    by_flow = {item.flow_id: item.component_id for item in facts}
    for name in _ELIGIBLE:
        flows = world.components[name]
        assert {by_flow[flow] for flow in flows} == {min(flows)}, name


def test_the_cutoff_is_strict_on_both_times(world: World, job: Engine) -> None:
    flows = world.components["stocked"]
    newest = world.builder.scalar(
        "SELECT max(occurred_at) FROM part_movements WHERE quantity_flow_id = ANY(:ids)",
        ids=sorted(flows),
    )
    assert flows <= _selected(job, newest + datetime.timedelta(microseconds=1))
    assert not flows & _selected(job, newest)
    # A server receipt after the cutoff keeps the component, whatever occurred_at says.
    last = world.builder.scalar(
        "SELECT max(id) FROM part_movements WHERE quantity_flow_id = ANY(:ids)", ids=sorted(flows)
    )
    with world.owner.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE part_movements DISABLE TRIGGER trg_part_movements_forbid_mutation")
        )
        connection.execute(
            sa.text(
                "UPDATE part_movements SET server_received_at = server_received_at"
                " + interval '1 day' WHERE id = :id"
            ),
            {"id": last},
        )
        connection.execute(
            sa.text("ALTER TABLE part_movements ENABLE TRIGGER trg_part_movements_forbid_mutation")
        )
    received = world.builder.scalar(
        "SELECT max(server_received_at) FROM part_movements WHERE quantity_flow_id = ANY(:ids)",
        ids=sorted(flows),
    )
    assert not flows & _selected(job, received)
    assert flows <= _selected(job, received + datetime.timedelta(microseconds=1))


def test_the_preview_counts_what_the_selection_takes(world: World) -> None:
    response = world.builder.admin.get("/api/history-archive/preview")
    assert response.status_code == 200, response.text
    body = response.json()
    flows = _all(world, _ELIGIBLE)
    assert body["flows"] == len(flows)
    assert body["components"] == len(_ELIGIBLE)
    assert body["movements"] == world.builder.scalar(
        "SELECT count(*) FROM part_movements WHERE quantity_flow_id = ANY(:ids)", ids=sorted(flows)
    )
    assert body["estimated_archive_bytes"] > 0
    assert body["cutoff_date"] == body["latest_cutoff_date"]
    # An older cutoff before every Movement finds nothing.
    old = world.builder.admin.get("/api/history-archive/preview?cutoff_date=2001-01-01")
    assert old.status_code == 200, old.text
    assert (old.json()["components"], old.json()["flows"], old.json()["movements"]) == (0, 0, 0)
    assert old.json()["estimated_archive_bytes"] == 0


# ---------------------------------------------------------------------------
# A whole run: request, export, verify (with a skip), wait, confirm, complete
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def first_run(world: World) -> dict[str, Any]:
    return _request(world, "First archive of old history")


def test_the_export_writes_the_archive_and_the_records(
    world: World, job: Engine, folders: JobFolders, first_run: dict[str, Any]
) -> None:
    movements_before = _movement_count(world)
    run_id = int(first_run["id"])
    assert first_run["state"] == "APPROVED"
    report = history_archive_job.export(job, folders, run_id)
    _done(report, "exported")
    assert report.state == "RUNNING"
    # The preview's estimate is of the right order (fixed files dominate here).
    estimate = int(first_run["preview"]["estimated_archive_bytes"])
    actual = int(report.details["archive_bytes"])
    assert actual / 4 <= estimate <= actual * 4
    name = str(report.details["archive_name"])
    assert history_archive_job.NAME_PATTERN.fullmatch(name)
    assert name.startswith(f"run-{run_id}-")
    folder = cast(Path, folders.archive_dir) / ".partial" / name
    assert sorted(path.name for path in folder.iterdir()) == list(history_archive_job.ARCHIVE_FILES)
    _sha256sum_check(folder)

    flows = _all(world, _ELIGIBLE)
    movement_rows = _lines(folder / "movements.jsonl.gz")
    assert [row["id"] for row in movement_rows] == sorted(row["id"] for row in movement_rows)
    assert {row["quantity_flow_id"] for row in movement_rows} == flows
    assert len(movement_rows) == world.builder.scalar(
        "SELECT count(*) FROM part_movements WHERE quantity_flow_id = ANY(:ids)", ids=sorted(flows)
    )
    assert set(movement_rows[0]) >= {
        "id",
        "quantity_flow_id",
        "part_number",
        "movement_type",
        "quantity",
        "occurred_at",
        "server_received_at",
        "device_event_id",
        "metadata",
    }
    flow_rows = _lines(folder / "flows.jsonl.gz")
    assert {row["flow"]["id"] for row in flow_rows} == flows
    lineage = _lines(folder / "lineage.jsonl.gz")
    assert {row["parent_flow_id"] for row in lineage} | {
        row["child_flow_id"] for row in lineage
    } <= flows
    assert any(row["void"] for row in lineage)

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "partflow-history-archive"
    assert manifest["format_version"] == 1
    assert manifest["archive_name"] == name
    assert manifest["run"]["id"] == run_id
    assert manifest["groups"] == ["MOVEMENT_HISTORY"]
    assert manifest["alembic_revision"] == schema_revision.code_head()
    assert manifest["counts"] == report.details["counts"]
    assert manifest["counts"]["components"] == len(_ELIGIBLE)
    assert manifest["counts"]["flows"] == len(flows)
    assert manifest["counts"]["movements"] == len(movement_rows)
    assert manifest["files"]["movements.jsonl.gz"]["rows"] == len(movement_rows)
    sums = (folder / "SHA256SUMS").read_text(encoding="ascii")
    assert f"{manifest['files']['movements.jsonl.gz']['sha256']}  movements.jsonl.gz\n" in sums
    context = json.loads((folder / "context.json").read_text(encoding="utf-8"))
    assert {area["name"] for area in context["areas"]} >= {"MAT", "STOCK"}
    archived_pns = {world.part_numbers[name] for name in _ELIGIBLE}
    assert {row["part_number"] for row in context["part_numbers"]} <= archived_pns
    assert {user["id"] for user in context["users"]} >= {world.builder.admin.user_id}
    assert "badge_barcode" not in json.dumps(context)

    run = _run(world, run_id)
    assert run["state"] == "RUNNING"
    assert run["exported_flows"] == len(flows)
    assert run["manifest_sha256"] == report.details["manifest_sha256"]
    records = _records(world, run_id)
    assert set(records) == flows
    assert all(
        record["purged_at"] is None and record["skipped_at"] is None for record in records.values()
    )
    assert _movement_count(world) == movements_before


def test_the_records_carry_the_quantities(world: World, first_run: dict[str, Any]) -> None:
    records = _records(world, int(first_run["id"]))
    stocked = records[min(world.components["stocked"])]
    assert (stocked["flow_status"], stocked["received_quantity"], stocked["stocked_quantity"]) == (
        "STOCKED",
        5,
        5,
    )
    assert stocked["stocked_area_id"] == world.builder.areas["STOCK"]
    assert stocked["received_demand_id"] is not None
    assert stocked["adjusted_quantity"] is None and stocked["scrapped_quantity"] is None
    scrapped = records[min(world.components["scrapped"])]
    assert (scrapped["flow_status"], scrapped["scrapped_quantity"]) == ("SCRAPPED", 4)
    assert scrapped["scrapped_area_id"] == world.builder.areas["MAT"]
    added = records[min(world.components["added"])]
    assert (added["adjusted_quantity"], added["received_quantity"], added["stocked_quantity"]) == (
        7,
        None,
        7,
    )
    split = [records[flow] for flow in sorted(world.components["split"])]
    assert [record["flow_status"] for record in split] == ["SPLIT", "STOCKED", "STOCKED"]
    assert sum(record["stocked_quantity"] or 0 for record in split) == 10
    undone = [records[flow] for flow in sorted(world.components["undone_split"])]
    assert {record["flow_status"] for record in undone} == {"STOCKED", "REVERSED"}
    assert all(record["void_command_ids"] for record in undone)
    assert all(not record["void_command_ids"] for record in split)
    merge = [records[flow] for flow in sorted(world.components["merge"])]
    assert [record["flow_status"] for record in merge] == ["MERGED", "MERGED", "STOCKED"]
    for record in records.values():
        assert record["movement_count"] >= 1
        assert record["first_movement_id"] <= record["last_movement_id"]


def _raw_lines(path: Path) -> list[tuple[bytes, dict[str, Any]]]:
    with gzip.open(path, "rb") as stream:
        return [(line, json.loads(line)) for line in stream]


def _one_row(
    rows: list[dict[str, Any]], kind: str, reversed_ids: set[int]
) -> dict[str, Any] | None:
    """The one row of ``kind`` that no REVERSED row undid, or None."""
    found = [row for row in rows if row["movement_type"] == kind and row["id"] not in reversed_ids]
    assert len(found) <= 1, (rows[0]["quantity_flow_id"], kind)
    return found[0] if found else None


def _whole_row_records(
    movements: list[tuple[bytes, dict[str, Any]]], lineage: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Each flow's record fields derived from its whole Movement rows (the reference)."""
    reversed_ids = {
        row["reverses_movement_id"]
        for _line, row in movements
        if row["movement_type"] == "REVERSED" and row["reverses_movement_id"] is not None
    }
    rows_of: dict[int, list[dict[str, Any]]] = {}
    lines_of: dict[int, list[bytes]] = {}
    for line, row in movements:
        rows_of.setdefault(row["quantity_flow_id"], []).append(row)
        lines_of.setdefault(row["quantity_flow_id"], []).append(line)
    void: dict[int, set[str]] = {}
    for edge in lineage:
        if edge["void"]:
            for key in ("parent_flow_id", "child_flow_id"):
                void.setdefault(edge[key], set()).add(str(edge["device_event_id"]))
    expected: dict[int, dict[str, Any]] = {}
    for flow_id, rows in rows_of.items():
        received = _one_row(rows, "RECEIVED", set())
        adjusted = _one_row(rows, "QUANTITY_ADJUSTED", reversed_ids)
        stocked = _one_row(rows, "STOCKED", reversed_ids)
        scrapped = _one_row(rows, "SCRAPPED", reversed_ids)
        demand = None
        if received is not None:
            demand = ((received["metadata"] or {}).get("context") or {}).get("work_order_demand_id")
        expected[flow_id] = {
            "content_sha256": hashlib.sha256(b"".join(lines_of[flow_id])).hexdigest(),
            "movement_count": len(rows),
            "first_movement_id": min(row["id"] for row in rows),
            "last_movement_id": max(row["id"] for row in rows),
            "received_quantity": None if received is None else received["quantity"],
            "received_demand_id": None if demand is None else int(demand),
            "received_at": None
            if received is None
            else datetime.datetime.fromisoformat(received["occurred_at"]),
            "adjusted_quantity": None if adjusted is None else adjusted["quantity"],
            "stocked_quantity": None if stocked is None else stocked["quantity"],
            "stocked_area_id": None if stocked is None else stocked["to_area_id"],
            "scrapped_quantity": None if scrapped is None else scrapped["quantity"],
            "scrapped_area_id": None if scrapped is None else scrapped["to_area_id"],
            "void_command_ids": sorted(void.get(flow_id, set())),
        }
    return expected


def _sorted_id_hash(ids: list[int]) -> str:
    """The manifest's id-set hash by its definition: the sorted ids, decimal, one per line."""
    return hashlib.sha256(
        "".join(f"{value}\n" for value in sorted(ids)).encode("ascii")
    ).hexdigest()


def test_the_records_and_the_manifest_follow_from_the_whole_rows(
    world: World, folders: JobFolders, first_run: dict[str, Any]
) -> None:
    """The export keeps a few fields per flow and hashes ids as they come: nothing differs."""
    run_id = int(first_run["id"])
    folder = cast(Path, folders.archive_dir) / ".partial" / str(_run(world, run_id)["archive_name"])
    movements = _raw_lines(folder / "movements.jsonl.gz")
    flow_rows = _lines(folder / "flows.jsonl.gz")
    lineage = _lines(folder / "lineage.jsonl.gz")
    routes = _lines(folder / "assigned_routes.jsonl.gz")
    expected = _whole_row_records(movements, lineage)
    stored = _records(world, run_id)
    assert set(stored) == set(expected) == {row["flow"]["id"] for row in flow_rows}
    for row in flow_rows:
        flow_id = row["flow"]["id"]
        in_file = row["record"]
        for key, value in expected[flow_id].items():
            assert stored[flow_id][key] == value, (flow_id, key)
            if key == "received_at":
                assert (in_file[key] is None) == (value is None), flow_id
                if value is not None:
                    assert datetime.datetime.fromisoformat(in_file[key]) == value, flow_id
            else:
                assert in_file[key] == value, (flow_id, key)
    # The Planned flow's route is in the routes file with its steps in route order.
    planned = min(world.components["planned"])
    route_id = next(
        row["flow"]["assigned_route_id"] for row in flow_rows if row["flow"]["id"] == planned
    )
    [route] = [row for row in routes if row["assigned_route"]["id"] == route_id]
    steps = world.builder.column(
        "SELECT to_jsonb(s) FROM assigned_route_steps s WHERE s.assigned_route_id = :id"
        " ORDER BY s.sequence, s.id",
        id=route_id,
    )
    assert len(route["steps"]) == 3
    assert [step["id"] for step in route["steps"]] == [step["id"] for step in steps]
    assert [step["area_id"] for step in route["steps"]] == [
        world.builder.areas[name] for name in ("MAT", "DEBURR", "STOCK")
    ]
    # Every data file's manifest entry: its bytes, rows and the hash of its sorted ids.
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    ids_of = {
        "movements.jsonl.gz": [row["id"] for _line, row in movements],
        "flows.jsonl.gz": [row["flow"]["id"] for row in flow_rows],
        "lineage.jsonl.gz": [row["id"] for row in lineage],
        "assigned_routes.jsonl.gz": [row["assigned_route"]["id"] for row in routes],
    }
    for file, ids in ids_of.items():
        data = (folder / file).read_bytes()
        assert manifest["files"][file] == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "rows": len(ids),
            "id_set_sha256": _sorted_id_hash(ids),
        }, file
    assert manifest["counts"]["assigned_routes"] == len(routes) >= 1


#: The context tables as the export reads them (key, columns), for the reference below.
_CONTEXT_TABLES = {
    name: (table, key, columns) for name, table, key, columns in history_archive_job._CONTEXT_TABLES
}


def _context_rows(connection: sa.Connection, name: str, ids: set[Any]) -> list[dict[str, Any]]:
    """The rows of one context table as the export before the rehearsal fix selected them."""
    if not ids:
        return []
    table, key, columns = _CONTEXT_TABLES[name]
    array = "text[]" if key in ("station_id", "part_number") else "integer[]"
    return [
        dict(row.value)
        for row in connection.execute(
            sa.text(
                f"SELECT to_jsonb(t) AS value FROM (SELECT {columns} FROM {table}"
                f" WHERE {key} = ANY(CAST(:ids AS {array}))) t ORDER BY t.{key}"
            ),
            {"ids": sorted(ids)},
        )
    ]


def test_the_streamed_context_is_the_document_the_export_held_before(
    world: World, folders: JobFolders, first_run: dict[str, Any]
) -> None:
    """context.json is streamed row by row (B2 rehearsal D2); its bytes are those of the
    whole document as the export built and serialized it before, from the same rows."""
    run = _run(world, int(first_run["id"]))
    folder = cast(Path, folders.archive_dir) / ".partial" / str(run["archive_name"])
    ids: dict[str, set[Any]] = {name: set() for name in _CONTEXT_TABLES}

    def add(name: str, value: Any) -> None:
        if value is not None:
            ids[name].add(value)

    for row in _lines(folder / "movements.jsonl.gz"):
        for key in ("from_area_id", "to_area_id"):
            add("areas", row[key])
        add("operations", row["operation_id"])
        for key in ("source_machine_id", "destination_machine_id"):
            add("machines", row[key])
        add("workers", row["worker_id"])
        add("scan_stations", row["station_id"])
        add("users", ((row["metadata"] or {}).get("context") or {}).get("actor_user_id"))
    for row in _lines(folder / "flows.jsonl.gz"):
        add("areas", row["flow"]["current_area_id"])
        add("machines", row["flow"]["current_machine_id"])
        for key in ("stocked_area_id", "scrapped_area_id"):
            add("areas", row["record"][key])
        add("work_order_demands", row["record"]["received_demand_id"])
        add("part_numbers", row["record"]["part_number"])
    for row in _lines(folder / "assigned_routes.jsonl.gz"):
        for step in row["steps"]:
            add("areas", step["area_id"])
            add("operations", step["operation_id"])
            add("machines", step["preferred_machine_id"])
    add("users", run["requested_by_user_id"])
    with world.owner.connect() as connection:
        document = {
            name: _context_rows(connection, name, ids[name])
            for name in _CONTEXT_TABLES
            if name != "work_orders"
        }
        document["work_orders"] = _context_rows(
            connection,
            "work_orders",
            {row["work_order_id"] for row in document["work_order_demands"]},
        )
    assert document["work_order_demands"] and document["part_numbers"] and document["areas"]
    assert (folder / "context.json").read_bytes() == archive_files.readable_json(document)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"a": []},
        {"a": [], "b": [{}], "c": [{"k": []}, {"k": {}}]},
        {
            "areas": [{"id": 1, "name": "Matériau “A”"}, {"id": 2, "name": "B\nC"}],
            "work_order_demands": [
                {"id": 7, "job_numbers": ["J-1", "J-2"], "due": None, "x": {"y": [1, {"z": 2.5}]}}
            ],
            "workers": [],
        },
    ],
)
def test_readable_sections_are_the_readable_json_bytes(
    tmp_path: Path, document: dict[str, list[Any]]
) -> None:
    path = tmp_path / "context.json"
    archive_files.write_readable_sections(
        path, ((key, iter(rows)) for key, rows in sorted(document.items()))
    )
    assert path.read_bytes() == archive_files.readable_json(document)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        archive_files.write_readable_sections(path, [])
    with pytest.raises(ValueError, match="keys must be sorted"):
        archive_files.write_readable_sections(tmp_path / "unsorted.json", [("b", []), ("a", [])])


def _file_hashes(records: dict[int, tuple[list[tuple[int, bytes]], int]]) -> Any:
    """A ``_FileHashes`` that expects each flow's record: (its lines, its last Movement id)."""
    hashes = history_archive_job._FileHashes()
    for flow_id, (lines, last) in records.items():
        content = hashlib.sha256(b"".join(line for _, line in lines)).hexdigest()
        hashes.expect(flow_id, content, last)
    return hashes


def test_the_file_check_holds_only_the_flows_whose_movements_are_coming() -> None:
    """The Movements file in id order, flows interleaved: each flow checked at its last line."""
    lines = {7: [(1, b"a\n"), (4, b"d\n")], 3: [(2, b"b\n"), (3, b"c\n"), (6, b"f\n")]}
    in_order = sorted(
        (
            (movement_id, flow_id, line)
            for flow_id, rows in lines.items()
            for movement_id, line in rows
        )
    )
    hashes = _file_hashes({7: (lines[7], 4), 3: (lines[3], 6)})
    for movement_id, flow_id, line in in_order:
        hashes.add(flow_id, movement_id, line)
        if movement_id == 4:
            assert set(hashes._open) == {3}  # flow 7 is checked and released
    hashes.check()
    assert hashes._open == {}


@pytest.mark.parametrize(
    ("feed", "message"),
    [
        # A line changed: the flow's hash differs at its last line.
        ([(7, 1, b"a\n"), (7, 4, b"X\n"), (3, 6, b"f\n")], "Quantity Flow 7 in"),
        # A line missing: the flow never reaches its last Movement.
        ([(7, 1, b"a\n"), (3, 6, b"f\n")], "Quantity Flow 7 in"),
        # A line after the flow's last Movement.
        ([(7, 1, b"a\n"), (7, 4, b"d\n"), (3, 6, b"f\n"), (7, 8, b"h\n")], "Quantity Flow 7 in"),
        # Two flows wrong: the smaller is named.
        ([(7, 1, b"a\n"), (7, 4, b"d\n"), (3, 6, b"X\n")], "Quantity Flow 3 in"),
        # A line of a flow without a record.
        ([(7, 1, b"a\n"), (7, 4, b"d\n"), (3, 6, b"f\n"), (9, 9, b"i\n")], "without a record"),
    ],
)
def test_the_file_check_names_the_first_problem(
    feed: list[tuple[int, int, bytes]], message: str
) -> None:
    hashes = _file_hashes({7: ([(1, b"a\n"), (4, b"d\n")], 4), 3: ([(6, b"f\n")], 6)})
    for flow_id, movement_id, line in feed:
        hashes.add(flow_id, movement_id, line)
    with pytest.raises(history_archive_job._RunFailed, match=message):
        hashes.check()


def test_the_id_set_hash_needs_ascending_ids() -> None:
    ids = archive_files.IdSet()
    for value in (3, 17, 120):
        ids.add(value)
    assert ids.sha256() == _sorted_id_hash([120, 3, 17])
    assert archive_files.IdSet().sha256() == _sorted_id_hash([])
    with pytest.raises(archive_files.IdOrderError):
        ids.add(120)
    with pytest.raises(archive_files.IdOrderError):
        ids.add(5)


def test_the_verification_skips_a_component_changed_after_the_export(
    world: World, job: Engine, folders: JobFolders, first_run: dict[str, Any]
) -> None:
    run_id = int(first_run["id"])
    # An Undo of the old full Scrap writes a REVERSED row and reopens the flow.
    world.builder.undo("MAT", world.part_numbers["undone"], world.scrap_event)
    report = history_archive_job.verify(job, folders, run_id)
    _done(report, "verified")
    assert report.details["skipped_components"] == 1
    assert report.details["skipped_flows"] == len(world.components["undone"])
    run = _run(world, run_id)
    name = str(run["archive_name"])
    archive_dir = cast(Path, folders.archive_dir)
    assert not (archive_dir / ".partial" / name).exists()
    published = archive_dir / name
    assert published.is_dir()
    assert "movements.jsonl.gz: OK" in _sha256sum_check(published)
    assert (run["state"], run["skipped_components"]) == ("RUNNING", 1)
    records = _records(world, run_id)
    skipped = {flow for flow, record in records.items() if record["skipped_at"] is not None}
    assert skipped == world.components["undone"]


def test_without_a_copy_folder_the_run_waits_for_a_confirmation(
    world: World,
    job: Engine,
    folders: JobFolders,
    first_run: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = int(first_run["id"])
    report = history_archive_job.copy(job, folders, run_id)
    _done(report, "waiting_for_copy")
    assert report.state == "WAITING_FOR_COPY"
    assert report.details["copied_to"] is None
    # Waiting and unconfirmed: next says wait, status reports no finding yet.
    _done(history_archive_job.next_run(job, folders), "wait")
    status = history_archive_job.status(job, folders)
    _done(status, "ok")
    assert status.details["max_wait_days"] == 7
    assert status.details["open_run"]["id"] == run_id
    assert status.details["open_run"]["state"] == "WAITING_FOR_COPY"
    # The waiting limit is the setting: a longer wait is a finding.
    later = history_archive.now() + datetime.timedelta(days=2)
    with monkeypatch.context() as patch:
        patch.setattr(history_archive_job, "_now", lambda: later)
        with world.owner.begin() as connection:
            connection.execute(sa.text("UPDATE application_policy SET archive_max_wait_days = 1"))
        try:
            overdue = history_archive_job.status(job, folders)
        finally:
            with world.owner.begin() as connection:
                connection.execute(
                    sa.text("UPDATE application_policy SET archive_max_wait_days = 7")
                )
    assert (overdue.exit_code, overdue.result) == (history_archive_job.EXIT_REFUSED, "finding")
    assert "copy_confirmation_overdue" in overdue.details["findings"]
    assert overdue.details["max_wait_days"] == 1
    # Copying again without a confirmation changes nothing.
    _done(history_archive_job.copy(job, folders, run_id), "waiting_for_copy")
    run = _run(world, run_id)
    response = world.builder.admin.post(
        f"/api/history-archive/runs/{run_id}/copy-confirmation",
        json={"manifest_sha256": run["manifest_sha256"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["copy_confirmed_by"]["user_id"] == world.builder.admin.user_id
    nxt = history_archive_job.next_run(job, folders)
    _done(nxt, "copy")
    assert nxt.run_id == run_id
    report = history_archive_job.copy(job, folders, run_id)
    # Since step 2 a confirmed copy goes back to RUNNING: the backup and the
    # purge come next (tests/test_history_purge.py).
    _done(report, "confirmed")
    run = _run(world, run_id)
    assert (run["state"], run["result"], run["copied_at"]) == ("RUNNING", None, None)
    _done(history_archive_job.next_run(job, folders), "backup")
    audits = _run_audits(world, run_id)
    assert [row["event_type"] for row in audits] == [
        "CREATED",
        "UPDATED",
        "UPDATED",
        "UPDATED",
        "UPDATED",
    ]
    assert _states(audits) == [
        "APPROVED",
        "RUNNING",
        "WAITING_FOR_COPY",
        "WAITING_FOR_COPY",
        "RUNNING",
    ]
    assert [row["metadata"]["source"] for row in audits] == [
        "web",
        "maintenance",
        "maintenance",
        "web",
        "maintenance",
    ]
    assert audits[0]["actor_user_id"] == world.builder.admin.user_id
    assert audits[1]["actor_user_id"] is None
    assert audits[-1]["after_data"]["groups"] == ["MOVEMENT_HISTORY"]
    # This module never purges: the next start finds the run stale and fails it.
    _end_without_purge(job, folders, run_id)
    _done(history_archive_job.next_run(job, folders), "none")


def _end_without_purge(job: Engine, folders: JobFolders, run_id: int) -> None:
    """A RUNNING run that is not purged here: the next start's recover fails it."""
    report = history_archive_job.recover(job, folders)
    _done(report, "recovered")
    assert run_id in report.details["failed_run_ids"]


# ---------------------------------------------------------------------------
# Further runs: a changed file, a copy folder, failures
# ---------------------------------------------------------------------------


def test_a_changed_file_fails_the_verification(
    world: World, job: Engine, folders: JobFolders
) -> None:
    run = _request(world, "Second archive")
    run_id = int(run["id"])
    report = history_archive_job.export(job, folders, run_id)
    _done(report, "exported")
    folder = cast(Path, folders.archive_dir) / ".partial" / str(report.details["archive_name"])
    with open(folder / "README.txt", "ab") as stream:
        stream.write(b"tampered\n")
    report = history_archive_job.verify(job, folders, run_id)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    assert report.result == "failed"
    assert report.error is not None and "README.txt" in report.error.message
    stored = _run(world, run_id)
    assert stored["state"] == "FAILED"
    assert "README.txt" in str(stored["failure_reason"])
    assert not (
        cast(Path, folders.archive_dir) / str(report.details.get("archive_name", "x"))
    ).exists()
    status = history_archive_job.status(job, folders)
    assert status.exit_code == 1
    assert "last_run_failed" in status.details["findings"]
    # recover removes the unfinished folder of the failed run.
    recovered = history_archive_job.recover(job, folders)
    _done(recovered, "recovered")
    assert str(folder) in recovered.details["removed_partial"]
    assert not folder.exists()


def test_a_copy_folder_copies_the_archive_unattended(
    world: World, job: Engine, folders: JobFolders, copy_dir: Path
) -> None:
    with_copy = JobFolders(folders.archive_dir, copy_dir)
    run_id = int(_request(world, "Third archive")["id"])
    _done(history_archive_job.export(job, with_copy, run_id), "exported")
    _done(history_archive_job.verify(job, with_copy, run_id), "verified")
    report = history_archive_job.copy(job, with_copy, run_id)
    _done(report, "copied")
    name = str(_run(world, run_id)["archive_name"])
    assert report.details["copied_to"] == str(copy_dir / name)
    assert "manifest.json: OK" in _sha256sum_check(copy_dir / name)
    run = _run(world, run_id)
    assert (run["state"], run["result"]) == ("RUNNING", None)
    assert run["copied_at"] is not None and run["copy_confirmed_at"] is None
    assert not list((copy_dir / ".partial").iterdir())
    # The copy is audited although the state stays RUNNING.
    audits = _run_audits(world, run_id)
    assert audits[-1]["metadata"]["command"] == "copy"
    assert audits[-1]["after_data"]["copied_at"] is not None
    _done(history_archive_job.next_run(job, with_copy), "backup")
    _end_without_purge(job, folders, run_id)


def test_an_unavailable_copy_folder_makes_the_run_wait(
    world: World, job: Engine, folders: JobFolders, tmp_path: Path
) -> None:
    missing = JobFolders(folders.archive_dir, tmp_path / "unplugged")
    run_id = int(_request(world, "Fourth archive")["id"])
    _done(history_archive_job.export(job, missing, run_id), "exported")
    _done(history_archive_job.verify(job, missing, run_id), "verified")
    report = history_archive_job.copy(job, missing, run_id)
    _done(report, "waiting_for_copy")
    assert "does not exist" in str(report.details["copy_problem"])
    _done(history_archive_job.next_run(job, missing), "wait")
    # With the folder back, the next start copies.
    (tmp_path / "unplugged").mkdir()
    _done(history_archive_job.next_run(job, missing), "copy")
    _done(history_archive_job.copy(job, missing, run_id), "copied")
    _end_without_purge(job, folders, run_id)


@pytest.mark.parametrize("confirmed", [False, True], ids=["copy-folder", "confirmation"])
def test_a_published_archive_changed_after_its_verification_fails_the_copy(
    world: World, job: Engine, folders: JobFolders, copy_dir: Path, confirmed: bool
) -> None:
    """Checked right before a copy, or before a confirmation is accepted (never while waiting)."""
    with_copy = JobFolders(folders.archive_dir, None if confirmed else copy_dir)
    run_id = int(_request(world, "Fifth archive")["id"])
    _done(history_archive_job.export(job, with_copy, run_id), "exported")
    _done(history_archive_job.verify(job, with_copy, run_id), "verified")
    published = cast(Path, folders.archive_dir) / str(_run(world, run_id)["archive_name"])
    with open(published / "context.json", "ab") as stream:
        stream.write(b" ")
    if confirmed:
        _done(history_archive_job.copy(job, with_copy, run_id), "waiting_for_copy")
        response = world.builder.admin.post(
            f"/api/history-archive/runs/{run_id}/copy-confirmation",
            json={"manifest_sha256": _run(world, run_id)["manifest_sha256"]},
        )
        assert response.status_code == 200, response.text
    report = history_archive_job.copy(job, with_copy, run_id)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    run = _run(world, run_id)
    assert run["state"] == "FAILED"
    assert "changed after its verification" in str(run["failure_reason"])


def test_an_export_finding_more_than_the_preview_fails(
    world: World, job: Engine, folders: JobFolders
) -> None:
    run_id = _insert_run(world, cutoff=_default_cutoff(), components=1, flows=1, movements=1)
    report = history_archive_job.export(job, folders, run_id)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    run = _run(world, run_id)
    assert run["state"] == "FAILED"
    assert "more than the run's preview" in str(run["failure_reason"])
    assert _records(world, run_id) == {}
    assert not list((cast(Path, folders.archive_dir) / ".partial").glob(f"run-{run_id}-*"))


def test_nothing_eligible_ends_the_run(world: World, job: Engine, folders: JobFolders) -> None:
    zone = history_archive.site_zone()
    run_id = _insert_run(
        world, cutoff=history_archive.start_of_day(datetime.date(2001, 1, 1), zone)
    )
    report = history_archive_job.export(job, folders, run_id)
    _done(report, "nothing_eligible")
    run = _run(world, run_id)
    assert (run["state"], run["archive_name"], run["exported_at"]) == (
        "NOTHING_ELIGIBLE",
        None,
        None,
    )
    assert run["finished_at"] is not None


def test_a_cutoff_the_shortest_period_no_longer_allows_fails_the_export(
    world: World, job: Engine, folders: JobFolders
) -> None:
    cutoff = _default_cutoff()
    run_id = _insert_run(world, cutoff=cutoff)
    with world.owner.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE application_policy SET retention_period_months = 600,"
                " shortest_retention_months = 600"
            )
        )
    try:
        report = history_archive_job.export(job, folders, run_id)
    finally:
        with world.owner.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE application_policy SET shortest_retention_months = 12,"
                    " retention_period_months = 24"
                )
            )
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    run = _run(world, run_id)
    assert run["state"] == "FAILED" and run["started_at"] is None
    assert "shortest allowed period of 600 months" in str(run["failure_reason"])


def test_an_existing_archive_name_fails_the_export(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed = datetime.datetime(2030, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    monkeypatch.setattr(history_archive_job, "_now", lambda: fixed)
    run_id = _insert_run(world, cutoff=_default_cutoff())
    (cast(Path, folders.archive_dir) / f"run-{run_id}-20300102T030405Z").mkdir()
    report = history_archive_job.export(job, folders, run_id)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    assert "already exists" in str(_run(world, run_id)["failure_reason"])


def test_recover_fails_a_stale_running_run(world: World, job: Engine, folders: JobFolders) -> None:
    run_id = _insert_run(world, cutoff=_default_cutoff())
    with world.owner.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('partflow.archive_run_id', :id, true)"), {"id": str(run_id)}
        )
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'RUNNING', started_at = now()"
                " WHERE id = :id"
            ),
            {"id": run_id},
        )
    partial = cast(Path, folders.archive_dir) / ".partial" / f"run-{run_id}-20300101T000000Z"
    partial.mkdir(parents=True)
    unknown = cast(Path, folders.archive_dir) / ".partial" / "something-else"
    unknown.mkdir()
    _done(history_archive_job.next_run(job, folders), "running")
    report = history_archive_job.recover(job, folders)
    _done(report, "recovered")
    assert report.details["failed_run_ids"] == [run_id]
    assert str(partial) in report.details["removed_partial"]
    assert str(unknown) in report.details["kept_partial"]
    run = _run(world, run_id)
    assert run["state"] == "FAILED"
    assert "stopped while the run was RUNNING" in str(run["failure_reason"])
    assert _states(_run_audits(world, run_id))[-1] == "FAILED"
    _done(history_archive_job.recover(job, folders), "nothing_to_recover")


def test_the_job_lock_refuses_a_second_command(
    world: World, job: Engine, folders: JobFolders
) -> None:
    run_id = _insert_run(world, cutoff=_default_cutoff())
    with world.owner.connect() as holder:
        holder.execute(
            sa.text("SELECT pg_advisory_lock(hashtextextended('partflow:history-archive', 0))")
        )
        try:
            report = history_archive_job.export(job, folders, run_id)
        finally:
            holder.execute(
                sa.text(
                    "SELECT pg_advisory_unlock(hashtextextended('partflow:history-archive', 0))"
                )
            )
            holder.commit()
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_REFUSED, "refused")
    assert report.error is not None and report.error.code == "job_running"
    assert _run(world, run_id)["state"] == "APPROVED"


def test_commands_refuse_an_unknown_run_or_another_state(
    world: World, job: Engine, folders: JobFolders
) -> None:
    for step in (history_archive_job.export, history_archive_job.verify, history_archive_job.copy):
        report = step(job, folders, 2_000_000)
        assert (report.exit_code, report.error.code if report.error else None) == (
            history_archive_job.EXIT_REFUSED,
            "unknown_run",
        )
    run_id = _insert_run(world, cutoff=_default_cutoff())
    for step in (history_archive_job.verify, history_archive_job.copy):
        report = step(job, folders, run_id)
        assert report.exit_code == history_archive_job.EXIT_REFUSED
        assert report.error is not None and report.error.code == "wrong_state"
    assert _run(world, run_id)["state"] == "APPROVED"
    no_folder = history_archive_job.export(job, JobFolders(None, None), run_id)
    assert (no_folder.exit_code, no_folder.error.code if no_folder.error else None) == (
        history_archive_job.EXIT_COULD_NOT_RUN,
        "archive_dir_missing",
    )


def test_nothing_is_ever_deleted(world: World) -> None:
    """Runs of step 1 archive without deleting: every Movement is still there."""
    assert (
        world.builder.scalar(
            "SELECT count(*) FROM history_archived_flows WHERE purged_at IS NOT NULL"
        )
        == 0
    )
    flows = _all(world, _ELIGIBLE)
    assert world.builder.scalar(
        "SELECT count(DISTINCT quantity_flow_id) FROM part_movements"
        " WHERE quantity_flow_id = ANY(:ids)",
        ids=sorted(flows),
    ) == len(flows)


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: Engine | None,
    folders: JobFolders,
    *args: str,
) -> tuple[int, dict[str, Any]]:
    monkeypatch.setattr(cli, "_history_archive_engine", lambda: engine)
    for name, value in (
        (history_archive_job.ARCHIVE_DIR_ENV, folders.archive_dir),
        (history_archive_job.COPY_DIR_ENV, folders.copy_dir),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(value))
    code = cli.main(["history-archive", *args])
    out = capsys.readouterr().out
    return code, cast(dict[str, Any], json.loads(out))


def test_the_cli_prints_one_json_report_with_its_exit_code(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The CLI disposes the engine it gets: give it its own.
    def engine() -> Engine:
        fresh = owner_engine(world.url, poolclass=NullPool)
        roles = app_mode_roles()
        if roles is not None:

            @event.listens_for(fresh, "connect")
            def _maintenance(dbapi_connection: Any, record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute(f'SET SESSION ROLE "{roles.maintenance}"')
                cursor.close()
                dbapi_connection.commit()

        return fresh

    code, document = _cli(monkeypatch, capsys, engine(), folders, "settings")
    assert code == 0
    assert (document["command"], document["result"], document["start_minute"]) == (
        "history-archive settings",
        "read",
        20,
    )
    assert document["archive_dir"] == str(folders.archive_dir)
    # The common keys (build step 3 adds request_id: the deletion request worked on;
    # build step 5 view_id: the view of an archive worked on).
    assert list(document)[:11] == [
        "report_version",
        "command",
        "result",
        "exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "run_id",
        "request_id",
        "view_id",
        "state",
    ]
    assert list(document)[-1] == "error"
    _close_open_runs(world)
    code, document = _cli(monkeypatch, capsys, engine(), folders, "next")
    assert (code, document["result"], document["run_id"]) == (0, "none", None)
    code, document = _cli(monkeypatch, capsys, engine(), folders, "export", "--run", "2000000")
    assert (code, document["result"], document["error"]["code"]) == (1, "refused", "unknown_run")
    code, document = _cli(monkeypatch, capsys, engine(), JobFolders(None, None), "recover")
    assert (code, document["error"]["code"]) == (2, "archive_dir_missing")
    code, document = _cli(monkeypatch, capsys, None, folders, "status")
    assert (code, document["error"]["code"]) == (2, "configuration_invalid")
    code, document = _cli(monkeypatch, capsys, engine(), folders, "status")
    assert code in (0, 1)
    assert document["result"] in ("ok", "finding")
    assert document["max_wait_days"] == 7
    assert document["archive_dir"]["usable"] is True
    assert isinstance(document["archive_dir"]["free_bytes"], int)
    run_id = int(_request(world, "CLI archive")["id"])
    code, document = _cli(monkeypatch, capsys, engine(), folders, "next")
    assert (code, document["result"], document["run_id"], document["state"]) == (
        0,
        "export",
        run_id,
        "APPROVED",
    )
    code, document = _cli(monkeypatch, capsys, engine(), folders, "export", "--run", str(run_id))
    assert (code, document["result"], document["state"]) == (0, "exported", "RUNNING")
    assert document["counts"]["flows"] > 0
    code, document = _cli(monkeypatch, capsys, engine(), folders, "verify", "--run", str(run_id))
    assert (code, document["result"]) == (0, "verified")
    code, document = _cli(monkeypatch, capsys, engine(), folders, "copy", "--run", str(run_id))
    assert (code, document["result"], document["state"]) == (
        0,
        "waiting_for_copy",
        "WAITING_FOR_COPY",
    )
    _close_open_runs(world)
