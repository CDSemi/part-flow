"""Integration tests for Phase 13 slice 6 — the Undo reason policy.

Exercises the full request path — FastAPI routes, the Application Undo
command and its preview, the policies service and PostgreSQL — against a
dedicated temporary database migrated to head by the real Alembic chain
(IMPLEMENTATION_ROADMAP Phase 13; PROJECT_PROFILE §16 "require a reason
when configured"; PLAN CD3, CD10; owner default OD-6):

- Administration → Correction permissions: ``GET`` / ``PUT
  /api/policies/correction-permissions`` (default off, strict boolean,
  audited as ``ApplicationPolicy`` / ``correction-permissions``, a no-op
  writes nothing, the Worker sessions section untouched);
- the Undo accepts an optional reason, stripped (blank = absent), stored
  on every ``REVERSED`` row of the command (one-, two- and four-row
  commands) and returned (also on a replay); while the policy is on, an
  Undo without one is 409 ``undo_reason_required`` with zero writes and
  every lock released;
- idempotency: the reason joins the fingerprint only when present (a
  reason-less Undo keeps the pre-slice-6 fingerprint), a committed Undo
  replays whatever the policy says now, a different reason under one id
  is the explicit conflict; the NUL refusal precedes the fast path;
- refusal precedence: state refusals first, then the reason, then the
  slice 5 / slice 4 identity refusals — no gate sign-in is staged for a
  reason refusal;
- the preview reports ``reason_required``; Tracking lists the reason;
- concurrency: commands never lock the policy, the policy is judged
  under the command's locks, the identity resolver's own policy reads
  still see a change committed while they waited, and the two policy
  sections serialize on the row lock without overwriting each other;
- static guards on ``undo.py``.

The API commits real transactions, so tests isolate through unique
PNs/Areas/stations/Workers; both policy sections are restored after
every test and the module database is dropped afterwards. Helpers are
copied from the slice 5 and Phase 9 modules (each module owns its own).
"""

import ast
import hashlib
import json
import os
import threading
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
from app.application import undo
from app.application.errors import InvalidInputError
from app.application.machine_processing import FINGERPRINT_KEY
from app.core.config import get_settings
from app.main import create_app
from tests.auth_harness import admin_of

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_UNDO_MODULE = _BACKEND_DIR / "app" / "application" / "undo.py"
_TEST_DATABASE = "partflow_test_undo_reason_policy_api"
_DB_URL_ENV = "DATABASE_URL"
_POLICY_PATH = "/api/policies/correction-permissions"
_SESSIONS_POLICY_PATH = "/api/policies/worker-sessions"

_E_R1 = (
    "A reason is required to reverse this action. Enter the reason and confirm again."
    " Nothing was reversed."
)
_E_R2 = "The Undo reason must be plain text."
_E_G1 = (
    "This action is now confirmed by a Worker badge scan. Scan your badge to confirm."
    " Nothing was recorded."
)
_CONFLICT = (
    "This device_event_id was already used for a different production request."
    " Nothing was recorded — a new intent needs a new device_event_id."
)
_SESSION_DEFAULTS = {
    "worker_session_timeout_minutes": 15,
    "badge_confirm_done": True,
    "badge_confirm_queue": True,
    "badge_confirm_undo": True,
}


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
    response = admin_of(client).put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "BC-", "digits": 4},
    )
    assert response.status_code == 200, response.text


@pytest.fixture(autouse=True)
def approved_policy(client: TestClient) -> Iterator[None]:
    """Every test leaves the approved defaults: no reason required, slice 4 / 5 defaults."""
    yield
    _ok(admin_of(client).put(_POLICY_PATH, json={"undo_reason_required": False}))
    _ok(admin_of(client).put(_SESSIONS_POLICY_PATH, json=_SESSION_DEFAULTS))


