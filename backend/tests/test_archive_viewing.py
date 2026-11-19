"""Tests of viewing an archive (Phase 16, history archival design D16 and §9.3; build step 5).

One module database: the data set of ``tests/archive_dataset.py`` and a
numbered Work Order whose release was split, built through the real API and
moved back in time, then archived and purged by one run (export, verify,
copy, purge). The API and every reader run as the application role in the
application-role test mode, the host job's commands as the maintenance role
(as the owner otherwise). In file order:

- the viewing settings and their texts;
- the refusals of Open for viewing (an unknown run, a run not finished, a run
  never verified);
- the main safety net: a view is requested, found by the cheap check (as the
  application role) and by ``next``, loaded by ``load-view``, and every figure
  every reader derives and every reconcile check are the same before and
  after the load (and after the close);
- the viewer: the search by each filter against the archive file itself,
  paging through every Movement, a flow's whole history and lineage, the
  names as they were at export (an Area renamed since), and the texts of a
  bad search;
- closing by a person and by the days, ``status`` and its finding;
- the load's failures: a tampered file, a missing file, a missing archive
  folder; a stopped load that ``recover`` fails, its rows removed;
- the setting of runs loaded at once; cancelling; the job's command line.
"""

import datetime
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import psycopg
import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.application import history_archive, history_archive_job, history_archive_views
from app.application.history_archive_job import JobFolders
from app.core.config import get_settings
from app.infrastructure import archive_files, models
from app.main import create_app
from tests.archive_dataset import (
    Builder,
    Dataset,
    backdate,
    build_dataset,
    copied_run,
    done,
    job_engine,
    purge_run,
    reader_figures,
    reconcile_outcome,
    request_run,
    run_row,
)
from tests.auth_harness import station_device_client
from tests.conftest import owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"
_VIEWS = "/api/history-archive/views"
_POLICY = "/api/policies/data-retention"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@dataclass
class World:
    data: Dataset
    url: URL
    owner: Engine
    job: Engine
    client: TestClient
    folders: JobFolders
    run_id: int
    #: The numbered Work Order whose release was split: its number and flows.
    work_order_number: str
    work_order_flows: set[int]
    views: dict[str, int] = field(default_factory=dict)

    @property
    def builder(self) -> Builder:
        return self.data.builder

    @property
    def folder(self) -> Path:
        assert self.folders.archive_dir is not None
        return self.folders.archive_dir / str(run_row(self.owner, self.run_id)["archive_name"])


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    database = f"partflow_test_archive_viewing_{uuid.uuid4().hex[:8]}"
    cluster = make_url(os.environ[_DB_URL_ENV])
    admin = create_engine(cluster, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    url = cluster.set(database=database)
    original = os.environ[_DB_URL_ENV]
    owner = owner_engine(url)
    job = job_engine(url)
    folders = JobFolders(
        tmp_path_factory.mktemp("view_archive"), tmp_path_factory.mktemp("view_archive_copy")
    )
    try:
        command.upgrade(_alembic_config(url), "head")
        os.environ[_DB_URL_ENV] = url.render_as_string(hide_password=False)
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            builder = Builder(station_device_client(client), url)
            builder.environment()
            data = build_dataset(builder, owner)
            # A numbered Work Order whose release was split by a partial transfer.
            number = f"VIEW-{uuid.uuid4().hex[:6].upper()}"
            pn = f"AD-VIEWWO-{uuid.uuid4().hex[:6].upper()}"
            response = builder.admin.post(
                "/api/work-orders",
                json={
                    "work_order_number": number,
                    "lines": [{"part_number": pn, "requested_quantity": 10}],
                },
            )
            assert response.status_code == 201, response.text
            body = response.json()
            flow = builder.release_line(int(body["id"]), int(body["demands"][0]["id"]), pn, 10)
            moved = builder.transfer("MAT", "DEBURR", flow, pn, 4)
            builder.stock("DEBURR", moved, pn, 4)
            builder.stock("MAT", builder.active_of(builder.component(flow)), pn, 6)
            flows = builder.component(flow)
            backdate(owner, flows)
            data.part_numbers["view_wo"] = pn
            data.components["view_wo"] = flows
            run_id = request_run(builder, "Archive the data set for viewing")
            copied_run(job, folders, run_id)
            done(purge_run(job, owner, folders, run_id), "completed")
            yield World(data, url, owner, job, client, folders, run_id, number, flows)
    finally:
        job.dispose()
        owner.dispose()
        os.environ[_DB_URL_ENV] = original
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _detail(response: Any, status: int) -> str:
    assert response.status_code == status, response.text
    return str(response.json()["detail"])


def _view_row(world: World, view_id: int) -> dict[str, Any]:
    with world.owner.connect() as connection:
        return dict(
            connection.execute(
                sa.text("SELECT * FROM history_archive_views WHERE id = :id"), {"id": view_id}
            )
            .mappings()
            .one()
        )


def _rows(world: World, view_id: int) -> dict[str, int]:
    with world.owner.connect() as connection:
        return {
            table: int(
                connection.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE view_id = :view"),
                    {"view": view_id},
                ).scalar_one()
            )
            for table in models.ARCHIVE_VIEWER_TABLES
        }


