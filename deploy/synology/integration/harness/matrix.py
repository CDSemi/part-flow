"""L4 — LOOP-04 real crash matrix (SPEC 8.3): rows R01..R66, the recovery procedure (SPEC 5.3) and the oracles."""
import json
import os
import re
import signal
import time

from . import checks
from . import faults
from . import instances as instances_module
from . import observe
from . import oracles
from . import phrases
from . import util

TERMINAL = ("completed", "cancelled", "failed_preserved")
ROUTE_RE = re.compile(r"pf --instance (?P<slug>\S+) (?P<args>.+)")
ROUTE_PHRASES = phrases.allow("RESUME", "RESUME PURGE", "RESUME ABORT DEPLOY", "ABANDON", "ABANDON RESTORE",
                              "ABANDON RECOVERY TARGET", "KEEP WORKSPACE", "ACKNOWLEDGE", "RESTORE", "ROLLBACK",
                              "CLEANUP", "REMOVE RECOVERY TARGET", "DELETE CHECKPOINT HISTORY", "ABORT DEPLOY",
                              "RESTORE COPY", "RESTORE INSTANCE", "DEPLOY", "UPDATE", "RESET", "EMERGENCY BACKUP")
STILL_RUNNING = "effect-still-running"
WAIT_BOUND = 900


# ------------------------------------------------------------------------------------------------- routes


def parse_routes(text, slug):
    """[(argv, description)] from the ``next`` line(s) of ``pf status``."""
    routes = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("next"):
            continue
        body = line.split(":", 1)[1] if ":" in line else ""
        for part in body.split("; "):
            command = part.split(": ", 1)[0].strip()
            match = ROUTE_RE.search(command)
            if match and match.group("slug") == slug:
                args = match.group("args").split()
                routes.append((args, part.split(": ", 1)[1] if ": " in part else ""))
    return routes


def pick_route(routes, prefer):
    if prefer:
        for args, description in routes:
            text = " ".join(args)
            if all(word in text for word in prefer):
                return args
    for args, _ in routes:
        if args[:1] == ["resume"] and "--abandon" not in args and "--keep-workspace" not in args:
            return args
    # when the forward route is exhausted: the superseding rollback, then abandon/keep-workspace, then any other route
    # that changes the operation (`backup --emergency` never changes it, so it is never picked automatically)
    for wanted in (lambda a: a[:1] == ["rollback"], lambda a: "--abandon" in a, lambda a: "--keep-workspace" in a,
                   lambda a: a[:2] != ["backup", "--emergency"]):
        for args, _ in routes:
            if wanted(args):
                return args
    return None


