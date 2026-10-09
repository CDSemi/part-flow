"""PF-A3.4 host side: the host-daemon command allowlist, the guarded ``docker`` call and the inventory proof.

The host Docker daemon is shared with the developer's ``partflow`` stack and other sessions. The isolated
integration run touches it only through ``guarded_docker()``: every argv is checked against the PF-A3.4 SPEC
section 2.1 allowlist (``check_argv``) and appended to ``host-commands.jsonl`` (argv, exit, start/end) before
anything else happens. ``snapshot`` records containers, images, volumes and networks with read-only calls through
the same guard; ``diff`` classifies the before/after change (harness / protected / foreign-concurrent).

Pure logic (``check_argv``, ``classify``) is Docker-free and covered offline by tests/test_integration_harness.py.
Python 3.9 grammar; no shell strings; every refusal fails closed.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SCHEMA_VERSION = 1
RUN_LABEL = "io.partflow.pfa34.run"
RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\Z")
DIND_TAG = "docker:28.5.1-dind"
DIND_DIGEST_RE = re.compile(r"docker:28\.5\.1-dind@sha256:[0-9a-f]{64}\Z")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX_ID_RE = re.compile(r"[0-9a-f]{12,64}\Z")
# Read-only object names the inspect family may take ("inspect ... of any ID"): IDs, volume/network names, tags.
OBJECT_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}\Z")
FORMAT_RE = re.compile(r"[^\x00-\x1f]{1,512}\Z")
SUPERVISOR_SCRIPT = "/pfa34/work/dind-entry.sh"
KEEPALIVE = 'trap "exit 0" TERM; while :; do sleep 3600; done'
PROTECTED_PROJECTS = ("partflow", "agentmemory")
FORBIDDEN_WORDS = ("partflow", "agentmemory")
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
CONTAINER_PATH_RE = re.compile(r"/[A-Za-z0-9._/-]{0,511}\Z")


class Refused(Exception):
    """An argv outside the allowlist (host safety rule violation)."""


def container_name(run):
    return "pfa34-" + run


def run_label(run):
    return RUN_LABEL + "=" + run


def _require_run(run):
    if not isinstance(run, str) or not RUN_ID_RE.fullmatch(run):
        raise Refused(f"invalid run id {run!r}")


def _no_foreign_names(argv, run):
    """Rule 2: no mutating command names partflow, agentmemory or another resource (the run label is ours)."""
    label = run_label(run)
    for token in argv:
        stripped = token.replace(label, "")
        lowered = stripped.lower()
        for word in FORBIDDEN_WORDS:
            if word in lowered:
                raise Refused(f"argument {token!r} names {word!r}")


def _host_path_ok(path, raw_dir):
    """A host-side ``docker cp`` path: under the run's raw directory, never on B:."""
    if raw_dir is None:
        return False
    text = str(path).replace("\\", "/")
    raw = str(raw_dir).replace("\\", "/").rstrip("/")
    if text.upper().startswith("B:") or raw.upper().startswith("B:"):
        return False
    if "/../" in text + "/" or text.endswith("/.."):
        return False
    return text == raw or text.startswith(raw + "/")


