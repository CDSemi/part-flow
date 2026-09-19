"""Offline tests. Docker/PostgreSQL are simulated; archive and filesystem work is real."""
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
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

PACKAGE = pfx.PACKAGE
REPO_PACKAGE = pfx.REPO_PACKAGE
pf = pfx.pf
pf_instance = pfx.pf_instance
OLD = pfx.OLD
NEW = pfx.NEW
TEST_GROUP = grp.getgrgid(os.getgid()).gr_name
source_fixture = pfx.source_fixture


def fixture(root, revision=OLD, new_migration=False):
    """Disposable protected installation: <tmp>/install (registry/release) + <tmp>/{repo,config,backups,recovery}.

    Returns the registered InstanceContext for slug ``staging`` / project ``partflow-staging``.
    v2.5 built an unregistered control/config layout here; PF-A1.1 requires the
    explicit registration transaction before any Controller exists.
    """
    root = Path(root)
    home = root.parent
    home.mkdir(parents=True, exist_ok=True)
    layout = pfx.install_root(home)
    paths = pfx.data_home(home, group=TEST_GROUP, revision=revision)
    if new_migration:
        (root / "backend/alembic/versions/002.py").write_text("revision='r2'\ndown_revision='r1'\n")
    context = pfx.register(layout, "staging", paths, project="partflow-staging")
    pfx.deployed_record(context, revision)
    fixture.layout = layout
    return context



def write_deploy_env(root, bind_ip="127.0.0.1"):
    config = root.parent / "config"
    config.mkdir(exist_ok=True)
    (config / ".env").write_text(
        "POSTGRES_USER=partflow_staging\n"
        "POSTGRES_PASSWORD=" + "a" * 64 + "\n"
        "POSTGRES_DB=partflow_staging\n"
        "SITE_TIMEZONE=America/Los_Angeles\n"
        f"PARTFLOW_BIND_IP={bind_ip}\n"
        "PARTFLOW_HTTP_PORT=5173\n"
        "PARTFLOW_ALLOWED_HOST=localhost\n"
    )