def wait_children_gone(h, inst, operation_id, timeout=WAIT_BOUND):
    """SPEC 5.3 step 4: a bounded wait while a named process group / one-off container / session still runs."""
    deadline = time.monotonic() + timeout
    observations = []
    while time.monotonic() < deadline:
        oneoffs = observe.containers(inst.project, all_states=False, oneoff=True)
        groups = []
        directory = h.operations_dir(inst.slug) / operation_id
        try:
            children = json.loads((directory / "children.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            children = []
        for item in children if isinstance(children, list) else []:
            pgid = item.get("pgid")
            if pgid and os.path.exists(f"/proc/{pgid}"):
                groups.append(pgid)
        observations.append({"at": util.utc(), "oneoffs": oneoffs, "groups": groups})
        if not oneoffs and not groups:
            return {"waited": True, "observations": observations[-3:]}
        time.sleep(5)
    return {"waited": False, "observations": observations[-3:]}


def recover(h, inst, operation_id, *, row, prefer=None, max_steps=8, dialogue=()):
    """The SPEC 5.3 recovery procedure. Returns {"steps", "routes_offered", "routes_taken", "wait_states",
    "final_phase", "dead_end"}."""
    record = {"steps": [], "routes_offered": [], "routes_taken": [], "wait_states": [], "dead_end": None,
              "alembic_upgrades": 0}
    argv_log = faults.ArgvLog()
    for number in range(max_steps):
        instances_module.wait_pf_idle(timeout=300)
        status = inst.pf(["status"], step_id=f"{row}-status-{number}", expect_exit=None)
        if number == 0:
            # partial status (exit 1 with "Status is partial") still works: it names the instance and operations
            record["status_works"] = status.exit in (0, 1) and "Operations:" in status.stdout
        if operation_id:
            inst.pf(["status", "--operation", operation_id], step_id=f"{row}-status-op-{number}", expect_exit=None)
        journal = checks.journal_of(inst, operation_id) if operation_id else {}
        phase = journal.get("phase")
        routes = parse_routes(status.stdout, inst.slug)
        record["routes_offered"].append({"phase": phase, "routes": [" ".join(args) for args, _ in routes]})
        none_open = "Operations: none open" in status.stdout
        if phase in TERMINAL or none_open:
            newest_op, _, newest_journal = checks.newest(inst)
            if phase not in TERMINAL and newest_op != operation_id:
                record["superseded_by"] = newest_op
                phase = f"superseded by {newest_op} ({newest_journal.get('phase')})"
            record["final_phase"] = phase
            record["alembic_upgrades"] = live_upgrade_count(argv_log)
            record["argv_log"] = argv_log.entries()[-40:]
            return record
        # A route that already failed twice with the same first error line is exhausted: the next documented route
        # is taken instead (a repeated identical refusal is never retried blindly).
        exhausted = {" ".join(step["route"]) for step in record["steps"]
                     if sum(1 for other in record["steps"] if other["route"] == step["route"] and other["exit"] != 0
                            and other["first_error"] == step["first_error"]) >= 2}
        available = [(args, text) for args, text in routes if " ".join(args) not in exhausted]
        route = pick_route(available, prefer if number == 0 or prefer else None)
        if route is None:
            record["dead_end"] = (f"no route offered at phase {phase}" if not routes else
                                  f"every offered route failed repeatedly at phase {phase}: {sorted(exhausted)}")
            record["final_phase"] = phase
            return record
        record["routes_taken"].append(" ".join(route))
        result = inst.pf(route, step_id=f"{row}-route-{number}", phrases=ROUTE_PHRASES, dialogue=dialogue,
                         expect_exit=None, allow_fail=True, timeout=3600, on_tick=argv_log.tick)
        first_error = next((line for line in result.stderr.splitlines() if line.startswith("ERROR")), "")[:200]
        record["steps"].append({"route": route, "exit": result.exit, "first_error": first_error,
                                "stderr": h.evidence.excerpt(result.stderr, 1500)})
        if STILL_RUNNING in result.stderr:
            wait = wait_children_gone(h, inst, operation_id)
            record["wait_states"].append({"route": route, "wait": wait})
            if not wait["waited"]:
                record["dead_end"] = "effect-still-running past the bound with nothing of the run alive" \
                    if not wait["observations"][-1]["oneoffs"] else "effect-still-running past the bound"
                record["final_phase"] = phase
                return record
            continue
        newest_op, plan, newest_journal = checks.newest(inst)
        if newest_op and newest_op != operation_id and newest_journal.get("phase") in TERMINAL and \
                route[:1] in (["rollback"], ["abort-deploy"], ["cleanup"], ["backup"]):
            record["superseded_by"] = newest_op
    journal = checks.journal_of(inst, operation_id) if operation_id else {}
    record["final_phase"] = journal.get("phase")
    if record["final_phase"] not in TERMINAL:
        record["dead_end"] = record["dead_end"] or f"no terminal state after {max_steps} routes"
    return record


# ------------------------------------------------------------------------------------------------ triggers


def effect_trigger(h, slug, *, effect_type, target_prefix=None, states=("unknown",), phase=None, effect_phase=None):
    """Return a predicate: the newest open operation has an effect of ``effect_type`` (target starting with
    ``target_prefix``; planned in ``effect_phase``) in one of ``states`` (and, optionally, the journal is in
    ``phase``)."""
    def predicate():
        operation, journal = faults.journal_state(h.operations_dir(slug))
        if not journal:
            return None
        if phase and journal.get("phase") != phase:
            return None
        plan_path = h.operations_dir(slug) / operation / "plan.json"
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        states_by_id = {item["effect_id"]: item.get("state") for item in journal.get("effects") or []}
        for effect in plan.get("effects") or []:
            if effect.get("type") != effect_type:
                continue
            if effect_phase and effect.get("phase") != effect_phase:
                continue
            if target_prefix and not str(effect.get("target", "")).startswith(target_prefix):
                continue
            if states_by_id.get(effect["effect_id"]) in states:
                return {"operation": operation, "effect_id": effect["effect_id"], "type": effect_type,
                        "target": effect.get("target"), "state": states_by_id.get(effect["effect_id"]),
                        "phase": journal.get("phase")}
        return None
    return predicate


def phase_trigger(h, slug, phase):
    def predicate():
        operation, journal = faults.journal_state(h.operations_dir(slug))
        if journal and journal.get("phase") == phase:
            return {"operation": operation, "phase": phase}
        return None
    return predicate


def make_injection(h, inst, spec, state):
    """A faults.Trigger for one row's injection ``spec`` (SPEC 5.3). Every journal gate only sees the operations the
    injected command creates (faults.set_baseline)."""
    kind = spec["primitive"]
    signum = getattr(signal, spec.get("signal", "SIGKILL"))
    gate = spec.get("gate")
    words = spec.get("child")
    faults.set_baseline(h.operations_dir(inst.slug))

    if kind == "I8":
        return rename_trigger(h, inst, spec, state, signum)
    if kind == "I3J":
        trigger = journal_phase_trigger(h, inst, spec["phase"], signum)
        trigger.state = state
        return trigger

    def match(process):
        context = gate() if gate else {"gate": "none"}
        if context is None:
            return None
        if words:
            child = faults.child_matching(process, words)
            if child is None:
                return None
            context = dict(context, child=child)
        return context

    def fire(process, target):
        state["window_reached"] = True
        if kind in ("I1", "I3"):
            if spec.get("daemon_restart"):
                faults.restart_daemon(h, wait=False)
                state["daemon_restarted"] = True
            if spec.get("before_signal"):
                spec["before_signal"](process, target)
            return faults.kill_pf(process, signum)
        if kind in ("I2", "I2T"):
            pid = faults.stop_pf(process)
            child = target.get("child", {}).get("pid")
            exited = faults.wait_exit(child, 600) if child else False
            state["child_exited"] = exited
            if not exited:
                os.kill(child, signal.SIGKILL)
                state["recorded_as"] = "I4 (child did not exit while pf was stopped)"
            if spec.get("daemon_restart"):
                faults.restart_daemon(h, wait=True)
                state["daemon_restarted"] = True
            if kind == "I2T":
                os.kill(pid, signal.SIGTERM)
                os.kill(pid, signal.SIGCONT)
                return {"pf_admin_pid": pid, "signal": "SIGTERM+SIGCONT"}
            os.kill(pid, signal.SIGKILL)
            return {"pf_admin_pid": pid, "signal": "SIGKILL"}
        if kind == "I4":
            child = target["child"]["pid"]
            os.kill(child, signal.SIGKILL)
            h.external_event("I4", f"kill -KILL {child} ({' '.join(target['child']['argv'][:6])})")
            return {"child": child}
        if kind == "I6":
            faults.restart_daemon(h, wait=False)
            state["daemon_restarted"] = True
            return {"daemon": "TERM"}
        if kind == "I7":
            return spec["act"](process, target)
        raise ValueError("unknown primitive " + kind)

    return faults.Trigger(match, fire)


def rename_trigger(h, inst, spec, state, signum):
    """I8: inotify (IN_MOVED_FROM|IN_MOVED_TO) on the workspace parent; SIGSTOP pf at the first rename event, record
    whether the registered workspace path is absent (between the two renames), then the row's signal (SIGKILL, or
    SIGTERM followed by SIGCONT)."""
    from . import inotify
    holder = {}

    def match(process):
        if "watch" not in holder:
            holder["watch"] = inotify.Watch(inst.workspace.parent)
        events = holder["watch"].read()
        names = [name for _, name in events]
        if not events:
            return None
        return {"rename_events": names}

    def fire(process, target):
        state["window_reached"] = True
        pid = faults.stop_pf(process)
        between = not inst.workspace.exists()
        state["between_renames_observed"] = between
        state["workspace_present_at_stop"] = not between
        holder["watch"].close()
        if signum == signal.SIGTERM:
            os.kill(pid, signal.SIGTERM)
            os.kill(pid, signal.SIGCONT)
            return {"pf_admin_pid": pid, "signal": "SIGTERM+SIGCONT", "between_renames": between}
        os.kill(pid, signal.SIGKILL)
        return {"pf_admin_pid": pid, "signal": "SIGKILL", "between_renames": between}

    return faults.Trigger(match, fire)


def journal_phase_trigger(h, inst, phase, signum=signal.SIGKILL):
    """A phase with no effect (R09) is passed between two journal writes, too fast for a polled gate. A thread blocks
    on inotify for the new operation's journal replacements, SIGSTOPs pf right after each one, reads the phase, and
    either sends the row's signal (the phase reached) or SIGCONT."""
    import select
    import threading
    from . import inotify
    directory = h.operations_dir(inst.slug)
    holder = {"result": None}

    def watch(launcher_pid):
        baseline = faults.BASELINE.get(str(directory), set())
        top = inotify.Watch(directory, mask=inotify.IN_CREATE | inotify.IN_MOVED_TO)
        try:
            operation = None
            deadline = time.monotonic() + 3600
            while operation is None and time.monotonic() < deadline:
                created = [name for name in sorted(os.listdir(str(directory))) if name not in baseline]
                if created:
                    operation = created[-1]
                    break
                select.select([top.fd], [], [], 0.5)
                top.read()
        finally:
            top.close()
        holder["operation"] = operation
        if operation is None:
            return
        journal_watch = inotify.Watch(directory / operation, mask=inotify.IN_MOVED_TO | 0x00000008)  # IN_CLOSE_WRITE
        seen = []
        try:
            while time.monotonic() < deadline and holder["result"] is None:
                ready, _, _ = select.select([journal_watch.fd], [], [], 0.5)
                if not ready:
                    if not os.path.exists(f"/proc/{launcher_pid}"):
                        return
                    continue
                names = [name for _, name in journal_watch.read()]
                if "journal.json" not in names:
                    continue
                pid = faults.pf_admin_pid(launcher_pid)
                if pid is None:
                    continue
                try:
                    os.kill(pid, signal.SIGSTOP)
                except ProcessLookupError:
                    return
                try:
                    journal = json.loads((directory / operation / "journal.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    journal = {}
                seen.append(journal.get("phase"))
                if journal.get("phase") == phase:
                    os.kill(pid, signum)
                    if signum != signal.SIGKILL:
                        os.kill(pid, signal.SIGCONT)
                    holder["result"] = {"operation": operation, "phase": phase, "pf_admin_pid": pid,
                                        "signal": signal.Signals(signum).name, "phases_seen": seen[-12:]}
                    return
                os.kill(pid, signal.SIGCONT)
        finally:
            journal_watch.close()
            holder["seen"] = seen[-12:]

    def guarded(launcher_pid):
        try:
            watch(launcher_pid)
        except Exception as exc:  # recorded in the row (a harness defect must never pass silently)
            holder["error"] = repr(exc)

    def match(process):
        if "thread" not in holder:
            holder["thread"] = threading.Thread(target=guarded, args=(process.pid,), daemon=True)
            holder["thread"].start()
        return dict(holder["result"]) if holder["result"] else None

    def fire(process, target):
        return {"pf_admin_pid": target["pf_admin_pid"], "signal": target["signal"],
                "phases_seen": target.get("phases_seen")}

    trigger = faults.Trigger(match, fire)
    trigger.holder = holder
    return trigger


# ------------------------------------------------------------------------------------------------- the row


class RowRun:
    def __init__(self, h, row, inst):
        self.h, self.row, self.inst = h, row, inst
        self.record = {"row": row["id"], "kind": row["kind"], "phase": row["phase"],
                       "effect_type": row.get("effect_type"), "effect_target": None,
                       "injection": row["injection"], "attempts": 0, "window_reached": False,
                       "entered_through": "installed launcher /usr/local/bin/pf (real dind daemon)",
                       "grid": row.get("grid", []), "when": row.get("when"), "subject": inst.slug}

    def finish(self, result, reason=None, **extra):
        self.record.update(extra)
        self.record["result"] = result
        self.record["reason"] = reason
        self.h.results.setdefault("rows", {})[self.row["id"]] = self.record
        self.h.evidence.write_json(f"rows/{self.row['id']}.json", self.record)
        self.h.save()
        return result


def state_left(h, inst, operation_id):
    journal = checks.journal_of(inst, operation_id) if operation_id else {}
    oneoffs = observe.containers(inst.project, all_states=False, oneoff=True)
    sessions = None
    container = inst.db_container()
    if container:
        env = inst.env()
        result = observe.psql(container, env["POSTGRES_USER"], "postgres",
                              "SELECT datname || ':' || state || ':' || left(query, 60) FROM pg_stat_activity "
                              "WHERE backend_type = 'client backend' AND pid <> pg_backend_pid();\n", timeout=60)
        sessions = result.stdout.splitlines() if result.returncode == 0 else ["unreadable"]
    return {"journal_phase": journal.get("phase"),
            "effect_states": {item["effect_id"]: item.get("state") for item in journal.get("effects") or []},
            "unresolved": journal.get("unresolved_effect"), "orphans": {"oneoffs": oneoffs, "sessions": sessions},
            "db_heads": inst.heads(), "daemon_facts": {"dockerd_pid": faults.daemon_pid()}}


def live_upgrade_count(argv_log):
    return len([item for item in argv_log.entries() if "alembic" in " ".join(item["argv"])
                and "upgrade" in " ".join(item["argv"]) and "compose" in " ".join(item["argv"])])


def execute_row(h, row, inst):
    """Run one row: precondition, the injected command, the recovery procedure, the oracles."""
    if row.get("custom"):
        return row["custom"](h, row, inst)
    run = RowRun(h, row, inst)
    try:
        context = row["prepare"](h, inst) or {}
    except (util.HarnessError, Exception) as exc:  # a precondition that cannot be reached is an environment block
        return run.finish("blocked", f"precondition: {type(exc).__name__}: {exc}")
    if context.get("subject") is not None:
        # a fresh replacement subject (alpha-r<n>): the gates are bound to its own operations directory
        inst = context["subject"]
        run.inst = inst
        run.record["subject"] = inst.slug
        row = dict(row, spec=dict(row["spec"]))
        if row.get("gate_effect"):
            row["spec"]["gate"] = effect_trigger(h, inst.slug, **row["gate_effect"])
        elif row.get("gate_kind"):
            row["spec"]["gate"] = effect_trigger(h, inst.slug, effect_type=row["gate_kind"])
        elif row.get("gate_phase"):
            row["spec"]["gate"] = phase_trigger(h, inst.slug, row["gate_phase"])
    pre_snapshot = inst.snapshot(f"{row['id']}-pre") if inst.db_container() else None
    others_before = checks.l5_state(h, exclude=(inst.slug,))
    argv, allowed, dialogue = row["command"](h, inst, context)
    state = {"window_reached": False}
    for attempt in range(1, row.get("attempts", 1) + 1):
        run.record["attempts"] = attempt
        argv_log = faults.ArgvLog()
        trigger = make_injection(h, inst, dict(row["spec"]), state)
        if row.get("setup_fault"):
            row["setup_fault"](h, inst, context, state)

        def tick(process, trigger=trigger, argv_log=argv_log):
            argv_log.tick(process)
            trigger.tick(process)

        result = inst.pf(argv, step_id=f"{row['id']}-inject-{attempt}", phrases=allowed, dialogue=dialogue,
                         expect_exit=None, allow_fail=True, on_tick=tick, timeout=row.get("timeout", 3600))
        if row.get("teardown_fault"):
            row["teardown_fault"](h, inst, context, state)
        holder = getattr(trigger, "holder", None)
        if holder is not None and holder.get("thread") is not None:
            holder["thread"].join(timeout=5)  # the watcher ends once the command's process is gone
        if trigger.fired is None and holder and holder.get("result"):
            # I3J killed pf from its watcher thread; the command may have ended before the next tick saw it
            trigger.fired = {"at": util.utc(), "target": dict(holder["result"]),
                             "result": trigger.fire(None, holder["result"])}
        if holder is not None:
            state["window_reached"] = bool(holder.get("result"))
            state["phases_seen"] = (holder.get("result") or {}).get("phases_seen") or holder.get("seen")
            state["watcher"] = {"error": holder.get("error"), "operation": holder.get("operation")}
        run.record["injected"] = trigger.fired
        run.record["inject_exit"] = result.exit
        run.record["inject_stderr"] = h.evidence.excerpt(result.stderr, 1200)
        if trigger.fired is not None:
            break
        if result.exit == 0:
            # the command completed before the window: the next attempt needs the precondition again
            if attempt < row.get("attempts", 1):
                context = row["prepare"](h, inst) or {}
                argv, allowed, dialogue = row["command"](h, inst, context)
    run.record["window_reached"] = bool(state.get("window_reached"))
    run.record["state_flags"] = {key: value for key, value in state.items() if key != "window_reached"}
    if not run.record["window_reached"]:
        reason = (f"window not reached in {run.record['attempts']} attempt(s); command exit "
                  f"{run.record.get('inject_exit')}")
        seen = state.get("phases_seen")
        if row["spec"].get("primitive") == "I3J" and seen and row["spec"]["phase"] not in seen:
            reason += (f"; the journal phases written were {seen}: phase {row['spec']['phase']} is never written for "
                       "this plan (pf enters a phase only at an effect's intent), so the boundary cannot be reached")
        return run.finish("blocked", reason)
    instances_module.wait_pf_idle(timeout=600)
    if state.get("daemon_restarted"):
        util.wait_until(lambda: util.docker(["info", "--format", "{{.ID}}"], timeout=10).returncode == 0,
                        timeout=180, interval=2, what="dockerd back")
        h.stop_events()
        h.start_events()
    if row.get("after_inject"):
        row["after_inject"](h, inst, context, state)
        run.record["state_flags"] = {key: value for key, value in state.items() if key != "window_reached"}
    operation = (trigger.fired.get("target") or {}).get("operation") or checks.newest(inst, row["kind"])[0]
    plan = h.operation_files(inst.slug, operation).get("plan.json", {}) if operation else {}
    run.record["operation_id"] = operation
    effect_id = (trigger.fired.get("target") or {}).get("effect_id")
    if effect_id:
        target = next((item for item in plan.get("effects") or [] if item["effect_id"] == effect_id), {})
        run.record["effect_target"] = target.get("target")
        run.record["effect_id"] = effect_id
        run.record["effect_type"] = target.get("type") or run.record["effect_type"]
    run.record["state_left"] = state_left(h, inst, operation)
    deletion_before = len(json.dumps(plan.get("effects") or []))
    recovery = recover(h, inst, operation, row=row["id"], prefer=row.get("prefer"), dialogue=row.get("dialogue", ()))
    run.record["routes_offered"] = recovery["routes_offered"]
    run.record["routes_taken"] = recovery["routes_taken"]
    run.record["wait_states"] = recovery["wait_states"]
    run.record["outcome"] = recovery.get("final_phase")
    oracles_record = {}
    instances_module.wait_pf_idle(timeout=300)
    plan_after = h.operation_files(inst.slug, operation).get("plan.json", {}) if operation else {}
    oracles_record["deletion_scope_unchanged"] = len(json.dumps(plan_after.get("effects") or [])) == deletion_before
    oracles_record["lock"] = inst.lock_check()
    others_after = checks.l5_state(h, exclude=(inst.slug,))
    restarted = bool(state.get("daemon_restarted"))
    other_problems = checks.l5_compare(others_before, others_after, daemon_restarted=restarted)
    oracles_record["resources"] = {"others_unchanged": not other_problems, "problems": other_problems,
                                   "compared": "IDs, volumes, networks and data (the I6 daemon restart restarts "
                                               "every container; run state excluded)" if restarted else
                                               "full identity and data"}
    post = inst.snapshot(f"{row['id']}-post") if inst.db_container() else None
    data = {"pre_heads": (pre_snapshot or {}).get("heads"), "post_heads": (post or {}).get("heads")}
    if pre_snapshot and post:
        pre_head = (pre_snapshot["heads"] or ["?"])[0][:4]
        post_head = (post["heads"] or ["?"])[0][:4]
        if row.get("data_relation") == "restored":
            data["relation"] = "restored-checkpoint (route documents the data change)"
            data["problems"] = []
        elif pre_head == post_head:
            data["problems"] = oracles.compare(pre_snapshot, post, h.fixture.get("volatile") or ["user_sessions"])
            data["relation"] = "≡"
        elif (pre_head, post_head) == ("0031", "0032"):
            data["problems"] = oracles.compare(pre_snapshot, post, h.fixture.get("volatile") or ["user_sessions"],
                                               migration="0031->0032")
            data["relation"] = "≡ [F: 0031→0032]"
        else:
            data["relation"] = f"heads {pre_head}->{post_head}"
            data["problems"] = []
        reference = {"0031": h.fixture.get("F_0031"), "0032": h.fixture.get("F_0032")}.get(post_head)
        data["schema_reference_ok"] = reference is None or post["schema"]["sha256"] == reference
    oracles_record["data"] = data
    oracles_record["schema"] = {"post_sha256": (post or {}).get("schema", {}).get("sha256"),
                                "reference_ok": data.get("schema_reference_ok")}
    run.record["evidence"] = [f"rows/{row['id']}.json", f"snapshots/{row['id']}-pre.json",
                              f"snapshots/{row['id']}-post.json", f"transcripts/{row['id']}-inject-1.txt"]
    oracles_record["alembic_upgrades_during_recovery"] = recovery.get("alembic_upgrades", 0)
    oracles_record["no_blind_rerun"] = (recovery.get("alembic_upgrades", 0) == 0) if row.get("no_rerun") else None
    extra = row.get("expect", lambda h, inst, ctx, rec: [])(h, inst, context, recovery)
    oracles_record["row_specific"] = extra
    run.record["oracles"] = oracles_record
    run.record["recovery_steps"] = recovery["steps"]
    problems = []
    if recovery.get("dead_end"):
        problems.append("dead end: " + recovery["dead_end"])
    final = str(recovery.get("final_phase"))
    if final not in TERMINAL and not (final.startswith("superseded by") and final.endswith("(completed)")):
        problems.append(f"not terminal: {final}")
    if recovery.get("status_works") is False:
        problems.append("pf status did not work after the injection")
    if data.get("problems"):
        problems.append("data: " + "; ".join(data["problems"][:4]))
    if data.get("schema_reference_ok") is False:
        problems.append("schema fingerprint differs from the reference for the head")
    if oracles_record["lock"].get("result") != "ok":
        problems.append("lock check failed")
    if not oracles_record["resources"]["others_unchanged"]:
        problems.append("other instances changed: " + "; ".join(oracles_record["resources"]["problems"]))
    if not oracles_record["deletion_scope_unchanged"]:
        problems.append("plan effects changed across resume")
    if oracles_record["no_blind_rerun"] is False:
        problems.append("blind re-run of the live migration")
    problems += [item["check"] for item in extra if item.get("ok") is False]
    if row.get("post"):
        row["post"](h, inst, context)
    return run.finish("passed" if not problems else "failed", "; ".join(problems) or None)


# ------------------------------------------------------------------------------------------- preconditions


def ensure_head(h, inst, head):
    """Bring the subject to ``head`` (0031 at OLD, or 0032 at NEW) through documented routes, recorded."""
    start_stopped_db(h, inst)
    current = (inst.heads() or ["?"])[0][:4]
    if current == head and h.open_operation(inst.slug) is None and inst.health() == 200:
        return
    if h.open_operation(inst.slug) is not None:
        operation = h.open_operation(inst.slug)["operation_id"]
        recover(h, inst, operation, row="pre")
    current = (inst.heads() or ["?"])[0][:4]
    db = inst.env()["POSTGRES_DB"]
    if head == "0031" and current != "0031":
        target = h.fixture["B4"]
        inst.pf(["rollback", target, "--restore-db"], step_id=f"pre-rollback-{util.utc()}",
                phrases=phrases.exact("RESTORE " + db + " " + target), timeout=3600)
    elif head == "0032" and current != "0032":
        sha = h.commit("NEW")
        inst.pf(["update", "--commit", sha, "--allow-migrations", "--skip-ci"], step_id=f"pre-update-{util.utc()}",
                phrases=phrases.exact("UPDATE " + sha[:12]), timeout=3600)
    elif inst.health() != 200:
        raise util.HarnessError(f"{inst.slug} is not healthy at {current}")


def start_stopped_db(h, inst):
    """The db container exists but is not running (an external stop/kill, not restarted by its restart policy). No
    pf route starts it (finding F-A34-03); the operator's manual route is a raw `docker start`, recorded."""
    found = observe.containers(inst.project, service="db", oneoff=False)
    if not found:
        return False
    item = util.docker_json(["inspect", found[0]])[0]
    if (item.get("State") or {}).get("Running"):
        return False
    h.external_event("operator-manual", f"docker start {found[0][:12]} (db; no pf route starts a stopped service)",
                     target="db", note="F-A34-03")
    util.docker_checked(["start", found[0]])
    util.wait_until(lambda: util.docker(["inspect", "--format", "{{.State.Health.Status}}", found[0]]).stdout.strip()
                    == "healthy", timeout=180, interval=2, what="db healthy after the manual start")
    return True


def prepare_update(h, inst):
    ensure_head(h, inst, "0031")
    return {}


def prepare_rollback(h, inst):
    ensure_head(h, inst, "0032")
    return {}


def prepare_any(h, inst):
    ensure_head(h, inst, (inst.heads() or ["0032"])[0][:4])
    return {}


def cmd_update(h, inst, context):
    sha = h.commit("NEW")
    return ["update", "--commit", sha, "--allow-migrations", "--skip-ci"], phrases.exact("UPDATE " + sha[:12]), ()


def cmd_backup(h, inst, context):
    return ["backup"], (), ()


def cmd_rollback(h, inst, context):
    target = h.fixture["B4"]
    db = inst.env()["POSTGRES_DB"]
    return ["rollback", target, "--restore-db"], phrases.exact("RESTORE " + db + " " + target), ()


def cmd_reset(h, inst, context):
    db = inst.env()["POSTGRES_DB"]
    return ["reset-db"], phrases.exact("RESET " + db), ()


def restore_data_after_reset(h, inst, context):
    """A reset row empties the application data; return to B4's data for the next rows (documented route)."""
    instances_module.wait_pf_idle(timeout=300)
    if h.open_operation(inst.slug) is None:
        ensure_head(h, inst, "0031") if (inst.heads() or ["?"])[0][:4] != "0031" else _rollback_b4(h, inst)


def _rollback_b4(h, inst):
    target = h.fixture["B4"]
    db = inst.env()["POSTGRES_DB"]
    inst.pf(["rollback", target, "--restore-db"], step_id=f"post-rollback-{util.utc()}",
            phrases=phrases.exact("RESTORE " + db + " " + target), timeout=3600)


def live_db_prefix(inst):
    name = inst.env().get("POSTGRES_DB") or inst.harness.fixture.get("alpha_db")
    if not name:
        raise util.HarnessError(f"{inst.slug}: the live database name is unknown (no .env and no recorded value)")
    return "database:" + name


# ---------------------------------------------------------------------------------------------- the rows


def rows(h, inst):
    """The implemented row table. Each entry carries its SPEC row id, kind/phase/effect type, the grid cells it
    proves, its injection and the expectations beyond the generic oracles."""
    live = live_db_prefix(inst)
    table = []

    def add(row_id, kind, phase, effect_type, injection, spec, *, grid=(), when="after", prepare=None, command=None,
            **extra):
        value = {"id": row_id, "kind": kind, "phase": phase, "effect_type": effect_type, "injection": injection,
                 "spec": spec, "grid": [list(cell) for cell in grid], "when": when,
                 "prepare": prepare or {"update": prepare_update, "rollback": prepare_rollback,
                                        "backup": prepare_any, "reset-db": prepare_any}.get(kind, prepare_any),
                 "command": command or {"update": cmd_update, "rollback": cmd_rollback, "backup": cmd_backup,
                                        "reset-db": cmd_reset}[kind]}
        value.update(extra)
        table.append(value)

    # ---- update
    add("R01", "update", "preserving", "capture", "I1 SIGKILL on pg_dump (pre-update capture)",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"],
         "gate": effect_trigger(h, inst.slug, effect_type="capture", states=("unknown",))}, when="before")
    add("R03", "update", "migrating", "database-migrate", "I2 on the live alembic upgrade (A3-T05 committed)",
        {"primitive": "I2", "child": ["alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)},
        grid=[("migrations", "SIGKILL"), ("journal commit", "SIGKILL")], no_rerun=True)
    add("R06", "update", "activating", "service-change", "I3 SIGTERM at service-change started",
        {"primitive": "I3", "signal": "SIGTERM",
         "gate": effect_trigger(h, inst.slug, effect_type="service-change", phase="activating")}, when="before")
    add("R07", "update", "activating", "service-change", "I6 daemon restart during activation",
        {"primitive": "I6", "gate": effect_trigger(h, inst.slug, effect_type="service-change", phase="activating")},
        when="before")
    # pf enters a phase only at an effect's intent (journal_update at the effect start); an update plan has no
    # finalizing effect, so the journal goes from its last effect phase to completed. The I3J watcher records every
    # journal phase it saw, so a never-written finalizing is evidence, not a missed race.
    add("R09", "update", "finalizing", None, "I3 SIGKILL at phase finalizing (inotify on the journal write)",
        {"primitive": "I3J", "signal": "SIGKILL", "phase": "finalizing"}, attempts=2)
    add("R40", "update", "migrating", "database-migrate", "I4 SIGKILL of the docker compose run alembic client",
        {"primitive": "I4", "child": ["compose", "alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)},
        grid=[("migrations", "error exit")], when="before")
    add("R42", "update", "migrating", "database-migrate", "I1 SIGTERM during the live alembic upgrade",
        {"primitive": "I1", "signal": "SIGTERM", "child": ["alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)},
        grid=[("migrations", "SIGTERM")], when="before")
    add("R43", "update", "migrating", "database-migrate", "I6 daemon restart during the live alembic upgrade",
        {"primitive": "I6", "child": ["alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)},
        grid=[("migrations", "restart")], when="before")
    add("R44", "update", "migrating", "database-migrate", "I4 SIGKILL of the rehearsal alembic client (pf_migrate_*)",
        {"primitive": "I4", "child": ["compose", "alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix="database:pf_migrate_")},
        grid=[("migrations", "error exit")], when="before")
    add("R57", "update", "migrating", "database-migrate", "I2T on the live alembic upgrade",
        {"primitive": "I2T", "child": ["alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)},
        grid=[("journal commit", "SIGTERM")], no_rerun=True)
    add("R58", "update", "migrating", "database-migrate", "I2 variant: child exits, I6 daemon restart, SIGKILL pf",
        {"primitive": "I2", "daemon_restart": True, "child": ["alembic", "upgrade"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-migrate", target_prefix=live)},
        grid=[("journal commit", "restart")], no_rerun=True)
    add("R59", "update", "preparing", "source-stage", "I3 SIGKILL at source-stage complete",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="source-stage", states=("complete",))})
    add("R08", "update", "syncing-workspace", "source-switch", "I3 SIGKILL at source-switch started",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="source-switch", states=("unknown",))},
        grid=[("workspace rename", "SIGKILL")], when="before")
    # ---- backup
    add("R11", "backup", "capturing", "capture", "I1 SIGKILL on pg_dump",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"],
         "gate": effect_trigger(h, inst.slug, effect_type="capture")}, when="before")
    add("R12", "backup", "verifying", "verification", "I1 SIGKILL on the restore-test pg_restore",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_restore", "pf_verify_"],
         "gate": effect_trigger(h, inst.slug, effect_type="verification")})
    # ---- rollback
    add("R14", "rollback", "preserving-current", "capture", "I1 SIGKILL on the capture",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"],
         "gate": effect_trigger(h, inst.slug, effect_type="capture")}, when="before")
    add("R15", "rollback", "restoring-candidate", "database-restore", "I1 SIGKILL on pg_restore into pf_restore_*",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_restore", "pf_restore_"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-restore")}, when="before",
        data_relation="restored")
    add("R16", "rollback", "switching", "database-switch", "I3 SIGKILL at database-switch started",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="database-switch", states=("unknown",))},
        grid=[("DB switch", "SIGKILL")], when="before", data_relation="restored")
    add("R17", "rollback", "switching", "database-switch", "I2 on the docker compose exec psql switch child",
        {"primitive": "I2", "child": ["psql", "RENAME TO"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-switch", states=("unknown",))},
        grid=[("DB switch", "SIGKILL"), ("journal commit", "SIGKILL")], data_relation="restored")
    add("R48", "rollback", "switching", "database-switch", "I3 SIGTERM at database-switch started",
        {"primitive": "I3", "signal": "SIGTERM",
         "gate": effect_trigger(h, inst.slug, effect_type="database-switch", states=("unknown",))},
        grid=[("DB switch", "SIGTERM")], when="before", data_relation="restored")
    add("R18", "rollback", "activating", "service-change", "I1 SIGKILL during service start",
        {"primitive": "I1", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="service-change", phase="activating")},
        data_relation="restored")
    add("R60", "rollback", "preparing", "source-stage", "I3 SIGKILL at source-stage started",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="source-stage", states=("unknown",))}, when="before",
        data_relation="restored")
    add("R19", "rollback", "syncing-workspace", "source-switch", "I3 SIGKILL at source-switch started",
        {"primitive": "I3", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="source-switch", states=("unknown",))},
        grid=[("workspace rename", "SIGKILL")], when="before", data_relation="restored")
    add("R49", "rollback", "switching", "database-switch", "I6 daemon restart at database-switch started",
        {"primitive": "I6", "gate": effect_trigger(h, inst.slug, effect_type="database-switch", states=("unknown",))},
        grid=[("DB switch", "restart")], when="before", data_relation="restored")
    # ---- reset-db
    add("R20", "reset-db", "preserving", "capture", "I1 SIGKILL on the capture",
        {"primitive": "I1", "signal": "SIGKILL", "child": ["pg_dump"],
         "gate": effect_trigger(h, inst.slug, effect_type="capture")}, when="before",
        data_relation="restored", post=restore_data_after_reset)
    add("R23", "reset-db", "switching", "database-switch", "I2 on the switch child",
        {"primitive": "I2", "child": ["psql", "RENAME TO"],
         "gate": effect_trigger(h, inst.slug, effect_type="database-switch", states=("unknown",))},
        grid=[("DB switch", "SIGKILL")], data_relation="restored", post=restore_data_after_reset)
    add("R61", "reset-db", "activating", "service-change", "I1 SIGKILL during the backend start",
        {"primitive": "I1", "signal": "SIGKILL",
         "gate": effect_trigger(h, inst.slug, effect_type="service-change", phase="activating")},
        data_relation="restored", post=restore_data_after_reset)
    return table


ORDER = ("R11", "R12", "R10", "R13",
         "R14", "R01", "R15", "R03", "R16", "R42", "R17", "R40", "R48", "R44", "R18", "R57", "R60", "R58", "R19",
         "R59", "R49", "R06", "R46", "R05", "R51", "R07", "R50", "R09", "R08",
         "R20", "R23", "R61", "R45", "R62", "R47",
         "R24", "R25", "R26", "R27", "R54", "R55", "R52", "R29",
         "R30", "R32", "R31", "R33", "R64",
         "R02", "R04", "R56", "R65", "R39", "R66", "R21", "R22", "R28", "R36", "R37", "R53",
         "R34", "R35", "R63", "R41", "R38")


def run(h, wanted=None):
    from . import matrix_custom
    from . import matrix_rows
    alpha = h.instance("alpha")
    from . import matrix_boundary
    table = {row["id"]: row for row in rows(h, alpha) + matrix_rows.rows(h, alpha) + matrix_custom.rows(h, alpha)
             + matrix_boundary.rows(h, alpha)}
    order = [row_id for row_id in ORDER + matrix_boundary.ORDER if row_id in table and (not wanted or row_id in wanted)]
    order += [row_id for row_id in sorted(table) if row_id not in order and (not wanted or row_id in wanted)]
    results = {}
    for row_id in order:
        h.log(f"L4 row {row_id}")
        try:
            results[row_id] = execute_row(h, table[row_id], alpha)
        except Exception as exc:  # a harness defect in one row is recorded; the run continues with the next row
            import traceback
            h.log(f"L4 row {row_id}: harness error {exc!r}\n{traceback.format_exc()}")
            results[row_id] = "harness-error"
            h.results.setdefault("rows", {})[row_id] = {"row": row_id, "result": "harness-error",
                                                        "reason": repr(exc)}
            h.save()
        h.log(f"L4 row {row_id}: {results[row_id]}")
    return "passed" if all(value == "passed" for value in results.values()) else "failed"
