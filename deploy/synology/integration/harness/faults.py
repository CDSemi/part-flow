"""Fault-injection primitives (SPEC 5.3) and the /proc argv log. Everything acts inside the dind container only."""
import json
import os
from pathlib import Path
import signal
import time

from . import util

PF_ADMIN_MARK = b"pf-admin.py"


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return [part.decode("utf-8", "replace") for part in handle.read().split(b"\0") if part]
    except OSError:
        return None


def parent(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            data = handle.read()
        return int(data[data.rindex(b")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def descendants(root):
    """PIDs whose parent chain reaches ``root`` (pf_runner children start new sessions but keep the parent)."""
    parents = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            value = parent(int(name))
            if value is not None:
                parents[int(name)] = value
    found = set()
    for pid in parents:
        chain, current = [], pid
        for _ in range(64):
            if current == root:
                found.add(pid)
                break
            current = parents.get(current)
            if current is None or current in chain:
                break
            chain.append(current)
    return sorted(found)


def pf_admin_pid(root):
    """The pf-admin Python process below the launcher ``root`` (the launcher execs the verifier, which execs it)."""
    for pid in [root] + descendants(root):
        argv = cmdline(pid) or []
        if any(PF_ADMIN_MARK.decode() in part for part in argv):
            return pid
    return None


class ArgvLog:
    """The argv of every process seen below a command (polled from /proc on each terminal tick)."""

    def __init__(self, interesting=None):
        self.seen = {}
        self.interesting = interesting

    def tick(self, process):
        for pid in descendants(process.pid):
            if pid in self.seen:
                continue
            argv = cmdline(pid)
            if argv:
                self.seen[pid] = {"pid": pid, "argv": argv, "at": util.utc()}

    def entries(self):
        return [self.seen[key] for key in sorted(self.seen)]

    def matching(self, *words):
        return [item for item in self.entries() if all(any(word in part for part in item["argv"]) for word in words)]


class Trigger:
    """An I1/I2/I3/I4/I8 trigger evaluated on every terminal tick of the step's command.

    ``match(process)`` returns a target description or None; ``fire(process, target)`` acts once."""

    def __init__(self, match, fire, *, argv_log=None):
        self.match = match
        self.fire = fire
        self.fired = None
        self.argv_log = argv_log
        self.attempt_states = []

    def tick(self, process):
        if self.argv_log is not None:
            self.argv_log.tick(process)
        if self.fired is not None or process.poll() is not None:
            return
        target = self.match(process)
        if target is not None:
            self.fired = {"at": util.utc(), "target": target}
            self.fired["result"] = self.fire(process, target)


def child_matching(process, pattern_words):
    """The first descendant of the launcher whose argv contains every word in ``pattern_words``."""
    for pid in descendants(process.pid):
        argv = cmdline(pid) or []
        text = " ".join(argv)
        if all(word in text for word in pattern_words) and PF_ADMIN_MARK.decode() not in text:
            return {"pid": pid, "argv": argv}
    return None


def kill_pf(process, signum):
    pid = pf_admin_pid(process.pid)
    if pid is None:
        return {"error": "no pf-admin process"}
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        return {"error": "pf-admin already gone"}
    return {"pf_admin_pid": pid, "signal": signal.Signals(signum).name}


def stop_pf(process):
    pid = pf_admin_pid(process.pid)
    if pid is not None:
        os.kill(pid, signal.SIGSTOP)
    return pid


def wait_exit(pid, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os.path.exists(f"/proc/{pid}") or _zombie(pid):
            return True
        time.sleep(0.05)
    return False


def _zombie(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            data = handle.read()
        return data[data.rindex(b")") + 2:].split()[0] == b"Z"
    except (OSError, ValueError):
        return True


BASELINE = {}  # operations directory -> operation names that existed before the injected command started


def command_anchor(holder, operations_dir):
    """The start time of the injected command a gate is evaluated for: re-anchored whenever set_baseline armed a new
    command for ``operations_dir``, so daemon events of earlier rows and preconditions never satisfy a gate."""
    key = id(BASELINE.get(str(Path(operations_dir))))
    if holder.get("baseline") != key:
        holder["baseline"], holder["started"] = key, time.time()
    return holder["started"]


def set_baseline(operations_dir):
    """A trigger only ever matches an operation created by the injected command (never a precondition's)."""
    directory = Path(operations_dir)
    BASELINE[str(directory)] = set(os.listdir(str(directory))) if directory.is_dir() else set()


def journal_state(operations_dir, operation_hint=None):
    """(operation id, journal) of the newest operation directory that has a journal (atomic generations), ignoring
    the operations recorded in BASELINE for this directory."""
    directory = Path(operations_dir)
    names = sorted(os.listdir(str(directory))) if directory.is_dir() else []
    excluded = BASELINE.get(str(directory), set()) if operation_hint is None else set()
    for name in reversed(names):
        if operation_hint and name != operation_hint:
            continue
        if name in excluded:
            continue
        path = directory / name / "journal.json"
        try:
            return name, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None, None


def effect_state(journal, effect_id):
    for item in (journal or {}).get("effects") or []:
        if item.get("effect_id") == effect_id:
            return item.get("state")
    states = (journal or {}).get("effect_states") or {}
    return states.get(effect_id) if isinstance(states, dict) else None


def daemon_pid():
    try:
        return int(Path("/run/pfa34/dockerd.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def restart_daemon(harness, *, wait=True):
    """I6: TERM dockerd; the supervisor starts it again; the event stream is re-attached."""
    pid = daemon_pid()
    harness.external_event("I6", f"kill -TERM {pid} (dockerd)", target="dockerd")
    if pid:
        os.kill(pid, signal.SIGTERM)
    if not wait:
        return pid
    wait_exit(pid, 120)
    util.wait_until(lambda: daemon_pid() not in (None, pid) and
                    util.docker(["info", "--format", "{{.ID}}"], timeout=10).returncode == 0,
                    timeout=180, interval=1, what="dockerd restarted by the supervisor")
    harness.stop_events()
    harness.start_events()
    return pid
