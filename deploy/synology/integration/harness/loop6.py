"""LOOP-06 on bravo (SPEC 8.2 L6): the frozen configuration, an open workspace handle across the generation switch,
late writes, retained generations. Recorded as A3-T10's generic-Linux part."""

from . import checks
from . import faults
from . import inotify
from . import observe
from . import phrases
from . import util
from .checks import Verdict
from .loops import _record, heads_prefix

NEW_ZONE = "Europe/Berlin"
MIGRATION_0032 = "backend/alembic/versions/20261007_0032_phase14_route_adjusted.py"


def generations_dir(inst):
    found = sorted(inst.home.glob(".pf-generations-*"))
    return found[0] if found else None


def run(h):
    bravo = h.instance("bravo")
    alpha = h.instance("alpha")
    verdict = Verdict("LOOP-06")
    alpha_before = observe.stable_identity(alpha.identities("L6-alpha-pre"))
    frozen_zone = bravo.env().get("SITE_TIMEZONE")
    notes = bravo.workspace / "notes"
    notes.mkdir(exist_ok=True)
    edit = notes / "user-edit.txt"
    edit.write_text("before the update\n", encoding="utf-8")
    h.external_event("workspace-edit", f"create {edit}", target=str(edit), note="operator file, kept open")
    handle = open(str(edit), "a", encoding="utf-8")
    watch = inotify.Watch(bravo.home)
    state = {"edited": False, "renames": [], "late": 0}
    env_path = bravo.config_dir / ".env"

    def tick(process):
        if not state["edited"]:
            _, journal = faults.journal_state(h.operations_dir("bravo"))
            if journal and journal.get("phase") not in (None, "planned"):
                # I3 observation at the freeze: edit the .env proposal and add an untracked file
                text = env_path.read_text(encoding="utf-8")
                env_path.write_text(text.replace("SITE_TIMEZONE=" + frozen_zone, "SITE_TIMEZONE=" + NEW_ZONE),
                                    encoding="utf-8")
                (bravo.workspace / "notes" / "untracked-after-freeze.txt").write_text("late file\n", encoding="utf-8")
                h.external_event("config-edit", f"SITE_TIMEZONE={NEW_ZONE} in {env_path}; add notes/untracked",
                                 target=str(env_path), note=f"at journal phase {journal.get('phase')}")
                state["edited"] = journal.get("phase")
        for mask, name in watch.read():
            state["renames"].append({"mask": mask, "name": name, "at": util.utc()})
            state["late"] += 1
            handle.write(f"late write {state['late']} after rename event {name}\n")
            handle.flush()

    sha = h.commit("NEW")
    faults.set_baseline(h.operations_dir("bravo"))  # the freeze trigger only sees this update's journal
    try:
        checks.run_alpha_step(h, bravo, ["update", "--commit", sha, "--allow-migrations", "--skip-ci"],
                              step_id="L6-update", phrases=phrases.exact("UPDATE " + sha[:12]),
                              on_tick=tick, l5_exclude=("bravo",))
    finally:
        handle.write("final late write\n")
        handle.close()
        watch.close()
    h.evidence.write_json("records/L6-renames.json", state)
    op, plan, journal = checks.newest(bravo, "update")
    verdict.check("L6: update completed", journal.get("phase") == "completed", journal.get("phase"))
    verdict.check("L6: the .env proposal was edited after the freeze", bool(state["edited"]), state["edited"])
    backend = bravo.backend_container()
    env = {}
    if backend:
        for entry in util.docker_json(["inspect", backend])[0]["Config"].get("Env") or []:
            key, _, value = entry.partition("=")
            env[key] = value
    verdict.check("L6: containers run with the frozen SITE_TIMEZONE", env.get("SITE_TIMEZONE") == frozen_zone,
                  env.get("SITE_TIMEZONE"))
    verdict.check("L6: the .env edit is still the proposal", bravo.env().get("SITE_TIMEZONE") == NEW_ZONE)
    verdict.check("L6: the new source (0032 migration) is in the active workspace",
                  (bravo.workspace / MIGRATION_0032).exists())
    verdict.check("L6: heads 0032", heads_prefix(bravo) == "0032", bravo.heads())
    generations = generations_dir(bravo)
    texts = []
    for path in [bravo.workspace / "notes" / "user-edit.txt"] + (
            sorted(generations.rglob("notes/user-edit.txt")) if generations else []):
        try:
            texts.append((str(path), path.read_text(encoding="utf-8")))
        except OSError:
            continue
    late_found = [path for path, text in texts if "final late write" in text]
    verdict.check("L6: the late bytes exist in the active or a retained generation", bool(late_found),
                  {"files": [path for path, _ in texts]})
    verdict.check("L6: rename events observed (I8, no kill)", bool(state["renames"]), state["renames"][:6])
    retained = sorted(item.name for item in generations.iterdir()) if generations else []
    status = bravo.pf(["status"], step_id="L6-status", expect_exit=None)
    verdict.check("L6: a retained generation exists and is never deleted", bool(retained), retained)
    verdict.check("L6: status lists the retained generations", "etained" in status.stdout and
                  any(name in status.stdout for name in retained) or "Retained workspace generations" in status.stdout,
                  status.stdout[-1200:])
    alpha_after = observe.stable_identity(alpha.identities("L6-alpha-post"))
    verdict.check("L6: alpha unchanged", alpha_before == alpha_after)
    ok = _record(h, "L6", verdict, extra={"renames": state["renames"][:20]})
    h.results["cases"]["A3-T10-generic-linux-L6"] = {"status": "passed" if ok else "failed",
                                                     "failures": verdict.failures}
    return "passed" if ok else "failed"