# ---------------------------------------------------------------------------
# Seeding helpers (copied from the slice 5 / Phase 9 modules)
# ---------------------------------------------------------------------------


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _ok(response: Any, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return cast(dict[str, Any], response.json())


class _Cell:
    """An Area with one Operation, one Scan Station and optional Machines."""

    def __init__(self, client: TestClient, *, machine_count: int = 0) -> None:
        department = _ok(
            admin_of(client).post("/api/departments", json={"name": _unique("DEPT")}), 201
        )
        self.area = _ok(
            admin_of(client).post(
                "/api/areas", json={"department_id": department["id"], "name": _unique("AREA")}
            ),
            201,
        )
        self.area_id = int(self.area["id"])
        operation = _ok(
            admin_of(client).post(
                "/api/operations", json={"area_id": self.area_id, "code": _unique("OP")}
            ),
            201,
        )
        self.operation_id = int(operation["id"])
        station = _ok(
            admin_of(client).post(
                "/api/scan-stations",
                json={"station_id": _unique("ST"), "area_id": self.area_id},
            ),
            201,
        )
        self.station_id = str(station["station_id"])
        self.machine_ids = [
            int(
                _ok(
                    admin_of(client).post(
                        "/api/machines", json={"area_id": self.area_id, "name": _unique("Lathe")}
                    ),
                    201,
                )["id"]
            )
            for _ in range(machine_count)
        ]

    @property
    def machine_id(self) -> int:
        return self.machine_ids[0]


def _worker(client: TestClient) -> dict[str, Any]:
    return _ok(
        admin_of(client).post(
            "/api/workers", json={"name": _unique("Worker"), "badge_barcode": _unique("BADGE")}
        ),
        201,
    )


def _release(client: TestClient, cell: _Cell, *, quantity: int = 10) -> tuple[int, str]:
    """Management releases ``quantity`` of a new PN into ``cell`` (no station)."""
    pn = _unique("PN")
    work_order = _ok(
        admin_of(client).post(
            "/api/work-orders", json={"lines": [{"part_number": pn, "requested_quantity": 500}]}
        ),
        201,
    )
    released = _ok(
        admin_of(client).post(
            f"/api/work-orders/{work_order['id']}/demands/{work_order['demands'][0]['id']}/release",
            json={
                "part_number": pn,
                "quantity": quantity,
                "route_mode": "FLOATING",
                "starting_area_id": cell.area_id,
                "operation_id": cell.operation_id,
                "confirm_active_quantity": False,
                "device_event_id": str(uuid.uuid4()),
            },
        ),
        201,
    )
    return int(released["quantity_flow_id"]), pn


def _event() -> str:
    return str(uuid.uuid4())


def _in_area(
    client: TestClient, cell: _Cell, route: str, flow_id: int, pn: str, quantity: int
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/scan-stations/{cell.station_id}/{route}",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "quantity": quantity,
                "machine_id": cell.machine_id,
                "device_event_id": _event(),
            },
        ),
        201,
    )


def _transfer(
    client: TestClient, source: _Cell, target: _Cell, flow_id: int, pn: str, quantity: int = 10
) -> dict[str, Any]:
    return _ok(
        client.post(
            f"/api/scan-stations/{target.station_id}/transfers",
            json={
                "part_number": pn,
                "quantity_flow_id": flow_id,
                "source_area_id": source.area_id,
                "target_area_id": target.area_id,
                "quantity": quantity,
                "device_event_id": _event(),
            },
        ),
        201,
    )


class _Command(NamedTuple):
    """A committed station command to undo at ``cell``."""

    cell: _Cell
    part_number: str
    device_event_id: str
    size: int  # Movements the command recorded (= REVERSED rows of its Undo)
    flow_ids: list[int]  # every involved flow
    machine_ids: list[int]  # every Machine the reversal touches


def _plain_transfer(client: TestClient) -> _Command:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    transfer = _transfer(client, source, target, flow_id, pn)
    return _Command(target, pn, str(transfer["device_event_id"]), 1, [flow_id], [])


def _implicit_completion_transfer(client: TestClient) -> _Command:
    source, target = _Cell(client, machine_count=1), _Cell(client, machine_count=1)
    flow_id, pn = _release(client, source)
    _in_area(client, source, "machine-assignments", flow_id, pn, 10)
    transfer = _transfer(client, source, target, flow_id, pn)
    assert transfer["completed_movement_id"] is not None
    return _Command(target, pn, str(transfer["device_event_id"]), 2, [flow_id], [source.machine_id])


def _partial_assignment(client: TestClient) -> _Command:
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    partial = _in_area(client, cell, "machine-assignments", flow_id, pn, 4)
    flows = sorted(
        {
            flow_id,
            int(partial["quantity_flow_id"]),
            int(partial["remainder_quantity_flow_id"]),
        }
    )
    return _Command(cell, pn, str(partial["device_event_id"]), 4, flows, [cell.machine_id])


_COMMANDS: dict[str, Callable[[TestClient], _Command]] = {
    "transfer": _plain_transfer,
    "implicit_completion": _implicit_completion_transfer,
    "partial_split": _partial_assignment,
}


