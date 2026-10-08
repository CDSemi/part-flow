"""Integration tests for Part Numbers management (Phase 13 slice 7).

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Covered per the slice
contract (PROJECT_PROFILE §8.1, §21, §28; GUI_DESIGN §14; CD1, CD2,
CD11):

- edit of the optional details (Name / Description, revision, ERP id):
  partial, trimmed, ``null`` clears, a no-op writes nothing, the PN
  itself is never editable, and every effective edit appends one
  ``UPDATED`` row with full before/after snapshots;
- the PN image on the row: PNG / JPEG / WebP up to 2 MiB, refusals
  store nothing, identical bytes are a no-op, served with an ETag and
  304, audited as a digest — never bytes — and never loaded by a list,
  page, create or edit;
- the hard delete removes only the master — demand, flows, Movements,
  allocations, lineage, routes and prior history stay byte-identical —
  with one ``DELETED`` row, and the PN gets a master again on its next
  use;
- the bounded management page and the Phase 4 search over the name;
- PNs with URL-hostile characters round-trip through every query route;
- locks: the create serializes with production create-on-first-use on
  the PN advisory lock (no production 409), while edit, image and
  delete take only the master's row lock and never wait on production;
- atomicity, and the read models (Production Board ``master``, PN
  Tracking ``name`` and detail metadata).

The API commits real transactions, so tests isolate through unique PN
values; the module database is dropped afterwards.
"""

import datetime
import functools
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from alembic import command
from app.application import common, images, part_numbers
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of, station_device_client
from tests.conftest import owner_connection

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_part_number_management_api"
_DB_URL_ENV = "DATABASE_URL"

_RESPONSE_KEYS = {
    "part_number",
    "barcode_value",
    "name",
    "current_revision",
    "erp_id",
    "image_updated_at",
    "created_at",
    "updated_at",
}
_UNSUPPORTED = "The file is not a PNG, JPEG or WebP image."
_TOO_LARGE = "The image is larger than 2 MB. Choose a smaller image."

# Minimal byte fixtures carrying the real magic bytes of each type.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x02" * 16
_WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x03" * 16
_GIF = b"GIF89a" + b"\x04" * 16

# A SELECT that names the image bytes column itself — not image_type or
# image_updated_at.
_IMAGE_COLUMN = re.compile(r"\bpart_numbers\.image\b(?!_)")


