"""Fixture contract (SPEC section 3.1-3.3): pinned commits, the disposable upstream, base-image pins and the staged
control release with the PF-A3.2 fixture release-source shim."""
import hashlib
import json
import os
from pathlib import Path
import shutil

from . import util

OLD = "af771729426c0b5dd212fc8b46d0a9f901f7b32d"
NEW = "181a8064de7436459dcc02d0cbea6382bc3d3935"
FIXTURE_IDENTITY = {
    "GIT_AUTHOR_NAME": "PF-A3.4 fixture", "GIT_AUTHOR_EMAIL": "pfa34@fixture.invalid",
    "GIT_COMMITTER_NAME": "PF-A3.4 fixture", "GIT_COMMITTER_EMAIL": "pfa34@fixture.invalid",
    "GIT_AUTHOR_DATE": "2026-10-08T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-08T00:00:00Z",
}
NEXT_FILE = "PFA34_FIXTURE.txt"
NEXT_BYTES = b"next fixture revision\n"
BROKEN_PATH = "backend/app/main.py"
BROKEN_SUFFIX = b'\nraise RuntimeError("PF-A3.4 fixture: activation failure")\n'
BASE_IMAGES = ("postgres:16", "python:3.12-slim", "ghcr.io/astral-sh/uv:0.8.22", "node:24-alpine")
# Pinned the same way but outside the SPEC 5.4 equality set: the harness-owned unrelated project ``othersite``.
EXTRA_IMAGES = ("alpine:3.20",)
CTL_COMMENT = b"# PF-A3.4 control-upgrade fixture\n"
UPSTREAM_RELATIVE = "upstream/partflow.git"
REMOTE_URL = "file:///srv/pfa34/upstream/partflow.git"


def _git(git_dir, *args, env=None, input_bytes=None):
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/root", "GIT_CONFIG_NOSYSTEM": "1", "LANG": "C.UTF-8"}
    environment.update(env or {})
    result = util.run(["/usr/bin/git", "--git-dir=" + str(git_dir), *args], env=environment, input_bytes=input_bytes)
    if result.returncode != 0:
        raise util.HarnessError(f"git {' '.join(args)} failed: {result.stderr.strip()[:400]}")
    return result.stdout


def _derived_commit(upstream, parent, path, data, message, scratch):
    index = scratch / ("index-" + hashlib.sha256(message.encode()).hexdigest()[:8])
    env = dict(FIXTURE_IDENTITY, GIT_INDEX_FILE=str(index))
    _git(upstream, "read-tree", parent, env=env)
    blob = _git(upstream, "hash-object", "-w", "--stdin", env=env, input_bytes=data).strip()
    _git(upstream, "update-index", "--add", "--cacheinfo", "100644," + blob + "," + path, env=env)
    tree = _git(upstream, "write-tree", env=env).strip()
    commit = _git(upstream, "commit-tree", tree, "-p", parent, "-m", message, env=env).strip()
    index.unlink()
    return commit, blob


