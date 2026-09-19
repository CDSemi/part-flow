"""PF-A1.2 acceptance and regression tests: runner, frozen configuration, protected source store.

Case mapping (ACCEPTANCE_CASES.json, revision 2026-09-13-design-r3):
  A1-T05 subprocess environment injection      -> SubprocessEnvironment (offline + installed CLI)
  A1-T06 writable Git executable metadata      -> WritableGitMetadata (real Git against a local approved remote)
  A1-T07 Python and plugin import injection    -> ImportAndPluginInjection (installed CLI + trusted executable chain)
  A1-T08 app values and secret round-trip      -> AppValuesRoundTrip
  A1-T09 timeout and descendant cancellation   -> TimeoutAndCancellation
  A1-T10 no writable Git provenance dependency -> ProvenanceWithoutGit
  A1-T18 frozen authority                      -> FrozenAuthority (offline + installed CLI)
  A1-T03 / A1-T17 repeats                      -> ReadOnlyAfterExtraction, ProtectedRuntimeDirectories
Negative tests write only harmless marker files inside disposable fixtures and assert
that the marker is absent and the intended target untouched. Docker is never
contacted: registered "docker"/"git" tools are fixture scripts unless a test says
"real git", in which case the real Git executable works against a local bare remote.
"""
import contextlib
import grp
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import urllib.parse
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protected_fixture as pfx  # noqa: E402

pf = pfx.pf
pf_instance = pfx.pf_instance
pf_bootstrap = pfx.pf_bootstrap
pf_runner = pf.pf_runner
pf_config = pf.pf_config
pf_source = pf.pf_source
GROUP = grp.getgrgid(os.getgid()).gr_name
ROOT_REQUIRED = unittest.skipUnless(os.geteuid() == 0, "protected fixtures require root ownership (uid 0)")
SECRET = "Fixture-Secret-p@ss:w/rd#1"
REAL_GIT = "/usr/bin/git" if os.path.isfile("/usr/bin/git") else None


def run_main(arguments, layout, **kwargs):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = pf.main(arguments, installation_root=layout.root, running_release=layout.release_dir,
                       trusted_launch=True, **kwargs)
    return code, stdout.getvalue(), stderr.getvalue()


def launcher_run(layout, arguments, env=None, cwd=None):
    environment = {"PATH": "/usr/bin:/bin", "TERM": "dumb"}
    environment.update(env or {})
    return subprocess.run([str(layout.launcher), *arguments], env=environment, cwd=str(cwd or layout.root.parent),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=180)


def recording_tool(directory, name, *, exit_code=1, extra=""):
    """A registered fixture tool that records its environment/argv/cwd and never contacts anything.

    ``docker compose version`` answers like an installed plugin so the Compose contract
    itself is exercised; every other invocation fails like a host without a daemon.
    """
    directory = Path(directory)
    body = (
        "#!/bin/sh\n"
        f"env | LC_ALL=C sort > {directory}/observed-{name}-env.txt\n"
        f"printf '%s\\n' \"$@\" > {directory}/observed-{name}-argv.txt\n"
        f"pwd > {directory}/observed-{name}-cwd.txt\n"
        "if [ \"$1\" = compose ] && [ \"$2\" = version ]; then echo 'Docker Compose version v2-fixture'; exit 0; fi\n"
        f"{extra}"
        f"echo 'fixture {name}: no daemon' >&2\n"
        f"exit {exit_code}\n"
    )
    return pfx.tool_script(directory / "tools", name, body)


def observed(directory, name):
    env_path = Path(directory) / f"observed-{name}-env.txt"
    if not env_path.exists():
        return None
    env = {}
    for line in env_path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    argv = (Path(directory) / f"observed-{name}-argv.txt").read_text().splitlines()
    cwd = (Path(directory) / f"observed-{name}-cwd.txt").read_text().strip()
    return {"env": env, "argv": argv, "cwd": cwd}


def marker_executable(directory, name, marker):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\necho executed > {marker}\nexit 0\n")
    os.chmod(path, 0o755)
    return path


def hostile_environment(base, marker_dir, marker):
    """Every caller-controlled channel pointed at harmless markers (A1-T05/T07)."""
    return {
        "PATH": f"{marker_dir}:/usr/bin:/bin",
        "HOME": str(base / "hostile-home"),
        "PYTHONPATH": str(base / "hostile-python"),
        "PYTHONSTARTUP": str(base / "hostile-python" / "startup.py"),
        "PYTHONHOME": str(base / "hostile-python"),
        "LD_PRELOAD": str(base / "hostile-python" / "libnothing.so"),
        "DOCKER_HOST": "tcp://127.0.0.1:1",
        "DOCKER_CONTEXT": "hostile",
        "DOCKER_CONFIG": str(base / "hostile-home" / ".docker"),
        "DOCKER_CLI_PLUGIN_EXTRA_DIRS": str(base / "hostile-home" / ".docker" / "cli-plugins"),
        "COMPOSE_FILE": str(base / "hostile-compose.yaml"),
        "COMPOSE_PROJECT_NAME": "hostile",
        "GIT_CONFIG_GLOBAL": str(base / "hostile-gitconfig"),
        "GIT_CONFIG_SYSTEM": str(base / "hostile-gitconfig"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": str(marker_dir / "git-marker"),
        "GIT_EXEC_PATH": str(marker_dir),
        "GIT_SSH_COMMAND": str(marker_dir / "git-marker"),
        "PF_HOME": str(base / "elsewhere"),
        "PF_PYTHON": str(marker_dir / "python3"),
        "PF_CONFIG_DIR": "/nonexistent",
        "TERM": "dumb",
    }


def plant_markers(base, marker):
    """Marker executables/plugins/import hooks in every hostile location."""
    marker_dir = base / "hostile-bin"
    for name in ("docker", "git", "docker-compose", "python3", "git-marker", "git-remote-https"):
        marker_executable(marker_dir, name, marker)
    hostile_home = base / "hostile-home"
    marker_executable(hostile_home / ".docker" / "cli-plugins", "docker-compose", marker)
    (hostile_home / ".docker" / "config.json").write_text(json.dumps({
        "cliPluginsExtraDirs": [str(marker_dir)], "credsStore": "marker", "currentContext": "hostile",
    }))
    (hostile_home / ".gitconfig").write_text(f"[core]\n\tfsmonitor = {marker_dir}/git-marker\n")
    (base / "hostile-gitconfig").write_text(f"[core]\n\thooksPath = {marker_dir}\n\tfsmonitor = {marker_dir}/git-marker\n")
    python_dir = base / "hostile-python"
    python_dir.mkdir(exist_ok=True)
    for name in ("sitecustomize.py", "usercustomize.py", "pf_instance.py", "pf_runner.py", "startup.py"):
        (python_dir / name).write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    return marker_dir


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        os.chmod(self.base, 0o755)
        self.marker = self.base / "marker-executed"

    def tearDown(self):
        for current, dirs, files in os.walk(self.base):
            for name in dirs:
                path = Path(current) / name
                if not path.is_symlink():
                    try:
                        os.chmod(path, 0o700)
                    except OSError:
                        pass
            for name in files:
                path = Path(current) / name
                if not path.is_symlink():
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass
        self.temp.cleanup()

    def install(self, tools):
        self.layout = pfx.install_root(self.base, tools=tools)
        return self.layout

    def instance(self, name="staging", **kwargs):
        paths = pfx.data_home(self.base / name, project="partflow-staging", group=GROUP, **kwargs)
        context = pfx.register(self.layout, name, paths, project="partflow-staging")
        pfx.deployed_record(context)
        return context, paths

    def controller(self, context):
        validation = pf_instance.validate_context(context, running_release=self.layout.release_dir)
        self.assertTrue(validation.mutation_allowed, validation.blocking_messages())
        return pf.Controller(context, validation=validation, running_release=self.layout.release_dir)


# =============================================================================== A1-T05


@ROOT_REQUIRED
class SubprocessEnvironment(Base):
    """A1-T05: caller PATH/loader/Docker/Git/client-config values never reach a child; only the
    registered executables and the allowlisted host environment are used."""

    def setUp(self):
        super().setUp()
        self.marker_dir = plant_markers(self.base, self.marker)
        self.tools = {"docker": str(recording_tool(self.base, "docker")),
                      "git": str(recording_tool(self.base, "git"))}
        self.install(self.tools)
        self.context, self.paths = self.instance()

    def assert_allowlisted(self, name, *, app_values=False):
        seen = observed(self.base, name)
        self.assertIsNotNone(seen, f"registered {name} tool did not run")
        env = dict(seen["env"])
        env.pop("PWD", None)  # set by the fixture shell itself, not passed by the runner
        expected_keys = set(pf_runner.host_environment(home="/x", docker_config="/y", docker_host="z"))
        if app_values:
            # A Compose invocation adds exactly the allowlisted application keys and the
            # two core-generated ones; nothing else.
            self.assertEqual(set(env) - expected_keys, set(pf_config.CHILD_KEYS), env)
            env = {key: value for key, value in env.items() if key not in pf_config.CHILD_KEYS}
        self.assertEqual(set(env), expected_keys, env)
        self.assertEqual(env["PATH"], pf_runner.TRUSTED_PATH)
        self.assertEqual(env["HOME"], str(self.context.home_dir))
        self.assertEqual(env["DOCKER_CONFIG"], str(self.context.docker_config_dir))
        self.assertEqual(env["DOCKER_HOST"], self.context.daemon.endpoint)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        for forbidden in ("PYTHONPATH", "LD_PRELOAD", "DOCKER_CONTEXT", "COMPOSE_FILE", "GIT_CONFIG_COUNT",
                          "PF_HOME", "PF_PYTHON", "GIT_EXEC_PATH", "GIT_SSH_COMMAND", "DOCKER_CLI_PLUGIN_EXTRA_DIRS"):
            self.assertNotIn(forbidden, env)
        self.assertEqual(seen["cwd"], str(self.context.installation_root))
        return seen

    def test_in_process_doctor_uses_registered_tools_and_allowlisted_environment(self):
        hostile = hostile_environment(self.base, self.marker_dir, self.marker)
        before = pfx.snapshot_tree(self.base / "staging")
        with mock.patch.dict(os.environ, hostile, clear=True):
            code, out, err = run_main(["--instance", "staging", "doctor"], self.layout)
        self.assertEqual(code, 1, err)  # live checks are unavailable: the fixture tools have no daemon
        self.assertFalse(self.marker.exists(), "a marker executable from the caller environment ran")
        git = self.assert_allowlisted("git")
        docker = self.assert_allowlisted("docker", app_values=True)
        self.assertEqual(git["argv"], ["--version"])
        # The last Docker invocation of doctor is the Compose contract check with the fixed inputs.
        self.assertEqual(docker["argv"][0], "compose")
        self.assertEqual(docker["argv"][docker["argv"].index("--env-file") + 1], str(self.context.diagnostic_env_path))
        self.assertEqual(self.context.diagnostic_env_path.read_bytes().strip().startswith(b"#"), True)
        self.assertIn("Registered tools (bootstrap/tools.conf; PATH is never searched): docker=" + self.tools["docker"], out)
        self.assertIn("Git: unavailable", out)
        self.assertIn("Docker: unavailable", out)
        self.assertEqual(pfx.snapshot_tree(self.base / "staging"), before)

    def test_installed_launcher_status_uses_registered_tools_only(self):
        hostile = hostile_environment(self.base, self.marker_dir, self.marker)
        result = launcher_run(self.layout, ["--instance", "staging", "status"], env=hostile)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.marker.exists(), "a marker executable from the caller environment ran")
        self.assert_allowlisted("docker", app_values=True)
        self.assertIn("Instance: staging", result.stdout)
        self.assertIn("Compose services: unavailable", result.stdout)
        # The observed argv is the fixed Compose contract: explicit project, empty env-file, installed file.
        argv = observed(self.base, "docker")["argv"]
        self.assertEqual(argv[0], "compose")
        self.assertIn("--env-file", argv)
        self.assertEqual(argv[argv.index("--env-file") + 1], str(self.context.diagnostic_env_path))
        self.assertEqual(argv[argv.index("-f") + 1], str(self.context.control.path / "compose.nas.yaml"))
        self.assertEqual(argv[argv.index("-p") + 1], "partflow-staging")

    def test_application_values_are_separate_from_host_authority(self):
        """App keys reach Compose as allowlisted variables; host variables can never be overridden."""
        controller = self.controller(self.context)
        controller.cli = ["docker", "compose"]
        with self.assertRaises(pf.Failure):
            controller.command(["docker", "version"], env={"DOCKER_HOST": "tcp://evil:2375"})
        with self.assertRaises(pf.Failure):
            controller.command(["docker", "version"], env={"PATH": str(self.marker_dir)})
        with self.assertRaises(pf.Failure):
            controller.command(["docker", "version"], env={"COMPOSE_FILE": "/x"})
        self.assertFalse((self.base / "observed-docker-env.txt").exists())
        with self.assertRaises(pf.Failure):
            controller.compose("config", "-q")  # the fixture docker exits 1
        env = observed(self.base, "docker")["env"]
        self.assertEqual(env["POSTGRES_PASSWORD"], "abc123")
        self.assertEqual(env["PARTFLOW_REPO_ROOT"], str(self.context.paths.workspace))
        self.assertEqual(env["PARTFLOW_DATABASE_URL"], "postgresql+psycopg://partflow_staging:abc123@db:5432/partflow_staging")
        self.assertEqual(env["DOCKER_HOST"], self.context.daemon.endpoint)
        with self.assertRaises(pf.Failure):
            controller.command(["python3", "-c", "print(1)"])  # not a typed executable id
        with self.assertRaises(pf.Failure):
            controller.command(["ip", "addr"])  # registered? no -> refused, never searched on PATH
        self.assertFalse(self.marker.exists())


