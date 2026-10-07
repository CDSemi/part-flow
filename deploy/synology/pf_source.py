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

PF-A3.1: the fd-safe archive writer (``archive_tree``), the streaming member inspector
(``inspect_archive``) and the descriptor-relative, exclusive-create extractor
(``extract_archive``) replace every path-based tar extraction of the control release.
"""
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import uuid
import zlib


def _load_sibling_module(name):
    """The already loaded sibling module (pf-admin loads pf_instance first), else this directory's own copy."""
    path = Path(__file__).resolve().parent / (name + ".py")
    if name in sys.modules and getattr(sys.modules[name], "__file__", None) == str(path):
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load control module: " + str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# PF-A3.1: the descriptor-relative removal of a tree the extractor just created (pf_instance never loads pf_source).
pf_instance = _load_sibling_module("pf_instance")

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


class ArchiveLimitExceeded(SourceError):
    """A tree exceeds an entry, name or size limit (walk_tree's entry count, archive_tree's ``limits``: the importer
    would refuse it). Unlike a link or special entry it says nothing about what a source replacement would destroy
    (audit AF-6)."""


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
                        raise ArchiveLimitExceeded("tree has more than %d entries; refusing to inventory it"
                                                   % MANIFEST_ENTRY_LIMIT)
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

    PF-A3.1: members are regular files only (parents implied), PAX format, uid/gid 0 and empty owner names.
    Returns ``(count, expanded_bytes, members_sha256)`` (section 3.2); existing callers use ``[0]``.
    """
    validate_manifest(manifest)
    expected = {entry["path"]: entry for entry in manifest["entries"]}
    if any(entry["kind"] != "file" for entry in expected.values()):
        raise SourceError("manifest contains unsupported entries; the tree cannot be archived as verified")
    destination = Path(destination)
    seen = set()
    members = []
    try:
        with tarfile.open(str(destination), "w:gz", format=tarfile.PAX_FORMAT) as archive:
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
                archive.addfile(_file_member(relative, len(data), executable, info), io.BytesIO(data))
                members.append((relative, "file", len(data), executable))
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
    return len(seen), sum(member[2] for member in members), members_digest(members)


def manifest_bytes(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def manifest_digest(manifest):
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def entries_digest(manifest):
    """Tree content identity (PF-A3.1 section 2.3): SHA-256 of ``manifest_bytes(manifest["entries"])``, without the
    provenance dict, so a tree extracted with unknown provenance compares equal to the same tree recorded as a
    commit."""
    return hashlib.sha256(manifest_bytes(manifest["entries"])).hexdigest()


# ---------------------------------------------------------------- archives (PF-A3.1)

ARCHIVE_PAX_KEYS = frozenset({"path", "size", "mtime", "atime", "ctime", "uid", "gid", "uname", "gname"})
ARCHIVE_TYPES = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
COPY_BLOCK = 1024 * 1024


@dataclasses.dataclass(frozen=True)
class ArchiveLimits:
    members: int
    total_bytes: int
    file_bytes: int
    path_bytes: int
    component_bytes: int
    depth: int


SOURCE_LIMITS = ArchiveLimits(MANIFEST_ENTRY_LIMIT, EXPORT_TOTAL_LIMIT, EXPORT_FILE_LIMIT, 1024, 255, 64)
HISTORY_LIMITS = ArchiveLimits(200000, 64 * 1024 ** 3, 64 * 1024 ** 3, 1024, 255, 64)


class ArchiveRefused(SourceError):
    """An archive refused before or during extraction: ``code`` (archive-member-refused, archive-unreadable,
    archive-changed), the member name and the reason of the section 3.2 refusal table."""

    def __init__(self, code, member, reason):
        self.code, self.member, self.reason = code, member, reason
        super().__init__(f"{code}: {reason}: {member!r}"[:400])


@dataclasses.dataclass(frozen=True)
class ArchiveInventory:
    """Pass 1 of an archive: explicit members ((path, kind, size, executable), ...) in archive order."""
    members: tuple
    expanded_bytes: int
    members_sha256: str


def _normalized_json(value):
    # The bytes of pf_instance.normalize_json (sorted keys, no whitespace, UTF-8, no NaN).
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8", "surrogateescape")


def members_digest(members):
    """SHA-256 of normalize_json([[path, "file"|"dir", size, executable], ...]) over explicit members in order."""
    return hashlib.sha256(_normalized_json([list(member) for member in members])).hexdigest()


def member_name_problem(name, limits):
    """(normalized name, refusal reason or None) for one archive member name (section 3.2 refusal table)."""
    if not isinstance(name, str) or not name:
        return name, "empty-component"
    if "\\" in name or "\x00" in name or any("\udc80" <= char <= "\udcff" for char in name):
        return name, "traversal"
    if name.startswith("/"):
        return name, "absolute"
    while name.startswith("./"):
        name = name[2:]
    if name.endswith("/"):
        name = name[:-1]
    if not name:
        return name, "empty-component"
    parts = name.split("/")
    if any(part in (".", "..") for part in parts):
        return name, "traversal"
    if any(part == "" for part in parts):
        return name, "empty-component"
    if len(name.encode("utf-8")) > limits.path_bytes:
        return name, "path-length"
    if any(len(part.encode("utf-8")) > limits.component_bytes for part in parts):
        return name, "component-length"
    if len(parts) > limits.depth:
        return name, "depth"
    return name, None


def _file_member(relative, size, executable, info):
    member = tarfile.TarInfo(name=relative)
    member.size = size
    member.mode = 0o755 if executable else 0o644
    member.mtime = int(info.st_mtime)
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    return member


class _HashingReader:
    """Exactly ``size`` bytes of a descriptor, hashed while tarfile copies them (a shrinking file fails the copy)."""

    def __init__(self, fd, size):
        self.fd, self.remaining, self.digest = fd, size, hashlib.sha256()

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        wanted = self.remaining if size is None or size < 0 else min(size, self.remaining)
        block = os.read(self.fd, min(wanted, COPY_BLOCK))
        self.remaining -= len(block)
        self.digest.update(block)
        return block


def archive_tree(root, destination, *, excludes=DEFAULT_EXCLUDES, unsupported="refuse", limits=SOURCE_LIMITS):
    """fd-safe ``tar.gz`` of the regular files under ``root`` (walk_tree: no-follow, descriptor-relative).

    Members are regular files only (parents implied), mode 0755 with any execute bit else 0644, uid/gid 0, empty
    owner names, ``mtime`` from fstat, PAX format; each file is hashed while it is archived. ``unsupported="refuse"``
    raises on a link or special entry; ``"record"`` skips and returns it (only `pf backup --emergency`). A name or
    size the importer would refuse (``limits``) is refused here too, so every archive pf writes can be imported.
    Returns {"manifest" (pf_source manifest of exactly what was archived, source unknown), "unsupported",
    "expanded_bytes", "members", "members_sha256"}; a partial destination is removed on failure.
    """
    if unsupported not in ("refuse", "record"):
        raise ValueError("unsupported must be refuse or record")
    destination = Path(destination)
    fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    entries, skipped, members, total = [], [], [], 0
    try:
        with os.fdopen(fd, "wb") as handle, \
                tarfile.open(fileobj=handle, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for relative, kind, file_fd, info in walk_tree(root, excludes=excludes):
                if kind != "file":
                    if unsupported == "refuse":
                        raise SourceError(f"unsupported entry (link or special file): {relative}")
                    skipped.append(relative)
                    continue
                try:
                    _, problem = member_name_problem(relative, limits)
                    if problem is not None:
                        raise ArchiveLimitExceeded(f"the name {relative!r} cannot be archived ({problem})")
                    if info.st_size > limits.file_bytes or total + info.st_size > limits.total_bytes:
                        raise ArchiveLimitExceeded(f"{relative}: the tree exceeds the archive size limits")
                    executable = bool(info.st_mode & 0o111)
                    reader = _HashingReader(file_fd, info.st_size)
                    archive.addfile(_file_member(relative, info.st_size, executable, info), reader)
                    if reader.remaining:
                        raise SourceError(f"{relative} changed while it was archived")
                finally:
                    os.close(file_fd)
                entries.append({"path": relative, "kind": "file", "executable": executable, "size": info.st_size,
                                "sha256": reader.digest.hexdigest()})
                members.append((relative, "file", info.st_size, executable))
                total += info.st_size
                if len(members) > limits.members:
                    raise ArchiveLimitExceeded("the tree has more members than the archive limit")
    except BaseException:
        try:
            os.unlink(str(destination))
        except OSError:
            pass
        raise
    entries.sort(key=lambda item: item["path"])
    return {"manifest": {"schema_version": MANIFEST_SCHEMA, "source": {"kind": "unknown"}, "entries": entries},
            "unsupported": skipped, "expanded_bytes": total, "members": len(members),
            "members_sha256": members_digest(members)}


def tree_limit_problem(root, *, excludes=DEFAULT_EXCLUDES, limits=SOURCE_LIMITS):
    """The first entry, name or size of ``root`` that archive_tree would refuse under ``limits`` (a read-only walk; no
    file byte is read), or None. Links and special entries are archive_tree's own refusal, not reported here."""
    total = members = 0
    walker = walk_tree(root, excludes=excludes)
    try:
        for relative, kind, file_fd, info in walker:
            if file_fd is not None:
                os.close(file_fd)
            if kind != "file":
                continue
            _, problem = member_name_problem(relative, limits)
            if problem is not None:
                return f"the name {relative!r} cannot be archived ({problem})"
            if info.st_size > limits.file_bytes or total + info.st_size > limits.total_bytes:
                return f"{relative}: the tree exceeds the archive size limits"
            total += info.st_size
            members += 1
            if members > limits.members:
                return "the tree has more members than the archive limit"
    except ArchiveLimitExceeded as exc:
        return str(exc)
    finally:
        walker.close()
    return None


