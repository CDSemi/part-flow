"""Data, schema and no-effect oracles (SPEC 3.5).

Collection (``snapshot`` and friends) reads PostgreSQL inside the instance's db container through ``docker exec …
psql`` in ONE read-only REPEATABLE READ transaction with fixed session settings, and closes the session before the
next pf command (session discipline). The comparison functions are pure and covered offline by
tests/test_integration_harness.py.
"""
import hashlib
import json

from . import util

SESSION_SETTINGS = (
    ("TimeZone", "UTC"),
    ("DateStyle", "ISO, YMD"),
    ("IntervalStyle", "postgres"),
    ("extra_float_digits", "3"),
)
EXCLUDED_TABLES = ("alembic_version",)
# 0032's object set (SPEC 3.5 F): exactly these differ between F(0031) and F(0032).
MIGRATION_0032_OBJECTS = {
    "constraints": ("ck_audit_events_event_type", "ck_audit_events_entity_type"),
    "indexes": ("uq_audit_events_route_adjustment_device_event_id", "ix_part_movements_assigned_route_step_id"),
    "functions": ("partflow_assigned_route_steps_forbid_update",),
    "triggers": ("trg_assigned_route_steps_forbid_update",),
}
REQUIRED_HISTORY_0031 = ("audit_events", "part_movements", "machine_lifecycle_events", "quantity_flow_lineage",
                         "work_order_allocations", "worker_sessions")
REQUIRED_HISTORY_0032 = REQUIRED_HISTORY_0031 + ("assigned_route_steps",)
ALWAYS_VOLATILE = ("user_sessions",)
ALWAYS_HISTORY = ("quantity_flows",)
SEPARATOR = "\x1f"

# pg_trigger.tgtype bits (src/include/catalog/pg_trigger.h)
TRIGGER_TYPE_BEFORE = 1 << 1
TRIGGER_TYPE_DELETE = 1 << 3
TRIGGER_TYPE_UPDATE = 1 << 4
TRIGGER_TYPE_TRUNCATE = 1 << 5


def session_prelude():
    """The transaction prelude every snapshot runs (SPEC 3.5): one read-only REPEATABLE READ snapshot."""
    lines = ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"]
    lines += [f"SET LOCAL {name} = '{value}';" for name, value in SESSION_SETTINGS]
    return "\n".join(lines) + "\n"


TABLES_SQL = ("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
              "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1")
