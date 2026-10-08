"""PF-A3.3: integrated operations: the isolated topology, the exact-bundle functional recovery verification, the
instance purge gate and registry tombstone, restore target identity and database image, side-by-side recovery,
current-data protection in reset/rollback/abort-deploy, the capacity model, pf cleanup and the runner-record
acknowledgement.

Case mapping (PF-A3.3 SPEC section 6.1):
  IsolatedModel IT-1..IT-9, FunctionalVerification FV-1..FV-8, ImageArchive IA-1..IA-6, ReconcileReport RQ-1..RQ-8,
  PurgeIntegrated PZ-1..PZ-15 (+ the OD-A33-08 registry tombstone rows TB-*), RestoreTarget RX-*, SideBySide SB-*,
  ResetRollback RP-*, AbortDeploy AD-*, Capacity CP-*, Cleanup CU-*, Acknowledge AK-*, SchemaA33 SA3-1..SA3-4,
  InstalledCli XC-*.

Docker and PostgreSQL are simulated by the fake daemon (tests/fake_docker.py: the instance plane and the isolated
planes of the topologies). The integration classes answer every docker child in-process through
protected_fixture.InProcessRunner (the same dispatch and state as the installed program, without a process); the
InstalledCli class runs the installed launcher with the program. Files, locks, renames, archives and the real
controller code are real. Nothing touches a real daemon, a real NAS or the running PartFlow stack. Evidence JSON is
written only when PF_A33_EVIDENCE names a directory.
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
import tarfile
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_instance = pfx.pf_instance
pf_config = pf.pf_config
pf_docker = pf.pf_docker
pf_source = pf.pf_source
pf_runner = pf.pf_runner
pf_install = pf.pf_install
CONTRACTS = pfx.PACKAGE / "contracts"
EXAMPLES = CONTRACTS / "examples" / "lifecycle"
ROOT_FS = unittest.skipUnless(os.geteuid() == 0, "real-filesystem cases run as uid 0 inside the throwaway container")
PROJECT = "partflow-staging"
GROUP = __import__("grp").getgrgid(os.getgid()).gr_name
SECRET = "abc123"  # the fixture's POSTGRES_PASSWORD (pfx.ENV_TEXT)


def evidence(name, payload):
    """Section 6.4 evidence (JSON) when PF_A33_EVIDENCE names a directory; nothing otherwise."""
    directory = os.environ.get("PF_A33_EVIDENCE")
    if not directory:
        return
    path = Path(directory) / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


def downgrade_calls(argvs):
    """RP-6 (module-level helper used by every class): no ``alembic downgrade`` argv anywhere."""
    return [argv for argv in argvs if "alembic" in argv and "downgrade" in argv]


# ============================================================================ pure layer


def fixture_model(values=None, *, project="pfverify-0123456789ab", topology_uuid="7d3c1a2b-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
                  directory="/ps/operations/op/isolated/pfverify-0123456789ab"):
    values = dict(values or pf_config.parse_app_env(pfx.ENV_TEXT.encode(), label=".env"))
    child = pf_config.child_values(values, workspace=directory, instance_id=topology_uuid)
    model = pfx.FAKE_DOCKER.render_model(pfx.COMPOSE_FIXTURE, child, project)
    expectation = pf_docker.ComposeExpectation(project=project, instance_id=topology_uuid, repo_root=directory,
                                               values=types.MappingProxyType({k: values[k] for k in pf_config.APP_KEYS}),
                                               database_url=child["PARTFLOW_DATABASE_URL"], images=None)
    return model, expectation


IMAGES = {"db": pfx.image_id("postgres-16"), "backend": pfx.image_id("abackend"), "frontend": pfx.image_id("afrontend")}


class IsolatedModel(unittest.TestCase):
    """IT-1, IT-2, IT-9 (pure part): the closed transformation, its independent allowlist and the daemon observation."""

    def isolated(self):
        model, expectation = fixture_model()
        pf_docker.validate_envelope(model, expectation)
        return pf_docker.isolate_model(model, images=IMAGES), expectation

    def test_it1_isolate_model_and_the_allowlist(self):
        model, expectation = self.isolated()
        services = model["services"]
        self.assertNotIn("ports", services["frontend"])
        self.assertTrue(all("build" not in services[name] for name in pf_docker.SERVICES))
        self.assertEqual({name: services[name]["image"] for name in pf_docker.SERVICES}, IMAGES)
        self.assertEqual({services[name]["restart"] for name in pf_docker.SERVICES}, {"no"})
        self.assertIs(model["networks"]["default"]["internal"], True)
        self.assertEqual(services["db"]["healthcheck"]["test"], pf_docker.HEALTHCHECK_DOUBLED)
        self.assertEqual(model["volumes"]["postgres_data"]["labels"],
                         {pf_docker.INSTANCE_LABEL: expectation.instance_id})
        result = pf_docker.validate_isolated_model(model, expectation, IMAGES)
        self.assertEqual(result.escape_mode, "doubled")

    def test_it2_every_violation_is_refused_with_its_code(self):
        def set_path(model, path, value):
            target = model
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value

        variants = {
            "published port": ([("services", "frontend", "ports"), [{"target": 5173, "published": "5173",
                                                                    "host_ip": "0.0.0.0"}]], "isolated-port"),
            "loopback port": ([("services", "frontend", "ports"), [{"target": 5173, "published": "15173",
                                                                   "host_ip": "127.0.0.1"}]], "isolated-port"),
            "non-internal network": ([("networks", "default", "internal"), False], "isolated-network"),
            "extra network": ([("networks", "other"), {"name": "x"}], "isolated-network"),
            "bind mount": ([("services", "backend", "volumes"), [{"type": "bind", "source": "/srv",
                                                                 "target": "/srv"}]], "isolated-mount"),
            "restart unless-stopped": ([("services", "db", "restart"), "unless-stopped"], "isolated-restart"),
            "image tag": ([("services", "backend", "image"), PROJECT + "-backend:backup-x"], "isolated-image"),
            "wrong label": ([("services", "db", "labels"), {pf_docker.INSTANCE_LABEL: "other"}], "isolated-label"),
            "dollar": ([("services", "frontend", "environment", "BACKEND_PROXY_TARGET"), "http://$x"],
                       "isolated-escape"),
            "project": ([("name",), "other-project"], "isolated-project"),
            "volume name": ([("volumes", "postgres_data", "name"), "other_postgres_data"], "isolated-mount"),
            "network name": ([("networks", "default", "name"), "other_default"], "isolated-network"),
            "extra env key": ([("services", "backend", "environment", "EXTRA"), "1"], "envelope-environment"),
            "privileged": ([("services", "backend", "privileged"), True], "envelope-forbidden"),
        }
        for label, ((path, value), code) in variants.items():
            with self.subTest(label):
                model, expectation = self.isolated()
                set_path(model, path, value)
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.validate_isolated_model(model, expectation, IMAGES)
                self.assertIn(code, [finding.code for finding in caught.exception.findings], label)
        model, expectation = self.isolated()
        model["services"]["extra"] = dict(model["services"]["db"])
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            pf_docker.validate_isolated_model(model, expectation, IMAGES)
        self.assertIn("isolated-project", [finding.code for finding in caught.exception.findings])

    def container(self, service, **changes):
        item = {"id": (service * 64)[:64], "labels": {pf_docker.COMPOSE_SERVICE_LABEL: service},
                "image": IMAGES[service], "port_bindings": {}, "ports": {"5173/tcp": None} if service == "frontend"
                else {}, "restart": "no", "networks": {"pfverify-0123456789ab_default": {}}, "running": True,
                "mounts": [{"Type": "volume", "Name": "pfverify-0123456789ab_postgres_data",
                            "Destination": "/var/lib/postgresql/data"}] if service == "db" else []}
        item.update(changes)
        return item

    def findings(self, containers=None, **network):
        base = {"name": "pfverify-0123456789ab_default", "internal": True, "driver": "bridge",
                "labels": {pf_docker.INSTANCE_LABEL: "uuid-1"}}
        base.update(network)
        return pf_docker.isolation_findings(containers or [self.container(name) for name in pf_docker.SERVICES], base,
                                            project="pfverify-0123456789ab", topology_uuid="uuid-1")

    def test_it9_loopback_alone_is_not_isolation(self):
        self.assertEqual(self.findings(), {})
        for host in ("127.0.0.1", "::1", "0.0.0.0"):
            with self.subTest(host=host):
                containers = [self.container("db"), self.container("backend"), self.container(
                    "frontend", ports={"5173/tcp": [{"HostIp": host, "HostPort": "15173"}]})]
                self.assertIn("isolation:listener", self.findings(containers))
        containers = [self.container("db"), self.container("backend"), self.container(
            "frontend", port_bindings={"5173/tcp": [{"HostIp": "127.0.0.1", "HostPort": "1"}]})]
        self.assertIn("isolation:listener", self.findings(containers))
        self.assertIn("isolation:network", self.findings(internal=False))
        restart = [self.container("db", restart="always"), self.container("backend"), self.container("frontend")]
        self.assertIn("isolation:restart", self.findings(restart))
        mounts = [self.container("db"), self.container("backend", mounts=[{"Type": "bind", "Source": "/srv"}]),
                  self.container("frontend")]
        self.assertIn("isolation:mounts", self.findings(mounts))


class ReconcileReport(unittest.TestCase):
    """RQ-1..RQ-7: the strict report parser and the oracle matrix (pure)."""

    @staticmethod
    def report(result="clean", code=None, counts=None, **extra):
        code = {"clean": 0, "mismatch": 1, "error": 2}[result] if code is None else code
        document = {"report_version": 1, "command": "reconcile", "exit_code": code, "result": result,
                    "checks": [{"id": check, "status": "mismatch" if (counts or {}).get(check) else "ok",
                                "finding_count": (counts or {}).get(check, 0), "duration_ms": 5}
                               for check in "abcdefghij"],
                    "findings": [{"part_number": "PN-1", "entity_id": "E-1"}]}
        document.update(extra)
        return json.dumps(document)

    def test_rq1_rq2_outcomes_and_exit_code_consistency(self):
        for result, code in (("clean", 0), ("mismatch", 1), ("error", 2)):
            parsed = pf_config.parse_reconcile_report(self.report(result, counts={"a": 2} if code == 1 else None),
                                                      code)
            self.assertEqual(parsed.outcome, result)
        self.assertEqual(pf_config.parse_reconcile_report(self.report("clean"), 1).outcome, "incomplete")
        self.assertEqual(pf_config.parse_reconcile_report(self.report("clean", code=1), 1).outcome, "incomplete")

    def test_rq3_rq4_anything_else_is_incomplete(self):
        good = self.report()
        for label, text, kwargs in (
                ("two documents", good + good, {}), ("trailing text", good + "\nmore", {}), ("empty", "", {}),
                ("non-JSON", "oops", {}), ("duplicate key", good[:-1] + ',"result":"clean"}', {}),
                ("NaN", good.replace('"duration_ms": 5', '"duration_ms": NaN', 1), {}),
                ("truncated", good, {"truncated": True}),
                ("unknown check", self.report(checks=[{"id": "z", "status": "ok", "finding_count": 0}]), {}),
                ("duplicate check", self.report(checks=[{"id": "a", "status": "ok", "finding_count": 0}] * 2), {}),
                ("version", self.report(report_version=2), {})):
            with self.subTest(label):
                self.assertEqual(pf_config.parse_reconcile_report(text, 0, **kwargs).outcome, "incomplete")

    def test_rq5_rq6_summary_holds_counts_only(self):
        one = pf_config.parse_reconcile_report(self.report("mismatch", counts={"b": 3}), 1)
        other = pf_config.parse_reconcile_report(self.report("mismatch", counts={"b": 3}, findings=[{"x": "PN-9"}]), 1)
        self.assertEqual(one.summary, other.summary)
        self.assertNotIn("PN-", one.summary)
        self.assertLessEqual(len(one.summary), 500)
        self.assertTrue(one.summary.startswith("result=mismatch;a:ok:0,b:mismatch:3"))

    def test_rq7_oracle_matrix(self):
        R = pf_config.ReconcileResult
        clean, mismatch = R("clean", 0, "result=clean;a:ok:0"), R("mismatch", 1, "result=mismatch;a:mismatch:1")
        other = R("mismatch", 1, "result=mismatch;a:mismatch:2")
        unavailable, error, incomplete = R("unavailable", 3, ""), R("error", 2, "x"), R("incomplete", None, "")
        check = pf_config.app_invariants_check
        self.assertEqual(check(clean, clean, mode="purge")["result"], "passed")
        self.assertEqual(check(mismatch, mismatch, mode="purge")["result"], "passed")
        self.assertEqual(check(mismatch, other, mode="purge")["result"], "failed")
        self.assertEqual(check(error, error, mode="purge")["result"], "failed")
        self.assertEqual(check(clean, incomplete, mode="purge")["result"], "failed")
        self.assertEqual(check(unavailable, unavailable, mode="purge")["result"], "not_run")
        self.assertTrue(check(unavailable, unavailable, mode="purge")["detail"].startswith("unavailable:"))
        self.assertEqual(check(unavailable, clean, mode="purge")["result"], "failed")  # capability disagreement
        self.assertEqual(check(None, clean, mode="side-by-side")["result"], "passed")
        self.assertEqual(check(None, mismatch, mode="side-by-side")["result"], "failed")
        self.assertEqual(check(None, mismatch, mode="side-by-side", recorded_summary=mismatch.summary)["result"],
                         "passed")
        self.assertEqual(check(None, error, mode="side-by-side")["result"], "failed")
        self.assertEqual(check(None, unavailable, mode="side-by-side")["result"], "not_run")


def oci_archive(path, images, *, layout="oci", tamper=None, extra=()):
    """A synthetic docker save tar of ``images`` ({id: name}), written with real config and layer hashes."""
    blobs, entries = {}, []
    for image_id, name in images.items():
        config, layer = pfx.FAKE_DOCKER.fake_config(name)
        assert "sha256:" + hashlib.sha256(config).hexdigest() == image_id
        c_hex, l_hex = hashlib.sha256(config).hexdigest(), hashlib.sha256(layer).hexdigest()
        c_name = c_hex + ".json" if layout == "legacy" else "blobs/sha256/" + c_hex
        l_name = l_hex + "/layer.tar" if layout == "legacy" else "blobs/sha256/" + l_hex
        blobs[c_name], blobs[l_name] = config, layer
        entries.append({"Config": c_name, "RepoTags": [name + ":x"], "Layers": [l_name]})
    if tamper:
        tamper(blobs, entries)
    with tarfile.open(path, "w") as archive:
        for name, data in list(blobs.items()) + [("manifest.json", json.dumps(entries).encode())] + list(extra):
            if isinstance(data, tarfile.TarInfo):
                archive.addfile(data)
                continue
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


class ImageArchive(unittest.TestCase):
    """IA-1..IA-6: the image archive proof (one sequential pass, real hashes)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.images = {pfx.image_id(name): name for name in ("ia-db", "ia-backend", "ia-frontend")}

    def tearDown(self):
        self.temp.cleanup()

    def prove(self, path, required=None):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            return pf_source.image_archive_proof(fd, required if required is not None else list(self.images))
        finally:
            os.close(fd)

    def test_ia1_ia2_ia6_oci_and_legacy_layouts(self):
        for layout in ("oci", "legacy"):
            with self.subTest(layout):
                proof = self.prove(oci_archive(self.dir / (layout + ".tar"), self.images, layout=layout))
                self.assertEqual(sorted(proof.image_ids), sorted(self.images))
                self.assertEqual(proof.layers_checked, 3)
                self.assertEqual(sorted(proof.repo_tags), sorted((name + ":x", image_id)
                                                                 for image_id, name in self.images.items()))

    def test_ia3_named_refusals(self):
        def layer_swap(blobs, entries):
            name = entries[0]["Layers"][0]
            blobs[name] = b"other layer"

        def config_swap(blobs, entries):
            blobs[entries[0]["Config"]] = b'{"rootfs":{"diff_ids":[]}}'

        def extra_layer(blobs, entries):
            entries[0]["Layers"].append(entries[1]["Layers"][0])

        cases = {"missing": ({}, "image-archive-missing", [pfx.image_id("ia-other")]),
                 "layer": ({"tamper": layer_swap}, "image-archive-layer-mismatch", None),
                 "config": ({"tamper": config_swap}, "image-archive-config-mismatch", None),
                 "layer count": ({"tamper": extra_layer}, "image-archive-layer-mismatch", None)}
        for label, (kwargs, code, required) in cases.items():
            with self.subTest(label):
                path = oci_archive(self.dir / (label + ".tar"), self.images, **kwargs)
                with self.assertRaises(pf_source.ArchiveRefused) as caught:
                    self.prove(path, required)
                self.assertEqual(caught.exception.code, code)
        path = self.dir / "nomanifest.tar"
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("x")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(pf_source.ArchiveRefused) as caught:
            self.prove(path)
        self.assertEqual(caught.exception.code, "image-archive-manifest-invalid")
        limits = pf_source.ImageArchiveLimits(100, 10, 4 * 1024 * 1024, 1024 * 1024)
        fd = os.open(str(oci_archive(self.dir / "big.tar", self.images)), os.O_RDONLY)
        try:
            with self.assertRaises(pf_source.ArchiveRefused) as caught:
                pf_source.image_archive_proof(fd, list(self.images), limits=limits)
        finally:
            os.close(fd)
        self.assertEqual(caught.exception.code, "archive-limit")

    def test_ia4_ia5_unsafe_members_and_the_member_bound(self):
        for label, kind in (("symlink", tarfile.SYMTYPE), ("hardlink", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE)):
            with self.subTest(label):
                info = tarfile.TarInfo("evil")
                info.type = kind
                info.linkname = "/etc/passwd"
                path = oci_archive(self.dir / (label + ".tar"), self.images, extra=[("evil", info)])
                with self.assertRaises(pf_source.ArchiveRefused) as caught:
                    self.prove(path)
                self.assertEqual(caught.exception.code, "archive-member-refused")
        for name in ("/abs", "../up"):
            with self.subTest(name):
                path = oci_archive(self.dir / "name.tar", self.images, extra=[(name, b"x")])
                with self.assertRaises(pf_source.ArchiveRefused) as caught:
                    self.prove(path)
                self.assertEqual(caught.exception.code, "archive-member-refused")
        path = oci_archive(self.dir / "count.tar", self.images, extra=[(f"pad-{n}", b"") for n in range(20)])
        fd = os.open(str(path), os.O_RDONLY)
        try:
            with self.assertRaises(pf_source.ArchiveRefused) as caught:
                pf_source.image_archive_proof(fd, list(self.images),
                                              limits=pf_source.ImageArchiveLimits(5, 1 << 20, 1 << 22, 1 << 30))
        finally:
            os.close(fd)
        self.assertEqual((caught.exception.code, caught.exception.reason), ("archive-limit", "member-count"))


