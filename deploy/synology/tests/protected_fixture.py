"""Disposable protected-installation fixtures for the PF-A1.1 offline tests.

Everything is created under a temporary directory owned by the test process.
The fixture plays the role of the trusted installer: it initializes an
installation root, installs the real control files as a release, and registers
instances through the explicit registration transaction. Nothing here touches
Docker, the network or any existing installation.
"""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import socket
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
REPO_PACKAGE = PACKAGE.parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pf = load_module("pf_admin", PACKAGE / "pf-admin.py")
# One module object each: the controller's own explicit sibling imports.
pf_instance = pf.pf_instance
pf_bootstrap = pf_instance.pf_bootstrap

OLD = "1" * 40
NEW = "2" * 40
RELEASE_ID = "pf-a1.1-fixture"
PROFILE_NAME = "partflow-staging-legacy.json"
POLICY_NAME = "staging.json"
ENGINE_ID = "FIXTURE-ENGINE-0001"
ENV_TEXT = (
    "POSTGRES_DB=partflow_staging\nPOSTGRES_USER=partflow_staging\nPOSTGRES_PASSWORD=abc123\n"
    "SITE_TIMEZONE=America/Los_Angeles\nPARTFLOW_BIND_IP=127.0.0.1\n"
    "PARTFLOW_HTTP_PORT=5173\nPARTFLOW_ALLOWED_HOST=localhost\n"
)


def release_files():
    """The real control files from the repository, installed as an immutable release."""
    return {
        "pf-admin.py": (PACKAGE / "pf-admin.py").read_bytes(),
        "pf_instance.py": (PACKAGE / "pf_instance.py").read_bytes(),
        "pf_bootstrap.py": (PACKAGE / "pf_bootstrap.py").read_bytes(),
        "pf_runner.py": (PACKAGE / "pf_runner.py").read_bytes(),
        "pf_config.py": (PACKAGE / "pf_config.py").read_bytes(),
        "pf_source.py": (PACKAGE / "pf_source.py").read_bytes(),
        "pf_docker.py": (PACKAGE / "pf_docker.py").read_bytes(),
        "pf_install.py": (PACKAGE / "pf_install.py").read_bytes(),
        "compose.nas.yaml": (REPO_PACKAGE / "compose.nas.yaml").read_bytes(),
        "pf-config.example.json": (PACKAGE / "pf-config.example.json").read_bytes(),
        "nas.env.example": (PACKAGE / "nas.env.example").read_bytes(),
    }


def profile_document(version="2.5.0-a1.1"):
    return pf_instance.normalize_json({
        "schema_version": 1, "id": pf_instance.PROFILE_ID, "version": version,
        "application": pf_instance.PROFILE_APPLICATION, "compose_file": pf_instance.PROFILE_COMPOSE_FILE,
    })


def policy_document(environment="staging", revision=1):
    return pf_instance.normalize_json({"schema_version": 1, "revision": revision, "environment": environment})


class Layout:
    def __init__(self, values):
        self.root = values["root"]
        self.release_dir = values["release_dir"]
        self.profile_path = values["profile_path"]
        self.policy_paths = values["policy_paths"]
        self.policy_path = values["policy_paths"][POLICY_NAME]
        self.launcher = self.root / pf_instance.BOOTSTRAP_DIR / pf_instance.LAUNCHER_NAME
        self.bootstrap_conf = self.root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_CONF_NAME
        self.bootstrap_module = self.root / pf_instance.BOOTSTRAP_DIR / pf_instance.BOOTSTRAP_MODULE_NAME
        self.tools_conf = self.root / pf_instance.BOOTSTRAP_DIR / pf_bootstrap.TOOLS_CONF_NAME
        self.sources = self.root / pf_instance.SOURCES_RELATIVE
        # PF-A1.3: the registration endpoint (through the <base>/var/run link) and the socket it resolves to.
        self.daemon_endpoint = values.get("daemon_endpoint")
        self.daemon_socket = values.get("daemon_socket")


# Host executables the fixture installer registers (PF-A1.2 runner): fixed system
# locations only, never a PATH search. A missing tool is simply left unregistered.
TOOL_CANDIDATES = {
    "docker": ("/usr/bin/docker", "/usr/local/bin/docker"),
    "git": ("/usr/bin/git", "/usr/local/bin/git"),
    "ip": ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip"),
    "hostname": ("/usr/bin/hostname", "/bin/hostname"),
}


def default_tools():
    tools = {}
    for tool, candidates in TOOL_CANDIDATES.items():
        for candidate in candidates:
            if os.path.isfile(candidate):
                tools[tool] = candidate
                break
    return tools


def install_daemon_socket(base):
    """A root-owned fixture socket <base>/run/docker.sock (0660 in a 0755 parent) and <base>/var/run -> ../run.

    Returns (registration endpoint through the link, resolved socket path). Nothing listens on it.
    """
    base = Path(base)
    run = base / "run"
    run.mkdir(exist_ok=True)
    os.chmod(run, 0o755)
    path = run / "docker.sock"
    if not os.path.lexists(path):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
        finally:
            listener.close()
    os.chmod(path, 0o660)
    var = base / "var"
    var.mkdir(exist_ok=True)
    os.chmod(var, 0o755)
    if not os.path.lexists(var / "run"):
        os.symlink("../run", var / "run")
    return "unix://" + str(var / "run" / "docker.sock"), path


WRAPPER_NAMES = ("backup.sh", "release-check.sh")


def wrapper_files():
    """The repository scheduler wrappers, placed in bootstrap/ by the installer (PF-A2.1, OD-A14-05)."""
    return {name: (PACKAGE / name).read_bytes() for name in WRAPPER_NAMES}


def content_release_id():
    """The content-addressed release id (PF-A2.1) of the repository control files."""
    files = release_files()
    return pf.pf_install.release_id_for(pf.pf_install.content_digest(
        {name: pf_instance.sha256_bytes(data) for name, data in files.items()}))


def install_root(base, *, launcher=None, interpreter=None, tools=None, wrappers=True, release_id=RELEASE_ID,
                 files=None):
    """Initialize <base>/install as a trusted installation root (plus the fixture daemon socket).

    ``wrappers``: place the repository scheduler wrappers in bootstrap/ as the PF-A2.1 installer does.
    ``release_id``: the fixture id by default; content_release_id() gives the installer's own id.
    ``files``: the release files (default: the repository's; fixture_release_files() for the PF-A3.2 CL harness)."""
    endpoint, socket_path = install_daemon_socket(base)
    values = pf_instance.initialize_installation_root(
        Path(base) / "install",
        launcher=launcher if launcher is not None else (REPO_PACKAGE / "pf.sh").read_bytes(),
        interpreter=interpreter or os.path.realpath(sys.executable),
        release_id=release_id,
        release_files=files if files is not None else release_files(),
        wrappers=wrapper_files() if wrappers else None,
        profile=(PROFILE_NAME, profile_document()),
        policy_documents={
            POLICY_NAME: policy_document(),
            "production.json": policy_document(environment="production"),
        },
        tools=default_tools() if tools is None else tools,
    )
    values = dict(values, daemon_endpoint=endpoint, daemon_socket=socket_path)
    return Layout(values)


