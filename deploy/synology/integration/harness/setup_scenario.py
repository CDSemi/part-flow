"""Scenario ``setup`` (SPEC 8.1): fixture, install, alpha OLD + D0, othersite, bravo (A2-T04 registration part),
charlie."""
from pathlib import Path
import time

from . import instances as instances_module
from . import observe
from . import oracles
from . import util

OTHERSITE_DIR = "othersite"
OTHERSITE_COMPOSE = """name: othersite
services:
  app:
    image: othersite/app:1
    build: .
    restart: unless-stopped
    volumes:
      - data:/data
    networks:
      - net
volumes:
  data: {}
networks:
  net: {}
"""
OTHERSITE_DOCKERFILE = """FROM alpine:3.20
CMD ["sh", "-c", "echo 'PF-A3.4 othersite fixture' > /data/fixture.txt && exec sleep 2147483647"]
"""
PRECONDITION_SQL = (
    "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
    "SELECT 'S', count(*)::text FROM assigned_route_steps;\n"
    "SELECT 'M', count(*)::text FROM part_movements WHERE assigned_route_step_id IS NOT NULL;\n"
    "SELECT 'A', entity_type, count(*)::text FROM audit_events GROUP BY entity_type ORDER BY entity_type;\n"
    "COMMIT;\n")


def othersite_container():
    found = util.docker_checked(["ps", "-q", "--filter", "label=com.docker.compose.project=othersite",
                                 "--filter", "label=com.docker.compose.service=app"]).split()
    return found[0] if found else None


def othersite_file_hash():
    container = othersite_container()
    if container is None:
        return None
    result = util.docker(["exec", container, "sha256sum", "/data/fixture.txt"], timeout=30)
    return result.stdout.split()[0] if result.returncode == 0 and result.stdout.strip() else None


def othersite_up(harness):
    directory = harness.root / OTHERSITE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "compose.yaml").write_text(OTHERSITE_COMPOSE, encoding="utf-8")
    (directory / "Dockerfile").write_text(OTHERSITE_DOCKERFILE, encoding="utf-8")
    result = util.run([util.DOCKER, "compose", "--project-directory", str(directory), "-f",
                       str(directory / "compose.yaml"), "up", "-d", "--build"], timeout=900)
    harness.external_event("othersite", "docker compose -p othersite up -d --build", target="othersite",
                           note=f"exit {result.returncode}")
    if result.returncode != 0:
        raise util.HarnessError("othersite up failed: " + result.stderr.strip()[-600:])
    return util.wait_until(othersite_file_hash, timeout=60, what="othersite fixture file")


def precondition_counts(instance):
    env = instance.env()
    result = observe.psql(instance.db_container(), env["POSTGRES_USER"], env["POSTGRES_DB"], PRECONDITION_SQL)
    if result.returncode != 0:
        raise util.HarnessError("precondition counts failed: " + result.stderr.strip()[:400])
    counts = {"assigned_route_steps": 0, "part_movements_with_route_step": 0, "audit_events": {}}
    for line in result.stdout.splitlines():
        fields = line.split(oracles.SEPARATOR)
        if fields[0] == "S":
            counts["assigned_route_steps"] = int(fields[1])
        elif fields[0] == "M":
            counts["part_movements_with_route_step"] = int(fields[1])
        elif fields[0] == "A":
            counts["audit_events"][fields[1]] = int(fields[2])
    return counts


def deploy_and_check(harness, instance, commit_name, *, head_prefix):
    instance.deploy(commit_name, step_id=f"setup-{instance.slug}-deploy-{commit_name}")
    instance.info["deployed"] = commit_name
    harness.save()
    instance.wait_healthy()
    heads = instance.heads()
    if not heads or not heads[0].startswith(head_prefix):
        raise util.HarnessError(f"BLOCKED: fixture: {instance.slug} heads {heads} after deploy {commit_name}")
    check = instance.reconcile(f"{instance.slug}-after-deploy-{commit_name}")
    if check["exit"] != 0:
        raise util.HarnessError(f"BLOCKED: fixture: reconcile on {instance.slug} exit {check['exit']}: "
                                f"{check.get('stderr_summary')}")
    return heads


def server_version(instance):
    env = instance.env()
    return observe.server_version(instance.db_container(), env["POSTGRES_USER"], env["POSTGRES_DB"])


