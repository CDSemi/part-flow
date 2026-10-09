"""L4 rows that need a multi-stage precondition or their own recovery evidence: R02, R04, R21/R22, R28, R36,
R37, R38, R39, R41, R53, R56, R65, R66 (SPEC 8.3) and the A12r2-F06 remainder (SPEC 1.4)."""
import json
import os
import re
import signal
import time

from . import checks
from . import faults
from . import instances as instances_module
from . import matrix
from . import matrix_rows
from . import noeffect
from . import observe
from . import oracles
from . import phrases
from . import terminal
from . import util
from .core import PF_ENV, LAUNCHER


def check(label, ok, detail=None):
    return {"check": label, "ok": bool(ok), "detail": detail}


# ---------------------------------------------------------------------------------------------- mismatch


def out_of_band_migration(h, inst, note):
    """Apply NEW's migration with NEW's backend image while pf's active image is OLD (declared external event)."""
    from .cases import new_backend_image
    reference, image = new_backend_image(h, inst)
    backend = inst.backend_container()
    info = util.docker_json(["inspect", backend])[0]
    url = next(entry.split("=", 1)[1] for entry in info["Config"]["Env"] if entry.startswith("DATABASE_URL="))
    network = sorted(info["NetworkSettings"]["Networks"])[0]
    h.external_event("out-of-band-migration", f"docker run --rm --network {network} {reference} uv run alembic "
                     "upgrade head", target=inst.slug, note=note)
    run = util.docker(["run", "--rm", "--network", network, "--label", "io.partflow.pfa34.scratch=1", "-e",
                       "DATABASE_URL=" + url, image, "uv", "run", "--no-sync", "alembic", "upgrade", "head"],
                      timeout=600)
    if run.returncode != 0:
        raise util.HarnessError("out-of-band migration failed: " + run.stderr[-300:])


def prepare_mismatch(h, inst):
    matrix.ensure_head(h, inst, "0031")
    out_of_band_migration(h, inst, "R21/R22 precondition: schema/image mismatch (NF-1)")
    if (inst.heads() or ["?"])[0][:4] != "0032":
        raise util.HarnessError("mismatch precondition not reached")
    return {}


def r22_expect(h, inst, context, recovery):
    text = " ".join(step.get("stderr", "") for step in recovery["steps"])
    status = inst.pf(["status"], step_id="R22-status-after", expect_exit=None)
    route_named = "reset-db" in (text + status.stdout) or "rollback" in (text + status.stdout)
    result = inst.pf(["reset-db"], step_id="R22-new-reset", phrases=phrases.exact("RESET " + inst.env()["POSTGRES_DB"]),
                     expect_exit=None, allow_fail=True, timeout=3600)
    return [check("R22: abandon closed cancelled", recovery.get("final_phase") == "cancelled", recovery.get("final_phase")),
            check("R22: the message names pf reset-db / pf rollback --restore-db", route_named),
            check("R22: a new pf reset-db completes", result.exit == 0, result.stderr[-300:])]


# --------------------------------------------------------------------------------------------- R28 drift


def r28_recreate(h, inst, context, state):
    """After the kill in deleting: recreate the first destroyed volume name (I7), observe the drift refusal, remove the
    recreated volume as the operator (the frozen plan is never expanded)."""
    destroyed = [((item.get("Actor") or {}).get("Attributes") or {}).get("name") or (item.get("Actor") or {}).get("ID")
                 for item in h.events_between(time.time() - 600) if item.get("Type") == "volume"
                 and (item.get("Action") or "") == "destroy"]
    present = set(util.docker_checked(["volume", "ls", "-q"]).split())
    # a destroyed name of this instance that is absent now (never a no-op `volume create` of a live volume)
    name = next((item for item in reversed(destroyed) if item and item.startswith(inst.project + "_")
                 and item not in present), None)
    context["r28_state"] = state  # read by r28_expect (the row record is written only when the row finishes)
    state["recreated"] = name
    if not name:
        state["drift"] = "no destroyed volume observed before the kill"
        return
    util.docker_checked(["volume", "create", "--label", f"io.deploy-admin.instance-id={inst.info['instance_id']}",
                         "--label", f"com.docker.compose.project={inst.project}", name])
    h.external_event("I7", f"docker volume create {name}", target=name, note="R28 recreated deleted name")
    result = inst.pf(["resume"], step_id="R28-resume-drift", phrases=matrix.ROUTE_PHRASES, expect_exit=None,
                     allow_fail=True)
    state["drift_exit"] = result.exit
    state["drift_copy"] = (result.stdout + result.stderr)[-600:]
    state["recreated_survived"] = name in util.docker_checked(["volume", "ls", "-q"]).split()
    util.docker(["volume", "rm", name])
    h.external_event("I7-removed", f"docker volume rm {name}", target=name, note="operator removes the recreated name")


