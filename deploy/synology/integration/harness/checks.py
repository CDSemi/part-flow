"""Shared step/oracle helpers for the loops, cases and rows: operations, checkpoints, probes, L5 guard, assertions."""
import json
import os
from pathlib import Path
import shutil
import time

from . import instances as instances_module
from . import observe
from . import oracles
from . import util

MARKER_SQL = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
              "SELECT 'W', work_order_number FROM work_orders WHERE work_order_number LIKE 'PFA34-W%' ORDER BY 2;\n"
              "SELECT 'C', work_order_number FROM work_orders WHERE work_order_number LIKE 'PFA34-C%' ORDER BY 2;\n"
              "SELECT 'E', device_event_id FROM part_movements WHERE device_event_id IS NOT NULL ORDER BY 2;\n"
              "SELECT 'B', datname FROM pg_database ORDER BY 2;\n"
              "COMMIT;\n")


class Verdict:
    """Collects named assertions of one step/loop; ``ok`` only when every assertion held."""

    def __init__(self, name):
        self.name = name
        self.items = []

    def check(self, label, condition, detail=None):
        self.items.append({"check": label, "ok": bool(condition), "detail": detail})
        return bool(condition)

    def blocked(self, label, reason):
        self.items.append({"check": label, "ok": None, "detail": reason, "blocked": True})

    @property
    def ok(self):
        return all(item["ok"] is not False for item in self.items) and any(item["ok"] for item in self.items)

    @property
    def failures(self):
        return [item for item in self.items if item["ok"] is False]

    def as_record(self):
        return {"name": self.name, "ok": self.ok, "checks": self.items}


# ------------------------------------------------------------------------------------------------ operations


def operations(inst):
    """[(operation id, plan, journal)] of an instance, oldest first (plan-bearing ones only)."""
    result = []
    for name in inst.harness.list_operations(inst.slug):
        files = inst.harness.operation_files(inst.slug, name)
        if "plan.json" in files:
            result.append((name, files["plan.json"], files.get("journal.json") or {}))
    return result


def newest(inst, kind=None, *, after=None):
    for name, plan, journal in reversed(operations(inst)):
        if (kind is None or plan.get("kind") == kind) and (after is None or name > after):
            return name, plan, journal
    return None, None, None


def journal_of(inst, operation_id):
    return inst.harness.operation_files(inst.slug, operation_id).get("journal.json") or {}


def retained(journal, kind="checkpoint"):
    return [item.get("name") for item in journal.get("retained_artifacts") or [] if item.get("kind") == kind]


def checkpoint_dir(inst, checkpoint_id):
    return inst.backups / "revisions" / inst.project / checkpoint_id


def find_bundle_dir(inst, bundle_id):
    for base in (inst.backups, inst.recovery):
        for directory, dirnames, _ in os.walk(str(base)):
            if os.path.basename(directory) == bundle_id:
                return Path(directory)
            if directory.count("/") - str(base).count("/") > 3:
                dirnames[:] = []
    return None


def manifest(directory):
    path = Path(directory) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def effect_types(plan):
    return [item.get("type") for item in plan.get("effects") or []]


def image_ids():
    return set(util.docker_checked(["images", "-q", "--no-trunc"]).split())


def running_images(inst):
    found = {}
    for service in ("db", "backend", "frontend"):
        container = observe.running_container(inst.project, service)
        if container:
            found[service] = util.docker_checked(["inspect", "--format", "{{.Image}}", container]).strip()
    return found


def live_markers(inst):
    env = inst.env()
    container = inst.db_container()
    result = observe.psql(container, env["POSTGRES_USER"], env["POSTGRES_DB"], MARKER_SQL)
    grouped = {}
    for line in result.stdout.splitlines():
        fields = line.split(oracles.SEPARATOR)
        if len(fields) == 2:
            grouped.setdefault(fields[0], []).append(fields[1])
    return {"markers": grouped.get("W", []), "databases": grouped.get("B", []), "concurrent": grouped.get("C", []),
            "events": grouped.get("E", []), "exit": result.returncode}


# ---------------------------------------------------------------------------------------------------- probes


def probe(h, inst, dump_path, label, *, reconcile=False):
    """The preserved-data probe (SPEC 3.5): S, F, heads and the W markers of a pf-produced dump."""
    def reconcile_image(heads):
        """The instance's backend image of the commit whose Alembic head the restored capture is at (reconcile runs
        the code that matches the schema; 0031 -> OLD, 0032 -> NEW)."""
        head = (heads or ["?"])[0][:4]
        commit = {"0031": h.commit("OLD"), "0032": h.commit("NEW")}.get(head)
        if commit:
            for line in util.docker_checked(["images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}", "--filter",
                                             f"reference={inst.project}-backend:*"]).splitlines():
                reference, image = line.split()
                if commit[:12] in reference:
                    return image
        return running_images(inst).get("backend")
    value = observe.probe_dump(dump_path, label=label, instance=inst.slug,
                               reconcile_image=reconcile_image if reconcile else None,
                               reconcile_argv=h.reconcile_argv() if reconcile else None, extra_sql=MARKER_SQL)
    h.evidence.write_json(f"preserved/{label}.json", value)
    return value


