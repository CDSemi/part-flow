"""Offline tests. Docker/PostgreSQL are simulated; archive and filesystem work is real."""
import contextlib
import grp
import io
import json
import os
from pathlib import Path
import re
import signal
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

PACKAGE = pfx.PACKAGE
REPO_PACKAGE = pfx.REPO_PACKAGE
pf = pfx.pf
pf_instance = pfx.pf_instance
OLD = pfx.OLD
NEW = pfx.NEW
TEST_GROUP = grp.getgrgid(os.getgid()).gr_name
source_fixture = pfx.source_fixture
# PF-A3.1: realistic image IDs (sha256:<64 hex>) and project-prefixed tags; every lifecycle record requires them.
OLD_BACKEND, OLD_FRONTEND = pfx.image_id("old-backend"), pfx.image_id("old-frontend")
NEW_BACKEND, NEW_FRONTEND = pfx.image_id("new-backend"), pfx.image_id("new-frontend")
DB_IMAGE_ID = pfx.image_id("postgres-16")
PROJECT = "partflow-staging"


def checkpoint(controller, reason="scheduled-or-manual-backup"):
    """A healthy checkpoint taken the way every route takes one: inside a locked operation."""
    with contextlib.redirect_stdout(io.StringIO()), controller.lock():
        return controller.snapshot(reason)


def open_operations(controller):
    """PF-A3.2: [(kind, phase)] of the blocking lifecycle operations of the controller's instance."""
    return [(entry.kind, entry.phase) for entry in controller.operation_index().blocking]


def latest_operation(controller, kind=None):
    """PF-A3.2: (operation_id, plan, journal) of the newest operation with a plan (optionally of one kind)."""
    found = pfx.operations_of(controller.context, kind)
    return found[-1] if found else None


def effect_states(journal, plan, kind=None):
    """{target: state} of a journal (optionally only effects of one type)."""
    states = {item["effect_id"]: item["state"] for item in journal["effects"]}
    return {effect["target"]: states[effect["effect_id"]] for effect in plan["effects"]
            if kind is None or effect["type"] == kind}


def rewrite_manifest(folder, mutate):
    """Rewrite a schema 1 manifest in place (normalized bytes and manifest.sha256), as an attacker with write access
    to the protected folder could: ``mutate(manifest)`` edits the parsed manifest."""
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_bytes())
    mutate(manifest)
    data = pf_instance.normalize_json(manifest)
    (folder / "manifest.json").write_bytes(data)
    (folder / "manifest.sha256").write_text(pf_instance.sha256_bytes(data) + "\n")
    return manifest


def payload_entry(folder, path):
    data = (Path(folder) / path).read_bytes()
    return {"size": len(data), "sha256": pf_instance.sha256_bytes(data)}


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



