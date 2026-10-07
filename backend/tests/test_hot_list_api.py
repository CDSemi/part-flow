"""Integration tests for Phase 12 — Priority Management (the Hot list).

Exercises the full request path — FastAPI routes, the Application read
models and command, and PostgreSQL — against a dedicated temporary
database migrated to head by the real Alembic chain. Covered per
IMPLEMENTATION_ROADMAP Phase 12, PROJECT_PROFILE §14 / §21 / §28 and
GUI_DESIGN §8:

- the ONE command: ADD at the bottom, REMOVE closing the gap, MOVE_UP /
  MOVE_DOWN by exactly one position (the adjacent swap accepted as
  both), DRAG anywhere, UNDO / REDO of any single-entry delta; the
  ranks always dense 1..N (H1) and one audit row per changed rank with
  the identity snapshot;
- eligibility to join: active demand (``requested > allocated``) of an
  open Work Order — a completed Work Order and a fully allocated line
  of an open one are refused with nothing written;
- the automatic removal (Phase 12 follow-up, OD1 — invariant H2: no
  ranked demand is inactive): an allocation that fully allocates a
  ranked line (its Work Order completing included) and a Work Order
  save lowering it to its allocated quantity take it off the list in
  the same transaction, the ranks closing densely and every rank
  change audited with its cause; a reversal or a raised quantity
  re-adds nothing; a leftover inactive entry stays removable and
  movable through the command;
- the typed-confirmed removal of a Hot demand line (OD3): without the
  flag a 409 naming the current rank and zero writes, with it one
  transaction that takes the line off the list and deletes it, every
  other removal rule judged first;
- the lock order of every Hot-lock taker (a recorder of the advisory
  and row locks per path), the races of the automatic removal and the
  confirmed deletion against the Hot command, a release and an
  idempotency race at COMMIT, and a bounded concurrent smoke test;
- the optimistic precondition (stale ``expected_order`` → 409 with the
  current entries), idempotent replay and mismatched reuse — a replay
  rebuilt from the audit rows even after the line was deleted;
- the locks: two changes on one order (one winner, H1 kept), two
  identical submissions (one applies, one replays), a Hot add against
  a demand-line removal in both arrival orders, and a move against an
  allocation of the moved demand (no deadlock);
- the Department scope (several active → 409, none → 404, the
  distribution restricted to the Department's Areas);
- the candidate read (search, LIKE escaping, PN barcode with its own
  copy and no limit, exclusions, canonical order, already-listed
  count) and the list read (flags, PN-level distribution equal to the
  Production Board's, released quantity);
- the cross-consumer regression (the
  rank written by the command orders allocation, the Production Board,
  PN Tracking and the Area inventory) and the Work Order intake still
  rejecting ``priority_rank``.

The Hot list is one list for the whole module database, so every test
works against the order it reads (most start from an emptied list);
the API commits real transactions and the module database is dropped
afterwards.
"""

import contextlib
import datetime
import os
import re
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import allocations as allocations_service
from app.application import hot_list as hot_list_service
from app.application import hot_ranks, work_orders
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_hot_list_api"
_DB_URL_ENV = "DATABASE_URL"

_STALE = (
    "The Hot list was changed elsewhere. The current list is shown; review it and try"
    " again. Nothing was changed."
)
_MISSING = "This Work Order Demand no longer exists. Nothing was changed."
_INVALID = (
    "The requested change is not a single add, remove or move of one Hot entry."
    " Nothing was changed."
)
_RELEASED_REMOVAL = "Cannot remove: production quantity has already been released."
_ALLOCATED_REMOVAL = (
    "Cannot remove: stocked quantity has already been allocated to this demand line."
)
_LAST_LINE_REMOVAL = (
    "Cannot remove the last demand line: a Work Order always contains at least one Work"
    " Order Demand. Add the replacement line first, or leave the Work Order as it is."
)
_NOT_A_PN_BARCODE = (
    "This is not a Part Number barcode. Scan a PN barcode (PF:PN:…) or search by PN,"
    " Work Order Number or Job Number."
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
            yield station_device_client(test_client)
    finally:
        os.environ[_DB_URL_ENV] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    response = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "CD-", "digits": 4},
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _create_department(client: TestClient) -> int:
    response = admin_of(client).post("/api/departments", json={"name": _unique("DEPT")})
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _set_department_active(engine: Engine, department_id: int, active: bool) -> None:
    """Direct write: a Department with active Areas cannot be deactivated
    through the API, and these tests need exactly that configuration."""
    with engine.begin() as connection:
        connection.execute(
            sa.update(models.Department)
            .where(models.Department.id == department_id)
            .values(is_active=active)
        )


class _Cell:
    """An Area of one Department with one Operation, one Scan Station and
    ``machine_count`` Machines."""

    def __init__(
        self,
        client: TestClient,
        department_id: int,
        *,
        machine_count: int = 0,
        is_terminal: bool = False,
    ) -> None:
        area = admin_of(client).post(
            "/api/areas",
            json={
                "department_id": department_id,
                "name": _unique("AREA"),
                "is_terminal": is_terminal,
                "color": "var(--a-test)",
            },
        )
        assert area.status_code == 201, area.text
        self.area_id = int(area.json()["id"])
        operation = admin_of(client).post(
            "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
        )
        assert operation.status_code == 201, operation.text
        self.operation_id = int(operation.json()["id"])
        station = admin_of(client).post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
        )
        assert station.status_code == 201, station.text
        self.station_id = str(station.json()["station_id"])
        self.machine_ids: list[int] = []
        for _ in range(machine_count):
            machine = admin_of(client).post(
                "/api/machines", json={"area_id": self.area_id, "name": _unique("Lathe")}
            )
            assert machine.status_code == 201, machine.text
            self.machine_ids.append(int(machine.json()["id"]))


class _Shop:
    """The ONE active Department of the module: a direct-processing
    Area, a Machine Area and the Stockroom."""

    def __init__(self, client: TestClient) -> None:
        self.department_id = _create_department(client)
        self.material = _Cell(client, self.department_id)
        self.lathe = _Cell(client, self.department_id, machine_count=1)
        self.stockroom = _Cell(client, self.department_id, is_terminal=True)


@pytest.fixture(scope="module")
def shop(client: TestClient) -> _Shop:
    return _Shop(client)


class _WorkOrder:
    def __init__(self, body: dict[str, Any]) -> None:
        self.id = int(body["id"])
        self.number = body["work_order_number"]
        self.demand_ids = [int(line["id"]) for line in body["demands"]]

    @property
    def demand_id(self) -> int:
        return self.demand_ids[0]


def _line(pn: str, quantity: int = 10, **fields: Any) -> dict[str, Any]:
    return {"part_number": pn, "requested_quantity": quantity, **fields}


def _work_order(
    client: TestClient,
    lines: list[dict[str, Any]],
    *,
    number: str | None = None,
    received_date: str | None = None,
) -> _WorkOrder:
    payload: dict[str, Any] = {"lines": lines}
    if number is not None:
        payload["work_order_number"] = number
    if received_date is not None:
        payload["received_date"] = received_date
    response = admin_of(client).post("/api/work-orders", json=payload)
    assert response.status_code == 201, response.text
    return _WorkOrder(response.json())


def _demand(client: TestClient, pn: str | None = None, quantity: int = 10) -> int:
    """One fresh eligible demand on its own numbered Work Order."""
    work_order = _work_order(client, [_line(pn or _unique("PN"), quantity)], number=_unique("WO"))
    return work_order.demand_id


def _release_response(
    client: TestClient,
    cell: _Cell,
    work_order: _WorkOrder,
    demand_id: int,
    pn: str,
    quantity: int,
) -> Any:
    return admin_of(client).post(
        f"/api/work-orders/{work_order.id}/demands/{demand_id}/release",
        json={
            "part_number": pn,
            "quantity": quantity,
            "route_mode": "FLOATING",
            "starting_area_id": cell.area_id,
            "operation_id": cell.operation_id,
            "confirm_active_quantity": True,
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _release(
    client: TestClient,
    cell: _Cell,
    work_order: _WorkOrder,
    demand_id: int,
    pn: str,
    quantity: int,
) -> int:
    released = _release_response(client, cell, work_order, demand_id, pn, quantity)
    assert released.status_code == 201, released.text
    return int(released.json()["quantity_flow_id"])


def _stock(client: TestClient, shop: _Shop, flow_id: int, pn: str, quantity: int) -> None:
    stocked = client.post(
        f"/api/scan-stations/{shop.stockroom.station_id}/stockings",
        json={
            "part_number": pn,
            "quantity_flow_id": flow_id,
            "source_area_id": shop.material.area_id,
            "target_area_id": shop.stockroom.area_id,
            "quantity": quantity,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert stocked.status_code == 201, stocked.text


def _allocate(
    client: TestClient,
    pn: str,
    lines: list[tuple[int, int]],
    *,
    device_event_id: str | None = None,
    station_id: str | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "part_number": pn,
        "allocation_quantity": sum(quantity for _, quantity in lines),
        "lines": [
            {"work_order_demand_id": demand_id, "quantity": quantity}
            for demand_id, quantity in lines
        ],
        "device_event_id": device_event_id or str(uuid.uuid4()),
    }
    if station_id is not None:
        payload["station_id"] = station_id
        return client.post("/api/allocations", json=payload)
    return admin_of(client).post("/api/allocations/management", json=payload)


def _stocked(
    client: TestClient, shop: _Shop, work_order: _WorkOrder, demand_id: int, pn: str, quantity: int
) -> None:
    """Release ``quantity`` for the demand and stock it — not allocated yet."""
    flow_id = _release(client, shop.material, work_order, demand_id, pn, quantity)
    _stock(client, shop, flow_id, pn, quantity)


def _fulfil(
    client: TestClient, shop: _Shop, work_order: _WorkOrder, demand_id: int, pn: str, quantity: int
) -> None:
    """Release ``quantity`` for the demand, stock it and allocate it back
    to the demand — the line becomes fully allocated when ``quantity``
    is its requested quantity (and the Work Order completes when that
    was its last open line)."""
    flow_id = _release(client, shop.material, work_order, demand_id, pn, quantity)
    _stock(client, shop, flow_id, pn, quantity)
    allocated = _allocate(client, pn, [(demand_id, quantity)])
    assert allocated.status_code == 201, allocated.text


# ---------------------------------------------------------------------------
# Hot list helpers
# ---------------------------------------------------------------------------


def _hot_list(client: TestClient) -> dict[str, Any]:
    response = admin_of(client).get("/api/hot-list")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _order(client: TestClient) -> list[int]:
    return [int(entry["work_order_demand_id"]) for entry in _hot_list(client)["entries"]]


def _entry(client: TestClient, demand_id: int) -> dict[str, Any]:
    found = [
        entry
        for entry in _hot_list(client)["entries"]
        if entry["work_order_demand_id"] == demand_id
    ]
    assert len(found) == 1, found
    return cast(dict[str, Any], found[0])


def _change(
    client: TestClient,
    action: str,
    expected: list[int],
    new: list[int],
    *,
    device_event_id: str | None = None,
) -> Any:
    return admin_of(client).post(
        "/api/hot-list/changes",
        json={
            "device_event_id": device_event_id or str(uuid.uuid4()),
            "action": action,
            "expected_order": expected,
            "new_order": new,
        },
    )


def _add(client: TestClient, demand_id: int, **kw: Any) -> Any:
    current = _order(client)
    return _change(client, "ADD", current, [*current, demand_id], **kw)


def _added(client: TestClient, *demand_ids: int) -> None:
    for demand_id in demand_ids:
        response = _add(client, demand_id)
        assert response.status_code == 201, response.text


def _remove(client: TestClient, demand_id: int, **kw: Any) -> Any:
    current = _order(client)
    return _change(client, "REMOVE", current, [d for d in current if d != demand_id], **kw)


def _clear(client: TestClient) -> None:
    for demand_id in _order(client):
        response = _remove(client, demand_id)
        assert response.status_code == 201, response.text
    assert _order(client) == []


def _ranks(engine: Engine) -> dict[int, int]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(models.WorkOrderDemand.id, models.WorkOrderDemand.priority_rank).where(
                models.WorkOrderDemand.priority_rank.is_not(None)
            )
        )
        return {int(demand_id): int(rank) for demand_id, rank in rows}


def _assert_dense(engine: Engine) -> None:
    """Invariant H1: the ranked demands carry exactly the ranks 1..N."""
    ranks = sorted(_ranks(engine).values())
    assert ranks == list(range(1, len(ranks) + 1))


def _assert_no_inactive_entry(engine: Engine) -> None:
    """Invariant H2: no ranked demand is inactive — the read-only pre-deploy
    check query of the follow-up spec (§6) returns no row."""
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT d.id, d.priority_rank FROM work_order_demands d"
                " JOIN work_orders w ON w.id = d.work_order_id"
                " WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL"
                " OR d.requested_quantity <= d.allocated_quantity)"
            )
        ).all()
    assert rows == []


def _set_rank(engine: Engine, demand_id: int, rank: int | None) -> None:
    """Test-only direct write: a leftover from before the automatic
    removal, or non-dense ranks only out-of-band SQL can produce."""
    with engine.begin() as connection:
        connection.execute(
            sa.update(models.WorkOrderDemand)
            .where(models.WorkOrderDemand.id == demand_id)
            .values(priority_rank=rank)
        )


def _unrank_all(engine: Engine) -> None:
    """Test-only cleanup of out-of-band ranks the command cannot read as dense."""
    with engine.begin() as connection:
        connection.execute(
            sa.update(models.WorkOrderDemand)
            .where(models.WorkOrderDemand.priority_rank.is_not(None))
            .values(priority_rank=None)
        )


def _audit_mark(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(models.AuditEvent.id), 0))
            ).scalar_one()
        )


