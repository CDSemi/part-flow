"""The run's CRASH_MATRIX.json and ACCEPTANCE_RESULTS.json (SPEC 6.3), generated from results.json and the real
plan effect lists."""
from . import coverage
from . import observe

ALL_ROWS = tuple(f"R{number:02d}" for number in range(1, 67))
# Controller.effect_action (pf-admin.py) dispatches every plan effect by role to one executor shared by all kinds.
ROLE_EXECUTORS = {
    "workspace": "the workspace generation switch (W1..W4)", "topology": "Controller.topology_action",
    "registry": "Controller.act_registry_state", "staging": "Controller.act_stage / act_record_current",
    "stop": "Controller.pause", "frontend": "Controller.act_frontend", "backend": "Controller.activate_backend",
    "db-start": "Controller.act_db_start", "capture": "Controller.act_capture",
    "verification": "Controller.act_verification", "seal": "Controller.act_seal",
    "deletion": "Controller.act_delete (execute_deletion_plan)", "switch": "Controller.swap_database",
    "image-load": "Controller.act_image_load", "candidate": "Controller.act_database",
    "migration": "Controller.act_database", "data": "Controller.act_database", "pointer": "Controller.act_pointer",
    "file": "Controller.act_file",
}


def executor(pf_config, kind, effect):
    if not effect.get("target"):
        return None
    try:
        role = pf_config.effect_role(effect)
    except (KeyError, IndexError, AttributeError):
        return None
    if role == "topology":
        return ROLE_EXECUTORS["topology"]
    if kind == "cleanup":
        return "Controller.cleanup_action"
    if effect.get("type") == "image-load" and kind == "restore-side-by-side":
        return "Controller.act_side_by_side_image_load"
    return ROLE_EXECUTORS.get(role, "Controller.effect_action")


def observed_effects(h, pf_config):
    """{(kind, phase, effect type): {executor}} over every real plan of the run, and the kinds executed."""
    found, kinds = {}, set()
    for slug in sorted(h.instances):
        for name in h.list_operations(slug):
            plan = h.operation_files(slug, name).get("plan.json")
            if not plan:
                continue
            kinds.add(plan.get("kind"))
            for effect in plan.get("effects") or []:
                key = (plan.get("kind"), effect.get("phase"), effect.get("type"))
                found.setdefault(key, set()).add(executor(pf_config, plan.get("kind"), effect))
    return found, sorted(kind for kind in kinds if kind)


def declared_reasons(pf_config, phase_order, observed, kinds, rows):
    """SPEC 8.3.2 reasons: no-effect-phase (a phase of an executed kind that no real plan gave an effect),
    not-reached (workspace_sync_pending), and equivalent-to <rows> when the entry's executor is the executor a row of
    another kind or phase interrupted (Controller.effect_action dispatches by role, shared by every kind)."""
    declared = {}
    effect_phases = {(kind, phase) for kind, phase, _ in observed}
    for kind, phases in phase_order.items():
        for phase in phases:
            if phase == "planned":
                continue
            if phase == "workspace_sync_pending":
                declared[(kind, phase, None)] = ("not-reached", "reached only on a refused switch; exercised via R50")
            elif kind in kinds and (kind, phase) not in effect_phases:
                declared[(kind, phase, None)] = ("no-effect-phase", "no effect in any real plan of this kind")
    by_executor = {}
    for row in rows:
        if row.get("result") not in ("passed", "blocked") or not row.get("effect_type"):
            continue
        name = executor(pf_config, row.get("kind"),
                        {"type": row.get("effect_type"), "target": row.get("effect_target") or "",
                         "phase": row.get("phase")})
        if name:
            by_executor.setdefault((row["effect_type"], name), []).append(row)
            if name == ROLE_EXECUTORS["workspace"]:
                # W1..W4 (stage, switch, manifest) are the steps of the one workspace switch routine
                by_executor.setdefault(("*", name), []).append(row)
    for key, executors in sorted(observed.items(), key=lambda item: tuple(str(part) for part in item[0])):
        kind, phase, etype = key
        direct = [row for row in rows if row.get("kind") == kind and row.get("phase") == phase
                  and row.get("effect_type") == etype and row.get("result") in ("passed", "blocked")]
        before = [row["row"] for row in direct if row.get("when") in ("before", None)]
        after = [row["row"] for row in direct if row.get("when") in ("after", None)]
        needs_both = etype in coverage.IRREVERSIBLE_TYPES
        if (needs_both and before and after) or (not needs_both and direct):
            continue
        for name in sorted(item for item in executors if item):
            others = by_executor.get((etype, name), []) or by_executor.get(("*", name), [])
            other_before = sorted({row["row"] for row in others if row.get("when") in ("before", None)} | set(before))
            other_after = sorted({row["row"] for row in others if row.get("when") in ("after", None)} | set(after))
            if (needs_both and other_before and other_after) or (not needs_both and (other_before or other_after)):
                declared[key] = ("equivalent-to", f"before: {', '.join(other_before) or '-'}; after: "
                                                  f"{', '.join(other_after) or '-'} (the same executor {name}, "
                                                  "dispatched by Controller.effect_action for every kind)")
                break
    return declared


