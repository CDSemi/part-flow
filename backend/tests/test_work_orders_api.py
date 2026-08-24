"""Integration tests for the Phase 4 Work Order intake API.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Covered per the Phase 4
intake scope (PROJECT_PROFILE §7 Work Order, §8.2, §8.3, §13;
SLICE1_DATA_MODEL §5, §7, §16; GUI_DESIGN §11.1–§11.3):

- a blank confirmed Work Order Number persists NULL (no temporary
  number), multiple NULL numbers coexist, and an entered number is
  stored verbatim and unique — an existing number resolves the
  existing Work Order and never creates a duplicate;
- both due dates are nullable and never a validation error;
- demand lines carry the canonical PN by value: normalization/case
  variants reuse one PN master, created exactly once on first valid
  use; internal whitespace is rejected with zero writes;
- requested quantity must be a positive integer; a duplicate PN on
  one Work Order is rejected;
- saving demand is business demand only: zero QuantityFlows and zero
  PartMovements, always;
- every create/edit appends its audit row in the SAME transaction:
  an audit failure rolls back the business change, and a business
  write that fails at COMMIT takes its audit rows down with it;
- server-owned fields (status, allocated_quantity, priority_rank)
  are rejected by ``extra="forbid"`` schemas.
- one canonical PN appears at most once among a Work Order's
  current demand lines, and the rule is serialized against itself:
  two concurrent saves adding the same PN cannot both create a
  line (the loser waits on the parent Work Order row lock,
  re-reads the PN set under it, and is refused writing nothing);
- the active list is bounded server-side while the exact
  ``number=`` resolution reaches every Work Order.

The API commits real transactions, so tests isolate through unique
numbers/PNs; the module database is dropped afterwards.
"""