def r28_expect(h, inst, context, recovery):
    state = context.get("r28_state") or {}
    return [check("R28: the resume refused on drift", state.get("drift_exit") not in (0, None), state.get("drift_copy")),
            check("R28: the recreated item was not deleted", state.get("recreated_survived"), state.get("recreated"))]


# ------------------------------------------------------------------------------------ side-by-side, cleanup


def prepare_side_by_side(h, inst):
    matrix_rows.prepare_running(h, inst)
    bundle = matrix_rows.latest_bundle(h, inst) or h.fixture.get("P")
    if not bundle:
        raise util.HarnessError("no purge bundle for the side-by-side rows")
    return {"bundle": bundle}


def cmd_side_by_side(h, inst, context):
    return ["restore-instance", context["bundle"], "--side-by-side"], phrases.exact("RESTORE COPY " + context["bundle"]), ()


def remove_targets(h, inst, context=None):
    instances_module.wait_pf_idle(timeout=300)
    from .loop7 import target_projects
    for target in target_projects():
        allowed = (r"CLEANUP " + inst.project + r" [0-9a-f]{8}",) + phrases.exact("REMOVE RECOVERY TARGET " + target)
        inst.pf(["cleanup", "--apply", "--recovery-target", target], step_id=f"post-cleanup-{target}",
                phrases=allowed, expect_exit=None, allow_fail=True)


def prepare_cleanup_target(h, inst):
    context = prepare_side_by_side(h, inst)
    from .loop7 import target_projects
    before = set(target_projects())
    inst.pf(["restore-instance", context["bundle"], "--side-by-side"], step_id=f"pre-side-by-side-{util.utc()}",
            phrases=phrases.exact("RESTORE COPY " + context["bundle"]), timeout=3600)
    created = [name for name in target_projects() if name not in before]
    if not created:
        raise util.HarnessError("no recovery target created for R37")
    return {"target": created[0]}


def cmd_cleanup_target(h, inst, context):
    target = context["target"]
    allowed = (r"CLEANUP " + inst.project + r" [0-9a-f]{8}",) + phrases.exact("REMOVE RECOVERY TARGET " + target)
    return ["cleanup", "--apply", "--recovery-target", target], allowed, ()


CANDIDATE_RE = re.compile(r"pf_(?:verify|migrate|restore|clean)_[0-9a-f]{20}")


def prepare_cleanup_candidate(h, inst):
    matrix_rows.prepare_running(h, inst)
    report = inst.pf(["cleanup"], step_id=f"pre-cleanup-report-{util.utc()}", expect_exit=None)
    names = sorted(set(CANDIDATE_RE.findall(report.stdout)))
    live = set(checks.live_markers(inst)["databases"])
    present = [name for name in names if name in live]
    if not present:
        present = _arrange_candidate(h, inst)
    if not present:
        raise util.HarnessError("no recorded candidate database at the precondition (R53 cannot be arranged)")
    return {"candidate": present[0]}


