"""Scenario ``cases`` (SPEC 8.4): C-A1-11/12/13/14, C-A3-03/06/07/08/14/16 and C-CONC-01."""
import json
import os
from pathlib import Path
import shutil
import threading
import time

from . import api
from . import checks
from . import faults
from . import instances as instances_module
from . import matrix
from . import noeffect
from . import observe
from . import oracles
from . import phrases
from . import util
from .checks import Verdict
from .loop2 import purge as purge_step
from .loops import _record, heads_prefix

SCRATCH = "io.partflow.pfa34.scratch=1"


def case_result(h, case_id, verdict, *, extra=None, blocked=None):
    status = "blocked" if blocked else ("passed" if verdict.ok else "failed")
    value = {"status": status, "failures": verdict.failures, "checks": verdict.items}
    if blocked:
        value["reason"] = blocked
    if extra:
        value.update(extra)
    h.results["cases"][case_id] = value
    _record(h, "cases", verdict)
    return status


def rollback_to(h, inst, checkpoint, step_id, **kwargs):
    db = inst.env()["POSTGRES_DB"]
    return checks.run_alpha_step(h, inst, ["rollback", checkpoint, "--restore-db"], step_id=step_id,
                                 phrases=phrases.exact("RESTORE " + db + " " + checkpoint), timeout=3600, **kwargs)


def update_to(h, inst, name, step_id, *, migrations=True, **kwargs):
    sha = h.commit(name)
    argv = ["update", "--commit", sha, "--skip-ci"] + (["--allow-migrations"] if migrations else [])
    return checks.run_alpha_step(h, inst, argv, step_id=step_id, phrases=phrases.exact("UPDATE " + sha[:12]),
                                 timeout=3600, **kwargs)


def restore_instance(h, inst, bundle, step_id, **kwargs):
    db = h.fixture.get("alpha_db", "partflow_staging")
    allowed = phrases.exact("RESTORE INSTANCE " + inst.project, "RESTORE " + db + " " + bundle)
    result = checks.run_alpha_step(h, inst, ["restore-instance", bundle], step_id=step_id, phrases=allowed,
                                   timeout=7200, **kwargs)
    inst.info["purged"] = False
    h.save()
    return result


# ------------------------------------------------------------------------------------------------ C-A1-13