def _alembic_config(database_url: URL) -> Config:
    config = Config(str(_BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # ConfigParser interpolation reserves "%": escape the percent-encoded URL.
    url = database_url.render_as_string(hide_password=False).replace("%", "%%")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="module")
def api_database_url() -> Iterator[URL]:
    """Temporary database migrated to head for the API under test."""
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
    """Application client wired to the temporary database."""
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
    """Direct database access for state verification and lock holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique(prefix: str = "PN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _snapshot(
    part_number: str,
    name: str | None = None,
    current_revision: str | None = None,
    erp_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "part_number": part_number,
        "name": name,
        "current_revision": current_revision,
        "erp_id": erp_id,
    }


def _no_details(part_number: str) -> str:
    return f"Part Number {part_number} has no saved details."


def _already_saved(part_number: str) -> str:
    return f"Part Number “{part_number}” already has saved details."


def _digest(data: bytes, content_type: str) -> dict[str, str | int]:
    return images.image_digest(data, content_type)


def _create(client: TestClient, part_number: str, **details: Any) -> dict[str, Any]:
    response = admin_of(client).post(
        "/api/part-numbers", json={"part_number": part_number, **details}
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _patch(client: TestClient, part_number: str, body: dict[str, Any]) -> Any:
    return admin_of(client).patch("/api/part-numbers", params={"number": part_number}, json=body)


def _put_image(
    client: TestClient, part_number: str, data: bytes, content_type: str = "image/png"
) -> Any:
    return admin_of(client).put(
        "/api/part-numbers/image",
        params={"number": part_number},
        content=data,
        headers={"Content-Type": content_type},
    )


def _audit_rows(engine: Engine, part_number: str) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AuditEvent.__table__)
                .where(
                    models.AuditEvent.entity_type == "PartNumber",
                    models.AuditEvent.entity_id == part_number,
                )
                .order_by(models.AuditEvent.id)
            )
        )


def _stored(engine: Engine, part_number: str) -> sa.Row[Any] | None:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.PartNumber.__table__).where(
                models.PartNumber.part_number == part_number
            )
        ).first()


def _count(engine: Engine, table: sa.FromClause) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def _write_counts(engine: Engine) -> dict[str, int]:
    return {
        "part_numbers": _count(engine, models.PartNumber.__table__),
        "audit_events": _count(engine, models.AuditEvent.__table__),
    }


class _Cell:
    """A Department with one Area, an Operation and a Scan Station."""

    def __init__(self, client: TestClient) -> None:
        department = admin_of(client).post("/api/departments", json={"name": _unique("DEPT")})
        assert department.status_code == 201, department.text
        self.department_id = int(department.json()["id"])
        area = admin_of(client).post(
            "/api/areas", json={"department_id": self.department_id, "name": _unique("AREA")}
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


def _work_order(client: TestClient, part_number: str, quantity: int = 10) -> dict[str, Any]:
    response = admin_of(client).post(
        "/api/work-orders",
        json={"lines": [{"part_number": part_number, "requested_quantity": quantity}]},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _release(
    client: TestClient, cell: _Cell, work_order: dict[str, Any], part_number: str, quantity: int
) -> int:
    demand_id = int(work_order["demands"][0]["id"])
    response = admin_of(client).post(
        f"/api/work-orders/{work_order['id']}/demands/{demand_id}/release",
        json={
            "part_number": part_number,
            "quantity": quantity,
            "route_mode": "FLOATING",
            "starting_area_id": cell.area_id,
            "operation_id": cell.operation_id,
            "confirm_active_quantity": False,
            "device_event_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["quantity_flow_id"])


def _receive(client: TestClient, cell: _Cell, part_number: str) -> Any:
    return client.post(
        f"/api/scan-stations/{cell.station_id}/receipts",
        json={
            "part_number": part_number,
            "quantity": 3,
            "request_type": "MODIFY",
            "route_mode": "FLOATING",
            "scanned_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "device_event_id": str(uuid.uuid4()),
        },
    )


def _board_row(client: TestClient, cell: _Cell, part_number: str) -> dict[str, Any]:
    response = client.get("/api/production-board", params={"department_id": cell.department_id})
    assert response.status_code == 200, response.text
    found = [row for row in response.json()["rows"] if row["part_number"] == part_number]
    assert len(found) == 1
    return cast(dict[str, Any], found[0])


def _tracking_row(client: TestClient, part_number: str) -> dict[str, Any]:
    response = admin_of(client).get(
        "/api/tracking", params={"search": part_number, "status": "ALL"}
    )
    assert response.status_code == 200, response.text
    found = [row for row in response.json()["rows"] if row["part_number"] == part_number]
    assert len(found) == 1
    return cast(dict[str, Any], found[0])


def _tracking_detail(client: TestClient, part_number: str) -> dict[str, Any]:
    response = admin_of(client).get("/api/tracking/detail", params={"part_number": part_number})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


class _Call:
    """Run one request in a thread and collect its response or error."""

    def __init__(self, action: Callable[[], Any]) -> None:
        self._result: Any = None
        self._error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(action,))
        self.thread.start()

    def _run(self, action: Callable[[], Any]) -> None:
        try:
            self._result = action()
        except BaseException as exc:  # noqa: BLE001 — re-raised by finish()
            self._error = exc

    def finish(self, timeout: float = 30) -> Any:
        self.thread.join(timeout=timeout)
        assert not self.thread.is_alive(), "request never finished"
        if self._error is not None:
            raise self._error
        return self._result


class _Pause:
    """Test seam: wrap a callable so its FIRST call pauses after
    completing — while the caller holds whatever locks it took — until
    released; every later call passes straight through."""

    def __init__(self, real: Callable[..., Any]) -> None:
        self.real = real
        self.first_inside = threading.Event()
        self.let_first_finish = threading.Event()
        self._guard = threading.Lock()
        self._paused_once = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        result = self.real(*args, **kwargs)
        with self._guard:
            should_pause = not self._paused_once
            self._paused_once = True
        if should_pause:
            self.first_inside.set()
            assert self.let_first_finish.wait(timeout=20), "test deadlock: never released"
        return result


def _lock_waiters(engine: Engine, wait_event: str | None = None) -> int:
    query = (
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    if wait_event is not None:
        query += " AND wait_event = :wait_event"
    # As the owner: an application-role session sees no other session's wait.
    with owner_connection(engine.url) as connection:
        return int(connection.execute(sa.text(query), {"wait_event": wait_event}).scalar_one())


def _await_lock_waiters(
    engine: Engine, expected: int, wait_event: str | None = None, timeout: float = 10
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _lock_waiters(engine, wait_event) >= expected:
            return
        time.sleep(0.05)
    raise AssertionError(f"expected {expected} lock waiter(s) ({wait_event or 'any'})")


def _hold_part_number_lock(connection: sa.Connection, part_number: str) -> None:
    """Take the production PN advisory lock in ``connection``'s transaction."""
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"partflow:part-number:{part_number}"},
    )


# ---------------------------------------------------------------------------
# Edit (T-5, T-6, T-7)
# ---------------------------------------------------------------------------


