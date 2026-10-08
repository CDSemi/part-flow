"""PF-A3.2: the operation journal, the open-operation index and route gate, the durable effect protocol, resume and
abandon, the workspace generation switch, frozen configuration across restarts and the diagnostics.

Case mapping (PF-A3.2 SPEC section 6.1):
  schema amendments AM-1..AM-10          -> SchemaAmendments (SA-1..SA-11)
  operation store and index              -> Store (OS-1..OS-3, OS-5..OS-12, OS-14), StoreInstance (OS-4, OS-13)
  route gate and legal next steps        -> Routes (RO-1..RO-4, RO-6, RO-7, RO-9, RO-10, RO-12, RO-13),
                                            StoreInstance (RO-8, RO-11); RO-5 is test_artifacts EP-1
  durable effect protocol                -> Protocol (PR-1..PR-5, PR-7..PR-10), RunnerInterrupt (PR-11),
                                            PurgeSurvival (PR-6)
  resume / abandon and the crash matrix  -> CrashMatrix (6 kinds x every effect x before-intent, after-intent,
                                            after-effect), Resume (incl. RS-26b), ResumeDatabase, RestoreResume,
                                            ProcessProbes (RS-24, RS-25), CliLifecycle (RS-18, RS-25b)
  workspace generation switch            -> Workspace (WS-1..WS-20)
  frozen configuration (A3-T09)          -> ConfigConcurrency (CF-1..CF-5, CF-8), RestoreResume (CF-7),
                                            PurgeSurvival (CF-4b); CF-6 is Resume RS-27
  purge survival, diagnostics            -> PurgeSurvival (PU-1, PU-2, PU-4, PU-5), ProcessProbes (DG-1..DG-8)
  fail-closed and staging                -> FailClosed (FC-1..FC-4, SG-1..SG-3), ResumeDatabase (SG-4, RS-13)
  installer gate                         -> Installer (IN-1..IN-6)
  installed launcher                     -> CliRestart (CL-6..CL-10), CliPurgeSignals (CL-2, CL-5; purge variants),
                                            CliLifecycle (CL-1, CL-3, CL-4, CL-11..CL-14)

Docker and PostgreSQL are simulated by test_pf_admin.FakeController (the real lifecycle code runs) or by the
fake_docker.py program (CliLifecycle: its simulated application plane, under a release whose only difference is a
fixture-local release source); files, locks and renames are real. Real-filesystem cases run as uid 0 in the throwaway
container. Nothing touches a real daemon, a real NAS or the running PartFlow stack. Evidence JSON is written only when
PF_A32_EVIDENCE names a directory.
"""
import contextlib
import copy
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
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402
import test_pf_admin as tpa  # noqa: E402
import test_artifacts as ta  # noqa: E402

pf = pfx.pf
pf_instance = pfx.pf_instance
pf_config = pf.pf_config
pf_runner = pf.pf_runner
pf_install = pf.pf_install
CONTRACTS = pfx.PACKAGE / "contracts"
EXAMPLES = CONTRACTS / "examples" / "lifecycle"
ROOT_FS = unittest.skipUnless(os.geteuid() == 0, "real-filesystem cases run as uid 0 inside the throwaway container")
OLD, NEW = pfx.OLD, pfx.NEW
PROJECT = tpa.PROJECT
SLUG = "staging"


def evidence(name, payload):
    """Section 6.3 evidence (JSON) when PF_A32_EVIDENCE names a directory; nothing otherwise."""
    directory = os.environ.get("PF_A32_EVIDENCE")
    if not directory:
        return
    path = Path(directory) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


def validate(value, name, plan=None):
    return pf.lifecycle_errors(value, name, plan=plan)


def fixture_context(operations_dir=None):
    """The identity fields a lifecycle plan names, without an installation (pure table tests)."""
    return types.SimpleNamespace(
        instance_id="3f0c6a8e-1b2d-4c5e-9f00-a1b2c3d4e5f6", slug=SLUG, compose_project=PROJECT,
        daemon=types.SimpleNamespace(engine_id=pfx.ENGINE_ID), record_sha256="1" * 64,
        control=types.SimpleNamespace(release_id="pf-fixture", sha256="2" * 64),
        profile=types.SimpleNamespace(id="partflow-staging-legacy", version="2.5.0-a1.1", sha256="3" * 64),
        approved_policy=types.SimpleNamespace(revision=1, sha256="4" * 64),
        paths=types.SimpleNamespace(workspace=Path("/volume1/partflow/repo")),
        operations_dir=operations_dir)


def done(*ids):
    return {item: "complete" for item in ids}


def upto(last):
    """{e0001..e<last-1>: complete} (the effects before ``last``)."""
    return {f"e{index:04d}": "complete" for index in range(1, int(last[1:]))}


PURGE_DELETION = {"plan_sha256": "d" * 64, "delete_backups": True, "reset_admin_config": False,
                  "confirmed_at": "20261007T040500Z"}


def write_op(root, plan, journal=None):
    """The real writers into ``root/<op>`` (a fixture operation of the pure index tests)."""
    directory = Path(root) / plan["operation_id"]
    os.mkdir(str(directory), 0o700)
    pf_instance.write_plan_once(directory, pf_instance.normalize_json(plan))
    if journal is not None:
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(journal))
    return directory


def index_of(root, permissions=None, limit=None):
    files, overflow = pf_instance.scan_operations(root, limit=limit)
    return pf_config.classify_operations(files, permissions_journal=permissions, overflow=overflow, validate=validate)


def gate(index, route, **request):
    return pf_config.gate_decision(index, route, slug=SLUG, request=request, private_state="/ps")


def entry_of(plan, journal):
    data = pf_instance.normalize_json(plan)
    return pf_config.OperationEntry(plan["operation_id"], "blocking", plan=plan,
                                    plan_sha256=pf_instance.sha256_bytes(data), journal=journal)


def single_index(plan, journal):
    """The OperationIndex of exactly one valid operation (pure; classified as the scan would)."""
    files = [pf_instance.OperationFiles(plan["operation_id"], pf_instance.normalize_json(plan),
                                        pf_instance.normalize_json(journal), None)]
    return pf_config.classify_operations(files, permissions_journal=None, validate=validate)


# ============================================================================ SA: schema amendments (AM-1..AM-10)


A31_REBASELINED = {"operation-plan.json": ("operation_plan", True, None),
                   "operation-journal.json": ("operation_journal", True, None),
                   "invalid-plan-duplicate-effect.json": ("operation_plan", False,
                                                          "effect ids must be unique and increasing"),
                   "invalid-journal-phase-for-kind.json": ("operation_journal", False,
                                                           "phase deleting is not a phase of update")}
# The PF-A3.1 corpus rows kept byte-identical (SHA-256 of the files at the A3.1 base af77172) with their outcome.
A31_CORPUS = {
    'checkpoint-healthy.json': ('0f66f5bf4e7bdbb565205b9dd486baf450908d4e5fcb983f941a05c4637a5f39',
        'recovery_manifest', True, None),
    'checkpoint-emergency.json': ('a9bf87a29da23e2fdd66adf721d6b815b1fbe81e29478bc3a666c52600cbfba1',
        'recovery_manifest', True, None),
    'checkpoint-emergency-deployment-image.json': ('32db6f9203795d3efa96149c306f8896a05ae91dacaac9b78a3fc8d11f42358f',
        'recovery_manifest', True, None),
    'checkpoint-partial.json': ('b60e2753b1c532c4f1c4c0d16072688f3ea2cb5e13fea2f65655edac71e7d955',
        'recovery_manifest', True, None),
    'purge-bundle.json': ('99295adafeebd96d3554097d9f4eee4ebc2d3e662db5d33007ecd94f295e3501',
        'recovery_manifest', True, None),
    'deployment-record.json': ('232eb8336bcf43bd319745e043a110741ca1939305217fec45f7340ba2ffb811',
        'deployment_record', True, None),
    'verification-record.json': ('43337151b86f90ec8a4334c5ed684e55ac1304086cbd686a9ca4d374e81cf96a',
        'verification_record', True, None),
    'verification-functional-schema-valid.json': ('d58eb370bf24b4c98e6413106d44a84e5ef331c9bdd9faf4c163d9c6cbc0f36a',
        'verification_record', True, None),
    'legacy-format2-checkpoint.json': ('5d9974de423d01fee8f0f9938c4727337ae61211145334997f41decb584a1738',
        'legacy', True, None),
    'legacy-format2-migrated.json': ('aa0b625efbd5bd101711a188751686af42c37b9e0627f3d30d5b75b80c5c307a',
        'recovery_manifest', True, None),
    'legacy-format2-before-rollback.json': ('e358efece68648b1c5551d8886a3f7559a02b4083ac6bf4cb3c0fb5f09a08207',
        'legacy', True, None),
    'legacy-format2-before-rollback-migrated.json': ('0a960d320f414553f2099942307d293e61f456a67061becfe0f7f3b79c7f2dc8',
        'recovery_manifest', True, None),
    'legacy-format1-checkpoint.json': ('a35683295989d78eff24524097772671a33b96da5d2fa1311aed9e8deac4e39b',
        'legacy', True, None),
    'legacy-format1-checkpoint-migrated.json': ('acdd81614dfe2cdb81af53cfa3e988ae4730369f0c1b6f797927f93fd100dcdc',
        'recovery_manifest', True, None),
    'legacy-format2-purge.json': ('5cb94d7a0c45ba00c351a0a07919419a1b5cc47a61d83aee2286787d63222531',
        'legacy', True, None),
    'legacy-format2-purge-migrated.json': ('2fdbfd25e618905f7e15aa7dd9ca1b118bebf5a28a5c180d5fa0f8f1e7476b4a',
        'recovery_manifest', True, None),
    'legacy-format1-purge.json': ('c984f40ebea06f5f5169e0e577d73518557ab10d71a75baa64014ae9e8a12105',
        'legacy', True, None),
    'legacy-format1-purge-migrated.json': ('1723b601d4b57930a1604580a55416391020602651af0e2b0447eb4d75beec4e',
        'recovery_manifest', True, None),
    'invalid-empty-payloads.json': ('d1ed9cdb3cb4d368b7f44a718ba133eed74c71925d7d222db50374eb0cebc60c',
        'recovery_manifest', False, 'payloads: empty (rule 1)'),
    'invalid-store-without-dump.json': ('a3d4683cc3eddc29c2796c529e0241a83af7f57c8afabf68edfe56ccaf1f5902',
        'recovery_manifest', False, "no database_dump payload 'database.dump' of this store (rule 2)"),
    'invalid-healthy-missing-db-image.json': ('c9d5bf65ff52802cd39065f6c31812d8957edc33ffd66a00ef89574e028e3aeb',
        'recovery_manifest', False, 'healthy_checkpoint: images.db is required (rule 4)'),
    'invalid-healthy-missing-backend-image.json': ('461ba803c156181e3e4b273100dff06486fb04837b9685fd5bf2dbb1a6155594',
        'recovery_manifest', False, 'images.backend and images.frontend are required (rule 4)'),
    'invalid-healthy-null-platform.json': ('82ae520242d055bf45b53898ee7dc8d7e5561a3a2995d402bbb3fe8a2788a231',
        'recovery_manifest', False, 'every image needs a platform (rule 4)'),
    'invalid-wrong-schema-version.json': ('401f7576d87e1b01b03ae67792c9a839abf64731d41ba799d3bd415ca53564e2',
        'recovery_manifest', False, '$.schema_version: must equal 1'),
    'invalid-bool-schema-version.json': ('2846a9dca93890f280bbf783b6cd29477f805b935a146353efaad09f868a4dd5',
        'recovery_manifest', False, '$.schema_version: must equal 1'),
    'invalid-bundle-id-kind-mismatch.json': ('9d2432c7f9085a98bb8a48bf529e1eb48512965ae325ac3cf67ace9d9f80531f',
        'recovery_manifest', False, 'bundle_id does not match bundle_kind'),
    'invalid-duplicate-payload-path.json': ('fc3b037f83104996abab9428c53268c7a6a85860f3146889e8536e51d6e11ddc',
        'recovery_manifest', False, 'duplicate path'),
    'invalid-traversal-payload-path.json': ('7a281b1ca5156da342b9057e2f56f7e8ede8fd6bc81c825c74314b319844fd0f',
        'recovery_manifest', False, 'is not canonical'),
    'invalid-absolute-payload-path.json': ('b8bbfe9490be5587bfa755305da068cb5b13a6a7ec116ab5e8844981c2431ce4',
        'recovery_manifest', False, 'is not canonical'),
    'invalid-bad-sha256.json': ('42a9c146ede44187c399c7da62d14a7e665c2feb48b2c2683ca8a2437c6d8b36',
        'recovery_manifest', False, 'payloads[0].sha256: does not match'),
    'invalid-negative-size.json': ('888c12ba00c62fd82e19602e15240dbf2059c4826473de9f759056ae4d286ad3',
        'recovery_manifest', False, 'payloads[0].size: below minimum 0'),
    'invalid-float-size.json': ('6a8077c87b2f43f2e79a42491058515eb0d68225bc7d587cf2a6d597922211f0',
        'recovery_manifest', False, 'payloads[0].size: expected integer'),
    'invalid-unknown-key.json': ('1397a66615e120a625c543727d3ee0744964c9ee0cddb1eb68e5a9c3a82a0e56',
        'recovery_manifest', False, 'unknown keys status'),
    'invalid-duplicate-key.json': ('07e4e79931696a82709bd3f97fa494c8bda4307a1a755160c8e5d7fc12e1550d',
        'recovery_manifest', False, 'Duplicate'),
    'invalid-nan.json': ('bd12ff38245dfd5309e1e53d84b1fb03b300c78c879e05db3c53b43293eaa6e7',
        'recovery_manifest', False, 'NaN'),
    'invalid-not-normalized.json': ('c2ee906b9daf28568332e5f4b547dc9cb680ce4f0aee12a6b98c945e9bc1dafa',
        'recovery_manifest', False, 'not normalized'),
    'invalid-purge-partial.json': ('d9ff101c081178dcf03d2958dfe95a717cc9fa0bd4157aab8bf22029970c8b55',
        'recovery_manifest', False, 'partial: never a purge bundle (rule 6)'),
    'invalid-checkpoint-carries-config.json': ('a83dcadfbb85ed14e5c72abbcfed245e017aef202b1a3d6fe781ebee04b36d5b',
        'recovery_manifest', False, 'the group-readable checkpoint carries no config_env (rule 8)'),
    'invalid-provenance-commit-without-git.json': ('190f414d8b0e1dda5ed8712ed97105753dcca1d1e73b993825093cf58944c33c',
        'recovery_manifest', False, 'provenance git_commit exactly when commit and remote are set (rule 10)'),
    'invalid-multi-store-without-writers-stopped.json': ('a4eb89b4f7cebedc8d3b16ed563b291926c9c9cbbf714118d604e7338c393732',
        'recovery_manifest', False, 'a multi-store group claims writers-stopped (rule 9)'),
    'invalid-active-store-without-group.json': ('e16d5aaa7ed6084406e42b65cf71322d90de940ce3202b8408f9f053401ee0a4',
        'recovery_manifest', False, 'must be in exactly the one consistency group it names (rule 3)'),
    'invalid-emergency-wrong-reason.json': ('5d74fcfb03b0a80c333b5e8d441f7549813a93684bd5c52f5a88031b8dbfa55b',
        'recovery_manifest', False, 'reason must be emergency-manual or before-rollback (rule 5)'),
    'invalid-nonlegacy-null-producer.json': ('89de6bcb7d5f9d36eb6f398e2d3bf35e5f80175c35dc395c5fe10a4f43b408f9',
        'recovery_manifest', False,
        'producer, quiescence, postgresql.server_version_num and postgresql.image_id are required (rule 11)'),
    'invalid-nonlegacy-legacy-claim-origin.json': ('cc316c46f8942c45603be0ca304a0e43465c721e6c79489f700641c9629b0dad',
        'recovery_manifest', False, 'origin legacy-claim exactly for a legacy manifest (rule 10)'),
    'invalid-legacy-provenance-git-commit.json': ('8441a1739fce94423b995a256588149d5f4663f7723f4f7ca8d3d0fcd063b351',
        'recovery_manifest', False, 'a legacy claim is never a proof; provenance is unknown (rule 11)'),
    'invalid-legacy-null-without-limitation.json': ('0cbc3e8a584771da885b814949a399a8ddb3b17cdb2cbba077302b1cde13e561',
        'recovery_manifest', False, 'legacy.limitations: missing'),
    'invalid-purge-missing-globals.json': ('8de63f815597a1b702691eb810ed84a52d7d9a925c7d92ef4eb0a4982e77d7c8',
        'recovery_manifest', False, 'a postgres_globals payload is required (rule 7)'),
    'invalid-purge-legacy-missing-globals.json': ('23d0af162411b7da9933f68099a98c46bd731ab47000d4773101e312dfe964e2',
        'recovery_manifest', False, 'a postgres_globals payload is required (rule 7)'),
    'invalid-origin-none-with-payload.json': ('99e1c8725d6d156b16dae4db49ca03445a5ddd23ebdaccf886a42d95d3a22d35',
        'recovery_manifest', False, 'origin none exactly when the source payload is null (rule 10)'),
    'invalid-verification-missing-rows-check.json': ('2bf21367b50be3e018c6cf191daa4b50f8a27e1a2014568caad94331df335b49',
        'verification_record', False, 'check rows:postgresql:partflow_staging is missing'),
}


class SchemaAmendments(unittest.TestCase):
    """SA-1..SA-11: the amended lifecycle schema, its corpus and the cross-field rules."""

    def setUp(self):
        self.context = fixture_context()

    def rows(self):
        return json.loads((EXAMPLES / "cases.json").read_bytes())

    def problems(self, data, record, plan=None):
        return ta.record_problems(data, record, plan=plan)

    def plan(self, kind, effects=None, **kwargs):
        return pfx.lifecycle_plan(self.context, kind, effects, **kwargs)

    def plan_problems(self, plan):
        return validate(plan, "operation_plan")

    def test_sa1_embedded_schema_equals_the_contract_file(self):
        data = (CONTRACTS / "lifecycle-records.schema.json").read_bytes()
        self.assertEqual(json.loads(data), pf_config.LIFECYCLE_SCHEMA)
        self.assertEqual(data, (json.dumps(pf_config.LIFECYCLE_SCHEMA, indent=2) + "\n").encode("utf-8"))

    def test_sa2_every_corpus_row_has_its_named_outcome(self):
        rows = self.rows()
        self.assertEqual(len(rows), len({row["file"] for row in rows}))
        for row in rows:
            if row["record"] == "legacy":
                continue  # the legacy migration pairs are SC-2's (test_artifacts), byte for byte
            with self.subTest(file=row["file"]):
                plan = json.loads((EXAMPLES / row["plan"]).read_bytes()) if row.get("plan") else None
                found = self.problems((EXAMPLES / row["file"]).read_bytes(), row["record"], plan)
                if row["expect"]["valid"]:
                    self.assertEqual(found, [])
                else:
                    self.assertTrue(found)
                    self.assertTrue(any(row["expect"]["problem"] in item for item in found), found)
        listed = {row["file"] for row in rows} | {"cases.json", "README.md", "example-app.env"}
        self.assertEqual(sorted(name for name in os.listdir(str(EXAMPLES)) if name not in listed), [])

    def test_sa3_an_effect_phase_outside_the_kind_or_out_of_order_fails(self):
        plan = self.plan("update")
        foreign = copy.deepcopy(plan)
        foreign["effects"][1]["phase"] = "deleting"
        self.assertIn("effects.e0002.phase: deleting is not a phase of update", self.plan_problems(foreign))
        backwards = copy.deepcopy(plan)
        backwards["effects"][3]["phase"] = "preparing"
        self.assertIn("effects.e0004.phase: preparing comes before the phase of an earlier effect (phase order of "
                      "update)", self.plan_problems(backwards))
        for kind, order in pf_config.PHASE_ORDER.items():
            with self.subTest(kind=kind):
                self.assertEqual(order[0], "planned")
                self.assertTrue(set(order) <= set(pf_config.JOURNAL_PHASES[kind]))

    def test_sa4_supersedes_belongs_to_rollback_and_abort_deploy_only(self):
        update = self.plan("update")
        update["supersedes"] = pfx.operation_id("deploy", "00000000")
        self.assertIn("supersedes: a update operation never supersedes another operation (rollback and abort-deploy "
                      "only)", self.plan_problems(update))
        with self.assertRaises(AssertionError) as caught:
            self.plan("abort-deploy")
        self.assertIn("supersedes: required for abort-deploy (it supersedes the incomplete deploy)",
                      str(caught.exception))
        own = self.plan("rollback")
        own["supersedes"] = own["operation_id"]
        self.assertIn("supersedes: an operation cannot supersede itself", self.plan_problems(own))
        self.assertEqual(self.plan_problems(self.plan("rollback", supersedes=pfx.operation_id("update", "0000aaaa"))),
                         [])

    def test_sa5_workspace_modes(self):
        switch = self.plan("update")
        switch["workspace"].update(generation_id=None, container=None)
        self.assertIn("workspace: generation_id and container are set exactly for mode switch or pending",
                      self.plan_problems(switch))
        pending = self.plan("update", workspace_mode="pending")
        pending["workspace"]["reason"] = None
        self.assertIn("workspace.reason: set exactly for mode pending", self.plan_problems(pending))
        untouched = self.plan("update", pfx.default_effects("update", workspace=False))
        untouched["workspace"]["mode"] = "untouched"
        self.assertIn("workspace.mode: untouched exactly for backup, reset-db, purge and abort-deploy",
                      self.plan_problems(untouched))
        current = self.plan("rollback", pfx.default_effects("rollback", workspace=False))
        current["workspace"]["mode"] = "record-current"
        self.assertIn("workspace.mode: record-current only for deploy", self.plan_problems(current))
        for kind in ("backup", "reset-db", "purge"):
            with self.subTest(kind=kind):
                self.assertEqual(self.plan(kind)["workspace"]["mode"], "untouched")

    def test_sa6_deletion_hashes(self):
        purge = self.plan("purge")
        purge["resources"]["deletion_plan_sha256"] = "d" * 64
        self.assertIn("resources.deletion_plan_sha256: set exactly for abort-deploy", self.plan_problems(purge))
        plan = self.plan("purge")
        journal = pfx.lifecycle_journal(plan, phase="deleting", unresolved="e0005",
                                        states=dict(upto("e0005"), e0005="unknown"), deletion=PURGE_DELETION)
        journal["deletion"] = None
        self.assertIn("deletion: required once a resource-delete effect of the purge started",
                      validate(journal, "operation_journal", plan))
        abort = self.plan("abort-deploy", supersedes=pfx.operation_id("deploy", "00000000"))
        journal = pfx.lifecycle_journal(abort, phase="deleting", unresolved="e0001", states={"e0001": "unknown"},
                                        deletion=dict(PURGE_DELETION, delete_backups=False))
        journal["deletion"]["plan_sha256"] = "e" * 64
        self.assertIn("deletion.plan_sha256: differs from the plan's frozen deletion plan",
                      validate(journal, "operation_journal", abort))

    def test_sa7_evidence_is_bounded_and_one_line(self):
        plan = self.plan("update")
        journal = pfx.lifecycle_journal(plan, phase="preparing", states={"e0001": "complete"})
        long = copy.deepcopy(journal)
        long["effects"][0]["evidence"] = "x" * 2001
        found = validate(long, "operation_journal", plan)
        self.assertTrue(any("evidence" in item for item in found), found)
        control = copy.deepcopy(journal)
        control["effects"][0]["evidence"] = "line\nbreak"
        self.assertIn("effects.e0001.evidence: control character", validate(control, "operation_journal", plan))
        exact = copy.deepcopy(journal)
        exact["effects"][0]["evidence"] = "x" * 2000
        self.assertEqual(validate(exact, "operation_journal", plan), [])

    def test_sa8_the_a31_corpus_keeps_its_bytes_and_outcomes(self):
        rows = {row["file"]: row for row in self.rows()}
        for name, (sha256, record, valid, problem) in A31_CORPUS.items():
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((EXAMPLES / name).read_bytes()).hexdigest(), sha256)
                self.assertEqual((rows[name]["record"], rows[name]["expect"]["valid"], rows[name]["expect"]["problem"]),
                                 (record, valid, problem))
        for name, (record, valid, problem) in A31_REBASELINED.items():
            with self.subTest(rebaselined=name):
                row = rows[name]
                self.assertEqual((row["record"], row["expect"]["valid"], row["expect"]["problem"]),
                                 (record, valid, problem))
                plan = json.loads((EXAMPLES / row["plan"]).read_bytes()) if row.get("plan") else None
                found = self.problems((EXAMPLES / name).read_bytes(), record, plan)
                if valid:
                    self.assertEqual(found, [])
                else:
                    # A named cross-field problem, never a schema error.
                    self.assertTrue(any(problem in item for item in found), found)
                    self.assertFalse([item for item in found if item.startswith("$")], found)

    def test_sa9_an_effect_phase_planned_fails(self):
        found = self.problems((EXAMPLES / "invalid-plan-effect-phase-planned.json").read_bytes(), "operation_plan")
        self.assertTrue(any(item.startswith("$.effects[0].phase") for item in found), found)
        plan = self.plan("update")
        plan["effects"][0]["phase"] = "planned"
        self.assertTrue(self.plan_problems(plan))

    def test_sa10_a_restore_journal_with_deletion_after_data_started_fails(self):
        plan = self.plan("restore-instance")
        deletion = dict(PURGE_DELETION, delete_backups=False)
        ok = pfx.lifecycle_journal(plan, phase="preparing-target", unresolved="e0004",
                                   states=dict(upto("e0004"), e0004="unknown"), deletion=deletion)
        self.assertEqual(validate(ok, "operation_journal", plan), [])
        late = pfx.lifecycle_journal(plan, phase="restoring-data", unresolved="e0005",
                                     states=dict(upto("e0005"), e0005="unknown"))
        late["deletion"] = deletion
        self.assertIn("deletion: a restore-instance carries a deletion approval only for an abandon in "
                      "preparing-target before any restoring-data effect started",
                      validate(late, "operation_journal", plan))

    def test_sa11_frozen_config_hashes_the_rendered_app_env(self):
        rendered = (EXAMPLES / "example-app.env").read_bytes()
        expected = {"sha256": hashlib.sha256(rendered).hexdigest(), "bytes": len(rendered)}
        plans = sorted(name for name in os.listdir(str(EXAMPLES)) if name.startswith("operation-plan"))
        self.assertGreaterEqual(len(plans), 6)
        for name in plans:
            with self.subTest(plan=name):
                self.assertEqual(json.loads((EXAMPLES / name).read_bytes())["frozen_config"], expected)
        self.assertEqual(pf_config.parse_app_env(rendered, label="example-app.env")["POSTGRES_DB"], "partflow_staging")


