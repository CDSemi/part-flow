"""Integration tests for the PartNumber master API (Phase 4; Phase 13 create).

Exercises the full request path — FastAPI routes, Application-layer
services, and PostgreSQL — against a dedicated temporary database
migrated to head by the real Alembic chain. Covered per the Phase 4
intake scope (PROJECT_PROFILE §7 Part Number, §8.1, §10;
SLICE1_DATA_MODEL §6, §16):

- the one canonical normalization is reused, never duplicated:
  surrounding whitespace trimmed, canonical UPPERCASE stored and
  returned, and every case/whitespace variant of one PN resolves to a
  single master row;
- internal whitespace is rejected with zero writes — never silently
  removed to turn invalid input into a valid PN;
- the explicit create is create-only (Phase 13 slice 7): the master is
  created exactly once with its optional details, every later variant
  of the same canonical PN answers 409, concurrent creates have one
  winner, and the ``CREATED`` audit row — the full details snapshot —
  commits in the same transaction (rolled back together on failure);
- the barcode is fully derived (``PF:PN:<canonical-part-number>``) —
  no barcode is stored, entered, or separately issued;
- lookup by exact canonical number and by contains-search.

The Phase 13 management routes (edit, image, delete, page) are covered
by ``test_part_number_management_api.py``.

The API commits real transactions, so tests isolate through unique PN
values; the module database is dropped afterwards.
"""

import os
import threading
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
from app.application import part_numbers
from app.application.errors import InvalidInputError
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app
from tests.auth_harness import admin_of

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_part_numbers_api"


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


def _snapshot(
    part_number: str,
    name: str | None = None,
    current_revision: str | None = None,
    erp_id: str | None = None,
) -> dict[str, str | None]:
    """The audited details snapshot of a master (Phase 13 S7-OD3)."""
    return {
        "part_number": part_number,
        "name": name,
        "current_revision": current_revision,
        "erp_id": erp_id,
    }


def _already_saved(part_number: str) -> str:
    return f"Part Number \u201c{part_number}\u201d already has saved details."


def _unique_pn(prefix: str = "PN") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def _count(engine: Engine, table: sa.FromClause) -> int:
    with engine.connect() as connection:
        return connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()


def _audit_rows(engine: Engine, entity_id: str) -> list[sa.Row[Any]]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                sa.select(models.AuditEvent.__table__)
                .where(
                    models.AuditEvent.entity_type == "PartNumber",
                    models.AuditEvent.entity_id == entity_id,
                )
                .order_by(models.AuditEvent.id)
            )
        )


def test_create_normalizes_and_derives_the_barcode(client: TestClient, db_engine: Engine) -> None:
    """First valid use: trimmed, uppercased, created once with its
    CREATED audit row and the derived PF:PN barcode."""
    canonical = _unique_pn()
    created = admin_of(client).post(
        "/api/part-numbers", json={"part_number": f"  {canonical.lower()}  "}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["part_number"] == canonical
    assert body["barcode_value"] == f"PF:PN:{canonical}"

    events = _audit_rows(db_engine, canonical)
    assert len(events) == 1
    assert events[0].event_type == "CREATED"
    assert events[0].before_data is None
    assert events[0].after_data == _snapshot(canonical)


def test_pn_the_os_libc_would_uppercase_is_created(client: TestClient, db_engine: Engine) -> None:
    """`ɤ` (U+0264) has no uppercase in the backend's Python (UCD 15.0)
    but the OS libc maps it: since 0018 the "C"-collation CHECK admits
    the domain-canonical value instead of failing as an untranslated 500."""
    raw = f"pnɤ-{uuid.uuid4().hex[:8]}"
    canonical = raw.upper()
    assert "ɤ" in canonical
    created = admin_of(client).post("/api/part-numbers", json={"part_number": raw})
    assert created.status_code == 201, created.text
    assert created.json()["part_number"] == canonical

    events = _audit_rows(db_engine, canonical)
    assert [event.event_type for event in events] == ["CREATED"]
    assert events[0].after_data == _snapshot(canonical)


def _master_rows(engine: Engine, canonical: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartNumber.__table__)
            .where(models.PartNumber.part_number == canonical)
        ).scalar_one()


def test_post_is_create_only(client: TestClient, db_engine: Engine) -> None:
    """Every case/whitespace variant of an existing master answers 409
    naming the canonical PN; the one row and its one CREATED event stay."""
    canonical = _unique_pn()
    first = admin_of(client).post("/api/part-numbers", json={"part_number": canonical})
    assert first.status_code == 201, first.text

    for variant in (canonical, canonical.lower(), f" {canonical.lower()} ", f"\t{canonical}\n"):
        refused = admin_of(client).post("/api/part-numbers", json={"part_number": variant})
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == _already_saved(canonical)

    assert _master_rows(db_engine, canonical) == 1
    assert len(_audit_rows(db_engine, canonical)) == 1


