"""Tests of permanent deletion requests (Phase 16, history archival design D17; build step 3).

One module database, built through the real API (the builder of
``tests/archive_dataset.py``). The API and every reader run as the
application role in the application-role test mode, the host job's commands
as the maintenance role (as the owner otherwise), and the interim command
as the owner. In file order:

- the preview and the request through the API: what goes and every refusal
  (409 with the list, nothing recorded), the typed confirmation, the reason
  and the scope (422), the list and the cancellation;
- at most one open request, also when two requests race;
- the host job: ``settings`` (``deletion_pending``), ``next``
  (``start_deletion``, ``deletion_backup``), ``start-deletion`` and
  ``delete`` with the pre-purge backup's evidence — the stored rows are
  deleted, one DELETED audit row per Work Order and Part Number names the
  requester, every reconcile check is clean and the number is free again;
  a request waits while a run is between its steps (RUNNING);
- the row guards with real data: the job deletes only the stored rows of the
  RUNNING request, never another request's or a row outside the scope;
- a change between the request and its execution fails the request with
  the difference and deletes nothing; so does a refusal that appears;
- the requester's key at the start and at the deletion;
- the restore scenario and a transient error: ``recover`` fails a RUNNING
  request, and nothing is deleted;
- the backup's evidence, ``status``, and the job's command line.
"""

import datetime
import json
import os
import threading
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
from sqlalchemy.orm import Session

from alembic import command
from app import cli
from app.application import (
    deletion_requests,
    history_archive_job,
    permanent_deletion,
    reconciliation,
    system_status,
)
from app.application.errors import ConflictError
from app.application.history_archive_job import JobFolders, PurgeBackup
from app.core.config import get_settings
from app.domain.enums import Permission
from app.main import create_app
from tests.archive_dataset import (
    Builder,
    backdate,
    carry_out_request,
    deletion_backup_of,
    job_engine,
    request_run,
)
from tests.auth_harness import IdentityClient, admin_of, client_as, station_device_client
from tests.conftest import app_mode_roles, owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"
_KEY = Permission.DELETE_WORK_ORDERS_AND_PART_NUMBERS_PERMANENTLY
_PREVIEW = "/api/permanent-deletion/preview"
_REQUESTS = "/api/permanent-deletion/requests"
_NO_FOLDERS = JobFolders(None, None)
_CHANGED = "The data changed after the request; nothing was deleted. Request it again."


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _suffix() -> str:
    return uuid.uuid4().hex[:6].upper()