# ============================================================================ OS: operation store and index


class Store(unittest.TestCase):
    """OS-1..OS-3, OS-5..OS-12, OS-14: the store primitives and the pure index over real directories."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "operations"
        self.root.mkdir(mode=0o700)
        self.context = fixture_context(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, kind, **kwargs):
        return pfx.lifecycle_plan(self.context, kind, **kwargs)

    def test_os1_the_plan_is_written_once(self):
        plan = self.plan("update")
        directory = write_op(self.root, plan)
        data = (directory / "plan.json").read_bytes()
        with self.assertRaises(FileExistsError):
            pf_instance.write_plan_once(directory, b"{}")
        self.assertEqual((directory / "plan.json").read_bytes(), data)
        self.assertEqual(stat.S_IMODE(os.lstat(str(directory / "plan.json")).st_mode), 0o600)

    def test_os2_a_crash_before_the_rename_keeps_the_previous_generation(self):
        plan = self.plan("update")
        first = pfx.lifecycle_journal(plan, phase="preparing", states={"e0001": "unknown"}, unresolved="e0001")
        directory = write_op(self.root, plan, first)
        before = (directory / "journal.json").read_bytes()
        second = pfx.lifecycle_journal(plan, phase="preparing", states={"e0001": "complete"}, sequence=2)
        with mock.patch.object(pf_instance.os, "replace", side_effect=OSError(5, "simulated crash")):
            with self.assertRaises(OSError):
                pf_instance.write_journal_generation(directory, pf_instance.normalize_json(second))
        self.assertEqual((directory / "journal.json").read_bytes(), before)
        self.assertEqual(sorted(os.listdir(str(directory))), ["journal.json", "plan.json"])
        index = index_of(self.root)
        self.assertEqual((index.blocking[0].journal["sequence"], index.blocking[0].phase), (1, "preparing"))

    def test_os3_fsync_order_file_then_directory(self):
        plan = self.plan("update")
        directory = write_op(self.root, plan)
        synced = []
        real = os.fsync

        def fsync(fd):
            synced.append(os.readlink(f"/proc/self/fd/{fd}"))
            return real(fd)

        journal = pfx.lifecycle_journal(plan, phase="preparing", states={"e0001": "complete"})
        with mock.patch.object(pf_instance.os, "fsync", side_effect=fsync):
            pf_instance.write_journal_generation(directory, pf_instance.normalize_json(journal))
        self.assertTrue(Path(synced[0]).name.startswith("journal.json.tmp-"), synced)
        self.assertEqual(set(synced[1:]), {str(directory)})
        self.assertGreaterEqual(len(synced), 2)
        synced.clear()
        second = self.plan("backup")
        target = self.root / second["operation_id"]
        os.mkdir(str(target), 0o700)
        with mock.patch.object(pf_instance.os, "fsync", side_effect=fsync):
            pf_instance.write_plan_once(target, pf_instance.normalize_json(second))
        self.assertEqual(synced, [str(target / "plan.json"), str(target)])

    def test_os5_scan_bounds(self):
        plan = self.plan("update")
        journal = pfx.lifecycle_journal(plan, phase="preparing", states={"e0001": "complete"})
        big = write_op(self.root, plan, journal)
        with mock.patch.object(pf_instance, "OPERATION_FILE_LIMIT", 64):
            files, overflow = pf_instance.scan_operations(self.root)
        self.assertEqual(overflow, 0)
        self.assertIn("larger than 64 bytes", files[0].error)
        shutil.rmtree(str(big))
        linked = self.plan("backup")
        directory = write_op(self.root, linked)
        outside = Path(self.temp.name) / "outside.json"
        outside.write_bytes(pf_instance.normalize_json(pfx.lifecycle_journal(linked, phase="capturing")))
        os.symlink(str(outside), str(directory / "journal.json"))
        not_dir = self.root / pfx.operation_id("reset-db", "0000beef")
        not_dir.write_text("x")
        (self.root / "notes.txt").write_text("ignored")
        (self.root / ".pf-tmp").mkdir()
        files, overflow = pf_instance.scan_operations(self.root)
        found = {item.operation_id: item for item in files}
        self.assertEqual(set(found), {linked["operation_id"], not_dir.name})
        self.assertIsNotNone(found[linked["operation_id"]].error)
        self.assertIn("not a directory", found[not_dir.name].error)
        index = index_of(self.root)
        self.assertEqual({item.cls for item in index.blocking}, {"invalid"})
        self.assertEqual(gate(index, "backup").code, "operation-journal-invalid")

    def test_os6_a_plan_without_journal_never_blocks(self):
        plan = self.plan("update")
        write_op(self.root, plan)
        index = index_of(self.root)
        self.assertEqual([item.cls for item in index.entries], ["no-journal"])
        self.assertEqual(index.blocking, ())
        self.assertEqual(gate(index, "update").action, "new")
        self.assertEqual(gate(index, "resume").code, "nothing-to-resume")

    def test_os7_a_plan_hash_mismatch_is_invalid_and_blocking(self):
        plan = self.plan("update")
        directory = write_op(self.root, plan, pfx.lifecycle_journal(plan, phase="preparing",
                                                                    states={"e0001": "complete"}))
        changed = dict(plan, created_at="20261007T050000Z")
        os.chmod(str(directory / "plan.json"), 0o600)
        (directory / "plan.json").write_bytes(pf_instance.normalize_json(changed))
        index = index_of(self.root)
        self.assertEqual([(item.cls, item.error) for item in index.blocking],
                         [("invalid", "journal.plan_sha256 is not the SHA-256 of plan.json")])
        decision = gate(index, "resume")
        self.assertEqual(decision.code, "operation-journal-invalid")
        self.assertTrue(decision.message.startswith(f"operation-journal-invalid: {plan['operation_id']}: "))

    def test_os8_supersession_and_a_cancelled_superseding_operation(self):
        update = self.plan("update")
        write_op(self.root, update, pfx.lifecycle_journal(update, phase="migrating", unresolved="e0007",
                                                          states=dict(upto("e0007"), e0007="unknown")))
        rollback = pfx.lifecycle_plan(self.context, "rollback", op=pfx.operation_id("rollback", "0000cafe", 5),
                                      supersedes=update["operation_id"])
        directory = write_op(self.root, rollback, pfx.lifecycle_journal(rollback, phase="preserving-current",
                                                                        states={"e0001": "complete"}))
        index = index_of(self.root)
        self.assertEqual([(item.kind, item.cls) for item in index.blocking], [("rollback", "blocking")])
        self.assertEqual([(item.operation_id, item.superseded_by) for item in index.superseded],
                         [(update["operation_id"], rollback["operation_id"])])
        cancelled = pfx.lifecycle_journal(rollback, phase="cancelled", states={"e0001": "complete"}, sequence=2)
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(cancelled))
        index = index_of(self.root)
        self.assertEqual([(item.operation_id, item.cls) for item in index.blocking],
                         [(update["operation_id"], "blocking")])
        self.assertEqual(index.superseded, ())

    def test_os8b_a_same_second_superseding_operation_orders_by_its_plan(self):
        # Regression (PF-A3.2 implementation): operation IDs of one second order by kind, not by time.
        update = pfx.lifecycle_plan(self.context, "update", op=pfx.operation_id("update", "0000aaaa", 9))
        write_op(self.root, update, pfx.lifecycle_journal(update, phase="activating", unresolved="e0008",
                                                          states=dict(upto("e0008"), e0008="unknown")))
        rollback = pfx.lifecycle_plan(self.context, "rollback", op=pfx.operation_id("rollback", "0000bbbb", 9),
                                      supersedes=update["operation_id"])
        write_op(self.root, rollback, pfx.lifecycle_journal(rollback, phase="completed",
                                                            states={item["effect_id"]: "complete"
                                                                    for item in rollback["effects"]}))
        self.assertLess(rollback["operation_id"], update["operation_id"])
        index = index_of(self.root)
        self.assertEqual(index.blocking, ())
        self.assertEqual([item.operation_id for item in index.superseded], [update["operation_id"]])

    def test_os9_two_blocking_operations_refuse_every_route(self):
        update = self.plan("update")
        write_op(self.root, update, pfx.lifecycle_journal(update, phase="preparing", states={"e0001": "unknown"},
                                                          unresolved="e0001"))
        deploy = self.plan("deploy")
        write_op(self.root, deploy, pfx.lifecycle_journal(deploy, phase="preparing", states={"e0001": "unknown"},
                                                          unresolved="e0001"))
        index = index_of(self.root)
        self.assertTrue(index.conflict)
        for route in [name for name, item in pf.DISPATCH.items() if item.lock]:
            with self.subTest(route=route):
                decision = gate(index, route)
                self.assertEqual((decision.action, decision.code), ("refuse", "operation-conflict"))
                self.assertIn("2 operations of instance staging are open", decision.message)

    def test_os10_pending_json(self):
        permissions = json.dumps({"operation": "permissions", "operation_id": "op-1", "phase": "applying"}).encode()
        index = index_of(self.root, permissions=permissions)
        self.assertEqual(index.permissions["phase"], "applying")
        self.assertIsNone(index.legacy)
        self.assertEqual(gate(index, "permissions apply").action, "new")
        refused = gate(index, "resume")
        self.assertEqual(refused.code, "operation-open")
        self.assertIn("pf --instance staging permissions apply --resume", refused.message)
        for data in (json.dumps({"operation": "update", "phase": "paused"}).encode(), b"{not json"):
            with self.subTest(data=data):
                index = index_of(self.root, permissions=data)
                self.assertIsNotNone(index.legacy)
                for route in ("resume", "update", "permissions apply", "backup"):
                    self.assertEqual(gate(index, route).code, "journal-format-unsupported")

    def test_os11_private_lists_are_atomic_and_normalized(self):
        path = self.root / "attempts.json"
        entries = [{"b": 2, "a": 1}]
        pf_instance.rewrite_private_list(path, entries)
        self.assertEqual(path.read_bytes(), pf_instance.normalize_json(entries))
        self.assertEqual(stat.S_IMODE(os.lstat(str(path)).st_mode), 0o600)
        before = path.read_bytes()
        with mock.patch.object(pf_instance.os, "replace", side_effect=OSError(5, "crash")):
            with self.assertRaises(OSError):
                pf_instance.rewrite_private_list(path, entries + [{"c": 3}])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(str(self.root))), ["attempts.json"])
        self.assertEqual(pf_instance.read_private_list(path), entries)
        self.assertEqual(pf_instance.read_private_list(self.root / "absent.json"), [])
        (self.root / "bad.json").write_text("{}")
        with self.assertRaises(pf_instance.ContextError):
            pf_instance.read_private_list(self.root / "bad.json")

    def test_os12_boot_id_and_start_ticks(self):
        boot = pf_instance.boot_id()
        self.assertRegex(boot, r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
        ticks = pf_instance.process_start_ticks(os.getpid())
        self.assertIsInstance(ticks, int)
        self.assertEqual(pf_instance.process_start_ticks(os.getpid()), ticks)
        child = subprocess.Popen(["sleep", "5"])
        try:
            self.assertNotEqual(pf_instance.process_start_ticks(child.pid), None)
        finally:
            child.kill()
            child.wait()
        self.assertIsNone(pf_instance.process_start_ticks(2 ** 22 + 7))
        with mock.patch.object(pf_instance, "BOOT_ID_PATH", str(self.root / "absent")):
            self.assertIsNone(pf_instance.boot_id())

    def pending_switch(self):
        plan = self.plan("update", workspace_mode="pending")
        ids = pf_config.workspace_effect_ids(plan)
        states = {item["effect_id"]: "complete" for item in plan["effects"] if item["effect_id"] not in ids}
        journal = pfx.lifecycle_journal(plan, phase="workspace_sync_pending", states=states)
        return plan, journal

    def test_os14_the_backup_and_pending_switch_pair(self):
        update, journal = self.pending_switch()
        write_op(self.root, update, journal)
        index = index_of(self.root)
        self.assertEqual(gate(index, "backup").action, "new")
        backup = pfx.lifecycle_plan(self.context, "backup", op=pfx.operation_id("backup", "0000b0b0", 30))
        write_op(self.root, backup, pfx.lifecycle_journal(backup, phase="capturing", unresolved="e0001",
                                                          states={"e0001": "unknown"}))
        index = index_of(self.root)
        self.assertFalse(index.conflict)
        self.assertEqual({item.operation_id for item in index.pair}, {update["operation_id"], backup["operation_id"]})
        backup_resume = f"pf --instance staging resume --operation {backup['operation_id']}"
        refused = gate(index, "resume")
        self.assertEqual(refused.code, "operation-conflict")
        self.assertIn(f"name the backup with '{backup_resume}'", refused.message)
        decision = gate(index, "resume", operation=backup["operation_id"])
        self.assertEqual((decision.action, decision.entry.operation_id), ("reenter", backup["operation_id"]))
        decision = gate(index, "resume", operation=backup["operation_id"], abandon=True)
        self.assertEqual(decision.action, "reenter")
        # Audit F1: the pending switch is frozen while the backup is open. Starting its W effects (or closing them
        # with --keep-workspace) would end the pair and leave two blocking operations that no route can resume.
        for request in ({}, {"keep_workspace": True}):
            with self.subTest(request=request):
                decision = gate(index, "resume", operation=update["operation_id"], **request)
                self.assertEqual((decision.action, decision.code), ("refuse", "operation-open"))
                self.assertTrue(decision.message.startswith(
                    f"operation-open: operation {update['operation_id']} (update, phase workspace_sync_pending) waits "
                    f"for backup operation {backup['operation_id']}"), decision.message)
                self.assertIn(f"Legal next: {backup_resume}: ", decision.message)
                self.assertIn(f"{backup_resume} --abandon: ", decision.message)
        for route in ("backup", "update", "rollback", "backup emergency", "purge"):
            with self.subTest(route=route):
                decision = gate(index, route)
                self.assertEqual(decision.code, "operation-open")
                self.assertIn(backup_resume, decision.message)
                self.assertNotIn(f"resume --operation {update['operation_id']}", decision.message)
        # Any other two-blocking combination stays a conflict.
        shutil.rmtree(str(self.root / update["operation_id"]))
        migrating = pfx.lifecycle_plan(self.context, "update", op=pfx.operation_id("update", "0000dddd", 40))
        write_op(self.root, migrating, pfx.lifecycle_journal(migrating, phase="migrating", unresolved="e0007",
                                                             states=dict(upto("e0007"), e0007="unknown")))
        index = index_of(self.root)
        self.assertIsNone(index.pair)
        self.assertTrue(index.conflict)
        self.assertEqual(gate(index, "resume", operation=migrating["operation_id"]).code, "operation-conflict")

    def test_os8c_a_superseding_plan_stamped_before_the_superseded_one_still_supersedes(self):
        # Audit F4: a wall clock stepped back between the two operations never leaves both blocking. A superseding
        # plan is only written while the operation it names is the single blocking one, so its stamp proves nothing.
        update = dict(self.plan("update"), created_at="20261007T060000Z")
        write_op(self.root, update, pfx.lifecycle_journal(update, phase="migrating", unresolved="e0007",
                                                          states=dict(upto("e0007"), e0007="unknown")))
        rollback = dict(pfx.lifecycle_plan(self.context, "rollback", op=pfx.operation_id("rollback", "0000cafe", 5),
                                           supersedes=update["operation_id"]), created_at="20261007T055900Z")
        directory = write_op(self.root, rollback, pfx.lifecycle_journal(rollback, phase="preserving-current",
                                                                        states={"e0001": "complete"}))
        index = index_of(self.root)
        self.assertFalse(index.conflict)
        self.assertEqual([(item.kind, item.cls) for item in index.blocking], [("rollback", "blocking")])
        self.assertEqual([(item.operation_id, item.superseded_by) for item in index.superseded],
                         [(update["operation_id"], rollback["operation_id"])])
        self.assertEqual(gate(index, "resume").action, "reenter")
        completed = pfx.lifecycle_journal(rollback, phase="completed", sequence=2,
                                          states={item["effect_id"]: "complete" for item in rollback["effects"]})
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(completed))
        index = index_of(self.root)
        self.assertEqual(index.blocking, ())
        self.assertEqual([item.operation_id for item in index.superseded], [update["operation_id"]])

    def test_os15_runner_records_of_a_chained_supersession(self):
        # Audit F8: U <- R1 <- R2. U's records are reconciled by the end of the chain, never left open forever.
        update = self.plan("update")
        write_op(self.root, update, pfx.lifecycle_journal(update, phase="migrating", unresolved="e0007",
                                                          states=dict(upto("e0007"), e0007="unknown")))
        first = pfx.lifecycle_plan(self.context, "rollback", op=pfx.operation_id("rollback", "0000cafe", 5),
                                   supersedes=update["operation_id"])
        write_op(self.root, first, pfx.lifecycle_journal(
            first, phase="needs_operator", unresolved="e0002", states=dict(upto("e0002"), e0002="unknown"),
            last_error={"code": "effect-unknown", "message": "x"},
            result={"outcome": "needs_operator", "deployment_id": None}))
        second = pfx.lifecycle_plan(self.context, "rollback", op=pfx.operation_id("rollback", "0000beef", 7),
                                    supersedes=first["operation_id"])
        directory = write_op(self.root, second, pfx.lifecycle_journal(second, phase="preserving-current",
                                                                      states={"e0001": "complete"}))
        index = index_of(self.root)
        states = {item.operation_id: pf_config.runner_records_state(item, index) for item in index.entries}
        self.assertEqual(states[update["operation_id"]], ("open", None))
        self.assertEqual(states[first["operation_id"]], ("open", None))
        completed = pfx.lifecycle_journal(second, phase="completed", sequence=2,
                                          states={item["effect_id"]: "complete" for item in second["effects"]})
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(completed))
        index = index_of(self.root)
        self.assertEqual(index.blocking, ())
        states = {item.operation_id: pf_config.runner_records_state(item, index) for item in index.entries}
        self.assertEqual(states[update["operation_id"]], ("reconciled", 2))
        self.assertEqual(states[first["operation_id"]], ("reconciled", 2))
        self.assertEqual(pf_config.final_superseder(index, index.entry(update["operation_id"])).operation_id,
                         second["operation_id"])


# ============================================================================ RO: route gate and legal next steps


LOCKED_ROUTES = tuple(name for name, item in pf.DISPATCH.items() if item.lock)
CTX = fixture_context()


def ws_ids(plan):
    return pf_config.workspace_effect_ids(plan)


def route_fixtures():
    """(label, plan, journal, request overrides) of the RO-1 matrix: every kind in its characteristic phases."""
    rows = []

    def add(label, kind, phase, states, *, unresolved=None, deletion=None, plan=None, **plan_kwargs):
        plan = plan or pfx.lifecycle_plan(CTX, kind, **plan_kwargs)
        journal = pfx.lifecycle_journal(plan, phase=phase, states=states, unresolved=unresolved, deletion=deletion)
        rows.append((label, plan, journal))

    add("deploy/initializing", "deploy", "initializing", dict(upto("e0003"), e0003="unknown"), unresolved="e0003")
    add("deploy/activating frontend", "deploy", "activating", dict(upto("e0005"), e0005="unknown"),
        unresolved="e0005")
    add("update/preparing", "update", "preparing", {"e0001": "unknown"}, unresolved="e0001")
    add("update/preserving", "update", "preserving", dict(upto("e0003"), e0003="unknown"), unresolved="e0003")
    add("update/migrating", "update", "migrating", dict(upto("e0007"), e0007="unknown"), unresolved="e0007")
    update = pfx.lifecycle_plan(CTX, "update")
    needs = pfx.lifecycle_journal(update, phase="needs_operator", states=dict(upto("e0007"), e0007="unknown"),
                                  unresolved="e0007", last_error={"code": "effect-unknown", "message": "heads r1"},
                                  result={"outcome": "needs_operator", "deployment_id": None})
    rows.append(("update/needs_operator", update, needs))
    pending = pfx.lifecycle_plan(CTX, "update", workspace_mode="pending")
    ids = ws_ids(pending)
    add("update/workspace_sync_pending", "update", "workspace_sync_pending",
        {item["effect_id"]: "complete" for item in pending["effects"] if item["effect_id"] not in ids}, plan=pending)
    switch = pfx.lifecycle_plan(CTX, "update")
    ids = ws_ids(switch)
    add("update/syncing-workspace interval", "update", "syncing-workspace",
        dict(upto(ids[2]), **{ids[2]: "unknown"}), unresolved=ids[2], plan=switch)
    add("update/syncing-workspace W1", "update", "syncing-workspace", dict(upto(ids[0]), **{ids[0]: "unknown"}),
        unresolved=ids[0], plan=switch)
    add("rollback/switching", "rollback", "switching", dict(upto("e0005"), e0005="unknown"), unresolved="e0005")
    add("reset-db/initializing", "reset-db", "initializing", dict(upto("e0003"), e0003="unknown"), unresolved="e0003")
    add("backup/capturing", "backup", "capturing", {"e0001": "unknown"}, unresolved="e0001")
    add("purge/preserving", "purge", "preserving", {"e0001": "unknown"}, unresolved="e0001")
    add("purge/deleting", "purge", "deleting", dict(upto("e0005"), e0005="unknown"), unresolved="e0005",
        deletion=PURGE_DELETION)
    add("restore-instance/preparing-target", "restore-instance", "preparing-target",
        dict(upto("e0002"), e0002="unknown"), unresolved="e0002")
    add("abort-deploy/deleting", "abort-deploy", "deleting", {"e0001": "unknown"}, unresolved="e0001",
        supersedes=pfx.operation_id("deploy", "00000000", -100))
    return rows


# Section 3.3 per fixture: the routes that are not refused, with their decision (everything else: operation-open).
ROUTE_EXPECTATIONS = {
    "deploy/initializing": {"resume": "reenter", "abort-deploy": "supersede", "backup emergency": "new"},
    "deploy/activating frontend": {"resume": "reenter", "backup emergency": "new"},
    "update/preparing": {"resume": "reenter", "backup emergency": "new"},
    "update/preserving": {"resume": "reenter", "rollback": "supersede", "backup emergency": "new"},
    "update/migrating": {"resume": "reenter", "rollback": "supersede", "backup emergency": "new"},
    "update/needs_operator": {"rollback": "supersede", "backup emergency": "new"},
    "update/workspace_sync_pending": {"resume": "reenter", "rollback": "supersede", "backup": "new",
                                      "backup emergency": "new"},
    "update/syncing-workspace interval": {"resume": "reenter"},
    "update/syncing-workspace W1": {"resume": "reenter", "rollback": "supersede", "backup emergency": "new"},
    "rollback/switching": {"resume": "reenter", "rollback": "supersede", "backup emergency": "new"},
    "reset-db/initializing": {"resume": "reenter", "rollback": "supersede", "backup emergency": "new"},
    "backup/capturing": {"resume": "reenter", "backup": "reenter"},
    "purge/preserving": {"resume": "reenter"},
    "purge/deleting": {"resume": "reenter", "purge": "reenter"},
    "restore-instance/preparing-target": {"resume": "reenter", "restore-instance": "reenter"},
    "abort-deploy/deleting": {"resume": "reenter", "abort-deploy": "reenter"},
}


class Routes(unittest.TestCase):
    """RO-1..RO-4, RO-6, RO-7, RO-9, RO-10, RO-12, RO-13: the pure section 3.3 gate and section 3.6 routes."""

    def test_ro1_the_gate_matrix(self):
        table = []
        fixtures = route_fixtures()
        self.assertEqual({label for label, _, _ in fixtures}, set(ROUTE_EXPECTATIONS))
        for label, plan, journal in fixtures:
            index = single_index(plan, journal)
            self.assertEqual(len(index.blocking), 1, label)
            for route in LOCKED_ROUTES:
                with self.subTest(fixture=label, route=route):
                    request = {"bundle_id": pfx.BUNDLE_ID} if route == "restore-instance" else {}
                    decision = gate(index, route, **request)
                    expected = ROUTE_EXPECTATIONS[label].get(route)
                    if expected is None:
                        wanted = "operation-needs-operator" if route == "resume" else "operation-open"
                        self.assertEqual((decision.action, decision.code), ("refuse", wanted))
                        self.assertTrue(decision.message.endswith("Nothing was changed."))
                    else:
                        self.assertEqual(decision.action, expected)
                    table.append({"fixture": label, "route": route, "action": decision.action,
                                  "code": decision.code or None})
        evidence("ROUTE-TABLE-1", {"routes": list(LOCKED_ROUTES), "rows": table})

    def test_ro2_exact_legal_next_strings(self):
        rows = {label: (plan, journal) for label, plan, journal in route_fixtures()}
        plan, journal = rows["update/migrating"]
        op = plan["operation_id"]
        self.assertEqual(journal["legal_next"], [
            f"pf --instance staging resume --operation {op}",
            f"pf --instance staging rollback {pfx.CHECKPOINT_ID} --restore-db",
            "pf --instance staging backup --emergency"])
        plan, journal = rows["deploy/initializing"]
        self.assertEqual(journal["legal_next"], [
            f"pf --instance staging resume --operation {plan['operation_id']}", "pf --instance staging abort-deploy",
            "pf --instance staging backup --emergency"])
        plan, journal = rows["update/preserving"]
        self.assertEqual(journal["legal_next"], [
            f"pf --instance staging resume --operation {plan['operation_id']}",
            f"pf --instance staging resume --operation {plan['operation_id']} --abandon",
            "pf --instance staging rollback <checkpoint> --restore-db", "pf --instance staging backup --emergency"])
        plan, journal = rows["restore-instance/preparing-target"]
        self.assertEqual(journal["legal_next"], [
            f"pf --instance staging resume --operation {plan['operation_id']}",
            f"pf --instance staging resume --operation {plan['operation_id']} --abandon",
            f"pf --instance staging restore-instance {pfx.BUNDLE_ID}"])
        for label, (plan, journal) in rows.items():
            with self.subTest(fixture=label):
                self.assertEqual(journal["legal_next"], pf_config.legal_next(plan, journal, slug=SLUG))

    def test_ro3_resume_on_needs_operator(self):
        rows = {label: (plan, journal) for label, plan, journal in route_fixtures()}
        plan, journal = rows["update/needs_operator"]
        decision = gate(single_index(plan, journal), "resume")
        self.assertEqual(decision.code, "operation-needs-operator")
        self.assertEqual(decision.message,
                         f"operation-needs-operator: operation {plan['operation_id']} (update) stopped at migrating: "
                         "heads r1 No automatic continuation is safe. Supported next steps: pf --instance staging "
                         f"rollback {pfx.CHECKPOINT_ID} --restore-db; pf --instance staging backup --emergency. Nothing "
                         "was changed.")

    def test_ro4_permissions_and_lifecycle_never_mix(self):
        permissions = json.dumps({"operation": "permissions", "operation_id": "20261007T040000Z-permissions-1",
                                  "phase": "interrupted"}).encode()
        index = pf_config.classify_operations([], permissions_journal=permissions, validate=validate)
        decision = gate(index, "resume")
        self.assertEqual(decision.message,
                         "operation-open: operation 20261007T040000Z-permissions-1 (permissions, phase interrupted) is "
                         "incomplete; 'resume' is not a legal next action for it. Legal next: pf --instance staging "
                         "permissions apply --resume: finish the interrupted permission apply; pf --instance staging "
                         "permissions apply --abandon: compensate it from its effect journal. Nothing was changed.")
        rows = {label: (plan, journal) for label, plan, journal in route_fixtures()}
        plan, journal = rows["update/migrating"]
        decision = gate(single_index(plan, journal), "permissions apply")
        self.assertEqual(decision.code, "operation-open")
        self.assertTrue(decision.message.startswith(
            f"operation-open: operation {plan['operation_id']} (update, phase migrating) is incomplete; 'permissions "
            "apply' is not a legal next action for it."))

    def test_ro6_aliases(self):
        rows = {label: (plan, journal) for label, plan, journal in route_fixtures()}
        plan, journal = rows["purge/deleting"]
        index = single_index(plan, journal)
        self.assertEqual((gate(index, "purge").action, gate(index, "purge").alias), ("reenter", "purge"))
        self.assertEqual(gate(index, "purge", delete_backups=True).action, "reenter")
        refused = gate(index, "purge", delete_backups=False)
        self.assertEqual(refused.message,
                         "plan-inputs-conflict: 'purge --keep-backups' asks for --keep-backups, but the open purge "
                         f"operation {plan['operation_id']} was approved with delete backups. Run 'pf --instance "
                         f"staging resume --operation {plan['operation_id']}' or finish it first. Nothing was changed.")
        self.assertEqual(gate(index, "purge", reset_admin_config=True).code, "plan-inputs-conflict")
        plan, journal = rows["restore-instance/preparing-target"]
        index = single_index(plan, journal)
        self.assertEqual(gate(index, "restore-instance", bundle_id=pfx.BUNDLE_ID).alias, "restore-instance")
        other = gate(index, "restore-instance", bundle_id="purge-20261001T000000Z-" + "2" * 12 + "-abcdef")
        self.assertEqual(other.code, "plan-inputs-conflict")
        self.assertEqual(gate(index, "restore-instance", bundle_id=pfx.BUNDLE_ID, side_by_side=True).code,
                         "plan-inputs-conflict")
        self.assertIn("an interactively chosen bundle", gate(index, "restore-instance").message)
        for label, route in (("abort-deploy/deleting", "abort-deploy"), ("backup/capturing", "backup")):
            plan, journal = rows[label]
            decision = gate(single_index(plan, journal), route)
            self.assertEqual((decision.action, decision.alias, decision.entry.operation_id),
                             ("reenter", route, plan["operation_id"]))

    def test_ro7_rollback_supersedes_only_data_operations(self):
        for kind, phase, states in (("update", "preserving", dict(upto("e0003"), e0003="unknown")),
                                    ("rollback", "switching", dict(upto("e0005"), e0005="unknown")),
                                    ("reset-db", "initializing", dict(upto("e0003"), e0003="unknown")),
                                    ("deploy", "initializing", dict(upto("e0003"), e0003="unknown")),
                                    ("backup", "capturing", {"e0001": "unknown"}),
                                    ("purge", "preserving", {"e0001": "unknown"})):
            with self.subTest(kind=kind):
                plan = pfx.lifecycle_plan(CTX, kind)
                journal = pfx.lifecycle_journal(plan, phase=phase, states=states,
                                                unresolved=[key for key, value in states.items()
                                                            if value == "unknown"][0])
                decision = gate(single_index(plan, journal), "rollback")
                self.assertEqual(decision.action,
                                 "supersede" if kind in ("update", "rollback", "reset-db") else "refuse")

    def test_ro9_resume_selection(self):
        rows = {label: (plan, journal) for label, plan, journal in route_fixtures()}
        plan, journal = rows["update/migrating"]
        index = single_index(plan, journal)
        self.assertEqual(gate(index, "resume").entry.operation_id, plan["operation_id"])
        self.assertEqual(gate(index, "resume", operation=plan["operation_id"]).action, "reenter")
        empty = pf_config.classify_operations([], permissions_journal=None, validate=validate)
        self.assertEqual(gate(empty, "resume").message,
                         "nothing-to-resume: No incomplete operation exists. Nothing was changed.")
        closed = pfx.lifecycle_journal(plan, phase="cancelled", states=dict(upto("e0007"), e0007="complete"))
        index = single_index(plan, closed)
        self.assertEqual(gate(index, "resume", operation=plan["operation_id"]).message,
                         f"operation-not-open: operation {plan['operation_id']} is cancelled; nothing to resume. "
                         "Nothing was changed.")
        self.assertEqual(gate(index, "resume", operation=pfx.operation_id("update", "ffffffff")).code,
                         "operation-not-found")

    def test_ro10_dispatch_rows_of_the_moved_pending_cells(self):
        self.assertEqual(pf.DISPATCH["backup"].pending, "backup")
        self.assertEqual(pf.DISPATCH["restore-instance"].pending, "restore-instance")
        self.assertEqual(pf.DISPATCH["resume"].handler, "Controller.resume_operation")
        self.assertEqual(pf.DISPATCH["backup"].handler, "Controller.backup_operation")
        e5 = next(item for item in pf.ENTRY_ROUTES if item.id == "E5")
        self.assertEqual(e5.pending, "backup")
        for name in ("backup", "restore-instance", "purge", "abort-deploy", "rollback", "resume", "backup emergency",
                     "permissions apply"):
            self.assertIn(name, pf.PENDING_ROUTES)

    def test_ro12_the_workspace_switch_interval_admits_only_resume(self):
        rows = {label: (plan, journal) for label, plan, journal in route_fixtures()}
        plan, journal = rows["update/syncing-workspace interval"]
        self.assertTrue(pf_config.in_workspace_switch(plan, journal))
        index = single_index(plan, journal)
        op = plan["operation_id"]
        for route in LOCKED_ROUTES:
            with self.subTest(route=route):
                decision = gate(index, route)
                if route == "resume":
                    self.assertEqual(decision.action, "reenter")
                    continue
                self.assertEqual(decision.code, "operation-open")
                self.assertIn(f"Legal next: pf --instance staging resume --operation {op}: finish the workspace switch "
                              f"(bind the staged tree, then the source manifest); pf --instance staging resume "
                              f"--operation {op} --keep-workspace: rebind the old workspace tree and keep it (the "
                              "workspace is not refreshed). Nothing was changed.", decision.message)
        self.assertEqual(journal["legal_next"], [f"pf --instance staging resume --operation {op}",
                                                 f"pf --instance staging resume --operation {op} --keep-workspace"])

    def test_ro13_no_dead_end(self):
        """Every reachable (kind, phase, effect-state) journal prints at least one legal next command."""
        checked, documented = 0, []
        for kind in ("deploy", "update", "rollback", "reset-db", "backup", "purge", "restore-instance",
                     "abort-deploy"):
            kwargs = {"supersedes": pfx.operation_id("deploy", "00000000", -100)} if kind == "abort-deploy" else {}
            for mode in (("switch", "pending") if kind in pf_config.SWITCH_KINDS else (None,)):
                plan = pfx.lifecycle_plan(CTX, kind, workspace_mode=mode, **kwargs)
                for effect in plan["effects"]:
                    eid = effect["effect_id"]
                    for state in ("unknown", "partial", "not_started"):
                        states = dict(upto(eid), **{eid: state})
                        deletion = None
                        if kind == "purge" and effect["phase"] in ("deleting", "finalizing"):
                            deletion = PURGE_DELETION
                        journal = pfx.lifecycle_journal(plan, phase=effect["phase"], states=states,
                                                        unresolved=eid if state != "not_started" else None,
                                                        deletion=deletion)
                        with self.subTest(kind=kind, mode=mode, effect=eid, state=state):
                            self.assertTrue(journal["legal_next"], (kind, eid, state))
                            checked += 1
                if kind in ("deploy", "update", "rollback", "reset-db"):
                    last = plan["effects"][2]["effect_id"]
                    journal = pfx.lifecycle_journal(
                        plan, phase="needs_operator", states=dict(upto(last), **{last: "unknown"}), unresolved=last,
                        last_error={"code": "effect-unknown", "message": "x"},
                        result={"outcome": "needs_operator", "deployment_id": None})
                    self.assertTrue(journal["legal_next"], kind)
                    checked += 1
        # restore-instance abandon in progress: the single documented continuation.
        plan = pfx.lifecycle_plan(CTX, "restore-instance")
        journal = pfx.lifecycle_journal(plan, phase="preparing-target", states=dict(upto("e0004"), e0004="complete"),
                                        deletion=dict(PURGE_DELETION, delete_backups=False))
        self.assertEqual(journal["legal_next"],
                         [f"pf --instance staging resume --operation {plan['operation_id']} --abandon"])
        # The documented procedures (SYNOLOGY_ADMIN section 16): a purge whose bundle no longer verifies keeps its
        # routes (resume re-observes), and runner records of no-journal directories are refused by pf install.
        documented = ["purge/deleting with an unreadable bundle -> SYNOLOGY_ADMIN §16 (purge bundle no longer "
                      "verifies)", "no-journal runner records -> SYNOLOGY_ADMIN §15/§16 (instance-effects-unresolved)"]
        guide = (pfx.PACKAGE.parents[1] / "docs/deployment/SYNOLOGY_ADMIN.md")
        if guide.exists():
            text = guide.read_text(encoding="utf-8")
            self.assertIn("instance-effects-unresolved", text)
            self.assertIn("no longer reads or verifies", text)
        self.assertGreater(checked, 300)
        evidence("RO-13", {"checked_states": checked, "documented_procedures": documented})

    def test_ro14_the_documented_sources_and_exits_of_audit_limits(self):
        """Audit F5 and F1: the administrator guide (both languages) names every source of journal-less runner records
        (a child interrupted or timed out before the confirmation) and the wait of a switch paired with a backup."""
        docs = pfx.PACKAGE.parents[1] / "docs/deployment"
        if not (docs / "SYNOLOGY_ADMIN.md").exists():
            self.skipTest("the administrator guide is not part of this tree")
        english = (docs / "SYNOLOGY_ADMIN.md").read_text(encoding="utf-8")
        vietnamese = (docs / "SYNOLOGY_ADMIN.vi.md").read_text(encoding="utf-8")
        for text in (english, vietnamese):
            self.assertIn("`compose build`", text)
            self.assertIn("ensure_local_contract", text)
            self.assertIn("waits: backup operation", text)
        self.assertIn("interrupted or timed out before the confirmation", english)
        self.assertIn("bị ngắt hoặc hết thời gian trước khi xác nhận", vietnamese)


# ============================================================================ controller-level store and routes


class StoreInstance(ta.Base):
    """OS-4, OS-13, RO-8, RO-11 through the real controller (FakeController simulates only programs)."""

    def bind(self, plan):
        """Bind the controller to an operation directory as a re-entry does (no lock needed for the writer)."""
        _, journal = pfx.operation(self.context, plan["operation_id"])
        self.c.operation_id = plan["operation_id"]
        self.c.operation_dir = self.context.operations_dir / plan["operation_id"]
        self.c.plan, self.c.journal = plan, journal
        self.c.plan_sha256 = journal["plan_sha256"]

    def test_os4_journal_changed_when_the_sequence_moved(self):
        plan = pfx.frozen_operation(self.context, "update", phase="preparing", states={"e0001": "complete"})
        self.bind(plan)
        moved = pfx.lifecycle_journal(plan, phase="preserving", states={"e0001": "complete"}, sequence=2)
        pf_instance.write_journal_generation(self.c.operation_dir, pf_instance.normalize_json(moved))
        before = pfx.operation_files(self.context, plan["operation_id"])
        with self.assertRaisesRegex(pf.Failure, "^journal-changed: the journal of operation "
                                    + plan["operation_id"] + r" changed under this process \(sequence 1 expected, 2 "
                                    r"found\); it stopped before its next effect."):
            self.c.journal_update(phase="preserving")
        self.assertEqual(pfx.operation_files(self.context, plan["operation_id"]), before)
        os.unlink(str(self.c.operation_dir / "journal.json"))
        with self.assertRaisesRegex(pf.Failure, "^journal-changed: the journal of operation .* cannot be re-read"):
            self.c.journal_update(phase="preserving")

    def test_os13_index_overflow(self):
        for index in range(6):
            (self.context.operations_dir / pfx.operation_id("backup", f"{index:08x}", index)).mkdir(mode=0o700)
        with mock.patch.object(pf_instance, "OPERATION_SCAN_LIMIT", 5):
            index = self.c.operation_index()
            self.assertEqual(index.overflow, 6)
            for arguments in (["update", "--latest"], ["backup"], ["resume"]):
                with self.subTest(arguments=arguments):
                    self.assertEqual(self.invoke(arguments), 1)
                    self.assertIn("ERROR: operation-index-overflow: " + str(self.context.paths.private_state)
                                  + "/operations holds more than 20000 entries", self.last_error)
            with mock.patch.object(self.c, "compose", return_value="NAME  STATUS"), \
                    mock.patch.object(self.c, "describe_envelope", return_value="ok (fixture)"):
                self.invoke(["status"])
            self.assertIn("Operations: index overflow (6 entries in operations/; mutating routes are refused, see "
                          "SYNOLOGY_ADMIN §16)", self.output.getvalue())
            report = pf_install._Report()
            pf_install._instance_journal_checks(report, [self.context])
            self.assertEqual([item.code for item in report.conflicts], ["instance-operation-pending"])
            self.assertIn("operation-index-overflow", report.conflicts[0].detail)
        self.assertEqual(len(os.listdir(str(self.context.operations_dir))), 6)  # nothing truncated or removed

    def test_ro8_code_only_rollback_over_a_database_effect_needs_restore_db(self):
        self.c.new_migration = True
        self.c.fail = "live-migration"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        self.c.fail = None
        op, plan, _ = tpa.latest_operation(self.c, "update")
        before = pfx.operations_bytes(self.context)
        selected = next(item for item in self.c.snapshots() if item.reason == "before-update").bundle_id
        self.assertEqual(self.invoke(["rollback", selected]), 1)
        self.assertIn("The incomplete operation may have changed data/schema; review recovery with --restore-db.",
                      self.last_error)
        self.assertEqual(pfx.operations_bytes(self.context), before)
        self.assertEqual(self.invoke(["rollback", selected, "--restore-db"]), 0, self.last_error)
        self.assertEqual(self.c.operation_index().entry(op).cls, "superseded")
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r1"])

    def test_ro11_backup_beside_a_pending_workspace_switch(self):
        plan = pfx.lifecycle_plan(self.context, "update", workspace_mode="pending")
        ids = ws_ids(plan)
        states = {item["effect_id"]: "complete" for item in plan["effects"] if item["effect_id"] not in ids}
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(plan, phase="workspace_sync_pending",
                                                                      states=states))
        before = pfx.operation_files(self.context, plan["operation_id"])
        self.assertEqual(self.invoke(["backup"]), 0, self.last_error)
        self.assertEqual(pfx.operation_files(self.context, plan["operation_id"]), before)
        _, backup, journal = tpa.latest_operation(self.c, "backup")
        self.assertEqual(journal["phase"], "completed")
        self.assertEqual(tpa.open_operations(self.c), [("update", "workspace_sync_pending")])
        # W1 started: the pending switch is no longer untouched -> operation-open, nothing written.
        pfx.clear_operations(self.context)
        states = dict(states, **{ids[0]: "unknown"})
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(plan, phase="syncing-workspace", states=states,
                                                                      unresolved=ids[0]))
        before = pfx.operations_bytes(self.context)
        self.assertEqual(self.invoke(["backup"]), 1)
        self.assertIn(f"ERROR: operation-open: operation {plan['operation_id']} (update, phase syncing-workspace) is "
                      "incomplete; 'backup' is not a legal next action for it.", self.last_error)
        self.assertEqual(pfx.operations_bytes(self.context), before)


# ============================================================================ RS: crash matrix and resume


CARRIED = ("dbs", "tags", "contracts", "current_images", "running", "resources", "target", "new_migration",
           "workspace_head", "workspace_dirty", "droppable", "connected_sessions")
CRASH_ROWS = []
CLI_CRASH_ROWS = []  # rows entered through the installed launcher (CliLifecycle)


class Restartable(ta.Base):
    """A FakeController instance that can 'die' (SimulatedCrash through the section 6 seam) and be replaced by a
    fresh controller carrying only what outlives a process: the files and the simulated daemon/database state."""

    def restart(self):
        # A new process: the fake's constructor reads the workspace migrations, which may be absent in the
        # workspace-switch interval; the carried contracts replace them anyway.
        with mock.patch.object(pf, "migration_files", return_value={}):
            fresh = tpa.FakeController(self.context)
        for name in CARRIED:
            setattr(fresh, name, copy.deepcopy(getattr(self.c, name)))
        self.history = getattr(self, "history", []) + self.c.calls
        self.c = fresh
        # Program simulations bound to one controller object follow it into the new process.
        for patch in getattr(self, "controller_patches", ()):
            self.stack.enter_context(patch(self))
        return fresh

    def all_calls(self):
        return getattr(self, "history", []) + self.c.calls

    def crash(self, arguments, effect, point):
        """Run ``arguments`` with the seam armed at ``(effect, point)``; the process dies there (lock released by the
        finally, as an exiting process releases its flock). ``effect`` is an effect ID or a target prefix."""
        if re.fullmatch(r"e[0-9]{4}", effect):
            self.c._crash_point = (effect, point)
        else:
            controller = self.c

            def seam(effect_id, when):
                if when == point and controller.plan_effect(effect_id)["target"].startswith(effect):
                    raise pf.SimulatedCrash(f"{when} {effect_id}")

            self.c._crash_point = seam
        with self.assertRaises(pf.SimulatedCrash):
            self.invoke(arguments)
        self.restart()

    def alembic_calls(self, database=None):
        found = [call for call in self.all_calls() if call[0] == "compose" and call[1][0] == "run"
                 and "alembic" in call[1]]
        if database == "live":
            found = [call for call in found if call[2] is None]
        return found

    def assert_lock_free(self):
        handle = pf_instance.acquire_instance_lock(self.context)
        handle.release()

    def blocking(self):
        return tpa.open_operations(self.c)

    def observed_lines(self):
        return [line for line in self.output.getvalue().splitlines() if line.startswith(("observed:", "Resuming"))]


def update_setup(test):
    test.c.new_migration = True
    return ["update", "--latest", "--allow-migrations"]


def deploy_setup(test):
    tpa.write_deploy_env(test.root)
    (test.c.state / "deployed.json").unlink()
    test.c.dbs = {}
    test.c.running = {"db": False, "backend": False, "frontend": False}
    return ["deploy", "--latest"]


def reset_setup(test):
    return ["reset-db"]


def backup_setup(test):
    return ["backup"]


def rollback_setup(test):
    test.c.new_migration = True
    test.assertEqual(test.invoke(["update", "--latest", "--allow-migrations"]), 0, test.last_error)
    test.c.dbs["partflow_staging"]["rows"].append("newer-record")
    test.selected = next(item for item in test.c.snapshots() if item.reason == "before-update").bundle_id
    return ["rollback", test.selected, "--restore-db"]


def restore_setup(test):
    view = ta.PurgeBundle.build(test)
    test.view = view
    deployed = test.c.state / "deployed.json"
    if deployed.exists():
        deployed.unlink()
    test.c.dbs = {}
    test.c.running = {"db": False, "backend": False, "frontend": False}
    # Every database of the empty target is created by the restore itself, so a redo may drop it (RS-21).
    test.c.droppable = {"partflow_staging"} | {store["database"] for store in view.stores}
    for image in view.images.values():
        test.c.tags[image["reference"]] = image["id"]
    test.controller_patches = [ta.PurgeBundle.fake_command]
    test.stack.enter_context(ta.PurgeBundle.fake_command(test))
    return ["restore-instance", view.bundle_id]


def expect_update(index, point):
    if index < 7 or (index == 7 and point == "before-intent"):
        return "cancelled"
    if (index, point) == (7, "after-intent"):
        return "needs_operator"
    return "completed"


def expect_deploy(index, point):
    if index == 1 or (index == 2 and point == "before-intent"):
        return "cancelled"
    if (index, point) == (3, "after-intent"):
        return "needs_operator"
    return "completed"


def expect_reset(index, point):
    return "cancelled" if index < 3 or (index == 3 and point == "before-intent") else "completed"


def expect_rollback(index, point):
    return "cancelled" if index < 4 or (index == 4 and point == "before-intent") else "completed"


def expect_backup(index, point):
    return "cancelled" if (index, point) == (1, "before-intent") else "completed"


def expect_restore(index, point):
    return "cancelled" if index == 1 or (index == 2 and point == "before-intent") else "completed"


CRASH_KINDS = {
    "update": (update_setup, expect_update),
    "deploy": (deploy_setup, expect_deploy),
    "reset-db": (reset_setup, expect_reset),
    "rollback": (rollback_setup, expect_rollback),
    "backup": (backup_setup, expect_backup),
    "restore-instance": (restore_setup, expect_restore),
}


@ROOT_FS
class CrashMatrix(Restartable):
    """RS matrix (A3-T04 offline part): every effect of every journaled kind x {before-intent, after-intent,
    after-effect}; a fresh controller runs `resume` and the section 3.6 outcome, a valid closed journal, the released
    lock and the data oracles are asserted. Every row is recorded for CRASH_MATRIX.json."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    @classmethod
    def tearDownClass(cls):
        if CRASH_ROWS:
            evidence("CRASH_MATRIX-offline", {"rows": CRASH_ROWS})

    def fresh(self):
        self.tearDown()
        self.setUp()

    def oracle(self, kind, outcome, before):
        """The data oracles of one finished row."""
        dbs, running = self.c.dbs, self.c.running
        self.assertFalse([name for name in dbs if pf_config.CANDIDATE_RE.fullmatch(name)], sorted(dbs))
        self.assertFalse([name for name in dbs if name.startswith("pf_verify_")], sorted(dbs))
        if kind == "update":
            self.assertEqual(dbs["partflow_staging"]["rows"], ["old-record"])
            self.assertEqual(dbs["partflow_staging"]["heads"], ["r2"] if outcome == "completed" else ["r1"])
            self.assertTrue(all(running.values()), running)
            self.assertEqual(self.pointer()["sha"], NEW if outcome == "completed" else OLD)
        elif kind == "deploy":
            if outcome == "completed":
                self.assertEqual(dbs["partflow_staging"]["heads"], ["r1"])
                self.assertTrue(all(running.values()), running)
                self.assertEqual(self.pointer()["sha"], NEW)
            else:
                self.assertFalse(running["frontend"])
        elif kind == "reset-db":
            kept = [name for name in dbs if name.startswith("pf_keep_")]
            if outcome == "completed":
                self.assertEqual(dbs["partflow_staging"]["rows"], [])
                self.assertEqual([dbs[name]["rows"] for name in kept], [["old-record"]])
            else:
                self.assertEqual(dbs["partflow_staging"]["rows"], ["old-record"])
                self.assertEqual(kept, [])
            self.assertTrue(all(running.values()), running)
        elif kind == "rollback":
            kept = [name for name in dbs if name.startswith("pf_keep_")]
            if outcome == "completed":
                self.assertEqual((dbs["partflow_staging"]["heads"], dbs["partflow_staging"]["rows"]),
                                 (["r1"], ["old-record"]))
                self.assertEqual([dbs[name]["rows"] for name in kept], [["old-record", "newer-record"]])
                self.assertEqual(self.pointer()["sha"], OLD)
            else:
                self.assertEqual((dbs["partflow_staging"]["heads"], dbs["partflow_staging"]["rows"]),
                                 (["r2"], ["old-record", "newer-record"]))
                self.assertEqual(kept, [])
                self.assertEqual(self.pointer()["sha"], NEW)
            self.assertTrue(all(running.values()), running)
        elif kind == "backup":
            healthy = [item for item in self.c.snapshots() if not isinstance(item, pf.InvalidBundle)]
            self.assertEqual(len(healthy), 1 if outcome == "completed" else 0)
            self.assertEqual(dbs["partflow_staging"]["rows"], ["old-record"])
            self.assertTrue(all(running.values()), running)
        elif kind == "restore-instance":
            if outcome == "completed":
                self.assertEqual(dbs["partflow_staging"]["rows"], ["old-record"])
                self.assertTrue(all(running.values()), running)
            else:
                self.assertFalse(running["frontend"])
        self.assertEqual(self.c.dbs.get("pf_keep_20261001t000000z_abc123", {}).get("rows", ["kept"]), ["kept"])

    def run_row(self, kind, index, point):
        setup, expect = CRASH_KINDS[kind]
        arguments = setup(self)
        before = copy.deepcopy(self.c.dbs)
        effect = f"e{index:04d}"
        self.crash(arguments, effect, point)
        op, plan, journal = tpa.latest_operation(self.c, kind)
        target = plan["effects"][index - 1]
        left = {"phase": journal["phase"], "effect_state": pf_config.effect_state(journal, effect),
                "unresolved": journal["unresolved_effect"]}
        self.assertEqual(self.blocking(), [(kind, journal["phase"])])
        self.assertIn(f"pf --instance staging resume --operation {op}", journal["legal_next"])
        alembic_before = len(self.alembic_calls("live"))
        code = self.invoke(["resume"])
        _, plan, journal = tpa.latest_operation(self.c, kind)
        outcome = expect(index, point)
        observed = [line for line in self.observed_lines() if line.startswith("observed: " + effect)]
        row = {"kind": kind, "effect": effect, "type": target["type"], "target": target["target"],
               "injection": point, "entered_through": "in-process seam", "state_left": left,
               "observation": observed[0].split(": ", 2)[2] if observed else None,
               "action": next((line.rsplit(": ", 1)[1] for line in self.observed_lines()
                               if line.startswith("Resuming")), None),
               "outcome": journal["phase"], "exit": code, "test": "test_operations.CrashMatrix." + kind}
        CRASH_ROWS.append(row)
        self.assertEqual(journal["phase"], outcome, (row, self.last_error))
        self.assert_lock_free()
        self.assertEqual(validate(journal, "operation_journal", plan), [])
        if outcome == "needs_operator":
            self.assertEqual(code, 1)
            self.assertEqual(self.blocking(), [(kind, "needs_operator")])
            self.assertEqual(len(self.alembic_calls("live")), alembic_before)  # never retried
        else:
            self.assertEqual(code, 0, self.last_error)
            self.assertEqual(self.blocking(), [])
            self.oracle(kind, outcome, before)
        return row

    def run_kind(self, kind):
        self.fresh()
        self.assertEqual(self.invoke(CRASH_KINDS[kind][0](self)), 0, self.last_error)
        _, plan, journal = tpa.latest_operation(self.c, kind)
        self.assertEqual(journal["phase"], "completed")
        for index in range(1, len(plan["effects"]) + 1):
            for point in pf.CRASH_POINTS:
                with self.subTest(kind=kind, effect=index, point=point):
                    self.fresh()
                    self.run_row(kind, index, point)

    def test_rs_matrix_update(self):
        self.run_kind("update")

    def test_rs_matrix_deploy(self):
        self.run_kind("deploy")

    def test_rs_matrix_reset_db(self):
        self.run_kind("reset-db")

    def test_rs_matrix_rollback(self):
        self.run_kind("rollback")

    def test_rs_matrix_backup(self):
        self.run_kind("backup")

    def test_rs_matrix_restore_instance(self):
        self.run_kind("restore-instance")


