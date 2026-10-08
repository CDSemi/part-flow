"""PF-A3.1: lifecycle record contracts, strict bundle reading, legacy migration, the safe archive importer, the
deployed-source artifact store, emergency preservation, verification records, PostgreSQL requirements, purge
bundles and the new routes.

Case mapping (PF-A3.1 SPEC section 6.1; design r3 acceptance cases):
  A3-T01 strict manifests and legacy migration  -> Contracts (SC-1..SC-7), ManifestStrict (MF-1..MF-12),
                                                   Legacy (LG-1..LG-10)
  A3-T13 hostile archive import (filesystem)    -> ArchiveImport (AX-1..AX-17), ArchiveRoundTrip (AR-1..AR-7)
  A3-T02 deployed artifacts (filesystem)        -> DeployedArtifact (DA-1..DA-16)
  A3-T03 emergency preservation (offline part)  -> Emergency (EP-1..EP-12)
  verification / PostgreSQL / purge / routes    -> Verification (VR-1..VR-6), Postgres (PG-1..PG-9),
                                                   PurgeBundle (PB-1..PB-10), Routes (RT-1..RT-6)

Docker and PostgreSQL are simulated by test_pf_admin.FakeController (the real capture, reader, importer and seal
code runs); archives and filesystems are real. Real-filesystem cases run as uid 0 in the throwaway container and are
skipped with a named reason elsewhere. Nothing touches a real daemon, a real NAS or the running PartFlow stack.
"""
import ast
import contextlib
import copy
import gzip
import hashlib
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
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402
import test_pf_admin as tpa  # noqa: E402

PACKAGE = pfx.PACKAGE
pf = pfx.pf
pf_instance = pfx.pf_instance
pf_config = pf.pf_config
pf_source = pf.pf_source
pf_install = pf.pf_install
CONTRACTS = PACKAGE / "contracts"
EXAMPLES = CONTRACTS / "examples" / "lifecycle"
DEFS = pf_config.LIFECYCLE_SCHEMA["$defs"]
OLD, NEW = pfx.OLD, pfx.NEW
PROJECT = tpa.PROJECT
ROOT_FS = unittest.skipUnless(os.geteuid() == 0, "real-filesystem cases run as uid 0 inside the throwaway container")
MIB = 1024 * 1024


def example(name):
    return json.loads((EXAMPLES / name).read_bytes())


def record_problems(data, record, plan=None):
    """The strict reader's view of one example's bytes: parse, normalized byte form, schema, cross-field rules
    (PF-A3.2: a journal against its ``plan``)."""
    try:
        parsed = pf_instance.parse_strict_json(data, label="example")
    except pf_instance.ContextError as exc:
        return [str(exc)]
    if data != pf_instance.normalize_json(parsed):
        return ["not normalized"]
    return pf.lifecycle_errors(parsed, record, plan=plan)


def tree_hash(root):
    """(relative path, bytes sha256, mode) of every entry under ``root`` (no-follow)."""
    result = []
    for current, dirs, files in os.walk(str(root)):
        for name in sorted(dirs + files):
            path = Path(current) / name
            info = os.lstat(str(path))
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else None
            result.append((str(path.relative_to(root)), digest, stat.S_IMODE(info.st_mode)))
    return sorted(result)


