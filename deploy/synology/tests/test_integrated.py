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

    def test_pz16_the_globals_archive_connects_by_database_name_never_by_a_connection_string(self):
        """PF-A3.4 D5 regression (real LOOP-02 step 2, Engine 28.5.1, postgres:16.15): pg_dumpall's ``-d`` is a libpq
        connection string, so ``-d postgres`` failed every real purge ("missing "=" after "postgres" in connection
        info string") after the bundle capture started; the purge closed cancelled and nothing was deleted."""
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("purge")[2]["phase"], "completed")
        calls = [argv[argv.index("pg_dumpall"):] for argv in self.fake.argvs() if "pg_dumpall" in argv]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index("-l") + 1], "postgres")
        self.assertNotIn("-d", calls[0])
        self.assertIn("--globals-only", calls[0])

    def test_pz17_a_failed_verification_createdb_reports_the_server_answer(self):
        """PF-A3.4 D5 regression (real row R29, F-A34-08): a checkpoint verification's ``createdb`` exited 1 on a
        healthy server that has the store's locale, and every createdb failure with a locale was reported as "locale
        … is not available on this server", which hid the real error. The detail now carries the server's answer."""
        answer = 'createdb: error: database creation failed: ERROR:  simulated server answer'
        self.update_state(plane=dict(self.state()["plane"], createdb_failure={"prefix": "pf_verify_",
                                                                             "stderr": answer + "\n"}))
        code, out, err, _ = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"createdb failed: {answer} (requested locale C.UTF-8/C.UTF-8)", err)
        self.assertNotIn("is not available on this server", err)
        self.assertEqual(self.operation("purge")[2]["phase"], "cancelled")
        self.assertTrue([item for item in self.state()["volumes"] if item["name"] == self.project + "_postgres_data"])

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

    def test_pz5_coverage_of_the_final_bundle(self):
        """PZ-5: an extra instance-labelled volume outside the topology is retained (listed, never a candidate, never
        deleted); a foreign ``<project>_``-prefixed volume is never in any plan; a bind mount is listed as an exclusion
        of the final bundle and never deleted. The gate's coverage rule (pure) refuses every uncovered deletion
        candidate, and a coverage problem at the gate is coverage-incomplete: nothing deleted, reopened, cancelled."""
        state = self.state()
        labels = dict(pfx.labels_for(self.context), **{pf_docker.COMPOSE_VOLUME_LABEL: "uploads"})
        state["volumes"].append(pfx.volume(self.project + "_uploads", labels))
        state["volumes"].append(pfx.volume(self.project + "_foreign", {"other": "x"}))
        for item in state["containers"]:
            if item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "backend":
                item["mounts"].append({"Type": "bind", "Source": "/volume1/partflow-uploads", "Destination": "/bind",
                                       "RW": True, "Mode": "", "Propagation": "rprivate"})
        self.fake.write_state(state)
        code, out, err, phrases = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertIn("retained: owned-outside-topology volume " + self.project + "_uploads", out)
        op, plan, journal = self.operation("purge")
        binding = json.loads((self.context.operations_dir / op / "deletion-plan.json").read_bytes())
        keys = [item["key"] for item in binding["candidates"]]
        self.assertEqual([key for key in keys if key.startswith(self.project + "_") and "postgres_data" not in key
                          and "default" not in key], [])
        remaining = [item["name"] for item in self.state()["volumes"]]
        self.assertIn(self.project + "_uploads", remaining)
        self.assertIn(self.project + "_foreign", remaining)
        self.assertFalse([argv for argv in self.fake.argvs() if "/volume1/partflow-uploads" in argv])
        bundle = self.purged_bundle()["name"]
        manifest = json.loads((self.context.paths.recovery / self.project / bundle / "manifest.json").read_bytes())
        rows = {"partflow_staging": {}}
        self.assertEqual(pf_config.purge_coverage(manifest, binding, rows), [])
        volume = next(item for item in binding["candidates"] if item["kind"] == "volume")
        cases = {
            "database outside the bundle": (manifest, binding, dict(rows, other={})),
            "another volume candidate": (manifest, dict(binding, candidates=binding["candidates"] + [
                dict(volume, key=self.project + "_uploads")]), rows),
            "shared volume": (manifest, dict(binding, candidates=[dict(item, users=list(item.get("users") or [])
                                                                        + ["f" * 64]) if item is volume else item
                                                                   for item in binding["candidates"]]), rows),
            "external volume": (manifest, dict(binding, candidates=[
                dict(item, identity=dict(item["identity"], driver="nfs")) if item is volume else item
                for item in binding["candidates"]]), rows),
            "unrecorded bind path": (manifest, dict(binding, bind_paths=list(binding.get("bind_paths") or [])
                                                    + ["/volume1/elsewhere"]), rows)}
        for label, arguments in cases.items():
            with self.subTest(label):
                self.assertTrue(pf_config.purge_coverage(*arguments), label)
        # The gate wiring: a coverage problem refuses the deletion.
        self.tearDown()
        self.setUp()
        with mock.patch.object(pf_config, "purge_coverage", return_value=[("volumes", "a deletion candidate other "
                                                                           "than " + self.project + "_postgres_data")]):
            code, out, err, phrases = self.purge()
        self.assertEqual(code, 1, out + err)
        self.assertIn("coverage-incomplete: volumes: a deletion candidate other than", err)
        journal = self.operation("purge")[2]
        self.assertIsNone(journal["deletion"])
        self.assertEqual(journal["phase"], "cancelled")
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["volume", "rm"]
                          and any(word.startswith(self.project) for word in argv)])
        self.assertIn(self.project + "_postgres_data", [item["name"] for item in self.state()["volumes"]])
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

    def test_tb5_a_crash_between_the_abandon_write_back_and_its_close_is_resumable(self):
        """Audit finding: the restore's abandon writes ``purged`` back, then dies before the cancelled generation; the
        abandon is completed by ``resume --abandon`` (a forward resume refuses, the record is never changed again)."""
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        bundle = self.purged_bundle()["name"]
        with self.crashing(("registry:state=registered", "after-effect")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle)
        original = pf.Controller.close_operation

        def dying(controller, phase, *args, **kwargs):
            if phase == "cancelled" and controller.plan["kind"] == "restore-instance":
                raise pf.SimulatedCrash("power loss before the cancelled generation")
            return original(controller, phase, *args, **kwargs)

        with mock.patch.object(pf.Controller, "close_operation", dying):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("resume", "--abandon")
        record = self.context.record_path.read_bytes()
        self.assertEqual(json.loads(record)["state"], "purged")
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 1, out + err)
        self.assertIn("abandon-in-progress:", err)
        self.assertEqual(self.operation("restore-instance")[2]["phase"], "preparing-target")
        code, out, err, phrases = self.main("resume", "--abandon")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("restore-instance")[2]["phase"], "cancelled")
        self.assertEqual(self.context.record_path.read_bytes(), record)  # written once, not again
        # Another change of the record after the write-back still refuses the abandon.
        self.tearDown()
        self.setUp()
        code, out, err, _ = self.purge()
        bundle = self.purged_bundle()["name"]
        with self.crashing(("registry:state=registered", "after-effect")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle)
        with mock.patch.object(pf.Controller, "close_operation", dying):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("resume", "--abandon")
        changed = json.loads(self.context.record_path.read_bytes())
        changed["approved_config_revision"] += 1
        os.chmod(self.context.record_path, 0o600)
        self.context.record_path.write_bytes(pf_instance.normalize_json(changed))
        code, out, err, _ = self.main("resume", "--abandon")
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
        # Audit finding (section 3.17): the throwaway password files of the topology that never ran are gone.
        op = self.operation("purge")[0]
        self.assertEqual(self.topology_files(op), [])
        topology = next((self.context.operations_dir / op / "isolated").iterdir())
        self.assertEqual(json.loads((topology / "topology.json").read_bytes())["state"], "removed")

    def topology_files(self, op):
        return sorted(str(path.relative_to(self.context.operations_dir)) for path in
                      (self.context.operations_dir / op / "isolated").rglob("*")
                      if path.name in ("app.env", "compose.json"))

    def test_it_a_crash_between_compose_json_and_topology_json_is_recoverable(self):
        """Audit finding: compose.json written, topology.json not: the next run discards both files and writes new
        ones (never a raw FileExistsError); an abandon leaves no throwaway password file."""
        bundle = SideBySide.bundle(self)
        original = pf.Controller.write_topology_record
        calls = {"n": 0}

        def dying(controller, project, record):
            if record.get("state") == "created" and calls["n"] == 0:
                calls["n"] += 1
                raise pf.SimulatedCrash("before topology.json")
            return original(controller, project, record)

        for action in ("resume", "abandon"):
            with self.subTest(action):
                if action == "abandon":
                    self.tearDown()
                    self.setUp()
                    bundle = SideBySide.bundle(self)
                    calls["n"] = 0
                with mock.patch.object(pf.Controller, "write_topology_record", dying):
                    with self.assertRaises(pf.SimulatedCrash):
                        self.main("restore-instance", bundle, "--side-by-side")
                op = self.operation("restore-side-by-side")[0]
                self.assertEqual([name.rsplit("/", 1)[-1] for name in self.topology_files(op)],
                                 ["app.env", "compose.json"])
                temporary = self.context.operations_dir / op / "isolation-preflight-killed"
                temporary.mkdir(mode=0o700)
                (temporary / "app.env").write_text("POSTGRES_PASSWORD=left-by-a-killed-process\n")
                if action == "resume":
                    code, out, err, _ = self.main("resume")
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "completed")
                else:
                    code, out, err, _ = self.main("resume", "--abandon")
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(self.operation("restore-side-by-side")[2]["phase"], "cancelled")
                    self.assertEqual(self.topology_files(op), [])
                self.assertFalse(temporary.exists())

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

    def test_fv3_a_refused_teardown_after_passed_checks_never_reaches_the_deletion(self):
        """FV-3: every check passed, then the teardown is refused (a foreign container holds the topology volume): the
        record is passed with removed false, the verification effect fails isolated-topology-kept, the instance is
        reopened, the purge closes cancelled, and no second-stage (deletion) approval is ever asked."""
        harness = self

        def seam(label, when):
            if when == "inside" and label == pf.INSIDE_PREFIX + "before-teardown":
                state = harness.state()
                project = next(item["name"] for item in state["volumes"] if item["name"].startswith("pfverify-"))
                state["containers"].append(pfx.container("f" * 64, "foreign-user", {"other": "x"},
                                                         volumes=[project], status="exited"))
                harness.fake.write_state(state)

        with self.crashing(seam):
            code, out, err, phrases = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.assertIn("isolated-topology-kept", out + err)
        op, plan, journal = self.operation("purge")
        self.assertEqual(journal["phase"], "cancelled")
        self.assertIsNone(journal["deletion"])
        self.assertFalse([phrase for phrase in phrases if phrase.startswith(("ERASE ", "DELETE ", "RESET "))], phrases)
        bundle = next(item for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")
        record = [item for item in self.records(bundle["name"]) if item["level"] == "functional_recovery_verified"][0]
        self.assertEqual((record["result"], record["target"]["removed"]), ("passed", False))
        self.assertIn("isolated-topology", [item["kind"] for item in journal["retained_artifacts"]])
        verification = next(e for e in plan["effects"] if e["target"] == "bundle:purge")
        self.assertNotEqual(pf_config.effect_state(journal, verification["effect_id"]), "complete")
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["compose"] and "down" in argv
                          and self.project in argv])
        self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "registered")
        for container in self.state()["containers"]:
            if container["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project:
                self.assertEqual(container["status"], "running", container["name"])

    FV7_SEAMS = ("topology", "data", "store", "payloads", "runtime", "before-teardown", "teardown", "teardown-item")

    def test_fv7_a_crash_at_every_verification_step_leaves_no_record_and_resumes_by_teardown(self):
        """FV-7: a crash at every step of the functional verification (``inside:<step>``) leaves no functional record
        and the topology recorded (topology.json); ``pf resume`` tears it down by its exact (frozen when present)
        plan, reopens the unchanged application and closes the instance purge cancelled (section 3.12)."""
        rows = []
        for label in self.FV7_SEAMS:
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                with self.crashing((label, "inside")):
                    with self.assertRaises(pf.SimulatedCrash):
                        self.main("purge", "--keep-backups")
                op, plan, journal = self.operation("purge")
                self.assertEqual(journal["phase"], "verifying")
                bundle = self.purged_bundle()["name"]
                functional = [item for item in self.records(bundle) if item["level"] == "functional_recovery_verified"]
                self.assertEqual(functional, [])
                verification = next(e for e in plan["effects"] if e["target"] == "bundle:purge")
                project = verification["preconditions"][0].split(":", 1)[1]
                directory = self.context.operations_dir / op / "isolated" / project
                record = json.loads((directory / "topology.json").read_bytes())
                self.assertEqual(record["project"], project)
                code, out, err, _ = self.main("resume")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.operation("purge")[2]["phase"], "cancelled")
                self.assertEqual(self.isolated_resources(project), {"containers": [], "volumes": [], "networks": []})
                self.assertEqual(json.loads((directory / "topology.json").read_bytes())["state"], "removed")
                self.assertEqual(self.topology_files(op), [])
                self.assertEqual([item for item in self.records(bundle)
                                  if item["level"] == "functional_recovery_verified"], [])
                running = sorted(item["labels"][pf_docker.COMPOSE_SERVICE_LABEL] for item in self.state()["containers"]
                                 if item["status"] == "running"
                                 and item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project)
                self.assertEqual(running, ["backend", "db", "frontend"])
                self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "registered")
                rows.append({"kind": "purge", "effect": "verification bundle:purge", "injection": "inside:" + label,
                             "state_left": {"phase": "verifying", "topology": record["state"],
                                            "teardowns": [entry["state"] for entry in record["teardowns"]]},
                             "action": "resume", "outcome": "cancelled"})
        evidence("FUNCTIONAL-1-FV-7", {"rows": rows})

    def test_fv8_stores_in_manifest_order_and_the_non_connectable_flag_after_its_checks(self):
        """FV-8: every store is restored into the isolated server in manifest order; a non-connectable store gets
        ALLOW_CONNECTIONS false in the isolated server right after its own checks, before the next store."""
        state = self.state()
        state["plane"]["databases"]["partflow_archive"] = {
            "heads": [], "rows": {"public.archived": 2}, "allow": False, "owner": "partflow_staging",
            "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
        self.fake.write_state(state)
        observed = []
        harness = self

        def seam(label, when):
            if when == "inside" and label == pf.INSIDE_PREFIX + "store":
                isolated = harness.state().get("isolated") or {}
                plane = next(value for key, value in isolated.items() if key.startswith("pfverify-"))
                observed.append({name: item["allow"] for name, item in plane["databases"].items()})

        with self.crashing(seam):
            code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 0, out + err)
        bundle = self.purged_bundle()["name"]
        manifest = json.loads((self.context.paths.recovery / self.project / bundle / "manifest.json").read_bytes())
        names = [store["database"] for store in manifest["stores"]]
        self.assertEqual(sorted(names), ["partflow_archive", "partflow_staging"])
        restores = [argv[argv.index("-d") + 1] for argv in self.fake.argvs() if "pg_restore" in argv
                    and "--list" not in argv and "-p" in argv and argv[argv.index("-p") + 1].startswith("pfverify-")]
        self.assertEqual(restores, names)
        self.assertEqual(len(observed), len(names))
        position = names.index("partflow_archive")
        self.assertIs(observed[position]["partflow_archive"], False)  # closed right after its own checks
        for earlier in observed[:position]:
            self.assertNotIn("partflow_archive", earlier)  # not restored before its turn
        record = [item for item in self.records(bundle) if item["level"] == "functional_recovery_verified"][0]
        order = [check["name"].split(":", 1)[1] for check in record["checks"] if check["name"].startswith("restore:")]
        self.assertEqual(order, ["postgresql:" + name for name in names])
        self.assertEqual(record["result"], "passed")

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

    def test_pz6_a_replaced_volume_drifts_the_frozen_deletion_on_resume(self):
        """PZ-6 (A3-T07): the instance purge stops in deleting; meanwhile the planned data volume is replaced (another
        CreatedAt). ``pf resume`` refuses plan-drift before any removal: no item is added or removed, the bundle,
        the frozen plan and the lock file are unchanged, and the operation stays open in deleting."""
        with self.crashing(("deletion-plan", "after-intent")):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("purge", "--keep-backups")
        op, plan, journal = self.operation("purge")
        self.assertEqual(journal["phase"], "deleting")
        directory = self.context.operations_dir / op
        frozen = (directory / "deletion-plan.json").read_bytes()
        progress = directory / "deletion-progress.json"
        progress_before = progress.read_bytes() if progress.exists() else None
        bundle = self.purged_bundle()["name"]
        bundle_before = pfx.snapshot_tree(self.context.paths.recovery / self.project / bundle)
        lock = self.context.lock_path
        lock_inode = os.stat(str(lock)).st_ino
        state = self.state()
        for volume in state["volumes"]:
            if volume["name"] == self.project + "_postgres_data":
                volume["created_at"] = "2026-10-08T09:09:09Z"
        self.fake.write_state(state)
        volumes = sorted(item["name"] for item in state["volumes"])
        containers = sorted(item["id"] for item in state["containers"])
        self.fake.clear_calls()
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 1, out + err)
        self.assertIn("plan-drift: Planned volume " + self.project + "_postgres_data changed after the plan was frozen",
                      out + err)
        state = self.state()
        self.assertEqual(sorted(item["name"] for item in state["volumes"]), volumes)
        self.assertEqual(sorted(item["id"] for item in state["containers"]), containers)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["rm"]
                          or argv[:2] in (["volume", "rm"], ["network", "rm"], ["image", "rm"])])
        self.assertEqual((directory / "deletion-plan.json").read_bytes(), frozen)
        self.assertEqual(progress.read_bytes() if progress.exists() else None, progress_before)
        self.assertEqual(pfx.snapshot_tree(self.context.paths.recovery / self.project / bundle), bundle_before)
        self.assertEqual(os.stat(str(lock)).st_ino, lock_inode)
        self.assertEqual(self.operation("purge")[2]["phase"], "deleting")
        evidence("PURGE-GATE-1-PZ-6", {"refusal": (out + err).strip().splitlines()[-1]})

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

    def test_rx10_retained_candidate_named_databases_are_restored_from_the_bundle(self):
        """PF-A3.4 D5 regression (real row R33, Engine 28.5.1): a purge bundle retains every database of the instance,
        including a leftover ``pf_restore_*``/``pf_migrate_*`` candidate of an interrupted rollback or update. The
        restore routed those names to the rollback/update branches (``op_selected``) and every attempt ended
        ``plan-input-changed: checkpoint purge-… no longer reads or verifies (unreadable)``: a dead end with the instance
        purged. They are restored from the bundle's own stores like every other retained database."""
        leftovers = ("pf_restore_" + "c" * 20, "pf_migrate_" + "d" * 20)
        state = self.state()
        for name in leftovers:
            state["plane"]["databases"][name] = {"heads": [], "rows": {}, "allow": True, "owner": "partflow_staging",
                                                 "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
        self.fake.write_state(state)
        bundle = self.purged()
        manifest = json.loads((self.context.paths.recovery / self.project / bundle / "manifest.json").read_bytes())
        self.assertTrue(set(leftovers) <= {store["database"] for store in manifest["stores"]})
        code, out, err, _ = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("plan-input-changed", err)
        self.assertEqual(self.operation("restore-instance")[2]["phase"], "completed")
        self.assertTrue(set(leftovers) <= set(self.state()["plane"]["databases"]))

    def test_rx11_a_fresh_volume_waits_for_the_final_server_before_the_first_database_step(self):
        """PF-A3.4 D5 regression (real row R31, F-A34-07, postgres:16.15): on a fresh volume the image's temporary
        initialization server passed the health check (Unix socket) and was shutting down two seconds later; the
        restore's first database step met "the database system is shutting down" and stopped at restoring-data. The
        readiness wait now asks the final server over TCP (the temporary one never listens on TCP)."""
        bundle = self.purged()
        self.update_state(plane=dict(self.state()["plane"], init_server_answers=1))
        self.fake.clear_calls()
        with mock.patch.object(pf.time, "sleep"):
            code, out, err, _ = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("shutting down", err)
        self.assertEqual(self.operation("restore-instance")[2]["phase"], "completed")
        calls = self.fake.argvs()
        up = next(index for index, argv in enumerate(calls) if argv[:1] == ["compose"] and "up" in argv
                  and argv[-1] == "db")
        probes = [index for index, argv in enumerate(calls) if index > up and "pg_isready" in argv and "-h" in argv]
        first = next(index for index, argv in enumerate(calls) if index > up and argv[:1] == ["compose"]
                     and "exec" in argv and "pg_isready" not in argv)
        self.assertGreaterEqual(len(probes), 2)  # the first probe met the initialization server
        self.assertLess(probes[-1], first)

    def test_rx12_a_refused_restore_of_a_purged_instance_leaves_no_state_directory(self):
        """PF-A3.4 D5 regression (real C-A3-14 (b), F-A34-06): a refused restore-instance of a purged instance created
        the instance's empty private state/ before refusing; a refusal now leaves the tree as it found it."""
        bundle = self.purged()
        self.assertFalse(os.path.lexists(str(self.context.state_dir)))
        self.rewrite(bundle, lambda m: m["source_instance"].update(instance_id="00000000-0000-4000-8000-0000000000aa"))
        code, out, err, phrases = self.main("restore-instance", bundle)
        self.assertEqual(code, 1, out + err)
        self.assertIn("restore-target-mismatch:", err)
        self.assertFalse(os.path.lexists(str(self.context.state_dir)))
        # A state directory that holds anything is never removed.
        os.mkdir(str(self.context.state_dir), 0o700)
        os.chmod(str(self.context.state_dir), 0o700)
        (self.context.state_dir / "keep").write_text("x")
        os.chmod(str(self.context.state_dir / "keep"), 0o600)
        code, out, err, phrases = self.main("restore-instance", bundle)
        self.assertEqual(code, 1, out + err)
        self.assertTrue((self.context.state_dir / "keep").is_file())

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

    def test_rx9_a_legacy_bundle_without_a_db_image_uses_the_local_postgres_with_the_major_check(self):
        """RX-9: a legacy bundle records no database image: no image-tag effect, the note db-image-unrecorded names the
        local postgres:16, and the PostgreSQL major is checked after the db service starts (logical restore)."""
        self.purged()
        state = self.state()
        contract = {"files": {"alembic.ini": "0" * 64}, "heads": ["r1"]}
        state.setdefault("saved_contracts", {})[pfx.image_id("old-backend")] = contract  # loaded with images.tar
        self.fake.write_state(state)
        bundle = legacy_bundle(self)
        self.fake.clear_calls()
        code, out, err, phrases = self.main("restore-instance", bundle)
        self.assertEqual(code, 0, out + err)
        self.assertIn("note: db-image-unrecorded: the legacy bundle recorded no database image; local postgres:16 "
                      + pfx.DB_IMAGE_ID[7:19] + " is used (logical restore)", out)
        op, plan, journal = self.operation("restore-instance")
        self.assertEqual(journal["phase"], "completed")
        self.assertNotIn("image-tag", [effect["type"] for effect in plan["effects"]])
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["image", "tag"] or argv[:1] == ["tag"]])
        calls = self.fake.argvs()
        db_up = next(index for index, argv in enumerate(calls) if argv[:1] == ["compose"] and "up" in argv
                     and argv[-1] == "db")
        majors = [index for index, argv in enumerate(calls) if "SHOW server_version_num;" in argv]
        self.assertTrue([index for index in majors if index > db_up], (db_up, majors))
        tagged = [image["id"] for image in self.state()["images"] if pf_docker.DB_IMAGE in image["repo_tags"]]
        self.assertEqual(tagged, [pfx.DB_IMAGE_ID])

    def test_rx4_an_occupied_target_is_refused_before_any_confirmation(self):
        """RX-4: an owned volume of the instance, or an existing deployment record, refuses before any confirmation;
        no operation is opened and nothing is loaded or written."""
        bundle = self.purged()
        labels = dict(pfx.labels_for(self.context), **{pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"})
        for label, prepare, wanted in (
                ("owned volume", lambda: self.update_state(volumes=self.state()["volumes"] + [
                    pfx.volume(self.project + "_postgres_data", labels)]),
                 f"resource-target-not-empty: restore-instance requires an empty target: volume "
                 f"{self.project}_postgres_data"),
                ("deployment record", lambda: pf.write_json(self.context.state_dir / "deployed.json",
                                                            {"sha": pfx.OLD}),
                 "A managed deployment record already exists. Exact restore refuses to overwrite it.")):
            with self.subTest(label):
                saved = self.state()
                self.context.state_dir.mkdir(mode=0o700, exist_ok=True)
                prepare()
                before = pfx.operations_bytes(self.context)
                env_path = self.paths["configuration"] / ".env"
                env = env_path.read_bytes() if env_path.exists() else None
                self.fake.clear_calls()
                code, out, err, phrases = self.main("restore-instance", bundle)
                self.assertEqual(code, 1, out + err)
                self.assertIn(wanted, err)
                self.assertEqual(phrases, [])
                self.assertEqual(pfx.operations_bytes(self.context), before)
                self.assertEqual(env_path.read_bytes() if env_path.exists() else None, env)
                self.assertFalse([argv for argv in self.fake.argvs() if pf.unclassified_mutation("docker", argv)])
                self.fake.write_state(saved)
                pointer = self.context.state_dir / "deployed.json"
                if label == "deployment record":
                    pointer.unlink()

    def restore_with(self, bundle, script, *, crash=None):
        self.update_state(reconcile={self.project: script})
        if crash is None:
            return self.main("restore-instance", bundle)
        with self.crashing(crash):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("restore-instance", bundle)
        return None

    def test_rx6_the_activated_invariants_are_evidence_inside_the_frontend_effect(self):
        """RX-6: the restore runs the application invariant check once inside the frontend effect; every outcome is
        an evidence line and the frontend starts. A crash after app-check-activated.json is written never re-runs it;
        a crash before it re-runs it once."""
        cases = (("clean", {}, "clean", None),
                 ("mismatch equal", {"report": "mismatch:c=2"}, "mismatch equal to the bundle's verification",
                  {"*": {"report": "mismatch:c=2"}}),
                 ("mismatch", {"report": "mismatch:c=2"}, "mismatch (an incident, RUNBOOK §8)", None),
                 ("could not run", {"report": "garbage"}, "could not run", None),
                 ("unavailable", {"probe": 3}, "unavailable", None))
        for label, script, line, purge_script in cases:
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                if purge_script is not None:
                    self.update_state(reconcile=purge_script)
                bundle = self.purged()
                code, out, err, _ = self.restore_with(bundle, script)
                self.assertEqual(code, 0, out + err)
                self.assertIn("Application invariants: " + line, out)
                op, plan, journal = self.operation("restore-instance")
                self.assertEqual(journal["phase"], "completed")
                check = json.loads((self.context.operations_dir / op / "app-check-activated.json").read_bytes())
                self.assertEqual(check["label"], "activated")
                self.assertNotIn("PN-SECRET-4711", json.dumps(check))
                running = sorted(item["labels"][pf_docker.COMPOSE_SERVICE_LABEL] for item in self.state()["containers"]
                                 if item["status"] == "running"
                                 and item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project)
                self.assertEqual(running, ["backend", "db", "frontend"])
        for when, runs in (("after", 0), ("before", 1)):
            with self.subTest(crash=when):
                self.tearDown()
                self.setUp()
                bundle = self.purged()
                if when == "before":
                    def crash(effect_id, point):
                        found = pfx.operations_of(self.context, "restore-instance")
                        effect = next((item for item in found[-1][1]["effects"] if item["effect_id"] == effect_id),
                                      None) if found else None
                        if point == "after-intent" and effect is not None \
                                and effect["target"].startswith("service:frontend:start"):
                            raise pf.SimulatedCrash("after-intent " + effect_id)

                    self.restore_with(bundle, {}, crash=crash)
                else:
                    def dying(controller, images):
                        raise pf.SimulatedCrash("after app-check-activated.json")

                    with mock.patch.object(pf.Controller, "activate_frontend", dying):
                        with self.assertRaises(pf.SimulatedCrash):
                            self.main("restore-instance", bundle)
                op = self.operation("restore-instance")[0]
                self.assertEqual((self.context.operations_dir / op / "app-check-activated.json").exists(),
                                 when == "after")
                self.fake.clear_calls()
                code, out, err, _ = self.main("resume")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.operation("restore-instance")[2]["phase"], "completed")
                self.assertEqual(len([argv for argv in self.fake.argvs() if "app.cli" in argv]), runs)
                self.assertTrue((self.context.operations_dir / op / "app-check-activated.json").exists())

    def test_rx7_the_docker_root_need_counts_the_image_archive_when_an_image_is_absent(self):
        """RX-7: with a bundle image absent, images.tar is part of the preparing-target Docker-root need, summed with
        the workspace, private-state and artifact needs when they share one device (one floor)."""
        bundle = self.purged()
        controller = self.controller()
        controller.staging()
        view = controller.verify_recovery(self.context.paths.recovery / self.project / bundle)
        size = view.payload("images.tar")["size"]
        self.assertGreater(size, 0)
        without = controller.capacity_needs("restore-instance", view=view, load=False)
        loaded = controller.capacity_needs("restore-instance", view=view, load=True)
        self.assertEqual({(phase, role) for phase, role, _, _ in loaded},
                         {("preparing-target", "docker-root"), ("preparing-target", "private-state"),
                          ("preparing-target", "artifacts"), ("preparing-target", "workspace")})
        self.assertEqual(sum(item[3] for item in loaded) - sum(item[3] for item in without), size)
        floor = 1024 * 1024  # minimum_free_mb 1
        free = sum(item[3] for item in without) + floor + size // 2  # fits without the archive, not with it
        state = self.state()  # every bundle image present (the fake daemon's own load of images.tar)
        self.assertEqual(pfx.FAKE_DOCKER.plane_load(state, str(view.folder / "images.tar")).code, 0)
        self.fake.write_state(state)
        backend = pfx.topology_image_id("a", "backend")
        present = copy.deepcopy(self.state()["images"])
        for absent in (False, True):
            with self.subTest(absent=absent):
                state = self.state()
                state["images"] = [image for image in present if not absent or image["id"] != backend]
                self.fake.write_state(state)
                before = pfx.operations_bytes(self.context)

                def decline(phrase):
                    raise pf.Failure("declined at the confirmation")

                self.fake.clear_calls()
                with mock.patch.object(pf.Controller, "measure", lambda c, path, role: (7, free)):
                    code, out, err, phrases = self.main("restore-instance", bundle, confirm=decline)
                self.assertEqual(code, 1, out + err)
                self.assertEqual(phrases, [] if absent else phrases[:1])
                if absent:
                    self.assertIn("capacity-insufficient: preparing-target needs", err)
                    line = err.split("capacity-insufficient:", 1)[1].split("\n", 1)[0]
                    self.assertEqual(line.count(" on device "), 1, line)
                    for role in ("docker-root", "workspace", "private-state", "artifacts"):
                        self.assertIn(role, line)
                else:
                    self.assertIn("declined at the confirmation", err)  # the capacity preflight passed
                self.assertEqual(pfx.operations_bytes(self.context), before)
                self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["image", "load"]])


