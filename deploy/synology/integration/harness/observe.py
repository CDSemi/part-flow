"""Oracle collection against the dind daemon (SPEC 3.5): snapshots, reconcile, identities, health, the
preserved-data probe. Read-only on pf-managed resources; every PostgreSQL session is one short ``docker exec``
that ends before the next pf command."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

from . import oracles
from . import util

SCRATCH_LABEL = "io.partflow.pfa34.scratch=1"
SCRATCH_NETWORK = "pfa34-scratch"
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_ONEOFF = "com.docker.compose.oneoff"

SNAPSHOT_GEXEC = ("SELECT format($q$SELECT 'D', %L, count(*)::text, md5(coalesce(string_agg(t::text, E'\\n' "
                  "ORDER BY t::text COLLATE \"C\"), '')) FROM public.%I t$q$, c.relname, c.relname) "
                  "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                  "AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version' ORDER BY c.relname \\gexec")


def env_values(path):
    """The editable .env read as data (KEY=VALUE, comments skipped); a read-only oracle input."""
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def sha256_file(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def containers(project, *, service=None, all_states=True, oneoff=None):
    argv = ["ps", "-q", "--no-trunc", "--filter", f"label={COMPOSE_PROJECT}={project}"]
    if all_states:
        argv.insert(1, "-a")
    if service:
        argv += ["--filter", f"label={COMPOSE_SERVICE}={service}"]
    if oneoff is not None:
        argv += ["--filter", f"label={COMPOSE_ONEOFF}={'True' if oneoff else 'False'}"]
    return util.docker_checked(argv).split()


def running_container(project, service):
    found = [item for item in containers(project, service=service, all_states=False, oneoff=False)]
    return found[0] if found else None


def snapshot_script():
    lines = [oracles.session_prelude()]
    lines.append("SHOW TimeZone;\nSHOW DateStyle;\nSHOW IntervalStyle;\nSHOW extra_float_digits;\n")
    lines.append(oracles.HEADS_SQL + ";\n")
    lines.append(SNAPSHOT_GEXEC + "\n")
    for sql in (oracles.TRIGGERS_SQL, oracles.CONSTRAINTS_SQL, oracles.INDEXES_SQL, oracles.FUNCTIONS_SQL,
                oracles.COLUMNS_SQL):
        lines.append(sql + ";\n")
    lines.append("COMMIT;\n")
    return "".join(lines)


def psql(container, user, database, script, *, timeout=600):
    argv = ["exec", "-i", container, "psql", "-X", "-q", "-A", "-t", "-F", oracles.SEPARATOR, "-v", "ON_ERROR_STOP=1",
            "-U", user, "-d", database]
    return util.docker(argv, input_bytes=script.encode("utf-8"), timeout=timeout)


def snapshot_container(container, user, database, *, label, instance):
    result = psql(container, user, database, snapshot_script())
    if result.returncode != 0:
        raise util.HarnessError(f"snapshot {label} failed: {result.stderr.strip()[:600]}")
    value = oracles.parse_snapshot(result.stdout, label=label, instance=instance)
    value["taken_at"] = util.utc()
    value["database"] = database
    return value


def heads_container(container, user, database):
    result = psql(container, user, database, oracles.HEADS_SQL + ";\n", timeout=120)
    if result.returncode != 0:
        return None
    return sorted(line.split(oracles.SEPARATOR)[1] for line in result.stdout.splitlines() if line.startswith("H"))


def server_version(container, user, database):
    result = psql(container, user, database, "SHOW server_version;\n", timeout=60)
    return result.stdout.strip() if result.returncode == 0 else None


def load_installed_pf_config(pfroot):
    """RECONCILE_ARGV from the installed release's pf_config (SPEC 3.5 Invariants): imported, never copied."""
    releases = sorted(Path(pfroot, "releases").iterdir())
    conf = Path(pfroot, "bootstrap", "bootstrap.conf").read_text(encoding="utf-8")
    release = next(line.split("=", 1)[1] for line in conf.splitlines() if line.startswith("control_release="))
    path = Path(release) / "pf_config.py"
    if not path.exists():
        path = releases[-1] / "pf_config.py"
    spec = importlib.util.spec_from_file_location("installed_pf_config", path)
    module = importlib.util.module_from_spec(spec)
    import sys
    # never write __pycache__ into the installed release: the bootstrap refuses unlisted control files (seen in a
    # dev run where an inspection script imported pf_config without -B)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.dont_write_bytecode = previous
    return module


def reconcile_container(container, argv):
    result = util.docker(["exec", container, *argv], timeout=600)
    stdout = result.stdout[:8192]
    summary = next((line for line in reversed(result.stderr.strip().splitlines()) if line.strip()), "")
    report = None
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = None
    return {"argv": list(argv), "exit": result.returncode, "stderr_summary": summary[:500], "stdout": stdout,
            "result": (report or {}).get("result") if isinstance(report, dict) else None}