import os
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import work_orders
from app.application.errors import InvalidInputError
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_work_orders_api"


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.render_as_string(hide_password=False))
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
    """Temporary database migrated to head for the API under test."""
    admin_engine = create_engine(make_url(os.environ["DATABASE_URL"]), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{_TEST_DATABASE}"'))
    url = make_url(os.environ["DATABASE_URL"]).set(database=_TEST_DATABASE)
    command.upgrade(_alembic_config(url), "head")
    yield url
    with admin_engine.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{_TEST_DATABASE}" WITH (FORCE)'))
    admin_engine.dispose()


@pytest.fixture(scope="module")
def client(api_database_url: URL) -> Iterator[TestClient]:
    """Application client wired to the temporary database."""
    original_url = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = api_database_url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        os.environ["DATABASE_URL"] = original_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def db_engine(api_database_url: URL) -> Iterator[Engine]:
    """Direct database access for state verification."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _line(**overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {"part_number": _unique("PN"), "requested_quantity": 5}
    draft.update(overrides)
    return draft


def _create_work_order(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"lines": [_line()]}
    payload.update(overrides)
    response = client.post("/api/work-orders", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _count(engine: Engine, table: sa.FromClause) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def _write_counts(engine: Engine) -> dict[str, int]:
    """Everything a demand save may write — and what it never may."""
    return {
        "work_orders": _count(engine, models.WorkOrder.__table__),
        "work_order_demands": _count(engine, models.WorkOrderDemand.__table__),
        "part_numbers": _count(engine, models.PartNumber.__table__),
        "audit_events": _count(engine, models.AuditEvent.__table__),
        "quantity_flows": _count(engine, models.QuantityFlow.__table__),
        "part_movements": _count(engine, models.PartMovement.__table__),
    }


def _audit_rows(engine: Engine, entity_type: str, entity_id: str) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AuditEvent.__table__)
                .where(
                    models.AuditEvent.entity_type == entity_type,
                    models.AuditEvent.entity_id == entity_id,
                )
                .order_by(models.AuditEvent.id)
            )
        )


# ---------------------------------------------------------------------------
# Work Order Number — nullable, verbatim, unique
# ---------------------------------------------------------------------------


def test_blank_work_order_number_persists_null(client: TestClient, db_engine: Engine) -> None:
    """Omitted, null, empty, and whitespace-only all persist NULL on an
    internal Work Order — no temporary number is ever generated."""
    created = [
        _create_work_order(client),
        _create_work_order(client, work_order_number=None),
        _create_work_order(client, work_order_number=""),
        _create_work_order(client, work_order_number="   "),
    ]
    for body in created:
        assert body["work_order_number"] is None
        assert body["status"] == "OPEN"
    with db_engine.connect() as connection:
        stored = connection.execute(
            sa.select(models.WorkOrder.work_order_number).where(
                models.WorkOrder.id.in_([body["id"] for body in created])
            )
        ).scalars()
        assert list(stored) == [None, None, None, None]


def test_multiple_internal_work_orders_without_numbers_coexist(client: TestClient) -> None:
    """Uniqueness applies to non-null numbers only (partial index)."""
    first = _create_work_order(client, work_order_number=None)
    second = _create_work_order(client, work_order_number=None)
    assert first["id"] != second["id"]
    assert first["work_order_number"] is None
    assert second["work_order_number"] is None


def test_entered_number_is_verbatim_and_never_duplicated(
    client: TestClient, db_engine: Engine
) -> None:
    """An entered number is stored verbatim (never reformatted); an
    existing number is a conflict that creates nothing — the lookup
    endpoint resolves the existing Work Order instead."""
    number = f" wo 07 {uuid.uuid4().hex[:6]} "  # spaces and case preserved
    created = _create_work_order(client, work_order_number=number)
    assert created["work_order_number"] == number

    counts_before = _write_counts(db_engine)
    duplicate = client.post(
        "/api/work-orders", json={"work_order_number": number, "lines": [_line()]}
    )
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]
    assert _write_counts(db_engine) == counts_before

    resolved = client.get("/api/work-orders", params={"number": number})
    assert [row["id"] for row in resolved.json()] == [created["id"]]
    # Verbatim equality: the trimmed variant is a different number.
    assert client.get("/api/work-orders", params={"number": number.strip()}).json() == []


def test_due_dates_are_nullable_valid_data(client: TestClient) -> None:
    """A missing WO or line due date is valid data, never an error."""
    body = _create_work_order(
        client,
        due_date=None,
        lines=[_line(due_date=None), _line(due_date="2026-09-15")],
    )
    assert body["due_date"] is None
    assert [line["due_date"] for line in body["demands"]] == [None, "2026-09-15"]

    # An explicit "No due date" edit is equally valid.
    cleared = client.patch(
        f"/api/work-orders/{body['id']}",
        json={"line_edits": [{"id": body["demands"][1]["id"], "due_date": None}]},
    )
    assert cleared.status_code == 200, cleared.text
    assert [line["due_date"] for line in cleared.json()["demands"]] == [None, None]


# ---------------------------------------------------------------------------
# PN handling on demand lines
# ---------------------------------------------------------------------------


def test_line_part_numbers_normalize_and_reuse_one_master(
    client: TestClient, db_engine: Engine
) -> None:
    """Case/whitespace variants of one PN resolve to one canonical value
    and one master row, created exactly once with one CREATED audit."""
    canonical = _unique("PN")
    first = _create_work_order(client, lines=[_line(part_number=f"  {canonical.lower()}  ")])
    assert first["demands"][0]["part_number"] == canonical

    second = _create_work_order(client, lines=[_line(part_number=canonical)])
    assert second["demands"][0]["part_number"] == canonical

    with db_engine.connect() as connection:
        masters = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartNumber.__table__)
            .where(models.PartNumber.part_number == canonical)
        ).scalar_one()
    assert masters == 1
    assert len(_audit_rows(db_engine, "PartNumber", canonical)) == 1


def test_internal_whitespace_pn_is_rejected_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    """An invalid PN rejects the whole save: no Work Order, no demand,
    no PN master, no audit row."""
    counts_before = _write_counts(db_engine)
    for invalid in ("ABC 123", "ABC\t123", "ABC\n123", ""):
        rejected = client.post(
            "/api/work-orders",
            json={"lines": [_line(), _line(part_number=invalid)]},
        )
        assert rejected.status_code == 422, rejected.text
    assert _write_counts(db_engine) == counts_before


def test_duplicate_pn_on_one_work_order_is_rejected(client: TestClient, db_engine: Engine) -> None:
    """One PN, one demand line per Work Order — in the create draft and
    when adding lines later (the UI focuses the existing line)."""
    canonical = _unique("PN")
    counts_before = _write_counts(db_engine)
    rejected = client.post(
        "/api/work-orders",
        json={"lines": [_line(part_number=canonical), _line(part_number=canonical.lower())]},
    )
    assert rejected.status_code == 422
    assert "already on this Work Order" in rejected.json()["detail"]
    assert _write_counts(db_engine) == counts_before

    body = _create_work_order(client, lines=[_line(part_number=canonical)])
    added = client.patch(
        f"/api/work-orders/{body['id']}",
        json={"new_lines": [_line(part_number=f" {canonical.lower()} ")]},
    )
    assert added.status_code == 422
    assert "already on this Work Order" in added.json()["detail"]


def test_requested_quantity_must_be_a_positive_integer(
    client: TestClient, db_engine: Engine
) -> None:
    counts_before = _write_counts(db_engine)
    for quantity in (0, -5):
        rejected = client.post(
            "/api/work-orders", json={"lines": [_line(requested_quantity=quantity)]}
        )
        assert rejected.status_code == 422, rejected.text
        assert "positive whole number" in rejected.json()["detail"]
    # Shape-level rejections (schema): non-integers never reach the
    # Application layer.
    for non_integer in ("three", 2.5, True, None):
        rejected = client.post(
            "/api/work-orders", json={"lines": [_line(requested_quantity=non_integer)]}
        )
        assert rejected.status_code == 422, rejected.text
    assert _write_counts(db_engine) == counts_before


def test_a_work_order_needs_at_least_one_demand_line(client: TestClient, db_engine: Engine) -> None:
    counts_before = _write_counts(db_engine)
    rejected = client.post("/api/work-orders", json={"lines": []})
    assert rejected.status_code == 422
    assert "at least one demand line" in rejected.json()["detail"]
    assert _write_counts(db_engine) == counts_before


# ---------------------------------------------------------------------------
# Demand save is business demand only
# ---------------------------------------------------------------------------


def test_saving_demand_creates_zero_production_data(client: TestClient, db_engine: Engine) -> None:
    """Save and edit demand freely: zero QuantityFlows and zero
    PartMovements, always — release is a separate, later capability."""
    body = _create_work_order(
        client,
        lines=[_line(request_type="MODIFY", job_numbers=["17555", "17556"]), _line()],
    )
    line_id = body["demands"][0]["id"]
    edited = client.patch(
        f"/api/work-orders/{body['id']}",
        json={
            "due_date": "2026-10-01",
            "line_edits": [{"id": line_id, "requested_quantity": 9}],
            "new_lines": [_line()],
        },
    )
    assert edited.status_code == 200, edited.text
    assert _count(db_engine, models.QuantityFlow.__table__) == 0
    assert _count(db_engine, models.PartMovement.__table__) == 0


def test_manual_entry_defaults_and_metadata_round_trip(client: TestClient) -> None:
    """Request Type defaults to NEW; Job Numbers stay verbatim external
    metadata; requester/reason/notes persist per schema."""
    body = _create_work_order(
        client,
        lines=[
            _line(
                job_numbers=[" 17555 ", "JOB/2"],
                requester="Kim",
                reason="Customer order",
                notes="Rush",
            )
        ],
    )
    line = body["demands"][0]
    assert line["request_type"] == "NEW"
    assert line["job_numbers"] == [" 17555 ", "JOB/2"]
    assert line["requester"] == "Kim"
    assert line["reason"] == "Customer order"
    assert line["notes"] == "Rush"
    assert line["allocated_quantity"] == 0
    assert line["priority_rank"] is None


# ---------------------------------------------------------------------------
# Audit protocol — same transaction, both directions
# ---------------------------------------------------------------------------


def test_create_appends_all_audit_rows_in_one_transaction(
    client: TestClient, db_engine: Engine
) -> None:
    """One create: WorkOrder CREATED, one CREATED per demand line, and
    one PartNumber CREATED for the first-use PN only."""
    existing_pn = _unique("PN")
    assert client.post("/api/part-numbers", json={"part_number": existing_pn}).status_code == 201
    new_pn = _unique("PN")

    number = _unique("WO")
    body = _create_work_order(
        client,
        work_order_number=number,
        received_date="2026-08-19",
        lines=[_line(part_number=existing_pn), _line(part_number=new_pn)],
    )

    wo_events = _audit_rows(db_engine, "WorkOrder", str(body["id"]))
    assert [event.event_type for event in wo_events] == ["CREATED"]
    assert wo_events[0].before_data is None
    assert wo_events[0].after_data == {
        "work_order_number": number,
        "received_date": "2026-08-19",
        "due_date": None,
        "status": "OPEN",
    }
    for line in body["demands"]:
        events = _audit_rows(db_engine, "WorkOrderDemand", str(line["id"]))
        assert [event.event_type for event in events] == ["CREATED"]
        assert events[0].after_data["part_number"] == line["part_number"]
    # First use created the new PN's audit row; the reused PN got none.
    assert len(_audit_rows(db_engine, "PartNumber", new_pn)) == 1
    assert len(_audit_rows(db_engine, "PartNumber", existing_pn)) == 1


def test_edits_append_updated_rows_and_unchanged_saves_append_nothing(
    client: TestClient, db_engine: Engine
) -> None:
    """The audited Work Order Number edit and demand edits append
    UPDATED rows with before/after; a no-op save appends nothing."""
    body = _create_work_order(client, work_order_number=None)
    line = body["demands"][0]
    number = _unique("WO")

    edited = client.patch(
        f"/api/work-orders/{body['id']}",
        json={
            "work_order_number": number,
            "line_edits": [{"id": line["id"], "requested_quantity": 42}],
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["work_order_number"] == number

    wo_events = _audit_rows(db_engine, "WorkOrder", str(body["id"]))
    assert [event.event_type for event in wo_events] == ["CREATED", "UPDATED"]
    assert wo_events[1].before_data["work_order_number"] is None
    assert wo_events[1].after_data["work_order_number"] == number

    line_events = _audit_rows(db_engine, "WorkOrderDemand", str(line["id"]))
    assert [event.event_type for event in line_events] == ["CREATED", "UPDATED"]
    assert line_events[1].before_data["requested_quantity"] == line["requested_quantity"]
    assert line_events[1].after_data["requested_quantity"] == 42

    # Saving the same values again changes nothing and audits nothing.
    unchanged = client.patch(
        f"/api/work-orders/{body['id']}",
        json={
            "work_order_number": number,
            "line_edits": [{"id": line["id"], "requested_quantity": 42}],
        },
    )
    assert unchanged.status_code == 200
    assert len(_audit_rows(db_engine, "WorkOrder", str(body["id"]))) == 2
    assert len(_audit_rows(db_engine, "WorkOrderDemand", str(line["id"]))) == 2


def test_failed_audit_write_rolls_back_the_whole_save(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit row and business change commit together or not at all:
    an audit failure leaves no Work Order, demand, or PN behind."""
    counts_before = _write_counts(db_engine)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        client.post(
            "/api/work-orders",
            json={"work_order_number": _unique("WO"), "lines": [_line()]},
        )
    monkeypatch.undo()
    assert _write_counts(db_engine) == counts_before


def test_business_write_lost_at_commit_takes_its_audit_rows_with_it(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A uniqueness race lost at COMMIT (pre-check bypassed to simulate
    the concurrent writer) rolls back the number edit AND its audit
    row — the constraint stays the authority."""
    taken = _create_work_order(client, work_order_number=_unique("WO"))
    target = _create_work_order(client, work_order_number=_unique("WO"))
    events_before = len(_audit_rows(db_engine, "WorkOrder", str(target["id"])))

    from app.application import work_orders as work_orders_module

    monkeypatch.setattr(
        work_orders_module,
        "_reject_duplicate_work_order_number",
        lambda *args, **kwargs: None,
    )
    conflicted = client.patch(
        f"/api/work-orders/{target['id']}",
        json={"work_order_number": taken["work_order_number"]},
    )
    monkeypatch.undo()
    assert conflicted.status_code == 409
    assert "already exists" in conflicted.json()["detail"]

    detail = client.get(f"/api/work-orders/{target['id']}")
    assert detail.json()["work_order_number"] == target["work_order_number"]
    assert len(_audit_rows(db_engine, "WorkOrder", str(target["id"]))) == events_before


# ---------------------------------------------------------------------------
# Surface protection
# ---------------------------------------------------------------------------


def test_server_owned_fields_are_rejected(client: TestClient) -> None:
    """extra="forbid": status, allocation, priority, and the PN of a
    saved line are never client-writable."""
    assert (
        client.post("/api/work-orders", json={"status": "RELEASED", "lines": [_line()]}).status_code
        == 422
    )
    assert (
        client.post("/api/work-orders", json={"lines": [_line(allocated_quantity=3)]}).status_code
        == 422
    )
    assert (
        client.post("/api/work-orders", json={"lines": [_line(priority_rank=1)]}).status_code == 422
    )
    body = _create_work_order(client)
    line_id = body["demands"][0]["id"]
    assert (
        client.patch(
            f"/api/work-orders/{body['id']}",
            json={"line_edits": [{"id": line_id, "part_number": _unique("PN")}]},
        ).status_code
        == 422
    )


def test_explicit_null_request_type_on_edit_is_rejected(
    client: TestClient, db_engine: Engine
) -> None:
    """The NEW default applies to creating a line only: an edit that
    sends request_type null is rejected with zero writes — never
    silently reinterpreted as NEW."""
    body = _create_work_order(client, lines=[_line(request_type="MODIFY")])
    line = body["demands"][0]
    events_before = _audit_rows(db_engine, "WorkOrderDemand", str(line["id"]))

    rejected = client.patch(
        f"/api/work-orders/{body['id']}",
        json={"line_edits": [{"id": line["id"], "request_type": None}]},
    )
    assert rejected.status_code == 422
    assert "Request Type cannot be cleared" in rejected.json()["detail"]

    detail = client.get(f"/api/work-orders/{body['id']}").json()
    assert detail["demands"][0]["request_type"] == "MODIFY"
    assert _audit_rows(db_engine, "WorkOrderDemand", str(line["id"])) == events_before

    # Omitted keeps the value; an explicit value still updates normally.
    updated = client.patch(
        f"/api/work-orders/{body['id']}",
        json={"line_edits": [{"id": line["id"], "request_type": "NEW"}]},
    )
    assert updated.status_code == 200
    assert updated.json()["demands"][0]["request_type"] == "NEW"


def test_client_supplied_audit_actor_is_rejected(client: TestClient, db_engine: Engine) -> None:
    """No HTTP request carries an audit actor: `actor` is rejected by
    extra="forbid" everywhere, and audit rows written by these
    workflows keep actor_reference NULL until Phase 14."""
    assert (
        client.post(
            "/api/work-orders",
            json={"lines": [_line()], "actor": "mallory"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/part-numbers",
            json={"part_number": _unique("PN"), "actor": "mallory"},
        ).status_code
        == 422
    )
    body = _create_work_order(client)
    assert (
        client.patch(
            f"/api/work-orders/{body['id']}",
            json={"due_date": "2026-11-01", "actor": "mallory"},
        ).status_code
        == 422
    )

    # The rows the clean create/edit produced all carry a NULL actor.
    edited = client.patch(f"/api/work-orders/{body['id']}", json={"due_date": "2026-11-01"})
    assert edited.status_code == 200
    wo_events = _audit_rows(db_engine, "WorkOrder", str(body["id"]))
    line_events = _audit_rows(db_engine, "WorkOrderDemand", str(body["demands"][0]["id"]))
    pn_events = _audit_rows(db_engine, "PartNumber", body["demands"][0]["part_number"])
    assert [event.event_type for event in wo_events] == ["CREATED", "UPDATED"]
    for event in [*wo_events, *line_events, *pn_events]:
        assert event.actor_reference is None


def test_duplicate_demand_ids_in_one_save_are_rejected(
    client: TestClient, db_engine: Engine
) -> None:
    """One demand line may appear at most once per Save: duplicates are
    rejected with zero writes and zero audit rows — no intermediate
    states, no multiple UPDATED events for one Save."""
    body = _create_work_order(client, lines=[_line(requested_quantity=5)])
    line = body["demands"][0]
    counts_before = _write_counts(db_engine)
    events_before = _audit_rows(db_engine, "WorkOrderDemand", str(line["id"]))

    rejected = client.patch(
        f"/api/work-orders/{body['id']}",
        json={
            "due_date": "2026-12-01",
            "line_edits": [
                {"id": line["id"], "requested_quantity": 7},
                {"id": line["id"], "requested_quantity": 9},
            ],
        },
    )
    assert rejected.status_code == 422
    assert "appears more than once" in rejected.json()["detail"]

    assert _write_counts(db_engine) == counts_before
    assert _audit_rows(db_engine, "WorkOrderDemand", str(line["id"])) == events_before
    detail = client.get(f"/api/work-orders/{body['id']}").json()
    assert detail["demands"][0]["requested_quantity"] == 5
    assert detail["due_date"] is None


def test_unknown_work_order_and_foreign_line_are_not_found(client: TestClient) -> None:
    assert client.get("/api/work-orders/999999").status_code == 404
    first = _create_work_order(client)
    second = _create_work_order(client)
    foreign = client.patch(
        f"/api/work-orders/{first['id']}",
        json={"line_edits": [{"id": second["demands"][0]["id"], "requested_quantity": 2}]},
    )
    assert foreign.status_code == 404
    assert "does not exist on Work Order" in foreign.json()["detail"]


def test_list_and_search_over_work_order_numbers(client: TestClient) -> None:
    """The WO list row carries the demand aggregate; search is a
    case-insensitive contains-match over the number."""
    marker = uuid.uuid4().hex[:8].upper()
    pn_one, pn_two = _unique("PN"), _unique("PN")
    body = _create_work_order(
        client,
        work_order_number=f"WO-{marker}-X",
        lines=[_line(part_number=pn_one), _line(part_number=pn_two)],
    )
    found = client.get("/api/work-orders", params={"search": marker.lower()})
    assert found.status_code == 200
    rows = found.json()
    assert [row["id"] for row in rows] == [body["id"]]
    assert rows[0]["demand_line_count"] == 2
    assert rows[0]["part_numbers"] == [pn_one, pn_two]
    assert rows[0]["status"] == "OPEN"


# ---------------------------------------------------------------------------
# One canonical PN at most once per Work Order — serialized, not hoped for
# ---------------------------------------------------------------------------


class _PauseFirstWorkOrderLock:
    """Test seam: pause a save AFTER it holds the parent Work Order row
    lock and has re-read that Work Order's PN set, so a competing save
    adding the same PN can be started and observed blocking on the same
    lock rather than racing past it."""

    def __init__(self) -> None:
        self.real = work_orders._lock_work_order_and_read_part_numbers
        self.inside = threading.Event()
        self.let_finish = threading.Event()
        self._guard = threading.Lock()
        self._paused_once = False

    def __call__(self, session: Session, work_order_id: int) -> set[str]:
        result = self.real(session, work_order_id)
        with self._guard:
            should_pause = not self._paused_once
            self._paused_once = True
        if should_pause:
            self.inside.set()
            assert self.let_finish.wait(timeout=20), "test deadlock: never released"
        return result


def _demand_count_for(engine: Engine, work_order_id: int, part_number: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(sa.func.count())
            .select_from(models.WorkOrderDemand.__table__)
            .where(
                models.WorkOrderDemand.work_order_id == work_order_id,
                models.WorkOrderDemand.part_number == part_number,
            )
        ).scalar_one()


def test_concurrent_adds_of_the_same_part_number_cannot_both_create_a_line(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent saves adding the SAME PN to the SAME Work Order.

    The rule "one canonical PN appears at most once among a Work
    Order's current demand lines" is decided from a set read before any
    waiting, so it is only a rule if the read happens under the Work
    Order's row lock. The loser here waits on that lock, re-reads the
    PN set once the winner committed, and refuses its duplicate —
    writing nothing at all, not even the PN master or an audit row.

    The PN master EXISTS before the race on purpose: with a brand-new
    PN the loser would be stopped incidentally by the `part_numbers`
    primary-key race inside `ensure_part_number`, which would prove
    nothing about this rule.
    """
    duplicate_pn = _unique("RACE")
    master = client.post("/api/part-numbers", json={"part_number": duplicate_pn})
    assert master.status_code == 201, master.text

    body = _create_work_order(client, lines=[_line()])
    work_order_id = int(body["id"])
    before = _write_counts(db_engine)

    pause = _PauseFirstWorkOrderLock()
    monkeypatch.setattr(work_orders, "_lock_work_order_and_read_part_numbers", pause)
    results: dict[str, Any] = {}

    def add(key: str) -> None:
        with Session(db_engine) as session:
            try:
                work_orders.update_work_order(
                    session,
                    work_order_id,
                    new_lines=[{"part_number": duplicate_pn, "requested_quantity": 4}],
                )
                results[key] = "ok"
            except Exception as exc:  # noqa: BLE001 — collected for assertions
                results[key] = exc

    winner = threading.Thread(target=add, args=("winner",), daemon=True)
    loser = threading.Thread(target=add, args=("loser",), daemon=True)
    try:
        winner.start()
        assert pause.inside.wait(timeout=20)  # holds the Work Order row lock
        loser.start()
        # The overlap is real: the second save is blocked on the lock,
        # not finished and not failed.
        time.sleep(1.0)
        assert "loser" not in results
    finally:
        pause.let_finish.set()
    winner.join(timeout=20)
    loser.join(timeout=20)
    assert not winner.is_alive() and not loser.is_alive()

    assert results["winner"] == "ok"
    assert isinstance(results["loser"], InvalidInputError)
    assert "already on this Work Order" in str(results["loser"])

    # Exactly one demand line carries the PN, and the loser wrote nothing.
    assert _demand_count_for(db_engine, work_order_id, duplicate_pn) == 1
    after = _write_counts(db_engine)
    assert after["work_order_demands"] == before["work_order_demands"] + 1
    assert after["part_numbers"] == before["part_numbers"]
    assert after["audit_events"] == before["audit_events"] + 1  # the one created line
    assert after["quantity_flows"] == before["quantity_flows"]
    assert after["part_movements"] == before["part_movements"]

    # The Work Order is not left in a state the UI cannot save again.
    detail = client.get(f"/api/work-orders/{work_order_id}").json()
    assert [d["part_number"] for d in detail["demands"]].count(duplicate_pn) == 1
    line_id = next(d["id"] for d in detail["demands"] if d["part_number"] == duplicate_pn)
    edited = client.patch(
        f"/api/work-orders/{work_order_id}",
        json={"line_edits": [{"id": line_id, "requested_quantity": 9}]},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["demands"][-1]["requested_quantity"] == 9
    added = client.patch(
        f"/api/work-orders/{work_order_id}",
        json={"new_lines": [_line()]},
    )
    assert added.status_code == 200, added.text


def _race_one_add(client: TestClient, work_order_id: int, part_number: str) -> list[int]:
    """Two threads PATCH the same Work Order with the same new PN at the
    same instant; returns both HTTP status codes."""
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    guard = threading.Lock()

    def patch_add() -> None:
        barrier.wait(timeout=20)
        response = client.patch(
            f"/api/work-orders/{work_order_id}",
            json={"new_lines": [{"part_number": part_number, "requested_quantity": 3}]},
        )
        with guard:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=patch_add, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    return statuses


def test_unsynchronized_concurrent_adds_never_duplicate_a_part_number(
    client: TestClient, db_engine: Engine
) -> None:
    """The same race with no seam, over the real HTTP surface.

    No pause, no monkeypatch: two threads PATCH the same Work Order at
    the same instant, several rounds. Whatever the interleaving, the
    Work Order may never end up with two lines for one canonical PN,
    and the losing request must fail cleanly (422) rather than
    half-write.
    """
    duplicate_pn = _unique("STRESS")
    assert client.post("/api/part-numbers", json={"part_number": duplicate_pn}).status_code == 201

    for _ in range(8):
        body = _create_work_order(client, lines=[_line()])
        work_order_id = int(body["id"])
        statuses = _race_one_add(client, work_order_id, duplicate_pn)
        assert _demand_count_for(db_engine, work_order_id, duplicate_pn) == 1
        assert sorted(statuses) == [200, 422], statuses


# ---------------------------------------------------------------------------
# The active list is bounded in the query; exact resolution is not
# ---------------------------------------------------------------------------


def test_active_list_and_search_are_bounded_by_the_server(
    client: TestClient, db_engine: Engine
) -> None:
    """More Work Orders than the bound come back bounded, in order.

    Nothing leaves the active list before allocation-derived completion
    (Phase 10), so an unbounded read would only ever grow. The bound is
    applied in the query — the tail is truncated in canonical order,
    never sampled — and `search` narrows it server-side.
    """
    limit = work_orders.LIST_RESULT_LIMIT
    marker = uuid.uuid4().hex[:8].upper()
    # Seeded directly: this is about the read path, and creating
    # limit + 20 Work Orders through the API would only be slower.
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO work_orders (work_order_number, received_date, status)"
                " SELECT :marker || '-' || lpad(g::text, 4, '0'), DATE '2030-01-01' + g,"
                " 'OPEN' FROM generate_series(1, :count) g"
            ),
            {"marker": marker, "count": limit + 20},
        )

    rows = client.get("/api/work-orders", params={"search": marker})
    assert rows.status_code == 200
    numbers = [row["work_order_number"] for row in rows.json()]
    assert len(numbers) == limit
    # Canonical order preserved: newest received_date first, so the
    # highest generated suffixes survive and the OLDEST are cut.
    expected = [f"{marker}-{index:04d}" for index in range(limit + 20, 20, -1)]
    assert numbers == expected

    # The unfiltered listing is bounded too.
    assert len(client.get("/api/work-orders").json()) == limit

    # A Work Order outside the default page is still reachable: by a
    # narrower search...
    narrow = client.get("/api/work-orders", params={"search": f"{marker}-0001"})
    assert [row["work_order_number"] for row in narrow.json()] == [f"{marker}-0001"]
    # ...and by the exact resolution, which is never bounded away.
    exact = client.get("/api/work-orders", params={"number": f"{marker}-0007"})
    assert exact.status_code == 200
    assert [row["work_order_number"] for row in exact.json()] == [f"{marker}-0007"]


def test_search_is_evaluated_in_the_database_not_after_the_bound(
    client: TestClient, db_engine: Engine
) -> None:
    """A match beyond the first page is found because the WHERE runs
    before the LIMIT — the bound never hides a real match."""
    limit = work_orders.LIST_RESULT_LIMIT
    marker = uuid.uuid4().hex[:8].upper()
    needle = f"{marker}-NEEDLE"
    with db_engine.begin() as connection:
        # The needle is the OLDEST, so an unbounded-then-filter client
        # would never see it inside a bounded page.
        connection.execute(
            sa.text(
                "INSERT INTO work_orders (work_order_number, received_date, status)"
                " VALUES (:needle, DATE '2029-01-01', 'OPEN')"
            ),
            {"needle": needle},
        )
        connection.execute(
            sa.text(
                "INSERT INTO work_orders (work_order_number, received_date, status)"
                " SELECT :marker || '-NOISE-' || g, DATE '2031-01-01' + g, 'OPEN'"
                " FROM generate_series(1, :count) g"
            ),
            {"marker": marker, "count": limit + 20},
        )

    everything = client.get("/api/work-orders", params={"search": marker})
    assert needle not in [row["work_order_number"] for row in everything.json()]
    found = client.get("/api/work-orders", params={"search": "NEEDLE"})
    assert [row["work_order_number"] for row in found.json()] == [needle]


def test_quantity_floor_also_binds_a_line_that_only_carries_allocation(
    client: TestClient, db_engine: Engine
) -> None:
    """`requested_quantity` may never fall below `max(released, allocated)`.

    Allocation arrives with Phase 10 and no Phase 4 endpoint writes
    `allocated_quantity` (`extra="forbid"` rejects it), so the value is
    seeded directly here. The point of the test is that the canonical
    floor of PROJECT_PROFILE §13 is written as `max(released,
    allocated)` and not silently reduced to "released only" — the
    locked FIELDS stay tied to release, the quantity FLOOR does not.
    """
    body = _create_work_order(client, lines=[_line(requested_quantity=30)])
    line_id = int(body["demands"][0]["id"])
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE work_order_demands SET allocated_quantity = 12 WHERE id = :id"),
            {"id": line_id},
        )

    too_low = client.patch(
        f"/api/work-orders/{body['id']}",
        json={"line_edits": [{"id": line_id, "requested_quantity": 11}]},
    )
    assert too_low.status_code == 409
    assert "12 pcs are already allocated" in too_low.json()["detail"]

    # Nothing about the line changed.
    assert (
        client.get(f"/api/work-orders/{body['id']}").json()["demands"][0]["requested_quantity"]
        == 30
    )

    # Down to exactly the committed quantity is valid, and Request Type
    # stays editable — nothing has been RELEASED for this line.
    exact = client.patch(
        f"/api/work-orders/{body['id']}",
        json={"line_edits": [{"id": line_id, "requested_quantity": 12, "request_type": "MODIFY"}]},
    )
    assert exact.status_code == 200, exact.text
    saved = exact.json()["demands"][0]
    assert (saved["requested_quantity"], saved["request_type"]) == (12, "MODIFY")