class Resume(Restartable):
    """Named RS cases (section 6.1) through the real controller and FakeController: crash, restart, resume."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def migrate_crash(self, point):
        self.c.new_migration = True
        self.crash(["update", "--latest", "--allow-migrations"], "e0007", point)
        return tpa.latest_operation(self.c, "update")

    def test_rs1_a_committed_then_lost_live_migration_forwards_with_one_upgrade(self):
        op, plan, journal = self.migrate_crash("after-effect")
        self.assertEqual(pf_config.effect_state(journal, "e0007"), "unknown")
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r2"])
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertEqual(len(self.alembic_calls("live")), 1)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "completed")
        self.assertIn("observed: e0007 database-migrate database:partflow_staging:heads=r2: complete (heads r2)",
                      self.output.getvalue())

    def test_rs2_rs4_lost_result_with_pre_heads_needs_the_operator_then_rollback_restore_db(self):
        op, plan, journal = self.migrate_crash("after-intent")
        self.c.dbs["partflow_staging"]["rows"].append("written-after-crash")
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertEqual(self.alembic_calls("live"), [])
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "needs_operator")
        before_update = next(item for item in self.c.snapshots() if item.reason == "before-update").bundle_id
        self.assertIn(f"pf --instance staging rollback {before_update} --restore-db", journal["legal_next"])
        self.assertIn("effect-unknown: effect e0007 (database-migrate database:partflow_staging:heads=r2)",
                      self.last_error)
        # RS-4: the lossless route supersedes the update; the current data is preserved first.
        self.assertEqual(self.invoke(["rollback", before_update, "--restore-db"]), 0, self.last_error)
        self.assertEqual(self.c.operation_index().entry(op).cls, "superseded")
        self.assertEqual(self.blocking(), [])
        self.assertEqual((self.c.dbs["partflow_staging"]["heads"], self.c.dbs["partflow_staging"]["rows"]),
                         (["r1"], ["old-record"]))
        kept = [name for name in self.c.dbs if name.startswith("pf_keep_")]
        self.assertEqual([self.c.dbs[name]["rows"] for name in kept], [["old-record", "written-after-crash"]])
        status = self.output.getvalue()
        self.assertTrue(all(self.c.running.values()))
        self.assertNotIn("Traceback", status)

    def test_rs3_intermediate_heads_need_the_operator(self):
        op, plan, journal = self.migrate_crash("after-intent")
        self.c.dbs["partflow_staging"]["heads"] = ["r1b"]
        self.assertEqual(self.invoke(["resume"]), 1)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "needs_operator")
        self.assertEqual(self.alembic_calls("live"), [])

    def test_rs5_rs30_a_crash_in_preserving_reopens_unchanged_with_one_attempt_entry(self):
        self.crash(["update", "--latest"], "e0003", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "update")
        declined = mock.Mock(side_effect=pf.Failure("Confirmation did not match. Nothing was changed."))
        before = pfx.operation_files(self.context, op)
        self.assertEqual(self.invoke(["resume"], confirm=declined), 1)
        self.assertEqual(pfx.operation_files(self.context, op), before)  # a declined confirmation appends nothing
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "cancelled")
        attempts = json.loads((self.context.operations_dir / op / "attempts.json").read_bytes())
        self.assertEqual([item["action"] for item in attempts], ["open", "reopen-unchanged"])
        self.assertEqual([item["attempt"] for item in attempts], [1, 2])
        self.assertTrue(all(self.c.running.values()))
        self.assertEqual(self.pointer()["sha"], OLD)

    def test_rs6_the_rehearsal_candidate_is_dropped_by_its_plan_name_only(self):
        self.c.new_migration = True
        self.c.dbs["pf_migrate_" + "9" * 20] = {"heads": ["r1"], "rows": ["other"], "connections": True}
        self.crash(["update", "--latest", "--allow-migrations"], "e0005", "after-intent")
        _, plan, _ = tpa.latest_operation(self.c, "update")
        own = plan["effects"][3]["target"].split(":", 1)[1]
        self.assertIn(own, self.c.dbs)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertNotIn(own, self.c.dbs)
        self.assertEqual(self.c.dbs["pf_migrate_" + "9" * 20]["rows"], ["other"])

    def test_rs9_a_rollback_candidate_rerestores_and_a_tampered_bundle_refuses(self):
        rollback = rollback_setup(self)
        self.crash(rollback, "e0004", "after-effect")
        op, plan, _ = tpa.latest_operation(self.c, "rollback")
        folder = self.c.backups_dir / self.selected
        dump = folder / "database.dump"
        original = dump.read_bytes()
        os.chmod(str(dump), 0o600)
        dump.write_bytes(original + b"tampered")
        rows = copy.deepcopy(self.c.dbs["partflow_staging"])
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn("plan-input-changed", self.last_error)
        self.assertEqual(self.blocking(), [("rollback", "restoring-candidate")])
        self.assertEqual(self.c.dbs["partflow_staging"], rows)  # the active database is untouched
        dump.write_bytes(original)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r1"])
        self.assertFalse([name for name in self.c.dbs if name.startswith("pf_restore_")])

    def test_rs11_a_lost_initial_migration_then_abort_deploy_then_deploy_again(self):
        self.crash(deploy_setup(self), "e0003", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "deploy")
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertEqual(self.blocking(), [("deploy", "needs_operator")])
        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        self.assertEqual(self.invoke(["abort-deploy"]), 0, self.last_error)
        self.assertEqual(self.c.operation_index().entry(op).cls, "superseded")
        self.assertEqual(self.blocking(), [])
        self.assertEqual(self.c.resources["containers"], [])
        self.c.dbs = {}
        self.assertEqual(self.invoke(["deploy", "--latest"]), 0, self.last_error)
        self.assertEqual(self.pointer()["sha"], NEW)

    def test_rs12_after_the_frontend_intent_abort_deploy_is_refused_and_resume_completes(self):
        self.crash(deploy_setup(self), "e0005", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "deploy")
        before = pfx.operations_bytes(self.context)
        self.assertEqual(self.invoke(["abort-deploy"]), 1)
        self.assertIn(f"operation-open: operation {op} (deploy, phase activating) is incomplete; 'abort-deploy' is not "
                      "a legal next action for it.", self.last_error)
        self.assertEqual(pfx.operations_bytes(self.context), before)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertTrue(all(self.c.running.values()))

    def test_rs14_a_failed_pointer_write_stays_activating_without_a_stop_and_resume_rewrites_it(self):
        real = pf.write_json
        failed = []

        def write_json(path, value, *args, **kwargs):
            if Path(path).name == "deployed.json" and not failed:
                failed.append(path)
                raise OSError(28, "No space left on device")
            return real(path, value, *args, **kwargs)

        calls = len(self.c.calls)
        with mock.patch.object(pf, "write_json", side_effect=write_json):
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.blocking(), [("update", "activating")])
        # Activation completed before the pointer effect: fail_closed stops nothing (section 3.9).
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and call[1][0] == "stop"
                          and "frontend" in call[1] and self.c.calls.index(call) > self.activation_index(calls)])
        self.assertTrue(self.c.running["frontend"] and self.c.running["backend"])
        self.assertEqual(self.pointer()["sha"], OLD)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertEqual(self.pointer()["sha"], NEW)
        self.assertEqual(self.blocking(), [])

    def activation_index(self, start):
        return max(index for index, call in enumerate(self.c.calls) if index >= start and call[0] == "compose"
                   and call[1][0] == "up" and "frontend" in call[1])

    def test_rs15_a_backup_crash_in_capture_makes_a_new_attempt(self):
        def crash_in_dump_list(*args, **kwargs):
            raise pf.SimulatedCrash("inside the capture")

        with mock.patch.object(self.c, "write_dump_list", side_effect=crash_in_dump_list):
            with self.assertRaises(pf.SimulatedCrash):
                self.invoke(["backup"])
        self.restart()
        op, plan, journal = tpa.latest_operation(self.c, "backup")
        first = plan["effects"][0]["preconditions"][0].split(":", 1)[1]
        self.assertTrue((self.c.backups_dir / first).is_dir())
        self.assertEqual(self.invoke(["backup"]), 0, self.last_error)  # the alias re-enters
        _, _, journal = tpa.latest_operation(self.c, "backup")
        self.assertEqual(journal["phase"], "completed")
        self.assertIn({"kind": "bundle-attempt", "name": first, "sha256": None}, journal["retained_artifacts"])
        healthy = [item for item in self.c.snapshots() if not isinstance(item, pf.InvalidBundle)]
        self.assertEqual(len(healthy), 1)
        self.assertNotEqual(healthy[0].bundle_id, first)
        attempts = json.loads((self.context.operations_dir / op / "attempts.json").read_bytes())
        self.assertEqual([item["action"] for item in attempts], ["open", "alias:backup"])

    def test_rs16_a_backup_crash_in_verification_drops_its_candidate_and_verifies_again(self):
        real = self.c.drop_database

        def drop_database(name, *args, **kwargs):
            if name.startswith("pf_verify_"):
                raise pf.SimulatedCrash("inside the verification, after the candidate was restored and checked")
            return real(name, *args, **kwargs)

        with mock.patch.object(self.c, "drop_database", side_effect=drop_database):
            with self.assertRaises(pf.SimulatedCrash):
                self.invoke(["backup"])
        self.restart()
        leftover = [name for name in self.c.dbs if name.startswith("pf_verify_")]
        self.assertEqual(len(leftover), 1)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertFalse([name for name in self.c.dbs if name.startswith("pf_verify_")])
        _, _, journal = tpa.latest_operation(self.c, "backup")
        self.assertEqual(journal["phase"], "completed")
        view = [item for item in self.c.snapshots() if not isinstance(item, pf.InvalidBundle)][0]
        self.assertEqual(view.level, "data_restore_verified")

    def test_rs17_backup_abandon(self):
        self.crash(["backup"], "e0001", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "backup")
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        _, _, journal = tpa.latest_operation(self.c, "backup")
        self.assertEqual((journal["phase"], journal["result"]["outcome"]), ("cancelled", "cancelled"))
        attempts = json.loads((self.context.operations_dir / op / "attempts.json").read_bytes())
        self.assertEqual(attempts[-1]["action"], "abandon")
        self.assertEqual(self.blocking(), [])

    def test_rs26_a_client_session_refuses_a_database_effect_resume(self):
        op, plan, journal = self.migrate_crash("after-intent")
        self.c.connected_sessions = 1
        before = pfx.operation_files(self.context, op)
        confirm = mock.Mock(side_effect=AssertionError("no confirmation"))
        self.assertEqual(self.invoke(["resume"], confirm=confirm), 1)
        self.assertIn(f"effect-still-running: effect e0007 (database-migrate database:partflow_staging:heads=r2) of "
                      f"operation {op} still has a running database session on partflow_staging. resume never stops "
                      "it;", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)
        # The superseding route probes too and writes no plan.
        selected = next(item for item in self.c.snapshots() if item.reason == "before-update").bundle_id
        self.assertEqual(self.invoke(["rollback", selected, "--restore-db"], confirm=confirm), 1)
        self.assertIn("effect-still-running", self.last_error)
        self.assertEqual([plan["kind"] for _, plan, _ in pfx.operations_of(self.context)], ["update"])
        self.c.connected_sessions = 0
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn("effect-unknown", self.last_error)

    def test_rs26b_a_session_on_the_maintenance_database_refuses_a_database_switch_resume(self):
        self.crash(["reset-db"], "database-switch:", "after-intent")
        op, plan, _ = tpa.latest_operation(self.c, "reset-db")
        switch = next(item for item in plan["effects"] if item["type"] == "database-switch")
        probe = ("SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> "
                 "pg_backend_pid();")
        real = self.c.sql

        def sql(database, statement, **kwargs):
            # One client session on `postgres` (the swap statement's): the cluster-wide probe counts it, while no
            # session is on the switched database itself.
            if statement == probe:
                return "1"
            if "pg_stat_activity WHERE datname" in statement:
                return "0"
            return real(database, statement, **kwargs)

        before = pfx.operation_files(self.context, op)
        confirm = mock.Mock(side_effect=AssertionError("no confirmation"))
        with mock.patch.object(self.c, "sql", side_effect=sql) as patched:
            self.assertEqual(self.invoke(["resume"], confirm=confirm), 1)
        self.assertIn(mock.call("postgres", probe), patched.call_args_list)
        self.assertIn(f"effect-still-running: effect {switch['effect_id']} (database-switch {switch['target']}) of "
                      f"operation {op} still has a running database session on partflow_staging.", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)  # the session ended: names unchanged -> forward
        _, _, journal = tpa.latest_operation(self.c, "reset-db")
        self.assertEqual(journal["phase"], "completed")

    def test_rs27_plan_authority_changed(self):
        self.crash(["update", "--latest"], "e0003", "after-intent")
        op, plan, _ = tpa.latest_operation(self.c, "update")
        before = pfx.operation_files(self.context, op)
        cases = {"control release": dict(control=types.SimpleNamespace(release_id="pf-other", sha256="9" * 64,
                                                                         path=self.context.control.path)),
                 "instance record": dict(record_sha256="8" * 64),
                 "environment policy": dict(approved_policy=types.SimpleNamespace(revision=9, sha256="7" * 64)),
                 "profile": dict(profile=types.SimpleNamespace(id="p", version="v", sha256="6" * 64)),
                 "daemon engine": dict(daemon=types.SimpleNamespace(engine_id="OTHER-ENGINE",
                                                                    endpoint=self.context.daemon.endpoint))}
        for field, change in cases.items():
            with self.subTest(field=field):
                fake = types.SimpleNamespace(**{**{key: getattr(self.context, key) for key in
                                                   ("record_sha256", "approved_policy", "control", "profile",
                                                    "daemon")}, **change})
                with mock.patch.object(self.c, "context", fake), \
                        mock.patch.object(self.c, "permission_policy_ref", return_value=plan["permission_policy"]):
                    with self.assertRaisesRegex(pf.Failure, f"^plan-authority-changed: {field} changed after "
                                                f"operation {op} was approved"):
                        self.c.check_authority(plan)
        with mock.patch.object(self.c, "permission_policy_ref", return_value={"revision": 2, "sha256": "5" * 64}):
            with self.assertRaisesRegex(pf.Failure, "^plan-authority-changed: permission policy changed"):
                self.c.check_authority(plan)
            self.assertEqual(self.invoke(["resume"]), 1)
            self.assertIn("plan-authority-changed: permission policy changed", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)

    def test_rs28_abandon_after_a_live_database_effect_is_not_legal(self):
        op, plan, journal = self.migrate_crash("after-effect")
        before = pfx.operation_files(self.context, op)
        self.assertEqual(self.invoke(["resume", "--abandon"]), 1)
        self.assertIn(f"abandon-not-legal: operation {op} (update) is past effect e0007 (database-migrate "
                      "database:partflow_staging:heads=r2); abandoning would leave a live effect started without its "
                      "recovery.", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)

    def test_rs29_the_lock_is_held_across_the_probe_and_the_action(self):
        self.crash(["update", "--latest"], "e0003", "after-intent")
        results = []

        def probe(*args, **kwargs):
            pid = os.fork()
            if pid == 0:
                try:
                    handle = pf_instance.acquire_instance_lock(self.context)
                except BaseException:  # noqa: BLE001 - the child reports only whether the lock was free
                    os._exit(1)
                handle.release()
                os._exit(0)
            _, status = os.waitpid(pid, 0)
            results.append(os.WEXITSTATUS(status))

        self.assertEqual(self.invoke(["resume"], confirm=mock.Mock(side_effect=probe)), 0, self.last_error)
        self.assertEqual(results, [1])
        self.assert_lock_free()

    def test_rs34_a_crashed_superseding_rollback_withdraws(self):
        op, _, _ = self.migrate_crash("after-intent")
        self.assertEqual(self.invoke(["resume"]), 1)
        before_update = next(item for item in self.c.snapshots() if item.reason == "before-update").bundle_id
        self.crash(["rollback", before_update, "--restore-db"], "e0003", "after-intent")
        rollback_op, plan, _ = tpa.latest_operation(self.c, "rollback")
        self.assertEqual(plan["supersedes"], op)
        self.assertEqual(self.c.operation_index().entry(op).cls, "superseded")
        for arguments in (["resume"], ["resume", "--abandon"]):
            with self.subTest(arguments=arguments):
                calls = len(self.c.calls)
                if arguments == ["resume", "--abandon"]:
                    self.crash(["rollback", before_update, "--restore-db"], "e0003", "after-intent")
                    calls = len(self.c.calls)
                self.assertEqual(self.invoke(arguments), 0, self.last_error)
                self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose"
                                  and call[1][0] in ("up", "start")])
                self.assertFalse(self.c.running["frontend"])
                self.assertFalse(self.c.running["backend"])
                self.assertEqual(self.blocking(), [("update", "needs_operator")])
                _, _, journal = tpa.latest_operation(self.c, "rollback")
                self.assertEqual(journal["phase"], "cancelled")
                self.assertIn(f"pf --instance staging rollback {before_update} --restore-db",
                              self.c.operation_index().blocking[0].journal["legal_next"])

    def assert_abandoned(self, kind, op, *, running=True):
        _, _, journal = tpa.latest_operation(self.c, kind)
        self.assertEqual((journal["phase"], journal["result"]["outcome"]), ("cancelled", "cancelled"))
        attempts = json.loads((self.context.operations_dir / op / "attempts.json").read_bytes())
        self.assertEqual(attempts[-1]["action"], "abandon")
        self.assertFalse([name for name in self.c.dbs if pf_config.CANDIDATE_RE.fullmatch(name)])
        self.assertEqual(self.c.running, {"db": True, "backend": running, "frontend": running})

    def test_rs38_abandon_before_a_database_effect_reopens_the_unchanged_deployment(self):
        # Audit F2: --abandon is the "same" as resume in the stop/capture rows (section 3.6), never a bare close that
        # leaves the writers stopped with no operation left to resume.
        self.crash(["update", "--latest"], "e0003", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "update")
        self.assertFalse(self.c.running["backend"])
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assertIn(f"Resuming operation {op} (update, phase preserving): abandon", self.output.getvalue())
        self.assert_abandoned("update", op)
        self.assertEqual(self.pointer()["sha"], OLD)
        self.assertEqual(self.blocking(), [])

    def test_rs39_reset_db_abandon_drops_the_owned_candidate_and_reopens(self):
        self.crash(["reset-db"], "e0003", "after-effect")
        op, plan, _ = tpa.latest_operation(self.c, "reset-db")
        candidate = plan["effects"][2]["target"].split(":")[1]
        self.assertIn(candidate, self.c.dbs)
        rows = copy.deepcopy(self.c.dbs["partflow_staging"])
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assert_abandoned("reset-db", op)
        self.assertEqual(self.c.dbs["partflow_staging"], rows)

    def test_rs40_rollback_abandon_drops_the_owned_candidate_and_reopens(self):
        rollback = rollback_setup(self)
        self.crash(rollback, "e0004", "after-effect")
        op, plan, _ = tpa.latest_operation(self.c, "rollback")
        self.assertIsNone(plan["supersedes"])
        self.assertTrue([name for name in self.c.dbs if name.startswith("pf_restore_")])
        rows = copy.deepcopy(self.c.dbs["partflow_staging"])
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assert_abandoned("rollback", op)
        self.assertEqual(self.c.dbs["partflow_staging"], rows)

    def test_rs41_a_superseding_rollback_abandon_drops_its_candidate_and_withdraws(self):
        op, _, _ = self.migrate_crash("after-intent")
        self.assertEqual(self.invoke(["resume"]), 1)
        before_update = next(item for item in self.c.snapshots() if item.reason == "before-update").bundle_id
        self.crash(["rollback", before_update, "--restore-db"], "e0004", "after-effect")
        rollback_op, plan, _ = tpa.latest_operation(self.c, "rollback")
        self.assertEqual(plan["supersedes"], op)
        self.assertTrue([name for name in self.c.dbs if name.startswith("pf_restore_")])
        calls = len(self.c.calls)
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and call[1][0] in ("up", "start")])
        self.assert_abandoned("rollback", rollback_op, running=False)
        self.assertEqual(self.blocking(), [("update", "needs_operator")])

    def test_rs42_a_moved_input_bundle_refuses_before_the_confirmation(self):
        # Audit F3: the strict input re-read a remaining forward step needs runs before the typed confirmation, so the
        # refusal leaves every operation file byte-identical and its "Nothing was changed." is true.
        rollback = rollback_setup(self)
        self.crash(rollback, "e0004", "after-effect")
        op, _, _ = tpa.latest_operation(self.c, "rollback")
        folder = self.c.backups_dir / self.selected
        dump = folder / "database.dump"
        original = dump.read_bytes()
        os.chmod(str(dump), 0o600)
        dump.write_bytes(original + b"tampered")
        before = pfx.operation_files(self.context, op)
        confirm = mock.Mock()
        self.assertEqual(self.invoke(["resume"], confirm=confirm), 1)
        self.assertIn(f"plan-input-changed: checkpoint {self.selected} of operation {op}", self.last_error)
        confirm.assert_not_called()
        self.assertEqual(pfx.operation_files(self.context, op), before)
        dump.write_bytes(original)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)

    def test_sg5_a_superseded_operations_staging_is_swept_once_its_superseder_completed(self):
        # Audit F7: a superseded operation stays superseded forever; its unsealed staging is kept only while the
        # recovery that supersedes it is open.
        op, plan, _ = self.migrate_crash("after-intent")
        self.assertEqual(self.invoke(["resume"]), 1)
        before_update = next(item for item in self.c.snapshots() if item.reason == "before-update").bundle_id
        self.assertEqual(self.invoke(["rollback", before_update, "--restore-db"]), 0, self.last_error)
        dep = plan["source"]["deployment_id"]
        staging = self.c.deployments_dir / (".staging-" + dep)
        self.assertTrue(staging.is_dir())
        self.assertEqual(self.c.operation_index().entry(op).cls, "superseded")
        self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
        self.c.new_migration = False
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        self.assertFalse(os.path.lexists(str(staging)))
        self.assertIn(f"note: staging-removed: {dep}", self.output.getvalue())


# ============================================================================ PR: durable effect protocol


class Protocol(Restartable):
    """PR-1..PR-5, PR-7..PR-10 through the real controller (FakeController); PR-6 and PR-11 below use real
    processes."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def test_pr1_the_intent_is_durable_before_the_child_starts(self):
        self.c.new_migration = True
        seen = []
        real = self.c.compose

        def compose(*args, **kwargs):
            if args[0] == "run" and "alembic" in args and kwargs.get("env") is None:
                _, plan, journal = tpa.latest_operation(self.c, "update")
                eid = journal["unresolved_effect"]
                seen.append((eid, pf_config.effect_state(journal, eid), journal["phase"]))
            return real(*args, **kwargs)

        with mock.patch.object(self.c, "compose", side_effect=compose):
            self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0, self.last_error)
        self.assertEqual(seen, [("e0007", "unknown", "migrating")])

    def test_pr2_pr3_an_exit_zero_without_its_postcondition_is_never_complete(self):
        self.c.new_migration = True
        real = self.c.compose

        def compose(*args, **kwargs):
            if args[0] == "run" and "alembic" in args and kwargs.get("env") is None:
                return ""  # exit 0, heads unchanged
            return real(*args, **kwargs)

        with mock.patch.object(self.c, "compose", side_effect=compose):
            self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        op, plan, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(pf_config.effect_state(journal, "e0007"), "unknown")
        self.assertEqual(journal["unresolved_effect"], "e0007")
        self.assertIsNotNone(journal["last_error"])
        self.assertTrue(journal["last_error"]["message"])
        self.assertEqual(journal["legal_next"], pf_config.legal_next(plan, journal, slug=SLUG))
        self.assertIn(f"pf --instance staging resume --operation {op}", journal["legal_next"])

    def test_pr4_a_private_only_failure_cancels_and_removes_the_staging(self):
        def stage(*args, **kwargs):
            raise OSError(28, "No space left on device")

        with mock.patch.object(pf.pf_source, "archive_verified_tree", side_effect=stage):
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertIn("deployment-stage-failed", self.last_error)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "cancelled")
        self.assertEqual(self.blocking(), [])
        folder = self.c.deployments_dir
        self.assertFalse([name for name in (os.listdir(str(folder)) if folder.exists() else [])
                          if name.startswith(".staging-")])
        self.assertTrue(all(self.c.running.values()))

    def test_pr5_an_after_intent_crash_leaves_the_effect_unknown(self):
        self.crash(["update", "--latest"], "e0002", "after-intent")
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual((pf_config.effect_state(journal, "e0002"), journal["unresolved_effect"]), ("unknown", "e0002"))
        self.assertIsNone(journal["last_error"])  # a dying process writes no failure generation

    def test_pr7_a_failing_children_record_never_stops_the_child(self):
        controller = self.c
        controller.operation_dir = Path(self.temp.name)
        controller.plan = {"effects": []}
        controller._current_effect = "e0001"
        spec = types.SimpleNamespace(tool="docker")
        process = types.SimpleNamespace(pid=os.getpid())
        with mock.patch.object(pf_instance, "rewrite_private_list", side_effect=OSError(28, "No space left")):
            controller._record_child(spec, process)
        self.assertIn("note: child-record-failed: No space left; a later resume probes the daemon and database only.",
                      self.output.getvalue())
        controller.operation_dir = controller.plan = controller._current_effect = None

    def test_pr8_no_secret_in_any_operation_file_or_log(self):
        env = self.c.config_dir / ".env"
        secret = "Zq7xSecret@Pw-9k"
        env.write_text(env.read_text().replace("POSTGRES_PASSWORD=abc123", "POSTGRES_PASSWORD=" + secret))
        env_hash = hashlib.sha256(env.read_bytes()).hexdigest()
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0, self.last_error)
        self.crash(["backup"], "e0002", "after-intent")
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        import urllib.parse
        forbidden = [secret.encode(), urllib.parse.quote(secret, safe="").encode(), env_hash.encode()]
        scanned = 0
        for current, _, files in os.walk(str(self.context.operations_dir)):
            for name in files:
                if name in ("app.env", "frozen-config.json") or name.startswith("compose-"):
                    continue  # the frozen snapshot itself (0400) and the approved renders hold values by design
                data = (Path(current) / name).read_bytes()
                scanned += 1
                for value in forbidden:
                    self.assertNotIn(value, data, (current, name))
        self.assertGreater(scanned, 6)
        logs = (self.output.getvalue() + self.error_output.getvalue()).encode()
        for value in forbidden:
            self.assertNotIn(value, logs)

    def test_pr9_a_caught_capture_failure_closes_the_backup_failed_preserved(self):
        self.c.fail = "dump"
        calls = len(self.c.calls)
        self.assertEqual(self.invoke(["backup"]), 1)
        _, plan, journal = tpa.latest_operation(self.c, "backup")
        self.assertEqual((journal["phase"], journal["result"]["outcome"]), ("failed_preserved", "failed_preserved"))
        bundle = plan["effects"][0]["preconditions"][0].split(":", 1)[1]
        self.assertIn({"kind": "bundle-attempt", "name": bundle, "sha256": None}, journal["retained_artifacts"])
        self.assertFalse([call for call in self.c.calls[calls:] if call[0] == "compose" and call[1][0] == "stop"])
        self.assertEqual(self.blocking(), [])
        self.assertTrue(all(self.c.running.values()))

    def test_pr10_a_caught_verification_failure_closes_the_backup_failed_preserved(self):
        self.c.fail = "restore"
        self.assertEqual(self.invoke(["backup"]), 1)
        _, plan, journal = tpa.latest_operation(self.c, "backup")
        self.assertEqual(journal["phase"], "failed_preserved")
        self.assertEqual(self.blocking(), [])
        view = self.c.snapshots()[0]
        self.assertIn(view.level, ("captured", "failed"))
        # A failed verification keeps its own candidate as evidence (reported, not cleaned: section 10 limit); it
        # is the plan's pre-assigned name and the failed record lists it as not removed.
        planned = [item.split(":", 1)[1] for item in plan["effects"][0]["preconditions"] if item.startswith("verify:")]
        record = self.records(view.bundle_id)[-1]
        self.assertEqual((record["result"], record["target"]["names"], record["target"]["removed"]),
                         ("failed", planned, False))
        self.assertEqual([name for name in self.c.dbs if name.startswith("pf_verify_")],
                         [name for name in planned if name in self.c.dbs])


