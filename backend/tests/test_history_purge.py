"""Tests of the purge of Movement history (Phase 16, history archival design B step 2).

The data set of ``tests/archive_dataset.py`` is built through the real API in
one module database and its old components are moved back in time; the job
commands run as the maintenance database role in the application-role test
mode (as the owner otherwise), the API and the readers as the application
role. In file order:

- the main safety net: a run purges every eligible component (one of them,
  undone between the export and the purge, is skipped) in several batches,
  and every figure every reader derives — through the readers and the API —
  and every reconcile check are the same before and after; the receipt, its
  copy, the archive's own ``sha256sum -c`` and the runs API;
- Tracking's archived history: one entry per archived flow, paged with the
  Movements, ``archived`` on the flow, route steps ``ARCHIVED``;
- the pre-purge backup's evidence, the requester re-check (at ``next`` and at
  ``purge``), a resumed purge after an interruption (cancel and a new request
  refused meanwhile, ``recover`` leaves it), a run with nothing left to purge
  after its verification, the copy that waits without re-hashing (B1 review
  minor 7) and a confirmed copy, the receipt copied again by ``recover``, a
  batch that fails (rolled back whole, the run FAILED with its receipt), and
  the CLI.
"""

import datetime
import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
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
from app.application import history_archive_job
from app.application.history_archive_job import JobFolders, PurgeBackup, PurgeComponent
from app.core.config import get_settings
from app.domain.enums import Permission
from app.infrastructure import archive_files
from app.main import create_app
from tests.archive_dataset import (
    ARCHIVED,
    RETAINED,
    Builder,
    Dataset,
    backdate,
    backup_of,
    build_dataset,
    copied_run,
    done,
    job_engine,
    movement_count,
    part_number,
    purge_run,
    reader_figures,
    reconcile_outcome,
    request_run,
    run_row,
)
from tests.auth_harness import client_as, station_device_client
from tests.conftest import owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"


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
    client: TestClient

    @property
    def builder(self) -> Builder:
        return self.data.builder


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    database = f"partflow_test_history_purge_{uuid.uuid4().hex[:8]}"
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
            data = build_dataset(builder, owner)
            # late_undo: a full Scrap long ago, undone only between the export and the purge.
            pn = part_number("LATE")
            flow = builder.release(pn, 2)
            builder.scrap("MAT", flow, pn, 2)
            data.commands["late_undo"] = ("MAT", pn, builder.last_event)
            data.part_numbers["late_undo"] = pn
            data.components["late_undo"] = builder.component(flow)
            backdate(owner, data.components["late_undo"])
            yield World(data, url, owner, client)
    finally:
        owner.dispose()
        os.environ[_DB_URL_ENV] = original
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def job(world: World) -> Iterator[Engine]:
    engine = job_engine(world.url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def folders(tmp_path_factory: pytest.TempPathFactory) -> JobFolders:
    return JobFolders(tmp_path_factory.mktemp("archive"), tmp_path_factory.mktemp("archive_copy"))


def _set_policy(world: World, **values: int) -> None:
    response = world.builder.admin.put("/api/policies/data-retention", json=values)
    assert response.status_code == 200, response.text


def _records(world: World, run_id: int) -> dict[int, dict[str, Any]]:
    with world.owner.connect() as connection:
        return {
            int(row["flow_id"]): dict(row)
            for row in connection.execute(
                sa.text("SELECT * FROM history_archived_flows WHERE run_id = :id"), {"id": run_id}
            ).mappings()
        }


def _sha256sum_check(folder: Path) -> str:
    completed = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"], cwd=folder, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def _receipt(folder: Path, name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((folder / f"{name}.purged.json").read_bytes()))


def _old_stocked(world: World, count: int, label: str = "EXTRA") -> dict[str, set[int]]:
    """``count`` new old components (released and stocked, backdated), by PN."""
    builder = world.builder
    found: dict[str, set[int]] = {}
    for _ in range(count):
        pn = part_number(label)
        flow = builder.release(pn, 3)
        builder.stock("MAT", flow, pn, 3)
        found[pn] = builder.component(flow)
    backdate(world.owner, set().union(*found.values()))
    return found


# ---------------------------------------------------------------------------
# The main safety net
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def first(world: World, job: Engine, folders: JobFolders) -> dict[str, Any]:
    """Run 1: copied, the late Scrap undone, then figures, purge (PN limit 1), figures."""
    data = world.data
    run_id = request_run(world.builder, "Purge the old history of the data set")
    copied_run(job, folders, run_id)
    area, pn, scrap_event = data.commands["late_undo"]
    world.builder.undo(area, pn, scrap_event)
    _set_policy(world, archive_purge_batch_part_numbers=1)
    before = reader_figures(data)
    checks_before = reconcile_outcome(world.builder.engine)
    archived_movements = movement_count(world.owner, data.flows(ARCHIVED))
    report = purge_run(job, world.owner, folders, run_id)
    _set_policy(world, archive_purge_batch_part_numbers=20)
    return {
        "run_id": run_id,
        "report": report,
        "before": before,
        "checks_before": checks_before,
        "after": reader_figures(data),
        "checks_after": reconcile_outcome(world.builder.engine),
        "archived_movements": archived_movements,
    }


def test_the_purge_leaves_every_figure_and_check_unchanged(
    world: World, first: dict[str, Any]
) -> None:
    report = first["report"]
    done(report, "completed_with_skips")
    assert first["after"] == first["before"]
    # Not a vacuous comparison: the figures of purged history are there.
    after = first["after"]
    data = world.data
    assert after["stocked"][data.part_numbers["stocked#0"]] == 5
    assert after["scrapped"][data.part_numbers["scrapped#0"]] == 4
    assert after["scrapped"][data.part_numbers["partial_scrap#0"]] == 3
    assert after["introduced"][data.part_numbers["added#0"]] == 10
    assert after["stock_position"][data.part_numbers["allocated#0"]] == (8, 3)
    assert dict(after["released"])[min(data.demand_ids)] >= 0
    assert after["usage"][data.template_ids[0]][0] == 2
    assert after["consumed"] and after["edges"] and after["positions"]
    assert len(after["api"]["tracking_details"]) == len(data.all_part_numbers)
    assert first["checks_after"].keys() == first["checks_before"].keys()
    for check_id, (status, findings) in first["checks_after"].items():
        assert (status, findings) == first["checks_before"][check_id], check_id
        assert status in ("pass", "not_applicable"), (check_id, findings)
    assert first["checks_after"]["g"] == ("pass", [])
    # Every archived Movement is gone; the retained ones and the skipped component's stay.
    assert movement_count(world.owner, data.flows(ARCHIVED)) == 0
    assert movement_count(world.owner, data.flows(RETAINED)) > 0
    assert movement_count(world.owner, data.components["late_undo"]) > 0
    run = run_row(world.owner, first["run_id"])
    assert (run["state"], run["result"]) == ("COMPLETED_WITH_SKIPS", "PURGED")
    assert run["purged_movements"] == first["archived_movements"]
    assert run["purged_flows"] == len(data.flows(ARCHIVED))
    assert run["skipped_components"] == 1
    assert run["skipped_flows"] == len(data.components["late_undo"])
    records = _records(world, first["run_id"])
    assert {flow for flow, record in records.items() if record["purged_at"]} == data.flows(ARCHIVED)
    assert {flow for flow, record in records.items() if record["skipped_at"]} == (
        data.components["late_undo"]
    )
    # PN limit 1: one batch per Part Number of the components it reached (the
    # skipped one included: its batch re-checked it under the lock).
    assert report.details["batches"] == len({record["part_number"] for record in records.values()})
    assert report.details["batches"] > 1
    assert report.details["purged"]["movements"] == first["archived_movements"]
    assert report.details["skipped"] == {
        "components": 1,
        "flows": len(data.components["late_undo"]),
    }


def test_the_receipt_lists_the_components_and_is_copied(
    world: World, folders: JobFolders, first: dict[str, Any]
) -> None:
    run = run_row(world.owner, first["run_id"])
    name = str(run["archive_name"])
    archive_dir = cast(Path, folders.archive_dir)
    copy_dir = cast(Path, folders.copy_dir)
    content = (archive_dir / f"{name}.purged.json").read_bytes()
    assert archive_files.sha256_of_bytes(content) == run["receipt_sha256"]
    assert (copy_dir / f"{name}.purged.json").read_bytes() == content
    assert first["report"].details["receipt"] == {
        "name": f"{name}.purged.json",
        "sha256": run["receipt_sha256"],
        "copied_to": str(copy_dir / f"{name}.purged.json"),
        "copy_problem": None,
    }
    receipt = _receipt(archive_dir, name)
    assert receipt["format"] == "partflow-history-archive-purge-receipt"
    assert receipt["archive_name"] == name
    assert receipt["manifest_sha256"] == run["manifest_sha256"]
    assert receipt["run"]["state"] == "COMPLETED_WITH_SKIPS"
    assert receipt["run"]["result"] == "PURGED"
    assert receipt["pre_purge_backup"]["name"] == run["pre_purge_backup_name"]
    data = world.data
    outcomes = {
        component["component_id"]: component["outcome"] for component in receipt["components"]
    }
    assert outcomes[min(data.components["late_undo"])] == "SKIPPED"
    assert set(outcomes.values()) == {"PURGED", "SKIPPED"}
    assert receipt["counts"]["purged"]["movements"] == first["archived_movements"]
    assert receipt["counts"]["retained"] == {"components": 0, "flows": 0, "movements": 0}
    flows = {flow for component in receipt["components"] for flow in component["flow_ids"]}
    assert flows == data.flows(ARCHIVED) | data.components["late_undo"]
    # The archive itself never changed: it still checks with standard tools alone.
    assert "movements.jsonl.gz: OK" in _sha256sum_check(archive_dir / name)
    assert "movements.jsonl.gz: OK" in _sha256sum_check(copy_dir / name)


def test_the_runs_api_shows_the_purge(world: World, first: dict[str, Any]) -> None:
    response = world.builder.admin.get("/api/history-archive/runs")
    assert response.status_code == 200, response.text
    [body] = [run for run in response.json() if run["id"] == first["run_id"]]
    stored = run_row(world.owner, first["run_id"])
    assert (body["state"], body["result"]) == ("COMPLETED_WITH_SKIPS", "PURGED")
    assert body["pre_purge_backup"]["name"] == stored["pre_purge_backup_name"]
    assert body["purged"] == {
        "components": stored["purged_components"],
        "flows": stored["purged_flows"],
        "movements": stored["purged_movements"],
    }
    assert body["receipt_name"] == f"{stored['archive_name']}.purged.json"
    assert body["receipt_sha256"] == stored["receipt_sha256"]
    assert body["skipped"]["components"] == 1
    assert body["copied_at"] is not None and body["purge_started_at"] is not None


# ---------------------------------------------------------------------------
# Tracking's archived history
# ---------------------------------------------------------------------------


def _all_history(world: World, pn: str, **params: Any) -> list[tuple[str, int]]:
    """Every entry of the PN's history, page by page (limit 1): ("M", id) or ("A", flow)."""
    entries: list[tuple[str, int]] = []
    before: int | None = None
    while True:
        query: dict[str, Any] = {"part_number": pn, "limit": 1, **params}
        if before is not None:
            query["before"] = before
        response = world.builder.admin.get("/api/tracking/movements", params=query)
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["movements"]) + len(page["archived"]) == 1 or not page["has_more"]
        entries += [("M", int(movement["id"])) for movement in page["movements"]]
        entries += [("A", int(entry["quantity_flow_id"])) for entry in page["archived"]]
        if not page["has_more"]:
            return entries
        before = int(page["next_before_movement_id"])