def fake_inventory(context, resources):
    """PF-A1.3: an exact inventory (pf_docker.classify_inventory) built from fixture name lists.

    Containers are owned Compose containers of this instance (service from the name, else db);
    volumes and networks carry this instance's labels; image tags are owned instance tags.
    """
    pf_docker = pf.pf_docker
    project = context.compose_project
    containers = []
    for index, name in enumerate(resources.get("containers", [])):
        service = name if name in pf_docker.SERVICES else "db"
        containers.append(pfx.container(f"{index:02d}{name}".ljust(64, "0")[:64], name,
                                        pfx.labels_for(context, service)))
    volumes = [pfx.volume(name, dict(pfx.labels_for(context), **{pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"}))
               for name in resources.get("volumes", [])]
    networks = [pfx.network(("n" + name).ljust(64, "0")[:64], name,
                            dict(pfx.labels_for(context), **{pf_docker.COMPOSE_NETWORK_LABEL: "default"}))
                for name in resources.get("networks", [])]
    images = [{"reference": reference, "id": "sha256:" + reference.split(":", 1)[0],
               "labels": {pf_docker.INSTANCE_LABEL: context.instance_id}}
              for reference in resources.get("images", [])]
    parsed = {
        "container": pf_docker.parse_field_lines("\n".join(json.dumps(item) for item in containers), kind="container"),
        "volume": pf_docker.parse_field_lines("\n".join(json.dumps(item) for item in volumes), kind="volume"),
        "network": pf_docker.parse_field_lines("\n".join(json.dumps(item) for item in networks), kind="network"),
    }
    return pf_docker.classify_inventory(project=project, instance_id=context.instance_id,
                                        containers=parsed["container"], volumes=parsed["volume"],
                                        networks=parsed["network"], images=images)


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
        self.tags = {PROJECT + "-backend:old": OLD_BACKEND, PROJECT + "-frontend:old": OLD_FRONTEND,
                     "postgres:16": DB_IMAGE_ID}
        self.contracts = {OLD_BACKEND: {"files": pf.migration_files(self.root), "heads": ["r1"]}}
        self.current_images = {"backend": OLD_BACKEND, "frontend": OLD_FRONTEND, "db": DB_IMAGE_ID}
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
        self.droppable = set()  # PF-A3.1: databases a restore-instance test lets the fake drop (the empty init db)
        pfx.trust_daemon(self)

    def verify_daemon(self, *, refresh=False):
        # The daemon binding is exercised by test_docker_scope.py; here it is a verified fixture.
        return self._daemon

    def docker_inventory(self):
        return fake_inventory(self.context, self.resources)

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

    def deployed_source_origin(self, revision):
        # The fake protected store holds every commit the fixture deployed (create_deployed_source_archive below).
        return "protected-store" if isinstance(revision, str) and pf.SHA_RE.fullmatch(revision) else None

    def prove_tree_commit(self, tree, revision):
        # The fake protected store exports the fixture tree of a commit: the proof compares byte for byte.
        if self.deployed_source_origin(revision) is None:
            return False
        with tempfile.TemporaryDirectory() as tmp:
            exact = Path(tmp) / "exact"
            source_fixture(exact, revision)
            expected = self.candidate_manifest(exact, revision, verified=True)
            return bool(pf.pf_source.compare_manifest(tree, expected, excludes=pf.SOURCE_EXCLUDES)["matches"])

    def create_deployed_source_archive(self, destination, revision):
        if self.deployed_source_origin(revision) is None:
            raise pf.Failure(self.CANNOT_RECONSTRUCT)
        with tempfile.TemporaryDirectory() as tmp:
            exact = Path(tmp) / "exact"
            source_fixture(exact, revision)
            manifest = self.candidate_manifest(exact, revision, verified=True)
            _, expanded, members_sha256 = pf.pf_source.archive_verified_tree(exact, manifest, destination)
        return {"origin": "protected-store", "manifest": manifest, "expanded_bytes": expanded,
                "members": len(manifest["entries"]), "members_sha256": members_sha256}

    def write_workspace_manifest(self, manifest):
        # PF-A3.2: W4 (and deploy --current) record the new workspace; the simulated comparison follows it.
        digest = super().write_workspace_manifest(manifest)
        source = manifest["source"]
        self.workspace_head = source.get("commit") if source["kind"] == "git_commit" else None
        self.workspace_dirty = False
        return digest

    def inspect(self, service):
        return {"Image": self.current_images.get(service, DB_IMAGE_ID),
                "State": {"Running": self.running[service], "Health": {"Status": "healthy"}},
                "Config": {"Env": ["POSTGRES_DB=partflow_staging", "POSTGRES_USER=partflow_staging"]}}

    def record_render(self, root, override):
        """What require_envelope records for an approved render (PF-A1.3): the seal selects it by its input key."""
        if self.operation_dir is None:
            return
        selected = Path(override) if override else self.override
        selected = selected if selected.exists() else None
        compose_bytes = pf_instance.read_bytes_nofollow(self.control_dir / "compose.nas.yaml")
        model = {"name": self.context.compose_project, "services": {"render": len(self.calls)}}
        self._envelope_sequence += 1
        name = f"compose-{self._envelope_sequence}.json"
        pf_instance._write_private_file(self.operation_dir / name, pf_instance.normalize_json(model), 0o600)
        self._append_envelope_record({
            "sequence": self._envelope_sequence, "compose_version": "Docker Compose version v2.40.2-fixture",
            "inputs": {"compose_file": str(self.control_dir / "compose.nas.yaml"),
                       "compose_file_sha256": pf_instance.sha256_bytes(compose_bytes),
                       "override": str(selected) if selected else None,
                       "override_sha256": pf_instance.sha256_bytes(selected.read_bytes()) if selected else None,
                       "project_directory": str(Path(root or self.root)), "repo_root": str(Path(root or self.root)),
                       "frozen_env_sha256": self.frozen.env_sha256 if self.frozen is not None else None,
                       "instance_id": self.context.instance_id, "effective_values_sha256": "0" * 64,
                       "value_overrides": []},
            "resolved_file": name, "resolved_sha256": pf_instance.sha256_bytes(pf_instance.normalize_json(model)),
            "escape_mode": "doubled", "result": "approved"})

    def docker(self, *args, **kwargs):
        self.calls.append(("docker", args))
        if args[:2] in (("image", "save"), ("image", "load")):
            kwargs.setdefault("effect", pf.docker_effect(args))
            return self.command(["docker", *args], **kwargs)
        # PF-A1.3: per-resource deletion of the frozen plan (never `compose down`).
        if args[:2] == ("rm", "-f"):
            name = next(name for index, name in enumerate(self.resources.get("containers", []))
                        if f"{index:02d}{name}".ljust(64, "0")[:64] == args[2])
            self.resources["containers"] = [item for item in self.resources["containers"] if item != name]
            self.running[name if name in self.running else "db"] = False
            return ""
        if args[:2] == ("volume", "rm"):
            self.resources["volumes"] = [item for item in self.resources["volumes"] if item != args[2]]
            self.dbs.pop("partflow_staging", None)
            self.running = {"db": False, "backend": False, "frontend": False}
            return ""
        if args[:2] == ("network", "rm"):
            self.resources["networks"] = [item for item in self.resources.get("networks", [])
                                          if ("n" + item).ljust(64, "0")[:64] != args[2]]
            return ""
        if args[0] == "version":
            return "28.0.0"
        if args[0] == "tag":
            self.tags[args[2]] = args[1]
            return ""
        if args[:2] == ("image", "inspect"):
            if args[2] in self.tags:
                found = self.tags[args[2]]
            elif args[2] in self.tags.values():
                found = args[2]
            else:
                raise pf.Failure("image missing")
            return json.dumps([{"Id": found, "Os": "linux", "Architecture": "amd64", "RepoDigests": []}])
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
        # PF-A3.1 section 3.8 read-only inventory statements and the journaled connection window.
        if sql == pf.FACTS_SQL:
            return "\n".join(f"{name}|partflow_staging|UTF8|en_US.utf8|en_US.utf8|"
                             f"{'t' if item.get('connections', True) else 'f'}" for name, item in sorted(self.dbs.items()))
        if sql == pf.EXTENSIONS_SQL:
            return "plpgsql|1.0"
        if sql == pf.ROW_COUNTS_SQL:
            return f"public.records|{len(self.dbs[database]['rows'])}"
        if sql == pf.ROLES_SQL:
            return "partflow_staging|f|f|t|t|f|f"
        if sql == pf.AVAILABLE_EXTENSIONS_SQL:
            return "pg_trgm\nplpgsql"
        if sql.startswith("ALTER DATABASE") and "ALLOW_CONNECTIONS" in sql:
            name = re.search(r'ALTER DATABASE "([^"]+)"', sql).group(1)
            self.dbs[name]["connections"] = "ALLOW_CONNECTIONS true" in sql
            return "ALTER DATABASE"
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
            self.record_render(root, override)
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
                input_file.read()
                if output:
                    output.write(b"mock archive list\n")
                return ""
            assert args[4:6] == ("-U", "partflow_staging"), args  # the frozen role, not a shell variable
            if program == "pg_dump":
                if self.fail == "dump":
                    raise pf.Failure("simulated dump failure")
                output.write(json.dumps(self.dbs[args[args.index("-d") + 1]]).encode())
                return ""
            if program == "pg_dumpall":
                output.write(b"-- globals\n")
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
                # PF-A3.1: a pf_restore_ candidate is dropped when the selected checkpoint is incompatible.
                # PF-A3.2: a resumed reset drops its own pf_clean_ candidate before redoing it.
                assert name.startswith(("pf_verify_", "pf_migrate_", "pf_restore_", "pf_clean_")) \
                    or name in self.droppable, (name, sorted(self.droppable))
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
            image_id = pfx.image_id("new-" + service)
            reference = f"{PROJECT}-{service}:candidate"
            self.tags[reference] = image_id
            images[service] = {"reference": reference, "id": image_id}
        self.contracts[NEW_BACKEND] = {
            "files": pf.migration_files(candidate), "heads": ["r2" if self.new_migration else "r1"]}
        override = self.state / "candidate-images.yaml"
        self.make_override(images, override)
        return images, override

    def automatic_guard(self, current, candidate, target_sha):
        if self.fail == "divergence":
            raise pf.Deferred("not a descendant")
        if self.new_migration:
            raise pf.Deferred("migration change")

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
        # Interactive harness: an operator at a terminal answers every prompt (PF-A1.4 unattended gate).
        with (
            mock.patch.object(pf, "Controller", return_value=self.c),
            mock.patch.object(pf, "confirm"),
            mock.patch.object(pf, "prompt_yes_no", return_value=True),
            mock.patch.object(pf, "unattended", return_value=False),
        ):
            before = len(self.errors_text())
            code = pf.main(arguments, installation_root=self.layout.root,
                           running_release=self.layout.release_dir, trusted_launch=True)
        # No test passes vacuously on an unattended-gate refusal (PF-A1.4 SPEC section 6.2).
        for gate in ("terminal-required", "instance-required-unattended", "policy-grant-required"):
            self.assertNotIn(gate, self.errors_text()[before:])
        return code

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
        self.assertEqual(open_operations(self.c), [])
        _, plan, journal = latest_operation(self.c, "deploy")
        self.assertEqual((journal["phase"], journal["result"]["outcome"]), ("completed", "succeeded"))
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
        # PF-A3.2: an interrupted deploy whose initial migration is unknown (frontend effect not started).
        plan = pfx.lifecycle_plan(self.context, "deploy")
        deploy_op = plan["operation_id"]
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(
            plan, phase="initializing", states={"e0001": "complete", "e0002": "complete", "e0003": "unknown"},
            unresolved="e0003"))
        self.assertEqual(self.invoke(["abort-deploy"]), 0)
        self.assertEqual(open_operations(self.c), [])
        abort_op, abort_plan, journal = latest_operation(self.c, "abort-deploy")
        self.assertEqual((abort_plan["supersedes"], journal["phase"]), (deploy_op, "completed"))
        self.assertEqual(self.c.operation_index().entry(deploy_op).cls, "superseded")
        self.assertNotIn("partflow_staging", self.c.dbs)
        self.assertFalse(any(self.c.running.values()))
        self.assertTrue((self.c.config_dir / ".env").exists())

    def test_abort_deploy_refuses_cleanup_after_frontend_may_have_opened(self):
        write_deploy_env(self.root)
        (self.c.state / "deployed.json").unlink()
        plan = pfx.lifecycle_plan(self.context, "deploy")
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(
            plan, phase="activating", unresolved="e0005",
            states={"e0001": "complete", "e0002": "complete", "e0003": "complete", "e0004": "complete",
                    "e0005": "unknown"}))
        before = pfx.operation_files(self.context, plan["operation_id"])
        self.assertEqual(self.invoke(["abort-deploy"]), 1)
        self.assertIn("operation-open: operation " + plan["operation_id"] + " (deploy, phase activating)",
                      self.errors_text())
        self.assertEqual(pfx.operation_files(self.context, plan["operation_id"]), before)
        self.assertEqual(open_operations(self.c), [("deploy", "activating")])
        self.assertIn("partflow_staging", self.c.dbs)

    def test_deploy_refuses_existing_project_resources_without_touching_database(self):
        write_deploy_env(self.root)
        self.c.resources = {"containers": ["abc"], "volumes": ["partflow-staging_postgres_data"]}
        before = dict(self.c.dbs)
        self.assertEqual(self.invoke(["deploy", "--latest"]), 1)
        self.assertEqual(self.c.dbs, before)
        self.assertEqual(pfx.operations_of(self.context), [])
        self.assertTrue(all(self.c.running.values()))

    def test_prepare_new_env_generates_password_and_direct_lan_values(self):
        self.c = pf.Controller(self.context)
        (self.c.config_dir / ".env").unlink()
        # PF-A2.2: a password is generated only for a never-deployed instance (the wizard's deployed predicate).
        (self.c.state / "deployed.json").unlink()
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
        (self.c.state / "deployed.json").unlink()
        # PF-A2.2: the declaration order asks the port before the Reverse Proxy hostname.
        answers = iter(["", "", "", "2", "", "partflow.internal.example", "y"])
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

    def test_update_backs_up_then_switches_source_and_images(self):
        control_before = (self.c.control_dir / "pf-admin.py").read_text()
        config_before = (self.c.config_dir / "pf-config.json").read_text()
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        self.assertEqual(self.c.revision(), NEW)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertTrue(all(self.c.running.values()))
        self.assertEqual(open_operations(self.c), [])
        saved = self.c.snapshots()[0]
        # PF-A3.1 mapping: source_revision -> the proven commit, restore_test -> the external verification level.
        self.assertEqual(saved.manifest["source"]["commit"], OLD)
        self.assertEqual(saved.level, "data_restore_verified")
        self.assertEqual(self.c.ci_calls, [NEW])
        self.assertEqual((self.root / "pf.sh").read_text(), "# repository source copy\n")
        self.assertEqual((self.root / "deploy/synology/pf-admin.py").read_text(), "# repository source helper\n")
        self.assertEqual((self.c.control_dir / "pf-admin.py").read_text(), control_before)
        self.assertEqual((self.c.config_dir / "pf-config.json").read_text(), config_before)
        self.assertEqual(self.c.env()["POSTGRES_PASSWORD"], "abc123")

    def test_workspace_switch_replaces_the_repository_and_retains_the_old_tree(self):
        """PF-A3.2 (the former replace_source test, strictly stronger): the update's generation switch binds the
        deployed tree as the workspace, keeps the whole old tree (also files the new tree lacks) as a retained
        generation, and never touches the external control release or configuration."""
        (self.root / "compose.nas.yaml").write_text("local compose\n")
        (self.root / "docs/deployment").mkdir(parents=True)
        (self.root / "docs/deployment/SYNOLOGY_ADMIN.md").write_text("old docs\n")
        (self.root / "untracked-notes.txt").write_text("editor notes\n")
        control_before = (self.c.control_dir / "pf-admin.py").read_text()
        config_before = (self.c.config_dir / "pf-config.json").read_text()
        old_inode = os.stat(self.root).st_ino
        original = self.c.materialize_source

        def materialize(target, destination):
            original(target, destination)
            (destination / "compose.nas.yaml").write_text("remote compose\n")
            (destination / "deploy/synology/pf-admin.py").write_text("remote controller source\n")
            (destination / "docs/deployment").mkdir(parents=True)
            (destination / "docs/deployment/SYNOLOGY_ADMIN.md").write_text("new docs\n")

        self.c.workspace_dirty = True
        with mock.patch.object(self.c, "materialize_source", side_effect=materialize):
            self.assertEqual(self.invoke(["update", "--latest"]), 0, self.errors_text())
        self.assertEqual((self.root / "compose.nas.yaml").read_text(), "remote compose\n")
        self.assertEqual((self.root / "deploy/synology/pf-admin.py").read_text(), "remote controller source\n")
        self.assertEqual((self.root / "docs/deployment/SYNOLOGY_ADMIN.md").read_text(), "new docs\n")
        self.assertFalse((self.root / "untracked-notes.txt").exists())
        self.assertNotEqual(os.stat(self.root).st_ino, old_inode)
        _, plan, journal = latest_operation(self.c, "update")
        generation = plan["workspace"]["generation_id"]
        retained = pf_instance.generation_container(self.context) / generation
        self.assertEqual(os.stat(retained).st_ino, old_inode)
        self.assertEqual((retained / "docs/deployment/SYNOLOGY_ADMIN.md").read_text(), "old docs\n")
        self.assertEqual((retained / "compose.nas.yaml").read_text(), "local compose\n")
        self.assertEqual((retained / "untracked-notes.txt").read_text(), "editor notes\n")
        self.assertIn({"kind": "workspace-generation", "name": generation, "sha256": None},
                      journal["retained_artifacts"])
        self.assertEqual(stat.S_IMODE(os.stat(retained.parent).st_mode), 0o700)
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

    def apply_permissions(self):
        """PF-A2.3: the former bare `permissions` repair is `permissions apply` with the derived policy (every
        wizard default kept, one typed confirmation); it approves permission policy revision 1."""
        with mock.patch("builtins.input", side_effect=[""] * 11 + ["APPLY PERMISSIONS staging"]):
            self.assertEqual(self.invoke(["--instance", "staging", "permissions", "apply"]), 0, self.errors_text())
        record = json.loads((self.context.paths.private_state / "permission-policy.json").read_text())
        self.assertEqual(record["revision"], 1)

    def test_permissions_make_repo_and_config_writable_but_backups_read_only(self):
        source = self.root / "frontend/app.txt"
        os.chmod(source, 0o600)
        backup = self.c.backups_dir / "read-only.txt"
        backup.parent.mkdir(parents=True)
        backup.write_text("backup\n")
        os.chmod(backup, 0o600)
        config = self.c.config_dir / "pf-config.json"
        os.chmod(config, 0o600)

        self.apply_permissions()

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
        self.apply_permissions()
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o640)
        self.assertEqual(folder.stat().st_gid, grp.getgrnam(TEST_GROUP).gr_gid)
        self.assertEqual(file.stat().st_gid, grp.getgrnam(TEST_GROUP).gr_gid)

    def test_completed_checkpoint_is_group_readable_but_not_group_writable(self):
        view = checkpoint(self.c, "scheduled-or-manual-backup")
        folder = self.c.backups_dir / view.bundle_id

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
        self.assertEqual(pfx.operations_of(self.context), [])

    def test_manual_ci_bypass_is_explicit(self):
        self.c.fail = "ci"
        self.assertEqual(self.invoke(["update", "--latest", "--skip-ci"]), 0)
        self.assertEqual(self.c.ci_calls, [])

    def test_migration_requires_explicit_approval(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest"]), 20)
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(pfx.operations_of(self.context), [])

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
        # PF-A3.2: the update stays open in migrating with the rehearsal migration unknown (an owned candidate).
        _, plan, journal = latest_operation(self.c, "update")
        self.assertEqual(open_operations(self.c), [("update", "migrating")])
        states = effect_states(journal, plan, "database-migrate")
        self.assertEqual(sorted(states.values()), ["not_started", "unknown"])

    def test_live_migration_failure_is_not_auto_downgraded(self):
        self.c.new_migration = True
        self.c.fail = "live-migration"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        _, plan, journal = latest_operation(self.c, "update")
        self.assertEqual(open_operations(self.c), [("update", "migrating")])
        self.assertEqual(effect_states(journal, plan, "database-migrate")["database:partflow_staging:heads=r2"],
                         "unknown")
        self.assertEqual(journal["unresolved_effect"], plan["effects"][6]["effect_id"])
        self.assertFalse(self.c.running["backend"])
        self.assertFalse(any("downgrade" in str(call) for call in self.c.calls))

    def test_dump_failure_never_replaces_source(self):
        self.c.fail = "dump"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.c.revision(), OLD)
        # PF-A3.1: a capture interrupted before its manifest seal is listed as invalid and never selectable.
        self.assertIsInstance(self.c.snapshots()[0], pf.InvalidBundle)
        self.assertEqual(self.c.snapshots()[0].code, "manifest-missing")
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
        self.assertEqual(self.c.snapshots()[0].reason, "before-reset")

    def test_reset_refuses_external_sessions_without_killing_them(self):
        self.c.connected_sessions = 1
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertFalse(any("terminate_backend" in str(call) for call in self.c.calls))

    def test_reset_failed_atomic_swap_preserves_current_data(self):
        self.c.fail = "swap"
        self.assertEqual(self.invoke(["reset-db"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record"])
        self.assertEqual(open_operations(self.c), [("reset-db", "switching")])

    def test_code_rollback_preserves_newer_rows(self):
        self.assertEqual(self.invoke(["update", "--latest"]), 0)
        selected = self.c.snapshots()[0].bundle_id
        self.c.dbs["partflow_staging"]["rows"].append("newer-record")
        self.assertEqual(self.invoke(["rollback", selected]), 0)
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record", "newer-record"])
        self.assertFalse(any(k.startswith("pf_keep_") for k in self.c.dbs))

    def test_code_rollback_refuses_schema_mismatch(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0)
        selected = self.c.snapshots()[0].bundle_id
        self.assertEqual(self.invoke(["rollback", selected]), 1)
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.db_heads(), ["r2"])

    def test_full_rollback_restores_old_schema_and_keeps_new_database(self):
        self.c.new_migration = True
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 0)
        selected = self.c.snapshots()[0].bundle_id
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
        self.assertEqual(open_operations(self.c), [("update", "activating")])
        self.assertFalse(self.c.running["backend"])
        self.assertFalse(self.c.running["frontend"])

    def test_full_rollback_recovers_failed_update(self):
        self.c.new_migration = True
        self.c.fail = "health"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        selected = self.c.snapshots()[0].bundle_id
        self.c.fail = None
        self.assertEqual(self.invoke(["rollback", selected, "--restore-db"]), 0)
        self.assertEqual(open_operations(self.c), [])
        update_op, _, _ = latest_operation(self.c, "update")
        self.assertEqual(self.c.operation_index().entry(update_op).cls, "superseded")
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.db_heads(), ["r1"])

    def test_resume_only_before_changes(self):
        self.c.fail = "dump"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.c.fail = None
        self.assertEqual(self.invoke(["resume"]), 0)
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(open_operations(self.c), [])
        _, _, journal = latest_operation(self.c, "update")
        self.assertEqual(journal["phase"], "cancelled")

    def test_resume_refuses_after_live_migration_phase(self):
        self.c.new_migration = True
        self.c.fail = "live-migration"
        self.assertEqual(self.invoke(["update", "--latest", "--allow-migrations"]), 1)
        self.c.fail = None
        alembic = len([call for call in self.c.calls if call[0] == "compose" and "alembic" in call[1]])
        self.assertEqual(self.invoke(["resume"]), 1)
        # PF-A3.2: the live migration's result is unknown with the heads unchanged -> needs_operator, no retry.
        self.assertIn("effect-unknown:", self.errors_text())
        self.assertEqual(open_operations(self.c), [("update", "needs_operator")])
        self.assertEqual(len([call for call in self.c.calls if call[0] == "compose" and "alembic" in call[1]]),
                         alembic)

    def test_checkpoint_checksum_corruption_is_detected(self):
        view = checkpoint(self.c)
        (self.c.backups_dir / view.bundle_id / "database.dump").write_bytes(b"corrupted")
        # PF-A3.1: the coded refusal of the strict reader (section 3.1 step 7).
        with self.assertRaisesRegex(pf.Failure, "^bundle-payload-mismatch: " + view.bundle_id + ": database.dump: size"):
            self.c.verify_snapshot(view.bundle_id)

    def test_manifest_tampering_is_detected(self):
        view = checkpoint(self.c)
        path = self.c.backups_dir / view.bundle_id / "manifest.json"
        path.write_text(path.read_text() + " ")
        with self.assertRaisesRegex(pf.Failure, "^manifest-checksum-mismatch: " + view.bundle_id + ": manifest.json "
                                    "does not match manifest.sha256. Nothing was changed."):
            self.c.verify_snapshot(view.bundle_id)

    def test_rollback_refuses_checkpoint_source_with_reserved_paths_before_any_effect(self):
        """A12-R02: a checkpoint archive carrying a path the manifest cannot verify is refused
        before confirmation, pause, safety snapshot or database swap."""
        view = checkpoint(self.c)
        folder = self.c.backups_dir / view.bundle_id
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "tree"
            source_fixture(tree, OLD)
            (tree / "nested" / "node_modules").mkdir(parents=True)
            (tree / "nested" / "node_modules" / "tracked.js").write_text("module.exports = 1;\n")
            (folder / "source.tar.gz").write_bytes(pfx.tar_gz_bytes(tree))

        def swap_payload(manifest):
            for payload in manifest["payloads"]:
                if payload["path"] == "source.tar.gz":
                    payload.update(payload_entry(folder, "source.tar.gz"), expanded_bytes=None, members=None,
                                   members_sha256=None)

        rewrite_manifest(folder, swap_payload)
        with mock.patch.object(self.c, "snapshot", side_effect=AssertionError("snapshot must not run")), \
             mock.patch.object(self.c, "_capture", side_effect=AssertionError("capture must not run")), \
             mock.patch.object(self.c, "pause", side_effect=AssertionError("pause must not run")), \
             mock.patch.object(self.c, "swap_database", side_effect=AssertionError("swap must not run")):
            with self.assertRaisesRegex(pf.Failure, "cannot verify.*nested/node_modules/tracked.js"):
                self.c.rollback(view.bundle_id, restore_database=True)
            self.assertEqual(self.invoke(["rollback", view.bundle_id, "--restore-db"]), 1)
        self.assertEqual(open_operations(self.c), [])
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.revision(), OLD)
        self.assertFalse(any(name.startswith(("pf_keep_", "pf_restore_")) for name in self.c.dbs))
        self.assertEqual((self.root / "app-version.txt").read_text(), OLD)

    def test_rollback_proves_source_provenance_before_any_confirmation_or_effect(self):
        """A12r2-F02: the store proof can refuse (a legacy commit tracking a reserved name); that
        refusal must come before confirmation, pause, safety snapshot or database swap."""
        view = checkpoint(self.c)
        refusal = pf.Failure("Source provenance check failed: unsupported source path (tracked reserved "
                             "workspace artifact name): node_modules/x.js")
        with mock.patch.object(self.c, "prove_tree_commit", side_effect=refusal) as proof, \
             mock.patch.object(pf, "confirm", side_effect=AssertionError("confirmation must not be asked")), \
             mock.patch.object(self.c, "snapshot", side_effect=AssertionError("snapshot must not run")), \
             mock.patch.object(self.c, "_capture", side_effect=AssertionError("capture must not run")), \
             mock.patch.object(self.c, "pause", side_effect=AssertionError("pause must not run")), \
             mock.patch.object(self.c, "swap_database", side_effect=AssertionError("swap must not run")):
            with self.assertRaisesRegex(pf.Failure, "tracked reserved workspace artifact name"):
                self.c.rollback(view.bundle_id, restore_database=True)
        proof.assert_called_once()
        self.assertEqual(pfx.operations_of(self.context), [])
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(self.c.revision(), OLD)
        self.assertFalse(any(name.startswith(("pf_keep_", "pf_restore_")) for name in self.c.dbs))

    def test_fail_closed_runs_when_the_error_report_cannot_be_written(self):
        """A12r2-F01: after a hangup stderr is a dead terminal; the fail-closed stop must still run."""
        class GoneTerminal(io.StringIO):
            def write(self, text):
                raise OSError(5, "Input/output error")

        def interrupted_rollback(*args, **kwargs):
            raise KeyboardInterrupt("Interrupted by signal 1")

        view = checkpoint(self.c)
        with mock.patch.object(self.c, "rollback", side_effect=interrupted_rollback), \
             mock.patch.object(self.c, "fail_closed") as fail_closed, \
             mock.patch.object(sys, "stderr", GoneTerminal()):
            with self.assertRaises(OSError):
                self.invoke(["rollback", view.bundle_id, "--restore-db"])
        fail_closed.assert_called_once_with()

    def test_missing_retained_image_blocks_rollback(self):
        view = checkpoint(self.c)
        del self.c.tags[view.images["backend"]["reference"]]
        self.assertEqual(self.invoke(["rollback", view.bundle_id]), 1)
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

    # PF-A1.4: `release-check --apply` is refused by the auto-apply gate (no protected grant in A1), so
    # the guards of update(automatic=True) are exercised directly; the path stays for the PF-A4.3 grant.

    def automatic_update(self):
        with self.c.lock("release-check"):
            self.c.update(self.c.target, automatic=True)

    def test_auto_update_is_opt_in(self):
        with self.assertRaisesRegex(pf.Failure, "Unattended update is disabled"):
            self.automatic_update()
        self.assertTrue(self.c.running["frontend"])

    def test_auto_update_same_schema_succeeds_without_prompt(self):
        self.c.config["auto_update"] = True
        with mock.patch.object(pf, "confirm") as confirmation:
            self.automatic_update()
            confirmation.assert_not_called()
        self.assertEqual(self.c.revision(), NEW)

    def test_auto_update_never_migrates(self):
        self.c.config["auto_update"] = True
        self.c.new_migration = True
        with self.assertRaisesRegex(pf.Deferred, "migration change"):
            self.automatic_update()
        self.assertTrue(self.c.running["frontend"])
        self.assertEqual(pfx.operations_of(self.context), [])

    def test_auto_update_refuses_divergent_history(self):
        self.c.config["auto_update"] = True
        self.c.fail = "divergence"
        with self.assertRaisesRegex(pf.Deferred, "not a descendant"):
            self.automatic_update()
        self.assertEqual(self.c.revision(), OLD)

    def test_release_check_apply_is_refused_by_policy(self):
        self.c.config["auto_update"] = True
        with mock.patch.object(self.c, "resolve", wraps=self.c.resolve) as resolve, \
             mock.patch.object(self.c, "lock", wraps=self.c.lock) as lock:
            self.assertEqual(self.invoke(["--instance", "staging", "release-check", "--apply"]), 20)
        self.assertIn("auto-apply-not-permitted: release apply needs a protected policy that permits it; approved "
                      "policy revision 1 of instance staging does not", self.errors_text())
        self.assertEqual(self.c.revision(), OLD)
        resolve.assert_not_called()
        lock.assert_not_called()
        self.assertTrue(self.c.running["frontend"])

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
        plan = pfx.lifecycle_plan(self.context, "update")
        pfx.write_operation(self.context, plan, pfx.lifecycle_journal(
            plan, phase="migrating", unresolved="e0007", states={**{f"e000{n}": "complete" for n in range(1, 7)},
                                                                 "e0007": "unknown"}))
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertIn("operation-open: operation " + plan["operation_id"] + " (update, phase migrating) is incomplete; "
                      "'update' is not a legal next action for it.", self.errors_text())
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
        self.assertTrue(saved.manifest["workspace"]["differs_from_deployed"])
        self.assertTrue(saved.workspace_payload)
        self.assertTrue((self.c.backups_dir / saved.bundle_id / saved.workspace_payload).is_file())
        self.assertEqual(self.c.workspace_head, OLD)
        self.assertFalse(self.c.workspace_dirty)
        self.assertEqual(self.c.ci_calls, [OLD])

    def test_auto_update_refuses_dirty_workspace(self):
        self.c.config["auto_update"] = True
        self.c.workspace_dirty = True
        with self.assertRaisesRegex(pf.Deferred, "Automatic update refuses a workspace"):
            self.automatic_update()
        self.assertEqual(self.c.revision(), OLD)
        self.assertEqual(self.c.snapshots(), [])

    def test_code_rollback_can_recover_failed_update_without_migrations(self):
        self.c.fail = "health"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        selected = self.c.snapshots()[0].bundle_id
        self.c.fail = None
        self.assertEqual(self.invoke(["rollback", selected]), 0)
        self.assertEqual(open_operations(self.c), [])
        self.assertEqual(latest_operation(self.c, "rollback")[1]["supersedes"], latest_operation(self.c, "update")[0])
        self.assertEqual(self.c.revision(), OLD)

    def test_frontend_failure_never_discards_possible_new_writes(self):
        self.c.fail = "frontend-health"
        self.assertEqual(self.invoke(["update", "--latest"]), 1)
        self.assertEqual(self.c.dbs["partflow_staging"]["rows"], ["old-record", "write-during-opening"])
        self.assertFalse(self.c.running["frontend"])
        self.assertFalse(any(name.startswith("pf_keep_") for name in self.c.dbs))

    def test_interactive_backup_selection_has_ten_item_pages(self):
        items = [pf.InvalidBundle(Path(str(i)), "manifest-missing", "") for i in range(25)]
        with mock.patch.object(self.c, "snapshots", return_value=items), mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=["n", "12"]), \
             mock.patch.object(self.c, "verify_snapshot", side_effect=lambda value: value):
            self.assertEqual(self.c.choose_snapshot(), "11")
        self.assertIn("page 1/3", self.output.getvalue())
        self.assertIn("page 2/3", self.output.getvalue())

    @staticmethod
    def listed(bundle_id, commit):
        """A listing entry whose recorded source commit is ``commit`` (the section 3.6 provenance hypothesis)."""
        return pf.BundleView(Path(bundle_id), {"bundle_id": bundle_id, "legacy": None,
                                               "source": {"commit": commit}}, "0" * 64, None, "captured")

    def test_explicit_backup_id_never_opens_selection_menu(self):
        item = self.listed("specific", OLD)
        with mock.patch.object(self.c, "snapshots", return_value=[item]), \
             mock.patch.object(self.c, "verify_snapshot", return_value=item), \
             mock.patch("builtins.input") as prompt:
            self.assertEqual(self.c.choose_snapshot("specific"), item)
            prompt.assert_not_called()

    def test_ambiguous_full_sha_requires_backup_id(self):
        with mock.patch.object(self.c, "snapshots", return_value=[self.listed("one", OLD), self.listed("two", OLD)]):
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
        # PF-A1.4: the catch-all Compose route is gone; these words are named refusals before any read.
        for args in (["down", "-v"], ["down", "--volumes"], ["rm", "-svf"]):
            with self.subTest(args=args):
                before = len(self.errors_text())
                self.assertEqual(self.invoke(["--instance", "staging", *args]), 1)
                self.assertIn(f"ERROR: compose-route-removed: 'pf {args[0]}' no longer forwards to Docker Compose.",
                              self.errors_text()[before:])
        self.assertEqual(self.c.calls, [])


    def test_compose_runs_receive_managed_job_label(self):
        c = pf.Controller(self.context)
        c.cli = ["docker", "compose"]
        with mock.patch.object(c, "command", return_value="") as run, \
             mock.patch.object(c, "require_envelope") as envelope:
            c.compose("run", "--rm", "--no-deps", "backend", "uv", "run", "alembic", "heads")
        envelope.assert_called_once()  # PF-A1.3: a mutating verb passes the Compose envelope first
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
        pfx.trust_daemon(c)
        with mock.patch.dict(os.environ, {"POSTGRES_DB": "unexpected_database"}):
            self.assertEqual(c.command(["docker", "version"], effect=None), "absent")
            self.assertEqual(c.command(["docker", "version"], effect=None, env={"POSTGRES_DB": "rehearsal"}),
                             "rehearsal")
            with self.assertRaises(pf.Failure):
                c.command([os.sys.executable, "-c", "print(1)"], effect=None)  # not a registered tool id
            with self.assertRaises(pf.Failure):
                c.command(["docker", "version"], effect=None, env={"PATH": "/tmp"})  # reserved host variable