@ROOT_FS
class RunnerInterrupt(unittest.TestCase):
    """PR-11: an interrupt inside spawn_callback terminates the child group and records it as interrupted."""

    def test_pr11_an_interrupt_in_the_spawn_callback(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            effects = base / "unresolved-effects.json"
            sleep = shutil.which("sleep")
            runner = pf_runner.ProcessRunner({"docker": sleep}, home=str(base), docker_config=str(base),
                                             docker_host="unix:///nonexistent", effects_path=effects)
            seen = []

            def callback(spec, process):
                seen.append(process.pid)
                (base / "children.json.tmp-x").write_text("[]")
                raise KeyboardInterrupt("Interrupted by signal 15")

            runner.spawn_callback = callback
            spec = pf_runner.ProcessSpec(tool="docker", argv=("30",), cwd=str(base), env=runner.environment(),
                                         timeout=60, effect={"kind": "test", "verb": "sleep"})
            with self.assertRaises(KeyboardInterrupt):
                runner.run(spec)
            deadline = time.monotonic() + 10
            while pf_runner.group_members(seen[0]) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertEqual(pf_runner.group_members(seen[0]), [])
            records = pf_runner.load_unresolved_effects(effects)
            self.assertEqual([(item["outcome"], item["pid"]) for item in records], [("interrupted", seen[0])])
            # Without an effect the callback is never called.
            seen.clear()
            plain = pf_runner.ProcessSpec(tool="docker", argv=("0",), cwd=str(base), env=runner.environment(),
                                          timeout=60)
            self.assertEqual(runner.run(plain).returncode, 0)
            self.assertEqual(seen, [])


# ============================================================================ WS: workspace generation switch


def status_of(test, *arguments):
    """`pf status` through the FakeController (read-only views simulated); returns the new output."""
    before = len(test.output.getvalue())
    real = test.c.compose

    def compose(*args, **kwargs):
        if args[:1] == ("ps",):
            return "NAME  STATUS"
        return real(*args, **kwargs)

    with mock.patch.object(test.c, "compose", side_effect=compose), \
            mock.patch.object(test.c, "describe_envelope", return_value="ok (fixture)"):
        test.invoke(["status", *arguments])
    return test.output.getvalue()[before:]


def identity(path):
    info = os.lstat(str(path))
    return info.st_dev, info.st_ino


@ROOT_FS
class Workspace(Restartable):
    """WS-1..WS-20 (A3-T10 filesystem part): preflight reasons, the W1-W4 switch, reconciliation and the interval."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []
        self.container = pf_instance.generation_container(self.context)

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def manifest(self):
        tree = Path(self.temp.name) / "candidate-tree"
        if not tree.exists():
            pfx.source_fixture(tree, NEW)
        return self.c.candidate_manifest(tree, NEW, verified=True)

    def generation(self, kind="update"):
        _, plan, journal = tpa.latest_operation(self.c, kind)
        return plan["workspace"]["generation_id"], plan, journal

    def test_ws1_preflight_is_available_on_a_plain_directory(self):
        self.assertEqual(self.c.workspace_preflight(self.manifest()), ("switch", None))

    def test_ws2_a_mount_point_workspace_is_pending(self):
        real = os.lstat
        workspace = str(self.root)

        def lstat(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            if str(path) == workspace:
                return types.SimpleNamespace(st_dev=info.st_dev + 1, st_ino=info.st_ino, st_mode=info.st_mode)
            return info

        with mock.patch.object(pf.os, "lstat", side_effect=lstat):
            mode, reason = self.c.workspace_preflight(self.manifest())
        self.assertEqual((mode, reason), ("pending", "workspace-is-mount-point: the workspace is a mount point, "
                                                     "subvolume or shared-folder root"))

    def test_ws3_an_unsafe_container(self):
        outside = Path(self.temp.name) / "elsewhere"
        outside.mkdir(mode=0o700)
        os.symlink(str(outside), str(self.container))
        self.assertEqual(self.c.workspace_preflight(self.manifest()),
                         ("pending", "generation-container-unsafe: the container is a symbolic link"))
        os.unlink(str(self.container))
        self.container.mkdir(mode=0o755)
        os.chmod(str(self.container), 0o755)
        self.assertEqual(self.c.workspace_preflight(self.manifest())[1],
                         "generation-container-unsafe: the container mode is 0755, not 0700")
        os.chmod(str(self.container), 0o700)
        os.chown(str(self.container), 1234, -1)
        self.assertEqual(self.c.workspace_preflight(self.manifest())[1],
                         "generation-container-unsafe: the container is owned by uid 1234")

    def test_ws4_a_container_colliding_with_a_registered_path(self):
        rows = [("other-instance", "backups", str(self.container / "x"))]
        with mock.patch.object(pf_instance, "inventory_of", return_value=rows):
            self.assertEqual(self.c.workspace_preflight(self.manifest()),
                             ("pending", "generation-container-collision: backups of other-instance"))

    def test_ws5_an_acl_on_the_workspace_root(self):
        acl = types.SimpleNamespace(state=types.SimpleNamespace(kind="posix"))
        with mock.patch.object(pf_instance, "inspect_acl_fd", return_value=acl):
            self.assertEqual(self.c.workspace_preflight(self.manifest()),
                             ("pending", "workspace-root-acl: the workspace root carries an ACL a new root inode "
                                         "cannot keep"))

    def test_ws6_ws19_capacity(self):
        manifest = self.manifest()
        staged = sum(entry.get("size", 0) for entry in manifest["entries"])
        minimum = int(self.c.config["minimum_free_mb"]) * 1024 * 1024
        for free, expected in ((staged - 1, "pending"), (staged + minimum - 1, "pending"),
                               (staged + minimum, "switch")):
            with self.subTest(free=free):
                with mock.patch.object(self.c, "artifact_free_bytes", return_value=free):
                    mode, reason = self.c.workspace_preflight(manifest)
                self.assertEqual(mode, expected)
                if expected == "pending":
                    self.assertTrue(reason.startswith("workspace-capacity: "), reason)
        self.container.mkdir(mode=0o700)
        (self.container / pfx.GENERATION).mkdir()
        (self.container / pfx.GENERATION / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        _, lines = self.c.workspace_decision(manifest, False)
        self.assertIn("Retained workspace generations: 1 (2 MiB, unsealed, never deleted); free space on the "
                      "workspace device: ", lines[1])

    def test_ws7_a_successful_switch(self):
        (self.root / "untracked-notes.txt").write_text("editor notes\n")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("[core]\n\tfsmonitor = /bin/false\n")
        before = ta.tree_hash(self.root)
        old = identity(self.root)
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        generation, plan, journal = self.generation()
        retained = self.container / generation
        self.assertEqual(identity(retained), old)
        self.assertEqual(ta.tree_hash(retained), before)  # byte-identical, modes included
        self.assertNotEqual(identity(self.root), old)
        self.assertFalse((self.root / "untracked-notes.txt").exists())
        self.assertFalse((self.root / ".git").exists())
        manifest = pf_source_manifest(self.root)
        self.assertEqual(pf.pf_source.entries_digest(manifest), plan["source"]["entries_sha256"])
        info = os.lstat(str(self.container))
        self.assertEqual((stat.S_IMODE(info.st_mode), info.st_uid), (0o700, 0))
        recorded = json.loads(self.context.source_manifest_path.read_bytes())
        self.assertEqual(recorded["source"], {"kind": "git_commit", "commit": NEW,
                                              **{key: value for key, value in recorded["source"].items()
                                                 if key not in ("kind", "commit")}})
        self.assertIn({"kind": "workspace-generation", "name": generation, "sha256": None},
                      journal["retained_artifacts"])
        self.assertEqual(self.c.workspace_status()["head"], NEW)

    def test_ws8_open_handles_land_in_the_retained_generation(self):
        (self.root / "sub").mkdir()
        (self.root / "a.txt").write_text("before\n")
        trigger = Path(self.temp.name) / "trigger"
        ready = Path(self.temp.name) / "ready"
        script = ("import os, sys, time\n"
                  "handle = open(sys.argv[1] + '/a.txt', 'a')\n"
                  "os.chdir(sys.argv[1] + '/sub')\n"
                  "open(sys.argv[3], 'w').close()\n"
                  "deadline = time.time() + 60\n"
                  "while not os.path.exists(sys.argv[2]) and time.time() < deadline:\n"
                  "    time.sleep(0.05)\n"
                  "handle.write('written after the switch\\n')\n"
                  "handle.close()\n"
                  "open('created-after.txt', 'w').write('x')\n")
        helper = subprocess.Popen([sys.executable, "-c", script, str(self.root), str(trigger), str(ready)])
        try:
            deadline = time.monotonic() + 30
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            old = identity(self.root)
            self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
            new = identity(self.root)
            trigger.write_text("go")
            self.assertEqual(helper.wait(timeout=60), 0)
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait()
        generation, _, _ = self.generation()
        retained = self.container / generation
        self.assertEqual((retained / "a.txt").read_text(), "before\nwritten after the switch\n")
        self.assertTrue((retained / "sub" / "created-after.txt").exists())
        self.assertFalse((self.root / "a.txt").exists())
        self.assertFalse((self.root / "sub").exists())
        evidence("OPEN-HANDLE-1", {"old_workspace_inode": old, "new_workspace_inode": new,
                                   "retained_generation_inode": identity(retained),
                                   "retained_a_txt": (retained / "a.txt").read_text(),
                                   "retained_sub_entries": sorted(os.listdir(str(retained / "sub"))),
                                   "new_workspace_has_a_txt": (self.root / "a.txt").exists()})

    def interval(self):
        """An update crashed between the two renames (W3 intent written, nothing renamed back)."""
        self.crash(["update", "--latest"], "workspace:retain:", "after-effect")
        generation, plan, journal = self.generation()
        self.assertFalse(os.path.lexists(str(self.root)))
        return generation, plan, journal

    def test_ws9_ws18_the_interval_is_shown_and_only_resume_binds(self):
        generation, plan, journal = self.interval()
        op = plan["operation_id"]
        out = status_of(self)
        self.assertIn(f"Operations: {op} update phase syncing-workspace", out)
        self.assertIn("  workspace: the registered workspace path may be absent between the two renames of the "
                      "workspace switch; only 'resume' (or 'resume --keep-workspace') may continue", out)
        before = pfx.operations_bytes(self.context)
        for arguments in (["backup", "--emergency"], ["rollback", "--restore-db"], ["backup"], ["update", "--latest"]):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.invoke(arguments), 1)
                self.assertIn("registered-path-missing", self.last_error)
        self.assertEqual(pfx.operations_bytes(self.context), before)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertIn(f"note: workspace-switch-in-progress {op}: the workspace path is absent between the two renames; "
                      "only resume may continue.", self.output.getvalue())
        _, _, journal = self.generation()
        self.assertEqual(journal["phase"], "completed")
        self.assertTrue(self.root.is_dir())

    def test_ws18_a_foreign_retained_generation_refuses_resume_too(self):
        generation, plan, journal = self.interval()
        retained = self.container / generation
        os.rename(str(retained), str(self.container / "moved-away"))
        (self.container / generation).mkdir(mode=0o700)
        before = pfx.operations_bytes(self.context)
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn("Protected context validation refused mutation", self.last_error)
        self.assertEqual(pfx.operations_bytes(self.context), before)

    def test_ws18_a_validation_finding_after_the_bind_stops_before_w4(self):
        generation, plan, journal = self.interval()
        finding = types.SimpleNamespace(severity="refuse", path=str(self.context.paths.workspace), code="owner-mismatch",
                                        message="workspace owned by uid 9")
        real = pf_instance.validate_context
        calls = []

        def validate_context(context, **kwargs):
            result = real(context, **kwargs)
            calls.append(result)
            if len(calls) >= 2 and os.path.lexists(str(self.root)):
                return types.SimpleNamespace(findings=[finding], mutation_allowed=False,
                                             blocking_messages=lambda: ["owner-mismatch"])
            return result

        with mock.patch.object(pf_instance, "validate_context", side_effect=validate_context):
            self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn(f"workspace-validation-failed: after the workspace switch of operation {plan['operation_id']} the "
                      "registered workspace does not validate (owner-mismatch: workspace owned by uid 9); the source "
                      "manifest was not rewritten.", self.last_error)
        _, plan, journal = self.generation()
        ids = ws_ids(plan)
        self.assertEqual(pf_config.effect_state(journal, ids[2]), "complete")
        self.assertEqual(pf_config.effect_state(journal, ids[3]), "not_started")

    def test_ws10_keep_workspace_rebinds_the_old_tree(self):
        old = identity(self.root)
        generation, plan, journal = self.interval()
        self.assertEqual(self.invoke(["resume", "--keep-workspace"]), 0, self.last_error)
        self.assertEqual(identity(self.root), old)
        self.assertFalse(os.path.lexists(str(self.container / ("stage-" + generation))))
        self.assertFalse(os.path.lexists(str(self.container / generation)))
        _, plan, journal = self.generation()
        self.assertEqual(journal["phase"], "completed")
        ids = ws_ids(plan)
        self.assertEqual({item["evidence"] for item in journal["effects"] if item["effect_id"] in ids},
                         {"kept by operator (old tree rebound)"})
        attempts = json.loads((self.context.operations_dir / plan["operation_id"] / "attempts.json").read_bytes())
        self.assertEqual(attempts[-1]["action"], "keep-workspace")

    def test_ws11_a_crash_inside_w1_restages(self):
        real = self.c.publish_source_tree

        def partial(tree, manifest, target):
            (Path(target) / "partial.txt").write_text("half")
            raise pf.SimulatedCrash("inside W1")

        with mock.patch.object(self.c, "publish_source_tree", side_effect=partial):
            with self.assertRaises(pf.SimulatedCrash):
                self.invoke(["update", "--latest"])
        self.restart()
        generation, plan, _ = self.generation()
        self.assertTrue((self.container / ("stage-" + generation) / "partial.txt").exists())
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertFalse((self.root / "partial.txt").exists())
        self.assertIsNotNone(real)

    def test_ws12_ws13_an_unknown_directory_at_the_workspace_path(self):
        generation, plan, journal = self.interval()
        retained_hash = ta.tree_hash(self.container / generation)
        stage_hash = ta.tree_hash(self.container / ("stage-" + generation))
        preflight = self.context.operations_dir / plan["operation_id"] / "inventory-preflight.json"
        for content in (["planted.txt"], []):
            with self.subTest(content=content):
                self.root.mkdir()
                for name in content:
                    (self.root / name).write_text("x")
                # Audit F3: the refusing observation stops the resume before its confirmation; no operation file (and
                # not the operation's own inventory preflight record) changes.
                before = pfx.operations_bytes(self.context)
                recorded = preflight.read_bytes()
                confirm = mock.Mock()
                self.assertEqual(self.invoke(["resume"], confirm=confirm), 1)
                confirm.assert_not_called()
                self.assertEqual(pfx.operations_bytes(self.context), before)
                self.assertEqual(preflight.read_bytes(), recorded)
                self.assertIn(f"workspace-generation-mismatch: the workspace switch of operation {plan['operation_id']} "
                              "found the workspace is an unknown inode", self.last_error)
                _, _, journal = self.generation()
                self.assertEqual(journal["phase"], "syncing-workspace")
                self.assertEqual(ta.tree_hash(self.container / generation), retained_hash)
                self.assertEqual(ta.tree_hash(self.container / ("stage-" + generation)), stage_hash)
                shutil.rmtree(str(self.root))
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)

    def no_workspace_space(self):
        """Free space below the switch's need on the workspace device only (the artifact store keeps its space)."""
        real = pf.Controller.artifact_free_bytes
        parent = self.root.parent

        def free(path):
            return 0 if Path(path) == parent else real(path)

        return mock.patch.object(tpa.FakeController, "artifact_free_bytes", staticmethod(free))

    def test_ws14_pending_mode(self):
        with self.no_workspace_space():
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertIn("workspace-sync-pending: deployment ", self.last_error)
        generation, plan, journal = self.generation()
        self.assertEqual((plan["workspace"]["mode"], journal["phase"]), ("pending", "workspace_sync_pending"))
        self.assertTrue(all(self.c.running.values()))
        self.assertEqual(self.pointer()["sha"], NEW)
        out = status_of(self)
        self.assertIn(f"Operations: {plan['operation_id']} update phase workspace_sync_pending", out)
        self.assertIn("pf --instance staging backup: capture a manual backup while the activated deployment waits for "
                      "its workspace refresh", out)
        old = identity(self.root)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertNotEqual(identity(self.root), old)
        self.assertEqual(self.c.workspace_status()["head"], NEW)

    def test_ws14_pending_mode_keep_workspace(self):
        with self.no_workspace_space():
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        old = identity(self.root)
        self.assertEqual(self.invoke(["resume", "--keep-workspace"]), 0, self.last_error)
        self.assertEqual(identity(self.root), old)
        _, _, journal = self.generation()
        self.assertEqual(journal["phase"], "completed")

    def test_ws15_retained_generations_are_counted_and_never_a_backup(self):
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        generations = sorted(name for name in os.listdir(str(self.container)) if name.startswith("wsg-"))
        self.assertEqual(len(generations), 2)
        out = status_of(self)
        self.assertIn(f"Workspace generations: 2 unsealed in {self.container} (latest {generations[-1]} from operation ",
                      out)
        self.assertEqual(self.invoke(["backups"]), 0)
        self.assertNotIn("wsg-", self.output.getvalue().split("Workspace generations")[-1].split("\n", 1)[-1])
        checkpoint = self.checkpoint()
        self.assertFalse([item for item in checkpoint.manifest["payloads"] if "wsg-" in item["path"]])

    def test_ws16_no_git_child_runs_inside_the_container(self):
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        generation, _, _ = self.generation()
        hooks = self.container / generation / ".git" / "hooks"
        hooks.mkdir(parents=True)
        marker = Path(self.temp.name) / "hook-ran"
        (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch {marker}\n")
        os.chmod(str(hooks / "post-checkout"), 0o755)
        (self.container / generation / ".git" / "config").write_text(f"[core]\n\tfsmonitor = touch {marker}\n")
        specs = []
        real = pf_runner.ProcessRunner.run

        def run(runner, spec):
            specs.append(spec)
            return real(runner, spec)

        with mock.patch.object(pf_runner.ProcessRunner, "run", run):
            status_of(self)
            self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
            self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        for spec in specs:
            self.assertFalse(str(spec.cwd).startswith(str(self.container)), spec)
            self.assertFalse([item for item in spec.argv if str(self.container) in str(item)], spec)
        self.assertFalse(marker.exists())

    def test_ws17_after_the_bind_keep_workspace_is_not_legal(self):
        self.crash(["update", "--latest"], "source-manifest", "after-intent")
        op, plan, journal = tpa.latest_operation(self.c, "update")
        before = pfx.operation_files(self.context, op)
        self.assertEqual(self.invoke(["resume", "--keep-workspace"]), 1)
        self.assertIn(f"keep-workspace-not-legal: operation {op} is in syncing-workspace; --keep-workspace applies only "
                      "to the workspace refresh. Nothing was changed.", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)

    def test_ws17b_keep_workspace_after_an_unjournaled_bind_is_refused_before_the_confirmation(self):
        # Audit F3: the section 3.7 "bind done" row (W3 unknown, observed bound) refuses --keep-workspace before the
        # typed confirmation and the attempt entry.
        self.crash(["update", "--latest"], "workspace:bind:", "after-effect")
        op, plan, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(pf_config.effect_state(journal, ws_ids(plan)[2]), "unknown")
        before = pfx.operation_files(self.context, op)
        confirm = mock.Mock()
        self.assertEqual(self.invoke(["resume", "--keep-workspace"], confirm=confirm), 1)
        self.assertIn(f"keep-workspace-not-legal: operation {op} is in syncing-workspace; --keep-workspace applies only "
                      "to the workspace refresh. Nothing was changed.", self.last_error)
        confirm.assert_not_called()
        self.assertEqual(pfx.operation_files(self.context, op), before)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)

    def paired_backup(self):
        """An update waiting in workspace_sync_pending and a backup interrupted inside its capture (the pair)."""
        with self.no_workspace_space():
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        update_op, _, _ = tpa.latest_operation(self.c, "update")

        def crash(*args, **kwargs):
            raise pf.SimulatedCrash("inside the capture")

        with mock.patch.object(self.c, "write_dump_list", side_effect=crash):
            with self.assertRaises(pf.SimulatedCrash):
                self.invoke(["backup"])
        self.restart()
        backup_op, _, _ = tpa.latest_operation(self.c, "backup")
        index = self.c.operation_index()
        self.assertIsNotNone(index.pair)
        self.assertFalse(index.conflict)
        return update_op, backup_op

    def test_ws21_a_pending_switch_waits_while_its_paired_backup_is_open(self):
        # Audit F1: resuming the switch while the backup is open would start its W effects, end the pair and leave
        # two blocking operations no route can resume (a failing or crashing W1/W2 left operation-conflict, or an
        # absent workspace). The switch therefore waits; every refusal names the backup's routes.
        update_op, backup_op = self.paired_backup()
        backup_resume = f"pf --instance staging resume --operation {backup_op}"
        before = pfx.operations_bytes(self.context)
        for arguments in (["resume", "--operation", update_op], ["resume", "--operation", update_op, "--keep-workspace"],
                          ["resume"], ["backup"]):
            with self.subTest(arguments=arguments):
                confirm = mock.Mock()
                self.assertEqual(self.invoke(arguments, confirm=confirm), 1)
                self.assertIn(backup_resume, self.last_error)
                confirm.assert_not_called()
        self.assertEqual(pfx.operations_bytes(self.context), before)
        out = status_of(self)
        self.assertIn(f"  waits: backup operation {backup_op} is open; run '{backup_resume}' (or add --abandon) first, "
                      "then this operation's routes apply", out)
        self.assertEqual(self.invoke(["resume", "--operation", backup_op]), 0, self.last_error)
        self.assertEqual(self.blocking(), [("update", "workspace_sync_pending")])
        # The switch's own routes apply again: a failing W1 leaves one resumable operation.
        with mock.patch.object(self.c, "publish_source_tree", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(self.invoke(["resume", "--operation", update_op]), 1)
        self.assertEqual(self.blocking(), [("update", "syncing-workspace")])
        self.assertEqual(self.invoke(["resume", "--operation", update_op]), 0, self.last_error)
        _, _, journal = self.generation()
        self.assertEqual(journal["phase"], "completed")
        self.assertEqual(self.blocking(), [])

    def test_ws21b_an_abandoned_paired_backup_releases_the_switch(self):
        update_op, backup_op = self.paired_backup()
        self.assertEqual(self.invoke(["resume", "--operation", backup_op, "--abandon"]), 0, self.last_error)
        self.assertEqual(self.invoke(["resume", "--operation", update_op, "--keep-workspace"]), 0, self.last_error)
        self.assertEqual(self.blocking(), [])

    def test_ws22_keep_workspace_over_a_foreign_directory_records_the_retained_generation(self):
        # Audit F9: W2 renamed the old tree into the container but its completion was not journaled; an administrator
        # recreated the workspace directory. --keep-workspace keeps it and links the retained generation to the journal.
        generation, plan, journal = self.interval()
        self.assertEqual(pf_config.effect_state(journal, ws_ids(plan)[1]), "unknown")
        self.assertFalse([item for item in journal["retained_artifacts"] if item["kind"] == "workspace-generation"])
        self.root.mkdir()
        self.assertEqual(self.invoke(["resume", "--keep-workspace"]), 0, self.last_error)
        _, _, journal = self.generation()
        self.assertEqual(journal["phase"], "completed")
        self.assertIn({"kind": "workspace-generation", "name": generation, "sha256": None},
                      journal["retained_artifacts"])
        self.assertTrue((self.container / generation).is_dir())
        self.assertNotIn(f"latest {generation} unreferenced", status_of(self))

    def test_ws23_a_failed_seal_is_never_redone_when_the_pending_switch_resumes(self):
        # Audit F6: section 3.4 keeps a seal failure after a healthy activation terminal for the operation; a later
        # resume of its workspace switch closes failed_preserved, so the journal and deployed.json agree.
        with self.no_workspace_space(), \
                mock.patch.object(self.c, "seal_deployment", side_effect=pf.Failure("simulated seal failure")):
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        op, plan, journal = tpa.latest_operation(self.c, "update")
        seal = next(item["effect_id"] for item in plan["effects"] if item["type"] == "artifact-seal")
        self.assertEqual((journal["phase"], pf_config.effect_state(journal, seal)), ("workspace_sync_pending", "partial"))
        self.assertEqual(self.pointer().get("deployment_seal_failed"), op)
        sealing = mock.Mock(side_effect=AssertionError("a failed seal is never redone"))
        with mock.patch.object(self.c, "seal_deployment", sealing):
            self.assertEqual(self.invoke(["resume"]), 1)
        sealing.assert_not_called()
        self.assertIn("deployment-record-incomplete: the application was activated and passed health checks",
                      self.last_error)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "failed_preserved")
        self.assertEqual(journal["result"], {"outcome": "failed_preserved", "deployment_id": None})
        self.assertEqual(pf_config.effect_state(journal, seal), "partial")
        self.assertTrue(all(pf_config.effect_state(journal, eid) == "complete" for eid in ws_ids(plan)))
        self.assertEqual(self.pointer().get("deployment_seal_failed"), op)
        self.assertNotIn("deployment_id", self.pointer())
        self.assertEqual(self.blocking(), [])

    def test_ws20_a_superseded_stage_is_reported_and_left(self):
        self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        stale = self.container / "stage-wsg-20261001T000000Z-0badf00d"
        stale.mkdir(mode=0o700)
        (stale / "x.txt").write_text("left")
        out = status_of(self)
        self.assertIn("  stages: stage-wsg-20261001T000000Z-0badf00d superseded-stage (unreferenced)", out)
        self.assertTrue((stale / "x.txt").exists())


def pf_source_manifest(root):
    return pf.pf_source.build_manifest(root, source={"kind": "unknown"}, excludes=pf.SOURCE_EXCLUDES)


# ============================================================================ CF: frozen configuration (A3-T09)


class ConfigConcurrency(Restartable):
    """CF-1..CF-3, CF-5, CF-6, CF-8 through the real controller (FakeController)."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []
        self.env_path = self.c.config_dir / ".env"
        self.admin_path = self.c.config_dir / "pf-config.json"

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def edit_env(self):
        data = self.env_path.read_bytes().replace(b"POSTGRES_PASSWORD=abc123", b"POSTGRES_PASSWORD=edited999")
        self.env_path.write_bytes(data)
        return data

    def test_cf1_an_edit_after_the_freeze_never_reaches_a_child(self):
        seen = []
        real = self.c.compose
        edited = []

        def compose(*args, **kwargs):
            if args[0] == "stop" and not edited:
                edited.append(self.edit_env())
            seen.append(self.c.env()["POSTGRES_PASSWORD"])
            return real(*args, **kwargs)

        with mock.patch.object(self.c, "compose", side_effect=compose):
            self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        self.assertEqual(set(seen), {"abc123"})
        self.assertEqual(self.env_path.read_bytes(), edited[0])

    def test_cf2_cf3_a_resume_uses_the_plan_snapshot_and_the_frozen_admin_configuration(self):
        self.crash(["update", "--latest"], "e0004", "after-intent")
        op, plan, _ = tpa.latest_operation(self.c, "update")
        edited = self.edit_env()
        admin = json.loads(self.admin_path.read_text())
        frozen_timeout = admin.get("health_timeout_seconds")
        admin["health_timeout_seconds"] = 7
        self.admin_path.write_text(json.dumps(admin) + "\n")
        admin_bytes = self.admin_path.read_bytes()
        seen = []
        real = tpa.FakeController.compose

        def compose(controller, *args, **kwargs):
            seen.append((controller.env()["POSTGRES_PASSWORD"], controller.config["health_timeout_seconds"]))
            return real(controller, *args, **kwargs)

        with mock.patch.object(tpa.FakeController, "compose", compose):
            self.restart()
            self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertTrue(seen)
        self.assertEqual({item[0] for item in seen}, {"abc123"})
        self.assertEqual(len({item[1] for item in seen}), 1)
        self.assertNotIn(7, {item[1] for item in seen})
        if frozen_timeout is not None:
            self.assertEqual({item[1] for item in seen}, {frozen_timeout})
        out = self.output.getvalue()
        self.assertIn(f"note: config-proposal-differs: config/.env differs from the configuration operation {op}", out)
        self.assertIn(f"note: admin-config-proposal-differs: config/pf-config.json differs from (or is missing against) "
                      f"the copy operation {op} froze; it stays a proposal.", out)
        self.assertEqual(self.env_path.read_bytes(), edited)
        self.assertEqual(self.admin_path.read_bytes(), admin_bytes)

    def admin_change_resumes(self, change):
        self.crash(["update", "--latest"], "e0004", "after-intent")
        change()
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "completed")

    def test_cf4_an_absent_admin_file_never_blocks_resume(self):
        self.admin_change_resumes(self.admin_path.unlink)

    def test_cf4_an_invalid_admin_file_never_blocks_resume(self):
        self.admin_change_resumes(lambda: self.admin_path.write_text("{not json\n"))

    def test_cf5_a_tampered_snapshot_refuses(self):
        self.crash(["update", "--latest"], "e0004", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "update")
        snapshot = self.context.operations_dir / op / "app.env"
        os.chmod(str(snapshot), 0o600)
        snapshot.write_bytes(snapshot.read_bytes() + b"# tampered\n")
        before = pfx.operation_files(self.context, op)
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn(f"plan-input-changed: frozen application configuration of operation {op} no longer matches its "
                      "plan", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)
        admin = self.context.operations_dir / op / "admin-config.json"
        os.chmod(str(admin), 0o600)
        admin.write_bytes(admin.read_bytes() + b" ")
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn("plan-input-changed", self.last_error)

    def test_cf8_a_file_written_during_a_capture_survives_in_the_retained_generation(self):
        real = self.c.snapshot

        def snapshot(*args, **kwargs):
            (self.root / "written-during-capture.txt").write_text("keep me")
            return real(*args, **kwargs)

        with mock.patch.object(self.c, "snapshot", side_effect=snapshot):
            self.assertEqual(self.invoke(["update", "--latest"]), 0, self.last_error)
        _, plan, _ = tpa.latest_operation(self.c, "update")
        retained = pf_instance.generation_container(self.context) / plan["workspace"]["generation_id"]
        self.assertEqual((retained / "written-during-capture.txt").read_text(), "keep me")
        dep = plan["source"]["deployment_id"]
        record = json.loads((self.c.deployments_dir / dep / "deployment-record.json").read_bytes())
        self.assertEqual(record["source"]["manifest"]["entries_sha256"], plan["source"]["entries_sha256"])


# ============================================================================ FC / SG: fail-closed and staging


class FailClosed(Restartable):
    """FC-1..FC-4 (section 3.9) and SG-1, SG-2, SG-4 (section 3.13)."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def stops_after(self, start):
        return [call for call in self.c.calls[start:] if call[0] == "compose" and call[1][0] == "stop"]

    def test_fc1_a_failure_before_activation_completes_stops_the_application(self):
        self.c.fail = "health"
        start = len(self.c.calls)
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.stops_after(start)[-1][1], ("stop", "frontend", "backend"))
        self.assertFalse(self.c.running["frontend"] or self.c.running["backend"])

    def test_fc2_a_failure_after_activation_never_stops(self):
        for where in ("seal", "workspace"):
            with self.subTest(where=where):
                self.tearDown()
                self.setUp()
                start = len(self.c.calls)
                target = "seal_deployment" if where == "seal" else "publish_source_tree"
                with mock.patch.object(self.c, target, side_effect=OSError(5, "simulated I/O error")):
                    self.invoke(["update", "--latest"])
                after = [call for call in self.stops_after(start) if "frontend" in call[1]]
                # The only stop is the update's own pre-capture stop (service:stop), before activation.
                self.assertEqual(len(after), 1, after)
                self.assertTrue(self.c.running["frontend"] and self.c.running["backend"])

    def test_fc3_a_backup_failure_never_stops(self):
        self.c.fail = "dump"
        start = len(self.c.calls)
        self.assertEqual(self.invoke(["backup"]), 1)
        self.assertEqual(self.stops_after(start), [])

    def test_fc4_no_blocking_operation_no_stop(self):
        start = len(self.c.calls)
        with mock.patch.object(self.c, "resolve", side_effect=pf.Failure("release lookup failed")):
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.stops_after(start), [])
        self.assertTrue(all(self.c.running.values()))

    def staging_names(self):
        folder = self.c.deployments_dir
        return sorted(name for name in os.listdir(str(folder)) if name.startswith(".staging-")) \
            if folder.exists() else []

    def test_sg1_sg2_own_staging_is_removed_and_the_sweep_keeps_a_blocking_reference(self):
        self.crash(["update", "--latest"], "e0002", "after-intent")
        op, plan, _ = tpa.latest_operation(self.c, "update")
        own = ".staging-" + plan["source"]["deployment_id"]
        self.assertEqual(self.staging_names(), [own])
        stray = self.c.deployments_dir / ".staging-dep-20261001T000000Z-0000dead"
        stray.mkdir(mode=0o700)
        self.c.sweep_staging()
        self.assertEqual(self.staging_names(), [own])  # referenced by the blocking plan; the stray is swept
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        _, _, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "cancelled")
        self.assertEqual(self.staging_names(), [])

    def test_sg3_a_staging_symlink_is_reported_not_removed(self):
        self.c.deployments_dir.mkdir(parents=True, exist_ok=True)
        target = Path(self.temp.name) / "elsewhere"
        target.mkdir()
        link = self.c.deployments_dir / ".staging-dep-20261001T000000Z-0000beef"
        os.symlink(str(target), str(link))
        self.c.sweep_staging()
        self.assertTrue(os.path.islink(str(link)))
        self.assertTrue(target.is_dir())


