"""Tests for the read-only reconciliation command (Phase 16 slice 1).

``python -m app.cli reconcile`` runs checks (a)–(j) in ONE read-only
REPEATABLE READ snapshot taken after ACCESS SHARE table locks, prints
one JSON report and exits 0 clean / 1 mismatch / 2 could not run. It
never repairs.

A clean scenario is built ONCE through the real API (every Movement
type and every Quantity Flow status occur) in a template database;
every corruption case clones it (``CREATE DATABASE … TEMPLATE``) and
corrupts the clone as the database owner with direct SQL — never
through the application — then asserts the exact set of failing
checks, the exact finding codes per failing check and the named
entity, with every other check passing. ``app.cli.main`` runs in
process.
"""

import ast
import datetime
import json
import os
import unicodedata
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import psycopg.errors
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app import cli
from app.application import production_release, projections, reconciliation
from app.core.config import get_settings
from app.domain.enums import MovementType, QuantityFlowStatus
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEMPLATE_DATABASE = "partflow_test_reconciliation"
_CASE_DATABASE = "partflow_test_reconciliation_case"
_DB_URL_ENV = "DATABASE_URL"

_PN_A = "PN-A"
_PN_B = "PN-B"
_PN_E = "PN-É"
_PN_C = "PN-C"
_PN_D = "PN-D"

_ALL_RUN = ("a", "b", "c", "d", "e", "f", "i", "j")


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


def _admin_engine() -> Engine:
    return create_engine(make_url(os.environ[_DB_URL_ENV]), isolation_level="AUTOCOMMIT")


def _drop(engine: Engine, name: str) -> None:
    with engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


# ---------------------------------------------------------------------------
# The clean scenario (built once, through the API)
# ---------------------------------------------------------------------------


class Scenario(NamedTuple):
    areas: dict[str, int]
    operations: dict[str, int]
    stations: dict[str, str]
    machines: dict[str, int]
    workers: dict[str, int]
    work_orders: dict[str, int]
    demands: dict[str, int]
    flows: dict[str, int]
    commands: dict[str, str]


def _event() -> str:
    return str(uuid.uuid4())


