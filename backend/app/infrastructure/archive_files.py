"""Archive file handling of Movement-history archival (Phase 16, design B §5; step 1).

Infrastructure only, like ``backup_files`` (whose streaming SHA-256, durable
writes and atomic publish it reuses): canonical JSON, the gzip-compressed
JSON Lines writer and reader, ``SHA256SUMS`` in the exact format
``sha256sum -c`` reads, and the verified copy of a published archive folder.
No archival rule lives here (``app.application.history_archive_job``).
Nothing here connects to a database.
"""

import gzip
import hashlib
import json
import os
import re
import shutil
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import IO, Any, Final

from app.infrastructure import backup_files

#: gzip level 6: the usual balance of size and speed (``gzip -6``).
COMPRESS_LEVEL: Final = 6
SUMS_FILE: Final = "SHA256SUMS"
_SUMS_LINE: Final = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)")


def canonical_json(value: object) -> bytes:
    """One value as compact JSON with sorted keys, UTF-8 (the archive's one encoding)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def readable_json(value: object) -> bytes:
    """A JSON document for people (``manifest.json``, ``context.json``): sorted, indented."""
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


class IdOrderError(ValueError):
    """An id that is not larger than the id before it in a data file."""


class IdSet:
    """The id-set hash of one data file: SHA-256 of its ids in ascending order, one per line.

    Every data file holds its rows in ascending id order, so the ids are
    hashed as they come and none is kept (a large archive has millions); an
    id that is not larger than the one before raises ``IdOrderError``.
    """

    __slots__ = ("_hasher", "_last")

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self._last: int | None = None

    def add(self, value: int) -> None:
        if self._last is not None and value <= self._last:
            raise IdOrderError(f"id {value} follows id {self._last}")
        self._last = value
        self._hasher.update(f"{value}\n".encode("ascii"))

    def sha256(self) -> str:
        return self._hasher.hexdigest()


class JsonLinesWriter:
    """One ``*.jsonl.gz`` file: one canonical JSON object per line, gzip-compressed.

    The file is created new (never overwritten, mode 0600); the gzip header
    holds no name and no time, so the same rows always give the same bytes.
    ``close`` flushes and fsyncs it.
    """

    def __init__(self, path: Path) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.path = path
        self.rows = 0
        self.ids = IdSet()
        self._raw: IO[bytes] = os.fdopen(descriptor, "wb")
        self._gzip = gzip.GzipFile(
            filename="", mode="wb", fileobj=self._raw, compresslevel=COMPRESS_LEVEL, mtime=0
        )

    def write(self, row_id: int, value: object) -> bytes:
        """Append one row; returns the exact line written (with its newline)."""
        line = canonical_json(value) + b"\n"
        self.write_line(row_id, line)
        return line

    def write_line(self, row_id: int, line: bytes) -> None:
        """Append one row already rendered by ``canonical_json`` (with its newline)."""
        self._gzip.write(line)
        self.rows += 1
        self.ids.add(row_id)

    def close(self) -> None:
        self._gzip.close()
        self._raw.flush()
        os.fsync(self._raw.fileno())
        self._raw.close()


def write_readable_sections(path: Path, sections: Iterable[tuple[str, Iterable[object]]]) -> None:
    """``readable_json({key: [rows…], …})`` written section by section and row by row.

    The bytes are exactly those of ``readable_json`` for the same document
    (sorted keys, two-space indentation, UTF-8, a final line break), but no
    section is ever held whole: each row is rendered alone and indented to
    its depth, which works because JSON text holds a line break only between
    its tokens (a line break inside a string is escaped). ``sections`` must
    come in sorted key order. The file is created new (mode 0600, never
    overwriting) and fsynced, like ``write_document``.
    """
    encoder = json.JSONEncoder(sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb", buffering=backup_files.BLOCK_SIZE) as stream:
        previous: str | None = None
        for key, rows in sections:
            if previous is not None and key <= previous:
                raise ValueError(f"section {key!r} follows {previous!r}: keys must be sorted")
            stream.write(("{" if previous is None else ",").encode("utf-8"))
            stream.write(f"\n  {encoder.encode(key)}: ".encode())
            first = True
            for row in rows:
                rendered = encoder.encode(row).replace("\n", "\n    ")
                stream.write(f"{'[' if first else ','}\n    {rendered}".encode())
                first = False
            stream.write(b"[]" if first else b"\n  ]")
            previous = key
        stream.write(b"{}\n" if previous is None else b"\n}\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_json_lines(path: Path) -> Iterator[tuple[bytes, Any]]:
    """Every line of a ``*.jsonl.gz`` file: the raw line (with its newline) and its value."""
    with gzip.open(path, "rb") as stream:
        for line in stream:
            yield line, json.loads(line)


def write_document(path: Path, content: bytes) -> None:
    """A new file (mode 0600, never overwriting), fsynced."""
    backup_files.write_new_file(path, content)


def replace_document(path: Path, content: bytes, partial_dir: Path) -> None:
    """Write ``content`` to ``path`` atomically (the purge receipt).

    The file is written new (mode 0600) in ``partial_dir`` — a directory of
    the same file system, created 0700 when absent — fsynced, then renamed
    over ``path`` in one step, and the directory is fsynced: a reader sees
    the old file or the new one, never a part of it.
    """
    backup_files.make_private_directory(partial_dir)
    temporary = partial_dir / path.name
    if backup_files.exists(temporary):
        backup_files.remove_file(temporary)
    backup_files.write_new_file(temporary, content)
    os.replace(temporary, path)
    for directory in {path.parent, partial_dir}:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def sha256_of_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sums_text(digests: Mapping[str, str]) -> bytes:
    """``SHA256SUMS``: ``<sha256>  <name>`` per file, sorted by name (``sha256sum -c``)."""
    return "".join(f"{digests[name]}  {name}\n" for name in sorted(digests)).encode("ascii")


class SumsError(Exception):
    """``SHA256SUMS`` is missing or is not in the format ``sha256sum`` writes."""


def read_sums(path: Path) -> dict[str, str]:
    """The name → SHA-256 entries of a ``SHA256SUMS`` file (strict format)."""
    try:
        content = backup_files.read_bytes(path).decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise SumsError(f"{path.name} cannot be read ({type(exc).__name__})") from exc
    entries: dict[str, str] = {}
    lines = content.split("\n")
    if lines[-1] != "":
        raise SumsError(f"{path.name} does not end with a line break")
    for line in lines[:-1]:
        match = _SUMS_LINE.fullmatch(line)
        if match is None or match.group(2) in entries:
            raise SumsError(f"{path.name} has a malformed or repeated line")
        entries[match.group(2)] = match.group(1)
    return entries


def digest(path: Path) -> backup_files.FileDigest:
    return backup_files.digest(path)


def copy_file(source: Path, target: Path) -> None:
    """Copy one file to a new path (mode 0600, never overwriting) and fsync it."""
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with open(source, "rb") as reader, os.fdopen(descriptor, "wb") as writer:
        shutil.copyfileobj(reader, writer, backup_files.BLOCK_SIZE)
        writer.flush()
        os.fsync(writer.fileno())


def free_space(path: Path) -> tuple[int, int]:
    """(free bytes for this account, total bytes) of the file system holding ``path``."""
    usage = os.statvfs(path)
    return usage.f_bavail * usage.f_frsize, usage.f_blocks * usage.f_frsize


def is_writable_directory(path: Path) -> bool:
    return backup_files.is_real_directory(path) and os.access(path, os.W_OK | os.X_OK)