def test_patch_changes_only_the_provided_details(client: TestClient, db_engine: Engine) -> None:
    pn = _unique()
    created = _create(client, pn, name="BRACKET", current_revision="A", erp_id="ERP-1")

    renamed = _patch(client, pn, {"name": "  BRACKET, MOUNTING  "})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert set(body) == _RESPONSE_KEYS
    assert (body["name"], body["current_revision"], body["erp_id"]) == (
        "BRACKET, MOUNTING",
        "A",
        "ERP-1",
    )
    assert body["updated_at"] > created["updated_at"]

    cleared = _patch(client, pn, {"current_revision": None, "erp_id": "   "})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["current_revision"], cleared.json()["erp_id"]) == (None, None)
    assert cleared.json()["name"] == "BRACKET, MOUNTING"

    events = _audit_rows(db_engine, pn)
    assert [event.event_type for event in events] == ["CREATED", "UPDATED", "UPDATED"]
    assert events[1].before_data == _snapshot(pn, "BRACKET", "A", "ERP-1")
    assert events[1].after_data == _snapshot(pn, "BRACKET, MOUNTING", "A", "ERP-1")
    assert events[2].before_data == _snapshot(pn, "BRACKET, MOUNTING", "A", "ERP-1")
    assert events[2].after_data == _snapshot(pn, "BRACKET, MOUNTING")
    assert all(
        event.actor_reference is None and event._mapping["metadata"] is None for event in events
    )

    # The same values again, an empty body, and a whitespace variant of
    # a stored value are no-ops: 200, nothing written.
    stored = _stored(db_engine, pn)
    for body_again in ({"name": "BRACKET, MOUNTING"}, {}, {"name": " BRACKET, MOUNTING "}):
        again = _patch(client, pn, body_again)
        assert again.status_code == 200, again.text
        assert again.json() == cleared.json()
    assert len(_audit_rows(db_engine, pn)) == 3
    assert _stored(db_engine, pn) == stored


