"""Protected Git source store and workspace manifest service (PF-A1.2).

Privileged Git never runs against the writable workspace: the only repository the
control release consults is a bare store it created itself under the protected
installation root, with a configuration it wrote (no hooks, no fsmonitor, no
includes, no alternates, one approved remote, HTTPS only). Candidate trees are
exported from the store's object graph blob by blob (``ls-tree`` + ``cat-file``),
never through a checkout, ``git archive`` or attribute filters. Submodules,
symbolic links and Git LFS pointers are refused before anything is written.

Workspace status is an fd-safe byte/mode comparison against a protected manifest
recorded when the tool deployed a tree. A workspace without such a manifest, or a
tree supplied without a proven commit, has *unknown provenance*: no commit SHA is
invented and nothing is marked verified. Full deployed-artifact persistence and
the journaled workspace generation switch are PF-A3.

Python standard library only, Python 3.9 language baseline. Git itself is started
only through the caller-supplied runner callable.
"""
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import uuid

SOURCES_DIR = "sources"
STORE_SUFFIX = ".git"
HOOKS_SUFFIX = ".hooks"
LOCK_SUFFIX = ".lock"
MANIFEST_NAME = "source-manifest.json"
MANIFEST_SCHEMA = 1
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"
# Names of *untracked* workspace artifacts the manifest walk ignores (runtime configuration,
# dependency and cache directories, Git control metadata). They are an ignore policy for
# untracked content only: a commit that tracks a path with one of these components is refused
# by ``SourceStore.list_tree`` before anything is exported, a candidate tree that carries one is
# refused before deployment (``reserved_paths``), and a manifest that lists one cannot be
# verified (``compare_manifest``), so no deployed file is ever outside the provenance proof.
DEFAULT_EXCLUDES = frozenset({".git", ".env", "node_modules", ".venv", "__pycache__", ".pytest_cache"})
EXPORT_TOTAL_LIMIT = 512 * 1024 * 1024
EXPORT_FILE_LIMIT = 128 * 1024 * 1024
CHANGE_LIST_LIMIT = 200
# Keys ``git init --bare`` writes by itself; everything else must be one of ours.
INIT_CONFIG_KEYS = frozenset({
    "core.repositoryformatversion", "core.filemode", "core.bare", "core.ignorecase",
    "core.precomposeunicode", "core.symlinks", "core.logallrefupdates", "extensions.objectformat",
})


class SourceError(RuntimeError):
    """A refused or failed source operation. Nothing was written to the workspace."""


def store_key(remote_url):
    """Filesystem-safe key for one approved remote."""
    stripped = re.sub(r"^[a-z]+://", "", remote_url.strip().lower())
    stripped = re.sub(r"\.git\Z", "", stripped)
    key = re.sub(r"[^a-z0-9._-]+", "-", stripped).strip("-")
    if not key:
        raise SourceError("cannot derive a store key from remote " + repr(remote_url))
    return key[:120]


def _require_sha(value):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise SourceError("a full 40-hex commit SHA is required; got " + repr(value))
    return value


def parse_git_config(text):
    """Data parsing of a Git configuration file into {``section.sub.key``: value}.

    Sections ``[name]`` / ``[name "sub"]``, ``key = value`` lines, ``#``/``;`` comments.
    Quoted values are unquoted; includes are *not* followed (their presence is what the
    store verification refuses). Duplicate keys keep the last value, like Git.
    """
    values = {}
    section = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            match = re.fullmatch(r"\[\s*([A-Za-z0-9.-]+)(?:\s+\"((?:[^\"\\]|\\.)*)\")?\s*\]", line)
            if match is None:
                raise SourceError(f"git config line {number}: unsupported section header")
            section = match.group(1).lower()
            if match.group(2) is not None:
                section += "." + match.group(2).replace("\\\"", "\"").replace("\\\\", "\\")
            continue
        if section is None or "=" not in line:
            raise SourceError(f"git config line {number}: expected key = value inside a section")
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", key):
            raise SourceError(f"git config line {number}: unsupported key")
        if value.startswith("\"") and value.endswith("\"") and len(value) >= 2:
            value = value[1:-1].replace("\\\"", "\"").replace("\\\\", "\\")
        else:
            value = re.split(r"\s[#;]", value, maxsplit=1)[0].rstrip()
        values[section + "." + key] = value
    return values


