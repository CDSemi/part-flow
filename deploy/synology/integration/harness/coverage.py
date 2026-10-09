"""Coverage tables of the real crash matrix (SPEC 8.3.1 grid, 8.3.2 phase/effect table). Pure; checked offline."""

GRID_TARGETS = ("migrations", "DB switch", "workspace rename", "deletion", "journal commit")
GRID_INJECTIONS = ("error exit", "timeout", "SIGTERM", "SIGKILL", "restart")
# SPEC 8.3.1 written N/A reasons (a cell without a row needs one of these, never an empty reason).
GRID_NA = {
    ("workspace rename", "timeout"): "a rename is a syscall with no pf deadline and no child process",
    ("workspace rename", "restart"): "the daemon takes no part in a filesystem rename; process restart is covered by "
                                     "R08/R19 re-entry",
    ("deletion", "timeout"): "Docker removals do not wait on locks, and DROP DATABASE with sessions fails "
                             "immediately (covered by R53)",
    ("journal commit", "timeout"): "journal writes are local file writes with no deadline",
}
IRREVERSIBLE_TYPES = ("database-migrate", "database-switch", "database-drop", "resource-delete", "source-switch",
                      "image-load", "service-change")
REASONS = ("no-effect-phase", "not-reached", "equivalent-to")


def grid(rows, *, na=None):
    """{"cells": [{target, injection, rows, na}], "gaps": [(target, injection)]}. A cell is covered by the rows that
    name it (``row["grid"]``: [[target, injection], ...]) and whose result is not ``failed``; otherwise it needs a
    non-empty N/A reason. A row that only reached ``blocked`` still names its cell (SPEC 8.10 decides the gate)."""
    na = GRID_NA if na is None else na
    cells, gaps = [], []
    for target in GRID_TARGETS:
        for injection in GRID_INJECTIONS:
            names = sorted({row["row"] for row in rows
                            for cell in row.get("grid") or () if tuple(cell) == (target, injection)})
            reason = na.get((target, injection))
            if names:
                cells.append({"target": target, "injection": injection, "rows": names, "na": None})
            elif isinstance(reason, str) and reason.strip():
                cells.append({"target": target, "injection": injection, "rows": [], "na": reason})
            else:
                cells.append({"target": target, "injection": injection, "rows": [], "na": None})
                gaps.append((target, injection))
    return {"cells": cells, "gaps": gaps}


def phase_table(phase_order, observed, rows, *, declared=None):
    """SPEC 8.3.2. ``phase_order``: pf_config.PHASE_ORDER. ``observed``: (kind, phase, effect type) from the real
    plan.json effect lists of the run. ``rows``: crash rows with kind/phase/effect_type/when ("before"|"after").
    ``declared``: {(kind, phase, effect type or None): (reason, detail)} with reason in REASONS.

    One entry per observed (kind, phase, effect type), and one per phase with no observed effect. An entry needs a
    ``before`` and an ``after`` row for an irreversible type (or an equivalent-to), any row for the others, or a
    declared reason; otherwise it is a gap."""
    declared = declared or {}
    entries, gaps = [], []
    observed = sorted(set(tuple(item) for item in observed))
    for kind, phases in sorted(phase_order.items()):
        for phase in phases:
            if phase == "planned":
                continue
            types = sorted({effect for k, p, effect in observed if k == kind and p == phase})
            for effect in types or [None]:
                key = (kind, phase, effect)
                matched = [row for row in rows if row.get("kind") == kind and row.get("phase") == phase
                           and (effect is None or row.get("effect_type") == effect)]
                before = sorted(row["row"] for row in matched if row.get("when") in ("before", None))
                after = sorted(row["row"] for row in matched if row.get("when") in ("after", None))
                reason = declared.get(key)
                entry = {"kind": kind, "phase": phase, "effect_type": effect, "before": before, "after": after,
                         "reason": None}
                if reason is not None:
                    if not isinstance(reason, (tuple, list)) or len(reason) != 2 or reason[0] not in REASONS \
                            or not str(reason[1]).strip():
                        entry["reason"] = None
                        gaps.append(key + ("invalid reason",))
                        entries.append(entry)
                        continue
                    entry["reason"] = {"code": reason[0], "detail": reason[1]}
                if effect is None:
                    ok = bool(matched) or entry["reason"] is not None
                elif effect in IRREVERSIBLE_TYPES:
                    ok = (bool(before) and bool(after)) or entry["reason"] is not None
                else:
                    ok = bool(matched) or entry["reason"] is not None
                if not ok:
                    gaps.append(key)
                entries.append(entry)
    return {"phases": entries, "gaps": gaps}
