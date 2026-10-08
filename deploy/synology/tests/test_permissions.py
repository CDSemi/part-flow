"""PF-A2.3: semantic permission policy, check/plan/apply, scope floors and the resumable apply.

Contracts and the compiler are pure. Every filesystem case runs on a real filesystem as uid 0 inside the throwaway
test container (named skip elsewhere); unprivileged identities are forked children that switch to numeric ids
(uid 4242/4343 with the image's existing ``users``/``staff`` groups). Nothing is added to /etc/passwd or /etc/group,
and no ACL entry is ever written by the code under test (crafted xattr fixtures only).
"""
import contextlib
import copy
import grp
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

PACKAGE = pfx.PACKAGE
pf = pfx.pf
pf_instance = pfx.pf_instance
pf_bootstrap = pfx.pf_bootstrap
pf_config = pf.pf_config
pf_install = pf.pf_install
CONTRACTS = PACKAGE / "contracts"
EXAMPLES = CONTRACTS / "examples" / "permission-policy"
REAL = os.geteuid() == 0 and os.path.exists("/.dockerenv")
REAL_FS = unittest.skipUnless(REAL, "real-filesystem permission cases run as uid 0 inside the throwaway container")
SCOPES = pf_config.PERMISSION_SCOPES
EDITOR_UID, OTHER_UID = 4242, 4343
APPLY_PHRASE = "APPLY PERMISSIONS staging"
DEFAULTS = [""] * 11            # the wizard's eleven questions, every default kept


def gid_of(name):
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        raise unittest.SkipTest(f"the image has no '{name}' group; groups are never created")


def mode(path):
    return stat.S_IMODE(os.lstat(str(path)).st_mode)


def owner(path):
    info = os.lstat(str(path))
    return info.st_uid, info.st_gid


def policy(**overrides):
    """A valid policy document (the shared example) with per-scope field overrides {scope: {field: value}}."""
    document = json.loads((EXAMPLES / "shared.json").read_text(encoding="utf-8"))
    for scope, fields in overrides.items():
        document["permissions"][scope].update(fields)
    return document


def tree_state(*roots):
    """(mode, uid, gid, ino, ctime) of every entry below and including ``roots``."""
    result = {}
    for root in roots:
        for current, dirs, files in os.walk(str(root)):
            for name in [""] + dirs + files:
                path = Path(current) / name if name else Path(current)
                info = os.lstat(str(path))
                result[str(path)] = (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, info.st_ino,
                                     info.st_ctime_ns)
    return result


def child(action, *, uid=EDITOR_UID, groups=("users",), cwd=None):
    """Run ``action()`` in a forked child as an unprivileged identity; returns its exit status (0 = True)."""
    gids = [gid_of(name) for name in groups]
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            os.setgroups(gids)
            os.setgid(gids[0])
            os.setuid(uid)
            if cwd is not None:
                os.chdir(str(cwd))
            os._exit(0 if action() else 3)
        except BaseException:
            os._exit(4)
    _, status = os.waitpid(pid, 0)
    return os.WEXITSTATUS(status)


def can_open(path, flags=os.O_RDONLY):
    def action():
        try:
            os.close(os.open(str(path), flags))
            return True
        except OSError:
            return False
    return action


@contextlib.contextmanager
def holder(*, cwd=None, open_path=None):
    """A forked process that keeps its working directory (or an open descriptor) inside a scope until released."""
    ready_r, ready_w = os.pipe()
    hold_r, hold_w = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child
        try:
            os.close(ready_r)
            os.close(hold_w)
            fd = None
            if cwd is not None:
                os.chdir(str(cwd))
            if open_path is not None:
                fd = os.open(str(open_path), os.O_RDONLY)
            os.write(ready_w, b"1")
            os.read(hold_r, 1)
            if fd is not None:
                os.close(fd)
        finally:
            os._exit(0)
    os.close(ready_w)
    os.close(hold_r)
    os.read(ready_r, 1)
    try:
        yield pid
    finally:
        os.close(hold_w)
        os.close(ready_r)
        os.waitpid(pid, 0)


def acl_blob(entries):
    return struct.pack("<I", pf_bootstrap.ACL_VERSION) + b"".join(struct.pack("<HHI", *entry) for entry in entries)


ACCESS_ACL = acl_blob(((pf_bootstrap.ACL_USER_OBJ, 6, 0xFFFFFFFF), (pf_bootstrap.ACL_USER, 6, 1000),
                       (pf_bootstrap.ACL_GROUP_OBJ, 6, 0xFFFFFFFF), (pf_bootstrap.ACL_MASK, 6, 0xFFFFFFFF),
                       (pf_bootstrap.ACL_OTHER, 0, 0xFFFFFFFF)))
DEFAULT_ACL = acl_blob(((pf_bootstrap.ACL_USER_OBJ, 7, 0xFFFFFFFF), (pf_bootstrap.ACL_USER, 7, 1000),
                        (pf_bootstrap.ACL_GROUP_OBJ, 7, 0xFFFFFFFF), (pf_bootstrap.ACL_MASK, 7, 0xFFFFFFFF),
                        (pf_bootstrap.ACL_OTHER, 0, 0xFFFFFFFF)))


def set_xattr(path, name, value):
    if not hasattr(os, "setxattr"):
        raise unittest.SkipTest("ACL fixture unavailable: os.setxattr is missing")
    try:
        os.setxattr(str(path), name, value, follow_symlinks=False)
    except OSError as exc:
        raise unittest.SkipTest(f"ACL fixture unavailable: the filesystem refuses {name} ({exc.strerror or exc})")


def xattrs(path):
    return {name: os.getxattr(str(path), name, follow_symlinks=False)
            for name in os.listxattr(str(path), follow_symlinks=False)}


# ============================================================================ PS: contracts


class Contracts(unittest.TestCase):

    def test_ps1_the_r2_schema_and_examples_are_byte_copies(self):
        for path, expected in ((CONTRACTS / "permission-policy.schema.json",
                                "578b31a9014871d6aa030d3adc72eaf6a08b47a71e2a8d51beb0f28dbd01c8d4"),
                               (EXAMPLES / "shared.json",
                                "c4d47e692d73814d8dfc24945a113d77e3be6547377e9b2ac385590ca51eed52"),
                               (EXAMPLES / "restricted.json",
                                "a0b4b43aa4acdfc270ccb34aa0e816907529f3a454865a7e0be2c8354800aebb")):
            with self.subTest(path=path.name):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)

    def test_ps2_embedded_schemas_equal_the_contract_files(self):
        for name, embedded in (("permission-policy.schema.json", pf_config.PERMISSION_POLICY_SCHEMA),
                               ("permission-approval.schema.json", pf_config.PERMISSION_APPROVAL_SCHEMA),
                               ("permission-apply.schema.json", pf_config.PERMISSION_APPLY_SCHEMA)):
            with self.subTest(name=name):
                self.assertEqual(json.loads((CONTRACTS / name).read_text(encoding="utf-8")), embedded)

    def test_ps3_inlining_removes_exactly_the_allof_every_ref_and_the_top_level_defs(self):
        subset = pf_config.PERMISSION_POLICY_SUBSET
        expected = copy.deepcopy(pf_config.PERMISSION_POLICY_SCHEMA)
        group = expected.pop("$defs")["groupName"]
        workspace = expected["properties"]["permissions"]["properties"]["workspace"]
        self.assertEqual(len(workspace.pop("allOf")), 1)
        for item in expected["properties"]["permissions"]["properties"].values():
            if "group" in item["properties"]:
                self.assertEqual(item["properties"]["group"], {"$ref": "#/$defs/groupName"})
                item["properties"]["group"] = group
        self.assertEqual(subset, expected)
        text = json.dumps(subset)
        for removed in ("$ref", "allOf", "$defs", '"if"', '"then"'):
            self.assertNotIn(removed, text)
        document = json.loads((EXAMPLES / "shared.json").read_text(encoding="utf-8"))
        self.assertEqual(pf_instance.validate_against_schema(document, subset), [])
        with self.assertRaises(pf_instance.ContextError):  # the raw r2 schema is outside the A1 subset
            pf_instance.validate_against_schema(document, pf_config.PERMISSION_POLICY_SCHEMA)

    def test_ps4_every_group_pattern_is_anchored_and_matches_the_examples(self):
        pattern = pf_config.PERMISSION_POLICY_SCHEMA["$defs"]["groupName"]["pattern"]
        for name in ("shared.json", "restricted.json", "missing-group.json", "unsupported-workspace-no-exec.json"):
            document = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
            for scope, item in document["permissions"].items():
                if "group" in item:
                    with self.subTest(name=name, scope=scope):
                        self.assertTrue(pf_instance._anchored_fullmatch(pattern, item["group"]))
        document = json.loads((EXAMPLES / "invalid-group-name.json").read_text(encoding="utf-8"))
        for scope in ("workspace", "configuration"):
            self.assertFalse(pf_instance._anchored_fullmatch(pattern, document["permissions"][scope]["group"]))

    def test_ps5_cases_cover_every_example_and_hold(self):
        cases = json.loads((EXAMPLES / "cases.json").read_text(encoding="utf-8"))
        files = sorted(path.name for path in EXAMPLES.glob("*.json") if path.name != "cases.json")
        self.assertEqual(sorted(case["file"] for case in cases), files)
        for case in cases:
            with self.subTest(file=case["file"]):
                document, problems = pf_config.parse_permission_policy((EXAMPLES / case["file"]).read_bytes(),
                                                                       label=case["file"])
                expect = case["expect"]
                self.assertEqual(not problems, expect["valid"])
                if not expect["valid"]:
                    self.assertEqual(expect["code"], "permission-policy-invalid")
                    self.assertIn(expect["problem"], problems[0])
                    continue
                compiled = pf_config.compile_permission_policy(document)
                self.assertEqual({scope: {"group": target.group, "access": target.access,
                                          "dir": f"{target.dir_mode:04o}", "file": f"{target.file_mode:04o}",
                                          "exec": None if target.exec_mode is None else f"{target.exec_mode:04o}",
                                          "gid_rule": target.gid_rule, "owner_rule": target.owner_rule,
                                          "apply_rule": target.apply_rule}
                                  for scope, target in compiled.items()}, expect["targets"])
                unsupported = pf_config.permission_policy_unsupported(document)
                self.assertEqual(bool(unsupported), expect["code"] == "permission-policy-unsupported")

    def test_ps6_every_defs_entry_validates_through_validate_marked_without_schema_errors(self):
        apply_defs = pf_config.PERMISSION_APPLY_SCHEMA["$defs"]
        approval_defs = pf_config.PERMISSION_APPROVAL_SCHEMA["$defs"]
        op = "20261007T000000Z-permissions-apply-0123abcd"
        sha = "a" * 64
        status = {"scope": "workspace", "mode_applied": "yes", "effective_access_verified": "not verified",
                  "future_file_behavior_verified": "not verified", "notes": ["x"]}
        root = {"scope": "workspace", "dev": 1, "ino": 2, "gid": 100, "fenced": True}
        plan = {"schema_version": 1, "operation_id": op, "instance_id": "0" * 8 + "-0000-0000-0000-" + "0" * 12,
                "created": "20261007T000000Z", "base_revision": 0, "base_sha256": None, "policy_sha256": sha,
                "policy": {}, "scopes": ["workspace"], "freeze_scopes": [], "roots": [root], "change_count": 0,
                "plan_sha256": sha}
        effect = {"seq": 1, "kind": "entry", "scope": "backups", "path": "a/b", "dev": 1, "ino": 2, "type": "file",
                  "before_mode": 0o600, "after_mode": 0o640, "before_gid": 0, "after_gid": 100, "operation_id": op}
        outcome = {"schema_version": 1, "operation_id": op, "apply_operation_id": op, "completed": "20261007T000000Z",
                   "action": "apply", "result": "completed", "plan_sha256": None, "changed": 0, "unplanned": 0,
                   "conflicts": 0, "approved_revision": None, "scopes": [status]}
        record = {"schema_version": 1, "instance_id": plan["instance_id"], "revision": 1,
                  "approved": "20261007T000000Z", "operation_id": op, "policy_sha256": sha, "previous_sha256": None,
                  "confirmed_plans": [{"operation_id": op, "plan_sha256": sha}], "policy": {}}
        valid = [(apply_defs, "revision", 1), (apply_defs, "scope", "recovery"), (apply_defs, "plan_root", root),
                 (apply_defs, "plan", plan), (apply_defs, "plan", dict(plan, base_sha256=sha, base_revision=2)),
                 (apply_defs, "changes_header", {"schema_version": 1, "policy_sha256": sha, "scopes": ["control"],
                                                 "change_count": 0}),
                 (apply_defs, "effect", effect), (apply_defs, "outcome", outcome),
                 (apply_defs, "outcome", dict(outcome, plan_sha256=sha, approved_revision=3)),
                 (apply_defs, "scope_status", status), (approval_defs, "record", record),
                 (approval_defs, "record", dict(record, previous_sha256=sha, revision=2)),
                 (approval_defs, "confirmed_plan", {"operation_id": op, "plan_sha256": sha})]
        invalid = [(apply_defs, "scope", "elsewhere"), (apply_defs, "plan", dict(plan, scopes=["elsewhere"])),
                   (apply_defs, "plan", dict(plan, base_sha256="x")), (apply_defs, "plan", dict(plan, roots=[{}])),
                   (apply_defs, "outcome", dict(outcome, approved_revision=0)),
                   (apply_defs, "outcome", dict(outcome, scopes=[dict(status, effective_access_verified="yes")])),
                   (apply_defs, "effect", dict(effect, scope="elsewhere")), (apply_defs, "effect", dict(effect, seq=0)),
                   (approval_defs, "record", dict(record, previous_sha256="nope")),
                   (approval_defs, "record", dict(record, confirmed_plans=[{"operation_id": "x", "plan_sha256": sha}])),
                   (approval_defs, "record", dict(record, operation_id="20261007T000000Z-config-0123abcd"))]
        for defs, name, value in valid:
            with self.subTest(valid=name):
                self.assertEqual(pf_install.validate_marked(value, defs[name], defs=defs), [])
        for defs, name, value in invalid:
            with self.subTest(invalid=name):
                self.assertNotEqual(pf_install.validate_marked(value, defs[name], defs=defs), [])
        self.assertEqual(pf_config.effect_problems(effect), [])
        for bad in ("/abs", "a/../b", "a//b", "./a"):
            self.assertNotEqual(pf_config.effect_problems(dict(effect, path=bad)), [])