# ------------------------------------------------------------------ store


@dataclasses.dataclass(frozen=True)
class ExportEntry:
    path: str
    mode: str
    blob: str


class SourceStore:
    """One protected bare repository for one approved remote.

    ``git`` is a callable ``git(argv, *, cwd, stdin=None, stdout=None, timeout=None) -> str``
    that starts the registered Git executable through the controlled runner. Every
    invocation here names the store with ``--git-dir``; discovery from a working
    directory is never used.
    """

    def __init__(self, sources_root, remote_url, git, *, protocols=("https",), key=None,
                 reserved_names=DEFAULT_EXCLUDES):
        self.sources_root = Path(sources_root)
        self.remote_url = str(remote_url)
        self.key = key or store_key(self.remote_url)
        self.path = self.sources_root / (self.key + STORE_SUFFIX)
        self.hooks_path = self.sources_root / (self.key + HOOKS_SUFFIX)
        self.lock_path = self.sources_root / (self.key + LOCK_SUFFIX)
        self.git = git
        self.protocols = tuple(protocols)
        # Path components a verified commit may not track (the manifest's ignore policy).
        self.reserved_names = frozenset(reserved_names)

    # -- invocation --------------------------------------------------------

    def _run(self, *args, stdin=None, stdout=None, timeout=None):
        argv = ["--git-dir=" + str(self.path), *args]
        return self.git(argv, cwd=self.sources_root, stdin=stdin, stdout=stdout, timeout=timeout)

    def exists(self):
        return os.path.isdir(str(self.path))

    # -- creation and verification -------------------------------------------

    def controlled_config(self):
        """The complete store configuration; written once at creation, verified on every use."""
        settings = [
            ("core.hooksPath", str(self.hooks_path)),
            ("core.fsmonitor", "false"),
            ("core.untrackedCache", "false"),
            ("core.logAllRefUpdates", "false"),
            ("gc.auto", "0"),
            ("gc.autoDetach", "false"),
            ("maintenance.auto", "false"),
            ("protocol.allow", "never"),
            ("transfer.fsckObjects", "true"),
            ("fetch.fsckObjects", "true"),
            ("receive.fsckObjects", "true"),
            ("fetch.prune", "false"),
            ("remote.approved.url", self.remote_url),
            ("remote.approved.fetch", "+refs/heads/*:refs/remotes/approved/*"),
            ("remote.approved.tagOpt", "--no-tags"),
        ]
        for protocol in self.protocols:
            settings.append((f"protocol.{protocol}.allow", "always"))
        return settings

    def create(self):
        """``git init --bare`` in the protected sources directory plus the controlled configuration."""
        if os.path.lexists(str(self.path)):
            raise SourceError(f"source store already exists: {self.path}")
        if not os.path.isdir(str(self.hooks_path)):
            os.mkdir(str(self.hooks_path), 0o700)
        os.chmod(str(self.hooks_path), 0o700)
        try:
            with tempfile.TemporaryDirectory(prefix="template-", dir=str(self.sources_root)) as template:
                # An empty template: no sample hooks, no inherited description/exclude files.
                os.chmod(template, 0o700)
                self.git(["init", "--bare", "-q", "--template=" + template, str(self.path)],
                         cwd=self.sources_root, stdin=None, stdout=None, timeout=None)
            os.chmod(str(self.path), 0o700)
            for key, value in self.controlled_config():
                self._run("config", "--local", key, value)
            self.verify()
        except BaseException:
            # A half-created store is ours to discard; nothing else references it yet.
            shutil.rmtree(str(self.path), ignore_errors=True)
            raise

    def verify(self):
        """Read-only checks that the store is ours: protected, our config only, no alternates."""
        info = os.lstat(str(self.path))
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SourceError(f"source store is not a directory: {self.path}")
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o077:
            raise SourceError(f"source store is not private to the trusted owner: {self.path}")
        config_path = self.path / "config"
        try:
            config_info = os.lstat(str(config_path))
        except OSError as exc:
            raise SourceError(f"source store configuration missing: {config_path}: {exc}") from exc
        if stat.S_ISLNK(config_info.st_mode) or not stat.S_ISREG(config_info.st_mode) or config_info.st_uid != 0 \
                or stat.S_IMODE(config_info.st_mode) & 0o022:
            raise SourceError(f"source store configuration is not a protected regular file: {config_path}")
        fd = os.open(str(config_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            text = _hash_read(fd).decode("utf-8", "replace")
        finally:
            os.close(fd)
        actual = parse_git_config(text)
        expected = {key.lower(): value for key, value in self.controlled_config()}
        for key, value in expected.items():
            if actual.get(key) != value:
                raise SourceError(f"source store configuration lacks {key}={value!r}; refusing to use it")
        foreign = sorted(set(actual) - set(expected) - INIT_CONFIG_KEYS)
        if foreign:
            raise SourceError("source store configuration contains keys this release did not write: "
                              + ", ".join(foreign) + "; refusing to use it")
        if os.path.lexists(str(self.path / "objects" / "info" / "alternates")):
            raise SourceError("source store has an alternate object store; refusing to use it")
        hooks_info = os.lstat(str(self.hooks_path))
        if not stat.S_ISDIR(hooks_info.st_mode) or hooks_info.st_uid != 0 or os.listdir(str(self.hooks_path)):
            raise SourceError(f"source store hooks directory must be an empty protected directory: {self.hooks_path}")
        return True

    def ensure(self):
        if self.exists():
            self.verify()
        else:
            self.create()
        return self

    # -- objects -----------------------------------------------------------

    def has_commit(self, commit):
        _require_sha(commit)
        try:
            self._run("cat-file", "-e", commit + "^{commit}")
        except Exception:
            return False
        return True

    def fetch_commit(self, commit, *, timeout=None):
        """Fetch from the approved remote until ``commit`` is present; returns the verified SHA."""
        _require_sha(commit)
        self.verify()
        if not self.has_commit(commit):
            try:
                self._run("fetch", "--no-tags", "--no-write-fetch-head", "approved", commit, timeout=timeout)
            except Exception:
                self._run("fetch", "--no-write-fetch-head", "approved",
                          "+refs/heads/*:refs/remotes/approved/*", "+refs/tags/*:refs/tags/*", timeout=timeout)
        if not self.has_commit(commit):
            raise SourceError(f"commit {commit} is not available from the approved remote {self.remote_url}")
        resolved = self.resolve(commit)
        # Pin the verified commit so it stays reachable inside the store.
        self._run("update-ref", "refs/pinned/" + resolved, resolved)
        return resolved

    def resolve(self, commit):
        _require_sha(commit)
        actual = self._run("rev-parse", "--verify", "--end-of-options", commit + "^{commit}").strip()
        if actual != commit:
            raise SourceError(f"store resolved {commit} to {actual!r}; refusing")
        return actual

    def is_ancestor(self, ancestor, descendant):
        """True when ``ancestor`` reaches ``descendant``; both commits must already be in the store."""
        for commit in (ancestor, descendant):
            if not self.has_commit(commit):
                raise SourceError(f"commit {commit} is not in the protected source store")
        try:
            self._run("merge-base", "--is-ancestor", ancestor, descendant)
        except Exception:
            return False
        return True

    def list_tree(self, commit):
        """Tracked entries of ``commit``; refuses submodules, symbolic links and unknown modes."""
        _require_sha(commit)
        listing = self._run("ls-tree", "-r", "-z", "--full-tree", commit)
        entries = []
        for item in listing.split("\0"):
            if not item:
                continue
            meta, _, path = item.partition("\t")
            parts = meta.split(" ")
            if len(parts) != 3 or not path:
                raise SourceError("unexpected ls-tree output: " + repr(item[:80]))
            mode, kind, blob = parts
            if kind == "commit" or mode == "160000":
                raise SourceError(f"unsupported source entry (submodule): {path}")
            if mode == "120000":
                raise SourceError(f"unsupported source entry (symbolic link): {path}")
            if kind != "blob" or mode not in ("100644", "100755"):
                raise SourceError(f"unsupported source entry ({kind} {mode}): {path}")
            pieces = path.split("/")
            if any(piece in ("", ".", "..") for piece in pieces) or ".git" in pieces:
                raise SourceError(f"unsupported source path: {path}")
            if any(piece in self.reserved_names for piece in pieces):
                # Tracked content the workspace manifest would ignore can never be proven
                # deployed; the commit is refused as a whole before any export (A12-R02).
                raise SourceError(f"unsupported source path (tracked reserved workspace artifact name): {path}")
            if not SHA_RE.fullmatch(blob):
                raise SourceError("unexpected blob id in ls-tree output")
            entries.append(ExportEntry(path=path, mode=mode, blob=blob))
        if not entries:
            raise SourceError(f"commit {commit} has an empty tree")
        return entries

    def export(self, commit, destination, *, timeout=None):
        """Write the tracked blobs of ``commit`` under ``destination`` (must not exist).

        Blobs are streamed with ``cat-file --batch`` into a private spool file, checked
        for Git LFS pointers and size bounds, then written with 0600/0700 modes.
        Returns the list of ExportEntry that was written.
        """
        commit = self.resolve(commit)
        destination = Path(destination)
        if os.path.lexists(str(destination)):
            raise SourceError(f"export destination exists: {destination}")
        entries = self.list_tree(commit)
        blobs = sorted({entry.blob for entry in entries})
        spool_dir = destination.parent
        spool_fd, spool_name = tempfile.mkstemp(prefix=".export-", dir=str(spool_dir))
        os.close(spool_fd)
        contents = {}
        try:
            self._run("cat-file", "--batch", stdin=("\n".join(blobs) + "\n").encode("utf-8"), stdout=spool_name,
                      timeout=timeout)
            total = 0
            with open(spool_name, "rb") as spool:
                for blob in blobs:
                    header = spool.readline()
                    fields = header.decode("utf-8", "replace").split()
                    if len(fields) != 3 or fields[0] != blob or fields[1] != "blob":
                        raise SourceError("unexpected cat-file output for blob " + blob)
                    size = int(fields[2])
                    if size > EXPORT_FILE_LIMIT:
                        raise SourceError(f"blob {blob} exceeds the export size limit")
                    total += size
                    if total > EXPORT_TOTAL_LIMIT:
                        raise SourceError("exported tree exceeds the total size limit")
                    data = spool.read(size)
                    if len(data) != size or spool.read(1) != b"\n":
                        raise SourceError("truncated cat-file output for blob " + blob)
                    if data.startswith(LFS_POINTER_PREFIX):
                        raise SourceError("unsupported source entry (Git LFS pointer) for blob " + blob)
                    contents[blob] = data
        finally:
            try:
                os.unlink(spool_name)
            except OSError:
                pass
        os.mkdir(str(destination), 0o700)
        os.chmod(str(destination), 0o700)
        for entry in entries:
            target = destination / entry.path
            for parent in reversed(target.parents):
                if parent == destination or parent in destination.parents:
                    continue
                if not os.path.isdir(str(parent)):
                    os.mkdir(str(parent), 0o700)
            data = contents[entry.blob]
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.chmod(str(target), 0o700 if entry.mode == "100755" else 0o600)
        return entries


# ---------------------------------------------------------------- manifest


def _hash_fd(fd):
    digest = hashlib.sha256()
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        digest.update(block)
    return digest.hexdigest()


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
MANIFEST_ENTRY_LIMIT = 200000


def walk_tree(root, *, excludes=DEFAULT_EXCLUDES):
    """fd-safe walk of an editor-writable tree.

    Every directory is opened relative to its parent's descriptor with ``O_NOFOLLOW`` and
    every file is opened the same way, so a component swapped for a symbolic link between
    listing and opening is refused (``ELOOP``) instead of followed; ancestors renamed
    underneath keep the descriptors valid. Yields ``(relative path, kind, fd_or_None,
    stat_or_None)``: kind ``file`` comes with an open descriptor the caller must close,
    ``unsupported`` for links, special files and anything that could not be opened as
    the type its directory entry announced. Directories are not yielded.
    """
    root_fd = os.open(str(root), DIR_FLAGS)
    count = 0
    stack = [(root_fd, "")]
    try:
        while stack:
            dir_fd, prefix = stack.pop()
            try:
                with os.scandir(dir_fd) as entries:
                    listed = sorted(entries, key=lambda entry: entry.name)
                for entry in listed:
                    if entry.name in excludes:
                        continue
                    relative = entry.name if not prefix else prefix + "/" + entry.name
                    count += 1
                    if count > MANIFEST_ENTRY_LIMIT:
                        raise SourceError("tree has more than %d entries; refusing to inventory it" % MANIFEST_ENTRY_LIMIT)
                    if entry.is_symlink():
                        yield relative, "unsupported", None, None
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        try:
                            child = os.open(entry.name, DIR_FLAGS, dir_fd=dir_fd)
                        except OSError:
                            yield relative, "unsupported", None, None
                            continue
                        stack.append((child, relative))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        yield relative, "unsupported", None, None
                        continue
                    try:
                        fd = os.open(entry.name, FILE_FLAGS, dir_fd=dir_fd)
                    except OSError:
                        yield relative, "unsupported", None, None
                        continue
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode):
                        os.close(fd)
                        yield relative, "unsupported", None, None
                        continue
                    yield relative, "file", fd, info
            finally:
                os.close(dir_fd)
    finally:
        for dir_fd, _ in stack:
            os.close(dir_fd)


def reserved_paths(root, *, excludes=DEFAULT_EXCLUDES, limit=CHANGE_LIST_LIMIT):
    """Files under ``root`` (a private candidate tree) whose path has a component in ``excludes``.

    A candidate is deployed as a whole, so any such file would land in the workspace while the
    manifest walk ignores it; callers refuse the candidate when this is not empty.
    """
    found = []
    for relative, kind, fd, info in walk_tree(root, excludes=frozenset()):
        if fd is not None:
            os.close(fd)
        if any(piece in excludes for piece in relative.split("/")):
            found.append(relative)
    return found[:limit]


def build_manifest(root, *, source, excludes=DEFAULT_EXCLUDES):
    """fd-safe inventory of every regular file under ``root``: path, executable bit, size, sha256.

    Symbolic links and special files are recorded as unsupported entries. Directories
    are not recorded (Git does not track them). ``source`` describes provenance:
    ``{"kind": "git_commit", "commit": <sha>, "remote": <url>}`` or ``{"kind": "unknown"}``.
    """
    if source.get("kind") == "git_commit":
        _require_sha(source.get("commit"))
    elif source.get("kind") != "unknown":
        raise SourceError("manifest source kind must be git_commit or unknown")
    entries = []
    for relative, kind, fd, info in walk_tree(root, excludes=excludes):
        if kind != "file":
            entries.append({"path": relative, "kind": "unsupported"})
            continue
        try:
            entries.append({
                "path": relative, "kind": "file",
                "executable": bool(info.st_mode & 0o111),
                "size": info.st_size, "sha256": _hash_fd(fd),
            })
        finally:
            os.close(fd)
    entries.sort(key=lambda item: item["path"])
    return {"schema_version": MANIFEST_SCHEMA, "source": dict(source), "entries": entries}


def archive_verified_tree(root, manifest, destination, *, excludes=DEFAULT_EXCLUDES):
    """Write ``destination`` (tar.gz) from the bytes of ``root`` while proving them equal to ``manifest``.

    Each file is read once through its descriptor, hashed, compared with the manifest
    entry (size, digest, executable bit) and that same content is stored; an extra,
    missing, changed or unsupported entry aborts the archive. This is how a workspace
    without the commit in the protected store can still produce an exact deployed-source
    archive: the archive is the manifest-verified tree, not a second independent read.
    """
    validate_manifest(manifest)
    expected = {entry["path"]: entry for entry in manifest["entries"]}
    if any(entry["kind"] != "file" for entry in expected.values()):
        raise SourceError("manifest contains unsupported entries; the tree cannot be archived as verified")
    destination = Path(destination)
    seen = set()
    try:
        with tarfile.open(str(destination), "w:gz") as archive:
            for relative, kind, fd, info in walk_tree(root, excludes=excludes):
                if kind != "file":
                    raise SourceError(f"unsupported entry in the workspace: {relative}")
                try:
                    chunks = []
                    while True:
                        block = os.read(fd, 1024 * 1024)
                        if not block:
                            break
                        chunks.append(block)
                    data = b"".join(chunks)
                finally:
                    os.close(fd)
                entry = expected.get(relative)
                if entry is None:
                    raise SourceError(f"workspace has a file the manifest does not: {relative}")
                executable = bool(info.st_mode & 0o111)
                if (len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]
                        or executable != entry["executable"]):
                    raise SourceError(f"workspace file differs from the manifest: {relative}")
                seen.add(relative)
                member = tarfile.TarInfo(name=relative)
                member.size = len(data)
                member.mode = 0o755 if executable else 0o644
                member.mtime = int(info.st_mtime)
                archive.addfile(member, io.BytesIO(data))
        missing = sorted(set(expected) - seen)
        if missing:
            raise SourceError("workspace lacks manifest files: " + ", ".join(missing[:10]))
    except BaseException:
        # Never leave a partial archive that could be mistaken for a verified one.
        try:
            os.unlink(str(destination))
        except OSError:
            pass
        raise
    return len(seen)


