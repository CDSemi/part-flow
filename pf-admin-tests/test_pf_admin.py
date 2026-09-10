"""Offline tests. Docker/PostgreSQL are simulated; archive and filesystem work is real."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pf_admin", PACKAGE / "pf-admin.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)
OLD = "1" * 40
NEW = "2" * 40


def fixture(root, revision=OLD, new_migration=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "backend/alembic/versions").mkdir(parents=True)
    (root / "backend/alembic.ini").write_text("[alembic]\nscript_location=alembic\n")
    (root / "backend/alembic/env.py").write_text("# fixture environment\n")
    (root / "backend/alembic/versions/001.py").write_text("revision='r1'\n")
    if new_migration:
        (root / "backend/alembic/versions/002.py").write_text("revision='r2'\ndown_revision='r1'\n")
    (root / "frontend").mkdir()
    (root / "frontend/app.txt").write_text(revision)
    (root / "DEPLOYED_SOURCE.txt").write_text(revision + "\n")
    (root / ".env").write_text("POSTGRES_DB=partflow_staging\nPOSTGRES_USER=partflow_staging\nPOSTGRES_PASSWORD=abc123\n")
    (root / "compose.nas.yaml").write_text((PACKAGE / "compose.nas.yaml").read_text())
    (root / "pf.sh").write_text("# local stable controller\n")
    (root / "pf-admin.py").write_text("# local stable helper\n")
    (root / "app-version.txt").write_text(revision)


class FakeController(pf.Controller):
    """Simulate only external commands; execute the real workflow/backup code."""
    def __init__(self, root):
        super().__init__(root)
        self.config["minimum_free_mb"] = 1
        self.dbs = {"partflow_staging": {"heads": ["r1"], "rows": ["old-record"], "connections": True}}
        self.tags = {"backend:old": "sha256:old-backend", "frontend:old": "sha256:old-frontend"}
        self.contracts = {"sha256:old-backend": {"files": pf.migration_files(root), "heads": ["r1"]}}
        self.current_images = {"backend": "sha256:old-backend", "frontend": "sha256:old-frontend"}
        self.running = {"db": True, "backend": True, "frontend": True}
        self.calls = []
        self.fail = None
        self.new_migration = False
        self.target = {"sha": NEW, "ref": "v0.1.0-alpha.2", "release_id": 2}
        self.connected_sessions = 0
        self.ci_calls = []

    def inspect(self, service):
        return {"Image": self.current_images.get(service, "sha256:postgres"),
                "State": {"Running": self.running[service], "Health": {"Status": "healthy"}},
                "Config": {"Env": ["POSTGRES_DB=partflow_staging", "POSTGRES_USER=partflow_staging"]}}

    def docker(self, *args, **kwargs):
        self.calls.append(("docker", args))
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

    def sql(self, database, sql):
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
            self.current_images[service] = self.override_images(override)[service]
            self.running[service] = True
            return ""
        if args[0] == "config":
            return ""
        if args[0] == "exec":
            if "frontend" in args:
                return json.dumps({"status": "ok", "database": "connected"})
            if "pg_restore" in args:
                content = input_file.read()
                if output:
                    output.write(b"mock archive list\n")
                return ""
            script = args[args.index("-c") + 1]
            if "pg_dump" in script:
                if self.fail == "dump":
                    raise pf.Failure("simulated dump failure")
                output.write(json.dumps(self.dbs["partflow_staging"]).encode())
                return ""
            if "pg_restore" in script:
                if self.fail == "restore":
                    raise pf.Failure("simulated restore failure")
                self.dbs[args[-1]] = json.loads(input_file.read())
                return ""
            if "createdb" in script:
                name = args[-1]
                assert name not in self.dbs
                self.dbs[name] = {"heads": [], "rows": [], "connections": True}
                return ""
            if "dropdb" in script:
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

    def clone(self, target, destination):
        fixture(destination, target["sha"], self.new_migration)
        for name in pf.LOCAL_FILES - {"DEPLOYED_SOURCE.txt"}:
            if (self.root / name).is_file():
                shutil.copy2(self.root / name, destination / name)

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

    def automatic_guard(self, current, candidate):
        if self.fail == "divergence":
            raise pf.Deferred("not a descendant")
        if self.new_migration:
            raise pf.Deferred("migration change")

    def resolve(self, **kwargs):
        return self.target


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        fixture(self.root)
        self.c = FakeController(self.root)
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.errors = contextlib.redirect_stderr(io.StringIO())
        self.errors.__enter__()

    def tearDown(self):
        self.errors.__exit__(None, None, None)
        self.capture.__exit__(None, None, None)
        self.temp.cleanup()

    def invoke(self, arguments):
        with mock.patch.object(pf, "Controller", return_value=self.c), mock.patch.object(pf, "confirm"):
            return pf.main(arguments, root=self.root)

    def test_update_backs_up_then_switches_source_and_images(self):
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        self.assertEqual(self.c.revision(), NEW)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertTrue(all(self.c.running.values()))
        self.assertFalse(self.c.pending.exists())
        saved = self.c.snapshots()[0]
        self.assertEqual(saved["source_revision"], OLD)
        self.assertEqual(saved["restore_test"], "passed")
        self.assertEqual(self.c.ci_calls, [NEW])
        self.assertEqual((self.root / "pf.sh").read_text(), "# local stable controller\n")
        self.assertEqual(self.c.env()["POSTGRES_PASSWORD"], "abc123")

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
            self.assertEqual(pf.main(["release-check", "--apply"]), 0)
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

    def test_production_reset_is_blocked(self):
        self.c.config["environment"] = "production"
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.snapshots(), [])

    def test_production_update_is_blocked(self):
        self.c.config["environment"] = "production"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertTrue(self.c.running["frontend"])

    def test_nested_controller_lock_is_rejected(self):
        another = pf.Controller(self.root)
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


    def test_same_sha_archive_installation_is_manually_bootstrapped(self):
        self.c.target["sha"] = OLD
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        self.assertEqual(len(self.c.snapshots()), 1)
        self.assertEqual(self.c.ci_calls, [OLD])

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
        c = pf.Controller(self.root)
        c.cli = ["docker", "compose"]
        with mock.patch.object(c, "command", return_value="") as run:
            c.compose("run", "--rm", "--no-deps", "backend", "uv", "run", "alembic", "heads")
        args = run.call_args[0][0]
        self.assertIn("--label", args)
        self.assertIn("partflow.admin.project=partflow-staging", args)
        self.assertIn("POSTGRES_DB", run.call_args[1]["clean_env_keys"])

    def test_command_removes_exported_db_and_preserves_explicit_override(self):
        c = pf.Controller(self.root)
        command = [os.sys.executable, "-c", "import os; print(os.environ.get('POSTGRES_DB','absent'))"]
        with mock.patch.dict(os.environ, {"POSTGRES_DB": "unexpected_database"}):
            self.assertEqual(c.command(command, clean_env_keys=["POSTGRES_DB"]), "absent")
            self.assertEqual(c.command(command, clean_env_keys=["POSTGRES_DB"], env={"POSTGRES_DB": "rehearsal"}), "rehearsal")


class PureTests(unittest.TestCase):
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

    def test_source_archive_roundtrip_preserves_env_and_excludes_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            fixture(root)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("private git config")
            archive = Path(tmp) / "source.tar.gz"
            pf.create_source_archive(root, archive)
            dest = Path(tmp) / "out"
            dest.mkdir()
            pf.extract_source(archive, dest)
            self.assertTrue((dest / ".env").is_file())
            self.assertFalse((dest / ".git").exists())
            self.assertEqual(pf.migration_files(root), pf.migration_files(dest))

    def test_real_git_clone_is_pinned_to_requested_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            upstream = Path(tmp) / "upstream"
            fixture(upstream)
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
            root = Path(tmp) / "installed"
            fixture(root)
            c = pf.Controller(root)
            original_command = c.command
            def local_command(args, **kwargs):
                if args[:3] == ["git", "clone", "--no-checkout"]:
                    args = [*args[:-2], str(upstream), args[-1]]
                return original_command(args, **kwargs)
            candidate = Path(tmp) / "candidate"
            with mock.patch.object(c, "command", side_effect=local_command), mock.patch.object(c, "compose", return_value=""):
                c.clone({"sha": first}, candidate)
            self.assertEqual((candidate / "app-version.txt").read_text(), OLD)
            self.assertEqual((candidate / "DEPLOYED_SOURCE.txt").read_text().strip(), first)
            self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=candidate).decode().strip(), first)


if __name__ == "__main__":
    unittest.main(verbosity=2)
