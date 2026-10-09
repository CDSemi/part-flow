"""Tests for the backup artifact commands (Phase 16 slice 5: BK-*, BV-*, BR-*, FR-*).

``backup-manifest``, ``backup-verify`` and ``backup-rotate`` run in
process (``app.cli.main``) on temporary directories only; no database is
used. I/O failures are injected by monkeypatching (the development
container runs as root, so permission bits do not fail there).

The fixtures under ``tests/fixtures/backups/`` were captured from a real
PostgreSQL 16.14 dump of a throwaway database migrated to the code head
(``partflow_test_s5_capture``, created with ``createdb``, migrated with
``uv run alembic upgrade head`` and dropped afterwards)::

    docker compose exec -T -e TZ=UTC db sh -c 'pg_dump -U "$POSTGRES_USER" \\
        -d partflow_test_s5_capture --format=custom --no-owner --no-privileges \\
        --lock-wait-timeout=60s' > capture.dump
    docker compose exec -T -e TZ=UTC db pg_restore --list < capture.dump \\
        > tests/fixtures/backups/partflow.dump.list
    docker compose exec -T -e TZ=UTC db pg_restore --data-only \\
        --table=alembic_version --file=- < capture.dump \\
        > tests/fixtures/backups/alembic_version.sql
"""

import builtins
import datetime
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app import cli
from app.application import backups
from app.infrastructure import backup_files, schema_revision
from app.infrastructure.database_privileges import TABLE_CLASSES
from tests.backup_harness import (
    HEAD,
    RELEASE,
    flip_last_byte,
    listing,
    name_of,
    publish,
    reseal,
    revision_script,
    utc,
    write_partial,
)

NAME = "20261008T020000Z-daily"


