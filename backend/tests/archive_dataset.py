"""A realistic, reusable data set for Movement-history archival and purge (Phase 16, design B).

Built through the real API in a database of its own, then moved back in time
(``occurred_at`` and ``server_received_at`` of the old components, the
Movement guard disabled inside the owner's setup transaction): releases
against demand lines (several on one line, archived and retained ones on one
line), stock, a partial transfer (a split), a merge, an Undo of a split, of
a merge and of a full Scrap, a full and a partial Scrap, a quantity addition,
Planned Routes from a template (archived and still active), a stock
allocation, still-active flows beside archived ones (in the same component and
on the same Part Number) and recent closed flows. ``scale`` repeats the whole
set with new Part Numbers, so a rehearsal can grow it.

``reader_figures`` reads every figure a purge must leave unchanged — through
the readers themselves and through the API — and ``reconcile_outcome`` every
reconcile check, so a test compares them before and after a purge.

The job helpers drive a run through the host job's commands the way
``deploy/production/archive.sh`` does (export, verify, copy, the pre-purge
backup's evidence, purge).
"""

import datetime
import math
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, cast

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.application import (
    allocations,
    history_archive,
    history_archive_job,
    production_release,
    projections,
    reconciliation,
    route_templates,
    scan_station,
)
from app.application.history_archive_job import JobFolders
from app.domain.enums import MovementType
from tests.auth_harness import admin_of
from tests.conftest import app_mode_roles, owner_engine

BACKDATE_MONTHS = 36
#: The scenario names whose whole component is closed and old: archived and purged.
ARCHIVED = (
    "stocked",
    "split",
    "merge",
    "undone_split",
    "undone_merge",
    "scrapped",
    "undone_scrap",
    "partial_scrap",
    "added",
    "planned",
    "multi_release",
    "mixed_line_old",
    "allocated",
)
#: Scenario names that stay in the database.
RETAINED = ("active", "recent", "mixed_line_recent", "planned_active", "beside")


def event_id() -> str:
    return str(uuid.uuid4())