def _arrange_candidate(h, inst):
    """R53 precondition when no recorded candidate is left: a `pf backup` whose verification candidate pf_verify_*
    cannot be dropped because a harness session is open on it (recorded external event); the session is closed
    afterwards. Returns the recorded candidates the cleanup report then names."""
    state = {}

    def tick(process):
        if state.get("session") or process.poll() is not None:
            return
        names = [name for name in checks.live_markers(inst)["databases"] if name.startswith("pf_verify_")]
        if names:
            state["session"] = matrix_rows.Session(h, inst, names[0], ["SELECT 1;"],
                                                   f"R53 precondition: session on {names[0]}")
    result = inst.pf(["backup"], step_id=f"pre-R53-backup-{util.utc()}", expect_exit=None, allow_fail=True,
                     on_tick=tick, timeout=3600)
    if state.get("session"):
        state["session"].close()
    instances_module.wait_pf_idle(timeout=300)
    h.log(f"R53 precondition backup exit {result.exit}; session {'opened' if state.get('session') else 'never opened'}")
    open_op = h.open_operation(inst.slug)
    if open_op is not None:
        h.log(f"R53 precondition left {open_op['operation_id']} open")
    report = inst.pf(["cleanup"], step_id=f"pre-cleanup-report-{util.utc()}", expect_exit=None)
    names = sorted(set(CANDIDATE_RE.findall(report.stdout)))
    live = set(checks.live_markers(inst)["databases"])
    return [name for name in names if name in live]


def cmd_cleanup_default(h, inst, context):
    return ["cleanup", "--apply"], (r"CLEANUP " + inst.project + r" [0-9a-f]{8}",) + \
        phrases.allow("DELETE CHECKPOINT HISTORY", "REMOVE RECOVERY TARGET"), ()


def destroy_prefix_gate(h, slug, prefix):
    """Journal phase deleting and the first daemon destroy of a container whose project starts with ``prefix`` since
    the injected command started."""
    holder = {}

    def predicate():
        started = faults.command_anchor(holder, h.operations_dir(slug))
        operation, journal = faults.journal_state(h.operations_dir(slug))
        if not journal or journal.get("phase") != "deleting":
            return None
        for item in h.events_between(started):
            project = ((item.get("Actor") or {}).get("Attributes") or {}).get("com.docker.compose.project") or ""
            if (item.get("Action") or "").startswith("destroy") and project.startswith(prefix):
                return {"operation": operation, "destroy": (item.get("Actor") or {}).get("ID"), "phase": "deleting"}
        return None
    return predicate


# ------------------------------------------------------------------------------------------- abort-deploy


def prepare_opened_deploy(h, inst_unused):
    """A fresh subject whose first deploy is killed right after its frontend started (preserve-then-abort)."""
    context = matrix_rows.prepare_fresh(h, inst_unused)
    fresh = context["subject"]
    sha = h.commit("OLD")
    gate = matrix.effect_trigger(h, fresh.slug, effect_type="service-change", target_prefix="service:frontend",
                                 states=("complete",))
    faults.set_baseline(h.operations_dir(fresh.slug))
    trigger = faults.Trigger(lambda process: gate(), lambda process, target: faults.kill_pf(process, signal.SIGKILL))
    fresh.pf(["deploy", "--commit", sha, "--skip-ci"], step_id=f"pre-deploy-{fresh.slug}",
             phrases=phrases.exact("DEPLOY " + sha[:12]), dialogue=instances_module.REUSE_ENV, expect_exit=None,
             allow_fail=True, on_tick=trigger.tick, timeout=3600)
    if trigger.fired is None:
        raise util.HarnessError("the deploy finished before the frontend-started kill")
    instances_module.wait_pf_idle(timeout=300)
    return context


def cmd_abort(h, inst, context):
    return ["abort-deploy"], phrases.allow("ABORT DEPLOY"), ()


# ------------------------------------------------------------------------------------------------- R39