@pytest.fixture(autouse=True)
def _known_code_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real head, read once: loading the Alembic scripts per publish is slow."""
    monkeypatch.setattr(schema_revision, "code_head", lambda: HEAD)


_MANIFEST_KEYS = [
    "manifest_version",
    "name",
    "kind",
    "label",
    "environment",
    "host",
    "operator",
    "reason",
    "dump_started_at",
    "completed_at",
    "release",
    "tool",
    "images",
    "alembic_revision",
    "alembic_rows",
    "database",
    "dump",
    "files",
]
_MANIFEST_ARGUMENTS = [
    "--operator",
    "scheduler",
    "--reason",
    "scheduled daily backup",
    "--release-tag",
    RELEASE,
    "--release-commit",
    "a" * 40,
    "--expected-revision",
    HEAD,
    "--host",
    "nas01",
    "--image-backend",
    "sha256:" + "b" * 64,
    "--image-web",
    "sha256:" + "c" * 64,
    "--image-db",
    "",
]


def _run(capsys: pytest.CaptureFixture[str], *arguments: str) -> tuple[int, dict[str, Any], str]:
    code = cli.main(list(arguments))
    out, err = capsys.readouterr()
    # Exactly one document, printed with indent=2.
    assert out.splitlines().count(f'  "exit_code": {code},') == 1, out
    assert out.startswith("{\n") and out.endswith("\n}\n")
    return code, json.loads(out), err


def _manifest(capsys: pytest.CaptureFixture[str], backup_dir: Path, name: str = NAME) -> Any:
    return _run(
        capsys,
        "backup-manifest",
        "--name",
        name,
        *_MANIFEST_ARGUMENTS,
        "--backup-dir",
        str(backup_dir),
    )


def _verify(
    capsys: pytest.CaptureFixture[str], backup_dir: Path, name: str = NAME, *extra: str
) -> tuple[int, dict[str, Any], str]:
    return _run(capsys, "backup-verify", name, "--backup-dir", str(backup_dir), *extra)


def _check(document: dict[str, Any], check_id: str) -> dict[str, Any]:
    return next(check for check in document["checks"] if check["id"] == check_id)


def _statuses(document: dict[str, Any]) -> dict[str, str]:
    return {check["id"]: check["status"] for check in document["checks"]}


# ---------------------------------------------------------------------------
# BK: backup-manifest
# ---------------------------------------------------------------------------


def test_a_valid_partial_is_published(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BK-1."""
    monkeypatch.setenv("RELEASE_TAG", "v1.0.0-rc.2")
    monkeypatch.setenv("RELEASE_COMMIT", "e" * 40)
    write_partial(tmp_path, NAME, list_text=listing(created=utc(2026, 10, 8, 2, 0)))
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 0, err
    published = tmp_path / NAME
    assert document == {
        "report_version": 1,
        "command": "backup-manifest",
        "result": "published",
        "exit_code": 0,
        "name": NAME,
        "path": str(published),
        "manifest_sha256": document["manifest_sha256"],
        "alembic_revision": HEAD,
        "dump_started_at": "2026-10-08T02:00:00Z",
        "dump_bytes": (published / "partflow.dump").stat().st_size,
        "warnings": [],
        "error": None,
    }
    assert f"backup-manifest: published {NAME} (" in err
    assert sorted(os.listdir(published)) == [
        "SHA256SUMS",
        "manifest.json",
        "partflow.dump",
        "partflow.dump.list",
    ]
    assert stat.S_IMODE(published.stat().st_mode) == 0o700
    for entry in published.iterdir():
        assert stat.S_IMODE(entry.stat().st_mode) == 0o600, entry.name
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert list(manifest) == _MANIFEST_KEYS
    assert manifest["manifest_version"] == 1
    assert (manifest["kind"], manifest["label"], manifest["environment"]) == (
        "daily",
        None,
        "production",
    )
    assert manifest["release"] == {"tag": RELEASE, "commit": "a" * 40, "expected_revision": HEAD}
    assert manifest["tool"] == {"tag": "v1.0.0-rc.2", "commit": "e" * 40, "code_head": HEAD}
    assert manifest["images"] == {
        "backend": "sha256:" + "b" * 64,
        "web": "sha256:" + "c" * 64,
        "db": None,
    }
    assert manifest["alembic_revision"] == HEAD
    assert manifest["alembic_rows"] == [HEAD]
    assert manifest["database"] == {
        "name": "partflow",
        "server_version": "16.14 (Debian 16.14-1.pgdg13+1)",
        "server_major": 16,
        "pg_dump_version": "16.14 (Debian 16.14-1.pgdg13+1)",
    }
    assert manifest["dump"] == {
        "format": "custom",
        "options": [
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--lock-wait-timeout=60s",
        ],
        "toc_entries": 255,
        "table_data_entries": len(TABLE_CLASSES),
        "tables_checked": True,
        "extra_tables": [],
    }
    assert [entry["name"] for entry in manifest["files"]] == ["partflow.dump", "partflow.dump.list"]
    sums = (published / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert [line.split("  ")[1] for line in sums] == [
        "manifest.json",
        "partflow.dump",
        "partflow.dump.list",
    ]
    check = subprocess.run(
        ["sha256sum", "-c", "SHA256SUMS"], cwd=published, capture_output=True, text=True
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert sorted(os.listdir(tmp_path / ".partial")) == []
    assert not (published / "alembic_version.sql").exists()


@pytest.mark.parametrize("dump", [b"", b"PGDMX not a dump"])
def test_an_invalid_dump_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], dump: bytes
) -> None:
    """BK-2."""
    partial = write_partial(tmp_path, NAME, dump=dump)
    before = sorted(os.listdir(partial))
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 1
    assert document["result"] == "refused"
    assert document["path"] is None
    assert document["error"] == {
        "code": "dump_invalid",
        "message": (
            f"The dump of {NAME} is empty or is not a PostgreSQL custom-format archive. Nothing"
            " was published."
        ),
    }
    assert sorted(os.listdir(tmp_path)) == [".partial"]
    assert sorted(os.listdir(partial)) == before


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (listing(without=["Archive created at"]), 'no "Archive created at … UTC" line'),
        (listing(zone="CEST"), 'no "Archive created at … UTC" line'),
        (listing(archive_format="TAR"), "format is not CUSTOM"),
        (listing(without=["dbname"]), "no dbname"),
        (listing(toc_entries="0"), "no TOC entries"),
        (listing(without=["Dumped from database version"]), "no server version"),
    ],
)
def test_an_unreadable_listing_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, problem: str
) -> None:
    """BK-3."""
    write_partial(tmp_path, NAME, list_text=text)
    code, document, _ = _manifest(capsys, tmp_path)
    assert code == 1
    assert document["error"] == {
        "code": "list_invalid",
        "message": (
            f"The table of contents of {NAME} cannot be read ({problem}). Nothing was published."
        ),
    }
    assert not (tmp_path / NAME).exists()