def _archive_stream(fd):
    """A private read handle on ``fd`` from offset 0 (``os.dup``; the path is never re-opened)."""
    duplicate = os.dup(fd)
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        return os.fdopen(duplicate, "rb")
    except BaseException:
        os.close(duplicate)
        raise


_UNREADABLE = (tarfile.TarError, EOFError, zlib.error, OSError, UnicodeError, RecursionError)
# A GNU longname/longlink or PAX extended header is read whole into memory by tarfile while it parses the header, before
# any member limit can apply; a larger declared payload is refused first (audit AF-8).
EXTENDED_HEADER_LIMIT = 64 * 1024


class _BoundedTarInfo(tarfile.TarInfo):
    """tarfile's header parser with the extended-header payloads bounded before they are read (and GNU sparse maps,
    a refused type, never parsed)."""

    def _proc_gnulong(self, archive):
        if self.size > EXTENDED_HEADER_LIMIT:
            raise ArchiveRefused("archive-member-refused", self.name, "path-length")
        return super()._proc_gnulong(archive)

    def _proc_pax(self, archive):
        if self.size > EXTENDED_HEADER_LIMIT:
            raise ArchiveRefused("archive-member-refused", self.name, "pax-key")
        return super()._proc_pax(archive)

    def _proc_sparse(self, archive):
        raise ArchiveRefused("archive-member-refused", self.name, "type")