class CapacityPure(unittest.TestCase):
    """CP-1: same-device needs summed across roles, the floor once per device, distinct devices separate."""

    def test_cp1(self):
        needs = [("p", "backups", "/b", 1, 60), ("p", "workspace", "/w", 1, 60), ("p", "docker-root", "/d", 2, 60),
                 ("q", "backups", "/b", 1, 500)]
        self.assertEqual(pf_config.capacity_shortfalls(needs, {1: 200, 2: 200}, 10, phase="p"), [])
        short = pf_config.capacity_shortfalls(needs, {1: 125, 2: 200}, 10, phase="p")
        self.assertEqual([(item.device, item.roles, item.need, item.floor) for item in short],
                         [(1, ("backups", "workspace"), 120, 10)])
        self.assertEqual([item.device for item in pf_config.capacity_shortfalls(needs, {1: 200, 2: 200}, 10)], [1])
        self.assertEqual(pf_config.restored_estimate(dump_bytes=10), 10 + 256 * 1024 * 1024)
        self.assertEqual(pf_config.restored_estimate(dump_bytes=1 << 30), 4 << 30)
        self.assertEqual(pf_config.restored_estimate(live_bytes=7), 14)


class SchemaA33(unittest.TestCase):
    """SA3-1..SA3-4: the PF-A3.3 amendments in the corpus, the contract file and the record markers."""

    def test_sa3_1_new_examples_and_invalid_corpus(self):
        cases = {row["file"]: row for row in json.loads((EXAMPLES / "cases.json").read_text(encoding="utf-8"))}
        new = ["operation-plan-restore-side-by-side.json", "operation-plan-cleanup.json",
               "operation-journal-cleanup-deleting.json", "operation-plan-abort-deploy-preserving.json",
               "verification-record-functional.json", "verification-record-functional-unavailable-app-check.json",
               "verification-record-functional-legacy.json", "runner-acknowledgement.json", "generation-seal.json",
               "invalid-verification-functional-missing-isolation.json",
               "invalid-verification-functional-app-check-not-run.json",
               "invalid-verification-functional-health-not-run.json", "invalid-plan-side-by-side-without-bundle.json",
               "invalid-plan-cleanup-with-bundle.json", "invalid-plan-cleanup-effect-type.json",
               "invalid-journal-cleanup-deletion.json", "invalid-plan-abort-deploy-capture-without-stop.json",
               "invalid-runner-acknowledgement-control-char.json", "invalid-generation-seal-handles.json"]
        for name in new:
            with self.subTest(name):
                row = cases[name]
                value = pf_instance.parse_strict_json((EXAMPLES / name).read_bytes(), label=name)
                plan = json.loads((EXAMPLES / row["plan"]).read_bytes()) if "plan" in row else None
                problems = pf.lifecycle_errors(value, row["record"], plan=plan)
                self.assertEqual(not problems, row["expect"]["valid"], problems)
                if not row["expect"]["valid"]:
                    self.assertTrue(any(row["expect"]["problem"] in item for item in problems), problems)

    def test_sa3_2_contract_file_equals_the_embedded_schema(self):
        self.assertEqual(json.loads((CONTRACTS / "lifecycle-records.schema.json").read_text(encoding="utf-8")),
                         pf_config.LIFECYCLE_SCHEMA)
        for name in ("runner_acknowledgement", "generation_seal"):
            self.assertIn(name, pf_config.LIFECYCLE_RECORDS)
            self.assertIn(name, pf_config.LIFECYCLE_SCHEMA["$defs"])
        for kind in ("restore-side-by-side", "cleanup"):
            self.assertEqual(pf_config.KIND_FAIL_CLOSED[kind], "never")
            self.assertIn(kind, pf_config.WORKSPACE_UNTOUCHED_KINDS)
        self.assertEqual(pf_config.PHASE_ORDER["abort-deploy"], ("planned", "preserving", "deleting", "finalizing"))

    def test_sa3_4_markers_of_the_new_records_resolve(self):
        defs = pf_config.LIFECYCLE_SCHEMA["$defs"]
        for name in ("runner_acknowledgement", "generation_seal"):
            for item in json.dumps(defs[name]).split('"description": "')[1:]:
                marker = item.split('"', 1)[0]
                for prefix in ("null or ", "items ", "map "):
                    if marker.startswith(prefix):
                        target = marker[len(prefix):]
                        self.assertTrue(target in ("string", "sha256", "path", "scalar")
                                        or target[len("$defs."):] in defs, target)

    def test_fv5_fv6_functional_record_rules_and_writer_levels(self):
        record = json.loads((EXAMPLES / "verification-record-functional.json").read_bytes())
        self.assertEqual(pf.lifecycle_errors(record, "verification_record"), [])
        for label, mutate, problem in (
                ("no topology", lambda r: r.update(checks=[c for c in r["checks"]
                                                           if not c["name"].startswith("topology:")]),
                 "exactly one topology:<project> check"),
                ("two topologies", lambda r: r["checks"].append({"name": "topology:pfverify-ffffffffffff",
                                                                 "result": "passed", "detail": ""}),
                 "exactly one topology:<project> check"),
                ("isolation not_run", lambda r: [c.update(result="not_run", detail="x") for c in r["checks"]
                                                 if c["name"] == "isolation:network"], "must run"),
                ("config not_run", lambda r: [c.update(result="not_run", detail="skipped") for c in r["checks"]
                                              if c["name"] == "config:bundle"], "unavailable: or excluded:")):
            with self.subTest(label):
                value = copy.deepcopy(record)
                mutate(value)
                problems = pf.lifecycle_errors(value, "verification_record")
                self.assertTrue(any(problem in item for item in problems), problems)
        view = types.SimpleNamespace(bundle_id="x", bundle_kind="checkpoint", manifest_sha256="0" * 64)
        controller = pf.Controller.__new__(pf.Controller)
        with self.assertRaisesRegex(pf.Failure, "never written with level captured"):
            pf.Controller.write_verification(controller, view, level="captured", result="passed", target={},
                                             checks=[], started_at="20261007T000000Z")


