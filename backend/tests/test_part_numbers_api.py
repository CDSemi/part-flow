"""Integration tests for the Phase 4 PartNumber master API.

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
- create-on-first-valid-use with reuse of the existing canonical PN:
  the master is created exactly once, and its ``CREATED`` audit row
  commits in the same transaction (rolled back together on failure);
- the barcode is fully derived (``PF:PN:<canonical-part-number>``) —
  no barcode is stored, entered, or separately issued;
- lookup by exact canonical number and by contains-search.

The API commits real transactions, so tests isolate through unique PN
values; the module database is dropped afterwards.
"""

import os
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

from alembic import command
from app.application import part_numbers
from app.core.config import get_settings
from app.infrastructure import models
from app.main import create_app

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_TEST_DATABASE = "partflow_test_part_numbers_api"


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
    created = client.post("/api/part-numbers", json={"part_number": f"  {canonical.lower()}  "})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["part_number"] == canonical
    assert body["barcode_value"] == f"PF:PN:{canonical}"

    events = _audit_rows(db_engine, canonical)
    assert len(events) == 1
    assert events[0].event_type == "CREATED"
    assert events[0].before_data is None
    assert events[0].after_data == {"part_number": canonical}


def test_existing_canonical_pn_is_reused_not_duplicated(
    client: TestClient, db_engine: Engine
) -> None:
    """Every case/whitespace variant resolves to the one master row;
    reuse appends no second CREATED event."""
    canonical = _unique_pn()
    first = client.post("/api/part-numbers", json={"part_number": canonical})
    assert first.status_code == 201, first.text

    for variant in (canonical, canonical.lower(), f" {canonical.lower()} ", f"\t{canonical}\n"):
        reused = client.post("/api/part-numbers", json={"part_number": variant})
        assert reused.status_code == 200, reused.text
        assert reused.json()["part_number"] == canonical
        assert reused.json()["created_at"] == first.json()["created_at"]

    with db_engine.connect() as connection:
        rows = connection.execute(
            sa.select(sa.func.count())
            .select_from(models.PartNumber.__table__)
            .where(models.PartNumber.part_number == canonical)
        ).scalar_one()
    assert rows == 1
    assert len(_audit_rows(db_engine, canonical)) == 1


def test_invalid_part_numbers_are_rejected_with_zero_writes(
    client: TestClient, db_engine: Engine
) -> None:
    """Internal whitespace is never silently removed; nothing persists."""
    masters_before = _count(db_engine, models.PartNumber.__table__)
    audits_before = _count(db_engine, models.AuditEvent.__table__)

    for invalid in ("ABC 123", "ABC\t123", "ABC\n123", "", "   "):
        rejected = client.post("/api/part-numbers", json={"part_number": invalid})
        assert rejected.status_code == 422, rejected.text

    assert _count(db_engine, models.PartNumber.__table__) == masters_before
    assert _count(db_engine, models.AuditEvent.__table__) == audits_before


def test_lookup_by_number_and_search(client: TestClient) -> None:
    """`number` resolves the exact canonical PN (empty list on a miss);
    `search` is a case-insensitive contains-match."""
    canonical = _unique_pn("LOOKUP")
    assert client.post("/api/part-numbers", json={"part_number": canonical}).status_code == 201

    exact = client.get("/api/part-numbers", params={"number": f" {canonical.lower()} "})
    assert exact.status_code == 200
    assert [master["part_number"] for master in exact.json()] == [canonical]

    miss = client.get("/api/part-numbers", params={"number": _unique_pn("MISSING")})
    assert miss.status_code == 200
    assert miss.json() == []

    invalid = client.get("/api/part-numbers", params={"number": "ABC 123"})
    assert invalid.status_code == 422

    fragment = canonical[len("LOOKUP-") :].lower()
    found = client.get("/api/part-numbers", params={"search": fragment})
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
        assert client.post("/api/part-numbers", json={"part_number": canonical}).status_code == 201

    bounded = client.get("/api/part-numbers", params={"search": prefix})
    assert bounded.status_code == 200
    returned = [master["part_number"] for master in bounded.json()]
    assert len(returned) == part_numbers.SEARCH_RESULT_LIMIT
    # The bound keeps the canonical-PN ordering — it truncates the tail,
    # it does not sample.
    assert returned == sorted(seeded)[: part_numbers.SEARCH_RESULT_LIMIT]

    # The unfiltered listing is bounded by the same query.
    everything = client.get("/api/part-numbers")
    assert everything.status_code == 200
    assert len(everything.json()) <= part_numbers.SEARCH_RESULT_LIMIT

    # ...and the exact resolution reaches a master the bound cut off.
    beyond = sorted(seeded)[-1]
    exact = client.get("/api/part-numbers", params={"number": beyond})
    assert exact.status_code == 200
    assert [master["part_number"] for master in exact.json()] == [beyond]


def test_one_character_part_number_is_valid_and_exactly_resolvable(
    client: TestClient,
) -> None:
    """A short PN is a PN: the search bound is an optimization, never a
    domain rule about what a Part Number may be."""
    created = client.post("/api/part-numbers", json={"part_number": "x"})
    assert created.status_code in {200, 201}, created.text
    assert created.json()["part_number"] == "X"
    assert created.json()["barcode_value"] == "PF:PN:X"

    exact = client.get("/api/part-numbers", params={"number": "x"})
    assert exact.status_code == 200
    assert [master["part_number"] for master in exact.json()] == ["X"]


def test_server_owned_fields_are_rejected(client: TestClient) -> None:
    """extra="forbid": a client cannot write the derived barcode."""
    rejected = client.post(
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
        client.post("/api/part-numbers", json={"part_number": canonical})

    monkeypatch.undo()
    lookup = client.get("/api/part-numbers", params={"number": canonical})
    assert lookup.json() == []
    assert _audit_rows(db_engine, canonical) == []