def test_tracking_shows_one_archived_entry_per_flow(world: World, first: dict[str, Any]) -> None:
    data = world.data
    # A PN with archived and retained flows: the split's PN (beside is still active).
    pn = data.part_numbers["split#0"]
    detail = world.builder.admin.get("/api/tracking/detail", params={"part_number": pn})
    assert detail.status_code == 200, detail.text
    body = detail.json()
    archived_flows = data.components["split#0"]
    flows = {flow["id"]: flow for flow in body["flows"]["flows"]}
    for flow_id in archived_flows:
        entry = flows[flow_id]["archived"]
        assert entry["run_id"] == first["run_id"]
        assert entry["movement_count"] >= 1 and entry["quantity_flow_id"] == flow_id
        assert flows[flow_id]["trace"] == [] and flows[flow_id]["deviations"] == []
    assert flows[min(data.components["beside#0"])]["archived"] is None
    page = body["movements"]
    assert page["archived_total"] == len(archived_flows)
    assert page["total"] == world.builder.scalar(
        "SELECT count(*) FROM part_movements WHERE part_number = :pn", pn=pn
    )
    # Paging one entry at a time reaches every Movement and every archived flow once, in order.
    entries = _all_history(world, pn)
    assert sorted(flow for kind, flow in entries if kind == "A") == sorted(archived_flows)
    movement_ids = [ident for kind, ident in entries if kind == "M"]
    assert len(movement_ids) == len(set(movement_ids)) == page["total"]
    # The retained Movements are newer than the archived entries here.
    assert entries[-1][0] == "A"