@dataclass
class World:
    builder: Builder
    url: URL
    owner: Engine
    job: Engine
    client: TestClient
    route_template: int


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    database = f"partflow_test_deletion_requests_{uuid.uuid4().hex[:8]}"
    cluster = make_url(os.environ[_DB_URL_ENV])
    admin = create_engine(cluster, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    url = cluster.set(database=database)
    original = os.environ[_DB_URL_ENV]
    owner = owner_engine(url)
    job = job_engine(url)
    try:
        command.upgrade(_alembic_config(url), "head")
        os.environ[_DB_URL_ENV] = url.render_as_string(hide_password=False)
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            builder = Builder(station_device_client(client), url)
            builder.environment()
            template = builder.route_template(["MAT", "DEBURR"])
            yield World(builder, url, owner, job, client, template)
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


def _numbered_work_order(
    builder: Builder, lines: list[tuple[str, int]], label: str = "DEL"
) -> tuple[str, int, list[int]]:
    number = f"{label}-{_suffix()}"
    response = builder.admin.post(
        "/api/work-orders",
        json={
            "work_order_number": number,
            "lines": [
                {"part_number": pn, "requested_quantity": quantity} for pn, quantity in lines
            ],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return number, int(body["id"]), [int(line["id"]) for line in body["demands"]]


@dataclass
class Scenario:
    """A released Work Order with a split, stock, an allocation and a planned route."""

    number: str
    work_order_id: int
    lines: list[int]
    part_numbers: list[str]
    flows: list[int]


def _scenario(world: World, label: str = "DEL") -> Scenario:
    builder = world.builder
    pn_a, pn_b = f"{label}-A-{_suffix()}", f"{label}-B-{_suffix()}"
    number, work_order_id, (line_a, line_b) = _numbered_work_order(
        builder, [(pn_a, 10), (pn_b, 4)], label
    )
    flow = builder.release_line(work_order_id, line_a, pn_a, 6)
    moved = builder.transfer("MAT", "DEBURR", flow, pn_a, 2)
    builder.stock("DEBURR", moved, pn_a, 2)
    builder.allocate(pn_a, line_a, 2)
    planned = builder.release_line(work_order_id, line_b, pn_b, 4, world.route_template)
    builder.transfer("MAT", "DEBURR", planned, pn_b, 4)
    return Scenario(number, work_order_id, [line_a, line_b], [pn_a, pn_b], [flow, moved, planned])


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _post_request(
    caller: IdentityClient,
    *,
    work_orders: list[str] | None = None,
    part_number: str | None = None,
    reason: str = "test data entered by mistake",
    confirmation: list[str] | None = None,
) -> Any:
    body: dict[str, Any] = {"reason": reason}
    if work_orders is not None:
        body["work_order_numbers"] = work_orders
    if part_number is not None:
        body["part_number"] = part_number
    body["confirmation"] = (
        confirmation
        if confirmation is not None
        else (work_orders if work_orders is not None else [part_number])
    )
    return caller.post(_REQUESTS, json=body)


def _request_row(owner: Engine, request_id: int) -> dict[str, Any]:
    with owner.connect() as connection:
        return dict(
            connection.execute(
                sa.text("SELECT * FROM deletion_requests WHERE id = :id"), {"id": request_id}
            )
            .mappings()
            .one()
        )


def _scalar(engine: Engine, sql: str, **params: object) -> Any:
    with engine.connect() as connection:
        return connection.execute(sa.text(sql), params).scalar()


def _column(engine: Engine, sql: str, **params: object) -> list[Any]:
    with engine.connect() as connection:
        return list(connection.execute(sa.text(sql), params).scalars())


_evidence = deletion_backup_of


def _done(report: history_archive_job.JobReport, result: str) -> None:
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result) == (history_archive_job.EXIT_DONE, result), document


def _carry_out(world: World, request_id: int) -> history_archive_job.JobReport:
    """``start-deletion``, the pre-purge backup's evidence, ``delete``."""
    return carry_out_request(world.job, world.owner, request_id)


def _fingerprint(engine: Engine) -> dict[str, str]:
    """The content of every table a deletion deletes from (but a request's own audit rows)."""
    result: dict[str, str] = {}
    with engine.connect() as connection:
        for table in permanent_deletion.DELETE_ORDER:
            where = " WHERE t.entity_type <> 'DeletionRequest'" if table == "audit_events" else ""
            result[table] = str(
                connection.execute(
                    sa.text(
                        f"SELECT md5(coalesce(string_agg(t::text, ',' ORDER BY t::text), ''))"
                        f" FROM {table} t{where}"
                    )
                ).scalar_one()
            )
    return result


def _audit_of(owner: Engine, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
    with owner.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                sa.text(
                    "SELECT event_type, actor_user_id, before_data, after_data, metadata"
                    " FROM audit_events WHERE entity_type = :type AND entity_id = :id ORDER BY id"
                ),
                {"type": entity_type, "id": entity_id},
            ).mappings()
        ]


def _clean(world: World) -> None:
    report = reconciliation.run_reconciliation(world.builder.engine)
    assert reconciliation.result_of(report) == ("clean", 0), reconciliation.report_document(report)


def _no_open_request(world: World) -> None:
    """Cancel an open request a failed test may have left (keeps the next tests independent)."""
    with world.owner.connect() as connection:
        open_ids = list(
            connection.execute(
                sa.text("SELECT id FROM deletion_requests WHERE state IN ('APPROVED', 'RUNNING')")
            ).scalars()
        )
    for request_id in open_ids:
        if _request_row(world.owner, request_id)["state"] == "RUNNING":
            history_archive_job.recover(world.job, JobFolders(Path("/tmp"), None))
        else:
            _ok(admin_of(world.client).post(f"{_REQUESTS}/{request_id}/cancel"))


@pytest.fixture
def clean_slate(world: World) -> Iterator[World]:
    _no_open_request(world)
    yield world
    _no_open_request(world)


# ---------------------------------------------------------------------------
# The preview and the request through the API
# ---------------------------------------------------------------------------


def test_the_preview_shows_what_goes_and_writes_nothing(clean_slate: World) -> None:
    world = clean_slate
    scenario = _scenario(world, "PREVIEW")
    holder = client_as(world.client, _KEY)
    before = _fingerprint(world.owner)
    requests_before = _scalar(world.owner, "SELECT count(*) FROM deletion_requests")
    body = _ok(holder.get(_PREVIEW, params={"work_order_number": scenario.number}))
    assert body["scope"] == {"work_order_numbers": [scenario.number], "part_numbers": []}
    assert body["refusals"] == [] and body["open_request"] is None
    [work_order] = body["work_orders"]
    assert (work_order["id"], work_order["work_order_number"], work_order["named"]) == (
        scenario.work_order_id,
        scenario.number,
        True,
    )
    rows = body["rows"]
    assert set(rows) == set(permanent_deletion.DELETE_ORDER)
    assert (rows["work_orders"], rows["work_order_demands"], rows["assigned_routes"]) == (1, 2, 1)
    assert rows["work_order_allocations"] == 1 and rows["quantity_flow_lineage"] >= 1
    assert rows["part_numbers"] == 0
    assert _fingerprint(world.owner) == before
    assert _scalar(world.owner, "SELECT count(*) FROM deletion_requests") == requests_before
    # The key is needed; nothing else opens the preview.
    lacking = client_as(world.client, Permission.RUN_HISTORY_ARCHIVAL_AND_PURGE)
    assert lacking.get(_PREVIEW, params={"work_order_number": scenario.number}).status_code == 403


def test_a_refused_deletion_is_never_requested(clean_slate: World) -> None:
    world = clean_slate
    builder = world.builder
    pn = f"MERGE-{_suffix()}"
    number, work_order_id, (line,) = _numbered_work_order(builder, [(pn, 3)], "REFUSE")
    other, other_id, (other_line,) = _numbered_work_order(builder, [(pn, 2)], "STAY")
    mine = builder.release_line(work_order_id, line, pn, 3)
    theirs = builder.release_line(other_id, other_line, pn, 2)
    builder.merge(pn, [mine, theirs])
    holder = client_as(world.client, _KEY)
    preview = _ok(holder.get(_PREVIEW, params={"work_order_number": number}))
    assert preview["rows"] is None and preview["kept_audit_rows"] == []
    assert "lineage_link" in [refusal["code"] for refusal in preview["refusals"]]
    before = _fingerprint(world.owner)
    audit_count = _scalar(world.owner, "SELECT count(*) FROM audit_events")
    response = _post_request(holder, work_orders=[number])
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["deletion_refused"] is True
    assert body["detail"] == (
        "This deletion is refused, so nothing was requested: the data to delete is linked to"
        " data that stays, or a record does not exist. The refusals name each link."
    )
    assert body["refusals"] == preview["refusals"]
    assert repr(other) in " ".join(refusal["message"] for refusal in body["refusals"])
    assert _fingerprint(world.owner) == before
    assert _scalar(world.owner, "SELECT count(*) FROM audit_events") == audit_count
    assert (
        _scalar(world.owner, "SELECT count(*) FROM deletion_requests WHERE state = 'APPROVED'") == 0
    )
    # An unknown Work Order is a refusal too.
    unknown = _post_request(holder, work_orders=[f"NO-SUCH-{_suffix()}"])
    assert unknown.status_code == 409
    assert [refusal["code"] for refusal in unknown.json()["refusals"]] == ["unknown_work_order"]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {"reason": "x", "confirmation": []},
            "Name what to delete: one or more Work Order Numbers, or one Part Number, not both.",
        ),
        (
            {"work_order_numbers": ["A"], "part_number": "B", "reason": "x", "confirmation": ["A"]},
            "Name what to delete: one or more Work Order Numbers, or one Part Number, not both.",
        ),
        (
            {"work_order_numbers": ["  "], "reason": "x", "confirmation": ["  "]},
            "A Work Order Number must not be blank or contain a NUL character.",
        ),
        (
            {"work_order_numbers": [f"W{n}" for n in range(51)], "reason": "x", "confirmation": []},
            "Name at most 50 Work Orders in one deletion request.",
        ),
        (
            {"work_order_numbers": ["W-1"], "reason": " ", "confirmation": ["W-1"]},
            "Enter the reason for this deletion: 1 to 500 characters of plain text.",
        ),
        (
            {"work_order_numbers": ["W-1", "W-2"], "reason": "x", "confirmation": ["W-1"]},
            "Type the Work Order Number of every Work Order to delete, exactly as PartFlow shows"
            " it, to confirm the deletion. Nothing was requested.",
        ),
        (
            {"work_order_numbers": ["W-1"], "reason": "x", "confirmation": ["w-1"]},
            "Type the Work Order Number of every Work Order to delete, exactly as PartFlow shows"
            " it, to confirm the deletion. Nothing was requested.",
        ),
        (
            {"part_number": "PN-1", "reason": "x", "confirmation": ["PN-2"]},
            "Type the Part Number to delete again to confirm the deletion. Nothing was requested.",
        ),
        (
            {"part_number": "PN-1", "reason": "x", "confirmation": ["PN 1"]},
            "Type the Part Number to delete again to confirm the deletion. Nothing was requested.",
        ),
    ],
)
def test_the_request_checks_its_input(world: World, body: dict[str, Any], detail: str) -> None:
    holder = client_as(world.client, _KEY)
    count = _scalar(world.owner, "SELECT count(*) FROM deletion_requests")
    response = holder.post(_REQUESTS, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == detail
    assert _scalar(world.owner, "SELECT count(*) FROM deletion_requests") == count


def test_the_request_schema_is_strict(world: World) -> None:
    holder = client_as(world.client, _KEY)
    for body in (
        {"work_order_numbers": ["W"], "reason": "x", "confirmation": ["W"], "extra": 1},
        {"work_order_numbers": ["W"], "reason": "x"},
        {"work_order_numbers": ["W"], "reason": "x", "confirmation": "W"},
    ):
        response = holder.post(_REQUESTS, json=body)
        assert response.status_code == 422, body
        assert isinstance(response.json()["detail"], list), body
    assert holder.get(_PREVIEW).status_code == 422
    assert holder.get(_REQUESTS, params={"limit": 0}).status_code == 422


def test_a_request_is_recorded_listed_and_cancelled(clean_slate: World) -> None:
    world = clean_slate
    scenario = _scenario(world, "CANCEL")
    holder = client_as(world.client, _KEY)
    created = _ok(_post_request(holder, work_orders=[scenario.number]), 201)
    assert created["state"] == "APPROVED"
    assert created["scope"] == {"work_order_numbers": [scenario.number], "part_numbers": []}
    assert created["requested_by"]["user_id"] == holder.user_id
    assert created["requested_by_operator"] is None
    assert created["rows"]["work_orders"] == 1
    assert [entry["id"] for entry in created["work_orders"]] == [scenario.work_order_id]
    assert (created["started_at"], created["pre_purge_backup"], created["finished_at"]) == (
        None,
        None,
        None,
    )
    request_id = int(created["id"])
    # Its exact rows are stored, one per row its plan deletes.
    stored = _scalar(
        world.owner,
        "SELECT count(*) FROM deletion_request_rows WHERE request_id = :id",
        id=request_id,
    )
    assert stored == sum(created["rows"].values())
    [audit] = _audit_of(world.owner, "DeletionRequest", str(request_id))
    assert (audit["event_type"], audit["actor_user_id"]) == ("CREATED", holder.user_id)
    assert audit["metadata"] == {"source": "web", "command": "request"}
    # The preview names the open request; a second request is refused.
    preview = _ok(holder.get(_PREVIEW, params={"work_order_number": scenario.number}))
    assert preview["open_request"]["id"] == request_id
    other = _scenario(world, "SECOND")
    refused = _post_request(holder, work_orders=[other.number])
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == (
        f"Deletion request {request_id} is still open (APPROVED). Cancel it or wait until it"
        " finishes before requesting another deletion."
    )
    listed = _ok(holder.get(_REQUESTS))
    assert listed[0]["id"] == request_id
    # Cancel: CANCELLED with the canceller; again: unchanged; unknown: 404.
    canceller = client_as(world.client, _KEY)
    cancelled = _ok(canceller.post(f"{_REQUESTS}/{request_id}/cancel"))
    assert cancelled["state"] == "CANCELLED"
    assert cancelled["cancelled_by"]["user_id"] == canceller.user_id
    assert cancelled["finished_at"] is not None
    assert _ok(holder.post(f"{_REQUESTS}/{request_id}/cancel"))["state"] == "CANCELLED"
    missing = holder.post(f"{_REQUESTS}/999999/cancel")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "There is no deletion request 999999."
    audits = _audit_of(world.owner, "DeletionRequest", str(request_id))
    assert [row["event_type"] for row in audits] == ["CREATED", "UPDATED"]
    assert audits[1]["actor_user_id"] == canceller.user_id
    assert audits[1]["metadata"] == {"source": "web", "command": "cancel"}
    # The data stays.
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM work_orders WHERE id = :id",
            id=scenario.work_order_id,
        )
        == 1
    )