def test_the_alembic_version_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BK-4: no COPY block = an empty database; a block without its end marker is refused."""
    write_partial(tmp_path, NAME, revision=revision_script(None))
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 0, err
    assert (document["alembic_revision"], document["warnings"]) == (None, [])
    manifest = json.loads((tmp_path / NAME / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["alembic_revision"], manifest["alembic_rows"]) == (None, None)
    assert manifest["dump"]["tables_checked"] is False
    assert "revision none)" in err

    other = "20261008T030000Z-daily"
    write_partial(tmp_path, other, revision=revision_script([HEAD], terminated=False))
    code, document, _ = _manifest(capsys, tmp_path, other)
    assert code == 1
    assert document["error"] == {
        "code": "revision_invalid",
        "message": (
            f"The alembic_version data in {other} cannot be parsed (the COPY block has no end"
            " marker). Nothing was published."
        ),
    }


@pytest.mark.parametrize(
    ("rows", "expected_rows", "warning"),
    [
        ([HEAD, "0031_phase14_beyond_demand"], [HEAD, "0031_phase14_beyond_demand"], None),
        ([], [], "alembic_version holds 0 rows ()"),
        (["bad-rev!"], ["bad-rev!"], "alembic_version holds 1 rows (bad-rev!)"),
        (["x\x01" + "y" * 80], ["x?" + "y" * 62], None),
    ],
)
def test_an_anomalous_alembic_version_is_published_with_a_warning(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    rows: list[str],
    expected_rows: list[str],
    warning: str | None,
) -> None:
    """BK-4b: a faithful backup of a drifted database is still published."""
    write_partial(tmp_path, NAME, revision=revision_script(rows))
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 0, err
    assert document["result"] == "published"
    assert document["alembic_revision"] is None
    expected_warning = warning or (
        f"alembic_version holds {len(rows)} rows ({', '.join(expected_rows)})"
    )
    assert document["warnings"] == [expected_warning]
    assert ", 1 warnings" in err
    manifest = json.loads((tmp_path / NAME / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["alembic_revision"], manifest["alembic_rows"]) == (None, expected_rows)


def test_a_missing_table_at_the_head_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BK-5."""
    write_partial(tmp_path, NAME, list_text=listing(drop_tables=["part_movements"]))
    code, document, _ = _manifest(capsys, tmp_path)
    assert code == 1
    assert document["error"] == {
        "code": "tables_incomplete",
        "message": (
            f"The dump of {NAME} has no data entry for part_movements, which this release"
            f" expects at revision {HEAD}. Nothing was published."
        ),
    }