# =============================================================================== A1-T06


def make_bare_upstream(path, tree_revision=pfx.OLD):
    """A local bare 'approved remote' holding two commits; returns (first_sha, second_sha, worktree)."""
    work = Path(path) / "work"
    pfx.source_fixture(work, tree_revision)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=work, stderr=subprocess.DEVNULL,
                                       env={"PATH": "/usr/bin:/bin", "HOME": str(path),
                                            "GIT_CONFIG_NOSYSTEM": "1"}).decode().strip()

    (work / ".gitattributes").write_text("* filter=marker\napp-version.txt export-ignore\n")
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("add", ".")
    git("commit", "-qm", "first")
    first = git("rev-parse", "HEAD")
    (work / "app-version.txt").write_text(pfx.NEW)
    git("commit", "-qam", "second")
    second = git("rev-parse", "HEAD")
    bare = Path(path) / "approved.git"
    subprocess.check_call(["git", "clone", "-q", "--bare", str(work), str(bare)], stderr=subprocess.DEVNULL,
                          env={"PATH": "/usr/bin:/bin", "HOME": str(path), "GIT_CONFIG_NOSYSTEM": "1"})
    return first, second, work


def hostile_git_metadata(workspace, marker_dir, marker, alternate_objects):
    """A shared checkout whose .git carries every executable/redirecting configuration."""
    git_dir = Path(workspace) / ".git"
    (git_dir / "hooks").mkdir(parents=True)
    (git_dir / "objects" / "info").mkdir(parents=True)
    (git_dir / "refs" / "heads").mkdir(parents=True)
    for hook in ("pre-commit", "post-checkout", "fsmonitor-watchman"):
        marker_executable(git_dir / "hooks", hook, marker)
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n"
        f"\tfsmonitor = {marker_dir}/git-marker\n\thooksPath = {git_dir}/hooks\n"
        f"[filter \"marker\"]\n\tclean = {marker_dir}/git-marker\n\tsmudge = {marker_dir}/git-marker\n\trequired = true\n"
        f"[include]\n\tpath = {marker_dir}/../hostile-gitconfig\n"
        f"[remote \"origin\"]\n\turl = {marker_dir}/../hostile-remote.git\n"
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
        f"[uploadpack]\n\tpackObjectsHook = {marker_dir}/git-marker\n"
    )
    (git_dir / "objects" / "info" / "alternates").write_text(str(alternate_objects) + "\n")
    (Path(workspace) / ".gitattributes").write_text("* filter=marker\napp-version.txt export-ignore\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return git_dir


@ROOT_REQUIRED
@unittest.skipUnless(REAL_GIT, "real git required")
class WritableGitMetadata(Base):
    """A1-T06: shared Git metadata (fsmonitor, hooks, filters, includes, alternates, local remote)
    is never consulted; source resolves from the protected store with controlled configuration."""

    def setUp(self):
        super().setUp()
        self.marker_dir = plant_markers(self.base, self.marker)
        self.first, self.second, self.upstream_work = make_bare_upstream(self.base / "upstream")
        self.install({"git": REAL_GIT, "docker": str(recording_tool(self.base, "docker"))})
        self.context, self.paths = self.instance()
        self.workspace = self.paths["workspace"]
        self.git_dir = hostile_git_metadata(self.workspace, self.marker_dir, self.marker,
                                            self.base / "upstream" / "approved.git" / "objects")
        (self.git_dir / "refs" / "heads" / "main").write_text(self.first + "\n")
        self.git_calls = []
        self.c = self.controller(self.context)
        self.c.remote_override = str(self.base / "upstream" / "approved.git")
        self.c.source_protocols = ("file",)
        original = self.c.command

        def recorded(argv, **kwargs):
            if argv and argv[0] == "git":
                self.git_calls.append((list(map(str, argv)), str(kwargs.get("cwd"))))
            return original(argv, **kwargs)

        self.c.command = recorded

    def assert_git_never_touched_workspace(self):
        self.assertTrue(self.git_calls)
        store = self.c.source_store()
        for argv, cwd in self.git_calls:
            if argv[1] == "init":
                self.assertEqual(argv[2:4], ["--bare", "-q"], argv)
                self.assertEqual(argv[-1], str(store.path), argv)
            else:
                self.assertEqual(argv[1], "--git-dir=" + str(store.path), argv)
            self.assertEqual(cwd, str(store.sources_root))
            self.assertNotIn(str(self.workspace), " ".join(argv))
        self.assertFalse(self.marker.exists(), "workspace Git metadata executed a marker")

    def test_source_resolves_from_the_protected_store_not_the_shared_checkout(self):
        before = pfx.snapshot_tree(self.workspace)
        candidate = self.base / "candidate"
        with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
            self.c.materialize_source({"sha": self.first}, candidate)
        self.assert_git_never_touched_workspace()
        self.assertEqual((candidate / "app-version.txt").read_text(), pfx.OLD)
        self.assertFalse((candidate / ".git").exists())
        self.assertEqual(pfx.snapshot_tree(self.workspace), before)
        store = self.c.source_store()
        store.verify()
        self.assertTrue(store.path.is_relative_to(self.layout.sources))
        config = pf_source.parse_git_config((store.path / "config").read_text())
        self.assertEqual(config["core.hookspath"], str(store.hooks_path))
        self.assertEqual(config["core.fsmonitor"], "false")
        self.assertEqual(config["protocol.allow"], "never")
        self.assertEqual(config["remote.approved.url"], self.c.remote_override)
        self.assertFalse((store.path / "objects" / "info" / "alternates").exists())
        self.assertEqual(os.listdir(store.hooks_path), [])
        self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o700)
        self.assertTrue((store.path / "refs" / "pinned" / self.first).is_file())
        # The workspace's attributes (export-ignore, filters) did not shape the export.
        self.assertTrue((candidate / "app-version.txt").is_file())

    def test_current_deploy_proves_the_workspace_against_the_store_without_root_git(self):
        candidate = self.base / "candidate"
        with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
            target = self.c.current_target(candidate)
        self.assertEqual(target["sha"], self.first)
        self.assert_git_never_touched_workspace()
        # Edited workspace: provenance unknown, no SHA assigned, differences listed.
        (self.workspace / "frontend" / "app.txt").write_text("edited")
        self.git_calls.clear()
        with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
            with self.assertRaises(pf.Failure) as caught:
                self.c.current_target(self.base / "candidate-2")
        self.assertIn("provenance is unknown", str(caught.exception))
        self.assertIn("changed:frontend/app.txt", str(caught.exception))
        self.assert_git_never_touched_workspace()
        self.assertFalse(self.marker.exists())

    def test_tampered_store_configuration_is_refused_before_use(self):
        with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
            self.c.materialize_source({"sha": self.first}, self.base / "candidate")
        store = self.c.source_store()
        config_path = store.path / "config"
        original = config_path.read_text()
        variants = {
            "alternates": None,
            "include": original + f"[include]\n\tpath = {self.base}/hostile-gitconfig\n",
            "fsmonitor": original.replace("fsmonitor = false", f"fsmonitor = {self.marker_dir}/git-marker"),
            "hooks": original.replace(f"hooksPath = {store.hooks_path}", f"hooksPath = {self.git_dir}/hooks"),
            "filter": original + f"[filter \"x\"]\n\tclean = {self.marker_dir}/git-marker\n",
            "remote": original.replace("url = " + self.c.remote_override, f"url = {self.base}/hostile-remote.git"),
        }
        for label, text in variants.items():
            with self.subTest(variant=label):
                if text is None:
                    (store.path / "objects" / "info" / "alternates").write_text(str(self.git_dir / "objects") + "\n")
                else:
                    config_path.write_text(text)
                self.git_calls.clear()
                with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
                    with self.assertRaises(pf.Failure) as caught:
                        self.c.materialize_source({"sha": self.second}, self.base / ("cand-" + label))
                self.assertIn("refusing", str(caught.exception))
                self.assertEqual(self.git_calls, [], label)  # refused before any Git process
                self.assertFalse(self.marker.exists())
                config_path.write_text(original)
                alternates = store.path / "objects" / "info" / "alternates"
                if alternates.exists():
                    alternates.unlink()

    def test_unsupported_source_entries_are_refused_before_mutation(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base), "GIT_CONFIG_NOSYSTEM": "1"}

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.upstream_work, stderr=subprocess.DEVNULL,
                                           env=env).decode().strip()

        (self.upstream_work / "link").symlink_to("app-version.txt")
        git("add", "link")
        git("commit", "-qm", "symlink")
        with_link = git("rev-parse", "HEAD")
        (self.upstream_work / "link").unlink()
        git("rm", "-q", "link")
        (self.upstream_work / "big.bin").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n")
        git("add", "big.bin")
        git("commit", "-qm", "lfs pointer")
        with_lfs = git("rev-parse", "HEAD")
        subprocess.check_call(["git", "push", "-q", str(self.base / "upstream" / "approved.git"), "HEAD:refs/heads/main"],
                              cwd=self.upstream_work, env=env, stderr=subprocess.DEVNULL)
        before = pfx.snapshot_tree(self.workspace)
        for label, sha in (("symlink", with_link), ("lfs", with_lfs)):
            with self.subTest(entry=label):
                destination = self.base / ("cand-" + label)
                with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
                    with self.assertRaises(pf.Failure) as caught:
                        self.c.materialize_source({"sha": sha}, destination)
                self.assertIn("unsupported source entry", str(caught.exception))
                self.assertFalse(destination.exists())
        self.assertEqual(pfx.snapshot_tree(self.workspace), before)
        self.assert_git_never_touched_workspace()

    def test_tracked_reserved_workspace_names_are_refused_before_export(self):
        """A12-R02: a commit that tracks a path the workspace manifest ignores (.env, node_modules, ...)
        is refused as a whole before anything is exported or deployed; provenance never skips it."""
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.base), "GIT_CONFIG_NOSYSTEM": "1"}

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.upstream_work, stderr=subprocess.DEVNULL,
                                           env=env).decode().strip()

        (self.upstream_work / ".env").write_text("POSTGRES_PASSWORD=tracked-secret\n")
        git("add", "-f", ".env")
        git("commit", "-qm", "tracked env")
        with_env = git("rev-parse", "HEAD")
        git("rm", "-q", "-f", ".env")
        nested = self.upstream_work / "nested" / "node_modules"
        nested.mkdir(parents=True)
        (nested / "tracked.js").write_text("module.exports = 1;\n")
        git("add", "-f", "nested/node_modules/tracked.js")
        git("commit", "-qm", "tracked node_modules")
        with_modules = git("rev-parse", "HEAD")
        subprocess.check_call(["git", "push", "-q", str(self.base / "upstream" / "approved.git"), "HEAD:refs/heads/main"],
                              cwd=self.upstream_work, env=env, stderr=subprocess.DEVNULL)
        before = pfx.snapshot_tree(self.workspace)
        for label, sha, path in (("env", with_env, ".env"), ("node_modules", with_modules, "nested/node_modules/tracked.js")):
            with self.subTest(entry=label):
                destination = self.base / ("cand-" + label)
                with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
                    with self.assertRaises(pf.Failure) as caught:
                        self.c.materialize_source({"sha": sha}, destination)
                    # The same commit can never be proven deployed either (fail closed, not False).
                    with self.assertRaisesRegex(pf.Failure, "reserved workspace artifact"):
                        self.c.prove_tree_commit(self.workspace, sha)
                self.assertIn("reserved workspace artifact name", str(caught.exception))
                self.assertIn(path, str(caught.exception))
                self.assertFalse(destination.exists())
                self.assertFalse(self.context.source_manifest_path.exists())
        self.assertEqual(pfx.snapshot_tree(self.workspace), before)
        self.assert_git_never_touched_workspace()
        # The clean first commit still exports; the ignore policy applies to untracked content only.
        with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
            self.c.materialize_source({"sha": self.first}, self.base / "cand-clean")
        self.assertTrue((self.base / "cand-clean" / "app-version.txt").is_file())

    def test_checkpoint_metadata_never_assigns_provenance_without_the_store(self):
        candidate = self.base / "candidate"
        with mock.patch.object(self.c, "compose", return_value=""), self.c.lock():
            self.assertFalse(self.c.prove_tree_commit(candidate, self.first))  # no store yet
            self.c.materialize_source({"sha": self.first}, candidate)
            self.assertTrue(self.c.prove_tree_commit(candidate, self.first))
            (candidate / "app-version.txt").write_text("edited")
            self.assertFalse(self.c.prove_tree_commit(candidate, self.first))
            self.assertFalse(self.c.prove_tree_commit(candidate, self.second))  # not in the store: not proven
            self.assertFalse(self.c.prove_tree_commit(candidate, "not-a-sha"))
        self.assert_git_never_touched_workspace()

    def test_permissions_publish_runs_no_git_on_the_workspace(self):
        calls = []
        original = pf.Controller.command

        def recorded(controller, argv, **kwargs):
            calls.append(list(map(str, argv)))
            return original(controller, argv, **kwargs)

        with mock.patch.object(pf.Controller, "command", recorded):
            code, out, err = run_main(["--instance", "staging", "permissions"], self.layout)
        self.assertEqual(code, 0, err)
        self.assertEqual(calls, [])
        self.assertFalse(self.marker.exists())
        config = (self.git_dir / "config").read_text()
        self.assertNotIn("sharedRepository", config)


