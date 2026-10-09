"""Backup artifacts for the backup tests (Phase 16 slice 5).

Builds ``<backup-dir>/.partial/<NAME>/`` the way ``deploy/production/backup.sh``
leaves it (a fake custom-format dump, a ``pg_restore --list`` output and the
``alembic_version`` data script) from the fixtures captured from a real
PostgreSQL 16.14 dump (``tests/fixtures/backups/``; capture command in
``tests/test_backups.py``), and publishes it with the real
``backups.publish_backup``. Temporary directories only.
"""

import datetime
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from app.application import backups
from app.infrastructure import schema_revision

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "backups"
LIST_FIXTURE = FIXTURES / "partflow.dump.list"
REVISION_FIXTURE = FIXTURES / "alembic_version.sql"
HEAD = schema_revision.code_head()
DUMP = b"PGDMP\x01\x0f\x00" + bytes(range(256)) * 16
RELEASE = "v1.0.0-rc.1"
_COPY = "COPY public.alembic_version (version_num) FROM stdin;"


def utc(year: int, month: int, day: int, hour: int = 2, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.UTC)


def stamp(moment: datetime.datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def name_of(moment: datetime.datetime, kind: str = "daily", label: str | None = None) -> str:
    return f"{stamp(moment)}-{kind}" + (f"-{label}" if label else "")


def listing(
    *,
    dbname: str = "partflow",
    created: datetime.datetime | None = None,
    zone: str = "UTC",
    archive_format: str = "CUSTOM",
    toc_entries: str = "255",
    drop_tables: Iterable[str] = (),
    add_tables: Iterable[str] = (),
    without: Iterable[str] = (),
) -> str:
    """The captured listing with its header fields and table entries replaced.

    ``without`` drops header lines by their prefix (``Archive created at``,
    ``dbname``, ``Dumped from database version``).
    """
    moment = created or utc(2026, 10, 8)
    text = LIST_FIXTURE.read_text(encoding="utf-8")
    text = re.sub(
        r"(?m)^; Archive created at .*$",
        f"; Archive created at {moment:%Y-%m-%d %H:%M:%S} {zone}",
        text,
    )
    text = re.sub(r"(?m)^;     dbname: .*$", f";     dbname: {dbname}", text)
    text = re.sub(r"(?m)^;     Format: .*$", f";     Format: {archive_format}", text)
    text = re.sub(r"(?m)^;     TOC Entries: .*$", f";     TOC Entries: {toc_entries}", text)
    for table in drop_tables:
        text = re.sub(rf"(?m)^\d+; \d+ \d+ TABLE DATA public {table} \S+\n", "", text)
    for table in add_tables:
        text += f"9999; 0 1 TABLE DATA public {table} cdsemi\n"
    for prefix in without:
        text = re.sub(rf"(?m)^;\s+{re.escape(prefix)}.*\n", "", text)
    return text


def revision_script(rows: list[str] | None = None, *, terminated: bool = True) -> str:
    """The captured data script with the given rows (None: no COPY block at all)."""
    text = REVISION_FIXTURE.read_text(encoding="utf-8")
    start = text.index(_COPY)
    end = text.index("\n\\.\n", start)
    if rows is None:
        return text[:start] + text[end + len("\n\\.\n") :]
    body = "".join(f"{row}\n" for row in rows)
    tail = text[end + 1 :] if terminated else ""
    if not terminated:
        return text[:start] + _COPY + "\n" + body
    return text[:start] + _COPY + "\n" + body + tail


def write_partial(
    backup_dir: Path,
    name: str,
    *,
    dump: bytes = DUMP,
    list_text: str | None = None,
    revision: str | None = None,
) -> Path:
    partial = backup_dir / ".partial" / name
    partial.mkdir(parents=True, mode=0o700)
    (partial / "partflow.dump").write_bytes(dump)
    (partial / "partflow.dump.list").write_bytes(
        (list_text if list_text is not None else listing()).encode("utf-8")
    )
    (partial / "alembic_version.sql").write_bytes(
        (revision if revision is not None else revision_script([HEAD])).encode("utf-8")
    )
    return partial


def request(name: str, **overrides: Any) -> backups.ManifestRequest:
    parsed = backups.parse_name(name)
    assert parsed is not None, name
    values: dict[str, Any] = {
        "name": parsed,
        "operator": "scheduler",
        "reason": "scheduled daily backup",
        "release_tag": RELEASE,
        "release_commit": "a" * 40,
        "expected_revision": HEAD,
        "environment": "production",
        "host": "nas01",
        "image_backend": "sha256:" + "b" * 64,
        "image_web": "sha256:" + "c" * 64,
        "image_db": "sha256:" + "d" * 64,
        "tool_tag": RELEASE,
        "tool_commit": "a" * 40,
    }
    values.update(overrides)
    return backups.ManifestRequest(**values)


def publish(
    backup_dir: Path,
    name: str,
    *,
    dbname: str = "partflow",
    created: datetime.datetime | None = None,
    rows: Sequence[str] | None = (HEAD,),
    add_tables: Iterable[str] = (),
    **overrides: Any,
) -> Path:
    """A published backup ``<backup-dir>/<name>``; fails the test if it is not published."""
    write_partial(
        backup_dir,
        name,
        list_text=listing(dbname=dbname, created=created, add_tables=add_tables),
        revision=revision_script(None if rows is None else list(rows)),
    )
    report = backups.publish_backup(backup_dir, request(name, **overrides))
    assert report.result == "published", report.error
    return backup_dir / name


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reseal(path: Path, mutate: Callable[[dict[str, Any]], None] | None = None) -> None:
    """Rewrite the manifest (optionally changed) with matching ``files`` and ``SHA256SUMS``.

    Leaves a directory whose checksums are consistent, so only the check
    under test fails.
    """
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        {"name": file, "bytes": (path / file).stat().st_size, "sha256": _sha256(path / file)}
        for file in ("partflow.dump", "partflow.dump.list")
    ]
    if mutate is not None:
        mutate(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (path / "SHA256SUMS").write_text(
        "".join(
            f"{_sha256(path / file)}  {file}\n"
            for file in ("manifest.json", "partflow.dump", "partflow.dump.list")
        ),
        encoding="ascii",
    )


def flip_last_byte(path: Path) -> None:
    content = bytearray(path.read_bytes())
    content[-1] ^= 0xFF
    path.write_bytes(bytes(content))