def _members(handle):
    """Yield every member of a streaming ``tar.gz`` (``next()``, never ``getmembers()``) with its archive."""
    try:
        archive = tarfile.open(fileobj=handle, mode="r|gz", encoding="utf-8", errors="surrogateescape",
                               tarinfo=_BoundedTarInfo)
    except _UNREADABLE as exc:
        raise ArchiveRefused("archive-unreadable", "", str(exc) or type(exc).__name__) from exc
    with archive:
        while True:
            try:
                member = archive.next()
            except _UNREADABLE as exc:
                raise ArchiveRefused("archive-unreadable", "", str(exc) or type(exc).__name__) from exc
            if archive.pax_headers:
                raise ArchiveRefused("archive-member-refused", "<global header>", "pax-key")
            if member is None:
                return
            archive.members = []  # streaming: no member list is kept
            yield archive, member


def _check_member(member, limits, explicit, implied):
    """(name, kind, size, executable) of one member after every pass 1 refusal of the section 3.2 table."""
    if member.type not in ARCHIVE_TYPES:
        raise ArchiveRefused("archive-member-refused", member.name, "type")
    name, problem = member_name_problem(member.name, limits)
    if problem is not None:
        raise ArchiveRefused("archive-member-refused", member.name, problem)
    if set(member.pax_headers) - ARCHIVE_PAX_KEYS:
        raise ArchiveRefused("archive-member-refused", member.name, "pax-key")
    kind = "dir" if member.type == tarfile.DIRTYPE else "file"
    if kind == "file" and member.mode & 0o7000:
        raise ArchiveRefused("archive-member-refused", member.name, "mode-bits")
    parts = name.split("/")
    parents = ["/".join(parts[:index]) for index in range(1, len(parts))]
    if name in explicit or (kind == "file" and name in implied) \
            or any(explicit.get(parent) == "file" for parent in parents):
        raise ArchiveRefused("archive-member-refused", member.name, "duplicate")
    explicit[name] = kind
    implied.update(parents)
    size = member.size if kind == "file" else 0
    return name, kind, size, kind == "file" and bool(member.mode & 0o111)