# ============================================================================ PU: purge survival


import test_docker_scope as docker_scope  # noqa: E402


@ROOT_FS
class PurgeSurvival(docker_scope.PurgeHarness):
    """PU-1, PU-2, PU-4, PU-5 and CF-4b through the real purge, the fake daemon and the harness plane."""

    def lock_identity(self):
        info = os.lstat(str(self.context.lock_path))
        return info.st_dev, info.st_ino

    def crash_at(self, target, point="after-effect"):
        real = pf.Controller._crash

        def seam(controller, effect_id, when):
            if when == point and controller.plan_effect(effect_id)["target"] == target:
                raise pf.SimulatedCrash(f"{when} {effect_id}")
            return real(controller, effect_id, when)

        return mock.patch.object(pf.Controller, "_crash", seam)

    def test_pu1_purge_survives_its_own_cleanup(self):
        self.state(docker_scope.topology(self.context))
        lock = self.lock_identity()
        code, out, err, confirmations = self.purge()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.lock_identity(), lock)
        op, plan, journal = self.operation()
        self.assertEqual(journal["phase"], "completed")
        self.assertIn("purge-bundle", {item["kind"] for item in journal["retained_artifacts"]})
        self.assertTrue((self.context.operations_dir / op / "journal.json").is_file())
        self.assertFalse((self.context.paths.configuration / ".env").exists())
        state = self.fake.state()
        state["info_exit"] = 1
        self.fake.write_state(state)
        code, out, err, _ = self.main("status")
        self.assertIn(f"Recent operations: {op} purge completed", out)
        self.assertIn("Operations: none open", out)
        self.assertLess(out.index("Operations: none open"), out.index("unavailable"))

    def test_pr6_children_are_recorded_for_effect_children_only(self):
        self.state(docker_scope.topology(self.context))
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, err)
        op, plan, journal = self.operation()
        children = json.loads((self.context.operations_dir / op / "children.json").read_bytes())
        self.assertTrue(children)
        ids = {item["effect_id"] for item in plan["effects"]}
        for child in children:
            self.assertEqual(set(child), {"effect_id", "tool", "pgid", "boot_id", "start_ticks", "recorded_at"})
            self.assertIn(child["effect_id"], ids)
            self.assertIsInstance(child["pgid"], int)
            self.assertEqual(child["boot_id"], pf_instance.boot_id())
            self.assertTrue(child["start_ticks"] is None or isinstance(child["start_ticks"], int))
        removals = [argv for argv in self.fake.argvs() if argv[:2] in (["rm", "-f"], ["volume", "rm"],
                                                                      ["network", "rm"])]
        probes = [argv for argv in self.fake.argvs() if argv[:1] in (["info"], ["inspect"], ["ps"])]
        self.assertGreaterEqual(len(children), len(removals))
        self.assertLess(len(children), len(removals) + len(probes) + 50)

    def test_pu2_a_crash_after_the_state_cleanup_finishes_by_resume(self):
        self.state(docker_scope.topology(self.context))
        with self.crash_at("purge-cleanup:state"), self.assertRaises(pf.SimulatedCrash):
            self.purge()
        op, plan, journal = self.operation()
        self.assertEqual(journal["phase"], "finalizing")
        self.assertFalse(self.context.state_dir.exists())
        code, out, err, _ = self.main("status")
        self.assertIn(f"Operations: {op} purge phase finalizing", out)
        with self.plane():
            code, out, err, confirmations = self.main("resume")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.journal()["phase"], "completed")
        self.assertTrue(confirmations[0].startswith("RESUME PURGE partflow "))

    def test_pu4_deletion_progress_never_adds_an_item(self):
        journal = docker_scope_interrupted(self)
        plan = json.loads(self.plan_file().read_bytes())
        planned = {json.dumps(item, sort_keys=True) for item in plan["candidates"]}
        state = self.fake.state()
        extra = pfx.container("c" * 64, PROJECT + "-backend-run-feedface0001",
                              pfx.compose_run_labels(self.context, "backend"), status="exited")
        state["containers"].append(extra)
        self.fake.write_state(state)
        with self.plane():
            code, out, err, _ = self.main("resume")
        self.assertEqual(code, 0, err)
        keys = {(item["kind"], item["key"]) for item in plan["candidates"]}
        progress = self.progress()
        self.assertTrue(progress)
        self.assertLessEqual({(item["kind"], item["key"]) for item in progress}, keys)
        self.assertTrue(planned)
        self.assertIn(extra["id"], [item["id"] for item in self.fake.state()["containers"]])
        self.assertIsNotNone(journal)

    def test_pu5_a_declined_erase_reopens_in_process(self):
        self.state(docker_scope.topology(self.context))

        def decline(phrase):
            if phrase.startswith("ERASE "):
                raise pf.Failure("Confirmation did not match. Nothing was changed.")

        code, out, err, confirmations = self.purge(confirm=decline)
        self.assertEqual(code, 1)
        _, plan, journal = self.operation()
        self.assertEqual(journal["phase"], "cancelled")
        self.assertIsNone(journal["deletion"])
        self.assertEqual(tpa_open(self.context), [])
        self.assertTrue(self.activated)
        self.assertFalse(self.paused)

    def test_cf4b_a_reset_admin_config_purge_resumes_from_its_frozen_copy(self):
        self.state(docker_scope.topology(self.context))
        with self.crash_at("purge-cleanup:admin-config", "after-effect"), self.assertRaises(pf.SimulatedCrash):
            self.purge("--reset-admin-config")
        self.assertFalse((self.context.paths.configuration / "pf-config.json").exists())
        with self.plane():
            code, out, err, confirmations = self.main("purge", "--keep-backups")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.journal()["phase"], "completed")


