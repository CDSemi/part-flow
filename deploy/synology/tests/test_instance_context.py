"""PF-A1.1 acceptance and regression tests: protected context, registry, bootstrap trust, locks.

Case mapping (ACCEPTANCE_CASES.json, revision 2026-09-13-design-r3):
  A1-T01 hostile bootstrap instance values      -> HostileBootstrapValues
  A1-T02 selection and stable default           -> SelectionAndDefault
  A1-T03 read-only construction and diagnostics -> ReadOnlyDiagnostics
  A1-T04 stable lock and tombstone              -> StableLocks
  A1-T16 offline pending journal visibility     -> PendingJournalVisibility (installed CLI + in-process)
  A1-T17 paths, owners, links and ancestors     -> ProtectedPathChecks
Regressions converted from the original audit probes P03/P04/P07/P12/P19 are
marked in the test names. The fake transport only records calls; it proves that
no command was issued on read-only or refused routes. It does not implement the
PF-A1.2 runner boundary.
"""
import contextlib
import errno
import grp
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_instance = pfx.pf_instance
GROUP = grp.getgrgid(os.getgid()).gr_name
ROOT_REQUIRED = unittest.skipUnless(os.geteuid() == 0, "protected fixtures require root ownership (uid 0)")


class RecordingController(pf.Controller):
    """Real controller; the only fake is a no-effect transport that records every command."""

    def __init__(self, context, **kwargs):
        super().__init__(context, **kwargs)
        self.calls = []

    def command(self, argv, **kwargs):
        self.calls.append([str(item) for item in argv])
        raise pf.Failure("transport disabled by test: " + str(argv[0]))


def run_main(arguments, layout, **kwargs):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = pf.main(arguments, installation_root=layout.root, running_release=layout.release_dir,
                       trusted_launch=True, **kwargs)
    return code, stdout.getvalue(), stderr.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.layout = pfx.install_root(self.base)

    def tearDown(self):
        # Fixtures may have tightened or loosened modes; make cleanup deterministic.
        for current, dirs, files in os.walk(self.base):
            for name in dirs:
                path = Path(current) / name
                if not path.is_symlink():
                    os.chmod(path, 0o700)
        self.temp.cleanup()

    def instance(self, name, *, project=None, **kwargs):
        paths = pfx.data_home(self.base / name, project=project or "partflow-" + name, group=GROUP, **kwargs)
        return pfx.register(self.layout, name, paths, project=project or "partflow-" + name), paths

    def registry_bytes(self):
        return (self.layout.root / "registry/instances.json").read_bytes()


@ROOT_REQUIRED
class HostileBootstrapValues(Base):
    """A1-T01 and probe P07 (cross-instance env leak)."""

    def test_explicit_target_ignores_pf_environment_of_another_instance(self):
        alpha, alpha_paths = self.instance("alpha")
        beta, beta_paths = self.instance("beta")
        pfx.deployed_record(alpha)
        pfx.deployed_record(beta)
        hostile = {
            "PF_HOME": str(self.base / "alpha"), "PF_REPO_ROOT": str(alpha_paths["workspace"]),
            "PF_CONFIG_DIR": str(alpha_paths["configuration"]), "PF_CONTROL_DIR": str(self.layout.release_dir),
            "PF_PYTHON": "/bin/false", "PYTHONPATH": str(self.base / "alpha"),
        }
        before_alpha = pfx.snapshot_tree(self.base / "alpha")
        before_registry = self.registry_bytes()
        with mock.patch.dict(os.environ, hostile):
            context = pf_instance.resolve_instance(pf_instance.load_registry(self.layout.root), instance="beta")
            self.assertEqual(context.paths.workspace, beta_paths["workspace"])
            self.assertEqual(context.paths.configuration, beta_paths["configuration"])
            self.assertEqual(context.compose_project, "partflow-beta")
            self.assertEqual(context.control.path, self.layout.release_dir)
            with mock.patch.object(pf, "Controller", RecordingController):
                code, out, err = run_main(["--instance", "beta", "status"], self.layout)
        self.assertIn("Instance: beta", out)
        self.assertIn(str(beta_paths["workspace"]), out)
        self.assertNotIn(str(alpha_paths["workspace"]), out)
        self.assertEqual(pfx.snapshot_tree(self.base / "alpha"), before_alpha)
        self.assertEqual(self.registry_bytes(), before_registry)

    def test_refused_mutation_preflight_on_b_issues_no_commands_and_touches_nothing(self):
        alpha, alpha_paths = self.instance("alpha")
        beta, beta_paths = self.instance("beta")
        pfx.deployed_record(alpha)
        pfx.deployed_record(beta)
        pf.write_json(beta.journal_path, {"operation": "update", "phase": "migrating-live", "started": "x"})
        before = pfx.snapshot_tree(self.base)
        holder = {}

        class Capture(RecordingController):
            def __init__(self, context, **kwargs):
                super().__init__(context, **kwargs)
                holder["controller"] = self

        hostile = {"PF_HOME": str(self.base / "alpha"), "PF_CONFIG_DIR": str(alpha_paths["configuration"])}
        with mock.patch.dict(os.environ, hostile), mock.patch.object(pf, "Controller", Capture):
            code, out, err = run_main(["--instance", "beta", "update", "--latest"], self.layout)
        self.assertEqual(code, 1)
        self.assertIn("previous operation is incomplete", err)
        self.assertEqual(holder["controller"].calls, [])
        self.assertEqual(holder["controller"].context.slug, "beta")
        self.assertEqual(pfx.snapshot_tree(self.base), before)


