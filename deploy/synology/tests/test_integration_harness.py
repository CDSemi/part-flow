"""PF-A3.4 D6: the safety-relevant pure logic of the isolated integration harness (no Docker, no network).

Case mapping (PF-A3.4 SPEC section 8.7):
  HostAllowlist HA-1..HA-4 (the host-daemon allowlist of SPEC 2.1), InventoryDiff ID-1..ID-5 (SPEC 2.3),
  PhraseAllowlist PH-1..PH-3 (every confirm(...)/Type exactly constructor of pf-admin.py and pf_install.py),
  SnapshotRelations SR-1..SR-7 (SPEC 3.5 oracles), NoEffect NE-1..NE-4 (SPEC 3.5 N), Coverage CV-1..CV-5
  (SPEC 8.3.1/8.3.2 generators).

The real-Docker scenarios live in deploy/synology/integration/ outside test discovery: they are never skipped
here and never counted as offline evidence.
"""
import ast
import copy
from pathlib import Path
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
INTEGRATION = PACKAGE / "integration"
sys.path.insert(0, str(INTEGRATION))
import host_inventory  # noqa: E402
from harness import coverage, observe, oracles, phrases  # noqa: E402

RUN = "20261008T120000Z-0123abcd"
DIND = "docker:28.5.1-dind@sha256:" + "a" * 64
DIND_ID = "sha256:" + "b" * 64
NAME = "pfa34-" + RUN
LABEL = "io.partflow.pfa34.run=" + RUN
RAW = "D:/.claude-tmp/part-flow/ops/PF-A3.4/run-" + RUN


def state(**changes):
    value = {"dind_present_before": False, "pulled_by_run": True, "dind_ref": DIND, "dind_image_id": DIND_ID}
    value.update(changes)
    return value


def run_form():
    return ["run", "-d", "--privileged", "--name", NAME, "--label", LABEL, "--network", NAME, "--mount",
            "type=volume,dst=/pfa34,volume-label=" + LABEL, "-e", "DOCKER_TLS_CERTDIR=", "--entrypoint", "/bin/sh",
            DIND, "-c", host_inventory.KEEPALIVE]