def _undo_body(command_: _Command, **fields: Any) -> dict[str, Any]:
    return {
        "part_number": command_.part_number,
        "reverses_device_event_id": command_.device_event_id,
        "device_event_id": _event(),
        **fields,
    }


def _post_undo(client: TestClient, command_: _Command, body: dict[str, Any]) -> Any:
    return client.post(f"/api/scan-stations/{command_.cell.station_id}/undos", json=body)


def _preview(client: TestClient, command_: _Command) -> dict[str, Any]:
    return _ok(
        client.get(
            f"/api/scan-stations/{command_.cell.station_id}/undo-preview/{command_.device_event_id}"
        )
    )


def _set_required(client: TestClient, required: bool) -> dict[str, Any]:
    return _ok(admin_of(client).put(_POLICY_PATH, json={"undo_reason_required": required}))


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------


def _reversal_rows(engine: Engine, device_event_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT id, movement_type, reason, worker_id, scan_session_id, metadata"
                    " FROM part_movements WHERE device_event_id = :event"
                    " ORDER BY command_sequence"
                ),
                {"event": device_event_id},
            )
        )


def _reasons(engine: Engine, device_event_id: str) -> list[str | None]:
    rows = _reversal_rows(engine, device_event_id)
    assert rows and all(row.movement_type == "REVERSED" for row in rows)
    return [row.reason for row in rows]


_COUNTED_TABLES = (
    "part_movements",
    "quantity_flows",
    "audit_events",
    "worker_sessions",
)


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
            for table in _COUNTED_TABLES
        }


def _state(engine: Engine, command_: _Command) -> tuple[Any, ...]:
    """Every row a reversal of ``command_`` could change, verbatim."""
    with engine.connect() as connection:
        flows = list(
            connection.execute(
                sa.text("SELECT * FROM quantity_flows WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": command_.flow_ids},
            )
        )
        machines = list(
            connection.execute(
                sa.text("SELECT * FROM machines WHERE id = ANY(:ids) ORDER BY id"),
                {"ids": command_.machine_ids},
            )
        )
        sessions = list(connection.execute(sa.text("SELECT * FROM worker_sessions ORDER BY id")))
    return (
        [tuple(row) for row in flows],
        [tuple(row) for row in machines],
        [tuple(row) for row in sessions],
        _row_counts(engine),
    )


def _policy_audits(engine: Engine, entity_id: str) -> list[Any]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.text(
                    "SELECT event_type, entity_id, before_data, after_data, actor_reference"
                    " FROM audit_events WHERE entity_type = 'ApplicationPolicy'"
                    " AND entity_id = :entity_id ORDER BY id"
                ),
                {"entity_id": entity_id},
            )
        )


def _assert_reason_required(response: Any) -> None:
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": _E_R1, "undo_reason_required": True}


def _start(request: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(request()))
    thread.start()
    return thread, results


def _assert_blocked(thread: threading.Thread) -> None:
    thread.join(timeout=0.5)
    assert thread.is_alive()


def _finish(thread: threading.Thread, results: list[Any]) -> Any:
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(results) == 1
    return results[0]


