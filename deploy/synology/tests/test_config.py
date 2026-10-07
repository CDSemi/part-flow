"""PF-A2.2 acceptance and regression tests: versioned admin configuration, explicit migration and the config wizards.

Case mapping (PF-A2.2 SPEC section 6.1; design r3 acceptance cases A2-T05, A2-T06 and the A1-T08 re-run):
  SC-1..SC-3, BF-1   wire schemas, marker refactor, frozen bootstrap  -> Schema, MarkerGolden
  EX-1               admin-config examples                           -> Examples
  AV-1..AV-4         versioned strict parsing (A2-T05)                -> AdminValidation
  AM-1..AM-2         explicit migration 1 -> 2 (A2-T05)               -> AdminMigration
  AW-A1..AW-A12      admin wizard through main()/the launcher         -> AdminWizard
  PR-1..PR-8         pre-registration mode                            -> PreRegistration
  AD-1..AD-2         profile declarations (S9)                        -> AppDeclaration
  AP-1..AP-11        app-variable wizard (A2-T06)                     -> AppWizard
  ZD-1..ZD-4         host zone data                                   -> ZoneData
  ER-1..ER-3, UR-1   .env rewrite and URL round-trip (A1-T08)         -> EnvRewrite
  CC-1..CC-4         locks and concurrent writers                     -> Concurrency
  CR-1..CR-3         forked crash windows of the writer               -> Crash
  AU-1..AU-9         PF-A2.2 audit regressions (audit-findings.json)  -> AuditRegressions
  SK-1..SK-2         A2.1 smoke against an A2.2 candidate             -> SmokeCompatibility
  PO-1               policy versioned separately                      -> Policy
  LC-1               schema 2 lifecycle subset                        -> Schema2Lifecycle*, Schema2Purge

Everything runs as uid 0 in disposable temporary roots. No Docker daemon, NAS, launcher outside the fixture root or
running stack is touched; the wizards start no Git, Docker or Compose child (Direct LAN may run the read-only
``ip``/``hostname`` detection, mocked in these tests). Group detection is read-only (``grp``); no group is ever
created.
"""
import contextlib
import grp
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_config = pf.pf_config
pf_install = pf.pf_install
pf_instance = pfx.pf_instance
pf_bootstrap = pfx.pf_bootstrap
PACKAGE = pfx.PACKAGE
REPO_PACKAGE = pfx.REPO_PACKAGE
GROUP = grp.getgrgid(os.getgid()).gr_name
PROJECT = "partflow-staging"
ROOT_REQUIRED = unittest.skipUnless(os.geteuid() == 0, "protected fixtures require root ownership (uid 0)")
EXAMPLES = PACKAGE / "contracts" / "examples" / "admin-config"
AP1_PASSWORD = 'p@ss:w/rd#$%"\\x 2026-mật-khẩu-0123456789abcdef'
# A1-T08 password set: every value the strict parser accepts literally and the URL must round-trip.
A1_T08_PASSWORDS = ("abc123", "p@ss:w/rd", "100%sure", "a b c", 'q"uote', "back\\slash", "$HOME", "#hash",
                    AP1_PASSWORD, "x" * 64)


def users_gid():
    try:
        return grp.getgrnam("users").gr_gid
    except KeyError:
        raise unittest.SkipTest("the image has no 'users' group (the wizard default); groups are never created")


class Script:
    """Scripted ``input``: records each prompt; EOFError/KeyboardInterrupt entries raise; callables are called."""

    def __init__(self, answers, sink):
        self.answers = list(answers)
        self.sink = sink
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        self.sink.write(prompt + "\n")
        if not self.answers:
            raise EOFError
        item = self.answers.pop(0)
        if item in (EOFError, KeyboardInterrupt):
            raise item
        return item() if callable(item) else item


def env_text(**values):
    """Fixture .env text in APP_KEYS order; a value None omits the key, a str is written as given."""
    base = {"POSTGRES_USER": "partflow_staging", "POSTGRES_PASSWORD": "a" * 64, "POSTGRES_DB": "partflow_staging",
            "SITE_TIMEZONE": "UTC", "PARTFLOW_BIND_IP": "127.0.0.1", "PARTFLOW_HTTP_PORT": "5173",
            "PARTFLOW_ALLOWED_HOST": "partflow.internal.example"}
    base.update(values)
    return "".join(f"{key}={value}\n" for key, value in base.items() if value is not None)


def host_zone(name):
    status, _ = pf_config.zone_status(name)
    if status != "ok":
        raise unittest.SkipTest(f"the image's installed zone data has no {name} ({status})")