def test_at_most_one_request_is_open_even_when_two_race(
    clean_slate: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both look before either records (a barrier); the unique index decides."""
    world = clean_slate
    first, second = _scenario(world, "RACE1"), _scenario(world, "RACE2")
    user = client_as(world.client, _KEY).user_id
    original = permanent_deletion.open_request
    barrier = threading.Barrier(2, timeout=30)
    waited: set[int] = set()

    def look(connection: Any) -> Any:
        found = original(connection)
        if threading.get_ident() not in waited:
            waited.add(threading.get_ident())
            barrier.wait()
        return found

    monkeypatch.setattr(permanent_deletion, "open_request", look)
    outcomes: dict[str, Any] = {}

    def request(scenario: Scenario) -> None:
        with Session(world.builder.engine) as session:
            try:
                outcomes[scenario.number] = deletion_requests.request_deletion(
                    session,
                    work_order_numbers=[scenario.number],
                    part_number=None,
                    reason="racing",
                    confirmation=[scenario.number],
                    actor_user_id=user,
                ).id
            except ConflictError as conflict:
                outcomes[scenario.number] = conflict.message

    threads = [threading.Thread(target=request, args=(s,)) for s in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    monkeypatch.setattr(permanent_deletion, "open_request", original)
    winners = [value for value in outcomes.values() if isinstance(value, int)]
    losers = [value for value in outcomes.values() if isinstance(value, str)]
    assert len(winners) == 1 and len(losers) == 1, outcomes
    assert losers[0] == (
        f"Deletion request {winners[0]} is still open (APPROVED). Cancel it or wait until it"
        " finishes before requesting another deletion."
    )
    assert (
        _column(
            world.owner, "SELECT id FROM deletion_requests WHERE state IN ('APPROVED', 'RUNNING')"
        )
        == winners
    )
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM audit_events WHERE entity_type = 'DeletionRequest'"
            " AND event_type = 'CREATED' AND entity_id = :id",
            id=str(winners[0]),
        )
        == 1
    )


# ---------------------------------------------------------------------------
# The host job carries a request out
# ---------------------------------------------------------------------------


def test_the_job_carries_a_request_out(clean_slate: World) -> None:
    world = clean_slate
    scenario = _scenario(world, "JOB")
    # A Work Order that stays, with the same Part Number and its own stock.
    stay_number, stay_id, (stay_line,) = _numbered_work_order(
        world.builder, [(scenario.part_numbers[0], 3)], "STAY"
    )
    stay_flow = world.builder.release_line(stay_id, stay_line, scenario.part_numbers[0], 3)
    world.builder.stock("MAT", stay_flow, scenario.part_numbers[0], 3)
    world.builder.allocate(scenario.part_numbers[0], stay_line, 3)
    _clean(world)
    holder = client_as(world.client, _KEY)
    created = _ok(_post_request(holder, work_orders=[scenario.number]), 201)
    request_id = int(created["id"])
    # The cheap check of every start, then the actions of next.
    settings = history_archive_job.settings(world.job, _NO_FOLDERS)
    _done(settings, "read")
    assert settings.details["deletion_pending"] is True
    assert settings.details["deletion_request"]["id"] == request_id
    nxt = history_archive_job.next_run(world.job, _NO_FOLDERS)
    _done(nxt, "start_deletion")
    assert (nxt.request_id, nxt.state, nxt.details["run"]) == (request_id, "APPROVED", None)
    started = history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id)
    _done(started, "started")
    request = _request_row(world.owner, request_id)
    assert request["state"] == "RUNNING"
    assert request["started_at"] == request["started_at"].replace(microsecond=0)
    stamp = request["started_at"].astimezone(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    assert request["backup_label"] == f"deletion-{request_id}-{stamp}"
    assert started.details["backup"] == {"kind": "pre-purge", "label": request["backup_label"]}
    nxt = history_archive_job.next_run(world.job, _NO_FOLDERS)
    _done(nxt, "deletion_backup")
    assert nxt.details["backup"] == {"kind": "pre-purge", "label": request["backup_label"]}
    evidence = _evidence(world.owner, request_id)
    report = history_archive_job.delete_request(world.job, _NO_FOLDERS, request_id, evidence)
    _done(report, "deleted")
    assert report.state == "COMPLETED"
    assert report.details["deleted"] == created["rows"]
    request = _request_row(world.owner, request_id)
    assert request["state"] == "COMPLETED"
    assert (request["pre_purge_backup_name"], request["finished_at"] is not None) == (
        evidence.name,
        True,
    )
    # Every stored row is gone; what stays is untouched.
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM work_orders WHERE id = :id",
            id=scenario.work_order_id,
        )
        == 0
    )
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM quantity_flows WHERE id = ANY(:ids)",
            ids=scenario.flows,
        )
        == 0
    )
    assert (
        _scalar(world.owner, "SELECT count(*) FROM quantity_flows WHERE id = :id", id=stay_flow)
        == 1
    )
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM audit_events WHERE entity_type = 'WorkOrderDemand'"
            " AND entity_id = ANY(:ids)",
            ids=[str(line) for line in scenario.lines],
        )
        == 0
    )
    # Exactly one DELETED audit row for the Work Order, the requester its actor.
    [deleted] = _audit_of(world.owner, "WorkOrder", str(scenario.work_order_id))
    assert (deleted["event_type"], deleted["actor_user_id"]) == ("DELETED", holder.user_id)
    assert deleted["before_data"]["work_order_number"] == scenario.number
    record = deleted["metadata"][permanent_deletion.AUDIT_KEY]
    assert record["request_id"] == request_id
    assert (record["source"], record["operator"], record["backup"]) == ("web", None, evidence.name)
    assert record["reason"] == "test data entered by mistake"
    assert record["work_orders"] == [scenario.number]
    assert record["deleted"] == created["rows"]
    assert report.details["audit_rows"] == [
        {
            "id": report.details["audit_rows"][0]["id"],
            "entity_type": "WorkOrder",
            "entity_id": str(scenario.work_order_id),
        }
    ]
    # Every change of state is audited by the job without an actor.
    audits = _audit_of(world.owner, "DeletionRequest", str(request_id))
    assert [(row["event_type"], row["metadata"]["command"]) for row in audits] == [
        ("CREATED", "request"),
        ("UPDATED", "start-deletion"),
        ("UPDATED", "delete"),
    ]
    assert [row["actor_user_id"] for row in audits[1:]] == [None, None]
    _clean(world)
    # The Work Order Number is free again; nothing is pending any more.
    response = world.builder.admin.post(
        "/api/work-orders",
        json={
            "work_order_number": scenario.number,
            "lines": [{"part_number": f"AGAIN-{_suffix()}", "requested_quantity": 1}],
        },
    )
    assert response.status_code == 201, response.text
    settings = history_archive_job.settings(world.job, _NO_FOLDERS)
    assert settings.details["deletion_pending"] is False
    listed = _ok(holder.get(_REQUESTS))
    assert listed[0]["id"] == request_id and listed[0]["state"] == "COMPLETED"
    assert listed[0]["pre_purge_backup"]["name"] == evidence.name
    # Cancelling a finished request is refused.
    finished = holder.post(f"{_REQUESTS}/{request_id}/cancel")
    assert finished.status_code == 409
    assert finished.json()["detail"] == (
        f"Deletion request {request_id} is finished (COMPLETED) and cannot be cancelled."
    )


def test_a_request_waits_for_the_steps_of_a_running_run(clean_slate: World, tmp_path: Path) -> None:
    """``next`` never starts a deletion between the steps of a RUNNING run: those
    steps belong to the same start, and the next start's ``recover`` would fail
    a run left RUNNING. Once the run waits (or ends), the request comes first."""
    world = clean_slate
    builder = world.builder
    pn = f"OLD-{_suffix()}"
    flow = builder.release(pn, 2)
    builder.stock("MAT", flow, pn, 2)
    backdate(world.owner, builder.component(flow))
    run_id = request_run(builder, "Archive before a deletion")
    archive = tmp_path / "archive"
    archive.mkdir()
    folders = JobFolders(archive, None)
    _done(history_archive_job.export(world.job, folders, run_id), "exported")
    number, _work_order_id, _lines = _numbered_work_order(builder, [(f"WAIT-{_suffix()}", 1)])
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[number]), 201)["id"])
    for step, result in (
        ("verify", "verified"),
        ("copy", "waiting_for_copy"),
    ):
        nxt = history_archive_job.next_run(world.job, folders)
        _done(nxt, step)
        assert (nxt.run_id, nxt.request_id, nxt.state) == (run_id, None, "RUNNING")
        assert nxt.details["request"]["id"] == request_id
        _done(getattr(history_archive_job, step)(world.job, folders, run_id), result)
    # The run waits for its copy now: the request comes first.
    nxt = history_archive_job.next_run(world.job, folders)
    _done(nxt, "start_deletion")
    assert (nxt.request_id, nxt.details["run"]) == (request_id, None)
    _ok(holder.post(f"{_REQUESTS}/{request_id}/cancel"))
    _ok(builder.admin.post(f"/api/history-archive/runs/{run_id}/cancel"))
    _clean(world)


def test_a_part_number_request_deletes_its_master_and_internal_work_order(
    clean_slate: World,
) -> None:
    world = clean_slate
    builder = world.builder
    pn = f"PNDEL-{_suffix()}"
    _ok(builder.admin.post("/api/part-numbers", json={"part_number": pn, "name": "test"}), 201)
    response = builder.client.post(
        f"/api/scan-stations/{builder.stations['MAT']}/receipts",
        json={
            "part_number": pn,
            "quantity": 3,
            "request_type": "MODIFY",
            "route_mode": "FLOATING",
            "operation_id": builder.operations["MAT"],
            "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "confirm_active_quantity": True,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    received = int(_ok(response, 201)["quantity_flow_id"])
    added = builder.add_quantity(pn, 2)
    builder.merge(pn, [received, added])
    internal_id = int(
        _scalar(
            world.owner,
            "SELECT w.id FROM work_orders w JOIN work_order_demands d ON d.work_order_id = w.id"
            " WHERE d.part_number = :pn AND w.work_order_number IS NULL",
            pn=pn,
        )
    )
    holder = client_as(world.client, _KEY)
    preview = _ok(holder.get(_PREVIEW, params={"part_number": pn.lower()}))
    assert preview["scope"] == {"work_order_numbers": [], "part_numbers": [pn]}
    assert preview["part_numbers"] == [{"part_number": pn, "master": True}]
    [internal] = preview["work_orders"]
    assert (internal["id"], internal["internal"], internal["named"]) == (internal_id, True, False)
    created = _ok(_post_request(holder, part_number=pn, confirmation=[pn.lower()]), 201)
    assert created["rows"]["part_numbers"] == 1
    report = _carry_out(world, int(created["id"]))
    _done(report, "deleted")
    for table in ("part_numbers", "quantity_flows", "work_order_demands"):
        assert (
            _scalar(world.owner, f"SELECT count(*) FROM {table} WHERE part_number = :pn", pn=pn)
            == 0
        )
    pn_rows = _audit_of(world.owner, "PartNumber", pn)
    assert [row["event_type"] for row in pn_rows] == ["DELETED"]
    assert pn_rows[0]["before_data"]["name"] == "test"
    assert pn_rows[0]["actor_user_id"] == holder.user_id
    assert [row["event_type"] for row in _audit_of(world.owner, "WorkOrder", str(internal_id))] == [
        "DELETED"
    ]
    _clean(world)


def _receipt(builder: Builder, pn: str) -> None:
    """A Scan Station receipt: a flow on a new internal Work Order of ``pn``."""
    response = builder.client.post(
        f"/api/scan-stations/{builder.stations['MAT']}/receipts",
        json={
            "part_number": pn,
            "quantity": 2,
            "request_type": "MODIFY",
            "route_mode": "FLOATING",
            "operation_id": builder.operations["MAT"],
            "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "confirm_active_quantity": True,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    _ok(response, 201)


def test_a_later_deletion_keeps_the_record_of_an_earlier_one(clean_slate: World) -> None:
    """Review finding 4: the DELETED row of a Part Number deleted earlier — its
    canonical PN recurs once the Part Number is entered again — and the audit rows
    of every deletion request are never planned, deleted or listed as kept.

    A Work Order Number used again cannot reach them: no audit key names a Work
    Order by its number, and Work Order, demand line, flow and route ids are never
    reused.
    """
    world = clean_slate
    builder = world.builder
    holder = client_as(world.client, _KEY)
    pn = f"AGAIN-{_suffix()}"
    _ok(builder.admin.post("/api/part-numbers", json={"part_number": pn, "name": "first"}), 201)
    _receipt(builder, pn)
    first = int(_ok(_post_request(holder, part_number=pn), 201)["id"])
    _done(_carry_out(world, first), "deleted")
    records = _column(
        world.owner,
        "SELECT id FROM audit_events WHERE (entity_type = 'PartNumber' AND entity_id = :pn"
        " AND event_type = 'DELETED') OR (entity_type = 'DeletionRequest' AND entity_id = :id)"
        " ORDER BY id",
        pn=pn,
        id=str(first),
    )
    # The DELETED row of the Part Number, and CREATED, start-deletion and delete of the request.
    assert len(records) == 4
    # The Part Number is entered again: its details, and a receipt on a new internal Work Order.
    _ok(builder.admin.post("/api/part-numbers", json={"part_number": pn, "name": "second"}), 201)
    _receipt(builder, pn)
    preview = _ok(holder.get(_PREVIEW, params={"part_number": pn}))
    assert preview["refusals"] == []
    assert not {int(row["id"]) for row in preview["kept_audit_rows"]} & set(records)
    assert "DeletionRequest" not in {row["entity_type"] for row in preview["kept_audit_rows"]}
    second = int(_ok(_post_request(holder, part_number=pn), 201)["id"])
    planned = _column(
        world.owner,
        "SELECT row_key FROM deletion_request_rows WHERE request_id = :id"
        " AND table_name = 'audit_events'",
        id=second,
    )
    assert planned and not {int(key) for key in planned} & set(records)
    _done(_carry_out(world, second), "deleted")
    # Both deletions are on record: the first DELETED row and its request's rows stayed.
    assert set(records) <= set(_column(world.owner, "SELECT id FROM audit_events"))
    rows = _audit_of(world.owner, "PartNumber", pn)
    assert [row["event_type"] for row in rows] == ["DELETED", "DELETED"]
    assert [row["before_data"]["name"] for row in rows] == ["first", "second"]
    assert [row["metadata"][permanent_deletion.AUDIT_KEY]["request_id"] for row in rows] == [
        first,
        second,
    ]
    _clean(world)


def test_the_cheap_check_ends_when_the_database_does_not_answer(
    clean_slate: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``settings`` runs at every 5-minute start: a locked table ends it as could
    not run (exit 2) within the lock timeout of ``status``, never waiting on."""
    world = clean_slate
    monkeypatch.setattr(system_status, "LOCK_TIMEOUT_SECONDS", 1)
    with world.owner.connect() as locker:
        locker.execute(sa.text("LOCK TABLE deletion_requests IN ACCESS EXCLUSIVE MODE"))
        report = history_archive_job.settings(world.job, _NO_FOLDERS)
        locker.rollback()
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result) == (
        history_archive_job.EXIT_COULD_NOT_RUN,
        "could_not_run",
    ), document
    assert document["error"] == {
        "code": "database_unavailable",
        "message": (
            "PartFlow's database did not answer the settings read in time (statement timeout"
            f" {system_status.DEFAULT_STATEMENT_TIMEOUT_SECONDS} s, lock timeout 1 s): it is"
            " busy, locked or hung. Nothing was changed."
        ),
    }
    # Unlocked, the same check reads.
    _done(history_archive_job.settings(world.job, _NO_FOLDERS), "read")