def docker_scope_interrupted(test):
    """An interrupted purge in deleting (SIGTERM after the first per-resource removal), as CI-6 builds it."""
    test.state(docker_scope.topology(test.context))
    original = pf.pf_runner.ProcessRunner.run
    seen = []

    def interrupt_after_first_rm(runner, spec):
        result = original(runner, spec)
        if list(spec.argv[:2]) == ["rm", "-f"] and not seen:
            seen.append(spec.argv)
            raise KeyboardInterrupt("Interrupted by signal 15")
        return result

    with mock.patch.object(pf.pf_runner.ProcessRunner, "run", interrupt_after_first_rm):
        code, out, err, _ = test.purge()
    test.assertEqual(code, 1, err)
    return test.journal()


def tpa_open(context):
    return pfx.open_operations(context)


# ============================================================================ IN: installer gate


import test_install as tin  # noqa: E402


@ROOT_FS
class Installer(tin.InstallBase):
    """IN-1..IN-6 (sections 3.11, 3.11a): the installer gate over lifecycle journals and runner records."""

    def report(self):
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.contexts["a"]])
        return [(item.code, Path(item.subject).name) for item in report.conflicts]

    def test_in1_in2_in3_a_blocking_or_invalid_operation_refuses(self):
        context = self.contexts["a"]
        plan = pfx.migrating_update(context)
        self.assertEqual(self.report(), [("instance-operation-pending", plan["operation_id"])])
        code, out, err = self.run_pf(["install", "control", "--source", str(self.candidate())])
        self.assertEqual(code, 1, out + err)
        self.assertIn("instance-operation-pending", tin.conflict_codes(err))
        self.assertIn(plan["operation_id"], err)
        pfx.clear_operations(context)
        done_plan = pfx.lifecycle_plan(context, "update")
        pfx.write_operation(context, done_plan, pfx.lifecycle_journal(
            done_plan, phase="completed", states={item["effect_id"]: "complete" for item in done_plan["effects"]}))
        self.assertEqual(self.report(), [])
        directory = context.operations_dir / done_plan["operation_id"]
        os.chmod(str(directory / "journal.json"), 0o600)
        (directory / "journal.json").write_text("{}")
        self.assertEqual(self.report(), [("instance-operation-pending", done_plan["operation_id"])])

    def runner_record(self, directory):
        (directory / "unresolved-effects.json").write_text(json.dumps([{"id": "x", "outcome": "interrupted"}]))

    def test_in4_in5_runner_records_follow_their_journal(self):
        context = self.contexts["a"]
        closed = pfx.lifecycle_plan(context, "update")
        directory = pfx.write_operation(context, closed, pfx.lifecycle_journal(
            closed, phase="completed", states={item["effect_id"]: "complete" for item in closed["effects"]}))
        self.runner_record(directory)
        self.assertEqual(self.report(), [])  # reconciled by the closed journal
        journal_less = context.operations_dir / pfx.operation_id("backup", "0000e0e0", 50)
        journal_less.mkdir(mode=0o700)
        self.runner_record(journal_less)
        self.assertEqual(self.report(), [("instance-effects-unresolved", "unresolved-effects.json")])
        shutil.rmtree(str(journal_less))
        (directory / "unresolved-effects.json").write_text("{broken")
        self.assertEqual(self.report(), [("instance-effects-unresolved", "unresolved-effects.json")])

    def test_in6_a_superseded_operation_is_reconciled_only_after_its_successor_completed(self):
        context = self.contexts["a"]
        update = pfx.migrating_update(context)
        self.runner_record(context.operations_dir / update["operation_id"])
        rollback = pfx.lifecycle_plan(context, "rollback", op=pfx.operation_id("rollback", "0000c0c0", 60),
                                      supersedes=update["operation_id"])
        directory = pfx.write_operation(context, rollback, pfx.lifecycle_journal(
            rollback, phase="cancelled", states={"e0001": "complete"}))
        self.assertIn(("instance-operation-pending", update["operation_id"]), self.report())
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(pfx.lifecycle_journal(
            rollback, phase="completed", sequence=2,
            states={item["effect_id"]: "complete" for item in rollback["effects"]})))
        self.assertEqual(self.report(), [])


# ============================================================================ probes and diagnostics (fake daemon)


import test_entry_routes as ter  # noqa: E402


@ROOT_FS
class ProcessProbes(ter.Base):
    """RS-24, RS-25 (still-running probe), PR-6 (children.json), DG-1..DG-8 (diagnostics) with the fake daemon."""

    def interrupted(self):
        return pfx.frozen_operation(self.context, "deploy", phase="initializing", unresolved="e0003",
                                    states={"e0001": "complete", "e0002": "complete", "e0003": "unknown"})

    def record_child(self, plan, pid, ticks):
        directory = self.context.operations_dir / plan["operation_id"]
        pf_instance.rewrite_private_list(directory / "children.json", [{
            "effect_id": "e0003", "tool": "docker", "pgid": pid, "boot_id": pf_instance.boot_id(),
            "start_ticks": ticks, "recorded_at": "20261007T040100Z"}])

    def resume(self):
        declined = pf.Failure("Confirmation did not match. Nothing was changed.")
        with mock.patch.object(pf, "confirm", side_effect=declined) as confirm:
            result = self.run_main(["--instance", "staging", "resume"], interactive=True)
        self.confirmed = confirm.called
        return result

    def test_rs24_a_recorded_child_group_still_running(self):
        plan = self.interrupted()
        child = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            self.record_child(plan, child.pid, pf_instance.process_start_ticks(child.pid))
            before = pfx.operation_files(self.context, plan["operation_id"])
            code, out, err = self.resume()
            self.assertEqual(code, 1)
            self.assertFalse(self.confirmed)
            self.assertIn(f"effect-still-running: effect e0003 (database-migrate database:partflow_staging:heads=r1) "
                          f"of operation {plan['operation_id']} still has a running process group {child.pid} (docker)",
                          err)
            self.assertEqual(pfx.operation_files(self.context, plan["operation_id"]), before)
            # A reused PID (other start ticks) is not this operation's child.
            self.record_child(plan, child.pid, (pf_instance.process_start_ticks(child.pid) or 0) + 7)
            code, out, err = self.resume()
            self.assertNotIn("effect-still-running", err)
        finally:
            child.kill()
            child.wait()

    def test_rs24_a_group_that_outlives_its_leader(self):
        plan = self.interrupted()
        leader = subprocess.Popen(["sh", "-c", "sleep 60 </dev/null >/dev/null 2>&1 & exit 0"],
                                  start_new_session=True)
        ticks = pf_instance.process_start_ticks(leader.pid)
        leader.wait()
        try:
            self.assertTrue(pf_runner.group_members(leader.pid))
            self.record_child(plan, leader.pid, ticks)
            code, out, err = self.resume()
            self.assertEqual(code, 1)
            self.assertIn("effect-still-running", err)
            with mock.patch.object(pf_runner, "group_members", return_value=None):
                code, out, err = self.resume()
            self.assertIn(f"effect-probe-unavailable: operation {plan['operation_id']} recorded child process groups, "
                          "but this host cannot inspect processes (/proc unavailable)", err)
        finally:
            try:
                os.killpg(leader.pid, 9)
            except ProcessLookupError:
                pass

    def test_rs25_a_running_owned_oneoff(self):
        plan = self.interrupted()
        state = self.fake.state()
        state["containers"].append(pfx.container("b" * 64, PROJECT + "-backend-run-0a1b2c3d4e5f",
                                                 pfx.compose_run_labels(self.context, "backend")))
        self.fake.write_state(state)
        before = pfx.operation_files(self.context, plan["operation_id"])
        code, out, err = self.resume()
        self.assertEqual(code, 1)
        self.assertIn("still has a running one-off container bbbbbbbbbbbb", err)
        self.assertEqual(pfx.operation_files(self.context, plan["operation_id"]), before)

    def test_dg1_dg3_dg8_the_operations_block_comes_first(self):
        plan = self.interrupted()
        (self.paths["configuration"] / ".env").unlink()
        state = self.fake.state()
        state["info_exit"] = 1
        self.fake.write_state(state)
        first = []
        real = pf_runner.ProcessRunner.run

        def run(runner, spec):
            if not first:
                first.append(sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else "")
            return real(runner, spec)

        with mock.patch.object(pf_runner.ProcessRunner, "run", run):
            code, out, err = self.run_main(["--instance", "staging", "status"])
        self.assertIn(f"Operations: {plan['operation_id']} deploy phase initializing", out)
        if first:
            self.assertIn(f"Operations: {plan['operation_id']}", first[0])
        self.assertLess(out.index("Operations: "), out.index("unavailable"))
        self.assertNotIn("UNVERIFIED", out)

    def test_dg2_stopped_containers(self):
        plan = self.interrupted()
        state = self.fake.state()
        for container in state["containers"]:
            container["status"] = "exited"
        self.fake.write_state(state)
        code, out, err = self.run_main(["--instance", "staging", "status"])
        self.assertIn(f"Operations: {plan['operation_id']} deploy phase initializing", out)
        self.assertIn(f"pf --instance staging resume --operation {plan['operation_id']}", out)

    def test_dg4_status_operation_detail(self):
        plan = self.interrupted()
        op = plan["operation_id"]
        code, out, err = self.run_main(["--instance", "staging", "status", "--operation", op])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Operation {op}: deploy created ", out)
        self.assertIn("  e0003 initializing database-migrate database:partflow_staging:heads=r1 unknown - -", out)
        self.assertIn("  children: 0 recorded process group(s)", out)
        self.assertNotIn("abc123", out)
        self.assertNotIn(plan["frozen_config"]["sha256"], out)
        code, out, err = self.run_main(["--instance", "staging", "status", "--operation",
                                        pfx.operation_id("update", "ffffffff")])
        self.assertIn("operation-not-found", err)

    def test_dg5_dg6_doctor_and_instances(self):
        plan = self.interrupted()
        code, out, err = self.run_main(["--instance", "staging", "doctor"])
        self.assertIn(f"Operations: {plan['operation_id']} deploy phase initializing", out)
        code, out, err = self.run_main(["instances"])
        self.assertEqual(code, 0, err)
        self.assertIn("journal=deploy/initializing", out)
        pfx.clear_operations(self.context)
        code, out, err = self.run_main(["instances"])
        self.assertIn("journal=none", out)

    def test_dg7_status_works_while_the_lock_is_held(self):
        plan = self.interrupted()
        handle = pf_instance.acquire_instance_lock(self.context)
        try:
            code, out, err = self.run_main(["--instance", "staging", "status"])
        finally:
            handle.release()
        self.assertIn(f"Operations: {plan['operation_id']}", out)