def _hot_rows_since(engine: Engine, mark: int) -> list[models.AuditEvent]:
    """Every rank audit row (command or automatic) appended after ``mark``."""
    with Session(engine) as session:
        rows = session.scalars(
            sa.select(models.AuditEvent)
            .where(
                models.AuditEvent.id > mark,
                models.AuditEvent.entity_type == "WorkOrderDemand",
            )
            .order_by(models.AuditEvent.id)
        )
        return [row for row in rows if "hot_list_change" in (row.metadata_ or {})]


def _block(row: models.AuditEvent) -> dict[str, Any]:
    assert row.metadata_ is not None
    return cast(dict[str, Any], row.metadata_["hot_list_change"])


def _rank_change(row: models.AuditEvent) -> tuple[int, int | None, int | None]:
    return (
        int(row.entity_id),
        (row.before_data or {}).get("priority_rank"),
        (row.after_data or {}).get("priority_rank"),
    )


def _assert_removal_rows(
    rows: list[models.AuditEvent],
    *,
    action: str,
    cause: dict[str, Any],
    changes: list[tuple[int, int | None, int | None]],
) -> None:
    """One transaction's rank rows outside the command (follow-up §7):
    ordered by ``sequence`` = new rank ascending, removals last, then id;
    the identical cause on every row; no command idempotency keys."""
    ordered = sorted(rows, key=lambda row: int(_block(row)["sequence"]))
    assert [_rank_change(row) for row in ordered] == changes
    for sequence, row in enumerate(ordered, start=1):
        block = _block(row)
        assert row.event_type == "UPDATED"
        assert row.entity_type == "WorkOrderDemand"
        assert row.actor_reference is None
        assert block["action"] == action
        assert block["sequence"] == sequence
        assert block["cause"] == cause
        assert block["work_order_demand_id"] == int(row.entity_id)
        assert "device_event_id" not in block and "fingerprint" not in block
        assert {"part_number", "work_order_id", "work_order_number"} <= set(block)


# ---------------------------------------------------------------------------
# Lock-order recorder (follow-up §9.6 D1)
# ---------------------------------------------------------------------------

#: The global lock order (follow-up §4.1): PN advisory → Hot advisory →
#: Scan Station row → demand rows → Work Order rows.
_LOCK_ORDER: dict[str, int] = {"A1": 1, "A2": 2, "R1": 3, "R2": 4, "R3": 5}
_FOR_UPDATE = re.compile(r"\bFOR (UPDATE|KEY SHARE)\b")


@contextlib.contextmanager
def _recording() -> Iterator[list[tuple[str, int | None]]]:
    """Record, in execution order, every advisory lock and every
    ``FOR UPDATE`` / ``FOR KEY SHARE`` row lock the API takes while the
    block runs (class-level listener: the app owns its own engine)."""
    locks: list[tuple[str, int | None]] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        text = " ".join(statement.split())
        values = list(parameters.values()) if isinstance(parameters, dict) else []
        if "pg_advisory_xact_lock" in text:
            for value in values:
                if isinstance(value, str) and value.startswith("partflow:part-number:"):
                    locks.append(("A1", None))
                elif value == "partflow:hot-list":
                    locks.append(("A2", None))
            return
        if not _FOR_UPDATE.search(text):
            return
        ids = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
        if re.search(r"\bFROM scan_stations\b", text):
            locks.append(("R1", None))
        elif re.search(r"\bFROM work_order_demands\b", text):
            locks.append(("R2", ids[0] if ids else None))
        elif re.search(r"\bFROM work_orders\b", text):
            locks.append(("R3", None))

    event.listen(Engine, "before_cursor_execute", listener)
    try:
        yield locks
    finally:
        event.remove(Engine, "before_cursor_execute", listener)


def _classes(locks: list[tuple[str, int | None]]) -> list[str]:
    return [kind for kind, _ in locks]


def _assert_lock_order(locks: list[tuple[str, int | None]], *, hot_lock: bool) -> list[int]:
    """The class sequence never goes back in the global order, the demand
    rows are ONE contiguous strictly ascending run, and the Hot lock is
    taken exactly when expected. Returns the demand ids in lock order."""
    classes = _classes(locks)
    assert classes, "no lock recorded"
    ranks = [_LOCK_ORDER[kind] for kind in classes]
    assert ranks == sorted(ranks), classes
    assert ("A2" in classes) is hot_lock, classes
    assert classes.count("A2") <= 1, classes
    demand_positions = [index for index, kind in enumerate(classes) if kind == "R2"]
    if demand_positions:
        assert demand_positions == list(range(demand_positions[0], demand_positions[-1] + 1)), (
            classes
        )
    demand_ids = [cast(int, demand_id) for kind, demand_id in locks if kind == "R2"]
    assert demand_ids == sorted(set(demand_ids)), demand_ids
    return demand_ids


def _rank_of(engine: Engine, demand_id: int) -> int | None:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.WorkOrderDemand.priority_rank).where(
                models.WorkOrderDemand.id == demand_id
            )
        ).scalar_one()


def _demand_exists(engine: Engine, demand_id: int) -> bool:
    with engine.connect() as connection:
        found = connection.execute(
            sa.select(models.WorkOrderDemand.id).where(models.WorkOrderDemand.id == demand_id)
        ).scalar_one_or_none()
    return found is not None


# Environment and Machine configuration edits a test makes between two
# counts are audited too (Phase 13); these counts guard production and
# business writes, so they leave the configuration audit entities out.
_CONFIGURATION_AUDIT_ENTITIES = (
    "Department",
    "Area",
    "Operation",
    "ScanStation",
    "MachineAssetTagConfig",
    "Machine",
)


def _audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.count())
                .select_from(models.AuditEvent)
                .where(models.AuditEvent.entity_type.not_in(_CONFIGURATION_AUDIT_ENTITIES))
            ).scalar_one()
        )


def _audit_rows(engine: Engine, device_event_id: str) -> list[models.AuditEvent]:
    with Session(engine) as session:
        rows = session.scalars(
            sa.select(models.AuditEvent)
            .where(models.AuditEvent.entity_type == "WorkOrderDemand")
            .where(
                models.AuditEvent.metadata_["hot_list_change"]["device_event_id"].astext
                == device_event_id
            )
            .order_by(models.AuditEvent.id)
        )
        return list(rows)


def _change_line(
    demand_id: int, pn: str, number: str | None, previous: int | None, new: int | None
) -> dict[str, Any]:
    return {
        "work_order_demand_id": demand_id,
        "part_number": pn,
        "work_order_number": number,
        "previous_rank": previous,
        "new_rank": new,
    }


# ---------------------------------------------------------------------------
# ADD
# ---------------------------------------------------------------------------