# ============================================================================ integration harness


@ROOT_FS
class Plane(unittest.TestCase):
    """A registered instance on the fake daemon's application plane, every docker child answered in-process."""

    project = PROJECT

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.layout = pfx.install_root(self.base)
        self.fake = pfx.install_fake_docker(self.layout)
        self.context, self.paths = self.instance("staging", self.project)
        pf.write_json(self.context.state_dir / "deployed.json", {"sha": pfx.OLD})
        topology = pfx.owned_topology(self.context)
        for image in topology["images"]:
            if "-backend:" in image["repo_tags"][0]:
                image["contract"] = {"files": {"alembic.ini": "0" * 64}, "heads": ["r1"]}
        topology["images"].append(pfx.image(pfx.DB_IMAGE_ID, [pf_docker.DB_IMAGE], {}))
        for container in topology["containers"]:
            if container["labels"][pf_docker.COMPOSE_SERVICE_LABEL] == "db":
                container["image"] = pfx.DB_IMAGE_ID
        service_label = pf_docker.COMPOSE_SERVICE_LABEL
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
        state = pfx.default_docker_state()
        for key in ("containers", "volumes", "networks", "images"):
            state[key] += copy.deepcopy(topology[key])
        state["plane"] = self.plane
        self.fake.write_state(state)
        controller = self.controller()
        controller.make_override({"backend": topology["images"][0]["repo_tags"][0],
                                  "frontend": topology["images"][1]["repo_tags"][0]},
                                 self.context.state_dir / "active-images.yaml")
        self.fake.clear_calls()
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(self.patch_runner())
        self.stack.enter_context(self.patch_source())

    def tearDown(self):
        self.stack.close()
        for current, dirs, files in os.walk(self.base):
            for name in dirs + files:
                path = Path(current) / name
                if not path.is_symlink():
                    try:
                        os.chmod(path, 0o700 if path.is_dir() else 0o600)
                    except OSError:
                        pass
        self.temp.cleanup()

    def instance(self, slug, project):
        paths = pfx.data_home(self.base / slug, project=project, group=GROUP)
        config = paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1)) + "\n")
        context = pfx.register(self.layout, slug, paths, project=project)
        return context, paths

    def patch_runner(self):
        fake = self.fake

        def runner(controller):
            if controller._runner is None:
                controller._runner = pfx.InProcessRunner(controller, fake)
            return controller._runner

        return mock.patch.object(pf.Controller, "runner", property(runner))

    def patch_source(self):
        """The provenance of the deployed source is A3.1's concern: the protected store is simulated (as in
        test_docker_scope.PurgeHarness)."""
        harness = self

        def create_deployed_source_archive(controller, destination, revision):
            if revision is None:  # as the protected store: no proven deployed commit, no deployed-source archive
                raise pf.Failure("no deployed revision is recorded")
            tree = harness.base / "deployed-source"
            if not tree.exists():
                pfx.source_fixture(tree, revision or pfx.OLD)
            manifest = controller.candidate_manifest(tree, revision or pfx.OLD, verified=True)
            _, expanded, members_sha256 = pf_source.archive_verified_tree(tree, manifest, destination)
            return {"origin": "protected-store", "manifest": manifest, "expanded_bytes": expanded,
                    "members": len(manifest["entries"]), "members_sha256": members_sha256}

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(pf.Controller, "create_deployed_source_archive",
                                              create_deployed_source_archive))
        stack.enter_context(mock.patch.object(pf.Controller, "deployed_source_origin",
                                              lambda controller, revision: "protected-store"))
        return stack

    def controller(self, context=None):
        context = context or self.reload()
        validation = pf_instance.validate_context(context, running_release=self.layout.release_dir)
        self.assertTrue(validation.mutation_allowed, validation.blocking_messages())
        return pf.Controller(context, validation=validation, running_release=self.layout.release_dir)

    @staticmethod
    def crashing(point):
        """Every controller built inside the block carries the in-process crash seam ``point`` (section 6)."""
        original = pf.Controller.__init__

        def init(controller, *args, **kwargs):
            original(controller, *args, **kwargs)
            controller._crash_point = point

        return mock.patch.object(pf.Controller, "__init__", init)

    @staticmethod
    def crash_before(effect_id, when="before-intent"):
        def seam(current, point):
            if point == when and current == effect_id:
                raise pf.SimulatedCrash(f"{when} {effect_id}")
        return seam

    def reload(self, slug="staging"):
        return pf_instance.resolve_instance(pf_instance.load_registry(self.layout.root), instance=slug)

    def state(self):
        return self.fake.state()

    def update_state(self, **changes):
        state = self.fake.state()
        state.update(changes)
        self.fake.write_state(state)
        return state

    def main(self, *arguments, confirm=None, answers=None, slug="staging"):
        """In-process CLI with an operator terminal; ``confirm(phrase)`` sees every typed confirmation."""
        phrases = []

        def record(phrase, warning):
            phrases.append(phrase)
            if confirm is not None:
                confirm(phrase)

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(pf, "confirm", side_effect=record), \
                mock.patch.object(pf, "prompt_yes_no", return_value=False), \
                mock.patch.object(pf, "unattended", return_value=False), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = pf.main(["--instance", slug, *arguments], installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        out, err = stdout.getvalue(), stderr.getvalue()
        self.last_out = out.splitlines()
        self.assertEqual(downgrade_calls(self.fake.argvs()), [])
        for gate in ("terminal-required", "instance-required-unattended", "policy-grant-required"):
            self.assertNotIn(gate, err)
        return code, out, err, phrases

    def operation(self, kind):
        found = pfx.operations_of(self.context, kind)
        return found[-1] if found else (None, None, None)

    def records(self, bundle_id):
        directory = self.context.artifacts_dir / "verifications" / bundle_id
        return [json.loads(path.read_bytes()) for path in sorted(directory.iterdir())] if directory.exists() else []

    def isolated_resources(self, prefix):
        state = self.state()
        return {key: [item for item in state.get(key, []) if json.dumps(item).find(prefix) >= 0]
                for key in ("containers", "volumes", "networks")}

    def purge(self, *extra, confirm=None):
        return self.main("purge", "--keep-backups", *extra, confirm=confirm)

    def purged_bundle(self):
        op, plan, journal = self.operation("purge")
        return next(item for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")


# ============================================================================ PZ: the instance purge


class PurgeIntegrated(Plane):
    """PZ-1..PZ-15 and the OD-A33-08 registry tombstone (TB-1..TB-4)."""

    def test_pz1_fv1_full_instance_purge_with_functional_verification_and_tombstone(self):
        record_before = self.context.record_path.read_bytes()
        code, out, err, phrases = self.purge()
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("purge")
        self.assertEqual(journal["phase"], "completed")
        bundle = self.purged_bundle()
        records = [item for item in self.records(bundle["name"]) if item["level"] == "functional_recovery_verified"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record["result"], record["operation_id"], record["manifest_sha256"]),
                         ("passed", op, bundle["sha256"]))
        names = {check["name"] for check in record["checks"]}
        for entry in pf_config.FUNCTIONAL_CHECKS:
            self.assertTrue(any(name.startswith(entry) for name in names) if entry.endswith(":") else entry in names,
                            entry)
        self.assertTrue(record["target"]["removed"])
        self.assertEqual(record["environment"]["server_version_num"], 160099)  # the isolated server's
        app = next(check for check in record["checks"] if check["name"] == "app-invariants")
        self.assertEqual(app["result"], "passed")
        # The purge bundle itself was never restored into a pf_verify_* candidate of the live server.
        live_restores = [argv for argv in self.fake.argvs() if "pg_restore" in argv and "-p" in argv
                         and argv[argv.index("-p") + 1] == self.project and "--list" not in argv]
        bundle_records = self.records(bundle["name"])
        self.assertFalse([item for item in bundle_records if item["level"] == "data_restore_verified"])
        self.assertEqual(len(live_restores), 1)  # only the checkpoint:before-purge verification
        # Writers stayed stopped from the stop to the end (no up/start of the instance's backend/frontend).
        calls = self.fake.argvs()
        stop = next(index for index, argv in enumerate(calls) if argv[-3:] == ["stop", "frontend", "backend"])
        for argv in calls[stop:]:
            if argv[:1] == ["compose"] and "-p" in argv and argv[argv.index("-p") + 1] == self.project:
                self.assertFalse({"up", "start", "restart"} & set(argv) and ({"backend", "frontend"} & set(argv)),
                                 argv)
        # The topology is gone, its files too; the tombstone was written by a registry transaction.
        self.assertEqual(self.isolated_resources("pfverify-"), {"containers": [], "volumes": [], "networks": []})
        verification = next(e for e in plan["effects"] if e["target"] == "bundle:purge")
        project = verification["preconditions"][0].split(":", 1)[1]
        directory = self.context.operations_dir / op / "isolated" / project
        self.assertFalse((directory / "compose.json").exists())
        self.assertFalse((directory / "app.env").exists())
        record_after = json.loads(self.context.record_path.read_bytes())
        self.assertEqual(record_after["state"], "purged")
        self.assertEqual(record_after["record_revision"], json.loads(record_before)["record_revision"] + 1)
        self.assertIn(f"Final bundle {bundle['name']}: functional recovery verified in isolated topology {project}",
                      out)
        code, out, err, _ = self.main("status")
        self.assertIn(f"Lifecycle: purged by instance purge {op}", out)
        self.assertIn(f"recovery bundle {bundle['name']} [functional]", out)
        listing = io.StringIO()
        with contextlib.redirect_stdout(listing):
            pf.display_registry(pf_instance.load_registry(self.layout.root))
        self.assertIn("state=purged", listing.getvalue())
        self.assertIn("lifecycle=purged", listing.getvalue())
        evidence("FUNCTIONAL-1", {"passed": record})

    def erase_hook(self, action):
        """A confirmation that runs ``action()`` when the ERASE challenge is asked (right before the gate)."""
        def confirm(phrase):
            if phrase.startswith("ERASE "):
                action()
        return confirm

    def plant(self, **changes):
        bundle_dir = self.context.artifacts_dir / "verifications"
        for folder in bundle_dir.iterdir():
            for path in folder.iterdir():
                record = json.loads(path.read_bytes())
                if record["level"] != "functional_recovery_verified":
                    continue
                path.unlink()
                record.update(changes)
                record["verification_id"] = "ver-20261007T235959Z-0badf00d"
                target = bundle_dir / record["bundle_id"]
                target.mkdir(exist_ok=True)
                (target / (record["verification_id"] + ".json")).write_bytes(pf_instance.normalize_json(record))

    def assert_refused(self, code, out, err, wanted):
        self.assertEqual(code, 1, out + err)
        self.assertIn(wanted, err)
        op, plan, journal = self.operation("purge")
        self.assertEqual(journal["phase"], "cancelled", out + err)
        self.assertIsNone(journal["deletion"])
        running = sorted(item["labels"][pf_docker.COMPOSE_SERVICE_LABEL] for item in self.state()["containers"]
                         if item["status"] == "running"
                         and item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project)
        self.assertEqual(running, ["backend", "db", "frontend"])  # reopened
        self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "registered")
        self.assertTrue(self.state()["volumes"])

    def test_pz2_the_gate_binds_the_exact_record(self):
        rows = []
        variants = {
            "another bundle": {"bundle_id": "purge-20261001T000000Z-000000000000-abcdef"},
            "another manifest hash": {"manifest_sha256": "0" * 64},
            "another operation": {"operation_id": "20261001T000000Z-purge-0badf00d"},
            "data_restore only": {"level": "data_restore_verified"},
            "another record id": {},
        }
        for label, changes in variants.items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                code, out, err, _ = self.purge(confirm=self.erase_hook(lambda: self.plant(**changes)))
                self.assert_refused(code, out, err, "purge-bundle-unverified:")
                rows.append({"case": label, "code": "purge-bundle-unverified"})
        evidence("PURGE-GATE-1", {"rows": rows})

    def test_pz3_pz4_writers_and_source_changes_block_the_deletion(self):
        def start_backend():
            state = self.state()
            for item in state["containers"]:
                if item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "backend":
                    item["status"] = "running"
            self.fake.write_state(state)

        code, out, err, _ = self.purge(confirm=self.erase_hook(start_backend))
        self.assert_refused(code, out, err, "purge-writers-running: backend is running")

        for label, mutate in (("new database", lambda d: d.update(newdb={"heads": [], "rows": {}, "allow": True,
                                                                        "owner": "partflow_staging",
                                                                        "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]})),
                              ("row count", lambda d: d["partflow_staging"]["rows"].update({"public.part": 4})),
                              ("heads", lambda d: d["partflow_staging"].update(heads=["r9"]))):
            with self.subTest(label):
                self.tearDown()
                self.setUp()

                def change():
                    state = self.state()
                    mutate(state["plane"]["databases"])
                    self.fake.write_state(state)

                code, out, err, _ = self.purge(confirm=self.erase_hook(change))
                if label != "heads":
                    self.assert_refused(code, out, err, "purge-source-changed:")
                    continue
                # Changed heads: refused before any deletion; the reopen itself fails closed (the live heads no
                # longer match the deployed image), so the operation stays open for resume/abandon.
                self.assertEqual(code, 1, out + err)
                self.assertIn("purge-source-changed:", err)
                self.assertIn("Could not automatically resume after pre-delete purge failure", out + err)
                journal = self.operation("purge")[2]
                self.assertIsNone(journal["deletion"])
                self.assertNotIn(journal["phase"], ("deleting", "finalizing", "completed"))
                self.assertTrue(self.state()["volumes"])
                self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "registered")

    def test_pz7_crash_in_verifying_resumes_by_teardown_and_reopen(self):
        for when in ("after-intent", "inside"):
            with self.subTest(when):
                self.tearDown()
                self.setUp()
                point = ("bundle:purge", "after-intent") if when == "after-intent" else ("runtime", "inside")
                with self.crashing(point):
                    with self.assertRaises(pf.SimulatedCrash):
                        self.purge()
                op, plan, journal = self.operation("purge")
                self.assertEqual(journal["phase"], "verifying")
                code, out, err, _ = self.main("resume")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.operation("purge")[2]["phase"], "cancelled")
                self.assertEqual(self.isolated_resources("pfverify-"), {"containers": [], "volumes": [],
                                                                         "networks": []})
                self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "registered")

    def test_pz8_pz14_a_failed_verification_keeps_the_stack_and_blocks_the_next_purge(self):
        state = self.state()
        state.setdefault("isolated_faults", {})["host_port"] = True
        self.fake.write_state(state)
        code, out, err, _ = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn("functional-verification-failed:", err)
        self.assertIn("isolation:listener", err)
        op, plan, journal = self.operation("purge")
        self.assertEqual(journal["phase"], "cancelled")
        self.assertIn("isolated-topology", [item["kind"] for item in journal["retained_artifacts"]])
        kept = self.isolated_resources("pfverify-")
        self.assertTrue(kept["containers"])
        self.assertFalse([item for item in kept["containers"] if item["status"] == "running"])
        failed = [item for item in self.records(self.purged_bundle()["name"])
                  if item["level"] == "functional_recovery_verified"]
        self.assertEqual([item["result"] for item in failed], ["failed"])
        evidence("FUNCTIONAL-1-failed", {"failed": failed[0]})
        # PZ-14: the next purge refuses before PURGE; the cleanup removes the stack and the purge proceeds.
        state = self.state()
        state["isolated_faults"] = {}
        self.fake.write_state(state)
        code, out, err, phrases = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn("isolated-topology-present:", err)
        self.assertNotIn("PURGE " + self.project, phrases)
        code, out, err, phrases = self.main("cleanup")
        self.assertEqual(code, 0, out + err)
        self.assertIn("isolated-topology pfverify-", out)
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.isolated_resources("pfverify-"), {"containers": [], "volumes": [], "networks": []})
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)

    def test_pz9_capacity_refuses_before_the_purge_confirmation(self):
        config = self.paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1 << 30)) + "\n")
        before = pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"])
        code, out, err, phrases = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn("capacity-insufficient:", err)
        self.assertIn("Nothing was changed.", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"]), before)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] in (["rm"], ["volume"]) and "rm" in argv])

    def test_pz11_pz12_unavailable_and_failing_application_checks(self):
        self.update_state(reconcile={"*": {"probe": 3}})
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        record = [item for item in self.records(self.purged_bundle()["name"])
                  if item["level"] == "functional_recovery_verified"][0]
        app = next(check for check in record["checks"] if check["name"] == "app-invariants")
        self.assertEqual(app["result"], "not_run")
        self.assertTrue(app["detail"].startswith("unavailable:"))
        self.assertFalse([argv for argv in self.fake.argvs() if "app.cli" in argv])
        for report in ("error", "garbage"):
            with self.subTest(report):
                self.tearDown()
                self.setUp()
                self.update_state(reconcile={self.project: {"report": report}})
                code, out, err, _ = self.purge()
                self.assertEqual(code, 1, out + err)
                self.assertIn("app-check-failed:", err)
                self.assertEqual(self.operation("purge")[2]["phase"], "cancelled")
                for path in (self.context.operations_dir).rglob("*"):
                    if path.is_file():
                        self.assertNotIn(b"PN-SECRET-4711", path.read_bytes(), path)
                self.assertNotIn("PN-SECRET-4711", out + err)

    def test_pz15_a_running_recovery_target_blocks_the_purge(self):
        bundle = self.backup_bundle()
        code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        code, out, err, phrases = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn("isolated-topology-present: pfrecover-", err)
        self.assertIn("--recovery-target pfrecover-", err)

    def backup_bundle(self):
        """A purge bundle of this instance on the plane (an instance purge) and the instance reinstated by restore."""
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        bundle = self.purged_bundle()["name"]
        code, out, err, _ = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        return bundle

    def test_tb1_restore_into_the_purged_record_claims_the_project_again(self):
        bundle = self.backup_bundle()
        record = json.loads(self.context.record_path.read_bytes())
        self.assertEqual(record["state"], "registered")
        op, plan, journal = self.operation("restore-instance")
        self.assertEqual(plan["input_bundle"]["bundle_id"], bundle)
        self.assertIn("registry:state=registered", [effect["target"] for effect in plan["effects"]])
        self.assertEqual(journal["phase"], "completed")
        self.assertIn("Application invariants: clean", "\n".join(self.last_out))

    def test_tb2_a_second_instance_claims_the_released_project(self):
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        bundle = self.purged_bundle()["name"]
        other, _ = self.instance("second", self.project)  # registration accepts the released project
        self.assertEqual(other.compose_project, self.project)
        code, out, err, phrases = self.main("restore-instance", bundle)
        self.assertEqual(code, 1, out + err)
        self.assertIn("instance-claim-taken:", err)
        self.assertIn("second", err)
        self.assertEqual(phrases, [])
        self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "purged")

    def test_tb3_crashes_around_the_registry_write_resume_to_one_tombstone(self):
        for point in ("before-intent", "after-effect"):
            with self.subTest(point):
                self.tearDown()
                self.setUp()
                with self.crashing(("registry:state=purged", point)):
                    with self.assertRaises(pf.SimulatedCrash):
                        self.purge()
                op, plan, journal = self.operation("purge")
                self.assertEqual(journal["phase"], "finalizing")
                state = json.loads(self.context.record_path.read_bytes())["state"]
                self.assertEqual(state, "purged" if point == "after-effect" else "registered")
                code, out, err, _ = self.main("resume")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.operation("purge")[2]["phase"], "completed")
                record = json.loads(self.context.record_path.read_bytes())
                self.assertEqual(record["state"], "purged")
                self.assertEqual(record["record_revision"], 2)

    def test_tb4_any_other_record_change_still_refuses_the_resume(self):
        with self.crashing(("purge-cleanup:admin-config", "after-effect")):
            with self.assertRaises(pf.SimulatedCrash):
                self.purge()
        record = json.loads(self.context.record_path.read_bytes())
        record["approved_config_revision"] += 1
        os.chmod(self.context.record_path, 0o600)
        self.context.record_path.write_bytes(pf_instance.normalize_json(record))
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 1, out + err)
        self.assertIn("plan-authority-changed: instance record changed", err)