def record_environment(harness):
    """The inner half of environment.json (SPEC 6.3): engine, Compose, kernel, tools, capacity devices."""
    def text(argv):
        result = util.run(argv, timeout=60)
        return (result.stdout or result.stderr).strip()
    info = util.docker_json(["info", "--format", "{{json .}}"])
    value = {
        "engine": {key: info.get(key) for key in ("ID", "ServerVersion", "Driver", "DockerRootDir", "CgroupDriver",
                                                 "CgroupVersion", "KernelVersion", "OperatingSystem", "Architecture",
                                                 "NCPU", "MemTotal", "SecurityOptions")},
        "compose": text([util.DOCKER, "compose", "version"]),
        "kernel": text(["uname", "-a"]),
        "python": text(["python3", "--version"]),
        "git": text(["git", "--version"]),
        "psql": text(["psql", "--version"]),
        "iproute2": text(["/sbin/ip", "-V"]),
        "acl": text(["getfacl", "--version"]),
        "e2fsprogs": text(["mke2fs", "-V"]),
        "docker_root_device": text(["df", "-P", "/var/lib/docker"]),
        "data_device": text(["df", "-P", "/srv/pfa34"]),
    }
    for name in ("capacity-mode.txt", "loop-devices.txt", "pfa34-free-kib.txt", "apk-versions.txt",
                 "dockerd-starts.log"):
        path = Path("/pfa34/evidence/env") / name
        value[name] = path.read_text(encoding="utf-8")[-6000:] if path.exists() else None
    harness.evidence.write_json("environment-inner.json", value)
    return value


def run(harness):
    from .scenarios import setup_fixture, install
    record_environment(harness)
    setup_fixture(harness)
    install(harness)
    harness.pf(["install", "status"], step_id="setup-install-status")
    alpha = harness.instance("alpha")
    if not alpha.info.get("deployed"):
        alpha.register()
        deploy_and_check(harness, alpha, "OLD", head_prefix="0031")
    if not alpha.info.get("seed", {}).get("d0_flows"):
        alpha.seeder().d0()
    counts = precondition_counts(alpha)
    harness.fixture["d0_precondition"] = counts
    if counts["assigned_route_steps"] < 1 or counts["part_movements_with_route_step"] < 1:
        raise util.HarnessError("BLOCKED: fixture: the D0 seed did not reach assigned_route_steps / a Movement "
                                "with assigned_route_step_id (history sub-oracle blocked)")
    d0 = alpha.snapshot("D0")
    history = oracles.history_set(d0)
    problems = oracles.check_history_set(history, "0031")
    if problems:
        raise util.HarnessError(problems[0])
    harness.log("V: second idle snapshot in 30 s")
    time.sleep(30)
    d0_idle = alpha.snapshot("D0-idle")
    volatile = oracles.volatile_set(d0, d0_idle, history)
    harness.fixture.update({"history_0031": history, "volatile": volatile, "F_0031": d0["schema"]["sha256"],
                            "server_version": {"alpha": server_version(alpha)}})
    harness.save()
    if othersite_file_hash() is None:
        harness.fixture["othersite_hash"] = othersite_up(harness)
    pre_bravo = alpha.identities("pre-bravo")
    default_before = (harness.registry() or {}).get("default_instance_id")
    start = time.time()
    bravo = harness.instance("bravo")
    if not bravo.info.get("deployed"):
        bravo.register()
        deploy_and_check(harness, bravo, "OLD", head_prefix="0031")
        bravo.seeder().d0(small=True)
    end = time.time()
    post_bravo = alpha.identities("post-bravo")
    events = [item for item in harness.events_between(start, end)
              if item.get("Type") == "container"
              and ((item.get("Actor") or {}).get("Attributes") or {}).get("com.docker.compose.project") == alpha.project
              and (item.get("Action") or "").split(":")[0] in ("stop", "start", "die", "kill", "create", "destroy")]
    a2t04 = {
        "identities_equal": observe.stable_identity(pre_bravo) == observe.stable_identity(post_bravo),
        "env_equal": pre_bravo["env_sha256"] == post_bravo["env_sha256"],
        "record_equal": pre_bravo["record_sha256"] == post_bravo["record_sha256"],
        "default_unchanged": default_before == (harness.registry() or {}).get("default_instance_id"),
        "alpha_container_events": events,
    }
    a2t04["result"] = "passed" if all(a2t04[key] for key in ("identities_equal", "env_equal", "record_equal",
                                                              "default_unchanged")) and not events else "failed"
    harness.results["cases"]["A2-T04-registration"] = a2t04
    harness.record_step("setup-A2-T04-registration", result=a2t04["result"], extra={"detail": a2t04})
    charlie = harness.instance("charlie")
    if not charlie.info.get("deployed"):
        charlie.register()
        deploy_and_check(harness, charlie, "OLD", head_prefix="0031")
        charlie.seeder().d0(small=True)
    bravo.snapshot("D0b")
    charlie.snapshot("D0c")
    harness.fixture["othersite_hash"] = othersite_file_hash()
    locks = {}
    for path in sorted((harness.pfroot / "locks").glob("*.lock")) + sorted((harness.pfroot / "sources").glob("*.lock")):
        locks[str(path)] = instances_module.lock_identity(path)
    harness.fixture["locks"] = locks
    for slug in ("bravo", "charlie"):
        harness.fixture["server_version"][slug] = server_version(harness.instance(slug))
    harness.save()
    return "passed"
