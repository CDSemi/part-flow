"""PF-A1.3 acceptance and regression tests: daemon binding, Compose envelope, exact resource inventory,
frozen closed deletion plan, the command() effect guard and the release wiring.

Case mapping (PF-A1.3 SPEC section 6.1; design r3 acceptance cases):
  A1-T13/T16/T17 daemon binding        -> DaemonInfoParsing (pure), DaemonBinding (DB-1..DB-14)
  A1-T14/T18 Compose envelope          -> EnvelopeRules (pure, CE-1/CE-2/CE-12), ComposeEnvelope (CE-2..CE-11)
  A1-T11/T12 exact inventory and plan  -> InventoryRules, PlanRules (pure), ResourceInventory (RI-1..RI-24)
  owner decision A12r2-F03             -> CommandEffectGuard (CG-1..CG-7)
  release wiring                       -> ReleaseWiring (RW-1..RW-6)

Docker is never contacted: the registered ``docker`` tool is ``tests/fake_docker.py`` (state in a JSON
file, every call recorded) or a fixture shell script. Nothing here touches a real daemon, a real NAS
or the running PartFlow stack.
"""
import ast
import contextlib
import copy
import grp
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
import uuid
import warnings
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_docker = pf.pf_docker
pf_config = pf.pf_config
pf_instance = pfx.pf_instance
pf_bootstrap = pfx.pf_bootstrap
fake_docker = pfx.load_fake_docker_module()
PACKAGE = pfx.PACKAGE
GROUP = grp.getgrgid(os.getgid()).gr_name
ROOT_REQUIRED = unittest.skipUnless(os.geteuid() == 0, "protected fixtures require root ownership (uid 0)")
PROJECT = "partflow"
INSTANCE = "3f0c6a8e-1b2d-4c5e-9f00-a1b2c3d4e5f6"
OTHER_INSTANCE = "7a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
ROOT = "/volume1/partflow/repo"
VALUES = {
    "POSTGRES_USER": "partflow_staging", "POSTGRES_PASSWORD": "abc123", "POSTGRES_DB": "partflow_staging",
    "SITE_TIMEZONE": "America/Los_Angeles", "PARTFLOW_BIND_IP": "127.0.0.1", "PARTFLOW_HTTP_PORT": "5173",
    "PARTFLOW_ALLOWED_HOST": "localhost",
}
PROBE = ["info", "--format", "{{json .}}"]


# ============================================================================ pure helpers


def child(values=None, root=ROOT, instance=INSTANCE):
    return pf_config.child_values(dict(values or VALUES), workspace=root, instance_id=instance)


def expectation(values=None, *, images=None, root=ROOT):
    values = child(values, root)
    return pf_docker.ComposeExpectation(
        project=PROJECT, instance_id=INSTANCE, repo_root=values["PARTFLOW_REPO_ROOT"],
        values=types.MappingProxyType({key: values[key] for key in pf_config.APP_KEYS}),
        database_url=values["PARTFLOW_DATABASE_URL"], images=images)


def model(values=None, *, mode=None, healthcheck_mode=None, override_text=None, patches=(), root=ROOT):
    return fake_docker.render_model(pfx.COMPOSE_FIXTURE, child(values, root), PROJECT, mode=mode,
                                    healthcheck_mode=healthcheck_mode, override_text=override_text, patches=patches)


def codes(error):
    return {finding.code for finding in error.findings}


def ctx(instance_id=INSTANCE, project=PROJECT):
    return types.SimpleNamespace(instance_id=instance_id, compose_project=project)


def parse(kind, items):
    return pf_docker.parse_field_lines("\n".join(json.dumps(item) for item in items), kind=kind)


def classify(containers=(), volumes=(), networks=(), images=(), *, project=PROJECT, instance_id=INSTANCE):
    return pf_docker.classify_inventory(
        project=project, instance_id=instance_id, containers=parse("container", containers),
        volumes=parse("volume", volumes), networks=parse("network", networks),
        images=[{"reference": tag, "id": item["id"], "labels": item["labels"]}
                for item in images for tag in item["repo_tags"]])


def topology(context=None, prefix="a"):
    return pfx.owned_topology(context or ctx(), prefix=prefix)


def classify_topology(*fragments, **kwargs):
    merged = {"containers": [], "volumes": [], "networks": [], "images": []}
    for fragment in fragments:
        for key in merged:
            merged[key] += fragment.get(key, [])
    return classify(merged["containers"], merged["volumes"], merged["networks"], merged["images"], **kwargs)


DAEMON = {"endpoint": "unix:///run/docker.sock", "engine_id": pfx.ENGINE_ID}


# ============================================================================ pure: daemon


class DaemonInfoParsing(unittest.TestCase):
    """DB-7/DB-8/DB-9 at the rule level (the integration repeats them through the controller)."""

    binding = types.SimpleNamespace(engine_id=pfx.ENGINE_ID)

    def parse(self, info):
        return pf_docker.parse_daemon_info(json.dumps(info), endpoint="unix:///run/docker.sock")

    def test_valid_rootful_identity_and_null_security_options(self):
        observation = self.parse(pfx.daemon_info())
        self.assertEqual((observation.engine_id, observation.rootless), (pfx.ENGINE_ID, False))
        pf_docker.check_daemon(observation, self.binding)
        observation = self.parse(dict(pfx.daemon_info(), SecurityOptions=None))
        self.assertFalse(observation.rootless)
        pf_docker.check_daemon(observation, self.binding)

    def test_unusable_identity_is_daemon_info_invalid(self):
        variants = {
            "invalid JSON": "{",
            "duplicate key": '{"ID": "a", "ID": "b", "SecurityOptions": []}',
            "not an object": "[]",
            "missing ID": json.dumps({k: v for k, v in pfx.daemon_info().items() if k != "ID"}),
            "empty ID": json.dumps(dict(pfx.daemon_info(), ID="")),
            "missing SecurityOptions": json.dumps({k: v for k, v in pfx.daemon_info().items() if k != "SecurityOptions"}),
            "string SecurityOptions": json.dumps(dict(pfx.daemon_info(), SecurityOptions="name=rootless")),
            "object SecurityOptions": json.dumps(dict(pfx.daemon_info(), SecurityOptions={"name": "rootless"})),
        }
        for label, text in variants.items():
            with self.subTest(label):
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.parse_daemon_info(text, endpoint="unix:///run/docker.sock")
                self.assertEqual(caught.exception.code, "daemon-info-invalid")

    def test_server_errors_are_unreachable_and_rootless_or_drift_are_refused(self):
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            self.parse(dict(pfx.daemon_info(), ServerErrors=["error during connect"]))
        self.assertEqual(caught.exception.code, "daemon-unreachable")
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            pf_docker.check_daemon(self.parse(pfx.daemon_info(rootless=True)), self.binding)
        self.assertEqual(caught.exception.code, "daemon-rootless")
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            pf_docker.check_daemon(self.parse(pfx.daemon_info("OTHER-ENGINE")), self.binding)
        self.assertEqual(caught.exception.code, "daemon-drift")


# ============================================================================ pure: envelope


