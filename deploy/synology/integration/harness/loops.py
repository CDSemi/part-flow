"""LOOP-01 ... LOOP-07 (SPEC 8.2)."""
import json
from pathlib import Path

from . import checks
from . import phrases
from . import util
from .checks import Verdict

ROLLBACK_LOSS_COPY = "Newer writes will no longer appear in the active app"


def _record(h, loop, verdict, case=None, extra=None):
    value = verdict.as_record()
    if extra:
        value.update(extra)
    h.results["loops"].setdefault(loop, {}).setdefault("verdicts", []).append(value)
    h.evidence.append_jsonl("verdicts.jsonl", dict(value, loop=loop))
    h.record_step(f"{loop}-{verdict.name}", result="passed" if verdict.ok else "failed",
                  reason=None if verdict.ok else json.dumps(verdict.failures)[:2000])
    h.save()
    return verdict.ok


def _snapshot_of(h, label):
    value = h.evidence.read_json(f"snapshots/{label}.json")
    if value is None:
        raise util.HarnessError(f"missing reference snapshot {label}")
    return value


def update_args(h, name, *, migrations=True):
    sha = h.commit(name)
    argv = ["update", "--commit", sha, "--skip-ci"]
    if migrations:
        argv.insert(3, "--allow-migrations")
    return argv, phrases.exact("UPDATE " + sha[:12])


def heads_prefix(inst):
    heads = inst.heads() or []
    return heads[0][:4] if heads else None


def deployed_json(inst):
    path = Path(inst.info["private_state"]) / "state" / "deployed.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def deployment_artifact(inst, deployment_id):
    return Path(inst.info["private_state"]) / "artifacts" / "deployments" / deployment_id


def backup(h, inst, step_id, verdict):
    result = checks.run_alpha_step(h, inst, ["backup"], step_id=step_id)
    op, plan, journal = checks.newest(inst, "backup")
    verdict.check(f"{step_id}: completed", journal.get("phase") == "completed", journal.get("phase"))
    names = checks.retained(journal)
    verdict.check(f"{step_id}: one checkpoint", len(names) == 1, names)
    level = next((line.split(":", 1)[1].strip() for line in result.stdout.splitlines()
                  if line.startswith("Verification level:")), None)
    verdict.check(f"{step_id}: verification level recorded", bool(level), level)
    verdict.check(f"{step_id}: L5 unchanged", not result.l5_problems, result.l5_problems)
    verdict.check(f"{step_id}: lock", result.lock.get("result") == "ok", result.lock)
    checkpoint = names[0] if names else None
    if checkpoint:
        man = checks.manifest(checks.checkpoint_dir(inst, checkpoint))
        verdict.check(f"{step_id}: manifest present", man is not None)
    return checkpoint, result, level


def update(h, inst, name, step_id, verdict, *, migrations=True, from_head="0031", to_head="0032"):
    argv, allowed = update_args(h, name, migrations=migrations)
    result = checks.run_alpha_step(h, inst, argv, step_id=step_id, phrases=allowed)
    op, plan, journal = checks.newest(inst, "update")
    verdict.check(f"{step_id}: completed", journal.get("phase") == "completed", journal.get("phase"))
    verdict.check(f"{step_id}: heads {to_head}", heads_prefix(inst) == to_head, inst.heads())
    deployed = deployed_json(inst) or {}
    verdict.check(f"{step_id}: deployed.json names {name}", deployed.get("sha") == h.commit(name), deployed.get("sha"))
    artifact = deployment_artifact(inst, deployed.get("deployment_id") or "-")
    verdict.check(f"{step_id}: source artifact sealed for {name}", artifact.is_dir(), str(artifact))
    verdict.check(f"{step_id}: L5 unchanged", not result.l5_problems, result.l5_problems)
    verdict.check(f"{step_id}: lock", result.lock.get("result") == "ok", result.lock)
    reconcile = inst.reconcile(f"{step_id}")
    verdict.check(f"{step_id}: reconcile 0", reconcile.get("exit") == 0, reconcile.get("stderr_summary"))
    return result, op, plan, journal


# -------------------------------------------------------------------------------------------------------- L1