class IsolatedTopology(Plane):
    """IT-3..IT-8, PZ-7 (teardown blocker), PZ-10 and RQ-8 through the instance purge: the real topology code on the
    fake daemon's isolated plane."""

    FIXED = "0123456789ab"

    def fixed_project(self):
        original = pf.secrets.token_hex
        return mock.patch.object(pf.secrets, "token_hex", lambda n=None: self.FIXED if n == 6 else original(n))

    def purged_bundle(self):
        op, plan, journal = self.operation("purge")
        return next(item for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")

    def test_it3_a_render_back_mismatch_is_unsupported_before_any_up(self):
        self.update_state(isolated_faults={"config_mutation": True})
        code, out, err, phrases = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.assertIn("verification-isolation-unsupported:", err)
        self.assertEqual(self.operation("purge")[2]["phase"], "cancelled")
        for argv in self.fake.argvs():
            if argv[:1] == ["compose"] and "-p" in argv and argv[argv.index("-p") + 1].startswith("pfverify-"):
                self.assertNotIn("up", argv)

    def test_it4_a_generated_name_in_use_is_refused_before_the_confirmation(self):
        project = "pfverify-" + self.FIXED
        state = self.state()
        state["volumes"].append(pfx.volume(project + "_postgres_data",
                                           {pf_docker.COMPOSE_PROJECT_LABEL: project,
                                            pf_docker.INSTANCE_LABEL: "00000000-0000-4000-8000-000000000001"}))
        self.fake.write_state(state)
        with self.fixed_project():
            code, out, err, phrases = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"topology-name-collision: the generated project {project} is already used", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.operations_of(self.context, "purge"), [])

    def test_it6_isolation_observation_failures_stop_and_keep_the_topology(self):
        for fault, check in (("internal_false", "isolation:network"), ("restart_always", "isolation:restart"),
                             ("extra_mount", "isolation:mounts")):
            with self.subTest(fault):
                self.tearDown()
                self.setUp()
                self.update_state(isolated_faults={fault: True})
                code, out, err, _ = self.main("purge", "--keep-backups")
                self.assertEqual(code, 1, out + err)
                self.assertIn("functional-verification-failed:", err)
                self.assertIn(check, err)
                kept = self.isolated_resources("pfverify-")
                self.assertTrue(kept["containers"])
                self.assertFalse([item for item in kept["containers"] if item["status"] == "running"])
                self.assertEqual(self.operation("purge")[2]["phase"], "cancelled")

    def test_fv2_a_failing_check_records_failed_and_stops_before_any_deletion(self):
        rows = []
        for label, change, wanted in (
                ("heads", {"isolated_plane_defaults": {"restore_override": {"heads": ["r0"]}}}, "heads:"),
                ("rows", {"isolated_plane_defaults": {"restore_override": {"rows": {"public.part": 1}}}}, "rows:"),
                ("frontend", {"isolated_faults": {"missing_frontend": True}}, "frontend")):
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.update_state(**change)
                code, out, err, _ = self.main("purge", "--keep-backups")
                self.assertEqual(code, 1, out + err)
                self.assertIn("functional-verification-failed:", err)
                op, plan, journal = self.operation("purge")
                self.assertEqual((journal["phase"], journal["deletion"]), ("cancelled", None))
                self.assertTrue([item for item in self.state()["volumes"]
                                 if item["name"] == self.project + "_postgres_data"])
                record = [item for item in self.records(self.purged_bundle()["name"])
                          if item["level"] == "functional_recovery_verified"][0]
                self.assertEqual(record["result"], "failed")
                failed = [check["name"] for check in record["checks"] if check["result"] == "failed"]
                self.assertTrue([name for name in failed if wanted in name], failed)
                self.assertIn(failed[0], err)
                if label != "frontend":  # a data check fails first: the runtime checks are not reached
                    self.assertIn("not reached", json.dumps(record["checks"]))
                rows.append({"case": label, "failed": failed[0]})
        evidence("FUNCTIONAL-1-FV-2", {"rows": rows})

    def test_it7_the_instance_inventory_never_lists_the_topology(self):
        with self.crashing(("runtime", "inside")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("purge", "--keep-backups")
        self.assertTrue(self.isolated_resources("pfverify-")["containers"])
        inventory = self.controller().docker_inventory()
        names = [json.dumps(item.identity) + item.key for item in inventory.owned + inventory.blockers
                 + inventory.excluded if item.kind in ("container", "volume", "network")]
        self.assertFalse([name for name in names if "pfverify-" in name])
        images = {item.key: item.reason for item in inventory.owned + inventory.excluded if item.kind == "image"}
        self.assertIn("foreign-in-use", [reason for key, reason in images.items() if "-backend:" in key])
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 0, out + err)
        inventory = self.controller().docker_inventory()
        self.assertFalse([item for item in inventory.excluded
                          if item.kind == "image" and item.reason == "foreign-in-use"])

    def test_it8_the_bundle_password_never_reaches_an_operation_file(self):
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 0, out + err)
        op = self.operation("purge")[0]
        for path in (self.context.operations_dir / op).rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                if re.fullmatch(r"compose-[0-9]+\.json", path.name) \
                        and not json.loads(data)["name"].startswith(("pfverify-", "pfrecover-")):
                    # The A1.3 envelope record of the instance's own render (0600, pre-existing): it holds the
                    # instance's values by design; only topology renders are bound by IT-8.
                    continue
                self.assertNotIn(b"=" + SECRET.encode(), data, path)
                self.assertNotIn(b'"' + SECRET.encode() + b'"', data, path)
        self.assertNotIn(SECRET, out + err)
        isolated = self.context.operations_dir / op / "isolated"
        self.assertFalse([path for path in isolated.rglob("*") if path.name in ("app.env", "compose.json")])

    def test_it5_pz7_a_teardown_blocker_keeps_the_topology_and_still_cancels(self):
        with self.crashing(("before-teardown", "inside")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("purge", "--keep-backups")
        op, plan, journal = self.operation("purge")
        verification = next(e for e in plan["effects"] if e["target"] == "bundle:purge")
        project = verification["preconditions"][0].split(":", 1)[1]
        state = self.state()
        state["containers"].append(pfx.container("f" * 64, "foreign-user", {"other": "x"},
                                                 volumes=[project + "_postgres_data"], status="exited"))
        self.fake.write_state(state)
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 0, out + err)
        journal = self.operation("purge")[2]
        self.assertEqual(journal["phase"], "cancelled")
        self.assertIn("isolated-topology", [item["kind"] for item in journal["retained_artifacts"]])
        self.assertIn("isolated-topology-kept: " + project, out + err)
        self.assertTrue(self.isolated_resources(project)["volumes"])
        topology = json.loads((self.context.operations_dir / op / "isolated" / project / "topology.json").read_bytes())
        self.assertIn("refused", [entry["state"] for entry in topology["teardowns"]])

    def test_pz10_resume_in_deleting_runs_the_gate_without_a_new_verification(self):
        with self.crashing(("deletion-plan", "after-intent")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("purge", "--keep-backups")
        self.assertEqual(self.operation("purge")[2]["phase"], "deleting")
        self.fake.clear_calls()
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("purge")[2]["phase"], "completed")
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["compose"] and "-p" in argv
                          and argv[argv.index("-p") + 1].startswith("pfverify-")])
        self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "purged")

    def test_rq8_reports_reach_the_parser_and_never_leave_it(self):
        self.update_state(reconcile={"*": {"report": "mismatch:c=2"}})
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 0, out + err)
        bundle = self.purged_bundle()["name"]
        record = [item for item in self.records(bundle) if item["level"] == "functional_recovery_verified"][0]
        app = next(check for check in record["checks"] if check["name"] == "app-invariants")
        self.assertEqual(app["result"], "passed")
        self.assertIn("c:mismatch:2", app["detail"])
        for root in (self.context.operations_dir, self.context.artifacts_dir):
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"PN-SECRET-4711", path.read_bytes(), path)
                    self.assertNotIn(b"ENTITY-0042", path.read_bytes(), path)
        self.assertNotIn("PN-SECRET-4711", out + err)
        self.assertNotIn("Traceback", out + err)
        # A restored database that disagrees with the source oracle fails the verification.
        self.tearDown()
        self.setUp()
        self.update_state(reconcile={self.project: {"report": "mismatch:c=2"}})
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.assertIn("functional-verification-failed:", err)
        self.assertIn("app-invariants", err)
        self.assertNotIn("PN-SECRET-4711", out + err)
        # Probe exit 1 is neither available nor unavailable: the source check is incomplete and the purge stops.
        self.tearDown()
        self.setUp()
        self.update_state(reconcile={"*": {"probe": 1}})
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.assertIn("app-check-failed:", err)
        self.assertFalse([argv for argv in self.fake.argvs() if "app.cli" in argv])