FIXTURE_MAIN = '''if __name__ == "__main__":
    install_interrupt_handlers()
    sys.exit(main())
'''


def fixture_release_files(settings_path):
    """PF-A3.2 installed-CLI harness only: the repository release whose pf-admin.py entry block first installs a
    fixture-local release source, so update/rollback/backup run through the installed launcher offline. It answers
    the GitHub API from ``settings_path`` ({"github": {path: answer}, "remote": <local approved remote>,
    "timeout_data": seconds or null}) and uses that local remote with the file protocol (the test seams
    ``remote_override``/``source_protocols`` the in-process tests set); every other byte of every file, and so every
    production check, is the repository's. The test build is installed only into a disposable fixture root."""
    files = release_files()
    source = files["pf-admin.py"].decode("utf-8")
    if source.count(FIXTURE_MAIN) != 1 or not source.endswith(FIXTURE_MAIN):
        raise AssertionError("pf-admin.py entry block changed; update fixture_release_files")
    shim = (
        'if __name__ == "__main__":\n'
        "    # PF-A3.2 fixture-local release source (tests/protected_fixture.py); never part of a real release.\n"
        f"    _FIXTURE_SETTINGS = {str(settings_path)!r}\n"
        "\n"
        "    def _fixture_settings():\n"
        "        return json.loads(Path(_FIXTURE_SETTINGS).read_text(encoding='utf-8'))\n"
        "\n"
        "    def _fixture_github(self, path, missing=False):\n"
        "        answers = _fixture_settings()['github']\n"
        "        if path in answers:\n"
        "            return answers[path]\n"
        "        if missing:\n"
        "            return None\n"
        "        raise Failure('fixture release source: no answer for ' + path)\n"
        "\n"
        "    _fixture_init = Controller.__init__\n"
        "\n"
        "    def _fixture_controller(self, *args, **kwargs):\n"
        "        _fixture_init(self, *args, **kwargs)\n"
        "        self.remote_override = _fixture_settings()['remote']\n"
        "        self.source_protocols = ('file',)\n"
        "\n"
        "    Controller.github = _fixture_github\n"
        "    Controller.__init__ = _fixture_controller\n"
        "    if _fixture_settings().get('timeout_data'):\n"
        "        TIMEOUT_DATA = float(_fixture_settings()['timeout_data'])\n"
        "    install_interrupt_handlers()\n"
        "    sys.exit(main())\n")
    files["pf-admin.py"] = (source[:-len(FIXTURE_MAIN)] + shim).encode("utf-8")
    return files


def tool_script(directory, name, body):
    """A root-owned, non-writable executable script in ``directory`` (a protected fixture tool)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o755)
    path = directory / name
    path.write_text(body)
    os.chmod(path, 0o755)
    return path


def source_fixture(root, revision=OLD, new_migration=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "backend/alembic/versions").mkdir(parents=True)
    (root / "backend/alembic.ini").write_text("[alembic]\nscript_location=alembic\n")
    (root / "backend/alembic/env.py").write_text("# fixture environment\n")
    (root / "backend/alembic/versions/001.py").write_text("revision='r1'\n")
    if new_migration:
        (root / "backend/alembic/versions/002.py").write_text("revision='r2'\ndown_revision='r1'\n")
    (root / "frontend").mkdir()
    (root / "frontend/app.txt").write_text(revision)
    (root / "app-version.txt").write_text(revision)
    (root / "compose.nas.yaml").write_text((REPO_PACKAGE / "compose.nas.yaml").read_text())
    (root / "pf.sh").write_text("# repository source copy\n")
    admin = root / "deploy/synology"
    admin.mkdir(parents=True, exist_ok=True)
    (admin / "pf-admin.py").write_text("# repository source helper\n")


def data_home(home, *, project="partflow-staging", group, revision=OLD, source=True, env=True, environment="staging"):
    """Create the editable workspace/config and protected backups/recovery directories under ``home``."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    repo = home / "repo"
    if source:
        source_fixture(repo, revision)
    else:
        repo.mkdir()
    config = home / "config"
    config.mkdir()
    (config / "pf-config.json").write_text(json.dumps({
        "project": project, "environment": environment,
        "backup_read_group": group, "workspace_write_group": group,
    }) + "\n")
    if env:
        (config / ".env").write_text(ENV_TEXT)
    for name in ("backups", "recovery"):
        (home / name).mkdir()
        os.chmod(home / name, 0o750)
    return {"workspace": repo, "configuration": config, "backups": home / "backups", "recovery": home / "recovery"}


def registration_spec(layout, slug, paths, *, project=None, engine_id=ENGINE_ID, environment="staging", instance_id=None):
    spec = {
        "slug": slug,
        "compose_project": project or slug,
        "approved_environment": environment,
        "daemon": {"endpoint": layout.daemon_endpoint, "engine_id": engine_id},
        "paths": paths,
        "control_release_id": Path(layout.release_dir).name,
        "profile_path": layout.profile_path,
        "policy_path": layout.policy_paths.get(environment + ".json", layout.policy_path),
    }
    if instance_id:
        spec["instance_id"] = instance_id
    return spec


def register(layout, slug, paths, **kwargs):
    return pf_instance.register_instance(layout.root, registration_spec(layout, slug, paths, **kwargs))


def deployed_record(context, revision=OLD):
    pf.write_json(context.state_dir / "deployed.json", {"sha": revision})


def image_id(name):
    """A realistic image ID (sha256:<64 hex>) for a fixture image name (PF-A3.1 records require the form)."""
    import hashlib
    return "sha256:" + hashlib.sha256(name.encode("utf-8")).hexdigest()


def tar_gz_bytes(tree, *, arcnames=None):
    """The bytes of a legacy-style ``tar.gz`` of ``tree`` (``archive.add`` per top-level item, as v2.5 wrote it)."""
    import io
    import tarfile
    tree = Path(tree)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for item in sorted(tree.iterdir()):
            archive.add(str(item), arcname=(arcnames or {}).get(item.name, item.name))
    return buffer.getvalue()


def write_legacy_bundle(folder, manifest, files):
    """A legacy (format 1/2) bundle folder: ``files`` ({relative path: bytes}), their checksums, manifest.json
    (write_json: indented, trailing newline) and manifest.sha256 of the exact file bytes. Returns the folder."""
    import hashlib
    folder = Path(folder)
    folder.mkdir(parents=True)
    for relative, data in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = dict(manifest, checksums={relative: hashlib.sha256(data).hexdigest()
                                         for relative, data in files.items()})
    pf.write_json(folder / "manifest.json", manifest)
    (folder / "manifest.sha256").write_text(pf.digest(folder / "manifest.json") + "\n")
    return folder