def test_the_job_command_line_has_the_connect_timeout_of_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: dict[str, object] = {}

    def build_engine(database_url: str, **options: object) -> None:
        built.update(options)

    monkeypatch.setattr(cli, "build_engine", build_engine)
    cli._history_archive_engine()
    assert built["connect_timeout"] == system_status.CONNECT_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# The row guards with real data
# ---------------------------------------------------------------------------


def _attempt(world: World, statements: list[tuple[str, dict[str, Any]]]) -> str | None:
    """SQLSTATE of the first refused statement as the job's role (None: all ran); rolled back."""
    with world.job.connect() as connection:
        transaction = connection.begin()
        try:
            for sql, params in statements:
                connection.execute(sa.text(sql), params)
            return None
        except sa.exc.DBAPIError as exc:
            return str(getattr(exc.orig, "sqlstate", None))
        finally:
            transaction.rollback()


def _marked(request_id: int) -> tuple[str, dict[str, Any]]:
    return (
        "SELECT set_config('partflow.deletion_request_id', :id, true)",
        {"id": str(request_id)},
    )


def _stored(owner: Engine, request_id: int) -> dict[str, str]:
    """One stored row per table of the request: table → its key as text."""
    with owner.connect() as connection:
        return {
            str(row.table_name): str(row.row_key)
            for row in connection.execute(
                sa.text(
                    "SELECT DISTINCT ON (table_name) table_name, row_key FROM deletion_request_rows"
                    " WHERE request_id = :id ORDER BY table_name, row_key"
                ),
                {"id": request_id},
            )
        }


