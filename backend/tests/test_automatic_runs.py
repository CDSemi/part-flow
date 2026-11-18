"""Tests of automatic history archival runs (Phase 16, history archival design B step 4).

One module database is built through the real API — old components released
and stocked, moved back in time by the owner's setup transaction, as
``tests/archive_dataset.py`` does —; the host job's commands run as the
maintenance database role in the application-role test mode (as the owner
otherwise), the API as the application role. In file order:

- the settings of automatic runs: their ranges, and the approval step,
  switched off only with a reason and the typed confirmation (the audit row
  is the standing authorization and the setting keeps its id), renewed by a
  confirmation, cleared when switched on;
- ``history-archive evaluate``, one condition at a time: off, no retention
  period, a run open (PROPOSED included), each measure at, below and above
  its limit, nothing eligible, the interval since the last automatic run, a
  standing authorization no longer valid, a time limit, and the races of
  its insert (any setting the decision used changed meanwhile, also after
  the job's own check, when the run guard refuses the insert: both answer
  ``settings_changed``); a created run (PROPOSED with its preview, reason and CREATED
  audit row; APPROVED under the standing authorization, exported with its
  authority) and the CLI;
- approve (with a reason of the approver's own) and dismiss (like Cancel), and
  their races with Cancel, with a second approval and with each other;
- the re-check, before the pre-purge backup and before the purge, of the
  approver and of the standing authorization's User (no longer holding the
  key, or no longer active), and automatic runs waiting until a key holder
  saves the approval setting again;
- a run created under the standing authorization fails when the approval
  step was switched on again since (also when switched off again), and goes
  on after a renewal with the User of its own authorization re-checked;
- a standing authorization under which a run failed stays invalid, even
  once its User may run archival again, until the approval setting is saved
  again (a renewal or switching the step on); without such a run its
  validity follows its User;
- a run created under the standing authorization fails as a plain failure,
  its authorization still valid, once its cutoff is newer than the current
  retention period allows (or no period is set) or automatic runs are off
  (also switched on again); a manual run and an approved proposal keep the
  cutoff a person chose;
- the reminders of ``status``: a PROPOSED run waiting longer than the
  setting, a run failed because its authority was no longer valid, a standing
  authorization that is no longer valid.

Nothing is purged here: the purge itself is ``tests/test_history_purge.py``.
"""

import datetime
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.application import history_archive, history_archive_job
from app.application.errors import ConflictError
from app.application.history_archive_job import JobFolders, JobReport, PurgeBackup
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.archive_dataset import (
    Builder,
    backdate,
    backup_of,
    copied_run,
    done,
    job_engine,
    part_number,
    request_run,
    run_row,
)
from tests.auth_harness import IdentityClient, client_as, station_device_client
from tests.conftest import owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"
_SETTINGS = "/api/policies/data-retention"
_RUNS = "/api/history-archive/runs"
_RHAP = Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE
_CONFIRM = "RUN WITHOUT APPROVAL"
#: The settings every case starts from (the defaults; the approval step on).
_DEFAULT_POLICY: dict[str, object] = {
    "retention_period_months": 24,
    "archive_max_wait_days": 7,
    "archive_preview_seconds": 90,
    "archive_automatic_runs": False,
    "archive_measure": "LIVE_MOVEMENT_ROWS",
    "archive_limit_movement_rows": 5_000_000,
    "archive_limit_history_months": 1,
    "archive_limit_database_gib": 20,
    "archive_approval_required": True,
    "archive_interval_days": 7,
    "archive_authorization_id": None,
}
_E_CONFIRMATION = (
    "Type RUN WITHOUT APPROVAL to confirm that automatic runs archive and purge history without"
    " anyone approving them first."
)
_E_AUTHORIZATION_REASON = (
    "Enter the reason for letting automatic runs start without approval: 1 to 500 characters of"
    " plain text."
)
_E_APPROVAL_REASON = "Enter the reason for approving this run: 1 to 500 characters of plain text."


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@dataclass
class World:
    builder: Builder
    url: URL
    owner: Engine
    client: TestClient


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    database = f"partflow_test_automatic_runs_{uuid.uuid4().hex[:8]}"
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
            found = World(builder, url, owner, client)
            _old_stocked(found, 3)
            yield found
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


