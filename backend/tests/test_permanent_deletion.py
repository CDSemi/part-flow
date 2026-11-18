"""Tests for ``python -m app.cli delete-permanently`` (interim test-data deletion).

Every scenario is built through the real API (Work Orders, releases,
transfers, a partial transfer, a completion into the Stockroom,
allocations, combines, Scan Station receipts and quantity additions) in
one module database; each test uses its own Part Numbers and Work Order
Numbers. The deletion runs as the database owner (``owner_engine``, as
``pf delete-permanently`` runs it through the owner's one-shot service);
everything else, also in the application-role test mode, runs as the
application role.

After every successful deletion: reconciliation (all checks) is clean,
the deleted Work Order Number can be entered again, exactly one audit
row remains per deleted Work Order and Part Number, and every guard
trigger is enabled. A preview and a refusal never change anything.
"""

import datetime
import json
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import cli
from app.application import permanent_deletion, reconciliation
from app.core.config import get_settings
from app.infrastructure.database_privileges import GUARD_TRIGGERS
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client
from tests.conftest import app_mode_roles, owner_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DB_URL_ENV = "DATABASE_URL"
_TABLES = permanent_deletion.LOCK_ORDER


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _event() -> str:
    return str(uuid.uuid4())


def _suffix() -> str:
    return uuid.uuid4().hex[:6].upper()


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
        self.route_template_id = 0
        self.last_event = ""

    # -- environment -------------------------------------------------------

    def environment(self) -> None:
        department = self.admin.post("/api/departments", json={"name": "Deletion"})
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
        template = self.admin.post(
            "/api/route-templates",
            json={
                "name": "MAT-DEBURR",
                "description": None,
                "steps": [
                    {"area_id": self.areas["MAT"], "operation_id": self.operations["MAT"]},
                    {"area_id": self.areas["DEBURR"], "operation_id": self.operations["DEBURR"]},
                ],
            },
        )
        assert template.status_code == 201, template.text
        self.route_template_id = int(template.json()["id"])

    # -- demand ------------------------------------------------------------

    def work_order(self, number: str, lines: list[tuple[str, int]]) -> tuple[int, list[int]]:
        response = self.admin.post(
            "/api/work-orders",
            json={
                "work_order_number": number,
                "lines": [
                    {"part_number": part_number, "requested_quantity": requested}
                    for part_number, requested in lines
                ],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return int(body["id"]), [int(line["id"]) for line in body["demands"]]

    def release(
        self, work_order_id: int, demand_id: int, part_number: str, quantity: int, *, planned: bool
    ) -> int:
        payload: dict[str, Any] = {
            "part_number": part_number,
            "quantity": quantity,
            "route_mode": "PLANNED" if planned else "FLOATING",
            "starting_area_id": self.areas["MAT"],
            "operation_id": self.operations["MAT"],
            "confirm_active_quantity": True,
            "device_event_id": _event(),
        }
        if planned:
            payload["route_template_id"] = self.route_template_id
        response = self.admin.post(
            f"/api/work-orders/{work_order_id}/demands/{demand_id}/release", json=payload
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def allocate(self, part_number: str, lines: list[tuple[int, int]]) -> None:
        response = self.admin.post(
            "/api/allocations/management",
            json={
                "part_number": part_number,
                "allocation_quantity": sum(quantity for _, quantity in lines),
                "lines": [
                    {"work_order_demand_id": demand_id, "quantity": quantity}
                    for demand_id, quantity in lines
                ],
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text

    def hot_list(self) -> list[int]:
        response = self.admin.get("/api/hot-list")
        assert response.status_code == 200, response.text
        return [int(entry["work_order_demand_id"]) for entry in response.json()["entries"]]

    def hot_change(self, action: str, new_order: list[int]) -> None:
        response = self.admin.post(
            "/api/hot-list/changes",
            json={
                "device_event_id": _event(),
                "action": action,
                "expected_order": self.hot_list(),
                "new_order": new_order,
            },
        )
        assert response.status_code in (200, 201), response.text

    # -- production --------------------------------------------------------

    def arrival(
        self, action: str, source: str, target: str, flow_id: int, part_number: str, quantity: int
    ) -> int:
        event = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[target]}/{action}",
            json={
                "part_number": part_number,
                "quantity_flow_id": flow_id,
                "source_area_id": self.areas[source],
                "target_area_id": self.areas[target],
                "quantity": quantity,
                "device_event_id": event,
            },
        )
        assert response.status_code == 201, response.text
        self.last_event = event
        return int(response.json()["quantity_flow_id"])

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

    def adjust_route(self, flow_id: int) -> None:
        future = self.column(
            "SELECT s.id FROM assigned_route_steps s JOIN quantity_flows f"
            " ON f.assigned_route_id = s.assigned_route_id WHERE f.id = :id AND s.sequence > 1"
            " ORDER BY s.sequence",
            id=flow_id,
        )
        response = self.admin.post(
            f"/api/quantity-flows/{flow_id}/route-adjustments",
            json={
                "device_event_id": _event(),
                "expected_future_step_ids": future,
                "steps": [
                    {
                        "area_id": self.areas["DEBURR"],
                        "operation_id": self.operations["DEBURR"],
                        "instructions": "check the burrs twice",
                    }
                ],
                "reason": "customer asked for a second check",
            },
        )
        assert response.status_code in (200, 201), response.text

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

    def receipt(self, part_number: str, quantity: int) -> int:
        response = self.client.post(
            f"/api/scan-stations/{self.stations['MAT']}/receipts",
            json={
                "part_number": part_number,
                "quantity": quantity,
                "request_type": "MODIFY",
                "route_mode": "FLOATING",
                "operation_id": self.operations["MAT"],
                "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "confirm_active_quantity": True,
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    # -- reads -------------------------------------------------------------

    def scalar(self, sql: str, **params: object) -> Any:
        with self.engine.connect() as connection:
            return connection.execute(sa.text(sql), params).scalar()

    def column(self, sql: str, **params: object) -> list[Any]:
        with self.engine.connect() as connection:
            return list(connection.execute(sa.text(sql), params).scalars())


@pytest.fixture(scope="module")
def builder() -> Iterator[Builder]:
    database = f"partflow_test_permanent_deletion_{uuid.uuid4().hex[:8]}"
    cluster = make_url(os.environ[_DB_URL_ENV])
    admin = create_engine(cluster, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
    url = cluster.set(database=database)
    original = os.environ[_DB_URL_ENV]
    try:
        command.upgrade(_alembic_config(url), "head")
        os.environ[_DB_URL_ENV] = url.render_as_string(hide_password=False)
        get_settings.cache_clear()
        with TestClient(create_app()) as client:
            built = Builder(station_device_client(client), url)
            built.environment()
            yield built
    finally:
        os.environ[_DB_URL_ENV] = original
        get_settings.cache_clear()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def owner(builder: Builder) -> Iterator[Engine]:
    engine = owner_engine(builder.url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(
    work_orders: tuple[str, ...] = (),
    part_numbers: tuple[str, ...] = (),
    *,
    confirm: bool,
) -> permanent_deletion.DeletionRequest:
    return permanent_deletion.DeletionRequest(
        work_order_numbers=work_orders,
        part_numbers=part_numbers,
        operator="Test Operator",
        reason="test data entered by mistake",
        confirm=confirm,
        backup_name="20261117T010203Z-manual",
    )


def _run(
    engine: Engine,
    work_orders: tuple[str, ...] = (),
    part_numbers: tuple[str, ...] = (),
    *,
    confirm: bool,
) -> permanent_deletion.DeletionReport:
    return permanent_deletion.run_deletion(
        engine, _request(work_orders, part_numbers, confirm=confirm), lock_timeout_seconds=10
    )


def _fingerprint(engine: Engine) -> dict[str, str]:
    """The content of every touched table and the guard trigger states."""
    result: dict[str, str] = {}
    with engine.connect() as connection:
        for table in _TABLES:
            result[table] = str(
                connection.execute(
                    sa.text(
                        f"SELECT md5(coalesce(string_agg(t::text, ',' ORDER BY t::text), ''))"
                        f" FROM {table} t"
                    )
                ).scalar_one()
            )
        result["triggers"] = json.dumps(_trigger_states(connection))
    return result


def _trigger_states(connection: sa.Connection) -> dict[str, str]:
    return {
        str(name): str(enabled)
        for name, enabled in connection.execute(
            sa.text("SELECT tgname, tgenabled FROM pg_trigger WHERE tgname = ANY(:names)"),
            {"names": sorted(GUARD_TRIGGERS)},
        )
    }


def _assert_after_deletion(
    builder: Builder,
    report: permanent_deletion.DeletionReport,
    *,
    work_order_ids: list[int],
    part_numbers: list[str],
) -> None:
    assert report.result == "deleted", permanent_deletion.deletion_document(report)
    assert report.exit_code == 0
    with builder.engine.connect() as connection:
        states = _trigger_states(connection)
    assert states == dict.fromkeys(GUARD_TRIGGERS, "O")
    for work_order_id in work_order_ids:
        rows = builder.column(
            "SELECT metadata FROM audit_events WHERE entity_type = 'WorkOrder' AND entity_id = :id",
            id=str(work_order_id),
        )
        assert len(rows) == 1, rows
        record = rows[0][permanent_deletion.AUDIT_KEY]
        assert record["operator"] == "Test Operator"
        assert record["reason"] == "test data entered by mistake"
        assert record["backup"] == "20261117T010203Z-manual"
        assert record["deleted"]["work_orders"] >= 1
        assert (
            builder.scalar("SELECT count(*) FROM work_orders WHERE id = :id", id=work_order_id) == 0
        )
    for part_number in part_numbers:
        rows = builder.column(
            "SELECT event_type FROM audit_events WHERE entity_type = 'PartNumber'"
            " AND entity_id = :pn",
            pn=part_number,
        )
        assert rows == ["DELETED"]
        for table in ("part_numbers", "quantity_flows", "work_order_demands"):
            assert (
                builder.scalar(
                    f"SELECT count(*) FROM {table} WHERE part_number = :pn", pn=part_number
                )
                == 0
            )
    document = permanent_deletion.deletion_document(report)
    assert len(document["audit_rows"]) == len(work_order_ids) + len(part_numbers)  # type: ignore[arg-type]
    clean = reconciliation.run_reconciliation(builder.engine)
    assert reconciliation.result_of(clean) == ("clean", 0), reconciliation.report_document(clean)


# ---------------------------------------------------------------------------
# Successful deletions
# ---------------------------------------------------------------------------


def test_released_work_order_is_deleted_as_if_never_entered(
    builder: Builder, owner: Engine
) -> None:
    s = _suffix()
    pn_a, pn_b = f"DEL-A-{s}", f"DEL-B-{s}"
    number = f"TEST-{s}"
    work_order_id, (line_a, line_b) = builder.work_order(number, [(pn_a, 10), (pn_b, 5)])
    # A released flow, a partial transfer (split), a completion into the
    # Stockroom and an allocation of that stock.
    flow = builder.release(work_order_id, line_a, pn_a, 6, planned=False)
    moved = builder.arrival("transfers", "MAT", "DEBURR", flow, pn_a, 2)
    builder.arrival("stockings", "DEBURR", "STOCK", moved, pn_a, 2)
    builder.allocate(pn_a, [(line_a, 2)])
    # A planned release (an Assigned Route snapshot) that moves on.
    planned = builder.release(work_order_id, line_b, pn_b, 5, planned=True)
    builder.arrival("transfers", "MAT", "DEBURR", planned, pn_b, 5)
    builder.undo("DEBURR", pn_b, builder.last_event)
    builder.arrival("transfers", "MAT", "DEBURR", planned, pn_b, 5)
    # A Work Order that stays, with the same Part Number and its own stock.
    stay_number = f"STAY-{s}"
    stay_id, (stay_line,) = builder.work_order(stay_number, [(pn_a, 3)])
    stay_flow = builder.release(stay_id, stay_line, pn_a, 3, planned=False)
    builder.arrival("stockings", "MAT", "STOCK", stay_flow, pn_a, 3)
    builder.allocate(pn_a, [(stay_line, 3)])
    assert reconciliation.result_of(reconciliation.run_reconciliation(builder.engine))[1] == 0

    before = _fingerprint(owner)
    preview = _run(owner, (number,), confirm=False)
    assert preview.result == "preview", permanent_deletion.deletion_document(preview)
    assert preview.exit_code == 0
    assert _fingerprint(owner) == before
    assert preview.plan is not None
    counts = preview.plan.counts()
    assert counts["work_orders"] == 1
    assert counts["work_order_demands"] == 2
    assert counts["work_order_allocations"] == 1
    assert counts["quantity_flows"] >= 4
    assert counts["quantity_flow_lineage"] >= 2
    assert counts["assigned_routes"] == 1
    assert counts["assigned_route_steps"] == 2
    assert counts["part_numbers"] == 0
    assert counts["audit_events"] >= 3

    report = _run(owner, (number,), confirm=True)
    _assert_after_deletion(builder, report, work_order_ids=[work_order_id], part_numbers=[])
    # Every guard that refuses a delete on the touched tables; the INSERT-only
    # scope of the maintenance role's audit rows (Phase 16) stays enabled.
    assert sorted(report.guard_triggers) == sorted(
        trigger
        for trigger in GUARD_TRIGGERS
        if GUARD_TRIGGERS[trigger].table
        in ("audit_events", "part_movements", "quantity_flow_lineage", "work_order_allocations")
        and trigger != "trg_audit_events_maintenance_scope"
    )
    # No trace of the deleted records is left besides the one audit row.
    assert (
        builder.scalar(
            "SELECT count(*) FROM audit_events WHERE entity_type = 'WorkOrderDemand'"
            " AND entity_id = ANY(:ids)",
            ids=[str(line_a), str(line_b)],
        )
        == 0
    )
    assert (
        builder.scalar(
            "SELECT count(*) FROM part_movements WHERE movement_type = 'RECEIVED'"
            " AND (metadata['context'] ->> 'work_order_demand_id')::integer = ANY(:ids)",
            ids=[line_a, line_b],
        )
        == 0
    )
    # What stays is untouched: the other Work Order, its flow and stock,
    # and the Part Number masters (master data).
    assert builder.scalar("SELECT count(*) FROM work_orders WHERE id = :id", id=stay_id) == 1
    assert builder.scalar("SELECT count(*) FROM quantity_flows WHERE id = :id", id=stay_flow) == 1
    assert builder.column(
        "SELECT part_number FROM part_numbers WHERE part_number = ANY(:pns) ORDER BY 1",
        pns=[pn_a, pn_b],
    ) == [pn_a, pn_b]
    # The number can be entered again.
    again, _ = builder.work_order(number, [(pn_a, 1)])
    assert again != work_order_id


def test_part_numbers_with_flows_and_internal_work_order(builder: Builder, owner: Engine) -> None:
    s = _suffix()
    pn_p, pn_q = f"DEL-P-{s}", f"DEL-Q-{s}"
    number = f"TEST-P-{s}"
    work_order_id, (line_p,) = builder.work_order(number, [(pn_p, 4)])
    flow_p = builder.release(work_order_id, line_p, pn_p, 4, planned=True)
    builder.adjust_route(flow_p)
    route_audit = (
        "SELECT count(*) FROM audit_events WHERE entity_type = 'AssignedRoute'"
        " AND (metadata -> 'route_adjustment' ->> 'quantity_flow_id')::integer = :id"
    )
    assert builder.scalar(route_audit, id=flow_p) == 1
    builder.arrival("transfers", "MAT", "DEBURR", flow_p, pn_p, 4)
    # PN Q: a Scan Station receipt (an internal Work Order), a quantity
    # addition and a combine of both.
    received = builder.receipt(pn_q, 3)
    added = builder.add_quantity(pn_q, 2)
    builder.merge(pn_q, [received, added])
    internal_id = int(
        builder.scalar(
            "SELECT w.id FROM work_orders w JOIN work_order_demands d ON d.work_order_id = w.id"
            " WHERE d.part_number = :pn AND w.work_order_number IS NULL",
            pn=pn_q,
        )
    )

    preview = _run(owner, (number,), (pn_p, pn_q), confirm=False)
    assert preview.result == "preview", permanent_deletion.deletion_document(preview)
    assert preview.plan is not None
    entries = {int(cast(int, entry["id"])): entry for entry in preview.plan.work_orders}
    assert entries[internal_id]["internal"] is True
    assert entries[internal_id]["named"] is False

    report = _run(owner, (number,), (pn_p, pn_q), confirm=True)
    _assert_after_deletion(
        builder,
        report,
        work_order_ids=[work_order_id, internal_id],
        part_numbers=[pn_p, pn_q],
    )
    assert builder.scalar(route_audit, id=flow_p) == 0
    assert (
        builder.scalar(
            "SELECT count(*) FROM work_order_allocations WHERE part_number = ANY(:pns)",
            pns=[pn_p, pn_q],
        )
        == 0
    )
    builder.work_order(number, [(pn_p, 1)])


def test_kept_audit_rows_that_also_concern_other_records(builder: Builder, owner: Engine) -> None:
    s = _suffix()
    pn = f"DEL-H-{s}"
    number, stay_number = f"TEST-H-{s}", f"STAY-H-{s}"
    work_order_id, (line,) = builder.work_order(number, [(pn, 2)])
    stay_id, (stay_line,) = builder.work_order(stay_number, [(f"STAY-H-{s}", 2)])
    # Rank both, then take the line to delete off the Hot list: the
    # removal also moves the staying line up, in the same command.
    builder.hot_change("ADD", [*builder.hot_list(), line])
    builder.hot_change("ADD", [*builder.hot_list(), stay_line])
    builder.hot_change("REMOVE", [entry for entry in builder.hot_list() if entry != line])
    kept_ids = builder.column(
        "SELECT id FROM audit_events WHERE entity_type = 'WorkOrderDemand' AND entity_id = :id"
        " AND metadata ? 'hot_list_change' AND metadata -> 'hot_list_change' ->> 'action'"
        " = 'REMOVE'",
        id=str(line),
    )
    assert len(kept_ids) == 1

    report = _run(owner, (number,), confirm=True)
    _assert_after_deletion(builder, report, work_order_ids=[work_order_id], part_numbers=[])
    assert report.plan is not None
    listed = {int(cast(int, row["id"])) for row in report.plan.kept_audit_rows}
    assert set(kept_ids) <= listed
    assert builder.scalar(
        "SELECT count(*) FROM audit_events WHERE id = ANY(:ids)", ids=kept_ids
    ) == len(kept_ids)
    assert builder.scalar("SELECT count(*) FROM work_orders WHERE id = :id", id=stay_id) == 1
    # The ADD of the deleted line concerned it alone: it is gone.
    assert (
        builder.scalar(
            "SELECT count(*) FROM audit_events WHERE entity_type = 'WorkOrderDemand'"
            " AND entity_id = :id AND metadata -> 'hot_list_change' ->> 'action' = 'ADD'",
            id=str(line),
        )
        == 0
    )


# ---------------------------------------------------------------------------
# Refusals: nothing is deleted, each link is named
# ---------------------------------------------------------------------------


def _assert_refused(
    owner: Engine, report: permanent_deletion.DeletionReport, before: dict[str, str]
) -> list[str]:
    assert report.result == "refused", permanent_deletion.deletion_document(report)
    assert report.exit_code == 1
    assert _fingerprint(owner) == before
    assert report.plan is not None
    return [refusal.code for refusal in report.plan.refusals]


def test_refused_combine_with_a_flow_of_another_work_order(builder: Builder, owner: Engine) -> None:
    s = _suffix()
    pn = f"DEL-M-{s}"
    number, other = f"TEST-M-{s}", f"OTHER-M-{s}"
    work_order_id, (line,) = builder.work_order(number, [(pn, 3)])
    other_id, (other_line,) = builder.work_order(other, [(pn, 2)])
    mine = builder.release(work_order_id, line, pn, 3, planned=False)
    theirs = builder.release(other_id, other_line, pn, 2, planned=False)
    builder.merge(pn, [mine, theirs])
    before = _fingerprint(owner)
    for confirm in (False, True):
        report = _run(owner, (number,), confirm=confirm)
        assert "lineage_link" in _assert_refused(owner, report, before)
        assert report.plan is not None
        messages = " ".join(refusal.message for refusal in report.plan.refusals)
        assert f"Quantity Flow {theirs}" in messages
        assert repr(other) in messages
    # Deleting both Work Orders together is allowed.
    both = _run(owner, (number, other), confirm=True)
    _assert_after_deletion(builder, both, work_order_ids=[work_order_id, other_id], part_numbers=[])


def test_refused_stock_allocated_to_a_work_order_that_stays(
    builder: Builder, owner: Engine
) -> None:
    s = _suffix()
    pn = f"DEL-S-{s}"
    number, other = f"TEST-S-{s}", f"OTHER-S-{s}"
    work_order_id, (line,) = builder.work_order(number, [(pn, 5)])
    _, (other_line,) = builder.work_order(other, [(pn, 3)])
    flow = builder.release(work_order_id, line, pn, 5, planned=False)
    builder.arrival("stockings", "MAT", "STOCK", flow, pn, 5)
    builder.allocate(pn, [(other_line, 3)])
    before = _fingerprint(owner)
    report = _run(owner, (number,), confirm=True)
    assert _assert_refused(owner, report, before) == ["stock_needed"]
    assert report.plan is not None
    assert repr(other) in report.plan.refusals[0].message


def test_refused_allocation_command_shared_with_a_work_order_that_stays(
    builder: Builder, owner: Engine
) -> None:
    s = _suffix()
    pn = f"DEL-C-{s}"
    number, other = f"TEST-C-{s}", f"OTHER-C-{s}"
    supply_id, (supply_line,) = builder.work_order(f"SUPPLY-C-{s}", [(pn, 4)])
    flow = builder.release(supply_id, supply_line, pn, 4, planned=False)
    builder.arrival("stockings", "MAT", "STOCK", flow, pn, 4)
    _, (line,) = builder.work_order(number, [(pn, 2)])
    _, (other_line,) = builder.work_order(other, [(pn, 2)])
    builder.allocate(pn, [(line, 1), (other_line, 1)])
    before = _fingerprint(owner)
    report = _run(owner, (number,), confirm=True)
    assert _assert_refused(owner, report, before) == ["allocation_command_shared"]


def test_refused_part_number_used_by_a_work_order_that_stays(
    builder: Builder, owner: Engine
) -> None:
    s = _suffix()
    pn = f"DEL-U-{s}"
    other = f"OTHER-U-{s}"
    builder.work_order(other, [(pn, 1)])
    before = _fingerprint(owner)
    report = _run(owner, (), (pn,), confirm=True)
    assert _assert_refused(owner, report, before) == ["part_number_used_by_work_order"]
    assert report.plan is not None
    assert repr(other) in report.plan.refusals[0].message


def test_refused_demand_line_on_the_hot_list(builder: Builder, owner: Engine) -> None:
    s = _suffix()
    number = f"TEST-R-{s}"
    _, (line,) = builder.work_order(number, [(f"DEL-R-{s}", 2)])
    builder.hot_change("ADD", [*builder.hot_list(), line])
    before = _fingerprint(owner)
    report = _run(owner, (number,), confirm=True)
    assert _assert_refused(owner, report, before) == ["on_hot_list"]


def test_refused_archived_history(builder: Builder, owner: Engine) -> None:
    """A flow whose history an archival run recorded is never deleted here (Phase 16)."""
    s = _suffix()
    number = f"TEST-ARCH-{s}"
    work_order_id, (line,) = builder.work_order(number, [(f"DEL-ARCH-{s}", 3)])
    flow = builder.release(work_order_id, line, f"DEL-ARCH-{s}", 3, planned=False)
    with owner.begin() as connection:
        user_id = connection.execute(sa.text("SELECT min(id) FROM users")).scalar_one()
        run_id = connection.execute(
            sa.text(
                "INSERT INTO history_archive_runs (state, cutoff, reason, requested_by_user_id,"
                " preview_components, preview_flows, preview_movements, preview_archive_bytes)"
                " VALUES ('APPROVED', '2020-01-01T00:00:00Z', 'test', :user, 1, 1, 1, 0)"
                " RETURNING id"
            ),
            {"user": user_id},
        ).scalar_one()
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
        connection.execute(
            sa.text(
                "INSERT INTO history_archived_flows (run_id, flow_id, part_number, component_id,"
                " flow_status, movement_count, first_movement_id, last_movement_id,"
                " first_occurred_at, last_occurred_at, content_sha256)"
                " SELECT :run, f.id, f.part_number, f.id, 'STOCKED', 1, 1, 1, now(), now(), :sha"
                " FROM quantity_flows f WHERE f.id = :flow"
            ),
            {"run": run_id, "flow": flow, "sha": "ab" * 32},
        )
        connection.execute(
            sa.text(
                "UPDATE history_archive_runs SET state = 'FAILED', finished_at = now(),"
                " failure_reason = 'test' WHERE id = :id"
            ),
            {"id": run_id},
        )
    before = _fingerprint(owner)
    report = _run(owner, (number,), confirm=True)
    assert _assert_refused(owner, report, before) == ["archived_history"]
    assert report.plan is not None
    assert f"archival run {run_id}" in report.plan.refusals[0].message


def test_refused_unknown_inputs(builder: Builder, owner: Engine) -> None:
    s = _suffix()
    before = _fingerprint(owner)
    report = _run(owner, (f"NO-SUCH-{s}",), (f"NO-SUCH-{s}",), confirm=True)
    assert _assert_refused(owner, report, before) == ["unknown_work_order", "unknown_part_number"]
    assert report.plan is not None
    assert all(f"NO-SUCH-{s}" in refusal.message for refusal in report.plan.refusals)


def test_the_application_role_cannot_delete(builder: Builder, owner: Engine) -> None:
    if app_mode_roles() is None:
        pytest.skip("only the application-role test mode connects as the application role")
    s = _suffix()
    number = f"TEST-APP-{s}"
    builder.work_order(number, [(f"DEL-APP-{s}", 1)])
    before = _fingerprint(owner)
    report = _run(builder.engine, (number,), confirm=True)
    assert _assert_refused(owner, report, before) == ["owner_role_required"]
    with builder.engine.begin() as connection, pytest.raises(sa.exc.DBAPIError):
        connection.execute(sa.text("ALTER TABLE part_movements DISABLE TRIGGER ALL"))
    assert _fingerprint(owner) == before


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


class CliRun(NamedTuple):
    exit_code: int
    document: dict[str, Any]
    stderr: str


@pytest.fixture
def run_cli(
    builder: Builder,
    owner: Engine,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., CliRun]:
    # The CLI's own engine, as the database owner (pf runs it in the
    # owner's one-shot service).
    monkeypatch.setenv(_DB_URL_ENV, builder.url.render_as_string(hide_password=False))
    monkeypatch.setattr(cli, "_engine", lambda: owner_engine(builder.url))

    def _cli(*args: str) -> CliRun:
        get_settings.cache_clear()
        capsys.readouterr()
        try:
            exit_code = cli.main(["delete-permanently", *args])
        finally:
            get_settings.cache_clear()
        captured = capsys.readouterr()
        return CliRun(exit_code, json.loads(captured.out), captured.err)

    return _cli


def test_cli_preview_then_confirm(
    builder: Builder, owner: Engine, run_cli: Callable[..., CliRun]
) -> None:
    s = _suffix()
    number = f" TEST CLI {s} "
    pn = f"DEL-CLI-{s}"
    work_order_id, (line,) = builder.work_order(number, [(pn, 2)])
    builder.release(work_order_id, line, pn, 2, planned=False)
    common = ("--work-order", number, "--operator", " Ops ", "--reason", "typo test")

    before = _fingerprint(owner)
    preview = run_cli(*common)
    assert preview.exit_code == 0, preview
    assert _fingerprint(owner) == before
    document = preview.document
    assert document["command"] == "delete-permanently"
    assert (document["result"], document["mode"], document["exit_code"]) == (
        "preview",
        "preview",
        0,
    )
    assert document["operator"] == "Ops"
    assert document["requested"]["work_orders"] == [number]
    assert document["rows"]["work_orders"] == 1
    assert document["refusals"] == [] and document["audit_rows"] == []
    assert "preview" in preview.stderr

    done = run_cli(*common, "--confirm", "--backup-name", "20261117T010203Z-manual")
    assert done.exit_code == 0, done
    assert (done.document["result"], done.document["mode"]) == ("deleted", "delete")
    assert done.document["audit_rows"] == [
        {
            "id": done.document["audit_rows"][0]["id"],
            "entity_type": "WorkOrder",
            "entity_id": str(work_order_id),
        }
    ]
    assert done.document["guard_triggers"]

    refused = run_cli(*common, "--confirm")
    assert refused.exit_code == 1
    assert refused.document["result"] == "refused"
    assert [item["code"] for item in refused.document["refusals"]] == ["unknown_work_order"]

    # A Part Number is canonicalized as PartFlow does it everywhere.
    unknown = run_cli(
        "--part-number", f" no-such-{s.lower()} ", "--operator", "Ops", "--reason", "x"
    )
    assert unknown.exit_code == 1
    assert unknown.document["requested"]["part_numbers"] == [f"NO-SUCH-{s}"]


def test_cli_usage_errors(
    run_cli: Callable[..., CliRun], capsys: pytest.CaptureFixture[str]
) -> None:
    for args in (
        ("--operator", "Ops", "--reason", "x"),
        ("--work-order", "   ", "--operator", "Ops", "--reason", "x"),
        ("--part-number", "A B", "--operator", "Ops", "--reason", "x"),
        ("--work-order", "W", "--reason", "x"),
        ("--work-order", "W", "--operator", "Ops", "--reason", "x", "--backup-name", "nope"),
    ):
        with pytest.raises(SystemExit) as raised:
            cli.main(["delete-permanently", *args])
        assert raised.value.code == 2, args
        capsys.readouterr()