HOSTILE_RENDERS = (
    ("privileged:true", [{"op": "set", "path": ["services", "backend", "privileged"], "value": True}], "envelope-forbidden"),
    ("cap_add", [{"op": "set", "path": ["services", "backend", "cap_add"], "value": ["SYS_ADMIN"]}], "envelope-forbidden"),
    ("devices", [{"op": "set", "path": ["services", "db", "devices"], "value": [{"source": "/dev/sda", "target": "/dev/sda"}]}],
     "envelope-forbidden"),
    ("network_mode:host", [{"op": "set", "path": ["services", "frontend", "network_mode"], "value": "host"}], "envelope-forbidden"),
    ("pid:host", [{"op": "set", "path": ["services", "backend", "pid"], "value": "host"}], "envelope-forbidden"),
    ("ipc:host", [{"op": "set", "path": ["services", "backend", "ipc"], "value": "host"}], "envelope-forbidden"),
    ("userns_mode:host", [{"op": "set", "path": ["services", "db", "userns_mode"], "value": "host"}], "envelope-forbidden"),
    ("security_opt", [{"op": "set", "path": ["services", "db", "security_opt"], "value": ["seccomp=unconfined"]}],
     "envelope-forbidden"),
    ("sysctls", [{"op": "set", "path": ["services", "db", "sysctls"], "value": {"net.ipv4.ip_forward": "1"}}],
     "envelope-forbidden"),
    ("volumes_from", [{"op": "set", "path": ["services", "backend", "volumes_from"], "value": ["db"]}], "envelope-forbidden"),
    ("bind /var/run/docker.sock", [{"op": "set", "path": ["services", "backend", "volumes"], "value": [
        {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"}]}], "envelope-docker-socket"),
    ("bind /:/host", [{"op": "set", "path": ["services", "backend", "volumes"], "value": [
        {"type": "bind", "source": "/", "target": "/host"}]}], "envelope-bind-mount"),
    ("bind of the installation root", [{"op": "append", "path": ["services", "db", "volumes"], "value": {
        "type": "bind", "source": "/volume1/partflow-admin", "target": "/admin"}}], "envelope-bind-mount"),
    ("extra service", [{"op": "set", "path": ["services", "evil"], "value": {"image": "alpine"}}], "envelope-service-set"),
    ("missing service", [{"op": "delete", "path": ["services", "frontend"]}], "envelope-service-set"),
    ("top-level include", [{"op": "set", "path": ["include"], "value": ["/x.yaml"]}], "envelope-top-level"),
    ("top-level secrets", [{"op": "set", "path": ["secrets"], "value": {"s": {"file": "/etc/shadow"}}}], "envelope-top-level"),
    ("top-level configs", [{"op": "set", "path": ["configs"], "value": {"c": {"file": "/etc/passwd"}}}], "envelope-top-level"),
    ("external volume", [{"op": "set", "path": ["volumes", "postgres_data", "external"], "value": True}], "envelope-volume"),
    ("volume driver_opts bind device", [{"op": "set", "path": ["volumes", "postgres_data", "driver_opts"], "value": {
        "type": "none", "o": "bind", "device": "/"}}], "envelope-volume"),
    ("build context outside repo_root", [{"op": "set", "path": ["services", "backend", "build", "context"], "value": "/etc"}],
     "envelope-build-context"),
) + tuple(
    (f"build {option}", [{"op": "set", "path": ["services", "frontend", "build", option], "value": value}],
     "envelope-build-option")
    for option, value in (("additional_contexts", {"x": "/"}), ("ssh", ["default"]), ("secrets", ["s"]),
                          ("network", "host"), ("entitlements", ["network.host"]), ("privileged", True))
) + (
    ("foreign image name", [{"op": "set", "path": ["services", "backend", "image"], "value": "evil/backend:latest"}],
     "envelope-image"),
    ("host_ip:0.0.0.0", [{"op": "set", "path": ["services", "frontend", "ports", 0, "host_ip"], "value": "0.0.0.0"}],
     "envelope-port"),
    ("second port", [{"op": "append", "path": ["services", "frontend", "ports"], "value": {
        "mode": "ingress", "host_ip": "127.0.0.1", "target": 22, "published": "2222", "protocol": "tcp"}}], "envelope-port"),
    ("extra env key", [{"op": "set", "path": ["services", "backend", "environment", "EXTRA"], "value": "x"}],
     "envelope-environment"),
    ("missing label", [{"op": "delete", "path": ["services", "db", "labels"]}], "envelope-label"),
    ("wrong label", [{"op": "set", "path": ["services", "db", "labels"], "value": {pf_docker.INSTANCE_LABEL: OTHER_INSTANCE}}],
     "envelope-label"),
    ("name mismatch", [{"op": "set", "path": ["name"], "value": "other"}], "envelope-project-name"),
    ("unknown service key", [{"op": "set", "path": ["services", "db", "stop_grace_period"], "value": "1s"}],
     "envelope-unknown-key"),
    ("unrecognized healthcheck", [{"op": "set", "path": ["services", "db", "healthcheck", "test"], "value": ["CMD", "true"]}],
     "envelope-escape-unrecognized"),
)

SPECIAL_PASSWORDS = ("pa$word$$x1", 'q"uo"te1', "back\\slash1", "hash#tag1", "at@sign1", "per%cent1", "amp&ers1",
                     "less<than1", "with space1", "non-ASCII-é漢", "$$$$", "test", "data")


class EnvelopeRules(unittest.TestCase):
    """CE-1, CE-2 (rule table) and CE-12 against the recorded real-Compose fixture."""

    def test_golden_render_validates_in_both_escape_modes_with_literal_values(self):
        for password in SPECIAL_PASSWORDS:
            values = dict(VALUES, POSTGRES_PASSWORD=password)
            for mode in ("doubled", "literal"):
                with self.subTest(password=password, mode=mode):
                    result = pf_docker.validate_envelope(model(values, mode=mode, healthcheck_mode=mode),
                                                         expectation(values))
                    self.assertEqual(result.escape_mode, mode)
                    self.assertEqual(result.names, {"volume": {"postgres_data": "partflow_postgres_data"},
                                                    "network": {"default": "partflow_default"}})

    def test_double_interpolation_of_a_dollar_is_refused(self):
        values = dict(VALUES, POSTGRES_PASSWORD="pa$word$$x1")
        # Values doubled by the renderer while its healthcheck calibrates literal: one more interpolation pass.
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            pf_docker.validate_envelope(model(values, mode="doubled", healthcheck_mode="literal"), expectation(values))
        self.assertIn("envelope-environment", codes(caught.exception))
        self.assertNotIn("pa$word", str(caught.exception))  # keys and paths only, never values

    def test_hostile_models_are_refused_with_the_stated_code(self):
        for label, patches, code in HOSTILE_RENDERS:
            with self.subTest(label):
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.validate_envelope(model(patches=patches), expectation())
                self.assertIn(code, codes(caught.exception))
                paths = [finding.path for finding in caught.exception.findings]
                self.assertEqual(paths, sorted(paths))

    def test_image_override_round_trip_and_exact_grammar(self):
        images = {"backend": PROJECT + "-backend:candidate-abc", "frontend": PROJECT + "-frontend:candidate-abc"}
        data = pf_docker.render_image_override(images)
        self.assertEqual(pf_docker.parse_image_override(data, project=PROJECT), images)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "override.yaml"
            pf.Controller.make_override(None, {key: {"reference": value} for key, value in images.items()}, target)
            self.assertEqual(target.read_bytes(), data)
        rendered = model(override_text=data.decode())
        pf_docker.validate_envelope(rendered, expectation(images=images))
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            pf_docker.validate_envelope(rendered, expectation(images=dict(images, backend=PROJECT + "-backend:other")))
        self.assertIn("envelope-image", codes(caught.exception))
        hostile = {
            "extra key": data.decode().replace('    image: "partflow-backend', '    privileged: true\n    image: "partflow-backend'),
            "third service": data.decode() + '  db:\n    image: "postgres:16"\n',
            "privileged": data.decode() + "    privileged: true\n",
            "foreign project": data.decode().replace("partflow-frontend", "other-frontend"),
            "comment": "# x\n" + data.decode(),
        }
        for label, text in hostile.items():
            with self.subTest(label):
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.parse_image_override(text.encode(), project=PROJECT)
                self.assertEqual(caught.exception.code, "envelope-override")

    def test_recorded_fixture_and_benign_keys_match(self):
        document = json.loads(pfx.COMPOSE_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(document["escape_mode"], "doubled")
        services = document["model"]["services"]
        extra = set().union(*(set(body) for body in services.values())) - pf_docker.SERVICE_KEYS
        self.assertEqual(extra, set(pf_docker.BENIGN_SERVICE_KEYS))
        self.assertFalse(pf_docker.BENIGN_SERVICE_KEYS & pf_docker.FORBIDDEN_SERVICE_KEYS)
        self.assertEqual(set(document["model"]["networks"]["default"]) - pf_docker.TOP_NETWORK_KEYS,
                         set(pf_docker.BENIGN_NETWORK_KEYS))
        self.assertNotIn("image", services["backend"])
        self.assertEqual(services["db"]["healthcheck"]["test"], pf_docker.HEALTHCHECK_DOUBLED)


# ============================================================================ pure: inventory


class InventoryRules(unittest.TestCase):
    """RI-2/3/8/9/11/12/15/22 at the rule level; prefixes never expand the set (A1-T11)."""

    def test_prefix_siblings_are_never_candidates(self):
        sibling = ctx(OTHER_INSTANCE, PROJECT + "_test")
        inventory = classify_topology(topology(), topology(sibling, "b"))
        self.assertFalse(inventory.blockers)
        self.assertEqual({item.key for item in inventory.owned if item.kind == "volume"}, {"partflow_postgres_data"})
        self.assertEqual({item.key for item in inventory.owned if item.kind == "network"}, {"partflow_default"})
        self.assertEqual(len(inventory.owned_of("container")), 3)
        self.assertTrue(all("partflow_test" not in item.key for item in inventory.owned))
        self.assertTrue(inventory.present_topology)

    def test_unlabelled_topology_volume_is_legacy_or_a_name_collision(self):
        for labels, code in (({pf_docker.COMPOSE_PROJECT_LABEL: PROJECT}, "resource-legacy-unlabeled"),
                             ({}, "resource-name-collision")):
            with self.subTest(code):
                inventory = classify(volumes=[pfx.volume("partflow_postgres_data", labels)])
                self.assertEqual([item.cls for item in inventory.blockers], [code])
                self.assertTrue(inventory.present_topology)
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.plan_deletion(inventory, kind="purge", operation_id="op", daemon=DAEMON)
                self.assertEqual(caught.exception.code, "resource-blocked")

    def test_foreign_claims_and_label_conflicts_block(self):
        other = ctx(OTHER_INSTANCE)
        inventory = classify(
            containers=[pfx.container("c" * 64, "partflow-db-1", pfx.labels_for(other, "db"))],
            volumes=[pfx.volume("partflow_postgres_data", dict(pfx.labels_for(other), **{
                pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"}))],
            networks=[pfx.network("n" * 64, "partflow_default", dict(pfx.labels_for(ctx()), **{
                pf_docker.COMPOSE_NETWORK_LABEL: "other"}))])
        self.assertEqual(sorted(item.cls for item in inventory.blockers),
                         ["resource-foreign-claim", "resource-foreign-claim", "resource-label-conflict"])

    def test_unsupported_driver_blocks(self):
        labels = dict(pfx.labels_for(ctx()), **{pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"})
        inventory = classify(volumes=[pfx.volume("partflow_postgres_data", labels, driver="nfs")])
        self.assertEqual([item.cls for item in inventory.blockers], ["resource-unsupported-driver"])

    def test_inherited_image_labels_without_compose_markers_are_a_label_conflict(self):
        labels = pfx.labels_for(ctx(), "backend", markers=False)  # `docker run <partflow image>`
        inventory = classify(containers=[pfx.container("d" * 64, "adhoc", labels)])
        self.assertEqual([item.cls for item in inventory.blockers], ["resource-label-conflict"])
        self.assertFalse(inventory.owned)

    def test_reference_volume_bind_mount_and_owned_volume_outside_topology_are_retained(self):
        base = topology()
        db = base["containers"][0]
        db["mounts"].append({"Type": "volume", "Name": "shared_data", "Source": "/v", "Destination": "/s", "RW": True})
        runner = pfx.container("e" * 64, "partflow-backend-run-1", pfx.labels_for(ctx(), "backend", oneoff="True"),
                               binds=("/volume1/exports",))
        base["containers"].append(runner)
        base["volumes"].append(pfx.volume("partflow_cache", pfx.labels_for(ctx())))
        inventory = classify_topology(base)
        classes = {(item.kind, item.key): item.cls for item in inventory.excluded}
        self.assertEqual(classes[("volume", "shared_data")], "reference")
        self.assertEqual(classes[("bind", "/volume1/exports")], "bind-retained")
        self.assertEqual(classes[("volume", "partflow_cache")], "owned-outside-topology")
        self.assertEqual(inventory.bind_paths, ("/volume1/exports",))
        self.assertIn("e" * 64, {item.key for item in inventory.owned})
        plan = pf_docker.plan_deletion(inventory, kind="purge", operation_id="op", daemon=DAEMON, covered_image_refs=set())
        keys = {(item["kind"], item["key"]) for item in plan["candidates"]}
        self.assertNotIn(("volume", "shared_data"), keys)
        self.assertNotIn(("volume", "partflow_cache"), keys)
        self.assertEqual(plan["bind_paths"], ["/volume1/exports"])
        legacy = pfx.container("f" * 64, "legacy", {pf_docker.COMPOSE_PROJECT_LABEL: PROJECT}, binds=("/srv",))
        self.assertEqual([item.cls for item in classify(containers=[legacy]).blockers], ["resource-legacy-unlabeled"])

    def test_stopped_foreign_user_shares_the_owned_volume_or_network(self):
        base = topology()
        foreign = pfx.container("9" * 64, "other-app", {}, status="exited", volumes=("partflow_postgres_data",),
                                networks=(("partflow_default", "a" * 64),))
        base["containers"].append(foreign)
        inventory = classify_topology(base)
        self.assertEqual(sorted((item.kind, item.cls) for item in inventory.blockers),
                         [("network", "resource-shared"), ("volume", "resource-shared")])
        self.assertIn("9" * 64, inventory.users_of("volume", "partflow_postgres_data"))

    def test_image_tags_need_label_grammar_and_no_foreign_user(self):
        label = {pf_docker.INSTANCE_LABEL: INSTANCE}
        images = [pfx.image("sha256:1", ["partflow-backend:candidate-1", "otherapp:latest"], label),
                  pfx.image("sha256:2", ["partflow-frontend:backup-2"], label),
                  pfx.image("sha256:3", ["partflow-backend:candidate-3"], {})]
        foreign = pfx.container("8" * 64, "x", {}, config_image="partflow-frontend:backup-2")
        inventory = classify(containers=[foreign], images=images)
        excluded = {item.key: item.reason for item in inventory.excluded}
        self.assertEqual(inventory.owned_image_tags, {"partflow-backend:candidate-1"})
        self.assertEqual(excluded, {"otherapp:latest": "grammar", "partflow-frontend:backup-2": "foreign-in-use",
                                    "partflow-backend:candidate-3": "unlabelled"})

    def test_image_used_by_id_by_a_foreign_container_is_foreign_in_use(self):
        """Audit F4: a foreign container created from an image ID (config_image is the ID) keeps every
        tag of that image out of the candidates; an owned container using the same ID does not."""
        label = {pf_docker.INSTANCE_LABEL: INSTANCE}
        images = [pfx.image("sha256:1", ["partflow-backend:candidate-1", "partflow-backend:backup-1"], label),
                  pfx.image("sha256:2", ["partflow-frontend:candidate-2"], label)]
        foreign = pfx.container("8" * 64, "x", {}, status="exited", image="sha256:1", config_image="sha256:1")
        inventory = classify(containers=[foreign], images=images)
        self.assertEqual(inventory.owned_image_tags, {"partflow-frontend:candidate-2"})
        self.assertEqual({item.key: item.reason for item in inventory.excluded if item.kind == "image"},
                         {"partflow-backend:candidate-1": "foreign-in-use", "partflow-backend:backup-1": "foreign-in-use"})
        owned_user = topology()
        self.assertEqual(len(classify_topology(owned_user).owned_image_tags), 2)

    def test_container_fields_never_request_the_environment(self):
        """RI-19 (static)."""
        for template in (pf_docker.CONTAINER_FIELDS, pf_docker.VOLUME_FIELDS, pf_docker.NETWORK_FIELDS,
                         pf_docker.IMAGE_FIELDS):
            self.assertNotIn("Env", template)
        with self.assertRaises(pf_docker.DockerScopeError):
            pf_docker.parse_field_lines('{"id":"a","id":"b"}', kind="network")
        with self.assertRaises(pf_docker.DockerScopeError):
            pf_docker.parse_field_lines(json.dumps(dict(pfx.network("a", "n", {}), env=["X=1"])), kind="network")


# ============================================================================ pure: plan


class PlanRules(unittest.TestCase):
    """compare_plans (RI-21), coverage by image ID (RI-20), identity outcomes (RI-5/6/7), load_plan (RI-13)."""

    def plan(self, inventory, **kwargs):
        kwargs.setdefault("covered_image_refs", set())
        return pf_docker.plan_deletion(inventory, kind="purge", operation_id="op-1", daemon=DAEMON, **kwargs)

    def test_coverage_by_reference_and_image_id(self):
        base = topology()
        base["images"][0]["repo_tags"].append("partflow-backend:backup-old-inspect-1a2b3c")
        base["images"].append(pfx.image("sha256:zz", ["partflow-frontend:backup-stale"], {pf_docker.INSTANCE_LABEL: INSTANCE}))
        inventory = classify_topology(base)
        covered = {"partflow-backend:candidate-a00000000000-abcdef", "partflow-frontend:candidate-a00000000000-abcdef"}
        plan = self.plan(inventory, covered_image_refs=covered)
        images = [item["key"] for item in plan["candidates"] if item["kind"] == "image"]
        self.assertIn("partflow-backend:backup-old-inspect-1a2b3c", images)  # same image ID as a saved ref
        self.assertEqual({entry["key"]: entry["reason"] for entry in plan["exclusions"] if entry["kind"] == "image"},
                         {"partflow-frontend:backup-stale": "not-covered"})
        self.assertEqual([item["kind"] for item in plan["candidates"]],
                         ["container"] * 3 + ["network", "volume"] + ["image"] * 3)
        advisory = pf_docker.plan_deletion(inventory, kind="purge", operation_id="op-1", daemon=DAEMON)
        self.assertEqual(advisory["image_coverage"], "pending")
        self.assertEqual(len(advisory["pending_images"]), 4)
        abort = pf_docker.plan_deletion(inventory, kind="abort-deploy", operation_id="op-1", daemon=DAEMON)
        self.assertEqual(abort["image_coverage"], "none")
        self.assertFalse([item for item in abort["candidates"] if item["kind"] == "image"])
        self.assertTrue(all(entry["reason"] == "abort-retains-images" for entry in abort["exclusions"]
                            if entry["kind"] == "image"))

    def test_compare_plans_ignores_only_tags_this_operation_created(self):
        base = topology()
        preliminary = pf_docker.plan_deletion(classify_topology(base), kind="purge", operation_id="op", daemon=DAEMON)
        created = "partflow-backend:backup-now"
        later = copy.deepcopy(base)
        later["images"][0]["repo_tags"].append(created)
        binding = self.plan(classify_topology(later), covered_image_refs={created})
        pf_docker.compare_plans(preliminary, binding, created_image_refs={created})
        with self.assertRaises(pf_docker.DockerScopeError) as caught:
            pf_docker.compare_plans(preliminary, binding, created_image_refs=set())
        self.assertEqual(caught.exception.code, "plan-changed")
        for label, mutate in (
                ("container appears", lambda state: state["containers"].append(pfx.container(
                    "7" * 64, "partflow-backend-2", pfx.labels_for(ctx(), "backend")))),
                ("container disappears", lambda state: state["containers"].pop()),
                ("volume identity changes", lambda state: state["volumes"][0].update(created_at="2026-10-07T00:00:00Z"))):
            with self.subTest(label):
                state = copy.deepcopy(base)
                mutate(state)
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.compare_plans(preliminary, self.plan(classify_topology(state)), created_image_refs=set())
                self.assertEqual(caught.exception.code, "plan-changed")

    def test_identity_outcomes(self):
        plan = self.plan(classify_topology(topology()))
        volume_item = next(item for item in plan["candidates"] if item["kind"] == "volume")
        container_item = plan["candidates"][0]
        same = classify_topology(topology())
        self.assertEqual(pf_docker.compare_identity(volume_item, *same.index("volume")), "identical")
        recreated = topology()
        recreated["volumes"][0]["created_at"] = "2026-10-07T00:00:00Z"
        outcome = pf_docker.compare_identity(volume_item, *classify_topology(recreated).index("volume"))
        self.assertEqual(outcome[0], "drift")
        relabelled = topology()
        relabelled["volumes"][0]["options"] = {"type": "none"}
        self.assertEqual(pf_docker.compare_identity(volume_item, *classify_topology(relabelled).index("volume"))[0], "drift")
        replaced = topology()
        replaced["containers"][0]["id"] = "5" * 64
        self.assertEqual(pf_docker.compare_identity(container_item, *classify_topology(replaced).index("container"))[0],
                         "drift")
        gone = topology()
        gone["containers"].pop(0)
        self.assertEqual(pf_docker.compare_identity(container_item, *classify_topology(gone).index("container")), "absent")
        volume_users = pf_docker.users_violations(plan, volume_item, ["4" * 64])
        self.assertEqual(len(volume_users), 1)
        self.assertEqual(pf_docker.users_violations(plan, volume_item, [container_item["key"]]), ())

    def test_load_plan_verifies_bytes_hash_instance_kind_operation_and_coverage(self):
        inventory = classify_topology(topology())
        plan = self.plan(inventory)
        data = pf_docker.plan_bytes(plan)
        digest = pf_docker.plan_sha256(plan)
        self.assertEqual(pf_docker.load_plan(data, expected_sha256=digest, instance_id=INSTANCE, kind="purge",
                                             operation_id="op-1"), plan)
        tampered = data.replace(b'"partflow_default"', b'"partflow_defauLt"', 1)
        variants = {
            "tampered byte": (tampered, digest, INSTANCE, "purge", "op-1"),
            "not canonical": (data.replace(b"\n", b" \n"), pf_docker.sha256_bytes(data.replace(b"\n", b" \n")),
                              INSTANCE, "purge", "op-1"),
            "other instance": (data, digest, OTHER_INSTANCE, "purge", "op-1"),
            "wrong kind": (data, digest, INSTANCE, "abort-deploy", "op-1"),
            "wrong operation": (data, digest, INSTANCE, "purge", "op-2"),
        }
        for label, (blob, expected, instance_id, kind, operation) in variants.items():
            with self.subTest(label):
                with self.assertRaises(pf_docker.DockerScopeError) as caught:
                    pf_docker.load_plan(blob, expected_sha256=expected, instance_id=instance_id, kind=kind,
                                        operation_id=operation)
                self.assertEqual(caught.exception.code, "plan-invalid")
        pending = pf_docker.plan_deletion(inventory, kind="purge", operation_id="op-1", daemon=DAEMON)
        with self.assertRaises(pf_docker.DockerScopeError):
            pf_docker.load_plan(pf_docker.plan_bytes(pending), expected_sha256=pf_docker.plan_sha256(pending),
                                instance_id=INSTANCE, kind="purge", operation_id="op-1")


# ============================================================================ integration base


def run_main(arguments, layout, *, env=None, interactive=False):
    """In-process CLI. ``interactive=True`` simulates an operator terminal (PF-A1.4 unattended gate);
    the default keeps the real ``unattended()``."""
    stdout, stderr = io.StringIO(), io.StringIO()
    terminal = mock.patch.object(pf, "unattended", return_value=False) if interactive else contextlib.nullcontext()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), terminal:
        if env is None:
            code = pf.main(arguments, installation_root=layout.root, running_release=layout.release_dir,
                           trusted_launch=True)
        else:
            with mock.patch.dict(os.environ, env, clear=True):
                code = pf.main(arguments, installation_root=layout.root, running_release=layout.release_dir,
                               trusted_launch=True)
    return code, stdout.getvalue(), stderr.getvalue()


def launcher_run(layout, arguments, env=None, *, interactive=False):
    environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
    environment.update(env or {})
    with (pfx.interactive_stdin() if interactive else contextlib.nullcontext(subprocess.DEVNULL)) as stdin:
        return subprocess.run([str(layout.launcher), *arguments], env=environment, cwd=str(layout.root.parent),
                              stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
                              timeout=300)


class ScopeBase(unittest.TestCase):
    project = PROJECT

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.layout = pfx.install_root(self.base)
        self.fake = pfx.install_fake_docker(self.layout)
        self.context, self.paths = self.instance("staging", self.project)

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

    def instance(self, slug, project):
        paths = pfx.data_home(self.base / slug, project=project, group=GROUP)
        config = paths["configuration"] / "pf-config.json"
        config.write_text(json.dumps(dict(json.loads(config.read_text()), minimum_free_mb=1)) + "\n")
        context = pfx.register(self.layout, slug, paths, project=project)
        pfx.deployed_record(context)
        return context, paths

    def controller(self, context=None):
        context = context or self.context
        validation = pf_instance.validate_context(context, running_release=self.layout.release_dir)
        self.assertTrue(validation.mutation_allowed, validation.blocking_messages())
        return pf.Controller(context, validation=validation, running_release=self.layout.release_dir)

    def state(self, *fragments, **extra):
        state = pfx.default_docker_state()
        for fragment in fragments:
            for key in ("containers", "volumes", "networks", "images"):
                state[key] += copy.deepcopy(fragment.get(key, []))
        state.update(extra)
        self.fake.write_state(state)
        self.fake.clear_calls()
        return state

    def main(self, *arguments, confirm=None, slug="staging"):
        confirmations = []

        def record(phrase, warning):
            confirmations.append(phrase)
            if confirm is not None:
                confirm(phrase)

        with mock.patch.object(pf, "confirm", side_effect=record), \
                mock.patch.object(pf, "prompt_yes_no", return_value=False):
            # The patched confirmation is an operator at a terminal (interactive harness, PF-A1.4).
            code, out, err = run_main(["--instance", slug, *arguments], self.layout, interactive=True)
        for gate in ("terminal-required", "instance-required-unattended", "policy-grant-required"):
            self.assertNotIn(gate, err)  # no test passes vacuously on an unattended-gate refusal
        return code, out, err, confirmations

    def mutations(self):
        """Every recorded call that is not read-only by the controller's own classifiers."""
        return [argv for argv in self.fake.argvs() if pf.unclassified_mutation("docker", argv) is not None]

    def prepare_restore_bundle(self):
        recovery_id = "purge-20261006T000000Z-" + pfx.OLD[:12] + "-abcdef"
        folder = self.context.paths.recovery / PROJECT / recovery_id
        folder.mkdir(parents=True)
        manifest = {"format": 2, "kind": "partflow-purge-recovery", "status": "complete", "id": recovery_id,
                    "project": PROJECT, "root": str(self.context.paths.workspace), "postgres_major": 16,
                    "checksums": {}}
        pf.write_json(folder / "manifest.json", manifest)
        (folder / "manifest.sha256").write_text(pf.digest(folder / "manifest.json") + "\n")
        return recovery_id

    def operation_dirs(self):
        root = self.context.operations_dir
        return set(os.listdir(str(root))) if root.exists() else set()

    def operation_files(self, names):
        return {name: sorted(os.listdir(str(self.context.operations_dir / name))) for name in names}


def refuse_confirmation(phrase):
    raise AssertionError("confirmation must not be asked: " + phrase)


# ============================================================================ DB: daemon binding


@ROOT_REQUIRED
class DaemonBinding(ScopeBase):
    """A1-T13, A1-T16, A1-T17: endpoint canonicalization and trust, engine identity before any mutation."""

    def drift(self):
        state = self.fake.state()
        state["info"] = pfx.daemon_info("OTHER-ENGINE-9999")
        self.fake.write_state(state)
        self.fake.clear_calls()

    def test_db1_registration_stores_the_resolved_socket(self):
        self.assertTrue(self.layout.daemon_endpoint.endswith("/var/run/docker.sock"))
        self.assertEqual(self.context.daemon.endpoint, "unix://" + str(self.base / "run" / "docker.sock"))
        record = json.loads(self.context.record_path.read_text())
        self.assertEqual(record["daemon"]["endpoint"], self.context.daemon.endpoint)

    def test_db2_registration_refuses_an_untrusted_or_missing_socket(self):
        fresh = pfx.data_home(self.base / "fresh", project="partflow-fresh", group=GROUP)
        open_dir = self.base / "open"
        open_dir.mkdir()
        os.chmod(open_dir, 0o777)

        def make_socket(path, mode=0o660, owner=None):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.close()
            os.chmod(path, mode)
            if owner is not None:
                os.chown(path, owner, owner)
            return path

        regular = self.base / "run" / "file.sock"
        regular.write_text("not a socket")
        variants = {
            "missing socket": (self.base / "run" / "absent.sock", "daemon-endpoint-missing"),
            "regular file": (regular, "daemon-endpoint-not-socket"),
            "0777 non-sticky parent": (make_socket(open_dir / "d.sock"), "daemon-endpoint-ancestor-replaceable"),
            "uid 1000 owner": (make_socket(self.base / "run" / "user.sock", owner=1000), "daemon-endpoint-owner"),
            "0777 socket": (make_socket(self.base / "run" / "open.sock", mode=0o777), "daemon-endpoint-writable"),
        }
        reservations = self.layout.root / "registry" / "reservations"
        before = (sorted(os.listdir(str(reservations))), sorted(os.listdir(str(self.layout.root / "instances"))))
        for label, (path, code) in variants.items():
            with self.subTest(label):
                spec = pfx.registration_spec(self.layout, "fresh", fresh, project="partflow-fresh")
                spec["daemon"]["endpoint"] = "unix://" + str(path)
                with self.assertRaisesRegex(pf_instance.ContextError, code):
                    pf_instance.register_instance(self.layout.root, spec)
                self.assertEqual((sorted(os.listdir(str(reservations))),
                                  sorted(os.listdir(str(self.layout.root / "instances")))), before)

    def publish_endpoint(self, endpoint):
        record = json.loads(self.context.record_path.read_text())
        record["daemon"]["endpoint"] = endpoint
        os.chmod(self.context.record_path, 0o600)
        self.context.record_path.write_bytes(pf_instance.normalize_json(record))
        os.chmod(self.context.record_path, 0o600)

    def test_db3_a_symlinked_endpoint_in_a_published_record_is_refused_offline(self):
        self.publish_endpoint(self.layout.daemon_endpoint)  # through <base>/var/run -> ../run
        code, out, err, _ = self.main("status")
        self.assertEqual(code, 1)
        self.assertIn("daemon-endpoint-symlink", out)
        self.assertEqual(self.fake.calls(), [])
        code, out, err, _ = self.main("doctor")
        self.assertEqual(code, 1)
        self.assertIn("contains a symbolic link at " + str(self.base / "var" / "run"), out)
        self.assertEqual(self.fake.calls(), [])

    def test_db14_missing_or_symlinked_ancestor_is_still_a_refusal(self):
        alias = self.base / "alias"
        alias.symlink_to(self.base / "run")
        for endpoint, code in (("unix://" + str(self.base / "absent-dir" / "docker.sock"), "daemon-endpoint-ancestor-missing"),
                               ("unix://" + str(alias / "docker.sock"), "daemon-endpoint-symlink")):
            with self.subTest(endpoint):
                self.publish_endpoint(endpoint)
                registry = pf_instance.load_registry(self.layout.root)
                context = pf_instance.resolve_instance(registry, instance="staging")
                validation = pf_instance.validate_context(context, running_release=self.layout.release_dir)
                self.assertFalse(validation.mutation_allowed)
                self.assertIn(code, validation.refused_codes())

    def test_db4_one_probe_per_process_before_any_other_docker_child(self):
        self.state(topology(self.context))
        code, out, err, _ = self.main("status")
        argvs = self.fake.argvs()
        self.assertEqual(argvs[0], PROBE)
        self.assertEqual(argvs.count(PROBE), 1)
        self.assertIn("Docker daemon: verified | engine " + pfx.ENGINE_ID, out)
        controller = self.controller()
        with controller.lock("backup"):
            controller.docker_inventory()
            daemon = json.loads((controller.operation_dir / "daemon.json").read_text())
            self.assertEqual(stat.S_IMODE((controller.operation_dir / "daemon.json").stat().st_mode), 0o600)
        self.assertEqual((daemon["engine_id"], daemon["registered_engine_id"], daemon["result"], daemon["rootless"]),
                         (pfx.ENGINE_ID, pfx.ENGINE_ID, "verified", False))

    def test_db5_engine_drift_refuses_every_mutation_before_any_effect(self):
        self.state(topology(self.context))
        commands = {
            "backup": (["backup"], None),
            "deploy": (["deploy", "--current"], "deploy"),
            "purge": (["purge", "--keep-backups"], None),
            "abort-deploy": (["abort-deploy"], "abort"),
            "restore-instance": (["restore-instance"], "restore"),
        }
        for name, (arguments, prepare) in commands.items():
            with self.subTest(name):
                deployed = self.context.state_dir / "deployed.json"
                pending = self.context.journal_path
                if pending.exists():
                    pending.unlink()
                if prepare in ("deploy", "abort", "restore") and deployed.exists():
                    deployed.unlink()
                if prepare == "abort":
                    pf.write_json(pending, {"operation": "deploy", "phase": "migrating-database",
                                            "database": "partflow_staging", "started": "20261006T000000Z"})
                if prepare == "restore":
                    arguments = arguments + [self.prepare_restore_bundle()]
                journal_before = pending.read_bytes() if pending.exists() else None
                self.drift()
                state_before = self.fake.state_bytes()
                before = self.operation_dirs()
                code, out, err, confirmations = self.main(*arguments, confirm=refuse_confirmation)
                self.assertEqual(code, 1, err)
                self.assertIn("ERROR: daemon-drift: Docker daemon drift: instance staging is bound to engine "
                              + pfx.ENGINE_ID, err)
                self.assertIn("nothing was changed", err)
                self.assertEqual(self.fake.argvs(), [PROBE])
                self.assertEqual(confirmations, [])
                self.assertEqual(pending.read_bytes() if pending.exists() else None, journal_before)
                new = self.operation_dirs() - before
                self.assertEqual(len(new), 1)
                for files in self.operation_files(new).values():
                    self.assertTrue(set(files) <= {"operation.json", "app.env", "frozen-config.json"}, files)
                self.assertEqual(self.fake.state_bytes(), state_before)
                pfx.deployed_record(self.context)

    def test_db6_status_reports_drift_and_still_prints_every_non_docker_section(self):
        self.state(topology(self.context))
        self.drift()
        code, out, err, _ = self.main("status")
        self.assertEqual(code, 1)
        self.assertIn("Docker daemon: unavailable: daemon-drift", out)
        self.assertIn("but the endpoint answers as engine OTHER-ENGINE-9999", out)
        for section in ("Managed resources", "Database revisions", "Compose services"):
            self.assertIn(section + ": unavailable: daemon-drift", out)
        for line in ("Runtime .env: present", "Deployed source: " + pfx.OLD, "Workspace: provenance",
                     "Revision checkpoints: 0"):
            self.assertIn(line, out)
        self.assertEqual(self.fake.argvs(), [PROBE])

    def test_db7_rootless_daemon_is_refused(self):
        self.state(topology(self.context), info=pfx.daemon_info(rootless=True))
        code, out, err, _ = self.main("status")
        self.assertIn("Docker daemon: unavailable: daemon-rootless", out)
        code, out, err, _ = self.main("backup", confirm=refuse_confirmation)
        self.assertEqual(code, 1)
        self.assertIn("daemon-rootless", err)
        self.assertFalse(self.context.journal_path.exists())

    def test_db8_db9_unusable_or_unreachable_daemon_answers(self):
        good = pfx.daemon_info()
        variants = {
            "invalid JSON": ({"info_raw": "{"}, "daemon-info-invalid"),
            "missing ID": ({"info": {k: v for k, v in good.items() if k != "ID"}}, "daemon-info-invalid"),
            "missing SecurityOptions": ({"info": {k: v for k, v in good.items() if k != "SecurityOptions"}},
                                        "daemon-info-invalid"),
            "string SecurityOptions": ({"info": dict(good, SecurityOptions="name=seccomp")}, "daemon-info-invalid"),
            "object SecurityOptions": ({"info": dict(good, SecurityOptions={})}, "daemon-info-invalid"),
            "exit 1": ({"info_exit": 1, "info_stderr": "Cannot connect to the Docker daemon"}, "daemon-unreachable"),
            "ServerErrors": ({"info": dict(good, ServerErrors=["error during connect"])}, "daemon-unreachable"),
            "null SecurityOptions": ({"info": dict(good, SecurityOptions=None)}, None),
        }
        for label, (fields, code) in variants.items():
            with self.subTest(label):
                self.state(**fields)
                controller = self.controller()
                if code is None:
                    self.assertEqual(controller.verify_daemon().engine_id, pfx.ENGINE_ID)
                    continue
                with self.assertRaises(pf.DaemonFailure) as caught:
                    controller.verify_daemon()
                self.assertEqual(caught.exception.code, code)
                # Cached: a later Docker child raises it again without starting any process.
                calls = len(self.fake.calls())
                with self.assertRaises(pf.DaemonFailure):
                    controller.docker("ps")
                self.assertEqual(len(self.fake.calls()), calls)

    def test_db10_a_hostile_caller_environment_cannot_select_the_daemon(self):
        hostile_home = self.base / "hostile-home"
        (hostile_home / ".docker").mkdir(parents=True)
        (hostile_home / ".docker" / "config.json").write_text(json.dumps({"currentContext": "evil"}))
        hostile = {"DOCKER_HOST": "tcp://evil:2375", "DOCKER_CONTEXT": "evil", "HOME": str(hostile_home),
                   "DOCKER_CONFIG": str(hostile_home / ".docker"), "PATH": "/usr/bin:/bin"}
        self.state(topology(self.context))
        result = launcher_run(self.layout, ["--instance", "staging", "status"], env=hostile)
        self.assertIn("Docker daemon: verified", result.stdout, result.stderr)
        code, out, err = run_main(["--instance", "staging", "status"], self.layout, env=hostile)
        self.assertIn("Docker daemon: verified", out)
        calls = self.fake.calls()
        self.assertGreater(len(calls), 4)
        for call in calls:
            self.assertEqual(call["DOCKER_HOST"], self.context.daemon.endpoint)
            self.assertEqual(call["DOCKER_CONFIG"], str(self.context.docker_config_dir))
            self.assertFalse(call["has_DOCKER_CONTEXT"])

    def test_db12_doctor_prints_the_daemon_section_and_writes_nothing(self):
        self.state(topology(self.context))
        before = pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state)
        code, out, err, _ = self.main("doctor")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Docker daemon: verified | engine " + pfx.ENGINE_ID + " | endpoint " + self.context.daemon.endpoint,
                      out)
        self.assertNotIn("Docker: ", out)
        self.assertEqual(pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state), before)

    def test_db13_a_missing_socket_is_an_availability_note_not_a_trust_refusal(self):
        self.state(topology(self.context))
        os.unlink(str(self.layout.daemon_socket))
        validation = pf_instance.validate_context(self.context, running_release=self.layout.release_dir)
        self.assertTrue(validation.mutation_allowed, validation.blocking_messages())
        self.assertIn("daemon-endpoint-missing", {finding.code for finding in validation.findings})
        code, out, err, _ = self.main("status")
        self.assertEqual(code, 1)
        self.assertIn("Protected context: trusted", out)
        for line in ("Runtime .env: present", "Deployed source: " + pfx.OLD, "Workspace: provenance",
                     "Revision checkpoints: 0", "Docker daemon: unavailable: daemon-unreachable",
                     "Managed resources: unavailable: daemon-unreachable", "Compose services: unavailable: daemon-unreachable"):
            self.assertIn(line, out)
        self.assertIn("did not answer (socket absent)", out)
        code, out, err, _ = self.main("backup", confirm=refuse_confirmation)
        self.assertEqual(code, 1)
        self.assertIn("daemon-unreachable", err)
        self.assertFalse(self.context.journal_path.exists())
        self.assertEqual(self.fake.calls(), [])


# ============================================================================ CE: envelope


@ROOT_REQUIRED
class ComposeEnvelope(ScopeBase):
    """A1-T14, A1-T18: the resolved model of exactly the executed inputs is validated before any mutating verb."""

    def verbs(self):
        result = []
        for argv in self.fake.argvs():
            if argv[:1] == ["compose"]:
                rest = argv[1:]
                while rest and rest[0].startswith("-"):
                    rest = rest[2:]
                result.append(rest)
        return result

    def test_ce2_hostile_renders_are_refused_before_up_build_or_run(self):
        variants = [(label, {"render_patch": patches}, code) for label, patches, code in HOSTILE_RENDERS]
        variants += [("render exits 1", {"render_exit": 1}, "envelope-render-failed"),
                     ("invalid JSON", {"render_raw": "{"}, "envelope-render-failed"),
                     ("duplicate key", {"render_raw": '{"name": "a", "name": "b"}'}, "envelope-render-failed"),
                     ("over 4 MiB", {"render_size": pf_docker.RENDER_LIMIT + 1}, "envelope-render-failed")]
        for label, settings, code in variants:
            with self.subTest(label):
                state = self.state()
                state["compose"].update(settings)
                self.fake.write_state(state)
                controller = self.controller()
                with controller.lock("test"):
                    with self.assertRaises(pf.Failure) as caught:
                        controller.compose("up", "-d", "--no-deps", "db")
                    record = json.loads((controller.operation_dir / "compose-envelope.json").read_text())
                message = str(caught.exception)
                self.assertTrue(message.startswith("envelope-"), message)
                self.assertIn("Compose envelope refused for partflow", message)
                self.assertIn(code, message)
                self.assertEqual(record["renders"][-1]["result"], "refused")
                self.assertFalse([verb for verb in self.verbs() if verb[:1] in (["up"], ["build"], ["run"])])
                self.assertNotIn("abc123", message)

    def test_ce2_hand_edited_protected_override_is_refused(self):
        controller = self.controller()
        good = pf_docker.render_image_override({"backend": PROJECT + "-backend:backup-1",
                                                "frontend": PROJECT + "-frontend:backup-1"}).decode()
        for label, text in (("extra key", good + "    privileged: true\n"),
                            ("third service", good + '  db:\n    image: "postgres:16"\n'),
                            ("privileged", good.replace("  frontend:\n", "  frontend:\n    privileged: true\n"))):
            with self.subTest(label):
                self.state()
                controller.state.mkdir(exist_ok=True)
                controller.override.write_text(text)
                with controller.lock("test"):
                    with self.assertRaisesRegex(pf.Failure, "envelope-override"):
                        controller.compose("up", "-d", "--no-deps", "backend")
                self.assertFalse([verb for verb in self.verbs() if verb[:1] == ["up"]])
                self.assertFalse([verb for verb in self.verbs() if verb[:1] == ["config"]])  # refused before rendering

    def test_ce3_render_precedes_the_verb_and_is_reused_only_for_the_same_input_key(self):
        self.state()
        controller = self.controller()
        candidate = self.base / "candidate-images.yaml"
        other = self.base / "other-images.yaml"
        controller.make_override({"backend": PROJECT + "-backend:candidate-1", "frontend": PROJECT + "-frontend:candidate-1"},
                                 candidate)
        controller.make_override({"backend": PROJECT + "-backend:candidate-2", "frontend": PROJECT + "-frontend:candidate-2"},
                                 other)
        with controller.lock("test"):
            controller.compose("build", "backend", override=candidate)
            controller.compose("up", "-d", "--no-deps", "backend", override=candidate)
            controller.compose("up", "-d", "--no-deps", "frontend", override=other)
            record = json.loads((controller.operation_dir / "compose-envelope.json").read_text())
            controller.override.write_text(candidate.read_text() + "    privileged: true\n")
            with self.assertRaisesRegex(pf.Failure, "envelope-override"):
                controller.compose("up", "-d", "--no-deps", "backend")
        verbs = [verb[0] for verb in self.verbs() if verb[0] in ("config", "build", "up")]
        self.assertEqual(verbs, ["config", "build", "up", "config", "up"])
        self.assertEqual([item["result"] for item in record["renders"]], ["approved", "approved"])
        first = record["renders"][0]
        self.assertEqual(first["inputs"]["override"], str(candidate))
        self.assertEqual(first["inputs"]["override_sha256"], pf_instance.sha256_bytes(candidate.read_bytes()))
        self.assertEqual(first["inputs"]["instance_id"], self.context.instance_id)
        self.assertEqual(first["inputs"]["compose_file_sha256"],
                         pf_instance.sha256_bytes((self.context.control.path / "compose.nas.yaml").read_bytes()))
        self.assertEqual(first["escape_mode"], "doubled")
        self.assertEqual(first["resolved_file"], "compose-1.json")

    def test_ce4_resolved_files_are_private_and_never_logged(self):
        secret = 'Pw$1 "&<\\zz'
        env_path = self.paths["configuration"] / ".env"
        env_path.write_text(pfx.ENV_TEXT.replace("POSTGRES_PASSWORD=abc123", "POSTGRES_PASSWORD='" + secret + "'"))
        self.state()
        controller = self.controller()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), controller.lock("test"):
            controller.compose("up", "-d", "--no-deps", "db")
            directory = controller.operation_dir
            resolved = directory / "compose-1.json"
            self.assertEqual(stat.S_IMODE(resolved.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertIn(secret.replace("$", "$$"), json.loads(resolved.read_text())["services"]["db"]["environment"][
                "POSTGRES_PASSWORD"])
        self.assertNotIn(secret, output.getvalue())

    def test_ce5_doctor_compares_literal_values_and_leaves_no_file(self):
        env_path = self.paths["configuration"] / ".env"
        for password in ('dq"amp&lt<bs\\x', "test", "data", "pa$$word"):
            with self.subTest(password=password):
                env_path.write_text(pfx.ENV_TEXT.replace("POSTGRES_PASSWORD=abc123", "POSTGRES_PASSWORD='" + password + "'"))
                self.state(topology(self.context))
                before = pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state)
                code, out, err, _ = self.main("doctor")
                self.assertEqual(code, 0, out + err)
                self.assertIn("Compose envelope: ok | compose v2.40.2-fixture | services db, "
                              "backend, frontend | dollar-escape doubled | values compared", out)
                self.assertEqual(pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state), before)
                self.assertNotIn(password, out + err)
                self.assertNotIn("Compose config", out)

    def test_ce6_former_passthrough_verbs_are_refused_before_any_render(self):
        # PF-A1.4: the catch-all Compose route is removed. Audit F2's words (scale and watch create, build or
        # start containers from the model as well) are named refusals before any render or verb.
        for verb, arguments in (("up", ["-d", "db"]), ("run", ["-d", "db"]), ("scale", ["backend=2"]), ("watch", [])):
            with self.subTest(verb):
                state = self.state(topology(self.context))
                state["compose"]["render_patch"] = [{"op": "set", "path": ["services", "db", "privileged"], "value": True}]
                self.fake.write_state(state)
                code, out, err = run_main(["--instance", "staging", verb, *arguments], self.layout, interactive=True)
                self.assertEqual(code, 1)
                self.assertIn(f"ERROR: compose-route-removed: 'pf {verb}' no longer forwards to Docker Compose.", err)
                self.assertNotIn(["config", "--format", "json"], self.verbs())
                self.assertFalse([item for item in self.verbs() if item[:1] == [verb]])
                self.assertEqual(self.fake.calls(), [])
        self.assertTrue(pf_docker.ENVELOPE_VERBS <= set(pf.REMOVED_COMPOSE_ROUTES))

    def test_ce7_instance_label_positions_and_generated_variable(self):
        text = (pfx.REPO_PACKAGE / "compose.nas.yaml").read_text()
        label_line = pf_docker.INSTANCE_LABEL + ': "${DEPLOY_ADMIN_INSTANCE_ID:?Set DEPLOY_ADMIN_INSTANCE_ID ' \
            '(generated by the deployment admin)}"'
        self.assertEqual(text.count(label_line), 7)
        self.assertEqual(text.count(pf_docker.INSTANCE_LABEL + ":"), 7)
        self.assertIn("DEPLOY_ADMIN_INSTANCE_ID", pf_config.GENERATED_KEYS)
        self.assertNotIn("DEPLOY_ADMIN_INSTANCE_ID", pf_config.APP_KEYS)
        self.assertIn("${PARTFLOW_DATABASE_URL", text)
        backend = text[text.index("  backend:"):text.index("  frontend:")]
        self.assertNotIn("${POSTGRES_PASSWORD", backend)
        with self.assertRaisesRegex(pf_config.ConfigError, "unknown key 'DEPLOY_ADMIN_INSTANCE_ID'"):
            pf_config.parse_app_env((pfx.ENV_TEXT + "DEPLOY_ADMIN_INSTANCE_ID=x\n").encode(), label=".env")

    def test_ce8_workspace_compose_files_are_never_named(self):
        workspace = self.context.paths.workspace
        (workspace / "compose.override.yaml").write_text("services:\n  backend:\n    privileged: true\n")
        (workspace / ".env").write_text("POSTGRES_PASSWORD=hostile\nDEPLOY_ADMIN_INSTANCE_ID=hostile\n")
        self.state()
        controller = self.controller()
        with controller.lock("test"):
            controller.compose("up", "-d", "--no-deps", "db")
            frozen = str(controller.frozen.env_file)
        for argv in self.fake.argvs():
            if argv[:1] != ["compose"] or "-f" not in argv:
                continue
            files = [argv[index + 1] for index, word in enumerate(argv) if word == "-f"]
            self.assertEqual(files, [str(self.context.control.path / "compose.nas.yaml")])
            if "--env-file" in argv:
                self.assertEqual(argv[argv.index("--env-file") + 1], frozen)
            self.assertFalse([word for word in argv if word.startswith(str(workspace) + "/")])

    def test_ce9_reset_db_override_renders_the_clean_database(self):
        self.state()
        controller = self.controller()
        clean = "pf_clean_" + uuid.uuid4().hex[:20]
        with controller.lock("reset-db"):
            override = controller.state / "reset-images.yaml"
            controller.make_override({"backend": PROJECT + "-backend:backup-1", "frontend": PROJECT + "-frontend:backup-1"},
                                     override)
            controller.compose("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head",
                               override=override, env={"POSTGRES_DB": clean})
            record = json.loads((controller.operation_dir / "compose-envelope.json").read_text())
        calls = [call for call in self.fake.calls() if call["argv"][:1] == ["compose"] and "config" in call["argv"]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["POSTGRES_DB"], clean)
        expected_url = pf_config.database_url("partflow_staging", "abc123", clean)
        self.assertEqual(calls[0]["database_url_sha256"], pf_instance.sha256_bytes(expected_url.encode()))
        self.assertEqual(record["renders"][0]["inputs"]["value_overrides"], ["POSTGRES_DB"])
        verbs = [verb[0] for verb in self.verbs() if verb[0] in ("config", "run")]
        self.assertEqual(verbs, ["config", "run"])

    def test_ce10_update_migration_sequence_renders_exactly_two_input_keys(self):
        state = self.state()
        state["compose"]["run_output"] = json.dumps({"files": {}, "heads": ["r2"]})
        self.fake.write_state(state)
        controller = self.controller()
        with controller.lock("update"):
            candidate = controller.state / "candidate-repo"
            candidate.mkdir()
            override = controller.state / "candidate-images.yaml"
            controller.make_override({"backend": PROJECT + "-backend:candidate-1",
                                      "frontend": PROJECT + "-frontend:candidate-1"}, override)
            self.assertEqual(controller.image_contract(root=candidate, override=override)["heads"], ["r2"])
            rehearsal = "pf_migrate_" + uuid.uuid4().hex[:20]
            arguments = ("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head")
            controller.compose(*arguments, root=candidate, override=override, env={"POSTGRES_DB": rehearsal})
            controller.compose(*arguments, root=candidate, override=override)
            record = json.loads((controller.operation_dir / "compose-envelope.json").read_text())
        self.assertEqual(len(record["renders"]), 2)
        self.assertEqual([item["inputs"]["value_overrides"] for item in record["renders"]], [[], ["POSTGRES_DB"]])
        self.assertTrue(all(item["inputs"]["repo_root"] == str(candidate) for item in record["renders"]))
        renders = [call for call in self.fake.calls() if call["argv"][:1] == ["compose"] and "config" in call["argv"]]
        self.assertEqual([call["POSTGRES_DB"] for call in renders], ["partflow_staging", rehearsal])

    def test_ce11_hostile_value_overrides_are_refused_before_any_process(self):
        self.state()
        controller = self.controller()
        with controller.lock("test"):
            for overrides in ({"POSTGRES_PASSWORD": "x"}, {"PARTFLOW_BIND_IP": "0.0.0.0"}, {"POSTGRES_DB": "evil"},
                              {"POSTGRES_DB": "pf_clean_x; DROP"}, {"DEPLOY_ADMIN_INSTANCE_ID": OTHER_INSTANCE}):
                with self.subTest(overrides):
                    with self.assertRaisesRegex(pf.Failure, "^compose-override-refused: "):
                        controller.compose("run", "--rm", "backend", "true", env=overrides)
        self.assertEqual(self.fake.calls(), [])


# ============================================================================ RI: inventory, plan, purge


class PurgeHarness(ScopeBase):
    """The database/application plane of purge is simulated; Docker resources, the recovery bundle,
    the inventory, the plans and every deletion go through the real controller and the fake daemon."""

    def setUp(self):
        super().setUp()
        self.activated = []
        self.image_ids = {"backend": "sha256:abackend", "frontend": "sha256:afrontend"}

    @contextlib.contextmanager
    def plane(self):
        harness = self

        def inspect(controller, service):
            return {"Image": harness.image_ids.get(service, "sha256:postgres"),
                    "State": {"Running": True, "Health": {"Status": "healthy"}}, "Config": {"Env": []}}

        def snapshot(controller, reason, source_verified=True):
            backup_id = f"{pf.utc()}-{pfx.OLD[:12]}-{uuid.uuid4().hex[:6]}"
            controller.ensure_backup_tree()
            folder = controller.backups_dir / backup_id
            folder.mkdir(mode=0o700)
            images = controller.retain_images(backup_id)
            (folder / "database.dump").write_bytes(b"dump")
            (folder / "database.list").write_bytes(b"list")
            payload = folder / "payload.txt"
            payload.write_text("source")
            with tarfile.open(folder / "source.tar.gz", "w:gz") as archive:
                archive.add(payload, arcname="payload.txt")
            payload.unlink()
            metadata = {
                "format": 2, "id": backup_id, "created_at": pf.utc(), "reason": reason, "status": "complete",
                "source_revision": pfx.OLD, "source_provenance": "git_commit", "source_verified": True,
                "project": controller.config["project"], "repository": controller.config["repository"],
                "environment": "staging", "database": "partflow_staging", "database_user": "partflow_staging",
                "postgres_major": 16, "database_heads": ["r1"], "images": images, "migration_files": {},
                "checksums": {name: pf.digest(folder / name) for name in ("source.tar.gz", "database.dump",
                                                                          "database.list")},
            }
            pf.write_json(folder / "manifest.json", metadata)
            (folder / "manifest.sha256").write_text(pf.digest(folder / "manifest.json") + "\n")
            return metadata

        def pause(controller, kind, **extra):
            pf.write_json(controller.pending, {"operation": kind, "phase": "paused", "started": pf.utc(), **extra})

        def database_program(controller, program, *arguments, **kwargs):
            if kwargs.get("output") is not None:
                kwargs["output"].write(b"-- globals\n")
            return ""

        patches = (
            mock.patch.object(pf.Controller, "database_ready", lambda controller: 16),
            mock.patch.object(pf.Controller, "inspect", inspect),
            mock.patch.object(pf.Controller, "image_contract",
                              lambda controller, root=None, override=None: {"files": {}, "heads": ["r1"]}),
            mock.patch.object(pf.Controller, "db_heads", lambda controller, database=None: ["r1"]),
            mock.patch.object(pf.Controller, "snapshot", snapshot),
            mock.patch.object(pf.Controller, "pause", pause),
            mock.patch.object(pf.Controller, "database_inventory",
                              lambda controller: [{"name": "partflow_staging", "allow_connections": True}]),
            mock.patch.object(pf.Controller, "database_program", database_program),
            mock.patch.object(pf.Controller, "activate",
                              lambda controller, images, heads: harness.activated.append(images)),
        )
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            yield

    def purge(self, *extra, confirm=None):
        with self.plane():
            return self.main("purge", "--keep-backups", *extra, confirm=confirm)

    def resources(self, prefix):
        state = self.fake.state()
        return {key: [item for item in state[key] if json.dumps(item).find(prefix) >= 0]
                for key in ("containers", "volumes", "networks", "images")}

    def journal(self):
        return json.loads(self.context.journal_path.read_text())

    def plan_file(self):
        reference = self.journal()["deletion_plan"]
        return Path(reference["path"])


@ROOT_REQUIRED
class ResourceInventory(PurgeHarness):
    """A1-T11, A1-T12: exact scope, user relationships, closed frozen plan, exclusion oracle."""

    def sibling(self):
        context, _ = self.instance("sibling", PROJECT + "_test")
        return context

    def test_ri1_ri23_prefix_sibling_is_byte_identical_after_the_purge(self):
        sibling = self.sibling()
        self.state(topology(self.context, "a"), topology(sibling, "b"))
        before = self.resources("partflow_test")
        self.assertEqual(len(before["containers"]), 3)
        code, out, err, confirmations = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(confirmations[0], "PURGE partflow")
        self.assertEqual(self.resources("partflow_test"), before)
        state = self.fake.state()
        self.assertFalse([item for item in state["containers"] if "partflow-" in item["name"]])
        self.assertEqual([item["name"] for item in state["volumes"]], ["partflow_test_postgres_data"])
        self.assertEqual([item["name"] for item in state["networks"]], ["partflow_test_default"])
        self.assertEqual([tag for item in state["images"] for tag in item["repo_tags"]],
                         ["partflow_test-backend:candidate-b00000000000-abcdef",
                          "partflow_test-frontend:candidate-b00000000000-abcdef"])
        self.assertFalse(state.get("violations"))
        argvs = self.fake.argvs()
        for argv in argvs:
            self.assertNotIn("prune", argv)
            self.assertFalse(argv[:1] == ["compose"] and "down" in argv)
            self.assertFalse(argv[:2] == ["image", "rm"] and ("-f" in argv or "--force" in argv))
        # The sealed manifest holds exactly the binding plan's candidates (v2 shape) and verifies.
        recovery = sorted((self.context.paths.recovery / PROJECT).iterdir())[-1]
        controller = self.controller()
        verified = controller.verify_recovery({"_folder": str(recovery), "id": recovery.name})
        operation = sorted(self.context.operations_dir.iterdir())[-1]
        plan = json.loads((operation / "deletion-plan.json").read_text())
        self.assertEqual(plan["image_coverage"], "bound")
        self.assertEqual(verified["resources_before_purge"], {
            kind + "s": [item["key"] for item in plan["candidates"] if item["kind"] == kind]
            for kind in ("container", "volume", "network", "image")})
        expected_containers = [item["id"] for item in topology(self.context, "a")["containers"]]
        self.assertEqual(sorted(verified["resources_before_purge"]["containers"]), sorted(expected_containers))
        self.assertEqual(verified["resources_before_purge"]["volumes"], ["partflow_postgres_data"])
        self.assertEqual(verified["resources_before_purge"]["networks"], ["partflow_default"])
        images = verified["resources_before_purge"]["images"]
        self.assertEqual(len(images), 6)  # candidate, backup and the purge's own -inspect- alias per service
        self.assertTrue(all(image.startswith(("partflow-backend:", "partflow-frontend:")) for image in images))
        binding = json.loads((operation / "inventory-binding.json").read_text())
        self.assertEqual(binding["compare_plans"], "equal")
        self.assertEqual(len(binding["created_image_refs"]), 4)
        self.assertNotIn("abc123", (operation / "inventory-binding.json").read_text())
        self.assertFalse(self.context.state_dir.exists())

    def blocking_volume(self, labels):
        fragment = topology(self.context)
        fragment["volumes"] = [pfx.volume("partflow_postgres_data", labels)]
        return fragment

    def test_ri2_unlabelled_topology_volume_blocks_every_project_mutation(self):
        for labels, code in (({pf_docker.COMPOSE_PROJECT_LABEL: PROJECT}, "resource-legacy-unlabeled"),
                             ({}, "resource-name-collision")):
            fragment = self.blocking_volume(labels)
            fragment["containers"] = []
            for arguments, expected in ((["purge", "--keep-backups"], "resource-blocked"),
                                        (["abort-deploy"], "resource-blocked"),
                                        (["deploy", "--current"], "resource-target-not-empty"),
                                        (["backup"], "resource-not-owned"),
                                        (["update", "--latest"], "resource-not-owned"),
                                        (["reset-db"], "resource-not-owned"),
                                        (["rollback", "--restore-db"], "resource-not-owned")):
                with self.subTest(code=code, command=arguments[0]):
                    self.state(fragment)
                    pending = self.context.journal_path
                    if pending.exists():
                        pending.unlink()
                    deployed = self.context.state_dir / "deployed.json"
                    if arguments[0] in ("deploy", "abort-deploy") and deployed.exists():
                        deployed.unlink()
                    if arguments[0] == "abort-deploy":
                        pf.write_json(pending, {"operation": "deploy", "phase": "starting-database",
                                                "database": "partflow_staging", "started": "20261006T000000Z"})
                    journal = pending.read_bytes() if pending.exists() else None
                    with self.plane():
                        code_, out, err, confirmations = self.main(*arguments, confirm=refuse_confirmation)
                    self.assertEqual(code_, 1, err)
                    self.assertIn("ERROR: " + expected + ": ", err)
                    self.assertIn(code, err)
                    self.assertEqual(confirmations, [])
                    self.assertEqual(self.mutations(), [])
                    self.assertEqual(pending.read_bytes() if pending.exists() else None, journal)
                    pfx.deployed_record(self.context)

    def test_ri3_foreign_claim_blocks(self):
        other = types.SimpleNamespace(instance_id=OTHER_INSTANCE, compose_project=PROJECT)
        fragment = topology(self.context)
        fragment["volumes"] = [pfx.volume("partflow_postgres_data", dict(pfx.labels_for(other), **{
            pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"}))]
        self.state(fragment)
        code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
        self.assertEqual(code, 1)
        self.assertIn("resource-blocked: purge refused before any confirmation", err)
        self.assertIn("resource-foreign-claim: volume partflow_postgres_data", err)
        self.assertIn("adoption is PF-A2", err)

    def test_ri4_legacy_unlabelled_db_container_blocks_before_tag_or_up(self):
        legacy = pfx.container("1" * 64, "partflow-db-1", {pf_docker.COMPOSE_PROJECT_LABEL: PROJECT,
                                                           pf_docker.COMPOSE_SERVICE_LABEL: "db"})
        for arguments, expected in ((["backup"], "resource-not-owned"), (["update", "--latest"], "resource-not-owned"),
                                    (["restore-instance", "x"], "resource-target-not-empty")):
            with self.subTest(arguments[0]):
                self.state({"containers": [legacy]})
                if arguments[0] == "restore-instance":
                    (self.context.state_dir / "deployed.json").unlink()
                    arguments = ["restore-instance", self.prepare_restore_bundle()]
                with self.plane():
                    code, out, err, confirmations = self.main(*arguments, confirm=refuse_confirmation)
                self.assertEqual(code, 1, err)
                self.assertIn(expected, err)
                self.assertIn("partflow-db-1", err)
                self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["tag"]
                                  or (argv[:1] == ["compose"] and "up" in argv)])
                self.assertEqual(confirmations, [])
                pfx.deployed_record(self.context)

    def test_ri5_recreated_volume_before_or_during_execution(self):
        # Recreated after the binding plan, before execution (right after the refresh probe): no effect at all.
        self.state(topology(self.context), hooks=[{"after_argv_prefix": PROBE, "nth": 2, "mutate": [
            {"op": "update", "list": "volumes", "match": {"name": "partflow_postgres_data"},
             "set": {"created_at": "2026-10-07T00:00:00Z"}}]}])
        code, out, err, _ = self.purge()
        self.assertEqual(code, 1)
        self.assertIn("plan-drift: Planned volume partflow_postgres_data changed after the plan was frozen "
                      "(replaced: new creation time); deletion stopped. Already removed: 0.", err)
        self.assertEqual([argv for argv in self.mutations() if argv[:1] != ["tag"]], [])
        self.assertEqual(self.journal()["deleted"], [])

    def test_ri5_recreated_volume_mid_execution_stops_at_the_volume(self):
        sibling = self.sibling()
        fragment = topology(self.context)
        last = fragment["containers"][-1]["id"]
        self.state(fragment, topology(sibling, "b"), hooks=[{"after_argv_prefix": ["rm", "-f", last], "mutate": [
            {"op": "update", "list": "volumes", "match": {"name": "partflow_postgres_data"},
             "set": {"created_at": "2026-10-07T00:00:00Z"}}]}])
        before = self.resources("partflow_test")
        code, out, err, _ = self.purge()
        self.assertEqual(code, 1)
        self.assertIn("plan-drift: Planned volume partflow_postgres_data changed after the plan was frozen", err)
        journal = self.journal()
        self.assertEqual(journal["phase"], "deleting")
        removed = [entry for entry in journal["deleted"] if entry["outcome"] == "removed"]
        self.assertEqual([entry["kind"] for entry in removed], ["container"] * 3 + ["network"])
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["volume", "rm"]])
        code, out, err, _ = self.main("status")
        self.assertIn("pf purge --instance staging", out)
        self.assertEqual(self.resources("partflow_test"), before)

    def test_ri6_label_or_option_change_before_the_effect_is_drift(self):
        for change in ({"labels": dict(pfx.labels_for(self.context), **{pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data",
                                                                           "extra": "1"})},
                       {"options": {"type": "none", "o": "bind", "device": "/srv"}}):
            with self.subTest(sorted(change)):
                fragment = topology(self.context)
                last = fragment["containers"][-1]["id"]
                self.state(fragment, hooks=[{"after_argv_prefix": ["rm", "-f", last], "mutate": [
                    {"op": "update", "list": "volumes", "match": {"name": "partflow_postgres_data"}, "set": change}]}])
                code, out, err, _ = self.purge()
                self.assertEqual(code, 1)
                self.assertIn("plan-drift: Planned volume partflow_postgres_data", err)
                self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] == ["volume", "rm"]])
                self.context.journal_path.unlink()

    def test_ri7_replaced_container_is_drift_absent_container_is_already_absent(self):
        fragment = topology(self.context)
        first = fragment["containers"][0]
        replacement = dict(copy.deepcopy(first), id="6" * 64)
        self.state(fragment, hooks=[{"after_argv_prefix": ["image", "save"], "mutate": [
            {"op": "remove", "list": "containers", "match": {"id": first["id"]}},
            {"op": "append", "list": "containers", "value": replacement}]}])
        code, out, err, _ = self.purge()
        self.assertEqual(code, 1)
        self.assertIn("plan-changed", err)
        self.assertEqual(len(self.activated), 1)  # reopened; no journal is left behind
        self.assertFalse(self.context.journal_path.exists())
        # Absent without replacement at execution time: recorded as already-absent.
        fragment = topology(self.context)
        first = fragment["containers"][0]
        self.state(fragment)
        controller = self.controller()
        with self.plane(), controller.lock("purge"):
            inventory = controller.docker_inventory()
            plan = controller.plan_for("purge", inventory, command="purge", covered_image_refs=set())
            reference = controller.write_deletion_plan(plan)
            pf.write_json(controller.pending, {"operation": "purge", "phase": "deleting", "deletion_plan": reference,
                                               "deleted": []})
            state = self.fake.state()
            state["containers"] = [item for item in state["containers"] if item["id"] != first["id"]]
            self.fake.write_state(state)
            controller.execute_deletion_plan(plan)
            deleted = json.loads(controller.pending.read_text())["deleted"]
        self.assertEqual(deleted[0], {"kind": "container", "key": first["id"], "outcome": "already-absent"})
        replaced = topology(self.context)
        replaced_first = replaced["containers"][0]
        self.state(replaced)
        controller = self.controller()
        with self.plane(), controller.lock("purge"):
            plan = controller.plan_for("purge", controller.docker_inventory(), command="purge", covered_image_refs=set())
            pf.write_json(controller.pending, {"operation": "purge", "phase": "deleting", "deleted": []})
            state = self.fake.state()
            state["containers"][0] = dict(replaced_first, id="6" * 64)
            self.fake.write_state(state)
            with self.assertRaisesRegex(pf.Failure, "plan-drift: Planned container .* new ID"):
                controller.execute_deletion_plan(plan)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["rm"]])

    def test_ri9_stopped_foreign_users_block_the_purge(self):
        for mount in ({"volumes": ("partflow_postgres_data",)}, {"networks": (("partflow_default", "a" * 64),)}):
            with self.subTest(sorted(mount)):
                fragment = topology(self.context)
                fragment["containers"].append(pfx.container("2" * 64, "other-app", {}, status="exited", **mount))
                self.state(fragment)
                code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
                self.assertEqual(code, 1)
                self.assertIn("resource-shared", err)
                self.assertEqual(confirmations, [])
                self.assertEqual(self.mutations(), [])

    def test_ri10_late_users_stop_the_plan(self):
        attach = {"op": "append", "list": "containers", "value": pfx.container(
            "3" * 64, "late-app", {}, status="exited", networks=(("partflow_default", "a" * 64),))}
        # Before the first effect: right after the plan was frozen (no rm at all).
        fragment = topology(self.context)
        self.state(fragment)
        controller = self.controller()
        with self.plane(), controller.lock("purge"):
            plan = controller.plan_for("purge", controller.docker_inventory(), command="purge", covered_image_refs=set())
            pf.write_json(controller.pending, {"operation": "purge", "phase": "deleting", "deleted": []})
            state = self.fake.state()
            state["containers"].append(attach["value"])
            self.fake.write_state(state)
            with self.assertRaisesRegex(pf.Failure, "plan-drift: Planned network partflow_default changed"):
                controller.execute_deletion_plan(plan)
        self.assertEqual(self.mutations(), [])
        # After the containers are removed, before the network/volume effect.
        for mutation, kind in ((attach, "network"),
                               ({"op": "append", "list": "containers", "value": pfx.container(
                                   "4" * 64, "late-db-reader", {}, status="exited", volumes=("partflow_postgres_data",))},
                                "volume")):
            with self.subTest(kind):
                fragment = topology(self.context)
                last = fragment["containers"][-1]["id"]
                if self.context.journal_path.exists():
                    self.context.journal_path.unlink()
                self.state(fragment, hooks=[{"after_argv_prefix": ["rm", "-f", last], "mutate": [mutation]}])
                code, out, err, _ = self.purge()
                self.assertEqual(code, 1)
                self.assertIn("plan-drift", err)
                self.assertIn("resource-shared", err)
                self.assertFalse([argv for argv in self.fake.argvs() if argv[:2] in (["network", "rm"], ["volume", "rm"])])
                removed = [entry["kind"] for entry in self.journal()["deleted"]]
                self.assertEqual(removed, ["container"] * 3)

    def test_ri11_an_image_shared_with_another_app_keeps_the_foreign_tag(self):
        fragment = topology(self.context)
        fragment["images"][0]["repo_tags"].append("otherapp:latest")
        self.state(fragment)
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        state = self.fake.state()
        self.assertEqual([tag for item in state["images"] for tag in item["repo_tags"]], ["otherapp:latest"])
        removals = [argv for argv in self.fake.argvs() if argv[:2] == ["image", "rm"]]
        self.assertTrue(removals)
        self.assertTrue(all(len(argv) == 3 and argv[2].startswith("partflow-") for argv in removals))
        self.assertFalse(state.get("violations"))
        for argv in self.fake.argvs():
            self.assertNotIn("prune", argv)
            self.assertNotIn("--force", argv)
            self.assertFalse(argv[:1] == ["compose"] and "down" in argv)

    def test_ri11_an_image_a_foreign_container_uses_by_id_is_retained(self):
        """Audit F4: the foreign container references the image only by its ID."""
        fragment = topology(self.context)
        fragment["containers"].append(pfx.container("8" * 64, "other-app", {}, status="exited",
                                                    image="sha256:abackend", config_image="sha256:abackend"))
        self.state(fragment)
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertIn("retained: foreign-in-use image partflow-backend:candidate-a00000000000-abcdef", out)
        backend = next(item for item in self.fake.state()["images"] if item["id"] == "sha256:abackend")
        removals = [argv[2] for argv in self.fake.argvs() if argv[:2] == ["image", "rm"]]
        self.assertTrue(removals)
        self.assertFalse(set(removals) & set(backend["repo_tags"]))
        self.assertIn("partflow-backend:candidate-a00000000000-abcdef", backend["repo_tags"])
        self.assertTrue(all(argv.startswith("partflow-frontend:") for argv in removals))
        self.assertIn("8" * 64, [item["id"] for item in self.fake.state()["containers"]])

    def test_ri11_a_planned_tag_a_foreign_container_starts_using_after_the_freeze_is_drift(self):
        """Audit F8: a planned image item still present but no longer owned is never removed."""
        tag = "partflow-backend:candidate-a00000000000-abcdef"
        for label, late in (("by reference", pfx.container("8" * 64, "other-app", {}, status="exited",
                                                            config_image=tag)),
                            ("by image ID", pfx.container("8" * 64, "other-app", {}, status="exited",
                                                          image="sha256:abackend", config_image="sha256:abackend"))):
            with self.subTest(label):
                if self.context.journal_path.exists():
                    self.context.journal_path.unlink()
                self.state(topology(self.context), hooks=[{"after_argv_prefix": ["volume", "rm"], "mutate": [
                    {"op": "append", "list": "containers", "value": late}]}])
                code, out, err, _ = self.purge()
                self.assertEqual(code, 1, out + err)
                self.assertIn("plan-drift: Planned image partflow-backend:", err)
                self.assertIn("now excluded: foreign-in-use", err)
                self.assertNotIn(["image", "rm", tag], self.fake.argvs())
                self.assertIn(tag, [name for item in self.fake.state()["images"] for name in item["repo_tags"]])
                self.assertEqual(self.journal()["phase"], "deleting")

    def test_ri12_bind_paths_are_retained_and_a_legacy_bind_container_blocks(self):
        exports = self.base / "exports"
        (exports / "sub").mkdir(parents=True)
        (exports / "sub" / "keep.txt").write_text("keep")
        fragment = topology(self.context)
        runner = pfx.container("5" * 64, "partflow-backend-run-1", pfx.labels_for(self.context, "backend", oneoff="True"),
                               binds=(str(exports),))
        fragment["containers"].append(runner)
        self.state(fragment)
        before = pfx.snapshot_tree(exports)
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertIn("retained bind path (never deleted): " + str(exports), out)
        self.assertIn(["rm", "-f", "5" * 64], self.fake.argvs())
        self.assertEqual(pfx.snapshot_tree(exports), before)
        legacy = pfx.container("6" * 64, "legacy", {pf_docker.COMPOSE_PROJECT_LABEL: PROJECT}, binds=(str(exports),))
        self.state(topology(self.context), {"containers": [legacy]})
        code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
        self.assertEqual(code, 1)
        self.assertIn("resource-legacy-unlabeled", err)
        self.assertEqual(confirmations, [])

    def test_ri13_closed_resume_and_invalid_plans(self):
        fragment = topology(self.context)
        self.state(fragment)
        original_run = pf.pf_runner.ProcessRunner.run
        seen = []

        def interrupt_after_first_rm(runner, spec):
            result = original_run(runner, spec)
            if list(spec.argv[:2]) == ["rm", "-f"] and not seen:
                seen.append(spec.argv)
                raise KeyboardInterrupt("Interrupted by signal 15")
            return result

        with mock.patch.object(pf.pf_runner.ProcessRunner, "run", interrupt_after_first_rm):
            code, out, err, _ = self.purge()
        self.assertEqual(code, 1)
        self.assertIn("Interrupted by signal 15", err)
        journal = self.journal()
        self.assertEqual((journal["phase"], journal["deleted"]), ("deleting", []))
        plan_path = self.plan_file()
        plan_bytes = plan_path.read_bytes()
        # A new owned-labelled volume outside the topology appears; resume never adds it.
        state = self.fake.state()
        state["volumes"].append(pfx.volume("partflow_extra", pfx.labels_for(self.context)))
        self.fake.write_state(state)
        controller = self.controller()
        for label, mutate, restore in (
                ("tampered byte", lambda: plan_path.write_bytes(plan_bytes.replace(b'"purge"', b'"purgE"', 1)),
                 lambda: plan_path.write_bytes(plan_bytes)),
                ("path mismatch", lambda: self.rewrite_journal(path=str(plan_path) + ".x"),
                 lambda: self.rewrite_journal(path=str(plan_path))),
                ("abort-deploy plan from a purge journal",
                 lambda: self.replace_with_abort_plan(controller, plan_path), lambda: self.restore_plan(plan_path, plan_bytes))):
            with self.subTest(label):
                mutate()
                try:
                    calls = len(self.fake.calls())
                    code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
                    self.assertEqual(code, 1)
                    self.assertIn("plan-invalid", err)
                    self.assertEqual(confirmations, [])
                    self.assertFalse([argv for argv in self.fake.argvs()[calls:]
                                      if pf.unclassified_mutation("docker", argv)])
                finally:
                    restore()
        # A symlinked plan: the protected operations tree already refuses the context; the loader
        # itself refuses it as well (O_NOFOLLOW, regular file only).
        self.symlink_plan(plan_path)
        try:
            code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
            self.assertEqual(code, 1)
            self.assertIn("symlink", err)
            self.assertEqual(confirmations, [])
            with self.assertRaisesRegex(pf.Failure, "^plan-invalid: .*\n  detail: cannot open"):
                pf.Controller(self.context).load_deletion_plan(self.journal(), kind="purge")
        finally:
            self.unsymlink_plan(plan_path, plan_bytes)
        code, out, err, confirmations = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(confirmations, ["RESUME PURGE partflow " + journal["recovery"]])
        state = self.fake.state()
        self.assertEqual([item["name"] for item in state["volumes"]], ["partflow_extra"])
        self.assertFalse(state["containers"])
        self.assertFalse(state["networks"])

    def rewrite_journal(self, **reference):
        journal = self.journal()
        journal["deletion_plan"].update(reference)
        pf.write_json(self.context.journal_path, journal)

    def symlink_plan(self, plan_path):
        real = plan_path.with_name("real-plan.json")
        os.replace(str(plan_path), str(real))
        plan_path.symlink_to(real)

    def unsymlink_plan(self, plan_path, data):
        plan_path.unlink()
        plan_path.with_name("real-plan.json").unlink()
        plan_path.write_bytes(data)

    def replace_with_abort_plan(self, controller, plan_path):
        plan = json.loads(plan_path.read_text())
        plan.update(kind="abort-deploy", image_coverage="none")
        data = pf_docker.plan_bytes(plan)
        plan_path.write_bytes(data)
        self.rewrite_journal(sha256=pf_instance.sha256_bytes(data))
        self._saved_sha = pf_docker.plan_sha256(json.loads(plan_path.read_text()))

    def restore_plan(self, plan_path, data):
        plan_path.write_bytes(data)
        self.rewrite_journal(sha256=pf_instance.sha256_bytes(data))

    def test_ri14_pre_plan_journals_are_refused_with_plan_missing(self):
        self.state(topology(self.context))
        recovery_id = "purge-20261006T000000Z-" + pfx.OLD[:12] + "-abcdef"
        pf.write_json(self.context.journal_path, {"operation": "purge", "phase": "deleting", "recovery": recovery_id})
        code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
        self.assertEqual(code, 1)
        self.assertIn("plan-missing: This interrupted purge predates frozen deletion plans (PF-A1.3)", err)
        (self.context.state_dir / "deployed.json").unlink()
        pf.write_json(self.context.journal_path, {"operation": "deploy", "phase": "aborting"})
        code, out, err, confirmations = self.main("abort-deploy", confirm=refuse_confirmation)
        self.assertEqual(code, 1)
        self.assertIn("plan-missing: This interrupted abort-deploy predates", err)
        deletions = [argv for argv in self.fake.argvs() if argv[:1] == ["rm"] or argv[1:2] == ["rm"]
                     or (argv[:1] == ["compose"] and "down" in argv)]
        self.assertEqual(deletions, [])  # (fail-closed may stop the application services; nothing is deleted)

    def test_ri15_owned_volume_outside_the_topology_is_retained(self):
        fragment = topology(self.context)
        fragment["volumes"].append(pfx.volume("partflow_cache", pfx.labels_for(self.context)))
        self.state(fragment)
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertIn("owned-outside-topology volume partflow_cache", out)
        self.assertEqual([item["name"] for item in self.fake.state()["volumes"]], ["partflow_cache"])

    def test_ri16_abort_deploy_removes_planned_items_retains_images_and_resumes(self):
        (self.context.state_dir / "deployed.json").unlink()
        fragment = topology(self.context)
        self.state(fragment)
        pf.write_json(self.context.journal_path, {"operation": "deploy", "phase": "migrating-database",
                                                  "database": "partflow_staging", "started": "20261006T000000Z"})
        original_run = pf.pf_runner.ProcessRunner.run
        seen = []

        def interrupt_after_first_rm(runner, spec):
            result = original_run(runner, spec)
            if list(spec.argv[:2]) == ["rm", "-f"] and not seen:
                seen.append(spec.argv)
                raise KeyboardInterrupt("Interrupted by signal 2")
            return result

        with mock.patch.object(pf.pf_runner.ProcessRunner, "run", interrupt_after_first_rm):
            code, out, err, confirmations = self.main("abort-deploy")
        self.assertEqual(code, 1)
        self.assertEqual(confirmations, ["ABORT DEPLOY partflow"])
        self.assertEqual(self.journal()["phase"], "aborting")
        code, out, err, confirmations = self.main("abort-deploy")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(confirmations, ["RESUME ABORT DEPLOY partflow"])
        self.assertFalse(self.context.journal_path.exists())
        state = self.fake.state()
        self.assertFalse(state["containers"] or state["volumes"] or state["networks"])
        self.assertEqual(len(state["images"]), 2)
        self.assertIn("abort-retains-images", out)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["compose"] and "down" in argv])
        self.assertTrue((self.paths["configuration"] / ".env").exists())

    def test_ri17_exact_restore_requires_an_empty_target(self):
        bundle = self.prepare_restore_bundle()
        (self.context.state_dir / "deployed.json").unlink()
        for fragment in (topology(self.context), self.blocking_volume({})):
            with self.subTest(len(fragment["containers"])):
                self.state(fragment)
                code, out, err, confirmations = self.main("restore-instance", bundle, confirm=refuse_confirmation)
                self.assertEqual(code, 1, err)
                self.assertIn("resource-target-not-empty: restore-instance requires an empty target", err)
                self.assertEqual(confirmations, [])
                self.assertFalse(self.context.journal_path.exists())

    def test_ri18_status_issues_only_read_only_calls(self):
        state = self.state(topology(self.context))
        state["compose"]["psql"] = {"SELECT to_regclass('public.alembic_version') IS NOT NULL;": "f"}
        self.fake.write_state(state)
        before = pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state)
        code, out, err, _ = self.main("status")
        self.assertEqual(code, 0, out + err)
        self.assertIn("Managed resources: containers 3, networks 1, volumes 1, image tags 2 | excluded 0 | blocked 0", out)
        allowed = (["info"], ["compose", "version"], ["ps", "-a"], ["container", "inspect"], ["volume", "ls"],
                   ["volume", "inspect"], ["network", "ls"], ["network", "inspect"], ["image", "ls"],
                   ["image", "inspect"])
        for argv in self.fake.argvs():
            if argv[:1] == ["compose"] and argv[1] != "version":
                rest = argv[1:]
                while rest[0].startswith("-"):
                    rest = rest[2:]
                self.assertIn(rest[0], ("ps", "exec"), argv)
                if rest[0] == "exec":
                    self.assertEqual(rest[:4], ["exec", "-T", "db", "psql"])
                    self.assertIn(rest[rest.index("-c") + 1].split()[0], ("SELECT",))
            else:
                self.assertTrue(any(argv[:len(prefix)] == prefix for prefix in allowed), argv)
            self.assertIsNone(pf.unclassified_mutation("docker", argv), argv)
        self.assertEqual(pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state), before)
        blocked = topology(self.context)
        blocked["volumes"] = [pfx.volume("partflow_postgres_data", {})]
        blocked["containers"].append(pfx.container("5" * 64, "partflow-backend-run-1",
                                                   pfx.labels_for(self.context, "backend", oneoff="True"),
                                                   binds=("/srv/exports",)))
        self.state(blocked)
        code, out, err, _ = self.main("status")
        self.assertIn("  blocked: resource-name-collision volume partflow_postgres_data", out)
        self.assertIn("  retained: bind-retained bind /srv/exports", out)

    def test_ri20_purge_with_history_covers_historical_tags_and_aliases_by_image_id(self):
        fragment = topology(self.context)
        old = "20260101T000000Z-" + pfx.OLD[:12] + "-aaaaaa"
        fragment["images"][0]["repo_tags"] += ["partflow-backend:backup-" + old.lower(),
                                               "partflow-backend:backup-20260101t000000z-inspect-1a2b3c"]
        fragment["images"][1]["repo_tags"] += ["partflow-frontend:backup-" + old.lower()]
        fragment["images"].append(pfx.image("sha256:orphan", ["partflow-backend:backup-orphan"],
                                            {pf_docker.INSTANCE_LABEL: self.context.instance_id}))
        self.state(fragment)
        controller = self.controller()
        controller.ensure_backup_tree()
        folder = controller.backups_dir / old
        folder.mkdir()
        pf.write_json(folder / "manifest.json", {"id": old, "status": "complete", "images": {
            "backend": {"reference": "partflow-backend:backup-" + old.lower(), "id": "sha256:abackend"},
            "frontend": {"reference": "partflow-frontend:backup-" + old.lower(), "id": "sha256:afrontend"}}})
        code, out, err, _ = self.purge()
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("plan-changed", err)
        operation = sorted(self.context.operations_dir.iterdir())[-1]
        plan = json.loads((operation / "deletion-plan.json").read_text())
        images = {item["key"] for item in plan["candidates"] if item["kind"] == "image"}
        self.assertIn("partflow-backend:backup-" + old.lower(), images)
        self.assertIn("partflow-backend:backup-20260101t000000z-inspect-1a2b3c", images)
        self.assertEqual([(entry["key"], entry["reason"]) for entry in plan["exclusions"] if entry["kind"] == "image"],
                         [("partflow-backend:backup-orphan", "not-covered")])
        self.assertIn("not covered by the recovery bundle: retained image partflow-backend:backup-orphan", out)
        self.assertEqual([tag for item in self.fake.state()["images"] for tag in item["repo_tags"]],
                         ["partflow-backend:backup-orphan"])

    def test_ri21_a_candidate_appearing_during_the_bundle_is_plan_changed_without_a_sealed_manifest(self):
        fragment = topology(self.context)
        newcomer = pfx.container("7" * 64, "partflow-backend-2", pfx.labels_for(self.context, "backend"))
        new_tag = {"op": "update", "list": "images", "match": {"id": "sha256:abackend"},
                   "set": {"repo_tags": ["partflow-backend:candidate-a00000000000-abcdef", "partflow-backend:candidate-new"]}}
        for label, mutation in (("container appears", {"op": "append", "list": "containers", "value": newcomer}),
                                ("owned tag appears", new_tag)):
            with self.subTest(label):
                self.state(fragment, hooks=[{"after_argv_prefix": ["image", "save"], "mutate": [mutation]}])
                self.activated.clear()
                code, out, err, confirmations = self.purge()
                self.assertEqual(code, 1)
                self.assertIn("plan-changed: The deletion candidates changed while the recovery bundle was created", err)
                self.assertEqual(confirmations, ["PURGE partflow"])
                self.assertEqual(len(self.activated), 1)
                bundles = list((self.context.paths.recovery / PROJECT).iterdir())
                self.assertTrue(bundles)
                self.assertFalse([bundle for bundle in bundles if (bundle / "manifest.json").exists()])
                self.assertFalse(self.context.journal_path.exists())
                self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["rm"]])

    def test_ri21_a_blocker_appearing_during_the_bundle_is_plan_changed_after_the_pause(self):
        """Audit F1: never the pre-confirmation 'resource-blocked' copy once PURGE was confirmed and
        services were stopped. A foreign user of the volume/network reopens the application; a blocker
        Compose could adopt or recreate keeps the services stopped and the journal paused."""
        variants = (
            ("foreign volume user", pfx.container("2" * 64, "other-app", {}, status="exited",
                                                  volumes=("partflow_postgres_data",)), True),
            ("foreign network user", pfx.container("2" * 64, "other-app", {}, status="exited",
                                                   networks=(("partflow_default", "a" * 64),)), True),
            ("legacy project container", pfx.container("6" * 64, "partflow-backend-9",
                                                       {pf_docker.COMPOSE_PROJECT_LABEL: PROJECT,
                                                        pf_docker.COMPOSE_SERVICE_LABEL: "backend"}), False),
        )
        for label, newcomer, reopened in variants:
            with self.subTest(label):
                if self.context.journal_path.exists():
                    self.context.journal_path.unlink()
                before = self.operation_dirs()
                self.state(topology(self.context), hooks=[{"after_argv_prefix": ["image", "save"], "mutate": [
                    {"op": "append", "list": "containers", "value": newcomer}]}])
                self.activated.clear()
                code, out, err, confirmations = self.purge()
                self.assertEqual(code, 1, out + err)
                self.assertEqual(confirmations, ["PURGE partflow"])
                self.assertIn("plan-changed: 1 resource(s) became blocked while the recovery bundle was created, "
                              "after 'PURGE' was confirmed and the application services were stopped", err)
                self.assertNotIn("before any confirmation", err)
                self.assertNotIn("Nothing was stopped", err)
                self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["rm"] or argv[1:2] == ["rm"]])
                bundles = list((self.context.paths.recovery / PROJECT).iterdir())
                self.assertFalse([bundle for bundle in bundles if (bundle / "manifest.json").exists()])
                operation = self.context.operations_dir / sorted(self.operation_dirs() - before)[-1]
                binding = json.loads((operation / "inventory-binding.json").read_text())
                self.assertEqual(binding["compare_plans"], "resource-blocked")
                if reopened:
                    self.assertIn("The application is reopened.", err)
                    self.assertEqual(len(self.activated), 1)
                    self.assertFalse(self.context.journal_path.exists())
                else:
                    self.assertIn("The application is NOT reopened", err)
                    self.assertEqual(self.activated, [])
                    self.assertEqual(self.journal()["phase"], "paused")

    def test_db5_resume_routes_verify_the_daemon_before_the_resume_confirmation(self):
        """Audit F3: a deleting purge journal and an aborting abort-deploy journal refuse a drifted
        daemon with the daemon-drift copy before the RESUME prompt."""
        original_run = pf.pf_runner.ProcessRunner.run

        def interrupt_after_first_rm(runner, spec):
            result = original_run(runner, spec)
            if list(spec.argv[:2]) == ["rm", "-f"]:
                raise KeyboardInterrupt("Interrupted by signal 15")
            return result

        def drift():
            state = self.fake.state()
            state["info"] = pfx.daemon_info("OTHER-ENGINE-9999")
            self.fake.write_state(state)
            self.fake.clear_calls()

        for route in ("purge", "abort-deploy"):
            with self.subTest(route):
                self.state(topology(self.context))
                if route == "abort-deploy":
                    (self.context.state_dir / "deployed.json").unlink()
                    pf.write_json(self.context.journal_path, {
                        "operation": "deploy", "phase": "migrating-database", "database": "partflow_staging",
                        "started": "20261006T000000Z"})
                with mock.patch.object(pf.pf_runner.ProcessRunner, "run", interrupt_after_first_rm):
                    if route == "purge":
                        code, out, err, _ = self.purge()
                    else:
                        code, out, err, _ = self.main("abort-deploy")
                self.assertEqual(code, 1)
                journal_before = self.context.journal_path.read_bytes()
                self.assertEqual(self.journal()["phase"], "deleting" if route == "purge" else "aborting")
                drift()
                if route == "purge":
                    code, out, err, confirmations = self.purge(confirm=refuse_confirmation)
                else:
                    code, out, err, confirmations = self.main("abort-deploy", confirm=refuse_confirmation)
                self.assertEqual(code, 1, err)
                self.assertIn("ERROR: daemon-drift: Docker daemon drift: instance staging is bound to engine "
                              + pfx.ENGINE_ID, err)
                self.assertEqual(confirmations, [])
                self.assertEqual(self.fake.argvs(), [PROBE])
                self.assertEqual(self.context.journal_path.read_bytes(), journal_before)
                self.context.journal_path.unlink()
                pfx.deployed_record(self.context)

    def test_ri25_inventory_tolerates_a_container_vanishing_between_listing_and_inspect(self):
        """Audit F9: another application's short-lived container is not an inventory failure; endless
        churn is refused with a specific code after a bounded number of listings."""
        fragment = topology(self.context)
        fragment["containers"].append(pfx.container("9" * 64, "cron-job", {}, status="exited"))
        self.state(fragment, hooks=[{"after_argv_prefix": ["ps", "-a"], "mutate": [
            {"op": "remove", "list": "containers", "match": {"id": "9" * 64}}]}])
        inventory = self.controller().docker_inventory()
        self.assertEqual(len(inventory.owned_of("container")), 3)
        self.assertEqual(len([argv for argv in self.fake.argvs() if argv[:2] == ["ps", "-a"]]), 2)
        churn = [pfx.container(str(index) * 64, "cron-" + str(index), {}, status="exited") for index in (7, 8, 9)]
        fragment = topology(self.context)
        fragment["containers"] += churn
        self.state(fragment, hooks=[{"after_argv_prefix": ["ps", "-a"], "nth": number,
                                     "mutate": [{"op": "remove", "list": "containers", "match": {"id": item["id"]}}]}
                                    for number, item in enumerate(churn, start=1)])
        with self.assertRaisesRegex(pf.Failure, "^inventory-unstable: Docker inventory could not be completed"):
            self.controller().docker_inventory()
        self.assertEqual(len([argv for argv in self.fake.argvs() if argv[:2] == ["ps", "-a"]]), pf.INVENTORY_ATTEMPTS)
        self.assertEqual(self.mutations(), [])

    def test_db11_engine_drift_between_freeze_and_execution_is_plan_drift(self):
        self.state(topology(self.context), hooks=[{"after_argv_prefix": ["image", "save"], "mutate": [
            {"op": "set", "key": "info", "value": pfx.daemon_info("OTHER-ENGINE-9999")}]}])
        code, out, err, _ = self.purge()
        self.assertEqual(code, 1)
        self.assertIn("plan-drift: Planned daemon " + pfx.ENGINE_ID, err)
        self.assertFalse([argv for argv in self.fake.argvs() if argv[:1] == ["rm"] or argv[1:2] == ["rm"]])

    def test_ri24_plan_and_journal_writes_are_durable(self):
        self.state(topology(self.context))
        controller = self.controller()
        fsynced, opened = [], []
        real_fsync, real_open = os.fsync, os.open

        def record_fsync(fd):
            fsynced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return real_fsync(fd)

        def record_open(path, flags, *args, **kwargs):
            opened.append((str(path), flags))
            return real_open(path, flags, *args, **kwargs)

        with controller.lock("purge"):
            plan = controller.plan_for("purge", controller.docker_inventory(), command="purge", covered_image_refs=set())
            pf.write_json(controller.pending, {"operation": "purge", "phase": "recovery-ready"})
            with mock.patch.object(os, "fsync", record_fsync), mock.patch.object(os, "open", record_open):
                reference = controller.write_deletion_plan(plan)
                plan_dirs = sum(fsynced)
                controller.durable_phase("deleting", deletion_plan=reference, deleted=[])
        self.assertGreaterEqual(plan_dirs, 1)
        self.assertGreater(sum(fsynced), plan_dirs)
        creates = [flags for path, flags in opened if "deletion-plan.json.tmp" in path]
        self.assertTrue(creates and all(flags & os.O_EXCL and flags & os.O_NOFOLLOW for flags in creates))
        self.assertEqual(reference["sha256"], pf_docker.plan_sha256(plan))