class _Builder:
    """Drives the API; records the ids the corruption cases name."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.admin = admin_of(client)
        self.engine = cast(Engine, cast(Any, client.app).state.engine)
        self.areas: dict[str, int] = {}
        self.operations: dict[str, int] = {}
        self.stations: dict[str, str] = {}
        self.machines: dict[str, int] = {}
        self.workers: dict[str, int] = {}
        self.work_orders: dict[str, int] = {}
        self.demands: dict[str, int] = {}
        self.flows: dict[str, int] = {}
        self.commands: dict[str, str] = {}

    # -- environment -------------------------------------------------------

    def environment(self) -> None:
        department = self.admin.post("/api/departments", json={"name": "Reconciliation"})
        assert department.status_code == 201, department.text
        for name, terminal in (
            ("MAT", False),
            ("LATHE", False),
            ("DEBURR", False),
            ("STOCK", True),
        ):
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
        tag_format = self.admin.put(
            "/api/barcode-configuration/machine-asset-tag-format",
            json={"prefix": "CD-", "digits": 4},
        )
        assert tag_format.status_code == 200, tag_format.text
        for name in ("M1", "M2"):
            machine = self.admin.post(
                "/api/machines", json={"area_id": self.areas["LATHE"], "name": name}
            )
            assert machine.status_code == 201, machine.text
            self.machines[name] = int(machine.json()["id"])
        for key, badge in (("W1", "badge-é1"), ("W2", "BADGE-2")):
            worker = self.admin.post("/api/workers", json={"name": key, "badge_barcode": badge})
            assert worker.status_code == 201, worker.text
            self.workers[key] = int(worker.json()["id"])
        assert self._worker_badge(self.workers["W1"]) == "BADGE-É1"

    def _worker_badge(self, worker_id: int) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.execute(
                    sa.text("SELECT badge_barcode FROM workers WHERE id = :id"), {"id": worker_id}
                ).scalar_one()
            )

    def route_template(self) -> int:
        response = self.admin.post(
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
        assert response.status_code == 201, response.text
        return int(response.json()["id"])

    # -- demand ------------------------------------------------------------

    def work_order(self, key: str, lines: list[tuple[str, str, int]]) -> None:
        response = self.admin.post(
            "/api/work-orders",
            json={
                "work_order_number": key,
                "lines": [
                    {"part_number": part_number, "requested_quantity": requested}
                    for _, part_number, requested in lines
                ],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        self.work_orders[key] = int(body["id"])
        for (line_key, _, _), line in zip(lines, body["demands"], strict=True):
            self.demands[line_key] = int(line["id"])

    def release(
        self,
        key: str,
        line: str,
        part_number: str,
        quantity: int,
        *,
        route_template_id: int | None = None,
    ) -> int:
        payload: dict[str, Any] = {
            "part_number": part_number,
            "quantity": quantity,
            "route_mode": "PLANNED" if route_template_id is not None else "FLOATING",
            "starting_area_id": self.areas["MAT"],
            "operation_id": self.operations["MAT"],
            "confirm_active_quantity": True,
            "device_event_id": _event(),
        }
        if route_template_id is not None:
            payload["route_template_id"] = route_template_id
        work_order_id = self._work_order_of(line)
        response = self.admin.post(
            f"/api/work-orders/{work_order_id}/demands/{self.demands[line]}/release", json=payload
        )
        assert response.status_code == 201, response.text
        self.flows[key] = int(response.json()["quantity_flow_id"])
        return self.flows[key]

    def _work_order_of(self, line: str) -> int:
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    sa.text("SELECT work_order_id FROM work_order_demands WHERE id = :id"),
                    {"id": self.demands[line]},
                ).scalar_one()
            )

    def rank(self, line: str) -> None:
        hot_list = self.admin.get("/api/hot-list")
        assert hot_list.status_code == 200, hot_list.text
        order = [int(entry["work_order_demand_id"]) for entry in hot_list.json()["entries"]]
        response = self.admin.post(
            "/api/hot-list/changes",
            json={
                "device_event_id": _event(),
                "action": "ADD",
                "expected_order": order,
                "new_order": [*order, self.demands[line]],
            },
        )
        assert response.status_code in (200, 201), response.text

    def allocate(self, part_number: str, line: str, quantity: int) -> int:
        response = self.admin.post(
            "/api/allocations/management",
            json={
                "part_number": part_number,
                "allocation_quantity": quantity,
                "lines": [{"work_order_demand_id": self.demands[line], "quantity": quantity}],
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    sa.text(
                        "SELECT max(id) FROM work_order_allocations"
                        " WHERE work_order_demand_id = :id AND reverses_allocation_id IS NULL"
                    ),
                    {"id": self.demands[line]},
                ).scalar_one()
            )

    def reverse_allocation(self, allocation_id: int) -> None:
        response = self.admin.post(
            f"/api/allocations/{allocation_id}/reversals",
            json={"reason": "wrong Work Order", "device_event_id": _event()},
        )
        assert response.status_code == 201, response.text

    # -- production --------------------------------------------------------

    def arrival(
        self,
        action: str,
        source: str,
        target: str,
        flow: str,
        part_number: str,
        quantity: int,
        **extra: Any,
    ) -> dict[str, Any]:
        event = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[target]}/{action}",
            json={
                "part_number": part_number,
                "quantity_flow_id": self.flows[flow],
                "source_area_id": self.areas[source],
                "target_area_id": self.areas[target],
                "quantity": quantity,
                "device_event_id": event,
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        return {**response.json(), "device_event_id": event}

    def in_area(
        self,
        action: str,
        area: str,
        flow: str,
        part_number: str,
        quantity: int,
        **extra: Any,
    ) -> dict[str, Any]:
        event = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/{action}",
            json={
                "part_number": part_number,
                "quantity_flow_id": self.flows[flow],
                "quantity": quantity,
                "device_event_id": event,
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        return {**response.json(), "device_event_id": event}

    def merge(self, area: str, part_number: str, flows: list[str]) -> dict[str, Any]:
        event = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/merges",
            json={
                "part_number": part_number,
                "quantity_flow_ids": [self.flows[flow] for flow in flows],
                "device_event_id": event,
            },
        )
        assert response.status_code == 201, response.text
        return {**response.json(), "device_event_id": event}

    def add_quantity(self, area: str, part_number: str, quantity: int) -> dict[str, Any]:
        event = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/quantity-additions",
            json={
                "part_number": part_number,
                "quantity": quantity,
                "reason": "found on the rack",
                "operation_id": self.operations[area],
                "device_event_id": event,
            },
        )
        assert response.status_code == 201, response.text
        return {**response.json(), "device_event_id": event}

    def undo(self, area: str, part_number: str, command_id: str) -> str:
        event = _event()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/undos",
            json={
                "part_number": part_number,
                "reverses_device_event_id": command_id,
                "device_event_id": event,
            },
        )
        assert response.status_code == 201, response.text
        return event

    def receipt(self, area: str, part_number: str, quantity: int) -> None:
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/receipts",
            json={
                "part_number": part_number,
                "quantity": quantity,
                "request_type": "MODIFY",
                "route_mode": "FLOATING",
                "operation_id": self.operations[area],
                "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "confirm_active_quantity": True,
                "device_event_id": _event(),
            },
        )
        assert response.status_code == 201, response.text
        self.flows["RECEIPT"] = int(response.json()["quantity_flow_id"])

    def children(self, command_id: str) -> list[int]:
        """The child flows a SPLIT command created, ascending id."""
        with self.engine.connect() as connection:
            return [
                int(child)
                for child in connection.execute(
                    sa.text(
                        "SELECT DISTINCT child_flow_id FROM quantity_flow_lineage"
                        " WHERE device_event_id = :event ORDER BY child_flow_id"
                    ),
                    {"event": command_id},
                ).scalars()
            ]

    def stock(self, key: str, line: str, part_number: str, quantity: int) -> None:
        """Release ``quantity`` and stock all of it (one STOCKED flow)."""
        self.release(key, line, part_number, quantity)
        self.arrival("stockings", "MAT", "STOCK", key, part_number, quantity)

    def scenario(self) -> Scenario:
        return Scenario(
            areas=self.areas,
            operations=self.operations,
            stations=self.stations,
            machines=self.machines,
            workers=self.workers,
            work_orders=self.work_orders,
            demands=self.demands,
            flows=self.flows,
            commands=self.commands,
        )


def _build_scenario(client: TestClient) -> Scenario:
    b = _Builder(client)
    b.environment()
    route = b.route_template()

    # Demand.
    b.work_order("WO1", [("L1", _PN_A, 100), ("L2", _PN_B, 10)])
    b.work_order("WO2", [("WO2", _PN_E, 4)])
    b.work_order("WO3", [("WO3", _PN_C, 10)])
    b.work_order("WO4", [("WO4", _PN_D, 10)])
    b.work_order("WO5", [("WO5-X", "PN-E5", 3), ("WO5-Y", "PN-F5", 2)])
    b.work_order("WO6", [("WO6-A", "PN-G6", 2), ("WO6-B", "PN-H6", 2)])
    b.work_order("SUP-B", [("SUP-B", _PN_B, 5)])
    b.work_order("SUP-E", [("SUP-E", _PN_E, 2)])

    # PN-A — the planned flow: an implicit-completion transfer (PROCESSING
    # in MAT → AREA_COMPLETED + TRANSFERRED), then a later direct DONE.
    b.release("PLANNED", "L1", _PN_A, 20, route_template_id=route)
    moved = b.arrival("transfers", "MAT", "DEBURR", "PLANNED", _PN_A, 20)
    b.commands["IMPLICIT_COMPLETION"] = moved["device_event_id"]
    b.in_area("area-completions", "DEBURR", "PLANNED", _PN_A, 20)

    # Partial transfer: SPLIT, the moved child lands in DEBURR.
    b.release("SPLIT_SOURCE", "L1", _PN_A, 10)
    partial = b.arrival("transfers", "MAT", "DEBURR", "SPLIT_SOURCE", _PN_A, 4)
    b.flows["SPLIT_MOVED"] = int(partial["quantity_flow_id"])
    [remainder] = [
        child for child in b.children(partial["device_event_id"]) if child != b.flows["SPLIT_MOVED"]
    ]
    b.flows["SPLIT_REMAINDER"] = remainder

    # Machines: partial assignment, QUEUE return, DONE, one flow left on M1.
    b.release("LATHE_SOURCE", "L1", _PN_A, 8)
    b.arrival("transfers", "MAT", "LATHE", "LATHE_SOURCE", _PN_A, 8)
    assigned = b.in_area(
        "machine-assignments", "LATHE", "LATHE_SOURCE", _PN_A, 5, machine_id=b.machines["M1"]
    )
    b.flows["LATHE_ASSIGNED"] = int(assigned["quantity_flow_id"])
    [queued] = [
        child
        for child in b.children(assigned["device_event_id"])
        if child != b.flows["LATHE_ASSIGNED"]
    ]
    b.flows["ON_M1"] = queued
    b.in_area("machine-releases", "LATHE", "LATHE_ASSIGNED", _PN_A, 5, machine_id=b.machines["M1"])
    b.in_area(
        "machine-assignments", "LATHE", "LATHE_ASSIGNED", _PN_A, 5, machine_id=b.machines["M2"]
    )
    b.in_area("area-completions", "LATHE", "LATHE_ASSIGNED", _PN_A, 5, machine_id=b.machines["M2"])
    b.in_area("machine-assignments", "LATHE", "ON_M1", _PN_A, 3, machine_id=b.machines["M1"])
    retired = admin_of(b.client).post(f"/api/machines/{b.machines['M2']}/retire", json={})
    assert retired.status_code in (200, 201), retired.text
    # Repair: the completed quantity returns to MAT, which it visited.
    b.arrival(
        "transfers",
        "LATHE",
        "MAT",
        "LATHE_ASSIGNED",
        _PN_A,
        5,
        repair=True,
        repair_reason="burr left on the face",
    )

    # The plain released flow that stays in MAT.
    b.release("MAT_FLOW", "L1", _PN_A, 6)

    # Two combines in MAT: one effective, one undone.
    b.release("MERGE_1A", "L1", _PN_A, 3)
    b.release("MERGE_1B", "L1", _PN_A, 2)
    combined = b.merge("MAT", _PN_A, ["MERGE_1A", "MERGE_1B"])
    b.flows["MERGED_RESULT"] = int(combined["quantity_flow_id"])
    b.release("MERGE_2A", "L1", _PN_A, 2)
    b.release("MERGE_2B", "L1", _PN_A, 2)
    undone_merge = b.merge("MAT", _PN_A, ["MERGE_2A", "MERGE_2B"])
    b.flows["UNDONE_MERGE_RESULT"] = int(undone_merge["quantity_flow_id"])
    b.undo("MAT", _PN_A, undone_merge["device_event_id"])

    # Scrap: full (kept), full (undone), partial.
    b.release("SCRAPPED", "L1", _PN_A, 2)
    b.in_area("scraps", "MAT", "SCRAPPED", _PN_A, 2, reason="damaged")
    b.release("SCRAP_UNDONE", "L1", _PN_A, 2)
    scrap = b.in_area("scraps", "MAT", "SCRAP_UNDONE", _PN_A, 2, reason="damaged")
    b.commands["SCRAP_UNDO"] = b.undo("MAT", _PN_A, scrap["device_event_id"])
    b.release("PARTIAL_SCRAP", "L1", _PN_A, 3)
    b.in_area("scraps", "MAT", "PARTIAL_SCRAP", _PN_A, 1, reason="damaged")

    # Quantity additions: one kept, one undone.
    b.add_quantity("MAT", _PN_A, 2)
    undone_addition = b.add_quantity("MAT", _PN_A, 1)
    b.undo("MAT", _PN_A, undone_addition["device_event_id"])

    # Undo of a transfer and of a partial (SPLIT-prefixed) transfer.
    b.release("TRANSFER_UNDONE", "L1", _PN_A, 2)
    transfer = b.arrival("transfers", "MAT", "DEBURR", "TRANSFER_UNDONE", _PN_A, 2)
    b.undo("DEBURR", _PN_A, transfer["device_event_id"])
    b.release("PARTIAL_UNDONE", "L1", _PN_A, 4)
    partial_transfer = b.arrival("transfers", "MAT", "DEBURR", "PARTIAL_UNDONE", _PN_A, 1)
    b.undo("DEBURR", _PN_A, partial_transfer["device_event_id"])

    # Stock: full and partial STOCKED, allocated with a reversal.
    b.stock("STOCKED_A", "L1", _PN_A, 5)
    b.release("PARTIAL_STOCK", "L1", _PN_A, 4)
    b.arrival("stockings", "MAT", "STOCK", "PARTIAL_STOCK", _PN_A, 3)
    first = b.allocate(_PN_A, "L1", 3)
    b.reverse_allocation(first)
    b.allocate(_PN_A, "L1", 2)

    # PN-B: L2 fully released and stocked, plus a supply; L2 short.
    b.stock("STOCKED_B", "L2", _PN_B, 10)
    b.stock("SUPPLY_B", "SUP-B", _PN_B, 5)
    b.allocate(_PN_B, "L2", 7)

    # PN-É: WO2 completes, is reopened by a reversal, completes again.
    b.stock("STOCKED_E", "WO2", _PN_E, 4)
    b.stock("SUPPLY_E", "SUP-E", _PN_E, 2)
    completing = b.allocate(_PN_E, "WO2", 4)
    b.reverse_allocation(completing)
    b.allocate(_PN_E, "WO2", 4)

    # WO3: ranked, requested 10 > released 6 == allocated 6, open.
    b.stock("STOCKED_C", "WO3", _PN_C, 6)
    b.allocate(_PN_C, "WO3", 6)
    b.rank("WO3")

    # WO4: ranked, completed by a quantity-lowering save (9a7db92).
    b.stock("STOCKED_D", "WO4", _PN_D, 5)
    b.rank("WO4")
    b.allocate(_PN_D, "WO4", 5)
    lowered = admin_of(b.client).patch(
        f"/api/work-orders/{b.work_orders['WO4']}",
        json={"line_edits": [{"id": b.demands["WO4"], "requested_quantity": 5}]},
    )
    assert lowered.status_code == 200, lowered.text
    assert lowered.json()["status"] == "COMPLETED"

    # WO5: completed by removing its last short, unreleased line.
    b.stock("STOCKED_E5", "WO5-X", "PN-E5", 3)
    b.allocate("PN-E5", "WO5-X", 3)
    removed = admin_of(b.client).delete(
        f"/api/work-orders/{b.work_orders['WO5']}/demands/{b.demands['WO5-Y']}"
    )
    assert removed.status_code == 204, removed.text

    # WO6: line A fully allocated, line B unreleased and unallocated.
    b.stock("STOCKED_G6", "WO6-A", "PN-G6", 2)
    b.allocate("PN-G6", "WO6-A", 2)

    # A station Receive Quantity receipt (internal Work Order).
    b.receipt("MAT", "PN-R", 3)
    return b.scenario()


def _assert_clean_scenario(engine: Engine) -> None:
    with engine.connect() as connection:
        types = set(
            connection.execute(sa.text("SELECT movement_type FROM part_movements")).scalars()
        )
        statuses = set(connection.execute(sa.text("SELECT status FROM quantity_flows")).scalars())
    assert types == set(MovementType), set(MovementType) - types
    assert statuses == set(QuantityFlowStatus), set(QuantityFlowStatus) - statuses


@pytest.fixture(scope="module")
def scenario() -> Iterator[Scenario]:
    admin_engine = _admin_engine()
    _drop(admin_engine, _CASE_DATABASE)
    _drop(admin_engine, _TEMPLATE_DATABASE)
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{_TEMPLATE_DATABASE}"'))
    url = make_url(os.environ[_DB_URL_ENV]).set(database=_TEMPLATE_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    original_url = os.environ[_DB_URL_ENV]
    os.environ[_DB_URL_ENV] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            built = _build_scenario(station_device_client(client))
    finally:
        os.environ[_DB_URL_ENV] = original_url
        get_settings.cache_clear()
    check_engine = create_engine(url)
    try:
        _assert_clean_scenario(check_engine)
    finally:
        check_engine.dispose()
    yield built
    _drop(admin_engine, _CASE_DATABASE)
    _drop(admin_engine, _TEMPLATE_DATABASE)
    admin_engine.dispose()


class Case(NamedTuple):
    url: URL
    engine: Engine
    scenario: Scenario

    def execute(self, sql: str, **params: object) -> None:
        with self.engine.begin() as connection:
            connection.execute(sa.text(sql), params)

    def scalar(self, sql: str, **params: object) -> Any:
        """One value; committed, so an ``INSERT … RETURNING`` persists."""
        with self.engine.begin() as connection:
            return connection.execute(sa.text(sql), params).scalar()

    def rows(self, sql: str, **params: object) -> list[Any]:
        with self.engine.connect() as connection:
            return list(connection.execute(sa.text(sql), params))


@pytest.fixture
def case(scenario: Scenario) -> Iterator[Case]:
    admin_engine = _admin_engine()
    _drop(admin_engine, _CASE_DATABASE)
    with admin_engine.connect() as connection:
        connection.execute(
            sa.text(f'CREATE DATABASE "{_CASE_DATABASE}" TEMPLATE "{_TEMPLATE_DATABASE}"')
        )
    url = make_url(os.environ[_DB_URL_ENV]).set(database=_CASE_DATABASE)
    engine = create_engine(url)
    try:
        yield Case(url, engine, scenario)
    finally:
        engine.dispose()
        _drop(admin_engine, _CASE_DATABASE)
        admin_engine.dispose()


class Run(NamedTuple):
    exit_code: int
    report: dict[str, Any]
    stderr: str


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Callable[..., Run]:
    def _run(url: URL | None, *args: str) -> Run:
        if url is None:
            monkeypatch.delenv(_DB_URL_ENV, raising=False)
        else:
            monkeypatch.setenv(_DB_URL_ENV, url.render_as_string(hide_password=False))
        get_settings.cache_clear()
        capsys.readouterr()
        try:
            exit_code = cli.main(["reconcile", *args])
        finally:
            get_settings.cache_clear()
        captured = capsys.readouterr()
        return Run(exit_code, json.loads(captured.out), captured.err)

    return _run


def _checks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["id"]: check for check in report["checks"]}


def _codes(check: dict[str, Any]) -> set[str]:
    return {finding["code"] for finding in check["findings"]}


def _findings(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [
        finding
        for check in report["checks"]
        for finding in check["findings"]
        if finding["code"] == code
    ]


def _assert_failing(result: Run, expected: dict[str, set[str]], *, exit_code: int = 1) -> None:
    """The exact failing set and codes; every other check passes."""
    checks = _checks(result.report)
    failing = {check_id for check_id, check in checks.items() if check["status"] == "fail"}
    observed = {check_id: _codes(checks[check_id]) for check_id in failing}
    assert observed == expected, json.dumps(
        {check_id: checks[check_id]["findings"] for check_id in failing}, indent=1
    )
    for check_id in _ALL_RUN:
        if check_id not in expected:
            assert checks[check_id]["status"] == "pass", checks[check_id]
            assert all(count > 0 for count in checks[check_id]["examined"].values()), check_id
    assert checks["g"]["status"] == "not_applicable"
    assert checks["h"]["status"] == "not_applicable"
    assert result.exit_code == exit_code
    assert result.report["exit_code"] == exit_code


def _entity(finding: dict[str, Any]) -> tuple[str, Any]:
    return finding["entity"]["type"], finding["entity"]["id"]


def _one(report: dict[str, Any], code: str) -> dict[str, Any]:
    found = _findings(report, code)
    assert len(found) == 1, found
    return found[0]


# ---------------------------------------------------------------------------
# Corruption helpers (owner SQL on the clone)
# ---------------------------------------------------------------------------


def _insert_flow(
    case: Case,
    *,
    part_number: str,
    quantity: int,
    area: str,
    status: str = "ACTIVE",
) -> int:
    closed = "now()" if status != "ACTIVE" else "NULL"
    return int(
        case.scalar(
            "INSERT INTO quantity_flows (part_number, quantity, status, current_area_id,"
            f" closed_at) VALUES (:pn, :quantity, :status, :area, {closed}) RETURNING id",
            pn=part_number,
            quantity=quantity,
            status=status,
            area=case.scenario.areas[area],
        )
    )


def _insert_movement(
    case: Case,
    *,
    flow_id: int,
    part_number: str,
    movement_type: str,
    quantity: int,
    area: str,
    from_area: str | None = None,
    station: bool = True,
    metadata: dict[str, Any] | None = None,
    **columns: object,
) -> int:
    scenario = case.scenario
    values: dict[str, object] = {
        "quantity_flow_id": flow_id,
        "part_number": part_number,
        "movement_type": movement_type,
        "quantity": quantity,
        "from_area_id": None if from_area is None else scenario.areas[from_area],
        "to_area_id": scenario.areas[area],
        "operation_id": scenario.operations[area],
        "station_id": scenario.stations[area] if station else None,
        "device_event_id": _event(),
        **columns,
    }
    names = [*values, "occurred_at", "server_received_at", "metadata"]
    placeholders = [f":{name}" for name in values] + ["now()", "now()", "CAST(:metadata AS jsonb)"]
    return int(
        case.scalar(
            f"INSERT INTO part_movements ({', '.join(names)})"
            f" VALUES ({', '.join(placeholders)}) RETURNING id",
            **values,
            metadata=None if metadata is None else json.dumps(metadata),
        )
    )


def _received(case: Case, demand: str | int | None, quantity: int) -> dict[str, Any] | None:
    if demand is None:
        return None
    demand_id = demand if isinstance(demand, int) else case.scenario.demands[demand]
    return {"context": {"work_order_demand_id": demand_id}}


def _released(case: Case, line: str) -> int:
    return int(
        case.scalar(
            "SELECT coalesce(sum(quantity), 0) FROM part_movements WHERE movement_type ="
            " 'RECEIVED' AND (metadata -> 'context' ->> 'work_order_demand_id')::int = :id",
            id=case.scenario.demands[line],
        )
    )


def _demand(case: Case, line: str) -> Any:
    return case.rows(
        "SELECT requested_quantity, allocated_quantity FROM work_order_demands WHERE id = :id",
        id=case.scenario.demands[line],
    )[0]


def _available_stock(case: Case, part_number: str) -> int:
    return int(
        case.scalar(
            "SELECT (SELECT coalesce(sum(m.quantity), 0) FROM part_movements m"
            " WHERE m.part_number = :pn AND m.movement_type = 'STOCKED' AND NOT EXISTS"
            " (SELECT 1 FROM part_movements r WHERE r.reverses_movement_id = m.id))"
            " - (SELECT coalesce(sum(a.quantity), 0) FROM work_order_allocations a"
            " WHERE a.part_number = :pn AND a.reverses_allocation_id IS NULL AND NOT EXISTS"
            " (SELECT 1 FROM work_order_allocations r WHERE r.reverses_allocation_id = a.id))",
            pn=part_number,
        )
    )


def _insert_allocation(
    case: Case,
    *,
    part_number: str,
    line: str,
    quantity: int,
    reverses: int | None = None,
) -> int:
    return int(
        case.scalar(
            "INSERT INTO work_order_allocations (part_number, work_order_demand_id, quantity,"
            " source, allocation_reason, reverses_allocation_id, allocated_at, device_event_id)"
            " SELECT :pn, :demand, :quantity, source, :reason, :reverses, now(), :event"
            " FROM work_order_allocations ORDER BY id LIMIT 1 RETURNING id",
            pn=part_number,
            demand=case.scenario.demands[line],
            quantity=quantity,
            reason="corruption" if reverses is not None else None,
            reverses=reverses,
            event=_event(),
        )
    )


def _flow_quantity(case: Case, flow: str) -> int:
    return int(
        case.scalar(
            "SELECT quantity FROM quantity_flows WHERE id = :id", id=case.scenario.flows[flow]
        )
    )


# ---------------------------------------------------------------------------
# Clean runs
# ---------------------------------------------------------------------------


def test_clean_scenario_reports_clean(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-1: every check passes on the clean scenario; locks stay within CHECK_TABLES."""
    held: list[reconciliation.HeldLocks] = []
    original = reconciliation._held_locks

    def recording(session: Session) -> reconciliation.HeldLocks:
        locks = original(session)
        held.append(locks)
        return locks

    monkeypatch.setattr(reconciliation, "_held_locks", recording)
    result = run(case.url)

    report = result.report
    assert result.exit_code == 0, json.dumps(report, indent=1)
    assert report["result"] == "clean"
    assert report["report_version"] == 1
    assert report["command"] == "reconcile"
    assert list(report) == [
        "report_version",
        "command",
        "result",
        "exit_code",
        "started_at",
        "finished_at",
        "duration_ms",
        "runtime",
        "database",
        "options",
        "error",
        "checks",
    ]
    assert [check["id"] for check in report["checks"]] == list(reconciliation.CHECK_IDS)
    checks = _checks(report)
    for check_id in _ALL_RUN:
        assert checks[check_id]["status"] == "pass", checks[check_id]
        assert checks[check_id]["title"] == reconciliation.CHECK_TITLES[check_id]
        assert checks[check_id]["examined"], check_id
        assert all(count > 0 for count in checks[check_id]["examined"].values()), checks[check_id]
    assert checks["g"]["status"] == "not_applicable"
    assert checks["g"]["reason"] == (
        "No Movement-history archival exists yet. This check starts with the archival purge."
    )
    assert checks["h"]["status"] == "not_applicable"
    assert checks["h"]["reason"] == (
        "Database-role hardening is not in place yet. This check starts with it."
    )
    database = report["database"]
    assert database["transaction_read_only"] is True
    assert database["transaction_isolation"] == "repeatable read"
    assert database["name"] == _CASE_DATABASE
    assert report["runtime"]["unicode_version"] == unicodedata.unidata_version
    assert report["runtime"]["alembic_head"] == database["alembic_revision"]
    assert report["runtime"]["alembic_head"] is not None
    assert report["options"] == {
        "checks": list(reconciliation.CHECK_IDS),
        "statement_timeout_seconds": 300,
        "lock_timeout_seconds": 5,
        "max_findings": 100,
    }
    assert report["error"] is None
    assert result.stderr.strip().startswith("reconcile: clean (10 of 10 checks run, 0 findings) in")
    [locks] = held
    assert locks.advisory == 0
    allowed = {table for tables in reconciliation.CHECK_TABLES.values() for table in tables}
    assert locks.tables <= allowed | {"alembic_version"}, locks.tables - allowed


