"""Disposable protected-installation fixtures for the PF-A1.1 offline tests.

Everything is created under a temporary directory owned by the test process.
The fixture plays the role of the trusted installer: it initializes an
installation root, installs the real control files as a release, and registers
instances through the explicit registration transaction. Nothing here touches
Docker, the network or any existing installation.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys

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


def install_root(base, *, launcher=None, interpreter=None, tools=None):
    """Initialize <base>/install as a trusted installation root."""
    values = pf_instance.initialize_installation_root(
        Path(base) / "install",
        launcher=launcher if launcher is not None else (REPO_PACKAGE / "pf.sh").read_bytes(),
        interpreter=interpreter or os.path.realpath(sys.executable),
        release_id=RELEASE_ID,
        release_files=release_files(),
        profile=(PROFILE_NAME, profile_document()),
        policy_documents={
            POLICY_NAME: policy_document(),
            "production.json": policy_document(environment="production"),
        },
        tools=default_tools() if tools is None else tools,
    )
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
        "daemon": {"endpoint": "unix:///var/run/docker.sock", "engine_id": engine_id},
        "paths": paths,
        "control_release_id": RELEASE_ID,
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