# ============================================================================ CG: command() guard


READ_ONLY_DIRECT = {("git", "--version"), ("docker_compose", "version"), ("docker", "info"), ("docker", "version")}
REVIEWED_FUNCTIONS = {"detect_lan_ipv4"}


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _assigns_effect_before(function, call):
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and node.lineno < call.lineno:
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                        and target.value.id == "kwargs" and _is_str(target.slice) and target.slice.value == "effect":
                    return True
    return False


def effect_guard_violations(source_text):
    """Static proof (owner decision A12r2-F03): every ``.command(`` call is read-only or carries an explicit
    effect, and no literal ``effect=None`` reaches a mutating ``.docker(``/``.compose(`` call."""
    tree = ast.parse(source_text, feature_version=(3, 9))
    violations = []

    def check(call, function):
        if not isinstance(call.func, ast.Attribute):
            return
        name = call.func.attr
        keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
        starred = any(keyword.arg is None for keyword in call.keywords)
        where = f"line {call.lineno}"
        if name == "command":
            effect = keywords.get("effect")
            if effect is not None and not _is_none(effect):
                return                                                       # (a)
            if effect is not None:
                first = call.args[0] if call.args else None
                if isinstance(first, ast.List) and first.elts and _is_str(first.elts[0]):
                    tool = first.elts[0].value
                    second = first.elts[1].value if len(first.elts) > 1 and _is_str(first.elts[1]) else None
                    if (tool, second) in READ_ONLY_DIRECT or tool in ("ip", "hostname"):
                        return                                               # (b)
                if function is not None and function.name in REVIEWED_FUNCTIONS:
                    return                                                   # (c)
                violations.append(f"{where}: effect=None on an argv that is not a reviewed read-only literal")
                return
            if starred and function is not None and function.name in ("docker", "compose") \
                    and _assigns_effect_before(function, call):
                return                                                       # (d)
            violations.append(f"{where}: .command( without an explicit effect")
        elif name in ("docker", "compose"):
            effect = keywords.get("effect")
            if effect is None or not _is_none(effect):
                return
            if not all(_is_str(argument) for argument in call.args):
                violations.append(f"{where}: effect=None with dynamic arguments")
                return
            words = [argument.value for argument in call.args]
            read_only = pf.docker_effect(words) is None if name == "docker" else pf.compose_effect("p", words) is None
            if not read_only:
                violations.append(f"{where}: effect=None on mutating {name} {' '.join(words)}")

    def walk(node, function):
        for child_node in ast.iter_child_nodes(node):
            if isinstance(child_node, ast.Call):
                check(child_node, function)
            walk(child_node, child_node if isinstance(child_node, (ast.FunctionDef, ast.AsyncFunctionDef)) else function)

    walk(tree, None)
    return violations