@ROOT_REQUIRED
class SelectionAndDefault(Base):
    """A1-T02."""

    def test_multiple_instances_without_default_require_explicit_selection(self):
        self.instance("alpha")
        self.instance("beta")
        registry = pf_instance.load_registry(self.layout.root)
        self.assertIsNone(registry.default_instance_id)
        with self.assertRaisesRegex(pf_instance.ContextError, "pass --instance"):
            pf_instance.resolve_instance(registry)
        self.assertEqual(pf_instance.resolve_instance(registry, instance="alpha").slug, "alpha")
        self.assertEqual(pf_instance.resolve_instance(registry, instance=registry.entries[1].instance_id).slug, "beta")
        # The legacy --project alias works only when it maps to exactly one registration.
        self.assertEqual(pf_instance.resolve_instance(registry, project="partflow-beta").slug, "beta")
        with self.assertRaisesRegex(pf_instance.ContextError, "exactly one"):
            pf_instance.resolve_instance(registry, project="partflow-none")

    def test_single_instance_is_selected_automatically(self):
        alpha, _ = self.instance("alpha")
        registry = pf_instance.load_registry(self.layout.root)
        self.assertEqual(pf_instance.resolve_instance(registry).instance_id, alpha.instance_id)

    def test_default_is_explicit_and_never_changed_by_a_later_registration(self):
        alpha, _ = self.instance("alpha")
        beta, _ = self.instance("beta")
        pf_instance.set_default_instance(self.layout.root, alpha.instance_id)
        registry = pf_instance.load_registry(self.layout.root)
        self.assertEqual(pf_instance.resolve_instance(registry).slug, "alpha")
        gamma, _ = self.instance("gamma")
        registry = pf_instance.load_registry(self.layout.root)
        self.assertEqual(registry.default_instance_id, alpha.instance_id)
        self.assertEqual(pf_instance.resolve_instance(registry).slug, "alpha")
        self.assertEqual([entry.slug for entry in registry.entries], ["alpha", "beta", "gamma"])
        with self.assertRaisesRegex(pf_instance.ContextError, "not a registered instance"):
            pf_instance.set_default_instance(self.layout.root, "00000000-0000-4000-8000-000000000000")

    def test_default_is_not_inferred_from_filesystem_order_or_time(self):
        alpha, _ = self.instance("alpha")
        beta, _ = self.instance("beta")
        os.utime(alpha.record_path, (1, 1))
        os.utime(beta.record_path, (2, 2))
        with self.assertRaisesRegex(pf_instance.ContextError, "pass --instance"):
            pf_instance.resolve_instance(pf_instance.load_registry(self.layout.root))

    def test_duplicate_registrations_fail_before_publication(self):
        alpha, alpha_paths = self.instance("alpha")
        before_registry = self.registry_bytes()
        before_locks = sorted(os.listdir(self.layout.root / "locks"))
        before_instances = sorted(os.listdir(self.layout.root / "instances"))
        fresh = pfx.data_home(self.base / "fresh", project="partflow-fresh", group=GROUP)
        attempts = {
            "slug": pfx.registration_spec(self.layout, "alpha", fresh, project="partflow-fresh"),
            "instance_id": pfx.registration_spec(self.layout, "fresh", fresh, project="partflow-fresh",
                                                 instance_id=alpha.instance_id),
            "daemon/project": pfx.registration_spec(self.layout, "fresh", fresh, project="partflow-alpha"),
            "workspace": pfx.registration_spec(self.layout, "fresh", dict(fresh, workspace=alpha_paths["workspace"]),
                                               project="partflow-fresh"),
        }
        for label, spec in attempts.items():
            with self.subTest(duplicate=label):
                with self.assertRaises(pf_instance.ContextError):
                    pf_instance.register_instance(self.layout.root, spec)
                self.assertEqual(self.registry_bytes(), before_registry)
                self.assertEqual(sorted(os.listdir(self.layout.root / "locks")), before_locks)
                self.assertEqual(sorted(os.listdir(self.layout.root / "instances")), before_instances)

    def test_tampered_registry_is_a_diagnostic_error_not_a_reason_to_regenerate(self):
        alpha, _ = self.instance("alpha")
        path = self.layout.root / "registry/instances.json"
        good = json.loads(path.read_bytes())
        cases = {
            "duplicate uuid": dict(good, instances=good["instances"] + [dict(good["instances"][0], slug="other")]),
            "duplicate slug": dict(good, instances=good["instances"] + [dict(
                good["instances"][0], instance_id="00000000-0000-4000-8000-000000000000",
                record_path=str(self.layout.root / "instances/00000000-0000-4000-8000-000000000000/record.json"))]),
            "record outside root": dict(good, instances=[dict(good["instances"][0], record_path="/etc/passwd")]),
            "unknown default": dict(good, default_instance_id="00000000-0000-4000-8000-000000000000"),
            "unknown key": dict(good, extra=1),
            "wrong schema": dict(good, schema_version=2),
        }
        for label, document in cases.items():
            with self.subTest(case=label):
                path.write_bytes(json.dumps(document).encode())
                with self.assertRaises(pf_instance.ContextError):
                    pf_instance.load_registry(self.layout.root)
        path.write_bytes(b'{"schema_version": 1, "schema_version": 1, "default_instance_id": null, "instances": []}')
        with self.assertRaisesRegex(pf_instance.ContextError, "Duplicate JSON"):
            pf_instance.load_registry(self.layout.root)
        self.assertTrue(alpha.record_path.is_file())

    def test_selected_instance_never_falls_back_when_its_record_is_missing_or_invalid(self):
        alpha, _ = self.instance("alpha")
        beta, _ = self.instance("beta")
        pf_instance.set_default_instance(self.layout.root, alpha.instance_id)
        record = json.loads(beta.record_path.read_bytes())
        record["unexpected"] = True
        beta.record_path.write_bytes(json.dumps(record).encode())
        registry = pf_instance.load_registry(self.layout.root)
        with self.assertRaisesRegex(pf_instance.ContextError, "unknown keys"):
            pf_instance.resolve_instance(registry, instance="beta")
        beta.record_path.unlink()
        with self.assertRaisesRegex(pf_instance.ContextError, "cannot be read"):
            pf_instance.resolve_instance(registry, instance="beta")
        self.assertEqual(pf_instance.resolve_instance(registry).slug, "alpha")

    def test_record_schema_matches_contract_and_rejects_invalid_shapes(self):
        contract = pfx.PACKAGE / "contracts" / "instance-record.schema.json"
        self.assertEqual(json.loads(contract.read_text(encoding="utf-8")), pf_instance.INSTANCE_RECORD_SCHEMA)
        alpha, _ = self.instance("alpha")
        record = json.loads(alpha.record_path.read_bytes())
        self.assertEqual(pf_instance.validate_against_schema(record, pf_instance.INSTANCE_RECORD_SCHEMA), [])
        bad = [
            dict(record, record_revision=True),                      # booleans are not integers
            dict(record, schema_version=2),
            dict(record, instance_id="not-a-uuid"),
            dict(record, state="deleted"),
            dict(record, daemon=dict(record["daemon"], rootless=True)),
            dict(record, paths=dict(record["paths"], workspace="relative/path")),
            dict(record, control=dict(record["control"], sha256="ABC")),
            dict(record, extra="x"),
        ]
        for index, document in enumerate(bad):
            with self.subTest(index=index):
                self.assertTrue(pf_instance.validate_against_schema(document, pf_instance.INSTANCE_RECORD_SCHEMA))
        with self.assertRaisesRegex(pf_instance.ContextError, "Non-finite"):
            pf_instance.parse_strict_json(b'{"a": NaN}', label="x")

    def test_interrupted_registration_is_reconciled_without_a_second_identity(self):
        fresh = pfx.data_home(self.base / "fresh", project="partflow-fresh", group=GROUP)
        spec = pfx.registration_spec(self.layout, "fresh", fresh, project="partflow-fresh")
        before_registry = self.registry_bytes()
        with mock.patch.object(pf_instance, "_write_registry", side_effect=OSError("simulated crash before publish")):
            with self.assertRaises(OSError):
                pf_instance.register_instance(self.layout.root, spec)
        self.assertEqual(self.registry_bytes(), before_registry)
        registry = pf_instance.load_registry(self.layout.root)
        orphans = pf_instance.unpublished_registrations(registry)
        self.assertEqual(len(orphans), 1)
        code, out, err = run_main(["instances"], self.layout)
        self.assertEqual(code, 0)
        self.assertIn("UNPUBLISHED registration directory", out)
        # A conflicting request for the same slug is refused; the same request completes the publication.
        conflicting = pfx.registration_spec(self.layout, "fresh", fresh, project="partflow-other")
        with self.assertRaisesRegex(pf_instance.ContextError, "conflicts"):
            pf_instance.register_instance(self.layout.root, conflicting)
        context = pf_instance.register_instance(self.layout.root, spec)
        self.assertEqual(context.paths.private_state, orphans[0])
        self.assertEqual(sorted(os.listdir(self.layout.root / "locks")), sorted(["registry.lock", context.instance_id + ".lock"]))
        self.assertEqual(pf_instance.unpublished_registrations(pf_instance.load_registry(self.layout.root)), [])