def _pre_slice6_fingerprint(station_id: str, part_number: str, reverses: str) -> str:
    """The fingerprint of an Undo as every pre-slice-6 version computed it."""
    normalized = {
        "command": "UNDO",
        "station_id": station_id,
        "part_number": part_number,
        "reverses_device_event_id": reverses,
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# U-1 — the policy section
# ---------------------------------------------------------------------------


def test_policy_section_is_a_strict_audited_switch(client: TestClient, db_engine: Engine) -> None:
    sessions_before = _ok(admin_of(client).get(_SESSIONS_POLICY_PATH))
    initial = _ok(admin_of(client).get(_POLICY_PATH))
    assert set(initial) == {"undo_reason_required", "updated_at"}
    assert initial["undo_reason_required"] is False
    before = len(_policy_audits(db_engine, "correction-permissions"))

    stored = _set_required(client, True)
    assert stored["undo_reason_required"] is True
    rows = _policy_audits(db_engine, "correction-permissions")
    assert len(rows) == before + 1
    assert tuple(rows[-1]) == (
        "UPDATED",
        "correction-permissions",
        {"undo_reason_required": False},
        {"undo_reason_required": True},
        None,
    )
    # The same value again writes nothing.
    assert _set_required(client, True)["undo_reason_required"] is True
    assert len(_policy_audits(db_engine, "correction-permissions")) == before + 1
    _set_required(client, False)
    rows = _policy_audits(db_engine, "correction-permissions")
    assert len(rows) == before + 2
    assert (rows[-1].before_data, rows[-1].after_data) == (
        {"undo_reason_required": True},
        {"undo_reason_required": False},
    )

    counts = _row_counts(db_engine)
    for body in (
        {},
        {"undo_reason_required": "true"},
        {"undo_reason_required": 1},
        {"undo_reason_required": None},
        {"undo_reason_required": True, "extra": 1},
    ):
        assert admin_of(client).put(_POLICY_PATH, json=body).status_code == 422, body
    assert _ok(admin_of(client).get(_POLICY_PATH))["undo_reason_required"] is False
    assert _row_counts(db_engine) == counts
    # The Worker sessions section is untouched by every correction-permissions PUT.
    sessions_after = _ok(admin_of(client).get(_SESSIONS_POLICY_PATH))
    assert {key: sessions_after[key] for key in _SESSION_DEFAULTS} == {
        key: sessions_before[key] for key in _SESSION_DEFAULTS
    }


# ---------------------------------------------------------------------------
# U-2 / U-3 — the reason while the policy is off and on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", list(_COMMANDS.values()), ids=list(_COMMANDS))
def test_policy_off_stores_an_optional_reason_on_every_reversed_row(
    client: TestClient, db_engine: Engine, factory: Callable[[TestClient], _Command]
) -> None:
    for sent, stored in ((None, None), ("  wrong PN  ", "wrong PN"), ("   ", None)):
        command_ = factory(client)
        fields = {} if sent is None else {"reason": sent}
        with db_engine.connect() as connection:
            originals = list(
                connection.execute(
                    sa.text("SELECT * FROM part_movements WHERE device_event_id = :event"),
                    {"event": command_.device_event_id},
                )
            )
        body = _undo_body(command_, **fields)
        result = _ok(_post_undo(client, command_, body), 201)
        assert result["reason"] == stored
        assert _reasons(db_engine, body["device_event_id"]) == [stored] * command_.size
        with db_engine.connect() as connection:
            after = list(
                connection.execute(
                    sa.text("SELECT * FROM part_movements WHERE device_event_id = :event"),
                    {"event": command_.device_event_id},
                )
            )
        assert after == originals


def _assert_command_locks_released(engine: Engine, command_: _Command) -> None:
    """Probe from an independent connection: advisory locks are re-entrant
    for the backend holding them, so the API's own pool cannot tell."""
    with engine.connect() as observer:
        try:
            granted = observer.execute(
                sa.text(
                    "SELECT pg_try_advisory_xact_lock("
                    "hashtextextended('partflow:part-number:' || :pn, 0))"
                ),
                {"pn": command_.part_number},
            ).scalar_one()
            assert granted is True
            observer.execute(
                sa.text("SELECT 1 FROM quantity_flows WHERE id = ANY(:ids) FOR UPDATE NOWAIT"),
                {"ids": command_.flow_ids},
            )
            observer.execute(
                sa.text(
                    "SELECT 1 FROM scan_stations WHERE station_id = :station FOR UPDATE NOWAIT"
                ),
                {"station": command_.cell.station_id},
            )
        finally:
            observer.rollback()


@pytest.mark.parametrize("factory", list(_COMMANDS.values()), ids=list(_COMMANDS))
def test_policy_on_refuses_an_undo_without_a_reason_with_zero_writes(
    client: TestClient, db_engine: Engine, factory: Callable[[TestClient], _Command]
) -> None:
    command_ = factory(client)
    _set_required(client, True)
    before = _state(db_engine, command_)
    retried = _undo_body(command_)
    for fields in ({}, {"reason": None}, {"reason": ""}, {"reason": "   "}):
        _assert_reason_required(_post_undo(client, command_, {**retried, **fields}))
        assert _state(db_engine, command_) == before
    _assert_command_locks_released(db_engine, command_)

    # The same device_event_id, now with a reason, is a fresh attempt.
    result = _ok(_post_undo(client, command_, {**retried, "reason": "wrong PN"}), 201)
    assert result["reason"] == "wrong PN"
    assert _reasons(db_engine, retried["device_event_id"]) == ["wrong PN"] * command_.size


# ---------------------------------------------------------------------------
# U-4 / U-5 / U-6 — idempotency and the fingerprint
# ---------------------------------------------------------------------------


def test_a_committed_undo_replays_across_a_policy_flip(
    client: TestClient, db_engine: Engine
) -> None:
    # (a) Off, no reason → On → identical resend replays.
    command_ = _implicit_completion_transfer(client)
    body = _undo_body(command_)
    created = _ok(_post_undo(client, command_, body), 201)
    _set_required(client, True)
    before = _row_counts(db_engine)
    replayed = _ok(_post_undo(client, command_, body), 200)
    assert replayed == created
    assert replayed["reason"] is None
    assert _row_counts(db_engine) == before
    # (c) The reason-less Undo stored exactly the pre-slice-6 fingerprint.
    fingerprints = {
        row.metadata[FINGERPRINT_KEY] for row in _reversal_rows(db_engine, body["device_event_id"])
    }
    assert fingerprints == {
        _pre_slice6_fingerprint(
            command_.cell.station_id, command_.part_number, command_.device_event_id
        )
    }

    # (b) On, with a reason → Off → identical resend replays the recorded reason.
    command_ = _plain_transfer(client)
    body = _undo_body(command_, reason="wrong PN")
    created = _ok(_post_undo(client, command_, body), 201)
    _set_required(client, False)
    replayed = _ok(_post_undo(client, command_, body), 200)
    assert replayed == created
    assert replayed["reason"] == "wrong PN"


def test_the_reason_is_part_of_the_frozen_intent(client: TestClient, db_engine: Engine) -> None:
    command_ = _plain_transfer(client)
    body = _undo_body(command_, reason="a")
    _ok(_post_undo(client, command_, body), 201)
    before = _row_counts(db_engine)
    for changed in ({**body, "reason": "b"}, {key: body[key] for key in body if key != "reason"}):
        response = _post_undo(client, command_, changed)
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": _CONFLICT}
    # Normalized: the same reason with other padding is the same intent.
    assert _ok(_post_undo(client, command_, {**body, "reason": "  a "}), 200)["reason"] == "a"
    assert _row_counts(db_engine) == before

    command_ = _plain_transfer(client)
    body = _undo_body(command_)
    _ok(_post_undo(client, command_, body), 201)
    response = _post_undo(client, command_, {**body, "reason": "a"})
    assert (response.status_code, response.json()) == (409, {"detail": _CONFLICT})


def test_the_reasonless_fingerprint_is_the_pre_slice6_one() -> None:
    fields = {
        "station_id": "ST-1",
        "part_number": "PN-1",
        "reverses_device_event_id": "00000000-0000-0000-0000-000000000001",
    }
    expected = _pre_slice6_fingerprint(
        fields["station_id"], fields["part_number"], fields["reverses_device_event_id"]
    )
    assert undo._request_fingerprint(**fields, reason=None) == expected
    with_reason = undo._request_fingerprint(**fields, reason="wrong PN")
    assert with_reason != expected
    canonical = json.dumps(
        {"command": "UNDO", **fields, "reason": "wrong PN"}, sort_keys=True, separators=(",", ":")
    )
    assert '"reason"' in canonical
    assert with_reason == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# U-7 / U-8 — refusal precedence
# ---------------------------------------------------------------------------


def _assert_plain_conflict(response: Any, fragment: str) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert set(body) == {"detail"}
    assert fragment in body["detail"]


def test_state_refusals_precede_the_reason(client: TestClient, db_engine: Engine) -> None:
    # Already reversed.
    command_ = _plain_transfer(client)
    _ok(_post_undo(client, command_, _undo_body(command_)), 201)
    _set_required(client, True)
    _assert_plain_conflict(
        _post_undo(client, command_, _undo_body(command_)), "already been reversed"
    )

    # Restoring onto a retired Machine.
    cell = _Cell(client, machine_count=1)
    flow_id, pn = _release(client, cell)
    _in_area(client, cell, "machine-assignments", flow_id, pn, 10)
    done = _in_area(client, cell, "area-completions", flow_id, pn, 10)
    _ok(admin_of(client).post(f"/api/machines/{cell.machine_id}/retire", json={}))
    retired = _Command(cell, pn, str(done["device_event_id"]), 1, [flow_id], [cell.machine_id])
    _assert_plain_conflict(_post_undo(client, retired, _undo_body(retired)), "retired")

    # Restoring into a deactivated Area.
    source, target = _Cell(client), _Cell(client)
    flow_id, pn = _release(client, source)
    transfer = _transfer(client, source, target, flow_id, pn)
    _ok(admin_of(client).patch(f"/api/areas/{source.area_id}", json={"is_active": False}))
    deactivated = _Command(target, pn, str(transfer["device_event_id"]), 1, [flow_id], [])
    before = _row_counts(db_engine)
    _assert_plain_conflict(_post_undo(client, deactivated, _undo_body(deactivated)), "deactivated")
    assert _row_counts(db_engine) == before


def _scanned_undo(client: TestClient) -> tuple[_Command, dict[str, Any]]:
    """An Undo of a prior transfer at a Scanned-session station with a valid session."""
    command_ = _plain_transfer(client)
    _ok(
        admin_of(client).patch(
            f"/api/areas/{command_.cell.area_id}", json={"worker_identification_mode": "SCANNED"}
        )
    )
    signed_in = _worker(client)
    scan = _ok(
        client.post(
            f"/api/scan-stations/{command_.cell.station_id}/badge-scans",
            json={"badge": signed_in["badge_barcode"]},
        )
    )
    assert scan["outcome"] == "SIGNED_IN"
    return command_, signed_in


def _open_sessions(engine: Engine, station_id: str) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text(
                    "SELECT id, worker_id, expires_at, ended_at FROM worker_sessions"
                    " WHERE station_id = :station ORDER BY id"
                ),
                {"station": station_id},
            )
        ]