def legacy_purge_bundle(folder, *, project, root, tree, fmt=2, revision=OLD, extra=None, files=None, env=ENV_TEXT):
    """A complete legacy purge bundle (format 2, or format 1 with the runtime .env inside the source archive)
    for the instance of ``project``/``root``: source, active dump/list, globals, history, images, state and .env."""
    import tempfile
    recovery_id = Path(folder).name
    backend, frontend = f"{project}-backend:backup-legacy", f"{project}-frontend:backup-legacy"
    with tempfile.TemporaryDirectory() as temp:
        source_tree = Path(temp) / "tree"
        if fmt == 1:
            # v1 stored the runtime .env inside source.tar.gz.
            source_tree.mkdir()
            for item in Path(tree).iterdir():
                if item.is_dir():
                    import shutil
                    shutil.copytree(str(item), str(source_tree / item.name))
                else:
                    (source_tree / item.name).write_bytes(item.read_bytes())
            (source_tree / ".env").write_text(env)
        else:
            source_tree = Path(tree)
        history = Path(temp) / "history" / project / "20260909T120000Z-aaaaaaaaaaaa-abcdef"
        history.mkdir(parents=True)
        (history / "manifest.json").write_text("{}\n")
        payloads = {
            "source.tar.gz": tar_gz_bytes(source_tree),
            "databases/active.dump": json.dumps({"heads": ["r1"], "rows": ["old-record"], "connections": True}).encode(),
            "databases/active.list": b"mock archive list\n",
            "postgres-globals.sql": b"-- globals\n",
            "revision-checkpoints.tar.gz": tar_gz_bytes(Path(temp) / "history"),
            "images.tar": b"fixture image archive\n",
            "state/deployed.json": b'{"sha": "' + revision.encode() + b'"}\n',
        }
    if fmt == 2:
        payloads["configuration/.env"] = env.encode()
    payloads.update(files or {})
    manifest = {
        "format": fmt, "kind": "partflow-purge-recovery", "status": "complete", "id": recovery_id,
        "created_at": "20261006T000000Z", "project": project, "environment": "staging",
        "repository": "CDSemi/part-flow", "root": str(root), "source_revision": revision, "source_verified": True,
        "source_provenance": "git_commit", "active_checkpoint": "20261005T235900Z-" + revision[:12] + "-aaaaaa",
        "database": "partflow_staging", "database_user": "partflow_staging", "database_heads": ["r1"],
        "postgres_major": 16,
        "databases": [{"name": "partflow_staging", "allow_connections": True, "dump": "databases/active.dump",
                       "heads": ["r1"]}],
        "active_images": {"backend": {"reference": backend, "id": image_id("old-backend")},
                          "frontend": {"reference": frontend, "id": image_id("old-frontend")}},
        "saved_image_refs": [backend, frontend], "missing_historical_image_refs": [],
        "state_files": ["deployed.json"], "workspace_archive": None,
        "resources_before_purge": {"containers": [], "volumes": [], "networks": [], "images": []},
        "restore_scope": "fixture",
    }
    manifest.update(extra or {})
    return write_legacy_bundle(folder, manifest, payloads)


def legacy_checkpoint(folder, *, project, tree, fmt=2, revision=OLD, extra=None, files=None):
    """A complete legacy checkpoint (format 2: the v2.5 snapshot shape; format 1: no migration fingerprint)."""
    backup_id = Path(folder).name
    tree = Path(tree)
    payloads = {
        "source.tar.gz": tar_gz_bytes(tree),
        "database.dump": json.dumps({"heads": ["r1"], "rows": ["old-record"], "connections": True}).encode(),
        "database.list": b"mock archive list\n",
    }
    payloads.update(files or {})
    manifest = {
        "format": fmt, "id": backup_id, "created_at": "20261006T000000Z", "reason": "scheduled-or-manual-backup",
        "status": "complete", "source_revision": revision, "source_provenance": "git_commit",
        "source_verified": True, "project": project, "repository": "CDSemi/part-flow", "environment": "staging",
        "database": "partflow_staging", "database_user": "partflow_staging", "postgres_major": 16,
        "database_heads": ["r1"],
        "images": {"backend": {"reference": f"{project}-backend:backup-{backup_id.lower()}",
                               "id": image_id("old-backend")},
                   "frontend": {"reference": f"{project}-frontend:backup-{backup_id.lower()}",
                                "id": image_id("old-frontend")}},
        "workspace_head": revision, "workspace_dirty": False, "workspace_differs_from_deployed": False,
        "restore_test": "passed",
    }
    if fmt == 2:
        manifest["migration_files"] = pf.migration_files(tree)
    manifest.update(extra or {})
    return write_legacy_bundle(folder, manifest, payloads)


def snapshot_tree(*roots):
    """Bytes/mode/owner/inode inventory used to prove read-only behaviour (A1-T03)."""
    result = {}
    for root in roots:
        root = Path(root)
        for current, dirs, files in os.walk(root, followlinks=False):
            for name in dirs + files:
                path = Path(current) / name
                info = os.lstat(path)
                digest = None
                if os.path.isfile(path) and not os.path.islink(path):
                    digest = pf_instance.sha256_bytes(path.read_bytes())
                result[str(path)] = (info.st_mode, info.st_uid, info.st_gid, info.st_ino, info.st_nlink, digest)
        info = os.lstat(root)
        result[str(root)] = (info.st_mode, info.st_uid, info.st_gid, info.st_ino, info.st_nlink, None)
    return result


# ------------------------------------------------------------------ PF-A1.3 daemon fixtures


def daemon_info(engine_id=ENGINE_ID, *, rootless=False, security_options=None):
    options = ["name=seccomp,profile=builtin"] if security_options is None else security_options
    if rootless:
        options = list(options or []) + ["name=rootless"]
    return {"ID": engine_id, "ServerVersion": "28.0.0", "OperatingSystem": "Fixture Linux",
            "SecurityOptions": options, "ServerErrors": None}


def trust_daemon(controller, engine_id=None):
    """Test-only: mark the daemon binding verified for tests that exercise the runner, not the daemon."""
    controller._daemon = pf.pf_docker.DaemonObservation(
        endpoint=controller.context.daemon.endpoint, engine_id=engine_id or controller.context.daemon.engine_id,
        server_version="28.0.0", operating_system="Fixture Linux", rootless=False)
    return controller


COMPOSE_FIXTURE = PACKAGE / "fixtures" / "compose" / "partflow-2.40.2-desktop.1.json"