@ROOT_REQUIRED
class ReadOnlyDiagnostics(Base):
    """A1-T03 and probe P19 (constructor side effects before validation)."""

    def setUp(self):
        super().setUp()
        self.context, self.paths = self.instance("staging", project="partflow-staging")
        pfx.deployed_record(self.context)
        os.chmod(self.paths["configuration"], 0o700)

    def run_all_read_only(self, arguments_list):
        collected = []
        for arguments in arguments_list:
            holder = {}

            class Capture(RecordingController):
                def __init__(self, context, **kwargs):
                    super().__init__(context, **kwargs)
                    holder["controller"] = self

            with mock.patch.object(pf, "Controller", Capture):
                try:
                    code, out, err = run_main(arguments, self.layout)
                except SystemExit as exc:  # argparse --help
                    code, out, err = exc.code, "", ""
            collected.append((arguments, code, out, err, holder.get("controller")))
        return collected

    def test_help_list_status_doctor_and_unknown_command_change_nothing(self):
        before = pfx.snapshot_tree(self.base)
        results = self.run_all_read_only([
            ["--help"], ["instances"], ["status"], ["doctor"], ["backups"], ["recoveries"], ["frobnicate", "--now"],
        ])
        self.assertEqual(pfx.snapshot_tree(self.base), before)
        by_command = {tuple(args): (code, out, err, controller) for args, code, out, err, controller in results}
        self.assertEqual(by_command[("--help",)][0], 0)
        self.assertEqual(by_command[("instances",)][0], 0)
        self.assertIn("staging", by_command[("instances",)][1])
        # status/doctor run with a disabled transport: identity and journal are
        # printed, live data is reported unavailable, and nothing was created.
        self.assertIn("Instance: staging", by_command[("status",)][1])
        self.assertIn("No incomplete managed operation", by_command[("status",)][1])
        self.assertIn("unavailable", by_command[("status",)][1])
        self.assertIn("mutation allowed", by_command[("doctor",)][1])
        self.assertEqual(by_command[("frobnicate", "--now")][0], 1)
        for args, code, out, err, controller in results:
            if controller is not None:
                for argv in controller.calls:
                    self.assertNotIn("mkdir", argv)
        self.assertFalse((self.context.state_dir / "operation.lock").exists())

    def test_missing_config_is_a_diagnostic_and_is_never_created(self):
        (self.paths["configuration"] / "pf-config.json").unlink()
        before = pfx.snapshot_tree(self.base)
        results = self.run_all_read_only([["status"], ["doctor"], ["update", "--latest"], ["permissions"]])
        self.assertEqual(pfx.snapshot_tree(self.base), before)
        self.assertFalse((self.paths["configuration"] / "pf-config.json").exists())
        status = results[0]
        self.assertIn("Instance: staging", status[2])
        self.assertIn("Runtime configuration: unavailable", status[2])
        for args, code, out, err, controller in results[2:]:
            self.assertEqual(code, 1, args)
            self.assertIn("does not create it", err)
            self.assertEqual(controller.calls, [], args)

    def test_rejected_config_never_changes_modes_or_files(self):
        config = self.paths["configuration"] / "pf-config.json"
        saved = json.loads(config.read_text())
        variants = {
            "unknown key": json.dumps(dict(saved, unexpected=True)),
            "duplicate key": '{"project": "partflow-staging", "project": "partflow-staging"}',
            "wrong project": json.dumps(dict(saved, project="partflow-other")),
            "not json": "{",
        }
        for label, text in variants.items():
            with self.subTest(config=label):
                config.write_text(text)
                before = pfx.snapshot_tree(self.base)
                results = self.run_all_read_only([["status"], ["update", "--latest"], ["reset-db"], ["deploy", "--latest"]])
                self.assertEqual(pfx.snapshot_tree(self.base), before)
                self.assertEqual(stat.S_IMODE(self.paths["configuration"].stat().st_mode), 0o700)
                for args, code, out, err, controller in results[1:]:
                    self.assertEqual(code, 1, args)
                    self.assertEqual(controller.calls, [], args)

    def test_construction_alone_performs_no_filesystem_effect(self):
        shutil.rmtree(self.context.state_dir)
        (self.paths["configuration"] / "pf-config.json").unlink()
        before = pfx.snapshot_tree(self.base)
        controller = pf.Controller(self.context)
        self.assertEqual(pfx.snapshot_tree(self.base), before)
        self.assertFalse(self.context.state_dir.exists())
        self.assertIsNone(controller.read_journal())