def project_identities(project):
    """Resource identity of one Compose project (SPEC 3.5 Resource identity)."""
    value = {"project": project, "containers": [], "volumes": [], "networks": [], "images": []}
    ids = containers(project)
    images = set()
    if ids:
        for item in util.docker_json(["inspect", *ids]):
            state = item.get("State") or {}
            labels = (item.get("Config") or {}).get("Labels") or {}
            value["containers"].append({
                "id": item["Id"], "name": item["Name"].lstrip("/"), "image": item["Image"],
                "service": labels.get(COMPOSE_SERVICE), "oneoff": labels.get(COMPOSE_ONEOFF),
                "status": state.get("Status"), "started_at": state.get("StartedAt"),
                "restart_count": item.get("RestartCount"), "health": (state.get("Health") or {}).get("Status"),
                "env_sha256": {entry.split("=", 1)[0]: hashlib.sha256(entry.encode()).hexdigest()[:16]
                               for entry in (item.get("Config") or {}).get("Env") or []
                               if entry.split("=", 1)[0] in ("POSTGRES_PASSWORD", "DATABASE_URL", "SITE_TIMEZONE")}})
            images.add(item["Image"])
    for name in util.docker_checked(["volume", "ls", "-q", "--filter", f"label={COMPOSE_PROJECT}={project}"]).split():
        item = util.docker_json(["volume", "inspect", name])[0]
        value["volumes"].append({"name": item["Name"], "labels": item.get("Labels") or {},
                                 "created_at": item.get("CreatedAt"), "mountpoint": item.get("Mountpoint")})
    for network_id in util.docker_checked(["network", "ls", "-q", "--no-trunc", "--filter",
                                           f"label={COMPOSE_PROJECT}={project}"]).split():
        item = util.docker_json(["network", "inspect", network_id])[0]
        value["networks"].append({"id": item["Id"], "name": item["Name"], "internal": item.get("Internal"),
                                  "labels": item.get("Labels") or {}})
    for image in sorted(images):
        item = util.docker_json(["image", "inspect", image])[0]
        value["images"].append({"id": item["Id"], "tags": sorted(item.get("RepoTags") or [])})
    for key in ("containers", "volumes", "networks", "images"):
        value[key] = sorted(value[key], key=lambda entry: json.dumps(entry, sort_keys=True))
    return value


def stable_identity(value):
    """The comparable part of an identity record (container IDs, StartedAt, RestartCount, volumes, networks)."""
    return {
        "containers": sorted((item["id"], item["name"], item["image"], item["status"], item["started_at"],
                              item["restart_count"]) for item in value["containers"]),
        "volumes": sorted((item["name"], item["created_at"], item["mountpoint"]) for item in value["volumes"]),
        "networks": sorted((item["id"], item["name"]) for item in value["networks"]),
    }


def wait_no_container(project, service, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not running_container(project, service):
            return True
        time.sleep(1)
    return False


# --------------------------------------------------------------------------------------- preserved-data probe


def ensure_scratch_network():
    existing = util.docker_checked(["network", "ls", "-q", "--filter", f"name=^{SCRATCH_NETWORK}$"]).split()
    if not existing:
        util.docker_checked(["network", "create", "--internal", "--label", SCRATCH_LABEL, SCRATCH_NETWORK])


def probe_dump(dump_path, *, label, instance, image="postgres:16", reconcile_image=None, reconcile_argv=None,
               extra_sql=None):
    """Restore a pf-produced custom-format dump into a harness-owned scratch PostgreSQL container (pinned image,
    internal network, scratch label, removed afterwards) and take S, F and heads there."""
    ensure_scratch_network()
    name = "pfa34-scratch-" + hashlib.sha256((label + str(time.time())).encode()).hexdigest()[:10]
    password = hashlib.sha256(os.urandom(16)).hexdigest()
    util.docker_checked(["run", "-d", "--name", name, "--label", SCRATCH_LABEL, "--network", SCRATCH_NETWORK,
                         "-e", "POSTGRES_USER=scratch", "-e", "POSTGRES_PASSWORD=" + password, "-e",
                         "POSTGRES_DB=scratch", image])
    result = {"label": label, "dump": str(dump_path), "dump_sha256": sha256_file(dump_path)}
    try:
        util.wait_until(lambda: util.docker(["exec", name, "pg_isready", "-U", "scratch", "-d", "scratch"],
                                            timeout=20).returncode == 0, timeout=120, interval=1,
                        what="scratch postgres ready")
        time.sleep(2)
        util.wait_until(lambda: util.docker(["exec", name, "pg_isready", "-U", "scratch", "-d", "scratch"],
                                            timeout=20).returncode == 0, timeout=60, interval=1,
                        what="scratch postgres ready after init")
        restore = util.docker(["exec", "-i", name, "pg_restore", "-U", "scratch", "-d", "scratch", "--no-owner",
                               "--no-privileges", "--exit-on-error"], input_bytes=Path(dump_path).read_bytes(),
                              timeout=1800)
        result["restore_exit"] = restore.returncode
        result["restore_stderr"] = restore.stderr[-1000:]
        if restore.returncode != 0:
            result["result"] = "restore-failed"
            return result
        snapshot = snapshot_container(name, "scratch", "scratch", label=label, instance=instance)
        result["snapshot"] = snapshot
        result["heads"] = snapshot["heads"]
        if extra_sql:
            extra = psql(name, "scratch", "scratch", extra_sql, timeout=120)
            grouped = {}
            for line in extra.stdout.splitlines():
                fields = line.split(oracles.SEPARATOR)
                if len(fields) == 2:
                    grouped.setdefault(fields[0], []).append(fields[1])
            result["extra"] = grouped
            result["markers"] = grouped.get("W", [])
        if callable(reconcile_image):
            reconcile_image = reconcile_image(snapshot["heads"])
            result["reconcile_image"] = reconcile_image
        if reconcile_image and reconcile_argv:
            url = f"postgresql+psycopg://scratch:{password}@{name}:5432/scratch"
            run = util.docker(["run", "--rm", "--label", SCRATCH_LABEL, "--network", SCRATCH_NETWORK, "-e",
                               "DATABASE_URL=" + url, "-e", "SITE_TIMEZONE=UTC", reconcile_image, *reconcile_argv],
                              timeout=900)
            result["reconcile"] = {"exit": run.returncode, "stdout": run.stdout[:8192],
                                   "stderr_summary": (run.stderr.strip().splitlines() or [""])[-1][:500]}
        result["result"] = "ok"
        return result
    finally:
        util.docker(["rm", "-f", "-v", name], timeout=120)