class ConfigBase(unittest.TestCase):
    """A protected installation with one registered, never-deployed instance ``staging`` (schema 1 config)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.layout = pfx.install_root(self.base)
        self.paths = pfx.data_home(self.base / "staging", project=PROJECT, group=GROUP)
        self.context = pfx.register(self.layout, "staging", self.paths, project=PROJECT)
        self.config_dir = self.paths["configuration"]
        self.config_path = self.config_dir / "pf-config.json"
        self.env_path = self.config_dir / ".env"

    def tearDown(self):
        for current, dirs, files in os.walk(self.base):
            for name in dirs + files:
                path = Path(current) / name
                if not path.is_symlink():
                    try:
                        os.chmod(path, 0o700 if path.is_dir() else 0o600)
                    except OSError:
                        pass
        self.temp.cleanup()

    def run_main(self, arguments, answers=(), *, unattended=False):
        """In-process main() with scripted terminal answers; returns (code, stdout with prompts, stderr, script)."""
        stdout, stderr = io.StringIO(), io.StringIO()
        script = Script(answers, stdout)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                mock.patch.object(pf, "unattended", return_value=unattended), \
                mock.patch("builtins.input", script):
            code = pf.main(arguments, installation_root=self.layout.root, running_release=self.layout.release_dir,
                           trusted_launch=True)
        return code, stdout.getvalue(), stderr.getvalue(), script

    def cli(self, arguments, answers=()):
        """The installed launcher (<root>/bootstrap/pf) with a pty whose answers are typed in advance."""
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        with pfx.typed_terminal(answers) as stdin:
            return subprocess.run([str(self.layout.launcher), *arguments], env=environment, cwd=str(self.base),
                                  stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
                                  timeout=300)

    def admin(self, answers=(), **kwargs):
        return self.run_main(["--instance", "staging", "config", "admin"], answers, **kwargs)

    def app(self, answers=(), **kwargs):
        return self.run_main(["--instance", "staging", "config", "app"], answers, **kwargs)

    def audits(self):
        root = self.context.operations_dir
        found = []
        for name in sorted(os.listdir(str(root))):
            path = root / name / pf_config.CONFIG_CHANGE_NAME
            if path.exists():
                found.append(json.loads(path.read_text(encoding="utf-8")))
        return found

    def assert_valid_record(self, record):
        errors = pf_install.validate_marked(record, pf_config.CONFIG_CHANGE_SCHEMA["$defs"]["record"],
                                           defs=pf_config.CONFIG_CHANGE_SCHEMA["$defs"])
        self.assertEqual(errors + pf_config.config_change_problems(record), [])

    def temps(self, directory=None):
        directory = Path(directory or self.config_dir)
        return sorted(name for name in os.listdir(str(directory)) if ".pf-config-" in name)

    def file_state(self, path):
        info = os.lstat(str(path))
        return (Path(path).read_bytes(), info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
                info.st_mtime_ns)

    def deployed(self):
        pfx.deployed_record(self.context)


# ============================================================================ SC / BF: schemas


class Schema(unittest.TestCase):

    def test_sc1_admin_schema_contract_equals_the_embedded_copy_and_the_subset_validator_accepts_it(self):
        contract = json.loads((PACKAGE / "contracts" / "admin-config.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(contract, pf_config.ADMIN_CONFIG_SCHEMA)
        document = json.loads((EXAMPLES / "new-install.json").read_text(encoding="utf-8"))
        self.assertEqual(pf_instance.validate_against_schema(document, pf_config.ADMIN_CONFIG_SCHEMA), [])
        self.assertEqual(pf_config.ADMIN_CONFIG_SCHEMA["required"], ["schema_version", *pf_config.ADMIN_CONFIG_KEYS])
        for key, rule in pf_config.ADMIN_CONFIG_SCHEMA["properties"].items():
            if "pattern" in rule:
                with self.subTest(key=key):
                    self.assertTrue(pf_instance._anchored_fullmatch(rule["pattern"], document[key]))
        self.assertFalse(pf_instance._anchored_fullmatch(pf_config.ADMIN_CONFIG_SCHEMA["properties"]["branch"]["pattern"],
                                                         "release/"))
        for bad in (" users", "users ", "a:b", "x\ty", "", "u" * 65):
            with self.subTest(group=bad):
                self.assertFalse(pf_instance._anchored_fullmatch(
                    pf_config.ADMIN_CONFIG_SCHEMA["properties"]["backup_read_group"]["pattern"], bad))
        self.assertTrue(pf_instance._anchored_fullmatch(
            pf_config.ADMIN_CONFIG_SCHEMA["properties"]["backup_read_group"]["pattern"], "Domain Users"))

    def full_record(self, **changes):
        record = {"schema_version": 1, "operation_id": "20261007T000000Z-config-0123abcd", "completed": "20261007T000001Z",
                  "file": "pf-config.json", "mode": "migrate", "profile_id": None, "schema_before": 1,
                  "schema_after": 2, "before_sha256": "a" * 64, "after_sha256": "b" * 64,
                  "changes": [{"key": "branch", "action": "kept", "before": "main", "after": "main"},
                              {"key": "auto_update", "action": "materialized", "before": None, "after": False},
                              {"key": "minimum_free_mb", "action": "kept", "before": 1, "after": 1}]}
        record.update(changes)
        return record

    def marked(self, record):
        return pf_install.validate_marked(record, pf_config.CONFIG_CHANGE_SCHEMA["$defs"]["record"],
                                          defs=pf_config.CONFIG_CHANGE_SCHEMA["$defs"])

    def test_sc2_config_change_contract_markers_and_cross_field_rules(self):
        contract = json.loads((PACKAGE / "contracts" / "config-change.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(contract, pf_config.CONFIG_CHANGE_SCHEMA)
        for name, definition in pf_config.CONFIG_CHANGE_SCHEMA["$defs"].items():
            with self.subTest(definition=name):
                pf_instance.validate_against_schema({}, definition)  # no unsupported keyword (raises otherwise)
        self.assertEqual(self.marked(self.full_record()), [])
        self.assertEqual(pf_config.config_change_problems(self.full_record()), [])
        refused = {
            "profile_id int": self.full_record(profile_id=3),
            "sha not hex": self.full_record(before_sha256="Z" * 64),
            "unknown action": self.full_record(changes=[{"key": "branch", "action": "edited", "before": None,
                                                         "after": "x"}]),
            "list as scalar": self.full_record(changes=[{"key": "branch", "action": "kept", "before": ["x"],
                                                         "after": "x"}]),
            "float as scalar": self.full_record(changes=[{"key": "branch", "action": "kept", "before": 1.5,
                                                          "after": "x"}]),
            "bad operation id": self.full_record(operation_id="20261007T000000Z-deploy-0123abcd"),
        }
        for label, record in refused.items():
            with self.subTest(refused=label):
                self.assertTrue(self.marked(record))
        accepted = self.full_record(changes=[{"key": "auto_update", "action": "kept", "before": True, "after": True}])
        self.assertEqual(self.marked(accepted), [])
        env = self.full_record(file=".env", mode="complete", profile_id="partflow-staging-legacy", schema_before=None,
                               schema_after=None, before_sha256=None, after_sha256=None,
                               changes=[{"key": "POSTGRES_PASSWORD", "action": "unchanged", "before": None,
                                         "after": None}])
        self.assertEqual(self.marked(env) + pf_config.config_change_problems(env), [])
        cross = {
            "secret value": dict(env, changes=[{"key": "POSTGRES_PASSWORD", "action": "set", "before": None,
                                                "after": "x"}]),
            "secret kept": dict(env, changes=[{"key": "POSTGRES_PASSWORD", "action": "kept", "before": None,
                                               "after": None}]),
            "env hash": dict(env, after_sha256="b" * 64),
            "env without profile": dict(env, profile_id=None),
            "admin with profile": self.full_record(profile_id="x"),
            "set on a plain key": self.full_record(changes=[{"key": "branch", "action": "set", "before": None,
                                                             "after": "x"}]),
            "created with a before hash": self.full_record(mode="create", schema_before=None),
        }
        for label, record in cross.items():
            with self.subTest(cross=label):
                self.assertTrue(pf_config.config_change_problems(record))

    def test_bf1_bootstrap_launcher_and_install_contract_are_frozen(self):
        def sha(path):
            return pf_instance.sha256_bytes(Path(path).read_bytes())
        self.assertEqual(sha(PACKAGE / "pf_bootstrap.py"),
                         "84a824c8b73d0792b487e03dbf9d0374e08397fef64ac06a7b3944755c840281")
        self.assertEqual(sha(REPO_PACKAGE / "pf.sh"), "aadc41db5fa50478fe152d2b63432281e51c9d21dd108913a8f81395109e3532")
        self.assertEqual(sha(PACKAGE / "contracts" / "install-operation.schema.json"),
                         "3c8121d92777ea37010f3ca2f42681e58047d0cb9276f08f913340d9bceaf7a2")
        self.assertEqual((pf_install.INSTALL_CONTRACT, pf_install.INSTALL_SCHEMA_VERSION), (1, 1))
        self.assertEqual(pf_bootstrap.REQUIRED_RELEASE_FILES,
                         ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "pf_runner.py", "pf_config.py",
                          "pf_source.py", "pf_docker.py", "pf_install.py", "compose.nas.yaml"))
        self.assertEqual(set(pf_install.CONTROL_RELEASE_FILES),
                         set(pf_bootstrap.REQUIRED_RELEASE_FILES) | {"pf-config.example.json", "nas.env.example"})
        self.assertEqual(pf_install.CONTROL_RELEASE_FILES["pf-config.example.json"],
                         "deploy/synology/pf-config.example.json")

    def test_examples_shipped_file_is_schema_2_and_only_gains_schema_version(self):
        data = (PACKAGE / "pf-config.example.json").read_bytes()
        parsed = pf_config.parse_admin_config(data, label="example")
        self.assertEqual((parsed.schema_version, parsed.problems), (2, ()))
        legacy = data.replace(b'  "schema_version": 2,\n', b"", 1)
        self.assertEqual(pf_config.validate_admin_config(legacy, label="x")[0],
                         dict(pf_config.ADMIN_CONFIG_DEFAULTS))
        self.assertIn(b"# 'pf config app' (or the first 'pf deploy') creates <configuration>/.env from this file.\n",
                      (PACKAGE / "nas.env.example").read_bytes())


# ============================================================================ EX: examples


class Examples(unittest.TestCase):

    def test_ex1_every_case_row_has_its_outcome(self):
        cases = json.loads((EXAMPLES / "cases.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(row["file"] for row in cases),
                         sorted(path.name for path in EXAMPLES.iterdir() if path.suffix == ".json"
                                and path.name != "cases.json"))
        for row in cases:
            expect = row["expect"]
            with self.subTest(file=row["file"]):
                data = (EXAMPLES / row["file"]).read_bytes()
                if row["file"] == "conflicting-path.json":
                    self.assertEqual(expect["code"], "config-path-conflict")  # driven by PR-2
                    continue
                parsed = pf_config.parse_admin_config(data, label=row["file"])
                self.assertEqual(parsed.schema_version, expect["schema_version"])
                self.assertEqual(parsed.code, expect["code"])
                self.assertEqual(list(parsed.implicit), expect["implicit"])
                if expect["code"] is None:
                    self.assertEqual(parsed.problems, ())
                if "migrated_from" in expect:
                    source = pf_config.parse_admin_config((EXAMPLES / expect["migrated_from"]).read_bytes(), label="v1")
                    rendered = pf_config.render_admin_config(pf_config.migrate_admin_config(source))
                    self.assertEqual(rendered, data)
                    for key in source.implicit:
                        self.assertEqual(parsed.values[key], pf_config.ADMIN_CONFIG_DEFAULTS[key])
                if "group_missing" in expect:
                    self.assertEqual(parsed.values[expect["group_missing"]], "nas-editors")
        self.assertIn("not shipped", (EXAMPLES / "README.md").read_text(encoding="utf-8"))
        self.assertFalse(set(pf_install.CONTROL_RELEASE_FILES.values()) & {"deploy/synology/contracts/" + name for name in
                                                                          ("admin-config.schema.json",
                                                                           "config-change.schema.json")})


# ============================================================================ AV: versioned validation (A2-T05)


def doc2(**changes):
    document = pf_config.admin_document(dict(pf_config.ADMIN_CONFIG_DEFAULTS))
    document.update(changes)
    return document


class AdminValidation(unittest.TestCase):

    def test_av1_schema_1_keeps_the_a21_tuples_and_messages(self):
        validate = pf_config.validate_admin_config
        config, problems = validate(b'{"project": "partflow-x", "environment": "staging"}', label="cfg")
        self.assertEqual((config, problems), (dict(pf_config.ADMIN_CONFIG_DEFAULTS, project="partflow-x"), []))
        bad = json.dumps({"zz": 1, "repository": "x/y", "project": "Bad", "release_channel": "beta", "auto_update": 1,
                          "health_timeout_seconds": 0, "minimum_free_mb": True, "backup_read_group": " ",
                          "workspace_write_group": 3}).encode()
        config, problems = validate(bad, label="cfg")
        self.assertEqual(problems, [
            "Unknown configuration keys: zz", "This controller is scoped to CDSemi/part-flow.",
            "Invalid Compose project name.", "release_channel must be stable or prerelease.",
            "auto_update must be a JSON boolean.", "health_timeout_seconds must be a positive integer.",
            "minimum_free_mb must be a positive integer.", "backup_read_group must be a non-empty DSM group name.",
            "workspace_write_group must be a non-empty DSM group name."])
        self.assertEqual(config["zz"], 1)  # the A2.1 return: merged config even with problems
        self.assertEqual(validate(b"[]", label="cfg"), (None, ["Runtime configuration must be a JSON object: cfg"]))
        parsed = pf_config.parse_admin_config(b'{"project": "partflow-x"}', label="cfg")
        self.assertEqual((parsed.schema_version, parsed.code), (1, None))
        self.assertEqual(parsed.implicit, tuple(key for key in pf_config.ADMIN_CONFIG_KEYS if key != "project"))

    def test_av2_schema_2_refuses_every_shape_problem_with_ordered_messages(self):
        def problems(document):
            raw = document if isinstance(document, bytes) else json.dumps(document).encode()
            parsed = pf_config.parse_admin_config(raw, label="cfg")
            self.assertIsNone(parsed.values)
            self.assertEqual(parsed.code, "admin-config-invalid")
            return list(parsed.problems)

        self.assertEqual(problems(b'{"schema_version": 2, "branch": "a", "branch": "b"}'),
                         ["cfg: Duplicate JSON object key: branch"])
        self.assertEqual(problems(b'{"schema_version": 2, "minimum_free_mb": NaN}'),
                         ["cfg: Non-finite JSON number is not allowed: NaN"])
        self.assertEqual(problems(dict(doc2(), extra=1)), ["Unknown configuration keys: extra"])
        missing = doc2()
        del missing["ci_workflow"], missing["branch"]
        self.assertEqual(problems(missing), ["Missing configuration keys (schema 2 lists every key): branch, ci_workflow"])
        cases = {"auto_update": "false", "minimum_free_mb": 0, "health_timeout_seconds": True, "branch": "a..b",
                 "backup_read_group": " users", "repository": "other/repo"}
        for key, value in cases.items():
            with self.subTest(key=key):
                self.assertEqual(problems(doc2(**{key: value})), [f"{key}: {pf_config.ADMIN_RULES[key]}"])
        several = dict(doc2(branch="a..b", auto_update="no"), zz=1)
        del several["project"]
        self.assertEqual(problems(several), [
            "Unknown configuration keys: zz", "Missing configuration keys (schema 2 lists every key): project",
            "branch: " + pf_config.ADMIN_RULES["branch"], "auto_update: must be a JSON boolean"])
        self.assertEqual(pf_config.ADMIN_RULES["auto_update"], "must be a JSON boolean")
        self.assertEqual(pf_config.ADMIN_RULES["repository"], 'must be "CDSemi/part-flow"')
        self.assertEqual(pf_config.ADMIN_RULES["release_channel"], "must be one of stable, prerelease")

    def test_av3_unsupported_versions_are_one_problem(self):
        for value in (3, "2", 2.0, True, 1, None):
            with self.subTest(value=value):
                raw = json.dumps(dict(doc2(), schema_version=value)).encode()
                parsed = pf_config.parse_admin_config(raw, label="cfg")
                self.assertEqual((parsed.code, parsed.values, len(parsed.problems)),
                                 ("admin-config-version-unsupported", None, 1))
                self.assertIn(f"declares schema_version {json.dumps(value)}", parsed.problems[0])
                self.assertEqual(pf_config.validate_admin_config(raw, label="cfg")[0], None)

    def test_av4_validate_admin_config_shape_for_both_schemas(self):
        for raw in (b'{"project": "p1"}', json.dumps(doc2(project="p1")).encode()):
            with self.subTest(raw=raw[:20]):
                config, problems = pf_config.validate_admin_config(raw, label="cfg")
                self.assertEqual(problems, [])
                self.assertEqual((config["project"], config["environment"]), ("p1", "staging"))
                self.assertNotIn("schema_version", config)
                self.assertEqual(set(config), set(pf_config.ADMIN_CONFIG_KEYS))


# ============================================================================ AM: migration (A2-T05)


class AdminMigration(unittest.TestCase):

    def test_am1_explicit_values_kept_and_implicit_values_frozen_not_from_the_example(self):
        raw = json.dumps({"branch": "release/2.5", "minimum_free_mb": 4096, "workspace_write_group": "users",
                          "project": "p1"}).encode()
        parsed = pf_config.parse_admin_config(raw, label="cfg")
        # The shipped example may change its defaults in a later release; the migration is a pure function of the
        # file and the frozen schema 1 values (no example input), so such a change cannot reach an instance.
        document = pf_config.migrate_admin_config(parsed)
        self.assertEqual(pf_config.ADMIN_CONFIG_DEFAULTS, {
            "repository": "CDSemi/part-flow", "branch": "main", "project": "partflow-staging",
            "environment": "staging", "release_channel": "stable", "auto_update": False, "ci_workflow": "ci.yml",
            "health_timeout_seconds": 180, "minimum_free_mb": 2048, "backup_read_group": "users",
            "workspace_write_group": "users"})
        self.assertEqual(document["schema_version"], 2)
        for key in ("branch", "minimum_free_mb", "workspace_write_group", "project"):
            self.assertEqual(document[key], json.loads(raw)[key])
        for key in parsed.implicit:
            self.assertEqual(document[key], pf_config.ADMIN_CONFIG_DEFAULTS[key])
        rendered = pf_config.render_admin_config(document)
        self.assertEqual(list(json.loads(rendered)), ["schema_version", *pf_config.ADMIN_CONFIG_KEYS])
        self.assertEqual(pf_config.admin_document(pf_config.parse_admin_config(rendered, label="r").values), document)
        rows = pf_config.admin_config_changes(parsed, document, asked={})
        self.assertEqual({row["key"]: row["action"] for row in rows if row["action"] == "materialized"},
                         {key: "materialized" for key in parsed.implicit})

    def test_am2_an_explicit_value_invalid_under_schema_2_blocks_the_migration(self):
        parsed = pf_config.parse_admin_config(b'{"branch": "bad branch", "ci_workflow": "a/b"}', label="cfg")
        with self.assertRaises(pf_config.ConfigError) as caught:
            pf_config.migrate_admin_config(parsed)
        self.assertTrue(str(caught.exception).startswith("admin-config-migration-blocked: branch: "))
        self.assertIn("; ci_workflow: ", str(caught.exception))


# ============================================================================ AW: admin wizard (installed CLI)


@ROOT_REQUIRED
class AdminWizard(ConfigBase):

    def test_aw_a1_absent_file_create_through_the_installed_launcher(self):
        gid = users_gid()
        self.config_path.unlink()
        result = self.cli(["--instance", "staging", "config", "admin"], ["1", "users", "y"])
        transcript = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, transcript)
        self.assertIn("Groups on this host:\n    1. users (gid %d)" % gid, result.stdout)
        self.assertEqual(result.stdout.count("Choose a number or type a group name [1]"), 2)
        self.assertIn("Admin configuration " + str(self.config_path) + " (create)", result.stdout)
        expected = pf_config.render_admin_config(doc2(project=PROJECT, workspace_write_group="users",
                                                      backup_read_group="users"))
        self.assertEqual(self.config_path.read_bytes(), expected)
        self.assertEqual(expected, (EXAMPLES / "new-install.json").read_bytes())
        info = os.lstat(str(self.config_path))
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (0, gid, 0o660))
        records = self.audits()
        self.assertEqual(len(records), 1)
        self.assert_valid_record(records[0])
        self.assertEqual((records[0]["mode"], records[0]["schema_before"]), ("create", None))
        self.assertEqual({row["key"]: row["action"] for row in records[0]["changes"]}["workspace_write_group"], "added")
        # S9: no application question. The mandated backup-group consequence line (PERMISSIONS section 5) is the only
        # place the word "database" may appear.
        self.assertEqual(transcript.count(pf.BACKUP_GROUP_CONSEQUENCE), 1)
        for word in ("POSTGRES", "PostgreSQL", "database", "timezone"):
            self.assertNotIn(word, transcript.replace(pf.BACKUP_GROUP_CONSEQUENCE, ""))
        self.assertEqual(self.temps(), [])

    def test_aw_a2_schema_1_migrates_without_questions_and_keeps_owner_and_mode(self):
        os.chmod(self.config_path, 0o640)
        os.chown(self.config_path, 0, os.getgid())
        before = os.lstat(str(self.config_path))
        result = self.cli(["--instance", "staging", "config", "admin"], ["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Choose a number", result.stdout)
        self.assertIn("schema_version: (none) -> 2", result.stdout)
        self.assertIn("health_timeout_seconds: 180 (implicit legacy default, now explicit)", result.stdout)
        parsed = pf_config.parse_admin_config(self.config_path.read_bytes(), label="x")
        self.assertEqual(parsed.schema_version, 2)
        after = os.lstat(str(self.config_path))
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (before.st_uid, before.st_gid, 0o640))
        record = self.audits()[0]
        self.assert_valid_record(record)
        self.assertEqual((record["mode"], record["schema_before"], record["schema_after"]), ("migrate", 1, 2))
        self.assertIn("materialized", {row["action"] for row in record["changes"]})

    def test_aw_a3_missing_group_is_asked_with_the_list_and_no_default(self):
        gid = users_gid()
        pfx.admin_config(self.config_path, project=PROJECT, environment="staging", backup_read_group=GROUP,
                         workspace_write_group="nas-old")
        group_file = Path("/etc/group").read_bytes()
        result = self.cli(["--instance", "staging", "config", "admin"], ["nas-other", "9", "users", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Configured DSM group does not exist", result.stdout + result.stderr)
        self.assertIn("Choose a number or type a group name [none]", result.stdout)
        self.assertEqual(result.stdout.count("is not a listed number or an existing group on this host; groups are "
                                             "never created."), 2)
        self.assertIn("workspace_write_group: nas-old -> users (asked: nas-old does not exist on this host)",
                      result.stdout)
        self.assertEqual(pf_config.parse_admin_config(self.config_path.read_bytes(), label="x").values[
            "workspace_write_group"], "users")
        self.assertEqual(Path("/etc/group").read_bytes(), group_file)
        self.assertEqual({row["key"]: row["action"] for row in self.audits()[0]["changes"]}["workspace_write_group"],
                         "changed")
        self.assertEqual(os.lstat(str(self.config_path)).st_gid, os.getgid())  # replace keeps the owner and group
        del gid

    def test_aw_a4_schema_2_current_is_a_no_op(self):
        pfx.admin_config(self.config_path, version=2, project=PROJECT, backup_read_group=GROUP,
                         workspace_write_group=GROUP)
        before = self.file_state(self.config_path)
        result = self.cli(["--instance", "staging", "config", "admin"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"config-current: {self.config_path} is current (admin configuration schema 2); nothing to change.",
                      result.stdout)
        self.assertEqual(self.file_state(self.config_path), before)
        self.assertEqual(self.audits(), [])

    def test_aw_a5_project_and_environment_mismatch_copies(self):
        records = [self.context.record_path.read_bytes(), self.layout.policy_path.read_bytes(),
                   (self.layout.root / "registry" / "instances.json").read_bytes()]
        cases = ((dict(project=PROJECT, environment="production"), "names environment 'production'; the approved policy of "
                  "staging is staging revision 1. An editable label never changes the approved policy"),
                 (dict(project="other-project"), "names project 'other-project', but staging is registered with project "
                  "'partflow-staging'; the registration is authoritative"))
        for values, expected in cases:
            with self.subTest(values=values):
                pfx.admin_config(self.config_path, backup_read_group=GROUP, workspace_write_group=GROUP, **values)
                before = self.file_state(self.config_path)
                code, out, err, _ = self.admin()
                self.assertEqual(code, 1, out + err)
                self.assertIn("ERROR: admin-config-mismatch: " + str(self.config_path) + " " + expected, err)
                self.assertNotIn("disagrees with", err)
                self.assertEqual(self.file_state(self.config_path), before)
        self.assertEqual([self.context.record_path.read_bytes(), self.layout.policy_path.read_bytes(),
                          (self.layout.root / "registry" / "instances.json").read_bytes()], records)

    def test_aw_a6_every_cancel_leaves_the_target_and_directory_unchanged(self):
        users_gid()
        self.config_path.unlink()
        cases = (("q at workspace", ["q"], "Cancelled at workspace_write_group"),
                 ("EOF at workspace", [EOFError], "Cancelled at workspace_write_group"),
                 ("Ctrl-C at backup", ["1", KeyboardInterrupt], "Cancelled at backup_read_group"),
                 ("q at backup", ["1", "q"], "Cancelled at backup_read_group"),
                 ("n at confirmation", ["1", "1", "n"], "config-cancelled: Cancelled; "),
                 ("EOF at confirmation", ["1", "1", EOFError], "config-cancelled: Cancelled; "),
                 ("Ctrl-C at confirmation", ["1", "1", KeyboardInterrupt], "config-cancelled: Cancelled; "),
                 ("q at confirmation", ["1", "1", "q"], "config-cancelled: Cancelled; "))
        for label, answers, expected in cases:
            with self.subTest(case=label):
                before = pfx.snapshot_tree(self.config_dir)
                code, out, err, _ = self.admin(answers)
                self.assertEqual(code, 1, out + err)
                self.assertIn(expected, err)
                self.assertIn(f"{self.config_path} was not created or changed", err)
                self.assertEqual(pfx.snapshot_tree(self.config_dir), before)
                self.assertEqual(self.temps(), [])
        self.assertEqual(self.audits(), [])
        # Enter without a default re-asks (the missing-group question has none).
        pfx.admin_config(self.config_path, workspace_write_group="nas-old")
        code, out, err, script = self.admin(["", "q"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("A value is required.", out)
        self.assertEqual(len(script.prompts), 2)

    def test_aw_a7_a_migrated_v25_instance_with_its_legacy_control_is_refused_first(self):
        legacy_control = self.base / "legacy-control"
        legacy_control.mkdir()
        item = {"operation_id": "inst-20261007T000000Z-0123abcd", "kind": "migrate-legacy", "phase": "completed",
                "updated": None, "next": [], "error": None, "journal": {"next": []},
                "plan": {"instance": {"instance_id": self.context.instance_id, "slug": "staging"},
                         "legacy": {"control_dir": str(legacy_control)}}}
        before = self.file_state(self.config_path)
        with mock.patch.object(pf_install, "operations", return_value=[item]):
            code, out, err, script = self.admin(["1"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: legacy-control-active:", err)
        self.assertEqual(script.prompts, [])
        self.assertEqual(self.file_state(self.config_path), before)

    def acl_on(self, path, grants=("system.posix_acl_access grants write to user 1000",)):
        """inspect_posix_acl answers ``posix`` for ``path`` only (every protected path keeps its real state)."""
        real = pf_bootstrap.inspect_posix_acl

        def inspect(candidate):
            return pf_bootstrap.AclState("posix", grants) if Path(candidate) == Path(path) else real(candidate)

        return mock.patch.object(pf_bootstrap, "inspect_posix_acl", inspect)

    def test_aw_a8_an_acl_bearing_file_is_refused_before_the_first_question(self):
        before = self.file_state(self.config_path)
        with self.acl_on(self.config_path):
            code, out, err, script = self.admin(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: config-file-acl: {self.config_path} carries ACL entries (posix); replacing it would drop "
                      "them, so nothing was changed. Apply these changes by hand: the settings you intended to change. "
                      "ACL-preserving writes belong to PF-A2.3.", err)
        self.assertEqual(script.prompts, [])
        self.assertEqual(self.file_state(self.config_path), before)
        # The real xattr form (named skip when the filesystem refuses POSIX ACLs).
        pfx.with_acl(self.config_path)
        before = self.file_state(self.config_path)
        code, out, err, script = self.admin(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: config-file-acl:", err)
        self.assertEqual((script.prompts, self.file_state(self.config_path)), ([], before))

    def test_aw_a9_a_missing_backup_group_is_asked_and_its_limit_stated(self):
        users_gid()
        pfx.admin_config(self.config_path, version=2, project=PROJECT, backup_read_group="nas-gone",
                         workspace_write_group=GROUP)
        code, out, err, script = self.admin(["users", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertIn(pf.BACKUP_GROUP_CONSEQUENCE, out)
        self.assertEqual(len([prompt for prompt in script.prompts if "group name" in prompt]), 1)
        # PF-A2.3 (OD-A22-20 closed): the group is a proposal; the summary says so instead of the A2.2 limit.
        self.assertIn("backup_read_group is a proposal: backups and recovery bundles keep the group of their folders "
                      "until '", out)
        self.assertNotIn("without a separate approval", out)
        self.assertEqual(pf_config.parse_admin_config(self.config_path.read_bytes(), label="x").values["backup_read_group"],
                         "users")

    def test_aw_a10_invalid_and_unsupported_files_have_their_own_copy(self):
        prefix = pf_install.launcher_prefix(self.layout.root)
        cases = ((json.dumps({"project": PROJECT, "zz": 1}).encode(),
                  f"ERROR: admin-config-invalid: {self.config_path}: Unknown configuration keys: zz. Nothing was changed; "
                  f"fix the file by hand, then run '{prefix} --instance staging config admin' again."),
                 (json.dumps(doc2(schema_version=3)).encode(),
                  f"ERROR: admin-config-version-unsupported: {self.config_path} declares schema_version 3; this control "
                  f"({self.context.control.release_id}) reads schema 2 and the legacy form without schema_version. "
                  f"Nothing was changed. If a newer control wrote this file, select that control again ('{prefix} "
                  "install control --release <id>') or restore the previous file."),
                 (json.dumps(dict(doc2(), zz=1, branch="a..b")).encode(), "(+1 more)"))
        for data, expected in cases:
            with self.subTest(expected=expected[:40]):
                self.config_path.write_bytes(data)
                code, out, err, script = self.admin(["y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(expected, err)
                self.assertEqual(script.prompts, [])
                self.assertEqual(self.config_path.read_bytes(), data)

    def test_aw_a11_config_app_needs_a_valid_admin_configuration(self):
        prefix = pf_install.launcher_prefix(self.layout.root) + " --instance staging"
        env_before = self.env_path.read_bytes()
        self.config_path.unlink()
        code, out, err, script = self.app(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: admin-config-required: Runtime configuration is missing or unreadable", err)
        self.assertIn(f"Run '{prefix} config admin' first; nothing was changed.", err)
        self.assertEqual(script.prompts, [])
        pfx.admin_config(self.config_path, project=PROJECT, workspace_write_group="nas-gone")
        code, out, err, script = self.app(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: admin-config-required: Configured DSM group does not exist.", err)
        self.assertEqual((script.prompts, self.env_path.read_bytes()), ([], env_before))

    def test_aw_a12_doctor_and_deploy_name_the_wizard_with_the_launcher_prefix(self):
        prefix = pf_install.launcher_prefix(self.layout.root) + " --instance staging"
        self.config_path.unlink()
        code, out, err, _ = self.run_main(["--instance", "staging", "doctor"])
        self.assertNotEqual(code, 0)
        self.assertIn(f"The controller does not create it; create it with '{prefix} config admin'.", out + err)
        code, out, err, _ = self.run_main(["--instance", "staging", "deploy"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"create it with '{prefix} config admin'.", err)
        self.assertEqual(os.listdir(str(self.context.operations_dir)), [])  # refused before the lock
        pfx.admin_config(self.config_path, project=PROJECT, backup_read_group="nas-gone", workspace_write_group=GROUP)
        code, out, err, _ = self.run_main(["--instance", "staging", "deploy"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"Configured DSM group does not exist. Check backup_read_group and workspace_write_group in "
                      f"{self.config_path}; choose an existing group with '{prefix} config admin'.", err)


# ============================================================================ PR: pre-registration mode


@ROOT_REQUIRED
class PreRegistration(ConfigBase):

    def new_home(self, name="b", project="partflow-b"):
        paths = pfx.data_home(self.base / name, project=project, group=GROUP)
        (paths["configuration"] / "pf-config.json").unlink()
        (paths["configuration"] / ".env").unlink()
        return paths

    def pre(self, directory, answers=(), project=None):
        arguments = ["config", "admin", "--configuration", str(directory)]
        if project is not None:
            arguments += ["--project", project]
        return self.run_main(arguments, answers)

    def test_pr1_create_then_register_preflight_has_no_admin_config_conflict(self):
        users_gid()
        fake = pfx.install_fake_docker(self.layout)
        paths = self.new_home()
        code, out, err, _ = self.pre(paths["configuration"], ["1", "1", "y"], project="partflow-b")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Instance: (not registered yet), project partflow-b", out)
        target = paths["configuration"] / "pf-config.json"
        self.assertEqual(pf_config.parse_admin_config(target.read_bytes(), label="x").values["project"], "partflow-b")
        request = {"slug": "b", "project": "partflow-b", "docker_endpoint": self.layout.daemon_endpoint,
                   **{role: str(paths[role]) for role in pf_instance.ROLE_NAMES}}
        runner = pf_install._installed_runner(self.layout.root, request["docker_endpoint"])
        result = pf_install._preflight(self.layout.root, "register", request, runner=runner,
                                       running_release=self.layout.release_dir)
        codes = [item.code for item in result.conflicts]
        self.assertFalse([code for code in codes if code.startswith("admin-config") or code == "group-missing"], codes)
        self.assertEqual(self.temps(paths["configuration"]), [])
        del fake

    def test_pr2_conflicting_paths_are_refused_with_the_launcher_prefix(self):
        prefix = pf_install.launcher_prefix(self.layout.root)
        case = json.loads((EXAMPLES / "conflicting-path.json").read_text(encoding="utf-8"))
        proposal = Path(case["proposal"]["configuration"].replace("{base}/a", str(self.base / "staging")))
        self.assertEqual(case["registered"]["workspace"].replace("{base}/a", str(self.base / "staging")),
                         str(self.paths["workspace"]))
        proposal.mkdir()
        for directory in (proposal, self.config_dir):
            with self.subTest(directory=str(directory)):
                before = pfx.snapshot_tree(directory)
                code, out, err, script = self.pre(directory, ["1", "1", "y"], project=case["proposal"]["project"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: config-path-conflict: {directory} ", err)
                self.assertIn(f"'{prefix} --instance staging config admin'", err)
                self.assertEqual(script.prompts, [])
                self.assertEqual(pfx.snapshot_tree(directory), before)

    def test_pr3_directory_rules_list_every_finding(self):
        open_parent = self.base / "open"
        open_parent.mkdir()
        os.chmod(open_parent, 0o775)
        (open_parent / "config").mkdir()
        for directory, finding in ((str(self.base / "x") + "/", "path-noncanonical"),
                                   (str(self.base / "missing"), "registered-path-missing"),
                                   (str(open_parent / "config"), "ancestor-replaceable")):
            with self.subTest(directory=directory):
                code, out, err, script = self.pre(directory, ["y"], project="partflow-b")
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: config-dir-invalid: {directory} cannot hold a configuration (1 finding(s)); "
                              "nothing was created:", err)
                self.assertIn(f"  - {finding}: ", err)
                self.assertEqual(script.prompts, [])
        self.assertEqual(os.listdir(str(open_parent / "config")), [])

    def test_pr4_option_combinations_exit_2_before_any_registry_read(self):
        paths = self.new_home()
        cases = (["config", "admin", "--project", "partflow-b"],
                 ["--instance", "staging", "config", "admin", "--configuration", str(paths["configuration"])],
                 ["config", "app", "--configuration", str(paths["configuration"])],
                 ["config", "admin", "--configuration", str(paths["configuration"]), "--project", "Bad Name"])
        with mock.patch.object(pf_instance, "load_registry", wraps=pf_instance.load_registry) as loader:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    code, out, err, _ = self.run_main(arguments)
                    self.assertEqual(code, 2, out + err)
                    self.assertIn("ERROR: config-option-invalid: ", err)
                    self.assertIn("Nothing was read or changed.", err)
        loader.assert_not_called()

    def test_pr5_a_held_registry_lock_is_config_busy_after_the_confirmation(self):
        users_gid()
        paths = self.new_home()
        handle = pf_instance.acquire_registry_lock(self.layout.root)
        try:
            code, out, err, script = self.pre(paths["configuration"], ["1", "1", "y"], project="partflow-b")
        finally:
            handle.release()
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: config-busy: Another operation holds {self.layout.root / 'locks' / 'registry.lock'}; "
                      "nothing was changed. Try again after it finishes.", err)
        self.assertTrue(script.prompts[-1].startswith("Write "))
        self.assertEqual(os.listdir(str(paths["configuration"])), [])

    def test_pr6_open_install_operations_and_invalid_reservations_refuse_before_questions(self):
        paths = self.new_home()
        item = {"operation_id": "inst-20261007T000000Z-0123abcd", "kind": "register", "phase": "prepared",
                "updated": None, "next": ["install resume"], "error": None, "journal": {"next": ["install resume"]},
                "plan": {"instance": None, "legacy": None}}
        with mock.patch.object(pf_install, "operations", return_value=[item]):
            code, out, err, script = self.pre(paths["configuration"], ["1"], project="partflow-b")
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-operation-pending: pf config admin was refused and nothing was created:", err)
        self.assertIn("  - install-operation-pending: inst-20261007T000000Z-0123abcd: install operation (register) is open "
                      "in phase prepared", err)
        self.assertEqual(script.prompts, [])
        reservation = self.layout.root / "registry" / "reservations" / "not-a-uuid.json"
        if not reservation.parent.exists():
            pf_instance._create_private_dir(reservation.parent, 0o700)
        pf_instance._write_private_file(reservation, b"{}", 0o600)
        code, out, err, script = self.pre(paths["configuration"], ["1"], project="partflow-b")
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: registry-pending: pf config admin was refused and nothing was created:", err)
        self.assertEqual(script.prompts, [])
        self.assertEqual(os.listdir(str(paths["configuration"])), [])

    def test_pr7_a_registration_between_the_confirmation_and_the_lock_is_seen_under_the_lock(self):
        users_gid()
        paths = self.new_home()
        other = pfx.data_home(self.base / "c", project="partflow-c", group=GROUP)

        def register_then_confirm():
            moved = dict(other)
            moved["configuration"] = paths["configuration"]
            pfx.register(self.layout, "c", moved, project="partflow-c")
            return "y"

        code, out, err, _ = self.pre(paths["configuration"], ["1", "1", register_then_confirm], project="partflow-b")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: config-path-conflict: {paths['configuration']} is the registered configuration directory "
                      "of instance c", err)
        self.assertEqual(os.listdir(str(paths["configuration"])), [])

    def test_pr8_a_legacy_schema_1_file_is_never_migrated_here(self):
        paths = self.new_home()
        target = paths["configuration"] / "pf-config.json"
        for groups, missing in (({"backup_read_group": GROUP, "workspace_write_group": GROUP}, None),
                                ({"backup_read_group": "nas-gone", "workspace_write_group": GROUP}, "backup_read_group")):
            with self.subTest(missing=missing):
                pfx.admin_config(target, project="partflow-b", **groups)
                before = self.file_state(target)
                code, out, err, script = self.pre(paths["configuration"], ["1", "y"], project="partflow-b")
                self.assertEqual(code, 0, out + err)
                self.assertIn(f"admin-config-legacy-unregistered: {target} is a legacy schema 1 file in a directory that "
                              "is not registered; it was left unchanged because a v2.5 control may still read it.", out)
                if missing:
                    self.assertIn("backup_read_group 'nas-gone' does not exist on this host; fix it by hand first.", out)
                self.assertEqual(script.prompts, [])
                self.assertEqual(self.file_state(target), before)


# ============================================================================ AD: declarations (S9)


class AppDeclaration(unittest.TestCase):

    def test_ad1_partflow_declaration_equals_the_allowlist_and_others_are_refused(self):
        declaration = pf_config.app_declaration("partflow-staging-legacy")
        self.assertEqual(declaration.keys, pf_config.APP_KEYS)
        self.assertEqual([variable.key for variable in declaration.variables if variable.secret],
                         list(pf_config.SECRET_KEYS))
        self.assertEqual(declaration.generated_secret_hex_bytes, 32)
        with self.assertRaises(pf_config.ConfigError) as caught:
            pf_config.app_declaration("other-profile")
        self.assertEqual(str(caught.exception), "app-profile-undeclared: Profile other-profile declares no application "
                                                "variables in this control; nothing was changed.")

    def test_ad2_a_stateless_declaration_threads_its_keys_through_every_step(self):
        greeting = pf_config.AppDeclaration(
            profile_id="greeting-test", example_name="greeting.env.example", generated_secret_hex_bytes=0,
            variables=(pf_config.AppVariable("APP_GREETING", "text", False, False, "Greeting"),))
        with mock.patch.dict(pf_config.APP_DECLARATIONS, {"greeting-test": greeting}):
            declaration = pf_config.app_declaration("greeting-test")
            example = b"# greeting example\nAPP_GREETING=\n"
            plan = pf_config.plan_app_config(
                declaration, current=None, example=pf_config.parse_app_env(example, label="ex",
                                                                           allowed_keys=declaration.keys,
                                                                           require_all=False),
                deployed=False, check=lambda kind, value: None, zone=lambda name: ("ok", "/z"))
            self.assertEqual([(item.key, item.action) for item in plan], [("APP_GREETING", "ask")])
            data = pf_config.rewrite_app_env(example, keys=declaration.keys, set_values={"APP_GREETING": "hello world"},
                                             append={})
            self.assertEqual(data, b"# greeting example\nAPP_GREETING='hello world'\n")
            self.assertEqual(pf_config.parse_app_env(data, label="x", allowed_keys=("APP_GREETING",)),
                             {"APP_GREETING": "hello world"})
            with self.assertRaises(pf_config.ConfigError):
                pf_config.rewrite_app_env(example, keys=declaration.keys, set_values={"POSTGRES_USER": "x"}, append={})
        self.assertNotIn("greeting-test", pf_config.APP_DECLARATIONS)
        admin_source = (PACKAGE / "pf-admin.py").read_text(encoding="utf-8")
        wizard = admin_source[admin_source.index("    def admin_wizard(self):"):
                              admin_source.index("    def deployed_evidence(self):")]
        for word in ("POSTGRES", "SITE_TIMEZONE", "app_declaration", "zone_status"):
            self.assertNotIn(word, wizard)


# ============================================================================ AP: app wizard (A2-T06)


@ROOT_REQUIRED
class AppWizard(ConfigBase):

    def setUp(self):
        super().setUp()
        pfx.admin_config(self.config_path, project=PROJECT, backup_read_group=GROUP, workspace_write_group=GROUP)

    def assert_secret_absent(self, secret, *texts, roots=()):
        encoded = urllib.parse.quote(secret, safe="")
        for text in texts:
            self.assertNotIn(secret, text)
            self.assertNotIn(encoded, text)
        for root in roots:
            for current, _, files in os.walk(str(root)):
                for name in files:
                    path = Path(current) / name
                    if path == self.env_path or path.is_symlink() or not path.is_file():
                        continue
                    data = path.read_bytes()
                    self.assertNotIn(secret.encode("utf-8"), data, str(path))
                    self.assertNotIn(encoded.encode("utf-8"), data, str(path))

    def test_er3_the_wizard_refuses_an_example_without_every_declared_key(self):
        self.env_path.unlink()
        example = self.context.control.path / "nas.env.example"
        for label, text in (("missing", example.read_text().replace("PARTFLOW_HTTP_PORT=5173\n", "")),
                            ("twice", example.read_text() + "POSTGRES_DB=again\n")):
            with self.subTest(example=label):
                def read(path, text=text):
                    return text.encode() if Path(path) == example else pf_install._read_regular(path)

                with mock.patch.object(pf_install, "read_regular_file", read):
                    code, out, err, _ = self.app(["", "", "", "2", "5173", "h.example", "y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: app-example-invalid: The installed example {example} does not declare every "
                              "partflow-staging-legacy variable exactly once (", err)
                self.assertFalse(self.env_path.exists())
                self.assertEqual(self.temps(), [])

    def test_ap1_an_existing_secret_is_preserved_and_only_the_hostname_is_asked(self):
        original = env_text(POSTGRES_PASSWORD="'" + AP1_PASSWORD + "'", PARTFLOW_ALLOWED_HOST=None).encode("utf-8")
        self.env_path.write_bytes(original)
        code, out, err, script = self.app(["partflow.internal.example", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual([prompt for prompt in script.prompts if not prompt.startswith("Write ")],
                         ["Exact internal Reverse Proxy hostname: "])
        self.assertEqual(self.env_path.read_bytes(), original + b"PARTFLOW_ALLOWED_HOST=partflow.internal.example\n")
        self.assertIn("POSTGRES_PASSWORD: unchanged (not shown)", out)
        self.assertIn("PARTFLOW_ALLOWED_HOST: (missing) -> partflow.internal.example (asked)", out)
        record = self.audits()[0]
        self.assert_valid_record(record)
        actions = {row["key"]: row for row in record["changes"]}
        self.assertEqual((actions["POSTGRES_PASSWORD"]["action"], actions["POSTGRES_PASSWORD"]["after"]),
                         ("unchanged", None))
        self.assertEqual(actions["PARTFLOW_ALLOWED_HOST"]["action"], "added")
        values = pf_config.parse_app_env(self.env_path.read_bytes(), label=".env")
        url = pf_config.database_url(values["POSTGRES_USER"], values["POSTGRES_PASSWORD"], values["POSTGRES_DB"])
        self.assertEqual(urllib.parse.unquote(urllib.parse.urlsplit(url).password), AP1_PASSWORD)
        self.assert_secret_absent(AP1_PASSWORD, out, err, roots=(self.context.operations_dir,))

    def test_ap2_no_env_never_deployed_asks_the_declared_questions_and_generates_a_secret(self):
        host_zone("America/Los_Angeles")
        gid = users_gid()
        pfx.admin_config(self.config_path, project=PROJECT, backup_read_group=GROUP, workspace_write_group="users")
        self.env_path.unlink()
        generated = []
        real = pf.secrets.token_hex

        def token(nbytes):
            generated.append(real(nbytes))
            return generated[-1]

        with mock.patch.object(pf.secrets, "token_hex", token):
            code, out, err, script = self.app(["", "", "", "2", "", "partflow.internal.example", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(script.prompts, [
            "PostgreSQL user [partflow_staging]: ", "PostgreSQL database [partflow_staging]: ",
            "Factory IANA timezone [America/Los_Angeles]: ", "Select access mode [1]: ", "PartFlow HTTP port [5173]: ",
            "Exact internal Reverse Proxy hostname: ", f"Write {self.env_path}? [y/N]: "])
        self.assertEqual(len(generated), 1)
        self.assertRegex(generated[0], r"\A[0-9a-f]{64}\Z")
        self.assertIn("POSTGRES_PASSWORD: set (not shown; generated, 64 hexadecimal characters)", out)
        values = pf_config.parse_app_env(self.env_path.read_bytes(), label=".env")
        self.assertEqual(values, {"POSTGRES_USER": "partflow_staging", "POSTGRES_PASSWORD": generated[0],
                                  "POSTGRES_DB": "partflow_staging", "SITE_TIMEZONE": "America/Los_Angeles",
                                  "PARTFLOW_BIND_IP": "127.0.0.1", "PARTFLOW_HTTP_PORT": "5173",
                                  "PARTFLOW_ALLOWED_HOST": "partflow.internal.example"})
        self.assertIn(b"# Factory calendar, not the browser timezone.\n", self.env_path.read_bytes())
        info = os.lstat(str(self.env_path))
        self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (0, gid, 0o660))
        record = self.audits()[0]
        self.assert_valid_record(record)
        self.assertEqual({row["key"]: row["action"] for row in record["changes"]}["POSTGRES_PASSWORD"], "set")
        self.assertEqual(record["mode"], "create")
        self.assert_secret_absent(generated[0], out, err, roots=(self.context.operations_dir,))

    def test_ap3_empty_password_generated_into_its_line_or_refused_when_deployed_and_a_weak_one_is_noted(self):
        original = env_text(POSTGRES_PASSWORD="").encode()
        self.env_path.write_bytes(original)
        code, out, err, _ = self.app(["y"])
        self.assertEqual(code, 0, out + err)
        written = self.env_path.read_bytes()
        password = pf_config.parse_app_env(written, label=".env")["POSTGRES_PASSWORD"]
        self.assertEqual(written, original.replace(b"POSTGRES_PASSWORD=\n", b"POSTGRES_PASSWORD=" + password.encode() + b"\n"))
        # deployed: refused, unchanged
        self.env_path.write_bytes(original)
        self.deployed()
        code, out, err, script = self.app(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: app-credential-unusable: {self.env_path} has no usable POSTGRES_PASSWORD (empty) and "
                      "staging is treated as deployed (state/deployed.json;", err)
        self.assertEqual((self.env_path.read_bytes(), script.prompts), (original, []))
        (self.context.state_dir / "deployed.json").unlink()
        weak = env_text(POSTGRES_PASSWORD="w" * 20).encode()
        self.env_path.write_bytes(weak)
        code, out, err, _ = self.app()
        self.assertEqual(code, 0, out + err)
        self.assertIn("config-current: ", out)
        self.assertIn("password-weak-for-new-deployment: POSTGRES_PASSWORD is kept unchanged, but it is shorter than 32 "
                      "characters", out)
        self.assertEqual(self.env_path.read_bytes(), weak)

    def test_ap4_unrenderable_or_short_existing_passwords_are_a_migration_issue(self):
        for line in ('"it' + "'" + 's-a-long-password-0123456789abcdef"', '"abcdefgh-0123456789-abcdefgh-0123\\\\"', "abc"):
            with self.subTest(line=line):
                original = env_text(POSTGRES_PASSWORD=line).encode()
                self.env_path.write_bytes(original)
                code, out, err, script = self.app(["y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn("ERROR: migration-issue: config/.env holds values that cannot be frozen literally: "
                              "POSTGRES_PASSWORD: ", err)
                self.assertIn("Nothing was changed or regenerated; fix the file explicitly.", err)
                self.assertEqual((self.env_path.read_bytes(), script.prompts), (original, []))

    def test_ap5_timezone_against_host_zone_data(self):
        host_zone("UTC")
        for deployed in (False, True):
            with self.subTest(deployed=deployed):
                if deployed:
                    self.deployed()
                original = env_text(SITE_TIMEZONE="Mars/Base", PARTFLOW_ALLOWED_HOST=None).encode()
                self.env_path.write_bytes(original)
                code, out, err, script = self.app(["partflow.internal.example", "y"])
                self.assertEqual(code, 0, out + err)
                self.assertEqual(len(script.prompts), 2)
                self.assertIn("zone-unknown-on-host: SITE_TIMEZONE Mars/Base kept; it is not in this host's zone data", out)
                self.assertIn("SITE_TIMEZONE: Mars/Base (kept; not in host zone data)", out)
                self.assertEqual(self.env_path.read_bytes(), original + b"PARTFLOW_ALLOWED_HOST=partflow.internal.example\n")
        (self.context.state_dir / "deployed.json").unlink()
        self.env_path.write_bytes(env_text(SITE_TIMEZONE=None).encode())
        code, out, err, script = self.app(["Mars/Base", "UTC", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Invalid value: SITE_TIMEZONE 'Mars/Base' is not in the host zone data", out)
        self.assertIn("enter a zone such as America/Los_Angeles.", out)
        self.assertEqual(pf_config.parse_app_env(self.env_path.read_bytes(), label="x")["SITE_TIMEZONE"], "UTC")
        with mock.patch.object(pf_config, "compiled_tzpath", return_value=("/nonexistent-zoneinfo",)):
            present = env_text(SITE_TIMEZONE="Europe/Berlin").encode()
            self.env_path.write_bytes(present)
            code, out, err, _ = self.app()
            self.assertEqual(code, 0, out + err)
            self.assertIn("zone-data-unavailable: SITE_TIMEZONE Europe/Berlin kept; host zone data is unavailable "
                          "(searched /nonexistent-zoneinfo); the backend checks it at startup.", out)
            missing = env_text(SITE_TIMEZONE=None).encode()
            self.env_path.write_bytes(missing)
            code, out, err, script = self.app(["UTC", "y"])
            self.assertEqual(code, 1, out + err)
            self.assertIn("ERROR: zone-data-unavailable: No IANA zone data was found on this host (searched "
                          "/nonexistent-zoneinfo); SITE_TIMEZONE cannot be verified, so nothing was changed.", err)
            self.assertEqual((self.env_path.read_bytes(), script.prompts), (missing, []))

    def test_ap6_non_canonical_values_are_asked_with_their_canonical_default(self):
        original = env_text(PARTFLOW_HTTP_PORT="05173", PARTFLOW_ALLOWED_HOST="PartFlow.LAN").encode()
        self.env_path.write_bytes(original)
        code, out, err, script = self.app(["", "", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(script.prompts[:2], ["PartFlow HTTP port [5173]: ",
                                              "Exact internal Reverse Proxy hostname [partflow.lan]: "])
        self.assertEqual(self.env_path.read_bytes(),
                         original.replace(b"=05173\n", b"=5173\n").replace(b"=PartFlow.LAN\n", b"=partflow.lan\n"))
        self.assertIn("PARTFLOW_HTTP_PORT: 05173 -> 5173 (changed: asked)", out)

    def test_ap7_structural_errors_are_refused_with_the_parser_message(self):
        for text, fragment in ((env_text() + "EXTRA=1\n", "unknown key 'EXTRA'"),
                               (env_text() + "SITE_TIMEZONE=UTC\n", "duplicate key 'SITE_TIMEZONE'"),
                               ("export " + env_text(), "invalid key 'export POSTGRES_USER'"),
                               (env_text() + "A=1\rB=2\n", "bare carriage return")):
            with self.subTest(fragment=fragment):
                self.env_path.write_bytes(text.encode())
                code, out, err, script = self.app(["y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: app-config-invalid: {self.env_path}: ", err)
                self.assertIn(fragment, err)
                self.assertEqual((self.env_path.read_bytes(), script.prompts), (text.encode(), []))

    def test_ap8_every_cancel_discards_the_generated_secret(self):
        host_zone("America/Los_Angeles")
        self.env_path.unlink()
        stages = ["", "", "", "2", "", "partflow.internal.example"]
        for index in range(len(stages) + 1):
            for stop in ("q", EOFError, KeyboardInterrupt, "n"):
                if stop == "n" and index < len(stages):
                    continue
                with self.subTest(index=index, stop=stop):
                    generated = []
                    real = pf.secrets.token_hex
                    with mock.patch.object(pf.secrets, "token_hex", lambda n: generated.append(real(n)) or generated[-1]):
                        code, out, err, _ = self.app(stages[:index] + [stop])
                    self.assertEqual(code, 1, out + err)
                    self.assertIn("config-cancelled", err)
                    self.assertFalse(self.env_path.exists())
                    self.assertEqual(self.temps(), [])
                    for secret in generated:
                        # The fixture base holds the installation root, the configuration directory and every
                        # temporary file a run could leave.
                        self.assert_secret_absent(secret, out, err, roots=(self.base,))
                    if index == len(stages):
                        self.assertIn("and the generated password was discarded", err)
        self.assertEqual(self.audits(), [])

    def test_ap9_deploy_creates_env_through_the_same_wizard_and_freezes_it(self):
        host_zone("America/Los_Angeles")
        self.env_path.unlink()
        controller = pf.Controller(self.context)
        answers = iter(["", "", "", "2", "", "partflow.internal.example", "y"])
        prompts = []

        def answer(prompt=""):
            prompts.append(prompt)
            return next(answers)

        with contextlib.redirect_stdout(io.StringIO()), mock.patch("builtins.input", answer), \
                mock.patch.object(pf, "unattended", return_value=False), controller.lock(pending_route="deploy"):
            values = controller.prepare_new_env()
            frozen = dict(controller.frozen.values)
        self.assertEqual(prompts[:6], ["PostgreSQL user [partflow_staging]: ", "PostgreSQL database [partflow_staging]: ",
                                       "Factory IANA timezone [America/Los_Angeles]: ", "Select access mode [1]: ",
                                       "PartFlow HTTP port [5173]: ", "Exact internal Reverse Proxy hostname: "])
        self.assertEqual(prompts[6], "Write .env with these settings and continue [Y/n]: ")
        self.assertEqual(frozen, pf_config.parse_app_env(self.env_path.read_bytes(), label=".env"))
        self.assertEqual(values, frozen)

    def test_ap10_a_migrated_instance_is_deployed_by_its_completed_migration(self):
        item = {"operation_id": "inst-20261007T000000Z-0123abcd", "kind": "migrate-legacy", "phase": "completed",
                "updated": None, "next": [], "error": None, "journal": {"next": []},
                "plan": {"instance": {"instance_id": self.context.instance_id, "slug": "staging"},
                         "legacy": {"control_dir": str(self.base / "retired-control")}}}
        cases = (("absent", None, "POSTGRES_USER"), ("empty password", env_text(POSTGRES_PASSWORD=""), "POSTGRES_PASSWORD"),
                 ("maintenance db", env_text(POSTGRES_DB="postgres"), "POSTGRES_DB"))
        for label, text, key in cases:
            with self.subTest(case=label):
                if text is None:
                    if self.env_path.exists():
                        self.env_path.unlink()
                else:
                    self.env_path.write_text(text)
                with mock.patch.object(pf_install, "operations", return_value=[item]):
                    code, out, err, script = self.app(["x", "y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: app-credential-unusable: {self.env_path} has no usable {key} (", err)
                self.assertIn("treated as deployed (a completed v2.5 migration;", err)
                self.assertEqual(script.prompts, [])
                self.assertEqual(self.env_path.exists(), text is not None)
        self.env_path.unlink()
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            if str(path).endswith("/state/deployed.json"):
                raise PermissionError(13, "Permission denied", str(path))
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(pf.os, "lstat", lstat):
            code, out, err, script = self.app(["x", "y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("treated as deployed (unreadable state;", err)
        self.assertFalse(self.env_path.exists())

    def test_ap11_an_invalid_credential_is_asked_only_before_the_first_deployment(self):
        original = env_text(POSTGRES_DB="postgres").encode()
        self.env_path.write_bytes(original)
        code, out, err, script = self.app(["partflow_app", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(script.prompts[0], "PostgreSQL database: ")
        self.assertEqual(self.env_path.read_bytes(), original.replace(b"POSTGRES_DB=postgres\n",
                                                                      b"POSTGRES_DB=partflow_app\n"))
        row = {row["key"]: row for row in self.audits()[0]["changes"]}["POSTGRES_DB"]
        self.assertEqual((row["action"], row["before"], row["after"]), ("changed", "postgres", "partflow_app"))
        code, out, err, _ = self.app()
        self.assertEqual(code, 0, out + err)
        self.assertIn("config-current: ", out)


# ============================================================================ ZD: zone data


class ZoneData(unittest.TestCase):

    def test_zd1_fixture_zone_data(self):
        with tempfile.TemporaryDirectory() as temp:
            zones = pfx.zone_dir(Path(temp) / "zoneinfo", {"America/Los_Angeles": "tzif", "Bad/Text": "text",
                                                          "Bad/Fifo": "fifo", "US/Pacific": "link:../America/Los_Angeles"})
            tzpath = (str(zones),)
            for name, expected in (("America/Los_Angeles", "ok"), ("Bad/Text", "unknown-zone"),
                                   ("Bad/Fifo", "unknown-zone"), ("US/Pacific", "ok"), ("Mars/Base", "unknown-zone"),
                                   ("../etc/passwd", "invalid-name"), ("/UTC", "invalid-name"),
                                   ("America/../../etc", "invalid-name"), ("not a zone", "invalid-name")):
                with self.subTest(name=name):
                    self.assertEqual(pf_config.zone_status(name, tzpath=tzpath)[0], expected)
            self.assertEqual(pf_config.zone_status("America/Los_Angeles", tzpath=tzpath), ("ok", str(zones)))

    def test_zd2_no_zone_data(self):
        for tzpath in ((), ("/nonexistent-a", "/nonexistent-b")):
            with self.subTest(tzpath=tzpath):
                self.assertEqual(pf_config.zone_status("UTC", tzpath=tzpath)[0], "zone-data-unavailable")

    def test_zd3_the_interpreters_installed_zone_data(self):
        tzpath = pf_config.compiled_tzpath()
        if pf_config.zone_status("UTC")[0] == "zone-data-unavailable":
            self.skipTest(f"the image has no installed zone data (compiled TZPATH {tzpath})")
        self.assertEqual(pf_config.zone_status("America/Los_Angeles")[0], "ok")
        self.assertEqual(pf_config.zone_status("Mars/Base")[0], "unknown-zone")
        self.assertTrue(all(os.path.isabs(entry) for entry in tzpath))

    def test_zd4_pythontzpath_is_never_consulted(self):
        before = (pf_config.compiled_tzpath(), pf_config.zone_status("America/Los_Angeles"))
        with mock.patch.dict(os.environ, {"PYTHONTZPATH": "/nonexistent"}):
            self.assertEqual((pf_config.compiled_tzpath(), pf_config.zone_status("America/Los_Angeles")), before)


# ============================================================================ ER / UR: rewrite (A1-T08)


class EnvRewrite(unittest.TestCase):
    keys = pf_config.APP_KEYS

    def test_er1_line_endings_comments_and_quoting_are_preserved(self):
        base = ("# comment\r\nPOSTGRES_USER=u\r\nPOSTGRES_PASSWORD='p a s s'\nPOSTGRES_DB=d\r\n\n"
                "SITE_TIMEZONE=UTC\nPARTFLOW_BIND_IP=127.0.0.1\r\nPARTFLOW_HTTP_PORT=05173").encode()
        result = pf_config.rewrite_app_env(base, keys=self.keys, set_values={"PARTFLOW_HTTP_PORT": "5173",
                                                                           "POSTGRES_DB": "new db"},
                                           append={"PARTFLOW_ALLOWED_HOST": "h.example"})
        self.assertEqual(result, ("# comment\r\nPOSTGRES_USER=u\r\nPOSTGRES_PASSWORD='p a s s'\nPOSTGRES_DB='new db'\r\n\n"
                                  "SITE_TIMEZONE=UTC\nPARTFLOW_BIND_IP=127.0.0.1\r\nPARTFLOW_HTTP_PORT=5173\n"
                                  "PARTFLOW_ALLOWED_HOST=h.example\n").encode())

    def test_er2_render_value(self):
        for value, rendered in (("$HOME", "$HOME"), ("a\\b", "a\\b"), ('"q"', "'\"q\"'"), ("a b", "'a b'"),
                                ("#x", "'#x'"), ("tail\\", "tail\\")):
            with self.subTest(value=value):
                self.assertEqual(pf_config.render_value(value), rendered)
                parsed = pf_config.parse_app_env(f"POSTGRES_USER={rendered}\n".encode(), label="x",
                                                 require_all=False)
                self.assertEqual(parsed["POSTGRES_USER"], value)
        for value in ("it's", "a b\\", "x\ny"):
            with self.subTest(value=value):
                with self.assertRaises(pf_config.ConfigError):
                    pf_config.render_value(value)

    def test_er3_create_from_an_example_missing_a_key_or_holding_one_twice_is_refused(self):
        values = {key: "x" for key in self.keys}
        values.update(PARTFLOW_BIND_IP="127.0.0.1")
        with self.assertRaises(pf_config.ConfigError):
            pf_config.rewrite_app_env(b"POSTGRES_USER=x\n", keys=self.keys, set_values=values, append={})
        with self.assertRaises(pf_config.ConfigError):
            pf_config.parse_app_env(b"POSTGRES_USER=x\nPOSTGRES_USER=y\n", label="ex", require_all=False)

    def test_ur1_database_url_round_trips_every_a1_t08_password(self):
        import configparser
        for password in A1_T08_PASSWORDS:
            with self.subTest(password=password):
                url = pf_config.database_url("partflow_staging", password, "partflow_staging")
                self.assertEqual(urllib.parse.unquote(urllib.parse.urlsplit(url).password), password)
                parser = configparser.ConfigParser()
                parser.read_dict({"alembic": {"sqlalchemy.url": url.replace("%", "%%")}})
                self.assertEqual(parser.get("alembic", "sqlalchemy.url"), url)


# ============================================================================ CC: concurrency


@ROOT_REQUIRED
class Concurrency(ConfigBase):

    def test_cc1_a_held_instance_lock_is_the_a1_busy_refusal(self):
        before = self.file_state(self.config_path)
        handle = pf_instance.acquire_instance_lock(self.context)
        try:
            code, out, err, script = self.app(["y"])
        finally:
            handle.release()
        self.assertEqual(code, 1, out + err)
        self.assertIn("Another operation holds the lock of instance staging; try again after it finishes.", err)
        self.assertEqual((script.prompts, self.file_state(self.config_path)), ([], before))

    def test_cc2_another_writer_between_the_confirmation_and_the_swap_wins(self):
        other = b'{"project": "partflow-staging", "environment": "staging", "branch": "editor"}\n'

        def edit_then_confirm():
            self.config_path.write_bytes(other)
            return "y"

        code, out, err, _ = self.admin([edit_then_confirm])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: config-changed: {self.config_path} changed while the wizard was running", err)
        self.assertEqual(self.config_path.read_bytes(), other)
        self.assertEqual((self.temps(), self.audits()), ([], []))

    def test_cc3_a_held_registry_lock_refuses_a_second_create_and_link_eexist_is_config_changed(self):
        users_gid()
        first = pfx.data_home(self.base / "b", project="partflow-b", group=GROUP)["configuration"]
        second = pfx.data_home(self.base / "c", project="partflow-c", group=GROUP)["configuration"]
        for directory in (first, second):
            for name in ("pf-config.json", ".env"):
                (directory / name).unlink()
        code, out, err, _ = self.run_main(["config", "admin", "--configuration", str(first), "--project", "partflow-b"],
                                          ["1", "1", "y"])
        self.assertEqual(code, 0, out + err)
        written = (first / "pf-config.json").read_bytes()
        handle = pf_instance.acquire_registry_lock(self.layout.root)
        try:
            code, out, err, _ = self.run_main(["config", "admin", "--configuration", str(second), "--project",
                                               "partflow-c"], ["1", "1", "y"])
        finally:
            handle.release()
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: config-busy: ", err)
        self.assertEqual(((first / "pf-config.json").read_bytes(), os.listdir(str(second))), (written, []))
        target = second / "pf-config.json"
        real_link = os.link

        def link(source, destination, *args, **kwargs):
            if str(destination) == str(target):
                target.write_bytes(b"{}\n")
            return real_link(source, destination, *args, **kwargs)

        with mock.patch.object(pf.os, "link", link):
            with self.assertRaises(pf.Failure) as caught:
                pf.write_editable_file(target, b"new\n", expected=None, expected_identity=None, create_gid=0,
                                       op8="0123abcd")
        self.assertTrue(str(caught.exception).startswith("config-changed: "))
        self.assertEqual(target.read_bytes(), b"{}\n")
        self.assertEqual(self.temps(second), [])

    def test_cc4_pending_journals_and_open_install_operations_refuse(self):
        before = self.file_state(self.config_path)
        pf.write_json(self.context.journal_path, {"operation": "deploy", "phase": "paused", "started": "x"})
        code, out, err, script = self.admin(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("A previous operation is incomplete (operation=deploy, phase=paused).", err)
        self.context.journal_path.unlink()
        item = {"operation_id": "inst-20261007T000000Z-0123abcd", "kind": "control", "phase": "prepared",
                "updated": None, "next": ["install resume"], "error": None, "journal": {"next": ["install resume"]},
                "plan": {"instance": None, "legacy": None}}
        with mock.patch.object(pf_install, "operations", return_value=[item]):
            code, out, err, script = self.app(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: install-operation-pending: Install operation inst-20261007T000000Z-0123abcd (control)", err)
        self.assertEqual((script.prompts, self.file_state(self.config_path)), ([], before))


# ============================================================================ CR: crash windows of the writer


@ROOT_REQUIRED
class Crash(ConfigBase):

    def forked(self, patches, answers):
        """Run `pf config admin` in a forked child whose ``patches`` end it with os._exit(137); returns the status."""
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child
            code = 0
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                with contextlib.ExitStack() as stack:
                    for owner, name, replacement in patches:
                        stack.enter_context(mock.patch.object(owner, name, replacement))
                    self.admin(answers)
            except BaseException:  # noqa: B902 - the child reports any failure as exit 1
                code = 1
            os._exit(code)
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)

    @staticmethod
    def own(path):
        return bool(pf.EDITABLE_TEMP_RE.fullmatch(Path(str(path)).name))

    def seam(self, name, when):
        real = getattr(os, name)

        def wrapper(*args, **kwargs):
            if name in ("fchown",):
                own = True
            else:
                own = any(self.own(argument) for argument in args[:2])
            if own and when == "before":
                os._exit(137)
            result = real(*args, **kwargs)
            if own and when == "after":
                os._exit(137)
            return result

        return (os, name, wrapper)

    def test_cr1_every_crash_window_converges_on_the_next_run(self):
        users_gid()
        rows = (("after the temp write (replace)", "migrate", [self.seam("fchown", "before")], "write"),
                ("after fchown to a non-root uid (replace)", "migrate-1000", [self.seam("replace", "before")], "write"),
                ("after fchown (create)", "create", [self.seam("link", "before")], "write"),
                ("between link and unlink (create)", "create", [self.seam("link", "after")], "current"),
                ("after replace before the directory fsync", "migrate", [self.seam("replace", "after")], "current"),
                ("before the audit write", "migrate",
                 [(pf.Controller, "write_config_change", lambda *args, **kwargs: os._exit(137))], "current"))
        for label, mode, patches, outcome in rows:
            with self.subTest(window=label):
                for name in os.listdir(str(self.context.operations_dir)):
                    shutil.rmtree(str(self.context.operations_dir / name))
                for name in os.listdir(str(self.config_dir)):
                    if name != ".env":
                        os.unlink(str(self.config_dir / name))
                answers = ["1", "1", "y"] if mode == "create" else ["y"]
                if mode != "create":
                    pfx.admin_config(self.config_path, project=PROJECT, backup_read_group=GROUP,
                                     workspace_write_group=GROUP)
                    if mode == "migrate-1000":
                        os.chown(self.config_path, 1000, os.getgid())
                old = self.config_path.read_bytes() if self.config_path.exists() else None
                self.assertEqual(self.forked(patches, answers), 137)
                if self.config_path.exists():
                    parsed = pf_config.parse_admin_config(self.config_path.read_bytes(), label="x")
                    self.assertEqual(parsed.problems, ())
                    self.assertIn(self.config_path.read_bytes(), (old, pf_config.render_admin_config(
                        pf_config.admin_document(parsed.values))))
                leftovers = self.temps()
                code, out, err, _ = self.admin(answers)
                self.assertEqual(code, 0, out + err)
                if leftovers:
                    self.assertIn("config-temp-removed: Removed leftover temporary file(s) of an interrupted 'pf config' "
                                  "run: " + ", ".join(leftovers) + ".", out)
                self.assertEqual(self.temps(), [])
                self.assertEqual(os.lstat(str(self.config_path)).st_nlink, 1)
                self.assertEqual(pf_config.parse_admin_config(self.config_path.read_bytes(), label="x").schema_version, 2)
                if outcome == "current":
                    self.assertIn("config-current: ", out)
                    self.assertEqual(self.audits(), [])
                else:
                    self.assertEqual(len(self.audits()), 1)
                if mode == "migrate-1000":
                    self.assertEqual(os.lstat(str(self.config_path)).st_uid, 1000)

    def test_cr2_foreign_files_with_the_reserved_pattern_are_refused_and_kept(self):
        target_name = ".pf-config.json.pf-config-0123abcd"
        unrelated = self.base / "unrelated"
        unrelated.write_text("x")

        def other_owner(path):
            path.write_text("x")
            os.chown(path, 1000, 1000)
            os.chmod(path, 0o604)

        cases = (("another owner", other_owner),
                 ("second hard link", lambda path: os.link(str(unrelated), str(path))),
                 ("directory", lambda path: path.mkdir()),
                 ("symlink", lambda path: os.symlink(str(unrelated), str(path))))
        for label, make in cases:
            with self.subTest(case=label):
                path = self.config_dir / target_name
                make(path)
                code, out, err, script = self.admin(["y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn("ERROR: config-file-unsafe: ", err)
                self.assertIn(target_name, err)
                self.assertEqual(script.prompts, [])
                self.assertTrue(os.path.lexists(str(path)))
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()

    def test_cr3_a_refusal_removes_no_leftover(self):
        leftover = self.config_dir / ".pf-config.json.pf-config-0123abcd"
        leftover.write_text("partial")
        real = pf_bootstrap.inspect_posix_acl
        target = self.config_path

        def inspect(candidate):
            return pf_bootstrap.AclState("posix", ()) if Path(candidate) == target else real(candidate)

        with mock.patch.object(pf_bootstrap, "inspect_posix_acl", inspect):
            code, out, err, _ = self.admin(["y"])
        self.assertEqual(code, 1, out + err)
        self.assertIn("config-file-acl", err)
        self.assertTrue(leftover.exists())

        def edit_then_confirm():
            self.config_path.write_text('{"project": "partflow-staging", "branch": "editor"}\n')
            return "y"

        code, out, err, _ = self.admin([edit_then_confirm])
        self.assertEqual(code, 1, out + err)
        self.assertIn("config-changed", err)
        self.assertTrue(leftover.exists())


# ============================================================================ AU: audit regressions


@ROOT_REQUIRED
class AuditRegressions(ConfigBase):
    """PF-A2.2 audit (``_claude_outputs/ops/PF-A2.2/audit-findings.json``). AU-1, AU-2, AU-3, AU-5..AU-8 fail on
    5d102d1 and pass with their fix; AU-4 and AU-9 add the missing evidence for paths that already behaved."""

    def setUp(self):
        super().setUp()
        pfx.admin_config(self.config_path, project=PROJECT, backup_read_group=GROUP, workspace_write_group=GROUP)

    def new_home(self, name="b", project="partflow-b"):
        paths = pfx.data_home(self.base / name, project=project, group=GROUP)
        (paths["configuration"] / "pf-config.json").unlink()
        (paths["configuration"] / ".env").unlink()
        return paths

    def pre(self, directory, answers=(), project=None):
        arguments = ["config", "admin", "--configuration", str(directory)]
        if project is not None:
            arguments += ["--project", project]
        return self.run_main(arguments, answers)

    def clear_operations(self):
        for name in os.listdir(str(self.context.operations_dir)):
            shutil.rmtree(str(self.context.operations_dir / name))

    def legacy_admin_config(self):
        pfx.admin_config(self.config_path, project=PROJECT, backup_read_group=GROUP, workspace_write_group=GROUP)

    # AU-1 (findings 1 and 7): an unloadable registered record is never treated as "not registered".
    def test_au1_pre_registration_refuses_an_unloadable_registered_record(self):
        users_gid()
        other = self.new_home()
        record = self.context.record_path
        good = record.read_bytes()
        self.config_path.unlink()
        record.write_bytes(good[:-4] + b"\xff\xff\xff\xff")
        for directory in (self.config_dir, other["configuration"]):
            with self.subTest(directory=str(directory)):
                before = pfx.snapshot_tree(directory)
                code, out, err, script = self.pre(directory, ["1", "1", "y"], project="partflow-x")
                self.assertEqual(code, 1, out + err)
                self.assertIn("ERROR: registry-record-invalid: pf config admin was refused and nothing was created:",
                              err)
                self.assertIn("  - registry-record-invalid: ", err)
                self.assertIn(": staging: ", err)
                self.assertEqual(script.prompts, [])
                self.assertEqual(pfx.snapshot_tree(directory), before)
        # Damaged between the confirmation and the registry lock: refused by the re-check under the lock.
        record.write_bytes(good)

        def damage_then_confirm():
            record.write_bytes(good[:-4] + b"\xff\xff\xff\xff")
            return "y"

        code, out, err, script = self.pre(other["configuration"], ["1", "1", damage_then_confirm], project="partflow-b")
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: registry-record-invalid: ", err)
        self.assertTrue(script.prompts[-1].startswith("Write "))
        self.assertEqual(os.listdir(str(other["configuration"])), [])

    # AU-2 (finding 2): choosing the proxy binding re-asks a kept `localhost` hostname, with no default.
    def test_au2_the_proxy_binding_re_asks_a_kept_localhost_hostname(self):
        for label, bind in (("empty", ""), ("missing", None), ("invalid", "0.0.0.0")):
            with self.subTest(bind=label):
                self.clear_operations()
                original = env_text(PARTFLOW_BIND_IP=bind, PARTFLOW_ALLOWED_HOST="localhost").encode()
                self.env_path.write_bytes(original)
                code, out, err, script = self.app(["2", "partflow.internal.example", "y"])
                self.assertEqual(code, 0, out + err)
                self.assertEqual([prompt for prompt in script.prompts if not prompt.startswith("Write ")],
                                 ["Select access mode [1]: ", "Exact internal Reverse Proxy hostname: "])
                values = pf_config.parse_app_env(self.env_path.read_bytes(), label=".env")
                self.assertEqual((values["PARTFLOW_BIND_IP"], values["PARTFLOW_ALLOWED_HOST"]),
                                 ("127.0.0.1", "partflow.internal.example"))
                self.assertIn("PARTFLOW_ALLOWED_HOST: localhost -> partflow.internal.example (changed: asked)", out)
                record = self.audits()[0]
                self.assert_valid_record(record)
                row = {row["key"]: row for row in record["changes"]}["PARTFLOW_ALLOWED_HOST"]
                self.assertEqual((row["action"], row["before"], row["after"]),
                                 ("changed", "localhost", "partflow.internal.example"))
        # Typing localhost again keeps it (asked, not changed); Direct LAN never asks the hostname.
        self.clear_operations()
        self.env_path.write_bytes(env_text(PARTFLOW_BIND_IP="", PARTFLOW_ALLOWED_HOST="localhost").encode())
        code, out, err, script = self.app(["2", "localhost", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("PARTFLOW_ALLOWED_HOST: localhost (kept)", out)
        self.assertEqual({row["key"]: row["action"] for row in self.audits()[0]["changes"]}["PARTFLOW_ALLOWED_HOST"],
                         "kept")
        self.env_path.write_bytes(env_text(PARTFLOW_BIND_IP="", PARTFLOW_ALLOWED_HOST="localhost").encode())
        with mock.patch.object(pf.Controller, "detect_lan_ipv4", return_value=["192.168.1.20"]):
            code, out, err, script = self.app(["1", "", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertFalse([prompt for prompt in script.prompts if "hostname" in prompt])
        values = pf_config.parse_app_env(self.env_path.read_bytes(), label=".env")
        self.assertEqual((values["PARTFLOW_BIND_IP"], values["PARTFLOW_ALLOWED_HOST"]), ("192.168.1.20", "localhost"))

    # AU-3 (finding 3): a failure while recording the audit after the publish names the written file.
    def test_au3_an_interrupt_or_io_error_at_the_audit_record_names_the_written_file(self):
        cases = (("interrupt", KeyboardInterrupt("Interrupted by signal 15"), "Interrupted by signal 15"),
                 ("disk full", OSError(28, "No space left on device"), "No space left on device"))
        for label, error, reason in cases:
            with self.subTest(case=label, file="pf-config.json"):
                self.clear_operations()
                self.legacy_admin_config()
                with mock.patch.object(pf.Controller, "write_config_change", side_effect=error):
                    code, out, err, _ = self.admin(["y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: config-audit-not-recorded: {self.config_path} holds the new content; its change "
                              "record ", err)
                self.assertIn(f"{pf_config.CONFIG_CHANGE_NAME} was not recorded ({reason}). Run the command again to "
                              "review the file.", err)
                self.assertEqual(pf_config.parse_admin_config(self.config_path.read_bytes(), label="x").schema_version,
                                 2)
                self.assertEqual(self.audits(), [])
                code, out, err, _ = self.admin()
                self.assertEqual(code, 0, out + err)
                self.assertIn("config-current: ", out)
            with self.subTest(case=label, file=".env"):
                self.clear_operations()
                original = env_text(POSTGRES_PASSWORD="'" + AP1_PASSWORD + "'", PARTFLOW_ALLOWED_HOST=None).encode()
                self.env_path.write_bytes(original)
                with mock.patch.object(pf.Controller, "write_config_change", side_effect=error):
                    code, out, err, _ = self.app(["partflow.internal.example", "y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: config-audit-not-recorded: {self.env_path} holds the new content;", err)
                self.assertEqual(self.env_path.read_bytes(),
                                 original + b"PARTFLOW_ALLOWED_HOST=partflow.internal.example\n")
                self.assertEqual(self.audits(), [])
                self.assert_secret_absent(AP1_PASSWORD, out, err, roots=(self.context.operations_dir,))

    def assert_secret_absent(self, secret, *texts, roots=()):
        AppWizard.assert_secret_absent(self, secret, *texts, roots=roots)

    def interrupting(self, owner, name, when, *, match):
        """Raise KeyboardInterrupt (as a signal handler does) before or after the real call when ``match``."""
        real = getattr(owner, name)

        def wrapper(*args, **kwargs):
            hit = match(*args)
            if hit and when == "before":
                raise KeyboardInterrupt("Interrupted by signal 15")
            result = real(*args, **kwargs)
            if hit and when == "after":
                raise KeyboardInterrupt("Interrupted by signal 15")
            return result

        return mock.patch.object(owner, name, wrapper)

    # AU-4 (findings 4 and 10): an interrupt inside the writer is reported from an observation of the target.
    def test_au4_an_interrupt_inside_the_writer_is_reported_from_the_target(self):
        users_gid()
        own = Crash.own
        cancelled = f"ERROR: config-cancelled: Cancelled; {self.config_path} was not created or changed."
        interrupted = (f"ERROR: config-interrupted: {self.config_path} was written before the interrupt and holds the "
                       "new content; run the command again to review it.")
        rows = (("fchmod of the temp (replace)", "migrate", (os, "fchmod", "before", lambda *a: bool(self.temps())),
                 cancelled),
                ("before rename (replace)", "migrate", (os, "replace", "before", lambda *a: own(a[0])), cancelled),
                ("after rename (replace)", "migrate", (os, "replace", "after", lambda *a: own(a[0])), interrupted),
                ("before link (create)", "create", (os, "link", "before", lambda *a: own(a[0])), cancelled),
                ("between link and unlink (create)", "create", (os, "link", "after", lambda *a: own(a[0])),
                 interrupted),
                ("directory fsync after the publish", "migrate",
                 (pf_instance, "_fsync_directory", "before", lambda *a: Path(a[0]) == self.config_dir), interrupted))
        for label, mode, (owner, name, when, match), expected in rows:
            with self.subTest(window=label):
                self.clear_operations()
                if mode == "create":
                    if self.config_path.exists():
                        self.config_path.unlink()
                    answers = ["1", "1", "y"]
                else:
                    self.legacy_admin_config()
                    answers = ["y"]
                old = self.config_path.read_bytes() if self.config_path.exists() else None
                with self.interrupting(owner, name, when, match=match):
                    code, out, err, _ = self.admin(answers)
                self.assertEqual(code, 1, out + err)
                self.assertIn(expected, err)
                self.assertEqual(self.temps(), [])
                self.assertEqual(self.audits(), [])
                if expected == cancelled:
                    self.assertEqual(self.config_path.read_bytes() if self.config_path.exists() else None, old)
                else:
                    self.assertEqual(os.lstat(str(self.config_path)).st_nlink, 1)
                    self.assertEqual(pf_config.parse_admin_config(self.config_path.read_bytes(),
                                                                  label="x").schema_version, 2)
                    code, out, err, _ = self.admin()
                    self.assertEqual(code, 0, out + err)
                    self.assertIn("config-current: ", out)

    # AU-5 (finding 5): the pre-registration admin-config-invalid copy re-checks the same directory.
    def test_au5_pre_registration_invalid_file_copy_names_the_directory(self):
        prefix = pf_install.launcher_prefix(self.layout.root)
        paths = self.new_home()
        target = paths["configuration"] / "pf-config.json"
        data = json.dumps({"project": "partflow-b", "zz": 1}).encode()
        target.write_bytes(data)
        for project, suffix in ((None, ""), ("partflow-b", " --project partflow-b")):
            with self.subTest(project=project):
                code, out, err, script = self.pre(paths["configuration"], ["y"], project=project)
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: admin-config-invalid: {target}: Unknown configuration keys: zz. Nothing was "
                              f"changed; fix the file by hand, then run '{prefix} config admin --configuration "
                              f"{paths['configuration']}{suffix}' again.", err)
                self.assertEqual((script.prompts, target.read_bytes()), ([], data))

    # AU-6 (finding 6): the init completion copy names the pre-registration wizard.
    def test_au6_init_completion_names_the_pre_registration_wizard(self):
        plan = {"kind": "init", "root": str(self.layout.root), "operation_id": "inst-20261007T000000Z-0123abcd",
                "launcher": None}
        text = pf_install._finish_message({"result": {"release_id": "r1"}}, plan, self.layout.root)
        prefix = pf_install.launcher_prefix(str(self.layout.root))
        self.assertIn(f"Next: create the configuration with '{prefix} config admin --configuration <config> --project "
                      f"<project>', then '{prefix} install register'.", text)
        self.assertNotIn("by hand", text)

    # AU-7 (finding 8): a present SITE_TIMEZONE that is not a zone name is asked, not kept with a host-data note.
    def test_au7_a_present_timezone_that_is_not_a_zone_name_is_asked(self):
        host_zone("UTC")
        declaration = pf_config.app_declaration("partflow-staging-legacy")
        for name in ("Etc/..", "America/../UTC", "./UTC"):
            with self.subTest(name=name):
                self.assertTrue(pf_config.TIMEZONE_RE.fullmatch(name))  # passes the A1 grammar
                plan = pf_config.plan_app_config(declaration, current={"SITE_TIMEZONE": name}, example={},
                                                 deployed=True, check=lambda kind, value: None,
                                                 zone=pf_config.zone_status)
                item = {item.key: item for item in plan}["SITE_TIMEZONE"]
                self.assertEqual((item.action, item.code, item.default), ("ask", None, None))
                original = env_text(SITE_TIMEZONE=name).encode()
                self.env_path.write_bytes(original)
                code, out, err, script = self.app(["UTC", "y"])
                self.assertEqual(code, 0, out + err)
                self.assertEqual(script.prompts[0], "Factory IANA timezone: ")
                self.assertNotIn("zone-unknown-on-host", out)
                self.assertIn(f"SITE_TIMEZONE: {name} -> UTC (changed: asked", out)
                self.assertEqual(self.env_path.read_bytes(),
                                 original.replace(f"SITE_TIMEZONE={name}\n".encode(), b"SITE_TIMEZONE=UTC\n"))

    # AU-8 (finding 9): a parser error never echoes a character of the secret line.
    def test_au8_parser_errors_never_echo_a_character_of_a_secret(self):
        default = b"POSTGRES_PASSWORD=" + b"a" * 64
        cases = (("escape", b'POSTGRES_PASSWORD="Zsecret\\Qpart-0123456789abcdef0123456789"',
                  ":2: unsupported escape in a double-quoted value", ("\\Q", "Q")),
                 ("utf-8", b"POSTGRES_PASSWORD=caf\xe9-part-0123456789abcdef0123456789",
                  ":2: not valid UTF-8 (the bytes are not shown)", ("0xe9", "\\xe9", "position")),
                 ("control", b"POSTGRES_PASSWORD='Zsecret\x07part-0123456789abcdef0123456789'",
                  ":2: a control character is not supported (not shown)", ("U+0007",)))
        for label, line, fragment, forbidden in cases:
            with self.subTest(case=label):
                data = env_text().encode().replace(default, line)
                self.assertNotEqual(data, env_text().encode())
                self.env_path.write_bytes(data)
                code, out, err, script = self.app(["y"])
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"ERROR: app-config-invalid: {self.env_path}: ", err)
                self.assertIn(f"{self.env_path}{fragment}", err)
                for text in forbidden:
                    self.assertNotIn(text, out + err)
                self.assertEqual((script.prompts, self.env_path.read_bytes()), ([], data))
                report = pf_install._Report()
                pf_install._app_env_note(report, self.env_path, verb="register", root=self.layout.root, slug="b")
                self.assertEqual([note["code"] for note in report.notes], ["app-env-unparsed"])
                self.assertIn(fragment, report.notes[0]["detail"])
                for text in forbidden:
                    self.assertNotIn(text, report.notes[0]["detail"])

    # AU-9 (finding 12): Direct LAN through `pf config app` (read-only ip/hostname children, mocked here).
    def test_au9_direct_lan_through_config_app_derives_localhost_and_cancels_at_the_ipv4_question(self):
        original = env_text(PARTFLOW_BIND_IP=None, PARTFLOW_ALLOWED_HOST=None).encode()
        self.env_path.write_bytes(original)
        with mock.patch.object(pf.Controller, "detect_lan_ipv4", return_value=["192.168.1.20"]):
            code, out, err, script = self.app(["1", "q"])
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"ERROR: config-cancelled: Cancelled at PARTFLOW_BIND_IP; {self.env_path} was not created or "
                      "changed.", err)
        self.assertEqual(script.prompts, ["Select access mode [1]: ",
                                          "NAS LAN IPv4 (enter an address or detected number) [1]: "])
        self.assertEqual((self.env_path.read_bytes(), self.temps(), self.audits()), (original, [], []))
        with mock.patch.object(pf.Controller, "detect_lan_ipv4", return_value=["192.168.1.20"]):
            code, out, err, script = self.app(["1", "", "y"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("Detected NAS LAN IPv4 addresses:\n  1. 192.168.1.20", out)
        self.assertIn("PARTFLOW_ALLOWED_HOST: (missing) -> localhost (derived from access mode)", out)
        values = pf_config.parse_app_env(self.env_path.read_bytes(), label=".env")
        self.assertEqual((values["PARTFLOW_BIND_IP"], values["PARTFLOW_ALLOWED_HOST"]), ("192.168.1.20", "localhost"))
        record = self.audits()[0]
        self.assert_valid_record(record)
        actions = {row["key"]: row["action"] for row in record["changes"]}
        self.assertEqual((actions["PARTFLOW_BIND_IP"], actions["PARTFLOW_ALLOWED_HOST"]), ("added", "added"))


# ============================================================================ SK: smoke compatibility


import test_install as ti  # noqa: E402  (the A2.1 installer fixtures; imported as a module so no case is collected)


A21_VALIDATOR = b'''

def validate_admin_config(data, *, label):  # the PF-A2.1 body (test-only candidate mutation)
    try:
        supplied = pf_bootstrap.parse_strict_json(data, label=label)
    except pf_bootstrap.BootstrapError as exc:
        return None, [str(exc)]
    if not isinstance(supplied, dict):
        return None, ["Runtime configuration must be a JSON object: " + label]
    return _schema1_problems(supplied)
'''


@ROOT_REQUIRED
class SmokeCompatibility(ti.InstallBase):
    instances = ("a",)

    def schema2(self):
        path = self.paths["a"]["configuration"] / "pf-config.json"
        values = pf_config.validate_admin_config(path.read_bytes(), label="x")[0]
        path.write_bytes(pf_config.render_admin_config(pf_config.admin_document(values)))
        return path

    def test_sk1_an_a22_candidate_accepts_schema_2_and_changes_no_config(self):
        self.schema2()
        configs = self.config_bytes()
        source = self.candidate()
        rid = self.release_id(source)
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.journal()["phase"], "completed")
        self.assertEqual(self.config_bytes(), configs)
        self.assert_one_generation(self.root / "releases" / rid)

    def test_sk2_an_a21_validator_rejects_schema_2_and_nothing_is_bound(self):
        self.schema2()
        source = self.candidate(mutate={"deploy/synology/pf_config.py": lambda data: data + A21_VALIDATOR})
        rid = self.release_id(source)
        conf = (self.root / "bootstrap/bootstrap.conf").read_bytes()
        code, out, err = self.run_pf(["install", "control", "--source", str(source)], ["INSTALL CONTROL " + rid])
        self.assertEqual(code, 1, out + err)
        self.assertIn("install-smoke-failed:", err)
        self.assertIn("config of a rejected by the candidate (config)", err)
        self.assertEqual((self.root / "bootstrap/bootstrap.conf").read_bytes(), conf)


@ROOT_REQUIRED
class MarkerGolden(ti.InstallBase):
    """SC-3: validate_document gives the same results as the A2.1 walker for every plan/journal fixture."""

    instances = ("a",)

    @staticmethod
    def a21_validate(value, name):
        schema_defs = pf_install.INSTALL_OPERATION_SCHEMA["$defs"]

        def target(item, marker, path, errors):
            if marker.startswith("$defs."):
                definition(item, marker[len("$defs."):], path, errors)
            elif marker == "string":
                if not isinstance(item, str):
                    errors.append(f"{path}: expected string")
            elif marker == "sha256":
                if not (isinstance(item, str) and re.fullmatch("[a-f0-9]{64}", item)):
                    errors.append(f"{path}: expected sha256")
            elif marker == "path":
                if not (isinstance(item, str) and pf_instance.canonical_path_error(item) is None):
                    errors.append(f"{path}: expected a canonical absolute path")
            elif marker == "release-id":
                if not (isinstance(item, str) and re.fullmatch("[A-Za-z0-9][A-Za-z0-9._-]{0,127}", item)):
                    errors.append(f"{path}: expected a release id")

        def walk(item, schema, path, errors):
            if not isinstance(item, dict):
                return
            for key, sub in schema.get("properties", {}).items():
                if key not in item:
                    continue
                marker, element, where = sub.get("description", ""), item[key], f"{path}.{key}"
                if marker.startswith("null or "):
                    if element is not None:
                        target(element, marker[8:], where, errors)
                elif marker.startswith("items "):
                    if isinstance(element, list):
                        for index, entry in enumerate(element):
                            target(entry, marker[6:], f"{where}[{index}]", errors)
                elif marker.startswith("map "):
                    if isinstance(element, dict):
                        for entry_key, entry in element.items():
                            target(entry, marker[4:], f"{where}.{entry_key}", errors)
                elif sub.get("type") == "object":
                    walk(element, sub, where, errors)

        def definition(item, def_name, path, errors):
            schema = schema_defs[def_name]
            errors.extend(pf_instance.validate_against_schema(item, schema, path))
            walk(item, schema, path, errors)
            return errors

        return definition(value, name, "$", [])

    def test_sc3_marker_refactor_keeps_every_result(self):
        journal = self.operation("control", self.control_request(self.candidate()))
        plan = pf_install.load_operation(self.root, journal["operation_id"])[0]
        fixtures = [("plan", plan), ("journal", journal),
                    ("plan", dict(plan, extra=1)), ("plan", {k: v for k, v in plan.items() if k != "bindings"}),
                    ("journal", dict(journal, sequence=True)), ("journal", dict(journal, phase="half-done")),
                    ("plan", dict(plan, candidate=dict(plan["candidate"], source_root="relative"))),
                    ("journal", dict(journal, effects=[dict(journal["effects"][0], state="maybe")])),
                    ("plan", dict(plan, candidate=dict(plan["candidate"], files={"x": "ABC"}))),
                    ("plan", dict(plan, candidate=None)), ("journal", []), ("plan", "x")]
        for index, (name, document) in enumerate(fixtures):
            with self.subTest(fixture=index):
                self.assertEqual(pf_install.validate_document(document, name), self.a21_validate(document, name))
        self.assertEqual(pf_install.validate_document(plan, "plan"), [])


# ============================================================================ PO: policy versioned separately


@ROOT_REQUIRED
class Policy(ConfigBase):

    def test_po1_an_unsupported_policy_schema_is_refused_with_its_own_message(self):
        import dataclasses
        path = self.layout.root / "policies" / "staging-v2.json"
        data = pf_instance.normalize_json({"schema_version": 2, "revision": 1, "environment": "staging"})
        pf_instance._write_private_file(path, data, 0o600)
        context = dataclasses.replace(self.context, approved_policy=pf_instance.PolicyBinding(
            revision=1, path=path, sha256=pf_instance.sha256_bytes(data)))
        self.assertEqual(pf_instance.POLICY_SCHEMA_VERSION, 1)
        with self.assertRaises(pf_instance.ContextError) as caught:
            pf_instance.load_policy(context)
        self.assertEqual(str(caught.exception), f"Unsupported policy schema_version 2 in {path}; this control reads policy "
                                                "schema 1. The approved policy was not changed; select the control release "
                                                "that wrote it.")
        validation = pf_instance.validate_context(context, running_release=self.layout.release_dir)
        self.assertIn("policy-invalid", validation.refused_codes())
        self.assertEqual(path.read_bytes(), data)


# ============================================================================ LC-1: schema 2 lifecycle subset


import test_pf_admin as tpa  # noqa: E402  (imported as a module so no case is collected twice)


def to_schema2(path, **values):
    path = Path(path)
    current = pf_config.validate_admin_config(path.read_bytes(), label=str(path))[0]
    current.update(values)
    path.write_bytes(pf_config.render_admin_config(pf_config.admin_document(current)))


LIFECYCLE_ADMIN = {"test_deploy_latest_creates_new_database_migrates_and_opens_app",
                   "test_update_backs_up_then_switches_source_and_images",
                   "test_completed_checkpoint_is_group_readable_but_not_group_writable",
                   "test_full_rollback_restores_old_schema_and_keeps_new_database",
                   "test_prepare_new_env_generates_password_and_direct_lan_values"}
LIFECYCLE_PURGE = {"test_purge_requires_recovery_then_multiple_confirmations_before_cleanup",
                   "test_side_by_side_restore_never_replaces_active_database"}


class Schema2LifecycleAdmin(tpa.AdminTests):
    """LC-1: the A1 deploy/update/backup/rollback runs with a schema 2 pf-config.json give the same outcomes."""

    def setUp(self):
        super().setUp()
        to_schema2(self.root.parent / "config" / "pf-config.json")
        self.c = tpa.FakeController(self.context)
        self.assertEqual(self.c.config_schema_version, 2)


class Schema2LifecyclePurge(tpa.PurgeRecoveryTests):
    def setUp(self):
        super().setUp()
        to_schema2(self.root.parent / "config" / "pf-config.json")
        self.c = tpa.FakeController(self.context)
        self.assertEqual(self.c.config_schema_version, 2)


for _case, _keep in ((Schema2LifecycleAdmin, LIFECYCLE_ADMIN), (Schema2LifecyclePurge, LIFECYCLE_PURGE)):
    for _name in [name for name in dir(_case) if name.startswith("test_")]:
        if _name not in _keep:
            setattr(_case, _name, None)
del _case, _keep, _name  # a module-level alias of a TestCase class would be collected twice


@ROOT_REQUIRED
class Schema2Purge(unittest.TestCase):
    """LC-1: backups use the schema 2 backup_read_group; purge with and without --reset-admin-config."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = tpa.fixture(self.root)
        self.layout = tpa.fixture.layout
        self.config_path = self.root.parent / "config" / "pf-config.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_lc1_backup_group_and_purge_reset_with_schema_2(self):
        gid = users_gid()
        to_schema2(self.config_path, backup_read_group="users")
        controller = tpa.FakeController(self.context)
        with contextlib.redirect_stdout(io.StringIO()):
            checkpoint = tpa.checkpoint(controller)
        folder = controller.backups_dir / checkpoint.bundle_id
        # PF-A2.3 (OD-A22-20): the schema 2 value is a proposal; the unapproved instance keeps the folder's group.
        root_gid = controller.backups_root.stat().st_gid
        self.assertNotEqual(root_gid, gid)
        self.assertEqual(folder.stat().st_gid, root_gid)
        self.assertTrue(all(item.stat().st_gid == root_gid for item in folder.iterdir()))
        # Approving the proposal with `pf permissions apply` makes it the group of the next checkpoint.
        stdout = io.StringIO()
        answers = [""] * 7 + ["users", "", "users", ""] + ["APPLY PERMISSIONS staging"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout), \
                mock.patch.object(pf, "unattended", return_value=False), \
                mock.patch("builtins.input", Script(answers, stdout)):
            code = pf.main(["--instance", "staging", "permissions", "apply"], installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        self.assertEqual(code, 0, stdout.getvalue())
        controller = tpa.FakeController(self.context)
        with contextlib.redirect_stdout(io.StringIO()):
            checkpoint = tpa.checkpoint(controller)
        folder = controller.backups_dir / checkpoint.bundle_id
        self.assertEqual(folder.stat().st_gid, gid)
        self.assertTrue(all(item.stat().st_gid == gid for item in folder.iterdir()))
        prefix = pf_install.launcher_prefix(self.layout.root) + " --instance staging"
        for reset in (False, True):
            with self.subTest(reset=reset):
                controller = tpa.FakeController(self.context)
                out = io.StringIO()
                with contextlib.redirect_stdout(out), controller.lock(), \
                        mock.patch.object(controller, "execute_deletion_plan"):
                    controller.finish_purge_cleanup("purge-x", {}, delete_backups=False, reset_admin_config=reset)
                self.assertEqual(self.config_path.exists(), not reset)
                expected = (f"Next: {prefix} config admin, then {prefix} deploy --latest." if reset
                            else f"Next: {prefix} deploy --latest.")
                self.assertIn(expected, out.getvalue())
                self.assertNotIn("sudo pf deploy", out.getvalue().replace(prefix, ""))
        # AW-A1 after `purge --reset-admin-config`: the wizard recreates the file (nothing else reads it first).
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout), \
                mock.patch.object(pf, "unattended", return_value=False), \
                mock.patch("builtins.input", Script(["1", "1", "y"], stdout)):
            code = pf.main(["--instance", "staging", "config", "admin"], installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        self.assertEqual(code, 0, stdout.getvalue())
        self.assertEqual(pf_config.parse_admin_config(self.config_path.read_bytes(), label="x").schema_version, 2)


if __name__ == "__main__":
    unittest.main()