# ============================================================================ CP: compiler


class Compiler(unittest.TestCase):

    def test_cp1_every_mode_table_row(self):
        table = {"none": (0o600, 0o700, 0o2700), "read_only": (0o640, 0o750, 0o2750),
                 "read_write": (0o660, 0o770, 0o2770)}
        for access, (file_mode, plain, inherited) in table.items():
            for inherit in (False, True):
                with self.subTest(access=access, inherit=inherit):
                    target = pf_config.compile_permission_policy(policy(configuration={
                        "access": access, "inherit_group": inherit}))["configuration"]
                    self.assertEqual((target.file_mode, target.dir_mode),
                                     (file_mode, inherited if inherit else plain))

    def test_cp2_every_executable_combination(self):
        bits = {"none": 0, "owner_only": 0o100, "owner_and_group": 0o110}
        files = {"none": 0o600, "read_only": 0o640, "read_write": 0o660}
        for access in files:
            for executables in bits:
                document = policy(workspace={"access": access, "executables": executables})
                with self.subTest(access=access, executables=executables):
                    problems = pf_config.permission_policy_problems(document)
                    if access == "none" and executables == "owner_and_group":
                        self.assertIn("group execution needs group access", problems[0])
                        with self.assertRaises(pf_config.ConfigError):
                            pf_config.compile_permission_policy(document)
                        continue
                    self.assertEqual(problems, [])
                    target = pf_config.compile_permission_policy(document)["workspace"]
                    self.assertEqual(target.exec_mode, files[access] | bits[executables])
        self.assertEqual(pf_config.compile_permission_policy(policy(workspace={
            "access": "read_only", "executables": "owner_only"}))["workspace"].exec_mode, 0o740)

    def test_cp3_to_cp5_each_scope_floor_rejects_its_prohibited_options(self):
        rejected = (("control", {"access": "read_write"}), ("control", {"executables": "owner_and_group"}),
                    ("backups", {"access": "read_write"}), ("recovery", {"access": "read_write"}),
                    ("configuration", {"executables": "owner_only"}), ("private_state", {"group": "users"}),
                    ("private_state", {"access": "none"}), ("workspace", {"inherit_group": "yes"}))
        for scope, fields in rejected:
            with self.subTest(scope=scope, fields=fields):
                self.assertNotEqual(pf_config.permission_policy_problems(policy(**{scope: fields})), [])
        for scope, fields in (("control", {"access": "none"}), ("backups", {"access": "none"}),
                              ("recovery", {"access": "none"})):
            with self.subTest(allowed=scope):
                self.assertEqual(pf_config.permission_policy_problems(policy(**{scope: fields})), [])

    def test_cp6_unknown_keys_including_a_numeric_mode_override(self):
        for scope, fields in (("workspace", {"mode": "0770"}), ("backups", {"file_mode": 416}),
                              ("control", {"umask": "007"})):
            with self.subTest(scope=scope):
                problems = pf_config.permission_policy_problems(policy(**{scope: fields}))
                self.assertIn("unknown keys", problems[0])
        document = policy()
        document["mode_override"] = 0o777
        self.assertIn("unknown keys mode_override", pf_config.permission_policy_problems(document)[0])

    def test_cp7_duplicate_key_and_non_json_are_refused_by_the_parser(self):
        document, problems = pf_config.parse_permission_policy((EXAMPLES / "invalid-duplicate-key.json").read_bytes(),
                                                               label="dup")
        self.assertIsNone(document)
        self.assertIn("Duplicate JSON object key: access", problems[0])
        for data in (b"{", b'{"policy_version": NaN}', b"\xff"):
            with self.subTest(data=data):
                self.assertIsNone(pf_config.parse_permission_policy(data, label="x")[0])

    def test_cp8_policy_version_must_be_the_integer_1(self):
        for value in (0, 2, "1", True, 1.0):
            document = policy()
            document["policy_version"] = value
            with self.subTest(value=value):
                if value == 1.0 and not isinstance(value, bool):
                    # JSON 1.0 parses as a float; the const comparison accepts the numeric value 1.
                    continue
                self.assertNotEqual(pf_config.permission_policy_problems(document), [])

    def test_cp9_group_name_patterns(self):
        for bad in (" users", "users ", "a/b", "a:b", "", "x" * 129, "tab\there", "\x7f"):
            with self.subTest(bad=bad):
                self.assertNotEqual(pf_config.permission_policy_problems(policy(workspace={"group": bad})), [])
        for good in ("users", "nas-editors", "Domain Users", "x" * 128):
            with self.subTest(good=good):
                self.assertEqual(pf_config.permission_policy_problems(policy(workspace={"group": good})), [])

    def test_cp10_compile_is_deterministic(self):
        document = json.loads((EXAMPLES / "restricted.json").read_text(encoding="utf-8"))
        self.assertEqual(pf_config.compile_permission_policy(document), pf_config.compile_permission_policy(
            json.loads(json.dumps(document))))
        header = {"schema_version": 1, "policy_sha256": "b" * 64, "scopes": ["backups"], "change_count": 1}
        change = ["backups", "x", 1, 2, "file", 0o600, 0, 0o640, 100]
        self.assertEqual(pf_config.changes_bytes(header, [change]), pf_config.changes_bytes(dict(header), [list(change)]))

    def test_cp11_symbolic_mode_table(self):
        for value, kind, text in ((0o660, "file", "u=rw,g=rw,o="), (0o2770, "dir", "u=rwx,g=rwx,o=, setgid"),
                                  (0o640, "file", "u=rw,g=r,o="), (0o750, "dir", "u=rwx,g=rx,o="),
                                  (0o600, "file", "u=rw,g=,o="), (0o4755, "file", "u=rwx,g=rx,o=rx, setuid"),
                                  (0o1777, "dir", "u=rwx,g=rwx,o=rwx, sticky")):
            with self.subTest(mode=oct(value)):
                self.assertEqual(pf_config.symbolic_mode(value, kind), text)

    def test_cp12_derived_policy_equals_the_a1_modes_and_the_control_ceiling(self):
        derived = pf_config.derive_permission_policy({"workspace_write_group": "users"}, backups_group="root",
                                                     recovery_group="staff")
        compiled = pf_config.compile_permission_policy(derived)
        self.assertEqual((compiled["workspace"].dir_mode, compiled["workspace"].file_mode,
                          compiled["workspace"].exec_mode), (0o2770, 0o660, 0o770))
        self.assertEqual((compiled["configuration"].dir_mode, compiled["configuration"].file_mode), (0o2770, 0o660))
        self.assertEqual((compiled["backups"].group, compiled["backups"].dir_mode, compiled["backups"].file_mode),
                         ("root", 0o750, 0o640))
        self.assertEqual(compiled["recovery"].group, "staff")
        self.assertEqual((compiled["control"].access, compiled["control"].apply_rule, compiled["control"].gid_rule),
                         ("none", "ceiling", "unmanaged"))
        self.assertEqual((compiled["private_state"].dir_mode, compiled["private_state"].file_mode), (0o700, 0o600))
        control = compiled["control"]
        self.assertEqual(pf_config.ceiling_violations(control, mode=0o600, uid=0, gid=5, kind="file", policy_gid=100),
                         [])
        self.assertEqual(pf_config.ceiling_violations(control, mode=0o700, uid=0, gid=5, kind="dir", policy_gid=100),
                         [])
        self.assertEqual(pf_config.ceiling_violations(control, mode=0o700, uid=0, gid=5, kind="file", policy_gid=100),
                         [])  # an owner execute bit is accepted
        for bad_mode, uid, kind in ((0o640, 0, "file"), (0o660, 0, "file"), (0o604, 0, "file"), (0o4700, 0, "file"),
                                    (0o610, 0, "file"), (0o750, 0, "dir"), (0o600, 1000, "file")):
            with self.subTest(mode=oct(bad_mode), uid=uid):
                self.assertNotEqual(pf_config.ceiling_violations(control, mode=bad_mode, uid=uid, gid=100, kind=kind,
                                                                 policy_gid=100), [])
        readable = pf_config.compile_permission_policy(policy(control={"access": "read_only"}))["control"]
        self.assertEqual(pf_config.ceiling_violations(readable, mode=0o640, uid=0, gid=100, kind="file",
                                                      policy_gid=100), [])
        self.assertEqual(pf_config.ceiling_violations(readable, mode=0o750, uid=0, gid=100, kind="dir",
                                                      policy_gid=100), [])
        self.assertNotEqual(pf_config.ceiling_violations(readable, mode=0o640, uid=0, gid=7, kind="file",
                                                         policy_gid=100), [])

    def test_cp13_workspace_none_compiles_but_is_unsupported_and_change_lines_are_checked(self):
        document = json.loads((EXAMPLES / "unsupported-workspace-no-exec.json").read_text(encoding="utf-8"))
        target = pf_config.compile_permission_policy(document)["workspace"]
        self.assertEqual(target.exec_mode, target.file_mode)
        self.assertIn("workspace.executables = none", pf_config.permission_policy_unsupported(document)[0])
        self.assertEqual(pf_config.permission_policy_unsupported(policy()), [])
        good = ["workspace", "a/b", 1, 2, "file", 0o600, 0, 0o660, 100]
        self.assertEqual(pf_config.change_problems(good), [])
        for bad in (good[:8], ["elsewhere"] + good[1:], good[:1] + ["../x"] + good[2:], good[:4] + ["link"] + good[5:],
                    good[:5] + [-1] + good[6:], good[:7] + [0o17777] + good[8:], good[:2] + [True] + good[3:]):
            with self.subTest(bad=bad):
                self.assertNotEqual(pf_config.change_problems(bad), [])
        with self.assertRaises(pf_config.ConfigError):
            pf_config.changes_bytes({"schema_version": 1, "policy_sha256": "b" * 64, "scopes": ["backups"],
                                     "change_count": 2}, [good])


# ============================================================================ shared real-filesystem fixture