def inspect_archive(fd, *, limits, expected=None):
    """Pass 1 (section 3.2): stream every header and consume every byte of ``fd`` (a ``tar.gz``) without storing or
    extracting anything; ArchiveRefused on the first refused member. ``expected``: (expanded_bytes, members,
    members_sha256) declared by a manifest (None entries are not compared) -> ``declared-mismatch``."""
    explicit, implied, members, total = {}, set(), [], 0
    with _archive_stream(fd) as handle:
        for archive, member in _members(handle):
            if len(members) >= limits.members:
                raise ArchiveRefused("archive-member-refused", member.name, "member-count")
            name, kind, size, executable = _check_member(member, limits, explicit, implied)
            if kind == "file":
                if size > limits.file_bytes:
                    raise ArchiveRefused("archive-member-refused", member.name, "file-size")
                if total + size > limits.total_bytes:
                    raise ArchiveRefused("archive-member-refused", member.name, "total-size")
                counted = 0
                try:
                    source = archive.extractfile(member)
                    while True:
                        block = source.read(COPY_BLOCK)
                        if not block:
                            break
                        counted += len(block)
                except _UNREADABLE as exc:
                    raise ArchiveRefused("archive-unreadable", member.name, str(exc) or type(exc).__name__) from exc
                if counted != size:
                    raise ArchiveRefused("archive-unreadable", member.name, "truncated member data")
                total += size
            members.append((name, kind, size, executable))
    inventory = ArchiveInventory(tuple(members), total, members_digest(members))
    if expected is not None:
        declared = (inventory.expanded_bytes, len(inventory.members), inventory.members_sha256)
        if any(want is not None and want != got for want, got in zip(expected, declared)):
            raise ArchiveRefused("archive-member-refused", "", "declared-mismatch")
    return inventory


def _open_created(root_fd, parts, created):
    """The directory ``parts`` below ``root_fd``: missing components are created 0700 relative to their parent's
    descriptor; an existing component must be one this extraction created (``created``), else archive-changed."""
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            path = "/".join(parts[:index + 1])
            if path not in created:
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError as exc:
                    raise ArchiveRefused("archive-changed", path, "an entry appeared that this extraction did not "
                                                                  "create") from exc
                created.add(path)
            child = os.open(part, DIR_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _extract_into(fd, root_fd, inventory, limits):
    entries, created, total, index = [], set(), 0, 0
    with _archive_stream(fd) as handle:
        for archive, member in _members(handle):
            if index >= len(inventory.members) or member.type not in ARCHIVE_TYPES:
                raise ArchiveRefused("archive-changed", member.name, "the archive differs from pass 1")
            name, problem = member_name_problem(member.name, limits)
            kind = "dir" if member.type == tarfile.DIRTYPE else "file"
            size = member.size if kind == "file" else 0
            observed = (name, kind, size, kind == "file" and bool(member.mode & 0o111))
            if problem is not None or observed != tuple(inventory.members[index]):
                raise ArchiveRefused("archive-changed", member.name, "the archive differs from pass 1")
            index += 1
            parts = name.split("/")
            if kind == "dir":
                os.close(_open_created(root_fd, parts, created))
                continue
            parent = _open_created(root_fd, parts[:-1], created)
            try:
                try:
                    target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     0o600, dir_fd=parent)
                except FileExistsError as exc:
                    raise ArchiveRefused("archive-changed", member.name, "the member exists already") from exc
            finally:
                os.close(parent)
            digest, counted = hashlib.sha256(), 0
            try:
                source = archive.extractfile(member)
                while True:
                    try:
                        block = source.read(COPY_BLOCK)
                    except _UNREADABLE as exc:
                        raise ArchiveRefused("archive-unreadable", member.name,
                                             str(exc) or type(exc).__name__) from exc
                    if not block:
                        break
                    counted += len(block)
                    total += len(block)
                    if counted > size:
                        raise ArchiveRefused("archive-changed", member.name, "more bytes than its header")
                    if total > limits.total_bytes:
                        raise ArchiveRefused("archive-member-refused", member.name, "total-size")
                    digest.update(block)
                    view = memoryview(block)
                    while view:
                        written = os.write(target, view)
                        view = view[written:]
                if counted != size:
                    raise ArchiveRefused("archive-changed", member.name, "fewer bytes than its header")
                os.fchmod(target, 0o700 if observed[3] else 0o600)
            finally:
                os.close(target)
            entries.append({"path": name, "kind": "file", "executable": observed[3], "size": size,
                            "sha256": digest.hexdigest()})
    if index != len(inventory.members):
        raise ArchiveRefused("archive-changed", "", "the archive differs from pass 1")
    return entries


def extract_archive(fd, parent_fd, name, inventory, *, limits):
    """Pass 2 (section 3.2): extract ``fd`` into the new private directory ``name`` (0700, must not exist) below
    ``parent_fd``. Every member must equal pass 1 (``inventory``) in order; parents are created 0700 relative to their
    parent's descriptor; files are created exclusively without following links (0600, 0700 with an execute bit);
    nothing is chowned, timestamped or linked. On any failure the new directory is removed descriptor-relatively.
    Returns the pf_source manifest of the written bytes with ``source: {"kind": "unknown"}``."""
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    try:
        root_fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
        try:
            entries = _extract_into(fd, root_fd, inventory, limits)
        finally:
            os.close(root_fd)
    except BaseException:
        pf_instance.remove_private_tree_at(parent_fd, name)
        raise
    entries.sort(key=lambda item: item["path"])
    return {"schema_version": MANIFEST_SCHEMA, "source": {"kind": "unknown"}, "entries": entries}


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