class FakeDocker:
    """Handle on an installed fake Docker tool: its state file and recorded calls."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "docker"
        self.state_path = self.directory / "docker-state.json"
        self.calls_path = self.directory / "calls.jsonl"

    def state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def state_bytes(self):
        return self.state_path.read_bytes()

    def write_state(self, state):
        self.state_path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")

    def update(self, **fields):
        state = self.state()
        state.update(fields)
        self.write_state(state)
        return state

    def calls(self):
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def argvs(self):
        return [call["argv"] for call in self.calls()]

    def clear_calls(self):
        if self.calls_path.exists():
            self.calls_path.unlink()


def default_docker_state(engine_id=ENGINE_ID):
    return {"info": daemon_info(engine_id), "containers": [], "volumes": [], "networks": [], "images": [],
            "compose": {"fixture": str(COMPOSE_FIXTURE)}, "hooks": []}


def install_fake_docker(layout, state=None):
    """Install tests/fake_docker.py as the registered ``docker`` tool of ``layout`` (root-owned 0755,
    protected directory, absolute interpreter shebang) and rewrite bootstrap/tools.conf like the installer."""
    directory = Path(layout.root).parent / "fake-docker"
    directory.mkdir(exist_ok=True)
    os.chmod(directory, 0o755)
    source = (PACKAGE / "tests" / "fake_docker.py").read_text(encoding="utf-8")
    source = source.replace("STATE_DIR = None  # replaced when the tool is installed",
                            "STATE_DIR = " + repr(str(directory)), 1)
    body = "#!" + os.path.realpath(sys.executable) + " -I" + chr(10) + source
    tool = tool_script(directory, "docker", body)
    handle = FakeDocker(directory)
    handle.write_state(state if state is not None else default_docker_state())
    tools = pf_bootstrap.parse_tools_conf(pf_instance.read_bytes_nofollow(layout.tools_conf), label="tools.conf")
    tools["docker"] = str(tool)
    pf_instance._write_private_file(layout.tools_conf, pf_bootstrap.render_tools_conf(tools), 0o600)
    return handle


def labels_for(context, service=None, *, project=None, oneoff="False", markers=True):
    labels = {pf.pf_docker.INSTANCE_LABEL: context.instance_id,
              pf.pf_docker.COMPOSE_PROJECT_LABEL: project or context.compose_project}
    if service is not None:
        labels[pf.pf_docker.COMPOSE_SERVICE_LABEL] = service
        if markers:
            labels[pf.pf_docker.COMPOSE_ONEOFF_LABEL] = oneoff
            labels[pf.pf_docker.COMPOSE_CONTAINER_NUMBER_LABEL] = "1"
    return labels


def container(cid, name, labels, *, image="sha256:img", config_image="", volumes=(), binds=(), networks=(),
              status="running", created="2026-10-06T00:00:00Z"):
    mounts = [{"Type": "volume", "Name": volume_name, "Source": "/var/lib/docker/volumes/" + volume_name + "/_data",
               "Destination": "/data", "RW": True, "Driver": "local", "Mode": "z", "Propagation": ""}
              for volume_name in volumes]
    mounts += [{"Type": "bind", "Source": path, "Destination": "/bind", "RW": True, "Mode": "", "Propagation": "rprivate"}
               for path in binds]
    return {"id": cid, "name": "/" + name, "labels": labels, "image": image, "config_image": config_image,
            "created": created, "status": status, "mounts": mounts,
            "networks": {name: {"NetworkID": nid, "IPAddress": "172.18.0.2"} for name, nid in networks}}


def volume(name, labels, *, driver="local", scope="local", created_at="2026-10-06T00:00:00Z", options=None):
    return {"name": name, "driver": driver, "scope": scope, "created_at": created_at,
            "mountpoint": "/var/lib/docker/volumes/" + name + "/_data", "labels": labels, "options": options}


def network(nid, name, labels, *, driver="bridge", scope="local", created="2026-10-06T00:00:00.000000000Z"):
    return {"id": nid, "name": name, "driver": driver, "scope": scope, "created": created, "labels": labels}


def image(iid, tags, labels):
    return {"id": iid, "repo_tags": list(tags), "labels": labels}


def topology_image_id(prefix, service):
    """The image ID of ``service`` in owned_topology(prefix=...) (PF-A3.1: a realistic sha256:<64 hex>)."""
    return image_id(prefix + service)


DB_IMAGE_ID = image_id("postgres-16")


def owned_topology(context, *, prefix="a", with_images=True):
    """State fragments for one fully owned Compose deployment of ``context`` (3 services, volume, network)."""
    project = context.compose_project
    net_id = (prefix * 64)[:64]
    network_name = project + "_default"
    volume_name = project + "_postgres_data"
    containers = []
    for index, service in enumerate(("db", "backend", "frontend")):
        cid = ((prefix + str(index)) * 32)[:64]
        containers.append(container(cid, f"{project}-{service}-1", labels_for(context, service),
                                    image=topology_image_id(prefix, service),
                                    config_image=f"{project}-{service}:candidate-x",
                                    volumes=(volume_name,) if service == "db" else (),
                                    networks=((network_name, net_id),)))
    volumes = [volume(volume_name, dict(labels_for(context), **{pf.pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"}))]
    networks = [network(net_id, network_name,
                        dict(labels_for(context), **{pf.pf_docker.COMPOSE_NETWORK_LABEL: "default"}))]
    images = []
    if with_images:
        for service in ("backend", "frontend"):
            images.append(image(topology_image_id(prefix, service),
                                [f"{project}-{service}:candidate-{prefix}00000000000-abcdef"],
                                {pf.pf_docker.INSTANCE_LABEL: context.instance_id}))
    return {"containers": containers, "volumes": volumes, "networks": networks, "images": images}


def compose_run_labels(context, service):
    """The label set Docker Compose v2 gives a ``compose run`` one-off container of ``service``.

    Compose v2 (docker/compose v2.40.2, ``pkg/compose/run.go`` ``prepareRun`` adds
    ``com.docker.compose.oneoff=True`` and ``com.docker.compose.slug``; ``pkg/compose/create.go``
    ``prepareLabels`` sets the project, service and ``config-hash`` labels and writes
    ``container-number`` only for numbered service containers, never for a one-off). The instance
    label comes from the service definition in compose.nas.yaml. Real-daemon confirmation is a
    docker-gate item (PF-A3.4/PF-A5.1).
    """
    pf_docker = pf.pf_docker
    return {pf_docker.INSTANCE_LABEL: context.instance_id,
            pf_docker.COMPOSE_PROJECT_LABEL: context.compose_project,
            pf_docker.COMPOSE_SERVICE_LABEL: service,
            pf_docker.COMPOSE_ONEOFF_LABEL: "True",
            pf_docker.COMPOSE_CONFIG_HASH_LABEL: "f" * 64,
            "com.docker.compose.slug": "0123456789ab" * 4 + "0123456789abcdef"}


@contextlib.contextmanager
def interactive_stdin():
    """The slave side of a fresh pty, for a launcher subprocess's stdin: the controller sees a terminal.

    The master stays open for the duration; both ends are closed on exit. Tests that need it skip
    with the reason ``pty unavailable`` when the host cannot allocate one.
    """
    try:
        master, slave = pty.openpty()
    except OSError as exc:
        raise unittest.SkipTest("pty unavailable: " + str(exc))
    try:
        yield slave
    finally:
        os.close(slave)
        os.close(master)


def install_wrapper(layout, name):
    """Fixture-only stand-in for the PF-A2 installer: the repository scheduler wrapper ``name``
    (backup.sh, release-check.sh) as <root>/bootstrap/<name>, root-owned 0700."""
    path = Path(layout.root) / pf_instance.BOOTSTRAP_DIR / name
    pf_instance._write_private_file(path, (PACKAGE / name).read_bytes(), 0o700)
    return path


def load_fake_docker_module():
    """tests/fake_docker.py as a module (its render_model builds expected Compose models)."""
    return load_module("fake_docker", PACKAGE / "tests" / "fake_docker.py")


def expected_render(context, *, values=None, root=None, override_text=None, mode=None):
    """The model the recorded Compose fixture renders for these effective inputs (JSON text)."""
    if values is None:
        values = pf.pf_config.parse_app_env((Path(context.paths.configuration) / ".env").read_bytes(), label=".env")
    child = pf.pf_config.child_values(values, workspace=root or context.paths.workspace,
                                      instance_id=context.instance_id)
    model = load_fake_docker_module().render_model(COMPOSE_FIXTURE, child, context.compose_project, mode=mode,
                                                   override_text=override_text)
    return json.dumps(model, ensure_ascii=False)


def daemon_shell_lines(directory):
    """sh lines for a fixture tool that answers like the bound daemon for the PF-A1.3 gates only:
    ``docker info``, the inventory listings (empty) and ``compose ... config --format json`` (the
    recorded fixture model from ``<directory>/compose-render.json``). Everything else falls through."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "daemon-info.json").write_text(json.dumps(daemon_info()) + "\n")
    return (
        f'if [ "$1" = info ]; then cat {directory}/daemon-info.json; exit 0; fi\n'
        'if [ "$*" = "ps -a --no-trunc --format {{.ID}}" ]; then exit 0; fi\n'
        'case "$1 $2" in "volume ls"|"network ls"|"image ls") exit 0;; esac\n'
        f'case "$*" in *" config --format json") cat {directory}/compose-render.json; exit 0;; esac\n'
    )