def test_the_reason_precedes_the_badge_gate(client: TestClient, db_engine: Engine) -> None:
    command_, _ = _scanned_undo(client)
    _set_required(client, True)
    confirming = _worker(client)
    sessions = _open_sessions(db_engine, command_.cell.station_id)
    before = _row_counts(db_engine)

    # No reason, no badge: the reason refusal, not the badge requirement.
    _assert_reason_required(_post_undo(client, command_, _undo_body(command_)))
    # No reason with a valid badge: the reason refusal, and no gate sign-in staged.
    _assert_reason_required(
        _post_undo(
            client, command_, _undo_body(command_, confirming_badge=confirming["badge_barcode"])
        )
    )
    assert _open_sessions(db_engine, command_.cell.station_id) == sessions
    assert _row_counts(db_engine) == before

    body = _undo_body(command_, reason="wrong PN", confirming_badge=confirming["badge_barcode"])
    _ok(_post_undo(client, command_, body), 201)
    rows = _reversal_rows(db_engine, body["device_event_id"])
    assert [(row.reason, row.worker_id) for row in rows] == [("wrong PN", confirming["id"])]


def test_a_reason_at_a_question_gate_records_the_session_worker(
    client: TestClient, db_engine: Engine
) -> None:
    command_, signed_in = _scanned_undo(client)
    _ok(admin_of(client).put(_SESSIONS_POLICY_PATH, json={"badge_confirm_undo": False}))
    _set_required(client, True)
    body = _undo_body(command_, reason="wrong PN")
    _ok(_post_undo(client, command_, body), 201)
    rows = _reversal_rows(db_engine, body["device_event_id"])
    assert [(row.reason, row.worker_id) for row in rows] == [("wrong PN", signed_in["id"])]
    assert rows[0].scan_session_id is not None