def legacy_images_tar(project, services=("backend", "frontend")):
    """The bytes of a legacy purge bundle's images.tar (pfx.legacy_purge_bundle's ``-backup-legacy`` references of
    the ``old-<service>`` fixture images) written by the fake daemon's own ``image save`` with real config and layer
    hashes, so its archive proof and its load are real."""
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "images.tar"
        entries = []
        for service in services:
            image_id = pfx.image_id("old-" + service)
            config, layer = pfx.IMAGE_CONFIGS[image_id]
            entries.append((f"{project}-{service}:backup-legacy", {"id": image_id, "config": config, "layer": layer}))
        pfx.FAKE_DOCKER.write_image_archive({}, str(path), entries)
        return path.read_bytes()


def legacy_bundle(case, *, fmt=2, extra=None, name=None):
    """A legacy (format 1/2) purge bundle of ``case``'s instance in its recovery folder, with a real images.tar."""
    tree = case.base / ("legacy-tree-%d" % fmt)
    if not tree.exists():
        pfx.source_fixture(tree)
    bundle = name or "purge-20261006T000000Z-" + pfx.OLD[:12] + "-abcd%02d" % fmt
    pfx.legacy_purge_bundle(case.context.paths.recovery / case.project / bundle, project=case.project,
                            root=case.paths["workspace"], tree=tree, fmt=fmt, extra=extra,
                            files={"images.tar": legacy_images_tar(case.project)})
    return bundle