class Base(unittest.TestCase):
    """A registered instance driven by test_pf_admin.FakeController (real lifecycle code, simulated programs)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = tpa.fixture(self.root)
        self.layout = tpa.fixture.layout
        self.c = tpa.FakeController(self.context)
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

    def invoke(self, arguments, *, confirm=None, interactive=True):
        """The CLI through main() with an operator at a terminal; ``confirm``: the patched confirm (a Mock)."""
        confirm = confirm if confirm is not None else mock.Mock()
        before = len(self.error_output.getvalue())
        with mock.patch.object(pf, "Controller", return_value=self.c), \
                mock.patch.object(pf, "confirm", confirm), \
                mock.patch.object(pf, "prompt_yes_no", return_value=True), \
                mock.patch.object(pf, "unattended", return_value=not interactive):
            code = pf.main(arguments, installation_root=self.layout.root, running_release=self.layout.release_dir,
                           trusted_launch=True)
        self.last_error = self.error_output.getvalue()[before:]
        return code

    def checkpoint(self, reason="scheduled-or-manual-backup"):
        return tpa.checkpoint(self.c, reason)

    def deployments(self):
        folder = self.c.deployments_dir
        return sorted(os.listdir(str(folder))) if folder.exists() else []

    def pointer(self):
        return json.loads((self.c.state / "deployed.json").read_text())

    def deploy(self):
        """A first deployment of NEW through the CLI (the record is sealed)."""
        tpa.write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        self.c.dbs = {}
        self.c.running = {"db": False, "backend": False, "frontend": False}
        self.assertEqual(self.invoke(["deploy", "--latest"]), 0, self.last_error)
        return self.c.current_deployment()

    def open_ops(self):
        """PF-A3.2: [(kind, phase)] of the blocking lifecycle operations."""
        return tpa.open_operations(self.c)

    def interrupted_update(self, phase="activating"):
        """PF-A3.2: an update interrupted in its backend start (activating), as a real plan and journal."""
        plan = pfx.lifecycle_plan(self.context, "update", pfx.default_effects("update", migration=False))
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(
            plan, phase=phase, unresolved="e0004",
            states={"e0001": "complete", "e0002": "complete", "e0003": "complete", "e0004": "unknown"}))
        return plan

    def records(self, bundle_id):
        directory = self.c.verifications_dir / bundle_id
        return [json.loads(path.read_bytes()) for path in sorted(directory.glob("ver-*.json"))] \
            if directory.exists() else []


# ============================================================================ SC: contracts (A3-T01)


class Contracts(unittest.TestCase):

    def test_sc1_embedded_schema_equals_the_contract_file(self):
        self.assertEqual(json.loads((CONTRACTS / "lifecycle-records.schema.json").read_text(encoding="utf-8")),
                         pf_config.LIFECYCLE_SCHEMA)
        self.assertEqual(pf_config.LIFECYCLE_RECORDS, ("operation_plan", "operation_journal", "deployment_record",
                                                       "recovery_manifest", "verification_record"))
        for name in pf_config.LIFECYCLE_RECORDS:
            self.assertIn(name, DEFS)
        for kind, phases in pf_config.JOURNAL_PHASES.items():
            self.assertTrue(pf_config.TERMINAL_PHASES <= phases, kind)
            self.assertTrue(phases <= set(pf_config.JOURNAL_PHASE_NAMES), kind)
        self.assertEqual(set(pf_config.JOURNAL_PHASES), set(pf_config.OPERATION_KINDS))

    def test_sc2_every_case_holds_and_legacy_pairs_migrate_byte_for_byte(self):
        cases = json.loads((EXAMPLES / "cases.json").read_text(encoding="utf-8"))
        files = sorted(path.name for path in EXAMPLES.glob("*.json") if path.name != "cases.json")
        self.assertEqual(sorted(case["file"] for case in cases), files)
        results = []
        for case in cases:
            with self.subTest(file=case["file"]):
                data = (EXAMPLES / case["file"]).read_bytes()
                if case["record"] == "legacy":
                    legacy = pf_instance.parse_strict_json(data, label=case["file"])
                    manifest, record = pf_config.migrate_legacy_manifest(
                        legacy, legacy_sha256=hashlib.sha256(data).hexdigest(), bundle_kind=case["bundle_kind"],
                        payload_sizes=case["payload_sizes"])
                    migrated = (EXAMPLES / case["migrated"]).read_bytes()
                    self.assertEqual(pf_instance.normalize_json(manifest), migrated)
                    self.assertEqual(record["after_sha256"], hashlib.sha256(migrated).hexdigest())
                    self.assertEqual(record["before_sha256"], hashlib.sha256(data).hexdigest())
                    problems = []
                else:
                    problems = record_problems(data, case["record"],
                                              plan=example(case["plan"]) if "plan" in case else None)
                expect = case["expect"]
                self.assertEqual(not problems, expect["valid"], problems)
                if not expect["valid"]:
                    self.assertTrue(any(expect["problem"] in problem for problem in problems),
                                    (expect["problem"], problems))
                results.append({"file": case["file"], "valid": not problems, "problems": problems[:3]})
        self.assertEqual(len(results), len(cases))

    def test_sc3_every_limit_fails_when_exceeded_by_one(self):
        healthy = example("checkpoint-healthy.json")
        plan = example("operation-plan.json")

        def exceeded(mutate, base=healthy, record="recovery_manifest"):
            value = copy.deepcopy(base)
            mutate(value)
            return pf_config.lifecycle_problems(value, record)

        store = healthy["stores"][0]
        cases = {
            "stores: more than 256": lambda m: m.update(stores=[store] * 257),
            "roles: more than 1024": lambda m: m.update(roles=m["roles"] * 1025),
            "extensions: more than 256": lambda m: m["stores"][0].update(extensions=store["extensions"] * 257),
            "exclusions: more than 256": lambda m: m.update(exclusions=[m["exclusions"][0]] * 257),
            "manual_prerequisites: more than 64": lambda m: m.update(manual_prerequisites=["x"] * 65),
            "migration_files: more than 20000": lambda m: m["compatibility"].update(
                migration_files={f"alembic/versions/{index}.py": "0" * 64 for index in range(20001)}),
            "alembic_heads_live: more than 16": lambda m: m["compatibility"].update(
                alembic_heads_live=[f"r{index:02d}" for index in range(17)]),
            "consistency_groups: more than 256": lambda m: m.update(
                consistency_groups=m["consistency_groups"] * 257),
            "payloads: more than 4096": lambda m: m.update(payloads=m["payloads"] * 1366),
            "unsupported_entries: more than 200": lambda m: m["workspace"].update(unsupported_entries=["x"] * 201),
            "alembic_heads: more than 16": lambda m: m["stores"][0].update(
                alembic_heads=[f"r{index:02d}" for index in range(17)]),
        }
        for message, mutate in cases.items():
            with self.subTest(limit=message):
                self.assertTrue(any(message in problem for problem in exceeded(mutate)), message)
        at_limit = exceeded(lambda m: m.update(manual_prerequisites=["x"] * 64))
        self.assertFalse(any("manual_prerequisites" in problem for problem in at_limit))
        self.assertTrue(any("effects: more than 1024" in problem for problem in exceeded(
            lambda p: p.update(effects=[dict(p["effects"][0], effect_id=f"e{index:04d}") for index in range(1025)]),
            base=plan, record="operation_plan")))
        self.assertTrue(any("coverage: more than 256" in problem for problem in exceeded(
            lambda p: p.update(coverage=p["coverage"] * 257), base=plan, record="operation_plan")))
        heads = exceeded(lambda m: m["compatibility"].update(alembic_heads_image=["r2", "r1"]))
        self.assertTrue(any("sorted, unique" in problem for problem in heads))
        platform = exceeded(lambda m: m["images"]["backend"].update(platform="Linux AMD64"))
        self.assertTrue(any("os/architecture" in problem for problem in platform))
        server = exceeded(lambda m: m["postgresql"].update(server_version_num=True))
        self.assertTrue(any("server_version_num" in problem for problem in server))

    def test_sc4_every_marker_target_resolves(self):
        found = []

        def visit(node):
            if isinstance(node, dict):
                marker = node.get("description", "")
                for prefix in ("null or ", "items ", "map "):
                    if isinstance(marker, str) and marker.startswith(prefix):
                        found.append(marker[len(prefix):])
                for value in node.values():
                    visit(value)

        visit(DEFS)
        self.assertTrue(found)
        for target in found:
            with self.subTest(target=target):
                if target.startswith("$defs."):
                    self.assertIn(target[len("$defs."):], DEFS)
                else:
                    self.assertIn(target, ("string", "sha256", "path", "scalar"))
        for name, schema in DEFS.items():
            with self.subTest(defs=name):
                # Every entry is inside the A1 keyword subset (validate_against_schema raises otherwise).
                pf_instance.validate_against_schema({}, schema)

    def test_sc5_one_byte_form(self):
        healthy = example("checkpoint-healthy.json")
        data = pf_instance.normalize_json(healthy)
        self.assertEqual(record_problems(data, "recovery_manifest"), [])
        self.assertFalse(data.endswith(b"\n"))
        self.assertEqual(record_problems(data + b"\n", "recovery_manifest"), ["not normalized"])
        self.assertEqual(record_problems(json.dumps(healthy, sort_keys=True).encode(), "recovery_manifest"),
                         ["not normalized"])

    def test_sc6_reserved_levels_are_schema_valid_and_produced_by_no_writer(self):
        self.assertEqual(record_problems((EXAMPLES / "verification-functional-schema-valid.json").read_bytes(),
                                         "verification_record"), [])
        captured = dict(example("verification-record.json"), level="captured",
                        target={"kind": "none", "names": [], "removed": False},
                        checks=[{"name": "sealed", "result": "passed", "detail": ""}])
        self.assertEqual(pf.lifecycle_errors(captured, "verification_record"), [])
        tree = ast.parse((PACKAGE / "pf-admin.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "write_verification"]
        self.assertTrue(calls)
        for call in calls:
            level = next(keyword.value for keyword in call.keywords if keyword.arg == "level")
            self.assertIsInstance(level, ast.Constant)
            self.assertEqual(level.value, "data_restore_verified")
        view = types.SimpleNamespace(bundle_id="x", bundle_kind="checkpoint", manifest_sha256="0" * 64)
        controller = pf.Controller.__new__(pf.Controller)
        for level in ("captured", "functional_recovery_verified"):
            with self.subTest(level=level):
                with self.assertRaisesRegex(pf.Failure, "writes data_restore_verified verification records only"):
                    pf.Controller.write_verification(controller, view, level=level, result="passed",
                                                     target={}, checks=[], started_at="20261007T000000Z")


@ROOT_FS
class Pinning(Base):

    def test_sc7_manifest_sha256_is_the_hash_of_the_exact_file_bytes(self):
        view = self.checkpoint()
        data = (view.folder / "manifest.json").read_bytes()
        self.assertFalse(data.endswith(b"\n"))
        self.assertEqual(data, pf_instance.normalize_json(view.manifest))
        recorded = (view.folder / "manifest.sha256").read_text().splitlines()[0]
        self.assertEqual(recorded, hashlib.sha256(data).hexdigest())
        self.assertEqual(view.manifest_sha256, recorded)
        self.assertEqual([item["manifest_sha256"] for item in self.records(view.bundle_id)], [recorded])
        # A legacy manifest carries its write_json newline and is hashed with it, exactly as written.
        tree = Path(self.temp.name) / "legacy-tree"
        pfx.source_fixture(tree)
        legacy_id = "20260901T120000Z-" + OLD[:12] + "-a1b2c3"
        folder = pfx.legacy_checkpoint(self.c.backups_dir / legacy_id, project=PROJECT, tree=tree)
        legacy_bytes = (folder / "manifest.json").read_bytes()
        self.assertTrue(legacy_bytes.endswith(b"\n"))
        self.assertEqual(self.c.verify_snapshot(legacy_id).manifest_sha256, hashlib.sha256(legacy_bytes).hexdigest())
        (folder / "manifest.sha256").write_text(hashlib.sha256(legacy_bytes[:-1]).hexdigest() + "\n")
        with self.assertRaisesRegex(pf.Failure, "^manifest-checksum-mismatch: " + legacy_id):
            self.c.verify_snapshot(legacy_id)


# ============================================================================ MF: strict manifests (A3-T01)


@ROOT_FS
class ManifestStrict(Base):
    """MF-1..MF-12: every defect is refused by the strict reader before any confirmation, journal, staging,
    extraction or runner call, and the output claims no verification level."""

    def setUp(self):
        super().setUp()
        self.view = self.checkpoint()
        self.folder = self.view.folder

    def refused(self, pattern, *, command=None):
        """MF-12 for one defect: the coded refusal through `pf rollback <id>`, and nothing after it."""
        command = command or ["rollback", self.view.bundle_id]
        confirm = mock.Mock()
        calls = len(self.c.calls)
        state_before = sorted(os.listdir(str(self.c.state)))
        output_before = len(self.output.getvalue())
        self.assertEqual(self.invoke(command, confirm=confirm), 1)
        self.assertRegex(self.last_error, pattern)
        confirm.assert_not_called()
        self.assertEqual(pfx.operations_of(self.context), [])
        self.assertEqual(self.c.calls[calls:], [])
        self.assertEqual(sorted(os.listdir(str(self.c.state))), state_before)  # no rollback-* extraction left
        self.assertFalse([name for name in self.deployments() if name.startswith(".staging-")])
        printed = self.output.getvalue()[output_before:]
        self.assertNotIn("data-restore", printed)
        self.assertNotIn("data_restore_verified", printed)

    def rewrite(self, mutate):
        tpa.rewrite_manifest(self.folder, mutate)

    def test_mf1_empty_payloads(self):
        self.rewrite(lambda m: m.update(payloads=[]))
        self.refused(r"manifest-invalid: .*payloads: empty")

    def test_mf2_store_without_dump(self):
        self.rewrite(lambda m: m.update(payloads=[item for item in m["payloads"] if item["type"] != "database_dump"]))
        self.refused(r"manifest-invalid: .*no database_dump payload")

    def test_mf3_healthy_missing_db_image(self):
        def mutate(m):
            m["images"]["db"] = None
            m["exclusions"].append({"item": "image:db", "reason": "x"})
        self.rewrite(mutate)
        self.refused(r"manifest-invalid: .*images.db is required")

    def test_mf4_healthy_missing_backend_image(self):
        def mutate(m):
            m["images"]["backend"] = None
            m["exclusions"].append({"item": "image:backend", "reason": "x"})
        self.rewrite(mutate)
        self.refused(r"manifest-invalid: .*images.backend and images.frontend are required")

    def test_mf5_wrong_schema_versions(self):
        for value in (2, True, "1"):
            with self.subTest(value=value):
                self.rewrite(lambda m: m.update(schema_version=value))
                self.refused(r"^ERROR: manifest-schema-unsupported: " + self.view.bundle_id + r": schema_version "
                             r".*\(this control reads schema_version 1 and legacy formats 1 and 2\)\. Nothing was "
                             "changed.")

    def test_mf6_bundle_id_kind_and_folder(self):
        self.rewrite(lambda m: m.update(bundle_id="20200101T000000Z-" + OLD[:12] + "-000000"))
        self.refused(r"manifest-invalid: .*is not the folder name")
        self.rewrite(lambda m: m.update(bundle_id="purge-" + self.view.bundle_id))
        self.refused(r"manifest-invalid: .*bundle_id does not match bundle_kind")

    def test_mf7_duplicate_traversal_and_absolute_payload_paths(self):
        self.rewrite(lambda m: m["payloads"].append(copy.deepcopy(m["payloads"][0])))
        self.refused(r"manifest-invalid: .*duplicate path")
        for path in ("../x", "/x"):
            with self.subTest(path=path):
                def mutate(m, path=path):
                    m["payloads"][0]["path"] = path
                self.rewrite(mutate)
                self.refused(r"manifest-invalid: .*is not canonical")

    def test_mf8_negative_or_float_size_and_bad_hash(self):
        for field, value, pattern in (("size", -1, "below minimum"), ("size", 1.5, "expected integer"),
                                      ("sha256", "xyz", "does not match")):
            with self.subTest(field=field, value=value):
                def mutate(m, field=field, value=value):
                    m["payloads"][0][field] = value
                self.rewrite(mutate)
                self.refused(r"manifest-invalid: .*" + pattern)

    def test_mf9_duplicate_key_nan_and_not_normalized(self):
        data = (self.folder / "manifest.json").read_bytes()
        variants = {
            "Duplicate JSON object key": data.replace(b'{"bundle_id"', b'{"bundle_id":"x","bundle_id"', 1),
            "Non-finite JSON number": data.replace(b'"major":16', b'"major":NaN', 1),
            "not normalized": json.dumps(json.loads(data), indent=1).encode(),
        }
        for message, raw in variants.items():
            with self.subTest(message=message):
                (self.folder / "manifest.json").write_bytes(raw)
                (self.folder / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
                self.refused(r"manifest-invalid: " + self.view.bundle_id + r": 1 problem\(s\): .*" + message)

    def test_mf10_payload_differences_links_and_unlisted_files(self):
        dump = self.folder / "database.dump"
        original = dump.read_bytes()
        dump.write_bytes(original + b"x")
        self.refused(r"^ERROR: bundle-payload-mismatch: " + self.view.bundle_id + r": database.dump: size\. Nothing "
                     r"was changed\.")
        dump.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        self.refused(r"bundle-payload-mismatch: .*database.dump: hash")
        dump.write_bytes(original)
        os.link(str(dump), str(Path(self.temp.name) / "second-name"))
        self.refused(r"bundle-payload-mismatch: .*database.dump: linked")
        os.unlink(str(Path(self.temp.name) / "second-name"))
        moved = Path(self.temp.name) / "outside.dump"
        os.replace(str(dump), str(moved))
        os.symlink(str(moved), str(dump))
        self.refused(r"bundle-payload-mismatch: .*database.dump: not a regular file")
        os.unlink(str(dump))
        os.replace(str(moved), str(dump))
        (self.folder / "planted.sh").write_text("echo planted\n")
        self.refused(r"^ERROR: bundle-unlisted-file: " + self.view.bundle_id + ": planted.sh is not listed in the "
                     r"manifest\. Nothing was changed\.")
        os.unlink(str(self.folder / "planted.sh"))
        self.assertEqual(self.c.verify_snapshot(self.view.bundle_id).level, "data_restore_verified")

    def test_mf11_newer_schema_version_is_unsupported(self):
        self.rewrite(lambda m: m.update(schema_version=2))
        self.refused(r"manifest-schema-unsupported: .*schema_version 2")
        listing = self.c.snapshots()
        self.assertIsInstance(listing[0], pf.InvalidBundle)
        self.assertEqual(listing[0].code, "manifest-schema-unsupported")

    def test_mf12_restore_instance_refuses_a_defective_bundle_before_anything(self):
        # MF-12 also for the purge bundle reader: a tampered schema 1 bundle never reaches a confirmation.
        bundle = PurgeBundle.build(self)
        (self.c.state / "deployed.json").unlink()
        (bundle.folder / "postgres-globals.sql").write_bytes(b"-- tampered\n")
        self.refused(r"bundle-payload-mismatch: " + bundle.bundle_id + ": postgres-globals.sql",
                     command=["restore-instance", bundle.bundle_id])


# ============================================================================ LG: legacy manifests (A3-T01)


@ROOT_FS
class Legacy(Base):

    def tree(self, revision=OLD):
        tree = Path(self.temp.name) / ("tree-" + revision[:4])
        if not tree.exists():
            pfx.source_fixture(tree, revision)
        return tree

    def legacy(self, backup_id="20260901T120000Z-" + OLD[:12] + "-a1b2c3", **kwargs):
        return pfx.legacy_checkpoint(self.c.backups_dir / backup_id, project=PROJECT, tree=self.tree(), **kwargs)

    def test_lg1_healthy_migration_is_deterministic_and_keeps_the_claims_as_claims(self):
        folder = self.legacy()
        before = tree_hash(folder)
        calls = len(self.c.calls)
        listed = self.c.snapshots()[0]
        self.assertEqual(self.c.calls[calls:], [])  # image IDs come from the legacy manifest, never a daemon
        with self.c.lock():
            operation = self.c.operation_dir
            read = self.c.verify_snapshot(folder.name)
        again = self.c.snapshots()[0]
        for view in (listed, read, again):
            self.assertEqual(pf_instance.normalize_json(view.manifest), pf_instance.normalize_json(listed.manifest))
            self.assertEqual(view.manifest_sha256, listed.manifest_sha256)
            self.assertEqual(view.capture_class, "healthy_checkpoint")
        manifest = listed.manifest
        self.assertEqual(manifest["source"], {"provenance": "unknown", "commit": None, "remote": None,
                                              "origin": "legacy-claim", "payload": "source.tar.gz",
                                              "entries_sha256": None})
        self.assertEqual(manifest["legacy"]["claimed_source_revision"], OLD)
        self.assertIs(manifest["legacy"]["claimed_source_verified"], True)
        self.assertEqual(manifest["legacy"]["claimed_restore_test"], "passed")
        self.assertEqual(manifest["images"]["backend"]["id"], tpa.OLD_BACKEND)
        self.assertEqual(listed.source_hypothesis, OLD)
        self.assertEqual(listed.source_display, "claimed " + OLD[:12])
        self.assertEqual(listed.level, "captured")  # no verification record is synthesized from restore_test
        record = json.loads((operation / f"manifest-migration-{folder.name}.json").read_bytes())
        migrated = (operation / f"migrated-{folder.name}.json").read_bytes()
        self.assertEqual(migrated, pf_instance.normalize_json(manifest))
        self.assertEqual(record["after_sha256"], hashlib.sha256(migrated).hexdigest())
        self.assertEqual(record["before_sha256"], listed.manifest_sha256)
        self.assertEqual(stat.S_IMODE((operation / f"migrated-{folder.name}.json").stat().st_mode), 0o600)
        self.assertEqual(tree_hash(folder), before)

    def test_lg2_claimed_before_rollback_is_an_emergency_preservation(self):
        folder = self.legacy(extra={"reason": "before-rollback", "source_verified": False})
        view = self.c.verify_snapshot(folder.name)
        self.assertEqual(view.capture_class, "emergency_preservation")
        self.assertEqual((view.reason, view.manifest["legacy"]["claimed_reason"]), ("legacy", "before-rollback"))
        self.assertEqual(view.display_reason, "legacy:before-rollback")

    def test_lg3_a_missing_image_entry_is_partial(self):
        folder = self.legacy(extra={"images": {"frontend": {"reference": f"{PROJECT}-frontend:backup-x",
                                                            "id": tpa.OLD_FRONTEND}}})
        view = self.c.verify_snapshot(folder.name)
        self.assertEqual(view.capture_class, "partial")
        self.assertIn("image:backend", {item["item"] for item in view.manifest["exclusions"]})

    def test_lg4_format_1_checkpoint_is_partial_and_never_a_rollback_target(self):
        folder = self.legacy(fmt=1)
        view = self.c.verify_snapshot(folder.name)
        self.assertEqual(view.capture_class, "partial")
        self.assertIn("migration-files", {item["item"] for item in view.manifest["exclusions"]})
        self.assertEqual(self.invoke(["rollback", folder.name]), 1)
        self.assertIn(f"ERROR: checkpoint-not-rollback-target: {folder.name} is a partial capture: evidence and data "
                      "for repair or export, never a rollback target. Nothing was changed.", self.last_error)
        self.assertEqual(pfx.operations_of(self.context), [])

    def test_lg5_lg6_incomplete_or_unmapped_legacy_manifests_are_unsupported(self):
        incomplete = self.legacy(extra={"status": "incomplete"})
        with self.assertRaisesRegex(pf.Failure, "^manifest-schema-unsupported: " + incomplete.name
                                    + ": status is not complete"):
            self.c.verify_snapshot(incomplete.name)
        unmapped = self.legacy("20260902T120000Z-" + OLD[:12] + "-b2c3d4", files={"notes/extra.bin": b"x"})
        with self.assertRaisesRegex(pf.Failure, "^manifest-schema-unsupported: " + unmapped.name
                                    + ": legacy payload 'notes/extra.bin' has no payload type"):
            self.c.verify_snapshot(unmapped.name)

    def test_lg7_the_legacy_folder_is_never_rewritten_and_unlisted_files_are_never_opened(self):
        folder = self.legacy()
        os.mkfifo(str(folder / "operator.fifo"))  # opening it would block the reader
        before = tree_hash(folder)
        with self.c.lock():
            operation = self.c.operation_dir
            view = self.c.verify_snapshot(folder.name)
        self.assertEqual(view.legacy["unlisted_entries"], ["operator.fifo"])
        record = json.loads((operation / f"manifest-migration-{folder.name}.json").read_bytes())
        self.assertEqual(record["unlisted_entries"], ["operator.fifo"])
        self.assertEqual(tree_hash(folder), before)

    def test_lg8_listing_writes_nothing(self):
        self.legacy()
        before = pfx.snapshot_tree(self.context.paths.private_state)
        self.assertEqual(self.invoke(["backups"]), 0, self.last_error)
        self.assertIn("legacy-format-2", self.output.getvalue())
        self.assertEqual(pfx.snapshot_tree(self.context.paths.private_state), before)
        self.assertEqual(os.listdir(str(self.context.operations_dir)), [])

    def test_lg9_format_2_purge_bundle_migrates_to_a_valid_healthy_purge_bundle(self):
        recovery_id = "purge-20260903T120000Z-" + OLD[:12] + "-d4e5f6"
        dumps = {"databases/db-00112233445566aa.dump": b'{"heads": [], "rows": ["kept"], "connections": false}'}
        folder = pfx.legacy_purge_bundle(
            self.c.recovery_root / recovery_id, project=PROJECT, root=self.root, tree=self.tree(), files=dumps,
            extra={"databases": [{"name": "partflow_staging", "allow_connections": True,
                                  "dump": "databases/active.dump", "heads": ["r1"]},
                                 {"name": "pf_keep_20260801t000000z_aa11bb", "allow_connections": False,
                                  "dump": "databases/db-00112233445566aa.dump", "heads": []}]})
        view = self.c.verify_recovery(folder)
        self.assertEqual((view.bundle_kind, view.capture_class), ("purge-bundle", "healthy_checkpoint"))
        kept = next(store for store in view.stores if store["role"] == "retained")
        self.assertEqual((kept["alembic_heads"], kept["allow_connections"]), ([], False))
        self.assertIn(pf_config.LEGACY_LIMITATIONS["retained-heads"], view.manifest["legacy"]["limitations"])
        self.assertEqual(view.derived_from, "20261005T235900Z-" + OLD[:12] + "-aaaaaa")
        self.assertEqual(pf.lifecycle_errors(view.manifest, "recovery_manifest"), [])

    def test_lg10_format_1_purge_bundle_keeps_its_env_out_of_the_deployed_source(self):
        bundle = PurgeBundle.legacy(self, fmt=1)
        self.assertIn("config_env", {item["item"] for item in bundle.manifest["exclusions"]})
        self.assertIn(pf_config.LEGACY_LIMITATIONS["format-1-env"], bundle.manifest["legacy"]["limitations"])
        staged = []
        original = self.c.stage_deployment

        def stage(candidate, manifest, **kwargs):
            staged.append(sorted(entry["path"] for entry in manifest["entries"]))
            self.assertFalse((Path(candidate) / ".env").exists())
            return original(candidate, manifest, **kwargs)

        with mock.patch.object(self.c, "stage_deployment", side_effect=stage):
            self.assertEqual(PurgeBundle.restore(self, bundle), 0, self.last_error)
        self.assertNotIn(".env", staged[0])
        self.assertNotIn("DEPLOYED_SOURCE.txt", staged[0])
        # The runtime .env came from the format 1 source archive and lives only in the configuration directory.
        self.assertIn("POSTGRES_PASSWORD=abc123", (self.c.config_dir / ".env").read_text())
        self.assertFalse((self.root / ".env").exists())
        record = self.c.current_deployment().record
        with tarfile.open(self.c.deployments_dir / record["deployment_id"] / "source.tar.gz") as archive:
            self.assertNotIn(".env", archive.getnames())

    def restore_with_planted_env(self, plant):
        """Audit AF-1: a format 1 purge bundle whose folder also holds an unlisted runtime .env (``plant``)."""
        bundle = PurgeBundle.legacy(self, fmt=1)
        plant(bundle.folder)
        view = self.c.verify_recovery(bundle.folder)
        self.assertIn("configuration", view.legacy["unlisted_entries"])
        self.assertEqual(PurgeBundle.restore(self, view), 0, self.last_error)
        env = (self.c.config_dir / ".env").read_text()
        self.assertIn("POSTGRES_PASSWORD=abc123", env)  # the verified .env of the source payload
        self.assertNotIn("unlisted-secret", env)

    def test_lg11_an_unlisted_configuration_env_is_never_the_runtime_env(self):
        def plant(folder):
            (folder / "configuration").mkdir()
            (folder / "configuration" / ".env").write_text(pfx.ENV_TEXT.replace("abc123", "unlisted-secret"))

        self.restore_with_planted_env(plant)

    def test_lg11_an_unlisted_configuration_link_is_never_followed(self):
        def plant(folder):
            outside = Path(self.temp.name) / "outside-configuration"
            outside.mkdir()
            (outside / ".env").write_text(pfx.ENV_TEXT.replace("abc123", "unlisted-secret"))
            os.symlink(str(outside), str(folder / "configuration"))

        self.restore_with_planted_env(plant)

    def test_lg12_a_legacy_state_file_is_restored_only_from_its_verified_payload(self):
        # Audit AF-1: a state_files name without a state/<name> checksum is refused; a listed one is restored from
        # its verified bytes.
        recovery_id = "purge-20260904T120000Z-" + OLD[:12] + "-e5f6a7"
        folder = pfx.legacy_purge_bundle(self.c.recovery_root / recovery_id, project=PROJECT, root=self.root,
                                         tree=self.tree(), extra={"state_files": ["deployed.json", "last-reset.json"]})
        (folder / "state" / "last-reset.json").write_text('{"unlisted": true}\n')
        with self.assertRaisesRegex(pf.Failure, "^manifest-schema-unsupported: " + recovery_id + ": state file "
                                    "last-reset.json has no state/last-reset.json payload"):
            self.c.verify_recovery(folder)
        listed = b'{"time": "20261005T000000Z"}\n'
        bundle = PurgeBundle.legacy(self, files={"state/last-reset.json": listed},
                                    extra={"state_files": ["deployed.json", "last-reset.json"]})
        self.assertEqual(PurgeBundle.restore(self, bundle), 0, self.last_error)
        self.assertEqual((self.c.state / "last-reset.json").read_bytes(), listed)

    def test_lg13_a_bundle_folder_with_too_many_entries_is_listed_invalid(self):
        # Audit AF-4: one oversized folder is `[invalid: bundle-unlisted-file]`; the other folders stay listed.
        oversized = self.legacy()
        healthy = self.legacy("20260902T120000Z-" + OLD[:12] + "-b2c3d4")
        junk = oversized / "junk"
        junk.mkdir()
        for index in range(20001):
            os.close(os.open(str(junk / str(index)), os.O_WRONLY | os.O_CREAT, 0o600))
        listing = {item.bundle_id: item for item in self.c.snapshots()}
        self.assertIsInstance(listing[oversized.name], pf.InvalidBundle)
        self.assertEqual(listing[oversized.name].code, "bundle-unlisted-file")
        self.assertIsInstance(listing[healthy.name], pf.BundleView)
        self.assertEqual(self.invoke(["backups"]), 0, self.last_error)
        self.assertIn(f"{oversized.name}  [invalid: bundle-unlisted-file]", self.output.getvalue())


# ============================================================================ AX/AR: archive import (A3-T13)


def tar_bytes(members, *, fmt=tarfile.PAX_FORMAT, pax_headers=None, gnu=False):
    """A gzip tar of crafted members: [(TarInfo, data bytes or None)]."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT if gnu else fmt,
                      pax_headers=pax_headers) as archive:
        for info, data in members:
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def member(name, data=b"x", *, kind=tarfile.REGTYPE, mode=0o644, linkname="", pax=None):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.linkname = linkname
    if pax:
        info.pax_headers = dict(pax)
    if kind in (tarfile.REGTYPE, tarfile.AREGTYPE):
        info.size = len(data)
        return info, data
    return info, None