def test_an_extra_table_is_published_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BK-5b."""
    write_partial(tmp_path, NAME, list_text=listing(add_tables=["s4_extra"]))
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 0, err
    assert document["warnings"] == ["extra tables in the dump: s4_extra"]
    manifest = json.loads((tmp_path / NAME / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dump"]["tables_checked"] is True
    assert manifest["dump"]["extra_tables"] == ["s4_extra"]
    assert manifest["dump"]["table_data_entries"] == len(TABLE_CLASSES) + 1
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 0
    assert document["extra_tables"] == ["s4_extra"]


def test_another_revision_is_not_checked_for_completeness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BK-6."""
    write_partial(
        tmp_path,
        NAME,
        list_text=listing(drop_tables=["part_movements"]),
        revision=revision_script(["0031_phase14_beyond_demand"]),
    )
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 0, err
    manifest = json.loads((tmp_path / NAME / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["alembic_revision"] == "0031_phase14_beyond_demand"
    assert manifest["dump"]["tables_checked"] is False
    assert manifest["dump"]["extra_tables"] == []


def test_an_existing_name_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BK-7."""
    existing = publish(tmp_path, NAME)
    before = {entry.name: entry.read_bytes() for entry in existing.iterdir()}
    write_partial(tmp_path, NAME)
    code, document, _ = _manifest(capsys, tmp_path)
    assert code == 1
    assert document["error"] == {
        "code": "name_exists",
        "message": f"A backup named {NAME} already exists. Nothing was published.",
    }
    assert {entry.name: entry.read_bytes() for entry in existing.iterdir()} == before


def test_a_failed_publish_rename_publishes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BK-8."""
    write_partial(tmp_path, NAME)
    real_rename = os.rename

    def rename(source: Any, target: Any) -> None:
        if Path(target) == tmp_path / NAME:
            raise PermissionError(13, "Permission denied")
        real_rename(source, target)

    monkeypatch.setattr(os, "rename", rename)
    code, document, _ = _manifest(capsys, tmp_path)
    assert code == 2
    assert document["result"] == "failed"
    assert document["path"] is None
    assert document["error"] == {
        "code": "io_error",
        "message": (
            f"The backup files of {NAME} could not be read or written (Permission denied)."
            " Nothing was published."
        ),
    }
    assert sorted(os.listdir(tmp_path)) == [".partial"]
    assert sorted(os.listdir(tmp_path / ".partial")) == [f"{NAME}.publish"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--name", "20261008T020000Z-daily-v1"],
        ["--name", "20261008T020000Z-pre-release"],
        ["--name", "20261399T020000Z-daily"],
        ["--name", "../20261008T020000Z-daily"],
        ["--name", NAME, "--operator", 'a "quoted" name'],
        ["--name", NAME, "--reason", "back\\slash"],
        ["--name", NAME, "--release-commit", "ABC"],
        ["--name", NAME, "--image-db", "sha256:short"],
    ],
)
def test_option_errors_print_no_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    """BK-9."""
    with pytest.raises(SystemExit) as usage:
        cli.main(
            ["backup-manifest", *_MANIFEST_ARGUMENTS, *arguments, "--backup-dir", str(tmp_path)]
        )
    assert usage.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err


def test_backup_manifest_reads_no_database_setting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BK-10."""
    for key in [key for key in os.environ if key.startswith("DATABASE_")]:
        monkeypatch.delenv(key)

    def no_settings() -> None:
        raise AssertionError("backup-manifest must not read Settings")

    monkeypatch.setattr(cli, "get_settings", no_settings)
    write_partial(tmp_path, NAME)
    code, document, err = _manifest(capsys, tmp_path)
    assert code == 0, err
    assert document["result"] == "published"


# ---------------------------------------------------------------------------
# BV: backup-verify
# ---------------------------------------------------------------------------


def test_a_published_backup_verifies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BV-1."""
    path = publish(tmp_path, NAME)
    code = cli.main(["backup-verify", NAME, "--backup-dir", str(tmp_path)])
    out, err = capsys.readouterr()
    assert code == 0, err
    document = json.loads(out)
    assert list(document) == [
        "report_version",
        "command",
        "result",
        "exit_code",
        "name",
        "kind",
        "release_tag",
        "alembic_revision",
        "database_name",
        "dump_started_at",
        "dump_bytes",
        "manifest_sha256",
        "release_matches_revision",
        "checks",
        "extra_entries",
        "extra_tables",
        "alembic_rows",
        "error",
    ]
    assert document["result"] == "verified"
    assert _statuses(document) == {
        "directory": "pass",
        "files": "pass",
        "sha256sums": "pass",
        "manifest": "pass",
        "dump_header": "pass",
        "list": "pass",
        "expect_database": "skipped",
    }
    assert document["kind"] == "daily"
    assert document["database_name"] == "partflow"
    assert document["dump_bytes"] == (path / "partflow.dump").stat().st_size
    assert document["alembic_rows"] == [HEAD]
    assert document["error"] is None
    # The host scripts read these as indent-2 lines.
    assert f'  "release_tag": "{RELEASE}",' in out.splitlines()
    assert '  "release_matches_revision": true,' in out.splitlines()
    assert f'  "alembic_revision": "{HEAD}",' in out.splitlines()
    assert err.strip() == (
        f"backup-verify: {NAME} verified ({document['dump_bytes']} bytes, revision {HEAD},"
        " started 2026-10-08T02:00:00Z)"
    )


@pytest.mark.parametrize(
    ("expected_revision", "matches"), [("0031_phase14_beyond_demand", False), (None, None)]
)
def test_release_matches_revision(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    expected_revision: str | None,
    matches: bool | None,
) -> None:
    """BV-1b."""
    publish(tmp_path, NAME, expected_revision=expected_revision)
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 0
    assert document["release_matches_revision"] is matches


def test_a_flipped_dump_byte_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BV-2."""
    flip_last_byte(publish(tmp_path, NAME) / "partflow.dump")
    code, document, err = _verify(capsys, tmp_path)
    assert code == 1
    assert document["result"] == "invalid"
    assert _check(document, "sha256sums") == {
        "id": "sha256sums",
        "status": "fail",
        "detail": "partflow.dump: sha256 differs from SHA256SUMS",
    }
    assert document["error"] == {
        "code": "backup_invalid",
        "message": (
            f"The backup {NAME} failed verification (sha256sums: partflow.dump: sha256 differs"
            " from SHA256SUMS). Do not use it."
        ),
    }
    assert err.strip() == f"backup-verify: {document['error']['message']}"


@pytest.mark.parametrize("edit", ["edit_line", "fourth_line", "revision_line"])
def test_an_edited_checksum_file_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], edit: str
) -> None:
    """BV-3."""
    sums = publish(tmp_path, NAME) / "SHA256SUMS"
    lines = sums.read_text(encoding="ascii").splitlines(keepends=True)
    if edit == "edit_line":
        lines[1] = "0" * 64 + "  partflow.dump\n"
    elif edit == "fourth_line":
        lines.append("0" * 64 + "  extra.txt\n")
    else:
        lines[0] = "0" * 64 + "  alembic_version.sql\n"
    sums.write_text("".join(lines), encoding="ascii")
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 1
    assert _check(document, "sha256sums")["status"] == "fail"


def test_an_edited_manifest_fails_its_checksum(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BV-4."""
    path = publish(tmp_path, NAME) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["operator"] = "someone else"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 1
    assert _check(document, "sha256sums") == {
        "id": "sha256sums",
        "status": "fail",
        "detail": "manifest.json: sha256 differs from SHA256SUMS",
    }


def test_a_missing_listing_fails_the_files_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BV-5."""
    (publish(tmp_path, NAME) / "partflow.dump.list").unlink()
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 1
    assert _statuses(document) == {
        "directory": "pass",
        "files": "fail",
        "sha256sums": "not_run",
        "manifest": "not_run",
        "dump_header": "not_run",
        "list": "not_run",
        "expect_database": "not_run",
    }
    assert _check(document, "files")["detail"].startswith("partflow.dump.list: ")


def _set(key: str, value: Any) -> Any:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest[key] = value

    return mutate


def _drop(key: str) -> Any:
    def mutate(manifest: dict[str, Any]) -> None:
        del manifest[key]

    return mutate


def _wrong_bytes(manifest: dict[str, Any]) -> None:
    manifest["files"][0]["bytes"] += 1


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (_set("manifest_version", 2), "manifest.json: manifest_version is not 1"),
        (_drop("reason"), "manifest.json: reason is missing"),
        (
            _set("name", "20261008T030000Z-daily"),
            "manifest.json: name differs from the directory name",
        ),
        (_set("kind", "manual"), "manifest.json: kind or label differs from the directory name"),
        (_wrong_bytes, "manifest.json: files do not match partflow.dump and partflow.dump.list"),
        (_set("dump_started_at", "2026-10-08 02:00"), "manifest.json: dump_started_at is invalid"),
    ],
)
def test_an_invalid_manifest_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate: Any, detail: str
) -> None:
    """BV-6: consistent checksums, so only the manifest check fails."""
    reseal(publish(tmp_path, NAME), mutate)
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 1
    assert _check(document, "sha256sums")["status"] == "pass"
    assert _check(document, "manifest") == {"id": "manifest", "status": "fail", "detail": detail}


def test_directory_and_file_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BV-7."""
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 1
    assert _check(document, "directory") == {
        "id": "directory",
        "status": "fail",
        "detail": "not found",
    }
    assert document["error"]["message"] == (
        f"The backup {NAME} failed verification (directory: not found). Do not use it."
    )
    assert _statuses(document)["files"] == "not_run"

    published = publish(tmp_path / "real", NAME)
    staging = tmp_path / "staging" / ".partial"
    staging.mkdir(parents=True)
    shutil.copytree(published, staging / NAME)
    code, document, _ = _verify(capsys, staging)
    assert code == 1
    assert _check(document, "directory")["detail"] == "is inside .partial/ (not published)"

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / NAME).symlink_to(published, target_is_directory=True)
    code, document, _ = _verify(capsys, linked)
    assert code == 1
    assert _check(document, "directory")["detail"] == "is a symbolic link"

    dump = published / "partflow.dump"
    moved = tmp_path / "partflow.dump"
    dump.rename(moved)
    dump.symlink_to(moved)
    code, document, _ = _verify(capsys, tmp_path / "real")
    assert code == 1
    assert _check(document, "directory")["status"] == "pass"
    assert _check(document, "files")["status"] == "fail"