class FakeController(pf.Controller):
    """Simulate only external commands; execute the real workflow/backup code."""
    def __init__(self, context):
        super().__init__(context)
        self.config["minimum_free_mb"] = 1
        self.dbs = {"partflow_staging": {"heads": ["r1"], "rows": ["old-record"], "connections": True}}
        self.tags = {"backend:old": "sha256:old-backend", "frontend:old": "sha256:old-frontend"}
        self.contracts = {"sha256:old-backend": {"files": pf.migration_files(self.root), "heads": ["r1"]}}
        self.current_images = {"backend": "sha256:old-backend", "frontend": "sha256:old-frontend"}
        self.running = {"db": True, "backend": True, "frontend": True}
        self.calls = []
        self.fail = None
        self.new_migration = False
        self.target = {"sha": NEW, "ref": "v0.1.0-alpha.2", "release_id": 2}
        self.connected_sessions = 0
        self.ci_calls = []
        self.resources = {"containers": [], "volumes": []}
        self.workspace_head = OLD
        self.workspace_dirty = False

    def workspace_status(self, root=None):
        # Simulated protected-manifest comparison (PF-A1.2): the fake tracks what was deployed.
        root = Path(root or self.root)
        if root == self.root:
            return {
                "head": self.workspace_head,
                "dirty": self.workspace_dirty,
                "changes": ["changed:frontend/app.txt"] if self.workspace_dirty else [],
                "provenance": "unknown" if self.workspace_dirty else "git_commit",
                "manifest_commit": self.workspace_head,
            }
        return {"head": self.target["sha"], "dirty": False, "changes": [], "provenance": "git_commit",
                "manifest_commit": self.target["sha"]}

    def create_deployed_source_archive(self, destination, revision):
        with tempfile.TemporaryDirectory() as tmp:
            exact = Path(tmp) / "exact"
            source_fixture(exact, revision)
            pf.create_source_archive(exact, destination)

    def replace_source(self, candidate, revision, *, verified=True):
        super().replace_source(candidate, revision, verified=verified)
        self.workspace_head = revision
        self.workspace_dirty = False

    def inspect(self, service):
        return {"Image": self.current_images.get(service, "sha256:postgres"),
                "State": {"Running": self.running[service], "Health": {"Status": "healthy"}},
                "Config": {"Env": ["POSTGRES_DB=partflow_staging", "POSTGRES_USER=partflow_staging"]}}

    def docker(self, *args, **kwargs):
        self.calls.append(("docker", args))
        if args[:2] in (("image", "save"), ("image", "load")):
            return self.command(["docker", *args], **kwargs)
        if args[0] == "version":
            return "28.0.0"
        if args[0] == "tag":
            self.tags[args[2]] = args[1]
            return ""
        if args[:2] == ("image", "inspect"):
            if args[2] not in self.tags:
                raise pf.Failure("image missing")
            return json.dumps([{"Id": self.tags[args[2]]}])
        if args[:2] == ("ps", "-q"):
            return ""
        raise AssertionError(("docker", args))

    def sql(self, database, sql, *, mutation=False):
        self.calls.append(("sql", database, sql))
        if sql == "SELECT 1;":
            assert database in self.dbs
            return "1"
        if "SHOW server_version_num" in sql:
            return "160003"
        if "to_regclass" in sql:
            return "t" if self.dbs[database]["heads"] else "f"
        if sql.startswith("SELECT version_num"):
            return "\n".join(self.dbs[database]["heads"])
        if "pg_stat_activity" in sql:
            return str(self.connected_sessions)
        if sql.startswith("BEGIN;"):
            if self.fail == "swap":
                raise pf.Failure("simulated transaction failure")
            renames = re.findall(r'ALTER DATABASE "([^"]+)" RENAME TO "([^"]+)"', sql)
            current, retained = renames[0]
            prepared, target = renames[1]
            assert current == target and retained not in self.dbs
            self.dbs[retained] = self.dbs.pop(current)
            self.dbs[retained]["connections"] = False
            self.dbs[current] = self.dbs.pop(prepared)
            return "COMMIT"
        raise AssertionError((database, sql))

    def override_images(self, override=None):
        selected = Path(override) if override else self.override
        if not selected.exists():
            return dict(self.current_images)
        result = {}
        service = None
        for line in selected.read_text().splitlines():
            if line.startswith("  ") and not line.startswith("    "):
                service = line.strip().rstrip(":")
            if "image:" in line:
                result[service] = self.tags[json.loads(line.split("image:", 1)[1].strip())]
        return result

    def compose(self, *args, root=None, override=None, output=None, input_file=None, env=None, **kwargs):
        self.calls.append(("compose", args, env))
        if args[0] == "stop":
            for service in args[1:]:
                self.running[service] = False
            return ""
        if args[0] == "down":
            self.running = {"db": False, "backend": False, "frontend": False}
            self.dbs.pop("partflow_staging", None)
            self.resources = {"containers": [], "volumes": []}
            return ""
        if args[0] == "run":
            images = self.override_images(override)
            contract = self.contracts[images["backend"]]
            if "python" in args:
                return json.dumps(contract)
            assert "alembic" in args
            database = (env or {}).get("POSTGRES_DB", "partflow_staging")
            if self.fail == "migration" or self.fail == "live-migration" and database == "partflow_staging":
                raise pf.Failure("simulated migration failure")
            self.dbs[database]["heads"] = list(contract["heads"])
            return ""
        if args[0] == "up":
            service = args[-1]
            if service == "db":
                self.running["db"] = True
                self.dbs.setdefault("partflow_staging", {"heads": [], "rows": [], "connections": True})
                return ""
            self.current_images[service] = self.override_images(override)[service]
            self.running[service] = True
            return ""
        if args[0] == "config":
            return ""
        if args[0] == "exec":
            if "frontend" in args:
                return json.dumps({"status": "ok", "database": "connected"})
            # PF-A1.2 (A12-R03): database programs are direct argv inside the db service.
            program = args[3]
            assert "sh" not in args and "-c" not in args[:4], args
            if program == "pg_restore" and "--list" in args:
                content = input_file.read()
                if output:
                    output.write(b"mock archive list\n")
                return ""
            assert args[4:6] == ("-U", "partflow_staging"), args  # the frozen role, not a shell variable
            if program == "pg_dump":
                if self.fail == "dump":
                    raise pf.Failure("simulated dump failure")
                output.write(json.dumps(self.dbs["partflow_staging"]).encode())
                return ""
            if program == "pg_restore":
                if self.fail == "restore":
                    raise pf.Failure("simulated restore failure")
                self.dbs[args[args.index("-d") + 1]] = json.loads(input_file.read())
                return ""
            if program == "createdb":
                name = args[-1]
                assert name not in self.dbs
                self.dbs[name] = {"heads": [], "rows": [], "connections": True}
                return ""
            if program == "dropdb":
                name = args[-1]
                assert name.startswith(("pf_verify_", "pf_migrate_"))
                del self.dbs[name]
                return ""
        raise AssertionError(args)

    def wait_health(self, service):
        if self.fail == "health" and service == "backend":
            raise pf.Failure("simulated backend health timeout")
        if self.fail == "frontend-health" and service == "frontend":
            self.dbs["partflow_staging"]["rows"].append("write-during-opening")
            raise pf.Failure("simulated frontend timeout after possible client access")
        assert self.running[service]

    def require_ci(self, sha):
        self.ci_calls.append(sha)
        if self.fail == "ci":
            raise pf.Deferred("CI pending")

    def materialize_source(self, target, destination):
        # The real method fetches into the protected store and exports blobs; the fake
        # produces the same kind of private candidate tree without Git or network.
        assert self.operation_dir is not None, "source materialization requires a locked operation"
        source_fixture(destination, target["sha"], self.new_migration)

    def build_target(self, candidate, sha):
        images = {}
        for service in ("backend", "frontend"):
            image_id = "sha256:new-" + service
            reference = service + ":candidate"
            self.tags[reference] = image_id
            images[service] = {"reference": reference, "id": image_id}
        self.contracts["sha256:new-backend"] = {
            "files": pf.migration_files(candidate), "heads": ["r2" if self.new_migration else "r1"]}
        override = self.state / "candidate-images.yaml"
        self.make_override(images, override)
        return images, override

    def automatic_guard(self, current, candidate, target_sha):
        if self.fail == "divergence":
            raise pf.Deferred("not a descendant")
        if self.new_migration:
            raise pf.Deferred("migration change")

    def project_resources(self):
        return self.resources

    def ensure_listener_available(self, values):
        self.calls.append(("listener", values["PARTFLOW_BIND_IP"], values["PARTFLOW_HTTP_PORT"]))

    def resolve(self, **kwargs):
        return self.target


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = fixture(self.root)
        self.layout = fixture.layout
        self.c = FakeController(self.context)
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.error_output = io.StringIO()
        self.errors = contextlib.redirect_stderr(self.error_output)
        self.errors.__enter__()

    def tearDown(self):
        self.errors.__exit__(None, None, None)
        self.capture.__exit__(None, None, None)
        self.temp.cleanup()

    def errors_text(self):
        return self.error_output.getvalue()

    def invoke(self, arguments):
        with (
            mock.patch.object(pf, "Controller", return_value=self.c),
            mock.patch.object(pf, "confirm"),
            mock.patch.object(pf, "prompt_yes_no", return_value=True),
        ):
            return pf.main(arguments, installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)

    def test_deploy_latest_creates_new_database_migrates_and_opens_app(self):
        write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        self.c.dbs = {}
        self.c.running = {"db": False, "backend": False, "frontend": False}
        self.assertEqual(self.invoke(["deploy", "--latest"]), 0)
        self.assertEqual(self.c.revision(), NEW)
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r1"])
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], [])
        self.assertTrue(all(self.c.running.values()))
        self.assertFalse(self.c.pending.exists())
        deployed = pf.load_json(self.c.state / "deployed.json")
        self.assertTrue(deployed["initial_deploy"])
        self.assertEqual(deployed["sha"], NEW)
        self.assertEqual(self.c.ci_calls, [NEW])
        self.assertEqual(self.c.snapshots(), [])

    def test_abort_deploy_removes_only_incomplete_pre_frontend_resources(self):
        write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        self.c.running = {"db": True, "backend": False, "frontend": False}
        self.c.dbs = {"partflow_staging": {"heads": [], "rows": [], "connections": True}}
        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        pf.write_json(self.c.pending, {
            "operation": "deploy", "phase": "migrating-database",
            "database": "partflow_staging", "started": "20260910T000000Z",
        })
        self.assertEqual(self.invoke(["abort-deploy"]), 0)
        self.assertFalse(self.c.pending.exists())
        self.assertNotIn("partflow_staging", self.c.dbs)
        self.assertFalse(any(self.c.running.values()))
        self.assertTrue((self.c.config_dir / ".env").exists())

    def test_abort_deploy_refuses_cleanup_after_frontend_may_have_opened(self):
        write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        pf.write_json(self.c.pending, {
            "operation": "deploy", "phase": "opening-frontend",
            "database": "partflow_staging", "started": "20260910T000000Z",
        })
        self.assertEqual(self.invoke(["abort-deploy"]), 1)
        self.assertTrue(self.c.pending.exists())
        self.assertIn("partflow_staging", self.c.dbs)

    def test_deploy_refuses_existing_project_resources_without_touching_database(self):
        write_deploy_env(self.root)
        self.c.resources = {"containers": ["abc"], "volumes": ["partflow-staging_postgres_data"]}
        before = dict(self.c.dbs)
        self.assertEqual(self.invoke(["deploy", "--latest"]), 1)
        self.assertEqual(self.c.dbs, before)
        self.assertFalse(self.c.pending.exists())
        self.assertTrue(all(self.c.running.values()))

    def test_prepare_new_env_generates_password_and_direct_lan_values(self):
        self.c = pf.Controller(self.context)
        (self.c.config_dir / ".env").unlink()
        answers = iter(["", "", "", "1", "1", "", "y"])
        with (
            mock.patch.object(pf.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=lambda *args: next(answers)),
            mock.patch.object(self.c, "detect_lan_ipv4", return_value=["192.168.0.11"]),
            self.c.lock(),
        ):
            values = self.c.prepare_new_env()
            # The operation consumes the file it wrote, frozen once (PF-A1.2).
            self.assertIsNotNone(self.c.frozen)
            self.assertEqual(dict(self.c.frozen.values), values)
        saved = pf.read_app_env(self.c.config_dir / ".env")
        self.assertEqual(saved, values)
        self.assertEqual(saved["POSTGRES_USER"], "partflow_staging")
        self.assertEqual(saved["POSTGRES_DB"], "partflow_staging")
        self.assertEqual(saved["SITE_TIMEZONE"], "America/Los_Angeles")
        self.assertEqual(saved["PARTFLOW_BIND_IP"], "192.168.0.11")
        self.assertEqual(saved["PARTFLOW_HTTP_PORT"], "5173")
        self.assertEqual(saved["PARTFLOW_ALLOWED_HOST"], "localhost")
        self.assertRegex(saved["POSTGRES_PASSWORD"], r"[0-9a-f]{64}")
        self.assertEqual(stat.S_IMODE((self.c.config_dir / ".env").stat().st_mode), 0o660)

    def test_prepare_new_env_reverse_proxy_requires_exact_hostname(self):
        self.c = pf.Controller(self.context)
        (self.c.config_dir / ".env").unlink()
        answers = iter(["", "", "", "2", "partflow.internal.example", "", "y"])
        with (
            mock.patch.object(pf.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=lambda *args: next(answers)),
            self.c.lock(),
        ):
            values = self.c.prepare_new_env()
        self.assertEqual(values["PARTFLOW_BIND_IP"], "127.0.0.1")
        self.assertEqual(values["PARTFLOW_ALLOWED_HOST"], "partflow.internal.example")

    def test_prepare_new_env_never_overwrites_existing_env_without_reuse_confirmation(self):
        write_deploy_env(self.root)
        before = (self.c.config_dir / ".env").read_bytes()
        with mock.patch.object(pf, "prompt_yes_no", return_value=False), self.c.lock():
            with self.assertRaises(pf.Failure):
                self.c.prepare_new_env()
        self.assertEqual((self.c.config_dir / ".env").read_bytes(), before)

    def test_render_env_template_rejects_missing_required_sample_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample"
            sample.write_text("POSTGRES_USER=x\n")
            with self.assertRaises(pf.Failure):
                pf.render_env_template(sample, {"POSTGRES_USER": "x"})

    def test_update_backs_up_then_switches_source_and_images(self):
        control_before = (self.c.control_dir / "pf-admin.py").read_text()
        config_before = (self.c.config_dir / "pf-config.json").read_text()
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        self.assertEqual(self.c.revision(), NEW)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertTrue(all(self.c.running.values()))
        self.assertFalse(self.c.pending.exists())
        saved = self.c.snapshots()[0]
        self.assertEqual(saved["source_revision"], OLD)
        self.assertEqual(saved["restore_test"], "passed")
        self.assertEqual(self.c.ci_calls, [NEW])
        self.assertEqual((self.root / "pf.sh").read_text(), "# repository source copy\n")
        self.assertEqual((self.root / "deploy/synology/pf-admin.py").read_text(), "# repository source helper\n")
        self.assertEqual((self.c.control_dir / "pf-admin.py").read_text(), control_before)
        self.assertEqual((self.c.config_dir / "pf-config.json").read_text(), config_before)
        self.assertEqual(self.c.env()["POSTGRES_PASSWORD"], "abc123")

    def test_replace_source_replaces_entire_repository_but_preserves_external_control_and_config(self):
        (self.root / "compose.nas.yaml").write_text("local compose\n")
        (self.root / "docs/deployment").mkdir(parents=True)
        (self.root / "docs/deployment/SYNOLOGY_ADMIN.md").write_text("old docs\n")
        control_before = (self.c.control_dir / "pf-admin.py").read_text()
        config_before = (self.c.config_dir / "pf-config.json").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate"
            source_fixture(candidate, NEW)
            (candidate / "compose.nas.yaml").write_text("remote compose\n")
            (candidate / "deploy/synology/pf-admin.py").write_text("remote controller source\n")
            (candidate / "docs/deployment").mkdir(parents=True)
            (candidate / "docs/deployment/SYNOLOGY_ADMIN.md").write_text("new docs\n")
            pf.write_json(self.c.pending, {"operation": "test", "phase": "paused"})
            self.c.replace_source(candidate, NEW)
        self.assertEqual((self.root / "compose.nas.yaml").read_text(), "remote compose\n")
        self.assertEqual((self.root / "deploy/synology/pf-admin.py").read_text(), "remote controller source\n")
        self.assertEqual((self.root / "docs/deployment/SYNOLOGY_ADMIN.md").read_text(), "new docs\n")
        self.assertEqual((self.c.control_dir / "pf-admin.py").read_text(), control_before)
        self.assertEqual((self.c.config_dir / "pf-config.json").read_text(), config_before)

    def test_controller_reads_runtime_config_from_external_config_directory(self):
        config = self.root.parent / "config/pf-config.json"
        config.write_text(json.dumps({
            "minimum_free_mb": 1234,
            "backup_read_group": TEST_GROUP,
            "workspace_write_group": TEST_GROUP,
        }) + "\n")
        controller = pf.Controller(self.context)
        self.assertEqual(controller.config["minimum_free_mb"], 1234)

    def test_controller_never_creates_runtime_config_from_installed_control_template(self):
        # v2.5 wrote config/pf-config.json from the template during construction
        # (F12). PF-A1.1: construction is read-only; a missing configuration is a
        # diagnostic failure when it is first needed, and the template is untouched.
        config = self.root.parent / "config/pf-config.json"
        example = self.c.control_dir / "pf-config.example.json"
        config.unlink()
        before = pfx.snapshot_tree(self.root.parent / "config", self.c.control_dir)

        controller = pf.Controller(self.context)
        with self.assertRaisesRegex(pf.Failure, "does not create it"):
            controller.load_app_config()

        self.assertFalse(config.exists())
        self.assertTrue(example.is_file())
        self.assertEqual(pfx.snapshot_tree(self.root.parent / "config", self.c.control_dir), before)

    def test_compose_uses_external_env_control_file_and_explicit_repo_context(self):
        controller = pf.Controller(self.context)
        controller.cli = ["docker", "compose"]
        with mock.patch.object(controller, "command", return_value="") as command:
            controller.compose("config", "-q")
        argv = command.call_args.args[0]
        kwargs = command.call_args.kwargs
        self.assertIn("--project-directory", argv)
        self.assertEqual(argv[argv.index("--project-directory") + 1], str(self.root))
        self.assertIn("--env-file", argv)
        # Outside an operation Compose reads no editable file: the registration-created empty
        # env-file replaces <project-directory>/.env, and the values travel as allowlisted variables.
        self.assertEqual(argv[argv.index("--env-file") + 1], str(self.context.diagnostic_env_path))
        self.assertIn("-f", argv)
        self.assertEqual(argv[argv.index("-f") + 1], str(controller.control_dir / "compose.nas.yaml"))
        self.assertEqual(kwargs["env"]["PARTFLOW_REPO_ROOT"], str(self.root))
        self.assertEqual(kwargs["env"]["POSTGRES_DB"], "partflow_staging")
        self.assertEqual(kwargs["env"]["PARTFLOW_DATABASE_URL"],
                         "postgresql+psycopg://partflow_staging:abc123@db:5432/partflow_staging")
        self.assertNotIn("PATH", kwargs["env"])
        # Inside a locked operation the env-file is the frozen private snapshot of that operation.
        with mock.patch.object(controller, "command", return_value="") as command, controller.lock():
            controller.compose("config", "-q")
            argv = command.call_args.args[0]
            self.assertEqual(argv[argv.index("--env-file") + 1], str(controller.frozen.env_file))
            self.assertEqual(controller.frozen.env_file.parent, controller.operation_dir)

    def test_permissions_make_repo_and_config_writable_but_backups_read_only(self):
        source = self.root / "frontend/app.txt"
        os.chmod(source, 0o600)
        backup = self.c.backups_dir / "read-only.txt"
        backup.parent.mkdir(parents=True)
        backup.write_text("backup\n")
        os.chmod(backup, 0o600)
        config = self.c.config_dir / "pf-config.json"
        os.chmod(config, 0o600)

        self.c.permissions()

        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o2770)
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o660)
        self.assertEqual(stat.S_IMODE(self.c.config_dir.stat().st_mode), 0o2770)
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o660)
        self.assertEqual(stat.S_IMODE(self.c.backups_dir.stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.c.state.stat().st_mode), 0o700)

    def test_control_plane_refuses_group_writable_runtime_script(self):
        script = self.c.control_dir / "pf-admin.py"
        os.chmod(script, 0o660)
        with self.assertRaisesRegex(pf.Failure, "group/world writable"):
            self.c.require_trusted_context()

    def test_only_explicit_permissions_command_repairs_backup_permissions_for_smb_read(self):
        backup_id = "20260909T120000Z-" + OLD[:12] + "-abcdef"
        folder = self.root.parent / "backups/revisions/partflow-staging" / backup_id
        folder.mkdir(parents=True)
        file = folder / "manifest.json"
        file.write_text("{}\n")
        os.chmod(folder, 0o700)
        os.chmod(file, 0o600)

        # Construction (v2.5 repaired here, F12) changes nothing.
        pf.Controller(self.context)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)

        # The explicit, locked permissions command does.
        self.c.permissions()
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o640)
        self.assertEqual(folder.stat().st_gid, grp.getgrnam(TEST_GROUP).gr_gid)
        self.assertEqual(file.stat().st_gid, grp.getgrnam(TEST_GROUP).gr_gid)

    def test_completed_checkpoint_is_group_readable_but_not_group_writable(self):
        checkpoint = self.c.snapshot("permission-test")
        folder = self.c.backups_dir / checkpoint["id"]

        for directory in (self.c.backups_root, self.c.revisions_root, self.c.backups_dir, folder):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o750)
            self.assertEqual(directory.stat().st_gid, grp.getgrnam(TEST_GROUP).gr_gid)

        for file in folder.iterdir():
            if file.is_file():
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o640, file.name)
                self.assertEqual(file.stat().st_gid, grp.getgrnam(TEST_GROUP).gr_gid)

        self.assertEqual(stat.S_IMODE(self.c.state.stat().st_mode), 0o700)

    def test_repository_local_admin_config_is_ignored(self):
        (self.root / "pf-config.json").write_text('{"minimum_free_mb": 9999}\n')
        controller = pf.Controller(self.context)
        self.assertNotEqual(controller.config["minimum_free_mb"], 9999)

    def test_missing_configured_group_is_rejected_with_configuration_error(self):
        config = self.root.parent / "config/pf-config.json"
        config.write_text(json.dumps({
            "backup_read_group": "missing-group",
            "workspace_write_group": TEST_GROUP,
        }) + "\n")
        real_getgrnam = pf.grp.getgrnam
        def group_lookup(name):
            if name == "missing-group":
                raise KeyError(name)
            return real_getgrnam(name)
        with mock.patch.object(pf.grp, "getgrnam", side_effect=group_lookup):
            with self.assertRaisesRegex(pf.Failure, "backup_read_group"):
                pf.Controller(self.context).load_app_config()

    def test_update_missing_ci_does_not_stop_application(self):
        self.c.fail = "ci"
        self.assertEqual(self.invoke(["update", "--latest"]), 20)
        self.assertTrue(all(self.c.running.values()))
        self.assertFalse(self.c.pending.exists())

    def test_manual_ci_bypass_is_explicit(self):
        self.c.fail = "ci"
        self.assertEqual(self.invoke(["update", "--latest", "--skip-ci"]), 0)
        self.assertEqual(self.c.ci_calls, [])

    def test_migration_requires_explicit_approval(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest"]), 20)
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.revision(), OLD)
        self.assertFalse(self.c.pending.exists())

    def test_approved_migration_rehearses_before_live_migration(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0)
        self.assertEqual(self.c.db_heads(), ["r2"])
        runs = [call for call in self.c.calls if call[0] == "compose" and call[1][0] == "run" and "alembic" in call[1]]
        self.assertEqual(len(runs), 2)
        self.assertTrue(runs[0][2]["POSTGRES_DB"].startswith("pf_migrate_"))
        self.assertIsNone(runs[1][2])

    def test_migration_rehearsal_failure_keeps_original_database(self):
        self.c.new_migration = True
        self.c.fail = "migration"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        self.assertEqual(self.c.db_heads(), ["r1"])
        self.assertEqual(self.c.revision(), OLD)
        self.assertFalse(self.c.running["frontend"])
        self.assertEqual(pf.load_json(self.c.pending)["phase"], "backup-ready")

    def test_live_migration_failure_is_not_auto_downgraded(self):
        self.c.new_migration = True
        self.c.fail = "live-migration"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        self.assertEqual(pf.load_json(self.c.pending)["phase"], "migrating-live")
        self.assertFalse(self.c.running["backend"])
        self.assertFalse(any("downgrade" in str(call) for call in self.c.calls))

    def test_dump_failure_never_replaces_source(self):
        self.c.fail = "dump"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.snapshots()[0]["status"], "incomplete")
        self.assertFalse(self.c.running["frontend"])

    def test_failed_restore_check_blocks_database_reset(self):
        self.c.fail = "restore"
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertFalse(any(k.startswith("pf_keep_") for k in self.c.dbs))

    def test_reset_activates_clean_database_and_retains_original(self):
        self.assertEqual(self.invoke(["reset-db"]), 0)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], [])
        self.assertEqual(self.c.db_heads(), ["r1"])
        retained = [db for db in self.c.dbs if db.startswith("pf_keep_")]
        self.assertEqual(len(retained), 1)
        self.assertEqual(self.c.dbs[retained[0]]["rows"], ["old-record"])
        self.assertFalse(self.c.dbs[retained[0]]["connections"])
        self.assertEqual(self.c.snapshots()[0]["reason"], "before-reset")

    def test_reset_refuses_external_sessions_without_killing_them(self):
        self.c.connected_sessions = 1
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertFalse(any("terminate_backend" in str(call) for call in self.c.calls))

    def test_reset_failed_atomic_swap_preserves_current_data(self):
        self.c.fail = "swap"
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertTrue(self.c.pending.exists())

    def test_code_rollback_preserves_newer_rows(self):
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        selected = self.c.snapshots()[0]["id"]
        self.c.dbs["partflow_staging"]["rows"].append("newer-record")
        self.assertEqual(self.invoke(["rollback", selected]), 0)
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record", "newer-record"])
        self.assertFalse(any(k.startswith("pf_keep_") for k in self.c.dbs))

    def test_code_rollback_refuses_schema_mismatch(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0)
        selected = self.c.snapshots()[0]["id"]
        self.assertEqual(self.invoke(["rollback", selected]), 1)
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.db_heads(), ["r2"])

    def test_full_rollback_restores_old_schema_and_keeps_new_database(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0)
        selected = self.c.snapshots()[0]["id"]
        self.c.dbs["partflow_staging"]["rows"].append("newer-record")
        self.assertEqual(self.invoke(["rollback", selected, "--restore-db"]), 0)
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.db_heads(), ["r1"])
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        retained = [name for name in self.c.dbs if name.startswith("pf_keep_")][0]
        self.assertEqual(self.c.dbs[retained]["rows"], ["old-record", "newer-record"])

    def test_health_failure_leaves_pending_and_services_stopped(self):
        self.c.fail = "health"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertTrue(self.c.pending.exists())
        self.assertFalse(self.c.running["backend"])
        self.assertFalse(self.c.running["frontend"])

    def test_full_rollback_recovers_failed_update(self):
        self.c.new_migration = True
        self.c.fail = "health"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        selected = self.c.snapshots()[0]["id"]
        self.c.fail = None
        self.assertEqual(self.invoke(["rollback", selected, "--restore-db"]), 0)
        self.assertFalse(self.c.pending.exists())
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.db_heads(), ["r1"])

    def test_resume_only_before_changes(self):
        self.c.fail = "dump"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.c.fail = None
        self.assertEqual(self.invoke(["resume"]), 0)
        self.assertTrue(self.c.running["frontend"])
        self.assertFalse(self.c.pending.exists())

    def test_resume_refuses_after_live_migration_phase(self):
        self.c.new_migration = True
        self.c.fail = "live-migration"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        self.c.fail = None
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertTrue(self.c.pending.exists())

    def test_checkpoint_checksum_corruption_is_detected(self):
        checkpoint = self.c.snapshot("test")
        (self.c.backups_dir / checkpoint["id"] / "database.dump").write_bytes(b"corrupted")
        with self.assertRaises(pf.Failure):
            self.c.verify_snapshot(checkpoint["id"])

    def test_manifest_tampering_is_detected(self):
        checkpoint = self.c.snapshot("test")
        path = self.c.backups_dir / checkpoint["id"] / "manifest.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaises(pf.Failure):
            self.c.verify_snapshot(checkpoint["id"])

    def test_rollback_refuses_checkpoint_source_with_reserved_paths_before_any_effect(self):
        """A12-R02: a checkpoint archive carrying a path the manifest cannot verify is refused
        before confirmation, pause, safety snapshot or database swap."""
        checkpoint = self.c.snapshot("test")
        folder = self.c.backups_dir / checkpoint["id"]
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            source_fixture(tree, OLD)
            (tree / "nested" / "node_modules").mkdir(parents=True)
            (tree / "nested" / "node_modules" / "tracked.js").write_text("module.exports = 1;\n")
            with tarfile.open(folder / "source.tar.gz", "w:gz") as archive:
                for item in sorted(tree.iterdir()):
                    archive.add(item, arcname=item.name)
        manifest = pf.load_json(folder / "manifest.json")
        manifest["checksums"]["source.tar.gz"] = pf.digest(folder / "source.tar.gz")
        pf.write_json(folder / "manifest.json", manifest)
        (folder / "manifest.sha256").write_text(pf.digest(folder / "manifest.json") + "\n")
        with mock.patch.object(self.c, "snapshot", side_effect=AssertionError("snapshot must not run")), \
             mock.patch.object(self.c, "pause", side_effect=AssertionError("pause must not run")), \
             mock.patch.object(self.c, "swap_database", side_effect=AssertionError("swap must not run")):
            with self.assertRaisesRegex(pf.Failure, "cannot verify.*nested/node_modules/tracked.js"):
                self.c.rollback(checkpoint["id"], restore_database=True)
            self.assertEqual(self.invoke(["rollback", checkpoint["id"], "--restore-db"]), 1)
        self.assertFalse(self.c.pending.exists())
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.revision(), OLD)
        self.assertFalse(any(name.startswith(("pf_keep_", "pf_restore_")) for name in self.c.dbs))
        self.assertEqual((self.root / "app-version.txt").read_text(), OLD)

    def test_missing_retained_image_blocks_rollback(self):
        checkpoint = self.c.snapshot("test")
        del self.c.tags[checkpoint["images"]["backend"]["reference"]]
        self.assertEqual(self.invoke(["rollback", checkpoint["id"]]), 1)
        self.assertTrue(self.c.running["frontend"])

    def test_release_check_default_is_nonmutating(self):
        self.assertEqual(self.invoke(["release-check"]), 0)
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.snapshots(), [])
        self.assertTrue(self.c.running["frontend"])

    def test_no_stable_release_is_a_clean_noop(self):
        self.c.target = None
        self.assertEqual(self.invoke(["release-check"]), 0)
        self.assertEqual(self.c.revision(), OLD)

    def test_auto_update_is_opt_in(self):
        self.assertEqual(self.invoke(["release-check", "--apply"]), 1)
        self.assertTrue(self.c.running["frontend"])

    def test_auto_update_same_schema_succeeds_without_prompt(self):
        self.c.config["auto_update"] = True
        with mock.patch.object(pf, "Controller", return_value=self.c), mock.patch.object(pf, "confirm") as confirmation:
            self.assertEqual(pf.main(["release-check", "--apply"], installation_root=self.layout.root,
                                     running_release=self.layout.release_dir, trusted_launch=True), 0)
            confirmation.assert_not_called()
        self.assertEqual(self.c.revision(), NEW)

    def test_auto_update_never_migrates(self):
        self.c.config["auto_update"] = True
        self.c.new_migration = True
        self.assertEqual(self.invoke(["release-check", "--apply"]), 20)
        self.assertTrue(self.c.running["frontend"])
        self.assertFalse(self.c.pending.exists())

    def test_auto_update_refuses_divergent_history(self):
        self.c.config["auto_update"] = True
        self.c.fail = "divergence"
        self.assertEqual(self.invoke(["release-check", "--apply"]), 20)
        self.assertEqual(self.c.revision(), OLD)

    def mark_config_production(self):
        # v2.5 tests flipped the in-memory config; the approved environment is now
        # protected registration data, so the editable file is the only channel
        # an editor has, and it must be rejected before any effect.
        config = self.c.config_dir / "pf-config.json"
        saved = json.loads(config.read_text())
        saved["environment"] = "production"
        config.write_text(json.dumps(saved) + "\n")
        self.c.config = None

    def test_production_registration_is_not_enabled_in_a1(self):
        paths = pfx.data_home(Path(self.temp.name) / "prod", project="partflow-prod", group=TEST_GROUP,
                              environment="production")
        registry_before = (self.layout.root / "registry/instances.json").read_bytes()
        with self.assertRaisesRegex(pf_instance.ContextError, "not enabled in A1"):
            pfx.register(self.layout, "prod", paths, project="partflow-prod", environment="production")
        self.assertEqual((self.layout.root / "registry/instances.json").read_bytes(), registry_before)
        self.assertEqual(sorted(os.listdir(self.layout.root / "locks")), sorted(["registry.lock", self.context.instance_id + ".lock"]))

    def test_production_reset_is_blocked(self):
        self.mark_config_production()
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.snapshots(), [])
        self.assertIn("disagrees with the approved environment", self.errors_text())

    def test_production_update_is_blocked(self):
        self.mark_config_production()
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.ci_calls, [])
        self.assertIn("disagrees with the approved environment", self.errors_text())

    def test_nested_controller_lock_is_rejected(self):
        another = pf.Controller(self.context)
        with self.c.lock():
            with self.assertRaises(pf.Failure):
                with another.lock():
                    pass

    def test_lock_is_released_after_exception(self):
        try:
            with self.c.lock():
                raise RuntimeError("test")
        except RuntimeError:
            pass
        with self.c.lock():
            pass

    def test_pending_operation_blocks_new_update(self):
        pf.write_json(self.c.pending, {"phase": "migrating-live"})
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.c.ci_calls, [])


    def test_same_sha_clean_workspace_is_a_noop(self):
        self.c.target["sha"] = OLD
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        self.assertEqual(len(self.c.snapshots()), 0)
        self.assertEqual(self.c.ci_calls, [])

    def test_same_sha_dirty_workspace_is_archived_and_refreshed_manually(self):
        self.c.target["sha"] = OLD
        self.c.workspace_dirty = True
        (self.root / "frontend/app.txt").write_text("local-edit")
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        saved = self.c.snapshots()[0]
        self.assertTrue(saved["workspace_differs_from_deployed"])
        self.assertTrue(saved["workspace_archive"])
        self.assertTrue((self.c.backups_dir / saved["id"] / saved["workspace_archive"]).is_file())
        self.assertEqual(self.c.workspace_head, OLD)
        self.assertFalse(self.c.workspace_dirty)
        self.assertEqual(self.c.ci_calls, [OLD])

    def test_auto_update_refuses_dirty_workspace(self):
        self.c.config["auto_update"] = True
        self.c.workspace_dirty = True
        self.assertEqual(self.invoke(["release-check", "--apply"]), 20)
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.snapshots(), [])

    def test_code_rollback_can_recover_failed_update_without_migrations(self):
        self.c.fail = "health"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        selected = self.c.snapshots()[0]["id"]
        self.c.fail = None
        self.assertEqual(self.invoke(["rollback", selected]), 0)
        self.assertFalse(self.c.pending.exists())
        self.assertEqual(self.c.revision(), OLD)

    def test_frontend_failure_never_discards_possible_new_writes(self):
        self.c.fail = "frontend-health"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record", "write-during-opening"])
        self.assertFalse(self.c.running["frontend"])
        self.assertFalse(any(name.startswith("pf_keep_") for name in self.c.dbs))

    def test_interactive_backup_selection_has_ten_item_pages(self):
        items = [{"id": str(i), "status": "complete"} for i in range(25)]
        with mock.patch.object(self.c, "snapshots", return_value=items), mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["n", "12"]), \
             mock.patch.object(self.c, "verify_snapshot", side_effect=lambda value: value):
            self.assertEqual(self.c.choose_snapshot(), "11")
        self.assertIn("page 1/3", self.output.getvalue())
        self.assertIn("page 2/3", self.output.getvalue())

    def test_explicit_backup_id_never_opens_selection_menu(self):
        item = {"id": "specific", "source_revision": OLD}
        with mock.patch.object(self.c, "snapshots", return_value=[item]), \
             mock.patch.object(self.c, "verify_snapshot", return_value=item), \
             mock.patch("builtins.input") as prompt:
            self.assertEqual(self.c.choose_snapshot("specific"), item)
            prompt.assert_not_called()

    def test_ambiguous_full_sha_requires_backup_id(self):
        with mock.patch.object(self.c, "snapshots", return_value=[
                {"id": "one", "source_revision": OLD}, {"id": "two", "source_revision": OLD}]):
            with self.assertRaises(pf.Failure):
                self.c.choose_snapshot(OLD)

    def test_actual_ci_gate_uses_latest_attempt_not_previous_success(self):
        base = {"head_sha": NEW, "head_branch": "main", "event": "push",
                "head_repository": {"full_name": "CDSemi/part-flow"}, "run_number": 200,
                "status": "completed", "html_url": "https://github.com/example/run"}
        runs = [dict(base, run_attempt=1, conclusion="success"), dict(base, run_attempt=2, conclusion="failure")]
        with mock.patch.object(self.c, "github", return_value={"workflow_runs": runs}):
            with self.assertRaises(pf.Deferred):
                pf.Controller.require_ci(self.c, NEW)

    def test_actual_ci_gate_rejects_success_for_other_commit(self):
        run = {"head_sha": OLD, "head_branch": "main", "event": "push",
               "head_repository": {"full_name": "CDSemi/part-flow"}, "run_number": 200,
               "status": "completed", "conclusion": "success", "html_url": "https://github.com/example/run"}
        with mock.patch.object(self.c, "github", return_value={"workflow_runs": [run]}):
            with self.assertRaises(pf.Deferred):
                pf.Controller.require_ci(self.c, NEW)

    def test_actual_ci_gate_accepts_matching_success(self):
        run = {"head_sha": NEW, "head_branch": "main", "event": "push",
               "head_repository": {"full_name": "CDSemi/part-flow"}, "run_number": 200,
               "status": "completed", "conclusion": "success", "html_url": "https://github.com/example/run"}
        with mock.patch.object(self.c, "github", return_value={"workflow_runs": [run]}):
            pf.Controller.require_ci(self.c, NEW)

    def test_release_tag_is_resolved_instead_of_target_commitish_branch(self):
        release = {"id": 1, "tag_name": "v0.1.0-alpha.1", "target_commitish": "main", "prerelease": True}
        def api(path):
            if path.startswith("releases/tags/"):
                return release
            self.assertEqual(path, "commits/v0.1.0-alpha.1")
            return {"sha": OLD}
        with mock.patch.object(self.c, "github", side_effect=api):
            result = pf.Controller.resolve(self.c, release="v0.1.0-alpha.1")
        self.assertEqual(result["sha"], OLD)

    def test_moved_previously_observed_tag_is_refused(self):
        pf.write_json(self.c.state / "observed-tags.json", {"v0.1.0": OLD})
        release = {"id": 1, "tag_name": "v0.1.0", "prerelease": False}
        with mock.patch.object(self.c, "github", side_effect=[release, {"sha": NEW}]):
            with self.assertRaises(pf.Failure):
                pf.Controller.resolve(self.c, release="v0.1.0")

    def test_destructive_compose_flags_are_rejected(self):
        for args in (["down", "-v"], ["down", "--volumes"], ["rm", "-svf"]):
            with self.assertRaises(pf.Failure):
                self.c.passthrough(args)


    def test_compose_runs_receive_managed_job_label(self):
        c = pf.Controller(self.context)
        c.cli = ["docker", "compose"]
        with mock.patch.object(c, "command", return_value="") as run:
            c.compose("run", "--rm", "--no-deps", "backend", "uv", "run", "alembic", "heads")
        args = run.call_args[0][0]
        self.assertIn("--label", args)
        self.assertIn("partflow.admin.project=partflow-staging", args)
        self.assertIn("POSTGRES_DB", run.call_args[1]["env"])

    def test_command_removes_exported_db_and_preserves_explicit_override(self):
        # PF-A1.2: the child environment is never inherited; only an explicit allowlisted
        # application value reaches the registered tool, and argv[0] is a typed tool id.
        c = pf.Controller(self.context)
        script = pfx.tool_script(Path(self.temp.name) / "tools", "docker",
                                 "#!/bin/sh\nprintf '%s' \"${POSTGRES_DB:-absent}\"\n")
        c._runner = pf.pf_runner.ProcessRunner({"docker": str(script)}, home=self.context.home_dir,
                                               docker_config=self.context.docker_config_dir,
                                               docker_host=self.context.daemon.endpoint, redactor=c.redactor)
        with mock.patch.dict(os.environ, {"POSTGRES_DB": "unexpected_database"}):
            self.assertEqual(c.command(["docker"]), "absent")
            self.assertEqual(c.command(["docker"], env={"POSTGRES_DB": "rehearsal"}), "rehearsal")
            with self.assertRaises(pf.Failure):
                c.command([os.sys.executable, "-c", "print(1)"])  # not a registered tool id
            with self.assertRaises(pf.Failure):
                c.command(["docker"], env={"PATH": "/tmp"})  # reserved host variable