@ROOT_FS
class ArchiveImport(unittest.TestCase):
    """AX-1..AX-17: every refusal leaves the destination parent and a sentinel outside it unchanged."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.parent = self.base / "parent"
        self.parent.mkdir()
        self.sentinel = self.base / "sentinel.txt"
        self.sentinel.write_text("outside\n")

    def tearDown(self):
        self.temp.cleanup()

    def sentinel_state(self):
        info = os.lstat(str(self.sentinel))
        return (self.sentinel.read_bytes(), info.st_mode, info.st_mtime_ns, info.st_ctime_ns,
                sorted(os.listdir(str(self.parent))), sorted(os.listdir(str(self.base))))

    def archive(self, data, name="payload.tar.gz"):
        path = self.base / name
        path.write_bytes(data)
        return path

    def run_import(self, path, limits=pf_source.SOURCE_LIMITS, expected=None, inventory=None):
        fd = os.open(str(path), os.O_RDONLY)
        parent_fd = os.open(str(self.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            inventory = inventory or pf_source.inspect_archive(fd, limits=limits, expected=expected)
            return pf_source.extract_archive(fd, parent_fd, "out", inventory, limits=limits)
        finally:
            os.close(parent_fd)
            os.close(fd)

    def refused(self, data, reason, *, code="archive-member-refused", limits=pf_source.SOURCE_LIMITS,
                expected=None):
        path = self.archive(data)
        before = self.sentinel_state()
        with self.assertRaises(pf_source.ArchiveRefused) as caught:
            self.run_import(path, limits=limits, expected=expected)
        self.assertEqual((caught.exception.code, caught.exception.reason), (code, reason))
        self.assertEqual(self.sentinel_state(), before)
        self.assertFalse((self.parent / "out").exists())
        return caught.exception

    def test_ax1_absolute(self):
        self.refused(tar_bytes([member("/etc/pf-escape")]), "absolute")

    def test_ax2_traversal(self):
        for name in ("../escape", "a/../../escape", "a/./b"):
            with self.subTest(name=name):
                self.refused(tar_bytes([member(name)]), "traversal")
        self.refused(tar_bytes([member("a//b")]), "empty-component")

    def test_ax3_duplicate_after_normalization(self):
        self.refused(tar_bytes([member("a"), member("./a")]), "duplicate")

    def test_ax4_file_and_directory_at_one_path(self):
        self.refused(tar_bytes([member("a"), member("a/", kind=tarfile.DIRTYPE, mode=0o755)]), "duplicate")
        self.refused(tar_bytes([member("d/x"), member("d")]), "duplicate")
        self.refused(tar_bytes([member("f"), member("f/inside")]), "duplicate")

    def test_ax5_symlinks(self):
        self.refused(tar_bytes([member("secret", kind=tarfile.SYMTYPE, linkname="/etc/passwd")]), "type")
        self.refused(tar_bytes([member("a", kind=tarfile.SYMTYPE, linkname="../../outside")]), "type")

    def test_ax6_hardlink(self):
        self.refused(tar_bytes([member("a"), member("b", kind=tarfile.LNKTYPE, linkname="a")]), "type")

    def test_ax7_devices_and_fifo(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                self.refused(tar_bytes([member("special", kind=kind)]), "type")

    def test_ax8_gnu_sparse(self):
        info = tarfile.TarInfo("sparse")
        info.type = tarfile.GNUTYPE_SPARSE
        info.size = 0
        header = info.tobuf(tarfile.GNU_FORMAT)
        self.refused(gzip.compress(header + b"\0" * 1024), "type")
        self.refused(tar_bytes([member("sparse", pax={"GNU.sparse.size": "1"})]), "pax-key")

    def test_ax9_pax_keys_and_global_headers(self):
        for key in ("linkpath", "SCHILY.xattr.user.x", "LIBARCHIVE.xattr.user.x", "SCHILY.acl.access", "hdrcharset"):
            with self.subTest(key=key):
                self.refused(tar_bytes([member("file", pax={key: "/etc/passwd" if key == "linkpath" else "x"})]),
                             "pax-key")
        self.refused(tar_bytes([member("file")], pax_headers={"comment": "global"}), "pax-key")

    def test_ax10_special_bits_on_a_file(self):
        for mode in (0o4755, 0o2755, 0o1644):
            with self.subTest(mode=oct(mode)):
                self.refused(tar_bytes([member("tool", mode=mode)]), "mode-bits")

    def test_ax11_path_component_and_depth_limits(self):
        self.refused(tar_bytes([member("a" * 200 + "/" + "b" * 200 + "/" + "c" * 200 + "/" + "d" * 200 + "/"
                                       + "e" * 230)]), "path-length")
        self.refused(tar_bytes([member("x" * 256)]), "component-length")
        self.refused(tar_bytes([member("/".join(["d"] * 65))]), "depth")

    def test_ax12_member_count(self):
        limits = pf_source.ArchiveLimits(3, MIB, MIB, 1024, 255, 64)
        self.refused(tar_bytes([member(f"f{index}") for index in range(4)]), "member-count", limits=limits)

    def test_ax13_declared_size_is_refused_before_its_data_is_read(self):
        info = tarfile.TarInfo("huge")
        info.size = 1024 ** 4  # 1 TiB declared, 1 MiB present
        data = gzip.compress(info.tobuf(tarfile.PAX_FORMAT) + b"\0" * MIB)
        self.refused(data, "file-size")

    def test_ax14_gzip_bomb_is_refused_by_the_running_total(self):
        limits = pf_source.ArchiveLimits(100, 8 * MIB, 64 * MIB, 1024, 255, 64)
        bomb = tar_bytes([member("a", b"\0" * (6 * MIB)), member("b", b"\0" * (6 * MIB))])
        self.assertLess(len(bomb), 64 * 1024)
        self.refused(bomb, "total-size", limits=limits)
        truncated = bomb[:len(bomb) // 2]
        path = self.archive(truncated, "truncated.tar.gz")
        with self.assertRaises(pf_source.ArchiveRefused) as caught:
            self.run_import(path)
        self.assertEqual(caught.exception.code, "archive-unreadable")
        self.assertFalse((self.parent / "out").exists())

    def test_ax15_declared_counts_must_equal_the_archive(self):
        data = tar_bytes([member("a", b"12345"), member("b", b"67")])
        inventory = pf_source.inspect_archive(os.open(str(self.archive(data, "probe.tar.gz")), os.O_RDONLY),
                                              limits=pf_source.SOURCE_LIMITS)
        self.assertEqual((inventory.expanded_bytes, len(inventory.members)), (7, 2))
        for expected in ((8, None, None), (None, 3, None), (None, None, "0" * 64)):
            with self.subTest(expected=expected):
                self.refused(data, "declared-mismatch", expected=expected)
        self.run_import(self.archive(data), expected=(7, 2, inventory.members_sha256))

    def test_ax16_pass_two_divergence_is_archive_changed(self):
        first = self.archive(tar_bytes([member("a", b"first")]), "first.tar.gz")
        second = self.archive(tar_bytes([member("a", b"other-bytes")]), "second.tar.gz")
        fd = os.open(str(first), os.O_RDONLY)
        try:
            inventory = pf_source.inspect_archive(fd, limits=pf_source.SOURCE_LIMITS)
        finally:
            os.close(fd)
        before = self.sentinel_state()
        with self.assertRaises(pf_source.ArchiveRefused) as caught:
            self.run_import(second, inventory=inventory)  # the bytes behind the descriptor changed between passes
        self.assertEqual(caught.exception.code, "archive-changed")
        self.assertEqual(self.sentinel_state(), before)
        self.assertFalse((self.parent / "out").exists())

    def test_ax17_a_setgid_directory_member_is_accepted_and_extracts_0700(self):
        data = tar_bytes([member("shared/", kind=tarfile.DIRTYPE, mode=0o2770), member("shared/file", b"ok")])
        result = self.run_import(self.archive(data))
        self.assertEqual(stat.S_IMODE(os.lstat(str(self.parent / "out" / "shared")).st_mode), 0o700)
        self.assertEqual([entry["path"] for entry in result["entries"]], ["shared/file"])

    def test_ax18_an_oversized_extended_header_is_refused_before_tarfile_reads_it(self):
        # Audit AF-8: tarfile reads a GNU longname/longlink or PAX header payload whole while parsing the header; a
        # declared size above EXTENDED_HEADER_LIMIT is refused first (1 GiB declared, 1 MiB present).
        for kind, reason in ((tarfile.GNUTYPE_LONGNAME, "path-length"), (tarfile.GNUTYPE_LONGLINK, "path-length"),
                             (tarfile.XHDTYPE, "pax-key"), (tarfile.XGLTYPE, "pax-key")):
            with self.subTest(kind=kind):
                info = tarfile.TarInfo("././@LongLink")
                info.type = kind
                info.size = 1024 ** 3
                self.refused(gzip.compress(info.tobuf(tarfile.USTAR_FORMAT) + b"a" * MIB), reason)
        self.assertLess(pf_source.EXTENDED_HEADER_LIMIT, MIB)


@ROOT_FS
class ArchiveRoundTrip(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.tree = self.base / "tree"
        pfx.source_fixture(self.tree)
        tool = self.tree / "scripts" / "tool.sh"
        tool.parent.mkdir()
        tool.write_text("#!/bin/sh\n")
        os.chmod(str(tool), 0o755)

    def tearDown(self):
        self.temp.cleanup()

    def imported(self, archive, name="out", limits=pf_source.SOURCE_LIMITS):
        fd = os.open(str(archive), os.O_RDONLY)
        parent_fd = os.open(str(self.base), os.O_RDONLY | os.O_DIRECTORY)
        try:
            inventory = pf_source.inspect_archive(fd, limits=limits)
            return inventory, pf_source.extract_archive(fd, parent_fd, name, inventory, limits=limits)
        finally:
            os.close(parent_fd)
            os.close(fd)

    def test_ar1_archive_inspect_extract_round_trip(self):
        result = pf_source.archive_tree(self.tree, self.base / "a.tar.gz")
        inventory, extracted = self.imported(self.base / "a.tar.gz")
        self.assertEqual(extracted["entries"], result["manifest"]["entries"])
        self.assertEqual((inventory.expanded_bytes, len(inventory.members), inventory.members_sha256),
                         (result["expanded_bytes"], result["members"], result["members_sha256"]))
        self.assertTrue(next(entry for entry in extracted["entries"] if entry["path"] == "scripts/tool.sh")["executable"])

    def test_ar2_record_mode_records_a_link_without_following_it(self):
        os.symlink("/etc/passwd", str(self.tree / "passwd-link"))
        with self.assertRaisesRegex(pf_source.SourceError, "unsupported entry"):
            pf_source.archive_tree(self.tree, self.base / "refused.tar.gz")
        self.assertFalse((self.base / "refused.tar.gz").exists())
        result = pf_source.archive_tree(self.tree, self.base / "b.tar.gz", unsupported="record")
        self.assertEqual(result["unsupported"], ["passwd-link"])
        with tarfile.open(self.base / "b.tar.gz") as archive:
            self.assertNotIn("passwd-link", archive.getnames())

    def test_ar3_verified_tree_counters_equal_the_inspector(self):
        manifest = pf_source.build_manifest(self.tree, source={"kind": "unknown"})
        count, expanded, members_sha256 = pf_source.archive_verified_tree(self.tree, manifest, self.base / "c.tar.gz")
        inventory, _ = self.imported(self.base / "c.tar.gz")
        self.assertEqual((count, expanded, members_sha256),
                         (len(inventory.members), inventory.expanded_bytes, inventory.members_sha256))

    def test_ar4_ar5_modes_owner_and_implied_parents(self):
        manifest = pf_source.build_manifest(self.tree, source={"kind": "unknown"})
        pf_source.archive_verified_tree(self.tree, manifest, self.base / "d.tar.gz")
        inventory, _ = self.imported(self.base / "d.tar.gz")
        self.assertTrue(all(kind == "file" for _, kind, _, _ in inventory.members))  # parents implied, uncounted
        self.assertEqual(len(inventory.members), len(manifest["entries"]))
        for current, dirs, files in os.walk(str(self.base / "out")):
            for name in dirs + files:
                info = os.lstat(os.path.join(current, name))
                self.assertIn(stat.S_IMODE(info.st_mode), (0o600, 0o700), name)
                self.assertEqual(info.st_uid, 0)
                if name in dirs:
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)

    def test_ar6_a_v2_5_archive_of_a_setgid_workspace_extracts(self):
        for current, dirs, files in os.walk(str(self.tree)):
            os.chmod(current, 0o2770)
            for name in files:
                path = os.path.join(current, name)
                os.chmod(path, 0o770 if os.stat(path).st_mode & 0o100 else 0o660)
        # The A2.3 create_source_archive: tarfile.add per top-level item keeps the on-disk modes.
        legacy = self.base / "legacy.tar.gz"
        legacy.write_bytes(pfx.tar_gz_bytes(self.tree))
        inventory, extracted = self.imported(legacy)
        self.assertTrue(any(kind == "dir" for _, kind, _, _ in inventory.members))
        self.assertEqual(pf.migration_files(self.base / "out"), pf.migration_files(self.tree))
        self.assertEqual(stat.S_IMODE(os.lstat(str(self.base / "out" / "backend")).st_mode), 0o700)

    def test_ar7_non_ascii_names_keep_one_serializer_per_hash(self):
        (self.tree / "docs").mkdir()
        (self.tree / "docs" / "ghi-chú-đầu-tiên.md").write_text("tiếng Việt\n", encoding="utf-8")
        source = pf_source.build_manifest(self.tree, source={"kind": "unknown"})
        pf_source.archive_tree(self.tree, self.base / "e.tar.gz")
        _, extracted = self.imported(self.base / "e.tar.gz")
        self.assertEqual(pf_source.entries_digest(extracted), pf_source.entries_digest(source))
        self.assertEqual(pf_source.entries_digest(source),
                         hashlib.sha256(pf_source.manifest_bytes(source["entries"])).hexdigest())
        # The ASCII-escaped pf_source serializer and normalize_json give different bytes for this tree.
        self.assertNotEqual(pf_source.manifest_bytes(source["entries"]), pf_instance.normalize_json(source["entries"]))


# ============================================================================ DA: deployed-source artifacts (A3-T02)


class Events:
    """Record the order in which wrapped controller methods run (call-order assertions)."""

    def __init__(self, controller, *names):
        self.order = []
        self.patches = []
        for name in names:
            original = getattr(controller, name)

            def wrapper(*args, _name=name, _original=original, **kwargs):
                self.order.append(_name)
                return _original(*args, **kwargs)

            self.patches.append(mock.patch.object(controller, name, side_effect=wrapper))

    def __enter__(self):
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self.patches:
            patch.stop()
        return False


@ROOT_FS
class DeployedArtifact(Base):
    """DA-1..DA-16 (A3-T02): the deployed-source artifact store, its seal, pointer and readers."""

    def update(self, sha="3" * 40, *, code=0):
        self.c.target = {"sha": sha, "ref": "v0.2." + sha[:3], "release_id": 3}
        self.assertEqual(self.invoke(["update", "--latest"]), code, self.last_error if code == 0 else None)
        return self.c.current_deployment()

    def tamper(self, view, name, data=b"tampered"):
        path = view.folder / name
        os.chmod(str(path), 0o600)
        with path.open("ab") as stream:
            stream.write(data)

    def test_da1_deploy_seals_five_private_files_and_the_exact_pointer(self):
        view = self.deploy()
        self.assertIsNone(view.mismatch)
        folder = self.c.deployments_dir / view.deployment_id
        self.assertEqual(sorted(os.listdir(str(folder))), ["compose-resolved.json", "config.env",
                                                           "deployment-record.json", "source-manifest.json",
                                                           "source.tar.gz"])
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        for item in folder.iterdir():
            self.assertEqual((stat.S_IMODE(item.stat().st_mode), item.stat().st_uid), (0o600, 0), item.name)
        record = view.record
        self.assertEqual(pf.lifecycle_errors(record, "deployment_record"), [])
        self.assertEqual(record["strategy"], {"id": "postgresql-logical", "version": 1})
        self.assertEqual({service: image["platform"] for service, image in record["images"].items()},
                         {"backend": "linux/amd64", "frontend": "linux/amd64", "db": "linux/amd64"})
        self.assertEqual(record["images"]["db"]["id"], tpa.DB_IMAGE_ID)
        self.assertEqual((record["operation"]["kind"], record["previous_deployment_id"]), ("deploy", None))
        self.assertEqual((record["source"]["provenance"], record["source"]["commit"]), ("git_commit", NEW))
        self.assertEqual(record["database"]["alembic_heads"], ["r1"])
        pointer = self.pointer()
        self.assertEqual(set(pointer), {"sha", "ref", "release_id", "deployed_at", "checkpoint", "initial_deploy",
                                        "database_heads", "deployment_id", "deployment_record_sha256"})
        self.assertEqual((pointer["sha"], pointer["deployment_id"]), (NEW, view.deployment_id))
        self.assertEqual(pointer["deployment_record_sha256"],
                         hashlib.sha256((folder / "deployment-record.json").read_bytes()).hexdigest())
        operation = sorted(self.context.operations_dir.iterdir())[-1]
        audit = json.loads((operation / "deployment-artifact.json").read_text())
        self.assertEqual((audit["state"], audit["deployment_id"]), ("sealed", view.deployment_id))
        with tarfile.open(folder / "source.tar.gz") as archive:
            self.assertTrue(all(member.isfile() for member in archive.getmembers()))
            self.assertEqual(sorted(archive.getnames()),
                             sorted(entry["path"] for entry in json.loads(
                                 (folder / "source-manifest.json").read_bytes())["entries"]))

    def test_da2_update_and_rollback_chain_their_records(self):
        first = self.deploy()
        second = self.update()
        self.assertEqual((second.record["operation"]["kind"], second.record["previous_deployment_id"]),
                         ("update", first.deployment_id))
        checkpoint = self.c.snapshots()[0]
        self.assertEqual((checkpoint.reason, checkpoint.manifest["deployment"]["deployment_id"]),
                         ("before-update", first.deployment_id))
        self.assertEqual(self.invoke(["rollback", checkpoint.bundle_id]), 0, self.last_error)
        third = self.c.current_deployment()
        self.assertEqual((third.record["operation"]["kind"], third.record["previous_deployment_id"]),
                         ("rollback", second.deployment_id))
        self.assertEqual((third.record["source"]["provenance"], third.record["source"]["commit"]), ("git_commit", NEW))
        self.assertIsNone(third.record["restored_from"])
        self.assertEqual(self.pointer()["ref"], "rollback:" + checkpoint.bundle_id)
        self.assertEqual(self.c.deployment_inventory(third), (0, 0))

    def test_da3_a_tracked_export_ignore_file_is_in_the_store_export_record_and_archive(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed in this image")
        upstream = Path(self.temp.name) / "upstream"
        pfx.source_fixture(upstream)
        (upstream / ".gitattributes").write_text("secret-notes.txt export-ignore\n")
        (upstream / "secret-notes.txt").write_text("tracked although git archive would omit it\n")

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=upstream, stderr=subprocess.DEVNULL).decode().strip()

        git("init", "-q")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("add", ".")
        git("commit", "-qm", "export-ignore fixture")
        commit = git("rev-parse", "HEAD")
        controller = pf.Controller(self.context)
        controller.remote_override = str(upstream)
        controller.source_protocols = ("file",)
        candidate = Path(self.temp.name) / "candidate"
        with mock.patch.object(controller, "compose", return_value=""), controller.lock():
            controller.materialize_source({"sha": commit}, candidate)
            manifest = controller.candidate_manifest(candidate, commit, verified=True)
            staged = controller.stage_deployment(candidate, manifest, kind="deploy", ref="main")
        self.assertIn("secret-notes.txt", [entry["path"] for entry in manifest["entries"]])
        with tarfile.open(staged.staging / "source.tar.gz") as archive:
            self.assertIn("secret-notes.txt", archive.getnames())
        recorded = json.loads((staged.staging / "source-manifest.json").read_bytes())
        self.assertIn("secret-notes.txt", [entry["path"] for entry in recorded["entries"]])
        self.assertEqual(recorded["source"], {"kind": "git_commit", "commit": commit, "remote": str(upstream)})
        self.assertEqual(staged.source["manifest"]["entries_sha256"], pf_source.entries_digest(manifest))

    def test_da4_backup_after_workspace_and_git_loss_takes_the_exact_source_from_the_artifact(self):
        view = self.deploy()
        (self.root / ".git").mkdir()
        for item in list(self.root.iterdir()):
            shutil.rmtree(str(item)) if item.is_dir() else item.unlink()
        self.assertEqual(self.invoke(["backup"]), 0, self.last_error)
        checkpoint = self.c.snapshots()[0]
        source = checkpoint.payload("source.tar.gz")
        self.assertEqual((source["size"], source["sha256"]), (view.record["source"]["archive"]["size"],
                                                              view.record["source"]["archive"]["sha256"]))
        self.assertEqual(checkpoint.manifest["source"], {
            "provenance": "git_commit", "commit": NEW, "remote": view.record["source"]["remote"],
            "origin": "deployment-artifact", "payload": "source.tar.gz",
            "entries_sha256": view.record["source"]["manifest"]["entries_sha256"]})
        self.assertEqual(checkpoint.manifest["deployment"], {"deployment_id": view.deployment_id,
                                                             "record_sha256": view.record_sha256})
        self.assertIn("Checkpoint class: healthy_checkpoint", self.output.getvalue())
        self.assertIn("Verification level: data_restore_verified", self.output.getvalue())

    def test_da5_a_tampered_artifact_without_a_provable_source_is_refused_before_anything(self):
        view = self.deploy()
        self.tamper(view, "source.tar.gz")
        confirm = mock.Mock()
        calls = len(self.c.calls)
        with mock.patch.object(self.c, "deployed_source_origin", return_value=None):
            for arguments in (["update", "--latest"], ["backup"]):
                with self.subTest(arguments=arguments):
                    self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
                    self.assertEqual(self.invoke(arguments, confirm=confirm), 1)
                    self.assertIn(f"ERROR: deployment-artifact-mismatch: {view.deployment_id}: source.tar.gz differs "
                                  "from the deployment record, and the deployed source cannot be proven from the "
                                  "protected source store or the workspace either. Keep the folder as evidence; see "
                                  "SYNOLOGY_ADMIN §16. Nothing was changed.", self.last_error)
        confirm.assert_not_called()
        # PF-A3.2: only the completed first deployment has an operation; neither refused route wrote a plan.
        self.assertEqual([(plan["kind"], journal["phase"]) for _, plan, journal in pfx.operations_of(self.context)],
                         [("deploy", "completed")])
        self.assertEqual(self.c.snapshots(), [])
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and call[1][0] == "stop"])
        self.assertTrue(all(self.c.running.values()))
        self.assertTrue((view.folder / "source.tar.gz").exists())  # kept as evidence

    def test_da6a_a_repointed_retained_tag_refuses_the_rollback(self):
        checkpoint = self.checkpoint()
        self.c.tags[checkpoint.images["backend"]["reference"]] = tpa.NEW_BACKEND
        self.assertEqual(self.invoke(["rollback", checkpoint.bundle_id]), 1)
        self.assertIn("A retained image is missing or changed. Rollback refuses an unverified rebuild.",
                      self.last_error)
        self.assertEqual(pfx.operations_of(self.context, "rollback"), [])

    def test_da6b_a_legacy_checkpoint_is_compared_with_its_legacy_image_ids(self):
        tree = Path(self.temp.name) / "legacy-tree"
        pfx.source_fixture(tree)
        backup_id = "20260901T120000Z-" + OLD[:12] + "-a1b2c3"
        pfx.legacy_checkpoint(self.c.backups_dir / backup_id, project=PROJECT, tree=tree)
        view = self.c.verify_snapshot(backup_id)
        for service, image in view.images.items():
            self.c.tags[image["reference"]] = image["id"]
        self.c.tags[view.images["backend"]["reference"]] = tpa.NEW_BACKEND  # re-pointed after the capture
        self.assertEqual(self.invoke(["rollback", backup_id]), 1)
        self.assertIn("A retained image is missing or changed", self.last_error)

    def seal_failure(self, flow):
        failure = pf.Failure("no approved resolved Compose render of the activation's inputs was recorded")
        return mock.patch.object(self.c, "_seal_compose", side_effect=failure)

    def assert_not_recorded(self, *, calls_before):
        self.assertIn("ERROR: deployment-record-incomplete: the application was activated and passed health checks, "
                      "but its deployment record could not be sealed (no approved resolved Compose render of the "
                      "activation's inputs was recorded). The operation was closed and the deployment is treated as "
                      "one without a record; the next deploy, update, rollback or restore-instance seals one. Run "
                      "'pf --instance staging status'.", self.last_error)
        # PF-A3.2: the operation closed failed_preserved (not blocking); the workspace was still refreshed.
        self.assertEqual(self.open_ops(), [])
        _, plan, journal = tpa.latest_operation(self.c)
        self.assertEqual((journal["phase"], journal["last_error"]["code"]),
                         ("failed_preserved", "deployment-record-incomplete"))
        self.assertEqual(list(tpa.effect_states(journal, plan, "artifact-seal").values()), ["partial"])
        self.assertTrue(all(self.c.running.values()))  # fail_closed never ran: the healthy application keeps running
        pointer = self.pointer()
        self.assertNotIn("deployment_id", pointer)
        self.assertTrue(pf.OPERATION_ID_RE.fullmatch(pointer["deployment_seal_failed"]))
        self.assertTrue(self.c.describe_deployment().startswith(
            f"not recorded (seal failed in operation {pointer['deployment_seal_failed']}; the next "
            "deploy/update/rollback seals one)"))
        operation = self.context.operations_dir / pointer["deployment_seal_failed"]
        self.assertEqual(json.loads((operation / "deployment-artifact.json").read_text())["state"], "seal-failed")

    def test_da7a_b_seal_failure_after_deploy_or_update_is_non_wedging(self):
        tpa.write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        self.c.dbs, self.c.running = {}, {"db": False, "backend": False, "frontend": False}
        with self.seal_failure("deploy"):
            self.assertEqual(self.invoke(["deploy", "--latest"]), 1)
        self.assert_not_recorded(calls_before=0)
        self.assertEqual(self.pointer()["sha"], NEW)
        self.assertEqual(self.invoke(["backup"]), 0, self.last_error)  # the proven commit is captured as before
        self.assertEqual(self.c.snapshots()[0].manifest["source"]["origin"], "protected-store")
        calls = len(self.c.calls)
        with self.seal_failure("update"):
            self.update(code=1)
        self.assert_not_recorded(calls_before=calls)
        self.assertEqual(self.invoke(["backup"]), 0, self.last_error)
        # The next deployment seals a record again.
        sealed = self.update("4" * 40)
        self.assertIsNone(sealed.mismatch)

    def test_da7c_db_identity_failure_at_restore_seal_is_non_wedging(self):
        bundle = PurgeBundle.legacy(self)
        original = self.c.image_identity
        sealing = {"on": False}
        real_seal = self.c.seal_deployment

        def seal(staged, **kwargs):
            sealing["on"] = True
            return real_seal(staged, **kwargs)

        def identity(image, *, reference):
            if sealing["on"] and reference == "postgres:16":
                raise pf.Failure("docker image inspect: no such image")
            return original(image, reference=reference)

        with mock.patch.object(self.c, "seal_deployment", side_effect=seal), \
                mock.patch.object(self.c, "image_identity", side_effect=identity):
            self.assertEqual(PurgeBundle.restore(self, bundle), 1)
        self.assertIn("deployment-record-incomplete: the application was activated and passed health checks, but its "
                      "deployment record could not be sealed (docker image inspect: no such image)", self.last_error)
        self.assertEqual(self.open_ops(), [])
        self.assertTrue(all(self.c.running.values()))
        self.assertIn("deployment_seal_failed", self.pointer())
        text = self.c.describe_deployment()
        self.assertTrue(text.startswith("not recorded"))
        # The unsealed staging of the running deployment is reported and kept (section 3.13: never swept).
        self.assertIn("Unsealed deployment staging: 1", text)

    def test_da8_a_crash_between_seal_and_pointer_leaves_an_unreferenced_deployment(self):
        first = self.deploy()
        real = pf.write_json

        def failing(path, value):
            if Path(path).name == "deployed.json":
                raise OSError(28, "No space left on device")
            return real(path, value)

        with mock.patch.object(pf, "write_json", side_effect=failing):
            self.update(code=1)
        # PF-A3.2 RS-14: the pointer effect stays open in activating; no compose stop (activation completed).
        self.assertEqual(self.open_ops(), [("update", "activating")])
        self.assertTrue(all(self.c.running.values()))
        self.assertEqual(self.c.current_deployment().deployment_id, first.deployment_id)
        self.assertEqual(len([name for name in self.deployments() if pf.DEPLOYMENT_ID_RE.fullmatch(name)]), 2)
        text = self.c.describe_deployment()
        self.assertIn("Unreferenced deployments: 1", text)
        # `resume` rewrites the pointer (closes the A3.1 pointer-route gap) and refreshes the workspace.
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertEqual(self.open_ops(), [])
        self.assertNotEqual(self.c.current_deployment().deployment_id, first.deployment_id)

    def test_da9_artifact_capacity_is_refused_before_the_confirmation(self):
        checkpoint = self.checkpoint()
        dbs = json.dumps(self.c.dbs, sort_keys=True)
        for arguments in (["update", "--latest"], ["rollback", checkpoint.bundle_id]):
            with self.subTest(arguments=arguments):
                confirm = mock.Mock()
                calls = len(self.c.calls)
                # Only the deployment artifact store is full; an extraction elsewhere still has room.
                with mock.patch.object(self.c, "artifact_free_bytes",
                                       side_effect=lambda path: 0 if "artifacts" in str(path) else 1 << 40):
                    self.assertEqual(self.invoke(arguments, confirm=confirm), 1)
                self.assertRegex(self.last_error, r"ERROR: artifact-capacity: [0-9]+ MiB needed in .*/artifacts, "
                                                  r"0 MiB free\. Nothing was changed\.")
                confirm.assert_not_called()
                self.assertEqual(self.open_ops(), [])
                self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and call[1][0] == "stop"])
                self.assertEqual(len(self.c.snapshots()), 1)
                self.assertEqual(self.deployments(), [])
                self.assertEqual(json.dumps(self.c.dbs, sort_keys=True), dbs)
        tpa.write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        confirm = mock.Mock()
        with mock.patch.object(self.c, "artifact_free_bytes", return_value=0):
            self.assertEqual(self.invoke(["deploy", "--latest"], confirm=confirm), 1)
        self.assertIn("ERROR: artifact-capacity: ", self.last_error)
        confirm.assert_not_called()
        self.assertEqual(pfx.operations_of(self.context, "deploy"), [])

    def test_da9d_a_staging_failure_after_the_confirmation_changes_nothing(self):
        confirm = mock.Mock()
        calls = len(self.c.calls)
        with mock.patch.object(pf.pf_source, "archive_verified_tree", side_effect=OSError(5, "Input/output error")):
            self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
            self.assertEqual(self.invoke(["update", "--latest"], confirm=confirm), 1)
        # PF-A3.2 (DA-9d updated): the operation is closed cancelled and its own staging removed.
        operation, _, journal = tpa.latest_operation(self.c, "update")
        self.assertIn("ERROR: deployment-stage-failed: [Errno 5] Input/output error. The application, database and "
                      f"workspace were not changed; operation {operation} was closed (cancelled) and its staging "
                      "removed.", self.last_error)
        confirm.assert_called_once()
        self.assertEqual(journal["phase"], "cancelled")
        self.assertEqual(self.open_ops(), [])
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and call[1][0] == "stop"])
        self.assertTrue(all(self.c.running.values()))
        self.assertEqual(self.c.revision(), OLD)
        self.assertNotIn("Unsealed deployment staging", self.c.describe_deployment())

    def test_da10_secrets_stay_in_private_state(self):
        self.deploy()
        secret = b"a" * 64  # write_deploy_env's POSTGRES_PASSWORD
        view = self.c.current_deployment()
        self.assertIn(secret, (view.folder / "config.env").read_bytes())
        self.assertEqual(stat.S_IMODE((view.folder / "config.env").stat().st_mode), 0o600)
        self.assertEqual(self.invoke(["backup"]), 0, self.last_error)
        checkpoint = self.c.snapshots()[0]
        for path in checkpoint.folder.iterdir():
            data = path.read_bytes()
            self.assertNotIn(secret, data, path.name)
            if path.name.endswith(".tar.gz"):
                with tarfile.open(path) as archive:
                    self.assertNotIn(".env", [Path(name).name for name in archive.getnames()])
                    for member in archive.getmembers():
                        self.assertNotIn(secret, archive.extractfile(member).read(), member.name)
        self.assertFalse({"config_env", "compose_resolved", "deployment_record"}
                         & {item["type"] for item in checkpoint.manifest["payloads"]})
        self.assertNotIn(secret.decode(), self.output.getvalue() + self.error_output.getvalue())

    def test_da11_a_tampered_artifact_with_a_provable_commit_still_updates_and_seals_a_fresh_record(self):
        first = self.deploy()
        self.tamper(first, "config.env")
        second = self.update()
        self.assertIn(f"note: deployment-artifact-mismatch: {first.deployment_id}: config.env differs from the "
                      "deployment record; it is kept as evidence and not used. The checkpoint takes the source from "
                      "the protected source store.", self.output.getvalue())
        checkpoint = [item for item in self.c.snapshots() if item.reason == "before-update"][0]
        self.assertIsNone(checkpoint.manifest["deployment"])
        exclusion = next(item for item in checkpoint.manifest["exclusions"] if item["item"] == "deployment-record")
        self.assertEqual(exclusion["reason"], f"deployment {first.deployment_id}: config.env differs from its record; "
                                              "kept as evidence")
        self.assertEqual(checkpoint.manifest["source"]["origin"], "protected-store")
        self.assertIsNone(second.mismatch)
        self.assertNotEqual(second.deployment_id, first.deployment_id)
        self.assertEqual(second.record["previous_deployment_id"], first.deployment_id)
        self.assertTrue(first.folder.exists())

    def test_da12_a_non_ascii_path_keeps_one_tree_identity_through_backup_and_rollback(self):
        real = self.c.materialize_source

        def materialize(target, destination):
            real(target, destination)
            (Path(destination) / "docs").mkdir(exist_ok=True)
            (Path(destination) / "docs" / "ghi-chú.md").write_text("tiếng Việt\n", encoding="utf-8")

        with mock.patch.object(self.c, "materialize_source", side_effect=materialize):
            first = self.deploy()
            self.assertEqual(self.invoke(["backup"]), 0, self.last_error)
            checkpoint = self.c.snapshots()[0]
            self.update()
        self.assertEqual(checkpoint.manifest["source"]["entries_sha256"],
                         first.record["source"]["manifest"]["entries_sha256"])
        self.assertEqual(self.invoke(["rollback", checkpoint.bundle_id]), 0, self.last_error)
        rolled = self.c.current_deployment()
        self.assertEqual(rolled.record["source"]["manifest"]["entries_sha256"],
                         first.record["source"]["manifest"]["entries_sha256"])
        self.assertEqual((self.root / "docs" / "ghi-chú.md").read_text(encoding="utf-8"), "tiếng Việt\n")

    def legacy_rollback(self, revision):
        tree = Path(self.temp.name) / "legacy-tree"
        pfx.source_fixture(tree)
        backup_id = "20260901T120000Z-" + revision[:12] + "-a1b2c3"
        pfx.legacy_checkpoint(self.c.backups_dir / backup_id, project=PROJECT, tree=tree,
                              extra={"source_revision": revision})
        for image in self.c.verify_snapshot(backup_id).images.values():
            self.c.tags[image["reference"]] = image["id"]
        self.assertEqual(self.invoke(["rollback", backup_id]), 0, self.last_error)
        return self.c.current_deployment()

    def test_da13_a_legacy_claim_proven_by_the_store_deploys_as_a_commit(self):
        view = self.legacy_rollback(OLD)
        self.assertEqual((view.record["source"]["provenance"], view.record["source"]["commit"]), ("git_commit", OLD))
        self.assertEqual(self.pointer()["sha"], OLD)
        self.assertEqual(self.c.deployed_commit(), OLD)
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        self.assertEqual(self.c.current_deployment().record["source"]["commit"], NEW)

    def test_da14_an_unprovable_legacy_claim_is_unknown_and_only_a_manual_update_proceeds(self):
        view = self.legacy_rollback("9" * 40)
        self.assertEqual((view.record["source"]["provenance"], view.record["source"]["commit"]), ("unknown", None))
        self.assertIsNone(self.pointer()["sha"])
        self.assertIsNone(self.c.deployed_commit())
        self.c.config["auto_update"] = True
        with self.c.lock("release-check"):
            with self.assertRaisesRegex(pf.Deferred, "^Automatic update refuses a deployment whose source commit is "
                                                     "unknown; run a manual update.$"):
                self.c.update(self.c.target, automatic=True)
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        checkpoint = [item for item in self.c.snapshots() if item.reason == "before-update"][0]
        self.assertEqual((checkpoint.manifest["source"]["origin"], checkpoint.manifest["source"]["provenance"]),
                         ("deployment-artifact", "unknown"))
        self.assertTrue(checkpoint.bundle_id.split("-")[1] == "0" * 12)

    def test_da14_release_check_reports_an_unknown_deployed_source(self):
        # Audit AF-2: the check-only command reads the deployed commit like update does, never the strict revision().
        self.legacy_rollback("9" * 40)
        self.assertEqual(self.invoke(["release-check"]), 0, self.last_error)
        self.assertIn("Deployed source: unknown provenance (no proven commit is recorded)", self.output.getvalue())
        self.assertIn("Check-only. No code or database changes were made.", self.output.getvalue())

    def test_da17_an_a23_unproven_rollback_pointer_is_not_a_proven_commit(self):
        # Audit AF-3: an A2.3 rollback wrote the checkpoint's claim into deployed.json even when the store did not
        # prove the tree (its protected source manifest stayed `unknown`); after the upgrade it is no commit.
        backup_id = "20260901T120000Z-" + OLD[:12] + "-a1b2c3"
        pf.write_json(self.c.state / "deployed.json", {"sha": OLD, "ref": "rollback:" + backup_id,
                                                       "deployed_at": "20260901T130000Z",
                                                       "checkpoint": "20260901T125900Z-" + OLD[:12] + "-b2c3d4"})
        self.c.record_source_manifest(self.root, OLD, verified=False)
        self.assertIsNone(self.c.deployed_commit())
        self.assertEqual(self.c.describe_deployed_source(), "unknown provenance (no proven commit is recorded)")
        self.assertEqual(self.invoke(["backup"]), 1)
        self.assertIn(pf.Controller.CANNOT_RECONSTRUCT, self.last_error)
        self.assertEqual([item for item in self.c.snapshots()], [])
        # The same pointer whose tree the protected manifest records as that commit stays a proven commit, and an
        # update pointer keeps its sha as before.
        self.c.record_source_manifest(self.root, OLD, verified=True)
        self.assertEqual(self.c.deployed_commit(), OLD)
        self.c.record_source_manifest(self.root, OLD, verified=False)
        pf.write_json(self.c.state / "deployed.json", {"sha": OLD, "ref": "v1.0.0"})
        self.assertEqual(self.c.deployed_commit(), OLD)

    def test_da15_restore_instance_then_update(self):
        bundle = PurgeBundle.legacy(self)
        self.assertEqual(PurgeBundle.restore(self, bundle), 0, self.last_error)
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        record = self.c.current_deployment().record
        self.assertEqual((record["operation"]["kind"], record["source"]["commit"]), ("update", NEW))

    def test_da16_the_seal_selects_the_render_by_its_full_input_key_and_re_hashes_it(self):
        # PF-A3.2: the last activation step is the frontend effect (the A3.1 activate() split).
        real_activate = self.c.activate_frontend

        def activate_then_decoy(images):
            real_activate(images)
            decoy = {"name": "decoy", "services": {}}
            (self.c.operation_dir / "compose-99.json").write_bytes(pf_instance.normalize_json(decoy))
            self.c._append_envelope_record({
                "sequence": 99, "compose_version": "decoy",
                "inputs": dict(json.loads((self.c.operation_dir / "compose-envelope.json").read_bytes())["renders"][-1]
                               ["inputs"], override_sha256="f" * 64),
                "resolved_file": "compose-99.json",
                "resolved_sha256": pf_instance.sha256_bytes(pf_instance.normalize_json(decoy)), "escape_mode": "doubled",
                "result": "approved"})

        with mock.patch.object(self.c, "activate_frontend", side_effect=activate_then_decoy):
            view = self.deploy()
        operation = sorted(self.context.operations_dir.iterdir())[-1]
        renders = json.loads((operation / "compose-envelope.json").read_bytes())["renders"]
        selected = [render for render in renders if render["resolved_file"] != "compose-99.json"][-1]
        copied = (view.folder / "compose-resolved.json").read_bytes()
        self.assertEqual(copied, (operation / selected["resolved_file"]).read_bytes())
        self.assertEqual(view.record["compose"]["file_sha256"], hashlib.sha256(copied).hexdigest())
        self.assertEqual(view.record["compose"]["model_sha256"], selected["resolved_sha256"])
        self.assertNotEqual(view.record["compose"]["model_sha256"], renders[-1]["resolved_sha256"])

        def activate_then_tamper(images):
            real_activate(images)
            name = json.loads((self.c.operation_dir / "compose-envelope.json").read_bytes())["renders"][-1][
                "resolved_file"]
            (self.c.operation_dir / name).write_bytes(b'{"name":"changed"}')

        with mock.patch.object(self.c, "activate_frontend", side_effect=activate_then_tamper):
            self.update(code=1)
        self.assertIn("deployment-record-incomplete: the application was activated and passed health checks, but its "
                      "deployment record could not be sealed (the recorded resolved Compose model does not re-hash "
                      "to its approval)", self.last_error)


# ============================================================================ EP: emergency preservation (A3-T03)


@ROOT_FS
class Emergency(Base):
    """A3-T03 offline part: DB heads r2, a stopped backend container on the r1 image, an interrupted update."""

    def mismatch(self, *, pending=True, rows=("old-record", "r2-only-record")):
        self.c.dbs["partflow_staging"]["heads"] = ["r2"]
        self.c.dbs["partflow_staging"]["rows"] = list(rows)
        self.c.running["backend"] = False
        if pending:
            # PF-A3.2: an update interrupted in its backend start (a real plan and journal).
            self.update_plan = self.interrupted_update()

    def test_ep1_backup_emergency_captures_the_mismatch_and_leaves_the_journal(self):
        self.mismatch()
        before = pfx.operation_files(self.context, self.update_plan["operation_id"])
        confirm = mock.Mock()
        self.assertEqual(self.invoke(["backup", "--emergency"], confirm=confirm), 0, self.last_error)
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[0], "EMERGENCY BACKUP partflow-staging")
        # RT-3/RO-5: the capture is journal-less and never touches the interrupted update's files.
        self.assertEqual(pfx.operation_files(self.context, self.update_plan["operation_id"]), before)
        self.assertEqual([name for name, plan, _ in pfx.operations_of(self.context)],
                         [self.update_plan["operation_id"]])
        view = self.c.snapshots()[0]
        self.assertEqual((view.capture_class, view.reason, view.level),
                         ("emergency_preservation", "emergency-manual", "data_restore_verified"))
        mismatch = view.manifest["compatibility"]["mismatch"]
        self.assertEqual((mismatch["kind"], mismatch["live_heads"], mismatch["image_heads"],
                          mismatch["backend_image_id"]), ("schema-image-mismatch", ["r2"], ["r1"], tpa.OLD_BACKEND))
        for service in ("backend", "frontend"):
            self.assertIn(f"{PROJECT}-{service}:backup-{view.bundle_id.lower()}", self.c.tags)
        out = self.output.getvalue()
        self.assertIn("Observed contract: live heads r2 | image heads r1", out)
        self.assertIn(f"Emergency preservation {view.bundle_id} captured (data_restore_verified). It is evidence and "
                      "data for repair or export, not a rollback target.", out)

    def test_ep2_running_writers_give_a_single_store_snapshot_without_row_counts(self):
        self.assertEqual(self.invoke(["backup", "--emergency"]), 0, self.last_error)
        view = self.c.snapshots()[0]
        self.assertEqual(view.manifest["quiescence"]["mode"], "single_store_snapshot")
        self.assertIsNone(view.active_store["row_counts"])
        rows = next(check for check in self.records(view.bundle_id)[0]["checks"] if check["name"].startswith("rows:"))
        self.assertEqual(rows["result"], "not_run")

    def test_ep3_ep4_rollback_restore_db_preserves_first_and_the_preservation_is_never_a_target(self):
        target = self.checkpoint()
        self.mismatch()
        with Events(self.c, "_capture", "restore_candidate", "swap_database") as events:
            self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 0, self.last_error)
        self.assertEqual(events.order, ["_capture", "restore_candidate", "swap_database"])
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r1"])
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        kept = [name for name in self.c.dbs if name.startswith("pf_keep_")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.c.dbs[kept[0]]["rows"], ["old-record", "r2-only-record"])
        preserved = next(item for item in self.c.snapshots() if item.reason == "before-rollback")
        self.assertEqual((preserved.capture_class, preserved.level), ("emergency_preservation", "data_restore_verified"))
        self.assertEqual(preserved.manifest["quiescence"]["mode"], "writers_stopped")
        self.assertIsNotNone(preserved.active_store["row_counts"])
        self.assertIn(b"r2-only-record", (preserved.folder / "database.dump").read_bytes())
        self.assertEqual(self.pointer()["checkpoint"], preserved.bundle_id)
        self.assertEqual(self.invoke(["rollback", preserved.bundle_id]), 1)
        self.assertIn(f"ERROR: checkpoint-not-rollback-target: {preserved.bundle_id} is a emergency_preservation "
                      "capture: evidence and data for repair or export, never a rollback target. Nothing was changed.",
                      self.last_error)

    def preservation_failed(self, failure):
        target = self.checkpoint()
        self.mismatch()
        dbs = json.dumps(self.c.dbs, sort_keys=True)
        self.c.fail = failure
        with Events(self.c, "restore_candidate", "swap_database", "w1_stage") as events:
            self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 1)
        self.assertEqual(events.order, [])
        self.assertIn("ERROR: preservation-failed: the current database partflow_staging could not be preserved (",
                      self.last_error)
        self.assertIn("); the rollback did not restore or switch anything and the current data is unchanged. "
                      "Application services stay stopped. Preserve it manually (a pg_dump of partflow_staging to a "
                      "protected location) or fix the cause and run 'pf --instance staging backup --emergency', then "
                      "retry; 'pf --instance staging resume' reopens the unchanged deployment.", self.last_error)
        current = json.loads(dbs)["partflow_staging"]
        self.assertEqual((self.c.dbs["partflow_staging"]["heads"], self.c.dbs["partflow_staging"]["rows"]),
                         (current["heads"], current["rows"]))
        self.assertFalse([name for name in self.c.dbs if name.startswith(("pf_keep_", "pf_restore_"))])
        self.assertFalse(self.c.running["backend"] or self.c.running["frontend"])
        # PF-A3.2: the rollback superseding the interrupted update stays open in preserving-current; resume (which
        # withdraws a superseding operation) is a recorded legal next command.
        operation, plan, journal = tpa.latest_operation(self.c, "rollback")
        self.assertEqual((plan["supersedes"], journal["phase"]), (self.update_plan["operation_id"],
                                                                  "preserving-current"))
        self.assertIn(f"pf --instance staging resume --operation {operation}", journal["legal_next"])

    def test_ep5_a_dump_failure_blocks_the_rollback(self):
        self.preservation_failed("dump")
        self.assertIn("could not be preserved (simulated dump failure)", self.last_error)

    def test_ep6_a_failed_restore_test_blocks_the_rollback(self):
        self.preservation_failed("restore")
        self.assertIn("Verification database retained.", self.last_error)

    def test_ep7_an_unidentifiable_image_is_partial_and_still_preserves_the_data(self):
        target = self.checkpoint()
        self.mismatch()
        real = self.c.retain_image

        def retain(service, backup_id):
            if service == "backend":
                raise pf.Failure("Expected exactly one existing backend container; initialize the stack first.")
            return real(service, backup_id)

        with mock.patch.object(self.c, "retain_image", side_effect=retain):
            self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 0, self.last_error)
        preserved = next(item for item in self.c.snapshots() if item.reason == "before-rollback")
        self.assertEqual((preserved.capture_class, preserved.level), ("partial", "data_restore_verified"))
        self.assertIsNone(preserved.image("backend"))
        self.assertIn("image:backend", {item["item"] for item in preserved.manifest["exclusions"]})

    def test_ep8_backup_emergency_needs_a_terminal_and_the_scheduler_never_passes_it(self):
        self.assertEqual(self.invoke(["--instance", "staging", "backup", "--emergency"], interactive=False), 1)
        self.assertIn("ERROR: terminal-required: 'backup --emergency' asks for a typed confirmation and cannot run "
                      "without a terminal (scheduled task, script, or ssh without -t). Run it interactively: sudo pf "
                      "--instance staging backup --emergency. Nothing was changed.", self.last_error)
        self.assertEqual(self.c.snapshots(), [])
        self.assertNotIn("emergency", (PACKAGE / "backup.sh").read_text())

    def test_ep9_a_healthy_backup_keeps_the_equality_gate(self):
        self.mismatch(pending=False)
        self.assertEqual(self.invoke(["backup"]), 1)
        self.assertIn("The live database is not at the deployed image's Alembic head.", self.last_error)
        self.assertEqual(self.c.snapshots(), [])

    def test_ep10_code_only_rollback_preserves_after_the_pause_and_before_the_source(self):
        target = self.checkpoint()
        with Events(self.c, "pause", "preserve_current", "w1_stage") as events:
            self.assertEqual(self.invoke(["rollback", target.bundle_id]), 0, self.last_error)
        self.assertEqual(events.order, ["pause", "preserve_current", "w1_stage"])
        self.c.fail = "dump"
        with Events(self.c, "w1_stage") as events:
            self.assertEqual(self.invoke(["rollback", target.bundle_id]), 1)
        self.assertIn("ERROR: preservation-failed: ", self.last_error)
        self.assertEqual(events.order, [])

    def test_ep11_a_candidate_image_under_the_old_pointer_is_a_deployment_image_mismatch(self):
        deployed = self.deploy()
        target = self.checkpoint()
        candidate = pfx.image_id("unrecorded-candidate")
        self.c.tags[PROJECT + "-backend:unrecorded"] = candidate
        self.c.contracts[candidate] = dict(self.c.contracts[tpa.NEW_BACKEND])
        self.c.current_images["backend"] = candidate
        calls = len(self.c.calls)
        self.assertEqual(self.invoke(["backup"]), 1)
        self.assertIn(f"ERROR: deployment-image-mismatch: the running backend image {candidate[7:19]} is not "
                      f"deployment {deployed.deployment_id}'s {tpa.NEW_BACKEND[7:19]}. A healthy checkpoint would bind "
                      "the wrong images. Nothing was changed.", self.last_error)
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "docker" and call[1][0] == "tag"])
        self.assertEqual(len(self.c.snapshots()), 1)
        self.assertEqual(self.invoke(["rollback", target.bundle_id]), 0, self.last_error)
        preserved = next(item for item in self.c.snapshots() if item.reason == "before-rollback")
        self.assertEqual(preserved.capture_class, "emergency_preservation")
        self.assertEqual(preserved.manifest["compatibility"]["mismatch"]["kind"], "deployment-image-mismatch")
        self.assertEqual(preserved.manifest["compatibility"]["mismatch"]["expected_backend_image_id"], tpa.NEW_BACKEND)

    def test_ep12_a_workspace_link_blocks_preservation_but_is_recorded_by_backup_emergency(self):
        target = self.checkpoint()
        os.symlink("/etc/passwd", str(self.root / "passwd-link"))
        self.c.workspace_dirty = True
        before = tree_hash(self.root)
        self.assertEqual(self.invoke(["rollback", target.bundle_id]), 1)
        self.assertIn("ERROR: preservation-failed: ", self.last_error)
        self.assertIn("passwd-link", self.last_error)
        self.assertEqual(tree_hash(self.root), before)
        self.assertTrue(os.path.islink(str(self.root / "passwd-link")))
        # PF-A3.2: the failed rollback stays open; an emergency capture is legal next to it.
        self.assertEqual(self.open_ops(), [("rollback", "preserving-current")])
        self.c.running.update(backend=True, frontend=True)
        self.assertEqual(self.invoke(["backup", "--emergency"]), 0, self.last_error)
        view = next(item for item in self.c.snapshots() if isinstance(item, pf.BundleView)
                    and item.reason == "emergency-manual")
        self.assertEqual(view.manifest["workspace"]["unsupported_entries"], ["passwd-link"])


    def big_workspace_file(self):
        """Audit AF-6: a drifted workspace holding one file over the 128 MiB archive limit (sparse: no disk use)."""
        with open(str(self.root / "big-export.sql"), "wb") as handle:
            handle.truncate(pf_source.SOURCE_LIMITS.file_bytes + 1)
        self.c.workspace_dirty = True

    def test_ep13_backup_emergency_preserves_the_data_when_the_workspace_exceeds_the_limits(self):
        self.big_workspace_file()
        self.assertEqual(self.invoke(["backup", "--emergency"]), 0, self.last_error)
        view = self.c.snapshots()[0]
        self.assertEqual((view.capture_class, view.level), ("emergency_preservation", "data_restore_verified"))
        self.assertIsNone(view.workspace_payload)
        self.assertTrue(view.manifest["workspace"]["differs_from_deployed"])
        reason = next(item["reason"] for item in view.manifest["exclusions"] if item["item"] == "workspace")
        self.assertIn("big-export.sql: the tree exceeds the archive size limits", reason)
        self.assertIn("database.dump", {item["path"] for item in view.manifest["payloads"]})

    def test_ep14_a_capture_the_workspace_limits_would_refuse_is_refused_before_any_pause(self):
        target = self.checkpoint()
        self.big_workspace_file()
        for command in (["rollback", target.bundle_id], ["backup"]):
            with self.subTest(command=command[0]):
                confirm = mock.Mock()
                self.assertEqual(self.invoke(command, confirm=confirm), 1)
                self.assertIn("ERROR: workspace-archive-limit: the editable workspace differs from the deployed source "
                              "and cannot be archived within the archive limits (big-export.sql: the tree exceeds the "
                              "archive size limits).", self.last_error)
                self.assertIn("Nothing was changed.", self.last_error)
                confirm.assert_not_called()
                self.assertEqual(pfx.operations_of(self.context), [])
                self.assertTrue(self.c.running["backend"] and self.c.running["frontend"])
                self.assertEqual([item.bundle_id for item in self.c.snapshots()], [target.bundle_id])

    def test_ep15_resume_names_a_healthy_target_after_an_emergency_preserved_rollback(self):
        # Audit AF-5: the rollback retains its preservation capture, here an emergency one (never a target). PF-A3.2:
        # the recorded routes name the operation's healthy target (its input bundle), never the preservation.
        target = self.checkpoint()
        self.mismatch()
        with mock.patch.object(self.c, "activate_backend", side_effect=pf.Failure("backend did not become healthy")):
            self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 1)
        operation, plan, journal = tpa.latest_operation(self.c, "rollback")
        preserved = self.c.verify_snapshot(next(item["name"] for item in journal["retained_artifacts"]
                                                if item["kind"] == "checkpoint"))
        self.assertEqual(preserved.capture_class, "emergency_preservation")
        self.assertIn(f"pf --instance staging rollback {target.bundle_id} --restore-db", journal["legal_next"])
        self.assertFalse([line for line in journal["legal_next"] if preserved.bundle_id in line])
        # `pf resume` continues the rollback forward (its database switch completed) and completes it.
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertEqual(self.open_ops(), [])
        self.assertEqual(pfx.operation(self.context, operation)[1]["phase"], "completed")


# ============================================================================ VR: verification records


@ROOT_FS
class Verification(Base):

    def test_vr1_vr2_the_record_lives_outside_the_bundle_and_binds_the_unchanged_manifest(self):
        view = self.checkpoint()
        records = self.records(view.bundle_id)
        self.assertEqual(len(records), 1)
        record = records[0]
        path = self.c.verifications_dir / view.bundle_id / (record["verification_id"] + ".json")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual((record["manifest_sha256"], record["level"], record["result"]),
                         (view.manifest_sha256, "data_restore_verified", "passed"))
        self.assertTrue(record["target"]["removed"])
        self.assertFalse([item for item in view.folder.iterdir() if item.name.startswith("ver-")])
        data = (view.folder / "manifest.json").read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), view.manifest_sha256)
        self.assertNotIn(b"data_restore_verified", data)
        self.assertEqual(pf.lifecycle_errors(record, "verification_record"), [])

    def test_vr3_vr4_invalid_or_foreign_records_are_ignored(self):
        view = self.checkpoint()
        record = self.records(view.bundle_id)[0]
        path = self.c.verifications_dir / view.bundle_id / (record["verification_id"] + ".json")
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(self.c.verify_snapshot(view.bundle_id).level, "captured")
        self.assertIn(f"note: verification-record-invalid: {path.name}: ", self.output.getvalue())
        path.write_bytes(pf_instance.normalize_json(dict(record, manifest_sha256="0" * 64)))
        self.assertEqual(self.c.verify_snapshot(view.bundle_id).level, "captured")

    def test_vr5_a_failed_restore_writes_a_failed_record(self):
        self.c.fail = "restore"
        with self.assertRaisesRegex(pf.Failure, r"Verification of .* failed \(restore:postgresql:partflow_staging: "
                                                r"simulated restore failure\)\. Verification database retained\."):
            self.checkpoint()
        view = self.c.snapshots()[0]
        self.assertEqual(view.level, "failed")
        record = self.records(view.bundle_id)[0]
        self.assertEqual((record["result"], record["target"]["removed"]), ("failed", False))
        self.assertTrue([name for name in self.c.dbs if name.startswith("pf_verify_")])
        self.c.display_page(self.c.snapshots(), 1)
        self.assertIn(f"{view.bundle_id}  [healthy|failed]", self.output.getvalue())

    def test_vr6_restore_db_writes_the_selected_checkpoints_record_before_the_swap(self):
        for label in ("schema 1", "legacy"):
            with self.subTest(label=label):
                if label == "schema 1":
                    target = self.checkpoint()
                else:
                    tree = Path(self.temp.name) / "legacy-tree"
                    pfx.source_fixture(tree)
                    folder = pfx.legacy_checkpoint(self.c.backups_dir / ("20260901T120000Z-" + OLD[:12] + "-a1b2c3"),
                                                   project=PROJECT, tree=tree)
                    target = self.c.verify_snapshot(folder.name)
                    for image in target.images.values():
                        self.c.tags[image["reference"]] = image["id"]
                before = len(self.records(target.bundle_id))
                with Events(self.c, "write_verification", "swap_database") as events:
                    self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 0, self.last_error)
                self.assertLess(events.order.index("write_verification", 1), events.order.index("swap_database"))
                records = self.records(target.bundle_id)
                self.assertEqual(len(records), before + 1)
                record = next(item for item in records if item["target"]["names"][0].startswith("pf_restore_"))
                self.assertEqual((record["manifest_sha256"], record["result"], record["target"]["removed"]),
                                 (target.manifest_sha256, "passed", False))
                self.assertTrue(record["target"]["names"][0].startswith("pf_restore_"))
                locale = next(check for check in record["checks"] if check["name"].startswith("locale:"))
                self.assertEqual(locale["result"], "passed" if label == "schema 1" else "not_run")


# ============================================================================ PG: PostgreSQL requirements


@ROOT_FS
class Postgres(Base):

    def candidate_answers(self, *, collate=None, rows=None, available=None):
        real = self.c.sql

        def sql(database, statement, **kwargs):
            result = real(database, statement, **kwargs)
            if statement == pf.FACTS_SQL and collate:
                result = "\n".join(re.sub(r"\|en_US\.utf8\|", "|" + collate + "|", line, count=1)
                                   if line.startswith("pf_restore_") else line for line in result.splitlines())
            if statement == pf.ROW_COUNTS_SQL and rows is not None and database.startswith("pf_restore_"):
                result = f"public.records|{rows}"
            if statement == pf.AVAILABLE_EXTENSIONS_SQL and available is not None:
                result = "\n".join(available)
            return result

        return mock.patch.object(self.c, "sql", side_effect=sql)

    def test_pg1_store_facts_and_roles_never_carry_a_password(self):
        view = self.checkpoint()
        store = view.active_store
        self.assertEqual((store["owner"], store["encoding"], store["collate"], store["ctype"]),
                         ("partflow_staging", "UTF8", "en_US.utf8", "en_US.utf8"))
        self.assertEqual(store["extensions"], [{"name": "plpgsql", "version": "1.0"}])
        self.assertEqual(view.manifest["roles"][0]["name"], "partflow_staging")
        text = json.dumps(view.manifest)
        for forbidden in ("password", "rolpassword", "md5", "SCRAM"):
            self.assertNotIn(forbidden, text)
        for statement in (pf.FACTS_SQL, pf.ROLES_SQL, pf.EXTENSIONS_SQL, pf.ROW_COUNTS_SQL):
            self.assertNotIn("passw", statement.lower())

    def restore_refused(self, message, **answers):
        target = self.checkpoint()
        with self.candidate_answers(**answers):
            self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 1)
        self.assertIn(f"ERROR: checkpoint-incompatible: {target.bundle_id}: {message}. The candidate database was "
                      "dropped; the current database is unchanged.", self.last_error)
        self.assertFalse([name for name in self.c.dbs if name.startswith(("pf_restore_", "pf_keep_"))])
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        record = next(item for item in self.records(target.bundle_id)
                      if item["target"]["names"][0].startswith("pf_restore_"))
        self.assertEqual(record["result"], "failed")
        return target

    def test_pg2_candidate_collation_differs(self):
        self.restore_refused("collate differs (en_US.utf8 vs C)", collate="C")

    def test_pg3_missing_extension(self):
        calls = len(self.c.calls)
        self.restore_refused("extension plpgsql is not available on this server", available=["pg_trgm"])
        creates = [call for call in self.c.calls[calls:] if call[0] == "compose" and "createdb" in call[1]
                   and str(call[1][-1]).startswith("pf_restore_")]
        self.assertEqual(creates, [])

    def test_pg4_owner_differs_from_the_frozen_user(self):
        target = self.checkpoint()
        manifest = copy.deepcopy(target.manifest)
        manifest["stores"][0]["owner"] = "someone_else"
        foreign = pf.dataclasses.replace(target, manifest=manifest)
        calls = len(self.c.calls)
        with self.c.lock():
            with self.assertRaisesRegex(pf.Failure, "^checkpoint-incompatible: " + target.bundle_id + ": owner "
                                                    "someone_else is not the frozen POSTGRES_USER partflow_staging"):
                self.c.restore_candidate(foreign, "pf_restore_" + "0" * 20)
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and "createdb" in call[1]])

    def test_pg6_inventory_statements_are_read_only_and_the_window_is_journaled(self):
        for statement in (pf.FACTS_SQL, pf.EXTENSIONS_SQL, pf.ROW_COUNTS_SQL, pf.ROLES_SQL,
                          pf.AVAILABLE_EXTENSIONS_SQL, "SHOW server_version_num;"):
            words = ["exec", "-T", "db", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "u", "-d", "postgres", "-At",
                     "-c", statement]
            self.assertTrue(pf.read_only_sql(words), statement)
        self.c.dbs["pf_keep_20261001t000000z_abc123"] = {"heads": ["r1"], "rows": ["kept"], "connections": False}
        recorded = []
        real = self.c.sql

        def sql(database, statement, **kwargs):
            recorded.append((statement.split(None, 1)[0], kwargs.get("mutation")))
            return real(database, statement, **kwargs)

        with mock.patch.object(self.c, "sql", side_effect=sql), self.c.lock():
            self.c.database_facts(["pf_keep_20261001t000000z_abc123"], counts=True)
        mutations = [item for item in recorded if item[1]]
        self.assertEqual(mutations, [("ALTER", True), ("ALTER", True)])
        self.assertTrue(all(item[0] in ("SELECT", "SHOW") for item in recorded if not item[1]))

    def test_pg7_a_retained_store_is_read_inside_its_connection_window(self):
        name = "pf_keep_20261001t000000z_abc123"
        self.c.dbs[name] = {"heads": ["r1"], "rows": ["kept", "rows"], "connections": False}
        with self.c.lock():
            facts = self.c.database_facts([name], counts=True)[0]
        self.assertEqual((facts["heads"], facts["allow_connections"], facts["row_counts"]["total_rows"]),
                         (["r1"], False, 2))
        self.assertEqual(facts["extensions"], [{"name": "plpgsql", "version": "1.0"}])
        self.assertFalse(self.c.dbs[name]["connections"])
        with mock.patch.object(self.c, "row_counts", side_effect=pf.Failure("query failed")), self.c.lock():
            with self.assertRaisesRegex(pf.Failure, "query failed"):
                self.c.database_facts([name], counts=True)
        self.assertFalse(self.c.dbs[name]["connections"])
        alters = [call[2] for call in self.c.calls if call[0] == "sql" and "ALLOW_CONNECTIONS" in call[2]]
        self.assertEqual([statement.rsplit(" ", 1)[-1] for statement in alters], ["true;", "false;"] * 2)

    def test_pg8_row_counts_differ_on_the_candidate(self):
        self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        target = next(item for item in self.c.snapshots() if item.reason == "before-update")
        self.assertEqual(target.active_store["row_counts"]["total_rows"], 1)  # writers were stopped by the pause
        with self.candidate_answers(rows=7):
            self.assertEqual(self.invoke(["rollback", target.bundle_id, "--restore-db"]), 1)
        self.assertIn(f"ERROR: checkpoint-incompatible: {target.bundle_id}: row counts differ (1/1 vs 1/7). The "
                      "candidate database was dropped; the current database is unchanged.", self.last_error)

    def test_pg9_createdb_carries_the_store_locale_and_legacy_uses_defaults(self):
        calls = len(self.c.calls)
        self.checkpoint()
        creates = [call[1] for call in self.c.calls[calls:] if call[0] == "compose" and "createdb" in call[1]]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][-4:-1], ("--encoding=UTF8", "--lc-collate=en_US.utf8", "--lc-ctype=en_US.utf8"))
        tree = Path(self.temp.name) / "legacy-tree"
        pfx.source_fixture(tree)
        folder = pfx.legacy_checkpoint(self.c.backups_dir / ("20260901T120000Z-" + OLD[:12] + "-a1b2c3"),
                                       project=PROJECT, tree=tree)
        legacy = self.c.verify_snapshot(folder.name)
        for image in legacy.images.values():
            self.c.tags[image["reference"]] = image["id"]
        calls = len(self.c.calls)
        self.assertEqual(self.invoke(["rollback", folder.name, "--restore-db"]), 0, self.last_error)
        restore = [call[1] for call in self.c.calls[calls:] if call[0] == "compose" and "createdb" in call[1]
                   and str(call[1][-1]).startswith("pf_restore_")]
        self.assertEqual(len(restore), 1)
        self.assertFalse([word for word in restore[0] if str(word).startswith(("--encoding", "--lc-"))])


# ============================================================================ PB: purge bundles


@ROOT_FS
class PurgeBundle(Base):

    @staticmethod
    def fake_command(test):
        real = test.c.command

        def command(argv, **kwargs):
            if list(argv[:4]) == ["docker", "image", "save", "-o"]:
                target = Path(argv[4])
                manifest = Path(test.temp.name) / "image-manifest.json"
                manifest.write_text(json.dumps([str(item) for item in argv[5:]]) + "\n")
                with tarfile.open(target, "w") as archive:
                    archive.add(str(manifest), arcname="manifest.json")
                test.saved = [str(item) for item in argv[5:]]
                return ""
            if list(argv[:3]) == ["docker", "image", "load"]:
                return "Loaded image"
            return real(argv, **kwargs)

        return mock.patch.object(test.c, "command", side_effect=command)

    @staticmethod
    def build(test, *, workspace_edit=None):
        """A real schema 1 purge bundle of the fake instance (create_purge_recovery inside a locked operation)."""
        if workspace_edit is not None:
            test.c.workspace_dirty = True
            (test.root / "frontend" / "app.txt").write_text(workspace_edit)
        test.c.running.update(backend=False, frontend=False)
        test.c.dbs.setdefault("pf_keep_20261001t000000z_abc123", {"heads": ["r1"], "rows": ["kept"],
                                                                 "connections": False})
        with PurgeBundle.fake_command(test), \
                mock.patch.object(test.c, "available_snapshot_image_refs", return_value=([], [])), test.c.lock():
            test.c.resources = {"containers": ["c"], "volumes": ["partflow-staging_postgres_data"]}
            preliminary = test.c.plan_for("purge", test.c.docker_inventory(), command="purge")
            view, _ = test.c.create_purge_recovery(preliminary)
        test.c.running.update(backend=True, frontend=True)
        test.c.resources = {"containers": [], "volumes": []}
        test.c.workspace_dirty = False
        return test.c.verify_recovery(view.folder)

    @staticmethod
    def legacy(test, fmt=2, files=None, extra=None):
        tree = Path(test.temp.name) / "legacy-bundle-tree"
        if not tree.exists():
            pfx.source_fixture(tree)
        folder = pfx.legacy_purge_bundle(test.c.recovery_root / ("purge-20261006T000000Z-" + OLD[:12] + "-abcdef"),
                                         project=PROJECT, root=test.root, tree=tree, fmt=fmt, files=files,
                                         extra=extra)
        return test.c.verify_recovery(folder)

    @staticmethod
    def restore(test, view, *arguments):
        """`pf restore-instance <id>` of ``view`` into the emptied fake instance."""
        deployed = test.c.state / "deployed.json"
        if deployed.exists():
            deployed.unlink()
        # The purged instance: no database, no running service (the volume was removed with the purge).
        test.c.dbs = {}
        test.c.running = {"db": False, "backend": False, "frontend": False}
        test.c.droppable = {"partflow_staging"}
        for image in view.images.values():
            test.c.tags[image["reference"]] = image["id"]
        with PurgeBundle.fake_command(test):
            return test.invoke(["restore-instance", view.bundle_id, *arguments])

    def temp_path(self, name):
        return Path(self.temp.name) / name

    def test_pb1_pg5_a_schema_1_purge_bundle(self):
        view = PurgeBundle.build(self)
        manifest = view.manifest
        self.assertEqual(pf.lifecycle_errors(manifest, "recovery_manifest"), [])
        self.assertEqual((view.bundle_kind, view.capture_class, view.level),
                         ("purge-bundle", "healthy_checkpoint", "data_restore_verified"))
        types_ = {item["type"] for item in manifest["payloads"]}
        self.assertTrue({"source_archive", "postgres_globals", "checkpoint_history", "image_archive", "config_env",
                         "admin_config", "state_file", "database_dump", "database_list"} <= types_)
        self.assertIn("deployment-record", {item["item"] for item in manifest["exclusions"]})  # legacy pointer
        self.assertEqual(self.saved[-1], tpa.DB_IMAGE_ID)  # the db image layers saved by ID
        self.assertTrue(all(image["archived"] for image in manifest["images"].values()))
        for item in manifest["payloads"]:
            self.assertEqual(item["sensitive"], item["type"] in pf_config.SENSITIVE_PAYLOAD_TYPES, item["path"])
        self.assertEqual(manifest["quiescence"]["mode"], "writers_stopped")
        self.assertEqual(manifest["consistency_groups"],
                         [{"group_id": "purge", "stores": [store["store_id"] for store in view.stores],
                           "claim": "writers-stopped"}])
        self.assertTrue(all(store["row_counts"] is not None for store in view.stores))
        record = self.records(view.bundle_id)[0]
        self.assertEqual(record["manifest_sha256"], view.manifest_sha256)
        self.assertEqual(len([check for check in record["checks"] if check["name"].startswith("restore:")]), 2)
        self.assertEqual(view.derived_from, next(item for item in self.c.snapshots()
                                                 if item.reason == "before-purge").bundle_id)

    def test_pb2_restore_instance_seals_a_record_bound_to_the_bundle(self):
        view = PurgeBundle.build(self)
        self.assertEqual(PurgeBundle.restore(self, view), 0, self.last_error)
        deployment = self.c.current_deployment()
        record = deployment.record
        self.assertEqual(record["operation"]["kind"], "restore-instance")
        self.assertEqual(record["restored_from"], {"bundle_id": view.bundle_id,
                                                   "manifest_sha256": view.manifest_sha256})
        pointer = self.pointer()
        self.assertEqual((pointer["ref"], pointer["checkpoint"]), ("restore:" + view.bundle_id, view.derived_from))
        self.assertEqual(pointer["sha"], OLD)
        self.assertEqual(record["source"]["manifest"]["entries_sha256"], view.manifest["source"]["entries_sha256"])
        self.assertEqual(self.c.dbs["pf_keep_20261001t000000z_abc123"]["connections"], False)

    def test_pb3_a_legacy_format_2_bundle_still_restores_through_the_migration(self):
        view = PurgeBundle.legacy(self)
        self.assertEqual(PurgeBundle.restore(self, view), 0, self.last_error)
        self.assertIn(f"note: legacy-manifest-migrated: {view.bundle_id} format 2 read as healthy_checkpoint; "
                      "limitations: legacy manifest: no verification record", self.output.getvalue())
        record = self.c.current_deployment().record
        self.assertEqual(record["restored_from"]["manifest_sha256"], view.manifest_sha256)
        self.assertEqual((record["source"]["provenance"], record["source"]["commit"]), ("git_commit", OLD))

    def test_pb4_a_hostile_history_archive_is_refused_before_the_journal(self):
        hostile = io.BytesIO()
        with tarfile.open(fileobj=hostile, mode="w:gz") as archive:
            link = tarfile.TarInfo(PROJECT + "/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc"
            archive.addfile(link)
        view = PurgeBundle.legacy(self, files={"revision-checkpoints.tar.gz": hostile.getvalue()})
        confirm = mock.Mock()
        deployed = self.c.state / "deployed.json"
        deployed.unlink()
        self.assertEqual(self.invoke(["restore-instance", view.bundle_id], confirm=confirm), 1)
        self.assertIn("ERROR: archive-member-refused: revision-checkpoints.tar.gz: type: 'partflow-staging/escape'. "
                      "Nothing was extracted.", self.last_error)
        confirm.assert_not_called()
        self.assertEqual(pfx.operations_of(self.context, "restore-instance"), [])

    def test_pb5_side_by_side_reads_strictly_first(self):
        view = PurgeBundle.build(self)
        self.assertEqual(self.invoke(["restore-instance", view.bundle_id, "--side-by-side"]), 0, self.last_error)
        copies = [name for name in self.c.dbs if name.startswith("pf_recovery_")]
        self.assertEqual(len(copies), 1)
        self.assertEqual(self.c.dbs[copies[0]]["rows"], ["old-record"])
        (view.folder / "databases" / "active.dump").write_bytes(b"{}")
        confirm = mock.Mock()
        self.assertEqual(self.invoke(["restore-instance", view.bundle_id, "--side-by-side"], confirm=confirm), 1)
        self.assertIn(f"bundle-payload-mismatch: {view.bundle_id}: databases/active.dump", self.last_error)
        confirm.assert_not_called()

    def test_pb6_seal_then_restore_from_the_bundles_own_payloads(self):
        seen = []
        real = self.c.verify_store

        def verify(store, dump, candidate, **kwargs):
            seen.append(str(dump))
            return real(store, dump, candidate, **kwargs)

        with Events(self.c, "write_manifest", "verify_bundle") as events, \
                mock.patch.object(self.c, "verify_store", side_effect=verify):
            view = PurgeBundle.build(self)
        self.assertEqual(events.order, ["write_manifest", "verify_bundle", "write_manifest", "verify_bundle"])
        purge_dumps = [path for path in seen if str(view.folder) in path]
        self.assertEqual(sorted(purge_dumps), sorted(str(view.folder / store["dump"]) for store in view.stores))
        self.assertEqual(self.records(view.bundle_id)[0]["manifest_sha256"],
                         hashlib.sha256((view.folder / "manifest.json").read_bytes()).hexdigest())

    def test_pb7_a_bundle_without_a_passed_record_stops_the_purge_before_deletion(self):
        real = self.c.verify_purge_bundle

        def without_record(view, names):
            verified = real(view, names)
            shutil.rmtree(str(self.c.verifications_dir / view.bundle_id))
            return verified

        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        with PurgeBundle.fake_command(self), \
                mock.patch.object(self.c, "available_snapshot_image_refs", return_value=([], [])), \
                mock.patch.object(self.c, "verify_purge_bundle", side_effect=without_record), \
                mock.patch.object(self.c, "write_deletion_plan") as write_plan, \
                mock.patch.object(pf, "confirm"), mock.patch.object(pf, "prompt_yes_no", return_value=False), \
                self.c.lock():
            with self.assertRaisesRegex(pf.Failure, r"^purge-bundle-unverified: purge-.*: no passed "
                                                    r"data_restore_verified record for this bundle's manifest; "
                                                    r"deletion is blocked\. The purge stops before deletion; the "
                                                    r"application is reopened\.$"):
                self.c.purge()
            journal = self.c.journal
        write_plan.assert_not_called()
        # PU-5: reopened in-process, the purge closed cancelled with no deletion approval.
        self.assertEqual((journal["phase"], journal["deletion"]), ("cancelled", None))
        self.assertEqual(self.open_ops(), [])
        self.assertTrue(self.c.running["backend"] and self.c.running["frontend"])
        self.assertIn("partflow_staging", self.c.dbs)

    def test_pb8_a_resumed_deleting_purge_re_reads_without_a_new_verification(self):
        view = PurgeBundle.build(self)
        effects = pfx.default_effects("purge")
        effects[2] = dict(effects[2], preconditions=["bundle:" + view.bundle_id, "checkpoint:" + view.derived_from])
        plan = pfx.lifecycle_plan(self.context, "purge", effects)
        directory = pfx.write_operation(self.context, plan)
        binding = pf.pf_docker.plan_deletion(self.c.docker_inventory(), kind="purge",
                                             operation_id=plan["operation_id"], daemon=self.c.verify_daemon(),
                                             recovery_id=view.bundle_id, covered_image_refs=set())
        binding["slug"] = self.c.context.slug
        data = pf.pf_docker.plan_bytes(binding)
        pf_instance._write_private_file(directory / "deletion-plan.json", data, 0o600)
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(pfx.lifecycle_journal(
            plan, phase="deleting", states={"e0001": "complete", "e0002": "complete", "e0003": "complete",
                                            "e0004": "complete"},
            deletion={"plan_sha256": pf_instance.sha256_bytes(data), "delete_backups": False,
                      "reset_admin_config": False, "confirmed_at": "20261007T040500Z"})))
        before = self.records(view.bundle_id)
        with Events(self.c, "read_bundle") as events, \
                mock.patch.object(self.c, "purge_cleanup", return_value="absent") as cleanup:
            self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertIn("read_bundle", events.order)
        self.assertEqual(len(cleanup.call_args_list), 4)
        self.assertEqual(self.records(view.bundle_id), before)

    def test_pb9_a_copy_altered_before_the_seal_stops_the_purge(self):
        real = pf.copy_fresh

        def altered(source, destination):
            real(source, destination)
            if Path(destination).name == "active.dump":
                with open(str(destination), "ab") as stream:
                    stream.write(b"altered")

        with mock.patch.object(pf, "copy_fresh", side_effect=altered):
            with self.assertRaises(pf.PlanChanged) as caught:
                PurgeBundle.build(self)
        self.assertRegex(str(caught.exception), r"^bundle-payload-mismatch: purge-.*: databases/active\.dump: size\. "
                                                r"The purge stops before deletion; the application is reopened\.$")
        self.assertIsNotNone(caught.exception.checkpoint)
        bundles = list(self.c.recovery_root.iterdir())
        self.assertFalse([bundle for bundle in bundles if (bundle / "manifest.json").exists()])

    def test_pb10_restore_stages_the_source_payload_and_publishes_the_workspace_archive(self):
        view = PurgeBundle.build(self, workspace_edit="local edits\n")
        self.assertEqual(view.workspace_payload, "workspace.tar.gz")
        self.assertEqual(PurgeBundle.restore(self, view), 0, self.last_error)
        record = self.c.current_deployment().record
        self.assertEqual(record["source"]["manifest"]["entries_sha256"], view.manifest["source"]["entries_sha256"])
        self.assertEqual((self.root / "frontend" / "app.txt").read_text(), "local edits\n")
        with tarfile.open(self.c.current_deployment().folder / "source.tar.gz") as archive:
            self.assertEqual(archive.extractfile("frontend/app.txt").read(), OLD.encode())


    def test_pb12_recovery_replaces_workspace_but_preserves_external_control_and_admin_config(self):
        """Moved from test_pf_admin.RecoverySourceTests (former replace_source_for_recovery, PF-A3.2: W1-W4 of
        restore-instance): a format 1 bundle (its runtime .env inside the source archive) restores the recovered
        source as the workspace, keeps the old workspace as a retained generation, and never touches the external
        control release or pf-config.json; no .env or DEPLOYED_SOURCE.txt ever lands in the workspace."""
        control = self.c.control_dir / "pf-admin.py"
        control_before = control.read_bytes()
        config = self.c.config_dir / "pf-config.json"
        config_before = config.read_bytes()
        tree = Path(self.temp.name) / "legacy-bundle-tree"
        pfx.source_fixture(tree)
        (tree / "deploy/synology/pf-admin.py").write_text("# old recovered repository source\n")
        (self.root / "editor-scratch.txt").write_text("kept in the retained generation\n")
        view = PurgeBundle.legacy(self, fmt=1)
        self.assertEqual(PurgeBundle.restore(self, view), 0, self.last_error)
        self.assertEqual(control.read_bytes(), control_before)
        self.assertEqual(config.read_bytes(), config_before)
        self.assertEqual((self.root / "deploy/synology/pf-admin.py").read_text(), "# old recovered repository source\n")
        self.assertFalse((self.root / ".env").exists())
        self.assertFalse((self.root / "DEPLOYED_SOURCE.txt").exists())
        self.assertFalse((self.root / "editor-scratch.txt").exists())
        _, plan, journal = tpa.latest_operation(self.c, "restore-instance")
        retained = pf_instance.generation_container(self.context) / plan["workspace"]["generation_id"]
        self.assertEqual((retained / "editor-scratch.txt").read_text(), "kept in the retained generation\n")
        self.assertEqual(journal["phase"], "completed")

    def test_pb11_a_listed_state_file_without_its_payload_is_refused(self):
        # Audit AF-1 (schema 1): a state file is restored only from its verified state/<name> payload.
        view = PurgeBundle.build(self)
        missing = next(name for name in pf.RESTORABLE_STATE_FILES if view.payload("state/" + name) is None)
        tpa.rewrite_manifest(view.folder, lambda m: m["purge"]["state_files"].append(missing))
        with self.assertRaisesRegex(pf.Failure, "^recovery-state-file-refused: bundle " + view.bundle_id
                                    + " lists state file '" + missing + "' without a state/" + missing):
            self.c.verify_recovery(view.folder)


# ============================================================================ RT: routes and listings


class Routes(Base):

    def test_rt1_route_key_and_dispatch_row(self):
        parsed = pf.parser().parse_args(["backup", "--emergency"])
        self.assertEqual(pf.route_key(parsed), "backup emergency")
        self.assertEqual(pf.route_key(pf.parser().parse_args(["backup"])), "backup")
        self.assertEqual(pf.DISPATCH["backup emergency"], pf._locked(
            "backup emergency", "mutating", "backup emergency", "owned", "never", "terminal", "",
            "Controller.capture_emergency"))
        self.assertEqual(pf.PENDING_ROUTE_COMMANDS, {"backup emergency": "backup --emergency"})
        self.assertIn("backup", pf.KNOWN_COMMANDS)

    def test_rt2_the_pending_route(self):
        accepts = pf.PENDING_ROUTES["backup emergency"][0]
        phases = {"deploy": "initializing", "update": "preserving", "rollback": "preserving-current",
                  "reset-db": "initializing", "purge": "deleting", "restore-instance": "restoring-data",
                  "abort-deploy": "deleting"}
        for kind, phase in phases.items():
            plan = pfx.lifecycle_plan(self.context, kind, supersedes=pfx.operation_id("deploy", "00000000", -60)
                                      if kind == "abort-deploy" else None)
            journal = pfx.lifecycle_journal(plan, phase=phase, states={"e0001": "complete"})
            with self.subTest(kind=kind):
                self.assertEqual(accepts(pf.OperationView(kind, phase, plan, journal)),
                                 kind in ("deploy", "update", "rollback", "reset-db"))
        self.assertFalse(accepts(pf.OperationView("permissions", "applying")))

    @ROOT_FS
    def test_rt4_listing_lines(self):
        view = self.checkpoint()
        tree = Path(self.temp.name) / "legacy-tree"
        pfx.source_fixture(tree)
        legacy_id = "20260901T120000Z-" + OLD[:12] + "-a1b2c3"
        pfx.legacy_checkpoint(self.c.backups_dir / legacy_id, project=PROJECT, tree=tree)
        broken = "20260801T120000Z-" + OLD[:12] + "-b2b2b2"
        (self.c.backups_dir / broken).mkdir()
        self.assertEqual(self.invoke(["backups"]), 0, self.last_error)
        out = self.output.getvalue()
        self.assertIn(f"  1. {view.bundle_id}  [healthy|data-restore]  scheduled-or-manual-backup  DB=r1  "
                      f"source=git_commit {OLD[:12]}\n", out)
        self.assertIn(f"  2. {legacy_id}  [healthy|captured]  legacy:scheduled-or-manual-backup  DB=r1  "
                      f"source=claimed {OLD[:12]}  legacy-format-2\n", out)
        self.assertIn(f"  3. {broken}  [invalid: manifest-missing]\n", out)
        bundle = PurgeBundle.build(self)
        self.assertEqual(self.invoke(["recoveries"]), 0, self.last_error)
        self.assertIn(f"  1. {bundle.bundle_id}  [data-restore]  project=partflow-staging  db=partflow_staging  "
                      f"source=git_commit {OLD[:12]}  derived_from={bundle.derived_from}\n", self.output.getvalue())

    @ROOT_FS
    def test_rt5_status_and_doctor_report_the_deployment_read_only(self):
        view = self.deploy()
        real = self.c.compose

        def compose(*args, **kwargs):
            if args[:1] == ("ps",):
                return "NAME  STATUS"
            return real(*args, **kwargs)

        before = pfx.snapshot_tree(self.context.paths.private_state, self.layout.root / "locks")
        with mock.patch.object(self.c, "compose", side_effect=compose), \
                mock.patch.object(self.c, "describe_envelope", return_value="ok (fixture)"):
            self.invoke(["status"])
            self.invoke(["doctor"])
        out = self.output.getvalue()
        line = f"Deployment: {view.deployment_id} (git_commit {NEW[:12]}), sealed {view.record['created_at']}"
        self.assertEqual(out.count(line), 2)
        self.assertEqual(pfx.snapshot_tree(self.context.paths.private_state, self.layout.root / "locks"), before)

    def test_rt6_legal_routes_spell_the_command(self):
        plan = pfx.lifecycle_plan(self.context, "update")
        journal = pfx.lifecycle_journal(plan, phase="preserving", unresolved="e0003",
                                        states={"e0001": "complete", "e0002": "complete", "e0003": "unknown"})
        routes = {command: description for _, command, description in
                  pf.pf_config.operation_routes(plan, journal, slug="staging")}
        self.assertEqual(routes["pf --instance staging backup --emergency"],
                         "capture the current data as emergency preservation (does not change this operation)")
        self.assertEqual(routes[f"pf --instance staging resume --operation {plan['operation_id']}"],
                         "reopen the unchanged deployment (no data or source effect started)")
        self.assertEqual(journal["legal_next"], list(routes))


if __name__ == "__main__":
    unittest.main()