def r39(h, row, inst):
    """R39: SIGKILL of a `pf resume` mid-work, plus a concurrent second `pf resume` while the first holds the lock."""
    run = matrix.RowRun(h, row, inst)
    matrix.ensure_head(h, inst, "0031")
    sha = h.commit("NEW")
    live = matrix.live_db_prefix(inst)
    gate = matrix.effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)
    state = {"window_reached": False}
    spec = {"primitive": "I2", "child": ["alembic", "upgrade"], "gate": gate}
    trigger = matrix.make_injection(h, inst, spec, state)
    inst.pf(["update", "--commit", sha, "--allow-migrations", "--skip-ci"], step_id="R39-prepare-update",
            phrases=phrases.exact("UPDATE " + sha[:12]), expect_exit=None, allow_fail=True, on_tick=trigger.tick)
    instances_module.wait_pf_idle(timeout=300)
    operation = (trigger.fired or {}).get("target", {}).get("operation")
    if not operation:
        return run.finish("blocked", "the precondition update was not interrupted in migrating")
    second = {}

    def concurrent(process):
        if second or process.poll() is not None:
            return
        _, journal = faults.journal_state(h.operations_dir(inst.slug), operation)
        attempts = h.operations_dir(inst.slug) / operation / "attempts.json"
        if not attempts.exists() or len(json.loads(attempts.read_text())) < 2:
            return
        # the first resume is held (SIGSTOP) while the second runs, so N attributes only the second one's effects;
        # it is then killed (the row's SIGKILL)
        held = faults.stop_pf(process)
        before = noeffect.capture(h)
        started = time.time()
        result = terminal.run([LAUNCHER, "--instance", inst.slug, "resume", "--operation", operation],
                              phrases=phrases.allow("RESUME"), env=dict(PF_ENV), cwd="/", timeout=300)
        after = noeffect.capture(h)
        second.update({"held_first_pid": held, "exit": result.exit, "stderr": result.stderr[-400:], "typed": result.typed,
                       "noeffect": noeffect.evaluate(h, "R39-second-resume", before, after, (started, time.time()))})
        faults.kill_pf(process, signal.SIGKILL)

    result = inst.pf(["resume", "--operation", operation], step_id="R39-first-resume", phrases=phrases.allow("RESUME"),
                     expect_exit=None, allow_fail=True, on_tick=concurrent, timeout=1800)
    run.record.update({"operation_id": operation, "window_reached": bool(second), "second_resume": second,
                       "first_resume_exit": result.exit})
    if not second:
        return run.finish("blocked", "the first resume finished before the concurrent window")
    instances_module.wait_pf_idle(timeout=300)
    recovery = matrix.recover(h, inst, operation, row="R39")
    run.record.update({"routes_taken": recovery["routes_taken"], "outcome": recovery.get("final_phase"),
                       "alembic_upgrades_during_recovery": recovery.get("alembic_upgrades")})
    problems = []
    if second.get("exit") in (0, None) or second.get("typed"):
        problems.append("the concurrent resume was not refused before a confirmation")
    if (second.get("noeffect") or {}).get("result") != "passed":
        problems.append("N failed for the concurrent resume")
    if recovery.get("final_phase") != "completed":
        problems.append(f"a later resume did not complete: {recovery.get('final_phase')}")
    return run.finish("passed" if not problems else "failed", "; ".join(problems) or None)


# ------------------------------------------------------------------------------------- journal-less rows