def build_upstream(root, source_git):
    """``/srv/pfa34/upstream/partflow.git`` from the scratch clone: OLD/NEW real, NEXT/BROKEN derived with a fixed
    identity and fixed dates (reproducible SHAs). Refs: main -> NEXT, pfa34-broken -> BROKEN, nothing else."""
    upstream = Path(root) / UPSTREAM_RELATIVE
    scratch = Path(root) / "fixture" / "git-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    if upstream.exists():
        shutil.rmtree(str(upstream))
    upstream.parent.mkdir(parents=True, exist_ok=True)
    result = util.run(["/usr/bin/git", "clone", "--quiet", "--bare", "--no-local", str(source_git), str(upstream)],
                      env={"PATH": "/usr/bin:/bin", "HOME": "/root", "GIT_CONFIG_NOSYSTEM": "1"})
    if result.returncode != 0:
        raise util.HarnessError("upstream clone failed: " + result.stderr.strip()[:400])
    for commit in (OLD, NEW):
        if _git(upstream, "cat-file", "-t", commit).strip() != "commit":
            raise util.HarnessError(f"BLOCKED: fixture: {commit} is not a commit in the scratch clone")
    _git(upstream, "merge-base", "--is-ancestor", OLD, NEW)  # raises unless OLD reaches NEW (linear OLD...NEW)
    next_commit, next_blob = _derived_commit(upstream, NEW, NEXT_FILE, NEXT_BYTES, "PF-A3.4 fixture: NEXT", scratch)
    main_py = _git_bytes(upstream, NEW + ":" + BROKEN_PATH)
    broken_bytes = main_py + BROKEN_SUFFIX
    broken_commit, broken_blob = _derived_commit(upstream, NEW, BROKEN_PATH, broken_bytes,
                                                 "PF-A3.4 fixture: BROKEN", scratch)
    for line in _git(upstream, "for-each-ref", "--format=%(refname)").splitlines():
        if line.strip():
            _git(upstream, "update-ref", "-d", line.strip())
    _git(upstream, "update-ref", "refs/heads/main", next_commit)
    _git(upstream, "update-ref", "refs/heads/pfa34-broken", broken_commit)
    _git(upstream, "symbolic-ref", "HEAD", "refs/heads/main")
    heads = {}
    for name, commit in (("OLD", OLD), ("NEW", NEW)):
        heads[name] = alembic_head(upstream, commit)
    return {
        "upstream": str(upstream), "remote": REMOTE_URL,
        "commits": {"OLD": OLD, "NEW": NEW, "NEXT": next_commit, "BROKEN": broken_commit},
        "refs": {"refs/heads/main": next_commit, "refs/heads/pfa34-broken": broken_commit},
        "identity": FIXTURE_IDENTITY,
        "patches": {
            "NEXT": {"path": NEXT_FILE, "bytes_sha256": hashlib.sha256(NEXT_BYTES).hexdigest(), "blob": next_blob,
                     "bytes": NEXT_BYTES.decode()},
            "BROKEN": {"path": BROKEN_PATH, "appended": BROKEN_SUFFIX.decode(),
                       "appended_sha256": hashlib.sha256(BROKEN_SUFFIX).hexdigest(),
                       "bytes_sha256": hashlib.sha256(broken_bytes).hexdigest(), "blob": broken_blob},
        },
        "alembic_heads": heads,
    }


def _git_bytes(git_dir, spec):
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/root", "GIT_CONFIG_NOSYSTEM": "1"}
    result = util.run(["/usr/bin/git", "--git-dir=" + str(git_dir), "cat-file", "blob", spec], env=environment,
                      binary=True)
    if result.returncode != 0:
        raise util.HarnessError(f"git cat-file {spec} failed")
    return result.stdout


def alembic_head(git_dir, commit):
    """The newest Alembic revision file name prefix of ``commit`` (read-only listing; the oracle reads the DB)."""
    listing = _git(git_dir, "ls-tree", "--name-only", commit + ":backend/alembic/versions")
    names = sorted(name for name in listing.splitlines() if name.endswith(".py"))
    return names[-1] if names else None


# ------------------------------------------------------------------------------------------------ base images


def pin_base_images(pins_path, evidence):
    """SPEC 2.2 / OD-A34-15: resolve by tag once (recording repo digests), or pull ``<repo>@<digest>`` and tag it."""
    pinned = None
    if pins_path is not None and Path(pins_path).exists():
        pinned = json.loads(Path(pins_path).read_text(encoding="utf-8"))["images"]
    result = {"schema_version": 1, "images": {}, "mode": "pulled-by-digest" if pinned else "resolved-by-tag",
              "equality_set": list(BASE_IMAGES), "extra": list(EXTRA_IMAGES)}
    for tag in BASE_IMAGES + EXTRA_IMAGES:
        if pinned:
            digest_ref = pinned[tag]
            util.docker_checked(["pull", "-q", digest_ref], timeout=1800)
            util.docker_checked(["tag", digest_ref, tag])
            image_id = util.docker_checked(["image", "inspect", "--format", "{{.Id}}", tag]).strip()
            result["images"][tag] = digest_ref
            result.setdefault("image_ids", {})[tag] = image_id
        else:
            util.docker_checked(["pull", "-q", tag], timeout=1800)
            digests = json.loads(util.docker_checked(["image", "inspect", "--format", "{{json .RepoDigests}}", tag]))
            repo = tag.rsplit(":", 1)[0]
            match = [item for item in digests if item.startswith(repo + "@")]
            if not match:
                raise util.HarnessError(f"BLOCKED: no repo digest for {tag}")
            result["images"][tag] = match[0]
            result.setdefault("image_ids", {})[tag] = util.docker_checked(
                ["image", "inspect", "--format", "{{.Id}}", tag]).strip()
    evidence.write_json("base-images.json", result)
    return result