def test_add_to_an_empty_list_ranks_first_and_audits_the_identity_snapshot(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    pn = _unique("PN")
    number = _unique("WO")
    work_order = _work_order(client, [_line(pn)], number=number)
    event_id = str(uuid.uuid4())

    added = _add(client, work_order.demand_id, device_event_id=event_id)
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["created"] is True
    assert body["action"] == "ADD"
    assert body["device_event_id"] == event_id
    assert body["changes"] == [_change_line(work_order.demand_id, pn, number, None, 1)]
    assert [entry["work_order_demand_id"] for entry in body["entries"]] == [work_order.demand_id]
    assert body["entries"][0]["rank"] == 1

    [row] = _audit_rows(db_engine, event_id)
    assert row.event_type == "UPDATED"
    assert row.entity_type == "WorkOrderDemand"
    assert row.entity_id == str(work_order.demand_id)
    assert row.actor_reference is None
    assert row.before_data == {"priority_rank": None}
    assert row.after_data == {"priority_rank": 1}
    assert row.metadata_ is not None
    snapshot = row.metadata_["hot_list_change"]
    assert snapshot["action"] == "ADD"
    assert snapshot["sequence"] == 1
    assert snapshot["work_order_demand_id"] == work_order.demand_id
    assert snapshot["part_number"] == pn
    assert snapshot["work_order_id"] == work_order.id
    assert snapshot["work_order_number"] == number
    assert len(snapshot["fingerprint"]) == 64

    # A second add goes to the bottom and changes only its own rank.
    second = _demand(client)
    added = _add(client, second)
    assert added.status_code == 201, added.text
    assert [line["work_order_demand_id"] for line in added.json()["changes"]] == [second]
    assert added.json()["changes"][0]["new_rank"] == 2
    assert _order(client) == [work_order.demand_id, second]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_add_refusals_write_nothing(client: TestClient, db_engine: Engine) -> None:
    _clear(client)
    listed = _demand(client)
    _added(client, listed)
    other = _demand(client)
    before = _audit_count(db_engine)

    # Not at the bottom.
    response = _change(client, "ADD", [listed], [other, listed])
    assert response.status_code == 422 and response.json()["detail"] == _INVALID
    # Already listed: a duplicate id in the new order.
    response = _change(client, "ADD", [listed], [listed, listed])
    assert response.status_code == 422 and response.json()["detail"] == _INVALID
    # Unknown demand.
    response = _change(client, "ADD", [listed], [listed, 999_999_999])
    assert response.status_code == 404 and response.json()["detail"] == _MISSING
    # Empty delta, and an action that does not fit the delta.
    assert _change(client, "MOVE_UP", [listed], [listed]).status_code == 422
    assert _change(client, "REMOVE", [listed], [listed, other]).status_code == 422
    # Shape: non-positive ids, non-integer ids, an unknown action, an
    # invalid key, an extra field.
    assert _change(client, "ADD", [listed], [listed, 0]).status_code == 422
    assert _change(client, "ADD", [listed], [listed, "7"]).status_code == 422  # type: ignore[list-item]
    assert _change(client, "PROMOTE", [listed], [listed, other]).status_code == 422
    response = _change(client, "ADD", [listed], [listed, other], device_event_id="not-a-uuid")
    assert response.status_code == 422
    extra = admin_of(client).post(
        "/api/hot-list/changes",
        json={
            "device_event_id": str(uuid.uuid4()),
            "action": "ADD",
            "expected_order": [listed],
            "new_order": [listed, other],
            "priority_rank": 1,
        },
    )
    assert extra.status_code == 422

    assert _audit_count(db_engine) == before
    assert _order(client) == [listed]
    assert _rank_of(db_engine, other) is None


def test_ids_outside_the_database_integer_range_are_malformed_input(
    client: TestClient, db_engine: Engine
) -> None:
    """An id PostgreSQL cannot bind is a 422 before any query, not a 500."""
    _clear(client)
    listed = _demand(client)
    _added(client, listed)
    before = _audit_count(db_engine)
    too_large = 2_147_483_648

    response = _change(client, "ADD", [listed], [listed, too_large])
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "new_order must be a list of Work Order Demand ids."
    response = _change(client, "REMOVE", [listed, too_large], [listed])
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "expected_order must be a list of Work Order Demand ids."
    # The largest id the column holds is a well-formed id of no demand.
    response = _change(client, "ADD", [listed], [listed, too_large - 1])
    assert response.status_code == 404 and response.json()["detail"] == _MISSING

    assert _audit_count(db_engine) == before
    assert _order(client) == [listed]


def test_add_refuses_a_completed_work_order(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    pn = _unique("PN")
    number = _unique("WO")
    work_order = _work_order(client, [_line(pn, 4)], number=number)
    _fulfil(client, shop, work_order, work_order.demand_id, pn, 4)
    before = _audit_count(db_engine)

    response = _add(client, work_order.demand_id)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        f"Work Order {number} is completed, so its demand cannot be added to the Hot list."
        " Nothing was changed."
    )
    # An Undo / Redo that would re-insert it is refused the same way.
    assert _change(client, "UNDO", [], [work_order.demand_id]).status_code == 409
    assert _audit_count(db_engine) == before
    assert _rank_of(db_engine, work_order.demand_id) is None


def test_add_refuses_a_fully_allocated_line_of_an_open_work_order(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    full_pn, open_pn = _unique("PN"), _unique("PN")
    # Internal Work Order: the copy names it "— (internal)".
    work_order = _work_order(client, [_line(full_pn, 4), _line(open_pn, 4)])
    full, still_open = work_order.demand_ids
    _fulfil(client, shop, work_order, full, full_pn, 4)
    before = _audit_count(db_engine)

    response = _add(client, full)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        f"{full_pn} on Work Order — (internal) is fully allocated (4 of 4 pcs), so there"
        " is nothing left to expedite. Nothing was changed."
    )
    assert _change(client, "REDO", [], [full]).status_code == 409
    assert _audit_count(db_engine) == before
    assert _rank_of(db_engine, full) is None
    # The open line of the same (open) Work Order is still eligible.
    assert _add(client, still_open).status_code == 201


# ---------------------------------------------------------------------------
# REMOVE / MOVE / DRAG / UNDO / REDO
# ---------------------------------------------------------------------------


def test_remove_closes_the_gap_and_audits_every_shifted_entry(
    client: TestClient, db_engine: Engine
) -> None:
    _clear(client)
    first, middle, last = _demand(client), _demand(client), _demand(client)
    _added(client, first, middle, last)
    event_id = str(uuid.uuid4())

    removed = _change(
        client, "REMOVE", [first, middle, last], [first, last], device_event_id=event_id
    )
    assert removed.status_code == 201, removed.text
    changes = removed.json()["changes"]
    # Sorted by the new rank, removals last.
    assert [(c["work_order_demand_id"], c["previous_rank"], c["new_rank"]) for c in changes] == [
        (last, 3, 2),
        (middle, 2, None),
    ]
    rows = _audit_rows(db_engine, event_id)
    assert {row.entity_id for row in rows} == {str(last), str(middle)}
    assert _order(client) == [first, last]
    assert _rank_of(db_engine, middle) is None
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_moves_are_one_position_and_drag_goes_anywhere(
    client: TestClient, db_engine: Engine
) -> None:
    _clear(client)
    a, b = _demand(client), _demand(client)
    _added(client, a, b)

    # The adjacent swap is accepted as MOVE_UP of b and as MOVE_DOWN of a.
    assert _change(client, "MOVE_UP", [a, b], [b, a]).status_code == 201
    assert _change(client, "MOVE_DOWN", [b, a], [a, b]).status_code == 201
    assert _order(client) == [a, b]

    c = _demand(client)
    _added(client, c)
    before = _audit_count(db_engine)
    # A move by two positions is not a MOVE_UP.
    assert _change(client, "MOVE_UP", [a, b, c], [c, a, b]).status_code == 422
    assert _change(client, "MOVE_DOWN", [a, b, c], [b, c, a]).status_code == 422
    assert _audit_count(db_engine) == before

    event_id = str(uuid.uuid4())
    dragged = _change(client, "DRAG", [a, b, c], [a, c, b], device_event_id=event_id)
    assert dragged.status_code == 201, dragged.text
    # Only the changed ranks are audited: a keeps rank 1.
    assert {row.entity_id for row in _audit_rows(db_engine, event_id)} == {str(b), str(c)}
    assert _change(client, "DRAG", [a, c, b], [b, a, c]).status_code == 201
    assert _order(client) == [b, a, c]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_undo_and_redo_reinsert_and_reapply(client: TestClient, db_engine: Engine) -> None:
    _clear(client)
    a, b, c = _demand(client), _demand(client), _demand(client)
    _added(client, a, b, c)
    assert _change(client, "REMOVE", [a, b, c], [a, c]).status_code == 201

    undone = _change(client, "UNDO", [a, c], [a, b, c])
    assert undone.status_code == 201, undone.text
    assert _order(client) == [a, b, c]
    assert _change(client, "REDO", [a, b, c], [a, c]).status_code == 201
    # An Undo of a drag moves the entry back.
    assert _change(client, "DRAG", [a, c], [c, a]).status_code == 201
    assert _change(client, "UNDO", [c, a], [a, c]).status_code == 201
    assert _order(client) == [a, c]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


# ---------------------------------------------------------------------------
# Automatic removal of an inactive entry (OD1)
# ---------------------------------------------------------------------------


def test_completing_allocation_removes_the_entry_automatically(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A2: a Stockroom allocation that completes the Work Order takes its
    ranked line off the list (reason WORK_ORDER_COMPLETED); the Work
    Order stays read-only. A leftover inactive entry — only producible
    on a database that predates the automatic removal — can still be
    moved and removed through the command (the recovery path)."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 4)], number=_unique("WO"))
    other = _demand(client)
    _added(client, work_order.demand_id, other)
    _stocked(client, shop, work_order, work_order.demand_id, pn, 4)
    mark = _audit_mark(db_engine)
    event_id = str(uuid.uuid4())

    allocated = _allocate(
        client,
        pn,
        [(work_order.demand_id, 4)],
        device_event_id=event_id,
        station_id=shop.stockroom.station_id,
    )
    assert allocated.status_code == 201, allocated.text
    assert allocated.json()["completed_work_order_ids"] == [work_order.id]
    assert _order(client) == [other]
    _assert_removal_rows(
        _hot_rows_since(db_engine, mark),
        action="AUTO_REMOVE",
        cause={
            "trigger": "ALLOCATION",
            "reference": {
                "device_event_id": event_id,
                "source": "STOCKROOM",
                "station_id": shop.stockroom.station_id,
            },
            "removed": [
                {"work_order_demand_id": work_order.demand_id, "reason": "WORK_ORDER_COMPLETED"}
            ],
        },
        changes=[(other, 2, 1), (work_order.demand_id, 1, None)],
    )
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)

    # The Work Order stays completed, read-only history.
    detail = admin_of(client).get(f"/api/work-orders/{work_order.id}")
    assert detail.status_code == 200 and detail.json()["status"] == "COMPLETED"
    edit = admin_of(client).patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "requested_quantity": 9}]},
    )
    assert edit.status_code == 409

    # A leftover inactive entry is listed, flagged, movable and removable.
    _set_rank(db_engine, work_order.demand_id, 2)
    entry = _entry(client, work_order.demand_id)
    assert (entry["work_order_completed"], entry["active"]) == (True, False)
    moved = _change(client, "MOVE_UP", [other, work_order.demand_id], [work_order.demand_id, other])
    assert moved.status_code == 201, moved.text
    removed = _remove(client, work_order.demand_id)
    assert removed.status_code == 201, removed.text
    changes = removed.json()["changes"]
    assert [(c["work_order_demand_id"], c["previous_rank"], c["new_rank"]) for c in changes] == [
        (other, 2, 1),
        (work_order.demand_id, 1, None),
    ]
    assert changes[-1] == _change_line(work_order.demand_id, pn, work_order.number, 1, None)
    assert _order(client) == [other]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_management_allocation_removes_a_fully_allocated_middle_entry(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A1: the ranked middle line (rank 2 of 3) of an open Work Order is
    fully allocated from Management — it leaves the list, the old #3
    becomes #2, both changes audited with the allocation as the cause;
    a replay of the allocation touches nothing."""
    _clear(client)
    pn = _unique("PN")
    number = _unique("WO")
    work_order = _work_order(client, [_line(pn, 4), _line(_unique("PN"), 4)], number=number)
    middle = work_order.demand_ids[0]
    first, last = _demand(client), _demand(client)
    _added(client, first, middle, last)
    _stocked(client, shop, work_order, middle, pn, 4)
    mark = _audit_mark(db_engine)
    event_id = str(uuid.uuid4())

    allocated = _allocate(client, pn, [(middle, 4)], device_event_id=event_id)
    assert allocated.status_code == 201, allocated.text
    assert allocated.json()["completed_work_order_ids"] == []
    assert _order(client) == [first, last]
    assert _ranks(db_engine) == {first: 1, last: 2}
    rows = _hot_rows_since(db_engine, mark)
    _assert_removal_rows(
        rows,
        action="AUTO_REMOVE",
        cause={
            "trigger": "ALLOCATION",
            "reference": {"device_event_id": event_id, "source": "MANAGEMENT", "station_id": None},
            "removed": [{"work_order_demand_id": middle, "reason": "FULLY_ALLOCATED"}],
        },
        changes=[(last, 3, 2), (middle, 2, None)],
    )
    removed_block = _block(next(row for row in rows if int(row.entity_id) == middle))
    assert removed_block["part_number"] == pn
    assert removed_block["work_order_id"] == work_order.id
    assert removed_block["work_order_number"] == number
    detail = admin_of(client).get(f"/api/work-orders/{work_order.id}").json()
    assert detail["status"] != "COMPLETED" and detail["completed_at"] is None

    before = _audit_count(db_engine)
    replay = _allocate(client, pn, [(middle, 4)], device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert _audit_count(db_engine) == before
    assert _order(client) == [first, last]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_partial_allocation_keeps_the_rank(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A3: shortage remains — the line stays active and ranked."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 10)], number=_unique("WO"))
    other = _demand(client)
    _added(client, work_order.demand_id, other)
    _stocked(client, shop, work_order, work_order.demand_id, pn, 4)
    mark = _audit_mark(db_engine)

    allocated = _allocate(client, pn, [(work_order.demand_id, 4)])
    assert allocated.status_code == 201, allocated.text
    assert _order(client) == [work_order.demand_id, other]
    assert _hot_rows_since(db_engine, mark) == []
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_one_allocation_removes_two_entries_with_one_shared_cause(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A4: one allocation fully allocates the ranks 1 and 3 of 4."""
    _clear(client)
    pn = _unique("PN")
    one = _work_order(client, [_line(pn, 2), _line(_unique("PN"), 2)], number=_unique("WO"))
    three = _work_order(client, [_line(pn, 2), _line(_unique("PN"), 2)], number=_unique("WO"))
    two, four = _demand(client), _demand(client)
    _added(client, one.demand_id, two, three.demand_id, four)
    _stocked(client, shop, one, one.demand_id, pn, 2)
    _stocked(client, shop, three, three.demand_id, pn, 2)
    mark = _audit_mark(db_engine)
    event_id = str(uuid.uuid4())

    allocated = _allocate(
        client, pn, [(one.demand_id, 2), (three.demand_id, 2)], device_event_id=event_id
    )
    assert allocated.status_code == 201, allocated.text
    assert _order(client) == [two, four]
    _assert_removal_rows(
        _hot_rows_since(db_engine, mark),
        action="AUTO_REMOVE",
        cause={
            "trigger": "ALLOCATION",
            "reference": {"device_event_id": event_id, "source": "MANAGEMENT", "station_id": None},
            "removed": [
                {"work_order_demand_id": one.demand_id, "reason": "FULLY_ALLOCATED"},
                {"work_order_demand_id": three.demand_id, "reason": "FULLY_ALLOCATED"},
            ],
        },
        changes=[(two, 2, 1), (four, 4, 2), (one.demand_id, 1, None), (three.demand_id, 3, None)],
    )
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_reversal_reopens_but_never_re_adds(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A5: the reversal makes the demand active again and reopens the
    Work Order; the entry is NOT re-ranked and no rank row is written."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 3)], number=_unique("WO"))
    other = _demand(client)
    _added(client, other, work_order.demand_id)
    _stocked(client, shop, work_order, work_order.demand_id, pn, 3)
    allocated = _allocate(client, pn, [(work_order.demand_id, 3)])
    assert allocated.status_code == 201, allocated.text
    assert _rank_of(db_engine, work_order.demand_id) is None
    mark = _audit_mark(db_engine)

    reversed_ = admin_of(client).post(
        f"/api/allocations/{allocated.json()['rows'][0]['allocation_id']}/reversals",
        json={"reason": "Counted wrong", "device_event_id": str(uuid.uuid4())},
    )
    assert reversed_.status_code == 201, reversed_.text
    assert reversed_.json()["reopened_work_order_ids"] == [work_order.id]
    assert admin_of(client).get(f"/api/work-orders/{work_order.id}").json()["status"] != "COMPLETED"
    assert _rank_of(db_engine, work_order.demand_id) is None
    assert _order(client) == [other]
    assert _hot_rows_since(db_engine, mark) == []
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_save_lowering_the_quantity_to_the_allocated_quantity_removes_the_entry(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A6: the save makes the line fully allocated — it leaves the list
    in the same transaction; a later raise re-adds nothing."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 10), _line(_unique("PN"), 5)], number=_unique("WO"))
    hot = work_order.demand_ids[0]
    first, last = _demand(client), _demand(client)
    _added(client, first, hot, last)
    _stocked(client, shop, work_order, hot, pn, 4)
    assert _allocate(client, pn, [(hot, 4)]).status_code == 201
    assert _rank_of(db_engine, hot) == 2
    mark = _audit_mark(db_engine)

    saved = admin_of(client).patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": hot, "requested_quantity": 4}]},
    )
    assert saved.status_code == 200, saved.text
    [line] = [d for d in saved.json()["demands"] if d["id"] == hot]
    assert (line["requested_quantity"], line["priority_rank"]) == (4, None)
    assert _order(client) == [first, last]
    rows = _hot_rows_since(db_engine, mark)
    _assert_removal_rows(
        rows,
        action="AUTO_REMOVE",
        cause={
            "trigger": "WORK_ORDER_SAVE",
            "reference": {"work_order_id": work_order.id},
            "removed": [{"work_order_demand_id": hot, "reason": "FULLY_ALLOCATED"}],
        },
        changes=[(last, 3, 2), (hot, 2, None)],
    )
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)

    mark = _audit_mark(db_engine)
    raised = admin_of(client).patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": hot, "requested_quantity": 6}]},
    )
    assert raised.status_code == 200, raised.text
    assert _rank_of(db_engine, hot) is None
    assert _hot_rows_since(db_engine, mark) == []
    assert _order(client) == [first, last]