def test_writes_on_a_pn_without_details_are_404_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    pn = _unique("ABSENT")
    counts = _write_counts(db_engine)
    for response in (
        _patch(client, pn, {"name": "X"}),
        admin_of(client).delete("/api/part-numbers", params={"number": pn.lower()}),
        _put_image(client, pn, _PNG),
        admin_of(client).delete("/api/part-numbers/image", params={"number": pn}),
        client.get("/api/part-numbers/image", params={"number": pn}),
    ):
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == _no_details(pn)
    assert _write_counts(db_engine) == counts

    for response in (
        _patch(client, "ABC 123", {"name": "X"}),
        admin_of(client).delete("/api/part-numbers", params={"number": "ABC 123"}),
        _put_image(client, "ABC\t123", _PNG),
        admin_of(client).delete("/api/part-numbers/image", params={"number": "ABC 123"}),
        client.get("/api/part-numbers/image", params={"number": "ABC 123"}),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Part Number must not contain internal whitespace."
    for response in (
        admin_of(client).patch("/api/part-numbers", json={"name": "X"}),
        admin_of(client).delete("/api/part-numbers"),
        admin_of(client).put(
            "/api/part-numbers/image", content=_PNG, headers={"Content-Type": "image/png"}
        ),
        admin_of(client).delete("/api/part-numbers/image"),
        client.get("/api/part-numbers/image"),
    ):
        assert response.status_code == 422, response.text
    assert _write_counts(db_engine) == counts


def test_nul_characters_are_refused_or_match_nothing_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    """PostgreSQL text cannot hold NUL (U+0000): a detail or a PN holding
    it is a typed 422 and a search holding it matches nothing — never a
    500 from the driver — and nothing is written."""
    pn = _unique("NUL")
    _create(client, pn, name="KEEP")
    stored = _stored(db_engine, pn)
    counts = _write_counts(db_engine)

    for field, label in (
        ("name", "Name / Description"),
        ("current_revision", "Revision"),
        ("erp_id", "ERP ID"),
    ):
        for response in (
            _patch(client, pn, {field: "A\x00B"}),
            admin_of(client).post(
                "/api/part-numbers", json={"part_number": _unique("NUL"), field: "x\x00"}
            ),
        ):
            assert response.status_code == 422, response.text
            assert response.json()["detail"] == f"{label} must be text."

    nul_pn = f"{pn}\x00"
    for response in (
        admin_of(client).post("/api/part-numbers", json={"part_number": nul_pn}),
        _patch(client, nul_pn, {"name": "X"}),
        admin_of(client).delete("/api/part-numbers", params={"number": nul_pn}),
        _put_image(client, nul_pn, _PNG),
        admin_of(client).delete("/api/part-numbers/image", params={"number": nul_pn}),
        client.get("/api/part-numbers/image", params={"number": nul_pn}),
        admin_of(client).get("/api/part-numbers", params={"number": nul_pn}),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "Part Number must not contain a NUL character."

    page = admin_of(client).get("/api/part-numbers/page", params={"search": "a\x00"})
    assert page.status_code == 200, page.text
    assert (page.json()["rows"], page.json()["total"], page.json()["has_more"]) == ([], 0, False)
    lookup = admin_of(client).get("/api/part-numbers", params={"search": "a\x00"})
    assert lookup.status_code == 200, lookup.text
    assert lookup.json() == []

    assert _stored(db_engine, pn) == stored
    assert _write_counts(db_engine) == counts


def test_patch_never_edits_the_pn_or_server_owned_fields(
    client: TestClient, db_engine: Engine
) -> None:
    pn = _unique()
    _create(client, pn)
    stored = _stored(db_engine, pn)
    for body in (
        {"part_number": _unique()},
        {"barcode_value": "PF:PN:FORGED"},
        {"image_updated_at": None},
        {"actor": "mallory"},
        {"name": 12},
    ):
        rejected = _patch(client, pn, body)
        assert rejected.status_code == 422, (body, rejected.text)
    assert _stored(db_engine, pn) == stored
    assert len(_audit_rows(db_engine, pn)) == 1


# ---------------------------------------------------------------------------
# URL-hostile PNs (T-8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", ["A/B", "50%", "Q?1", "#7", "X&Y=Z", "A+B"])
def test_url_hostile_pns_round_trip_through_every_route(client: TestClient, stem: str) -> None:
    pn = f"{stem}-{uuid.uuid4().hex[:8].upper()}"
    assert _create(client, pn)["part_number"] == pn

    found = admin_of(client).get("/api/part-numbers", params={"number": pn})
    assert [master["part_number"] for master in found.json()] == [pn]

    patched = _patch(client, pn, {"name": "Hostile"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["part_number"] == pn

    uploaded = _put_image(client, pn, _PNG)
    assert uploaded.status_code == 200, uploaded.text
    served = client.get(
        "/api/part-numbers/image",
        params={"number": pn, "v": uploaded.json()["image_updated_at"]},
    )
    assert served.status_code == 200
    assert served.content == _PNG
    assert (
        admin_of(client).delete("/api/part-numbers/image", params={"number": pn}).status_code == 200
    )

    page = admin_of(client).get("/api/part-numbers/page", params={"search": pn})
    assert [row["part_number"] for row in page.json()["rows"]] == [pn]

    deleted = admin_of(client).delete("/api/part-numbers", params={"number": pn})
    assert deleted.status_code == 204, deleted.text
    assert admin_of(client).get("/api/part-numbers", params={"number": pn}).json() == []


# ---------------------------------------------------------------------------
# Image (T-9, T-10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(_PNG, "image/png"), (_JPEG, "image/jpeg"), (_WEBP, "image/webp")],
)
def test_image_upload_is_served_back_with_cache_headers(
    client: TestClient, db_engine: Engine, data: bytes, content_type: str
) -> None:
    pn = _unique("IMG")
    _create(client, pn)
    response = _put_image(client, pn, data, content_type)
    assert response.status_code == 200, response.text
    assert set(response.json()) == _RESPONSE_KEYS
    assert response.json()["image_updated_at"] is not None

    served = client.get("/api/part-numbers/image", params={"number": pn, "v": "anything"})
    assert served.status_code == 200
    assert served.content == data
    assert served.headers["Content-Type"] == content_type
    assert re.fullmatch(r'"\d+"', served.headers["ETag"])
    assert served.headers["Cache-Control"] == "private, no-cache"
    assert served.headers["X-Content-Type-Options"] == "nosniff"

    cached = client.get(
        "/api/part-numbers/image",
        params={"number": pn},
        headers={"If-None-Match": served.headers["ETag"]},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["ETag"] == served.headers["ETag"]

    events = _audit_rows(db_engine, pn)
    assert [event.event_type for event in events] == ["CREATED", "UPDATED"]
    assert events[1].before_data == {"image": None}
    assert events[1].after_data == {"image": _digest(data, content_type)}


def test_image_replace_no_op_and_remove(client: TestClient, db_engine: Engine) -> None:
    pn = _unique("IMG")
    _create(client, pn, name="KEEP")
    first = _put_image(client, pn, _PNG).json()
    stored = _stored(db_engine, pn)

    again = _put_image(client, pn, _PNG)
    assert again.status_code == 200
    assert again.json() == first
    assert _stored(db_engine, pn) == stored
    assert len(_audit_rows(db_engine, pn)) == 2

    second = _put_image(client, pn, _WEBP, "image/webp").json()
    assert second["image_updated_at"] > first["image_updated_at"]
    replaced = _audit_rows(db_engine, pn)[-1]
    assert replaced.before_data == {"image": _digest(_PNG, "image/png")}
    assert replaced.after_data == {"image": _digest(_WEBP, "image/webp")}

    removed = admin_of(client).delete("/api/part-numbers/image", params={"number": pn})
    assert removed.status_code == 200, removed.text
    assert removed.json()["image_updated_at"] is None
    assert removed.json()["name"] == "KEEP"
    missing = client.get("/api/part-numbers/image", params={"number": pn})
    assert missing.status_code == 404
    assert missing.json()["detail"] == f"Part Number {pn} has no image."
    events = _audit_rows(db_engine, pn)
    assert events[-1].before_data == {"image": _digest(_WEBP, "image/webp")}
    assert events[-1].after_data == {"image": None}
    row = _stored(db_engine, pn)
    assert row is not None
    assert (row.image, row.image_type, row.image_updated_at) == (None, None, None)

    # Removing again is a no-op; no audit row carries bytes.
    assert (
        admin_of(client).delete("/api/part-numbers/image", params={"number": pn}).json()
        == removed.json()
    )
    assert len(_audit_rows(db_engine, pn)) == len(events)
    for event in events:
        for data in (event.before_data, event.after_data):
            assert "data" not in repr(data)


@pytest.mark.parametrize(
    ("content", "content_type", "status", "detail"),
    [
        pytest.param(
            _PNG + b"\x00" * (2 * 1024 * 1024 + 1 - len(_PNG)),
            "image/png",
            413,
            _TOO_LARGE,
            id="body-of-2097153-bytes",
        ),
        pytest.param(_GIF, "image/gif", 415, _UNSUPPORTED, id="gif"),
        pytest.param(_GIF, "image/png", 415, _UNSUPPORTED, id="gif-declared-as-png"),
        pytest.param(_PNG, "image/jpeg", 415, _UNSUPPORTED, id="png-declared-as-jpeg"),
        pytest.param(b"", "image/png", 422, "The image is empty.", id="empty-body"),
    ],
)
def test_image_refusals_store_nothing(
    client: TestClient,
    db_engine: Engine,
    content: bytes,
    content_type: str,
    status: int,
    detail: str,
) -> None:
    pn = _unique("IMG")
    _create(client, pn)
    stored = _stored(db_engine, pn)
    counts = _write_counts(db_engine)
    response = _put_image(client, pn, content, content_type)
    assert response.status_code == status, response.text
    assert response.json()["detail"] == detail
    assert _stored(db_engine, pn) == stored
    assert _write_counts(db_engine) == counts


def test_no_list_payload_or_list_query_carries_image_bytes(
    client: TestClient, db_engine: Engine
) -> None:
    pn = _unique("NOBYTES")
    _create(client, pn, name="NOBYTES NAME")
    assert _put_image(client, pn, _PNG).status_code == 200
    statements: list[str] = []

    def _record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    sa.event.listen(Engine, "before_cursor_execute", _record)
    try:
        responses = [
            admin_of(client).get("/api/part-numbers", params={"number": pn}),
            admin_of(client).get("/api/part-numbers", params={"search": "NOBYTES"}),
            admin_of(client).get("/api/part-numbers"),
            admin_of(client).get("/api/part-numbers/page", params={"search": pn}),
            admin_of(client).post("/api/part-numbers", json={"part_number": _unique("NOBYTES")}),
            _patch(client, pn, {"erp_id": "ERP-NB"}),
        ]
    finally:
        sa.event.remove(Engine, "before_cursor_execute", _record)

    for response in responses:
        assert response.status_code in {200, 201}, response.text
    for body in (responses[0].json(), responses[1].json(), responses[3].json()["rows"]):
        assert body
        for row in body:
            assert set(row) == _RESPONSE_KEYS
    assert set(responses[4].json()) == _RESPONSE_KEYS
    assert set(responses[5].json()) == _RESPONSE_KEYS
    assert statements
    assert not [statement for statement in statements if _IMAGE_COLUMN.search(statement)]


def test_image_bytes_are_never_loaded_by_default() -> None:
    assert sa.inspect(models.PartNumber).attrs["image"].deferred is True


# ---------------------------------------------------------------------------
# Hard delete (T-11, T-12)
# ---------------------------------------------------------------------------

_PRODUCTION_TABLES = (
    models.WorkOrder,
    models.WorkOrderDemand,
    models.QuantityFlow,
    models.PartMovement,
    models.WorkOrderAllocation,
    models.QuantityFlowLineage,
    models.AssignedRoute,
    models.AssignedRouteStep,
)


def _table_snapshot(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    snapshot: dict[str, list[tuple[Any, ...]]] = {}
    with engine.connect() as connection:
        for model in _PRODUCTION_TABLES:
            table = cast(sa.Table, model.__table__)
            snapshot[table.name] = [
                tuple(row)
                for row in connection.execute(sa.select(table).order_by(*table.primary_key.columns))
            ]
    return snapshot


def _all_audit_rows(engine: Engine) -> list[tuple[Any, ...]]:
    table = models.AuditEvent.__table__
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(sa.select(table).order_by(models.AuditEvent.id))
        ]


def test_delete_removes_only_the_details_and_the_pn_comes_back_on_first_use(
    client: TestClient, db_engine: Engine
) -> None:
    cell = _Cell(client)
    pn = _unique("DEL")
    work_order = _work_order(client, pn, quantity=10)
    _release(client, cell, work_order, pn, 4)
    assert _patch(client, pn, {"name": "WIDGET", "current_revision": "C"}).status_code == 200
    assert _put_image(client, pn, _JPEG, "image/jpeg").status_code == 200
    assert _board_row(client, cell, pn)["master"] == {"name": "WIDGET", "current_revision": "C"}
    detail_before = _tracking_detail(client, pn)
    assert detail_before["master"]["name"] == "WIDGET"

    production = _table_snapshot(db_engine)
    history = _all_audit_rows(db_engine)

    deleted = admin_of(client).delete("/api/part-numbers", params={"number": f" {pn.lower()} "})
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""

    assert _table_snapshot(db_engine) == production
    after = _all_audit_rows(db_engine)
    assert after[: len(history)] == history
    assert len(after) == len(history) + 1
    [event] = _audit_rows(db_engine, pn)[-1:]
    assert event.event_type == "DELETED"
    assert event.before_data == {
        **_snapshot(pn, name="WIDGET", current_revision="C"),
        "image": _digest(_JPEG, "image/jpeg"),
    }
    assert event.after_data is None
    assert _stored(db_engine, pn) is None

    assert admin_of(client).get("/api/part-numbers", params={"number": pn}).json() == []
    detail = _tracking_detail(client, pn)
    assert detail["master"] is None
    assert detail["demands"] == detail_before["demands"]
    assert detail["flows"] == detail_before["flows"]
    assert _tracking_row(client, pn)["has_master"] is False
    assert _board_row(client, cell, pn)["master"] is None

    again = admin_of(client).delete("/api/part-numbers", params={"number": pn})
    assert again.status_code == 404
    assert again.json()["detail"] == _no_details(pn)

    # Re-created explicitly with empty details...
    recreated = admin_of(client).post("/api/part-numbers", json={"part_number": pn})
    assert recreated.status_code == 201, recreated.text
    assert (recreated.json()["name"], recreated.json()["image_updated_at"]) == (None, None)
    assert admin_of(client).delete("/api/part-numbers", params={"number": pn}).status_code == 204

    # ...or on its next first use by a Work Order line.
    events_before = len(_audit_rows(db_engine, pn))
    _work_order(client, pn, quantity=2)
    events = _audit_rows(db_engine, pn)
    assert len(events) == events_before + 1
    assert events[-1].event_type == "CREATED"
    assert events[-1].after_data == _snapshot(pn)


# ---------------------------------------------------------------------------
# Management page and the Phase 4 name search (T-13)
# ---------------------------------------------------------------------------


def test_page_searches_every_detail_and_pages(client: TestClient) -> None:
    prefix = f"PAGE{uuid.uuid4().hex[:6].upper()}"
    marker = uuid.uuid4().hex[:8]
    seeded = [f"{prefix}-{index:02d}" for index in range(5)]
    for pn in reversed(seeded):
        _create(client, pn)
    by_name = _create(client, _unique("ZNAME"), name=f"Gear {marker} housing")["part_number"]
    by_revision = _create(client, _unique("ZREV"), current_revision=f"R{marker}")["part_number"]
    by_erp = _create(client, _unique("ZERP"), erp_id=f"ERP-{marker.upper()}")["part_number"]

    first = admin_of(client).get(
        "/api/part-numbers/page", params={"search": prefix.lower(), "limit": 2}
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"rows", "total", "offset", "limit", "has_more"}
    assert [row["part_number"] for row in body["rows"]] == seeded[:2]
    assert (body["total"], body["offset"], body["limit"], body["has_more"]) == (5, 0, 2, True)

    last = (
        admin_of(client)
        .get("/api/part-numbers/page", params={"search": prefix, "offset": 4, "limit": 2})
        .json()
    )
    assert [row["part_number"] for row in last["rows"]] == seeded[4:]
    assert (last["total"], last["has_more"]) == (5, False)

    matched = (
        admin_of(client).get("/api/part-numbers/page", params={"search": marker.upper()}).json()
    )
    assert [row["part_number"] for row in matched["rows"]] == sorted([by_name, by_revision, by_erp])
    assert matched["total"] == 3

    default = admin_of(client).get("/api/part-numbers/page").json()
    assert default["limit"] == part_numbers.DEFAULT_PAGE_LIMIT
    assert default["offset"] == 0
    assert [row["part_number"] for row in default["rows"]] == sorted(
        row["part_number"] for row in default["rows"]
    )

    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert admin_of(client).get("/api/part-numbers/page", params=params).status_code == 422


def test_search_wildcards_are_literal(client: TestClient) -> None:
    marker = uuid.uuid4().hex[:6].upper()
    literal = _create(client, f"W{marker}%_\\X")["part_number"]
    _create(client, f"W{marker}AB\\X")
    for term in (f"{marker}%", f"{marker}%_", "%_\\"):
        page = admin_of(client).get("/api/part-numbers/page", params={"search": term}).json()
        assert literal in [row["part_number"] for row in page["rows"]]
        assert all("%" in row["part_number"] for row in page["rows"])
        lookup = admin_of(client).get("/api/part-numbers", params={"search": term}).json()
        assert all("%" in row["part_number"] for row in lookup)


def test_add_part_search_matches_the_pn_or_the_name_only(client: TestClient) -> None:
    marker = uuid.uuid4().hex[:8].upper()
    by_pn = _create(client, f"LOOK-{marker}")["part_number"]
    by_name = _create(client, _unique("LOOKN"), name=f"Shaft {marker.lower()} long")["part_number"]
    _create(client, _unique("LOOKR"), current_revision=marker)
    _create(client, _unique("LOOKE"), erp_id=marker)

    found = admin_of(client).get("/api/part-numbers", params={"search": marker.lower()})
    assert found.status_code == 200
    assert [row["part_number"] for row in found.json()] == sorted([by_pn, by_name])
    named = [row for row in found.json() if row["part_number"] == by_name]
    assert named[0]["name"] == f"Shaft {marker.lower()} long"


# ---------------------------------------------------------------------------
# Locks (T-4b, T-14, T-15)
# ---------------------------------------------------------------------------


def test_create_waits_on_production_creators_and_production_reuses_it(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paused management create holds the PN advisory lock: a Work
    Order save and a Scan Station receipt of the same PN wait on it,
    then reuse the master it committed — no 409 on a production
    surface, one master row and one CREATED event (the create's)."""
    cell = _Cell(client)

    def _save(pn: str) -> Any:
        return admin_of(client).post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 3}]}
        )

    def _receipt(pn: str) -> Any:
        return _receive(client, cell, pn)

    productions: list[tuple[str, Callable[[str], Any]]] = [
        ("work order save", _save),
        ("scan station receipt", _receipt),
    ]
    for name, production in productions:
        pn = _unique("RACE")
        pause = _Pause(common.flush)
        monkeypatch.setattr(part_numbers, "flush", pause)
        create = _Call(
            functools.partial(
                admin_of(client).post,
                "/api/part-numbers",
                json={"part_number": pn, "name": "FROM MANAGEMENT"},
            )
        )
        try:
            assert pause.first_inside.wait(timeout=10), name
            command_call = _Call(functools.partial(production, pn))
            _await_lock_waiters(db_engine, 1, wait_event="advisory")
            assert command_call.thread.is_alive(), name
        finally:
            pause.let_first_finish.set()
        assert create.finish().status_code == 201, name
        response = command_call.finish()
        assert response.status_code == 201, (name, response.text)
        monkeypatch.undo()

        rows = admin_of(client).get("/api/part-numbers", params={"number": pn}).json()
        assert [row["name"] for row in rows] == ["FROM MANAGEMENT"], name
        events = _audit_rows(db_engine, pn)
        created = [event.after_data for event in events if event.event_type == "CREATED"]
        assert created == [_snapshot(pn, name="FROM MANAGEMENT")], name


def test_create_after_a_production_creator_answers_already_saved(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror: a Work Order save paused after creating the master
    (under the PN lock) makes the management create wait, which then
    answers the conflict — the production save commits unharmed."""
    pn = _unique("RACE")
    pause = _Pause(common.flush)
    monkeypatch.setattr(part_numbers, "flush", pause)
    save = _Call(
        lambda: admin_of(client).post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 3}]}
        )
    )
    try:
        assert pause.first_inside.wait(timeout=10)
        create = _Call(lambda: admin_of(client).post("/api/part-numbers", json={"part_number": pn}))
        _await_lock_waiters(db_engine, 1, wait_event="advisory")
        assert create.thread.is_alive()
    finally:
        pause.let_first_finish.set()
    assert save.finish().status_code == 201
    refused = create.finish()
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == _already_saved(pn)
    assert [event.event_type for event in _audit_rows(db_engine, pn)] == ["CREATED"]


def test_edit_image_and_delete_never_wait_on_the_production_lock(
    client: TestClient, db_engine: Engine
) -> None:
    pn = _unique("FREE")
    _create(client, pn)
    with db_engine.connect() as holder:
        _hold_part_number_lock(holder, pn)
        try:
            for action in (
                lambda: _patch(client, pn, {"name": "NO WAIT"}),
                lambda: _put_image(client, pn, _PNG),
                lambda: admin_of(client).delete("/api/part-numbers/image", params={"number": pn}),
                lambda: admin_of(client).delete("/api/part-numbers", params={"number": pn}),
            ):
                response = _Call(action).finish(timeout=5)
                assert response.status_code in {200, 204}, response.text
        finally:
            holder.rollback()

    # The create, by contrast, serializes with the production creators.
    new_pn = _unique("WAIT")
    with db_engine.connect() as holder:
        _hold_part_number_lock(holder, new_pn)
        try:
            create = _Call(
                lambda: admin_of(client).post("/api/part-numbers", json={"part_number": new_pn})
            )
            _await_lock_waiters(db_engine, 1, wait_event="advisory")
            assert create.thread.is_alive()
        finally:
            holder.commit()
        assert create.finish().status_code == 201


@pytest.mark.parametrize("write", ["patch", "image"])
def test_a_write_that_loses_to_a_delete_answers_404(
    client: TestClient, db_engine: Engine, write: str
) -> None:
    pn = _unique("LOST")
    _create(client, pn)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM part_numbers WHERE part_number = :pn FOR UPDATE"), {"pn": pn}
        )
        try:
            if write == "patch":
                call = _Call(lambda: _patch(client, pn, {"name": "TOO LATE"}))
            else:
                call = _Call(lambda: _put_image(client, pn, _PNG))
            _await_lock_waiters(db_engine, 1)
            assert call.thread.is_alive()
            holder.execute(sa.text("DELETE FROM part_numbers WHERE part_number = :pn"), {"pn": pn})
        finally:
            holder.commit()
    response = call.finish()
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == _no_details(pn)
    assert [event.event_type for event in _audit_rows(db_engine, pn)] == ["CREATED"]


@pytest.mark.parametrize("write", ["create", "patch", "image", "remove image"])
def test_a_committed_write_answers_success_when_a_delete_commits_right_after(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch, write: str
) -> None:
    """A delete of the same PN that commits between a write's commit and
    the route's answer never turns the committed write into a 500: the
    answer reports exactly what the write committed."""
    pn = _unique("AFTER")
    if write != "create":
        _create(client, pn)
    if write == "remove image":
        assert _put_image(client, pn, _PNG).status_code == 200
    actions: dict[str, Callable[[], Any]] = {
        "create": lambda: admin_of(client).post(
            "/api/part-numbers", json={"part_number": pn, "name": "COMMITTED"}
        ),
        "patch": lambda: _patch(client, pn, {"name": "COMMITTED"}),
        "image": lambda: _put_image(client, pn, _PNG),
        "remove image": lambda: admin_of(client).delete(
            "/api/part-numbers/image", params={"number": pn}
        ),
    }
    pause = _Pause(common.commit)
    monkeypatch.setattr(part_numbers, "commit", pause)
    call = _Call(actions[write])
    try:
        assert pause.first_inside.wait(timeout=10)
        deleted = admin_of(client).delete("/api/part-numbers", params={"number": pn})
        assert deleted.status_code == 204, deleted.text
    finally:
        pause.let_first_finish.set()
    response = call.finish()
    monkeypatch.undo()

    assert response.status_code == (201 if write == "create" else 200), response.text
    body = response.json()
    assert set(body) == _RESPONSE_KEYS
    assert body["part_number"] == pn
    if write in {"create", "patch"}:
        assert body["name"] == "COMMITTED"
    assert (body["image_updated_at"] is not None) == (write == "image")
    assert _stored(db_engine, pn) is None
    events = [event.event_type for event in _audit_rows(db_engine, pn)]
    assert events[-2:] == ["CREATED" if write == "create" else "UPDATED", "DELETED"]


# ---------------------------------------------------------------------------
# Atomicity (T-16)
# ---------------------------------------------------------------------------


def test_a_failed_audit_write_leaves_the_master_unchanged(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    pn = _unique("ATOMIC")
    _create(client, pn, name="BEFORE")
    stored = _stored(db_engine, pn)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    actions: list[Callable[[], Any]] = [
        lambda: _patch(client, pn, {"name": "AFTER"}),
        lambda: _put_image(client, pn, _PNG),
        lambda: admin_of(client).delete("/api/part-numbers", params={"number": pn}),
    ]
    for action in actions:
        with pytest.raises(RuntimeError, match="audit persistence failed"):
            action()
        assert _stored(db_engine, pn) == stored
    monkeypatch.undo()
    assert len(_audit_rows(db_engine, pn)) == 1


# ---------------------------------------------------------------------------
# Read models (T-17)
# ---------------------------------------------------------------------------


def test_read_models_carry_the_saved_details(client: TestClient) -> None:
    cell = _Cell(client)
    with_details = _unique("RM-A")
    without_master = _unique("RM-B")
    name_only = _unique("RM-C")
    for pn in (with_details, without_master, name_only):
        work_order = _work_order(client, pn, quantity=5)
        _release(client, cell, work_order, pn, 2)

    def _board() -> dict[str, dict[str, Any]]:
        response = client.get("/api/production-board", params={"department_id": cell.department_id})
        assert response.status_code == 200, response.text
        return {row["part_number"]: row for row in response.json()["rows"]}

    order_before = list(_board())
    assert (
        admin_of(client).delete("/api/part-numbers", params={"number": without_master}).status_code
        == 204
    )
    _patch(client, with_details, {"name": "PLATE", "current_revision": "B", "erp_id": "ERP-9"})
    uploaded = _put_image(client, with_details, _PNG).json()
    _patch(client, name_only, {"name": "ONLY NAME"})

    rows = _board()
    # The master never takes part in the canonical board order.
    assert list(rows) == order_before
    assert rows[with_details]["master"] == {"name": "PLATE", "current_revision": "B"}
    assert rows[name_only]["master"] == {"name": "ONLY NAME", "current_revision": None}
    assert rows[without_master]["master"] is None

    assert _tracking_row(client, with_details)["name"] == "PLATE"
    assert _tracking_row(client, name_only)["name"] == "ONLY NAME"
    assert _tracking_row(client, without_master)["name"] is None

    master = _tracking_detail(client, with_details)["master"]
    assert set(master) == {
        "part_number",
        "created_at",
        "updated_at",
        "name",
        "current_revision",
        "erp_id",
        "image_updated_at",
    }
    assert (master["name"], master["current_revision"], master["erp_id"]) == (
        "PLATE",
        "B",
        "ERP-9",
    )
    assert master["image_updated_at"] == uploaded["image_updated_at"]
    assert _tracking_detail(client, without_master)["master"] is None