# ---------------------------------------------------------------------------
# U-9 — shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [123, [], {}, True])
def test_a_non_text_reason_is_refused_by_the_schema(
    client: TestClient, db_engine: Engine, reason: object
) -> None:
    command_ = _plain_transfer(client)
    before = _row_counts(db_engine)
    response = _post_undo(client, command_, _undo_body(command_, reason=reason))
    assert response.status_code == 422, response.text
    assert _row_counts(db_engine) == before


def test_an_unknown_field_is_still_refused(client: TestClient) -> None:
    command_ = _plain_transfer(client)
    response = _post_undo(client, command_, _undo_body(command_, reasons="wrong PN"))
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("required", [False, True])
def test_a_reason_containing_nul_is_refused_before_anything(
    client: TestClient, db_engine: Engine, required: bool
) -> None:
    command_ = _plain_transfer(client)
    _set_required(client, required)
    before = _row_counts(db_engine)
    # The JSON escape \u0000 on the wire (json.dumps escapes the NUL).
    content = json.dumps(_undo_body(command_, reason="wrong\x00PN"))
    assert "\\u0000" in content
    response = client.post(
        f"/api/scan-stations/{command_.cell.station_id}/undos",
        content=content,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": _E_R2}
    assert _row_counts(db_engine) == before

    # Under a committed device_event_id too: the shape precedes the fast path.
    body = _undo_body(command_, reason="wrong PN")
    _ok(_post_undo(client, command_, body), 201)
    response = _post_undo(client, command_, {**body, "reason": "wrong\x00PN"})
    assert (response.status_code, response.json()) == (422, {"detail": _E_R2})


def test_the_application_guard_refuses_nul_and_non_text() -> None:
    for value in ("a\x00", 1, b"a"):
        with pytest.raises(InvalidInputError, match="plain text"):
            undo._undo_reason(value)
    assert undo._undo_reason(None) is None
    assert undo._undo_reason("   ") is None
    assert undo._undo_reason("  a b ") == "a b"


# ---------------------------------------------------------------------------
# U-10 — the preview
# ---------------------------------------------------------------------------


def test_the_preview_reports_the_policy(client: TestClient, db_engine: Engine) -> None:
    command_ = _plain_transfer(client)
    before = _row_counts(db_engine)
    assert _preview(client, command_)["reason_required"] is False
    counts = _row_counts(db_engine)
    _set_required(client, True)
    assert _preview(client, command_)["reason_required"] is True
    assert _row_counts(db_engine) == {**counts, "audit_events": counts["audit_events"] + 1}
    _set_required(client, False)
    assert _preview(client, command_)["reason_required"] is False
    assert before == counts

    # Present on an ineligible preview too.
    _ok(_post_undo(client, command_, _undo_body(command_)), 201)
    _set_required(client, True)
    ineligible = _preview(client, command_)
    assert (ineligible["eligible"], ineligible["reason_required"]) == (False, True)


# ---------------------------------------------------------------------------
# U-11 / U-11b / U-11c / U-12 — concurrency
# ---------------------------------------------------------------------------


def test_commands_never_lock_the_policy(client: TestClient, db_engine: Engine) -> None:
    command_ = _plain_transfer(client)
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(sa.text("UPDATE application_policy SET undo_reason_required = true"))
        thread, results = _start(lambda: _post_undo(client, command_, _undo_body(command_)))
        try:
            # Read the committed `false`, never blocked by the uncommitted change.
            response = _finish(thread, results)
        finally:
            holder.commit()
    assert response.status_code == 201, response.text
    later = _plain_transfer(client)
    _assert_reason_required(_post_undo(client, later, _undo_body(later)))


def test_the_policy_is_judged_under_the_commands_locks(
    client: TestClient, db_engine: Engine
) -> None:
    command_ = _plain_transfer(client)
    before = _state(db_engine, command_)
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT 1 FROM quantity_flows WHERE id = ANY(:ids) FOR UPDATE"),
            {"ids": command_.flow_ids},
        )
        thread, results = _start(lambda: _post_undo(client, command_, _undo_body(command_)))
        try:
            _assert_blocked(thread)
            # A different row: the PUT is not blocked by the held flow.
            _set_required(client, True)
        finally:
            holder.rollback()
        response = _finish(thread, results)
    _assert_reason_required(response)
    after = _state(db_engine, command_)
    # Only the policy PUT's own audit row was added.
    assert after[:3] == before[:3]
    assert after[3] == {**before[3], "audit_events": before[3]["audit_events"] + 1}