def test_an_archived_planned_flow_shows_its_route_steps_archived(
    world: World, first: dict[str, Any]
) -> None:
    data = world.data
    pn = data.part_numbers["planned#0"]
    detail = world.builder.admin.get("/api/tracking/detail", params={"part_number": pn})
    assert detail.status_code == 200, detail.text
    flows = {flow["id"]: flow for flow in detail.json()["flows"]["flows"]}
    archived = flows[min(data.components["planned#0"])]
    assert [step["state"] for step in archived["route_steps"]] == ["ARCHIVED"] * 3
    assert archived["off_route"] is False
    active = flows[min(data.components["planned_active#0"])]
    assert [step["state"] for step in active["route_steps"]] == ["DONE", "CURRENT", "FUTURE"]


def test_the_scrap_history_shows_an_archived_scrap(world: World, first: dict[str, Any]) -> None:
    pn = world.data.part_numbers["scrapped#0"]
    entries = _all_history(world, pn, movement_type="SCRAPPED")
    assert entries == [("A", min(world.data.components["scrapped#0"]))]
    # An archived flow without a scrap is not in the Scrap history.
    other = world.data.part_numbers["stocked#0"]
    assert _all_history(world, other, movement_type="SCRAPPED") == []


# ---------------------------------------------------------------------------
# The pre-purge backup and the requester
# ---------------------------------------------------------------------------