def check_argv(argv, *, run, state=None, raw_dir=None):
    """Return the allowlist form name of ``argv`` (the arguments after ``docker``), or raise Refused.

    ``state``: {"dind_present_before": bool, "pulled_by_run": bool, "dind_ref": "docker:28.5.1-dind@sha256:…",
    "dind_image_id": "sha256:…"}. ``raw_dir``: the host raw directory ``docker cp`` may read from or write to.
    """
    _require_run(run)
    state = state or {}
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(item, str) for item in argv):
        raise Refused("argv must be a non-empty list of strings")
    argv = list(argv)
    for token in argv:
        if "\x00" in token or "\n" in token or "\r" in token:
            raise Refused("control character in an argument")
    name = container_name(run)
    label = run_label(run)
    head = argv[0]
    rest = argv[1:]

    # -- read-only forms -------------------------------------------------------------------------------------
    if head in ("version", "info"):
        if rest == [] or (len(rest) == 2 and rest[0] == "--format" and FORMAT_RE.fullmatch(rest[1])):
            return "read:" + head
        raise Refused(f"{head}: only an optional --format is allowed")
    listing = {
        "ps": ["-a", "--no-trunc", "--format"],
        "images": ["--no-trunc", "--format"],
    }
    if head in listing:
        expected = listing[head]
        if rest[:-1] == expected and len(rest) == len(expected) + 1 and FORMAT_RE.fullmatch(rest[-1]):
            return "read:" + head
        raise Refused(f"{head}: only '{' '.join(expected)} <format>' is allowed")
    if head in ("volume", "network") and rest[:1] == ["ls"]:
        expected = ["ls", "--format"] if head == "volume" else ["ls", "--no-trunc", "--format"]
        if rest[:-1] == expected and len(rest) == len(expected) + 1 and FORMAT_RE.fullmatch(rest[-1]):
            return "read:" + head + "-ls"
        raise Refused(f"{head} ls: only '{' '.join(expected)} <format>' is allowed")
    inspect_family = {"inspect": 1, "volume": 2, "network": 2, "image": 2}
    if head == "inspect" or (head in ("volume", "network", "image") and rest[:1] == ["inspect"]):
        tail = argv[inspect_family[head]:]
        if len(tail) >= 2 and tail[0] == "--format":
            if not FORMAT_RE.fullmatch(tail[1]):
                raise Refused("inspect: invalid --format")
            tail = tail[2:]
        if not tail:
            raise Refused("inspect: an object reference is required")
        for ref in tail:
            if ref.startswith("-") or not OBJECT_REF_RE.fullmatch(ref):
                raise Refused(f"inspect: invalid object reference {ref!r}")
        return "read:" + (head if head == "inspect" else head + "-inspect")

    # -- every remaining form is mutating or executes inside the run container: no foreign names --------------
    _no_foreign_names(argv, run)

    if head == "pull":
        if rest == [DIND_TAG]:
            if state.get("dind_present_before", True):
                raise Refused("pull: the dind image was present before the run; nothing is pulled")
            return "pull:dind"
        raise Refused("pull: only docker:28.5.1-dind")
    if head == "network":
        if rest == ["create", "--label", label, name]:
            return "network:create"
        if rest == ["rm", name]:
            return "network:rm"
        raise Refused("network: only 'create --label <run label> pfa34-<run>' and 'rm pfa34-<run>'")
    if head == "run":
        dind_ref = state.get("dind_ref")
        if not dind_ref or not DIND_DIGEST_RE.fullmatch(dind_ref):
            raise Refused("run: the dind image digest is not resolved")
        expected = ["-d", "--privileged", "--name", name, "--label", label, "--network", name,
                    "--mount", "type=volume,dst=/pfa34,volume-label=" + label,
                    "-e", "DOCKER_TLS_CERTDIR=", "--entrypoint", "/bin/sh", dind_ref, "-c", KEEPALIVE]
        if rest == expected:
            return "run:dind"
        raise Refused("run: only the exact pinned dind form of SPEC 2.1")
    if head == "cp":
        if len(rest) != 2:
            raise Refused("cp: exactly a source and a destination")
        source, destination = rest
        prefix = name + ":"
        if destination.startswith(prefix) and not source.startswith(prefix):
            inner = destination[len(prefix):]
            if not CONTAINER_PATH_RE.fullmatch(inner) or "/../" in inner + "/":
                raise Refused("cp: invalid container path")
            if source != "-" and not _host_path_ok(source, raw_dir):
                raise Refused("cp: the host source must be '-' or under the run's raw directory")
            return "cp:in"
        if source.startswith(prefix) and not destination.startswith(prefix):
            inner = source[len(prefix):]
            if not CONTAINER_PATH_RE.fullmatch(inner) or "/../" in inner + "/":
                raise Refused("cp: invalid container path")
            if not _host_path_ok(destination, raw_dir):
                raise Refused("cp: the host destination must be under the run's raw directory")
            return "cp:out"
        raise Refused("cp: only into or out of pfa34-<run>")
    if head == "exec":
        if rest[:2] == ["-d", name]:
            if rest[2:] == [SUPERVISOR_SCRIPT]:
                return "exec:supervisor"
            raise Refused("exec -d: only the supervisor start")
        if rest[:1] == ["-i"]:
            rest = rest[1:]
        if rest[:1] == [name] and len(rest) >= 2 and not rest[1].startswith("-"):
            return "exec"
        raise Refused("exec: only 'exec [-i] pfa34-<run> <command>'")
    if head == "rm":
        if rest == ["-f", "-v", name]:
            return "rm:dind"
        raise Refused("rm: only 'rm -f -v pfa34-<run>'")
    if head == "image" and rest[:1] == ["rm"]:
        image_id = state.get("dind_image_id")
        if state.get("pulled_by_run") and image_id and IMAGE_ID_RE.fullmatch(image_id) and rest == ["rm", image_id]:
            return "image:rm-dind"
        raise Refused("image rm: only the dind image, and only when this run pulled it")
    raise Refused(f"{head}: not in the host allowlist")


