"""Integration tests for the Phase 13 Workers registry API.

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain:

- create and list: name trimmed, badge canonicalized (trim, UPPERCASE;
  owner decision OD-3) in the response, the row and the audit snapshot;
  non-ASCII canonical values agree with the database CHECK;
  case-insensitive uniqueness across active and inactive Workers;
- validation with zero writes (empty name/badge, `PF:` in any case,
  over-long badges, server-owned fields, explicit nulls, unknown ids);
- duplicate badges, including races lost at flush on create and edit;
- edits, no-ops, and the per-facet audit chain;
- the Worker row lock (a concurrent edit audits its committed
  predecessor) and audit atomicity on every write path;
- the avatar: PUT/GET/DELETE, ETag/304, identical-upload no-op, and
  every refusal (413, 415, 422, 404) with nothing stored;
- the badge resolver used by the later Scan Station slices;
- Phase 13 S3: the Fixed Worker of any Area cannot be deactivated;
- registry isolation: only the registry's owners touch the `workers`
  table.

The API commits real transactions, so tests isolate through unique
names and badges instead of rollbacks; the module database is dropped
afterwards. The development database in DATABASE_URL is only used as
the admin connection for CREATE/DROP DATABASE.
"""

import ast
import hashlib
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
from httpx import Response
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from alembic import command
from app.application import workers as workers_service
from app.core.config import get_settings
from app.domain.worker_badge import normalize_badge_barcode
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_workers_api"

_RESPONSE_KEYS = {
    "id",
    "name",
    "badge_barcode",
    "is_active",
    "avatar_updated_at",
    "created_at",
    "updated_at",
}
_RACE_MESSAGE = "This badge barcode is already assigned to another Worker."
_UNSUPPORTED = "The file is not a PNG, JPEG or WebP image."
_TOO_LARGE = "The image is larger than 2 MB. Choose a smaller image."

# Minimal byte fixtures carrying the real magic bytes of each type.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x02" * 16
_WEBP = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x03" * 16


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
    """Application client wired to the temporary database through the
    real startup path (DATABASE_URL pointed at it, settings re-read)."""
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
    """Direct database access for assertions and concurrent holders."""
    engine = create_engine(api_database_url)
    yield engine
    engine.dispose()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _badge() -> str:
    """A fresh badge already in canonical form."""
    return f"B{uuid.uuid4().hex[:12]}".upper()


def _create_worker(
    client: TestClient, name: str | None = None, badge: str | None = None
) -> dict[str, Any]:
    payload = {"name": name or _unique("Worker"), "badge_barcode": badge or _badge()}
    response = client.post("/api/workers", json=payload)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _count(engine: Engine, table: sa.FromClause) -> int:
    with engine.connect() as connection:
        return int(connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one())


def _write_counts(engine: Engine) -> dict[str, int]:
    return {
        "workers": _count(engine, models.Worker.__table__),
        "audit_events": _count(engine, models.AuditEvent.__table__),
    }


def _audit_rows(engine: Engine, worker_id: int) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AuditEvent.__table__)
                .where(
                    models.AuditEvent.entity_type == "Worker",
                    models.AuditEvent.entity_id == str(worker_id),
                )
                .order_by(models.AuditEvent.id)
            )
        )


def _stored(engine: Engine, worker_id: int) -> sa.Row[Any]:
    """The whole stored row, avatar bytes included."""
    with engine.connect() as connection:
        return connection.execute(
            sa.select(models.Worker.__table__).where(models.Worker.id == worker_id)
        ).one()


def _put_avatar(
    client: TestClient, worker_id: int, data: bytes, content_type: str = "image/png"
) -> Response:
    response = client.put(
        f"/api/workers/{worker_id}/avatar", content=data, headers={"Content-Type": content_type}
    )
    return cast(Response, response)