def part_number(label: str) -> str:
    return f"AD-{label}-{uuid.uuid4().hex[:6].upper()}"


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

    # -- environment -------------------------------------------------------

    def environment(self, suffix: str = "") -> None:
        department = self.admin.post("/api/departments", json={"name": f"Archive{suffix}"})
        assert department.status_code == 201, department.text
        for name, terminal in (("MAT", False), ("DEBURR", False), ("STOCK", True)):
            area = self.admin.post(
                "/api/areas",
                json={
                    "department_id": department.json()["id"],
                    "name": f"{name}{suffix}",
                    "is_terminal": terminal,
                },
            )
            assert area.status_code == 201, area.text
            self.areas[name] = int(area.json()["id"])
            operation = self.admin.post(
                "/api/operations", json={"area_id": self.areas[name], "code": f"OP-{name}{suffix}"}
            )
            assert operation.status_code == 201, operation.text
            self.operations[name] = int(operation.json()["id"])
            station = self.admin.post(
                "/api/scan-stations",
                json={"station_id": f"ST-{name}{suffix}", "area_id": self.areas[name]},
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

    # -- demand and release ------------------------------------------------

    def work_order(self, lines: list[tuple[str, int]]) -> tuple[int, list[int]]:
        """A Work Order with one demand line per (PN, requested quantity)."""
        response = self.admin.post(
            "/api/work-orders",
            json={
                "lines": [
                    {"part_number": pn, "requested_quantity": quantity} for pn, quantity in lines
                ]
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        return int(body["id"]), [int(line["id"]) for line in body["demands"]]

    def release_line(
        self,
        work_order_id: int,
        demand_id: int,
        pn: str,
        quantity: int,
        route_template_id: int | None = None,
    ) -> int:
        response = self.admin.post(
            f"/api/work-orders/{work_order_id}/demands/{demand_id}/release",
            json={
                "part_number": pn,
                "quantity": quantity,
                "route_mode": "FLOATING" if route_template_id is None else "PLANNED",
                "route_template_id": route_template_id,
                "starting_area_id": self.areas["MAT"],
                "operation_id": self.operations["MAT"],
                "confirm_active_quantity": True,
                "device_event_id": event_id(),
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def release(self, pn: str, quantity: int, route_template_id: int | None = None) -> int:
        work_order, [demand] = self.work_order([(pn, 100)])
        return self.release_line(work_order, demand, pn, quantity, route_template_id)

    # -- Scan Station commands --------------------------------------------

    def arrival(
        self, action: str, source: str, target: str, flow_id: int, pn: str, quantity: int
    ) -> int:
        """A transfer or stocking; returns the flow that arrived (a split child when partial)."""
        self.last_event = event_id()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[target]}/{action}",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "source_area_id": self.areas[source],
                "target_area_id": self.areas[target],
                "quantity": quantity,
                "device_event_id": self.last_event,
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def transfer(self, source: str, target: str, flow_id: int, pn: str, quantity: int) -> int:
        return self.arrival("transfers", source, target, flow_id, pn, quantity)

    def stock(self, source: str, flow_id: int, pn: str, quantity: int) -> int:
        return self.arrival("stockings", source, "STOCK", flow_id, pn, quantity)

    def scrap(self, area: str, flow_id: int, pn: str, quantity: int) -> int:
        self.last_event = event_id()
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/scraps",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "quantity": quantity,
                "reason": "damaged during processing",
                "device_event_id": self.last_event,
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def undo(self, area: str, pn: str, command_id: str) -> None:
        response = self.client.post(
            f"/api/scan-stations/{self.stations[area]}/undos",
            json={
                "part_number": pn,
                "reverses_device_event_id": command_id,
                "device_event_id": event_id(),
            },
        )
        assert response.status_code == 201, response.text

    def merge(self, pn: str, flow_ids: list[int]) -> int:
        self.last_event = event_id()
        response = self.client.post(
            f"/api/scan-stations/{self.stations['MAT']}/merges",
            json={
                "part_number": pn,
                "quantity_flow_ids": flow_ids,
                "device_event_id": self.last_event,
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def add_quantity(self, pn: str, quantity: int) -> int:
        response = self.client.post(
            f"/api/scan-stations/{self.stations['MAT']}/quantity-additions",
            json={
                "part_number": pn,
                "quantity": quantity,
                "reason": "found on the rack",
                "operation_id": self.operations["MAT"],
                "device_event_id": event_id(),
            },
        )
        assert response.status_code == 201, response.text
        return int(response.json()["quantity_flow_id"])

    def allocate(self, pn: str, demand_id: int, quantity: int) -> None:
        """A Management allocation of stocked quantity to one demand line."""
        response = self.admin.post(
            "/api/allocations/management",
            json={
                "part_number": pn,
                "allocation_quantity": quantity,
                "lines": [{"work_order_demand_id": demand_id, "quantity": quantity}],
                "reason": "allocated for the archive data set",
                "device_event_id": event_id(),
            },
        )
        assert response.status_code in (200, 201), response.text

    # -- reads -------------------------------------------------------------

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

    def active_of(self, flows: Iterable[int]) -> int:
        """The one ACTIVE flow among ``flows``."""
        [flow] = self.column(
            "SELECT id FROM quantity_flows WHERE id = ANY(:ids) AND status = 'ACTIVE'",
            ids=sorted(flows),
        )
        return int(flow)


def backdate(owner: Engine, flow_ids: Iterable[int], months: int = BACKDATE_MONTHS) -> None:
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


@dataclass
class Dataset:
    builder: Builder
    owner: Engine
    #: Every flow of each scenario's component, by scenario name (and repetition).
    components: dict[str, set[int]] = field(default_factory=dict)
    part_numbers: dict[str, str] = field(default_factory=dict)
    template_ids: list[int] = field(default_factory=list)
    demand_ids: set[int] = field(default_factory=set)
    #: Commands a test may undo later: scenario name → (area, PN, command id).
    commands: dict[str, tuple[str, str, str]] = field(default_factory=dict)

    def flows(self, names: Iterable[str]) -> set[int]:
        found: set[int] = set()
        for name in names:
            for key, flows in self.components.items():
                if key == name or key.startswith(f"{name}#"):
                    found |= flows
        return found

    @property
    def all_part_numbers(self) -> list[str]:
        return sorted(set(self.part_numbers.values()))


def _scenarios(builder: Builder, data: Dataset, template: int, tag: str) -> list[str]:
    """One repetition of every scenario; returns the names to backdate."""

    def remember(name: str, pn: str, flow_id: int) -> None:
        key = f"{name}#{tag}"
        data.part_numbers[key] = pn
        data.components[key] = builder.component(flow_id)

    old: list[str] = []
    # stocked: released against a line and stocked whole.
    pn = part_number("STOCKED")
    flow = builder.release(pn, 5)
    builder.stock("MAT", flow, pn, 5)
    remember("stocked", pn, flow)
    old.append("stocked")
    # split: a partial transfer, both parts stocked.
    pn = part_number("SPLIT")
    flow = builder.release(pn, 10)
    moved = builder.transfer("MAT", "DEBURR", flow, pn, 4)
    builder.stock("DEBURR", moved, pn, 4)
    builder.stock("MAT", builder.active_of(builder.component(flow)), pn, 6)
    remember("split", pn, flow)
    old.append("split")
    # beside: the same PN released again later and still active (retained).
    beside = builder.release(pn, 2)
    remember("beside", pn, beside)
    # merge: two releases merged, the result stocked.
    pn = part_number("MERGE")
    first = builder.release(pn, 3)
    second = builder.release(pn, 2)
    merged = builder.merge(pn, [first, second])
    builder.stock("MAT", merged, pn, 5)
    remember("merge", pn, first)
    old.append("merge")
    # undone_split: a partial transfer undone, then the reopened flow stocked.
    pn = part_number("UNSPLIT")
    flow = builder.release(pn, 8)
    builder.transfer("MAT", "DEBURR", flow, pn, 3)
    builder.undo("DEBURR", pn, builder.last_event)
    builder.stock("MAT", flow, pn, 8)
    remember("undone_split", pn, flow)
    old.append("undone_split")
    # undone_merge: two releases merged, the merge undone, both sources stocked.
    pn = part_number("UNMERGE")
    first = builder.release(pn, 3)
    second = builder.release(pn, 4)
    builder.merge(pn, [first, second])
    builder.undo("MAT", pn, builder.last_event)
    builder.stock("MAT", first, pn, 3)
    builder.stock("MAT", second, pn, 4)
    remember("undone_merge", pn, first)
    old.append("undone_merge")
    # scrapped: released and scrapped whole.
    pn = part_number("SCRAP")
    flow = builder.release(pn, 4)
    builder.scrap("MAT", flow, pn, 4)
    remember("scrapped", pn, flow)
    old.append("scrapped")
    # undone_scrap: a full Scrap undone, the reopened flow stocked.
    pn = part_number("UNSCRAP")
    flow = builder.release(pn, 6)
    builder.scrap("MAT", flow, pn, 6)
    builder.undo("MAT", pn, builder.last_event)
    builder.stock("MAT", flow, pn, 6)
    remember("undone_scrap", pn, flow)
    old.append("undone_scrap")
    # partial_scrap: part of a flow scrapped (a split), the rest stocked.
    pn = part_number("PSCRAP")
    flow = builder.release(pn, 9)
    builder.scrap("MAT", flow, pn, 3)
    builder.stock("MAT", builder.active_of(builder.component(flow)), pn, 6)
    remember("partial_scrap", pn, flow)
    old.append("partial_scrap")
    # added: a quantity addition (no demand) beside a released flow, both stocked.
    pn = part_number("ADDED")
    released = builder.release(pn, 3)
    flow = builder.add_quantity(pn, 7)
    builder.stock("MAT", flow, pn, 7)
    builder.stock("MAT", released, pn, 3)
    remember("added", pn, flow)
    data.components[f"added#{tag}"] |= builder.component(released)
    old.append("added")
    # planned: released on a Planned Route from the template and moved along it.
    pn = part_number("PLANNED")
    flow = builder.release(pn, 6, template)
    builder.transfer("MAT", "DEBURR", flow, pn, 6)
    builder.stock("DEBURR", flow, pn, 6)
    remember("planned", pn, flow)
    old.append("planned")
    # planned_active: the same template, a recent release still on its way (retained).
    flow = builder.release(pn, 2, template)
    builder.transfer("MAT", "DEBURR", flow, pn, 2)
    remember("planned_active", pn, flow)
    # multi_release: two releases against one demand line, both stocked.
    pn = part_number("MULTI")
    work_order, [demand] = builder.work_order([(pn, 10)])
    data.demand_ids.add(demand)
    flow = builder.release_line(work_order, demand, pn, 4)
    builder.stock("MAT", flow, pn, 4)
    remember("multi_release", pn, flow)
    other = builder.release_line(work_order, demand, pn, 6)
    builder.stock("MAT", other, pn, 6)
    data.components[f"multi_release#{tag}"] |= builder.component(other)
    old.append("multi_release")
    # mixed_line: one demand line released once long ago and once recently.
    pn = part_number("MIXED")
    work_order, [demand] = builder.work_order([(pn, 9)])
    data.demand_ids.add(demand)
    flow = builder.release_line(work_order, demand, pn, 3)
    builder.stock("MAT", flow, pn, 3)
    remember("mixed_line_old", pn, flow)
    old.append("mixed_line_old")
    recent = builder.release_line(work_order, demand, pn, 2)
    builder.stock("MAT", recent, pn, 2)
    remember("mixed_line_recent", pn, recent)
    # allocated: stocked long ago and allocated (now) to a line of another Work Order.
    pn = part_number("ALLOC")
    flow = builder.release(pn, 8)
    builder.stock("MAT", flow, pn, 8)
    remember("allocated", pn, flow)
    old.append("allocated")
    _work_order, [line] = builder.work_order([(pn, 3)])
    data.demand_ids.add(line)
    builder.allocate(pn, line, 3)
    # active: a partial transfer whose remaining part is still active (whole
    # component retained, although its Movements are as old as the others).
    pn = part_number("ACTIVE")
    flow = builder.release(pn, 9)
    moved = builder.transfer("MAT", "DEBURR", flow, pn, 2)
    builder.stock("DEBURR", moved, pn, 2)
    remember("active", pn, flow)
    old.append("active")
    # recent: a closed flow of today (retained).
    pn = part_number("RECENT")
    flow = builder.release(pn, 1)
    builder.stock("MAT", flow, pn, 1)
    remember("recent", pn, flow)
    return old


def build_dataset(builder: Builder, owner: Engine, *, scale: int = 1) -> Dataset:
    """The data set (``scale`` repetitions), old components backdated."""
    data = Dataset(builder=builder, owner=owner)
    template = builder.route_template(["MAT", "DEBURR", "STOCK"])
    data.template_ids.append(template)
    for repetition in range(scale):
        old = _scenarios(builder, data, template, str(repetition))
        backdate(owner, data.flows(old))
    data.demand_ids |= {
        int(value)
        for value in builder.column(
            "SELECT id FROM work_order_demands WHERE part_number = ANY(:pns)",
            pns=data.all_part_numbers,
        )
    }
    return data


# ---------------------------------------------------------------------------
# What a purge must leave unchanged
# ---------------------------------------------------------------------------


def _sorted_items(mapping: dict[Any, Any]) -> list[tuple[Any, Any]]:
    return sorted(mapping.items(), key=lambda item: str(item[0]))


def reader_figures(data: Dataset) -> dict[str, Any]:
    """Every figure the readers derive from history, for the data set's PNs.

    Through the readers (as the application role in the application-role
    test mode) and through the API. Display of the history itself (the
    trace, the Movement list, route step states) is not a figure and
    changes by design.
    """
    builder = data.builder
    pns = data.all_part_numbers
    areas = sorted(builder.areas.values())
    flows = sorted(
        int(value)
        for value in builder.column(
            "SELECT id FROM quantity_flows WHERE part_number = ANY(:pns)", pns=pns
        )
    )
    figures: dict[str, Any] = {}
    with Session(builder.engine) as session:
        figures["released"] = _sorted_items(
            production_release.released_quantities(session, sorted(data.demand_ids))
        )
        figures["stocked"] = {pn: projections.stocked_quantity_of(session, pn) for pn in pns}
        figures["stock_position"] = {
            pn: tuple(allocations.stock_position_of(session, pn)) for pn in pns
        }
        figures["scrapped"] = {pn: scan_station.scrapped_quantity_of(session, pn) for pn in pns}
        figures["introduced"] = {
            pn: projections.effective_total_of(
                session, pn, (MovementType.RECEIVED, MovementType.QUANTITY_ADJUSTED)
            )
            for pn in pns
        }
        for kind in (MovementType.STOCKED, MovementType.SCRAPPED):
            totals = projections.effective_totals_by_area(session, kind, areas)
            figures[f"by_area_{kind}"] = sorted(
                (pn, area, quantity) for (pn, area), quantity in totals.items() if pn in pns
            )
        figures["usage_counts"] = _sorted_items(
            route_templates._usage_counts(session, data.template_ids)
        )
        figures["usage"] = {
            template: (
                usage.total,
                sorted((entry.quantity_flow_id, entry.part_number) for entry in usage.flows),
                sorted(entry.released_at.isoformat() for entry in usage.flows),
            )
            for template in data.template_ids
            for usage in [route_templates.route_template_usage(session, template)]
        }
        figures["consumed"] = sorted(set(projections.consumed_flow_ids(session)) & set(flows))
        figures["edges"] = sorted(
            edge.id for edge in projections.effective_lineage_edges(session, flows)
        )
        figures["positions"] = sorted(
            (flow_id, tuple(position))
            for flow_id, position in projections.rebuild_current_positions(session).items()
            if flow_id in flows
        )
        figures["statuses"] = sorted(
            (int(row.id), str(row.status))
            for row in session.execute(
                sa.text("SELECT id, status FROM quantity_flows WHERE id = ANY(:ids)"),
                {"ids": flows},
            )
        )
    figures["api"] = _api_figures(builder, pns)
    return figures


def _api_figures(builder: Builder, pns: list[str]) -> dict[str, Any]:
    admin = builder.admin
    found: dict[str, Any] = {}
    listing = admin.get("/api/tracking", params={"status": "ALL", "limit": 200})
    assert listing.status_code == 200, listing.text
    found["tracking_rows"] = sorted(
        (
            row["part_number"],
            row["status"],
            row["active_quantity"],
            row["stocked_quantity"],
            row["allocated_quantity"],
            row["available_stocked_quantity"],
            row["scrapped_quantity"],
            str(
                sorted(
                    (entry["area"]["id"], entry["quantity"], entry["stocked"])
                    for entry in row["distribution"]
                )
            ),
        )
        for row in listing.json()["rows"]
        if row["part_number"] in pns
    )
    details: dict[str, Any] = {}
    for pn in pns:
        response = admin.get("/api/tracking/detail", params={"part_number": pn, "flows_limit": 200})
        assert response.status_code == 200, response.text
        body = response.json()
        details[pn] = {
            "status": body["status"],
            "quantities": [
                body[key]
                for key in (
                    "active_quantity",
                    "stocked_quantity",
                    "allocated_quantity",
                    "available_stocked_quantity",
                    "scrapped_quantity",
                    "introduced_quantity",
                )
            ],
            "stocked": sorted(
                (entry["area"]["id"], entry["quantity"]) for entry in body["stocked"]
            ),
            "demands": sorted(
                (entry["work_order_demand_id"], entry["released_quantity"], entry["shortage"])
                for entry in body["demands"]
            ),
            "flows": sorted(
                (
                    flow["id"],
                    flow["status"],
                    flow["quantity"],
                    tuple(sorted(link["quantity_flow_id"] for link in flow["parents"])),
                    tuple(sorted(link["quantity_flow_id"] for link in flow["children"])),
                )
                for flow in body["flows"]["flows"]
            ),
            "locations": sorted(
                (entry["area"]["id"], entry["quantity"], entry["state"])
                for entry in body["locations"]
            ),
            "history_total": body["movements"]["total"] + body["movements"]["archived_total"] > 0,
        }
    found["tracking_details"] = details
    work_orders: dict[int, Any] = {}
    for work_order_id in builder.column(
        "SELECT DISTINCT work_order_id FROM work_order_demands WHERE part_number = ANY(:pns)",
        pns=pns,
    ):
        response = admin.get(f"/api/work-orders/{int(work_order_id)}")
        assert response.status_code == 200, response.text
        body = response.json()
        work_orders[int(work_order_id)] = (
            body["status"],
            body["completed_at"],
            sorted(
                (
                    line["id"],
                    line["released_quantity"],
                    line["remaining_quantity"],
                    line["has_released_quantity"],
                    line["allocated_quantity"],
                )
                for line in body["demands"]
            ),
        )
    found["work_orders"] = work_orders
    templates = admin.get("/api/route-templates/management")
    assert templates.status_code == 200, templates.text
    found["route_templates"] = sorted(
        (template["id"], template["usage_count"]) for template in templates.json()
    )
    board = admin.get("/api/production-board")
    assert board.status_code == 200, board.text
    body = board.json()
    found["production_board"] = (
        sorted(
            (
                row["part_number"],
                row["active_quantity"],
                row["stocked_quantity"],
                row["scrapped_quantity"],
                row["total_quantity"],
            )
            for row in body["rows"]
        ),
        [body[key] for key in ("active_quantity", "stocked_quantity", "scrapped_quantity")],
    )
    area_board = admin.get("/api/area-board")
    assert area_board.status_code == 200, area_board.text
    found["area_board"] = _without_times(area_board.json())
    return found


def _without_times(value: Any) -> Any:
    """A response without its instants (positions' entry times are not figures)."""
    if isinstance(value, dict):
        return {
            key: _without_times(item)
            for key, item in value.items()
            if key not in ("since", "expected_by", "entered_at")
        }
    if isinstance(value, list):
        return [_without_times(item) for item in value]
    return value


def reconcile_outcome(engine: Engine) -> dict[str, Any]:
    """Every reconcile check: status and findings (codes and entities)."""
    report = reconciliation.run_reconciliation(engine)
    assert report.error is None, report.error
    return {
        check.id: (
            check.status,
            sorted(
                (finding.code, finding.entity_type, str(finding.entity_id))
                for finding in check.findings
            ),
        )
        for check in report.checks
    }


# ---------------------------------------------------------------------------
# Driving the host job
# ---------------------------------------------------------------------------


def job_engine(url: URL) -> Engine:
    """The job's engine: the maintenance role in app mode, the owner otherwise."""
    engine = owner_engine(url, poolclass=NullPool)
    roles = app_mode_roles()
    if roles is not None:

        @event.listens_for(engine, "connect")
        def _maintenance(dbapi_connection: Any, record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET SESSION ROLE "{roles.maintenance}"')
            cursor.close()
            dbapi_connection.commit()

    return engine


def run_row(owner: Engine, run_id: int) -> dict[str, Any]:
    with owner.connect() as connection:
        return dict(
            connection.execute(
                sa.text("SELECT * FROM history_archive_runs WHERE id = :id"), {"id": run_id}
            )
            .mappings()
            .one()
        )


def done(report: history_archive_job.JobReport, result: str) -> None:
    document = history_archive_job.job_document(report)
    assert report.exit_code == history_archive_job.EXIT_DONE, document
    assert report.result == result, document


def request_run(builder: Builder, reason: str = "Purge old history", **body: Any) -> int:
    response = builder.admin.post("/api/history-archive/runs", json={"reason": reason, **body})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def copied_run(job: Engine, folders: JobFolders, run_id: int) -> None:
    """export → verify → copy (with the copy folder of ``folders``)."""
    done(history_archive_job.export(job, folders, run_id), "exported")
    done(history_archive_job.verify(job, folders, run_id), "verified")
    done(history_archive_job.copy(job, folders, run_id), "copied")


def backup_of(owner: Engine, run_id: int) -> history_archive_job.PurgeBackup:
    """The evidence of a pre-purge backup taken now: its name and dump start.

    The dump start is the first whole second after the verification; the
    helper waits until it has passed, as a real backup always starts after
    the verification and before the purge.
    """
    run = run_row(owner, run_id)
    verified: datetime.datetime = run["verified_at"]
    started = verified.replace(microsecond=0) + datetime.timedelta(seconds=1)
    wait = (started - datetime.datetime.now(datetime.UTC)).total_seconds()
    if wait > 0:
        time.sleep(math.ceil(wait * 100) / 100 + 0.01)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    return history_archive_job.PurgeBackup(
        f"{stamp}-pre-purge-{run['archive_name']}", started.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def purge_run(
    job: Engine, owner: Engine, folders: JobFolders, run_id: int
) -> history_archive_job.JobReport:
    """The pre-purge backup's evidence, then ``purge``."""
    return history_archive_job.purge(job, folders, run_id, backup_of(owner, run_id))


def movement_count(owner: Engine, flows: Iterable[int]) -> int:
    with owner.connect() as connection:
        return int(
            connection.execute(
                sa.text("SELECT count(*) FROM part_movements WHERE quantity_flow_id = ANY(:ids)"),
                {"ids": sorted(flows)},
            ).scalar_one()
        )


def default_cutoff() -> datetime.datetime:
    zone = history_archive.site_zone()
    today = history_archive.site_date_of(history_archive.now(), zone)
    return history_archive.start_of_day(history_archive.months_before(today, 24), zone)


# ---------------------------------------------------------------------------
# Driving a permanent deletion request (build step 3)
# ---------------------------------------------------------------------------

#: The deletion commands need neither the archive folder nor the copy folder.
NO_FOLDERS = JobFolders(None, None)


def deletion_backup_of(owner: Engine, request_id: int) -> history_archive_job.PurgeBackup:
    """The evidence of the pre-purge backup taken right after the request started."""
    with owner.connect() as connection:
        request = (
            connection.execute(
                sa.text("SELECT started_at, backup_label FROM deletion_requests WHERE id = :id"),
                {"id": request_id},
            )
            .mappings()
            .one()
        )
    started: datetime.datetime = request["started_at"].astimezone(datetime.UTC)
    return history_archive_job.PurgeBackup(
        f"{started.strftime('%Y%m%dT%H%M%SZ')}-pre-purge-{request['backup_label']}",
        started.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def carry_out_request(job: Engine, owner: Engine, request_id: int) -> history_archive_job.JobReport:
    """``start-deletion``, the pre-purge backup's evidence, ``delete`` (as archive.sh does)."""
    done(history_archive_job.start_deletion(job, NO_FOLDERS, request_id), "started")
    return history_archive_job.delete_request(
        job, NO_FOLDERS, request_id, deletion_backup_of(owner, request_id)
    )