class HostAllowlist(unittest.TestCase):
    def check(self, argv, **kwargs):
        return host_inventory.check_argv(argv, run=RUN, state=kwargs.pop("state", state()), raw_dir=RAW, **kwargs)

    def refused(self, argv, **kwargs):
        with self.assertRaises(host_inventory.Refused, msg=argv):
            self.check(argv, **kwargs)

    def test_ha1_exactly_the_spec_forms_are_accepted(self):
        accepted = [
            ["version"], ["version", "--format", "{{json .}}"], ["info", "--format", "{{.OperatingSystem}}"],
            ["ps", "-a", "--no-trunc", "--format", "{{.ID}}"], ["images", "--no-trunc", "--format", "{{.ID}}"],
            ["volume", "ls", "--format", "{{.Name}}"], ["network", "ls", "--no-trunc", "--format", "{{.ID}}"],
            ["inspect", "a" * 64], ["inspect", "--format", "{{.Id}}", NAME], ["volume", "inspect", "anything_x"],
            ["network", "inspect", "c" * 64], ["image", "inspect", "docker:28.5.1-dind"],
            ["pull", "docker:28.5.1-dind"],
            ["network", "create", "--label", LABEL, NAME], run_form(),
            ["cp", "-", NAME + ":/pfa34/work"], ["cp", RAW + "/inputs.tar", NAME + ":/pfa34/work/inputs.tar"],
            ["cp", NAME + ":/pfa34/evidence", RAW + "/evidence"],
            ["exec", NAME, "cat", "/run/pfa34/state"], ["exec", "-i", NAME, "python3", "-B", "-m", "harness"],
            ["exec", "-d", NAME, "/pfa34/work/dind-entry.sh"],
            ["rm", "-f", "-v", NAME], ["network", "rm", NAME], ["image", "rm", DIND_ID],
        ]
        for argv in accepted:
            self.assertTrue(self.check(argv), argv)
        self.assertEqual(self.check(["exec", "-d", NAME, "/pfa34/work/dind-entry.sh"]), "exec:supervisor")

    def test_ha2_mounts_sockets_prune_and_compose_are_refused(self):
        bind = run_form()
        bind[bind.index("--mount") + 1] = "type=bind,src=/,dst=/host"
        self.refused(bind)
        socket = run_form()
        socket[socket.index("--mount"):socket.index("--mount")] = ["-v", "/var/run/docker.sock:/var/run/docker.sock"]
        self.refused(socket)
        for extra in (["--pid=host"], ["--network=host"], ["-v", "D:/x:/x"]):
            argv = run_form()
            argv[1:1] = extra
            self.refused(argv)
        for argv in (["system", "prune", "-f"], ["volume", "prune", "-f"], ["image", "prune", "-a"],
                     ["builder", "prune"], ["compose", "up", "-d"], ["compose", "-p", "x", "down"],
                     ["exec", "-d", NAME, "sh", "-c", "dockerd"], ["exec", "-w", "/", NAME, "ls"],
                     ["run", "--rm", "alpine", "true"], ["pull", "alpine"], ["image", "rm", "sha256:" + "c" * 64],
                     ["cp", "-", "other:/x"], ["cp", NAME + ":/x", "B:/out"], ["cp", NAME + ":/x", "C:/elsewhere"],
                     ["exec", NAME, "sh", "-c", "a\nb"], ["inspect", "--bogus", "x"]):
            self.refused(argv)

    def test_ha3_foreign_names_are_refused_in_every_mutating_form(self):
        for argv in (["rm", "-f", "-v", "partflow-db-1"], ["network", "rm", "partflow_default"],
                     ["exec", NAME, "docker", "rm", "-f", "partflow-backend-1"],
                     ["exec", NAME, "cat", "/srv/agentmemory"], ["network", "create", "--label", LABEL, "other"],
                     ["rm", "-f", "-v", "pfa34-20261008T120000Z-ffffffff"]):
            self.refused(argv)

    def test_ha4_conditional_forms_follow_the_run_state(self):
        self.refused(["pull", "docker:28.5.1-dind"], state=state(dind_present_before=True))
        self.refused(["image", "rm", DIND_ID], state=state(pulled_by_run=False))
        self.refused(run_form(), state=state(dind_ref=None))
        with self.assertRaises(host_inventory.Refused):
            host_inventory.check_argv(["version"], run="not-a-run")


def snap(containers=None, images=None, volumes=None, networks=None):
    return {"containers": containers or {}, "images": images or {}, "volumes": volumes or {},
            "networks": networks or {}}


def container(name, *, project=None, started="2026-10-08T01:00:00Z", status="running", restarts=0, labels=None):
    values = dict(labels or {})
    if project:
        values["com.docker.compose.project"] = project
    return {"name": name, "image": "sha256:" + "1" * 64, "labels": values, "status": status, "started_at": started,
            "restart_count": restarts, "project": project}