TRIGGERS_SQL = ("SELECT 'T', t.tgname, c.relname, t.tgenabled, t.tgtype::text, p.proname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc p ON p.oid = t.tgfoid WHERE n.nspname = 'public' AND NOT t.tgisinternal")
CONSTRAINTS_SQL = ("SELECT 'C', con.conname, c.relname, con.contype::text, con.convalidated::text, "
                   "pg_get_constraintdef(con.oid) FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                   "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'")
INDEXES_SQL = "SELECT 'I', indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
FUNCTIONS_SQL = ("SELECT 'P', p.proname, pg_get_function_identity_arguments(p.oid), md5(p.prosrc) FROM pg_proc p "
                 "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'")
COLUMNS_SQL = ("SELECT 'L', table_name, column_name, data_type, is_nullable, coalesce(column_default, ''), "
               "ordinal_position::text FROM information_schema.columns WHERE table_schema = 'public'")
HEADS_SQL = "SELECT 'H', version_num FROM alembic_version"


def table_sql(table):
    quoted = '"' + table.replace('"', '""') + '"'
    return (f"SELECT 'D', '{table}', count(*)::text, md5(coalesce(string_agg(t::text, E'\\n' ORDER BY t::text "
            f"COLLATE \"C\"), '')) FROM public.{quoted} t")


def snapshot_script(tables):
    """The psql script of one snapshot: prelude, heads, per-table count/md5, and the five schema parts."""
    parts = [session_prelude()]
    parts.append("SHOW TimeZone;\nSHOW DateStyle;\nSHOW IntervalStyle;\nSHOW extra_float_digits;\n")
    parts.append(HEADS_SQL + ";\n")
    for table in tables:
        if table in EXCLUDED_TABLES:
            continue
        parts.append(table_sql(table) + ";\n")
    for sql in (TRIGGERS_SQL, CONSTRAINTS_SQL, INDEXES_SQL, FUNCTIONS_SQL, COLUMNS_SQL):
        parts.append(sql + ";\n")
    parts.append("COMMIT;\n")
    return "".join(parts)


def parse_snapshot(output, *, label, instance):
    """Parse the unaligned (``-A -t -F <US>``) output of ``snapshot_script``."""
    lines = [line for line in output.split("\n") if line != ""]
    settings = []
    data = {"label": label, "instance": instance, "heads": [], "tables": {},
            "schema": {"triggers": [], "constraints": [], "indexes": [], "functions": [], "columns": []}}
    for line in lines:
        fields = line.split(SEPARATOR)
        kind = fields[0]
        if kind == "H" and len(fields) == 2:
            data["heads"].append(fields[1])
        elif kind == "D" and len(fields) == 4:
            data["tables"][fields[1]] = {"count": int(fields[2]), "md5": fields[3]}
        elif kind == "T":
            data["schema"]["triggers"].append(fields[1:])
        elif kind == "C":
            data["schema"]["constraints"].append(fields[1:])
        elif kind == "I":
            data["schema"]["indexes"].append(fields[1:])
        elif kind == "P":
            data["schema"]["functions"].append(fields[1:])
        elif kind == "L":
            data["schema"]["columns"].append(fields[1:])
        elif len(fields) == 1 and kind not in ("BEGIN", "SET", "COMMIT"):
            settings.append(kind)
    data["heads"].sort()
    data["session_settings"] = dict(zip([name for name, _ in SESSION_SETTINGS], settings[:len(SESSION_SETTINGS)]))
    finish_schema(data)
    return data


def _sha(rows):
    return hashlib.sha256("\n".join(SEPARATOR.join(row) for row in rows).encode("utf-8")).hexdigest()


def finish_schema(data):
    schema = data["schema"]
    for key in ("triggers", "constraints", "indexes", "functions", "columns"):
        schema[key] = sorted(schema[key])
    schema["columns_sha256"] = _sha(schema["columns"])
    schema["sha256"] = _sha([[key, _sha(schema[key])] for key in ("triggers", "constraints", "indexes", "functions",
                                                                    "columns")])
    return data


# ------------------------------------------------------------------------------------------------- relations


def schema_diff(first, second):
    """{part: {"removed": [...], "added": [...]}} between two schema fingerprints (by row)."""
    result = {}
    for key in ("triggers", "constraints", "indexes", "functions", "columns"):
        a = {SEPARATOR.join(row) for row in first["schema"][key]}
        b = {SEPARATOR.join(row) for row in second["schema"][key]}
        if a != b:
            result[key] = {"removed": sorted(a - b), "added": sorted(b - a)}
    return result


def history_set(snapshot):
    """H (SPEC 3.5): every public table with a non-internal BEFORE trigger on UPDATE, DELETE or TRUNCATE, plus
    quantity_flows."""
    tables = set(ALWAYS_HISTORY)
    for row in snapshot["schema"]["triggers"]:
        table, tgtype = row[1], int(row[3])
        if tgtype & TRIGGER_TYPE_BEFORE and tgtype & (TRIGGER_TYPE_DELETE | TRIGGER_TYPE_UPDATE |
                                                     TRIGGER_TYPE_TRUNCATE):
            tables.add(table)
    return sorted(tables)


def check_history_set(history, head):
    required = REQUIRED_HISTORY_0032 if head == "0032" else REQUIRED_HISTORY_0031
    missing = [table for table in required if table not in history]
    return ["FAIL oracle-history-set: missing " + ", ".join(missing)] if missing else []


def append_only_triggers_ok(snapshot, history):
    """Every append-only trigger of an H table is present and enabled ('O')."""
    problems = []
    found = {}
    for row in snapshot["schema"]["triggers"]:
        name, table, enabled, tgtype = row[0], row[1], row[2], int(row[3])
        if table in history and tgtype & TRIGGER_TYPE_BEFORE:
            found.setdefault(table, []).append((name, enabled))
            if enabled != "O":
                problems.append(f"trigger {name} on {table} is not enabled (tgenabled={enabled})")
    for table in history:
        if table not in ALWAYS_HISTORY and table not in found:
            problems.append(f"append-only trigger of {table} is missing")
    return problems


def volatile_set(first, second, history):
    """V: tables that differ between two idle snapshots, plus user_sessions; an H table in V stops the run."""
    volatile = set(ALWAYS_VOLATILE)
    for table, value in first["tables"].items():
        other = second["tables"].get(table)
        if other is None or other != value:
            volatile.add(table)
    clash = sorted(volatile & set(history))
    if clash:
        raise util.HarnessError("FAIL oracle-volatile-history: " + ", ".join(clash))
    return sorted(volatile)


def compare(first, second, volatile, *, migration=None):
    """SPEC 3.5 equality. ``migration`` None: S_x ≡ S_y (data and F equal). ``migration`` "0031->0032": data ≡ and
    F moves exactly by 0032's object set (S_x ≡ S_y [F: 0031→0032]). ``alembic_version`` is never data.
    Returns a list of difference strings (empty when the relation holds)."""
    problems = []
    volatile = set(volatile)
    tables = sorted((set(first["tables"]) | set(second["tables"])) - set(EXCLUDED_TABLES))
    for table in tables:
        a, b = first["tables"].get(table), second["tables"].get(table)
        if a is None or b is None:
            problems.append(f"table {table} present in only one snapshot")
            continue
        if table in volatile:
            if a["count"] != b["count"]:
                problems.append(f"volatile table {table}: count {a['count']} != {b['count']}")
        elif a != b:
            problems.append(f"table {table}: count/md5 {a['count']}/{a['md5'][:8]} != {b['count']}/{b['md5'][:8]}")
    if migration is None:
        if first["schema"]["sha256"] != second["schema"]["sha256"]:
            problems.append("schema fingerprint differs: " + json.dumps(schema_diff(first, second))[:2000])
    elif migration == "0031->0032":
        problems += migration_0032_problems(first, second)
    else:
        raise ValueError("unknown migration relation " + str(migration))
    return problems


def _object_names(rows, part):
    if part == "indexes":
        return {row[0] for row in rows}
    if part == "functions":
        return {row[0] for row in rows}
    return {row[0] for row in rows}


def migration_0032_problems(before, after):
    """F(0031) -> F(0032) must differ in exactly 0032's object set; every append-only trigger stays enabled."""
    problems = []
    diff = schema_diff(before, after)
    for part, change in diff.items():
        if part == "columns":
            problems.append("columns changed across 0031->0032: " + json.dumps(change)[:800])
            continue
        allowed = set(MIGRATION_0032_OBJECTS.get(part, ()))
        names = {item.split(SEPARATOR)[0] for item in change["removed"] + change["added"]}
        extra = sorted(names - allowed)
        if extra:
            problems.append(f"{part} outside 0032's object set changed: {extra}")
    for part, names in MIGRATION_0032_OBJECTS.items():
        present = _object_names(after["schema"][part], part)
        for name in names:
            if name not in present:
                problems.append(f"0032 object {name} ({part}) missing after the migration")
    for part in ("indexes", "functions", "triggers"):
        before_names = _object_names(before["schema"][part], part)
        for name in MIGRATION_0032_OBJECTS[part]:
            if name in before_names:
                problems.append(f"0032 object {name} ({part}) already present before the migration")
    history = history_set(after)
    problems += append_only_triggers_ok(after, history)
    return problems


# ------------------------------------------------------------------------------------------------ no-effect


MUTATING_EVENTS = {
    "container": ("create", "start", "stop", "die", "destroy", "kill", "rename", "update"),
    "image": ("build", "load", "tag", "untag", "delete", "import", "pull"),
    "volume": ("create", "destroy"),
    "network": ("create", "destroy"),
}
# The bookkeeping a refused command may leave (SPEC 3.5 N (d)), fixed from pf-admin.py at 181a806: in ONE new
# operations/<id>/ directory, begin_operation's operation.json (2567), freeze_admin_config's admin-config.json
# (2590), freeze_app_config's app.env + frozen-config.json snapshot, and the pre-plan read-only observation records
# a refusal may follow: the daemon verification (daemon.json, 1923), the inventory preflight
# (inventory-preflight.json, 2204), the Compose envelope renders (compose-<n>.json 1970, compose-envelope.json 2069)
# and the capacity decision (capacity.json). Nothing else; a plan, journal, unresolved-effect, child or attempt
# record in the new directory is a violation.
BOOKKEEPING_PATTERNS = (r"operation\.json", r"admin-config\.json", r"app\.env", r"frozen-config\.json",
                        r"daemon\.json", r"inventory-preflight\.json", r"compose-[0-9]+\.json",
                        r"compose-envelope\.json", r"capacity\.json")
BOOKKEEPING_FILES = BOOKKEEPING_PATTERNS
FORBIDDEN_OPERATION_FILES = ("plan.json", "journal.json", "unresolved-effects.json", "children.json",
                             "attempts.json")


def is_bookkeeping(name):
    import re
    return any(re.fullmatch(pattern, name) for pattern in BOOKKEEPING_PATTERNS)


def mutating_events(events):
    found = []
    for item in events:
        kind = item.get("Type") or item.get("type")
        action = (item.get("Action") or item.get("status") or "").split(":")[0].strip()
        if action in MUTATING_EVENTS.get(kind, ()):
            found.append({"type": kind, "action": action, "id": (item.get("Actor") or {}).get("ID") or item.get("id"),
                          "time": item.get("time")})
    return found


def tree_difference(before, after):
    """Paths added, removed or changed between two {relative path: sha256 or 'dir'} maps."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(path for path in set(before) & set(after) if before[path] != after[path])
    return added, removed, changed


def classify_tree_change(before, after, *, operations_prefixes=()):
    """SPEC 3.5 N (d)/(e): every difference must be inside exactly one new ``operations/<id>/`` directory holding
    only bookkeeping files (and no plan/journal/unresolved effects); pf's log files may change. Returns
    (bookkeeping, violations)."""
    added, removed, changed = tree_difference(before, after)
    bookkeeping, violations = [], []
    new_operation_dirs = set()
    for path in removed:
        violations.append("removed: " + path)
    for path in changed:
        if path.endswith(".log"):
            bookkeeping.append("log changed: " + path)
        else:
            violations.append("changed: " + path)
    for path in added:
        parts = path.split("/")
        operation_dir = None
        for prefix in operations_prefixes:
            prefix_parts = prefix.strip("/").split("/")
            if parts[:len(prefix_parts)] == prefix_parts and len(parts) > len(prefix_parts):
                operation_dir = "/".join(parts[:len(prefix_parts) + 1])
                rest = parts[len(prefix_parts) + 1:]
                break
        if operation_dir is None:
            if path.endswith(".log"):
                bookkeeping.append("log added: " + path)
            else:
                violations.append("added: " + path)
            continue
        new_operation_dirs.add(operation_dir)
        if not rest:
            bookkeeping.append("operation directory: " + path)
        elif len(rest) == 1 and is_bookkeeping(rest[0]):
            bookkeeping.append("bookkeeping: " + path)
        elif len(rest) == 1 and rest[0] in FORBIDDEN_OPERATION_FILES:
            violations.append("new journal file: " + path)
        else:
            violations.append("added inside the operation directory: " + path)
    if len(new_operation_dirs) > 1:
        violations.append("more than one new operation directory: " + ", ".join(sorted(new_operation_dirs)))
    return bookkeeping, violations


def tree_hashes(roots, *, exclude=()):
    """{path: sha256 | 'dir' | 'link:<target>'} of every entry under ``roots`` (read-only, no-follow)."""
    import os
    result = {}
    for root in roots:
        for directory, dirnames, filenames in os.walk(str(root), followlinks=False):
            if any(directory == item or directory.startswith(item + "/") for item in exclude):
                dirnames[:] = []
                continue
            result[directory.lstrip("/")] = "dir"
            for name in filenames + [item for item in dirnames if os.path.islink(os.path.join(directory, item))]:
                path = os.path.join(directory, name)
                if os.path.islink(path):
                    result[path.lstrip("/")] = "link:" + os.readlink(path)
                    continue
                try:
                    with open(path, "rb") as handle:
                        digest = hashlib.sha256()
                        for block in iter(lambda: handle.read(1 << 20), b""):
                            digest.update(block)
                    result[path.lstrip("/")] = digest.hexdigest()
                except OSError as exc:
                    result[path.lstrip("/")] = "unreadable:" + type(exc).__name__
    return result