@pytest.fixture(autouse=True)
def reset(world: World) -> Iterator[None]:
    """Every case ends with no open run and the default settings."""
    yield
    with world.owner.connect() as connection:
        open_runs = connection.execute(
            sa.text(
                "SELECT id, state FROM history_archive_runs"
                " WHERE state IN ('PROPOSED', 'APPROVED', 'RUNNING', 'WAITING_FOR_COPY')"
            )
        ).all()
    for run_id, state in open_runs:
        if state == "PROPOSED":
            _ok(world.builder.admin.post(f"{_RUNS}/{run_id}/dismiss"))
            continue
        with world.owner.begin() as connection:
            connection.execute(
                sa.text("SELECT set_config('partflow.archive_run_id', :id, true)"),
                {"id": str(run_id)},
            )
            connection.execute(
                sa.text(
                    "UPDATE history_archive_runs SET state = 'FAILED', finished_at = now(),"
                    " failure_reason = 'test cleanup' WHERE id = :id"
                ),
                {"id": run_id},
            )
    _policy(world, **_DEFAULT_POLICY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(response: httpx.Response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _detail(response: httpx.Response, status: int) -> Any:
    assert response.status_code == status, response.text
    return response.json()["detail"]


def _old_stocked(world: World, count: int, label: str = "AUTO") -> set[int]:
    """``count`` new old components (released and stocked whole, moved 36 months back)."""
    builder = world.builder
    flows: set[int] = set()
    for _ in range(count):
        pn = part_number(label)
        flow = builder.release(pn, 3)
        builder.stock("MAT", flow, pn, 3)
        flows |= builder.component(flow)
    backdate(world.owner, flows)
    return flows


def _policy(world: World, **values: object) -> None:
    """Set settings directly (owner setup; the API's rules are tested on their own)."""
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    with world.owner.begin() as connection:
        connection.execute(sa.text(f"UPDATE application_policy SET {assignments}"), values)


def _automatic_on(world: World, **values: object) -> None:
    """Automatic runs on, with a measure every database passes unless a case says otherwise."""
    _policy(world, archive_automatic_runs=True, archive_limit_movement_rows=0, **values)


def _at(monkeypatch: pytest.MonkeyPatch, moment: datetime.datetime) -> datetime.datetime:
    """The archival clock (the cutoff, the interval, the dismissal time) stands at ``moment``."""
    monkeypatch.setattr(history_archive, "now", lambda: moment)
    return moment


def _due(world: World, monkeypatch: pytest.MonkeyPatch) -> datetime.datetime:
    """Move the archival clock to a moment the next automatic run is due."""
    with world.owner.connect() as connection:
        newest = connection.execute(
            sa.text("SELECT max(finished_at) FROM history_archive_runs WHERE trigger = 'AUTOMATIC'")
        ).scalar_one()
        interval = connection.execute(
            sa.text("SELECT archive_interval_days FROM application_policy")
        ).scalar_one()
    moment = datetime.datetime.now(datetime.UTC)
    if newest is not None:
        moment = max(moment, newest + datetime.timedelta(days=int(interval), minutes=1))
    return _at(monkeypatch, moment)


def _finishing_last(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The job's clock after every run's end, so the run it fails next is the last finished
    one (earlier cases dismissed runs at the later moments of the archival clock)."""
    with world.owner.connect() as connection:
        newest = connection.execute(
            sa.text("SELECT max(finished_at) FROM history_archive_runs")
        ).scalar_one()
    moment = datetime.datetime.now(datetime.UTC)
    if newest is not None:
        moment = max(moment, newest + datetime.timedelta(minutes=1))
    monkeypatch.setattr(history_archive_job, "_now", lambda: moment)


def _evaluate(job: Engine, folders: JobFolders, result: str) -> JobReport:
    report = history_archive_job.evaluate(job, folders)
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_DONE, result), document
    return report


def _movements(world: World) -> int:
    with world.owner.connect() as connection:
        return int(connection.execute(sa.text("SELECT count(*) FROM part_movements")).scalar_one())


def _audits(world: World, run_id: int) -> list[dict[str, Any]]:
    with world.owner.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id, event_type, actor_user_id, before_data, after_data, metadata"
                    " FROM audit_events WHERE entity_type = 'HistoryArchiveRun'"
                    " AND entity_id = :id ORDER BY id"
                ),
                {"id": str(run_id)},
            ).mappings()
        ]


def _policy_audits(world: World) -> list[dict[str, Any]]:
    with world.owner.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id, actor_user_id, before_data, after_data, metadata FROM audit_events"
                    " WHERE entity_type = 'ApplicationPolicy' AND entity_id = 'data-retention'"
                    " ORDER BY id"
                )
            ).mappings()
        ]


def _stored_authorization(world: World) -> int | None:
    with world.owner.connect() as connection:
        value = connection.execute(
            sa.text("SELECT archive_authorization_id FROM application_policy")
        ).scalar_one()
    return None if value is None else int(value)


def _proposed(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> int:
    """A PROPOSED automatic run, created by ``evaluate``."""
    _automatic_on(world)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "created")
    assert report.state == "PROPOSED" and report.run_id is not None
    return report.run_id


def _key_holder(world: World) -> IdentityClient:
    return client_as(world.client, _RHAP, Permission.VIEW_PRODUCTION_DATA)


def _authorize(holder: IdentityClient, reason: str = "Weekly archival without approval") -> int:
    body = _ok(
        holder.put(
            _SETTINGS,
            json={"archive_approval_required": False, "reason": reason, "confirmation": _CONFIRM},
        )
    )
    standing = body["standing_authorization"]
    assert standing["valid"] and standing["authorized_by"]["user_id"] == holder.user_id
    return int(standing["audit_event_id"])


def _lose(world: World, holder: IdentityClient, loss: str) -> None:
    """The User loses the archival key, or is made inactive (owner setup)."""
    assert holder.identity is not None
    with world.owner.begin() as connection:
        if loss == "key":
            connection.execute(
                sa.text("DELETE FROM role_permissions WHERE role_id = :role AND permission = :key"),
                {"role": holder.identity.role_id, "key": _RHAP.value},
            )
        else:
            connection.execute(
                sa.text("UPDATE users SET is_active = false WHERE id = :id"),
                {"id": holder.user_id},
            )


def _restore(world: World, holder: IdentityClient, loss: str) -> None:
    """The User gets the archival key back, or is made active again (owner setup)."""
    assert holder.identity is not None
    with world.owner.begin() as connection:
        if loss == "key":
            connection.execute(
                sa.text("INSERT INTO role_permissions (role_id, permission) VALUES (:role, :key)"),
                {"role": holder.identity.role_id, "key": _RHAP.value},
            )
        else:
            connection.execute(
                sa.text("UPDATE users SET is_active = true WHERE id = :id"),
                {"id": holder.user_id},
            )


def _who(world: World, holder: IdentityClient) -> str:
    """The User as the texts name them: ``<display name> (user <id>)``."""
    with world.owner.connect() as connection:
        name = connection.execute(
            sa.text("SELECT display_name FROM users WHERE id = :id"), {"id": holder.user_id}
        ).scalar_one()
    return f"{name} (user {holder.user_id})"


def _switched_on_again(run_id: int) -> str:
    return (
        f"The approval step of automatic runs was switched on again after run {run_id} was"
        " created without approval, so its history was not purged. Nothing was deleted; while"
        " the step is on, the next automatic run waits for approval, and a holder of Run history"
        " archival and purge may request a new run."
    )


def _failed_under(run_id: int, who: str) -> str:
    return (
        f"Run {run_id} failed because the User who switched off the approval step of automatic"
        f" runs, {who}, could no longer run it. This standing authorization stays invalid, even"
        " once that User is active and holds Run history archival and purge again: no automatic"
        " run is created until a holder of the key saves the approval setting again."
    )


def _start(request: Callable[[], httpx.Response]) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(request()))
    thread.start()
    return thread, results


def _finish(thread: threading.Thread, results: list[Any]) -> httpx.Response:
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(results) == 1
    return cast(httpx.Response, results[0])


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {"archive_measure": "ROWS"},
            "Choose the measure of automatic runs: live Movement rows (LIVE_MOVEMENT_ROWS), the"
            " age of retained history (RETAINED_HISTORY_AGE) or the database size"
            " (DATABASE_SIZE).",
        ),
        (
            {"archive_measure": None},
            "Choose the measure of automatic runs: live Movement rows (LIVE_MOVEMENT_ROWS), the"
            " age of retained history (RETAINED_HISTORY_AGE) or the database size"
            " (DATABASE_SIZE).",
        ),
        (
            {"archive_limit_movement_rows": -1},
            "The limit of live Movement rows must be a whole number from 0 to 1000000000.",
        ),
        (
            {"archive_limit_movement_rows": 1_000_000_001},
            "The limit of live Movement rows must be a whole number from 0 to 1000000000.",
        ),
        (
            {"archive_limit_history_months": 1201},
            "The limit of the age of retained history must be a whole number of months from 0 to"
            " 1200.",
        ),
        (
            {"archive_limit_database_gib": 100_001},
            "The limit of the database size must be a whole number of GiB from 0 to 100000.",
        ),
        (
            {"archive_interval_days": 0},
            "The days between automatic runs must be a whole number from 1 to 365.",
        ),
        (
            {"archive_interval_days": 366},
            "The days between automatic runs must be a whole number from 1 to 365.",
        ),
        ({"archive_automatic_runs": None}, "Automatic runs must be On or Off."),
        (
            {"archive_approval_required": None},
            "The approval step of automatic runs must be On or Off.",
        ),
    ],
)
def test_the_settings_of_automatic_runs_keep_their_ranges(
    world: World, body: dict[str, Any], detail: str
) -> None:
    count = len(_policy_audits(world))
    assert _detail(world.builder.admin.put(_SETTINGS, json=body), 422) == detail
    assert len(_policy_audits(world)) == count


def test_the_automatic_runs_settings_merge_and_are_audited(world: World) -> None:
    changes = {
        "archive_automatic_runs": True,
        "archive_measure": "DATABASE_SIZE",
        "archive_limit_movement_rows": 0,
        "archive_limit_history_months": 1200,
        "archive_limit_database_gib": 0,
        "archive_interval_days": 365,
    }
    body = _ok(world.builder.admin.put(_SETTINGS, json=changes))
    assert {key: body[key] for key in changes} == changes
    assert body["archive_approval_required"] is True and body["standing_authorization"] is None
    last = _policy_audits(world)[-1]
    assert {key: last["after_data"][key] for key in changes} == changes
    assert last["metadata"] is None
    # A non-boolean switch is the schema's refusal (strict booleans).
    response = world.builder.admin.put(_SETTINGS, json={"archive_automatic_runs": "yes"})
    assert response.status_code == 422 and isinstance(response.json()["detail"], list)


def test_switching_the_approval_step_off_needs_a_reason_and_the_confirmation(world: World) -> None:
    holder = _key_holder(world)
    count = len(_policy_audits(world))
    for body, detail in (
        ({"archive_approval_required": False, "reason": "Weekly"}, _E_CONFIRMATION),
        (
            {"archive_approval_required": False, "reason": "Weekly", "confirmation": "yes"},
            _E_CONFIRMATION,
        ),
        ({"archive_approval_required": False, "confirmation": _CONFIRM}, _E_AUTHORIZATION_REASON),
        (
            {"archive_approval_required": False, "confirmation": _CONFIRM, "reason": " "},
            _E_AUTHORIZATION_REASON,
        ),
        (
            {"confirmation": _CONFIRM, "reason": "Weekly"},
            "Type the confirmation only to switch off the approval step of automatic runs.",
        ),
    ):
        assert _detail(holder.put(_SETTINGS, json=body), 422) == detail, body
    assert len(_policy_audits(world)) == count and _stored_authorization(world) is None
    # Switched off: the audit row is the standing authorization; the setting keeps its id.
    body = _ok(
        holder.put(
            _SETTINGS,
            json={
                "archive_approval_required": False,
                "reason": " Weekly archival without approval ",
                "confirmation": " run without  approval ",
            },
        )
    )
    standing = body["standing_authorization"]
    [row] = _policy_audits(world)[count:]
    assert standing["audit_event_id"] == row["id"]
    assert standing["authorized_by"]["user_id"] == holder.user_id
    assert (standing["reason"], standing["valid"], standing["problem"]) == (
        "Weekly archival without approval",
        True,
        None,
    )
    assert standing["authorized_at"] is not None
    assert row["actor_user_id"] == holder.user_id
    assert row["metadata"] == {
        "reason": "Weekly archival without approval",
        "standing_authorization": "granted",
    }
    before, after = row["before_data"], row["after_data"]
    assert (before["archive_approval_required"], after["archive_approval_required"]) == (
        True,
        False,
    )
    assert "archive_authorization_id" not in row["after_data"]
    assert _stored_authorization(world) == row["id"]
    # Saving it off again without a confirmation changes nothing.
    _ok(holder.put(_SETTINGS, json={"archive_approval_required": False}))
    assert len(_policy_audits(world)) == count + 1
    # A confirmation renews it: a new audit row and id, the reason kept.
    renewed = _ok(
        world.builder.admin.put(_SETTINGS, json={"confirmation": _CONFIRM, "reason": "Renewed"})
    )["standing_authorization"]
    [renewal] = _policy_audits(world)[count + 1 :]
    assert renewed["audit_event_id"] == renewal["id"] > row["id"]
    assert renewal["metadata"] == {"reason": "Renewed", "standing_authorization": "granted"}
    assert renewal["before_data"] == renewal["after_data"]
    assert _stored_authorization(world) == renewal["id"]
    # Switching the step on withdraws it.
    body = _ok(world.builder.admin.put(_SETTINGS, json={"archive_approval_required": True}))
    assert body["standing_authorization"] is None and _stored_authorization(world) is None
    assert _policy_audits(world)[-1]["metadata"] == {"standing_authorization": "withdrawn"}


# ---------------------------------------------------------------------------
# evaluate: one condition at a time
# ---------------------------------------------------------------------------


def test_evaluate_creates_nothing_while_automatic_runs_are_off(
    world: World, job: Engine, folders: JobFolders
) -> None:
    report = _evaluate(job, folders, "off")
    assert report.details["automatic_runs"] is False
    assert report.details["measure"] is None and report.details["preview"] is None
    assert report.details["run"] is None and report.run_id is None
    assert report.details["message"] == (
        "Automatic runs are off (History archival & purge), so no run was created."
    )


def test_evaluate_needs_a_retention_period(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    _automatic_on(world, retention_period_months=None)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "no_retention_period")
    assert report.details["cutoff"] is None and report.details["measure"] is None


def test_evaluate_waits_while_a_run_is_open(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    manual = request_run(world.builder, "Requested by hand")
    _automatic_on(world)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "run_open")
    assert (report.run_id, report.state) == (manual, "APPROVED")
    assert report.details["open_run"] == {"id": manual, "state": "APPROVED"}
    assert report.details["message"] == (
        f"Run {manual} is still open (APPROVED); no automatic run is created while a run is open."
    )
    _ok(world.builder.admin.post(f"{_RUNS}/{manual}/cancel"))
    # A proposed run is open too.
    proposed = _proposed(world, job, folders, monkeypatch)
    report = _evaluate(job, folders, "run_open")
    assert report.details["open_run"] == {"id": proposed, "state": "PROPOSED"}


def test_evaluate_counts_the_live_movement_rows_against_their_limit(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _movements(world)
    _automatic_on(world)
    _due(world, monkeypatch)
    for limit, result in ((rows, "below_limit"), (rows + 1, "below_limit"), (rows - 1, "created")):
        _policy(world, archive_limit_movement_rows=limit)
        report = _evaluate(job, folders, result)
        assert report.details["measure"] == {
            "name": "LIVE_MOVEMENT_ROWS",
            "read": "count",
            "value": rows,
            "limit": limit,
            "threshold": None,
            "passes": result == "created",
        }
    assert report.details["run"]["reason"] == (
        f"Live Movement rows {rows:,} above the limit {rows - 1:,}; retention 24 months"
    )
    assert _detail_of_run(world, report)["reason"] == report.details["run"]["reason"]


def _detail_of_run(world: World, report: JobReport) -> dict[str, Any]:
    assert report.run_id is not None
    return run_row(world.owner, report.run_id)


def test_evaluate_reads_the_age_of_retained_history(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The oldest retained Movement passes when it is older than the cutoff by more than the
    limit: strictly before the start of the cutoff's day, the limit's months earlier."""
    flows = _old_stocked(world, 1, "OLDEST")
    _automatic_on(world, archive_measure="RETAINED_HISTORY_AGE", archive_limit_history_months=30)
    moment = _due(world, monkeypatch)
    zone = history_archive.site_zone()
    cutoff = history_archive.months_before(history_archive.site_date_of(moment, zone), 24)
    threshold = history_archive.start_of_day(history_archive.months_before(cutoff, 30), zone)
    for offset, result in (
        (datetime.timedelta(0), "below_limit"),
        (datetime.timedelta(seconds=1), "below_limit"),
        (-datetime.timedelta(microseconds=1), "created"),
    ):
        _first_movement_at(world, flows, threshold + offset)
        report = _evaluate(job, folders, result)
        assert report.details["measure"]["value"] == history_archive.iso(threshold + offset)
        assert report.details["measure"]["threshold"] == history_archive.iso(threshold)
        assert report.details["measure"]["read"] == "index"
    oldest = history_archive.site_date_of(threshold - datetime.timedelta(microseconds=1), zone)
    assert report.details["run"]["reason"] == (
        f"Oldest retained Movement {oldest.isoformat()} more than 30 months older than the cutoff"
        f" {cutoff.isoformat()}; retention 24 months"
    )


def _first_movement_at(world: World, flows: set[int], moment: datetime.datetime) -> None:
    """The flows' first Movement at ``moment``, the others an hour later (owner setup)."""
    with world.owner.begin() as connection:
        connection.execute(
            sa.text("ALTER TABLE part_movements DISABLE TRIGGER trg_part_movements_forbid_mutation")
        )
        first = connection.execute(
            sa.text("SELECT min(id) FROM part_movements WHERE quantity_flow_id = ANY(:ids)"),
            {"ids": sorted(flows)},
        ).scalar_one()
        later = moment + datetime.timedelta(hours=1)
        connection.execute(
            sa.text(
                "UPDATE part_movements SET occurred_at = CASE WHEN id = :first THEN :moment"
                " ELSE :later END, server_received_at = CASE WHEN id = :first THEN :moment"
                " ELSE :later END WHERE quantity_flow_id = ANY(:ids)"
            ),
            {"first": first, "moment": moment, "later": later, "ids": sorted(flows)},
        )
        connection.execute(
            sa.text("ALTER TABLE part_movements ENABLE TRIGGER trg_part_movements_forbid_mutation")
        )


def test_evaluate_compares_the_database_size_with_its_limit(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    _automatic_on(world, archive_measure="DATABASE_SIZE", archive_limit_database_gib=20)
    _due(world, monkeypatch)
    limit = 20 * history_archive.GIB
    for size, result in (
        (limit, "below_limit"),
        (limit - 1, "below_limit"),
        (limit + 1, "created"),
    ):
        monkeypatch.setattr(history_archive, "database_bytes", lambda _connection, s=size: s)
        report = _evaluate(job, folders, result)
        assert report.details["measure"]["value"] == size
        assert report.details["measure"]["read"] == "size"
    assert report.details["run"]["reason"] == (
        f"Database size {limit + 1:,} bytes (20.0 GiB) above the limit 20 GiB; retention 24 months"
    )
    # The real size: every database is larger than a limit of 0 GiB.
    _ok(world.builder.admin.post(f"{_RUNS}/{report.run_id}/dismiss"))
    monkeypatch.undo()
    _automatic_on(world, archive_measure="DATABASE_SIZE", archive_limit_database_gib=0)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "created")
    assert report.details["measure"]["value"] > 0 and report.details["measure"]["passes"]


def test_evaluate_needs_something_eligible(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    _automatic_on(world, retention_period_months=1200)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "nothing_eligible")
    assert report.details["measure"]["passes"] is True
    assert report.details["preview"]["flows"] == 0
    assert report.details["message"].startswith("Nothing is eligible before ")


def test_evaluate_proposes_a_run_and_waits_the_days_between_automatic_runs(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _proposed(world, job, folders, monkeypatch)
    run = run_row(world.owner, run_id)
    assert (run["trigger"], run["requested_by_user_id"], run["authorization_id"]) == (
        "AUTOMATIC",
        None,
        None,
    )
    assert run["preview_flows"] > 0 and run["reason"].startswith("Live Movement rows ")
    [created] = _audits(world, run_id)
    assert (created["event_type"], created["actor_user_id"], created["before_data"]) == (
        "CREATED",
        None,
        None,
    )
    assert created["metadata"] == {"source": "maintenance", "command": "evaluate"}
    assert created["after_data"]["state"] == "PROPOSED"
    # It waits for a person: next has nothing to do, the runs list shows it.
    report = history_archive_job.next_run(job, folders)
    assert (report.result, report.state) == ("wait", "PROPOSED")
    [listed] = [item for item in _ok(world.builder.admin.get(_RUNS)) if item["id"] == run_id]
    assert (listed["trigger"], listed["requested_by"], listed["approval"]) == (
        "AUTOMATIC",
        None,
        None,
    )
    # Dismissed: the next automatic run is due the interval later, not before.
    ended = _at(monkeypatch, datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30))
    _ok(world.builder.admin.post(f"{_RUNS}/{run_id}/dismiss"))
    _at(monkeypatch, ended + datetime.timedelta(days=7, seconds=-1))
    report = _evaluate(job, folders, "not_due")
    assert report.details["last_automatic_run"]["id"] == run_id
    assert report.details["next_due_at"] == history_archive.iso(ended + datetime.timedelta(days=7))
    _at(monkeypatch, ended + datetime.timedelta(days=7))
    second = _evaluate(job, folders, "created").run_id
    # The interval is a setting.
    _policy(world, archive_interval_days=1)
    ended = _at(monkeypatch, ended + datetime.timedelta(days=8))
    _ok(world.builder.admin.post(f"{_RUNS}/{second}/dismiss"))
    _at(monkeypatch, ended + datetime.timedelta(hours=23))
    _evaluate(job, folders, "not_due")
    _at(monkeypatch, ended + datetime.timedelta(days=1))
    _evaluate(job, folders, "created")


def test_evaluate_reports_a_time_limit_as_could_not_run(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    _automatic_on(world)
    _due(world, monkeypatch)

    def expired(*_args: object) -> history_archive.Measurement:
        raise ConflictError("The preview did not finish within its time limit.")

    monkeypatch.setattr(history_archive, "measure", expired)
    report = history_archive_job.evaluate(job, folders)
    assert (report.exit_code, report.result) == (
        history_archive_job.EXIT_COULD_NOT_RUN,
        "could_not_run",
    )
    assert report.error is not None and report.error.code == "evaluation_timed_out"
    assert report.error.message.startswith(
        "The evaluation of automatic runs did not finish within the preview time limit of 90"
        " seconds, so no run was created."
    )


@pytest.mark.parametrize("race", ["settings_changed", "run_open"])
def test_evaluate_creates_nothing_when_the_settings_or_the_runs_change_meanwhile(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    _automatic_on(world)
    _due(world, monkeypatch)
    decide = history_archive.evaluate_automatic
    opened: list[int] = []

    def meanwhile(connection: sa.Connection) -> history_archive.AutomaticDecision:
        decision = decide(connection)
        if race == "settings_changed":
            _policy(world, archive_automatic_runs=False)
        else:
            opened.append(request_run(world.builder, "Requested while the job evaluated"))
        return decision

    monkeypatch.setattr(history_archive, "evaluate_automatic", meanwhile)
    report = _evaluate(job, folders, race)
    assert report.details["run"] is None
    if opened:
        assert (report.run_id, report.state) == (opened[0], "APPROVED")
    with world.owner.connect() as connection:
        automatic = connection.execute(
            sa.text("SELECT count(*) FROM history_archive_runs WHERE state = 'PROPOSED'")
        ).scalar_one()
    assert automatic == 0


_SETTINGS_CHANGED = (
    "The settings of automatic runs or the retention period changed while the evaluation ran, so"
    " no run was created; the next hourly work evaluates again."
)


def _automatic_runs(world: World) -> int:
    with world.owner.connect() as connection:
        return int(
            connection.execute(
                sa.text("SELECT count(*) FROM history_archive_runs WHERE trigger = 'AUTOMATIC'")
            ).scalar_one()
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("retention_period_months", 30),
        ("archive_measure", "DATABASE_SIZE"),
        ("archive_limit_movement_rows", 1),
        ("archive_limit_history_months", 2),
        ("archive_limit_database_gib", 21),
        ("archive_interval_days", 8),
    ],
)
def test_evaluate_creates_nothing_when_a_setting_it_decided_from_changes_meanwhile(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    column: str,
    value: object,
) -> None:
    # Review B4, minor 1: the insert's transaction reads every setting the decision used.
    _automatic_on(world)
    _due(world, monkeypatch)
    decide = history_archive.evaluate_automatic
    before = _automatic_runs(world)

    def meanwhile(connection: sa.Connection) -> history_archive.AutomaticDecision:
        decision = decide(connection)
        _policy(world, **{column: value})
        return decision

    monkeypatch.setattr(history_archive, "evaluate_automatic", meanwhile)
    report = _evaluate(job, folders, "settings_changed")
    assert report.details["message"] == _SETTINGS_CHANGED
    assert report.details["run"] is None and report.run_id is None
    assert _automatic_runs(world) == before


@pytest.mark.parametrize("change", ["approval_step_on", "shortest_period_longer"])
def test_a_guard_refusal_of_the_insert_answers_settings_changed(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    # Review B4, minor 2: a setting the run guard reads changes after the job's
    # own check, before its insert: the guard refuses it (P0001, or 23514 for
    # the cutoff floor), and evaluate answers settings_changed, exit 0.
    if change == "approval_step_on":
        _authorize(_key_holder(world))
    _automatic_on(world)
    _due(world, monkeypatch)
    before = _automatic_runs(world)
    hold = history_archive.AutomaticDecision.settings_hold

    def checked_then_changed(
        decision: history_archive.AutomaticDecision, connection: sa.Connection
    ) -> bool:
        held = hold(decision, connection)
        if change == "approval_step_on":
            _policy(world, archive_approval_required=True, archive_authorization_id=None)
        else:
            _policy(world, retention_period_months=36, shortest_retention_months=36)
        return held

    monkeypatch.setattr(history_archive.AutomaticDecision, "settings_hold", checked_then_changed)
    try:
        report = _evaluate(job, folders, "settings_changed")
    finally:
        _policy(world, shortest_retention_months=12)
    assert report.details["message"] == _SETTINGS_CHANGED
    assert report.details["run"] is None
    assert _automatic_runs(world) == before


def test_the_cli_evaluates(
    world: World,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(history_archive_job.ARCHIVE_DIR_ENV, str(folders.archive_dir))
    monkeypatch.setattr(cli, "_history_archive_engine", lambda: job_engine(world.url))
    code = cli.main(["history-archive", "evaluate"])
    document = json.loads(capsys.readouterr().out)
    assert (code, document["command"], document["result"]) == (0, "history-archive evaluate", "off")
    assert list(document)[:10] == [
        "report_version",
        "command",
        "result",
        "exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "run_id",
        "request_id",
        "state",
    ]
    assert {
        "automatic_runs",
        "approval_required",
        "retention_period_months",
        "cutoff",
        "cutoff_date",
        "interval_days",
        "open_run",
        "last_automatic_run",
        "next_due_at",
        "standing_authorization",
        "measure",
        "preview",
        "message",
        "run",
        "error",
    } <= set(document)


# ---------------------------------------------------------------------------
# Approve and dismiss
# ---------------------------------------------------------------------------


def test_a_proposed_run_is_approved_with_a_reason_of_its_own(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _proposed(world, job, folders, monkeypatch)
    approver = _key_holder(world)
    path = f"{_RUNS}/{run_id}/approve"
    assert approver.post(path, json={}).status_code == 422
    assert _detail(approver.post(path, json={"reason": " "}), 422) == _E_APPROVAL_REASON
    assert _detail(approver.post(path, json={"reason": "x" * 501}), 422) == _E_APPROVAL_REASON
    assert _detail(approver.post(f"{_RUNS}/999999/approve", json={"reason": "Go"}), 404) == (
        "There is no archival run 999999."
    )
    body = _ok(approver.post(path, json={"reason": " Old history, as agreed "}))
    assert body["state"] == "APPROVED"
    assert body["approval"]["approved_by"]["user_id"] == approver.user_id
    assert body["approval"]["reason"] == "Old history, as agreed"
    run = run_row(world.owner, run_id)
    assert (run["approved_by_user_id"], run["approval_reason"]) == (
        approver.user_id,
        "Old history, as agreed",
    )
    assert run["approved_at"] >= run["requested_at"]
    approval = _audits(world, run_id)[-1]
    assert approval["actor_user_id"] == approver.user_id
    assert approval["metadata"] == {"source": "web", "command": "approve"}
    assert approval["after_data"]["approval_reason"] == "Old history, as agreed"
    # Approving again as the same User changes nothing; another User is refused.
    count = len(_audits(world, run_id))
    assert _ok(approver.post(path, json={"reason": "Again"}))["approval"]["reason"] == (
        "Old history, as agreed"
    )
    assert len(_audits(world, run_id)) == count
    assert _detail(world.builder.admin.post(path, json={"reason": "Me too"}), 409) == (
        f"Run {run_id} is APPROVED; only a run waiting for approval (PROPOSED) can be approved."
    )
    assert _detail(world.builder.admin.post(f"{_RUNS}/{run_id}/dismiss"), 409) == (
        f"Run {run_id} is APPROVED; only a run waiting for approval (PROPOSED) can be dismissed."
        " Cancel it instead."
    )
    # The job takes the approved run.
    assert history_archive_job.next_run(job, folders).result == "export"


def test_a_proposed_run_is_dismissed_like_a_cancel(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _proposed(world, job, folders, monkeypatch)
    admin = world.builder.admin
    assert _detail(admin.post(f"{_RUNS}/{run_id}/cancel"), 409) == (
        f"Run {run_id} waits for approval (PROPOSED): dismiss it instead of cancelling it."
    )
    assert _detail(admin.post(_RUNS, json={"reason": "By hand"}), 409) == (
        f"Run {run_id} is waiting for approval (PROPOSED). Approve or dismiss it before requesting"
        " a new run."
    )
    body = _ok(admin.post(f"{_RUNS}/{run_id}/dismiss"))
    assert body["state"] == "DISMISSED" and body["finished_at"] is not None
    assert body["dismissed_by"]["user_id"] == admin.user_id
    assert _audits(world, run_id)[-1]["metadata"] == {"source": "web", "command": "dismiss"}
    count = len(_audits(world, run_id))
    assert _ok(admin.post(f"{_RUNS}/{run_id}/dismiss"))["state"] == "DISMISSED"
    assert len(_audits(world, run_id)) == count
    assert _detail(admin.post(f"{_RUNS}/{run_id}/approve", json={"reason": "Late"}), 409) == (
        f"Run {run_id} is DISMISSED; only a run waiting for approval (PROPOSED) can be approved."
    )
    # A run the job works on, and a finished one, are refused.
    manual = request_run(world.builder, "Dismissal check")
    done(history_archive_job.export(job, folders, manual), "exported")
    assert _detail(admin.post(f"{_RUNS}/{manual}/dismiss"), 409) == (
        f"Run {manual} is running on the server now and cannot be dismissed. Wait until it stops."
    )
    done(history_archive_job.recover(job, folders), "recovered")
    assert run_row(world.owner, manual)["state"] == "FAILED"
    assert _detail(admin.post(f"{_RUNS}/{manual}/dismiss"), 409) == (
        f"Run {manual} is finished (FAILED) and cannot be dismissed."
    )


_ACTIONS: dict[str, Callable[[IdentityClient, int], httpx.Response]] = {
    "approve": lambda client, run_id: client.post(
        f"{_RUNS}/{run_id}/approve", json={"reason": f"Approved by {client.user_id}"}
    ),
    "dismiss": lambda client, run_id: client.post(f"{_RUNS}/{run_id}/dismiss"),
    "cancel": lambda client, run_id: client.post(f"{_RUNS}/{run_id}/cancel"),
}


def _acting(action: str, person: IdentityClient, run_id: int) -> Callable[[], httpx.Response]:
    return lambda: _ACTIONS[action](person, run_id)


@pytest.mark.parametrize(
    ("first", "second"),
    [("approve", "cancel"), ("dismiss", "cancel"), ("approve", "approve"), ("approve", "dismiss")],
)
def test_a_race_for_a_proposed_run(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
    second: str,
) -> None:
    """Both wait on the run's row lock; whoever takes it first wins, and the other sees the
    committed outcome: never two approvals, never a cancelled or dismissed approval lost."""
    run_id = _proposed(world, job, folders, monkeypatch)
    people = (_key_holder(world), _key_holder(world))
    before = len(_audits(world, run_id))
    with world.owner.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM history_archive_runs WHERE id = :id FOR UPDATE"), {"id": run_id}
        )
        threads = [
            _start(_acting(action, person, run_id))
            for action, person in zip((first, second), people, strict=True)
        ]
        for thread, _results in threads:
            thread.join(timeout=0.5)
            assert thread.is_alive()
        holder.rollback()
    responses = [_finish(thread, results) for thread, results in threads]
    run = run_row(world.owner, run_id)
    statuses = {
        (action, response.status_code)
        for action, response in zip((first, second), responses, strict=True)
    }
    succeeded = [response for response in responses if response.status_code == 200]
    assert all(response.status_code in (200, 409) for response in responses)
    assert len(_audits(world, run_id)) == before + len(succeeded)
    if (first, second) == ("approve", "cancel"):
        # The approval always stands; the cancel wins only after it.
        assert ("approve", 200) in statuses
        assert run["state"] == ("CANCELLED" if ("cancel", 200) in statuses else "APPROVED")
        assert run["approved_by_user_id"] == people[0].user_id
    elif (first, second) == ("dismiss", "cancel"):
        assert statuses == {("dismiss", 200), ("cancel", 409)}
        assert run["state"] == "DISMISSED"
    elif (first, second) == ("approve", "approve"):
        assert sorted(response.status_code for response in responses) == [200, 409]
        winner = people[0] if responses[0].status_code == 200 else people[1]
        assert (run["state"], run["approved_by_user_id"]) == ("APPROVED", winner.user_id)
    else:
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert run["state"] == ("APPROVED" if responses[0].status_code == 200 else "DISMISSED")


# ---------------------------------------------------------------------------
# The standing authorization, the re-checks and the reminders
# ---------------------------------------------------------------------------


def test_an_automatic_run_starts_approved_under_the_standing_authorization(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder = _key_holder(world)
    authorization = _authorize(holder)
    _automatic_on(world)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "created")
    assert report.state == "APPROVED" and report.run_id is not None
    run_id = report.run_id
    assert report.details["standing_authorization"]["audit_event_id"] == authorization
    assert report.details["message"].startswith(
        f"Run {run_id} was created APPROVED under the standing authorization of audit row"
        f" {authorization}: Live Movement rows "
    )
    run = run_row(world.owner, run_id)
    assert (run["trigger"], run["authorization_id"], run["approved_at"]) == (
        "AUTOMATIC",
        authorization,
        None,
    )
    [listed] = [item for item in _ok(world.builder.admin.get(_RUNS)) if item["id"] == run_id]
    assert listed["authorization"]["audit_event_id"] == authorization
    assert listed["authorization"]["authorized_by"]["user_id"] == holder.user_id
    # The job works on it without approval; the archive names its authority.
    assert history_archive_job.next_run(job, folders).result == "export"
    exported = history_archive_job.export(job, folders, run_id)
    done(exported, "exported")
    assert folders.archive_dir is not None
    folder = folders.archive_dir / ".partial" / str(exported.details["archive_name"])
    manifest = json.loads((folder / history_archive_job.MANIFEST_FILE).read_bytes())
    assert manifest["run"]["trigger"] == "AUTOMATIC"
    assert manifest["run"]["requested_by_user_id"] is None
    assert manifest["run"]["authorization_id"] == authorization
    context = json.loads((folder / history_archive_job.CONTEXT_FILE).read_bytes())
    assert holder.user_id in {user["id"] for user in context["users"]}


@pytest.mark.parametrize(
    ("stage", "loss"),
    [("backup", "key"), ("backup", "inactive"), ("purge", "key"), ("purge", "inactive")],
)
def test_the_standing_authorization_is_rechecked_before_the_backup_and_the_purge(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    loss: str,
) -> None:
    holder = _key_holder(world)
    _authorize(holder)
    _automatic_on(world)
    _due(world, monkeypatch)
    run_id = _evaluate(job, folders, "created").run_id
    assert run_id is not None
    copied_run(job, folders, run_id)
    movements = _movements(world)
    backup: PurgeBackup | None = None
    if stage == "purge":
        done(history_archive_job.next_run(job, folders), "backup")
        backup = backup_of(world.owner, run_id)
    _lose(world, holder, loss)
    _finishing_last(world, monkeypatch)
    report = (
        history_archive_job.next_run(job, folders)
        if stage == "backup"
        else history_archive_job.purge(job, folders, run_id, backup)
    )
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_RUN_FAILED, "failed")
    run = run_row(world.owner, run_id)
    assert (run["state"], run["authorization_failed"], run["purge_started_at"]) == (
        "FAILED",
        True,
        None,
    )
    held = "is no longer active" if loss == "inactive" else "no longer holds Run history"
    assert run["failure_reason"].startswith(
        "The User who switched off the approval step of automatic runs,"
    )
    assert held in run["failure_reason"]
    assert (
        f"so the history of run {run_id} was not purged. Nothing was deleted"
        in (run["failure_reason"])
    )
    assert _movements(world) == movements
    # The reminders, and automatic runs waiting until the setting is saved again.
    status = history_archive_job.status(job, folders)
    assert {"last_run_failed", "authorization_failed", "standing_authorization_invalid"} <= set(
        status.details["findings"]
    )
    standing = status.details["automatic_runs"]["standing_authorization"]
    assert (standing["valid"], standing["problem"]) == (
        False,
        _failed_under(run_id, _who(world, holder)),
    )
    _due(world, monkeypatch)
    waiting = _evaluate(job, folders, "authorization_invalid")
    assert waiting.details["message"] == standing["problem"]
    shown = _ok(world.builder.admin.get(_SETTINGS))["standing_authorization"]
    assert (shown["valid"], shown["problem"]) == (False, standing["problem"])
    renewed = _authorize(world.builder.admin, "Saved again by another holder")
    assert (
        "standing_authorization_invalid"
        not in history_archive_job.status(job, folders).details["findings"]
    )
    report = _evaluate(job, folders, "created")
    assert run_row(world.owner, cast(int, report.run_id))["authorization_id"] == renewed


def _approved_without_approval(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> tuple[IdentityClient, int, int]:
    """A key holder's standing authorization, and a run created under it, verified and copied."""
    holder = _key_holder(world)
    authorization = _authorize(holder)
    _automatic_on(world)
    _due(world, monkeypatch)
    report = _evaluate(job, folders, "created")
    assert report.state == "APPROVED" and report.run_id is not None
    copied_run(job, folders, report.run_id)
    return holder, authorization, report.run_id


@pytest.mark.parametrize(
    ("stage", "change"),
    [("backup", "on"), ("purge", "on"), ("backup", "on_and_off_again")],
)
def test_switching_the_approval_step_on_again_fails_a_run_created_without_approval(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    change: str,
) -> None:
    holder, authorization, run_id = _approved_without_approval(world, job, folders, monkeypatch)
    movements = _movements(world)
    backup: PurgeBackup | None = None
    if stage == "purge":
        done(history_archive_job.next_run(job, folders), "backup")
        backup = backup_of(world.owner, run_id)
    _ok(holder.put(_SETTINGS, json={"archive_approval_required": True}))
    again: int | None = None
    if change == "on_and_off_again":
        # Off again before the job looked: the step did not stay off for this run.
        again = _authorize(world.builder.admin, "Off again with a new authorization")
        assert again != authorization
    _finishing_last(world, monkeypatch)
    report = (
        history_archive_job.next_run(job, folders)
        if stage == "backup"
        else history_archive_job.purge(job, folders, run_id, backup)
    )
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_RUN_FAILED, "failed")
    run = run_row(world.owner, run_id)
    assert (run["state"], run["authorization_failed"], run["purge_started_at"]) == (
        "FAILED",
        True,
        None,
    )
    assert run["failure_reason"] == _switched_on_again(run_id)
    assert _movements(world) == movements
    # Reported as a failed authority; the setting as it stands now decides the next run.
    status = history_archive_job.status(job, folders)
    assert {"last_run_failed", "authorization_failed"} <= set(status.details["findings"])
    assert "standing_authorization_invalid" not in status.details["findings"]
    _due(world, monkeypatch)
    created = _evaluate(job, folders, "created")
    if again is None:
        assert created.state == "PROPOSED"
    else:
        assert created.state == "APPROVED"
        assert run_row(world.owner, cast(int, created.run_id))["authorization_id"] == again


def test_a_renewal_keeps_a_run_created_without_approval_going(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder, authorization, run_id = _approved_without_approval(world, job, folders, monkeypatch)
    renewed = _authorize(world.builder.admin, "Renewed by another holder")
    # The step stayed off: the run goes on under the authorization it was created under.
    done(history_archive_job.next_run(job, folders), "backup")
    backup = backup_of(world.owner, run_id)
    # Its own authorization's User is re-checked, not the renewal's.
    _lose(world, holder, "key")
    _finishing_last(world, monkeypatch)
    report = history_archive_job.purge(job, folders, run_id, backup)
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_RUN_FAILED, "failed")
    run = run_row(world.owner, run_id)
    assert (run["state"], run["authorization_failed"], run["authorization_id"]) == (
        "FAILED",
        True,
        authorization,
    )
    assert run["failure_reason"] == (
        "The User who switched off the approval step of automatic runs,"
        f" {_who(world, holder)}, no longer holds Run history archival and purge, so the history"
        f" of run {run_id} was not purged. Nothing was deleted; the next automatic run follows the"
        " renewed standing authorization."
    )
    # The renewal stays valid: the run failed under the authorization before it.
    status = history_archive_job.status(job, folders)
    assert "standing_authorization_invalid" not in status.details["findings"]
    standing = status.details["automatic_runs"]["standing_authorization"]
    assert (standing["audit_event_id"], standing["valid"]) == (renewed, True)
    _due(world, monkeypatch)
    created = _evaluate(job, folders, "created")
    assert run_row(world.owner, cast(int, created.run_id))["authorization_id"] == renewed


@pytest.mark.parametrize("remedy", ["renewal", "switched_on"])
def test_a_standing_authorization_a_run_failed_under_stays_invalid_until_saved_again(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    remedy: str,
) -> None:
    holder, authorization, run_id = _approved_without_approval(world, job, folders, monkeypatch)
    _lose(world, holder, "key")
    _finishing_last(world, monkeypatch)
    failed = history_archive_job.next_run(job, folders)
    assert failed.exit_code == history_archive_job.EXIT_RUN_FAILED
    assert run_row(world.owner, run_id)["authorization_failed"] is True
    # Its User holds the key again; the authorization stays invalid, everywhere.
    _restore(world, holder, "key")
    problem = _failed_under(run_id, _who(world, holder))
    status = history_archive_job.status(job, folders)
    assert "standing_authorization_invalid" in status.details["findings"]
    standing = status.details["automatic_runs"]["standing_authorization"]
    assert (standing["audit_event_id"], standing["valid"], standing["problem"]) == (
        authorization,
        False,
        problem,
    )
    _due(world, monkeypatch)
    assert _evaluate(job, folders, "authorization_invalid").details["message"] == problem
    shown = _ok(world.builder.admin.get(_SETTINGS))["standing_authorization"]
    assert (shown["valid"], shown["problem"]) == (False, problem)
    # Saving another setting keeps the authorization, and so its invalidity.
    shown = _ok(world.builder.admin.put(_SETTINGS, json={"archive_interval_days": 8}))[
        "standing_authorization"
    ]
    assert (shown["audit_event_id"], shown["valid"], shown["problem"]) == (
        authorization,
        False,
        problem,
    )
    _due(world, monkeypatch)
    _evaluate(job, folders, "authorization_invalid")
    # A key holder saving the approval setting again ends it.
    if remedy == "renewal":
        renewed = _authorize(holder, "Saved again after the failed run")
        created = _evaluate(job, folders, "created")
        assert created.state == "APPROVED"
        assert run_row(world.owner, cast(int, created.run_id))["authorization_id"] == renewed
    else:
        _ok(holder.put(_SETTINGS, json={"archive_approval_required": True}))
        assert _stored_authorization(world) is None
        assert _evaluate(job, folders, "created").state == "PROPOSED"
    findings = history_archive_job.status(job, folders).details["findings"]
    assert "standing_authorization_invalid" not in findings


@pytest.mark.parametrize("loss", ["key", "inactive"])
def test_without_a_failed_run_the_standing_authorization_follows_its_user(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    loss: str,
) -> None:
    holder = _key_holder(world)
    authorization = _authorize(holder)
    _automatic_on(world)
    _lose(world, holder, loss)
    held = "is no longer active" if loss == "inactive" else "no longer holds Run history"
    standing = history_archive_job.status(job, folders).details["automatic_runs"][
        "standing_authorization"
    ]
    assert standing["valid"] is False
    assert standing["problem"].startswith(
        f"The User who switched off the approval step of automatic runs, {_who(world, holder)},"
        f" {held}"
    )
    _due(world, monkeypatch)
    _evaluate(job, folders, "authorization_invalid")
    # Valid again with its User, without a new save.
    _restore(world, holder, loss)
    assert _stored_authorization(world) == authorization
    standing = history_archive_job.status(job, folders).details["automatic_runs"][
        "standing_authorization"
    ]
    assert (standing["valid"], standing["problem"]) == (True, None)
    assert _evaluate(job, folders, "created").state == "APPROVED"


def _rechecked(
    job: Engine, folders: JobFolders, run_id: int, backup: PurgeBackup | None, stage: str
) -> JobReport:
    """The re-check before the pre-purge backup (``next``) or before the purge."""
    report = (
        history_archive_job.next_run(job, folders)
        if stage == "backup"
        else history_archive_job.purge(job, folders, run_id, backup)
    )
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_RUN_FAILED, "failed")
    return report


def _cutoff_not_allowed(run_id: int, cutoff: datetime.datetime, months: int | None) -> str:
    zone = history_archive.site_zone()
    day = history_archive.site_date_of(cutoff, zone).isoformat()
    if months is None:
        return (
            f"No retention period is set now, so the history of run {run_id}, created without"
            f" approval with the cutoff {day}, was not purged. Nothing was deleted; no history is"
            " eligible until a retention period is set."
        )
    today = history_archive.site_date_of(history_archive.now(), zone)
    latest = history_archive.months_before(today, months).isoformat()
    return (
        f"The retention period is now {months} months: it keeps everything since {latest}, and"
        f" run {run_id} was created without approval with the newer cutoff {day}, so its history"
        " was not purged. Nothing was deleted; the next automatic run takes the cutoff the"
        " retention period allows then, and a holder of Run history archival and purge may"
        " request a new run."
    )


def _plain_failure_keeps_the_authorization(
    world: World, job: Engine, folders: JobFolders, run_id: int, authorization: int
) -> None:
    """The run FAILED without ``authorization_failed``; its standing authorization stays valid."""
    run = run_row(world.owner, run_id)
    assert (run["state"], run["authorization_failed"], run["purge_started_at"]) == (
        "FAILED",
        False,
        None,
    )
    status = history_archive_job.status(job, folders)
    findings = set(status.details["findings"])
    assert "last_run_failed" in findings
    assert not {"authorization_failed", "standing_authorization_invalid"} & findings
    standing = status.details["automatic_runs"]["standing_authorization"]
    assert (standing["audit_event_id"], standing["valid"], standing["problem"]) == (
        authorization,
        True,
        None,
    )


@pytest.mark.parametrize(("stage", "months"), [("backup", 30), ("purge", 30), ("backup", None)])
def test_a_run_created_without_approval_needs_its_cutoff_allowed_by_the_current_retention(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    months: int | None,
) -> None:
    # Review B4, minor 1: nobody reviewed its cutoff, so the retention period as it
    # stands now must still allow it.
    holder, authorization, run_id = _approved_without_approval(world, job, folders, monkeypatch)
    movements = _movements(world)
    backup: PurgeBackup | None = None
    if stage == "purge":
        done(history_archive_job.next_run(job, folders), "backup")
        backup = backup_of(world.owner, run_id)
    _ok(holder.put(_SETTINGS, json={"retention_period_months": months}))
    _finishing_last(world, monkeypatch)
    _rechecked(job, folders, run_id, backup, stage)
    run = run_row(world.owner, run_id)
    assert run["failure_reason"] == _cutoff_not_allowed(run_id, run["cutoff"], months)
    assert _movements(world) == movements
    _plain_failure_keeps_the_authorization(world, job, folders, run_id, authorization)
    # The next automatic run takes the cutoff the retention period allows then.
    _due(world, monkeypatch)
    if months is None:
        _evaluate(job, folders, "no_retention_period")
        return
    created = _evaluate(job, folders, "created")
    zone = history_archive.site_zone()
    latest = history_archive.months_before(
        history_archive.site_date_of(history_archive.now(), zone), months
    )
    following = run_row(world.owner, cast(int, created.run_id))
    assert (following["state"], following["authorization_id"], following["cutoff"]) == (
        "APPROVED",
        authorization,
        history_archive.start_of_day(latest, zone),
    )


@pytest.mark.parametrize("stage", ["backup", "purge"])
def test_switching_automatic_runs_off_stops_a_run_created_without_approval(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    # Review B4, minor 3: such a run proceeds only while automatic runs are on.
    holder, authorization, run_id = _approved_without_approval(world, job, folders, monkeypatch)
    movements = _movements(world)
    backup: PurgeBackup | None = None
    if stage == "purge":
        done(history_archive_job.next_run(job, folders), "backup")
        backup = backup_of(world.owner, run_id)
    _ok(holder.put(_SETTINGS, json={"archive_automatic_runs": False}))
    _finishing_last(world, monkeypatch)
    _rechecked(job, folders, run_id, backup, stage)
    assert run_row(world.owner, run_id)["failure_reason"] == (
        f"Automatic runs were switched off after run {run_id} was created without approval, so"
        " its history was not purged. Nothing was deleted; a holder of Run history archival and"
        " purge may request a new run."
    )
    assert _movements(world) == movements
    _plain_failure_keeps_the_authorization(world, job, folders, run_id, authorization)
    # Switched on again: the same standing authorization starts the next run.
    _ok(holder.put(_SETTINGS, json={"archive_automatic_runs": True}))
    _plain_failure_keeps_the_authorization(world, job, folders, run_id, authorization)
    _due(world, monkeypatch)
    created = _evaluate(job, folders, "created")
    assert created.state == "APPROVED"
    assert run_row(world.owner, cast(int, created.run_id))["authorization_id"] == authorization


@pytest.mark.parametrize("kind", ["manual", "approved_proposal"])
def test_a_run_a_person_authorized_keeps_its_cutoff_and_ignores_automatic_runs_off(
    world: World,
    job: Engine,
    folders: JobFolders,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    if kind == "manual":
        run_id = request_run(world.builder, "Requested at the retention cutoff")
    else:
        run_id = _proposed(world, job, folders, monkeypatch)
        approver = _key_holder(world)
        _ok(approver.post(f"{_RUNS}/{run_id}/approve", json={"reason": "Reviewed the preview"}))
    copied_run(job, folders, run_id)
    # A longer retention period and automatic runs off: the person chose this cutoff.
    _ok(
        world.builder.admin.put(
            _SETTINGS, json={"retention_period_months": 30, "archive_automatic_runs": False}
        )
    )
    done(history_archive_job.next_run(job, folders), "backup")
    assert run_row(world.owner, run_id)["state"] == "RUNNING"


def test_the_approver_is_rechecked_before_the_backup(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _proposed(world, job, folders, monkeypatch)
    approver = _key_holder(world)
    _ok(approver.post(f"{_RUNS}/{run_id}/approve", json={"reason": "Approved, then left"}))
    copied_run(job, folders, run_id)
    _lose(world, approver, "key")
    _finishing_last(world, monkeypatch)
    report = history_archive_job.next_run(job, folders)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    run = run_row(world.owner, run_id)
    assert (run["state"], run["authorization_failed"]) == ("FAILED", True)
    assert run["failure_reason"].startswith(f"The User who approved run {run_id}, ")
    assert run["failure_reason"].endswith(
        ", no longer holds Run history archival and purge, so its history was not purged. Nothing"
        " was deleted; a holder of the key may request a new run."
    )
    findings = history_archive_job.status(job, folders).details["findings"]
    assert {"last_run_failed", "authorization_failed"} <= set(findings)
    assert "standing_authorization_invalid" not in findings


def test_status_reports_a_proposal_waiting_longer_than_the_setting(
    world: World, job: Engine, folders: JobFolders, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _proposed(world, job, folders, monkeypatch)
    status = history_archive_job.status(job, folders)
    assert status.details["open_run"]["state"] == "PROPOSED"
    assert "approval_overdue" not in status.details["findings"]
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7, minutes=5)
    monkeypatch.setattr(history_archive_job, "_now", lambda: later)
    status = history_archive_job.status(job, folders)
    assert status.details["open_run"]["id"] == run_id
    assert status.details["open_run"]["age_seconds"] > 7 * 86400
    assert "approval_overdue" in status.details["findings"]
    assert status.exit_code == history_archive_job.EXIT_REFUSED
    _policy(world, archive_max_wait_days=30)
    assert "approval_overdue" not in history_archive_job.status(job, folders).details["findings"]