def no_journal_row(h, row, inst, *, prepare, argv, allowed, dialogue, child, label, app_running):
    """R41/R66 + the A12r2-F06 remainder: a journal-less operation with runner records, its acknowledgement route,
    the route refused while unacknowledged, and the acknowledgement."""
    run = matrix.RowRun(h, row, inst)
    base = inst
    # SPEC R66: SIGKILL first; when no runner record results, SIGTERM (pf's own interruption handler records the
    # child); every attempt is recorded
    attempts, operation, files, records, trigger = [], None, [], [], None
    for signame in ("SIGKILL", "SIGTERM"):
        context = prepare(h, base) or {}  # R41: each attempt on its own fresh subject
        inst = context.get("subject", base)
        run.record["subject"] = inst.slug
        names_before = set(h.list_operations(inst.slug))
        state = {"window_reached": False}
        spec = {"primitive": "I1", "signal": signame, "child": child, "gate": None}
        trigger = matrix.make_injection(h, inst, spec, state)
        inst.pf(argv, step_id=f"{row['id']}-inject-{signame}", phrases=allowed, dialogue=dialogue, expect_exit=None,
                allow_fail=True, on_tick=trigger.tick, timeout=3600)
        instances_module.wait_pf_idle(timeout=300)
        created = sorted(set(h.list_operations(inst.slug)) - names_before)
        operation = created[-1] if created else None
        files = sorted(os.listdir(str(h.operations_dir(inst.slug) / operation))) if operation else []
        records_path = h.operations_dir(inst.slug) / operation / "unresolved-effects.json" if operation else None
        records = json.loads(records_path.read_text(encoding="utf-8"))             if records_path and records_path.exists() else []
        attempts.append({"signal": signame, "window_reached": bool(trigger.fired), "injected": trigger.fired,
                         "operations": created, "runner_records": len(records)})
        if records or not trigger.fired:
            break
    run.record.update({"window_reached": bool(trigger.fired), "injected": trigger.fired, "attempts": len(attempts),
                       "attempt_log": attempts, "operations": [item for a in attempts for item in a["operations"]]})
    if not trigger.fired:
        return run.finish("blocked", "window not reached")
    run.record.update({"operation_id": operation, "files": files, "runner_records": records})
    results = [check(f"{row['id']}: no lifecycle journal", "journal.json" not in files, files),
               check(f"{row['id']}: runner records written", bool(records), len(records))]
    if not records:
        reason = "no runner record resulted after SIGKILL and SIGTERM (A12r2-F06 sub-part blocked)"
        if "pg_dump" in child:
            reason += ("; shipped design: pg_dump is a read-only child (compose_effect returns None for "
                       "COMPOSE_EXEC_READ_ONLY_PROGRAMS) and pf_runner records only declared effects, so no interruption "
                       "or timeout of it can produce a record")
        return run.finish("blocked", reason, checks=results)
    status = inst.pf(["status"], step_id=f"{row['id']}-status", expect_exit=None)
    detail = inst.pf(["status", "--operation", operation], step_id=f"{row['id']}-status-op", expect_exit=None)
    text = status.stdout + detail.stdout
    results.append(check(f"{row['id']}: pf status names the acknowledgement route",
                         "--acknowledge" in text or "acknowledge" in text.lower(), text[-800:]))
    release = sorted((h.pfroot / "releases").iterdir())[-1].name
    refused, record = noeffect.refused(h, inst, ["install", "control", "--release", release],
                                       step_id=f"{row['id']}-install-control-refused",
                                       phrases=phrases.allow("SELECT CONTROL", "INSTALL CONTROL"),
                                       expect_text="instance-effects-unresolved")
    results.append(check(f"{row['id']}: pf install control refused while unacknowledged",
                         record["refused"] and record["expected_text_seen"], refused.stderr[-400:]))
    results.append(check(f"{row['id']}: N holds for the refused route", record["result"] == "passed",
                         record.get("tree_diff")))
    sessions = None
    if app_running and inst.db_container():
        env = inst.env()
        count = observe.psql(inst.db_container(), env["POSTGRES_USER"], "postgres",
                             "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND "
                             f"datname = '{env['POSTGRES_DB']}';\n", timeout=60)
        sessions = count.stdout.strip()
    acknowledged = inst.pf(["resume", "--operation", operation, "--acknowledge"], step_id=f"{row['id']}-acknowledge",
                           phrases=phrases.exact("ACKNOWLEDGE " + operation[-8:]), expect_exit=None, allow_fail=True)
    results.append(check(f"{row['id']}: --acknowledge completes" + (" with the app's pooled sessions open"
                                                                     if app_running else ""),
                         acknowledged.exit == 0, {"stderr": acknowledged.stderr[-400:], "app_sessions": sessions}))
    scope_named = any((item.get("effect") or {}).get("kind") == "database" for item in records
                      if isinstance(item, dict))
    run.record["scoped_refusal"] = "observed" if scope_named else \
        "blocked: no real journal-less record names a database effect (offline AK-7 remains the evidence)"
    problems = [item["check"] for item in results if not item["ok"]]
    return run.finish("passed" if not problems else "failed", "; ".join(problems) or None, checks=results)


def r41(h, row, inst):
    sha = h.commit("OLD")
    return no_journal_row(h, row, inst, prepare=matrix_rows.prepare_fresh, argv=["deploy", "--commit", sha,
                                                                                 "--skip-ci"],
                          allowed=phrases.exact("DEPLOY " + sha[:12]), dialogue=instances_module.REUSE_ENV,
                          child=["compose", "build"], label="deploy pre-plan build", app_running=False)