@ROOT_REQUIRED
class StableLocks(Base):
    """A1-T04."""

    def setUp(self):
        super().setUp()
        self.alpha, self.alpha_paths = self.instance("alpha")
        self.beta, self.beta_paths = self.instance("beta")
        pfx.deployed_record(self.alpha)
        pfx.deployed_record(self.beta)

    def test_same_instance_conflicts_other_instance_proceeds(self):
        a1, a2, b = pf.Controller(self.alpha), pf.Controller(self.alpha), pf.Controller(self.beta)
        with a1.lock():
            with self.assertRaisesRegex(pf.Failure, "holds the lock of instance alpha"):
                with a2.lock():
                    pass
            with b.lock() as handle:
                self.assertEqual(handle.path, self.beta.lock_path)
        with a2.lock():
            pass

    def test_lock_inode_survives_purge_cleanup_and_is_never_unlinked(self):
        controller = RecordingController(self.alpha)
        before = os.stat(self.alpha.lock_path)
        with controller.lock() as held:
            with mock.patch.object(controller, "detailed_project_resources", return_value={
                "containers": [], "volumes": [], "networks": [], "images": [],
            }):
                with contextlib.redirect_stdout(io.StringIO()):
                    controller.finish_purge_cleanup("purge-x", delete_backups=True, reset_admin_config=False)
            self.assertFalse(controller.state.exists())
            self.assertFalse((self.alpha_paths["configuration"] / ".env").exists())
            after = os.stat(self.alpha.lock_path)
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            self.assertEqual(held.inode, (after.st_dev, after.st_ino))
            # Still held: a second acquisition conflicts while purge cleanup finished.
            with self.assertRaises(pf.Failure):
                with pf.Controller(self.alpha).lock():
                    pass
        self.assertTrue(self.alpha.record_path.is_file())
        self.assertTrue(self.alpha.lock_path.is_file())
        with pf.Controller(self.alpha).lock():
            pass
        self.assertTrue(controller.state.is_dir())

    def test_missing_or_symlinked_lock_is_refused_not_created(self):
        self.alpha.lock_path.unlink()
        with self.assertRaisesRegex(pf.Failure, "never created implicitly"):
            with pf.Controller(self.alpha).lock():
                pass
        self.assertFalse(self.alpha.lock_path.exists())
        outside = self.base / "outside.lock"
        outside.touch()
        self.alpha.lock_path.symlink_to(outside)
        with self.assertRaisesRegex(pf.Failure, "missing or unreadable"):
            with pf.Controller(self.alpha).lock():
                pass

    def test_pending_journal_routes_are_explicit_not_blanket(self):
        controller = pf.Controller(self.alpha)
        pf.write_json(self.alpha.journal_path, {"operation": "update", "phase": "migrating-live"})
        for command in ("deploy", "update", "backup", "reset-db", "permissions", "restore-instance", "release-check", None):
            with self.subTest(command=command):
                with self.assertRaisesRegex(pf.Failure, "previous operation is incomplete"):
                    with controller.lock(pending_route=command):
                        pass
        with controller.lock(pending_route="rollback"):
            pass
        pf.write_json(self.alpha.journal_path, {"operation": "purge", "phase": "deleting", "recovery": "purge-x"})
        with self.assertRaises(pf.Failure):
            with controller.lock(pending_route="resume"):
                pass
        with controller.lock(pending_route="purge"):
            pass
        self.assertEqual(
            controller.legal_routes({"operation": "purge", "phase": "deleting"}),
            ["pf purge --instance alpha: resume the recorded purge deletion plan with the already verified recovery bundle"],
        )
        # Read-only commands never take the lock and never consult the route table.
        with controller.lock(pending_route="purge"):
            code, out, err = run_main(["--instance", "alpha", "instances"], self.layout)
            self.assertEqual(code, 0)
            with mock.patch.object(pf, "Controller", RecordingController):
                code, out, err = run_main(["--instance", "alpha", "status"], self.layout)
            self.assertIn("operation: purge", out)


