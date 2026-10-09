"""Harness context: paths, the installed-launcher driver, step records, the daemon event stream."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from . import evidence as evidence_module
from . import terminal
from . import util

LAUNCHER = "/usr/local/bin/pf"
ENTERED_THROUGH = "installed launcher /usr/local/bin/pf (real dind daemon)"
PF_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM": "dumb", "LANG": "C.UTF-8",
          "HOME": "/root"}


class StepFailed(Exception):
    pass


class Harness:
    def __init__(self, *, root, evidence_dir, run, work="/pfa34/work"):
        self.root = Path(root)
        self.run_id = run
        self.work = Path(work)
        self.evidence = evidence_module.Evidence(evidence_dir, run)
        self.pfroot = self.root / "pfroot"
        self.homes = self.root / "homes"
        self.fixture_dir = self.root / "fixture"
        self.settings_path = self.fixture_dir / "release-source.json"
        self.fixture = self.evidence.read_json("fixture.json") or {}
        self.instances = self.evidence.read_json("instances.json") or {}
        self.step_counter = 0
        self.events = None
        self.external_events = []
        self.current_scenario = None
        self.results = self.evidence.read_json("results.json") or {"cases": {}, "rows": {}, "loops": {}}
        # Synthetic credentials the harness needs again (admin password, station device tokens): a private file
        # outside the evidence tree; every value is also registered for redaction.
        self.secrets_path = self.fixture_dir / "secrets.json"
        self.secrets = json.loads(self.secrets_path.read_text(encoding="utf-8")) if self.secrets_path.exists() else {}
        pending = list(self.secrets.values())
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, str):
                self.evidence.add_secret(value)

    # ------------------------------------------------------------------------------------------- persistence

    def save(self):
        if self.secrets:
            util.write_private(self.secrets_path, json.dumps(self.secrets, indent=1, sort_keys=True), mode=0o600)
        self.evidence.write_json("fixture.json", self.fixture)
        self.evidence.write_json("instances.json", self.instances)
        self.evidence.write_json("results.json", self.results)

    def log(self, text):
        line = f"{util.utc()} [{self.current_scenario or '-'}] {text}"
        print(line, flush=True)
        self.evidence.append_log("loops/" + (self.current_scenario or "harness") + ".log", line + "\n")

    _reconcile_argv = None

    def reconcile_argv(self):
        """``pf_config.RECONCILE_ARGV`` imported from the installed release (recorded once)."""
        if self._reconcile_argv is None:
            from . import observe
            module = observe.load_installed_pf_config(self.pfroot)
            self._reconcile_argv = tuple(module.RECONCILE_ARGV)
            self.fixture["reconcile_argv"] = list(self._reconcile_argv)
        return self._reconcile_argv

    def instance(self, slug):
        from . import instances
        return instances.Instance(self, slug)

    # ------------------------------------------------------------------------------------------ the launcher

    def commit(self, name):
        return self.fixture["commits"][name]

    def pf(self, argv, *, instance=None, phrases=(), dialogue=(), step_id=None, expect_exit=0, timeout=7200,
           on_tick=None, on_start=None, oracles=(), note=None, record=True, allow_fail=False, launcher=LAUNCHER,
           env_extra=None):
        """``pf [--instance <slug>] <argv>`` through the installed launcher at a scripted terminal. ``env_extra``: a
        hostile caller environment (C-A1-13 (a)); the launcher must ignore it."""
        full = [launcher] + (["--instance", instance] if instance else []) + list(argv)
        environment = dict(PF_ENV)
        environment.update(env_extra or {})
        self.step_counter += 1
        step_id = step_id or f"{self.current_scenario or 'x'}-{self.step_counter:04d}"
        before = self.open_operation(instance) if instance else None
        self.log(f"step {step_id}: pf {' '.join(full[1:])}")
        result = terminal.run(full, phrases=phrases, dialogue=dialogue, env=environment, cwd="/", timeout=timeout,
                              on_tick=on_tick, on_start=on_start)
        after = self.open_operation(instance) if instance else None
        operation = self.latest_operation(instance) if instance else None
        ok = (expect_exit is None or result.exit == expect_exit) and result.aborted is None
        record_value = {
            "step_id": step_id, "scenario": self.current_scenario, "instance": instance, "argv": ["pf"] + full[1:],
            "typed": result.typed, "prompts": [self.evidence.redact(item) for item in result.prompts],
            "exit": result.exit, "aborted": result.aborted, "phase_before": before, "phase_after": after,
            "operation_id": operation, "stdout_excerpt": self.evidence.excerpt(result.stdout),
            "stderr_excerpt": self.evidence.excerpt(result.stderr), "started_at": result.started_at,
            "ended_at": result.ended_at, "oracles": list(oracles), "external_events": [], "wait_states": [],
            "result": "passed" if ok else "failed",
            "reason": note or (None if ok else f"exit {result.exit} (expected {expect_exit}); aborted={result.aborted}"),
        }
        if record:
            self.evidence.append_jsonl("steps.jsonl", record_value)
        self.evidence.append_log("transcripts/" + step_id + ".txt",
                                 f"$ pf {' '.join(full[1:])}\n--- typed: {result.typed}\n--- stdout\n{result.stdout}"
                                 f"\n--- stderr\n{result.stderr}\n--- exit {result.exit} aborted={result.aborted}\n")
        result.step_id = step_id
        result.operation_id = operation
        if not ok and not allow_fail:
            raise StepFailed(f"{step_id}: pf {' '.join(full[1:4])}… exit {result.exit} (expected {expect_exit}); "
                             f"aborted={result.aborted}; stderr: {self.evidence.redact(result.stderr.strip()[-600:])}")
        return result

    def record_step(self, step_id, *, result, reason=None, oracles=(), external_events=(), extra=None):
        value = {"step_id": step_id, "scenario": self.current_scenario, "result": result, "reason": reason,
                 "oracles": list(oracles), "external_events": list(external_events), "at": util.utc()}
        if extra:
            value.update(extra)
        self.evidence.append_jsonl("steps.jsonl", value)

    def external_event(self, kind, command, *, target=None, note=None):
        """A declared harness act on a pf-managed resource (SPEC 5.1): fault or carry-over, never a state edit."""
        event_id = f"x{len(self.external_events) + 1:04d}"
        value = {"id": event_id, "kind": kind, "command": command, "target": target, "note": note, "at": util.utc(),
                 "scenario": self.current_scenario}
        self.external_events.append(value)
        self.evidence.append_jsonl("external-events.jsonl", value)
        return event_id

    # --------------------------------------------------------------------------------------- instance state

    def registry(self):
        path = self.pfroot / "registry" / "instances.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def instance_record(self, slug):
        info = self.instances.get(slug)
        if not info:
            return None
        path = Path(info["record_path"])
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def operations_dir(self, slug):
        info = self.instances.get(slug) or {}
        state = info.get("private_state")
        return Path(state) / "operations" if state else None

    def list_operations(self, slug):
        directory = self.operations_dir(slug)
        if directory is None or not directory.is_dir():
            return []
        return sorted(name for name in os.listdir(str(directory)))

    def latest_operation(self, slug):
        names = self.list_operations(slug)
        return names[-1] if names else None

    def operation_files(self, slug, operation_id):
        directory = self.operations_dir(slug) / operation_id
        files = {}
        for name in ("operation.json", "plan.json", "journal.json"):
            path = directory / name
            if path.exists():
                try:
                    files[name] = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    files[name] = {"unreadable": True}
        return files

    def open_operation(self, slug):
        """(operation id, phase) of the newest operation whose journal is not terminal and that no later completed
        operation supersedes (``plan.supersedes``), else None."""
        superseded = set()
        for name in reversed(self.list_operations(slug)):
            files = self.operation_files(slug, name)
            journal, plan = files.get("journal.json"), files.get("plan.json") or {}
            if journal and journal.get("phase") in ("completed", "cancelled", "failed_preserved") and \
                    plan.get("supersedes"):
                superseded.add(plan["supersedes"])
            if name in superseded:
                continue
            if journal and journal.get("phase") not in ("completed", "cancelled", "failed_preserved", "superseded"):
                return {"operation_id": name, "phase": journal.get("phase")}
        return None

    # ---------------------------------------------------------------------------------------- daemon events

    def start_events(self):
        if self.events is not None and self.events.poll() is None:
            return
        path = self.evidence.path("daemon-events.jsonl")
        handle = open(str(path), "ab")
        self.events = subprocess.Popen([util.DOCKER, "events", "--format", "{{json .}}"], stdout=handle,
                                       stderr=subprocess.DEVNULL, env=dict(util.BASE_ENV))
        handle.close()
        self.evidence.append_jsonl("daemon-events-attach.jsonl", {"attached_at": util.utc(), "pid": self.events.pid})

    def stop_events(self):
        if self.events is not None and self.events.poll() is None:
            self.events.send_signal(signal.SIGTERM)
            try:
                self.events.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.events.kill()
        self.events = None

    def events_between(self, start, end=None):
        """Daemon events with ``timeNano`` within [start, end] (epoch seconds)."""
        path = self.evidence.path("daemon-events.jsonl")
        if not path.exists():
            return []
        found = []
        low = int(start * 1e9)
        high = int((end if end is not None else time.time() + 5) * 1e9)
        with open(str(path), "rb") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                stamp = item.get("timeNano") or 0
                if low <= stamp <= high:
                    found.append(item)
        return found


def shortsha(sha):
    return sha[:12]


def phrase_re(text):
    return "^" + re.escape(text) + "$"