def r66(h, row, inst):
    return no_journal_row(h, row, inst, prepare=matrix_rows.prepare_running, argv=["backup", "--emergency"],
                          allowed=phrases.exact("EMERGENCY BACKUP " + inst.project), dialogue=(),
                          child=["pg_dump"], label="backup --emergency pg_dump", app_running=True)


# ----------------------------------------------------------------------------------------------- R56 / R65


BALLAST = []


def r56_act(h, inst):
    target, size = matrix_rows.ballast(h.root, 0)
    BALLAST.append(target)
    h.external_event("I7-ballast", f"fallocate -l {size} {target}", target=str(target),
                     note="R56: instance-tree device full right after the live migration container exited")
    return {"ballast": str(target), "bytes": size}


def r56_gate(h, inst):
    live = matrix.live_db_prefix(inst)
    effect = matrix.effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)
    holder = {}

    def predicate():
        faults.command_anchor(holder, h.operations_dir(inst.slug))
        context = effect()
        if context is None:
            holder.pop("live_started", None)
            return None
        # anchored when the live migration's intent is first seen: the rehearsal's one-off died before it
        started = holder.setdefault("live_started", (holder["baseline"], time.time()))
        if started[0] != holder["baseline"]:
            started = holder["live_started"] = (holder["baseline"], time.time())
        for item in h.events_between(started[1]):
            attributes = (item.get("Actor") or {}).get("Attributes") or {}
            if item.get("Type") == "container" and (item.get("Action") or "").startswith("die") and \
                    attributes.get("com.docker.compose.oneoff") == "True" and \
                    attributes.get("com.docker.compose.project") == inst.project:
                return dict(context, die=(item.get("Actor") or {}).get("ID"))
        return None
    return predicate


def r56_cleanup(h, inst, context, state):
    while BALLAST:
        target = BALLAST.pop()
        if target.exists():
            target.unlink()
        h.external_event("I7-ballast-removed", f"rm {target}", target=str(target))


def r65_act(h, inst):
    container = inst.db_container()
    h.external_event("I7", f"docker stop {container[:12]} (db at service:backend:start)", target="db",
                     note="R65 real activation failure after a completed switch")
    util.docker(["stop", container], timeout=120)
    return {"stopped": container}


def r65_expect(h, inst, context, recovery):
    offered = sorted({route for item in recovery["routes_offered"] for route in item["routes"]})
    # the retained pf_keep_* can only be listed with the db running: after a dead end the operator's manual start
    # (F-A34-03) is recorded first
    matrix.start_stopped_db(h, inst)
    kept = [name for name in checks.live_markers(inst)["databases"] if name.startswith("pf_keep_")] \
        if inst.db_container() else []
    result = [check("R65: the forward resume is offered", any(route.startswith("resume") for route in offered),
                    offered),
              check("R65: the superseding rollback --restore-db is offered",
                    any(route.startswith("rollback") and "--restore-db" in route for route in offered), offered),
              check("R65: the retained pf_keep_* exists (manual route evidence)", bool(kept), kept)]
    operation = next((route.split()[-1] for route in recovery.get("routes_taken") or []
                      if route.startswith("resume --operation ")), None)
    plan = h.operation_files(inst.slug, operation).get("plan.json") if operation else None
    keep = next((effect["target"].split(":")[-1] for effect in (plan or {}).get("effects") or []
                 if effect.get("type") == "database-switch"), None)
    pre = h.evidence.read_json("snapshots/R65-pre.json")
    if keep in kept and pre:
        # SPEC R65: the retained pf_keep_* holds the pre-switch data (S, F)
        # pf_keep_* does not accept connections (ALLOW_CONNECTIONS false): it is read through a throwaway copy made
        # with CREATE DATABASE ... TEMPLATE (a recorded harness observation; the copy is dropped afterwards)
        env = inst.env()
        copy = "pfa34_probe_r65"
        h.external_event("observation-copy", f"CREATE DATABASE {copy} TEMPLATE {keep}", target=keep,
                         note="R65: read the retained pre-switch database without enabling connections on it")
        made = observe.psql(inst.db_container(), env["POSTGRES_USER"], "postgres",
                            f"DROP DATABASE IF EXISTS {copy};\nCREATE DATABASE {copy} TEMPLATE {keep};\n", timeout=600)
        try:
            if made.returncode != 0:
                raise util.HarnessError(f"template copy failed: {made.stderr[-300:]}")
            held = inst.snapshot("R65-keep", database=copy)
        except (util.HarnessError, KeyError, ValueError) as exc:
            result.append(check("R65: the retained " + keep + " holds the pre-switch data (S and F)", False,
                                f"snapshot failed: {exc}"))
        else:
            problems = oracles.compare(pre, held, h.fixture.get("volatile") or ["user_sessions"])
            result.append(check("R65: the retained " + keep + " holds the pre-switch data (S and F)",
                                not problems and held["schema"]["sha256"] == pre["schema"]["sha256"], problems[:5]))
        finally:
            observe.psql(inst.db_container(), env["POSTGRES_USER"], "postgres", f"DROP DATABASE IF EXISTS {copy};\n",
                         timeout=300)
            h.external_event("observation-copy-dropped", f"DROP DATABASE {copy}", target=copy)
    else:
        result.append(check("R65: the operation's pf_keep_* is present", False, {"keep": keep, "kept": kept}))
    if recovery.get("dead_end"):
        # OD-A33-12 evidence: whether the documented routes work once the operator has started the db by hand
        if operation:
            after = matrix.recover(h, inst, operation, row="R65-after-manual-start")
            result.append(check("R65: after the manual db start a documented route completes",
                                after.get("final_phase") == "completed" or
                                str(after.get("final_phase")).endswith("(completed)"),
                                {"routes_taken": after.get("routes_taken"), "final_phase": after.get("final_phase"),
                                 "dead_end": after.get("dead_end")}))
    return result