def probe_checkpoint(h, inst, checkpoint_id, label, **kwargs):
    directory = checkpoint_dir(inst, checkpoint_id)
    if not (directory / "database.dump").exists():
        found = find_bundle_dir(inst, checkpoint_id)
        directory = found if found is not None else directory
    dump = directory / "database.dump"
    if not dump.exists():
        candidates = sorted(directory.glob("**/*.dump")) if directory.exists() else []
        active = [item for item in candidates if item.name in ("active.dump", "database.dump")]
        dump = (active or candidates or [dump])[0]
    return probe(h, inst, dump, label, **kwargs)


def equivalent(h, verdict, label, first, second, *, migration=None):
    problems = oracles.compare(first, second, h.fixture.get("volatile") or ["user_sessions"], migration=migration)
    verdict.check(label, not problems, problems[:20] if problems else None)
    return not problems


# ------------------------------------------------------------------------------------------------- L5 guard


def l5_state(h, *, exclude=()):
    state = {"identities": {}, "data": {}}
    for slug in ("bravo", "charlie"):
        if slug in exclude or slug not in h.instances:
            continue
        inst = h.instance(slug)
        if inst.info.get("purged"):
            continue
        state["identities"][slug] = observe.stable_identity(inst.identities())
        if inst.db_container():
            snap = inst.snapshot(f"l5-{slug}", record=False)
            state["data"][slug] = {"tables": snap["tables"], "schema": snap["schema"]["sha256"]}
    from . import setup_scenario
    state["identities"]["othersite"] = observe.stable_identity(observe.project_identities("othersite"))
    state["data"]["othersite"] = setup_scenario.othersite_file_hash()
    return state


def _without_run_state(identity):
    """A daemon restart (fault I6) restarts every container of the daemon: compare container IDs, names and images,
    volumes and networks, never StartedAt/RestartCount/status."""
    if not identity:
        return identity
    return {"containers": sorted(tuple(item[:3]) for item in identity["containers"]), "volumes": identity["volumes"],
            "networks": identity["networks"]}


def l5_compare(before, after, *, daemon_restarted=False):
    problems = []
    for key in sorted(set(before["identities"]) | set(after["identities"])):
        first, second = before["identities"].get(key), after["identities"].get(key)
        if daemon_restarted:
            first, second = _without_run_state(first), _without_run_state(second)
        if first != second:
            problems.append(f"identities of {key} changed")
    for key in sorted(set(before["data"]) | set(after["data"])):
        a, b = before["data"].get(key), after["data"].get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            changed = [table for table in set(a["tables"]) | set(b["tables"])
                       if table != "user_sessions" and a["tables"].get(table) != b["tables"].get(table)]
            if changed or a["schema"] != b["schema"]:
                problems.append(f"data of {key} changed: {sorted(changed)[:10]}")
        elif a != b:
            problems.append(f"data of {key} changed")
    return problems


def run_alpha_step(h, inst, argv, *, step_id, phrases=(), dialogue=(), expect_exit=0, l5=True, l5_exclude=(),
                   allow_fail=False, **kwargs):
    """One pf step on a subject instance with the woven L5 checks and the SPEC 5.2 lock check after it."""
    before = l5_state(h, exclude=l5_exclude) if l5 else None
    images_before = image_ids()
    started = time.time()
    result = inst.pf(argv, step_id=step_id, phrases=phrases, dialogue=dialogue, expect_exit=expect_exit,
                     allow_fail=True, **kwargs)
    ended = time.time()
    result.window = (started, ended)
    result.new_images = sorted(image_ids() - images_before)
    instances_module.wait_pf_idle(timeout=120)
    result.lock = inst.lock_check()
    if l5:
        after = l5_state(h, exclude=l5_exclude)
        problems = l5_compare(before, after)
        h.results.setdefault("l5", []).append({"step": step_id, "problems": problems, "ok": not problems})
        result.l5_problems = problems
    else:
        result.l5_problems = []
    ok_exit = (expect_exit is None or result.exit == expect_exit) and result.aborted is None
    if not ok_exit and not allow_fail:
        from .core import StepFailed
        raise StepFailed(f"{step_id}: exit {result.exit} (expected {expect_exit}); aborted={result.aborted}; "
                         f"stderr: {h.evidence.redact(result.stderr.strip()[-800:])}")
    return result


def workspace_git(inst):
    return (inst.workspace / ".git").exists()


def remove_workspace_git(h, inst):
    path = inst.workspace / ".git"
    existed = path.exists()
    if existed:
        shutil.rmtree(str(path)) if path.is_dir() else path.unlink()
        h.external_event("workspace-edit", f"rm -rf {path}", target=str(path),
                         note="operator removes the workspace .git (LOOP-01/02)")
    return existed


def status_text(h, inst, step_id):
    result = inst.pf(["status"], step_id=step_id, expect_exit=None)
    return result.stdout + result.stderr