# ------------------------------------------------------------------------------------------- guarded execution


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_state(path):
    if path is None or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_state(path, state):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes((json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.replace(str(temp), str(path))


def append_log(log, record):
    with open(str(log), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def guarded_docker(argv, *, run, log, state=None, raw_dir=None, stdin_path=None, stdout=None, capture=True,
                   timeout=None):
    """Check ``argv`` against the allowlist, record it, run ``docker <argv>`` (list argv, no shell).

    Returns a CompletedProcess. A refused argv is recorded with ``refused`` and raises Refused; nothing runs."""
    started = _utc()
    try:
        form = check_argv(argv, run=run, state=state, raw_dir=raw_dir)
    except Refused as exc:
        append_log(log, {"argv": ["docker"] + list(argv), "refused": str(exc), "start": started, "end": _utc()})
        raise
    stdin = open(str(stdin_path), "rb") if stdin_path else subprocess.DEVNULL
    try:
        if stdout is not None:
            result = subprocess.run(["docker"] + list(argv), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
                                    check=False, timeout=timeout)
        else:
            result = subprocess.run(["docker"] + list(argv), stdin=stdin,
                                    stdout=subprocess.PIPE if capture else None,
                                    stderr=subprocess.PIPE if capture else None, check=False, timeout=timeout)
    finally:
        if stdin_path:
            stdin.close()
    append_log(log, {"argv": ["docker"] + list(argv), "form": form, "exit": result.returncode, "start": started,
                     "end": _utc()})
    return result


def _text(result):
    data = result.stdout or b""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def _checked(result, what):
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else result.stderr
        raise RuntimeError(f"{what} failed (exit {result.returncode}): {(error or '').strip()[:400]}")
    return _text(result)


# ------------------------------------------------------------------------------------------------- snapshot


def _chunks(items, size=50):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def take_snapshot(*, run, log, state=None):
    """The host inventory through read-only allowlisted calls only (SPEC 2.3)."""
    call = lambda argv, what: _checked(guarded_docker(argv, run=run, log=log, state=state), what)  # noqa: E731
    snapshot = {"schema_version": SCHEMA_VERSION, "run": run, "taken_at": _utc(),
                "containers": {}, "images": {}, "volumes": {}, "networks": {}}
    ids = sorted(set(line.strip() for line in call(["ps", "-a", "--no-trunc", "--format", "{{.ID}}"], "ps").splitlines()
                     if line.strip()))
    for chunk in _chunks(ids):
        for item in json.loads(call(["inspect"] + chunk, "inspect containers")):
            labels = (item.get("Config") or {}).get("Labels") or {}
            stateinfo = item.get("State") or {}
            snapshot["containers"][item["Id"]] = {
                "name": (item.get("Name") or "").lstrip("/"), "image": item.get("Image"), "labels": labels,
                "status": stateinfo.get("Status"), "started_at": stateinfo.get("StartedAt"),
                "restart_count": item.get("RestartCount"), "project": labels.get(COMPOSE_PROJECT_LABEL)}
    images = sorted(set(line.strip() for line in call(["images", "--no-trunc", "--format", "{{.ID}}"], "images")
                        .splitlines() if line.strip()))
    for chunk in _chunks(images):
        for item in json.loads(call(["image", "inspect"] + chunk, "image inspect")):
            snapshot["images"][item["Id"]] = {"repo_tags": sorted(item.get("RepoTags") or []),
                                              "repo_digests": sorted(item.get("RepoDigests") or []),
                                              "created": item.get("Created"),
                                              "labels": (item.get("Config") or {}).get("Labels") or {}}
    volumes = sorted(set(line.strip() for line in call(["volume", "ls", "--format", "{{.Name}}"], "volume ls")
                         .splitlines() if line.strip()))
    for chunk in _chunks(volumes):
        for item in json.loads(call(["volume", "inspect"] + chunk, "volume inspect")):
            labels = item.get("Labels") or {}
            snapshot["volumes"][item["Name"]] = {"driver": item.get("Driver"), "labels": labels,
                                                 "created_at": item.get("CreatedAt"),
                                                 "mountpoint": item.get("Mountpoint"),
                                                 "project": labels.get(COMPOSE_PROJECT_LABEL)}
    networks = sorted(set(line.strip() for line in call(["network", "ls", "--no-trunc", "--format", "{{.ID}}"],
                                                         "network ls").splitlines() if line.strip()))
    for chunk in _chunks(networks):
        for item in json.loads(call(["network", "inspect"] + chunk, "network inspect")):
            labels = item.get("Labels") or {}
            snapshot["networks"][item["Id"]] = {"name": item.get("Name"), "driver": item.get("Driver"),
                                                "labels": labels, "project": labels.get(COMPOSE_PROJECT_LABEL)}
    return snapshot


# ----------------------------------------------------------------------------------------------------- diff


KINDS = ("containers", "images", "volumes", "networks")
COMPARED = {
    "containers": ("name", "image", "labels", "status", "started_at", "restart_count"),
    "images": ("repo_tags", "repo_digests"),
    "volumes": ("driver", "labels", "created_at", "mountpoint"),
    "networks": ("name", "driver", "labels"),
}


def _is_harness(kind, key, item, run, state):
    labels = item.get("labels") or {}
    if labels.get(RUN_LABEL) == run:
        return True
    if kind == "volumes" and key in (state.get("anonymous_volumes") or []):
        return True
    if kind == "images" and state.get("pulled_by_run") and key == state.get("dind_image_id"):
        return True
    return False


def _protected_project(item):
    project = item.get("project")
    if project in PROTECTED_PROJECTS:
        return True
    labels = item.get("labels") or {}
    return any(word in (item.get("name") or "") for word in PROTECTED_PROJECTS) or \
        labels.get(COMPOSE_PROJECT_LABEL) in PROTECTED_PROJECTS


def _named_by(commands, key, item):
    """Host commands that named this resource (by ID, short ID or name) in a mutating form."""
    names = {key, key[:12]} | ({item["name"]} if item.get("name") else set())
    if key.startswith("sha256:"):
        names |= {key[7:], key[7:19]}
    hits = []
    for record in commands:
        form = record.get("form") or ""
        if form.startswith("read:"):
            continue
        if any(token in names for token in record.get("argv", [])[1:]):
            hits.append(record.get("argv"))
    return hits


def classify(before, after, *, run, state=None, commands=()):
    """SPEC 2.3 classification. Returns {"result": "clean"|"FAIL host-leak"|"FAIL host-touched", "harness": [...],
    "protected_changes": [...], "foreign_concurrent": [...], "leaks": [...]}.

    - harness: carries the run label, is the recorded anonymous volume, or is the dind image pulled by this run;
      it must be absent after teardown (else FAIL host-leak).
    - protected: every resource present before the run (and the partflow/agentmemory projects): it must be
      identical after (else FAIL host-touched). A pre-existing image whose removed tags now name an image created
      during the run is a concurrent retag (foreign-concurrent), never ours (the run creates no host tag).
    - foreign-concurrent: created or removed during the run and not ours (a removed protected-project resource
      still fails); ``commands`` must show no mutating command that named it.
    """
    state = state or {}
    report = {"schema_version": SCHEMA_VERSION, "run": run, "harness": [], "leaks": [], "protected_changes": [],
              "foreign_concurrent": [], "commands_naming_foreign": []}
    for kind in KINDS:
        old, new = before.get(kind) or {}, after.get(kind) or {}
        for key in sorted(set(old) | set(new)):
            item_before, item_after = old.get(key), new.get(key)
            item = item_after if item_after is not None else item_before
            if _is_harness(kind, key, item, run, state):
                entry = {"kind": kind, "id": key, "present_before": item_before is not None,
                         "present_after": item_after is not None}
                report["harness"].append(entry)
                if item_after is not None:
                    report["leaks"].append(entry)
                continue
            if item_before is not None and item_after is not None:
                changed = [field for field in COMPARED[kind] if item_before.get(field) != item_after.get(field)]
                if not changed:
                    continue
                if kind == "images" and set(changed) <= {"repo_tags", "repo_digests"}:
                    removed = set(item_before.get("repo_tags") or []) - set(item_after.get("repo_tags") or [])
                    added = set(item_after.get("repo_tags") or []) - set(item_before.get("repo_tags") or [])
                    retagged = all(any(tag in (other.get("repo_tags") or []) for other_key, other in new.items()
                                       if other_key not in old) for tag in removed)
                    if removed and not added and retagged:
                        report["foreign_concurrent"].append({"kind": kind, "id": key, "change": "retagged",
                                                             "tags": sorted(removed)})
                        continue
                report["protected_changes"].append({"kind": kind, "id": key, "fields": changed,
                                                    "before": {field: item_before.get(field) for field in changed},
                                                    "after": {field: item_after.get(field) for field in changed}})
                continue
            if item_before is not None:  # removed during the run
                if _protected_project(item_before):
                    report["protected_changes"].append({"kind": kind, "id": key, "fields": ["removed"],
                                                        "before": {"present": True}, "after": {"present": False}})
                    continue
                report["foreign_concurrent"].append({"kind": kind, "id": key, "change": "removed",
                                                     "name": item_before.get("name")})
            else:  # created during the run, not ours
                report["foreign_concurrent"].append({"kind": kind, "id": key, "change": "created",
                                                     "name": item_after.get("name")})
            named = _named_by(commands, key, item)
            if named:
                report["commands_naming_foreign"].append({"kind": kind, "id": key, "commands": named})
    if report["leaks"]:
        report["result"] = "FAIL host-leak"
    elif report["protected_changes"] or report["commands_naming_foreign"]:
        report["result"] = "FAIL host-touched"
    else:
        report["result"] = "clean"
    return report


# ------------------------------------------------------------------------------------------------ input tar


PACK_SKIP_DIRS = ("__pycache__",)
PACK_SKIP_SUFFIXES = (".pyc", ".pyo")
EXECUTABLE_SUFFIXES = (".sh",)


def pack_inputs(repo, source_git, out, base_images=None):
    """The tar of the fixture inputs (SPEC 2.1 rule 3) with explicit, platform-independent modes: the OPS tree
    (``deploy/synology`` incl. ``integration/``, ``pf.sh``, ``compose.nas.yaml``), ``dind-entry.sh`` at the top and
    the scratch bare clone as ``source.git/``. Returns {path: sha256} of the OPS files (the control/harness tree)."""
    import hashlib
    import tarfile
    repo = Path(repo)
    hashes = {}

    def add_file(tar, path, arcname, mode):
        data = path.read_bytes()
        info = tarfile.TarInfo(arcname)
        info.size, info.mode, info.uid, info.gid, info.mtime = len(data), mode, 0, 0, 0
        info.uname = info.gname = "root"
        import io
        tar.addfile(info, io.BytesIO(data))
        return hashlib.sha256(data).hexdigest()

    def add_dir(tar, arcname):
        info = tarfile.TarInfo(arcname)
        info.type, info.mode, info.uid, info.gid, info.mtime = tarfile.DIRTYPE, 0o755, 0, 0, 0
        info.uname = info.gname = "root"
        tar.addfile(info)

    def walk(tar, base, prefix, record):
        add_dir(tar, prefix)
        for entry in sorted(base.iterdir(), key=lambda item: item.name):
            arc = prefix + "/" + entry.name
            if entry.is_symlink():
                raise RuntimeError(f"pack: symlink {entry} refused")
            if entry.is_dir():
                if entry.name in PACK_SKIP_DIRS:
                    continue
                walk(tar, entry, arc, record)
            elif entry.is_file():
                if entry.name.endswith(PACK_SKIP_SUFFIXES):
                    continue
                mode = 0o755 if entry.name.endswith(EXECUTABLE_SUFFIXES) or entry.name == "pf-admin.py" else 0o644
                digest = add_file(tar, entry, arc, mode)
                if record:
                    hashes[arc] = digest
    with tarfile.open(str(out), "w", format=tarfile.PAX_FORMAT) as tar:
        add_dir(tar, "deploy")
        walk(tar, repo / "deploy" / "synology", "deploy/synology", True)
        hashes["pf.sh"] = add_file(tar, repo / "pf.sh", "pf.sh", 0o755)
        hashes["compose.nas.yaml"] = add_file(tar, repo / "compose.nas.yaml", "compose.nas.yaml", 0o644)
        add_file(tar, repo / "deploy" / "synology" / "integration" / "dind-entry.sh", "dind-entry.sh", 0o755)
        walk(tar, Path(source_git), "source.git", False)
        if base_images:
            # OD-A34-15: the digests the first environment resolved; this environment pulls them by digest.
            hashes["base-images.json"] = add_file(tar, Path(base_images), "base-images.json", 0o644)
    return hashes


# ------------------------------------------------------------------------------------------------------- CLI


def _read_commands(path):
    if not path or not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="host_inventory.py", allow_abbrev=False)
    parser.add_argument("--run", required=True)
    parser.add_argument("--raw", required=True, help="The run's host raw directory (log and state live here)")
    sub = parser.add_subparsers(dest="verb", required=True)
    docker = sub.add_parser("docker", allow_abbrev=False)
    docker.add_argument("--stdin-file")
    docker.add_argument("--stdout-file")
    docker.add_argument("--stream", action="store_true", help="Inherit stdout/stderr (long exec)")
    docker.add_argument("argv", nargs=argparse.REMAINDER)
    snap = sub.add_parser("snapshot", allow_abbrev=False)
    snap.add_argument("--out", required=True)
    diff = sub.add_parser("diff", allow_abbrev=False)
    diff.add_argument("--before", required=True)
    diff.add_argument("--after", required=True)
    diff.add_argument("--out", required=True)
    pack = sub.add_parser("pack", allow_abbrev=False)
    pack.add_argument("--repo", required=True)
    pack.add_argument("--source-git", required=True)
    pack.add_argument("--out", required=True)
    pack.add_argument("--base-images", default=None)
    sub.add_parser("state-get", allow_abbrev=False).add_argument("key")
    setter = sub.add_parser("state-set", allow_abbrev=False)
    setter.add_argument("key")
    setter.add_argument("value")
    setter.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    run = args.run
    try:
        _require_run(run)
    except Refused as exc:
        print("BLOCKED: " + str(exc), file=sys.stderr)
        return 2
    raw = Path(args.raw)
    if str(raw).replace("\\", "/").upper().startswith("B:"):
        print("BLOCKED: the run directory must not be on B:", file=sys.stderr)
        return 2
    raw.mkdir(parents=True, exist_ok=True)
    log = raw / "host-commands.jsonl"
    state_path = raw / "run-state.json"
    state = load_state(state_path)
    if args.verb == "state-get":
        value = state.get(args.key)
        if value is None:
            return 1
        print(value if isinstance(value, str) else json.dumps(value))
        return 0
    if args.verb == "state-set":
        state[args.key] = json.loads(args.value) if args.json else args.value
        save_state(state_path, state)
        return 0
    if args.verb == "pack":
        try:
            hashes = pack_inputs(args.repo, args.source_git, args.out, args.base_images)
        except (OSError, RuntimeError) as exc:
            print("BLOCKED: pack: " + str(exc), file=sys.stderr)
            return 2
        import hashlib
        tree = hashlib.sha256("".join(f"{name}\0{digest}\n" for name, digest in sorted(hashes.items()))
                              .encode("utf-8")).hexdigest()
        (raw / "inputs-tree.json").write_bytes((json.dumps({"files": hashes, "tree_sha256": tree}, indent=1,
                                                           sort_keys=True) + "\n").encode("utf-8"))
        print(tree)
        return 0
    if args.verb == "docker":
        command = list(args.argv)
        if command[:1] == ["--"]:
            command = command[1:]
        try:
            if args.stream:
                result = guarded_docker(command, run=run, log=log, state=state, raw_dir=raw,
                                        stdin_path=args.stdin_file, capture=False)
            elif args.stdout_file:
                with open(args.stdout_file, "wb") as out:
                    result = guarded_docker(command, run=run, log=log, state=state, raw_dir=raw,
                                            stdin_path=args.stdin_file, stdout=out)
                if result.returncode != 0:
                    sys.stderr.write(result.stderr.decode("utf-8", "replace"))
            else:
                result = guarded_docker(command, run=run, log=log, state=state, raw_dir=raw,
                                        stdin_path=args.stdin_file)
                sys.stdout.write(result.stdout.decode("utf-8", "replace"))
                sys.stderr.write(result.stderr.decode("utf-8", "replace"))
        except Refused as exc:
            print("BLOCKED: host-allowlist: " + str(exc), file=sys.stderr)
            return 2
        return result.returncode
    if args.verb == "snapshot":
        try:
            snapshot = take_snapshot(run=run, log=log, state=state)
        except (Refused, RuntimeError, ValueError) as exc:
            print("BLOCKED: snapshot: " + str(exc), file=sys.stderr)
            return 2
        Path(args.out).write_bytes((json.dumps(snapshot, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        return 0
    if args.verb == "diff":
        before = json.loads(Path(args.before).read_text(encoding="utf-8"))
        after = json.loads(Path(args.after).read_text(encoding="utf-8"))
        report = classify(before, after, run=run, state=state, commands=_read_commands(log))
        Path(args.out).write_bytes((json.dumps(report, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print(report["result"])
        return 0 if report["result"] == "clean" else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