# =============================================================================== A1-T07


@ROOT_REQUIRED
class ImportAndPluginInjection(Base):
    """A1-T07: PYTHONPATH/sitecustomize, executable symlink chains and Docker CLI plugin directories
    cannot inject code; an invalid ancestor or helper stops before any effect."""

    def setUp(self):
        super().setUp()
        self.marker_dir = plant_markers(self.base, self.marker)

    def test_installed_cli_ignores_python_and_plugin_injection(self):
        self.install({"docker": str(recording_tool(self.base, "docker"))})
        context, paths = self.instance()
        hostile = hostile_environment(self.base, self.marker_dir, self.marker)
        for arguments in (["--help"], ["instances"], ["--instance", "staging", "status"],
                          ["--instance", "staging", "doctor"], ["ps"]):
            with self.subTest(arguments=arguments):
                result = launcher_run(self.layout, arguments, env=hostile, cwd=self.base / "hostile-python")
                self.assertIn(result.returncode, (0, 1), result.stderr)
                self.assertFalse(self.marker.exists(), "injected Python/plugin/PATH code executed")
        seen = observed(self.base, "docker")
        self.assertIsNotNone(seen)
        self.assertEqual(seen["env"]["DOCKER_CONFIG"], str(context.docker_config_dir))
        self.assertEqual(seen["env"]["HOME"], str(context.home_dir))
        self.assertTrue((context.docker_config_dir / "cli-plugins").is_dir())
        self.assertEqual(os.listdir(context.docker_config_dir / "cli-plugins"), [])
        self.assertEqual((context.docker_config_dir / "config.json").read_bytes(), b"{}\n")

    def test_executable_symlink_chain_must_be_trusted_end_to_end(self):
        trusted = self.base / "trusted"
        trusted.mkdir()
        os.chmod(trusted, 0o755)
        target = marker_executable(trusted, "real-docker", self.marker)
        os.chmod(target, 0o755)
        link1 = trusted / "docker-link"
        link1.symlink_to(target)
        link2 = trusted / "docker"
        link2.symlink_to(link1)
        # A root-owned chain in root-owned, non-writable directories resolves.
        self.assertEqual(pf_runner.resolve_trusted_executable(str(link2)), str(target))
        self.assertFalse(self.marker.exists())
        # An editor-owned link anywhere in the chain is refused.
        os.lchown(link1, 65534, 65534)
        with self.assertRaisesRegex(pf_runner.RunnerError, "tool-untrusted-link"):
            pf_runner.resolve_trusted_executable(str(link2))
        os.lchown(link1, 0, 0)
        # A group/world-writable directory on the way is refused (an editor could replace entries).
        os.chmod(trusted, 0o775)
        with self.assertRaisesRegex(pf_runner.RunnerError, "tool-ancestor-replaceable"):
            pf_runner.resolve_trusted_executable(str(link2))
        os.chmod(trusted, 0o755)
        # A writable or editor-owned target is refused.
        os.chmod(target, 0o777)
        with self.assertRaisesRegex(pf_runner.RunnerError, "tool-writable"):
            pf_runner.resolve_trusted_executable(str(link2))
        os.chmod(target, 0o755)
        os.chown(target, 65534, 65534)
        with self.assertRaisesRegex(pf_runner.RunnerError, "tool-untrusted-owner"):
            pf_runner.resolve_trusted_executable(str(link2))
        os.chown(target, 0, 0)
        for text in ("relative/docker", "/trusted/../docker", "//trusted/docker", "/trusted/docker/"):
            with self.assertRaisesRegex(pf_runner.RunnerError, "tool-path-noncanonical"):
                pf_runner.resolve_trusted_executable(text)
        with self.assertRaisesRegex(pf_runner.RunnerError, "tool-missing"):
            pf_runner.resolve_trusted_executable(str(trusted / "absent"))
        # A packaged layout with a relative link through '..' stays inside the trusted tree.
        (trusted / "lib").mkdir()
        os.chmod(trusted / "lib", 0o755)
        real = marker_executable(trusted / "lib", "docker-real", self.marker)
        (trusted / "bin").mkdir()
        os.chmod(trusted / "bin", 0o755)
        (trusted / "bin" / "docker").symlink_to("../lib/docker-real")
        self.assertEqual(pf_runner.resolve_trusted_executable(str(trusted / "bin" / "docker")), str(real))
        escaping = trusted / "bin" / "escape"
        escaping.symlink_to("../../../../../../../../bin/sh")
        with self.assertRaisesRegex(pf_runner.RunnerError, "tool-link-chain"):
            pf_runner.resolve_trusted_executable(str(escaping))
        self.assertFalse(self.marker.exists())

    def test_invalid_registered_helper_stops_before_effects(self):
        trusted = self.base / "trusted"
        trusted.mkdir()
        os.chmod(trusted, 0o775)  # editor-replaceable directory: the registered docker is refused
        docker = marker_executable(trusted, "docker", self.marker)
        self.install({"docker": str(docker)})
        context, paths = self.instance()
        before = pfx.snapshot_tree(self.base / "staging")
        code, out, err = run_main(["--instance", "staging", "doctor"], self.layout)
        self.assertEqual(code, 1)
        self.assertIn("docker=REFUSED (tool-ancestor-replaceable)", out)
        self.assertIn("Docker: unavailable: tool-ancestor-replaceable", out)
        self.assertFalse(self.marker.exists(), "refused helper was executed")
        self.assertEqual(pfx.snapshot_tree(self.base / "staging"), before)
        # Mutation through the launcher stops at the same boundary.
        result = launcher_run(self.layout, ["--instance", "staging", "backup"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("tool-ancestor-replaceable", result.stderr)
        self.assertFalse(self.marker.exists())

    def test_tampered_tools_registration_is_refused_by_the_bootstrap_verifier(self):
        self.install({"docker": str(recording_tool(self.base, "docker"))})
        self.instance()
        conf = self.layout.tools_conf
        original = conf.read_bytes()
        for label, mutate in (
            ("writable", lambda: os.chmod(conf, 0o666)),
            ("unknown key", lambda: conf.write_bytes(original + b"shell=/bin/sh\n")),
            ("relative path", lambda: conf.write_bytes(b"docker=bin/docker\n")),
            ("symlink", lambda: (conf.rename(self.base / "tools.conf"), conf.symlink_to(self.base / "tools.conf"))),
        ):
            with self.subTest(tamper=label):
                mutate()
                result = launcher_run(self.layout, ["--instance", "staging", "status"])
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("No control code was executed", result.stderr)
                self.assertIn("tools", result.stderr)
                if conf.is_symlink():
                    conf.unlink()
                conf.write_bytes(original)
                os.chmod(conf, 0o600)
        result = launcher_run(self.layout, ["instances"])
        self.assertEqual(result.returncode, 0, result.stderr)


# =============================================================================== A1-T08


TRICKY_VALUES = (
    "p@ss:w/rd#1",
    "dollar$sign${NOT_EXPANDED}$$",
    'double"quote',
    "back\\slash\\in\\the\\middle",
    "space in value",
    "hash # inside",
    "unicode-ñ-日本",
    "percent%20encoded%zz",
    "=equals=and;semi",
    "tab\\tliteral-backslash-t",
)


class AppValuesRoundTrip(unittest.TestCase):
    """A1-T08: strict parsing, literal round-trip through the private snapshot and the encoded
    connection URL; unsupported forms are rejected before mutation and never regenerated."""

    def values(self, password):
        return {
            "POSTGRES_USER": "partflow_staging", "POSTGRES_PASSWORD": password, "POSTGRES_DB": "partflow_staging",
            "SITE_TIMEZONE": "America/Los_Angeles", "PARTFLOW_BIND_IP": "127.0.0.1",
            "PARTFLOW_HTTP_PORT": "5173", "PARTFLOW_ALLOWED_HOST": "localhost",
        }

    def test_snapshot_render_and_parse_are_literal_for_every_supported_form(self):
        for password in TRICKY_VALUES:
            with self.subTest(password=password):
                values = self.values(password)
                rendered = pf_config.render_app_env(values)
                self.assertIn(b"POSTGRES_PASSWORD='" + password.encode("utf-8") + b"'", rendered)
                self.assertEqual(pf_config.parse_app_env(rendered, label="snapshot"), values)
                url = pf_config.database_url(values["POSTGRES_USER"], password, values["POSTGRES_DB"])
                parts = urllib.parse.urlsplit(url)
                self.assertEqual(urllib.parse.unquote(parts.password), password)
                self.assertEqual(urllib.parse.unquote(parts.username), "partflow_staging")
                self.assertEqual(parts.hostname, "db")
                self.assertEqual(parts.port, 5432)
                if any(ch in password for ch in "@:/#$\"\\ %;="):
                    self.assertNotIn(password, url)

    def test_editable_file_forms_parse_literally_without_expansion(self):
        cases = {
            b"POSTGRES_PASSWORD=$literal${X}\n": "$literal${X}",
            b"POSTGRES_PASSWORD='p@ss:w/rd#1 \"q\" \\x'\n": 'p@ss:w/rd#1 "q" \\x',
            b'POSTGRES_PASSWORD="a\\"b\\\\c$d"\n': 'a"b\\c$d',
            b"POSTGRES_PASSWORD='hash # inside'  # comment\n": "hash # inside",
            b"POSTGRES_PASSWORD=crlf-value\r\n": "crlf-value",
            b"  # comment line\n\nPOSTGRES_PASSWORD=x\n": "x",
        }
        base = self.values("x")
        for text, expected in cases.items():
            with self.subTest(text=text):
                data = b"".join(f"{k}={v}\n".encode() for k, v in base.items() if k != "POSTGRES_PASSWORD") + text
                self.assertEqual(pf_config.parse_app_env(data, label="t")["POSTGRES_PASSWORD"], expected)

    def test_unsupported_forms_are_rejected_before_any_mutation(self):
        base = b"".join(f"{k}={v}\n".encode() for k, v in self.values("x").items())
        rejected = {
            "duplicate key": base + b"POSTGRES_DB=other\n",
            "unknown key": base + b"DOCKER_HOST=tcp://evil\n",
            "host-reserved key": base + b"PARTFLOW_REPO_ROOT=/evil\n",
            "export prefix": base.replace(b"POSTGRES_DB=", b"export POSTGRES_DB="),
            "whitespace around =": base.replace(b"POSTGRES_DB=", b"POSTGRES_DB = "),
            "unterminated quote": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD='x"),
            "text after quote": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD='x'y"),
            "unquoted space": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD=a b"),
            "unquoted hash": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD=a#b"),
            "control character": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD='a\x01b'"),
            "bare carriage return": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD=a\rb"),
            "multiline": base.replace(b"POSTGRES_PASSWORD=x", b"POSTGRES_PASSWORD='a\nb'"),
            "unsupported escape": base.replace(b"POSTGRES_PASSWORD=x", b'POSTGRES_PASSWORD="a\\nb"'),
            "missing key": base.replace(b"POSTGRES_DB=partflow_staging\n", b""),
            "NUL": base + b"\x00",
            "not utf-8": base + b"\xff\n",
        }
        for label, data in rejected.items():
            with self.subTest(rejected=label):
                with self.assertRaises(pf_config.ConfigError) as caught:
                    pf_config.parse_app_env(data, label="t")
                self.assertNotIn("evil", str(caught.exception).replace("DOCKER_HOST", ""))

    def test_values_without_a_literal_rendering_are_an_explicit_migration_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            for password in ("it's", "trailing\\"):
                with self.subTest(password=password):
                    values = self.values(password)
                    self.assertIsNotNone(pf_config.value_render_issue(password))
                    with self.assertRaises(pf_config.ConfigError) as caught:
                        pf_config.freeze_app_config(values, source_bytes=b"", operation_id="op",
                                                    operation_dir=Path(tmp))
                    self.assertIn("migration-issue", str(caught.exception))
                    self.assertNotIn(password, str(caught.exception))
                    self.assertEqual(os.listdir(tmp), [], "a refused freeze wrote something")

    def test_frozen_snapshot_is_private_immutable_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = self.values(TRICKY_VALUES[0])
            frozen = pf_config.freeze_app_config(values, source_bytes=b"source", operation_id="op",
                                                 operation_dir=Path(tmp))
            self.assertEqual(stat.S_IMODE(frozen.env_file.stat().st_mode), 0o400)
            self.assertEqual(dict(frozen.values), values)
            with self.assertRaises(TypeError):
                frozen.values["POSTGRES_PASSWORD"] = "changed"
            record = json.loads((Path(tmp) / "frozen-config.json").read_text())
            self.assertEqual(record["env_file_sha256"], frozen.env_sha256)
            self.assertNotIn(TRICKY_VALUES[0], json.dumps(record))
            pf_config.verify_frozen(frozen)
            os.chmod(frozen.env_file, 0o600)
            frozen.env_file.write_bytes(frozen.env_file.read_bytes().replace(b"5173", b"5174"))
            with self.assertRaisesRegex(pf_config.ConfigError, "changed on disk"):
                pf_config.verify_frozen(frozen)
            with self.assertRaisesRegex(pf_config.ConfigError, "already exists"):
                pf_config.freeze_app_config(values, source_bytes=b"", operation_id="op", operation_dir=Path(tmp))

    def test_redactor_hides_secrets_and_their_encoded_form_in_bounded_output(self):
        redactor = pf_runner.Redactor([SECRET])
        encoded = urllib.parse.quote(SECRET, safe="")
        text = f"url=postgresql+psycopg://u:{encoded}@db/x raw={SECRET} tail"
        self.assertNotIn(SECRET, redactor.text(text))
        self.assertNotIn(encoded, redactor.text(text))
        self.assertIn("[REDACTED]", redactor.text(text))
        sink = io.StringIO()
        stream = pf_runner._Stream(redactor, sink, 10 ** 6)
        payload = ("line " + SECRET + "\n").encode("utf-8")
        for index in range(0, len(payload), 7):  # secret split across reads
            stream.feed(payload[index:index + 7])
        stream.close()
        self.assertNotIn(SECRET, sink.getvalue())
        self.assertIn("[REDACTED]", sink.getvalue())
        # A line longer than the flush threshold with the secret right at the retained-tail
        # boundary, and a secret that arrives in two pieces after the flush.
        for offset in range(0, len(SECRET) + 2):
            sink = io.StringIO()
            stream = pf_runner._Stream(redactor, sink, 10 ** 6)
            stream.feed(b"x" * (64 * 1024 + 1 + offset) + SECRET.encode("utf-8") + b"yy")
            stream.feed(b" more\n")
            stream.close()
            self.assertNotIn(SECRET, sink.getvalue(), offset)
        sink = io.StringIO()
        stream = pf_runner._Stream(redactor, sink, 10 ** 6)
        stream.feed(b"x" * (64 * 1024 + 1) + SECRET[:5].encode("utf-8"))
        stream.feed(SECRET[5:].encode("utf-8") + b"\n")
        stream.close()
        self.assertNotIn(SECRET, sink.getvalue())
        # A bounded capture cut in the middle of the secret keeps no unredactable prefix.
        capture = pf_runner._Capture(100)
        capture.feed(b"a" * 90 + SECRET.encode("utf-8") + b"tail")
        kept = redactor.data(capture.data(redactor.longest))
        self.assertTrue(capture.truncated)
        self.assertNotIn(SECRET[:5].encode("utf-8"), kept)


@ROOT_REQUIRED
class SecretsNeverReachLogs(Base):
    """A1-T08 (controller level): a failing tool that echoes the secret cannot leak it."""

    def test_failure_messages_and_captures_are_redacted(self):
        docker = recording_tool(self.base, "docker", extra="echo \"leak=$POSTGRES_PASSWORD $PARTFLOW_DATABASE_URL\"\n"
                                                          "echo \"err=$POSTGRES_PASSWORD\" >&2\n")
        self.install({"docker": str(docker)})
        (self.base / "staging").mkdir()
        paths = pfx.data_home(self.base / "staging", project="partflow-staging", group=GROUP, env=False)
        (paths["configuration"] / ".env").write_bytes(
            b"POSTGRES_DB=partflow_staging\nPOSTGRES_USER=partflow_staging\n"
            + b"POSTGRES_PASSWORD='" + SECRET.encode() + b"'\nSITE_TIMEZONE=America/Los_Angeles\n"
            b"PARTFLOW_BIND_IP=127.0.0.1\nPARTFLOW_HTTP_PORT=5173\nPARTFLOW_ALLOWED_HOST=localhost\n")
        context = pfx.register(self.layout, "staging", paths, project="partflow-staging")
        pfx.deployed_record(context)
        controller = self.controller(context)
        controller.cli = ["docker", "compose"]
        with self.assertRaises(pf.Failure) as caught:
            controller.compose("ps")
        message = str(caught.exception)
        self.assertNotIn(SECRET, message)
        self.assertNotIn(urllib.parse.quote(SECRET, safe=""), message)
        self.assertIn("[REDACTED]", message)
        result = controller.runner.history[-1]
        self.assertNotIn(SECRET, result.stdout + result.stderr + " ".join(result.argv))
        code, out, err = run_main(["--instance", "staging", "status"], self.layout)
        self.assertEqual(code, 1)
        self.assertNotIn(SECRET, out + err)
        self.assertNotIn(urllib.parse.quote(SECRET, safe=""), out + err)


# =============================================================================== A1-T09


@ROOT_REQUIRED
class TimeoutAndCancellation(Base):
    """A1-T09: a timed-out child and its descendants are terminated as a process group, output is
    bounded and redacted, and the unresolved external effect is recorded before any retry.

    Audit A12-R01: the effect is produced by the production wrappers (``compose()``,
    ``docker()``, ``sql()``, ``store_git()``); no test passes ``effect=`` by hand."""

    def setUp(self):
        super().setUp()
        self.pidfile = self.base / "descendant.pid"
        body = (
            "#!/bin/sh\n"
            "if [ \"$1\" = compose ] && [ \"$2\" = version ]; then echo 'Docker Compose version v2-fixture'; exit 0; fi\n"
            f"sleep 300 &\necho $! > {self.pidfile}\n"
            # The application secret reaches the child only as an allowlisted variable; the
            # child echoes it back on both streams like a chatty tool would.
            "echo \"starting with secret ${POSTGRES_PASSWORD:-" + SECRET + "}\"\n"
            "i=0\nwhile [ $i -lt 400 ]; do printf '%s\\n' 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'; i=$((i+1)); done\n"
            "echo \"stderr sees ${POSTGRES_PASSWORD:-" + SECRET + "} too\" >&2\n"
            "exec sleep 300\n"
        )
        self.tool = pfx.tool_script(self.base / "tools", "docker", body)
        self.install({"docker": str(self.tool), "git": str(self.tool)})
        self.context, self.paths = self.instance()
        self.password = dict(line.split("=", 1) for line in pfx.ENV_TEXT.splitlines())["POSTGRES_PASSWORD"]

    @staticmethod
    def alive(pid):
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                return handle.read().rsplit(b")", 1)[-1].split()[0] != b"Z"
        except OSError:
            return False

    def wait_descendant_dead(self):
        descendant = int(self.pidfile.read_text().strip())
        deadline = time.monotonic() + 5
        while self.alive(descendant) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.alive(descendant), "descendant survived the process-group termination")

    def effects(self, controller):
        return pf_runner.load_unresolved_effects(controller.operation_dir / "unresolved-effects.json")

    def test_timeout_terminates_the_process_group_bounds_output_and_records_the_effect(self):
        controller = self.controller(self.context)
        started = time.monotonic()
        with controller.lock():
            with self.assertRaises(pf.Failure) as caught:
                controller.compose("up", "-d", "--no-deps", "db", timeout=2)
            records = self.effects(controller)
            operation_id = controller.operation_id
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 30)
        self.assertIn("timed out", str(caught.exception))
        self.assertNotIn(self.password, str(caught.exception))
        result = controller.runner.history[-1]
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.returncode)
        self.assertNotIn(self.password, result.stdout + result.stderr)
        self.assertIn("[REDACTED]", result.stderr)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), pf_runner.DEFAULT_CAPTURE_LIMIT + len("[REDACTED]"))
        self.wait_descendant_dead()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(pf_runner.group_members(record["process_group"]), [])
        self.assertEqual(record["outcome"], "timeout")
        self.assertEqual(record["effect"], {"kind": "compose", "verb": "up", "project": "partflow-staging",
                                            "targets": ["db"]})
        self.assertEqual(record["argv"][0], "compose")
        self.assertEqual(record["argv"][-4:], ["up", "-d", "--no-deps", "db"])
        self.assertEqual(record["id"], result.effect_id)
        self.assertNotIn(self.password, json.dumps(records))
        # The operation directory is the journal location, and status reads it from there.
        self.assertTrue((self.context.operations_dir / operation_id / "unresolved-effects.json").is_file())

    def test_read_only_timeouts_record_no_effect(self):
        controller = self.controller(self.context)
        with controller.lock():
            for label, call in (
                ("compose ps", lambda: controller.compose("ps", timeout=2)),
                ("compose config", lambda: controller.compose("config", "-q", timeout=2)),
                ("docker ps", lambda: controller.docker("ps", "-a", "-q", timeout=2)),
                ("docker image inspect", lambda: controller.docker("image", "inspect", "x:y", timeout=2)),
                ("git rev-parse", lambda: controller.store_git(["--git-dir=/nonexistent", "rev-parse", "--verify",
                                                                "HEAD"], cwd=self.base, timeout=2)),
            ):
                with self.subTest(call=label):
                    with self.assertRaisesRegex(pf.Failure, "timed out"):
                        call()
                    self.assertIsNone(controller.runner.history[-1].effect_id)
            self.assertFalse((controller.operation_dir / "unresolved-effects.json").exists())
            # Mutations after the read-only calls are journaled in the same operation; the `run`
            # descriptor comes from the caller's arguments, not from the managed label.
            with self.assertRaisesRegex(pf.Failure, "timed out"):
                controller.docker("volume", "rm", "partflow-staging_pgdata", timeout=2)
            with self.assertRaisesRegex(pf.Failure, "timed out"):
                controller.compose("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade",
                                   "head", timeout=2)
            records = self.effects(controller)
        self.assertEqual([item["effect"] for item in records],
                         [{"kind": "docker", "verb": "volume rm", "targets": ["partflow-staging_pgdata"]},
                          {"kind": "compose", "verb": "run", "project": "partflow-staging",
                           "targets": ["backend", "uv", "run", "alembic", "upgrade", "head"]}])
        self.assertIn("--label", records[1]["argv"])

    def test_mutations_outside_a_locked_operation_are_refused_before_the_child_starts(self):
        """Fail closed: without an operation there is no journal, so a mutating child never starts."""
        controller = self.controller(self.context)
        for label, call in (
            ("compose up", lambda: controller.compose("up", "-d", "--no-deps", "db", timeout=2)),
            ("docker tag", lambda: controller.docker("tag", "sha256:x", "r:t", timeout=2)),
            ("store fetch", lambda: controller.store_git(["--git-dir=/nonexistent", "fetch", "approved", "x"],
                                                        cwd=self.base, timeout=2)),
        ):
            with self.subTest(call=label):
                with self.assertRaisesRegex(pf.Failure, "requires a locked operation"):
                    call()
        self.assertFalse(self.pidfile.exists(), "a mutating child ran without an operation")
        self.assertEqual([r for r in controller.runner.history if r.effect_id], [])
        # Read-only diagnostics still run (and still time out) without an operation.
        with self.assertRaisesRegex(pf.Failure, "timed out"):
            controller.docker("ps", "-q", timeout=2)
        self.assertFalse(list(self.context.operations_dir.glob("*/unresolved-effects.json")))

    def test_sql_wrapper_journals_only_statements_declared_as_mutations(self):
        controller = self.controller(self.context)
        with controller.lock(), mock.patch.object(controller, "compose", return_value="t") as compose:
            for query in ("SELECT 1;", "SHOW server_version_num;", "  select datname FROM pg_database;"):
                controller.sql("postgres", query)
                self.assertIsNone(compose.call_args.kwargs["effect"], query)
            # Anything that does not start with SELECT/SHOW is a mutation by default (fail closed;
            # a CTE query is over-recorded rather than a DDL statement being missed)...
            for statement in ('ALTER DATABASE "pf_x" ALLOW_CONNECTIONS false;', "BEGIN; SET LOCAL lock_timeout = '10s'; COMMIT;",
                              "CREATE DATABASE pf_verify_x;", "",
                              "WITH x AS (SELECT 1 AS n) SELECT n FROM x;"):
                controller.sql("postgres", statement)
                self.assertEqual(compose.call_args.kwargs["effect"]["kind"], "database", statement)
            # ...and the caller's explicit classification wins.
            controller.sql("postgres", "SELECT pg_terminate_backend(1);", mutation=True)
            self.assertEqual(compose.call_args.kwargs["effect"]["verb"], "sql")
            controller.sql("postgres", 'ALTER DATABASE "pf_x" ALLOW_CONNECTIONS false;', mutation=True)
            self.assertEqual(compose.call_args.kwargs["effect"],
                             {"kind": "database", "verb": "sql", "database": "postgres",
                              "statement": 'ALTER DATABASE "pf_x" ALLOW_CONNECTIONS false;'})
            argv = compose.call_args.args
            self.assertEqual(argv[:4], ("exec", "-T", "db", "psql"))
            self.assertNotIn("sh", argv)
            self.assertIn(("-U", "partflow_staging"), [argv[i:i + 2] for i in range(len(argv))])
        # Without an explicit descriptor, an exec of psql is a mutation (fail-closed), and the
        # production classification tables cover every verb the control plane issues.
        project = "partflow-staging"
        self.assertEqual(pf.compose_effect(project, ("exec", "-T", "db", "psql", "-c", "SELECT 1"))["verb"], "psql")
        for read_only in (("ps",), ("ps", "-a", "-q", "db"), ("config", "-q"), ("logs", "db"), ("version",),
                          ("exec", "-T", "db", "pg_dump", "-U", "u", "-d", "d"),
                          ("exec", "-T", "db", "pg_dumpall", "-U", "u", "--globals-only"),
                          ("exec", "-T", "db", "pg_restore", "--list"),
                          ("exec", "-T", "frontend", "wget", "-q", "-O", "-", "http://localhost:5173/api/health")):
            self.assertIsNone(pf.compose_effect(project, read_only), read_only)
        for mutation, verb in ((("up", "-d", "--no-deps", "--no-build", "--force-recreate", "backend"), "up"),
                               (("down", "--volumes", "--remove-orphans"), "down"),
                               (("stop", "frontend", "backend"), "stop"),
                               (("build", "backend"), "build"),
                               (("run", "--rm", "--no-deps", "-T", "backend", "uv", "run", "alembic", "upgrade", "head"), "run"),
                               (("exec", "-T", "db", "createdb", "-U", "u", "--owner=u", "pf_verify_1"), "createdb"),
                               (("exec", "-T", "db", "dropdb", "-U", "u", "pf_verify_1"), "dropdb"),
                               (("exec", "-T", "db", "pg_restore", "-U", "u", "-d", "pf_verify_1", "--exit-on-error"), "pg_restore"),
                               (("exec", "-T", "db", "unknown-program"), "unknown-program"),
                               (("kill",), "kill")):
            effect = pf.compose_effect(project, mutation)
            self.assertEqual((effect["verb"], effect["project"]), (verb, project), mutation)
        self.assertEqual(pf.compose_effect(project, ("run", "--rm", "--no-deps", "-T", "backend", "uv", "run",
                                                     "alembic", "upgrade", "head"))["targets"],
                         ["backend", "uv", "run", "alembic", "upgrade", "head"])
        self.assertEqual(pf.compose_effect(project, ("exec", "-T", "db", "dropdb", "-U", "u", "pf_verify_1")),
                         {"kind": "compose-exec", "verb": "dropdb", "project": project, "service": "db",
                          "targets": ["u", "pf_verify_1"]})
        for read_only in (("ps", "-a", "-q"), ("inspect", "abc"), ("version", "--format", "x"),
                          ("image", "inspect", "r:t"), ("image", "ls"), ("volume", "ls"), ("network", "ls"),
                          ("compose", "version"), ("image", "save", "-o", "/x.tar", "r:t")):
            self.assertIsNone(pf.docker_effect(read_only), read_only)
        for mutation, verb in ((("tag", "sha256:x", "r:t"), "tag"), (("stop", "--time", "30", "job"), "stop"),
                               (("rm", "-f", "c"), "rm"), (("network", "rm", "n"), "network rm"),
                               (("volume", "rm", "v"), "volume rm"), (("image", "rm", "r:t"), "image rm"),
                               (("image", "load", "-i", "/x.tar"), "image load"), (("system", "prune"), "system")):
            self.assertEqual(pf.docker_effect(mutation)["verb"], verb, mutation)
        for read_only in (["--version"], ["--git-dir=/s", "rev-parse", "--verify", "x"], ["--git-dir=/s", "ls-tree", "-r", "x"],
                          ["--git-dir=/s", "cat-file", "--batch"], ["--git-dir=/s", "merge-base", "--is-ancestor", "a", "b"]):
            self.assertIsNone(pf.git_effect(read_only), read_only)
        for mutation, verb in ((["--git-dir=/s", "fetch", "-q", "approved", "sha"], "fetch"),
                               (["init", "--bare", "-q", "/s"], "init"),
                               (["--git-dir=/s", "update-ref", "refs/pinned/x", "x"], "update-ref"),
                               (["--git-dir=/s", "config", "--local", "k", "v"], "config")):
            self.assertEqual(pf.git_effect(mutation), {"kind": "source-store", "verb": verb,
                                                       "targets": pf.effect_targets(mutation[mutation.index(verb) + 1:])})

    def test_bounded_capture_with_small_limit_keeps_the_total_count(self):
        controller = self.controller(self.context)
        spec = pf_runner.ProcessSpec(tool="docker", argv=("x",), cwd=str(self.base), env=controller.runner.environment(),
                                     timeout=2, capture_limit=1024)
        result = controller.runner.run(spec)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 1024 + 32)

    def test_interruption_terminates_the_group_and_records_the_effect(self):
        controller = self.controller(self.context)

        def interrupted(process, *args, **kwargs):
            deadline = time.monotonic() + 5
            while not self.pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            for pipe in (process.stdout, process.stderr):
                pipe.close()
            raise KeyboardInterrupt("Interrupted by signal 15")

        controller.cli = ["docker", "compose"]  # the interrupted child is the stop itself, not the version probe
        with controller.lock():
            with mock.patch.object(controller.runner, "_pump", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    controller.compose("stop", "frontend", "backend", timeout=30)
            records = self.effects(controller)
        self.wait_descendant_dead()
        self.assertEqual([item["outcome"] for item in records], ["interrupted"])
        self.assertEqual(records[0]["effect"], {"kind": "compose", "verb": "stop", "project": "partflow-staging",
                                                "targets": ["frontend", "backend"]})
        self.assertNotIn(self.password, json.dumps(records))

    def test_status_reports_unresolved_effects_of_earlier_operations(self):
        controller = self.controller(self.context)
        with controller.lock():
            with self.assertRaises(pf.Failure):
                controller.compose("up", "-d", "--no-deps", "db", timeout=1)
            operation_dir = controller.operation_dir
        self.assertTrue((operation_dir / "unresolved-effects.json").is_file())
        with mock.patch.object(pf.Controller, "command", side_effect=pf.Failure("transport disabled by test")):
            code, out, err = run_main(["--instance", "staging", "status"], self.layout)
        self.assertIn("UNRESOLVED EFFECTS recorded by earlier operations: 1", out)
        self.assertIn("docker compose up db -> timeout", out)
        self.assertLess(out.index("UNRESOLVED EFFECTS"), out.index("unavailable"))
        self.assertNotIn(self.password, out)


@ROOT_REQUIRED
class RunnerInputOutput(Base):
    """Data paths of the runner: bytes/file stdin, file/captured stdout; nothing is captured twice."""

    def test_file_objects_paths_and_bytes_flow_through_the_boundary(self):
        tool = pfx.tool_script(self.base / "tools", "docker", "#!/bin/sh\ncat\n")
        self.install({"docker": str(tool)})
        context, paths = self.instance()
        controller = self.controller(context)
        payload = b"dump-bytes\n" * 5000
        out_path = self.base / "out-1.bin"
        with out_path.open("wb") as out:
            self.assertEqual(controller.command(["docker"], input_file=payload, output=out), "")
        self.assertEqual(out_path.read_bytes(), payload)
        in_path = self.base / "in.bin"
        in_path.write_bytes(payload)
        with in_path.open("rb") as stream, (self.base / "out-2.bin").open("wb") as out:
            controller.command(["docker"], input_file=stream, output=out)
        self.assertEqual((self.base / "out-2.bin").read_bytes(), payload)
        controller.command(["docker"], input_file=str(in_path), output=str(self.base / "out-3.bin"))
        self.assertEqual((self.base / "out-3.bin").read_bytes(), payload)
        self.assertEqual(controller.command(["docker"], input_file=b"hello"), "hello")
        for result in controller.runner.history[:3]:
            self.assertEqual(result.stdout, "")
        self.assertEqual(controller.runner.history[-1].stdout, "hello")

    def test_child_that_floods_stdout_before_reading_stdin_cannot_deadlock_the_runner(self):
        tool = pfx.tool_script(self.base / "tools", "docker",
                               "#!/bin/sh\nhead -c 300000 /dev/zero | tr '\\0' 'z'\ncat\n")
        self.install({"docker": str(tool)})
        context, paths = self.instance()
        controller = self.controller(context)
        payload = b"q" * 300000
        started = time.monotonic()
        output = controller.command(["docker"], input_file=payload, timeout=20)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(len(output), 600000)
        self.assertTrue(output.endswith("q" * 10))
        # An early-exiting child does not turn the stdin write into an exception.
        tool.write_text("#!/bin/sh\nexit 3\n")
        with self.assertRaisesRegex(pf.Failure, "exit 3"):
            controller.command(["docker"], input_file=payload, timeout=20)


# =============================================================================== A1-T10


@ROOT_REQUIRED
class ProvenanceWithoutGit(Base):
    """A1-T10: diagnostics compare the workspace with the protected manifest; no ``.git`` is needed
    and none is consulted; unproven trees are unknown provenance, never a fabricated commit."""

    def setUp(self):
        super().setUp()
        self.install({"docker": str(recording_tool(self.base, "docker")), "git": str(recording_tool(self.base, "git"))})
        self.context, self.paths = self.instance()
        self.workspace = self.paths["workspace"]
        self.c = self.controller(self.context)

    def test_manifest_comparison_is_the_only_workspace_status(self):
        status = self.c.workspace_status()
        self.assertEqual(status["provenance"], "unknown")
        self.assertIsNone(status["head"])
        self.assertTrue(status["dirty"])
        self.assertEqual(status["changes"], ["<no-protected-manifest>"])
        # A deployed tree records a protected manifest (here: the tree itself, as a deploy would).
        self.c.record_source_manifest(self.workspace, pfx.OLD, verified=True)
        manifest_path = self.context.source_manifest_path
        self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
        status = self.c.workspace_status()
        self.assertEqual(status, {"head": pfx.OLD, "dirty": False, "changes": [], "provenance": "git_commit",
                                  "manifest_commit": pfx.OLD})
        # Git metadata is data: adding or removing .git changes nothing, and no git ran.
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (self.workspace / ".git" / "config").write_text("[core]\n\tfsmonitor = /nonexistent\n")
        self.assertFalse(self.c.workspace_status()["dirty"])
        shutil.rmtree(self.workspace / ".git")
        self.assertFalse(self.c.workspace_status()["dirty"])
        self.assertIsNone(observed(self.base, "git"))
        # Byte, mode and inventory changes are reported without any Git.
        (self.workspace / "frontend" / "app.txt").write_text("edited")
        os.chmod(self.workspace / "app-version.txt", 0o755)
        (self.workspace / "new.txt").write_text("new")
        (self.workspace / "pf.sh").unlink()
        (self.workspace / "link").symlink_to("app-version.txt")
        status = self.c.workspace_status()
        self.assertTrue(status["dirty"])
        self.assertEqual(status["provenance"], "unknown")
        self.assertEqual(status["head"], pfx.OLD)  # the manifest's commit is still known; the workspace is not proven
        self.assertEqual(set(status["changes"]), {"changed:app-version.txt", "changed:frontend/app.txt",
                                                  "added:new.txt", "removed:pf.sh", "unsupported:link"})
        self.assertIsNone(observed(self.base, "git"))

    def test_status_and_doctor_work_without_git_and_never_run_git_on_the_workspace(self):
        self.c.record_source_manifest(self.workspace, pfx.OLD, verified=True)
        before = pfx.snapshot_tree(self.base / "staging")
        for command in ("status", "doctor"):
            with self.subTest(command=command):
                code, out, err = run_main(["--instance", "staging", command], self.layout)
                self.assertEqual(code, 1, err)  # fixture docker has no daemon
                self.assertIn("Instance: staging", out)
                if command == "status":
                    self.assertIn("Workspace: provenance git_commit | manifest commit " + pfx.OLD, out)
                    self.assertIn("Deployed source: " + pfx.OLD, out)
        seen = observed(self.base, "git")
        self.assertEqual(seen["argv"], ["--version"])  # doctor's version probe only
        self.assertNotEqual(seen["cwd"], str(self.workspace))
        self.assertEqual(pfx.snapshot_tree(self.base / "staging"), before)

    def test_unproven_tree_is_unknown_provenance_and_gets_no_commit(self):
        # A tree dropped in from a ZIP: no manifest, no .git.
        with self.assertRaises(pf.Failure) as caught:
            with self.c.lock():
                self.c.current_target(self.base / "candidate")
        self.assertIn("unknown provenance", str(caught.exception))
        self.assertNotRegex(str(caught.exception), r"\b[0-9a-f]{40}\b")
        self.assertFalse(self.context.source_manifest_path.exists())
        self.assertIsNone(observed(self.base, "git"))
        # A checkout whose HEAD claims a commit is only a hint: the store must prove it.
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "HEAD").write_text(pfx.NEW + "\n")
        self.c.remote_override = str(self.base / "no-such-remote.git")
        self.c.source_protocols = ("file",)
        with self.assertRaises(pf.Failure):
            with self.c.lock():
                self.c.current_target(self.base / "candidate-2")
        self.assertFalse(self.context.source_manifest_path.exists())
        self.assertFalse((self.context.state_dir / "deployed.json").exists() and
                         pf.load_json(self.context.state_dir / "deployed.json").get("sha") == pfx.NEW)
        seen = observed(self.base, "git")
        self.assertIsNotNone(seen)
        self.assertIn(str(self.layout.sources), " ".join(seen["argv"]))
        self.assertNotIn(str(self.workspace), " ".join(seen["argv"]))
        self.assertEqual(seen["cwd"], str(self.layout.sources))
        # Backups never claim a verified source they cannot reconstruct.
        (self.context.state_dir / "deployed.json").unlink()
        with self.assertRaises(pf.Failure):
            self.c.revision()
        with self.assertRaises(pf.Failure) as caught:
            self.c.create_deployed_source_archive(self.base / "x.tar.gz", pfx.OLD)
        self.assertIn("Cannot reconstruct", str(caught.exception))
        self.assertFalse((self.base / "x.tar.gz").exists())

    def test_deployed_source_archive_is_built_from_manifest_verified_bytes(self):
        self.c.record_source_manifest(self.workspace, pfx.OLD, verified=True)
        archive = self.base / "deployed.tar.gz"
        with self.c.lock():
            self.c.create_deployed_source_archive(archive, pfx.OLD)
        import tarfile
        with tarfile.open(archive) as tar:
            names = sorted(member.name for member in tar.getmembers())
            self.assertIn("frontend/app.txt", names)
            self.assertNotIn(".git/HEAD", names)
            self.assertEqual(tar.extractfile("app-version.txt").read(), pfx.OLD.encode())
        # A workspace that drifted from the manifest yields no archive at all.
        (self.workspace / "frontend" / "app.txt").write_text("edited")
        with self.c.lock():
            with self.assertRaisesRegex(pf.Failure, "differs from the manifest"):
                self.c.create_deployed_source_archive(self.base / "second.tar.gz", pfx.OLD)
        self.assertFalse((self.base / "second.tar.gz").exists())
        (self.workspace / "frontend" / "app.txt").write_text(pfx.OLD)
        (self.workspace / "extra.txt").write_text("x")
        with self.c.lock():
            with self.assertRaisesRegex(pf.Failure, "manifest does not"):
                self.c.create_deployed_source_archive(self.base / "third.tar.gz", pfx.OLD)
        (self.workspace / "extra.txt").unlink()
        with self.c.lock():
            with self.assertRaisesRegex(pf.Failure, "Cannot reconstruct"):
                self.c.create_deployed_source_archive(self.base / "fourth.tar.gz", pfx.NEW)
        self.assertIsNone(observed(self.base, "git"))

    def test_manifest_walk_refuses_swapped_directories_and_bounded_hints(self):
        self.c.record_source_manifest(self.workspace, pfx.OLD, verified=True)
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside\n")
        shutil.rmtree(self.workspace / "frontend")
        (self.workspace / "frontend").symlink_to(outside)
        status = self.c.workspace_status()
        self.assertIn("unsupported:frontend", status["changes"])
        self.assertNotIn("added:frontend/secret.txt", status["changes"])
        (self.workspace / "frontend").unlink()
        (self.workspace / "frontend").mkdir()
        (self.workspace / "frontend" / "app.txt").write_text(pfx.OLD)
        self.assertFalse(self.c.workspace_status()["dirty"])
        git_dir = self.workspace / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").symlink_to("/dev/zero")
        self.assertIsNone(pf_source.workspace_head_hint(self.workspace))
        (git_dir / "HEAD").unlink()
        (git_dir / "HEAD").write_bytes(b"ref: refs/heads/main\n")
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "refs" / "heads" / "main").write_bytes(b"x" * (pf_source.HINT_READ_LIMIT + 10))
        self.assertIsNone(pf_source.workspace_head_hint(self.workspace))
        (git_dir / "refs" / "heads" / "main").write_bytes(pfx.NEW.encode() + b"\n")
        self.assertEqual(pf_source.workspace_head_hint(self.workspace), pfx.NEW)
        shutil.rmtree(git_dir)
        (self.workspace / ".git").symlink_to(outside)
        self.assertIsNone(pf_source.workspace_head_hint(self.workspace))

    def test_tracked_content_is_never_outside_the_manifest_proof(self):
        """A12-R02 without Git: a candidate that carries a file the manifest policy ignores is refused
        before the workspace changes; a manifest that lists such a path cannot be verified; untracked
        artifacts with those names in the workspace remain ignored."""
        self.c.record_source_manifest(self.workspace, pfx.OLD, verified=True)
        # Untracked runtime artifacts (not in the manifest) are ignored by policy.
        (self.workspace / ".env").write_text("POSTGRES_PASSWORD=untracked\n")
        (self.workspace / "frontend" / "node_modules").mkdir()
        (self.workspace / "frontend" / "node_modules" / "x.js").write_text("x")
        self.assertFalse(self.c.workspace_status()["dirty"])
        (self.workspace / ".env").unlink()
        shutil.rmtree(self.workspace / "frontend" / "node_modules")
        # A candidate tree (what a deploy would copy whole) with tracked reserved names is refused.
        def candidate_with(relative):
            candidate = self.base / ("candidate-" + relative.replace("/", "-") + "-" + uuid.uuid4().hex[:4])
            pfx.source_fixture(candidate, pfx.NEW)
            target = candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("tracked content\n")
            self.assertEqual(pf_source.reserved_paths(candidate), [relative])
            return candidate

        before = pfx.snapshot_tree(self.workspace)
        manifest_before = self.context.source_manifest_path.read_bytes()
        for relative in (".env", "nested/node_modules/tracked.js"):
            with self.subTest(candidate_entry=relative):
                candidate = candidate_with(relative)
                with self.c.lock():
                    pf.write_json(self.c.pending, {"operation": "update", "phase": "x"})
                    with self.assertRaisesRegex(pf.Failure, "cannot verify"):
                        self.c.replace_source(candidate, pfx.NEW)
                    self.c.pending.unlink()
                self.assertEqual(pfx.snapshot_tree(self.workspace), before)
                self.assertEqual(self.context.source_manifest_path.read_bytes(), manifest_before)
        with self.c.lock():
            with self.assertRaisesRegex(pf.Failure, "cannot verify"):
                self.c.replace_source_for_recovery(candidate_with("nested/node_modules/tracked.js"), pfx.NEW)
        self.assertEqual(pfx.snapshot_tree(self.workspace), before)
        self.assertEqual(self.context.source_manifest_path.read_bytes(), manifest_before)
        # A v1 bundle's top-level runtime .env is stripped from the candidate first (it is runtime
        # configuration, never source), so the restored workspace and its manifest carry no .env.
        with self.c.lock():
            self.c.replace_source_for_recovery(candidate_with(".env"), pfx.NEW, verified=False)
        self.assertFalse((self.workspace / ".env").exists())
        self.assertEqual(self.c.workspace_status()["provenance"], "unknown")
        self.assertFalse(self.c.workspace_status()["dirty"])
        # A manifest listing a reserved path can never be proven by the walk: comparison fails closed.
        manifest = pf_source.load_manifest(self.context.source_manifest_path, pf_instance.parse_strict_json)
        entry = dict(next(e for e in manifest["entries"] if e["kind"] == "file"), path="nested/node_modules/tracked.js")
        manifest["entries"].append(entry)
        with self.assertRaisesRegex(pf_source.SourceError, "cannot be verified"):
            pf_source.compare_manifest(self.workspace, manifest)
        pf_source.write_manifest(self.context.source_manifest_path, manifest)
        with self.assertRaisesRegex(pf.Failure, "cannot be verified"):
            self.c.workspace_status()
        # Changing the bytes of such a file is therefore never reported as a match either way.
        (self.workspace / "nested" / "node_modules").mkdir(parents=True)
        (self.workspace / "nested" / "node_modules" / "tracked.js").write_text("changed")
        with self.assertRaises(pf.Failure):
            self.c.workspace_status()

    def test_replace_source_records_the_candidate_manifest_and_refuses_links(self):
        candidate = self.base / "candidate"
        pfx.source_fixture(candidate, pfx.NEW)
        with self.c.lock():
            pf.write_json(self.c.pending, {"operation": "update", "phase": "x"})
            self.c.replace_source(candidate, pfx.NEW)
            self.c.pending.unlink()
        status = self.c.workspace_status()
        self.assertEqual((status["head"], status["dirty"], status["provenance"]), (pfx.NEW, False, "git_commit"))
        manifest = pf_source.load_manifest(self.context.source_manifest_path, pf_instance.parse_strict_json)
        self.assertEqual(manifest["source"]["commit"], pfx.NEW)
        self.assertTrue(all(entry["kind"] == "file" for entry in manifest["entries"]))
        (candidate / "evil-link").symlink_to("/etc/passwd")
        before = pfx.snapshot_tree(self.workspace)
        with self.c.lock():
            pf.write_json(self.c.pending, {"operation": "update", "phase": "x"})
            with self.assertRaises(pf.Failure):
                self.c.replace_source(candidate, pfx.NEW)
            self.c.pending.unlink()
        self.assertEqual(pfx.snapshot_tree(self.workspace), before)