class Instance(unittest.TestCase):
    """A protected installation with one registered instance ``staging``. The workspace and configuration group is
    ``users``; backups and recovery roots carry gid 0 (root), as data_home creates them."""

    group = "users"

    def setUp(self):
        if not REAL:
            raise unittest.SkipTest("real-filesystem permission cases run as uid 0 inside the throwaway container")
        gid_of(self.group)
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.layout = pfx.install_root(self.base)
        self.paths = self.make_paths()
        self.context = pfx.register(self.layout, "staging", self.paths, project="partflow-staging")
        self.workspace = self.paths["workspace"]
        self.config_dir = self.paths["configuration"]
        self.backups = self.paths["backups"]
        self.recovery = self.paths["recovery"]
        self.private = self.context.paths.private_state
        self.record_path = self.private / "permission-policy.json"
        os.chmod(self.base / "home", 0o755)

    def make_paths(self):
        return pfx.data_home(self.base / "home", group=self.group)

    def tearDown(self):
        for current, dirs, files in os.walk(str(self.base)):
            for name in dirs:
                path = Path(current) / name
                if not path.is_symlink():
                    with contextlib.suppress(OSError):
                        os.chmod(str(path), 0o700)
        self.temp.cleanup()

    def run_cli(self, *arguments, answers=(), controller_class=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(pf, "unattended", return_value=False),
                   mock.patch("builtins.input", side_effect=list(answers) or EOFError)]
        if controller_class is not None:
            patches.append(mock.patch.object(pf, "Controller", controller_class))
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            for patch in patches:
                stack.enter_context(patch)
            code = pf.main(["--instance", "staging", *arguments], installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        return code, stdout.getvalue(), stderr.getvalue()

    def apply(self, *extra, answers=None, phrase=APPLY_PHRASE, controller_class=None):
        answers = list(DEFAULTS if answers is None else answers) + ([phrase] if phrase else [])
        return self.run_cli("permissions", "apply", *extra, answers=answers, controller_class=controller_class)

    def controller(self):
        return pf.Controller(self.context)

    def record(self):
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def operations(self):
        """Operation directories in creation order (operation.json is written once, when the operation begins)."""
        root = self.context.operations_dir
        return sorted(os.listdir(str(root)), key=lambda name: (os.lstat(str(root / name / "operation.json")).st_mtime_ns
                                                               if (root / name / "operation.json").exists() else 0,
                                                               name))

    def outcome(self, operation_id):
        return json.loads((self.context.operations_dir / operation_id / "permission-apply.json").read_text())

    def last_apply(self):
        return [name for name in self.operations() if "-permissions-apply-" in name][-1]

    def effects(self, operation_id):
        path = self.context.operations_dir / operation_id / "permission-effects.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def pending(self):
        path = self.context.journal_path
        return json.loads(path.read_text()) if path.exists() else None

    def manifest(self):
        """A protected source manifest of the current workspace (unknown provenance) for executable designation."""
        self.controller().record_source_manifest(self.workspace, None, verified=False)

    def seamed(self, *, fault=None, hook=None):
        class Seamed(pf.Controller):
            def __init__(self, context, **kwargs):
                super().__init__(context, **kwargs)
                self.permission_fault = fault
                self.permission_hook = hook
        return Seamed

    def scramble(self):
        """Differing initial modes and gids in the editable and protected scopes."""
        for path in (self.workspace / "frontend", self.workspace / "backend"):
            os.chmod(path, 0o755)
        os.chmod(self.workspace / "frontend/app.txt", 0o644)
        os.chown(self.workspace / "app-version.txt", -1, 0)
        os.chmod(self.config_dir / "pf-config.json", 0o600)
        folder = self.backups / "revisions" / "partflow-staging" / "20260909T120000Z-aaaaaaaaaaaa-abcdef"
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text("{}\n")
        os.chmod(folder, 0o700)
        os.chmod(folder / "manifest.json", 0o600)
        return folder


# ============================================================================ AR: approval


class Approval(Instance):

    def test_ar1_record_validation_rules_and_the_confirmed_plan_hash_is_the_changes_file(self):
        code, out, err = self.apply()
        self.assertEqual(code, 0, out + err)
        record, data = self.record(), self.record_path.read_bytes()
        instance = self.context.instance_id
        self.assertEqual(pf_config.permission_approval_problems(record, instance_id=instance), [])
        operation = record["confirmed_plans"][0]["operation_id"]
        changes = (self.context.operations_dir / operation / "permission-changes.jsonl").read_bytes()
        self.assertEqual(record["confirmed_plans"][0]["plan_sha256"], hashlib.sha256(changes).hexdigest())
        self.assertNotEqual(hashlib.sha256(changes + b"\n").hexdigest(), record["confirmed_plans"][0]["plan_sha256"])
        cases = ((dict(record, previous_sha256="a" * 64), "revision 1 has a null previous_sha256"),
                 (dict(record, revision=2), "names the sha256 of the previous record"),
                 (dict(record, instance_id="1" * 8 + "-1111-1111-1111-" + "1" * 12), "is not the selected instance"),
                 (dict(record, policy_sha256="c" * 64), "policy_sha256 is not the sha256"),
                 (dict(record, confirmed_plans=[]), "confirmed_plans is empty"),
                 (dict(record, policy=policy(workspace={"executables": "none"}),
                       policy_sha256=pf_instance.sha256_bytes(pf_instance.normalize_json(
                           policy(workspace={"executables": "none"})))), "not supported by this control"))
        for value, message in cases:
            with self.subTest(message=message):
                self.assertIn(message, " ".join(pf_config.permission_approval_problems(value, instance_id=instance)))
        later = dict(record, revision=2, previous_sha256=hashlib.sha256(b"other").hexdigest())
        self.assertIn("previous record bytes", " ".join(pf_config.permission_approval_problems(
            later, instance_id=instance, previous_bytes=data)))
        defs = pf_config.PERMISSION_APPROVAL_SCHEMA["$defs"]
        self.assertNotEqual(pf_install.validate_marked(dict(record, previous_sha256="x"), defs["record"], defs=defs), [])

    def test_ar2_an_invalid_record_refuses_with_no_derived_fallback(self):
        self.assertEqual(self.apply()[0], 0)
        cases = (("mode", lambda: os.chmod(self.record_path, 0o644)),
                 ("bytes", lambda: self.record_path.write_bytes(b'{"schema_version": 1}')),
                 ("owner", lambda: os.chown(self.record_path, EDITOR_UID, -1)))
        original = self.record_path.read_bytes()
        for label, damage in cases:
            with self.subTest(case=label):
                self.record_path.write_bytes(original)
                os.chmod(self.record_path, 0o600)
                os.chown(self.record_path, 0, 0)
                damage()
                code, out, err = self.run_cli("permissions", "check")
                self.assertEqual(code, 1)
                self.assertIn("ERROR: permission-approval-invalid: The approved permission policy record "
                              f"{self.record_path} cannot be used", err)
                self.assertIn("no derived policy is used. See SYNOLOGY_ADMIN §16.", err)
                controller = self.controller()
                with self.assertRaisesRegex(pf.Failure, "permission-approval-invalid"):
                    controller.ensure_backup_tree()
                code, out, err = self.run_cli("permissions", "plan")
                self.assertEqual(code, 1)
                self.assertIn("permission-approval-invalid", err)

    def test_ar3_ar4_first_apply_writes_revision_1_and_an_unchanged_reapply_is_current(self):
        code, out, err = self.apply()
        self.assertEqual(code, 0, out + err)
        self.assertIn("permissions-applied: Permission policy revision 1 applied.", out)
        record = self.record()
        operation = self.last_apply()
        self.assertEqual((record["revision"], record["previous_sha256"], record["operation_id"]), (1, None, operation))
        self.assertEqual([item["operation_id"] for item in record["confirmed_plans"]], [operation])
        self.assertEqual((self.context.operations_dir / operation / "permission-approval.json").read_bytes(),
                         self.record_path.read_bytes())
        self.assertEqual(mode(self.record_path), 0o600)
        self.assertEqual(self.outcome(operation)["result"], "completed")
        self.assertIsNone(self.pending())
        before = (self.record_path.read_bytes(), os.lstat(str(self.record_path)).st_ino)
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 0, out + err)
        self.assertIn("permissions-current: Permissions already match permission policy revision 1; nothing to change.",
                      out)
        self.assertEqual((self.record_path.read_bytes(), os.lstat(str(self.record_path)).st_ino), before)
        latest = self.last_apply()
        self.assertEqual(sorted(os.listdir(str(self.context.operations_dir / latest))), ["operation.json"])

    def test_ar5_a_changed_group_writes_revision_2_chained_to_revision_1(self):
        gid_of("staff")
        self.assertEqual(self.apply()[0], 0)
        first = self.record_path.read_bytes()
        answers = list(DEFAULTS)
        answers[7] = "staff"            # backups group
        code, out, err = self.apply(answers=answers)
        self.assertEqual(code, 0, out + err)
        record = self.record()
        self.assertEqual((record["revision"], record["previous_sha256"]), (2, hashlib.sha256(first).hexdigest()))
        self.assertEqual(record["policy"]["permissions"]["backups"]["group"], "staff")
        self.assertIn('backups.group: "root" -> "staff"', out)
        self.assertEqual(owner(self.backups)[1], gid_of("staff"))

    def publish_folder(self, controller, name):
        """What snapshot does with the permission policy: the tree, then a fresh folder with explicit targets."""
        with controller.lock():
            controller.ensure_backup_tree()
            folder = controller.backups_dir / name
            folder.mkdir(mode=0o700)
            (folder / "manifest.json").write_text("{}\n")
            controller.publish_fresh("backups", folder)
        return folder

    def test_ar6_after_approval_an_edited_backup_group_is_only_a_proposal(self):
        gid_of("staff")
        self.assertEqual(self.apply()[0], 0)
        pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging", backup_read_group="staff",
                         workspace_write_group="users")
        folder = self.publish_folder(self.controller(), "20261007T000000Z-aaaaaaaaaaaa-000001")
        self.assertEqual((mode(folder), owner(folder)[1]), (0o750, 0))
        self.assertEqual((mode(folder / "manifest.json"), owner(folder / "manifest.json")[1]), (0o640, 0))
        code, out, err = self.run_cli("permissions", "check")
        self.assertIn("permission-group-proposal: pf-config.json proposes group staff for backups; permission policy "
                      "revision 1 uses root until", out)
        code, out, err = self.run_cli("doctor")
        self.assertIn("| proposals: backups root -> staff, recovery root -> staff", out)

    def test_ar7_unapproved_instances_take_the_backup_group_from_the_protected_root(self):
        gid_of("staff")
        pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging", backup_read_group="staff",
                         workspace_write_group="users")
        folder = self.publish_folder(self.controller(), "20261007T000000Z-aaaaaaaaaaaa-000002")
        self.assertEqual(owner(folder)[1], 0)          # the root's gid, never the editable proposal
        self.assertFalse(self.record_path.exists())
        code, out, err = self.run_cli("permissions", "check")
        self.assertIn("permission-group-proposal: pf-config.json proposes group staff for backups; the unapproved "
                      "derived policy uses root until", out)
        self.assertIn("permission-policy-unapproved: Permission policy not approved yet", out)
        os.chown(self.backups, -1, 54321)
        code, out, err = self.run_cli("permissions", "check")
        self.assertEqual(code, 1)
        self.assertIn(f"ERROR: permission-group-missing: Group gid 54321 on {self.backups} has no group name", err)
        with self.assertRaisesRegex(pf.Failure, "permission-group-missing"):
            self.controller().permission_targets()

    def test_ar8_admin_wizard_summary_states_the_proposals(self):
        code, out, err = self.run_cli("config", "admin", answers=["n"])
        self.assertIn("backup_read_group is a proposal: backups and recovery bundles keep the group of their folders "
                      "until", out)
        self.assertIn("workspace_write_group applies to files pf creates in the workspace and configuration until the "
                      "first", out)
        self.assertEqual(self.apply()[0], 0)
        code, out, err = self.run_cli("config", "admin", answers=["n"])
        self.assertIn("backup_read_group and workspace_write_group are proposals: permission policy revision 1 stays in "
                      "force until", out)
        self.assertNotIn("without a separate approval", out)

    def test_ar9_purge_keeps_the_approval_record_and_the_redeployed_instance_uses_it(self):
        gid_of("staff")
        answers = list(DEFAULTS)
        answers[7] = "staff"
        self.assertEqual(self.apply(answers=answers)[0], 0)
        before = self.record_path.read_bytes()
        for reset in (False, True):
            with self.subTest(reset_admin_config=reset):
                controller = self.controller()
                controller.config["minimum_free_mb"] = 1
                with contextlib.redirect_stdout(io.StringIO()), \
                        mock.patch.object(controller, "execute_deletion_plan"):
                    controller.finish_purge_cleanup("purge-x", {}, delete_backups=False, reset_admin_config=reset)
                self.assertFalse(controller.state.exists())
                self.assertEqual(self.record_path.read_bytes(), before)
                if reset:
                    pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging",
                                     backup_read_group="users", workspace_write_group="users")
                os.chown(self.backups, -1, 0)
                folder = self.publish_folder(self.controller(), f"20261007T00000{int(reset)}Z-aaaaaaaaaaaa-00000{int(reset)}")
                self.assertEqual(owner(folder)[1], gid_of("staff"))

    def test_ar10_fl9_exact_restore_keeps_the_record_and_restores_private_state_files(self):
        self.assertEqual(self.apply()[0], 0)
        record = (self.record_path.read_bytes(), os.lstat(str(self.record_path)).st_ino)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_pf_admin
        controller = test_pf_admin.FakeController(self.context)
        tree = self.base / "tree"
        pfx.source_fixture(tree, pfx.OLD)
        state = json.dumps({"sha": pfx.OLD}).encode()
        # PF-A3.1: a complete legacy bundle of this instance, strictly read; the pointer is written fresh by the seal
        # path (its record is simulated here) and the bundle's state files are restored as before.
        folder = pfx.legacy_purge_bundle(
            controller.recovery_root / ("purge-20260910T120000Z-" + pfx.OLD[:12] + "-abcdef"), project="partflow-staging",
            root=self.workspace, tree=tree, files={"state/last-reset.json": state, "state/deployed.json": state},
            extra={"state_files": ["last-reset.json", "deployed.json"]})
        for name in ("last-reset.json", "deployed.json"):
            os.chmod(folder / "state" / name, 0o640)
        recovery = controller.verify_recovery(folder)
        if (controller.state / "deployed.json").exists():
            (controller.state / "deployed.json").unlink()
        sealed = {"deployment_id": "dep-20261007T000000Z-0a1b2c3d", "record_sha256": "0" * 64}
        with contextlib.ExitStack() as stack:
            for name, value in (("require_empty_target", None), ("docker", ""),
                                ("verify_images", None), ("make_override", None), ("compose", ""),
                                ("wait_health", None), ("drop_database", None), ("restore_into", None),
                                ("db_heads", ["r1"]), ("restore_revision_checkpoints", ("absent in the bundle", [])),
                                ("activate_backend", None), ("activate_frontend", None),
                                ("stage_deployment", types.SimpleNamespace(deployment_id=sealed["deployment_id"],
                                                                             kind="restore-instance")),
                                ("seal_deployment", sealed), ("act_seal", None),
                                # PF-A3.2: the pointer effect runs for real from the simulated staged/sealed records.
                                ("staged_record", {"deployment_id": sealed["deployment_id"],
                                                   "staged": {"pointer": {"sha": pfx.OLD}}}),
                                ("sealed_view", ({}, sealed["record_sha256"]))):
                stack.enter_context(mock.patch.object(controller, name, return_value=value))
            stack.enter_context(mock.patch.object(pf, "confirm"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(controller.lock())
            # PF-A3.2: the staged source is simulated, so the workspace is kept (no generation switch).
            controller.restore_instance(recovery, keep_workspace=True)
        self.assertEqual((self.record_path.read_bytes(), os.lstat(str(self.record_path)).st_ino), record)
        for name in ("last-reset.json", "deployed.json"):
            path = controller.state / name
            self.assertEqual((mode(path), owner(path)[0]), (0o600, 0), name)
        env = self.config_dir / ".env"
        self.assertEqual((mode(env), owner(env)[1]), (0o660, gid_of("users")))

    def test_ar11_three_approvals_form_a_chain_walkable_from_the_operation_directories(self):
        for group in ("staff", "users", "root"):
            gid_of(group)
        for group in ("staff", "users", "root"):
            answers = list(DEFAULTS)
            answers[7] = group
            self.assertEqual(self.apply(answers=answers)[0], 0)
        copies = {}
        for name in self.operations():
            path = self.context.operations_dir / name / "permission-approval.json"
            if path.exists():
                copies[hashlib.sha256(path.read_bytes()).hexdigest()] = path.read_bytes()
        current = self.record_path.read_bytes()
        revisions = []
        while True:
            record = json.loads(current)
            revisions.append(record["revision"])
            changes = (self.context.operations_dir / record["confirmed_plans"][0]["operation_id"]
                       / "permission-changes.jsonl").read_bytes()
            self.assertEqual(hashlib.sha256(changes).hexdigest(), record["confirmed_plans"][0]["plan_sha256"])
            if record["previous_sha256"] is None:
                break
            previous = copies[record["previous_sha256"]]
            self.assertEqual(pf_config.permission_approval_problems(record, instance_id=self.context.instance_id,
                                                                    previous_bytes=previous), [])
            current = previous
        self.assertEqual(revisions, [3, 2, 1])

    def test_ar12_the_manual_route_for_an_invalid_record(self):
        gid_of("staff")
        answers = list(DEFAULTS)
        answers[7] = "staff"
        self.assertEqual(self.apply(answers=answers)[0], 0)
        self.record_path.write_bytes(b"{}")
        self.assertEqual(self.run_cli("permissions", "check")[0], 1)
        os.rename(self.record_path, self.private / "permission-policy.invalid-20261007T000000Z.json")
        os.chown(self.backups, -1, 0)
        folder = self.publish_folder(self.controller(), "20261007T000000Z-aaaaaaaaaaaa-000003")
        self.assertEqual(owner(folder)[1], 0)          # derived: the root's gid, no widening
        code, out, err = self.apply()
        self.assertEqual(code, 0, out + err)
        record = self.record()
        self.assertEqual((record["revision"], record["previous_sha256"]), (1, None))


# ============================================================================ FS: inventory and the engine (real fs)


class Inventory(Instance):

    def inventory(self, scope, root, **kwargs):
        kwargs.setdefault("owner_rule", "trusted" if scope in ("backups", "recovery", "private_state", "control")
                          else "preserve")
        kwargs.setdefault("limit", pf.pf_source.MANIFEST_ENTRY_LIMIT)
        return pf_instance.inventory_scope(scope, root, **kwargs)

    def test_fs1_differing_initial_modes_reach_the_exact_targets_of_every_scope_rule(self):
        script = self.workspace / "tools/run.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\n")
        os.chmod(script, 0o755)
        self.manifest()
        folder = self.scramble()
        state_file = self.context.state_dir / "observed-tags.json"
        state_file.write_text("{}")
        os.chmod(state_file, 0o640)
        users = gid_of("users")
        code, out, err = self.apply()
        self.assertEqual(code, 0, out + err)
        for path in (self.workspace, self.workspace / "frontend", self.workspace / "tools"):
            self.assertEqual((mode(path), owner(path)), (0o2770, (0, users)), path)
        self.assertEqual((mode(self.workspace / "frontend/app.txt"), owner(self.workspace / "frontend/app.txt")[1]),
                         (0o660, users))
        self.assertEqual(mode(script), 0o770)
        self.assertEqual(mode(self.workspace / "app-version.txt"), 0o660)
        self.assertEqual((mode(self.config_dir), mode(self.config_dir / "pf-config.json")), (0o2770, 0o660))
        self.assertEqual((mode(folder), mode(folder / "manifest.json"), owner(folder)[1]), (0o750, 0o640, 0))
        self.assertEqual(mode(state_file), 0o600)
        self.assertEqual(mode(self.layout.release_dir), 0o700)   # control: checked, never changed
        outcome = self.outcome(self.last_apply())
        self.assertEqual({item["scope"]: item["mode_applied"] for item in outcome["scopes"]},
                         {"workspace": "yes", "configuration": "yes", "control": "check-only", "backups": "yes",
                          "recovery": "yes", "private_state": "yes"})
        for item in outcome["scopes"]:
            self.assertEqual((item["effective_access_verified"], item["future_file_behavior_verified"]),
                             ("not verified", "not verified"))

    def test_fs2_a_second_apply_changes_no_ctime(self):
        self.scramble()
        self.assertEqual(self.apply()[0], 0)
        editable = tree_state(self.workspace, self.config_dir, self.backups, self.recovery)
        private = {path: value[:4] for path, value in tree_state(self.private).items()
                   if "/operations" not in path and not path.endswith("/state/pending.json")}
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 0, out + err)
        self.assertIn("permissions-current", out)
        self.assertEqual(tree_state(self.workspace, self.config_dir, self.backups, self.recovery), editable)
        self.assertEqual({path: value[:4] for path, value in tree_state(self.private).items()
                          if "/operations" not in path and not path.endswith("/state/pending.json")}, private)

    def test_fs3_special_bits_are_removed(self):
        file = self.workspace / "frontend/app.txt"
        os.chmod(file, 0o6644)
        os.chmod(self.workspace / "backend", 0o1775)
        code, out, err = self.run_cli("permissions", "plan")
        self.assertRegex(out, r"special-bits [1-9]")
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual((mode(file), mode(self.workspace / "backend")), (0o660, 0o2770))

    def test_fs4_executables_come_only_from_the_manifest_and_the_workspace_still_matches_it(self):
        listed = self.workspace / "listed.sh"
        listed.write_text("#!/bin/sh\n")
        os.chmod(listed, 0o700)
        self.manifest()
        unlisted = self.workspace / "later.py"
        unlisted.write_text("print(1)\n")
        os.chmod(unlisted, 0o600)
        controller = self.controller()
        self.assertTrue(controller.workspace_status()["dirty"])  # later.py is not in the manifest
        unlisted.unlink()
        self.assertFalse(controller.workspace_status()["dirty"])
        (self.workspace / "notes.sh").write_text("echo\n")
        self.manifest()
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual(mode(listed), 0o770)
        self.assertEqual(mode(self.workspace / "notes.sh"), 0o660)   # never by extension
        status = self.controller().workspace_status()
        self.assertFalse(status["dirty"], status["changes"])
        # Without a manifest nothing is designated, and the plan says so.
        self.context.source_manifest_path.unlink()
        code, out, err = self.run_cli("permissions", "plan", "--scope", "workspace")
        self.assertIn("workspace-no-source-manifest", out)
        # A candidate with workspace executables none is refused before anything changes.
        before = tree_state(self.workspace)

        def none_wizard(controller, base, admin):
            candidate = json.loads(json.dumps(base.policy))
            candidate["permissions"]["workspace"]["executables"] = "none"
            return candidate

        with mock.patch.object(pf.Controller, "permission_wizard", none_wizard):
            code, out, err = self.apply(answers=[], phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permission-policy-unsupported: The permission policy choice workspace.executables = none "
                      "is valid but not supported by this control", err)
        self.assertEqual(tree_state(self.workspace), before)

    def test_fs5_a_hard_link_to_an_outside_file_is_a_blocker_and_the_outside_inode_is_untouched(self):
        outside = self.base / "outside.txt"
        outside.write_text("secret\n")
        os.chmod(outside, 0o600)
        os.link(outside, self.workspace / "frontend/linked.txt")
        before = os.lstat(str(outside))
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("scope-entry-hardlinked: workspace: frontend/linked.txt: 2 hard links", out)
        self.assertIn("ERROR: permissions-blocked:", err)
        after = os.lstat(str(outside))
        self.assertEqual((after.st_mode, after.st_gid, after.st_ctime_ns), (before.st_mode, before.st_gid,
                                                                           before.st_ctime_ns))
        self.assertEqual(outside.read_text(), "secret\n")
        self.assertFalse(self.record_path.exists())

    def test_fs6_symlinks_and_fifos_are_blockers_and_never_followed(self):
        target = self.base / "target.txt"
        target.write_text("x\n")
        os.chmod(target, 0o604)
        os.symlink(str(target), str(self.workspace / "frontend/link"))
        os.mkfifo(str(self.backups / "pipe"))
        before = os.lstat(str(target))
        code, out, err = self.run_cli("permissions", "check")
        self.assertEqual(code, 1)
        self.assertIn("scope-entry-link: workspace: frontend/link: symbolic link", out)
        self.assertIn("scope-entry-special: backups: pipe: not a regular file or directory", out)
        self.assertEqual(self.apply(phrase=None)[0], 1)
        self.assertEqual(os.lstat(str(target)).st_ctime_ns, before.st_ctime_ns)

    def test_fs8_a_replaced_parent_is_refused_and_the_outside_directory_is_untouched(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "app.txt").write_text("outside\n")
        os.chmod(outside / "app.txt", 0o600)
        os.chmod(self.backups, 0o700)   # an unfenced scope with a change, applied after the workspace
        before = os.lstat(str(outside / "app.txt"))
        frontend = self.workspace / "frontend"
        os.chmod(frontend / "app.txt", 0o644)

        def hook(name):
            if name == "after-fence":
                os.rename(frontend, self.workspace / "frontend.moved")
                os.symlink(str(outside), str(frontend))

        code, out, err = self.apply(controller_class=self.seamed(hook=hook))
        self.assertEqual(code, 1)
        self.assertTrue("permissions-entry-changed" in err or "permissions-blocked" in err, err)
        after = os.lstat(str(outside / "app.txt"))
        self.assertEqual((after.st_mode, after.st_ctime_ns), (before.st_mode, before.st_ctime_ns))
        # The engine itself: a planned entry whose parent became a link is permissions-entry-changed.
        os.unlink(frontend)
        os.rename(self.workspace / "frontend.moved", frontend)
        inventory = self.inventory("workspace", self.workspace)
        entry = next(item for item in inventory.entries if item.relative == "frontend/app.txt")
        directories = {item.relative: (item.dev, item.ino) for item in inventory.entries if item.type == "dir"}
        os.rename(frontend, self.workspace / "frontend.moved")
        os.symlink(str(outside), str(frontend))
        root_fd = pf_instance.open_scope_root(self.workspace, inventory.root_identity)
        try:
            with self.assertRaises(pf_instance.PermissionEntryChanged):
                pf_instance.open_entry_parent(root_fd, entry.relative, directories)
        finally:
            os.close(root_fd)
        self.assertEqual(os.lstat(str(outside / "app.txt")).st_ctime_ns, before.st_ctime_ns)

    def test_fs9_mount_boundaries_are_reported_and_never_entered(self):
        inventory = self.inventory("workspace", "/dev")
        mounts = {relative for code, relative, _ in inventory.blockers if code == "scope-mount-boundary"}
        expected = {name for name in ("shm", "pts", "mqueue")
                    if os.path.isdir("/dev/" + name) and os.lstat("/dev/" + name).st_dev != os.lstat("/dev").st_dev}
        if not expected:
            raise unittest.SkipTest("the container has no separate mount below /dev")
        self.assertTrue(expected <= mounts, (expected, mounts))
        self.assertFalse(any(entry.relative.startswith(tuple(name + "/" for name in expected))
                             for entry in inventory.entries))

    def test_fs10_application_storage_is_never_a_scope_or_entered(self):
        docker = self.workspace / "@docker"
        docker.mkdir()
        (docker / "volume.bin").write_text("x")
        inventory = self.inventory("workspace", self.workspace)
        self.assertIn(("scope-contains-app-storage", "@docker"), [item[:2] for item in inventory.blockers])
        self.assertFalse(any(entry.relative.startswith("@docker/") for entry in inventory.entries))
        for root in ("/var/lib/docker", "/", "/volume1", "/var/lib"):
            with self.subTest(root=root):
                inventory = self.inventory("backups", root)
                self.assertEqual(inventory.blockers[0][0], "scope-contains-app-storage")
                self.assertEqual(inventory.entries, ())

    def test_fs11_an_untrusted_owner_in_protected_storage_is_a_blocker_and_copies_are_root_owned(self):
        imported = self.backups / "imported.dump"
        imported.write_text("x\n")
        os.chown(imported, EDITOR_UID, -1)
        os.chmod(imported, 0o644)
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("scope-untrusted-owner: backups: imported.dump: owner uid 4242", out)
        self.assertEqual((owner(imported)[0], mode(imported)), (EDITOR_UID, 0o644))
        copy_path = self.recovery / "copy.dump"
        pf.copy_fresh(imported, copy_path)
        self.assertEqual(owner(copy_path)[0], 0)
        self.assertEqual(xattrs(copy_path).keys() & set(pf_bootstrap.POSIX_ACL_NAMES), set())

    def test_fs12_the_entry_limit_stops_the_inventory_and_nothing_changes(self):
        self.scramble()
        before = tree_state(self.workspace, self.backups)
        with mock.patch.object(pf.pf_source, "MANIFEST_ENTRY_LIMIT", 3):
            code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("scope-too-large: workspace:", out)
        self.assertEqual(tree_state(self.workspace, self.backups), before)

    def test_fs13_a_directory_swapped_for_a_link_during_the_walk_is_never_read_through(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("x\n")
        real_examine = pf_instance._Walk.examine
        frontend = self.workspace / "frontend"

        def examine(walk, parent_fd, name, relative):
            if relative == "frontend" and not os.path.islink(str(frontend)):
                os.rename(frontend, self.workspace / "frontend.away")
                os.symlink(str(outside), str(frontend))
            return real_examine(walk, parent_fd, name, relative)

        with mock.patch.object(pf_instance._Walk, "examine", examine):
            inventory = self.inventory("workspace", self.workspace)
        self.assertIn(("scope-entry-link", "frontend"), [item[:2] for item in inventory.blockers])
        self.assertFalse(any("private.txt" in entry.relative for entry in inventory.entries))

    # FS-13 above swaps before the walk's lstat (the lstat classification). The two cases below swap between the lstat
    # and the no-follow open (PF-A2.3 audit): the O_NOFOLLOW and the opened-identity defences of _Walk.examine.
    def inventory_swapped_at_open(self, replace):
        real_open = os.open
        swapped = []

        def opener(path, flags, *args, **kwargs):
            if path == "frontend" and kwargs.get("dir_fd") is not None and not swapped:
                swapped.append(True)
                replace()
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(pf_instance.os, "open", opener):
            inventory = self.inventory("workspace", self.workspace)
        self.assertEqual(swapped, [True])
        return inventory

    def test_fs13b_a_directory_swapped_for_a_link_after_its_lstat_is_refused_by_the_no_follow_open(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("x\n")
        frontend = self.workspace / "frontend"

        def replace():
            os.rename(frontend, self.workspace / "frontend.away")
            os.symlink(str(outside), str(frontend))

        inventory = self.inventory_swapped_at_open(replace)
        self.assertIn(("scope-entry-link", "frontend", "replaced by a symbolic link (never followed)"),
                      inventory.blockers)
        self.assertFalse(any("private.txt" in entry.relative for entry in inventory.entries))

    def test_fs13c_a_directory_replaced_by_another_after_its_lstat_is_refused_by_the_identity_check(self):
        frontend = self.workspace / "frontend"

        def replace():
            os.rename(frontend, self.workspace / "frontend.away")
            frontend.mkdir()
            (frontend / "planted.txt").write_text("x\n")

        inventory = self.inventory_swapped_at_open(replace)
        self.assertIn(("scope-path-unsafe", "frontend", "replaced while it was inventoried"), inventory.blockers)
        self.assertFalse(any("planted.txt" in entry.relative for entry in inventory.entries))


class ContextMapping(Instance):
    """FS-7 / RT-9: check maps refuse findings onto scopes (A1-T17 offline, through the CLI)."""

    def make_paths(self):
        home = self.base / "home"
        paths = pfx.data_home(home, group=self.group)
        shared = home / "shared"
        shared.mkdir()
        os.chmod(shared, 0o755)
        workspace = shared / "repo"
        os.rename(paths["workspace"], workspace)
        paths["workspace"] = workspace
        return paths

    def test_fs7_rt9_scope_attributable_findings_block_only_that_scope(self):
        shared = self.base / "home" / "shared"
        os.chmod(shared, 0o775)       # an editor-writable ancestor of the workspace only
        code, out, err = self.run_cli("permissions", "check")
        self.assertEqual(code, 1, out + err)
        self.assertIn("scope-path-unsafe: workspace: .: [refuse] ancestor-replaceable", out)
        for scope in ("configuration", "backups", "recovery", "private_state"):
            self.assertIn(f"\n{scope} — ", out)
        self.assertIn("ERROR: permissions-blocked:", err)
        os.chmod(shared, 0o755)
        # A symlinked workspace root.
        real = self.base / "home" / "shared" / "repo.real"
        os.rename(self.workspace, real)
        os.symlink(str(real), str(self.workspace))
        code, out, err = self.run_cli("permissions", "check")
        self.assertEqual(code, 1)
        self.assertIn("scope-path-unsafe: workspace: .: [refuse] registered-path-symlink", out)
        self.assertIn("\nbackups — ", out)
        # apply in the same context: the protected-context refusal comes before the lock.
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("Protected context validation refused mutation", err)
        self.assertEqual([name for name in self.operations() if "permissions" in name], [])

    def test_rt9_a_context_level_finding_refuses_without_walking_any_scope(self):
        conf = self.layout.bootstrap_conf
        os.chmod(conf, 0o666)
        try:
            with mock.patch.object(pf_instance, "inventory_scope", side_effect=AssertionError("walked")):
                code, out, err = self.run_cli("permissions", "check")
        finally:
            os.chmod(conf, 0o600)
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permissions-context-refused: The protected context is refused (see the findings above), "
                      "so the permission policy cannot be read safely; no scope was inspected.", err)
        self.assertIn(str(conf), out)

    def test_rt9b_a_finding_on_an_ancestor_shared_with_the_installation_root_is_context_level(self):
        """PF-A2.3 audit: the temp base is an ancestor of every data root and of the installation root/private state;
        a refuse finding there invalidates the approval record too, so check reads neither the record nor a scope."""
        shared = pf_bootstrap.Finding("refuse", "ancestor-replaceable", str(self.base),
                                      "owner uid 0 mode 0o777: an editor could replace entries")
        validation = types.SimpleNamespace(findings=[shared], mutation_allowed=False)

        class Shared(pf.Controller):
            def ensure_validation(self):
                return validation

        controller = Shared(self.context)
        context, unsafe = controller.permission_findings()
        self.assertEqual(context, [shared])
        with mock.patch.object(pf_instance, "inventory_scope", side_effect=AssertionError("walked")), \
                mock.patch.object(pf.Controller, "read_permission_record", side_effect=AssertionError("read")):
            code, out, err = self.run_cli("permissions", "check", controller_class=Shared)
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: permissions-context-refused:", err)
        self.assertIn("[refuse] ancestor-replaceable: " + str(self.base), out)
        # An ancestor of the data roots only (not of the installation root) stays scope-attributable (FS-7).
        home = pf_bootstrap.Finding("refuse", "ancestor-replaceable", str(self.base / "home"), "mode 0o777")
        validation.findings = [home]
        context, unsafe = Shared(self.context).permission_findings()
        self.assertEqual(context, [])
        self.assertEqual(sorted(unsafe), ["backups", "configuration", "recovery", "workspace"])

    def test_fs7b_a_missing_or_unsafe_storage_root_blocks_only_that_scope_on_an_unapproved_instance(self):
        """PF-A2.3 audit: the derived backups/recovery group is never read from an unsafe or unreadable root; the
        scope is blocked and every other scope is still reported."""
        self.assertFalse(self.record_path.exists())
        for scope, root in (("backups", self.backups), ("recovery", self.recovery)):
            moved = root.with_name(root.name + ".away")
            os.rename(root, moved)
            try:
                for verb in ("check", "plan"):
                    with self.subTest(scope=scope, verb=verb):
                        code, out, err = self.run_cli("permissions", verb)
                        self.assertEqual(code, 1, out + err)
                        self.assertIn(f"scope-path-unsafe: {scope}: .:", out)
                        self.assertIn(f"\n{scope} — {root}\n  Policy: group unavailable", out)
                        self.assertNotIn(f"proposes group users for {scope};", out)
                        for other in ("workspace", "configuration", "private_state"):
                            self.assertIn(f"\n{other} — ", out)
                        self.assertIn("ERROR: permissions-blocked:", err)
            finally:
                os.rename(moved, root)
        real = self.backups.with_name("backups.real")
        os.rename(self.backups, real)
        os.symlink(str(real), str(self.backups))
        code, out, err = self.run_cli("permissions", "check")
        self.assertEqual(code, 1, out + err)
        self.assertIn("scope-path-unsafe: backups: .: [refuse] registered-path-symlink", out)
        self.assertIn(f"\nbackups — {self.backups}\n  Policy: group unavailable", out)
        self.assertIn("\nrecovery — ", out)


# ============================================================================ AC: ACL safety (real fs)


class Acl(Instance):

    def test_ac1_an_access_acl_blocks_its_scope_and_nothing_is_rewritten(self):
        file = self.workspace / "frontend/app.txt"
        os.chmod(file, 0o640)
        set_xattr(file, "system.posix_acl_access", ACCESS_ACL)
        before = (mode(file), xattrs(file))
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("scope-entry-acl: workspace: frontend/app.txt: carries an access ACL", out)
        self.assertEqual((mode(file), xattrs(file)), before)

    def test_ac2_an_unknown_acl_named_xattr_is_a_blocker(self):
        file = self.backups / "data.bin"
        file.write_text("x")
        set_xattr(file, "user.pf_acl_fixture", b"1")
        inventory = pf_instance.inventory_scope("backups", self.backups, owner_rule="trusted", limit=100)
        self.assertIn(("scope-entry-acl", "data.bin"), [item[:2] for item in inventory.blockers])

    def test_ac3_a_default_acl_directory_is_allowed_and_reported_as_future_file_behaviour(self):
        folder = self.workspace / "frontend"
        set_xattr(folder, "system.posix_acl_default", DEFAULT_ACL)
        inventory = pf_instance.inventory_scope("workspace", self.workspace, owner_rule="preserve", limit=1000)
        self.assertEqual([item for item in inventory.blockers if item[0] == "scope-entry-acl"], [])
        self.assertIn("frontend", inventory.default_acl_dirs)
        code, out, err = self.run_cli("permissions", "check", "--scope", "workspace")
        self.assertIn("default ACL present on 1 dirs", out)
        self.assertIn("future_file_behavior_verified: not verified", out)

    def test_ac4_the_fd_classification_agrees_with_inspect_posix_acl(self):
        fixtures = {}
        for name in ("none", "access", "default", "both", "unknown"):
            path = self.base / ("acl-" + name)
            path.mkdir()
            fixtures[name] = path
        set_xattr(fixtures["access"], "system.posix_acl_access", ACCESS_ACL)
        set_xattr(fixtures["default"], "system.posix_acl_default", DEFAULT_ACL)
        set_xattr(fixtures["both"], "system.posix_acl_access", ACCESS_ACL)
        set_xattr(fixtures["both"], "system.posix_acl_default", DEFAULT_ACL)
        set_xattr(fixtures["unknown"], "user.acl_custom", b"x")
        expected_names = {"none": set(), "access": {"system.posix_acl_access"},
                          "default": {"system.posix_acl_default"}, "unknown": set(),
                          "both": {"system.posix_acl_access", "system.posix_acl_default"}}
        for name, path in fixtures.items():
            with self.subTest(fixture=name):
                fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    info = pf_instance.inspect_acl_fd(fd)
                finally:
                    os.close(fd)
                self.assertEqual(info.state, pf_bootstrap.inspect_posix_acl(path))
                self.assertEqual(set(info.names), expected_names[name])
        # An unreadable blob classifies as unknown through both readers.
        with mock.patch.object(os, "getxattr", return_value=b"bad"):
            fd = os.open(str(fixtures["access"]), os.O_RDONLY | os.O_DIRECTORY)
            try:
                info = pf_instance.inspect_acl_fd(fd)
            finally:
                os.close(fd)
            self.assertEqual(info.state, pf_bootstrap.inspect_posix_acl(fixtures["access"]))
            self.assertEqual(info.state.kind, "unknown")

    def test_ac5_copy_fresh_carries_no_acl_and_no_mode(self):
        source = self.base / "source.txt"
        source.write_text("content\n")
        os.chmod(source, 0o751)
        set_xattr(source, "system.posix_acl_access", ACCESS_ACL)
        destination = self.base / "copy.txt"
        pf.copy_fresh(source, destination)
        self.assertEqual(destination.read_text(), "content\n")
        self.assertEqual(set(xattrs(destination)) & set(pf_bootstrap.POSIX_ACL_NAMES), set())
        self.assertNotEqual(mode(destination), 0o751)
        tree = self.base / "tree"
        (tree / "sub").mkdir(parents=True)
        (tree / "sub/file").write_text("x")
        os.symlink("file", str(tree / "sub/link"))
        with self.assertRaisesRegex(pf.Failure, "links and special files are never copied"):
            pf.copy_fresh(tree, self.base / "tree-copy")


# ============================================================================ FL: copy_fresh races (real fs)


@REAL_FS
class CopyFreshRaces(unittest.TestCase):
    """PF-A2.3 audit: copy_fresh is descriptor-relative and no-follow on both sides (section 3.10). Each case swaps
    an entry right after the check that used to guard a path-based step."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.source = self.base / "configuration"
        self.target = self.base / "bundle"
        self.source.mkdir()
        self.target.mkdir()
        self.secret = self.base / "secret"
        self.secret.write_text("SECRET-CONTENT")
        os.chmod(self.secret, 0o600)
        (self.source / "pf-config.json").write_text("{}")

    def tearDown(self):
        self.temp.cleanup()

    @contextlib.contextmanager
    def after(self, functions, name, swap):
        """Run ``swap`` once, right after the first ``os.<function>`` (any of ``functions``) of an entry called
        ``name`` returns: the check that guards the next step has just passed."""
        done = []

        def wrap(real):
            def wrapper(path, *args, **kwargs):
                result = real(path, *args, **kwargs)
                if not done and isinstance(path, (str, bytes, os.PathLike))                         and os.path.basename(os.fspath(path)) == name:
                    done.append(True)
                    swap()
                return result
            return wrapper

        with contextlib.ExitStack() as stack:
            for function in functions:
                stack.enter_context(mock.patch.object(os, function, wrap(getattr(os, function))))
            yield

    def test_fl11_a_source_file_swapped_for_a_link_after_its_lstat_is_never_read_through(self):
        config = self.source / "pf-config.json"

        def swap():
            os.unlink(str(config))
            os.symlink(str(self.secret), str(config))

        with self.assertRaisesRegex(pf.Failure, "links and special files are never copied"):
            with self.after(("lstat", "stat"), "pf-config.json", swap):
                pf.copy_fresh(config, self.target / "pf-config.json")
        self.assertEqual(os.listdir(str(self.target)), [])

    def test_fl12_a_source_file_replaced_after_its_lstat_is_refused_by_the_identity_check(self):
        config = self.source / "pf-config.json"

        def swap():
            os.rename(str(config), str(self.source / "pf-config.away"))
            config.write_text("OTHER")

        with self.assertRaisesRegex(pf.Failure, "replaced while it was copied"):
            with self.after(("lstat", "stat"), "pf-config.json", swap):
                pf.copy_fresh(config, self.target / "pf-config.json")
        self.assertEqual(os.listdir(str(self.target)), [])

    def test_fl13_a_new_directory_swapped_for_a_link_is_never_written_through(self):
        candidate = self.base / "candidate"
        (candidate / "frontend").mkdir(parents=True)
        (candidate / "frontend" / "app.txt").write_text("x")
        outside = self.base / "outside"
        outside.mkdir()
        made = self.target / "frontend"

        def swap():
            os.rename(str(made), str(self.target / "frontend.away"))
            os.symlink(str(outside), str(made))

        with self.assertRaisesRegex(pf.Failure, "replaced while it was written"):
            with self.after(("mkdir",), "frontend", swap):
                pf.copy_fresh(candidate / "frontend", made)
        self.assertEqual(os.listdir(str(outside)), [])

    def test_fl14_a_hard_linked_source_file_is_never_copied(self):
        config = self.source / "pf-config.json"
        os.link(str(config), str(self.base / "elsewhere"))
        with self.assertRaisesRegex(pf.Failure, "hard links"):
            pf.copy_fresh(config, self.target / "pf-config.json")
        self.assertEqual(os.listdir(str(self.target)), [])

    def test_fl15_a_tree_copy_is_content_only_and_new_inodes(self):
        tree = self.base / "tree"
        (tree / "a" / "b").mkdir(parents=True)
        (tree / "a" / "b" / "file").write_text("deep")
        os.chmod(str(tree / "a" / "b" / "file"), 0o755)
        pf.copy_fresh(tree, self.target / "tree")
        copied = self.target / "tree" / "a" / "b" / "file"
        self.assertEqual(copied.read_text(), "deep")
        self.assertEqual(mode(self.target / "tree" / "a"), 0o700)
        self.assertNotEqual(os.lstat(str(copied)).st_ino, os.lstat(str(tree / "a" / "b" / "file")).st_ino)
        self.assertEqual(sorted(os.listdir(str(self.target / "tree" / "a" / "b"))), ["file"])


# ============================================================================ FZ: editor freeze (real fs)


class Freeze(Instance):

    def bulk(self):
        os.chmod(self.workspace / "frontend/app.txt", 0o644)
        os.chmod(self.config_dir / "pf-config.json", 0o640)

    def assert_refused_and_restored(self, code, err, holder_kind, pid, before):
        self.assertEqual(code, 1)
        self.assertIn("ERROR: editor-freeze-refused:", err)
        self.assertIn(f"{pid} ", err)
        self.assertIn(f" {holder_kind})", err)
        self.assertIn("start the command from outside it (for example 'cd /')", err)
        self.assertEqual(tree_state(self.workspace, self.config_dir)[str(self.workspace / "frontend/app.txt")],
                         before[str(self.workspace / "frontend/app.txt")])
        operation = self.last_apply()
        self.assertEqual(self.outcome(operation)["result"], "abandoned")
        self.assertIsNone(self.pending())
        self.assertFalse(self.record_path.exists())
        kinds = {line["kind"] for line in self.effects(operation)}
        self.assertEqual(kinds, {"fence"})

    def test_fz1_a_working_directory_inside_the_workspace_refuses_and_lifts_the_fence(self):
        self.bulk()
        root_mode = mode(self.workspace)
        before = tree_state(self.workspace, self.config_dir)
        with holder(cwd=self.workspace / "frontend") as pid:
            code, out, err = self.apply()
        self.assert_refused_and_restored(code, err, "cwd", pid, before)
        self.assertEqual(mode(self.workspace), root_mode)

    def test_fz2_an_open_descriptor_on_a_deep_file_refuses(self):
        self.bulk()
        before = tree_state(self.workspace, self.config_dir)
        with holder(open_path=self.workspace / "backend/alembic/versions/001.py") as pid:
            code, out, err = self.apply()
        self.assert_refused_and_restored(code, err, "fd", pid, before)

    def test_fz3_the_fence_denies_editors_during_the_apply_and_the_target_admits_them_after(self):
        users = gid_of("users")
        for path in (self.workspace, self.workspace / "frontend"):
            os.chown(path, -1, users)
            os.chmod(path, 0o750)
        file = self.workspace / "frontend/app.txt"
        os.chown(file, -1, users)
        os.chmod(file, 0o640)
        os.chmod(self.workspace / "app-version.txt", 0o604)
        self.assertEqual(child(can_open(file)), 0)
        seen = []

        def hook(name):
            if name == "after-fence":
                seen.append(child(can_open(file)))

        code, out, err = self.apply(controller_class=self.seamed(hook=hook))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(seen, [3])                 # EACCES while fenced
        self.assertEqual(mode(self.workspace), 0o2770)
        self.assertEqual(child(can_open(file, os.O_RDWR)), 0)

    def test_fz4_an_unusable_proc_blocks_bulk_apply(self):
        self.bulk()
        before = tree_state(self.workspace, self.config_dir)
        with mock.patch.object(pf_instance, "PROC_ROOT", str(self.base / "no-proc")):
            code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("editor-freeze-unavailable: Bulk change of workspace needs a verified editor freeze, which is "
                      "unavailable here", out)
        self.assertEqual(tree_state(self.workspace, self.config_dir), before)

    def test_fz5_an_acl_on_the_scope_root_makes_the_freeze_unavailable(self):
        self.bulk()
        set_xattr(self.workspace, "system.posix_acl_default", DEFAULT_ACL)
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("the scope root carries an ACL", out)

    def test_fz6_a_root_only_change_needs_no_freeze(self):
        self.assertEqual(self.apply()[0], 0)
        os.chmod(self.workspace, 0o2750)
        code, out, err = self.run_cli("permissions", "plan", "--scope", "workspace")
        self.assertIn("Freeze: not needed", out)
        self.assertEqual(self.apply("--scope", "workspace")[0], 0)
        self.assertEqual({line["kind"] for line in self.effects(self.last_apply())}, {"entry"})

    def test_fz7_a_handle_in_one_scope_refuses_before_any_entry_of_either_changes(self):
        self.bulk()
        below = {path: value for path, value in tree_state(self.workspace, self.config_dir).items()
                 if path not in (str(self.workspace), str(self.config_dir))}
        with holder(open_path=self.config_dir / "pf-config.json"):
            code, out, err = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("editor-freeze-refused", err)
        self.assertEqual({path: value for path, value in tree_state(self.workspace, self.config_dir).items()
                          if path not in (str(self.workspace), str(self.config_dir))}, below)
        lines = self.effects(self.last_apply())
        self.assertEqual([(line["kind"], line["scope"]) for line in lines],
                         [("fence", "workspace"), ("fence", "configuration"), ("fence", "configuration"),
                          ("fence", "workspace")])
        self.assertEqual(self.outcome(self.last_apply())["result"], "abandoned")

    def created_after_the_plan(self, create, **hold):
        """Apply with an entry created at the after-plan seam and held (cwd or fd) by another process."""
        self.bulk()
        before = tree_state(self.workspace, self.config_dir)
        held = {}
        with contextlib.ExitStack() as stack:
            def hook(name):
                if name == "after-plan":
                    path = create()
                    held["pid"] = stack.enter_context(holder(**{key: path for key in hold}))

            code, out, err = self.apply(controller_class=self.seamed(hook=hook))
        return code, err, held["pid"], before

    def test_fz9_a_working_directory_in_a_directory_created_after_the_plan_refuses(self):
        """PF-A2.3 audit: the open-handle scan covers the authoritative (post-fence) inventory, not only the plan."""
        def create():
            folder = self.workspace / "newdir"
            folder.mkdir()
            return folder

        code, err, pid, before = self.created_after_the_plan(create, cwd=True)
        self.assert_refused_and_restored(code, err, "cwd", pid, before)

    def test_fz10_a_descriptor_on_a_file_created_after_the_plan_refuses(self):
        def create():
            path = self.workspace / "frontend" / "newfile.txt"
            path.write_text("x")
            return path

        code, err, pid, before = self.created_after_the_plan(create, open_path=True)
        self.assert_refused_and_restored(code, err, "fd", pid, before)

    def test_fz8_a_compliant_root_is_lifted_by_an_explicit_root_operation(self):
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual(mode(self.workspace), 0o2770)
        os.chmod(self.workspace / "frontend/app.txt", 0o600)
        self.assertEqual(self.apply("--scope", "workspace")[0], 0)
        operation = self.last_apply()
        self.assertEqual(mode(self.workspace), 0o2770)
        plan = json.loads((self.context.operations_dir / operation / "permission-plan.json").read_text())
        changes = (self.context.operations_dir / operation / "permission-changes.jsonl").read_text().splitlines()
        self.assertEqual(plan["change_count"], 2)
        self.assertEqual([json.loads(line)[1] for line in changes[1:]], ["", "frontend/app.txt"])
        lines = self.effects(operation)
        self.assertEqual([(line["kind"], line["path"]) for line in lines],
                         [("fence", ""), ("entry", "frontend/app.txt"), ("entry", "")])
        self.assertEqual(lines[-1]["before_mode"], lines[0]["after_mode"])


# ============================================================================ FF: future files (real fs)


class FutureFiles(Instance):

    def test_ff1_the_config_writer_creates_env_with_the_configuration_target(self):
        users = gid_of("users")
        for access, expected, writable in (("1", 0o660, 0), ("2", 0o640, 3)):
            with self.subTest(access=access):
                env = self.config_dir / ".env"
                if env.exists():
                    env.unlink()
                for name in os.listdir(str(self.private)):
                    if name == "permission-policy.json":
                        os.unlink(str(self.private / name))
                answers = list(DEFAULTS)
                answers[5] = access          # configuration access
                self.assertEqual(self.apply(answers=answers)[0], 0)
                old = os.umask(0o077)
                try:
                    code, out, err = self.run_cli("config", "app",
                                                  answers=["", "", "UTC", "2", "", "partflow.internal.example", "y"])
                finally:
                    os.umask(old)
                if code != 0 and "zone" in err:
                    raise unittest.SkipTest("the image's installed zone data has no UTC")
                self.assertEqual(code, 0, out + err)
                self.assertEqual((owner(env), mode(env)), ((0, users), expected))
                self.assertEqual(child(can_open(env, os.O_WRONLY)), writable)

    def test_ff2_setgid_carries_the_group_not_write_access(self):
        self.assertEqual(self.apply()[0], 0)
        folder = self.workspace / "frontend"

        def create():
            os.umask(0o077)
            with open(str(folder / "editor.txt"), "w") as stream:
                stream.write("x")
            return True

        self.assertEqual(child(create), 0)
        created = folder / "editor.txt"
        self.assertEqual((owner(created), mode(created)), ((EDITOR_UID, gid_of("users")), 0o600))
        code, out, err = self.run_cli("permissions", "check", "--scope", "workspace")
        self.assertEqual(code, 1)
        self.assertIn("setgid on directories carries group users, not write access", out)
        self.assertIn("ERROR: permissions-differ: 1 entry differs", err)

    def test_ff3_a_default_acl_directory_is_reported_not_verified(self):
        self.assertEqual(self.apply()[0], 0)
        folder = self.workspace / "frontend"
        set_xattr(folder, "system.posix_acl_default", DEFAULT_ACL)
        code, out, err = self.run_cli("permissions", "check", "--scope", "workspace")
        self.assertIn("future_file_behavior_verified: not verified — setgid on directories carries group users", out)
        self.assertIn("default ACL present on 1 dirs", out)

    def test_ff4_publish_fresh_sets_and_verifies_the_backups_targets_under_a_restrictive_umask(self):
        controller = self.controller()
        old = os.umask(0o077)
        try:
            with controller.lock():
                controller.ensure_backup_tree()
                folder = controller.backups_dir / "20261007T000000Z-aaaaaaaaaaaa-000009"
                folder.mkdir()
                (folder / "database.dump").write_bytes(b"dump")
                controller.publish_fresh("backups", folder)
        finally:
            os.umask(old)
        self.assertEqual((mode(folder), mode(folder / "database.dump")), (0o750, 0o640))
        self.assertEqual(owner(folder / "database.dump"), (0, 0))


# ============================================================================ PA: partial apply, resume, abandon (real fs)


class Partial(Instance):

    def interrupted(self, *, fault=None, hook=None):
        """A bulk apply interrupted by the fault seam (after ``fault`` operations) or a raising hook."""
        self.folder = self.scramble()
        for name in ("a.txt", "b.txt", "c.txt"):
            (self.workspace / "frontend" / name).write_text(name)
            os.chmod(self.workspace / "frontend" / name, 0o644)
        code, out, err = self.apply(controller_class=self.seamed(fault=fault, hook=hook))
        self.assertEqual(code, 1, out + err)
        return self.last_apply(), err

    def test_pa1_pa2_an_interruption_is_never_complete_and_check_reports_partial_without_writing(self):
        operation, err = self.interrupted(fault=5)
        self.assertIn("ERROR: permissions-interrupted: The permission apply was interrupted after", err)
        self.assertIn("it is NOT complete. Run '", err)
        lines = self.effects(operation)
        self.assertGreaterEqual(len([line for line in lines if line["kind"] == "entry"]), 5)
        self.assertEqual([line["seq"] for line in lines], list(range(1, len(lines) + 1)))
        self.assertEqual((self.pending()["operation"], self.pending()["phase"]), ("permissions", "interrupted"))
        self.assertFalse(self.record_path.exists())
        before = tree_state(self.workspace, self.config_dir, self.backups, self.recovery, self.private)
        operations = self.operations()
        code, out, err = self.run_cli("permissions", "check")
        self.assertEqual(code, 1)
        self.assertRegex(out, r"workspace: mode_applied: partial \(\d+ differ\)")
        self.assertIn(f"an apply is in progress: {operation}", out)
        self.assertEqual(tree_state(self.workspace, self.config_dir, self.backups, self.recovery, self.private), before)
        self.assertEqual(self.operations(), operations)

    def test_pa3_an_open_journal_blocks_other_routes_and_the_installer(self):
        operation, _ = self.interrupted(fault=2)
        code, out, err = self.run_cli("backup")
        self.assertEqual(code, 1)
        self.assertIn(f"operation-open: operation {operation} (permissions, phase interrupted) is incomplete; 'backup' "
                      "is not a legal next action for it.", err)
        self.assertIn("pf --instance staging permissions apply --resume: finish the interrupted permission apply; "
                      "pf --instance staging permissions apply --abandon: compensate it from its effect journal", err)
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn(f"ERROR: permissions-apply-pending: An interrupted permission apply {operation} is open", err)
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.context])
        self.assertIn("instance-operation-pending", [item.code for item in report.conflicts])
        code, out, err = self.run_cli("doctor")
        self.assertIn(f"| interrupted apply {operation}", out)

    def test_pa4_resume_completes_and_approves_with_both_confirmed_plans(self):
        operation, _ = self.interrupted(fault=3)
        code, out, err = self.run_cli("permissions", "apply", "--resume", answers=["RESUME PERMISSIONS staging"])
        self.assertEqual(code, 0, out + err)
        record = self.record()
        resume = self.last_apply()
        self.assertEqual(record["operation_id"], operation)
        self.assertEqual([item["operation_id"] for item in record["confirmed_plans"]], [operation, resume])
        resume_changes = (self.context.operations_dir / resume / "permission-changes.jsonl").read_bytes()
        self.assertEqual(record["confirmed_plans"][1]["plan_sha256"], hashlib.sha256(resume_changes).hexdigest())
        self.assertIsNone(self.pending())
        self.assertEqual(self.outcome(resume)["action"], "resume")
        self.assertEqual({line["operation_id"] for line in self.effects(operation)}, {operation, resume})
        self.assertEqual(self.run_cli("permissions", "check")[0], 0)

    def test_pa5_abandon_restores_and_reports_conflicts(self):
        operation, _ = self.interrupted(fault=4)
        executed = [line for line in self.effects(operation) if line["kind"] == "entry"][:4]
        edited = None
        for line in executed:
            if line["type"] == "file":
                edited = (self.workspace if line["scope"] == "workspace" else self.config_dir) / line["path"]
                break
        os.chmod(edited, 0o604)
        code, out, err = self.run_cli("permissions", "apply", "--abandon", answers=["ABANDON PERMISSIONS staging"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permissions-abandon-conflicts: 1 object(s) changed after the interruption and were left "
                      "as they are:", err)
        self.assertEqual(mode(edited), 0o604)
        for line in executed:
            path = (self.workspace if line["scope"] == "workspace" else self.config_dir) / line["path"]
            if path != edited:
                self.assertEqual((mode(path), owner(path)[1]), (line["before_mode"], line["before_gid"]), path)
        self.assertIsNone(self.pending())
        self.assertEqual(self.outcome(self.last_apply())["result"], "abandoned-with-conflicts")
        self.assertFalse(self.record_path.exists())
        # PA-6: a fresh apply with a changed policy runs the wizard again and completes.
        gid_of("staff")
        answers = list(DEFAULTS)
        answers[7] = "staff"
        code, out, err = self.apply(answers=answers)
        self.assertEqual(code, 0, out + err)
        self.assertEqual((self.record()["revision"], self.record()["policy"]["permissions"]["backups"]["group"]),
                         (1, "staff"))

    def test_pa7_a_torn_last_line_is_ignored_and_a_corrupt_line_is_refused(self):
        operation, _ = self.interrupted(fault=2)
        path = self.context.operations_dir / operation / "permission-effects.jsonl"
        good = path.read_bytes()
        path.write_bytes(good + b'{"seq": 99, "kind": "en')
        code, out, err = self.run_cli("permissions", "apply", "--resume", answers=["RESUME PERMISSIONS staging"])
        self.assertEqual(code, 0, out + err)
        self.assertTrue(path.read_bytes().startswith(good))
        self.assertNotIn(b'"seq": 99', path.read_bytes())
        # A corrupt middle line in another interrupted journal.
        os.chmod(self.workspace / "frontend/a.txt", 0o644)
        os.chmod(self.workspace / "frontend/b.txt", 0o644)
        code, out, err = self.apply(controller_class=self.seamed(fault=1))
        self.assertEqual(code, 1)
        second = self.last_apply()
        path = self.context.operations_dir / second / "permission-effects.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(lines[0][:-2] + b' }\n' + b"".join(lines[1:]))
        for flag, phrase in (("--resume", "RESUME"), ("--abandon", "ABANDON")):
            code, out, err = self.run_cli("permissions", "apply", flag, answers=[phrase + " PERMISSIONS staging"])
            self.assertEqual(code, 1)
            self.assertIn(f"ERROR: permissions-journal-invalid: The permission effect journal {path} is unreadable at "
                          "line 1", err)
        self.assertEqual(self.pending()["operation_id"], second)

    def test_pa8_a_crash_after_verify_or_after_the_operation_copy_is_approved_once_by_resume(self):
        for seam in ("after-verify", "after-approval-copy"):
            with self.subTest(seam=seam):
                if self.record_path.exists():
                    self.record_path.unlink()
                if self.pending() is not None:
                    self.context.journal_path.unlink()

                def hook(name, seam=seam):
                    if name == seam:
                        raise KeyboardInterrupt

                os.chmod(self.workspace / "frontend/app.txt", 0o644)
                code, out, err = self.apply(controller_class=self.seamed(hook=hook))
                self.assertEqual(code, 1)
                self.assertIn("permissions-interrupted", err)
                self.assertFalse(self.record_path.exists())
                operation = self.last_apply()
                code, out, err = self.run_cli("permissions", "apply", "--resume",
                                              answers=["RESUME PERMISSIONS staging"])
                self.assertEqual(code, 0, out + err)
                record = self.record()
                self.assertEqual((record["revision"], record["operation_id"]), (1, operation))
                self.assertEqual(len(record["confirmed_plans"]), 2)

    def test_pa9_a_crash_after_the_approval_closes_without_rewriting_and_abandon_is_refused(self):
        def hook(name):
            if name == "after-approval":
                raise KeyboardInterrupt

        operation, err = self.interrupted(hook=hook)
        self.assertIn("permissions-interrupted", err)
        written = (self.record_path.read_bytes(), os.lstat(str(self.record_path)).st_ino)
        before = tree_state(self.workspace, self.config_dir, self.backups)
        code, out, err = self.run_cli("permissions", "apply", "--abandon", answers=["ABANDON PERMISSIONS staging"])
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permissions-already-approved: Permission policy revision 1 was already written by this "
                      "apply; only '", err)
        self.assertEqual(tree_state(self.workspace, self.config_dir, self.backups), before)
        code, out, err = self.run_cli("permissions", "apply", "--resume", answers=["RESUME PERMISSIONS staging"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual((self.record_path.read_bytes(), os.lstat(str(self.record_path)).st_ino), written)
        self.assertIsNone(self.pending())
        self.assertEqual(self.outcome(self.last_apply())["approved_revision"], 1)

    def test_pa10_a_chmod_between_plan_and_apply_is_refused_and_before_values_stay_true(self):
        folder = self.scramble()
        target = folder / "manifest.json"
        actual = {}

        def hook(name):
            if name == "after-plan":
                for path in (self.backups / "revisions", self.backups / "revisions/partflow-staging", folder):
                    actual[str(path)] = (mode(path), owner(path)[1])
                os.chmod(target, 0o400)

        code, out, err = self.apply("--scope", "backups", controller_class=self.seamed(hook=hook))
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permissions-entry-changed: backups: revisions/partflow-staging/"
                      "20260909T120000Z-aaaaaaaaaaaa-abcdef/manifest.json changed during the apply", err)
        self.assertEqual(mode(target), 0o400)
        lines = self.effects(self.last_apply())
        executed = [line for line in lines if line["path"] != str(target.relative_to(self.backups))]
        for line in executed:
            path = str(self.backups / line["path"]) if line["path"] else str(self.backups)
            if path in actual:
                self.assertEqual((line["before_mode"], line["before_gid"]), actual[path])

    def test_pa11_a_crash_while_fenced_before_the_root_operation_abandons_with_zero_conflicts(self):
        original = mode(self.workspace)
        os.chmod(self.workspace / "frontend/app.txt", 0o644)
        os.chmod(self.workspace / "app-version.txt", 0o644)
        code, out, err = self.apply("--scope", "workspace", controller_class=self.seamed(fault=2))
        self.assertEqual(code, 1)
        operation = self.last_apply()
        kinds = [(line["kind"], line["path"]) for line in self.effects(operation)]
        self.assertEqual(kinds[0], ("fence", ""))
        self.assertEqual(kinds[-1], ("entry", ""))           # written ahead, never executed
        self.assertEqual(mode(self.workspace), 0o700)
        code, out, err = self.run_cli("permissions", "apply", "--abandon", answers=["ABANDON PERMISSIONS staging"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(mode(self.workspace), original)
        self.assertEqual((mode(self.workspace / "frontend/app.txt"), mode(self.workspace / "app-version.txt")),
                         (0o644, 0o644))
        self.assertEqual(self.outcome(self.last_apply())["result"], "abandoned")

    def test_pa12_entries_created_between_plan_and_apply(self):
        self.assertEqual(self.apply()[0], 0)
        os.chmod(self.config_dir, 0o2750)            # a root-only change: unfenced
        os.chmod(self.workspace / "frontend/app.txt", 0o644)

        def hook(name):
            if name == "after-plan":
                (self.config_dir / "new.txt").write_text("x")
                (self.workspace / "new.txt").write_text("y")
                os.chmod(self.workspace / "new.txt", 0o644)

        code, out, err = self.apply("--scope", "configuration", "--scope", "workspace",
                                    controller_class=self.seamed(hook=hook))
        self.assertEqual(code, 0, out + err)
        self.assertIn("permission-entry-unplanned: configuration: new.txt appeared after the plan", out)
        self.assertEqual(mode(self.workspace / "new.txt"), 0o660)   # fenced: in the authoritative inventory
        self.assertEqual(self.outcome(self.last_apply())["unplanned"], 1)

    def test_pa13_an_interrupt_between_chown_and_chmod_is_restored_by_abandon(self):
        calls = []

        def hook(name):
            if name == "between-chown-chmod":
                calls.append(name)
                raise KeyboardInterrupt

        self.assertEqual(self.apply()[0], 0)          # approves backups group root
        os.chown(self.backups, -1, gid_of("users"))
        os.chmod(self.backups, 0o700)
        code, out, err = self.apply("--scope", "backups", controller_class=self.seamed(hook=hook))
        self.assertEqual(code, 1)
        self.assertEqual(owner(self.backups)[1], 0)   # chowned, not chmodded
        code, out, err = self.run_cli("permissions", "apply", "--abandon", answers=["ABANDON PERMISSIONS staging"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual((mode(self.backups), owner(self.backups)[1]), (0o700, gid_of("users")))


# ============================================================================ RT: routes


class Routes(Instance):

    def test_rt1_dispatch_rows(self):
        rows = {name: (route.mutability, route.lock, route.trusted_launch, route.trusted_context, route.pending,
                       route.preflight, route.fail_closed, route.unattended, route.policy_class, route.handler)
                for name, route in pf.DISPATCH.items() if name.startswith("permissions")}
        self.assertEqual(rows, {
            "permissions check": ("read-only", False, True, False, "any", "none", "never", "allowed", "",
                                  "Controller.permissions_check"),
            "permissions plan": ("read-only", False, True, False, "any", "none", "never", "allowed", "",
                                 "Controller.permissions_plan"),
            "permissions apply": ("mutating", True, True, True, "permissions apply", "none", "never", "terminal", "",
                                  "Controller.permissions_apply"),
        })
        self.assertIn("permissions apply", pf.PENDING_ROUTES)
        self.assertEqual(pf.NO_CONFIG_ROUTES, frozenset({"config", "permissions apply"}))
        self.assertNotIn("permissions", {route.policy_class for route in pf.DISPATCH.values()})
        self.assertIn("permissions", pf.KNOWN_COMMANDS)

    def test_rt2_bare_permissions_is_refused_before_any_registry_read(self):
        for words in (["permissions"], ["permissions", "fix"]):
            with self.subTest(words=words), \
                    mock.patch.object(pf_instance, "load_registry", side_effect=AssertionError("read")):
                code, out, err = self.run_cli(*words)
                self.assertEqual(code, 2)
                self.assertIn("ERROR: permissions-verb-required: 'pf permissions' no longer changes anything by "
                              "itself.", err)
                self.assertIn("Nothing was read or changed.", err)

    def test_rt3_check_and_plan_take_no_lock_and_write_nothing(self):
        os.rmdir(str(self.context.state_dir))     # registration creates it empty; only a locked route may create it
        handle = pf_instance.acquire_instance_lock(self.context)
        try:
            before = tree_state(self.base)
            for verb in ("check", "plan"):
                with self.subTest(verb=verb):
                    code, out, err = self.run_cli("permissions", verb)
                    self.assertNotIn("instance-busy", err)
                    self.assertIn("Permission " + verb, out)
            self.assertFalse(self.context.state_dir.exists())
            self.assertEqual(tree_state(self.base), before)
        finally:
            handle.release()

    def test_rt4_to_rt6_terminal_pending_and_option_refusals(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                mock.patch.object(pf, "unattended", return_value=True):
            code = pf.main(["--instance", "staging", "permissions", "apply"], installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        self.assertEqual(code, 1)
        self.assertIn("terminal-required: 'permissions apply' asks for a typed confirmation", stderr.getvalue())
        update = pfx.migrating_update(self.context)
        code, out, err = self.apply(phrase=None)
        self.assertEqual(code, 1)
        self.assertIn(f"operation-open: operation {update['operation_id']} (update, phase migrating) is incomplete; "
                      "'permissions apply' is not a legal next action for it.", err)
        pfx.clear_operations(self.context)
        for extra in (["--resume", "--scope", "workspace"], ["--abandon", "--scope", "backups"],
                      ["--resume", "--abandon"]):
            with self.subTest(extra=extra):
                try:
                    code, out, err = self.run_cli("permissions", "apply", *extra)
                except SystemExit as exc:
                    code = exc.code
                self.assertEqual(code, 2)
        code, out, err = self.run_cli("permissions", "apply", "--resume")
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permissions-nothing-pending: No interrupted permission apply is open.", err)

    def test_rt7_installed_launcher_check_plan_apply_check(self):
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}

        def launcher(*arguments, answers=()):
            with pfx.typed_terminal(answers) as stdin:
                return subprocess.run([str(self.layout.launcher), "--instance", "staging", *arguments],
                                      env=environment, cwd="/", stdin=stdin, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, check=False, timeout=300)

        self.scramble()
        first = launcher("permissions", "check")
        self.assertEqual(first.returncode, 1, first.stdout + first.stderr)
        self.assertIn("permissions-differ", first.stderr)
        plan = launcher("permissions", "plan", "--details")
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        applied = launcher("permissions", "apply", answers=DEFAULTS + [APPLY_PHRASE])
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn("permissions-applied: Permission policy revision 1 applied.", applied.stdout)
        last = launcher("permissions", "check")
        self.assertEqual(last.returncode, 0, last.stdout + last.stderr)
        self.assertIn("Permissions match permission policy revision 1", last.stdout)
        record = json.loads((self.context.operations_dir / self.last_apply() / "operation.json").read_text())
        self.assertEqual(record["command"], "permissions apply")

    def test_rt8_doctor_permission_line_in_every_state(self):
        def line():
            out = self.run_cli("doctor")[1]
            found = [text for text in out.splitlines() if text.startswith("Permissions: ")]
            self.assertEqual(len(found), 1, out)
            self.assertNotIn("approved policy revision", found[0])
            return found[0]

        pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging", backup_read_group="root",
                         workspace_write_group="users")
        self.assertIn("Permissions: permission policy not approved; derived (backups/recovery groups from their "
                      "folders, workspace/configuration from pf-config.json); run '", line())
        self.assertEqual(self.apply()[0], 0)
        self.assertRegex(line(), r"\APermissions: permission policy revision 1 \(permission policy [0-9a-f]{12}\)\Z")
        pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging", backup_read_group="users",
                         workspace_write_group="users")
        self.assertIn("| proposals: backups root -> users, recovery root -> users", line())
        os.chmod(self.workspace / "frontend/app.txt", 0o604)
        self.apply("--scope", "workspace", controller_class=self.seamed(fault=1))
        self.assertIn("| interrupted apply ", line())
        self.context.journal_path.unlink()
        os.chmod(self.record_path, 0o644)
        self.assertIn("Permissions: unavailable: permission-approval-invalid", line())

    def test_rt10_plan_output_contract(self):
        pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging", backup_read_group="users",
                         workspace_write_group="users")
        code, out, err = self.run_cli("permissions", "plan")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(out.splitlines()[0],
                         "Permission plan for instance staging — permission policy not approved (derived)")
        self.assertIn("  Members of users: ", out)
        self.assertEqual(out.count("  " + pf.BACKUP_GROUP_CONSEQUENCE), 4)   # effective and candidate blocks
        self.assertIn("== With pf-config.json proposals == candidate (not approved;", out)
        effective_hash = [text for text in out.splitlines() if text.startswith("Plan hash: ")][0]
        code, applied, err = self.apply()
        self.assertEqual(code, 0, applied + err)
        self.assertIn(effective_hash, applied)
        pfx.admin_config(self.config_dir / "pf-config.json", project="partflow-staging", backup_read_group="root",
                         workspace_write_group="users")
        code, out, err = self.run_cli("permissions", "plan")
        self.assertNotIn("== With pf-config.json proposals ==", out)

    def test_rt11_apply_reaches_the_wizard_with_an_invalid_env_and_freezes_no_snapshot(self):
        (self.config_dir / ".env").write_text("NOT A VALID LINE\n")
        code, out, err = self.apply(answers=["q"], phrase=None)
        self.assertEqual(code, 1)
        self.assertIn("ERROR: permissions-cancelled: Cancelled; no permission was changed and no policy was approved.",
                      err)
        self.assertIn("Permission policy wizard", out)
        self.assertEqual(sorted(os.listdir(str(self.context.operations_dir / self.last_apply()))), ["operation.json"])


# ============================================================================ FL: lifecycle flows


class Flows(Instance):

    def fake(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_pf_admin
        controller = test_pf_admin.FakeController(self.context)
        return controller

    def test_fl1_snapshot_publishes_the_fresh_checkpoint_only(self):
        import test_pf_admin
        controller = self.fake()
        pfx.deployed_record(self.context)
        unrelated = self.backups / "operator-notes.txt"
        unrelated.write_text("x")
        os.chmod(unrelated, 0o600)
        with contextlib.redirect_stdout(io.StringIO()):
            checkpoint = test_pf_admin.checkpoint(controller)
        folder = controller.backups_dir / checkpoint.bundle_id
        for path in (folder, *folder.iterdir()):
            expected = 0o750 if path.is_dir() else 0o640
            self.assertEqual((mode(path), owner(path)), (expected, (0, 0)), path)
        self.assertEqual(mode(unrelated), 0o600)

    def test_fl3_restore_checkpoints_and_fl9_restored_state_files(self):
        controller = self.fake()
        bundle = self.base / "bundle"
        source = self.base / "archive-src" / "partflow-staging" / "20260909T120000Z-aaaaaaaaaaaa-abcdef"
        source.mkdir(parents=True)
        (source / "manifest.json").write_text("{}")
        os.chmod(source / "manifest.json", 0o604)
        bundle.mkdir()
        counts = controller.create_tree_archive(source.parent, bundle / "revision-checkpoints.tar.gz",
                                                "partflow-staging")
        data = (bundle / "revision-checkpoints.tar.gz").read_bytes()
        # PF-A3.1: the history is imported from a strictly read bundle view through the safe importer.
        view = pf.BundleView(bundle, {"bundle_id": "purge-20260910T120000Z-" + pfx.OLD[:12] + "-abcdef", "payloads": [
            {"path": "revision-checkpoints.tar.gz", "type": "checkpoint_history", "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest(), "store": None, "sensitive": False,
             "expanded_bytes": counts[0], "members": counts[1], "members_sha256": counts[2]}]}, "0" * 64, None,
            "data_restore_verified")
        with controller.lock():
            controller.restore_revision_checkpoints(view)
            restored = controller.backups_dir / source.name
            self.assertEqual((mode(restored), mode(restored / "manifest.json")), (0o750, 0o640))
            state = bundle / "state"
            state.mkdir()
            (state / "last-reset.json").write_text("{}")
            os.chmod(state / "last-reset.json", 0o640)
            pf.copy_fresh(state / "last-reset.json", controller.state / "last-reset.json")
            controller.publish_fresh("private_state", controller.state / "last-reset.json")
        self.assertEqual((mode(controller.state / "last-reset.json"), owner(controller.state / "last-reset.json")[0]),
                         (0o600, 0))

    def test_fl4_the_workspace_switch_designates_from_the_manifest_and_retains_concurrent_entries(self):
        # PF-A3.2 (former replace_source test): W1 publishes the staged tree with the policy targets and the
        # manifest's designated executables; an editor's file written into the old workspace while the switch runs
        # stays, untouched, in the retained generation (never re-permissioned).
        controller = self.fake()
        candidate = self.base / "candidate"
        pfx.source_fixture(candidate, pfx.NEW)
        script = candidate / "scripts/start.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\n")
        os.chmod(script, 0o755)
        real_retain = controller.w2_retain

        def retain(step):
            editor = self.workspace / "frontend/editor.txt"
            editor.write_text("mine")
            os.chown(editor, EDITOR_UID, -1)
            os.chmod(editor, 0o600)
            return real_retain(step)

        output = io.StringIO()
        with mock.patch.object(controller, "w2_retain", retain), contextlib.redirect_stdout(output), controller.lock():
            pfx.run_workspace_switch(controller, candidate, pfx.NEW, verified=False)
            generation = controller.plan["workspace"]["generation_id"]
        self.assertEqual(mode(self.workspace / "scripts/start.sh"), 0o770)
        self.assertEqual(mode(self.workspace / "app-version.txt"), 0o660)
        self.assertEqual(mode(self.workspace / "frontend"), 0o2770)
        self.assertFalse((self.workspace / "frontend/editor.txt").exists())
        retained = pf_instance.generation_container(self.context) / generation
        self.assertEqual(mode(retained / "frontend/editor.txt"), 0o600)
        self.assertEqual(owner(retained / "frontend/editor.txt")[0], EDITOR_UID)

    def test_fl5_restore_env_and_fl6_the_reuse_branch_touch_only_env(self):
        controller = self.fake()
        other = self.config_dir / "notes.txt"
        other.write_text("x")
        os.chmod(other, 0o600)
        controller.restore_runtime_environment(pfx.ENV_TEXT.encode())  # audit AF-1: the verified payload bytes
        env = self.config_dir / ".env"
        self.assertEqual((mode(env), owner(env)), (0o660, (0, gid_of("users"))))
        self.assertEqual(mode(other), 0o600)
        env.write_text(pfx.ENV_TEXT.replace("abc123", "b" * 40))
        os.chmod(env, 0o600)
        os.chown(env, -1, 0)
        (controller.state / "deployed.json").unlink() if (controller.state / "deployed.json").exists() else None
        with mock.patch.object(pf, "prompt_yes_no", return_value=True), contextlib.redirect_stdout(io.StringIO()), \
                controller.lock():
            controller.prepare_new_env()
        self.assertEqual((mode(env), owner(env)[1]), (0o660, gid_of("users")))
        self.assertEqual(mode(other), 0o600)

    def test_fl7_the_writer_creates_from_the_approved_configuration_target(self):
        answers = list(DEFAULTS)
        answers[5] = "2"           # configuration: View and copy
        self.assertEqual(self.apply(answers=answers)[0], 0)
        controller = self.controller()
        self.assertEqual(controller.configuration_create_target("users"), (gid_of("users"), 0o640))
        target = self.config_dir / "created.txt"
        pf.write_editable_file(target, b"x\n", expected=None, expected_identity=None, create_gid=gid_of("users"),
                               op8="0123abcd", create_mode=0o640)
        self.assertEqual((mode(target), owner(target)), (0o640, (0, gid_of("users"))))

    def test_fl8_deploy_current_no_longer_changes_workspace_permissions(self):
        source = (PACKAGE / "pf-admin.py").read_text(encoding="utf-8")
        body = source[source.index("    def deploy(self, target=None"):source.index("    def confirmation_ref(")]
        self.assertIn("Workspace permissions were not changed; check them with", body)
        self.assertNotIn("publish_", body)
        self.assertNotIn("os.chmod", body)

    def test_fl10_an_inherited_access_acl_stops_protected_flows_and_is_a_note_in_the_workspace(self):
        controller = self.fake()
        with controller.lock():
            controller.ensure_backup_tree()
        set_xattr(controller.backups_dir, "system.posix_acl_default", DEFAULT_ACL)
        folder = controller.backups_dir / "20261007T000000Z-aaaaaaaaaaaa-00000a"
        folder.mkdir()
        (folder / "manifest.json").write_text("{}")
        if "system.posix_acl_access" not in xattrs(folder / "manifest.json"):
            raise unittest.SkipTest("the filesystem does not inherit default ACLs")
        with self.assertRaisesRegex(pf.Failure, "fresh-entry-acl: backups: new entry .* inherited an ACL"):
            controller.publish_fresh("backups", folder)
        set_xattr(self.workspace, "system.posix_acl_default", DEFAULT_ACL)
        (self.workspace / "fresh.txt").write_text("x")
        created = mode(self.workspace / "fresh.txt")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            controller.publish_fresh("workspace", self.workspace / "fresh.txt")
        self.assertIn("fresh-entry-acl: workspace: new entry fresh.txt has an ACL; its mode was left as created.",
                      output.getvalue())
        self.assertEqual(mode(self.workspace / "fresh.txt"), created)