def crash_matrix(h):
    pf_config = observe.load_installed_pf_config(h.pfroot)
    rows = [value for key, value in sorted((h.results.get("rows") or {}).items())
            if key.startswith(("R", "B")) or key.startswith("C-")]
    observed, kinds = observed_effects(h, pf_config)
    usable = [row for row in rows if row.get("result") in ("passed", "blocked")]
    grid = coverage.grid(usable)
    declared = declared_reasons(pf_config, pf_config.PHASE_ORDER, observed, kinds, rows)
    table = coverage.phase_table(pf_config.PHASE_ORDER, list(observed), usable, declared=declared)
    present = {row["row"] for row in rows}
    missing = [row_id for row_id in ALL_ROWS if row_id not in present]
    value = {"schema_version": 1, "run": h.run_id, "release_source": "fixture-local file remote", "rows": rows,
             "not_run": [{"row": row_id, "result": "not_run", "reason": "not run in this evidence run"}
                         for row_id in missing],
             "coverage": {"grid": grid["cells"], "grid_gaps": grid["gaps"], "phases": table["phases"],
                          "phase_gaps": table["gaps"], "observed_kinds": kinds,
                          # kept so that the package merge can recompute the tables over several evidence runs
                          "observed_effects": [[kind, phase, etype, sorted(name for name in names if name)]
                                               for (kind, phase, etype), names in sorted(
                                                   observed.items(), key=lambda item: tuple(map(str, item[0])))]}}
    h.evidence.write_json("CRASH_MATRIX.json", value)
    return value


def merge_matrices(matrices, pf_config):
    """One package CRASH_MATRIX over several evidence runs of the same equality set: every row keeps its run
    (``environment``), and both coverage tables are recomputed over the union of rows and observed plan effects."""
    rows, observed, kinds = [], {}, set()
    for matrix in matrices:
        for row in matrix.get("rows") or []:
            rows.append(dict(row, environment=matrix.get("run")))
        for kind, phase, etype, names in (matrix.get("coverage") or {}).get("observed_effects") or []:
            observed.setdefault((kind, phase, etype), set()).update(names or [None])
        kinds.update((matrix.get("coverage") or {}).get("observed_kinds") or [])
    usable = [row for row in rows if row.get("result") in ("passed", "blocked")]
    grid = coverage.grid(usable)
    declared = declared_reasons(pf_config, pf_config.PHASE_ORDER, observed, sorted(kinds), rows)
    table = coverage.phase_table(pf_config.PHASE_ORDER, list(observed), usable, declared=declared)
    present = {row["row"] for row in rows}
    return {"schema_version": 1, "runs": [matrix.get("run") for matrix in matrices],
            "release_source": "fixture-local file remote", "rows": rows,
            "not_run": [{"row": row_id, "result": "not_run", "reason": "not run in any evidence run"}
                        for row_id in ALL_ROWS if row_id not in present],
            "coverage": {"grid": grid["cells"], "grid_gaps": grid["gaps"], "phases": table["phases"],
                         "phase_gaps": table["gaps"], "observed_kinds": sorted(kinds)}}


def acceptance(h):
    cases = dict(h.results.get("cases") or {})
    value = {"schema_version": 1, "run": h.run_id, "cases": cases, "loops": {
        key: {"outcome": item.get("outcome"), "error": item.get("error")}
        for key, item in (h.results.get("loops") or {}).items()}, "l5": h.results.get("l5", [])}
    h.evidence.write_json("ACCEPTANCE_RESULTS.json", value)
    return value


def run(h):
    matrix = crash_matrix(h)
    acceptance(h)
    h.log(f"report: {len(matrix['rows'])} rows, {len(matrix['not_run'])} not run, "
          f"{len(matrix['coverage']['grid_gaps'])} grid gaps, {len(matrix['coverage']['phase_gaps'])} phase gaps")
    return "passed"