def test_resolver_policy_reads_still_see_a_change_committed_while_they_waited(
    client: TestClient, db_engine: Engine
) -> None:
    command_, _ = _scanned_undo(client)
    _ok(admin_of(client).put(_SESSIONS_POLICY_PATH, json={"badge_confirm_undo": False}))
    sessions = _open_sessions(db_engine, command_.cell.station_id)
    with db_engine.connect() as holder:
        # Conflicts with the resolver's FOR KEY SHARE re-read of the station Area.
        holder.execute(
            sa.text("SELECT 1 FROM areas WHERE id = :id FOR UPDATE"),
            {"id": command_.cell.area_id},
        )
        thread, results = _start(lambda: _post_undo(client, command_, _undo_body(command_)))
        try:
            # The slice 6 read already ran (no reason: no short-circuit) and said off.
            _assert_blocked(thread)
            _ok(admin_of(client).put(_SESSIONS_POLICY_PATH, json={"badge_confirm_undo": True}))
        finally:
            holder.rollback()
        response = _finish(thread, results)
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": _E_G1, "badge_confirmation_required": True}
    assert _open_sessions(db_engine, command_.cell.station_id) == sessions


def test_the_two_policy_sections_never_overwrite_each_other(
    client: TestClient, db_engine: Engine
) -> None:
    with db_engine.connect() as holder:
        holder.execute(sa.text("SELECT 1 FROM application_policy WHERE id = 1 FOR NO KEY UPDATE"))
        holder.execute(sa.text("UPDATE application_policy SET badge_confirm_done = false"))
        thread, results = _start(
            lambda: admin_of(client).put(_POLICY_PATH, json={"undo_reason_required": True})
        )
        try:
            _assert_blocked(thread)
            holder.commit()
        finally:
            response = _finish(thread, results)
    assert _ok(response)["undo_reason_required"] is True
    with db_engine.connect() as connection:
        stored = connection.execute(
            sa.text("SELECT badge_confirm_done, undo_reason_required FROM application_policy")
        ).one()
    assert tuple(stored) == (False, True)
    last = _policy_audits(db_engine, "correction-permissions")[-1]
    assert (last.before_data, last.after_data) == (
        {"undo_reason_required": False},
        {"undo_reason_required": True},
    )