def _purge_refusal(
    job: Engine, folders: JobFolders, run_id: int, backup: PurgeBackup | None
) -> str:
    report = history_archive_job.purge(job, folders, run_id, backup)
    assert report.exit_code == history_archive_job.EXIT_REFUSED, report.details
    assert report.error is not None
    return report.error.code


def test_the_pre_purge_backup_is_checked(world: World, job: Engine, folders: JobFolders) -> None:
    _old_stocked(world, 2)
    run_id = request_run(world.builder, "Second purge")
    copied_run(job, folders, run_id)
    valid = backup_of(world.owner, run_id)
    stamp, _ = valid.name.split("-pre-purge-")
    started = datetime.datetime.strptime(valid.started_at, "%Y-%m-%dT%H:%M:%SZ")
    early = (started - datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    later = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert _purge_refusal(job, folders, run_id, None) == "backup_required"
    for backup in (
        PurgeBackup(f"{stamp}-pre-purge-run-999-20200101T000000Z", valid.started_at),
        PurgeBackup(f"{stamp}-pre-release-v1.0.0", valid.started_at),
        PurgeBackup(f"{stamp}-manual", valid.started_at),
        PurgeBackup("not a backup", valid.started_at),
        PurgeBackup(valid.name, early),
        PurgeBackup(valid.name, later),
        PurgeBackup(valid.name, "yesterday"),
    ):
        assert _purge_refusal(job, folders, run_id, backup) == "backup_invalid", backup
    run = run_row(world.owner, run_id)
    assert (run["state"], run["pre_purge_backup_name"]) == ("RUNNING", None)
    done(history_archive_job.next_run(job, folders), "backup")
    done(history_archive_job.purge(job, folders, run_id, valid), "completed")
    run = run_row(world.owner, run_id)
    assert (run["pre_purge_backup_name"], run["result"]) == (valid.name, "PURGED")
    # A finished run takes no purge any more.
    assert _purge_refusal(job, folders, run_id, None) == "wrong_state"


def test_next_fails_the_run_of_a_requester_who_lost_the_key(
    world: World, job: Engine, folders: JobFolders
) -> None:
    _old_stocked(world, 1)
    requester = client_as(
        world.client, Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE, Permission.VIEW_PRODUCTION_DATA
    )
    response = requester.post("/api/history-archive/runs", json={"reason": "Their run"})
    assert response.status_code == 201, response.text
    run_id = int(response.json()["id"])
    copied_run(job, folders, run_id)
    assert requester.identity is not None
    with world.owner.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :role AND permission = :key"),
            {"role": requester.identity.role_id, "key": Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE},
        )
    report = history_archive_job.next_run(job, folders)
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_RUN_FAILED, "failed")
    run = run_row(world.owner, run_id)
    assert run["state"] == "FAILED" and run["pre_purge_backup_name"] is None
    assert "no longer holds Run history archival and purge" in str(run["failure_reason"])
    assert report.error is not None and report.error.message == run["failure_reason"]


def test_purge_fails_the_run_of_a_requester_no_longer_active(
    world: World, job: Engine, folders: JobFolders
) -> None:
    requester = client_as(
        world.client, Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE, Permission.VIEW_PRODUCTION_DATA
    )
    response = requester.post("/api/history-archive/runs", json={"reason": "Inactive soon"})
    assert response.status_code == 201, response.text
    run_id = int(response.json()["id"])
    copied_run(job, folders, run_id)
    done(history_archive_job.next_run(job, folders), "backup")
    backup = backup_of(world.owner, run_id)
    with world.owner.begin() as connection:
        connection.execute(
            sa.text("UPDATE users SET is_active = false WHERE id = :id"),
            {"id": requester.user_id},
        )
    flows = set(_records(world, run_id))
    report = history_archive_job.purge(job, folders, run_id, backup)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    run = run_row(world.owner, run_id)
    assert run["state"] == "FAILED" and run["purge_started_at"] is None
    assert "is no longer active" in str(run["failure_reason"])
    assert movement_count(world.owner, flows) == sum(
        record["movement_count"] for record in _records(world, run_id).values()
    )


# ---------------------------------------------------------------------------
# An interrupted purge resumes; nothing interrupts it from outside
# ---------------------------------------------------------------------------


