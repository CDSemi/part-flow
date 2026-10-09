"""Backup artifact file handling (Phase 16 slice 5; CD7).

Infrastructure only: streaming SHA-256, the ``pg_restore --list`` header
and table-of-contents parser, the ``alembic_version`` COPY-block parser,
directory scans, durable writes, the atomic publish (fsync + rename) and
the two-step delete. No backup rule lives here (``app.application.backups``).
Nothing here connects to a database.
"""

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: Files are hashed in blocks of this size; nothing is loaded whole.
BLOCK_SIZE: Final = 1024 * 1024
CUSTOM_ARCHIVE_MAGIC: Final = b"PGDMP"

_ARCHIVE_CREATED: Final = re.compile(
    r"^;\s+Archive created at (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\S+)\s*$"
)
_HEADER_FIELD: Final = re.compile(r"^;\s+([A-Za-z][A-Za-z_ ]*[A-Za-z]):\s?(.*?)\s*$")
_TABLE_DATA: Final = re.compile(r"^\d+; \d+ \d+ TABLE DATA public (\S+) \S+\s*$")
_ALEMBIC_COPY: Final = "COPY public.alembic_version (version_num) FROM stdin;"
_COPY_END: Final = "\\."


@dataclass(frozen=True)
class FileDigest:
    size: int
    sha256: str


@dataclass(frozen=True)
class TocListing:
    """The parsed ``pg_restore --list`` output; None = the header line is absent."""

    archive_created: str | None = None
    archive_zone: str | None = None
    dbname: str | None = None
    toc_entries: int | None = None
    archive_format: str | None = None
    server_version: str | None = None
    pg_dump_version: str | None = None
    table_data: list[str] = field(default_factory=list)


class CopyBlockError(Exception):
    """The ``alembic_version`` COPY block has no ``\\.`` end marker."""


def digest(path: Path) -> FileDigest:
    """Size and SHA-256 of one file, read in ``BLOCK_SIZE`` blocks."""
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while block := stream.read(BLOCK_SIZE):
            hasher.update(block)
            size += len(block)
    return FileDigest(size, hasher.hexdigest())


def read_prefix(path: Path, length: int) -> bytes:
    with open(path, "rb") as stream:
        return stream.read(length)


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as stream:
        return stream.read()


def _lines(text: str) -> list[str]:
    """Lines split at LF only (``str.splitlines`` also splits at other control characters)."""
    return [line.removesuffix("\r") for line in text.split("\n")]


def parse_toc_listing(listing: str) -> TocListing:
    """The header fields and the ``TABLE DATA public`` entries of a listing."""
    created: str | None = None
    zone: str | None = None
    fields: dict[str, str] = {}
    tables: list[str] = []
    for line in _lines(listing):
        if line.startswith(";"):
            created_match = _ARCHIVE_CREATED.match(line)
            if created_match is not None:
                if created is None:
                    created, zone = created_match.group(1), created_match.group(2)
                continue
            field_match = _HEADER_FIELD.match(line)
            if field_match is not None:
                fields.setdefault(field_match.group(1), field_match.group(2))
            continue
        table_match = _TABLE_DATA.match(line)
        if table_match is not None:
            tables.append(table_match.group(1))
    toc_entries: int | None = None
    if fields.get("TOC Entries", "").isdigit():
        toc_entries = int(fields["TOC Entries"])
    return TocListing(
        archive_created=created,
        archive_zone=zone,
        dbname=fields.get("dbname") or None,
        toc_entries=toc_entries,
        archive_format=fields.get("Format") or None,
        server_version=fields.get("Dumped from database version") or None,
        pg_dump_version=fields.get("Dumped by pg_dump version") or None,
        table_data=tables,
    )


def parse_alembic_copy(script: str) -> list[str] | None:
    """The rows of the ``alembic_version`` COPY block; None = no COPY block.

    Raises ``CopyBlockError`` when the block has no end marker.
    """
    lines = _lines(script)
    try:
        start = lines.index(_ALEMBIC_COPY)
    except ValueError:
        return None
    rows: list[str] = []
    for line in lines[start + 1 :]:
        if line == _COPY_END:
            return rows
        rows.append(line)
    raise CopyBlockError("the COPY block has no end marker")


def exists(path: Path) -> bool:
    """Anything at ``path``, a dangling symbolic link included.

    Only "not found" is False: a directory that cannot be searched (for
    example the 0700 backup directory of another account) raises
    ``PermissionError`` instead of looking empty.
    """
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def is_symlink(path: Path) -> bool:
    return stat.S_ISLNK(os.lstat(path).st_mode)


def is_regular_file(path: Path) -> bool:
    """A regular file that is not a symbolic link."""
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISREG(mode)


def is_real_directory(path: Path) -> bool:
    """A directory that is not a symbolic link."""
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISDIR(mode)


def entry_names(directory: Path) -> list[str]:
    """The entry names of one directory, sorted."""
    with os.scandir(directory) as entries:
        return sorted(entry.name for entry in entries)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new_file(path: Path, content: bytes) -> None:
    """Create ``path`` (mode 0600, never overwriting) with ``content`` and fsync it."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def set_private_modes(directory: Path, files: list[str]) -> None:
    """Directory 0700, each named file 0600."""
    os.chmod(directory, 0o700)
    for name in files:
        os.chmod(directory / name, 0o600)


def remove_file(path: Path) -> None:
    os.unlink(path)


def move_directory(source: Path, target: Path) -> None:
    """One ``rename`` (never a copy), then fsync of both parent directories.

    ``rename`` replaces an empty target directory on POSIX, so the target
    must not exist: ``FileExistsError`` otherwise.
    """
    if exists(target):
        raise FileExistsError(17, "File exists", str(target))
    os.rename(source, target)
    _fsync_directory(target.parent)
    if source.parent != target.parent:
        _fsync_directory(source.parent)


def make_private_directory(path: Path) -> None:
    """``mkdir`` with mode 0700 when absent (an existing directory is kept)."""
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if not is_real_directory(path):
            raise


def remove_tree(path: Path) -> None:
    """Remove a directory tree (or a single file) below ``.partial/``."""
    if is_real_directory(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)