class RestoreTarget(Plane):
    """RX-1..RX-9: the restore target identity, recorded paths as provenance only and the database image rule."""

    def purged(self):
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("purge")
        return next(item for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")["name"]

    def rewrite(self, bundle, mutate):
        folder = self.context.paths.recovery / self.project / bundle
        for path in (folder, *folder.rglob("*")):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        manifest = json.loads((folder / "manifest.json").read_bytes())
        mutate(manifest)
        data = pf_instance.normalize_json(manifest)
        (folder / "manifest.json").write_bytes(data)
        (folder / "manifest.sha256").write_text(pf_instance.sha256_bytes(data) + "\n")

    def test_rx1_recorded_paths_are_provenance_only(self):
        bundle = self.purged()
        sentinel = self.base / "foreign-workspace"
        sentinel.mkdir()
        (sentinel / "keep.txt").write_text("untouched")
        before = pfx.snapshot_tree(sentinel)
        self.rewrite(bundle, lambda m: m["source_instance"].update(workspace=str(sentinel),
                                                                    repository="Other/repository"))
        code, out, err, _ = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"note: bundle-workspace-differs: the bundle recorded workspace {sentinel}", out)
        self.assertEqual(pfx.snapshot_tree(sentinel), before)
        self.assertEqual(self.operation("restore-instance")[2]["phase"], "completed")
        self.assertIn("Application invariants: clean", out)

    def test_rx2_rx8_another_instance_bundle_is_refused_before_any_confirmation(self):
        bundle = self.purged()
        self.rewrite(bundle, lambda m: m["source_instance"].update(instance_id="00000000-0000-4000-8000-0000000000aa"))
        before = pfx.operations_bytes(self.context)
        code, out, err, phrases = self.main("restore-instance", bundle)
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"restore-target-mismatch: bundle {bundle} belongs to instance 00000000", err)
        self.assertNotIn("side-by-side", err.split("restore-target-mismatch", 1)[1].split("Nothing", 1)[0].lower()
                         .replace("exact and side-by-side restore", ""))
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.operations_bytes(self.context), before)
        code, out, err, phrases = self.main("restore-instance", bundle, "--side-by-side")
        self.assertIn("restore-target-mismatch:", err)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["image", "load"]])
        evidence("RESTORE-TARGET-1", {"refusal": err.strip().splitlines()[-1]})

    def test_rx5_an_absent_postgres_tag_is_tagged_to_the_bundle_image(self):
        bundle = self.purged()
        state = self.state()
        for image in state["images"]:
            if pf_docker.DB_IMAGE in image["repo_tags"]:
                state["images"].remove(image)
        self.fake.write_state(state)
        code, out, err, phrases = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        self.assertIn("note: db-image-tagged: postgres:16 was absent", out)
        op, plan, journal = self.operation("restore-instance")
        self.assertIn("image-tag", [effect["type"] for effect in plan["effects"]])
        tagged = [image for image in self.state()["images"] if pf_docker.DB_IMAGE in image["repo_tags"]]
        self.assertEqual([image["id"] for image in tagged], [pfx.DB_IMAGE_ID])


