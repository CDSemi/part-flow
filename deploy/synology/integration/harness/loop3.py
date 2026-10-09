"""LOOP-03 (+A3-T03) on alpha after L7 (SPEC 8.2 L3): a migrated-but-failed activation and explicit old-data
restore."""
from . import checks
from . import noeffect
from . import phrases
from .checks import Verdict
from .loops import _record, backup, heads_prefix, update, update_args


def checkpoint_names(inst):
    base = inst.backups / "revisions" / inst.project
    return set(item.name for item in base.iterdir()) if base.is_dir() else set()


def run(h):
    alpha = h.instance("alpha")
    seeder = alpha.seeder()
    verdict = Verdict("LOOP-03")
    t03 = Verdict("A3-T03")
    b2 = h.fixture.get("B2")
    db = alpha.env()["POSTGRES_DB"]
    # 1 rollback B2 --restore-db (OLD / 0031, data S(B1))
    result = checks.run_alpha_step(h, alpha, ["rollback", b2, "--restore-db"], step_id="L3-1-rollback-B2",
                                   phrases=phrases.exact("RESTORE " + db + " " + b2))
    op, plan, journal = checks.newest(alpha, "rollback")
    verdict.check("L3-1: completed", journal.get("phase") == "completed", journal.get("phase"))
    verdict.check("L3-1: heads 0031", heads_prefix(alpha) == "0031", alpha.heads())
    # 2 W4, S5
    seeder.marker("W4", movement=True)
    s5 = alpha.snapshot("L3-S5")
    # 3 update BROKEN: the live migration succeeds, the activation fails
    argv, allowed = update_args(h, "BROKEN")
    result = checks.run_alpha_step(h, alpha, argv, step_id="L3-3-update-broken", phrases=allowed, expect_exit=None,
                                   allow_fail=True, timeout=3600)
    h.fixture["L3_broken_exit"] = result.exit
    text = result.stdout + result.stderr
    branch_refused = "branch" in text.lower() and result.exit != 0 and heads_prefix(alpha) == "0031"
    h.fixture["L3_branch_refused"] = branch_refused
    op, plan, journal = checks.newest(alpha, "update")
    h.evidence.write_json("records/L3-3-update-journal.json", journal)
    verdict.check("L3-3: activation failed (non-zero exit)", result.exit not in (0, None), result.exit)
    verdict.check("L3-3: live migration applied (heads 0032)", heads_prefix(alpha) == "0032", alpha.heads())
    status = checks.status_text(h, alpha, "L3-3-status")
    h.fixture["L3_terminal_state"] = {"phase": journal.get("phase"), "status": status[-2000:]}
    pre_update = checks.retained(journal)
    verdict.check("L3-3: the pre-update checkpoint is retained", bool(pre_update), journal.get("retained_artifacts"))
    # 4 emergency capture E1 with mismatch evidence; it is never a rollback target
    names_before = checkpoint_names(alpha)
    dialogue = ()
    result = alpha.pf(["backup", "--emergency"], step_id="L3-4-emergency", expect_exit=None, allow_fail=True,
                      phrases=phrases.exact("EMERGENCY BACKUP " + alpha.project), dialogue=dialogue, timeout=3600)
    created = sorted(checkpoint_names(alpha) - names_before)
    t03.check("L3-4: emergency capture completed", result.exit == 0 and len(created) == 1,
              {"exit": result.exit, "created": created, "stderr": result.stderr[-400:]})
    e1 = created[-1] if created else None
    h.fixture["E1"] = e1
    if e1:
        manifest = checks.manifest(checks.checkpoint_dir(alpha, e1)) or {}
        mismatch = (manifest.get("compatibility") or {}).get("mismatch")
        t03.check("L3-4: mismatch evidence recorded (0032 data against the active image)", bool(mismatch),
                  manifest.get("compatibility"))
        refused, record = noeffect.refused(h, alpha, ["rollback", e1], step_id="L3-4-rollback-E1-refused")
        t03.check("L3-4: rollback E1 refused", record["refused"], refused.stderr[-400:])
        t03.check("L3-4: N holds", record["result"] == "passed", record)
        probe = checks.probe_checkpoint(h, alpha, e1, "L3-E1")
        t03.check("L3-4: E1 holds W4 at 0032 with F(0032)",
                  "PFA34-W4" in (probe.get("markers") or []) and (probe.get("heads") or ["?"])[0].startswith("0032")
                  and (probe.get("snapshot") or {}).get("schema", {}).get("sha256") == h.fixture.get("F_0032"),
                  {"markers": probe.get("markers"), "heads": probe.get("heads")})
    # 5 explicit old-data restore from the update's pre-update checkpoint
    if pre_update:
        target = pre_update[0]
        result = checks.run_alpha_step(h, alpha, ["rollback", target, "--restore-db"], step_id="L3-5-rollback",
                                       phrases=phrases.exact("RESTORE " + db + " " + target), timeout=3600)
        op, plan, journal = checks.newest(alpha, "rollback")
        verdict.check("L3-5: completed", journal.get("phase") == "completed", journal.get("phase"))
        verdict.check("L3-5: heads 0031", heads_prefix(alpha) == "0031", alpha.heads())
        s_after = alpha.snapshot("L3-after-rollback")
        checks.equivalent(h, verdict, "L3-5: S == S5 (W4 present)", s5, s_after)
        captures = checks.retained(journal)
        if captures:
            capture = checks.probe_checkpoint(h, alpha, captures[0], "L3-rollback-capture")
            t03.check("L3-5: the before-rollback capture holds the 0032 data (W4, F(0032))",
                      "PFA34-W4" in (capture.get("markers") or []) and
                      (capture.get("heads") or ["?"])[0].startswith("0032"), capture.get("heads"))
    # 6 backup B4; update NEW
    b4, _, _ = backup(h, alpha, "L3-6-backup", verdict)
    h.fixture["B4"] = b4
    update(h, alpha, "NEW", "L3-6-update", verdict)
    ok = _record(h, "L3", verdict)
    ok03 = _record(h, "L3", t03)
    h.results["cases"]["A3-T03-L3"] = {"status": "passed" if ok03 else "failed", "failures": t03.failures}
    h.results["cases"]["LOOP-03"] = {"status": "passed" if ok else "failed", "failures": verdict.failures}
    return "passed" if ok and ok03 else "failed"