def write_daemon_answers(directory, context, **kwargs):
    (Path(directory) / "compose-render.json").write_text(expected_render(context, **kwargs), encoding="utf-8")


# ------------------------------------------------------------------ PF-A2.1 installer fixtures


def candidate_copy(base, *, mutate=None, block_on=None, name="candidate"):
    """A repository-shaped copy of every candidate file the installer reads (pf_install.CONTROL_RELEASE_FILES and
    BOOTSTRAP_FILES), root-owned, under ``<base>/<name>``.

    ``mutate``: {relative path: bytes | callable(bytes) -> bytes | None (remove)}. ``block_on``: "import" (the
    candidate's pf-admin.py blocks on the FIFO ``<base>/<name>.fifo`` whenever it is imported: reached by the smoke)
    or "instances" (it blocks only when run as __main__ with ``instances``: reached only by the installer's
    end-to-end verify). Before blocking it writes ``<base>/<name>.reached``. Test-only; no production hook.
    """
    pf_install = pf.pf_install
    base = Path(base)
    root = base / name
    # install-control.sh too, so a candidate tree can also run the repository init entry (RS-2/RS-4).
    relatives = sorted(set(pf_install.CONTROL_RELEASE_FILES.values()) | set(pf_install.BOOTSTRAP_FILES.values())
                       | {"deploy/synology/install-control.sh"})
    for relative in relatives:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_PACKAGE / relative).read_bytes())
    for current, dirs, files in os.walk(root):
        os.chmod(current, 0o755)
    if block_on is not None:
        fifo = base / (name + ".fifo")
        reached = base / (name + ".reached")
        if not fifo.exists():
            os.mkfifo(str(fifo), 0o600)
        condition = "True" if block_on == "import" else \
            '__name__ == "__main__" and "instances" in __import__("sys").argv'
        # Blocks once: a later import (a resume after the test released the FIFO) runs straight through.
        hook = ("if " + condition + " and not __import__('os').path.exists(" + repr(str(reached)) + "):\n"
                "    open(" + repr(str(reached)) + ", 'w').write('blocked')\n"
                "    open(" + repr(str(fifo)) + ", 'rb').read()\n")
        admin = root / "deploy/synology/pf-admin.py"
        text = admin.read_text(encoding="utf-8")
        marker = "from __future__ import annotations\n"
        admin.write_text(text.replace(marker, marker + hook, 1), encoding="utf-8")
    for relative, change in (mutate or {}).items():
        target = root / relative
        data = change(target.read_bytes()) if callable(change) else change
        if data is None:
            target.unlink()
        else:
            target.write_bytes(data)
    return root


def release_fifo(base, name="candidate"):
    """Let a blocked candidate continue: open the FIFO for writing once (the reader sees EOF), then close it."""
    fifo = Path(base) / (name + ".fifo")
    try:
        fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
    except OSError:  # ENXIO: no reader is blocked on it (any more)
        return False
    os.close(fd)
    return True


def legacy_home(base, *, workspace_name="my-checkout", env_repo=None, env_config=None, admin_legacy=None,
                admin_config=None, state=True, pending=False, launcher=None, lock_held=False,
                project="partflow-legacy", group=None, state_files=None, name="legacyhome"):
    """A v2.5 home: control/ (the v2.5 launcher target), config/, backups/ and recovery/ (0750), the checkout
    ``<home>/<workspace_name>`` and, with ``state``, ``.pf-state-<project>/`` (0700) holding operation.lock.

    ``env_repo``/``env_config``: text of ``<workspace>/.env`` / ``config/.env``; ``admin_legacy``/``admin_config``:
    pf-config.json fields (dicts, merged over a valid staging document) at ``<workspace>/deploy/synology/`` /
    ``config/``. ``pending`` writes a v2.5 ``pending.json``; ``launcher`` names a path that receives the v2.5
    launcher template of this home; ``lock_held`` returns an open descriptor holding the v2.5 lock (closed by
    the caller).
    """
    import fcntl
    import grp
    group = group or grp.getgrgid(os.getgid()).gr_name
    home = Path(base) / name
    home.mkdir(parents=True)
    os.chmod(home, 0o755)
    control = home / "control"
    control.mkdir()
    (control / "pf.sh").write_text("#!/bin/sh\n# v2.5 control (legacy fixture; never executed by these tests)\n")
    os.chmod(control / "pf.sh", 0o740)
    config = home / "config"
    config.mkdir()
    workspace = home / workspace_name
    (workspace / "deploy" / "synology").mkdir(parents=True)
    for directory in ("backups", "recovery"):
        (home / directory).mkdir()
        os.chmod(home / directory, 0o750)

    def admin(document):
        values = {"project": project, "environment": "staging", "backup_read_group": group,
                  "workspace_write_group": group}
        values.update(document or {})
        return json.dumps(values) + "\n"

    if env_repo is not None:
        (workspace / ".env").write_text(env_repo)
        os.chmod(workspace / ".env", 0o640)
    if env_config is not None:
        (config / ".env").write_text(env_config)
    if admin_legacy is not None:
        (workspace / "deploy" / "synology" / "pf-config.json").write_text(admin(admin_legacy))
        os.chmod(workspace / "deploy" / "synology" / "pf-config.json", 0o644)
    if admin_config is not None:
        (config / "pf-config.json").write_text(admin(admin_config))
    state_dir = home / (".pf-state-" + project)
    held = None
    if state:
        state_dir.mkdir()
        os.chmod(state_dir, 0o700)
        lock = state_dir / "operation.lock"
        lock.write_bytes(b"")
        os.chmod(lock, 0o600)
        for state_name, text in (state_files or {}).items():
            (state_dir / state_name).write_text(text)
            os.chmod(state_dir / state_name, 0o600)
        if pending:
            (state_dir / "pending.json").write_text('{"operation": "update", "phase": "paused"}\n')
            os.chmod(state_dir / "pending.json", 0o600)
        if lock_held:
            held = os.open(str(lock), os.O_RDONLY | os.O_CLOEXEC)
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if launcher is not None:
        launcher = Path(launcher)
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text('#!/bin/sh\n# PartFlow NAS installed launcher\nexec "' + str(control)
                            + '/pf.sh" "$@"\n')
        os.chmod(launcher, 0o700)
    return {"home": home, "control": control, "config": config, "workspace": workspace, "state": state_dir,
            "backups": home / "backups", "recovery": home / "recovery", "project": project, "lock_fd": held}