def test_each_check_reads_only_the_tables_it_locks_up_front(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-1b: CHECK_TABLES is complete PER check.

    A full run locks the union of every check's tables up front, so it
    cannot reveal a table missing from one check's own entry. Run alone,
    a check that reads a table outside its entry takes that lock
    implicitly after the snapshot started, and the step-8 guard sees it.
    """
    held: list[reconciliation.HeldLocks] = []
    original = reconciliation._held_locks

    def recording(session: Session) -> reconciliation.HeldLocks:
        locks = original(session)
        held.append(locks)
        return locks

    monkeypatch.setattr(reconciliation, "_held_locks", recording)
    for check_id in reconciliation.CHECK_IDS:
        held.clear()
        result = run(case.url, "--check", check_id)
        assert result.exit_code == 0, (check_id, json.dumps(result.report["checks"], indent=1))
        [locks] = held
        declared = set(reconciliation.CHECK_TABLES[check_id])
        assert locks.advisory == 0, check_id
        assert declared <= locks.tables, (check_id, declared - locks.tables)
        assert locks.tables <= declared | {"alembic_version"}, (check_id, locks.tables - declared)


def _table_state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        state: dict[str, Any] = {
            table.name: connection.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one()
            for table in models.Base.metadata.sorted_tables
        }
        for table in ("quantity_flows", "work_order_demands", "work_orders"):
            state[f"{table}#hash"] = connection.execute(
                sa.text(f"SELECT md5(string_agg(t::text, '|' ORDER BY id)) FROM {table} t")
            ).scalar_one()
    return state


def _stable(report: dict[str, Any]) -> dict[str, Any]:
    stable = {
        key: value
        for key, value in report.items()
        if key not in {"started_at", "finished_at", "duration_ms"}
    }
    stable["checks"] = [
        {key: value for key, value in check.items() if key != "duration_ms"}
        for check in report["checks"]
    ]
    return stable


def test_runs_write_nothing_and_repeat_identically(case: Case, run: Callable[..., Run]) -> None:
    """R-2: no write; two runs on unchanged data give the same report."""
    before = _table_state(case.engine)
    first = run(case.url)
    second = run(case.url)
    assert _table_state(case.engine) == before
    assert _stable(first.report) == _stable(second.report)


def test_selected_checks_only(case: Case, run: Callable[..., Run]) -> None:
    """R-3: unselected checks are skipped."""
    result = run(case.url, "--check", "a", "--check", "j")
    assert result.exit_code == 0
    statuses = {check_id: check["status"] for check_id, check in _checks(result.report).items()}
    assert statuses == {
        check_id: ("pass" if check_id in {"a", "j"} else "skipped")
        for check_id in reconciliation.CHECK_IDS
    }
    assert result.report["options"]["checks"] == ["a", "j"]
    assert result.stderr.strip().startswith("reconcile: clean (2 of 10 checks run, 0 findings) in")


# ---------------------------------------------------------------------------
# (a) Current positions
# ---------------------------------------------------------------------------


def test_projected_area_drift(case: Case, run: Callable[..., Run]) -> None:
    """C-a1."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET current_area_id = :area WHERE id = :id",
        area=s.areas["DEBURR"],
        id=s.flows["MAT_FLOW"],
    )
    result = run(case.url)
    _assert_failing(result, {"a": {"PROJECTION_AREA_MISMATCH"}})
    finding = _one(result.report, "PROJECTION_AREA_MISMATCH")
    assert _entity(finding) == ("QuantityFlow", s.flows["MAT_FLOW"])
    assert (finding["expected"], finding["actual"]) == (s.areas["MAT"], s.areas["DEBURR"])
    assert finding["part_number"] == _PN_A
    assert result.stderr.strip().startswith("reconcile: MISMATCH in check(s) a (1 findings) in")