class SideBySide(Plane):
    """SB-1..SB-11: the side-by-side recovery target beside the running instance."""

    def test_sb8_legacy_bundles_follow_the_legacy_rules(self):
        """SB-8: a legacy purge bundle (format 2 with config_env; format 1 with its config_env exclusion) records no
        database image: the db runs as the local postgres:16 ID resolved at plan time (precondition db-image:<id>),
        images:archive proves backend/frontend and names the excluded db image, config:bundle passes (format 2) or is
        not_run 'unavailable: legacy format 1 excludes config_env' with the instance's values (format 1),
        deployment:record is not_run 'excluded:', heads:runtime compares the recorded live heads. A legacy bundle
        without a backend image is refused before the confirmation (by the strict reader's rule 4, which precedes the
        section 3.6 verification-isolation-unsupported rule)."""
        rows = []
        for fmt in (2, 1):
            with self.subTest(fmt=fmt):
                self.tearDown()
                self.setUp()
                bundle = legacy_bundle(self, fmt=fmt)
                code, out, err, phrases = self.main("restore-instance", bundle, "--side-by-side")
                self.assertEqual(code, 0, out + err)
                self.assertEqual(phrases, ["RESTORE COPY " + bundle])
                op, plan, journal = self.operation("restore-side-by-side")
                self.assertEqual(journal["phase"], "completed")
                restore = next(e for e in plan["effects"] if e["type"] == "database-restore")
                self.assertIn("db-image:" + pfx.DB_IMAGE_ID, restore["preconditions"])
                self.assertIn("image-load", [e["type"] for e in plan["effects"]])  # the old images were absent
                record = [item for item in self.records(bundle) if item["operation_id"] == op][0]
                self.assertEqual((record["level"], record["result"]), ("functional_recovery_verified", "passed"))
                checks = {check["name"]: check for check in record["checks"]}
                self.assertEqual(checks["images:archive"]["result"], "passed")
                self.assertIn("db image not recorded", checks["images:archive"]["detail"])
                if fmt == 2:
                    self.assertEqual(checks["config:bundle"]["result"], "passed")
                else:
                    self.assertEqual(checks["config:bundle"]["result"], "not_run")
                    self.assertTrue(checks["config:bundle"]["detail"].startswith(
                        "unavailable: legacy format 1 excludes config_env"), checks["config:bundle"])
                self.assertEqual(checks["deployment:record"]["result"], "not_run")
                self.assertTrue(checks["deployment:record"]["detail"].startswith("excluded:"))
                self.assertEqual(checks["heads:runtime"]["result"], "passed")
                running = [item for item in self.state()["containers"]
                           if item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL, "").startswith("pfrecover-")
                           and item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "db"]
                self.assertEqual([item["image"] for item in running], [pfx.DB_IMAGE_ID])
                rows.append({"format": fmt, "checks": {name: [check["result"], check["detail"]]
                                                       for name, check in checks.items()
                                                       if name in ("images:archive", "config:bundle",
                                                                   "deployment:record", "heads:runtime")}})
        self.tearDown()
        self.setUp()
        frontend = {"reference": f"{self.project}-frontend:backup-legacy", "id": pfx.image_id("old-frontend")}
        bundle = legacy_bundle(self, extra={"active_images": {"frontend": frontend},
                                            "saved_image_refs": [frontend["reference"]]})
        code, out, err, phrases = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 1, out + err)
        # The strict reader already refuses a legacy purge bundle without a backend image (its rule 4), before the
        # side-by-side rule (verification-isolation-unsupported) could be reached: refused, nothing asked or written.
        self.assertIn(f"manifest-schema-unsupported: {bundle}: legacy manifest cannot be migrated (healthy_checkpoint: "
                      "images.backend and images.frontend are required (rule 4)). Nothing was changed.", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.operations_of(self.context, "restore-side-by-side"), [])
        evidence("FUNCTIONAL-1-SB-8", {"rows": rows})

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

    def test_sb6_absent_images_are_loaded_without_touching_the_live_override(self):
        """SB-6 (audit finding, R2-13): the side-by-side image-load is load-only. The live instance meanwhile runs other
        images, so its override differs from the bundle: it stays byte-identical and no existing tag is re-pointed."""
        bundle = self.bundle()
        state = self.state()
        backend = pfx.topology_image_id("a", "backend")
        state["images"] = [image for image in state["images"] if image["id"] != backend]
        self.fake.write_state(state)
        override = self.context.state_dir / "active-images.yaml"
        override.write_bytes(override.read_bytes().replace(b"backup-", b"livenew-"))
        before = self.live_hashes()
        code, out, err, phrases = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("restore-side-by-side")
        self.assertEqual(journal["phase"], "completed")
        load = [effect for effect in plan["effects"] if effect["type"] == "image-load"]
        self.assertEqual([effect["target"] for effect in load], ["images:" + bundle])
        self.assertEqual(len([argv for argv in self.fake.argvs() if argv[:2] == ["image", "load"]]), 1)
        self.assertTrue(self.image_present(backend))
        after = self.live_hashes()
        self.assertEqual(after["override"], before["override"])
        for key in ("env", "pointer", "databases", "workspace", "containers"):
            self.assertEqual(after[key], before[key], key)
        # The load may add the bundle's own tags back; no tag that existed before names another image now.
        self.assertEqual(sorted(set(before["tags"]) - set(after["tags"])), [])
        loaded = next(item for item in journal["effects"] if item["effect_id"] == load[0]["effect_id"])
        self.assertEqual(loaded["state"], "complete")
        self.assertIn(backend[7:19], loaded["evidence"])

    def image_present(self, image_id):
        return any(image["id"] == image_id for image in self.state()["images"])

    def test_sb5_no_recovery_database_and_an_existing_one_is_only_reported(self):
        """SB-5: the former mode (pf_recovery_* restored into the live server) is gone: a side-by-side restore creates
        no such database; an existing one is reported by status and cleanup and never dropped, also when the
        recovery target is removed."""
        bundle = self.bundle()
        legacy = "pf_recovery_20261001t000000z_abcdef"
        state = self.state()
        state["plane"]["databases"][legacy] = {"heads": ["r1"], "rows": {"public.part": 1}, "allow": True,
                                               "owner": "partflow_staging", "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
        self.fake.write_state(state)
        code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        project = next(item["name"] for item in self.operation("restore-side-by-side")[2]["retained_artifacts"]
                       if item["kind"] == "recovery-target")
        databases = self.state()["plane"]["databases"]
        self.assertEqual([name for name in databases if name.startswith("pf_recovery_")], [legacy])
        touching = [argv for argv in self.fake.argvs() if any("pf_recovery_" in str(word) for word in argv)
                    and not any(word.startswith("SELECT ") for word in argv)]
        self.assertEqual(touching, [])  # never created, restored into, dropped or altered
        code, out, err, _ = self.main("status")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"note: legacy-recovery-database: {legacy} (pf_recovery_*) is kept; it is user data.", out)
        code, out, err, _ = self.main("cleanup")
        self.assertIn(f"report-only (never removed): legacy-recovery-database {legacy}", out)
        code, out, err, phrases = self.main("cleanup", "--apply", "--recovery-target", project)
        self.assertEqual(code, 0, out + err)
        self.assertIn(legacy, self.state()["plane"]["databases"])
        self.assertFalse([argv for argv in self.fake.argvs() if "dropdb" in argv and legacy in argv])

    def test_sb7_a_mismatch_passes_only_when_equal_to_the_bundle_oracle(self):
        """SB-7: no live source: clean passes; a mismatch passes only when it equals the app-invariants summary of a
        passed functional record of the same manifest (the bundle's instance purge oracle), else it fails."""
        self.update_state(reconcile={"*": {"report": "mismatch:c=2"}})
        bundle = self.bundle()  # the instance purge recorded the oracle c:mismatch:2
        code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("restore-side-by-side")
        record = [item for item in self.records(bundle) if item["operation_id"] == op][0]
        app = next(check for check in record["checks"] if check["name"] == "app-invariants")
        self.assertEqual(app["result"], "passed")
        self.assertIn("c:mismatch:2", app["detail"])
        project = next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "recovery-target")
        code, out, err, _ = self.main("cleanup", "--apply", "--recovery-target", project)
        self.assertEqual(code, 0, out + err)
        # Another mismatch than the recorded oracle fails the target (failed_preserved, kept for inspection).
        self.update_state(reconcile={"*": {"report": "mismatch:c=3"}})
        first = op
        code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 1, out + err)
        self.assertIn("functional-verification-failed:", err)
        self.assertIn("app-invariants", err)
        # Two operations may share their creation second: select the new one by ID, never by sort order.
        op, plan, journal = next(item for item in pfx.operations_of(self.context, "restore-side-by-side")
                                 if item[0] != first)
        self.assertEqual(journal["phase"], "failed_preserved")
        record = [item for item in self.records(bundle) if item["operation_id"] == op][0]
        app = next(check for check in record["checks"] if check["name"] == "app-invariants")
        self.assertEqual(app["result"], "failed")
        self.assertNotIn("PN-SECRET-4711", out + err)

    def test_sb9_a_foreign_bundle_is_refused_and_nothing_is_loaded(self):
        """SB-9: another instance's bundle (other UUID) and a legacy bundle of another project are refused
        (restore-target-mismatch) before any confirmation; no image is loaded and no operation is opened."""
        bundle = self.bundle()
        state = self.state()
        backend = pfx.topology_image_id("a", "backend")
        state["images"] = [image for image in state["images"] if image["id"] != backend]  # a load would be needed
        self.fake.write_state(state)
        RestoreTarget.rewrite(self, bundle, lambda m: m["source_instance"].update(
            instance_id="00000000-0000-4000-8000-0000000000aa"))
        tree = self.base / "legacy-tree"
        pfx.source_fixture(tree)
        legacy = "purge-20261006T000000Z-" + pfx.OLD[:12] + "-abcdef"
        pfx.legacy_purge_bundle(self.context.paths.recovery / self.project / legacy, project="partflow-other",
                                root=self.paths["workspace"], tree=tree)
        before = pfx.operations_bytes(self.context)
        for selected, owner in ((bundle, "instance 00000000"), (legacy, "project partflow-other")):
            with self.subTest(selected):
                code, out, err, phrases = self.main("restore-instance", selected, "--side-by-side")
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"restore-target-mismatch: bundle {selected} belongs to {owner}", err)
                self.assertIn("Nothing was changed.", err)
                self.assertEqual(phrases, [])
        self.assertEqual(pfx.operations_bytes(self.context), before)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["image", "load"]])
        self.assertFalse(self.image_present(backend))
        self.assertEqual(self.isolated_resources("pfrecover-"), {"containers": [], "volumes": [], "networks": []})

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

    def test_cu13_no_dead_end(self):
        """CU-13 (subset): a per-item failure keeps that item and the next is processed (failed_preserved): an OSError
        on a folder removal, a drop the daemon reports done while the database stays (plan-effect-unconfirmed). An
        unreachable daemon mid-deleting leaves the cleanup open: resume continues it; --abandon closes it
        failed_preserved listing what was not removed."""
        original_remove = pf_instance.remove_private_tree_at

        def failing_remove(fd, name, *args, **kwargs):
            if name == pfx.CHECKPOINT_ID:
                raise OSError(5, "Input/output error")
            return original_remove(fd, name, *args, **kwargs)

        name, attempt = self.closed_backup_with_leftover()
        with mock.patch.object(pf_instance, "remove_private_tree_at", failing_remove):
            code, out, err, _ = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"cleanup-item-failed: remove:bundle-attempt:{pfx.CHECKPOINT_ID}: os-error", out)
        self.assertTrue(attempt.exists())
        self.assertNotIn(name, self.state()["plane"]["databases"])
        self.assertEqual(self.operation("cleanup")[2]["phase"], "failed_preserved")

        self.tearDown()
        self.setUp()
        name, attempt = self.closed_backup_with_leftover()
        with mock.patch.object(pf.Controller, "drop_database", lambda controller, database: None):
            code, out, err, _ = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"cleanup-item-failed: database:{name}: plan-effect-unconfirmed", out)
        self.assertIn(name, self.state()["plane"]["databases"])
        self.assertFalse(attempt.exists())
        self.assertEqual(self.operation("cleanup")[2]["phase"], "failed_preserved")

        for action in ("resume", "abandon"):
            with self.subTest(action):
                self.tearDown()
                self.setUp()
                name, attempt = self.closed_backup_with_leftover()

                def unreachable(controller, database):
                    raise pf.DaemonFailure("daemon-unreachable", "daemon-unreachable: the Docker daemon is not "
                                                                 "reachable")

                with mock.patch.object(pf.Controller, "drop_database", unreachable):
                    code, out, err, _ = self.main("cleanup", "--apply")
                self.assertEqual(code, 1, out + err)
                op, plan, journal = self.operation("cleanup")
                self.assertNotIn(journal["phase"], pf_config.TERMINAL_PHASES)
                if action == "resume":
                    code, out, err, _ = self.main("resume")
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(self.operation("cleanup")[2]["phase"], "completed")
                    self.assertNotIn(name, self.state()["plane"]["databases"])
                    self.assertFalse(attempt.exists())
                else:
                    code, out, err, _ = self.main("resume", "--abandon")
                    self.assertEqual(code, 0, out + err)
                    journal = self.operation("cleanup")[2]
                    self.assertEqual(journal["phase"], "failed_preserved")
                    self.assertIn("cleanup-abandoned: not removed: ", journal["last_error"]["message"])
                    self.assertIn(name, journal["last_error"]["message"])
                    self.assertIn(name, self.state()["plane"]["databases"])

    def test_cu13_plan_drift_and_a_refused_image_rm_keep_the_item_and_continue(self):
        """CU-13 (remaining rows): a foreign container that joins a kept topology's network after the approval makes
        its frozen plan drift (plan-drift: the topology is kept, the next item is still removed); ``docker image rm``
        refused by the daemon (a failing child, not an unreachable daemon) keeps the tag and the next item is still
        removed; both close failed_preserved and change nothing outside their items."""
        self.update_state(isolated_faults={"host_port": True})
        code, out, err, _ = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.update_state(isolated_faults={})
        name, attempt = self.closed_backup_with_leftover()
        network = next(item for item in self.state()["networks"] if item["name"].startswith("pfverify-"))
        harness = self

        def join(effect_id, point):
            if point != "after-intent":
                return
            found = pfx.operations_of(harness.context, "cleanup")
            effect = next((item for item in found[-1][1]["effects"] if item["effect_id"] == effect_id), None) \
                if found else None
            if effect is not None and effect["target"].startswith("deletion-plan:pfverify-"):
                state = harness.state()
                state["containers"].append(pfx.container("e" * 64, "foreign-joiner", {"other": "x"},
                                                         networks=((network["name"], network["id"]),)))
                harness.fake.write_state(state)

        with self.crashing(join):
            code, out, err, _ = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("cleanup-item-failed: deletion-plan:pfverify-", out)
        self.assertIn(": plan-drift: ", out)
        self.assertTrue(self.isolated_resources(network["name"][:-len("_default")])["networks"])
        self.assertNotIn(name, self.state()["plane"]["databases"])
        self.assertFalse(attempt.exists())  # the next item was still processed
        op, plan, journal = self.operation("cleanup")
        self.assertEqual(journal["phase"], "failed_preserved")
        rows = [{"row": "plan-drift (a foreign container joins a topology network)", "outcome": journal["phase"]}]

        self.tearDown()
        self.setUp()
        name, attempt = self.closed_backup_with_leftover()
        tags = [f"{self.project}-{service}:backup-{pfx.CHECKPOINT_ID.lower()}" for service in ("backend", "frontend")]
        state = self.state()
        state["images"].append(pfx.image(pfx.image_id("attempt-image"), tags,
                                         {pf_docker.INSTANCE_LABEL: self.context.instance_id}))
        self.fake.write_state(state)
        original = pf.Controller.docker

        def refusing(controller, *args, **kwargs):
            if args[:2] == ("image", "rm") and args[2] == tags[0]:
                raise pf.Failure("docker image rm: Error response from daemon: conflict: unable to remove repository "
                                 "reference (simulated)")
            return original(controller, *args, **kwargs)

        with mock.patch.object(pf.Controller, "docker", refusing):
            code, out, err, _ = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("cleanup-item-failed: deletion-plan:image-tags: ", out)
        remaining = [tag for image in self.state()["images"] for tag in image["repo_tags"]]
        self.assertIn(tags[0], remaining)
        self.assertFalse(attempt.exists())  # the next item was still processed
        self.assertNotIn(name, self.state()["plane"]["databases"])
        self.assertEqual(self.operation("cleanup")[2]["phase"], "failed_preserved")
        rows.append({"row": "image rm refused by the daemon", "outcome": "failed_preserved"})
        evidence("CLEANUP-1-CU-13", {"rows": rows})

    def test_cu4_a_bundle_attempt_that_reads_sealed_is_kept(self):
        """CU-4: a bundle-attempt artifact whose folder now strictly reads as a sealed checkpoint is report-only
        (bundle-attempt-sealed): never in the default set, never removed, its tags kept."""
        code, out, err, _ = self.main("backup")
        self.assertEqual(code, 0, out + err)
        active = self.context.paths.backups / "revisions" / self.project
        checkpoint = next(path.name for path in active.iterdir() if pf.BACKUP_RE.fullmatch(path.name))
        plan = pfx.lifecycle_plan(self.context, "backup", [
            pfx.effect("capturing", "capture", "checkpoint:scheduled-or-manual-backup",
                       preconditions=["bundle:" + checkpoint, "verify:pf_verify_" + "d" * 20]),
            pfx.effect("verifying", "verification", "bundle:scheduled-or-manual-backup")],
            op=pfx.operation_id("backup", "0badcafe", -60))
        journal = pfx.lifecycle_journal(plan, phase="failed_preserved",
                                        states={"e0001": "complete", "e0002": "unknown"},
                                        retained=[{"kind": "bundle-attempt", "name": checkpoint, "sha256": None}],
                                        result={"outcome": "failed_preserved", "deployment_id": None})
        pfx.write_operation(self.context, plan, journal)
        before = pfx.snapshot_tree(self.context.paths.backups)
        tags = sorted(tag for image in self.state()["images"] for tag in image["repo_tags"])
        code, out, err, _ = self.main("cleanup")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"report-only (never removed): bundle-attempt-sealed {checkpoint}", out)
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("cleanup-nothing:", err)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.snapshot_tree(self.context.paths.backups), before)
        self.assertEqual(sorted(tag for image in self.state()["images"] for tag in image["repo_tags"]), tags)

    def test_cu5_a_crash_at_every_seam_resumes_the_frozen_list(self):
        """CU-5: a crash at every seam of every cleanup effect; ``pf resume`` continues the frozen list (the plan bytes
        are unchanged) and never adds an item: a leftover recorded after the approval stays."""
        rows = []
        index = 1
        while index < 12:
            effect_id = f"e{index:04d}"
            done = False
            for point in ("before-intent", "after-intent", "after-effect"):
                with self.subTest(effect=effect_id, point=point):
                    self.tearDown()
                    self.setUp()
                    name, attempt = self.closed_backup_with_leftover()
                    generation, container = self.retained_generation()
                    with self.crashing((effect_id, point)):
                        try:
                            self.main("cleanup", "--apply", "--generation", generation)
                            crashed = False
                        except pf.SimulatedCrash:
                            crashed = True
                    op, plan, journal = self.operation("cleanup")
                    if not any(effect["effect_id"] == effect_id for effect in plan["effects"]):
                        done = True
                        break
                    self.assertTrue(crashed, (effect_id, point))
                    plan_bytes = (self.context.operations_dir / op / "plan.json").read_bytes()
                    late = "pf_verify_" + "f" * 20  # recorded by a closed operation after the approval
                    state = self.state()
                    state["plane"]["databases"][late] = {"heads": [], "rows": {}, "allow": True,
                                                         "owner": "partflow_staging",
                                                         "locale": ["UTF8", "C.UTF-8", "C.UTF-8"]}
                    self.fake.write_state(state)
                    late_plan = pfx.lifecycle_plan(self.context, "backup", [
                        pfx.effect("capturing", "capture", "checkpoint:scheduled-or-manual-backup",
                                   preconditions=["bundle:" + pfx.CHECKPOINT_ID, "verify:" + late]),
                        pfx.effect("verifying", "verification", "bundle:scheduled-or-manual-backup")],
                        op=pfx.operation_id("backup", "1a7e1a7e", -30))
                    pfx.write_operation(self.context, late_plan, pfx.lifecycle_journal(
                        late_plan, phase="failed_preserved", states={"e0001": "complete", "e0002": "unknown"},
                        result={"outcome": "failed_preserved", "deployment_id": None}))
                    started = any(item["state"] != "not_started" for item in journal["effects"])
                    code, out, err, _ = self.main("resume")
                    self.assertEqual(code, 0, out + err)
                    journal = self.operation("cleanup")[2]
                    self.assertEqual((self.context.operations_dir / op / "plan.json").read_bytes(), plan_bytes)
                    databases = self.state()["plane"]["databases"]
                    self.assertIn(late, databases)  # never added to the frozen list
                    if not started:  # nothing of the list started: the A3.2 row closes it cancelled, unchanged
                        self.assertEqual(journal["phase"], "cancelled")
                        self.assertIn(name, databases)
                        self.assertTrue(attempt.exists())
                    else:
                        self.assertEqual(journal["phase"], "completed")
                        self.assertNotIn(name, databases)
                        self.assertFalse(attempt.exists())
                        self.assertFalse((container / generation).exists())
                    rows.append({"kind": "cleanup", "effect": effect_id, "injection": point,
                                 "target": next(e["target"] for e in plan["effects"] if e["effect_id"] == effect_id),
                                 "action": "resume", "outcome": journal["phase"]})
            if done:
                break
            index += 1
        self.assertGreaterEqual(len(rows), 9)
        evidence("CLEANUP-1-CU-5", {"rows": rows})

    def test_cu8_a_recovery_target_only_with_its_selector_and_phrase(self):
        """CU-8: a side-by-side recovery target is never in the default set; only ``--recovery-target`` with its typed
        phrase removes it, by the deletion plan frozen before the cleanup plan (exactly its own containers, network
        and volume); the live instance is untouched."""
        bundle = SideBySide.bundle(self)
        code, out, err, _ = self.main("restore-instance", bundle, "--side-by-side")
        self.assertEqual(code, 0, out + err)
        project = next(item["name"] for item in self.operation("restore-side-by-side")[2]["retained_artifacts"]
                       if item["kind"] == "recovery-target")
        live = SideBySide.live_hashes(self)
        code, out, err, phrases = self.main("cleanup")
        self.assertIn(f"recovery-target {project} (operation ", out)
        self.assertIn(f"only with --recovery-target {project}", out)
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertEqual(code, 1, out + err)
        self.assertIn("cleanup-nothing:", err)
        code, out, err, phrases = self.main("cleanup", "--apply", "--recovery-target", "pfrecover-ffffffffffff")
        self.assertEqual(code, 1, out + err)
        self.assertIn("cleanup-target-unknown: --recovery-target pfrecover-ffffffffffff", err)
        self.assertTrue(self.isolated_resources(project)["containers"])
        declined = []

        def decline(phrase):
            declined.append(phrase)
            if phrase.startswith("REMOVE RECOVERY TARGET"):
                raise pf.Failure("declined")

        code, out, err, phrases = self.main("cleanup", "--apply", "--recovery-target", project, confirm=decline)
        self.assertEqual(code, 1, out + err)
        self.assertTrue(self.isolated_resources(project)["containers"])  # no phrase, no removal
        code, out, err, phrases = self.main("cleanup", "--apply", "--recovery-target", project)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(phrases[1:], ["REMOVE RECOVERY TARGET " + project])
        op, plan, journal = self.operation("cleanup")
        effect = next(e for e in plan["effects"] if e["target"] == "deletion-plan:" + project)
        frozen = self.context.operations_dir / op / f"deletion-plan-{project}.json"
        self.assertIn("plan-sha256:" + hashlib.sha256(frozen.read_bytes()).hexdigest(), effect["preconditions"])
        candidates = json.loads(frozen.read_bytes())["candidates"]
        self.assertEqual(sorted(item["kind"] for item in candidates), ["container"] * 3 + ["network", "volume"])
        self.assertTrue(all(project in json.dumps(item) for item in candidates))
        self.assertEqual(self.isolated_resources(project), {"containers": [], "volumes": [], "networks": []})
        self.assertEqual(SideBySide.live_hashes(self), live)

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
        one checkpoint is also in the active history (a real checkpoint of ``pf backup``, copied) unless ``unique``
        (a synthetic one only there)."""
        name = self.project + ".pre-restore-0badcafe"
        if unique:
            displaced = self.context.paths.backups / "revisions" / name / pfx.CHECKPOINT_ID
            displaced.mkdir(parents=True)
            (displaced / "manifest.json").write_bytes(b'{"synthetic": "manifest"}')
        else:
            code, out, err, _ = self.main("backup")
            self.assertEqual(code, 0, out + err)
            active = self.context.paths.backups / "revisions" / self.project
            checkpoint = next(path for path in active.iterdir() if pf.BACKUP_RE.fullmatch(path.name))
            displaced = self.context.paths.backups / "revisions" / name / checkpoint.name
            displaced.parent.mkdir()
            shutil.copytree(str(checkpoint), str(displaced))
            self.fake.clear_calls()
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
        checkpoint = next(iter(os.listdir(str(folder))))
        code, out, err, phrases = self.main("cleanup")
        self.assertEqual(code, 0, out + err)
        self.assertIn(name, out)
        self.assertIn(f"{checkpoint} [", out)
        self.assertIn("also in the active history (same manifest)", out)
        code, out, err, phrases = self.main("cleanup", "--apply")
        self.assertIn("cleanup-nothing:", err)  # selector-only: never part of the default set
        self.assertTrue(folder.exists())
        code, out, err, phrases = self.main("cleanup", "--apply", "--checkpoint-history", name)
        self.assertEqual(code, 0, out + err)
        self.assertIn("DELETE CHECKPOINT HISTORY " + name, phrases)
        self.assertFalse(folder.exists())
        self.assertTrue((self.context.paths.backups / "revisions" / self.project / checkpoint).exists())

    def test_cu12_an_entry_that_is_not_a_checkpoint_or_a_damaged_active_copy_is_unique(self):
        """Audit finding: the displaced tree is removed as a whole, so every entry must be proven duplicated: an entry
        that is not a checkpoint, or a checkpoint whose active copy no longer reads strictly, refuses before the
        confirmation and nothing changes."""
        name, folder = self.displaced_history(unique=False)
        checkpoint = next(iter(os.listdir(str(folder))))
        for case in ("stray-file", "stray-directory", "damaged-active-copy"):
            with self.subTest(case):
                stray = None
                if case == "stray-file":
                    stray = folder / "legacy-dump-2025-12-01.sql.gz"
                    stray.write_bytes(b"only copy of a legacy dump")
                elif case == "stray-directory":
                    stray = folder / "notes"
                    stray.mkdir()
                    (stray / "why.txt").write_text("operator notes")
                else:
                    active = self.context.paths.backups / "revisions" / self.project / checkpoint
                    dump = next(path for path in sorted(active.rglob("*")) if path.is_file()
                                and path.name != "manifest.json")
                    original = dump.read_bytes()
                    os.chmod(str(dump), 0o600)
                    dump.write_bytes(original + b"bit rot")
                before = pfx.snapshot_tree(self.context.paths.backups)
                code, out, err, phrases = self.main("cleanup", "--apply", "--checkpoint-history", name)
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"cleanup-history-unique-checkpoint: {name} holds ", err)
                self.assertIn("legacy-dump" if case == "stray-file" else "notes" if case == "stray-directory"
                              else checkpoint, err)
                self.assertEqual(phrases, [])
                self.assertEqual(pfx.snapshot_tree(self.context.paths.backups), before)
                if stray is not None:
                    shutil.rmtree(str(stray)) if stray.is_dir() else stray.unlink()
                else:
                    dump.write_bytes(original)
        code, out, err, phrases = self.main("cleanup", "--apply", "--checkpoint-history", name)
        self.assertEqual(code, 0, out + err)
        self.assertFalse(folder.exists())

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

    def sealed_members(self, generation):
        seal = self.paths["backups"] / "generations" / self.project / generation
        with tarfile.open(str(seal / "workspace.tar.gz")) as archive:
            return sorted(member.name.rsplit("/", 1)[-1] for member in archive.getmembers() if member.isfile())

    def test_cu5_cu6_a_second_cleanup_never_discards_an_earlier_valid_seal(self):
        """Audit finding: an interrupted removal leaves a partial tree; the earlier seal is then the only complete copy.
        A second cleanup keeps it and the partial tree (generation-seal-exists), never re-seals over it."""
        generation, container = self.retained_generation()
        (container / generation / "precious.txt").write_bytes(b"only complete copy after the removal\n")

        def seam(label, when):  # a removal interrupted part-way (OSError or power loss) leaves a partial tree
            if when == "inside" and label == pf.INSIDE_PREFIX + "before-remove":
                if (container / generation / "precious.txt").exists():
                    (container / generation / "precious.txt").unlink()

        with self.crashing(seam):
            code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 1, out + err)
        seal = self.paths["backups"] / "generations" / self.project / generation
        record = (seal / "seal.json").read_bytes()
        self.assertIn("precious.txt", self.sealed_members(generation))
        code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"generation-seal-exists: {generation} already has a valid seal", out)
        self.assertEqual((seal / "seal.json").read_bytes(), record)
        self.assertIn("precious.txt", self.sealed_members(generation))
        self.assertTrue((container / generation / "app-version.txt").exists())
        self.assertEqual(self.operation("cleanup")[2]["phase"], "failed_preserved")

    def test_cu6_a_second_cleanup_adopts_an_earlier_seal_of_the_same_content(self):
        generation, container = self.retained_generation()

        def seam(label, when):
            if when == "inside" and label == pf.INSIDE_PREFIX + "before-remove":
                (container / generation / "late.txt").write_text("written after the seal")

        with self.crashing(seam):
            code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 1, out + err)
        seal = self.paths["backups"] / "generations" / self.project / generation
        record = (seal / "seal.json").read_bytes()
        (container / generation / "late.txt").unlink()  # the operator reverts the late write
        code, out, err, _ = self.main("cleanup", "--apply", "--generation", generation)
        self.assertEqual(code, 0, out + err)
        self.assertEqual((seal / "seal.json").read_bytes(), record)
        self.assertFalse((container / generation).exists())
        self.assertEqual(self.sealed_members(generation), ["app-version.txt"])


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

    def test_ak7_the_running_application_sessions_do_not_block_the_acknowledgement(self):
        """Audit finding: the probe counts sessions only on the databases the records' database effects name; the live
        application's pooled sessions on its own database never block it. A session on a named database does."""
        op, directory = self.no_journal()

        def sessions(found):
            state = self.state()
            state["plane"].update(client_backends=sum(found.values()), sessions=found)
            self.fake.write_state(state)

        before = sorted(path.name for path in directory.iterdir())
        sessions({"partflow_staging": 3, "pf_verify_" + "c" * 20: 1})
        code, out, err, phrases = self.main("resume", "--operation", op, "--acknowledge")
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"effect-still-running: operation {op} still has a running 1 database session(s) on pf_verify_",
                      err)
        self.assertEqual(sorted(path.name for path in directory.iterdir()), before)
        sessions({"partflow_staging": 4})
        code, out, err, phrases = self.main("resume", "--operation", op, "--acknowledge")
        self.assertEqual(code, 0, out + err)
        # A record that names no database effect is never probed for sessions; one whose database is unidentified
        # counts every client session (the strict rule: stop the application first).
        records = json.loads((directory / "unresolved-effects.json").read_text())
        for name, effect, wanted in (
                ("20261007T032000Z-backup-emergency-2c3d4e5f", {"kind": "compose", "verb": "build",
                                                                 "project": self.project, "targets": []}, 0),
                ("20261007T032500Z-backup-emergency-3d4e5f60", {"kind": "compose-exec", "verb": "pg_restore",
                                                                 "project": self.project, "service": "db",
                                                                 "targets": []}, 1)):
            other = self.context.operations_dir / name
            other.mkdir(mode=0o700)
            (other / "unresolved-effects.json").write_text(json.dumps([dict(records[0], effect=effect)]) + "\n")
            os.chmod(other / "unresolved-effects.json", 0o600)
            code, out, err, _ = self.main("resume", "--operation", name, "--acknowledge")
            self.assertEqual(code, wanted, out + err)
            if wanted:
                self.assertIn("4 database session(s) (a record names an unidentified database)", err)
        self.assertEqual(pf.acknowledgement_session_scope([]), None)


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

    def test_rp8_a_healthy_attempt_folder_gives_the_emergency_capture_a_fallback_id(self):
        """RP-8: the healthy attempt fails after creating its folder; the emergency preservation gets its own ID, and
        the journal, status and the activation read that retained ID, never the pre-assigned one (an emergency
        before-reset is never a rollback target). An emergency capture with an unidentified frontend image refuses
        reset-images-unidentified after the capture: the preservation is kept and no candidate is created."""
        original = pf.Controller._capture

        def healthy_fails(controller, reason, **kwargs):
            if kwargs.get("capture_class") == "healthy_checkpoint" and kwargs.get("bundle_id"):
                (controller.backups_dir / kwargs["bundle_id"]).mkdir(parents=True)
                raise pf.Failure("simulated: the healthy restore test failed")
            return original(controller, reason, **kwargs)

        with mock.patch.object(pf.Controller, "_capture", healthy_fails):
            code, out, err, phrases = self.reset()
        self.assertEqual(code, 0, out + err)
        op, plan, journal = self.operation("reset-db")
        self.assertEqual(journal["phase"], "completed")
        capture = next(effect for effect in plan["effects"] if effect["target"] == "checkpoint:before-reset")
        assigned = self.precondition(capture, "bundle")
        retained = [item["name"] for item in journal["retained_artifacts"] if item["kind"] == "checkpoint"]
        self.assertEqual(len(retained), 1)
        self.assertNotEqual(retained[0], assigned)
        self.assertTrue((self.context.paths.backups / "revisions" / self.project / retained[0]).exists())
        evidence_text = next(item for item in journal["effects"] if item["effect_id"] == capture["effect_id"])
        self.assertIn("bundle:" + retained[0], evidence_text["evidence"])
        self.assertIn("emergency", evidence_text["evidence"])
        self.assertEqual(pf_config.rollback_target(plan, journal), "<checkpoint>")
        code, out, err, _ = self.main("status", "--operation", op)
        self.assertEqual(code, 0, out + err)
        self.assertIn(retained[0], out)

        self.tearDown()
        self.setUp()
        state = self.state()
        state["plane"]["databases"]["partflow_staging"]["heads"] = ["r0"]  # emergency preservation
        self.fake.write_state(state)
        retain = pf.Controller.retain_image

        def unidentified(controller, service, backup_id):
            if service == "frontend" and controller.journal is not None:  # the capture, not the preview
                raise pf.Failure("the running frontend image cannot be identified")
            return retain(controller, service, backup_id)

        with mock.patch.object(pf.Controller, "retain_image", unidentified):
            code, out, err, phrases = self.reset()
        self.assertEqual(code, 1, out + err)
        self.assertIn("reset-images-unidentified:", out + err)
        op, plan, journal = self.operation("reset-db")
        retained = [item["name"] for item in journal["retained_artifacts"] if item["kind"] == "checkpoint"]
        self.assertEqual(len(retained), 1)
        self.assertTrue((self.context.paths.backups / "revisions" / self.project / retained[0]).exists())
        self.assertFalse([name for name in self.state()["plane"]["databases"] if name.startswith("pf_reset_")
                          or name.startswith("pf_clean_")])
        self.assertEqual(self.state()["plane"]["databases"]["partflow_staging"]["heads"], ["r0"])
        # NF-1: the mismatched database cannot be reopened; the copy names the abandon, which closes it.
        self.assertIn(f"resume --operation {op} --abandon' closes it", out + err)
        code, out, err, _ = self.main("resume", "--abandon")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("reset-db")[2]["phase"], "cancelled")
        self.assertEqual(self.state()["plane"]["databases"]["partflow_staging"]["heads"], ["r0"])

    @staticmethod
    def precondition(effect, key):
        return next((item.split(":", 1)[1] for item in effect["preconditions"] if item.startswith(key + ":")), None)

    def mismatched(self):
        state = self.state()
        state["plane"]["databases"]["partflow_staging"]["heads"] = ["r0"]  # schema/image mismatch: emergency first
        self.fake.write_state(state)

    def running_services(self):
        return sorted(item["labels"][pf_docker.COMPOSE_SERVICE_LABEL] for item in self.state()["containers"]
                      if item["status"] == "running"
                      and item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project)

    NF1_SEAMS = (("e0001", "after-intent"), ("e0001", "after-effect"), ("e0002", "before-intent"),
                 ("e0002", "after-intent"), ("e0002", "after-effect"), ("e0003", "before-intent"))

    def test_nf1_a_mismatch_reset_interrupted_in_preserving_has_a_legal_route(self):
        """NF-1 (audit, completion run; RO-13 no dead end): a reset-db planned on a schema/image mismatch and
        interrupted in ``preserving`` cannot reopen the unchanged deployment (its database is not at the running
        image's heads). ``resume`` goes forward: it re-observes (or re-runs) the emergency preservation and completes
        the reset. ``resume --abandon`` closes it cancelled without a reopen: candidates dropped, the services left as
        the reset left them, the next commands named; a new ``pf reset-db`` then completes (a legal route exists)."""
        rows = []
        for effect_id, point in self.NF1_SEAMS:
            for action in ("resume", "abandon"):
                with self.subTest(effect=effect_id, point=point, action=action):
                    self.tearDown()
                    self.setUp()
                    self.mismatched()
                    with self.crashing((effect_id, point)):
                        with self.assertRaises(pf.SimulatedCrash):
                            self.reset()
                    op, plan, journal = self.operation("reset-db")
                    self.assertEqual(journal["phase"], "preserving")
                    capture = next(e for e in plan["effects"] if e["target"] == "checkpoint:before-reset")
                    self.assertIn(pf_config.RESET_MISMATCH_PRECONDITION, capture["preconditions"])
                    legal = journal["legal_next"]
                    self.assertIn(f"pf --instance staging resume --operation {op}", legal)
                    self.assertIn(f"pf --instance staging resume --operation {op} --abandon", legal)
                    before = self.running_services()
                    if action == "resume":
                        code, out, err, phrases = self.main("resume")
                        self.assertEqual(code, 0, out + err)
                        self.assertEqual(phrases, ["RESUME " + op[-8:]])
                        op, plan, journal = self.operation("reset-db")
                        self.assertEqual(journal["phase"], "completed")
                        databases = self.state()["plane"]["databases"]
                        self.assertEqual(databases["partflow_staging"]["heads"], ["r1"])
                        retained = [name for name in databases if name.startswith("pf_keep_")]
                        self.assertEqual([databases[name]["heads"] for name in retained], [["r0"]])
                        checkpoints = [item["name"] for item in journal["retained_artifacts"]
                                       if item["kind"] == "checkpoint"]
                        self.assertTrue(checkpoints)
                        self.assertTrue((self.context.paths.backups / "revisions" / self.project
                                         / checkpoints[-1]).exists())
                        self.assertEqual(self.running_services(), ["backend", "db", "frontend"])
                        rows.append({"effect": effect_id, "injection": point, "action": "resume", "exit": code,
                                     "outcome": journal["phase"]})
                        continue
                    code, out, err, phrases = self.main("resume", "--abandon")
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(phrases, ["ABANDON " + op[-8:]])
                    journal = self.operation("reset-db")[2]
                    self.assertEqual(journal["phase"], "cancelled")
                    databases = self.state()["plane"]["databases"]
                    self.assertEqual(databases["partflow_staging"]["heads"], ["r0"])  # unchanged, never reset
                    self.assertFalse([name for name in databases if name.startswith(("pf_clean_", "pf_keep_"))])
                    self.assertEqual(self.running_services(), before)  # never reopened, never started
                    self.assertIn("the unchanged deployment cannot be reopened", out)
                    self.assertIn("--instance staging reset-db' (preserves the current data again", out)
                    self.fake.clear_calls()
                    code, out, err, phrases = self.reset()  # the named route works
                    self.assertEqual(code, 0, out + err)
                    later = [item for item in pfx.operations_of(self.context, "reset-db") if item[0] != op]
                    self.assertEqual([item[2]["phase"] for item in later], ["completed"])
                    self.assertEqual(self.state()["plane"]["databases"]["partflow_staging"]["heads"], ["r1"])
                    rows.append({"effect": effect_id, "injection": point, "action": "abandon", "exit": 0,
                                 "outcome": "cancelled", "then": "pf reset-db completed"})
        evidence("NF-1", {"rows": rows})

    def renames(self):
        return [argv for argv in self.fake.argvs() if any(str(word).startswith("BEGIN;") and "RENAME TO" in str(word)
                                                          for word in argv)]

    def test_rp3_a_failed_preservation_creates_no_candidate_and_no_switch(self):
        """RP-3: neither the healthy nor the emergency capture completes: preservation-failed; no clean candidate, no
        switch, the live database unchanged (consistent and mismatched instances alike)."""
        def failing(controller, reason, **kwargs):
            raise pf.Failure("simulated: the capture cannot complete")

        for mismatched in (False, True):
            with self.subTest(mismatched=mismatched):
                self.tearDown()
                self.setUp()
                if mismatched:
                    self.mismatched()
                heads = self.state()["plane"]["databases"]["partflow_staging"]["heads"]
                with mock.patch.object(pf.Controller, "_capture", failing):
                    code, out, err, _ = self.reset()
                self.assertEqual(code, 1, out + err)
                self.assertIn("preservation-failed: the current database partflow_staging could not be preserved",
                              out + err)
                databases = self.state()["plane"]["databases"]
                self.assertFalse([name for name in databases if name.startswith(("pf_clean_", "pf_keep_"))])
                self.assertEqual(databases["partflow_staging"]["heads"], heads)
                self.assertEqual(self.renames(), [])
                op, plan, journal = self.operation("reset-db")
                for effect in plan["effects"]:
                    if effect["phase"] not in ("preserving",):
                        self.assertEqual(pf_config.effect_state(journal, effect["effect_id"]), "not_started", effect)
                if mismatched:  # NF-1: the copy names the routes that exist; the abandon closes it
                    self.assertIn("resume --abandon' closes it (the database does not match the running image",
                                  out + err)
                    code, out, err, _ = self.main("resume", "--abandon")
                    self.assertEqual(code, 0, out + err)
                    self.assertEqual(self.operation("reset-db")[2]["phase"], "cancelled")
                else:
                    self.assertIn("'pf --instance staging resume' reopens the unchanged deployment.", out + err)

    def test_rp5_a_failure_after_the_switch_goes_forward_without_a_second_rename(self):
        """RP-5: the database switch completed, then the activation fails (backend health) or the process dies right
        after the switch: resume observes the switch complete and continues forward; the rename transaction appears
        exactly once in the call log."""
        for case in ("health", "crash"):
            with self.subTest(case):
                self.tearDown()
                self.setUp()
                if case == "health":
                    original = pf.Controller.wait_health
                    failures = {"n": 0}

                    def unhealthy(controller, service):
                        if service == "backend" and controller.plan is not None \
                                and controller.plan["kind"] == "reset-db" and failures["n"] == 0:
                            failures["n"] += 1
                            raise pf.Failure("backend did not become healthy within the configured timeout.")
                        return original(controller, service)

                    with mock.patch.object(pf.Controller, "wait_health", unhealthy):
                        code, out, err, _ = self.reset()
                    self.assertEqual(code, 1, out + err)
                else:
                    def crash(effect_id, point):
                        found = pfx.operations_of(self.context, "reset-db")
                        effect = next((item for item in found[-1][1]["effects"] if item["effect_id"] == effect_id),
                                      None) if found else None
                        if point == "after-effect" and effect is not None and effect["type"] == "database-switch":
                            raise pf.SimulatedCrash("after-effect " + effect_id)

                    with self.crashing(crash):
                        with self.assertRaises(pf.SimulatedCrash):
                            self.reset()
                op, plan, journal = self.operation("reset-db")
                switch = next(e for e in plan["effects"] if e["type"] == "database-switch")
                self.assertNotIn(journal["phase"], pf_config.CLOSED_PHASES)
                self.assertEqual(len(self.renames()), 1)
                code, out, err, _ = self.main("resume")
                self.assertEqual(code, 0, out + err)
                op, plan, journal = self.operation("reset-db")
                self.assertEqual(journal["phase"], "completed")
                self.assertEqual(pf_config.effect_state(journal, switch["effect_id"]), "complete")
                self.assertEqual(len(self.renames()), 1)
                if case == "crash":
                    self.assertIn(f"observed: {switch['effect_id']} database-switch", out)
                databases = self.state()["plane"]["databases"]
                self.assertEqual(databases["partflow_staging"]["heads"], ["r1"])
                self.assertEqual(len([name for name in databases if name.startswith("pf_keep_")]), 1)

    def test_rp9_a_deployment_image_mismatch_is_refused_before_the_confirmation(self):
        """RP-9: the running backend image is not the deployment record's: reset-db refuses
        reset-deployment-image-mismatch before the confirmation, both with matching heads (deployment-image-mismatch)
        and with a schema mismatch (it would activate an image the record does not name)."""
        SideBySide.bundle(self)  # an instance purge, then the exact restore writes a deployment record
        controller = self.controller()
        view = controller.current_deployment()
        self.assertIsNotNone(view)
        self.assertIsNone(view.mismatch)
        recorded = view.record["images"]["backend"]["id"]
        other = pfx.image_id("rp9-backend")
        state = self.state()
        image = pfx.image(other, [], {pf_docker.INSTANCE_LABEL: self.context.instance_id})
        image["contract"] = {"files": {"alembic.ini": "0" * 64}, "heads": ["r1"]}
        state["images"].append(image)
        for container in state["containers"]:
            if container["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project \
                    and container["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "backend":
                container["image"] = other
        self.fake.write_state(state)
        for label, heads in (("deployment-image-mismatch", None), ("schema mismatch", ["r0"])):
            with self.subTest(label):
                if heads is not None:
                    state = self.state()
                    state["plane"]["databases"]["partflow_staging"]["heads"] = heads
                    self.fake.write_state(state)
                databases = json.dumps(self.state()["plane"]["databases"], sort_keys=True)
                code, out, err, phrases = self.reset()
                self.assertEqual(code, 1, out + err)
                self.assertIn(f"reset-deployment-image-mismatch: the running backend image {other[7:19]} is not "
                              f"deployment {view.deployment_id}'s {recorded[7:19]}", err)
                self.assertIn("Nothing was changed.", err)
                self.assertEqual(phrases, [])
                self.assertEqual(pfx.operations_of(self.context, "reset-db"), [])
                self.assertEqual(json.dumps(self.state()["plane"]["databases"], sort_keys=True), databases)

    def test_nf1_a_consistent_reset_interrupted_in_preserving_still_reopens(self):
        """NF-1 counterpart: without a mismatch the A3.2 row is unchanged (resume and abandon reopen, cancelled)."""
        for action in ("resume", "abandon"):
            with self.subTest(action):
                self.tearDown()
                self.setUp()
                with self.crashing(("e0002", "after-effect")):
                    with self.assertRaises(pf.SimulatedCrash):
                        self.reset()
                op, plan, journal = self.operation("reset-db")
                capture = next(e for e in plan["effects"] if e["target"] == "checkpoint:before-reset")
                self.assertNotIn(pf_config.RESET_MISMATCH_PRECONDITION, capture["preconditions"])
                code, out, err, _ = self.main(*(("resume",) if action == "resume" else ("resume", "--abandon")))
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.operation("reset-db")[2]["phase"], "cancelled")
                self.assertEqual(self.running_services(), ["backend", "db", "frontend"])
                self.assertEqual(self.state()["plane"]["databases"]["partflow_staging"]["heads"], ["r1"])

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
        # PF-A3.4 (F-A34-03): a stopped db container is started by the route; unreachable means it cannot start.
        self.deploy(phase="activating", unknown="e0006")
        state = self.state()
        for item in state["containers"]:
            if item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "db":
                item["status"] = "exited"
        state["plane"]["start_error"] = "Error response from daemon: simulated: the db container cannot start"
        self.fake.write_state(state)
        code, out, err, phrases = self.main("abort-deploy")
        self.assertEqual(code, 1, out + err)
        self.assertIn("preservation-failed:", err)
        self.assertTrue([argv for argv in self.fake.argvs() if argv[:1] == ["compose"] and argv[-2:] == ["start", "db"]])
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