def _delete(table: str, key: str) -> tuple[str, dict[str, Any]]:
    if table == "part_numbers":
        return ("DELETE FROM part_numbers WHERE part_number = :key", {"key": key})
    return (f"DELETE FROM {table} WHERE id = :key", {"key": int(key)})


#: Admitted by the row guard: it ran, or a foreign key objected afterwards (a
#: parent deleted alone, before its children, as the job never does).
_ADMITTED = (None, "23503")
#: Tables whose earlier deletes stay allowed without the marker to every role but
#: the maintenance role (the application's own deletes).
_APPLICATION_DELETES = ("assigned_route_steps", "work_order_demands", "part_numbers")


def test_the_job_deletes_only_the_stored_rows_of_the_running_request(clean_slate: World) -> None:
    world = clean_slate
    mine, other = _scenario(world, "GUARD1"), _scenario(world, "GUARD2")
    holder = client_as(world.client, _KEY)
    # A finished request that stored the other Work Order's rows.
    previous = int(_ok(_post_request(holder, work_orders=[other.number]), 201)["id"])
    _ok(holder.post(f"{_REQUESTS}/{previous}/cancel"))
    request_id = int(_ok(_post_request(holder, work_orders=[mine.number]), 201)["id"])
    rows = _stored(world.owner, request_id)
    others = _stored(world.owner, previous)
    assert set(rows) == set(permanent_deletion.DELETE_ORDER) - {"part_numbers"}
    # APPROVED: not yet RUNNING, so nothing may be deleted with its marker.
    assert _attempt(world, [_marked(request_id), _delete("work_orders", rows["work_orders"])]) == (
        "P0001"
    )
    _done(history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id), "started")
    maintenance = app_mode_roles() is not None
    for table, key in rows.items():
        delete = _delete(table, key)
        # Without the marker: refused for the maintenance role on every table; the
        # owner (the job's role outside the app-role mode) keeps the application's
        # own deletes.
        unmarked = _attempt(world, [delete])
        if table in _APPLICATION_DELETES and not maintenance:
            assert unmarked in _ADMITTED, table
        else:
            assert unmarked == "P0001", table
        # Another (finished) request's marker, an unknown one: refused.
        assert _attempt(world, [_marked(previous), delete]) == "P0001", table
        assert _attempt(world, [_marked(999999), delete]) == "P0001", table
        # A row of the other request, outside this one's scope: refused.
        assert _attempt(world, [_marked(request_id), _delete(table, others[table])]) == "P0001"
        # Its own marker and its own stored row: admitted by the guard.
        assert _attempt(world, [_marked(request_id), delete]) in _ADMITTED, table
    report = history_archive_job.delete_request(
        world.job, _NO_FOLDERS, request_id, _evidence(world.owner, request_id)
    )
    _done(report, "deleted")
    # Finished: its marker admits nothing any more (the other Work Order stays).
    assert (
        _attempt(world, [_marked(request_id), _delete("work_orders", others["work_orders"])])
        == "P0001"
    )
    assert (
        _scalar(
            world.owner, "SELECT count(*) FROM work_orders WHERE id = :id", id=other.work_order_id
        )
        == 1
    )