class SideBySide(Plane):
    """SB-1..SB-11: the side-by-side recovery target beside the running instance."""

    def bundle(self):
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("purge")
        bundle = next(item for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")["name"]
        code, out, err, _ = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        self.fake.clear_calls()
        return bundle

    def live_hashes(self):
        state = self.state()
        return {"override": (self.context.state_dir / "active-images.yaml").read_bytes(),
                "env": (self.paths["configuration"] / ".env").read_bytes(),
                "pointer": (self.context.state_dir / "deployed.json").read_bytes(),
                "databases": json.dumps(state["plane"]["databases"], sort_keys=True),
                "workspace": pfx.snapshot_tree(self.paths["workspace"]),
                "containers": sorted((item["id"], item["status"], item["image"]) for item in state["containers"]
                                     if item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project),
                "tags": sorted((tag, image["id"]) for image in state["images"] for tag in image["repo_tags"])}

    def test_sb1_sb2_a_kept_isolated_recovery_target(self):
        bundle = self.bundle()
        before = self.live_hashes()
        code, out, err, phrases = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(phrases, ["RESTORE COPY " + bundle])
        op, plan, journal = self.operation("restore-side-by-side")
        self.assertEqual(journal["phase"], "completed")
        project = next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "recovery-target")
        self.assertTrue(project.startswith("pfrecover-"))
        self.assertEqual(self.live_hashes(), before)
        for argv in self.fake.argvs():
            if argv[:1] == ["compose"] and "-p" in argv and argv[argv.index("-p") + 1] == self.project:
                verb = [word for word in argv[argv.index("-f") + 2:] if not word.startswith("-")][:1]
                self.assertTrue(pf.unclassified_mutation("docker", argv) is None or verb == ["exec"] and
                                "psql" in argv, argv)
        record = [item for item in self.records(bundle) if item["level"] == "functional_recovery_verified"
                  and item["operation_id"] == op][0]
        self.assertEqual((record["result"], record["target"]["removed"]), ("passed", False))
        running = self.isolated_resources(project)["containers"]
        self.assertEqual(sorted(item["status"] for item in running), ["running"] * 3)
        self.assertFalse([item for item in running if item.get("ports") and any(item["ports"].values())])
        code, out, err, _ = self.main("status")
        self.assertIn(f"Recovery targets: {project} from bundle {bundle} (operation {op}", out)
        self.assertFalse([name for name in self.state()["plane"]["databases"] if name.startswith("pf_recovery_")])
        evidence("ISOLATION-1", {"record": record, "containers": running})
        # CU-8 / XC-3: removed only by its selector and phrase.
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("cleanup-nothing", err)
        code, out, err, phrases = self.main("cleanup", "--apply", "--recovery-target", project)
        self.assertEqual(code, 0, out + err)
        self.assertIn("REMOVE RECOVERY TARGET " + project, phrases)
        self.assertEqual(self.isolated_resources(project), {"containers": [], "volumes": [], "networks": []})

    def test_sb2_sb11_a_published_listener_fails_the_target(self):
        for fault in ("host_port", "loopback_port", "loopback6_port"):
            with self.subTest(fault):
                self.tearDown()
                self.setUp()
                bundle = self.bundle()
                state = self.state()
                state["isolated_faults"] = {fault: True}
                self.fake.write_state(state)
                code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
                self.assertEqual(code, 1, out + err)
                self.assertIn("functional-verification-failed:", err)
                self.assertIn("isolation:listener", err)
                self.assertIn("--recovery-target pfrecover-", err)
                op, plan, journal = self.operation("restore-side-by-side")
                self.assertEqual(journal["phase"], "failed_preserved")

    def test_sb3_crash_in_activating_goes_forward_without_a_second_restore(self):
        bundle = self.bundle()
        with self.crashing(self.crash_before("e0002", "after-intent")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "activating")
        self.fake.clear_calls()
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "completed")
        self.assertFalse([argv for argv in self.fake.argvs() if "pg_restore" in argv and "--list" not in argv])

    def test_sb3_a_replaced_volume_is_a_lost_target(self):
        bundle = self.bundle()
        with self.crashing(self.crash_before("e0002")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle, "--side-by-side")
        state = self.state()
        for volume in state["volumes"]:
            if volume["name"].startswith("pfrecover-"):
                volume["created_at"] = "2026-10-08T00:00:00Z"
        self.fake.write_state(state)
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 1, out + err)
        self.assertIn("recovery-target-lost", out + err)
        self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "failed_preserved")
        self.assertEqual(self.isolated_resources("pfrecover-"), {"containers": [], "volumes": [], "networks": []})

    def test_sb3_abandon_tears_the_target_down(self):
        bundle = self.bundle()
        with self.crashing(self.crash_before("e0003")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle, "--side-by-side")
        op, plan, journal = self.operation("restore-side-by-side")
        project = plan["effects"][-1]["target"].split(":", 1)[1]
        code, out, err, phrases = self.main("resume", "--abandon")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ABANDON RECOVERY TARGET " + project, phrases)
        self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "cancelled")
        self.assertEqual(self.isolated_resources(project), {"containers": [], "volumes": [], "networks": []})

    def test_sb4_the_repeated_command_aliases_resume_and_another_bundle_conflicts(self):
        bundle = self.bundle()
        with self.crashing(self.crash_before("e0002")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle, "--side-by-side")
        code, out, err, _ = self.main("restore-instance", "purge-20261001T000000Z-000000000000-abcdef",
                                      "--side-by-side")
        self.assertEqual(code, 1)
        self.assertIn("plan-inputs-conflict:", err)
        code, out, err, _ = self.main("backup")
        self.assertIn("operation-open:", err)
        code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "completed")

    def test_sb10_a_load_that_would_retag_is_refused(self):
        bundle = self.bundle()
        state = self.state()
        backend = pfx.topology_image_id("a", "backend")
        state["images"] = [image for image in state["images"] if image["id"] != backend]
        state["images"].append(pfx.image(pfx.image_id("intruder"), [], {}))
        self.fake.write_state(state)
        # Point the bundle's backend tag at another image: loading would re-point it.
        manifest = json.loads((self.context.paths.recovery / self.project / bundle / "manifest.json").read_bytes())
        tag = manifest["purge"]["saved_image_refs"][0]
        state = self.state()
        for image in state["images"]:
            if image["id"] == pfx.image_id("intruder"):
                image["repo_tags"].append(tag)
        self.fake.write_state(state)
        code, out, err, phrases = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 1, out + err)
        self.assertIn("image-load-would-retag:", err)
        self.assertEqual(phrases, [])
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["image", "load"]])