class StoppedDatabase(Plane):
    """PF-A3.4 D5 regressions for F-A34-03 (real rows R10 and R65, Engine 28.5.1): a db container stopped or killed from
    outside is not restarted by ``unless-stopped``; every route that needs the database starts the instance's own
    existing db container (``compose start``, never a create), so no state is left without a working route (RO-13)."""

    def instance(self, slug, project):
        # A short health bound: before the fix a resumed activation waited for a backend that never became healthy.
        paths = pfx.data_home(self.base / slug, project=project, group=GROUP)
        config = paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1,
                                          health_timeout_seconds=4)) + "\n")
        context = pfx.register(self.layout, slug, paths, project=project)
        return context, paths

    def db_containers(self, state=None):
        state = state or self.state()
        return [item for item in state["containers"]
                if item["labels"].get(pf_docker.COMPOSE_SERVICE_LABEL) == "db"
                and item["labels"].get(pf_docker.COMPOSE_PROJECT_LABEL) == self.project]

    def stop_db(self):
        state = self.state()
        for item in self.db_containers(state):
            item["status"] = "exited"
        self.fake.write_state(state)

    def db_calls(self, verb):
        return [argv for argv in self.fake.argvs() if argv[:1] == ["compose"] and verb in argv and argv[-1] == "db"]

    def test_sd1_r10_the_next_backup_starts_a_db_stopped_from_outside(self):
        """R10: a ``docker kill`` of the db during a backup's ``pg_dump`` closes it failed_preserved; the next
        ``pf backup`` refused "The database container is not running." and pf status named no route (dead end)."""
        self.stop_db()
        self.fake.clear_calls()
        code, out, err, phrases = self.main("backup")
        self.assertEqual(code, 0, out + err)
        self.assertIn("note: db-started: the database container of this instance was stopped", out)
        self.assertEqual(self.operation("backup")[2]["phase"], "completed")
        self.assertEqual(len(self.db_calls("start")), 1)
        self.assertEqual(self.db_calls("up"), [])  # an existing container is started, never recreated
        self.assertEqual({item["status"] for item in self.db_containers()}, {"running"})
        # The readiness wait asked the final server over TCP before the first database step.
        calls = self.fake.argvs()
        start = calls.index(self.db_calls("start")[0])
        ready = next(index for index, argv in enumerate(calls) if "pg_isready" in argv and "-h" in argv)
        first_sql = next(index for index, argv in enumerate(calls) if index > start and "psql" in argv)
        self.assertLess(start, ready)
        self.assertLess(ready, first_sql)

    def test_sd2_a_missing_db_container_is_never_created_by_the_start(self):
        state = self.state()
        removed = self.db_containers(state)
        state["containers"] = [item for item in state["containers"] if item not in removed]
        self.fake.write_state(state)
        volumes = copy.deepcopy(state["volumes"])
        self.fake.clear_calls()
        code, out, err, phrases = self.main("backup")
        self.assertEqual(code, 1, out + err)
        self.assertIn("Expected exactly one existing db container", err)
        self.assertEqual((self.db_calls("start"), self.db_calls("up")), ([], []))
        self.assertEqual(self.state()["volumes"], volumes)
        self.assertEqual(self.db_containers(), [])

    def interrupted_switch(self):
        """R65's precondition on the plane: an operation whose database switch completed, interrupted at its backend
        start; then the db container is stopped from outside. reset-db runs the same switch and activation effects
        (``activation_specs``, ``activate_backend``) as rollback --restore-db; the superseding rollback itself is
        test_artifacts EP-16."""
        def crash(effect_id, point):
            found = pfx.operations_of(self.context, "reset-db")
            effect = next((item for item in found[-1][1]["effects"] if item["effect_id"] == effect_id),
                          None) if found else None
            if point == "after-intent" and effect is not None and effect["target"].startswith("service:backend:"):
                raise pf.SimulatedCrash("after-intent " + effect_id)

        with self.crashing(crash):
            with self.assertRaises(pf.SimulatedCrash):
                self.main("reset-db")
        op, plan, journal = self.operation("reset-db")
        switch = next(effect for effect in plan["effects"] if effect["type"] == "database-switch")
        self.assertEqual(pf_config.effect_state(journal, switch["effect_id"]), "complete")
        self.assertEqual(journal["phase"], "activating")
        self.stop_db()
        self.fake.clear_calls()
        return op

    def renames(self):
        return [argv for argv in self.fake.argvs() if any(str(word).startswith("BEGIN;") and "RENAME TO" in str(word)
                                                          for word in argv)]

    def test_sd3_r65_a_forward_resume_of_the_activation_starts_the_stopped_db(self):
        """R65 (OD-A33-12): the forward resume re-ran ``service:backend:start`` with ``--no-deps``; the backend never
        became healthy without its database, every time."""
        op = self.interrupted_switch()
        code, out, err, _ = self.main("status")
        self.assertIn(f"pf --instance staging resume --operation {op}", out)
        code, out, err, _ = self.main("resume")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.operation("reset-db")[2]["phase"], "completed")
        calls = self.fake.argvs()
        start = calls.index(self.db_calls("start")[0])
        backend = next(index for index, argv in enumerate(calls) if argv[:1] == ["compose"] and "up" in argv
                       and argv[-1] == "backend")
        self.assertLess(start, backend)
        self.assertEqual(self.renames(), [])  # the completed switch is never repeated
        databases = self.state()["plane"]["databases"]
        self.assertEqual(len([name for name in databases if name.startswith("pf_keep_")]), 1)

    def test_sd4_r65_the_emergency_backup_route_starts_the_stopped_db(self):
        """R65: ``backup --emergency``, a route pf status offers next to the interrupted operation, needs the database
        as well and refused "The database container is not running."."""
        self.interrupted_switch()
        code, out, err, phrases = self.main("backup", "--emergency")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(phrases, ["EMERGENCY BACKUP " + self.project])
        self.assertEqual(len(self.db_calls("start")), 1)
        self.assertEqual(self.operation("reset-db")[2]["phase"], "activating")  # unchanged; resume stays legal