def test_post_records_trimmed_details(client: TestClient, db_engine: Engine) -> None:
    """The details are trimmed (blank → null) and the CREATED row
    carries the full four-key snapshot."""
    canonical = _unique_pn("DETAILS")
    created = admin_of(client).post(
        "/api/part-numbers",
        json={
            "part_number": canonical,
            "name": "  BRACKET, MOUNTING  ",
            "current_revision": "   ",
            "erp_id": " ERP-PN-1 ",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "BRACKET, MOUNTING"
    assert body["current_revision"] is None
    assert body["erp_id"] == "ERP-PN-1"
    assert body["image_updated_at"] is None

    events = _audit_rows(db_engine, canonical)
    assert [event.event_type for event in events] == ["CREATED"]
    assert events[0].after_data == _snapshot(canonical, name="BRACKET, MOUNTING", erp_id="ERP-PN-1")


def test_post_refuses_non_text_and_unknown_fields_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    """A non-text detail is refused by request validation (422) and
    nothing is written; so are the derived barcode, an actor, an image
    field and any unknown field."""
    masters_before = _count(db_engine, models.PartNumber.__table__)
    audits_before = _count(db_engine, models.AuditEvent.__table__)

    extras: list[dict[str, Any]] = [
        {"name": 12},
        {"current_revision": 3},
        {"erp_id": ["ERP"]},
        {"barcode_value": "PF:PN:FORGED"},
        {"actor": "mallory"},
        {"image_updated_at": "2026-10-05T00:00:00Z"},
        {"unknown": "x"},
    ]
    for extra in extras:
        rejected = admin_of(client).post(
            "/api/part-numbers", json={"part_number": _unique_pn(), **extra}
        )
        assert rejected.status_code == 422, (extra, rejected.text)

    assert _count(db_engine, models.PartNumber.__table__) == masters_before
    assert _count(db_engine, models.AuditEvent.__table__) == audits_before


def test_services_refuse_non_text_details_before_any_write(
    api_database_url: URL, db_engine: Engine, client: TestClient
) -> None:
    """The Application guard (E5) runs before any lock or write — over
    HTTP the typed request model refuses a non-string first."""
    canonical = _unique_pn("GUARD")
    actor_user_id = admin_of(client).user_id
    engine = create_engine(api_database_url)
    try:
        with Session(engine) as session:
            part_numbers.create_part_number(session, canonical, actor_user_id=actor_user_id)
        masters_before = _count(db_engine, models.PartNumber.__table__)
        audits_before = _count(db_engine, models.AuditEvent.__table__)
        cases = (
            ("name", "Name / Description must be text."),
            ("current_revision", "Revision must be text."),
            ("erp_id", "ERP ID must be text."),
        )
        for field, message in cases:
            with Session(engine) as session:
                with pytest.raises(InvalidInputError) as created:
                    part_numbers.create_part_number(
                        session, _unique_pn(), actor_user_id=actor_user_id, **{field: 7}
                    )
                assert created.value.message == message
            with Session(engine) as session:
                with pytest.raises(InvalidInputError) as updated:
                    part_numbers.update_part_number(
                        session, canonical, actor_user_id=actor_user_id, **{field: 7}
                    )
                assert updated.value.message == message
    finally:
        engine.dispose()

    assert _count(db_engine, models.PartNumber.__table__) == masters_before
    assert _count(db_engine, models.AuditEvent.__table__) == audits_before


def test_concurrent_creates_have_one_winner(client: TestClient, db_engine: Engine) -> None:
    """Two simultaneous POSTs of one new PN: exactly one 201, one 409,
    one row and one CREATED event."""
    canonical = _unique_pn("RACE")
    barrier = threading.Barrier(2)
    statuses: list[int] = []
    guard = threading.Lock()

    def _post(raw: str) -> None:
        barrier.wait()
        response = admin_of(client).post("/api/part-numbers", json={"part_number": raw})
        with guard:
            statuses.append(response.status_code)

    threads = [
        threading.Thread(target=_post, args=(raw,)) for raw in (canonical, canonical.lower())
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(statuses) == [201, 409]
    assert _master_rows(db_engine, canonical) == 1
    assert [event.event_type for event in _audit_rows(db_engine, canonical)] == ["CREATED"]


def test_invalid_part_numbers_are_rejected_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    """Internal whitespace is never silently removed; nothing persists."""
    masters_before = _count(db_engine, models.PartNumber.__table__)
    audits_before = _count(db_engine, models.AuditEvent.__table__)

    for invalid in ("ABC 123", "ABC\t123", "ABC\n123", "", "   "):
        rejected = admin_of(client).post("/api/part-numbers", json={"part_number": invalid})
        assert rejected.status_code == 422, rejected.text

    assert _count(db_engine, models.PartNumber.__table__) == masters_before
    assert _count(db_engine, models.AuditEvent.__table__) == audits_before


def test_lookup_by_number_and_search(client: TestClient) -> None:
    """`number` resolves the exact canonical PN (empty list on a miss);
    `search` is a case-insensitive contains-match (the name match is
    covered by the management tests)."""
    canonical = _unique_pn("LOOKUP")
    assert (
        admin_of(client).post("/api/part-numbers", json={"part_number": canonical}).status_code
        == 201
    )

    exact = admin_of(client).get("/api/part-numbers", params={"number": f" {canonical.lower()} "})
    assert exact.status_code == 200
    assert [master["part_number"] for master in exact.json()] == [canonical]

    miss = admin_of(client).get("/api/part-numbers", params={"number": _unique_pn("MISSING")})
    assert miss.status_code == 200
    assert miss.json() == []

    invalid = admin_of(client).get("/api/part-numbers", params={"number": "ABC 123"})
    assert invalid.status_code == 422

    fragment = canonical[len("LOOKUP-") :].lower()
    found = admin_of(client).get("/api/part-numbers", params={"search": fragment})
    assert found.status_code == 200
    assert canonical in [master["part_number"] for master in found.json()]


def test_search_results_are_bounded_by_the_server(client: TestClient) -> None:
    """The lookup is bounded in the QUERY, not by slicing in the browser.

    The PN master is an unbounded catalog: a broad search term (and the
    unfiltered listing) must never stream all of it to a client. The
    bound is the server's, so a client that ignores it still cannot
    download more than one screenful.
    """
    prefix = f"BOUND{uuid.uuid4().hex[:6].upper()}"
    seeded = [f"{prefix}-{index:03d}" for index in range(part_numbers.SEARCH_RESULT_LIMIT + 10)]
    for canonical in seeded:
        assert (
            admin_of(client).post("/api/part-numbers", json={"part_number": canonical}).status_code
            == 201
        )

    bounded = admin_of(client).get("/api/part-numbers", params={"search": prefix})
    assert bounded.status_code == 200
    returned = [master["part_number"] for master in bounded.json()]
    assert len(returned) == part_numbers.SEARCH_RESULT_LIMIT
    # The bound keeps the canonical-PN ordering — it truncates the tail,
    # it does not sample.
    assert returned == sorted(seeded)[: part_numbers.SEARCH_RESULT_LIMIT]

    # The unfiltered listing is bounded by the same query.
    everything = admin_of(client).get("/api/part-numbers")
    assert everything.status_code == 200
    assert len(everything.json()) <= part_numbers.SEARCH_RESULT_LIMIT

    # ...and the exact resolution reaches a master the bound cut off.
    beyond = sorted(seeded)[-1]
    exact = admin_of(client).get("/api/part-numbers", params={"number": beyond})
    assert exact.status_code == 200
    assert [master["part_number"] for master in exact.json()] == [beyond]


def test_one_character_part_number_is_valid_and_exactly_resolvable(
    client: TestClient,
) -> None:
    """A short PN is a PN: the search bound is an optimization, never a
    domain rule about what a Part Number may be."""
    created = admin_of(client).post("/api/part-numbers", json={"part_number": "x"})
    assert created.status_code == 201, created.text
    assert created.json()["part_number"] == "X"
    assert created.json()["barcode_value"] == "PF:PN:X"

    exact = admin_of(client).get("/api/part-numbers", params={"number": "x"})
    assert exact.status_code == 200
    assert [master["part_number"] for master in exact.json()] == ["X"]


def test_server_owned_fields_are_rejected(client: TestClient) -> None:
    """extra="forbid": a client cannot write the derived barcode."""
    rejected = admin_of(client).post(
        "/api/part-numbers",
        json={"part_number": _unique_pn(), "barcode_value": "PF:PN:FORGED"},
    )
    assert rejected.status_code == 422, rejected.text


def test_failed_audit_write_rolls_back_the_master(
    client: TestClient, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The master and its CREATED audit row commit together or not at
    all: an audit failure leaves no PartNumber behind."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr("app.application.audit.append_audit_event", _boom)
    canonical = _unique_pn("ATOMIC")
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        admin_of(client).post("/api/part-numbers", json={"part_number": canonical})

    monkeypatch.undo()
    lookup = admin_of(client).get("/api/part-numbers", params={"number": canonical})
    assert lookup.json() == []
    assert _audit_rows(db_engine, canonical) == []