def test_an_interrupted_purge_is_resumed_and_never_cancelled(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Left over from the requester tests: their components, and three new ones.
    _old_stocked(world, 3)
    _set_policy(world, archive_purge_batch_part_numbers=1)
    run_id = request_run(world.builder, "Interrupted purge")
    copied_run(job, folders, run_id)
    real = history_archive_job._purge_batch
    calls: list[int] = []

    def interrupted(engine: Engine, run: int, batch: list[PurgeComponent]) -> Any:
        if calls:
            raise KeyboardInterrupt  # the process is killed after one whole batch
        calls.append(run)
        return real(engine, run, batch)

    monkeypatch.setattr(history_archive_job, "_purge_batch", interrupted)
    with pytest.raises(KeyboardInterrupt):
        purge_run(job, world.owner, folders, run_id)
    monkeypatch.setattr(history_archive_job, "_purge_batch", real)
    run = run_row(world.owner, run_id)
    assert run["state"] == "PURGING" and run["purged_components"] == 1
    # Whole components only: the purge checks pass between two batches.
    assert reconcile_outcome(world.builder.engine)["g"] == ("pass", [])
    # Nobody stops it from outside, and no other run starts.
    cancel = world.builder.admin.post(f"/api/history-archive/runs/{run_id}/cancel")
    assert cancel.status_code == 409, cancel.text
    assert "running on the server now" in cancel.json()["detail"]
    other = world.builder.admin.post("/api/history-archive/runs", json={"reason": "another"})
    assert other.status_code == 409, other.text
    assert f"Run {run_id} is still open (PURGING)" in other.json()["detail"]
    recovered = history_archive_job.recover(job, folders)
    assert run_id not in recovered.details["failed_run_ids"]
    nxt = history_archive_job.next_run(job, folders)
    done(nxt, "purge")
    assert nxt.run_id == run_id
    # A resumed purge needs no backup, and refuses another one.
    other_backup = PurgeBackup(
        f"20300101T000000Z-pre-purge-{run['archive_name']}", "2030-01-01T00:00:00Z"
    )
    assert _purge_refusal(job, folders, run_id, other_backup) == "backup_mismatch"
    report = history_archive_job.purge(job, folders, run_id, None)
    done(report, "completed")
    assert report.details["resumed"] is True
    _set_policy(world, archive_purge_batch_part_numbers=20)
    run = run_row(world.owner, run_id)
    records = _records(world, run_id)
    assert run["purged_flows"] == len(records) == sum(1 for r in records.values() if r["purged_at"])
    assert run["result"] == "PURGED"
    assert reconcile_outcome(world.builder.engine)["g"] == ("pass", [])


# ---------------------------------------------------------------------------
# Nothing left to purge; the copy that waits; a confirmed copy
# ---------------------------------------------------------------------------


def test_a_run_whose_every_component_changed_finishes_at_its_verification(
    world: World, job: Engine, folders: JobFolders
) -> None:
    builder = world.builder
    pn = part_number("ALLCHANGED")
    flow = builder.release(pn, 2)
    builder.scrap("MAT", flow, pn, 2)
    scrap_event = builder.last_event
    backdate(world.owner, builder.component(flow))
    run_id = request_run(builder, "Everything changes")
    done(history_archive_job.export(job, folders, run_id), "exported")
    builder.undo("MAT", pn, scrap_event)
    report = history_archive_job.verify(job, folders, run_id)
    done(report, "nothing_to_purge")
    run = run_row(world.owner, run_id)
    assert (run["state"], run["result"]) == ("COMPLETED_WITH_SKIPS", "NOTHING_PURGED")
    assert run["copied_at"] is None and run["pre_purge_backup_name"] is None
    assert run["receipt_sha256"] == report.details["receipt"]["sha256"]
    receipt = _receipt(cast(Path, folders.archive_dir), str(run["archive_name"]))
    assert {component["outcome"] for component in receipt["components"]} == {"SKIPPED"}
    assert receipt["pre_purge_backup"] is None
    done(history_archive_job.next_run(job, folders), "none")


def test_the_copy_waits_without_rehashing_until_it_is_confirmed(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _old_stocked(world, 1)
    unplugged = JobFolders(folders.archive_dir, tmp_path / "unplugged")
    run_id = request_run(world.builder, "No copy folder today")
    done(history_archive_job.export(job, unplugged, run_id), "exported")
    done(history_archive_job.verify(job, unplugged, run_id), "verified")
    hashed: list[str] = []
    real = history_archive_job._check_published

    def counting(source: Path, run: Any) -> None:
        hashed.append(source.name)
        real(source, run)

    monkeypatch.setattr(history_archive_job, "_check_published", counting)
    report = history_archive_job.copy(job, unplugged, run_id)
    done(report, "waiting_for_copy")
    assert "does not exist" in str(report.details["copy_problem"])
    done(history_archive_job.next_run(job, unplugged), "wait")
    done(history_archive_job.copy(job, unplugged, run_id), "waiting_for_copy")
    assert hashed == []
    run = run_row(world.owner, run_id)
    response = world.builder.admin.post(
        f"/api/history-archive/runs/{run_id}/copy-confirmation",
        json={"manifest_sha256": run["manifest_sha256"]},
    )
    assert response.status_code == 200, response.text
    done(history_archive_job.next_run(job, unplugged), "copy")
    report = history_archive_job.copy(job, unplugged, run_id)
    done(report, "confirmed")
    assert hashed == [run["archive_name"]]
    assert run_row(world.owner, run_id)["state"] == "RUNNING"
    done(history_archive_job.next_run(job, unplugged), "backup")
    report = purge_run(job, world.owner, unplugged, run_id)
    done(report, "completed")
    # A confirmed copy: the archive folder's own off-host copy carries the receipt.
    assert report.details["receipt"]["copied_to"] is None
    assert report.details["receipt"]["copy_problem"] is None


def test_recover_copies_a_missing_receipt(
    world: World, job: Engine, folders: JobFolders, first: dict[str, Any]
) -> None:
    name = str(run_row(world.owner, first["run_id"])["archive_name"])
    copy = cast(Path, folders.copy_dir) / f"{name}.purged.json"
    copy.unlink()
    status = history_archive_job.status(job, folders)
    assert "receipt_not_copied" in status.details["findings"]
    assert status.details["receipts_not_copied"] == [name]
    report = history_archive_job.recover(job, folders)
    done(report, "recovered")
    assert report.details["copied_receipts"] == [str(copy)]
    assert (
        copy.read_bytes() == (cast(Path, folders.archive_dir) / f"{name}.purged.json").read_bytes()
    )
    assert "receipt_not_copied" not in history_archive_job.status(job, folders).details["findings"]


# ---------------------------------------------------------------------------
# A batch that fails is rolled back whole; the run fails with its receipt
# ---------------------------------------------------------------------------


def test_a_failing_batch_rolls_back_whole_and_fails_the_run(
    world: World, job: Engine, folders: JobFolders
) -> None:
    extra = _old_stocked(world, 3, "FAILING")
    _set_policy(world, archive_purge_batch_part_numbers=1)
    run_id = request_run(world.builder, "A purge that fails")
    copied_run(job, folders, run_id)
    failing_pn = sorted(extra)[1]
    failing = extra[failing_pn]
    with world.owner.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE FUNCTION test_fail_purge() RETURNS trigger LANGUAGE plpgsql AS $$"
                " BEGIN RAISE EXCEPTION 'injected failure'; END; $$"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TRIGGER test_fail_purge BEFORE UPDATE ON history_archived_flows"
                f" FOR EACH ROW WHEN (NEW.purged_at IS NOT NULL AND NEW.flow_id = {min(failing)})"
                " EXECUTE FUNCTION test_fail_purge()"
            )
        )
    try:
        report = purge_run(job, world.owner, folders, run_id)
    finally:
        with world.owner.begin() as connection:
            connection.execute(sa.text("DROP TRIGGER test_fail_purge ON history_archived_flows"))
            connection.execute(sa.text("DROP FUNCTION test_fail_purge()"))
        _set_policy(world, archive_purge_batch_part_numbers=20)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    run = run_row(world.owner, run_id)
    assert run["state"] == "FAILED"
    assert "Whole components only were deleted" in str(run["failure_reason"])
    # The failing component's batch was rolled back whole: its Movements stay.
    assert movement_count(world.owner, failing) > 0
    records = _records(world, run_id)
    assert all(records[flow]["purged_at"] is None for flow in failing)
    assert all(records[flow]["purged_at"] is not None for flow in extra[sorted(extra)[0]])
    receipt = _receipt(cast(Path, folders.archive_dir), str(run["archive_name"]))
    assert run["receipt_sha256"] is not None
    assert receipt["run"]["state"] == "FAILED"
    outcomes = {
        component["component_id"]: component["outcome"] for component in receipt["components"]
    }
    assert outcomes[min(failing)] == "RETAINED"
    assert "PURGED" in outcomes.values()
    # The report says what was deleted before the run failed (the run's totals).
    assert report.details["purged"] == {
        "components": run["purged_components"],
        "flows": run["purged_flows"],
        "movements": run["purged_movements"],
    }
    assert report.details["purged"]["components"] >= 1
    assert report.details["skipped"] == {
        "components": run["skipped_components"],
        "flows": run["skipped_flows"],
    }
    # The rest stays retained and is archived again by the next run.
    assert reconcile_outcome(world.builder.engine)["g"] == ("pass", [])
    run_id = request_run(world.builder, "The rest after a failure")
    copied_run(job, folders, run_id)
    done(purge_run(job, world.owner, folders, run_id), "completed")
    assert movement_count(world.owner, failing) == 0