# ---------------------------------------------------------------------------
# A change between the request and its execution
# ---------------------------------------------------------------------------


def test_a_change_after_the_request_fails_it_and_deletes_nothing(clean_slate: World) -> None:
    world = clean_slate
    scenario = _scenario(world, "CHANGE")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    # A partial transfer of a flow of the request, recorded after it.
    remaining = world.builder.active_of(world.builder.component(scenario.flows[0]))
    world.builder.transfer("MAT", "DEBURR", remaining, scenario.part_numbers[0], 1)
    before = _fingerprint(world.owner)
    report = _carry_out(world, request_id)
    document = history_archive_job.job_document(report)
    assert (report.exit_code, report.result, report.state) == (
        history_archive_job.EXIT_RUN_FAILED,
        "failed",
        "FAILED",
    ), document
    request = _request_row(world.owner, request_id)
    assert request["state"] == "FAILED"
    assert request["failure_reason"].startswith(_CHANGED)
    assert "Changed rows: " in request["failure_reason"]
    assert "part_movements +" in request["failure_reason"]
    # The backup it was given is recorded with the failure.
    assert request["pre_purge_backup_name"] == _evidence(world.owner, request_id).name
    after = _fingerprint(world.owner)
    assert after == before


def test_a_refusal_that_appears_after_the_request_fails_it(clean_slate: World) -> None:
    world = clean_slate
    builder = world.builder
    pn = f"LATER-{_suffix()}"
    number, work_order_id, (line,) = _numbered_work_order(builder, [(pn, 3)], "LATER")
    _, other_id, (other_line,) = _numbered_work_order(builder, [(pn, 2)], "STAYS")
    mine = builder.release_line(work_order_id, line, pn, 3)
    theirs = builder.release_line(other_id, other_line, pn, 2)
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[number]), 201)["id"])
    builder.merge(pn, [mine, theirs])
    report = _carry_out(world, request_id)
    assert report.exit_code == history_archive_job.EXIT_RUN_FAILED
    reason = _request_row(world.owner, request_id)["failure_reason"]
    assert reason.startswith(f"{_CHANGED} It would now be refused: ")
    assert (
        _scalar(world.owner, "SELECT count(*) FROM work_orders WHERE id = :id", id=work_order_id)
        == 1
    )


