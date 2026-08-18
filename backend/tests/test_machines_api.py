"""Integration tests for the Phase 3.5 Machines management API.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Covered per the Phase 3.5
scope (PROJECT_PROFILE §8.6, GUI_DESIGN §12):

- automatic Asset Tag allocation from the configured format: the
  zero-padded rendering, the minimum-width overflow behavior, the
  never-reset counter across format changes, and the atomic rollback
  of an unissued number when creation fails at COMMIT;
- server-owned identity: ``asset_tag``/``barcode_value`` are never
  client-writable, the barcode is always derived, and the Area of an
  active Machine is not editable;
- display-name uniqueness among the active Machines of one Area only
  (reuse across Areas and after retirement stays allowed);
- the maintenance override lifecycle: start/update-in-place/clear with
  the documented ``maintenance_since``/``state_changed_at`` semantics;
- retirement and reactivation: blockers, the forward-only Area change,
  and the append-only lifecycle events committing atomically with the
  change they record — blocked actions record neither.

The API commits real transactions, so tests isolate through unique
names instead of rollbacks; the module database is dropped afterwards.
The development database in DATABASE_URL is only used as the admin
connection for CREATE/DROP DATABASE.
"""

import os
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

from alembic import command
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_machines_api"


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
    """Direct database access for seeding and state verification."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def asset_tag_format(client: TestClient) -> None:
    """Machine creation requires the configured Asset Tag format."""
    response = client.put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "CD-", "digits": 4},
    )
    assert response.status_code == 200, response.text


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _create_area(client: TestClient, **overrides: Any) -> dict[str, Any]:
    department = client.post("/api/departments", json={"name": _unique("DEPT")})
    assert department.status_code == 201, department.text
    payload = {"department_id": department.json()["id"], "name": _unique("AREA"), **overrides}
    response = client.post("/api/areas", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _create_machine(
    client: TestClient, area_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    if area_id is None:
        area_id = int(_create_area(client)["id"])
    payload = {"area_id": area_id, "name": _unique("MACHINE"), **overrides}
    response = client.post("/api/machines", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _retire(client: TestClient, machine_id: int, **overrides: Any) -> dict[str, Any]:
    response = client.post(f"/api/machines/{machine_id}/retire", json={**overrides})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _lifecycle_events(client: TestClient, machine_id: int) -> list[dict[str, Any]]:
    response = client.get(f"/api/machines/{machine_id}/lifecycle-events")
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


def _next_sequence(db_engine: Engine) -> int:
    with db_engine.connect() as connection:
        return int(
            connection.execute(
                sa.select(models.MachineAssetTagConfig.next_sequence).where(
                    models.MachineAssetTagConfig.id == 1
                )
            ).scalar_one()
        )


def _set_next_sequence(db_engine: Engine, value: int) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            sa.update(models.MachineAssetTagConfig)
            .where(models.MachineAssetTagConfig.id == 1)
            .values(next_sequence=value)
        )


# ---------------------------------------------------------------------------
# Asset Tag allocation
# ---------------------------------------------------------------------------


def test_create_allocates_sequential_asset_tags_and_derived_barcode(
    client: TestClient, db_engine: Engine
) -> None:
    _set_next_sequence(db_engine, 1)
    first = _create_machine(client)
    second = _create_machine(client)

    assert first["asset_tag"] == "CD-0001"
    assert second["asset_tag"] == "CD-0002"
    for machine in (first, second):
        assert machine["barcode_value"] == f"PF:MACHINE:{machine['asset_tag']}"
        assert machine["retired_on"] is None
        assert machine["maintenance_since"] is None
        assert machine["state_changed_at"] and machine["created_at"]
    assert _next_sequence(db_engine) == 3


def test_format_change_applies_forward_and_never_resets_the_counter(
    client: TestClient, db_engine: Engine
) -> None:
    before = _create_machine(client)
    before_sequence = _next_sequence(db_engine)

    reformatted = client.put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "MS-", "digits": 6},
    )
    assert reformatted.status_code == 200
    assert reformatted.json()["next_sequence"] == before_sequence

    after = _create_machine(client)
    # The new format applies to Machines created afterwards only; the
    # existing tag is never renamed and the counter keeps counting.
    assert after["asset_tag"] == f"MS-{before_sequence:06d}"
    assert client.get(f"/api/machines/{before['id']}").json()["asset_tag"] == before["asset_tag"]

    restored = client.put(
        "/api/barcode-configuration/machine-asset-tag-format",
        json={"prefix": "CD-", "digits": 4},
    )
    assert restored.status_code == 200


def test_digits_are_a_minimum_width_never_a_truncation(
    client: TestClient, db_engine: Engine
) -> None:
    _set_next_sequence(db_engine, 123456)
    overflow = _create_machine(client)
    assert overflow["asset_tag"] == "CD-123456"
    _set_next_sequence(db_engine, 500)
    padded = _create_machine(client)
    assert padded["asset_tag"] == "CD-0500"


def test_create_requires_configured_asset_tag_format(client: TestClient, db_engine: Engine) -> None:
    area = _create_area(client)
    with db_engine.begin() as connection:
        connection.execute(sa.delete(models.MachineAssetTagConfig))
    try:
        response = client.post(
            "/api/machines", json={"area_id": area["id"], "name": _unique("MACHINE")}
        )
        assert response.status_code == 409
        assert "not configured" in response.json()["detail"]
    finally:
        restored = client.put(
            "/api/barcode-configuration/machine-asset-tag-format",
            json={"prefix": "CD-", "digits": 4},
        )
        assert restored.status_code == 200
        # Recreating the deleted singleton restarted its counter at 1;
        # move it past every tag already issued in this module so later
        # allocations never collide.
        _set_next_sequence(db_engine, 200000)


def test_failed_creation_rolls_back_the_allocated_sequence(
    client: TestClient, db_engine: Engine
) -> None:
    """Atomicity of allocation + INSERT: a creation that fails at COMMIT
    returns its unissued sequence number with the transaction."""
    area = _create_area(client)
    sequence = _next_sequence(db_engine)
    # Occupy the tag the next allocation would render, bypassing the
    # counter — the API insert then loses at COMMIT on
    # uq_machines_asset_tag after its pre-checks passed.
    colliding_tag = f"CD-{sequence:04d}"
    with db_engine.begin() as connection:
        connection.execute(
            sa.insert(models.Machine).values(
                area_id=int(area["id"]), name=_unique("SEED"), asset_tag=colliding_tag
            )
        )

    blocked = client.post("/api/machines", json={"area_id": area["id"], "name": _unique("MACHINE")})
    assert blocked.status_code == 409
    assert "Asset Tag" in blocked.json()["detail"]
    # Rolled back together: no Machine row, counter unchanged.
    assert _next_sequence(db_engine) == sequence

    # After the counter passes the occupied value, creation recovers.
    _set_next_sequence(db_engine, sequence + 1)
    recovered = _create_machine(client, area_id=int(area["id"]))
    assert recovered["asset_tag"] == f"CD-{sequence + 1:04d}"


# ---------------------------------------------------------------------------
# Server-owned identity and validation
# ---------------------------------------------------------------------------


def test_asset_tag_and_barcode_are_never_client_writable(client: TestClient) -> None:
    area = _create_area(client)
    for field in ({"asset_tag": "CD-9999"}, {"barcode_value": "PF:MACHINE:CD-9999"}):
        response = client.post(
            "/api/machines", json={"area_id": area["id"], "name": _unique("MACHINE"), **field}
        )
        assert response.status_code == 422, field

    machine = _create_machine(client, area_id=int(area["id"]))
    for field in (
        {"asset_tag": "CD-9999"},
        {"barcode_value": "PF:MACHINE:CD-9999"},
        {"area_id": area["id"]},
        {"retired_on": "2026-08-18"},
        {"maintenance_since": "2026-08-18T00:00:00Z"},
    ):
        response = client.patch(f"/api/machines/{machine['id']}", json=field)
        assert response.status_code == 422, field


def test_create_requires_existing_active_area_and_name(client: TestClient) -> None:
    missing = client.post("/api/machines", json={"area_id": 999999, "name": _unique("MACHINE")})
    assert missing.status_code == 422

    area = _create_area(client)
    assert client.patch(f"/api/areas/{area['id']}", json={"is_active": False}).status_code == 200
    inactive = client.post(
        "/api/machines", json={"area_id": area["id"], "name": _unique("MACHINE")}
    )
    assert inactive.status_code == 409

    active_area = _create_area(client)
    blank = client.post("/api/machines", json={"area_id": active_area["id"], "name": "   "})
    assert blank.status_code == 422
    assert "must not be empty" in blank.json()["detail"]


def test_display_name_unique_among_active_machines_of_one_area_only(
    client: TestClient,
) -> None:
    area = _create_area(client)
    machine = _create_machine(client, area_id=int(area["id"]))

    duplicate = client.post("/api/machines", json={"area_id": area["id"], "name": machine["name"]})
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]

    # The same name stays valid in another Area.
    other_area = _create_area(client)
    elsewhere = _create_machine(client, area_id=int(other_area["id"]), name=machine["name"])
    assert elsewhere["name"] == machine["name"]

    # Replacement reuse: after retirement the familiar floor-position
    # name is free again — the retired record keeps it too.
    _retire(client, int(machine["id"]))
    replacement = _create_machine(client, area_id=int(area["id"]), name=machine["name"])
    assert replacement["asset_tag"] != machine["asset_tag"]
    retired_row = client.get(f"/api/machines/{machine['id']}").json()
    assert retired_row["name"] == machine["name"]


def test_metadata_edit_keeps_identity_and_rejects_retired_records(
    client: TestClient,
) -> None:
    machine = _create_machine(client, manufacturer="  Haas  ", model="VF-2")
    assert machine["manufacturer"] == "Haas"

    new_name = _unique("MACHINE")
    updated = client.patch(
        f"/api/machines/{machine['id']}",
        json={
            "name": new_name,
            "manufacturer": None,
            "serial_number": "SN-1234",
            "installed_on": "2024-05-01",
            "notes": "Spindle rebuilt 2025.",
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == new_name
    assert body["manufacturer"] is None
    assert body["serial_number"] == "SN-1234"
    assert body["installed_on"] == "2024-05-01"
    assert body["asset_tag"] == machine["asset_tag"]
    assert body["updated_at"] > machine["updated_at"]

    rename_collision_target = _create_machine(client, area_id=int(machine["area_id"]))
    collision = client.patch(
        f"/api/machines/{rename_collision_target['id']}", json={"name": new_name}
    )
    assert collision.status_code == 409

    _retire(client, int(machine["id"]))
    frozen = client.patch(f"/api/machines/{machine['id']}", json={"notes": "changed"})
    assert frozen.status_code == 409
    assert "retired" in frozen.json()["detail"]

    unknown = client.patch("/api/machines/999999", json={"notes": "x"})
    assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# Maintenance override
# ---------------------------------------------------------------------------


def test_maintenance_start_update_in_place_and_clear(client: TestClient) -> None:
    machine = _create_machine(client)

    started = client.post(
        f"/api/machines/{machine['id']}/maintenance",
        json={"note": "Spindle bearing change", "expected_return": "2026-08-25"},
    )
    assert started.status_code == 201
    body = started.json()
    assert body["maintenance_since"] is not None
    assert body["maintenance_note"] == "Spindle bearing change"
    assert body["maintenance_expected_return"] == "2026-08-25"
    # Starting the override changes the derived state — the age resets.
    assert body["state_changed_at"] > machine["state_changed_at"]

    again = client.post(f"/api/machines/{machine['id']}/maintenance", json={})
    assert again.status_code == 409
    assert "already under maintenance" in again.json()["detail"]

    updated = client.patch(
        f"/api/machines/{machine['id']}/maintenance",
        json={"note": "Waiting for parts", "expected_return": "2026-09-01"},
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["maintenance_note"] == "Waiting for parts"
    assert updated_body["maintenance_expected_return"] == "2026-09-01"
    # In-place context update: neither the start time nor the state.
    assert updated_body["maintenance_since"] == body["maintenance_since"]
    assert updated_body["state_changed_at"] == body["state_changed_at"]

    cleared = client.delete(f"/api/machines/{machine['id']}/maintenance")
    assert cleared.status_code == 200
    cleared_body = cleared.json()
    assert cleared_body["maintenance_since"] is None
    assert cleared_body["maintenance_note"] is None
    assert cleared_body["maintenance_expected_return"] is None
    assert cleared_body["state_changed_at"] > updated_body["state_changed_at"]


def test_maintenance_requires_an_active_override_and_an_active_record(
    client: TestClient,
) -> None:
    machine = _create_machine(client)

    for response in (
        client.patch(f"/api/machines/{machine['id']}/maintenance", json={"note": "x"}),
        client.delete(f"/api/machines/{machine['id']}/maintenance"),
    ):
        assert response.status_code == 409
        assert "not under maintenance" in response.json()["detail"]

    _retire(client, int(machine["id"]))
    retired_start = client.post(f"/api/machines/{machine['id']}/maintenance", json={})
    assert retired_start.status_code == 409
    assert "retired" in retired_start.json()["detail"]


def test_retirement_keeps_maintenance_context_until_reactivation(
    client: TestClient,
) -> None:
    # Retirement is allowed while the override is active and leaves its
    # context untouched (no canonical rule clears it); reactivation is
    # the step that explicitly clears any override.
    machine = _create_machine(client)
    assert (
        client.post(
            f"/api/machines/{machine['id']}/maintenance", json={"note": "long repair"}
        ).status_code
        == 201
    )
    retired = _retire(client, int(machine["id"]))
    assert retired["maintenance_since"] is not None
    assert retired["maintenance_note"] == "long repair"

    reactivated = client.post(
        f"/api/machines/{machine['id']}/reactivate", json={"reason": "Repair finished"}
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["maintenance_since"] is None
    assert reactivated.json()["maintenance_note"] is None


# ---------------------------------------------------------------------------
# Retirement, reactivation, lifecycle history
# ---------------------------------------------------------------------------


def test_retire_records_the_atomic_lifecycle_event(client: TestClient) -> None:
    machine = _create_machine(client)
    assert _lifecycle_events(client, int(machine["id"])) == []

    retired = client.post(
        f"/api/machines/{machine['id']}/retire",
        json={"reason": "Replaced by a new machine", "actor": "  Peter  "},
    )
    assert retired.status_code == 200
    assert retired.json()["retired_on"] is not None

    events = _lifecycle_events(client, int(machine["id"]))
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == "RETIRED"
    assert event["before_state"] == "ACTIVE"
    assert event["after_state"] == "RETIRED"
    assert event["reason"] == "Replaced by a new machine"
    assert event["actor"] == "Peter"
    assert event["from_area_id"] is None and event["to_area_id"] is None
    assert event["occurred_at"]

    again = client.post(f"/api/machines/{machine['id']}/retire", json={})
    assert again.status_code == 409
    assert "already retired" in again.json()["detail"]
    # The blocked retirement recorded nothing.
    assert len(_lifecycle_events(client, int(machine["id"]))) == 1


def test_lifecycle_filter_splits_active_and_retired(client: TestClient) -> None:
    active_machine = _create_machine(client)
    retired_machine = _create_machine(client)
    _retire(client, int(retired_machine["id"]))

    active_ids = [row["id"] for row in client.get("/api/machines?lifecycle=active").json()]
    retired_ids = [row["id"] for row in client.get("/api/machines?lifecycle=retired").json()]
    all_ids = [row["id"] for row in client.get("/api/machines").json()]

    assert active_machine["id"] in active_ids and active_machine["id"] not in retired_ids
    assert retired_machine["id"] in retired_ids and retired_machine["id"] not in active_ids
    assert active_machine["id"] in all_ids and retired_machine["id"] in all_ids

    invalid = client.get("/api/machines?lifecycle=broken")
    assert invalid.status_code == 422


def test_reactivate_same_area_returns_idle_on_the_same_record(client: TestClient) -> None:
    machine = _create_machine(client, serial_number="SN-REACT-1")
    _retire(client, int(machine["id"]), reason="Seasonal shutdown")

    reactivated = client.post(
        f"/api/machines/{machine['id']}/reactivate",
        json={"reason": "Season restart", "actor": "Mai"},
    )
    assert reactivated.status_code == 200
    body = reactivated.json()
    # Same record, same identity, retirement cleared, state age reset.
    assert body["id"] == machine["id"]
    assert body["asset_tag"] == machine["asset_tag"]
    assert body["barcode_value"] == machine["barcode_value"]
    assert body["area_id"] == machine["area_id"]
    assert body["retired_on"] is None
    assert body["state_changed_at"] > machine["state_changed_at"]

    events = _lifecycle_events(client, int(machine["id"]))
    assert [event["event_type"] for event in events] == ["RETIRED", "REACTIVATED"]
    reactivation = events[1]
    assert reactivation["before_state"] == "RETIRED"
    assert reactivation["after_state"] == "ACTIVE"
    assert reactivation["reason"] == "Season restart"
    assert reactivation["actor"] == "Mai"
    # No Area move: the pair stays absent.
    assert reactivation["from_area_id"] is None and reactivation["to_area_id"] is None


def test_reactivate_with_forward_only_area_move_records_the_pair(
    client: TestClient,
) -> None:
    origin_area = _create_area(client)
    target_area = _create_area(client)
    machine = _create_machine(client, area_id=int(origin_area["id"]))
    _retire(client, int(machine["id"]))

    moved = client.post(
        f"/api/machines/{machine['id']}/reactivate",
        json={"reason": "Machine moved to the new hall", "area_id": target_area["id"]},
    )
    assert moved.status_code == 200
    assert moved.json()["area_id"] == target_area["id"]

    reactivation = _lifecycle_events(client, int(machine["id"]))[-1]
    assert reactivation["from_area_id"] == origin_area["id"]
    assert reactivation["to_area_id"] == target_area["id"]


def test_reactivate_requires_a_reason_and_a_retired_machine(client: TestClient) -> None:
    machine = _create_machine(client)

    not_retired = client.post(f"/api/machines/{machine['id']}/reactivate", json={"reason": "x"})
    assert not_retired.status_code == 409
    assert "not retired" in not_retired.json()["detail"]

    _retire(client, int(machine["id"]))
    for payload in ({}, {"reason": "   "}):
        response = client.post(f"/api/machines/{machine['id']}/reactivate", json=payload)
        assert response.status_code == 422, payload
    # Blocked attempts recorded nothing beyond the retirement.
    assert len(_lifecycle_events(client, int(machine["id"]))) == 1


def test_reactivate_blockers_area_name_and_serial(client: TestClient) -> None:
    area = _create_area(client)
    machine = _create_machine(client, area_id=int(area["id"]), serial_number="SN-DUP-77")
    _retire(client, int(machine["id"]))

    # Target Area must be active.
    inactive_area = _create_area(client)
    assert (
        client.patch(f"/api/areas/{inactive_area['id']}", json={"is_active": False}).status_code
        == 200
    )
    to_inactive = client.post(
        f"/api/machines/{machine['id']}/reactivate",
        json={"reason": "back", "area_id": inactive_area["id"]},
    )
    assert to_inactive.status_code == 409
    to_missing = client.post(
        f"/api/machines/{machine['id']}/reactivate", json={"reason": "back", "area_id": 999999}
    )
    assert to_missing.status_code == 422

    # Display-name collision with an active Machine of the target Area;
    # renaming resolves it.
    occupant = _create_machine(client, area_id=int(area["id"]), name=machine["name"])
    collision = client.post(f"/api/machines/{machine['id']}/reactivate", json={"reason": "back"})
    assert collision.status_code == 409
    assert "already exists" in collision.json()["detail"]

    # Serial number meanwhile reissued to another active Machine.
    reissued = client.patch(f"/api/machines/{occupant['id']}", json={"serial_number": "SN-DUP-77"})
    assert reissued.status_code == 200
    serial_blocked = client.post(
        f"/api/machines/{machine['id']}/reactivate",
        json={"reason": "back", "name": _unique("MACHINE")},
    )
    assert serial_blocked.status_code == 409
    assert "serial number" in serial_blocked.json()["detail"]

    # Every blocked attempt left the record retired with only the
    # original RETIRED event — no partial write, no stray event.
    frozen = client.get(f"/api/machines/{machine['id']}").json()
    assert frozen["retired_on"] is not None
    assert len(_lifecycle_events(client, int(machine["id"]))) == 1

    # Clearing both blockers, the rename-on-reactivate path succeeds.
    resolved = client.patch(f"/api/machines/{occupant['id']}", json={"serial_number": None})
    assert resolved.status_code == 200
    renamed = _unique("MACHINE")
    reactivated = client.post(
        f"/api/machines/{machine['id']}/reactivate",
        json={"reason": "back", "name": renamed},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["name"] == renamed


def test_lifecycle_events_are_append_only_in_the_database(
    client: TestClient, db_engine: Engine
) -> None:
    machine = _create_machine(client)
    _retire(client, int(machine["id"]))
    event_id = _lifecycle_events(client, int(machine["id"]))[0]["id"]

    for statement in (
        sa.update(models.MachineLifecycleEvent)
        .where(models.MachineLifecycleEvent.id == event_id)
        .values(reason="rewritten"),
        sa.delete(models.MachineLifecycleEvent).where(models.MachineLifecycleEvent.id == event_id),
    ):
        with pytest.raises(sa.exc.DBAPIError, match="append-only"), db_engine.begin() as connection:
            connection.execute(statement)


def test_lifecycle_history_unknown_machine_not_found(client: TestClient) -> None:
    response = client.get("/api/machines/999999/lifecycle-events")
    assert response.status_code == 404