def test_unknown_entries_are_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BV-8."""
    (publish(tmp_path, NAME) / "@eaDir").mkdir()
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 0
    assert document["extra_entries"] == ["@eaDir"]


def test_expect_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BV-9."""
    publish(tmp_path, NAME)
    code, document, _ = _verify(capsys, tmp_path, NAME, "--expect-database", "partflow")
    assert (code, _check(document, "expect_database")["status"]) == (0, "pass")
    code, document, _ = _verify(capsys, tmp_path, NAME, "--expect-database", "other")
    assert code == 1
    assert _check(document, "expect_database") == {
        "id": "expect_database",
        "status": "fail",
        "detail": "manifest.json: database partflow, not other",
    }


def _unreadable(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    real_open = builtins.open

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, str | os.PathLike) and Path(file) == target:
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)


def test_an_unreadable_dump_cannot_be_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BV-10."""
    path = publish(tmp_path, NAME)
    _unreadable(monkeypatch, path / "partflow.dump")
    code, document, err = _verify(capsys, tmp_path)
    assert code == 2
    assert document["result"] == "failed"
    assert document["error"] == {
        "code": "io_error",
        "message": f"The backup {NAME} could not be read (Permission denied).",
    }
    assert _statuses(document)["files"] == "pass"
    assert _statuses(document)["sha256sums"] == "not_run"


def test_an_unsearchable_backup_directory_is_not_reported_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BV-10c: the 0700 directory of another account is "could not read", never "not found"."""
    path = publish(tmp_path, NAME)
    real_lstat = os.lstat

    def lstat(target: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(target) == path:
            raise PermissionError(13, "Permission denied", str(target))
        return real_lstat(target, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 2
    assert document["error"] == {
        "code": "io_error",
        "message": f"The backup {NAME} could not be read (Permission denied).",
    }
    assert _statuses(document)["directory"] == "not_run"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files without permission bits")
def test_an_unreadable_dump_by_permission_bits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BV-10b."""
    dump = publish(tmp_path, NAME) / "partflow.dump"
    dump.chmod(0)
    try:
        code, document, _ = _verify(capsys, tmp_path)
    finally:
        dump.chmod(0o600)
    assert code == 2
    assert document["error"]["code"] == "io_error"


def test_a_listing_of_another_database_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BV-11."""
    path = publish(tmp_path, NAME)
    (path / "partflow.dump.list").write_text(listing(dbname="other"), encoding="utf-8")
    reseal(path)
    code, document, _ = _verify(capsys, tmp_path)
    assert code == 1
    assert _check(document, "list") == {
        "id": "list",
        "status": "fail",
        "detail": "partflow.dump.list: dbname differs from the manifest",
    }


def test_verification_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BV-12."""
    path = publish(tmp_path, NAME)
    flip_last_byte(path / "partflow.dump")

    def state() -> list[tuple[str, int, int]]:
        return sorted(
            (str(entry.relative_to(tmp_path)), entry.stat().st_mtime_ns, entry.stat().st_mode)
            for entry in tmp_path.rglob("*")
        )

    before = state()
    _verify(capsys, tmp_path)
    _verify(capsys, tmp_path, NAME, "--expect-database", "other")
    assert state() == before


# ---------------------------------------------------------------------------
# BR: backup-rotate
# ---------------------------------------------------------------------------


def _dailies(backup_dir: Path, first: datetime.datetime, days: int) -> list[str]:
    names = []
    for offset in range(days):
        name = name_of(first + datetime.timedelta(days=offset))
        publish(backup_dir, name)
        names.append(name)
    return names


def _rotate(
    capsys: pytest.CaptureFixture[str], backup_dir: Path, daily: int, weekly: int, *extra: str
) -> tuple[int, dict[str, Any], str]:
    return _run(
        capsys,
        "backup-rotate",
        "--keep-daily",
        str(daily),
        "--keep-weekly",
        str(weekly),
        "--backup-dir",
        str(backup_dir),
        *extra,
    )


def _root(backup_dir: Path) -> list[str]:
    return sorted(entry for entry in os.listdir(backup_dir) if entry != ".partial")


def test_rotation_keeps_the_newest_dailies_and_one_per_week(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BR-1: 2026-09-01 (Tuesday) to 2026-09-20; ISO weeks 36, 37, 38."""
    names = _dailies(tmp_path, utc(2026, 9, 1), 20)
    code, document, err = _rotate(capsys, tmp_path, 14, 8)
    assert code == 0, err
    expected = sorted(names[5:])  # 09-06 (newest of week 36) and the 14 newest
    assert document["result"] == "rotated"
    assert document["kept"] == expected
    assert document["deleted"] == sorted(names[:5])
    assert (document["skipped"], document["invalid"], document["error"]) == ([], [], None)
    assert _root(tmp_path) == expected
    assert err.strip() == "backup-rotate: kept 15 daily backups, deleted 5"
    # Converges: a rerun deletes nothing.
    code, document, _ = _rotate(capsys, tmp_path, 14, 8)
    assert (code, document["deleted"]) == (0, [])


def test_weeks_overlap_the_daily_window(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BR-1b: 84 dailies from Monday 2026-06-01 (exactly 12 ISO weeks)."""
    names = _dailies(tmp_path, utc(2026, 6, 1), 84)
    code, document, err = _rotate(capsys, tmp_path, 14, 8)
    assert code == 0, err
    sundays = [name_of(utc(2026, 6, 7) + datetime.timedelta(weeks=week)) for week in range(12)]
    expected = set(names[-14:]) | set(sundays[-8:])
    assert document["kept"] == sorted(expected)
    assert len(expected) == 20
    # The "8 weeks beyond the daily window" reading would also keep these.
    for absent in sundays[:4]:
        assert absent not in document["kept"]
        assert absent in document["deleted"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--keep-weekly", "8"],
        ["--keep-daily", "14"],
        ["--keep-daily", "0", "--keep-weekly", "8"],
        ["--keep-daily", "14", "--keep-weekly", "-1"],
    ],
)
def test_rotation_needs_both_keep_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    """BR-1c, BR-5: no code default; --keep-daily is at least 1."""
    with pytest.raises(SystemExit) as usage:
        cli.main(["backup-rotate", *arguments, "--backup-dir", str(tmp_path)])
    assert usage.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage:" in err


def test_iso_weeks_across_a_year_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BR-2: 2026-12-28 to 2027-01-03 is 2026-W53."""
    names = _dailies(tmp_path, utc(2026, 12, 26), 16)  # 2026-12-26 .. 2027-01-10
    code, document, _ = _rotate(capsys, tmp_path, 1, 3)
    assert code == 0
    assert document["kept"] == sorted(
        [name_of(utc(2027, 1, 10)), name_of(utc(2027, 1, 3)), name_of(utc(2026, 12, 27))]
    )
    assert len(document["deleted"]) == len(names) - 3


def test_other_entries_are_never_deleted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BR-3."""
    _dailies(tmp_path, utc(2026, 9, 1), 3)
    pre_release = name_of(utc(2026, 8, 1), "pre-release", "v1.0.0-rc.1")
    manual = name_of(utc(2026, 8, 2), "manual")
    publish(tmp_path, pre_release)
    publish(tmp_path, manual)
    disagreeing = name_of(utc(2026, 8, 3))
    reseal(publish(tmp_path, disagreeing), _set("kind", "manual"))
    unreadable = name_of(utc(2026, 8, 4))
    (publish(tmp_path, unreadable) / "manifest.json").write_text("{not json", encoding="utf-8")
    for directory in ("notes", "archive", ".backup.lock"):
        (tmp_path / directory).mkdir()
    code, document, _ = _rotate(capsys, tmp_path, 1, 0)
    assert code == 0
    assert document["kept"] == [name_of(utc(2026, 9, 3))]
    assert document["deleted"] == [name_of(utc(2026, 9, 1)), name_of(utc(2026, 9, 2))]
    assert document["skipped"] == [
        {"name": disagreeing, "reason": "manifest disagrees with the name"},
        {"name": unreadable, "reason": "manifest unreadable"},
    ]
    for kept in (pre_release, manual, disagreeing, unreadable, "notes", "archive", ".backup.lock"):
        assert (tmp_path / kept).exists(), kept


def test_a_dry_run_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """BR-4."""
    names = _dailies(tmp_path, utc(2026, 9, 1), 4)
    old_partial = tmp_path / ".partial" / name_of(utc(2026, 8, 1))
    old_partial.mkdir()
    code, document, err = _rotate(capsys, tmp_path, 2, 0, "--dry-run")
    assert code == 0
    assert document["dry_run"] is True
    assert document["deleted"] == names[:2]
    assert document["partial_removed"] == [old_partial.name]
    assert _root(tmp_path) == names
    assert old_partial.exists()
    assert err.strip().endswith("(dry run)")


def test_stale_partial_entries_are_removed(tmp_path: Path) -> None:
    """BR-6."""
    now = utc(2026, 10, 8, 12)
    publish(tmp_path, name_of(utc(2026, 10, 8)))
    partial = tmp_path / ".partial"
    old = partial / name_of(now - datetime.timedelta(hours=25))
    old_publish = partial / f"{name_of(now - datetime.timedelta(hours=26))}.publish"
    young = partial / name_of(now - datetime.timedelta(hours=1))
    for entry in (old, old_publish, young):
        entry.mkdir()
        (entry / "partflow.dump").write_bytes(b"PGDMP")
    report = backups.rotate_backups(tmp_path, keep_daily=1, keep_weekly=0, now=now)
    assert report.result == "rotated"
    assert report.partial_removed == sorted([old.name, old_publish.name])
    assert not old.exists() and not old_publish.exists()
    assert young.exists()


def test_an_interrupted_removal_leaves_no_half_backup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BR-7."""
    names = _dailies(tmp_path, utc(2026, 9, 1), 2)

    def interrupted(path: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError(5, "Input/output error")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", interrupted)
        code, document, _ = _rotate(capsys, tmp_path, 1, 0)
    assert code == 2
    assert document["error"]["code"] == "delete_failed"
    assert _root(tmp_path) == [names[1]]
    assert (tmp_path / ".partial" / f"{names[0]}.delete").exists()
    code, document, _ = _rotate(capsys, tmp_path, 1, 0)
    assert code == 0
    assert document["partial_removed"] == [f"{names[0]}.delete"]
    assert os.listdir(tmp_path / ".partial") == []


def test_a_failed_deletion_keeps_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BR-8."""
    names = _dailies(tmp_path, utc(2026, 9, 1), 3)
    real_rename = os.rename

    def rename(source: Any, target: Any) -> None:
        if str(target).endswith(".delete"):
            raise PermissionError(13, "Permission denied")
        real_rename(source, target)

    monkeypatch.setattr(os, "rename", rename)
    code, document, _ = _rotate(capsys, tmp_path, 1, 0)
    assert code == 2
    assert document["result"] == "failed"
    assert document["kept"] == [names[2]]
    assert document["deleted"] == []
    assert document["error"] == {
        "code": "delete_failed",
        "message": (
            f"Some backups could not be removed ({names[0]}, {names[1]}: Permission denied)."
            " The kept backups are unchanged."
        ),
    }
    assert _root(tmp_path) == names


def test_invalid_dailies_are_kept_and_not_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """BR-9: 2026-09-01 (Tuesday) to 2026-09-20; the newest daily of week 37 is corrupted."""
    names = _dailies(tmp_path, utc(2026, 9, 1), 20)
    corrupted = name_of(utc(2026, 9, 13))
    flip_last_byte(tmp_path / corrupted / "partflow.dump")
    edited = name_of(utc(2026, 9, 20))
    manifest = tmp_path / edited / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("nas01", "nas02"))
    code, document, err = _rotate(capsys, tmp_path, 14, 8)
    assert code == 1
    assert document["result"] == "rotated_with_invalid"
    assert document["invalid"] == [
        {
            "name": corrupted,
            "check": "sha256sums",
            "detail": "partflow.dump: sha256 differs from SHA256SUMS",
        },
        {
            "name": edited,
            "check": "sha256sums",
            "detail": "manifest.json: sha256 differs from SHA256SUMS",
        },
    ]
    verified = [name for name in names if name not in (corrupted, edited)]
    newest = set(verified[-14:])
    # Week 37 (09-07 .. 09-13): 09-12 replaces the corrupted 09-13 as its representative.
    assert name_of(utc(2026, 9, 12)) in newest
    assert document["kept"] == sorted(newest | {name_of(utc(2026, 9, 6))})
    assert corrupted not in document["deleted"] and edited not in document["deleted"]
    assert (tmp_path / corrupted).exists() and (tmp_path / edited).exists()
    assert document["error"]["message"] == (
        f"Daily backups {corrupted}, {edited} failed verification; they were not counted or"
        " deleted. Review and remove them by hand (OPERATIONS_RUNBOOK §9)."
    )
    assert err.strip().endswith(", 2 failed verification and were kept for review")
    for name in document["kept"]:
        assert (tmp_path / name).exists()


def test_unreadable_dailies_are_kept_and_not_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """BR-10 (P16-S5 audit): a daily that verification cannot read is invalid, never deleted."""
    names = _dailies(tmp_path, utc(2026, 9, 1), 5)
    oldest, newest = names[0], names[-1]
    unreadable = {tmp_path / oldest / "partflow.dump", tmp_path / newest / "partflow.dump"}
    real_digest = backup_files.digest

    def digest(path: Path) -> Any:
        if Path(path) in unreadable:
            raise PermissionError(13, "Permission denied", str(path))
        return real_digest(path)

    monkeypatch.setattr(backup_files, "digest", digest)
    code, document, _ = _rotate(capsys, tmp_path, 1, 0)
    assert code == 1
    assert document["result"] == "rotated_with_invalid"
    assert document["invalid"] == [
        {"name": oldest, "check": "sha256sums", "detail": "cannot be read (Permission denied)"},
        {"name": newest, "check": "sha256sums", "detail": "cannot be read (Permission denied)"},
    ]
    # Not counted: the newest readable daily is kept; neither unreadable daily is deleted.
    assert document["kept"] == [names[3]]
    assert document["deleted"] == [names[1], names[2]]
    assert _root(tmp_path) == [oldest, names[3], newest]


# ---------------------------------------------------------------------------
# FR: freshness
# ---------------------------------------------------------------------------

_STARTED = utc(2026, 10, 8, 10, 0)


def test_not_before_equality_passes() -> None:
    """FR-1."""
    freshness = backups.check_freshness(
        _STARTED, not_before=_STARTED, max_age_minutes=60, now=_STARTED
    )
    assert freshness.document() == {"rule": "not_before", "not_before": "2026-10-08T10:00:00Z"}


def test_one_second_before_not_before_is_stale() -> None:
    """FR-2."""
    with pytest.raises(backups.StaleBackupError):
        backups.check_freshness(
            _STARTED,
            not_before=_STARTED + datetime.timedelta(seconds=1),
            max_age_minutes=60,
            now=_STARTED,
        )


def test_the_age_limit() -> None:
    """FR-3."""
    freshness = backups.check_freshness(
        _STARTED,
        not_before=None,
        max_age_minutes=60,
        now=_STARTED + datetime.timedelta(minutes=60),
    )
    assert freshness.document() == {"rule": "max_age", "max_age_minutes": 60, "age_minutes": 60}
    with pytest.raises(backups.StaleBackupError) as stale:
        backups.check_freshness(
            _STARTED,
            not_before=None,
            max_age_minutes=60,
            now=_STARTED + datetime.timedelta(minutes=61),
        )
    assert stale.value.freshness.age_minutes == 61


def test_a_future_stamp_is_stale() -> None:
    """FR-4: two minutes of clock tolerance."""
    backups.check_freshness(
        _STARTED,
        not_before=None,
        max_age_minutes=60,
        now=_STARTED - datetime.timedelta(minutes=2),
    )
    with pytest.raises(backups.StaleBackupError):
        backups.check_freshness(
            _STARTED,
            not_before=None,
            max_age_minutes=60,
            now=_STARTED - datetime.timedelta(minutes=3),
        )