class PureTests(unittest.TestCase):
    def test_root_entry_point_uses_the_registered_interpreter_not_a_path_search(self):
        # v2.5 searched PATH and SynoCommunity package paths for an interpreter. PF-A1.1
        # runs only the canonical interpreter registered in the protected bootstrap.conf
        # (ARCHITECTURE.md section 4) and never selects one from PATH or PF_PYTHON.
        script = (REPO_PACKAGE / "pf.sh").read_text()
        self.assertNotIn("/var/packages/python311/target/bin/python3.11", script)
        self.assertNotIn("PF_PYTHON:-", script)
        self.assertIn("interpreter|control_release)", script)
        self.assertIn('"$INTERPRETER" -I -B "$VERIFIER"', script)

    def test_repository_launcher_refuses_operational_execution(self):
        result = subprocess.run(
            ["sh", str(REPO_PACKAGE / "pf.sh"), "--help"],
            cwd=REPO_PACKAGE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository source copy", result.stderr)
        self.assertIn("sudo pf", result.stderr)

    def test_control_installer_encodes_external_layout_and_read_only_control_policy(self):
        script = (REPO_PACKAGE / "deploy/synology/install-control.sh").read_text()
        self.assertIn('CONTROL="$PF_HOME/control"', script)
        self.assertIn('CONFIG="$PF_HOME/config"', script)
        self.assertIn('mv "$REPO_ROOT/.env" "$CONFIG/.env"', script)
        self.assertIn('chmod 0740 "$TEMP/pf.sh"', script)
        self.assertIn('chmod 0700 "$LAUNCHER"', script)
        self.assertIn('rm -f "$LEGACY_CONFIG"', script)
        self.assertIn('Both legacy deploy/synology/pf-config.json and config/pf-config.json exist and differ.', script)

    def test_pagination_25_items(self):
        items = list(range(25))
        self.assertEqual(pf.page_items(items, 1), (list(range(10)), 3, 0))
        self.assertEqual(pf.page_items(items, 2), (list(range(10, 20)), 3, 10))
        self.assertEqual(pf.page_items(items, 3), (list(range(20, 25)), 3, 20))

    def test_invalid_page_refused(self):
        for page in (0, -1, 4):
            with self.assertRaises(pf.Failure):
                pf.page_items(list(range(25)), page)

    def test_selection_excludes_drafts_and_stable_excludes_prerelease(self):
        entries = [{"id": 1, "draft": False, "prerelease": False, "published_at": "2026-09-01"},
                   {"id": 2, "draft": False, "prerelease": True, "published_at": "2026-09-02"},
                   {"id": 3, "draft": True, "prerelease": False, "published_at": "2026-09-03"}]
        self.assertEqual(pf.select_release(entries, "stable")["id"], 1)
        self.assertEqual(pf.select_release(entries, "prerelease")["id"], 2)

    def test_no_stable_release_returns_none(self):
        self.assertIsNone(pf.select_release([{"id": 2, "draft": False, "prerelease": True, "published_at": "2026-09-02"}], "stable"))

    def test_schema_equality_passes(self):
        self.assertFalse(pf.schema_gate({"alembic/versions/a.py": "x"}, {"alembic/versions/a.py": "x"}, ["r1"], ["r1"]))

    def test_changed_schema_requires_approval(self):
        with self.assertRaises(pf.Deferred):
            pf.schema_gate({"alembic/versions/a.py": "x"}, {"alembic/versions/a.py": "x", "alembic/versions/b.py": "y"}, ["r1"], ["r2"])

    def test_edited_historical_migration_is_never_accepted(self):
        with self.assertRaises(pf.Failure):
            pf.schema_gate({"alembic/versions/a.py": "x"}, {"alembic/versions/a.py": "z"}, ["r1"], ["r1"], allow=True)

    def test_deleted_historical_migration_is_never_accepted(self):
        with self.assertRaises(pf.Failure):
            pf.schema_gate({"alembic/versions/a.py": "x"}, {}, ["r1"], ["r1"], allow=True)

    def test_multiple_heads_rejected(self):
        with self.assertRaises(pf.Failure):
            pf.schema_gate({}, {}, ["r1"], ["r1", "r2"], allow=True)

    def test_reset_phrase_must_match_exactly(self):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", return_value="yes"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(pf.Failure):
                pf.confirm("RESET partflow_staging", "warning")

    def test_reset_phrase_accepts_exact_text(self):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("builtins.input", return_value="RESET partflow_staging"), contextlib.redirect_stdout(io.StringIO()):
            pf.confirm("RESET partflow_staging", "warning")

    def test_noninteractive_reset_is_refused(self):
        with mock.patch("sys.stdin.isatty", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(pf.Failure):
                pf.confirm("RESET partflow_staging", "warning")

    def test_sql_identifier_injection_rejected(self):
        for value in ('db"; DROP DATABASE postgres;--', "../db", "a" * 64, ""):
            with self.assertRaises(pf.Failure):
                pf.quote_identifier(value)

    def test_database_switch_is_one_transaction(self):
        sql = pf.database_swap_sql("app", "prepared", "retained")
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertTrue(sql.endswith("COMMIT;"))
        self.assertNotIn("DROP DATABASE", sql)
        self.assertEqual(sql.count("RENAME TO"), 2)

    def test_tar_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.tar.gz"
            with tarfile.open(path, "w:gz") as tar:
                item = tarfile.TarInfo("../../escape")
                item.size = 1
                tar.addfile(item, io.BytesIO(b"x"))
            with self.assertRaises(pf.Failure):
                pf.extract_source(path, Path(tmp) / "out")

    def test_tar_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.tar.gz"
            with tarfile.open(path, "w:gz") as tar:
                item = tarfile.TarInfo("secret")
                item.type = tarfile.SYMTYPE
                item.linkname = "/etc/passwd"
                tar.addfile(item)
            with self.assertRaises(pf.Failure):
                pf.extract_source(path, Path(tmp) / "out")

    def test_source_archive_excludes_git_and_runtime_env_is_external(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            fixture(root)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("private git config")
            (root / ".env").write_text("STALE_REPO_SECRET=must-not-be-backed-up\n")
            archive = Path(tmp) / "source.tar.gz"
            pf.create_source_archive(root, archive)
            dest = Path(tmp) / "out"
            dest.mkdir()
            pf.extract_source(archive, dest)
            self.assertFalse((dest / ".env").exists())
            self.assertFalse((dest / ".git").exists())
            self.assertTrue((root.parent / "config/.env").is_file())
            self.assertEqual(pf.migration_files(root), pf.migration_files(dest))

    def test_real_git_clone_is_pinned_to_requested_commit(self):
        """PF-A1.2: the candidate is exported from the protected store, never cloned/checked out."""
        with tempfile.TemporaryDirectory() as tmp:
            upstream = Path(tmp) / "upstream"
            source_fixture(upstream)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=upstream, stderr=subprocess.DEVNULL).decode().strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            git("add", ".")
            git("commit", "-qm", "first")
            first = git("rev-parse", "HEAD")
            (upstream / "app-version.txt").write_text("second")
            git("commit", "-am", "second", "-q")
            second = git("rev-parse", "HEAD")
            root = Path(tmp) / "installed" / "repo"
            context = fixture(root)
            c = pf.Controller(context)
            c.remote_override = str(upstream)
            c.source_protocols = ("file",)
            candidate = Path(tmp) / "candidate"
            with mock.patch.object(c, "compose", return_value=""), c.lock():
                c.materialize_source({"sha": first}, candidate)
                store = c.source_store()
                self.assertTrue(store.path.is_dir())
                self.assertEqual(store.path.parent, fixture.layout.sources)
                with self.assertRaises(pf.pf_source.SourceError):
                    store.is_ancestor(first, second)  # second is not in the store yet: unknown, not False
                c.materialize_source({"sha": second}, Path(tmp) / "candidate-2")
                self.assertTrue(store.is_ancestor(first, second))
                self.assertFalse(store.is_ancestor(second, first))
                with self.assertRaises(pf.Failure):
                    c.materialize_source({"sha": "3" * 40}, Path(tmp) / "missing")
            self.assertEqual((candidate / "app-version.txt").read_text(), OLD)
            self.assertFalse((candidate / "DEPLOYED_SOURCE.txt").exists())
            self.assertFalse((candidate / ".git").exists())
            self.assertEqual(sorted(p.name for p in candidate.iterdir()),
                             sorted(p.name for p in upstream.iterdir() if p.name != ".git"))
            store_config = pf.pf_source.parse_git_config((store.path / "config").read_text())
            self.assertEqual(store_config["core.hookspath"], str(store.hooks_path))
            self.assertEqual(store_config["remote.approved.url"], str(upstream))
            self.assertEqual(store_config["protocol.allow"], "never")


if __name__ == "__main__":
    unittest.main(verbosity=2)

class PurgeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = fixture(self.root)
        self.layout = fixture.layout
        self.c = FakeController(self.context)

    def tearDown(self):
        self.temp.cleanup()

    def test_parser_exposes_purge_and_restore_commands(self):
        self.assertEqual(pf.parser().parse_args(["purge", "--project", "partflow-staging"]).command, "purge")
        args = pf.parser().parse_args(["restore-instance", "abc", "--side-by-side"])
        self.assertEqual(args.command, "restore-instance")
        self.assertTrue(args.side_by_side)
        self.assertEqual(pf.parser().parse_args(["instances"]).command, "instances")
        self.assertEqual(pf.parser().parse_args(["recoveries"]).command, "recoveries")

    def test_instances_lists_registry_and_purge_requires_explicit_selection(self):
        # v2.5 discovered instances through Docker and offered an interactive
        # menu. PF-A1.1 lists the protected registry (no Docker) and refuses an
        # implicit target when several instances exist without a default.
        other_paths = pfx.data_home(Path(self.temp.name) / "other", project="partflow-b", group=TEST_GROUP)
        pfx.register(self.layout, "beta", other_paths, project="partflow-b")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pf.main(["instances"], installation_root=self.layout.root,
                                     running_release=self.layout.release_dir, trusted_launch=True), 0)
            listing = output.getvalue()
            self.assertIn("staging", listing)
            self.assertIn("beta", listing)
            self.assertIn("project=partflow-b", listing)
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors), mock.patch.object(pf, "Controller", return_value=self.c):
                self.assertEqual(pf.main(["purge"], installation_root=self.layout.root,
                                         running_release=self.layout.release_dir, trusted_launch=True), 1)
            self.assertIn("pass --instance", errors.getvalue())
            with mock.patch.object(pf, "Controller", return_value=self.c) as constructed, \
                 mock.patch.object(self.c, "purge") as purge:
                self.assertEqual(pf.main(["--instance", "beta", "purge", "--keep-backups"],
                                         installation_root=self.layout.root,
                                         running_release=self.layout.release_dir, trusted_launch=True), 0)
            self.assertEqual(constructed.call_args.args[0].slug, "beta")
            purge.assert_called_once()

    def test_purge_requires_recovery_then_multiple_confirmations_before_cleanup(self):
        summary = {
            "project": "partflow-staging", "root": str(self.root), "database": "partflow_staging",
            "database_user": "partflow_staging", "revision": OLD,
            "containers": ["c1"], "volumes": ["v1"], "networks": ["n1"], "images": ["i1"],
            "checkpoints": 0, "state_present": True, "env_present": True,
        }
        recovery = {
            "id": "purge-20260910T120000Z-" + OLD[:12] + "-abcdef",
            "active_checkpoint": "20260910T115900Z-" + OLD[:12] + "-aaaaaa",
            "database": "partflow_staging", "databases": [{"name": "partflow_staging"}],
            "saved_image_refs": ["backend:test", "frontend:test"],
        }
        checkpoint = {"id": recovery["active_checkpoint"], "images": {
            "backend": {"reference": "backend:old", "id": "sha256:old-backend"},
            "frontend": {"reference": "frontend:old", "id": "sha256:old-frontend"},
        }, "database_heads": ["r1"]}
        confirmations = []
        def record_confirm(phrase, warning):
            confirmations.append(phrase)

        with mock.patch.object(self.c, "instance_summary", return_value=summary), \
             mock.patch.object(self.c, "log_instance_summary"), \
             mock.patch.object(self.c, "database_ready", return_value=16), \
             mock.patch.object(self.c, "ensure_local_contract", return_value={"heads": ["r1"]}), \
             mock.patch.object(self.c, "create_purge_recovery", return_value=recovery), \
             mock.patch.object(self.c, "verify_snapshot", return_value=checkpoint), \
             mock.patch.object(self.c, "finish_purge_cleanup") as cleanup, \
             mock.patch.object(pf, "confirm", side_effect=record_confirm), \
             mock.patch.object(pf, "prompt_yes_no", return_value=False):
            self.c.purge()

        self.assertEqual(confirmations[0], "PURGE partflow-staging")
        self.assertEqual(confirmations[1], "DELETE partflow_staging")
        self.assertTrue(confirmations[2].startswith("ERASE partflow-staging "))
        cleanup.assert_called_once_with(recovery["id"], delete_backups=False, reset_admin_config=False)

    def test_purge_delete_backups_adds_separate_confirmation(self):
        summary = {
            "project": "partflow-staging", "root": str(self.root), "database": "partflow_staging",
            "database_user": "partflow_staging", "revision": OLD,
            "containers": ["c1"], "volumes": ["v1"], "networks": [], "images": [],
            "checkpoints": 1, "state_present": True, "env_present": True,
        }
        recovery = {
            "id": "purge-20260910T120000Z-" + OLD[:12] + "-abcdef",
            "active_checkpoint": "20260910T115900Z-" + OLD[:12] + "-aaaaaa",
            "database": "partflow_staging", "databases": [{"name": "partflow_staging"}],
            "saved_image_refs": ["backend:test", "frontend:test"],
        }
        checkpoint = {"id": recovery["active_checkpoint"], "images": {}, "database_heads": ["r1"]}
        confirmations = []
        with mock.patch.object(self.c, "instance_summary", return_value=summary), \
             mock.patch.object(self.c, "log_instance_summary"), \
             mock.patch.object(self.c, "database_ready", return_value=16), \
             mock.patch.object(self.c, "ensure_local_contract"), \
             mock.patch.object(self.c, "create_purge_recovery", return_value=recovery), \
             mock.patch.object(self.c, "verify_snapshot", return_value=checkpoint), \
             mock.patch.object(self.c, "finish_purge_cleanup"), \
             mock.patch.object(pf, "confirm", side_effect=lambda phrase, warning: confirmations.append(phrase)):
            self.c.purge(delete_backups=True)
        self.assertIn("DELETE BACKUPS partflow-staging", confirmations)

    def test_interrupted_deleting_purge_can_resume_from_verified_bundle(self):
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        pf.write_json(self.c.pending, {
            "operation": "purge", "phase": "deleting", "recovery": recovery_id,
            "delete_backups": True, "reset_admin_config": False,
        })
        item = {"id": recovery_id, "project": "partflow-staging", "status": "complete"}
        with mock.patch.object(self.c, "recoveries", return_value=[item]), \
             mock.patch.object(self.c, "verify_recovery", return_value=item), \
             mock.patch.object(self.c, "finish_purge_cleanup") as cleanup, \
             mock.patch.object(pf, "confirm") as confirmation:
            self.c.purge()
        confirmation.assert_called_once()
        cleanup.assert_called_once_with(recovery_id, delete_backups=True, reset_admin_config=False)

    def test_side_by_side_restore_never_replaces_active_database(self):
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        folder = self.c.recovery_root / "side-by-side-fixture"
        folder.mkdir(parents=True)
        dump = folder / "databases/active.dump"
        dump.parent.mkdir()
        dump.write_bytes(b"dump")
        recovery = {
            "id": recovery_id, "_folder": str(folder), "project": "partflow-staging",
            "postgres_major": 16, "database": "partflow_staging",
        }
        with mock.patch.object(self.c, "verify_recovery", return_value=recovery), \
             mock.patch.object(self.c, "database_ready", return_value=16), \
             mock.patch.object(self.c, "restore_into") as restore, \
             mock.patch.object(pf, "confirm"):
            self.c.restore_instance(recovery, side_by_side=True)
        target_name = restore.call_args.args[0]
        self.assertTrue(target_name.startswith("pf_recovery_"))
        self.assertNotEqual(target_name, "partflow_staging")
        self.assertEqual(restore.call_args.args[1], dump)

    def test_exact_restore_refuses_nonempty_project(self):
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        recovery = {
            "id": recovery_id, "_folder": self.temp.name, "project": "partflow-staging",
            "root": str(self.root), "postgres_major": 16, "database": "partflow_staging",
            "database_user": "partflow_staging", "source_revision": OLD, "databases": [],
        }
        with mock.patch.object(self.c, "verify_recovery", return_value=recovery), \
             mock.patch.object(self.c, "detailed_project_resources", return_value={
                 "containers": ["c"], "volumes": [], "networks": [], "images": []
             }):
            with self.assertRaisesRegex(pf.Failure, "empty target project"):
                self.c.restore_instance(recovery)

    def test_verify_recovery_detects_modified_payload(self):
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        folder = self.c.recovery_root / recovery_id
        folder.mkdir(parents=True)
        payload = folder / "source.tar.gz"
        payload.write_bytes(b"original")
        metadata = {
            "format": 1, "kind": "partflow-purge-recovery", "status": "complete",
            "id": recovery_id, "project": "partflow-staging",
            "checksums": {"source.tar.gz": pf.digest(payload)},
        }
        pf.write_json(folder / "manifest.json", metadata)
        (folder / "manifest.sha256").write_text(pf.digest(folder / "manifest.json") + "\n")
        item = dict(metadata, _folder=str(folder))
        self.assertEqual(self.c.verify_recovery(item)["id"], recovery_id)
        payload.write_bytes(b"tampered")
        with self.assertRaisesRegex(pf.Failure, "checksum mismatch"):
            self.c.verify_recovery(item)

class PurgeRecoveryBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = fixture(self.root)
        self.layout = fixture.layout
        self.c = FakeController(self.context)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_purge_recovery_builds_checksum_verified_bundle(self):
        backup_id = "20260910T115900Z-" + OLD[:12] + "-aaaaaa"
        checkpoint_dir = self.c.backups_dir / backup_id
        checkpoint_dir.mkdir(parents=True)
        with tarfile.open(checkpoint_dir / "source.tar.gz", "w:gz") as archive:
            source_file = Path(self.temp.name) / "source.txt"
            source_file.write_text("source\n")
            archive.add(source_file, arcname="source.txt")
        (checkpoint_dir / "database.dump").write_bytes(b"active-dump")
        (checkpoint_dir / "database.list").write_text("mock-list\n")
        workspace_archive = checkpoint_dir / "workspace.tar.gz"
        with tarfile.open(workspace_archive, "w:gz") as archive:
            workspace_file = Path(self.temp.name) / "workspace.txt"
            workspace_file.write_text("local edits\n")
            archive.add(workspace_file, arcname="workspace.txt")
        checkpoint = {
            "id": backup_id,
            "source_revision": OLD,
            "database_heads": ["r1"],
            "postgres_major": 16,
            "workspace_archive": "workspace.tar.gz",
            "workspace_head": OLD,
            "workspace_dirty": True,
            "images": {
                "backend": {"reference": "backend:recovery", "id": "sha256:old-backend"},
                "frontend": {"reference": "frontend:recovery", "id": "sha256:old-frontend"},
            },
        }

        def fake_compose(*args, **kwargs):
            output = kwargs.get("output")
            if output is not None and any("pg_dumpall" in str(value) for value in args):
                output.write(b"-- globals\n")
                return ""
            raise AssertionError(args)

        def fake_command(argv, **kwargs):
            if argv[:4] == ["docker", "image", "save", "-o"]:
                target = Path(argv[4])
                dummy = Path(self.temp.name) / "image-manifest.json"
                dummy.write_text("{}\n")
                with tarfile.open(target, "w") as archive:
                    archive.add(dummy, arcname="manifest.json")
                return ""
            raise AssertionError(argv)

        with mock.patch.object(self.c, "database_ready", return_value=16), \
             mock.patch.object(self.c, "snapshot", return_value=checkpoint), \
             mock.patch.object(self.c, "database_inventory", return_value=[
                 {"name": "partflow_staging", "allow_connections": True}
             ]), \
             mock.patch.object(self.c, "db_heads", return_value=["r1"]), \
             mock.patch.object(self.c, "available_snapshot_image_refs", return_value=([], [])), \
             mock.patch.object(self.c, "detailed_project_resources", return_value={
                 "containers": ["c"], "volumes": ["v"], "networks": ["n"], "images": []
             }), \
             mock.patch.object(self.c, "compose", side_effect=fake_compose), \
             mock.patch.object(self.c, "command", side_effect=fake_command), \
             self.c.lock():
            recovery = self.c.create_purge_recovery()
            frozen_values = dict(self.c.frozen.values)

        verified = self.c.verify_recovery({**recovery, "_folder": str(self.c.recovery_root / recovery["id"])})
        self.assertEqual(verified["database"], "partflow_staging")
        self.assertEqual(verified["source_revision"], OLD)
        folder = Path(verified["_folder"])
        self.assertTrue((folder / "images.tar").is_file())
        self.assertTrue((folder / "revision-checkpoints.tar.gz").is_file())
        self.assertTrue((folder / "workspace.tar.gz").is_file())
        self.assertTrue((folder / "configuration/.env").is_file())
        # The bundle carries the frozen configuration (literal values), strictly parseable.
        self.assertEqual(pf.read_app_env(folder / "configuration/.env"), frozen_values)
        self.assertTrue((folder / "configuration/pf-config.json").is_file())
        self.assertEqual(verified["workspace_archive"], "workspace.tar.gz")

class RecoverySourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = fixture(self.root)
        self.layout = fixture.layout
        self.c = FakeController(self.context)

    def tearDown(self):
        self.temp.cleanup()

    def test_recovery_replaces_workspace_but_preserves_external_control_and_admin_config(self):
        current_admin = self.c.control_dir / "pf-admin.py"
        current_admin.write_text("# current v2.5 installed control plane\n")
        current_config = self.c.config_dir / "pf-config.json"
        current_config.write_text(json.dumps({
            "project": "partflow-staging",
            "backup_read_group": TEST_GROUP,
            "workspace_write_group": TEST_GROUP,
        }) + "\n")

        candidate = Path(self.temp.name) / "candidate"
        source_fixture(candidate, NEW)
        (candidate / "deploy/synology/pf-admin.py").write_text("# old recovered repository source\n")

        self.c.replace_source_for_recovery(candidate, NEW)

        self.assertEqual(current_admin.read_text(), "# current v2.5 installed control plane\n")
        self.assertNotIn('"minimum_free_mb": 777', current_config.read_text())
        self.assertIn('"project": "partflow-staging"', current_config.read_text())
        self.assertEqual((self.root / "deploy/synology/pf-admin.py").read_text(), "# old recovered repository source\n")
        self.assertFalse((self.root / ".env").exists())
        self.assertFalse((self.root / "DEPLOYED_SOURCE.txt").exists())

    def test_restore_runtime_environment_writes_external_config_env(self):
        recovery_dir = Path(self.temp.name) / "recovery"
        saved = recovery_dir / "configuration"
        saved.mkdir(parents=True)
        (saved / ".env").write_text(
            "POSTGRES_DB=partflow_staging\nPOSTGRES_USER=partflow_staging\n"
            "POSTGRES_PASSWORD=old-secret\nSITE_TIMEZONE=America/Los_Angeles\n"
            "PARTFLOW_BIND_IP=127.0.0.1\nPARTFLOW_HTTP_PORT=5173\nPARTFLOW_ALLOWED_HOST=localhost\n"
        )
        self.c.restore_runtime_environment({"_folder": str(recovery_dir), "format": 2})
        restored = self.c.config_dir / ".env"
        self.assertIn("POSTGRES_PASSWORD=old-secret", restored.read_text())
        self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o660)
