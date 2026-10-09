"""LOOP-07 / A3-T15 side-by-side isolation (SPEC 8.2 L7) on alpha after L2."""
import hashlib
import json

from . import checks
from . import noeffect
from . import observe
from . import phrases
from . import util
from .checks import Verdict
from .loop2 import check_results, invariants_clean, verification_records
from .loops import _record

EGRESS_PROBE = (
    "import socket, sys\n"
    "results = {}\n"
    "for name, host, port in (('gateway', sys.argv[1], 5432), ('gateway-http', sys.argv[1], 80),\n"
    "                         ('internet', '1.1.1.1', 443)):\n"
    "    try:\n"
    "        socket.create_connection((host, port), timeout=5).close()\n"
    "        results[name] = 'connected'\n"
    "    except OSError as exc:\n"
    "        results[name] = 'failed: ' + type(exc).__name__\n"
    "for name in sys.argv[2:]:\n"
    "    try:\n"
    "        results['resolve:' + name] = socket.gethostbyname(name)\n"
    "    except OSError as exc:\n"
    "        results['resolve:' + name] = 'failed: ' + type(exc).__name__\n"
    "print(__import__('json').dumps(results))\n")


def sha(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def container_env(container_id):
    env = {}
    for entry in util.docker_json(["inspect", container_id])[0]["Config"].get("Env") or []:
        key, _, value = entry.partition("=")
        env[key] = value
    return env


def target_projects():
    names = set()
    for line in util.docker_checked(["ps", "-a", "--format", "{{.Label \"com.docker.compose.project\"}}"]).split():
        if line.startswith("pfrecover-"):
            names.add(line)
    return sorted(names)


def default_gateway():
    value = util.docker_json(["network", "inspect", "bridge"])[0]
    configs = (value.get("IPAM") or {}).get("Config") or []
    return configs[0].get("Gateway") if configs else "172.17.0.1"


def run(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A3-T15")
    bundle, b3 = h.fixture.get("P"), h.fixture.get("B3")
    listing = alpha.pf(["recoveries"], step_id="L7-0-recoveries", expect_exit=None)
    verdict.check("L7 precondition: pf recoveries lists P", bool(bundle) and bundle in listing.stdout,
                  listing.stdout[-800:])
    # 1 negative check: a checkpoint is not a side-by-side source
    if b3:
        result, record = noeffect.refused(h, alpha, ["restore-instance", b3, "--side-by-side"],
                                          step_id="L7-1-checkpoint-side-by-side",
                                          expect_text="Specify one exact purge recovery ID.")
        verdict.check("L7-1: B3 --side-by-side refused with the purge-ID copy",
                      record["refused"] and record["expected_text_seen"], result.stderr[-400:])
        verdict.check("L7-1: N holds", record["result"] == "passed", record)
    # 2 side-by-side restore of P while alpha runs
    pre = alpha.identities("L7-pre")
    s_pre = alpha.snapshot("L7-pre")
    alpha_backend = alpha.backend_container()
    alpha_env = container_env(alpha_backend) if alpha_backend else {}
    before_projects = set(target_projects())
    result = checks.run_alpha_step(h, alpha, ["restore-instance", bundle, "--side-by-side"],
                                   step_id="L7-2-side-by-side", phrases=phrases.exact("RESTORE COPY " + bundle),
                                   timeout=7200)
    op, plan, journal = checks.newest(alpha, "restore-side-by-side")
    verdict.check("L7-2: completed", journal.get("phase") == "completed", journal.get("phase"))
    projects = [name for name in target_projects() if name not in before_projects]
    verdict.check("L7-2: one new pfrecover-* project", len(projects) == 1, projects)
    target = projects[0] if projects else None
    h.fixture["L7_target"] = target
    if target:
        identity = observe.project_identities(target)
        h.evidence.write_json("identities/L7-target.json", identity)
        alpha_volumes = {item["name"] for item in pre["volumes"]}
        verdict.check("L7-2: distinct project volumes", identity["volumes"] and
                      not {item["name"] for item in identity["volumes"]} & alpha_volumes,
                      [item["name"] for item in identity["volumes"]])
        verdict.check("L7-2: every target network is internal", identity["networks"] and
                      all(item["internal"] for item in identity["networks"]), identity["networks"])
        details = util.docker_json(["inspect", *[item["id"] for item in identity["containers"]]])
        published, binds, restart, by_id = [], [], [], []
        for item in details:
            ports = (item.get("NetworkSettings") or {}).get("Ports") or {}
            if any(value for value in ports.values()) or (item.get("HostConfig") or {}).get("PortBindings"):
                published.append(item["Name"])
            binds += [mount for mount in item.get("Mounts") or [] if mount.get("Type") == "bind"]
            restart.append(((item.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name"))
            by_id.append((item.get("Config") or {}).get("Image"))
        verdict.check("L7-2: no published port", not published, published)
        verdict.check("L7-2: no bind mount", not binds, binds)
        verdict.check("L7-2: restart no", all(value in ("no", "") for value in restart), restart)
        verdict.check("L7-2: images by ID", all(str(value).startswith("sha256:") for value in by_id), by_id)
        records = verification_records(alpha, bundle)
        mine = [(path, record) for path, record in records if target in json.dumps(record)]
        h.evidence.write_json("records/L7-verification-records.json", [{"path": p, "record": r} for p, r in mine])
        invariants = check_results(mine[-1][1]).get("app-invariants") if mine else None
        verdict.check("L7-2: target verification app-invariants clean/clean", invariants_clean(invariants),
                      invariants)
        backend = observe.running_container(target, "backend")
        db = observe.running_container(target, "db")
        if backend:
            probe = util.docker(["exec", backend, "python", "-c", EGRESS_PROBE, default_gateway(),
                                 alpha_backend and util.docker_checked(["inspect", "--format", "{{.Name}}",
                                                                        alpha_backend]).strip().lstrip("/") or
                                 "partflow-backend-1", "partflow-db-1"], timeout=120)
            try:
                egress = json.loads(probe.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                egress = {"error": probe.stdout[-300:] + probe.stderr[-300:]}
            h.evidence.write_json("records/L7-egress.json", egress)
            verdict.check("L7-2: egress to the dind gateway and 1.1.1.1:443 fails",
                          all(str(egress.get(key, "")).startswith("failed") for key in
                              ("gateway", "gateway-http", "internet")), egress)
            verdict.check("L7-2: alpha's container names do not resolve",
                          all(str(value).startswith("failed") for key, value in egress.items()
                              if key.startswith("resolve:")), egress)
            target_env = container_env(backend)
            db_env = container_env(db) if db else {}
            alpha_values = alpha.env()
            verdict.check("L7-2: no shared credentials",
                          sha(db_env.get("POSTGRES_PASSWORD")) != sha(alpha_values.get("POSTGRES_PASSWORD")) and
                          sha(target_env.get("DATABASE_URL")) != sha(alpha_env.get("DATABASE_URL")) and
                          bool(db_env.get("POSTGRES_PASSWORD")),
                          {"target_db_password_sha8": sha(db_env.get("POSTGRES_PASSWORD"))[:8],
                           "alpha_password_sha8": sha(alpha_values.get("POSTGRES_PASSWORD"))[:8]})
        else:
            verdict.check("L7-2: target backend running", False)
        shared = []
        for path in list(h.pfroot.joinpath("registry").rglob("*.json")) + list(h.pfroot.glob("instances/*/record.json")):
            if target in path.read_text(encoding="utf-8", errors="replace"):
                shared.append(str(path))
        for crontab in ("/etc/crontabs/root", "/var/spool/cron/crontabs/root", "/etc/crontab"):
            try:
                if target in open(crontab, encoding="utf-8").read():
                    shared.append(crontab)
            except OSError:
                pass
        verdict.check("L7-2: no shared jobs (no registry record, scheduler entry or job file names the target)",
                      not shared, shared)
    post = alpha.identities("L7-post")
    verdict.check("L7-2: alpha containers, StartedAt unchanged",
                  observe.stable_identity(pre) == observe.stable_identity(post))
    verdict.check("L7-2: alpha health", all(item.get("health") in ("healthy", None) for item in post["containers"]
                                            if item.get("oneoff") != "True"))
    s_post = alpha.snapshot("L7-post")
    checks.equivalent(h, verdict, "L7-2: alpha S unchanged", s_pre, s_post)
    verdict.check("L7-2: alpha listener answers", alpha.health() == 200)
    verdict.check("L7-2: L5 unchanged", not result.l5_problems, result.l5_problems)
    # 3 cleanup report, then apply for the target
    report = alpha.pf(["cleanup"], step_id="L7-3-cleanup-report", expect_exit=None)
    verdict.check("L7-3: cleanup report names the target", bool(target) and target in report.stdout,
                  report.stdout[-600:])
    if target:
        allowed = (r"CLEANUP " + alpha.project + r" [0-9a-f]{8}",) + phrases.exact("REMOVE RECOVERY TARGET " + target)
        result = checks.run_alpha_step(h, alpha, ["cleanup", "--apply", "--recovery-target", target],
                                       step_id="L7-3-cleanup-apply", phrases=allowed)
        leftovers = []
        for argv in (["ps", "-aq", "--filter", f"label=com.docker.compose.project={target}"],
                     ["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={target}"],
                     ["network", "ls", "-q", "--filter", f"label=com.docker.compose.project={target}"]):
            leftovers += util.docker_checked(argv).split()
        verdict.check("L7-3: the target is fully removed", result.exit == 0 and not leftovers, leftovers)
        after = alpha.identities("L7-after-cleanup")
        verdict.check("L7-3: nothing of alpha touched", observe.stable_identity(post) == observe.stable_identity(after))
    ok = _record(h, "L7", verdict)
    h.results["cases"]["A3-T15"] = {"status": "passed" if ok else "failed", "level": "docker_integration",
                                    "failures": verdict.failures}
    return "passed" if ok else "failed"
