"""The no-effect oracle N (SPEC 3.5) around a step that expects a refusal."""
import time

from . import observe
from . import oracles


def tree_roots(h):
    roots = [str(h.pfroot)]
    for slug in sorted(h.instances):
        inst = h.instance(slug)
        roots += [str(inst.config_dir), str(inst.backups), str(inst.recovery)]
    return roots


def capture(h):
    value = {"time": time.time(), "identities": {}, "snapshots": {}, "tree": oracles.tree_hashes(tree_roots(h))}
    for slug in sorted(h.instances):
        inst = h.instance(slug)
        value["identities"][slug] = observe.stable_identity(inst.identities())
        if inst.db_container():
            snap = inst.snapshot(f"noeffect-{slug}", record=False)
            value["snapshots"][slug] = {"tables": snap["tables"], "schema": snap["schema"]["sha256"],
                                        "heads": snap["heads"]}
    value["identities"]["othersite"] = observe.stable_identity(observe.project_identities("othersite"))
    return value


def operations_prefixes(h):
    prefixes = []
    for slug in sorted(h.instances):
        directory = h.operations_dir(slug)
        if directory is not None:
            prefixes.append(str(directory).lstrip("/"))
    return prefixes


def evaluate(h, step_id, before, after, window):
    events = oracles.mutating_events(h.events_between(*window))
    identities_equal = before["identities"] == after["identities"]
    snapshots_equal = True
    for slug in set(before["snapshots"]) | set(after["snapshots"]):
        a, b = before["snapshots"].get(slug), after["snapshots"].get(slug)
        if a is None or b is None:
            snapshots_equal = False
            continue
        tables = [table for table in set(a["tables"]) | set(b["tables"])
                  if table != "user_sessions" and a["tables"].get(table) != b["tables"].get(table)]
        if tables or a["schema"] != b["schema"] or a["heads"] != b["heads"]:
            snapshots_equal = False
    bookkeeping, violations = oracles.classify_tree_change(before["tree"], after["tree"],
                                                           operations_prefixes=operations_prefixes(h))
    result = "passed" if not events and identities_equal and snapshots_equal and not violations else "FAIL no-effect"
    value = {"step_id": step_id, "window": list(window), "daemon_events": events, "identities_equal": identities_equal,
             "snapshots_equal": snapshots_equal, "tree_diff": violations, "bookkeeping": bookkeeping,
             "result": result}
    h.evidence.write_json(f"noeffect/{step_id}.json", value)
    return value


def refused(h, inst, argv, *, step_id, expect_exit=None, phrases=(), dialogue=(), expect_text=None, **kwargs):
    """Run a step that must be refused; returns (result, N record). ``expect_text`` must appear in the output."""
    before = capture(h)
    started = time.time()
    result = inst.pf(argv, step_id=step_id, phrases=phrases, dialogue=dialogue, expect_exit=expect_exit,
                     allow_fail=True, **kwargs)
    window = (started, time.time())
    after = capture(h)
    record = evaluate(h, step_id, before, after, window)
    text = result.stdout + result.stderr
    record["refused"] = result.exit not in (0, None) and result.aborted is None
    record["expected_text_seen"] = expect_text is None or expect_text in text
    record["exit"] = result.exit
    h.evidence.write_json(f"noeffect/{step_id}.json", record)
    return result, record