class InventoryDiff(unittest.TestCase):
    def setUp(self):
        self.before = snap(
            containers={"p" * 64: container("partflow-db-1", project="partflow"),
                        "t" * 64: container("transient", status="running")},
            images={"sha256:" + "2" * 64: {"repo_tags": ["partflow/backend:s2"], "repo_digests": []}},
            volumes={"partflow_postgres_data": {"driver": "local", "labels": {}, "created_at": "x", "mountpoint": "/v",
                                                "project": "partflow"}})

    def classify(self, after, **kwargs):
        return host_inventory.classify(self.before, after, run=RUN, state=kwargs.pop("state", {
            "anonymous_volumes": ["a" * 64], "pulled_by_run": True, "dind_image_id": DIND_ID}), **kwargs)

    def test_id1_identical_is_clean(self):
        self.assertEqual(self.classify(copy.deepcopy(self.before))["result"], "clean")

    def test_id2_a_changed_started_at_of_a_protected_container_fails(self):
        after = copy.deepcopy(self.before)
        after["containers"]["p" * 64]["started_at"] = "2026-10-08T02:00:00Z"
        report = self.classify(after)
        self.assertEqual(report["result"], "FAIL host-touched")
        self.assertEqual(report["protected_changes"][0]["fields"], ["started_at"])
        after = copy.deepcopy(self.before)
        after["containers"]["p" * 64]["restart_count"] = 1
        self.assertEqual(self.classify(after)["result"], "FAIL host-touched")
        after = copy.deepcopy(self.before)
        del after["volumes"]["partflow_postgres_data"]
        self.assertEqual(self.classify(after)["result"], "FAIL host-touched")

    def test_id3_harness_resources_must_be_absent_after(self):
        after = copy.deepcopy(self.before)
        after["containers"]["h" * 64] = container(NAME, labels={host_inventory.RUN_LABEL: RUN})
        self.assertEqual(self.classify(after)["result"], "FAIL host-leak")
        after = copy.deepcopy(self.before)
        after["volumes"]["a" * 64] = {"driver": "local", "labels": {}, "created_at": "y", "mountpoint": "/a"}
        self.assertEqual(self.classify(after)["result"], "FAIL host-leak")
        after = copy.deepcopy(self.before)
        after["images"][DIND_ID] = {"repo_tags": ["docker:28.5.1-dind"], "repo_digests": []}
        self.assertEqual(self.classify(after)["result"], "FAIL host-leak")

    def test_id4_foreign_concurrent_is_listed_never_a_failure_unless_named(self):
        after = copy.deepcopy(self.before)
        del after["containers"]["t" * 64]
        after["containers"]["n" * 64] = container("other-session-test")
        report = self.classify(after)
        self.assertEqual(report["result"], "clean")
        self.assertEqual(sorted(item["change"] for item in report["foreign_concurrent"]), ["created", "removed"])
        commands = [{"argv": ["docker", "rm", "-f", "n" * 12], "form": "rm:dind"}]
        self.assertEqual(self.classify(after, commands=commands)["result"], "FAIL host-touched")

    def test_id5_a_concurrent_retag_is_foreign_never_ours(self):
        after = copy.deepcopy(self.before)
        after["images"]["sha256:" + "2" * 64]["repo_tags"] = []
        after["images"]["sha256:" + "3" * 64] = {"repo_tags": ["partflow/backend:s2"], "repo_digests": []}
        report = self.classify(after)
        self.assertEqual(report["result"], "clean")
        self.assertIn("retagged", [item["change"] for item in report["foreign_concurrent"]])
        after["images"]["sha256:" + "3" * 64]["repo_tags"] = []
        self.assertEqual(self.classify(after)["result"], "FAIL host-touched")


# ---------------------------------------------------------------------------------------------- phrases


CONFIRM_FUNCTIONS = {"confirm": 0, "permission_confirm": 0, "_confirm": 1}


class _Index:
    """Leading string literal(s) of a phrase expression, resolving local names and the module's phrase helpers."""

    def __init__(self, tree):
        self.functions = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.setdefault(node.name, []).append(node)

    def leading(self, node, scope, depth=0):
        if depth > 8:
            return {None}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.JoinedStr):
            first = node.values[0]
            if isinstance(first, ast.Constant):
                text = first.value
                if len(node.values) == 1:
                    return {text}
                return {text}
            return {item + (" " if not item.endswith(" ") else "")
                    for item in self.leading(first.value, scope, depth + 1) if item is not None} or {None}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.leading(node.left, scope, depth + 1)
        if isinstance(node, ast.IfExp):
            return self.leading(node.body, scope, depth + 1) | self.leading(node.orelse, scope, depth + 1)
        if isinstance(node, ast.Name):
            values = set()
            for item in ast.walk(scope):
                if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == node.id
                                                        for target in item.targets):
                    values |= self.leading(item.value, scope, depth + 1)
            return values or {None}
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            values = set()
            for function in self.functions.get(name, ()):
                for item in ast.walk(function):
                    if isinstance(item, ast.Return) and item.value is not None:
                        values |= self.leading(item.value, function, depth + 1)
            return values or {None}
        return {None}