# ---------------------------------------------------------------------------------------------- control stage


def _protected_fixture(work):
    import importlib.util
    import sys
    path = Path(work) / "deploy" / "synology" / "tests" / "protected_fixture.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("protected_fixture", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["protected_fixture"] = module
    spec.loader.exec_module(module)
    return module


def write_settings(path, commits, *, timeout_data=None, remote=REMOTE_URL):
    settings = {"github": {"commits/" + sha: {"sha": sha} for sha in commits},
                "remote": remote, "timeout_data": timeout_data}
    util.write_private(path, json.dumps(settings, indent=1, sort_keys=True) + "\n", mode=0o644)
    return settings


def stage_control(work, stage_dir, settings_path, *, ctl_candidate=False):
    """A repository-shaped candidate tree for ``install-control.sh init`` / ``pf install control --source``: the
    repository bytes with exactly the fixture release-source entry block (and, for ``ctl``, one appended comment
    line in compose.nas.yaml). Returns {name: sha256} of the release files and the recorded diffs."""
    pfx = _protected_fixture(work)
    files = pfx.fixture_release_files(settings_path)
    original = pfx.release_files()
    if ctl_candidate:
        files["compose.nas.yaml"] = files["compose.nas.yaml"] + CTL_COMMENT
    pf_install = pfx.pf.pf_install
    changed = sorted(name for name in files if files[name] != original[name])
    allowed = ["pf-admin.py"] + (["compose.nas.yaml"] if ctl_candidate else [])
    if changed != sorted(allowed):
        raise util.HarnessError(f"control stage: unexpected changed release files {changed}")
    entry = pfx.FIXTURE_MAIN.encode("utf-8")
    base = original["pf-admin.py"][:-len(entry)]
    if not files["pf-admin.py"].startswith(base) or not original["pf-admin.py"].endswith(entry):
        raise util.HarnessError("control stage: the shim changed more than the pf-admin.py entry block")
    stage_dir = Path(stage_dir)
    if stage_dir.exists():
        shutil.rmtree(str(stage_dir))
    layout = dict(pf_install.CONTROL_RELEASE_FILES)
    for name, relative in pf_install.BOOTSTRAP_FILES.items():
        layout.setdefault("bootstrap:" + name, relative)
    layout["installer"] = "deploy/synology/install-control.sh"
    hashes = {}
    for name, relative in sorted(layout.items()):
        data = files[name] if name in files else (Path(work) / relative).read_bytes()
        target = stage_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        for parent in [target.parent] + list(target.parent.parents):
            if parent == stage_dir.parent:
                break
            os.chmod(str(parent), 0o755)
        target.write_bytes(data)
        os.chmod(str(target), 0o755 if relative.endswith(".sh") or relative == "pf.sh" else 0o644)
        hashes[relative] = hashlib.sha256(data).hexdigest()
    shim = files["pf-admin.py"][len(base):]
    record = {"stage": str(stage_dir), "files": hashes, "settings": str(settings_path),
              "shim_sha256": hashlib.sha256(shim).hexdigest(), "shim": shim.decode("utf-8"),
              "pf_admin_repository_sha256": hashlib.sha256(original["pf-admin.py"]).hexdigest(),
              "changed_release_files": changed}
    if ctl_candidate:
        record["ctl_diff"] = CTL_COMMENT.decode()
        record["ctl_diff_sha256"] = hashlib.sha256(CTL_COMMENT).hexdigest()
    return record