# --------------------------------------------------------------------------------------------------- table


def rows(h, inst):
    live = matrix.live_db_prefix(inst)
    table = []

    def add(row_id, kind, phase, effect_type, injection, spec=None, *, grid=(), when="after", **extra):
        value = {"id": row_id, "kind": kind, "phase": phase, "effect_type": effect_type, "injection": injection,
                 "spec": spec or {}, "grid": [list(cell) for cell in grid], "when": when}
        value.update(extra)
        table.append(value)

    add("R02", "update", "migrating", "database-restore", "I2 on pg_restore into pf_migrate_*",
        {"primitive": "I2", "child": ["pg_restore", "pf_migrate_"],
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="database-restore",
                                       target_prefix="database:pf_migrate_")},
        grid=[("migrations", "SIGKILL")], prepare=matrix.prepare_update, command=matrix.cmd_update)
    add("R04", "update", "migrating", "database-migrate",
        "I5 lock + I1 SIGKILL + I7 docker kill of the migration container (A3-T05 not committed)",
        {"primitive": "I7", "child": ["alembic", "upgrade"],
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live),
         "act": lambda process, target: _r04_act(h, inst, process)},
        grid=[("migrations", "error exit")], prepare=matrix.prepare_update, command=matrix.cmd_update,
        setup_fault=lambda h, inst, context, state: _r04_lock_thread(h, inst, state),
        after_inject=matrix_rows._close_sessions, when="before")
    add("R21", "reset-db", "preserving", "capture", "I1 SIGKILL on the capture (schema/image mismatch, NF-1)",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"],
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="capture")},
        prepare=prepare_mismatch, command=matrix.cmd_reset, data_relation="restored", when="before",
        post=matrix.restore_data_after_reset)
    add("R22", "reset-db", "preserving", "capture", "as R21, then pf resume --abandon",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"],
         "gate": matrix.effect_trigger(h, inst.slug, effect_type="capture")},
        prepare=prepare_mismatch, command=matrix.cmd_reset, data_relation="restored", when="before",
        prefer=["--abandon"], expect=r22_expect, post=matrix.restore_data_after_reset)
    add("R28", "purge", "deleting", "resource-delete", "as R27, then I7 recreates the deleted volume before resume",
        {"primitive": "I3", "signal": "SIGKILL", "gate": matrix_rows.first_destroy_gate(h, inst.slug, inst.project, "volume")},
        grid=[("deletion", "SIGKILL")], prepare=matrix_rows.prepare_running, command=matrix_rows.cmd_purge,
        after_inject=r28_recreate, expect=r28_expect, post=matrix_rows.bring_back, data_relation="restored")
    add("R36", "restore-side-by-side", "restoring-data", "database-restore", "I1 SIGKILL during restoring-data",
        {"primitive": "I3", "signal": "SIGKILL", "gate": matrix.phase_trigger(h, inst.slug, "restoring-data")},
        prepare=prepare_side_by_side, command=cmd_side_by_side, post=remove_targets, when="before")
    add("R37", "cleanup", "deleting", "resource-delete", "I3 SIGKILL after the first removal",
        {"primitive": "I3", "signal": "SIGKILL", "gate": destroy_prefix_gate(h, inst.slug, "pfrecover-")},
        grid=[("deletion", "SIGKILL")], prepare=prepare_cleanup_target, command=cmd_cleanup_target,
        post=remove_targets)
    add("R53", "cleanup", "deleting", "database-drop", "error exit: a harness session on the candidate at the drop",
        {"primitive": "I7", "gate": matrix.phase_trigger(h, inst.slug, "deleting"),
         "act": lambda process, target: _r53_session(h, inst)},
        grid=[("deletion", "error exit")], prepare=prepare_cleanup_candidate, command=cmd_cleanup_default,
        after_inject=matrix_rows._close_sessions, when="before")
    add("R38", "abort-deploy", "preserving", "capture", "I1 SIGKILL on the capture",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"]}, prepare=prepare_opened_deploy,
        command=cmd_abort, gate_phase="preserving", when="before")
    add("R56", "update", "migrating", "database-migrate",
        "error exit of the journal write: ballast fills the instance-tree device after the migration container exits",
        {"primitive": "I7", "gate": r56_gate(h, inst), "act": lambda process, target: r56_act(h, inst)},
        grid=[("journal commit", "error exit")], prepare=matrix.prepare_update, command=matrix.cmd_update,
        after_inject=r56_cleanup, no_rerun=True)
    add("R65", "rollback", "activating", "service-change",
        "I7 docker stop of the db at service:backend:start after the switch (OD-A33-12)",
        {"primitive": "I7", "gate": matrix.effect_trigger(h, inst.slug, effect_type="service-change",
                                                          target_prefix="service:backend:start"),
         "act": lambda process, target: r65_act(h, inst)},
        prepare=matrix.prepare_rollback, command=matrix.cmd_rollback, expect=r65_expect, data_relation="restored",
        when="before")
    add("R39", "resume", "migrating", "database-migrate", "I1 SIGKILL of pf resume + a concurrent second resume",
        custom=r39)
    add("R41", "deploy", "pre-plan", "image-build", "I1 SIGKILL during the candidate compose build", custom=r41)
    add("R66", "backup", "journal-less", "capture", "I1 SIGKILL during the emergency pg_dump", custom=r66)
    return table


