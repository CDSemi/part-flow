"""LOOP-02 / A3-T12 (SPEC 8.2 L2) with the A3-T06 purge-gate observations."""
import hashlib
import json
import os
from pathlib import Path

from . import checks
from . import faults
from . import phrases
from . import util
from .checks import Verdict
from .loops import _record, deployed_json, deployment_artifact, heads_prefix, update


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verification_records(inst, bundle_id):
    base = Path(inst.info["private_state"]) / "artifacts" / "verifications" / bundle_id
    found = []
    if base.is_dir():
        for path in sorted(base.rglob("*.json")):
            try:
                found.append((str(path), json.loads(path.read_text(encoding="utf-8"))))
            except ValueError:
                continue
    return found


def check_results(record):
    """{check id: entry} of a verification record (list or mapping form)."""
    value = record.get("checks") or record.get("functional_checks") or []
    result = {}
    if isinstance(value, dict):
        result.update(value)
    else:
        for item in value:
            if isinstance(item, dict):
                result[item.get("id") or item.get("check") or item.get("name")] = item
    return result


def invariants_clean(check):
    """The ``app-invariants`` functional check ran and both sides were clean ("source and restored equal:
    result=clean"); ``unavailable`` / ``not_run`` / failed never count."""
    if not isinstance(check, dict):
        return False
    detail = str(check.get("detail") or "")
    return check.get("result") == "passed" and "result=clean" in detail and "unavailable" not in detail


def project_events(h, window, project, actions, services=None):
    found = []
    for item in h.events_between(*window):
        attributes = (item.get("Actor") or {}).get("Attributes") or {}
        if item.get("Type") != "container" or attributes.get("com.docker.compose.project") != project:
            continue
        action = (item.get("Action") or "").split(":")[0]
        if action in actions and (services is None or attributes.get("com.docker.compose.service") in services):
            found.append({"time": item.get("timeNano"), "action": action,
                          "service": attributes.get("com.docker.compose.service"),
                          "id": (item.get("Actor") or {}).get("ID")})
    return found