@contextlib.contextmanager
def typed_terminal(answers=()):
    """A fresh pty whose slave is a child's stdin; ``answers`` are typed into the master in advance."""
    try:
        master, slave = pty.openpty()
    except OSError as exc:
        raise unittest.SkipTest("pty unavailable: " + str(exc))
    try:
        if answers:
            os.write(master, ("\n".join(answers) + "\n").encode("utf-8"))
        yield slave
    finally:
        os.close(slave)
        os.close(master)


def run_installer(arguments, *, answers=(), env=None, timeout=300, cwd=None, preexec_fn=None, terminal=True,
                  script=None):
    """``sh install-control.sh <arguments>`` with a pty as stdin (``answers`` typed in advance, one per line).
    ``script``: another copy of install-control.sh (a candidate tree's); the repository's by default."""
    import subprocess
    environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
    environment.update(env or {})
    command = ["sh", str(script or PACKAGE / "install-control.sh"), *arguments]
    with (typed_terminal(answers) if terminal else contextlib.nullcontext(subprocess.DEVNULL)) as stdin:
        return subprocess.run(command, env=environment, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, check=False, timeout=timeout, cwd=cwd, preexec_fn=preexec_fn)


@contextlib.contextmanager
def forked_crash(seam, when, index):
    """Inside a forked child only: the ``index``-th call (0-based) of ``pf_install.<seam>`` ends the process with
    ``os._exit(137)`` ``before`` or ``after`` the real call, so no finally/except/last_error handler runs.
    Yields the call counter (a one-element list)."""
    from unittest import mock
    module = pf.pf_install
    real = getattr(module, seam)
    calls = [0]

    def wrapper(*args, **kwargs):
        number = calls[0]
        calls[0] += 1
        if number == index and when == "before":
            os._exit(137)
        result = real(*args, **kwargs)
        if number == index and when == "after":
            os._exit(137)
        return result

    with mock.patch.object(module, seam, wrapper):
        yield calls


# ------------------------------------------------------------------ PF-A2.2 config wizard fixtures


def admin_config(path, version=1, **values):
    """Write ``pf-config.json`` at ``path``: schema 1 holds exactly ``values`` (A1 form, other keys implicit);
    schema 2 is the rendered document of the frozen defaults overlaid with ``values`` (every key explicit)."""
    path = Path(path)
    if version == 1:
        path.write_text(json.dumps(values) + "\n")
    else:
        config = pf.pf_config
        merged = dict(config.ADMIN_CONFIG_DEFAULTS, **values)
        path.write_bytes(config.render_admin_config(config.admin_document(merged)))
    return path


def zone_dir(base, zones):
    """A fixture zone data directory: {name: "tzif" | "text" | "fifo" | "link:<target>"}. Returns its path."""
    directory = Path(base)
    directory.mkdir(parents=True, exist_ok=True)
    for name, kind in zones.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "tzif":
            path.write_bytes(b"TZif2" + b"\x00" * 40)
        elif kind == "text":
            path.write_bytes(b"not zone data\n")
        elif kind == "fifo":
            os.mkfifo(str(path), 0o600)
        elif kind.startswith("link:"):
            os.symlink(kind[len("link:"):], str(path))
        else:
            raise ValueError(kind)
    return directory


def with_acl(path):
    """Give ``path`` a POSIX access ACL (a named user entry) through the xattr API, or skip with a named reason."""
    import struct
    pf_bootstrap_module = pf.pf_bootstrap
    entries = ((pf_bootstrap_module.ACL_USER_OBJ, 6, 0xFFFFFFFF), (pf_bootstrap_module.ACL_USER, 6, 1000),
               (pf_bootstrap_module.ACL_GROUP_OBJ, 6, 0xFFFFFFFF), (pf_bootstrap_module.ACL_MASK, 6, 0xFFFFFFFF),
               (pf_bootstrap_module.ACL_OTHER, 4, 0xFFFFFFFF))
    blob = struct.pack("<I", pf_bootstrap_module.ACL_VERSION) + b"".join(struct.pack("<HHI", *entry)
                                                                      for entry in entries)
    if not hasattr(os, "setxattr"):
        raise unittest.SkipTest("posix ACL fixture unavailable: os.setxattr is missing")
    try:
        os.setxattr(str(path), "system.posix_acl_access", blob, follow_symlinks=False)
    except OSError as exc:
        raise unittest.SkipTest("posix ACL fixture unavailable: the filesystem refuses system.posix_acl_access "
                                f"({exc.strerror or exc})")
    return path


# ------------------------------------------------------------------ PF-A3.2 operation records (helpers only)

def _stamp(offset=0):
    return f"20261007T{(40000 + offset):06d}Z"


def operation_id(kind, suffix="1234abcd", offset=0):
    return f"{_stamp(offset)}-{kind}-{suffix}"


def effect(phase, type_, target, *, preconditions=(), postcondition="done", preservation_refs=()):
    return {"phase": phase, "type": type_, "target": target, "preconditions": list(preconditions),
            "postcondition": postcondition, "preservation_refs": list(preservation_refs)}


GENERATION = "wsg-20261007T040000Z-0badcafe"
CHECKPOINT_ID = "20261007T040000Z-" + "1" * 12 + "-c0ffee"
BUNDLE_ID = "purge-20261007T040000Z-" + "1" * 12 + "-c0ffee"


def workspace_effects(generation=GENERATION):
    return [effect("syncing-workspace", "source-stage", "workspace:stage:" + generation),
            effect("syncing-workspace", "source-switch", "workspace:retain:" + generation),
            effect("syncing-workspace", "source-switch", "workspace:bind:" + generation),
            effect("syncing-workspace", "file-write", "source-manifest")]


def activation_effects(deployment="dep-20261007T040000Z-0000abcd", heads=("r1",)):
    return [effect("activating", "service-change", "service:backend:start:" + "a" * 12,
                   preconditions=["heads:" + ",".join(heads)]),
            effect("activating", "service-change", "service:frontend:start:" + "b" * 12),
            effect("activating", "artifact-seal", "deployment:" + deployment),
            effect("activating", "file-write", "pointer:deployed.json")]