@ROOT_REQUIRED
class PendingJournalVisibility(Base):
    """A1-T16 and probe P12 (status hides journal without .env). Evidence level: cli."""

    def setUp(self):
        super().setUp()
        self.context, self.paths = self.instance("staging", project="partflow-staging")
        pfx.deployed_record(self.context)
        self.journal = {"operation": "purge", "phase": "deleting", "started": "20260914T000000Z",
                        "recovery": "purge-20260914T000000Z-111111111111-abcdef",
                        "delete_backups": True, "reset_admin_config": False, "target": {"token": "private"}}
        pf.write_json(self.context.journal_path, self.journal)
        (self.paths["configuration"] / ".env").unlink()

    def launcher(self, arguments, env=None):
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        environment.update(env or {})
        return subprocess.run([str(self.layout.launcher), *arguments], env=environment, cwd=str(self.base),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=120)

    def test_installed_cli_status_shows_journal_before_live_data_errors(self):
        before = pfx.snapshot_tree(self.base)
        result = self.launcher(["--instance", "staging", "status"],
                               env={"PF_HOME": "/nonexistent-a", "PF_CONFIG_DIR": "/nonexistent-a/config",
                                    "PYTHONPATH": str(self.base), "DOCKER_HOST": "tcp://127.0.0.1:1"})
        self.assertEqual(result.returncode, 1, result.stderr)
        out = result.stdout
        self.assertIn("Instance: staging", out)
        journal_at = out.index("INCOMPLETE OPERATION")
        self.assertIn("operation: purge", out)
        self.assertIn("phase: deleting", out)
        self.assertIn("Next supported action: pf purge --instance staging", out)
        self.assertIn("private fields not shown: target", out)
        self.assertNotIn("token", out)
        first_unavailable = out.index("unavailable")
        self.assertLess(journal_at, first_unavailable)
        self.assertIn("Runtime .env: unavailable: missing", out)
        self.assertIn("Status is partial", result.stderr)
        self.assertEqual(pfx.snapshot_tree(self.base), before)
        self.assertFalse((self.paths["configuration"] / ".env").exists())

    def test_installed_cli_doctor_and_instances_report_journal_without_repair(self):
        before = pfx.snapshot_tree(self.base)
        doctor = self.launcher(["doctor"])
        self.assertEqual(doctor.returncode, 1, doctor.stderr)
        self.assertLess(doctor.stdout.index("INCOMPLETE OPERATION"), doctor.stdout.index("unavailable"))
        self.assertIn("created, repaired and migrated nothing", doctor.stdout)
        listing = self.launcher(["instances"])
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("journal=purge/deleting", listing.stdout)
        self.assertEqual(pfx.snapshot_tree(self.base), before)

    def test_installed_cli_refuses_untrusted_bootstrap_configuration(self):
        conf = self.layout.root / "bootstrap" / "bootstrap.conf"
        original = conf.read_bytes()
        for text in (b"interpreter=python3\ncontrol_release=" + str(self.layout.release_dir).encode() + b"\n",
                     original + b"extra=/x\n",
                     original.replace(b"control_release=", b"control_release=/tmp/")):
            with self.subTest(conf=text[:40]):
                conf.write_bytes(text)
                result = self.launcher(["status"])
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("bootstrap.conf", result.stderr)
        conf.write_bytes(original)

    def test_in_process_restore_journal_is_visible_without_env_git_or_docker(self):
        pf.write_json(self.context.journal_path, {"operation": "restore-instance", "phase": "confirmed",
                                                  "recovery": "purge-x", "database": "partflow_staging"})
        with mock.patch.object(pf, "Controller", RecordingController):
            code, out, err = run_main(["status"], self.layout)
        self.assertEqual(code, 1)
        self.assertLess(out.index("INCOMPLETE OPERATION"), out.index("unavailable"))
        self.assertIn("operation: restore-instance", out)
        self.assertIn("Next supported action: none automatic", out)

    def test_legacy_control_directory_is_diagnostics_only_and_ignores_pf_environment(self):
        home = self.base / "legacyhome"
        control = home / "control"
        control.mkdir(parents=True)
        for name in ("pf-admin.py", "pf_instance.py"):
            shutil.copy2(pfx.PACKAGE / name, control / name)
        shutil.copy2(pfx.REPO_PACKAGE / "pf.sh", control / "pf.sh")
        os.chmod(control / "pf.sh", 0o700)
        (home / "config").mkdir()
        (home / "config" / "pf-config.json").write_text('{"project": "partflow-legacy"}\n')
        state = home / ".pf-state-partflow-legacy"
        state.mkdir()
        pf.write_json(state / "pending.json", {"operation": "update", "phase": "backup-ready", "checkpoint": "c1"})
        before = pfx.snapshot_tree(home)
        env = {"PATH": "/usr/bin:/bin", "PF_HOME": str(self.base / "elsewhere"), "PF_CONFIG_DIR": "/nonexistent"}
        status = subprocess.run(["sh", str(control / "pf.sh"), "status"], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, check=False, timeout=120)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("UNREGISTERED legacy installation (v2.5 layout) at " + str(home), status.stdout)
        self.assertIn("INCOMPLETE OPERATION in .pf-state-partflow-legacy: operation=update, phase=backup-ready", status.stdout)
        self.assertNotIn("elsewhere", status.stdout)
        update = subprocess.run(["sh", str(control / "pf.sh"), "update", "--latest"], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, check=False, timeout=120)
        self.assertEqual(update.returncode, 1)
        self.assertIn("refused on an unregistered installation", update.stderr)
        self.assertEqual(pfx.snapshot_tree(home), before)