def _set(world: World, **values: Any) -> None:
    _ok(world.builder.admin.put(_POLICY, json=values))


def _request(world: World, run_id: int | None = None) -> int:
    body = _ok(world.builder.admin.post(_VIEWS, json={"run_id": run_id or world.run_id}), 201)
    assert body["state"] == "REQUESTED", body
    return int(body["id"])


def _load(world: World, view_id: int) -> history_archive_job.JobReport:
    return history_archive_job.load_view(world.job, world.folders, view_id)


def _opened(world: World) -> int:
    """A view of the run, loaded (every other view of it ended first)."""
    _end_views(world)
    view_id = _request(world)
    done(_load(world, view_id), "loaded")
    return view_id


def _end_views(world: World) -> None:
    """Cancel requested views and close open ones, as people and the job do."""
    for view in cast(list[dict[str, Any]], _ok(world.builder.admin.get(_VIEWS))):
        if view["state"] == "REQUESTED":
            _ok(world.builder.admin.post(f"{_VIEWS}/{view['id']}/cancel"))
        elif view["state"] == "OPEN":
            _ok(world.builder.admin.post(f"{_VIEWS}/{view['id']}/close"))
            done(history_archive_job.close_view(world.job, world.folders, view["id"]), "closed")
        elif view["state"] == "CLOSING":
            done(history_archive_job.close_view(world.job, world.folders, view["id"]), "closed")


def _archive_movements(world: World) -> list[dict[str, Any]]:
    return [
        value for _line, value in archive_files.read_json_lines(world.folder / "movements.jsonl.gz")
    ]


def _newest_first(movements: list[dict[str, Any]]) -> list[int]:
    return [
        int(value["id"])
        for value in sorted(
            movements,
            key=lambda value: (datetime.datetime.fromisoformat(value["occurred_at"]), value["id"]),
            reverse=True,
        )
    ]


def _search(world: World, view_id: int, **params: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        _ok(world.builder.admin.get(f"{_VIEWS}/{view_id}/movements", params=params)),
    )


def _all_pages(world: World, view_id: int, limit: int, **params: Any) -> list[int]:
    found: list[int] = []
    before: int | None = None
    while True:
        page = _search(
            world, view_id, limit=limit, **({"before": before} if before else {}), **params
        )
        found.extend(int(entry["id"]) for entry in page["movements"])
        assert len(page["movements"]) <= limit
        if not page["has_more"]:
            assert page["next_before_movement_id"] is None
            return found
        before = page["next_before_movement_id"]
        assert before == found[-1]


# ---------------------------------------------------------------------------
# Settings and refusals
# ---------------------------------------------------------------------------