# ---------------------------------------------------------------------------
# A database error that does not last, a lost lock connection, large batches
# ---------------------------------------------------------------------------


def _error(kind: type[sa.exc.DBAPIError], original: Exception) -> sa.exc.DBAPIError:
    return kind("SELECT 1", {}, original)


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (_error(sa.exc.OperationalError, psycopg.errors.DeadlockDetected("deadlock")), True),
        (_error(sa.exc.OperationalError, psycopg.errors.SerializationFailure("x")), True),
        (
            _error(
                sa.exc.OperationalError,
                psycopg.errors.LockNotAvailable("canceling statement due to lock timeout"),
            ),
            True,
        ),
        (_error(sa.exc.OperationalError, psycopg.errors.AdminShutdown("terminating")), True),
        (_error(sa.exc.OperationalError, psycopg.errors.CrashShutdown("terminating")), True),
        (_error(sa.exc.OperationalError, psycopg.errors.CannotConnectNow("starting")), True),
        (_error(sa.exc.OperationalError, psycopg.OperationalError("connection lost")), True),
        (
            _error(
                sa.exc.OperationalError,
                psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
            ),
            True,
        ),
        (
            _error(
                sa.exc.OperationalError,
                psycopg.errors.QueryCanceled("canceling statement due to user request"),
            ),
            False,
        ),
        (_error(sa.exc.InternalError, psycopg.errors.RaiseException("guard refusal")), False),
        (_error(sa.exc.OperationalError, psycopg.errors.DiskFull("no space left")), False),
        (_error(sa.exc.IntegrityError, psycopg.errors.CheckViolation("check")), False),
        (RuntimeError("not a database error"), False),
    ],
)
def test_which_errors_leave_a_purge_resumable(error: Exception, transient: bool) -> None:
    assert history_archive_job.is_transient(error) is transient