def project_resources(project):
    """Containers, volumes or networks still labelled with ``project``."""
    found = []
    for argv in (["ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                 ["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                 ["network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"]):
        found += util.docker_checked(argv).split()
    return found


def event_projects(h, window, prefix):
    names = set()
    for item in h.events_between(*window):
        project = ((item.get("Actor") or {}).get("Attributes") or {}).get("com.docker.compose.project") or ""
        if project.startswith(prefix):
            names.add(project)
    return sorted(names)


def purge_bundle(journal):
    names = [item.get("name") for item in journal.get("retained_artifacts") or []
             if str(item.get("name", "")).startswith("purge-")]
    return names[-1] if names else None


def purge(h, inst, step_id, verdict, t06, *, pre=None):
    """``pf purge --keep-backups`` with the SPEC L2 step 2 / A3-T06 observations. Returns (bundle, result)."""
    env = inst.env()
    pre = pre or inst.identities(f"{step_id}-pre")
    allowed = phrases.exact("PURGE " + inst.project, "DELETE " + env["POSTGRES_DB"]) + \
        (r"ERASE " + inst.project + r" [0-9A-F]{6}",)
    result = checks.run_alpha_step(h, inst, ["purge", "--keep-backups"], step_id=step_id, phrases=allowed,
                                   timeout=7200, l5_exclude=(inst.slug,))
    op, plan, journal = checks.newest(inst, "purge")
    verdict.check(f"{step_id}: completed", journal.get("phase") == "completed", journal.get("phase"))
    bundle = purge_bundle(journal)
    verdict.check(f"{step_id}: final bundle", bundle is not None, journal.get("retained_artifacts"))
    h.evidence.write_json(f"records/{step_id}-journal.json", journal)
    h.evidence.write_json(f"records/{step_id}-plan.json", plan)
    operation_dir = h.operations_dir(inst.slug) / op
    frozen, progress = {}, None
    for name in ("deletion-plan.json", "deletion-progress.json"):
        path = operation_dir / name
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            h.evidence.write_json(f"records/{step_id}-{name}", value)
            if name == "deletion-plan.json":
                frozen = value
            else:
                progress = value
    bundle_dir = checks.find_bundle_dir(inst, bundle) if bundle else None
    manifest_path = bundle_dir / "manifest.json" if bundle_dir else None
    manifest_sha = sha_file(manifest_path) if manifest_path and manifest_path.exists() else None
    records = verification_records(inst, bundle) if bundle else []
    h.evidence.write_json(f"records/{step_id}-verification-records.json",
                          [{"path": path, "record": record} for path, record in records])
    functional = [(path, record) for path, record in records if record.get("level") == "functional_recovery_verified"]
    t06.check(f"{step_id}: a functional_recovery_verified record exists for the bundle", bool(functional),
              [path for path, _ in records])
    invariants = None
    if functional:
        path, record = functional[-1]
        text = json.dumps(record)
        t06.check(f"{step_id}: bound to the bundle's manifest hash", bool(manifest_sha) and manifest_sha in text,
                  {"manifest_sha256": manifest_sha, "record": path})
        for other in (h.fixture.get("B1"), h.fixture.get("B2"), h.fixture.get("X")):
            if other:
                t06.check(f"{step_id}: not bound to {other}", other not in text)
        invariants = check_results(record).get("app-invariants")
        t06.check(f"{step_id}: app-invariants ran clean/clean", invariants_clean(invariants), invariants)
    stops = project_events(h, result.window, inst.project, ("stop", "die", "kill"), ("backend", "frontend"))
    starts = project_events(h, result.window, inst.project, ("start",), ("backend", "frontend"))
    destroys = project_events(h, result.window, inst.project, ("destroy",))
    first_stop = min([item["time"] for item in stops] or [0])
    first_destroy = min([item["time"] for item in destroys] or [2 ** 63])
    verdict.check(f"{step_id}: writers stopped from capture through verification",
                  bool(stops) and not [item for item in starts if first_stop <= item["time"] <= first_destroy],
                  {"stops": len(stops), "starts": starts[:4]})
    projects = event_projects(h, result.window, "pfverify-")
    leftovers = [name for name in projects if project_resources(name)]
    verdict.check(f"{step_id}: pfverify topology created and fully removed", bool(projects) and not leftovers,
                  {"projects": projects, "leftovers": leftovers})
    post = inst.identities(f"{step_id}-post")
    deleted = {
        "containers": sorted({item["id"] for item in pre["containers"]} - {item["id"] for item in post["containers"]}),
        "volumes": sorted({item["name"] for item in pre["volumes"]} - {item["name"] for item in post["volumes"]}),
        "networks": sorted({item["id"] for item in pre["networks"]} - {item["id"] for item in post["networks"]}),
    }
    frozen_text = json.dumps(frozen)
    unplanned = [item for values in deleted.values() for item in values if item not in frozen_text]
    verdict.check(f"{step_id}: exactly the frozen deletion-plan items were deleted",
                  not unplanned and progress is not None, {"deleted": deleted, "unplanned": unplanned})
    record = h.instance_record(inst.slug) or {}
    verdict.check(f"{step_id}: registry state purged", record.get("state") == "purged", record.get("state"))
    inst.info["purged"] = True
    h.save()
    return bundle, result, invariants


def run(h):
    alpha = h.instance("alpha")
    seeder = alpha.seeder()
    verdict = Verdict("A3-T12")
    t06 = Verdict("A3-T06")
    # 1 W3, S4, identities(pre-purge)
    seeder.marker("W3", movement=True)
    s4 = alpha.snapshot("L2-S4")
    pre = alpha.identities("pre-purge")
    env_before = alpha.env()
    password_hash = sha_text(env_before.get("POSTGRES_PASSWORD", ""))
    instance_id = alpha.info["instance_id"]
    # 2 purge --keep-backups
    bundle, result, _ = purge(h, alpha, "L2-2-purge", verdict, t06, pre=pre)
    h.fixture["P"] = bundle
    verdict.check("L2-2: bravo/charlie/othersite unchanged", not result.l5_problems, result.l5_problems)
    kept = [name for name in (h.fixture.get("B1"), h.fixture.get("B2"))
            if name and checks.checkpoint_dir(alpha, name).is_dir()]
    verdict.check("L2-2: checkpoints kept", len(kept) == 2, kept)
    h.save()
    if bundle is None:
        _record(h, "L2", verdict)
        _record(h, "L2", t06)
        return "failed"
    # 3 remove .git; upstream away
    checks.remove_workspace_git(h, alpha)
    upstream = Path(h.fixture["upstream"])
    away = upstream.with_name(upstream.name + ".away")
    os.rename(str(upstream), str(away))
    h.external_event("upstream-away", f"mv {upstream} {away}", target=str(upstream), note="LOOP-02 remote absent")
    try:
        # 4 restore-instance P
        argv_log = faults.ArgvLog()
        allowed = phrases.exact("RESTORE INSTANCE " + alpha.project, "RESTORE " + env_before["POSTGRES_DB"] + " " + bundle)
        result = checks.run_alpha_step(h, alpha, ["restore-instance", bundle], step_id="L2-4-restore-instance",
                                       phrases=allowed, on_tick=argv_log.tick, timeout=7200)
        h.evidence.write_json("records/L2-4-argv-log.json", argv_log.entries())
        op, plan, journal = checks.newest(alpha, "restore-instance")
        verdict.check("L2-4: completed", journal.get("phase") == "completed", journal.get("phase"))
        verdict.check("L2-4: no store fetch and no remote git", not argv_log.matching("git", "fetch"),
                      [item["argv"] for item in argv_log.matching("git")][:5])
        record = h.instance_record("alpha") or {}
        verdict.check("L2-4: same UUID and project", record.get("instance_id") == instance_id and
                      record.get("compose_project") == alpha.project, record.get("instance_id"))
        verdict.check("L2-4: registry state registered", record.get("state") == "registered", record.get("state"))
        running = checks.running_images(alpha)
        pre_images = {item["service"]: item["image"] for item in pre["containers"]
                      if item.get("service") in ("backend", "frontend") and item.get("oneoff") != "True"}
        verdict.check("L2-4: image IDs equal the pre-purge IDs (loaded, not rebuilt)",
                      bool(pre_images) and all(running.get(key) == value for key, value in pre_images.items()) and
                      "image-build" not in checks.effect_types(plan),
                      {"running": running, "pre": pre_images, "effects": checks.effect_types(plan)})
        s_restored = alpha.snapshot("L2-after-restore")
        checks.equivalent(h, verdict, "L2-4: S == S4", s4, s_restored)
        verdict.check("L2-4: W3 present", "PFA34-W3" in checks.live_markers(alpha)["markers"])
        verdict.check("L2-4: heads 0032", heads_prefix(alpha) == "0032", alpha.heads())
        reconcile = alpha.reconcile("L2-4-restore")
        verdict.check("L2-4: reconcile 0", reconcile.get("exit") == 0, reconcile.get("stderr_summary"))
        verdict.check("L2-4: .env password hash equal",
                      sha_text(alpha.env().get("POSTGRES_PASSWORD", "")) == password_hash)
        line = next((item for item in result.stdout.splitlines() if "Application invariants:" in item), None)
        verdict.check("L2-4: printed Application invariants clean", line is not None and "clean" in line, line)
        deployed = deployed_json(alpha) or {}
        verdict.check("L2-4: deployment record written", bool(deployed.get("deployment_id")) and
                      deployment_artifact(alpha, deployed["deployment_id"]).is_dir(), deployed.get("deployment_id"))
        alpha.info["purged"] = False
        h.save()
        # 5 backup B3 with the upstream still away and no .git
        argv_log = faults.ArgvLog()
        checks.run_alpha_step(h, alpha, ["backup"], step_id="L2-5-backup", on_tick=argv_log.tick)
        h.evidence.write_json("records/L2-5-argv-log.json", argv_log.entries())
        op, plan, journal = checks.newest(alpha, "backup")
        verdict.check("L2-5: completed", journal.get("phase") == "completed", journal.get("phase"))
        b3 = (checks.retained(journal) or [None])[0]
        h.fixture["B3"] = b3
        verdict.check("L2-5: no fetch or remote git", not argv_log.matching("git", "fetch"),
                      [item["argv"] for item in argv_log.matching("git")][:5])
        verdict.check("L2-5: workspace still has no .git", not checks.workspace_git(alpha))
        if b3:
            probe = checks.probe_checkpoint(h, alpha, b3, "L2-B3")
            if probe.get("snapshot"):
                checks.equivalent(h, verdict, "L2-5: S(B3) == S4", s4, probe["snapshot"])
    finally:
        # 6 upstream back
        if away.exists() and not upstream.exists():
            os.rename(str(away), str(upstream))
            h.external_event("upstream-back", f"mv {away} {upstream}", target=str(upstream))
    verdict.check("L2-6: workspace still has no .git", not checks.workspace_git(alpha))
    # 7 update NEXT without migrations
    result, op, plan, journal = update(h, alpha, "NEXT", "L2-7-update", verdict, migrations=False)
    verdict.check("L2-7: no migration effect", "database-migrate" not in checks.effect_types(plan),
                  checks.effect_types(plan))
    s_next = alpha.snapshot("L2-after-next")
    checks.equivalent(h, verdict, "L2-7: S == S4", s4, s_next)
    h.fixture["L2_7_workspace_git"] = checks.workspace_git(alpha)
    ok = _record(h, "L2", verdict)
    ok06 = _record(h, "L2", t06)
    h.results["cases"]["A3-T12"] = {"status": "passed" if ok else "failed", "level": "docker_postgresql",
                                    "failures": verdict.failures}
    h.results["cases"]["A3-T06-L2"] = {"status": "passed" if ok06 else "failed", "failures": t06.failures}
    return "passed" if ok and ok06 else "failed"
