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


def install_root(base, *, launcher=None, interpreter=None, tools=None, wrappers=True, release_id=RELEASE_ID):
    """Initialize <base>/install as a trusted installation root (plus the fixture daemon socket).

    ``wrappers``: place the repository scheduler wrappers in bootstrap/ as the PF-A2.1 installer does.
    ``release_id``: the fixture id by default; content_release_id() gives the installer's own id."""
    endpoint, socket_path = install_daemon_socket(base)
    values = pf_instance.initialize_installation_root(
        Path(base) / "install",
        launcher=launcher if launcher is not None else (REPO_PACKAGE / "pf.sh").read_bytes(),
        interpreter=interpreter or os.path.realpath(sys.executable),
        release_id=release_id,
        release_files=release_files(),
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
                                    image=f"sha256:{prefix}{service}", config_image=f"{project}-{service}:candidate-x",
                                    volumes=(volume_name,) if service == "db" else (),
                                    networks=((network_name, net_id),)))
    volumes = [volume(volume_name, dict(labels_for(context), **{pf.pf_docker.COMPOSE_VOLUME_LABEL: "postgres_data"}))]
    networks = [network(net_id, network_name,
                        dict(labels_for(context), **{pf.pf_docker.COMPOSE_NETWORK_LABEL: "default"}))]
    images = []
    if with_images:
        for service in ("backend", "frontend"):
            images.append(image(f"sha256:{prefix}{service}",
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