class PureTests(unittest.TestCase):
    def test_every_catchable_termination_signal_interrupts_the_controller_once(self):
        """A12r2-F01: SIGHUP/SIGQUIT must unwind like SIGTERM/SIGINT (not kill without cleanup), and a
        repeated signal must not abort the termination and effect record already under way."""
        names = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
        saved = {name: signal.getsignal(getattr(signal, name)) for name in names}
        try:
            handler = pf.install_interrupt_handlers()
            for name in names:
                self.assertIs(signal.getsignal(getattr(signal, name)), handler, name)
            with self.assertRaisesRegex(KeyboardInterrupt, "Interrupted by signal 1"):
                handler(int(signal.SIGHUP), None)
            self.assertIsNone(handler(int(signal.SIGTERM), None))
        finally:
            for name, previous in saved.items():
                signal.signal(getattr(signal, name), previous)
        source = (PACKAGE / "pf-admin.py").read_text()
        self.assertIn('if __name__ == "__main__":\n    install_interrupt_handlers()\n    sys.exit(main())', source)
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
        # PF-A2.1: the copy names the working installer verb and the launcher that reaches a new root.
        self.assertIn("  sudo sh ./deploy/synology/install-control.sh init --root <root>", result.stderr)
        self.assertIn("  sudo <root>/bootstrap/pf <command>", result.stderr)

    def test_control_installer_is_the_thin_init_only_wrapper(self):
        """PF-A2.1 (OD-A14-07): install-control.sh only initializes a new root through pf_install.py; the v2.5
        copy-and-replace installer (moves of .env/pf-config.json, rm -rf control/, launcher overwrite) is gone."""
        script = (REPO_PACKAGE / "deploy/synology/install-control.sh").read_text()
        code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn('if [ "$1" != init ]; then', code)
        self.assertIn("installer-verb-installed-only: install-control.sh only initializes a new installation root.", code)
        self.assertIn('exec env -i PATH="$PATH" HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 TERM="${TERM:-dumb}" "$PY" -I -B '
                      '"$SCRIPT_DIR/pf_install.py" "$@"', code)
        self.assertIn("Run as root: sudo sh ./deploy/synology/install-control.sh init --root <root>", code)
        self.assertIn("Interactive terminal required; installation has no --yes bypass.", code)
        for removed in ("mv ", "rm -", "cp ", "chmod", "chown", "cat >", "/usr/local/bin/pf", "eval", "permissions"):
            self.assertNotIn(removed, code, removed)

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

    @staticmethod
    def import_archive(path, parent, name):
        """PF-A3.1: the importer that replaced extract_source (pass 1 inspection, then pass 2 extraction)."""
        fd = os.open(str(path), os.O_RDONLY)
        parent_fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            inventory = pf.pf_source.inspect_archive(fd, limits=pf.pf_source.SOURCE_LIMITS)
            return pf.pf_source.extract_archive(fd, parent_fd, name, inventory, limits=pf.pf_source.SOURCE_LIMITS)
        finally:
            os.close(parent_fd)
            os.close(fd)

    def test_tar_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.tar.gz"
            with tarfile.open(path, "w:gz") as tar:
                item = tarfile.TarInfo("../../escape")
                item.size = 1
                tar.addfile(item, io.BytesIO(b"x"))
            with self.assertRaises(pf.pf_source.ArchiveRefused) as caught:
                self.import_archive(path, tmp, "out")
            self.assertEqual((caught.exception.code, caught.exception.reason), ("archive-member-refused", "traversal"))
            self.assertFalse((Path(tmp) / "out").exists())

    def test_tar_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.tar.gz"
            with tarfile.open(path, "w:gz") as tar:
                item = tarfile.TarInfo("secret")
                item.type = tarfile.SYMTYPE
                item.linkname = "/etc/passwd"
                tar.addfile(item)
            with self.assertRaises(pf.pf_source.ArchiveRefused) as caught:
                self.import_archive(path, tmp, "out")
            self.assertEqual((caught.exception.code, caught.exception.reason), ("archive-member-refused", "type"))
            self.assertFalse((Path(tmp) / "out").exists())

    def test_source_archive_excludes_git_and_runtime_env_is_external(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            fixture(root)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("private git config")
            (root / ".env").write_text("STALE_REPO_SECRET=must-not-be-backed-up\n")
            archive = Path(tmp) / "source.tar.gz"
            pf.create_source_archive(root, archive)
            self.import_archive(archive, tmp, "out")
            dest = Path(tmp) / "out"
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
                 mock.patch.object(self.c, "purge") as purge, \
                 mock.patch.object(pf, "unattended", return_value=False):  # an operator at a terminal
                self.assertEqual(pf.main(["--instance", "beta", "purge", "--keep-backups"],
                                         installation_root=self.layout.root,
                                         running_release=self.layout.release_dir, trusted_launch=True), 0)
            self.assertEqual(constructed.call_args.args[0].slug, "beta")
            purge.assert_called_once()

    PURGE_SUMMARY = {
        "project": "partflow-staging", "root": "", "database": "partflow_staging",
        "database_user": "partflow_staging", "revision": OLD,
        "containers": ["c1"], "volumes": ["v1"], "networks": ["n1"], "images": ["i1"],
        "checkpoints": 0, "state_present": True, "env_present": True,
    }

    @contextlib.contextmanager
    def purge_mocks(self, recovery, checkpoint, plan):
        """PF-A3.2: the purge's effect bodies are mocked at their own seams (snapshot, the bundle capture and its
        verification, the deletion and each cleanup); the plan, journal and gate run for real."""
        with mock.patch.object(self.c, "instance_summary", return_value=dict(self.PURGE_SUMMARY, root=str(self.root))), \
                mock.patch.object(self.c, "log_instance_summary"), \
                mock.patch.object(self.c, "database_ready", return_value=16), \
                mock.patch.object(self.c, "ensure_local_contract", return_value={"heads": ["r1"]}), \
                mock.patch.object(self.c, "pause"), \
                mock.patch.object(self.c, "snapshot", return_value=checkpoint), \
                mock.patch.object(self.c, "capture_purge_bundle", return_value=(recovery, plan)) as capture, \
                mock.patch.object(self.c, "verify_purge_bundle", return_value=recovery), \
                mock.patch.object(self.c, "verify_recovery", return_value=recovery) as gate, \
                mock.patch.object(self.c, "execute_deletion_plan", return_value=[]) as delete, \
                mock.patch.object(self.c, "purge_cleanup", return_value="absent") as cleanup, \
                mock.patch.object(self.c, "reopen_unchanged") as reopen:
            yield types.SimpleNamespace(capture=capture, gate=gate, delete=delete, cleanup=cleanup, reopen=reopen)

    def test_purge_requires_recovery_then_multiple_confirmations_before_cleanup(self):
        recovery, checkpoint = self.purge_views()
        confirmations = []

        def record_confirm(phrase, warning):
            confirmations.append(phrase)

        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        with contextlib.redirect_stdout(io.StringIO()), self.c.lock():
            plan = self.binding_plan(recovery.bundle_id)
            with self.purge_mocks(recovery, checkpoint, plan) as mocks, \
                    mock.patch.object(pf, "confirm", side_effect=record_confirm), \
                    mock.patch.object(pf, "prompt_yes_no", return_value=False):
                self.c.purge()
            journal = self.c.journal

        self.assertEqual(confirmations[0], "PURGE partflow-staging")
        self.assertEqual(confirmations[1], "DELETE partflow_staging")
        self.assertTrue(confirmations[2].startswith("ERASE partflow-staging "))
        # The preliminary plan is handed to the bundle, and the binding plan is what deletion executes.
        self.assertEqual(mocks.capture.call_args.args[0]["image_coverage"], "pending")
        self.assertEqual(journal["deletion"]["plan_sha256"], pf.pf_docker.plan_sha256(plan))
        self.assertEqual((journal["deletion"]["delete_backups"], journal["deletion"]["reset_admin_config"]),
                         (False, False))
        mocks.delete.assert_called_once_with(plan)
        self.assertEqual([call.args[0] for call in mocks.cleanup.call_args_list],
                         ["backups", "env", "state", "admin-config"])
        self.assertEqual(journal["phase"], "completed")
        # PF-A3.1 deletion gate: the bundle is re-read strictly right before the deletion approval is journaled.
        mocks.gate.assert_called_once_with(recovery.folder)

    def purge_views(self, level="data_restore_verified"):
        """Stand-ins for the purge bundle and its before-purge checkpoint (the BundleView accessors purge reads)."""
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        recovery = types.SimpleNamespace(
            bundle_id=recovery_id, folder=self.c.recovery_root / recovery_id, manifest_sha256="b" * 64,
            derived_from="20260910T115900Z-" + OLD[:12] + "-aaaaaa", database="partflow_staging",
            stores=[{"database": "partflow_staging"}], level=level,
            purge={"saved_image_refs": [PROJECT + "-backend:test", PROJECT + "-frontend:test"]})
        checkpoint = types.SimpleNamespace(bundle_id=recovery.derived_from, manifest_sha256="a" * 64,
                                           database_heads=["r1"], images={
                                               "backend": {"reference": PROJECT + "-backend:old", "id": OLD_BACKEND},
                                               "frontend": {"reference": PROJECT + "-frontend:old",
                                                            "id": OLD_FRONTEND}})
        return recovery, checkpoint

    def test_purge_bundle_without_a_passed_record_is_refused_before_deleting_and_reopens(self):
        """PB-7: no passed data_restore_verified record of the bundle's own manifest -> purge-bundle-unverified,
        no deletion approval, and the application is reopened in-process; the purge closes cancelled (PU-5)."""
        for level in ("captured", "failed"):
            with self.subTest(level=level):
                recovery, checkpoint = self.purge_views(level=level)
                with contextlib.redirect_stdout(io.StringIO()) as out, self.c.lock():
                    plan = self.binding_plan(recovery.bundle_id)
                    with self.purge_mocks(recovery, checkpoint, plan) as mocks, \
                            mock.patch.object(self.c, "write_deletion_plan") as write_plan, \
                            mock.patch.object(pf, "confirm"):
                        with self.assertRaisesRegex(pf.Failure, "^purge-bundle-unverified: " + recovery.bundle_id
                                                    + ": no passed data_restore_verified record for this bundle's "
                                                    "manifest; deletion is blocked. The purge stops before deletion; "
                                                    "the application is reopened."):
                            self.c.purge(delete_backups=False)
                    journal = self.c.journal
                write_plan.assert_not_called()
                mocks.delete.assert_not_called()
                mocks.cleanup.assert_not_called()
                mocks.reopen.assert_called_once_with()
                self.assertEqual((journal["phase"], journal["deletion"]), ("cancelled", None))
                self.assertIn("Purge cancelled/failed before deletion; application services were restored.",
                              out.getvalue())

    def binding_plan(self, recovery_id, operation_id=None):
        plan = pf.pf_docker.plan_deletion(self.c.docker_inventory(), kind="purge",
                                          operation_id=operation_id or self.c.operation_id,
                                          daemon=self.c.verify_daemon(), recovery_id=recovery_id,
                                          covered_image_refs=set())
        plan["slug"] = self.c.context.slug
        return plan

    def test_purge_delete_backups_adds_separate_confirmation(self):
        recovery, checkpoint = self.purge_views()
        confirmations = []
        with contextlib.redirect_stdout(io.StringIO()), self.c.lock():
            plan = self.binding_plan(recovery.bundle_id)
            with self.purge_mocks(recovery, checkpoint, plan), \
                    mock.patch.object(pf, "confirm", side_effect=lambda phrase, warning: confirmations.append(phrase)):
                self.c.purge(delete_backups=True)
            journal = self.c.journal
        self.assertIn("DELETE BACKUPS partflow-staging", confirmations)
        self.assertTrue(journal["deletion"]["delete_backups"])

    def deleting_purge(self, *, delete_backups=True):
        """PF-A3.2: an interrupted purge in deleting (the frozen binding plan and the deletion approval)."""
        plan = pfx.lifecycle_plan(self.context, "purge")
        directory = pfx.write_operation(self.context, plan)
        self.c.resources = {"containers": ["db"], "volumes": ["partflow-staging_postgres_data"]}
        binding = self.binding_plan(pfx.BUNDLE_ID, operation_id=plan["operation_id"])
        data = pf.pf_docker.plan_bytes(binding)
        pf_instance._write_private_file(directory / "deletion-plan.json", data, 0o600)
        journal = pfx.lifecycle_journal(
            plan, phase="deleting", unresolved="e0005",
            states={"e0001": "complete", "e0002": "complete", "e0003": "complete", "e0004": "complete",
                    "e0005": "unknown"},
            deletion={"plan_sha256": pf_instance.sha256_bytes(data), "delete_backups": delete_backups,
                      "reset_admin_config": False, "confirmed_at": "20261007T040500Z"})
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(journal))
        return plan, binding

    def run_cli(self, arguments, *, confirm=None):
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors), \
                mock.patch.object(pf, "Controller", return_value=self.c), \
                mock.patch.object(pf, "confirm", confirm or mock.Mock()), \
                mock.patch.object(pf, "unattended", return_value=False):
            code = pf.main(arguments, installation_root=self.layout.root, running_release=self.layout.release_dir,
                           trusted_launch=True)
        return code, errors.getvalue()

    def test_interrupted_deleting_purge_can_resume_from_verified_bundle(self):
        plan, binding = self.deleting_purge()
        item = pf.InvalidBundle(self.c.recovery_root / pfx.BUNDLE_ID, "", "")
        confirmation = mock.Mock()
        with mock.patch.object(self.c, "verify_recovery", return_value=item) as reread, \
                mock.patch.object(self.c, "execute_deletion_plan", return_value=[]) as delete, \
                mock.patch.object(self.c, "purge_cleanup", return_value="absent") as cleanup:
            code, errors = self.run_cli(["purge"], confirm=confirmation)
        self.assertEqual(code, 0, errors)
        # `pf purge` aliases `pf resume` (section 3.3): one RESUME PURGE confirmation, the frozen plan only.
        confirmation.assert_called_once()
        self.assertEqual(confirmation.call_args.args[0], f"RESUME PURGE partflow-staging {pfx.BUNDLE_ID}")
        # PF-A3.1 PB-8: the bundle is re-read strictly (no new verification).
        reread.assert_called_once_with(item.folder)
        delete.assert_called_once_with(binding)
        self.assertEqual([(call.args[0], call.kwargs["delete_backups"]) for call in cleanup.call_args_list],
                         [("backups", True), ("env", True), ("state", True), ("admin-config", True)])
        _, journal = pfx.operation(self.context, plan["operation_id"])
        self.assertEqual(journal["phase"], "completed")
        attempts = json.loads((self.context.operations_dir / plan["operation_id"] / "attempts.json").read_bytes())
        self.assertEqual([item["action"] for item in attempts], ["alias:purge"])

    def test_legacy_lifecycle_pending_journal_is_refused_as_unsupported(self):
        """PF-A3.2 (former plan-missing case): a lifecycle state/pending.json can only come from an unsupported control
        change; every mutating route refuses journal-format-unsupported and nothing is read further."""
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        pf.write_json(self.c.pending, {
            "operation": "purge", "phase": "deleting", "recovery": recovery_id,
            "delete_backups": True, "reset_admin_config": False,
        })
        with mock.patch.object(self.c, "purge_cleanup") as cleanup:
            code, errors = self.run_cli(["purge"], confirm=mock.Mock(side_effect=AssertionError("no confirmation")))
        self.assertEqual(code, 1)
        self.assertIn("journal-format-unsupported: " + str(self.context.paths.private_state)
                      + "/state/pending.json records purge/deleting", errors)
        cleanup.assert_not_called()

    def test_side_by_side_restore_never_replaces_active_database(self):
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        folder = self.c.recovery_root / "side-by-side-fixture"
        folder.mkdir(parents=True)
        dump = folder / "databases/active.dump"
        dump.parent.mkdir()
        dump.write_bytes(b"dump")
        recovery = types.SimpleNamespace(bundle_id=recovery_id, folder=folder, compose_project="partflow-staging",
                                         postgres_major=16, database="partflow_staging",
                                         active_store={"dump": "databases/active.dump"})
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
        recovery = types.SimpleNamespace(bundle_id=recovery_id, folder=Path(self.temp.name),
                                         compose_project="partflow-staging", workspace_root=str(self.root),
                                         postgres_major=16, database="partflow_staging")
        self.c.resources = {"containers": ["c"], "volumes": []}
        (self.c.state / "deployed.json").unlink()
        with mock.patch.object(self.c, "verify_recovery", return_value=recovery), \
             mock.patch.object(pf, "confirm", side_effect=AssertionError("no confirmation before the empty-target test")):
            with self.assertRaisesRegex(pf.Failure, "resource-target-not-empty: restore-instance requires an empty target"):
                self.c.restore_instance(recovery)

    def exact_restore_bundle(self, tree_extra=None):
        """PF-A3.1: a complete legacy format 2 purge bundle of this instance, strictly read (BundleView)."""
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        tree = Path(self.temp.name) / "tree"
        source_fixture(tree, OLD)
        if tree_extra:
            tree_extra(tree)
        folder = pfx.legacy_purge_bundle(self.c.recovery_root / recovery_id, project=PROJECT, root=self.root,
                                         tree=tree)
        (self.c.state / "deployed.json").unlink()
        return self.c.verify_recovery(folder)

    def assert_exact_restore_refused_before_any_effect(self, recovery, pattern):
        env_path = self.c.config_dir / ".env"
        env_before = env_path.read_bytes() if env_path.exists() else None
        with mock.patch.object(self.c, "verify_recovery", return_value=recovery), \
             mock.patch.object(pf, "confirm", side_effect=AssertionError("confirmation must not be asked")), \
             mock.patch.object(self.c, "restore_runtime_environment",
                               side_effect=AssertionError("configuration must not be touched")):
            with self.assertRaisesRegex(pf.Failure, pattern):
                self.c.restore_instance(recovery)
        # No restore-instance plan or journal is left behind to wedge the instance.
        self.assertEqual(pfx.operations_of(self.context), [])
        self.assertEqual(env_path.read_bytes() if env_path.exists() else None, env_before)

    def test_exact_restore_refuses_reserved_bundle_paths_before_confirmation_or_journal(self):
        """A12r2-F02: a bundle source carrying a path the manifest cannot verify is refused before
        either RESTORE confirmation, the pending journal or any configuration change."""
        def reserved(tree):
            (tree / "nested" / "node_modules").mkdir(parents=True)
            (tree / "nested" / "node_modules" / "x.js").write_text("1")

        self.assert_exact_restore_refused_before_any_effect(
            self.exact_restore_bundle(reserved), "cannot verify.*nested/node_modules/x.js")

    def test_exact_restore_proves_source_provenance_before_confirmation_or_journal(self):
        """A12r2-F02: a store-proof refusal also comes before confirmation, journal and configuration."""
        recovery = self.exact_restore_bundle()
        refusal = pf.Failure("Source provenance check failed: unsupported source path (tracked reserved "
                             "workspace artifact name): node_modules/x.js")
        with mock.patch.object(self.c, "prove_tree_commit", side_effect=refusal) as proof:
            self.assert_exact_restore_refused_before_any_effect(recovery, "tracked reserved workspace artifact name")
        proof.assert_called_once()

    def test_verify_recovery_detects_modified_payload(self):
        # PF-A3.1 (D19): a complete format 1 purge bundle (active dump/list, globals, history, images, state and the
        # runtime .env inside the source archive) is accepted; after tampering the coded refusal follows.
        recovery_id = "purge-20260910T120000Z-" + OLD[:12] + "-abcdef"
        tree = Path(self.temp.name) / "tree"
        source_fixture(tree, OLD)
        folder = pfx.legacy_purge_bundle(self.c.recovery_root / recovery_id, project=PROJECT, root=self.root,
                                         tree=tree, fmt=1)
        item = {"_folder": str(folder), "id": recovery_id}
        view = self.c.verify_recovery(item)
        self.assertEqual(view.bundle_id, recovery_id)
        self.assertEqual(view.manifest["legacy"]["format"], 1)
        self.assertEqual(view.capture_class, "healthy_checkpoint")
        (folder / "source.tar.gz").write_bytes(b"tampered")
        # A legacy manifest records no sizes: the whole-file hash refuses.
        with self.assertRaisesRegex(pf.Failure, "^bundle-payload-mismatch: " + recovery_id + ": source.tar.gz: hash. "
                                    "Nothing was changed."):
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
        # PF-A3.1: the real before-purge checkpoint and schema 1 purge bundle through the fake database plane;
        # only the image save is simulated (the fake controller has no image layers).
        def fake_command(argv, **kwargs):
            if argv[:4] == ["docker", "image", "save", "-o"]:
                target = Path(argv[4])
                dummy = Path(self.temp.name) / "image-manifest.json"
                dummy.write_text(json.dumps(argv[5:]) + "\n")
                with tarfile.open(target, "w") as archive:
                    archive.add(dummy, arcname="manifest.json")
                return ""
            raise AssertionError(argv)

        self.c.workspace_dirty = True
        (self.root / "frontend/app.txt").write_text("local edits\n")
        self.c.running.update(backend=False, frontend=False)  # purge pauses writers before the bundle
        self.c.dbs["pf_keep_20260901t000000z_abcdef"] = {"heads": ["r1"], "rows": ["kept"], "connections": False}
        with contextlib.redirect_stdout(io.StringIO()), \
             mock.patch.object(self.c, "available_snapshot_image_refs", return_value=([], [])), \
             mock.patch.object(self.c, "command", side_effect=fake_command) as command, \
             self.c.lock():
            self.c.resources = {"containers": ["c"], "volumes": ["partflow-staging_postgres_data"],
                                "networks": ["partflow-staging_default"]}
            preliminary = self.c.plan_for("purge", self.c.docker_inventory(), command="purge")
            recovery, binding = self.c.create_purge_recovery(preliminary)
            frozen_values = dict(self.c.frozen.values)

        verified = self.c.verify_recovery(recovery.folder)
        self.assertEqual(verified.manifest_sha256, recovery.manifest_sha256)
        self.assertEqual(verified.level, "data_restore_verified")
        self.assertEqual(verified.database, "partflow_staging")
        self.assertEqual(verified.manifest["source"]["commit"], OLD)
        folder = verified.folder
        self.assertTrue((folder / "images.tar").is_file())
        self.assertTrue((folder / "revision-checkpoints.tar.gz").is_file())
        self.assertTrue((folder / "workspace.tar.gz").is_file())
        self.assertTrue((folder / "configuration/.env").is_file())
        # The bundle carries the frozen configuration (literal values), strictly parseable.
        self.assertEqual(pf.read_app_env(folder / "configuration/.env"), frozen_values)
        self.assertTrue((folder / "configuration/pf-config.json").is_file())
        self.assertEqual(verified.workspace_payload, "workspace.tar.gz")
        # PF-A1.3: resources_before_purge is sealed from the binding plan's candidates.
        self.assertEqual(binding["image_coverage"], "bound")
        self.assertEqual(verified.purge["resources_before_purge"], {
            kind + "s": [item["key"] for item in binding["candidates"] if item["kind"] == kind]
            for kind in ("container", "volume", "network", "image")})
        self.assertEqual(verified.purge["resources_before_purge"]["volumes"], ["partflow-staging_postgres_data"])
        # PB-1: the retained store with its real heads (inside the connection window), the db image saved by ID,
        # the sensitive payloads, one writers-stopped group, and the window closed again.
        stores = {store["database"]: store for store in verified.stores}
        kept = stores["pf_keep_20260901t000000z_abcdef"]
        self.assertEqual((kept["role"], kept["allow_connections"], kept["alembic_heads"]), ("retained", False, ["r1"]))
        self.assertFalse(self.c.dbs["pf_keep_20260901t000000z_abcdef"]["connections"])
        self.assertEqual(command.call_args.args[0][-1], DB_IMAGE_ID)
        self.assertTrue(verified.manifest["images"]["db"]["archived"])
        sensitive = {item["path"] for item in verified.manifest["payloads"] if item["sensitive"]}
        self.assertTrue({"configuration/.env", "configuration/pf-config.json", "postgres-globals.sql",
                         "state/deployed.json"} <= sensitive)
        self.assertEqual(verified.manifest["consistency_groups"], [
            {"group_id": "purge", "stores": sorted(stores_id for stores_id in
                                                   (store["store_id"] for store in verified.stores)),
             "claim": "writers-stopped"}])

class RecoverySourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.context = fixture(self.root)
        self.layout = fixture.layout
        self.c = FakeController(self.context)

    def tearDown(self):
        self.temp.cleanup()

    def test_restore_runtime_environment_writes_external_config_env(self):
        # Audit AF-1: the caller passes the verified payload bytes (runtime_environment_bytes).
        self.c.restore_runtime_environment(
            b"POSTGRES_DB=partflow_staging\nPOSTGRES_USER=partflow_staging\n"
            b"POSTGRES_PASSWORD=old-secret\nSITE_TIMEZONE=America/Los_Angeles\n"
            b"PARTFLOW_BIND_IP=127.0.0.1\nPARTFLOW_HTTP_PORT=5173\nPARTFLOW_ALLOWED_HOST=localhost\n"
        )
        restored = self.c.config_dir / ".env"
        self.assertIn("POSTGRES_PASSWORD=old-secret", restored.read_text())
        self.assertEqual(stat.S_IMODE(restored.stat().st_mode), 0o660)