def loop1(h):
    """LOOP-01 / A3-T11 on alpha after setup."""
    alpha = h.instance("alpha")
    seeder = alpha.seeder()
    verdict = Verdict("A3-T11")
    d0 = _snapshot_of(h, "D0")
    # 1 backup B1
    b1, _, level = backup(h, alpha, "L1-1-backup", verdict)
    h.fixture["B1"] = b1
    p_b1 = checks.probe_checkpoint(h, alpha, b1, "L1-B1")
    verdict.check("L1-1: probe B1 restored", p_b1.get("result") == "ok", p_b1.get("restore_stderr"))
    if p_b1.get("snapshot"):
        checks.equivalent(h, verdict, "L1-1: S(B1) == S(D0)", d0, p_b1["snapshot"])
    # 2 W1
    seeder.marker("W1", movement=True)
    s1 = alpha.snapshot("L1-S1")
    # 3 update NEW with migrations
    images_before = checks.image_ids()
    result, op, plan, journal = update(h, alpha, "NEW", "L1-3-update", verdict)
    targets = [str(item.get("target")) for item in plan.get("effects") or []]
    verdict.check("L1-3: rehearsal pf_migrate_* planned", any("pf_migrate_" in target for target in targets),
                  [target for target in targets if "pf_migrate" in target or "migrate" in target][:6])
    live = checks.live_markers(alpha)
    verdict.check("L1-3: rehearsal database dropped", not [name for name in live["databases"]
                                                           if name.startswith("pf_migrate_")], live["databases"])
    s2 = alpha.snapshot("L1-S2")
    checks.equivalent(h, verdict, "L1-3: S2 == S1 [F: 0031->0032]", s1, s2, migration="0031->0032")
    h.fixture["F_0032"] = s2["schema"]["sha256"]
    from . import oracles
    h.fixture["history_0032"] = oracles.history_set(s2)
    verdict.check("L1-3: H(0032) complete", not oracles.check_history_set(h.fixture["history_0032"], "0032"),
                  h.fixture["history_0032"])
    running = checks.running_images(alpha)
    new_images = sorted(checks.image_ids() - images_before)
    verdict.check("L1-3: new images built once and running",
                  running.get("backend") in new_images and running.get("frontend") in new_images and
                  len([item for item in result.new_images if item in (running.get("backend"),
                                                                      running.get("frontend"))]) == 2,
                  {"new": new_images, "running": running})
    # 4 W2
    seeder.marker("W2", movement=True)
    alpha.snapshot("L1-S3")
    # 5 rollback B1 --restore-db
    env = alpha.env()
    phrase = "RESTORE " + env["POSTGRES_DB"] + " " + b1
    images_before = checks.image_ids()
    result = checks.run_alpha_step(h, alpha, ["rollback", b1, "--restore-db"], step_id="L1-5-rollback",
                                   phrases=phrases.exact(phrase))
    op, plan, journal = checks.newest(alpha, "rollback")
    verdict.check("L1-5: completed", journal.get("phase") == "completed", journal.get("phase"))
    verdict.check("L1-5: confirmation states the loss of newer writes", ROLLBACK_LOSS_COPY in result.stdout)
    verdict.check("L1-5: heads 0031", heads_prefix(alpha) == "0031", alpha.heads())
    live_snapshot = alpha.snapshot("L1-after-rollback")
    if p_b1.get("snapshot"):
        checks.equivalent(h, verdict, "L1-5: live S == S(B1)", p_b1["snapshot"], live_snapshot)
    live = checks.live_markers(alpha)
    verdict.check("L1-5: W1 and W2 absent live", not [m for m in live["markers"] if m in ("PFA34-W1", "PFA34-W2")],
                  live["markers"])
    b1_manifest = checks.manifest(checks.checkpoint_dir(alpha, b1)) or {}
    running = checks.running_images(alpha)
    verdict.check("L1-5: running images are B1's", all(running.get(service) == (b1_manifest.get("images") or {})
                                                       .get(service, {}).get("id") for service in ("backend", "frontend")),
                  {"running": running, "b1": {key: value.get("id") for key, value in
                                              (b1_manifest.get("images") or {}).items()}})
    verdict.check("L1-5: no build", "image-build" not in checks.effect_types(plan) and not result.new_images,
                  {"effects": checks.effect_types(plan), "new_images": result.new_images})
    captures = checks.retained(journal)
    verdict.check("L1-5: current-data capture retained", bool(captures), captures)
    if captures:
        capture = checks.probe_checkpoint(h, alpha, captures[0], "L1-rollback-capture")
        verdict.check("L1-5: capture holds W1 and W2", {"PFA34-W1", "PFA34-W2"} <= set(capture.get("markers") or []),
                      capture.get("markers"))
        verdict.check("L1-5: capture at 0032 with F(0032)",
                      (capture.get("heads") or ["?"])[0].startswith("0032") and
                      (capture.get("snapshot") or {}).get("schema", {}).get("sha256") == h.fixture["F_0032"],
                      capture.get("heads"))
    reconcile = alpha.reconcile("L1-5-rollback")
    verdict.check("L1-5: reconcile 0", reconcile.get("exit") == 0, reconcile.get("stderr_summary"))
    verdict.check("L1-5: L5 unchanged", not result.l5_problems, result.l5_problems)
    verdict.check("L1-5: lock", result.lock.get("result") == "ok", result.lock)
    # 6 remove .git
    existed = checks.remove_workspace_git(h, alpha)
    h.fixture["L1_git_existed"] = existed
    # 7 backup B2
    b2, _, _ = backup(h, alpha, "L1-7-backup", verdict)
    h.fixture["B2"] = b2
    p_b2 = checks.probe_checkpoint(h, alpha, b2, "L1-B2")
    if p_b2.get("snapshot") and p_b1.get("snapshot"):
        checks.equivalent(h, verdict, "L1-7: S(B2) == S(B1)", p_b1["snapshot"], p_b2["snapshot"])
    # 8 update NEW again without a workspace .git
    update(h, alpha, "NEW", "L1-8-update", verdict)
    s_after = alpha.snapshot("L1-after-second-update")
    if p_b2.get("snapshot"):
        checks.equivalent(h, verdict, "L1-8: S == S(B2) [F: 0031->0032]", p_b2["snapshot"], s_after,
                          migration="0031->0032")
    verdict.check("L1-8: no workspace .git", not checks.workspace_git(alpha))
    # 9 status and backups
    status = checks.status_text(h, alpha, "L1-9-status")
    verdict.check("L1-9: no open operation", h.open_operation("alpha") is None, status[-600:])
    listing = alpha.pf(["backups"], step_id="L1-9-backups", expect_exit=0)
    pre_update = []
    for name, plan, journal in checks.operations(alpha):
        if plan.get("kind") == "update":
            pre_update += checks.retained(journal)
    missing = [item for item in [b1, b2] + pre_update if item and item not in listing.stdout]
    verdict.check("L1-9: B1, B2 and the pre-update checkpoints listed", not missing, missing)
    h.save()
    ok = _record(h, "L1", verdict, extra={"verification_level_B1": level})
    h.results["cases"]["A3-T11"] = {"status": "passed" if ok else "failed", "level": "docker_postgresql",
                                    "failures": verdict.failures}
    return "passed" if ok else "failed"