def c_a1_13(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A1-T13")
    # (a) a hostile caller context is ignored; the registered socket is used
    hostile = {"DOCKER_HOST": "tcp://127.0.0.1:2375", "DOCKER_CONTEXT": "foo", "DOCKER_CONFIG": "/tmp/x"}
    result = alpha.pf(["ps"], step_id="C-A1-13-a-ps", expect_exit=None, env_extra=hostile)
    verdict.check("C-A1-13 (a): caller DOCKER_HOST/CONTEXT/CONFIG ignored; the registered socket answers",
                  result.exit == 0 and "backend" in result.stdout, (result.stdout + result.stderr)[-500:])
    before, record = noeffect.refused(h, alpha, ["backup"], step_id="C-A1-13-a-backup-hostile",
                                      env_extra=hostile) if False else (None, None)
    # (b) engine-ID drift: every mutating command refused before mutation; restoring the ID works again
    engine_path = Path("/var/lib/docker/engine-id")
    original = engine_path.read_bytes() if engine_path.exists() else None
    if original is None:
        verdict.blocked("C-A1-13 (b)", "no /var/lib/docker/engine-id in this engine")
    else:
        # exact bytes (the daemon reads the file as is): only the first eight characters differ
        drifted = b"ffffffff" + original[8:]
        set_engine_id(h, drifted)
        try:
            for argv, step in ((["backup"], "C-A1-13-b-backup"),
                               (["update", "--commit", h.commit("NEXT"), "--skip-ci"], "C-A1-13-b-update")):
                result, record = noeffect.refused(h, alpha, argv, step_id=step, expect_text="daemon-drift",
                                                  phrases=())
                verdict.check(f"{step}: refused with daemon-drift before mutation", record["refused"] and
                              record["expected_text_seen"], (result.stdout + result.stderr)[-500:])
                verdict.check(f"{step}: N holds", record["result"] == "passed", record.get("tree_diff"))
        finally:
            set_engine_id(h, original)
        alpha.wait_healthy(timeout=300)
        result = alpha.pf(["status"], step_id="C-A1-13-b-restored", expect_exit=None)
        verdict.check("C-A1-13 (b): restoring the ID works again", result.exit == 0, result.stderr[-300:])
    # (c) a non-unix endpoint is refused at registration
    home = h.homes / "delta"
    for sub in ("repo", "config", "backups", "recovery"):
        (home / sub).mkdir(parents=True, exist_ok=True)
        os.chmod(str(home / sub), 0o755)
    os.chmod(str(home), 0o755)
    result, record = noeffect.refused(h, alpha, ["install", "register", "--slug", "delta", "--project", "pfdelta",
                                                 "--workspace", str(home / "repo"), "--configuration",
                                                 str(home / "config"), "--backups", str(home / "backups"),
                                                 "--recovery", str(home / "recovery"), "--docker-endpoint",
                                                 "tcp://127.0.0.1:2375"],
                                      step_id="C-A1-13-c-register-tcp", phrases=())
    verdict.check("C-A1-13 (c): a tcp:// endpoint is refused", record["refused"], (result.stdout + result.stderr)[-500:])
    verdict.check("C-A1-13 (c): N holds", record["result"] == "passed", record.get("tree_diff"))
    shutil.rmtree(str(home), ignore_errors=True)
    verdict.blocked("C-A1-13 (d) rootless", "no rootless engine in the disposable environment (PF-A5.1)")
    return case_result(h, "A1-T13", verdict, extra={"parts": [
        {"part": "remote/drift/caller-context", "status": "passed" if verdict.ok else "failed"},
        {"part": "rootless", "status": "blocked", "owner": "PF-A5.1"}]})


def set_engine_id(h, value):
    """Stop dockerd (the supervisor holds), rewrite /var/lib/docker/engine-id, release: a declared external event."""
    hold = Path("/run/pfa34/hold")
    hold.write_text("C-A1-13\n")
    pid = faults.daemon_pid()
    h.external_event("I6+engine-id", f"hold supervisor; kill -TERM {pid}; write engine-id; release",
                     target="/var/lib/docker/engine-id", note=value[:8].decode())
    try:
        os.kill(pid, 15)
        faults.wait_exit(pid, 120)
        Path("/var/lib/docker/engine-id").write_bytes(value)
    finally:
        hold.unlink()
    util.wait_until(lambda: faults.daemon_pid() not in (None, pid) and
                    util.docker(["info", "--format", "{{.ID}}"], timeout=10).returncode == 0,
                    timeout=180, interval=1, what="dockerd restarted")
    h.stop_events()
    h.start_events()


# ------------------------------------------------------------------------------------------------ C-A1-14


VARIANT_OVERRIDE = """services:
  backend:
    privileged: true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    devices:
      - /dev/loop0:/dev/loop0
"""
VARIANT_INCLUDE = """include:
  - /srv/pfa34/stage/evil-include.yaml
"""
EVIL_INCLUDE = """services:
  evil:
    image: alpine:3.20
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
"""


def latest_renders(h, inst, operation):
    directory = h.operations_dir(inst.slug) / operation
    renders = {}
    for path in sorted(directory.glob("compose-*.json")):
        try:
            renders[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
    return renders


def normalized_services(render):
    """Service/mount/device/privilege/include facts of one pf render (image names and paths vary per operation)."""
    model = render.get("model") or render.get("compose") or render
    services = (model.get("services") if isinstance(model, dict) else None) or {}
    facts = {}
    for name, service in sorted(services.items()):
        facts[name] = {"privileged": bool(service.get("privileged")), "devices": service.get("devices") or [],
                       "volumes": sorted(json.dumps(item, sort_keys=True) for item in service.get("volumes") or []
                                         if "docker.sock" in json.dumps(item) or (isinstance(item, dict) and
                                                                                 item.get("type") == "bind")),
                       "environment_keys": sorted((service.get("environment") or {}).keys())
                       if isinstance(service.get("environment"), dict) else []}
    return {"services": facts, "include": model.get("include") if isinstance(model, dict) else None}


def running_privileges(inst):
    found = []
    for service in ("db", "backend", "frontend"):
        container = observe.running_container(inst.project, service)
        if not container:
            continue
        item = util.docker_json(["inspect", container])[0]
        host = item.get("HostConfig") or {}
        found.append({"service": service, "privileged": host.get("Privileged"),
                      "devices": host.get("Devices") or [],
                      "sock": [m for m in item.get("Mounts") or [] if "docker.sock" in json.dumps(m)],
                      "binds": [m for m in item.get("Mounts") or [] if m.get("Type") == "bind"]})
    return found


def c_a1_14(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A1-T14")
    variants = {}
    # The real `docker compose config` render of the approved topology (a throwaway harness project name).
    release = sorted((h.pfroot / "releases").iterdir())[-1]
    op, _, _ = checks.newest(alpha, "update")
    app_env = h.operations_dir("alpha") / op / "app.env"
    render = util.run([util.DOCKER, "compose", "-p", "pfa34render", "--project-directory", str(alpha.workspace),
                       "--env-file", str(app_env), "-f", str(release / "compose.nas.yaml"), "config"],
                      env={"PARTFLOW_REPO_ROOT": str(alpha.workspace), "DEPLOY_ADMIN_INSTANCE_ID":
                           alpha.info["instance_id"], "PARTFLOW_DATABASE_URL": "postgresql+psycopg://redacted@db/x"},
                      timeout=120)
    h.evidence.write_json("records/C-A1-14-compose-config.json", {"exit": render.returncode,
                                                                  "stdout": h.evidence.redact(render.stdout)[:20000],
                                                                  "stderr": render.stderr[-2000:]})
    verdict.check("C-A1-14: real docker compose config render of the approved topology recorded",
                  render.returncode == 0, render.stderr[-300:])
    clean_renders = latest_renders(h, alpha, op)
    clean = {name: normalized_services(value) for name, value in clean_renders.items()}
    # (a) .env adds COMPOSE_FILE / COMPOSE_PROFILES / PARTFLOW_REPO_ROOT=/
    env_path = alpha.config_dir / ".env"
    original = env_path.read_bytes()
    try:
        env_path.write_bytes(original + b"COMPOSE_FILE=/srv/x.yaml\nCOMPOSE_PROFILES=evil\nPARTFLOW_REPO_ROOT=/\n")
        h.external_event("config-edit", f"append COMPOSE_FILE/COMPOSE_PROFILES/PARTFLOW_REPO_ROOT to {env_path}")
        sha = h.commit("NEXT")
        result, record = noeffect.refused(h, alpha, ["update", "--commit", sha, "--skip-ci"],
                                          step_id="C-A1-14-a", phrases=())
        variants["a"] = {"outcome": "refused" if record["refused"] else "ran", "noeffect": record["result"],
                         "copy": (result.stdout + result.stderr)[-600:]}
        verdict.check("C-A1-14 (a): refused before build/up", record["refused"] and record["result"] == "passed",
                      variants["a"])
    finally:
        env_path.write_bytes(original)
        h.external_event("config-edit", f"restore {env_path}")
    # (b)-(d) workspace files: pf renders only -f <control>/compose.nas.yaml plus its own override
    stage = h.root / "stage"
    stage.mkdir(exist_ok=True)
    (stage / "evil-include.yaml").write_text(EVIL_INCLUDE, encoding="utf-8")
    plants = {
        "b": [(alpha.workspace / "compose.override.yaml", VARIANT_OVERRIDE)],
        "c": [(alpha.workspace / "backend" / "pfa34-escape", "->/etc")],
        "d": [(alpha.workspace / "compose.yaml", "services: {}\n" + VARIANT_INCLUDE),
              (alpha.workspace / "compose.override.yaml", VARIANT_INCLUDE)],
    }
    targets = {"b": "NEW", "c": "NEXT", "d": "NEW"}
    for key in ("b", "c", "d"):
        for path, content in plants[key]:
            if content.startswith("->"):
                if os.path.lexists(str(path)):
                    os.unlink(str(path))
                os.symlink(content[2:], str(path))
            else:
                path.write_text(content, encoding="utf-8")
            h.external_event("workspace-edit", f"plant {path}", note=f"C-A1-14 ({key})")
        name = targets[key]
        before_events = time.time()
        result = update_to(h, alpha, name, f"C-A1-14-{key}", migrations=False, expect_exit=None, allow_fail=True)
        op, plan, journal = checks.newest(alpha, "update")
        renders = {item: normalized_services(value) for item, value in latest_renders(h, alpha, op).items()}
        evil = [item for item in running_privileges(alpha) if item["privileged"] or item["devices"] or item["sock"]]
        started = [item for item in h.events_between(before_events) if item.get("Type") == "container"
                   and ((item.get("Actor") or {}).get("Attributes") or {}).get("com.docker.compose.service")
                   == "evil"]
        inert = result.exit == 0 and not evil and not started and \
            all(value["services"] == clean[next(iter(clean))]["services"] for value in renders.values()
                if value["services"]) if clean else False
        variants[key] = {"outcome": "refused" if result.exit != 0 else ("inert" if inert else "NOT inert"),
                         "exit": result.exit, "running_privileges": running_privileges(alpha),
                         "renders_equal_clean": inert, "evil_events": len(started),
                         "confirmed_before_refusal": bool(result.typed),
                         "copy": (result.stderr or "")[-400:]}
        verdict.check(f"C-A1-14 ({key}): refused before build/up or provably inert",
                      variants[key]["outcome"] in ("refused", "inert") and not evil and not started, variants[key])
        open_op = h.open_operation("alpha")
        if open_op:
            # a refusal after the confirmation left an open operation: its documented route (recorded)
            recovery = matrix.recover(h, alpha, open_op["operation_id"], row=f"C-A1-14-{key}-recover")
            variants[key]["recovery"] = {"routes_taken": recovery["routes_taken"],
                                         "final_phase": recovery.get("final_phase")}
            variants[key]["outcome"] += " (after the confirmation and the services stop; reopened by the route)"
        for path, content in plants[key]:
            for candidate in (path, alpha.workspace / path.relative_to(alpha.workspace)):
                if os.path.lexists(str(candidate)):
                    os.unlink(str(candidate))
    after = update_to(h, alpha, "NEW", "C-A1-14-clean-again", migrations=False, expect_exit=None, allow_fail=True)
    op, _, _ = checks.newest(alpha, "update")
    again = {name: normalized_services(value) for name, value in latest_renders(h, alpha, op).items()}
    verdict.check("C-A1-14: the clean topology re-renders with identical app values",
                  after.exit == 0 and [value["services"] for value in again.values() if value["services"]][:1] ==
                  [value["services"] for value in clean.values() if value["services"]][:1])
    h.evidence.write_json("records/C-A1-14-variants.json", variants)
    return case_result(h, "A1-T14", verdict, extra={"variants": {key: value["outcome"] for key, value in
                                                                 variants.items()}})


# ------------------------------------------------------------------------------------------------ C-A3-03


def new_backend_image(h, inst):
    """The NEW backend image this instance built (a candidate or active tag naming NEW's short SHA)."""
    short = h.commit("NEW")[:12]
    lines = util.docker_checked(["images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}",
                                 "--filter", f"reference={inst.project}-backend:*"]).splitlines()
    for line in lines:
        reference, image = line.split()
        if short in reference:
            return reference, image
    return None, None


def c_a3_03(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A3-T03-out-of-band")
    b4 = h.fixture.get("B4")
    rollback_to(h, alpha, b4, "C-A3-03-to-OLD")
    verdict.check("C-A3-03: alpha at OLD/0031", heads_prefix(alpha) == "0031", alpha.heads())
    alpha.seeder().marker("W6", movement=True)
    reference, image = new_backend_image(h, alpha)
    backend = alpha.backend_container()
    url = None
    for entry in util.docker_json(["inspect", backend])[0]["Config"]["Env"]:
        if entry.startswith("DATABASE_URL="):
            url = entry.split("=", 1)[1]
    network = util.docker_json(["inspect", backend])[0]["NetworkSettings"]["Networks"]
    network_name = sorted(network)[0]
    if not image or not url:
        return case_result(h, "A3-T03-out-of-band", verdict, blocked="no NEW backend image or DATABASE_URL")
    h.external_event("out-of-band-migration", f"docker run --rm --network {network_name} -e DATABASE_URL=<alpha> "
                     f"{reference} uv run alembic upgrade head", target="alpha database",
                     note="C-A3-03: 0032 applied while pf's active image is OLD")
    run = util.docker(["run", "--rm", "--network", network_name, "--label", SCRATCH, "-e", "DATABASE_URL=" + url,
                       image, "uv", "run", "--no-sync", "alembic", "upgrade", "head"], timeout=600)
    verdict.check("C-A3-03: out-of-band migration applied", run.returncode == 0 and heads_prefix(alpha) == "0032",
                  run.stderr[-400:])
    rollback_to(h, alpha, b4, "C-A3-03-rollback")
    op, plan, journal = checks.newest(alpha, "rollback")
    effects = {item["effect_id"]: item for item in journal.get("effects") or []}
    capture = [item for item in plan.get("effects") or [] if item["type"] == "capture"]
    switch = [item for item in plan.get("effects") or [] if item["type"] == "database-switch"]
    order_ok = bool(capture and switch) and \
        (effects[capture[0]["effect_id"]].get("observed_at") or "") <= \
        (effects[switch[0]["effect_id"]].get("observed_at") or "~")
    verdict.check("C-A3-03: completed", journal.get("phase") == "completed", journal.get("phase"))
    verdict.check("C-A3-03: the preservation completed before the switch", order_ok,
                  {key: value.get("observed_at") for key, value in effects.items()})
    captures = checks.retained(journal)
    if captures:
        probe = checks.probe_checkpoint(h, alpha, captures[0], "C-A3-03-capture")
        verdict.check("C-A3-03: the capture holds the 0032 data with F(0032)",
                      (probe.get("heads") or ["?"])[0].startswith("0032") and "PFA34-W6" in (probe.get("markers") or [])
                      and (probe.get("snapshot") or {}).get("schema", {}).get("sha256") == h.fixture.get("F_0032"),
                      {"heads": probe.get("heads"), "markers": probe.get("markers")})
    else:
        verdict.check("C-A3-03: a current-data capture retained", False, journal.get("retained_artifacts"))
    status = case_result(h, "A3-T03-out-of-band", verdict)
    update_to(h, alpha, "NEW", "C-A3-03-back-to-NEW")
    return status


# ------------------------------------------------------------------------------------------------ C-A3-16


def c_a3_16(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A3-T16-reset")
    alpha.seeder().marker("W5", movement=True)
    row = {"id": "C-A3-16", "kind": "reset-db", "phase": "switching", "effect_type": "database-switch",
           "injection": "I3 SIGKILL after the switch (database-switch complete)",
           "spec": {"primitive": "I3", "signal": "SIGKILL",
                    "gate": matrix.effect_trigger(h, "alpha", effect_type="database-switch", states=("complete",))},
           "prepare": lambda h, inst: {}, "command": matrix.cmd_reset, "data_relation": "restored"}
    outcome = matrix.execute_row(h, row, alpha)
    record = h.results["rows"].get("C-A3-16", {})
    verdict.check("C-A3-16: resume reconciles after a kill after the switch", outcome == "passed", record.get("reason"))
    op = record.get("operation_id")
    journal = checks.journal_of(alpha, op) if op else {}
    plan = h.operation_files("alpha", op).get("plan.json", {}) if op else {}
    verdict.check("C-A3-16: the operator's choice is in the plan/confirmation summary",
                  bool((plan.get("confirmation") or {}).get("phrase")), plan.get("confirmation"))
    captures = checks.retained(journal)
    if captures:
        probe = checks.probe_checkpoint(h, alpha, captures[0], "C-A3-16-before-reset")
        verdict.check("C-A3-16: W5 recoverable from the before-reset capture with F(0032)",
                      "PFA34-W5" in (probe.get("markers") or []) and
                      (probe.get("snapshot") or {}).get("schema", {}).get("sha256") == h.fixture.get("F_0032"),
                      probe.get("markers"))
    else:
        verdict.check("C-A3-16: before-reset capture retained", False, journal.get("retained_artifacts"))
    status = case_result(h, "A3-T16-reset", verdict)
    # documented route back to data: rollback to B4 (OLD), then update NEW
    instances_module.wait_pf_idle(timeout=300)
    rollback_to(h, alpha, h.fixture["B4"], "C-A3-16-restore-data")
    update_to(h, alpha, "NEW", "C-A3-16-back-to-NEW")
    return status


# ----------------------------------------------------------------------------------------------- C-CONC-01


class Writer(threading.Thread):
    """Concurrent synthetic writes through the HTTP API: Work Orders and station transfers, each with a unique
    device_event_id; every answer status and time is recorded. A write is never retried."""

    def __init__(self, h, inst):
        super().__init__(daemon=True)
        self.h, self.inst = h, inst
        self.seeder = api.Seeder(h, inst.slug, inst.port, inst.project)
        self.seeder.client.credentials = (self.seeder.ids["admin_login"], self.seeder.secret["admin_password"])
        self.stop_event = threading.Event()
        self.log = []
        self.counter = 0

    def run(self):
        while not self.stop_event.is_set():
            self.counter += 1
            name = f"PFA34-C{self.counter:04d}"
            started = time.time()
            entry = {"n": self.counter, "work_order": name, "sent": started}
            try:
                order = self.seeder.work_order(name, self.seeder.ids["part_numbers"][0], 1)
                entry.update(work_order_status=201, work_order_id=order["id"], acked=time.time())
                release = self.seeder.release(order, 1, planned=False)
                event = api.new_event_id()
                entry["device_event_id"] = event
                moved = self.seeder.transfer(release, 1, event_id=event)
                entry.update(movement_status=201, movement_id=moved["movement_id"], movement_acked=time.time())
            except (util.HarnessError, OSError) as exc:
                entry["error"] = str(exc)[:200]
                entry["failed_at"] = time.time()
            self.log.append(entry)
            time.sleep(0.2)

    def stop(self):
        self.stop_event.set()
        self.join(timeout=120)


def backend_events(h, project, window, actions):
    """Event times of the backend SERVICE container (never a `compose run backend` one-off: pf's contract probe and
    migrations run one-offs of the backend service while the service still answers)."""
    found = []
    for item in h.events_between(*window):
        attributes = (item.get("Actor") or {}).get("Attributes") or {}
        if item.get("Type") == "container" and (item.get("Action") or "").split(":")[0] in actions \
                and attributes.get("com.docker.compose.project") == project \
                and attributes.get("com.docker.compose.service") == "backend" \
                and attributes.get("com.docker.compose.oneoff") != "True":
            found.append(item.get("timeNano") / 1e9)
    return sorted(found)


def downtime(h, project, window):
    """(first backend stop, first backend start after it) inside ``window`` (the services:stop effect window)."""
    stops = backend_events(h, project, window, ("stop", "kill"))
    if not stops:
        return None, None
    starts = [item for item in backend_events(h, project, window, ("start",)) if item > stops[0]]
    return stops[0], (starts[0] if starts else window[1])


def acked(entry):
    """(kind, name, time) of every 2xx-acknowledged write of one writer entry."""
    found = []
    if entry.get("work_order_status") == 201:
        found.append(("work_order", entry["work_order"], entry["acked"]))
    if entry.get("movement_status") == 201:
        found.append(("movement", entry["device_event_id"], entry["movement_acked"]))
    return found


def c_conc_01(h):
    charlie = h.instance("charlie")
    verdict = Verdict("C-CONC-01")
    writer = Writer(h, charlie)
    writer.start()
    time.sleep(3)
    windows = {}
    try:
        # (a) backup capturing with the application running
        result = checks.run_alpha_step(h, charlie, ["backup"], step_id="C-CONC-01-a-backup", l5_exclude=("charlie",))
        op, plan, journal = checks.newest(charlie, "backup")
        checkpoint_a = (checks.retained(journal) or [None])[0]
        windows["a"] = (checkpoint_a, None, result.window)
        # (b) update: the services:stop effect and the pre-update capture
        sha = h.commit("NEW")
        result = checks.run_alpha_step(h, charlie, ["update", "--commit", sha, "--allow-migrations", "--skip-ci"],
                                       step_id="C-CONC-01-b-update", phrases=phrases.exact("UPDATE " + sha[:12]),
                                       l5_exclude=("charlie",))
        op, plan, journal = checks.newest(charlie, "update")
        windows["b"] = ((checks.retained(journal) or [None])[0], downtime(h, charlie.project, result.window),
                        result.window)
        live_b = checks.live_markers(charlie)
        # (c) rollback to (a)'s checkpoint
        db = charlie.env()["POSTGRES_DB"]
        result = checks.run_alpha_step(h, charlie, ["rollback", checkpoint_a, "--restore-db"],
                                       step_id="C-CONC-01-c-rollback",
                                       phrases=phrases.exact("RESTORE " + db + " " + checkpoint_a),
                                       l5_exclude=("charlie",))
        op, plan, journal = checks.newest(charlie, "rollback")
        windows["c"] = ((checks.retained(journal) or [None])[0], downtime(h, charlie.project, result.window),
                        result.window)
    finally:
        writer.stop()
    h.evidence.write_json("records/C-CONC-01-writer.json", writer.log)
    h.evidence.write_json("records/C-CONC-01-windows.json", windows)
    writes = [item for entry in writer.log for item in acked(entry)]
    verdict.check("C-CONC-01: the writer acknowledged writes during the run", len(writes) > 5, len(writes))
    failed = [entry for entry in writer.log if entry.get("error")]
    for name, (checkpoint, stopped, window) in sorted(windows.items()):
        if not checkpoint:
            verdict.check(f"C-CONC-01 ({name}): capture present", False)
            continue
        probe = checks.probe_checkpoint(h, charlie, checkpoint, f"C-CONC-01-{name}", reconcile=True)
        extra = probe.get("extra") or {}
        orders, events = set(extra.get("C") or []), set(extra.get("E") or [])
        reconcile = probe.get("reconcile") or {}
        verdict.check(f"C-CONC-01 ({name}): reconcile on the restored capture exits 0", reconcile.get("exit") == 0,
                      reconcile.get("stderr_summary"))
        partial = [entry["work_order"] for entry in writer.log if entry.get("device_event_id") in events
                   and entry["work_order"] not in orders]
        verdict.check(f"C-CONC-01 ({name}): every Movement command entirely present or absent "
                      "(its Work Order present with it; reconcile clean)", not partial, partial[:5])
        if stopped and stopped[0]:
            stop, start = stopped
            before = [item for item in writes if item[2] < stop - 0.2]
            missing = [item for item in before if (item[0] == "work_order" and item[1] not in orders) or
                       (item[0] == "movement" and item[1] not in events)]
            verdict.check(f"C-CONC-01 ({name}): every write acknowledged before the stop is in the capture",
                          not missing, missing[:5])
            during = [item for item in writes if stop < item[2] < start]
            verdict.check(f"C-CONC-01 ({name}): no write acknowledged 2xx between the stop and the restart",
                          not during, during[:5])
            if name == "b":
                live_orders, live_events = set(live_b.get("concurrent") or []), set(live_b.get("events") or [])
                lost = [item for item in before if (item[0] == "work_order" and item[1] not in live_orders) or
                        (item[0] == "movement" and item[1] not in live_events)]
                verdict.check("C-CONC-01 (b): every write acknowledged before the stop is live after the update",
                              not lost, lost[:5])
        elif name in ("b", "c"):
            verdict.check(f"C-CONC-01 ({name}): the services stop observed in daemon events", False, stopped)
    h.fixture["C_CONC_01_failed_requests"] = len(failed)
    return case_result(h, "C-CONC-01", verdict, extra={"writes": len(writer.log), "acked": len(writes),
                                                       "failed_or_timed_out": len(failed)})


# ------------------------------------------------------------------------------------------- C-A3-08/07/06


def c_a3_08(h):
    alpha, charlie = h.instance("alpha"), h.instance("charlie")
    verdict = Verdict("A3-T08")
    t06 = Verdict("A3-T06-charlie")
    bundle, _, invariants = purge_step(h, charlie, "C-A3-08-charlie-purge", verdict, t06)
    h.fixture["Pc"] = bundle
    verdict.check("C-A3-08: charlie's purge bundle Pc with app-invariants clean", bundle is not None and t06.ok,
                  t06.failures)
    source = checks.find_bundle_dir(charlie, bundle) if bundle else None
    target = alpha.recovery / alpha.project / bundle if bundle else None
    if source and target:
        target.parent.mkdir(parents=True, exist_ok=True)
        util.run(["cp", "-a", str(source), str(target)], timeout=600)
        h.external_event("carry-over", f"cp -a {source} {target}", target=str(target),
                         note="declared operator carry-over (SPEC 5.1 (i), OD-A34-14)")
        for argv, step in ((["restore-instance", bundle], "C-A3-08-exact"),
                           (["restore-instance", bundle, "--side-by-side"], "C-A3-08-side-by-side")):
            result, record = noeffect.refused(h, alpha, argv, step_id=step, expect_text="restore-target-mismatch",
                                              phrases=())
            verdict.check(f"{step}: refused with restore-target-mismatch before any confirmation",
                          record["refused"] and record["expected_text_seen"] and not result.typed and
                          not result.prompts, {"typed": result.typed, "copy": result.stderr[-300:]})
            loads = [item for item in oracles.mutating_events(h.events_between(*record["window"]))
                     if item["action"] in ("load", "create")]
            verdict.check(f"{step}: no load/create event; N holds", not loads and record["result"] == "passed",
                          {"events": loads, "tree": record.get("tree_diff")})
        shutil.rmtree(str(target))
        h.external_event("carry-over-removed", f"rm -rf {target}", target=str(target))
    else:
        verdict.check("C-A3-08: the carry-over copy could be placed", False)
    p_bundle = h.fixture.get("P")
    result, record = noeffect.refused(h, alpha, ["restore-instance", p_bundle], step_id="C-A3-08-occupied",
                                      phrases=())
    verdict.check("C-A3-08: restore into an occupied target refused (RX-4) with N",
                  record["refused"] and record["result"] == "passed", result.stderr[-400:])
    return case_result(h, "A3-T08", verdict)


def c_a3_07(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A3-T07")
    pre = alpha.identities("C-A3-07-pre")
    util.docker_checked(["volume", "create", "--label", f"io.deploy-admin.instance-id={alpha.info['instance_id']}",
                         "--label", SCRATCH, "stray"])
    h.external_event("I7", f"docker volume create --label io.deploy-admin.instance-id={alpha.info['instance_id']} "
                           "stray", target="stray")
    try:
        env = alpha.env()
        allowed = phrases.exact("PURGE " + alpha.project, "DELETE " + env["POSTGRES_DB"]) + \
            (r"ERASE " + alpha.project + r" [0-9A-F]{6}",)
        result, record = noeffect.refused(h, alpha, ["purge", "--keep-backups"], step_id="C-A3-07-purge",
                                          phrases=allowed, timeout=7200)
        text = result.stdout + result.stderr
        op, plan, journal = checks.newest(alpha, "purge")
        completed = journal.get("phase") == "completed" and result.exit == 0
        stray_present = "stray" in util.docker_checked(["volume", "ls", "-q"]).split()
        frozen_path = h.operations_dir("alpha") / op / "deletion-plan.json" if op else None
        frozen = json.loads(frozen_path.read_text(encoding="utf-8")) if frozen_path and frozen_path.exists() else {}
        if completed:
            # PZ-5 branch: the stray is outside the frozen plan and retained; the purge itself completed
            outcome = "retained (PZ-5)"
            planned = [item.get("key") for item in frozen.get("candidates") or []]
            excluded = {item.get("key"): item.get("class") for item in frozen.get("exclusions") or []}
            verdict.check("C-A3-07: the stray volume is retained, excluded from the frozen plan, never planned",
                          stray_present and "stray" not in planned and excluded.get("stray") is not None,
                          {"exclusion": excluded.get("stray"), "copy": text[-300:]})
        else:
            outcome = "refused"
            coded = any(code in text for code in ("coverage-incomplete", "resource-"))
            verdict.check("C-A3-07: the purge refused with coverage-incomplete or a blocker", coded, text[-600:])
            post = alpha.identities("C-A3-07-post")
            verdict.check("C-A3-07: nothing deleted", {item["name"] for item in pre["volumes"]} <=
                          {item["name"] for item in post["volumes"]} and stray_present)
        verdict.check("C-A3-07: the outcome is not an unrelated refusal", "daemon-drift" not in text, text[-200:])
        h.fixture["C_A3_07"] = {"outcome": outcome, "noeffect": record["result"], "copy": text[-800:]}
        open_op = h.open_operation("alpha")
        if open_op:
            matrix.recover(h, alpha, open_op["operation_id"], row="C-A3-07-recover")
        if completed:
            bundle = next((item["name"] for item in journal.get("retained_artifacts") or []
                           if item.get("kind") == "purge-bundle"), None)
            alpha.info["purged"] = True
            h.save()
            util.docker(["volume", "rm", "stray"])
            h.external_event("I7-removed", "docker volume rm stray", target="stray",
                             note="before the restore of the new bundle")
            restore_instance(h, alpha, bundle, "C-A3-07-restore")
    finally:
        if util.docker(["volume", "rm", "stray"]).returncode == 0:
            h.external_event("I7-removed", "docker volume rm stray", target="stray")
    return case_result(h, "A3-T07", verdict, extra={"outcome": h.fixture.get("C_A3_07", {}).get("outcome")})


FOREIGN = (("container", "pfa34-foreign-mount"), ("container", "pfa34-foreign-bind"),
           ("container", "othersite-external-1"), ("volume", "partflow_extra"), ("volume", "partflow_shared_ext"),
           ("network", "partflow_shared_net"), ("tag", "othersite/shared:1"))


def remove_foreign(h, names=None):
    """Remove the C-A1-11/12 foreign resources the harness created (declared external events); idempotent."""
    for kind, name in FOREIGN:
        if names is not None and name not in names:
            continue
        argv = {"container": ["rm", "-f", name], "volume": ["volume", "rm", name], "network": ["network", "rm", name],
                "tag": ["image", "rm", name]}[kind]
        if util.docker(argv, timeout=120).returncode == 0:
            h.external_event("I7-removed", " ".join(["docker"] + argv), target=name)


def c_a1_11_12_a3_06(h):
    """Alpha's purge with prefix-similar, shared, bind and external resources present (C-A1-11, C-A1-12) and a
    verified backup X immediately before it (C-A3-06); then the restore of the new bundle.

    Stage 1: a stopped foreign container mounting alpha's data volume makes the volume shared; the shipped purge
    refuses before any confirmation (blocker) and N holds. Stage 2: with that container removed, the remaining foreign
    resources (a foreign container binding a path under alpha's home, alpha's backend image also tagged for othersite,
    the prefix-similar volume partflow_extra, an external volume/network referenced by an othersite container) are
    classified and excluded; the purge completes; the shared tag still resolves to the same image."""
    alpha = h.instance("alpha")
    v11, v12, v06 = Verdict("A1-T11"), Verdict("A1-T12"), Verdict("A3-T06")
    remove_foreign(h)
    bundle = None
    try:
        util.docker_checked(["volume", "create", "--label", SCRATCH, "partflow_extra"])
        util.docker_checked(["volume", "create", "--label", SCRATCH, "partflow_shared_ext"])
        util.docker_checked(["network", "create", "--label", SCRATCH, "partflow_shared_net"])
        util.docker_checked(["run", "-d", "--name", "othersite-external-1", "--label",
                             "com.docker.compose.project=othersite", "--label", SCRATCH, "-v",
                             "partflow_shared_ext:/ext", "--network", "partflow_shared_net", "alpine:3.20", "sleep",
                             "2147483647"])
        data_volume = next(item["name"] for item in alpha.identities()["volumes"]
                           if item["name"].endswith("postgres_data"))
        util.docker_checked(["create", "--name", "pfa34-foreign-mount", "--label", SCRATCH, "-v",
                             f"{data_volume}:/data:ro", "alpine:3.20", "true"])
        util.docker_checked(["create", "--name", "pfa34-foreign-bind", "--label", SCRATCH, "-v",
                             f"{alpha.home}/config:/x:ro", "alpine:3.20", "true"])
        backend_image = checks.running_images(alpha).get("backend")
        util.docker_checked(["tag", backend_image, "othersite/shared:1"])
        for kind, name in FOREIGN:
            h.external_event("I7", f"create {kind} {name}", target=name, note="C-A1-11/12 foreign resources")
        env = alpha.env()
        allowed = phrases.exact("PURGE " + alpha.project, "DELETE " + env["POSTGRES_DB"]) + \
            (r"ERASE " + alpha.project + r" [0-9A-F]{6}",)
        # stage 1: the shared data volume blocks the purge before any confirmation
        result, record = noeffect.refused(h, alpha, ["purge", "--keep-backups"], step_id="C-A1-12-shared-volume",
                                          phrases=(), expect_text="resource-shared")
        v12.check("C-A1-12: a foreign container on alpha's data volume classifies it shared and blocks the purge "
                  "before any confirmation", record["refused"] and record["expected_text_seen"] and not result.typed,
                  (result.stdout + result.stderr)[-600:])
        v12.check("C-A1-12: N holds for the blocked purge", record["result"] == "passed", record.get("tree_diff"))
        remove_foreign(h, ["pfa34-foreign-mount"])
        # stage 2 + C-A3-06: a verified backup X, then the purge with the remaining foreign resources
        checks.run_alpha_step(h, alpha, ["backup"], step_id="C-A3-06-backup-X")
        op, plan, journal = checks.newest(alpha, "backup")
        h.fixture["X"] = (checks.retained(journal) or [None])[0]
        argv_log = faults.ArgvLog()
        result = checks.run_alpha_step(h, alpha, ["purge", "--keep-backups"], step_id="C-A1-12-purge",
                                       phrases=allowed, on_tick=argv_log.tick, timeout=7200, expect_exit=None,
                                       allow_fail=True, l5_exclude=("othersite",))
        h.evidence.write_json("records/C-A1-12-argv-log.json", argv_log.entries())
        op, plan, journal = checks.newest(alpha, "purge")
        directory = h.operations_dir("alpha") / op
        frozen = json.loads((directory / "deletion-plan.json").read_text(encoding="utf-8")) \
            if (directory / "deletion-plan.json").exists() else {}
        preliminary = json.loads((directory / "inventory-preliminary.json").read_text(encoding="utf-8")) \
            if (directory / "inventory-preliminary.json").exists() else {}
        h.evidence.write_json("records/C-A1-12-deletion-plan.json", frozen)
        h.evidence.write_json("records/C-A1-12-inventory-preliminary.json", preliminary)
        candidates = json.dumps(frozen.get("candidates") or [])
        v11.check("C-A1-11: partflow_extra and bravo's partflow_test are not planned",
                  bool(frozen) and "partflow_extra" not in candidates and "partflow_test" not in candidates,
                  candidates[:400])
        v12.check("C-A1-12: the foreign bind container, the external volume/network and othersite are not planned",
                  bool(frozen) and not [name for name in ("pfa34-foreign-bind", "partflow_shared_ext",
                                                          "partflow_shared_net", "othersite") if name in candidates])
        v12.check("C-A1-12: the shared tag still resolves to the same image ID",
                  util.docker(["image", "inspect", "--format", "{{.Id}}", "othersite/shared:1"]).stdout.strip()
                  == backend_image)
        v12.check("C-A1-12: the external volume/network and the foreign containers still exist",
                  "partflow_shared_ext" in util.docker_checked(["volume", "ls", "-q"]).split() and
                  util.docker(["network", "inspect", "partflow_shared_net"]).returncode == 0 and
                  util.docker(["inspect", "pfa34-foreign-bind"]).returncode == 0)
        v12.check("C-A1-12: no global prune in the argv log",
                  not [item for item in argv_log.entries() if "prune" in item["argv"]])
        v12.check("C-A1-12: the purge completed", journal.get("phase") == "completed",
                  (result.stdout + result.stderr)[-800:])
        if journal.get("phase") == "completed":
            bundle = next((item["name"] for item in journal.get("retained_artifacts") or []
                           if item.get("kind") == "purge-bundle"), None)
            alpha.info["purged"] = True
            from .loop2 import verification_records
            records = verification_records(alpha, bundle) if bundle else []
            text = json.dumps([record for _, record in records])
            v06.check("C-A3-06: the purge gate record names the final bundle, never X",
                      bool(bundle) and bundle in text and (h.fixture.get("X") or "~") not in text,
                      {"bundle": bundle, "X": h.fixture.get("X")})
        else:
            v06.blocked("C-A3-06", "the purge did not complete")
    finally:
        remove_foreign(h)
    h.fixture["P2"] = bundle
    status = [case_result(h, "A1-T11", v11), case_result(h, "A1-T12", v12), case_result(h, "A3-T06", v06)]
    if bundle:
        status.append(c_a3_14_b(h, alpha, bundle))
        restore_instance(h, alpha, bundle, "C-A1-12-restore")
    return status


def c_a3_14_b(h, alpha, bundle):
    """C-A3-14 (b) / RX-7 (181a806 fix): a restore whose bundle image is absent needs images.tar in the Docker root;
    with that device short it is refused before any effect with the '<phase> needs' copy and N holds."""
    verdict = Verdict("A3-T14-docker-storage-b")
    if (Path("/pfa34/evidence/env/capacity-mode.txt").read_text().strip()
            if Path("/pfa34/evidence/env/capacity-mode.txt").exists() else "") != "loop":
        return case_result(h, "A3-T14-docker-storage-b", verdict, blocked="no loop devices (OD-A34-06)")
    folder = checks.find_bundle_dir(alpha, bundle)
    manifest = checks.manifest(folder) or {}
    image = ((manifest.get("images") or {}).get("backend") or {}).get("id")
    if image and util.docker(["image", "inspect", image]).returncode == 0:
        util.docker(["image", "rm", "-f", image], timeout=300)
        h.external_event("I7", f"docker image rm -f {image[:19]}", target=image,
                         note="C-A3-14 (b): the bundle's backend image is absent locally (operator removed it)")
    verdict.check("C-A3-14 (b): the bundle's backend image is absent locally",
                  bool(image) and util.docker(["image", "inspect", image]).returncode != 0, image)
    archive = folder / "images.tar" if folder else None
    size = archive.stat().st_size if archive and archive.exists() else 0
    target, used, _ = ballast("/var/lib/docker", 2048 * 1024 * 1024 + size // 2)
    h.external_event("I7-ballast", f"fallocate -l {used} {target}", target=str(target))
    try:
        db = h.fixture.get("alpha_db", "partflow_staging")
        del db
        result, record = noeffect.refused(h, alpha, ["restore-instance", bundle], step_id="C-A3-14-b-restore",
                                          expect_text="needs", phrases=())
        verdict.check("C-A3-14 (b): restore refused with the '<phase> needs' copy before any effect",
                      record["refused"] and "capacity-insufficient" in result.stderr and record["expected_text_seen"],
                      result.stderr[-500:])
        verdict.check("C-A3-14 (b): N holds", record["result"] == "passed", record.get("tree_diff"))
        verdict.check("C-A3-14 (b): the only checkpoints are never deleted to make room",
                      all(checks.checkpoint_dir(alpha, name).is_dir() for name in (h.fixture.get("B1"),
                                                                                    h.fixture.get("B2")) if name))
    finally:
        if target.exists():
            target.unlink()
        h.external_event("I7-ballast-removed", f"rm {target}", target=str(target))
    return case_result(h, "A3-T14-docker-storage-b", verdict)


# ------------------------------------------------------------------------------------------------ C-A3-14


def ballast(path, keep_free_bytes):
    """Fill the filesystem of ``path`` until ``keep_free_bytes`` remain free (fallocate, declared I7)."""
    stats = os.statvfs(str(path))
    free = stats.f_bavail * stats.f_frsize
    size = max(0, free - keep_free_bytes)
    target = Path(path) / ".pfa34-ballast"
    result = util.run(["fallocate", "-l", str(size), str(target)], timeout=600)
    return target, size, result.returncode


def c_a3_14(h):
    alpha = h.instance("alpha")
    verdict = Verdict("A3-T14-docker-storage")
    mode = Path("/pfa34/evidence/env/capacity-mode.txt").read_text().strip() \
        if Path("/pfa34/evidence/env/capacity-mode.txt").exists() else "unknown"
    if mode != "loop":
        return case_result(h, "A3-T14-docker-storage", verdict, blocked="no loop devices (OD-A34-06)")
    floor = 2048 * 1024 * 1024
    target, size, code = ballast("/var/lib/docker", floor + 64 * 1024 * 1024)
    h.external_event("I7-ballast", f"fallocate -l {size} {target}", target=str(target))
    try:
        result, record = noeffect.refused(h, alpha, ["purge", "--keep-backups"], step_id="C-A3-14-a-purge",
                                          expect_text="needs", phrases=())
        verdict.check("C-A3-14 (a): purge refused with the '<phase> needs' copy before any effect",
                      record["refused"] and "capacity-insufficient" in result.stderr and record["expected_text_seen"],
                      result.stderr[-500:])
        verdict.check("C-A3-14 (a): N holds", record["result"] == "passed", record.get("tree_diff"))
    finally:
        if target.exists():
            target.unlink()
        h.external_event("I7-ballast-removed", f"rm {target}", target=str(target))
    return case_result(h, "A3-T14-docker-storage", verdict)


def run(h, only=None):
    """``only``: case names to run (``--rows``, development re-runs); all cases otherwise."""
    outcomes = {}
    remove_foreign(h)  # a re-run never inherits the foreign resources of an interrupted earlier attempt
    h.fixture["alpha_db"] = h.instance("alpha").env().get("POSTGRES_DB", "partflow_staging")
    for name, function in (("C-A1-13", c_a1_13), ("C-A3-14", c_a3_14), ("C-A1-14", c_a1_14), ("C-A3-03", c_a3_03),
                           ("C-A3-16", c_a3_16), ("C-A3-07", c_a3_07), ("C-CONC-01", c_conc_01),
                           ("C-A3-08", c_a3_08), ("C-A1-11/12+C-A3-06", c_a1_11_12_a3_06)):
        if only and name not in only:
            continue
        h.log(f"case {name}")
        try:
            outcomes[name] = function(h)
        except Exception as exc:  # recorded as a harness error of this case; the next case still runs
            import traceback
            h.log(f"case {name}: harness error {exc!r}\n{traceback.format_exc()}")
            outcomes[name] = "harness-error"
            h.results["cases"].setdefault(name, {"status": "harness-error", "reason": repr(exc)})
        h.log(f"case {name}: {outcomes[name]}")
        h.save()
    flat = [item for value in outcomes.values() for item in (value if isinstance(value, list) else [value])]
    return "passed" if all(item in ("passed", "blocked") for item in flat) else "failed"