def manifest_bytes(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def manifest_digest(manifest):
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def validate_manifest(value, label="manifest"):
    if not isinstance(value, dict) or set(value) != {"schema_version", "source", "entries"}:
        raise SourceError(f"{label}: keys must be exactly schema_version, source, entries")
    if value["schema_version"] is True or value["schema_version"] != MANIFEST_SCHEMA:
        raise SourceError(f"{label}: unsupported schema_version")
    source = value["source"]
    if not isinstance(source, dict) or source.get("kind") not in ("git_commit", "unknown"):
        raise SourceError(f"{label}: source.kind must be git_commit or unknown")
    if source["kind"] == "git_commit":
        _require_sha(source.get("commit"))
    if not isinstance(value["entries"], list):
        raise SourceError(f"{label}: entries must be an array")
    for entry in value["entries"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or entry.get("kind") not in ("file", "unsupported"):
            raise SourceError(f"{label}: invalid entry")
        if entry["kind"] == "file" and (not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("size"), int)
                                        or not isinstance(entry.get("executable"), bool)):
            raise SourceError(f"{label}: invalid file entry " + entry["path"])
    return value


def write_manifest(path, manifest):
    """Protected private manifest file (0600), atomically replaced."""
    validate_manifest(manifest)
    path = Path(path)
    data = manifest_bytes(manifest) + b"\n"
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    except BaseException:
        try:
            os.unlink(str(temporary))
        except OSError:
            pass
        raise
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(data[:-1]).hexdigest()


def load_manifest(path, parse_json):
    """Strict load through the caller's duplicate-key-rejecting JSON parser; None when absent."""
    path = Path(path)
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise SourceError(f"{path}: manifest is not a protected regular file")
        data = _hash_read(fd)
    finally:
        os.close(fd)
    return validate_manifest(parse_json(data, label=str(path)), str(path))


def _hash_read(fd):
    chunks = []
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block:
            break
        chunks.append(block)
    return b"".join(chunks)


def compare_manifest(root, manifest, *, excludes=DEFAULT_EXCLUDES):
    """Compare the tree under ``root`` with ``manifest``. Returns a bounded change report."""
    validate_manifest(manifest)
    unverifiable = [entry["path"] for entry in manifest["entries"]
                    if any(piece in excludes for piece in entry["path"].split("/"))]
    if unverifiable:
        # The walk would skip these entries, so a match could never prove them: fail closed.
        raise SourceError("manifest lists paths the workspace policy ignores; they cannot be verified: "
                          + ", ".join(unverifiable[:10]))
    current = build_manifest(root, source={"kind": "unknown"}, excludes=excludes)
    expected = {entry["path"]: entry for entry in manifest["entries"] if entry["kind"] == "file"}
    unsupported_expected = {entry["path"] for entry in manifest["entries"] if entry["kind"] != "file"}
    actual = {entry["path"]: entry for entry in current["entries"] if entry["kind"] == "file"}
    unsupported = sorted(entry["path"] for entry in current["entries"] if entry["kind"] != "file")
    changed, added, removed = [], [], []
    for path in sorted(set(expected) | set(actual)):
        if path in expected and path in actual:
            left, right = expected[path], actual[path]
            if left["sha256"] != right["sha256"] or left["executable"] != right["executable"]:
                changed.append(path)
        elif path in actual:
            added.append(path)
        else:
            removed.append(path)
    counts = {"changed": len(changed), "added": len(added), "removed": len(removed), "unsupported": len(unsupported)}
    matches = not (changed or added or removed or unsupported or unsupported_expected)
    return {
        "matches": matches,
        "counts": counts,
        "changed": changed[:CHANGE_LIST_LIMIT],
        "added": added[:CHANGE_LIST_LIMIT],
        "removed": removed[:CHANGE_LIST_LIMIT],
        "unsupported": unsupported[:CHANGE_LIST_LIMIT],
    }


def change_summary(report, limit=10):
    items = []
    for label in ("changed", "added", "removed", "unsupported"):
        items.extend(f"{label}:{path}" for path in report[label])
    if len(items) > limit:
        items = items[:limit] + ["..."]
    return items


HINT_READ_LIMIT = 256 * 1024


def _read_small_nofollow(path, limit=HINT_READ_LIMIT):
    """Bounded read of an editor-controlled regular file; links and special files yield None."""
    try:
        fd = os.open(str(path), FILE_FLAGS)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, limit + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > limit:
        return None
    return data.decode("utf-8", "replace")


def workspace_head_hint(workspace):
    """The commit the writable checkout *claims* (``.git/HEAD`` read as data), or None.

    This is only a hint for fetching into the protected store; no Git program runs
    here and the value is never trusted until the exported commit tree matches the
    workspace byte for byte. Reads are bounded and never follow links.
    """
    git_dir = Path(workspace) / ".git"
    try:
        if stat.S_ISLNK(os.lstat(str(git_dir)).st_mode):
            return None
    except OSError:
        return None
    head = _read_small_nofollow(git_dir / "HEAD")
    if head is None:
        return None
    head = head.strip()
    if SHA_RE.fullmatch(head):
        return head
    if not head.startswith("ref: "):
        return None
    ref = head[5:].strip()
    if not re.fullmatch(r"refs/[A-Za-z0-9._/-]+", ref) or ".." in ref:
        return None
    value = _read_small_nofollow(git_dir / ref)
    if value is not None and SHA_RE.fullmatch(value.strip()):
        return value.strip()
    packed = _read_small_nofollow(git_dir / "packed-refs")
    if packed is not None:
        for line in packed.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and SHA_RE.fullmatch(parts[0]):
                return parts[0]
    return None