def default_effects(kind, *, migration=True, workspace=True, restore_db=True, deployment="dep-20261007T040000Z-0000abcd",
                    generation=GENERATION):
    """Effect specs in the section 3.4 shape of one kind (the targets a production plan writes)."""
    stage = effect("preparing", "source-stage", "deployment:" + deployment)
    stop = effect("preserving", "service-change", "services:stop:frontend,backend")
    capture = lambda reason, phase="preserving": effect(  # noqa: E731
        phase, "capture", "checkpoint:" + reason, preconditions=["bundle:" + CHECKPOINT_ID,
                                                                 "verify:pf_verify_" + "0" * 20])
    switch = [] if not workspace else workspace_effects(generation)
    if kind == "deploy":
        return [stage, effect("initializing", "service-change", "service:db:start"),
                effect("initializing", "database-migrate", "database:partflow_staging:heads=r1")] \
            + activation_effects(deployment) + switch
    if kind == "update":
        found = [stage, stop, capture("before-update")]
        if migration:
            candidate = "pf_migrate_" + "1" * 20
            found += [effect("migrating", "database-restore", "database:" + candidate),
                      effect("migrating", "database-migrate", f"database:{candidate}:heads=r2"),
                      effect("migrating", "database-drop", "database:" + candidate),
                      effect("migrating", "database-migrate", "database:partflow_staging:heads=r2")]
        return found + activation_effects(deployment, ("r2",) if migration else ("r1",)) + switch
    if kind == "rollback":
        found = [stage, dict(stop, phase="preserving-current"), capture("before-rollback", "preserving-current")]
        if restore_db:
            found += [effect("restoring-candidate", "database-restore", "database:pf_restore_" + "2" * 20),
                      effect("switching", "database-switch",
                             f"database-switch:partflow_staging:pf_restore_{'2' * 20}:pf_keep_20261007t040000z_abcdef")]
        return found + activation_effects(deployment) + switch
    if kind == "reset-db":
        return [stop, capture("before-reset"),
                effect("initializing", "database-create", "database:pf_clean_" + "3" * 20),
                effect("initializing", "database-migrate", f"database:pf_clean_{'3' * 20}:heads=r1"),
                effect("switching", "database-switch",
                       f"database-switch:partflow_staging:pf_clean_{'3' * 20}:pf_keep_20261007t040000z_abcdef"),
                activation_effects()[0], activation_effects()[1],
                effect("finalizing", "file-write", "last-reset", preconditions=["checkpoint:" + CHECKPOINT_ID])]
    if kind == "backup":
        return [capture("scheduled-or-manual-backup", "capturing"),
                effect("verifying", "verification", "bundle:scheduled-or-manual-backup")]
    if kind == "purge":
        return [stop, capture("before-purge"),
                effect("capturing", "capture", "purge-bundle", preconditions=["bundle:" + BUNDLE_ID,
                                                                             "checkpoint:" + CHECKPOINT_ID]),
                effect("verifying", "verification", "bundle:purge"),
                effect("deleting", "resource-delete", "deletion-plan")] + [
            effect("finalizing", "file-write", "purge-cleanup:" + name)
            for name in ("backups", "env", "state", "admin-config")]
    if kind == "restore-instance":
        return [effect("preparing-target", "source-stage", "deployment:" + deployment),
                effect("preparing-target", "file-write", "config:.env", postcondition="bytes sha256 " + "e" * 64),
                effect("preparing-target", "image-load", "images:" + BUNDLE_ID),
                effect("preparing-target", "service-change", "service:db:start"),
                effect("restoring-data", "database-drop", "database:partflow_staging"),
                effect("restoring-data", "database-restore", "database:partflow_staging")] \
            + activation_effects(deployment) + switch
    if kind == "abort-deploy":
        return [effect("deleting", "resource-delete", "deletion-plan"),
                effect("finalizing", "file-write", "override:active-images.yaml", postcondition="absent")]
    raise ValueError(kind)


def lifecycle_plan(context, kind, effects=None, *, op=None, workspace_mode=None, generation=GENERATION, reason=None,
                   supersedes=None, input_bundle=None, deletion_plan_sha256=None, frozen_config=None,
                   admin_config=None, deployment="dep-20261007T040000Z-0000abcd"):
    """A schema-valid OperationPlan of this instance (validated by the real lifecycle rules)."""
    effects = effects if effects is not None else default_effects(kind, deployment=deployment, generation=generation)
    numbered = [dict(item, effect_id=f"e{index:04d}") for index, item in enumerate(effects, 1)]
    if workspace_mode is None:
        if kind in ("backup", "reset-db", "purge", "abort-deploy"):
            workspace_mode = "untouched"
        elif any(item["target"].startswith("workspace:") for item in effects):
            workspace_mode = "switch"
        else:
            workspace_mode = "keep"
    switching = workspace_mode in ("switch", "pending")
    if kind in ("rollback", "restore-instance") and input_bundle is None:
        input_bundle = {"bundle_id": CHECKPOINT_ID if kind == "rollback" else BUNDLE_ID, "manifest_sha256": "f" * 64}
    if kind == "abort-deploy" and deletion_plan_sha256 is None:
        deletion_plan_sha256 = "d" * 64
    has_stage = any(item["type"] == "source-stage" and item["target"].startswith("deployment:") for item in effects)
    plan = {
        "schema_version": 1, "operation_id": op or operation_id(kind), "kind": kind, "created_at": _stamp(),
        "instance": {"instance_id": context.instance_id, "slug": context.slug,
                     "compose_project": context.compose_project, "daemon_engine_id": context.daemon.engine_id,
                     "record_sha256": context.record_sha256},
        "producer": {"control_release_id": context.control.release_id, "control_sha256": context.control.sha256,
                     "profile_id": context.profile.id, "profile_version": context.profile.version,
                     "profile_sha256": context.profile.sha256, "instance_record_sha256": context.record_sha256},
        "environment_policy": {"revision": context.approved_policy.revision, "sha256": context.approved_policy.sha256},
        "permission_policy": None,
        "source": {"provenance": "git_commit", "commit": NEW, "entries_sha256": "c" * 64,
                   "deployment_id": deployment if has_stage else None},
        "images": {}, "frozen_config": frozen_config, "admin_config": admin_config,
        "resources": {"inventory_sha256": None, "deletion_plan_sha256": deletion_plan_sha256},
        "coverage": [], "confirmation": {"phrase": kind.upper(), "summary_sha256": "0" * 64}
        if kind != "backup" else None,
        "limits": {"timeout_seconds": 3600, "minimum_free_bytes": 1048576},
        "effects": numbered, "recovery_route": [], "supersedes": supersedes,
        "workspace": {"mode": workspace_mode, "generation_id": generation if switching else None,
                      "container": str(pf_instance.generation_container(context)) if switching else None,
                      "reason": (reason or "workspace-capacity: test") if workspace_mode == "pending" else None},
        "input_bundle": input_bundle,
    }
    problems = pf.lifecycle_errors(plan, "operation_plan")
    if problems:
        raise AssertionError(problems)
    return plan


def lifecycle_journal(plan, *, phase="planned", states=None, sequence=1, deletion=None, unresolved=None,
                      retained=(), last_error=None, result=None, evidence=None, slug=None):
    """A journal generation of ``plan`` (``states``: {effect_id: state}; ``evidence``: {effect_id: text})."""
    states = states or {}
    evidence = evidence or {}
    data = pf_instance.normalize_json(plan)
    plan_sha256 = pf_instance.sha256_bytes(data)
    terminal = phase in pf.pf_config.TERMINAL_PHASES
    if result is None and terminal:
        outcome = {"completed": "succeeded"}.get(phase, phase)
        result = {"outcome": outcome, "deployment_id": None}
    journal = {
        "schema_version": 1, "operation_id": plan["operation_id"], "plan_sha256": plan_sha256, "kind": plan["kind"],
        "sequence": sequence, "phase": phase, "updated_at": _stamp(sequence),
        "approvals": [{"plan_sha256": plan_sha256, "confirmed_at": _stamp(), "method": "typed-phrase"}]
        if plan["confirmation"] is not None else [],
        "effects": [{"effect_id": item["effect_id"], "state": states.get(item["effect_id"], "not_started"),
                     "observed_at": _stamp() if states.get(item["effect_id"]) == "complete" else None,
                     "evidence": evidence.get(item["effect_id"])} for item in plan["effects"]],
        "unresolved_effect": unresolved, "retained_artifacts": list(retained), "last_error": last_error,
        "legal_next": [], "result": result, "deletion": deletion}
    journal["legal_next"] = pf.pf_config.legal_next(plan, journal, slug=slug or plan["instance"]["slug"])
    problems = pf.lifecycle_errors(journal, "operation_journal", plan=plan)
    if problems:
        raise AssertionError(problems)
    return journal