def test_a_transient_database_error_leaves_the_purge_resumable(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 3 (design §7.5): a deadlock in the second batch interrupts the purge."""
    _old_stocked(world, 2, "DEADLOCK")
    _set_policy(world, archive_purge_batch_part_numbers=1)
    run_id = request_run(world.builder, "A purge that meets a deadlock")
    copied_run(job, folders, run_id)
    real = history_archive_job._purge_batch
    calls: list[int] = []

    def deadlocked(engine: Engine, run: int, batch: list[PurgeComponent]) -> Any:
        if calls:
            raise _error(sa.exc.OperationalError, psycopg.errors.DeadlockDetected("deadlock"))
        calls.append(run)
        return real(engine, run, batch)

    monkeypatch.setattr(history_archive_job, "_purge_batch", deadlocked)
    try:
        report = purge_run(job, world.owner, folders, run_id)
    finally:
        monkeypatch.setattr(history_archive_job, "_purge_batch", real)
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result, report.state) == (
        history_archive_job.EXIT_COULD_NOT_RUN,
        "interrupted",
        "PURGING",
    ), document
    assert report.error is not None and report.error.code == "purge_interrupted"
    assert "resumes the purge" in report.error.message
    run = run_row(world.owner, run_id)
    assert (run["state"], run["failure_reason"], run["receipt_sha256"]) == ("PURGING", None, None)
    # The report says what the committed batch deleted.
    assert run["purged_components"] == 1
    assert document["purged"] == {
        "components": run["purged_components"],
        "flows": run["purged_flows"],
        "movements": run["purged_movements"],
    }
    # Whole components only, and the next start resumes it.
    assert reconcile_outcome(world.builder.engine)["g"] == ("pass", [])
    done(history_archive_job.next_run(job, folders), "purge")
    report = history_archive_job.purge(job, folders, run_id, None)
    done(report, "completed")
    assert report.details["resumed"] is True
    _set_policy(world, archive_purge_batch_part_numbers=20)
    assert run_row(world.owner, run_id)["result"] == "PURGED"


def test_a_lasting_error_while_purging_still_fails_the_run(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A statement cancelled by a person is no transient error: the run fails, with its receipt."""
    _old_stocked(world, 1, "CANCELLED")
    run_id = request_run(world.builder, "A purge someone cancels")
    copied_run(job, folders, run_id)

    def cancelled(engine: Engine, run: int, batch: list[PurgeComponent]) -> Any:
        raise _error(
            sa.exc.OperationalError,
            psycopg.errors.QueryCanceled("canceling statement due to user request"),
        )

    monkeypatch.setattr(history_archive_job, "_purge_batch", cancelled)
    report = purge_run(job, world.owner, folders, run_id)
    assert (report.exit_code, report.result, report.state) == (
        history_archive_job.EXIT_RUN_FAILED,
        "failed",
        "FAILED",
    )
    run = run_row(world.owner, run_id)
    assert run["state"] == "FAILED" and run["receipt_sha256"] is not None
    assert report.details["purged"] == {"components": 0, "flows": 0, "movements": 0}


def test_a_lost_lock_connection_does_not_fail_a_completed_purge(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 2: the job's lock session dies during the purge; the purge completes."""
    _old_stocked(world, 1, "LOCKLOST")
    run_id = request_run(world.builder, "A purge that loses its lock connection")
    copied_run(job, folders, run_id)
    real = history_archive_job._purge_batch
    killed: list[int] = []

    def lock_lost(engine: Engine, run: int, batch: list[PurgeComponent]) -> Any:
        if not killed:
            with world.owner.connect() as connection:
                killed.append(
                    int(
                        connection.execute(
                            sa.text(
                                "SELECT count(pg_terminate_backend(pid)) FROM pg_locks"
                                " WHERE locktype = 'advisory' AND granted"
                                " AND database = (SELECT oid FROM pg_database"
                                " WHERE datname = current_database())"
                                " AND pid <> pg_backend_pid()"
                            )
                        ).scalar_one()
                    )
                )
        return real(engine, run, batch)

    monkeypatch.setattr(history_archive_job, "_purge_batch", lock_lost)
    try:
        report = purge_run(job, world.owner, folders, run_id)
    finally:
        monkeypatch.setattr(history_archive_job, "_purge_batch", real)
    assert killed == [1]
    done(report, "completed")
    assert report.state == "COMPLETED" and report.error is None
    assert report.details["receipt"]["sha256"] == run_row(world.owner, run_id)["receipt_sha256"]
    # The lock ended with its session: the next command takes it again.
    done(history_archive_job.next_run(job, folders), "none")


def test_an_error_after_the_run_ended_is_not_its_failure(
    world: World, job: Engine, first: dict[str, Any]
) -> None:
    """Never ``run_failed`` for a run that ended on its own: the error is logged instead."""

    def completed_then_broken(engine: Engine, report: history_archive_job.JobReport) -> None:
        report.run_started = True
        report.state = "PURGING"
        report.result, report.exit_code = "completed_with_skips", history_archive_job.EXIT_DONE
        raise RuntimeError("the lock connection was lost")

    report = history_archive_job._command(
        "purge", job, completed_then_broken, run_id=first["run_id"]
    )
    assert (report.exit_code, report.result, report.state, report.error) == (
        history_archive_job.EXIT_DONE,
        "completed_with_skips",
        "COMPLETED_WITH_SKIPS",
        None,
    )

    def broken(engine: Engine, report: history_archive_job.JobReport) -> None:
        report.run_started = True
        raise RuntimeError("broken before any result")

    report = history_archive_job._command("purge", job, broken, run_id=first["run_id"])
    assert (report.exit_code, report.state) == (
        history_archive_job.EXIT_COULD_NOT_RUN,
        "COMPLETED_WITH_SKIPS",
    )
    assert report.error is not None and report.error.code == "outcome_unknown"


def test_a_batch_names_its_flows_in_one_array(world: World, job: Engine) -> None:
    """Review finding 1: a batch may hold more flows than a statement takes parameters."""
    ids = list(range(2_000_000_000, 2_000_070_000))
    now = datetime.datetime.now(datetime.UTC)
    with job.connect() as connection, connection.begin() as transaction:
        deleted = connection.execute(
            sa.text(history_archive_job._DELETE_MOVEMENTS_SQL), {"ids": ids}
        ).rowcount
        marked = connection.execute(
            sa.text(history_archive_job._MARK_RECORDS_SQL.format(column="skipped_at")),
            {"moment": now, "run_id": 0, "ids": ids},
        ).rowcount
        transaction.rollback()
    assert (deleted, marked) == (0, 0)
    # The batch itself: 70,000 flows no record names are all re-checked and skipped
    # in one statement each; marking none of them skipped stops the batch.
    batch = [
        PurgeComponent(
            flow_id,
            [history_archive_job._PurgeRecord(flow_id, "PN-NONE", "STOCKED", 1, flow_id)],
        )
        for flow_id in ids
    ]
    with pytest.raises(
        history_archive_job._PurgeMismatchError, match="marked 0 records skipped, 70000 expected"
    ):
        history_archive_job._purge_batch(job, 0, batch)


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------


def test_the_cli_purges_with_the_backup_evidence(
    world: World,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _old_stocked(world, 1, "CLI")
    run_id = request_run(world.builder, "CLI purge")
    engine = job_engine(world.url)
    copied_run(engine, folders, run_id)
    for name, value in (
        (history_archive_job.ARCHIVE_DIR_ENV, folders.archive_dir),
        (history_archive_job.COPY_DIR_ENV, folders.copy_dir),
    ):
        monkeypatch.setenv(name, str(value))
    with pytest.raises(SystemExit) as usage:
        cli.main(["history-archive", "purge", "--run", str(run_id), "--backup", "x"])
    assert usage.value.code == 2
    capsys.readouterr()
    backup = backup_of(world.owner, run_id)
    monkeypatch.setattr(cli, "_history_archive_engine", lambda: job_engine(world.url))
    code = cli.main(
        [
            "history-archive",
            "purge",
            "--run",
            str(run_id),
            "--backup",
            backup.name,
            "--backup-started-at",
            backup.started_at,
        ]
    )
    document = json.loads(capsys.readouterr().out)
    assert (code, document["result"], document["state"]) == (0, "completed", "COMPLETED")
    assert document["command"] == "history-archive purge"
    assert document["backup"] == {
        "name": backup.name,
        "dump_started_at": backup.started_at.replace("Z", ".000000Z"),
    }
    assert set(document["receipt"]) == {"name", "sha256", "copied_to", "copy_problem"}
    engine.dispose()