def test_projected_machine_drift(case: Case, run: Callable[..., Run]) -> None:
    """C-a2."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET current_machine_id = NULL WHERE id = :id", id=s.flows["ON_M1"]
    )
    result = run(case.url)
    _assert_failing(
        result, {"a": {"PROJECTION_MACHINE_MISMATCH"}, "d": {"MACHINE_ASSIGNED_MISMATCH"}}
    )
    assert _entity(_one(result.report, "PROJECTION_MACHINE_MISMATCH")) == (
        "QuantityFlow",
        s.flows["ON_M1"],
    )
    assert _entity(_one(result.report, "MACHINE_ASSIGNED_MISMATCH")) == (
        "Machine",
        s.machines["M1"],
    )


def test_stored_status_drift(case: Case, run: Callable[..., Run]) -> None:
    """C-a3."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET status = 'SCRAPPED', closed_at = now() WHERE id = :id",
        id=s.flows["SPLIT_MOVED"],
    )
    result = run(case.url)
    _assert_failing(result, {"a": {"FLOW_STATUS_MISMATCH"}})
    finding = _one(result.report, "FLOW_STATUS_MISMATCH")
    assert _entity(finding) == ("QuantityFlow", s.flows["SPLIT_MOVED"])
    assert (finding["expected"], finding["actual"]) == ("ACTIVE", "SCRAPPED")


def test_closed_flow_on_machine(case: Case, run: Callable[..., Run]) -> None:
    """C-a4."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET current_machine_id = :machine WHERE id = :id",
        machine=s.machines["M1"],
        id=s.flows["STOCKED_A"],
    )
    result = run(case.url)
    _assert_failing(result, {"a": {"CLOSED_FLOW_ON_MACHINE"}})
    assert _entity(_one(result.report, "CLOSED_FLOW_ON_MACHINE")) == (
        "QuantityFlow",
        s.flows["STOCKED_A"],
    )


def test_replay_set_divergence(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """C-a5: not reachable by data — guards a future replay divergence."""
    s = case.scenario
    original = projections.rebuild_current_positions

    def dropping(session: Session) -> dict[int, projections.CurrentPosition]:
        positions = original(session)
        positions.pop(s.flows["MAT_FLOW"])
        return positions

    monkeypatch.setattr(projections, "rebuild_current_positions", dropping)
    result = run(case.url)
    _assert_failing(result, {"a": {"REPLAY_SET_MISMATCH"}, "c": {"PN_QUANTITY_IMBALANCE"}})
    finding = _one(result.report, "REPLAY_SET_MISMATCH")
    assert _entity(finding) == ("QuantityFlow", s.flows["MAT_FLOW"])
    assert (finding["expected"], finding["actual"]) == ("ACTIVE", "not ACTIVE")


# ---------------------------------------------------------------------------
# (b) History and conservation
# ---------------------------------------------------------------------------


def test_second_introduction(case: Case, run: Callable[..., Run]) -> None:
    """C-b1."""
    s = case.scenario
    quantity = _flow_quantity(case, "MAT_FLOW") + 1
    requested, _ = _demand(case, "L1")
    assert quantity <= requested - _released(case, "L1")
    movement = _insert_movement(
        case,
        flow_id=s.flows["MAT_FLOW"],
        part_number=_PN_A,
        movement_type="RECEIVED",
        quantity=quantity,
        area="MAT",
        station=False,
        metadata=_received(case, "L1", quantity),
    )
    result = run(case.url)
    _assert_failing(
        result,
        {
            "b": {"INTRODUCTION_NOT_FIRST", "MOVEMENT_QUANTITY_MISMATCH"},
            "c": {"PN_QUANTITY_IMBALANCE"},
        },
    )
    assert _entity(_one(result.report, "INTRODUCTION_NOT_FIRST")) == ("PartMovement", movement)
    assert _entity(_one(result.report, "MOVEMENT_QUANTITY_MISMATCH")) == (
        "QuantityFlow",
        s.flows["MAT_FLOW"],
    )


def test_split_not_conserved(case: Case, run: Callable[..., Run]) -> None:
    """C-b2."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET quantity = quantity + 1 WHERE id = :id",
        id=s.flows["SPLIT_REMAINDER"],
    )
    split_event = case.scalar(
        "SELECT device_event_id FROM quantity_flow_lineage WHERE child_flow_id = :id",
        id=s.flows["SPLIT_REMAINDER"],
    )
    result = run(case.url)
    _assert_failing(
        result,
        {
            "b": {"SPLIT_NOT_CONSERVED", "MOVEMENT_QUANTITY_MISMATCH"},
            "c": {"PN_QUANTITY_IMBALANCE"},
        },
    )
    assert _entity(_one(result.report, "SPLIT_NOT_CONSERVED")) == (
        "QuantityFlowLineage",
        split_event,
    )


def test_route_step_on_floating_flow(case: Case, run: Callable[..., Run]) -> None:
    """C-b3."""
    s = case.scenario
    stepped = [
        int(row.id)
        for row in case.rows(
            "SELECT id FROM part_movements WHERE quantity_flow_id = :id"
            " AND assigned_route_step_id IS NOT NULL",
            id=s.flows["PLANNED"],
        )
    ]
    assert stepped
    case.execute(
        "UPDATE quantity_flows SET route_mode = 'FLOATING', assigned_route_id = NULL"
        " WHERE id = :id",
        id=s.flows["PLANNED"],
    )
    result = run(case.url)
    _assert_failing(result, {"b": {"ROUTE_STEP_MISMATCH"}})
    found = _findings(result.report, "ROUTE_STEP_MISMATCH")
    assert sorted(finding["entity"]["id"] for finding in found) == sorted(stepped)


def test_flow_without_movement(case: Case, run: Callable[..., Run]) -> None:
    """C-b4."""
    flow = _insert_flow(case, part_number=_PN_A, quantity=1, area="MAT", status="REVERSED")
    result = run(case.url)
    _assert_failing(result, {"b": {"FLOW_WITHOUT_MOVEMENT"}})
    assert _entity(_one(result.report, "FLOW_WITHOUT_MOVEMENT")) == ("QuantityFlow", flow)