def _digest(data: bytes, content_type: str) -> dict[str, Any]:
    return {
        "content_type": content_type,
        "byte_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Create and list
# ---------------------------------------------------------------------------


def test_create_trims_the_name_and_canonicalizes_the_badge(
    client: TestClient, db_engine: Engine
) -> None:
    raw_badge = f" abc{uuid.uuid4().hex[:8]}\r\n"
    canonical = raw_badge.strip().upper()
    name = _unique("Alex Tran")
    response = client.post("/api/workers", json={"name": f"  {name} ", "badge_barcode": raw_badge})
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == _RESPONSE_KEYS
    assert body["name"] == name
    assert body["badge_barcode"] == canonical
    assert body["is_active"] is True
    assert body["avatar_updated_at"] is None
    assert _stored(db_engine, body["id"]).badge_barcode == canonical

    events = _audit_rows(db_engine, body["id"])
    assert len(events) == 1
    assert events[0].event_type == "CREATED"
    assert events[0].before_data is None
    assert events[0].after_data == {"name": name, "badge_barcode": canonical, "is_active": True}
    assert events[0].actor_reference is None
    assert events[0].metadata is None


def test_list_includes_inactive_workers_ordered_by_name_then_id(client: TestClient) -> None:
    stem = _unique("List")
    second = _create_worker(client, name=f"{stem} B")
    first = _create_worker(client, name=f"{stem} A")
    twin = _create_worker(client, name=f"{stem} A")
    deactivated = client.patch(f"/api/workers/{second['id']}", json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text

    response = client.get("/api/workers")
    assert response.status_code == 200
    listed = [worker for worker in response.json() if worker["name"].startswith(stem)]
    assert [worker["id"] for worker in listed] == [first["id"], twin["id"], second["id"]]
    assert listed[2]["is_active"] is False
    for worker in response.json():
        # No bytes, type or size ever travel in a Worker payload.
        assert set(worker) == _RESPONSE_KEYS


# `ɤ` (U+0264): Python 3.12 (Unicode 15) has no uppercase for it, while
# the glibc `upper()` of the database collation maps it to U+A7CB — the
# canonical form comes from the one domain rule, whatever its Unicode
# version.
_NON_ASCII_BADGES = [
    ("straße", "STRASSE"),
    ("é1", "É1"),
    ("ǆ9", "Ǆ9"),
    ("ɤ1", normalize_badge_barcode("ɤ1")),
]


@pytest.mark.parametrize(("raw", "canonical"), _NON_ASCII_BADGES)
def test_non_ascii_canonical_badges_pass_the_database_check(
    client: TestClient, db_engine: Engine, raw: str, canonical: str
) -> None:
    """Every badge the domain rule accepts passes the canonical-form
    CHECK, even where Python and the OS libc case tables disagree: the
    CHECK compares under the "C" collation, so it never fails with a 500."""
    suffix = uuid.uuid4().hex[:8].upper()
    response = client.post(
        "/api/workers", json={"name": _unique("Unicode"), "badge_barcode": f"{raw}-{suffix}"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["badge_barcode"] == f"{canonical}-{suffix}"
    assert _stored(db_engine, response.json()["id"]).badge_barcode == f"{canonical}-{suffix}"


@pytest.mark.parametrize(("raw", "canonical"), _NON_ASCII_BADGES)
def test_non_ascii_canonical_badges_pass_the_database_check_on_edit(
    client: TestClient, db_engine: Engine, raw: str, canonical: str
) -> None:
    worker = _create_worker(client)
    suffix = uuid.uuid4().hex[:8].upper()
    response = client.patch(
        f"/api/workers/{worker['id']}", json={"badge_barcode": f"{raw}-{suffix}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["badge_barcode"] == f"{canonical}-{suffix}"
    assert _stored(db_engine, worker["id"]).badge_barcode == f"{canonical}-{suffix}"


def test_badges_are_unique_regardless_of_letter_case(client: TestClient, db_engine: Engine) -> None:
    lower = f"abc{uuid.uuid4().hex[:8]}"
    holder = _create_worker(client, name=_unique("Holder"), badge=lower)
    assert holder["badge_barcode"] == lower.upper()
    counts = _write_counts(db_engine)
    for variant in (lower.upper(), lower[:1].upper() + lower[1:], f" {lower} "):
        response = client.post(
            "/api/workers", json={"name": _unique("Other"), "badge_barcode": variant}
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"This badge barcode is already assigned to {holder['name']}."
        )
    assert _write_counts(db_engine) == counts


# ---------------------------------------------------------------------------
# Validation — zero writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name_is_refused(client: TestClient, db_engine: Engine, name: str) -> None:
    counts = _write_counts(db_engine)
    response = client.post("/api/workers", json={"name": name, "badge_barcode": _badge()})
    assert response.status_code == 422
    assert response.json()["detail"] == "Worker name must not be empty."
    assert _write_counts(db_engine) == counts


def test_a_nul_character_in_the_name_is_refused_never_500(
    client: TestClient, db_engine: Engine
) -> None:
    """PostgreSQL text cannot hold NUL: the name is refused before any query."""
    worker = _create_worker(client)
    counts = _write_counts(db_engine)
    detail = {"detail": "Worker name must not contain a NUL character."}
    created = client.post("/api/workers", json={"name": "A\x00B", "badge_barcode": _badge()})
    edited = client.patch(f"/api/workers/{worker['id']}", json={"name": "A\x00B"})
    for response in (created, edited):
        assert (response.status_code, response.json()) == (422, detail)
    assert _write_counts(db_engine) == counts


@pytest.mark.parametrize("badge", ["", "\r\n"])
def test_empty_badge_is_refused(client: TestClient, db_engine: Engine, badge: str) -> None:
    counts = _write_counts(db_engine)
    response = client.post("/api/workers", json={"name": _unique("W"), "badge_barcode": badge})
    assert response.status_code == 422
    assert response.json()["detail"] == "Badge barcode must not be empty."
    assert _write_counts(db_engine) == counts


@pytest.mark.parametrize("badge", ["PF:WORKER:1", "pf:x", " Pf:abc "])
def test_partflow_namespace_badge_is_refused_in_any_case(
    client: TestClient, db_engine: Engine, badge: str
) -> None:
    counts = _write_counts(db_engine)
    response = client.post("/api/workers", json={"name": _unique("W"), "badge_barcode": badge})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "A badge barcode cannot start with PF: — that prefix belongs to PartFlow barcodes."
        " Use the barcode printed on the employee badge."
    )
    assert _write_counts(db_engine) == counts


def test_over_long_badges_are_refused_never_500(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client)
    counts = _write_counts(db_engine)
    for badge in ("A" * 129, "A" * 3000):
        created = client.post("/api/workers", json={"name": _unique("W"), "badge_barcode": badge})
        assert created.status_code == 422
        assert created.json()["detail"] == "A badge barcode must be at most 128 characters."
        edited = client.patch(f"/api/workers/{worker['id']}", json={"badge_barcode": badge})
        assert edited.status_code == 422
        assert edited.json()["detail"] == "A badge barcode must be at most 128 characters."
    assert _write_counts(db_engine) == counts
    assert _stored(db_engine, worker["id"]).badge_barcode == worker["badge_barcode"]


@pytest.mark.parametrize(
    "extra",
    [
        {"id": 99},
        {"avatar_image_type": "image/png"},
        {"actor": "someone"},
        {"is_active": False},
    ],
)
def test_create_refuses_unknown_and_server_owned_fields(
    client: TestClient, db_engine: Engine, extra: dict[str, Any]
) -> None:
    counts = _write_counts(db_engine)
    response = client.post(
        "/api/workers", json={"name": _unique("W"), "badge_barcode": _badge(), **extra}
    )
    assert response.status_code == 422
    assert _write_counts(db_engine) == counts


@pytest.mark.parametrize("extra", [{"id": 99}, {"avatar_updated_at": None}, {"actor": "someone"}])
def test_update_refuses_unknown_and_server_owned_fields(
    client: TestClient, db_engine: Engine, extra: dict[str, Any]
) -> None:
    worker = _create_worker(client)
    counts = _write_counts(db_engine)
    response = client.patch(f"/api/workers/{worker['id']}", json={"name": "Changed", **extra})
    assert response.status_code == 422
    assert _write_counts(db_engine) == counts
    assert _stored(db_engine, worker["id"]).name == worker["name"]


@pytest.mark.parametrize(
    ("field", "detail"),
    [
        ("name", "Worker name must not be empty."),
        ("badge_barcode", "Badge barcode must be text."),
        ("is_active", "Worker active status must be true or false."),
    ],
)
def test_update_refuses_explicit_null(
    client: TestClient, db_engine: Engine, field: str, detail: str
) -> None:
    worker = _create_worker(client)
    counts = _write_counts(db_engine)
    response = client.patch(f"/api/workers/{worker['id']}", json={field: None})
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert _write_counts(db_engine) == counts


def test_update_of_an_unknown_worker_is_404(client: TestClient) -> None:
    response = client.patch("/api/workers/999999", json={"name": "Nobody"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Worker 999999 does not exist."


# ---------------------------------------------------------------------------
# Duplicate badges — zero writes
# ---------------------------------------------------------------------------


def test_duplicate_of_an_active_worker_names_the_worker(
    client: TestClient, db_engine: Engine
) -> None:
    holder = _create_worker(client)
    counts = _write_counts(db_engine)
    response = client.post(
        "/api/workers", json={"name": _unique("W"), "badge_barcode": holder["badge_barcode"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"This badge barcode is already assigned to {holder['name']}."
    )
    assert _write_counts(db_engine) == counts


def test_duplicate_of_an_inactive_worker_names_it_as_inactive(
    client: TestClient, db_engine: Engine
) -> None:
    holder = _create_worker(client)
    client.patch(f"/api/workers/{holder['id']}", json={"is_active": False})
    counts = _write_counts(db_engine)
    response = client.post(
        "/api/workers", json={"name": _unique("W"), "badge_barcode": holder["badge_barcode"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"This badge barcode is already assigned to {holder['name']} (inactive)."
    )
    assert _write_counts(db_engine) == counts


def test_edit_to_another_workers_badge_is_refused_in_any_case(
    client: TestClient, db_engine: Engine
) -> None:
    holder = _create_worker(client)
    editor = _create_worker(client)
    counts = _write_counts(db_engine)
    for variant in (holder["badge_barcode"], holder["badge_barcode"].lower()):
        response = client.patch(f"/api/workers/{editor['id']}", json={"badge_barcode": variant})
        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"This badge barcode is already assigned to {holder['name']}."
        )
    assert _write_counts(db_engine) == counts
    assert _stored(db_engine, editor["id"]).badge_barcode == editor["badge_barcode"]


def test_create_race_lost_at_flush_is_a_conflict(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UNIQUE stays the authority: with the pre-check bypassed (the
    concurrent writer's window), the flush refusal maps to the generic
    conflict and nothing is written."""
    badge = _badge()
    first = _create_worker(client, badge=badge)
    counts = _write_counts(db_engine)
    monkeypatch.setattr(workers_service, "_reject_duplicate_badge", lambda *a, **k: None)
    response = client.post("/api/workers", json={"name": _unique("W"), "badge_barcode": badge})
    assert response.status_code == 409
    assert response.json()["detail"] == _RACE_MESSAGE
    assert _write_counts(db_engine) == counts
    assert len(_audit_rows(db_engine, first["id"])) == 1


def test_edit_race_lost_at_flush_is_a_conflict_never_500(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder = _create_worker(client)
    editor = _create_worker(client)
    counts = _write_counts(db_engine)
    monkeypatch.setattr(workers_service, "_reject_duplicate_badge", lambda *a, **k: None)
    response = client.patch(
        f"/api/workers/{editor['id']}", json={"badge_barcode": holder["badge_barcode"]}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == _RACE_MESSAGE
    assert _write_counts(db_engine) == counts
    assert _stored(db_engine, editor["id"]).badge_barcode == editor["badge_barcode"]
    assert len(_audit_rows(db_engine, editor["id"])) == 1


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_name_change_is_audited_with_exact_snapshots(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client)
    new_name = _unique("Renamed")
    response = client.patch(f"/api/workers/{worker['id']}", json={"name": f" {new_name} "})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == new_name
    events = _audit_rows(db_engine, worker["id"])
    assert [event.event_type for event in events] == ["CREATED", "UPDATED"]
    profile = {"badge_barcode": worker["badge_barcode"], "is_active": True}
    assert events[1].before_data == {"name": worker["name"], **profile}
    assert events[1].after_data == {"name": new_name, **profile}
    assert events[1].actor_reference is None


def test_badge_change_is_canonicalized_and_audited(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client)
    new_badge = f"new{uuid.uuid4().hex[:8]}"
    response = client.patch(f"/api/workers/{worker['id']}", json={"badge_barcode": new_badge})
    assert response.status_code == 200, response.text
    assert response.json()["badge_barcode"] == new_badge.upper()
    events = _audit_rows(db_engine, worker["id"])
    assert events[1].before_data["badge_barcode"] == worker["badge_barcode"]
    assert events[1].after_data["badge_barcode"] == new_badge.upper()


def test_deactivation_and_reactivation_are_two_audited_updates(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _create_worker(client)
    off = client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
    assert off.status_code == 200 and off.json()["is_active"] is False
    on = client.patch(f"/api/workers/{worker['id']}", json={"is_active": True})
    assert on.status_code == 200 and on.json()["is_active"] is True
    events = _audit_rows(db_engine, worker["id"])
    assert [event.event_type for event in events] == ["CREATED", "UPDATED", "UPDATED"]
    assert (events[1].before_data["is_active"], events[1].after_data["is_active"]) == (True, False)
    assert (events[2].before_data["is_active"], events[2].after_data["is_active"]) == (False, True)


def _fixed_area(client: TestClient, worker_id: int) -> dict[str, Any]:
    department = client.post("/api/departments", json={"name": _unique("DEPT")})
    assert department.status_code == 201, department.text
    response = client.post(
        "/api/areas",
        json={
            "department_id": department.json()["id"],
            "name": _unique("AREA"),
            "worker_identification_mode": "FIXED",
            "fixed_worker_id": worker_id,
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def test_the_fixed_worker_of_an_area_cannot_be_deactivated(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _create_worker(client)
    path = f"/api/workers/{worker['id']}"
    first = _fixed_area(client, worker["id"])

    before = _write_counts(db_engine)
    refused = client.patch(path, json={"is_active": False})
    assert refused.status_code == 409
    assert refused.json()["detail"] == (
        f"Worker '{worker['name']}' is the Fixed Worker of Area '{first['name']}'. Choose"
        " another Fixed Worker or Worker ID mode for that Area in Administration → Areas"
        " before deactivating this Worker."
    )
    assert _write_counts(db_engine) == before

    # Any Area counts, an inactive one included; both are named, by name.
    second = _fixed_area(client, worker["id"])
    assert client.patch(f"/api/areas/{second['id']}", json={"is_active": False}).status_code == 200
    before = _write_counts(db_engine)
    refused = client.patch(path, json={"is_active": False, "name": _unique("Renamed")})
    assert refused.status_code == 409
    names = ", ".join(f"'{name}'" for name in sorted([first["name"], second["name"]]))
    assert refused.json()["detail"] == (
        f"Worker '{worker['name']}' is the Fixed Worker of Areas {names}. Choose another"
        " Fixed Worker or Worker ID mode for those Areas in Administration → Areas before"
        " deactivating this Worker."
    )
    assert _write_counts(db_engine) == before
    assert _stored(db_engine, worker["id"]).is_active is True

    for area in (first, second):
        response = client.patch(
            f"/api/areas/{area['id']}", json={"worker_identification_mode": "DISABLED"}
        )
        assert response.status_code == 200, response.text
    before = _write_counts(db_engine)
    deactivated = client.patch(path, json={"is_active": False})
    assert deactivated.status_code == 200, deactivated.text
    assert _write_counts(db_engine)["audit_events"] == before["audit_events"] + 1
    # Reactivation has no guard.
    assert client.patch(path, json={"is_active": True}).status_code == 200


def test_no_op_patch_writes_and_audits_nothing(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client, badge=f"ABC{uuid.uuid4().hex[:8].upper()}")
    stored_before = _stored(db_engine, worker["id"])
    for body in (
        {"badge_barcode": f" {worker['badge_barcode'].lower()} "},
        {"name": f" {worker['name']} ", "badge_barcode": worker["badge_barcode"]},
        {"name": worker["name"], "badge_barcode": worker["badge_barcode"], "is_active": True},
        {},
    ):
        response = client.patch(f"/api/workers/{worker['id']}", json=body)
        assert response.status_code == 200, response.text
        assert response.json() == worker
    assert len(_audit_rows(db_engine, worker["id"])) == 1
    assert _stored(db_engine, worker["id"]).updated_at == stored_before.updated_at


def test_audit_chain_is_continuous_within_each_facet(client: TestClient, db_engine: Engine) -> None:
    """Profile and avatar rows interleave; each facet chains to its own
    committed predecessor."""
    worker = _create_worker(client)
    path = f"/api/workers/{worker['id']}"
    assert client.patch(path, json={"name": _unique("One")}).status_code == 200
    assert _put_avatar(client, worker["id"], _PNG).status_code == 200
    assert client.patch(path, json={"badge_barcode": _badge()}).status_code == 200
    assert client.delete(f"{path}/avatar").status_code == 200
    assert client.patch(path, json={"is_active": False}).status_code == 200

    updates = _audit_rows(db_engine, worker["id"])[1:]
    assert len(updates) == 5
    assert [event.id for event in updates] == sorted(event.id for event in updates)
    profile = [event for event in updates if "avatar" not in event.after_data]
    avatar = [event for event in updates if "avatar" in event.after_data]
    assert len(profile) == 3 and len(avatar) == 2
    for previous, current in zip(profile, profile[1:], strict=False):
        assert current.before_data == previous.after_data
    for previous, current in zip(avatar, avatar[1:], strict=False):
        assert current.before_data["avatar"] == previous.after_data["avatar"]
    assert avatar[0].before_data == {"avatar": None}
    assert avatar[1].after_data == {"avatar": None}


# ---------------------------------------------------------------------------
# Row lock
# ---------------------------------------------------------------------------


def test_concurrent_edit_waits_and_audits_the_committed_predecessor(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _create_worker(client)
    results: dict[str, Response] = {}
    with db_engine.connect() as holder:
        holder.execute(
            sa.text("SELECT id FROM workers WHERE id = :id FOR UPDATE"), {"id": worker["id"]}
        )
        holder.execute(
            sa.text("UPDATE workers SET name = 'X' WHERE id = :id"), {"id": worker["id"]}
        )
        patcher = threading.Thread(
            target=lambda: results.update(
                patch=client.patch(f"/api/workers/{worker['id']}", json={"is_active": False})
            )
        )
        patcher.start()
        patcher.join(timeout=0.5)
        # Still waiting on the row lock.
        assert patcher.is_alive()
        holder.commit()
    patcher.join(timeout=30)
    assert not patcher.is_alive()
    assert results["patch"].status_code == 200, results["patch"].text
    assert results["patch"].json()["name"] == "X"
    update = _audit_rows(db_engine, worker["id"])[-1]
    assert update.before_data == {
        "name": "X",
        "badge_barcode": worker["badge_barcode"],
        "is_active": True,
    }
    assert update.after_data["is_active"] is False


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def _boom(*args: object, **kwargs: object) -> None:
    raise RuntimeError("audit persistence failed")


def test_failed_audit_write_rolls_back_every_write_path(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audited change and its audit row commit together or not at all."""
    worker = _create_worker(client)
    with_avatar = _create_worker(client)
    assert _put_avatar(client, with_avatar["id"], _JPEG, "image/jpeg").status_code == 200
    stored = {
        worker["id"]: _stored(db_engine, worker["id"]),
        with_avatar["id"]: _stored(db_engine, with_avatar["id"]),
    }
    counts = _write_counts(db_engine)

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    attempts: list[Callable[[], Response]] = [
        lambda: client.post("/api/workers", json={"name": _unique("W"), "badge_barcode": _badge()}),
        lambda: client.patch(
            f"/api/workers/{worker['id']}",
            json={"name": "Changed", "badge_barcode": _badge(), "is_active": False},
        ),
        lambda: _put_avatar(client, worker["id"], _PNG),
        lambda: client.delete(f"/api/workers/{with_avatar['id']}/avatar"),
    ]
    for attempt in attempts:
        with pytest.raises(RuntimeError, match="audit persistence failed"):
            attempt()
    monkeypatch.undo()

    assert _write_counts(db_engine) == counts
    for worker_id, row in stored.items():
        assert _stored(db_engine, worker_id) == row


# ---------------------------------------------------------------------------
# Avatar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(_PNG, "image/png"), (_JPEG, "image/jpeg"), (_WEBP, "image/webp")],
)
def test_avatar_upload_is_served_back_with_cache_headers(
    client: TestClient, db_engine: Engine, data: bytes, content_type: str
) -> None:
    worker = _create_worker(client)
    response = _put_avatar(client, worker["id"], data, content_type)
    assert response.status_code == 200, response.text
    assert set(response.json()) == _RESPONSE_KEYS
    assert response.json()["avatar_updated_at"] is not None

    served = client.get(f"/api/workers/{worker['id']}/avatar?v=anything")
    assert served.status_code == 200
    assert served.content == data
    assert served.headers["Content-Type"] == content_type
    assert re.fullmatch(r'"\d+"', served.headers["ETag"])
    assert served.headers["Cache-Control"] == "private, no-cache"
    assert served.headers["X-Content-Type-Options"] == "nosniff"

    events = _audit_rows(db_engine, worker["id"])
    assert len(events) == 2
    assert events[1].event_type == "UPDATED"
    assert events[1].before_data == {"avatar": None}
    assert events[1].after_data == {"avatar": _digest(data, content_type)}


def test_matching_if_none_match_answers_304(client: TestClient) -> None:
    worker = _create_worker(client)
    _put_avatar(client, worker["id"], _PNG)
    etag = client.get(f"/api/workers/{worker['id']}/avatar").headers["ETag"]
    cached = client.get(f"/api/workers/{worker['id']}/avatar", headers={"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["ETag"] == etag
    stale = client.get(f"/api/workers/{worker['id']}/avatar", headers={"If-None-Match": '"1"'})
    assert stale.status_code == 200


def test_replacing_the_avatar_changes_its_version_and_audits_the_digest(
    client: TestClient, db_engine: Engine
) -> None:
    worker = _create_worker(client)
    first = _put_avatar(client, worker["id"], _PNG).json()
    first_etag = client.get(f"/api/workers/{worker['id']}/avatar").headers["ETag"]
    second = _put_avatar(client, worker["id"], _WEBP, "image/webp").json()
    assert second["avatar_updated_at"] != first["avatar_updated_at"]
    assert client.get(f"/api/workers/{worker['id']}/avatar").headers["ETag"] != first_etag
    replaced = _audit_rows(db_engine, worker["id"])[-1]
    assert replaced.before_data == {"avatar": _digest(_PNG, "image/png")}
    assert replaced.after_data == {"avatar": _digest(_WEBP, "image/webp")}


def test_identical_avatar_upload_is_a_no_op(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client)
    first = _put_avatar(client, worker["id"], _PNG).json()
    stored = _stored(db_engine, worker["id"])
    again = _put_avatar(client, worker["id"], _PNG)
    assert again.status_code == 200
    assert again.json() == first
    assert len(_audit_rows(db_engine, worker["id"])) == 2
    assert _stored(db_engine, worker["id"]) == stored


def test_removing_the_avatar_is_audited_once(client: TestClient, db_engine: Engine) -> None:
    worker = _create_worker(client)
    _put_avatar(client, worker["id"], _PNG)
    removed = client.delete(f"/api/workers/{worker['id']}/avatar")
    assert removed.status_code == 200
    assert removed.json()["avatar_updated_at"] is None
    missing = client.get(f"/api/workers/{worker['id']}/avatar")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "This Worker has no avatar."
    events = _audit_rows(db_engine, worker["id"])
    assert len(events) == 3
    assert events[2].before_data == {"avatar": _digest(_PNG, "image/png")}
    assert events[2].after_data == {"avatar": None}
    row = _stored(db_engine, worker["id"])
    assert (row.avatar_image, row.avatar_image_type, row.avatar_image_updated_at) == (
        None,
        None,
        None,
    )

    again = client.delete(f"/api/workers/{worker['id']}/avatar")
    assert again.status_code == 200
    assert again.json() == removed.json()
    assert len(_audit_rows(db_engine, worker["id"])) == 3


def _oversized_chunks() -> Iterator[bytes]:
    """A streamed (chunked) body without a Content-Length."""
    yield _PNG
    yield b"\x00" * (2 * 1024 * 1024)


@pytest.mark.parametrize(
    ("request_kwargs", "status", "detail"),
    [
        pytest.param(
            {"content": _PNG + b"\x00" * (2 * 1024 * 1024 + 1 - len(_PNG))},
            413,
            _TOO_LARGE,
            id="body-of-2097153-bytes",
        ),
        pytest.param(
            {"content": _PNG, "headers": {"Content-Length": str(3 * 1024 * 1024)}},
            413,
            _TOO_LARGE,
            id="declared-length-above-the-limit",
        ),
        pytest.param({"content": _oversized_chunks()}, 413, _TOO_LARGE, id="chunked-oversized"),
        pytest.param(
            {"content": _PNG, "headers": {"Content-Type": "text/plain"}},
            415,
            _UNSUPPORTED,
            id="text-plain",
        ),
        pytest.param(
            {"content": _PNG, "headers": {"Content-Type": "image/jpeg"}},
            415,
            _UNSUPPORTED,
            id="png-declared-as-jpeg",
        ),
        pytest.param(
            {"content": b"not an image at all"}, 415, _UNSUPPORTED, id="random-bytes-as-png"
        ),
        pytest.param(
            {"content": _PNG, "headers": {"Content-Type": ""}}, 415, _UNSUPPORTED, id="no-type"
        ),
        pytest.param({"content": b""}, 422, "The image is empty.", id="empty-body"),
    ],
)
def test_avatar_refusals_store_nothing(
    client: TestClient,
    db_engine: Engine,
    request_kwargs: dict[str, Any],
    status: int,
    detail: str,
) -> None:
    worker = _create_worker(client)
    stored = _stored(db_engine, worker["id"])
    counts = _write_counts(db_engine)
    headers = {"Content-Type": "image/png", **request_kwargs.get("headers", {})}
    response = client.put(
        f"/api/workers/{worker['id']}/avatar",
        content=request_kwargs["content"],
        headers=headers,
    )
    assert response.status_code == status, response.text
    assert response.json()["detail"] == detail
    assert _stored(db_engine, worker["id"]) == stored
    assert _write_counts(db_engine) == counts


def test_avatar_routes_of_an_unknown_worker_are_404(client: TestClient) -> None:
    for response in (
        _put_avatar(client, 999999, _PNG),
        client.delete("/api/workers/999999/avatar"),
        client.get("/api/workers/999999/avatar"),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == "Worker 999999 does not exist."


def test_avatar_bytes_are_never_loaded_by_default() -> None:
    assert sa.inspect(models.Worker).attrs["avatar_image"].deferred is True


# ---------------------------------------------------------------------------
# Badge resolver (no standalone route — consumed by the Scan Station
# badge sign-in and badge-confirmation paths)
# ---------------------------------------------------------------------------


def test_resolve_badge(client: TestClient, db_engine: Engine) -> None:
    active = _create_worker(client, badge=f"RES{uuid.uuid4().hex[:8].upper()}")
    inactive = _create_worker(client)
    client.patch(f"/api/workers/{inactive['id']}", json={"is_active": False})
    badge = active["badge_barcode"]
    with Session(db_engine) as session:

        def resolved(raw: object) -> int | None:
            worker = workers_service.resolve_badge(session, raw)
            return None if worker is None else worker.id

        assert resolved(badge) == active["id"]
        assert resolved(f"\r\n{badge}\r\n") == active["id"]
        assert resolved(badge.lower()) == active["id"]
        assert resolved(badge[:2] + badge[2:].lower()) == active["id"]
        assert resolved(inactive["badge_barcode"]) is None
        assert resolved("PF:PN:X") is None
        assert resolved("") is None
        assert resolved("A" * 129) is None
        assert resolved(f"{badge}\x00") is None
        assert resolved(12345) is None
        assert resolved(None) is None
        assert resolved(_badge()) is None


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

_REGISTRY_OWNERS = {
    "app/infrastructure/models.py",
    "app/application/workers.py",
    "app/api/workers.py",
    # Phase 13 S3: the Fixed Worker of an Area, the identity resolver,
    # and the read models naming a recorded Worker.
    "app/application/environment.py",
    "app/application/station_identity.py",
    "app/application/undo.py",
    "app/application/tracking.py",
    "app/api/scan_station.py",
    # Phase 13 S4: the Worker Sessions and the badge sign-in answer
    # naming the signed-in and the previous Worker.
    "app/application/worker_sessions.py",
    "app/application/scan_station.py",
}
_RAW_SQL_ON_WORKERS = re.compile(r"(?i)\b(from|join|update|into)\s+workers\b")
_MODELS_MODULE = "app.infrastructure.models"


def _touches_the_registry(tree: ast.Module) -> bool:
    model_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == _MODELS_MODULE and any(
                alias.name == "Worker" for alias in node.names
            ):
                return True
            if node.module == "app.infrastructure":
                model_aliases.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "models"
                )
        elif isinstance(node, ast.Import):
            model_aliases.update(
                alias.asname
                for alias in node.names
                if alias.name == _MODELS_MODULE and alias.asname
            )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "Worker"
            and isinstance(node.value, ast.Name)
            and node.value.id in model_aliases
        ):
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _RAW_SQL_ON_WORKERS.search(node.value)
        ):
            return True
    return False


def test_worker_registry_is_read_only_by_its_owners() -> None:
    """Only the named owners read or write `workers`: the registry and,
    since Phase 13 S3, the Worker identity modules; the later slices
    extend the allow-list by name."""
    flagged = {
        path.relative_to(_BACKEND_DIR).as_posix()
        for path in sorted((_BACKEND_DIR / "app").rglob("*.py"))
        if _touches_the_registry(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert flagged - _REGISTRY_OWNERS == set()
    for owner in _REGISTRY_OWNERS:
        assert (_BACKEND_DIR / owner).is_file()