# ---------------------------------------------------------------------------
# The requester's key
# ---------------------------------------------------------------------------


def _revoke(world: World, role_id: int) -> None:
    with world.owner.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :role AND permission = :key"),
            {"role": role_id, "key": _KEY.value},
        )


def test_the_requester_must_still_hold_the_key(clean_slate: World) -> None:
    world = clean_slate
    # At the start: the request fails before anything is backed up.
    scenario = _scenario(world, "KEY1")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    role_id = int(
        _scalar(world.owner, "SELECT role_id FROM users WHERE id = :id", id=holder.user_id)
    )
    _revoke(world, role_id)
    report = history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id)
    assert (report.exit_code, report.result, report.state) == (
        history_archive_job.EXIT_RUN_FAILED,
        "failed",
        "FAILED",
    )
    reason = _request_row(world.owner, request_id)["failure_reason"]
    assert reason.endswith(
        f"(user {holder.user_id}), no longer holds Delete Work Orders and Part Numbers"
        " permanently, so nothing was deleted. A holder of the key may request the deletion"
        " again."
    )
    assert _request_row(world.owner, request_id)["started_at"] is None
    # At the deletion: started, then the key is gone before the backup ends.
    scenario = _scenario(world, "KEY2")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    _done(history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id), "started")
    role_id = int(
        _scalar(world.owner, "SELECT role_id FROM users WHERE id = :id", id=holder.user_id)
    )
    _revoke(world, role_id)
    evidence = _evidence(world.owner, request_id)
    report = history_archive_job.delete_request(world.job, _NO_FOLDERS, request_id, evidence)
    assert (report.exit_code, report.state) == (history_archive_job.EXIT_RUN_FAILED, "FAILED")
    request = _request_row(world.owner, request_id)
    assert request["pre_purge_backup_name"] == evidence.name
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM work_orders WHERE id = :id",
            id=scenario.work_order_id,
        )
        == 1
    )


# ---------------------------------------------------------------------------
# The restore scenario, a transient error, the backup's evidence
# ---------------------------------------------------------------------------