# =============================================================================== A1-T18


@ROOT_REQUIRED
class FrozenAuthority(Base):
    """A1-T18: an operation consumes the frozen approved bytes; edits to app config, policy or
    record after approval either leave the operation on the frozen values or stop it."""

    def setUp(self):
        super().setUp()
        self.install({"docker": str(recording_tool(self.base, "docker"))})
        self.context, self.paths = self.instance()
        self.env_path = self.paths["configuration"] / ".env"

    def test_operation_uses_the_frozen_snapshot_after_the_editable_file_changes(self):
        controller = self.controller(self.context)
        controller.cli = ["docker", "compose"]
        original = self.env_path.read_bytes()
        with controller.lock():
            frozen = controller.frozen
            self.assertIsNotNone(frozen)
            self.assertEqual(frozen.env_file.parent, controller.operation_dir)
            self.assertEqual(stat.S_IMODE(frozen.env_file.stat().st_mode), 0o400)
            self.assertEqual(frozen.source_sha256, pf_instance.sha256_bytes(original))
            operation = json.loads((controller.operation_dir / "operation.json").read_text())
            self.assertEqual(operation["record_sha256"], self.context.record_sha256)
            self.assertEqual(operation["policy_sha256"], self.context.approved_policy.sha256)
            # An editor changes the proposal mid-operation.
            self.env_path.write_bytes(original.replace(b"abc123", b"changed-secret-1"))
            with self.assertRaises(pf.Failure):
                controller.compose("ps")  # fixture docker exits 1, but it saw the frozen values
            seen = observed(self.base, "docker")
            self.assertEqual(seen["env"]["POSTGRES_PASSWORD"], "abc123")
            self.assertEqual(seen["argv"][seen["argv"].index("--env-file") + 1], str(frozen.env_file))
            self.assertEqual(controller.env()["POSTGRES_PASSWORD"], "abc123")
            with self.assertRaisesRegex(pf.Failure, "changed after this operation froze it"):
                controller.freeze_app_config()
            # The snapshot itself is verified before every Compose invocation.
            os.chmod(frozen.env_file, 0o600)
            frozen.env_file.write_bytes(frozen.env_file.read_bytes().replace(b"abc123", b"tampered-snap"))
            with self.assertRaisesRegex(pf.Failure, "changed on disk"):
                controller.compose("ps")
        self.assertIsNone(controller.frozen)
        # A new operation approves the new proposal explicitly, in a new private snapshot.
        with controller.lock():
            self.assertEqual(controller.frozen.values["POSTGRES_PASSWORD"], "changed-secret-1")
            self.assertNotEqual(controller.frozen.env_file, frozen.env_file)

    def test_deploy_wizard_reuses_the_frozen_values_not_a_later_edit(self):
        controller = self.controller(self.context)
        original = self.env_path.read_bytes()
        with mock.patch.object(pf, "prompt_yes_no", return_value=True), controller.lock():
            self.env_path.write_bytes(original.replace(b"partflow_staging\nPOSTGRES_PASSWORD",
                                                       b"partflow_edited\nPOSTGRES_PASSWORD"))
            with self.assertRaisesRegex(pf.Failure, "too short"):
                controller.prepare_new_env()  # frozen abc123 is < 32 characters: refused on the frozen values
        strong = original.replace(b"abc123", b"f" * 64)
        self.env_path.write_bytes(strong)
        with mock.patch.object(pf, "prompt_yes_no", return_value=True), controller.lock():
            self.env_path.write_bytes(strong.replace(b"partflow_staging\nPOSTGRES_PASSWORD",
                                                     b"partflow_edited\nPOSTGRES_PASSWORD"))
            with self.assertRaisesRegex(pf.Failure, "changed after this operation froze it"):
                controller.prepare_new_env()
        self.env_path.write_bytes(strong)
        with mock.patch.object(pf, "prompt_yes_no", return_value=True), controller.lock():
            values = controller.prepare_new_env()
            self.assertEqual(values["POSTGRES_DB"], "partflow_staging")
            self.assertEqual(values, dict(controller.frozen.values))

    def test_raw_compose_config_is_not_available_through_the_passthrough(self):
        code, out, err = run_main(["config"], self.layout)
        self.assertEqual(code, 1)
        self.assertIn("not available through this route", err)
        self.assertIsNone(observed(self.base, "docker"))
        code, out, err = run_main(["config", "-o", str(self.base / "dump.yaml")], self.layout)
        self.assertEqual(code, 1)
        self.assertFalse((self.base / "dump.yaml").exists())

    def test_read_only_diagnostics_freeze_nothing(self):
        before = pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state)
        for command in ("status", "doctor", "ps"):
            code, out, err = run_main(["--instance", "staging", command], self.layout)
        self.assertEqual(pfx.snapshot_tree(self.base / "staging", self.context.paths.private_state), before)
        self.assertEqual(os.listdir(self.context.operations_dir), [])

    def test_installed_cli_mutation_freezes_the_proposal_and_stops_on_unsupported_values(self):
        original = self.env_path.read_bytes()
        result = launcher_run(self.layout, ["--instance", "staging", "permissions"])
        self.assertEqual(result.returncode, 0, result.stderr)
        operations = sorted(os.listdir(self.context.operations_dir))
        self.assertEqual(len(operations), 1)
        snapshot = self.context.operations_dir / operations[0] / "app.env"
        self.assertEqual(stat.S_IMODE(snapshot.stat().st_mode), 0o400)
        self.assertEqual(pf_config.parse_app_env(snapshot.read_bytes(), label="s"),
                         pf_config.parse_app_env(original, label="e"))
        record = json.loads((self.context.operations_dir / operations[0] / "operation.json").read_text())
        self.assertEqual(record["command"], "permissions")
        self.assertEqual(record["record_sha256"], self.context.record_sha256)
        # A value that cannot round-trip: explicit migration issue, nothing regenerated or written.
        self.env_path.write_bytes(original.replace(b"abc123", b"\"it's-not-quotable\""))
        edited = self.env_path.read_bytes()
        result = launcher_run(self.layout, ["--instance", "staging", "permissions"])
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("migration-issue", result.stderr)
        self.assertNotIn("it's-not-quotable", result.stderr + result.stdout)
        self.assertEqual(self.env_path.read_bytes(), edited)
        newer = set(os.listdir(self.context.operations_dir)) - set(operations)
        self.assertEqual(len(newer), 1)
        self.assertFalse((self.context.operations_dir / newer.pop() / "app.env").exists())
        # A hand-edited policy is refused, never applied silently.
        self.env_path.write_bytes(original)
        policy = self.layout.policy_path
        policy.write_bytes(pfx.policy_document(revision=2))
        result = launcher_run(self.layout, ["--instance", "staging", "permissions"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("policy-invalid", result.stderr)
        self.assertEqual(len(os.listdir(self.context.operations_dir)), 2)


# ===================================================== A1-T03 / A1-T17 repeats after extraction


@ROOT_REQUIRED
class ReadOnlyAfterExtraction(Base):
    """A1-T03 repeat: unknown commands, help, status and doctor create no operation, snapshot or store."""

    def test_no_operation_state_is_created_by_read_only_or_unknown_commands(self):
        self.install({"docker": str(recording_tool(self.base, "docker")), "git": str(recording_tool(self.base, "git"))})
        context, paths = self.instance()
        before = pfx.snapshot_tree(self.base / "staging", context.paths.private_state, self.layout.sources)
        for arguments in (["--help"], ["instances"], ["--instance", "staging", "status"],
                          ["--instance", "staging", "doctor"], ["frobnicate", "--now"], ["ps"], ["version"]):
            with self.subTest(arguments=arguments):
                try:
                    code, _, err = run_main(arguments, self.layout)
                except SystemExit as exc:
                    code, err = exc.code, ""
                if arguments[0] == "frobnicate":
                    self.assertEqual(code, 1)
                    self.assertIn("Unknown command 'frobnicate'", err)
        self.assertEqual(pfx.snapshot_tree(self.base / "staging", context.paths.private_state, self.layout.sources), before)
        self.assertEqual(os.listdir(context.operations_dir), [])
        self.assertEqual(os.listdir(self.layout.sources), [])


@ROOT_REQUIRED
class ProtectedRuntimeDirectories(Base):
    """A1-T17 repeat: the registration-created runtime home, operations and artifacts directories are
    protected private state; an unsafe one refuses mutation from leaf modes alone."""

    def setUp(self):
        super().setUp()
        self.install({"docker": str(recording_tool(self.base, "docker"))})
        self.context, self.paths = self.instance()

    def validation(self):
        return pf_instance.validate_context(self.context, running_release=self.layout.release_dir)

    def test_registration_creates_private_runtime_directories(self):
        for directory in (self.context.home_dir, self.context.docker_config_dir,
                          self.context.docker_config_dir / "cli-plugins", self.context.operations_dir,
                          self.context.artifacts_dir):
            self.assertTrue(directory.is_dir(), directory)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700, directory)
        self.assertEqual(stat.S_IMODE((self.context.docker_config_dir / "config.json").stat().st_mode), 0o600)
        self.assertTrue(self.validation().mutation_allowed)

    def test_unsafe_runtime_directories_refuse_mutation(self):
        cases = {
            "writable home": (self.context.home_dir, lambda p: os.chmod(p, 0o777), "writable"),
            "editor-owned operations": (self.context.operations_dir, lambda p: os.chown(p, 65534, 65534), "untrusted-owner"),
            "symlinked artifacts": (self.context.artifacts_dir, lambda p: (p.rmdir(), p.symlink_to(self.base)), "symlink"),
            "missing client config": (self.context.docker_config_dir / "config.json", lambda p: p.unlink(),
                                      "docker-client-config-missing"),
            "missing diagnostics env-file": (self.context.diagnostic_env_path, lambda p: p.unlink(),
                                             "diagnostic-env-missing"),
        }
        for label, (path, mutate, code) in cases.items():
            with self.subTest(case=label):
                mutate(path)
                validation = self.validation()
                self.assertFalse(validation.mutation_allowed)
                self.assertIn(code, validation.refused_codes())
                result = run_main(["--instance", "staging", "permissions"], self.layout)
                self.assertEqual(result[0], 1)
                self.assertIn(code, result[2])
                if path.is_symlink():
                    path.unlink()
                    path.mkdir(0o700)
                elif path.name == "config.json":
                    path.write_bytes(b"{}\n")
                    os.chmod(path, 0o600)
                elif path == self.context.diagnostic_env_path:
                    path.write_bytes(pf_instance.DIAGNOSTIC_ENV_CONTENT)
                    os.chmod(path, 0o600)
                else:
                    os.chown(path, 0, 0)
                    os.chmod(path, 0o700)
        self.assertTrue(self.validation().mutation_allowed)


class StaticCallSites(unittest.TestCase):
    """Every child process goes through pf_runner; no other release module starts one."""

    def test_only_the_runner_starts_processes(self):
        for name in ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "pf_config.py", "pf_source.py"):
            source = (pfx.PACKAGE / name).read_text()
            for forbidden in ("import subprocess", "os.system(", "os.popen(", "os.exec", "os.spawn", "os.fork(",
                              "shell=True"):
                if name == "pf_bootstrap.py" and forbidden == "os.exec":
                    continue  # the verifier's final execv of the pinned release entry point
                self.assertNotIn(forbidden, source, f"{name} contains {forbidden}")
        admin = (pfx.PACKAGE / "pf-admin.py").read_text()
        # A12-R03: control-plane children are argv only; no shell command strings anywhere in the release.
        for name in ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "pf_config.py", "pf_source.py", "pf_runner.py"):
            source = (pfx.PACKAGE / name).read_text()
            for forbidden in ('"sh", "-c"', "'sh', '-c'", '"bash", "-c"', "'bash', '-c'", "sh -c", "bash -c",
                              "shell=True", "$POSTGRES_USER", "$POSTGRES_DB", '"$1"', '"$2"'):
                self.assertNotIn(forbidden, source, f"{name} contains {forbidden!r}")
        self.assertNotIn("os.environ", admin)
        self.assertNotIn("read_dotenv", admin)
        self.assertNotIn("clean_env_keys", admin)
        self.assertNotIn("safe.directory", admin)
        self.assertNotIn("git clone", admin)
        runner = (pfx.PACKAGE / "pf_runner.py").read_text()
        self.assertIn("start_new_session=True", runner)
        self.assertIn("os.killpg", runner)
        self.assertNotIn("os.environ", runner)
        compose = (pfx.REPO_PACKAGE / "compose.nas.yaml").read_text()
        self.assertIn("${PARTFLOW_DATABASE_URL", compose)
        self.assertNotIn("${POSTGRES_PASSWORD", compose.split("backend:")[1].split("frontend:")[0])

    def test_release_modules_compile_without_deprecation_warnings(self):
        """A12-R04: compiling the release raises no syntax-level DeprecationWarning, and the config parser's
        ``re.split`` path (positional ``maxsplit`` was deprecated in 3.13) runs clean with warnings as errors."""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            for name in ("pf-admin.py", "pf_instance.py", "pf_bootstrap.py", "pf_config.py", "pf_source.py",
                         "pf_runner.py"):
                compile((pfx.PACKAGE / name).read_text(), str(pfx.PACKAGE / name), "exec")
            pf_source.parse_git_config("[core]\n\tbare = true # comment\n\tfsmonitor = false ; note\n")
        self.assertNotIn("re.split(r\"\\s[#;]\", value, 1)", (pfx.PACKAGE / "pf_source.py").read_text())
        self.assertIn("maxsplit=1", (pfx.PACKAGE / "pf_source.py").read_text())

    def test_release_inventory_and_installer_carry_the_new_modules(self):
        for name in ("pf_runner.py", "pf_config.py", "pf_source.py"):
            self.assertIn(name, pf_bootstrap.REQUIRED_RELEASE_FILES)
            self.assertIn(name, (pfx.PACKAGE / "install-control.sh").read_text())
        self.assertEqual(pf_bootstrap.TOOL_IDS, ("docker", "docker_compose", "git", "ip", "hostname"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