def constructor_verbs(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    index = _Index(tree)
    verbs = {}
    for function in [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name not in CONFIRM_FUNCTIONS or function.name in CONFIRM_FUNCTIONS:
                continue
            position = CONFIRM_FUNCTIONS[name]
            if len(node.args) <= position:
                continue
            for verb in index.leading(node.args[position], function):
                verbs.setdefault(verb, []).append(f"{Path(path).name}:{node.lineno}")
    return verbs


class PhraseAllowlist(unittest.TestCase):
    def test_ph1_every_confirmation_constructor_has_an_entry(self):
        found = {}
        for path in (PACKAGE / "pf-admin.py", PACKAGE / "pf_install.py"):
            for verb, places in constructor_verbs(path).items():
                found.setdefault(verb, []).extend(places)
        self.assertNotIn(None, found, f"unresolved phrase constructors: {found.get(None)}")
        normalized = {verb.strip(): places for verb, places in found.items()}
        missing = sorted(set(normalized) - set(phrases.PHRASES))
        self.assertEqual(missing, [], {verb: normalized[verb] for verb in missing})
        stale = sorted(set(phrases.PHRASES) - set(normalized))
        self.assertEqual(stale, [], "allowlist entries no constructor produces")

    def test_ph2_entries_accept_the_constructed_shapes(self):
        samples = {
            "INSTALL CONTROL r-0123456789abcdef": "INSTALL CONTROL", "REGISTER alpha": "REGISTER",
            "DEPLOY af771729426c": "DEPLOY", "UPDATE 181a8064de74": "UPDATE",
            "RESTORE partflow_staging 20261008T151105Z-af771729426c-c044f6": "RESTORE",
            "ROLLBACK 20261008T151105Z-af771729426c-c044f6": "ROLLBACK", "PURGE partflow": "PURGE",
            "DELETE partflow_staging": "DELETE", "ERASE partflow 0A1B2C": "ERASE",
            "RESTORE INSTANCE partflow": "RESTORE INSTANCE", "RESUME 1234abcd": "RESUME",
            "ACKNOWLEDGE 1234abcd": "ACKNOWLEDGE", "EMERGENCY BACKUP partflow": "EMERGENCY BACKUP",
            "ABANDON RESTORE partflow 1234abcd": "ABANDON RESTORE", "KEEP WORKSPACE 1234abcd": "KEEP WORKSPACE",
            "CLEANUP partflow 1234abcd": "CLEANUP", "REMOVE RECOVERY TARGET pfrecover-0123456789ab":
                "REMOVE RECOVERY TARGET", "RESUME PURGE partflow purge-20261008T151105Z-x": "RESUME PURGE",
        }
        for phrase, verb in samples.items():
            self.assertEqual(phrases.entry_for(phrase), verb, phrase)

    def test_ph3_an_unknown_phrase_is_refused(self):
        for phrase in ("DROP EVERYTHING", "DEPLOY main", "UPDATE 181a8064de7", "ERASE partflow abcdef", "yes", ""):
            self.assertIsNone(phrases.entry_for(phrase), phrase)
        with self.assertRaises(ValueError):
            phrases.exact("DROP EVERYTHING")
        with self.assertRaises(KeyError):
            phrases.allow("DROP")
        self.assertFalse(phrases.allowed("DEPLOY ffffffffffff", phrases.exact("DEPLOY af771729426c")))


# ------------------------------------------------------------------------------------------ snapshot relations


TRIGGER_BEFORE_UPDATE_DELETE = str(oracles.TRIGGER_TYPE_BEFORE | oracles.TRIGGER_TYPE_UPDATE |
                                   oracles.TRIGGER_TYPE_DELETE)


def snapshot(*, head="0031_phase14_beyond_demand", tables=None, triggers=None, constraints=None, indexes=None,
             functions=None, columns=None):
    value = {"label": "x", "instance": "alpha", "heads": [head],
             "tables": tables if tables is not None else {
                 "audit_events": {"count": 3, "md5": "a" * 32}, "part_movements": {"count": 2, "md5": "b" * 32},
                 "work_orders": {"count": 3, "md5": "c" * 32}, "user_sessions": {"count": 1, "md5": "d" * 32}},
             "schema": {
                 "triggers": triggers if triggers is not None else [
                     ["trg_audit_events_append_only", "audit_events", "O", TRIGGER_BEFORE_UPDATE_DELETE, "f1"],
                     ["trg_part_movements_append_only", "part_movements", "O", TRIGGER_BEFORE_UPDATE_DELETE, "f2"]],
                 "constraints": constraints if constraints is not None else [
                     ["ck_audit_events_event_type", "audit_events", "c", "true", "CHECK (event_type IN (old))"],
                     ["ck_audit_events_entity_type", "audit_events", "c", "true", "CHECK (entity_type IN (old))"]],
                 "indexes": indexes if indexes is not None else [["pk_work_orders", "CREATE UNIQUE INDEX pk ..."]],
                 "functions": functions if functions is not None else [["f1", "", "m1"], ["f2", "", "m2"]],
                 "columns": columns if columns is not None else [["work_orders", "id", "integer", "NO", "", "1"]]}}
    return oracles.finish_schema(value)


def migrated(base):
    value = copy.deepcopy(base)
    value["heads"] = ["0032_phase14_route_adjusted"]
    schema = value["schema"]
    schema["constraints"] = [
        ["ck_audit_events_event_type", "audit_events", "c", "true", "CHECK (event_type IN (new))"],
        ["ck_audit_events_entity_type", "audit_events", "c", "true", "CHECK (entity_type IN (new))"]]
    schema["indexes"] = schema["indexes"] + [
        ["uq_audit_events_route_adjustment_device_event_id", "CREATE UNIQUE INDEX uq ..."],
        ["ix_part_movements_assigned_route_step_id", "CREATE INDEX ix ..."]]
    schema["functions"] = schema["functions"] + [["partflow_assigned_route_steps_forbid_update", "", "m3"]]
    schema["triggers"] = schema["triggers"] + [
        ["trg_assigned_route_steps_forbid_update", "assigned_route_steps", "O",
         str(oracles.TRIGGER_TYPE_BEFORE | oracles.TRIGGER_TYPE_UPDATE), "partflow_assigned_route_steps_forbid_update"]]
    return oracles.finish_schema(value)


class SnapshotRelations(unittest.TestCase):
    def test_sr1_equivalence_needs_equal_data_and_schema(self):
        base = snapshot()
        self.assertEqual(oracles.compare(base, copy.deepcopy(base), ["user_sessions"]), [])
        changed = copy.deepcopy(base)
        changed["tables"]["work_orders"] = {"count": 4, "md5": "e" * 32}
        self.assertTrue(oracles.compare(base, changed, ["user_sessions"]))
        schema = copy.deepcopy(base)
        schema["schema"]["functions"][0][2] = "other"
        oracles.finish_schema(schema)
        self.assertTrue(oracles.compare(base, schema, ["user_sessions"]))

    def test_sr2_volatile_tables_compare_by_count_only(self):
        base = snapshot()
        other = copy.deepcopy(base)
        other["tables"]["user_sessions"]["md5"] = "f" * 32
        self.assertEqual(oracles.compare(base, other, ["user_sessions"]), [])
        other["tables"]["user_sessions"]["count"] = 2
        self.assertTrue(oracles.compare(base, other, ["user_sessions"]))

    def test_sr3_the_0032_relation_accepts_exactly_its_object_set(self):
        base = snapshot()
        after = migrated(base)
        self.assertEqual(oracles.compare(base, after, ["user_sessions"], migration="0031->0032"), [])
        self.assertTrue(oracles.compare(base, after, ["user_sessions"]))  # plain equality refuses the move
        extra = copy.deepcopy(after)
        extra["schema"]["indexes"].append(["ix_unrelated", "CREATE INDEX ix_unrelated ..."])
        oracles.finish_schema(extra)
        self.assertTrue(oracles.compare(base, extra, ["user_sessions"], migration="0031->0032"))
        column = copy.deepcopy(after)
        column["schema"]["columns"].append(["work_orders", "new", "text", "YES", "", "2"])
        oracles.finish_schema(column)
        self.assertTrue(oracles.compare(base, column, ["user_sessions"], migration="0031->0032"))
        incomplete = copy.deepcopy(after)
        incomplete["schema"]["indexes"] = [row for row in incomplete["schema"]["indexes"]
                                           if row[0] != "ix_part_movements_assigned_route_step_id"]
        oracles.finish_schema(incomplete)
        self.assertTrue(oracles.compare(base, incomplete, ["user_sessions"], migration="0031->0032"))

    def test_sr4_a_missing_or_disabled_append_only_trigger_is_refused(self):
        base = snapshot()
        after = migrated(base)
        disabled = copy.deepcopy(after)
        disabled["schema"]["triggers"][0][2] = "D"
        oracles.finish_schema(disabled)
        problems = oracles.compare(base, disabled, ["user_sessions"], migration="0031->0032")
        self.assertTrue(any("not enabled" in item or "outside" in item for item in problems), problems)
        missing = copy.deepcopy(after)
        missing["schema"]["triggers"] = [row for row in missing["schema"]["triggers"] if row[1] != "part_movements"]
        oracles.finish_schema(missing)
        self.assertTrue(oracles.compare(base, missing, ["user_sessions"], migration="0031->0032"))
        self.assertTrue(oracles.append_only_triggers_ok(missing, ["part_movements", "audit_events"]))

    def test_sr5_an_alembic_version_only_difference_is_not_data_drift(self):
        base = snapshot()
        other = copy.deepcopy(base)
        other["heads"] = ["0032_phase14_route_adjusted"]
        other["tables"]["alembic_version"] = {"count": 1, "md5": "9" * 32}
        self.assertEqual(oracles.compare(base, other, ["user_sessions"]), [])
        self.assertNotIn("alembic_version", oracles.snapshot_script(["alembic_version", "work_orders"])
                         .split("HEADS")[0].split("FROM public.")[-1])

    def test_sr6_history_and_volatile_sets(self):
        base = snapshot()
        history = oracles.history_set(base)
        self.assertEqual(history, ["audit_events", "part_movements", "quantity_flows"])
        self.assertTrue(oracles.check_history_set(history, "0031"))
        idle = copy.deepcopy(base)
        idle["tables"]["user_sessions"]["md5"] = "0" * 32
        self.assertEqual(oracles.volatile_set(base, idle, history), ["user_sessions"])
        moving = copy.deepcopy(base)
        moving["tables"]["audit_events"]["count"] = 4
        with self.assertRaises(Exception) as caught:
            oracles.volatile_set(base, moving, history)
        self.assertIn("FAIL oracle-volatile-history", str(caught.exception))

    def test_sr7_the_fixed_session_settings_are_applied_in_one_read_only_snapshot(self):
        script = observe.snapshot_script()
        self.assertTrue(script.startswith("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"))
        for name, value in oracles.SESSION_SETTINGS:
            self.assertIn(f"SET LOCAL {name} = '{value}';", script)
        self.assertEqual(script.count("BEGIN"), 1)
        self.assertTrue(script.rstrip().endswith("COMMIT;"))
        self.assertIn("alembic_version", script)  # excluded from S only
        self.assertIn("c.relname <> 'alembic_version'", script)
        output = "\n".join(["UTC", "ISO, YMD", "postgres", "3", "H\x1f0031_x", "D\x1fwork_orders\x1f3\x1f" + "c" * 32,
                            "T\x1ftrg\x1fwork_orders\x1fO\x1f" + TRIGGER_BEFORE_UPDATE_DELETE + "\x1ff"])
        parsed = oracles.parse_snapshot(output, label="L", instance="alpha")
        self.assertEqual(parsed["session_settings"], {"TimeZone": "UTC", "DateStyle": "ISO, YMD",
                                                      "IntervalStyle": "postgres", "extra_float_digits": "3"})
        self.assertEqual(parsed["tables"], {"work_orders": {"count": 3, "md5": "c" * 32}})
        self.assertEqual(parsed["heads"], ["0031_x"])


# -------------------------------------------------------------------------------------------------- no-effect


OPS = "srv/pfa34/pfroot/instances/u/operations"


class NoEffect(unittest.TestCase):
    def tree(self, **extra):
        value = {OPS: "dir", OPS + "/old": "dir", OPS + "/old/journal.json": "j1",
                 "srv/pfa34/pfroot/registry/instances.json": "r1", "srv/pfa34/homes/alpha/config/.env": "e1"}
        value.update(extra)
        return value

    def classify(self, after):
        return oracles.classify_tree_change(self.tree(), after, operations_prefixes=(OPS,))

    def test_ne1_the_bookkeeping_allowlist_is_accepted(self):
        after = self.tree(**{OPS + "/new": "dir", OPS + "/new/operation.json": "o", OPS + "/new/admin-config.json": "a",
                             OPS + "/new/app.env": "e", OPS + "/new/frozen-config.json": "f",
                             OPS + "/new/daemon.json": "d", OPS + "/new/inventory-preflight.json": "i",
                             OPS + "/new/compose-1.json": "c", OPS + "/new/capacity.json": "k"})
        bookkeeping, violations = self.classify(after)
        self.assertEqual(violations, [])
        self.assertEqual(len(bookkeeping), 9)

    def test_ne2_a_new_journal_or_plan_is_a_violation(self):
        for name in ("plan.json", "journal.json", "unresolved-effects.json", "children.json", "attempts.json",
                     "other.json"):
            after = self.tree(**{OPS + "/new": "dir", OPS + "/new/operation.json": "o", OPS + "/new/" + name: "x"})
            _, violations = self.classify(after)
            self.assertTrue(violations, name)

    def test_ne3_any_other_tree_change_is_a_violation(self):
        for after in (self.tree(**{"srv/pfa34/homes/alpha/config/.env": "e2"}),
                      self.tree(**{OPS + "/old/journal.json": "j2"}),
                      {key: value for key, value in self.tree().items() if not key.endswith("instances.json")},
                      self.tree(**{"srv/pfa34/homes/alpha/backups/x": "y"}),
                      self.tree(**{OPS + "/a": "dir", OPS + "/a/operation.json": "o", OPS + "/b": "dir",
                                   OPS + "/b/operation.json": "o"})):
            _, violations = self.classify(after)
            self.assertTrue(violations, after)

    def test_ne4_mutating_daemon_events_are_detected(self):
        events = [{"Type": "container", "Action": "start", "Actor": {"ID": "x"}},
                  {"Type": "image", "Action": "tag", "Actor": {"ID": "y"}},
                  {"Type": "container", "Action": "exec_start: psql", "Actor": {"ID": "z"}},
                  {"Type": "volume", "Action": "mount", "Actor": {"ID": "v"}}]
        found = oracles.mutating_events(events)
        self.assertEqual([(item["type"], item["action"]) for item in found], [("container", "start"),
                                                                              ("image", "tag")])


# --------------------------------------------------------------------------------------------------- coverage


class Coverage(unittest.TestCase):
    def full_rows(self):
        rows = []
        for target in coverage.GRID_TARGETS:
            for injection in coverage.GRID_INJECTIONS:
                if (target, injection) not in coverage.GRID_NA:
                    rows.append({"row": f"R{len(rows) + 1:02d}", "grid": [[target, injection]]})
        return rows

    def test_cv1_a_complete_grid_has_no_gap(self):
        self.assertEqual(coverage.grid(self.full_rows())["gaps"], [])

    def test_cv2_an_uncovered_grid_cell_is_flagged(self):
        rows = [row for row in self.full_rows() if row["grid"] != [["DB switch", "restart"]]]
        self.assertEqual(coverage.grid(rows)["gaps"], [("DB switch", "restart")])

    def test_cv3_an_na_without_a_reason_is_flagged(self):
        na = dict(coverage.GRID_NA)
        na[("deletion", "timeout")] = "   "
        self.assertIn(("deletion", "timeout"), coverage.grid(self.full_rows(), na=na)["gaps"])

    def test_cv4_an_uncovered_kind_phase_effect_is_flagged(self):
        order = {"update": ("planned", "preparing", "migrating", "finalizing")}
        observed = [("update", "preparing", "source-stage"), ("update", "migrating", "database-migrate")]
        rows = [{"row": "R59", "kind": "update", "phase": "preparing", "effect_type": "source-stage", "when": "after"},
                {"row": "R03", "kind": "update", "phase": "migrating", "effect_type": "database-migrate",
                 "when": "after"}]
        declared = {("update", "finalizing", None): ("no-effect-phase", "no effect in any real plan")}
        table = coverage.phase_table(order, observed, rows, declared=declared)
        self.assertEqual(table["gaps"], [("update", "migrating", "database-migrate")])  # irreversible: before missing
        rows.append({"row": "R42", "kind": "update", "phase": "migrating", "effect_type": "database-migrate",
                     "when": "before"})
        self.assertEqual(coverage.phase_table(order, observed, rows, declared=declared)["gaps"], [])
        self.assertEqual(coverage.phase_table(order, observed, rows)["gaps"], [("update", "finalizing", None)])

    def test_cv5_a_declared_reason_must_be_known_and_explained(self):
        order = {"backup": ("planned", "finalizing")}
        for reason in (("because", "x"), ("no-effect-phase", ""), "no-effect-phase"):
            table = coverage.phase_table(order, [], [], declared={("backup", "finalizing", None): reason})
            self.assertTrue(table["gaps"], reason)


if __name__ == "__main__":
    unittest.main()