class Cleanup(Plane):
    """CU-1..CU-13 (offline subset with the real filesystem and the fake daemon)."""

    def closed_backup_with_leftover(self):
        """A closed (failed) backup whose verification candidate was retained and whose first capture attempt left
        an unsealed folder."""
        name = "pf_verify_" + "a" * 20
        state = self.state()
        state["plane"]["databases"][name] = {"heads": ["r1"], "rows": {}, "allow": True, "owner": "partflow_staging",
                                             "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
        self.fake.write_state(state)
        plan = pfx.lifecycle_plan(self.context, "backup", [
            pfx.effect("capturing", "capture", "checkpoint:scheduled-or-manual-backup",
                       preconditions=["bundle:" + pfx.CHECKPOINT_ID, "verify:" + name]),
            pfx.effect("verifying", "verification", "bundle:scheduled-or-manual-backup")])
        attempt = self.context.paths.backups / "revisions" / self.project / pfx.CHECKPOINT_ID
        attempt.mkdir(parents=True)
        (attempt / "database.dump").write_text("partial")
        journal = pfx.lifecycle_journal(plan, phase="failed_preserved",
                                        states={"e0001": "complete", "e0002": "unknown"},
                                        retained=[{"kind": "bundle-attempt", "name": pfx.CHECKPOINT_ID,
                                                   "sha256": None}],
                                        result={"outcome": "failed_preserved", "deployment_id": None})
        pfx.write_operation(self.context, plan, journal)
        # A name matching the pattern that no operation recorded (CU-3), pf_keep_ and pf_recovery_ (never touched).
        state = self.state()
        for extra in ("pf_verify_" + "b" * 20, "pf_keep_20261001t000000z_abcdef", "pf_recovery_20261001_abcdef"):
            state["plane"]["databases"][extra] = {"heads": [], "rows": {}, "allow": False,
                                                  "owner": "partflow_staging", "locale": ["UTF8", "C.UTF-8",
                                                                                          "C.UTF-8"]}
        self.fake.write_state(state)
        return name, attempt

    def test_cu1_the_report_is_observe_only(self):
        name, attempt = self.closed_backup_with_leftover()
        operations = sorted(os.listdir(str(self.context.operations_dir)))
        code, out, err, phrases = self.main("cleanup")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"candidate-database {name}", out)
        self.assertIn(f"bundle-attempt {pfx.CHECKPOINT_ID}", out)
        self.assertIn("report-only (never removed): retained-database pf_keep_20261001t000000z_abcdef", out)
        self.assertIn("report-only (never removed): legacy-recovery-database pf_recovery_20261001_abcdef", out)
        self.assertNotIn("pf_verify_" + "b" * 20, out)
        self.assertEqual(sorted(os.listdir(str(self.context.operations_dir))), operations)
        self.assertEqual(phrases, [])
        self.assertFalse([argv for argv in self.fake.argvs() if pf.unclassified_mutation("docker", argv)])

    def test_cu2_cu3_apply_removes_the_recorded_default_set_only(self):
        name, attempt = self.closed_backup_with_leftover()
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertEqual(code, 0, out + err)
        databases = self.state()["plane"]["databases"]
        self.assertNotIn(name, databases)
        for kept in ("pf_verify_" + "b" * 20, "pf_keep_20261001t000000z_abcdef", "pf_recovery_20261001_abcdef",
                     "partflow_staging"):
            self.assertIn(kept, databases)
        self.assertFalse(attempt.exists())
        op, plan, journal = self.operation("cleanup")
        self.assertEqual(journal["phase"], "completed")
        self.assertTrue(phrases[0].startswith("CLEANUP " + self.project + " "))
        evidence("CLEANUP-1", {"plan": [effect["target"] for effect in plan["effects"]]})

    def test_cu10_a_busy_database_is_kept(self):
        name, attempt = self.closed_backup_with_leftover()
        state = self.state()
        state["plane"]["sessions"] = {name: 2}
        self.fake.write_state(state)
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"cleanup-item-busy: {name} has 2 open session(s); it was kept", out)
        self.assertIn(name, self.state()["plane"]["databases"])
        self.assertFalse(attempt.exists())  # the next item was still processed
        self.assertEqual(self.operation("cleanup")[2]["phase"], "failed_preserved")

    def test_cu9_an_open_cleanup_is_re_entered_only_with_equal_selectors(self):
        name, attempt = self.closed_backup_with_leftover()
        with self.crashing(self.crash_before("e0002")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("cleanup", "--apply")
        op, plan, journal = self.operation("cleanup")
        self.assertEqual(journal["phase"], "deleting")
        code, out, err, _ = self.main("cleanup", "--apply", "--generation", pfx.GENERATION)
        self.assertEqual(code, 1, out + err)
        self.assertIn("plan-inputs-conflict:", err)
        code, out, err, _ = self.main("backup")
        self.assertEqual(code, 1, out + err)
        self.assertIn("operation-open:", err)
        code, out, err, _ = self.main("cleanup", "--apply")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("cleanup")[0], op)
        self.assertEqual(self.operation("cleanup")[2]["phase"], "completed")
        self.assertNotIn(name, self.state()["plane"]["databases"])
        self.assertFalse(attempt.exists())

    def displaced_history(self, *, unique):
        """A closed restore-instance that displaced the checkpoint history to ``<project>.pre-restore-<8 hex>``; its
        one checkpoint is also in the active history unless ``unique``."""
        name = self.project + ".pre-restore-0badcafe"
        displaced = self.context.paths.backups / "revisions" / name / pfx.CHECKPOINT_ID
        displaced.mkdir(parents=True)
        (displaced / "manifest.json").write_bytes(b'{"synthetic": "manifest"}')
        if not unique:
            active = self.context.paths.backups / "revisions" / self.project / pfx.CHECKPOINT_ID
            active.mkdir(parents=True)
            (active / "manifest.json").write_bytes(b'{"synthetic": "manifest"}')
        plan = pfx.lifecycle_plan(self.context, "restore-instance")
        journal = pfx.lifecycle_journal(plan, phase="completed",
                                        states={effect["effect_id"]: "complete" for effect in plan["effects"]},
                                        retained=[{"kind": "checkpoint-history", "name": name, "sha256": None}],
                                        result={"outcome": "succeeded", "deployment_id": None})
        pfx.write_operation(self.context, plan, journal)
        return name, displaced.parent

    def test_cu12_a_history_with_a_unique_checkpoint_is_refused_before_the_confirmation(self):
        name, folder = self.displaced_history(unique=True)
        before = pfx.snapshot_tree(self.context.paths.backups)
        operations = sorted(os.listdir(str(self.context.operations_dir)))
        code, out, err, phrases = self.main("cleanup", "--apply", "--checkpoint-history", name)
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"cleanup-history-unique-checkpoint: {name} holds checkpoint {pfx.CHECKPOINT_ID}", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.snapshot_tree(self.context.paths.backups), before)
        for name in set(os.listdir(str(self.context.operations_dir))) - set(operations):
            self.assertFalse((self.context.operations_dir / name / "plan.json").exists())  # no operation opened

    def test_cu7_a_duplicated_history_is_removed_with_its_selector_and_phrase(self):
        name, folder = self.displaced_history(unique=False)
        code, out, err, phrases = self.main("cleanup")
        self.assertEqual(code, 0, out + err)
        self.assertIn(name, out)
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertIn("cleanup-nothing:", err)  # selector-only: never part of the default set
        self.assertTrue(folder.exists())
        code, out, err, phrases = self.main("cleanup", "--apply", "--checkpoint-history", name)
        self.assertEqual(code, 0, out + err)
        self.assertIn("DELETE CHECKPOINT HISTORY " + name, phrases)
        self.assertFalse(folder.exists())
        self.assertTrue((self.context.paths.backups / "revisions" / self.project / pfx.CHECKPOINT_ID).exists())

    def test_cu11_nothing_and_unknown_change_nothing(self):
        before = sorted(os.listdir(str(self.context.operations_dir)))
        code, out, err, _ = self.main("cleanup", "--apply")
        self.assertIn("cleanup-nothing:", err)
        code, out, err, _ = self.main("cleanup", "--apply", "--generation", "wsg-20261001T000000Z-00000000")
        self.assertIn("cleanup-target-unknown: --generation wsg-20261001T000000Z-00000000", err)
        new = set(os.listdir(str(self.context.operations_dir))) - set(before)
        for name in new:
            self.assertFalse((self.context.operations_dir / name / "plan.json").exists())

    def retained_generation(self, *, content=b"old tree\n"):
        """A closed update whose W2 retained generation ``wsg-...`` exists in the generation container."""
        generation = pfx.GENERATION
        container = pf_instance.generation_container(self.context)
        container.mkdir(mode=0o700)
        os.chmod(container, 0o700)
        (container / generation).mkdir()
        (container / generation / "app-version.txt").write_bytes(content)
        plan = pfx.lifecycle_plan(self.context, "update", generation=generation)
        journal = pfx.lifecycle_journal(plan, phase="completed", states={e["effect_id"]: "complete"
                                                                         for e in plan["effects"]},
                                        retained=[{"kind": "workspace-generation", "name": generation,
                                                   "sha256": None}],
                                        result={"outcome": "succeeded", "deployment_id": None})
        pfx.write_operation(self.context, plan, journal)
        return generation, container

    def test_cu6_a_generation_is_sealed_then_retired(self):
        generation, container = self.retained_generation()
        code, out, err, phrases = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 0, out + err)
        self.assertFalse((container / generation).exists())
        seal = self.paths["backups"] / "generations" / self.project / generation
        record = json.loads((seal / "seal.json").read_bytes())
        self.assertEqual(pf.lifecycle_errors(record, "generation_seal"), [])
        fd = os.open(str(seal / "workspace.tar.gz"), os.O_RDONLY)
        try:
            pf_source.inspect_archive(fd, limits=pf_source.SOURCE_LIMITS)
        finally:
            os.close(fd)

    def test_cu6_an_open_handle_keeps_the_generation(self):
        generation, container = self.retained_generation()
        holder = subprocess.Popen([sys.executable, "-c", "import sys,time; f=open(sys.argv[1]); time.sleep(60)",
                                   str(container / generation / "app-version.txt")])
        try:
            __import__("time").sleep(0.5)
            code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        finally:
            holder.kill()
            holder.wait()
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"generation-in-use: {generation}", out)
        self.assertTrue((container / generation).exists())
        self.assertEqual(self.operation("cleanup")[2]["phase"], "failed_preserved")

    def test_cu6_a_link_inside_is_unsupported(self):
        generation, container = self.retained_generation()
        os.symlink("/etc/passwd", str(container / generation / "link"))
        code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"generation-unsupported-entry: {generation} holds a link or special file (link)", out)
        self.assertTrue((container / generation).exists())

    def test_cu6_a_write_after_the_seal_keeps_the_tree(self):
        generation, container = self.retained_generation()

        def seam(label, when):
            if when == "inside" and label == pf.INSIDE_PREFIX + "before-remove":
                (container / generation / "late.txt").write_text("written after the seal")

        with self.crashing(seam):
            code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"cleanup-item-changed: remove:generation:{generation} changed after the cleanup was approved",
                      out)
        self.assertTrue((container / generation / "late.txt").exists())
        self.assertTrue((self.paths["backups"] / "generations" / self.project / generation / "seal.json").exists())


class Acknowledge(Plane):
    """AK-1..AK-6: the attended acknowledgement of runner records in a no-journal directory."""

    def no_journal(self):
        op = "20261007T030000Z-backup-emergency-9a8b7c6d"
        directory = self.context.operations_dir / op
        directory.mkdir(mode=0o700)
        record = [{"id": "1" * 32, "recorded_at": "20261007T030100Z", "outcome": "timeout", "tool": "docker",
                   "executable": "/usr/bin/docker", "argv": ["compose", "exec", "-T", "db", "pg_restore"],
                   "cwd": "/", "pid": 4242, "process_group": 4242, "returncode": None,
                   "effect": {"kind": "compose-exec", "verb": "pg_restore", "project": self.project,
                              "service": "db", "database": "pf_verify_" + "c" * 20, "targets": []},
                   "label": "docker"}]
        (directory / "unresolved-effects.json").write_text(json.dumps(record, indent=2) + "\n")
        os.chmod(directory / "unresolved-effects.json", 0o600)
        return op, directory

    def install_report(self):
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.reload()])
        return [item.code for item in report.conflicts]

    def test_ak1_ak2_ak5_acknowledged_records_no_longer_block_the_installer(self):
        op, directory = self.no_journal()
        self.assertEqual(self.install_report(), ["instance-effects-unresolved"])
        before = sorted(os.listdir(str(self.context.operations_dir)))
        code, out, err, phrases = self.main("resume", "--operation", op, "--acknowledge")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(phrases, ["ACKNOWLEDGE " + op[-8:]])
        self.assertIn("observed: services db running", out)
        self.assertEqual(sorted(os.listdir(str(self.context.operations_dir))), before)
        files = sorted(path.name for path in directory.iterdir() if path.name.startswith("acknowledgement-"))
        self.assertEqual(len(files), 1)
        path = directory / files[0]
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        record = json.loads(path.read_bytes())
        self.assertEqual(pf.lifecycle_errors(record, "runner_acknowledgement"), [])
        self.assertEqual(self.install_report(), [])
        code, out, err, _ = self.main("status")
        self.assertIn("1 acknowledged", out)
        # AK-2: an appended record is open again.
        records = json.loads((directory / "unresolved-effects.json").read_text())
        records.append(dict(records[0], id="2" * 32))
        (directory / "unresolved-effects.json").write_text(json.dumps(records, indent=2) + "\n")
        self.assertEqual(self.install_report(), ["instance-effects-unresolved"])
        evidence("ACK-1", {"record": record})

    def test_ak3_ak4_ak6_refusals(self):
        op, directory = self.no_journal()
        code, out, err, _ = self.main("resume", "--operation", op, "--acknowledge")
        self.assertEqual(code, 0, out + err)
        code, out, err, phrases = self.main("resume", "--operation", op, "--acknowledge")
        self.assertIn("acknowledge-not-legal: operation " + op + " is already acknowledged", err)
        self.assertEqual(phrases, [])
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = pf.main(["--instance", "staging", "resume", "--acknowledge"], installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        self.assertEqual(code, 2)
        # AK-4: a running owned one-off container -> effect-still-running, nothing written.
        op2 = "20261007T031500Z-backup-emergency-1b2c3d4e"
        other = self.context.operations_dir / op2
        other.mkdir(mode=0o700)
        shutil.copy(str(directory / "unresolved-effects.json"), str(other / "unresolved-effects.json"))
        state = self.state()
        state["containers"].append(pfx.container("c" * 64, self.project + "-backend-run-1",
                                                 pfx.compose_run_labels(self.context, "backend")))
        self.fake.write_state(state)
        code, out, err, phrases = self.main("resume", "--operation", op2, "--acknowledge")
        self.assertEqual(code, 1, out + err)
        self.assertIn("effect-still-running:", err)
        self.assertEqual(sorted(path.name for path in other.iterdir()), ["unresolved-effects.json"])


class ResetRollback(Plane):
    """RP-1, RP-2, RP-4, RP-6, RP-7: current-data protection of reset-db on the real plane."""

    def reset(self):
        return self.main("reset-db")

    def assert_summary_bound(self, kind):
        op, plan, journal = self.operation(kind)
        path = self.context.operations_dir / op / "confirmation-summary.txt"
        data = path.read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), plan["confirmation"]["summary_sha256"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        code, out, err, _ = self.main("status", "--operation", op)
        self.assertEqual(code, 0, out + err)
        self.assertIn(data.decode("utf-8").splitlines()[0], out)
        return op, plan, journal, data.decode("utf-8")

    def test_rp1_a_consistent_instance_keeps_the_healthy_preservation(self):
        code, out, err, phrases = self.reset()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(phrases, ["RESET partflow_staging"])
        op, plan, journal, summary = self.assert_summary_bound("reset-db")
        self.assertEqual(journal["phase"], "completed")
        self.assertIn("Current data preservation: healthy checkpoint", summary)
        databases = self.state()["plane"]["databases"]
        self.assertEqual(databases["partflow_staging"]["heads"], ["r1"])
        self.assertTrue([name for name in databases if name.startswith("pf_keep_")])

    def test_rp2_rp4_a_schema_image_mismatch_is_preserved_as_emergency_first(self):
        state = self.state()
        state["plane"]["databases"]["partflow_staging"]["heads"] = ["r0"]
        self.fake.write_state(state)
        code, out, err, phrases = self.reset()
        self.assertEqual(code, 0, out + err)
        op, plan, journal, summary = self.assert_summary_bound("reset-db")
        self.assertEqual(journal["phase"], "completed")
        self.assertIn("emergency preservation (schema-image-mismatch)", summary)
        retained = [name for name in self.state()["plane"]["databases"] if name.startswith("pf_keep_")]
        self.assertEqual(len(retained), 1)
        self.assertIn(retained[0], summary)
        self.assertEqual(self.state()["plane"]["databases"]["partflow_staging"]["heads"], ["r1"])
        self.assertEqual(self.state()["plane"]["databases"][retained[0]]["heads"], ["r0"])
        capture = next(effect for effect in journal["effects"] if effect["effect_id"] == "e0002")
        self.assertIn("emergency", capture["evidence"] or "")
        self.assertEqual(pf_config.rollback_target(plan, journal), "<checkpoint>")

    def test_rp7_an_unreadable_contract_is_refused_before_the_confirmation(self):
        state = self.state()
        for image in state["images"]:
            image.pop("contract", None)
        state["plane"]["databases"]["partflow_staging"]["heads"] = ["r0"]
        self.fake.write_state(state)
        before = sorted(os.listdir(str(self.context.operations_dir)))
        code, out, err, phrases = self.reset()
        self.assertEqual(code, 1, out + err)
        self.assertIn("reset-contract-unknown:", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.operations_of(self.context, "reset-db"), [])
        del before


class AbortDeploy(Plane):
    """AD-1, AD-2, AD-3: abort-deploy after the frontend opened preserves the current database first."""

    def deploy(self, *, phase, unknown):
        (self.context.state_dir / "deployed.json").unlink()
        return pfx.interrupted_deploy(self.context, phase=phase, unknown=unknown)

    def test_ad1_after_the_frontend_opened_the_database_is_preserved_first(self):
        self.deploy(phase="activating", unknown="e0006")
        code, out, err, phrases = self.main("abort-deploy")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(phrases, ["ABORT DEPLOY " + self.project])
        op, plan, journal = self.operation("abort-deploy")
        self.assertEqual(journal["phase"], "completed")
        self.assertEqual([effect["phase"] for effect in plan["effects"]][:2], ["preserving", "preserving"])
        self.assertEqual(plan["effects"][1]["target"], "checkpoint:before-abort")
        path = self.context.operations_dir / op / "confirmation-summary.txt"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), plan["confirmation"]["summary_sha256"])
        checkpoint = plan["effects"][1]["preservation_refs"] or [p.split(":", 1)[1] for p in
                                                                 plan["effects"][1]["preconditions"]
                                                                 if p.startswith("bundle:")]
        folders = list((self.context.paths.backups / "revisions" / self.project).iterdir())
        self.assertTrue([folder for folder in folders if folder.name in checkpoint
                         or any(item["name"] == folder.name for item in journal["retained_artifacts"])], folders)
        self.assertFalse([item for item in self.state()["volumes"] if item["name"] == self.project + "_postgres_data"])

    def test_ad2_an_unreachable_database_is_refused_before_the_confirmation(self):
        self.deploy(phase="activating", unknown="e0006")
        state = self.state()
        for item in state["containers"]:
            if item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "db":
                item["status"] = "exited"
        self.fake.write_state(state)
        code, out, err, phrases = self.main("abort-deploy")
        self.assertEqual(code, 1, out + err)
        self.assertIn("preservation-failed:", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.operations_of(self.context, "abort-deploy"), [])

    def test_ad4_a_crash_in_preserving_re_runs_the_preservation_before_any_deletion(self):
        for point in ("before-intent", "after-intent", "after-effect"):
            with self.subTest(point=point):
                self.tearDown()
                self.setUp()
                self.deploy(phase="activating", unknown="e0006")
                with self.crashing(("checkpoint:before-abort", point)):
                    with self.assertRaises(pf.SimulatedCrash):
                        self.main("abort-deploy")
                op, plan, journal = self.operation("abort-deploy")
                self.assertEqual(journal["phase"], "preserving")
                self.assertTrue([item for item in self.state()["volumes"]
                                 if item["name"] == self.project + "_postgres_data"])  # nothing deleted yet
                self.fake.clear_calls()
                code, out, err, _ = self.main("resume")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.operation("abort-deploy")[2]["phase"], "completed")
                calls = self.fake.argvs()
                dumps = [index for index, argv in enumerate(calls) if "pg_dump" in argv]
                removals = [index for index, argv in enumerate(calls) if argv[:2] == ["volume", "rm"]]
                self.assertTrue(removals)
                if point != "after-effect":
                    self.assertTrue(dumps and max(dumps) < min(removals), (dumps, removals))

    def test_ad3_before_the_frontend_the_plan_is_unchanged(self):
        self.deploy(phase="initializing", unknown="e0003")
        code, out, err, phrases = self.main("abort-deploy")
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("abort-deploy")
        self.assertEqual([effect["type"] for effect in plan["effects"]], ["resource-delete", "file-write"])
        self.assertEqual(journal["phase"], "completed")