def test_recover_fails_a_running_request_as_after_a_restore(
    clean_slate: World, tmp_path: Path
) -> None:
    """The pre-purge backup is taken while the request is RUNNING: a restore of it
    holds a RUNNING request, which the next start fails — nothing is deleted again."""
    world = clean_slate
    scenario = _scenario(world, "RESTORE")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    _done(history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id), "started")
    before = _fingerprint(world.owner)
    # (Here the backup's dump starts, the job stops, and the database is restored from it.)
    report = history_archive_job.recover(world.job, JobFolders(tmp_path, None))
    _done(report, "recovered")
    assert report.details["failed_request_ids"] == [request_id]
    request = _request_row(world.owner, request_id)
    assert request["state"] == "FAILED"
    assert request["failure_reason"] == (
        "The host job stopped while the deletion request was RUNNING (found by the next start of"
        " the job, also after a restore of its pre-purge backup). Nothing was deleted: the"
        " deletion is one transaction. Request the deletion again."
    )
    assert _fingerprint(world.owner) == before
    # A FAILED request is never carried out.
    refused = history_archive_job.delete_request(
        world.job, _NO_FOLDERS, request_id, _evidence(world.owner, request_id)
    )
    assert (refused.exit_code, refused.error and refused.error.code) == (1, "wrong_state")
    _done(history_archive_job.next_run(world.job, _NO_FOLDERS), "none")


def _error(kind: type[sa.exc.DBAPIError], original: Exception) -> sa.exc.DBAPIError:
    return kind("SELECT 1", {}, original)


def test_a_transient_error_leaves_the_request_running_until_the_next_start(
    clean_slate: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = clean_slate
    scenario = _scenario(world, "DEADLOCK")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    _done(history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id), "started")

    def deadlocked(*args: Any, **kwargs: Any) -> Any:
        raise _error(sa.exc.OperationalError, psycopg.errors.DeadlockDetected("deadlock"))

    monkeypatch.setattr(permanent_deletion, "carry_out", deadlocked)
    report = history_archive_job.delete_request(
        world.job, _NO_FOLDERS, request_id, _evidence(world.owner, request_id)
    )
    monkeypatch.undo()
    assert (report.exit_code, report.result, report.state) == (2, "interrupted", "RUNNING")
    assert report.error is not None and report.error.code == "deletion_interrupted"
    assert _request_row(world.owner, request_id)["state"] == "RUNNING"
    # The next start fails it (it cannot be told from a restored one).
    _done(history_archive_job.recover(world.job, JobFolders(tmp_path, None)), "recovered")
    assert _request_row(world.owner, request_id)["state"] == "FAILED"
    assert (
        _scalar(
            world.owner,
            "SELECT count(*) FROM work_orders WHERE id = :id",
            id=scenario.work_order_id,
        )
        == 1
    )


def test_the_backup_evidence_is_checked(clean_slate: World) -> None:
    world = clean_slate
    scenario = _scenario(world, "EVIDENCE")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    # Not RUNNING yet.
    early = history_archive_job.delete_request(
        world.job, _NO_FOLDERS, request_id, PurgeBackup("x", "y")
    )
    assert (early.exit_code, early.error and early.error.code) == (1, "wrong_state")
    _done(history_archive_job.start_deletion(world.job, _NO_FOLDERS, request_id), "started")
    good = _evidence(world.owner, request_id)
    started = _request_row(world.owner, request_id)["started_at"]
    earlier = (started - datetime.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for backup in (
        PurgeBackup(good.name.replace("deletion-", "run-"), good.started_at),
        PurgeBackup(good.name.replace("-pre-purge-", "-manual-"), good.started_at),
        PurgeBackup(good.name, "yesterday"),
        PurgeBackup(good.name, earlier),
        PurgeBackup(good.name, future),
    ):
        report = history_archive_job.delete_request(world.job, _NO_FOLDERS, request_id, backup)
        assert (report.exit_code, report.error and report.error.code) == (1, "backup_invalid"), (
            backup
        )
        assert _request_row(world.owner, request_id)["state"] == "RUNNING"
    _done(history_archive_job.delete_request(world.job, _NO_FOLDERS, request_id, good), "deleted")
    unknown = history_archive_job.start_deletion(world.job, _NO_FOLDERS, 999999)
    assert (unknown.exit_code, unknown.error and unknown.error.code) == (1, "unknown_request")


def test_status_reports_the_deletion_requests(clean_slate: World, tmp_path: Path) -> None:
    world = clean_slate
    scenario = _scenario(world, "STATUS")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    folders = JobFolders(tmp_path, None)
    report = history_archive_job.status(world.job, folders)
    open_request = report.details["open_deletion_request"]
    assert (open_request["id"], open_request["state"]) == (request_id, "APPROVED")
    assert open_request["age_seconds"] >= 0
    assert "deletion" not in " ".join(report.details["findings"])
    _done(_carry_out(world, request_id), "deleted")
    report = history_archive_job.status(world.job, folders)
    assert report.details["open_deletion_request"] is None
    last = report.details["last_finished_deletion_request"]
    assert (last["id"], last["state"]) == (request_id, "COMPLETED")
    assert history_archive_job.job_document(report)["request_id"] is None


def test_the_cli_runs_the_deletion_commands(
    clean_slate: World,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = clean_slate
    scenario = _scenario(world, "CLIJOB")
    holder = client_as(world.client, _KEY)
    request_id = int(_ok(_post_request(holder, work_orders=[scenario.number]), 201)["id"])
    monkeypatch.setattr(cli, "_history_archive_engine", lambda: job_engine(world.url))

    def run(*args: str) -> tuple[int, dict[str, Any]]:
        capsys.readouterr()
        code = cli.main(["history-archive", *args])
        return code, cast(dict[str, Any], json.loads(capsys.readouterr().out))

    with pytest.raises(SystemExit) as usage:
        cli.main(["history-archive", "delete", "--request", str(request_id), "--backup", "x"])
    assert usage.value.code == 2
    code, document = run("start-deletion", "--request", str(request_id))
    assert (code, document["result"], document["state"]) == (0, "started", "RUNNING")
    assert (document["command"], document["request_id"], document["run_id"]) == (
        "history-archive start-deletion",
        request_id,
        None,
    )
    evidence = _evidence(world.owner, request_id)
    code, document = run(
        "delete",
        "--request",
        str(request_id),
        "--backup",
        evidence.name,
        "--backup-started-at",
        evidence.started_at,
    )
    assert (code, document["result"], document["state"]) == (0, "deleted", "COMPLETED")
    assert document["request"]["rows"] == document["deleted"]
    assert document["backup"] == {
        "name": evidence.name,
        "dump_started_at": evidence.started_at.replace("Z", ".000000Z"),
    }