@ROOT_REQUIRED
class CommandEffectGuard(ScopeBase):
    """Owner decision (VERDICT A12r2-F03): ``effect`` is required; mutating argv never runs with effect=None."""

    source = (PACKAGE / "pf-admin.py").read_text(encoding="utf-8")

    def test_cg1_omitting_effect_is_a_type_error(self):
        with self.assertRaises(TypeError):
            self.controller().command(["docker", "version"])

    def test_cg2_the_release_passes_the_static_guard(self):
        self.assertEqual(effect_guard_violations(self.source), [])

    def test_cg3_the_guard_flags_each_seeded_violation(self):
        seeded = {
            "missing effect": "class C:\n    def f(self):\n        self.command(['docker', 'rm', 'x'])\n",
            "effect=None on docker rm": "class C:\n    def f(self):\n        self.command(['docker', 'rm', 'x'], effect=None)\n",
            "**kwargs elsewhere": "class C:\n    def other(self, **kwargs):\n        kwargs['effect'] = None\n"
                                  "        self.command(['docker'], **kwargs)\n",
            "compose up effect=None": "class C:\n    def f(self):\n        self.compose('up', '-d', effect=None)\n",
            "docker volume rm effect=None": "class C:\n    def f(self):\n        self.docker('volume', 'rm', 'x', effect=None)\n",
            "probe through a dynamic argv": "class C:\n    def f(self):\n"
                                            "        self.command(list(DAEMON_PROBE_ARGV), effect=None)\n",
        }
        for label, text in seeded.items():
            with self.subTest(label):
                self.assertEqual(len(effect_guard_violations(text)), 1, effect_guard_violations(text))

    def test_cg4_cg5_runtime_cross_check_refuses_before_any_process(self):
        self.state()
        controller = self.controller()
        for argv in (["docker", "rm", "x"], ["docker"], ["git", "--git-dir=/s", "fetch", "approved", "x"],
                     ["docker", "compose", "-p", "p", "-f", "f", "up", "-d"],
                     ["docker_compose", "-p", "p", "-f", "f", "down"],
                     ["docker", "compose", "--profile", "x", "ps"],
                     ["docker", "compose", "-p", "p", "exec", "-T", "db", "psql", "-c", "DROP DATABASE x;"],
                     ["docker", "compose", "-p", "p", "exec", "-T", "db", "psql", "-f", "/x.sql", "-c", "SELECT 1;"]):
            with self.subTest(argv):
                with self.assertRaisesRegex(pf.Failure, "^unclassified-mutation: .*a mutating child must carry an "
                                                        "explicit effect descriptor; nothing was started."):
                    controller.command(argv, effect=None)
        self.assertEqual(self.fake.calls(), [])

    def test_cg6_read_only_compose_and_sql_pass_the_cross_check(self):
        for words in (["compose", "-p", "p", "-f", "f", "exec", "-T", "db", "psql", "-X", "-U", "u", "-d", "d", "-At",
                       "-c", "SELECT 1;"],
                      ["compose", "--project-directory", "/r", "--env-file", "/e", "-p", "p", "-f", "f", "ps"],
                      ["compose", "version"], ["compose", "-p", "p", "-f", "f", "config", "--format", "json"]):
            with self.subTest(words):
                self.assertIsNone(pf.unclassified_mutation("docker", words))
        self.assertIsNone(pf.unclassified_mutation("docker_compose", ["version"]))
        self.assertIsNone(pf.unclassified_mutation("ip", ["-4", "addr"]))
        self.state()
        controller = self.controller()
        output = controller.command(["docker", "compose", "-p", "p", "-f", "f", "exec", "-T", "db", "psql", "-c",
                                     "SELECT 1;"], effect=None)
        self.assertEqual(output, "")

    def test_cg7_one_runner_call_site_and_the_literal_probe(self):
        self.assertEqual(self.source.count("self.runner.run("), 1)
        tree = ast.parse(self.source, feature_version=(3, 9))
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        self.assertIn("self.runner.run(", ast.get_source_segment(self.source, functions["command"]))
        probes = [call for call in ast.walk(functions["verify_daemon"]) if isinstance(call, ast.Call)
                  and isinstance(call.func, ast.Attribute) and call.func.attr == "command"]
        self.assertEqual(len(probes), 1)
        literal = tuple(element.value for element in probes[0].args[0].elts)
        self.assertEqual(literal, pf_docker.DAEMON_PROBE_ARGV)
        self.assertEqual(pf.TIMEOUT_DAEMON_PROBE, 30.0)