def test_the_viewing_settings(world: World) -> None:
    admin = world.builder.admin
    policy = _ok(admin.get(_POLICY))
    assert (policy["archive_view_days"], policy["archive_view_max_loaded"]) == (7, 1)
    for body, text in (
        (
            {"archive_view_days": 0},
            "The days a loaded archive stays open must be a whole number from 1 to 365.",
        ),
        (
            {"archive_view_days": 366},
            "The days a loaded archive stays open must be a whole number from 1 to 365.",
        ),
        (
            {"archive_view_max_loaded": 0},
            "The runs that may be loaded for viewing at once must be a whole number from 1 to 10.",
        ),
        (
            {"archive_view_max_loaded": 11},
            "The runs that may be loaded for viewing at once must be a whole number from 1 to 10.",
        ),
    ):
        assert _detail(admin.put(_POLICY, json=body), 422) == text
    assert admin.put(_POLICY, json={"archive_view_days": "7"}).status_code == 422
    changed = _ok(admin.put(_POLICY, json={"archive_view_days": 3, "archive_view_max_loaded": 2}))
    assert (changed["archive_view_days"], changed["archive_view_max_loaded"]) == (3, 2)
    with world.owner.connect() as connection:
        before, after = connection.execute(
            sa.text(
                "SELECT before_data, after_data FROM audit_events WHERE entity_type ="
                " 'ApplicationPolicy' AND entity_id = 'data-retention' ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert (before["archive_view_days"], after["archive_view_days"]) == (7, 3)
    assert (before["archive_view_max_loaded"], after["archive_view_max_loaded"]) == (1, 2)
    _set(world, archive_view_days=7, archive_view_max_loaded=1)


def test_only_a_finished_verified_run_opens_for_viewing(world: World) -> None:
    admin = world.builder.admin
    assert _detail(admin.post(_VIEWS, json={"run_id": 2_000_000}), 404) == (
        "There is no archival run 2000000."
    )
    assert admin.post(_VIEWS, json={"run_id": "1"}).status_code == 422
    assert admin.post(_VIEWS, json={"run_id": world.run_id, "reason": "x"}).status_code == 422
    # Old history for a second run, which stays open, then is cancelled unverified.
    pn = f"AD-UNVERIFIED-{uuid.uuid4().hex[:6].upper()}"
    flow = world.builder.release(pn, 2)
    world.builder.stock("MAT", flow, pn, 2)
    backdate(world.owner, world.builder.component(flow))
    second = request_run(world.builder, "A run that is not finished")
    assert _detail(admin.post(_VIEWS, json={"run_id": second}), 409) == (
        f"Run {second} is APPROVED and not finished: only the archive of a finished run can be"
        " opened for viewing."
    )
    _ok(admin.post(f"/api/history-archive/runs/{second}/cancel"))
    assert _detail(admin.post(_VIEWS, json={"run_id": second}), 409) == (
        f"Run {second} ended CANCELLED before its archive was verified, so it has no archive to"
        " view."
    )


# ---------------------------------------------------------------------------
# The main safety net: loading changes no figure and no check
# ---------------------------------------------------------------------------


def test_a_view_loads_the_archive_and_changes_no_figure(world: World) -> None:
    data = world.data
    figures = reader_figures(data)
    checks = reconcile_outcome(world.builder.engine)
    run = run_row(world.owner, world.run_id)
    body = _ok(world.builder.admin.post(_VIEWS, json={"run_id": world.run_id}), 201)
    view_id = int(body["id"])
    world.views["main"] = view_id
    assert body["to_load"] == {
        "archive_name": run["archive_name"],
        "movements": run["exported_movements"],
        "flows": run["exported_flows"],
        "lineage_edges": run["exported_lineage_edges"],
        "assigned_routes": run["exported_assigned_routes"],
        "archive_bytes": run["archive_bytes"],
    }
    assert (body["loaded"], body["open_until"], body["requested_by"]["user_id"]) == (
        None,
        None,
        world.builder.admin.user_id,
    )
    # The cheap check of every start, as the application role (archive.sh runs it
    # through the backend), and the job's next step.
    settings = history_archive_job.settings(world.builder.engine, world.folders)
    done(settings, "read")
    assert settings.details["viewing_pending"] is True
    assert [(entry["id"], entry["action"]) for entry in settings.details["pending_views"]] == [
        (view_id, "load_view")
    ]
    following = history_archive_job.next_run(world.job, world.folders)
    done(following, "load_view")
    assert (following.view_id, following.state, following.details["run"]) == (
        view_id,
        "REQUESTED",
        None,
    )
    report = _load(world, view_id)
    done(report, "loaded")
    document = history_archive_job.job_document(report)
    assert (document["view_id"], document["state"]) == (view_id, "OPEN")
    manifest = json.loads((world.folder / "manifest.json").read_bytes())
    files = manifest["files"]
    loaded = cast(dict[str, int], document["loaded"])
    assert loaded["movements"] == files["movements.jsonl.gz"]["rows"] == run["exported_movements"]
    assert loaded["flows"] == files["flows.jsonl.gz"]["rows"] == run["exported_flows"]
    assert loaded["lineage_edges"] == files["lineage.jsonl.gz"]["rows"]
    assert loaded["assigned_routes"] == files["assigned_routes.jsonl.gz"]["rows"]
    context = json.loads((world.folder / "context.json").read_bytes())
    assert loaded["names"] == sum(len(rows) for rows in context.values())
    assert _rows(world, view_id) == {
        "archive_view_movements": loaded["movements"],
        "archive_view_flows": loaded["flows"],
        "archive_view_lineage": loaded["lineage_edges"],
        "archive_view_routes": loaded["assigned_routes"],
        "archive_view_names": loaded["names"],
    }
    # Every figure and every check, before and after the load.
    assert reader_figures(data) == figures
    assert reconcile_outcome(world.builder.engine) == checks
    # The view and the run as Administration shows them.
    [view] = [view for view in _ok(world.builder.admin.get(_VIEWS)) if view["id"] == view_id]
    assert view["state"] == "OPEN" and view["loaded"] == loaded
    opened = datetime.datetime.fromisoformat(view["opened_at"])
    assert datetime.datetime.fromisoformat(view["open_until"]) == opened + datetime.timedelta(
        days=7
    )
    [shown] = [
        entry
        for entry in _ok(world.builder.admin.get("/api/history-archive/runs"))
        if entry["id"] == world.run_id
    ]
    assert (shown["viewable"], shown["view"]) == (True, {"id": view_id, "state": "OPEN"})
    # status: the open view, the space of the viewer tables, no finding of its own.
    status = history_archive_job.status(world.job, world.folders)
    assert [entry["id"] for entry in status.details["open_views"]] == [view_id]
    assert status.details["viewer_bytes"] > 0
    assert status.details["view_settings"] == {"days": 7, "max_loaded": 1}
    assert "view_not_closed" not in status.details["findings"]
    assert status.details["views_not_closed"] == []
    # Nothing is pending any more.
    assert (
        history_archive_job.settings(world.job, world.folders).details["viewing_pending"] is False
    )
    # The job's audit rows of the view, in order.
    with world.owner.connect() as connection:
        commands = [
            (row.event_type, row.actor_user_id is not None, row.metadata["command"])
            for row in connection.execute(
                sa.text(
                    "SELECT event_type, actor_user_id, metadata FROM audit_events WHERE"
                    " entity_type = 'HistoryArchiveView' AND entity_id = :id ORDER BY id"
                ),
                {"id": str(view_id)},
            )
        ]
    assert commands == [
        ("CREATED", True, "open"),
        ("UPDATED", False, "load-view"),
        ("UPDATED", False, "load-view"),
    ]


def test_a_loaded_view_counts_toward_no_database_size(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M1: the database-size measure of automatic runs leaves the viewer tables out,
    so opening an archive never makes an automatic run."""
    view_id = world.views["main"]
    assert _view_row(world, view_id)["state"] == "OPEN"
    assert _rows(world, view_id)["archive_view_movements"] > 0
    with world.job.connect() as connection:
        viewer = history_archive.viewer_bytes(connection)
        whole = history_archive.database_bytes(connection)
    assert 0 < viewer < whole
    # A database at exactly its limit once the viewer tables are subtracted, whatever
    # they hold when the evaluation reads them: below the limit, nothing created.
    limit = 20 * history_archive.GIB
    monkeypatch.setattr(
        history_archive,
        "database_bytes",
        lambda connection: limit + history_archive.viewer_bytes(connection),
    )
    _set(world, archive_automatic_runs=True, archive_measure="DATABASE_SIZE")
    try:
        report = history_archive_job.evaluate(world.job, world.folders)
    finally:
        _set(world, archive_automatic_runs=False, archive_measure="LIVE_MOVEMENT_ROWS")
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result) == (0, "below_limit"), document
    measure = cast(dict[str, Any], document["measure"])
    assert measure["viewer_bytes"] > 0
    assert (measure["value"], measure["database_bytes"], measure["passes"]) == (
        limit,
        limit + measure["viewer_bytes"],
        False,
    )
    assert document["message"] == (
        f"Database size {limit:,} bytes (20.0 GiB) is not above the limit 20 GiB, so no run was"
        " created."
    )


# ---------------------------------------------------------------------------
# The viewer
# ---------------------------------------------------------------------------


def test_the_search_finds_what_the_archive_holds(world: World) -> None:
    view_id = world.views["main"]
    movements = _archive_movements(world)
    every = _search(world, view_id)
    assert every["total"] == len(movements)
    # Paging through every Movement, newest first, without a gap or a repeat.
    assert _all_pages(world, view_id, 7) == _newest_first(movements)

    def expect(params: dict[str, Any], keep: Any) -> None:
        matching = [value for value in movements if keep(value)]
        page = _search(world, view_id, limit=200, **params)
        assert page["total"] == len(matching), params
        assert [int(entry["id"]) for entry in page["movements"]] == (
            _newest_first(matching)[:200]
        ), params

    pn = world.data.part_numbers["split#0"]
    expect({"part_number": pn.lower()}, lambda value: value["part_number"] == pn)
    flow = min(world.data.components["split#0"])
    expect({"quantity_flow_id": flow}, lambda value: value["quantity_flow_id"] == flow)
    area = world.builder.areas["DEBURR"]
    expect(
        {"area_id": area},
        lambda value: area in (value["from_area_id"], value["to_area_id"]),
    )
    operation = world.builder.operations["MAT"]
    expect({"operation_id": operation}, lambda value: value["operation_id"] == operation)
    expect(
        {"machine_id": 1},
        lambda value: 1 in (value["source_machine_id"], value["destination_machine_id"]),
    )
    expect({"worker_id": 1}, lambda value: value["worker_id"] == 1)
    zone = history_archive.site_zone()
    day = history_archive.site_date_of(
        datetime.datetime.fromisoformat(movements[0]["occurred_at"]), zone
    )

    def on(value: dict[str, Any], first: datetime.date, last: datetime.date) -> bool:
        moment = history_archive.site_date_of(
            datetime.datetime.fromisoformat(value["occurred_at"]), zone
        )
        return first <= moment <= last

    expect(
        {"date_from": day.isoformat(), "date_to": day.isoformat()},
        lambda value: on(value, day, day),
    )
    expect(
        {"date_from": day.isoformat()},
        lambda value: on(value, day, datetime.date(9999, 12, 31)),
    )
    # A Work Order Number: the flows released for it and the flows split from them.
    expect(
        {"work_order_number": world.work_order_number},
        lambda value: value["quantity_flow_id"] in world.work_order_flows,
    )
    assert len(world.work_order_flows) > 1
    expect({"work_order_number": "NO-SUCH-NUMBER"}, lambda value: False)
    # Filters combine.
    expect(
        {"part_number": pn, "area_id": area},
        lambda value: (
            value["part_number"] == pn and area in (value["from_area_id"], value["to_area_id"])
        ),
    )


def test_a_movement_reads_as_tracking_shows_it(world: World) -> None:
    view_id = world.views["main"]
    pn = world.data.part_numbers["split#0"]
    page = _search(world, view_id, part_number=pn, limit=200)
    by_type = {entry["movement_type"]: entry for entry in page["movements"]}
    received = by_type["RECEIVED"]
    assert received["demand"]["work_order_demand_id"] > 0
    assert received["to_area"] == {"id": world.builder.areas["MAT"], "name": "MAT"}
    assert received["operation"]["code"] == "OP-MAT"
    split = by_type["SPLIT"]
    assert split["lineage"] and all(edge["relation"] == "SPLIT" for edge in split["lineage"])
    # An undone original names its REVERSED row, and the REVERSED row the original.
    undone = _search(
        world, view_id, part_number=world.data.part_numbers["undone_scrap#0"], limit=200
    )
    reversals = [entry for entry in undone["movements"] if entry["movement_type"] == "REVERSED"]
    assert reversals
    originals = {entry["id"]: entry for entry in undone["movements"]}
    for reversal in reversals:
        assert (
            originals[reversal["reverses_movement_id"]]["reversed_by_movement_id"] == reversal["id"]
        )


def test_names_read_as_they_were_at_export(world: World) -> None:
    view_id = world.views["main"]
    area = world.builder.areas["MAT"]
    renamed = f"RENAMED-{uuid.uuid4().hex[:6]}"
    _ok(world.builder.admin.patch(f"/api/areas/{area}", json={"name": renamed}))
    names = _ok(world.builder.admin.get(f"{_VIEWS}/{view_id}/names"))
    assert {"id": area, "name": "MAT"} in names["areas"]
    assert {entry["id"] for entry in names["operations"]} >= {world.builder.operations["MAT"]}
    page = _search(world, view_id, area_id=area, limit=1)
    entry = page["movements"][0]
    shown = entry["to_area"] if entry["to_area"]["id"] == area else entry["from_area"]
    assert shown == {"id": area, "name": "MAT"}
    _ok(world.builder.admin.patch(f"/api/areas/{area}", json={"name": "MAT"}))


def test_a_flow_shows_its_whole_history_and_lineage(world: World) -> None:
    view_id = world.views["main"]
    movements = _archive_movements(world)
    records = {
        int(value["flow"]["id"]): value
        for _line, value in archive_files.read_json_lines(world.folder / "flows.jsonl.gz")
    }
    parent = min(world.data.components["split#0"])
    flow = _ok(world.builder.admin.get(f"{_VIEWS}/{view_id}/flows/{parent}"))
    assert (flow["id"], flow["part_number"], flow["status"]) == (
        parent,
        records[parent]["flow"]["part_number"],
        records[parent]["flow"]["status"],
    )
    assert flow["record"] == records[parent]["record"]
    assert [entry["id"] for entry in flow["movements"]] == _newest_first(
        [value for value in movements if value["quantity_flow_id"] == parent]
    )
    assert flow["children"] and all(link["relation"] == "SPLIT" for link in flow["children"])
    child = flow["children"][0]["quantity_flow_id"]
    child_flow = _ok(world.builder.admin.get(f"{_VIEWS}/{view_id}/flows/{child}"))
    assert [link["quantity_flow_id"] for link in child_flow["parents"]] == [parent]
    # A planned flow's route snapshot, its steps named.
    planned = min(world.data.components["planned#0"])
    route = _ok(world.builder.admin.get(f"{_VIEWS}/{view_id}/flows/{planned}"))["route"]
    assert [step["area"]["name"] for step in route["steps"]] == ["MAT", "DEBURR", "STOCK"]
    # The numbered Work Order: on the released flow and on the flow split from it.
    for flow_id in world.work_order_flows:
        shown = _ok(world.builder.admin.get(f"{_VIEWS}/{view_id}/flows/{flow_id}"))
        assert [ref["work_order_number"] for ref in shown["work_orders"]] == [
            world.work_order_number
        ]
    assert _detail(world.builder.admin.get(f"{_VIEWS}/{view_id}/flows/2000000"), 404) == (
        f"Quantity Flow 2000000 is not in view {view_id}."
    )


def test_a_bad_search_is_refused_with_its_text(world: World) -> None:
    view_id = world.views["main"]
    path = f"{_VIEWS}/{view_id}/movements"
    admin = world.builder.admin
    assert _detail(admin.get(path, params={"part_number": "  "}), 422)
    assert (
        _detail(admin.get(path, params={"date_from": "2026-02-02", "date_to": "2026-02-01"}), 422)
        == "The first day of the search must not be after its last day."
    )
    assert _detail(admin.get(path, params={"work_order_number": " "}), 422) == (
        "A Work Order Number must not be blank or contain a NUL character."
    )
    assert _detail(admin.get(path, params={"area_id": 0}), 422) == (
        "The Area must be a whole number from 1 to 2147483647."
    )
    assert (
        _detail(admin.get(path, params={"before": 9_000_000_000}), 404)
        == f"Movement 9000000000 is not in view {view_id}."
    )
    assert admin.get(path, params={"limit": 0}).status_code == 422
    assert admin.get(path, params={"limit": 201}).status_code == 422
    assert (
        _detail(admin.get(f"{_VIEWS}/2000000/movements"), 404)
        == "There is no archive view 2000000."
    )


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


def test_close_viewing_removes_the_loaded_rows(world: World) -> None:
    view_id = world.views["main"]
    figures = reader_figures(world.data)
    closed = _ok(world.builder.admin.post(f"{_VIEWS}/{view_id}/close"))
    assert (closed["state"], closed["closed_by"]["user_id"]) == (
        "CLOSING",
        world.builder.admin.user_id,
    )
    # Closing again answers unchanged; reading is over.
    assert _ok(world.builder.admin.post(f"{_VIEWS}/{view_id}/close"))["state"] == "CLOSING"
    assert _detail(world.builder.admin.get(f"{_VIEWS}/{view_id}/movements"), 409) == (
        f"View {view_id} of run {world.run_id} is closed (CLOSING): the host job removes its"
        " loaded rows at its next start. Open the run for viewing again to read it."
    )
    settings = history_archive_job.settings(world.builder.engine, world.folders)
    assert [(entry["id"], entry["action"]) for entry in settings.details["pending_views"]] == [
        (view_id, "close_view")
    ]
    following = history_archive_job.next_run(world.job, world.folders)
    done(following, "close_view")
    assert following.view_id == view_id
    report = history_archive_job.close_view(world.job, world.folders, view_id)
    done(report, "closed")
    assert report.state == "CLOSED"
    assert sum(report.details["removed"].values()) > 0
    assert _rows(world, view_id) == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)
    row = _view_row(world, view_id)
    assert (row["state"], row["closed_by_user_id"]) == ("CLOSED", world.builder.admin.user_id)
    assert reader_figures(world.data) == figures
    assert _detail(world.builder.admin.get(f"{_VIEWS}/{view_id}/names"), 409) == (
        f"View {view_id} of run {world.run_id} is closed (CLOSED): its loaded rows are removed."
        " Open the run for viewing again to read it."
    )
    # A finished view never changes.
    assert _ok(world.builder.admin.post(f"{_VIEWS}/{view_id}/close"))["state"] == "CLOSED"
    assert _detail(world.builder.admin.post(f"{_VIEWS}/{view_id}/cancel"), 409) == (
        f"View {view_id} of run {world.run_id} is finished (CLOSED) and cannot be cancelled."
    )


def test_a_view_closes_when_its_days_run_out(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    view_id = _opened(world)
    opened = _view_row(world, view_id)["opened_at"]
    later = opened + datetime.timedelta(days=7, minutes=1)
    monkeypatch.setattr(history_archive_job, "_now", lambda: later)
    pending = history_archive_job.settings(world.job, world.folders).details["pending_views"]
    assert [(entry["id"], entry["action"]) for entry in pending] == [(view_id, "close_view")]
    assert (
        "view_not_closed"
        not in history_archive_job.status(world.job, world.folders).details["findings"]
    )
    # Past its days by more than an hour, still open: the job could not close it.
    monkeypatch.setattr(history_archive_job, "_now", lambda: later + datetime.timedelta(hours=2))
    unclosed = history_archive_job.status(world.job, world.folders)
    assert "view_not_closed" in unclosed.details["findings"]
    assert unclosed.details["views_not_closed"] == [view_id]
    done(history_archive_job.next_run(world.job, world.folders), "close_view")
    report = history_archive_job.close_view(world.job, world.folders, view_id)
    done(report, "closed")
    row = _view_row(world, view_id)
    assert (row["state"], row["closed_by_user_id"]) == ("CLOSED", None)
    assert _rows(world, view_id) == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)
    assert (
        "view_not_closed"
        not in history_archive_job.status(world.job, world.folders).details["findings"]
    )


def test_an_open_view_is_not_closed_before_its_days(world: World) -> None:
    view_id = _opened(world)
    report = history_archive_job.close_view(world.job, world.folders, view_id)
    assert (report.exit_code, report.error and report.error.code) == (1, "wrong_state")
    assert _view_row(world, view_id)["state"] == "OPEN"
    _end_views(world)


# ---------------------------------------------------------------------------
# The load's failures
# ---------------------------------------------------------------------------


def _damage(world: World, kind: str, saved: Path) -> None:
    folder = world.folder
    if kind == "tampered":
        shutil.copy2(folder / "movements.jsonl.gz", saved / "movements.jsonl.gz")
        content = bytearray((folder / "movements.jsonl.gz").read_bytes())
        content[-12] ^= 0xFF
        (folder / "movements.jsonl.gz").write_bytes(bytes(content))
    elif kind == "missing_file":
        shutil.move(str(folder / "lineage.jsonl.gz"), str(saved / "lineage.jsonl.gz"))
    else:
        shutil.move(str(folder), str(saved / folder.name))


def _repair(world: World, kind: str, saved: Path) -> None:
    folder = world.folder
    if kind == "tampered":
        shutil.copy2(saved / "movements.jsonl.gz", folder / "movements.jsonl.gz")
    elif kind == "missing_file":
        shutil.move(str(saved / "lineage.jsonl.gz"), str(folder / "lineage.jsonl.gz"))
    else:
        shutil.move(str(saved / folder.name), str(folder))


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("tampered", "movements.jsonl.gz of {name} does not match its SHA-256 in SHA256SUMS"),
        ("missing_file", "The archive folder {name} is missing lineage.jsonl.gz."),
        ("missing_folder", "The archive folder {name} of run {run} is missing."),
    ],
)
def test_a_damaged_archive_fails_the_load_and_loads_nothing(
    world: World, tmp_path: Path, kind: str, reason: str
) -> None:
    _end_views(world)
    view_id = _request(world)
    name = world.folder.name
    _damage(world, kind, tmp_path)
    try:
        report = _load(world, view_id)
    finally:
        _repair(world, kind, tmp_path)
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result, report.state) == (3, "failed", "FAILED"), document
    expected = reason.format(name=name, run=world.run_id)
    assert report.error is not None and expected in report.error.message
    assert report.error.message.endswith(
        "Nothing was loaded; restore the archive folder from its off-host copy, then open the"
        " run for viewing again."
    )
    row = _view_row(world, view_id)
    assert row["state"] == "FAILED" and expected in row["failure_reason"]
    # The report shows the view as it ended, not as it started loading.
    shown = cast(dict[str, Any], document["view"])
    assert (shown["state"], shown["failure_reason"]) == ("FAILED", row["failure_reason"])
    assert _rows(world, view_id) == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)


def test_a_stopped_load_is_failed_by_the_next_recover(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _end_views(world)
    view_id = _request(world)

    def stopped(*_arguments: Any) -> int:
        raise sa.exc.OperationalError("COPY", None, psycopg.errors.LockNotAvailable("lock"))

    monkeypatch.setattr(history_archive_job, "_load_movements", stopped)
    report = _load(world, view_id)
    assert (report.exit_code, report.result, report.state) == (2, "interrupted", "LOADING")
    assert report.error is not None and report.error.code == "view_interrupted"
    partial = _rows(world, view_id)
    assert partial["archive_view_names"] > 0 and partial["archive_view_movements"] == 0
    monkeypatch.undo()
    # The next start: the cheap check sees it, recover removes its rows and fails it.
    pending = history_archive_job.settings(world.job, world.folders).details["pending_views"]
    assert [(entry["id"], entry["action"]) for entry in pending] == [(view_id, "recover")]
    assert history_archive_job.next_run(world.job, world.folders).result != "load_view"
    recovered = history_archive_job.recover(world.job, world.folders)
    done(recovered, "recovered")
    assert recovered.details["failed_view_ids"] == [view_id]
    assert _rows(world, view_id) == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)
    row = _view_row(world, view_id)
    assert row["state"] == "FAILED"
    assert row["failure_reason"].startswith("The host job stopped while it loaded this view")


def test_an_error_after_the_view_opened_reports_it_open(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review m2: once the view is OPEN, a later error never says that it stays LOADING
    or that its rows were removed."""
    _end_views(world)
    view_id = _request(world)
    summary = history_archive_views.view_summary

    def failing_once_open(view: Any, settings: Any) -> dict[str, Any]:
        if view["state"] == "OPEN":
            raise sa.exc.OperationalError("SELECT", None, psycopg.errors.LockNotAvailable("lock"))
        return summary(view, settings)

    monkeypatch.setattr(history_archive_views, "view_summary", failing_once_open)
    report = _load(world, view_id)
    monkeypatch.undo()
    assert (report.exit_code, report.result, report.state) == (2, "could_not_run", "OPEN")
    assert report.error is not None and report.error.code == "outcome_unknown"
    assert report.error.message == (
        f"View {view_id} is OPEN now; the command stopped on an error (OperationalError). See"
        " the job log."
    )
    assert _view_row(world, view_id)["state"] == "OPEN"
    assert _rows(world, view_id)["archive_view_movements"] > 0
    _end_views(world)


def test_a_file_changed_during_the_load_fails_the_view(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review m3: every file is hashed once at the check, and a file written or replaced
    while the job loads it (another modification time here) fails the view."""
    _end_views(world)
    view_id = _request(world)
    readme = world.folder / "README.txt"
    before = readme.stat()
    load = history_archive_job._load_archive

    def touching(engine: Engine, view: int, folder: Path) -> Any:
        counts = load(engine, view, folder)
        os.utime(readme, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        return counts

    monkeypatch.setattr(history_archive_job, "_load_archive", touching)
    try:
        report = _load(world, view_id)
    finally:
        monkeypatch.undo()
        os.utime(readme, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert (report.exit_code, report.result, report.state) == (3, "failed", "FAILED")
    assert report.error is not None and report.error.message == (
        f"README.txt of {world.folder.name} changed while the job loaded it, so the rows it had"
        " loaded were removed. Restore the archive folder from its off-host copy, then open the"
        " run for viewing again."
    )
    assert _view_row(world, view_id)["state"] == "FAILED"
    assert _rows(world, view_id) == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)


# ---------------------------------------------------------------------------
# The setting of runs loaded at once, cancelling, the command line
# ---------------------------------------------------------------------------


def _second_viewable_run(world: World) -> int:
    """A run cancelled while it waited for its copy confirmation: verified, viewable."""
    pn = f"AD-SECOND-{uuid.uuid4().hex[:6].upper()}"
    flow = world.builder.release(pn, 3)
    world.builder.stock("MAT", flow, pn, 3)
    backdate(world.owner, world.builder.component(flow))
    run_id = request_run(world.builder, "A second archive to view")
    no_copy = JobFolders(world.folders.archive_dir, None)
    done(history_archive_job.export(world.job, no_copy, run_id), "exported")
    done(history_archive_job.verify(world.job, no_copy, run_id), "verified")
    done(history_archive_job.copy(world.job, no_copy, run_id), "waiting_for_copy")
    _ok(world.builder.admin.post(f"/api/history-archive/runs/{run_id}/cancel"))
    return run_id


def test_at_most_the_set_number_of_runs_is_loaded(world: World) -> None:
    admin = world.builder.admin
    first = _opened(world)
    second_run = _second_viewable_run(world)
    assert _detail(admin.post(_VIEWS, json={"run_id": world.run_id}), 409) == (
        f"Run {world.run_id} is already open for viewing, or waiting to be loaded (view {first},"
        " OPEN)."
    )
    assert _detail(admin.post(_VIEWS, json={"run_id": second_run}), 409) == (
        "At most 1 run may be loaded for viewing at once (History archival & purge), and 1 is"
        f" loaded or waiting: view {first} of run {world.run_id} (OPEN). Close a view, or wait"
        f" until it is closed, before opening run {second_run}."
    )
    _set(world, archive_view_max_loaded=2)
    second = _request(world, second_run)
    # The setting lowered meanwhile: the load fails with its reason, loading nothing.
    _set(world, archive_view_max_loaded=1)
    report = _load(world, second)
    assert (report.exit_code, report.result, report.state) == (3, "failed", "FAILED")
    assert report.error is not None and report.error.message == (
        f"Run {second_run} was not loaded for viewing: 1 loaded run is open already, and History"
        " archival & purge allows 1 at once now. Close a view, then open run"
        f" {second_run} for viewing again."
    )
    assert _rows(world, second) == dict.fromkeys(models.ARCHIVE_VIEWER_TABLES, 0)
    _end_views(world)


def test_cancelling_a_requested_view(world: World) -> None:
    admin = world.builder.admin
    _end_views(world)
    view_id = _request(world)
    assert _detail(admin.post(f"{_VIEWS}/{view_id}/close"), 409) == (
        f"View {view_id} of run {world.run_id} waits to be loaded (REQUESTED): cancel it instead"
        " of closing it."
    )
    assert _detail(admin.get(f"{_VIEWS}/{view_id}/names"), 409) == (
        f"View {view_id} of run {world.run_id} is not loaded yet (REQUESTED): the host job loads"
        " it within minutes of the request."
    )
    cancelled = _ok(admin.post(f"{_VIEWS}/{view_id}/cancel"))
    assert (cancelled["state"], cancelled["cancelled_by"]["user_id"]) == (
        "CANCELLED",
        admin.user_id,
    )
    assert _ok(admin.post(f"{_VIEWS}/{view_id}/cancel"))["state"] == "CANCELLED"
    assert (
        _detail(admin.post(f"{_VIEWS}/2000000/cancel"), 404) == "There is no archive view 2000000."
    )
    opened = _opened(world)
    assert _detail(admin.post(f"{_VIEWS}/{opened}/cancel"), 409) == (
        f"View {opened} of run {world.run_id} is open (OPEN): close it instead of cancelling it."
    )
    _end_views(world)


def _cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    world: World,
    *arguments: str,
) -> tuple[int, dict[str, Any]]:
    monkeypatch.setenv(history_archive_job.ARCHIVE_DIR_ENV, str(world.folders.archive_dir))
    monkeypatch.setattr(cli, "_history_archive_engine", lambda: job_engine(world.url))
    code = cli.main(["history-archive", *arguments])
    return code, cast(dict[str, Any], json.loads(capsys.readouterr().out))


def test_the_job_command_line(
    world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _end_views(world)
    view_id = _request(world)
    code, document = _cli(monkeypatch, capsys, world, "load-view", "--view", str(view_id))
    assert (code, document["command"], document["result"], document["view_id"]) == (
        0,
        "history-archive load-view",
        "loaded",
        view_id,
    )
    assert document["view"]["state"] == "OPEN" and document["loaded"]["movements"] > 0
    code, document = _cli(monkeypatch, capsys, world, "close-view", "--view", str(view_id))
    assert (code, document["result"], document["error"]["code"]) == (1, "refused", "wrong_state")
    _ok(world.builder.admin.post(f"{_VIEWS}/{view_id}/close"))
    code, document = _cli(monkeypatch, capsys, world, "close-view", "--view", str(view_id))
    assert (code, document["result"], document["state"]) == (0, "closed", "CLOSED")
    code, document = _cli(monkeypatch, capsys, world, "load-view", "--view", "2000000")
    assert (code, document["error"]["code"]) == (1, "unknown_view")
    with pytest.raises(SystemExit):
        cli.main(["history-archive", "load-view"])