def test_merge_not_conserved(case: Case, run: Callable[..., Run]) -> None:
    """C-b5."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET quantity = quantity + 1 WHERE id = :id",
        id=s.flows["MERGED_RESULT"],
    )
    result = run(case.url)
    _assert_failing(
        result,
        {
            "b": {"MERGE_NOT_CONSERVED", "MOVEMENT_QUANTITY_MISMATCH"},
            "c": {"PN_QUANTITY_IMBALANCE"},
        },
    )


def test_lineage_edge_across_part_numbers(case: Case, run: Callable[..., Run]) -> None:
    """C-b6."""
    s = case.scenario
    assert _flow_quantity(case, "STOCKED_E") == _flow_quantity(case, "SPLIT_MOVED")
    edge = case.scalar(
        "INSERT INTO quantity_flow_lineage (relation, parent_flow_id, child_flow_id,"
        " device_event_id) VALUES ('SPLIT', :parent, :child, :event) RETURNING id",
        parent=s.flows["STOCKED_E"],
        child=s.flows["SPLIT_MOVED"],
        event=_event(),
    )
    result = run(case.url)
    _assert_failing(result, {"a": {"LINEAGE_CONSUMPTION_MISMATCH"}, "b": {"LINEAGE_PN_MISMATCH"}})
    assert _entity(_one(result.report, "LINEAGE_CONSUMPTION_MISMATCH")) == (
        "QuantityFlow",
        s.flows["STOCKED_E"],
    )
    assert _entity(_one(result.report, "LINEAGE_PN_MISMATCH")) == ("QuantityFlowLineage", edge)


def test_reversal_of_a_reversal(case: Case, run: Callable[..., Run]) -> None:
    """C-b7."""
    s = case.scenario
    [undo] = case.rows(
        "SELECT * FROM part_movements WHERE device_event_id = :event",
        event=s.commands["SCRAP_UNDO"],
    )
    assert undo.movement_type == "REVERSED"
    reversal = case.scalar(
        "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type, quantity,"
        " from_area_id, to_area_id, operation_id, station_id, occurred_at, server_received_at,"
        " device_event_id, reverses_movement_id) VALUES (:flow, :pn, 'REVERSED', :quantity,"
        " :from_area, :to_area, :operation, :station, now(), now(), :event, :reverses)"
        " RETURNING id",
        flow=undo.quantity_flow_id,
        pn=undo.part_number,
        quantity=undo.quantity,
        from_area=undo.from_area_id,
        to_area=undo.to_area_id,
        operation=undo.operation_id,
        station=undo.station_id,
        event=_event(),
        reverses=undo.id,
    )
    result = run(case.url)
    _assert_failing(result, {"b": {"REVERSAL_MISMATCH"}})
    assert _entity(_one(result.report, "REVERSAL_MISMATCH")) == ("PartMovement", reversal)


def test_partial_command_reversal(case: Case, run: Callable[..., Run]) -> None:
    """C-b8."""
    s = case.scenario
    command_rows = case.rows(
        "SELECT * FROM part_movements WHERE device_event_id = :event ORDER BY id",
        event=s.commands["IMPLICIT_COMPLETION"],
    )
    assert [row.movement_type for row in command_rows] == ["AREA_COMPLETED", "TRANSFERRED"]
    completed = command_rows[0]
    case.execute(
        "INSERT INTO part_movements (quantity_flow_id, part_number, movement_type, quantity,"
        " from_area_id, to_area_id, operation_id, station_id, occurred_at, server_received_at,"
        " device_event_id, reverses_movement_id) VALUES (:flow, :pn, 'REVERSED', :quantity,"
        " :area, :area, :operation, :station, now(), now(), :event, :reverses)",
        flow=completed.quantity_flow_id,
        pn=completed.part_number,
        quantity=completed.quantity,
        area=completed.to_area_id,
        operation=completed.operation_id,
        station=completed.station_id,
        event=_event(),
        reverses=completed.id,
    )
    result = run(case.url)
    _assert_failing(result, {"b": {"PARTIAL_COMMAND_REVERSAL"}})
    assert _entity(_one(result.report, "PARTIAL_COMMAND_REVERSAL")) == (
        "PartMovement",
        s.commands["IMPLICIT_COMPLETION"],
    )


def test_replay_failure_isolated_to_replay_checks(case: Case, run: Callable[..., Run]) -> None:
    """C-r1: a lineage-less SPLIT flow fails (b) and errors (a), (c), (d) only."""
    flow = _insert_flow(case, part_number=_PN_A, quantity=1, area="DEBURR")
    _insert_movement(
        case,
        flow_id=flow,
        part_number=_PN_A,
        movement_type="SPLIT",
        quantity=1,
        area="DEBURR",
        from_area="DEBURR",
    )
    result = run(case.url)
    checks = _checks(result.report)
    assert result.exit_code == 2
    assert result.report["result"] == "error"
    assert checks["b"]["status"] == "fail"
    assert _codes(checks["b"]) == {"FIRST_MOVEMENT_INVALID"}
    assert _entity(_one(result.report, "FIRST_MOVEMENT_INVALID")) == ("QuantityFlow", flow)
    for check_id in ("a", "c", "d"):
        assert checks[check_id]["status"] == "error"
        assert checks[check_id]["error_code"] == "replay_failed"
        assert str(flow) in checks[check_id]["reason"]
        assert checks[check_id]["reason"].startswith("The Movement-history replay failed: ")
    for check_id in ("e", "f", "i", "j"):
        assert checks[check_id]["status"] == "pass", checks[check_id]
    assert result.stderr.strip().startswith("reconcile: ERROR in check(s) a, c, d;")


# ---------------------------------------------------------------------------
# (c) Per-PN balance, (d) Machines
# ---------------------------------------------------------------------------


def test_pn_quantity_imbalance(case: Case, run: Callable[..., Run]) -> None:
    """C-c1."""
    s = case.scenario
    case.execute(
        "UPDATE quantity_flows SET quantity = quantity + 2 WHERE id = :id", id=s.flows["MAT_FLOW"]
    )
    result = run(case.url)
    _assert_failing(result, {"b": {"MOVEMENT_QUANTITY_MISMATCH"}, "c": {"PN_QUANTITY_IMBALANCE"}})
    finding = _one(result.report, "PN_QUANTITY_IMBALANCE")
    assert _entity(finding) == ("PN", _PN_A)
    detail = finding["detail"]
    assert finding["expected"] == detail["received"] + detail["added"] - detail["added_reversed"]
    assert finding["actual"] == detail["active"] + detail["stocked"] + detail["scrapped"]
    assert finding["actual"] - finding["expected"] == 2
    assert detail["added"] > 0 and detail["added_reversed"] > 0
    assert detail["scrapped"] > 0 and detail["stocked"] > 0


def test_retired_machine_holds_quantity(case: Case, run: Callable[..., Run]) -> None:
    """C-d1."""
    s = case.scenario
    case.execute(
        "UPDATE machines SET retired_on = current_date WHERE id = :id", id=s.machines["M1"]
    )
    result = run(case.url)
    _assert_failing(result, {"d": {"RETIRED_MACHINE_HOLDS_QUANTITY"}})
    assert _entity(_one(result.report, "RETIRED_MACHINE_HOLDS_QUANTITY")) == (
        "Machine",
        s.machines["M1"],
    )


def test_assignment_outside_the_machine_area(case: Case, run: Callable[..., Run]) -> None:
    """C-d2."""
    s = case.scenario
    _insert_movement(
        case,
        flow_id=s.flows["SPLIT_MOVED"],
        part_number=_PN_A,
        movement_type="ASSIGNED_TO_MACHINE",
        quantity=_flow_quantity(case, "SPLIT_MOVED"),
        area="DEBURR",
        from_area="DEBURR",
        destination_machine_id=s.machines["M1"],
    )
    result = run(case.url)
    _assert_failing(
        result,
        {
            "a": {"PROJECTION_MACHINE_MISMATCH"},
            "d": {"MACHINE_ASSIGNED_MISMATCH", "MACHINE_AREA_MISMATCH"},
        },
    )
    finding = _one(result.report, "MACHINE_AREA_MISMATCH")
    assert _entity(finding) == ("QuantityFlow", s.flows["SPLIT_MOVED"])
    assert (finding["expected"], finding["actual"]) == (s.areas["LATHE"], s.areas["DEBURR"])


# ---------------------------------------------------------------------------
# (e) Release evidence and Work Order status
# ---------------------------------------------------------------------------


def test_release_naming_a_missing_demand(case: Case, run: Callable[..., Run]) -> None:
    """C-e1."""
    flow = _insert_flow(case, part_number=_PN_A, quantity=2, area="MAT")
    movement = _insert_movement(
        case,
        flow_id=flow,
        part_number=_PN_A,
        movement_type="RECEIVED",
        quantity=2,
        area="MAT",
        station=False,
        metadata=_received(case, 2147480000, 2),
    )
    result = run(case.url)
    _assert_failing(result, {"e": {"RELEASE_DEMAND_MISSING"}})
    finding = _one(result.report, "RELEASE_DEMAND_MISSING")
    assert _entity(finding) == ("PartMovement", movement)
    assert finding["actual"] == 2147480000


def test_released_beyond_requested(case: Case, run: Callable[..., Run]) -> None:
    """C-e2."""
    s = case.scenario
    released = _released(case, "L1")
    _, allocated = _demand(case, "L1")
    assert released - 1 >= allocated
    case.execute(
        "UPDATE work_order_demands SET requested_quantity = :requested WHERE id = :id",
        requested=released - 1,
        id=s.demands["L1"],
    )
    result = run(case.url)
    _assert_failing(result, {"e": {"RELEASED_EXCEEDS_REQUESTED"}})
    finding = _one(result.report, "RELEASED_EXCEEDS_REQUESTED")
    assert _entity(finding) == ("WorkOrderDemand", s.demands["L1"])
    assert (finding["expected"], finding["actual"]) == (released - 1, released)


def test_duplicate_pn_on_a_work_order(case: Case, run: Callable[..., Run]) -> None:
    """C-e3."""
    s = case.scenario
    duplicate = case.scalar(
        "INSERT INTO work_order_demands (work_order_id, part_number, request_type,"
        " requested_quantity) VALUES (:wo, :pn, 'NEW', 5) RETURNING id",
        wo=s.work_orders["WO1"],
        pn=_PN_A,
    )
    result = run(case.url)
    _assert_failing(result, {"e": {"DUPLICATE_PN_ON_WORK_ORDER"}})
    finding = _one(result.report, "DUPLICATE_PN_ON_WORK_ORDER")
    assert _entity(finding) == ("WorkOrder", s.work_orders["WO1"])
    assert finding["detail"]["demand_ids"] == sorted([s.demands["L1"], duplicate])


def test_stored_work_order_status(case: Case, run: Callable[..., Run]) -> None:
    """C-e4."""
    s = case.scenario
    case.execute(
        "UPDATE work_orders SET status = 'RELEASED' WHERE id = :id", id=s.work_orders["WO1"]
    )
    result = run(case.url)
    _assert_failing(result, {"e": {"WORK_ORDER_STATUS_STORED"}})
    assert _entity(_one(result.report, "WORK_ORDER_STATUS_STORED")) == (
        "WorkOrder",
        s.work_orders["WO1"],
    )


def test_release_without_or_against_its_demand(case: Case, run: Callable[..., Run]) -> None:
    """C-e5."""
    bare = _insert_flow(case, part_number=_PN_A, quantity=1, area="MAT")
    without = _insert_movement(
        case,
        flow_id=bare,
        part_number=_PN_A,
        movement_type="RECEIVED",
        quantity=1,
        area="MAT",
        station=False,
    )
    other = _insert_flow(case, part_number=_PN_E, quantity=1, area="MAT")
    mismatched = _insert_movement(
        case,
        flow_id=other,
        part_number=_PN_E,
        movement_type="RECEIVED",
        quantity=1,
        area="MAT",
        station=False,
        metadata=_received(case, "L1", 1),
    )
    result = run(case.url)
    _assert_failing(result, {"e": {"RELEASE_WITHOUT_DEMAND", "RELEASE_PN_MISMATCH"}})
    assert _entity(_one(result.report, "RELEASE_WITHOUT_DEMAND")) == ("PartMovement", without)
    finding = _one(result.report, "RELEASE_PN_MISMATCH")
    assert _entity(finding) == ("PartMovement", mismatched)
    assert (finding["expected"], finding["actual"]) == (_PN_A, _PN_E)


# ---------------------------------------------------------------------------
# (f) Allocations and completion, (i) Hot list
# ---------------------------------------------------------------------------


def test_allocated_projection_drift(case: Case, run: Callable[..., Run]) -> None:
    """C-f1."""
    s = case.scenario
    case.execute(
        "UPDATE work_order_demands SET allocated_quantity = allocated_quantity + 1 WHERE id = :id",
        id=s.demands["L2"],
    )
    result = run(case.url)
    _assert_failing(result, {"f": {"ALLOCATED_QUANTITY_MISMATCH"}})
    assert _entity(_one(result.report, "ALLOCATED_QUANTITY_MISMATCH")) == (
        "WorkOrderDemand",
        s.demands["L2"],
    )


def test_stuck_work_order_after_a_save(case: Case, run: Callable[..., Run]) -> None:
    """C-f2: the pre-9a7db92 shape is reported, never repaired."""
    s = case.scenario
    requested, allocated = _demand(case, "WO3")
    assert allocated == _released(case, "WO3") < requested
    rank = case.scalar(
        "SELECT priority_rank FROM work_order_demands WHERE id = :id", id=s.demands["WO3"]
    )
    assert rank is not None
    audit_rows = case.scalar("SELECT count(*) FROM audit_events")
    case.execute(
        "UPDATE work_order_demands SET requested_quantity = allocated_quantity WHERE id = :id",
        id=s.demands["WO3"],
    )
    result = run(case.url)
    _assert_failing(
        result,
        {
            "e": {"WORK_ORDER_STATUS_MISMATCH"},
            "f": {"COMPLETED_AT_MISMATCH"},
            "i": {"HOT_ENTRY_INACTIVE"},
        },
    )
    status = _one(result.report, "WORK_ORDER_STATUS_MISMATCH")
    assert _entity(status) == ("WorkOrder", s.work_orders["WO3"])
    assert (status["expected"], status["actual"]) == ("COMPLETED", "RELEASED")
    completion = _one(result.report, "COMPLETED_AT_MISMATCH")
    assert _entity(completion) == ("WorkOrder", s.work_orders["WO3"])
    assert completion["actual"] is None and completion["expected"] is not None
    assert completion["detail"] == {
        "kind": "not_completed_but_fully_allocated",
        "latest_demand_change_at": None,
    }
    assert _entity(_one(result.report, "HOT_ENTRY_INACTIVE")) == (
        "WorkOrderDemand",
        s.demands["WO3"],
    )
    # Nothing was repaired.
    assert (
        case.scalar("SELECT completed_at FROM work_orders WHERE id = :id", id=s.work_orders["WO3"])
        is None
    )
    assert (
        case.scalar(
            "SELECT priority_rank FROM work_order_demands WHERE id = :id", id=s.demands["WO3"]
        )
        == rank
    )
    assert case.scalar("SELECT count(*) FROM audit_events") == audit_rows


def test_pn_over_allocated(case: Case, run: Callable[..., Run]) -> None:
    """C-f3."""
    s = case.scenario
    quantity = _available_stock(case, _PN_A) + 1
    requested, allocated = _demand(case, "L1")
    assert quantity <= requested - allocated
    _insert_allocation(case, part_number=_PN_A, line="L1", quantity=quantity)
    result = run(case.url)
    _assert_failing(result, {"f": {"PN_OVER_ALLOCATED", "ALLOCATED_QUANTITY_MISMATCH"}})
    assert _entity(_one(result.report, "PN_OVER_ALLOCATED")) == ("PN", _PN_A)
    assert _entity(_one(result.report, "ALLOCATED_QUANTITY_MISMATCH")) == (
        "WorkOrderDemand",
        s.demands["L1"],
    )


def test_allocation_against_another_pn(case: Case, run: Callable[..., Run]) -> None:
    """C-f4."""
    assert _available_stock(case, _PN_E) >= 1
    allocation = _insert_allocation(case, part_number=_PN_E, line="L1", quantity=1)
    result = run(case.url)
    _assert_failing(result, {"f": {"ALLOCATION_PN_MISMATCH", "ALLOCATED_QUANTITY_MISMATCH"}})
    finding = _one(result.report, "ALLOCATION_PN_MISMATCH")
    assert _entity(finding) == ("WorkOrderAllocation", allocation)
    assert (finding["expected"], finding["actual"]) == (_PN_A, _PN_E)


def test_stuck_work_order_after_a_line_removal(case: Case, run: Callable[..., Run]) -> None:
    """C-f5."""
    s = case.scenario
    case.execute("DELETE FROM work_order_demands WHERE id = :id", id=s.demands["WO6-B"])
    result = run(case.url)
    _assert_failing(result, {"e": {"WORK_ORDER_STATUS_MISMATCH"}, "f": {"COMPLETED_AT_MISMATCH"}})
    completion = _one(result.report, "COMPLETED_AT_MISMATCH")
    assert _entity(completion) == ("WorkOrder", s.work_orders["WO6"])
    assert completion["detail"]["kind"] == "not_completed_but_fully_allocated"


def test_allocated_beyond_requested(case: Case, run: Callable[..., Run]) -> None:
    """C-f6."""
    s = case.scenario
    requested, allocated = _demand(case, "L2")
    quantity = requested - allocated + 1
    assert quantity <= _available_stock(case, _PN_B)
    _insert_allocation(case, part_number=_PN_B, line="L2", quantity=quantity)
    result = run(case.url)
    _assert_failing(result, {"f": {"ALLOCATED_EXCEEDS_REQUESTED", "ALLOCATED_QUANTITY_MISMATCH"}})
    assert _entity(_one(result.report, "ALLOCATED_EXCEEDS_REQUESTED")) == (
        "WorkOrderDemand",
        s.demands["L2"],
    )


def test_reversal_with_another_quantity(case: Case, run: Callable[..., Run]) -> None:
    """C-f7."""
    s = case.scenario
    [original] = case.rows(
        "SELECT a.id, a.quantity FROM work_order_allocations a WHERE a.work_order_demand_id = :id"
        " AND a.reverses_allocation_id IS NULL AND NOT EXISTS (SELECT 1 FROM"
        " work_order_allocations r WHERE r.reverses_allocation_id = a.id)",
        id=s.demands["L1"],
    )
    reversal = _insert_allocation(
        case,
        part_number=_PN_A,
        line="L1",
        quantity=int(original.quantity) + 1,
        reverses=int(original.id),
    )
    result = run(case.url)
    _assert_failing(result, {"f": {"ALLOCATION_REVERSAL_MISMATCH", "ALLOCATED_QUANTITY_MISMATCH"}})
    assert _entity(_one(result.report, "ALLOCATION_REVERSAL_MISMATCH")) == (
        "WorkOrderAllocation",
        reversal,
    )


def test_different_done_date(case: Case, run: Callable[..., Run]) -> None:
    """C-f8."""
    s = case.scenario
    case.execute(
        "UPDATE work_orders SET completed_at = completed_at - interval '1 day' WHERE id = :id",
        id=s.work_orders["WO2"],
    )
    result = run(case.url)
    _assert_failing(result, {"f": {"COMPLETED_AT_MISMATCH"}})
    finding = _one(result.report, "COMPLETED_AT_MISMATCH")
    assert _entity(finding) == ("WorkOrder", s.work_orders["WO2"])
    assert finding["detail"] == {"kind": "different_done_date"}


def test_completed_hot_entry(case: Case, run: Callable[..., Run]) -> None:
    """C-i1."""
    s = case.scenario
    case.execute(
        "UPDATE work_orders SET completed_at = now() WHERE id = :id", id=s.work_orders["WO3"]
    )
    result = run(case.url)
    _assert_failing(
        result,
        {
            "e": {"WORK_ORDER_STATUS_MISMATCH"},
            "f": {"COMPLETED_AT_MISMATCH"},
            "i": {"HOT_ENTRY_INACTIVE"},
        },
    )
    status = _one(result.report, "WORK_ORDER_STATUS_MISMATCH")
    assert (status["expected"], status["actual"]) == ("OPEN", "COMPLETED")
    assert _one(result.report, "COMPLETED_AT_MISMATCH")["detail"] == {
        "kind": "completed_but_not_fully_allocated"
    }
    hot = _one(result.report, "HOT_ENTRY_INACTIVE")
    assert _entity(hot) == ("WorkOrderDemand", s.demands["WO3"])
    assert hot["expected"] is None and hot["actual"] == hot["detail"]["priority_rank"]


# ---------------------------------------------------------------------------
# (j) Canonical identity
# ---------------------------------------------------------------------------


def test_non_canonical_and_colliding_pns(case: Case, run: Callable[..., Run]) -> None:
    """C-j1."""
    case.execute("INSERT INTO part_numbers (part_number) VALUES ('PNé1'), ('PNÉ1')")
    result = run(case.url)
    _assert_failing(result, {"j": {"PN_NOT_CANONICAL", "PN_CANONICAL_COLLISION"}})
    finding = _one(result.report, "PN_NOT_CANONICAL")
    assert _entity(finding) == ("PN", "PNé1")
    assert finding["expected"] == "PNÉ1"
    assert finding["detail"]["changed_code_points"] == ["U+00E9"]
    assert finding["detail"]["tables"] == ["part_numbers"]
    collision = _one(result.report, "PN_CANONICAL_COLLISION")
    assert _entity(collision) == ("PN", "PNÉ1")
    assert collision["actual"] == ["PNÉ1", "PNé1"]


def test_pn_with_internal_unicode_whitespace(case: Case, run: Callable[..., Run]) -> None:
    """C-j2."""
    case.execute("INSERT INTO part_numbers (part_number) VALUES (:pn)", pn="PN　X")
    result = run(case.url)
    _assert_failing(result, {"j": {"PN_NOT_CANONICAL"}})
    finding = _one(result.report, "PN_NOT_CANONICAL")
    assert finding["expected"] is None
    assert finding["detail"]["error"] == "Part Number must not contain internal whitespace."


def test_non_canonical_and_colliding_badges(case: Case, run: Callable[..., Run]) -> None:
    """C-j3."""
    s = case.scenario
    case.execute(
        "UPDATE workers SET badge_barcode = :badge WHERE id = :id",
        badge="BADGE-é2",
        id=s.workers["W2"],
    )
    case.execute(
        "UPDATE workers SET badge_barcode = :badge WHERE id = :id",
        badge="BADGE-É2",
        id=s.workers["W1"],
    )
    result = run(case.url)
    _assert_failing(result, {"j": {"BADGE_NOT_CANONICAL", "BADGE_CANONICAL_COLLISION"}})
    finding = _one(result.report, "BADGE_NOT_CANONICAL")
    assert _entity(finding) == ("Worker", s.workers["W2"])
    assert finding["expected"] == "BADGE-É2"
    assert finding["detail"]["changed_code_points"] == ["U+00E9"]
    collision = _one(result.report, "BADGE_CANONICAL_COLLISION")
    assert collision["actual"] == sorted([s.workers["W1"], s.workers["W2"]])


def test_asset_tag_prefix_refused_by_the_interpreter(case: Case, run: Callable[..., Run]) -> None:
    """C-j4: a no-break space passes the database's [[:space:]] but not Python's \\s."""
    case.execute("UPDATE machine_asset_tag_config SET prefix = :prefix", prefix="A ")
    assert case.scalar("SELECT prefix FROM machine_asset_tag_config") == "A ", (
        "the database refused the no-break space prefix; the case needs a ctype that accepts it"
    )
    result = run(case.url)
    _assert_failing(result, {"j": {"ASSET_TAG_PREFIX_REFUSED"}})
    assert _entity(_one(result.report, "ASSET_TAG_PREFIX_REFUSED")) == (
        "MachineAssetTagConfig",
        1,
    )


def test_check_outcome_changed(case: Case, run: Callable[..., Run]) -> None:
    """C-j5."""
    s = case.scenario
    case.execute("ALTER TABLE workers DROP CONSTRAINT ck_workers_badge_barcode_canonical")
    case.execute("UPDATE workers SET badge_barcode = 'PF:X' WHERE id = :id", id=s.workers["W2"])
    result = run(case.url)
    _assert_failing(result, {"j": {"CHECK_OUTCOME_CHANGED", "BADGE_NOT_CANONICAL"}})
    finding = _one(result.report, "CHECK_OUTCOME_CHANGED")
    assert _entity(finding) == ("Worker", s.workers["W2"])
    assert finding["detail"] == {
        "constraint": "ck_workers_badge_barcode_canonical",
        "table": "workers",
        "value": "PF:X",
    }


def test_duplicate_identity_key_without_its_index(case: Case, run: Callable[..., Run]) -> None:
    """C-j6."""
    s = case.scenario
    case.execute("ALTER TABLE workers DROP CONSTRAINT uq_workers_badge_barcode")
    case.execute(
        "UPDATE workers SET badge_barcode = (SELECT badge_barcode FROM workers WHERE id = :w1)"
        " WHERE id = :w2",
        w1=s.workers["W1"],
        w2=s.workers["W2"],
    )
    result = run(case.url)
    _assert_failing(result, {"j": {"UNIQUE_KEY_DUPLICATED"}})
    finding = _one(result.report, "UNIQUE_KEY_DUPLICATED")
    assert _entity(finding) == ("Worker", "BADGE-É1")
    assert finding["actual"] == sorted([s.workers["W1"], s.workers["W2"]])
    assert finding["detail"] == {
        "table": "workers",
        "constraint": "uq_workers_badge_barcode",
        "count": 2,
    }


def test_findings_are_truncated_but_counted(case: Case, run: Callable[..., Run]) -> None:
    """T-1."""
    case.execute("INSERT INTO part_numbers (part_number) VALUES ('PNá1'), ('PNé3'), ('PNí1')")
    result = run(case.url, "--max-findings", "2")
    _assert_failing(result, {"j": {"PN_NOT_CANONICAL"}})
    check = _checks(result.report)["j"]
    assert check["finding_count"] == 3
    assert len(check["findings"]) == 2
    assert check["truncated"] is True


# ---------------------------------------------------------------------------
# Run-level and check-level failures
# ---------------------------------------------------------------------------


def test_a_write_is_refused_by_the_read_only_transaction(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-1."""
    before = case.scalar("SELECT count(*) FROM audit_events")

    def writing(context: Any) -> Any:
        context.session.execute(
            sa.text(
                "INSERT INTO audit_events (event_type, entity_type, entity_id, occurred_at)"
                " VALUES ('CREATED', 'WorkOrder', '1', now())"
            )
        )
        raise AssertionError("the write was accepted")

    monkeypatch.setattr(reconciliation, "_check_i", writing)
    result = run(case.url)
    checks = _checks(result.report)
    assert checks["i"]["status"] == "error"
    assert checks["i"]["error_code"] == "read_only_violation"
    assert checks["i"]["reason"] == (
        "The check attempted a write and the read-only transaction refused it."
    )
    for check_id in ("a", "b", "c", "d", "e", "f", "j"):
        assert checks[check_id]["status"] == "pass", checks[check_id]
    assert result.exit_code == 2
    assert case.scalar("SELECT count(*) FROM audit_events") == before


