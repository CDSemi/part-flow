"""Scenario ``ctl`` (SPEC 8.6): A2-T04's control-only upgrade through the installed launcher."""
import json
import time
from pathlib import Path

from . import fixture as fixture_module
from . import observe
from . import phrases
from .checks import Verdict
from .loops import _record

NOTE = "app-operation-required"


def record_bytes(inst):
    path = inst.info.get("record_path")
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def run(h):
    verdict = Verdict("A2-T04")
    alpha, bravo = h.instance("alpha"), h.instance("bravo")
    if alpha.health() != 200:
        h.log("ctl precondition: alpha is not running; the documented route is recorded instead of a repair")
        verdict.check("ctl precondition: alpha running", False, "alpha not healthy before ctl")
    pre = {slug: h.instance(slug).identities(f"pre-ctl-{slug}") for slug in ("alpha", "bravo")}
    records_before = {slug: record_bytes(h.instance(slug)) for slug in ("alpha", "bravo")}
    default_before = (h.registry() or {}).get("default_instance_id")
    workspaces = {slug: str(h.instance(slug).workspace) for slug in ("alpha", "bravo")}
    stage = h.root / "stage" / "control-ctl"
    record = fixture_module.stage_control(h.work, stage, h.settings_path, ctl_candidate=True)
    h.evidence.write_json("control-stage-ctl.json", record)
    verdict.check("ctl: the candidate differs by exactly the appended comment line",
                  record["changed_release_files"] == ["compose.nas.yaml", "pf-admin.py"] and
                  record.get("ctl_diff") == fixture_module.CTL_COMMENT.decode(), record["changed_release_files"])
    started = time.time()
    result = h.pf(["install", "control", "--source", str(stage)], step_id="ctl-install-control",
                  phrases=phrases.allow("INSTALL CONTROL"), expect_exit=None, allow_fail=True, timeout=1800)
    window = (started, time.time())
    verdict.check("ctl: the operation completes", result.exit == 0, result.stderr[-600:])
    verdict.check("ctl: the app-operation-required note is printed", NOTE in result.stdout + result.stderr,
                  (result.stdout + result.stderr)[-800:])
    events = []
    for item in h.events_between(*window):
        attributes = (item.get("Actor") or {}).get("Attributes") or {}
        if item.get("Type") == "container" and attributes.get("com.docker.compose.project") in (alpha.project,
                                                                                               bravo.project):
            action = (item.get("Action") or "").split(":")[0]
            if action in ("stop", "start", "create", "die", "kill", "destroy", "restart"):
                events.append({"action": action, "project": attributes.get("com.docker.compose.project")})
    verdict.check("ctl: no stop/start/create/die for alpha or bravo", not events, events)
    for slug in ("alpha", "bravo"):
        inst = h.instance(slug)
        post = inst.identities(f"post-ctl-{slug}")
        verdict.check(f"ctl: {slug} containers, StartedAt, RestartCount unchanged",
                      observe.stable_identity(pre[slug]) == observe.stable_identity(post))
        verdict.check(f"ctl: {slug} .env unchanged", pre[slug]["env_sha256"] == post["env_sha256"])
        verdict.check(f"ctl: {slug} workspace path kept", str(inst.workspace) == workspaces[slug])
        after = record_bytes(inst) or {}
        before = records_before[slug] or {}
        changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        verdict.check(f"ctl: {slug} record differs only in control/record_revision",
                      set(changed) <= {"control", "record_revision"} and "control" in changed, changed)
    verdict.check("ctl: default instance unchanged", default_before == (h.registry() or {}).get("default_instance_id"))
    status = alpha.pf(["status"], step_id="ctl-status", expect_exit=None)
    h.fixture["ctl_status"] = status.stdout[-1500:]
    ok = _record(h, "ctl", verdict)
    h.results["cases"]["A2-T04-control-upgrade"] = {"status": "passed" if ok else "failed",
                                                    "failures": verdict.failures}
    return "passed" if ok else "failed"