# ============================================================================ CL: through the installed launcher


@ROOT_FS
class CliRestart(ter.Base):
    """CL-5..CL-10 (subset; section 6.4) through the installed launcher (<root>/bootstrap/pf), the real
    validate_context, the fake daemon and real signals. The interrupted operation of CL-5..CL-7 is produced by an
    in-process run of the real lifecycle code with the section 6 crash seam (FakeController simulating the programs),
    because W2->W3 issue no child a signal could interrupt; every later step enters through the launcher."""

    def launch(self, arguments, answers=(), *, timeout=300):
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        with pfx.typed_terminal(answers) as stdin:
            return subprocess.run([str(self.layout.launcher), "--instance", "staging", *arguments],
                                  env=environment, cwd=str(self.layout.root.parent), stdin=stdin,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
                                  timeout=timeout)

    def transcript(self, name, arguments, result):
        self.transcripts = getattr(self, "transcripts", {})
        self.transcripts[name + ": pf " + " ".join(arguments)] = {
            "exit": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        evidence("RESUME-CLI-1-" + name, self.transcripts)

    def in_process_crash(self, arguments, target, point):
        """The real update with FakeController programs, killed by the seam at the effect whose target starts with
        ``target``; returns the operation's plan."""
        with mock.patch.object(pf, "migration_files", wraps=pf.migration_files):
            fake = tpa.FakeController(self.context)
        controller = fake

        def seam(effect_id, when):
            if when == point and controller.plan_effect(effect_id)["target"].startswith(target):
                raise pf.SimulatedCrash(f"{when} {effect_id}")

        fake._crash_point = seam
        output = io.StringIO()
        with mock.patch.object(pf, "Controller", return_value=fake), mock.patch.object(pf, "confirm"), \
                mock.patch.object(pf, "prompt_yes_no", return_value=True), \
                mock.patch.object(pf, "unattended", return_value=False), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(pf.SimulatedCrash):
                pf.main(["--instance", "staging", *arguments], installation_root=self.layout.root,
                        running_release=self.layout.release_dir, trusted_launch=True)
        self.fake_controller = fake
        return pfx.operations_of(self.context)[-1][1]

    def test_cl6_the_workspace_interval_through_the_launcher(self):
        plan = self.in_process_crash(["update", "--latest"], "workspace:bind:", "after-intent")
        op = plan["operation_id"]
        workspace = self.context.paths.workspace
        self.assertFalse(os.path.lexists(str(workspace)))
        result = self.launch(["status"])
        self.transcript("CL-6", ["status"], result)
        self.assertIn(f"Operations: {op} update phase syncing-workspace", result.stdout)
        self.assertIn("unresolved effect: ", result.stdout)
        self.assertIn("workspace:bind:", result.stdout)
        before = pfx.operations_bytes(self.context)
        for arguments in (["backup", "--emergency"], ["rollback", "--restore-db"]):
            result = self.launch(arguments)
            self.transcript("CL-6", arguments, result)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("registered-path-missing", result.stderr)
        self.assertEqual(pfx.operations_bytes(self.context), before)
        result = self.launch(["resume"], answers=["RESUME " + op[-8:]])
        self.transcript("CL-6", ["resume"], result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"note: workspace-switch-in-progress {op}", result.stdout)
        _, journal = pfx.operation(self.context, op)
        self.assertEqual(journal["phase"], "completed")
        self.assertTrue(workspace.is_dir())
        self.assertEqual(pfx.open_operations(self.context), [])

    def test_cl7_keep_workspace_through_the_launcher(self):
        old = identity(self.context.paths.workspace)
        plan = self.in_process_crash(["update", "--latest"], "workspace:bind:", "after-intent")
        op = plan["operation_id"]
        result = self.launch(["resume", "--keep-workspace"], answers=["KEEP WORKSPACE " + op[-8:]])
        self.transcript("CL-7", ["resume", "--keep-workspace"], result)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(identity(self.context.paths.workspace), old)
        _, journal = pfx.operation(self.context, op)
        self.assertEqual(journal["phase"], "completed")

    def test_cl8_update_while_a_backup_blocks(self):
        plan = pfx.lifecycle_plan(self.context, "backup")
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(plan, phase="capturing", unresolved="e0001",
                                                                      states={"e0001": "unknown"}))
        before = pfx.operations_bytes(self.context)
        self.fake.clear_calls()
        result = self.launch(["update", "--latest"])
        self.transcript("CL-8", ["update", "--latest"], result)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"ERROR: operation-open: operation {plan['operation_id']} (backup, phase capturing) is "
                      "incomplete; 'update' is not a legal next action for it.", result.stderr)
        self.assertEqual([argv for argv in self.fake.argvs() if pf.unclassified_mutation("docker", argv)], [])
        self.assertEqual(pfx.operations_bytes(self.context), before)

    def test_cl9_install_control_is_refused_while_an_operation_blocks(self):
        plan = pfx.migrating_update(self.context)
        candidate = pfx.candidate_copy(self.base, mutate={"compose.nas.yaml": lambda data: data + b"# cl9\n"})
        result = self.launch(["install", "control", "--source", str(candidate)])
        self.transcript("CL-9", ["install", "control"], result)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("instance-operation-pending", result.stderr)
        self.assertIn(plan["operation_id"], result.stderr)

    def test_cl10_purge_keep_backups_against_an_approved_delete(self):
        plan = pfx.deleting_purge(self.context)
        before = pfx.operations_bytes(self.context)
        result = self.launch(["purge", "--keep-backups"])
        self.transcript("CL-10", ["purge", "--keep-backups"], result)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"ERROR: plan-inputs-conflict: 'purge --keep-backups' asks for --keep-backups, but the open purge "
                      f"operation {plan['operation_id']} was approved with delete backups.", result.stderr)
        self.assertEqual(pfx.operations_bytes(self.context), before)


@ROOT_FS
class CliPurgeSignals(docker_scope.PurgeHarness):
    """CL-5 and CL-2 (purge variants): a resume killed (SIGKILL) or interrupted (SIGTERM) through the installed
    launcher while its effect child (a per-resource `docker rm -f` of the frozen deletion plan) is blocked."""

    def launch(self, arguments, answers=(), *, wait_marker=None, signum=None, timeout=300):
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        out_path, err_path = self.base / "cli-out.txt", self.base / "cli-err.txt"
        with open(str(out_path), "wb") as out, open(str(err_path), "wb") as err, \
                pfx.typed_terminal(answers) as stdin:
            process = subprocess.Popen([str(self.layout.launcher), "--instance", "staging", *arguments],
                                       env=environment, cwd=str(self.layout.root.parent), stdin=stdin, stdout=out,
                                       stderr=err)
            try:
                if wait_marker is not None:
                    deadline = time.monotonic() + 120
                    while not wait_marker.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(wait_marker.exists(), err_path.read_text())
                    time.sleep(0.3)
                    process.send_signal(signum)
                code = process.wait(timeout=timeout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        result = types.SimpleNamespace(returncode=code, stdout=out_path.read_text(), stderr=err_path.read_text())
        self.transcripts = getattr(self, "transcripts", [])
        self.transcripts.append({"argv": ["pf", *arguments], "exit": code, "stdout": result.stdout,
                                 "stderr": result.stderr})
        return result

    def interrupted_purge(self):
        journal = docker_scope_interrupted(self)
        op, plan, _ = self.operation()
        bundle = next(item.split(":", 1)[1] for effect in plan["effects"] if effect["target"] == "purge-bundle"
                      for item in effect["preconditions"] if item.startswith("bundle:"))
        self.assertEqual(journal["phase"], "deleting")
        return op, bundle

    def block_rm(self):
        marker = self.base / "blocked-child.pid"
        if marker.exists():
            marker.unlink()
        state = self.fake.state()
        state["block"] = {"argv_contains": ["rm", "-f"], "seconds": 300, "marker": str(marker)}
        self.fake.write_state(state)
        return marker

    def test_cl5_a_killed_resume_leaves_a_recorded_child_and_a_second_resume_refuses_until_it_ends(self):
        op, bundle = self.interrupted_purge()
        marker = self.block_rm()
        phrase = f"RESUME PURGE partflow {bundle}"
        result = self.launch(["resume"], [phrase], wait_marker=marker, signum=signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9)
        child = int(marker.read_text())
        try:
            children = json.loads((self.context.operations_dir / op / "children.json").read_bytes())
            self.assertIn(child, [item["pgid"] for item in children])
            self.assertTrue(pf_runner.group_members(child))
            before = pfx.operation_files(self.context, op)
            result = self.launch(["resume"], [phrase])
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(f"effect-still-running: effect e0005 (resource-delete deletion-plan) of operation {op} still "
                          f"has a running process group {child} (docker)", result.stderr)
            self.assertEqual(pfx.operation_files(self.context, op), before)
        finally:
            try:
                os.killpg(child, 9)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 30
        while pf_runner.group_members(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        result = self.launch(["resume"], [phrase])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.journal()["phase"], "completed")
        self.assert_lock_free()
        evidence("RESUME-CLI-1-CL-5", {"transcripts": self.transcripts})

    def test_cl2_an_interrupted_resume_records_its_effect_and_the_installer_gate_follows_the_journal(self):
        op, bundle = self.interrupted_purge()
        marker = self.block_rm()
        phrase = f"RESUME PURGE partflow {bundle}"
        result = self.launch(["resume"], [phrase], wait_marker=marker, signum=signal_module().SIGTERM)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Interrupted by signal 15", result.stderr)
        records = pf_runner.load_unresolved_effects(self.context.operations_dir / op / "unresolved-effects.json")
        self.assertTrue([item for item in records if item["outcome"] == "interrupted"])
        _, journal = pfx.operation(self.context, op)
        self.assertEqual((journal["phase"], journal["last_error"]["code"]), ("deleting", "interrupted"))
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.context])
        self.assertIn("instance-operation-pending", [item.code for item in report.conflicts])
        result = self.launch(["resume"], [phrase])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.context])
        self.assertEqual(report.conflicts, [])  # the runner record is reconciled by the closed journal
        status = self.launch(["status", "--operation", op])
        self.assertIn(f"(reconciled by journal sequence {self.journal()['sequence']})", status.stdout)
        evidence("RESUME-CLI-1-CL-2", {"transcripts": self.transcripts})

    def assert_lock_free(self):
        handle = pf_instance.acquire_instance_lock(self.context)
        handle.release()


def signal_module():
    import signal
    return signal


# ============================================================================ CL: lifecycle commands through the launcher


def upstream_repository(path):
    """The local approved remote of the CL harness: commit A (the fixture tree, Alembic head r1) and commit B (adds
    revision r2 and changes the application version). Returns (A, B)."""
    pfx.source_fixture(path, OLD)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=str(path), stderr=subprocess.DEVNULL).decode().strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    git("add", ".")
    git("commit", "-qm", "a")
    first = git("rev-parse", "HEAD")
    (path / "backend/alembic/versions/002.py").write_text("revision='r2'\ndown_revision='r1'\n")
    (path / "app-version.txt").write_text("b\n")
    git("add", ".")
    git("commit", "-qm", "b")
    return first, git("rev-parse", "HEAD")