# ============================================================================ RW: release wiring


RELEASE_MODULES = ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "pf_runner.py", "pf_config.py", "pf_source.py",
                   "pf_docker.py", "pf_install.py")


class ReleaseWiring(unittest.TestCase):

    def test_rw1_pf_docker_is_a_required_installed_release_file(self):
        self.assertIn("pf_docker.py", pf_bootstrap.REQUIRED_RELEASE_FILES)
        # PF-A2.1: the installer (pf_install.py, run by the thin install-control.sh init wrapper) stages exactly
        # CONTROL_RELEASE_FILES; the shell script no longer copies files itself.
        self.assertEqual(pf.pf_install.CONTROL_RELEASE_FILES["pf_docker.py"], "deploy/synology/pf_docker.py")
        self.assertIn('"$SCRIPT_DIR/pf_install.py" "$@"', (PACKAGE / "install-control.sh").read_text())
        self.assertIn("pf_docker.py", pfx.release_files())
        self.assertNotIn("fixtures", json.dumps(sorted(pfx.release_files())))

    def test_rw2_release_modules_parse_as_python_39_and_compile_without_warnings(self):
        for name in RELEASE_MODULES:
            with self.subTest(name):
                source = (PACKAGE / name).read_text(encoding="utf-8")
                ast.parse(source, filename=name, feature_version=(3, 9))
                with warnings.catch_warnings():
                    warnings.simplefilter("error", DeprecationWarning)
                    warnings.simplefilter("error", SyntaxWarning)
                    compile(source, name, "exec")

    def test_rw3_pf_docker_is_pure(self):
        source = (PACKAGE / "pf_docker.py").read_text(encoding="utf-8")
        for forbidden in ("import subprocess", "os.system(", "os.environ", "shell=True", "import os"):
            self.assertNotIn(forbidden, source)

    def test_rw4_read_only_docker_forms(self):
        for entry in (("info",), ("container", "inspect"), ("volume", "inspect"), ("network", "inspect")):
            self.assertIn(entry, pf.DOCKER_READ_ONLY)
        self.assertIn("container", pf.DOCKER_GROUPS)
        self.assertIsNone(pf.docker_effect(["container", "inspect", "--format", "x", "id"]))
        self.assertEqual(pf.docker_effect(["container", "rm", "id"])["verb"], "container rm")

    def test_rw5_prefix_based_resource_discovery_is_gone(self):
        source = (PACKAGE / "pf-admin.py").read_text(encoding="utf-8")
        for absent in ("def project_resources", "def detailed_project_resources", ".startswith(project",
                       "startswith(prefix)", 'compose("down"'):
            self.assertNotIn(absent, source)

    def test_rw6_checkpoint(self):
        self.assertEqual(pf.CHECKPOINT, "PF-A2.1")


if __name__ == "__main__":
    unittest.main()