def _r04_lock_thread(h, inst, state):
    """Hold the audit_events ACCESS SHARE lock from the rehearsal's end (before the live migration starts)."""
    gate = matrix.effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix="database:pf_migrate_",
                                 states=("complete",))
    import threading

    def wait():
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline and not state.get("r04_locked"):
            if gate():
                matrix_rows._audit_lock(h, inst)
                state["r04_locked"] = True
                return
            time.sleep(0.05)
    threading.Thread(target=wait, daemon=True).start()


def _r04_act(h, inst, process):
    result = faults.kill_pf(process, signal.SIGKILL)
    oneoffs = observe.containers(inst.project, all_states=False, oneoff=True)
    for container in oneoffs:
        util.docker(["kill", container])
        h.external_event("I7", f"docker kill {container[:12]} (the blocked live migration one-off)", target=container)
    result["killed_oneoffs"] = oneoffs
    return result


def _r53_session(h, inst):
    report = inst.pf if False else None
    del report
    names = [name for name in checks.live_markers(inst)["databases"] if CANDIDATE_RE.fullmatch(name)]
    if not names:
        return {"session": "no candidate left"}
    session = matrix_rows.Session(h, inst, names[0], ["SELECT 1;"], f"R53 session on {names[0]}")
    matrix_rows.SESSIONS.append(session)
    return {"session": names[0]}