@ROOT_FS
class CliLifecycle(ter.Base):
    """CL-1, CL-3, CL-4 (with RS-25b), CL-11..CL-14: real lifecycle commands through the installed launcher on the
    fake daemon's simulated application plane (fake_docker.py ``plane``), with real signals and a real runner timeout;
    no journal is fabricated. The installed release is the repository's with a fixture-local release source
    (protected_fixture.fixture_release_files): GitHub API answers come from a fixture file and the approved remote is
    a local Git repository (commit A deployed, commit B adds Alembic revision r2)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.upstream = self.base / "upstream"
        self.commit_a, self.commit_b = upstream_repository(self.upstream)
        self.settings_path = self.base / "release-source.json"
        self.settings = {"github": {"commits/" + sha: {"sha": sha} for sha in (self.commit_a, self.commit_b)},
                         "remote": str(self.upstream), "timeout_data": None}
        self.write_settings()
        self.layout = pfx.install_root(self.base, files=pfx.fixture_release_files(self.settings_path))
        self.fake = pfx.install_fake_docker(self.layout)
        self.context, self.paths = self.instance("staging", self.project)
        # Deployed: commit A, its tree in the workspace with a protected manifest (as a deploy records it).
        pf.write_json(self.context.state_dir / "deployed.json", {"sha": self.commit_a})
        controller = self.controller()
        controller.remote_override, controller.source_protocols = str(self.upstream), ("file",)
        controller.record_source_manifest(self.paths["workspace"], self.commit_a, verified=True)
        topology = pfx.owned_topology(self.context)
        for image in topology["images"]:
            if "-backend:" in image["repo_tags"][0]:
                image["contract"] = {"files": pf.migration_files(self.paths["workspace"]), "heads": ["r1"]}
        topology["images"].append(pfx.image(pfx.topology_image_id("a", "db"), [pf.pf_docker.DB_IMAGE], {}))
        service_label = pf.pf_docker.COMPOSE_SERVICE_LABEL
        self.plane = {
            "databases": {"partflow_staging": {"heads": ["r1"], "rows": {"public.movement": 7, "public.part": 3},
                                               "allow": True, "owner": "partflow_staging",
                                               "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}},
            "db_env": ["POSTGRES_DB=partflow_staging", "POSTGRES_USER=partflow_staging"],
            "data_volume": self.project + "_postgres_data",
            "templates": {"containers": {item["labels"][service_label]: item
                                         for item in copy.deepcopy(topology["containers"])},
                          "volumes": copy.deepcopy(topology["volumes"]),
                          "networks": copy.deepcopy(topology["networks"])}}
        self.state(topology, plane=self.plane)
        self.transcripts = []

    def write_settings(self, **changes):
        self.settings.update(changes)
        self.settings_path.write_text(json.dumps(self.settings), encoding="utf-8")

    def plane_state(self):
        return self.fake.state()["plane"]

    def set_plane(self, **changes):
        state = self.fake.state()
        state["plane"].update(changes)
        self.fake.write_state(state)

    def launch(self, arguments, answers=(), *, block=None, signum=None, timeout=300):
        """``pf --instance staging <arguments>`` through the installed launcher. ``block``: a fake_docker block whose
        child ``signum`` interrupts once it runs (without ``signum`` the command just meets the blocked child).
        Returns (result, the blocked child's PID or None)."""
        marker = None
        if block is not None:
            marker = self.base / "blocked-child.pid"
            if marker.exists():
                marker.unlink()
            state = self.fake.state()
            state["block"] = dict(block, marker=str(marker))
            self.fake.write_state(state)
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        out_path, err_path = self.base / "cli-out.txt", self.base / "cli-err.txt"
        with open(str(out_path), "wb") as out, open(str(err_path), "wb") as err, \
                pfx.typed_terminal(answers) as stdin:
            process = subprocess.Popen([str(self.layout.launcher), "--instance", "staging", *arguments],
                                       env=environment, cwd=str(self.layout.root.parent), stdin=stdin, stdout=out,
                                       stderr=err)
            try:
                if signum is not None:
                    deadline = time.monotonic() + 120
                    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(marker.exists(), out_path.read_text() + err_path.read_text())
                    time.sleep(0.3)  # the blocked child runs; the controller waits in the runner
                    process.send_signal(signum)
                code = process.wait(timeout=timeout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        result = types.SimpleNamespace(returncode=code, stdout=out_path.read_text(), stderr=err_path.read_text())
        self.transcripts.append({"argv": ["pf", *arguments], "exit": code, "stdout": result.stdout,
                                 "stderr": result.stderr})
        child = int(marker.read_text()) if marker is not None and marker.exists() else None
        return result, child

    def end_child(self, pid):
        """A blocked fake child that outlived its killed controller ends (the test stands in for the slow program)."""
        try:
            os.killpg(pid, signal_module().SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 30
        while pf_runner.group_members(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(pf_runner.group_members(pid))

    def update(self, **block):
        """``pf update --commit B --allow-migrations --skip-ci``; with ``block`` the live `alembic upgrade` child
        blocks and the launcher is SIGKILLed while it runs. Returns (result, child, operation ID)."""
        arguments = ["update", "--commit", self.commit_b, "--allow-migrations", "--skip-ci"]
        answers = ["UPDATE " + self.commit_b[:12]]
        if not block:
            result, child = self.launch(arguments, answers)
        else:
            result, child = self.launch(arguments, answers, block=dict(
                {"argv_contains": ["alembic", "upgrade"], "env": {"POSTGRES_DB": "partflow_staging"},
                 "seconds": 300}, **block), signum=signal_module().SIGKILL)
        operations = pfx.operations_of(self.context, "update")
        return result, child, operations[-1][0] if operations else None

    def live_upgrades(self):
        return [call for call in self.fake.calls() if call["argv"][:1] == ["compose"] and "alembic" in call["argv"]
                and "upgrade" in call["argv"] and call.get("POSTGRES_DB") == "partflow_staging"]

    def database(self, name="partflow_staging"):
        return self.plane_state()["databases"].get(name)

    def journal(self, op):
        return pfx.operation(self.context, op)[1]

    def row(self, test, op, effect, injection, left, outcome):
        """One CRASH_MATRIX row entered through the installed launcher (section 6.3)."""
        plan, _ = pfx.operation(self.context, op)
        target = next(item for item in plan["effects"] if item["effect_id"] == effect)
        CLI_CRASH_ROWS.append({"kind": plan["kind"], "effect": effect, "type": target["type"],
                               "target": target["target"], "injection": injection,
                               "entered_through": "installed launcher (fake daemon plane)", "state_left": left,
                               "outcome": outcome, "test": "test_operations.CliLifecycle." + test})

    @classmethod
    def tearDownClass(cls):
        if CLI_CRASH_ROWS:
            evidence("CRASH_MATRIX-cli", {"rows": CLI_CRASH_ROWS})

    def compose_calls(self):
        """Every Compose child that ran with the instance's inputs (``compose version`` carries none)."""
        return [call for call in self.fake.calls() if call["argv"][:1] == ["compose"] and "--env-file" in call["argv"]]

    @staticmethod
    def env_file(call):
        return call["argv"][call["argv"].index("--env-file") + 1]

    def left(self, op):
        journal = self.journal(op)
        unresolved = journal["unresolved_effect"]
        return {"phase": journal["phase"], "unresolved": unresolved,
                "effect_state": pf_config.effect_state(journal, unresolved) if unresolved else None}

    def checkpoint_of(self, op):
        return next(item["name"] for item in self.journal(op)["retained_artifacts"] if item["kind"] == "checkpoint")

    def test_cl3_a_killed_update_whose_migration_committed_resumes_forward_with_one_upgrade(self):
        result, child, op = self.update(apply="after")
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        self.assertEqual(self.database()["heads"], ["r2"])  # committed, the result lost with the process
        left = self.left(op)
        status, _ = self.launch(["status"])
        self.assertIn(f"Operations: {op} update phase migrating", status.stdout)
        self.assertIn("  unresolved effect: e0007 database-migrate database:partflow_staging:heads=r2 (unknown)",
                      status.stdout)
        self.assertIn(f"pf --instance staging resume --operation {op}", status.stdout)
        self.assertEqual(len(self.live_upgrades()), 1)
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Resuming operation {op} (update, phase migrating): forward", result.stdout)
        self.assertIn("observed: e0007 database-migrate database:partflow_staging:heads=r2: complete (heads r2)",
                      result.stdout)
        journal = self.journal(op)
        self.assertEqual(journal["phase"], "completed")
        self.assertEqual(len(self.live_upgrades()), 1)  # exactly one live `alembic upgrade` in total
        self.assertEqual(json.loads((self.context.state_dir / "deployed.json").read_text())["sha"], self.commit_b)
        plan, _ = pfx.operation(self.context, op)
        state = self.fake.state()
        running = {item["labels"][pf.pf_docker.COMPOSE_SERVICE_LABEL]: item["image"] for item in state["containers"]
                   if item["status"] == "running"}
        self.assertEqual({service: running[service] for service in ("backend", "frontend")},
                         {service: plan["images"][service]["id"] for service in ("backend", "frontend")})
        self.assertEqual(pfx.open_operations(self.context), [])
        self.assert_lock_free()
        self.row("test_cl3", op, "e0007", "SIGKILL (blocked live alembic upgrade, apply after)", left,
                 journal["phase"])
        evidence("RESUME-CLI-1-CL-3", {"transcripts": self.transcripts})

    def test_cl4_rs25b_a_lost_migration_result_needs_the_operator_then_rollback_restore_db_supersedes(self):
        result, child, op = self.update(apply="before")
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        self.assertEqual(self.database()["heads"], ["r1"])  # the killed upgrade never applied
        left = self.left(op)
        status, _ = self.launch(["status"])
        self.assertIn(f"Operations: {op} update phase migrating", status.stdout)
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"effect-unknown: effect e0007 (database-migrate database:partflow_staging:heads=r2) of "
                      f"operation {op} has an unknown outcome", result.stderr)
        self.assertEqual(len(self.live_upgrades()), 1)  # no alembic call on resume
        self.assertEqual(self.journal(op)["phase"], "needs_operator")
        checkpoint = self.checkpoint_of(op)
        status, _ = self.launch(["status"])
        self.assertIn(f"Operations: {op} update phase needs_operator", status.stdout)
        self.assertIn(f"pf --instance staging rollback {checkpoint} --restore-db", status.stdout)
        self.row("test_cl4", op, "e0007", "SIGKILL (blocked live alembic upgrade, apply before)", left,
                 "needs_operator")
        # RS-25b: an orphaned owned `compose run` one-off still runs; the superseding rollback refuses before its plan.
        state = self.fake.state()
        state["containers"].append(pfx.container("c" * 64, PROJECT + "-backend-run-0a1b2c3d4e5f",
                                                 pfx.compose_run_labels(self.context, "backend")))
        self.fake.write_state(state)
        before = pfx.operations_bytes(self.context)
        phrase = "RESTORE partflow_staging " + checkpoint
        result, _ = self.launch(["rollback", checkpoint, "--restore-db"], [phrase])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"effect-still-running: effect e0007 (database-migrate database:partflow_staging:heads=r2) of "
                      f"operation {op} still has a running one-off container cccccccccccc.", result.stderr)
        self.assertEqual(pfx.operations_bytes(self.context), before)  # no rollback plan was written
        self.assertEqual(self.database()["heads"], ["r1"])
        state = self.fake.state()
        state["containers"] = [item for item in state["containers"] if item["id"] != "c" * 64]
        self.fake.write_state(state)
        result, _ = self.launch(["rollback", checkpoint, "--restore-db"], [phrase])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rollback_op, plan, journal = pfx.operations_of(self.context, "rollback")[-1]
        self.assertEqual((plan["supersedes"], journal["phase"]), (op, "completed"))
        self.assertEqual(self.controller().operation_index().entry(op).cls, "superseded")
        self.assertEqual(pfx.open_operations(self.context), [])
        databases = self.plane_state()["databases"]
        self.assertEqual(databases["partflow_staging"]["heads"], ["r1"])
        kept = [name for name in databases if name.startswith("pf_keep_")]
        self.assertEqual([databases[name]["rows"] for name in kept], [{"public.movement": 7, "public.part": 3}])
        self.assertFalse([name for name in databases if pf_config.CANDIDATE_RE.fullmatch(name)
                          or name.startswith("pf_verify_")])
        self.assertEqual(len(self.live_upgrades()), 1)
        self.assert_lock_free()
        evidence("RESUME-CLI-1-CL-4", {"transcripts": self.transcripts})

    def test_cl1_a_backup_killed_in_pg_dump_resumes_with_a_new_bundle(self):
        result, child = self.launch(["backup"], block={"argv_contains": ["pg_dump"], "seconds": 300},
                                    signum=signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        op, plan, journal = pfx.operations_of(self.context, "backup")[-1]
        first = plan["effects"][0]["preconditions"][0].split(":", 1)[1]
        left = self.left(op)
        status, _ = self.launch(["status"])
        self.assertIn(f"Operations: {op} backup phase capturing", status.stdout)
        self.assertIn("  unresolved effect: e0001 capture checkpoint:scheduled-or-manual-backup (unknown)", status.stdout)
        self.assertIn(f"pf --instance staging resume --operation {op}", status.stdout)
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = self.journal(op)
        self.assertEqual(journal["phase"], "completed")
        self.assertIn({"kind": "bundle-attempt", "name": first, "sha256": None}, journal["retained_artifacts"])
        checkpoint = self.checkpoint_of(op)
        self.assertNotEqual(checkpoint, first)
        listing, _ = self.launch(["backups"])
        self.assertIn(checkpoint, listing.stdout)
        self.assertIn(f"{checkpoint}  [healthy|data-restore]", listing.stdout)
        self.assertIn(f"{first}  [invalid: manifest-missing]", listing.stdout)  # kept as evidence, never selectable
        self.assertFalse([name for name in self.plane_state()["databases"] if name.startswith("pf_verify_")])
        self.assert_lock_free()
        self.row("test_cl1", op, "e0001", "SIGKILL (blocked pg_dump)", left, journal["phase"])
        evidence("RESUME-CLI-1-CL-1", {"transcripts": self.transcripts})

    def test_cl11_a_runner_timeout_closes_a_backup_and_leaves_an_update_for_resume(self):
        self.write_settings(timeout_data=3)
        result, child = self.launch(["backup"], block={"argv_contains": ["pg_dump"], "seconds": 120})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("timed out", result.stderr)
        self.assertFalse(pf_runner.group_members(child))  # the runner ended the child's group
        op, _, journal = pfx.operations_of(self.context, "backup")[-1]
        self.assertEqual((journal["phase"], journal["last_error"]["code"]), ("failed_preserved", "timeout"))
        self.assertEqual(pfx.open_operations(self.context), [])
        # pg_dump is a read-only child, so its timeout leaves no runner record; a timed-out verification restore
        # (an effect-carrying child) does, and the closed journal reconciles it.
        self.assertFalse(os.path.lexists(str(self.context.operations_dir / op / "unresolved-effects.json")))
        self.row("test_cl11", op, "e0001", "timeout (pg_dump past TIMEOUT_DATA)", {"phase": "capturing"},
                 journal["phase"])
        result, child = self.launch(["backup"], block={"argv_contains": ["pg_restore"], "argv_match": r" -d pf_verify_",
                                                       "seconds": 120})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(pf_runner.group_members(child))
        op, _, journal = pfx.operations_of(self.context, "backup")[-1]
        self.assertEqual((journal["phase"], journal["last_error"]["code"]), ("failed_preserved", "timeout"))
        self.assertIn("Verification of ", journal["last_error"]["message"])
        records = pf_runner.load_unresolved_effects(self.context.operations_dir / op / "unresolved-effects.json")
        self.assertEqual([item["outcome"] for item in records], ["timeout"])
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.context])
        self.assertEqual(report.conflicts, [])  # the runner record is reconciled by the closed journal
        detail, _ = self.launch(["status", "--operation", op])
        self.assertIn(f"(reconciled by journal sequence {journal['sequence']})", detail.stdout)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["compose"]
                          and argv[-3:] == ["stop", "frontend", "backend"]])  # a backup never stops the application
        self.row("test_cl11", op, "e0002", "timeout (verification pg_restore past TIMEOUT_DATA)",
                 {"phase": "verifying"}, journal["phase"])
        transcripts = list(self.transcripts)
        for apply, outcome in (("after", "completed"), ("before", "needs_operator")):
            with self.subTest(apply=apply):
                self.tearDown()
                self.setUp()
                self.write_settings(timeout_data=3)
                arguments = ["update", "--commit", self.commit_b, "--allow-migrations", "--skip-ci"]
                result, child = self.launch(arguments, ["UPDATE " + self.commit_b[:12]], block={
                    "argv_contains": ["alembic", "upgrade"], "env": {"POSTGRES_DB": "partflow_staging"},
                    "seconds": 120, "apply": apply})
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(pf_runner.group_members(child))
                op, _, journal = pfx.operations_of(self.context, "update")[-1]
                self.assertEqual((journal["phase"], journal["unresolved_effect"], journal["last_error"]["code"]),
                                 ("migrating", "e0007", "timeout"))
                self.assertEqual(pf_config.effect_state(journal, "e0007"), "unknown")
                left = self.left(op)
                status, _ = self.launch(["status"])
                self.assertIn(f"Operations: {op} update phase migrating", status.stdout)
                self.assertIn("  last error: timeout: ", status.stdout)
                self.assertIn(f"pf --instance staging resume --operation {op}", status.stdout)
                result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
                self.assertEqual(result.returncode, 0 if outcome == "completed" else 1, result.stdout + result.stderr)
                self.assertEqual(self.journal(op)["phase"], outcome)
                self.assertEqual(len(self.live_upgrades()), 1)
                self.assertEqual(self.database()["heads"], ["r2"] if apply == "after" else ["r1"])
                self.row("test_cl11", op, "e0007", f"timeout (live alembic upgrade past TIMEOUT_DATA, apply {apply})",
                         left, outcome)
                transcripts += self.transcripts
        evidence("RESUME-CLI-1-CL-11", {"transcripts": transcripts})

    def frozen_calls(self, calls):
        """{(env-file, URL hash)} of the active database's Compose children in ``calls``."""
        return {(self.env_file(call), call["database_url_sha256"]) for call in calls
                if call.get("POSTGRES_DB") == "partflow_staging"}

    def edit_env(self):
        path = self.paths["configuration"] / ".env"
        edited = path.read_bytes().replace(b"PARTFLOW_HTTP_PORT=5173", b"PARTFLOW_HTTP_PORT=5180") \
            .replace(b"POSTGRES_PASSWORD=abc123", b"POSTGRES_PASSWORD=edited-after-freeze-7")
        path.write_bytes(edited)
        return path, edited, hashlib.sha256(pf_config.database_url(
            "partflow_staging", "edited-after-freeze-7", "partflow_staging").encode("utf-8")).hexdigest()

    def test_cl12_config_edits_after_the_freeze_never_reach_a_resumed_update(self):
        result, child, op = self.update(apply="after")
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        frozen = self.frozen_calls(self.compose_calls())
        self.assertEqual(len(frozen), 1, frozen)
        env_file, url_hash = next(iter(frozen))
        self.assertTrue(env_file.startswith(str(self.context.operations_dir / op) + "/"), env_file)
        env_path, edited_env, edited_hash = self.edit_env()
        admin_path = self.paths["configuration"] / "pf-config.json"
        admin_path.write_text(json.dumps(dict(json.loads(admin_path.read_text()), health_timeout_seconds=1,
                                              auto_update=True)) + "\n")
        edited_admin = admin_path.read_bytes()
        # The frontend reports `starting` once: the frozen 180 s health timeout waits for it; the edited 1 s would not.
        self.set_plane(health={"frontend": ["starting", "healthy"]})
        self.fake.clear_calls()
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.journal(op)["phase"], "completed")
        self.assertIn(f"note: config-proposal-differs: config/.env differs from the configuration operation {op} "
                      "froze", result.stdout)
        self.assertIn(f"note: admin-config-proposal-differs: config/pf-config.json differs from (or is missing "
                      f"against) the copy operation {op} froze", result.stdout)
        self.assertEqual((env_path.read_bytes(), admin_path.read_bytes()), (edited_env, edited_admin))
        calls = self.compose_calls()
        self.assertTrue(calls)
        self.assertEqual({self.env_file(call) for call in calls}, {env_file})
        self.assertEqual({call["database_url_sha256"] for call in calls}, {url_hash})
        self.assertNotEqual(url_hash, edited_hash)
        self.assertEqual(self.plane_state()["health"]["frontend"], ["healthy"])  # the `starting` answer was waited out
        self.assertNotIn("edited-after-freeze-7", result.stdout + result.stderr)
        evidence("RESUME-CLI-1-CL-12", {"transcripts": self.transcripts, "frozen_env_file": env_file,
                                        "frozen_url_sha256": url_hash, "edited_url_sha256": edited_hash})

    def test_cl13_a_killed_rollback_restore_db_resumes_on_its_frozen_snapshot(self):
        result, _ = self.launch(["backup"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checkpoint = self.checkpoint_of(pfx.operations_of(self.context, "backup")[-1][0])
        result, child = self.launch(["rollback", checkpoint, "--restore-db"], ["RESTORE partflow_staging " + checkpoint],
                                    block={"argv_contains": ["pg_restore"], "argv_match": r" -d pf_restore_",
                                           "seconds": 300}, signum=signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        op, plan, journal = pfx.operations_of(self.context, "rollback")[-1]
        self.assertEqual((journal["phase"], journal["unresolved_effect"]), ("restoring-candidate", "e0004"))
        left = self.left(op)
        frozen = self.frozen_calls([call for call in self.compose_calls()
                                    if self.env_file(call).startswith(str(self.context.operations_dir / op))])
        self.assertEqual(len(frozen), 1, frozen)
        env_file, url_hash = next(iter(frozen))
        env_path, edited_env, edited_hash = self.edit_env()
        self.fake.clear_calls()
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"note: config-proposal-differs: config/.env differs from the configuration operation {op} "
                      "froze", result.stdout)
        self.assertEqual(self.journal(op)["phase"], "completed")
        self.assertEqual(env_path.read_bytes(), edited_env)
        calls = self.compose_calls()
        self.assertEqual({self.env_file(call) for call in calls}, {env_file})
        self.assertEqual({call["database_url_sha256"] for call in calls
                          if call.get("POSTGRES_DB") == "partflow_staging"}, {url_hash})
        self.assertNotEqual(url_hash, edited_hash)
        databases = self.plane_state()["databases"]
        self.assertEqual(databases["partflow_staging"]["heads"], ["r1"])
        self.assertFalse([name for name in databases if name.startswith(("pf_restore_", "pf_verify_"))])
        self.assertEqual(len([name for name in databases if name.startswith("pf_keep_")]), 1)
        self.row("test_cl13", op, "e0004", "SIGKILL (blocked pg_restore into the rollback candidate)", left,
                 "completed")
        evidence("RESUME-CLI-1-CL-13", {"transcripts": self.transcripts, "frozen_env_file": env_file})

    def test_rs18_a_purge_killed_inside_the_allow_connections_window_closes_the_flag_and_reopens(self):
        kept = "pf_keep_20261001t000000z_0c0ffe"  # never containing the fixture password (output is redacted)
        databases = self.plane_state()["databases"]
        databases[kept] = {"heads": ["r1"], "rows": {"public.part": 2}, "allow": False, "owner": "partflow_staging",
                           "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
        self.set_plane(databases=databases)
        result, child = self.launch(["purge", "--keep-backups"], ["PURGE " + self.project],
                                    block={"argv_contains": ["pg_dump"], "argv_match": r" -d pf_keep_", "seconds": 300},
                                    signum=signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        self.assertTrue(self.database(kept)["allow"])  # the process died inside the window
        op, plan, journal = pfx.operations_of(self.context, "purge")[-1]
        capture = next(item for item in plan["effects"] if item["target"] == "purge-bundle")
        self.assertIn("allow_connections=false:" + kept, capture["preconditions"])
        self.assertEqual((journal["phase"], journal["unresolved_effect"]), ("capturing", capture["effect_id"]))
        left = self.left(op)
        status, _ = self.launch(["status"])
        self.assertIn(f"Operations: {op} purge phase capturing", status.stdout)
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Resuming operation {op} (purge, phase capturing): reopen unchanged deployment", result.stdout)
        self.assertEqual(self.journal(op)["phase"], "cancelled")
        self.assertFalse(self.database(kept)["allow"])
        self.assertEqual(self.database(kept)["rows"], {"public.part": 2})
        state = self.fake.state()
        self.assertEqual(sorted(item["labels"][pf.pf_docker.COMPOSE_SERVICE_LABEL] for item in state["containers"]
                                if item["status"] == "running"), ["backend", "db", "frontend"])
        self.assertTrue((self.paths["configuration"] / ".env").is_file())
        self.assertEqual(pfx.open_operations(self.context), [])
        self.row("test_rs18", op, capture["effect_id"], "SIGKILL (blocked pg_dump inside the ALLOW_CONNECTIONS window)",
                 left, "cancelled")
        evidence("RESUME-CLI-1-RS-18", {"transcripts": self.transcripts})

    def test_rs18b_abandon_of_a_purge_killed_inside_the_window_also_closes_the_flag_and_reopens(self):
        # Audit F2: section 3.6 makes --abandon the "same" as resume before the deletion generation.
        kept = "pf_keep_20261001t000000z_0c0ffe"
        databases = self.plane_state()["databases"]
        databases[kept] = {"heads": ["r1"], "rows": {"public.part": 2}, "allow": False, "owner": "partflow_staging",
                           "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
        self.set_plane(databases=databases)
        result, child = self.launch(["purge", "--keep-backups"], ["PURGE " + self.project],
                                    block={"argv_contains": ["pg_dump"], "argv_match": r" -d pf_keep_", "seconds": 300},
                                    signum=signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        self.assertTrue(self.database(kept)["allow"])
        op, _, journal = pfx.operations_of(self.context, "purge")[-1]
        self.assertEqual(journal["phase"], "capturing")
        result, _ = self.launch(["resume", "--abandon"], ["ABANDON " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Resuming operation {op} (purge, phase capturing): abandon", result.stdout)
        self.assertEqual(self.journal(op)["phase"], "cancelled")
        self.assertFalse(self.database(kept)["allow"])
        self.assertEqual(self.database(kept)["rows"], {"public.part": 2})
        state = self.fake.state()
        self.assertEqual(sorted(item["labels"][pf.pf_docker.COMPOSE_SERVICE_LABEL] for item in state["containers"]
                                if item["status"] == "running"), ["backend", "db", "frontend"])
        self.assertEqual(pfx.open_operations(self.context), [])

    def purge_in_process(self):
        """The preceding purge (in-process, the real controller on the same fake plane; its random ERASE challenge
        needs the patched confirmation). Returns the recovery bundle ID."""
        with mock.patch.object(pf, "confirm"), mock.patch.object(pf, "prompt_yes_no", return_value=False):
            code, out, err = self.run_main(["--instance", "staging", "purge", "--keep-backups"], interactive=True)
        self.assertEqual(code, 0, out + err)
        op, plan, journal = pfx.operations_of(self.context, "purge")[-1]
        self.assertEqual(journal["phase"], "completed")
        return next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")

    def test_cl14_restore_instance_keeps_an_edited_env_and_resumes_on_the_bundle_snapshot(self):
        bundle = self.purge_in_process()
        env_path = self.paths["configuration"] / ".env"
        self.assertFalse(env_path.exists())
        self.assertEqual(self.plane_state()["databases"], {})
        edited = pfx.ENV_TEXT.replace("PARTFLOW_HTTP_PORT=5173", "PARTFLOW_HTTP_PORT=5180") \
            .replace("POSTGRES_PASSWORD=abc123", "POSTGRES_PASSWORD=edited-after-freeze-7").encode("utf-8")
        env_path.write_bytes(edited)
        self.fake.clear_calls()
        result, child = self.launch(["restore-instance", bundle],
                                    ["RESTORE INSTANCE " + self.project, "RESTORE partflow_staging " + bundle],
                                    block={"argv_contains": ["image", "load"], "seconds": 300, "apply": "before"},
                                    signum=signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        op, plan, journal = pfx.operations_of(self.context, "restore-instance")[-1]
        self.assertEqual(pf_config.effect_state(journal, "e0002"), "complete")  # the .env write
        proposal = self.paths["configuration"] / (".env.proposal-" + op[-8:])
        self.assertEqual(proposal.read_bytes(), edited)
        bundle_env = env_path.read_bytes()
        self.assertNotEqual(bundle_env, edited)
        self.assertEqual("bytes sha256 " + hashlib.sha256(bundle_env).hexdigest(), plan["effects"][1]["postcondition"])
        left = self.left(op)
        self.fake.clear_calls()
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.journal(op)["phase"], "completed")
        self.assertEqual((env_path.read_bytes(), proposal.read_bytes()), (bundle_env, edited))
        bound = {self.env_file(call) for call in self.compose_calls()
                 if self.env_file(call).startswith(str(self.context.operations_dir / op))}
        self.assertEqual(len(bound), 1, bound)
        snapshot = Path(next(iter(bound)))
        self.assertRegex(str(snapshot.parent.name), r"^refreeze-[0-9]+$")
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), plan["frozen_config"]["sha256"])
        restored_hash = hashlib.sha256(pf_config.database_url("partflow_staging", "abc123",
                                                              "partflow_staging").encode("utf-8")).hexdigest()
        self.assertEqual({call["database_url_sha256"] for call in self.compose_calls()
                          if self.env_file(call) == str(snapshot) and call.get("POSTGRES_DB") == "partflow_staging"},
                         {restored_hash})
        databases = self.plane_state()["databases"]
        self.assertEqual((databases["partflow_staging"]["heads"], databases["partflow_staging"]["rows"]),
                         (["r1"], {"public.movement": 7, "public.part": 3}))
        self.row("test_cl14", op, "e0003", "SIGKILL (blocked image load after the .env write)", left, "completed")
        evidence("RESUME-CLI-1-CL-14", {"transcripts": self.transcripts, "bundle_snapshot": str(snapshot)})


@ROOT_FS
class RestoreResume(Restartable):
    """RS-31..RS-33, RS-35..RS-37 and CF-7: restore-instance resume, abandon, the frozen bundle snapshot and the
    displaced checkpoint history (FakeController programs; the strict bundle reader and the files are real)."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []
        self.env_path = self.c.config_dir / ".env"

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def purged(self, *, env=None):
        """The restore arguments of a purged instance (no .env unless ``env`` bytes are given)."""
        arguments = restore_setup(self)
        self.bundle_env = self.env_path.read_bytes()
        if env is None:
            self.env_path.unlink()
        else:
            self.env_path.write_bytes(env)
        return arguments

    def operation(self):
        return tpa.latest_operation(self.c, "restore-instance")

    def test_rs31_abandon_after_the_db_start_removes_what_the_restore_created(self):
        arguments = self.purged()
        self.crash(arguments, "service:db:start", "after-effect")
        op, plan, journal = self.operation()
        self.assertTrue(self.env_path.exists())  # the bundle's .env, written by this operation
        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assertIn("Restore-instance abandon plan (exact resources of this instance; images are retained):",
                      self.output.getvalue())
        _, _, journal = self.operation()
        self.assertEqual(journal["phase"], "cancelled")
        self.assertIsNotNone(journal["deletion"])
        self.assertEqual(self.c.resources["containers"], [])
        self.assertEqual(self.c.resources["volumes"], [])
        self.assertFalse(self.env_path.exists())
        staging = [name for name in os.listdir(str(self.c.deployments_dir)) if name.startswith(".staging-")] \
            if self.c.deployments_dir.exists() else []
        self.assertEqual(staging, [])
        attempts = json.loads((self.context.operations_dir / op / "attempts.json").read_bytes())
        self.assertEqual(attempts[-1]["action"], "abandon")
        # The same bundle restores again on the emptied target.
        self.c.dbs, self.c.running = {}, {"db": False, "backend": False, "frontend": False}
        self.assertEqual(self.invoke(arguments), 0, self.last_error)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])

    def test_rs32_a_crashed_abandon_finishes_only_by_abandon(self):
        arguments = self.purged()
        self.crash(arguments, "service:db:start", "after-effect")
        op, _, _ = self.operation()
        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        real = self.c.docker
        removed = []

        def docker(*args, **kwargs):
            result = real(*args, **kwargs)
            if args[:2] == ("rm", "-f"):
                removed.append(args)
                raise pf.SimulatedCrash("inside the abandon deletion")
            return result

        with mock.patch.object(self.c, "docker", side_effect=docker):
            with self.assertRaises(pf.SimulatedCrash):
                self.invoke(["resume", "--abandon"])
        self.restart()
        self.assertEqual(len(removed), 1)
        before = pfx.operation_files(self.context, op)
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn(f"abandon-in-progress: operation {op} (restore-instance) is being abandoned with a frozen "
                      "deletion plan;", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assertEqual(self.c.resources["volumes"], [])
        _, _, journal = self.operation()
        self.assertEqual(journal["phase"], "cancelled")

    def test_rs33_an_unreadable_bundle_refuses_forward_but_not_abandon(self):
        arguments = self.purged()
        self.crash(arguments, "config:.env", "after-effect")
        op, _, _ = self.operation()
        moved = Path(self.temp.name) / "bundle-moved"
        os.rename(str(self.view.folder), str(moved))
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn("plan-input-changed", self.last_error)
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        _, _, journal = self.operation()
        self.assertEqual(journal["phase"], "cancelled")

    def test_rs35_cf7_abandon_at_every_point_of_the_env_write_gives_an_edit_back(self):
        for point in pf.CRASH_POINTS:
            with self.subTest(point=point):
                self.tearDown()
                self.setUp()
                edited = b"# operator edit\n" + self.env_path.read_bytes().replace(b"abc123", b"edited999")
                arguments = self.purged(env=edited)
                self.crash(arguments, "config:.env", point)
                op, plan, _ = self.operation()
                proposal = self.c.config_dir / (".env.proposal-" + op[-8:])
                if point == "after-effect":
                    self.assertEqual(proposal.read_bytes(), edited)
                    self.assertNotEqual(self.env_path.read_bytes(), edited)
                self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
                self.assertEqual(self.env_path.read_bytes(), edited)
                self.assertFalse(proposal.exists())
                _, _, journal = self.operation()
                self.assertEqual(journal["phase"], "cancelled")

    def test_cf7_an_identical_env_writes_no_proposal_and_an_edit_is_kept(self):
        arguments = self.purged(env=None)
        # The bundle's .env bytes: written by a first attempt, which is then abandoned (its own bytes removed).
        self.crash(arguments, "config:.env", "after-effect")
        written = self.env_path.read_bytes()
        self.assertEqual(self.invoke(["resume", "--abandon"]), 0, self.last_error)
        self.assertFalse(self.env_path.exists())
        self.env_path.write_bytes(written)
        self.assertEqual(self.invoke(arguments), 0, self.last_error)
        op, _, _ = self.operation()
        self.assertFalse((self.c.config_dir / (".env.proposal-" + op[-8:])).exists())
        self.assertEqual(self.env_path.read_bytes(), written)

    def test_rs36_only_the_snapshot_the_plan_names_is_bound(self):
        arguments = self.purged()
        self.crash(arguments, "images:", "after-intent")
        op, plan, _ = self.operation()
        directory = self.context.operations_dir / op
        named = [path.parent for path in directory.rglob("frozen-config.json")
                 if json.loads(path.read_bytes())["env_file_sha256"] == plan["frozen_config"]["sha256"]]
        self.assertEqual(len(named), 1)
        decoy = directory / "refreeze-9"
        shutil.copytree(str(named[0]), str(decoy)) if named[0] != directory else decoy.mkdir()
        for name in ("app.env", "frozen-config.json"):
            if (decoy / name).exists():
                os.chmod(str(decoy / name), 0o600)
        (decoy / "app.env").write_bytes(b"POSTGRES_PASSWORD=decoy\n")
        record = json.loads((named[0] / "frozen-config.json").read_bytes())
        record["env_file_sha256"] = hashlib.sha256(b"POSTGRES_PASSWORD=decoy\n").hexdigest()
        (decoy / "frozen-config.json").write_text(json.dumps(record))
        snapshot = named[0] / "app.env"
        original = snapshot.read_bytes()
        os.chmod(str(snapshot), 0o600)
        snapshot.write_bytes(original + b"# tampered\n")
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn(f"plan-input-changed: frozen application configuration of operation {op}", self.last_error)
        snapshot.write_bytes(original)
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        self.assertNotIn("decoy", "".join(str(call) for call in self.c.calls))

    def test_rs37_an_existing_checkpoint_history_is_displaced_never_removed(self):
        arguments = self.purged()
        project_tree = self.c.revisions_root / self.c.config["project"]
        if not project_tree.exists():
            self.skipTest("the fixture bundle carries no checkpoint history")
        (project_tree / "added-after-the-bundle.txt").write_text("operator file")
        before = ta.tree_hash(project_tree)
        removed = []
        real = shutil.rmtree

        def rmtree(path, *args, **kwargs):
            removed.append(str(path))
            return real(path, *args, **kwargs)

        with mock.patch.object(shutil, "rmtree", side_effect=rmtree):
            self.crash(arguments, "checkpoint-history", "after-effect")
            op, _, _ = self.operation()
            self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        displaced = self.c.revisions_root / f"{self.c.config['project']}.pre-restore-{op[-8:]}"
        self.assertEqual(ta.tree_hash(displaced), before)
        self.assertFalse([path for path in removed if str(self.c.revisions_root) in path])
        _, _, journal = self.operation()
        self.assertIn({"kind": "checkpoint-history", "name": displaced.name, "sha256": None},
                      journal["retained_artifacts"])
        start = len(self.output.getvalue())
        self.assertEqual(self.invoke(["backups"]), 0)
        self.assertNotIn(displaced.name, self.output.getvalue()[start:])


class ResumeDatabase(Restartable):
    """RS-7 (a database switch left half done), RS-26c (the db service stopped), RS-13 and SG-4 (seal failure)."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def test_rs7_rs8_a_half_done_switch_needs_the_operator_and_a_not_started_one_forwards(self):
        self.crash(["reset-db"], "database-switch:", "after-intent")
        op, plan, _ = tpa.latest_operation(self.c, "reset-db")
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)  # RS-8: names unchanged -> redo forward
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], [])
        self.tearDown()
        self.setUp()
        self.crash(["reset-db"], "database-switch:", "after-intent")
        op, plan, _ = tpa.latest_operation(self.c, "reset-db")
        target = next(item["target"] for item in plan["effects"] if item["type"] == "database-switch")
        _, current, prepared, retained = target.split(":")
        self.c.dbs[retained] = self.c.dbs.pop(current)  # the first rename happened, the second did not
        self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn(f"database-switch-unknown: the database rename of operation {op} left {current} absent, "
                      f"{prepared} present, {retained} present;", self.last_error)
        self.assertEqual(self.blocking(), [("reset-db", "needs_operator")])
        self.assertEqual(self.c.dbs[retained]["rows"], ["old-record"])

    def test_rs26c_a_stopped_database_is_started_only_after_the_confirmation(self):
        self.c.new_migration = True
        self.crash(["update", "--latest", "--allow-migrations"], "e0007", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "update")
        self.c.running["db"] = False
        order = []
        real_compose = self.c.compose

        def compose(*args, **kwargs):
            if args[0] == "up":
                order.append(("up", args[-1]))
            return real_compose(*args, **kwargs)

        def confirm(phrase, warning):
            order.append(("confirm", phrase))

        with mock.patch.object(self.c, "compose", side_effect=compose):
            self.assertEqual(self.invoke(["resume"], confirm=mock.Mock(side_effect=confirm)), 1)
        self.assertEqual(order, [("confirm", "RESUME " + op[-8:]), ("up", "db")])
        self.assertIn("effect-unknown", self.last_error)  # observed after the start: heads unchanged
        self.c.running["db"] = False
        before = pfx.operation_files(self.context, op)
        self.tearDown()
        self.setUp()
        self.c.new_migration = True
        self.crash(["update", "--latest", "--allow-migrations"], "e0007", "after-intent")
        op, _, _ = tpa.latest_operation(self.c, "update")
        self.c.running["db"] = False
        before = pfx.operation_files(self.context, op)

        def failing(*args, **kwargs):
            if args[0] == "up":
                raise pf.Failure("simulated: db did not start")
            return real_compose(*args, **kwargs)

        real_compose = self.c.compose
        with mock.patch.object(self.c, "compose", side_effect=failing):
            self.assertEqual(self.invoke(["resume"]), 1)
        self.assertIn(f"database-unavailable: the database service could not be started to observe effect e0007 of "
                      f"operation {op} (simulated: db did not start). The operation is unchanged.", self.last_error)
        self.assertEqual(pfx.operation_files(self.context, op), before)

    def test_rs13_sg4_a_seal_failure_preserves_and_the_next_sweep_keeps_the_active_staging(self):
        with mock.patch.object(self.c, "seal_deployment", side_effect=pf.Failure("simulated seal failure")):
            self.assertEqual(self.invoke(["update", "--latest"]), 1)
        op, plan, journal = tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "failed_preserved")
        self.assertEqual(self.blocking(), [])
        self.assertTrue(all(self.c.running.values()))
        dep = plan["source"]["deployment_id"]
        self.assertEqual(self.pointer()["deployment_seal_failed"], op)
        artifact = json.loads((self.context.operations_dir / op / "deployment-artifact.json").read_bytes())
        self.assertEqual(artifact["state"], "seal-failed")
        staging = self.c.deployments_dir / (".staging-" + dep)
        self.assertTrue(staging.is_dir())
        self.assertEqual(self.c.workspace_status()["head"], NEW)  # the workspace was still refreshed
        self.c.target = {"sha": "3" * 40, "ref": "v0.3", "release_id": 3}
        start = len(self.output.getvalue())
        self.c.sweep_staging()
        self.assertIn(f"note: unsealed-active-staging: {dep}", self.output.getvalue()[start:])
        self.assertTrue(staging.is_dir())