def posix_acl(entries):
    """Build a system.posix_acl_access blob: entries are (tag, perm, id)."""
    blob = struct.pack("<I", 0x0002)
    for tag, perm, identifier in entries:
        blob += struct.pack("<HHI", tag, perm, identifier)
    return blob


@ROOT_REQUIRED
class ProtectedPathChecks(Base):
    """A1-T17 (offline part) and probes P03 (parent/symlink guard) and P04 (hardlink escape)."""

    def setUp(self):
        super().setUp()
        self.context, self.paths = self.instance("staging", project="partflow-staging")
        pfx.deployed_record(self.context)

    def validation(self):
        return pf_instance.validate_context(self.context, running_release=self.layout.release_dir,
                                            interpreter=sys.executable)

    def codes(self, validation):
        return sorted({finding.code for finding in validation.findings if finding.severity == "refuse"})

    def assert_mutation_refused_but_status_readable(self, expected_code):
        validation = self.validation()
        self.assertFalse(validation.mutation_allowed)
        self.assertIn(expected_code, self.codes(validation))
        before = pfx.snapshot_tree(self.base)
        with mock.patch.object(pf, "Controller", RecordingController):
            code, out, err = run_main(["permissions"], self.layout)
            self.assertEqual(code, 1)
            self.assertIn(expected_code, err)
            code, out, err = run_main(["status"], self.layout)
            self.assertIn("Instance: staging", out)
        self.assertEqual(pfx.snapshot_tree(self.base), before)

    def test_clean_fixture_validates(self):
        validation = self.validation()
        self.assertTrue(validation.mutation_allowed, [f.render() for f in validation.findings])

    def test_root_owned_leaf_under_writable_ancestor_is_refused(self):
        os.chmod(self.base, 0o770)
        self.assert_mutation_refused_but_status_readable("ancestor-replaceable")

    def test_leaf_modes_alone_are_no_security_claim(self):
        os.chmod(self.context.paths.private_state, 0o777)
        self.assert_mutation_refused_but_status_readable("writable")

    def test_untrusted_owner_is_refused(self):
        os.chown(self.context.record_path, 65534, -1)
        self.assert_mutation_refused_but_status_readable("untrusted-owner")

    def test_symlinked_control_payload_is_refused(self):
        compose = self.layout.release_dir / "compose.nas.yaml"
        outside = self.base / "outside-compose.yaml"
        compose.rename(outside)
        compose.symlink_to(outside)
        self.assert_mutation_refused_but_status_readable("symlink")

    def test_symlinked_registered_data_path_is_refused(self):
        real = self.paths["backups"]
        moved = self.base / "moved-backups"
        real.rename(moved)
        real.symlink_to(moved)
        self.assert_mutation_refused_but_status_readable("registered-path-symlink")

    def test_hard_linked_control_file_is_refused(self):
        os.link(self.layout.release_dir / "pf-admin.py", self.base / "another-name.py")
        self.assert_mutation_refused_but_status_readable("hardlinked")

    def test_permissions_refuse_hard_link_and_leave_outside_inode_untouched(self):
        private = self.base / "private-fixture"
        private.write_text("not a real secret")
        os.chmod(private, 0o600)
        os.link(private, self.paths["workspace"] / "hardlinked-source")
        source = self.paths["workspace"] / "frontend/app.txt"
        os.chmod(source, 0o600)
        before = pfx.snapshot_tree(self.paths["workspace"], self.paths["configuration"])
        controller = pf.Controller(self.context, running_release=self.layout.release_dir)
        with self.assertRaisesRegex(pf.Failure, "hard links"):
            controller.permissions()
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
        self.assertEqual(pfx.snapshot_tree(self.paths["workspace"], self.paths["configuration"]), before)

    def test_mount_boundary_inside_protected_tree_is_refused(self):
        real_lstat = os.lstat
        target = str(self.context.lock_path)

        def fake_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if str(path) == target:
                values = list(info)
                values[stat.ST_DEV] = info.st_dev + 1
                return os.stat_result(values)
            return info

        with mock.patch.object(os, "lstat", side_effect=fake_lstat):
            validation = self.validation()
        self.assertIn("mount-boundary", self.codes(validation))

    def test_acl_write_grant_and_unknown_acl_attribute_limit_mutation(self):
        registry_file = self.layout.root / "registry/instances.json"
        blob = posix_acl([(0x01, 6, 0xFFFFFFFF), (0x02, 6, 65534), (0x04, 0, 0xFFFFFFFF),
                          (0x10, 6, 0xFFFFFFFF), (0x20, 0, 0xFFFFFFFF)])
        try:
            os.setxattr(registry_file, "system.posix_acl_access", blob)
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM):
                self.skipTest("filesystem does not support POSIX ACL xattrs here: " + str(exc))
            raise
        try:
            validation = self.validation()
            self.assertIn("acl-write", self.codes(validation))
            self.assertEqual(validation.acl_state, "posix")
        finally:
            os.removexattr(registry_file, "system.posix_acl_access")
            os.chmod(registry_file, 0o600)
        self.assertTrue(self.validation().mutation_allowed)
        try:
            os.setxattr(registry_file, "user.syno_acl_probe", b"opaque")
        except OSError as exc:
            self.skipTest("user xattrs unsupported here: " + str(exc))
        try:
            validation = self.validation()
            self.assertIn("acl-unknown", self.codes(validation))
            self.assertEqual(validation.acl_state, "unknown")
        finally:
            os.removexattr(registry_file, "user.syno_acl_probe")

    def test_control_release_mismatch_refuses_mutation_but_not_diagnostics(self):
        validation = pf_instance.validate_context(self.context, running_release=self.base / "elsewhere",
                                                  interpreter=sys.executable)
        self.assertIn("control-release-mismatch", self.codes(validation))
        validation = pf_instance.validate_context(self.context, running_release=self.layout.release_dir,
                                                  interpreter="/bin/sh")
        self.assertIn("interpreter-mismatch", self.codes(validation))

    def test_tampered_control_file_is_detected_against_the_protected_record(self):
        (self.layout.release_dir / "compose.nas.yaml").write_bytes(b"services: {}\n")
        self.assertIn("control-file-hash", self.codes(self.validation()))
        (self.layout.release_dir / "extra.py").write_bytes(b"print('x')\n")
        self.assertIn("control-file-unlisted", self.codes(self.validation()))


class StaticCallSites(unittest.TestCase):
    def test_no_privileged_path_override_is_read_from_the_environment(self):
        source = (pfx.PACKAGE / "pf-admin.py").read_text()
        for name in ("PF_HOME", "PF_CONFIG_DIR", "PF_CONTROL_DIR", "PF_REPO_ROOT", "PF_PYTHON"):
            self.assertNotIn('os.environ.get("' + name, source)
            self.assertNotIn("os.environ[\"" + name, source)
        self.assertNotIn("allow_pending=", source)
        self.assertNotIn("assert_control_plane_secure", source)
        launcher = (pfx.REPO_PACKAGE / "pf.sh").read_text()
        self.assertIn("unset PF_PYTHON PF_HOME PF_REPO_ROOT PF_CONFIG_DIR PF_CONTROL_DIR", launcher)
        self.assertIn('"$INTERPRETER" -I -B "$ADMIN" --installation-root "$ROOT"', launcher)
        self.assertNotIn("${PF_HOME:-", launcher)


if __name__ == "__main__":
    unittest.main(verbosity=2)