class CapacityIntegrated(Plane):
    """CP-2 (purge row), CP-3, CP-6, CP-7: the Docker root and the real statvfs."""

    def test_cp2_the_purge_recovery_need_counts_the_image_archive(self):
        """Audit finding (section 3.8 purge row): images.tar (Σ docker image inspect Size of the saved refs and the db
        image) is part of the capturing/recovery need, so a recovery device without room for it refuses before the
        confirmation instead of failing in docker image save after the stop."""
        info = os.statvfs(str(self.paths["recovery"]))
        free = info.f_bavail * info.f_frsize
        state = self.state()
        for image in state["images"]:
            image["size"] = free  # each distinct image alone fills the device
        self.fake.write_state(state)
        before = pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"])
        code, out, err, phrases = self.main("purge", "--keep-backups")
        self.assertEqual(code, 1, out + err)
        self.assertIn("capacity-insufficient:", err)
        line = err.split("capacity-insufficient:", 1)[1].split("\n", 1)[0]
        self.assertIn("recovery", line)
        self.assertEqual(phrases, [])
        self.assertEqual(pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"]), before)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["image", "save"]])
        # The need grows with the image sizes (distinct IDs; an image without a size is unmeasurable).
        state = self.state()
        for image in state["images"]:
            image["size"] = 0
        self.fake.write_state(state)
        controller = self.controller()
        controller.staging()
        small = controller.purge_image_bytes()
        state = self.state()
        for image in state["images"]:
            image["size"] = 1 << 20
        self.fake.write_state(state)
        self.assertGreaterEqual(controller.purge_image_bytes() - small, 3 << 20)  # backend, frontend, db at least

    def test_cp8_a_capacity_refusal_runs_no_contract_probe(self):
        """PF-A3.4 D5 regression (real C-A3-14 (a), F-A34-04): the capacity refusals of purge and backup came after
        the contract probe, which tags the running images, runs a probe container and rewrites
        state/inspect-images.yaml (purge also wrote inventory-preliminary.json): a refused command was not effect-free.
        The read-only capacity refusal now comes first."""
        def measure(controller, path, role):
            return (2, 1) if role == "docker-root" else (1, 1 << 50)  # only the Docker root is short

        inspect = self.context.state_dir / "inspect-images.yaml"
        for arguments in (("purge", "--keep-backups"), ("backup",)):
            with self.subTest(arguments[0]):
                before = inspect.read_bytes() if inspect.exists() else None
                images = copy.deepcopy(self.state()["images"])
                self.fake.clear_calls()
                with mock.patch.object(pf.Controller, "measure", measure):
                    code, out, err, phrases = self.main(*arguments)
                self.assertEqual(code, 1, out + err)
                self.assertIn("capacity-insufficient:", err)
                calls = self.fake.argvs()
                self.assertEqual([argv for argv in calls if argv[:1] == ["tag"]], [])
                self.assertEqual([argv for argv in calls if argv[:1] == ["compose"] and "run" in argv], [])
                self.assertEqual(self.state()["images"], images)
                self.assertEqual(inspect.read_bytes() if inspect.exists() else None, before)
                self.assertEqual(list(self.context.operations_dir.rglob("inventory-preliminary.json")), [])

    CP2_TABLE = {
        "backup": {("capturing", "backups"), ("verifying", "docker-root")},
        "update": {("preserving", "backups"), ("preserving", "docker-root"), ("migrating", "docker-root")},
        "rollback": {("preserving-current", "backups"), ("preserving-current", "docker-root")},
        "reset-db": {("preserving", "backups"), ("preserving", "docker-root"), ("initializing", "docker-root")},
        "purge": {("preserving", "backups"), ("preserving", "docker-root"), ("capturing", "recovery"),
                  ("capturing", "docker-root"), ("verifying", "docker-root"), ("verifying", "private-state")},
        "abort-deploy": {("preserving", "backups"), ("preserving", "docker-root")},
        "deploy": {("preparing", "docker-root")}}

    def test_cp2_each_kind_names_its_roles_phases_and_devices(self):
        """CP-2: the section 3.8 table per kind: every preserving capture (backup, update, rollback, reset, instance
        purge, abort-deploy) has its Docker-root verification need (2 x the live size) next to its backups need; the
        Docker root is the daemon's DockerRootDir. Through the CLI a short Docker root alone refuses each kind's
        preflight naming docker-root, before any confirmation, with nothing changed."""
        controller = self.controller()
        controller.staging()
        live = controller.database_sizes()["partflow_staging"]
        for kind, rows in self.CP2_TABLE.items():
            with self.subTest(kind):
                needs = controller.capacity_needs(kind)
                self.assertEqual({(phase, role) for phase, role, _, _ in needs}, rows)
                for phase, role, path, size in needs:
                    if role == "docker-root":
                        self.assertEqual(str(path), "/")  # the fake daemon's DockerRootDir
                    if role == "backups":
                        self.assertEqual(Path(path), controller.backups_root)
                    if role == "docker-root" and phase in ("preserving", "preserving-current", "verifying") \
                            and kind != "purge":
                        self.assertEqual(size, pf_config.restored_estimate(live_bytes=live))
        rows = []

        def measure(controller, path, role):
            return (2, 1) if role == "docker-root" else (1, 1 << 50)  # only the Docker root is short

        for label, arguments in (("backup", ("backup",)), ("reset-db", ("reset-db",)),
                                 ("purge", ("purge", "--keep-backups"))):
            with self.subTest(cli=label):
                before = pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"])
                with mock.patch.object(pf.Controller, "measure", measure):
                    code, out, err, phrases = self.main(*arguments)
                self.assertEqual(code, 1, out + err)
                line = err.split("capacity-insufficient:", 1)[1].split("\n", 1)[0]
                self.assertIn("on device 2 (docker-root: /)", line)
                self.assertNotIn("backups", line)
                self.assertTrue(line.rstrip().endswith("Nothing was changed."), line)
                self.assertEqual(phrases, [])
                self.assertEqual(pfx.operations_of(self.context, label), [])
                self.assertEqual(pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"]), before)
                rows.append({"kind": label, "refusal": "capacity-insufficient:" + line})
        evidence("CAPACITY-1-CP-2", {"table": {kind: sorted(map(list, value))
                                               for kind, value in self.CP2_TABLE.items()}, "refusals": rows})

    def test_cp4_cp6_in_operation_re_checks_stop_before_the_phase(self):
        """CP-4 (backup) / CP-6: the preflight passes, the Docker root is short at the verifying re-check: the backup
        closes failed_preserved before its verification ('The operation stopped before verifying.'); nothing was
        deleted (no deletion child) and every file that existed before is unchanged."""
        def measure(controller, path, role):
            short = role == "docker-root" and controller.plan is not None
            return (2, 1) if short else (1, 1 << 50)

        before = pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"], self.context.artifacts_dir)
        with mock.patch.object(pf.Controller, "measure", measure):
            code, out, err, _ = self.main("backup")
        self.assertEqual(code, 1, out + err)
        self.assertIn("capacity-insufficient: verifying needs", out + err)
        self.assertIn("The operation stopped before verifying.", out + err)
        op, plan, journal = self.operation("backup")
        self.assertEqual(journal["phase"], "failed_preserved")
        verification = next(e for e in plan["effects"] if e["type"] == "verification")
        self.assertEqual(pf_config.effect_state(journal, verification["effect_id"]), "not_started")
        self.assert_nothing_deleted(before)
        decisions = json.loads((self.context.operations_dir / op / "capacity.json").read_bytes())
        self.assertEqual([item["phase"] for item in decisions if item["result"] == "short"], ["verifying"])

    def assert_nothing_deleted(self, before):
        """CP-6: no deletion child, and every file of the backups/recovery/artifacts trees from before is unchanged."""
        deletions = [argv for argv in self.fake.argvs() if argv[:1] == ["rm"] or argv[:2] in (
            ["volume", "rm"], ["network", "rm"], ["image", "rm"]) or "dropdb" in argv]
        self.assertEqual(deletions, [])
        after = pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"], self.context.artifacts_dir)
        for path, value in before.items():
            self.assertIn(path, after)
            if value[-1] is not None:  # a file: same inode and bytes (a directory may gain entries)
                self.assertEqual(after[path], value, path)

    def test_cp5_capacity_json_holds_the_decisions_only(self):
        """CP-5: capacity.json of a completed backup: 0600, one decision per device per check (preflight, capturing,
        verifying) with the closed key set; no value, password or secret."""
        code, out, err, _ = self.main("backup")
        self.assertEqual(code, 0, out + err)
        op = self.operation("backup")[0]
        path = self.context.operations_dir / op / "capacity.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        decisions = json.loads(path.read_bytes())
        self.assertEqual({item["phase"] for item in decisions}, {"preflight", "capturing", "verifying"})
        for item in decisions:
            self.assertEqual(set(item), {"phase", "device", "roles", "paths", "need_bytes", "free_bytes",
                                         "floor_bytes", "result"})
            self.assertEqual(item["result"], "ok")
            self.assertEqual(item["floor_bytes"], 1024 * 1024)
        text = path.read_text()
        for secret in (SECRET, "POSTGRES_PASSWORD", "partflow_staging:"):
            self.assertNotIn(secret, text)
        code, out, err, _ = self.main("status", "--operation", op)
        self.assertIn("capacity: verifying device", out)

    def test_cp6_preflight_refusals_change_nothing(self):
        """CP-6: after a capacity refusal of every kind's preflight (backup, reset-db, instance purge, cleanup with a
        generation): zero deletion children; the backups/recovery/artifacts trees are
        byte-identical; no plan was written."""
        generation, _ = Cleanup.retained_generation(self)
        config = self.paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1 << 30)) + "\n")
        for arguments in (("backup",), ("reset-db",), ("purge", "--keep-backups"),
                          ("cleanup", "--apply", "--generation", generation)):
            with self.subTest(arguments[0]):
                operations = len(pfx.operations_of(self.context))
                before = pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"], self.context.artifacts_dir)
                self.fake.clear_calls()
                code, out, err, phrases = self.main(*arguments)
                self.assertEqual(code, 1, out + err)
                self.assertIn("capacity-insufficient:", err)
                self.assertEqual(phrases, [])
                self.assertEqual(len(pfx.operations_of(self.context)), operations)
                self.assertEqual(pfx.snapshot_tree(self.paths["backups"], self.paths["recovery"],
                                                   self.context.artifacts_dir), before)
                self.assert_nothing_deleted(before)

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


    def launch_scripted(self, arguments, *, timeout=300):
        """``pf --instance staging <arguments>`` through the installed launcher at a scripted terminal: every typed
        confirmation the program asks ("Type exactly '<phrase>'") is answered with that exact phrase once it is
        printed (the instance purge's random ERASE challenge included)."""
        import pty
        environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
        out_path, err_path = self.base / "cli-out.txt", self.base / "cli-err.txt"
        try:
            master, slave = pty.openpty()
        except OSError as exc:
            raise unittest.SkipTest("pty unavailable: " + str(exc))
        answered = []
        try:
            with open(str(out_path), "wb") as out, open(str(err_path), "wb") as err:
                process = subprocess.Popen([str(self.layout.launcher), "--instance", "staging", *arguments],
                                           env=environment, cwd=str(self.layout.root.parent), stdin=slave,
                                           stdout=out, stderr=err)
                deadline = __import__("time").monotonic() + timeout
                try:
                    while process.poll() is None and __import__("time").monotonic() < deadline:
                        asked = re.findall(r"Type exactly '([^']+)': ", out_path.read_text(errors="replace"))
                        for phrase in asked[len(answered):]:
                            os.write(master, (phrase + "\n").encode("utf-8"))
                            answered.append(phrase)
                        __import__("time").sleep(0.05)
                    code = process.wait(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        finally:
            os.close(slave)
            os.close(master)
        result = types.SimpleNamespace(returncode=code, stdout=out_path.read_text(), stderr=err_path.read_text(),
                                       phrases=answered)
        self.transcripts.append({"argv": ["pf", *arguments], "exit": code, "typed": answered,
                                 "stdout": result.stdout, "stderr": result.stderr})
        return result

    def test_xc1_an_instance_purge_end_to_end_and_the_tombstone_line(self):
        """XC-1: ``pf purge --keep-backups`` through the installed launcher to completion (PURGE and the random ERASE
        challenge typed at the scripted terminal), then ``pf status`` prints the tombstone line."""
        result = self.launch_scripted(["purge", "--keep-backups"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((result.phrases[0], result.phrases[-1].split()[:2]), ("PURGE " + self.project,
                                                                              ["ERASE", self.project]))
        op, plan, journal = pfx.operations_of(self.context, "purge")[-1]
        self.assertEqual(journal["phase"], "completed")
        bundle = next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")
        self.assertIn(f"Final bundle {bundle}: functional recovery verified in isolated topology pfverify-",
                      result.stdout)
        state = self.fake.state()
        self.assertFalse([item for item in state["containers"] + state["volumes"] + state["networks"]
                          if "pfverify-" in json.dumps(item) or self.project in json.dumps(item.get("labels"))])
        self.assertEqual(json.loads(self.context.record_path.read_bytes())["state"], "purged")
        status, _ = self.launch(["status"])  # partial (exit 1): the purged instance has no .env or database
        self.assertIn(f"Lifecycle: purged by instance purge {op}", status.stdout)
        self.assertIn(f"recovery bundle {bundle} [functional]", status.stdout)
        evidence("XC-1", {"transcripts": self.transcripts})

    def test_xc3_side_by_side_status_and_cleanup_through_the_launcher(self):
        """XC-3: ``pf restore-instance <id> --side-by-side``, ``pf status`` and ``pf cleanup --apply
        --recovery-target <p>`` through the installed launcher (after an instance purge and an exact restore)."""
        result = self.launch_scripted(["purge", "--keep-backups"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = pfx.operations_of(self.context, "purge")[-1][2]
        bundle = next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "purge-bundle")
        result = self.launch_scripted(["restore-instance", bundle])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.launch_scripted(["restore-instance", bundle, "--side-by-side"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.phrases, ["RESTORE COPY " + bundle])
        op, plan, journal = pfx.operations_of(self.context, "restore-side-by-side")[-1]
        self.assertEqual(journal["phase"], "completed")
        project = next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "recovery-target")
        status, _ = self.launch(["status"])
        self.assertIn(f"Recovery targets: {project} from bundle {bundle} (operation {op}", status.stdout)
        result = self.launch_scripted(["cleanup", "--apply", "--recovery-target", project])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.phrases[1:], ["REMOVE RECOVERY TARGET " + project])
        state = self.fake.state()
        self.assertFalse([item for item in state["containers"] + state["volumes"] + state["networks"]
                          if project in json.dumps(item)])
        self.assertEqual(sorted(item["labels"][pf_docker.COMPOSE_SERVICE_LABEL] for item in state["containers"]
                                if item["status"] == "running"), ["backend", "db", "frontend"])
        evidence("XC-3", {"transcripts": self.transcripts})

    def test_xc7_abort_deploy_after_the_frontend_opened_through_the_launcher(self):
        """XC-7: ``pf abort-deploy`` of a first deployment whose frontend effect started, through the installed
        launcher: the current database is preserved first (checkpoint before-abort), then the deployment's
        containers and volume are deleted."""
        (self.context.state_dir / "deployed.json").unlink()
        pfx.interrupted_deploy(self.context, phase="activating", unknown="e0006")
        result = self.launch_scripted(["abort-deploy"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.phrases, ["ABORT DEPLOY " + self.project])
        op, plan, journal = pfx.operations_of(self.context, "abort-deploy")[-1]
        self.assertEqual(journal["phase"], "completed")
        self.assertEqual(plan["effects"][1]["target"], "checkpoint:before-abort")
        checkpoint = next(item["name"] for item in journal["retained_artifacts"] if item["kind"] == "checkpoint")
        self.assertTrue((self.context.paths.backups / "revisions" / self.project / checkpoint).exists())
        calls = [call["argv"] for call in self.fake.calls()]
        dumps = [index for index, argv in enumerate(calls) if "pg_dump" in argv]
        removals = [index for index, argv in enumerate(calls) if argv[:2] == ["volume", "rm"]]
        self.assertTrue(dumps and removals and max(dumps) < min(removals), (dumps, removals))
        self.assertFalse([item for item in self.fake.state()["volumes"]
                          if item["name"] == self.project + "_postgres_data"])
        evidence("XC-7", {"transcripts": self.transcripts})


for _name in dir(tops.CliLifecycle):
    if _name.startswith("test"):
        setattr(InstalledCli, _name, None)  # not collected: the CL-* cases run in test_operations


class CapacityUpdate(tops.Restartable):
    """CP-4 (update): the preflight passes and the Docker root is short at the ``migrating`` re-check: the update
    stops before migrating (fail-closed, nothing migrated); ``pf resume`` reopens the unchanged deployment."""

    def setUp(self):
        super().setUp()
        self.stack = contextlib.ExitStack()
        self.controller_patches = []
        self.history = []

    def tearDown(self):
        self.stack.close()
        super().tearDown()

    def test_cp4_an_update_short_before_migrating_fails_closed_and_resume_reopens_unchanged(self):
        self.c.new_migration = True
        controller_class = pf.Controller  # invoke() replaces pf.Controller by a factory mock
        original = controller_class.capacity_decide

        def decide(controller, needs, *, phase=None, tail="Nothing was changed."):
            if phase == "migrating":
                with mock.patch.object(controller_class, "measure", lambda c, path, role: (9, 0)):
                    return original(controller, needs, phase=phase, tail=tail)
            return original(controller, needs, phase=phase, tail=tail)

        with mock.patch.object(pf.Controller, "capacity_decide", decide):
            self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        self.assertIn("capacity-insufficient: migrating needs", self.last_error)
        self.assertIn("The operation stopped before migrating.", self.last_error)
        op, plan, journal = tops.tpa.latest_operation(self.c, "update")
        self.assertNotIn(journal["phase"], pf_config.CLOSED_PHASES)
        for effect in plan["effects"]:
            if effect["phase"] not in ("preparing", "preserving"):
                self.assertEqual(pf_config.effect_state(journal, effect["effect_id"]), "not_started", effect)
        self.assertEqual(self.alembic_calls(), [])
        self.assertFalse(self.c.running["backend"] or self.c.running["frontend"])  # failed closed
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r1"])
        self.restart()
        self.assertEqual(self.invoke(["resume"]), 0, self.last_error)
        op, plan, journal = tops.tpa.latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "cancelled")
        self.assertTrue(all(self.c.running.values()))
        self.assertEqual(self.c.dbs["partflow_staging"]["heads"], ["r1"])
        self.assertEqual(self.pointer()["sha"], pfx.OLD)


if __name__ == "__main__":
    unittest.main()