def write_operation(context, plan, journal=None, *, files=None):
    """The real writers (pf_instance.write_plan_once / write_journal_generation) for a fixture operation; ``files``:
    {name: bytes} written next to them (children.json, attempts.json, ...). Returns the operation directory."""
    directory = context.operations_dir / plan["operation_id"]
    os.mkdir(str(directory), 0o700)
    pf_instance.write_plan_once(directory, pf_instance.normalize_json(plan))
    if journal is not None:
        pf_instance.write_journal_generation(directory, pf_instance.normalize_json(journal))
    for name, data in (files or {}).items():
        pf_instance._write_private_file(directory / name, data, 0o600)
    return directory


def frozen_operation(context, kind, *, phase, states=None, unresolved=None, effects=None):
    """A real operation as a locked route writes it: its directory, the frozen application snapshot of the instance's
    config/.env (section 3.8, named by the plan's ``frozen_config``), the plan and journal generation 1. Returns the
    plan."""
    op = operation_id(kind)
    directory = context.operations_dir / op
    os.mkdir(str(directory), 0o700)
    data = (context.paths.configuration / ".env").read_bytes()
    values = pf.pf_config.parse_app_env(data, label=".env")
    frozen = pf.pf_config.freeze_app_config(values, source_bytes=data, operation_id=op, operation_dir=directory)
    rendered = frozen.env_file.read_bytes()
    plan = lifecycle_plan(context, kind, effects, op=op,
                          frozen_config={"sha256": pf_instance.sha256_bytes(rendered), "bytes": len(rendered)})
    pf_instance.write_plan_once(directory, pf_instance.normalize_json(plan))
    journal = lifecycle_journal(plan, phase=phase, states=states, unresolved=unresolved)
    pf_instance.write_journal_generation(directory, pf_instance.normalize_json(journal))
    return plan


def operation(context, operation_id):
    """(plan, journal) of one operation directory (None for an absent file)."""
    directory = context.operations_dir / operation_id
    result = []
    for name in ("plan.json", "journal.json"):
        path = directory / name
        result.append(json.loads(path.read_bytes()) if path.exists() else None)
    return tuple(result)


def operation_files(context, operation_id):
    """{name: bytes} of the protected operation files that a refused resume must leave byte-identical."""
    directory = context.operations_dir / operation_id
    return {name: (directory / name).read_bytes() for name in pf_instance.OPERATION_FILES[:5]
            if (directory / name).exists()}


def operations_of(context, kind=None):
    """[(operation_id, plan, journal)] of every operation with a plan, oldest first (optionally of one kind)."""
    found = []
    root = context.operations_dir
    for name in sorted(os.listdir(str(root))) if root.exists() else []:
        plan, journal = operation(context, name)
        if plan is not None and (kind is None or plan["kind"] == kind):
            found.append((name, plan, journal))
    return found


def clear_operations(context):
    """Test cleanup between subtests: remove every operation directory that holds a plan (never in production)."""
    import shutil
    root = context.operations_dir
    for name in sorted(os.listdir(str(root))) if root.exists() else []:
        if (root / name / "plan.json").exists():
            shutil.rmtree(str(root / name))


def operations_bytes(context):
    """{"<op>/<file>": bytes} of every plan/journal/attempts/children/progress file (byte-identity checks)."""
    found = {}
    root = context.operations_dir
    for name in sorted(os.listdir(str(root))) if root.exists() else []:
        for item in pf_instance.OPERATION_FILES[:5]:
            path = root / name / item
            if path.exists():
                found[name + "/" + item] = path.read_bytes()
    return found


def open_operations(context):
    """[(kind, phase)] of the blocking lifecycle operations of ``context`` (the real index)."""
    files, overflow = pf_instance.scan_operations(context.operations_dir)
    index = pf.pf_config.classify_operations(files, permissions_journal=None, overflow=overflow,
                                             validate=lambda value, name, plan=None: pf.lifecycle_errors(
                                                 value, name, plan=plan))
    return [(entry.kind, entry.phase) for entry in index.blocking]


def interrupted_deploy(context, *, phase="initializing", unknown="e0003"):
    """An initial deploy interrupted in its migration (frontend effect not started): abort-deploy may supersede it."""
    plan = lifecycle_plan(context, "deploy")
    states = {f"e{index:04d}": "complete" for index in range(1, int(unknown[1:]))}
    states[unknown] = "unknown"
    write_operation(context, plan, lifecycle_journal(plan, phase=phase, states=states, unresolved=unknown))
    return plan


def deleting_purge(context):
    """A purge interrupted in its deletion (the deletion approval journaled; no deletion plan file needed by the
    read-only diagnostics). Returns the plan."""
    plan = lifecycle_plan(context, "purge")
    write_operation(context, plan, lifecycle_journal(
        plan, phase="deleting", unresolved="e0005",
        states={"e0001": "complete", "e0002": "complete", "e0003": "complete", "e0004": "complete",
                "e0005": "unknown"},
        deletion={"plan_sha256": "d" * 64, "delete_backups": True, "reset_admin_config": False,
                  "confirmed_at": "20261007T040500Z"}))
    return plan


def migrating_update(context):
    """An update interrupted in its live migration (heads unknown). Returns the plan."""
    plan = lifecycle_plan(context, "update")
    write_operation(context, plan, lifecycle_journal(
        plan, phase="migrating", unresolved="e0007",
        states={**{f"e000{index}": "complete" for index in range(1, 7)}, "e0007": "unknown"}))
    return plan


def run_workspace_switch(controller, candidate, revision, *, verified=True):
    """PF-A3.2 test helper (the former ``replace_source``), inside ``controller.lock()``: a real plan whose source-stage
    effect stages ``candidate`` as this operation's deployment and whose W1-W4 switch the workspace to it. A refused
    candidate ends ``deployment-stage-failed`` (closed cancelled, workspace untouched)."""
    import uuid as _uuid
    manifest = controller.candidate_manifest(candidate, revision, verified=verified)
    deployment = f"dep-{pf.utc()}-{_uuid.uuid4().hex[:8]}"
    workspace = controller.workspace_plan("switch")
    effects = [{"phase": "preparing", "type": "source-stage", "target": "deployment:" + deployment,
                "postcondition": f"staged {deployment}", "preconditions": ["test"]}]
    effects += controller.workspace_effect_specs(workspace, deployment_id=deployment)
    git = manifest["source"]["kind"] == "git_commit"
    return controller.start_operation(
        "update", {"candidate": candidate, "manifest": manifest, "images": None, "ref": None, "pointer": {}},
        effects=effects, workspace=workspace, images={}, confirmation={"phrase": "TEST", "summary_sha256": "0" * 64},
        source={"provenance": "git_commit" if git else "unknown", "commit": revision if git else None,
                "entries_sha256": pf.pf_source.entries_digest(manifest), "deployment_id": deployment})
