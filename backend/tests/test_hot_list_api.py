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
  of an open one are refused with nothing written; inactive entries
  already on the list stay and can still be removed and moved, the
  Work Order staying read-only;
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
- the demand-line removal guard, the cross-consumer regression (the
  rank written by the command orders allocation, the Production Board,
  PN Tracking and the Area inventory) and the Work Order intake still
  rejecting ``priority_rank``.

The Hot list is one list for the whole module database, so every test
works against the order it reads (most start from an emptied list);
the API commits real transactions and the module database is dropped
afterwards.
"""

import os
import threading
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
from sqlalchemy.orm import Session

from alembic import command
from app.application import hot_list as hot_list_service
from app.application import work_orders
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

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
_HOT_REMOVAL = (
    "Cannot remove: this demand line is on the Hot list. Remove it from the Hot list in"
    " Management → Priority first."
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
            yield test_client
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
    response = client.put(
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
    response = client.post("/api/departments", json={"name": _unique("DEPT")})
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
        area = client.post(
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
        operation = client.post(
            "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
        )
        assert operation.status_code == 201, operation.text
        self.operation_id = int(operation.json()["id"])
        station = client.post(
            "/api/scan-stations", json={"station_id": _unique("ST"), "area_id": self.area_id}
        )
        assert station.status_code == 201, station.text
        self.station_id = str(station.json()["station_id"])
        self.machine_ids: list[int] = []
        for _ in range(machine_count):
            machine = client.post(
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
    response = client.post("/api/work-orders", json=payload)
    assert response.status_code == 201, response.text
    return _WorkOrder(response.json())


def _demand(client: TestClient, pn: str | None = None, quantity: int = 10) -> int:
    """One fresh eligible demand on its own numbered Work Order."""
    work_order = _work_order(client, [_line(pn or _unique("PN"), quantity)], number=_unique("WO"))
    return work_order.demand_id


def _release(
    client: TestClient,
    cell: _Cell,
    work_order: _WorkOrder,
    demand_id: int,
    pn: str,
    quantity: int,
) -> int:
    released = client.post(
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


def _allocate(client: TestClient, pn: str, lines: list[tuple[int, int]]) -> Any:
    return client.post(
        "/api/allocations",
        json={
            "part_number": pn,
            "allocation_quantity": sum(quantity for _, quantity in lines),
            "lines": [
                {"work_order_demand_id": demand_id, "quantity": quantity}
                for demand_id, quantity in lines
            ],
            "device_event_id": str(uuid.uuid4()),
        },
    )


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
    response = client.get("/api/hot-list")
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
    return client.post(
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


def _audit_count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(sa.func.count()).select_from(models.AuditEvent)
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
    extra = client.post(
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


def test_inactive_entries_stay_and_can_be_moved_and_removed(
    client: TestClient, shop: _Shop, db_engine: Engine
) -> None:
    _clear(client)
    pn = _unique("PN")
    work_order = _work_order(client, [_line(pn, 4)], number=_unique("WO"))
    other = _demand(client)
    _added(client, work_order.demand_id, other)
    # Completing the Work Order writes nothing to the Hot list.
    _fulfil(client, shop, work_order, work_order.demand_id, pn, 4)
    entry = _entry(client, work_order.demand_id)
    assert entry["rank"] == 1
    assert entry["work_order_completed"] is True
    assert entry["active"] is False

    moved = _change(
        client, "MOVE_DOWN", [work_order.demand_id, other], [other, work_order.demand_id]
    )
    assert moved.status_code == 201, moved.text
    assert _rank_of(db_engine, work_order.demand_id) == 2
    removed = _remove(client, work_order.demand_id)
    assert removed.status_code == 201, removed.text
    assert removed.json()["changes"] == [
        _change_line(work_order.demand_id, pn, work_order.number, 2, None)
    ]
    assert len(_audit_rows(db_engine, removed.json()["device_event_id"])) == 1

    # The Work Order stays completed, read-only history.
    detail = client.get(f"/api/work-orders/{work_order.id}")
    assert detail.status_code == 200 and detail.json()["status"] == "COMPLETED"
    edit = client.patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "requested_quantity": 9}]},
    )
    assert edit.status_code == 409
    _assert_dense(db_engine)


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

    deleted = client.delete(f"/api/work-orders/{work_order.id}/demands/{removable}")
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
        deactivated = client.patch(f"/api/departments/{second}", json={"is_active": False})
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
    """L3: the add holds the demand row lock — the removal waits, then
    sees the rank and refuses."""
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
            delete=client.delete(f"/api/work-orders/{work_order.id}/demands/{target}")
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
    assert results["delete"].json()["detail"] == _HOT_REMOVAL
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
            delete=client.delete(f"/api/work-orders/{work_order.id}/demands/{target}")
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
    """L4: the move holds the demand row locks; the allocation (PN lock →
    demand rows → Work Orders) waits for them and then completes."""
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
            client.get("/api/hot-list"),
            client.get("/api/hot-list/candidates"),
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
        deactivated = client.patch(f"/api/departments/{second}", json={"is_active": False})
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
            client.get("/api/hot-list"),
            client.get("/api/hot-list/candidates", params={"search": "x"}),
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
    response = client.get("/api/hot-list/candidates", params=params)
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
        response = client.get("/api/hot-list/candidates", params={"barcode": barcode})
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == _NOT_A_PN_BARCODE
    empty = client.get("/api/hot-list/candidates", params={"barcode": "PF:PN:"})
    assert empty.status_code == 422
    assert empty.json()["detail"] == "Part Number must not be empty."
    both = client.get("/api/hot-list/candidates", params={"barcode": "PF:PN:X", "search": "X"})
    assert both.status_code == 422
    # PostgreSQL text cannot hold NUL: a 422 before any query, not a 500.
    for params in ({"search": "a\x00b"}, {"barcode": "PF:PN:A\x00B"}):
        response = client.get("/api/hot-list/candidates", params=params)
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
    # A fully allocated line of an open Work Order, and a completed one.
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
    assert [entry["rank"] for entry in body["entries"]] == [1, 2, 3]

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

    fully = entries[full]
    assert fully["work_order_completed"] is False
    assert fully["shortage_quantity"] == 0 and fully["active"] is False
    completed = entries[done.demand_id]
    assert completed["work_order_completed"] is True and completed["active"] is False
    assert completed["part_number_locations"] == []

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


def test_removing_a_hot_demand_line_is_refused(client: TestClient, db_engine: Engine) -> None:
    _clear(client)
    work_order = _work_order(client, [_line(_unique("PN")), _line(_unique("PN"))])
    hot = work_order.demand_ids[0]
    _added(client, hot)
    refused = client.delete(f"/api/work-orders/{work_order.id}/demands/{hot}")
    assert refused.status_code == 409
    assert refused.json()["detail"] == _HOT_REMOVAL
    assert _demand_exists(db_engine, hot)
    # Off the Hot list, the same line removes normally.
    assert _remove(client, hot).status_code == 201
    assert client.delete(f"/api/work-orders/{work_order.id}/demands/{hot}").status_code == 204


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
    tracking = client.get("/api/tracking", params={"search": f"XC{token}"})
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


def test_a_fully_allocated_hot_line_of_an_open_work_order_keeps_its_rank_on_monitoring_rows(
    client: TestClient, shop: _Shop
) -> None:
    """OD1 as implemented: a ranked line that becomes fully allocated
    while its Work Order stays open keeps supplying its STORED rank to
    the Production Board, PN Tracking (Hot only included) and the Area
    inventory demand context until a manager removes or moves it."""
    _clear(client)
    pn = f"FA{uuid.uuid4().hex[:8].upper()}"
    # The second line keeps the Hot line's Work Order open.
    hot = _work_order(client, [_line(pn, 4), _line(_unique("PN"), 4)], number=_unique("WO"))
    later = _work_order(client, [_line(pn, 5, due_date="2031-01-01")], number=_unique("WO"))
    _added(client, hot.demand_id)
    _fulfil(client, shop, hot, hot.demand_id, pn, 4)
    # Quantity still in production gives the PN its monitoring rows.
    _release(client, shop.material, later, later.demand_id, pn, 2)
    entry = _entry(client, hot.demand_id)
    assert (entry["rank"], entry["work_order_completed"], entry["active"]) == (1, False, False)

    def board_rank() -> list[Any]:
        rows = client.get("/api/production-board").json()["rows"]
        return [row["hot_rank"] for row in rows if row["part_number"] == pn]

    assert board_rank() == [1]
    tracking = client.get("/api/tracking", params={"search": pn, "hot_only": "true"})
    assert tracking.status_code == 200, tracking.text
    assert [(row["part_number"], row["hot_rank"]) for row in tracking.json()["rows"]] == [(pn, 1)]
    inventory = client.get(f"/api/areas/{shop.material.area_id}/inventory")
    assert inventory.status_code == 200, inventory.text
    [context] = [item for item in inventory.json()["demand_context"] if item["part_number"] == pn]
    assert [(d["work_order_demand_id"], d["priority_rank"]) for d in context["demands"]] == [
        (hot.demand_id, 1),
        (later.demand_id, None),
    ]

    # Removing the entry hands the rows to the next demand, unranked.
    assert _remove(client, hot.demand_id).status_code == 201
    assert board_rank() == [None]
    tracking = client.get("/api/tracking", params={"search": pn, "hot_only": "true"})
    assert tracking.status_code == 200 and tracking.json()["rows"] == []


def test_work_order_intake_still_rejects_priority_rank(client: TestClient) -> None:
    created = client.post(
        "/api/work-orders", json={"lines": [_line(_unique("PN"), priority_rank=1)]}
    )
    assert created.status_code == 422
    work_order = _work_order(client, [_line(_unique("PN"))])
    edited = client.patch(
        f"/api/work-orders/{work_order.id}",
        json={"line_edits": [{"id": work_order.demand_id, "priority_rank": 1}]},
    )
    assert edited.status_code == 422