def test_a_save_keeping_a_shortage_or_editing_only_the_due_date_keeps_the_rank(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A7: a lowered quantity that keeps a shortage changes no rank, and a
    due-date-only save of a ranked line takes no Hot lock at all."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 10), _line(_unique("PN"), 5)], number=_unique("WO"))
    hot = work_order.demand_ids[0]
    other = _demand(client)
    _added(client, hot, other)
    _stocked(client, shop, work_order, hot, pn, 4)
    assert _allocate(client, pn, [(hot, 4)]).status_code == 201
    mark = _audit_mark(db_engine)

    lowered = admin_of(client).patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": hot, "requested_quantity": 6}]},
    )
    assert lowered.status_code == 200, lowered.text
    with _recording() as locks:
        dated = admin_of(client).patch(
            f"/api/work-orders/{work_order.id}",
            json={"line_edits": [{"id": hot, "due_date": "2031-02-03"}]},
        )
    assert dated.status_code == 200, dated.text
    assert "A2" not in _classes(locks)
    assert _order(client) == [hot, other]
    assert _hot_rows_since(db_engine, mark) == []
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_save_removes_only_a_line_whose_quantity_it_lowered(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A7b: a leftover ranked line that is already fully allocated (only
    possible from before the automatic removal) is edited for its due
    date in the same save that lowers ANOTHER line's quantity. The save
    takes the Hot lock for that quantity edit, but it did not make the
    leftover inactive: the leftover keeps its rank and no rank row names
    the save as its cause."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 5), _line(_unique("PN"), 10)], number=_unique("WO"))
    leftover, open_line = work_order.demand_ids
    other = _demand(client)
    _fulfil(client, shop, work_order, leftover, pn, 5)
    try:
        _set_rank(db_engine, leftover, 1)
        _set_rank(db_engine, other, 2)
        mark = _audit_mark(db_engine)

        with _recording() as locks:
            saved = admin_of(client).patch(
                f"/api/work-orders/{work_order.id}",
                json={
                    "line_edits": [
                        {"id": leftover, "due_date": "2031-02-03"},
                        {"id": open_line, "requested_quantity": 8},
                    ]
                },
            )
        assert saved.status_code == 200, saved.text
        _assert_lock_order(locks, hot_lock=True)
        lines = {d["id"]: d for d in saved.json()["demands"]}
        assert (lines[leftover]["due_date"], lines[leftover]["priority_rank"]) == (
            "2031-02-03",
            1,
        )
        assert lines[open_line]["requested_quantity"] == 8
        assert _ranks(db_engine) == {leftover: 1, other: 2}
        assert _hot_rows_since(db_engine, mark) == []
    finally:
        _unrank_all(db_engine)


def test_a_receipt_raising_a_ranked_internal_line_keeps_its_rank(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A8: a Scan Station receipt reusing an internal MODIFY line raises
    its requested quantity — the rank stays. Under H2 only a leftover can
    be ranked while fully allocated (the state a receipt reuses), so the
    rank is set test-only."""
    _clear(client)
    pn = _unique("PN")
    created = admin_of(client).post(
        "/api/work-orders",
        json={
            "lines": [
                {"part_number": pn, "requested_quantity": 4, "request_type": "MODIFY"},
                {"part_number": _unique("PN"), "requested_quantity": 5, "request_type": "MODIFY"},
            ]
        },
    )
    assert created.status_code == 201, created.text
    work_order = _WorkOrder(created.json())
    line = work_order.demand_ids[0]
    _fulfil(client, shop, work_order, line, pn, 4)
    _set_rank(db_engine, line, 1)
    mark = _audit_mark(db_engine)

    received = client.post(
        f"/api/scan-stations/{shop.material.station_id}/receipts",
        json={
            "part_number": pn,
            "quantity": 3,
            "request_type": "MODIFY",
            "route_mode": "FLOATING",
            "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert received.status_code == 201, received.text
    detail = admin_of(client).get(f"/api/work-orders/{work_order.id}").json()
    [reused] = [d for d in detail["demands"] if d["id"] == line]
    assert (reused["requested_quantity"], reused["priority_rank"]) == (7, 1)
    assert _hot_rows_since(db_engine, mark) == []
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_non_dense_ranks_close_relative_to_the_stored_ranks(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A9: ranks {1, 3, 4} (only out-of-band SQL produces them); removing
    3 leaves {1, 3}: only the old #4 shifts and the #1 row is untouched."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 2), _line(_unique("PN"), 2)], number=_unique("WO"))
    target = work_order.demand_ids[0]
    top, bottom = _demand(client), _demand(client)
    _stocked(client, shop, work_order, target, pn, 2)
    try:
        _set_rank(db_engine, top, 1)
        _set_rank(db_engine, target, 3)
        _set_rank(db_engine, bottom, 4)
        mark = _audit_mark(db_engine)

        allocated = _allocate(client, pn, [(target, 2)])
        assert allocated.status_code == 201, allocated.text
        assert _ranks(db_engine) == {top: 1, bottom: 3}
        rows = _hot_rows_since(db_engine, mark)
        assert sorted(_rank_change(row) for row in rows) == sorted(
            [(bottom, 4, 3), (target, 3, None)]
        )
    finally:
        _unrank_all(db_engine)


def test_a_shifted_command_line_has_its_own_row_and_is_not_a_removal(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A10: PN X on WO-1 at #1, PN Y at #2, PN X on WO-2 at #3; one
    allocation of X fully allocates the #1 line and partly the #3 line —
    the #3 line shifts to #2 with its own row and is no removal."""
    _clear(client)
    pn = _unique("PN")
    first = _work_order(client, [_line(pn, 2), _line(_unique("PN"), 2)], number=_unique("WO"))
    second = _work_order(client, [_line(pn, 5)], number=_unique("WO"))
    other = _demand(client)
    _added(client, first.demand_id, other, second.demand_id)
    _stocked(client, shop, first, first.demand_id, pn, 2)
    _stocked(client, shop, second, second.demand_id, pn, 1)
    mark = _audit_mark(db_engine)
    event_id = str(uuid.uuid4())

    allocated = _allocate(
        client, pn, [(first.demand_id, 2), (second.demand_id, 1)], device_event_id=event_id
    )
    assert allocated.status_code == 201, allocated.text
    assert _order(client) == [other, second.demand_id]
    _assert_removal_rows(
        _hot_rows_since(db_engine, mark),
        action="AUTO_REMOVE",
        cause={
            "trigger": "ALLOCATION",
            "reference": {"device_event_id": event_id, "source": "MANAGEMENT", "station_id": None},
            "removed": [{"work_order_demand_id": first.demand_id, "reason": "FULLY_ALLOCATED"}],
        },
        changes=[(other, 2, 1), (second.demand_id, 3, 2), (first.demand_id, 1, None)],
    )
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


# ---------------------------------------------------------------------------
# Precondition and idempotency
# ---------------------------------------------------------------------------


def test_a_stale_expected_order_is_refused_with_the_current_entries(
    client: TestClient, db_engine: Engine
) -> None:
    _clear(client)
    listed, latecomer = _demand(client), _demand(client)
    _added(client, listed)
    before = _audit_count(db_engine)

    stale = _change(client, "ADD", [], [latecomer])
    assert stale.status_code == 409, stale.text
    body = stale.json()
    assert body["detail"] == _STALE
    assert body["hot_list_changed"] is True
    assert [entry["work_order_demand_id"] for entry in body["entries"]] == [listed]
    assert body["entries"][0]["rank"] == 1
    assert _audit_count(db_engine) == before
    assert _rank_of(db_engine, latecomer) is None


def test_a_replay_returns_the_original_change_and_a_mismatch_is_refused(
    client: TestClient, db_engine: Engine
) -> None:
    _clear(client)
    a, b = _demand(client), _demand(client)
    event_id = str(uuid.uuid4())
    first = _change(client, "ADD", [], [a], device_event_id=event_id)
    assert first.status_code == 201, first.text
    before = _audit_count(db_engine)

    replay = _change(client, "ADD", [], [a], device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json()["created"] is False
    assert replay.json()["changes"] == first.json()["changes"]
    assert _audit_count(db_engine) == before

    mismatch = _change(client, "ADD", [a], [a, b], device_event_id=event_id)
    assert mismatch.status_code == 409
    assert "different Hot list change" in mismatch.json()["detail"]
    assert _audit_count(db_engine) == before
    assert _order(client) == [a]


def test_a_replay_survives_the_deletion_of_the_removed_line(
    client: TestClient, db_engine: Engine
) -> None:
    _clear(client)
    pn = _unique("PN")
    number = _unique("WO")
    work_order = _work_order(client, [_line(pn), _line(_unique("PN"))], number=number)
    removable = work_order.demand_ids[0]
    kept = _demand(client)
    _added(client, removable, kept)
    event_id = str(uuid.uuid4())
    removed = _change(client, "REMOVE", [removable, kept], [kept], device_event_id=event_id)
    assert removed.status_code == 201, removed.text

    deleted = admin_of(client).delete(f"/api/work-orders/{work_order.id}/demands/{removable}")
    assert deleted.status_code == 204, deleted.text
    assert not _demand_exists(db_engine, removable)

    replay = _change(client, "REMOVE", [removable, kept], [kept], device_event_id=event_id)
    assert replay.status_code == 200, replay.text
    assert replay.json()["created"] is False
    assert replay.json()["changes"] == removed.json()["changes"]
    assert _change_line(removable, pn, number, 1, None) in replay.json()["changes"]
    # The entries are the CURRENT list.
    assert [entry["work_order_demand_id"] for entry in replay.json()["entries"]] == [kept]


def test_a_replay_survives_a_department_configuration_change(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """A committed change replays with its original ``changes`` even when
    no single active Department exists any more; the list itself cannot
    be shown then, so ``entries`` is null and nothing is written."""
    _clear(client)
    added = _demand(client)
    event_id = str(uuid.uuid4())
    first = _change(client, "ADD", [], [added], device_event_id=event_id)
    assert first.status_code == 201, first.text
    before = _audit_count(db_engine)

    second = _create_department(client)
    try:
        replay = _change(client, "ADD", [], [added], device_event_id=event_id)
        assert replay.status_code == 200, replay.text
        assert replay.json()["created"] is False
        assert replay.json()["changes"] == first.json()["changes"]
        assert replay.json()["entries"] is None
        # A new change is still refused until exactly one is active.
        assert _change(client, "REMOVE", [added], []).status_code == 409
    finally:
        deactivated = admin_of(client).patch(
            f"/api/departments/{second}", json={"is_active": False}
        )
        assert deactivated.status_code == 200, deactivated.text

    _set_department_active(db_engine, shop.department_id, False)
    try:
        replay = _change(client, "ADD", [], [added], device_event_id=event_id)
        assert replay.status_code == 200, replay.text
        assert replay.json()["changes"] == first.json()["changes"]
        assert replay.json()["entries"] is None
    finally:
        _set_department_active(db_engine, shop.department_id, True)

    assert _audit_count(db_engine) == before
    assert _order(client) == [added]


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


def test_two_changes_on_one_order_have_one_winner(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L1: the second change waits on the Hot lock while the first reads
    the order, then is refused as stale — never applied to a snapshot."""
    _clear(client)
    a, b = _demand(client), _demand(client)
    real = hot_list_service.current_ranked_order
    inside, release = threading.Event(), threading.Event()
    calls: list[list[int]] = []

    def paused(session: Session) -> list[int]:
        result = real(session)
        calls.append(list(result))
        if len(calls) == 1:
            inside.set()
            assert release.wait(timeout=20), "test deadlock: never released"
        return result

    monkeypatch.setattr(hot_list_service, "current_ranked_order", paused)
    results: dict[str, Any] = {}
    first = threading.Thread(target=lambda: results.update(first=_change(client, "ADD", [], [a])))
    first.start()
    assert inside.wait(timeout=20)
    second = threading.Thread(target=lambda: results.update(second=_change(client, "ADD", [], [b])))
    second.start()
    second.join(timeout=1.0)
    # Still waiting on the Hot lock: it has not read the order.
    assert second.is_alive() and calls == [[]]
    release.set()
    first.join(timeout=30)
    second.join(timeout=30)
    assert results["first"].status_code == 201, results["first"].text
    assert results["second"].status_code == 409, results["second"].text
    assert results["second"].json()["hot_list_changed"] is True
    assert calls == [[], [a]]
    assert _ranks(db_engine) == {a: 1}
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_two_identical_submissions_apply_once(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L2: the same device_event_id and payload sent concurrently — the
    second re-checks after the lock and replays."""
    _clear(client)
    a = _demand(client)
    event_id = str(uuid.uuid4())
    real = hot_list_service.current_ranked_order
    inside, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def paused(session: Session) -> list[int]:
        result = real(session)
        calls.append(len(result))
        if len(calls) == 1:
            inside.set()
            assert release.wait(timeout=20), "test deadlock: never released"
        return result

    monkeypatch.setattr(hot_list_service, "current_ranked_order", paused)
    results: dict[str, Any] = {}

    def submit(key: str) -> None:
        results[key] = _change(client, "ADD", [], [a], device_event_id=event_id)

    first = threading.Thread(target=submit, args=("first",))
    first.start()
    assert inside.wait(timeout=20)
    second = threading.Thread(target=submit, args=("second",))
    second.start()
    second.join(timeout=1.0)
    assert second.is_alive()
    release.set()
    first.join(timeout=30)
    second.join(timeout=30)
    assert results["first"].status_code == 201, results["first"].text
    assert results["second"].status_code == 200, results["second"].text
    assert results["second"].json()["changes"] == results["first"].json()["changes"]
    # The replay never read the order: it replayed after the lock.
    assert calls == [0]
    assert len(_audit_rows(db_engine, event_id)) == 1
    assert _ranks(db_engine) == {a: 1}


def test_a_hot_add_and_a_line_removal_serialize_on_the_demand_row(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3 / C7: the add holds the demand row lock — the removal without
    the confirmation flag waits, then sees the rank and asks for the
    typed confirmation, removing nothing."""
    _clear(client)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    target = work_order.demand_ids[0]
    real = hot_list_service.lock_demand_rows
    inside, release = threading.Event(), threading.Event()

    def paused(session: Session, demand_ids: Any) -> dict[int, models.WorkOrderDemand]:
        result = real(session, demand_ids)
        if target in result and not inside.is_set():
            inside.set()
            assert release.wait(timeout=20), "test deadlock: never released"
        return result

    monkeypatch.setattr(hot_list_service, "lock_demand_rows", paused)
    results: dict[str, Any] = {}
    adder = threading.Thread(
        target=lambda: results.update(add=_change(client, "ADD", [], [target]))
    )
    adder.start()
    assert inside.wait(timeout=20)
    remover = threading.Thread(
        target=lambda: results.update(
            delete=admin_of(client).delete(f"/api/work-orders/{work_order.id}/demands/{target}")
        )
    )
    remover.start()
    remover.join(timeout=1.0)
    assert remover.is_alive()
    release.set()
    adder.join(timeout=30)
    remover.join(timeout=30)
    assert results["add"].status_code == 201, results["add"].text
    assert results["delete"].status_code == 409, results["delete"].text
    body = results["delete"].json()
    assert body["confirmation_required"] is True
    assert body["hot_list_entry"]["rank"] == 1
    assert _demand_exists(db_engine, target)
    assert _rank_of(db_engine, target) == 1


def test_a_line_removal_and_a_hot_add_serialize_the_other_way(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L3 reverse: the removal holds the demand row lock — the add waits,
    then finds the line gone and writes nothing."""
    _clear(client)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    target = work_order.demand_ids[0]
    real = work_orders._demand_has_allocation_history
    inside, release = threading.Event(), threading.Event()

    def paused(session: Session, demand_id: int) -> bool:
        result = real(session, demand_id)
        if demand_id == target:
            inside.set()
            assert release.wait(timeout=20), "test deadlock: never released"
        return result

    monkeypatch.setattr(work_orders, "_demand_has_allocation_history", paused)
    before = _audit_count(db_engine)
    results: dict[str, Any] = {}
    remover = threading.Thread(
        target=lambda: results.update(
            delete=admin_of(client).delete(f"/api/work-orders/{work_order.id}/demands/{target}")
        )
    )
    remover.start()
    assert inside.wait(timeout=20)
    adder = threading.Thread(
        target=lambda: results.update(add=_change(client, "ADD", [], [target]))
    )
    adder.start()
    adder.join(timeout=1.0)
    assert adder.is_alive()
    release.set()
    remover.join(timeout=30)
    adder.join(timeout=30)
    assert results["delete"].status_code == 204, results["delete"].text
    assert results["add"].status_code == 404, results["add"].text
    assert results["add"].json()["detail"] == _MISSING
    assert not _demand_exists(db_engine, target)
    assert _audit_count(db_engine) == before
    assert _order(client) == []


def test_a_move_and_an_allocation_of_the_moved_demand_never_deadlock(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L4: the move holds the Hot lock and its demand row locks; the
    allocation (PN lock → Hot lock → demand rows → Work Orders) waits on
    the Hot lock and then completes (a partial allocation: no removal)."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 10)], number=_unique("WO"))
    moved = work_order.demand_id
    flow_id = _release(client, shop.material, work_order, moved, pn, 5)
    _stock(client, shop, flow_id, pn, 5)
    other = _demand(client)
    _added(client, other, moved)

    real = hot_list_service.lock_demand_rows
    inside, release = threading.Event(), threading.Event()

    def paused(session: Session, demand_ids: Any) -> dict[int, models.WorkOrderDemand]:
        result = real(session, demand_ids)
        if moved in result and not inside.is_set():
            inside.set()
            assert release.wait(timeout=20), "test deadlock: never released"
        return result

    monkeypatch.setattr(hot_list_service, "lock_demand_rows", paused)
    results: dict[str, Any] = {}
    mover = threading.Thread(
        target=lambda: results.update(
            move=_change(client, "MOVE_UP", [other, moved], [moved, other])
        )
    )
    mover.start()
    assert inside.wait(timeout=20)
    allocator = threading.Thread(
        target=lambda: results.update(allocate=_allocate(client, pn, [(moved, 5)]))
    )
    allocator.start()
    allocator.join(timeout=1.0)
    assert allocator.is_alive()
    release.set()
    mover.join(timeout=30)
    allocator.join(timeout=30)
    assert results["move"].status_code == 201, results["move"].text
    assert results["allocate"].status_code == 201, results["allocate"].text
    assert _ranks(db_engine) == {moved: 1, other: 2}
    entry = _entry(client, moved)
    assert entry["allocated_quantity"] == 5 and entry["shortage_quantity"] == 5


# ---------------------------------------------------------------------------
# Department scope
# ---------------------------------------------------------------------------


def test_several_active_departments_refuse_every_hot_list_route(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    candidate = _demand(client)
    second = _create_department(client)
    before = _audit_count(db_engine)
    try:
        for response in (
            admin_of(client).get("/api/hot-list"),
            admin_of(client).get("/api/hot-list/candidates"),
            _change(client, "ADD", [], [candidate]),
        ):
            assert response.status_code == 409, response.text
            detail = response.json()["detail"]
            assert detail.startswith("Several active Departments exist (")
            assert detail.endswith(
                "The Hot list is managed within one Department, so nothing can be shown or"
                " changed until exactly one Department is active."
            )
    finally:
        deactivated = admin_of(client).patch(
            f"/api/departments/{second}", json={"is_active": False}
        )
        assert deactivated.status_code == 200, deactivated.text
    assert _audit_count(db_engine) == before
    assert _rank_of(db_engine, candidate) is None


def test_no_active_department_refuses_every_hot_list_route(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    candidate = _demand(client)
    _set_department_active(db_engine, shop.department_id, False)
    try:
        for response in (
            admin_of(client).get("/api/hot-list"),
            admin_of(client).get("/api/hot-list/candidates", params={"search": "x"}),
            _change(client, "ADD", [], [candidate]),
        ):
            assert response.status_code == 404, response.text
            assert response.json()["detail"] == "No active Department is configured."
    finally:
        _set_department_active(db_engine, shop.department_id, True)
    assert _rank_of(db_engine, candidate) is None


def test_the_distribution_excludes_areas_of_another_department(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 10)], number=_unique("WO"))
    elsewhere_department = _create_department(client)
    elsewhere = _Cell(client, elsewhere_department)
    _release(client, elsewhere, work_order, work_order.demand_id, pn, 3)
    _release(client, shop.material, work_order, work_order.demand_id, pn, 2)
    _set_department_active(db_engine, elsewhere_department, False)
    _added(client, work_order.demand_id)

    locations = _entry(client, work_order.demand_id)["part_number_locations"]
    assert [(loc["area"]["id"], loc["quantity"]) for loc in locations] == [
        (shop.material.area_id, 2)
    ]
    # Released quantity is the demand's, wherever it went.
    assert _entry(client, work_order.demand_id)["released_quantity"] == 5


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _candidates(client: TestClient, **params: str) -> dict[str, Any]:
    response = admin_of(client).get("/api/hot-list/candidates", params=params)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _candidate_ids(body: dict[str, Any]) -> list[int]:
    return [int(entry["work_order_demand_id"]) for entry in body["candidates"]]


def test_search_matches_pn_work_order_number_and_job_number(
    client: TestClient, shop: _Shop
) -> None:
    token = uuid.uuid4().hex[:8].upper()
    by_pn = _demand(client, f"PNQ{token}")
    by_number = _work_order(client, [_line(_unique("PN"))], number=f"wo-{token}-x").demand_id
    by_job = _work_order(
        client, [_line(_unique("PN"), job_numbers=["J1", f"JOB/{token}/7"])]
    ).demand_id

    body = _candidates(client, search=f"  {token.lower()} ")
    assert sorted(_candidate_ids(body)) == sorted([by_pn, by_number, by_job])
    assert body["part_number"] is None
    assert body["truncated"] is False
    assert body["already_listed_count"] == 0
    entry = next(e for e in body["candidates"] if e["work_order_demand_id"] == by_job)
    assert entry["rank"] is None
    assert entry["job_numbers"] == ["J1", f"JOB/{token}/7"]
    assert entry["active"] is True

    _added(client, by_pn)
    body = _candidates(client, search=token)
    assert sorted(_candidate_ids(body)) == sorted([by_number, by_job])
    assert body["already_listed_count"] == 1


def test_search_escapes_like_wildcards(client: TestClient, shop: _Shop) -> None:
    token = uuid.uuid4().hex[:8].upper()
    literal = _demand(client, f"ESC{token}_1")
    _demand(client, f"ESC{token}A1")
    assert _candidate_ids(_candidates(client, search=f"ESC{token}_1")) == [literal]
    assert _candidate_ids(_candidates(client, search=f"ESC{token}%")) == []


def test_a_pn_barcode_lists_every_eligible_demand_in_canonical_order(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    assert _candidates(client, barcode=f"PF:PN:{pn.lower()}") == {
        "part_number": pn,
        "candidates": [],
        "already_listed_count": 0,
        "truncated": False,
    }
    late = _work_order(client, [_line(pn, due_date="2031-03-01")]).demand_id
    body = _candidates(client, barcode=f" PF:PN:{pn} ")
    assert _candidate_ids(body) == [late]
    undated = _work_order(client, [_line(pn)], received_date="2026-01-01").demand_id
    early = _work_order(client, [_line(pn, due_date="2031-01-01")]).demand_id
    body = _candidates(client, barcode=f"PF:PN:{pn}")
    # Dated earliest first, undated after all dated.
    assert _candidate_ids(body) == [early, late, undated]
    assert body["part_number"] == pn


def test_a_barcode_returns_more_than_fifty_demands_untruncated(
    client: TestClient, shop: _Shop
) -> None:
    pn = _unique("PN")
    demand_ids = [_demand(client, pn, 1) for _ in range(51)]
    body = _candidates(client, barcode=f"PF:PN:{pn}")
    assert sorted(_candidate_ids(body)) == sorted(demand_ids)
    assert body["truncated"] is False
    searched = _candidates(client, search=pn)
    assert len(searched["candidates"]) == 50 and searched["truncated"] is True
    unfiltered = _candidates(client)
    assert len(unfiltered["candidates"]) == 50 and unfiltered["truncated"] is True


def test_candidate_refusals(client: TestClient, shop: _Shop) -> None:
    for barcode in ("PF:MACHINE:CD-0001", "hello", "PF:AREA:7"):
        response = admin_of(client).get("/api/hot-list/candidates", params={"barcode": barcode})
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == _NOT_A_PN_BARCODE
    empty = admin_of(client).get("/api/hot-list/candidates", params={"barcode": "PF:PN:"})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Part Number must not be empty."
    both = admin_of(client).get(
        "/api/hot-list/candidates", params={"barcode": "PF:PN:X", "search": "X"}
    )
    assert both.status_code == 422
    # PostgreSQL text cannot hold NUL: a 422 before any query, not a 500.
    for params in ({"search": "a\x00b"}, {"barcode": "PF:PN:A\x00B"}):
        response = admin_of(client).get("/api/hot-list/candidates", params=params)
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == (
            "The search text or barcode contains a NUL character. Nothing was searched."
        )


def test_ranked_completed_and_fully_allocated_demand_is_no_candidate(
    client: TestClient, shop: _Shop
) -> None:
    _clear(client)
    pn = _unique("PN")
    ranked = _demand(client, pn, 5)
    completed = _work_order(client, [_line(pn, 3)], number=_unique("WO"))
    _fulfil(client, shop, completed, completed.demand_id, pn, 3)
    open_work_order = _work_order(client, [_line(pn, 3), _line(_unique("PN"), 3)])
    full = open_work_order.demand_ids[0]
    _fulfil(client, shop, open_work_order, full, pn, 3)
    eligible = _demand(client, pn, 5)
    _added(client, ranked)

    body = _candidates(client, barcode=f"PF:PN:{pn}")
    assert _candidate_ids(body) == [eligible]
    assert body["already_listed_count"] == 1


# ---------------------------------------------------------------------------
# The list read
# ---------------------------------------------------------------------------


def test_the_list_reports_flags_distribution_and_released_quantity(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    # An internal Work Order, partly released into two Areas.
    pn = _unique("PN")
    internal = _work_order(client, [_line(pn, 10, due_date="2031-05-01")])
    _release(client, shop.material, internal, internal.demand_id, pn, 3)
    lathe_flow = _release(client, shop.lathe, internal, internal.demand_id, pn, 4)
    assigned = client.post(
        f"/api/scan-stations/{shop.lathe.station_id}/machine-assignments",
        json={
            "part_number": pn,
            "quantity_flow_id": lathe_flow,
            "machine_id": shop.lathe.machine_ids[0],
            "quantity": 2,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert assigned.status_code == 201, assigned.text
    # A fully allocated line of an open Work Order, and a completed one:
    # both were ranked and leave the list as they become inactive.
    full_pn = _unique("PN")
    open_work_order = _work_order(client, [_line(full_pn, 2), _line(_unique("PN"), 2)])
    full = open_work_order.demand_ids[0]
    done_pn = _unique("PN")
    done = _work_order(client, [_line(done_pn, 2)], number=_unique("WO"))
    _added(client, internal.demand_id, full, done.demand_id)
    _fulfil(client, shop, open_work_order, full, full_pn, 2)
    _fulfil(client, shop, done, done.demand_id, done_pn, 2)

    body = _hot_list(client)
    assert body["department"]["id"] == shop.department_id
    entries = {entry["work_order_demand_id"]: entry for entry in body["entries"]}
    # Both inactive entries left the list automatically (OD1).
    assert [entry["rank"] for entry in body["entries"]] == [1]
    assert full not in entries and done.demand_id not in entries

    first = entries[internal.demand_id]
    assert first["work_order_number"] is None
    assert first["part_number"] == pn
    assert first["request_type"] == "NEW"
    assert first["due_date"] == "2031-05-01"
    assert first["requested_quantity"] == 10
    assert first["allocated_quantity"] == 0
    assert first["shortage_quantity"] == 10
    assert first["released_quantity"] == 7
    assert first["work_order_completed"] is False and first["active"] is True

    # The distribution is the Production Board's grouping for the PN.
    board = client.get("/api/production-board")
    assert board.status_code == 200, board.text
    [row] = [row for row in board.json()["rows"] if row["part_number"] == pn]

    def shape(location: dict[str, Any]) -> tuple[Any, ...]:
        machine = location["machine"]
        return (
            location["area"]["id"],
            location["area"]["name"],
            location["area"]["color"],
            location["state"],
            machine["id"] if machine is not None else None,
            location["activity"],
            location["quantity"],
        )

    expected = [shape(loc) for loc in row["locations"] if loc["state"] != "STOCKED"]
    assert [shape(loc) for loc in first["part_number_locations"]] == expected
    assert {loc["state"] for loc in first["part_number_locations"]} >= {"MACHINE", "QUEUE"}
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


# ---------------------------------------------------------------------------
# Removing a Hot demand line (OD3)
# ---------------------------------------------------------------------------


def _delete_line(
    client: TestClient, work_order_id: int, demand_id: int, *, confirm: bool = False
) -> Any:
    query = "?confirm_hot_removal=true" if confirm else ""
    return admin_of(client).delete(f"/api/work-orders/{work_order_id}/demands/{demand_id}{query}")


def _demand_state(engine: Engine) -> list[tuple[Any, ...]]:
    """Every demand row with its rank and ``updated_at`` — the zero-write
    witness of a refusal."""
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.select(
                    models.WorkOrderDemand.id,
                    models.WorkOrderDemand.priority_rank,
                    models.WorkOrderDemand.updated_at,
                ).order_by(models.WorkOrderDemand.id)
            )
        ]


def test_removing_a_hot_line_without_the_flag_asks_for_confirmation(
    client: TestClient, db_engine: Engine
) -> None:
    """B1: a 409 that names the current rank and writes nothing."""
    _clear(client)
    pn = _unique("PN")
    number = _unique("WO")
    work_order = _work_order(client, [_line(pn), _line(_unique("PN"))], number=number)
    hot = work_order.demand_ids[0]
    other = _demand(client)
    _added(client, other, hot)
    before_count, before_state = _audit_count(db_engine), _demand_state(db_engine)

    refused = _delete_line(client, work_order.id, hot)
    assert refused.status_code == 409, refused.text
    assert refused.json() == {
        "detail": (
            f"{pn} on Work Order {number} is on the Hot list at #2. Removing this demand line"
            " also removes it from the Hot list, and every entry below it moves up one rank."
            " Confirm the removal to continue. Nothing was removed."
        ),
        "confirmation_required": True,
        "hot_list_entry": {"work_order_demand_id": hot, "part_number": pn, "rank": 2},
    }
    assert _audit_count(db_engine) == before_count
    assert _demand_state(db_engine) == before_state
    assert _order(client) == [other, hot]


def test_a_confirmed_hot_line_removal_leaves_the_list_and_deletes_the_line(
    client: TestClient, db_engine: Engine
) -> None:
    """B2: one transaction — the rank cleared, the ranks below shifted
    (audited as LINE_DELETE), the line deleted; a Hot replay naming the
    deleted line still returns its original changes."""
    _clear(client)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    hot = work_order.demand_ids[0]
    first, last = _demand(client), _demand(client)
    _added(client, first, hot, last)
    event_id = str(uuid.uuid4())
    moved = _change(
        client, "MOVE_UP", [first, hot, last], [hot, first, last], device_event_id=event_id
    )
    assert moved.status_code == 201, moved.text
    mark = _audit_mark(db_engine)

    deleted = _delete_line(client, work_order.id, hot, confirm=True)
    assert deleted.status_code == 204, deleted.text
    assert not _demand_exists(db_engine, hot)
    assert _order(client) == [first, last]
    rows = _hot_rows_since(db_engine, mark)
    _assert_removal_rows(
        rows,
        action="LINE_DELETE",
        cause={
            "trigger": "DEMAND_LINE_REMOVAL",
            "reference": {"work_order_id": work_order.id},
            "removed": [{"work_order_demand_id": hot, "reason": "LINE_DELETED"}],
        },
        changes=[(first, 2, 1), (last, 3, 2), (hot, 1, None)],
    )
    [deleted_row] = [row for row in rows if row.entity_id == str(hot)]
    assert deleted_row.after_data == {"priority_rank": None}
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)

    replay = _change(
        client, "MOVE_UP", [first, hot, last], [hot, first, last], device_event_id=event_id
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["changes"] == moved.json()["changes"]


def test_a_confirmed_removal_of_a_released_hot_line_is_refused_and_releases_the_hot_lock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """B3: the released rule refuses first, writing nothing; the Hot lock
    the flag took is released with the rollback."""
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn), _line(_unique("PN"))], number=_unique("WO"))
    hot = work_order.demand_ids[0]
    _release(client, shop.material, work_order, hot, pn, 1)
    _added(client, hot)
    before_count, before_state = _audit_count(db_engine), _demand_state(db_engine)

    refused = _delete_line(client, work_order.id, hot, confirm=True)
    assert refused.status_code == 409, refused.text
    assert refused.json() == {"detail": _RELEASED_REMOVAL}
    assert _audit_count(db_engine) == before_count
    assert _demand_state(db_engine) == before_state
    # Immediately: a held Hot lock would make this wait forever.
    assert _remove(client, hot).status_code == 201


def test_every_other_removal_rule_comes_before_the_hot_confirmation(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """B4: the last line and an allocated line answer their own 409 —
    with or without the flag, never the confirmation — writing nothing."""
    _clear(client)
    only = _work_order(client, [_line(_unique("PN"))], number=_unique("WO"))
    pn = _unique("PN")
    allocated = _work_order(client, [_line(pn, 10), _line(_unique("PN"))], number=_unique("WO"))
    allocated_line = allocated.demand_ids[0]
    _stocked(client, shop, allocated, allocated_line, pn, 4)
    assert _allocate(client, pn, [(allocated_line, 4)]).status_code == 201
    _added(client, only.demand_id, allocated_line)
    before_count, before_state = _audit_count(db_engine), _demand_state(db_engine)

    for confirm in (True, False):
        last = _delete_line(client, only.id, only.demand_id, confirm=confirm)
        assert last.status_code == 409, last.text
        assert last.json() == {"detail": _LAST_LINE_REMOVAL}
        refused = _delete_line(client, allocated.id, allocated_line, confirm=confirm)
        assert refused.status_code == 409, refused.text
        assert refused.json() == {"detail": _ALLOCATED_REMOVAL}
    assert _audit_count(db_engine) == before_count
    assert _demand_state(db_engine) == before_state


def test_a_hot_line_with_reversed_allocation_history_says_so_and_is_refused(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """B4b: a partial allocation of a Hot line, then reversed — the line
    shows no allocated quantity but keeps its allocation history. The
    Work Order read says so (`has_allocation_history`, so the UI never
    asks a typed Hot confirmation it would type in vain), and removal
    answers the allocation-history 409 with or without the flag, never
    the confirmation, writing nothing."""
    _clear(client)
    pn = _unique("PN")
    # The stock comes from another Work Order's release, so the Hot line
    # itself carries no released quantity (that rule would answer first).
    source = _work_order(client, [_line(pn, 4)], number=_unique("WO"))
    _stocked(client, shop, source, source.demand_id, pn, 4)
    work_order = _work_order(client, [_line(pn, 10), _line(_unique("PN"))], number=_unique("WO"))
    hot, untouched = work_order.demand_ids
    allocated = _allocate(client, pn, [(hot, 4)])
    assert allocated.status_code == 201, allocated.text
    reversed_ = admin_of(client).post(
        f"/api/allocations/{allocated.json()['rows'][0]['allocation_id']}/reversals",
        json={"reason": "Counted wrong", "device_event_id": str(uuid.uuid4())},
    )
    assert reversed_.status_code == 201, reversed_.text
    _added(client, hot)

    detail = admin_of(client).get(f"/api/work-orders/{work_order.id}")
    assert detail.status_code == 200, detail.text
    lines = {d["id"]: d for d in detail.json()["demands"]}
    assert (
        lines[hot]["allocated_quantity"],
        lines[hot]["has_released_quantity"],
        lines[hot]["priority_rank"],
        lines[hot]["has_allocation_history"],
    ) == (0, False, 1, True)
    assert lines[untouched]["has_allocation_history"] is False
    before_count, before_state = _audit_count(db_engine), _demand_state(db_engine)

    for confirm in (False, True):
        refused = _delete_line(client, work_order.id, hot, confirm=confirm)
        assert refused.status_code == 409, refused.text
        assert refused.json() == {
            "detail": (
                "Cannot remove: stocked quantity has been allocated to this demand line before"
                " and the allocation history stays with it."
            )
        }
    assert _audit_count(db_engine) == before_count
    assert _demand_state(db_engine) == before_state
    assert _order(client) == [hot]


def test_the_flag_on_an_unranked_line_is_a_plain_removal(
    client: TestClient, db_engine: Engine
) -> None:
    """B5: 204, no rank row."""
    _clear(client)
    listed = _demand(client)
    _added(client, listed)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    before = _audit_count(db_engine)

    deleted = _delete_line(client, work_order.id, work_order.demand_ids[0], confirm=True)
    assert deleted.status_code == 204, deleted.text
    assert not _demand_exists(db_engine, work_order.demand_ids[0])
    assert _audit_count(db_engine) == before
    assert _order(client) == [listed]


def test_the_flag_on_an_unknown_demand_is_a_404(client: TestClient, db_engine: Engine) -> None:
    """B6: nothing to remove, nothing written."""
    _clear(client)
    listed = _demand(client)
    _added(client, listed)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    before_count, before_state = _audit_count(db_engine), _demand_state(db_engine)

    missing = _delete_line(client, work_order.id, 999_999_999, confirm=True)
    assert missing.status_code == 404, missing.text
    foreign = _delete_line(client, work_order.id, listed, confirm=True)
    assert foreign.status_code == 404, foreign.text
    assert _audit_count(db_engine) == before_count
    assert _demand_state(db_engine) == before_state
    assert _order(client) == [listed]


# ---------------------------------------------------------------------------
# Cross-consumer regression and the intake boundary
# ---------------------------------------------------------------------------


def test_the_command_rank_orders_every_consumer(client: TestClient, shop: _Shop) -> None:
    """R2: ranks written only through the command order allocation, the
    Production Board, PN Tracking and the Area inventory."""
    _clear(client)
    token = uuid.uuid4().hex[:8].upper()
    first_pn, second_pn = f"XC{token}-1", f"XC{token}-2"
    # Without ranks first_pn would lead: its demand is due far earlier.
    first = _work_order(client, [_line(first_pn, due_date="2026-01-01")], number=_unique("WO"))
    first_dated = _work_order(
        client, [_line(first_pn, due_date="2025-06-01")], number=_unique("WO")
    )
    second = _work_order(client, [_line(second_pn, due_date="2031-01-01")], number=_unique("WO"))
    _release(client, shop.material, first, first.demand_id, first_pn, 2)
    _release(client, shop.material, second, second.demand_id, second_pn, 2)
    _added(client, second.demand_id, first.demand_id)

    # Allocation suggestion: the Hot demand before the earlier-dated one.
    suggestion = client.get("/api/allocations/suggestion", params={"part_number": first_pn})
    assert suggestion.status_code == 200, suggestion.text
    assert [line["work_order_demand_id"] for line in suggestion.json()["lines"]] == [
        first.demand_id,
        first_dated.demand_id,
    ]
    assert suggestion.json()["lines"][0]["priority_rank"] == 2

    # Production Board: rank order and hot_rank.
    rows = client.get("/api/production-board").json()["rows"]
    board = [row for row in rows if row["part_number"] in (first_pn, second_pn)]
    assert [(row["part_number"], row["hot_rank"]) for row in board] == [
        (second_pn, 1),
        (first_pn, 2),
    ]
    assert rows.index(board[0]) == 0 and rows.index(board[1]) == 1

    # PN Tracking.
    tracking = admin_of(client).get("/api/tracking", params={"search": f"XC{token}"})
    assert tracking.status_code == 200, tracking.text
    assert [(row["part_number"], row["hot_rank"]) for row in tracking.json()["rows"]] == [
        (second_pn, 1),
        (first_pn, 2),
    ]

    # Area inventory: the demand context carries the rank, Hot first.
    inventory = client.get(f"/api/areas/{shop.material.area_id}/inventory")
    assert inventory.status_code == 200, inventory.text
    [context] = [
        item for item in inventory.json()["demand_context"] if item["part_number"] == first_pn
    ]
    assert [(d["work_order_demand_id"], d["priority_rank"]) for d in context["demands"]] == [
        (first.demand_id, 2),
        (first_dated.demand_id, None),
    ]


def test_a_fully_allocated_hot_line_of_an_open_wo_leaves_the_hot_list_and_monitoring_rows(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """OD1: a ranked line that becomes fully allocated while its Work
    Order stays open leaves the Hot list with the allocation, so the
    Production Board, PN Tracking (Hot only) and the Area inventory
    demand context show no Hot rank for the PN any more — the next
    demand leads, unranked."""
    _clear(client)
    pn = f"FA{uuid.uuid4().hex[:8].upper()}"
    # The second line keeps the Hot line's Work Order open.
    hot = _work_order(client, [_line(pn, 4), _line(_unique("PN"), 4)], number=_unique("WO"))
    later = _work_order(client, [_line(pn, 5, due_date="2031-01-01")], number=_unique("WO"))
    _added(client, hot.demand_id)
    # Quantity still in production gives the PN its monitoring rows.
    _release(client, shop.material, later, later.demand_id, pn, 2)

    def board_rank() -> list[Any]:
        rows = client.get("/api/production-board").json()["rows"]
        return [row["hot_rank"] for row in rows if row["part_number"] == pn]

    assert board_rank() == [1]
    _fulfil(client, shop, hot, hot.demand_id, pn, 4)
    assert _rank_of(db_engine, hot.demand_id) is None
    assert _order(client) == []

    assert board_rank() == [None]
    tracking = admin_of(client).get("/api/tracking", params={"search": pn, "hot_only": "true"})
    assert tracking.status_code == 200 and tracking.json()["rows"] == []
    inventory = client.get(f"/api/areas/{shop.material.area_id}/inventory")
    assert inventory.status_code == 200, inventory.text
    [context] = [item for item in inventory.json()["demand_context"] if item["part_number"] == pn]
    assert all(d["priority_rank"] is None for d in context["demands"])
    assert context["demands"][0]["work_order_demand_id"] == later.demand_id
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_work_order_intake_still_rejects_priority_rank(client: TestClient) -> None:
    created = admin_of(client).post(
        "/api/work-orders", json={"lines": [_line(_unique("PN"), priority_rank=1)]}
    )
    assert created.status_code == 422
    work_order = _work_order(client, [_line(_unique("PN"))])
    edited = admin_of(client).patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "priority_rank": 1}]},
    )
    assert edited.status_code == 422


# ---------------------------------------------------------------------------
# Races of the automatic removal and the confirmed deletion (follow-up §9.5)
# ---------------------------------------------------------------------------


def _start(results: dict[str, Any], key: str, call: Callable[[], Any]) -> threading.Thread:
    """Run ``call`` in a thread; its response — or the exception it raised,
    e.g. a server error the TestClient re-raises — lands in ``results``."""

    def run() -> None:
        try:
            results[key] = call()
        except Exception as exc:  # recorded for the test's assertions
            results[key] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def _pause(
    monkeypatch: pytest.MonkeyPatch,
    target: Any,
    name: str,
    *,
    before: bool = False,
    when: Callable[..., bool] = lambda *args, **kwargs: True,
) -> tuple[threading.Event, threading.Event]:
    """Pause the first matching call of ``target.name`` — before or after
    the real call — until the test releases it."""
    real = getattr(target, name)
    inside, release = threading.Event(), threading.Event()

    def hold() -> None:
        inside.set()
        assert release.wait(timeout=20), "test deadlock: never released"

    def paused(*args: Any, **kwargs: Any) -> Any:
        matches = not inside.is_set() and when(*args, **kwargs)
        if matches and before:
            hold()
        result = real(*args, **kwargs)
        if matches and not before:
            hold()
        return result

    monkeypatch.setattr(target, name, paused)
    return inside, release


def _waits(thread: threading.Thread) -> bool:
    thread.join(timeout=1.0)
    return thread.is_alive()


def _finish(release: threading.Event, *threads: threading.Thread) -> None:
    release.set()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()


def _two_line_work_order(client: TestClient, pn: str, quantity: int) -> _WorkOrder:
    """A numbered Work Order whose second line keeps it open."""
    return _work_order(
        client, [_line(pn, quantity), _line(_unique("PN"), quantity)], number=_unique("WO")
    )


def test_a_move_then_an_allocation_removing_the_moved_entry(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1: the allocation waits on the Hot lock held by the MOVE, then
    removes D against the moved order."""
    _clear(client)
    pn = _unique("PN")
    target = _two_line_work_order(client, pn, 4)
    d = target.demand_id
    other = _demand(client)
    _stocked(client, shop, target, d, pn, 4)
    _added(client, other, d)
    mark = _audit_mark(db_engine)
    inside, release = _pause(
        monkeypatch, hot_list_service, "lock_demand_rows", when=lambda session, ids: d in ids
    )
    results: dict[str, Any] = {}
    mover = _start(results, "move", lambda: _change(client, "MOVE_UP", [other, d], [d, other]))
    assert inside.wait(timeout=20)
    allocator = _start(results, "allocate", lambda: _allocate(client, pn, [(d, 4)]))
    assert _waits(allocator)
    _finish(release, mover, allocator)

    assert results["move"].status_code == 201, results["move"].text
    assert results["allocate"].status_code == 201, results["allocate"].text
    assert _order(client) == [other]
    assert [_block(row)["action"] for row in _hot_rows_since(db_engine, mark)] == [
        "MOVE_UP",
        "MOVE_UP",
        "AUTO_REMOVE",
        "AUTO_REMOVE",
    ]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_hot_change_waiting_on_an_allocation_sees_the_removal_as_stale(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2: the allocation holds the Hot lock; a REMOVE of another entry
    waits, then is refused as stale with the current entries."""
    _clear(client)
    pn = _unique("PN")
    target = _two_line_work_order(client, pn, 4)
    d = target.demand_id
    other = _demand(client)
    _stocked(client, shop, target, d, pn, 4)
    _added(client, d, other)
    expected = _order(client)
    mark = _audit_mark(db_engine)
    inside, release = _pause(monkeypatch, hot_ranks, "hot_rank_scope")
    results: dict[str, Any] = {}
    allocator = _start(results, "allocate", lambda: _allocate(client, pn, [(d, 4)]))
    assert inside.wait(timeout=20)
    remover = _start(results, "remove", lambda: _change(client, "REMOVE", expected, [d]))
    assert _waits(remover)
    _finish(release, allocator, remover)

    assert results["allocate"].status_code == 201, results["allocate"].text
    assert results["remove"].status_code == 409, results["remove"].text
    body = results["remove"].json()
    assert body["hot_list_changed"] is True
    assert [entry["work_order_demand_id"] for entry in body["entries"]] == [other]
    assert {_block(row)["action"] for row in _hot_rows_since(db_engine, mark)} == {"AUTO_REMOVE"}
    assert _order(client) == [other]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_an_add_then_an_allocation_fully_allocating_the_added_demand(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3: the allocation waits for the ADD, then removes what it added."""
    _clear(client)
    pn = _unique("PN")
    target = _two_line_work_order(client, pn, 4)
    d = target.demand_id
    _stocked(client, shop, target, d, pn, 4)
    inside, release = _pause(
        monkeypatch, hot_list_service, "lock_demand_rows", when=lambda session, ids: d in ids
    )
    results: dict[str, Any] = {}
    adder = _start(results, "add", lambda: _change(client, "ADD", [], [d]))
    assert inside.wait(timeout=20)
    allocator = _start(results, "allocate", lambda: _allocate(client, pn, [(d, 4)]))
    assert _waits(allocator)
    _finish(release, adder, allocator)

    assert results["add"].status_code == 201, results["add"].text
    assert results["allocate"].status_code == 201, results["allocate"].text
    assert _rank_of(db_engine, d) is None
    assert _order(client) == []
    _assert_no_inactive_entry(db_engine)


def test_an_add_waiting_on_a_fully_allocating_allocation_is_refused(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C4: the ADD waits on the Hot lock, then judges the line fully allocated."""
    _clear(client)
    pn = _unique("PN")
    target = _two_line_work_order(client, pn, 4)
    d = target.demand_id
    _stocked(client, shop, target, d, pn, 4)
    inside, release = _pause(monkeypatch, hot_ranks, "hot_rank_scope")
    results: dict[str, Any] = {}
    allocator = _start(results, "allocate", lambda: _allocate(client, pn, [(d, 4)]))
    assert inside.wait(timeout=20)
    adder = _start(results, "add", lambda: _change(client, "ADD", [], [d]))
    assert _waits(adder)
    _finish(release, allocator, adder)

    assert results["allocate"].status_code == 201, results["allocate"].text
    assert results["add"].status_code == 409, results["add"].text
    assert results["add"].json()["detail"] == (
        f"{pn} on Work Order {target.number} is fully allocated (4 of 4 pcs), so there is"
        " nothing left to expedite. Nothing was changed."
    )
    assert _rank_of(db_engine, d) is None


def test_a_move_waiting_on_a_quantity_save_is_refused_as_stale(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C5: the save holds the PN and Hot locks (paused right after the Hot
    lock); the MOVE waits, the save removes D, the MOVE is stale."""
    _clear(client)
    pn = _unique("PN")
    target = _two_line_work_order(client, pn, 10)
    d = target.demand_id
    other = _demand(client)
    _stocked(client, shop, target, d, pn, 4)
    assert _allocate(client, pn, [(d, 4)]).status_code == 201
    _added(client, other, d)
    mark = _audit_mark(db_engine)
    inside, release = _pause(monkeypatch, hot_ranks, "hot_rank_scope")
    results: dict[str, Any] = {}
    saver = _start(
        results,
        "save",
        lambda: admin_of(client).patch(
            f"/api/work-orders/{target.id}",
            json={"line_edits": [{"id": d, "requested_quantity": 4}]},
        ),
    )
    assert inside.wait(timeout=20)
    mover = _start(results, "move", lambda: _change(client, "MOVE_UP", [other, d], [d, other]))
    assert _waits(mover)
    _finish(release, saver, mover)

    assert results["save"].status_code == 200, results["save"].text
    assert results["move"].status_code == 409, results["move"].text
    assert results["move"].json()["hot_list_changed"] is True
    assert [_block(row)["action"] for row in _hot_rows_since(db_engine, mark)] == ["AUTO_REMOVE"]
    assert _order(client) == [other]
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_confirmed_hot_line_removal_and_a_move_serialize_both_ways(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C6: the flagged DELETE holds the Hot lock and its rows — a MOVE
    waits and is then stale; the other way round, the DELETE waits for a
    Hot REMOVE of the line and becomes a plain removal."""
    _clear(client)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    d = work_order.demand_ids[0]
    other = _demand(client)
    _added(client, other, d)
    inside, release = _pause(
        monkeypatch, work_orders, "_demand_has_allocation_history", when=lambda s, i: i == d
    )
    results: dict[str, Any] = {}
    deleter = _start(
        results, "delete", lambda: _delete_line(client, work_order.id, d, confirm=True)
    )
    assert inside.wait(timeout=20)
    mover = _start(results, "move", lambda: _change(client, "MOVE_UP", [other, d], [d, other]))
    assert _waits(mover)
    _finish(release, deleter, mover)
    assert results["delete"].status_code == 204, results["delete"].text
    assert results["move"].status_code == 409, results["move"].text
    assert results["move"].json()["hot_list_changed"] is True
    assert not _demand_exists(db_engine, d)
    assert _order(client) == [other]
    monkeypatch.undo()

    # The reverse order.
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    d = work_order.demand_ids[0]
    _added(client, d)
    expected = _order(client)
    mark = _audit_mark(db_engine)
    inside, release = _pause(
        monkeypatch, hot_list_service, "lock_demand_rows", when=lambda session, ids: d in ids
    )
    results = {}
    remover = _start(
        results,
        "remove",
        lambda: _change(client, "REMOVE", expected, [x for x in expected if x != d]),
    )
    assert inside.wait(timeout=20)
    deleter = _start(
        results, "delete", lambda: _delete_line(client, work_order.id, d, confirm=True)
    )
    assert _waits(deleter)
    _finish(release, remover, deleter)
    assert results["remove"].status_code == 201, results["remove"].text
    assert results["delete"].status_code == 204, results["delete"].text
    assert {_block(row)["action"] for row in _hot_rows_since(db_engine, mark)} == {"REMOVE"}
    assert not _demand_exists(db_engine, d)
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def test_a_release_of_a_shift_row_waits_for_the_allocation(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8: an allocation of PN X (rank 1) holds PN Y's ranked row (its
    shift scope) once its demand pass is done; a release of that row
    waits on it — no deadlock — and runs after the COMMIT."""
    _clear(client)
    x_pn, y_pn = _unique("PN"), _unique("PN")
    x_work_order = _two_line_work_order(client, x_pn, 4)
    dx = x_work_order.demand_id
    y_work_order = _work_order(client, [_line(y_pn, 10)], number=_unique("WO"))
    dy = y_work_order.demand_id
    _stocked(client, shop, x_work_order, dx, x_pn, 4)
    _added(client, dx, dy)
    inside, release = _pause(monkeypatch, allocations_service, "_lock_work_orders", before=True)
    results: dict[str, Any] = {}
    allocator = _start(results, "allocate", lambda: _allocate(client, x_pn, [(dx, 4)]))
    assert inside.wait(timeout=20)
    releaser = _start(
        results,
        "release",
        lambda: _release_response(client, shop.material, y_work_order, dy, y_pn, 2),
    )
    assert _waits(releaser)
    _finish(release, allocator, releaser)

    assert results["allocate"].status_code == 201, results["allocate"].text
    assert results["release"].status_code == 201, results["release"].text
    assert _ranks(db_engine) == {dy: 1}
    _assert_no_inactive_entry(db_engine)


def test_an_idempotency_race_lost_at_commit_answers_409_and_keeps_the_rank(
    client: TestClient, shop: _Shop, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C9: while the allocation is paused before its Hot removal, a
    reversal of another PN commits under the same device_event_id. The
    allocation's COMMIT loses the unique race and answers the existing
    409 — not a 500 — and its Hot writes roll back with it."""
    _clear(client)
    x_pn, y_pn = _unique("PN"), _unique("PN")
    x_work_order = _two_line_work_order(client, x_pn, 4)
    d = x_work_order.demand_id
    other = _demand(client)
    _stocked(client, shop, x_work_order, d, x_pn, 4)
    _added(client, d, other)
    y_work_order = _work_order(client, [_line(y_pn, 5)], number=_unique("WO"))
    _stocked(client, shop, y_work_order, y_work_order.demand_id, y_pn, 2)
    y_allocation = _allocate(client, y_pn, [(y_work_order.demand_id, 2)])
    assert y_allocation.status_code == 201, y_allocation.text
    shared_id = str(uuid.uuid4())
    mark = _audit_mark(db_engine)
    inside, release = _pause(monkeypatch, hot_ranks, "remove_from_hot_list", before=True)
    results: dict[str, Any] = {}
    allocator = _start(
        results, "allocate", lambda: _allocate(client, x_pn, [(d, 4)], device_event_id=shared_id)
    )
    assert inside.wait(timeout=20)
    reversal = admin_of(client).post(
        f"/api/allocations/{y_allocation.json()['rows'][0]['allocation_id']}/reversals",
        json={"reason": "Wrong line", "device_event_id": shared_id},
    )
    assert reversal.status_code == 201, reversal.text
    _finish(release, allocator)

    response = results["allocate"]
    assert not isinstance(response, Exception), repr(response)
    assert response.status_code == 409, response.text
    assert "different allocation request" in response.json()["detail"]
    assert _ranks(db_engine) == {d: 1, other: 2}
    assert _hot_rows_since(db_engine, mark) == []
    assert _entry(client, d)["allocated_quantity"] == 0
    _assert_dense(db_engine)


# ---------------------------------------------------------------------------
# Deadlock-freedom witnesses (follow-up §9.6)
# ---------------------------------------------------------------------------


def test_every_hot_lock_taker_follows_the_global_lock_order(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """D1: PN locks → Hot lock → station → ONE ascending demand pass →
    Work Orders, the Hot lock exactly where the follow-up §4.2 takes it."""
    _clear(client)
    pn = _unique("PN")
    target = _two_line_work_order(client, pn, 2)
    first_shift, second_shift = _demand(client), _demand(client)
    _added(client, target.demand_id, first_shift, second_shift)
    _stocked(client, shop, target, target.demand_id, pn, 2)

    # A Stockroom allocation that removes rank 1: its pass holds both shift rows.
    with _recording() as locks:
        allocated = _allocate(
            client, pn, [(target.demand_id, 2)], station_id=shop.stockroom.station_id
        )
    assert allocated.status_code == 201, allocated.text
    demand_ids = _assert_lock_order(locks, hot_lock=True)
    assert demand_ids == sorted([target.demand_id, first_shift, second_shift])
    assert _classes(locks)[:3] == ["A1", "A2", "R1"] and _classes(locks)[-1] == "R3"

    # A Management allocation (partial): no station.
    partial_pn = _unique("PN")
    partial = _work_order(client, [_line(partial_pn, 10)], number=_unique("WO"))
    _stocked(client, shop, partial, partial.demand_id, partial_pn, 4)
    with _recording() as locks:
        allocated = _allocate(client, partial_pn, [(partial.demand_id, 2)])
    assert allocated.status_code == 201, allocated.text
    _assert_lock_order(locks, hot_lock=True)
    assert "R1" not in _classes(locks)

    # The reversal (Management only since Phase 14 slice 3): no station row.
    with _recording() as locks:
        reversed_ = admin_of(client).post(
            f"/api/allocations/{allocated.json()['rows'][0]['allocation_id']}/reversals",
            json={"reason": "Recount", "device_event_id": str(uuid.uuid4())},
        )
    assert reversed_.status_code == 201, reversed_.text
    assert _assert_lock_order(locks, hot_lock=False) == [partial.demand_id]
    assert "R1" not in _classes(locks)

    # Work Order saves: a quantity edit takes the Hot lock, a due date does not.
    _added(client, partial.demand_id)
    with _recording() as locks:
        saved = admin_of(client).patch(
            f"/api/work-orders/{partial.id}",
            json={"line_edits": [{"id": partial.demand_id, "requested_quantity": 9}]},
        )
    assert saved.status_code == 200, saved.text
    _assert_lock_order(locks, hot_lock=True)
    with _recording() as locks:
        saved = admin_of(client).patch(
            f"/api/work-orders/{partial.id}",
            json={"line_edits": [{"id": partial.demand_id, "due_date": "2031-04-05"}]},
        )
    assert saved.status_code == 200, saved.text
    _assert_lock_order(locks, hot_lock=False)

    # Demand-line removal with and without the flag.
    removable = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    hot_line, plain_line = removable.demand_ids
    _added(client, hot_line)
    trailing = _demand(client)
    _added(client, trailing)
    with _recording() as locks:
        deleted = _delete_line(client, removable.id, hot_line, confirm=True)
    assert deleted.status_code == 204, deleted.text
    assert trailing in _assert_lock_order(locks, hot_lock=True)
    with _recording() as locks:
        deleted = _delete_line(client, removable.id, plain_line)
    assert deleted.status_code == 409, deleted.text  # the last line now
    assert _assert_lock_order(locks, hot_lock=False) == [plain_line]

    # The Hot command.
    expected = _order(client)
    with _recording() as locks:
        moved = _change(client, "MOVE_UP", expected, [*expected[:-2], expected[-1], expected[-2]])
    assert moved.status_code == 201, moved.text
    _assert_lock_order(locks, hot_lock=True)
    _assert_dense(db_engine)
    _assert_no_inactive_entry(db_engine)


def _smoke_round(client: TestClient, shop: _Shop, round_number: int) -> dict[str, Any]:
    """One round of D2: five Hot-lock-relevant commands released together."""
    _clear(client)
    x_pn = _unique("PN")
    x_work_order = _two_line_work_order(client, x_pn, 3)
    dx = x_work_order.demand_id
    _stocked(client, shop, x_work_order, dx, x_pn, 3)
    patched = _work_order(client, [_line(_unique("PN"), 5)], number=_unique("WO"))
    r_pn = _unique("PN")
    released = _work_order(client, [_line(r_pn, 5)], number=_unique("WO"))
    dm = _demand(client)
    deleted = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    dd = deleted.demand_id
    _added(client, dx, patched.demand_id, released.demand_id, dm, dd)
    barrier = threading.Barrier(5)

    def move() -> Any:
        order = _order(client)
        index = order.index(dm)
        new = [*order[: index - 1], dm, order[index - 1], *order[index + 1 :]]
        return _change(client, "MOVE_UP", order, new)

    calls: dict[str, Callable[[], Any]] = {
        "allocate": lambda: _allocate(client, x_pn, [(dx, 3)]),
        "patch": lambda: admin_of(client).patch(
            f"/api/work-orders/{patched.id}",
            json={
                "line_edits": [
                    {"id": patched.demand_id, "due_date": f"2031-01-{round_number + 1:02d}"}
                ]
            },
        ),
        "release": lambda: _release_response(
            client, shop.material, released, released.demand_id, r_pn, 2
        ),
        "move": move,
        "delete": lambda: _delete_line(client, deleted.id, dd, confirm=True),
    }

    def gated(call: Callable[[], Any]) -> Callable[[], Any]:
        def run() -> Any:
            barrier.wait(timeout=20)
            return call()

        return run

    results: dict[str, Any] = {}
    threads = [_start(results, key, gated(call)) for key, call in calls.items()]
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a command never finished"
    return results


def test_concurrent_hot_lock_takers_never_deadlock(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    """D2: bounded smoke — an allocation removing rank 1, a due-date save
    and a release of shift-row demands, a fresh-read MOVE and a confirmed
    Hot line deletion, released together for several rounds: no 500, no
    deadlock (40P01), H1 and H2 at the end of every round."""
    expected = {
        "allocate": {201},
        "patch": {200},
        "release": {201},
        # Built from a fresh read: a concurrent rank change makes it stale.
        "move": {201, 409},
        "delete": {204},
    }
    for round_number in range(8):
        results = _smoke_round(client, shop, round_number)
        for key, result in results.items():
            assert not isinstance(result, Exception), f"{key}: {result!r}"
            assert "deadlock" not in result.text.lower(), result.text
            assert result.status_code in expected[key], (key, result.status_code, result.text)
        _assert_dense(db_engine)
        _assert_no_inactive_entry(db_engine)