class CapacityIntegrated(Plane):
    """CP-3, CP-6, CP-7: the Docker root and the real statvfs."""

    def test_cp3_an_unknown_docker_root_refuses_the_backup_with_the_emergency_route(self):
        state = self.state()
        state["info"] = dict(state["info"])
        del state["info"]["DockerRootDir"]
        self.fake.write_state(state)
        code, out, err, _ = self.main("backup")
        self.assertEqual(code, 1, out + err)
        self.assertIn("capacity-unmeasurable:", err)
        self.assertIn("backup --emergency' preserves the database without this check", err)
        self.assertEqual(pfx.operations_of(self.context, "backup"), [])

    def test_cp7_a_floor_above_the_free_space_refuses_through_statvfs(self):
        config = self.paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1 << 30)) + "\n")
        code, out, err, _ = self.main("backup")
        self.assertEqual(code, 1, out + err)
        self.assertIn("capacity-insufficient:", err)
        self.assertIn("Nothing was changed.", err)
        self.assertEqual(pfx.operations_of(self.context, "backup"), [])
        directories = list(self.context.operations_dir.iterdir())
        capacity = [path / "capacity.json" for path in directories if (path / "capacity.json").exists()]
        self.assertTrue(capacity)
        decisions = json.loads(capacity[-1].read_bytes())
        self.assertIn("short", [item["result"] for item in decisions])
        self.assertNotIn(SECRET, capacity[-1].read_text())
        evidence("CAPACITY-1", {"decisions": decisions})

    def test_cp8_backups_and_docker_root_on_one_device_are_summed(self):
        backups = self.context.paths.backups
        self.assertEqual(os.stat(str(backups)).st_dev, os.stat("/").st_dev)  # DockerRootDir "/" of the fake
        info = os.statvfs(str(backups))
        free = info.f_bavail * info.f_frsize
        live = int(free / 2.5)  # backups needs ~live, the Docker root ~2 x live: each fits alone, not summed

        with mock.patch.object(pf.Controller, "database_sizes", lambda controller: {"partflow_staging": live}):
            code, out, err, _ = self.main("backup")
        self.assertEqual(code, 1, out + err)
        self.assertIn("capacity-insufficient:", err)
        line = err.split("capacity-insufficient:", 1)[1].split("\n", 1)[0]
        self.assertEqual(line.count(" on device "), 1, line)
        self.assertIn("backups", line)
        self.assertIn("docker-root", line)
        self.assertEqual(pfx.operations_of(self.context, "backup"), [])
        evidence("CAPACITY-1-CP-8", {"refusal": line, "free_bytes": free, "live_bytes": live})


# ============================================================================ XC: through the installed launcher

import test_operations as tops  # noqa: E402  (the CL harness: installed launcher, fake plane, scripted terminal)


@ROOT_FS
class InstalledCli(tops.CliLifecycle):
    """XC-2, XC-4, XC-5, XC-6 through the installed launcher on the fake plane (the A3.2 CliLifecycle harness; its
    own CL-* cases are not repeated here)."""

    def test_xc2_sigkill_during_the_functional_verification_then_resume(self):
        result, child = self.launch(["purge", "--keep-backups"], ["PURGE " + self.project],
                                    block={"argv_contains": ["pg_restore"], "argv_match": r"-p pfverify-",
                                           "seconds": 300},
                                    signum=tops.signal_module().SIGKILL)
        self.assertEqual(result.returncode, -9, result.stderr)
        self.end_child(child)
        op, plan, journal = pfx.operations_of(self.context, "purge")[-1]
        self.assertEqual(journal["phase"], "verifying")
        result, _ = self.launch(["resume"], ["RESUME " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(pfx.operations_of(self.context, "purge")[-1][2]["phase"], "cancelled")
        state = self.fake.state()
        self.assertFalse([item for item in state["containers"] + state["volumes"] + state["networks"]
                          if "pfverify-" in json.dumps(item)])
        self.assertEqual(sorted(item["labels"][pf_docker.COMPOSE_SERVICE_LABEL] for item in state["containers"]
                                if item["status"] == "running"), ["backend", "db", "frontend"])
        self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "registered")
        evidence("XC-2", {"transcripts": self.transcripts})

    def test_xc4_cleanup_report_through_the_launcher_is_observe_only(self):
        before = sorted(os.listdir(str(self.context.operations_dir)))
        result, _ = self.launch(["cleanup"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Cleanup report of instance staging (read-only; nothing is changed):", result.stdout)
        self.assertIn("Cleanup candidates: none", result.stdout)
        self.assertEqual(sorted(os.listdir(str(self.context.operations_dir))), before)
        result, _ = self.launch(["cleanup", "--apply"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cleanup-nothing:", result.stderr)
        evidence("XC-4", {"transcripts": self.transcripts})

    def test_xc5_acknowledge_then_the_installer_is_allowed(self):
        op = "20261007T030000Z-backup-emergency-9a8b7c6d"
        directory = self.context.operations_dir / op
        directory.mkdir(mode=0o700)
        record = [{"id": "1" * 32, "recorded_at": "20261007T030100Z", "outcome": "timeout", "tool": "docker",
                   "executable": "/usr/bin/docker", "argv": ["compose", "exec", "-T", "db", "pg_restore"],
                   "cwd": "/", "pid": 4242, "process_group": 4242, "returncode": None,
                   "effect": {"kind": "compose-exec", "verb": "pg_restore", "project": self.project,
                              "service": "db", "database": "pf_verify_" + "c" * 20, "targets": []},
                   "label": "docker"}]
        path = directory / "unresolved-effects.json"
        path.write_text(json.dumps(record, indent=2) + "\n")
        os.chmod(path, 0o600)
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.context])
        self.assertIn("instance-effects-unresolved", [item.code for item in report.conflicts])
        result, _ = self.launch(["resume", "--operation", op, "--acknowledge"], ["ACKNOWLEDGE " + op[-8:]])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = pf_install._Report()
        pf_install._instance_journal_checks(report, [self.context])
        self.assertEqual(report.conflicts, [])
        evidence("XC-5", {"transcripts": self.transcripts})

    def test_xc6_a_capacity_refusal_through_the_launcher(self):
        config = self.paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1 << 30)) + "\n")
        result, _ = self.launch(["backup"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("capacity-insufficient:", result.stderr)
        self.assertEqual(pfx.operations_of(self.context, "backup"), [])
        evidence("XC-6", {"transcripts": self.transcripts})


for _name in dir(tops.CliLifecycle):
    if _name.startswith("test"):
        setattr(InstalledCli, _name, None)  # not collected: the CL-* cases run in test_operations


if __name__ == "__main__":
    unittest.main()