# ---------------------------------------------------------------------------
# U-13 — Tracking
# ---------------------------------------------------------------------------


def test_tracking_lists_the_reversal_reason(client: TestClient) -> None:
    command_ = _plain_transfer(client)
    _ok(_post_undo(client, command_, _undo_body(command_, reason="wrong PN")), 201)
    detail = _ok(
        admin_of(client).get("/api/tracking/detail", params={"part_number": command_.part_number})
    )
    reversed_ = [
        movement
        for movement in detail["movements"]["movements"]
        if movement["movement_type"] == "REVERSED"
    ]
    assert [movement["reason"] for movement in reversed_] == ["wrong PN"]


# ---------------------------------------------------------------------------
# U-14 — static guards on undo.py
# ---------------------------------------------------------------------------


def _undo_tree() -> ast.Module:
    return ast.parse(_UNDO_MODULE.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    [function] = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    return function


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    found = [
        item for item in ast.walk(node) if isinstance(item, ast.Call) and _called_name(item) == name
    ]
    return sorted(found, key=lambda call: (call.lineno, call.col_offset))


def test_the_reason_joins_the_fingerprint_only_when_present() -> None:
    fingerprint = _function(_undo_tree(), "_request_fingerprint")
    guarded = [
        node
        for node in ast.walk(fingerprint)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.ops[0], ast.IsNot)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "reason"
    ]
    assert len(guarded) == 1
    assert ast.unparse(guarded[0].body[0]) == "normalized['reason'] = reason"


def test_the_policy_check_sits_between_the_state_refusals_and_the_resolver() -> None:
    tree = _undo_tree()
    command_ = _function(tree, "undo_command")
    [check] = _calls(command_, "is_undo_reason_required")
    rechecks = _calls(command_, "committed_command")
    assert len(rechecks) >= 2
    assert check.lineno > rechecks[1].lineno
    area_loops = [
        node
        for node in ast.walk(command_)
        if isinstance(node, ast.For) and "current_area_id" in ast.unparse(node.iter)
    ]
    assert area_loops and check.lineno > max(loop.end_lineno or 0 for loop in area_loops)
    [resolver] = _calls(command_, "resolve_station_identity")
    assert check.lineno < resolver.lineno
    # Only the command and its preview read the policy.
    owners = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef) and _calls(function, "is_undo_reason_required")
    }
    assert owners == {"undo_command", "undo_preview"}


def test_every_reversed_row_carries_exactly_the_normalized_reason() -> None:
    command_ = _function(_undo_tree(), "undo_command")
    movements = _calls(command_, "PartMovement")
    assert movements
    for call in movements:
        [keyword] = [keyword for keyword in call.keywords if keyword.arg == "reason"]
        assert isinstance(keyword.value, ast.Name) and keyword.value.id == "undo_reason"


def test_the_undo_command_never_loads_the_policy_entity() -> None:
    tree = _undo_tree()
    assert "get_policy" not in ast.unparse(_function(tree, "undo_command"))
    assert "get_policy" not in _UNDO_MODULE.read_text(encoding="utf-8")


def test_no_local_named_reason_is_assigned_in_the_undo_command() -> None:
    command_ = _function(_undo_tree(), "undo_command")
    assigned = {
        target.id
        for node in ast.walk(command_)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "reason" not in assigned
    assert "ineligible" in assigned