def test_statement_timeout_errors_one_check(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-2."""

    def sleeping(context: Any) -> Any:
        context.session.execute(sa.text("SELECT pg_sleep(2)"))
        raise AssertionError("the statement timeout did not fire")

    monkeypatch.setattr(reconciliation, "_check_b", sleeping)
    result = run(case.url, "--statement-timeout", "1")
    checks = _checks(result.report)
    assert checks["b"]["status"] == "error"
    assert checks["b"]["error_code"] == "statement_timeout"
    assert checks["b"]["reason"] == "The check exceeded the statement timeout of 1 s."
    for check_id in ("c", "d", "e", "f", "i", "j"):
        assert checks[check_id]["status"] == "pass", checks[check_id]
    assert result.exit_code == 2
    assert result.report["options"]["statement_timeout_seconds"] == 1


def test_lock_timeout_behind_ddl(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-3."""
    monkeypatch.setattr(reconciliation, "LOCK_TIMEOUT_SECONDS", 1)
    with case.engine.connect() as holder:
        holder.execute(sa.text("LOCK TABLE quantity_flows IN ACCESS EXCLUSIVE MODE"))
        try:
            result = run(case.url)
        finally:
            holder.rollback()
    assert result.exit_code == 2
    assert result.report["checks"] == []
    assert result.report["error"] == {
        "code": "lock_timeout",
        "message": (
            "The run waited more than 1 s for a table lock. A migration or maintenance may be"
            " running. Nothing was checked."
        ),
    }
    assert result.report["options"]["lock_timeout_seconds"] == 1
    assert result.stderr.strip().endswith("Nothing was checked.")


def test_statement_timeout_while_waiting_for_a_table_lock(
    case: Case, run: Callable[..., Run]
) -> None:
    """N-3b: a statement timeout shorter than the lock timeout cancels
    LOCK TABLE first; that is a lock wait, never an unreachable database."""
    with case.engine.connect() as holder:
        holder.execute(sa.text("LOCK TABLE quantity_flows IN ACCESS EXCLUSIVE MODE"))
        try:
            result = run(case.url, "--statement-timeout", "1")
        finally:
            holder.rollback()
    assert result.exit_code == 2
    assert result.report["checks"] == []
    assert result.report["error"] == {
        "code": "lock_timeout",
        "message": (
            "The run waited more than 1 s for a table lock. A migration or maintenance may be"
            " running. Nothing was checked."
        ),
    }
    assert result.report["options"]["lock_timeout_seconds"] == 5


def test_connection_lost_at_the_advisory_guard_keeps_the_results(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-5b: the connection drops after the last check; the completed
    results stay in the report and the run reads as could-not-run."""
    original = reconciliation._held_locks

    def terminated(session: Session) -> reconciliation.HeldLocks:
        with case.engine.connect() as killer:
            killer.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity"
                    " WHERE application_name = 'partflow-reconcile' AND datname = :name"
                ),
                {"name": _CASE_DATABASE},
            )
        return original(session)

    monkeypatch.setattr(reconciliation, "_held_locks", terminated)
    result = run(case.url)
    checks = _checks(result.report)
    assert result.exit_code == 2
    assert result.report["error"] == {
        "code": "database_unavailable",
        "message": (
            "The connection to the PartFlow database was lost. The report is incomplete."
            " Nothing was repaired."
        ),
    }
    assert result.report["database"] is not None
    for check_id in _ALL_RUN:
        assert checks[check_id]["status"] == "pass", checks[check_id]
    assert checks["g"]["status"] == checks["h"]["status"] == "not_applicable"


def test_run_level_database_errors_are_classified_by_their_origin() -> None:
    """U-3: statement-level refusals are never "could not be reached"."""

    def wrapped(original: Exception, *, invalidated: bool = False) -> sa.exc.DBAPIError:
        return sa.exc.OperationalError(
            "LOCK TABLE", {}, original, connection_invalidated=invalidated
        )

    for refusal in (
        psycopg.errors.QueryCanceled("canceled"),
        psycopg.errors.LockNotAvailable("lock"),
        psycopg.errors.DeadlockDetected("deadlock"),
    ):
        assert not reconciliation._is_connection_failure(wrapped(refusal)), refusal
        assert reconciliation._is_connection_failure(wrapped(refusal, invalidated=True))
    assert reconciliation._is_connection_failure(wrapped(psycopg.OperationalError("refused")))
    assert reconciliation._is_connection_failure(
        sa.exc.InterfaceError("SELECT 1", {}, psycopg.InterfaceError("closed"))
    )
    assert reconciliation._lock_failure_message(
        wrapped(psycopg.errors.DeadlockDetected("deadlock")), 300
    ) == (
        "The run's table locks deadlocked with another session. A migration or maintenance may"
        " be running. Nothing was checked."
    )
    assert reconciliation._lock_failure_message(
        wrapped(psycopg.errors.QueryCanceled("canceled")), 3
    ) == (
        "The run waited more than 3 s for a table lock. A migration or maintenance may be"
        " running. Nothing was checked."
    )
    assert reconciliation._lock_failure_message(wrapped(psycopg.OperationalError("x")), 3) is None


def test_one_snapshot_for_every_check(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-4: a write committed during the run is invisible to it."""
    s = case.scenario
    original = projections.rebuild_current_positions
    committed: list[int] = []

    def concurrent(session: Session) -> dict[int, projections.CurrentPosition]:
        if not committed:
            flow = _insert_flow(case, part_number=_PN_A, quantity=1, area="MAT")
            _insert_movement(
                case,
                flow_id=flow,
                part_number=_PN_A,
                movement_type="RECEIVED",
                quantity=1,
                area="MAT",
                station=False,
                metadata=_received(case, "L1", 1),
            )
            committed.append(flow)
        return original(session)

    monkeypatch.setattr(projections, "rebuild_current_positions", concurrent)
    flows_before = case.scalar("SELECT count(*) FROM quantity_flows")
    first = run(case.url)
    assert first.exit_code == 0, json.dumps(first.report["checks"], indent=1)
    assert _checks(first.report)["a"]["examined"]["flows"] == flows_before
    second = run(case.url)
    assert second.exit_code == 0
    assert _checks(second.report)["a"]["examined"]["flows"] == flows_before + 1
    assert committed and s.flows


def test_unreachable_database(run: Callable[..., Run], scenario: Scenario) -> None:
    """N-5."""
    url = make_url(os.environ[_DB_URL_ENV]).set(database=_TEMPLATE_DATABASE, port=1)
    result = run(url)
    assert result.exit_code == 2
    assert result.report["checks"] == []
    assert result.report["database"] is None
    assert result.report["error"]["code"] == "database_unavailable"
    output = json.dumps(result.report) + result.stderr
    assert url.password is None or url.password not in output
    assert f"{url.host}:1" not in output


def test_missing_configuration(
    run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """N-6."""
    monkeypatch.chdir(tmp_path)
    result = run(None)
    assert result.exit_code == 2
    assert result.report["error"] == {
        "code": "configuration_invalid",
        "message": "DATABASE_URL is not set or the configuration is invalid. Nothing was checked.",
    }
    assert result.report["checks"] == []


@pytest.mark.parametrize(
    "database_url",
    [
        "postgres://partflow:s3cret-pw@db/partflow",  # unknown dialect name
        "postgresql+psycopg://partflow:s3cret-pw@db:notaport/partflow",  # bad port
        "s3cret-pw-garbage",  # not a URL
    ],
)
def test_malformed_database_url(
    database_url: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-6b: a malformed DATABASE_URL still yields one complete report and
    exit 2 (never a traceback with Python's status 1, which reads as a
    mismatch), and the report never repeats the URL or its password."""
    monkeypatch.setenv(_DB_URL_ENV, database_url)
    get_settings.cache_clear()
    capsys.readouterr()
    try:
        exit_code = cli.main(["reconcile"])
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 2
    assert (report["result"], report["exit_code"]) == ("error", 2)
    assert report["error"]["code"] == "configuration_invalid"
    assert report["checks"] == []
    assert report["runtime"]["alembic_head"] is not None
    assert "s3cret-pw" not in captured.out + captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "arguments",
    [["--check", "z"], ["--statement-timeout", "0"], ["--max-findings", "10001"]],
)
def test_usage_errors(arguments: list[str]) -> None:
    """N-7."""
    with pytest.raises(SystemExit) as raised:
        cli.main(["reconcile", *arguments])
    assert raised.value.code == 2


def test_unexpected_error_keeps_finished_results(
    case: Case, run: Callable[..., Run], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-8."""

    def broken(context: Any) -> Any:
        raise RuntimeError("defect in check e")

    monkeypatch.setattr(reconciliation, "_check_e", broken)
    result = run(case.url)
    checks = _checks(result.report)
    assert result.exit_code == 2
    assert result.report["error"]["code"] == "internal_error"
    for check_id in ("a", "b", "c", "d"):
        assert checks[check_id]["status"] == "pass"
    assert (checks["e"]["status"], checks["e"]["error_code"]) == ("error", "internal_error")
    for check_id in ("f", "i", "j"):
        assert (checks[check_id]["status"], checks[check_id]["error_code"]) == (
            "error",
            "not_run",
        )
    assert checks["g"]["status"] == checks["h"]["status"] == "not_applicable"
    assert "RuntimeError: defect in check e" in result.stderr
    assert "Traceback" in result.stderr


def _head_down_revision() -> tuple[str, str]:
    script = ScriptDirectory(str(_BACKEND_DIR / "alembic"))
    head = script.get_current_head()
    assert head is not None
    revision = script.get_revision(head)
    assert revision is not None and isinstance(revision.down_revision, str)
    return head, revision.down_revision


def test_schema_revision_mismatch(case: Case, run: Callable[..., Run]) -> None:
    """N-9."""
    head, previous = _head_down_revision()
    case.execute("UPDATE alembic_version SET version_num = :revision", revision=previous)
    result = run(case.url)
    checks = _checks(result.report)
    for check_id in ("a", "b", "c", "d", "e", "f", "i"):
        assert (checks[check_id]["status"], checks[check_id]["error_code"]) == (
            "error",
            "schema_mismatch",
        )
        assert checks[check_id]["reason"] == (
            f"The database is at revision {previous} but this code expects {head}."
            " Only check (j) runs across revisions."
        )
    assert checks["j"]["status"] == "pass"
    assert result.exit_code == 2
    assert run(case.url, "--check", "j").exit_code == 0


def test_missing_alembic_version_table(case: Case, run: Callable[..., Run]) -> None:
    """N-10."""
    case.execute("DROP TABLE alembic_version")
    result = run(case.url)
    database = result.report["database"]
    assert database["alembic_revision"] is None
    assert database["collation"] and database["transaction_read_only"] is True
    checks = _checks(result.report)
    for check_id in ("a", "b", "c", "d", "e", "f", "i"):
        assert checks[check_id]["error_code"] == "schema_mismatch"
        assert "revision none but" in checks[check_id]["reason"]
    assert checks["j"]["status"] == "pass"


def test_id_lookups_take_more_ids_than_bind_parameters(case: Case) -> None:
    """N-11: check (e) passes every demand id and the positions replay
    every active flow id; both lookups must stay correct past the
    protocol's 65,535 bind-parameter limit."""
    many = range(1, 70_001)
    with Session(case.engine) as session:
        demand_ids = list(session.scalars(sa.text("SELECT id FROM work_order_demands")))
        flow_ids = set(session.scalars(sa.text("SELECT id FROM quantity_flows")))
        assert max(demand_ids) < many.stop and max(flow_ids) < many.stop
        released = production_release.released_quantities(session, list(many))
        latest = projections._own_latest_position_bearing(session, set(many))
        assert released and released == production_release.released_quantities(session, demand_ids)
        assert latest and latest.keys() == (
            projections._own_latest_position_bearing(session, flow_ids).keys()
        )


# ---------------------------------------------------------------------------
# Static and pure checks
# ---------------------------------------------------------------------------


def test_hot_list_query_is_the_documented_one() -> None:
    """D-1: the check runs the DEPLOYMENT §5 query verbatim.

    The development backend container mounts ``docs/`` read-only at
    ``/docs`` (compose.yaml) for this test; it runs wherever the
    repository's ``docs/`` is reachable (Compose, CI, the host).
    """
    deployment = _BACKEND_DIR.parent / "docs" / "DEPLOYMENT.md"
    if not deployment.is_file():
        pytest.skip("docs/DEPLOYMENT.md is not reachable from this test environment")
    assert reconciliation.HOT_LIST_CHECK_SQL + ";" in deployment.read_text(encoding="utf-8")


def test_the_cli_imports_reconciliation() -> None:
    """S-1 (the rest is test_cli.py's B-STATIC)."""
    tree = ast.parse((_BACKEND_DIR / "app" / "cli.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.application"
        for alias in node.names
    }
    assert "reconciliation" in imported


def test_reconciliation_never_writes() -> None:
    """S-2."""
    source = (_BACKEND_DIR / "app" / "application" / "reconciliation.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(("fastapi", "app.api", "alembic")), alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(("fastapi", "app.api", "alembic")), node.module
            if node.module.startswith("sqlalchemy"):
                names = {alias.name for alias in node.names}
                assert not names & {"insert", "update", "delete"}, names
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "commit",
                "add",
                "add_all",
                "flush",
                "delete",
                "merge",
            }, node.func.attr
    for forbidden in ("pg_advisory", "with_for_update", "retention_period_months"):
        assert forbidden not in source


def test_identity_evaluators() -> None:
    """U-1."""
    assert reconciliation.part_number_identity_findings({"PN-1": ["part_numbers"]}) == []
    [eszett] = reconciliation.part_number_identity_findings({"PNß": ["quantity_flows"]})
    assert (eszett.code, eszett.expected, eszett.detail["changed_code_points"]) == (
        "PN_NOT_CANONICAL",
        "PNSS",
        ["U+00DF"],
    )
    [refused] = reconciliation.part_number_identity_findings({"A B": ["part_numbers"]})
    assert refused.expected is None and "whitespace" in str(refused.detail["error"])
    collision = reconciliation.part_number_identity_findings({"pn-x": ["a"], "PN-X": ["b"]})
    assert {finding.code for finding in collision} == {"PN_NOT_CANONICAL", "PN_CANONICAL_COLLISION"}
    [long_badge] = reconciliation.badge_identity_findings([(7, "ß" * 65, True)])
    assert long_badge.code == "BADGE_NOT_CANONICAL"
    assert long_badge.detail["error"] == "A badge barcode must be at most 128 characters."


def test_collation_version_evaluator() -> None:
    """U-2."""
    [finding] = reconciliation.collation_version_findings("db", "2.36", "2.39")
    assert (finding.code, finding.expected, finding.actual) == (
        "COLLATION_VERSION_MISMATCH",
        "2.36",
        "2.39",
    )
    assert reconciliation.collation_version_findings("db", "2.36", "2.36") == []
    assert reconciliation.collation_version_findings("db", None, "2.36") == []
    assert reconciliation.collation_version_findings("db", "2.36", None) == []
